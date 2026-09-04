#!/usr/bin/env python3
"""viva's docket — a read-only status line across every `.viva/` session found
under a set of roots (#173): which repo, which doc, whose turn, and whether
the round is live or resumable.

CLI-only: a terminal filter, not a server route, so it doesn't collide with
`server.py`'s one-read-outside-`.viva/` invariant (CLAUDE.md).

Writes nothing — every fact comes from a stat or a short HTTP probe.

Usage:
  docket.py [--root GLOB]... [--format text|json]

Roots are directory globs, each checked two ways: the root itself may BE a
repo (`<root>/.viva`), or a directory OF repos (`<root>/*/.viva`). `--root` is
repeatable; with none given, `VIVA_DOCKET_ROOTS` (colon-separated) is used;
with neither, the default is `~/Projects/*`.

For each `.viva/` found, one row is reported: repo name, doc file and doc type
(from the current round's `review-input-rN.json`, when present), round
number, STATE, and AGE.

STATE:

  your-turn        Round N is armed and unanswered, and any reachable server
                    agrees it is serving N. Also the between-launches case
                    where no `server.url` exists and nothing was submitted.
  agent-working     `review-rN.json` exists for round N — the agent's turn.
  parsed-not-armed  Round N is parsed but the live server is serving something
                    else (a stale earlier round, or a `/viva-write` qa payload
                    with no `round` key). Nothing populates `review-rN.json`
                    until `loop.py arm` runs.
  dead              `server.url` exists but nothing answers within the probe
                    timeout.
  qa                A `qa-input.json`/`answers.json` exists with no
                    `review-input-rN.json` — an intake interview, not a round.
  done              Best-effort: `review-rN.json` exists and
                    `schema.round_is_complete()` is satisfied.

AGE is the mtime of whichever round/qa file is newest — there is no
timestamp field, so mtime is the only signal. Human string in `--format
text`, raw epoch seconds in `--format json`.

Imports no sibling but `schema.py` (CLAUDE.md), and does not import
`loop.py` — its round-derivation and liveness probe are reimplemented here.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402  — the one permitted sibling import (CLAUDE.md)

_DEFAULT_ROOTS = ["~/Projects/*"]
# Short: a dead server.url must not stall the whole report waiting on it.
_PROBE_TIMEOUT = 0.7


# ── roots ──────────────────────────────────────────────────────────────────
def resolve_roots(root_args: List[str]) -> List[str]:
    """`--root` wins outright; otherwise `VIVA_DOCKET_ROOTS`; otherwise the
    single default."""
    if root_args:
        return list(root_args)
    env = os.environ.get("VIVA_DOCKET_ROOTS")
    if env:
        return [r for r in env.split(":") if r]
    return list(_DEFAULT_ROOTS)


def find_viva_dirs(root_globs: List[str]) -> List[Path]:
    """Every `.viva/` directory reachable from `root_globs`, one level deep.

    Checks each expanded root two ways — does it directly hold `.viva/`, and
    does any immediate child — so a repo root and a directory-of-repos root
    both work.
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
#    imported from it (scripts/*.py may import only schema.py). ────────────
def current_round(viva: Path) -> int:
    """Highest *parsed* round on disk. 0 when none."""
    rounds = [schema.parse_round_input_stem(p.stem)
              for p in viva.glob(schema.round_input_glob())]
    return max((n for n in rounds if n is not None), default=0)


def round_files(viva: Path, n: int) -> Tuple[Path, Path]:
    return schema.round_file_paths(viva, n)


def load_json(p: Path) -> Optional[dict]:
    """`None` on anything short of a clean parse of a JSON *object* — must
    never crash on a mid-write or wrong-shape round file. Callers do
    `load_json(p) or {}`, so a non-dict payload must not reach them as-is."""
    try:
        with p.open() as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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
    """`server.url` is repo-supplied — constrained to loopback so a repo
    naming an attacker's host can't turn a sweep into an SSRF probe. Returns
    `None` on a rejected URL rather than raising, same as a missing file, so
    one bad `.viva/` doesn't stop the sweep (and reads as "your-turn", not
    `dead`, which requires a real loopback address that didn't answer)."""
    f = viva / "server.url"
    if not f.exists():
        return None
    try:
        text = f.read_text().strip()
    except OSError:
        return None
    if not text:
        return None
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        return None
    return text


def probe_input(base: str, timeout: float = _PROBE_TIMEOUT) -> Optional[dict]:
    """The payload a live server at `base` is serving, or `None` if nothing
    answers within `timeout`. "No server" (`None`) and "no round" (a dict
    with no `round` key, e.g. a live qa payload during the `/viva-write`
    hand-off) are different answers — only the caller decides via
    `payload.get("round") != current_round()`, so the missing-key case falls
    out of the same check as an ordinary stale round."""
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
        # Neither a parsed round nor a qa session (e.g. mid `start
        # --parse-only`) — none of the five documented states fit.
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
        # Human submitted verdicts — agent's turn, unless already signed off.
        verdicts = load_json(out)
        state = "agent-working"
        if verdicts is not None:
            try:
                if schema.round_is_complete(input_data, verdicts):
                    state = "done"
            except Exception:
                pass  # best-effort — never let this crash the row
    else:
        # Round N parsed but unanswered — whose turn depends on what the
        # live server (if any) is actually serving.
        base = server_url(viva)
        if base is None:
            # No server reachable and nothing submitted yet.
            state = "your-turn"
        else:
            payload = probe_input(base)
            if payload is None:
                state = "dead"
            elif payload.get("round") != n:
                # Stale round, or a live qa payload with no `round` key.
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
