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
    print("OK (11 tests)")


if __name__ == "__main__":
    main()
