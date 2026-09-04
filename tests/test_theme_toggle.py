#!/usr/bin/env python3
"""Theme toggle — the reader's override of `prefers-color-scheme`.

Cycles system -> light -> dark -> system, where "system" is the *absence*
of `data-theme`, not a third value. Dark is written twice (media query and
`[data-theme="dark"]`, since CSS can't share a palette across selectors
without a build step); `test_dark_palettes_are_identical` guards the two
from drifting.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import HTML  # noqa: E402


def _tokens(block: str) -> dict:
    """Every `--token: value;` declaration in a CSS block, as a dict."""
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r'(--[\w-]+):\s*([^;]+);', block)}


def _block_after(text: str, selector: str) -> str:
    """The declaration body following `selector`, to its closing brace."""
    start = text.index(selector) + len(selector)
    start = text.index('{', start) + 1
    return text[start:text.index('}', start)]


def test_dark_palettes_are_identical() -> None:
    """The two dark blocks must declare the same tokens with the same values.

    Catches a dark palette tuned in only one place, which would make the
    toggle silently produce a different dark than the OS does."""
    media = _tokens(_block_after(HTML, ':root:not([data-theme="light"])'))
    explicit = _tokens(_block_after(HTML, ':root[data-theme="dark"]'))
    assert media, "no tokens found in the prefers-color-scheme dark block"
    assert media == explicit, (
        "the two dark palettes have drifted — media-query block vs "
        "[data-theme=\"dark\"] block:\n"
        + "\n".join(
            f"  {k}: media={media.get(k, '<missing>')!r} "
            f"explicit={explicit.get(k, '<missing>')!r}"
            for k in sorted(set(media) | set(explicit))
            if media.get(k) != explicit.get(k)))
    print("  ok  test_dark_palettes_are_identical")


def test_override_beats_the_media_query_both_ways() -> None:
    """`[data-theme]` must win over `prefers-color-scheme`, in both directions.

    Light-on-dark-OS needs `:not([data-theme="light"])` on the media block;
    without it the toggle would appear to do nothing on a dark-mode machine."""
    assert ':root:not([data-theme="light"])' in HTML, \
        "the dark media block must stand down when light is chosen explicitly"
    assert ':root[data-theme="dark"]' in HTML, \
        "no explicit dark selector — dark would be unreachable on a light OS"
    assert re.search(r':root\[data-theme="light"\]\s*\{[^}]*color-scheme:\s*light', HTML), \
        "explicit light must set color-scheme so browser chrome follows"
    assert re.search(r':root\[data-theme="dark"\]\s*\{[^}]*color-scheme:\s*dark', HTML), \
        "explicit dark must set color-scheme so browser chrome follows"
    print("  ok  test_override_beats_the_media_query_both_ways")


def test_theme_applies_before_first_paint() -> None:
    """The stored choice is read in <head>, ahead of the stylesheet.

    A theme applied after the body renders paints OS-then-flips; pinned by
    position since no other test in this suite would catch it."""
    head = HTML[:HTML.index('<style>')]
    assert "localStorage.getItem('viva-theme')" in head, \
        "the stored theme must be applied before first paint, in <head>"
    assert 'document.documentElement.dataset.theme' in head, \
        "the pre-paint script must set data-theme on the root element"
    assert 'try' in head and 'catch' in head, \
        "localStorage throws in private mode — a theme is not worth a broken page"
    print("  ok  test_theme_applies_before_first_paint")


def test_system_is_the_absence_of_the_attribute() -> None:
    """Cycling back to "system" removes the attribute and the stored key.

    Storing "system" as a third value would leave a returning reader
    pinned to whatever the attribute last said."""
    assert 'const THEME_CYCLE = [null, ' in HTML, \
        "the cycle must start from null (system), not a string"
    assert "delete document.documentElement.dataset.theme" in HTML, \
        "returning to system must remove the attribute, not set it to 'system'"
    assert "localStorage.removeItem('viva-theme')" in HTML, \
        "returning to system must clear the stored choice"
    print("  ok  test_system_is_the_absence_of_the_attribute")


def test_toggle_is_reachable_and_labelled() -> None:
    """Keyboard-reachable, focus-visible, and named for what it does.

    The name must spell out the switch, not repeat the visible current-state
    label, or a screen-reader user can't tell state from action."""
    assert 'id="theme-toggle"' in HTML, "no theme toggle in the markup"
    assert re.search(r'<button type="button" class="theme-toggle"', HTML), \
        "the toggle must be a real button — a div is not keyboard-reachable"
    assert '.theme-toggle:focus-visible' in HTML, \
        "the toggle must carry the suite's visible focus state"
    assert "'Theme: ' + (t || 'following system') + '. Activate to switch to '" in HTML, \
        "the accessible name must say what activating the button does"
    assert re.search(r'\.theme-toggle\s*\{[^}]*border-radius:\s*0', HTML), \
        "square per the catalog's shape rule"
    print("  ok  test_toggle_is_reachable_and_labelled")


def main() -> None:
    test_dark_palettes_are_identical()
    test_override_beats_the_media_query_both_ways()
    test_theme_applies_before_first_paint()
    test_system_is_the_absence_of_the_attribute()
    test_toggle_is_reachable_and_labelled()
    print("OK (5 tests)")


if __name__ == "__main__":
    main()
