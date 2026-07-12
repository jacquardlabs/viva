# Design: Recommended-choice flag for QA schema (#114)

**Date:** 2026-07-11
**Issue:** [#114](https://github.com/jacquardlabs/viva/issues/114) — "qa-mode
choices schema has no way to mark a recommended option"
**Epic:** jig-integration — stabilize viva's headless protocol and extend
review surfaces for jig integration
**Status:** Draft — pending `/gate-design-review`

---

## Problem & persona

PRODUCT.md names the agent author as viva's first persona:

> **The agent author (primary, non-human).** Claude Code, having written a
> spec, ADR, runbook, or design doc. It needs to hand the doc to a human for
> sign-off without burning context...

jig's `/design` skill is this persona using viva's `qa` mode for its
step-3 "present forks" moment: a batch of interview questions asked before a
document exists (PRODUCT.md's feature map: "Brainstorming Q&A — batch design
questions before the spec is written"). Some of those questions are
architectural forks where jig, as the calling agent, has a genuine
recommendation and a reason for it — issue #114 quotes the intended framing
directly: *"Recommend — the human decides."*

Today's `QAQuestion` shape (`scripts/schema.py`) has no field for this:

```python
class QAQuestion(TypedDict, total=False):
    id: str
    text: str
    hint: str
    choices: List[str]  # optional — rendered as chip buttons
```

`choices` is a flat list of strings; every chip `server.py` renders from it
(`buildQACard`, `server.py` ~line 2369) is visually identical. The only way
today for jig to surface a recommendation is an unenforced convention —
prefixing `"(recommended)"` into a choice string or the `hint` — which issue
#114 found breaking down during jig's own dogfood interview (#109): several
fork questions had a real recommendation behind them that nothing in the
rendered card distinguished from the alternatives. A convention nobody is
forced to follow gets forgotten; a schema field renders every time.

## Proposed design

`QAQuestion` gains one new optional field:

```python
class QAQuestion(TypedDict, total=False):
    id: str
    text: str
    hint: str
    choices: List[str]
    recommended_choice: str  # optional — must exactly match one entry in `choices`
```

`recommended_choice` names the recommended choice **by value**, not index —
consistent with `QAAnswer.choice`, which already records the human's answer
by the chip's string value rather than its position. A caller authoring a
question by hand references a choice the same way at write time
(`recommended_choice`) and read time (`answers.json`'s `choice`), and the
field is immune to the choice list being reordered or edited without the
recommendation being updated to match.

When `recommended_choice` is present, the chip whose value matches it renders
with a distinct badge — visually the same idea the feature map already
ships twice: "Per-section annotations rendered as card badges (advisory)"
and "Confidence triage — sourced/inferred · level." This story applies that
established, already-reviewed pattern (a small badge decorating a card
element) to one more surface — a Q&A choice chip — rather than inventing a
new visual language for it. Reusing viva's existing badge/annotation color
tokens (already defined in `server.py`'s CSS, e.g. the teal accent
`.vbadge-approved`/annotation strip use) over a bespoke color keeps this
consistent with "prefer reuse over creation."

Critically, the badge is **advisory, never gating** — PRODUCT.md's third
principle, already the operating rule for annotations, confidence, and
learned preferences:

> **Advisory, never gating.** Annotations, producers, confidence, learned
> preferences, and open notes all decorate or inform — the human alone
> decides a verdict. Nothing is auto-accepted.

The recommended chip is not pre-selected, not defaulted, and does not disable
the others. The human clicks whichever chip they want, including overriding
the recommendation entirely; `QAAnswer.choice` records whatever they actually
picked, exactly as today. This story only widens what the card *shows*, not
what the human can *do*.

**Backward compatibility is structural, not just behavioral.** `choices`
itself does not change shape — it stays `List[str]`, byte-identical to
today. A question that omits `recommended_choice` is indistinguishable, at
the type and the render level, from a question authored before this field
existed. No existing `qa-input.json`, no existing test fixture, and no other
`scripts/*.py` consumer of `QAQuestion` needs to change.

## User journey

1. jig's `/design` skill reaches an architectural fork while interviewing
   the human before drafting a doc — e.g. "should the retry use exponential
   backoff or a fixed interval?" — and has a recommendation with a reason.
2. jig writes `.viva/qa-input.json` as it does today, with one addition: the
   fork question's `choices` list plus `recommended_choice: "Exponential
   backoff"` naming which entry is the recommendation. The reason itself
   continues to live in the question's existing `hint` field or the choice
   text — this story adds the flag, not a new reason field.
3. jig launches `/viva-qa` exactly as today — same launch, same wait, same
   `.viva/answers.json` contract.
4. In the browser, the human sees the fork question's choice chips. The chip
   matching `recommended_choice` carries a small badge distinguishing it from
   the other choices at a glance — no scanning choice text or hint prose to
   find where the agent's opinion was buried.
5. The human weighs the recommendation like any other input and picks a
   chip — the recommended one, a different one, or skips the question
   entirely. Nothing about confirm/skip, note-taking, or image attachment
   changes.
6. The human submits. `.viva/answers.json` is written in the unchanged
   `QAOutput` shape — `choice` holds whatever the human picked, with no
   record of whether it matched the recommendation. jig reads the answer
   exactly as it does today.
7. A question with no `recommended_choice` set (every question written
   before this story, and most questions after it — most forks don't carry
   a strong recommendation) renders exactly as it does today: same chips, no
   badge, same journey start to finish.

This composes into the existing Brainstorming Q&A capability the feature map
already names — it does not introduce a new mode, a new file, or a new step
in the loop.

## Out of scope

- **A reason/justification field.** The recommendation's "why" continues to
  live in the existing `hint` (question-level) or in the choice text itself.
  Issue #114's request is scoped to the flag; a structured reason field is a
  separate ask if it turns out to be needed.
- **Any change to `QAAnswer` / `answers.json`.** Whether the human followed
  or overrode the recommendation is not recorded. If a future consumer needs
  that signal, it is derivable by the caller (compare `answer.choice` to the
  `recommended_choice` it already sent) without a schema change here.
- **Enforcement of any kind.** The recommended chip is never pre-selected,
  defaulted, or required — Principle 3 applies without exception.
- **Multiple or ranked recommendations.** At most one `recommended_choice`
  per question. Issue #114 asks for "one recommendation," not a ranking.
- **Free-text-only questions.** A question with no `choices` array has
  nothing for `recommended_choice` to name; see validation below.
- **`ReviewInput` / `SectionVerdict` / any review-mode schema.** This story
  touches `QAQuestion` only. Review-mode section cards, annotations, and
  confidence badges are unrelated code paths and are not touched.
- **Retiring or rewriting existing convention-based recommendations already
  embedded in choice/hint text** in any in-flight `qa-input.json` a caller
  may have authored. This is a purely additive schema change; no migration
  of existing data is needed or performed.

## Alternatives considered

1. **Per-choice object shape** — `choices: List[str | {"text": str,
   "recommended": bool}]`, the other option issue #114 names. Rejected: this
   widens `choices` from a flat `List[str]` to a mixed/union element type,
   which every consumer has to branch on — `server.py`'s
   `q.choices.map(c => ...)` render loop, `validate_qa_input`'s choice
   handling, and any future tooling that reads `qa-input.json` off disk. A
   single additive field on `QAQuestion` gets the same observable result
   (one chip marked distinctly) without widening a type every existing
   consumer already assumes is a flat string list — narrower change, same
   user-facing outcome, per CLAUDE.md's "prefer narrowing types over
   widening."
2. **Status quo: convention-only recommendation** (prefix `"(recommended)"`
   into choice text or `hint`). Rejected: this is the problem issue #114
   documents, not a fix for it — invisible unless the calling agent
   remembers to do it, and renders with no visual distinction from any other
   choice once it does.
3. **Index-based flag** (`recommended_index: int` into `choices`). Rejected:
   fragile against hand-edited question JSON — reordering or inserting a
   choice silently shifts which chip the badge lands on with no validation
   error, whereas a value-based `recommended_choice: str` that no longer
   matches any `choices` entry is a detectable, loud validation failure (see
   Operational readiness) rather than a silent misfire.

## Operational readiness

- **Migration:** none. `QAQuestion.recommended_choice` is a new optional
  key; no on-disk file needs rewriting, and `.viva/*` round files are
  already disposable, session-scoped state per CLAUDE.md's "Extension
  seams" — nothing here needs to survive past the session that wrote it.
- **Rollback:** the field is caller-opt-in. A caller that never sets
  `recommended_choice` is unaffected by this change existing at all,
  including if it ships with a rendering bug — reverting `server.py`'s badge
  rendering leaves `recommended_choice` a harmless, ignored key in the JSON,
  not a breaking one.
- **Rollout:** no flag. Ships as an additive `TypedDict(total=False)` field,
  safe by construction — every existing caller and every existing test
  fixture omits it and is unaffected.
- **Validation (boundary, per CLAUDE.md "fix data at the boundary"):**
  `validate_qa_input` (`scripts/schema.py`) is the boundary this field
  enters at, alongside the existing `id`/`text` checks. When
  `recommended_choice` is present, validation should reject (loud
  `ValueError` at server startup, matching every other `validate_qa_input`
  failure mode today) a question where it does not exactly match an entry
  in that question's own `choices`, and reject it outright if `choices` is
  absent entirely — a silently-ignored typo'd or dangling recommendation is
  exactly the kind of point-of-use surprise the boundary-validation
  convention exists to prevent.
- **Docs:** `docs/headless-contract.md` §3 already documents `QAQuestion` in
  a field table (`id`, `text`, `hint`, `choices`). This story adds one row
  for `recommended_choice` — additive only, per the epic pre-mortem's
  explicit warning that this doc is touched by multiple stories in this
  batch and must never be wholesale-rewritten by any one of them.
- **Observability:** none beyond what exists. PRODUCT.md is explicit that
  viva is "local and keyless... no hosted service" — there is no fleet to
  monitor. A malformed `recommended_choice` surfaces the same way every
  other `qa-input.json` validation failure does today: server refuses to
  start, error printed to stdout.

## Open questions

- **Exact badge treatment.** Whether the badge reuses `.vbadge`'s existing
  CSS class/markup directly or is a smaller purpose-built marker on the chip
  itself is an implementation choice, not a schema or UX-contract question —
  left to the build phase. Either satisfies "rendered distinctly... e.g. a
  badge."
- **Validation failure mode.** This design proposes `validate_qa_input`
  hard-fails (loud `ValueError`) on a `recommended_choice` that doesn't match
  any `choices` entry. Confirming that's the right severity (versus, say, a
  non-fatal warning) versus existing `validate_qa_input` precedent is left to
  the build phase to settle against the current failure modes in
  `scripts/schema.py`.
- **`docs/headless-contract.md` ownership for this batch.** The epic
  pre-mortem (risk #7) flags that doc as touched by multiple stories in this
  amendment. This design assumes this story adds its own additive row and
  leaves other stories' edits untouched; if the driver serializes contract
  doc edits differently, that's a scheduling detail, not a design change.
