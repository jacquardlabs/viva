# Frontend v2 Phase 1 — Round Ergonomics + Sheet Ground Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the schema-compatible slice of the frontend v2 target (`docs/superpowers/specs/2026-07-16-frontend-v2-target-design.md`): the sheet-on-table ground replacing the 24px grid, carried approvals collapsed in place, the transmittal slip on round ≥ 2, the recap overlay as the submit gate, and a between-rounds state that keeps the reviewer's context. Zero schema change — every input already rides in `review-input-r{N}.json` (`section.diff`, `section.open_notes`, `section.annotations`, `approved_ids`) and in `rState` at submit time.

**Architecture:** Five independent moves inside `server.py`'s `HTML` constant, one docs pass. Ground (Task 1) is pure CSS plus one wrapper div, replacing the viewport `.sheet-frame` with a content-bounded `#paper`. Tasks 2–5 are display-layer JS: each derives its UI from fields the browser already receives and writes nothing new to the wire — `review-r{N}.json` stays byte-identical for identical reviewer actions. Task 6 lands the DESIGN.md rewrite and refreshes `assets/screenshot.png`.

**Tech Stack:** Vanilla JS/CSS embedded in `server.py`'s `HTML` constant (no build step, no npm). Python stdlib test harness (`tests/test_*.py`, subprocess + `urllib`, page-ships string assertions per `tests/test_server_ledger.py` precedent).

**Branch:** execution starts on `feat/frontend-v2-phase1` off current `main` (nothing from the exploration is committed yet; the design doc and `docs/superpowers/specs/frontend-v2/*.png` captures land in the first commit).

## Global Constraints

- Approved spec: `docs/superpowers/specs/2026-07-16-frontend-v2-target-design.md`. Exact values, do not deviate: table `#060e1a` dark / `#e2e8f1` light; sheet fill `var(--bg)`, edge `1px solid var(--border2)`, inner rule `1px solid var(--border)` at `inset: 7px`; edge coordinate letters/numbers in Fragment Mono 8px `var(--text3)`; corner `+` marks in `var(--accent)`; **no background grid at any layer** — the 24px grid deletes outright, including the light-mode override.
- Pixel reference: `docs/superpowers/specs/frontend-v2/*.png` (captured from the approved interactive demo). Where this plan and the captures disagree, the captures win.
- `scripts/schema.py`, every `scripts/*.py` producer, every endpoint, and both wire shapes (`ReviewInput`, verdict payload) are untouched. A submitted review must serialize byte-identical to today for the same reviewer actions.
- Q&A mode gets the ground (Task 1 is global chrome) and nothing else — no slip, no recap, no carried-collapse. Diff mode gets the ground and the recap overlay; the transmittal slip is **review-mode-only** this phase (hunk identity across rounds is positional — design doc open question 3).
- The spine minimap, margin verdict column, and continuous print are **phase 2** — do not start them here. The card accordion and its `overflow: hidden` animation stay untouched.
- Tests are plain `python3 tests/test_*.py` scripts with `main()` printing `OK`, CI matrix 3.8–3.13. Frontend behavior beyond served-HTML string assertions gets an honest human checkpoint: dogfood `/viva` on a real doc across two rounds before merge.
- New keyboard binding `o` (recap/index) must not collide: existing bindings are `a`/`c`/`i`, `Tab`, `1`–`9`, `⌘/Ctrl+Enter` (`server.py:2821-2879`); update the kbd legend (`server.py:1437-1447`).

---

### Task 1: Sheet ground — table, bounded sheet, coordinates, corner marks

**Files:**
- Modify: `server.py:50-67` and `:71-89` (token blocks — add `--table`)
- Modify: `server.py:91-96` and `:145-156` (delete both body grid `background-image` blocks; body gets `background: var(--table)`)
- Modify: `server.py:369-374` (replace `.sheet-frame` rules with `#paper` rules)
- Modify: `server.py:1357` (replace the `.sheet-frame` div with the `#paper` wrapper opening; close it after `</main>` at `:1449`)
- Modify: `server.py:764-767` (blueprint geometry group — membership unchanged, comment updated)
- Test: `tests/test_server_sheet_ground.py` (new)

**Interfaces:**
- Produces: `#paper` as the content-bounded sheet that Tasks 2–5 render inside; `--table` token.
- Consumes: nothing.

Current code at `server.py:1357`:

```html
<div class="sheet-frame" aria-hidden="true"><span class="sf-mark sf-tl">+</span><span class="sf-mark sf-tr">+</span><span class="sf-mark sf-bl">+</span><span class="sf-mark sf-br">+</span></div>
```

