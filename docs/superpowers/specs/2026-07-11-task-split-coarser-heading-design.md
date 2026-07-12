# Design: fix silent absorption of coarser-heading content in task-card split

Source: issue [#115](https://github.com/jacquardlabs/viva/issues/115), filed
as an extension of #110 (task-card review mode for plan documents). Epic:
jig-integration — "Ship two review-surface capabilities jig needs (a unified
Q&A→review session, task-card splitting for plan documents) and a versioned,
documented headless contract that reflects the surface those capabilities
actually produce, so jig can build against viva without re-parsing internals."

## Problem & persona

PRODUCT.md's reviewing-human persona:

> **The reviewing human (primary).** A developer who must sign off on a doc an
> agent produced and refuses to rubber-stamp it. Wants to see each section
> verbatim, leave one or more typed comments per section …

And Principle 1:

> **The section is the unit of trust.** Comment, request changes, or ask per
> section. The document passes only when every section is approved.

`parse_sections.py` auto-detects a split level by picking the highest heading
level (fewest `#`s) that repeats more than once (`_find_split_level`). For a
`PLAN.md`-shaped doc whose only repeating headings are `### Task N` blocks,
this already resolves correctly to level 3 with no `--split-on` flag needed —
verified empirically below. But `_split_sections`'s section-building loop
slices *all* content between one split-level heading and the next into the
preceding section, regardless of what heading levels appear in between. A
heading coarser than the detected split level — e.g. a trailing `## Not-here
follow-ups` after the last `### Task N` — never enters `_find_split_level`'s
level-counting comparison in a way that makes it a split point, so its content
is silently appended, verbatim, inside whichever task card precedes it.

This breaks Principle 1 twice over: the reviewer never gets to approve,
comment on, or request changes to that content as its own unit, and — worse —
they plausibly attribute it to the preceding task's scope, since it renders
inside that card with no visual seam. Principle 2 ("Verbatim, not
summarized... viva never paraphrases the human or the doc") is about content
fidelity within a card; this bug is a fidelity failure one level up, in which
*content* survives the parse but its *section identity* is silently merged
into the wrong unit of trust.

**Reproduced empirically.** Fixture (`# jig PLAN.md` intro, four `### Task N`
blocks, trailing `## Not-here follow-ups`), parsed with today's
`scripts/parse_sections.py`, no `--split-on`:

```
viva: wrote 5 sections → out_before.json
```

`Task 4`'s content ends with:

```
"### Task 4: Add regression test\n\nBody text for task 4.\n\n## Not-here follow-ups\n\n
These are out-of-scope items noticed while planning but not part of this\nplan's tasks.\n\n
- Follow-up A\n- Follow-up B\n"
```

The follow-ups section is real content, matched no card, and is indistinguishable
from Task 4's own body once rendered.

## Proposed design

Fix the auto-detect path only, inside `_split_sections`'s `else` branch (the
one `_find_split_level` feeds). After computing `split_headings` at the
detected level, also promote to a split point any heading whose level is
coarser (fewer `#`s) than the detected level **and** whose line occurs after
the first split-level heading's line:

```python
split_headings = [(lv, t, idx) for lv, t, idx in headings if lv == split_level]
first_split_idx = split_headings[0][2]
coarser = [
    (lv, t, idx) for lv, t, idx in headings
    if lv < split_level and idx > first_split_idx
]
if coarser:
    split_headings = sorted(split_headings + coarser, key=lambda h: h[2])
```

Everything downstream — preamble slicing, the `Revision History`
case-insensitive exclusion, per-section content slicing to the next boundary,
`s1`/`s2`/… id numbering, and the integrity check — runs completely unchanged,
because all of it already only consumes a list of `(title, line_idx)` split
points (same "no second identity rule" pattern #110's design used for
`--split-on`; title still flows through `schema.section_key()` in
`_load_approved`, `_carry_annotations`, `_compute_diffs`, and
`_attach_open_notes` exactly as today, unmodified).

**Why this is safe — a structural invariant, not a heuristic.**
`_find_split_level` returns the *coarsest* level with more than one
occurrence (`sorted(counts.keys())`, first match wins). That means once
`split_level = L` is returned, every level `< L` is *guaranteed* to occur at
most once in the whole document — if any coarser level had occurred twice,
`_find_split_level` would have returned that level instead of `L`. So the
`coarser` list built above can only ever contain distinct, singleton
headings; it can never re-trigger a "coarsest repeater wins" ambiguity, and it
can never interact with the existing `>20 sections → fall back one level`
branch (that branch only returns a coarser level when it independently already
has `count >= 2`, so the invariant holds for whatever level is actually
returned).

**Preamble and the document title are untouched by construction.** The
`idx > first_split_idx` guard excludes anything before the first split-level
heading — which is where a document's `# Title` and its lead-in paragraph
almost always live. `test_preamble_uses_h1_title`, `test_preamble_empty_omitted`,
and every other pre-existing test in `tests/test_parse_sections.py` passed
unchanged against the patched script (32/32), confirming this isn't just an
intent but a verified outcome.

