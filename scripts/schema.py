#!/usr/bin/env python3
"""Shared schema contract for viva's `.viva/*.json` round files.

This is the one module `scripts/*.py` and `server.py` import to agree on the
load-bearing pieces of the protocol:

- **Section identity** — `section_key()`, the single normalization used to match
  a section across rounds (approvals, carried annotations, diffs, open threads).
- **The ledger rule** — `verdict_to_ledger_entry()`, the single source of truth
  for which verdicts become a Revision-History row and how the note is derived.
- **The round shapes** — `TypedDict`s documenting `review-input-r{N}.json` and
  `review-r{N}.json` (documentation only; CI runs no type checker).
- **Boundary validation** — `validate_review_input()` / `validate_verdicts()`,
  called where data enters the system so a missed producer fails loudly instead
  of silently corrupting a downstream reader.

stdlib-only, no runtime dependency. It is the single shared sibling: every other
script stays standalone and imports nothing but this.

`GET /input` shape note (issue #58): the server serves the review-input merged
with a live `ledger: [...]` key — `json.dumps({**_input_data, "ledger": _ledger})`.
That `ledger` field is injected by the server at serve time and is **not** part of
the `review-input-r{N}.json` file schema the `ReviewInput` TypedDict describes.
"""
from __future__ import annotations

from typing import List, Optional, TypedDict

# Verdicts that earn a Revision-History ledger row. `approved`/`pending` do not.
LEDGER_VERDICTS = ("changes", "info")
# Every verdict a review output section may carry.
VERDICTS = ("approved", "changes", "info", "pending")


# ── Section identity ──────────────────────────────────────────────────────────
def section_key(title: str) -> str:
    """Canonical section identity: case-folded, edge-trimmed title.

    The ONE normalization that matches a section across rounds — approvals,
    carried annotations, round diffs, and open-note threads all key on it, so a
    title edit changes identity in exactly one place.

    Deliberately distinct from `checklist.py`'s `_norm`, which strips *all*
    non-alphanumeric characters for tolerant template matching. That is a fuzzy
    match, this is an identity; do not merge the two.
    """
    return (title or "").strip().lower()


# ── Ledger rule ───────────────────────────────────────────────────────────────
def is_ledger_verdict(verdict: object) -> bool:
    """True iff a section's verdict earns a ledger row (requested changes or
    asked a question)."""
    return verdict in LEDGER_VERDICTS


def ledger_note(section: dict) -> str:
    """The verbatim note for a ledger row.

    Multi-comment sections (issue #68) carry their notes in `comments[]`; their
    notes are joined with ` · `. Older single-note sections fall back to the
    section's own `note`. An empty result is normal (a `changes` with no text).
    """
    comments = section.get("comments") or []
    if comments:
        return " · ".join(c.get("note", "") for c in comments if c.get("note"))
    return section.get("note", "")


def verdict_to_ledger_entry(
    rnd: int, section_title: str, section: dict
) -> Optional[dict]:
    """The single source of truth for one ledger row.

    Returns `{round, section_title, verdict, note}` for a `changes`/`info`
    section, or `None` if the verdict earns no row. Used by both the server's
    live `/input` ledger and `revision_history.py`'s on-disk Revision History
    table, so the two surfaces never drift.
    """
    if not is_ledger_verdict(section.get("verdict")):
        return None
    return {
        "round": rnd,
        "section_title": section_title,
        "verdict": section["verdict"],
        "note": ledger_note(section),
    }


# ── Round shapes (documentation-only TypedDicts) ──────────────────────────────
# CI runs no type checker (tests execute the files), so these document the
# contract for humans and editors; `validate_*` below carry the enforced rules.
class Annotation(TypedDict, total=False):
    kind: str       # required — producer tag / badge label
    severity: str   # required — info | warn | error
    message: str    # required — inline text
    # `anchor` is overloaded (see DESIGN.md → JSON protocol conventions):
    #   - a display string → rendered as the badge's hover `title`, OR
    #   - another section's id → rendered as a deep-link (contradiction producer).
    # NOT the same as a comment's `anchor`, which is a {text, offset} selection
    # object in the OUTPUT schema (SectionVerdict.comments) — a different shape.
    anchor: str
    basis: str      # confidence only — sourced | inferred
    level: str      # confidence only — high | medium | low


class ReviewSection(TypedDict, total=False):
    id: str                       # required — stable id (s1, s2, …)
    title: str                    # required — heading text
    content: str                  # required — verbatim markdown
    annotations: List[Annotation]  # optional — advisory badges
    diff: dict                    # optional — round-to-round change
    open_notes: list              # optional — carried-forward threads


class ReviewInput(TypedDict, total=False):
    mode: str                       # "review"
    doc_file: str                   # relative path for the UI
    round: int                      # round number
    approved_ids: List[str]         # ids approved in prior rounds
    sections: List[ReviewSection]


class SectionVerdict(TypedDict, total=False):
    id: str        # required — section id
    verdict: str   # required — one of VERDICTS
    # optional — typed comment threads (issue #68). Each comment may carry an
    # `anchor` object {text, offset}: the reviewer's exact selection, used to
    # scope the rewrite. Distinct from Annotation.anchor (a string) above.
    comments: list


