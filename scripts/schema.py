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
- **The completion rule** — `round_is_complete()`, the one predicate
  `loop.py finish` and the server's `/complete` both ask, plus the conjunct-only
  invariant a round's optional `pass` obeys: a pass may only add a condition to
  the all-approved base, never relax it.
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

import re
from typing import List, Optional, TypedDict

# Verdicts that earn a Revision-History ledger row. `approved`/`pending` do not.
LEDGER_VERDICTS = ("changes", "info")
# Every verdict a review output section may carry.
VERDICTS = ("approved", "changes", "info", "pending")

# ── Passes — depth and posture as round parameters ────────────────────────────
# The four depths a round can run at. A pass is OPTIONAL on `ReviewInput`, and
# absent means today's behavior exactly — never defaulted, never written as
# `null` (PRODUCT.md principle 4). `doc_types.py` reads these too: a bundle's
# `default_pass` must name a kind a round can actually run.
PASS_KINDS = ("architecture", "line", "checks", "final")
# Posture is a setting ON the pass, not a second round field — `hard` licenses
# the author to argue rather than concede. Absent reads as `normal`.
PASS_POSTURES = ("normal", "hard")

# The annotation `kind`s a check producer emits — the handle `round_is_complete`
# reads to find a `checks` round's flags (`headings_present.py`'s `KIND` is
# the first, and its docstring names this key as that handle). A new check
# producer ADDS ITS KIND HERE, or a `checks` pass never sees its flags and
# closes a round it should have held. Deliberately not "every warn/error
# annotation": drift, checklist, contradiction, confidence, and preference flags
# are advisory producers with nothing to do with checking claims.
CHECK_KINDS = ("headings-present",)

# The scope a producer's flag is ABOUT. `headings_present.py` and `checklist.py`
# both report a fact about the WHOLE DOCUMENT and both anchor it to
# `sections[0]["id"]` — not because it belongs there, but because
# `parse_sections.py`'s integrity check makes a card for a section the document
# does not have impossible, so the first card is the only document-level handle
# either one has (both docstrings say so). A reader that takes the anchor
# literally prints five document facts in the margin of section 1, which is the
# first thing a round-1 review paints. Registered here, never tested at a call
# site. Fails open exactly as CHECK_KINDS does: an unregistered kind is simply
# treated as section-scope and lands wherever its producer anchored it.
#
# A DIFFERENT AXIS from CHECK_KINDS, which asks "does this gate a `checks`
# round". `headings-present` is deliberately in both.
DOC_SCOPE_KINDS = ("headings-present", "checklist")

# The comment type a reviewer's suggested edit carries, beside today's `changes`
# and `info`: a directive with the wording attached. The reviewer supplies the
# exact `replacement` for the span their `anchor` names instead of describing
# the change, and the author applies it VERBATIM — no rewrite pass.
SUGGESTION = "suggestion"
# Every type a reviewer's comment may carry. A DIFFERENT AXIS from `VERDICTS`:
# a type is per-comment and reviewer-chosen, a verdict is the section's derived
# state. `suggestion` derives to the section verdict `changes` (it is a
# directive) and never becomes a verdict of its own — see `round_is_complete`'s
# callers and DESIGN.md's derivation rule. `open_notes.py` threads on this
# tuple, so a fourth type carries across rounds the day it is added here.
COMMENT_TYPES = ("changes", "info", SUGGESTION)

# ── Open-note thread statuses ─────────────────────────────────────────────────
# One thread per comment `cid` in `.viva/open-notes.json`, whose single writer is
# `open_notes.py`. `declined` is the AUTHOR's turn — they did not comply and
# recorded `grounds` on that exchange. It is a THREAD status and NOT a verdict:
# `VERDICTS` is the section's state, and a section with two comments, one applied
# and one declined, has no coherent section-level verdict (#167). Only the
# reviewer settles, so a decline never closes anything; it is an answer the
# reviewer accepts (settle) or overrides (reply).
THREAD_OPEN = "open"
THREAD_SETTLED = "settled"
THREAD_DECLINED = "declined"
THREAD_STATUSES = (THREAD_OPEN, THREAD_SETTLED, THREAD_DECLINED)

