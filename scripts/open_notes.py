#!/usr/bin/env python3
"""Maintain viva's open-note store — notes that carry across rounds (issue #16).

A note the reviewer marks *open* persists round to round, accumulating the
exchange (what was asked, what the agent answered) until it is *settled*. This
script is the SINGLE writer of `.viva/open-notes.json`; the server only reads it
(via `parse_sections.py --open-notes`, which attaches open threads to the next
round's cards) and `revision_history.py` folds the full threads into the ledger
at sign-off.

The store is keyed by comment `cid` — one thread per inline comment, not one per
section. Each thread carries its section title (for re-attachment) and the
anchored quote (if any).

  open-notes.json:
  {
    "s1-c1": {
      "cid": "s1-c1",
      "title": "Goals",
      "quote": "retries 3x",
      "status": "open",            # open | settled
      "exchanges": [
        {"round": 1, "verdict": "changes", "note": "5x not 3x", "response": "Done."}
      ]
    }
  }

Usage:
  open_notes.py update \\
    --store .viva/open-notes.json \\
    --round N \\
    --verdicts .viva/review-rN.json \\
    --input .viva/review-input-rN.json \\
    [--response "<cid>=one-line summary of the rewrite" ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import schema


def update(
    store: dict,
    round_num: int,
    verdicts: dict,
    input_data: dict,
    responses: dict,
) -> dict:
    """Apply one round's verdicts to the per-comment thread store. Pure.

    Each section carries a `comments` list; each comment is its own thread keyed
    by `cid`. For every comment:
      - open & changes/info → append an exchange (create the thread if new),
        carrying the agent's `responses[cid]`.
      - settled truthy      → mark that thread settled.
    Approving a section settles every still-open thread whose `cid` belongs to it
    (matched by the section's stable title), so approval clears the section's
    conversation. A section with no comments is a no-op (today's behavior).
    """
    titles = {s.get("id"): s.get("title", s.get("id"))
              for s in input_data.get("sections", [])}
    out = {k: {**v, "exchanges": list(v.get("exchanges", []))}
           for k, v in store.items()}

    for s in verdicts.get("sections", []):
        sid = s.get("id")
        title = titles.get(sid, sid or "?")
        verdict = s.get("verdict")
        comments = s.get("comments") or []

        if verdict == "approved":
            # Settle every open thread belonging to this section (by title).
            for thread in out.values():
                if (schema.section_key(thread.get("title")) == schema.section_key(title)
                        and thread.get("status") == "open"):
                    thread["status"] = "settled"
            continue

        for c in comments:
            cid = c.get("cid")
            if not cid:
                continue
            thread = out.get(cid)
            if c.get("settled"):
                # Settling is decisive: close the thread and ignore any note on
                # this turn (a reply typed then settled is intentionally dropped).
                if thread:
                    thread["status"] = "settled"
                continue
            if c.get("type") in ("changes", "info") and c.get("open"):
                anchor = c.get("anchor") or {}
                if thread is None:
                    thread = {"cid": cid, "title": title, "quote": anchor.get("text", ""),
                              "status": "open", "exchanges": []}
                    out[cid] = thread
                thread["status"] = "open"
                thread["title"] = title          # keep display title fresh
                if anchor.get("text"):
                    thread["quote"] = anchor["text"]
                thread["exchanges"].append({
                    "round": round_num,
                    "verdict": c.get("type"),
                    "note": c.get("note", ""),
                    "response": responses.get(cid, ""),
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
                    help='Agent response for a comment, as "<cid>=text" (repeatable)')
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
