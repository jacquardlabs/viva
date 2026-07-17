#!/usr/bin/env python3
"""Frontend v2 phase 1 — served-page integration tests (sheet ground).

This is the phase's shared test file: the review fixture below and the
subprocess + urllib boot are the harness later phase-1 tasks (transmittal,
recap, between-rounds) extend with their own page-ships checks. Task 1's
coverage is the sheet ground: the served review page frames the shell in a
bounded #paper sheet — edge border, 1px inner rule at 7px inset, aria-hidden
coordinate/corner decoration — on a flat --table ground, the 24px drafting
grid and the fixed .sheet-frame are gone at every layer, and diff mode widens
the sheet in lockstep with the shell.

These are wiring checks against the served page (the HTML constant is static,
so one review-mode boot serves every mode's CSS); rendered layout is a browser
check, not a subprocess + urllib one.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get_text, launch_server  # noqa: E402

# Round-1 review fixture — deliberately plain (no diff/open_notes/annotations/
# approved_ids content) so later tasks can layer round-2 fixtures beside it.
REVIEW_INPUT_R1 = {
    "mode": "review",
    "doc_file": "docs/example.md",
    "round": 1,
    "approved_ids": [],
    "sections": [
        {"id": "s1", "title": "Overview", "content": "## Overview\n\nWhat this is."},
        {"id": "s2", "title": "Goals", "content": "## Goals\n\n- ship it"},
    ],
}


def test_page_ships_sheet_ground(page: str) -> None:
    """The served page frames the review in the bounded #paper sheet: --table
    ground in both theme token blocks, body painted with it, edge border,
    inner rule at 7px inset, and aria-hidden coordinate/corner decoration."""
    assert '--table:     #060e1a;' in page, "dark token block missing --table"
    assert '--table:     #e2e8f1;' in page, "light token block missing --table"
    assert 'background: var(--table);' in page, "body must sit on the flat table"
    assert '<div id="paper">' in page, "page missing the #paper sheet"
    assert ('#paper { position: relative; max-width: 700px; margin: 32px auto 96px; '
            'background: var(--bg); border: 1px solid var(--border2); }') in page, \
        "#paper missing its content-bounded edge"
    assert ("#paper::before { content: ''; position: absolute; inset: 7px; "
            "border: 1px solid var(--border); pointer-events: none; }") in page, \
        "#paper missing the 1px inner rule at 7px inset"
    assert '<div class="paper-marks" aria-hidden="true">' in page, \
        "sheet decoration must be aria-hidden"
    assert page.count('class="pmark') == 4, "expected 4 corner registration marks"
    assert '<span class="pcoord pc-n" style="left:12.5%">1</span>' in page, \
        "missing edge coordinate numbers"
    assert '<span class="pcoord pc-w" style="top:12.5%">A</span>' in page, \
        "missing edge coordinate letters"
    # The sheet bounds the content: main.shell opens after #paper and closes
    # before it.
    assert page.index('<div id="paper">') < page.index('<main class="shell"')
    assert page.index('</main>') < page.index('</div><!-- /#paper -->')
    print("test_page_ships_sheet_ground: OK")


def test_grid_gone_at_every_layer(page: str) -> None:
    """No 24px drafting grid at any theme layer, and the fixed .sheet-frame
    (CSS and markup, including its .sf-mark corners) is deleted outright."""
    assert 'background-size: 24px 24px' not in page, "24px grid still served"
    assert 'sheet-frame' not in page, ".sheet-frame still served"
    assert 'sf-mark' not in page, "legacy .sf-mark corner marks still served"
    print("test_grid_gone_at_every_layer: OK")


def test_mode_diff_paper_widens(page: str) -> None:
    """Diff mode widens the sheet with the shell it wraps — the mode-scoped
    override ships in the static CSS (served identically in every mode)."""
    assert '.mode-diff #paper { max-width: min(95vw, 1600px); }' in page, \
        "page missing: .mode-diff #paper widening rule"
    print("test_mode_diff_paper_widens: OK")


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(REVIEW_INPUT_R1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        page = get_text(base, "/")
        test_page_ships_sheet_ground(page)
        test_grid_gone_at_every_layer(page)
        test_mode_diff_paper_widens(page)
    print("\nOK (3 tests)")


if __name__ == "__main__":
    main()
