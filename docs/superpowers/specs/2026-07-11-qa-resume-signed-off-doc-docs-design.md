# Design: Document Resuming Review on an Already-Signed-Off Doc (#113)

**Date:** 2026-07-11
**Issue:** [#113](https://github.com/jacquardlabs/viva/issues/113) — "SKILL.md doesn't document resuming review on an already-signed-off doc"
**Epic:** jig-integration — "Ship two review-surface capabilities jig needs (a unified
Q&A→review session, task-card splitting for plan documents) and a versioned,
documented headless contract that reflects the surface those capabilities
actually produce, so jig can build against viva without re-parsing internals."
**Status:** Draft — pending `/gate-design-review`

---

## Problem & persona

PRODUCT.md names the agent author as viva's first persona:

> **The agent author (primary, non-human).** Claude Code, having written a
> spec, ADR, runbook, or design doc. It needs to hand the doc to a human for
> sign-off without burning context: parse without reading the doc, wait
> without polling cost, rewrite only flagged sections, and learn what this
> reviewer always wants.

Issue #113 names a sharper instance of this persona directly: "a fresh-context
executor given only a task block and SKILL.md as a Read-first pointer (jig's
`/build` dispatch model)." This executor has no memory of any prior viva
session — everything it knows about how to run a review comes from reading
SKILL.md once, cold, in the current turn. That is exactly the failure mode
the issue reports: jig's own dogfood followed SKILL.md's documented round-1
steps literally — "ran the generic clear-state block ahead of a revision
round" — and destroyed the previous session's `review-input-rN.json` /
`review-rN.json` before they could be referenced, because SKILL.md's step 1
never distinguishes "a doc that has never been through viva" from "a doc
that already completed sign-off in a prior session and is now being opened
again."

Today, SKILL.md's step 1 ("Parse and launch") already branches once — a
no-read default path, and a second path when standing learned preferences
exist (`## Steps`, "Learned-preference auto-engage"). Both paths open with
the identical clear-state block:

```bash
rm -f .viva/server.url .viva/review-input-r*.json .viva/review-r*.json .viva/open-notes.json
rm -rf .viva/attachments
```

This block is correct for a genuinely first-ever review of a doc — there is
nothing to preserve. It is silently destructive for the resume case: the
prior session's finishing-round files are the *only* on-disk evidence of
which sections a human already approved, and they live at exactly the paths
this glob deletes. `parse_sections.py --prior-input`/`--prior-verdicts`
already exists to carry forward byte-identical approved sections (verified
in `scripts/parse_sections.py`'s `_load_approved`, and exercised by
`tests/test_parse_sections.py::test_approved_matching_same_content`) — the
gap is purely procedural: SKILL.md never tells the agent to reach for those
files, or to save them, before the block above runs.

This is distinct from "round 2 of a live session," which is already
well-documented (step 4, "Rewrite and re-arm") and never touches the
clear-state block at all — a live session's round 2 reads the still-present
round-1 files and POSTs to the still-running server; nothing is deleted in
between. The bug is specific to the boundary between two *separate*
sessions on the same doc, which today collapses into "just run round 1
again" with no acknowledgment that `.viva/` may still be holding the
previous session's approvals.

## Proposed design

Add a third, explicitly named branch to SKILL.md's step 1 — "Resuming
review on an already-signed-off doc" — parallel in structure to the existing
"Learned-preference auto-engage" callout, so the step keeps its current
shape (a default path, plus documented exceptions the agent checks for
before running the shared clear-state block).

**Trigger.** The agent recognizes this case from context, the same way it
already recognizes the standing-preferences case by checking state rather
than by an automatic heuristic buried in a script: the doc being opened for
review already carries a `## Revision History` heading (written by
`revision_history.py` at every prior sign-off — `HEADING = "## Revision
History"` in `scripts/revision_history.py`), and/or the caller's own context
says explicitly that this doc was already signed off in a previous session
(e.g., jig re-opening review after further edits). This mirrors the existing
pattern exactly — SKILL.md already asks the agent to check
`preferences.py list --status standing` before choosing a branch; here it
asks the agent to check the doc for a Revision History heading and its own
situational knowledge, not a new script flag.

**Procedure**, run *before* the shared clear-state block, only when the
trigger above applies:

1. **Locate** the highest-numbered `review-input-rN.json` / `review-rN.json`
   pair still present in `.viva/` — the tail of the just-completed prior
   session. (If neither file is present — a fresh clone, or `.viva/` was
   already cleaned since sign-off — there is nothing to preserve; fall
   through to the default no-prior round-1 launch. This is PRODUCT.md
   principle 4, "no-op when absent," applied to this branch: the resume
   optimization degrades to today's exact behavior when its evidence is
   gone, it does not try to reconstruct it.)
