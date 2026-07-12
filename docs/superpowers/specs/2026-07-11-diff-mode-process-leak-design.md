# Design: Diagnose and fix orphaned `--mode diff` server processes (#116)

**Date:** 2026-07-11
**Issue:** [#116](https://github.com/jacquardlabs/viva/issues/116) — "Multiple orphaned `--mode diff` server processes found running, unrelated to any active session"
**Related:** [#112](https://github.com/jacquardlabs/viva/issues/112) — "QA-mode server never calls `/complete`" (separate story, `qa-next-round-hardening`, in progress on a sibling branch)
**Epic:** jig-integration — "Ship two review-surface capabilities jig needs (a unified
Q&A→review session, task-card splitting for plan documents) and a versioned,
documented headless contract that reflects the surface those capabilities
actually produce, so jig can build against viva without re-parsing internals."
**Status:** Draft — pending `/gate-design-review`

---

## Problem & persona

PRODUCT.md names the reviewing human as viva's second persona:

> **The reviewing human (primary).** A developer who must sign off on a doc
> [or diff] an agent produced and refuses to rubber-stamp it. ... Reviews
> many docs, so wants recurring critiques learned rather than re-typed.

`/viva-diff` is this persona's entry point for the diff checkpoint PRODUCT.md's
thesis names explicitly: "a diff checkpoint (hunk-by-hunk code review before a
commit)." This persona runs `/viva-diff` repeatedly, in the same repo, across
many separate review sessions over days or weeks — the same "reviews many
docs" habit PRODUCT.md already attributes to them, applied to diffs instead of
markdown files.

The problem, confirmed live during jig's dogfood of #109 (issue #116's own
report): 5 separate `server.py --mode diff ...` processes were found running
on one machine, from unrelated sessions spanning several days, none of them
tied to an active review. Two concrete harms follow directly from PRODUCT.md's
principle 6 ("Local and keyless ... a single stdlib-only Python server"),
which promises a lightweight local tool, not one that silently accumulates
background processes:

1. **Resource leak.** Each orphaned process holds an open TCP listener and a
   live Python interpreter indefinitely, with no automatic cleanup — nothing
   in `server.py` ever times out a session that stops receiving requests.
2. **A concrete, repo-scoped block on the *same* persona's next session.**
   `diff.md`'s own launch guard (line 36) refuses to start a new `/viva-diff`
   session in a repo where `.viva/server.url` already exists: `[ -f
   .viva/server.url ] && { echo "viva-diff: a prior session may still be
   running (.viva/server.url exists)"; exit 1; }`. An orphaned process from a
   *finished* review (nothing left to approve) leaves that file in place
   forever, so the reviewing human's next `/viva-diff` invocation in that same
   repo fails outright until they manually find and kill the stale PID — the
   opposite of principle 4 ("No-op when absent... A plain review never pays
   for a feature it does not use").

Issue #116 was filed without a confirmed root cause, explicitly asking the
design phase to determine whether this is #112's shape (a documented finish
path that never calls `/complete`) or something distinct to diff mode's own
lifecycle. That diagnosis is this doc's first job.

## Diagnosis

**Not #112's shape.** #112's bug is structural and total: `brainstorming-qa.md`
never calls `POST /complete` anywhere in its documented qa-mode steps, so
*every* qa session leaks by construction. `/viva-diff`'s primary finish path
is not missing this call — `.claude/skills/viva/diff.md` step 5 ("Finish")
already does exactly what `SKILL.md`'s review-mode step 5 does:

```bash
curl -s -X POST "$BASE/complete" -H "Content-Type: application/json" \
  -d "{\"rounds_total\": N, \"sections_total\": M, \"sections_revised\": K}"
```

