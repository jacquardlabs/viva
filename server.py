#!/usr/bin/env python3
"""viva — section-by-section markdown review server.

Usage:
  python server.py --mode review --input .viva/review-input-r1.json --output .viva/review-r1.json
  python server.py --mode qa     --input .viva/qa-input.json        --output .viva/answers.json
  python server.py --mode session --session-id ID --input .viva/qa-input.json --output .viva/answers.json
"""
from __future__ import annotations  # 3.8-safe `X | None` hints (CI matrix runs 3.8)

import argparse
import base64
import json
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
# location, never $VIVA_DIR (SKILL.md never exports it). Escaped for
# embedding in the JS single-quoted string literal in
# `assets/app/js/06-submit-prefs-theme.js`.
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
    # The mono face. Referenced from an `@font-face { src: url(...) }` in
    # `assets/app/css/01-foundation.css` rather than from a `<script src>` —
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

# The frontend, composed from assets/app/ at import (#203). The order is
# load-bearing: the CSS cascade and the JS's cross-file hoisting follow it.
_APP_DIR = Path(__file__).resolve().parent / "assets" / "app"
_APP_PARTS = (
    "shell-head.html",
    "css/01-foundation.css",
    "css/02-status-ledger-slip.css",
    "css/03-cards.css",
    "css/04-doc-grid.css",
    "css/05-controls.css",
    "css/06-overlays.css",
    "shell-body.html",
    "js/01-core.js",
    "js/02-review-cards.js",
    "js/03-print.js",
    "js/04-comments.js",
    "js/05-palette-qa.js",
    "js/06-submit-prefs-theme.js",
    "js/07-voice.js",
    "js/08-boot.js",
    "shell-tail.html",
)
HTML = "".join((_APP_DIR / part).read_text(encoding="utf-8")
               for part in _APP_PARTS).replace("__PREFS_SCRIPT_PATH__", _PREFS_SCRIPT_PATH_JS).replace(
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
# The `--input` modes each launch mode boots on (#224) — a table, not
# equality, since a launch mode may boot on another mode's input. The
# first entry is what a mode-less input reads as.
_BOOT_INPUT_MODES: dict[str, tuple[str, ...]] = {
    "review": ("review",), "qa": ("qa",), "diff": ("diff",),
    # A lifecycle session (#241) boots on its interview, or on its diff when
    # #242 relaunches one whose server died between gates.
    "session": ("qa", "diff")}
# A session server's gates: (current gate, its state) → (round mode it
# accepts at /next-round, the gate that round leaves live). Anything else 400s.
_SESSION_ROUTES: dict[tuple[str, str], tuple[str, str]] = {
    ("intake", "live"): ("review", "spec"),
    ("spec", "live"): ("review", "spec"),
    ("spec", "done"): ("diff", "diff"),
    ("diff", "live"): ("diff", "diff"),
}
_session_id: str = ""  # `--session-id`, set once at startup; session mode only
_reviewer: str = ""  # `--reviewer` (#212), stamped on every output file; "" → none
# Rebound, never mutated, so a bare read under `_data_lock` is a consistent
# snapshot — the same discipline as `_input_data`.
_gates: tuple = ()
# The interview's questions and submitted answers, kept past the hand-off
# for `GET /intake` (#244); session mode only, rebound like `_gates`.
_intake: dict = {}
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


def _boot_refusal(launch_mode: str, data: dict,
                  table: dict[str, tuple[str, ...]] = _BOOT_INPUT_MODES) -> str | None:
    """Why `--mode launch_mode` must not boot on `data`, or None. Absent
    `mode` reads as the launch's first boot input, not "review" as at
    /next-round: a mode-less file has only the launch to go by."""
    accepted = table[launch_mode]
    incoming = data.get("mode", accepted[0])
    if incoming in accepted:
        return None
    return ("input mode %r does not match the server's launch mode (--mode %s, "
            "which boots on %s inputs) — the browser's view is fixed at boot"
            % (incoming, launch_mode, " or ".join(map(repr, accepted))))


def _boot_input_mode(launch_mode: str, data: dict) -> str:
    """The input mode `data` boots as: its own, else the launch's first."""
    return data.get("mode", _BOOT_INPUT_MODES[launch_mode][0])


def _open_gate(kind: str) -> tuple:
    """`kind` live, every gate before it done, every gate after it waiting."""
    at = schema.SESSION_GATE_KINDS.index(kind)
    return tuple({"kind": k,
                  "state": "done" if i < at else "live" if i == at else "waiting"}
                 for i, k in enumerate(schema.SESSION_GATE_KINDS))


def _close_gate(gates: tuple, kind: str) -> tuple:
    return tuple(dict(g, state="done") if g["kind"] == kind else g for g in gates)


def _session_at(gates: tuple) -> tuple[str, str]:
    """(gate, state) a session server is at: the live gate, else the last done."""
    live = [g["kind"] for g in gates if g["state"] == "live"]
    if live:
        return live[0], "live"
    return [g["kind"] for g in gates if g["state"] == "done"][-1], "done"


def _session_payload(gates: tuple) -> dict:
    """The serve-time `session` key, like `ledger` and `repo` — only a
    session server carries one, so a standalone tab renders no timeline."""
    if _launch_mode != "session":
        return {}
    return {"session": {"id": _session_id, "gates": [dict(g) for g in gates]}}


def _next_round_refusal(incoming: str, gates: tuple) -> str | None:
    """Why `/next-round` must not serve a round of mode `incoming`, or None."""
    if _launch_mode != "session":
        allowed = "diff" if _launch_mode == "diff" else "review"
        if incoming == allowed:
            return None
        return ("round mode %r does not match the server's launch mode "
                "(--mode %s, which serves %r rounds) — only a --mode session "
                "server re-stamps its view from a round push"
                % (incoming, _launch_mode, allowed))
    at = _session_at(gates)
    route = _SESSION_ROUTES.get(at)
    if route and route[0] == incoming:
        return None
    return ("round mode %r refused — this session is at its %s gate (%s), "
            "which accepts %s" % (incoming, at[0], at[1],
                                  "%r rounds" % route[0] if route else "no rounds"))


def _submit_refusal(data: dict, served: dict, gates: tuple) -> str | None:
    """Why a `/submit` is not for the round being served (#199), or None. A
    session's spec round N and diff round 1 share a process, so identity is
    shape, `round`, and `mode` — which, in a session, names the gate."""
    if "questions" in served:
        if "answers" not in data or "sections" in data:
            return "this server is serving an interview, not a review round"
    elif "sections" not in data:
        return "this server is serving a review round, not an interview"
    else:
        rnd, mode = data.get("round"), data.get("mode")
        if type(rnd) is not int or rnd != served.get("round"):
            return "round %r is not the round being served" % (rnd,)
        if mode is None and _launch_mode == "session":
            return "a session server needs the round's mode on every submit"
        if mode is not None and mode != served.get("mode", "review"):
            return "mode %r is not the mode being served" % (mode,)
    if _launch_mode == "session" and _session_at(gates)[1] != "live":
        return "the session's %s gate is closed — no round is open" % _session_at(gates)[0]
    return None


def _intake_links(viva_dir: Path, gates: tuple) -> tuple[list[dict], list[dict]]:
    """The intake's answer → section links (#244) and, past the spec gate, the
    interview `loop.py` saved beside them: `.viva/decisions.json` while the
    spec gate is live, SPEC_DECISIONS_FILE after. Unreadable → none."""
    live_spec = _session_at(gates) == ("spec", "live")
    path = viva_dir / ("decisions.json" if live_spec else schema.SPEC_DECISIONS_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    if live_spec:
        return schema.decision_links(data), []
    rows = [data.get(k) if isinstance(data.get(k), list) else [] for k in ("decisions", "intake")]
    return ([r for r in rows[0] if isinstance(r, dict) and isinstance(r.get("message"), str)
             and isinstance(r.get("sections"), list)],
            [r for r in rows[1] if isinstance(r, dict) and isinstance(r.get("question"), str)
             and isinstance(r.get("answer"), str)])


def _held_intake(intake: dict) -> list[dict]:
    """The interview this process holds, as `{question, answer}` rows."""
    answers = {a.get("id"): a for a in intake.get("answers", []) if isinstance(a, dict)}
    return [{"question": str(q.get("text", "")),
             "answer": " — ".join(str(a[k]) for k in ("choice", "note") if a.get(k))}
            for q in intake.get("questions", []) if isinstance(q, dict)
            for a in [answers.get(q.get("id"), {})]]


def _intake_rows(intake: list[dict], links: list[dict]) -> list[dict]:
    """One `{question, answer, sections}` per interview question. A decision
    is its question's text and answer verbatim (#211), so it joins on the
    longest question prefixing it; one that joins none is its own row."""
    texts = [" ".join(r["question"].split()) for r in intake]
    rows = [dict(r, sections=[]) for r in intake]
    loose = []
    for link in links:
        owners = [i for i, t in enumerate(texts)
                  if t and (link["message"] == t or link["message"].startswith(t + " "))]
        if not owners:
            loose.append(link)
            continue
        got = rows[max(owners, key=lambda i: len(texts[i]))]["sections"]
        got.extend(t for t in link["sections"] if t not in got)
    for link in loose:
        # Best effort, since either half may hold an arrow: after a `?` if any.
        question, mark, answer = link["message"].partition("? → ")
        question, answer = ((question + "?", answer) if mark
                            else link["message"].partition(" → ")[::2])
        rows.append({"question": question, "answer": answer, "sections": link["sections"]})
    return rows


def _served_identity(served: dict, gates: tuple) -> dict:
    """The round a stale tab is behind, for a `/submit` refusal's body."""
    ident = {"mode": served.get("mode", "qa" if "questions" in served else "review")}
    if "round" in served:
        ident["round"] = served["round"]
    return {**ident, **_session_payload(gates)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="viva review server")
    p.add_argument("--mode",       required=True, choices=list(_BOOT_INPUT_MODES))
    p.add_argument("--input",      required=True)
    p.add_argument("--output",     required=True)
    p.add_argument("--session-id", help="the lifecycle session record's id; "
                                        "required with, and only with, --mode session")
    p.add_argument("--reviewer", help="who reviews (#212): stamped as `reviewer` on "
                                      "every output file; omit for none")
    p.add_argument("--no-browser", action="store_true", help="Skip opening browser (for testing)")
    args = p.parse_args()
    if (args.mode == "session") != bool(args.session_id):
        p.error("--session-id is required with, and only with, --mode session")
    if args.reviewer is not None and not args.reviewer.strip():
        p.error("--reviewer must be a non-empty name; omit it for none")
    return args


def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


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
    # truncated/partial file — schema.atomic_write is the shared
    # implementation; this wrapper only adds the mkdir its 3 callers rely on.
    path.parent.mkdir(parents=True, exist_ok=True)
    schema.atomic_write(path, text)


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


def _evidence_refs(data: dict) -> set[str]:
    """The `/evidence` allowlist: every servable ref parseable from a
    confidence annotation's `source`, across every section in the LIVE round.
    A ref not in this set 404s before any path is even resolved — the request
    can only ask for evidence the round itself is currently citing."""
    refs: set[str] = set()
    for section in data.get("sections", []) or []:
        for a in section.get("annotations", []) or []:
            if not isinstance(a, dict) or a.get("kind") != "confidence":
                continue
            source = a.get("source")
            if not isinstance(source, str):
                continue
            m = _EVIDENCE_REF_RE.match(source.strip())
            if m:
                refs.add(m.group(0))
    return refs


def _parse_evidence_ref(ref: str) -> tuple[str, int | None, int | None] | None:
    """`path`, `path:N`, or `path:A-B` → (path, start, end), 1-indexed and
    inclusive, or None for `path`/`path:N` alone (whole file / single line
    read the same way `end = start`)."""
    m = _EVIDENCE_REF_RE.match(ref)
    if not m or m.group(0) != ref:
        return None
    path, start, end = m.group(1), m.group(2), m.group(3)
    return path, (int(start) if start else None), (int(end) if end else None)


def _resolve_evidence(ref: str, viva_dir: Path) -> dict | None:
    """Resolve an allowlisted ref to `{path, start, end, lines}`, or None on
    any failure — every failure 404s identically, so this never leaks WHY."""
    parsed = _parse_evidence_ref(ref)
    if parsed is None:
        return None
    rel_path, start, end = parsed
    root = viva_dir.parent
    # A denylisted segment anywhere in the path (not just a literal `../`)
    # refuses — `schema.SKIP_DIRS` is the one table `drift.py` also honors.
    if any(part in schema.SKIP_DIRS for part in Path(rel_path).parts):
        return None
    try:
        resolved = (root / rel_path).resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    try:
        if resolved.stat().st_size > MAX_EVIDENCE_BYTES:
            return None
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    all_lines = text.splitlines()
    if start is None:
        start, end = 1, len(all_lines)
    elif end is None:
        end = start
    if start < 1 or end < start:
        return None
    end = min(end, start + MAX_EVIDENCE_LINES - 1, len(all_lines))
    if start > len(all_lines):
        return None
    return {"path": rel_path, "start": start, "end": end,
            "lines": all_lines[start - 1:end]}


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

# `/evidence` (#106): a cited file is read whole only up to this size, and a
# cited range is capped in line count — a confidence `source` is agent-
# written, not reviewer-written, so these bound its cost rather than trusting it.
MAX_EVIDENCE_BYTES = 1 * 1024 * 1024  # 1 MiB
MAX_EVIDENCE_LINES = 400
# `path`, `path:N`, or `path:A-B` at the start of a `source` string — the
# trailing text (" — CACHE_TTL = 300") is free-form and ignored. Mirrored in
# the embedded JS (search `EVIDENCE_REF_RE`) so the button and the route agree
# on what counts as a servable ref.
_EVIDENCE_REF_RE = re.compile(r"^([\w./-]+\.\w+)(?::(\d+)(?:-(\d+))?)?")


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
        if host not in schema.LOOPBACK_HOSTS:
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
                gates_snapshot = _gates
            body = json.dumps({**_with_revision_counts(data_snapshot, _viva_dir),
                               "ledger": ledger_snapshot,
                               "repo": _viva_dir.parent.name,
                               **_session_payload(gates_snapshot)}).encode()
            self._send(200, "application/json", body)
        elif path == "/intake" and _launch_mode == "session":
            # The done intake gate, read-only (#244); a standalone server 404s.
            with _data_lock:
                intake_snapshot, gates_snapshot = _intake, _gates
            links, signed = _intake_links(_viva_dir, gates_snapshot)
            # A relaunch holds no interview; the one saved at spec finish stands in.
            rows = _intake_rows(_held_intake(intake_snapshot) or signed, links)
            self._send(200, "application/json", json.dumps({"answers": rows}).encode())
        elif path == "/preferences":
            # Every preference, every status, label-sorted — the in-page
            # panel's read (#142). Missing/corrupt store degrades to an
            # empty list (see _load_preferences_store).
            store = _load_preferences_store(_viva_dir)
            body = json.dumps(preferences.select(store, "all")).encode()
            self._send(200, "application/json", body)
        elif path == "/evidence":
            # #106 — serve the lines a confidence annotation's `source` cites.
            # The allowlist is derived from the LIVE round, not from the
            # request: a `ref` the round isn't currently citing 404s before
            # any path is resolved. Same-failure-mode-for-every-reason: this
            # never distinguishes "not cited" from "denylisted" from
            # "too big" in its response.
            qs = parse_qs(urlparse(self.path).query)
            ref = (qs.get("ref") or [None])[0]
            with _data_lock:
                allowed = _evidence_refs(_input_data)
            evidence = _resolve_evidence(ref, _viva_dir) \
                if ref and ref in allowed else None
            if evidence is None:
                self._error(404, "no such evidence")
                return
            self._send(200, "application/json", json.dumps(evidence).encode())
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
            if o.scheme != "http" or o.hostname not in schema.LOOPBACK_HOSTS:
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
        global _last_verdicts, _intake
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
        # The name is the launch's, never the tab's (#212).
        data.pop("reviewer", None)
        if _reviewer:
            data["reviewer"] = _reviewer

        # Validate review verdicts at the boundary. Q&A submits an `answers`
        # payload with no `sections`, so it is gated out (shape, not mode).
        if "sections" in data:
            try:
                schema.validate_verdicts(data)
            except ValueError as e:
                self._error(400, f"invalid verdicts: {e}")
                return

        with _data_lock:
            # A tab that missed a `round` event must not write the served
            # round's file (#199) — refused with what it is behind.
            refusal = _submit_refusal(data, _input_data, _gates)
            current = _served_identity(_input_data, _gates) if refusal else None
            if refusal is None:
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
                if isinstance(data.get("answers"), list) and _launch_mode == "session":
                    _intake = {"questions": questions_snapshot, "answers": data["answers"]}
                rnd = _input_data.get("round", 0)
                entries = [schema.verdict_to_ledger_entry(
                    rnd, titles.get(s.get("id"), s.get("id", "?")), s)
                    for s in data.get("sections", [])]
                _ledger.extend(e for e in entries if e is not None)
        if refusal is not None:
            self._send(409, "application/json", json.dumps(
                {"error": "stale submit: %s" % refusal, "current": current}).encode())
            return
        data = extract_attachments(data, out, rnd)
        if "answers" in data:
            data = annotate_qa_acceptance(data, questions_snapshot)
        try:
            _atomic_write(Path(out), json.dumps(data, indent=2))
        except (IOError, OSError) as e:
            self._error(500, f"write failed: {e}")
            return

        self._send(200, "application/json", b'{"ok":true}')
        _push_sse("processing", {})

    def _post_next_round(self) -> None:
        global _input_data, _output_path, _last_verdicts, _gates
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
        # The launch mode gates which round mode may replace the served one
        # (#126); a session server walks its gate table instead (#241). The
        # check, the gate move, and the round swap share one lock. Absent
        # `mode` reads as "review", and is stored so: the tab echoes it on submit.
        incoming = new_data.setdefault("mode", "review")
        opened = None
        with _data_lock:
            refusal = _next_round_refusal(incoming, _gates)
            if refusal is None:
                if _launch_mode == "session":
                    opens = _SESSION_ROUTES[_session_at(_gates)][1]
                    if _session_at(_gates) != (opens, "live"):
                        # A gate is a fresh epoch: the spec's rows are already
                        # in its doc's ledger, so the diff gate's start empty.
                        _gates, opened = _open_gate(opens), opens
                        _ledger.clear()
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
                gates_snapshot = _gates
        if refusal is not None:
            self._error(400, refusal)
            return
        if handoff:
            # Distinct from the per-mode startup line so a terminal-watching
            # caller (or a human tailing stdout) can see the hand-off happen,
            # not just infer it from the browser reflowing.
            print(f"viva · hand-off qa → review · {_url}", flush=True)
        if opened:
            print(f"viva · session {opened} gate open · {_url}", flush=True)
        self._send(200, "application/json", b'{"ok":true}')
        _push_sse("round", {**_with_revision_counts(new_data, _viva_dir),
                            "ledger": ledger_snapshot,
                            "repo": _viva_dir.parent.name,
                            **_session_payload(gates_snapshot)})

    def _post_complete(self) -> None:
        global _gates
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
            at = _session_at(_gates) if _launch_mode == "session" else None
        # A session completes its spec and diff gates only; the intake ends by
        # hand-off, and a closed gate has no round left to sign off.
        if at is not None and at not in (("spec", "live"), ("diff", "live")):
            self._error(400, "this session is at its %s gate (%s), which has "
                             "no round to complete" % at)
            return
        # Server state, never the body: is the served round a diff round?
        diff_round = _launch_mode == "diff" or at == ("diff", "live")
        # Q&A is exempt by shape (`questions`, never `sections`). Diff mode
        # is NOT exempt by mode any more (#177): a blanket exemption let a
        # `--mode diff` server accept ANY verdicts, reopening for hunks the
        # hole #102 closed for sections. The caller now must say WHY via
        # `resolved: "empty"`, honored only on a diff round (`diff_round`).
        if "sections" in round_input:
            if submitted is None:
                self._error(400, "no verdicts submitted for this round — "
                                 "nothing to complete")
                return
            resolved = summary.get("resolved")
            if resolved is not None and resolved != "empty":
                self._error(400, "unknown 'resolved' value %r — the only "
                                 "signal is \"empty\", and only a diff "
                                 "round honors it" % (resolved,))
                return
            if resolved is not None and not diff_round:
                self._error(400, "'resolved' is a diff-review signal — a "
                                 "%s round cannot resolve empty; every "
                                 "section must be approved"
                                 % round_input.get("mode", "review"))
                return
            resolved_empty = resolved == "empty" and diff_round
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
        if at == ("spec", "live"):
            # The spec gate closes and the process idles for the diff gate
            # (#241); the tab keeps its stream open on the `session` it reads.
            with _data_lock:
                _gates = _close_gate(_gates, "spec")
                gates_snapshot = _gates
            self._send(200, "application/json", b'{"ok":true}')
            print(f"viva · session spec gate closed · waiting for the diff "
                  f"gate · {_url}", flush=True)
            _push_sse("complete", {**summary, **_session_payload(gates_snapshot)})
            return
        with _data_lock:
            if at is not None:
                _gates = _close_gate(_gates, "diff")
            gates_snapshot = _gates
        self._send(200, "application/json", b'{"ok":true}')
        _push_sse("complete", {**summary, **_session_payload(gates_snapshot)})
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
    with open(args.input, encoding='utf-8') as f:
        _input_data = json.load(f)
    # Validate the input on read, keyed on the LAUNCH MODE — same reason as
    # `/complete`'s guard: keying on shape let a file with neither `sections`
    # nor `questions` through unvalidated. A shape/mode mismatch now exits 1
    # at launch instead of booting a tab that can't render. A session boots
    # on two input modes, so it keys on the one the input boots as.
    boot_mode = args.mode if args.mode != "session" else \
        _boot_input_mode(args.mode, _input_data if isinstance(_input_data, dict) else {})
    if boot_mode == "qa":
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
    refusal = _boot_refusal(args.mode, _input_data)
    if refusal:
        sys.exit("viva: invalid %s %s: %s"
                 % ("qa-input" if boot_mode == "qa" else "review-input",
                    args.input, refusal))
    _output_path = args.output
    _output_root = Path(args.output).resolve().parent
    _launch_mode = args.mode
    _reviewer = args.reviewer or ""
    if args.mode == "session":
        # Gates derive from the boot input: an interview opens the intake,
        # a diff (#242's relaunch) means intake and spec are already signed.
        _session_id = args.session_id
        _gates = _open_gate("intake" if boot_mode == "qa" else "diff")

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