2. **Copy** (not move) that pair to two fixed names that the clear-state
   glob cannot match: `.viva/prior-review-input.json` and
   `.viva/prior-review-verdicts.json`. Neither name starts with
   `review-input-r` or `review-r`, so `rm -f .viva/review-input-r*.json
   .viva/review-r*.json` cannot touch them. (Confirmed by construction: the
   glob requires the literal substring `review-input-r` or `review-r`
   immediately after `.viva/`; `prior-review-input.json` starts with
   `prior-`, not `review-`.)
3. **Run** the existing clear-state block unchanged — safe now, since the
   copies live outside its glob.
4. **Parse round 1** of the new session exactly as the default path does,
   with two additional flags pointed at the copies:
   ```bash
   python3 "$VIVA_DIR/scripts/parse_sections.py" <doc.md> \
     --output .viva/review-input-r1.json --round 1 --doc-file <relative/path/to/doc.md> \
     --prior-input .viva/prior-review-input.json \
     --prior-verdicts .viva/prior-review-verdicts.json
   ```
   `_load_approved` (unmodified, already round-number-agnostic — it reads
   only `prior_in`/`prior_v` content, never compares round numbers) carries
   forward every section whose title and content are still byte-identical
   to what was approved before. The human reviews only what changed since
   sign-off; everything else collapses into the round as pre-approved,
   exactly like any other round 2+ carry-forward, just across a session
   boundary instead of within one.
5. **Discard** the two preserved copies once round 1's parse has written
   `.viva/review-input-r1.json` successfully — they have done their one job
   (feed round 1's `--prior-input`/`--prior-verdicts`) and keeping them
   around risks a *third* future resume silently reading a two-sessions-old
   pair instead of the one that just ran.

Sign-off at the end of the new session is unaffected: `revision_history.py`
already appends a second block under an existing `## Revision History`
heading rather than overwriting it ("If the heading already exists
(re-reviewed doc), the new session's block is appended under it" — already
documented in step 5), and its round-file glob (`viva_dir.glob
("review-input-r*.json")`) only ever sees the *new* session's own
`review-input-r1.json` onward, because the preserved copies from step 2
above were named outside that glob and are gone by the time sign-off runs.
Round numbering restarting at 1 for the new session is intentional and
already the shape `revision_history.py` expects — it is not asked to
reconcile round numbers across the session boundary, only to append a new,
separately-dated block.

**No code changes.** Every mechanism this design depends on —
`--prior-input`/`--prior-verdicts`, `_load_approved`'s byte-identity rule,
`revision_history.py`'s append-under-existing-heading behavior — already
ships and is already exercised by `tests/test_parse_sections.py` and
`tests/test_revision_history.py`. This story's entire
deliverable is the missing procedural instruction in SKILL.md that tells the
agent to use what already exists, in the right order, instead of deleting
the evidence first.

## User journey

1. **Session A.** `/viva` reviews `doc.md`. The human approves every
   section by round 2. Step 5 runs: `revision_history.py` appends `##
   Revision History` to `doc.md`, sign-off completes, the agent offers to
   commit. `.viva/review-input-r2.json` and `.viva/review-r2.json` — the
   finishing round — are still sitting in `.viva/` (nothing in step 5 clears
   them; the clear-state block only ever runs at the *next* round-1 launch).
2. Time passes in the same clone. Either the human edits `doc.md` further
   by hand, or a fresh-context executor (jig's `/build` dispatch, or a new
   Claude Code conversation) is asked to review it again — for example
   because upstream content changed and a subset of sections need a fresh
   pass.
3. `/viva` starts again. The Invocation guard confirms no server is running
   (`.viva/server.url` absent) — safe to proceed to step 1. The agent notices
   `doc.md` already carries `## Revision History` and/or already knows from
   its own context that this doc was signed off before: the new "Resuming
   review on an already-signed-off doc" branch applies, not the default
   first-ever-review path.
4. Before touching the clear-state block, the agent copies
   `review-input-r2.json` / `review-r2.json` to
   `.viva/prior-review-input.json` / `.viva/prior-review-verdicts.json`.
5. The clear-state block runs exactly as documented today — now harmless to
   the copies, which live outside its glob.
6. Round 1 of the new session parses with `--prior-input
   .viva/prior-review-input.json --prior-verdicts
   .viva/prior-review-verdicts.json` added to the otherwise-unchanged launch
   block. `viva: wrote N sections → ... (K pre-approved)` on stdout confirms
   how many sections carried forward untouched.
7. The browser opens showing only the sections that actually changed (or
   are new) as needing review; previously-approved, still-identical
   sections are pre-approved and collapsed, exactly as a live round 2+
   already presents carried-forward approvals.
8. The human reviews the delta, not the whole doc again. The loop proceeds
   exactly as the existing core loop from here — nothing past round 1
   changes.
9. Sign-off appends a **second**, separately dated block under the existing
   `## Revision History` heading (already-supported behavior), and the two
   preserved copies from step 4 are discarded — the on-disk evidence a
   *third* resume would need is now `review-input-r{final}.json` /
   `review-r{final}.json` from *this* session, not the stale pair from
   session A.

This composes two mechanisms PRODUCT.md's feature map already names as
already-shipped, unrelated lines — the core review loop's round-to-round
carry-forward, and the verbatim Revision History ledger's append-not-replace
behavior — into a documented procedure for the one seam between them that
SKILL.md currently leaves unhandled: the boundary between two separate
sessions on the same doc. It introduces no third capability.

## Out of scope

- **No code changes.** `parse_sections.py`, `server.py`, `schema.py`,
  `open_notes.py`, `revision_history.py` are all unmodified — this story
  documents an existing, already-tested mechanism used in a new order, it
  does not add or change behavior in any script.
- **No automatic/programmatic resume detection.** No new
  `parse_sections.py` flag or `server.py` behavior infers "this is a
  resume" from disk state on its own. Detection stays an agent judgment
  call documented in SKILL.md prose (check for `## Revision History` in the
  doc, or situational context), matching the existing standing-preferences
  branch's pattern exactly. `.viva/` is one flat, undifferentiated directory
  with no per-doc namespace — silently auto-wiring whatever round files
  happen to be present risks carrying forward approvals that belong to a
  different, unrelated doc's abandoned session. An explicit, agent-driven
  branch avoids inventing a namespacing scheme this story doesn't need.
- **No change to `.viva/`'s disposability policy.** CLAUDE.md's "State
  lifecycle" note ("everything else under `.viva/` is disposable and reset
  each session") stays accurate in spirit: the preserved copies are read
  once by the new round 1's parse and then discarded (step 5 of Proposed
  design) — nothing new persists past the resume itself. See Open questions
  for whether that note needs a one-line pointer to this new branch.
