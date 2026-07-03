#!/usr/bin/env python3
"""Integration test for side-by-side hunk rendering in --mode diff (issue #99).

This is a wiring test, not a parse-correctness test: the two-column table is
built client-side in JS, and this repo has no JS/browser test harness (stdlib
Python only, no npm/node). What's verifiable from a subprocess+urllib harness
is that:

  1. The renderer and its side-by-side CSS are actually shipped in the served
     page, gated on diff mode.
  2. A diff-mode round still serves each section's `content` as the verbatim
     fenced ```diff block, unchanged — the /viva-diff skill relocates edits by
     matching `comment.anchor.text` against the source, and round-to-round
     carry-forward compares `content` byte-for-byte (parse_diff.py
     `_carry_forward`). The new renderer is a pure view transform; it must
     never be allowed to alter what's actually served for `content`.

Manual end-to-end verification of the rendered table itself (alignment,
context-fold, gutters, per-cell highlighting, binary fallback) is a browser
check, not a subprocess+urllib one — nothing that lives in the DOM is
exercised here.
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
            "title": "foo.py hunk 1",
            "content": "```diff\n@@ -1,3 +1,4 @@\n line 1\n-old line\n+new line\n+extra\n line 3\n```",
        },
        {
            "id": "s2",
            "title": "binary.png hunk 1",
            "content": "Binary file changed — no content to review",
        },
    ],
}

GROUPED_DIFF_INPUT = {
    "mode": "diff",
    "doc_file": "HEAD~1..HEAD",
    "round": 1,
    "approved_ids": [],
    "sections": [
        {
            "id": "s1",
            "title": "src/foo.py hunk 1",
            "content": "```diff\n@@ -1,2 +1,3 @@\n a\n+b\n c\n```",
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
    ],
}


def test_page_ships_side_by_side_renderer() -> None:
    """The served page embeds renderDiffTable, its .sxs- CSS, and the
    diff-mode gate in _ensureRendered — not just the old single-column path."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        for needle in (
            "function renderDiffTable",
            "function langFromTitle",
            "function sectionTitleFor",
            "function toggleFold",
            ".sxs-table",
            ".sxs-half.sxs-del",
            ".sxs-half.sxs-add",
            ".sxs-fold",
            "REVIEW_DATA.mode === 'diff'",
        ):
            assert needle in page, f"page missing: {needle}"
    print("test_page_ships_side_by_side_renderer: OK")


def test_page_ships_filepath_helper() -> None:
    """filepathFromTitle is extracted as its own function, and both
    langFromTitle (language inference) and diffFileHunkCounts (file-header
    grouping) call it from their own function bodies — not just co-present
    on the page, but actually reused, so the strip-hunk-suffix regex has
    exactly one definition."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        assert "function filepathFromTitle" in page, "page missing: function filepathFromTitle"
        for caller in ("langFromTitle", "diffFileHunkCounts"):
            m = re.search(r"function " + caller + r"\(.*?\n\}", page, re.S)
            assert m, f"page missing: function {caller}"
            assert "filepathFromTitle(" in m.group(0), \
                f"{caller} does not call filepathFromTitle — reuse not confirmed"
    print("test_page_ships_filepath_helper: OK")


def test_page_ships_file_group_header() -> None:
    """The grouping logic and its CSS are shipped, gated on diff mode."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(GROUPED_DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        for needle in (
            "function diffFileHunkCounts",
            "file-group-header",
            "REVIEW_DATA.mode === 'diff'",
        ):
            assert needle in page, f"page missing: {needle}"
    print("test_page_ships_file_group_header: OK")


def test_grouped_sections_stay_file_contiguous() -> None:
    """The grouping feature assumes hunks of the same file are never
    interleaved with another file's hunks — parse_diff.py guarantees this by
    construction. This pins that precondition against the fixture the SPA
    would otherwise silently mis-group if it regressed."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(GROUPED_DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        data = get(base, "/input")
        titles = [s["title"] for s in data["sections"]]
        filepaths = [t.rsplit(" hunk ", 1)[0] for t in titles]
        seen = []
        for fp in filepaths:
            if not seen or seen[-1] != fp:
                seen.append(fp)
        assert seen.count("src/foo.py") == 1 and seen.count("src/bar.py") == 1, \
            f"expected each filepath as one contiguous run, got order: {filepaths}"
    print("test_grouped_sections_stay_file_contiguous: OK")


def test_diff_content_served_verbatim() -> None:
    """The new renderer must never reshape what /input serves for `content` —
    anchor-based edit relocation and carry-forward both depend on the raw
    fenced ```diff string reaching the client unchanged."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        data = get(base, "/input")
        by_id = {s["id"]: s for s in data["sections"]}
        assert by_id["s1"]["content"] == DIFF_INPUT["sections"][0]["content"], \
            "hunk content must be served byte-for-byte unchanged"
        assert by_id["s2"]["content"] == "Binary file changed — no content to review", \
            "binary sentinel must be served unchanged (no ```diff fence to gate on)"
    print("test_diff_content_served_verbatim: OK")


