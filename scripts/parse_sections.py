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
  --split-on REGEX   Split on any heading whose title matches this pattern
                      (re.search, case-sensitive, any heading depth), instead
                      of auto-detecting a split level. Replaces the level
                      -counting heuristic entirely, including its >20-section
                      coarsening fallback. Omit for today's unchanged
                      auto-detect behavior. Zero matches is a hard error, not
                      a silent fallback to auto-detection.

Exits non-zero if the doc can't be read, parsing fails the integrity check,
--split-on matches no heading, or prior round files are specified but can't
be read.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import schema


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Parse markdown into viva review-input JSON")
    p.add_argument("doc", help="Path to the markdown file")
    p.add_argument("--output", required=True, help="Path to write review-input JSON")
    p.add_argument("--round", type=int, required=True, dest="round_num")
    p.add_argument("--doc-file", help="Relative path shown in UI (defaults to --doc)")
    p.add_argument(
        "--split-on",
        dest="split_on",
        help="Regex (re.search): a heading is a split point iff its title matches, "
             "regardless of depth. Overrides auto-detection. Omit for unchanged "
             "default behavior.",
    )
    p.add_argument("--prior-input", help="Prior round review-input JSON (for round 2+)")
    p.add_argument("--prior-verdicts", help="Prior round verdicts JSON (for round 2+)")
    p.add_argument("--open-notes", help="Open-note store JSON (.viva/open-notes.json)")
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


def _split_sections(
    text: str, doc_path: str, split_on: str | None = None
) -> tuple[list[dict], int | None]:
    """Split markdown into sections. Returns (sections, revision_history_line_idx).

    split_on: optional regex (re.search, case-sensitive). When given, it is the
    sole selection rule — a heading is a split point iff its title matches,
    regardless of `#` depth — and replaces `_find_split_level`'s level-counting
    heuristic entirely, including its >20-section coarsening fallback (an
    explicit caller-supplied pattern isn't a heuristic guess that needs
    protecting from over-splitting). When omitted, this runs exactly the
    auto-detect path it always has — that branch is untouched.
    """
    lines = text.splitlines(keepends=True)
    headings = _heading_lines(lines)

    if split_on is not None:
        try:
            pattern = re.compile(split_on)
        except re.error as e:
            sys.exit(f"viva: invalid --split-on pattern {split_on!r}: {e}")
        split_headings = [(lv, t, idx) for lv, t, idx in headings if pattern.search(t)]
        if not split_headings:
            sys.exit(f"viva: --split-on {split_on!r} matched no heading in {doc_path}")
    else:
        split_level = _find_split_level(headings)
        if split_level is None:
            return [{"id": "s1", "title": Path(doc_path).stem, "content": text}], None
        split_headings = [(lv, t, idx) for lv, t, idx in headings if lv == split_level]
        # Promote any heading coarser than the detected split level to a split
        # point too, as long as it occurs after the first split-level heading
        # (before that is preamble/title territory — see design doc's
        # "Alternatives considered" #2 for why the idx guard matters). Per
        # `_find_split_level`'s "coarsest repeater wins" contract, every level
        # coarser than `split_level` occurs at most once in the whole
        # document, so this can only ever add distinct, singleton headings —
        # never re-trigger a coarsest-repeater ambiguity.
        first_split_idx = split_headings[0][2]
        coarser = [
            (lv, t, idx) for lv, t, idx in headings
            if lv < split_level and idx > first_split_idx
        ]
        if coarser:
            split_headings = sorted(split_headings + coarser, key=lambda h: h[2])

    h1_title = next((h[1] for h in headings if h[0] == 1), None)

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


