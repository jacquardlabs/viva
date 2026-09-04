#!/usr/bin/env python3
r"""headings-present — the doc-type grammar check, as a pre-review producer (#169).

Reports which headings from a type bundle's `sections[]` the round is
missing:

  python3 doc_types.py design-doc \
    | python3 headings_present.py --input .viva/review-input-r1.json --bundle - \
    | python3 annotate.py --input .viva/review-input-r1.json --annotations -

Prints a sidecar annotation list (JSON) to stdout — one `warn` per missing
heading (not `checklist.py`'s `error`: a type's grammar is expected shape,
not a hard requirement), all anchored on the first card, since a card for a
nonexistent section is impossible under the parser's integrity check.

Matching is `schema.section_key` identity, not `checklist.py`'s fuzzy `_norm`
— a type's grammar is a heading list copied from a template, so an exact
title is the right bar.

Headings are collected from every section's title AND from headings inside
its content, since a `--split-on` round can fold a real heading into the
previous card's body.

Two known limits: a doc with nothing missing looks identical to "never ran"
on disk (no run marker here), and a flag can outlive its fix when the carry
rule (`parse_sections._carry_annotations`) copies it onto an unchanged first
card — `checklist.py` shares both exposures.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import schema

# The `kind` every flag this producer emits carries. Stable — renaming it
# orphans them from later completion checks.
KIND = "headings-present"

# ATX headings, per line, trailing closing `#`s stripped.
_HEADING_RE = re.compile(r"(?m)^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$")


def present_keys(sections: list) -> set:
    """Every heading identity the round carries — card titles plus in-body
    headings."""
    keys = set()
    for s in sections:
        keys.add(schema.section_key(s.get("title") or ""))
        for m in _HEADING_RE.finditer(s.get("content") or ""):
            keys.add(schema.section_key(m.group(1)))
    keys.discard("")
    return keys


def missing_sections(bundle: dict, sections: list) -> list:
    """The bundle's expected headings that the round does not carry, in bundle
    order."""
    present = present_keys(sections)
    return [h for h in (bundle.get("sections") or [])
            if schema.section_key(h) not in present]


def build_sidecar(data: dict, bundle: dict) -> list:
    """Build the annotation sidecar for one round against one type bundle."""
    sections = data.get("sections") or []
    if not sections or not sections[0].get("id"):
        return []
    name = bundle.get("name") or "doc"
    anchor_id = sections[0]["id"]
    return [
        {
            "id": anchor_id,
            "kind": KIND,
            "severity": "warn",
            "message": f"missing expected {name} section: '{heading}'",
            "anchor": f"{name} grammar",
        }
        for heading in missing_sections(bundle, sections)
    ]


def main() -> None:
    p = argparse.ArgumentParser(
        description="headings-present doc-type grammar producer")
    p.add_argument("--input", required=True, help="Round review-input JSON")
    p.add_argument("--bundle", required=True,
                   help="Type bundle JSON from doc_types.py, or '-' for stdin")
    args = p.parse_args()

    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"headings_present: cannot read {args.input}: {e}")

    try:
        raw = sys.stdin.read() if args.bundle == "-" \
            else Path(args.bundle).read_text(encoding="utf-8")
        bundle = json.loads(raw)
    except (OSError, ValueError) as e:
        sys.exit(f"headings_present: cannot read bundle: {e}")
    if not isinstance(bundle, dict):
        sys.exit("headings_present: bundle must be a JSON object — pipe "
                 "`doc_types.py <name>` into --bundle -")

    json.dump(build_sidecar(data, bundle), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
