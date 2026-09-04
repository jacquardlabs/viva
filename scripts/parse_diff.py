#!/usr/bin/env python3
"""Parse git diff output into viva review-input JSON (one section per hunk).

Round 1:
  python3 parse_diff.py .viva/diff.patch \\
    --output .viva/review-input-r1.json --round 1 \\
    [--doc-file "HEAD~1..HEAD"]

Round 2+:
  python3 parse_diff.py .viva/diff.patch \\
    --output .viva/review-input-r2.json --round 2 \\
    --prior-input .viva/review-input-r1.json \\
    --prior-verdicts .viva/review-r1.json \\
    [--doc-file "HEAD~1..HEAD"]

Exits non-zero if the patch file cannot be read, if it contains no parseable
hunks, or if prior round files are specified but cannot be read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import section_key, validate_review_input


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Parse git diff into viva review-input JSON"
    )
    p.add_argument("patch", help="Path to a git diff patch file")
    p.add_argument("--output", required=True, help="Path to write review-input JSON")
    p.add_argument("--round", type=int, required=True, dest="round_num")
    p.add_argument("--doc-file", default="working tree",
                   help="Description shown in the UI title block")
    p.add_argument("--prior-input",
                   help="Prior round review-input JSON (round 2+)")
    p.add_argument("--prior-verdicts",
                   help="Prior round verdicts JSON (round 2+)")
    return p.parse_args()


def _extract_filepath(file_block: str) -> str | None:
    """Target filepath from a single file-level diff block."""
    # "+++ b/path" — covers modified and new files
    m = re.search(r'^\+\+\+ b/(.+)$', file_block, re.MULTILINE)
    if m:
        return m.group(1).rstrip()
    # "--- a/path" — covers deleted files (where +++ is /dev/null)
    m = re.search(r'^--- a/(.+)$', file_block, re.MULTILINE)
    if m and m.group(1).strip() != '/dev/null':
        return m.group(1).rstrip()
    # Fallback: "diff --git a/X b/X"
    m = re.match(r'diff --git a/(.+?) b/', file_block)
    if m:
        return m.group(1).rstrip()
    return None


def _is_binary(file_block: str) -> bool:
    """True if the file block has no hunk headers (binary or pure metadata)."""
    return not re.search(r'^@@ ', file_block, re.MULTILINE)


def _parse_hunks(file_block: str, filepath: str, start_id: int) -> list[dict]:
    """Split one file's diff block into per-hunk section dicts."""
    sections: list[dict] = []
    # Split at every "@@ ... @@" line, keeping the delimiter
    parts = re.split(r'^(?=@@ )', file_block, flags=re.MULTILINE)
    hunk_index = 0
    current_id = start_id
    for part in parts:
        if not part.startswith('@@'):
            continue
        hunk_index += 1
        sections.append({
            "id": f"s{current_id}",
            "title": f"{filepath} hunk {hunk_index}",
            "content": f"```diff\n{part.rstrip()}\n```",
        })
        current_id += 1
    return sections


def parse_diff(text: str) -> list[dict]:
    """Parse a full git diff text into a flat list of section dicts."""
    sections: list[dict] = []
    # Split on "diff --git" file headers
    file_blocks = re.split(r'^(?=diff --git )', text, flags=re.MULTILINE)
    for file_block in file_blocks:
        if not file_block.strip():
            continue
        filepath = _extract_filepath(file_block)
        if filepath is None:
            continue
        start_id = len(sections) + 1
        if _is_binary(file_block):
            sections.append({
                "id": f"s{start_id}",
                "title": f"{filepath} hunk 1",
                "content": "Binary file changed — no content to review",
            })
        else:
            sections.extend(_parse_hunks(file_block, filepath, start_id))
    return sections


def _hunk_body(content: str) -> str:
    """The hunk with its `@@ -a,b +c,d @@` header removed — the identity the
    carry rules compare on.

    The header shifts whenever an earlier hunk in the same file grows or
    shrinks, with no line of this hunk touched; comparing whole content made
    that drop approval/summary with no notice (#103). Header still renders —
    this is the comparison key only. A binary sentinel has no header.
    """
    if not content.startswith("```diff\n@@ "):
        return content
    first_newline = content.index("\n", len("```diff\n"))
    return content[:len("```diff\n")] + content[first_newline + 1:]


def _carry_forward(
    sections: list[dict],
    prior_input: dict | None,
    prior_verdicts: dict | None,
) -> list[str]:
    """Return ids of sections approved in the prior round with an identical body.

    Carries forward only when normalized title matches a prior approved
    section AND the hunk body (`_hunk_body`, header stripped) is identical —
    parse_sections.py's rule, with the diff-specific allowance that a
    header-only line shift is not a change.
    """
    if not prior_input or not prior_verdicts:
        return []

    prior_by_key: dict[str, dict] = {}
    for s in prior_input.get("sections", []):
        k = section_key(s.get("title", ""))
        prior_by_key[k] = s

    pre_approved = set(prior_input.get("approved_ids", []))
    verdict_approved = {
        sv.get("id")
        for sv in prior_verdicts.get("sections", [])
        if sv.get("verdict") == "approved"
    }
    all_prior_approved = pre_approved | verdict_approved

    approved_ids: list[str] = []
    for s in sections:
        k = section_key(s["title"])
        prior_s = prior_by_key.get(k)
        if (
            prior_s is not None
            and prior_s.get("id") in all_prior_approved
            and _hunk_body(s["content"]) == _hunk_body(prior_s.get("content", ""))
        ):
            approved_ids.append(s["id"])
    return approved_ids


def _carry_summaries(sections: list[dict], prior_input: dict | None) -> None:
    """Carry each prior hunk's one-line summary onto its unchanged twin, in place.

    Summaries are written by the agent between parsing and arming (#188).
    Keyed on (normalized title, hunk body) — the same identity `_carry_forward`
    uses, so a changed body drops the summary for a fresh one. Hunk numbering
    alone is not identity: an added hunk shifts later `hunk N` titles, which
    still drops both carries (#196).
    """
    if not prior_input:
        return
    prior = {
        (section_key(s.get("title", "")), _hunk_body(s.get("content", ""))): s["summary"]
        for s in prior_input.get("sections", [])
        if s.get("summary")
    }
    if not prior:
        return
    for s in sections:
        key = (section_key(s["title"]), _hunk_body(s["content"]))
        if key in prior:
            s["summary"] = prior[key]


def _atomic_write(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def main() -> int:
    args = _parse_args()

    try:
        text = Path(args.patch).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"parse_diff: cannot read {args.patch}: {e}", file=sys.stderr)
        return 1

    sections = parse_diff(text)
    if not sections:
        print("parse_diff: no hunks found in diff", file=sys.stderr)
        return 1

    prior_input: dict | None = None
    prior_verdicts: dict | None = None

    if args.prior_input:
        try:
            prior_input = json.loads(
                Path(args.prior_input).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as e:
            print(f"parse_diff: cannot read prior-input: {e}", file=sys.stderr)
            return 1

    if args.prior_verdicts:
        try:
            prior_verdicts = json.loads(
                Path(args.prior_verdicts).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as e:
            print(f"parse_diff: cannot read prior-verdicts: {e}", file=sys.stderr)
            return 1

    approved_ids = _carry_forward(sections, prior_input, prior_verdicts)
    _carry_summaries(sections, prior_input)

    data: dict = {
        "mode": "diff",
        "doc_file": args.doc_file,
        "round": args.round_num,
        "approved_ids": approved_ids,
        "sections": sections,
    }
    validate_review_input(data)
    _atomic_write(args.output, json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
