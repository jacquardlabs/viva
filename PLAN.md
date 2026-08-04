# Plan: Keyboard shortcut to approve the active card without a mouse

Spine: Task 1 only — a single task. The behavior change and its regression test land together, matching `task-execution-discipline`'s TDD-per-capability pillar: neither of this story's specific claims (which function the `a` key calls, what the new legend text reads) is checkable by the *existing* test suite, only by the test this task adds — so splitting implementation from its own proof across two tasks left the first task's `Done means` unable to check anything the second task hadn't already landed.

### Task 1 — Route the `a` key to `approveSection`, correct its legend copy, and add the regression test proving it [PASS]
Why now:    This is the design's whole behavior change (`docs/design/approve-shortcut.md`, Proposed design) — closes the latent auto-accept path named in Problem & persona. The legend copy correction is one paired edit with it, not a separate change: they land together or the UI's own documentation of itself goes stale. The regression test is what makes either claim mechanically checkable at all.
Read first: `server.py:3419-3436`, `server.py:2491-2498`, `server.py:1683-1692`, `tests/test_server_a11y.py`, `docs/design/approve-shortcut.md`
Rests on:   nothing — first task.
Do:         In the keydown handler's `a`-key branch (`server.py:3433`), replace `setReviewVerdict(rState.active, 'approved')` with `approveSection(rState.active)`. Update the `.kbd-legend`'s `a` row `<dd>` text (`server.py:1686`) from `approve section` to `approve section — refused while it has comments`. Add `test_a_key_calls_approve_section()` to `tests/test_server_a11y.py`, asserting the literal substring `approveSection(rState.active)` appears in `HTML` immediately following the `e.key === 'a'` branch condition, and that `setReviewVerdict(rState.active, 'approved')` does not; wire it into `main()`. `chmod +x tests/test_server_a11y.py` so this task's own `Done means` commands are directly runnable — this repo's test files aren't executable by convention except `scripts/revision_history.py` (the one existing precedent), and `CLAUDE.md`'s own baseline (`for f in tests/test_*.py; do python3 "$f"; done`) keeps working unchanged since it always invokes the interpreter explicitly; this is a permission-bit change only, no content or behavior change.
Not here:   The `c`/`i` branches (`server.py:3434-3435`) and their legend rows (`server.py:1687-1688`) — tracked separately at issue #156, not this story. Don't delete `setReviewVerdict` — `c` and `i` still call it. No new toast/shake/sound feedback on refusal. Don't touch `closeCommentPopover`'s focus handling — pre-existing, unrelated. No new test file — this extends the existing a11y suite, matching its established string-needle pattern. No attempt to simulate runtime behavior (guard, timing) — string-level only, per Operational readiness's own framing of what this repo's harness can check.

Done means:
1. [cap]  `tests/test_server_a11y.py::test_a_key_calls_approve_section` exists and fails if the `a`-key branch calls `setReviewVerdict(` instead of `approveSection(`.   (tier: test-backed `tests/test_server_a11y.py`)
2. [hold] The suite's other 11 tests still pass, and the `c`/`i` branches remain unchanged.   (tier: test-backed `tests/test_server_a11y.py`)
Evidence: `tests/test_server_a11y.py` output showing `OK (12 tests)`, plus the manual browser check (no scripted-probe tool in this repo — stdlib-only per CLAUDE.md; this file's own docstring already defers aria-expanded toggle behavior the same way, and this story's design doc makes the same deferral explicit in Operational readiness). Two runs, both required:
  (1) review mode: comment a card -> press `a` -> refused (no dim, no advance) -> remove the comment via its `×` -> the recap overlay reads pending, not approved.
  (2) diff mode (`/viva-diff`): same sequence on a hunk card -> recap reads pending, not approved.
  Record both outcomes in the build report; a passing test suite alone is not sufficient evidence for this task — it only proves the source calls the right function, not that the guard actually blocks a stale write at runtime.

## Not-here follow-ups
- Fix `c`/`i`'s comment-less-verdict silent-drop bug — tracked at issue #156. Same root cause (`setReviewVerdict`, unguarded) as this story's own bug, but explicitly deferred per the design's Fork 2 ruling (recommended option A, ruled via q2).
- Add a labeled undo affordance for an in-round `a` approval, matching the `withdraw approval` control a round ≥2 carried approval already has — named as a real, disclosed asymmetry in the design's Fork 1, not fixed here (issue #140's acceptance criteria never asked for undo).

---

## Revision History

Signed off via viva review — 1 round, 3 sections, 0 revised. 2026-08-03
