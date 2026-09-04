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
from urllib.parse import parse_qs, urlparse

# The sibling `scripts/` dir holds the shared schema contract (section_key, the
# ledger rule, boundary validation). It sits beside server.py in both the repo
# and the installed plugin cache (`~/.claude/plugins/cache/**/viva/{server.py,scripts/}`).
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import schema  # noqa: E402
import preferences  # noqa: E402

# Absolute path to preferences.py, resolved from this file's own on-disk
# location — never the shell variable $VIVA_DIR: SKILL.md computes that with
# a local `find` inside its own bash block (viva SKILL.md, Invocation)
# and never exports it, so a copy-pasted "$VIVA_DIR/..." command fails with
# "No such file" in a fresh terminal. Same resolution style as the sys.path
# insert above. Escaped for embedding inside the JS single-quoted string
# literal it's substituted into below — a path containing `'` or `\` would
# otherwise terminate that string early and blank the whole panel.
_PREFS_SCRIPT_PATH = str(Path(__file__).resolve().parent / "scripts" / "preferences.py")
_PREFS_SCRIPT_PATH_JS = _PREFS_SCRIPT_PATH.replace("\\", "\\\\").replace("'", "\\'")
# Store path is set once at startup from _viva_dir; a placeholder is replaced
# after _viva_dir lands, mirroring the pattern for _PREFS_SCRIPT_PATH above.
_PREFS_STORE_PATH: str = ""

# Third-party browser assets, vendored under `assets/vendor/` and served from
# disk at `/vendor/<file>` (#79, #144). Nothing in the page is fetched from ANY
# remote host any more — not jsdelivr, and (reversing #79's scope decision) not
# Google Fonts either — so a review works offline and the supply chain is ten
# pinned files in-tree rather than a range resolved by a CDN at load time.
#
# Resolved from this file's own location, never the cwd: the server is launched
# from whatever repo is under review, and `assets/` sits beside server.py in
# both the repo and the installed plugin cache — same resolution style as the
# sys.path insert above.
#
# The table maps ROUTE → (filename, content type) by exact match. The filename
# is always a literal from this table and never derived from the request, which
# is what closes path traversal: an unlisted route is a 404 before any path is
# built. Versions are stamped into both the filename and the URL, so the same
# URL can never serve different bytes — that is what makes the `immutable`
# cache header on these responses correct across a viva upgrade.
# The files are read per request, not at startup; see assets/vendor/README.md
# for the pins, licenses, and the bump procedure.
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
# In a viva the candidate submits writing and the examiner SPEAKS. viva already
# has the written defence; this is the examiner's voice, and it is input only.
#
# The grammar lives here rather than in `scripts/schema.py` because the browser
# is its only consumer — nothing outside the page reads a spoken phrase, and
# schema.py is the on-disk contract. It lives here rather than inline in the JS
# for the reason `__CHECK_KINDS__` is injected: one table, checked by a test,
# instead of a hand-kept copy that drifts. `tests/test_voice_grammar.py` pins it
# to `schema.COMMENT_TYPES` and `schema.VERDICTS`, so adding a comment type
# fails until it has something a reviewer can say.
#
# Two classes of verb, and the split is the safety rule:
#
#   carries=False — matches ONLY when the WHOLE normalized utterance is the
#                   phrase. "improve this section" matches nothing; "approve"
#                   matches. These act immediately, because a verb with no text
#                   is a button press: there is nothing a recognizer can
#                   mis-transcribe INTO THE RECORD.
#   carries=True  — matches at the START; the remainder is the reviewer's text.
#                   These never act. They STAGE the text in the comment
#                   composer, where the reviewer reads it and confirms — which
#                   is what keeps PRODUCT.md's "verbatim, not summarized" true
#                   of a ledger built from speech.
#
# `submit` is deliberately an alias for `recap`, not for submitting: ending the
# round is the one action the page already gates behind the recap overlay and a
# confirm click, and a spoken word does not get to skip a gate the mouse can't.
_VOICE_VERBS = (
    # Carrying verbs — one per COMMENT_TYPES value.
    {"act": "comment", "type": "changes", "carries": True,
     "phrases": ("request changes", "changes", "change")},
    {"act": "comment", "type": "info", "carries": True,
     "phrases": ("need info", "question", "info")},
    {"act": "comment", "type": schema.SUGGESTION, "carries": True,
     "phrases": ("suggest wording", "suggest", "replace with")},
    # Bare verbs — whole-utterance only.
    # No "pass": in review vocabulary it is ambiguous in the wrong direction —
    # "I'll pass on this section" means skip, not sign off — and this is the one
    # bare verb that writes a verdict.
    {"act": "approve", "carries": False, "phrases": ("approve", "approved")},
    {"act": "next", "carries": False, "phrases": ("next", "skip", "move on")},
    {"act": "back", "carries": False, "phrases": ("back", "previous", "go back")},
    {"act": "save", "carries": False, "phrases": ("save", "done", "commit")},
    {"act": "cancel", "carries": False, "phrases": ("cancel", "scratch that", "never mind")},
    {"act": "recap", "carries": False, "phrases": ("recap", "submit", "submit all")},
    {"act": "stop", "carries": False, "phrases": ("stop listening", "stop")},
)

# Flattened one-rule-per-phrase and sorted LONGEST PHRASE FIRST, so the browser
# can take the first match and be right: "request changes …" must never be read
# as the verb `changes` carrying the word "request". Sorting here rather than in
# JS keeps the ordering rule in the same file as the table it orders.
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
/* Theme, applied before first paint. This runs synchronously in <head> —
   ahead of the stylesheet and every deferred vendor script — because reading the
   stored choice after the body renders means painting the OS theme first and
   flipping to the reader's, which is the flash the toggle exists to avoid.
   Deliberately dependency-free and inside a try: localStorage throws in a
   private-mode iframe, and a theme preference is never worth a broken page. */
(function () {
  try {
    var t = localStorage.getItem('viva-theme');
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  } catch (e) { /* no storage — follow the system, which is the default */ }
})();
</script>
<style>
/* ─── Faces ──────────────────────────────────────────────────
   Fragment Mono, vendored (assets/vendor/README.md) and served from this
   server's own /vendor route. Nothing in this page reaches a remote host any
   more: a review works offline, and viva's "local and keyless" claim covers
   its typography too — until this, both faces came from Google Fonts, a
   per-session request to Google on every review. (The host is not named here
   on purpose: `test_typography.py` forbids the string outright, so a reinstated
   `<link>` fails rather than shipping.)

   latin + latin-ext only. Google serves a cyrillic-ext subset per style as
   well; both are under 1 KiB and fall below the truncation floor
   `test_server_vendor_assets.py` uses to catch a half-downloaded asset, so
   Cyrillic falls back to the system mono instead, which is the right degrade.

   The unicode-range values are Google's own, kept verbatim so subsetting
   behaves exactly as it did remotely. No `crossorigin` anywhere: same-origin. */
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
/* Catalog: a parts-catalog page — white ground, compact type, every state
   visible and tabular. Light is the primary theme (the ground the design was
   drawn on); dark is the override below.

   INK DISCIPLINE — four parties, one hue each, never shared:
     --touch   catalog yellow  the reviewer's touch ON THE TEXT, and nothing
                               else: anchored spans, applied replacement
                               wording, palette selection. Never a label,
                               never a border, never syntax.
     --acc     cobalt          the reviewer's party: their comments, their
                               suggestions, open judgment, every interactive
                               control.
     --machine teal            the machine's party: passed checks, approved.
     --fact    amber           machine-flagged open facts — a claim missing a
                               source, an unanswered check.
   Red and green appear in exactly one place, the suggestion fence, where diff
   semantics already own them. They are not tokens; see the fence block. */
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
  /* The closed mass in a segmented rule. Not a fifth party ink — settled
     belongs to nobody, so it is a filled neutral rather than a hue, and it
     is the one bar color that carries no meaning beyond "done". */
  --settled:   #e3e4e2;
  /* The edge under an anchored span. Transparent on the white ground, where
     the yellow fill is already the mark; a real edge on charcoal, where
     `--touch` is a 22% wash and a wash alone is a ~5% luminance lift — not
     enough to read as "the reviewer touched this text". */
  --touch-edge: transparent;

  /* Component aliases. Component styles keep using these names, so the
     catalog palette lands without rewriting every rule; the four party inks
     above are the source of truth for anything new. */
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
/* The catalog page after hours: same ink discipline, inverted ground. Each
   party ink is lifted for contrast on charcoal rather than reused — yellow
   at full strength on dark is a highlighter, not a touch.

   The dark palette is written twice, and that duplication is deliberate:
     1. under `prefers-color-scheme: dark` for readers who never touch the
        toggle, scoped `:not([data-theme="light"])` so an explicit light
        choice wins over the OS;
     2. under `[data-theme="dark"]` for readers who picked dark on a
        light-mode machine.
   CSS has no way to name a palette and apply it from two selectors without a
   preprocessor, so instead of a comment asking the next editor to keep them
   in sync, `test_theme_toggle.py` parses both blocks and fails if a single
   value drifts. The invariant is enforced, not requested. */
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

/* Dark, chosen explicitly. Same values as the media block above — kept in
   sync by test_theme_toggle.py, not by hope. `[data-theme]` on <html> beats
   the media query's bare `:root` on specificity in both directions, which is
   what lets the toggle override the OS rather than merely agree with it. */
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
  /* Catalog type: a compact grotesque for everything a human reads, a mono
     for everything the machine says. No display face — a catalog page earns
     its character from density and rules, not from a headline font. */
  font-family: 'Helvetica Neue', Helvetica, Inter, system-ui, sans-serif;
  background: var(--paper);
  color: var(--text);
  min-height: 100vh;
  font-variant-numeric: tabular-nums;
  /* Byte-verbatim: no glyph the source did not contain. Fragment Mono
     substitutes `>=` to a single U+2265 and `->` to U+2192, so a reviewer
     approving a hunk cannot tell the ligature from a real ≥ in the file — on a
     surface whose whole promise is byte-for-byte display. Declared ONCE, on the
     ground, and inherited: a per-surface rule is the one the next mono surface
     forgets. `none` is the complete set (no common, discretionary, historical
     or contextual). Deliberately the high-level `font-variant-*` property and
     not the low-level OpenType-feature one: mixing the two on a single element
     is the only way to put the `tabular-nums` line above at risk. */
  font-variant-ligatures: none;
  -webkit-font-smoothing: antialiased;
}

/* ─── Shell ──────────────────────────────────────────────── */
/* The shell is fluid; the PROSE is what holds a measure. A fixed 700px
   container made every long section a scrolling exercise while the window's
   spare width sat empty — past ~76 characters longer lines hurt reading, so
   the extra width goes to the margin conversation (.card-margin), never to
   wider text. .section-content carries the measure cap itself. */
.shell {
  max-width: 1240px;
  margin: 0 auto;
  padding: 32px clamp(16px, 3vw, 44px) 140px;
}

/* ─── Diff-first layout (mode-diff) ──────────────────────────
   Code diffs want the opposite of the 700px prose column: width, and one
   scroll context. body.mode-diff (stamped by the diff dispatch branch)
   widens the sheet, shell, and bottom bar together and removes .section-content's
   nested 60vh scroll — a hunk with context folding doesn't need the cap an
   arbitrary long document does, and page scroll becomes the only vertical
   scroll. Widening the container (instead of escaping it) is what keeps
   .card-body-inner's overflow:hidden accordion animation untouched — see
   the Rejected Approach note in the diff-first-surface design doc. */
/* ─── Doc-mode page width ─────────────────────────────────────
   The review print is as wide as its three columns and no wider. The 1240px
   shell is right for a single column of cards; for `gutter | prose | margin`
   it is 190px too wide, and that surplus has to land somewhere — as a dead
   band beside the text, or as a margin that grows until it rivals the document
   (both of which it did). Capping the page is what lets the prose hold ~88
   characters AND the margin hold its 300, with nothing over.

   Widen this number to widen the TEXT: the margin is capped, so the prose
   track is what grows. */
.mode-doc .shell, .mode-doc .bottom-inner,
.mode-qa  .shell, .mode-qa  .bottom-inner { max-width: 1054px; }

.mode-diff .shell, .mode-diff .bottom-inner { max-width: min(95vw, 1600px); }
.mode-diff #paper { max-width: min(95vw, 1600px); }
/* …and it drops the PROSE MEASURE, because a diff-mode section holds no prose.
   Every section here is a hunk. Left capped at 72ch, `.section-content` held a
   rendered diff to a reading measure: on a 1282px card the d2h wrapper came
   out 540px and each side-by-side pane 267px, clipping every line mid-word
   while 746px of the card sat empty. The break-out rule above could not save
   it — that rule lifts the child's cap, and the container was the constraint. */
.mode-diff .section-content { max-height: none; overflow-y: visible; max-width: none; }

/* ─── Header ─────────────────────────────────────────────── */
.header {
  margin-bottom: 36px;
}
/* The masthead stays put in the print: file, round, pass and the approved
   count are the reader's bearings, and a 7,000px document scrolled them off
   the top of the page after the first screen. Opaque on the paper ground so
   the prose passes beneath it, and under the overlays (z-index 200). */
.mode-doc .header {
  position: sticky;
  top: 0;
  z-index: 20;
  background: var(--paper);
  padding-top: 12px;
  margin-top: -12px;
}

/* ─── Status bar — the catalog header ────────────────────
   Was a drafting title block: four bordered cells stacking an 8px uppercase
   label over a value, captioned DRAWING / REV / TITLE / SIGNED. That is the
   corner of an engineering drawing, and it survived the ground change as the
   loudest remaining piece of the old metaphor.

   A catalog states its facts on one line and gets out of the way: filename,
   round, progress, reading left to right in one weight, closed by the same
   2px ink rule that closes the page at the bottom bar. The cell markup is
   unchanged — the same ids are filled by the same code — but the cells are
   now inline runs, and each label sits BEFORE its value rather than above it,
   so the bar costs one line instead of four stacked boxes. */
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
   The cover slip on a returned drawing: one jump-link row per section
   attributing what changed at this revision — revised to your note, bare
   revised, flagged & unreviewed, approved & unchanged. Reuses the verdict
   color slots: revised → orange (the rev-tri), flag error → orange, flag
   warn → violet, approved → teal. */
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

/* Theme toggle — sits beside the prefs toggle and wears the same control
   grammar, square per the catalog's shape rule. It states the current mode in
   words rather than showing a sun or a moon, because a glyph makes the reader
   guess which state it names: the one it is in, or the one it switches to. */
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

/* The session controls — learned prefs, voice, theme — as ONE unit.
   `margin-left: auto` used to sit on the theme toggle alone, which right-aligned
   it inside the wrapping stats row. With voice beside it the row no longer fits
   on one line at ordinary widths, and an auto margin on a single item wraps that
   item by itself: the theme control printed on a line of its own under
   everything else. They are one cluster, so they wrap as one. */
.bar-controls {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}
.theme-toggle:hover { color: var(--ink); border-color: var(--ink); }

/* ─── Voice — the oral examination ───────────────────────────
   The toggle wears the theme toggle's grammar and states its mode in words
   for the same reason: a microphone glyph makes the reader guess whether it
   names the state or the action. Cobalt only while listening — speech is the
   reviewer's party, and an idle control is not doing anything yet. */
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

/* The strip is the transcript, and it exists so nothing is ever swallowed in
   silence: every utterance prints here with the reading it got, INCLUDING the
   ones that matched no verb. A reviewer who cannot tell "heard nothing" from
   "heard something and ignored it" cannot trust the layer at all. */
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

/* A section is a run of the print, not a box on it. The card kept a 1px
   border and a filled panel from the era when it was a drawing pinned to a
   sheet; on a catalog page that framing is what made four sections read as
   four objects instead of one document. What separates sections now is a
   hairline above and the heading's own weight — the same thing that separates
   entries in a printed catalog. */
.card {
  position: relative;
  border: none;
  border-top: 1px solid var(--rule);
  background: none;
  transition: opacity 0.35s;
  animation: fadeUp 0.4s ease both;
}
.card:first-child { border-top: none; }

/* The active card's `+` registration marks — which pinned it to the drafting
   table like a sheet — are gone with the rest of the blueprint chrome. The
   active card is marked by its edge and elevation below, not by hanging
   drafting glyphs outside its corners. */

/* The catalog has no sheet. #paper survives as the page's content wrapper —
   the element every layout rule and test already hangs off — but its
   drawing-sheet dress is gone: no edge border, no 7px inner rule, no corner
   registration marks, no A/B/C/D edge coordinates (markup deleted, not
   hidden). Those said "this is a drafting sheet resting on a table"; the
   ground now says "this is a catalog page," which needs no frame to be read. */
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

/* Carried card (round >= 2 prior approval): a dimmed head-only line — kept a
   touch brighter than is-approved so the reveal and withdraw affordances
   stay discoverable — with the mono APPROVED mini-stamp echoing the
   completion stamp motif. */
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
/* Flat dots. The glows were the drafting board's cyan linework lit from
   behind; on paper a status dot is printed, not lit. */
.dot-approved { background: var(--machine); box-shadow: none; }
.dot-changes  { background: var(--acc);     box-shadow: none; }
.dot-info     { background: var(--fact);    box-shadow: none; }
/* Revision triangle — drafting's "this region changed at this rev" flag, keyed
   to the titleblock REV and the revision log. */
.rev-tri { font-family: 'Fragment Mono', monospace; font-size: 11px; font-weight: 600; color: var(--orange); letter-spacing: 0.04em; margin-left: 10px; flex-shrink: 0; align-self: center; }
/* Cumulative revision count (issue #141) — a second run of text inside the
   same .rev-tri element, not a separate badge. Label convention (DESIGN.md):
   8-10px Fragment Mono (inherited from .rev-tri), var(--text3), not the
   triangle's own orange. Decorative text, not interactive — no focus target. */
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

/* The summary, in the HEAD. Scoped override of the base `.section-summary`
   below, which is sized for the doc print's open prose column; here the point
   is the COLLAPSED list — 41 `server.py hunk N` heads with no way to tell the
   segmented rule from the anchor walker (#188). One line, always: the head is
   an index entry, so a long summary ellipsizes rather than growing the row.
   `.card-title-wrap` already carries `min-width: 0`, which is what lets the
   truncation take effect inside the flex head. */
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
/* Prose sections diff as whole paragraphs — one line per paragraph — so lines
   wrap instead of forcing a horizontal scroll that hides the change. Word-level
   marks (.dw, computed in markWordDiff) show what moved inside a paired
   rewrite; the mark tint is a stronger mix of the same verdict slot. */
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
   Rendering is delegated to diff2html (see renderDiffHunk); these rules
   bend its chrome to the blueprint without forking its stylesheet.
   Surface theming rides d2h's own CSS custom properties — the light and
   dark property families both map to viva tokens, which already flip via
   prefers-color-scheme, so one block themes both modes. The ins/del/change
   tints are deliberately left as d2h's own green/red: they encode change
   direction, a different semantic axis than viva's teal/orange verdict
   palette. */
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
/* Structural guards — each carries a lesson this surface taught:
   - td reset: .section-content td (the generic editorial-markdown-table
     rule) would otherwise chop every diff row into bordered, padded cells.
   - unselectable line numbers: a drag can't capture them into
     comment.anchor.text (renderDiffHunk also aria-hides them).
   - position:relative on the wrapper: d2h's line-number cells are
     position:absolute; without a positioned ancestor inside the card,
     their containing block is .card (relative) — outside
     .card-body-inner's overflow:hidden — so they'd escape the accordion's
     collapse clip and ghost over the page (verified via computed-style
     inspection). Auto offsets make this inert while open.
   - 6px radius matches .diff-block, the incumbent documented diff-surface
     radius, replacing d2h's own undocumented 3px. */
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