def test_page_ships_diff_mode_sort_toggle_guard() -> None:
    """setupCardSort's own function body — not just anywhere on the page —
    must force hasConfidence false in diff mode, unconditionally, not just
    because diff-mode sections happen not to carry confidence annotations
    today. Without this guard, the static file-group-header divs (which
    carry no CSS `order`) would be stranded if the sort toggle ever
    reordered cards in diff mode."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(GROUPED_DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        m = re.search(r"function setupCardSort\(.*?\n\}", page, re.S)
        assert m, "page missing: function setupCardSort"
        assert "REVIEW_DATA.mode !== 'diff'" in m.group(0), \
            "setupCardSort does not guard hasConfidence on REVIEW_DATA.mode !== 'diff'"
    print("test_page_ships_diff_mode_sort_toggle_guard: OK")


def test_page_ships_native_fold_button() -> None:
    """The fold toggle is a real <button> (aria-controls + aria-expanded,
    in the :focus-visible group), not a <tr role="button"> with a manual
    keydown shim. Wiring check only — does not execute a click."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        for needle in (
            'class="sxs-fold-btn"',
            "aria-controls=",
            ".sxs-fold-btn:focus-visible",
        ):
            assert needle in page, f"page missing: {needle}"
        assert 'tabindex="0" role="button"' not in page, \
            "fold row should no longer be a manually-keyboard-shimmed <tr role=button>"
    print("test_page_ships_native_fold_button: OK")


def test_page_ships_highlight_cap() -> None:
    """Wiring check only: the served page ships HLJS_HIGHLIGHT_CAP and the
    lazy-highlight-on-expand path in toggleFold, so an oversized hunk skips
    hljs entirely instead of paying a highlightElement() call per line.
    Does not execute the cap decision against a real DOM."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        for needle in (
            "const HLJS_HIGHLIGHT_CAP",
            "target.dataset.sxsHighlight",
            "b.classList.contains('hljs')",
        ):
            assert needle in page, f"page missing: {needle}"
    print("test_page_ships_highlight_cap: OK")


def test_page_ships_mobile_stacked_layout() -> None:
    """Wiring check only: below 720px the served CSS stacks .sxs-change-cell
    to a single column and drops each half's independent horizontal scroll,
    so the two-column table doesn't become unreadable/misaligned on narrow
    viewports. Does not render or measure an actual viewport."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        assert "@media (max-width: 720px)" in page
        mobile_block = page.split("@media (max-width: 720px)", 1)[1]
        assert ".sxs-change-cell" in mobile_block and "flex-direction: column" in mobile_block, \
            "mobile breakpoint missing .sxs-change-cell column-stack rule"
    print("test_page_ships_mobile_stacked_layout: OK")


def test_page_ships_cross_half_selection_guard() -> None:
    """Wiring check only (no JS/browser harness in this repo): the served page
    ships closestSxsHalf and the mouseup handler's crossesHalves check that
    degrades a selection spanning two .sxs-half cells to an unanchored
    whole-section note, instead of storing a garbled comment.anchor.text.
    Does not execute the comparison against a real Selection."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        for needle in (
            "function closestSxsHalf",
            "closestSxsHalf(sel.anchorNode) !== closestSxsHalf(sel.focusNode)",
        ):
            assert needle in page, f"page missing: {needle}"
    print("test_page_ships_cross_half_selection_guard: OK")


def test_page_ships_lcs_alignment() -> None:
    """Wiring check only: the served page ships lcsMatches, alignBlock, and
    the sxs-same-row class, so a replace block's line pairing goes through
    real LCS realignment instead of naive buffer-index pairing. Does not
    execute the algorithm against a real DOM — see the plan's manual Node
    verification step for behavioral proof."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        for needle in (
            "function lcsMatches",
            "function alignBlock",
            "sxs-same-row",
        ):
            assert needle in page, f"page missing: {needle}"
        m = re.search(r"function alignBlock\(.*?\n\}", page, re.S)
        assert m, "page missing: function alignBlock"
        assert "lcsMatches(" in m.group(0), "alignBlock does not call lcsMatches"
    print("test_page_ships_lcs_alignment: OK")


def main() -> None:
    test_page_ships_side_by_side_renderer()
    test_page_ships_filepath_helper()
    test_page_ships_file_group_header()
    test_grouped_sections_stay_file_contiguous()
    test_diff_content_served_verbatim()
    test_page_ships_diff_mode_sort_toggle_guard()
    test_page_ships_native_fold_button()
    test_page_ships_highlight_cap()
    test_page_ships_mobile_stacked_layout()
    test_page_ships_cross_half_selection_guard()
    test_page_ships_lcs_alignment()
    print("\nAll server diff-render tests passed.")


if __name__ == "__main__":
    main()
