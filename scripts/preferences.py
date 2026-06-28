#!/usr/bin/env python3
"""Maintain viva's learned-preference store — recurring critiques (issue #17).

Reviewers repeat the same critiques across docs and sessions — "unsourced
numbers", "passive voice", "no rollback step". This script is the SINGLE writer
of `.viva/preferences.json`, the store that lets viva *learn* those patterns and
pre-apply (or pre-flag) them before the human re-types the note.

Division of labor matches the rest of viva: the semantic work — clustering free
-text notes into one critique, matching a new cluster to an existing preference
across sessions — is judgment the agent does. This script only does the
mechanical bookkeeping the agent can't be trusted to keep consistent: stable
ids, a count of distinct sessions, promotion when recurrence crosses a
threshold, and listing for both the agent (json) and the human (text).

The store survives across sessions: it lives at `.viva/preferences.json`, which
the skill's round-1 state clear deliberately does NOT delete. It is gitignored,
so learned preferences are per-developer (a reviewer's own habits), not shared
across clones. It is plain JSON — the human can open and edit it directly, and
`set --status muted` makes a bad learned preference correctable, which is what
keeps the fallible sign-off clustering safe.

  preferences.json:
  {
    "version": 1,
    "preferences": {
      "cite-sources": {
        "id": "cite-sources",
        "label": "Cite a source for every quantitative claim",
        "guidance": "When a section states a number/percent/count, attach a "
                    "citation or mark it unsourced.",
        "status": "candidate",        # candidate | standing | muted
        "observations": 3,            # total section-hits, informational
        "sessions": ["2026-06-20 plan.md", "2026-06-25 spec.md"],  # distinct
        "created": "2026-06-20",
        "updated": "2026-06-25"
      }
    }
  }

A preference is promoted candidate→standing once it has recurred across
`threshold` distinct sessions (default 2). Only *standing* preferences are
consulted at generation/rewrite time, so the stream of single-session candidates
never leaks into the doc.

Usage:
  preferences.py record --store .viva/preferences.json \\
      --session "2026-06-28 plan.md" \\
      [--id cite-sources] [--label "..."] [--guidance "..."] \\
      [--count 1] [--threshold 2]
  preferences.py list --store .viva/preferences.json \\
      [--status standing|candidate|muted|all] [--format json|text]
  preferences.py set  --store .viva/preferences.json --id ID \\
      --status standing|candidate|muted
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

VERSION = 1
STATUSES = ("candidate", "standing", "muted")


def slug(label: str) -> str:
    """Derive a stable, human-readable id from a label."""
    s = re.sub(r"[^a-z0-9]+", "-", (label or "").strip().lower()).strip("-")
    return s or "pref"


def empty_store() -> dict:
    return {"version": VERSION, "preferences": {}}


def _normalize(store: dict) -> dict:
    """Clone a store, defensively coercing it to the current shape."""
    prefs = store.get("preferences", {}) if isinstance(store, dict) else {}
    return {
        "version": VERSION,
        "preferences": {
            k: {**v, "sessions": list(v.get("sessions", []))}
            for k, v in prefs.items()
        },
    }


def record(
    store: dict,
    *,
    session: str,
    pref_id: str | None = None,
    label: str | None = None,
    guidance: str | None = None,
    count: int = 1,
    threshold: int = 2,
    today: str | None = None,
) -> dict:
    """Create or reinforce one preference. Pure — returns a new store dict.

    - `pref_id` matches an existing preference → reinforce it.
    - otherwise create a new *candidate* (id = `pref_id` or `slug(label)`).

    Reinforcing adds `session` to the distinct-session set (deduped), adds
    `count` to `observations`, refreshes `label`/`guidance` when supplied, and
    promotes candidate→standing once the distinct-session count reaches
    `threshold`. A muted preference is never auto-promoted here — only an
    explicit `set` moves it.
    """
    today = today or date.today().isoformat()
    out = _normalize(store)
    prefs = out["preferences"]

    pid = pref_id or slug(label) if (pref_id or label) else None
    if not pid:
        raise ValueError("record needs --id or --label")

    pref = prefs.get(pid)
    if pref is None:
        if not label:
            raise ValueError(f"new preference {pid!r} needs --label")
        pref = {
            "id": pid,
            "label": label,
            "guidance": guidance or "",
            "status": "candidate",
            "observations": 0,
            "sessions": [],
            "created": today,
            "updated": today,
        }
        prefs[pid] = pref

    if label:
        pref["label"] = label
    if guidance:
        pref["guidance"] = guidance
    pref["observations"] = int(pref.get("observations", 0)) + max(0, count)
    if session and session not in pref["sessions"]:
        pref["sessions"].append(session)
    pref["updated"] = today
    if pref.get("status") == "candidate" and len(pref["sessions"]) >= threshold:
        pref["status"] = "standing"
    return out


def set_status(store: dict, pref_id: str, status: str) -> dict:
    """Force a preference's status (mute/unmute/promote). Pure."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {list(STATUSES)}")
    out = _normalize(store)
    if pref_id not in out["preferences"]:
        raise KeyError(pref_id)
    out["preferences"][pref_id]["status"] = status
    out["preferences"][pref_id]["updated"] = date.today().isoformat()
    return out