/* The body is the page continuing under the head, not a panel opening beneath
   it: no fill, no second rule (the head's own hairline already separates the
   entries), and the left edge lines up with the title above it so the row grid
   starts where the reader's eye already is. */
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
/* The prose column. `max-width` is a MEASURE, not a container width: the card
   may be as wide as the window allows, but a line of text stops at ~72
   characters because that is where reading breaks down. Wide content (code,
   tables) escapes it via .section-content > pre below.

   No nested scroll. The old `max-height: 60vh; overflow-y: auto` put a second
   scrollbar inside every long section — the reader scrolled a viewport to
   reach content that was already on screen, and the page scrollbar lied about
   how much was left. The section prints in full; the page is the only scroll. */
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

/* Code and tables are not prose and do not take the prose measure — they take
   the room, and scroll sideways in their own container so the page body never
   does. This is the catalog's break-out rule.

   It only lifts the CHILD's cap. A child cannot be wider than its parent, so
   this rule does nothing on its own while `.section-content` is still capped
   at 72ch — something has to unbind the container too. In review mode that is
   `.doc .section-content` (the row grid owns the measure); in diff mode it is
   `.mode-diff .section-content` below. */
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
/* Sentence case, not uppercase. The old rule shouted every heading in 14px
   caps with tracking — a drafting label applied to prose. A catalog sets a
   heading in the same face as its body, one step up in weight. */
.section-content h1, .section-content h2 {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: none;
  color: var(--ink);
  margin: 18px 0 8px;
}

/* The section's own title is already printed by the card head directly above,
   so the leading heading in the rendered markdown is a duplicate — it read as
   the same words twice, once in sentence case and once shouted. Hide the
   first heading of a section's content and let the head carry it. */
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
/* The code well. Solid rule, not the old dashed one — a dash was a blueprint
   gesture, and a catalog boxes its specifications. The block no longer paints
   itself accent-blue either: that flattened every token to one hue and left
   the syntax theme with nothing to say. */
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
   #185 shipped the catalog's MATERIALS on the old accordion. This is its
   STRUCTURE: rows of `check gutter | prose | margin`, in document order.
   The reader reads the document; the commentary sits beside the passage it
   annotates instead of stacking on top of it.

   TWO CLASSES, TWO IDEAS. `.doc` is the GRAMMAR and is stamped on every
   card container that renders sections — review, diff and Q&A alike, because
   nothing about a hunk or a question resists a margin. `.print` is
   CONTINUOUS PRINT — every section open at once — and is review's alone: 52
   hunks printed together is a worse surface than one at a time, and a
   question at a time is the point of an interview. Anything keyed on
   `.doc-section` is already print-only by class.

   THE WASTED-SPACE RULE. The composite reserves 70px of gutter and 300px
   of margin on every row; production must not. Both side columns are
   variables and both go to 0 when the DOCUMENT has nothing to put in
   them. The decision is per-document, not per-row or per-section, and
   that is the one judgment call in this layout: a per-row decision jogs
   the prose column sideways between paragraphs, a per-section one jogs it
   between sections, and a column of text that moves as you read it is
   worse than the space it saves. The 28px alley rides inside each side
   cell's own padding rather than in `column-gap`, because a gap is drawn
   between zero-width tracks too — this way a collapsed column costs
   exactly zero. */
.doc {
  /* A GLYPH RAIL, not a text column. 70px of 9px mono could not hold a real
     flag — `✓ §4 defines "cold start"` clamped to `✓ §4 defines "cold`, which
     is worse than not showing it, and right-aligned ragged 9px type is barely
     readable even when it does fit. What the gutter is actually for is
     LOCALITY: knowing this paragraph carries a machine flag, and of what
     severity, without the text interrupting the reading. A colored glyph says
     that in 14px; the words go where there is room to read them. */
  --gutter-w: 34px;                    /* 14px glyph + the 20px alley */
  /* The margin is CAPPED at the composite's 300px (+ the 28px alley). Letting
     it absorb the shell's spare width instead put it at 515px against 540px of
     prose — a 51:49 split, where the commentary took as much of the page as the
     document it annotates. The composite runs about 61:39, and it is right:
     the margin is secondary and has to read that way.

     The leftover does not become a dead band, because the page itself is capped
     to its three columns (`.mode-doc .shell`). One consequence worth stating:
     with the margin fixed, every pixel of extra width from here goes to the
     TEXT, not to the notes. */
  --margin-w: minmax(253px, 328px);
}
/* .cards is the flex column the sections sit in; in the continuous print its
   gap is the space between entries, so the sections carry no margin of their
   own. The accordion keeps its own hairline-and-6px separation — a run of
   collapsed heads is a list, and 22px between list items is a gap, not a
   rhythm. */
.doc.print { gap: 22px; }
.doc.no-gutter { --gutter-w: 0px; }
.doc.no-margin { --margin-w: 0px; }
/* The prose track takes whatever the gutter and the margin do not, and the
   PAGE is what holds the measure (`.mode-doc .shell` below) — so the text
   always fills its column, the margin always ends flush, and no `ch` value
   appears in the template. That last part matters: `ch` on a track resolves
   against the row's own font-size, and a `72ch` track was ~99px wider on the
   head row (inheriting the section's size) than on a prose row inside
   `.section-content`, which put the spec table that far right of every note
   below it. `.doc-section` fixes one size for the print regardless. */
.doc .row {
  display: grid;
  grid-template-columns: var(--gutter-w) minmax(0, 1fr) var(--margin-w);
  align-items: start;
}
/* Code and tables are not prose and do not hold the prose measure — the
   catalog's break-out rule, at row scale instead of `.section-content > pre`.
   They take the margin's room ONLY on a row that has no margin cell, which is
   the room actually going spare; a row annotating its own code keeps three
   columns and the code well scrolls sideways in its own container, exactly as
   `.section-content > pre` always did. `:has()` reads the row's own contents,
   so nothing in JS has to remember to set a class when a note is added or
   removed.

   THE PRINT ONLY. There, a wide block is one row among a dozen and borrowing
   the empty column beside it costs the reader nothing. In the accordion the
   wide row IS the section — a hunk — and `:has()` would make the first
   comment on it a 328px re-layout of the very lines being commented on,
   under the cursor. Whether a hunk is inset is the per-document decision
   `--margin-w` already makes, made once for all of them.

   CODE ONLY, not tables. A table reflows — its cells wrap to the measure and
   it keeps the prose's right edge — so a table wider than the text was the
   one wide thing that read as wrong, and it snapped back to the measure the
   moment it took a note. A code block cannot wrap, so it keeps the room. */
.doc.print .row.wide:not(:has(> .rm)):has(> .rp > pre, > .rp > .d2h-wrapper) .rp { grid-column: 2 / 4; }
/* Explicit column placement, so a row that omits an empty side cell still
   prints its prose in the middle track instead of sliding left. */
.doc .rg { grid-column: 1; padding-right: 20px; display: flex; flex-direction: column; align-items: flex-end; gap: 3px; padding-top: 2px; }
.doc .rp { grid-column: 2; min-width: 0; }
.doc .rm { grid-column: 3; padding-left: 28px; min-width: 0; }
/* One type size for the whole print, so `ch` means the same thing in every
   row — the heading and the machine's own faces set their own size on top. */
.doc-section { font-size: 13.5px; }
/* The head row has no margin cell in any state, so it needs no collapsed-margin
   exemption, no reflow rule and no spec cap — three special cases deleted,
   none added. The FOOT band keeps a two-track twin, and only because
   `updateDocColumns` counts `docFlagSplit(s).margin` and `docNotes(s)` but NOT
   `split.gutter`: a section-scope, non-jumping flag whose anchor resolves to no
   row falls back to the foot band and renders its words there through
   `marginFlagHTML`, which on a `no-margin` document would be a 0px track. (The
   COMPOSER needs no such rule — `.rm .comment-popover.is-open` drops
   `no-margin` synchronously before paint; see openCommentPopover.)

   `1fr`, not `72ch` — the last `ch` in a grid template, and the one invariant
   #5 was written against: it resolves against the row's own font-size, so the
   band would come out narrower than every prose row above it. */
.doc.no-margin .row-foot { grid-template-columns: var(--gutter-w) minmax(0, 1fr); }
.doc.no-margin .row-foot .rm { grid-column: 2; padding-left: 0; }
/* Below the composite's own breakpoint the third column has no room to be a
   margin; notes fall under the passage they annotate and the gutter narrows
   to a glyph rail. `.row-foot` needs no term in either list — it is a
   `.doc .row` and its margin cell is a `.doc .rm`, both already covered. Nor
   does the wide-row selector, which was only ever a redundant restatement of
   the same two tracks: a wide row IS a `.doc .row`, and no top-level rule
   re-templates it. Naming it here also put a wide-row template restatement in
   the source, which `assert_catalog_ground` forbids outright — masked until
   now only by whichever selector happened to follow it in the list. */
@media (max-width: 920px) {
  .doc .row { grid-template-columns: 30px minmax(0, 1fr); }
  .doc .rg { padding-right: 8px; }
  .doc .rm { grid-column: 2; padding-left: 0; }
  /* The bar's row breaks here and nowhere wider: at the print's 1054px cap the
     squeeze rule below keeps one line, and under 920px `.stats` can no longer
     shrink enough, so the dispatch controls take a line of their own rather
     than leaving the viewport. */
  .bottom-inner { flex-wrap: wrap; row-gap: 8px; }
  /* Every control the grid reflows for a narrow screen is a touch target. */
  .nt-btn, .theme-toggle, .voice-toggle, .prefs-toggle, .sort-toggle,
  .btn-skip, .btn-submit, .pal-row, .cmt-chip, .cmt-save, .cmt-cancel,
  .attach-btn, .mic-btn, .settle-btn, .thread-reply-btn, .choice-chip { min-height: 44px; }
}

/* ─── The foot band ───────────────────────────────────────────
   The section's DISPOSITION: what is open on it, and what you do about it.
   Horizontal, at the reading measure, under the prose — which is where a parts
   catalog puts an entry's spec block and its order line.

   It was the head row's margin cell: a vertical stack of a five-row state
   table and a button cluster in a 328px column, beside a one-line title. That
   is 101px against 40px on EVERY annotated section, and 400px against 40px
   once unanchored notes joined it — 360px of blank page between a heading and
   its own first paragraph, measured. The margin column earns its position for
   a note that points at a passage sitting beside it; a section's state points
   at the section, has no passage beside it, and was being paid for in white.

   A sibling of `.section-content`, never a child — `docRows` is
   `#rcontent-<id> > .row`, and a row inside it is one `rowForAnchor` can
   return, `docNotesOrdered` can index and `markAndPin` walks. */
.doc .row-foot { margin-top: 18px; }
/* `.doc .row + .row`'s 10px does not reach here — the foot row's previous
   sibling is `.section-content`, not a row — and 10px is a paragraph gap
   anyway. This band closes a section. */

/* The reading measure, taken as a max-width rather than a grid track: `ch`
   resolves against the element's own font-size, which is exactly why no `ch`
   appears in a TEMPLATE (issue #5's lesson) and exactly why it is right here —
   the band measures what the prose above it measures, on whichever surface it
   is on. Without it, diff mode's `min(95vw, 1600px)` shell would put `approve`
   1200px from the state readout. */
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

/* The state, as a run rather than a table: five label/value pairs at 10.5px
   mono is one line at the measure, against the ~120px a five-row table took in
   a 328px column. Separation is `gap`, never generated content — a `::before`
   punctuation mark is announced by some screen readers, and the `.sp-k`/`.sp-v`
   ink pairing already separates a label from its value. */
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
/* At narrow widths the state run leads the left edge under the verbs —
   right-aligning it against a 30px-gutter column strands it. This media query
   sits AFTER `.spec-strip { margin-left: auto }` deliberately: the two rules
   tie on specificity, so source order is the whole mechanism. */
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
/* The prose dims on approval; the band that lets you take it back does not.
   `↺ withdraw approval` is the only way out of an approved section, and this
   fight was already had for carried cards ("so the affordances stay
   discoverable"). 0.72 is the floor at which --ink2 still clears 4.5:1 on
   both grounds — a dimmed section is still one the reviewer may re-read
   before dispatch. Specificity 0,4,0 — it TIES
   the hover rule above, so its position after it is the whole mechanism. */
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
   State × party under an open heading, in honest counts: blue open
   judgment, amber open facts, the settled remainder. The order is FIXED
   (judgment → facts → settled) and that fixed order is the colorblind-safe
   second encoding — position says which is which when hue does not. The
   raw counts ride in the aria-label, so the honest-proportions claim is
   auditable rather than asserted.

   Drawn only where something is open. A settled section keeps the thin
   hairline: a state bar on a section with nothing open is decoration. */
.seg { display: flex; height: 4px; margin: 0 0 10px; }
.seg i { display: block; height: 4px; min-width: 2px; }
.seg-judgment { background: var(--acc); }
.seg-fact     { background: var(--fact); }
.seg-settled  { background: var(--settled); }
.rule-s { border-bottom: 1px solid var(--rule); padding-bottom: 6px; margin-bottom: 10px; }

/* ─── Check gutter ────────────────────────────────────────────
   Producer flags print beside the paragraph they concern, right-aligned
   against the prose column, in the machine's own face. A flag that carries
   an interactive jump (a contradiction's cross-section link, a learned
   preference's badge) does not fit in 70px and is not a glance — those
   route to the margin as notes instead. */
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
   in the conversation, so it takes no border and no actions: a producer flag
   is advisory and there is nothing here to answer. Inked by severity and led
   by the same glyph as the rail, so the pairing reads left to right. */
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
   The wording it replaces struck out in the faint ink, the replacement on the
   same catalog yellow the anchor wears — so the reviewer reads the sentence
   as it would stand, not a note about it. `del` is still the document (it is
   what the source says today); `ins` is the reviewer's proposal and is
   excluded from every text walk, so it can never be counted as prose or
   commented on. The gap between them is margin, never a text node, for the
   same reason. */
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
/* A pin on a diff line. Prose WRAPS, so a pin set after its anchor is always
   on screen; a code line SCROLLS, and a pin appended to one lands at whatever
   character offset that line happens to be long — measured at x=1052 inside a
   445px-wide pane whose content is 1687px, i.e. nowhere the reviewer will ever
   see it. The unit a diff note pairs with is the LINE, so the pin sits at the
   head of it and stays put under horizontal scroll. Both ends of the pairing
   stay visible, which is the whole point of numbering them. */
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

/* The round-to-round diff, collapsed, in the head row's prose cell — where it
   costs one mono line and fills the space the spec table opens beside the
   heading (measured at 88px of dead prose column before it moved here). It
   ships collapsed because "what changed since last round" is not what the
   reader opened the document to read, and it expands at the reading measure
   rather than at full width: a prose diff wraps, and a diff wide enough to
   need its own row was the thing standing between the reader and the text. */
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
/* The whole-document invitation, printed once ABOVE the print — it is the
   only line that teaches the core gesture, and at the foot of an eight-section
   document nobody met it until they had read everything. It shares a row with
   the sort toggle so neither sits alone in a band of its own. Label ink
   (--soft), never the settled ink: this is live instruction. */
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

/* The document's own balance, drawn across the footer that closes the page —
   same grammar as a section's rule, same fixed order, one document-wide
   denominator. Unreviewed sections are the bare track it does not fill.

   Heavier than a section's rule (6px against 4px), and its settled segment is
   INK rather than the section rule's gray: at document scale "done" is the
   page's closed mass, drawn in the same ink as the 2px rules that bracket the
   page top and bottom. The gray belongs to one section's remainder. */
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
   Selectable controls (verdict actions, Q&A chips + buttons) used to wear
   drafting crop-ticks — four corner arms painted as eight gradients, with no
   edge between them. A catalog draws the whole rule: these are full 1px
   borders now, square, on the page's own ground.

   The --c state machine survives the change untouched. Every state rule below
   still just reassigns --c, so `.sel-approve`, `.sel-changes`, hover, and the
   comment-chip states all recolor exactly as before — the property now feeds
   a border instead of a gradient stack. Registering --c keeps the recolor
   animatable; without @property support it snaps. */
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
/* The composer's second secondary control — same grammar as attach, same
   `--c` edge, because it does the same kind of job: it fills the box beside
   it and never decides anything. Cobalt while live: speech is the reviewer's
   party, and a control that is currently doing something is the one place the
   accent belongs. */
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
   anchor now, on every surface, so the stacked `.cmt` rows under a card have
   no host left. `.cmt-del` survives as a WIRING HOOK only — its old rule
   (`border: none; font-size: 14px`) sat later in the sheet than `.nt-btn` and
   quietly stripped the margin's `remove` verb of the box every other verb
   wears.

   The wording a suggestion carries, in a carried thread's exchange. Its own
   line under the note, accent-inked, arrow-led like `.exchange-a`'s reply. */
.cmt-repl { display: block; margin-top: 3px; color: var(--accent); overflow-wrap: anywhere; }
.cmt-repl::before { content: '→ '; }

/* ─── Q&A choices (chip style) ────────────────────────────
   The choices ARE the question's body, so they print in the prose column
   under its heading — no `Choices` label above them, because a row of
   pickable chips under a question needs no caption to be read as the answer.
   Each carries the digit that picks it, the way every other control in this
   ground carries its own keycap.

   ONE PER LINE. Wrapped into a ragged row they read as a grid — the eye has
   to find where each option ends — and the digit that picks an option lands
   somewhere different on every row. Stacked, the labels start on one left
   edge and the keycaps end on one right edge, which is exactly the shape
   `.pal-row` already uses for the same job: a list of things you pick, each
   with the key that picks it.

   BOUNDED to 328px — `--margin-w`'s maximum, the width every other pick-list
   -shaped object here takes. `.pal-row` is a ~500px palette holding
   sentence-length labels; stretching that shape across a 606px measure to hold
   `per-request` stranded the digit keycap ~550px from its label.
   `align-items: flex-start` would keep the digit near its label by making the
   chips ragged — trading away the property this comment argues for. Bound it,
   don't un-stretch it. */
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

/* Recommended-choice badge (issue #114) — advisory only: the chip it
   decorates is never pre-selected, focus-defaulted, or otherwise styled as
   the primary action, so the human still picks freely. Reuses the same
   teal token .vbadge-approved/.annot-info already use, per "prefer reuse
   over creation" rather than inventing a new badge color. */
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