**`--split-on` is completely untouched.** The patch lives entirely in the
`else` branch; the `split_on is not None` branch is not touched by a single
character. This matters because `--split-on`'s "a heading is a split point
iff its title matches, regardless of depth" is #110's own explicit, reviewed,
already-tested design decision (`test_split_on_ignores_coarser_repeated_heading`
asserts a coarser, even *repeating*, non-matching heading stays absorbed under
`--split-on`, by design) — re-litigating that here would contradict a
decision this story doesn't own. There is also no coherent "coarser than the
split" to compute for `--split-on`: matched headings can sit at heterogeneous
depths (`test_split_on_matches_regardless_of_depth` matches a `##` and a `###`
under the same pattern), so there is no single reference level to be coarser
than — #110 explicitly rejected adding one (`No --split-level companion
flag`).

**Fixture verification (patched script, isolated copy — not committed).**
Same fixture as above:

```
viva: wrote 6 sections → out_after.json
=== Task 4: Add regression test (id=s5) ===
'### Task 4: Add regression test\n\nBody text for task 4.\n\n'
=== Not-here follow-ups (id=s6) ===
"## Not-here follow-ups\n\n...\n- Follow-up A\n- Follow-up B\n"
integrity match: True
```

`Task 4` and `Not-here follow-ups` are now two cards; the integrity check
(every non-exempt source char in exactly one section) still passes. A second
fixture with the coarser heading *interleaved* mid-document (`### Task 1`,
`## Aside`, `### Task 2`) produces the same correct result: `Aside` becomes
its own card between the two tasks, integrity check passes. A third fixture
confirmed a documented, previously-deferred gap from #110's design doc (Open
questions: "Revision History under a task-focused pattern") is a specific
instance of this same bug for the auto-detect path, and resolves as a direct
consequence of this fix with no extra code: a trailing `## Revision History`
that's coarser than an auto-detected `###` split level is now correctly
recognized as the ledger boundary (excluded, integrity-exempt) instead of
being absorbed into the last task card. (The `--split-on`-specific instance of
that same open question is not touched — see Out of scope.)

**Full regression pass.** Every test file under `tests/` (32 files, including
all server integration tests) was run against an isolated copy of the repo
with only `scripts/parse_sections.py` swapped for the patched version. All
passed with no changes required to any test — see Evidence.

## User journey

Extends the same core loop PRODUCT.md's Feature map names ("parse → review →
rewrite → loop → sign off with ledger") and the task-card journey #110's
design doc already walked through; no new loop, no changed steps except the
one below.

1. jig's `/plan` skill (the agent author) writes `PLAN.md` with `### Task N`
   blocks, plus (per its handoff §5.2 format) a coarser `## Not-here
   follow-ups` section — exactly the shape found while dogfooding.
2. It parses with the default invocation (or `--split-on`, unaffected either
   way) exactly as #110 documented.
3. **Changed step:** where today the follow-ups content silently vanishes
   into whichever task card happens to precede it, the reviewing human now
   sees it as its own card, titled `Not-here follow-ups`, with its own
   verdict slot. Every other card renders exactly as before.
4. The reviewing human reviews, comments on, approves, or requests changes on
   that card like any other section — per Principle 1, it is now actually a
   unit of trust instead of invisible passenger content on Task N.
5. Round-to-round carry-forward (approvals, diffs, annotations, open notes)
   works unmodified for this new card because it flows through the same
   `title`/`content` → `section_key()` path every other section already uses.
   One transition note: a document that was already mid-round when this fix
   ships, and that happens to have this exact split shape, will see its
   affected task's approval not carry forward round-to-round (content
   byte-identity breaks because the section boundary moved) — this is the
   same "changed content requires re-review" behavior `_load_approved`
   already documents for any other content change, and is the *correct*
   outcome here: the previous approval covered a merged blob the reviewer
   never got to consider as two separate units.
6. Sign-off produces the same verbatim Revision-History ledger, now with a
   row for the follow-ups card if it received a `changes`/`info` verdict —
   nothing new to jig's contract.

## Out of scope

- **`--split-on`'s absorption behavior.** Deliberately unchanged — see
  Proposed design's "why `--split-on` is untouched." A coarser heading not
  matching an explicit `--split-on` pattern keeps today's behavior
  (`test_split_on_ignores_coarser_repeated_heading` still passes unmodified).