def select(store: dict, status: str = "standing") -> list:
    """Return preferences with the given status (or 'all'), label-sorted."""
    prefs = list(store.get("preferences", {}).values())
    if status != "all":
        prefs = [p for p in prefs if p.get("status") == status]
    return sorted(prefs, key=lambda p: (p.get("label") or "").lower())


def _load(path: Path) -> dict:
    if not path.exists():
        return empty_store()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"viva preferences: cannot read {path}: {e}")


def _write(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False),
                    encoding="utf-8")


def _format_text(prefs: list) -> str:
    if not prefs:
        return "(no preferences)"
    lines = []
    for p in prefs:
        lines.append(f"[{p.get('status', '?')}] {p.get('id', '?')} — "
                     f"{p.get('label', '')}")
        if p.get("guidance"):
            lines.append(f"    guidance: {p['guidance']}")
        n_sessions = len(p.get("sessions", []))
        lines.append(f"    seen in {n_sessions} "
                     f"session{'' if n_sessions == 1 else 's'}, "
                     f"{p.get('observations', 0)} observation(s)")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Maintain viva's learned-preference store")
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="Create or reinforce a preference")
    rec.add_argument("--store", required=True)
    rec.add_argument("--session", required=True,
                     help='Session label, e.g. "2026-06-28 plan.md"')
    rec.add_argument("--id", dest="pref_id", default=None,
                     help="Reinforce this preference id (omit to create from --label)")
    rec.add_argument("--label", default=None)
    rec.add_argument("--guidance", default=None)
    rec.add_argument("--count", type=int, default=1,
                     help="Sections hit this session (added to observations)")
    rec.add_argument("--threshold", type=int, default=2,
                     help="Distinct sessions before candidate→standing")

    lst = sub.add_parser("list", help="List preferences")
    lst.add_argument("--store", required=True)
    lst.add_argument("--status", default="standing",
                     choices=[*STATUSES, "all"])
    lst.add_argument("--format", default="text", choices=["text", "json"])

    st = sub.add_parser("set", help="Force a preference's status")
    st.add_argument("--store", required=True)
    st.add_argument("--id", dest="pref_id", required=True)
    st.add_argument("--status", required=True, choices=list(STATUSES))

    args = p.parse_args()
    store_path = Path(args.store)

    if args.cmd == "record":
        store = _load(store_path)
        try:
            store = record(store, session=args.session, pref_id=args.pref_id,
                           label=args.label, guidance=args.guidance,
                           count=args.count, threshold=args.threshold)
        except ValueError as e:
            sys.exit(f"viva preferences: {e}")
        _write(store_path, store)
        pid = args.pref_id or slug(args.label)
        pref = store["preferences"][pid]
        print(f"viva preferences: recorded {pid!r} → {pref['status']} "
              f"({len(pref['sessions'])} session(s), "
              f"{pref['observations']} obs)", flush=True)

    elif args.cmd == "list":
        store = _load(store_path)
        prefs = select(store, args.status)
        if args.format == "json":
            print(json.dumps(prefs, indent=2, ensure_ascii=False), flush=True)
        else:
            print(_format_text(prefs), flush=True)

    elif args.cmd == "set":
        store = _load(store_path)
        try:
            store = set_status(store, args.pref_id, args.status)
        except KeyError:
            sys.exit(f"viva preferences: no preference {args.pref_id!r}")
        _write(store_path, store)
        print(f"viva preferences: {args.pref_id!r} → {args.status}", flush=True)


if __name__ == "__main__":
    main()
