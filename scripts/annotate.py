#!/usr/bin/env python3
"""Shared annotation-merge helper for viva pre-review producers.

Every producer (claim grounding #9, contradiction #10, spec↔code drift #11,
checklist gating #13) computes its flags and writes them through this one path.
It merges a sidecar list of annotations into the round's review-input file,
in place, after parse_sections.py has generated it and before the round is armed.

  python3 annotate.py --input .viva/review-input-r2.json --annotations flags.json

Sidecar format — a JSON list, each item targeting a section by id:

  [
    {"id": "s3", "kind": "grounding", "severity": "warn",
     "message": "claim 'sub-second' unsupported", "anchor": "line 12"},
    {"id": "s3", "kind": "drift", "severity": "error",
     "message": "code retries 3x, doc says 5x"}
  ]

The merge is:
  - additive    — appends to any existing `annotations` (carried-forward flags survive);
  - validated   — severity normalized to info|warn|error, kind/message required;
  - idempotent  — an identical flag already present is not re-added;
  - answering   — a flag that matches one already there except for its `result`
                  writes that result onto it rather than appending a twin;
  - a no-op     — an empty sidecar leaves the input byte-identical.

CONVENTION EXCEPTION: unlike every other script, annotate.py modifies `--input`
IN PLACE and has no `--output` — producers pipe flags into the round file the
server will read. See DESIGN.md → CLI conventions.

Reads the sidecar from --annotations PATH, or from stdin when the path is '-'.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SEVERITIES = {"info", "warn", "error"}


def _clean(item: dict) -> dict | None:
    """Validate/normalize one sidecar item into {"id", "annotation"}, or
    None if unusable (no id, no message).
    """
    if not isinstance(item, dict):
        return None
    sid = item.get("id")
    message = item.get("message")
    if not sid or not message:
        return None
    severity = item.get("severity")
    annot = {
        "kind": str(item.get("kind") or "note"),
        "severity": severity if severity in SEVERITIES else "info",
        "message": str(message),
    }
    if item.get("anchor"):
        annot["anchor"] = str(item["anchor"])
    # Confidence sort keys the server's weakest-first toggle reads (#40).
    if item.get("basis") in ("sourced", "inferred"):
        annot["basis"] = item["basis"]
    if item.get("level") in ("high", "medium", "low"):
        annot["level"] = item["level"]
    # A check's finding (schema.CHECK_KINDS). Non-empty by construction: a
    # blank result answers nothing, and a `checks` round stays held until
    # every check flag carries one.
    if isinstance(item.get("result"), str) and item["result"].strip():
        annot["result"] = item["result"]
    return {"id": str(sid), "annotation": annot}


def _same_flag(a: dict, b: dict) -> bool:
    """Are these the same flag, ignoring its result?

    The result is the ANSWER to a flag, not part of its identity, so a
    producer re-emitting a flag with a result lands on the existing flag
    instead of appending a result-less twin.
    """
    return ({k: v for k, v in a.items() if k != "result"}
            == {k: v for k, v in b.items() if k != "result"})


def merge_annotations(data: dict, sidecar: list) -> dict:
    """Merge sidecar annotations into `data` sections in place; return `data`.

    Unknown ids are skipped. Duplicate flags (same kind/severity/message/anchor
    already on the section) are skipped so a re-run can't double them; one that
    differs only by carrying a `result` answers the flag already there.
    """
    by_id = {s["id"]: s for s in data.get("sections", []) if "id" in s}
    for item in sidecar or []:
        cleaned = _clean(item)
        if cleaned is None:
            continue
        section = by_id.get(cleaned["id"])
        if section is None:
            print(f"annotate: no section {cleaned['id']!r} — skipping flag",
                  file=sys.stderr)
            continue
        annots = section.setdefault("annotations", [])
        new = cleaned["annotation"]
        existing = next((a for a in annots if _same_flag(a, new)), None)
        if existing is None:
            annots.append(new)
        elif "result" in new:
            existing["result"] = new["result"]
        # Drop an empty list we may have just created.
        if not annots:
            del section["annotations"]
    return data


def main() -> None:
    p = argparse.ArgumentParser(description="Merge producer annotations into a review-input")
    p.add_argument("--input", required=True, help="Round review-input JSON (modified in place)")
    p.add_argument("--annotations", required=True,
                   help="Sidecar JSON list, or '-' to read from stdin")
    args = p.parse_args()

    inp = Path(args.input)
    try:
        data = json.loads(inp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"annotate: cannot read {args.input}: {e}")

    try:
        raw = sys.stdin.read() if args.annotations == "-" \
            else Path(args.annotations).read_text(encoding="utf-8")
        sidecar = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"annotate: cannot read annotations: {e}")
    if not isinstance(sidecar, list):
        sys.exit("annotate: sidecar must be a JSON list of annotation objects")

    merge_annotations(data, sidecar)

    inp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    added = sum(len(s.get("annotations", [])) for s in data.get("sections", []))
    print(f"annotate: {args.input} now carries {added} annotation(s)", flush=True)


if __name__ == "__main__":
    main()
