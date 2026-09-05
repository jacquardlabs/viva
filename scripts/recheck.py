#!/usr/bin/env python3
"""recheck.py — withdraw a recheck's (#83) seeded approval per drift flag.

  python3 recheck.py --input .viva/review-input-r1.json

`loop.py start --recheck` seeds every section approved from the doc's own
`## Revision History` (`parse_sections.py --recheck`), then runs `drift.py`
and merges its flags through `annotate.py`. This script is the last step
before the round arms: it removes from `approved_ids` every section that now
carries an annotation of a withdrawing `kind` — `drift` by default, `--kind`
repeatable for a producer other than the shipped one. A section drift did
not flag stays approved and collapses on the card, same as an ordinary
review's carried approval.

CONVENTION EXCEPTION, like annotate.py: modifies `--input` IN PLACE and has
no `--output`.

Prints the withdrawn count to stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def withdraw(data: dict, kinds: set[str]) -> int:
    """Remove from `data["approved_ids"]` every section carrying an
    annotation whose kind is in `kinds`. Mutates and returns the count
    withdrawn."""
    flagged = {
        s["id"]
        for s in data.get("sections", []) or []
        if any(isinstance(a, dict) and a.get("kind") in kinds
               for a in (s.get("annotations") or []))
    }
    approved = data.get("approved_ids") or []
    withdrawn = [i for i in approved if i in flagged]
    if withdrawn:
        data["approved_ids"] = [i for i in approved if i not in flagged]
    return len(withdrawn)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Withdraw a recheck's seeded approval per drift flag")
    p.add_argument("--input", required=True,
                   help="Round review-input JSON (modified in place)")
    p.add_argument("--kind", action="append", dest="kinds", default=None,
                   help="Withdrawing annotation kind, repeatable "
                        "(default: drift)")
    args = p.parse_args()

    inp = Path(args.input)
    try:
        data = json.loads(inp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"recheck: cannot read {args.input}: {e}")

    kinds = set(args.kinds) if args.kinds else {"drift"}
    n = withdraw(data, kinds)

    inp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"recheck: {n} section(s) withdrawn from approval", flush=True)


if __name__ == "__main__":
    main()
