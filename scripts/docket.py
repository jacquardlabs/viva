#!/usr/bin/env python3
"""viva's docket — a read-only status line across every `.viva/` session found
under a set of roots (issue #173): which repo, which doc, whose turn, and
whether the round is live or resumable.

**CLI-only, deliberately.** This is a terminal filter, not a server route.
CLAUDE.md documents `server.py`'s one read outside `.viva/` as `assets/vendor/`
— ten pinned, committed assets. A `/docket` HTTP route walking arbitrary paths
under a reviewer's home directory would break that documented invariant, so
this stays `python3 scripts/docket.py`, run by a human or an agent in a
terminal, never wired into `server.py`. Whether a served "live tabs" view of
this is wanted at all is a decision this script deliberately leaves to the
maintainer — see the PR body, not this file.

Writes nothing: no new state file, no home-directory registry, no XDG cache.
Every fact below comes from a stat or a short HTTP probe, never from a store
this script itself maintains.

Usage:
  docket.py [--root GLOB]... [--format text|json]

Roots are directory globs, each checked two ways: the root itself may BE a
repo (`<root>/.viva`), or a directory OF repos (`<root>/*/.viva`) — both are
supported so `--root ~/Projects/viva` and `--root ~/Projects/*` both work.
`--root` is repeatable; with none given, `VIVA_DOCKET_ROOTS` (colon-separated,
same shape as `$PATH`) is used; with neither, the default is `~/Projects/*`.

For each `.viva/` found, one row is reported: repo name (the parent directory's
name), doc file and doc type (from the current round's `review-input-rN.json`,
when present), round number, STATE, and AGE.

STATE is the entire point of this tool — it is not just "is there a round",
it is "does anything still need a human, and is the server that would answer
even alive":

  your-turn        Round N is armed (`review-input-rN.json` exists) and not
                    yet answered (`review-rN.json` for that N does not exist),
                    and — if a server is reachable — it agrees it is serving N.
                    Also reported when no `server.url` exists at all and no
                    round has been submitted (the session between launches).
  agent-working     `review-rN.json` exists for round N — the human submitted
                    verdicts and it is the agent's turn to act on them.
  parsed-not-armed  `review-input-rN.json` exists for round N, but the live
                    server at `server.url` answers with something other than
                    round N — either an earlier round (`rearm --parse-only`
                    wrote round N while the server still serves the round
                    before it) or a qa payload with no `round` key at all (the
                    `/viva-write` hand-off window, where the interview server
                    is still up when round 1 is parsed to disk). Either way,
                    nothing will populate `review-rN.json` until `loop.py arm`
                    runs. This is `loop.py wait`'s "round N is parsed but not
                    armed" condition (see `cmd_wait`), read off disk instead of
                    raised as a fatal wait error — the whole reason this state
                    exists as its own bucket rather than collapsing into
                    "your-turn".
  dead              `server.url` exists but nothing answers there within the
                    probe timeout — the process is gone and no round will ever
                    be armed or resolved until something relaunches it.
  qa                A `qa-input.json` or `answers.json` exists and there is no
                    `review-input-rN.json` at all — an intake interview
                    (`/viva-write`), not a review round.
  done              Best-effort only: `review-rN.json` exists for round N and
                    `schema.round_is_complete()` says N's base (and any
                    `pass` conjunct) is satisfied — the round the agent would
                    hand to `loop.py finish`. Not attempted for anything but
                    the review shape; a qa or malformed round is never
                    reported "done".

AGE is the filesystem mtime of whichever of `review-input-rN.json` /
`review-rN.json` (or `qa-input.json` / `answers.json` for a qa session) is
newest — there is no timestamp written into any `.viva/` file, so mtime is the
only signal available. Rendered as a human string ("2h ago") in `--format
text`, and as a raw Unix epoch (seconds) in `--format json`.

This script imports no sibling under `scripts/` — `schema.py` is the one
permitted cross-import (CLAUDE.md) — and does not import `loop.py`, whose
round-derivation (`current_round`) and liveness probe (`probe_input`) are
reimplemented here rather than shared, per the same rule that keeps
`doc_types.py` un-imported by `loop.py`: run it, don't import it. Nothing here
runs a subprocess either; there is nothing to run — the doc-type name, when
present, is read straight off the round JSON that already carries it
(`schema.ReviewInput.doc_type`), never re-resolved through `doc_types.py`.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402  — the one permitted sibling import (CLAUDE.md)

_DEFAULT_ROOTS = ["~/Projects/*"]
# Short, deliberately — this walks N repos on every invocation, and a
# `server.url` naming a process that will never answer must not stall the
# whole report waiting on it.
_PROBE_TIMEOUT = 0.7


# ── roots ──────────────────────────────────────────────────────────────────
def resolve_roots(root_args: List[str]) -> List[str]:
    """`--root` (repeatable) wins outright; otherwise `VIVA_DOCKET_ROOTS`
    (colon-separated); otherwise the single default."""
    if root_args:
        return list(root_args)
    env = os.environ.get("VIVA_DOCKET_ROOTS")
    if env:
        return [r for r in env.split(":") if r]
    return list(_DEFAULT_ROOTS)


def find_viva_dirs(root_globs: List[str]) -> List[Path]:
    """Every `.viva/` directory reachable from `root_globs`, one level deep.

    Each glob is expanded (`~` and `*` both), and each resulting directory is
    checked two ways so a root that IS a repo and a root that is a directory
    OF repos both work without the caller having to know which: does IT
    directly hold `.viva/`, and does any immediate child?
    """
    found: Dict[str, Path] = {}
    for pattern in root_globs:
        expanded = os.path.expanduser(pattern)
        for candidate in sorted(glob.glob(expanded)):
            path = Path(candidate)
            if not path.is_dir():
                continue
            direct = path / ".viva"
            if direct.is_dir():
                found[str(direct)] = direct
            for child in sorted(path.glob("*/.viva")):
                if child.is_dir():
                    found[str(child)] = child
    return list(found.values())


# ── round derivation — mirrors loop.py's current_round/round_files, not
#    imported from it (scripts/*.py may import only schema.py) ─────────────
def current_round(viva: Path) -> int:
    """Highest *parsed* round on disk. 0 when none — matches
    `loop.py.current_round`'s contract exactly (see that docstring)."""
    rounds = [int(p.stem[len("review-input-r"):])
              for p in viva.glob("review-input-r*.json")
              if p.stem[len("review-input-r"):].isdigit()]
    return max(rounds, default=0)


