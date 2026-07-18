# Build report — frontend v2 phase 1 (transmittal rounds and sheet ground)

**Story:** frontend-v2-phase1 · **Branch:** `build/plan-202607172214` (→ PR to `main`)
**Gate trail:** should-we-build **BUILD** → design-review **PROCEED TO PLAN** → audit **PASS** (3 rounds) → acceptance **SHIP**
**Built:** 6 tasks, 19 Done-means items, all `PASS`, all `test-backed`; evidence fresh (6/6 folders).

Phase 1 of the frontend v2 target (`docs/superpowers/specs/2026-07-16-frontend-v2-target-design.md`): the sheet-on-table ground replacing the 24px grid, carried approvals collapsed in place, the transmittal slip on round ≥ 2, the recap overlay submit gate, and the between-rounds card. Zero schema change; the whole feature is the embedded HTML/CSS/JS constant in `server.py`.

## Evidence table

### Task 1 — Sheet ground replaces grid and sheet-frame  ·  evidence: `docs/jig/evidence/2026-07-17-1/`

| # | Done means | Method | Pass |
|---|---|---|---|
| 1 | [cap] Served review page ships the `id="paper"` sheet — edge border, inner rule, `aria-hidden` coordinate/corner decoration… | test-backed | PASS |
| 2 | [cap] The grid is gone at every layer: served HTML contains zero `background-size: 24px 24px` and zero `sheet-frame` occurr… | test-backed | PASS |
| 3 | [hold] Diff mode still widens and renders: `.mode-diff #paper` carries `min(95vw, 1600px)` and the existing diff-render asse… | test-backed | PASS |

<details><summary>item 1 evidence (PASS)</summary>

```
command: python3 tests/test_server_a11y.py
exit code: 0
  ok  test_keyboard_legend_present_and_real
  ok  test_sheet_ground_ships
  ok  test_grid_and_sheet_frame_gone
OK (11 tests)
```
</details>

<details><summary>item 2 evidence (PASS)</summary>

```
command: python3 tests/test_server_a11y.py
exit code: 0
  ok  test_keyboard_legend_present_and_real
  ok  test_sheet_ground_ships
  ok  test_grid_and_sheet_frame_gone
OK (11 tests)
```
</details>

<details><summary>item 3 evidence (PASS)</summary>

```
command: python3 tests/test_server_diff_render.py
exit code: 0
test_page_ships_mode_diff_layout: OK
test_page_ships_diff2html_renderer: OK
test_page_ships_d2h_guards: OK
All server diff-render tests passed.
```
</details>

### Task 2 — Carried approvals collapse in place  ·  evidence: `docs/jig/evidence/2026-07-17-2/`

| # | Done means | Method | Pass |
|---|---|---|---|
| 1 | [cap] Round-2 fixture serves carried markup for each `approved_ids` member: `carried` head marker, `APPROVED` stamp text, `… | test-backed | PASS |
| 2 | [cap] `POST /submit` for the round-2 fixture records carried sections as `"verdict": "approved"` in the round output exactl… | test-backed | PASS |
| 3 | [hold] Round-1 fixture (no `approved_ids`) serves zero `carried` markers and unchanged accordion card markup | test-backed | PASS |
| 4 | [hold] Comment and anchored-note submit flow is untouched: existing comment-submit suite passes | test-backed | PASS |

<details><summary>item 1 evidence (PASS)</summary>

```
command: python3 tests/test_frontend_v2_phase1.py
exit code: 0
test_round1_zero_carried_markers: OK
test_round2_serves_carried_markup: OK
test_round2_submit_records_carried_approved: OK
OK (6 tests)
```
</details>

<details><summary>item 2 evidence (PASS)</summary>

```
command: python3 tests/test_frontend_v2_phase1.py
exit code: 0
test_round1_zero_carried_markers: OK
test_round2_serves_carried_markup: OK
test_round2_submit_records_carried_approved: OK
OK (6 tests)
```
</details>

<details><summary>item 3 evidence (PASS)</summary>

```
command: python3 tests/test_frontend_v2_phase1.py
exit code: 0
test_round1_zero_carried_markers: OK
test_round2_serves_carried_markup: OK
test_round2_submit_records_carried_approved: OK
OK (6 tests)
```
</details>

<details><summary>item 4 evidence (PASS)</summary>

```
command: python3 tests/test_server_comments_submit.py
exit code: 0
viva · review mode · http://127.0.0.1:52090
viva · review mode · http://127.0.0.1:52093
OK
  ok  test_comment_images_survive_submit
