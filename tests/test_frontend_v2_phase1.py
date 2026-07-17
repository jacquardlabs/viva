#!/usr/bin/env python3
"""Frontend v2 phase 1 — served-page integration tests (sheet ground, carried
approvals).

This is the phase's shared test file: the review fixtures below and the
subprocess + urllib boot are the harness later phase-1 tasks (transmittal,
recap, between-rounds) extend with their own page-ships checks. Task 1's
coverage is the sheet ground: the served review page frames the shell in a
bounded #paper sheet — edge border, 1px inner rule at 7px inset, aria-hidden
coordinate/corner decoration — on a flat --table ground, the 24px drafting
grid and the fixed .sheet-frame are gone at every layer, and diff mode widens
the sheet in lockstep with the shell. Task 2 adds the carried-approval
surface: round >= 2 `approved_ids` members collapse to head-only carried
cards (mono APPROVED mini-stamp, read-only reveal, withdraw-to-pending), and
the submit payload keeps recording them as `"verdict": "approved"`.

These are wiring checks against the served page (the HTML constant is static,
so one review-mode boot serves every mode's CSS/JS; GET /input carries the
round data that drives which sections render carried); rendered layout is a
browser check, not a subprocess + urllib one.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, get_text, launch_server, post  # noqa: E402

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

# Round-2 review fixture — the carried-card surface (Task 2) and the raw
# material later tasks (transmittal slip jumps, recap labels) key on:
# 3 carried approvals, 1 revision answering a reviewer note (diff +
# open_notes), 1 silent revision (diff only), and 2 flagged sections
# (annotations only).
REVIEW_INPUT_R2 = {
    "mode": "review",
    "doc_file": "docs/example.md",
    "round": 2,
    "approved_ids": ["s1", "s2", "s3"],
    "sections": [
        {"id": "s1", "title": "Overview", "content": "## Overview\n\nWhat this is."},
        {"id": "s2", "title": "Goals", "content": "## Goals\n\n- ship it"},
        {"id": "s3", "title": "Non-goals", "content": "## Non-goals\n\n- no rewrite"},
        {"id": "s4", "title": "Design", "content": "## Design\n\nRetries 5x.",
         "diff": [{"op": "@", "text": "@@ -1,2 +1,2 @@"},
                  {"op": "-", "text": "Retries 3x."},
                  {"op": "+", "text": "Retries 5x."}],
         "open_notes": [{"cid": "s4-c1", "quote": "Retries 3x",
                         "exchanges": [{"round": 1, "verdict": "changes",
                                        "note": "5x not 3x",
                                        "response": "Bumped to 5x."}]}]},
        {"id": "s5", "title": "Rollout", "content": "## Rollout\n\nBehind a flag.",
         "diff": [{"op": "+", "text": "Behind a flag."}]},
        {"id": "s6", "title": "Risks", "content": "## Risks\n\nCache stampede.",
         "annotations": [{"kind": "checklist", "severity": "warn",
                          "message": "no mitigation listed"}]},
        {"id": "s7", "title": "Open questions", "content": "## Open questions\n\nTBD.",
         "annotations": [{"kind": "drift", "severity": "info",
                          "message": "restates a risk", "anchor": "s6"}]},
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


def test_round2_serves_carried_markup(page: str, data: dict) -> None:
    """Cap: the round-2 fixture serves the carried-card surface. GET /input
    carries round 2 plus every `approved_ids` member (each resolving to a
    served section), and the page ships the builder that renders exactly
    those members as collapsed carried cards: `carried` head marker, mono
    `APPROVED` mini-stamp, `unchanged since your stamp — show` reveal over a
    hidden read-only body, and the withdraw-approval control that clears the
    verdict back to pending."""
    # /input drives the carried rendering: round >= 2 and each carried member
    # is a served section.
    assert data["round"] == 2, data
    assert data["approved_ids"] == ["s1", "s2", "s3"], data
    served_ids = {s["id"] for s in data["sections"]}
    for sid in data["approved_ids"]:
        assert sid in served_ids, f"approved id {sid} has no served section"
    # The fixture mix later tasks build on stays intact: one revision
    # answering a note, one silent revision, two flagged sections.
    by_id = {s["id"]: s for s in data["sections"]}
    assert by_id["s4"].get("diff") and by_id["s4"].get("open_notes"), by_id["s4"]
    assert by_id["s5"].get("diff") and not by_id["s5"].get("open_notes"), by_id["s5"]
    assert by_id["s6"].get("annotations") and by_id["s7"].get("annotations")

    # The page ships the carried builder, gated to round >= 2 members.
    assert 'const isCarried = REVIEW_DATA.round > 1 && priorApprovedSet.has(s.id);' in page, \
        "page missing the round >= 2 carried gate"
    assert 'isCarried ? buildCarriedCard(s) : buildReviewCard(s)' in page, \
        "initReview must route carried sections to buildCarriedCard"
    assert 'function buildCarriedCard(section)' in page, "page missing buildCarriedCard"
    assert "card.className = 'card is-carried';" in page, "carried card missing is-carried"
    assert '<span class="carried-marker">carried</span>' in page, \
        "carried head missing its `carried` marker"
    assert '<span class="carried-stamp">APPROVED</span>' in page, \
        "carried head missing the mono APPROVED mini-stamp"
    assert 'unchanged since your stamp &mdash; show' in page, \
        "carried head missing the `unchanged since your stamp — show` reveal"
    assert '<div class="carried-body" id="rcarried-body-${section.id}" hidden>' in page, \
        "carried content must start hidden (head-only line)"
    assert '<span aria-hidden="true">&times;</span> withdraw approval' in page, \
        "carried head missing the withdraw-approval control"
    # Withdraw clears the verdict back to pending and swaps in a normal
    # accordion card, in place (document order is canonical).
    assert 'function withdrawApproval(id)' in page, "page missing withdrawApproval"
    assert 'withdrawApproval(section.id)' in page, "withdraw control not wired"
    assert 'old.replaceWith(buildReviewCard(section));' in page, \
        "withdraw must swap the carried card for a normal accordion card in place"
    # The carry-forward submit invariant's client half stays intact: prior
    # approvals pre-populate rState so deriveVerdict submits them approved.
    assert "rState.verdicts[id] = { verdict: 'approved', note: '' };" in page, \
        "prior-approval pre-population must survive the carried-card render"
    print("test_round2_serves_carried_markup: OK")


def test_round2_submit_records_carried_approved(base: str, viva: Path) -> None:
    """Cap: POST /submit for the round-2 fixture records carried sections as
    `"verdict": "approved"` in the round output exactly as today's
    carry-forward does — bare {id, verdict} entries, no comments. The payload
    mirrors what submitReview derives client-side: carried sections stay
    approved, revised/flagged ones carry their own verdicts."""
    payload = {"round": 2, "submitted_early": False, "sections": [
        {"id": "s1", "verdict": "approved"},
        {"id": "s2", "verdict": "approved"},
        {"id": "s3", "verdict": "approved"},
        {"id": "s4", "verdict": "approved"},
        {"id": "s5", "verdict": "changes", "comments": [
            {"cid": "s5-c1", "type": "changes", "note": "name the flag",
             "open": True, "settled": False}]},
        {"id": "s6", "verdict": "approved"},
        {"id": "s7", "verdict": "info", "comments": [
            {"cid": "s7-c1", "type": "info", "note": "which risks?",
             "open": True, "settled": False}]},
    ]}
    post(base, "/submit", payload)
    out = json.loads((viva / "out2.json").read_text())
    assert out["round"] == 2, out
    for sid in REVIEW_INPUT_R2["approved_ids"]:
        sec = next(s for s in out["sections"] if s["id"] == sid)
        assert sec == {"id": sid, "verdict": "approved"}, \
            f"carried section must record exactly as today's carry-forward: {sec}"
    # Non-carried verdicts pass through beside them untouched.
    s5 = next(s for s in out["sections"] if s["id"] == "s5")
    assert s5["verdict"] == "changes" and s5["comments"][0]["cid"] == "s5-c1", s5
    print("test_round2_submit_records_carried_approved: OK")


def test_round1_zero_carried_markers(page: str, data: dict) -> None:
    """Hold: the round-1 fixture (no approved_ids) serves zero carried
    markers — GET /input carries no approved ids and the carried builder is
    gated to round >= 2 — and the accordion card markup is unchanged."""
    assert data["round"] == 1, data
    assert data.get("approved_ids") == [], data
    # The only carried-render path is gated on round > 1 membership, so a
    # round-1 boot can never produce a carried card.
    assert 'const isCarried = REVIEW_DATA.round > 1 && priorApprovedSet.has(s.id);' in page
    # Unchanged accordion card markup (head button + body region + actions).
    assert ('<button type="button" class="card-head" aria-expanded="false" '
            'aria-controls="rbody-${section.id}">') in page, \
        "round-1 accordion card head changed"
    assert '<div class="card-body-wrap" id="rbody-${section.id}">' in page, \
        "round-1 accordion card body changed"
    assert '<button type="button" class="action-btn is-approve" id="rbtn-primary-${section.id}">' in page, \
        "round-1 accordion approve action changed"
    print("test_round1_zero_carried_markers: OK")


def main() -> None:
    # Round-1 boot — sheet ground plus the zero-carried hold. Its own tmp dir:
    # wait_for_url polls for `server.url` beside the output file, so each boot
    # gets a fresh directory rather than racing a stale url file.
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(REVIEW_INPUT_R1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        page = get_text(base, "/")
        data = get(base, "/input")
        test_page_ships_sheet_ground(page)
        test_grid_gone_at_every_layer(page)
        test_mode_diff_paper_widens(page)
        test_round1_zero_carried_markers(page, data)

    # Round-2 boot — carried cards collapse in place, submit stays approved.
    tmp2 = Path(tempfile.mkdtemp())
    viva2 = tmp2 / ".viva"
    viva2.mkdir()
    (viva2 / "in2.json").write_text(json.dumps(REVIEW_INPUT_R2))
    with launch_server(viva2 / "in2.json", viva2 / "out2.json", cwd=tmp2) as base:
        page = get_text(base, "/")
        data = get(base, "/input")
        test_round2_serves_carried_markup(page, data)
        test_round2_submit_records_carried_approved(base, viva2)

    print("\nOK (6 tests)")


if __name__ == "__main__":
    main()
