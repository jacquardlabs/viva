#!/usr/bin/env python3
"""Static a11y assertions on the embedded SPA (server.HTML).

Covers card-header buttons and aria-expanded/controls (#74), the <main>
landmark (#37), aria-live stats/title (#35), aria-hidden emoji (#38),
focus-visible/type="button" coverage (#52, #51), the keyboard legend (#39),
and the frontend-v2 sheet-ground chrome. String-needle checks only; toggle
behavior is verified manually in a browser.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import server  # noqa: E402
from _server_harness import (  # noqa: E402
    assert_catalog_ground, assert_grid_gone, assert_ink_discipline)

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
    # The live region is the counters alone — the toggles beside them rewrite
    # their own labels, and inside the region every repaint re-announced them.
    assert 'id="stat-run" aria-live="polite"' in HTML
    assert 'id="stats-area" aria-live' not in HTML, "the toggles must sit outside the live region"
    print("  ok  test_stats_aria_live_and_dynamic_title")


def test_tab_title_identifies_document():
    # Tab titles lead with the doc/topic basename so concurrent viva sessions
    # are distinguishable (#172). All four title sites route through one
    # shared helper so none can drift back to a hardcoded, doc-blind title.
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
    # Spot-check that leading button glyphs are aria-hidden-wrapped; attach
    # and dictate lost their emoji outright, so those are asserted absent.
    for needle in ('<span aria-hidden="true">&#10003;</span>',   # approve / confirm / settle
                   '<span aria-hidden="true">&#8595;</span>',     # skip
                   '<span aria-hidden="true">&#9662;</span>'):    # diff toggle
        assert needle in HTML, f"missing aria-hidden wrap: {needle}"
    for gone in ('&#128206;', '&#127908;', '&#9889;'):
        assert gone not in HTML, f"emoji glyph must not ship: {gone}"
    print("  ok  test_decorative_emoji_are_aria_hidden")


def test_focus_visible_group_and_button_types():
    assert ".card-head:focus-visible" in HTML
    assert ".settle-btn:focus-visible" in HTML and ".diff-toggle:focus-visible" in HTML
    # The per-section action row is gone from both surfaces; approve is a
    # margin verb, and it is still a real <button type="button">.
    assert '<button type="button" class="nt-btn is-pri" id=' in HTML
    print("  ok  test_focus_visible_group_and_button_types")


def test_keyboard_legend_present_and_real():
    assert 'class="kbd-legend"' in HTML
    # Legend documents the actual handler keys, not generic placeholders.
    for needle in ("<kbd>a</kbd>", "<kbd>c</kbd>", "<kbd>i</kbd>",
                   "<kbd>Tab</kbd>", "<kbd>Enter</kbd>"):
        assert needle in HTML, f"legend missing real shortcut: {needle}"
    # Pin the 'a' row's exact copy — it has drifted twice already, so nothing
    # else asserts this string.
    assert "<dd>approve section (refused while it has open comments)</dd>" in HTML, \
        "the 'a' row's legend copy must read 'refused while it has open comments'"
    print("  ok  test_keyboard_legend_present_and_real")


def test_the_counts_define_themselves_in_reach():
    """Finding 08: the bar/footer counts (items, convergence, approved,
    checks) never said what they counted. `title` is hover-only and not
    screen-reader announced, so the definitions ride in the real, keyboard-
    reachable `kbd-legend` `<details>` instead."""
    legend = HTML[HTML.index('<details class="kbd-legend">'):]
    legend = legend[:legend.index("</details>")]
    assert "what the counts mean" in legend, \
        "the disclosure must say it holds the vocabulary, not only the keys"
    for term in ("<dt>item</dt>", "<dt>open</dt>", "<dt>convergence</dt>",
                 "<dt>approved</dt>", "<dt>checks</dt>"):
        assert term in legend, f"an aggregate that never defines itself: {term}"
    # The producer-flag exclusion is the one part of the vocabulary a reader
    # cannot guess, and it is what finding 09 changed. State it to them, not
    # only in DESIGN.md.
    item = legend[legend.index("<dt>item</dt>"):]
    assert "not an item" in item[:item.index("</dd>")], \
        "the `item` definition must say a producer flag is not one"
    print("  ok  test_the_counts_define_themselves_in_reach")


def test_a_key_calls_approve_section():
    # The 'a' shortcut must route through approveSection (refuses while open
    # comments remain), not the old direct setReviewVerdict(..., 'approved')
    # auto-accept, and must guard Cmd/Ctrl/Alt so Cmd+A isn't hijacked.
    idx = HTML.index("e.key === 'a'")
    branch = HTML[idx:idx + 140]  # ends before the 'c' branch begins
    assert "approveSection(rState.active)" in branch, \
        "the 'a'-key branch must call approveSection(rState.active)"
    assert "!e.metaKey && !e.ctrlKey && !e.altKey" in branch, \
        "the 'a'-key branch must be guarded against Cmd/Ctrl/Alt modifiers"
    assert "setReviewVerdict(rState.active, 'approved')" not in HTML, \
        "the auto-accept path via setReviewVerdict(..., 'approved') must not remain"
    print("  ok  test_a_key_calls_approve_section")


def test_catalog_ground_ships():
    # Catalog page: light primary, four party inks, 72ch measure, no sheet
    # chrome. Needles shared with test_frontend_v2_phase1 via assert_catalog_ground.
    assert_catalog_ground(HTML)
    assert_ink_discipline(HTML)
    print("  ok  test_catalog_ground_ships")


def test_grid_and_sheet_frame_gone():
    # The 24px drafting grid and the fixed .sheet-frame (CSS + markup +
    # .sf-mark corners) are gone at every layer — shared negative check.
    assert_grid_gone(HTML)
    print("  ok  test_grid_and_sheet_frame_gone")


def test_prefs_toggle_is_native_button_static_label():
    # #142: a native button inside the aria-live #stats-area with a static
    # label — no interpolated count (would double-announce on updates).
    # Ships display:none; see test_prefs_toggle_gated_on_empty_store.
    assert ('<button type="button" class="prefs-toggle" id="prefs-toggle" '
            'style="display:none">learned prefs</button>') in HTML
    stats_open = HTML.index('id="stats-area"')
    stats_close = HTML.index('</div>', stats_open)
    assert 'id="prefs-toggle"' in HTML[stats_open:stats_close], \
        "prefs-toggle must live inside #stats-area (decision prefs-inspector-2)"
    print("  ok  test_prefs_toggle_is_native_button_static_label")


def test_prefs_toggle_gated_on_empty_store():
    # Fix for prefs-toggle-shown-with-empty-store: a clone with no store has
    # nothing to inspect/mute, so the toggle ships hidden and only the boot
    # handler reveals it, after PREFS_DATA is assigned from the response.
    assert "el('prefs-toggle').style.display = PREFS_DATA.length ? '' : 'none';" in HTML
    boot_start = HTML.index("Promise.all([")
    assign_at = HTML.index("PREFS_DATA  = Array.isArray(prefs)", boot_start)
    gate_at = HTML.index("el('prefs-toggle').style.display", boot_start)
    assert boot_start < assign_at < gate_at, \
        "prefs-toggle visibility must be gated after PREFS_DATA is assigned in the boot handler"
    print("  ok  test_prefs_toggle_gated_on_empty_store")


def test_prefs_overlay_is_dialog_mirrors_recap():
    # role=dialog/aria-modal, ships hidden, same close affordances as the
    # recap overlay (Escape/backdrop/close button all wired through
    # setBackgroundInert — checked structurally below).
    assert ('<div class="prefs-overlay" id="prefs-overlay" role="dialog" '
            'aria-modal="true" aria-labelledby="prefs-title" style="display:none">') in HTML
    assert '<button type="button" class="prefs-close" id="prefs-close" aria-label="Close preferences">' in HTML
    # The 9px keycap itself isn't a click target, so padding lives on the
    # BUTTON: hit area clears 24px (measured 48x24) while the cap stays sized
    # like every other keycap.
    assert re.search(r'\.recap-close, \.prefs-close \{[^}]*padding:\s*6px 8px', HTML), \
        "a dialog's close control must be a real click target, not a bare keycap"
    assert "function openPrefsPanel(triggerEl, focusPrefId)" in HTML
    assert "function closePrefsPanel()" in HTML
    assert "setBackgroundInert(true)" in HTML and "setBackgroundInert(false)" in HTML
    print("  ok  test_prefs_overlay_is_dialog_mirrors_recap")


def test_prefs_and_recap_are_mutually_exclusive():
    # Opening either overlay closes the other first — at most one modal.
    assert "if (prefsIsOpen()) closePrefsPanel();" in HTML
    assert "if (recapIsOpen()) closeRecap();" in HTML
    print("  ok  test_prefs_and_recap_are_mutually_exclusive")


def test_prefs_panel_swallows_card_shortcuts_while_open():
    # Fix for prefs-panel-open-verdict-shortcuts-live: inert on #paper blocks
    # pointer/Tab but not this keydown listener, and focus inside the panel
    # doesn't hit the tag-based TEXTAREA/INPUT guard. Fix is an unconditional
    # `return` gated on prefsIsOpen(), ahead of the review/QA branches but
    # after the Escape case.
    kd = HTML.index("document.addEventListener('keydown'")
    esc_idx = HTML.index(
        "if (e.key === 'Escape' && prefsIsOpen()) { closePrefsPanel(); return; }", kd)
    guard_idx = HTML.index("if (prefsIsOpen()) return;", kd)
    review_branch = HTML.index("if (REVIEW_DATA) {", kd)
    qa_branch = HTML.index("if (!REVIEW_DATA && QA_DATA && qState.active)", kd)
    assert kd < esc_idx < guard_idx < review_branch < qa_branch, \
        ("prefsIsOpen()'s Escape-close and blanket return must both sit ahead "
         "of the review and QA keydown branches")
    print("  ok  test_prefs_panel_swallows_card_shortcuts_while_open")


def test_prefs_panel_closes_on_sse_view_swaps():
    # Fix for prefs-panel-survives-round-swap: an SSE view swap while the
    # panel is open would render behind an open modal. Mirrors closeRecap()'s
    # per-handler treatment.
    proc_start = HTML.index("es.addEventListener('processing'")
    round_start = HTML.index("es.addEventListener('round'", proc_start)
    complete_start = HTML.index("es.addEventListener('complete'", round_start)
    onerror_start = HTML.index("es.onerror = ", complete_start)
    assert proc_start < round_start < complete_start < onerror_start
    assert "closePrefsPanel();" in HTML[proc_start:round_start], \
        "'processing' handler must close the prefs panel"
    assert "closePrefsPanel();" in HTML[round_start:complete_start], \
        "'round' handler must close the prefs panel"
    assert "closePrefsPanel();" in HTML[complete_start:onerror_start], \
        "'complete' handler must close the prefs panel"
    print("  ok  test_prefs_panel_closes_on_sse_view_swaps")


def test_prefs_status_is_the_only_live_region_in_the_panel():
    # The whole list must never be the live region — it would announce every
    # row's text on open. Only #prefs-status carries aria-live.
    assert 'id="prefs-status" aria-live="polite"' in HTML
    assert 'id="prefs-list" aria-live' not in HTML, \
        "#prefs-list must not itself be an aria-live region"
    print("  ok  test_prefs_status_is_the_only_live_region_in_the_panel")


def test_muted_row_names_the_unmute_recovery_and_this_round_effect():
    # Mute is one-way (decision prefs-inspector-1), so a muted row must name
    # the recovery command and state that already-shown badges stay as a
    # record, not "next session" — a mute mid-round can still reach that
    # round's own rewrite (SKILL.md's three --status standing readers).
    assert "takes effect next session" not in HTML
    assert "stay as a record" in HTML
    # "$VIVA_DIR" is a local bash var SKILL.md never exports, so a
    # copy-pasted "$VIVA_DIR/..." command 404s. Assert against server.py's
    # own resolved _PREFS_SCRIPT_PATH rather than a hardcoded literal.
    assert "$VIVA_DIR" not in HTML, "no shell-variable path may appear in the shipped recovery command"
    expected_script_path = str(ROOT / "scripts" / "preferences.py")
    assert f'python3 "{expected_script_path}" set' in HTML
    assert Path(expected_script_path).is_file(), \
        "the path embedded in the recovery command must name a real file, not just match a string"
    # Fix for prefs-recovery-store-path-unquoted: unquoted store path breaks
    # the copy-pasted command on any project path with a space.
    assert '--store "__PREFS_STORE_PATH__"' in HTML and "--status standing</code>" in HTML
    assert "function prefMutedNoteHTML(id)" in HTML
    print("  ok  test_muted_row_names_the_unmute_recovery_and_this_round_effect")


def test_mute_button_only_on_standing_rows():
    # candidate/muted rows render read-only; only a standing row grows the
    # mute control. Anchored to the actual gating expression, not any
    # "=== 'standing'" occurrence (prefStatusLabel's ternary would also match).
    assert "const muteBtn = status === 'standing'" in HTML
    assert 'class="pref-mute-btn" data-id="' in HTML
    print("  ok  test_mute_button_only_on_standing_rows")


def test_prefs_data_fetched_once_and_cached_for_round_rebuilds():
    # Badges must survive a round-2+ SSE rebuild without re-fetching:
    # PREFS_DATA/PREFS_BY_ID are populated once in the boot Promise.all and
    # only ever read afterward.
    assert "Promise.all([" in HTML
    assert "fetch('/preferences')" in HTML
    assert HTML.count("fetch('/preferences')") == 1, \
        "preferences must be fetched exactly once, at boot — never per-render"
    assert "PREFS_BY_ID = new Map(PREFS_DATA.map(p => [p.id, p]));" in HTML
    # The SSE 'round' handler must never reassign PREFS_DATA/PREFS_BY_ID —
    # a stale/failed refetch there would silently blank every badge.
    round_start = HTML.index("es.addEventListener('round'")
    round_end = HTML.index("es.addEventListener('complete'")
    assert round_start < round_end, "could not locate the SSE round handler body"
    assert "PREFS_" not in HTML[round_start:round_end], \
        "the SSE round handler must not touch PREFS_DATA/PREFS_BY_ID — cached at boot, reused as-is"
    print("  ok  test_prefs_data_fetched_once_and_cached_for_round_rebuilds")


def test_dead_session_overlay_is_the_one_modal_that_does_not_close():
    # #174. Unlike the two Escape-dismissible dialogs, this modal deliberately
    # doesn't close: it's raised because the server is gone, so `alertdialog`
    # interrupts rather than offering a choice. This file only checks it's
    # announced/focused like a modal and adds no third Escape claimant;
    # structural coverage lives in test_server_dead_session.py.
    assert 'role="alertdialog" aria-modal="true"' in HTML
    assert 'aria-labelledby="dead-title" aria-describedby="dead-body"' in HTML
    assert 'id="dead-panel" tabindex="-1"' in HTML, \
        "a dialog with no focusable child must be focusable itself"
    assert "el('dead-panel').focus();" in HTML
    # Only the recap and prefs closes carry a keycap; no third one appeared.
    assert HTML.count("<kbd>esc</kbd>") == 2, \
        "only the two dismissible dialogs may advertise Escape"
    # Escape is swallowed, not handled, while the overlay is up: the blanket
    # return sits ahead of every keydown branch including the two dialogs'
    # own Escape cases.
    kd = HTML.index("document.addEventListener('keydown'")
    swallow = HTML.index("if (deadSessionIsOpen()) return;", kd)
    prefs_esc = HTML.index(
        "if (e.key === 'Escape' && prefsIsOpen()) { closePrefsPanel(); return; }", kd)
    assert kd < swallow < prefs_esc, \
        "the dead-session swallow must precede every Escape handler"
    print("  ok  test_dead_session_overlay_is_the_one_modal_that_does_not_close")


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
    test_the_counts_define_themselves_in_reach()
    test_a_key_calls_approve_section()
    test_catalog_ground_ships()
    test_grid_and_sheet_frame_gone()
    test_prefs_toggle_is_native_button_static_label()
    test_prefs_toggle_gated_on_empty_store()
    test_prefs_overlay_is_dialog_mirrors_recap()
    test_prefs_and_recap_are_mutually_exclusive()
    test_prefs_panel_swallows_card_shortcuts_while_open()
    test_prefs_panel_closes_on_sse_view_swaps()
    test_prefs_status_is_the_only_live_region_in_the_panel()
    test_muted_row_names_the_unmute_recovery_and_this_round_effect()
    test_mute_button_only_on_standing_rows()
    test_prefs_data_fetched_once_and_cached_for_round_rebuilds()
    test_dead_session_overlay_is_the_one_modal_that_does_not_close()
    test_preference_badge_reuses_annot_jump_never_the_raw_id()
    print("OK (25 tests)")


if __name__ == "__main__":
    main()
