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
                      (re.search, case-sensitive, any heading depth), replacing
                      auto-detection entirely. Zero matches is a hard error.
  --doc-type NAME    Record the doc type this round was started with (a name
                      `scripts/doc_types.py` resolves). Recorded only, never
                      resolved here — the parser owns no type semantics.
  --pass KIND        Record the depth this round runs at (architecture | line |
                      checks | final). Omit for a round with no pass, which
                      carries no `pass` key and completes exactly as it did
                      before the field existed.
  --posture P        normal | hard — a setting ON the pass, written inside the
                      `pass` object, never as its own round field. Needs --pass.
  --recheck          Re-certification (#83): seeds every section approved
                      from the doc's own `## Revision History` instead of a
                      prior round file. Refuses on an unsigned doc.

Exits non-zero if the doc can't be read, parsing fails the integrity check,
--split-on matches no heading, --recheck names an unsigned doc, or prior
round files are specified but can't be read.
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
    p.add_argument(
        "--doc-type",
        dest="doc_type",
        help="Doc-type name to record on the round (resolved by doc_types.py "
             "before it gets here). Omit for an untyped round.",
    )
    p.add_argument(
        "--pass",
        dest="pass_kind",
        choices=schema.PASS_KINDS,
        help="Depth this round runs at. Recorded only — the parser owns no pass "
             "semantics; `schema.round_is_complete` is what reads it. Omit for a "
             "round with no pass (today's behavior, unchanged).",
    )
    p.add_argument(
        "--posture",
        dest="posture",
        choices=schema.PASS_POSTURES,
        help="Posture setting on the pass ('hard' licenses the author to argue "
             "rather than concede). Requires --pass; absent reads as normal.",
    )
    p.add_argument("--prior-input", help="Prior round review-input JSON (for round 2+)")
    p.add_argument("--prior-verdicts", help="Prior round verdicts JSON (for round 2+)")
    p.add_argument("--open-notes", help="Open-note store JSON (.viva/open-notes.json)")
    p.add_argument(
        "--recheck",
        action="store_true",
        help="Re-certification (#83): refuses unless `doc` already carries a "
             "`## Revision History`. Seeds every section approved from the "
             "doc's own ledger — a drift producer withdraws approval from "
             "whichever sections it flags before the round arms. Recorded as "
             "`recheck: true`; --prior-input, when also given, carries a "
             "recheck's own round 1 forward the normal way.",
    )
    return p.parse_args()


def _heading_lines(lines: list[str]) -> list[tuple[int, str, int]]:
    """(level, title, line_idx) for every ATX heading line."""
    result = []
    for i, line in enumerate(lines):
        m = re.match(r'^(#{1,6})\s+(.+)', line.rstrip("\r\n"))
        if m:
            title = re.sub(r"\s+#+\s*$", "", m.group(2)).strip()
            result.append((len(m.group(1)), title, i))
    return result


def _find_split_level(headings: list[tuple[int, str, int]]) -> int | None:
    """Highest level (fewest #s) that repeats. If that level has >20
    sections, falls back one level coarser to reduce the count.
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

    split_on: optional regex (re.search, case-sensitive) — when given, a
    heading is a split point iff its title matches, regardless of `#` depth,
    replacing `_find_split_level` entirely (its >20-section coarsening
    fallback included). Omitted runs the auto-detect path.
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
        # Promote any coarser heading after the first split-level heading to a
        # split point too (before that is preamble/title territory). Each such
        # level occurs at most once per `_find_split_level`'s contract, so this
        # only adds distinct singleton headings.
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
    """Every source char (before Revision History) must appear in exactly one section."""
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

    A section stays approved only if its title matches (case-insensitive) and
    content is byte-identical to the prior approved version. The prior round's
    verdict outranks a carried `approved_ids` stamp: an ID not approved last
    round is dropped even if still listed, so a reopened section with no edit
    (e.g. a declined comment, #167) doesn't come back silently APPROVED.
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
    # …and the ones it explicitly did NOT approve, which revoke a carried stamp.
    # Truthy check, not `is not None`: a row with no verdict decides nothing and
    # leaves the stamp standing; `pending` is a withdrawal and drops it.
    withdrawn: set[str] = {
        s.get("id") for s in prior_v.get("sections", []) or []
        if s.get("id") and s.get("verdict") and s.get("verdict") != "approved"
    }
    all_approved = (pre_approved | verdict_approved) - withdrawn

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


def _carry_identical(
    prior_in: dict | None, new_sections: list[dict], field: str
) -> None:
    """Copy one prior-round section field onto byte-identical new sections.

    Shared by `_carry_annotations` and `_carry_summaries`: keyed on
    (normalized title, content) so a value survives only if the section it
    describes is unchanged. Title alone would carry onto a rewrite.
    """
    if prior_in is None:
        return
    prior: dict[tuple[str, str], object] = {
        (schema.section_key(s["title"]), s.get("content", "")): s[field]
        for s in prior_in.get("sections", [])
        if s.get(field)
    }
    if not prior:
        return
    for s in new_sections:
        key = (schema.section_key(s["title"]), s.get("content", ""))
        if key in prior:
            s[field] = prior[key]


def _carry_annotations(prior_in: dict | None, new_sections: list[dict]) -> None:
    """Carry prior annotations onto byte-identical new sections, in place.

    A flag survives only if the section's title and content are unchanged; a
    rewritten section may have addressed it, so annotations drop and the next
    pre-review pass can re-flag.
    """
    _carry_identical(prior_in, new_sections, "annotations")


def _carry_summaries(prior_in: dict | None, new_sections: list[dict]) -> None:
    """Carry prior one-line summaries onto byte-identical new sections, in place.

    Same rule as the annotation carry: a summary describes content, so a
    rewritten section's summary is stale and drops. A section carrying a
    `diff` this round therefore carries no `summary` (#188).
    """
    _carry_identical(prior_in, new_sections, "summary")


def _line_diff(prior: str, current: str) -> list[dict]:
    """Unified line diff prior→current as a list of {op, text} rows.

    op is '+' (added) | '-' (removed) | ' ' (context) | '@' (hunk header).
    The `--- / +++` file headers are dropped — the card already names the section.
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
        # File headers only appear before the first hunk; after that a
        # `--`/`++`-prefixed line is real content, not a header.
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
    (case-insensitive) and the content differs. Unchanged and brand-new
    sections get no `diff` key.
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

    The store (open_notes.py) is keyed by normalized title. A thread not yet
    `settled` (`open`, or `declined` by the author) re-presents on its section
    next round; that filter is the whole holding mechanism for a decline.
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
        if not schema.thread_is_unresolved(t.get("status")):
            continue
        by_title.setdefault(schema.section_key(t.get("title")), []).append({
            "cid": t.get("cid"),
            "quote": t.get("quote", ""),
            "status": t.get("status", schema.THREAD_OPEN),
            "exchanges": t.get("exchanges", []),
        })
    for threads in by_title.values():
        # `or ""`: a thread with cid explicitly None would sort None vs str.
        threads.sort(key=lambda t: t.get("cid") or "")
    for s in new_sections:
        key = schema.section_key(s.get("title"))
        if key in by_title:
            s["open_notes"] = by_title[key]


def main() -> None:
    args = _parse_args()

    # A posture with no pass is a setting on nothing — refused at the boundary.
    if args.posture is not None and args.pass_kind is None:
        sys.exit("viva: --posture needs --pass — a posture is a setting on a "
                 "pass, not a round field of its own")

    try:
        text = Path(args.doc).read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"viva: cannot read {args.doc}: {e}")

    if args.recheck and not schema.has_revision_history(text):
        sys.exit(f"viva: --recheck requires a signed doc — {args.doc} carries "
                 f"no ## Revision History")

    sections, rev_line = _split_sections(text, args.doc, args.split_on)

    if not sections:
        sys.exit(f"viva: no reviewable sections found in {args.doc}")

    _integrity_check(text, sections, rev_line)

    prior_in, prior_v = _load_prior(args.prior_input, args.prior_verdicts)
    if args.recheck and prior_in is None:
        # Round 1 of a recheck: no round file carries the doc's prior sign-off,
        # so the doc's own ledger IS the approval — seed every section, and
        # `recheck.py` withdraws whichever ones a drift flag lands on before
        # the round arms. Round 2+ of a recheck passes --prior-input, and the
        # normal carry below already does the right thing with no special
        # case: it intersects round 1's approved_ids with this round's
        # verdicts, keyed on title+content match.
        approved_ids = [s["id"] for s in sections]
    else:
        approved_ids = _load_approved(prior_in, prior_v, sections)
    _carry_annotations(prior_in, sections)
    _carry_summaries(prior_in, sections)
    _compute_diffs(prior_in, sections)
    _attach_open_notes(args.open_notes, sections)

    data = {
        "mode": "review",
        "doc_file": args.doc_file or Path(args.doc).name,
        "round": args.round_num,
        "approved_ids": approved_ids,
        "sections": sections,
    }
    # Written only when given: `loop.py rearm` reads `split_on` back to
    # re-split every later round the same way.
    if args.split_on is not None:
        data["split_on"] = args.split_on
    # Same rule for doc_type: round state `loop.py` reads back across rounds
    # and resumes.
    if args.doc_type is not None:
        data["doc_type"] = args.doc_type
    # A round with no pass must carry NO `pass` key — absence is what makes
    # `round_is_complete` fall through to the base rule.
    if args.pass_kind is not None:
        pass_spec = {"kind": args.pass_kind}
        if args.posture is not None:
            pass_spec["posture"] = args.posture
        data["pass"] = pass_spec
    # Round state `loop.py rearm` reads back and carries forward, same as
    # split_on/doc_type — a recheck's round 2 must still say so, or the
    # finishing ledger silently reads "Signed off" instead of "Re-certified".
    if args.recheck:
        data["recheck"] = True
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