```
</details>

### Task 3 — Transmittal slip on round >= 2, review mode only  ·  evidence: `docs/jig/evidence/2026-07-17-3/`

| # | Done means | Method | Pass |
|---|---|---|---|
| 1 | [cap] The slip builder ships both attribution branches — `revised to your note` and bare `revised` — and `GET /input` carri… | test-backed | PASS |
| 2 | [cap] The builder guards `round > 1` and review mode: round-1 fixture HTML renders zero `transmittal` markers | test-backed | PASS |
| 3 | [hold] Q&A mode ships no slip path and its suite passes unchanged | test-backed | PASS |

<details><summary>item 1 evidence (PASS)</summary>

```
command: python3 tests/test_frontend_v2_phase1.py
exit code: 0
test_round2_serves_carried_markup: OK
test_round2_submit_records_carried_approved: OK
test_round2_serves_transmittal_slip: OK
OK (8 tests)
```
</details>

<details><summary>item 2 evidence (PASS)</summary>

```
command: python3 tests/test_frontend_v2_phase1.py
exit code: 0
test_round2_serves_carried_markup: OK
test_round2_submit_records_carried_approved: OK
test_round2_serves_transmittal_slip: OK
OK (8 tests)
```
</details>

<details><summary>item 3 evidence (PASS)</summary>

```
command: python3 tests/test_server_qa.py
exit code: 0
viva · qa mode · http://127.0.0.1:53052
OK
```
</details>

### Task 4 — Recap overlay as the submit gate  ·  evidence: `docs/jig/evidence/2026-07-17-4/`

| # | Done means | Method | Pass |
|---|---|---|---|
| 1 | [cap] Served page ships the recap `overlay` container with a `confirm & submit` control and the kbd legend gains the `o` sh… | test-backed | PASS |
| 2 | [cap] The ready-submit path routes through the recap — only the overlay control invokes `submitReview(false)` — while `skip… | test-backed | PASS |
| 3 | [hold] Q&A submit is untouched — the qa branch still calls `submitQA(false)` directly — and the qa→review hand-off suite pas… | test-backed | PASS |

<details><summary>item 1 evidence (PASS)</summary>

```
command: python3 tests/test_frontend_v2_phase1.py
exit code: 0
test_round2_serves_carried_markup: OK
test_round2_submit_records_carried_approved: OK
test_round2_serves_transmittal_slip: OK
OK (10 tests)
```
</details>

<details><summary>item 2 evidence (PASS)</summary>

```
command: python3 tests/test_frontend_v2_phase1.py
exit code: 0
test_round2_serves_carried_markup: OK
test_round2_submit_records_carried_approved: OK
test_round2_serves_transmittal_slip: OK
OK (10 tests)
```
</details>

<details><summary>item 3 evidence (PASS)</summary>

```
command: python3 tests/test_server_qa_review_handoff.py
exit code: 0
  ok  test_qa_keydown_branch_guarded_by_review_data
  ok  test_handoff_same_server_no_second_launch
  ok  test_standalone_qa_has_no_handoff_line
OK (6 tests)
```
</details>

### Task 5 — Between-rounds state replaces the spinner  ·  evidence: `docs/jig/evidence/2026-07-17-5/`

| # | Done means | Method | Pass |
|---|---|---|---|
| 1 | [cap] Served page ships the between-rounds card markup — `the agent is revising` heading and the verbatim request-row templ… | test-backed | PASS |
| 2 | [hold] Both #119 strings still ship — `Still waiting — check the terminal.` and `Connection lost — check the terminal.` — an… | test-backed | PASS |
| 3 | [hold] The qa→review hand-off suite passes with the minimal processing variant | test-backed | PASS |

<details><summary>item 1 evidence (PASS)</summary>

```
command: python3 tests/test_frontend_v2_phase1.py
exit code: 0
test_round2_serves_carried_markup: OK
test_round2_submit_records_carried_approved: OK
test_round2_serves_transmittal_slip: OK
OK (12 tests)
```
</details>

<details><summary>item 2 evidence (PASS)</summary>

```
command: python3 tests/test_server_processing_timeout.py
exit code: 0
  ok  test_onerror_escalates_over_still_waiting_banner
  ok  test_banner_info_css_uses_violet_not_orange
  ok  test_sse_client_has_no_duplicate_timer_helpers
OK (8 tests)
```
</details>

