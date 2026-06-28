#!/usr/bin/env python3
"""Required-section checklist gating — a viva pre-review producer (#13).

Doc types have an expected shape: a spec needs problem / non-goals / testing;
an ADR needs context / decision / consequences; a runbook needs trigger / steps
/ rollback. An agent-written doc that silently omits a required section passes
review only because the reviewer didn't notice the gap. This producer checks
the parsed headings against a per-type template and flags missing sections so
the absence is impossible to miss.

  python3 checklist.py --input .viva/review-input-r1.json [--type spec|adr|runbook]

Prints a sidecar annotation list (JSON) to stdout — pipe it into annotate.py:

  python3 checklist.py --input IN.json | python3 annotate.py --input IN.json --annotations -

A missing required section attaches an `error` flag to the *first* card rather
than a synthetic placeholder card — the parser's integrity check requires every
card's content to come from the source doc, so a card for a non-existent section
is impossible. The first card (preamble/H1) is the document-level anchor.

Doc type is taken from --type, else inferred from the filename or H1, else
nothing — an untyped doc emits no flags and reviews exactly as today.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Per-type required sections. Labels are shown to the reviewer; matching is done
# on a punctuation-insensitive normalized form so "Non-Goals", "Non Goals", and
# "Non-goals" all satisfy the same requirement.
TEMPLATES = {
    "spec": ["Problem", "Non-goals", "Testing"],
    "adr": ["Context", "Decision", "Consequences"],
    "runbook": ["Trigger", "Steps", "Rollback"],
}


def _norm(s: str) -> str:
    """Lowercase and strip every non-alphanumeric char for tolerant matching."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def infer_type(doc_file: str, sections: list) -> str | None:
    """Resolve a doc type from the filename or H1, or None if untyped.

    Filename wins (it's the most explicit author signal); the first section's
    title (usually the H1/preamble) is the fallback. Returns None when nothing
    matches — the caller then emits no gating.
    """
    haystacks = [doc_file or ""]
    if sections:
        haystacks.append(sections[0].get("title", ""))
    for hay in haystacks:
        n = _norm(hay)
        for doc_type in TEMPLATES:
            if doc_type in n:
                return doc_type
    return None


def missing_sections(template: list, sections: list) -> list:
    """Return the template labels with no matching section heading."""
    present = [_norm(s.get("title", "")) for s in sections]
    missing = []
    for label in template:
        key = _norm(label)
        if not any(key in title for title in present):
            missing.append(label)
    return missing


def build_sidecar(data: dict, doc_type: str | None) -> list:
    """Build the annotation sidecar for the resolved doc type."""
    sections = data.get("sections", [])
    if doc_type is None:
        doc_type = infer_type(data.get("doc_file", ""), sections)
    template = TEMPLATES.get(doc_type) if doc_type else None
    if not template or not sections:
        return []
    anchor_id = sections[0]["id"]
    return [
        {
            "id": anchor_id,
            "kind": "checklist",
            "severity": "error",
            "message": f"missing required {doc_type} section: '{label}'",
            "anchor": f"{doc_type} template",
        }
        for label in missing_sections(template, sections)
    ]


def main() -> None:
    p = argparse.ArgumentParser(description="Required-section checklist gating producer")
    p.add_argument("--input", required=True, help="Round review-input JSON")
    p.add_argument("--type", dest="doc_type", choices=sorted(TEMPLATES),
                   help="Doc type; inferred from filename/H1 when omitted")
    args = p.parse_args()

    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"checklist: cannot read {args.input}: {e}")

    sidecar = build_sidecar(data, args.doc_type)
    json.dump(sidecar, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
