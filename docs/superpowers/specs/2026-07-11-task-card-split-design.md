# Task-card review mode for plan documents

Source: issue [#110](https://github.com/jacquardlabs/viva/issues/110). Epic:
jig-integration — "Ship two review-surface capabilities jig needs (a unified
Q&A→review session, task-card splitting for plan documents) and a versioned,
documented headless contract that reflects the surface those capabilities
actually produce, so jig can build against viva without re-parsing internals."

## Problem & persona

PRODUCT.md names two primary personas. This story is about a case where the
same non-human persona shows up one layer removed:

> **The agent author (primary, non-human).** Claude Code, having written a
> spec, ADR, runbook, or design doc. It needs to hand the doc to a human for
> sign-off without burning context: parse without reading the doc, wait
> without polling cost, rewrite only flagged sections, and learn what this
> reviewer always wants.

jig's `/plan` skill *is* an agent author in this sense — it writes a
`PLAN.md` and needs to hand it to a human for sign-off exactly the way any
other Claude Code session hands off a spec today. The doc shape is just
different: a plan document, structured as `### Task 1`, `### Task 2`, …
blocks, not the prose-with-headings shape `parse_sections.py` was built
against.

That shape collides with how the reviewing human is served today:

> **The reviewing human (primary).** … Wants to see each section verbatim,
> leave one or more typed comments per section … The section is the unit of
> trust. … The document passes only when every section is approved.

`parse_sections.py`'s split level is auto-detected by `_find_split_level`,
which scans heading levels from coarsest (H1) to finest (H6) and returns the
*first* level that occurs more than once — subsection frequency is never
consulted, and a plan with two or more `### Task N` headings and no repeated
coarser heading already resolves correctly to the task level today (verified
against `_find_split_level`: for this doc's own example, H1/H2 don't repeat
and H3 (`Task 1`, `Task 2`) does, so auto-detection already picks split level
3 without `--split-on`). The failure that's actually reachable is the
opposite of "cards smaller than a task": because the scan stops at the first
repeated level and coarser levels are checked first, *any* repeated heading
above the task level — a `## Overview` or `## Risks` aside that recurs inside
every task, or a doc-level `## Phase N` grouping wrapping several tasks —
wins before task headings are ever considered, and every task gets swallowed
into whichever coarser section it falls under. Auto-detection has no way to
know "task" is the semantically right unit of trust versus whatever coarser
heading happens to repeat; it just picks the coarsest repeater. That's a
guarantee `--split-on` needs to give jig, not an accident of what heading
shapes a given plan happens to contain — the unit of trust Principle 1
promises has to line up with the unit jig's `/plan` skill and its human
reviewer actually think in: the task, regardless of what other headings the
plan nests around it.

Issue #110 is explicit that the fix belongs in viva, not in jig: "so
per-task approve/changes/info works without custom parsing in the consuming
plugin." Today jig would have to pre-split `PLAN.md` itself and either lose
viva's round-to-round carry-forward (approvals, annotations, diffs, open
notes all key off section identity) or reimplement it — the exact "second
identity rule" risk called out in this epic's pre-mortem (item 4).

## Proposed design

Add one new, optional CLI flag to `scripts/parse_sections.py`:

```
--split-on PATTERN
```

