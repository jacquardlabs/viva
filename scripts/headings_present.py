#!/usr/bin/env python3
r"""headings-present — the doc-type grammar check, as a pre-review producer (#169).

A type bundle's `sections[]` is the heading grammar a doc of that type is
expected to carry. This reports the ones the round does not have:

  python3 doc_types.py design-doc \
    | python3 headings_present.py --input .viva/review-input-r1.json --bundle - \
    | python3 annotate.py --input .viva/review-input-r1.json --annotations -

The bundle arrives on stdin (or `--bundle PATH`) rather than by type name, so
this stays a stateless filter that resolves nothing and imports no sibling but
`schema` (CLAUDE.md). A driver reads `checks[]` to decide which producers to
run; each check name maps to `<name with - as _>.py` in this directory.

Prints a sidecar annotation list (JSON) to stdout — one `warn` per missing
heading, all anchored on the FIRST card. `parse_sections.py`'s integrity check
requires every card's content to come from the source doc, so a card for a
section that does not exist is impossible; the first card (preamble/H1) is the
document-level anchor, the same constraint `checklist.py` works around.

`warn`, not `checklist.py`'s `error`: a type's grammar is the shape a doc of
that type is *expected* to take, not a hard template requirement. Both are
advisory either way — PRODUCT.md keeps annotations non-gating.

**Reading the results back.** After `annotate.py` merges the sidecar, each
result is an annotation with `kind == "headings-present"` on that first section
— that key is the handle a later completion check reads.

Two known limits, documented rather than papered over, because a completion rule
built on these results has to account for both:

  * A doc with nothing missing produces no annotations at all, so "ran, found
    nothing" and "never ran" look identical on disk. A run marker is completion
    semantics and belongs with the pass work, not here.
  * A flag can outlive the fix it asked for. `parse_sections._carry_annotations`
    copies a prior round's annotations onto a byte-identical section, and the
    first card is usually the preamble — adding the missing heading further down
    the doc leaves that card unchanged, so round N+1 carries the stale flag even
    though its own producer run finds nothing. `checklist.py` has the identical
    exposure for the same two reasons (document-level fact, first-card anchor);
    the carry rule is shared, so this is not a fix either producer can make
    alone.

Matching is identity — `schema.section_key`, the same normalization approvals,
round diffs, and open threads key on. Deliberately NOT `checklist.py`'s `_norm`,
which strips all punctuation for tolerant template matching: a type's grammar is
a heading list an author copies from a template, so an exact title is the right
bar, and CLAUDE.md keeps the fuzzy match and the identity rule separate.

Headings are collected from every section's title AND from the headings inside
its content. A `--split-on` round promotes only matching headings to cards — a
plan split on `^Task \d+` folds its `## Not-here follow-ups` heading into the
last card's body — so scanning titles alone would report a heading the doc
plainly has.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import schema

# The `kind` every flag this producer emits carries, and the key a later
# completion check finds those results by. Stable — renaming it orphans them.
KIND = "headings-present"

# ATX headings, per line, trailing closing `#`s stripped — the same shape
# `parse_sections.py._heading_lines` matches, applied to a card's body instead
# of the whole doc. Not imported: cross-importing a sibling is forbidden, and
# the identity rule (which IS shared) comes from `schema.section_key` below.
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
