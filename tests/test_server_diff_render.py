#!/usr/bin/env python3
"""Integration test for diff-mode hunk rendering (#99).

A wiring test, not a parse-correctness test: the diff is rendered client-side
via diff2html (no JS/browser harness here), so this checks the assets and
adapter ship correctly and that `content` is served byte-for-byte unchanged.
One server boot serves every check.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, get_text, launch_server, post_status  # noqa: E402

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
            # Agent's one-liner (#188), present on one hunk, absent on the next.
            "summary": "swaps the placeholder line for the real one",
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
    """filepathFromTitle is the single definition of 'strip the hunk suffix';
    both diffFileHunkCounts and renderDiffHunk must call it."""
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
    """Hunks of the same file must never interleave with another file's —
    parse_diff.py guarantees this by construction."""
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
    anchor relocation and carry-forward both depend on it reaching the client unchanged."""
    by_id = {s["id"]: s for s in data["sections"]}
    assert by_id["s1"]["content"] == DIFF_INPUT["sections"][0]["content"], \
        "hunk content must be served byte-for-byte unchanged"
    assert by_id["s4"]["content"] == "Binary file changed — no content to review", \
        "binary sentinel must be served unchanged (no ```diff fence to gate on)"
    print("test_diff_content_served_verbatim: OK")


def test_page_ships_diff_mode_sort_toggle_guard(page: str) -> None:
    """setupCardSort must force hasConfidence false in diff mode,
    unconditionally — otherwise the file-group-header divs get stranded
    if sort ever reorders cards."""
    m = re.search(r"function setupCardSort\(.*?\n\}", page, re.S)
    assert m, "page missing: function setupCardSort"
    assert "REVIEW_DATA.mode !== 'diff'" in m.group(0), \
        "setupCardSort does not guard hasConfidence on REVIEW_DATA.mode !== 'diff'"
    print("test_page_ships_diff_mode_sort_toggle_guard: OK")


def test_page_ships_mode_diff_layout(page: str) -> None:
    """Wiring check: diff dispatch stamps mode-diff on <body>, injects the
    diff2html stylesheet, and ships the mode-scoped layout overrides."""
    m = re.search(r"mode === 'diff'\) \{(.*?)\} else", page, re.S)
    assert m, "page missing: diff dispatch branch"
    branch = m.group(1)
    assert "document.body.classList.add('mode-diff')" in branch, \
        "diff branch does not stamp mode-diff on body"
    for needle in (
        "d2hCss.id = 'diff2html-css'",
        # Local, version-stamped route — never jsdelivr (#144).
        "d2hCss.href = '/vendor/diff2html-3.4.56.min.css'",
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
    """Wiring check: the page loads diff2html@3 scripts and ships
    renderDiffHunk with sanitize-BEFORE-DOM, a CSS-readiness gate, and
    aria-hidden line numbers. The hand-rolled renderer stays gone."""
    for tag in (
        'id="diff2html-script" src="/vendor/diff2html-3.4.56.min.js"',
        'id="diff2html-ui-script" src="/vendor/diff2html-ui-slim-3.4.56.min.js"',
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
        "outputFormat: 'line-by-line'",
        "Diff2Html.html(",
        "DOMPurify.sanitize(rawHtml)",
        "cssLink.sheet",
        "setAttribute('aria-hidden'",
    ):
        assert needle in body, f"renderDiffHunk missing: {needle}"
    # sanitize must feed innerHTML directly, never read back materialized DOM.
    assert "DOMPurify.sanitize(target.innerHTML)" not in body, \
        "renderDiffHunk sanitizes after materializing — inverted order"
    # The hand-rolled renderer must be deleted, not just bypassed.
    for gone in ("function renderDiffTable", "function alignBlock",
                 "function lcsMatches", "function alignGap",
                 "function buildSxsTableHtml", "function toggleFold",
                 "HLJS_HIGHLIGHT_CAP", "sxs"):
        assert gone not in page, f"page still ships deleted symbol: {gone}"
    print("test_page_ships_diff2html_renderer: OK")


def test_page_ships_d2h_guards(page: str) -> None:
    """Wiring check: viva-side guards on the d2h surface ship — token
    theming, font guard, dedup, td reset, and the shared load-retry helper."""
    # Cross-pane selection guard went with the panes: a unified hunk is one
    # column, so every selection is a contiguous substring already.
    assert "closestD2hPane" not in page, \
        "the cross-pane guard must not outlive the panes"
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
        "function retryOnceScriptsLoad",
        "retryOnceScriptsLoad(['diff2html-script', 'diff2html-ui-script'], '.section-content.d2h-pending')",
        "retryOnceScriptsLoad(['marked-script', 'dompurify-script'], '.section-content.md-raw')",
    ):
        assert needle in page, f"page missing: {needle}"
    print("test_page_ships_d2h_guards: OK")


