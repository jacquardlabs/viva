# Build report — Preferences inspector: view and manage learned preferences from the browser UI

Source: issue #142. Branch `epic/reviewer-experience--prefs-inspector`, cut from `epic/reviewer-experience`. Gates: design-review `PROCEED TO PLAN` (`5607035`), audit `PASS` (`5ff801d`), acceptance `SHIP` (`b2ba340`).

## Evidence table

**No `PLAN.md`-based Done-means table exists for this story.** Unlike `approve-shortcut` (built via `/work-on` → `/plan` → `/build`, with `scripts/evidence-capture` writing a dated evidence folder per task), this story was built via `/work-through`'s epic-driver orchestration — phase assignments (`design`, `build`) dispatched directly against the epic's own work file, never through a checkpoint-block `PLAN.md`. The `PLAN.md` present in this worktree at merge time is a stale, unrelated leftover from an earlier, different story (`frontend-v2-phase1`, last touched at `a934c87`) — not this story's own plan, and removed below along with the rest of the branch-local scaffolding. No evidence folder under `docs/jig/evidence/` belongs to this story either; `scripts/evidence-capture list` for this branch returns only that same unrelated story's six legacy folders.

This isn't a gap to backfill (`/finish` doesn't invent or re-capture evidence it never had) — it's an honest structural fact about which build route this story took. What actually verified this story's acceptance criteria:

- **`/gate-audit`**: PASS, all applicable lanes, at `5ff801d`.
- **`/gate-acceptance`**: two rounds. Round 1 (legacy-recorded, pre-episode-tracking) found three SHOULD FIX issues — a non-runnable recovery command, a wrong store path in that command, and stale "next session"/"empty store" claims in `DESIGN.md`/`server.py` — addressed by commits `72a6830`, `7bb0f53`, `1ffb9dc`. A fresh round (this session, since no episode existed to re-enter) independently re-verified all three fixes directly against source — script path resolution, store path resolution, `aria-live` scoping — before either dispatched reviewer returned, and both confirmed clean. That round found two further SHOULD FIX issues (unquoted store path in the recovery command; auto-accept-reading legend copy) plus one MINOR (misleading toggle label), all fixed and committed this session (`b2ba340`).
- **Test suite, this story's own coverage**: `tests/test_server_preferences.py` — a real HTTP integration test (spins up a server, exercises `GET /preferences` and `POST /preferences/mute`, verifies mutation against `.viva/preferences.json` on disk, not just the HTTP response; covers the missing/corrupt-store degrade path and the shared loopback-Origin/body-size POST guards). `tests/test_server_a11y.py` carries 10 prefs-specific tests (toggle semantics, dialog/focus-trap parity with the recap overlay, mutual exclusivity with the recap, aria-live scoping, the recovery command's runnability and quoting, mute-button gating by status, badge-jump rendering). Both pass; full 41-file suite clean as of `b2ba340`.

<details>
<summary><code>tests/test_server_preferences.py</code> — full output</summary>

```
viva · review mode · http://127.0.0.1:50009
OK
```
</details>

<details>
<summary><code>tests/test_server_a11y.py</code> — full output (22 tests, 10 prefs-specific)</summary>

```
  ok  test_card_head_is_button_with_aria
  ok  test_aria_expanded_sync_helper_exists
  ok  test_main_landmark_wraps_shell
  ok  test_skip_link_targets_main
  ok  test_stats_aria_live_and_dynamic_title
  ok  test_tab_title_identifies_document
  ok  test_decorative_emoji_are_aria_hidden
  ok  test_focus_visible_group_and_button_types
  ok  test_keyboard_legend_present_and_real
  ok  test_sheet_ground_ships
  ok  test_grid_and_sheet_frame_gone
  ok  test_prefs_toggle_is_native_button_static_label
  ok  test_prefs_toggle_gated_on_empty_store
  ok  test_prefs_overlay_is_dialog_mirrors_recap
  ok  test_prefs_and_recap_are_mutually_exclusive
  ok  test_prefs_panel_swallows_card_shortcuts_while_open
  ok  test_prefs_panel_closes_on_sse_view_swaps
  ok  test_prefs_status_is_the_only_live_region_in_the_panel
  ok  test_muted_row_names_the_unmute_recovery_and_this_round_effect
  ok  test_mute_button_only_on_standing_rows
  ok  test_prefs_data_fetched_once_and_cached_for_round_rebuilds
  ok  test_preference_badge_reuses_annot_jump_never_the_raw_id
OK (22 tests)
```
</details>

**No `probe`-tier (manual/runtime-only) evidence gap on this story** — every acceptance criterion (route shape, on-disk mutation, degrade behavior, aria-live scoping, recovery-command runnability) is mechanically checkable and is checked by the two files above; nothing here needed a manual browser check the way `approve-shortcut`'s keyboard-timing criteria did.

## cctx footer

cctx is not installed in this environment — skipping the session-cost footer and harvest offer. Install with `pipx install cctx-cli` to enable this step on a future `/finish` run.

## Follow-ups

No `PLAN.md` `## Not-here follow-ups` section exists (see above — no `PLAN.md` for this story). The design doc's own **Open questions** section names three deferred items, functionally the same kind of survivor this step exists to catch — presented as drafts, held for your per-item confirmation before any are filed:

1. **Un-mute requires the terminal.** Decision prefs-inspector-1 keeps un-mute CLI-only; the design doc names this as cutting against the story's own "without leaving the tab" premise, unresolved.
2. **Should a `candidate` preference be mutable from the UI too?** The route itself doesn't restrict by status — only the client does. Left as a follow-up.
3. **Should the panel link back to the section(s) that flagged a given preference** (the reverse of the badge-to-entry link this story builds)? Not asked for by the acceptance criteria; flagged as a possible small follow-up.

*(Draft issues not yet written — confirm which, if any, you want filed, and I'll draft title+body for each before creating anything.)*

**NOTES stubs**: 0 found — same expected result as `approve-shortcut`; `/build`'s current implementation doesn't write these regardless of which orchestration route dispatched it.

## Proposed decision patches

None. The one decision that outlives this feature — `scripts/preferences.py` no longer being the sole writer of `.viva/preferences.json` — is already documented in the shipped code itself (`scripts/preferences.py:9-14`'s updated module docstring) and in `DESIGN.md`'s own "Preferences panel" section (added by this story, already committed). Nothing remains to propose as a patch.

## Session remediation summary

- Round-1 acceptance findings (recovery command non-runnable, wrong store path, stale next-session/empty-store claims) — fixed prior to this session (`72a6830`, `7bb0f53`, `1ffb9dc`), independently re-verified against source this session before either fresh reviewer returned.
- Round-2 (this session, fresh episode) findings — unquoted recovery store path, auto-accept-reading legend copy, misleading toggle label — fixed this session (`b2ba340`).

Full commit list, `78d8bc5..b2ba340`:

```
b2ba340 fix: quote the mute-recovery store path; drop auto-accept-reading legend copy
1ffb9dc fix: resolve mute-recovery store-path and add status decoder to preferences panel
7bb0f53 fix: make the mute-recovery command runnable and correct its effect copy
72a6830 fix: keep card verdict shortcuts inert behind the preferences panel
5ff801d docs: correct the preferences-panel focus-ring and row-contents claims
ebd7b2c feat: preferences inspector — view and mute learned preferences from the UI
5607035 docs: pre-mortem register for the preferences inspector (#142)
551139a docs: design the preferences inspector (#142)
```
