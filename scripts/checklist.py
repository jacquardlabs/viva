#!/usr/bin/env python3
"""Required-section checklist gating — a viva pre-review producer (#13).

Checks parsed headings against a per-doc-type template (spec, adr, runbook)
and flags missing sections so an agent-written doc can't silently omit one.

  python3 checklist.py --input .viva/review-input-r1.json [--type spec|adr|runbook]

Prints a sidecar annotation list (JSON) to stdout — pipe it into annotate.py.
A missing section attaches an `error` flag to the first card (the document
anchor) rather than a synthetic placeholder card, since every card must come
from the source doc. Doc type is taken from --type, else inferred from the
filename or H1, else nothing (untyped docs emit no flags).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Per-type required sections. Matching is punctuation-insensitive, so
# "Non-Goals", "Non Goals", and "Non-goals" all satisfy the same requirement.
TEMPLATES = {
    "spec": ["Problem", "Non-goals", "Testing"],
    "adr": ["Context", "Decision", "Consequences"],
    "runbook": ["Trigger", "Steps", "Rollback"],
}


def _norm(s: str) -> str:
    """Lowercase and strip every non-alphanumeric char for tolerant matching."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _tokens(s: str) -> set:
    """Lowercased alphanumeric tokens — used for whole-word type inference."""
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if t}


def infer_type(doc_file: str, sections: list) -> str | None:
    """Resolve a doc type from the filename or H1 (filename wins), matching
    a whole token so 'inspector.md' doesn't infer 'spec'. None if untyped."""
    haystacks = [doc_file or ""]
    if sections:
        haystacks.append(sections[0].get("title", ""))
    for hay in haystacks:
        toks = _tokens(hay)
        for doc_type in TEMPLATES:
            if doc_type in toks:
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