And the server-side mechanism this call depends on is already correct and
already tested for diff mode specifically — `tests/test_server_diff.py`'s
`test_diff_mode_complete_shuts_down` launches a real `--mode diff` subprocess,
POSTs `/complete`, and asserts `proc.poll()` goes non-`None` within the
2-second grace window (`server.py:3167-3181`'s `threading.Timer(2.0,
_shutdown.set).start()`, generic across all three modes). That test passes
today. So the primitive works; the question is whether every path through
`/viva-diff`'s documented loop reaches it.

**It doesn't — diff mode has its own, distinct completion gap.** Unlike
`SKILL.md`'s review loop (which always either waits for the next round or
reaches step 5 — there is no branch that exits the loop early), `diff.md`
step 4 ("Re-diff and re-arm") re-derives the entire review input from the
filesystem's current diff on every round, and has to handle the case where
that diff is now empty (the human's requested edits fully resolved it — e.g.
a `changes` comment on the review's only remaining hunk asked to revert it,
and the agent's edit did exactly that):

```bash
git diff <ref> > .viva/diff.patch 2>/dev/null
[ -s .viva/diff.patch ] || { echo "viva-diff: diff is now empty — all changes may have been fully applied"; exit 0; }
```

This branch (`diff.md:91`, present since the original design at
`docs/superpowers/plans/2026-07-02-human-checkpoint-primitives-and-diff-mode.md:1606`)
prints a message and exits the bash block — but the server launched back in
step 1 is still running, still listening, still holding `.viva/server.url` on
disk. Nothing in this branch, or anywhere else in the documented steps,
signals that server to shut down. The loop simply stops being driven. This is
diff mode's own structural analogue of #112's gap — a finish-shaped moment
that never calls `/complete` — but it lives in a different step (a mid-loop
early exit, step 4) than the one #112 fixes (the primary finish step), and it
arises from a control-flow shape (re-deriving state from `git diff`) that
review mode's loop, working from a static parsed document, structurally
cannot hit. That is the "distinct cause" issue #116 asked the design phase to
confirm or rule out.

(Two smaller, less likely contributors are named in Open questions rather
than folded into the fix: a slow/failed launch at step 1's 10-second poll,
and unrelated session abandonment — a human closing the terminal mid-review.
Neither matches the issue's evidence — 5 fully-bound, multi-day-old,
presumably-idle listeners — as well as the empty-diff exit does, and neither
is diff-mode-specific.)

## Proposed design

From the reviewing human's point of view, nothing about `/viva-diff`'s normal
flow changes. What changes: when their requested edits fully resolve the
diff before every hunk was individually approved, the session now finishes
the same way it would if they had clicked approve on every remaining hunk —
the browser tab reflects completion, and the background server exits within
its normal 2-second grace window — instead of silently going idle forever
with no visible signal that anything is wrong.

Concretely, `diff.md` step 4's empty-diff branch is changed to run the same
finish sequence step 5 already runs, before exiting the block:

```bash
git diff <ref> > .viva/diff.patch 2>/dev/null
if [ ! -s .viva/diff.patch ]; then
  echo "viva-diff: diff is now empty — all changes were fully applied; finishing"
  curl -s -X POST "$BASE/complete" -H "Content-Type: application/json" \
    -d "{\"rounds_total\": N, \"sections_total\": M, \"sections_revised\": K}"
  exit 0
