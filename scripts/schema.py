#!/usr/bin/env python3
"""Shared schema contract for viva's `.viva/*.json` round files.

The one module `scripts/*.py` and `server.py` import: section identity
(`section_key`), the ledger rule (`verdict_to_ledger_entry`), round-shape
`TypedDict`s, the completion rule (`round_is_complete`), and boundary
validation (`validate_review_input`/`validate_verdicts`). stdlib-only —
every other script stays standalone and imports nothing but this.

`GET /input` (#58) serves the review-input merged with a live `ledger: [...]`
key, injected by the server at serve time — not part of the on-disk schema
`ReviewInput` describes.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple, TypedDict

# `Path` is used only in type annotations (never evaluated, thanks to
# `from __future__ import annotations`) and deliberately NOT imported —
# `test_schema_reaches_no_io` bans `pathlib` so nothing here can reach disk.

# Bare token for a section/question `id`: no `"`, `<`, `>`, `'`, `&`, or
# whitespace, since `server.py` interpolates `id` unescaped into HTML
# attributes (`buildReviewCard`/`buildQACard`).
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# Verdicts that earn a Revision-History ledger row. `approved`/`pending` do not.
LEDGER_VERDICTS = ("changes", "info")
# Every verdict a review output section may carry.
VERDICTS = ("approved", "changes", "info", "pending")

# ── Passes — depth and posture as round parameters ────────────────────────────
# The four depths a round can run at. Optional on `ReviewInput`; absent means
# today's behavior exactly (PRODUCT.md principle 4). `doc_types.py`'s
# `default_pass` must name one of these.
PASS_KINDS = ("architecture", "line", "checks", "final")
# A setting ON the pass, not a second round field — `hard` licenses the
# author to argue rather than concede. Absent reads as `normal`.
PASS_POSTURES = ("normal", "hard")

# Annotation `kind`s a check producer emits — the handle `round_is_complete`
# reads to find a `checks` round's flags. A new check producer ADDS ITS KIND
# HERE or a `checks` pass never sees its flags. Advisory producers (drift,
# checklist, contradiction, confidence, preference) are not check flags.
CHECK_KINDS = ("headings-present",)

# The scope a producer's flag is ABOUT — a DIFFERENT AXIS from CHECK_KINDS
# ("does this gate a checks round"). `headings_present.py`/`checklist.py`
# report whole-document facts but anchor to `sections[0]["id"]` (the only
# document-level handle `parse_sections.py`'s integrity check leaves them);
# registering here renders them once in the document slip instead of five
# times in section 1's margin. Fails open: an unregistered kind is treated
# as section-scope.
DOC_SCOPE_KINDS = ("headings-present", "checklist")

# A reviewer's suggested-edit comment type: the exact `replacement` wording
# for the anchored span, applied VERBATIM — no rewrite pass.
SUGGESTION = "suggestion"
# Every type a comment may carry. A DIFFERENT AXIS from `VERDICTS`: a type is
# per-comment and reviewer-chosen; `suggestion` derives to section verdict
# `changes` and is never a verdict itself (DESIGN.md's derivation rule).
# `open_notes.py` threads on this tuple.
COMMENT_TYPES = ("changes", "info", SUGGESTION)

# ── Open-note thread statuses ─────────────────────────────────────────────────
# One thread per comment `cid` in `.viva/open-notes.json` (`open_notes.py`
# is the sole writer). `declined` is the AUTHOR's turn — non-compliance with
# `grounds` recorded. A THREAD status, not a verdict: a section with one
# applied and one declined comment has no coherent section verdict (#167).
# Only the reviewer settles; a decline never closes anything on its own.
THREAD_OPEN = "open"
THREAD_SETTLED = "settled"
THREAD_DECLINED = "declined"
THREAD_STATUSES = (THREAD_OPEN, THREAD_SETTLED, THREAD_DECLINED)

# One human-facing label per status, shared by the web tab
# (`__THREAD_STATUS_LABELS__`) and `revision_history.py`'s report, so both
# surfaces describe an event the same way. Every `THREAD_STATUSES` member
# must have an entry — an absence here is a bug, not a silent fallback.
THREAD_STATUS_LABELS = {
    THREAD_OPEN: "open note",
    THREAD_DECLINED: "author kept as-is",
    THREAD_SETTLED: "settled",
}

# ── Q&A recommendation grounds (issue #175) ───────────────────────────────────
# How a QAQuestion's `recommended_choice` was arrived at. Named independently
# of `open_notes.py`'s decline `grounds` (unrelated shape/file, same English
# word). A DIFFERENT AXIS from the confidence `basis` tuple below: that is a
# reviewer's self-report on a document SECTION; this classifies a
# recommendation the agent offers on a Q&A QUESTION, with a third value basis
# has no room for.
#   sourced  — names its provenance: ticket, codebase standard, measurement,
#              prior ledger ruling.
#   inferred — best practice with no local provenance; an opinion, not fact.
#   taste    — no recommendation offered at all; `recommended_choice` must be
#              absent (validate_qa_input rejects the two together).
QA_GROUNDS = ("sourced", "inferred", "taste")


def thread_is_unresolved(status: object) -> bool:
    """Is this thread still live — does it carry into the next round?

    `open` and `declined` both are; `settled` is the only closed status.
    Membership, not `!= settled`, so an unknown status is never silently
    treated as live.
    """
    return status in (THREAD_OPEN, THREAD_DECLINED)


# ── Section identity ──────────────────────────────────────────────────────────
def section_key(title: str) -> str:
    """Canonical section identity: case-folded, edge-trimmed title.

    The ONE normalization matching a section across rounds — approvals,
    carried annotations, diffs, open threads — so a title edit changes
    identity in exactly one place. Distinct from `checklist.py`'s `_norm`
    (a fuzzy match, not an identity); do not merge the two.
    """
    return (title or "").strip().lower()


# ── Ledger rule ───────────────────────────────────────────────────────────────
def is_ledger_verdict(verdict: object) -> bool:
    """True iff a section's verdict earns a ledger row (requested changes or
    asked a question)."""
    return verdict in LEDGER_VERDICTS


def _comment_fragment(comment: dict) -> str:
    """One comment's contribution to a ledger note.

    A `changes`/`info` comment contributes its note. A `suggestion` also
    contributes the replacement wording VERBATIM, tagged `suggested:` — the
    only place a ledger reader learns wording was supplied rather than
    described.
    """
    note = comment.get("note", "") or ""
    if comment.get("type") != SUGGESTION:
        return note
    replacement = comment.get("replacement", "") or ""
    if not replacement:
        return note
    return (note + " — suggested: " + replacement) if note else ("suggested: " + replacement)


def ledger_note(section: dict) -> str:
    """The verbatim note for a ledger row.

    Multi-comment sections (#68) join `comments[]` fragments with ` · `.
    Older single-note sections fall back to the section's own `note`. An
    empty result is normal (a `changes` with no text).
    """
    comments = section.get("comments") or []
    if comments:
        frags = [_comment_fragment(c) for c in comments if isinstance(c, dict)]
        return " · ".join(f for f in frags if f)
    return section.get("note", "")


def verdict_to_ledger_entry(
    rnd: int, section_title: str, section: dict
) -> Optional[dict]:
    """The single source of truth for one ledger row.

    Returns `{round, section_title, verdict, note}` for a `changes`/`info`
    section, else `None`. Used by both the live `/input` ledger and
    `revision_history.py`'s on-disk table, so the two never drift.
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
    # Overloaded (DESIGN.md → JSON protocol conventions): a display string
    # (badge hover `title`) or another section's id (deep-link, contradiction
    # producer). NOT SectionVerdict.comments' anchor, a {text, offset} object.
    anchor: str
    basis: str      # confidence only — sourced | inferred
    level: str      # confidence only — high | medium | low
    # confidence only — the evidence named at write time: what was read for a
    # `sourced` claim, what was searched and not found for an `inferred` one.
    source: str
    # optional — the check's finding for this flag (producer-written, or
    # merged later by `annotate.py`). Load-bearing only for a `checks` pass:
    # `round_is_complete()` holds until every `CHECK_KINDS` flag has one.
    result: str


class ReviewSection(TypedDict, total=False):
    id: str                       # required — stable id (s1, s2, …)
    title: str                    # required — heading text
    content: str                  # required — verbatim markdown
    # optional — one line describing what this section IS, agent-written,
    # rendered under the card title. Never derived from or folded into
    # `title` (branch B parses `title` as `{filepath} hunk N`, #188). Carried
    # round to round only onto a byte-identical section; else drops.
    summary: str
    annotations: List[Annotation]  # optional — advisory badges
    diff: dict                    # optional — round-to-round change
    # optional — carried-forward threads (`parse_sections._attach_open_notes`'s
    # projection of `.viva/open-notes.json`): `{cid, quote, status, exchanges}`,
    # only unresolved threads attach, each exchange
    # `{round, verdict, note, response}` plus presence-gated `replacement`/
    # `grounds`.
    open_notes: list


class ReviewPass(TypedDict, total=False):
    kind: str      # required when a pass is present — one of PASS_KINDS
    posture: str   # optional — one of PASS_POSTURES; absent reads as `normal`


# `ReviewInput.pass` — optional depth/posture; absent is today's behavior
# exactly (PRODUCT.md principle 4) and can only make `round_is_complete()`
# stricter, never looser. Recorded by `parse_sections.py`; carried within a
# session by `loop.py rearm`, NOT across a resume (a per-round decision,
# unlike `split_on`/`doc_type`). `server.py` renders nothing from it.
#
# Functional form because `pass` is a Python keyword; mixed into ReviewInput.
_ReviewInputPass = TypedDict("_ReviewInputPass", {"pass": ReviewPass}, total=False)


class ReviewInput(_ReviewInputPass, total=False):
    mode: str                       # "review"
    doc_file: str                   # relative path for the UI
    round: int                      # round number
    approved_ids: List[str]         # ids approved in prior rounds
    # optional — `--split-on` regex this round was parsed with, so `loop.py
    # rearm` re-splits round N+1 identically. Absent = auto-detected split.
    split_on: str
    # optional — resolved doc-type name (`scripts/doc_types.py`), carried
    # round to round like `split_on`. Passthrough — `server.py` ignores it.
    doc_type: str
    # `pass` — see `_ReviewInputPass` above; the key cannot be spelled here.
    sections: List[ReviewSection]


class SectionVerdict(TypedDict, total=False):
    id: str        # required — section id
    verdict: str   # required — one of VERDICTS
    # optional — typed comment threads (#68). `type` is one of COMMENT_TYPES,
    # optional `note`, optional `anchor` {text, offset, occurrence?} scoping
    # the rewrite (`offset` -1 when it can't resolve against markdown, #95).
    # Distinct from Annotation.anchor (a string).
    #
    # A `suggestion` also carries `replacement`: exact wording, applied
    # verbatim — reviewer-authored and BINDING, unlike an Annotation (#166).
    # `validate_verdicts` requires it non-empty.
    comments: list


class ReviewOutput(TypedDict, total=False):
    round: int
    submitted_early: bool
    sections: List[SectionVerdict]


# ── Boundary validation ───────────────────────────────────────────────────────
def default_round(data: dict) -> dict:
    """Give a review-input an explicit `round`, in place, and return it.

    `round` is optional on the wire; a hand-built payload that omits it would
    otherwise print "REV undefined" in the tab and break the round-2 landing
    (NaN comparison against `undefined`). NORMALIZE at the boundary rather
    than reject — one is the only defensible default for an unnumbered round.

    Called at the server's two read boundaries. `validate_review_input` does
    the other half: a present-but-malformed `round` is a hard failure there.
    """
    if not isinstance(data, dict):
        return data
    if "round" not in data:
        data["round"] = 1
    return data


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
    # Presence-gated: optional key, but a present non-string is a hard
    # failure — `loop.py rearm` hands this straight to `parse_sections.py
    # --split-on`, and a `null` would silently re-split by auto-detection.
    if "split_on" in data and not isinstance(data["split_on"], str):
        raise ValueError("review-input.split_on must be a string")
    # Same reason: `loop.py`'s resume/rearm hand this to `parse_sections.py
    # --doc-type`, and a `null` would silently drop the type (and its checks).
    if "doc_type" in data and not isinstance(data["doc_type"], str):
        raise ValueError("review-input.doc_type must be a string")
    # Presence-gated, NOT required (round has always been optional; ~85
    # fixtures omit it). A malformed value prints "REV undefined" and breaks
    # the round-2 NaN comparison client-side with no error. Absence is
    # NORMALIZED at the server's read boundary (`default_round`), not
    # rejected. `bool` excluded: it's an `int` subclass and `True` would
    # render as round 01.
    if "round" in data and (
        isinstance(data["round"], bool)
        or not isinstance(data["round"], int)
        or data["round"] < 1
    ):
        raise ValueError(
            "review-input.round must be an integer >= 1 — omit the key entirely "
            "for a round that does not number itself, never null"
        )
    # Presence-gated: `pass` is the only round field that changes when
    # `POST /complete` may succeed, so a malformed value must fail where it
    # was written rather than silently reverting to the base rule.
    if "pass" in data:
        spec = data["pass"]
        if not isinstance(spec, dict):
            raise ValueError(
                "review-input.pass must be an object {kind, posture} — omit the "
                "key entirely for a round that runs no pass, never null")
        if spec.get("kind") not in PASS_KINDS:
            raise ValueError(
                "review-input.pass.kind %r is not one of %s"
                % (spec.get("kind"), "|".join(PASS_KINDS)))
        if "posture" in spec and spec["posture"] not in PASS_POSTURES:
            raise ValueError(
                "review-input.pass.posture %r is not one of %s"
                % (spec.get("posture"), "|".join(PASS_POSTURES)))
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            raise ValueError(f"review-input.sections[{i}] must be an object")
        for field in ("id", "title", "content"):
            if not isinstance(s.get(field), str):
                raise ValueError(
                    f"review-input.sections[{i}] missing required string {field!r}"
                )
        if not ID_RE.match(s["id"]):
            raise ValueError(
                f"review-input.sections[{i}].id {s['id']!r} must match "
                f"{ID_RE.pattern!r} — it reaches an HTML attribute context "
                f"unescaped (server.py's buildReviewCard)"
            )
        # Presence-gated: reaches a render site, so a `null` or stray object
        # would print as "null"/"[object Object]" under the card title.
        if "summary" in s and not isinstance(s["summary"], str):
            raise ValueError(
                f"review-input.sections[{i}].summary must be a string"
            )


def validate_verdicts(data: dict) -> None:
    """Raise `ValueError` if `data` is not a structurally valid review output
    (`review-r{N}.json`).

    Enforces `id` and a known `verdict` on every section, plus one gated
    comment rule: a `suggestion` must carry replacement wording. Callers gate
    on `"sections" in data` so Q&A `answers` payloads are skipped.
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
        if not ID_RE.match(s["id"]):
            raise ValueError(
                f"review output.sections[{i}].id {s['id']!r} must match {ID_RE.pattern!r}"
            )
        if s.get("verdict") not in VERDICTS:
            raise ValueError(
                f"review output.sections[{i}] has invalid verdict {s.get('verdict')!r}"
            )
        # Gated on comment TYPE: only a `suggestion` is checked. The wording
        # IS the comment — an empty one is unappliable, so this fails loud
        # here rather than silently at apply time.
        for j, c in enumerate(s.get("comments") or []):
            if not isinstance(c, dict) or c.get("type") != SUGGESTION:
                continue
            replacement = c.get("replacement")
            if not isinstance(replacement, str) or not replacement.strip():
                raise ValueError(
                    f"review output.sections[{i}].comments[{j}] is a "
                    f"{SUGGESTION!r} with no replacement wording"
                )


REVISION_HISTORY_RE = re.compile(r"(?m)^## Revision History\s*$")


def has_revision_history(doc_text: str) -> bool:
    """Has this doc already been signed off — i.e. is a `start` a resume?

    Anchored, never a substring test: a bare `in` also matches the phrase
    inside backticks or prose (both SKILL.md and DESIGN.md mention it), which
    would falsely take the resume branch and pre-approve an unseen section.
    `loop.py`'s resume detection and `revision_history.py`'s append-vs-create
    branch both ask this.
    """
    return REVISION_HISTORY_RE.search(doc_text) is not None


def _check_flags(input_data: dict) -> list:
    """Every check-producer flag on this round — annotations whose `kind` is
    in `CHECK_KINDS`."""
    return [a
            for s in input_data.get("sections", []) or []
            for a in (s.get("annotations") or [])
            if isinstance(a, dict) and a.get("kind") in CHECK_KINDS]


def _flag_is_answered(flag: dict) -> bool:
    """Does this check flag carry a result — i.e. did the check come back?

    A non-empty string `result`; a blank one answers nothing.
    """
    result = flag.get("result")
    return isinstance(result, str) and result.strip() != ""


def _has_unresolved_suggestion(input_data: dict, verdicts: dict) -> bool:
    """Does this round carry a suggested edit nobody has settled?

    Checks two places: the verdicts just submitted (a `suggestion` comment
    not marked `settled`) and the round's carried open threads (one whose
    latest exchange's `verdict` is `suggestion`). An exchange's `verdict` is
    the REVIEWER's comment type, never the author's answer — a declined
    suggestion still holds a `final` round, since only the reviewer's settle
    resolves it.
    """
    for s in verdicts.get("sections", []) or []:
        for c in s.get("comments") or []:
            if (isinstance(c, dict) and c.get("type") == SUGGESTION
                    and not c.get("settled")):
                return True
    for s in input_data.get("sections", []) or []:
        for thread in s.get("open_notes") or []:
            exchanges = (thread or {}).get("exchanges") or []
            last = exchanges[-1] if exchanges else None
            if isinstance(last, dict) and last.get("verdict") == SUGGESTION:
                return True
    return False


def round_file_paths(viva_dir: Path, n: int) -> Tuple[Path, Path]:
    """The `(review-input-r{n}.json, review-r{n}.json)` pair for round `n`.

    The one place the round-file naming convention is spelled out. Pure
    name-to-path formatting, no disk I/O — schema.py's "no disk" rule is
    about deciding policy from disk state, not formatting a filename.
    """
    return (viva_dir / f"review-input-r{n}.json", viva_dir / f"review-r{n}.json")


def round_input_glob() -> str:
    """The glob pattern every `review-input-r{N}.json` scan uses (`loop.py`,
    `docket.py`), so a caller reads it from one place."""
    return "review-input-r*.json"


def round_output_glob() -> str:
    """The `review-r{N}.json` half of the same convention."""
    return "review-r*.json"


def parse_round_input_stem(stem: str) -> Optional[int]:
    """`"review-input-r7"` -> `7`; anything else -> `None`. The inverse of
    `round_file_paths`' input half, for scanning `viva_dir.glob(...)` results
    back into round numbers."""
    prefix = "review-input-r"
    if not stem.startswith(prefix):
        return None
    tail = stem[len(prefix):]
    return int(tail) if tail.isdigit() else None


def round_is_complete(input_data: dict, verdicts: dict) -> bool:
    """Is this round finished — i.e. may the session sign off?

    The single rule both `loop.py finish` and the server's `/complete`
    handler ask. Pure: dicts in, bool out, no disk.

    The base: every section in the round's input carries an `approved`
    verdict (checked against the input, not just `verdicts`, since a
    section with no verdict row at all is incomplete).

    **The conjunct-only invariant.** A round's optional `pass` may only ADD
    a condition to that base, never relax it — every branch runs the base
    first and returns False the moment it fails, so no pass can return True
    where a passless round returns False (reopening the hole #102 closed).

      | pass                       | complete when                              |
      | absent, architecture, line | every section approved                     |
      | checks                     | …and every check flag carries a result     |
      | final                      | …and no suggested edit is unresolved       |

    `tests/test_schema.py`'s `test_no_pass_relaxes_the_all_approved_base` walks
    `PASS_KINDS` to enforce this.

    The `checks` conjunct reads flags off the round input, where
    `annotate.py` merged them — a flag answered in round N rides into N+1
    with its answer (`parse_sections._carry_annotations`), which is also how
    a stale flag survives its own fix (`headings_present.py`).

    Callers gate on shape: a Q&A round carries `questions`, not `sections`,
    and never reaches this function. A diff round does. The one diff finish
    that skips it is the empty re-capture (nothing left to approve), which
    `loop.py finish` asserts as `resolved: "empty"` — honored only on a
    `--mode diff` launch (#177).
    """
    section_ids = [s.get("id") for s in input_data.get("sections", [])]
    if not section_ids:
        return False
    by_id = {s.get("id"): s for s in verdicts.get("sections", [])}
    if not all(
        (by_id.get(sid) or {}).get("verdict") == "approved" for sid in section_ids
    ):
        return False  # the base — no pass relaxes it

    spec = input_data.get("pass")
    kind = spec.get("kind") if isinstance(spec, dict) else None
    if kind == "checks":
        return all(_flag_is_answered(f) for f in _check_flags(input_data))
    if kind == "final":
        return not _has_unresolved_suggestion(input_data, verdicts)
    return True


# ── Q&A round shapes ──────────────────────────────────────────────────────────
class QAQuestion(TypedDict, total=False):
    id: str           # required
    text: str         # required
    hint: str         # optional — shown below the question text
    choices: List[str]  # optional — rendered as chip buttons
    # optional — must exactly match an entry in `choices` (value, not index;
    # validate_qa_input). Renders as a badge on that chip, advisory only
    # (PRODUCT.md's "advisory, never gating") — never pre-selected or required.
    recommended_choice: str
    # optional — one of QA_GROUNDS, classifying HOW `recommended_choice` was
    # arrived at (#175). `taste` may not share a question with a
    # `recommended_choice` (validate_qa_input rejects the combination).
    grounds: str


class QAInput(TypedDict, total=False):
    mode: str                   # "qa"
    context: str                # one-liner shown in the title block
    questions: List[QAQuestion]


class QAAnswer(TypedDict, total=False):
    id: str               # question id
    choice: str           # selected chip value
    note: str             # free-text field value
    attachments: List[str]  # server-written image paths
    # optional — present only when that question had a `recommended_choice`;
    # True iff the chosen `choice` matches it. Written server-side at
    # `POST /submit` (#175's accept-rate instrumentation); unvalidated here.
    accepted_recommendation: bool


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

    Enforces `id`/`text` on every question, and when present, that
    `recommended_choice` exactly matches an entry in that question's own
    `choices`, and `grounds` is one of `QA_GROUNDS` (never `"taste"` paired
    with a `recommended_choice`). Call at startup when `--mode qa`.
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
        if not ID_RE.match(q["id"]):
            raise ValueError(
                f"qa-input.questions[{i}].id {q['id']!r} must match {ID_RE.pattern!r} "
                f"— it reaches an HTML attribute context unescaped (buildQACard)"
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
        if "grounds" in q:
            grounds = q.get("grounds")
            if grounds not in QA_GROUNDS:
                raise ValueError(
                    f"qa-input.questions[{i}].grounds {grounds!r} must be one "
                    f"of {QA_GROUNDS!r}"
                )
            # Contradictory data, not a style choice: taste means no
            # recommendation is offered at all.
            if grounds == "taste" and "recommended_choice" in q:
                raise ValueError(
                    f"qa-input.questions[{i}] has grounds 'taste' and a "
                    "recommended_choice — taste means no recommendation is "
                    "offered"
                )
