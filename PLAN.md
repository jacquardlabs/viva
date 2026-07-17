# PLAN — Frontend v2 phase 1: transmittal rounds and sheet ground

Derived from `docs/superpowers/specs/2026-07-16-frontend-v2-target-design.md` (Phase 1 only), on branch `feat/frontend-v2-phase1`; pre-mortem register at `docs/studious/premortems/2026-07-16-frontend-v2-target-design.md`. Load-bearing constraints: zero schema change (`scripts/schema.py` and both wire shapes untouched); a round-1 review renders byte-equivalent to today (no-op-when-absent); no spine in this phase (phase 2, with its navigation); Q&A mode gets the ground and nothing else; the transmittal slip is review-mode-only; all verification is `python3 tests/test_*.py` stdlib scripts (no probe tooling exists in this repo — live-UI fidelity is judged at viva sign-off and the acceptance dogfood, never self-attested).

Spine: Task 1 -> Task 2 -> Task 3; Task 4 and Task 5 run any time after Task 1; Task 6 runs last and rests on Tasks 1-5.

### Task 1 — Sheet ground replaces grid and sheet-frame [PASS]

Why now:    Foundation chrome every later page-ships assertion renders against; lowest risk; also creates this phase's shared test file so Tasks 2-6 have a lintable method.
Read first: `server.py:50-96`, `server.py:145-156`, `server.py:369-374`, `docs/superpowers/specs/2026-07-16-frontend-v2-target-design.md`, `tests/test_server_a11y.py`
Rests on:   —
Do:         Delete both 24px grid background blocks (dark body + light override); add `--table` (`#060e1a` dark / `#e2e8f1` light) to both token blocks; replace `.sheet-frame` (CSS at 369-374, div at 1357) with a content-bounded `#paper` wrapper around `main.shell` (edge `1px solid var(--border2)`, inner rule at `inset: 7px`, corner `+` marks, coordinate letters/numbers, all decoration `aria-hidden="true"`); mirror `.mode-diff .shell` widening with `.mode-diff #paper { max-width: min(95vw, 1600px) }`; update `tests/test_server_a11y.py` for the new chrome and create `tests/test_frontend_v2_phase1.py` (subprocess + urllib harness per `tests/_server_harness.py`) holding this phase's fixtures and its sheet-ground page-ships test.
Not here:   No spine (phase 2 ships beads and navigation together); no transmittal, recap, or between-rounds markup; no DESIGN.md rewrite (Task 6).

Done means:
1. [cap]  Served review page ships the `id="paper"` sheet — edge border, inner rule, `aria-hidden` coordinate/corner decoration — and `--table` in both theme token blocks (tier: test-backed `tests/test_server_a11y.py`)
2. [cap]  The grid is gone at every layer: served HTML contains zero `background-size: 24px 24px` and zero `sheet-frame` occurrences (tier: test-backed `tests/test_server_a11y.py`)
3. [hold] Diff mode still widens and renders: `.mode-diff #paper` carries `min(95vw, 1600px)` and the existing diff-render assertions pass unchanged (tier: test-backed `tests/test_server_diff_render.py`)

Evidence: verify-step transcripts of both named test files, captured by /build.

### Task 2 — Carried approvals collapse in place [PASS]

Why now:    Freezes the carried-card DOM and the submit-payload invariant that Task 3's jump targets and Task 4's recap labels build against; touches the highest-risk surface (verdict flow) while only one task deep.
Read first: `server.py:1637-1686`, `server.py:1847-1932`, `server.py:2047-2075`, `docs/studious/premortems/2026-07-16-frontend-v2-target-design.md`, `tests/_server_harness.py`
Rests on:   Task 1
Do:         Render `priorApprovedSet` members (round >= 2) as collapsed carried cards — dimmed head-only line, mono `APPROVED` mini-stamp, an `unchanged since your stamp — show` expand revealing content read-only, and a `× withdraw approval` control that clears the verdict back to pending so the card becomes a normal accordion card; add the round-2 fixture (3 carried, 1 revised-with-note, 1 revised-no-note, 2 flagged) and carried tests to `tests/test_frontend_v2_phase1.py`.
Not here:   No transmittal slip (Task 3); no changes to `deriveVerdict`, comment flow, or any `scripts/*.py`; no reordering — document order is canonical.