/* Grounds-classed recommendation badges (issue #175) — extend .chip-badge's
   shape rather than inventing a second visual language. `.chip-badge-sourced`
   keeps the same teal (a recommendation with a citation is still a machine
   fact, just an attributed one). `.chip-badge-taste` reuses the accent token
   the palette already comments "`changes` is reviewer judgment" — a taste
   call is exactly that, the reviewer's own. It decorates the question's
   choices rather than a chip (no `recommended_choice` ever accompanies
   `taste`), so it needs its own line above them. `inferred` gets no ambient
   color here — see .chip-reveal below, the disclosure it renders behind
   instead. */
.chip-badge-sourced { background: var(--teal-bg); color: var(--teal); }
.chip-badge-taste   { display: block; width: fit-content; margin-bottom: 6px;
                       background: var(--orange-bg); color: var(--orange); }

/* An inferred recommendation answers only on request (issue #175) — a
   best-practice opinion with no local provenance earns a click, not a
   glance. Reuses the native <details>/<summary> disclosure .kbd-legend
   already establishes, rather than a bespoke expand/collapse widget. */
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

/* The Q&A action row is gone with every other action row: confirm and skip
   are margin verbs in the `.nt-btn` grammar, beside the note they act on.

   ─── The margin's compose block ───────────────────────────
   The reviewer's optional context on a question, inside the same bordered
   note the margin gives every other piece of commentary — field and
   attachments within it, not stacked under it, so an untouched question still
   reads as one object rather than three. Smaller than the popover's field: a
   300px margin is not a card body. */
.nt-compose .note-field {
  min-height: 58px;
  margin-top: 3px;
  font-size: 12px;
  padding: 6px 8px;
}
.nt-compose .attach-btn { margin-top: 6px; }
.nt-compose .thumb-strip { margin-top: 6px; }
/* The disclosure head IS the question — printed once, wrapping, numbered like
   a catalog entry. It was clamped to one line and dimmed once open, as an
   index line pointing at the `<h2>` below it; that mitigation assumed the entry
   held something other than the same sentence, which a free-text question does
   not. With the duplicate gone the head has to carry the whole question, so the
   clamp goes with it — ellipsizing a question now printed nowhere else leaves
   it readable nowhere. */
#qa-cards .card-title { white-space: normal; }

/* A free-text question has nothing to put in the prose column, and a 606px
   empty track beside a 328px compose note is the same wasted-space failure
   `.doc.no-margin` exists to answer. Decided as a constant, because whether a
   question has choices cannot change mid-session. */
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

/* The two dispatch controls are one unit and they do not shrink: a flex item's
   default `flex-shrink: 1` let `skip rest & submit` wrap onto three lines at a
   780px viewport (measured). `.stats` above already wraps and absorbs the width
   instead. The content columns reflow well and are deliberately untouched.
   `display: flex` stays in the stylesheet — the SSE `round` handler restores
   this group with `style.display = ''` and falls back to exactly this rule. */
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
/* The not-ready dispatch takes `.btn-skip`'s quiet outline grammar. It kept
   the primary's filled shape and painted it `--border2` (an alias of --ink)
   on `--text3` (an alias of --faint, DESIGN.md's "settled, disabled" ink) —
   which made the one control that cannot act the highest-contrast block on
   the page, in both themes. The affordance was inverted; the fix is the
   shape, not the token. It carries `aria-disabled` from updateReviewStats /
   updateQAStats; the DOM `disabled` attribute stays reserved for in-flight. */
.btn-submit.disabled {
  background: transparent;
  color: var(--soft);
  border-color: var(--rule);
  cursor: not-allowed;
}

/* ─── Recap overlay (submit gate — review/diff modes) ──────
   The pre-flight index over every section: id, title, verdict dot + label,
   active-note count. btn-submit's ready click opens this instead of
   submitting; only #recap-confirm calls submitReview(false). Reuses the
   card dot slots; row typography matches the transmittal slip. */
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
/* Three children, not one: the blocked state on the left, the two controls on
   the right. The row held only `confirm & submit`, so a recap opened with
   sections still pending offered exactly one control and that control could
   not act. */
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
   A second modal, independent of the recap overlay but built on the exact
   same shape (role="dialog", inert background, focus trap): at most one of
   the two is ever open at a time. Row typography borrows the recap row's
   mono-label pairing; the mute control and muted-row note are new. */
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
   The tab outliving its server used to be a fully interactive page whose
   every submit POSTed into a socket that was gone. Same materials as the two
   dialogs above — scrim, paper, square border, one lift — but this is the one
   modal with no close control and no Escape: dismissing it hands the reviewer
   back the dead tab. Orange, because "the connection dropped" is the weight
   the connection-lost banner already carried here.
   z-index clears the palette's 1200 (the skip link's 2000 is inside the inert
   set, so it can't surface through this). */
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
   (alive, not busy — the spinner is gone) over the reviewer's own
   just-submitted changes/info requests, echoed verbatim. Zero rows (a qa
   submit, a round with no feedback) fall back to the minimal line. Row
   typography matches the transmittal slip; type colors reuse the verdict
   slots (changes → orange, info → violet). */
@keyframes viva-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.25; }
}