class ReviewOutput(TypedDict, total=False):
    round: int
    submitted_early: bool
    sections: List[SectionVerdict]


# ── Boundary validation ───────────────────────────────────────────────────────
def validate_review_input(data: dict) -> None:
    """Raise `ValueError` if `data` is not a structurally valid review-input.

    Enforces only the load-bearing invariants — top-level shape and the
    `id`/`title`/`content` identity triple on every section — and stays
    permissive about optional feature fields. Call at the boundary:
    `parse_sections.py` on write, `server.py` on read (review mode only).
    """
    if not isinstance(data, dict):
        raise ValueError("review-input must be a JSON object")
    sections = data.get("sections")
    if not isinstance(sections, list):
        raise ValueError("review-input.sections must be a list")
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            raise ValueError(f"review-input.sections[{i}] must be an object")
        for field in ("id", "title", "content"):
            if not isinstance(s.get(field), str):
                raise ValueError(
                    f"review-input.sections[{i}] missing required string {field!r}"
                )


def validate_verdicts(data: dict) -> None:
    """Raise `ValueError` if `data` is not a structurally valid review output
    (`review-r{N}.json`).

    Enforces that every section carries an `id` and a known `verdict`. Permissive
    about comments/attachments. Only meaningful for review-mode output (sections);
    callers gate on `"sections" in data` so Q&A `answers` payloads are skipped.
    """
    if not isinstance(data, dict):
        raise ValueError("review output must be a JSON object")
    sections = data.get("sections")
    if not isinstance(sections, list):
        raise ValueError("review output.sections must be a list")
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            raise ValueError(f"review output.sections[{i}] must be an object")
        if not isinstance(s.get("id"), str):
            raise ValueError(f"review output.sections[{i}] missing required string 'id'")
        if s.get("verdict") not in VERDICTS:
            raise ValueError(
                f"review output.sections[{i}] has invalid verdict {s.get('verdict')!r}"
            )


# ── Q&A round shapes ──────────────────────────────────────────────────────────
class QAQuestion(TypedDict, total=False):
    id: str           # required
    text: str         # required
    hint: str         # optional — shown below the question text
    choices: List[str]  # optional — rendered as chip buttons
    # optional — must exactly match one entry in `choices` (value, not index;
    # see validate_qa_input). Renders as a small badge on that chip. Advisory
    # only, per PRODUCT.md's "advisory, never gating" principle: never
    # pre-selected, defaulted, or required — the human picks whichever chip
    # they want, including a different one. Ignored/absent on every question
    # authored before this field existed — no render change without it.
    recommended_choice: str


class QAInput(TypedDict, total=False):
    mode: str                   # "qa"
    context: str                # one-liner shown in the title block
    questions: List[QAQuestion]


class QAAnswer(TypedDict, total=False):
    id: str               # question id
    choice: str           # selected chip value
    note: str             # free-text field value
    attachments: List[str]  # server-written image paths


class QAOutput(TypedDict, total=False):
    answers: List[QAAnswer]
    submitted_early: bool


class DiffInput(TypedDict, total=False):
    """Diff-mode input — same structure as ReviewInput, mode='diff'."""
    mode: str                       # "diff"
    doc_file: str                   # ref description shown in UI
    round: int
    approved_ids: List[str]
    sections: List[ReviewSection]   # one entry per hunk


def validate_qa_input(data: dict) -> None:
    """Raise `ValueError` if `data` is not a structurally valid Q&A input.

    Enforces that every question has `id` and `text`, and — when present —
    that `recommended_choice` is a string that exactly matches an entry in
    that same question's own `choices`. A dangling or typo'd recommendation
    (no `choices`, or a value not in it) is a loud startup failure here,
    never a silent no-badge misfire at render time. Permissive about other
    optional fields (`hint`, `choices`, `context`). Call at startup when
    `--mode qa`.
    """
    if not isinstance(data, dict):
        raise ValueError("qa-input must be a JSON object")
    questions = data.get("questions")
    if not isinstance(questions, list):
        raise ValueError("qa-input.questions must be a list")
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            raise ValueError(f"qa-input.questions[{i}] must be an object")
        for field in ("id", "text"):
            if not isinstance(q.get(field), str):
                raise ValueError(
                    f"qa-input.questions[{i}] missing required string {field!r}"
                )
        if "recommended_choice" in q:
            recommended = q.get("recommended_choice")
            if not isinstance(recommended, str):
                raise ValueError(
                    f"qa-input.questions[{i}].recommended_choice must be a string"
                )
            # Presence guard before membership test: `choices` may be absent
            # entirely, and `or` short-circuits so a non-list `choices` never
            # reaches the `in` check below.
            choices = q.get("choices")
            if not isinstance(choices, list) or recommended not in choices:
                raise ValueError(
                    f"qa-input.questions[{i}].recommended_choice {recommended!r} "
                    "does not match any entry in that question's own choices"
                )