- [ ] **Step 1: Write the failing test** — `test_page_ships_sheet_ground`: GET `/` and assert the HTML contains `id="paper"` and `--table:`, and does **not** contain `background-size: 24px 24px` or `sheet-frame`.
- [ ] **Step 2: Tokens + table.** Add `--table: #060e1a` (dark block) / `--table: #e2e8f1` (light block). Body: `background: var(--table)`; delete the two grid `background-image`/`background-size` blocks (`:152-155` and the light override `:91-96`).
- [ ] **Step 3: The sheet.** Replace `.sheet-frame` CSS with `#paper`: `position: relative; max-width: 746px; margin: 22px auto 120px; background: var(--bg); border: 1px solid var(--border2); box-shadow: 0 14px 60px rgba(0,0,0,.30);` plus `::before` inner rule at `inset: 7px`. Add `.mode-diff #paper { max-width: min(95vw, 1600px) }` mirroring the existing `.mode-diff .shell` rule (`server.py:174`). Corner marks: four `.sfmark` spans (`+`, accent, positioned at the sheet corners); coordinates: letters A–F down both edges, numbers 1–8 across top and bottom, generated by a ~12-line IIFE into a `#coords` span (port verbatim from the demo). Whole decoration cluster `aria-hidden="true"`.
- [ ] **Step 4: Wrap the shell.** `#paper` wraps `<main class="shell">` (skip-link and bottom bar stay outside). Verify the `@media (max-width: 720px)` behavior that previously hid `.sheet-frame` becomes: sheet loses side borders/shadow below 720px (full-bleed), decorations hidden.
- [ ] **Step 5: Run the test; eyeball all three modes** (`--mode review|diff|qa` with fixture inputs) against `frontend-v2/spec-gate.png` / `diff-gate.png` / `qa-gate.png` for ground fidelity (table, sheet edge, inner rule, coords, marks).

### Task 2: Carried approvals collapse in place

**Files:**
- Modify: `server.py:386-387` (`.card.is-approved` styling → carried variant)
- Modify: `server.py:1637-1686` (`initReview` — carried rendering branch for `priorApprovedSet` members)
- Modify: `server.py:1847-1932` (`buildReviewCard` — carried head variant: dimmed one-liner, `APPROVED` stamp badge, "unchanged since your stamp — show" expand, "× withdraw approval")
- Test: `tests/test_server_sheet_ground.py` (extend: `test_page_ships_carried_collapse` asserting the marker strings)

**Interfaces:**
- Produces: carried-card DOM that Task 3's slip jump-links target.
- Consumes: `REVIEW_DATA.approved_ids` (already populated by `initReview`, `server.py:1640-1644`).

- [ ] **Step 1: Failing test** — served HTML contains `carried` marker class and `withdraw approval` string.
- [ ] **Step 2: Render.** A section in `priorApprovedSet` on round ≥ 2 renders head-only: dimmed (reuse the existing 0.42→0.72 hover ramp), mini `APPROVED` stamp (mono, teal, 1.5px double rule, −3° rotate — port `.ministamp` from the demo), expand toggle revealing the content read-only, and withdraw.
- [ ] **Step 3: Withdraw semantics.** Withdraw clears the verdict in `rState` (section returns to pending, card becomes a normal accordion card, stats/submit update via existing `updateReviewStats`). Confirm the submit payload for withdraw-then-approve is byte-identical to today's approve.
- [ ] **Step 4: Round-1 no-op.** With empty `approved_ids`, rendering is byte-equivalent to today (guard in the branch, not in CSS).

### Task 3: Transmittal slip (review mode, round ≥ 2)

**Files:**
- Modify: `server.py:1361-1389` (review view — slip container between the ledger and `#review-cards`)
- Modify: `server.py:1637-1686` (`initReview` — call the slip builder)
- Test: `tests/test_server_transmittal_inputs.py` (new)

**Interfaces:**
- Produces: `transmittalHTML(sections, approved_ids, round)` — a pure function over `REVIEW_DATA`.
- Consumes: `section.diff` (revised), `section.open_notes` (threads), `section.annotations` (flags), `approved_ids` (carried), Task 2's card ids for jumps.

