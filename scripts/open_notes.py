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
      "status": "open",            # schema.THREAD_STATUSES: open|declined|settled
      "exchanges": [
        {"round": 1, "verdict": "changes", "note": "5x not 3x", "response": "Done."}
        # a `suggestion` turn also carries "replacement": the exact wording
        # a DECLINED turn also carries "grounds": why the author did not comply
      ]
    }
  }

`declined` is the author's turn, not a verdict (see `schema.THREAD_STATUSES`).
It resolves nothing: the thread carries into the next round exactly as an open
one does, so the section stays held until the reviewer either settles it
(accepting the decline) or replies (insisting). **Insisting wins** — a reply
re-opens the thread and the author has no second decline on it; this script
refuses one.

Usage:
  open_notes.py update \\
    --store .viva/open-notes.json \\
    --round N \\
    --verdicts .viva/review-rN.json \\
    --input .viva/review-input-rN.json \\
    [--response "<cid>=one-line summary of the rewrite" ...] \\
    [--decline "<cid>=why you did not comply" ...]
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
    declines: dict | None = None,
) -> dict:
    """Apply one round's verdicts to the per-comment thread store. Pure.

    Each section carries a `comments` list; each comment is its own thread keyed
    by `cid`. For every comment:
      - open & a known type (`schema.COMMENT_TYPES`) → append an exchange
        (create the thread if new), carrying the agent's `responses[cid]` and,
        for a suggestion, the replacement wording.
      - settled truthy      → mark that thread settled.
    Approving a section settles every still-unresolved thread whose `cid` belongs
    to it (matched by the section's stable title), so approval clears the
    section's conversation — including a declined one, which is how the reviewer
    accepts a decline. A section with no comments is a no-op (today's behavior).

    `declines[cid]` is the author's grounds for *not* complying with that turn.
    It rides on the same exchange the turn creates (`grounds`) and moves the
    thread to `schema.THREAD_DECLINED`. A `responses[cid]` may accompany it —
    grounds are why the author did not comply, a response is what they did
    instead — and neither settles anything.

    Raises `ValueError` on a second decline of the same thread: the reviewer has
    already seen those grounds and re-requested, so the turn is spent. A decline
    for a comment the reviewer settled this round is dropped, as a response is —
    settling is decisive, and there is no turn left to answer.
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
            # Settle every unresolved thread belonging to this section (by
            # title) — open or declined, since approving the section IS how the
            # reviewer accepts a decline. Mutating `thread["status"]` in place is
            # safe: `out` holds fresh copies (`{**v, ...}` above), not aliases
            # into the input `store`.
            for thread in out.values():
                if (schema.section_key(thread.get("title")) == schema.section_key(title)
                        and schema.thread_is_unresolved(thread.get("status"))):
                    thread["status"] = schema.THREAD_SETTLED
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
                    thread["status"] = schema.THREAD_SETTLED
                continue
            if c.get("type") in schema.COMMENT_TYPES and c.get("open"):
                anchor = c.get("anchor") or {}
                if thread is None:
                    thread = {"cid": cid, "title": title, "quote": anchor.get("text", ""),
                              "status": schema.THREAD_OPEN, "exchanges": []}
                    out[cid] = thread
                # A reviewer turn returns a declined thread to `open` — this one
                # assignment is insisting-wins: they answered the decline, so the
                # author's refusal no longer stands and the request is live again.
                thread["status"] = schema.THREAD_OPEN
                thread["title"] = title          # keep display title fresh
                if anchor.get("text"):
                    thread["quote"] = anchor["text"]
                exchange = {
                    "round": round_num,
                    "verdict": c.get("type"),
                    "note": c.get("note", ""),
                    "response": responses.get(cid, ""),
                }
                # A suggestion's payload is the wording, so it rides on the
                # exchange too: without it, round N+1 re-presents the thread
                # with the rationale and the replacement stripped, and "apply
                # verbatim" has nothing left to apply. Presence-gated — a
                # changes/info exchange stays byte-identical to today's.
                if c.get("replacement"):
                    exchange["replacement"] = c["replacement"]
                # The author's turn. `is not None`, not truthiness: a decline
                # with no grounds is still a decline — it just reads weaker than
                # one with them — and the KEY is what marks the turn declined.
                grounds = (declines or {}).get(cid)
                if grounds is not None:
                    if any("grounds" in x for x in thread["exchanges"]):
                        raise ValueError(
                            f"{cid} was already declined and the reviewer has "
                            f"re-requested it — insisting wins, so there is no "
                            f"second decline on this thread. Comply and record "
                            f"it with --response instead."
                        )
                    exchange["grounds"] = grounds
                    # After the open branch above deliberately: a declined turn
                    # is unresolved, not open, and only the reviewer's next move
                    # settles it or returns it to open.
                    thread["status"] = schema.THREAD_DECLINED
                thread["exchanges"].append(exchange)
    return out


def _parse_pairs(pairs: list) -> dict:
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
    up.add_argument("--decline", action="append", default=[],
                    help='Decline a comment with grounds, as "<cid>=why you did '
                         'not comply" (repeatable). The thread goes `declined`, '
                         'which resolves nothing — one decline per thread.')
    args = p.parse_args()

    store_path = Path(args.store)
    store = json.loads(store_path.read_text(encoding="utf-8")) if store_path.exists() else {}
    verdicts = _load_json(args.verdicts)
    input_data = _load_json(args.input)
    responses = _parse_pairs(args.response)
    declines = _parse_pairs(args.decline)

    try:
        store = update(store, args.round_num, verdicts, input_data, responses,
                       declines)
    except ValueError as e:
        # Nothing is written on a refusal: the store on disk still records the
        # first decline, which is the state the reviewer is answering.
        sys.exit(f"viva open_notes: {e}")

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    open_threads = sum(1 for t in store.values()
                       if t.get("status") == schema.THREAD_OPEN)
    declined = sum(1 for t in store.values()
                   if t.get("status") == schema.THREAD_DECLINED)
    print(f"viva: open-note store updated → {store_path} ({open_threads} open"
          + (f", {declined} declined" if declined else "") + ")",
          flush=True)


if __name__ == "__main__":
    main()
