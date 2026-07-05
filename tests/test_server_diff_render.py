#!/usr/bin/env python3
"""Integration test for diff-mode hunk rendering (issue #99).

This is a wiring test, not a parse-correctness test: the rendered diff is
built client-side in JS (delegated to diff2html), and this repo has no
JS/browser test harness (stdlib Python only, no npm/node). What's verifiable
from a subprocess+urllib harness is that:

  1. The diff2html CDN assets and the renderDiffHunk adapter are actually
     shipped in the served page, gated on diff mode, and the deleted
     hand-rolled renderer is truly gone (not just bypassed).
  2. A diff-mode round still serves each section's `content` as the verbatim
     fenced ```diff block, unchanged — the /viva-diff skill relocates edits by
     matching `comment.anchor.text` against the source, and round-to-round
     carry-forward compares `content` byte-for-byte (parse_diff.py
     `_carry_forward`). The renderer is a pure view transform; it must never
     be allowed to alter what's actually served for `content`.

One server boot serves every check (the repo's one-boot-per-file convention;
the page is a static constant and all requests here are read-only GETs), with
one fixture covering grouping (two files, multi-hunk) and the binary sentinel.

Manual end-to-end verification of the rendered diff itself (alignment,
word-level highlighting, gutters, binary fallback) is a browser check, not a
subprocess+urllib one — nothing that lives in the DOM is exercised here.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, get_text, launch_server  # noqa: E402

DIFF_INPUT = {
    "mode": "diff",
    "doc_file": "HEAD~1..HEAD",
    "round": 1,
    "approved_ids": [],
    "sections": [
        {
            "id": "s1",
            "title": "src/foo.py hunk 1",
            "content": "```diff\n@@ -1,3 +1,4 @@\n line 1\n-old line\n+new line\n+extra\n line 3\n```",
        },
        {
            "id": "s2",
            "title": "src/foo.py hunk 2",
            "content": "```diff\n@@ -10,2 +11,3 @@\n x\n+y\n z\n```",
        },
        {
            "id": "s3",
            "title": "src/bar.py hunk 1",
            "content": "```diff\n@@ -1,1 +1,2 @@\n p\n+q\n```",
        },
        {
            "id": "s4",
            "title": "binary.png hunk 1",
            "content": "Binary file changed — no content to review",
        },
    ],
}


def test_page_ships_filepath_helper(page: str) -> None:
    """filepathFromTitle stays the single definition of 'strip the hunk
    suffix off a diff-mode section title' — both diffFileHunkCounts
    (file grouping) and renderDiffHunk (preamble synthesis) call it from
    their own function bodies."""
    assert "function filepathFromTitle" in page, "page missing: function filepathFromTitle"
    for caller in ("diffFileHunkCounts", "renderDiffHunk"):
        m = re.search(r"function " + caller + r"\(.*?\n\}", page, re.S)
        assert m, f"page missing: function {caller}"
        assert "filepathFromTitle(" in m.group(0), \
            f"{caller} does not call filepathFromTitle — reuse not confirmed"
    print("test_page_ships_filepath_helper: OK")


def test_page_ships_file_group_header(page: str) -> None:
    """The grouping logic and its CSS are shipped, gated on diff mode."""
    for needle in (
        "function diffFileHunkCounts",
        "file-group-header",
        "REVIEW_DATA.mode === 'diff'",
    ):
        assert needle in page, f"page missing: {needle}"
    print("test_page_ships_file_group_header: OK")


def test_grouped_sections_stay_file_contiguous(data: dict) -> None:
    """The grouping feature assumes hunks of the same file are never
    interleaved with another file's hunks — parse_diff.py guarantees this by
    construction. This pins that precondition against the fixture the SPA
    would otherwise silently mis-group if it regressed."""
    titles = [s["title"] for s in data["sections"]]
    filepaths = [t.rsplit(" hunk ", 1)[0] for t in titles]
    seen = []
    for fp in filepaths:
        if not seen or seen[-1] != fp:
            seen.append(fp)
    assert seen.count("src/foo.py") == 1 and seen.count("src/bar.py") == 1, \
        f"expected each filepath as one contiguous run, got order: {filepaths}"
    print("test_grouped_sections_stay_file_contiguous: OK")


def test_diff_content_served_verbatim(data: dict) -> None:
    """The renderer must never reshape what /input serves for `content` —
    anchor-based edit relocation and carry-forward both depend on the raw
    fenced ```diff string reaching the client unchanged."""
    by_id = {s["id"]: s for s in data["sections"]}
    assert by_id["s1"]["content"] == DIFF_INPUT["sections"][0]["content"], \
        "hunk content must be served byte-for-byte unchanged"
    assert by_id["s4"]["content"] == "Binary file changed — no content to review", \
        "binary sentinel must be served unchanged (no ```diff fence to gate on)"
    print("test_diff_content_served_verbatim: OK")


