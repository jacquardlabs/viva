#!/usr/bin/env python3
"""Maintain viva's open-note store — notes that carry across rounds (issue #16).

A note the reviewer marks *open* persists round to round, accumulating the
exchange (what was asked, what the agent answered) until it is *settled*. This
script is the SINGLE writer of `.viva/open-notes.json`; the server only reads it
(via `parse_sections.py --open-notes`, which attaches open threads to the next
round's cards) and `revision_history.py` folds the full threads into the ledger
at sign-off.

The store is keyed by normalized section title — the same stable identity that
approval and annotation carry-forward use, since section ids are positional and
re-assigned each round.

  open-notes.json:
  {
    "goals": {
      "title": "Goals",
      "status": "open",            # open | settled
      "exchanges": [
        {"round": 1, "verdict": "changes", "note": "tighten", "response": "Done."}
      ]
    }
  }

Usage:
  open_notes.py update \\
    --store .viva/open-notes.json \\
    --round N \\
    --verdicts .viva/review-rN.json \\
    --input .viva/review-input-rN.json \\
    [--response "s2=one-line summary of the rewrite" ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def norm(title: str) -> str:
    return (title or "").strip().lower()


def update(
    store: dict,
    round_num: int,
    verdicts: dict,
    input_data: dict,
    responses: dict,
) -> dict:
    """Apply one round's verdicts to the thread store. Pure — returns a new dict.

    - changes/info with `open` truthy → append an exchange (create the thread
      if new); the agent's `response` for that section id rides along.
    - `settle` truthy                 → mark the thread settled.
    - approved                        → settle any open thread (approval settles,
                                        so an open note never blocks sign-off).
    Untracked changes/info notes (no `open`) are ignored — today's behavior.
    """
    titles = {s.get("id"): s.get("title", s.get("id"))
              for s in input_data.get("sections", [])}
    # Deep-enough copy: clone each thread and its exchange list so the input
    # store is never mutated.
    out = {k: {**v, "exchanges": list(v.get("exchanges", []))}
           for k, v in store.items()}

    for s in verdicts.get("sections", []):
        sid = s.get("id")
        title = titles.get(sid, sid or "?")
        key = norm(title)
        verdict = s.get("verdict")
        thread = out.get(key)

        if verdict == "approved":
            if thread and thread.get("status") == "open":
                thread["status"] = "settled"
        elif s.get("settle"):
            if thread:
                thread["status"] = "settled"
        elif verdict in ("changes", "info") and s.get("open"):
            if thread is None:
                thread = {"title": title, "status": "open", "exchanges": []}
                out[key] = thread
            thread["status"] = "open"
            thread["title"] = title  # keep display title fresh
            thread["exchanges"].append({
                "round": round_num,
                "verdict": verdict,
                "note": s.get("note", ""),
                "response": responses.get(sid, ""),
            })
    return out


def _parse_responses(pairs: list) -> dict:
    """Turn ['s2=text', ...] into {'s2': 'text'}. Splits on the first '='."""
    out = {}
    for p in pairs or []:
        sid, sep, text = p.partition("=")
        if sep:
            out[sid.strip()] = text
    return out


def _load_json(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"viva open_notes: cannot read {path}: {e}")


def main() -> None:
    p = argparse.ArgumentParser(description="Maintain viva's open-note store")
    sub = p.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("update", help="Apply a round's verdicts to the store")
    up.add_argument("--store", required=True)
    up.add_argument("--round", type=int, required=True, dest="round_num")
    up.add_argument("--verdicts", required=True)
    up.add_argument("--input", required=True)
    up.add_argument("--response", action="append", default=[],
                    help='Agent response for a section, as "id=text" (repeatable)')
    args = p.parse_args()

    store_path = Path(args.store)
    store = json.loads(store_path.read_text(encoding="utf-8")) if store_path.exists() else {}
    verdicts = _load_json(args.verdicts)
    input_data = _load_json(args.input)
    responses = _parse_responses(args.response)

    store = update(store, args.round_num, verdicts, input_data, responses)

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    open_threads = sum(1 for t in store.values() if t.get("status") == "open")
    print(f"viva: open-note store updated → {store_path} ({open_threads} open)",
          flush=True)


if __name__ == "__main__":
    main()