fi
```

`N`, `M`, `K` are the same counters the agent already tracks by the time it
can reach *any* finish point today (step 5's existing call needs them too) —
this is not new bookkeeping, only a second call site for a payload the loop
already knows how to build. The human then gets the same sign-off report
("N hunks approved across M files in R round(s). K hunks revised.") and the
same "Commit these changes?" prompt that step 5 already gives — the empty-diff
case genuinely is "finished," just via a different path, and should read that
way to the human instead of silently trailing off with a message they may
never see (the message goes to the agent's terminal transcript, not the
browser tab that the review actually took place in).

This leans on two PRODUCT.md principles directly:

- **Principle 4 (No-op when absent).** A review that reached its natural end
  should leave *no* trailing state (`server.url`, a live process) behind for
  the human to clean up by hand — the same guarantee step 5 already gives.
- **Principle 6 (Local and keyless / cheap for the agent).** The fix reuses
  the exact `/complete` call and payload shape step 5 already sends and that
  the server already handles correctly and has a passing test for — no new
  endpoint, no new server-side state, no new schema field.

## User journey

1. The reviewing human runs `/viva-diff` against a working-tree diff with two
   hunks in `foo.py`. The server launches, the browser tab opens showing both
   hunks.
2. They mark hunk 1 `approved` and leave a `changes` comment on hunk 2:
   "actually, drop this change entirely — revert it."
3. The agent applies the requested edit, reverting hunk 2's change in
   `foo.py`. Step 4 re-diffs: with hunk 1 already matching `<ref>` (unchanged
   in this round) and hunk 2 now reverted, `git diff <ref>` produces no
   output.
4. **Today:** the agent prints "diff is now empty — all changes may have been
   fully applied" to its own terminal transcript and stops. The human, still
   looking at the browser tab showing hunk 2 pending review, sees nothing
   change — no completion state, no indication the review concluded. The
   server process keeps running. If they start a new `/viva-diff` review in
   this same repo tomorrow, step 1's guard immediately fails: "a prior
   session may still be running (.viva/server.url exists)" — even though
   yesterday's review was, in every meaningful sense, done.
5. **With this fix:** the same empty-diff branch also POSTs `/complete`. The
   browser's SSE `complete` event fires exactly as it does for a normal
   all-approved finish, the human gets the same sign-off report and commit
   prompt in the agent's response, and the server process exits within its
   usual 2-second window. `.viva/server.url` is removed (`server.py:3236`,
   generic to every exit path). Their next `/viva-diff` invocation in this
   repo, tomorrow or next week, launches cleanly.

## Out of scope

- **#112 itself (qa-mode's structural gap).** Tracked and being fixed by the
  sibling `qa-next-round-hardening` story on its own branch
  (`epic/jig-integration--qa-next-round-hardening`). This story does not
  touch `brainstorming-qa.md` or any qa-mode code path.
- **A general idle-timeout / heartbeat reaper in `server.py`.** Would give
  defense-in-depth against *any* abandoned session in *any* mode (a human
  closes their laptop mid-review, a session crashes) — but that is a new
  server-side lifecycle mechanism affecting all three modes, a materially
  larger change than the acceptance criteria's "diagnose ... fix
  accordingly" asks for, and not what the evidence in #116 points at (see
  Diagnosis — the empty-diff exit is a sufficient, targeted explanation).
  Considered and rejected below as this story's fix; left as an open
  question for whether it is worth a future story if orphans recur after
  this fix ships.
- **The two smaller candidate causes named in Diagnosis** (a launch that
  backgrounds successfully but is slow to write `server.url` past the
  10-second poll window; a human abandoning a session mid-review by closing
  the terminal). Neither is diff-mode-specific, and #116's evidence — fully
  bound, multi-day-idle listeners — fits the empty-diff gap more precisely
  than either. Not fixed here; flagged in Open questions.
- **No changes to `server.py` or `scripts/parse_diff.py`.** The server-side
  `/complete` → shutdown mechanism this fix depends on is already correct
  and already covered by `tests/test_server_diff.py::test_diff_mode_complete_shuts_down`.
  This is a `.claude/skills/viva/diff.md` prose/control-flow fix only — it
  also means this story cannot collide with the sibling stories that do
  touch `server.py` (`qa-next-round-hardening`, `qa-recommended-choice`,
  `qa-handoff-spinner-timeout` — see the epic pre-mortem addendum's item 6).
- **No retroactive cleanup of already-orphaned processes.** Out of scope for
  a design/code fix — the reviewing human kills the 5 existing stragglers by
  hand once, same as they would today; this story prevents new ones.

## Alternatives considered

1. **Ship a general idle-timeout reaper in `server.py`** (e.g., shut down
   after N minutes with no HTTP request). Rejected as this story's fix:
   larger surface (touches all three modes' shared server code, needs a
   tuned timeout that doesn't false-positive on a human genuinely reading a
   large diff for 20 minutes), and the diagnosis shows a precise, narrower
   gap that fully explains the reported symptom without it. Worth
   reconsidering later only if orphans recur after this fix ships (see Open
   questions) — simplest-fix-first per CLAUDE.md's Design & Planning
   guidance.
2. **Treat "diff is now empty" as an error, prompting the human to
   confirm/re-run `/viva-diff` explicitly to close it out.** Rejected: it is
   not an error — it is the review reaching its natural end through a
   different door than "every hunk individually approved." Making the human
   take an extra manual step to close a session that already succeeded adds
   friction PRODUCT.md's principle 5 ("cheap for the agent") and the "human
   signs off, not babysits infrastructure" ethos both argue against.
3. **Have `parse_diff.py` itself detect an empty diff and refuse to be
   invoked**, pushing the check earlier. Rejected: the empty-diff condition
   here arises *mid-loop*, after step 1 already launched a live server with
   a non-empty round 1 — there is no earlier point to push the check to
   without restructuring the whole loop; the fix belongs exactly where the
   condition is discovered, step 4.
4. **Make the browser's own idle state trigger shutdown client-side** (e.g.,
   JS-side timer if no round update in N minutes). Rejected: adds
   browser-side complexity and a new failure mode (an active human reading a
   diff slowly gets shut down from under them) to solve a problem that has a
   precise, agent-side, zero-ambiguity trigger (`git diff` is empty) already
   available at the exact moment it should fire.

## Operational readiness

- **Migration:** none — a documentation/prose change to `.claude/skills/viva/diff.md`,
  no schema, no on-disk state shape, no server code.
- **Rollback:** revert the file's commit. The reverted state is exactly
  today's shipped behavior (the underlying bug) — no data or state to unwind
  since the change touches no persisted format.
- **Rollout:** takes effect the next time a human invokes `/viva-diff` after
  the change lands (a new skill invocation reads the current file — no
  running sessions to migrate, since anything currently running was launched
  from the pre-fix version of the skill).
- **How we'll know it's working:** the mechanism this fix relies on
  (`/complete` → 2-second shutdown) already has a passing regression test,
  `tests/test_server_diff.py::test_diff_mode_complete_shuts_down`. This
  story's build phase should add a companion test that exercises the new
  call site specifically — most directly, a small addition to
  `tests/test_server_diff.py` that POSTs the same `/complete` payload the
  empty-diff branch now sends and asserts the same shutdown behavior (the
  server-side handling is call-site agnostic, so this closes the loop on
  "the new call site sends a payload the server actually accepts and acts
  on," which is the part this story adds). The skill's own bash control flow
  (the `if [ ! -s .viva/diff.patch ]` branch) is prose the agent executes,
  per CLAUDE.md's architecture note ("There is no `main()` in code that runs
  the loop; the agent is the runtime") — it is not independently unit-testable
  the way `scripts/*.py` are, so the regression coverage lives at the
  primitive it depends on, matching how `test_diff_mode_complete_shuts_down`
  already covers step 5's identical call.
- **Failure mode if this fix is wrong or incomplete:** identical to today's
  bug — a process keeps running past a review that has effectively ended.
  No new failure mode is introduced (the change only adds a call to an
  already-correct, already-tested existing endpoint); the worst case is "no
  better than before," not "worse."

## Open questions

- **Should the two smaller candidate causes named in Diagnosis (slow launch
  past the 10-second poll; a human abandoning a session mid-review) get
  hardened too?** Left open rather than bundled in, since #116's evidence
  doesn't clearly implicate either, and CLAUDE.md's CI/Lint Scope guidance
  ("don't fix ... failures outside the scope of the current change")
  argues for keeping this story's diff tight to the diagnosed cause. If
  orphans are still observed after this fix ships, that would be the signal
  to open a follow-up.
- **Is a general idle-timeout reaper worth a future story?** Rejected here
  as scope creep on this bug fix (see Alternatives considered #1), but the
  question of "what happens when a human abandons a `/viva-diff` (or
  `/viva`, or `/viva-qa`) session entirely mid-review, terminal closed and
  all" is real and applies to every mode, not just diff. Left for a
  separate should-we-build conversation rather than decided here.
- **Whether this same "state can legitimately reach zero before every
  section is individually approved" shape exists anywhere in review or
  qa-mode's loop.** This design's diagnosis found it does not today (see
  Diagnosis) — review mode's loop has no analogous early exit. Flagged here
  only so a future change to either loop's control flow re-checks this
  invariant rather than assuming it holds by construction.