def test_page_ships_diff_mode_sort_toggle_guard(page: str) -> None:
    """setupCardSort's own function body — not just anywhere on the page —
    must force hasConfidence false in diff mode, unconditionally, not just
    because diff-mode sections happen not to carry confidence annotations
    today. Without this guard, the static file-group-header divs (which
    carry no CSS `order`) would be stranded if the sort toggle ever
    reordered cards in diff mode."""
    m = re.search(r"function setupCardSort\(.*?\n\}", page, re.S)
    assert m, "page missing: function setupCardSort"
    assert "REVIEW_DATA.mode !== 'diff'" in m.group(0), \
        "setupCardSort does not guard hasConfidence on REVIEW_DATA.mode !== 'diff'"
    print("test_page_ships_diff_mode_sort_toggle_guard: OK")


def test_page_ships_mode_diff_layout(page: str) -> None:
    """Wiring check only: the diff dispatch branch stamps mode-diff on <body>
    and injects the diff2html stylesheet (mode-specific, so review/QA never
    pay a render-blocking CDN fetch for it), and the mode-scoped CSS
    overrides (wide shell/bottom bar, no nested section scroll) ship in the
    served page. Does not measure rendered layout."""
    m = re.search(r"mode === 'diff'\) \{(.*?)\} else", page, re.S)
    assert m, "page missing: diff dispatch branch"
    branch = m.group(1)
    assert "document.body.classList.add('mode-diff')" in branch, \
        "diff branch does not stamp mode-diff on body"
    for needle in (
        "d2hCss.id = 'diff2html-css'",
        "d2hCss.href = 'https://cdn.jsdelivr.net/npm/diff2html@3/bundles/css/diff2html.min.css'",
        "retryOnceScriptsLoad(['diff2html-css']",
    ):
        assert needle in branch, f"diff branch missing stylesheet injection/retry: {needle}"
    m = re.search(r"\.mode-diff \.shell,\s*\.mode-diff \.bottom-inner \{[^}]*\}", page)
    assert m and "min(95vw, 1600px)" in m.group(0), \
        "page missing: mode-diff wide shell/bottom-bar rule"
    m = re.search(r"\.mode-diff \.section-content \{[^}]*\}", page)
    assert m and "max-height: none" in m.group(0) and "overflow-y: visible" in m.group(0), \
        "page missing: mode-diff nested-scroll removal"
    print("test_page_ships_mode_diff_layout: OK")