def round_files(viva: Path, n: int) -> Tuple[Path, Path]:
    return viva / f"review-input-r{n}.json", viva / f"review-r{n}.json"


def load_json(p: Path) -> Optional[dict]:
    """`None` on anything short of a clean parse — a docket report must never
    crash on a round file some other process is mid-write on."""
    try:
        with p.open() as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def mtime_of(paths: List[Path]) -> Optional[float]:
    """The newest mtime among the paths that exist, or `None` if none do."""
    times = []
    for p in paths:
        try:
            times.append(p.stat().st_mtime)
        except OSError:
            pass
    return max(times) if times else None


# ── liveness — probed, not stat'ed (mirrors loop.py's server_url/probe_input,
#    with a short timeout: a docket run must never hang on a dead process) ──
def server_url(viva: Path) -> Optional[str]:
    f = viva / "server.url"
    if not f.exists():
        return None
    try:
        text = f.read_text().strip()
    except OSError:
        return None
    return text or None


def probe_input(base: str, timeout: float = _PROBE_TIMEOUT) -> Optional[dict]:
    """The payload a live server at `base` is serving, or `None` if nothing
    answers there within `timeout` — a dead process. Mirrors
    `loop.py.probe_input`'s split on purpose: "no server" (`None`) and "no
    round" (a dict with no `round` key) are different answers, and only the
    first may be read as dead. The second is real and reachable here — during
    the `/viva-write` hand-off window (CLAUDE.md) the interview's qa server
    has already written `server.url` by the time round 1 is parsed to disk,
    so a live qa payload with no `round` key can sit behind an *already
    parsed* `review-input-r1.json`. Collapsing that into "dead" would misreport
    exactly the live-vs-resumable distinction this tool exists to get right;
    the caller compares `payload.get("round")` against `current_round()`
    instead, so a missing key (`None != n` for any `n >= 1`) falls out of the
    same `!=` check that catches an ordinary stale round."""
    try:
        with urllib.request.urlopen(base + "/input", timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else {}


# ── classification — the entire point of this tool ─────────────────────────
def classify(viva: Path) -> Dict[str, object]:
    """One `.viva/` session's status: state, doc identity, round, mtime."""
    n = current_round(viva)

    if n == 0:
        qa_in, qa_out = viva / "qa-input.json", viva / "answers.json"
        if qa_in.exists() or qa_out.exists():
            qa_data = load_json(qa_in) or {}
            return {
                "state": "qa",
                "doc_file": None,
                "doc_type": None,
                "round": None,
                "context": qa_data.get("context"),
                "mtime": mtime_of([qa_in, qa_out]),
            }
        # A `.viva/` directory with neither a parsed round nor a qa session —
        # e.g. between `start --parse-only` and the producer's parse. None of
        # the five documented states fit; reported honestly rather than
        # guessed into one of them.
        return {
            "state": "empty",
            "doc_file": None,
            "doc_type": None,
            "round": None,
            "mtime": None,
        }

    inp, out = round_files(viva, n)
    input_data = load_json(inp) or {}
    doc_file = input_data.get("doc_file")
    doc_type = input_data.get("doc_type")
    mtime = mtime_of([inp, out])

    if out.exists():
        # The human has submitted verdicts for round N — the agent's turn,
        # unless the round is fully signed off already (best-effort "done").
        verdicts = load_json(out)
        state = "agent-working"
        if verdicts is not None:
            try:
                if schema.round_is_complete(input_data, verdicts):
                    state = "done"
            except Exception:
                pass  # best-effort per the issue — never let this crash the row
    else:
        # Round N is parsed but not yet answered. Whether it is actually the
        # human's turn depends on what the live server (if any) is serving —
        # this is `loop.py wait`'s "round is parsed but not armed" check
        # (`cmd_wait`, "served != n"), read off disk rather than raised.
        base = server_url(viva)
        if base is None:
            # No server ever launched this session (or it launched and its
            # url file was later cleaned up) and nothing has been submitted —
            # the between-sessions case the issue calls out explicitly.
            state = "your-turn"
        else:
            payload = probe_input(base)
            if payload is None:
                state = "dead"
            elif payload.get("round") != n:
                # Covers both an ordinary stale round (still serving N-1) and
                # a live qa payload with no `round` key at all (`None != n`)
                # — see `probe_input`'s docstring for why the second is real.
                state = "parsed-not-armed"
            else:
                state = "your-turn"

    return {
        "state": state,
        "doc_file": doc_file,
        "doc_type": doc_type,
        "round": n,
        "mtime": mtime,
    }


def build_docket(root_globs: List[str]) -> List[Dict[str, object]]:
    now = time.time()
    rows = []
    for viva in find_viva_dirs(root_globs):
        repo_dir = viva.parent
        info = classify(viva)
        mtime = info.get("mtime")
        rows.append({
            "repo": repo_dir.name,
            "path": str(repo_dir),
            "state": info["state"],
            "doc_file": info.get("doc_file"),
            "doc_type": info.get("doc_type"),
            "round": info.get("round"),
            "context": info.get("context"),
            "mtime": mtime,
            "age": format_age(mtime, now),
        })
    rows.sort(key=lambda r: (str(r["repo"]).lower(), r["path"]))
    return rows


# ── age ──────────────────────────────────────────────────────────────────
def format_age(mtime: Optional[float], now: float) -> str:
    if mtime is None:
        return "unknown"
    delta = max(0.0, now - mtime)
    if delta < 60:
        return "just now"
    minutes = delta / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 30:
        return f"{int(days)}d ago"
    months = days / 30
    if months < 12:
        return f"{int(months)}mo ago"
    years = months / 12
    return f"{int(years)}y ago"


# ── rendering ───────────────────────────────────────────────────────────────
def render_text(rows: List[Dict[str, object]], roots: List[str]) -> str:
    if not rows:
        return f"docket: no .viva/ sessions found under: {', '.join(roots)}"
    headers = ("REPO", "STATE", "ROUND", "DOC", "TYPE", "AGE")
    table = []
    for r in rows:
        table.append((
            str(r["repo"]),
            str(r["state"]),
            "-" if r["round"] is None else str(r["round"]),
            str(r["doc_file"] or r.get("context") or "-"),
            str(r["doc_type"] or "-"),
            str(r["age"]),
        ))
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in table))
        for i in range(len(headers))
    ]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    for row in table:
        lines.append("  ".join(c.ljust(w) for c, w in zip(row, widths)))
    return "\n".join(lines)


def render_json(rows: List[Dict[str, object]]) -> str:
    return json.dumps(rows, indent=2)


# ── CLI ──────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docket.py",
        description="List open viva review sessions across repos: whose "
                     "turn, live or resumable. Read-only; writes nothing.",
    )
    parser.add_argument(
        "--root", action="append", default=[], metavar="GLOB",
        help="Repo or directory-of-repos glob (repeatable). Default: "
             "$VIVA_DOCKET_ROOTS (colon-separated) or ~/Projects/*.",
    )
    parser.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text).",
    )
    args = parser.parse_args(argv)

    roots = resolve_roots(args.root)
    rows = build_docket(roots)

    if args.format == "json":
        print(render_json(rows))
    else:
        print(render_text(rows, roots))
    return 0


if __name__ == "__main__":
    sys.exit(main())
