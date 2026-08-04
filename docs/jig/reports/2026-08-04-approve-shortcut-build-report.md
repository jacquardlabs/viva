# Build report — Keyboard shortcut to approve the active card without a mouse

Source: issue #140. Branch `epic/reviewer-experience--approve-shortcut`, cut from `epic/reviewer-experience`. Gates: design-review `PROCEED TO PLAN` (`39e0f35`), audit `PASS` (`6aea04c`), acceptance `SHIP` (`2b96a47`).

## Evidence table

`PLAN.md` — Task 1: Route the `a` key to `approveSection`, correct its legend copy, and add the regression test proving it.

Evidence folder: `docs/jig/evidence/2026-08-04-task-1-build-plan-202608040028/` (resolved via `scripts/evidence-capture resolve`; freshness confirmed via `scripts/evidence-freshness`, both PASS).

| # | Done means | Tier | Status | Evidence |
|---|---|---|---|---|
| 1 | `tests/test_server_a11y.py::test_a_key_calls_approve_section` exists and fails if the `a`-key branch calls `setReviewVerdict(` instead of `approveSection(`. | test-backed `tests/test_server_a11y.py` | **PASS** | See detail below |
| 2 | The suite's other 11 tests still pass, and the `c`/`i` branches remain unchanged. | test-backed `tests/test_server_a11y.py` | **PASS** | See detail below |

<details>
<summary>Item 1 &amp; 2 — <code>tests/test_server_a11y.py</code> run (from <code>results.json</code>)</summary>

```
command: tests/test_server_a11y.py
exit code: 0
--- stdout ---
  ok  test_card_head_is_button_with_aria
  ok  test_aria_expanded_sync_helper_exists
  ok  test_main_landmark_wraps_shell
  ok  test_skip_link_targets_main
  ok  test_stats_aria_live_and_dynamic_title
  ok  test_tab_title_identifies_document
  ok  test_decorative_emoji_are_aria_hidden
  ok  test_focus_visible_group_and_button_types
  ok  test_keyboard_legend_present_and_real
  ok  test_a_key_calls_approve_section
  ok  test_sheet_ground_ships
  ok  test_grid_and_sheet_frame_gone
OK (12 tests)

--- stderr ---
```

This is the capture-time run (commit `e22d73b`), predating this session's own three post-audit/acceptance fix commits (`5f43093`, `2b96a47` final legend wording, plus the earlier `3e9c785` evidence-recording commit). The full 40-file suite was re-run after every subsequent commit in this session and stayed green throughout — see `report.md` in the same evidence folder and this build report's own commit history.
</details>

**Item requiring the manual (non-scripted) check**, per `PLAN.md`'s own `Evidence:` line — "a passing test suite alone is not sufficient evidence for this task": two manual browser runs (review mode, diff mode), transcribed from both build executors' own reports into `docs/jig/evidence/2026-08-04-task-1-build-plan-202608040028/report.md` (added during `/gate-audit` round 1 remediation, since it was originally narrated only in agent-return messages and never committed — see that file's own attribution and disposition sections for full detail, including the pre-fix negative-control runs).

## cctx footer

cctx is not installed in this environment — skipping the session-cost footer and harvest offer. Install with `pipx install cctx-cli` to enable this step on a future `/finish` run.

## Follow-ups

Both of `PLAN.md`'s `## Not-here follow-ups` bullets already carry tracked GitHub issues — filed/updated during this session's `/gate-acceptance` remediation, not fresh drafts from this step:

1. **`c`/`i`'s comment-less-verdict silent-drop bug** — [issue #156](https://github.com/jacquardlabs/viva/issues/156), pre-existing. Commented during this session ([comment](https://github.com/jacquardlabs/viva/issues/156#issuecomment-5173720005)) to explicitly scope in two gaps `/gate-audit` and `/gate-acceptance` surfaced on this branch: the `c`/`i` legend rows' copy asymmetry, and their missing Cmd/Ctrl/Alt modifier guard (present on `a` and `o`, absent on `c`/`i`) — both would otherwise go untracked if #156 closed on just the verdict-drop fix.
2. **In-round approve has no labeled undo** (design doc Fork 1, option A — human-ruled, not reopened) — filed this session as new: [issue #157](https://github.com/jacquardlabs/viva/issues/157).

**NOTES stubs**: 0 found. `/build`'s current implementation doesn't yet write these (no NOTES-stub step exists in `skills/build/SKILL.md`); this is the expected, honest result, not a gap.

## Proposed decision patches

Propose-only — nothing below has been applied. Copy in by hand if you want it.

**`DESIGN.md`** — "Multiple inline comments" (line 305) states the verdict-derivation rule but never states the corollary this story's legend copy now discloses to users: approval itself is refused while any comment is active, not just derived away at submit time.

```diff
 A section card hosts a list of typed comments rather than a single verdict pick. The
 section verdict is **derived** from its comments, never chosen directly: no active
 comments → approved/pending; any `changes` comment → changes; otherwise info.
+
+Corollary: approving (mouse click, or the `a` keyboard shortcut) is refused outright
+while the card has any active comment — `approveSection` no-ops rather than setting a
+verdict `deriveVerdict` would immediately override anyway. The `.kbd-legend` discloses
+this ("approve section (refused while it has open comments)"); this section is its
+source of truth.
 
 Design elements:
```

(A second candidate — documenting `.kbd-legend`'s own copy convention, parenthetical/conditional rather than em-dash, discovered the hard way across three rewrites this session — was considered and dropped: DESIGN.md documents no keyboard-shortcut surface at all today, so a convention-only note would sit in a section that doesn't exist yet. That's a larger, separate documentation gap, not a decision this one story's fix should silently create a home for.)

## Session remediation summary (context for the table above)

This branch's audit (`/gate-audit`) and acceptance (`/gate-acceptance`) rounds each surfaced findings that were fixed within the same session before recording their verdicts, rather than deferred to a FIX AND RE-REVIEW cycle:

- Missing manual-check evidence (Critical, product-reviewer) — closed by committing `report.md` into the evidence folder.
- Missing Cmd/Ctrl/Alt modifier guard on `a` (Important, security/accessibility/product-reviewer) — closed by adding the guard, matching the `o` shortcut's existing precedent.
- Legend copy overstating/mis-describing the guard — three iterations: "refused while it has comments" (imprecise) → "refused while it has unsettled feedback" (precise but pointed at a round-1-invisible control) → final: "refused while it has open comments" (matches PRODUCT.md's own "Open notes" term, correct in both rounds). Pinned with a literal-string test needle in the final commit.

Full commit list, `78d8bc5..a38deed`:

```
a38deed fix: correct task-1 evidence manifest's stale branch field
2b96a47 fix: use "open comments" in the a-key legend, matching round-1 reality
6aea04c fix: match the a-key legend row to its sibling conditional-copy pattern
3e9c785 docs: record task 1's manual browser-check evidence
5f43093 fix: guard the a-key shortcut against modifier combos, sharpen legend copy
fde055b status-flip: task 1 -> PASS
7b78ae4 docs: task 1 verification evidence
e22d73b test: add regression test proving the 'a' key routes through approveSection
640a944 fix: route the `a` shortcut through approveSection to close the auto-accept path
1fa91c7 docs: correct PLAN.md to a single verifiable task
3c19b1d docs: PLAN.md for approve-shortcut (viva-signed, plan-lint clean)
39e0f35 docs: pre-mortem register for approve-shortcut design review
```