def test_page_ships_diff2html_renderer(page: str) -> None:
    """Wiring check only: the served page loads the diff2html@3 core +
    slim-UI scripts (the stylesheet is injected by the diff dispatch branch
    — see the layout test), and ships the renderDiffHunk adapter with the
    audited pipeline: string API (Diff2Html.html), sanitize-BEFORE-DOM
    (gate-audit: materializing first would let insertion-time payloads run
    before removal), a CSS-readiness gate, an aria-hidden pass on line
    numbers, and the spec's exact config. The hand-rolled renderer stays
    gone."""
    for tag in (
        'id="diff2html-script" src="https://cdn.jsdelivr.net/npm/diff2html@3/bundles/js/diff2html.min.js"',
        'id="diff2html-ui-script" src="https://cdn.jsdelivr.net/npm/diff2html@3/bundles/js/diff2html-ui-slim.min.js"',
    ):
        assert tag in page, f"page missing script tag: {tag}"
    m = re.search(r"function renderDiffHunk\(.*?\n\}", page, re.S)
    assert m, "page missing: function renderDiffHunk"
    body = m.group(0)
    for needle in (
        "diffStyle: 'word'",
        "matching: 'words'",
        "drawFileList: false",
        "colorScheme: 'auto'",
        "window.innerWidth >= 900 ? 'side-by-side' : 'line-by-line'",
        "Diff2Html.html(",
        "DOMPurify.sanitize(rawHtml)",
        "cssLink.sheet",
        "setAttribute('aria-hidden'",
    ):
        assert needle in body, f"renderDiffHunk missing: {needle}"
    # sanitize-before-DOM order: the sanitize call must feed the innerHTML
    # assignment directly, never read back already-materialized DOM.
    assert "DOMPurify.sanitize(target.innerHTML)" not in body, \
        "renderDiffHunk sanitizes after materializing — inverted order"
    # The hand-rolled renderer is deleted, not just bypassed. 'sxs' had no
    # other meaning anywhere in the page, so its total absence is the
    # strongest cheap deletion check available to this harness.
    for gone in ("function renderDiffTable", "function alignBlock",
                 "function lcsMatches", "function alignGap",
                 "function buildSxsTableHtml", "function toggleFold",
                 "HLJS_HIGHLIGHT_CAP", "sxs"):
        assert gone not in page, f"page still ships deleted symbol: {gone}"
    print("test_page_ships_diff2html_renderer: OK")


def test_page_ships_d2h_guards(page: str) -> None:
    """Wiring check only: the viva-side guards on the d2h surface ship —
    token theming via d2h's own custom properties (light + dark families),
    the Fragment Mono font guard, the file-name/tag dedup, the scoped td
    reset (specificity bleed), user-select:none on line numbers (anchor
    hygiene), the containing-block/radius rule on the file wrapper, the
    cross-pane selection guard, and the shared load-retry helper wired to
    both d2h scripts (the hljs-race lesson from gate-audit)."""
    for needle in (
        "--d2h-bg-color: var(--bg)",
        "--d2h-dark-bg-color: var(--bg)",
        "--d2h-file-header-bg-color: var(--bg2)",
        ".section-content .d2h-diff-table",
        ".section-content .d2h-file-name",
        ".section-content .d2h-wrapper td",
        ".section-content .d2h-code-linenumber",
        "user-select: none",
        "position: relative; border-radius: 6px;",
        "function closestD2hPane",
        "closestD2hPane(sel.anchorNode) !== closestD2hPane(sel.focusNode)",
        "function retryOnceScriptsLoad",
        "retryOnceScriptsLoad(['diff2html-script', 'diff2html-ui-script'], '.section-content.d2h-pending')",
        "retryOnceScriptsLoad(['marked-script', 'dompurify-script'], '.section-content.md-raw')",
    ):
        assert needle in page, f"page missing: {needle}"
    print("test_page_ships_d2h_guards: OK")


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        data = get(base, "/input")
        test_page_ships_filepath_helper(page)
        test_page_ships_file_group_header(page)
        test_grouped_sections_stay_file_contiguous(data)
        test_diff_content_served_verbatim(data)
        test_page_ships_diff_mode_sort_toggle_guard(page)
        test_page_ships_mode_diff_layout(page)
        test_page_ships_diff2html_renderer(page)
        test_page_ships_d2h_guards(page)
    print("\nAll server diff-render tests passed.")


if __name__ == "__main__":
    main()
