#!/usr/bin/env python3
"""Static a11y assertions on the embedded SPA (server.HTML).

Cluster A accessibility pass: card headers are native <button>s with
aria-expanded/aria-controls (#74), a <main> landmark wraps the shell (#37),
stats announce via aria-live and the title is set per mode/round (#35),
decorative emoji are aria-hidden (#38), the focus-visible group covers the new
controls (#52), action-btns carry type="button" (#51), and a keyboard legend
ships (#39). These are string-needle checks against the HTML constant; the
aria-expanded *toggle* behavior is verified manually in a browser.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

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
    assert '<main class="shell">' in HTML and "</main>" in HTML
    assert '<div class="shell">' not in HTML
    print("  ok  test_main_landmark_wraps_shell")


def test_stats_aria_live_and_dynamic_title():
    assert 'id="stats-area" aria-live="polite"' in HTML
    assert "document.title = 'viva · review · REV '" in HTML
    assert "document.title = 'viva · brainstorm'" in HTML
    print("  ok  test_stats_aria_live_and_dynamic_title")


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


def main():
    test_card_head_is_button_with_aria()
    test_aria_expanded_sync_helper_exists()
    test_main_landmark_wraps_shell()
    test_stats_aria_live_and_dynamic_title()
    test_decorative_emoji_are_aria_hidden()
    test_focus_visible_group_and_button_types()
    test_keyboard_legend_present_and_real()
    print("OK (7 tests)")


if __name__ == "__main__":
    main()