def test_a_rendered_diff_is_not_held_to_the_prose_measure(page: str) -> None:
    """Regression: a diff-mode section holds no prose, so `.section-content`'s
    72ch reading-width cap must be dropped there — capping the child
    (`.d2h-wrapper`) can't fix it since a child can't exceed its parent."""
    m = re.search(r'\.mode-diff \.section-content\s*\{([^}]*)\}', page)
    assert m, "the diff-mode container rule is gone"
    body = m.group(1)
    assert 'max-width: none' in body, \
        "diff mode must drop the prose measure — the container is the constraint"
    assert 'max-height: none' in body and 'overflow-y: visible' in body, \
        "diff mode must also keep its no-nested-scroll guarantee"
    print("test_a_rendered_diff_is_not_held_to_the_prose_measure: OK")


def test_page_renders_a_section_summary_under_the_title(page: str) -> None:
    """Wiring check: the agent's one-line `summary` (#188) renders escaped
    in the title wrap of both builders — a `<span>` in the button head
    (`buildReviewCard`), a `<div>` in the continuous print (`buildDocSection`)."""
    for fn, tag in (("buildReviewCard", "span"), ("buildDocSection", "div")):
        m = re.search(r"function " + fn + r"\(.*?\n\}", page, re.S)
        assert m, f"page missing: function {fn}"
        body = m.group(0)
        needle = 'section.summary ? `<%s class="section-summary">${esc(section.summary)}' % tag
        assert needle in body, f"{fn} does not render an escaped summary as a <{tag}>"
    # Conditional, so a section without one emits no empty element at all.
    assert "${section.summary ? `" in page, \
        "the summary render must be gated on presence, not always emitted"
    # Head override clamps to one line, or a long summary grows every row.
    m = re.search(r"\.card-title-wrap \.section-summary \{[^}]*\}", page)
    assert m, "page missing: the card-head override for .section-summary"
    assert "text-overflow: ellipsis" in m.group(0), \
        "a head summary must clamp to one line"
    print("test_page_renders_a_section_summary_under_the_title: OK")


def test_a_summary_is_served_and_validated(data: dict, base: str) -> None:
    """`/input` passes `summary` through untouched; `/next-round`'s boundary
    validator rejects a non-string one (keeps `null` from printing under a title).
    Run last: a payload past the gate replaces the live round."""
    by_id = {s["id"]: s for s in data["sections"]}
    assert by_id["s1"]["summary"] == DIFF_INPUT["sections"][0]["summary"], \
        "the summary must be served verbatim"
    assert "summary" not in by_id["s2"], \
        "a section the agent left undescribed must gain no summary key"
    bad = json.loads(json.dumps(DIFF_INPUT))
    bad["output"] = "out2.json"
    bad["sections"][0]["summary"] = None
    code = post_status(base, "/next-round", bad)
    assert code == 400, f"a null summary must be refused at /next-round; got {code}"
    print("test_a_summary_is_served_and_validated: OK")


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
        test_a_rendered_diff_is_not_held_to_the_prose_measure(page)
        test_page_renders_a_section_summary_under_the_title(page)
        test_a_summary_is_served_and_validated(data, base)
    print("\nAll server diff-render tests passed.")


if __name__ == "__main__":
    main()