- **No handling of state lost across clones/machines.** If `.viva/` genuinely
  has no trace of the prior session (fresh clone, manual cleanup, a
  different machine), there is nothing to preserve and no attempt is made
  to reconstruct approvals from git history or the Revision History table
  already written into the doc — the branch falls through to today's
  ordinary first-round behavior. PRODUCT.md principle 4 ("no-op when
  absent") applies directly.
- **No handling of an abandoned (never-signed-off) prior session.** The
  preserve-then-reference mechanism happens to generalize to a session that
  crashed mid-review rather than reaching sign-off, but issue #113 and the
  acceptance criteria name only the already-signed-off case. This design
  documents that one case; a crashed-session resume is not tested or
  claimed here and is left for a future issue if it turns out to matter in
  practice.
- **No new test infrastructure.** The carry-forward mechanism this design
  leans on is already covered by
  `tests/test_parse_sections.py::test_approved_matching_same_content` (and
  siblings), which already exercise `--prior-input`/`--prior-verdicts`
  without any round-number continuity requirement. See Operational
  readiness for the one narrow regression test proposed for the build
  phase.

## Alternatives considered

1. **Never delete round files; keep an unbounded history under `.viva/`.**
   Rejected: directly contradicts CLAUDE.md's explicit state-lifecycle
   policy ("everything else under `.viva/` is disposable and reset each
   session") and PRODUCT.md principle 4 ("a plain review never pays for a
   feature it does not use"). An ever-growing round-file history is exactly
   the kind of new persistent state CLAUDE.md says needs a documented
   reason to exist; preserving only the one pair actually needed, then
   discarding it, is cheaper and needs no new justification.
2. **Auto-detect resume purely from `.viva/`'s contents, with no explicit
   agent branch.** Rejected (see Out of scope): `.viva/` has no per-doc
   namespace, so blindly wiring whatever `review-r*.json` happens to be the
   highest-numbered file into `--prior-input` risks carrying forward
   approvals from an unrelated doc's leftover session. Keeping detection an
   explicit, documented judgment call (checking the doc's own `##
   Revision History` heading, which *is* doc-specific) matches the existing
   standing-preferences pattern and avoids that risk without inventing new
   machinery.
3. **Store the preserved copies outside `.viva/`** (repo root dotfile,
   `/tmp`, or similar). Rejected: `.viva/` is the project's one established
   location for all review state — CLAUDE.md: "coupled only by JSON files
   under `.viva/`." The collision this design fixes is solved entirely by
   choosing names outside the destructive glob; there is no reason to leave
   the directory the rest of the protocol already uses.
4. **Bump `--round` to continue numbering across the session boundary**
   (e.g., new session's first round is numbered 3, continuing from session
   A's final round 2) instead of restarting at 1. Rejected: `_load_approved`
   is already round-number-agnostic, so continuity buys no carry-forward
   benefit, and `revision_history.py`'s existing append-under-existing-
   heading behavior already assumes each session's own round files start
   fresh at 1 (its glob only ever sees the current session's `.viva/`
   contents) — continuing the numbering would require *new* code to track
   the last global round across sessions, for a benefit (a monotonic round
   counter across sign-offs) nothing in PRODUCT.md or the acceptance
   criteria asks for.

## Operational readiness

- **Migration:** none — no schema, script, or on-disk format changes. The
  two preserved-copy filenames (`.viva/prior-review-input.json`,
  `.viva/prior-review-verdicts.json`) are new only in the sense that
  SKILL.md now names them; nothing parses or validates a new shape — they
  are byte-identical copies of the existing `ReviewInput`/verdicts shapes
  already defined in `schema.py`.
- **Rollback:** revert the SKILL.md commit. The procedure is pure
  agent-executed prose with no code path depending on it; reverting returns
  every future session to today's behavior (clear-state always runs first,
  with no preserve step) with zero residual effect.
- **Rollout:** ships as a normal doc commit; takes effect the next time an
  agent reads SKILL.md. No flag, no staged rollout — matches how every
  other SKILL.md procedural change in this project has shipped.
- **Observability:** none needed beyond what already exists.
  `parse_sections.py`'s existing stdout line — `viva: wrote N sections → ...
  (K pre-approved)` — already reports how many sections carried forward on
  every round, including this one; a human or agent watching the terminal
  can already confirm the resume worked (K > 0 on a doc that was previously
  fully approved) without any new instrumentation.
- **Failure mode:** if an agent skips the new branch and runs the plain
  clear-state block anyway (reverting to today's behavior by omission, not
  by a code path), the result is not a crash or data loss — round 1 of the
  new session becomes an ordinary from-scratch parse with zero pre-approved
  sections, and the human re-approves every section, including ones
  unchanged since the prior sign-off. That is exactly today's status quo,
  not a new failure mode; the previously-approved text is still permanently
  recorded in the doc's own `## Revision History` table and, once
  committed, in git history. The worst case is wasted review time, never
  lost approvals.
- **Proposed for the build phase (not mandated by this design, since
  CLAUDE.md's test requirement targets features/bug fixes and this is a
  docs-only deliverable):** one additional regression test in
  `tests/test_parse_sections.py` that pins down the fact this design
  depends on but that no existing test states explicitly — that
  `--prior-input`/`--prior-verdicts` carry forward approvals when the new
  `--round` is *not* one greater than the prior round's own `round` field
  (e.g., new round `1` referencing a prior round `2`, mirroring a resumed
  session restarting its numbering). This guards the documented procedure
  against a future accidental round-continuity check being added to
  `_load_approved`.

## Open questions

- **Whether CLAUDE.md's "State lifecycle" line needs a one-line pointer to
  this new branch.** CLAUDE.md currently states "everything else under
  `.viva/` is disposable and reset each session" without qualification.
  This design does not add new persistent state (the preserved copies are
  read once and discarded within the same resume), so the line stays
  technically accurate, but a reader could still be surprised that
  clear-state's timing now depends on a preceding preserve step for this
  one case. Left to the build phase to decide whether a short cross-
  reference from CLAUDE.md to SKILL.md's new branch is worth adding, or
  whether SKILL.md alone (where the acceptance criteria scopes this story)
  is sufficient.
- **Exact preserved-copy filenames.** `.viva/prior-review-input.json` /
  `.viva/prior-review-verdicts.json` are proposed here because they read
  clearly and provably avoid the clear-state glob (verified by
  construction in Proposed design) — the build phase should treat the
  specific names as a detail it can finalize, not a decision this design
  doc needs to re-litigate, as long as whatever is chosen avoids the same
  two glob patterns.
- **Whether the proposed regression test (Operational readiness) lands in
  this story's build phase or as a follow-up.** Flagged as a proposal, not
  a requirement, matching how the sibling `headless-contract` design doc
  treated its own proposed drift-guard test.
