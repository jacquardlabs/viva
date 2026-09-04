#!/usr/bin/env python3
"""Static regression test: renderMarkdown() must never commit HTML that marked
produced without DOMPurify having sanitized it (the two CDN scripts load via
`defer`, so a window exists where marked is ready but DOMPurify isn't).

String-needle checks against the HTML constant (no browser/DOM in this
harness), matching the pattern in test_server_a11y.py.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

HTML = server.HTML


def test_dompurify_script_has_id():
    # Needed for a 'load' listener, same as marked-script has.
    assert 'id="dompurify-script"' in HTML, \
        "dompurify <script> tag must carry an id for its own load listener"
    print("  ok  test_dompurify_script_has_id")


def test_rendermarkdown_requires_both_deps_before_rendering():
    m = re.search(
        r"function renderMarkdown\(target, md\) \{\s*"
        r"if \(([^)]+)\) \{",
        HTML,
    )
    assert m, "renderMarkdown() not found in expected shape"
    guard = m.group(1)
    assert "window.marked" in guard and "window.DOMPurify" in guard, \
        f"renderMarkdown must gate on both marked and DOMPurify, got: {guard!r}"
    print("  ok  test_rendermarkdown_requires_both_deps_before_rendering")


def test_no_unsanitized_fallback_branch():
    # Old ternary fell back to raw `html` when DOMPurify hadn't loaded yet.
    assert "DOMPurify.sanitize(html)" in HTML
    assert ": html;" not in HTML, \
        "no branch may fall back to raw (unsanitized) html on innerHTML assignment"
    print("  ok  test_no_unsanitized_fallback_branch")


def test_retry_listener_attached_to_both_scripts():
    # A raw-text card must retry once *either* dep finishes loading, not just
    # marked's — otherwise it can be stranded if marked loads first.
    assert "['marked-script', 'dompurify-script']" in HTML, \
        "the fallback retry must listen on both marked-script and dompurify-script"
    print("  ok  test_retry_listener_attached_to_both_scripts")


def main():
    test_dompurify_script_has_id()
    test_rendermarkdown_requires_both_deps_before_rendering()
    test_no_unsanitized_fallback_branch()
    test_retry_listener_attached_to_both_scripts()
    print("OK (4 tests)")


if __name__ == "__main__":
    main()