Done means:
1. [cap]  Round-2 fixture serves carried markup for each `approved_ids` member: `carried` head marker, `APPROVED` stamp text, `unchanged since your stamp` expand, and `withdraw approval` control (tier: test-backed `tests/test_frontend_v2_phase1.py`)
2. [cap]  `POST /submit` for the round-2 fixture records carried sections as `"verdict": "approved"` in the round output exactly as today's carry-forward does (tier: test-backed `tests/test_frontend_v2_phase1.py`)
3. [hold] Round-1 fixture (no `approved_ids`) serves zero `carried` markers and unchanged accordion card markup (tier: test-backed `tests/test_frontend_v2_phase1.py`)
4. [hold] Comment and anchored-note submit flow is untouched: existing comment-submit suite passes (tier: test-backed `tests/test_server_comments_submit.py`)

Evidence: verify-step transcripts, captured by /build.

### Task 3 — Transmittal slip on round >= 2, review mode only [PASS]

Why now:    The kernel of the phase (the decide gate named it); consumes Task 2's carried semantics for its ▣ row and jump behavior.
Read first: `server.py:1361-1389`, `server.py:1703-1793`, `server.py:1637-1686`, `tests/test_server_qa.py`
Rests on:   Task 2
Do:         Add a `transmittalHTML` builder (pure function over `REVIEW_DATA`) rendering between the ledger and `#review-cards` on `round > 1` in review mode only: `△ revised to your note` rows only for sections whose `diff` has an `open_notes` entry behind it, bare `△ revised` for diff-without-note, `⚑ flagged & unreviewed` from `annotations` (error before warn), `▣ approved & unchanged` from `approved_ids`; each row jump-links (carried targets scroll + expand rather than activate); extend `tests/test_frontend_v2_phase1.py` with slip assertions against the Task 2 fixture.
Not here:   No diff-mode slip (positional hunk identity — design doc open question 3); no new fields on any wire shape; no reordering of the card list.

Done means:
1. [cap]  The slip builder ships both attribution branches — `revised to your note` and bare `revised` — and `GET /input` carries `diff`, `open_notes`, `annotations`, and `approved_ids` intact for the round-2 fixture (tier: test-backed `tests/test_frontend_v2_phase1.py`)
2. [cap]  The builder guards `round > 1` and review mode: round-1 fixture HTML renders zero `transmittal` markers (tier: test-backed `tests/test_frontend_v2_phase1.py`)
3. [hold] Q&A mode ships no slip path and its suite passes unchanged (tier: test-backed `tests/test_server_qa.py`)

Evidence: verify-step transcripts, captured by /build.

### Task 4 — Recap overlay as the submit gate [PASS]

Why now:    Independent of the slip; rewires the shared submit button, so it lands before Task 6 documents the final submit flow; the pre-mortem's qa-guard failure mode (#3) is checked here.
Read first: `server.py:2356-2375`, `server.py:2537-2552`, `server.py:2677-2686`, `server.py:2821-2879`, `server.py:1437-1447`
Rests on:   Task 1
Do:         Add the recap overlay (index grid of every section: id, title, verdict dot + label, note count; click closes and activates) with `o` toggle and Escape close; when `btn-submit` is ready in review or diff mode its click opens the overlay and only the overlay's `confirm & submit` control calls `submitReview(false)`; `skip rest & submit` keeps calling `submitReview(true)` directly; Q&A keeps its direct `done →` path; add the `o` row to the kbd legend; extend `tests/test_frontend_v2_phase1.py`.
Not here:   No recap in Q&A mode; no change to verdict derivation or the submit payload; no removal of the early-submit escape hatch.

Done means:
1. [cap]  Served page ships the recap `overlay` container with a `confirm & submit` control and the kbd legend gains the `o` shortcut row (tier: test-backed `tests/test_frontend_v2_phase1.py`)
2. [cap]  The ready-submit path routes through the recap — only the overlay control invokes `submitReview(false)` — while `skip rest & submit` still invokes `submitReview(true)` directly (tier: test-backed `tests/test_frontend_v2_phase1.py`)
3. [hold] Q&A submit is untouched — the qa branch still calls `submitQA(false)` directly — and the qa→review hand-off suite passes (tier: test-backed `tests/test_server_qa_review_handoff.py`)

Evidence: verify-step transcripts, captured by /build.