/* The interlude is the same PAGE, waiting — not a splash screen with the
   document taken away. It keeps the page's left edge and its measure, and the
   reviewer's just-submitted requests print as what they are: the notes they
   wrote a moment ago, in the note grammar the margin wrote them in. That is
   also why `.pr-*` is gone — a second row grammar for the same objects. */
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
   highlight.js has been loaded and applied to language-tagged blocks since
   diff mode shipped, but no theme ever came with it — every token rendered
   at body color, so `hljs.highlightElement` was doing invisible work. This
   is that theme, written inside the ink discipline rather than lifted from
   a highlight.js preset, because a stock theme would spend the reviewer's
   own colors on syntax.

   The rules the palette obeys:
     - NO catalog yellow. Yellow means the reviewer touched that text; a
       string literal is not a reviewer's mark.
     - NO red or green. Those belong to the suggestion fence alone.
     - Comments recede (they are the least of what the code says), keywords
       carry ink weight rather than hue, and the two hues that do appear are
       the machine's own teal and amber — code is the machine's voice.
   The result is close to monochrome on purpose: on a catalog page the code
   is a specification, and a specification is not a rainbow. */
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
        <!-- Review-mode cells (#186): the composite's bar states the document's
             whole condition on one line — checks, items, what is open, and the
             way in to the keyboard layer. They ship hidden; initReview reveals
             them and updateReviewStats keeps them current. -->
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
    <!-- "flags", not "checks": the rows are every DOC_SCOPE_KINDS flag, and
         only `headings-present` is also a CHECK_KIND — a `checklist` row is not
         a check. The visible head already says "Document · N flags", so naming
         it checks here gave a screen reader a name the sighted head contradicts.
         The checks TALLY still rides in that head, where it is one part of the
         line rather than the name of the whole thing. -->
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
       The interview step of /viva-write, and a first-class surface: the same
       composite bar review carries, stating the round's whole condition on one
       line. No progress track — the footer's segmented rule is the progress,
       in state rather than in percent, and two bars saying the same thing
       differently is one bar too many. -->
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
    <!-- Every aggregate the bar and the footer print, defined once, in the
         page, in the reader's reach. A reviewer who cannot reproduce the
         arithmetic stops trusting the numbers — on the one surface whose
         whole job is to be trustworthy state. `title` on the two cells is a
         mouse convenience; this list is what states them. -->
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
     off; the balance bar prints just inside the 2px ink rule that closes
     the page. Ships empty and hidden — updateReviewStats fills it in
     review mode only, since a diff or Q&A round has no document balance. -->
<div class="bottom-bar" id="bottom-bar-el">
  <div class="foot-seg" id="foot-seg" style="display:none"></div>
  <!-- The voice transcript. Its own aria-live region rather than a cell inside
       #stats-area's: the counters announce a count, this announces a sentence,
       and one region cannot pace both. Ships hidden — nothing about this page
       changes until a reviewer turns the microphone on. -->
  <div class="voice-strip" id="voice-strip" aria-live="polite" style="display:none"></div>
  <div class="sr-only" id="sr-status" role="status" aria-live="polite"></div>
  <div class="bottom-inner">
    <div class="stats" id="stats-area">
      <!-- The live region is the COUNTERS, not the whole bar: the three
           toggles beside them rewrite their own labels, and inside the region
           every repaint re-read them on top of the button's own name change. -->
      <span class="stat-run" id="stat-run" aria-live="polite">
      <span class="stat-pending"  id="stat-pending"></span>
      <!-- Review-mode footer (#186). `convergence` is the question a
           multi-round review actually asks — is the reviewer closing more than
           they open — and `round trip` is a real measurement, not a claim:
           the last same-origin request this page made. -->
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

<!-- Preferences panel (#142) — view/mute learned preferences without leaving
     the tab. Ships hidden, empty; renderPrefsList() fills it from the
     preferences fetched once at boot. Reachable in every mode (review, diff,
     qa) since it lives in the one shared bottom bar. At most one of this and
     the recap overlay is ever open at a time. -->
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

<!-- Dead-session overlay (#174) — the SSE connection dropping is the honest
     signal that the server behind this tab is gone (see es.onerror). Ships
     hidden; showDeadSession() reveals it and only es.onopen — a connection
     that actually came back — takes it down again. `alertdialog`, not
     `dialog`: it interrupts rather than offering a choice, and it carries no
     close control because there is nothing on the other side of it. The
     resume line ships hidden too: only a review-mode payload names a target
     `/viva-review` would take. -->
<div class="dead-overlay" id="dead-overlay" role="alertdialog" aria-modal="true"
     aria-labelledby="dead-title" aria-describedby="dead-body" style="display:none">
  <div class="dead-panel" id="dead-panel" tabindex="-1">
    <h2 class="dead-title" id="dead-title">Session ended</h2>
    <p class="dead-body" id="dead-body">This tab lost its review server. Nothing submitted from here can reach it &mdash; resume from the terminal.</p>
    <p class="dead-resume" id="dead-resume" style="display:none">resume: <code id="dead-cmd"></code></p>
  </div>
</div>

<!-- Command palette (⌘K, issue #186) — E's keyboard layer. A directory of
     verbs the page already carries as controls and keycaps, never a second
     interaction model. Ships hidden and empty; openPalette() fills the list
     from live state each open. -->
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

// `.rev-tri`'s title (tooltip) text. `revision_count_partial` (server.py's
// `_with_revision_counts`) means a historical round file this session
// couldn't be read, so any count is a lower bound, not exact — say "≥N",
// never assert N as fact. It rides on every section with a `diff` this
// round (the same predicate that gates the triangle itself just below),
// not only ones that clear the 2+ threshold: the unreadable round might be
// exactly the one that would have pushed a below-threshold section over
// 2, so a bare `△ NN` with no count and no caveat would silently vanish
// the multiplier instead of merely under-reporting it
// (corrupt-round-file-silent-undercount).
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
// the life of this tab, so it is stashed once where it enters (the /input
// boot fetch, and again off the 'round' SSE payload for a qa→review
// hand-off) rather than threaded through every setTabTitle call site.
let TAB_REPO = null;

function setTabTitle(...parts) {
  document.title = [TAB_REPO, ...parts].filter(Boolean).concat('viva').join(' · ');
}

// The 'processing' SSE handler's own title setter, kept distinct from
// setTabTitle rather than added as a fifth call site to it: this fires the
// instant a round is submitted, before the server has a fresh doc/round to
// report, so it only ever has the PRIOR round's doc name to work with. Same
// join convention as setTabTitle (TAB_REPO leads, filter-empty, ' · '-joined,
// 'viva' last).
function setProcessingTabTitle(docName) {
  document.title = [TAB_REPO, docName, 'working…'].filter(Boolean).concat('viva').join(' · ');
}

// Turn-state colors for the inline data: URI favicon — no network fetch,
// mirroring the CSS custom properties for the same states (--acc "your
// turn"/boot, --fact "processing", --machine "done") rather than inventing a
// second palette. Swaps the <link>'s href in place; setTabTitle's sibling.
const FAVICON_COLOR = { turn: '2946c4', processing: 'a06a12', done: '0c7f6b' };
function setTabFavicon(state) {
  const color = FAVICON_COLOR[state] || FAVICON_COLOR.turn;
  const link = document.getElementById('favicon-link');
  if (!link) return;
  link.href = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%23" + color + "'/%3E%3C/svg%3E";
}

/* Render verbatim markdown into el. Falls back to raw monospace text if
   either dependency hasn't loaded yet — the /vendor scripts are `defer`, so
   the boot below runs ahead of them: marked parses, DOMPurify sanitizes the
   result. Both are required before
   we'll commit HTML to the DOM; parsing without sanitizing would render
   untrusted markdown's raw HTML unescaped. Returns true on a real markdown
   render, false on the raw fallback — callers use this to decide whether the
   section is eligible for a later retry (see the marked-script/dompurify-script
   'load' listeners below, which re-render any card still showing the fallback
   once both dependencies are ready). */
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
// diffFileHunkCounts (file-header grouping) and renderDiffHunk (preamble
// synthesis).
function filepathFromTitle(title) {
  return String(title || '').replace(/\s+hunk\s+\d+$/, '');
}

// _ensureRendered only has a section id at render time; renderDiffHunk
// needs the section's title to synthesize the file preamble diff2html
// expects. Delegates to reviewSectionTitles() — the one id→title lookup.
function sectionTitleFor(id) {
  return reviewSectionTitles().get(id) || '';
}

/* Render one git hunk via diff2html: unified (line-by-line),
   line-by-line below, word-level intra-line diffs. Pure view transform —
   section.content stays the verbatim fence the /viva-diff skill
   (anchor-based edit relocation) and round-to-round carry-forward
   (byte-for-byte compare) depend on; the ---/+++ preamble diff2html needs
   to parse a bare @@ hunk is synthesized here at render time only, from
   the section title's filepath, and never stored.
   Pipeline order is load-bearing (gate-audit): Diff2Html.html() gives the
   markup as a STRING, DOMPurify sanitizes the string, and only then does
   it touch the DOM — same sanitize-before-assign order as renderMarkdown.
   (Materializing first and sanitizing after would let insertion-time
   payloads like <img onerror> execute before removal.) The whole draw is
   try/caught: a hunk diff2html can't parse falls back to the fenced view
   instead of stranding the card mid-activation. Syntax coloring is an
   enhancement layered on the sanitized DOM via the slim UI wrapper and
   the page's own hljs; when either is missing or throws, the word-level
   ins/del emphasis from diff2html itself still renders.
   Fallback when the diff2html assets (core script, or the mode-injected
   stylesheet — gated via link.sheet to avoid an unstyled flash when the
   script is cache-warm but the CSS is not) haven't loaded: the
   fenced-```diff markdown view, tagged d2h-pending so the load listeners
   upgrade it in place. Returns renderMarkdown's boolean on that path so
   _ensureRendered's retry bookkeeping stays correct. */
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
      // UNIFIED, always. Side-by-side splits the hunk into two panes, and a
      // pane is not the window: at a 1440px viewport the shell is 1368, the
      // hunk 892, and each pane 445 — 53 visible characters, against 962
      // needed for a 104-character line. BOTH panes scroll, independently,
      // and the reviewer drags twice to read one change. The same 892px
      // unified shows 107 characters, so a 100-column line fits whole.
      //
      // The old rule measured `window.innerWidth`, which is the wrong
      // quantity: the glyph rail and the margin take their share before the
      // code gets any, so a wide window says nothing about whether a pane can
      // hold a line. What changed *within* a line survives the format —
      // `diffStyle: 'word'` marks it either way.
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
// One status line for the refusals the page used to swallow — an approve
// pressed on a section with comments open, a save pressed on an empty box.
// Cleared and re-set on a tick so the same sentence announces twice.
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
   The cover slip on a returned drawing: one row per section attributing
   what changed at this revision and why — `revised to your note` (a diff
   answering an open note), bare `revised` (a silent diff), `flagged &
   unreviewed` (error before warn annotations; info advises, it doesn't
   flag), `approved & unchanged` (carried approvals). Each row jump-links
   to its card; carried targets scroll + reveal rather than activate
   (activateReviewCard's carried branch). transmittalHTML is pure over the
   review-input shape — classification and ordering only, no DOM;
   renderTransmittal owns the mount and the jump wiring. Diff mode ships
   no slip: hunk identity is positional across rounds. */
const FLAG_RANK = { error: 0, warn: 1 };

// Strongest flag severity on a section: 0 (error), 1 (warn), or null.
/* The author's turn on a thread, answered FOR THIS ROUND: a response, or
   grounds for a decline. Key PRESENCE for `grounds`, exactly as
   `openNotesHTML` does — a decline with no grounds is still a decline, and a
   decline is still an answer to the note.

   `round` is not optional and the freshness test is the whole point.
   `open_notes.py` appends an exchange only when the REVIEWER takes a turn on
   that cid, and an unsettled thread carries forward untouched — so a thread
   answered in round 1 that the reviewer then neither settled nor replied to
   still reads "answered" in round 3, 4 and 5. Without `last.round === round - 1`
   this predicate re-presents a two-round-old answer as this round's news,
   which is the exact defect it was written to close. `- 1` because an exchange
   is stamped with the round the reviewer's turn was made in, and the author's
   response to it lands in the round after.

   Defined ONCE, taking the round rather than reading a global, because two
   readers ask the same question and must never disagree about it: the
   transmittal's `answered` bucket (a pure function over its own `data`), and
   where round >= 2 lands the reader. */
function authorAnswered(t, round) {
  const last = ((t || {}).exchanges || []).slice(-1)[0] || {};
  if (Number(last.round) !== round - 1) return false;
  return Boolean(last.response) || last.grounds !== undefined;
}

function sectionAnswered(s, round) {
  return ((s || {}).open_notes || []).some(t => authorAnswered(t, round));
}

// A DOC_SCOPE flag is skipped: it is a fact about the document, and `checklist`
// emits `severity: "error"`, so one missing template heading would otherwise
// brand section 1 `flagged & unreviewed` in the cover slip — the same
// misattribution in the transmittal that the margin used to make.
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
  // approval clears its rState verdict, so it drops out of the carried bucket
  // (and, if it carries annotations, reappears as flagged) — the slip tracks
  // the live verdict, not just the static approved_ids the round shipped with.
  const carriedNow = id => approved.has(id) && rState.verdicts[id]?.verdict === 'approved';
  const revisedNoted = [], revisedBare = [], flaggedErr = [], flaggedWarn = [],
        answered = [], carried = [];
  (data.sections || []).forEach(s => {
    const hasDiff  = Array.isArray(s.diff) && s.diff.length > 0;
    const hasNotes = Array.isArray(s.open_notes) && s.open_notes.length > 0;
    if (hasDiff) { (hasNotes ? revisedNoted : revisedBare).push(s); return; }
    if (carriedNow(s.id)) { carried.push(s); return; }
    // The author answered the reviewer's own note and made no edit — a decline
    // (#167), or a response that needed none. Without a row this is the one
    // thing a round 2 exists for and the only thing the slip could not see: no
    // diff, no error/warn flag and no carried stamp fell through the whole
    // dispatch and rendered nothing. AFTER the carried test on purpose: a
    // section the reviewer already signed off is settled business, not news.
    // A STALE answer — one the reviewer left unsettled and unanswered for a
    // round — falls through to `flagRank` exactly as it did before, so a
    // section carrying both keeps its flag row rather than re-reading as news.
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
  // The head is a disclosure. The slip is the round's cover note, not the
  // round's content — above the print but COLLAPSED, so what a reader meets
  // first is the document rather than a bordered index of it (issue #186's
  // reading-order finding applies to the slip as much as to the threads).
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
   Every doc-scope flag in the round, once, in section order. They are the same
   fact wherever a producer had to hang them, so the document states them ONCE
   — as a slip, the way the transmittal states what changed — instead of five
   amber lines in the margin of section 1. */
function documentFlags() {
  return ((REVIEW_DATA && REVIEW_DATA.sections) || [])
    .flatMap(s => docFlagSplit(s).doc);
}

function docSlipHTML() {
  /* Every mode that renders sections, NOT review alone. `docFlagSplit` routes a
     doc-scope flag out of both columns unconditionally, so a mode gate here is
     not "the slip is a review feature" — it is the flag rendering NOWHERE. A
     diff round carrying one showed `checks 0/1` in the bar, with
     `round_is_complete` going on enforcing the gate in Python and no surface
     anywhere saying what the check was: exactly the worst-of-both this slip was
     built to prevent, relocated one mode over. `renderDocSlip` is called from
     `initReview`, which boots review and diff alike, so the surface was already
     there. */
  if (!REVIEW_DATA) return '';
  const flags = documentFlags();
  if (!flags.length) return '';
  // The checks tally rides in the head because `sectionSpec` no longer draws
  // one: today's only CHECK_KIND is doc-scope, so with the flags out of the
  // sections the gate would have no readout anywhere on the page while
  // `round_is_complete` went on enforcing it — the worst of both.
  const checks = flags.filter(a => CHECK_KINDS.includes(a.kind));
  const done = checks.filter(a => a.result).length;
  // Collapsed, like the transmittal, for the reading-order reason its own
  // comment states — UNLESS the document is carrying an error. Demoting a
  // document-level error to a digit behind a disclosure is a claim about
  // severity nobody made; one condition answers it without a debate.
  const open = flags.some(a => a.severity === 'error');
  return '<button type="button" class="transmittal-head" id="doc-slip-head" aria-expanded="'
    + (open ? 'true' : 'false') + '" aria-controls="doc-slip-rows">'
    + '<span class="transmittal-title">Document &middot; ' + flags.length
    + (flags.length === 1 ? ' flag' : ' flags')
    + (checks.length ? ' &middot; checks ' + done + '/' + checks.length : '')
    + '</span><span class="transmittal-chevron" aria-hidden="true">&#9662;</span></button>'
    + '<div class="transmittal-rows" id="doc-slip-rows"' + (open ? '' : ' hidden') + '>'
    // The rows dedupe their `result`; the tally above does NOT. `checks D/T` is
    // computed off the RAW flags because deduping first would read `checks 1/5`
    // on a round where all five were answered with one sentence — and this head
    // is the only readout of the `checks` gate anywhere on the page while
    // `round_is_complete` goes on enforcing it in Python.
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
// every hunk of a file contiguously before moving to the next file, so a
// single pass building this map is enough to know each run's total up front —
// no need to look ahead while iterating in the render loop below.
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
  // `.doc` arms every grid rule in the stylesheet, `.print` only the rules
  // that follow from every section being open at once — so the two share one
  // page and one set of margin builders without a runtime branch in CSS.
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
  // review mode, so the check below is always false there — zero behavior
  // change for review mode.
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
    // Continuous print retires that collapse in review mode (issue #186): a
    // settled section DIMS IN PLACE, prose and all, because the point of a
    // document review is reading the document and a carried section is still
    // part of it. buildCarriedCard stays the diff-mode path, where a carried
    // hunk genuinely has nothing to read.
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
  // Continuous print renders every section up front — there is nothing to
  // open, so there is nothing to render lazily. `_pendingMarkdown` and the
  // late-load retry keep working unchanged: `retryOnceScriptsLoad` selects on
  // the `.md-raw`/`.d2h-pending` marker classes, not on pending state.
  if (asDoc) REVIEW_DATA.sections.forEach(s => _ensureRendered(s.id));
  // Where round >= 2 LANDS. A section unchanged since the prior round keeps its
  // flags verbatim (`parse_sections._carry_annotations` copies them onto a
  // byte-identical section), so opening on the first unapproved section walked
  // a round-2 reader straight back onto the same flag wall they had already
  // read — while the author's answers to their OWN notes, the reason a round 2
  // exists at all, sat further down the print. On round >= 2 the print lands on
  // the first section carrying something new: a revision, else a thread the
  // author answered (a response, or grounds for a decline).
  //
  // The `!priorApprovedSet.has` conjunct is belt-and-braces. `_load_approved`
  // already drops a section whose content changed and one whose prior verdict
  // was not `approved`, so neither a diffed nor an answered section can be
  // carried — but the conjunct keeps `activateReviewCard`'s carried branch out
  // of play should a resume ever pre-approve differently.
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

// Build the advisory annotation strip for a card from section.annotations.
// Returns '' when there are none, so a bare section renders exactly as before.
// Map every section id → title for the current round, so an annotation whose
// anchor names another section can render a deep-link to it.
function reviewSectionTitles() {
  const m = new Map();
  ((typeof REVIEW_DATA !== 'undefined' && REVIEW_DATA.sections) || [])
    .forEach(s => m.set(s.id, s.title));
  return m;
}

// A kind:"preference" annotation encodes its preference id as a leading
// "[id]" token in the message (SKILL.md's own convention — annotate.py's
// merge whitelist has no generic passthrough for a structured field, so the
// id has to ride in the text). Matched only against PREFS_BY_ID; a stale or
// malformed token falls back to plain text, same as an unmatched anchor.
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
    // Badge-to-entry link (#142): a preference annotation whose leading [id]
    // token matches a fetched preference grows a second jump control,
    // labeled with *that preference's own* label/id — never the raw
    // substring — and opens the preferences panel to that row instead of
    // jumping to a section.
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

// Build the round-to-round diff block from section.diff (rows of {op, text}).
// Returns '' when there is no diff, so unchanged/new cards render as before.
// Presentational only — it never touches a verdict. Shown by default; the
// header toggles it collapsed.
// Word-level diff of a paired removed/added line. Returns [delHTML, addHTML]
// with changed tokens wrapped in <span class="dw">, both sides fully escaped.
// Falls back to plain escaped text when the pair shares too little (marking a
// full rewrite is noise, not signal) or the token product is too large.
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

// Build the open-note thread for a card from section.open_notes (issue #16) —
// the prior exchange (what was asked, what the agent answered) carried across
// rounds until the reviewer settles it. Returns '' when there's no open thread,
// so a bare section renders exactly as before.
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

// One carried thread, as a complete element. Split out of openThreadHTML so
// the margin (issue #186) can place each thread beside its own anchor instead
// of stacking the whole run above the prose — both surfaces build from this
// one function, and the doc grid restyles `.open-thread` into the margin's
// note grammar rather than forking the markup.
// The `.nh-num` span ships empty and stays empty in the accordion; only the
// margin numbers its notes, and it fills this in place (renumberDocNotes) so
// the reply textarea beside it never gets rebuilt out from under a keystroke.
function openThreadItemHTML(t) {
    const cid = esc(t.cid || '');
    const exs = t.exchanges || [];
    // The thread's current type carries to a reply (continuing an info thread
    // stays info), defaulting to info — but never as a suggestion: the reply
    // box collects prose, not replacement wording, and a suggestion comment
    // with no `replacement` is rejected at the server's boundary. Replying to a
    // suggestion re-requests it, which is exactly `changes`.
    const last = (exs.length && exs[exs.length - 1].verdict) || 'info';
    const type = (last === 'changes' || last === 'suggestion') ? 'changes' : 'info';
    const quote = t.quote ? '<span class="open-thread-quote">' + esc(t.quote) + '</span>' : '';
    // A declined thread is unresolved, not closed: the author answered and the
    // move is now the reviewer's — settle to accept, or reply to insist, which
    // always wins. Same settle button, same reply box, different label and
    // prompt; the clamp above already sends an insisting reply as `changes`.
    const declined = t.status === 'declined';
    /* One verb per note, with its keycap, instead of a permanently-open reply
       box under two type chips. The old block was ~120px of controls on every
       carried thread whether or not the reviewer intended to say anything —
       affordable at the foot of an accordion card, not in a 253px margin
       beside the paragraph.

       The verbs are viva's actual moves, not new ones. A declined thread is
       waiting on accept-or-insist, so it leads with `Accept` (settle: the
       author's decline stands) against `Change anyway` (reply: an insisting
       reply is binding and always wins). An open thread offers `Reply` and
       `Settle`. Nothing here is a second confirmation step for a suggestion —
       making one IS the instruction, and the prose already shows it applied. */
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
      +   '<span class="open-thread-label">' + (declined ? 'author kept as-is' : 'open note')
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
   The generating agent self-annotates each section with a
   kind:"confidence" annotation carrying basis (sourced|inferred) and level
   (high|medium|low). The reviewer can reorder the queue weakest-first so
   attention lands where the agent is shakiest; document order stays the
   default and remains available. Sorting reads the structured fields off the
   annotation — never the message text. Sections with no confidence annotation
   sink to the bottom and keep document order (a doc with none is unchanged). */
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
  // Diff mode's file-header grouping depends on cards staying in fixed document
  // order (CSS `order` would strand the static file-group-header divs, which
  // carry no order, away from their file's cards) — so force the toggle off
  // here rather than relying on diff-mode sections happening not to carry
  // confidence annotations.
  const hasConfidence = REVIEW_DATA.mode !== 'diff' && REVIEW_DATA.sections.some(s => confidenceAnnot(s));
  if (bar) bar.style.display = hasConfidence ? '' : 'none';
  applyCardSort();
}

/* ─── The accordion, wearing the margin grammar ─────────────────
   Diff mode's builder. What survives is the ACCORDION — one hunk open at a
   time, a real disclosure button for a head, the animating body region —
   because 52 hunks printed at once is a worse surface than one at a time.
   What does not survive is the accordion's CHROME. The annotation strip, the
   stacked thread list, the add-a-note row and the action row all sat between
   the reviewer and the hunk, stacking commentary on top of the thing it is
   about: the same inversion the print fixed, in a narrower frame. They move
   to the margin, where they sit beside the lines they concern.

   The hunk is `.d2h-wrapper` — one block, so layoutDocRows gives it a single
   `wide` row and the notes hang off it. It never breaks out into the margin's
   track: see the `.doc.print` scope on that rule. */
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
   `carried` marker, title, an "unchanged since your stamp — show" reveal of
   the read-only content, the mono APPROVED mini-stamp, and a withdraw
   control. No comment machinery — a carried card is settled unless
   withdrawn, at which point it becomes a normal accordion card again. */
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

// Withdraw a carried approval: clear the verdict back to pending and swap the
// collapsed carried card for a normal accordion card, opened for re-review.
// The fresh card replaces the carried one in place — document order is
// canonical, withdrawn cards never reorder.
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
   Review mode's renderer. Sections print open, in document order, as a run
   of `check gutter | prose | margin` rows; a settled section dims in place
   instead of collapsing to a clickable row. Commentary sits BESIDE the
   passage it annotates — the reading-order inversion this story fixes was
   that a round-2 section put ~700px of threads, diff and slip between the
   reader and the paragraph all of it was about.

   Diff mode is untouched and keeps the accordion (buildReviewCard): a hunk
   is not prose, it has no margin to annotate and no measure to hold, and a
   200-hunk changeset read as one continuous print is a worse surface than
   one hunk at a time.

   The registry of check kinds is injected from scripts/schema.py rather
   than restated here — CHECK_KINDS is the flag registry that gates a
   `checks` round, and a second copy of it in the frontend is exactly the
   fail-open drift the schema module exists to prevent. ═════════════════ */
const CHECK_KINDS = __CHECK_KINDS__;
/* The scope registry, injected for the same reason and against the same
   failure. DOC_SCOPE_KINDS is a DIFFERENT AXIS from CHECK_KINDS — that one
   asks "does this gate a `checks` round", this one asks "what is this flag
   ABOUT" — and `headings-present` is deliberately in both. A hand-kept second
   copy would fail open the same silent way: an unregistered kind is treated as
   section-scope and piles onto whichever card its producer anchored it to. */
const DOC_SCOPE_KINDS = __DOC_SCOPE_KINDS__;

/* ─── The seam: the grammar is not the print ─────────────────
   The restructure is TWO things, and one class stamped for both is what
   conflated them.

   THE GRAMMAR — three-column rows, margin notes with numbered pins, the
   glyph rail, the segmented rule, the spec table, per-note verbs — belongs
   to anything that renders a section. Nothing about a hunk or a question
   resists a margin, and the commentary-beside-the-passage inversion this
   work fixes is not a property of documents.

   CONTINUOUS PRINT — every section open at once, a settled one dimming in
   place instead of collapsing — is review's alone. 52 hunks printed at once
   is worse than one at a time, and a question at a time is the point of an
   interview.

   `.doc` on the card container arms the grammar; `.print` arms the print.
   Review stamps both, diff and Q&A stamp only the first.

   There is deliberately no `usesMargin()` beside `isContinuousPrint()`. Every
   place `isDocMode()` used to branch turns out to be reached only from a
   surface that renders sections — and every such surface wears the margin — so
   the honest form of "does this surface use the margin grammar" is no
   condition at all. A predicate that is true at all of its call sites is worse
   than none: it advertises a surface that does not exist. */
function isContinuousPrint() { return !!(REVIEW_DATA && REVIEW_DATA.mode === 'review'); }

/* ─── Flags: which column a producer flag belongs in ─────────
   The 70px gutter is for a glance — a severity glyph and a short message
   read at the edge of vision while the eye stays on the prose. A flag that
   carries an interactive jump (a contradiction's cross-section link, a
   learned preference's badge-to-entry link) is neither short nor a glance,
   so it routes to the margin and renders through annotStripHTML, keeping
   the jump wiring those two features already ship. */
function docFlagSplit(section) {
  const titles = reviewSectionTitles();
  const gutter = [], margin = [], doc = [];
  (section.annotations || []).forEach(a => {
    if (!a) return;
    // A document fact, not a flag on this passage. Both producers that emit one
    // anchor it to the first card because that is the only document-level
    // handle they have — taking that literally put five amber "missing expected
    // design-doc section" lines in the margin of section 1, which is the first
    // thing a round-1 review paints. It goes to neither column: it goes to the
    // document slip.
    if (DOC_SCOPE_KINDS.includes(a.kind)) { doc.push(a); return; }
    // A confidence annotation is the agent's self-report about the whole
    // section, not a flag on a passage — it drives the triage sort, and its
    // readout is the spec table. Letting it into the gutter would hold 98px
    // open on every self-annotated document for sort metadata, which is
    // exactly what the wasted-space rule is about.
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

/* One decision, printed once. A `checks` round answers several flags with the
   same sentence: five check flags each printed the SAME one-line `result`
   verbatim, measured in round 3 of a live session — one decision, printed five
   times. Every flag's MESSAGE still prints and always will; a `result` already
   printed in this pass is dropped, so the answer reads once and the flags that
   share it stay legible.

   The annotation is COPIED, never mutated: `a.result` is what `specHTML`'s
   `checksDone`, `sectionBalance`'s `settled`, `documentBalance` and the slip's
   own `checks D/T` head all count, and blanking it in place would silently move
   a number with no error anywhere.

   `seen` is a parameter, not a module-level Set, so every caller owns one per
   invocation. `placeDocFlags` is idempotent by contract — a shared Set would
   look right on first paint and suppress every result on the first re-sync. */
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
   The rendered markdown's top-level blocks become the prose column of one
   row each, so a note can sit beside the paragraph it annotates rather
   than beside the section. Code and tables take a `wide` row: they are not
   prose and do not hold the prose measure. */
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
  // heading is the same words twice (what `.section-content > h1:first-child`
  // hid in the accordion, done here because the heading is no longer a
  // first child of anything once the blocks are distributed).
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

/* The section's foot band. Static markup from both builders — a pure query,
   like `docHeadRow` was, so nothing here creates DOM inside a render loop.
   `buildCarriedCard` builds no bands, so a carried reveal yields null and every
   caller's `if (!host) return;` keeps doing the job it already did. */
function docFootRow(id) {
  const sec = el('rcard-' + id);
  return sec ? sec.querySelector('.row-foot') : null;
}

// The row whose prose holds the given occurrence of `text`. Counts occurrences
// across rows in document order so a phrase repeated in three paragraphs puts
// the note beside the paragraph the reviewer actually selected in, the same
// ordinal renderHighlights marks. Null when nothing matches — an unresolvable
// anchor is a whole-section note, never a silently misplaced one.
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

// Side cells are created on demand and never pre-reserved: a row that gains a
// note grows a margin cell, a row that never has one never carries an empty
// box. The COLUMN's width is a separate, document-level decision
// (updateDocColumns) — this is only about the box.
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

/* Where a note hangs. A note whose anchor resolved into a row hangs in that
   row's margin, beside its own passage — the whole reason the margin column
   exists. A note with no row of its own — unanchored, or an anchor that
   resolved to nothing — hangs at the section's FOOT, which is where a
   whole-section note is read. Never at the head: a note about all of a section
   is not an introduction to it, and at the head it started the section's own
   first paragraph 400px down the page. */
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
   Two sources, deliberately kept apart. A carried THREAD is built once and
   placed once — it owns a reply textarea, and rebuilding it mid-keystroke
   would steal focus, which is exactly the invariant the accordion's
   separate `.comment-list` host protected. This round's COMMENTS are
   static text and are rebuilt freely on every sync. */
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

// Notes in reading order: by the row their anchor lands in, then by the order
// they were made. An unanchored note sorts to the END — `rows.length`, past
// every real index — because a whole-section note is read AFTER the section,
// not before it, and because that is where it now renders (the foot band). It
// sorted to -1, the section head, until that put 400px of margin beside a
// one-line title and started the section's own first paragraph 400px below it.
//
// An anchor that resolves to NO row degrades the same way. `rowForAnchor` reads
// a row's concatenated prose text and matches across element boundaries;
// `wrapNth` needs the needle inside a single text node and is strictly
// stricter, so a `rowForAnchor` miss implies a `wrapNth` miss: the note lands
// at the foot with its quote echo, takes the highest ordinal, and carries no
// pin. That is the honest degrade, and at the foot it reads as a whole-section
// note instead of disappearing into a pile above the prose.
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
  /* The fence is for a suggestion the prose could not show applied — code, or
     an anchor that never resolved. When markAndPin DID splice it inline, the
     note says so rather than printing the same two strings a second time: the
     applied sentence is upstream, in the text, which is where a reviewer
     judges wording. */
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
  // Nothing open and nothing checked: no state run at all. A spec reading all
  // zeros is not a state readout, and this is also what keeps the FOOT band's
  // height independent of which section is live — see renderDocSpec. On a
  // clean typed round 1 whose only annotations were doc-scope producer flags,
  // this early return is now what leaves section 1's band as bare verbs.
  const conf0 = confidenceAnnot(section);
  if (!s.comments && !s.suggestions && !s.declined && !s.checks && !conf0) return '';
  // A RUN, not a table: five label/value pairs at 10.5px mono are one line at
  // the reading measure, against the ~120px a five-row table took in a 328px
  // column beside a one-line title. Same items, same ink rule, same early
  // return. The table's `<caption>` is gone — `.doc-apparatus` carries a
  // `role="group"` with an `aria-label`, so the band is still named.
  const item = (label, value, open) =>
    '<span class="sp' + (open ? ' sp-open' : '') + '">'
    + '<span class="sp-k">' + label + '</span> <span class="sp-v">' + value + '</span></span>';
  // The agent's own confidence is a state item, not a gutter flag: it is about
  // the section, and it is what the triage sort orders on. `docFlagSplit` sends
  // a `confidence` annotation to NEITHER column precisely because this is its
  // readout — drop it here and a documented feature goes invisible with no
  // error anywhere.
  const conf = confidenceAnnot(section);
  // Each count prints only when it is one — three zeros were the run's usual
  // content on a typed round, and the reader scanned past them to reach the
  // one live value. A decline is OPEN judgment (sectionBalance says so), so
  // it takes the open ink like the other two.
  return (s.comments ? item('comments open', s.comments, true) : '')
    + (s.suggestions ? item('suggestions open', s.suggestions, true) : '')
    + (s.declined ? item('author kept as-is', s.declined, true) : '')
    + (s.checks ? item('checks', s.checksDone + '/' + s.checks
        + (s.checksDone === s.checks ? ' &#10003;' : ''), s.checksDone < s.checks) : '')
    + (conf ? item('agent confidence',
        [conf.basis, conf.level].filter(Boolean).map(esc).join(' &middot; ') || esc(conf.message || '—'),
        conf.level === 'low') : '');
}

/* ─── Segmented rule ─────────────────────────────────────────
   One denominator, one place. Every open item is either JUDGMENT (the
   reviewer's call — changes, a suggestion, a decline waiting on accept-or-
   insist) or an open FACT (a question, an unanswered check, a producer's
   warn/error flag); everything resolved is SETTLED. The order is fixed and
   that fixed order is the colorblind-safe second encoding. The raw counts
   ride out in the aria-label so the honest-proportions claim is auditable
   rather than asserted. */
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
    // without this the branch below would count five document facts as five
    // open items on section 1 — the section none of them concerns.
    //
    // A stated decision, not a derivation: because the skip precedes that
    // branch, an ANSWERED doc-scope check contributes no `settled` here or in
    // `documentBalance`'s sum. It is accounted for exactly once, in
    // `documentBalance`'s own `checks`/`checksDone` pair, which is what the
    // slip's `checks D/T` prints.
    if (DOC_SCOPE_KINDS.includes(a.kind)) return;
    if (CHECK_KINDS.includes(a.kind)) { if (a.result) settled++; else facts++; return; }
    // And a PLAIN producer flag is not an item at all. `.mflag` takes no
    // border and no actions — it is advisory and there is nothing to answer,
    // so nothing the reviewer can do makes it close. Counting it as an open
    // fact painted a section whose only annotation was one warn flag with a
    // 100%-wide amber bar (four on the first screen, measured) and held
    // `N open` off zero on a round where every section was approved and every
    // check answered. An unanswered CHECK_KIND above is the opposite case: it
    // is answered with `result` and it gates a `checks` round, so it stays.
  });
  /* The section's own sign-off is an item in BOTH states, and that is the
     whole of the correction. It used to count only once approved (`settled++`
     and nothing on the other branch), so a document's item total measured
     decisions ALREADY MADE rather than things to decide: a fresh eight-section
     round with five unanswered checks printed `0 items · 0 open`, which the
     legend's own definition — "an item is … or a section's own sign-off" —
     says is eight. The count grew as the reviewer worked, which is the exact
     class of defect the aggregates were redefined to end.

     It rides out as its own field rather than folding into `judgment` because
     the two consumers want different things and both are right. A pending
     sign-off IS open business, so `documentBalance` counts it. It is not
     business the segmented rule should paint: `segHTML` sums only
     judgment/facts/settled, so a section whose only open item is its own
     sign-off still draws the settled hairline instead of a 100%-wide cobalt
     slab on every unreviewed section of every round 1 — finding 09 in a
     different ink. */
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

/* ─── The head row: orientation, one track ───────────────────
   The heading, the section's number, its summary, the segmented rule and the
   collapsed round diff. Nothing else, and NO margin cell — that is the whole
   of finding 01. The margin column exists for a note that points at a passage
   sitting beside it; a section's state table and its verbs point at the
   SECTION, have no passage beside them, and were being paid for in blank page.
   A one-line title beside a 400px pile of margin rendered as 360px of white
   between a heading and its own first paragraph, and even the bare
   state-table-plus-approve case measured 40px against 101px on EVERY annotated
   section. The head row is now a single track: its height is its prose's
   height, and a void is not expressible.

   The prose cell still differs by surface — the print prints the heading here,
   the accordion has already printed it in the disclosure button — so it is
   still passed in. What used to differ is gone: there is no margin cell to
   keep identical. The section's verbs moved WITH the state, to the foot band
   (`docFootRowHTML`), which is where `skip` went too. */
function docHeadRowHTML(id, proseHTML) {
  return '<div class="row row-head"><div class="rp">' + proseHTML + '</div></div>';
}

/* ─── The foot band: disposition ─────────────────────────────
   What the head row used to hold in its margin, laid out the way a catalog
   lays out an entry's state block: horizontally, at the reading measure, under
   the thing it describes. A parts catalog prints the entry, then its
   specification, then how to order it. So does this.

   Static markup from both builders, never created on demand — that keeps
   `docFootRow` a pure query (the same shape `docHeadRow` was) and keeps DOM
   creation out of the render loops that call into it on every sync.
   `buildCarriedCard` builds neither band, so a carried reveal still yields
   `null` for the same structural reason it always did, and the three
   `if (!host) return;` guards keep guarding with no explicit refusal.

   It is a SIBLING of `.section-content`, never a child: `docRows` is
   `#rcontent-<id> > .row`, so a foot row inside it would become a row
   `rowForAnchor` can return, `docNotesOrdered` can index, `markAndPin` can
   walk and `proseWalker` has to filter — the margin's own echo re-entering the
   document walk, which is the #95 failure this grid already fought once.
   Outside it, it is invisible to all four.

   The VERBS lead and the state trails (see `.spec-strip { order: 2 }`), so
   `approve` sits on the same left edge the reader has been reading down,
   whether or not the section has a spec to state.

   `skip` is the accordion's alone. In the print every section is on the page
   already and skipping to the next one is just reading on; with one hunk open
   at a time, "not this one, not now" is a real move and it needs a control. */
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

/* The wiring that goes with the two bands. Approve is the section's own
   control and it stays reachable by pointer and by Tab: with the action row
   gone, a section carrying no notes would otherwise hold no focusable element
   at all, and keyboard access to every section is a hard requirement
   (test_server_a11y). ⌘K is a second path to the same verb, never the only
   one. `root` is the whole card/section in both builders, so the head/foot
   split is invisible here — everything is addressed by id. */
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

  // The live section follows the reader: pointing at or tabbing into one
  // makes it active without yanking the page, which is what the jump paths
  // (transmittal rows, pins, the palette) deliberately still do. The print's
  // alone — in the accordion the disclosure button is what makes a section
  // live, and there is only ever one open.
  sec.addEventListener('mousedown', () => activateReviewCard(id, { noScroll: true }));
  sec.addEventListener('focusin',   () => activateReviewCard(id, { noScroll: true }));

  wireDocSection(sec, id);
  return sec;
}

// Withdraw, in continuous print. The accordion swapped a collapsed carried
// card for an open one; here nothing was ever collapsed, so withdrawing is
// only the verdict going back to pending — the prose stays exactly where the
// reader was reading it.
function docWithdraw(id) {
  if (rState.verdicts[id]) rState.verdicts[id].verdict = undefined;
  syncReviewCard(id);
  updateReviewStats();
  renderTransmittal();
}

/* ─── Place: flags, threads, notes, pins ─────────────────────
   Called once per section after its markdown is laid out into rows, then
   surgically on every sync. Placement is idempotent — a thread already in
   the right cell is left alone, because moving a DOM node blurs whatever
   is focused inside it and a thread owns a reply textarea. */
function placeDocFlags(id) {
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  const split = docFlagSplit(section);
  const byRow = new Map();
  // Per call, spanning every row of this section — see `dedupeResults`. Today
  // no doc-scope kind reaches this loop at all (`docFlagSplit` routes them to
  // the slip, and `headings-present` is the only CHECK_KIND), so this is the
  // guard for the day a section-scope check kind lands, not the site that
  // closed the finding. That one is `docSlipHTML`.
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
    // Idempotent like its siblings: initReview calls _ensureRendered in the
    // eager loop and again through activateReviewCard, and on the md-raw path
    // neither call deletes from _pendingMarkdown — without this the strip
    // would stack twice for as long as the raw fallback is on screen.
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
    // The same guard `placeDocFlags` and `docNoteHost` carry, and for the same
    // reason: an unanchored thread falls back to the section's FOOT band, and
    // a surface that has no foot band (a carried reveal) has nowhere to put it.
    // The three fallbacks move together or they disagree.
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
    // A rendered diff line is a code well too — `.d2h-code-line-ctn`, not
    // `<pre>` — and everything the strike rule says about code says it twice
    // about a diff: a `del`/`ins` pair spliced into a `+` line reads as
    // neither version of anything. The −/+ fence in the margin carries it.
    n.inCode = !!(mark.closest && mark.closest('pre, .d2h-code-line'));
    const repl = noteReplacement(n);
    let tail = mark;
    /* A suggestion is SHOWN APPLIED, in the prose: the wording it replaces
       struck in gray, the replacement on the same catalog yellow the anchor
       wears. Without this the reviewer reads a note *about* a sentence and
       never the sentence — which is the whole difference between a suggestion
       and a comment, and the composite's own caption for it.

       Not in code. A struck line inside a code well reads as broken syntax,
       and a replacement spliced mid-expression reads as neither version; the
       −/+ fence in the margin carries a code suggestion instead, which is the
       grammar every reviewer already knows. `n.inCode` is what the margin
       reads to decide. */
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
    // On a diff line the pin leads the line rather than trailing the anchor —
    // see `.pin-line`. The mark still shows WHICH span; the pin shows WHICH
    // NOTE, and only one of the two has to survive a horizontal scroll.
    // The LINE (`.d2h-code-line`), never the inner `.d2h-code-line-ctn` —
    // that span only exists on some line kinds, and a rule that holds for
    // three lines in four is worse than none.
    const line = tail.closest && tail.closest('.d2h-code-line');
    if (line) { pin.classList.add('pin-line'); line.prepend(pin); }
    else tail.after(pin);
  });
}

function renderDocMargin(id) {
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  const sec = el('rcard-' + id); if (!sec) return;
  // Only the dynamic hosts are wiped. Thread notes and their reply textareas
  // are never rebuilt — the accordion protected the same invariant by keeping
  // threads out of `.comment-list`.
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
      // A carried suggestion the prose could not show applied — a code
      // anchor — gets the −/+ fence, once. In prose the inline strike and
      // replacement upstream already say it, and a fence there would print
      // the same two strings a second time.
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

/* Drawn for every section that has something to state, NOT only the live one.
   Gating it on `rState.active` made activation a layout change: clicking `+
   note` on a section moved the state readout from one band to another, so the
   section jumped 57px up while the button slid 17px out from under the cursor
   (measured). A reviewer cannot click a control that leaves when they reach
   for it. The live section is marked at its heading instead — a border and a
   compensating negative margin, which cost no layout at all. */
function renderDocSpec(id) {
  const mount = el('rspecbody-' + id); if (!mount) return;
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  mount.innerHTML = specHTML(section);
}

/* The wasted-space rule, decided once for the whole surface.

   Read off the ROUND, not off the DOM. The continuous print renders every
   section up front, so there the two agree — but the accordion renders a
   section's rows only when that section is opened, and a DOM read would
   therefore see an empty margin until the reviewer reached the first hunk
   carrying a note, then collapse or open the column mid-review and jog every
   hunk sideways. That is the per-navigation decision this rule exists to
   avoid; the whole point is that it is made ONCE, for the whole round.

   `docNotes` already reads rState, so a comment made a moment ago counts the
   same as a thread that shipped with the round — the property the old DOM
   read was there for, kept. */
function updateDocColumns() {
  const doc = el('review-cards');
  if (!doc || !doc.classList.contains('doc')) return;
  const sections = (REVIEW_DATA && REVIEW_DATA.sections) || [];
  const gutter = sections.some(s => docFlagSplit(s).gutter.length);
  // An OPEN compose popover holds the margin as surely as a saved note does.
  // Without it, the first anchored comment on a bare document — the exact
  // document the collapse rule exists for — mounts its textarea into a 0px
  // track. It is also what makes the `+ note` composer safe in the foot band
  // with no rule of its own — this clause runs synchronously before paint.
  // `.is-open` rather than a style-attribute match: the serialized inline
  // style is the browser's business, not a selector's.
  //
  // NOTE what is deliberately NOT counted: `split.gutter`. A gutter-bucket
  // flag renders words into a `.rm` too (placeDocFlags' byRow loop), so one
  // whose anchor resolves to no row lands in the foot band's margin on a
  // document this rule would otherwise collapse. That is what the
  // `.doc.no-margin .row-foot` twin in the CSS is for — widening this line
  // instead would hold the column open on every flagged document.
  const margin = sections.some(s => docFlagSplit(s).margin.length || docNotes(s).length)
    || !!doc.querySelector('.rm .comment-popover.is-open');
  doc.classList.toggle('no-gutter', !gutter);
  // The PRINT never collapses its margin. An empty margin is still the
  // measure — the page holds it — and collapsing put the prose at 967px, ~150
  // characters a line, on every note-less round, then rewrapped the whole
  // document the moment the first composer opened. The accordion still
  // collapses: there the wide row IS the section, and the room is real.
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

// `opts.noScroll` marks a passive activation — the reader pointed at or tabbed
// into a section in the continuous print and the live section should follow
// them without the page moving under their hands. Every explicit jump
// (transmittal row, pin, palette, annotation link) omits it and still scrolls.
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
  /* A CARRIED card is the one thing that renders section content without
     wearing the grammar. It is a read-only reveal of a hunk already signed
     off: nothing to annotate, no notes to place, no verbs — and, crucially,
     no `.row-head` and no `.row-foot`, because buildCarriedCard never builds
     either band. Running the
     pipeline over it grids a body nobody can comment on, and an unanchored
     carried thread takes `placeDocThreads` down `docCell(null, 'rm')`. */
  const card = el('rcard-' + id);
  const carried = !!(card && card.classList.contains('is-carried'));
  if (!rendered) {
    // marked/DOMPurify haven't landed: the content is raw text, not blocks.
    // The doc print still grids it — one row — so a renderer-less boot reads as a
    // document with its margin rather than as one undifferentiated slab. The
    // retry re-renders in place once the scripts arrive.
    if (!carried) { layoutDocRows(id); placeDocFlags(id); placeDocThreads(id); renderDocMargin(id); }
    return;
  }
  // A d2h-pending card rendered successfully as fenced markdown but is
  // waiting for diff2html to load — keep its source so the diff2html-script
  // load listener below can re-render it properly. Deleting it here would
  // strand the card on the fallback view forever (the same
  // late-loading-dependency lesson as the marked/DOMPurify retry, and the
  // hljs race the gate-audit pass caught).
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

// One-time-per-script retry for late-loading renderers: a card opened
// before a renderer's dependencies finished loading rendered a fallback and
// stayed in _pendingMarkdown (marked/DOMPurify missing → raw text tagged
// .md-raw, since renderMarkdown requires *both*; diff2html missing → fenced
// markdown tagged .d2h-pending). Re-render every card still carrying the
// marker class once the script(s) land — attaching to every script in the
// list means load order never matters; whichever lands last is what flips
// the renderer over. Scoped to the marker class (not all of
// _pendingMarkdown) so cards that simply haven't been opened yet stay
// lazily unrendered.
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

/* `c` / `i` — open the composer with that comment type already picked.

   Neither key writes a verdict of its own. They used to (`setReviewVerdict`,
   now gone): the card badged `changes` while `submitReview` derived the
   section from `activeComments`, found none, and submitted `pending` — the
   reviewer's decision visible on screen and absent from the payload (#156).
   Routing through the composer keeps `changes`/`info` a DERIVED verdict, so it
   always arrives carrying the note the revise loop acts on.

   Toggle-off went with `setReviewVerdict`; the composer's own controls replace
   it. Cancel before saving, or remove the saved comment in the margin — the
   verdict un-derives either way. */
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

  // A verdict is part of the section's balance (an approval is a settled item)
  // and part of its spec, and neither repaints from the comment path — approve
  // used to leave the segmented rule showing the state before the stamp.
  renderDocSeg(id); renderDocSpec(id);

  syncNoteInline(id);
}

/* ─── Comments (multi-comment review) ───────────────────────────
   A section owns a list of typed comments; the section verdict is DERIVED,
   never picked. No active comments → approved (if reviewer approved) or pending;
   any `changes` OR `suggestion` comment → changes; otherwise info. A suggestion
   is a directive carrying the wording, so it lands with `changes`: a section
   holding one is not approved. Each comment is an open thread by default
   (cid-keyed). The same rule is stated in DESIGN.md ("Multiple inline
   comments"), SKILL.md's verdict table, and `scripts/schema.py`'s
   COMMENT_TYPES — all three move together. */
function commentsOf(id) { return (rState.verdicts[id] ||= {}).comments ||= []; }

// Comments that are real, unsettled feedback — the basis for the verdict, the
// button count, the rendered list, and whether a section can be approved. A
// suggestion qualifies on its `replacement` alone: the wording IS the comment,
// and its note is optional rationale.
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

// One control, one grammar. It wore `.action-btn` in the accordion and
// `.nt-btn` in the print, which meant reading the class back off the DOM to
// decide how to relabel it; with the margin on both surfaces there is one
// button and one rule — approve reads "approve" only while nothing is open,
// and an approved section offers to withdraw.
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
    // In the doc grid the margin and the check gutter are descendants of the
    // section container, so `.section-content` alone no longer means "in the
    // document" — a drag across your own prior note would open a popover
    // anchored to text that is not in the doc. The prose cell is the document.
    if (!start.closest('.rp') || start.closest('.sug-ins')) return;
    const m = content.id.match(/^rcontent-(.+)$/);
    if (!m) return;
    // Which occurrence of a repeated phrase the reviewer picked exists only in
    // the rendered content — that is where the selection lives. Read the
    // ordinal there, resolve the SAME ordinal against the markdown source
    // (issue #95). `getRangeAt(0)`, not anchorNode/focusNode: a backwards drag
    // reports its endpoints in the opposite order, and the ordinal is counted
    // from where the selection *starts* in document order.
    const occurrence = occurrenceInRendered(content, sel.getRangeAt(0), text);
    openCommentPopover(m[1],
      { anchor: { text, offset: offsetInSource(m[1], text, occurrence), occurrence } });
  }, 0);
});

// A selection endpoint may be a text node; normalize to its element.
function toElement(node) {
  return node && node.nodeType === 3 ? node.parentElement : node;
}

/* The cross-pane selection guard is gone with the panes. Side-by-side put
   old and new in two adjacent scroll boxes, so a drag across them yielded
   DOM-order text that was not a contiguous substring of the raw hunk, and the
   guard degraded it to an unanchored note. A unified hunk is one column in
   source order: every selection inside it is already contiguous. */

/* ─── Anchor resolution: rendered occurrence → source offset (#95) ─────
   The reviewer selects in rendered HTML; the anchor must address the markdown
   source. A phrase that repeats has one identity — which occurrence — read
   where the selection actually happened and then resolved against the source,
   so the stored offset and the on-screen highlight name the same span. The
   ordinal rides out on the anchor because it is the half that survives a
   re-render; the offset is the half the source edit uses. */

// 0-based ordinal of the selected occurrence of `text`: how many occurrences
// *begin* before the selection starts, counted over the rendered text. One
// scope, the section's own content — side-by-side's facing pane, which
// repeated every line and had to be scoped out of the count, no longer exists.
function occurrenceInRendered(root, range, text) {
  if (!root.contains || !root.contains(range.startContainer)) return 0;
  // The doc grid puts the margin INSIDE the section container, and a margin
  // note echoes the wording it annotates (.nt-quote, .open-thread-quote). A
  // Range over the container counts those echoes, inflating the ordinal so
  // offsetInSource addresses a different span than the reviewer picked — the
  // #95 bug, except the margin manufactures the repeat. Count the prose only.
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
   One filter, used by everything that must address the DOCUMENT rather than
   what is written about it: the margin, the check gutter, and an open
   compose popover are all descendants of the section container in the doc
   grid, and none of them is the text under review. In the accordion nothing
   matches these classes inside `.section-content`, so the filter is inert
   there and both surfaces run the same walk. */
function proseWalker(root) {
  return document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      for (let p = node.parentElement; p && p !== root; p = p.parentElement) {
        const c = p.classList;
        // `.sug-ins` is wording the reviewer PROPOSES — it has never been in
        // the document, so counting it would inflate every later ordinal and
        // let a comment anchor to text the author never wrote. The `.sug-del`
        // beside it is the real source text and stays counted.
        if (c && (c.contains('rm') || c.contains('rg') || c.contains('comment-popover')
                  || c.contains('sug-ins')))
          return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
}

// 0-based ordinal of the selected occurrence counted over prose text only, or
// null when the selection starts somewhere the walk cannot place (an element
// boundary rather than inside a text node) — the caller then falls back to the
// Range count rather than guessing.
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

// Char offset of the reviewer's chosen occurrence of `text` in the section's
// raw markdown source — the rewrite target. -1 when the ordinal does not
// resolve there; the anchor still stores text + occurrence, and -1 says
// "unplaced", not "absent" — the agent scopes by the section rather than
// guessing a match.
function offsetInSource(id, text, occurrence) {
  const src = _pendingMarkdown.get(id)
    || REVIEW_DATA.sections.find(s => s.id === id)?.content || '';
  const n = occurrence > 0 ? occurrence : 0;
  const at = nthIndexOf(src, text, n);
  // Rendered and source occurrence counts can diverge — markdown syntax the
  // renderer strips, diff chrome it adds. An ordinal that overruns the source
  // still resolves when the source holds exactly one match, because one match
  // is unambiguous; with two or more it stays honestly unresolved rather than
  // silently collapsing back onto the first, which is the bug being fixed.
  if (at < 0 && n > 0 && nthIndexOf(src, text, 1) < 0) return nthIndexOf(src, text, 0);
  return at;
}

// A small popover with the type chips + a note field + save/cancel. `anchor`
// is {text, offset} or null (whole-section note); `type` pre-picks a chip
// (the `c` / `i` shortcuts), defaulting to `changes`.
//
// The third chip, `suggest wording`, adds a replacement field: the reviewer
// types the exact wording instead of describing the change, and the author
// applies it verbatim to the anchored span. Review mode only — a diff hunk's
// suggestion would be a verbatim code edit, and /viva-diff carries no
// instruction to apply one (issue #166 scopes that out).
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
  // beside its own passage; unanchored (`+ note`), in the FOOT band's margin,
  // appended after whatever is already there — which is exactly the cell the
  // note it saves lands in, one child up, in `.rm-notes`. The composer and
  // every unanchored note it creates share ONE `.rm`, which is what keeps
  // `renderDocMargin`'s wipe-and-renumber pass and the box the reviewer is
  // typing in describing the same place.
  //
  // The invariant this used to guard — the button just clicked must not move —
  // is now structural rather than positional. `+ note` lives in the same foot
  // row, in the PROSE cell; a box appended to the margin cell beside it cannot
  // displace it at all. Moved before focus: relocating a node after focusing
  // inside it blurs the field.
  const row = anchor ? rowForAnchor(id, anchor.text, anchor.occurrence) : null;
  const foot = docFootRow(id);
  const host = row ? docNoteHost(id, row) : (foot && docCell(foot, 'rm'));
  if (host && pop.parentElement !== host) host.appendChild(pop);
  pop.style.display = '';
  pop.classList.add('is-open');
  // Opening the box is what makes the margin non-empty; closing it (saved or
  // cancelled) is what may let the column collapse again. This is also why the
  // FOOT band needs no `.doc.no-margin` twin for the composer: the sequence
  // above is synchronous and completes before paint, so `.rm .comment-popover
  // .is-open` has already dropped `no-margin` by the time the textarea is laid
  // out. (The twin that IS kept is for a gutter-bucket flag whose anchor
  // resolved to nothing — `updateDocColumns` does not count those.)
  updateDocColumns();

  const ta        = pop.querySelector('.cmt-pop-note');
  const strip     = pop.querySelector('.thumb-strip');
  const attachBtn = pop.querySelector('.attach-btn');
  const fileInput = pop.querySelector('input[type="file"]');
  wireCapture(() => captureState, ta, strip, attachBtn, fileInput, el('rcard-' + id));

  /* One field, whose JOB changes with the type — not a second field that
     appears beneath the first. Picking `suggest wording` used to reveal a
     `.cmt-pop-repl` textarea below the note, which asked the reviewer to fill
     two boxes to say one thing and made the popover taller than the card it
     annotates. The type chips choose what the box means:

       changes / info  → the box is the note
       suggestion      → the box is the replacement wording, applied verbatim

     Anything already typed carries across the switch, because a described
     change ("cut the second clause") is usually a draft of the wording that
     replaces it. A suggestion's rationale is optional by schema
     (`_comment_fragment`: the wording alone is a full ledger row), so nothing
     is lost by not asking for one here. */
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
  // `nearest`, not `center`: a popover taller than the viewport aligns to its
  // top — where the type chips are — and a short one does not move the page at
  // all, so the prose is never yanked out from under the reader. `preventScroll`
  // on the focus, or the browser's own scroll-to-field undoes it and lands the
  // viewport on the textarea with the chips and the quote above the fold.
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
  // Dictation is the same act as typing here: it fills THIS box, and the save
  // above is still the only thing that makes a comment. The button turns the
  // microphone on and puts the caret back — from there the modal rule takes
  // over, because a focused note field is what makes speech dictation.
  const mic = pop.querySelector('.mic-btn');
  if (mic) {
    // Built after paintVoiceToggle last ran, so it paints its own live state.
    mic.classList.toggle('is-live', voiceIsOn());
    mic.onclick = () => startVoice(() => ta.focus());
  }
}

/* Scroll a node clear of the fixed bottom bar and the sticky masthead, by
   the smallest amount that does it. `scrollIntoView({ block: 'nearest' })`
   was the previous mechanism, and under `scroll-behavior: smooth` Chrome
   ignores `scroll-padding` for it — the composer opened with its save and
   cancel under the bar, and the page did not move. A plain `scrollBy` honors
   the behavior the page asks for, so the paddings are read and applied here. */
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

/* Marking used to happen twice — `renderHighlights` wrapped the anchors, then
   `markAndPin` wrapped them again and hung the numbers. Two passes could
   disagree about which span is note 3, so the margin's pass is now the only
   one; with the accordion wearing the margin too, the first has no surface
   left to serve and is gone. `markAndPin` is the single owner of both ends of
   the pairing. */

// Wrap the `n`th (0-based) text-node occurrence of `needle` in a <mark
// class=cls>. The ordinal is the anchor's own `occurrence`, so a comment on
// the third "retries" highlights the third one and the reviewer sees the span
// the stored offset names. Unchanged limitation from wrapFirst: a needle split
// across element boundaries (an inline <code> mid-phrase) lives in no single
// text node, so it is never matched — and because occurrenceInRendered counts
// over the flat text where it *is* present, an earlier straddling occurrence
// shifts this walk's count and the mark can land one occurrence late. Visual
// only; the stored offset is unaffected.
// Returns the <mark> it created (null if the needle never resolved), so the
// doc print can hang that note's pin off it without walking the tree twice.
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
  // Nothing matched inside a single text node. That is the common case for a
  // CODE anchor: highlight.js splits `time.sleep(0.3)` into six token spans,
  // so the phrase the reviewer selected lives in no one node and the walk
  // above silently marks nothing — a suggestion on a line of code drew no
  // highlight and no pin at all. Same for a prose phrase crossing an inline
  // `<code>` or `<em>`. Fall through to a Range, which can span elements.
  return wrapSpanning(root, needle, cls, n);
}

// Wrap the nth occurrence of `needle` even when it crosses element
// boundaries. Kept as the FALLBACK rather than the primary: `surroundContents`
// splits partially-selected elements, and diff mode's marks land inside
// diff2html's table markup where that is not a trade worth making unless the
// alternative is no mark at all.
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
    /* Partially-selected elements: extract (which splits them, each half
       keeping its own class) and re-insert under the mark.

       A PLACEHOLDER holds the spot. `extractContents` can remove the very
       nodes the range's start boundary points into, which collapses the range
       up to their parent — so a plain `range.insertNode(mark)` afterwards
       lands the mark as a SIBLING of the container the text came out of.
       Measured on a diff line: an emptied 506px `.d2h-code-line-ctn`
       followed by the text 506px to its right, a gap the width of the line.
       And it compounds — `markAndPin`'s teardown flattens a mark into
       whatever parent it currently has, so once the text is a sibling it
       stays one for the rest of the session. The slot is outside the
       extracted range, so it survives to say where the mark belongs. */
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
/* Reviewer replies to an open thread → continues the SAME cid thread. The reply
   rides as a comment on that cid (open, unsettled, flagged `reply`), so
   open_notes.update appends it as a new exchange and the agent answers again
   next round — a GitHub-style back-and-forth until the thread is settled. An
   emptied reply clears the pending one. Reply comments are kept out of the
   new-comment list (they live in their thread) but still count as active
   feedback, so the section can't be approved while a reply is pending. */
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
  // The grammar, not the print. `no-gutter` is a constant here rather than a
  // computed collapse: a question has no producer flags to rail, and the
  // margin always holds this question's verbs, so neither column's state can
  // change mid-session and updateDocColumns has nothing to decide.
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

// Grounds-classed recommendation badge (issue #175) — `grounds` is optional
// and, absent, renders the exact plain badge issue #114 shipped (no render
// change without it, the same guarantee `recommended_choice` itself carries).
// `sourced` keeps the badge ambient, relabeled — the citation itself rides in
// the question's own text/hint this iteration (see schema.py's QAQuestion).
// `inferred` renders nothing here on purpose: an opinion with no local
// provenance earns a click, not a glance — see buildQACard's `groundsReveal`,
// the disclosure it answers behind instead. `taste` never reaches this
// function: it never has a matching chip, since a `recommended_choice` can't
// coexist with it (validate_qa_input) — its own label is `tasteLabel` below.
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

  // recommended_choice is optional (issue #114) — undefined-safe by
  // construction: `c` is always a string, so this is false for every chip
  // on a question that never sets the field. Advisory only: the matching chip
  // gets a badge, nothing else (no pre-selection, no default focus, no
  // restyle as primary). The digit keycap is the same key the keydown handler
  // binds, so the keyboard layer is on the control rather than only in the
  // legend — 1-9, and nothing past the ninth choice.
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

  // The disclosure head IS the question, printed once, wrapping, numbered like
  // a catalog entry. The `<h2>` that used to restate it one line below is gone:
  // the head was clamped and dimmed as "an index line" pointing at that
  // heading, a mitigation that assumed the entry held something OTHER than the
  // duplicate — and a free-text entry holds a hairline and nothing else. The
  // number goes INSIDE `.card-title`: `.card-title-wrap` is `flex-direction:
  // column`, so a sibling span would stack the digit above the question.
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

  // Guarded: a choiceless question has no prose cell and therefore no chip
  // list. Both readers of `#qchoices-` carry the guard — this one and
  // `syncQACard`'s — or the one that does not throws on every sync of that
  // card, on a surface no test can execute.
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

/* The ONE definition of "this question has an answer" (#121).

   A free-text-only question can never set `choice` — there is no chip to
   click — so gating on `choice` alone made a typed note invisible to the
   progress stat, the dot, the auto-advance, the dispatch button, and, worst,
   the submit filter, which dropped the answer entirely rather than marking it
   unanswered. The reviewer's typed text never reached `answers.json`.

   That is `/viva-write`'s normal case, not its edge case: the interview asks
   the residual decisions the attachments could not answer, and those are
   exactly the ones with no fixed answer set to offer.

   Every reader routes through here rather than repeating the test, so the six
   places that ask the question can never disagree about it — the same rule the
   `choices` normalization above follows, for the same reason. */
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
  // Same footer as the review page, stating the same kind of thing: what
  // blocks the dispatch, and the keycap that performs it. An interview has no
  // judgment/facts axis — an answer is given or it is not — so the balance
  // rule fills with settled alone and the bare track is what nobody has
  // answered yet, which is the one honest way to draw "not yet decided".
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
// Between-rounds snapshot — the changes/info rows the reviewer just sent,
// captured from rState at submit time so the 'processing' view can echo
// them back verbatim while the agent revises. Deliberately in-memory only
// (never written to .viva/): a tab reload during revision re-boots into
// the prior round exactly as before, not into this card.
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
// bar so the reviewer can retry. fetch() resolves (never rejects) on a 4xx/5xx,
// so a non-2xx is turned into a throw here; and because the bar's `disabled`
// attribute is the in-flight signal the recap gate reads, a failed submit that
// left it set would strand the reviewer with no retry path but a page reload
// (which loses the round's verdicts). On success the buttons stay disabled —
// the round is genuinely in flight and the SSE 'processing'/'round' events
// drive the next view.
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
   btn-submit's ready click opens this index of every section — id, title,
   verdict dot + label, active-note count — instead of submitting; only
   #recap-confirm calls submitReview(false). `o` toggles it, Escape closes
   it, a row click closes-and-activates its section. Q&A ships no recap:
   the done → button stays wired straight to submitQA(false), and btn-skip
   keeps its direct submitReview(true) escape hatch. */
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
  // The confirm control mirrors btn-submit's readiness, so a recap opened
  // mid-review via `o` can't submit a round the bottom bar wouldn't. The
  // `.disabled` attribute is the in-flight signal: submitReview sets it
  // before the POST and the 'round' handler clears it, so mirroring it here
  // keeps a recap reopened after a submit (SSE still up, or dropped) from
  // re-arming a second POST that would duplicate the ledger rows.
  const ready = el('btn-submit').classList.contains('ready') && !el('btn-submit').disabled;
  el('recap-confirm').className = 'btn-submit ' + (ready ? 'ready' : 'disabled');
  el('recap-confirm').setAttribute('aria-disabled', ready ? 'false' : 'true');
  // Three states, not two, and the reason is printed rather than inferred.
  // `disabled` is the IN-FLIGHT signal (submitReview sets it, the 'round'
  // handler clears it); pending is the not-ready one. Derived separately from
  // `ready` above so that line stays the single readiness rule.
  const inFlight = el('btn-submit').disabled;
  // The same arithmetic the bar prints: deriveVerdict returns exactly one of
  // approved/changes/info/pending, so this equals updateReviewStats's
  // `remaining` and the two can never disagree.
  const pending = REVIEW_DATA.sections.filter(s => deriveVerdict(s.id) === 'pending').length;
  // The control that CAN act with sections still pending. `btn-skip` does the
  // same job in the bar, but the bar is inert behind this modal, so naming it
  // in copy would not be enough.
  const canSkip = pending > 0 && !inFlight;
  el('recap-skip').style.display = canSkip ? '' : 'none';
  el('recap-blocked').textContent = inFlight ? 'submitted — the agent is revising'
                                  : pending ? pending + ' of ' + REVIEW_DATA.sections.length + ' unreviewed'
                                  : '';
  el('recap-overlay').style.display = '';
  setBackgroundInert(true);   // trap focus + block interaction behind the modal
  // Focus the confirm when it can act, otherwise the close — NEVER the skip.
  // Opening on `skip rest & submit` made `o` then Enter dispatch a round with
  // every section unreviewed, unconfirmed; the escape hatch is one Tab away,
  // never the default. Runs AFTER both display flips above — focus() on a
  // `display:none` node silently no-ops, the same class of trap closeRecap's
  // comment records for `inert`.
  (ready ? el('recap-confirm') : el('recap-close')).focus();
}

// The recap is a modal (aria-modal="true"): mark everything behind it inert
// while it's open, so Tab can't walk into the sheet or bottom bar and a
// background click can't reach a card. inert removes the whole subtree from
// the tab order, which makes the overlay's own controls the only focusable
// region — a focus trap without hand-rolled Tab-wrap bookkeeping.
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
   #prefs-overlay mirrors the recap overlay's modal shape exactly
   (role="dialog" aria-modal, Escape/backdrop/close-button dismiss,
   setBackgroundInert while open) but is a second, independent surface: at
   most one of the two is ever open — openRecap() closes this one first,
   and vice versa here. Reachable in every mode (review, diff, qa), unlike
   the recap overlay, since preferences aren't review-specific. */
let _prefsTriggerEl = null;

function prefsIsOpen() { return el('prefs-overlay').style.display !== 'none'; }

function prefStatusLabel(status) {
  return status === 'standing' ? 'standing' : status === 'muted' ? 'muted' : 'candidate';
}

// Static recovery copy for a muted row — mute is one-way from this panel
// (decision prefs-inspector-1), so the CLI command that reverses it has to
// be visible on the row itself, not just known to exist. A still-visible
// badge on this round's card is not a sign the mute silently failed, but the
// copy makes no next-session claim either: `--status standing` has three
// readers, not one — `loop.py`'s standing_preferences(), `wait`'s printed set,
// (:146), and step 4's rewrite consult (:366) — so a mute during this round
// can still reach this same round's rewrite. The copy only says that
// badges already shown this round are a historical record.
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

// Mutates the one row's DOM in place — status text, mute button removed,
// muted note appended — never a list rebuild, so a mute never disturbs
// scroll position or any other row (journey step 4: "the same row, updated,
// not replaced or removed").
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
   Three states, cycled in this order: system → light → dark → system.
   "system" is the absence of the attribute, not a third value — the page
   falls back to the `prefers-color-scheme` media query, so a reader who never
   touches this control is unaffected and a reader who wants to hand control
   back can reach that state without clearing storage by hand.

   The pre-paint script in <head> is what applies a stored choice; this only
   changes it. Both write the same key, and both treat any other value as
   "system", so a corrupted entry degrades to following the OS. */
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
   viva is named after the PhD oral: the candidate submits WRITING and the
   examiner SPEAKS. The written defence is already here — the doc, the
   rewrites, the ledger. This is the examiner's half, and it is input only.

   THE INVARIANT: speech may command, it may never author.

     A verb carrying no text (approve, next, save, stop) acts immediately. It
     is a button press; there is nothing a recognizer can mis-transcribe into
     the record, and every one of them is reversible on screen.

     A note, question, or suggestion is STAGED in the comment composer, where
     the reviewer reads it and confirms. Nothing below calls `addComment`.
     That is not a convention, it is the reason the layer is safe: a ledger
     PRODUCT.md promises is verbatim must not carry a sentence the human never
     read. `tests/test_server_voice.py` holds the line.

   A spoken "save" is both the last word of the comment and the confirm of it.
   That is deliberate and it is not a hole: the staged text is on the screen,
   unedited, at the moment the reviewer chooses to say the word.

   THE MODAL RULE, one sentence: a focused note field makes speech dictation;
   nothing focused makes it grammar. `save`, `cancel` and `stop` work from
   inside a field anyway, or the microphone is hot with the caret in a textarea
   and no spoken word gets you out.

   Nothing here runs until a reviewer turns it on: no recognizer is
   constructed at load, and the control is not even drawn where the browser
   has none. PRODUCT.md principle 4, the same as every other layer. ══════ */

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
   phrase in the table is already written in. Known limitation: a hyphen
   INSIDE one of the first words of an utterance splits it in two here but not
   in the raw text, so the remainder handed to a carrying verb would drop a
   word. Recognizers emit commas and terminal stops, not mid-phrase hyphens,
   so this has never been observed; it is written down rather than guarded. */
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
   The transcript exists so nothing is ever swallowed in silence: every
   utterance prints here with the reading it got, INCLUDING the ones that
   matched no verb. A reviewer who cannot tell "heard nothing" from "heard
   something and ignored it" cannot trust the layer at all. */
function voiceSay(state, heard, read) {
  const strip = el('voice-strip');
  if (!strip) return;
  strip.style.display = '';
  strip.innerHTML = '<span class="vs-state">' + esc(state) + '</span>'
    + (heard ? '<span class="vs-heard">&ldquo;' + esc(String(heard).trim()) + '&rdquo;</span>' : '')
    + (read  ? '<span class="vs-read">' + esc(read) + '</span>' : '');
}

// Interim results are the recognizer still deciding. Shown so the reviewer can
// see it is hearing them — and `aria-hidden` so it is not ANNOUNCED, because a
// live region reading every partial guess aloud is unusable. The final
// reading, through voiceSay above, is what announces.
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
// Is there still a round on screen to command? The processing and complete
// views leave `REVIEW_DATA` set and `rState.active` null, which is exactly the
// state `voiceReopen` treats as "reopen where you were" — so without this a
// spoken verb walks last round's cards while the page reads "Claude is
// revising…".
function voiceRoundIsLive() {
  const shown = id => { const n = el(id); return n && n.style.display !== 'none'; };
  return !shown('processing-view') && !shown('complete-view');
}

function handleUtterance(raw) {
  const norm = normalizeUtterance(raw);
  if (!norm) return;
  /* The terminal and modal guards, in the keydown handler's own order and for
     its reason (#174). Speech is a SECOND input path into the same verdict
     state, so every guard that stops a keystroke has to stop an utterance too
     or the hole it closed is open again through the microphone. The two
     TERMINAL states also turn the microphone off, at their own source — a hot
     mic with nothing left to command is worse than an unresponsive one.
     Between rounds is not terminal: the next round lands in this same tab, so
     the utterance is refused and the microphone stays on rather than making
     the reviewer re-arm it every round. */
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
  /* Approving or skipping the LAST open card leaves nothing active, and every
     verb below needs a card. On a keyboard that is a shrug — you click one. A
     reviewer working hands-free has no click, so without this the answer to
     every remaining utterance is "no section is open", forever, including the
     verb they would say to get out. So the first verb spoken into that state
     REOPENS where they were and does nothing else: an ambiguous target is
     worth a second utterance, and approving a card nobody can see is not. */
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

// Reopen the last card a spoken verb touched — or the first one, when speech
// has not touched any yet. Carried sections have no accordion to open, so this
// walks forward to one that does rather than reporting success on a card that
// never appears.
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
  // `preventScroll`, like the opener's own focus: `openCommentPopover` has
  // already scrolled the whole popover into view, and a bare re-focus here
  // would scroll back to the field and undo it — leaving the type chips and
  // the quote above the fold with only save/cancel on screen.
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

/* Disclosed once, and disclosed rather than buried. The browser's recognizer
   is a network service: viva itself stays keyless, hosts nothing and stores no
   audio, but the audio does leave the machine, and "local" is a promise this
   page makes. The reviewer decides with the fact in front of them. */
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
       session on its own after a silence. The counter is the storm guard: a
       recognizer that cannot stay up ends immediately every time, and without
       a ceiling that is an invisible infinite loop. It resets on every result,
       so a long review with real speech in it never reaches the ceiling. */
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
  /* The label states which state is ON, so the accessible name has to say what
     the button DOES — the same rule the theme toggle follows, for the same
     reason: otherwise a screen reader hears a state and cannot tell it from an
     action. */
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
// Soft, client-side-only timeout for #processing-view (#119). Neither
// 'round' nor 'complete' is guaranteed to arrive promptly: the qa→review
// hand-off's wait is bounded by an external caller's own synthesis step
// (docs/headless-contract.md §6/§7), not by an LLM turn in this process, and
// the SSE connection stays open the whole time — es.onerror only fires on an
// actual connection drop, so it can't detect a merely-slow hand-off. This
// constant is deliberately in the 15-30s range the design doc suggests: long
// enough that a normal in-session revise rarely trips it, short enough that
// a stalled hand-off isn't a silent, indefinite spinner.
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

// A position: fixed banner prepended to document.body, tokenized colors, so a
// human reads "banner at the top of the tab = something needs my attention".
// Skipped outright if the connection has actually dropped in the interim: the
// dead-session overlay is the harder, more specific signal, and it is a
// full-screen scrim this banner's z-index would float above and contradict.
function showStillWaitingBanner() {
  processingTimer = null;
  if (deadSessionIsOpen()) return;
  const b = document.createElement('div');
  b.id = 'processing-wait-banner';
  b.className = 'error-banner banner-info';
  b.textContent = 'Still waiting — check the terminal.';
  document.body.prepend(b);
}

/* A round arrived that this tab cannot render. The server refuses such a body
   at `/next-round` — that is the boundary and this is NOT a second copy of it.
   This is the strand backstop: the cost of the server ever being wrong is a tab
   frozen on "the agent is revising" with the throw buried inside initReview and
   nothing on screen, which is precisely the failure that was reported. The
   round is refused before any state moves, so what stays on screen is the round
   the reviewer already has, and the banner says why. Full `--orange` ink, not
   `.banner-info`: this is a broken payload, not a slow one. */
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
   A banner was the wrong shape for this. The tab kept every control live,
   so a reviewer could work a whole round into a page whose submit POSTs into
   a socket that is gone. Three layers block it, each covering what the
   others cannot: `inert` takes the pointer and Tab out of the background,
   the document keydown listener (which `inert` never reaches) swallows the
   shortcut layer, and sendSubmit refuses outright.

   Not dismissible by the reviewer — every dismissal returns them to the dead
   tab this exists to stop them working in. es.onopen is the one thing that
   takes it down, because a connection that came back means the session was
   never dead: a lid-close blip must not lock a reviewer out of a live server
   and lose the round's in-memory verdicts. */
function deadSessionIsOpen() { return el('dead-overlay').style.display !== 'none'; }

function showDeadSession() {
  if (deadSessionIsOpen()) return;
  // Close the other modals FIRST: they sit outside setBackgroundInert's
  // subtree, and both restore focus into the background on close — after
  // this overlay takes focus, that would pull it straight back out.
  closeRecap();
  closePrefsPanel();
  closePalette();
  // And the microphone. `inert` on #paper takes the pointer and Tab away from
  // the voice toggle, and the document keydown listener returns above the
  // Escape-stop branch — so a mic left hot here is one the reviewer cannot
  // reach any control to turn off.
  stopVoice('the session ended');
  // The one command this tab can honestly name. `doc_file` is a real target
  // only in review mode — parse_diff.py writes review_target.py's LABEL there
  // ("PR #187", "working tree"), and a qa payload has no doc at all — so
  // those two get the generic line rather than a command that would not run.
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

// Renders #processing-view for its two variants: the between-rounds card
// (pulsing dot, `REV 0N submitted — the agent is revising`, the reviewer's
// just-submitted changes/info rows verbatim) when submitReview snapshotted
// rows, else the minimal processing line — a qa submit never snapshots, and
// a zero-row review submit has nothing to echo.
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
  // The same note the reviewer wrote in the margin a moment ago, in the same
  // grammar — `info` is an open fact and takes the fact ink, everything else
  // is the reviewer's judgment and takes theirs. A second row vocabulary for
  // the same objects is what `.pr-*` was.
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
    // Retire the previous round's controls with its cards. The dispatch button
    // is dead here, and `skip rest & submit` is worse than stale — it is live,
    // and would POST a second submit for a round already in flight. The bar
    // itself stays: theme, preferences and voice remain legitimately usable
    // while waiting. Restored by the 'round' handler, the only way out.
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
    // Guard BEFORE routing. Every statement below this point either reads
    // `data.sections` or overwrites state the current round is still using, so
    // a payload this tab cannot render has to be turned away while the previous
    // round is still whole. See showRoundRefused for why the client refuses at
    // all when the server already validates.
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
    // A qa → review hand-off (#109) lands here too — the qa session this tab
    // may have been showing is done; drop its state so leftover QA_DATA/
    // qState.active can't be picked up by qa-branch logic (keydown handler,
    // updateQAStats/submitQA) once REVIEW_DATA cards are what's on screen.
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
    // Hide qa-view unconditionally rather than relying on a prior
    // 'processing' event having already done so: a caller that POSTs a
    // review round-1 payload to a still-running qa server without the browser
    // ever seeing a 'processing' event first (e.g. this tab's SSE connection
    // reconnected mid-transition and missed it) would otherwise leave qa-view
    // visible underneath the review cards.
    el('qa-view').style.display         = 'none';
    // ...and its page class with it. `mode-diff` happens to out-order
    // `mode-qa` in the stylesheet today, so a stale class would not clamp a
    // diff round's 1600px page — but that is source order doing the work, and
    // source order is not a rule anyone should have to know.
    document.body.classList.remove('mode-qa');
    el('review-view').style.display     = '';
    // The whole bar restoration in one place. #foot-seg and #stat-pending need
    // no explicit restore: initReview() above runs updateReviewStats →
    // reviewFootSeg → renderFootSeg, which sets `bar.style.display` on both of
    // its branches, and rewrites #stat-pending.
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
      // Absent counts DROP the line, never degrade it. The summary is
      // caller-supplied and unvalidated (`json.loads(body) if body.strip()
      // else {}`), and a real caller already omits `sections_total`, so
      // `? sheets · 1 revision` was reachable — printed into the APPROVED
      // stamp, the product's one uninterrupted moment. Both counts or
      // neither: a half-known line is the same defect. `display:none` as well
      // as an empty string, because `.stamp-sub` carries `margin-top: 2px`
      // and an empty div would still spend it.
      const counted = typeof r === 'number' && typeof s === 'number';
      stampSub.textContent = counted
        ? `${s} sheet${s !== 1 ? 's' : ''} · ${r} revision${r !== 1 ? 's' : ''}`
        : '';
      stampSub.style.display = counted ? '' : 'none';
    }
    const stampMeta = el('stamp-meta');
    if (stampMeta) stampMeta.textContent = 'viva · ' + new Date().toISOString().slice(0, 10);
    el('complete-detail').textContent   = rev != null
      ? `${rev} section${rev !== 1 ? 's' : ''} revised`
      : '';
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

  // EventSource retries on its own, and onerror fires on every attempt — so
  // "dropped" and "gone" look identical from here. This is the only thing
  // that tells them apart after the fact: a reconnect that succeeded means
  // the server outlived the drop, and the tab is live again.
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
  // Nothing on this page can reach the server any more (#174), Escape
  // included — the dead-session overlay is the one modal that does not close.
  // `inert` blocks the pointer and Tab but never this document-level
  // listener, so without a blanket swallow a/c/i and ⌘+Enter would keep
  // mutating verdict state behind a full-screen scrim.
  if (deadSessionIsOpen()) return;
  // ⌘K opens the palette from anywhere, including from inside a textarea —
  // it is the one global verb, and a reviewer mid-reply is exactly who wants
  // "jump to next open thread" without reaching for the mouse. This sits
  // ahead of the TEXTAREA/INPUT guard for that reason.
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
  // cancels; a box with a draft only gives up focus, so Escape never loses
  // typed text. Never over the modals that own Escape themselves.
  if (e.key === 'Escape' && !prefsIsOpen() && !(REVIEW_DATA && recapIsOpen())) {
    const pop = document.querySelector('.comment-popover.is-open');
    if (pop) {
      e.preventDefault();
      const ta = pop.querySelector('.cmt-pop-note');
      if (ta && ta.value.trim()) ta.blur(); else pop.querySelector('.cmt-cancel')?.click();
      return;
    }
  }

  /* Escape stops listening from ANYWHERE, and like ⌘K it sits ahead of the
     TEXTAREA/INPUT guard on purpose — that is the whole point of it. Staging a
     spoken comment puts the caret in a textarea, so every key below this line
     is unreachable exactly when the microphone is hot and the reviewer most
     wants it off. Never over the two modals that own Escape themselves. */
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
  // can never reach the card/QA shortcuts behind the backdrop. inert (on
  // #paper) blocks pointer/Tab into the background but not this document
  // keydown listener, and focus inside the panel sits on #prefs-close or a
  // .pref-row — neither TEXTAREA nor INPUT — so the guard above this block
  // doesn't catch it either; this is the only thing that does.
  if (prefsIsOpen()) return;

  /* `v` is a MODE toggle, so it belongs in the theme/palette tier rather than
     with `a`/`c`/`i`: it is not about the card under the reader, it works in
     the interview as well as the review, and gating it on `rState.active`
     would make it dead on a page with nothing expanded. */
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
      // controls, and browser chrome stay keyboard-reachable (#75).
      // Shift+Tab is always native: backward is a direction, not a verb.
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
  // keystroke can never route through the qa branch — and flip btn-submit to
  // 'ready' via updateQAStats(), arming the class-gated click handler (the
  // recap gate) early — while review cards are what's on screen.
  if (!REVIEW_DATA && QA_DATA && qState.active) {
    const q = QA_DATA.questions.find(q => q.id === qState.active);
    if (q) {
      const n = parseInt(e.key, 10);
      if (!isNaN(n) && n >= 1 && n <= q.choices.length) {
        e.preventDefault();
        pickQAChoice(qState.active, q.choices[n - 1]);
        return;
      }
      // `c` confirms, the way `a` approves a section — the keycap is printed
      // on the button itself, so the keyboard layer is on the control rather
      // than only in the legend. Free here: `c` is review's request-changes,
      // and this whole branch is guarded on `!REVIEW_DATA`.
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

// Runs immediately (not on DOMContentLoaded): this inline script tag sits at
// the very end of <body>, after every element it references, so the DOM is
// already parsed by the time it executes. Waiting for DOMContentLoaded would
// needlessly serialize this loopback /input fetch behind the five `defer`red
// /vendor <script> tags above — the HTML spec guarantees `defer` scripts finish
// before DOMContentLoaded fires, so ~570KB of renderer bytes would stall a
// fetch that has nothing to do with them.
el('btn-skip').disabled   = true;
el('btn-submit').disabled = true;

// Titleblock's doc-path/doc-title cells — shared by the initial review/diff
// boot (bootReviewMode) and the in-place 'round' SSE hand-off/advance, so a
// qa→review hand-off (#109) populates them exactly like a fresh review boot
// does instead of leaving them at their qa-view blank default.
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

// The preferences fetch is awaited alongside /input, before cards are ever
// built — the badge-to-entry link (annotStripHTML's PREFS_BY_ID lookup)
// resolves on first paint rather than upgrading a beat later. A failed or
// malformed preferences fetch degrades to an empty list rather than
// blocking or erroring the round-data boot: every preference badge then
// falls back to its plain, non-interactive rendering — the same degrade an
// unmatched [id] token already gets.
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
    // Ships hidden (same treatment as the confidence sort toggle,
    // references/producers.md, Confidence triage — "a doc with none hides
    // the toggle entirely"): a clone
    // with an empty/absent store has nothing to inspect or mute, so the
    // control stays off rather than opening onto an empty panel.
    el('prefs-toggle').style.display = PREFS_DATA.length ? '' : 'none';
    el('btn-skip').disabled   = false;
    el('btn-submit').disabled = false;

    if (data.mode === 'review') {
      REVIEW_DATA = data;
      bootReviewMode(data, 'review', '');
    } else if (data.mode === 'diff') {
      REVIEW_DATA = data;
      document.body.classList.add('mode-diff');
      // diff2html's stylesheet is mode-specific — injected here rather than
      // shipped as a render-blocking <link> in <head>, so review/QA sessions
      // never pay a fetch for a diff-rendering stylesheet they can't use.
      // (The companion diff2html script tags stay in <head>: they're defer,
      // so they don't block, and the boot-time d2h-pending retry keys off
      // their loads.) renderDiffHunk gates on link.sheet, so a card opened
      // before this stylesheet lands falls back to the fenced view; the
      // retry attached here — where the <link> actually exists — upgrades
      // it once the CSS arrives. Served from this server's own /vendor route;
      // the version in the path is the pin (assets/vendor/README.md).
      const d2hCss = document.createElement('link');
      d2hCss.id = 'diff2html-css';
      d2hCss.rel = 'stylesheet';
      d2hCss.href = '/vendor/diff2html-3.4.56.min.css';
      document.head.appendChild(d2hCss);
      retryOnceScriptsLoad(['diff2html-css'], '.section-content.d2h-pending');
      bootReviewMode(data, 'diff', 'diff');
    } else {
      // `choices` is OPTIONAL on the wire — a question that omits it renders a
      // free-text field only (references/qa.md). Normalized once, here, where
      // the payload enters the client: three readers downstream take it as a
      // list (the chip builder's `.map`, the palette's `.slice`, the digit
      // handler's `.length`), and an absent field made the FIRST of them throw
      // during render — taking the whole interview down, not just its card.
      // Guarding each reader instead would leave the fourth one to rediscover
      // this (CLAUDE.md: fix data at the boundary, never at the point of use).
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
)

_HTML_BYTES = HTML.encode()

_shutdown = threading.Event()
_input_data: dict = {}
_output_path: str = ""
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
    """Cumulative per-section revision count for the round being served.

    Server-side, wire-only derivation (issue #141) — never written to any
    round file, no `schema.py` field. Walks the *historical* rounds
    `1..round_num-1` on disk (the same `review-input-r{k}.json` naming
    `scripts/revision_history.py` already depends on to build the sign-off
    ledger) plus the just-arrived round's own in-hand `sections`, and counts
    one revision per round a section carried a `diff`.

    Returns `(counts, partial)`. A missing round file, one whose JSON fails
    to parse, one that doesn't decode to a dict, or one whose `sections` key
    isn't a list, contributes zero revisions for that round *and* sets
    `partial = True` — every round 1..round_num-1 has to actually be read to
    trust the cumulative total as exact, so any round this loop couldn't
    make sense of turns every count this call returns into a lower bound,
    not the same tolerant "just skip it" `scripts/revision_history.py` has
    for a session with gaps in its round-file pairs. Callers must surface
    that, not print the lower bound as if it were exact
    (corrupt-round-file-silent-undercount).

    Predicate is `s.get("diff") is not None` — presence with a non-null
    value — not Python truthiness of the value itself:
    `parse_sections.py`'s `_compute_diffs` can legitimately write an empty
    `diff: []` when two rounds' content differs only in a way
    `str.splitlines()` collapses (e.g. a trailing-newline-only edit), and the
    card's own `.rev-tri` trigger (`section.diff ?` in JS) treats that
    exactly like any other diff — JS arrays are truthy regardless of length.
    `bool([])` is Python-falsy, so counting on truthiness would silently
    undercount relative to what the triangle itself already shows.

    Counted per round via a `set` of `section_key`s, not a per-occurrence
    increment: `parse_sections.py` assigns `id` positionally with no title
    uniquification, so two same-level headings that normalize alike (two
    `## Notes`) both carry their own `diff` after one rewrite. Incrementing
    once per matching section, instead of once per distinct key, would double
    (or N-tuple) count a round that only happened once.
    """
    counts: dict[str, int] = {}
    partial = False
    for k in range(1, round_num):
        try:
            hist = json.loads((viva_dir / f"review-input-r{k}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            partial = True
            continue
        if not isinstance(hist, dict):
            partial = True
            continue
        hist_sections = hist.get("sections")
        # A round file that parses as valid JSON but carries "sections":
        # null (or any non-list) is exactly as unusable as a missing file —
        # guard it the same way the in-hand round-N path below already does,
        # so it degrades into the `partial` signal instead of raising
        # (`for s in None` -> TypeError, uncaught by the `except` above and
        # fatal to both GET /input and the /next-round SSE push).
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
    """Return `data` with a `revision_count` attached to each section whose
    cumulative count (`_revision_counts`) reaches 2+ — the threshold the
    card's `△ NN`-suffix multiplier renders at (issue #141). A section below
    that threshold, or a Q&A payload (`sections` absent/not a list), passes
    through unchanged. Functional: never mutates `data` or any section dict
    in place, and writes nothing to disk — the served JSON response is the
    only place this key exists, mirroring `ledger`'s serve-time-only
    precedent (`GET /input`, schema.py's docstring).

    When `_revision_counts` couldn't read every historical round file, the
    counts it returned are a lower bound, not an exact figure. Every section
    with a `diff` this round — the same predicate `.rev-tri` itself renders
    on (`section.diff ?` in JS) — gets `revision_count_partial: True`, not
    only the ones that clear the 2+ threshold: a round this call couldn't
    fully read might be exactly the round that would have tipped a
    below-threshold section's count over 2, and silently showing that
    section's plain triangle with no signal at all would be the worse half
    of corrupt-round-file-silent-undercount — the multiplier vanishing
    entirely instead of merely under-reporting. The client renders the
    `>= 2` case as "≥N revisions, partial history" and the `< 2` case as a
    number-free "partial history" caveat (`revTriTooltip`, `server.py`'s
    HTML) — never a bare, possibly-wrong number either way."""
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
        # Server-owned wire fields, not a pass-through: strip any
        # `revision_count`/`revision_count_partial` a caller's payload
        # happened to carry so the served value is always exactly what
        # `_revision_counts` just computed — never stale data.
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
    """Tolerant read of `.viva/preferences.json` for the two preferences
    routes below. Deliberately NOT `preferences._load` — that helper calls
    `sys.exit()` on a parse failure, correct for a one-shot CLI invocation
    but fatal here: a single corrupt store would take the whole review
    server down mid-session. Missing, unparseable, AND parseable-but-wrong-
    shape (a hand-edited `[]`, `null`, or `{"preferences": []}`) all degrade
    to an empty store (PRODUCT.md principle 4, "No-op when absent") — a
    shape check matters here because `preferences.select()`'s own
    `_normalize` only guards `set_status`'s write path, not this read path,
    and `store.get("preferences", {}).values()` on a non-dict raises."""
    path = viva_dir / "preferences.json"
    if not path.exists():
        return preferences.empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return preferences.empty_store()
    if not isinstance(data, dict) or not isinstance(data.get("preferences"), dict):
        return preferences.empty_store()
    # A per-entry non-dict value (as opposed to the container shape above)
    # would still crash `select()`'s `p.get("status")` — drop those entries
    # rather than the whole store, so one hand-edited bad row doesn't hide
    # every other valid preference.
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
    """Turn inline base64 `images` on each submitted item into written files.

    Walks review `sections` and Q&A `answers`, plus `comments[]` inside each
    section. For each image: validates the declared MIME against
    ALLOWED_IMAGE_MIMES, base64-decodes the data, enforces MAX_IMAGE_BYTES, and
    writes it under `<output dir>/attachments/` with a SERVER-GENERATED filename
    `{prefix}-{safeId}-{i}.{ext}`, where the prefix follows the item's kind:
    review `sections` use `r{rnd}` (rounds start at 1), Q&A `answers` use `qa`
    (there is no round concept in Q&A). Comment images inside sections use the
    same `r{rnd}` prefix as their section. Surviving paths are collected into the
    item's `attachments` list. Invalid, oversized, or undecodable images are
    dropped silently. The `images` key is always removed. Mutates and returns
    `data`.
    """
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
    `recommended_choice` (issue #175's accept-rate instrumentation).

    Deliberately server-side rather than client-side: the browser's `.choice`
    dereferences are a closed set `test_server_qa_choiceless.py` pins (every
    place that treats `.choice` as the answered-signal is enumerated there),
    and this is a value comparison, not an answered-gate, so it does not
    belong in that set. `questions` is the round's own recorded
    `QAInput.questions` — read server-side rather than trusting anything the
    client posted, so a `recommended_choice` never carries a value the client
    could have forged. Additive and idempotent: sets `accepted_recommendation`
    only on an answer whose question actually has a `recommended_choice`,
    never invents the key otherwise. Mutates and returns `data`.
    """
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

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", ""):
            self._send(200, "text/html; charset=utf-8", _HTML_BYTES)
        elif path in _VENDOR_ROUTES:
            # Exact-match dict lookup, not a filesystem join on request data:
            # `filename` is a literal from `_VENDOR_ASSETS`, so `/vendor/../…`
            # (and its percent-encoded spelling, which `urlparse` leaves
            # undecoded) simply misses the table and 404s below. Read per
            # request rather than at import — startup stays free of ~570KB of
            # I/O for assets a session may never open.
            filename, ctype = _VENDOR_ROUTES[path]
            try:
                body = (_VENDOR_DIR / filename).read_bytes()
            except OSError:
                # A truncated install: 404 rather than a traceback, so the
                # page's md-raw/d2h-pending fallbacks take over.
                self._error(404, "vendor asset missing: " + filename)
                return
            # The version is in the path, so these bytes are immutable for this
            # URL — an upgrade moves the URL rather than changing what it serves.
            self._send(200, ctype, body,
                       cache_control="public, max-age=31536000, immutable")
        elif path == "/input":
            # Snapshot both under the lock, then do `_revision_counts`' N-1
            # historical-round disk reads outside it — mirrors /next-round's
            # `ledger_snapshot` pattern (below). `_input_data` is rebound, never
            # mutated in place (see /next-round), so a bare reference is a valid
            # snapshot; `_ledger` is appended to in place (/submit), so it needs
            # an actual copy or a concurrent append would race the `json.dumps`
            # below. The round files `_revision_counts` reads are written by the
            # pipeline process, not this server, so `_data_lock` never protected
            # them — safe to read after releasing it.
            with _data_lock:
                data_snapshot = _input_data
                ledger_snapshot = list(_ledger)
            body = json.dumps({**_with_revision_counts(data_snapshot, _viva_dir),
                               "ledger": ledger_snapshot,
                               "repo": _viva_dir.parent.name}).encode()
            self._send(200, "application/json", body)
        elif path == "/preferences":
            # Every preference, every status, label-sorted — the in-page
            # panel's read (#142). A missing/corrupt store degrades to an
            # empty list rather than an error (see _load_preferences_store).
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
        """Shared loopback-only guard for every caller-facing POST endpoint:
        reject a present, non-loopback `Origin` header (403, defense-in-depth
        against a malicious page driving a local write sink via CSRF) and cap
        `Content-Length` at `cap` (400 if not an integer, 413 if over). Sends
        the error response itself and returns None on rejection; otherwise
        returns the validated length for the caller to `self.rfile.read()`."""
        origin = self.headers.get("Origin", "")
        if origin:
            # Exact host, never a prefix: `http://127.0.0.1.attacker.tld` is an
            # ordinary A record whose Origin literally starts with
            # `http://127.0.0.1`, so a prefix test admits an attacker-controlled
            # page to every write sink here — including a forged all-approved
            # `/submit` that the finish guard would then honour, since the
            # verdicts on record genuinely say approved.
            o = urlparse(origin)
            if o.scheme != "http" or o.hostname not in ("127.0.0.1", "localhost"):
                self._error(403, "forbidden origin")
                return None
        # A cross-origin `fetch` with `Content-Type: text/plain` is a *simple*
        # request: no preflight, so a page that never sees our 403 can still
        # deliver the body. Requiring JSON forces a preflight this server does
        # not answer, which is what actually stops the send.
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
        global _input_data, _output_path, _last_verdicts
        parsed = urlparse(self.path)
        path   = parsed.path
        params = parse_qs(parsed.query)
        if path == "/submit":
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
                # Snapshot for /complete's finish guard, taken here under the
                # same lock that guards `_input_data` so the two always describe
                # the same round. Same shape gate as the validation above: a Q&A
                # `answers` payload is not a verdict set.
                if "sections" in data:
                    _last_verdicts = data
                titles = {s.get("id"): s.get("title", "")
                          for s in _input_data.get("sections", [])}
                # Snapshotted under the same lock, for the same reason `titles`
                # is: a QA `answers` payload's recommendations are read off
                # the round actually on record, never off anything the client
                # posted (issue #175's accept-rate instrumentation, below).
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
        elif path == "/next-round":
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
            # `output` travels in the JSON body like every other POST field. The
            # legacy `?output=` query param is still honored as a fallback.
            # ORDER IS LOAD-BEARING: the missing-`output` refusal stays AHEAD of
            # the shape validation — `test_server_api.py`'s missing-output case
            # POSTs a structurally valid round and pins that error text.
            output = new_data.pop("output", None) or params.get("output", [None])[0]
            if not output:
                self._error(400, "missing 'output' in body")
                return
            # EVERY body, not only one that happens to carry `sections`. The
            # shape gate that used to stand here let a round nested one level
            # deep (`{"round": {...}, "output": …}`) through untouched: the
            # server answered `{"ok":true}`, replaced `_input_data` with it, and
            # pushed a sections-less `round` event that threw inside the
            # client's initReview — a tab stuck on "the agent is revising"
            # forever with no error on either side. `/next-round` is
            # review-shaped only; a `questions`-shaped body is refused here too,
            # and that is correct, since the `round` SSE handler has always
            # assumed review shape (`REVIEW_DATA = data; QA_DATA = null`).
            try:
                schema.validate_review_input(new_data)
            except ValueError as e:
                self._error(400, f"invalid review-input: {e}")
                return
            # The launch mode gates which round shape may replace the served
            # one (#126). The browser stamps `mode-diff` and injects the
            # diff2html stylesheet ONLY at boot (`data.mode === 'diff'`); the
            # `round` SSE handler does neither, so a diff payload landing here
            # on a non-diff server renders raw fenced code at review width — a
            # broken tab behind an `{"ok":true}`. Refused rather than
            # re-stamped: a caller whose two mode declarations disagree has a
            # bug this server cannot repair from one side. Absent `mode` reads
            # as "review", the browser's own default. A qa-launched server
            # hands off to review and to nothing else (§7 of the contract),
            # which is what makes the hand-off line below true by construction.
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
                # Unified Q&A → review session (#109): a qa-originated review
                # round carries no distinguishing field in the wire payload —
                # ReviewInput's shape is deliberately unchanged by that story:
                # schema changes were out of scope. The signal instead is
                # operational and inferred here, never persisted: the prior
                # round on this server was Q&A-shaped (`questions`) and this
                # one is review-shaped (`sections`). #111's headless-contract
                # should describe this session type as "a review round POSTed
                # to a server launched with --mode qa", not as a payload field.
                handoff = "questions" in _input_data and "sections" in new_data
                # Normalize on the way in, the same as the startup boundary: an
                # absent `round` renders as the literal "REV undefined" and
                # turns the author-answered freshness test into a NaN
                # comparison that silently never matches.
                _input_data = schema.default_round(new_data)
                _output_path = output
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
        elif path == "/complete":
            length = self._check_origin_and_length(MAX_SUBMIT_BYTES)
            if length is None:
                return
            body = self.rfile.read(length) if length else b'{}'
            try:
                summary = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError:
                summary = {}
            # The finish guard. "Nothing is auto-accepted" is a hard product
            # line, and a check that lives only in `loop.py finish` is a norm the
            # next caller walks around — so the server refuses on its own too,
            # asking `schema.round_is_complete`, the one predicate both processes
            # share.
            with _data_lock:
                round_input = _input_data
                submitted   = _last_verdicts
            # Two sessions are exempt, and shape alone does not identify them.
            # Q&A carries `questions`, never `sections`, so the shape test
            # excepts it. Diff mode does not: `parse_diff.py` emits `sections`,
            # and `viva-diff/SKILL.md:109-113`'s empty-re-diff finish signs off
            # with `changes` verdicts on record by design — the diff reached zero
            # because a hunk was reverted at the reviewer's request, not because
            # every hunk was approved. Gating it would 4xx a legitimate finish,
            # leak the server, and strand the tab on the processing card.
            # Closing that carve-out needs an explicit resolved-empty signal
            # from viva-diff, which belongs to another story.
            # The exemption keys on the *launch* mode, not the round payload's
            # `mode`. `_input_data` is replaced wholesale from `/next-round`'s
            # body, and no validator inspects `mode` — so gating on it let any
            # caller send `{"sections": [...], "mode": "diff"}` and then sign off
            # with zero verdicts, defeating the invariant this guard exists to
            # enforce. `--mode` is an argparse choice fixed at startup and
            # unreachable from any request.
            if "sections" in round_input and _launch_mode != "diff":
                if submitted is None:
                    self._error(400, "no verdicts submitted for this round — "
                                     "nothing to complete")
                    return
                if not schema.round_is_complete(round_input, submitted):
                    # `round_is_complete` above is the gate; the detail below is
                    # only the message, and it follows the predicate rather than
                    # deciding anything. A round's `pass` ADDS a conjunct to the
                    # all-approved base, so a refusal with nothing pending is
                    # now reachable — reporting "0 of N not approved" there would
                    # send the caller to re-present a round the human already
                    # approved, instead of to the conjunct that held it.
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
                        # Before the pass branch: an empty round is refused by
                        # the base rule, and naming a conjunct here would blame
                        # a `architecture`/`line` pass that adds none.
                        why = "the round carries no sections to approve"
                    elif pending:
                        why = ("%d of %d section(s) not approved"
                               % (pending, len(sections)))
                    elif kind:
                        # The recovery is the next round, not a merge into this
                        # round's file: the round this process serves was loaded
                        # once and is replaced only by `/next-round`, so a check
                        # answered on disk under it is one this guard never sees.
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
        elif path == "/abandon":
            # The shutdown route with no sign-off meaning: `loop.py abandon` is
            # a different process from the one that launched the server (start
            # detaches it), holds no child handle, and `server.url` carries a
            # URL and nothing else — so abandon reaches the server over HTTP,
            # not by signal. Deliberately *not* /complete: no `complete` SSE
            # event (the browser's `es.onerror` fires when `_shutdown` releases
            # the /events wait, which is the honest "connection lost" signal for
            # a session that was dropped, not finished) and no 2-second grace.
            length = self._check_origin_and_length(MAX_SUBMIT_BYTES)
            if length is None:
                return
            if length:
                self.rfile.read(length)  # drain: unread body turns close() into RST
            self._send(200, "application/json", b'{"ok":true}')
            _shutdown.set()
        elif path == "/preferences/mute":
            # Second, narrow writer of `.viva/preferences.json` (#142) —
            # flips one existing preference to `muted` via the same
            # `preferences.set_status()` the CLI's `set --status muted`
            # already calls. Doesn't restrict by current status (neither
            # does `set_status` itself); the client only ever renders the
            # mute control on a `standing` row. Un-muting stays CLI-only —
            # scripts/preferences.py's own docstring documents the split.
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
        else:
            self._error(404, "not found")

    def _send(self, status: int, content_type: str, body: bytes,
              cache_control: str = "") -> None:
        """Send one response. `cache_control` is opt-in and defaults to absent:
        every other endpoint here serves live session state, and only the
        version-stamped /vendor routes are safe to cache."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache_control:
            self.send_header("Cache-Control", cache_control)
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
    # SIGTERM joins SIGINT on one handler: Ctrl-C is the human's exit, and
    # `proc.terminate()` is the headless parent's (#125). Unhandled, SIGTERM is
    # fatal — the process dies at -15 and the `finally` below never unlinks
    # `server.url`, leaking it into the next launch's liveness guard.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: _shutdown.set())
    _viva_dir = Path(args.input).resolve().parent
    # Resolve the preferences store path and update _HTML_BYTES with the
    # absolute path, mirroring the pattern for _PREFS_SCRIPT_PATH above.
    _PREFS_STORE_PATH = str(_viva_dir / "preferences.json")
    _PREFS_STORE_PATH_JS = _PREFS_STORE_PATH.replace("\\", "\\\\").replace("'", "\\'")
    _HTML_BYTES = HTML.replace("__PREFS_STORE_PATH__", _PREFS_STORE_PATH_JS).encode()
    _input_data = load_input(args.input)
    # Validate the input on read at the boundary, keyed on the LAUNCH MODE —
    # the same thing `/complete`'s guard keys on, and for the same reason:
    # `--mode` is an argparse choice fixed at startup, while the payload's own
    # shape is whatever the caller wrote. Keying on shape meant a file that
    # carried neither `sections` nor `questions` was validated by nobody, so
    # `--mode review` booted a server that served garbage and bricked the tab
    # exactly as the `/next-round` hole above did. A shape/mode mismatch now
    # exits 1 at launch instead of painting a view that cannot render.
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
