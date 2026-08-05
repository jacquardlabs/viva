# Build plan — loop.py driver owns round numbering, liveness, and the round gate

Design: `docs/design/loop-driver.md` (signed off 2026-08-04, 9/9 sections, 4 review rounds).
Issues: #104, #102 (parts 1 and 3), #103 (parts 1 and 2), #125. Epic `reliability`, story `loop-driver`.

A walking-skeleton `scripts/loop.py` already exists on this branch (commit `57ccc47`) carrying
`start`, `arm`, `wait`, and `finish`, plus `schema.round_is_complete()`. It was built as design
evidence and its guards are verified; these tasks complete the surface around it rather than
starting from zero.

Spine: Task 1 → Task 3 → Task 4 (Task 2 is independent, runs any time). Task 1 freezes the
`/abandon` endpoint that Task 3's own `abandon` subcommand calls; Task 4 documents the sequence
Task 3 finishes. Task 2 is the round gate — highest-risk logic, no other task builds on it.

### Task 1 — Server teardown: POST /abandon and the SIGTERM handler
Why now:    Task 3's `abandon` subcommand has no way to reach the server until this endpoint exists — the design's own round-3 blocker.
Read first: `server.py`, `docs/design/loop-driver.md`, `tests/test_server_qa_complete_shutdown.py`
Rests on:   nothing — first task in the spine.
Do:         In `server.py`, add a `POST /abandon` branch beside `/complete` that runs `_check_origin_and_length`, replies `{"ok":true}`, and sets `_shutdown` without `/complete`'s sign-off semantics or its 2-second timer. Separately register `signal.SIGTERM` alongside the existing `SIGINT` handler so `proc.terminate()` reaches the same shutdown `finally`.
Not here:   `loop.py`'s own `abandon` subcommand (Task 3); any change to `/complete` (Task 2).

Done means:
1. [cap]  `POST /abandon` against a live review session returns 200, the process exits, and `.viva/server.url` is removed          (tier: test-backed `tests/test_server_qa_complete_shutdown.py`)
2. [cap]  `SIGTERM` to the server process runs the shutdown `finally` and removes `.viva/server.url`, asserted independently of the `SIGINT` path          (tier: test-backed `tests/test_server_qa_complete_shutdown.py`)
3. [hold] `POST /complete`'s existing behavior is unchanged — the standalone Q&A finish sequence still exits the process          (tier: test-backed `tests/test_server_qa_complete_shutdown.py`)
4. [hold] `/abandon` goes through `_check_origin_and_length` like every sibling POST, so a non-loopback `Origin` is rejected          (tier: test-backed `tests/test_server_origin_guard.py`)
Evidence: the four assertions above, run against a real subprocess-launched server per the file's existing harness pattern.

### Task 2 — The round gate on POST /complete
Why now:    "Nothing is auto-accepted" is enforced by the agent's restraint today; this is the check the code performs. Highest-risk logic in the plan — two exemptions, both load-bearing.
Read first: `server.py`, `scripts/schema.py`, `.claude/skills/viva-diff/SKILL.md`, `docs/design/loop-driver.md`
Rests on:   nothing — independent of the rest of the spine.
Do:         In `server.py`, snapshot the submitted verdicts under `_data_lock` at `/submit`, and gate `/complete` on `schema.round_is_complete()` when `_input_data` carries `sections` **and** its `mode` is not `"diff"`. Return two distinct 4xx bodies: one for no-verdicts-yet, one naming how many sections are not approved.
Not here:   `loop.py finish`'s own client-side check, which already exists; `POST /abandon` (Task 1).

Done means:
1. [cap]  A review session with any non-approved section gets 4xx from `POST /complete`, and an all-approved one still gets 200          (tier: test-backed `tests/test_server_qa_complete_shutdown.py`)
2. [cap]  A Q&A session, which carries `questions` and no `sections`, still completes — `schema.round_is_complete` is never consulted for it          (tier: test-backed `tests/test_server_qa_complete_shutdown.py`)
3. [cap]  A diff session whose verdicts hold `changes` still completes, matching `viva-diff`'s documented empty-re-diff finish          (tier: test-backed `tests/test_server_qa_complete_shutdown.py`)
4. [hold] `POST /complete` before any `/submit` returns a 4xx distinct from the not-approved one          (tier: test-backed `tests/test_server_qa_complete_shutdown.py`)
5. [hold] `schema.round_is_complete` stays pure — no disk reads, no `Path`/`os`/`json.load` usage in `scripts/schema.py`          (tier: test-backed `tests/test_schema.py`)
Evidence: each case driven against a real server subprocess; the diff case asserts a 200 with `changes` verdicts on record, which a shape-only guard would refuse.

