# Manual browser-check evidence — Task 1 (approve-shortcut)

`PLAN.md`'s own Evidence field requires two manual browser runs (review mode,
diff mode) beyond the automated suite: "a passing test suite alone is not
sufficient evidence for this task — it only proves the source calls the
right function, not that the guard actually blocks a stale write at
runtime." Both runs were performed, by two independent executor dispatches
during `/build`, but the results were only narrated back to the Foreman
in-session and never written into the repo — a gap gate-audit round 1
flagged (product-reviewer, corroborated by premortem-auditor and
architecture-auditor). This file closes it.

**Attribution.** Neither run below is this document's author's own
observation — both are the build executors' self-reported runtime results,
transcribed here verbatim in substance from their own return messages, for
the durable record `/gate-audit` and later readers need. Method, for both:
`claude-in-chrome` was unavailable (extension not connected) and macOS
Accessibility permission was denied for System Events keystroke automation,
so each executor drove a dedicated headless Chrome instance over raw CDP
(`--remote-debugging-port`, isolated scratch profile, Node's built-in
WebSocket/fetch, no new packages installed) and dispatched an untrusted
`KeyboardEvent('keydown', {key: 'a'})` on `document` — valid here since the
keydown handler reads only `e.key`, modifier flags, and
`activeElement.tagName`, none of which depend on `isTrusted`. Neither run
was captured by a committed script or fixture — this is an ad hoc,
one-off harness, not a repeatable probe; this repo has no scripted-browser
tooling (stdlib-only per `CLAUDE.md`), which is why the plan calls for a
manual check at all.

## Run 1 — executor for commit `1767424` (rebased forward to `640a944`), 2026-08-04T00:40Z

Four runs, one script, one fixture set: the two required (fixed) plus two
pre-fix counter-proof runs against `git show HEAD:server.py` before this
task's own fix landed, to confirm the check discriminates rather than
passing vacuously.

| Run | `verdictAfterA` | `isApprovedClassAfterA` | `activeStillS1AfterA` | recap row (after comment removed) |
|---|---|---|---|---|
| review mode, fixed | `null` | `false` | `true` | `"pending"` |
| diff mode, fixed | `null` | `false` | `true` | `"pending"` |
| review mode, pre-fix (counter-proof) | `"approved"` | `true` | `false` | `"approved"` |
| diff mode, pre-fix (counter-proof) | `"approved"` | `true` | `false` | `"approved"` |

The pre-fix counter-proof runs reproduce the exact stale-write bug the
design targets. Diff mode shares `REVIEW_DATA`, `rState`, and
`approveSection` with review mode (`REVIEW_DATA.mode === 'diff'` gates only
rendering, not the keydown handler), so one fix covers both `/viva-diff`
and plain review mode. All test servers and the headless Chrome instance
were torn down afterward; the executor reported `git status` clean except
the fix itself.

## Run 2 — executor for commit `e22d73b` (final), 2026-08-04T01:00Z

Independently re-ran both sequences against the task's completed, final
commit:

1. Review mode (`http://127.0.0.1:61274`, ad hoc `--mode review` fixture):
   commented the "Goals" card → pressed `a` → refused (card stayed active,
   orange dot, no advance, `1 with feedback`) → removed the comment via its
   `×` → recap overlay read **PENDING** for s1, not approved.
2. Diff mode (`http://127.0.0.1:61296`, ad hoc `--mode diff` fixture): same
   sequence on the `src/foo.py hunk 1` card → refused identically → recap
   read **PENDING** for s1.

Also confirmed `approveSection` (server.py:2491-2498) contains the guard
(`if (activeComments(id).length) return;`) backing the legend's
"refused while it has comments" claim (later reworded, see below), and
that the `c`/`i` branches (server.py:3434-3435) remain untouched, still
calling `setReviewVerdict`.

## Disposition

Both required runs (review mode, diff mode) are independently confirmed
twice, against two different commits in this task's own history, including
negative-control (pre-fix counter-proof) runs proving the check isn't
vacuous. This closes gate-audit round 1's Critical finding.

**Post-audit addendum (commit `5f43093`, after this evidence was captured):**
the legend copy Run 2 cites above ("refused while it has comments") was
reworded to "refused while it has unsettled feedback" to precisely match
the `activeComments` guard predicate (`!c.settled && c.note`) — flagged
independently by frontend-reviewer and product-reviewer as overstating the
guard. The same commit added a Cmd/Ctrl/Alt modifier guard to the `a`-key
branch, matching the `o`-shortcut's own existing precedent (line 3425) —
flagged independently by security-auditor, accessibility-auditor, and
product-reviewer. Neither change alters the guard behavior these two runs
verified; both are re-covered by the existing regression test
(`tests/test_server_a11y.py::test_a_key_calls_approve_section`, updated in
the same commit to assert the modifier guard too), which passed 12/12
after the change, full suite 40/40 clean.
