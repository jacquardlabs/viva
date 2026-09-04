#!/usr/bin/env python3
"""viva — section-by-section markdown review server.

Usage:
  python server.py --mode review --input .viva/review-input-r1.json --output .viva/review-r1.json
  python server.py --mode qa     --input .viva/qa-input.json        --output .viva/answers.json
"""
from __future__ import annotations  # 3.8-safe `X | None` hints (CI matrix runs 3.8)

import argparse
import base64
import json
import os
import re
import signal
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

# The sibling `scripts/` dir holds the shared schema contract (section_key, the
# ledger rule, boundary validation). It sits beside server.py in both the repo
# and the installed plugin cache (`~/.claude/plugins/cache/**/viva/{server.py,scripts/}`).
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import schema  # noqa: E402
import preferences  # noqa: E402

# Absolute path to preferences.py, resolved from this file's own on-disk
# location, never $VIVA_DIR (SKILL.md never exports it). Escaped for
# embedding in the JS single-quoted string literal below.
_PREFS_SCRIPT_PATH = str(Path(__file__).resolve().parent / "scripts" / "preferences.py")
_PREFS_SCRIPT_PATH_JS = _PREFS_SCRIPT_PATH.replace("\\", "\\\\").replace("'", "\\'")
# Store path is set once at startup from _viva_dir; a placeholder is replaced
# after _viva_dir lands, mirroring the pattern for _PREFS_SCRIPT_PATH above.
_PREFS_STORE_PATH: str = ""

# Third-party browser assets, vendored under `assets/vendor/` and served from
# disk at `/vendor/<file>` (#79, #144) — nothing fetched from a remote host,
# so review works offline. Resolved from this file's own location, not cwd.
#
# ROUTE → (filename, content type), exact match only: the filename is always
# a table literal, never request-derived, which closes path traversal.
# Versioned filename+URL keeps the `immutable` cache header correct across
# upgrades. Read per request; see assets/vendor/README.md for pins/licenses.
_VENDOR_DIR = Path(__file__).resolve().parent / "assets" / "vendor"
_VENDOR_ASSETS = (
    ("marked-12.0.2.min.js", "text/javascript; charset=utf-8"),
    ("purify-3.4.13.min.js", "text/javascript; charset=utf-8"),
    ("highlight-11.11.1.min.js", "text/javascript; charset=utf-8"),
    ("diff2html-3.4.56.min.js", "text/javascript; charset=utf-8"),
    ("diff2html-ui-slim-3.4.56.min.js", "text/javascript; charset=utf-8"),
    ("diff2html-3.4.56.min.css", "text/css; charset=utf-8"),
    # The mono face. Referenced from an `@font-face { src: url(...) }` inside
    # the `HTML` constant's own stylesheet rather than from a `<script src>` —
    # a fourth spelling of the same three-place coordinated edit.
    ("fragment-mono-v6-latin.woff2", "font/woff2"),
    ("fragment-mono-v6-latin-ext.woff2", "font/woff2"),
    ("fragment-mono-v6-latin-italic.woff2", "font/woff2"),
    ("fragment-mono-v6-latin-ext-italic.woff2", "font/woff2"),
)
_VENDOR_ROUTES = {"/vendor/" + name: (name, ctype) for name, ctype in _VENDOR_ASSETS}

# ── The spoken grammar (viva voce) ───────────────────────────────────────────
# The examiner's voice, input only. Lives here (not schema.py) because the
# browser is its only consumer; `tests/test_voice_grammar.py` pins it to
# `schema.COMMENT_TYPES`/`VERDICTS` so a new comment type needs a phrase too.
#
# carries=False matches only the WHOLE utterance and acts immediately (a
# button press, nothing to mis-transcribe into the record). carries=True
# matches at the START and STAGES the remainder as text for the reviewer to
# confirm, keeping PRODUCT.md's "verbatim, not summarized" true of speech.
#
# `submit` aliases `recap`, not submission: ending the round stays gated
# behind the recap overlay's confirm click even from voice.
_VOICE_VERBS = (
    # Carrying verbs — one per COMMENT_TYPES value.
    {"act": "comment", "type": "changes", "carries": True,
     "phrases": ("request changes", "changes", "change")},
    {"act": "comment", "type": "info", "carries": True,
     "phrases": ("need info", "question", "info")},
    {"act": "comment", "type": schema.SUGGESTION, "carries": True,
     "phrases": ("suggest wording", "suggest", "replace with")},
    # Bare verbs — whole-utterance only. No "pass": "I'll pass on this
    # section" means skip in review vocabulary, not sign off.
    {"act": "approve", "carries": False, "phrases": ("approve", "approved")},
    {"act": "next", "carries": False, "phrases": ("next", "skip", "move on")},
    {"act": "back", "carries": False, "phrases": ("back", "previous", "go back")},
    {"act": "save", "carries": False, "phrases": ("save", "done", "commit")},
    {"act": "cancel", "carries": False, "phrases": ("cancel", "scratch that", "never mind")},
    {"act": "recap", "carries": False, "phrases": ("recap", "submit", "submit all")},
    {"act": "stop", "carries": False, "phrases": ("stop listening", "stop")},
)

# Flattened one-rule-per-phrase, sorted LONGEST PHRASE FIRST so the browser's
# first match is right: "request changes …" must never read as `changes`
# carrying the word "request".
_VOICE_RULES = tuple(sorted(
    (dict(phrase=phrase, act=verb["act"], carries=verb["carries"],
          **({"type": verb["type"]} if "type" in verb else {}))
     for verb in _VOICE_VERBS for phrase in verb["phrases"]),
    key=lambda rule: -len(rule["phrase"])))

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>viva</title>
<link rel="icon" id="favicon-link" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%232946c4'/%3E%3C/svg%3E">
<script defer id="marked-script" src="/vendor/marked-12.0.2.min.js"></script>
<script defer id="dompurify-script" src="/vendor/purify-3.4.13.min.js"></script>
<script defer src="/vendor/highlight-11.11.1.min.js"></script>
<script defer id="diff2html-script" src="/vendor/diff2html-3.4.56.min.js"></script>
<script defer id="diff2html-ui-script" src="/vendor/diff2html-ui-slim-3.4.56.min.js"></script>
<script>
/* Theme, applied before first paint, synchronously in <head> — reading the
   stored choice after body render would flash the OS theme first. Wrapped in
   try: localStorage throws in a private-mode iframe. */
(function () {
  try {
    var t = localStorage.getItem('viva-theme');
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  } catch (e) { /* no storage — follow the system, which is the default */ }
})();
</script>
<style>
/* ─── Faces ──────────────────────────────────────────────────
   Fragment Mono, vendored (assets/vendor/README.md), served from this
   server's own /vendor route — same-origin, no `crossorigin`. Host not
   named here on purpose: `test_typography.py` forbids the string outright.

   latin + latin-ext only; no cyrillic-ext subset, so it falls back to system
   mono. unicode-range values kept verbatim from the source so subsetting
   behaves the same. */
@font-face {
  font-family: 'Fragment Mono'; font-style: normal; font-weight: 400;
  font-display: swap; src: url('/vendor/fragment-mono-v6-latin.woff2') format('woff2');
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}
@font-face {
  font-family: 'Fragment Mono'; font-style: normal; font-weight: 400;
  font-display: swap; src: url('/vendor/fragment-mono-v6-latin-ext.woff2') format('woff2');
  unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF;
}
@font-face {
  font-family: 'Fragment Mono'; font-style: italic; font-weight: 400;
  font-display: swap; src: url('/vendor/fragment-mono-v6-latin-italic.woff2') format('woff2');
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD;
}
@font-face {
  font-family: 'Fragment Mono'; font-style: italic; font-weight: 400;
  font-display: swap; src: url('/vendor/fragment-mono-v6-latin-ext-italic.woff2') format('woff2');
  unicode-range: U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF;
}

/* ─── Tokens ─────────────────────────────────────────────── */
/* Catalog page: white ground, compact type. Light is the primary theme; dark
   is the override below.

   INK DISCIPLINE — four parties, one hue each, never shared:
     --touch   yellow  reviewer's touch ON THE TEXT only (never label/border)
     --acc     cobalt  reviewer's party: comments, suggestions, controls
     --machine teal    machine's party: passed checks, approved
     --fact    amber   machine-flagged open facts
   Red/green appear only in the suggestion fence (diff semantics), not tokens. */
:root {
  --paper:     #ffffff;   /* the page */
  --sunk:      #f8f8f7;   /* recessed wells: code, inputs, table heads */
  --ink:       #1d1f21;   /* rules, headings, the dispatch stamp */
  --ink2:      #2c2e30;   /* body copy */
  --soft:      #6b6e71;   /* labels, secondary */
  --faint:     #b0b1ae;   /* settled, disabled */
  --rule:      #d9dad8;   /* hairline */
  --touch:     #ffec8f;
  --acc:       #2946c4;
  --acc-dim:   rgba(41,70,196,0.08);
  --machine:   #0c7f6b;
  --fact:      #a06a12;
  /* Not a fifth party ink — a filled neutral meaning only "done". */
  --settled:   #e3e4e2;
  /* Edge under an anchored span: transparent on white (the yellow fill is
     already the mark); a real edge on charcoal, where the wash alone reads
     too faint. */
  --touch-edge: transparent;

  /* Component aliases so existing rules don't need rewriting; the four
     party inks above are the source of truth for anything new. */
  --bg:        var(--sunk);
  --bg2:       var(--paper);
  --bg3:       #f0f0ee;
  --table:     var(--paper);
  --border:    var(--rule);
  --border2:   var(--ink);
  --text:      var(--ink2);
  --text2:     var(--soft);
  --text3:     var(--faint);
  --accent:    var(--acc);
  --accent-dim:var(--acc-dim);
  --scrim:     rgba(29,31,33,0.28);
  --teal:      var(--machine);
  --teal-bg:   rgba(12,127,107,0.07);
  --orange:    var(--acc);          /* `changes` is reviewer judgment */
  --orange-bg: var(--acc-dim);
  --violet:    var(--fact);         /* `info` is an open fact */
  --violet-bg: rgba(160,106,18,0.08);
}

/* ─── Dark ───────────────────────────────────────────────── */
/* Same ink discipline, inverted ground, each ink lifted for charcoal contrast.
   Written twice deliberately — under `prefers-color-scheme` for OS dark and
   under `[data-theme="dark"]` for an explicit toggle choice — and kept in
   sync by `test_theme_toggle.py`, which fails if a value drifts between them. */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper:   #16181a;
    --sunk:    #1c1e21;
    --ink:     #e6e7e8;
    --ink2:    #d5d7d8;
    --soft:    #9a9ea1;
    --faint:   #5f6265;
    --rule:    #2f3235;
    --touch:   rgba(255,236,143,0.22);
    --acc:     #8fa6f5;
    --acc-dim: rgba(143,166,245,0.12);
    --machine: #4fc2a5;
    --fact:    #d19a3f;
    --settled: #3a3e41;
    --touch-edge: #ffec8f;

    --bg3:     #232629;
    --scrim:   rgba(10,11,12,0.72);
    --teal-bg: rgba(79,194,165,0.10);
    --violet-bg: rgba(209,154,63,0.12);
  }
}

/* Same values as the media block above — kept in sync by test_theme_toggle.py.
   `[data-theme]` beats the media query's bare `:root` on specificity, which
   lets the toggle override the OS rather than merely agree with it. */
:root[data-theme="dark"] {
  --paper:   #16181a;
  --sunk:    #1c1e21;
  --ink:     #e6e7e8;
  --ink2:    #d5d7d8;
  --soft:    #9a9ea1;
  --faint:   #5f6265;
  --rule:    #2f3235;
  --touch:   rgba(255,236,143,0.22);
  --acc:     #8fa6f5;
  --acc-dim: rgba(143,166,245,0.12);
  --machine: #4fc2a5;
  --fact:    #d19a3f;
  --settled: #3a3e41;
  --touch-edge: #ffec8f;

  --bg3:     #232629;
  --scrim:   rgba(10,11,12,0.72);
  --teal-bg: rgba(79,194,165,0.10);
  --violet-bg: rgba(209,154,63,0.12);
}

/* `color-scheme` follows the choice so the browser's own chrome — form
   controls, scrollbars, the canvas behind an unpainted area — matches the
   page instead of flashing the opposite ground. */
:root { color-scheme: light dark; }
:root[data-theme="light"] { color-scheme: light; }
:root[data-theme="dark"]  { color-scheme: dark; }


/* ─── Reset ──────────────────────────────────────────────── */
*,*::before,*::after { box-sizing:border-box; margin:0; padding:0; }
button { font-family:inherit; cursor:pointer; }
textarea { font-family:inherit; }

/* ─── Base ───────────────────────────────────────────────── */
html { scroll-behavior: smooth; scroll-padding-top: 72px; scroll-padding-bottom: 130px; }

body {
  /* Compact grotesque for human prose, mono for the machine. No display
     face — density and rules carry the character, not a headline font. */
  font-family: 'Helvetica Neue', Helvetica, Inter, system-ui, sans-serif;
  background: var(--paper);
  color: var(--text);
  min-height: 100vh;
  font-variant-numeric: tabular-nums;
  /* Byte-verbatim: Fragment Mono ligates `>=`/`->` to single glyphs, which a
     byte-for-byte diff surface can't allow. `none` disables all four
     categories; declared once here, inherited, rather than per-surface. */
  font-variant-ligatures: none;
  -webkit-font-smoothing: antialiased;
}

/* ─── Shell ──────────────────────────────────────────────── */
/* The shell is fluid; PROSE holds the measure (.section-content carries the
   cap), so spare window width goes to the margin conversation, never to
   wider text. */
.shell {
  max-width: 1240px;
  margin: 0 auto;
  padding: 32px clamp(16px, 3vw, 44px) 140px;
}

/* ─── Diff-first layout (mode-diff) ──────────────────────────
   Diffs want width and one scroll context, not the prose column.
   body.mode-diff widens sheet/shell/bottom-bar and removes the nested
   section scroll, keeping page scroll as the only vertical scroll — widening
   the container rather than escaping it, so the accordion animation still works. */
/* ─── Doc-mode page width ─────────────────────────────────────
   Capped so the `gutter | prose | margin` layout holds prose at ~88 chars
   and margin at 300, with no leftover dead band. Widen this to widen the
   TEXT — the margin is capped, so prose is what grows. */
.mode-doc .shell, .mode-doc .bottom-inner,
.mode-qa  .shell, .mode-qa  .bottom-inner { max-width: 1054px; }

.mode-diff .shell, .mode-diff .bottom-inner { max-width: min(95vw, 1600px); }
.mode-diff #paper { max-width: min(95vw, 1600px); }
/* Drops the prose measure — a diff-mode section holds no prose, only hunks.
   Capped at 72ch, a rendered diff clipped mid-word with the card mostly
   empty; the break-out rule alone couldn't fix it since the container itself
   was the constraint. */
.mode-diff .section-content { max-height: none; overflow-y: visible; max-width: none; }

/* ─── Header ─────────────────────────────────────────────── */
.header {
  margin-bottom: 36px;
}
/* Sticky so file/round/pass/approved-count stay visible on a long document.
   Opaque on the paper ground so prose passes beneath it, under overlays
   (z-index 200). */
.mode-doc .header {
  position: sticky;
  top: 0;
  z-index: 20;
  background: var(--paper);
  padding-top: 12px;
  margin-top: -12px;
}

/* ─── Status bar — the catalog header ────────────────────
   Filename, round, progress read left to right in one weight, closed by the
   same 2px ink rule as the bottom bar. Label sits BEFORE its value (inline
   runs), so the bar costs one line, not stacked boxes. */
.titleblock {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 20px;
  border: none;
  border-bottom: 2px solid var(--ink);
  background: none;
  padding-bottom: 8px;
  margin-bottom: 18px;
}
.tb-cell {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 0;
  border: none;
  min-width: 0;
  flex: 0 0 auto;
  overflow: hidden;
}
/* The filename takes the room; the last cell (progress) is pushed to the far
   end, where a catalog puts its count. */
.tb-flex { flex: 0 1 auto; }
.tb-wide { flex: 0 1 auto; }
.tb-cell:last-child { margin-left: auto; }
.tb-label {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 9px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--soft);
  margin-bottom: 0;
  white-space: nowrap;
}
h1.tb-val { margin: 0; }
.tb-val {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tb-val em { font-style: normal; color: var(--soft); font-weight: 400; }
.tb-val.mono {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 12px;
  color: var(--ink);
  padding-top: 0;
}

/* ─── Revision ledger ────────────────────────────────────── */
.ledger {
  border: 1px solid var(--border2);
  background: var(--bg2);
  margin-bottom: 14px;
  animation: fadeUp 0.4s ease both;
}
.ledger-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 8px 14px;
  cursor: pointer;
  user-select: none;
  background: none;
  border: 0;
  font: inherit;
  color: inherit;
  text-align: left;
}
.ledger-head:hover { background: var(--bg3); }
.ledger-title {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--text2);
}
.ledger-chevron { font-size: 10px; color: var(--text3); transition: transform 0.2s; }
.ledger.is-collapsed .ledger-chevron { transform: rotate(-90deg); }
.ledger-body-wrap {
  display: grid;
  grid-template-rows: 1fr;
  transition: grid-template-rows 0.28s cubic-bezier(0.4,0,0.2,1);
}
.ledger.is-collapsed .ledger-body-wrap { grid-template-rows: 0fr; }
.ledger-body-inner { overflow: hidden; }
.ledger-rows { padding: 0 14px 10px; }
.ledger-row {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 10px;
  align-items: baseline;
  padding: 5px 0;
  border-top: 1px solid var(--border);
  font-size: 12px;
}
.ledger-round {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  color: var(--text3);
  flex-shrink: 0;
}
.ledger-section {
  color: var(--text);
  font-weight: 500;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.ledger-verdict {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  flex-shrink: 0;
}
.ledger-verdict.v-changes { color: var(--orange); }
.ledger-verdict.v-info    { color: var(--violet); }
/* The note is verbatim reviewer prose of arbitrary length — it takes its own
   full-width line under the round/section/verdict head instead of competing
   with the title for leftover row space (the one-word-per-line squeeze). */
.ledger-note { color: var(--text2); font-style: italic; flex: 1 1 100%; min-width: 0; overflow-wrap: break-word; }
.ledger.ledger-static .ledger-head { cursor: default; }
.ledger.ledger-static .ledger-head:hover { background: none; }
.complete-inner .ledger { width: 100%; max-width: 560px; text-align: left; margin-top: 1.5rem; }

/* ─── Transmittal slip (round >= 2, review mode only) ────────
   One jump-link row per section, attributing what changed this revision.
   Reuses verdict color slots: revised/flag-error → orange, flag-warn →
   violet, approved → teal. */
.transmittal {
  border: 1px solid var(--border2);
  background: var(--bg2);
  margin-bottom: 14px;
  animation: fadeUp 0.4s ease both;
}
/* A disclosure button, dressed as the label it already was — the slip ships
   collapsed so the print is what a reader meets first. */
.transmittal-head {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 14px;
  border: 0;
  background: none;
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.transmittal-head:hover .transmittal-title { color: var(--acc); }
.transmittal-chevron { font-size: 10px; color: var(--text3); transition: transform 0.2s; }
.transmittal-head[aria-expanded="false"] .transmittal-chevron { transform: rotate(-90deg); }
.transmittal-title {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--text2);
}
.transmittal-rows { padding: 4px 14px 10px; }
.transmittal-row {
  display: flex;
  width: 100%;
  gap: 10px;
  align-items: baseline;
  padding: 5px 0;
  margin: 0;
  border: none;
  border-top: 1px solid var(--border);
  background: none;
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.transmittal-row:first-child { border-top: none; }
.transmittal-row:hover { background: var(--bg3); }
.transmittal-row:hover .tr-title { color: var(--accent); }
.tr-marker { flex-shrink: 0; font-size: 11px; }
.tr-label {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  flex-shrink: 0;
}
.tr-title { color: var(--text); font-weight: 500; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.tr-revised-note .tr-marker, .tr-revised-note .tr-label,
.tr-revised .tr-marker,      .tr-revised .tr-label,
.tr-flag-error .tr-marker,   .tr-flag-error .tr-label { color: var(--orange); }
.tr-flag-warn .tr-marker,    .tr-flag-warn .tr-label  { color: var(--violet); }
.tr-approved .tr-marker,     .tr-approved .tr-label   { color: var(--teal); }
/* An answer that changed no text is information, not a revision — so it takes
   the facts/info party's ink, the same slot `.tr-flag-warn` uses. */
.tr-answered .tr-marker,     .tr-answered .tr-label   { color: var(--violet); }

.progress-track {
  flex: 1;
  height: 2px;
  background: var(--border2);
  border-radius: 2px;
  overflow: visible;
  position: relative;
}

.progress-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 2px;
  transition: width 0.6s cubic-bezier(0.4,0,0.2,1);
}

.progress-label {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  color: var(--text3);
  white-space: nowrap;
  letter-spacing: 0.06em;
}

/* ─── Cards ──────────────────────────────────────────────── */
.cards { display: flex; flex-direction: column; gap: 6px; }

/* ─── diff-mode file grouping: static divider above each run of hunks
   belonging to the same file. Landmark, not a heading — same quiet
   typographic register as .diff-toggle. ─── */
.file-group-header {
  color: var(--text3);
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 6px 2px 0;
}

/* ─── Confidence triage sort (issue #12) ─────────────────── */
.sort-bar { display: flex; justify-content: flex-end; margin-bottom: 6px; }
.sort-toggle {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  cursor: pointer;
  color: var(--text2);
  padding: 4px 10px;
  border: 1px solid var(--border2);
  border-radius: 3px;
  background: none;
}
.sort-toggle:hover { color: var(--ink); border-color: var(--ink); }
.sort-toggle.is-active { color: var(--violet); border-color: var(--violet); background: var(--violet-bg); }

/* ─── Preferences panel toggle (issue #142) ──────────────────
   Lives inside #stats-area beside the (aria-live) verdict counters — a
   static label, never an interpolated count, so it never competes with the
   counters for that region's announcement. */
.prefs-toggle {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  cursor: pointer;
  color: var(--soft);
  padding: 4px 10px;
  border: 1px solid var(--rule);
  border-radius: 0;
  background: none;
}
.prefs-toggle:hover { color: var(--ink); border-color: var(--ink); }

/* States the current mode in words rather than a sun/moon glyph — a glyph
   makes the reader guess which state it names. */
.theme-toggle {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  cursor: pointer;
  color: var(--soft);
  padding: 4px 10px;
  border: 1px solid var(--rule);
  border-radius: 0;
  background: none;
}

/* Session controls — prefs, voice, theme — as ONE unit so they wrap together;
   an auto margin on a single item would wrap that item alone. */
.bar-controls {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}
.theme-toggle:hover { color: var(--ink); border-color: var(--ink); }

/* ─── Voice — the oral examination ───────────────────────────
   States its mode in words, same reason as the theme toggle. Cobalt only
   while listening — an idle control isn't doing anything yet. */
.voice-toggle {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  cursor: pointer;
  color: var(--soft);
  padding: 4px 10px;
  border: 1px solid var(--rule);
  border-radius: 0;
  background: none;
}
.voice-toggle:hover { color: var(--ink); border-color: var(--ink); }
.voice-toggle.is-live { color: var(--acc); border-color: var(--acc); }

/* The transcript: every utterance prints here with the reading it got,
   INCLUDING ones that matched no verb, so nothing is silently swallowed. */
.mode-doc .voice-strip, .mode-qa .voice-strip { max-width: 1054px; }
.voice-strip {
  max-width: 1240px;
  margin: 0 auto 8px;
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.05em;
  color: var(--text2);
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.voice-strip .vs-state { color: var(--acc); font-weight: 600; text-transform: uppercase; }
.voice-strip .vs-heard { color: var(--text); }
.voice-strip .vs-read  { color: var(--text3); }
/* Interim results are the recognizer still deciding — shown so the reviewer
   can see it is hearing them, dimmed so it never reads as the record. */
.voice-strip .vs-interim { color: var(--text3); font-style: italic; }
.voice-notice { color: var(--text2); }
.voice-notice button {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  cursor: pointer;
  color: var(--acc);
  background: none;
  border: 1px solid var(--acc);
  border-radius: 0;
  padding: 3px 9px;
  margin-left: 8px;
}

/* A section is a run of the print, not a box on it: separated by a hairline
   above and the heading's own weight, not a border/panel. */
.card {
  position: relative;
  border: none;
  border-top: 1px solid var(--rule);
  background: none;
  transition: opacity 0.35s;
  animation: fadeUp 0.4s ease both;
}
.card:first-child { border-top: none; }

/* The active card is marked by its edge and elevation below, not corner glyphs. */

/* #paper is the page's content wrapper that every layout rule and test hangs
   off — no frame, no edge border, no corner marks. */
#paper { position: relative; max-width: 1240px; margin: 0 auto; background: var(--paper); }

/* Entrance stagger is set inline per card as `animation-delay: 0.04 + i*0.04s`
   in buildReviewCard/buildQACard — it scales to any doc length and, being an
   inline style, overrides any :nth-child rule, so none are defined here. */

@keyframes fadeUp {
  from { opacity:0; transform:translateY(8px); }
  to   { opacity:1; transform:translateY(0); }
}

.card.is-approved { opacity: 0.72; }
.card.is-approved:hover { opacity: 1; transition: opacity 0.2s; }

/* Carried card (round >= 2 prior approval): dimmed head-only line, a touch
   brighter than is-approved so reveal/withdraw affordances stay discoverable. */
.card.is-carried { opacity: 0.72; }
.card.is-carried:hover, .card.is-carried:focus-within { opacity: 1; transition: opacity 0.2s; }
.carried-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 14px;
  min-height: 48px;
  cursor: pointer;
  transition: background 0.12s;
}
.carried-head:hover { background: var(--bg3); }
.carried-head .card-title { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.carried-marker {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text3);
  flex-shrink: 0;
}
.carried-show, .carried-withdraw {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  color: var(--soft);
  background: none;
  border: 0;
  padding: 2px 0;
  flex-shrink: 0;
}
.carried-show { text-decoration: underline dotted; text-underline-offset: 3px; }
.carried-show:hover { color: var(--accent); }
.carried-withdraw:hover { color: var(--orange); }
.carried-stamp {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.14em;
  color: var(--teal);
  border: 1px solid var(--teal);
  border-radius: 2px;
  padding: 2px 7px;
  transform: rotate(-2deg);
  flex-shrink: 0;
}
.carried-body { padding: 0 14px 14px; }

/* The live section is marked where the reader's eye already is — at the
   heading — not by outlining the whole run. */
.card.is-active { box-shadow: none; }
.card.is-active .card-head { border-left: 2px solid var(--ink); margin-left: -2px; }

/* ─── Card head ──────────────────────────────────────────── */
.card-head {
  /* Native <button> reset — the header is a real button (a11y) but must look
     like the surrounding card chrome, not a default button. */
  width: 100%;
  border: 0;
  background: transparent;
  text-align: left;
  font: inherit;
  color: inherit;
  -webkit-appearance: none;
  appearance: none;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 14px;
  cursor: pointer;
  user-select: none;
  transition: color 0.12s;
  min-height: 0;
  padding: 14px 14px 6px;
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
}
/* No fill on hover — a filled band would rebuild the panel the flattening
   just removed. The title takes the accent instead. */
.card-head:hover { background: none; color: var(--acc); }

/* dot */
.dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: background 0.25s, box-shadow 0.25s;
}
.dot-idle     { background: var(--faint); }
.dot-active   { background: var(--acc); box-shadow: none; }
/* Flat dots — printed, not lit. */
.dot-approved { background: var(--machine); box-shadow: none; }
.dot-changes  { background: var(--acc);     box-shadow: none; }
.dot-info     { background: var(--fact);    box-shadow: none; }
/* Revision triangle — drafting's "this region changed at this rev" flag, keyed
   to the titleblock REV and the revision log. */
.rev-tri { font-family: 'Fragment Mono', monospace; font-size: 11px; font-weight: 600; color: var(--orange); letter-spacing: 0.04em; margin-left: 10px; flex-shrink: 0; align-self: center; }
/* Cumulative revision count (#141) — a second run inside .rev-tri, not a
   separate badge; var(--text3) not the triangle's orange. Decorative, no focus target. */
.rev-tri .rev-mult { font-size: 9px; color: var(--text3); letter-spacing: 0.1em; }

/* Flex column so the title + inline-note <span>s stack as they did when divs
   (the header is now a <button>, whose content must be phrasing-level spans). */
.card-title-wrap { flex: 1; min-width: 0; display: flex; flex-direction: column; }

.card-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
  line-height: 1.4;
}

/* Scoped override of `.section-summary` for the COLLAPSED list view (#188):
   always one line, ellipsized rather than growing the row. Relies on
   `.card-title-wrap`'s `min-width: 0` for the truncation to take effect. */
.card-title-wrap .section-summary {
  font-size: 11.5px;
  line-height: 1.4;
  color: var(--text3);
  margin: 2px 0 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.note-inline {
  font-size: 11px;
  color: var(--text3);
  font-style: italic;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* verdict badge */
.vbadge {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 3px 8px;
  border-radius: 3px;
  flex-shrink: 0;
  max-width: 45%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.vbadge-approved { background: var(--teal-bg);   color: var(--teal);   }
.vbadge-changes  { background: var(--orange-bg);  color: var(--orange); }
.vbadge-info     { background: var(--violet-bg);  color: var(--violet); }

/* annotation strip — advisory pre-review flags. Reuses the verdict color
   slots: info → teal, warn → violet (amber #ffc857), error → orange.
   Advisory only — they decorate a card, they never gate a verdict. */
.annot-strip { display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; }
.annot {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 11.5px;
  line-height: 1.5;
  padding: 6px 9px;
  border-radius: 5px;
  border-left: 2px solid var(--border2);
}
.annot-kind {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  flex-shrink: 0;
  white-space: nowrap;
}
.annot-msg { color: var(--text2); min-width: 0; overflow-wrap: break-word; }
/* deep-link to a conflicting section (contradiction producer): rendered when an
   annotation's anchor matches a section id. */
.annot-jump {
  background: none;
  border: none;
  padding: 0 0 0 6px;
  margin: 0;
  cursor: pointer;
  font: inherit;
  color: var(--violet);
  text-decoration: underline;
  white-space: nowrap;
}
.annot-jump:hover { filter: brightness(1.2); }
.annot-info  { background: var(--teal-bg);   border-color: var(--teal);   }
.annot-warn  { background: var(--violet-bg);  border-color: var(--violet); }
/* An error flag is still the machine's news — the facts ink at every
   severity, the glyph carrying the difference; cobalt is the reviewer's. */
.annot-error { background: var(--violet-bg);  border-color: var(--violet); }
.annot-info  .annot-kind { color: var(--teal);   }
.annot-warn  .annot-kind { color: var(--violet); }
.annot-error .annot-kind { color: var(--violet); font-weight: 700; }

/* round-to-round diff — added/removed lines vs the prior round on a rewritten
   card. Reuses the verdict slots: added → teal, removed → orange. Presentational
   only; it never alters a verdict. Shown by default, collapsible. */
.diff-block { margin-bottom: 12px; border: 1px solid var(--border2); border-radius: 6px; overflow: hidden; }
.diff-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: none;
  border-bottom: 1px solid var(--border2);
  color: var(--text2);
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 5px 9px;
  cursor: pointer;
  text-align: left;
}
.diff-toggle:hover { color: var(--teal); }
.diff-block.collapsed .diff-toggle { border-bottom: none; }
.diff-block.collapsed .diff-body { display: none; }
.diff-body {
  font-family: 'Fragment Mono', monospace;
  font-size: 11px;
  line-height: 1.55;
  overflow-x: auto;
}
/* Prose sections diff as whole paragraphs (one line each) so lines wrap
   instead of scrolling horizontally. Word-level marks (.dw, markWordDiff)
   show what moved inside a paired rewrite. */
.diff-line { display: flex; padding: 0 9px; }
.diff-gutter { flex-shrink: 0; width: 1.1em; opacity: 0.6; user-select: none; }
.diff-text { flex: 1; min-width: 0; white-space: pre-wrap; overflow-wrap: break-word; }
.diff-add { background: var(--teal-bg);   color: var(--teal);   }
.diff-del { background: var(--orange-bg); color: var(--orange); }
.diff-add .dw { background: color-mix(in srgb, var(--teal) 30%, transparent);   border-radius: 2px; }
.diff-del .dw { background: color-mix(in srgb, var(--orange) 30%, transparent); border-radius: 2px; }
.diff-ctx { color: var(--text2); }
.diff-hunk { color: var(--violet); padding: 1px 9px; opacity: 0.7; white-space: pre; }

/* ─── diff2html output (diff mode) ─────────────────────────
   Rendering delegated to diff2html (renderDiffHunk); these rules bend its
   chrome without forking its stylesheet. Theming rides d2h's own CSS custom
   properties, mapped to viva tokens. ins/del/change tints stay d2h's own
   green/red on purpose — a different semantic axis than viva's verdict palette. */
.section-content .d2h-wrapper {
  --d2h-bg-color: var(--bg);
  --d2h-dark-bg-color: var(--bg);
  --d2h-border-color: var(--border);
  --d2h-dark-border-color: var(--border);
  --d2h-file-header-bg-color: var(--bg2);
  --d2h-dark-file-header-bg-color: var(--bg2);
  --d2h-file-header-border-color: var(--border2);
  --d2h-dark-file-header-border-color: var(--border2);
  --d2h-line-border-color: var(--border);
  --d2h-dark-line-border-color: var(--border);
  --d2h-dim-color: var(--text3);
  --d2h-dark-dim-color: var(--text3);
}
/* DESIGN.md: two font families, no exceptions — d2h's own Menlo/Consolas
   stack (on the diff table) and Source Sans (on the file header) yield to
   Fragment Mono. */
.section-content .d2h-diff-table,
.section-content .d2h-file-header { font-family: 'Fragment Mono', monospace; }
/* The card title and file-group header already name the file; d2h's
   name + always-"CHANGED" tag would be a misleading third. Keep its
   per-hunk +N/−M line stats, which nothing else shows. */
.section-content .d2h-file-name,
.section-content .d2h-tag { display: none; }
/* Guards: td reset undoes the generic table rule chopping diff rows into
   cells; line numbers are unselectable so a drag can't capture them into
   comment.anchor.text; wrapper is position:relative so d2h's absolute line
   numbers don't escape the accordion's overflow:hidden clip. */
.section-content .d2h-wrapper td { border-bottom: none; padding: 0; }
.section-content .d2h-code-linenumber { user-select: none; }
.section-content .d2h-file-wrapper { position: relative; border-radius: 6px; }

/* ─── Card body (smooth height animation) ────────────────── */
.card-body-wrap {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 0.28s cubic-bezier(0.4,0,0.2,1);
}
.card.is-active .card-body-wrap {
  grid-template-rows: 1fr;
}
.card-body-inner {
  overflow: hidden;
}

/* The body continues the page under the head, not a panel opening beneath it:
   no fill, no second rule, and the left edge lines up with the title above. */
.card-body {
  padding: 4px 14px 22px;
  border-top: none;
  background: none;
}

/* The agent's one-line description of a section, under its title. This is the
   doc-print size, sitting in the open prose column under the `<h2>`; the
   accordion head overrides it above (`.card-title-wrap .section-summary`). */
.section-summary {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 11px;
  line-height: 1.6;
  color: var(--soft);
  margin-bottom: 12px;
}

/* The document itself — a quiet page surface inside the card chrome */
/* `max-width` is a MEASURE, not a container width — a line stops at ~72
   characters because that is where reading breaks down. Wide content (code,
   tables) escapes it via .section-content > pre below. No nested scroll: the
   section prints in full; the page is the only scroll. */
.section-content {
  font-family: 'Helvetica Neue', Helvetica, Inter, system-ui, sans-serif;
  font-size: 13.5px;
  font-weight: 400;
  line-height: 1.62;
  color: var(--text);
  padding: 6px 2px 12px;
  border: none;
  background: transparent;
  border-radius: 0;
  margin-bottom: 14px;
  overflow-wrap: break-word;
  max-width: 72ch;
}

/* Code and tables don't take the prose measure — they take the room and
   scroll sideways in their own container. This only lifts the CHILD's cap;
   `.section-content` itself must also be unbound (`.doc .section-content` in
   review mode, `.mode-diff .section-content` in diff mode). */
.section-content > pre,
.section-content > table,
.section-content > .table-wrap {
  max-width: none;
  width: 100%;
  overflow-x: auto;
}
.section-content::-webkit-scrollbar { width: 10px; }
.section-content::-webkit-scrollbar-thumb {
  background: var(--border2);
  border-radius: 5px;
  border: 3px solid var(--bg2);
}
.section-content::-webkit-scrollbar-track { background: transparent; }

/* offline fallback: raw verbatim source */
.section-content.md-raw {
  font-family: 'Fragment Mono', monospace;
  font-size: 12px;
  line-height: 1.75;
  white-space: pre-wrap;
}

.section-content p { margin: 0 0 12px; }
.section-content > *:last-child { margin-bottom: 0; }
.section-content strong { font-weight: 500; color: var(--text); }
.section-content em { color: var(--text2); }

/* In-document headings: title-block lettering for majors, mono overline for minors */
/* Sentence case, not uppercase — headings set in the body's own face, one
   step up in weight. */
.section-content h1, .section-content h2 {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: none;
  color: var(--ink);
  margin: 18px 0 8px;
}

/* The section's own title is already printed by the card head above, so the
   leading heading in the rendered markdown is a duplicate. Hide it and let
   the head carry it. */
.section-content > h1:first-child,
.section-content > h2:first-child,
.section-content > h3:first-child { display: none; }
.section-content h3 {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin: 16px 0 6px;
}
.section-content h4, .section-content h5, .section-content h6 {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text2);
  margin: 16px 0 6px;
}
.section-content h1:first-child, .section-content h2:first-child,
.section-content h3:first-child, .section-content h4:first-child { margin-top: 2px; }

/* Code and diagrams: cyan linework on the print */
/* The code well: solid border, no fill, so the syntax theme keeps the ink. */
.section-content pre {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 12.5px;
  line-height: 1.6;
  background: var(--sunk);
  border: 1px solid var(--rule);
  padding: 12px 14px;
  overflow-x: auto;
  margin: 0 0 12px;
  color: var(--ink2);
}
.section-content code {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 12px;
  background: var(--sunk);
  border: 1px solid var(--rule);
  padding: 1px 5px;
  color: var(--ink2);
}
.section-content pre code { background: none; border: none; padding: 0; color: inherit; }

/* Editorial tables: hairline rows, mono overline headers, no grid boxes */
.section-content table {
  border-collapse: collapse;
  width: 100%;
  margin: 2px 0 14px;
  font-size: 12px;
}
.section-content th {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text2);
  text-align: left;
  padding: 6px 12px 6px 0;
  border-bottom: 1px solid var(--border2);
}
.section-content td {
  padding: 6px 12px 6px 0;
  border-bottom: 1px solid var(--border);
  color: var(--text2);
}
.section-content tr:last-child td { border-bottom: none; }

.section-content ul, .section-content ol { margin: 0 0 12px; padding-left: 20px; }
.section-content li { margin: 3px 0; }
.section-content li::marker { color: var(--text3); }

.section-content blockquote {
  border-left: 2px solid var(--accent);
  margin: 0 0 12px;
  padding: 2px 14px;
  color: var(--text2);
  font-style: italic;
}
.section-content a {
  color: var(--accent);
  text-decoration: none;
  border-bottom: 1px solid var(--accent-dim);
}
.section-content a:hover { border-bottom-color: var(--accent); }
.section-content hr { border: none; border-top: 1px solid var(--border); margin: 14px 0; }
.section-content img { max-width: 100%; }

/* ─── The document grid — doc + margin (issue #186) ───────────
   Rows of `check gutter | prose | margin`, in document order; commentary
   sits beside the passage it annotates. `.doc` is the grammar (review, diff,
   Q&A alike); `.print` is continuous print, review-only. Gutter/margin are
   variables that collapse to 0 per-document (never per-row/section, which
   would jog the prose sideways as you read); the 28px alley lives in each
   side cell's own padding so a collapsed column costs exactly zero. */
.doc {
  /* A glyph rail, not a text column: 70px of 9px mono can't hold a real flag
     legibly, so this just signals LOCALITY — a colored glyph, not the text. */
  --gutter-w: 34px;                    /* 14px glyph + the 20px alley */
  /* Capped at 300px (+28px alley), not left to absorb spare width — else the
     margin runs near 50:50 with the prose instead of the intended ~61:39. */
  --margin-w: minmax(253px, 328px);
}
/* .cards's gap is the space between entries in continuous print; the
   accordion keeps its own hairline-and-6px separation instead. */
.doc.print { gap: 22px; }
.doc.no-gutter { --gutter-w: 0px; }
.doc.no-margin { --margin-w: 0px; }
/* The prose track takes whatever the gutter/margin don't; the page holds the
   measure, so no `ch` value appears in the template — `ch` resolves against
   the row's own font-size, which varied a `72ch` track's width by ~99px
   across rows before `.doc-section` fixed one size for the print. */
.doc .row {
  display: grid;
  grid-template-columns: var(--gutter-w) minmax(0, 1fr) var(--margin-w);
  align-items: start;
}
/* Break-out rule at row scale: code (not tables — a table reflows to the
   measure) borrows the margin's room ONLY on a row with no margin cell, and
   only in print — the accordion's wide row IS the hunk, so re-laying it out
   under the cursor when a comment lands would be wrong there. */
.doc.print .row.wide:not(:has(> .rm)):has(> .rp > pre, > .rp > .d2h-wrapper) .rp { grid-column: 2 / 4; }
/* Explicit column placement, so a row that omits an empty side cell still
   prints its prose in the middle track instead of sliding left. */
.doc .rg { grid-column: 1; padding-right: 20px; display: flex; flex-direction: column; align-items: flex-end; gap: 3px; padding-top: 2px; }
.doc .rp { grid-column: 2; min-width: 0; }
.doc .rm { grid-column: 3; padding-left: 28px; min-width: 0; }
/* One type size for the whole print, so `ch` means the same thing in every
   row — the heading and the machine's own faces set their own size on top. */
.doc-section { font-size: 13.5px; }
/* The head row has no margin cell in any state, so no collapsed-margin
   exemption is needed. The foot band keeps a two-track twin because an
   unanchored section-scope flag can fall back to it (`marginFlagHTML`).
   `1fr`, not `72ch` (issue #5): `ch` resolves against the row's own
   font-size and would come out narrower than the prose rows above it. */
.doc.no-margin .row-foot { grid-template-columns: var(--gutter-w) minmax(0, 1fr); }
.doc.no-margin .row-foot .rm { grid-column: 2; padding-left: 0; }
/* Below the composite's own breakpoint the third column has no room to be a
   margin; notes fall under the passage they annotate and the gutter narrows
   to a glyph rail. No separate term needed for `.row-foot` or the wide-row
   selector — both are already a `.doc .row` and covered by it. */
@media (max-width: 920px) {
  .doc .row { grid-template-columns: 30px minmax(0, 1fr); }
  .doc .rg { padding-right: 8px; }
  .doc .rm { grid-column: 2; padding-left: 0; }
  /* The bar's row breaks here and nowhere wider — under 920px `.stats` can no
     longer shrink enough, so the dispatch controls take their own line. */
  .bottom-inner { flex-wrap: wrap; row-gap: 8px; }
  /* Every control the grid reflows for a narrow screen is a touch target. */
  .nt-btn, .theme-toggle, .voice-toggle, .prefs-toggle, .sort-toggle,
  .btn-skip, .btn-submit, .pal-row, .cmt-chip, .cmt-save, .cmt-cancel,
  .attach-btn, .mic-btn, .settle-btn, .thread-reply-btn, .choice-chip { min-height: 44px; }
}

/* ─── The foot band ───────────────────────────────────────────
   The section's DISPOSITION: what is open on it, and what you do about it.
   Horizontal, at the reading measure, under the prose. A sibling of
   `.section-content`, never a child — `docRows`, `rowForAnchor`,
   `docNotesOrdered`, and `markAndPin` all depend on that. */
.doc .row-foot { margin-top: 18px; }
/* `.doc .row + .row`'s 10px does not reach here — the foot row's previous
   sibling is `.section-content`, not a row. */

/* Measure as max-width, not a grid track: `ch` resolves against font-size, so
   no `ch` in a TEMPLATE (issue #5). Without this, diff mode's wide shell
   would put `approve` 1200px from the state readout. */
.doc .row-foot .rp { max-width: 72ch; }

.doc-apparatus {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 16px;
  border-top: 1px solid var(--rule);
  padding-top: 8px;
}
/* The verbs LEAD and the state TRAILS, visually — `approve` sits on the same
   left edge the reader has been reading down, in the same place whether or not
   the section has a spec to state, so the one control that is always there
   never moves. In the DOM the state comes first, because "2 comments open"
   before "approve" is the order a screen reader should hear it. The reordered
   element carries no focusable content, so focus order is untouched and the
   `order` pair costs nothing. */
.spec-strip { order: 2; margin-left: auto; }
.doc-acts   { order: 1; }
/* An all-zero spec renders nothing (specHTML's early return) — then the band
   is one line of verbs, and an empty flex item with an auto margin has no
   business eating the free space. */
.spec-strip:empty { display: none; }

/* State as a run, not a table — one line at 10.5px mono. Separation is
   `gap`, never generated content: a `::before` mark is announced by some
   screen readers, and the `.sp-k`/`.sp-v` ink pairing already separates a
   label from its value. */
.spec-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 14px;
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 10.5px;
  line-height: 1.6;
  letter-spacing: 0.02em;
  font-variant-numeric: tabular-nums;
}
.sp-k { color: var(--soft); }
.sp-v { color: var(--text2); }
/* The one thing that is open takes the reviewer's ink, exactly as
   `.spec .spec-open td:last-child` did. */
.sp-open .sp-v { color: var(--acc); font-weight: 600; }
/* At narrow widths the state run leads the left edge instead of right-aligning.
   This sits AFTER `.spec-strip { margin-left: auto }` deliberately — the two
   rules tie on specificity, so source order is the whole mechanism. */
@media (max-width: 920px) {
  .spec-strip { margin-left: 0; }
}

.doc-section { position: relative; }
/* Continuous print: nothing collapses, so a settled section dims in place.
   Its prose stays on the page and stays readable — that is the whole point
   of printing the document rather than one section of it. */
.doc-section.is-approved .rp { opacity: 0.72; }
.doc-section.is-approved .doc-head { color: var(--soft); }
.doc-section.is-approved:hover .rp { opacity: 1; transition: opacity 0.2s; }
/* The prose dims on approval; the band with `↺ withdraw approval` does not —
   affordances stay discoverable. 0.72 is the floor where --ink2 still clears
   4.5:1. Specificity ties the hover rule above, so position is the mechanism. */
.doc-section.is-approved .row-foot .rp { opacity: 1; }
/* Nor does the heading: it is already in --soft, and dimming that a second
   time (the head row is a `.rp` too) put it at 2.9:1. The reader navigates
   by headings, settled or not. */
.doc-section.is-approved .row-head .rp { opacity: 1; }

/* The section heading carries its catalog number, the way a parts catalog
   numbers its entries — `9 · One human, N threads`. */
.doc-head {
  display: flex;
  align-items: baseline;
  gap: 7px;
  font-size: 14.5px;
  font-weight: 700;
  color: var(--ink);
  margin: 0 0 8px;
  scroll-margin-top: 16px;
}
.doc-num {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-weight: 400;
  color: var(--soft);
  flex-shrink: 0;
}
/* The live section is marked where the reader's eye already is — at the
   heading — the same rule the accordion's active card used. */
.doc-section.is-active .doc-head { border-left: 2px solid var(--ink); margin-left: -10px; padding-left: 8px; }
/* The segmented rule's slot is reserved whether or not a rule is drawn, so
   approving a section (which draws the settled hairline) moves nothing. */
.doc .row-head [id^="rseg-"] { min-height: 5px; margin-bottom: 8px; }
.doc .row-head [id^="rseg-"] .rule-s { padding-bottom: 4px; margin-bottom: 0; }
.doc .row-head [id^="rseg-"] .seg { margin-bottom: 0; }
/* The keycap on `approve` is live only on the live section — `a` acts on
   rState.active — so it prints only there. Hidden, not removed: the button
   keeps its width, so activation moves nothing either. */
.doc-section:not(.is-active) .doc-acts kbd { visibility: hidden; }
/* A control that refuses (approve with comments open) says so in the disabled
   grammar rather than sitting there enabled and silent. */
.nt-btn[aria-disabled="true"] { border-color: var(--rule); color: var(--soft); background: none; cursor: not-allowed; }
.nt-btn[aria-disabled="true"]:hover { border-color: var(--rule); color: var(--soft); background: none; }

/* ─── Segmented rule ──────────────────────────────────────────
   State × party under an open heading, honest counts: judgment → facts →
   settled, a FIXED order that is the colorblind-safe second encoding. Raw
   counts ride in the aria-label. Drawn only where something is open. */
.seg { display: flex; height: 4px; margin: 0 0 10px; }
.seg i { display: block; height: 4px; min-width: 2px; }
.seg-judgment { background: var(--acc); }
.seg-fact     { background: var(--fact); }
.seg-settled  { background: var(--settled); }
.rule-s { border-bottom: 1px solid var(--rule); padding-bottom: 6px; margin-bottom: 10px; }

/* ─── Check gutter ────────────────────────────────────────────
   Producer flags print beside the paragraph they concern, right-aligned
   against the prose column, in the machine's own face. A flag needing an
   interactive jump doesn't fit in 70px and routes to the margin instead. */
/* The rail: one glyph per flag, at a size you can actually see, colored by
   whose news it is. Decorative to a screen reader — the readable line is in
   the margin (.mflag) and reading both would be the same flag twice. */
.lflag {
  font-size: 13px;
  line-height: 1.3;
  cursor: default;
}
.lflag-info  { color: var(--machine); }
.lflag-warn  { color: var(--fact); }
.lflag-error { color: var(--fact); font-weight: 700; }

/* The words, in the margin of the same row — the machine's line, not a note
   in the conversation, so no border and no actions: a producer flag is
   advisory. Inked by severity, led by the same glyph as the rail. */
.mflag {
  display: flex;
  gap: 6px;
  margin-bottom: 10px;
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 10.5px;
  line-height: 1.5;
  letter-spacing: 0.02em;
  color: var(--soft);
  overflow-wrap: anywhere;
}
.mflag .g { flex-shrink: 0; }
.mflag-info  .g { color: var(--machine); }
.mflag-warn  .g { color: var(--fact); }
.mflag-error .g { color: var(--acc); }
.mflag-warn, .mflag-error { color: var(--text2); }
.mflag .r { display: block; color: var(--machine); }

/* ─── Margin notes ────────────────────────────────────────────
   One note per thread or comment, numbered, sitting beside its own anchor.
   The number is the same on both ends: the pin in the text and the note in
   the margin. */
.nt {
  margin-bottom: 12px;
  font-size: 12px;
  line-height: 1.5;
  border: 1px solid var(--rule);
  background: var(--paper);
  padding: 7px 9px 8px;
}
.nt.is-settled { opacity: 0.55; }
.nh {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 5px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--acc);
  margin-bottom: 4px;
}
.nh .pn {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-weight: 400;
  letter-spacing: 0;
  text-transform: none;
  color: var(--soft);
}
.nt-fact  .nh { color: var(--fact); }
.nt-check .nh { color: var(--machine); }
/* The author's party, not the reviewer's: a declined thread is the author
   answering, and it speaks in the neutral ink. */
.nt-author .nh { color: var(--soft); }
.nt-author { border-left: 2px solid var(--soft); }
.nt-body { color: var(--text2); overflow-wrap: anywhere; }
.nt-quote {
  display: block;
  font-style: italic;
  font-size: 11px;
  color: var(--text3);
  margin-bottom: 4px;
  overflow-wrap: anywhere;
}
.nt-acts { display: flex; gap: 6px; margin-top: 7px; flex-wrap: wrap; }
.nt-btn {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
  padding: 3px 9px;
  border: 1px solid var(--ink);
  border-radius: 0;
  background: var(--paper);
  color: var(--ink);
  cursor: pointer;
}
.nt-btn:hover { background: var(--bg3); }
.nt-btn.is-pri { background: var(--ink); color: var(--paper); }
.nt-btn.is-pri:hover { opacity: 0.85; }
.nt-btn.is-quiet { border-color: var(--rule); color: var(--soft); }
.nt-btn.is-quiet:hover { border-color: var(--ink); color: var(--ink); background: none; }

/* Keycaps sit on the control they name rather than being hover-revealed —
   the palette is a directory of this same layer, never a second one. */
.doc kbd, .pal kbd, .pal-hint kbd, .recap-panel kbd, .prefs-panel kbd {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 9px;
  border: 1px solid var(--rule);
  border-bottom-width: 2px;
  border-radius: 0;
  padding: 0 4px;
  margin-left: 5px;
  color: var(--soft);
  background: var(--sunk);
}
.nt-btn.is-pri kbd { border-color: var(--soft); background: none; color: var(--paper); }
.choice-chip kbd { flex-shrink: 0; }
.choice-chip.selected kbd { border-color: var(--accent); color: var(--accent); }

/* ─── Pins ────────────────────────────────────────────────────
   The mark in the text that matches the note in the margin. The anchored
   span wears catalog yellow and nothing else does — per the ink discipline,
   `--touch` is the reviewer's touch ON THE TEXT — so the pin, not the
   highlight, is what carries whose note it is. */
.doc mark[class^="cmt-hl-"] {
  background: var(--touch);
  border-bottom: 1.5px solid var(--touch-edge);
  color: inherit;
}
.pin {
  display: inline-block;
  min-width: 14px;
  height: 14px;
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 9px;
  font-weight: 700;
  line-height: 14px;
  text-align: center;
  vertical-align: 3px;
  margin-left: 2px;
  cursor: pointer;
  border: 0;
  padding: 0;
}
/* ─── A suggestion, shown applied ────────────────────────────
   Replaced wording struck through in faint ink, the replacement on catalog
   yellow, so the reviewer reads the sentence as it would stand. `del` is
   still the document; `ins` is the proposal and is excluded from every text
   walk, so it can never be counted as prose or commented on. */
.sug-del { color: var(--faint); text-decoration: line-through; }
.sug-ins {
  text-decoration: none;
  background: var(--touch);
  border-bottom: 1.5px solid var(--touch-edge);
  color: inherit;
  margin-left: 5px;
}
.nt-applied {
  margin-top: 5px;
  font-size: 10.5px;
  color: var(--soft);
  font-style: italic;
}

.pin-you    { background: var(--acc); color: var(--paper); }
.pin-author { border: 1.5px solid var(--soft); color: var(--soft); background: none; }
.pin-fact   { background: var(--fact); color: var(--paper); }
/* A pin on a diff line: prose WRAPS but a code line SCROLLS, so a pin
   appended at its anchor's offset could scroll off-screen. Pairing with the
   LINE instead keeps the pin sticky at its head under horizontal scroll. */
.pin-line {
  position: sticky;
  left: 0;
  z-index: 2;
  margin: 0 5px 0 0;
  vertical-align: 1px;
}

/* ─── Suggestion fence ────────────────────────────────────────
   The one place red and green are correct: the fence and the diff are the
   same object, and diff semantics already own those two colors. Squared off
   — the recognizable part is the −/+ grammar, not the radius. */
.fence {
  margin-top: 6px;
  border: 1px solid var(--rule);
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 11px;
  line-height: 1.5;
  overflow: hidden;
}
.fence-h {
  background: var(--sunk);
  border-bottom: 1px solid var(--rule);
  padding: 3px 8px;
  font-family: 'Helvetica Neue', Helvetica, Inter, system-ui, sans-serif;
  font-size: 10px;
  color: var(--soft);
}
.fence-ln { display: grid; grid-template-columns: 16px minmax(0, 1fr); }
.fence-g { text-align: center; user-select: none; }
.fence-tx { padding: 1px 6px; white-space: pre-wrap; overflow-wrap: anywhere; }
.fence-del { background: rgba(209,36,47,0.12); }
.fence-add { background: rgba(26,127,55,0.12); }
.fence-del .fence-g { color: #d1242f; }
.fence-add .fence-g { color: #1a7f37; }

/* The round-to-round diff, collapsed, in the head row's prose cell. Ships
   collapsed since "what changed since last round" isn't what the reader
   opened the document for, and expands at the reading measure, not full
   width, so it wraps rather than pushing the text aside. */
.doc .diff-block { border-radius: 0; border-color: var(--rule); margin: 4px 0 0; }
.doc .diff-toggle { border-bottom-color: var(--rule); }

/* ─── Command palette (⌘K) ────────────────────────────────────
   A directory of the keyboard layer, not a second interaction model: every
   verb it lists is one the page also shows as a keycap or a control. It
   takes the floor's materials — square, 1px ink border, selection on
   catalog yellow rather than a tint of the accent. */
.pal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  background: var(--scrim);
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding: 12vh 16px 16px;
}
.pal {
  width: min(460px, 100%);
  background: var(--paper);
  border: 1px solid var(--ink);
  border-radius: 0;
  box-shadow: 0 18px 50px var(--scrim);
  font-size: 13px;
  overflow: hidden;
}
.pal-input {
  width: 100%;
  border: 0;
  border-bottom: 1px solid var(--rule);
  background: none;
  color: var(--ink);
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 12.5px;
  padding: 9px 12px;
}
.pal-input:focus { outline: none; box-shadow: inset 0 -2px 0 var(--acc); }
.pal-input::placeholder { color: var(--soft); }
.pal-list { max-height: 46vh; overflow-y: auto; }
.pal-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  width: 100%;
  padding: 7px 12px;
  border: 0;
  background: none;
  font: inherit;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}
.pal-row.is-on { background: var(--touch); }
.pal-row .k {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 10.5px;
  color: var(--soft);
  flex-shrink: 0;
}
.pal-empty { padding: 10px 12px; font-size: 12px; color: var(--soft); }

/* The section container is the row host, not the measure: the 72ch cap moved
   to the grid's middle track, and the padding that framed a card body would
   only push the rows off the alley they share with every other row. */
.doc .section-content { max-width: none; padding: 0; margin-bottom: 0; }
/* The break-out rule, at row scale. The `wide` row already gives the middle
   track the room; this is what lets the block use it. */
.doc .rp > pre, .doc .rp > .table-wrap {
  max-width: none;
  width: 100%;
  overflow-x: auto;
}
/* A table is as wide as its columns need, up to the measure: a three-column
   table stretched to 100% stranded its last column 300px right of its text. */
.doc .rp > table, .doc .rp > .table-wrap > table { width: auto; max-width: 100%; }
.doc .rp > *:last-child { margin-bottom: 0; }
.doc .row + .row { margin-top: 10px; }

/* A carried thread keeps every affordance it had in the accordion — settle,
   reply, the type chips — and takes the margin's note dress. One builder,
   two surfaces (openThreadItemHTML); this is the restyle, not a fork. */
.doc .open-thread {
  margin-bottom: 12px;
  border: 1px solid var(--rule);
  border-left: 2px solid var(--acc);
  border-radius: 0;
  background: var(--paper);
}
.doc .open-thread.is-declined { border-left-color: var(--soft); }
.doc .open-thread-head { flex-wrap: wrap; gap: 5px; padding: 6px 9px 5px; border-bottom: none; }
.doc .open-thread-label { color: var(--acc); }
.doc .open-thread.is-declined .open-thread-label { color: var(--soft); }
.doc .open-thread-quote { max-width: 100%; white-space: normal; }
.doc .open-thread-body { padding: 0 9px; }
.doc .exchange { padding: 5px 0; }
/* One verb per note, with its keycap; the reply box is what a verb reveals,
   not something every thread carries open. */
.open-thread .nt-acts { padding: 2px 9px 9px; margin-top: 0; }
.thread-reply { padding: 0 9px 9px; margin-top: 0; }
.settle-btn.is-on { --c: var(--machine); border-color: var(--machine); color: var(--machine); }
.nt-btn.is-pri.is-on { background: var(--machine); border-color: var(--machine); }
.open-thread.is-settled .nt-acts .thread-reply-btn { display: none; }
/* The note number, shared by the margin's two note builders and by the pin
   that answers it. Empty in the accordion — only the margin numbers notes. */
.nh-num, .nh .nh-num {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-weight: 700;
  color: var(--ink);
  flex-shrink: 0;
}
.nh-num:empty { display: none; }
.open-thread-head .pn,
.nh .pn {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 9px;
  font-weight: 400;
  letter-spacing: 0;
  text-transform: none;
  color: var(--faint);
}
.doc .comment-popover { border-radius: 0; margin-top: 0; margin-bottom: 12px; }
.doc .annot { border-radius: 0; margin-bottom: 12px; flex-direction: column; gap: 3px; }
/* The whole-document invitation, printed once ABOVE the print, sharing a row
   with the sort toggle. Label ink (--soft), never the settled ink: this is
   live instruction. */
.doc-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px 24px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.doc-tools .sort-bar { margin: 0 0 0 auto; }
.doc-hint {
  margin: 0;
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 10px;
  letter-spacing: 0.05em;
  color: var(--soft);
}

/* The document's own balance, same grammar as a section's rule but heavier
   (6px vs 4px) and its settled segment is INK rather than gray — at document
   scale "done" is the page's closed mass, not one section's remainder. */
.foot-seg { position: absolute; top: 0; left: 0; right: 0; display: flex; height: 6px; }
.foot-seg i { display: block; height: 6px; }
.foot-seg .seg-settled { background: var(--ink); }

/* The way in to the keyboard layer, stated in the bar rather than buried in
   the legend at the foot of the page — a palette nobody knows about is a
   palette nobody uses. */
.pal-hint {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 10px;
  letter-spacing: 0.05em;
  color: var(--soft);
  background: none;
  border: 0;
  padding: 0;
  cursor: pointer;
}
.pal-hint:hover { color: var(--ink); }
.stat-conv b { color: var(--ink); font-weight: 600; }
.stat-lat { color: var(--soft); }
.stat-pending kbd, .stats kbd {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 9px;
  border: 1px solid var(--rule);
  border-bottom-width: 2px;
  border-radius: 0;
  padding: 0 4px;
  margin-left: 4px;
  color: var(--soft);
  background: var(--sunk);
}

/* ─── Blueprint geometry: drafting sheets have square corners ── */
.card, .note-field, .vbadge, .btn-skip, .btn-submit,
.section-content, .choice-chip,
.transmittal-row, .recap-row,
.progress-track, .progress-fill { border-radius: 0; }

/* ─── Control edges ───────────────────────────────────────────
   Selectable controls (verdict actions, Q&A chips + buttons): full 1px
   borders, square. State rules reassign --c — the property feeds a border
   instead of a gradient stack; registering it via @property keeps the
   recolor animatable (it snaps without @property support). */
@property --c { syntax: '<color>'; inherits: true; initial-value: transparent; }
.choice-chip, .attach-btn, .mic-btn, .cmt-chip, .cmt-save, .cmt-cancel {
  --c: var(--rule);
  border: 1px solid var(--c);
  background: var(--paper);
  transition: --c 0.12s, color 0.12s, background 0.12s;
}

/* The per-section action row is gone from every surface. A section's verbs
   live in its margin (`.nt-acts`, `.nt-btn`) beside the notes they answer —
   the one place a reviewer is already looking — so `.actions` and
   `.action-btn` have no host left. */

/* ─── Note textarea ──────────────────────────────────────── */
.note-field {
  width: 100%;
  font-size: 13px;
  padding: 9px 12px;
  border: 1px solid var(--border2);
  background: var(--bg2);
  color: var(--text);
  resize: vertical;
  min-height: 72px;
  line-height: 1.55;
  transition: border-color 0.15s;
  margin-top: 2px;
  display: block;
}
.note-field:focus { outline: none; border-color: var(--acc); }
.note-field::placeholder { color: var(--soft); }
.note-field[aria-invalid="true"] { border-color: var(--fact); }
.thumb-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.thumb {
  position: relative;
  width: 64px;
  height: 64px;
  overflow: hidden;
  border: 1px solid var(--border2);
}
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.thumb-remove {
  position: absolute;
  top: 1px;
  right: 1px;
  width: 20px;
  height: 20px;
  line-height: 18px;
  text-align: center;
  border: none;
  background: var(--bg3);
  color: var(--text2);
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}
/* Error surfaces — tokenized so they participate in light mode like everything
   else (were the only components with hardcoded hex). */
.error-banner {
  position: fixed;
  top: 0; left: 0; right: 0;
  padding: 0.6rem 1rem;
  background: var(--orange-bg);
  color: var(--orange);
  border-bottom: 1px solid var(--orange);
  font-family: 'Fragment Mono', monospace;
  font-size: 0.82rem;
  z-index: 1000;
  text-align: center;
}
/* Soft-timeout variant (#119) — same structural rules as .error-banner, but
   --violet/--violet-bg ("Info / question" per DESIGN.md) instead of
   --orange, since "no event yet, connection still open" is a lighter-weight
   signal than "the connection actually dropped". */
.error-banner.banner-info {
  background: var(--violet-bg);
  color: var(--violet);
  border-bottom: 1px solid var(--violet);
}
.load-error {
  padding: 2rem;
  color: var(--orange);
}
.attach-btn {
  margin-top: 6px;
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  cursor: pointer;
  color: var(--text2);
  padding: 5px 10px;
}
.attach-btn:hover { --c: var(--text3); color: var(--text); }
/* The composer's second secondary control — same `--c` edge grammar as
   attach. Cobalt while live: a control currently doing something is the one
   place the accent belongs. */
.mic-btn {
  margin-top: 6px;
  margin-left: 6px;
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  cursor: pointer;
  color: var(--text2);
  padding: 5px 10px;
}
.mic-btn:hover { --c: var(--text3); color: var(--text); }
.mic-btn.is-live { --c: var(--accent); color: var(--accent); }
/* neutral active highlight for a drop zone — teal stays reserved for approve */
.card.is-drop-target { box-shadow: 0 0 0 2px var(--accent); }

/* ─── Multi-comment review ───
   The add-a-note row went with the action row: `+ note` is a margin verb now,
   and the invitation to select a passage is printed once for the whole
   document (`.doc-hint`) rather than once per section. */
mark.cmt-hl-changes { background: var(--orange-bg); border-bottom: 2px solid var(--orange); color: inherit; }
mark.cmt-hl-info    { background: var(--violet-bg); border-bottom: 2px solid var(--violet); color: inherit; }
/* A suggested span is the reviewer's own ink over the author's — the accent
   slot, the same one `.cmt-pop-quote` uses for the span being acted on. Not a
   verdict color: the three verdict inks stay approved/changes/info. */
mark.cmt-hl-suggestion { background: var(--accent-dim); border-bottom: 2px solid var(--accent); color: inherit; }
.comment-popover { border: 1px solid var(--border2); border-radius: 4px; background: var(--bg2); padding: 8px; margin-top: 6px; }
.cmt-pop-row { display: flex; gap: 8px; align-items: center; margin: 4px 0; }
/* The span being commented on — a focal accent callout so it reads clearly as
   the thing being acted on, not muted context. Uses the app's accent slot. */
.cmt-pop-quote {
  font-size: 12px;
  color: var(--text);
  background: var(--accent-dim);
  border-left: 2px solid var(--accent);
  padding: 5px 9px;
  margin: 2px 0 9px;
  overflow-wrap: anywhere;
}
.cmt-chip {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.03em;
  cursor: pointer;
  color: var(--text2);
  padding: 5px 8px;
  /* A chip that does not fit moves to the next row whole; it never breaks
     `request changes` into two lines inside a 300px composer. */
  white-space: nowrap;
}
.cmt-pop-row { flex-wrap: wrap; }
.cmt-chip:hover { --c: var(--text3); color: var(--text); }
.cmt-chip-changes.is-on { --c: var(--orange); color: var(--orange); }
.cmt-chip-info.is-on    { --c: var(--violet); color: var(--violet); }
.cmt-chip-suggestion.is-on { --c: var(--accent); color: var(--accent); }
/* The replacement field only exists while the suggestion chip is on; it reuses
   `.note-field` for shape (square corners, per DESIGN.md's grouped rule). */
/* Popover save / cancel — save is the reviewer's own act and takes the
   reviewer's cobalt (teal is the machine's party); cancel stays muted. */
.cmt-save, .cmt-cancel {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  cursor: pointer;
  padding: 6px 14px;
  color: var(--text2);
}
.cmt-save { --c: var(--acc); color: var(--acc); }
.cmt-save:hover   { color: var(--text); }
.cmt-cancel:hover { --c: var(--text3); color: var(--text); }
/* a section the reviewer is selecting text in, to make the anchor target obvious */
.section-content::selection,
.section-content *::selection { background: var(--violet-bg); }

/* ─── Open notes (issue #16) — a note that carries across rounds ─── */
.open-thread {
  margin-bottom: 12px;
  border: 1px solid var(--border2);
  border-left: 2px solid var(--violet);
  border-radius: 5px;
  background: var(--bg);
}
.open-thread-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 9px;
  border-bottom: 1px solid var(--border);
}
.open-thread-label {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--violet);
}
.settle-btn {
  margin-left: auto;
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  cursor: pointer;
  color: var(--text2);
  padding: 3px 9px;
  border: 1px solid var(--border2);
  border-radius: 3px;
  background: none;
}
.settle-btn:hover { color: var(--teal); border-color: var(--teal); }
.open-thread.is-settled { opacity: 0.55; }
.open-thread.is-settled .settle-btn { color: var(--teal); border-color: var(--teal); }
/* A thread the author declined: unresolved like an open one, so it keeps every
   affordance — only the label and the rule change, so the reviewer can see at a
   glance which threads are waiting on their accept-or-insist. */
.open-thread.is-declined { border-left-color: var(--orange); }
.open-thread.is-declined .open-thread-label { color: var(--orange); }
/* Reply box at the foot of an open thread — continue the back-and-forth, or
   switch the chip to "request changes" to turn the discussion into an edit. */
.thread-reply { margin-top: 7px; }
.thread-reply-chips { display: flex; gap: 8px; margin-bottom: 5px; }
.thread-reply-field {
  width: 100%;
  font-size: 12px;
  padding: 6px 9px;
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text);
  resize: vertical;
  min-height: 34px;
  line-height: 1.5;
  display: block;
}
.thread-reply-field:focus { outline: none; border-color: var(--acc); }
.thread-reply-field::placeholder { color: var(--soft); }
.open-thread.is-settled .thread-reply { display: none; }
.exchange { padding: 7px 9px; font-size: 11.5px; line-height: 1.5; }
.exchange + .exchange { border-top: 1px solid var(--border); }
.exchange-q { display: flex; align-items: baseline; gap: 7px; flex-wrap: wrap; }
.exchange-round {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px; font-weight: 600; color: var(--text3); flex-shrink: 0;
}
.exchange-verdict {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px; font-weight: 600; text-transform: uppercase; flex-shrink: 0;
}
.exchange-verdict.v-changes { color: var(--orange); }
.exchange-verdict.v-info    { color: var(--violet); }
.exchange-verdict.v-suggestion { color: var(--accent); }
.exchange-note { color: var(--text2); min-width: 0; overflow-wrap: break-word; }
.exchange-a {
  margin-top: 3px; padding-left: 10px;
  border-left: 1px solid var(--border2);
  color: var(--text3);
}
.exchange-a::before { content: '↳ '; }
/* The author's decline — their grounds for not complying with that turn. Sits
   between the request and the response: it answers the reviewer's turn without
   resolving it. */
.exchange-d {
  margin-top: 3px; padding-left: 10px;
  border-left: 1px solid var(--orange);
  color: var(--text2);
  overflow-wrap: anywhere;
}
.exchange-d::before { content: '⊘ '; }
.open-thread-quote {
  font-style: italic;
  font-size: 11px;
  color: var(--text3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60%;
}

/* The comment list is gone: a comment lives in the margin beside its own
   anchor now. `.cmt-del` survives as a WIRING HOOK only. The wording a
   suggestion carries, in a carried thread's exchange — accent-inked,
   arrow-led like `.exchange-a`'s reply. */
.cmt-repl { display: block; margin-top: 3px; color: var(--accent); overflow-wrap: anywhere; }
.cmt-repl::before { content: '→ '; }

/* ─── Q&A choices (chip style) ────────────────────────────
   The choices ARE the question's body — no `Choices` label needed. One per
   line (wrapped, they'd read as a grid with the picking digit landing
   differently on every row) and bounded to 328px (`--margin-w`'s max) so the
   keycap doesn't strand far from its label at full measure. */
.choices { display: flex; flex-direction: column; align-items: stretch; gap: 4px; margin-bottom: 4px; max-width: 328px; }

.choice-chip {
  font-size: 12px;
  font-weight: 400;
  padding: 6px 12px;
  color: var(--text2);
  cursor: pointer;
  text-align: left;
  display: flex;
  align-items: baseline;
  gap: 8px;
}
/* The label takes the row; the badge and the keycap ride its right edge. */
.chip-label { flex: 1; min-width: 0; }
.choice-chip:hover    { --c: var(--text3);  color: var(--text);   }
.choice-chip.selected { --c: var(--accent); color: var(--accent); }

/* Recommended-choice badge (#114) — advisory only, never pre-selected or
   focus-defaulted. Reuses the .vbadge-approved/.annot-info teal token. */
.chip-badge {
  display: inline-block;
  font-family: 'Fragment Mono', monospace;
  font-size: 8px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 1px 5px;
  border-radius: 0;
  background: var(--teal-bg);
  color: var(--teal);
  vertical-align: middle;
}

/* Grounds-classed recommendation badges (#175), extending .chip-badge's
   shape: `-sourced` keeps the shared teal; `-taste` reuses the accent token
   and decorates the choices, not a chip. `inferred` gets no color here. */
.chip-badge-sourced { background: var(--teal-bg); color: var(--teal); }
.chip-badge-taste   { display: block; width: fit-content; margin-bottom: 6px;
                       background: var(--orange-bg); color: var(--orange); }

/* An inferred recommendation answers only on request (#175) — a
   best-practice opinion with no local provenance earns a click, not a
   glance. Reuses the native <details>/<summary> disclosure .kbd-legend uses. */
.chip-reveal {
  margin-top: 4px;
  font-family: 'Fragment Mono', monospace;
  font-size: 11px;
  color: var(--soft);
}
.chip-reveal summary { cursor: pointer; }
.chip-reveal summary:focus-visible {
  outline: 1.5px solid var(--accent);
  outline-offset: 2px;
}
.chip-reveal-body { margin-top: 2px; }

/* Q&A confirm/skip are margin verbs in the `.nt-btn` grammar. The compose
   block puts the reviewer's context inside the same bordered note the
   margin gives other commentary — a 300px margin is not a card body. */
.nt-compose .note-field {
  min-height: 58px;
  margin-top: 3px;
  font-size: 12px;
  padding: 6px 8px;
}
.nt-compose .attach-btn { margin-top: 6px; }
.nt-compose .thumb-strip { margin-top: 6px; }
/* The disclosure head IS the question — printed once, wrapping, numbered
   like a catalog entry. No line clamp: a free-text question is printed
   nowhere else, so ellipsizing it would leave it readable nowhere. */
#qa-cards .card-title { white-space: normal; }

/* A free-text question has nothing to put in the prose column — same
   wasted-space failure `.doc.no-margin` exists to answer. A constant,
   since whether a question has choices cannot change mid-session. */
#qa-cards .row-head.is-choiceless { grid-template-columns: var(--gutter-w) minmax(0, 1fr); }
#qa-cards .row-head.is-choiceless .rm { grid-column: 2; padding-left: 0; max-width: 420px; }

/* ─── Skip link (first Tab stop; hidden until focused) ───── */
.skip-link {
  position: fixed;
  top: -100px;
  left: 8px;
  z-index: 2000;
  padding: 8px 12px;
  background: var(--bg2);
  color: var(--accent);
  border: 1px solid var(--accent);
  font-family: 'Fragment Mono', monospace;
  font-size: 12px;
  text-decoration: none;
  transition: top 0.15s;
}
.skip-link:focus { top: 8px; outline: 1.5px solid var(--accent); outline-offset: 2px; }
#main-content:focus { outline: none; }

.sr-only {
  position: absolute; width: 1px; height: 1px; overflow: hidden;
  clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap;
}

/* ─── Keyboard focus (quality floor) ─────────────────────── */
.card-head:focus-visible,
.choice-chip:focus-visible,
.attach-btn:focus-visible, .cmt-chip:focus-visible,
.cmt-save:focus-visible, .cmt-cancel:focus-visible,
.settle-btn:focus-visible, .diff-toggle:focus-visible,
.carried-show:focus-visible, .carried-withdraw:focus-visible,
.nt-btn:focus-visible, .pin:focus-visible, .pal-row:focus-visible,
.thread-reply-btn:focus-visible,
.transmittal-row:focus-visible, .transmittal-head:focus-visible,
.recap-row:focus-visible, .recap-close:focus-visible,
.annot-jump:focus-visible,
.prefs-toggle:focus-visible, .prefs-close:focus-visible, .pref-mute-btn:focus-visible,
.theme-toggle:focus-visible, .pal-hint:focus-visible,
.voice-toggle:focus-visible, .mic-btn:focus-visible, .voice-notice button:focus-visible,
.btn-skip:focus-visible, .btn-submit:focus-visible {
  outline: 1.5px solid var(--accent);
  outline-offset: 2px;
}

/* ─── Keyboard shortcut legend ───────────────────────────── */
.kbd-legend {
  margin: 4px 2px 0;
  font-family: 'Fragment Mono', monospace;
  font-size: 11px;
  color: var(--soft);
}
.kbd-legend summary {
  cursor: pointer;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  padding: 4px 0;
  user-select: none;
}
.kbd-legend summary:focus-visible {
  outline: 1.5px solid var(--accent);
  outline-offset: 2px;
}
.kbd-list {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 14px;
  margin: 6px 0 0;
  align-items: center;
}
.kbd-list dt { margin: 0; }
/* The second list in the same disclosure: the aggregates, defined. Its terms
   are words, not keycaps, so they get no `.kbd-list kbd` chrome — only the
   rule that separates the two lists. */
.term-list { margin-top: 12px; }
.term-list dt { color: var(--text2); }
.kbd-list dd { margin: 0; color: var(--text2); }
.kbd-list kbd {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  color: var(--text2);
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 1px 5px;
}

/* ─── Bottom bar ─────────────────────────────────────────── */
.bottom-bar {
  position: fixed;
  bottom: 0; left: 0; right: 0;
  z-index: 100;
  padding: 12px 20px;
  /* Opaque, not frosted: a catalog footer states the count, it does not
     blur the page behind it. The 2px ink rule is the same one that closes
     the header — the page is bracketed top and bottom. */
  background: var(--paper);
  border-top: 2px solid var(--ink);
}

.bottom-inner {
  max-width: 1240px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  /* NOWRAP, and the counters give way instead. A flex container breaks its
     line BEFORE it shrinks anything, so `wrap` here meant the row never even
     attempted the shrink that would have let it fit: at doc mode's own 1054px
     cap, 613px of counters against a 450px stamp is 1063, and the stamp
     dropped to a line of its own over nine pixels. `.stats` is `0 1 auto` with
     `min-width: 0` and wraps internally, so it absorbs the squeeze down to its
     own widest child — measured no overflow at 1054, 900, 760, 640 and 520px
     of container, with the stamp keeping its full 450px at every one. */
  flex-wrap: nowrap;
}

/* The counters take the room the dispatch controls do not, and give way
   INTERNALLY rather than pushing them onto a line of their own. `.btn-group`
   is `flex: 0 0 auto`, so without `min-width: 0` here the counters claim their
   full content width and the two blocks overflow the row: measured at doc
   mode's own 1054px cap, 613px of counters against 450px of buttons is 1063 —
   over by nine pixels, and the stamp wrapped under the stats. Shrinking lets
   `.bar-controls` (the `margin-left: auto` cluster) take the second line
   instead, which is the wrap it was grouped to make graceful. */
.stats {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.05em;
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  /* Shrink, never grow. `1 1 auto` made the counters claim the whole row and
     push the stamp onto a second line just as surely as no shrink at all. */
  flex: 0 1 auto;
  min-width: 0;
}
.stat-run { display: flex; gap: 14px; flex-wrap: wrap; min-width: 0; align-items: center; }
.stat-pending  { color: var(--soft); }

/* The two dispatch controls are one unit and do not shrink: default
   `flex-shrink: 1` let `skip rest & submit` wrap onto three lines at a
   780px viewport. `.stats` above absorbs the width instead. `display: flex`
   stays in the stylesheet — the SSE `round` handler falls back to it. */
.btn-group { display: flex; gap: 8px; flex: 0 0 auto; }

.btn-skip {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  padding: 9px 16px;
  border: 1px solid var(--rule);
  background: transparent;
  color: var(--soft);
  /* The label is three words; a button narrower than its text broke
     `skip rest & submit` onto three lines before `.btn-group` stopped
     shrinking. Both halves are needed. */
  white-space: nowrap;
  transition: all 0.15s;
}
.btn-skip:hover { border-color: var(--ink); color: var(--ink); }

.btn-submit {
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 9px 20px;
  /* A transparent 1px border, not `none`: the ready and not-ready states share
     one box, so the outline state below cannot grow the control by 2px. */
  border: 1px solid transparent;
  white-space: nowrap;
  transition: all 0.2s;
}
.btn-submit.ready {
  background: var(--acc);
  color: var(--paper);
}
.btn-submit.ready:hover {
  background: var(--ink);
  transform: none;
}
/* The not-ready dispatch takes `.btn-skip`'s quiet outline grammar rather
   than a filled shape in disabled tokens, which made the one control that
   cannot act the highest-contrast block on the page. Carries `aria-disabled`
   from updateReviewStats/updateQAStats; the DOM `disabled` attr stays
   reserved for in-flight. */
.btn-submit.disabled {
  background: transparent;
  color: var(--soft);
  border-color: var(--rule);
  cursor: not-allowed;
}

/* ─── Recap overlay (submit gate — review/diff modes) ──────
   Pre-flight index over every section: id, title, verdict dot + label,
   active-note count. btn-submit's ready click opens this instead of
   submitting; only #recap-confirm calls submitReview(false). */
.recap-overlay {
  position: fixed; inset: 0; z-index: 200;
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
  background: var(--scrim);
}
/* The palette's materials, since the palette is what a catalog overlay looks
   like here: paper rather than the recessed panel `--bg` gave it, a square 1px
   ink border, and the same lift off the scrim. */
.recap-panel {
  width: min(640px, 92vw); max-height: 82vh;
  display: flex; flex-direction: column;
  background: var(--paper);
  border: 1px solid var(--ink);
  border-radius: 0;
  box-shadow: 0 18px 50px var(--scrim);
}
.recap-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}
.recap-title {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--text2);
}
/* A dialog closes on Escape, so the control says so rather than drawing a
   glyph the reader has to learn. Same keycap the palette and the margin verbs
   wear — one keyboard layer, printed wherever it applies. */
/* 9px of keycap is not a click target. The padding goes on the BUTTON, not
   on the cap, so the cap keeps the size every other keycap on the page has
   while the hit area clears 24px. */
.recap-close, .prefs-close {
  border: none; background: none; cursor: pointer;
  padding: 6px 8px; line-height: 1;
  display: flex; align-items: center;
}
.recap-close:hover kbd, .prefs-close:hover kbd { border-color: var(--ink); color: var(--ink); }
.recap-grid { overflow-y: auto; overscroll-behavior: contain; padding: 4px 14px; }
.recap-row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) auto 52px;
  gap: 10px; align-items: center;
  width: 100%;
  padding: 6px 0; margin: 0;
  border: none;
  border-top: 1px solid var(--border);
  background: none;
  font: inherit; font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.recap-row:first-child { border-top: none; }
/* Catalog yellow marks what the pointer is on, the way `.pal-row.is-on`
   does — the ground's one selection ink, rather than a second gray band. */
.recap-row:hover { background: var(--touch); }
.recap-id {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  letter-spacing: 0.06em;
  color: var(--soft);
}
.recap-row-title { color: var(--text); font-weight: 500; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.recap-verdict {
  display: flex; align-items: center; gap: 6px;
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.rv-approved { color: var(--teal); }
.rv-changes  { color: var(--orange); }
.rv-info     { color: var(--violet); }
.rv-pending  { color: var(--soft); }
.recap-notes {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  color: var(--soft);
  text-align: right;
}
/* Three children, not one: the blocked state on the left, the two controls
   on the right — a recap opened with sections pending needs a control that
   can act. */
.recap-actions {
  display: flex; justify-content: flex-end; align-items: center; gap: 12px;
  flex-wrap: wrap;   /* three children now, in a row built for one */
  padding: 12px 14px;
  border-top: 1px solid var(--border);
}
/* Why the confirm is quiet, said in place. `.recap-title`'s label grammar, so
   it reads as a state label rather than as a new ink. */
.recap-blocked {
  margin-right: auto;   /* the label holds the left edge; the controls stay right */
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text2);
}

/* ─── Preferences panel — view/mute learned preferences (#142) ────
   A second modal, built on the recap overlay's shape (role="dialog", inert
   background, focus trap); at most one of the two is ever open at a time. */
.prefs-overlay {
  position: fixed; inset: 0; z-index: 200;
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
  background: var(--scrim);
}
.prefs-panel {
  width: min(640px, 92vw); max-height: 82vh;
  display: flex; flex-direction: column;
  background: var(--paper);
  border: 1px solid var(--ink);
  border-radius: 0;
  box-shadow: 0 18px 50px var(--scrim);
}
.prefs-head {
  display: flex; flex-direction: column; gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}
.prefs-title {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--text2);
}
.prefs-help {
  font-size: 11px;
  line-height: 1.4;
  color: var(--text3);
}
.prefs-help strong { color: var(--text2); font-weight: 600; }
/* The panel's *only* aria-live region — one line, updated on mute.
   #prefs-list deliberately carries none: a live-labeled list would announce
   every row's text on open, not just the one status change after a mute. */
.prefs-status {
  min-height: 1em;
  padding: 6px 14px 0;
  font-family: 'Fragment Mono', monospace;
  font-size: 10px;
  color: var(--text2);
}
.prefs-list { overflow-y: auto; overscroll-behavior: contain; padding: 8px 14px 14px; }
.prefs-empty { color: var(--text3); font-size: 12px; padding: 8px 0; margin: 0; }
.pref-row { padding: 10px 0; border-top: 1px solid var(--border); }
.pref-row:first-child { border-top: none; }
/* Programmatic badge-jump target (tabindex="-1", never a mouse focus) — a
   visible ring confirms which row the jump landed on, same accent outline
   every other focus target in the page uses. */
.pref-row:focus { outline: 1.5px solid var(--accent); outline-offset: 2px; }
.pref-row-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pref-status {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 2px 6px;
  border-radius: 0;
}
.pref-status-standing  { color: var(--teal);  background: var(--teal-bg); }
.pref-status-candidate { color: var(--text2); background: var(--bg3); }
.pref-status-muted     { color: var(--text3); background: var(--bg3); }
.pref-label { color: var(--text); font-weight: 500; }
.pref-guidance { color: var(--text2); font-size: 12px; margin-top: 4px; }
.pref-meta {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  color: var(--text3);
  margin-top: 4px;
  overflow-wrap: break-word;
}
/* A verb in a list of items is a verb in the margin's grammar — `.nt-btn`,
   squared and bordered, rather than a bare word that reads as a label. */
.pref-mute-btn {
  margin-left: auto;
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
  padding: 3px 9px;
  border: 1px solid var(--rule);
  border-radius: 0;
  background: var(--paper);
  color: var(--soft);
  cursor: pointer;
}
.pref-mute-btn:hover { border-color: var(--ink); color: var(--ink); }
.pref-mute-btn:disabled { opacity: 0.5; cursor: default; border-color: var(--rule); color: var(--soft); }
.pref-muted-note { margin-top: 6px; font-size: 11px; color: var(--text3); }
.pref-muted-note code { font-family: 'Fragment Mono', monospace; font-size: 10px; color: var(--text2); }

/* ─── Dead-session overlay (#174) ──────────────────────────
   Same materials as the two dialogs above, but no close control and no
   Escape: dismissing it hands the reviewer back a tab whose submits go
   nowhere. Orange, the connection-lost banner's weight. z-index clears the
   palette's 1200 (the skip link's 2000 stays inside the inert set). */
.dead-overlay {
  position: fixed; inset: 0; z-index: 1300;
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
  background: var(--scrim);
}
.dead-panel {
  width: min(520px, 92vw);
  padding: 22px 24px;
  background: var(--paper);
  border: 1px solid var(--orange);
  border-radius: 0;
  box-shadow: 0 18px 50px var(--scrim);
}
.dead-title {
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--orange);
  margin: 0 0 10px;
}
.dead-body { margin: 0; font-size: 13px; line-height: 1.55; color: var(--text); }
.dead-resume { margin: 14px 0 0; font-size: 12px; color: var(--text2); }
/* `user-select: all` — the whole command in one click, since retyping it is
   the only way back into this review. */
.dead-resume code {
  font-family: 'Fragment Mono', monospace;
  font-size: 12px;
  color: var(--ink);
  background: var(--bg3);
  padding: 2px 6px;
  user-select: all;
}

/* ─── Processing / Complete states ──────────────────────── */
/* Between-rounds card — the round is in the agent's hands. A pulsing dot
   (alive, not busy) over the reviewer's own just-submitted changes/info
   requests, echoed verbatim. Zero rows fall back to the minimal line;
   type colors reuse the verdict slots (changes → orange, info → violet). */
@keyframes viva-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.25; }
}

/* The interlude is the same PAGE, waiting — not a splash screen with the
   document taken away. It keeps the page's left edge and measure, and the
   reviewer's just-submitted requests print in the margin's own note grammar. */
.processing-inner {
  padding: 3.5rem 0 8rem;
  color: var(--text2);
}
.processing-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--acc);
  animation: viva-pulse 1.6s ease-in-out infinite;
  margin-bottom: 14px;
}
.processing-text {
  font-family: ui-monospace, 'SF Mono', 'Fragment Mono', Menlo, monospace;
  font-size: 13px;
  letter-spacing: 0.04em;
  color: var(--ink);
  border-bottom: 1px solid var(--rule);
  padding-bottom: 10px;
  margin-bottom: 16px;
}
/* The echoed requests take the PAGE's measure. `#processing-view` keeps the
   page's left edge and its measure rather than centering — a bare 460px cap
   honored only the first half of that. */
.processing-requests { max-width: 72ch; }

.complete-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8rem 2rem;
  text-align: center;
}
/* Approval stamp — the drafting world's gesture for "signed off". Double-ruled,
   teal ink, slammed onto the sheet at a slight angle as the review completes. */
.approve-stamp { transform: rotate(-5deg); margin-bottom: 2rem; animation: stamp-down 0.42s cubic-bezier(0.2, 1.4, 0.4, 1) both; }
.stamp-rule {
  border: 2px solid var(--teal);
  color: var(--teal);
  padding: 14px 30px 12px;
  position: relative;
  /* The machine's own token, not the neon mint this was drawn in before the
     catalog palette landed — the only hardcoded rgba left on this view. */
  background: var(--teal-bg);
}
.stamp-rule::before { content: ''; position: absolute; inset: 3px; border: 1px solid var(--teal); opacity: 0.55; }
.stamp-word { font-family: 'Fragment Mono', monospace; font-size: 2.1rem; font-weight: 600; letter-spacing: 0.16em; }
.stamp-meta { font-family: 'Fragment Mono', monospace; font-size: 0.72rem; letter-spacing: 0.12em; opacity: 0.85; margin-top: 6px; text-transform: uppercase; }
.stamp-sub  { font-family: 'Fragment Mono', monospace; font-size: 0.7rem; letter-spacing: 0.08em; opacity: 0.6; margin-top: 2px; }
@keyframes stamp-down {
  0%   { opacity: 0; transform: rotate(-5deg) scale(2.1); }
  60%  { opacity: 1; }
  100% { opacity: 1; transform: rotate(-5deg) scale(1); }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  .approve-stamp, .card, .ledger, .transmittal, .doc-section, .processing-dot { animation: none; }
  .card-body-wrap, .progress-fill, .btn-skip, .btn-submit, .ledger-chevron, .transmittal-chevron { transition: none; }
}
.complete-headline {
  font-size: 1.6rem;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 0.5rem;
}
.complete-detail {
  font-size: 0.95rem;
  color: var(--text2);
  margin-bottom: 0.25rem;
}
.complete-hint {
  font-family: 'Fragment Mono', monospace;
  font-size: 0.78rem;
  color: var(--text3);
  margin-top: 1.75rem;
}
/* ─── Syntax highlighting ──────────────────────────────────────────
   A theme for highlight.js written inside the ink discipline rather than a
   stock preset, which would spend the reviewer's own colors on syntax:
     - NO catalog yellow — that means the reviewer touched the text.
     - NO red or green — those belong to the suggestion fence alone.
     - Comments recede; keywords carry ink weight; the two hues that do
       appear (teal, amber) are the machine's own. Close to monochrome on
       purpose: the code is a specification, not a rainbow. */
.hljs                { color: var(--ink2); }
.hljs-comment,
.hljs-quote          { color: var(--faint); font-style: italic; }
.hljs-keyword,
.hljs-selector-tag,
.hljs-literal,
.hljs-built_in       { color: var(--ink); font-weight: 600; }
.hljs-string,
.hljs-regexp,
.hljs-addition:not(.hljs-diff) { color: var(--machine); }
.hljs-number,
.hljs-symbol,
.hljs-bullet         { color: var(--fact); }
.hljs-title,
.hljs-name,
.hljs-section,
.hljs-title.function_ { color: var(--ink); font-weight: 600; }
.hljs-attr,
.hljs-attribute,
.hljs-variable,
.hljs-template-variable,
.hljs-params         { color: var(--ink2); }
.hljs-type,
.hljs-class .hljs-title,
.hljs-meta           { color: var(--soft); }
.hljs-emphasis       { font-style: italic; }
.hljs-strong         { font-weight: 700; }
.hljs-link           { text-decoration: underline; }

/* Inside a rendered git diff, hljs's addition/deletion classes mark whole
   lines. This is the one place red and green are correct — the fence and the
   diff are the same object, and every reviewer already reads them. */
.d2h-wrapper .hljs-addition,
pre .hljs-addition { background: rgba(26,127,55,0.12);  color: inherit; }
.d2h-wrapper .hljs-deletion,
pre .hljs-deletion { background: rgba(209,36,47,0.12);  color: inherit; }
</style>
</head>
<body>

<a class="skip-link" id="skip-link-a" href="#main-content">Skip to review</a>

<div id="paper">


<main class="shell" id="main-content" tabindex="-1">

  <!-- ── Review mode ──────────────────────────────────────── -->
  <div id="review-view" style="display:none">
    <div class="header">
      <div class="titleblock">
        <div class="tb-cell tb-flex tb-wide"><h1 class="tb-val mono" id="doc-path"></h1></div>
        <div class="tb-cell"><div class="tb-label">round</div><div class="tb-val mono" id="round-badge"></div></div>
        <div class="tb-cell tb-flex"><div class="tb-val" id="doc-title"></div></div>
        <!-- Review-mode cells (#186): the composite bar states the document's
             whole condition on one line. Ship hidden; initReview reveals them
             and updateReviewStats keeps them current. -->
        <div class="tb-cell" id="tb-checks" style="display:none"><div class="tb-label">checks</div><div class="tb-val mono" id="r-checks">0/0</div></div>
        <div class="tb-cell" id="tb-items" style="display:none"><div class="tb-val mono" id="r-items"></div></div>
        <div class="tb-cell"><div class="tb-label">approved</div><div class="tb-val mono" id="r-progress-label">0 / 0</div></div>
        <div class="tb-cell" id="tb-palette" style="display:none"><button type="button" class="pal-hint" id="pal-open">palette<kbd>&#8984;K</kbd></button></div>
      </div>
      <div class="progress-track" id="r-progress-track">
        <div class="progress-fill" id="r-progress" style="width:0%"></div>
      </div>
    </div>
    <div class="ledger" id="ledger" style="display:none">
      <button type="button" class="ledger-head" id="ledger-head" aria-expanded="true" aria-controls="ledger-body">
        <span class="ledger-title">Revisions &middot; <span id="ledger-count">0</span></span>
        <span class="ledger-chevron" aria-hidden="true">&#9662;</span>
      </button>
      <div class="ledger-body-wrap" id="ledger-body">
        <div class="ledger-body-inner">
          <div class="ledger-rows" id="ledger-rows"></div>
        </div>
      </div>
    </div>
    <nav class="transmittal" id="transmittal" aria-label="What changed this round" style="display:none"></nav>
    <!-- "flags", not "checks": rows are every DOC_SCOPE_KINDS flag, and only
         `headings-present` is also a CHECK_KIND. The visible head says
         "Document · N flags" — naming it checks here would contradict that. -->
    <nav class="transmittal doc-slip" id="doc-slip" aria-label="Document-level flags" style="display:none"></nav>
    <div class="doc-tools" id="doc-tools">
      <div class="doc-hint" id="doc-hint" style="display:none">Select any passage to comment on it &middot; <kbd>&#8984;K</kbd> for every command</div>
      <div class="sort-bar" id="sort-bar" style="display:none">
        <button type="button" class="sort-toggle" id="sort-toggle" title="Reorder the sections by where the agent flagged itself least confident"><span aria-hidden="true">&#8645;</span> sort weakest first</button>
      </div>
    </div>
    <div class="cards" id="review-cards"></div>
  </div>

  <!-- ── Q&A mode ─────────────────────────────────────────────
       The /viva-write interview step; carries the same composite bar as
       review. No progress track — the footer's segmented rule is the
       progress, in state rather than percent. -->
  <div id="qa-view" style="display:none">
    <div class="header">
      <div class="titleblock">
        <div class="tb-cell tb-flex tb-wide"><div class="tb-val mono" id="qa-title"></div></div>
        <div class="tb-cell"><div class="tb-label">phase</div><div class="tb-val mono">Q&amp;A</div></div>
        <div class="tb-cell tb-flex"><div class="tb-val" id="qa-mode-title">viva <em>interview</em></div></div>
        <div class="tb-cell"><div class="tb-label">questions</div><div class="tb-val mono" id="qa-count-badge"></div></div>
        <div class="tb-cell"><div class="tb-label">answered</div><div class="tb-val mono" id="qa-progress-label">0 / 0</div></div>
        <div class="tb-cell"><button type="button" class="pal-hint" id="qa-pal-open">palette<kbd>&#8984;K</kbd></button></div>
      </div>
    </div>
    <div class="cards" id="qa-cards"></div>
    <div class="doc-hint" id="qa-hint">Pick a choice with <kbd>1</kbd>&ndash;<kbd>9</kbd> &middot; <kbd>c</kbd> to confirm &middot; <kbd>&#8984;K</kbd> for the command palette</div>
  </div>

  <!-- ── Processing / between-rounds state ────────────────── -->
  <div id="processing-view" style="display:none">
    <div class="processing-inner">
      <div class="processing-dot" aria-hidden="true"></div>
      <div class="processing-text" id="processing-heading">Claude is revising…</div>
      <div class="processing-requests" id="processing-requests" style="display:none"></div>
    </div>
  </div>

  <!-- ── Complete state ───────────────────────────────────── -->
  <div id="complete-view" style="display:none">
    <div class="complete-inner">
      <div class="approve-stamp" id="approve-stamp">
        <div class="stamp-rule">
          <div class="stamp-word">APPROVED</div>
          <div class="stamp-meta" id="stamp-meta">viva</div>
          <div class="stamp-sub" id="stamp-sub"></div>
        </div>
      </div>
      <div class="complete-headline" id="complete-headline"></div>
      <div class="complete-detail" id="complete-detail"></div>
      <div class="ledger ledger-static" id="complete-ledger" style="display:none">
        <div class="ledger-head">
          <span class="ledger-title">Revisions &middot; <span id="complete-ledger-count">0</span></span>
        </div>
        <div class="ledger-rows" id="complete-ledger-rows"></div>
      </div>
      <div class="complete-hint">You can close this tab.</div>
    </div>
  </div>

  <details class="kbd-legend">
    <summary>keyboard shortcuts &amp; what the counts mean</summary>
    <dl class="kbd-list">
      <dt><kbd>a</kbd></dt><dd>approve section (refused while it has open comments)</dd>
      <dt><kbd>c</kbd></dt><dd>comment &mdash; request changes (review) &middot; confirm answer (Q&amp;A)</dd>
      <dt><kbd>i</kbd></dt><dd>comment &mdash; need info</dd>
      <dt><kbd>Tab</kbd></dt><dd>advance to next card (when focused in one); else moves focus normally &middot; <kbd>Shift</kbd>+<kbd>Tab</kbd> always moves focus back</dd>
      <dt><kbd>r</kbd> <kbd>s</kbd> <kbd>y</kbd> <kbd>n</kbd></dt><dd>on the note that has focus: reply, settle, accept, change anyway</dd>
      <dt><kbd>j</kbd></dt><dd>jump to the next open thread</dd>
      <dt><kbd>l</kbd></dt><dd>open the revision ledger</dd>
      <dt><kbd>Esc</kbd></dt><dd>close the composer (a draft keeps; an empty box cancels), the palette, or the recap</dd>
      <dt><kbd>1</kbd>&ndash;<kbd>9</kbd></dt><dd>pick a choice (Q&amp;A)</dd>
      <dt><kbd>o</kbd></dt><dd>recap overlay (review)</dd>
      <dt><kbd>v</kbd></dt><dd>voice &mdash; the oral examination; Escape stops listening from anywhere</dd>
      <dt><kbd>&#8984;/Ctrl</kbd>+<kbd>K</kbd></dt><dd>command palette &mdash; every verb on this page, by name</dd>
      <dt><kbd>t</kbd></dt><dd>cycle theme &mdash; system, light, dark</dd>
      <dt><kbd>&#8984;/Ctrl</kbd>+<kbd>Enter</kbd></dt><dd>approve &mdash; dispatch the round</dd>
    </dl>
    <!-- Every aggregate the bar and footer print, defined once, in the
         reader's reach — a reviewer who cannot reproduce the arithmetic
         stops trusting the numbers. `title` on the two cells is a mouse
         convenience; this list is what states them. -->
    <dl class="kbd-list term-list">
      <dt>item</dt><dd>one thing with a state: a carried thread, a comment you made, an unanswered check, or a section's own sign-off. A producer flag is advisory and is not an item.</dd>
      <dt>open</dt><dd>items still waiting on someone &mdash; judgment (changes, suggestions, declines) plus facts (questions, unanswered checks). Everything else is settled.</dd>
      <dt>convergence</dt><dd>open items when this round was armed &rarr; open items now. Falling means you are closing more than you open.</dd>
      <dt>approved</dt><dd>sections carrying an approved verdict, out of all sections. A section with feedback is reviewed but not approved.</dd>
      <dt>checks</dt><dd>producer checks that carry an answer, out of all of them. A document-level check is counted here and nowhere else.</dd>
    </dl>
  </details>

</main>

</div><!-- /#paper -->

<!-- Bottom bar. `position: fixed` is the containing block .foot-seg hangs
     off. Ships empty and hidden — updateReviewStats fills it in review
     mode only, since a diff or Q&A round has no document balance. -->
<div class="bottom-bar" id="bottom-bar-el">
  <div class="foot-seg" id="foot-seg" style="display:none"></div>
  <!-- The voice transcript. Its own aria-live region rather than a cell
       inside #stats-area's: the counters announce a count, this a sentence,
       and one region cannot pace both. Ships hidden until voice is on. -->
  <div class="voice-strip" id="voice-strip" aria-live="polite" style="display:none"></div>
  <div class="sr-only" id="sr-status" role="status" aria-live="polite"></div>
  <div class="bottom-inner">
    <div class="stats" id="stats-area">
      <!-- The live region is the COUNTERS, not the whole bar: the three
           toggles beside them rewrite their own labels, and inside the region
           every repaint re-read them on top of the button's own name change. -->
      <span class="stat-run" id="stat-run" aria-live="polite">
      <span class="stat-pending"  id="stat-pending"></span>
      <!-- Review-mode footer (#186). `convergence`: is the reviewer closing
           more than they open. `round trip`: the last same-origin request
           this page made, a measurement not a claim. -->
      <span class="stat-conv" id="stat-conv" style="display:none"></span>
      <span class="stat-lat"  id="stat-lat"  style="display:none"></span>
      </span>
      <div class="bar-controls">
        <button type="button" class="prefs-toggle" id="prefs-toggle" style="display:none">learned prefs</button>
        <!-- Ships hidden and stays hidden where the browser has no recognizer:
             a control that cannot work is worse than no control. `initVoice`
             reveals it. -->
        <button type="button" class="voice-toggle" id="voice-toggle" style="display:none">voice: off</button>
        <button type="button" class="theme-toggle" id="theme-toggle"
                title="Cycle theme: follow system, light, dark">theme: system</button>
      </div>
    </div>
    <div class="btn-group">
      <button type="button" class="btn-skip" id="btn-skip">skip rest &amp; submit</button>
      <button type="button" class="btn-submit disabled" id="btn-submit" aria-disabled="true">approve &mdash; dispatch</button>
    </div>
  </div>
</div>

<!-- Recap overlay — the submit gate for review/diff modes. Ships hidden;
     openRecap() rebuilds the index grid from live verdict state each open.
     Q&A never opens it: the done → path submits directly. -->
<div class="recap-overlay" id="recap-overlay" role="dialog" aria-modal="true" aria-labelledby="recap-title" style="display:none">
  <div class="recap-panel">
    <div class="recap-head">
      <span class="recap-title" id="recap-title">Recap &middot; REV <span id="recap-round"></span></span>
      <button type="button" class="recap-close" id="recap-close" aria-label="Close recap"><kbd>esc</kbd></button>
    </div>
    <div class="recap-grid" id="recap-grid"></div>
    <div class="recap-actions">
      <span class="recap-blocked" id="recap-blocked"></span>
      <button type="button" class="btn-skip" id="recap-skip" style="display:none">skip rest &amp; submit</button>
      <button type="button" class="btn-submit ready" id="recap-confirm">confirm &amp; submit</button>
    </div>
  </div>
</div>

<!-- Preferences panel (#142) — view/mute learned preferences without
     leaving the tab. Ships hidden, empty; renderPrefsList() fills it from
     the preferences fetched once at boot. Reachable in every mode. -->
<div class="prefs-overlay" id="prefs-overlay" role="dialog" aria-modal="true" aria-labelledby="prefs-title" style="display:none">
  <div class="prefs-panel">
    <div class="prefs-head">
      <div style="display: flex; align-items: center; justify-content: space-between;">
        <span class="prefs-title" id="prefs-title">Learned Preferences</span>
        <button type="button" class="prefs-close" id="prefs-close" aria-label="Close preferences"><kbd>esc</kbd></button>
      </div>
      <div class="prefs-help"><strong>standing:</strong> recurred 2+ sessions, applied at rewrite &mdash; still yours to approve &bull; <strong>candidate:</strong> new, waiting to recur &bull; <strong>muted:</strong> won't be applied or flagged</div>
    </div>
    <div class="prefs-status" id="prefs-status" aria-live="polite"></div>
    <div class="prefs-list" id="prefs-list"></div>
  </div>
</div>

<!-- Dead-session overlay (#174) — the SSE connection dropping signals the
     server behind this tab is gone (see es.onerror). Ships hidden;
     showDeadSession() reveals it, only es.onopen takes it down again.
     `alertdialog`, not `dialog`: no close control, nothing to choose. -->
<div class="dead-overlay" id="dead-overlay" role="alertdialog" aria-modal="true"
     aria-labelledby="dead-title" aria-describedby="dead-body" style="display:none">
  <div class="dead-panel" id="dead-panel" tabindex="-1">
    <h2 class="dead-title" id="dead-title">Session ended</h2>
    <p class="dead-body" id="dead-body">This tab lost its review server. Nothing submitted from here can reach it &mdash; resume from the terminal.</p>
    <p class="dead-resume" id="dead-resume" style="display:none">resume: <code id="dead-cmd"></code></p>
  </div>
</div>

<!-- Command palette (⌘K, #186) — a directory of verbs the page already
     carries as controls and keycaps, never a second interaction model.
     Ships hidden and empty; openPalette() fills the list from live state. -->
<div class="pal-overlay" id="pal-overlay" style="display:none">
  <div class="pal" role="dialog" aria-modal="true" aria-label="Command palette">
    <input type="text" class="pal-input" id="pal-input" placeholder="&gt; type a command" autocomplete="off"
           aria-label="Command" role="combobox" aria-expanded="true" aria-controls="pal-list" aria-autocomplete="list">
    <div class="pal-list" id="pal-list" role="listbox" aria-label="Commands"></div>
  </div>
</div>

<script>
/* ─────────────────────────────────────────────────────────
   DATA
───────────────────────────────────────────────────────── */
let REVIEW_DATA = null;
let QA_DATA = null;
// Fetched once at boot alongside /input, reused for every card build after
// (including a round 2+ SSE rebuild) — never re-fetched mid-session (#142).
let PREFS_DATA = [];
let PREFS_BY_ID = new Map();

/* ─────────────────────────────────────────────────────────
   STATE
   Cards are built ONCE. All interactions do surgical DOM
   updates — no innerHTML rebuilds, no animation resets.
───────────────────────────────────────────────────────── */
const rState = { verdicts: {}, active: null };
const qState = { answers: {}, active: null };
const _pendingMarkdown = new Map(); // section id → raw markdown; deleted after first render

/* ─────────────────────────────────────────────────────────
   HELPERS
───────────────────────────────────────────────────────── */
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// `.rev-tri`'s title (tooltip) text. `revision_count_partial` means a
// historical round file couldn't be read, so any count is a lower bound —
// say "≥N", never assert N as fact. Checked on every section with a `diff`
// this round, not only ones past the 2+ threshold, since the unreadable
// round could be the one that would have pushed it over.
function revTriTooltip(round, section) {
  const base = `revised at REV ${String(round).padStart(2,'0')}`;
  if (section.revision_count >= 2) {
    return section.revision_count_partial
      ? `${base} · ≥${section.revision_count} revisions, partial history`
      : `${base} · ${section.revision_count} content revisions this session`;
  }
  return section.revision_count_partial
    ? `${base} · partial history, revision count unavailable`
    : base;
}

function tabDocName(path) {
  return (path || '').split('/').pop();
}

// Session identity, not per-event data (#172) — the repo name is fixed for
// the tab's life, stashed once where it enters rather than threaded
// through every setTabTitle call site.
let TAB_REPO = null;

function setTabTitle(...parts) {
  document.title = [TAB_REPO, ...parts].filter(Boolean).concat('viva').join(' · ');
}

// The 'processing' SSE handler's own title setter, kept distinct from
// setTabTitle: it fires the instant a round is submitted, before the
// server has a fresh doc/round, so it only has the PRIOR round's doc name.
function setProcessingTabTitle(docName) {
  document.title = [TAB_REPO, docName, 'working…'].filter(Boolean).concat('viva').join(' · ');
}

// Turn-state colors for the inline data: URI favicon — no network fetch,
// mirroring the CSS custom properties for the same states rather than a
// second palette. Swaps the <link>'s href in place; setTabTitle's sibling.
const FAVICON_COLOR = { turn: '2946c4', processing: 'a06a12', done: '0c7f6b' };
function setTabFavicon(state) {
  const color = FAVICON_COLOR[state] || FAVICON_COLOR.turn;
  const link = document.getElementById('favicon-link');
  if (!link) return;
  link.href = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%23" + color + "'/%3E%3C/svg%3E";
}

/* Render verbatim markdown into el. Falls back to raw monospace text if
   marked/DOMPurify haven't loaded yet (`defer` scripts) — both are required
   before HTML is committed to the DOM, since parsing without sanitizing
   would render untrusted markdown's raw HTML unescaped. Returns true on a
   real render, false on the fallback, so callers can retry later. */
function renderMarkdown(target, md) {
  if (window.marked && window.DOMPurify) {
    const html = marked.parse(md);
    target.innerHTML = DOMPurify.sanitize(html);
    target.classList.remove('md-raw');
    if (window.hljs) {
      target.querySelectorAll('code[class^="language-"]').forEach(b => hljs.highlightElement(b));
    }
    return true;
  }
  target.classList.add('md-raw');
  target.textContent = md;
  return false;
}

// section.title for diff-mode sections is "{filepath} hunk N" (parse_diff.py).
// Strip the " hunk N" suffix to recover the filepath. Shared by
// diffFileHunkCounts and renderDiffHunk.
function filepathFromTitle(title) {
  return String(title || '').replace(/\s+hunk\s+\d+$/, '');
}

// _ensureRendered only has a section id at render time; renderDiffHunk
// needs the section's title to synthesize the file preamble diff2html
// expects. Delegates to reviewSectionTitles() — the one id→title lookup.
function sectionTitleFor(id) {
  return reviewSectionTitles().get(id) || '';
}

/* Render one git hunk via diff2html: unified, line-by-line, word-level
   intra-line diffs. Pure view transform — section.content stays the
   verbatim fence other logic depends on; the ---/+++ preamble diff2html
   needs is synthesized here at render time only, from the title's
   filepath, and never stored.
   Pipeline order is load-bearing: Diff2Html.html() gives markup as a
   STRING, DOMPurify sanitizes the string, and only then does it touch the
   DOM — materializing first would let insertion-time payloads execute
   before removal. Falls back to the fenced-```diff markdown view (tagged
   d2h-pending for the load listeners to upgrade) if diff2html can't parse
   the hunk or its assets haven't loaded. */
function renderDiffHunk(target, raw, title) {
  const body = raw.replace(/^```diff\n/, '').replace(/\n```$/, '');
  if (!/^@@ /.test(body)) return renderMarkdown(target, raw);
  const cssLink = el('diff2html-css');
  if (!(window.Diff2Html && window.Diff2HtmlUI && window.DOMPurify && cssLink && cssLink.sheet)) {
    const ok = renderMarkdown(target, raw);
    if (ok) target.classList.add('d2h-pending');
    return ok;
  }
  const fp = filepathFromTitle(title);
  const diff = '--- a/' + fp + '\n+++ b/' + fp + '\n' + body;
  try {
    const rawHtml = Diff2Html.html(diff, {
      drawFileList: false,
      colorScheme: 'auto',
      matching: 'words',
      diffStyle: 'word',
      // UNIFIED, always. Side-by-side splits the hunk into two panes that
      // each scroll independently — at a 1440px viewport each pane is only
      // 445px (53 chars), while unified's 892px shows 107. Word-level
      // diffs survive either format via `diffStyle: 'word'`.
      outputFormat: 'line-by-line',
    });
    target.innerHTML = DOMPurify.sanitize(rawHtml);
  } catch (e) {
    return renderMarkdown(target, raw);
  }
  target.classList.remove('d2h-pending');
  // Line numbers are visual chrome: unselectable via CSS (anchor hygiene),
  // and hidden from screen readers here — they'd otherwise announce before
  // every code line.
  target.querySelectorAll('.d2h-code-linenumber')
    .forEach(n => n.setAttribute('aria-hidden', 'true'));
  // Slim UI wrapper constructed with an undefined diff wraps the existing
  // (sanitized) DOM; hljs is the page's own instance, passed in because the
  // slim bundle deliberately doesn't embed one.
  try {
    new Diff2HtmlUI(target, undefined, { highlight: true }, window.hljs).highlightCode();
  } catch (e) { /* syntax color only; word-level diff survives */ }
  target.classList.remove('md-raw');
  return true;
}

function el(id) { return document.getElementById(id); }
// Reduced motion is honored in script as well as in CSS: every programmatic
// scroll asks this instead of hardcoding `smooth`.
const SMOOTH = (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) ? 'auto' : 'smooth';
// The footer prints a round trip only once it is worth knowing about.
const SLOW_RTT_MS = 200;
// One status line for refusals — an approve pressed on a section with
// comments open, a save pressed on an empty box. Cleared and re-set on a
// tick so the same sentence announces twice.
function announce(text) {
  const n = el('sr-status'); if (!n) return;
  n.textContent = '';
  setTimeout(() => { n.textContent = text; }, 30);
}

function ledgerRowsHTML(entries) {
  return entries.map(e => `
    <div class="ledger-row">
      <span class="ledger-round">R${esc(e.round)}</span>
      <span class="ledger-section">${esc(e.section_title)}</span>
      <span class="ledger-verdict v-${e.verdict}">${e.verdict}</span>
      <span class="ledger-note">${e.note ? '&ldquo;' + esc(e.note) + '&rdquo;' : '&mdash;'}</span>
    </div>`).join('');
}

function renderLedger() {
  const entries = (REVIEW_DATA && REVIEW_DATA.ledger) || [];
  const panel = el('ledger');
  if (!entries.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  el('ledger-count').textContent = entries.length;
  el('ledger-rows').innerHTML = ledgerRowsHTML(entries);
  el('ledger').classList.toggle('is-collapsed', entries.length > 2);
  const head = el('ledger-head');
  const paint = () => head.setAttribute('aria-expanded',
    el('ledger').classList.contains('is-collapsed') ? 'false' : 'true');
  paint();
  head.onclick = () => { el('ledger').classList.toggle('is-collapsed'); paint(); };
}

// The palette's and `l`'s one path to the ledger: open, expand, scroll.
function openLedger() {
  const p = el('ledger');
  if (!p || p.style.display === 'none') return;
  p.classList.remove('is-collapsed');
  el('ledger-head')?.setAttribute('aria-expanded', 'true');
  p.scrollIntoView({ behavior: SMOOTH, block: 'nearest' });
}

/* ─── Transmittal slip (round >= 2, review mode only) ────────
   One row per section: revised (to your note, or silent), flagged &
   unreviewed, or approved & unchanged. Pure classification, no DOM — diff
   mode ships none since hunk identity is positional across rounds. */
const FLAG_RANK = { error: 0, warn: 1 };

// Strongest flag severity on a section: 0 (error), 1 (warn), or null.
/* The author's turn on a thread, answered FOR THIS ROUND: a response, or
   grounds (key presence — a decline with no grounds still counts). The
   `round - 1` freshness check stops a stale answer re-reading as news later. */
function authorAnswered(t, round) {
  const last = ((t || {}).exchanges || []).slice(-1)[0] || {};
  if (Number(last.round) !== round - 1) return false;
  return Boolean(last.response) || last.grounds !== undefined;
}

function sectionAnswered(s, round) {
  return ((s || {}).open_notes || []).some(t => authorAnswered(t, round));
}

// A DOC_SCOPE flag is skipped: it's a fact about the document, not this
// section, and `checklist` emits severity:"error" — without this, one missing
// template heading would brand section 1 "flagged & unreviewed".
function flagRank(section) {
  const ranks = ((section && section.annotations) || [])
    .filter(a => a && !DOC_SCOPE_KINDS.includes(a.kind))
    .map(a => FLAG_RANK[(a || {}).severity])
    .filter(r => r !== undefined);
  return ranks.length ? Math.min(...ranks) : null;
}

function transmittalHTML(data) {
  if (!data || data.mode !== 'review' || !(data.round > 1)) return '';
  const approved = new Set(data.approved_ids || []);
  // A carried row reflects a prior-round stamp that still stands. A withdrawn
  // approval clears rState's verdict, dropping the row from carried (and
  // reappearing as flagged if it still carries annotations).
  const carriedNow = id => approved.has(id) && rState.verdicts[id]?.verdict === 'approved';
  const revisedNoted = [], revisedBare = [], flaggedErr = [], flaggedWarn = [],
        answered = [], carried = [];
  (data.sections || []).forEach(s => {
    const hasDiff  = Array.isArray(s.diff) && s.diff.length > 0;
    const hasNotes = Array.isArray(s.open_notes) && s.open_notes.length > 0;
    if (hasDiff) { (hasNotes ? revisedNoted : revisedBare).push(s); return; }
    if (carriedNow(s.id)) { carried.push(s); return; }
    // The author answered the reviewer's note with no edit — a decline (#167)
    // or a response needing none. Checked AFTER carried, since a signed-off
    // section is settled, not news; a stale answer falls through to flagRank.
    if (sectionAnswered(s, data.round)) { answered.push(s); return; }
    const rank = flagRank(s);
    if (rank !== null) { (rank === 0 ? flaggedErr : flaggedWarn).push(s); return; }
  });
  const row = (s, cls, marker, label) =>
    '<button type="button" class="transmittal-row ' + cls + '" data-target="' + esc(s.id) + '">'
    + '<span class="tr-marker" aria-hidden="true">' + marker + '</span>'
    + '<span class="tr-label">' + label + '</span>'
    + '<span class="tr-title">' + esc(s.title) + '</span></button>';
  // A revised row names its cause when the diff answers the reviewer's own
  // open note; a silent revision stays bare.
  const revisedRow = s => {
    const noted = Array.isArray(s.open_notes) && s.open_notes.length > 0;
    return row(s, noted ? 'tr-revised-note' : 'tr-revised', '&#9651;',
               noted ? 'revised to your note' : 'revised');
  };
  const rows = revisedNoted.concat(revisedBare).map(revisedRow).concat(
    // News before unreviewed machine output: an answer is the author's turn,
    // a flag is a producer's.
    answered.map(s => row(s, 'tr-answered', '&#8627;', 'answered, not revised')),
    flaggedErr.map(s => row(s, 'tr-flag-error', '&#9873;', 'flagged &amp; unreviewed')),
    flaggedWarn.map(s => row(s, 'tr-flag-warn', '&#9873;', 'flagged &amp; unreviewed')),
    carried.map(s => row(s, 'tr-approved', '&#9635;', 'approved &amp; unchanged')));
  if (!rows.length) return '';
  // The head is a disclosure, collapsed by default: the slip is the round's
  // cover note, not its content, so a reader meets the document first
  // rather than a bordered index of it (issue #186).
  return '<button type="button" class="transmittal-head" id="transmittal-head" aria-expanded="false"'
    + ' aria-controls="transmittal-rows"><span class="transmittal-title">Transmittal &middot; REV '
    + esc(String(data.round).padStart(2, '0')) + ' &middot; ' + rows.length
    + (rows.length === 1 ? ' change' : ' changes')
    + '</span><span class="transmittal-chevron" aria-hidden="true">&#9662;</span></button>'
    + '<div class="transmittal-rows" id="transmittal-rows" hidden>' + rows.join('') + '</div>';
}

function renderTransmittal() {
  const panel = el('transmittal');
  if (!panel) return;
  const html = transmittalHTML(REVIEW_DATA);
  if (!html) { panel.style.display = 'none'; panel.innerHTML = ''; return; }
  panel.innerHTML = html;
  panel.style.display = '';
  panel.querySelectorAll('.transmittal-row').forEach(btn => {
    btn.addEventListener('click', () => activateReviewCard(btn.dataset.target));
  });
  const head = el('transmittal-head'), body = el('transmittal-rows');
  if (head && body) head.addEventListener('click', () => {
    body.hidden = !body.hidden;
    head.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');
  });
}

/* ─── The document slip ──────────────────────────────────────
   Every doc-scope flag in the round, once, in section order — stated once as
   a slip instead of five amber lines duplicated in section 1's margin. */
function documentFlags() {
  return ((REVIEW_DATA && REVIEW_DATA.sections) || [])
    .flatMap(s => docFlagSplit(s).doc);
}

function docSlipHTML() {
  /* Every mode renders this, not review alone: `docFlagSplit` routes a
     doc-scope flag out of both columns unconditionally, so gating it here
     would make the flag render NOWHERE while round_is_complete still enforces it. */
  if (!REVIEW_DATA) return '';
  const flags = documentFlags();
  if (!flags.length) return '';
  // The checks tally rides in the head because `sectionSpec` no longer draws
  // one: today's only CHECK_KIND is doc-scope, so without this the gate would
  // have no readout anywhere while round_is_complete keeps enforcing it.
  const checks = flags.filter(a => CHECK_KINDS.includes(a.kind));
  const done = checks.filter(a => a.result).length;
  // Collapsed like the transmittal, UNLESS the document carries an error:
  // demoting a document-level error to a digit behind a disclosure is a
  // severity claim nobody made.
  const open = flags.some(a => a.severity === 'error');
  return '<button type="button" class="transmittal-head" id="doc-slip-head" aria-expanded="'
    + (open ? 'true' : 'false') + '" aria-controls="doc-slip-rows">'
    + '<span class="transmittal-title">Document &middot; ' + flags.length
    + (flags.length === 1 ? ' flag' : ' flags')
    + (checks.length ? ' &middot; checks ' + done + '/' + checks.length : '')
    + '</span><span class="transmittal-chevron" aria-hidden="true">&#9662;</span></button>'
    + '<div class="transmittal-rows" id="doc-slip-rows"' + (open ? '' : ' hidden') + '>'
    // Rows dedupe `result`; the tally above does NOT — it's computed off raw
    // flags, else `checks D/T` would misread `1/5` when all five were answered
    // with one sentence.
    + dedupeResults(flags, new Set()).map(marginFlagHTML).join('') + '</div>';
}

function renderDocSlip() {
  const panel = el('doc-slip');
  if (!panel) return;
  const html = docSlipHTML();
  if (!html) { panel.style.display = 'none'; panel.innerHTML = ''; return; }
  panel.innerHTML = html;
  panel.style.display = '';
  const head = el('doc-slip-head'), body = el('doc-slip-rows');
  if (head && body) head.addEventListener('click', () => {
    body.hidden = !body.hidden;
    head.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');
  });
}

/* ─────────────────────────────────────────────────────────
   REVIEW MODE — build once, update surgically
───────────────────────────────────────────────────────── */
// Diff mode only: how many sections share each filepath. parse_diff.py emits
// every hunk of a file contiguously, so one pass here suffices — no lookahead
// needed while iterating in the render loop below.
function diffFileHunkCounts(sections) {
  const counts = new Map();
  sections.forEach(s => {
    const fp = filepathFromTitle(s.title);
    counts.set(fp, (counts.get(fp) || 0) + 1);
  });
  return counts;
}

function initReview() {
  _pendingMarkdown.clear();
  const container = el('review-cards');
  // Both surfaces wear the margin grammar; only review prints continuously.
  // `.doc` arms every grid rule, `.print` only the ones for every section
  // being open at once — no runtime branch needed in CSS.
  const asDoc = isContinuousPrint();
  container.classList.add('doc');
  container.classList.toggle('print', asDoc);
  // Stamped on <body> like `mode-diff`, because the page width it sets has to
  // reach the shell and the bottom bar, which are outside #review-cards.
  document.body.classList.toggle('mode-doc', asDoc);
  el('doc-hint').style.display = '';
  // The composite's bar has no progress track: the footer's segmented rule is
  // the document's progress, in state rather than in percent, and two bars
  // saying the same thing differently is one bar too many.
  el('r-progress-track').style.display = 'none';
  // `round 2 · line` — the round and the pass it was armed for, the way the
  // composite states it. `pass.kind` is boundary-validated against PASS_KINDS,
  // and a diff round can be armed with one too.
  if (REVIEW_DATA.pass && REVIEW_DATA.pass.kind) {
    el('round-badge').textContent =
      String(REVIEW_DATA.round).padStart(2, '0') + ' · ' + REVIEW_DATA.pass.kind;
  }
  const priorApprovedSet = new Set(REVIEW_DATA.approved_ids || []);
  // Pre-populate approved state for sections approved in previous rounds
  priorApprovedSet.forEach(id => {
    rState.verdicts[id] = { verdict: 'approved', note: '' };
  });
  // File-header grouping (diff mode only): a static divider ahead of each
  // contiguous run of hunks sharing a filepath. hunkCounts stays null in
  // review mode, so the check below is always false there.
  const hunkCounts = REVIEW_DATA.mode === 'diff' ? diffFileHunkCounts(REVIEW_DATA.sections) : null;
  let lastFilepath = null;
  let animIdx = 0;
  REVIEW_DATA.sections.forEach((s, i) => {
    if (hunkCounts) {
      const fp = filepathFromTitle(s.title);
      if (fp !== lastFilepath) {
        const header = document.createElement('div');
        header.className = 'file-group-header';
        const n = hunkCounts.get(fp);
        header.textContent = fp + ' · ' + n + ' hunk' + (n === 1 ? '' : 's');
        container.appendChild(header);
        lastFilepath = fp;
      }
    }
    // Sections approved in a prior round (round >= 2) collapse to carried
    // cards — a head-only line with the read-only content one reveal away.
    // Round 1 keeps the normal-card path even when a resume pre-approves ids.
    //
    // Continuous print retires that collapse in review mode (#186): a settled
    // section DIMS IN PLACE, since reading the document is still the point.
    // buildCarriedCard stays diff-mode's path, where a carried hunk has nothing to read.
    const isCarried = !asDoc && REVIEW_DATA.round > 1 && priorApprovedSet.has(s.id);
    const card = asDoc ? buildDocSection(s, i)
                       : isCarried ? buildCarriedCard(s) : buildReviewCard(s);
    // Carried cards appear instantly (no fade) — only new/changed cards get
    // the staggered fade-in, re-indexed among themselves so the stagger stays
    // tight regardless of how many sections are already carried.
    if (isCarried) {
      card.style.animation = 'none';
    } else {
      card.style.animationDelay = Math.min(0.04 + animIdx * 0.04, 0.3) + 's';
      animIdx++;
    }
    container.appendChild(card);
    // Apply approved CSS immediately for round-1 pre-approved normal cards;
    // carried cards bake their collapsed state into their own markup.
    if (!isCarried && priorApprovedSet.has(s.id)) syncReviewCard(s.id);
  });
  // Continuous print renders every section up front — nothing to open, so
  // nothing to render lazily. retryOnceScriptsLoad selects on the
  // `.md-raw`/`.d2h-pending` marker classes, not pending state, so it keeps working.
  if (asDoc) REVIEW_DATA.sections.forEach(s => _ensureRendered(s.id));
  // Where round >= 2 LANDS: the first section carrying something new (a
  // revision, or a thread the author answered) — not the first unapproved
  // section, which would re-show a flag wall the reader already read.
  // `!priorApprovedSet.has` is belt-and-braces against a resume that
  // pre-approves differently than `_load_approved` expects.
  const newBusiness = (isContinuousPrint() && REVIEW_DATA.round > 1)
    ? REVIEW_DATA.sections.find(s => !priorApprovedSet.has(s.id)
        && ((Array.isArray(s.diff) && s.diff.length > 0)
            || sectionAnswered(s, REVIEW_DATA.round)))
    : null;
  // Open first non-approved card
  const firstPending = REVIEW_DATA.sections.find(s => !priorApprovedSet.has(s.id));
  const landing = newBusiness || firstPending || REVIEW_DATA.sections[0];
  if (landing) activateReviewCard(landing.id);
  updateReviewStats();
  renderLedger();
  renderTransmittal();
  // Per-round static: doc-scope flags never change with a verdict, so this is
  // the only call site — the two verdict paths that re-render the transmittal
  // have nothing to say to it, and `/next-round` re-enters initReview.
  renderDocSlip();
  setupCardSort();
}

// Severity → CSS-slot whitelist. Anything off-list (or missing) renders as
// 'info' so a bad value can never break out of the class= attribute position.
const ANNOT_SEVERITIES = { info: 1, warn: 1, error: 1 };

// Advisory annotation strip built from section.annotations (returns '' when
// none). Maps every section id → title for the round, so an annotation
// anchored to another section can render a deep-link to it.
function reviewSectionTitles() {
  const m = new Map();
  ((typeof REVIEW_DATA !== 'undefined' && REVIEW_DATA.sections) || [])
    .forEach(s => m.set(s.id, s.title));
  return m;
}

// A kind:"preference" annotation encodes its id as a leading "[id]" token in
// the message (SKILL.md convention — no structured-field passthrough in
// annotate.py's merge). Unmatched/stale tokens fall back to plain text.
const PREF_ID_RE = /^\[([^\]]+)\]/;

function annotStripHTML(annotations) {
  if (!Array.isArray(annotations) || annotations.length === 0) return '';
  const titles = reviewSectionTitles();
  const rows = annotations.map(a => {
    a = a || {};
    const sev    = ANNOT_SEVERITIES[a.severity] ? a.severity : 'info';
    const kind   = esc(a.kind || 'note');
    const msg    = esc(a.message || '');
    const anchorId = a.anchor != null ? String(a.anchor) : '';
    // Anchor that matches a section id → clickable jump; otherwise hover title.
    const isJump = anchorId && titles.has(anchorId);
    const titleAttr = (anchorId && !isJump) ? ' title="' + esc(anchorId) + '"' : '';
    const jump = isJump
      ? '<button type="button" class="annot-jump" data-target="' + esc(anchorId)
        + '">' + esc(titles.get(anchorId) || anchorId) + ' ↗</button>'
      : '';
    // Badge-to-entry link (#142): a preference annotation whose [id] token
    // matches a fetched preference grows a second jump control, labeled
    // with the preference's own label/id, opening the preferences panel.
    let prefJump = '';
    if (a.kind === 'preference') {
      const m = PREF_ID_RE.exec(a.message || '');
      const pref = m ? PREFS_BY_ID.get(m[1]) : null;
      if (pref) {
        prefJump = '<button type="button" class="annot-jump" data-pref-id="' + esc(pref.id)
          + '">' + esc(pref.label || pref.id) + ' ↗</button>';
      }
    }
    return '<div class="annot annot-' + sev + '"' + titleAttr + '>'
         + '<span class="annot-kind">' + kind + '</span>'
         + '<span class="annot-msg">' + msg + jump + prefJump + '</span></div>';
  }).join('');
  return '<div class="annot-strip" aria-label="pre-review annotations">' + rows + '</div>';
}

// Round-to-round diff block from section.diff (rows of {op, text}); '' when
// none. Presentational only — never touches a verdict. Shown by default, the
// header toggles it collapsed.
// Word-level diff of a paired removed/added line → [delHTML, addHTML] with
// changed tokens wrapped in <span class="dw">, fully escaped. Falls back to
// plain text when the pair shares too little (rewrite noise) or is too large.
function markWordDiff(a, b) {
  // Tokens are word+trailing-whitespace chunks, so bare spaces never count as
  // shared content when judging whether the pair is similar enough to mark.
  const ta = a.split(/(?<=\s)(?=\S)/);
  const tb = b.split(/(?<=\s)(?=\S)/);
  const n = ta.length, m = tb.length;
  if (!n || !m || n * m > 250000) return [esc(a), esc(b)];
  const L = [];
  for (let i = n; i >= 0; i--) L[i] = new Uint16Array(m + 1);
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      L[i][j] = ta[i] === tb[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  if (L[0][0] / Math.max(n, m) < 0.3) return [esc(a), esc(b)];
  const mark = t => '<span class="dw">' + esc(t) + '</span>';
  const oa = [], ob = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (ta[i] === tb[j]) { oa.push(esc(ta[i])); ob.push(esc(tb[j])); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) oa.push(mark(ta[i++]));
    else ob.push(mark(tb[j++]));
  }
  while (i < n) oa.push(mark(ta[i++]));
  while (j < m) ob.push(mark(tb[j++]));
  return [oa.join(''), ob.join('')];
}

function diffStripHTML(id, diff) {
  if (!Array.isArray(diff) || diff.length === 0) return '';
  const line = (cls, g, html) => '<div class="diff-line ' + cls + '">'
    + '<span class="diff-gutter">' + g + '</span>'
    + '<span class="diff-text">' + html + '</span></div>';
  const out = [];
  let k = 0;
  while (k < diff.length) {
    const d = diff[k] || {};
    if (d.op === '@') { out.push('<div class="diff-hunk">' + esc(d.text || '') + '</div>'); k++; continue; }
    if (d.op === '+') { out.push(line('diff-add', '+', esc(d.text || ''))); k++; continue; }
    if (d.op !== '-') { out.push(line('diff-ctx', ' ', esc(d.text || ''))); k++; continue; }
    // A '-' run followed by a '+' run is a rewrite: word-diff the pairs.
    const dels = []; while (k < diff.length && (diff[k] || {}).op === '-') dels.push(String((diff[k++] || {}).text || ''));
    const adds = []; while (k < diff.length && (diff[k] || {}).op === '+') adds.push(String((diff[k++] || {}).text || ''));
    const paired = Math.min(dels.length, adds.length);
    const addHTML = adds.map(esc);
    for (let p = 0; p < dels.length; p++) {
      if (p < paired) {
        const [dh, ah] = markWordDiff(dels[p], adds[p]);
        out.push(line('diff-del', '-', dh));
        addHTML[p] = ah;
      } else out.push(line('diff-del', '-', esc(dels[p])));
    }
    addHTML.forEach(h => out.push(line('diff-add', '+', h)));
  }
  const rows = out.join('');
  return '<div class="diff-block" id="rdiff-' + id + '">'
       + '<button type="button" class="diff-toggle" id="rdiff-toggle-' + id + '">'
       + '<span aria-hidden="true">&#9662;</span> changes since last round</button>'
       + '<div class="diff-body">' + rows + '</div></div>';
}

// Open-note thread for a card from section.open_notes (#16) — the prior
// exchange, carried across rounds until the reviewer settles it. Returns ''
// when there's no open thread.
function openNotesHTML(exchanges) {
  return (exchanges || []).map(x => {
    x = x || {};
    const v = String(x.verdict || '');
    const vClass = (v === 'changes' || v === 'info' || v === 'suggestion') ? ' v-' + v : '';
    return '<div class="exchange">'
      + '<div class="exchange-q">'
      +   '<span class="exchange-round">R' + esc(x.round) + '</span>'
      +   '<span class="exchange-verdict' + vClass + '">' + esc(v) + '</span>'
      +   '<span class="exchange-note">' + esc(x.note || '')
      +     (x.replacement ? '<span class="cmt-repl">' + esc(x.replacement) + '</span>' : '')
      +   '</span>'
      + '</div>'
      // The author's grounds for declining THAT turn, before the response,
      // because it answers the reviewer's request without resolving it. Key
      // presence, not truthiness: a decline with no grounds is still a decline.
      + (x.grounds !== undefined
          ? '<div class="exchange-d">declined: ' + esc(x.grounds) + '</div>' : '')
      + (x.response ? '<div class="exchange-a">' + esc(x.response) + '</div>' : '')
      + '</div>';
  }).join('');
}

// One carried thread as a complete element, shared by both surfaces (#186):
// the doc grid restyles `.open-thread` into the margin's note grammar rather
// than forking markup. `.nh-num` ships empty; only the margin fills it in
// place (renumberDocNotes), so the reply textarea is never rebuilt mid-keystroke.
function openThreadItemHTML(t) {
    const cid = esc(t.cid || '');
    const exs = t.exchanges || [];
    // The thread's current type carries to a reply, defaulting to info — but
    // never suggestion: the reply box collects prose, not replacement wording,
    // and a suggestion with no `replacement` is rejected server-side.
    const last = (exs.length && exs[exs.length - 1].verdict) || 'info';
    const type = (last === 'changes' || last === 'suggestion') ? 'changes' : 'info';
    const quote = t.quote ? '<span class="open-thread-quote">' + esc(t.quote) + '</span>' : '';
    // A declined thread is unresolved, not closed: the author answered and the
    // move is now the reviewer's — settle to accept, or reply to insist (which
    // always wins). Same settle/reply controls, different label and prompt.
    const declined = t.status === 'declined';
    /* One verb per note, with its keycap, rather than a permanently-open reply
       box — too much chrome for a 253px margin. Declined threads lead with
       Accept/Change anyway (settle/reply — an insisting reply always wins);
       open threads offer Reply/Settle. */
    const btn = (cls, label, key, attrs) =>
      '<button type="button" class="nt-btn ' + cls + '"' + (attrs || '') + '>'
      + label + '<kbd>' + key + '</kbd></button>';
    const settle = extra => btn('settle-btn ' + extra, declined ? 'Accept' : 'Settle',
      declined ? 'y' : 's', ' id="rsettle-' + cid + '" data-cid="' + cid + '"');
    const reply = () => btn('thread-reply-btn', declined ? 'Change anyway' : 'Reply',
      declined ? 'n' : 'r',
      ' data-cid="' + cid + '" data-type="' + (declined ? 'changes' : esc(type))
      + '" aria-expanded="false" aria-controls="rreplywrap-' + cid + '"');
    return '<div class="open-thread' + (declined ? ' is-declined' : '')
      + '" id="rthread-' + cid + '" data-cid="' + cid + '">'
      + '<div class="open-thread-head">'
      +   '<span class="nh-num" id="rnum-' + cid + '" aria-hidden="true"></span>'
      +   '<span class="open-thread-label">' + (declined ? THREAD_STATUS_LABELS.declined : THREAD_STATUS_LABELS.open)
      +   '</span><span class="pn">&middot; ' + cid + '</span>' + quote
      + '</div>'
      + '<div class="open-thread-body">' + openNotesHTML(exs) + '</div>'
      + '<div class="nt-acts">'
      +   (declined ? settle('is-pri') + reply() : reply() + settle('is-quiet'))
      + '</div>'
      // Ships hidden; a verb reveals it. wireOpenThread un-hides it on build
      // when a reply is already pending in rState, so a rebuild never loses one.
      + '<div class="thread-reply" id="rreplywrap-' + cid + '" data-cid="' + cid + '" data-type="' + esc(type) + '" hidden>'
      +   '<div class="thread-reply-chips" role="group" aria-label="Reply type">'
      +     '<button type="button" class="cmt-chip cmt-chip-changes' + (type === 'changes' ? ' is-on' : '')
      +       '" data-type="changes" aria-pressed="' + (type === 'changes') + '">request changes</button>'
      +     '<button type="button" class="cmt-chip cmt-chip-info' + (type === 'info' ? ' is-on' : '')
      +       '" data-type="info" aria-pressed="' + (type === 'info') + '">need info</button>'
      +   '</div>'
      +   '<textarea class="thread-reply-field" aria-label="Reply" id="rreply-' + cid + '" data-cid="' + cid
      +     '" placeholder="' + (declined
            ? 'A reply insists, and an insisting reply is binding.'
            : 'Reply… (switch to “request changes” to turn the discussion into an edit)')
      +     '"></textarea>'
      + '</div>'
      + '</div>';   // close .open-thread — unclosed, two threads nested
}

/* ─── Confidence triage (issue #12) ───────────────────────────
   Each section self-annotates with kind:"confidence" (basis: sourced|inferred,
   level: high|medium|low). The reviewer can reorder weakest-first (default:
   document order); sections with none sink to the bottom. */
const LEVEL_RANK = { low: 0, medium: 1, high: 2 };
const BASIS_RANK = { inferred: 0, sourced: 1 };

function confidenceAnnot(section) {
  return (section.annotations || []).find(a => a && a.kind === 'confidence') || null;
}

// Smaller = weaker = shown first. inferred+low → 0 (weakest); sourced+high → 5.
// No confidence annotation → 99, so unknowns sink below ranked cards while
// CSS `order` ties preserve document (DOM) order among them.
function weaknessScore(section) {
  const c = confidenceAnnot(section);
  if (!c) return 99;
  const l = LEVEL_RANK[c.level] === undefined ? 1 : LEVEL_RANK[c.level];
  const b = BASIS_RANK[c.basis] === undefined ? 1 : BASIS_RANK[c.basis];
  return l * 2 + b;
}

function applyCardSort() {
  const conf = rState.sortMode === 'confidence';
  REVIEW_DATA.sections.forEach(s => {
    const card = el('rcard-' + s.id);
    if (card) card.style.order = conf ? String(weaknessScore(s)) : '';
  });
  const btn = el('sort-toggle');
  if (btn) {
    btn.classList.toggle('is-active', conf);
    // The label names what a click DOES; the state is visible in the print.
    btn.innerHTML = conf ? '&#8645; restore document order' : '&#8645; sort weakest first';
    btn.setAttribute('aria-pressed', conf ? 'true' : 'false');
  }
}

function setupCardSort() {
  rState.sortMode = 'document';
  const bar = el('sort-bar');
  // Diff mode's file-header grouping depends on cards staying in document
  // order (CSS `order` would strand the file-group-header divs), so force the
  // toggle off here rather than relying on diff sections lacking confidence.
  const hasConfidence = REVIEW_DATA.mode !== 'diff' && REVIEW_DATA.sections.some(s => confidenceAnnot(s));
  if (bar) bar.style.display = hasConfidence ? '' : 'none';
  applyCardSort();
}

/* ─── The accordion, wearing the margin grammar ─────────────────
   Diff mode's builder: one hunk open at a time via a real disclosure button.
   Old chrome (annotation strip, thread list, note/action rows) moved to the
   margin, beside the lines it concerns, rather than stacking atop the hunk.
   The hunk itself is one `wide` row (`.d2h-wrapper`, layoutDocRows). */
function buildReviewCard(section) {
  const card = document.createElement('div');
  card.className = 'card';
  card.id = 'rcard-' + section.id;

  // Store raw markdown for lazy render on first open
  _pendingMarkdown.set(section.id, section.content ?? '');

  card.innerHTML = `
    <button type="button" class="card-head" aria-expanded="false" aria-controls="rbody-${section.id}">
      <span class="dot dot-idle" id="rdot-${section.id}"></span>
      <span class="card-title-wrap">
        <span class="card-title">${esc(section.title)}</span>
        ${section.summary ? `<span class="section-summary">${esc(section.summary)}</span>` : ''}
        <span class="note-inline" id="rnote-inline-${section.id}" style="display:none"></span>
      </span>
      ${section.diff ? `<span class="rev-tri" title="${revTriTooltip(REVIEW_DATA.round, section)}"><span aria-hidden="true">&#9651;</span> ${String(REVIEW_DATA.round).padStart(2,'0')}${section.revision_count >= 2 ? `<span class="rev-mult"> ${section.revision_count}&times;</span>` : ''}</span>` : ''}
      <span class="vbadge" id="rbadge-${section.id}" style="display:none"></span>
    </button>
    <div class="card-body-wrap" id="rbody-${section.id}">
      <div class="card-body-inner">
        <div class="card-body">
          ${docHeadRowHTML(section.id, `<div id="rseg-${section.id}"></div>${diffStripHTML(section.id, section.diff)}`)}
          <div class="section-content" id="rcontent-${section.id}"></div>
          ${docFootRowHTML(section.id, section.title, { skip: true })}
          <div class="comment-popover" id="rpop-${section.id}" style="display:none"></div>
        </div>
      </div>
    </div>`;

  card.querySelector('.card-head').addEventListener('click', () => {
    toggleReviewCard(section.id);
  });

  wireDocSection(card, section.id);
  return card;
}

/* ─── Carried cards (round >= 2 prior approvals) ────────────────
   A section approved in a prior round collapses to a dimmed, head-only line:
   marker, title, reveal toggle, APPROVED stamp, withdraw control. No
   comment machinery — withdrawing turns it back into a normal card. */
function buildCarriedCard(section) {
  const card = document.createElement('div');
  card.className = 'card is-carried';
  card.id = 'rcard-' + section.id;

  // Keep raw markdown for lazy render on first reveal (same path live cards use).
  _pendingMarkdown.set(section.id, section.content ?? '');

  card.innerHTML = `
    <div class="carried-head">
      <span class="carried-marker">carried</span>
      <span class="card-title">${esc(section.title)}</span>
      <button type="button" class="carried-show" id="rcarried-show-${section.id}" aria-expanded="false" aria-controls="rcarried-body-${section.id}">unchanged since your stamp &mdash; show</button>
      <span class="carried-stamp">APPROVED</span>
      <button type="button" class="carried-withdraw" id="rcarried-withdraw-${section.id}" title="withdraw approval &mdash; reopen this section for review"><span aria-hidden="true">&times;</span> withdraw approval</button>
    </div>
    <div class="carried-body" id="rcarried-body-${section.id}" hidden>
      <div class="section-content" id="rcontent-${section.id}"></div>
    </div>`;

  // The whole head line toggles the reveal (mouse convenience); the show
  // button is the focusable, aria-wired affordance for the same action.
  card.querySelector('.carried-head').addEventListener('click', () => {
    setCarriedShown(section.id, el('rcarried-body-' + section.id).hidden);
  });
  card.querySelector('#rcarried-show-' + section.id).addEventListener('click', e => {
    e.stopPropagation();
    setCarriedShown(section.id, el('rcarried-body-' + section.id).hidden);
  });
  card.querySelector('#rcarried-withdraw-' + section.id).addEventListener('click', e => {
    e.stopPropagation(); withdrawApproval(section.id);
  });
  return card;
}

// Reveal/hide a carried card's read-only content, keeping the show button's
// label and aria-expanded in sync. Rendering stays lazy via _ensureRendered.
function setCarriedShown(id, shown) {
  const body = el('rcarried-body-' + id); if (!body) return;
  body.hidden = !shown;
  if (shown) _ensureRendered(id);
  const btn = el('rcarried-show-' + id);
  if (btn) {
    btn.setAttribute('aria-expanded', shown ? 'true' : 'false');
    btn.innerHTML = 'unchanged since your stamp &mdash; ' + (shown ? 'hide' : 'show');
  }
}

// Withdraw a carried approval: clear the verdict, swap the collapsed carried
// card for a normal accordion card, opened for re-review. The fresh card
// replaces the carried one in place — document order stays canonical.
function withdrawApproval(id) {
  if (rState.verdicts[id]) rState.verdicts[id].verdict = undefined;
  const section = REVIEW_DATA.sections.find(s => s.id === id);
  const old = el('rcard-' + id);
  if (!section || !old) return;
  // buildReviewCard re-arms _pendingMarkdown, so content re-renders lazily
  // even if the carried reveal already consumed it.
  old.replaceWith(buildReviewCard(section));
  activateReviewCard(id);
  updateReviewStats();
  renderTransmittal();   // the withdrawn section is no longer "approved & unchanged"
}

/* ═════════════════════════════════════════════════════════════
   THE DOCUMENT PRINT — doc + margin (issue #186)
   ─────────────────────────────────────────────────────────────
   Review mode's renderer: sections print open in document order as
   `gutter | prose | margin` rows, commentary beside its passage. Diff mode
   keeps the accordion (a hunk has no margin). CHECK_KINDS is injected from
   schema.py to avoid drift. ═════════════════ */
const CHECK_KINDS = __CHECK_KINDS__;
/* DOC_SCOPE_KINDS is injected for the same anti-drift reason. Different axis
   from CHECK_KINDS: that asks "does this gate a checks round", this asks
   "what is this flag ABOUT" (headings-present is in both; unregistered
   fails open as section-scope). */
const DOC_SCOPE_KINDS = __DOC_SCOPE_KINDS__;
/* Thread-status label map, injected for the same reason: a hand-kept copy
   here would drift from scripts/schema.py's Revision History wording, and a
   reviewer would read two vocabularies for the same status. */
const THREAD_STATUS_LABELS = __THREAD_STATUS_LABELS__;

/* ─── The seam: the grammar is not the print ─────────────────
   THE GRAMMAR (margin notes, glyph rail, segmented rule) belongs to anything
   that renders a section. CONTINUOUS PRINT (every section open, settled ones
   dimming in place) is review's alone. `.doc` arms the grammar, `.print`
   arms continuous print — review stamps both, diff/Q&A only the first. No
   `usesMargin()` predicate exists: it would be true at every call site. */
function isContinuousPrint() { return !!(REVIEW_DATA && REVIEW_DATA.mode === 'review'); }

/* ─── Flags: which column a producer flag belongs in ─────────
   The 70px gutter is for a glance (severity glyph + short message). A flag
   carrying an interactive jump (cross-section link, preference badge link)
   isn't a glance, so it routes to the margin via annotStripHTML instead. */
function docFlagSplit(section) {
  const titles = reviewSectionTitles();
  const gutter = [], margin = [], doc = [];
  (section.annotations || []).forEach(a => {
    if (!a) return;
    // A document fact, not a flag on this passage. Producers anchor these to
    // the first card (their only document-level handle), which used to paint
    // five amber lines in section 1's margin. Goes to the document slip instead.
    if (DOC_SCOPE_KINDS.includes(a.kind)) { doc.push(a); return; }
    // A confidence annotation is the agent's self-report about the whole
    // section (drives the triage sort; rendered in the spec table) — not a
    // passage flag, so it's skipped here rather than holding the gutter open.
    if (a.kind === 'confidence') return;
    const anchorId = a.anchor != null ? String(a.anchor) : '';
    const m = a.kind === 'preference' ? PREF_ID_RE.exec(a.message || '') : null;
    const jumps = (anchorId && titles.has(anchorId)) || !!(m && PREFS_BY_ID.get(m[1]));
    (jumps ? margin : gutter).push(a);
  });
  return { gutter, margin, doc };
}

const FLAG_GLYPH = { info: '&#10003;', warn: '&#9651;', error: '&#10007;' };

function flagSeverity(a) {
  return ANNOT_SEVERITIES[a.severity] ? a.severity : 'info';
}

// The rail glyph: locality and severity, nothing else. aria-hidden, because
// the margin line below carries the same flag in words.
function gutterGlyphHTML(a) {
  const sev = flagSeverity(a);
  const full = [a.kind || 'note', a.message || '', a.result ? '→ ' + a.result : '']
    .filter(Boolean).join(' · ');
  return '<span class="lflag lflag-' + sev + '" title="' + esc(full) + '" aria-hidden="true">'
    + FLAG_GLYPH[sev] + '</span>';
}

// The same flag in words, in the margin of its own row — where a 300px column
// can hold `✓ §4 defines "cold start"` without clamping it to `✓ §4 defines
// "cold`, which is what 70px of 9px type did to it.
function marginFlagHTML(a) {
  const sev = flagSeverity(a);
  return '<div class="mflag mflag-' + sev + '" title="' + esc(a.kind || 'note') + '">'
    + '<span class="g" aria-hidden="true">' + FLAG_GLYPH[sev] + '</span>'
    + '<span>' + esc(a.message || '')
    + (a.result ? '<span class="r">&rarr; ' + esc(a.result) + '</span>' : '')
    + '</span></div>';
}

/* One decision, printed once: a duplicate `result` across flags is dropped
   after the first (message still prints). Annotation is COPIED not mutated —
   specHTML/sectionBalance/documentBalance all count `a.result`. `seen` is
   per-call so placeDocFlags stays idempotent on re-sync. */
function dedupeResults(list, seen) {
  return list.map(a => {
    const r = a && a.result;
    if (!r) return a;
    if (seen.has(r)) return Object.assign({}, a, { result: undefined });
    seen.add(r);
    return a;
  });
}

/* ─── Rows ───────────────────────────────────────────────────
   Each top-level markdown block becomes one prose row, so a note can sit
   beside its paragraph rather than the whole section. Code/tables take a
   `wide` row instead. */
function docRow(wide) {
  const row = document.createElement('div');
  row.className = 'row' + (wide ? ' wide' : '');
  const rp = document.createElement('div');
  rp.className = 'rp';
  row.appendChild(rp);
  return row;
}

function layoutDocRows(id) {
  const host = el('rcontent-' + id); if (!host) return;
  if (host.querySelector(':scope > .row')) return;          // already laid out
  // marked/DOMPurify missing → renderMarkdown wrote raw text, no elements.
  // One row keeps the raw fallback inside the grid instead of outside it.
  if (!host.firstElementChild) {
    if (!host.textContent) return;
    const row = docRow(false);
    row.querySelector('.rp').textContent = host.textContent;
    host.textContent = '';
    host.appendChild(row);
    return;
  }
  // The head row already prints the section title; markdown's own leading
  // heading is the same words twice, so it's removed here (the accordion's
  // `.section-content > h1:first-child` CSS hid it instead).
  const first = host.firstElementChild;
  if (first && /^H[1-3]$/.test(first.tagName)) first.remove();
  Array.from(host.children).forEach(node => {
    const wide = node.tagName === 'PRE' || node.tagName === 'TABLE'
              || node.classList.contains('table-wrap') || node.classList.contains('d2h-wrapper');
    const row = docRow(wide);
    host.appendChild(row);
    row.querySelector('.rp').appendChild(node);
  });
}

function docRows(id) {
  const host = el('rcontent-' + id);
  return host ? Array.from(host.querySelectorAll(':scope > .row')) : [];
}

/* The section's foot band: static markup from both builders, a pure query
   (like docHeadRow) so nothing here creates DOM in a render loop.
   `buildCarriedCard` builds no bands, so a carried reveal yields null. */
function docFootRow(id) {
  const sec = el('rcard-' + id);
  return sec ? sec.querySelector('.row-foot') : null;
}

// The row whose prose holds the given occurrence of `text`. Counts
// occurrences across rows in document order, matching the ordinal
// renderHighlights marks. Null when nothing matches — never misplaced.
function rowForAnchor(id, text, occurrence) {
  const t = String(text || '').trim();
  if (!t) return null;
  let n = occurrence > 0 ? occurrence : 0;
  const rows = docRows(id);
  for (const r of rows) {
    const hay = (r.querySelector('.rp') || {}).textContent || '';
    let c = 0, i = hay.indexOf(t);
    while (i >= 0) { c++; i = hay.indexOf(t, i + 1); }
    if (c > n) return r;
    n -= c;
  }
  return rows.find(r => (((r.querySelector('.rp') || {}).textContent) || '').includes(t)) || null;
}

// Side cells are created on demand, never pre-reserved: a row that gains a
// note grows a margin cell; one that never has one carries no empty box.
// Column width is a separate decision (updateDocColumns).
function docCell(row, cls) {
  let cell = row.querySelector(':scope > .' + cls);
  if (!cell) {
    cell = document.createElement('div');
    cell.className = cls;
    if (cls === 'rg') row.insertBefore(cell, row.firstChild);
    else row.appendChild(cell);
  }
  return cell;
}

/* Where a note hangs: a resolved anchor hangs in its row's margin, beside
   its passage. Unanchored/unresolved notes hang at the section's FOOT
   instead — never the head, which isn't an introduction to a whole section. */
function docNoteHost(id, row) {
  const target = row || docFootRow(id);
  if (!target) return null;
  const rm = docCell(target, 'rm');
  let host = rm.querySelector(':scope > .rm-notes');
  if (!host) {
    host = document.createElement('div');
    host.className = 'rm-notes';
    rm.appendChild(host);
  }
  return host;
}

/* ─── Notes: what the margin holds ───────────────────────────
   Two sources kept apart: a carried THREAD is built once and placed once (it
   owns a reply textarea — rebuilding mid-keystroke would steal focus).
   This round's COMMENTS are static text, rebuilt freely on every sync. */
function docNotes(section) {
  const id = section.id;
  const threads = section.open_notes || [];
  const cs = (rState.verdicts[id] || {}).comments || [];
  const out = threads.map(t => ({
    kind: 'thread', cid: t.cid, thread: t,
    comment: cs.find(c => c.cid === t.cid) || null,
    anchor: t.quote ? { text: t.quote, occurrence: 0 } : null,
  }));
  activeComments(id)
    .filter(c => !c.reply && !threads.some(t => t.cid === c.cid))
    .forEach(c => out.push({ kind: 'comment', cid: c.cid, comment: c, anchor: c.anchor || null }));
  return out;
}

// Notes in reading order: by the row their anchor lands in, then order made.
// Unanchored notes sort to the END (`rows.length`), matching where they
// render — the foot band, never the head. An anchor resolving to no row
// degrades the same way: it lands at the foot with its quote echo, no pin.
function docNotesOrdered(section) {
  const rows = docRows(section.id);
  return docNotes(section)
    .map((n, i) => {
      const r = n.anchor ? rowForAnchor(section.id, n.anchor.text, n.anchor.occurrence) : null;
      return Object.assign({}, n, { row: r ? rows.indexOf(r) : rows.length, seq: i });
    })
    .sort((a, b) => a.row - b.row || a.seq - b.seq);
}

function noteTypeOf(n) {
  if (n.kind === 'thread') {
    const last = (n.thread.exchanges || []).slice(-1)[0] || {};
    return last.verdict === 'changes' || last.verdict === 'suggestion' ? last.verdict : 'info';
  }
  return n.comment.type === 'changes' || n.comment.type === 'suggestion' ? n.comment.type : 'info';
}

// The exact wording a note proposes, from either source: this round's
// suggestion comment, or a carried thread whose last turn was one.
function noteReplacement(n) {
  if (n.kind === 'comment') return n.comment.replacement || '';
  const last = (n.thread.exchanges || []).slice(-1)[0] || {};
  return last.verdict === 'suggestion' ? (last.replacement || '') : '';
}

/* D's fence, squared: the reviewer's replacement against the wording it
   replaces. Red and green live here and nowhere else — the fence and the
   diff are the same object, and diff semantics already own those colors. */
function suggestionFenceHTML(c) {
  const was = (c.anchor || {}).text || '';
  return '<div class="fence"><div class="fence-h">suggestion &middot; ' + esc(c.cid) + '</div>'
    + (was ? '<div class="fence-ln fence-del"><span class="fence-g" aria-hidden="true">&minus;</span>'
           + '<span class="fence-tx">' + esc(was) + '</span></div>' : '')
    + '<div class="fence-ln fence-add"><span class="fence-g" aria-hidden="true">+</span>'
    + '<span class="fence-tx">' + esc(c.replacement) + '</span></div></div>';
}

function commentNoteHTML(n) {
  const c = n.comment;
  const word = c.type === 'suggestion' ? 'suggestion' : c.type === 'info' ? 'question' : 'comment';
  const cls = c.type === 'info' ? ' nt-fact' : '';
  /* The fence is for a suggestion the prose couldn't show applied (code, or
     an unresolved anchor). When markAndPin DID splice it inline, the note
     says so instead of printing the same two strings twice. */
  const showsInline = !!(c.replacement && n.placedInline);
  return '<div class="nt' + cls + '" data-cid="' + esc(c.cid) + '">'
    + '<div class="nh"><span class="nh-num">' + n.num + '</span> you &mdash; ' + word
    + '<span class="pn">&middot; ' + esc(c.cid) + '</span></div>'
    + (c.anchor && c.anchor.text && c.type !== 'suggestion'
        ? '<span class="nt-quote">' + esc(c.anchor.text) + '</span>' : '')
    + (c.note ? '<div class="nt-body">' + esc(c.note) + '</div>' : '')
    + (showsInline ? '<div class="nt-applied">applied above &mdash; struck wording out, '
                   + 'replacement on yellow</div>' : '')
    + (c.replacement && !showsInline ? suggestionFenceHTML(c) : '')
    + '<div class="nt-acts">'
    +   '<button type="button" class="nt-btn is-quiet cmt-del" data-cid="' + esc(c.cid) + '">remove</button>'
    + '</div></div>';
}

/* ─── The state run ──────────────────────────────────────────
   The transmittal slip's successor at section scale, and the foot band's
   answer to "what is open here" — stated as a spec, not described. */
function sectionSpec(section) {
  const id = section.id;
  const threads = section.open_notes || [];
  const cs = (rState.verdicts[id] || {}).comments || [];
  const isSettled = cid => cs.some(c => c.cid === cid && c.settled);
  let comments = 0, suggestions = 0, declined = 0;
  threads.forEach(t => {
    if (isSettled(t.cid)) return;
    if (t.status === 'declined') { declined++; return; }
    const last = (t.exchanges || []).slice(-1)[0] || {};
    if (last.verdict === 'suggestion') suggestions++; else comments++;
  });
  activeComments(id).filter(c => !c.reply && !threads.some(t => t.cid === c.cid))
    .forEach(c => { if (c.type === 'suggestion') suggestions++; else comments++; });
  // A doc-scope check is a fact about the document, and its readout is the
  // document slip's own `checks D/T` tally — not this section's state.
  const checks = (section.annotations || []).filter(
    a => a && CHECK_KINDS.includes(a.kind) && !DOC_SCOPE_KINDS.includes(a.kind));
  return { comments, suggestions, declined,
           checks: checks.length, checksDone: checks.filter(a => a.result).length };
}

function specHTML(section) {
  const s = sectionSpec(section);
  // Nothing open and nothing checked: no state run at all. Keeps the FOOT
  // band's height independent of which section is live (renderDocSpec), and
  // leaves section 1's band as bare verbs when its only flags are doc-scope.
  const conf0 = confidenceAnnot(section);
  if (!s.comments && !s.suggestions && !s.declined && !s.checks && !conf0) return '';
  // A RUN, not a table: five label/value pairs fit one line at 10.5px mono
  // vs ~120px for a table. `<caption>` is gone; `.doc-apparatus`'s
  // `role="group"`/`aria-label` names the band instead.
  const item = (label, value, open) =>
    '<span class="sp' + (open ? ' sp-open' : '') + '">'
    + '<span class="sp-k">' + label + '</span> <span class="sp-v">' + value + '</span></span>';
  // The agent's own confidence is a state item, not a gutter flag — it's
  // what the triage sort orders on. `docFlagSplit` sends it to neither
  // column because this is its readout; drop it here and it goes invisible.
  const conf = confidenceAnnot(section);
  // Each count prints only when nonzero — three zeros were the run's usual
  // content on a typed round. A decline is OPEN judgment (sectionBalance
  // agrees), so it takes the open ink like the other two.
  return (s.comments ? item('comments open', s.comments, true) : '')
    + (s.suggestions ? item('suggestions open', s.suggestions, true) : '')
    + (s.declined ? item(THREAD_STATUS_LABELS.declined, s.declined, true) : '')
    + (s.checks ? item('checks', s.checksDone + '/' + s.checks
        + (s.checksDone === s.checks ? ' &#10003;' : ''), s.checksDone < s.checks) : '')
    + (conf ? item('agent confidence',
        [conf.basis, conf.level].filter(Boolean).map(esc).join(' &middot; ') || esc(conf.message || '—'),
        conf.level === 'low') : '');
}

/* ─── Segmented rule ─────────────────────────────────────────
   Every open item is JUDGMENT (reviewer's call) or a FACT (question,
   unanswered check, producer flag); resolved is SETTLED. Fixed order is a
   colorblind-safe second encoding; raw counts ride the aria-label. */
function sectionBalance(section) {
  const id = section.id;
  const threads = section.open_notes || [];
  const cs = (rState.verdicts[id] || {}).comments || [];
  const isSettled = cid => cs.some(c => c.cid === cid && c.settled);
  let judgment = 0, facts = 0, settled = 0;
  threads.forEach(t => {
    if (isSettled(t.cid)) { settled++; return; }
    const last = (t.exchanges || []).slice(-1)[0] || {};
    if (t.status === 'declined' || last.verdict === 'changes' || last.verdict === 'suggestion') judgment++;
    else facts++;
  });
  activeComments(id).filter(c => !c.reply && !threads.some(t => t.cid === c.cid))
    .forEach(c => { if (c.type === 'changes' || c.type === 'suggestion') judgment++; else facts++; });
  (section.annotations || []).forEach(a => {
    if (!a) return;
    // A DOCUMENT fact is not this section's item. Placed ABOVE the CHECK_KINDS
    // branch on purpose: `headings-present` is doc-scope AND a check kind, so
    // this stops five document facts being counted as section-1 items.
    // An answered doc-scope check contributes no `settled` here — it's
    // counted once, in documentBalance's own checks/checksDone pair.
    if (DOC_SCOPE_KINDS.includes(a.kind)) return;
    if (CHECK_KINDS.includes(a.kind)) { if (a.result) settled++; else facts++; return; }
    // A PLAIN producer flag is not an item: `.mflag` is advisory, nothing
    // the reviewer does closes it. Counting it as open painted a section with
    // one warn flag as a 100%-wide amber bar even with every check answered.
  });
  /* The section's own sign-off is an item in BOTH states — otherwise a fresh
     round with unanswered checks would print `0 items · 0 open` when the
     legend defines it as nonzero. It rides as its own field, not folded into
     `judgment`: `documentBalance` counts a pending sign-off as open, but
     `segHTML` (judgment/facts/settled only) should not paint it — a section
     whose only open item is its sign-off still draws the settled hairline. */
  const signoff = deriveVerdict(id) === 'approved' ? 0 : 1;
  if (!signoff) settled++;
  return { judgment, facts, settled, signoff };
}

function segHTML(bal) {
  const total = bal.judgment + bal.facts + bal.settled;
  if (!total) return '';
  // Nothing open: the thin settled hairline. A state bar on a settled
  // section is decoration, and decoration is what this ground removed.
  if (!bal.judgment && !bal.facts) return '<div class="rule-s"></div>';
  const pct = n => (n / total * 100).toFixed(2) + '%';
  const seg = (cls, n) => n ? '<i class="' + cls + '" style="width:' + pct(n) + '"></i>' : '';
  const label = 'open: ' + bal.judgment + ' judgment, ' + bal.facts + ' fact'
    + (bal.facts === 1 ? '' : 's') + '; ' + bal.settled + ' settled';
  return '<div class="seg" role="img" aria-label="' + esc(label) + '">'
    + seg('seg-judgment', bal.judgment) + seg('seg-fact', bal.facts)
    + seg('seg-settled', bal.settled) + '</div>';
}

/* Single-track head row: heading, number, summary, segmented rule, collapsed
   diff — no margin cell (finding 01: margin is for a note beside its
   passage, not section state). Verbs live in the foot band
   (`docFootRowHTML`). */
function docHeadRowHTML(id, proseHTML) {
  return '<div class="row row-head"><div class="rp">' + proseHTML + '</div></div>';
}

/* Foot band: what the head row's margin used to hold, laid out horizontally
   under the section. Static markup from both builders, never created on
   demand, which keeps `docFootRow` a pure query. Must stay a SIBLING of
   `.section-content`, not a child, or `docRows`/`rowForAnchor`/
   `docNotesOrdered`/`markAndPin`/`proseWalker` would have to filter it out
   of the document walk (#95). Verbs lead, state trails
   (`.spec-strip{order:2}`); `skip` is the accordion's alone — the print has
   nothing to skip TO. */
function docFootRowHTML(id, title, opts) {
  const skip = !!(opts && opts.skip);
  return '<div class="row row-foot">'
    + '<div class="rp">'
    +   '<div class="doc-apparatus" role="group" aria-label="'
    +     esc(title) + ' &mdash; state and actions">'
    +     '<div class="spec-strip" id="rspecbody-' + id + '"></div>'
    +     '<div class="nt-acts doc-acts">'
    +       '<button type="button" class="nt-btn is-pri" id="rbtn-primary-' + id + '">'
    +         '<span aria-hidden="true">&#10003;</span> approve<kbd>a</kbd></button>'
    +       '<button type="button" class="nt-btn is-quiet" id="rcmtnote-' + id + '">+ note</button>'
    +       (skip ? '<button type="button" class="nt-btn is-quiet" id="rbtn-skip-' + id + '">'
                  + '<span aria-hidden="true">&#8595;</span> skip</button>' : '')
    +     '</div>'
    +   '</div>'
    + '</div>'
    + '</div>';
}

/* Approve must stay focusable by pointer and Tab — with no action row, a
   note-less section would otherwise have no focusable element at all
   (test_server_a11y). ⌘K is a second path to the same verb, never the only
   one. `root` addresses by id, so the head/foot split is invisible here. */
function wireDocSection(root, id) {
  root.querySelector('#rbtn-primary-' + id).addEventListener('click', e => {
    e.stopPropagation();
    if (deriveVerdict(id) === 'approved') docWithdraw(id); else approveSection(id);
  });
  root.querySelector('#rcmtnote-' + id).addEventListener('click', e => {
    e.stopPropagation(); openCommentPopover(id, {});
  });
  const skip = root.querySelector('#rbtn-skip-' + id);
  if (skip) skip.addEventListener('click', e => { e.stopPropagation(); skipReviewCard(id); });

  const diffToggle = root.querySelector('#rdiff-toggle-' + id);
  if (diffToggle) {
    // Ships collapsed. "What changed since last round" is not what the reader
    // opened the document to read, and at full width above the prose it was
    // the single largest thing between them and the text.
    root.querySelector('#rdiff-' + id).classList.add('collapsed');
    diffToggle.addEventListener('click', e => {
      e.stopPropagation();
      root.querySelector('#rdiff-' + id).classList.toggle('collapsed');
    });
  }

  // A pin is a jump to its own note — the pairing works in both directions.
  root.addEventListener('click', e => {
    const pin = e.target.closest ? e.target.closest('.pin') : null;
    if (!pin) return;
    e.stopPropagation();
    const note = root.querySelector('[data-cid="' + pin.dataset.cid + '"]');
    if (note) {
      note.scrollIntoView({ behavior: SMOOTH, block: 'nearest' });
      // The note's first verb, not its reply box — the box ships hidden now,
      // and focusing a hidden field silently drops the focus on the floor.
      const target = note.querySelector('.nt-btn, textarea:not([hidden])');
      if (target) target.focus({ preventScroll: true });
    }
  });
}

/* ─── Build ──────────────────────────────────────────────────
   The section element keeps the `rcard-` id the rest of the app addresses
   sections by, so activateReviewCard, advanceFrom, the transmittal jumps
   and the Tab handler all keep working against it unchanged. */
function buildDocSection(section, index) {
  const id = section.id;
  const sec = document.createElement('section');
  sec.className = 'doc-section';
  sec.id = 'rcard-' + id;
  sec.setAttribute('aria-labelledby', 'rhead-' + id);

  _pendingMarkdown.set(id, section.content ?? '');

  sec.innerHTML = docHeadRowHTML(id, `
        <h2 class="doc-head" id="rhead-${id}"><span class="doc-num" aria-hidden="true">${index + 1} &middot;</span> ${esc(section.title)}</h2>
        ${section.summary ? `<div class="section-summary">${esc(section.summary)}</div>` : ''}
        <div id="rseg-${id}"></div>
        ${diffStripHTML(id, section.diff)}`) + `
    <div class="section-content" id="rcontent-${id}"></div>`
    + docFootRowHTML(id, section.title) + `
    <div class="comment-popover" id="rpop-${id}" style="display:none"></div>`;

  // The live section follows the reader without scrolling — jump paths
  // (transmittal rows, pins, palette) still scroll. Print-only: in the
  // accordion the disclosure button makes a section live instead.
  sec.addEventListener('mousedown', () => activateReviewCard(id, { noScroll: true }));
  sec.addEventListener('focusin',   () => activateReviewCard(id, { noScroll: true }));

  wireDocSection(sec, id);
  return sec;
}

// Withdraw in continuous print: nothing was ever collapsed, so this is just
// the verdict reverting to pending — the prose stays put.
function docWithdraw(id) {
  if (rState.verdicts[id]) rState.verdicts[id].verdict = undefined;
  syncReviewCard(id);
  updateReviewStats();
  renderTransmittal();
}

/* ─── Place: flags, threads, notes, pins ─────────────────────
   Called once per section, then surgically on every sync. Idempotent — a
   thread already in the right cell is left alone, since moving its DOM node
   would blur a focused reply textarea. */
function placeDocFlags(id) {
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  const split = docFlagSplit(section);
  const byRow = new Map();
  // Guards against a future section-scope CHECK_KIND — today `docFlagSplit`
  // routes doc-scope kinds to the slip (`docSlipHTML`), and `headings-present`
  // is the only CHECK_KIND, so this loop sees none yet.
  const seenResults = new Set();
  split.gutter.forEach(a => {
    const row = a.anchor != null ? rowForAnchor(id, String(a.anchor), 0) : null;
    const key = row || docFootRow(id);
    if (!key) return;
    if (!byRow.has(key)) byRow.set(key, []);
    byRow.get(key).push(a);
  });
  // Glyph in the rail, words in the margin, both on the row the flag concerns.
  byRow.forEach((flags, row) => {
    docCell(row, 'rg').innerHTML = flags.map(gutterGlyphHTML).join('');
    // AFTER the rail, deliberately: the glyph's `title` also carries
    // `→ result`, but a tooltip appears one at a time and is not a wall.
    flags = dedupeResults(flags, seenResults);
    const rm = docCell(row, 'rm');
    let host = rm.querySelector(':scope > .rm-flags');
    if (!host) {
      host = document.createElement('div');
      host.className = 'rm-flags';
      // Above the threads and this round's notes: the machine's reading of the
      // paragraph comes before the conversation about it.
      rm.insertBefore(host, rm.firstChild);
    }
    host.innerHTML = flags.map(marginFlagHTML).join('');
  });
  if (split.margin.length) {
    const host = docNoteHost(id, null);
    // Idempotent: _ensureRendered can run twice on the md-raw path (eager
    // loop, then activateReviewCard) without clearing _pendingMarkdown, so
    // without this guard the strip would stack twice.
    if (host && !host.querySelector(':scope > .annot-strip')) {
      host.insertAdjacentHTML('afterbegin', annotStripHTML(split.margin));
      host.querySelectorAll('.annot-jump').forEach(btn => {
        btn.addEventListener('click', e => {
          e.stopPropagation();
          const prefId = btn.getAttribute('data-pref-id');
          if (prefId) openPrefsPanel(btn, prefId);
          else activateReviewCard(btn.getAttribute('data-target'));
        });
      });
    }
  }
}

function placeDocThreads(id) {
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  const threads = section.open_notes || [];
  if (!threads.length) return;
  const sec = el('rcard-' + id); if (!sec) return;
  threads.forEach(t => {
    let node = el('rthread-' + t.cid);
    if (!node) {
      const holder = document.createElement('div');
      holder.innerHTML = openThreadItemHTML(t);
      node = holder.firstElementChild;
      wireOpenThread(id, node);
      // A rebuild (the late-load retry replaces the container's innerHTML,
      // threads included) must not lose a reply the reviewer already typed —
      // the text lives in rState, so put it back in the box.
      const pending = ((rState.verdicts[id] || {}).comments || [])
        .find(c => c.cid === t.cid && c.reply && c.note);
      if (pending) {
        const field = node.querySelector('.thread-reply-field');
        if (field) field.value = pending.note;
      }
    }
    const row = t.quote ? rowForAnchor(id, t.quote, 0) : null;
    // Same guard as `placeDocFlags`/`docNoteHost`: an unanchored thread falls
    // back to the section's foot band, which a carried reveal doesn't have.
    // All three fallbacks must move together.
    const host = row || docFootRow(id);
    if (!host) return;
    const rm = docCell(host, 'rm');
    let threadHost = rm.querySelector(':scope > .rm-threads');
    if (!threadHost) {
      threadHost = document.createElement('div');
      threadHost.className = 'rm-threads';
      // Threads precede this round's fresh notes: a carried thread is older
      // business than a comment made a minute ago.
      //
      // With the head row's static `<div class="rm-notes">` gone, the foot
      // band's `.rm` starts empty and this query returns null on the first
      // call — `insertBefore(node, null)` appends, and `docNoteHost` then
      // creates `.rm-notes` after it. Flags → threads → notes still comes out
      // in the documented order, but it comes out that way from
      // `_ensureRendered`'s call order rather than from this line. Do not
      // "simplify" the insertBefore to an append: a rebuild that places a
      // thread AFTER notes already in the cell would reverse them.
      rm.insertBefore(threadHost, rm.querySelector(':scope > .rm-notes'));
    }
    if (node.parentElement !== threadHost) threadHost.appendChild(node);
  });
}

// What a reply MEANS, in one place: `info` keeps the discussion going,
// `changes` turns it into an edit. The chips and the reveal verbs both set it
// here so they can never disagree about which one is lit.
function setThreadReplyType(wrap, type) {
  wrap.dataset.type = type;
  wrap.querySelectorAll('.cmt-chip').forEach(c => {
    const on = c.dataset.type === type;
    c.classList.toggle('is-on', on);
    c.setAttribute('aria-pressed', String(on));
  });
}

// The settle button + reply box wiring, lifted out of buildReviewCard so both
// surfaces bind one thread the same way. `node` is a scope, not one thread:
// the accordion passes its whole card, the margin passes a single thread.
function wireOpenThread(id, node) {
  node.querySelectorAll('.settle-btn').forEach(b =>
    b.addEventListener('click', e => { e.stopPropagation(); settleOpenNotes(id, b.dataset.cid); }));
  // `Reply` / `Change anyway` reveal the box and set what a reply MEANS:
  // insisting on a declined thread is an edit request, never a chat turn.
  node.querySelectorAll('.thread-reply-btn').forEach(b =>
    b.addEventListener('click', e => {
      e.stopPropagation();
      const wrap = node.querySelector('.thread-reply[data-cid="' + b.dataset.cid + '"]');
      if (!wrap) return;
      wrap.hidden = false;
      node.querySelectorAll('.thread-reply-btn[data-cid="' + b.dataset.cid + '"]')
        .forEach(x => x.setAttribute('aria-expanded', 'true'));
      setThreadReplyType(wrap, b.dataset.type);
      const field = wrap.querySelector('.thread-reply-field');
      if (field) field.focus({ preventScroll: true });
    }));
  node.querySelectorAll('.thread-reply').forEach(wrap => {
    const cid = wrap.dataset.cid;
    // A reply already in rState (a rebuild, or a resumed round) keeps its box
    // open — hiding it would hide feedback the reviewer has already given.
    const pending = ((rState.verdicts[id] || {}).comments || [])
      .find(c => c.cid === cid && c.reply && c.note);
    if (pending) {
      wrap.hidden = false;
      const f = wrap.querySelector('.thread-reply-field');
      if (f && !f.value) f.value = pending.note;
    }
    wrap.querySelectorAll('.cmt-chip').forEach(ch => ch.addEventListener('click', e => {
      e.stopPropagation();
      setThreadReplyType(wrap, ch.dataset.type);
      replyToThread(id, cid);   // re-tag any pending reply with the new type
    }));
    const field = wrap.querySelector('.thread-reply-field');
    field.addEventListener('input', () => replyToThread(id, cid));
    field.addEventListener('click', e => e.stopPropagation());
  });
}

// Mark every note's anchor and pin it with the note's own number. One pass
// owns both ends of the pairing, so the number in the text and the number in
// the margin can never disagree.
function markAndPin(id, ordered) {
  const content = el('rcontent-' + id); if (!content) return;
  content.querySelectorAll('.pin').forEach(p => p.remove());
  // A `.sug` unwraps back to the wording it replaced — the `del` half IS the
  // document; the `ins` half is the reviewer's proposal and was never in it.
  content.querySelectorAll('span.sug').forEach(s => {
    const was = s.querySelector('del');
    s.replaceWith(document.createTextNode(was ? was.textContent : ''));
  });
  content.querySelectorAll('mark[class^="cmt-hl-"]').forEach(m =>
    m.replaceWith(document.createTextNode(m.textContent)));
  content.normalize();
  ordered.forEach(n => {
    const a = n.anchor;
    if (!a || !a.text) return;
    const type = noteTypeOf(n);
    const mark = wrapNth(content, a.text, 'cmt-hl-' + type, a.occurrence > 0 ? a.occurrence : 0);
    if (!mark) return;
    // A rendered diff line is a code well too (`.d2h-code-line-ctn`, not
    // `<pre>`): a spliced del/ins pair reads as neither version. The −/+
    // fence in the margin carries a diff suggestion instead.
    n.inCode = !!(mark.closest && mark.closest('pre, .d2h-code-line'));
    const repl = noteReplacement(n);
    let tail = mark;
    /* A suggestion is shown APPLIED in the prose — original struck,
       replacement inserted — except in code, where a spliced del/ins reads
       as broken syntax; there the −/+ fence in the margin carries it
       instead. `n.inCode` decides which. */
    n.placedInline = !!(repl && !n.inCode);
    if (n.placedInline) {
      const sug = document.createElement('span');
      sug.className = 'sug';
      const was = document.createElement('del');
      was.className = 'sug-del';
      was.textContent = mark.textContent;
      const now = document.createElement('ins');
      now.className = 'sug-ins';
      now.textContent = repl;
      sug.append(was, now);          // no text between them — the gap is CSS,
      mark.replaceWith(sug);         // so it can never be counted as prose
      tail = now;
    }
    const declined = n.kind === 'thread' && n.thread.status === 'declined';
    const pin = document.createElement('button');
    pin.type = 'button';
    pin.className = 'pin ' + (declined ? 'pin-author' : type === 'info' ? 'pin-fact' : 'pin-you');
    pin.dataset.cid = n.cid;
    pin.textContent = String(n.num);
    pin.setAttribute('aria-label', 'Go to note ' + n.num);
    // On a diff line the pin leads the line instead of trailing the anchor
    // (`.pin-line`), since only the pin needs to survive a horizontal scroll.
    // Anchored to the LINE (`.d2h-code-line`), not `.d2h-code-line-ctn` —
    // that span doesn't exist on every line kind.
    const line = tail.closest && tail.closest('.d2h-code-line');
    if (line) { pin.classList.add('pin-line'); line.prepend(pin); }
    else tail.after(pin);
  });
}

function renderDocMargin(id) {
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  const sec = el('rcard-' + id); if (!sec) return;
  // Only the dynamic hosts are wiped. Thread notes and their reply textareas
  // are never rebuilt, since moving a focused reply textarea would blur it.
  sec.querySelectorAll('.rm-notes .nt').forEach(n => n.remove());

  const ordered = docNotesOrdered(section);
  ordered.forEach((n, i) => { n.num = i + 1; });
  // Marking runs FIRST. It is what decides whether a suggestion could be shown
  // applied in the prose (`placedInline`) or has to fall back to the margin's
  // −/+ fence, and the note is written from that answer.
  markAndPin(id, ordered);
  ordered.forEach(n => {
    if (n.kind === 'thread') {
      const numEl = el('rnum-' + n.cid);
      if (numEl) numEl.textContent = String(n.num);
      const node = el('rthread-' + n.cid);
      if (!node) return;
      node.classList.toggle('is-settled', !!(n.comment && n.comment.settled));
      // A carried suggestion the prose couldn't show applied (a code anchor)
      // gets the −/+ fence, once — printing it again in prose would repeat
      // both strings.
      const repl = noteReplacement(n);
      if (repl && n.inCode && !node.querySelector('.fence')) {
        const body = node.querySelector('.open-thread-body');
        if (body) body.insertAdjacentHTML('beforeend', suggestionFenceHTML({
          cid: n.cid, replacement: repl, anchor: n.anchor }));
      }
      return;
    }
    // An unanchored note carries `row === rows.length`, so the index read is
    // out of range and yields undefined; `|| null` states that rather than
    // leaning on `undefined || footRow` inside docNoteHost.
    const host = docNoteHost(id, docRows(id)[n.row] || null);
    if (host) host.insertAdjacentHTML('beforeend', commentNoteHTML(n));
  });
  sec.querySelectorAll('.rm-notes .cmt-del').forEach(b =>
    b.addEventListener('click', e => { e.stopPropagation(); removeComment(id, b.dataset.cid); }));

  renderDocSeg(id);
  renderDocSpec(id);
  updateDocColumns();
}

function renderDocSeg(id) {
  const mount = el('rseg-' + id); if (!mount) return;
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  mount.innerHTML = segHTML(sectionBalance(section));
}

/* Drawn for every section with something to state, not only the live one —
   gating on `rState.active` would move the state readout on activation,
   shifting layout under the cursor. The live section is marked at its
   heading instead (border + negative margin), which costs no layout. */
function renderDocSpec(id) {
  const mount = el('rspecbody-' + id); if (!mount) return;
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  mount.innerHTML = specHTML(section);
}

/* The wasted-space rule, decided once for the whole round — reads off
   REVIEW_DATA/rState, not the DOM, because the accordion renders a section's
   rows only when opened; a DOM read would jog every hunk sideways as the
   reviewer navigates. `docNotes` reads rState, so a fresh comment counts the
   same as one that shipped with the round. */
function updateDocColumns() {
  const doc = el('review-cards');
  if (!doc || !doc.classList.contains('doc')) return;
  const sections = (REVIEW_DATA && REVIEW_DATA.sections) || [];
  const gutter = sections.some(s => docFlagSplit(s).gutter.length);
  // An open compose popover holds the margin open like a saved note does —
  // without it, the first anchored comment on a bare doc mounts its textarea
  // into a 0px track. `.is-open` is checked rather than the inline style,
  // which is the browser's business, not a selector's.
  //
  // `split.gutter` is deliberately NOT counted: a gutter flag with no
  // resolved row still lands in the foot band's margin, which is what the
  // `.doc.no-margin .row-foot` CSS twin covers instead of widening this check.
  const margin = sections.some(s => docFlagSplit(s).margin.length || docNotes(s).length)
    || !!doc.querySelector('.rm .comment-popover.is-open');
  doc.classList.toggle('no-gutter', !gutter);
  // The print never collapses its margin — an empty margin is still the
  // measure, so the prose stays at a fixed width instead of rewrapping the
  // moment the first composer opens. The accordion still collapses.
  doc.classList.toggle('no-margin', !margin && !isContinuousPrint());
}

// Open/close a card, keeping the header button's aria-expanded in sync.
// `is-active` is the single source of truth for "expanded", so every site that
// flips it routes through here — otherwise aria-expanded desyncs on auto-advance
// or programmatic activation and lies to screen readers.
function setCardExpanded(cardEl, expanded) {
  if (!cardEl) return;
  cardEl.classList.toggle('is-active', expanded);
  const head = cardEl.querySelector('.card-head');
  if (head) head.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  // A body clipped to 0fr is still in the tab order unless it is inert — a
  // keyboard reader walked nine invisible stops per closed card. Focus that
  // was inside moves to the head rather than being dropped on the floor.
  const wrap = cardEl.querySelector('.card-body-wrap');
  if (wrap) {
    if (!expanded && wrap.contains(document.activeElement) && head) head.focus({ preventScroll: true });
    wrap.inert = !expanded;
  }
}

// `opts.noScroll` marks a passive activation (pointer/tab in continuous
// print) — the live section follows without the page moving. Every explicit
// jump (transmittal row, pin, palette, annotation link) omits it and scrolls.
function activateReviewCard(id, opts) {
  // A carried card has no accordion body to activate — reveal its read-only
  // content and scroll to it instead (annotation jumps, all-carried resumes).
  // It never becomes rState.active: active means "under review".
  const target = el('rcard-' + id);
  if (target && target.classList.contains('is-carried')) {
    setCarriedShown(id, true);
    target.scrollIntoView({ behavior: SMOOTH, block: 'nearest' });
    return;
  }
  const prev = rState.active;
  // Deactivate previous
  if (prev && prev !== id) {
    setCardExpanded(el('rcard-' + prev), false);
    syncReviewDot(prev);
    syncNoteInline(prev);
  }
  rState.active = id;
  _ensureRendered(id);
  const card = el('rcard-' + id);
  if (card) {
    setCardExpanded(card, true);
    if (!(opts && opts.noScroll)) card.scrollIntoView({ behavior: SMOOTH, block: 'nearest' });
  }
  syncReviewDot(id);
}

function _ensureRendered(id) {
  if (!_pendingMarkdown.has(id)) return;
  const contentEl = el('rcontent-' + id);
  if (!contentEl) return;
  const raw = _pendingMarkdown.get(id);
  // Diff mode's hunk content (a fenced ```diff block) renders via diff2html
  // (renderDiffHunk). Binary-change sections have no fence (parse_diff.py's
  // plaintext sentinel) and fall through to renderMarkdown unchanged.
  const isDiffHunk = REVIEW_DATA && REVIEW_DATA.mode === 'diff' && /^```diff\n/.test(raw);
  const rendered = isDiffHunk ? renderDiffHunk(contentEl, raw, sectionTitleFor(id)) : renderMarkdown(contentEl, raw);
  /* A CARRIED card is read-only: no `.row-head`/`.row-foot` (buildCarriedCard
     never builds either band). Running the normal pipeline over it grids a
     body nobody can comment on, and an unanchored carried thread would take
     `placeDocThreads` down `docCell(null, 'rm')`. */
  const card = el('rcard-' + id);
  const carried = !!(card && card.classList.contains('is-carried'));
  if (!rendered) {
    // marked/DOMPurify haven't landed yet: content is raw text, not blocks.
    // The doc print still grids it as one row so a renderer-less boot still
    // reads as a document. The retry re-renders in place once the scripts
    // arrive.
    if (!carried) { layoutDocRows(id); placeDocFlags(id); placeDocThreads(id); renderDocMargin(id); }
    return;
  }
  // A d2h-pending card rendered as fenced markdown but is waiting on
  // diff2html — keep its source so the load listener below can re-render it;
  // deleting it here would strand the card on the fallback view forever.
  if (!contentEl.classList.contains('d2h-pending')) _pendingMarkdown.delete(id);
  if (carried) return;
  // The doc grid distributes the freshly rendered blocks into rows before
  // anything is placed beside them — a note cannot find its paragraph until
  // the paragraph is a row.
  layoutDocRows(id);
  placeDocFlags(id);
  placeDocThreads(id);
  renderDocMargin(id);
}

// One-time-per-script retry for late-loading renderers: a card opened before
// its renderer's deps landed stays tagged .md-raw or .d2h-pending. Re-render
// every card with that marker once the script(s) land — attaching to every
// script means load order never matters. Scoped to the marker class so
// cards not yet opened stay lazily unrendered.
function retryOnceScriptsLoad(scriptIds, selector) {
  const retry = () => {
    document.querySelectorAll(selector).forEach(contentEl => {
      const m = contentEl.id.match(/^rcontent-(.+)$/);
      if (m) _ensureRendered(m[1]);
    });
  };
  scriptIds.forEach(scriptId => {
    const script = el(scriptId);
    if (script) script.addEventListener('load', retry, { once: true });
  });
}
retryOnceScriptsLoad(['marked-script', 'dompurify-script'], '.section-content.md-raw');
retryOnceScriptsLoad(['diff2html-script', 'diff2html-ui-script'], '.section-content.d2h-pending');
// The third d2h dependency — the mode-injected stylesheet — gets its retry
// listener attached at dispatch time, when the <link> actually exists (see
// the diff branch); attaching here would silently no-op on a null element.

function skipReviewCard(id) {
  setCardExpanded(el('rcard-' + id), false);
  rState.active = null;
  syncReviewDot(id);
  const sections = REVIEW_DATA.sections;
  const idx = sections.findIndex(s => s.id === id);
  const rest = [...sections.slice(idx + 1), ...sections.slice(0, idx)];
  const next = rest.find(s => !rState.verdicts[s.id]?.verdict);
  if (next) setTimeout(() => {
    activateReviewCard(next.id);
    // Tab advanced the section; focus advances with it, or the reader is
    // left focused on a control in the section they just left.
    el('rbtn-primary-' + next.id)?.focus({ preventScroll: true });
  }, 80);
}

function toggleReviewCard(id) {
  if (rState.active === id) {
    setCardExpanded(el('rcard-' + id), false);
    rState.active = null;
    syncReviewDot(id);
    syncNoteInline(id);
  } else {
    activateReviewCard(id);
  }
}

// Advance past a just-decided card: close it, add is-approved CSS, auto-advance
// to the next unreviewed card. Does NOT call sync/stats — caller handles that.
function advanceFrom(id) {
  setCardExpanded(el('rcard-' + id), false);
  el('rcard-' + id)?.classList.add('is-approved');
  rState.active = null;
  const sections = REVIEW_DATA.sections;
  const idx = sections.findIndex(s => s.id === id);
  const next = sections.slice(idx + 1).find(s => deriveVerdict(s.id) !== 'approved');
  if (next) setTimeout(() => activateReviewCard(next.id), 80);
}

// Approve = sign off this section. A section with comments cannot approve; the
// primary button only reads "approve" when comments.length === 0.
function approveSection(id) {
  if (activeComments(id).length) {          // guarded by label AND said aloud
    announce('approve is refused while comments are open — settle or remove them first');
    return;
  }
  (rState.verdicts[id] ||= {}).skip = false;
  rState.verdicts[id].verdict = 'approved';
  advanceFrom(id);
  syncReviewCard(id);
  updateReviewStats();
}

/* `c` / `i` open the composer with that type pre-picked. Neither key writes
   a verdict directly — verdict is always DERIVED from `activeComments`
   (#156), so it always carries the note the revise loop acts on. Cancel or
   remove the saved comment to un-derive it. */
function openTypedComment(id, type) {
  const pop = el('rpop-' + id);
  // Already composing: switch the type on the open box. Re-opening rewrites
  // its innerHTML, which would discard a half-typed note and any image
  // already attached. The chip is the one selection path, so drive the chip.
  if (pop && pop.classList.contains('is-open')) {
    const chip = pop.querySelector('.cmt-chip[data-type="' + type + '"]');
    if (chip) chip.click();
    return;
  }
  // A section that already carries comments gets ANOTHER one, not a re-open of
  // the last: a section owns a list (#68), and each comment is its own thread.
  openCommentPopover(id, { type });
}

function syncReviewCard(id) {
  const verdict = rState.verdicts[id]?.verdict || null;

  // Approved dimming
  el('rcard-' + id)?.classList.toggle('is-approved', verdict === 'approved');

  // Dot
  syncReviewDot(id);

  // Badge
  const badge = el('rbadge-' + id);
  if (badge) {
    if (verdict === 'approved') { badge.style.display=''; badge.className='vbadge vbadge-approved'; badge.textContent='approved'; }
    else if (verdict === 'changes') { badge.style.display=''; badge.className='vbadge vbadge-changes'; badge.textContent='changes'; }
    else if (verdict === 'info')    { badge.style.display=''; badge.className='vbadge vbadge-info';    badge.textContent='info'; }
    else badge.style.display = 'none';
  }

  // Primary button
  renderPrimaryButton(id);

  // A verdict feeds both the section's balance (an approval is a settled
  // item) and its spec — neither repaints from the comment path, so approve
  // must call both directly.
  renderDocSeg(id); renderDocSpec(id);

  syncNoteInline(id);
}

/* ─── Comments (multi-comment review) ───────────────────────────
   Verdict is DERIVED, never picked: no active comments → approved/pending;
   any `changes` or `suggestion` comment → changes; otherwise info. Same rule
   in DESIGN.md, SKILL.md, and schema.py's COMMENT_TYPES — keep in sync. */
function commentsOf(id) { return (rState.verdicts[id] ||= {}).comments ||= []; }

// Real, unsettled feedback — basis for the verdict, button count, rendered
// list, and whether a section can approve. A suggestion qualifies on its
// `replacement` alone; its note is optional rationale.
function activeComments(id) {
  return (rState.verdicts[id]?.comments || []).filter(c => !c.settled && (c.note || c.replacement));
}

function deriveVerdict(id) {
  const active = activeComments(id);
  if (active.length === 0) return rState.verdicts[id]?.verdict === 'approved' ? 'approved' : 'pending';
  return active.some(c => c.type === 'changes' || c.type === 'suggestion') ? 'changes' : 'info';
}

function addComment(id, { type, note, anchor, images, replacement }) {
  const cs = commentsOf(id);
  const n = cs.reduce((m, c) => Math.max(m, +(String(c.cid).split('-c')[1] || 0)), 0);
  cs.push({ cid: id + '-c' + (n + 1), type, note: note || '',
            ...(anchor && { anchor }),
            ...(replacement && { replacement }),
            ...(images?.length && { images }),
            open: true, settled: false });
  syncCard(id);
}

function removeComment(id, cid) {
  const v = rState.verdicts[id]; if (!v) return;
  v.comments = (v.comments || []).filter(c => c.cid !== cid);
  syncCard(id);
}

// Repaint everything that derives from a card's comments: dot, primary
// button, the margin (marks, pins, notes, spec, rule).
function syncCard(id) {
  syncReviewDot(id);
  renderPrimaryButton(id);
  // The margin is every surface's comment list now: same job, one column.
  renderDocMargin(id);
  updateReviewStats();
}

// One control, one grammar: approve reads "approve" only while nothing is
// open; an approved section offers to withdraw.
function renderPrimaryButton(id) {
  const btn = el('rbtn-primary-' + id); if (!btn) return;
  const n = activeComments(id).length;
  const approved = deriveVerdict(id) === 'approved';
  btn.className = 'nt-btn ' + (approved || n ? 'is-quiet' : 'is-pri');
  // With comments open the control cannot act, and it says so: the disabled
  // grammar plus a title naming the way out, never an enabled silent no-op.
  // No checkmark on a section whose derived verdict is `changes`.
  const refused = !approved && n > 0;
  btn.setAttribute('aria-disabled', refused ? 'true' : 'false');
  btn.title = refused ? 'approve is refused while comments are open — settle or remove them first' : '';
  btn.innerHTML = approved ? '<span aria-hidden="true">&#8634;</span> withdraw approval'
    : n ? (n + (n === 1 ? ' comment' : ' comments') + ' open')
        : '<span aria-hidden="true">&#10003;</span> approve<kbd>a</kbd>';
}

/* ─── Selection → popover comment creation ─────────────────────
   Finishing a text selection inside a section's rendered content auto-opens
   the comment popover anchored to that selection — no extra click. `mouseup`
   is the "selection finished" signal (selectionchange fires continuously
   mid-drag). A plain click (collapsed selection), a selection outside any
   section content, or one inside the popover itself is ignored. */
document.addEventListener('mouseup', () => {
  // Defer a tick so the browser has finalized the selection after mouseup.
  setTimeout(() => {
    const sel = document.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const text = sel.toString().trim();
    if (!text) return;
    const start = toElement(sel.anchorNode);
    const content = start && start.closest ? start.closest('.section-content') : null;
    if (!content) return;
    // The margin and check gutter are descendants of the section container,
    // so `.section-content` alone doesn't mean "in the document" — the prose
    // cell (`.rp`) is the document.
    if (!start.closest('.rp') || start.closest('.sug-ins')) return;
    const m = content.id.match(/^rcontent-(.+)$/);
    if (!m) return;
    // Which occurrence of a repeated phrase was picked exists only in the
    // rendered content; read the ordinal there and resolve the same ordinal
    // against the markdown source (#95). `getRangeAt(0)`, not anchorNode/
    // focusNode — a backwards drag reports endpoints in reverse order.
    const occurrence = occurrenceInRendered(content, sel.getRangeAt(0), text);
    openCommentPopover(m[1],
      { anchor: { text, offset: offsetInSource(m[1], text, occurrence), occurrence } });
  }, 0);
});

// A selection endpoint may be a text node; normalize to its element.
function toElement(node) {
  return node && node.nodeType === 3 ? node.parentElement : node;
}

/* No cross-pane selection guard needed: a unified hunk is one column in
   source order, so every selection inside it is already contiguous. */

/* ─── Anchor resolution: rendered occurrence → source offset (#95) ─────
   A repeated phrase's identity is which occurrence — read where the
   selection happened, then resolve the same ordinal against the source. The
   ordinal survives a re-render; the offset is what the source edit uses. */

// 0-based ordinal of the selected occurrence of `text`: how many occurrences
// *begin* before the selection starts, counted over the section's own
// rendered content.
function occurrenceInRendered(root, range, text) {
  if (!root.contains || !root.contains(range.startContainer)) return 0;
  // The margin lives inside the section container and echoes annotated
  // wording (.nt-quote, .open-thread-quote); counting it would inflate the
  // ordinal (#95-style bug). Count the prose only.
  const counted = proseOccurrenceBefore(root, range, text);
  if (counted !== null) return counted;
  const all = document.createRange();
  all.selectNodeContents(root);
  const pre = document.createRange();
  pre.selectNodeContents(root);
  try { pre.setEnd(range.startContainer, range.startOffset); }
  catch (e) { return 0; }
  return countStartsBefore(all.toString(), text, pre.toString().length);
}

/* ─── Prose-only text walking ─────────────────────────────────
   Filters out the margin, check gutter, and open popover — section-container
   descendants that are not the text under review. Inert in the accordion,
   so both surfaces share this walk. */
function proseWalker(root) {
  return document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      for (let p = node.parentElement; p && p !== root; p = p.parentElement) {
        const c = p.classList;
        // `.sug-ins` is proposed wording, never in the document — counting
        // it would inflate every later ordinal and anchor a comment to text
        // the author never wrote. `.sug-del` is real source text and stays
        // counted.
        if (c && (c.contains('rm') || c.contains('rg') || c.contains('comment-popover')
                  || c.contains('sug-ins')))
          return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
}

// 0-based ordinal counted over prose text only, or null when the selection
// starts somewhere the walk can't place (an element boundary) — the caller
// then falls back to the Range count.
function proseOccurrenceBefore(root, range, text) {
  if (!text) return 0;
  const walk = proseWalker(root);
  let hay = '', pre = null, node;
  while ((node = walk.nextNode())) {
    if (node === range.startContainer) pre = hay.length + Math.max(0, range.startOffset);
    hay += node.nodeValue;
  }
  return pre === null ? null : countStartsBefore(hay, text, pre);
}

// Occurrences of `needle` in `hay` that start before index `limit`. Steps by 1
// so an overlapping repeat ("aa" in "aaaa") counts the same way nthIndexOf
// resolves it — the two must agree or the ordinal names a different span in
// the source than it did on screen.
function countStartsBefore(hay, needle, limit) {
  if (!needle) return 0;
  let n = 0, i = hay.indexOf(needle);
  while (i >= 0 && i < limit) { n++; i = hay.indexOf(needle, i + 1); }
  return n;
}

// Index of the `n`th (0-based) occurrence of `needle` in `hay`, -1 if there is
// no such occurrence.
function nthIndexOf(hay, needle, n) {
  if (!needle) return -1;
  let i = hay.indexOf(needle);
  while (i >= 0 && n > 0) { i = hay.indexOf(needle, i + 1); n--; }
  return i;
}

// Char offset of the reviewer's chosen occurrence in the section's raw
// markdown source. -1 means "unplaced," not "absent" — the anchor still
// stores text + occurrence, and the agent scopes by section rather than
// guessing a match.
function offsetInSource(id, text, occurrence) {
  const src = _pendingMarkdown.get(id)
    || REVIEW_DATA.sections.find(s => s.id === id)?.content || '';
  const n = occurrence > 0 ? occurrence : 0;
  const at = nthIndexOf(src, text, n);
  // Rendered and source occurrence counts can diverge (markdown syntax
  // stripped, diff chrome added). An overrun ordinal still resolves when the
  // source holds exactly one match; with two or more it stays unresolved
  // rather than silently collapsing to the first.
  if (at < 0 && n > 0 && nthIndexOf(src, text, 1) < 0) return nthIndexOf(src, text, 0);
  return at;
}

// A small popover: type chips + note field + save/cancel. `anchor` is
// {text, offset} or null (whole-section note); `type` pre-picks a chip,
// defaulting to `changes`. `suggest wording` adds a replacement field
// (review mode only — diff hunks scope it out, #166).
function openCommentPopover(id, { anchor, type } = {}) {
  const pop = el('rpop-' + id); if (!pop) return;
  pop.dataset.type = 'changes';
  const canSuggest = !REVIEW_DATA || REVIEW_DATA.mode !== 'diff';
  const captureState = {};
  // Where focus goes back to when the box closes: the control that opened
  // it, or the section's own `+ note` verb when a selection opened it.
  pop._returnTo = (document.activeElement && document.activeElement !== document.body)
    ? document.activeElement : el('rcmtnote-' + id);
  pop.innerHTML =
      '<div class="cmt-pop-row" role="group" aria-label="Comment type">'
    +   '<button type="button" class="cmt-chip cmt-chip-changes is-on" data-type="changes" aria-pressed="true">request changes</button>'
    +   '<button type="button" class="cmt-chip cmt-chip-info" data-type="info" aria-pressed="false">need info</button>'
    +   (canSuggest ? '<button type="button" class="cmt-chip cmt-chip-suggestion" data-type="suggestion" aria-pressed="false">suggest wording</button>' : '')
    + '</div>'
    + (anchor ? '<div class="cmt-pop-quote">' + esc(anchor.text) + '</div>' : '')
    + '<textarea class="note-field cmt-pop-note" aria-label="Comment" placeholder="Describe the change or question…"></textarea>'
    + '<div class="thumb-strip" style="display:none" aria-live="polite"></div>'
    + '<button type="button" class="attach-btn">attach image</button>'
    + (voiceSupported()
        ? '<button type="button" class="mic-btn">dictate</button>'
        : '')
    + '<input type="file" accept="image/*" multiple style="display:none">'
    + '<div class="cmt-pop-row"><button type="button" class="cmt-save">save</button>'
    +   '<button type="button" class="cmt-cancel">cancel</button></div>';
  // The popover composes a MARGIN note, so it opens in the margin: anchored,
  // beside its passage; unanchored, in the foot band's margin, appended
  // where the saved note itself lands (`.rm-notes`) — composer and note
  // share one `.rm`. Moved before focus, since relocating a focused node
  // blurs it.
  const row = anchor ? rowForAnchor(id, anchor.text, anchor.occurrence) : null;
  const foot = docFootRow(id);
  const host = row ? docNoteHost(id, row) : (foot && docCell(foot, 'rm'));
  if (host && pop.parentElement !== host) host.appendChild(pop);
  pop.style.display = '';
  pop.classList.add('is-open');
  // Opening the box makes the margin non-empty; closing it may let the
  // column collapse again. No `.doc.no-margin` twin needed for the composer
  // — this runs synchronously before paint, so `.is-open` has already
  // dropped `no-margin` by the time the textarea lays out.
  updateDocColumns();

  const ta        = pop.querySelector('.cmt-pop-note');
  const strip     = pop.querySelector('.thumb-strip');
  const attachBtn = pop.querySelector('.attach-btn');
  const fileInput = pop.querySelector('input[type="file"]');
  wireCapture(() => captureState, ta, strip, attachBtn, fileInput, el('rcard-' + id));

  /* One field, whose JOB changes with the type:
       changes / info  → the box is the note
       suggestion      → the box is the replacement wording, applied verbatim
     Typed text carries across the switch — a described change is usually a
     draft of its own replacement wording. A suggestion's rationale is
     optional by schema (`_comment_fragment`). */
  const PLACEHOLDERS = {
    changes:    'Describe the change or question…',
    info:       'Describe the change or question…',
    suggestion: 'Replacement wording — applied verbatim',
  };
  pop.querySelectorAll('.cmt-chip').forEach(ch => ch.onclick = () => {
    pop.dataset.type = ch.dataset.type;
    pop.querySelectorAll('.cmt-chip').forEach(c => {
      c.classList.toggle('is-on', c === ch);
      c.setAttribute('aria-pressed', String(c === ch));
    });
    ta.placeholder = PLACEHOLDERS[pop.dataset.type] || PLACEHOLDERS.changes;
    ta.focus();
  });
  // Opening with a type is the same act as picking its chip — driven through
  // the chip so the dataset, the `is-on` mark and the placeholder can never
  // disagree with each other about what the box means.
  if (type) {
    const chip = pop.querySelector('.cmt-chip[data-type="' + type + '"]');
    if (chip) chip.click();
  }
  // `nearest`, not `center`: a tall popover aligns to its top (where the
  // chips are); a short one doesn't move the page at all. `preventScroll` on
  // focus, or the browser's own scroll-to-field lands the viewport past the
  // chips.
  ta.focus({ preventScroll: true });
  revealWithinBars(pop);
  pop.querySelector('.cmt-save').onclick = () => {
    const text = ta.value.trim();
    // A suggestion ships on its wording: the same box the other types use for
    // a note carries the replacement the author applies verbatim.
    const isSuggestion = pop.dataset.type === 'suggestion';
    if (!text) {
      const why = isSuggestion ? 'a suggestion needs replacement wording'
                               : 'a comment needs a note';
      // Said, not only shown: the placeholder swap alone was silent to a
      // screen reader and vanished on the first keystroke.
      ta.placeholder = why;
      ta.setAttribute('aria-invalid', 'true');
      ta.addEventListener('input', () => ta.removeAttribute('aria-invalid'), { once: true });
      announce(why);
      ta.focus();
      return;
    }
    addComment(id, { type: pop.dataset.type,
                     note: isSuggestion ? '' : text,
                     anchor: anchor || undefined,
                     replacement: isSuggestion ? text : undefined,
                     images: captureState.images?.length ? captureState.images : undefined });
    closeCommentPopover(id);
  };
  pop.querySelector('.cmt-cancel').onclick = () => closeCommentPopover(id);
  // Dictation just fills this box — save is still the only thing that makes
  // a comment. The button turns the mic on and returns focus; a focused note
  // field is what the modal voice rule keys on.
  const mic = pop.querySelector('.mic-btn');
  if (mic) {
    // Built after paintVoiceToggle last ran, so it paints its own live state.
    mic.classList.toggle('is-live', voiceIsOn());
    mic.onclick = () => startVoice(() => ta.focus());
  }
}

/* Scroll a node clear of the fixed bottom bar and sticky masthead, by the
   smallest amount that does it. Chrome ignores scroll-padding for
   scrollIntoView under smooth scrolling, so a plain scrollBy reads and
   applies the paddings itself. */
function revealWithinBars(node) {
  const r = node.getBoundingClientRect();
  const cs = getComputedStyle(document.documentElement);
  const padTop = parseFloat(cs.scrollPaddingTop) || 0;
  const padBottom = parseFloat(cs.scrollPaddingBottom) || 0;
  let dy = 0;
  if (r.bottom > innerHeight - padBottom) dy = r.bottom - (innerHeight - padBottom);
  if (r.top - dy < padTop) dy = r.top - padTop;   // never push the top under the masthead
  if (dy) scrollBy({ top: dy, behavior: SMOOTH });
}

function closeCommentPopover(id) {
  const pop = el('rpop-' + id);
  // Focus goes back to what opened the box. Wiping innerHTML with focus
  // inside it dropped focus to <body>, and the next Tab restarted the page.
  const back = pop && pop._returnTo && document.contains(pop._returnTo)
    ? pop._returnTo : el('rcmtnote-' + id);
  if (pop) { pop.style.display = 'none'; pop.innerHTML = ''; pop.classList.remove('is-open'); pop._returnTo = null; }
  updateDocColumns();
  if (back && back.focus) back.focus({ preventScroll: true });
}

/* `markAndPin` is the single owner of both ends of the anchor/pin pairing —
   two separate marking passes could disagree about which span is note 3. */

// Wraps the `n`th (0-based) text-node occurrence of `needle` in a
// <mark class=cls>, per the anchor's own `occurrence`. A needle split across
// element boundaries matches nothing here (visual only — stored offset is
// unaffected). Returns the mark it created, or null.
function wrapNth(root, needle, cls, n) {
  if (!needle) return null;
  // Prose only: a margin note echoes the wording it annotates, so an
  // unfiltered walk could count — and mark — inside the commentary.
  const walk = proseWalker(root);
  let node, seen = 0;
  while ((node = walk.nextNode())) {
    let i = node.nodeValue.indexOf(needle);
    while (i >= 0) {
      if (seen === n) {
        const after = node.splitText(i);
        after.splitText(needle.length);
        const mark = document.createElement('mark');
        mark.className = cls;
        mark.textContent = after.nodeValue;
        after.replaceWith(mark);
        return mark;
      }

      seen++;
      i = node.nodeValue.indexOf(needle, i + 1);
    }
  }
  // Nothing matched inside a single text node — common for a CODE anchor:
  // highlight.js splits tokens across spans, so the phrase lives in no one
  // node. Same for prose crossing an inline <code>/<em>. Fall through to a
  // Range, which can span elements.
  return wrapSpanning(root, needle, cls, n);
}

// Wraps the nth occurrence of `needle` even across element boundaries. Kept
// as the FALLBACK, not primary: `surroundContents` splits partially-selected
// elements, which is only worth it when the alternative is no mark at all.
function wrapSpanning(root, needle, cls, n) {
  const walk = proseWalker(root);
  const nodes = [], starts = [];
  let text = '', node;
  while ((node = walk.nextNode())) { starts.push(text.length); nodes.push(node); text += node.nodeValue; }
  if (!nodes.length) return null;
  const at = nthIndexOf(text, needle, n > 0 ? n : 0);
  if (at < 0) return null;
  const locate = pos => {
    for (let i = nodes.length - 1; i >= 0; i--)
      if (starts[i] <= pos) return [nodes[i], pos - starts[i]];
    return [nodes[0], 0];
  };
  const [sn, so] = locate(at);
  const [en, eo] = locate(at + needle.length);
  const range = document.createRange();
  try { range.setStart(sn, so); range.setEnd(en, eo); } catch (e) { return null; }
  const mark = document.createElement('mark');
  mark.className = cls;
  try { range.surroundContents(mark); }
  catch (e) {
    /* Partially-selected elements: extract (splits them, each half keeping
       its own class) and re-insert under the mark. A PLACEHOLDER text node
       holds the spot — `extractContents` can collapse the range's start
       boundary up to its parent, so a plain `insertNode(mark)` would land
       the mark as a sibling of where the text came from instead. */
    try {
      const slot = document.createTextNode('');
      range.insertNode(slot);
      range.setStartAfter(slot);
      mark.appendChild(range.extractContents());
      slot.parentNode.replaceChild(mark, slot);
    } catch (e2) { return null; }
  }
  return mark;
}

/* ─── Open notes (issue #16) — settle by cid, recorded as a comment so the
   submit carries it to open_notes.py which closes the thread. ─── */
/* A reply continues the SAME cid thread (flagged `reply`, unsettled), so
   open_notes.update appends it and the agent answers again next round. An
   emptied reply clears the pending one; replies count as active feedback
   (blocking approval) but stay out of the new-comment list. */
function replyToThread(id, cid) {
  const field = el('rreply-' + cid);
  const wrap = field ? field.closest('.thread-reply') : null;
  const type = (wrap && wrap.dataset.type) || 'info';   // info = keep discussing; changes = escalate to an edit
  const note = (field ? field.value : '').trim();
  const cs = commentsOf(id);
  let c = cs.find(x => x.cid === cid);
  if (!note) {
    if (c && c.reply) rState.verdicts[id].comments = cs.filter(x => x !== c);
    syncCard(id);
    return;
  }
  if (!c) { c = { cid }; cs.push(c); }
  Object.assign(c, { type, note, open: true, settled: false, reply: true });
  syncCard(id);
}

function settleOpenNotes(id, cid) {
  const cs = commentsOf(id);
  let c = cs.find(x => x.cid === cid);
  if (!c) { c = { cid, type: 'info', note: '', open: true, settled: true }; cs.push(c); }
  else { c.settled = !c.settled; c.reply = false; }
  const thread = el('rthread-' + cid);
  const btn = el('rsettle-' + cid);
  if (thread) thread.classList.toggle('is-settled', !!c.settled);
  // The button's own label stays put — it names the verb (`Settle`, or
  // `Accept` on a declined thread) and carries a keycap, so rewriting its
  // innerHTML to report state would delete both. State is the lit class plus
  // the thread dimming, which is what the reviewer actually reads.
  if (btn) btn.classList.toggle('is-on', !!c.settled);
  syncCard(id);
}

function syncReviewDot(id) {
  const verdict  = deriveVerdict(id);
  const isActive = rState.active === id;
  const dot = el('rdot-' + id);
  if (!dot) return;
  dot.className = 'dot ' + (
    verdict === 'approved' ? 'dot-approved' :
    verdict === 'changes'  ? 'dot-changes'  :
    verdict === 'info'     ? 'dot-info'     :
    isActive               ? 'dot-active'   : 'dot-idle'
  );
}

function syncNoteInline(id) {
  const verdict = deriveVerdict(id);
  const note    = rState.verdicts[id]?.note || '';
  const inlineEl = el('rnote-inline-' + id);
  if (!inlineEl) return;
  const show = note && verdict && verdict !== 'approved' && rState.active !== id;
  inlineEl.style.display = show ? '' : 'none';
  if (show) { inlineEl.textContent = note; inlineEl.title = note; }
}

function updateReviewStats() {
  const sections = REVIEW_DATA.sections;
  const approved    = sections.filter(s => deriveVerdict(s.id) === 'approved').length;
  const withFeedback= sections.filter(s => ['changes','info'].includes(deriveVerdict(s.id))).length;
  const total    = sections.length;
  const reviewed = approved + withFeedback;
  const remaining= total - reviewed;

  el('r-progress').style.width = (reviewed / total * 100) + '%';
  // The cell is LABELLED `approved` (and DESIGN.md specifies `approved N/M`),
  // so it prints APPROVED. It printed `reviewed`, which counts sections
  // carrying feedback too — which is how the bar could read `approved 8 / 8`
  // on a round where three sections had open changes.
  el('r-progress-label').textContent = `${approved} / ${total}`;
  el('stat-pending').textContent = remaining > 0 ? `${remaining} unreviewed` : 'all reviewed';

  const sub = el('btn-submit');
  // ONE consequential stamp, named for what it does to the document rather
  // than for the HTTP verb behind it. Blocked, it says what is blocking; the
  // count comes from the same item arithmetic the bar prints, so the two can
  // never disagree.
  const openItems = documentBalance().open;
  sub.className = remaining === 0 && reviewed > 0 ? 'btn-submit ready' : 'btn-submit disabled';
  // Announce the deadness as well as draw it. NOT the `disabled` ATTRIBUTE —
  // that one already means IN FLIGHT (submitReview sets it, sendSubmit's
  // failure path and the boot fetch clear it), and openRecap's readiness
  // mirror reads it; overloading it here would re-enable a not-ready button
  // on any submit failure.
  sub.setAttribute('aria-disabled', sub.classList.contains('disabled') ? 'true' : 'false');
  // The stamp names its action and nothing else: `#stat-pending` beside it
  // already carries the blocking count, and restating it here uppercased the
  // same number a second time in the same bar.
  sub.textContent = 'approve — dispatch';
  // The composite's footer states four things; this one was stating seven, and
  // at the doc page's width that wrapped the stamp onto a second line. The bar
  // above already carries `approved N/M` and the item counts, so the footer
  // keeps only what is about DISPATCHING: what blocks it, whether the round is
  // converging, and what the last round trip cost. The two cells that stated
  // `N approved` and `N with feedback` are GONE, not hidden: they were set and
  // then hidden on every path, and the feedback cell's else-branch hid without
  // clearing, so `#stats-area` — an `aria-live` region — kept announcing a
  // stale `3 with feedback` beside a live `8 open` forever.
  const cap = ' <kbd>&#8984;&#9166;</kbd>';
  el('stat-pending').innerHTML = remaining > 0
    ? `blocked &middot; ${remaining} unreviewed`
    : ((openItems ? `${openItems} open` : 'ready') + cap);

  reviewFootSeg(sections, total);
  renderDocStatus();
}

/* ─── The document's condition, in items ──────────────────────
   The bar and the footer state one quantity between them — how many items
   this document holds and how many are still open — so the two can never
   disagree. An ITEM is what sectionBalance already counts: a thread, a
   comment, an unanswered check, and a section's own sign-off. A producer flag
   is advisory and is NOT an item — see sectionBalance. The vocabulary is
   stated to the reader in the `kbd-legend` disclosure, because a reviewer who
   cannot reproduce the arithmetic stops trusting it. */
function documentBalance() {
  let judgment = 0, facts = 0, settled = 0, atStart = 0, checks = 0, checksDone = 0;
  let signoff = 0;
  // Which sections arrived already signed off. The baseline reads the round as
  // ARMED, so it asks `approved_ids` — the static field the round shipped with —
  // never the live verdict, which is the thing convergence measures against it.
  const armedApproved = new Set(REVIEW_DATA.approved_ids || []);
  (REVIEW_DATA.sections || []).forEach(s => {
    const b = sectionBalance(s);
    judgment += b.judgment; facts += b.facts; settled += b.settled; signoff += b.signoff;
    // What was open when the round was ARMED: every carried thread arrives
    // unsettled, every unanswered check and flag arrives open, and every
    // section not in `approved_ids` arrives owing a sign-off. Nothing here
    // reads live reviewer state — that is what makes it a baseline to measure
    // convergence against rather than a second view of the same number.
    atStart += (s.open_notes || []).length;
    // Both ends of the arrow count the sign-off or the arrow lies: `open` now
    // includes every pending one, so a baseline that skipped them would read
    // `convergence 0 → 8` on a round where the reviewer has done nothing yet.
    if (!armedApproved.has(s.id)) atStart++;
    (s.annotations || []).forEach(a => {
      if (!a) return;
      // Only a CHECK is an annotation this baseline counts. A doc-scope flag IS
      // counted in `checks`/`checksDone` — that pair is the bar's document-level
      // `checks D/T` readout, and a fact about the document is exactly what
      // belongs in it. It is NOT counted in `atStart`, and the asymmetry is
      // load-bearing rather than sloppy: `atStart` is the LEFT of the
      // convergence arrow and `open` is the RIGHT, but `open` is a sum of
      // `sectionBalance`, which skips doc-scope. Count it on one side only and
      // a document carrying five unanswered `headings-present` flags and
      // nothing else reads `convergence 5 → 0` on a round where nothing was
      // closed. The two ends of the arrow answer the same question or the
      // arrow lies — which is also why a plain warn/error producer flag is
      // counted at NEITHER end: `sectionBalance` stopped treating an advisory
      // flag as an open item, so a baseline that still counted one would make
      // every flagged round appear to converge by exactly its flag count.
      if (CHECK_KINDS.includes(a.kind)) {
        checks++;
        if (a.result) checksDone++;
        else if (!DOC_SCOPE_KINDS.includes(a.kind)) atStart++;
      }
    });
  });
  return { judgment, facts, settled, checks, checksDone, atStart, signoff,
           open: judgment + facts + signoff,
           total: judgment + facts + settled + signoff };
}

// The last same-origin round trip this page actually measured. A real number
// or nothing — the footer never prints a latency it did not observe.
let _lastRTT = null;

function timedFetch(url, opts) {
  const t0 = performance.now();
  return fetch(url, opts).then(r => {
    _lastRTT = Math.round(performance.now() - t0);
    return r;
  });
}

// The bar's own cells, plus the footer's convergence and latency.
function renderDocStatus() {
  const b = documentBalance();
  el('r-checks').textContent = b.checksDone + '/' + b.checks
    + (b.checks && b.checksDone === b.checks ? ' ✓' : '');
  el('tb-checks').style.display = b.checks ? '' : 'none';
  el('r-items').innerHTML = b.total + ' item' + (b.total === 1 ? '' : 's')
    + ' &middot; <b>' + b.open + '</b> open';
  // Hover convenience only — `title` is not keyboard-reachable and most screen
  // readers do not announce it on a non-interactive div. The `kbd-legend`
  // term list is what actually states this vocabulary.
  el('r-items').title = 'an item is a thread, a comment, an unanswered check, '
    + 'or a section sign-off; open = judgment + facts';
  el('tb-items').style.display = '';
  el('tb-palette').style.display = '';
  // Convergence: open items when the round was armed against open items now.
  // The question a multi-round review actually asks — is the reviewer closing
  // more than they open — with both ends counted, never estimated.
  const conv = el('stat-conv');
  // Printed once the arrow has somewhere to point: on a fresh round both ends
  // are the same number, and a round compared with itself teaches nothing.
  conv.style.display = b.open !== b.atStart ? '' : 'none';
  conv.innerHTML = 'convergence ' + b.atStart + ' &rarr; <b>' + b.open + '</b>';
  conv.title = 'open items when this round was armed → open items now';
  const lat = el('stat-lat');
  // A measured number, and only one worth acting on — a local server's 11 ms
  // is machine trivia in the reviewer's bar.
  if (_lastRTT === null || _lastRTT < SLOW_RTT_MS) lat.style.display = 'none';
  else { lat.style.display = ''; lat.textContent = 'round trip ' + _lastRTT + ' ms'; }
}

/* The whole round's balance, across the footer that closes the page. Same
   grammar and same fixed order as a section's rule, one denominator: every
   section, or every question. What the bar does NOT fill is what nobody has
   looked at yet — the bare track — which is the one honest way to draw "not
   yet decided" without inventing a fourth color.

   Counts in, not sections: an interview has no judgment/facts axis (an answer
   is given or it is not), and asking this function to know that would put a
   mode branch inside the one thing both footers share. */
function renderFootSeg(counts, total, label) {
  const bar = el('foot-seg'); if (!bar) return;
  if (!total) { bar.style.display = 'none'; bar.innerHTML = ''; return; }
  const pct = n => (n / total * 100).toFixed(2) + '%';
  const seg = (cls, n) => n ? '<i class="' + cls + '" style="width:' + pct(n) + '"></i>' : '';
  bar.style.display = '';
  bar.setAttribute('role', 'img');
  bar.setAttribute('aria-label', label);
  bar.innerHTML = seg('seg-judgment', counts.judgment) + seg('seg-fact', counts.facts)
                + seg('seg-settled', counts.settled);
}

// The review page's own tally, in the vocabulary its sections carry.
function reviewFootSeg(sections, total) {
  let judgment = 0, facts = 0, settled = 0;
  sections.forEach(s => {
    const v = deriveVerdict(s.id);
    if (v === 'approved') settled++;
    else if (v === 'changes') judgment++;
    else if (v === 'info') facts++;
  });
  renderFootSeg({ judgment, facts, settled }, total,
    'document balance: ' + judgment + ' judgment, ' + facts + ' fact'
    + (facts === 1 ? '' : 's') + ', ' + settled + ' settled of ' + total + ' sections');
}

/* ═════════════════════════════════════════════════════════════
   COMMAND PALETTE (⌘K, issue #186)
   A directory of the keyboard layer, never a second interaction model:
   every verb listed here is one the page also carries as a control or a
   keycap. Built from live state on each open, so "approve section 9" names
   the section actually under the reader. ═══════════════════════════════ */
function paletteCommands() {
  return REVIEW_DATA ? reviewPaletteCommands() : qaPaletteCommands();
}

/* The interview's own directory. Every verb here is one the Q&A page also
   carries as a control or a keycap — the choices by their digits, confirm by
   `c`, skip by its button — which is the rule the palette exists under: a
   directory of the keyboard layer, never a second interaction model. */
function qaPaletteCommands() {
  const cmds = [];
  const live = qState.active ? QA_DATA.questions.find(q => q.id === qState.active) : null;
  if (live) {
    const n = QA_DATA.questions.indexOf(live) + 1;
    // Every choice, not the first nine. The digit handler binds 1-9 (one
    // keypress, `parseInt(e.key)`), so a tenth choice carries no keycap — but
    // truncating the list here left it with no place in the directory at all,
    // which is the palette lying about being the directory of the keyboard
    // layer. A digit-less row still runs from ⌘K, and the chip itself is a
    // plain <button> that Tab already reaches — so nothing here needs a `0` or
    // a letter bound to it, and binding one would collide the day a second
    // modifier-free letter shortcut lands on this page.
    live.choices.forEach((c, i) => {
      cmds.push({ label: 'Answer ' + n + ' — ' + c, key: i < 9 ? String(i + 1) : '',
                  run: () => pickQAChoice(live.id, c) });
    });
    if (qaAnswered(live.id)) {
      cmds.push({ label: 'Confirm question ' + n, key: 'c', run: () => advanceQA(live.id) });
    }
    cmds.push({ label: 'Skip question ' + n + ' for now', key: '⇥', run: () => advanceQA(live.id) });
  }
  const next = QA_DATA.questions.find(q => !qaAnswered(q.id)
                                        && q.id !== qState.active);
  if (next) cmds.push({ label: 'Jump to next unanswered', key: 'j',
                        run: () => activateQACard(next.id) });
  if (voiceSupported()) cmds.push(voicePaletteCommand());
  cmds.push({ label: 'Cycle theme', key: 't', run: () => cycleTheme() });
  return cmds;
}

function reviewPaletteCommands() {
  const cmds = [];
  const live = rState.active ? REVIEW_DATA.sections.find(s => s.id === rState.active) : null;
  if (live && deriveVerdict(live.id) !== 'approved' && !activeComments(live.id).length) {
    const n = REVIEW_DATA.sections.indexOf(live) + 1;
    cmds.push({ label: 'Approve section ' + n + ' — ' + live.title, key: '⏎',
                run: () => approveSection(live.id) });
  }
  const unblocked = REVIEW_DATA.sections.filter(s =>
    deriveVerdict(s.id) === 'pending' && !activeComments(s.id).length);
  if (unblocked.length) {
    cmds.push({ label: 'Approve all unblocked (' + unblocked.length + ')', key: '⇧⏎',
                run: () => { unblocked.forEach(s => approveSection(s.id)); } });
  }
  const openThread = nextOpenThread();
  if (openThread) cmds.push({ label: 'Jump to next open thread', key: 'j',
                              run: () => activateReviewCard(openThread) });
  // Listed only while there is a ledger to open — a verb that cannot act
  // has no place in the directory.
  if (el('ledger').style.display !== 'none') cmds.push({ label: 'Open revision ledger', key: 'l', run: openLedger });
  cmds.push({ label: 'Open recap and submit', key: 'o', run: () => openRecap() });
  if (voiceSupported()) cmds.push(voicePaletteCommand());
  cmds.push({ label: 'Cycle theme', key: 't', run: () => cycleTheme() });
  return cmds;
}

// One entry, both directories — the palette is a directory of the keyboard
// layer, and `v` means the same thing on the review page and in the interview.
function voicePaletteCommand() {
  return { label: voiceIsOn() ? 'Stop listening' : 'Start the oral examination (voice)',
           key: 'v', run: () => toggleVoice() };
}

// The next section carrying live business, from the live section forward and
// wrapping — `open` and `declined` are both unresolved; only settled closes.
function nextOpenThread() {
  const secs = REVIEW_DATA.sections;
  const start = Math.max(0, secs.findIndex(s => s.id === rState.active)) + 1;
  const order = [...secs.slice(start), ...secs.slice(0, start)];
  const live = s => {
    const cs = (rState.verdicts[s.id] || {}).comments || [];
    return (s.open_notes || []).some(t => !cs.some(c => c.cid === t.cid && c.settled))
        || activeComments(s.id).length > 0;
  };
  const hit = order.find(live);
  return hit ? hit.id : null;
}

let _palCmds = [];
let _palIdx = 0;
let _palReturnTo = null;

function paletteIsOpen() { return el('pal-overlay').style.display !== 'none'; }

function openPalette() {
  if ((!REVIEW_DATA && !QA_DATA) || paletteIsOpen()) return;
  _palReturnTo = document.activeElement;
  el('pal-overlay').style.display = '';
  el('pal-input').value = '';
  renderPalette('');
  // Modal in fact as well as in aria: the page behind the scrim is inert
  // while it is open, and focus goes back where it came from on close.
  setBackgroundInert(true);
  el('pal-input').focus();
}

function closePalette() {
  el('pal-overlay').style.display = 'none';
  el('pal-list').innerHTML = '';
  _palCmds = [];
  setBackgroundInert(false);
  const back = _palReturnTo; _palReturnTo = null;
  if (back && back !== document.body && document.contains(back)) back.focus({ preventScroll: true });
}

function renderPalette(query) {
  const q = String(query || '').trim().toLowerCase();
  _palCmds = paletteCommands().filter(c => !q || c.label.toLowerCase().includes(q));
  _palIdx = 0;
  const list = el('pal-list');
  if (!_palCmds.length) { list.innerHTML = '<div class="pal-empty">no matching command</div>'; return; }
  // Options are addressed through the combobox (`aria-activedescendant`) and
  // take no tab stop of their own — Tab-then-Enter used to run row 0.
  list.innerHTML = _palCmds.map((c, i) =>
    '<button type="button" class="pal-row' + (i === 0 ? ' is-on' : '') + '" role="option" tabindex="-1"'
    + ' id="pal-row-' + i + '" aria-selected="' + (i === 0) + '" data-i="' + i + '">'
    + '<span>' + esc(c.label) + '</span><span class="k">' + esc(c.key) + '</span></button>').join('');
  el('pal-input').setAttribute('aria-activedescendant', 'pal-row-0');
  list.querySelectorAll('.pal-row').forEach(b =>
    b.addEventListener('click', () => runPalette(+b.dataset.i)));
}

function movePalette(delta) {
  const rows = el('pal-list').querySelectorAll('.pal-row');
  if (!rows.length) return;
  _palIdx = (_palIdx + delta + rows.length) % rows.length;
  rows.forEach((r, i) => {
    r.classList.toggle('is-on', i === _palIdx);
    r.setAttribute('aria-selected', String(i === _palIdx));
  });
  el('pal-input').setAttribute('aria-activedescendant', rows[_palIdx].id);
  rows[_palIdx].scrollIntoView({ block: 'nearest' });
}

function runPalette(i) {
  const cmd = _palCmds[i];
  closePalette();
  if (cmd) cmd.run();
}

/* ─────────────────────────────────────────────────────────
   Q&A MODE — build once, update surgically
───────────────────────────────────────────────────────── */
/* ─── Q&A on the catalog ──────────────────────────────────────
   A question is not a document section, but it holds one the same way: a
   thing to read and decide, with the machine's advice and the reviewer's own
   move beside it rather than stacked on top of it. So Q&A takes the GRAMMAR —
   `gutter | prose | margin` rows, the note grammar, per-note verbs, the
   composite's bar and footer — and not the PRINT: one question at a time is
   the point of an interview, and the accordion is what makes that true.

   The prose column is the question, numbered like a catalog entry, with its
   choices under it as chips carrying the digit that picks them. The margin is
   the machine's hint and the reviewer's own note with its attachments. The
   gutter is empty — a question carries no producer flags — so it collapses
   for good; the margin never does, because the verbs live there.

   One thing deliberately stays in the prose column: the recommended-choice
   badge. It is advice ABOUT A CONTROL, and a reviewer should not have to read
   the margin, look back, and hunt for the chip it meant. */

// Taste-first ordering (issue #175) — same discipline as the review print's
// weakest-first confidence sort (`hasConfidence` above): the reorder is a
// toggle keyed on whether the data exists at all, not a default. A batch
// where no question carries `grounds` comes back untouched, so an
// interview authored before this field existed renders in the exact
// document order it always has. Where at least one question carries
// `grounds`, taste-classed questions move first — a taste question is the
// reviewer's own call regardless of index, so it should not wait behind a
// page of machine opinions. A stable sort keeps every other relative
// ordering (including ties) exactly as authored.
function orderQAQuestions(questions) {
  if (!questions.some(q => q.grounds)) return questions;
  return questions
    .map((q, i) => ({ q, i }))
    .sort((a, b) => {
      const ta = a.q.grounds === 'taste' ? 0 : 1;
      const tb = b.q.grounds === 'taste' ? 0 : 1;
      return (ta - tb) || (a.i - b.i);
    })
    .map(x => x.q);
}

function initQA() {
  const container = el('qa-cards');
  // `no-gutter` is a constant, not a computed collapse: a question has no
  // producer flags to rail, so neither column's state changes mid-session.
  container.className = 'cards doc no-gutter';
  QA_DATA.questions.forEach((q, i) => {
    const card = buildQACard(q, i);
    card.style.animationDelay = (0.04 + i * 0.04) + 's';
    container.appendChild(card);
  });
  if (QA_DATA.questions.length > 0) {
    activateQACard(QA_DATA.questions[0].id);
  }
  updateQAStats();
}

// `grounds` classing (#175): absent, renders the plain badge (#114). `sourced`
// keeps it ambient — the citation rides in the question's own text/hint.
// `inferred` renders nothing here; see buildQACard's `groundsReveal`. `taste`
// never reaches this function — no matching chip (validate_qa_input).
function recommendedBadge(grounds) {
  if (grounds === 'sourced') {
    return '<span class="chip-badge chip-badge-sourced" title="Recommended — sourced; see the question for its citation">sourced</span>';
  }
  if (grounds === 'inferred') return '';
  return '<span class="chip-badge" title="Recommended — pick whichever you want">recommended</span>';
}

function buildQACard(q, index) {
  const card = document.createElement('div');
  card.className = 'card';
  card.id = 'qacard-' + q.id;

  // recommended_choice is optional (#114) — advisory only: the matching chip
  // gets a badge, nothing else (no pre-selection, no restyle as primary).
  // The digit keycap binds to the same key the keydown handler uses, 1-9.
  const choicesHtml = q.choices.map((c, i) => {
    const isRecommended = q.recommended_choice !== undefined && c === q.recommended_choice;
    const badge = isRecommended
      ? recommendedBadge(q.grounds)
      : '';
    const cap = i < 9 ? `<kbd>${i + 1}</kbd>` : '';
    return `<button class="choice-chip" data-choice="${esc(c)}"><span class="chip-label">${esc(c)}</span>${badge}${cap}</button>`;
  }).join('');
  // An inferred recommendation answers only behind a reveal (issue #175) —
  // never ambiently on the chip itself. Native <details>/<summary>, the same
  // disclosure element `.kbd-legend` already uses.
  const groundsReveal = (q.grounds === 'inferred' && q.recommended_choice !== undefined)
    ? `<details class="chip-reveal"><summary>inferred pick &mdash; show</summary>` +
      `<div class="chip-reveal-body">recommended: <strong>${esc(q.recommended_choice)}</strong></div></details>`
    : '';
  // Taste-classed questions never carry a recommended_choice at all
  // (validate_qa_input rejects the two together), so there is no chip to
  // badge — the label decorates the question's choices instead.
  const tasteLabel = q.grounds === 'taste'
    ? '<span class="chip-badge chip-badge-taste" title="No recommendation offered — this one is yours">this one is yours</span>'
    : '';

  // The disclosure head IS the question, printed once, numbered like a catalog
  // entry. The number goes INSIDE `.card-title`: `.card-title-wrap` is
  // `flex-direction: column`, so a sibling span would stack it above the text.
  const choiceless = q.choices.length === 0;
  card.innerHTML = `
    <button type="button" class="card-head" aria-expanded="false" aria-controls="qbody-${q.id}">
      <span class="dot dot-idle" id="qdot-${q.id}"></span>
      <span class="card-title-wrap">
        <span class="card-title"><span class="doc-num" aria-hidden="true">${index + 1} &middot;</span> ${esc(q.text)}</span>
      </span>
      <span class="vbadge vbadge-approved" id="qbadge-${q.id}" style="display:none"></span>
    </button>
    <div class="card-body-wrap" id="qbody-${q.id}">
      <div class="card-body-inner">
        <div class="card-body">
          <div class="row row-head${choiceless ? ' is-choiceless' : ''}">
            ${choiceless ? '' : `<div class="rp">
              <div class="rule-s"></div>
              ${tasteLabel}
              <div class="choices" id="qchoices-${q.id}">${choicesHtml}</div>
              ${groundsReveal}
            </div>`}
            <div class="rm">
              ${q.hint ? `<div class="nt nt-check"><div class="nh">hint</div><div class="nt-body">${esc(q.hint)}</div></div>` : ''}
              <div class="nt nt-compose">
                <div class="nh">you &mdash; context</div>
                <textarea class="note-field" id="qnote-${q.id}" aria-label="Context for this answer" placeholder="Optional — or paste a screenshot"></textarea>
                <div class="thumb-strip" id="qthumbs-${q.id}" aria-live="polite" style="display:none"></div>
                <button type="button" class="attach-btn" id="qattach-${q.id}">attach image</button>
                ${voiceSupported() ? `<button type="button" class="mic-btn" id="qmic-${q.id}">dictate</button>` : ''}
                <input type="file" accept="image/*" multiple style="display:none" id="qfile-${q.id}">
              </div>
              <div class="nt-acts doc-acts">
                <button type="button" class="nt-btn is-quiet" id="qconfirm-${q.id}"><span aria-hidden="true">&#10003;</span> confirm<kbd>c</kbd></button>
                <button type="button" class="nt-btn is-quiet" id="qskip-${q.id}"><span aria-hidden="true">&#8595;</span> skip</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>`;

  card.querySelector('.card-head').addEventListener('click', () => toggleQACard(q.id));

  // Guarded: a choiceless question has no chip list. Both readers of
  // `#qchoices-` (this one and `syncQACard`'s) carry the guard.
  const ch = card.querySelector('#qchoices-' + q.id);
  if (ch) ch.addEventListener('click', e => {
    const chip = e.target.closest('.choice-chip');
    if (!chip) return;
    e.stopPropagation();
    pickQAChoice(q.id, chip.dataset.choice);
  });

  const qta = card.querySelector('#qnote-' + q.id);
  qta.addEventListener('input', e => {
    if (!qState.answers[q.id]) qState.answers[q.id] = {};
    qState.answers[q.id].note = e.target.value;
    // A typed note IS an answer (#121), so it has to move the same indicators a
    // chip does. Without this the state held the text while the page kept
    // reporting the question unanswered — the counter, the dot and the confirm
    // button only ever refreshed on a chip click.
    syncQACard(q.id);
    updateQAStats();
  });
  qta.addEventListener('click', e => e.stopPropagation());

  card.querySelector('#qconfirm-' + q.id).addEventListener('click', e => { e.stopPropagation(); advanceQA(q.id); });
  card.querySelector('#qskip-'   + q.id).addEventListener('click', e => { e.stopPropagation(); advanceQA(q.id); });

  // Same contract as the review composer's mic: turn the microphone on, put
  // the caret in this box, and let the modal rule do the rest.
  const qmic = card.querySelector('#qmic-' + q.id);
  if (qmic) {
    qmic.classList.toggle('is-live', voiceIsOn());
    qmic.addEventListener('click', e => { e.stopPropagation(); startVoice(() => qta.focus()); });
  }

  wireCapture(
    () => (qState.answers[q.id] ||= {}),
    card.querySelector('#qnote-' + q.id),
    card.querySelector('#qthumbs-' + q.id),
    card.querySelector('#qattach-' + q.id),
    card.querySelector('#qfile-' + q.id),
    card
  );

  return card;
}

/* The ONE definition of "this question has an answer" (#121). A free-text-only
   question never sets `choice`, so gating on `choice` alone dropped typed
   notes from the progress stat, the dot, auto-advance, and the submit filter.
   Every reader routes through here so they can't disagree about it. */
function qaAnswered(id) {
  const a = qState.answers[id];
  if (!a) return false;
  return Boolean(a.choice || (a.note && a.note.trim()));
}

// One place a choice is picked, so the chip, the digit key and the palette
// can never disagree about what a second press means — it clears the answer.
function pickQAChoice(id, choice) {
  const a = (qState.answers[id] ||= {});
  a.choice = a.choice === choice ? null : choice;
  syncQACard(id);
  updateQAStats();
}

function activateQACard(id) {
  if (qState.active && qState.active !== id) {
    setCardExpanded(el('qacard-' + qState.active), false);
    syncQADot(qState.active);
  }
  qState.active = id;
  const card = el('qacard-' + id);
  if (card) {
    setCardExpanded(card, true);
    card.scrollIntoView({ behavior: SMOOTH, block: 'nearest' });
  }
  syncQADot(id);
}

function toggleQACard(id) {
  if (qState.active === id) {
    setCardExpanded(el('qacard-' + id), false);
    qState.active = null;
    syncQADot(id);
  } else {
    activateQACard(id);
  }
}

function advanceQA(id) {
  setCardExpanded(el('qacard-' + id), false);
  if (qaAnswered(id)) el('qacard-' + id)?.classList.add('is-approved');
  qState.active = null;
  syncQADot(id);

  const qs  = QA_DATA.questions;
  const idx = qs.findIndex(q => q.id === id);
  const next= qs.slice(idx + 1).find(q => !qaAnswered(q.id));
  if (next) setTimeout(() => activateQACard(next.id), 80);

  updateQAStats();
}

function syncQACard(id) {
  const choice = qState.answers[id]?.choice || null;

  // Chip selections. Guarded for the same reason buildQACard's wiring is: a
  // choiceless question has no `#qchoices-` element at all.
  const chEl = el('qchoices-' + id);
  if (chEl) chEl.querySelectorAll('.choice-chip').forEach(chip => {
    chip.classList.toggle('selected', chip.dataset.choice === choice);
    chip.setAttribute('aria-pressed', String(chip.dataset.choice === choice));
  });

  // Badge
  const badge = el('qbadge-' + id);
  if (choice) { badge.style.display=''; badge.textContent=choice; }
  else badge.style.display = 'none';

  // The verb's own grammar: primary once there is an answer to confirm, quiet
  // while there is not — the same rule review's approve follows, and the same
  // two classes, so one button reads the same way on both surfaces.
  const btn = el('qconfirm-' + id);
  btn.className = 'nt-btn ' + (qaAnswered(id) ? 'is-pri' : 'is-quiet');

  syncQADot(id);
}

function syncQADot(id) {
  const answered = qaAnswered(id);
  const isActive = qState.active === id;
  const dot = el('qdot-' + id);
  if (!dot) return;
  dot.className = 'dot ' + (answered ? 'dot-approved' : isActive ? 'dot-active' : 'dot-idle');
}

function updateQAStats() {
  const qs       = QA_DATA.questions;
  const answered = qs.filter(q => qaAnswered(q.id)).length;
  const total    = qs.length;
  const remaining= total - answered;

  el('qa-progress-label').textContent = `${answered} / ${total}`;
  // Same footer shape as the review page. An interview has no judgment/facts
  // axis — an answer is given or it is not — so the balance rule fills with
  // settled alone.
  el('stat-pending').innerHTML = remaining > 0
    ? `blocked &middot; ${remaining} unanswered`
    : 'ready <kbd>&#8984;&#9166;</kbd>';
  renderFootSeg({ judgment: 0, facts: 0, settled: answered }, total,
                `answers: ${answered} of ${total} questions`);

  const sub = el('btn-submit');
  sub.className = remaining === 0 ? 'btn-submit ready' : 'btn-submit disabled';
  // Same rule as updateReviewStats: aria-disabled for not-ready, the DOM
  // `disabled` attribute reserved for in-flight.
  sub.setAttribute('aria-disabled', sub.classList.contains('disabled') ? 'true' : 'false');
  sub.textContent = remaining > 0 ? `answers — dispatch (${remaining} unanswered)`
                                  : 'answers — dispatch';
}

/* ─── Image attachments ────────────────────────────────────── */
function renderThumbs(stateObj, stripEl) {
  const imgs = stateObj.images || [];
  stripEl.innerHTML = imgs.map((im, i) =>
    `<div class="thumb"><img src="data:${esc(im.mime)};base64,${im.data}" width="64" height="64" alt="Attached image ${i + 1}">` +
    `<button class="thumb-remove" data-i="${i}" title="Remove image" aria-label="Remove image">&times;</button></div>`
  ).join('');
  stripEl.style.display = imgs.length ? 'flex' : 'none';
  stripEl.querySelectorAll('.thumb-remove').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      stateObj.images.splice(Number(btn.dataset.i), 1);
      renderThumbs(stateObj, stripEl);
    });
  });
}

function attachImageFiles(stateObj, files, stripEl) {
  const list = Array.from(files || []).filter(f => f.type.startsWith('image/'));
  if (!list.length) return;
  if (!stateObj.images) stateObj.images = [];
  let pending = list.length;
  list.forEach(file => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      const comma = result.indexOf(',');
      stateObj.images.push({ data: result.slice(comma + 1), mime: file.type });
      if (--pending === 0) renderThumbs(stateObj, stripEl);
    };
    reader.onerror = () => { if (--pending === 0) renderThumbs(stateObj, stripEl); };
    reader.readAsDataURL(file);
  });
}

function wireCapture(stateGetter, textarea, stripEl, attachBtn, fileInput, card) {
  // Only accept drops when the note area holding the strip is visible — review
  // cards hide it for approved/pending verdicts, where captured images could
  // neither be seen, removed, nor read by the verdict's consumer.
  const droppable = () => stripEl.isConnected && stripEl.parentElement != null
                       && stripEl.parentElement.style.display !== 'none';
  textarea.addEventListener('paste', e => {
    const files = Array.from(e.clipboardData?.items || [])
      .filter(it => it.kind === 'file' && it.type.startsWith('image/'))
      .map(it => it.getAsFile()).filter(Boolean);
    if (files.length) { e.preventDefault(); attachImageFiles(stateGetter(), files, stripEl); }
  });
  card.addEventListener('dragover', e => {
    if (!droppable()) return;
    e.preventDefault();
    card.classList.add('is-drop-target');
  });
  card.addEventListener('dragleave', e => {
    if (e.target === card) card.classList.remove('is-drop-target');
  });
  card.addEventListener('drop', e => {
    card.classList.remove('is-drop-target');
    if (!droppable() || !e.dataTransfer?.files?.length) return;
    e.preventDefault();
    attachImageFiles(stateGetter(), e.dataTransfer.files, stripEl);
  });
  attachBtn.addEventListener('click', e => { e.stopPropagation(); fileInput.click(); });
  fileInput.addEventListener('change', () => {
    attachImageFiles(stateGetter(), fileInput.files, stripEl);
    fileInput.value = '';
  });
}

/* ─── Submit handlers ──────────────────────────────────────── */
// Between-rounds snapshot — the changes/info rows just sent, captured from
// rState so the 'processing' view can echo them back verbatim. In-memory
// only (never written to .viva/): a tab reload re-boots into the prior round.
let betweenRounds = null;

function snapshotBetweenRounds() {
  betweenRounds = {
    round: REVIEW_DATA.round,
    // A suggestion's note is optional, so the processing card falls back to the
    // wording — a row reading only its section title says nothing.
    rows: REVIEW_DATA.sections.flatMap(s =>
      activeComments(s.id).map(c => ({ sectionTitle: s.title, type: c.type,
                                       note: c.note || c.replacement || '' })))
  };
}

// POST a round/answer payload to /submit, surface failure, and re-enable the
// bar so the reviewer can retry. fetch() resolves (never rejects) on a
// 4xx/5xx, so a non-2xx is turned into a throw here. On success the buttons
// stay disabled — the SSE 'processing'/'round' events drive the next view.
function sendSubmit(result) {
  // The one choke point every submit passes through, review and qa alike.
  // The server behind this tab is gone (#174); a POST here fails into the
  // same silent nothing the dead-session overlay exists to end.
  if (deadSessionIsOpen()) return;
  fetch('/submit', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(result)
  })
    .then(r => { if (!r.ok) throw new Error('server returned ' + r.status); })
    .catch(err => {
      alert('Submit failed: ' + (err.message || 'network error'));
      el('btn-skip').disabled   = false;
      el('btn-submit').disabled = false;
    });
}

function submitReview(early) {
  el('btn-skip').disabled   = true;
  el('btn-submit').disabled = true;
  snapshotBetweenRounds();  // before the POST — 'processing' renders from it
  const result = {
    round: REVIEW_DATA.round,
    submitted_early: early,
    sections: REVIEW_DATA.sections.map(s => {
      const v = rState.verdicts[s.id] || {};
      const comments = v.comments || [];
      const verdict = deriveVerdict(s.id);
      return { id: s.id, verdict,
               ...(comments.length && { comments }) };
    })
  };
  sendSubmit(result);
}

function submitQA(early) {
  el('btn-skip').disabled   = true;
  el('btn-submit').disabled = true;
  // Images on a question with no selected choice would be silently dropped by
  // the choice filter below — warn before discarding them.
  if (!early) {
    const orphaned = QA_DATA.questions.filter(
      q => qState.answers[q.id]?.images?.length && !qaAnswered(q.id)
    );
    if (orphaned.length &&
        !confirm(orphaned.length + ' question(s) have an attached image but no selected choice — their images will be dropped. Continue?')) {
      el('btn-skip').disabled   = false;
      el('btn-submit').disabled = false;
      return;
    }
  }
  const result = {
    answers: QA_DATA.questions
      .filter(q => qaAnswered(q.id))
      .map(q => {
        const a = qState.answers[q.id];
        return { id: q.id, choice: a.choice || '', note: a.note || '',
                 ...(a.images && a.images.length && { images: a.images }) };
      }),
    submitted_early: early
  };
  sendSubmit(result);
}

el('btn-skip').addEventListener('click', () => {
  if (REVIEW_DATA) submitReview(true);
  else             submitQA(true);
});

el('btn-submit').addEventListener('click', () => {
  if (el('btn-submit').classList.contains('disabled')) return;
  // Review/diff route through the recap gate — only #recap-confirm calls
  // submitReview(false). Q&A keeps its direct done → path.
  if (REVIEW_DATA) openRecap();
  else             submitQA(false);
});

/* ─── Recap overlay — the submit gate (review/diff modes) ────
   btn-submit's ready click opens this index of every section instead of
   submitting; only #recap-confirm calls submitReview(false). `o` toggles it,
   Escape closes it. Q&A ships no recap — done → wires straight to submitQA. */
const RECAP_VERDICTS = {
  approved: { dot: 'dot-approved', cls: 'rv-approved', label: 'approved' },
  changes: { dot: 'dot-changes', cls: 'rv-changes', label: 'changes' },
  info: { dot: 'dot-info', cls: 'rv-info', label: 'info' },
  pending: { dot: 'dot-idle', cls: 'rv-pending', label: 'pending' },
};

function recapRowsHTML() {
  // Numbered as the print numbers them — `1 ·`, not the machine's `s1`.
  return REVIEW_DATA.sections.map((s, i) => {
    const v = RECAP_VERDICTS[deriveVerdict(s.id)] || RECAP_VERDICTS.pending;
    const notes = activeComments(s.id).length;
    return '<button type="button" class="recap-row" data-target="' + esc(s.id) + '">'
      + '<span class="recap-id">' + (i + 1) + '</span>'
      + '<span class="recap-row-title">' + esc(s.title) + '</span>'
      + '<span class="recap-verdict ' + v.cls + '"><span class="dot ' + v.dot + '" aria-hidden="true"></span>' + v.label + '</span>'
      + '<span class="recap-notes">' + (notes ? notes + ' note' + (notes === 1 ? '' : 's') : '&mdash;') + '</span>'
      + '</button>';
  }).join('');
}

function recapIsOpen() { return el('recap-overlay').style.display !== 'none'; }

function openRecap() {
  // Q&A ships no recap, and a hidden review-view (processing/complete) has
  // nothing to recap — the `o` shortcut lands here too, not just the
  // class-gated btn-submit click.
  if (!REVIEW_DATA || el('review-view').style.display === 'none') return;
  if (prefsIsOpen()) closePrefsPanel();   // only one modal open at a time
  el('recap-round').textContent = String(REVIEW_DATA.round).padStart(2, '0');
  const grid = el('recap-grid');
  grid.innerHTML = recapRowsHTML();
  grid.querySelectorAll('.recap-row').forEach(btn => {
    btn.addEventListener('click', () => { closeRecap(); activateReviewCard(btn.dataset.target); });
  });
  // Mirrors btn-submit's readiness so a recap opened via `o` can't submit a
  // round the bottom bar wouldn't, and can't re-arm a duplicate POST while
  // one is already in flight (`.disabled`).
  const ready = el('btn-submit').classList.contains('ready') && !el('btn-submit').disabled;
  el('recap-confirm').className = 'btn-submit ' + (ready ? 'ready' : 'disabled');
  el('recap-confirm').setAttribute('aria-disabled', ready ? 'false' : 'true');
  // Three states, reason printed rather than inferred: `disabled` is the
  // IN-FLIGHT signal, `pending` is the not-ready count.
  const inFlight = el('btn-submit').disabled;
  const pending = REVIEW_DATA.sections.filter(s => deriveVerdict(s.id) === 'pending').length;
  // btn-skip does the same job in the bar, but the bar is inert behind this
  // modal, so naming it in copy would not be enough.
  const canSkip = pending > 0 && !inFlight;
  el('recap-skip').style.display = canSkip ? '' : 'none';
  el('recap-blocked').textContent = inFlight ? 'submitted — the agent is revising'
                                  : pending ? pending + ' of ' + REVIEW_DATA.sections.length + ' unreviewed'
                                  : '';
  el('recap-overlay').style.display = '';
  setBackgroundInert(true);   // trap focus + block interaction behind the modal
  // Focus the confirm when it can act, otherwise the close — NEVER the skip;
  // that let `o` then Enter dispatch a round with every section unreviewed.
  // Runs AFTER both display flips above — focus() on `display:none` no-ops.
  (ready ? el('recap-confirm') : el('recap-close')).focus();
}

// The recap is a modal (aria-modal="true"): mark everything behind it inert
// while open, so Tab and background clicks can't reach it. A focus trap
// without hand-rolled Tab-wrap bookkeeping.
function setBackgroundInert(on) {
  ['skip-link-a', 'paper', 'bottom-bar-el'].forEach(id => {
    const node = el(id);
    if (node) node.inert = on;
  });
}

function closeRecap() {
  const overlay = el('recap-overlay');
  if (overlay.style.display === 'none') return;
  const hadFocus = overlay.contains(document.activeElement);
  overlay.style.display = 'none';
  setBackgroundInert(false);   // clear inert BEFORE restoring focus — focus()
                               // on an element inside an inert subtree no-ops
  // Don't strand keyboard focus on the now-hidden overlay.
  if (hadFocus) el('btn-submit').focus();
}

function toggleRecap() { if (recapIsOpen()) closeRecap(); else openRecap(); }

el('recap-confirm').addEventListener('click', () => {
  // Belt-and-suspenders with openRecap's readiness mirror: never submit while
  // one is already in flight (btn-submit.disabled), so a fast reopen can't
  // fire a duplicate POST between submit and the 'processing'/'round' events.
  if (el('recap-confirm').classList.contains('disabled') || el('btn-submit').disabled) return;
  closeRecap();
  submitReview(false);
});
el('recap-close').addEventListener('click', closeRecap);
// The bar's early-submit escape hatch, reachable from inside the modal:
// setBackgroundInert marks #bottom-bar-el inert, so `btn-skip` itself cannot
// be clicked or tabbed to while the recap is open.
el('recap-skip').addEventListener('click', () => {
  if (el('btn-submit').disabled) return;   // never a second POST for a round in flight
  closeRecap();
  submitReview(true);
});
el('recap-overlay').addEventListener('click', e => {
  if (e.target === el('recap-overlay')) closeRecap();   /* backdrop click */
});

/* ─── Preferences panel — view/mute learned preferences (#142) ───
   #prefs-overlay mirrors the recap overlay's modal shape (role="dialog"
   aria-modal, Escape/backdrop/close, setBackgroundInert) but is independent:
   at most one of the two is ever open. Reachable in every mode, unlike recap. */
let _prefsTriggerEl = null;

function prefsIsOpen() { return el('prefs-overlay').style.display !== 'none'; }

function prefStatusLabel(status) {
  return status === 'standing' ? 'standing' : status === 'muted' ? 'muted' : 'candidate';
}

// Static recovery copy for a muted row — mute is one-way from this panel
// (decision prefs-inspector-1), so the CLI command that reverses it must be
// visible on the row itself. Badges already shown this round stay as a
// record; the copy makes no claim about whether this round's rewrite saw it.
function prefMutedNoteHTML(id) {
  return '<div class="pref-muted-note">muted &mdash; badges already shown this round '
    + 'stay as a record; nothing further is flagged or applied for this preference. '
    + 'restore from a terminal: <code>python3 "__PREFS_SCRIPT_PATH__" set '
    + '--store "__PREFS_STORE_PATH__" --id ' + esc(id) + ' --status standing</code></div>';
}

function prefRowHTML(p) {
  const status   = prefStatusLabel(p.status);
  const sessions = p.sessions || [];
  const obs      = p.observations || 0;
  const meta = sessions.length
    ? obs + ' observation' + (obs === 1 ? '' : 's') + ' &middot; seen in ' + sessions.length
      + ' session' + (sessions.length === 1 ? '' : 's') + ': ' + esc(sessions.join(', '))
    : 'no sessions recorded yet';
  const muteBtn = status === 'standing'
    ? '<button type="button" class="pref-mute-btn" data-id="' + esc(p.id) + '">mute</button>'
    : '';
  const mutedNote = status === 'muted' ? prefMutedNoteHTML(p.id) : '';
  return '<div class="pref-row" id="pref-row-' + esc(p.id) + '" data-id="' + esc(p.id)
    + '" data-status="' + esc(status) + '" tabindex="-1">'
    + '<div class="pref-row-head">'
    +   '<span class="pref-status pref-status-' + esc(status) + '">' + esc(status) + '</span>'
    +   '<span class="pref-label">' + esc(p.label || p.id) + '</span>'
    +   muteBtn
    + '</div>'
    + (p.guidance ? '<div class="pref-guidance">' + esc(p.guidance) + '</div>' : '')
    + '<div class="pref-meta">' + meta + '</div>'
    + mutedNote
    + '</div>';
}

function renderPrefsList() {
  el('prefs-list').innerHTML = PREFS_DATA.length
    ? PREFS_DATA.map(prefRowHTML).join('')
    : '<p class="prefs-empty">No preferences learned yet.</p>';
  el('prefs-list').querySelectorAll('.pref-mute-btn').forEach(btn => {
    btn.addEventListener('click', () => mutePreference(btn.dataset.id));
  });
}

// Mutates the one row's DOM in place, never a list rebuild, so a mute never
// disturbs scroll position or any other row.
function markPrefRowMuted(id) {
  const row = document.getElementById('pref-row-' + id);
  if (!row) return;
  const statusEl = row.querySelector('.pref-status');
  if (statusEl) { statusEl.textContent = 'muted'; statusEl.className = 'pref-status pref-status-muted'; }
  const btn = row.querySelector('.pref-mute-btn');
  if (btn) btn.remove();
  if (!row.querySelector('.pref-muted-note')) row.insertAdjacentHTML('beforeend', prefMutedNoteHTML(id));
  row.dataset.status = 'muted';
}

function mutePreference(id) {
  const row = document.getElementById('pref-row-' + id);
  const btn = row && row.querySelector('.pref-mute-btn');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const prevLabel = btn.textContent;
  btn.textContent = 'muting…';
  fetch('/preferences/mute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: id }),
  })
    .then(r => r.json().then(respBody => {
      if (!r.ok || !respBody.ok) throw new Error((respBody && respBody.error) || 'mute failed');
    }))
    .then(() => {
      const pref = PREFS_BY_ID.get(id);
      if (pref) pref.status = 'muted';
      markPrefRowMuted(id);
      el('prefs-status').textContent = ((pref && pref.label) || id)
        + ' muted — badges already shown this round stay as a record; nothing further is flagged or applied for it.';
    })
    .catch(err => {
      btn.disabled = false;
      btn.textContent = prevLabel;
      el('prefs-status').textContent = 'Could not mute — ' + (err.message || 'request failed') + '.';
    });
}

function openPrefsPanel(triggerEl, focusPrefId) {
  if (recapIsOpen()) closeRecap();   // only one modal open at a time
  el('prefs-status').textContent = '';   // clear a stale mute announcement from a prior open
  renderPrefsList();
  _prefsTriggerEl = triggerEl || el('prefs-toggle');
  el('prefs-overlay').style.display = '';
  setBackgroundInert(true);
  const row = focusPrefId && document.getElementById('pref-row-' + focusPrefId);
  if (row) { row.scrollIntoView({ block: 'center' }); row.focus(); }
  else      { el('prefs-close').focus(); }
}

function closePrefsPanel() {
  const overlay = el('prefs-overlay');
  if (overlay.style.display === 'none') return;
  const hadFocus = overlay.contains(document.activeElement);
  overlay.style.display = 'none';
  setBackgroundInert(false);   // clear inert BEFORE restoring focus, same order as closeRecap
  if (hadFocus) (_prefsTriggerEl || el('prefs-toggle')).focus();
}

/* ─── Theme toggle ──────────────────────────────────────────
   Three states, cycled: system → light → dark → system. "system" is the
   absence of the attribute, not a third value — falls back to
   `prefers-color-scheme`. The pre-paint script in <head> applies a stored
   choice; this only changes it, writing the same key. */
const THEME_CYCLE = [null, 'light', 'dark'];

function currentTheme() {
  const t = document.documentElement.dataset.theme;
  return (t === 'light' || t === 'dark') ? t : null;
}

function paintThemeToggle() {
  const t = currentTheme();
  const btn = el('theme-toggle');
  btn.textContent = 'theme: ' + (t || 'system');
  /* The label states which theme is ON, so the accessible name has to say
     what the button DOES — otherwise a screen reader hears "theme: dark" and
     cannot tell whether that is the state or the action. */
  const next = THEME_CYCLE[(THEME_CYCLE.indexOf(t) + 1) % THEME_CYCLE.length];
  btn.setAttribute('aria-label',
    'Theme: ' + (t || 'following system') + '. Activate to switch to ' + (next || 'follow system') + '.');
}

function cycleTheme() {
  const next = THEME_CYCLE[(THEME_CYCLE.indexOf(currentTheme()) + 1) % THEME_CYCLE.length];
  if (next) document.documentElement.dataset.theme = next;
  else delete document.documentElement.dataset.theme;
  try {
    if (next) localStorage.setItem('viva-theme', next);
    else localStorage.removeItem('viva-theme');
  } catch (e) { /* no storage: the choice holds for this tab only */ }
  paintThemeToggle();
}

el('theme-toggle').addEventListener('click', cycleTheme);
paintThemeToggle();

/* ═══ Voice — the oral examination (input only) ══════════════════════════
   Speech may command but never author: a bare verb acts immediately, while a
   note/question/suggestion is STAGED in the composer and never reaches
   `addComment` until confirmed (tests/test_server_voice.py). Off by default. ══ */

// Injected from server.py's `_VOICE_RULES`, longest phrase first — see the
// table there for the two verb classes. Injected rather than restated for the
// reason `__CHECK_KINDS__` is: a second copy in JS drifts, silently.
const VOICE_RULES = __VOICE_RULES__;

const VoiceCtor = window.SpeechRecognition || window.webkitSpeechRecognition || null;
const VOICE_ACK_KEY = 'viva-voice';
// The three verbs that must still work while a note field holds the caret.
const VOICE_ESCAPES = ['save', 'cancel', 'stop'];

let _rec = null;          // constructed on first start, never at load
let _voiceOn = false;     // the reviewer's switch, not the recognizer's state
let _voiceRestarts = 0;
// The card a spoken verb last applied to. See `voiceReopen` — this is the way
// back out of the state where nothing is open.
let _voiceLastCard = null;

function voiceSupported() { return Boolean(VoiceCtor); }
function voiceIsOn() { return _voiceOn; }

/* Lowercase, punctuation to spaces, whitespace collapsed — the form every
   phrase in the table is written in. Known limitation: a hyphen inside a
   leading word can split it in two; never observed in practice, so left
   unguarded rather than fixed. */
function normalizeUtterance(raw) {
  return String(raw || '').toLowerCase().replace(/[^a-z0-9\s']/g, ' ')
                          .replace(/\s+/g, ' ').trim();
}

// First match wins, and the table is sorted longest-first, so "request
// changes …" can never be read as the verb `changes` carrying "request".
function matchVoiceRule(norm) {
  for (let i = 0; i < VOICE_RULES.length; i++) {
    const r = VOICE_RULES[i];
    if (norm === r.phrase || (r.carries && norm.indexOf(r.phrase + ' ') === 0)) {
      return { rule: r, words: r.phrase.split(' ').length };
    }
  }
  return null;
}

// The reviewer's own text, in their own casing — taken off the RAW utterance,
// never the normalized one, because the note is what goes on the record.
function remainderOf(raw, words) {
  return String(raw).trim().split(/\s+/).slice(words).join(' ')
                    .replace(/^[\s,:;.!?-]+/, '');
}

function activeNoteField() {
  const a = document.activeElement;
  return a && a.classList && a.classList.contains('note-field') ? a : null;
}

function blurNoteField() { const f = activeNoteField(); if (f) f.blur(); }

// Land dictated text at the caret rather than replacing the box: a reviewer
// who typed half a sentence and finished it out loud keeps both halves.
function insertDictation(field, text) {
  const cur = field.value;
  const at = field.selectionStart == null ? cur.length : field.selectionStart;
  const before = cur.slice(0, at), after = cur.slice(at);
  const sep = before && !/\s$/.test(before) ? ' ' : '';
  field.value = before + sep + text + after;
  const pos = (before + sep + text).length;
  try { field.setSelectionRange(pos, pos); } catch (e) { /* detached field */ }
  // The Q&A note wires its state off `input`; the review composer reads
  // `.value` at save time. Firing the event serves the first and is inert for
  // the second, so one line covers both surfaces.
  field.dispatchEvent(new Event('input', { bubbles: true }));
}

/* ─── The strip ───────────────────────────────────────────
   Every utterance prints here with the reading it got, including ones that
   matched no verb — a reviewer must be able to tell "heard nothing" from
   "heard something and ignored it". */
function voiceSay(state, heard, read) {
  const strip = el('voice-strip');
  if (!strip) return;
  strip.style.display = '';
  strip.innerHTML = '<span class="vs-state">' + esc(state) + '</span>'
    + (heard ? '<span class="vs-heard">&ldquo;' + esc(String(heard).trim()) + '&rdquo;</span>' : '')
    + (read  ? '<span class="vs-read">' + esc(read) + '</span>' : '');
}

// Interim results are shown so the reviewer sees it hearing them, but
// `aria-hidden` so a live region doesn't announce every partial guess aloud —
// voiceSay (the final reading) is what announces.
function voiceInterim(text) {
  const strip = el('voice-strip');
  if (!strip) return;
  strip.style.display = '';
  strip.innerHTML = '<span class="vs-state">listening</span>'
    + '<span class="vs-interim" aria-hidden="true">' + esc(text) + '</span>';
}

function hideVoiceStrip() {
  const s = el('voice-strip');
  if (s) { s.style.display = 'none'; s.innerHTML = ''; }
}

/* ─── Routing one utterance ───────────────────────────────── */
// Is there still a round on screen? processing/complete views leave
// REVIEW_DATA set and rState.active null, the same state voiceReopen treats
// as "reopen where you were" — without this a verb walks stale cards.
function voiceRoundIsLive() {
  const shown = id => { const n = el(id); return n && n.style.display !== 'none'; };
  return !shown('processing-view') && !shown('complete-view');
}

function handleUtterance(raw) {
  const norm = normalizeUtterance(raw);
  if (!norm) return;
  /* Same terminal/modal guards as the keydown handler, same order (#174):
     speech is a second input path into the same verdict state. Terminal
     states also turn the mic off; between-rounds is not terminal, so the
     utterance is refused but the mic stays on. */
  if (deadSessionIsOpen()) { stopVoice('the session ended'); return; }
  if (!voiceRoundIsLive()) {
    voiceSay('heard', raw, 'no round on screen yet — nothing to command');
    return;
  }
  if (prefsIsOpen()) {
    voiceSay('heard', raw, 'the preferences panel is open — close it first');
    return;
  }
  if (REVIEW_DATA && recapIsOpen()) {
    voiceSay('heard', raw, 'the recap is open — confirm or close it by hand');
    return;
  }
  const hit = norm ? matchVoiceRule(norm) : null;
  const field = activeNoteField();
  if (field) {
    if (hit && norm === hit.rule.phrase && VOICE_ESCAPES.indexOf(hit.rule.act) >= 0) {
      voiceSay('heard', raw, runVoiceAct(hit.rule, ''));
      return;
    }
    insertDictation(field, String(raw).trim());
    voiceSay('dictated', raw, 'into the open note');
    return;
  }
  if (!hit) {
    // Named in the vocabulary of the page actually on screen: an interview has
    // no sections to approve, and offering the review's verbs there teaches the
    // reviewer the wrong three words.
    voiceSay('heard', raw, REVIEW_DATA
      ? 'no command — try "approve", "request changes …", "next"'
      : 'no command — try "question …", "next", or press dictate to answer aloud');
    return;
  }
  voiceSay('heard', raw, runVoiceAct(hit.rule, remainderOf(raw, hit.words)));
}

// Every branch RETURNS what it did, in the reviewer's words, and the strip
// prints it. A verb that acted silently would be indistinguishable from one
// that was misheard.
function runVoiceAct(rule, rest) {
  if (rule.act === 'stop') { stopVoice('you said so'); return 'stopped listening'; }
  const pop = document.querySelector('.comment-popover.is-open');
  if (rule.act === 'save') {
    if (pop) { pop.querySelector('.cmt-save').click(); return 'saved the comment'; }
    blurNoteField(); return 'nothing staged — closed the note';
  }
  if (rule.act === 'cancel') {
    if (pop) { pop.querySelector('.cmt-cancel').click(); return 'discarded the comment'; }
    blurNoteField(); return 'nothing staged — closed the note';
  }
  if (REVIEW_DATA) return runReviewVoiceAct(rule, rest);
  if (QA_DATA)     return runQAVoiceAct(rule, rest);
  return 'nothing on screen to command';
}

function runReviewVoiceAct(rule, rest) {
  /* "submit" is an alias for the RECAP, never for submitting. Ending the round
     is the one action this page already gates behind an overlay and a confirm
     click, and a spoken word does not get to skip a gate the mouse cannot. */
  if (rule.act === 'recap') { openRecap(); return 'opened the recap — confirm there to submit'; }
  const secs = REVIEW_DATA.sections;
  const id = rState.active;
  /* Approving/skipping the LAST open card leaves nothing active, and a
     hands-free reviewer has no click to reopen one — so the first verb spoken
     into that state REOPENS where they were instead of erroring forever. */
  if (!id) return voiceReopen();
  _voiceLastCard = id;
  const n = secs.findIndex(s => s.id === id) + 1;
  if (rule.act === 'approve') {
    // Same refusal the approve button carries, said out loud: a section
    // holding live comments is not one anybody can sign off.
    if (activeComments(id).length) return 'section ' + n + ' has open comments — settle them first';
    approveSection(id);
    return 'approved section ' + n;
  }
  if (rule.act === 'next') { skipReviewCard(id); return 'moved on from section ' + n; }
  if (rule.act === 'back') {
    const prev = secs[(secs.findIndex(s => s.id === id) - 1 + secs.length) % secs.length];
    activateReviewCard(prev.id);
    return 'back to section ' + (secs.indexOf(prev) + 1);
  }
  if (rule.act === 'comment') return stageVoiceComment(id, rule.type, rest);
  return 'no command';
}

// Reopen the last card a spoken verb touched, or the first if none yet.
// Carried sections have no accordion, so this walks forward to one that
// does rather than reporting success on a card that never appears.
function voiceReopen() {
  const secs = REVIEW_DATA.sections;
  const start = Math.max(0, secs.findIndex(s => s.id === _voiceLastCard));
  const order = [...secs.slice(start), ...secs.slice(0, start)];
  const hit = order.find(s => {
    const card = el('rcard-' + s.id);
    return card && !card.classList.contains('is-carried');
  });
  if (!hit) return 'nothing left open — use the recap to submit';
  _voiceLastCard = hit.id;
  activateReviewCard(hit.id);
  return 'reopened section ' + (secs.indexOf(hit) + 1) + ' — say the verb again';
}

/* The load-bearing half. Opens the composer, drops the transcript in its box,
   leaves the caret there — and stops. The reviewer reads what the recognizer
   heard and says "save" (or clicks it) to make it a comment. */
function stageVoiceComment(id, type, rest) {
  openCommentPopover(id, { type });
  const pop = el('rpop-' + id);
  const ta = pop && pop.querySelector('.cmt-pop-note');
  if (!ta) return 'could not open the composer';
  // Report what the box actually BECAME, not what was asked for: `suggest
  // wording` has no chip in diff mode, so the composer stays on `changes` and
  // saying otherwise would be a lie about the record being written.
  const became = pop.dataset.type;
  if (rest) insertDictation(ta, rest);
  // `preventScroll`, like the opener's own focus: openCommentPopover already
  // scrolled the popover into view, and a bare re-focus here would scroll
  // back to the field, leaving the type chips and quote above the fold.
  ta.focus({ preventScroll: true });
  return 'staged ' + (/^[aeiou]/.test(became) ? 'an ' : 'a ') + became
       + ' comment — say "save" to keep it';
}

function runQAVoiceAct(rule, rest) {
  if (rule.act === 'recap') return 'the interview has no recap gate';
  const qs = QA_DATA.questions;
  const id = qState.active;
  // Confirming the last question closes everything, the same dead end
  // `voiceReopen` exists for on the review page — and the same way out.
  if (!id) {
    const start = Math.max(0, qs.findIndex(q => q.id === _voiceLastCard));
    _voiceLastCard = qs[start].id;
    activateQACard(qs[start].id);
    return 'reopened question ' + (start + 1) + ' — say the verb again';
  }
  _voiceLastCard = id;
  const n = qs.findIndex(q => q.id === id) + 1;
  // The interview's own verb for "I am done here" is confirm, and it is the
  // same move as skip — `advanceQA` is what both buttons call.
  if (rule.act === 'next' || rule.act === 'approve') { advanceQA(id); return 'moved on from question ' + n; }
  if (rule.act === 'back') {
    const prev = qs[(qs.findIndex(q => q.id === id) - 1 + qs.length) % qs.length];
    activateQACard(prev.id);
    return 'back to question ' + (qs.indexOf(prev) + 1);
  }
  if (rule.act === 'comment') {
    const ta = el('qnote-' + id);
    if (!ta) return 'this question has no note field';
    if (rest) insertDictation(ta, rest);
    ta.focus();
    return 'added to question ' + n + '’s note';
  }
  return 'no command in the interview';
}

/* ─── The switch ──────────────────────────────────────────── */
function voiceAcknowledged() {
  try { return localStorage.getItem(VOICE_ACK_KEY) === 'ack'; } catch (e) { return false; }
}

/* Disclosed once rather than buried: the browser's recognizer is a network
   service — viva stays keyless and stores no audio, but the audio does
   leave the machine. The reviewer decides with that fact in front of them. */
function showVoiceNotice(after) {
  const strip = el('voice-strip');
  if (!strip) return;
  strip.style.display = '';
  strip.innerHTML = '<span class="vs-state">voice</span>'
    + '<span class="voice-notice">Your browser’s speech recognition sends audio to its vendor '
    + '(Google, in Chrome). viva itself stays keyless and keeps no recording.'
    + '<button type="button" id="voice-ack">start listening</button>'
    + '<button type="button" id="voice-nack">not now</button></span>';
  el('voice-ack').onclick = () => {
    try { localStorage.setItem(VOICE_ACK_KEY, 'ack'); } catch (e) { /* holds for this tab */ }
    beginVoice(after);
  };
  el('voice-nack').onclick = () => hideVoiceStrip();
  el('voice-ack').focus();
}

function startVoice(after) {
  if (!voiceSupported()) return;
  if (_voiceOn) { if (after) after(); return; }
  if (!voiceAcknowledged()) { showVoiceNotice(after); return; }
  beginVoice(after);
}

function beginVoice(after) {
  _voiceOn = true;
  _voiceRestarts = 0;
  paintVoiceToggle();
  ensureRecognizer();
  try { _rec.start(); } catch (e) { /* already starting; onstart still fires */ }
  voiceSay('listening', '', 'say "approve", "request changes …", "next" — Escape or "stop" to end');
  if (after) after();
}

function stopVoice(why) {
  const wasOn = _voiceOn;
  _voiceOn = false;                       // read by onend — set BEFORE stop()
  paintVoiceToggle();
  if (_rec) { try { _rec.stop(); } catch (e) { /* never started */ } }
  if (wasOn) voiceSay('off', '', why || 'microphone off');
}

function toggleVoice() {
  if (_voiceOn) stopVoice('you turned it off'); else startVoice();
}

function ensureRecognizer() {
  if (_rec) return;
  _rec = new VoiceCtor();
  _rec.continuous = true;
  _rec.interimResults = true;
  _rec.lang = document.documentElement.lang || 'en-US';

  _rec.onresult = e => {
    _voiceRestarts = 0;                   // real speech: the storm guard resets
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) handleUtterance(r[0].transcript);
      else interim += r[0].transcript;
    }
    if (interim.trim()) voiceInterim(interim.trim());
  };

  _rec.onerror = ev => {
    // A refused microphone is terminal — restarting just re-refuses. Everything
    // else (`no-speech`, `aborted`, `network`) is transient and `onend`, which
    // always follows, is the one place that decides whether to come back.
    if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
      stopVoice('the browser refused microphone access');
    }
  };

  _rec.onend = () => {
    if (!_voiceOn) return;                // the reviewer's switch wins
    /* A "continuous" recognizer is really a restarted one — Chrome ends the
       session after silence. The counter is a storm guard against an
       invisible infinite restart loop; it resets on every real result. */
    if (_voiceRestarts >= 8) { stopVoice('the recognizer kept dropping'); return; }
    _voiceRestarts++;
    setTimeout(() => { if (_voiceOn) { try { _rec.start(); } catch (e) {} } }, 250);
  };
}

function paintVoiceToggle() {
  const btn = el('voice-toggle');
  if (!btn) return;
  btn.textContent = 'voice: ' + (_voiceOn ? 'listening' : 'off');
  btn.classList.toggle('is-live', _voiceOn);
  /* The label states which state is ON, so the accessible name must say what
     the button DOES — same rule the theme toggle follows, so a screen reader
     can tell a state from an action. */
  btn.setAttribute('aria-label', _voiceOn
    ? 'Voice input is listening. Activate to stop listening.'
    : 'Voice input is off. Activate to start the oral examination.');
  document.querySelectorAll('.mic-btn').forEach(m => m.classList.toggle('is-live', _voiceOn));
}

function initVoice() {
  // No control where there is no recognizer: a button that cannot work is
  // worse than no button, and every caller already asks `voiceSupported()`
  // before drawing its own.
  if (!voiceSupported()) return;
  el('voice-toggle').style.display = '';
  paintVoiceToggle();
  el('voice-toggle').addEventListener('click', () => toggleVoice());
}
initVoice();
/* ═══ End voice ══════════════════════════════════════════════════════════ */

el('prefs-toggle').addEventListener('click', () => openPrefsPanel(el('prefs-toggle')));
el('prefs-close').addEventListener('click', closePrefsPanel);
el('prefs-overlay').addEventListener('click', e => {
  if (e.target === el('prefs-overlay')) closePrefsPanel();   /* backdrop click */
});

el('sort-toggle').addEventListener('click', () => {
  rState.sortMode = rState.sortMode === 'confidence' ? 'document' : 'confidence';
  applyCardSort();
});

/* ─── Init — fetch data from server, then build cards ─── */
/* ─── SSE client ────────────────────────────────────────── */
// Soft, client-side timeout for #processing-view (#119): a slow qa→review
// hand-off doesn't trip es.onerror, so this catches it instead
// (docs/headless-contract.md §6/§7). Long enough not to false-trigger.
const PROCESSING_STILL_WAITING_MS = 20000;
let processingTimer = null;

// Clears the armed timeout (if any) and removes the still-waiting banner
// (if shown) — called whenever #processing-view's own visibility changes,
// so the timer's lifecycle never diverges from the view it describes.
function clearProcessingTimer() {
  if (processingTimer) { clearTimeout(processingTimer); processingTimer = null; }
  const b = el('processing-wait-banner');
  if (b) b.remove();
}

// A position: fixed banner prepended to document.body. Skipped outright if
// the connection has actually dropped: the dead-session overlay is the
// harder signal, and it's a full-screen scrim this banner would contradict.
function showStillWaitingBanner() {
  processingTimer = null;
  if (deadSessionIsOpen()) return;
  const b = document.createElement('div');
  b.id = 'processing-wait-banner';
  b.className = 'error-banner banner-info';
  b.textContent = 'Still waiting — check the terminal.';
  document.body.prepend(b);
}

/* A round arrived that this tab cannot render. The server already refuses
   this at `/next-round`; this is the strand backstop for when it doesn't —
   the previous round stays on screen instead of freezing on "revising".
   Full `--orange` ink: a broken payload, not a slow one. */
function showRoundRefused() {
  clearProcessingTimer();       // its banner and ours would stack at top: 0
  if (el('round-refused-banner')) return;
  const b = document.createElement('div');
  b.id = 'round-refused-banner';
  b.className = 'error-banner';
  b.textContent = 'A round arrived that this tab cannot render — check the terminal.';
  document.body.prepend(b);
}

// The one removal site, called from both SSE handlers that mean "the session
// moved on". A `position: fixed` banner with no removal path outlives the thing
// it describes and sits over a perfectly good later round.
function clearRoundRefused() {
  const b = el('round-refused-banner');
  if (b) b.remove();
}

/* ─── Dead session (#174) ───────────────────────────────────
   A banner alone let a reviewer keep working into a socket that's gone.
   Three layers block it: `inert` takes pointer/Tab from the background, the
   document keydown listener catches what `inert` can't, and sendSubmit
   refuses outright. Not dismissible — only es.onopen (a real reconnect)
   clears it, so a lid-close blip can't lock out a live session. */
function deadSessionIsOpen() { return el('dead-overlay').style.display !== 'none'; }

function showDeadSession() {
  if (deadSessionIsOpen()) return;
  // Close the other modals FIRST: they sit outside setBackgroundInert's
  // subtree, and both restore focus into the background on close — after
  // this overlay takes focus, that would pull it straight back out.
  closeRecap();
  closePrefsPanel();
  closePalette();
  // And the microphone: `inert` on #paper takes pointer/Tab from the voice
  // toggle, and the keydown listener returns above the Escape-stop branch —
  // so a mic left hot here has no control left to reach.
  stopVoice('the session ended');
  // The one command this tab can honestly name. `doc_file` is a real target
  // only in review mode (parse_diff.py writes review_target.py's LABEL, e.g.
  // "PR #187"); qa has no doc, so it gets the generic line instead.
  const doc = REVIEW_DATA && REVIEW_DATA.mode === 'review' && REVIEW_DATA.doc_file;
  el('dead-cmd').textContent = doc ? '/viva-review ' + doc : '';
  el('dead-resume').style.display = doc ? '' : 'none';
  el('dead-overlay').style.display = '';
  setBackgroundInert(true);
  el('dead-panel').focus();
}

function hideDeadSession() {
  if (!deadSessionIsOpen()) return;
  const overlay = el('dead-overlay');
  const hadFocus = overlay.contains(document.activeElement);
  overlay.style.display = 'none';
  setBackgroundInert(false);   // clear inert BEFORE restoring focus, same order as closeRecap
  if (hadFocus) el('btn-submit').focus();
}

// Renders #processing-view's two variants: the between-rounds card (with
// the reviewer's just-submitted rows verbatim) when submitReview snapshotted
// rows, else the minimal line — qa submits and zero-row reviews never do.
function renderProcessingView() {
  const heading = el('processing-heading');
  const list    = el('processing-requests');
  const rows    = (betweenRounds && betweenRounds.rows) || [];
  if (!rows.length) {
    heading.textContent = 'Claude is revising…';
    list.style.display = 'none';
    list.innerHTML = '';
    return;
  }
  heading.textContent = 'REV ' + String(betweenRounds.round).padStart(2, '0') + ' submitted — the agent is revising';
  // Same note the reviewer wrote in the margin, same grammar — `info` is an
  // open fact and takes the fact ink, everything else takes the judgment ink.
  // A second row vocabulary for the same objects is what `.pr-*` was.
  list.innerHTML = rows.map(r =>
    '<div class="nt' + (r.type === 'info' ? ' nt-fact' : '') + '">'
    + '<div class="nh">' + esc(r.type)
    +   '<span class="pn">&middot; ' + esc(r.sectionTitle) + '</span></div>'
    + '<div class="nt-body">' + esc(r.note) + '</div>'
    + '</div>').join('');
  list.style.display = '';
}

function connectSSE() {
  const es = new EventSource('/events');

  es.addEventListener('processing', () => {
    clearRoundRefused();  // a new submit is in flight; the refused round is history
    closeRecap();       // the review it recapped is gone from under it
    closePrefsPanel();  // ditto — no full-screen backdrop survives a view swap
    // Retitle the instant the round is submitted, before the agent's response
    // arrives — otherwise the tab keeps showing the previous "your turn" REV
    // badge while the agent is actually working (#172).
    setProcessingTabTitle(REVIEW_DATA ? tabDocName(REVIEW_DATA.doc_file) : null);
    setTabFavicon('processing');
    renderProcessingView();
    el('review-view').style.display     = 'none';
    el('qa-view').style.display         = 'none';
    el('processing-view').style.display = '';
    // Retire the previous round's controls with its cards: `skip rest &
    // submit` staying live would POST a second submit for a round already in
    // flight. The bar itself stays (theme/prefs/voice); the 'round' handler restores it.
    document.querySelector('.btn-group').style.display = 'none';
    el('foot-seg').style.display = 'none';   // a rule describing a round that is over
    // #stats-area is aria-live=polite, so this announces the wait once instead
    // of leaving `blocked · N unreviewed` from the dead round on screen. The
    // page's own words for this state, from the processing heading above it.
    el('stat-pending').textContent = 'submitted — the agent is revising';
    clearProcessingTimer();
    processingTimer = setTimeout(showStillWaitingBanner, PROCESSING_STILL_WAITING_MS);
  });

  es.addEventListener('round', e => {
    const data = JSON.parse(e.data);
    // Guard BEFORE routing: everything below reads data.sections or overwrites
    // state the current round still uses, so an unrenderable payload must be
    // turned away first. See showRoundRefused for why the client refuses at all.
    if (!data || !Array.isArray(data.sections)) {
      console.error('viva: refused a round payload with no sections[]', data);
      showRoundRefused();
      return;
    }
    clearRoundRefused();
    const modeWord = data.mode === 'diff' ? 'diff' : 'review';
    closeRecap();        // a stale grid must never sit over a fresh round's cards
    closePrefsPanel();   // ditto — a fresh round's cards must never sit behind it
    REVIEW_DATA       = data;
    TAB_REPO          = data.repo || null;   // a qa→review hand-off carries it too (#172)
    // A qa → review hand-off (#109) lands here too: the qa session is done,
    // so drop QA_DATA/qState.active to keep qa-branch logic (keydown handler,
    // updateQAStats/submitQA) from picking up stale state once cards show.
    QA_DATA           = null;
    qState.active     = null;
    rState.verdicts   = {};
    rState.active     = null;
    // The snapshot's round is over — a later 'processing' event with no
    // fresh submit behind it falls back to the minimal line, never a stale
    // card.
    betweenRounds = null;
    setDocTitleBlock(data, modeWord, modeWord === 'diff' ? 'diff' : '');
    el('round-badge').textContent = String(data.round).padStart(2, '0');
    const rev = 'REV ' + String(data.round).padStart(2, '0');
    setTabTitle(tabDocName(data.doc_file), ...(data.mode === 'diff' ? ['diff', rev] : [rev]));
    setTabFavicon('turn');
    el('review-cards').innerHTML  = '';
    initReview();
    el('processing-view').style.display = 'none';
    clearProcessingTimer();
    // Hide qa-view unconditionally rather than trusting a prior 'processing'
    // event to have done so: a reconnect that missed that event (mid-
    // transition) would otherwise leave qa-view visible under the review cards.
    el('qa-view').style.display         = 'none';
    // ...and its page class with it. `mode-diff` happens to out-order `mode-qa`
    // in the stylesheet today, so a stale class wouldn't clamp the diff page —
    // but that's source order doing the work, not a rule to rely on.
    document.body.classList.remove('mode-qa');
    el('review-view').style.display     = '';
    // The whole bar restoration in one place. #foot-seg and #stat-pending need
    // no explicit restore: initReview() → updateReviewStats → reviewFootSeg →
    // renderFootSeg already sets both.
    document.querySelector('.btn-group').style.display = '';
    el('btn-skip').disabled   = false;
    el('btn-submit').disabled = false;
  });

  es.addEventListener('complete', e => {
    es.close(); // prevent onerror when server shuts down 2s later
    const data = JSON.parse(e.data);
    closePrefsPanel();  // no full-screen backdrop survives into complete-view
    stopVoice('the review is signed off');  // nothing left to command
    el('processing-view').style.display = 'none';
    clearProcessingTimer();
    el('review-view').style.display     = 'none';
    el('qa-view').style.display         = 'none';
    el('complete-view').style.display   = '';
    setTabTitle(REVIEW_DATA ? tabDocName(REVIEW_DATA.doc_file) : null, 'done');
    setTabFavicon('done');
    const r   = data.rounds_total;
    const s   = data.sections_total;
    const rev = data.sections_revised != null ? data.sections_revised : null;
    el('complete-headline').textContent = '';
    const stampSub = el('stamp-sub');
    if (stampSub) {
      // Absent counts DROP the line entirely rather than degrade it: a real
      // caller already omits `sections_total`, and "? sheets · 1 revision" in
      // the APPROVED stamp is worse than omitting it. `display:none` too,
      // since `.stamp-sub`'s margin-top would still show for an empty string.
      const counted = typeof r === 'number' && typeof s === 'number';
      stampSub.textContent = counted
        ? `${s} sheet${s !== 1 ? 's' : ''} · ${r} revision${r !== 1 ? 's' : ''}`
        : '';
      stampSub.style.display = counted ? '' : 'none';
    }
    const stampMeta = el('stamp-meta');
    if (stampMeta) stampMeta.textContent = 'viva · ' + new Date().toISOString().slice(0, 10);
    // A diff that re-captured empty signed off with nothing left to approve
    // (`resolved: "empty"`, loop.py finish); say so rather than counting
    // sections that no longer exist.
    el('complete-detail').textContent   = data.resolved === 'empty'
      ? `diff fully resolved · ${rev != null ? rev : 0} hunk${rev !== 1 ? 's' : ''} revised`
      : (rev != null ? `${rev} section${rev !== 1 ? 's' : ''} revised` : '');
    const entries = (REVIEW_DATA && REVIEW_DATA.ledger) || [];
    if (entries.length) {
      el('complete-ledger').style.display = '';
      el('complete-ledger-count').textContent = entries.length;
      el('complete-ledger-rows').innerHTML = ledgerRowsHTML(entries);
    }
    document.querySelector('.bottom-bar').style.display = 'none';
  });

  es.onerror = () => {
    // The connection actually dropping is the harder, more specific signal —
    // it supersedes any still-waiting banner already shown rather than the
    // two stacking, one over a full-screen scrim.
    const waiting = el('processing-wait-banner');
    if (waiting) waiting.remove();
    showDeadSession();
  };

  // EventSource retries on its own and onerror fires every attempt, so
  // "dropped" and "gone" look identical — a successful reconnect is the only
  // thing that tells them apart: the server outlived the drop.
  es.onopen = () => { hideDeadSession(); };
}

/* ─── Command palette wiring ────────────────────────────── */
el('pal-input').addEventListener('input', e => renderPalette(e.target.value));
el('pal-open').addEventListener('click', () => openPalette());
el('qa-pal-open').addEventListener('click', () => openPalette());
el('pal-overlay').addEventListener('mousedown', e => {
  if (e.target === el('pal-overlay')) closePalette();
});

/* ─── Keyboard shortcuts ────────────────────────────────── */
document.addEventListener('keydown', e => {
  // Nothing on this page can reach the server any more (#174) — the
  // dead-session overlay is the one modal that doesn't close on Escape.
  // `inert` blocks pointer/Tab but not this listener, so without this
  // swallow, a/c/i and ⌘+Enter would keep mutating state behind the scrim.
  if (deadSessionIsOpen()) return;
  // ⌘K opens the palette from anywhere, including inside a textarea — a
  // reviewer mid-reply wants "jump to next open thread" without the mouse.
  // Sits ahead of the TEXTAREA/INPUT guard for that reason.
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    // Never over another dialog: the prefs panel and the recap gate are both
    // modal and both own Escape, so stacking a third would leave two things
    // claiming the same key.
    if (prefsIsOpen() || (REVIEW_DATA && recapIsOpen())) return;
    e.preventDefault();
    if (paletteIsOpen()) closePalette(); else openPalette();
    return;
  }
  // The palette is modal, and its own input is where typing goes — so its
  // keys are handled before the TEXTAREA guard would return on that input.
  if (paletteIsOpen()) {
    if (e.key === 'Escape')    { e.preventDefault(); closePalette(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); movePalette(1);  return; }
    if (e.key === 'ArrowUp')   { e.preventDefault(); movePalette(-1); return; }
    if (e.key === 'Enter') {
      e.preventDefault();
      // ⇧⏎ is printed beside `Approve all unblocked`; it runs that row
      // wherever the highlight is, or the highlighted row when it is absent.
      const all = e.shiftKey ? _palCmds.findIndex(c => c.key === '⇧⏎') : -1;
      runPalette(all >= 0 ? all : _palIdx);
      return;
    }
    return;
  }

  // Escape closes the composer from inside its own textarea — the one place
  // the TEXTAREA guard below would otherwise swallow it. An empty box
  // cancels; a box with a draft only blurs, so Escape never loses typed text.
  if (e.key === 'Escape' && !prefsIsOpen() && !(REVIEW_DATA && recapIsOpen())) {
    const pop = document.querySelector('.comment-popover.is-open');
    if (pop) {
      e.preventDefault();
      const ta = pop.querySelector('.cmt-pop-note');
      if (ta && ta.value.trim()) ta.blur(); else pop.querySelector('.cmt-cancel')?.click();
      return;
    }
  }

  /* Escape stops listening from ANYWHERE, ahead of the TEXTAREA/INPUT guard
     on purpose: staging a spoken comment puts the caret in a textarea, so
     without this the mic can't be turned off exactly when it's hottest. */
  if (e.key === 'Escape' && voiceIsOn()
      && !prefsIsOpen() && !(REVIEW_DATA && recapIsOpen())) {
    e.preventDefault(); stopVoice('you pressed Escape'); return;
  }

  const tag = document.activeElement?.tagName;
  if (tag === 'TEXTAREA' || tag === 'INPUT') return;

  // The preferences panel is reachable in every mode, so its Escape check
  // sits ahead of the REVIEW_DATA-gated block below (the recap overlay's
  // equivalent check lives inside it, since recap is review/diff-only).
  if (e.key === 'Escape' && prefsIsOpen()) { closePrefsPanel(); return; }
  // Modal, like the recap overlay: every other key is swallowed here so it
  // can't reach the card/QA shortcuts behind the backdrop. `inert` blocks
  // pointer/Tab but not this listener, and focus here is never TEXTAREA/INPUT.
  if (prefsIsOpen()) return;

  /* `v` is a MODE toggle, so it's in the theme/palette tier, not with
     `a`/`c`/`i`: it works in both interview and review, and gating on
     `rState.active` would make it dead with nothing expanded. */
  if (e.key === 'v' && !e.metaKey && !e.ctrlKey && !e.altKey && voiceSupported()) {
    e.preventDefault(); toggleVoice(); return;
  }
  // `t` is the theme control's keycap in both directories, like `v`.
  if (e.key === 't' && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault(); cycleTheme(); return;
  }

  if (REVIEW_DATA) {
    if (e.key === 'o' && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); toggleRecap(); return; }
    if (e.key === 'Escape' && recapIsOpen()) { closeRecap(); return; }
    if (recapIsOpen()) {
      // The recap is modal — card shortcuts stay inert under it; ⌘/Ctrl+Enter
      // keeps its "submit" meaning by driving the gate's own confirm control.
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); el('recap-confirm').click(); }
      return;
    }
    // The palette's other two keycaps, bound where they are printed.
    if (e.key === 'l' && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); openLedger(); return; }
    if (e.key === 'j' && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const t = nextOpenThread();
      if (t) { e.preventDefault(); activateReviewCard(t); }
      return;
    }
    // The margin's own verbs, live on the note that has focus: r/s/y/n are
    // printed on Reply / Settle / Accept / Change anyway, and a bare `s` in
    // the prose must never settle a thread the reader is not looking at.
    if (e.key.length === 1 && 'rsyn'.includes(e.key) && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const note = document.activeElement && document.activeElement.closest
        ? document.activeElement.closest('.open-thread') : null;
      const verb = note && [...note.querySelectorAll('.nt-acts .nt-btn')]
        .find(b => (b.querySelector('kbd') || {}).textContent === e.key);
      if (verb) { e.preventDefault(); verb.click(); return; }
    }
    if (e.key === 'a' && !e.metaKey && !e.ctrlKey && !e.altKey && rState.active) { e.preventDefault(); approveSection(rState.active); return; }
    // Modifier-guarded like 'a' and 'o': bare `c` opens a composer, so an
    // unguarded branch would swallow ⌘C/Ctrl+C — copy, on a page of prose.
    if (e.key === 'c' && !e.metaKey && !e.ctrlKey && !e.altKey && rState.active) { e.preventDefault(); openTypedComment(rState.active, 'changes'); return; }
    if (e.key === 'i' && !e.metaKey && !e.ctrlKey && !e.altKey && rState.active) { e.preventDefault(); openTypedComment(rState.active, 'info'); return; }
    if (e.key === 'Tab' && !e.shiftKey) {
      // Advance to the next card only while focus is inside the active card;
      // otherwise let Tab navigate natively so the skip-link, bottom-bar
      // controls, and browser chrome stay reachable (#75). Shift+Tab is always native.
      const card = rState.active ? el('rcard-' + rState.active) : null;
      if (card && card.contains(document.activeElement)) {
        e.preventDefault();
        skipReviewCard(rState.active);
        return;
      }
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      const sub = el('btn-submit');
      if (sub.classList.contains('ready') && !sub.disabled) { e.preventDefault(); sub.click(); }
      return;
    }
  }

  // Guarded by !REVIEW_DATA in addition to the round handler's QA_DATA/
  // qState.active reset (#109 hand-off): belt-and-suspenders so a digit
  // keystroke can never route through the qa branch while review is on screen.
  if (!REVIEW_DATA && QA_DATA && qState.active) {
    const q = QA_DATA.questions.find(q => q.id === qState.active);
    if (q) {
      const n = parseInt(e.key, 10);
      if (!isNaN(n) && n >= 1 && n <= q.choices.length) {
        e.preventDefault();
        pickQAChoice(qState.active, q.choices[n - 1]);
        return;
      }
      // `c` confirms, the way `a` approves a section — printed on the button
      // itself, so the keyboard layer is on the control, not just the legend.
      // Free here since this whole branch is guarded on `!REVIEW_DATA`.
      if (e.key === 'c' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault(); advanceQA(qState.active); return;
      }
    }
    if (e.key === 'Tab' && !e.shiftKey) {
      const card = el('qacard-' + qState.active);
      if (card && card.contains(document.activeElement)) {
        e.preventDefault(); advanceQA(qState.active); return;
      }
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      const sub = el('btn-submit');
      if (sub.classList.contains('ready') && !sub.disabled) { e.preventDefault(); sub.click(); }
      return;
    }
  }
});

// Runs immediately, not on DOMContentLoaded: this script sits at the end of
// <body>, after everything it references, so the DOM is already parsed.
// Waiting would needlessly serialize this /input fetch behind the deferred
// /vendor <script> tags, which finish before DOMContentLoaded fires anyway.
el('btn-skip').disabled   = true;
el('btn-submit').disabled = true;

// Titleblock's doc-path/doc-title cells — shared by the initial boot
// (bootReviewMode) and the in-place 'round' SSE hand-off, so a qa→review
// hand-off (#109) populates them like a fresh boot instead of leaving them blank.
function setDocTitleBlock(data, modeWord, docFallback) {
  el('doc-path').textContent    = data.doc_file || docFallback;
  el('doc-path').title          = data.doc_file || docFallback;   /* full path on hover when truncated */
  el('doc-title').innerHTML     = 'viva <em>' + modeWord + '</em>';
}

// Shared boot tail for the two review-card modes (review and diff) — the
// title block, round badge, view reveal, card build, and SSE hookup are
// identical apart from the mode word and the doc-path fallback.
function bootReviewMode(data, modeWord, docFallback) {
  setDocTitleBlock(data, modeWord, docFallback);
  el('round-badge').textContent = String(data.round).padStart(2, '0');
  setTabTitle(tabDocName(data.doc_file), ...(modeWord === 'diff' ? ['diff'] : []), 'REV ' + String(data.round).padStart(2, '0'));
  setTabFavicon('turn');
  el('review-view').style.display = '';
  initReview();
  connectSSE();
}

// The preferences fetch is awaited alongside /input so the badge-to-entry
// link (PREFS_BY_ID) resolves on first paint. A failed/malformed fetch
// degrades to an empty list rather than blocking the boot — every badge
// falls back to plain rendering, the same degrade an unmatched [id] gets.
Promise.all([
  // Timed, so the footer's latency line is a measurement of this page's own
  // round trip rather than a number copied off a mock.
  timedFetch('/input').then(r => r.json()),
  fetch('/preferences').then(r => r.json()).catch(() => []),
])
  .then(([data, prefs]) => {
    TAB_REPO    = data.repo || null;   // set once at boot, every mode (#172)
    PREFS_DATA  = Array.isArray(prefs) ? prefs : [];
    PREFS_BY_ID = new Map(PREFS_DATA.map(p => [p.id, p]));
    // Ships hidden, same treatment as the confidence sort toggle
    // (references/producers.md, Confidence triage): an empty/absent store
    // has nothing to inspect or mute, so the control stays off.
    el('prefs-toggle').style.display = PREFS_DATA.length ? '' : 'none';
    el('btn-skip').disabled   = false;
    el('btn-submit').disabled = false;

    if (data.mode === 'review') {
      REVIEW_DATA = data;
      bootReviewMode(data, 'review', '');
    } else if (data.mode === 'diff') {
      REVIEW_DATA = data;
      document.body.classList.add('mode-diff');
      // diff2html's stylesheet is mode-specific, so it's injected here rather
      // than a render-blocking <link> in <head>. renderDiffHunk gates on
      // link.sheet; the retry here upgrades a fenced-view card once the CSS
      // arrives (version pinned per assets/vendor/README.md).
      const d2hCss = document.createElement('link');
      d2hCss.id = 'diff2html-css';
      d2hCss.rel = 'stylesheet';
      d2hCss.href = '/vendor/diff2html-3.4.56.min.css';
      document.head.appendChild(d2hCss);
      retryOnceScriptsLoad(['diff2html-css'], '.section-content.d2h-pending');
      bootReviewMode(data, 'diff', 'diff');
    } else {
      // `choices` is OPTIONAL on the wire (references/qa.md) — normalized once
      // here, at the boundary, rather than guarded at each downstream reader.
      // An absent field used to throw during render, taking the whole interview down.
      QA_DATA = data;
      (QA_DATA.questions || []).forEach(q => {
        if (!Array.isArray(q.choices)) q.choices = [];
      });
      // Taste-first reorder, at the same boundary the `choices` normalization
      // above uses — see orderQAQuestions (issue #175).
      QA_DATA.questions = orderQAQuestions(QA_DATA.questions || []);
      el('qa-title').textContent        = data.context || 'Q&A phase';
      el('qa-title').title              = data.context || 'Q&A phase';   /* full topic on hover when truncated */
      el('qa-count-badge').textContent  = String(data.questions.length);
      setTabTitle(data.context || 'brainstorm');
      setTabFavicon('turn');
      // Same page cap as the review print: a column that holds a measure plus
      // a margin, and no wider. See `.mode-doc, .mode-qa` in the stylesheet.
      document.body.classList.add('mode-qa');
      el('qa-view').style.display = '';
      initQA();
      connectSSE();
    }
  })
  .catch(err => {
    document.body.innerHTML = '<p class="load-error">Failed to load session data: ' + (err.message || 'network error') + '</p>';
  });
</script>
</body>
</html>""".replace("__PREFS_SCRIPT_PATH__", _PREFS_SCRIPT_PATH_JS).replace(
    # The check-flag registry, injected rather than restated in JS. `CHECK_KINDS`
    # is what makes a producer's flags gate a `checks` round, and it fails open —
    # an unregistered kind is simply invisible. A hand-kept second copy in the
    # frontend would fail open the same silent way, in the surface that draws
    # `checks N/M`, so the frontend reads the registry itself.
    "__CHECK_KINDS__", json.dumps(list(schema.CHECK_KINDS))
).replace(
    # The scope registry, beside the check registry so the two read as one
    # block. A doc-scope flag is about the DOCUMENT, not about the card its
    # producer had to anchor it to; the frontend routes on this and would
    # fail open the same silent way on a hand-kept copy.
    "__DOC_SCOPE_KINDS__", json.dumps(list(schema.DOC_SCOPE_KINDS))
).replace(
    # The spoken grammar, injected for the same reason and in the same shape:
    # one table, in `_VOICE_RULES` above, already sorted longest-phrase-first so
    # the browser can take the first match and be right.
    "__VOICE_RULES__", json.dumps(list(_VOICE_RULES))
).replace(
    # The thread-status label map, injected for the same reason: one table,
    # shared with `revision_history.py`'s report, so the tab and the appended
    # Revision History describe a thread's status in the same words.
    "__THREAD_STATUS_LABELS__", json.dumps(dict(schema.THREAD_STATUS_LABELS))
)

_HTML_BYTES = HTML.encode()

_shutdown = threading.Event()
_input_data: dict = {}
_output_path: str = ""
# Set once at startup from `Path(--output).resolve().parent` and never
# reassigned — the one launch-time root `/next-round`'s `output` field is
# contained to. `--output` is documented (headless-contract.md §4) to
# legitimately live outside `.viva/`, so this is NOT hardcoded to `_viva_dir`;
# it is whatever directory the operator chose at launch, fixed for the life
# of the process so a POSTed round cannot redirect a later write to a
# different directory than the one this session was launched to write into.
_output_root: Path = Path(".")
# Set once at startup from --input; historical round files for the
# revision-count derivation (issue #141) live here, never reassigned after.
_viva_dir: Path = Path(".")
_url: str = ""  # set once at startup; reused by the /next-round hand-off log line
_sse_clients: list = []
_clients_lock = threading.Lock()
_data_lock = threading.Lock()
_ledger: list = []
# The verdicts this server actually received, snapshotted at /submit and read by
# /complete's finish guard. Deliberately not a re-read of `_output_path`: the
# file on disk can be replaced by a caller between the two calls, and the guard
# must judge what the human submitted. `None` (not `{}`) means no round has been
# submitted for the round currently loaded — its own refusal, distinct from a
# round that was reviewed and came back with work outstanding.
_last_verdicts = None
# The launch `--mode`, fixed at startup. The finish guard keys on this
# rather than on the round payload's `mode`, which any caller can set.
_launch_mode: str = "review"
# Serializes the /preferences/mute read-modify-write against a concurrent
# mute (single-reviewer, single-tab in practice, but cheap insurance against
# two fast double-clicks or two tabs open on the same session — #142).
_prefs_lock = threading.Lock()


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _push_sse(event: str, data: dict) -> None:
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
    with _clients_lock:
        dead = []
        for wfile in _sse_clients:
            try:
                wfile.write(msg)
                wfile.flush()
            except (IOError, OSError):
                dead.append(wfile)
        for wfile in dead:
            _sse_clients.remove(wfile)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="viva review server")
    p.add_argument("--mode",       required=True, choices=["review", "qa", "diff"])
    p.add_argument("--input",      required=True)
    p.add_argument("--output",     required=True)
    p.add_argument("--no-browser", action="store_true", help="Skip opening browser (for testing)")
    return p.parse_args()


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def load_input(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _revision_counts(sections: list, round_num: int, viva_dir: Path) -> tuple[dict[str, int], bool]:
    """Cumulative per-section revision count for the round served (#141) —
    wire-only, never written to disk. Walks historical rounds 1..round_num-1
    plus the in-hand round's own sections, counting one revision per round a
    section carried a non-null `diff` (presence, not truthiness — an empty
    `diff: []` still counts, matching `.rev-tri`'s own JS truthiness).

    Returns `(counts, partial)`; `partial` is True if any historical round
    file was missing/unparseable/malformed, making the count a lower bound
    callers must surface rather than print as exact. Counted via a `set` of
    `section_key`s per round so duplicate-titled sections aren't double-counted.
    """
    counts: dict[str, int] = {}
    partial = False
    for k in range(1, round_num):
        hist_path, _ = schema.round_file_paths(viva_dir, k)
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            partial = True
            continue
        if not isinstance(hist, dict):
            partial = True
            continue
        hist_sections = hist.get("sections")
        # "sections": null (or any non-list) is as unusable as a missing
        # file — degrade to `partial` instead of `for s in None` raising
        # TypeError, which would be fatal to /input and the SSE push.
        if not isinstance(hist_sections, list):
            partial = True
            continue
        for key in {schema.section_key(s.get("title", ""))
                    for s in hist_sections
                    if isinstance(s, dict) and s.get("diff") is not None}:
            counts[key] = counts.get(key, 0) + 1
    for key in {schema.section_key(s.get("title", ""))
                for s in sections
                if isinstance(s, dict) and s.get("diff") is not None}:
        counts[key] = counts.get(key, 0) + 1
    return counts, partial


def _with_revision_counts(data: dict, viva_dir: Path) -> dict:
    """Attach `revision_count` to each section whose cumulative count
    (`_revision_counts`) reaches 2+, the threshold the card's `△ NN`
    multiplier renders at (#141). Functional: never mutates `data`/sections
    in place; the served response is the only place this key exists.

    When `_revision_counts` returned a lower bound, every section with a
    `diff` this round gets `revision_count_partial: True` — even below the
    2+ threshold — since an unread round could have tipped it over. The
    client renders that as a caveat, never a bare possibly-wrong number."""
    sections = data.get("sections")
    if not isinstance(sections, list):
        return data
    try:
        round_num = int(data.get("round", 0))
    except (TypeError, ValueError):
        round_num = 0
    counts, partial = _revision_counts(sections, round_num, viva_dir)

    def _tag(s: dict) -> dict:
        if not isinstance(s, dict):
            return s
        n = counts.get(schema.section_key(s.get("title", "")), 0)
        # Server-owned wire fields: strip any `revision_count`/
        # `revision_count_partial` the caller's payload carried so the
        # served value is always what `_revision_counts` just computed.
        base = {k: v for k, v in s.items()
                if k not in ("revision_count", "revision_count_partial")}
        if n >= 2:
            base["revision_count"] = n
        if partial and s.get("diff") is not None:
            base["revision_count_partial"] = True
        return base

    return {**data, "sections": [_tag(s) for s in sections]}


def _atomic_write(path: Path, text: str) -> None:
    # A reader polling with `[ -f path ]` then `cat path` must never observe a
    # truncated/partial file. Write a sibling tmp, then rename atomically.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def write_output(path: str, data: dict) -> None:
    _atomic_write(Path(path), json.dumps(data, indent=2))


def _load_preferences_store(viva_dir: Path) -> dict:
    """Tolerant read of `.viva/preferences.json` for the routes below.
    Deliberately not `preferences._load`, which `sys.exit()`s on a parse
    failure — fatal here since one corrupt store would take the whole
    review server down mid-session.

    Missing, unparseable, or parseable-but-wrong-shape all degrade to an
    empty store (PRODUCT.md principle 4): `preferences.select()`'s
    `_normalize` only guards the write path, not this read path."""
    path = viva_dir / "preferences.json"
    if not path.exists():
        return preferences.empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return preferences.empty_store()
    if not isinstance(data, dict) or not isinstance(data.get("preferences"), dict):
        return preferences.empty_store()
    # A per-entry non-dict value would still crash `select()`'s
    # `p.get("status")` — drop those entries rather than the whole store.
    data["preferences"] = {k: v for k, v in data["preferences"].items()
                            if isinstance(v, dict)}
    return data


# Raster formats only — SVG is excluded deliberately because it can carry
# embedded JavaScript. The MIME is also the sole source of the on-disk
# extension, so this allowlist doubles as the extension allowlist.
ALLOWED_IMAGE_MIMES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MiB decoded, per image
MAX_SUBMIT_BYTES = 256 * 1024 * 1024  # 256 MiB total /submit request body


def _write_item_images(item: dict, prefix: str, safe_id: str, attach_dir: Path) -> None:
    """Pop `images` from item, validate, write files, set `attachments`. Mutates item."""
    images = item.pop("images", None)
    if not isinstance(images, list):
        return
    paths: list[str] = []
    for i, img in enumerate(images):
        if not isinstance(img, dict):
            continue
        ext = ALLOWED_IMAGE_MIMES.get(img.get("mime"))
        if ext is None:
            continue
        try:
            raw = base64.b64decode(img.get("data", ""), validate=True)
        except (ValueError, TypeError):
            continue
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            continue
        attach_dir.mkdir(parents=True, exist_ok=True)
        dest = attach_dir / f"{prefix}-{safe_id}-{i}.{ext}"
        try:
            dest.write_bytes(raw)
        except OSError as e:
            print(f"viva · warning: could not write attachment {dest}: {e}",
                  file=sys.stderr, flush=True)
            continue
        paths.append(str(dest))
    if paths:
        item["attachments"] = paths


def extract_attachments(data: dict, output_path: str, rnd: int) -> dict:
    """Turn inline base64 `images` on each submitted item (review `sections`,
    Q&A `answers`, and their `comments[]`) into written files under
    `<output dir>/attachments/`, named `{prefix}-{safeId}-{i}.{ext}`
    (`r{rnd}` for review/comments, `qa` for Q&A answers).

    Invalid MIME, oversized, or undecodable images are dropped silently;
    `images` is always removed. Mutates and returns `data`."""
    attach_dir = Path(output_path).parent / "attachments"
    # Tag each item with its filename prefix by the list it came from, so Q&A
    # attachments are never mislabeled with a nonexistent `r0-` round.
    items = ([("r%d" % rnd, s) for s in data.get("sections", [])]
             + [("qa", a) for a in data.get("answers", [])])
    for prefix, item in items:
        if not isinstance(item, dict):
            continue
        # Section/question ids are sequential (s1, q1, …), so sanitized names do
        # not collide; the sub() only neutralizes path separators in the id.
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(item.get("id", "x"))) or "x"
        _write_item_images(item, prefix, safe_id, attach_dir)
        for cmt in item.get("comments", []) or []:
            if not isinstance(cmt, dict):
                continue
            safe_cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(cmt.get("cid", "x"))) or "x"
            _write_item_images(cmt, prefix, safe_cid, attach_dir)
    return data


def annotate_qa_acceptance(data: dict, questions: list) -> dict:
    """Record whether each answer's `choice` matched its question's
    `recommended_choice` (#175's accept-rate instrumentation). Server-side
    so a client can't forge `recommended_choice`; `questions` is read from
    the round's own recorded `QAInput.questions`, never the client's post.

    Additive: sets `accepted_recommendation` only where a
    `recommended_choice` exists. Mutates and returns `data`."""
    recommended = {
        q.get("id"): q.get("recommended_choice")
        for q in questions
        if isinstance(q, dict) and "recommended_choice" in q
    }
    if not recommended:
        return data
    for a in data.get("answers", []):
        if not isinstance(a, dict) or a.get("id") not in recommended:
            continue
        a["accepted_recommendation"] = a.get("choice") == recommended[a["id"]]
    return data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:
        pass  # silence access log

    def _check_host(self) -> bool:
        """Loopback-only `Host` header guard for every GET route. POST already
        refuses a non-loopback `Origin`, but GET carries no `Origin` — without
        this, DNS-rebinding to 127.0.0.1 lets an attacker page read `/input`,
        the ledger, and preferences as a same-origin request. `Host` still
        names the browser's address-bar domain, which rebinding can't forge.
        Sends the 403 itself and returns False on rejection."""
        host = urlparse("//" + self.headers.get("Host", "")).hostname
        if host not in ("127.0.0.1", "localhost"):
            self._error(403, "forbidden host")
            return False
        return True

    def do_GET(self) -> None:
        if not self._check_host():
            return
        path = urlparse(self.path).path
        if path in ("/", ""):
            self._send(200, "text/html; charset=utf-8", _HTML_BYTES)
        elif path in _VENDOR_ROUTES:
            # Exact-match dict lookup, not a filesystem join on request data:
            # `filename` is a literal from `_VENDOR_ASSETS`, so `/vendor/../…`
            # simply misses the table and 404s below. Read per request rather
            # than at import to keep startup free of unneeded I/O.
            filename, ctype = _VENDOR_ROUTES[path]
            try:
                body = (_VENDOR_DIR / filename).read_bytes()
            except OSError:
                # A truncated install: 404 rather than a traceback, so the
                # page's md-raw/d2h-pending fallbacks take over.
                self._error(404, "vendor asset missing: " + filename)
                return
            # The version is in the path, so these bytes are immutable for
            # this URL — an upgrade moves the URL rather than changing it.
            self._send(200, ctype, body,
                       cache_control="public, max-age=31536000, immutable")
        elif path == "/input":
            # Snapshot both under the lock, then do `_revision_counts`' disk
            # reads outside it. `_input_data` is rebound not mutated, so a
            # bare reference suffices; `_ledger` is appended in place, so it
            # needs a real copy to avoid racing the `json.dumps` below.
            with _data_lock:
                data_snapshot = _input_data
                ledger_snapshot = list(_ledger)
            body = json.dumps({**_with_revision_counts(data_snapshot, _viva_dir),
                               "ledger": ledger_snapshot,
                               "repo": _viva_dir.parent.name}).encode()
            self._send(200, "application/json", body)
        elif path == "/preferences":
            # Every preference, every status, label-sorted — the in-page
            # panel's read (#142). Missing/corrupt store degrades to an
            # empty list (see _load_preferences_store).
            store = _load_preferences_store(_viva_dir)
            body = json.dumps(preferences.select(store, "all")).encode()
            self._send(200, "application/json", body)
        elif path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                with _clients_lock:
                    _sse_clients.append(self.wfile)
                _shutdown.wait()
            except Exception:
                pass
            finally:
                with _clients_lock:
                    try:
                        _sse_clients.remove(self.wfile)
                    except ValueError:
                        pass
        elif path == "/favicon.ico":
            # Purely so a browser's automatic favicon.ico probe doesn't
            # 404-log-spam; the actual tab icon is the inline data: URI
            # `<link rel="icon">` in HTML's <head>, never this route.
            self.send_response(204)
            self.end_headers()
        else:
            self._error(404, "not found")

    def _check_origin_and_length(self, cap: int) -> int | None:
        """Shared loopback-only guard for every POST endpoint: reject a
        present, non-loopback `Origin` (403, CSRF defense-in-depth) and cap
        `Content-Length` at `cap` (400 if not an integer, 413 if over).
        Sends the error itself; returns None on rejection, else the length."""
        origin = self.headers.get("Origin", "")
        if origin:
            # Exact host, never a prefix: `http://127.0.0.1.attacker.tld` is
            # an ordinary A record whose Origin starts with `http://127.0.0.1`,
            # so a prefix test would admit an attacker page to every write sink.
            o = urlparse(origin)
            if o.scheme != "http" or o.hostname not in ("127.0.0.1", "localhost"):
                self._error(403, "forbidden origin")
                return None
        # A cross-origin `fetch` with `Content-Type: text/plain` is a
        # *simple* request: no preflight, so requiring JSON here forces a
        # preflight this server never answers — what actually stops the send.
        ctype = self.headers.get("Content-Type", "")
        if ctype and not ctype.split(";")[0].strip().lower() == "application/json":
            self._error(415, "expected Content-Type: application/json")
            return None
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._error(400, "invalid Content-Length")
            return None
        if length > cap:
            self._error(413, "payload too large")
            return None
        return length

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path   = parsed.path
        if path == "/submit":
            self._post_submit()
        elif path == "/next-round":
            self._post_next_round()
        elif path == "/complete":
            self._post_complete()
        elif path == "/abandon":
            self._post_abandon()
        elif path == "/preferences/mute":
            self._post_preferences_mute()
        else:
            self._error(404, "not found")

    def _post_submit(self) -> None:
        global _last_verdicts
        length = self._check_origin_and_length(MAX_SUBMIT_BYTES)
        if length is None:
            return

        body = self.rfile.read(length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._error(400, "invalid json")
            return
        if not isinstance(data, dict):
            self._error(400, "body must be a JSON object")
            return

        # Validate review verdicts at the boundary. Q&A submits an `answers`
        # payload with no `sections`, so it is gated out (shape, not mode).
        if "sections" in data:
            try:
                schema.validate_verdicts(data)
            except ValueError as e:
                self._error(400, f"invalid verdicts: {e}")
                return

        with _data_lock:
            out = _output_path
            # Snapshot for /complete's finish guard, under the same lock
            # that guards `_input_data` so the two describe the same round.
            if "sections" in data:
                _last_verdicts = data
            titles = {s.get("id"): s.get("title", "")
                      for s in _input_data.get("sections", [])}
            # Snapshotted under the same lock as `titles`: recommendations
            # are read off the round on record, never the client's post (#175).
            questions_snapshot = _input_data.get("questions", [])
            try:
                rnd = int(data.get("round", _input_data.get("round", 0)))
            except (TypeError, ValueError):
                rnd = 0
            for s in data.get("sections", []):
                entry = schema.verdict_to_ledger_entry(
                    rnd, titles.get(s.get("id"), s.get("id", "?")), s)
                if entry is not None:
                    _ledger.append(entry)
        data = extract_attachments(data, out, rnd)
        if "answers" in data:
            data = annotate_qa_acceptance(data, questions_snapshot)
        try:
            write_output(out, data)
        except (IOError, OSError) as e:
            self._error(500, f"write failed: {e}")
            return

        self._send(200, "application/json", b'{"ok":true}')
        _push_sse("processing", {})

    def _post_next_round(self) -> None:
        global _input_data, _output_path, _last_verdicts
        length = self._check_origin_and_length(MAX_SUBMIT_BYTES)
        if length is None:
            return
        body = self.rfile.read(length)
        try:
            new_data = json.loads(body)
        except json.JSONDecodeError:
            self._error(400, "invalid json")
            return
        if not isinstance(new_data, dict):
            self._error(400, "body must be a JSON object")
            return
        # `output` travels in the JSON body; the legacy `?output=` query
        # param is gone (#103). ORDER IS LOAD-BEARING: the missing-`output`
        # refusal stays AHEAD of shape validation — `test_server_api.py`
        # pins that error text on a structurally valid round.
        output = new_data.pop("output", None)
        if not output:
            self._error(400, "missing 'output' in body")
            return
        # `output` is a write path /submit will later use — contain it to
        # `_output_root`, the operator's `--output` directory (deliberately
        # not hardcoded to `_viva_dir`; headless-contract.md §4 documents
        # `--output` as legitimately living outside `.viva/`).
        resolved = Path(output).resolve()
        try:
            resolved.relative_to(_output_root)
        except ValueError:
            self._error(400, "'output' must resolve inside %s" % _output_root)
            return
        # EVERY body, not only one that happens to carry `sections` — a
        # shape gate here once let a mis-nested round through as
        # `{"ok":true}` and bricked the tab with no error either side.
        # `/next-round` is review-shaped only; a `questions`-shaped body is
        # refused too, since the `round` SSE handler always assumes review shape.
        try:
            schema.validate_review_input(new_data)
        except ValueError as e:
            self._error(400, f"invalid review-input: {e}")
            return
        # The launch mode gates which round shape may replace the served
        # one (#126): the browser stamps diff styling only at boot, so a
        # diff payload on a non-diff server would render raw fenced code —
        # refused rather than re-stamped. Absent `mode` reads as "review".
        incoming = new_data.get("mode", "review")
        allowed = "diff" if _launch_mode == "diff" else "review"
        if incoming != allowed:
            self._error(400, "round mode %r does not match the server's "
                             "launch mode (--mode %s, which serves %r "
                             "rounds) — the browser's view is fixed at "
                             "boot and cannot be re-stamped from a round "
                             "push" % (incoming, _launch_mode, allowed))
            return
        with _data_lock:
            # Unified Q&A → review session (#109): the wire payload carries
            # no distinguishing field by design, so the hand-off is inferred
            # here, never persisted — prior round was Q&A-shaped
            # (`questions`), this one is review-shaped (`sections`).
            handoff = "questions" in _input_data and "sections" in new_data
            # Normalize on the way in, as at startup: an absent `round`
            # renders as "REV undefined" and breaks the freshness test.
            _input_data = schema.default_round(new_data)
            _output_path = str(resolved)
            # The verdict snapshot belongs to the round that produced it.
            # Section ids are stable across rounds (s1…sN), so a carried
            # all-approved snapshot would sign off a round nobody has seen.
            _last_verdicts = None
            ledger_snapshot = list(_ledger)
        if handoff:
            # Distinct from the per-mode startup line so a terminal-watching
            # caller (or a human tailing stdout) can see the hand-off happen,
            # not just infer it from the browser reflowing.
            print(f"viva · hand-off qa → review · {_url}", flush=True)
        self._send(200, "application/json", b'{"ok":true}')
        _push_sse("round", {**_with_revision_counts(new_data, _viva_dir),
                            "ledger": ledger_snapshot,
                            "repo": _viva_dir.parent.name})

    def _post_complete(self) -> None:
        length = self._check_origin_and_length(MAX_SUBMIT_BYTES)
        if length is None:
            return
        body = self.rfile.read(length) if length else b'{}'
        try:
            summary = json.loads(body) if body.strip() else {}
        except json.JSONDecodeError:
            summary = {}
        if not isinstance(summary, dict):
            # `[]` and `3` parse; `.get` on either below would 500.
            summary = {}
        # The finish guard: "nothing is auto-accepted" is a hard product
        # line, so the server refuses on its own too, asking
        # `schema.round_is_complete`, the predicate both processes share.
        with _data_lock:
            round_input = _input_data
            submitted   = _last_verdicts
        # Q&A is exempt by shape (`questions`, never `sections`). Diff mode
        # is NOT exempt by mode any more (#177): a blanket exemption let a
        # `--mode diff` server accept ANY verdicts, reopening for hunks the
        # hole #102 closed for sections. The caller now must say WHY via
        # `resolved: "empty"`, honored only when `_launch_mode == "diff"`
        # (fixed at startup, unforgeable by the request body).
        if "sections" in round_input:
            if submitted is None:
                self._error(400, "no verdicts submitted for this round — "
                                 "nothing to complete")
                return
            resolved = summary.get("resolved")
            if resolved is not None and resolved != "empty":
                self._error(400, "unknown 'resolved' value %r — the only "
                                 "signal is \"empty\", and only a --mode "
                                 "diff server honors it" % (resolved,))
                return
            if resolved is not None and _launch_mode != "diff":
                self._error(400, "'resolved' is a diff-review signal — a "
                                 "%s session cannot resolve empty; every "
                                 "section must be approved" % _launch_mode)
                return
            resolved_empty = resolved == "empty" and _launch_mode == "diff"
            if not resolved_empty and not schema.round_is_complete(
                    round_input, submitted):
                # `round_is_complete` above is the gate; this only builds the
                # message. A round's `pass` ADDS a conjunct to the
                # all-approved base, so "0 of N not approved" is reachable
                # too — point the caller at the conjunct that held it instead.
                by_id = {s.get("id"): s
                         for s in submitted.get("sections", [])}
                sections = round_input.get("sections", [])
                pending = sum(
                    1 for s in sections
                    if (by_id.get(s.get("id")) or {}).get("verdict")
                    != "approved")
                spec = round_input.get("pass")
                kind = spec.get("kind") if isinstance(spec, dict) else None
                if not sections:
                    # Checked before the pass branch, so an empty round
                    # isn't blamed on an `architecture`/`line` pass that adds
                    # no conjunct.
                    why = "the round carries no sections to approve"
                elif pending:
                    why = ("%d of %d section(s) not approved"
                           % (pending, len(sections)))
                elif kind:
                    # Recovery is the next round, not a disk merge into this
                    # one — this served round is replaced only by /next-round.
                    why = ("every section is approved, but the %s pass is "
                           "not satisfied — a checks round holds until "
                           "every check flag carries a result, a final round "
                           "until no suggested edit is unresolved. Answer "
                           "the flags in the next round and POST it to "
                           "/next-round" % kind)
                else:
                    why = "the round carries no sections to approve"
                self._error(409, "refusing to complete: %s. Nothing is "
                                 "auto-accepted; re-present the round or "
                                 "abandon it." % why)
                return
        self._send(200, "application/json", b'{"ok":true}')
        _push_sse("complete", summary)
        threading.Timer(2.0, _shutdown.set).start()

    def _post_abandon(self) -> None:
        # The shutdown route with no sign-off meaning: `loop.py abandon` runs
        # in a different, detached process, so it reaches the server over
        # HTTP, not by signal. Deliberately *not* /complete: no `complete`
        # SSE event and no 2-second grace — the browser's `es.onerror` on
        # shutdown is the honest "connection lost" signal for a dropped session.
        length = self._check_origin_and_length(MAX_SUBMIT_BYTES)
        if length is None:
            return
        if length:
            self.rfile.read(length)  # drain: unread body turns close() into RST
        self._send(200, "application/json", b'{"ok":true}')
        _shutdown.set()

    def _post_preferences_mute(self) -> None:
        # Second, narrow writer of `.viva/preferences.json` (#142) — flips
        # one preference to `muted` via `preferences.set_status()`. Un-muting
        # stays CLI-only (see scripts/preferences.py's docstring).
        length = self._check_origin_and_length(MAX_SUBMIT_BYTES)
        if length is None:
            return
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._error(400, "invalid json")
            return
        if not isinstance(payload, dict):
            self._error(400, "body must be a JSON object")
            return
        pref_id = payload.get("id")
        if not isinstance(pref_id, str) or not pref_id:
            self._error(400, "missing 'id'")
            return
        with _prefs_lock:
            store = _load_preferences_store(_viva_dir)
            try:
                store = preferences.set_status(store, pref_id, "muted")
            except KeyError:
                self._error(404, f"no preference {pref_id!r}")
                return
            try:
                _atomic_write(_viva_dir / "preferences.json",
                             json.dumps(store, indent=2, ensure_ascii=False))
            except (IOError, OSError) as e:
                self._error(500, f"write failed: {e}")
                return
        self._send(200, "application/json", b'{"ok":true}')

    def _send(self, status: int, content_type: str, body: bytes,
              cache_control: str = "") -> None:
        """Send one response. `cache_control` is opt-in (absent by default) —
        only the version-stamped /vendor routes are safe to cache.

        Every response also carries a fixed CSP (defence in depth; the
        loopback-`Origin` guard is the real write-sink protection).
        `img-src 'self' data:` stops a reviewed doc's `![](http://...)`
        remote image from beaconing on render. `'unsafe-inline'` on
        script/style is required by the page's own inline `<script>`,
        `<style>`, and `style="..."` markup — no build step here to nonce
        it (`PRODUCT.md` principle 6 refuses the npm dependency)."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache_control:
            self.send_header("Cache-Control", cache_control)
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self'; connect-src 'self'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        """Send a standardized JSON error body. Every error response goes through
        here so a client can parse any failure by content type — successes are
        already uniformly `{"ok": true}` / JSON."""
        self._send(status, "application/json",
                   json.dumps({"error": message}).encode())


if __name__ == "__main__":
    args = parse_args()
    # SIGTERM joins SIGINT on one handler: Ctrl-C is the human's exit,
    # `proc.terminate()` the headless parent's (#125). Unhandled, it would
    # skip the `finally` below and leak `server.url` into the next launch.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: _shutdown.set())
    _viva_dir = Path(args.input).resolve().parent
    # Resolve the preferences store path and stamp it into _HTML_BYTES.
    _PREFS_STORE_PATH = str(_viva_dir / "preferences.json")
    _PREFS_STORE_PATH_JS = _PREFS_STORE_PATH.replace("\\", "\\\\").replace("'", "\\'")
    _HTML_BYTES = HTML.replace("__PREFS_STORE_PATH__", _PREFS_STORE_PATH_JS).encode()
    _input_data = load_input(args.input)
    # Validate the input on read, keyed on the LAUNCH MODE — same reason as
    # `/complete`'s guard: keying on shape let a file with neither `sections`
    # nor `questions` through unvalidated. A shape/mode mismatch now exits 1
    # at launch instead of booting a tab that can't render.
    if args.mode == "qa":
        try:
            schema.validate_qa_input(_input_data)
        except ValueError as e:
            sys.exit(f"viva: invalid qa-input {args.input}: {e}")
    else:
        try:
            schema.validate_review_input(_input_data)
        except ValueError as e:
            sys.exit(f"viva: invalid review-input {args.input}: {e}")
        # Validated, then normalized — in that order, so a malformed `round`
        # still fails loudly here rather than being quietly replaced by 1.
        schema.default_round(_input_data)
    _output_path = args.output
    _output_root = Path(args.output).resolve().parent
    _launch_mode = args.mode

    port = find_free_port()
    server = ThreadedHTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 0.5
    url = f"http://127.0.0.1:{port}"
    _url = url

    url_file = Path(args.output).parent / "server.url"
    _atomic_write(url_file, url)
    print(f"viva · {args.mode} mode · {url}", flush=True)

    if not args.no_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    try:
        while not _shutdown.is_set():
            server.handle_request()
    finally:
        url_file.unlink(missing_ok=True)
        server.server_close()
        print("viva · done", flush=True)
