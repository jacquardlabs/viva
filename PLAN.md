# Plan: Keyboard shortcut to approve the active card without a mouse

Spine: Task 1 -> Task 2 (Task 2 is a regression test verifying Task 1's change; no independent task).

### Task 1 — Route the `a` key to `approveSection` and correct its legend copy
Why now:    This is the design's whole behavior change (`docs/design/approve-shortcut.md`, Proposed design) — closes the latent auto-accept path named in Problem & persona. The legend copy correction is one paired edit with it, not a separate change: they land together or the UI's own documentation of itself goes stale.
Read first: `server.py:3419-3436`, `server.py:2491-2498`, `server.py:1683-1692`, `docs/design/approve-shortcut.md`
Rests on:   nothing — first task.
Do:         In the keydown handler's `a`-key branch (`server.py:3433`), replace `setReviewVerdict(rState.active, 'approved')` with `approveSection(rState.active)`. Update the `.kbd-legend`'s `a` row `<dd>` text (`server.py:1686`) from `approve section` to `approve section — refused while it has comments`.
Not here:   The `c`/`i` branches (`server.py:3434-3435`) and their legend rows (`server.py:1687-1688`) — tracked separately at issue #156, not this story. Don't delete `setReviewVerdict` — `c` and `i` still call it. No new toast/shake/sound feedback on refusal. Don't touch `closeCommentPopover`'s focus handling — pre-existing, unrelated.

Done means:
1. [cap]  The `a`-key branch of the keydown handler calls `approveSection(rState.active)`, not `setReviewVerdict(rState.active, 'approved')`.   (tier: script `server.py`)
2. [hold] The `c`/`i` branches still call `setReviewVerdict(rState.active, 'changes'|'info')`, unchanged.   (tier: script `server.py`)
Evidence: Manual browser check (no scripted-probe tool in this repo — stdlib-only per CLAUDE.md; `tests/test_server_a11y.py`'s own docstring defers aria-expanded toggle behavior the same way, and this story's design doc makes the same deferral explicit in Operational readiness). Two runs, both required:
  (1) review mode: comment a card -> press `a` -> refused (no dim, no advance) -> remove the comment via its `×` -> the recap overlay reads pending, not approved.
  (2) diff mode (`/viva-diff`): same sequence on a hunk card -> recap reads pending, not approved.
  Record both outcomes in the build report; a passing test suite alone (Task 2) is not sufficient evidence for this task — it only proves the source calls the right function, not that the guard actually blocks a stale write at runtime.

### Task 2 — Add a regression test asserting the `a`-key branch calls `approveSection`
Why now:    Closes the real test gap named in Operational readiness: nothing today asserts which function the `a` key calls, only that the legend text exists. This is the one behavior from this story that's mechanically checkable without a browser — the rest needs Task 1's manual check.
Read first: `tests/test_server_a11y.py`, `server.py:3433`
Rests on:   Task 1 (this test's assertion is only true once Task 1's swap lands).
Do:         Add `test_a_key_calls_approve_section()` to `tests/test_server_a11y.py`, asserting the literal substring `approveSection(rState.active)` appears in `HTML` immediately following the `e.key === 'a'` branch condition, and that `setReviewVerdict(rState.active, 'approved')` does not. Wire it into `main()`.
Not here:   No new test file — this extends the existing a11y suite, matching its established string-needle pattern. No assertion about `c`/`i`'s branches (unchanged, not this task's concern). No attempt to simulate runtime behavior (guard, timing) — string-level only, per Operational readiness's own framing of what this repo's harness can check.

Done means:
1. [cap]  `tests/test_server_a11y.py::test_a_key_calls_approve_section` exists and fails if the `a`-key branch calls `setReviewVerdict(` instead of `approveSection(`.   (tier: test-backed `tests/test_server_a11y.py`)
2. [hold] The suite's 11 pre-existing tests still pass unchanged.   (tier: test-backed `tests/test_server_a11y.py`)
Evidence: `python3 tests/test_server_a11y.py` output showing `OK (12 tests)`.

## Not-here follow-ups
- Fix `c`/`i`'s comment-less-verdict silent-drop bug — tracked at issue #156. Same root cause (`setReviewVerdict`, unguarded) as this story's own bug, but explicitly deferred per the design's Fork 2 ruling (recommended option A, ruled via q2).
- Add a labeled undo affordance for an in-round `a` approval, matching the `withdraw approval` control a round ≥2 carried approval already has — named as a real, disclosed asymmetry in the design's Fork 1, not fixed here (issue #140's acceptance criteria never asked for undo).

---

## Revision History

Signed off via viva review — 1 round, 4 sections, 0 revised. 2026-08-03