`PATTERN` is a Python regular expression. When present, it replaces the
entire auto-detection step (`_find_split_level`'s level-counting heuristic)
with a direct rule: **a heading is a split point iff its title text matches
`PATTERN`**, regardless of heading depth (`#` count). Everything downstream
of "here are the split headings, in document order" — preamble handling,
per-section content slicing up to the next boundary, `Revision History`
exclusion among the matched headings, section id numbering (`s1`, `s2`, …),
and the integrity check — runs unchanged, because that logic already only
consumes a list of `(title, line_idx)` split points and doesn't care how the
list was produced.

When `--split-on` is **not** given, the code path is exactly today's: the
new flag defaults to unset, and the branch that reads it is never taken.
This isn't just a testing goal, it's a structural guarantee — `_split_sections`
and `_find_split_level` are not modified, only called from an earlier
decision point that chooses between "auto-detect" (today, unconditional) and
"use the caller's pattern" (new, opt-in).

For jig's case, the pattern is a title match like `^Task \d+` against
headings such as:

```markdown
### Task 1: Add the CLI flag

Body text, acceptance criteria, whatever the plan needs.

#### Acceptance criteria
- ...

### Task 2: Write the fixture
...
```

Each matched `### Task N: …` heading becomes one section; its `#### Acceptance
criteria` sub-heading stays nested inside that section's content exactly as
non-split-level headings do today (same rule as an `## Overview` aside inside
a `## Background` section under default parsing) — no new nesting rule is
introduced, this falls out of the existing "one section = one split heading
through the next one" definition.

**Identity — no new rule.** The `title` field on each produced section is
whatever text the matched heading carries, fed into `_load_approved`,
`_carry_annotations`, `_compute_diffs`, and `_attach_open_notes` exactly as
today. All four already normalize through `schema.section_key()` before
comparing across rounds. `--split-on` changes *which* headings become
`title`s; it does not touch any of the four functions that key off
`title`. This is the direct implementation of the pre-mortem's item 4 concern
— the safest way to guarantee no second identity rule is to not write one.

**No `--split-level` companion flag.** The pattern alone decides split
points, independent of `#` depth. A depth-only flag (`--split-level 3`)
would split on *every* H3 in the doc, including unrelated asides at the same
depth — it doesn't satisfy "custom heading pattern" from the issue, and it
reintroduces the false-positive risk pattern matching exists to avoid.

**The 20-section coarsening heuristic does not apply.** `_find_split_level`
falls back one level coarser past 20 sections to protect the *auto-detection*
heuristic from over-splitting on an accidental level match. `--split-on` is
an explicit instruction, not a heuristic guess — a 30-task plan should
produce 30 cards, not get silently coarsened down a level. Bypassing that
fallback for the pattern path is deliberate, not an oversight.

**Documentation is part of "done" for this flag** (the issue's "flag or
convention" language, satisfied via a flag): the module docstring at the top
of `parse_sections.py`, and the "Parsing rules it implements (for reference)"
bullets in `SKILL.md`, both get a new bullet naming `--split-on`, its
matching rule, and the "no `--split-on` → unchanged" guarantee. README's
"Server CLI (advanced)" section gets the flag added to the round-1/round-2+
invocation examples it already documents.

**Zero matches is an error, not a silent fallback.** If `PATTERN` matches no
heading in the document, the parser exits non-zero with a message naming the
pattern and the doc — the same failure mode as today's "no reviewable
sections found," not a silent drop back to auto-detection. A caller passing
`--split-on` has made an explicit claim about the doc's shape; failing loud
when that claim doesn't hold is cheaper than a reviewer discovering it via a
one-section review of the whole plan.

## User journey

This extends viva's existing core loop (PRODUCT.md's Feature map: "parse →
review → rewrite → loop → sign off with ledger") to a new doc shape; it does
not add a new loop.

1. jig's `/plan` skill (the agent author) writes `PLAN.md` with `### Task N`
   blocks, exactly as it does today for its own planning output.
2. It parses with the new flag instead of the default invocation:
   ```bash
   python3 scripts/parse_sections.py PLAN.md \
     --output .viva/review-input-r1.json --round 1 \
     --split-on '^Task \d+'
   ```
3. It launches the server and waits, per the existing headless protocol —
   unchanged. jig does not need to know `--split-on` exists once round 1 is
   written; every later round file and every server endpoint looks exactly
   like a normal review.
4. The reviewing human opens the same viva UI they already know. Each card
   is one task (`Task 1`, `Task 2`, …), verbatim, per Principle 2. They
   approve, request changes, leave a typed comment, or ask a question — per
   task, per Principle 1 — exactly as they would on any other section.
5. jig rewrites the tasks that got `changes`/`info` verdicts and reparses
   with `--split-on` again for round 2. Carry-forward of approvals, round-to
   -round diffs on rewritten task cards, annotations, and open-note threads
   all work unmodified, because all four are keyed by task title through
   `section_key()` — no jig-side bookkeeping required, which is the entire
   point of issue #110 ("no custom parsing needed downstream").
6. Sign-off produces the same verbatim Revision-History ledger any other
   viva review produces, with one row per task that received a `changes` or
   `info` verdict.

## Out of scope

- **No UI change.** Task cards render exactly as any other section card —
  no new "task" card type, no new badge. The feature is entirely a parse
  -time heading-selection rule.
- **No new `.viva/*.json` field, no `schema.py` change.** Section objects
  keep the same `{id, title, content, …}` shape `validate_review_input`
  already enforces; `--split-on` is a `parse_sections.py` CLI-only concern
  that never reaches the JSON as a field.
- **No zero-config auto-detected convention.** A doc is never auto-split on
  a hardcoded "Task N"-shaped pattern without the caller passing
  `--split-on` explicitly. An implicit convention risks matching a heading
  in an unrelated doc (a spec with a "## Task 3 caveats" aside) and would
  make "default parsing is byte-for-byte unchanged" an accident of what
  today's fixtures happen to contain rather than a guarantee.
- **No change to any other script.** `checklist.py`, `drift.py`,
  `annotate.py`, `revision_history.py`, and `server.py` all consume the
  section objects `parse_sections.py` already produces (`id`/`title`
  /`content`) and are agnostic to how the split happened — confirmed by
  reading each for heading-shape assumptions; none exist outside
  `parse_sections.py` itself.
- **No multi-pattern support.** One `--split-on` pattern per invocation. A
  doc needing two different heading conventions split simultaneously is not
  a case jig's `/plan` skill has today; adding it later is additive, not a
  breaking change to this flag.
- **No fix to the pre-existing Revision-History/split-level mismatch.** See
  Open questions — deliberately not touched, to keep the default path
  provably byte-for-byte unchanged.

## Alternatives considered

1. **Zero-config convention** — always treat any heading matching a
   hardcoded `^Task \d+`-shaped pattern as a split point, no flag needed.
   Rejected: makes "default parsing unchanged" true by coincidence (no
   existing fixture happens to contain such a heading) rather than by
   construction, and risks changing behavior for an unrelated future doc
   that happens to contain a "Task N" heading as prose, not structure. An
   explicit, caller-supplied flag makes the opt-in auditable at the call
   site.
2. **`--split-level N`** — force the split level to a caller-given depth
   instead of matching heading text. Rejected: doesn't satisfy the issue's
   literal ask ("split on a custom heading pattern"); a plan doc with
   unrelated headings at the same depth as `### Task N` would over-split,
   handing the reviewer cards that aren't tasks.
3. **jig pre-splits `PLAN.md` into per-task files itself**, calling
   `parse_sections.py` once per task (whole file = one section). Rejected:
   this is precisely the "custom parsing downstream" issue #110 exists to
   eliminate, and it forces jig to reimplement its own section-identity
   bookkeeping for approval/annotation/diff/open-note carry-forward across
   rounds instead of reusing `schema.section_key()` — the second-identity
   -rule risk this epic's pre-mortem calls out by name (item 4).
4. **A parallel `parse_task_cards.py` script** dedicated to task-card plans.
   Rejected: doubles the surface every downstream reader (`server.py`,
   `annotate.py`, jig itself) has to know about for no behavioral gain over
   one flag on the existing parser, and doubles the chance of the two
   scripts' identity handling drifting apart over time.

## Operational readiness

N/A — no operational surface. This is a parse-time-only change to a
stdlib-only local CLI script; there is no deployed service, no metrics
pipeline, and no persisted state to migrate (`.viva/*.json` round files are
disposable per session per CLAUDE.md's state-lifecycle note, and this story
adds no new file and no new field to the existing ones). Rollback is a
straight `git revert` of the flag's commit — no data to reconcile, since a
document parsed with `--split-on` produces the same section-object shape any
other document does.

## Open questions

- **Revision History under a task-focused pattern.** `revision_history.py`
  always appends a hardcoded `## Revision History` heading. Today's default
  parser only recognizes a `Revision History` heading if it sits at the
  auto-detected split level — a doc whose split level isn't H2 already has
  this gap. `--split-on '^Task \d+'` never matches "Revision History" text,
  so on a second parse round of a doc that's already been signed off once
  (ledger appended), that block would be swallowed into the last task
  section's content rather than excluded and integrity-check-exempted, the
  same way it would for any existing doc whose auto-detected level happens
  not to be H2. This story doesn't fix that (see Out of scope, to keep the
  default path provably unchanged) — flagging it because task-card plans
  are more likely than average to hit it (H3 task headings, H2 ledger
  heading) than the docs viva reviews today. Worth a follow-up if jig
  round-trips a `PLAN.md` through sign-off more than once.
- **Pattern case sensitivity.** Proposed default is plain `re.search`
  (case-sensitive, no implicit flags) against the heading's title text — the
  same text that ends up in the JSON `title` field. A caller wanting
  case-insensitive matching adds `(?i)` inline. Confirming this is the
  behavior jig expects before implementation, rather than assuming, since a
  wrong default here would need a compatibility shim once jig starts calling
  it.
- **Where the exact task-heading regex lives.** This design has jig supply
  `--split-on` itself at the call site (no shared constant). If the "Task N"
  convention's exact text ever needs to change, it changes in one place
  (jig's own skill) without a viva release. Flagging in case the reviewer
  wants a documented default pattern shipped instead — not proposed here
  because it would be the first case of viva anticipating a specific
  consumer's heading vocabulary rather than staying doc-shape-agnostic.