- [ ] **Step 1: Failing test** — launch the server with a fixture review-input carrying all four fields; assert `GET /input` returns them intact (the slip's input contract) and `GET /` ships the `transmittal` markup marker.
- [ ] **Step 2: Derive rows.** `△ N revised to your note` only for sections with `diff` **and** a reviewer note behind it (quote the latest note); sections with `diff` but no note render a neutral `△ N revised` — never assert attribution the data doesn't carry · `⁇ N thread answered` (sections with `open_notes`, excluding already-counted revised) · `⚑ N flagged & unreviewed` (sections with `annotations`, not carried, not revised — order error > warn) · `▣ N approved & unchanged` (carried). Empty rows drop; all rows empty → no slip. Round 1 → no slip, unconditionally.
- [ ] **Step 3: Header + jumps.** `TRANSMITTAL · REV 0{N-1} → REV 0{N}` / "what changed while you were away". Each row jump-activates its first section via the existing `activateReviewCard`; the carried row scrolls to the first carried card.
- [ ] **Step 4: Match `frontend-v2/spec-gate.png`** for row grammar, glyph colors (`--orange`/`--amber`/`--teal`), and the corner registration marks on the slip.

### Task 4: Recap overlay as the submit gate

**Files:**
- Modify: `server.py:2356-2375` (`updateReviewStats` — submit-ready path opens the overlay instead of arming direct submit) and the QA equivalent `:2537-2552`
- Modify: `server.py:2677-2686` (submit/skip listeners), `server.py:2821-2879` (keyboard — add `o`, Escape), `server.py:1437-1447` (kbd legend)
- Modify: `server.py` review/diff views (overlay markup before `<script>`)
- Test: `tests/test_server_sheet_ground.py` (extend: overlay markers, `confirm & submit` string)

**Interfaces:**
- Produces: `openOverlay()`/`closeOverlay()` reading `rState`/`REVIEW_DATA`.
- Consumes: `deriveVerdict`, `activeComments` (existing, unchanged).

- [ ] **Step 1: Failing test** — served HTML ships the overlay container and `confirm & submit`.
- [ ] **Step 2: Overlay.** Grid of every item: id label (`S-0N` / `filepath @@…`), title, verdict dot + label, note count. Click a card → close + `activateReviewCard`. Title: "Index" when items remain, "All reviewed — recap before submitting" when the queue is clear. `o` toggles anytime; Escape closes; focus returns to the invoking control.
- [ ] **Step 3: Gate the submit.** `btn-submit` ready → click opens the overlay; only the overlay's `confirm & submit` calls `submitReview(false)`. **`skip rest & submit` stays a direct escape hatch** (`submitReview(true)`, no overlay) — per the assembly decision. Q&A keeps today's direct `done →` (no recap in qa, per Global Constraints).
- [ ] **Step 4: Match `frontend-v2/recap-overlay.png`.**

### Task 5: Between-rounds state

**Files:**
- Modify: `server.py:1408-1413` (`#processing-view` markup → between-rounds card)
- Modify: `server.py:2623-2642` (`submitReview` — snapshot the submitted feedback before POST), `server.py:2735-2741` (SSE `processing` handler renders the snapshot)
- Test: `tests/test_server_sheet_ground.py` (extend: between-rounds markers; assert the #119 strings `Still waiting — check the terminal.` and `Connection lost — check the terminal.` still ship)

**Interfaces:**
- Produces: a between-rounds view listing the just-submitted `changes`/`info` notes verbatim.
- Consumes: `rState.verdicts` at submit time; the existing `processing` SSE event (unchanged on the wire).

Current code at `server.py:1408-1413`:

```html
  <!-- ── Processing state ─────────────────────────────────── -->
  <div id="processing-view" style="display:none">
    <div class="processing-inner">
      <div class="spinner"></div>
      <div class="processing-text">Claude is revising…</div>
    </div>
  </div>
```

- [ ] **Step 1: Failing test** — between-rounds marker ships; both #119 banner strings still present.
- [ ] **Step 2: Snapshot.** `submitReview` stores `{sectionTitle, type, note}` rows from `rState` before the POST (submitted-early flag included). The `processing` handler renders: pulsing amber dot, `REV 0N submitted — the agent is revising`, the rows verbatim, and the reassurance line — replacing the bare spinner. Zero rows (all-approved submit) → keep a minimal processing line; the `round`/`complete` events clear it exactly as today. A tab reload during revision re-boots into the prior round's view exactly as today — the snapshot is deliberately not persisted (`.viva/` state lifecycle unchanged).
- [ ] **Step 3: #119 interplay.** `PROCESSING_STILL_WAITING_MS` timer and both banners are untouched — the soft-timeout banner overlays the between-rounds card the same way it overlaid the spinner. The qa→review hand-off (#109) path renders the minimal variant (a qa submit has no `changes` rows).
- [ ] **Step 4: Match `frontend-v2/between-rounds.png`.**

### Task 6: DESIGN.md rewrite, screenshot refresh, dogfood gate

**Files:**
- Modify: `DESIGN.md` (Metaphor; Layout; Blueprint elements — `.sheet-frame` superseded by the sheet; new sections: Transmittal slip, Recap overlay, Between rounds, Carried approvals; delete the grid-paper language and 24px token references)
- Modify: `assets/screenshot.png` (recapture on the new ground)
- Test: full suite `for f in tests/test_*.py; do python3 "$f"; done`

- [ ] **Step 1: DESIGN.md.** Reword Metaphor to "sheet on table"; document the exact ground spec (Global Constraints values), the slip row grammar, the recap gate, the between-rounds state, and carried-collapse. Note the slip is review-mode-only with the diff-round rationale (positional hunk identity).
- [ ] **Step 2: Screenshot.** Recapture `assets/screenshot.png` on a round-2 review showing slip + carried + sheet (README's image).
- [ ] **Step 3: Human checkpoint (required, honest).** Dogfood a two-round `/viva` review of a real doc: round 1 approve-some/flag-some, verify the between-rounds state, verify round 2's slip counts match what was flagged, withdraw one carried approval, submit through the recap. Then run one `/viva-diff` and one `/viva-qa` session to confirm no-op surfaces (ground only).
- [ ] **Step 4: Full test suite green across the local Python; PR titled `feat: frontend v2 phase 1 — transmittal rounds and sheet ground`.**
