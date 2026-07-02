#!/usr/bin/env python3
"""Static regression test: renderMarkdown() must never commit HTML that marked
produced without DOMPurify having sanitized it.

Context (issue found in the /input-fetch-decoupling speed fix): the fetch that
populates REVIEW_DATA now runs before DOMContentLoaded, specifically so it
doesn't wait on the two `defer`red CDN <script> tags (marked, DOMPurify). Since
DOMPurify is the second `defer` script with no id/listener of its own, there is
a structurally guaranteed window where `window.marked` is truthy but
`window.DOMPurify` is not. If renderMarkdown() rendered in that window (as it
did when gated on `window.marked` alone, falling back to raw `html` when
DOMPurify was absent), it would permanently commit unsanitized HTML — the
one-time retry only re-renders cards still carrying the 'md-raw' class, so a
card that took the unsanitized-but-'successful' path was never revisited.

These are string-needle checks against the HTML constant, matching the pattern
in test_server_a11y.py; the actual load-order race isn't reproducible in the
subprocess+urllib server harness (no browser/DOM), so this test pins the
source-level invariants that close the window instead.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

HTML = server.HTML


def test_dompurify_script_has_id():
    # Needed so a 'load' listener can be attached to it directly, the same way
    # marked-script already has one.
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
    # The old `window.DOMPurify ? DOMPurify.sanitize(html) : html` ternary let
    # unsanitized `html` reach innerHTML when DOMPurify hadn't loaded yet.
    # Once both deps are required to enter the branch at all, sanitize() is
    # unconditional.
    assert "DOMPurify.sanitize(html)" in HTML
    assert ": html;" not in HTML, \
        "no branch may fall back to raw (unsanitized) html on innerHTML assignment"
    print("  ok  test_no_unsanitized_fallback_branch")


def test_retry_listener_attached_to_both_scripts():
    # A card that fell back to raw text while only one dependency was missing
    # must become eligible for retry once *either* one finishes loading —
    # otherwise it can be stranded as raw text if marked's 'load' fires before
    # DOMPurify is ready (the guaranteed defer order) and nothing re-checks
    # after DOMPurify itself loads.
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