def _load_prior(
    prior_input_path: str | None,
    prior_verdicts_path: str | None,
) -> tuple[dict | None, dict | None]:
    """Read the prior round's input and verdict files once, or (None, None)."""
    if not prior_input_path or not prior_verdicts_path:
        return None, None
    try:
        prior_in = json.loads(Path(prior_input_path).read_text(encoding="utf-8"))
        prior_v = json.loads(Path(prior_verdicts_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"viva: could not read prior round files: {e}")
    return prior_in, prior_v


def _load_approved(
    prior_in: dict | None,
    prior_v: dict | None,
    new_sections: list[dict],
) -> list[str]:
    """Carry forward approved IDs by title+content equality.

    A section is only kept approved if its title matches exactly (case-insensitive)
    AND its content is byte-for-byte identical to the prior approved version.
    Changed content requires re-review.
    """
    if prior_in is None or prior_v is None:
        return []

    by_id: dict[str, dict] = {s["id"]: s for s in prior_in.get("sections", [])}

    # IDs that were already approved coming into the prior round
    pre_approved: set[str] = set(prior_in.get("approved_ids", []))
    # IDs that got an approved verdict in the prior round
    verdict_approved: set[str] = {
        s["id"] for s in prior_v.get("sections", []) if s.get("verdict") == "approved"
    }
    all_approved = pre_approved | verdict_approved

    # Map section identity → content for every approved section
    approved_content: dict[str, str] = {
        schema.section_key(by_id[sid]["title"]): by_id[sid].get("content", "")
        for sid in all_approved
        if sid in by_id
    }

    return [
        s["id"]
        for s in new_sections
        if schema.section_key(s["title"]) in approved_content
        and s["content"] == approved_content[schema.section_key(s["title"])]
    ]


def _carry_annotations(prior_in: dict | None, new_sections: list[dict]) -> None:
    """Carry prior annotations onto byte-identical new sections, in place.

    Annotations are advisory flags from a pre-review pass. A flag is only still
    valid if the section's title and content are unchanged; a rewritten section
    may already have addressed the flag, so its annotations are dropped (the
    next round's pre-review pass can re-flag). Sections that never carried
    annotations gain no `annotations` key — output stays byte-identical to a
    no-annotation run.
    """
    if prior_in is None:
        return
    prior_annots: dict[tuple[str, str], list] = {
        (schema.section_key(s["title"]), s.get("content", "")): s["annotations"]
        for s in prior_in.get("sections", [])
        if s.get("annotations")
    }
    if not prior_annots:
        return
    for s in new_sections:
        key = (schema.section_key(s["title"]), s.get("content", ""))
        if key in prior_annots:
            s["annotations"] = prior_annots[key]


def _line_diff(prior: str, current: str) -> list[dict]:
    """Unified line diff prior→current as a list of {op, text} rows.

    op ∈ '+' (added) | '-' (removed) | ' ' (context) | '@' (hunk header).
    The leading `--- / +++` file headers are dropped — the card already names
    the section. Trailing newlines are stripped per line for clean rendering.
    """
    rows: list[dict] = []
    diff = difflib.unified_diff(
        prior.splitlines(), current.splitlines(), n=3, lineterm=""
    )
    seen_hunk = False
    for line in diff:
        if line.startswith("@@"):
            seen_hunk = True
            rows.append({"op": "@", "text": line})
        # The `--- / +++` file headers only appear before the first hunk; after
        # that a `--`/`++`-prefixed line is real content, not a header.
        elif not seen_hunk and (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        elif line.startswith("+"):
            rows.append({"op": "+", "text": line[1:]})
        elif line.startswith("-"):
            rows.append({"op": "-", "text": line[1:]})
        else:  # context line — unified_diff prefixes a single space
            rows.append({"op": " ", "text": line[1:]})
    return rows


def _compute_diffs(prior_in: dict | None, new_sections: list[dict]) -> None:
    """Attach a round-to-round `diff` onto rewritten sections, in place.

    A section gets a diff when a prior-round section shares its title
    (case-insensitive) and the content differs. Byte-identical carried sections
    and brand-new sections (no prior title match) get no `diff` key — output
    stays byte-identical to a no-prior run for those.
    """
    if prior_in is None:
        return
    prior_by_title: dict[str, str] = {
        schema.section_key(s["title"]): s.get("content", "")
        for s in prior_in.get("sections", [])
    }
    for s in new_sections:
        prior_content = prior_by_title.get(schema.section_key(s["title"]))
        if prior_content is not None and prior_content != s["content"]:
            s["diff"] = _line_diff(prior_content, s["content"])


def _attach_open_notes(open_notes_path: str | None, new_sections: list[dict]) -> None:
    """Attach each open thread's exchanges onto the matching section, in place.

    The open-note store (maintained by open_notes.py) is keyed by normalized
    title. A thread still `open` re-presents on its section's card next round so
    the reviewer sees the prior exchange; a settled thread is dropped. Sections
    with no open thread gain no `open_notes` key — output stays byte-identical to
    a run without the store.
    """
    if not open_notes_path:
        return
    path = Path(open_notes_path)
    if not path.exists():
        return
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"viva: could not read open-notes store {open_notes_path}: {e}")
    by_title: dict[str, list] = {}
    for t in store.values():
        if t.get("status") != "open":
            continue  # settled threads drop from later rounds
        by_title.setdefault(schema.section_key(t.get("title")), []).append({
            "cid": t.get("cid"),
            "quote": t.get("quote", ""),
            "status": t.get("status", "open"),
            "exchanges": t.get("exchanges", []),
        })
    for threads in by_title.values():
        # `or ""` (not a default arg): a thread whose cid is explicitly None
        # would otherwise sort None against str and raise TypeError.
        threads.sort(key=lambda t: t.get("cid") or "")
    for s in new_sections:
        key = schema.section_key(s.get("title"))
        if key in by_title:
            s["open_notes"] = by_title[key]


def main() -> None:
    args = _parse_args()

    try:
        text = Path(args.doc).read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"viva: cannot read {args.doc}: {e}")

    sections, rev_line = _split_sections(text, args.doc, args.split_on)

    if not sections:
        sys.exit(f"viva: no reviewable sections found in {args.doc}")

    _integrity_check(text, sections, rev_line)

    prior_in, prior_v = _load_prior(args.prior_input, args.prior_verdicts)
    approved_ids = _load_approved(prior_in, prior_v, sections)
    _carry_annotations(prior_in, sections)
    _compute_diffs(prior_in, sections)
    _attach_open_notes(args.open_notes, sections)

    data = {
        "mode": "review",
        "doc_file": args.doc_file or Path(args.doc).name,
        "round": args.round_num,
        "approved_ids": approved_ids,
        "sections": sections,
    }
    # Validate at the boundary, on write, so a malformed round file never
    # reaches the server or a downstream reader.
    schema.validate_review_input(data)

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