### Task 5 — Between-rounds state replaces the spinner

Why now:    Independent of Tasks 2-4; interacts with the #119 soft-timeout machinery, so it carries its own regression holds; second half of the phase's kernel.
Read first: `server.py:1408-1413`, `server.py:2623-2642`, `server.py:2694-2741`, `tests/test_server_processing_timeout.py`
Rests on:   Task 1
Do:         Snapshot the just-submitted `changes`/`info` rows (`{sectionTitle, type, note}`) from `rState` inside `submitReview` before the POST; render `#processing-view` as the between-rounds card — pulsing dot, `REV 0N submitted — the agent is revising`, the rows verbatim — with zero rows (or a qa submit) falling back to the minimal processing line; a tab reload during revision re-boots into the prior round exactly as today (snapshot deliberately not persisted); keep `PROCESSING_STILL_WAITING_MS` and both #119 banners untouched; extend `tests/test_frontend_v2_phase1.py`.
Not here:   No persistence of the snapshot to `.viva/`; no SSE protocol change; no change to the qa→review hand-off sequence.

Done means:
1. [cap]  Served page ships the between-rounds card markup — `the agent is revising` heading and the verbatim request-row template fed from the `rState` snapshot in `submitReview` (tier: test-backed `tests/test_frontend_v2_phase1.py`)
2. [hold] Both #119 strings still ship — `Still waiting — check the terminal.` and `Connection lost — check the terminal.` — and the timeout suite passes (tier: test-backed `tests/test_server_processing_timeout.py`)
3. [hold] The qa→review hand-off suite passes with the minimal processing variant (tier: test-backed `tests/test_server_qa_review_handoff.py`)

Evidence: verify-step transcripts, captured by /build.

### Task 6 — DESIGN.md rewrite and draft-plan supersession

Why now:    Documents only shipped behavior, so it must run last; also retires the pre-jig draft plan so the branch carries exactly one plan artifact.
Read first: `DESIGN.md`, `docs/superpowers/plans/2026-07-16-frontend-v2-phase1.md`, `docs/superpowers/specs/2026-07-16-frontend-v2-target-design.md`
Rests on:   Task 1, Task 2, Task 3, Task 4, Task 5
Do:         Rewrite DESIGN.md's Metaphor and Layout sections for the sheet-on-table ground (exact values from the design doc's decision 1) and delete every grid-paper/24px-grid reference including the dead `.sheet-frame` documentation; add subsections for the transmittal slip (row grammar + attribution rule), recap overlay, between-rounds state, and carried approvals; delete `docs/superpowers/plans/2026-07-16-frontend-v2-phase1.md` (superseded by this `PLAN.md`); extend `tests/test_frontend_v2_phase1.py` with the docs-alignment assertions.
Not here:   No README rewrite and no `assets/screenshot.png` recapture (follow-ups below); no PRODUCT.md changes.

Done means:
1. [cap]  `DESIGN.md` documents the sheet ground (`--table`, inner rule, edge coordinates), the transmittal row grammar with its attribution rule, the recap gate, the between-rounds state, and carried approvals, with zero remaining `grid paper` references (tier: test-backed `tests/test_frontend_v2_phase1.py`)
2. [cap]  `docs/superpowers/plans/2026-07-16-frontend-v2-phase1.md` no longer exists on the branch — `PLAN.md` is the only live plan artifact (tier: test-backed `tests/test_frontend_v2_phase1.py`)
3. [hold] The docs-contract suite passes untouched (tier: test-backed `tests/test_headless_contract_doc.py`)

Evidence: verify-step transcripts, captured by /build.

## Not-here follow-ups

- Refresh `assets/screenshot.png` (README hero) on the new ground once phase 1 is merged — needs a live capture pass, rides with /finish or the acceptance gate.
- README feature-cluster documentation (PRODUCT.md's own known problem #1) — separate story; phase 1 only worsens the gap by one feature cluster.
- Diff-round transmittal slip — blocked on hunk carry-forward identity (design doc open question 3); revisit when hunk identity is stable across rounds.
- Recap-overlay friction check at the acceptance dogfood (pre-mortem item 7) — if it reads as a speed bump, demote to opt-in before merge.
- Phase 2 (redline surface) and phase 3 (lifecycle session) get their own /plan runs against the same design doc.