<details><summary>item 3 evidence (PASS)</summary>

```
command: python3 tests/test_server_qa_review_handoff.py
exit code: 0
  ok  test_qa_keydown_branch_guarded_by_review_data
  ok  test_handoff_same_server_no_second_launch
  ok  test_standalone_qa_has_no_handoff_line
OK (6 tests)
```
</details>

### Task 6 — DESIGN.md rewrite and draft-plan supersession  ·  evidence: `docs/jig/evidence/2026-07-17-6/`

| # | Done means | Method | Pass |
|---|---|---|---|
| 1 | [cap] `DESIGN.md` documents the sheet ground (`--table`, inner rule, edge coordinates), the transmittal row grammar with it… | test-backed | PASS |
| 2 | [cap] `docs/superpowers/plans/2026-07-16-frontend-v2-phase1.md` no longer exists on the branch — `PLAN.md` is the only live… | test-backed | PASS |
| 3 | [hold] The docs-contract suite passes untouched | test-backed | PASS |

<details><summary>item 1 evidence (PASS)</summary>

```
command: python3 tests/test_frontend_v2_phase1.py
exit code: 0
test_round2_serves_carried_markup: OK
test_round2_submit_records_carried_approved: OK
test_round2_serves_transmittal_slip: OK
OK (14 tests)
```
</details>

<details><summary>item 2 evidence (PASS)</summary>

```
command: python3 tests/test_frontend_v2_phase1.py
exit code: 0
test_round2_serves_carried_markup: OK
test_round2_submit_records_carried_approved: OK
test_round2_serves_transmittal_slip: OK
OK (14 tests)
```
</details>

<details><summary>item 3 evidence (PASS)</summary>

```
command: python3 tests/test_headless_contract_doc.py
exit code: 0
  ok  test_mode_choices_match_argparse
  ok  test_submit_size_cap_matches_doc
  ok  test_exit_code_2_is_argparse_usage_error
OK (5 tests)
```
</details>
## Session-cost footer (cctx)

Run via `uvx --from cctx-cli cctx autopsy` (no install). Verdict: 56 findings · **~$66.85 attributed waste (10%)** — UNUSED CONTEXT + EXPLORATION THRASH + TOOL THRASH + STALE CONTEXT + FANOUT WASTE. Session cost ~$696.04 (incl. 41 subagents ~$134.34). Largest single line: a 6K-token read carried stale ~772 turns (~$47.69). Fan-out waste is dominated by two extra full `/gate-audit` rounds the branch/worktree split forced (couldn't narrow). Harvest patches previewed (`--dry-run`) and **not applied** — they are behavioral (better suited to global `~/.claude/CLAUDE.md`), not viva project guidance.

## Follow-ups filed

Not-here follow-ups (from PLAN.md):
- #146 — docs: refresh README hero screenshot for the sheet-on-table ground
- #147 — docs: document shipped feature clusters in README
- #148 — feat: diff-round transmittal slip (blocked on hunk carry-forward identity)
- #149 — chore: dogfood recap friction + carried-collapse rubber-stamping (pre-mortem items 5 & 7)
- #150 — feat: frontend v2 phases 2 (redline surface) and 3 (lifecycle session)

Gate-surfaced Track items:
- #151 — fix: transmittal slip pads with per-section carried rows at scale (count header)
- #152 — fix: /submit appends to ledger before write_output — failed-then-retried submit duplicates rows (durable duplicate-ledger fix)
- #153 — docs: clear residual spinner/grid drift + unfiled UX polish (tap targets, jump affordance, .pr-title overflow)

## Decision patches

None. Phase 1's lasting design decisions (sheet-on-table ground, `--scrim` token, transmittal row grammar, carried-collapse, recap gate) already landed in `DESIGN.md` via Task 6 and commit `edd89c6`. The one unresolved question (transmittal per-section rows vs count summary — DESIGN.md and the design doc disagree) is filed as #151, not a settled decision to record.

## Scaffolding cleanup note

`PLAN.md` (phase-1 build scaffolding) is removed in the cleanup commit — its Done-means table + evidence live in this report and the PR body. The design doc (`docs/superpowers/specs/2026-07-16-frontend-v2-target-design.md`), the pre-mortem register, and `docs/jig/evidence/` are **kept**: unlike a per-feature design doc, this one is the shared multi-phase target that phases 2–3 (#150) build against — durable, not disposable.
