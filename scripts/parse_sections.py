#!/usr/bin/env python3
"""Fast markdown section splitter for viva.

Round 1:
  python3 parse_sections.py doc.md \\
    --output .viva/review-input-r1.json \\
    --round 1

Round 2+:
  python3 parse_sections.py doc.md \\
    --output .viva/review-input-r2.json \\
    --round 2 \\
    --prior-input .viva/review-input-r1.json \\
    --prior-verdicts .viva/review-r1.json

Optional:
  --doc-file PATH    Relative path shown in UI (defaults to the doc argument)

Exits non-zero if the doc can't be read, parsing fails the integrity check,
or prior round files are specified but can't be read.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Parse markdown into viva review-input JSON")
    p.add_argument("doc", help="Path to the markdown file")
    p.add_argument("--output", required=True, help="Path to write review-input JSON")
    p.add_argument("--round", type=int, required=True, dest="round_num")
    p.add_argument("--doc-file", help="Relative path shown in UI (defaults to --doc)")
    p.add_argument("--prior-input", help="Prior round review-input JSON (for round 2+)")
    p.add_argument("--prior-verdicts", help="Prior round verdicts JSON (for round 2+)")
    return p.parse_args()


def _heading_lines(lines: list[str]) -> list[tuple[int, str, int]]:
    """Return (level, title, line_idx) for every ATX heading line."""
    result = []
    for i, line in enumerate(lines):
        m = re.match(r'^(#{1,6})\s+(.+)', line.rstrip("\r\n"))
        if m:
            title = re.sub(r"\s+#+\s*$", "", m.group(2)).strip()
            result.append((len(m.group(1)), title, i))
    return result


def _find_split_level(headings: list[tuple[int, str, int]]) -> int | None:
    """Find the heading level to split on per SKILL.md rules.

    Picks the highest level (fewest #s) that occurs more than once.
    If that level yields > 20 sections, falls back one level coarser
    (fewer #s) to reduce section count.
    """
    if not headings:
        return None
    counts = Counter(h[0] for h in headings)
    for level in sorted(counts.keys()):
        if counts[level] > 1:
            if counts[level] > 20 and level > 1:
                coarser = level - 1
                if counts.get(coarser, 0) >= 2:
                    return coarser
            return level
    # Every level appears only once — use the highest (fewest #s)
    return min(counts.keys())


def _split_sections(text: str, doc_path: str) -> tuple[list[dict], int | None]:
    """Split markdown into sections. Returns (sections, revision_history_line_idx)."""
    lines = text.splitlines(keepends=True)
    headings = _heading_lines(lines)
    split_level = _find_split_level(headings)

    if split_level is None:
        return [{"id": "s1", "title": Path(doc_path).stem, "content": text}], None

    h1_title = next((h[1] for h in headings if h[0] == 1), None)
    split_headings = [(lv, t, idx) for lv, t, idx in headings if lv == split_level]

    rev_line: int | None = next(
        (h[2] for h in split_headings if h[1].strip().lower() == "revision history"),
        None,
    )
    active = [(lv, t, idx) for lv, t, idx in split_headings
              if t.strip().lower() != "revision history"]

    sections: list[dict] = []

    if not active:
        end = rev_line if rev_line is not None else len(lines)
        content = "".join(lines[:end])
        if content.strip():
            return [{"id": "s1", "title": h1_title or Path(doc_path).stem, "content": content}], rev_line
        return [], rev_line

    # Preamble: everything before the first active split heading
    preamble = "".join(lines[: active[0][2]])
    if preamble.strip():
        sections.append({"id": "_", "title": h1_title or "Preamble", "content": preamble})

    # Each active section: heading line through the line before the next boundary
    for i, (_, title, line_idx) in enumerate(active):
        if i + 1 < len(active):
            end_line = active[i + 1][2]
        elif rev_line is not None:
            end_line = rev_line
        else:
            end_line = len(lines)
        sections.append({"id": "_", "title": title, "content": "".join(lines[line_idx:end_line])})

    for i, s in enumerate(sections):
        s["id"] = f"s{i + 1}"

    return sections, rev_line


def _integrity_check(text: str, sections: list[dict], rev_line: int | None) -> None:
    """Every non-exempt source char must appear in exactly one section."""
    lines = text.splitlines(keepends=True)
    end = rev_line if rev_line is not None else len(lines)
    source = "".join(lines[:end])
    reconstructed = "".join(s["content"] for s in sections)
    if source == reconstructed:
        return
    for i, (a, b) in enumerate(zip(source, reconstructed)):
        if a != b:
            sys.exit(
                f"viva integrity check failed at char {i}:\n"
                f"  source:   {source[max(0, i - 20):i + 30]!r}\n"
                f"  sections: {reconstructed[max(0, i - 20):i + 30]!r}"
            )
    sys.exit(
        f"viva integrity check failed: source={len(source)} chars, "
        f"sections={len(reconstructed)} chars"
    )


def _load_approved(
    prior_input_path: str | None,
    prior_verdicts_path: str | None,
    new_sections: list[dict],
) -> list[str]:
    """Carry forward approved IDs by title+content equality.

    A section is only kept approved if its title matches exactly (case-insensitive)
    AND its content is byte-for-byte identical to the prior approved version.
    Changed content requires re-review.
    """
    if not prior_input_path or not prior_verdicts_path:
        return []
    try:
        prior_in = json.loads(Path(prior_input_path).read_text(encoding="utf-8"))
        prior_v = json.loads(Path(prior_verdicts_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"viva: could not read prior round files: {e}")

    by_id: dict[str, dict] = {s["id"]: s for s in prior_in.get("sections", [])}

    # IDs that were already approved coming into the prior round
    pre_approved: set[str] = set(prior_in.get("approved_ids", []))
    # IDs that got an approved verdict in the prior round
    verdict_approved: set[str] = {
        s["id"] for s in prior_v.get("sections", []) if s.get("verdict") == "approved"
    }
    all_approved = pre_approved | verdict_approved

    # Map title (lower) → content for every approved section
    approved_content: dict[str, str] = {
        by_id[sid]["title"].strip().lower(): by_id[sid].get("content", "")
        for sid in all_approved
        if sid in by_id
    }

    return [
        s["id"]
        for s in new_sections
        if s["title"].strip().lower() in approved_content
        and s["content"] == approved_content[s["title"].strip().lower()]
    ]


def main() -> None:
    args = _parse_args()

    try:
        text = Path(args.doc).read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"viva: cannot read {args.doc}: {e}")

    sections, rev_line = _split_sections(text, args.doc)

    if not sections:
        sys.exit(f"viva: no reviewable sections found in {args.doc}")

    _integrity_check(text, sections, rev_line)

    approved_ids = _load_approved(args.prior_input, args.prior_verdicts, sections)

    data = {
        "mode": "review",
        "doc_file": args.doc_file or Path(args.doc).name,
        "round": args.round_num,
        "approved_ids": approved_ids,
        "sections": sections,
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"viva: wrote {len(sections)} sections → {out}"
        + (f" ({len(approved_ids)} pre-approved)" if approved_ids else ""),
        flush=True,
    )


if __name__ == "__main__":
    main()