### Task 3 — loop.py completes its surface: annotate, rearm, abandon
Why now:    The producer seam and the round-2+ path are the half of the loop the skeleton left out; without them SKILL.md still needs bash blocks that name round files.
Read first: `scripts/loop.py`, `scripts/annotate.py`, `.claude/skills/viva/SKILL.md`, `docs/design/loop-driver.md`
Rests on:   Task 1
Do:         Add three subcommands to `scripts/loop.py`: `annotate --sidecar <path>` merging through `annotate.py` against the derived round file; `rearm --response <cid>=<text> [--parse-only]` updating `open_notes.py` and re-parsing, arming unless `--parse-only`; and `abandon` POSTing `/abandon` and reporting the session unfinished.
Not here:   The SKILL.md rewrite (Task 4); any change to the existing `start`/`arm`/`wait`/`finish` behavior.

Done means:
1. [cap]  `loop.py annotate --sidecar` merges a sidecar into the current round's `review-input` with no round number passed by the caller          (tier: test-backed `tests/test_annotate.py`)
2. [cap]  `loop.py rearm --parse-only` re-parses and stops before arming; without the flag it re-parses and POSTs `/next-round`          (tier: test-backed `tests/test_server_orchestration.py`)
3. [cap]  `loop.py abandon` ends a live session and leaves no `.viva/server.url`, asserted independently of the `SIGINT` and `SIGTERM` paths          (tier: test-backed `tests/test_server_qa_complete_shutdown.py`)
4. [hold] Every subcommand derives its round from the highest `review-input-r{N}.json` on disk — none accepts a round argument          (tier: test-backed `tests/test_server_orchestration.py`)
5. [hold] `scripts/loop.py` imports no sibling but `schema`, per CLAUDE.md's one-cross-import rule          (tier: test-backed `tests/test_server_orchestration.py`)
Evidence: the round-2+ sequence driven end to end through `loop.py` rather than hand-written curl, replacing the bash sequence the file currently guards.

### Task 4 — Slim SKILL.md to judgment work, extract references/
Why now:    The 382-line skill is the defect's home; every earlier task exists so this rewrite has code to call instead of prose to execute.
Read first: `.claude/skills/viva/SKILL.md`, `scripts/loop.py`, `docs/design/loop-driver.md`, `PRODUCT.md`
Rests on:   Task 3
Do:         Rewrite `.claude/skills/viva/SKILL.md`'s Steps section as a sequence of `loop.py` calls, and move the opt-in feature documentation (annotations, producers, confidence, open notes, preferences) to `.claude/skills/viva/references/`. Keep the step-4 apply-standing-preferences directive inline, and drop the auto-approve edge case.
Not here:   `viva-qa`'s and `viva-diff`'s own SKILL.md files; `viva-diff`'s documented drift, which belongs to the `skill-prose-fixes` story.

Done means:
1. [cap]  `.claude/skills/viva/SKILL.md` contains zero bash blocks that launch a server, POST an endpoint, or name a `review-input-r` or `review-r` file          (tier: test-backed `tests/test_server_orchestration.py`)
2. [cap]  The rewritten skill's rewrite step still instructs applying standing preferences, sourced from `loop.py wait`'s printed output          (tier: test-backed `tests/test_server_orchestration.py`)
3. [cap]  The skill carries no "too short to review → auto-approved" rule, and its verdict table carries the paused-reviewer branch          (tier: test-backed `tests/test_server_orchestration.py`)
4. [hold] Every file under `.claude/skills/viva/references/` is reachable — each is named by a path `loop.py` prints, with no `$VIVA_DIR` interpolation on a references path          (tier: test-backed `tests/test_server_orchestration.py`)
Evidence: grep-shaped assertions over the rewritten skill plus the reachability check, all in the file that already guards SKILL.md's documented sequences against drift.

## Not-here follow-ups

- `viva-diff/SKILL.md`'s drift — the missing long-timeout line, the legacy `?output=` query param, and the `info` row pointing at thread machinery diff mode lacks — belongs to the `skill-prose-fixes` story, which depends on this one.
- Gating diff mode's finish requires `viva-diff/SKILL.md` to send an explicit resolved-empty signal on its empty-re-diff path; Task 2 exempts `mode: "diff"` rather than closing it here.
- `revision_history.py` reports "0 revised" for a session whose doc changed materially between sign-offs — the count is scoped to rounds within a session and cannot see a between-session rewrite. Surfaced by dogfooding this story's own design doc; worth its own issue.
- Extending the driver to `viva-qa`'s and `viva-diff`'s own loops is a later story, deliberately not a hidden half of this one.

---

## Revision History

Signed off via viva review — 1 round, 6 sections, 0 revised. 2026-08-04