- **The Revision-History-under-`--split-on` gap** #110's design doc flagged
  and explicitly deferred ("This story doesn't fix that … Worth a follow-up if
  jig round-trips a `PLAN.md` through sign-off more than once"). This story's
  fix resolves the *auto-detect* instance of that gap as a side effect (see
  Proposed design) but leaves the `--split-on`-specific instance exactly as
  #110 left it — no reference split level exists to define "coarser" against
  under `--split-on`, so there's nothing this story can safely fix there
  without inventing the depth concept #110 explicitly rejected.
- **`_find_split_level`'s "coarsest repeater wins" semantics.** Unchanged. A
  coarser heading that *repeats* two or more times anywhere in the document
  still legitimately wins the split level (`test_split_on_ignores_coarser_repeated_heading`'s
  auto-detect half still asserts this). That's a distinct, pre-existing,
  already-understood behavior — it's the reason `--split-on` exists as an
  escape hatch — not the bug this story fixes. The invariant in Proposed
  design shows the two cases are structurally disjoint: this fix only ever
  fires on headings that occur exactly once.
- **No UI change.** The new card renders exactly like any other section
  card — no new card type, no badge — same precedent #110 set.
- **No new `.viva/*.json` field, no `schema.py` change.** Section objects
  keep the `{id, title, content, …}` shape `validate_review_input` already
  enforces.
- **No `--split-on` behavior for headings finer than the detected level**
  (e.g. `#### Acceptance criteria` nested under a `### Task N`). Those already
  correctly nest inside their parent task's content today — that's the
  existing, desired, unsurprising markdown-nesting behavior, not a bug.

## Alternatives considered

1. **Documentation only** (the issue's second horn: "explicitly document that
   plan-doc authors must keep all top-level content at or below the split
   level"). Rejected as the primary fix, though still worth stating as
   guidance: it pushes the burden onto every doc author (including jig) to
   know an internal parsing detail (which level `_find_split_level` happened
   to pick) before writing a doc, is silently unenforced (nothing warns when
   violated — the whole failure mode is *silence*), and the fix that replaces
   it is small (7 lines), provably scoped to a case that can only ever be a
   singleton heading, and empirically regression-free against the entire test
   suite. A docs-only fix would leave the exact bug #115 reports live in
   viva's default path.
2. **Split on any heading level ≤ detected level, everywhere in the document**
   (not just after the first split heading). Rejected: this would also
   promote a document's `# Title` heading (almost always coarser than a
   detected `##`/`###` split level and almost always the very first heading)
   into its own one-line section, breaking `h1_title`/preamble handling for
   effectively every existing document reviewed today — a correctness
   regression far larger than the bug being fixed. The `idx > first_split_idx`
   guard is the one-line difference between "fixes the reported bug" and
   "breaks preamble everywhere."
3. **Extend the same treatment to `--split-on`.** Rejected — see Out of
   scope: no reference level exists to be "coarser than" under `--split-on`,
   and an existing, deliberate test asserts the opposite behavior there.
4. **Fold this into `_find_split_level` itself** (treat a coarser singleton
   heading as if it always "repeated," so it competes for split-level
   selection). Rejected: conflates two different, unrelated pieces of logic —
   *which* level is chosen (an existing, working heuristic with its own
   20-section fallback) versus *what happens to content at other levels once
   a level is chosen* (this bug). The proposed fix touches only the second
   and leaves `_find_split_level` provably unmodified (0-line diff to that
   function).
5. **A new `--absorb-coarser=false`-style opt-out flag**, defaulting to
   today's absorb-everything behavior for backward compatibility. Rejected:
   there's no real backward-compatibility need to protect — the invariant
   proof plus the full regression pass show the fix is a no-op for every doc
   shape in the existing suite (and, by construction, for every doc that
   doesn't contain a coarser singleton heading after the split point). Adding
   a flag to opt into *correct* behavior inverts the burden PRODUCT.md's
   Principle 4 ("No-op when absent... degrades to exactly the prior behavior
   when its state file is missing") is about — this isn't a new optional
   layer, it's closing a gap in the core parse step every review already
   depends on.

## Operational readiness

N/A — no operational surface. `scripts/parse_sections.py` is a stdlib-only,
stateless CLI filter (per CLAUDE.md's Architecture section); there is no
deployed service, no metrics pipeline, and no persisted state to migrate.
`.viva/*.json` round files are disposable per session (CLAUDE.md's state
-lifecycle note); this story adds no new file and no new schema field.
Rollback is a straight `git revert` of the fix's commit — a document
re-parsed after revert produces exactly the same (pre-fix) sections it did
before this story landed, since the change is purely additive to which
headings become split points.

## Open questions

- **Should build phase's regression test live in `tests/test_parse_sections.py`
  alongside the existing `--split-on`/auto-detect tests, or get its own
  fixture file under `tests/fixtures/`?** The existing `--split-on` tests
  (e.g. `test_split_on_fixture_one_section_per_task`) use a shared
  `tests/fixtures/PLAN.md`; this story's fixture needs a *trailing coarser
  heading* the current `PLAN.md` fixture doesn't have. Leaning toward an
  inline doc string (matching most of the file's existing tests) plus one
  assertion against the shared fixture only if it's extended — deferring the
  concrete choice to build phase since it's an implementation-organization
  question, not a design one.
- **Does jig's actual `PLAN.md` handoff format (§5.2) ever place a coarser
  heading *before* the first `### Task N` block**, e.g. a `## Overview`
  preface? If so, that content stays folded into the preamble section exactly
  as it does today (unaffected by this fix, and not the shape #115's Context
  section described) — flagging in case jig's dogfooding surfaces a related
  but distinct "preamble swallows an Overview aside" complaint later; this
  story does not attempt to guess at or pre-empt that.