# ── Q&A recommendation grounds (issue #175) ───────────────────────────────────
# How a QAQuestion's `recommended_choice` was arrived at, so the interview can
# tell "cites where this comes from" apart from "just an opinion" apart from
# "no opinion at all". Named independently of `open_notes.py`'s decline
# `grounds` (the author's reason for not complying with a turn) — same English
# word, unrelated shape, unrelated file. Also a DIFFERENT AXIS from the
# confidence `basis` tuple below (`sourced`/`inferred`): that one is a
# REVIEWER'S self-report on a document SECTION during review; this one
# classifies a recommendation the agent itself offers on a Q&A QUESTION during
# the interview, and adds a third value that basis has no room for.
#   sourced  — the chip names its provenance: the ticket, a codebase standard,
#              a measurement, a prior ledger ruling.
#   inferred — best practice with no local provenance; an opinion, not a fact.
#   taste    — labeled "this one is yours": no recommendation is offered at
#              all, so `recommended_choice` must be absent (validate_qa_input
#              rejects the two together).
QA_GROUNDS = ("sourced", "inferred", "taste")


def thread_is_unresolved(status: object) -> bool:
    """Is this thread still live — does it carry into the next round?

    `open` and `declined` both are: an unresolved decline holds its section
    exactly as an open thread does, because the reviewer has yet to accept it or
    insist. `settled` is the one closed status.

    Membership, not `!= settled`, so an unknown status is never silently treated
    as live. The two readers ask the same question from opposite ends:
    `parse_sections._attach_open_notes` re-presents what is unresolved, and
    `open_notes.update` settles what is unresolved when its section is approved.
    """
    return status in (THREAD_OPEN, THREAD_DECLINED)


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


