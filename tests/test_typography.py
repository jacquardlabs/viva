#!/usr/bin/env python3
"""The page's faces: verbatim glyphs, local files, two families (#79).

String needles against `server.HTML` — no JS engine or browser harness here,
so a needle proves the page SAYS this, not that it paints it.
`tests/test_server_vendor_assets.py` checks the `/vendor/` routes exist.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

HTML = server.HTML


def test_no_glyph_the_source_did_not_contain() -> None:
    assert "font-variant-ligatures: none;" in HTML, \
        "ligature substitution must be off — `>=` must not paint as a single ≥"
    assert HTML.count("font-variant-ligatures") == 1, \
        ("declared once, on the ground, and inherited — a per-surface rule is "
         "the one the next mono surface forgets")
    # Anchored `\nbody {` — a bare `body {` also matches `.thread-body {`.
    body_open = HTML.index("\nbody {")
    body_close = HTML.index("\n}", body_open)
    assert body_open < HTML.index("font-variant-ligatures") < body_close, \
        "the rule belongs on `body`, where everything inherits it"
    assert "font-feature-settings" not in HTML, \
        ("mixing the low-level property with `font-variant-numeric: tabular-nums` "
         "on one element is the only way to put tabular figures at risk")
    print("  ok  test_no_glyph_the_source_did_not_contain")


def test_the_page_reaches_no_font_host() -> None:
    for host in ("fonts.googleapis.com", "fonts.gstatic.com"):
        assert host not in HTML, f"the page still fetches a face from {host}"
    assert "preconnect" not in HTML, \
        "a preconnect means there is still a remote host to connect to"
    print("  ok  test_the_page_reaches_no_font_host")


def test_every_face_is_served_from_this_server() -> None:
    faces = re.findall(r"@font-face\s*\{(.*?)\}", HTML, re.S)
    assert len(faces) == 4, f"expected 4 @font-face blocks, found {len(faces)}"
    for block in faces:
        assert "font-family: 'Fragment Mono'" in block, \
            "the only vendored family is Fragment Mono"
        assert "font-display: swap" in block, \
            "a face with no display strategy blocks the first paint"
        assert "unicode-range:" in block, \
            "Google's own subsetting ranges are kept verbatim"
        url = re.search(r"url\('([^']+)'\)", block)
        assert url, f"a face with no src: {block[:60]}"
        assert url.group(1) in server._VENDOR_ROUTES, \
            f"{url.group(1)} has no route — the browser 404s into a silent fallback"
    styles = sorted(re.search(r"font-style: (\w+)", b).group(1) for b in faces)
    assert styles == ["italic", "italic", "normal", "normal"], \
        f"both styles, both subsets — got {styles}"
    print("  ok  test_every_face_is_served_from_this_server")


def test_only_two_families_ship() -> None:
    """Read the FILE, not just `HTML`, so a stray comment fails too."""
    source = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "Bricolage" not in source, \
        ("DESIGN.md: \"Two families only. No exceptions.\" — Bricolage Grotesque "
         "was a third, and `'Bricolage Grotesque', sans-serif` fell back past "
         "the page's own grotesque stack entirely when it failed to load")
    print("  ok  test_only_two_families_ship")


def main() -> None:
    test_no_glyph_the_source_did_not_contain()
    test_the_page_reaches_no_font_host()
    test_every_face_is_served_from_this_server()
    test_only_two_families_ship()
    print("OK (4 tests)")


if __name__ == "__main__":
    main()
