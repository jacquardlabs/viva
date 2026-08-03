#!/usr/bin/env python3
"""Static a11y assertions on the embedded SPA (server.HTML).

Cluster A accessibility pass: card headers are native <button>s with
aria-expanded/aria-controls (#74), a <main> landmark wraps the shell (#37),
stats announce via aria-live and the title is set per mode/round (#35),
decorative emoji are aria-hidden (#38), the focus-visible group covers the new
controls (#52), action-btns carry type="button" (#51), and a keyboard legend
ships (#39). These are string-needle checks against the HTML constant; the
aria-expanded *toggle* behavior is verified manually in a browser.

Frontend v2 phase 1 adds the sheet-ground chrome checks: the review sits on a
bounded #paper sheet (edge border, inner rule, aria-hidden coordinate/corner
decoration) over a flat --table ground, and the 24px grid + fixed .sheet-frame
are gone at every layer.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import server  # noqa: E402
from _server_harness import assert_grid_gone, assert_sheet_ground  # noqa: E402

HTML = server.HTML


def test_card_head_is_button_with_aria():
    # Review and Q&A headers are both native buttons wired to their body region.
    assert HTML.count('class="card-head" aria-expanded="false" aria-controls=') == 2, \
        "both card headers must be <button aria-expanded aria-controls>"
    assert '<button type="button" class="card-head"' in HTML
    assert 'aria-controls="rbody-${section.id}"' in HTML
    assert 'aria-controls="qbody-${q.id}"' in HTML
    assert 'id="rbody-${section.id}"' in HTML and 'id="qbody-${q.id}"' in HTML
    # No legacy <div class="card-head"> remains.
    assert '<div class="card-head"' not in HTML, "card-head must not be a div"
    print("  ok  test_card_head_is_button_with_aria")


def test_aria_expanded_sync_helper_exists():
    # A single helper keeps aria-expanded in lockstep with is-active.
    assert "function setCardExpanded(cardEl, expanded)" in HTML
    assert "head.setAttribute('aria-expanded'" in HTML
    # No raw card is-active mutation should bypass the helper.
    assert "card.classList.add('is-active')" not in HTML, \
        "card is-active mutations must route through setCardExpanded"
    print("  ok  test_aria_expanded_sync_helper_exists")


def test_main_landmark_wraps_shell():
    assert '<main class="shell"' in HTML and "</main>" in HTML
    assert '<div class="shell">' not in HTML
    print("  ok  test_main_landmark_wraps_shell")


def test_skip_link_targets_main():
    # A skip link is the first focusable element and jumps to the <main> (#37).
    # Matched by attributes (class + href), not the exact tag string, so the
    # inert-helper's id= addition doesn't break this check.
    assert 'class="skip-link"' in HTML and 'href="#main-content"' in HTML
    assert 'id="main-content"' in HTML
    # It precedes the main content in source order (so it's the first Tab stop).
    assert HTML.index('class="skip-link"') < HTML.index('id="main-content"')
    print("  ok  test_skip_link_targets_main")


def test_stats_aria_live_and_dynamic_title():
    assert 'id="stats-area" aria-live="polite"' in HTML
    print("  ok  test_stats_aria_live_and_dynamic_title")


def test_tab_title_identifies_document():
    # Tab titles lead with the doc/topic name (basename, not full path) so
    # concurrent viva sessions are distinguishable in the tab bar; 'viva' is
    # a fixed trailing suffix. All four title-setting sites (the shared
    # review/diff boot tail, qa init, SSE round, SSE complete) route through
    # one shared helper so a future site can't drift back to a hardcoded,
    # doc-blind title. (Review and diff init share one call site inside
    # bootReviewMode() rather than each carrying their own.)
    assert "function tabDocName(path)" in HTML
    assert "function setTabTitle(...parts)" in HTML
    # No call site may hardcode the old doc-blind title strings.
    assert "document.title = 'viva · review · REV '" not in HTML
    assert "document.title = 'viva · diff · REV '" not in HTML
    assert "document.title = 'viva · brainstorm'" not in HTML
    assert "document.title = 'viva · ' + modeWord" not in HTML
    # Exactly one definition + four call sites (shared review/diff boot tail,
    # qa init, SSE round, SSE complete).
    assert HTML.count("setTabTitle(") == 5, \
        "expected setTabTitle def + 4 call sites (bootReviewMode, qa init, SSE round, SSE complete)"
    assert "setTabTitle(tabDocName(data.doc_file), ...(modeWord === 'diff' ? ['diff'] : []), 'REV ' + String(data.round).padStart(2, '0'));" in HTML
    assert "setTabTitle(data.context || 'brainstorm');" in HTML
    assert "setTabTitle(tabDocName(data.doc_file), ...(data.mode === 'diff' ? ['diff', rev] : [rev]));" in HTML
    assert "setTabTitle(REVIEW_DATA ? tabDocName(REVIEW_DATA.doc_file) : null, 'done');" in HTML
    print("  ok  test_tab_title_identifies_document")


def test_decorative_emoji_are_aria_hidden():
    # Every leading button glyph is wrapped; spot-check a representative set and
    # confirm no bare entity sits directly against a button open tag.
    for needle in ('<span aria-hidden="true">&#10003;</span>',   # approve / confirm / settle
                   '<span aria-hidden="true">&#8595;</span>',     # skip
                   '<span aria-hidden="true">&#128206;</span>',   # attach
                   '<span aria-hidden="true">&#9662;</span>'):    # diff toggle
        assert needle in HTML, f"missing aria-hidden wrap: {needle}"
    print("  ok  test_decorative_emoji_are_aria_hidden")


def test_focus_visible_group_and_button_types():
    assert ".card-head:focus-visible" in HTML
    assert ".settle-btn:focus-visible" in HTML and ".diff-toggle:focus-visible" in HTML
    assert '<button type="button" class="action-btn is-approve"' in HTML
    print("  ok  test_focus_visible_group_and_button_types")


def test_keyboard_legend_present_and_real():
    assert 'class="kbd-legend"' in HTML
    # Legend documents the actual handler keys, not generic placeholders.
    for needle in ("<kbd>a</kbd>", "<kbd>c</kbd>", "<kbd>i</kbd>",
                   "<kbd>Tab</kbd>", "<kbd>Enter</kbd>"):
        assert needle in HTML, f"legend missing real shortcut: {needle}"
    print("  ok  test_keyboard_legend_present_and_real")


def test_sheet_ground_ships():
    # The review sits on a bounded drawing sheet (#paper) over a flat table.
    # The needle set is shared with test_frontend_v2_phase1 via
    # assert_sheet_ground (one owner for the sheet-chrome contract), checked
    # here against the HTML constant.
    assert_sheet_ground(HTML)
    print("  ok  test_sheet_ground_ships")


def test_grid_and_sheet_frame_gone():
    # The 24px drafting grid and the fixed .sheet-frame (CSS + markup +
    # .sf-mark corners) are gone at every layer — shared negative check.
    assert_grid_gone(HTML)
    print("  ok  test_grid_and_sheet_frame_gone")


def test_prefs_toggle_is_native_button_static_label():
    # #142's bottom-bar control: a native button inside the aria-live
    # #stats-area, with a static label — no interpolated count baked into
    # its own text (pre-mortem lane 4: that would double-announce on every
    # counter update).
    assert '<button type="button" class="prefs-toggle" id="prefs-toggle">preferences</button>' in HTML
    stats_open = HTML.index('id="stats-area"')
    stats_close = HTML.index('</div>', stats_open)
    assert 'id="prefs-toggle"' in HTML[stats_open:stats_close], \
        "prefs-toggle must live inside #stats-area (decision prefs-inspector-2)"
    print("  ok  test_prefs_toggle_is_native_button_static_label")


def test_prefs_overlay_is_dialog_mirrors_recap():
    # role=dialog/aria-modal, ships hidden, same close affordances as the
    # recap overlay (Escape/backdrop/close button all wired through
    # setBackgroundInert — checked structurally below).
    assert ('<div class="prefs-overlay" id="prefs-overlay" role="dialog" '
            'aria-modal="true" aria-labelledby="prefs-title" style="display:none">') in HTML
    assert '<button type="button" class="prefs-close" id="prefs-close" aria-label="Close preferences">' in HTML
    assert "function openPrefsPanel(triggerEl, focusPrefId)" in HTML
    assert "function closePrefsPanel()" in HTML
    assert "setBackgroundInert(true)" in HTML and "setBackgroundInert(false)" in HTML
    print("  ok  test_prefs_overlay_is_dialog_mirrors_recap")


def test_prefs_and_recap_are_mutually_exclusive():
    # Opening either overlay closes the other first — at most one modal.
    assert "if (prefsIsOpen()) closePrefsPanel();" in HTML
    assert "if (recapIsOpen()) closeRecap();" in HTML
    print("  ok  test_prefs_and_recap_are_mutually_exclusive")


def test_prefs_status_is_the_only_live_region_in_the_panel():
    # Pre-mortem lane 3: the whole list must never be the live region — a
    # freshly opened panel with several rows would announce every row's text
    # on open, not just the one status change after a mute. Only the
    # dedicated one-line #prefs-status may carry aria-live; #prefs-list must
    # not.
    assert 'id="prefs-status" aria-live="polite"' in HTML
    assert 'id="prefs-list" aria-live' not in HTML, \
        "#prefs-list must not itself be an aria-live region"
    print("  ok  test_prefs_status_is_the_only_live_region_in_the_panel")


def test_muted_row_names_the_unmute_recovery_and_next_session_effect():
    # Pre-mortem lanes 5 and 6: mute is one-way from the UI (decision
    # prefs-inspector-1) with no confirmation step, so a muted row must
    # carry static copy naming both the recovery command and that the
    # effect is next-session, not retroactive to the card already on screen.
    assert "takes effect next session" in HTML
    assert "preferences.py set --store" in HTML and "--status standing</code>" in HTML
    assert "function prefMutedNoteHTML(id)" in HTML
    print("  ok  test_muted_row_names_the_unmute_recovery_and_next_session_effect")


def test_mute_button_only_on_standing_rows():
    # candidate/muted rows render read-only; only a standing row grows the
    # mute control (design: pre-flight never reads candidates, and a
    # criterion can't verify an invisible effect there). Anchored to the
    # actual gating expression, not just any "=== 'standing'" occurrence in
    # the file (prefStatusLabel's own ternary would also match a weaker check).
    assert "const muteBtn = status === 'standing'" in HTML
    assert 'class="pref-mute-btn" data-id="' in HTML
    print("  ok  test_mute_button_only_on_standing_rows")


def test_prefs_data_fetched_once_and_cached_for_round_rebuilds():
    # Pre-mortem lane 1: badges must survive a round-2+ SSE rebuild, which
    # never re-fetches /input's own data. The fix is caching, not a
    # per-render fetch — PREFS_DATA/PREFS_BY_ID are populated once in the
    # boot Promise.all and read (never reassigned) by annotStripHTML/
    # initReview afterward.
    assert "Promise.all([" in HTML
    assert "fetch('/preferences')" in HTML
    assert HTML.count("fetch('/preferences')") == 1, \
        "preferences must be fetched exactly once, at boot — never per-render"
    assert "PREFS_BY_ID = new Map(PREFS_DATA.map(p => [p.id, p]));" in HTML
    # Negative check that actually guards the pre-mortem's named failure: the
    # SSE 'round' handler (the round-2+ rebuild path) must never reassign
    # PREFS_DATA/PREFS_BY_ID — if it did, a stale/failed refetch there would
    # silently blank every badge on the very rebuild this lane is about.
    round_start = HTML.index("es.addEventListener('round'")
    round_end = HTML.index("es.addEventListener('complete'")
    assert round_start < round_end, "could not locate the SSE round handler body"
    assert "PREFS_" not in HTML[round_start:round_end], \
        "the SSE round handler must not touch PREFS_DATA/PREFS_BY_ID — cached at boot, reused as-is"
    print("  ok  test_prefs_data_fetched_once_and_cached_for_round_rebuilds")


def test_preference_badge_reuses_annot_jump_never_the_raw_id():
    # The badge-to-entry link renders label/id straight from the matched
    # preference object, never the raw regex-captured substring from the
    # annotation message.
    assert "const pref = m ? PREFS_BY_ID.get(m[1]) : null;" in HTML
    assert "esc(pref.id)" in HTML and "esc(pref.label || pref.id)" in HTML
    print("  ok  test_preference_badge_reuses_annot_jump_never_the_raw_id")


def main():
    test_card_head_is_button_with_aria()
    test_aria_expanded_sync_helper_exists()
    test_main_landmark_wraps_shell()
    test_skip_link_targets_main()
    test_stats_aria_live_and_dynamic_title()
    test_tab_title_identifies_document()
    test_decorative_emoji_are_aria_hidden()
    test_focus_visible_group_and_button_types()
    test_keyboard_legend_present_and_real()
    test_sheet_ground_ships()
    test_grid_and_sheet_frame_gone()
    test_prefs_toggle_is_native_button_static_label()
    test_prefs_overlay_is_dialog_mirrors_recap()
    test_prefs_and_recap_are_mutually_exclusive()
    test_prefs_status_is_the_only_live_region_in_the_panel()
    test_muted_row_names_the_unmute_recovery_and_next_session_effect()
    test_mute_button_only_on_standing_rows()
    test_prefs_data_fetched_once_and_cached_for_round_rebuilds()
    test_preference_badge_reuses_annot_jump_never_the_raw_id()
    print("OK (19 tests)")


if __name__ == "__main__":
    main()