def _comment_fragment(comment: dict) -> str:
    """One comment's contribution to a ledger note.

    A `changes`/`info` comment contributes its note. A `suggestion` also
    contributes the reviewer's replacement wording VERBATIM, tagged
    `suggested:` — the row's `verdict` column carries the *section* verdict
    (`changes`, since a suggestion is a directive), so the fragment is the only
    place a ledger reader learns wording was supplied rather than described. A
    suggestion's note is optional rationale; the wording alone is a full row.
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

    Multi-comment sections (issue #68) carry their notes in `comments[]`; their
    fragments (`_comment_fragment`) are joined with ` · `. Older single-note
    sections fall back to the section's own `note`. An empty result is normal
    (a `changes` with no text).
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
    # optional — the check's finding for this flag, written by the producer that
    # raised it or merged onto it later (`annotate.py` answers a flag in place).
    # Only load-bearing for a `checks` pass: `round_is_complete()` holds such
    # a round until every `CHECK_KINDS` flag carries one. Advisory everywhere
    # else, like the rest of this shape.
    result: str


class ReviewSection(TypedDict, total=False):
    id: str                       # required — stable id (s1, s2, …)
    title: str                    # required — heading text
    content: str                  # required — verbatim markdown
    # optional — one line describing what this section IS, written by the agent
    # and rendered under the card title so a collapsed list reads as a table of
    # contents. Never derived from `title`, and never folded INTO it: branch B
    # parses `title` as `{filepath} hunk N` to find the file to edit (#188).
    # No producer writes it — a producer's flag is a note ABOUT a section, this
    # is a description OF one. Carried round to round only onto a section whose
    # content is byte-identical; a rewritten section's summary is stale and
    # drops, exactly as its annotations do.
    summary: str
    annotations: List[Annotation]  # optional — advisory badges
    diff: dict                    # optional — round-to-round change
    # optional — carried-forward threads, `parse_sections._attach_open_notes`'s
    # projection of the `.viva/open-notes.json` store: each is
    # `{cid, quote, status, exchanges}`, `status` one of `THREAD_STATUSES` (only
    # the unresolved ones attach — a settled thread drops), and each exchange is
    # `{round, verdict, note, response}` plus, presence-gated, `replacement`
    # (the reviewer's suggested wording) and `grounds` (the author's reason for
    # declining that turn).
    open_notes: list


class ReviewPass(TypedDict, total=False):
    kind: str      # required when a pass is present — one of PASS_KINDS
    posture: str   # optional — one of PASS_POSTURES; absent reads as `normal`


# `ReviewInput.pass` — optional; the depth and posture this round runs at.
# ABSENT for a round that runs no pass, and absent is today's behavior exactly:
# the round parses, arms, waits, and completes as it did before this field
# existed (PRODUCT.md principle 4). Present, it can only make
# `round_is_complete()` stricter — never looser. Recorded by
# `parse_sections.py`; carried within a session by `loop.py rearm`, deliberately
# NOT across a resume (depth is a per-round decision, unlike the session
# identity `split_on`/`doc_type` carry). `server.py` renders nothing from it —
# only `/complete`'s finish guard reads it, through `round_is_complete()`.
#
# Declared with the functional form because `pass` is a Python keyword and
# cannot be a class-body annotation; mixed into `ReviewInput` below.
_ReviewInputPass = TypedDict("_ReviewInputPass", {"pass": ReviewPass}, total=False)


class ReviewInput(_ReviewInputPass, total=False):
    mode: str                       # "review"
    doc_file: str                   # relative path for the UI
    round: int                      # round number
    approved_ids: List[str]         # ids approved in prior rounds
    # optional — the `--split-on` regex this round was parsed with, recorded by
    # `parse_sections.py` so `loop.py rearm` re-splits round N+1 identically.
    # Absent when the round used the auto-detected split level.
    split_on: str
    # optional — the resolved doc-type name (`scripts/doc_types.py`), recorded
    # by `parse_sections.py` and carried round to round the way `split_on` is.
    # Passthrough: nothing in `server.py` reads or renders it. Absent when the
    # session was started without `--type`.
    doc_type: str
    # `pass` — see `_ReviewInputPass` above; the key cannot be spelled here.
    sections: List[ReviewSection]


class SectionVerdict(TypedDict, total=False):
    id: str        # required — section id
    verdict: str   # required — one of VERDICTS
    # optional — typed comment threads (issue #68). Each comment carries a
    # `type` (one of COMMENT_TYPES), an optional `note`, and may carry an
    # `anchor` object {text, offset, occurrence?}: the reviewer's exact
    # selection, used to scope the rewrite. `occurrence` is the 0-based index of
    # that selection among the identical matches in the RENDERED section
    # content, where the selection was made; `offset` is that same ordinal
    # resolved against the markdown source, or -1 when it does not resolve there
    # (#95). Distinct from Annotation.anchor (a string) above.
    #
    # A `suggestion` comment also carries `replacement`: the reviewer's exact
    # wording for the anchored span, applied verbatim. It is the payload that
    # makes the comment appliable, so `validate_verdicts` requires a non-empty
    # one — reviewer-authored and BINDING, unlike an `Annotation`, which is
    # producer-authored and advisory (#166).
    comments: list


class ReviewOutput(TypedDict, total=False):
    round: int
    submitted_early: bool
    sections: List[SectionVerdict]


# ── Boundary validation ───────────────────────────────────────────────────────
def default_round(data: dict) -> dict:
    """Give a review-input an explicit `round`, in place, and return it.

    `round` is optional on the wire and both in-repo producers always write it,
    so this fires only for a hand-built payload — but when it does, absence is
    not harmless downstream. The browser prints the value straight into the tab
    title and the bar (`String(undefined)` → the literal "REV undefined") and
    does round arithmetic with it (`Number(last.round) !== round - 1`, which
    against `undefined` is a NaN comparison that is never equal, so the round-2
    landing and the transmittal's `answered` bucket go dead with no error).

    NORMALIZE rather than reject, per the house rule: fix data at the boundary,
    not at the point of use. One is the only defensible default — an unnumbered
    round is a first round, and every feature that keys on `round > 1` then
    behaves correctly instead of accidentally. Rejecting instead would be a wire
    break for a field the contract has always documented as optional.

    Called at the server's two read boundaries, which are the only places
    `_input_data` is assigned. `validate_review_input` stays pure and does the
    other half: a present-but-malformed `round` is a hard failure there.
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
    # Presence-gated: the key is optional, but a present non-string is a hard
    # failure — `loop.py rearm` hands this value straight back to
    # `parse_sections.py --split-on`, and a `null` would silently re-split the
    # next round by auto-detection instead of the pattern the session started
    # with. Loud here beats a mid-session split change nobody asked for.
    if "split_on" in data and not isinstance(data["split_on"], str):
        raise ValueError("review-input.split_on must be a string")
    # Presence-gated for the same reason: `loop.py`'s resume and `rearm` hand
    # this value straight back to `parse_sections.py --doc-type`, and a `null`
    # would silently drop the type — and with it the round's check set — rather
    # than failing where the bad value was written.
    if "doc_type" in data and not isinstance(data["doc_type"], str):
        raise ValueError("review-input.doc_type must be a string")
    # Presence-gated, NOT required — the wire contract has always documented
    # `round` as optional and ~85 fixtures across the suite omit it. What a
    # present-but-malformed value costs is the reason for the gate: the browser
    # prints `String(round).padStart(2,'0')` into the tab title and compares
    # `Number(last.round) !== round - 1` to decide whether an author's answer is
    # fresh. A string or a null yields "REV undefined" and a NaN comparison that
    # is never equal, which silently disables the round-2 landing and the
    # transmittal's `answered` bucket rather than failing anywhere. Absence is
    # handled by NORMALIZING at the server's read boundary (see
    # `default_round`), never by rejecting a round a caller is entitled to send.
    # `bool` is excluded explicitly: it is an `int` subclass, and `True` would
    # otherwise pass and render as round 01.
    if "round" in data and (
        isinstance(data["round"], bool)
        or not isinstance(data["round"], int)
        or data["round"] < 1
    ):
        raise ValueError(
            "review-input.round must be an integer >= 1 — omit the key entirely "
            "for a round that does not number itself, never null"
        )
    # Presence-gated like the two above, for a sharper reason: the pass is the
    # only round field that changes when `POST /complete` may succeed. A
    # malformed one must fail where it was written — a `null` or a typo'd kind
    # would otherwise revert the round to the base rule silently, dropping a
    # conjunct the reviewer asked for. Absent stays absent; nothing defaults it.
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
        # Presence-gated like the round-level three above: the key is optional,
        # but a present non-string is a hard failure. It reaches a render site
        # rather than a flag, so a `null` or a stray object would print as
        # "null"/"[object Object]" under the card title — a silent wrong answer
        # in the one place the reviewer looks to navigate 52 hunks.
        if "summary" in s and not isinstance(s["summary"], str):
            raise ValueError(
                f"review-input.sections[{i}].summary must be a string"
            )


def validate_verdicts(data: dict) -> None:
    """Raise `ValueError` if `data` is not a structurally valid review output
    (`review-r{N}.json`).

    Enforces that every section carries an `id` and a known `verdict`, plus one
    presence-gated comment rule: a `suggestion` must carry replacement wording.
    Permissive about everything else on a comment, and about attachments. Only
    meaningful for review-mode output (sections); callers gate on
    `"sections" in data` so Q&A `answers` payloads are skipped.
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
        # Gated on the comment TYPE, not on presence of a field: only a
        # `suggestion` is checked, so no payload written before this type
        # existed can trip it. The wording IS the comment — an empty one is
        # unappliable, and the author would be left inventing the edit the
        # reviewer meant to hand over. Loud here, at the server's read of the
        # submit, rather than silently at apply time.
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

    Anchored, never a substring test: `"## Revision History" in text` also
    matches the phrase inside backticks, a fenced block, or ordinary prose, and
    viva's own SKILL.md and DESIGN.md both discuss the ledger by name. A false
    positive there takes the resume branch and can pre-approve a section the
    human never saw, against PRODUCT.md's "nothing is auto-accepted".

    `loop.py`'s resume detection and `revision_history.py`'s append-vs-create
    branch ask the same question, so they ask it here — the same rule
    `section_key()` follows.

    Known residue: a fenced code block whose content begins the line still
    matches. Every mention in this repo is inline-backticked mid-line, which the
    anchor rejects; fence-awareness would need a block parser and would change
    `revision_history.py`'s append branch too.
    """
    return REVISION_HISTORY_RE.search(doc_text) is not None


def _check_flags(input_data: dict) -> list:
    """Every check-producer flag on this round — the annotations whose `kind` is
    in `CHECK_KINDS`. Advisory producers' flags are not check flags."""
    return [a
            for s in input_data.get("sections", []) or []
            for a in (s.get("annotations") or [])
            if isinstance(a, dict) and a.get("kind") in CHECK_KINDS]


def _flag_is_answered(flag: dict) -> bool:
    """Does this check flag carry a result — i.e. did the check come back?

    A non-empty string `result`. Emptiness is the whole point: the producer
    raises the flag, the result is what answers it, and a blank one answers
    nothing.
    """
    result = flag.get("result")
    return isinstance(result, str) and result.strip() != ""


def _has_unresolved_suggestion(input_data: dict, verdicts: dict) -> bool:
    """Does this round carry a suggested edit nobody has settled?

    A suggested edit is a reviewer comment typed `SUGGESTION`, written by the
    server's comment popover and carrying the wording in `replacement`.

    Two places one can be outstanding, both existing shapes:
      * the verdicts just submitted — a `suggestion` comment not marked
        `settled`;
      * the round's carried open threads — `parse_sections.py` attaches only
        threads still `open`, and `open_notes.py` records each turn's comment
        type as that exchange's `verdict`, so a thread whose LATEST exchange is
        a suggestion is one the author has not answered yet.

    An exchange's `verdict` is the REVIEWER's comment type, never the author's
    answer — declining a suggestion adds `grounds` and moves the thread to
    `THREAD_DECLINED`, leaving that exchange's verdict `suggestion`. That is
    what keeps a declined suggestion holding a `final` round: the author's
    refusal is not a resolution; only the reviewer's settle is.
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


def round_is_complete(input_data: dict, verdicts: dict) -> bool:
    """Is this round finished — i.e. may the session sign off?

    The single rule both `loop.py finish` and the server's `/complete` handler
    ask, so the invariant lives in one place rather than being re-derived at two
    call sites in two processes. Pure: dicts in, bool out, no disk.

    The base: every section in the round's input carries an `approved` verdict.
    The input side matters — a section present in the input with no verdict row
    at all is incomplete, which a scan of `verdicts` alone cannot see.

    **The conjunct-only invariant.** A round's optional `pass` may only ADD a
    condition to that base; it may never relax it. Every branch below runs the
    base first and returns False the moment it fails, so no pass — now or later
    — can return True where a passless round returns False. One that could would
    reopen the hole #102 closed: `POST /complete` accepting a round the human
    never approved.

      | pass                       | complete when                              |
      | absent, architecture, line | every section approved                     |
      | checks                     | …and every check flag carries a result     |
      | final                      | …and no suggested edit is unresolved       |

    `tests/test_schema.py`'s `test_no_pass_relaxes_the_all_approved_base` walks
    `PASS_KINDS` and enforces this, so a fifth kind is covered the day it lands.

    The `checks` conjunct reads flags off the round input, where
    `annotate.py` merged them, so a flag answered in round N rides into N+1 with
    its answer (`parse_sections._carry_annotations` copies whole annotations onto
    byte-identical sections). That carry is also how a stale flag survives its
    own fix — a pre-existing limit `headings_present.py` documents, and the
    reason answering a flag in place, rather than waiting for it to disappear,
    is the satisfying move.

    Callers gate on shape: a Q&A round carries `questions` rather than
    `sections` and never reaches this function. A diff round does — every hunk
    approved is its base too. The one diff finish that skips it is the
    empty re-capture (every hunk applied or reverted at the reviewer's request,
    nothing left to approve), which `loop.py finish` asserts to `/complete` as
    `resolved: "empty"` from a fresh capture; the server honors that signal on a
    `--mode diff` launch only (#177).
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
    # optional — must exactly match one entry in `choices` (value, not index;
    # see validate_qa_input). Renders as a small badge on that chip. Advisory
    # only, per PRODUCT.md's "advisory, never gating" principle: never
    # pre-selected, defaulted, or required — the human picks whichever chip
    # they want, including a different one. Ignored/absent on every question
    # authored before this field existed — no render change without it.
    recommended_choice: str
    # optional — one of QA_GROUNDS, classifying HOW `recommended_choice` was
    # arrived at (issue #175). Absent renders exactly as before `grounds`
    # existed — see `recommended_choice`'s own no-render-change guarantee
    # above. `taste` never appears alongside a `recommended_choice` on the
    # same question (validate_qa_input rejects that combination); provenance
    # text for `sourced` rides in the question's own `text`/`hint` this
    # iteration — no separate citation field was added.
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
    # optional — present only when that question's `recommended_choice` was
    # set at all (any grounds); True iff the chosen `choice` matches it.
    # Written server-side at `POST /submit` (`server.annotate_qa_acceptance`,
    # issue #175's accept-rate instrumentation) against the round's own
    # recorded questions, never against anything the client posted, and never
    # read back by this module — documentation only, same as every other
    # QAAnswer field; unvalidated, like the rest of QAOutput.
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

    Enforces that every question has `id` and `text`, and — when present —
    that `recommended_choice` is a string that exactly matches an entry in
    that same question's own `choices`. A dangling or typo'd recommendation
    (no `choices`, or a value not in it) is a loud startup failure here,
    never a silent no-badge misfire at render time. `grounds`, when present,
    must be one of `QA_GROUNDS`, and `grounds: "taste"` may not share a
    question with a `recommended_choice` — taste means no recommendation is
    offered at all, so the two together are contradictory data, not a
    rendering preference. Permissive about other optional fields (`hint`,
    `choices`, `context`). Call at startup when `--mode qa`.
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
