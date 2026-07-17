#!/usr/bin/env python3
"""Frontend v2 phase 1 — served-page integration tests (sheet ground, carried
approvals).

This is the phase's shared test file: the review fixtures below and the
subprocess + urllib boot are the harness later phase-1 tasks (recap,
between-rounds) extend with their own page-ships checks. Task 1's
coverage is the sheet ground: the served review page frames the shell in a
bounded #paper sheet — edge border, 1px inner rule at 7px inset, aria-hidden
coordinate/corner decoration — on a flat --table ground, the 24px drafting
grid and the fixed .sheet-frame are gone at every layer, and diff mode widens
the sheet in lockstep with the shell. Task 2 adds the carried-approval
surface: round >= 2 `approved_ids` members collapse to head-only carried
cards (mono APPROVED mini-stamp, read-only reveal, withdraw-to-pending), and
the submit payload keeps recording them as `"verdict": "approved"`. Task 3
adds the transmittal slip: a pure builder over the review-input, mounted
between the ledger and #review-cards on round > 1 in review mode only,
attributing each section — `revised to your note` (diff answering an open
note), bare `revised` (silent diff), `flagged & unreviewed` (error before
warn annotations), `approved & unchanged` (carried) — with every row
jump-linking through activateReviewCard. Task 4 adds the recap overlay as
the submit gate: a hidden dialog indexing every section (id, title, verdict
dot + label, note count), toggled by `o`, closed by Escape, opened by
btn-submit's ready click in review/diff mode; only its `confirm & submit`
control calls submitReview(false), while btn-skip keeps submitReview(true)
and Q&A keeps its direct submitQA(false) path. Task 5 replaces the spinner
with the between-rounds card: submitReview snapshots the just-submitted
changes/info rows ({sectionTitle, type, note}) from rState before the POST,
the 'processing' SSE handler renders #processing-view as a pulsing dot over
`REV 0N submitted — the agent is revising` plus those rows verbatim, and
zero rows (or a qa submit, which never snapshots) fall back to the minimal
processing line; the snapshot is deliberately in-memory only, so a tab
reload during revision re-boots into the prior round exactly as before.
Task 6 closes the phase with docs alignment: DESIGN.md documents the shipped
surface (sheet ground values, transmittal row grammar + attribution rule,
recap gate, between-rounds state, carried approvals) with zero grid-paper/
sheet-frame/spinner residue, and the pre-jig draft plan is deleted so
PLAN.md is the branch's only live plan artifact.

These are wiring checks against the served page (the HTML constant is static,
so one review-mode boot serves every mode's CSS/JS; GET /input carries the
round data that drives which sections render carried); rendered layout is a
browser check, not a subprocess + urllib one. The Task 6 checks are static
file assertions — no server boot.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, get_text, launch_server, post  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

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


def test_round2_serves_transmittal_slip(page: str, data: dict) -> None:
    """Cap: the transmittal slip ships for the round-2 fixture. GET /input
    carries the slip's raw material intact — per-section `diff`,
    `open_notes`, `annotations`, and the top-level `approved_ids` — and the
    page ships the pure builder with both attribution branches (`revised to
    your note` for a diff answering an open note, bare `revised` for a
    silent diff), flag rows partitioned error before warn, `approved &
    unchanged` rows for carried members, and jump wiring routed through
    activateReviewCard (whose carried branch scrolls + reveals rather than
    activates)."""
    # /input carries the raw material for every attribution branch intact.
    by_id = {s["id"]: s for s in data["sections"]}
    fixture = {s["id"]: s for s in REVIEW_INPUT_R2["sections"]}
    assert data["approved_ids"] == REVIEW_INPUT_R2["approved_ids"], data
    assert by_id["s4"]["diff"] == fixture["s4"]["diff"], by_id["s4"]
    assert by_id["s4"]["open_notes"] == fixture["s4"]["open_notes"], by_id["s4"]
    assert by_id["s5"]["diff"] == fixture["s5"]["diff"], by_id["s5"]
    assert "open_notes" not in by_id["s5"], by_id["s5"]
    assert by_id["s6"]["annotations"] == fixture["s6"]["annotations"], by_id["s6"]
    assert by_id["s7"]["annotations"] == fixture["s7"]["annotations"], by_id["s7"]

    # The slip mounts between the ledger and the card list.
    assert page.index('id="ledger"') < page.index('id="transmittal"') \
        < page.index('id="review-cards"'), "slip must mount between ledger and cards"

    # The page ships the pure builder, guarded to review mode + round > 1.
    assert 'function transmittalHTML(' in page, "page missing transmittalHTML"
    assert "if (!data || data.mode !== 'review' || !(data.round > 1)) return '';" in page, \
        "transmittalHTML missing its round > 1 + review-mode guard"
    # Both attribution branches of a revised row ship as one decision.
    assert "noted ? 'revised to your note' : 'revised'" in page, \
        "slip missing the revised-to-your-note / bare-revised attribution"
    # Flag rows come from error/warn annotations, error partition first.
    assert 'const FLAG_RANK = { error: 0, warn: 1 };' in page, \
        "slip missing the error/warn flag ranking"
    assert "flaggedErr.map(s => row(s, 'tr-flag-error', '&#9873;', 'flagged &amp; unreviewed'))" in page
    assert "flaggedWarn.map(s => row(s, 'tr-flag-warn', '&#9873;', 'flagged &amp; unreviewed'))" in page
    assert page.index("flaggedErr.map") < page.index("flaggedWarn.map"), \
        "error flags must row before warn flags"
    # Carried approvals close the slip as approved & unchanged.
    assert "carried.map(s => row(s, 'tr-approved', '&#9635;', 'approved &amp; unchanged'))" in page
    # Every row jump-links through activateReviewCard — its carried branch
    # scrolls + reveals rather than activates, so carried targets behave.
    assert "panel.querySelectorAll('.transmittal-row').forEach(btn => {" in page
    assert "btn.addEventListener('click', () => activateReviewCard(btn.dataset.target));" in page
    # initReview renders the slip alongside the ledger.
    assert 'renderTransmittal();' in page, "initReview must render the slip"
    print("test_round2_serves_transmittal_slip: OK")


def test_round1_zero_transmittal_markers(page: str, data: dict) -> None:
    """Cap: the slip is guarded to round > 1 and review mode — the round-1
    fixture serves an empty, hidden mount, zero rendered slip rows, and a
    builder whose only render path bails on round 1 or a non-review mode."""
    assert data["round"] == 1, data
    # The static mount ships empty and hidden — no slip markers rendered.
    assert ('<nav class="transmittal" id="transmittal" '
            'aria-label="What changed this round" style="display:none"></nav>') in page, \
        "transmittal mount must ship empty and hidden"
    assert not re.search(r'class="transmittal-row [a-z-]+"', page), \
        "static page must never carry a rendered slip row"
    # The only populate path is the guarded pure builder …
    assert "if (!data || data.mode !== 'review' || !(data.round > 1)) return '';" in page
    # … and the mount collapses back to hidden when the builder returns ''.
    assert "if (!html) { panel.style.display = 'none'; panel.innerHTML = ''; return; }" in page
    print("test_round1_zero_transmittal_markers: OK")


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


def test_page_ships_recap_overlay(page: str) -> None:
    """Cap: the served page ships the recap overlay container — a hidden
    dialog whose grid indexes every section (id, title, verdict dot + label,
    note count) with a `confirm & submit` control — and the kbd legend gains
    the `o` shortcut row, with `o`-toggle, Escape-close, and row
    close-and-activate wiring shipped in the review keydown branch."""
    # Static container: hidden dialog, empty grid mount, confirm control.
    assert ('<div class="recap-overlay" id="recap-overlay" role="dialog" '
            'aria-modal="true" aria-labelledby="recap-title" '
            'style="display:none">') in page, \
        "page missing the hidden recap overlay dialog"
    assert '<div class="recap-grid" id="recap-grid"></div>' in page, \
        "recap grid mount must ship empty"
    assert ('<button type="button" class="btn-submit ready" id="recap-confirm">'
            'confirm &amp; submit</button>') in page, \
        "recap overlay missing its confirm & submit control"
    # Rows exist only in the builder's source string — never pre-rendered
    # into the static page (the grid mount above ships empty).
    assert page.count('class="recap-row"') == 1, \
        "static page must never carry a rendered recap row"
    # The row builder indexes all four columns: id, title, verdict dot +
    # label (one shared slot map), and active-note count.
    assert 'function recapRowsHTML()' in page, "page missing recapRowsHTML"
    assert "approved: { dot: 'dot-approved', cls: 'rv-approved', label: 'approved' }" in page
    assert "changes: { dot: 'dot-changes', cls: 'rv-changes', label: 'changes' }" in page
    assert "info: { dot: 'dot-info', cls: 'rv-info', label: 'info' }" in page
    assert "pending: { dot: 'dot-idle', cls: 'rv-pending', label: 'pending' }" in page
    assert "const v = RECAP_VERDICTS[deriveVerdict(s.id)] || RECAP_VERDICTS.pending;" in page, \
        "recap rows must derive their verdict slot per section"
    assert "const notes = activeComments(s.id).length;" in page, \
        "recap rows must count active comments"
    assert "'<span class=\"recap-id\">' + esc(s.id) + '</span>'" in page, \
        "recap row missing its section-id column"
    assert "'<span class=\"recap-row-title\">' + esc(s.title) + '</span>'" in page, \
        "recap row missing its title column"
    assert "'<span class=\"recap-verdict ' + v.cls + '\">" in page, \
        "recap row missing its verdict column"
    assert "<span class=\"dot ' + v.dot + '\" aria-hidden=\"true\"></span>' + v.label" in page, \
        "recap verdict column missing its dot + label pair"
    assert "(notes ? notes + ' note' + (notes === 1 ? '' : 's') : '&mdash;')" in page, \
        "recap row missing its note-count column"
    # Q&A ships no recap: openRecap bails without REVIEW_DATA (or with the
    # review view hidden, e.g. processing).
    assert "if (!REVIEW_DATA || el('review-view').style.display === 'none') return;" in page, \
        "openRecap missing its review-mode guard"
    # A row click closes the overlay and activates its section.
    assert ("btn.addEventListener('click', () => "
            "{ closeRecap(); activateReviewCard(btn.dataset.target); });") in page, \
        "recap rows must close-and-activate on click"
    # The kbd legend gains the `o` row.
    assert '<dt><kbd>o</kbd></dt><dd>recap overlay (review)</dd>' in page, \
        "kbd legend missing the `o` shortcut row"
    # `o` toggles and Escape closes, wired inside the review keydown branch
    # (before the qa branch — never reachable from it).
    kd = page.index("document.addEventListener('keydown'")
    o_idx = page.index("if (e.key === 'o' && !e.metaKey && !e.ctrlKey && !e.altKey) "
                       "{ e.preventDefault(); toggleRecap(); return; }")
    esc_idx = page.index("if (e.key === 'Escape' && recapIsOpen()) { closeRecap(); return; }")
    qa_branch = page.index("if (!REVIEW_DATA && QA_DATA && qState.active)")
    assert kd < o_idx < qa_branch, "`o` toggle must sit in the review keydown branch"
    assert kd < esc_idx < qa_branch, "Escape close must sit in the review keydown branch"
    print("test_page_ships_recap_overlay: OK")


def test_ready_submit_routes_through_recap(page: str) -> None:
    """Cap: the ready-submit path routes through the recap — btn-submit's
    review/diff click branch opens the overlay, and the overlay's confirm
    control is the page's only submitReview(false) call site — while
    btn-skip keeps calling submitReview(true) directly and the qa branch
    keeps its direct submitQA(false) path (hold)."""
    # btn-submit's class-gated click: review/diff opens the gate, qa submits.
    submit_handler = page.index("el('btn-submit').addEventListener('click'")
    open_call = page.index("if (REVIEW_DATA) openRecap();")
    qa_call = page.index("else             submitQA(false);")
    assert submit_handler < open_call < qa_call, \
        "btn-submit click must route review/diff to openRecap, qa to submitQA(false)"
    # Only the overlay's confirm control invokes submitReview(false) — the
    # statement form (trailing `;`) counts call sites, not prose comments.
    assert page.count("submitReview(false);") == 1, \
        "submitReview(false) must have exactly one call site (the recap confirm)"
    confirm_handler = page.index("el('recap-confirm').addEventListener('click'")
    close_wiring = page.index("el('recap-close').addEventListener('click', closeRecap);")
    assert confirm_handler < page.index("submitReview(false);") < close_wiring, \
        "the single submitReview(false) call site must be the recap confirm handler"
    # The confirm control is class-gated like btn-submit, so a recap opened
    # mid-review via `o` can't submit a round the bottom bar wouldn't.
    assert "if (el('recap-confirm').classList.contains('disabled')) return;" in page, \
        "recap confirm must stay class-gated"
    assert "el('recap-confirm').className = 'btn-submit ' + (ready ? 'ready' : 'disabled');" in page, \
        "recap confirm must mirror btn-submit readiness at open"
    # The early-submit escape hatch is untouched: btn-skip → submitReview(true).
    skip_handler = page.index("el('btn-skip').addEventListener('click'")
    skip_call = page.index("if (REVIEW_DATA) submitReview(true);")
    assert skip_handler < skip_call < submit_handler, \
        "btn-skip must keep calling submitReview(true) directly"
    # Q&A keeps exactly one direct submitQA(false) path — the qa click branch.
    assert page.count("submitQA(false);") == 1, \
        "submitQA(false) must keep its single direct call site (qa click branch)"
    print("test_ready_submit_routes_through_recap: OK")


def test_page_ships_between_rounds_card(page: str) -> None:
    """Cap: #processing-view ships as the between-rounds card — a pulsing dot
    (the spinner is replaced, not accompanied), the `REV 0N submitted — the
    agent is revising` heading template, and the verbatim request-row
    template — with the minimal processing line as the zero-row fallback."""
    # Static scaffold: pulsing dot, heading defaulting to the minimal line,
    # and an empty hidden rows mount.
    assert '<div class="processing-dot" aria-hidden="true"></div>' in page, \
        "processing view missing its pulsing dot"
    assert ('<div class="processing-text" id="processing-heading">'
            'Claude is revising…</div>') in page, \
        "processing heading must default to the minimal line"
    assert ('<div class="processing-requests" id="processing-requests" '
            'style="display:none"></div>') in page, \
        "request-rows mount must ship empty and hidden"
    # The spinner is gone at every layer: markup, CSS rule, and keyframes.
    assert 'class="spinner"' not in page, "spinner markup still served"
    assert '.spinner {' not in page, "spinner CSS still served"
    assert 'viva-spin' not in page, "spinner keyframes still served"
    # The dot pulses, with a reduced-motion opt-out.
    assert '@keyframes viva-pulse' in page, "page missing the pulse keyframes"
    assert 'animation: viva-pulse' in page, "processing dot must pulse"
    assert '.processing-dot { animation: none; }' in page, \
        "pulsing dot missing its reduced-motion opt-out"
    # The heading template names the just-submitted round.
    assert ("'REV ' + String(betweenRounds.round).padStart(2, '0') "
            "+ ' submitted — the agent is revising'") in page, \
        "page missing the between-rounds heading template"
    # The verbatim row template: type slot, section title, untruncated note.
    assert "'<div class=\"pr-row pr-' + esc(r.type) + '\">'" in page, \
        "request row missing its type-classed container"
    assert "'<span class=\"pr-type\">' + esc(r.type) + '</span>'" in page, \
        "request row missing its type column"
    assert "'<span class=\"pr-title\">' + esc(r.sectionTitle) + '</span>'" in page, \
        "request row missing its section-title column"
    assert "'<span class=\"pr-note\">' + esc(r.note) + '</span>'" in page, \
        "request row missing its verbatim note column"
    # Zero rows fall back to the minimal processing line.
    assert "heading.textContent = 'Claude is revising…';" in page, \
        "renderProcessingView missing its minimal-line fallback"
    print("test_page_ships_between_rounds_card: OK")


def test_between_rounds_snapshot_wiring(page: str) -> None:
    """Cap: submitReview snapshots the changes/info rows from rState before
    the POST, the 'processing' handler renders from the snapshot, the
    'round' handler consumes it, and qa submits never snapshot — while the
    snapshot stays in-memory only (a tab reload during revision re-boots
    into the prior round, exactly as before)."""
    # In-memory only: declared null, and no web-storage API in the page.
    assert 'let betweenRounds = null;' in page, \
        "page missing the betweenRounds snapshot declaration"
    assert 'localStorage' not in page and 'sessionStorage' not in page, \
        "the snapshot must never persist across a reload"
    # The snapshot is taken inside submitReview, before the POST…
    fn = page.index('function submitReview(early)')
    snap = page.index('snapshotBetweenRounds();')
    post_idx = page.index("fetch('/submit'", fn)
    assert fn < snap < post_idx, \
        "submitReview must snapshot rState before the POST"
    # …and maps activeComments to verbatim {sectionTitle, type, note} rows —
    # settled/empty comments (and approved sections) contribute nothing.
    assert ('activeComments(s.id).map(c => '
            '({ sectionTitle: s.title, type: c.type, note: c.note }))') in page, \
        "snapshot rows must map active comments to {sectionTitle, type, note}"
    # The 'processing' handler renders the view from the snapshot…
    proc = page.index("es.addEventListener('processing'")
    round_h = page.index("es.addEventListener('round'")
    assert proc < page.index('renderProcessingView();', proc) < round_h, \
        "the 'processing' handler must render the between-rounds card"
    # …and the 'round' handler consumes it, so a later processing event with
    # no fresh submit behind it falls back to the minimal line.
    complete_h = page.index("es.addEventListener('complete'")
    assert round_h < page.index('betweenRounds = null;', round_h) < complete_h, \
        "the 'round' handler must consume the snapshot"
    # A qa submit never snapshots — the minimal processing variant (the
    # qa→review hand-off suite holds against this same page).
    qa_fn = page.index('function submitQA(early)')
    qa_end = page.index("el('btn-skip').addEventListener", qa_fn)
    assert 'betweenRounds' not in page[qa_fn:qa_end], \
        "submitQA must not touch the between-rounds snapshot"
    assert 'snapshotBetweenRounds' not in page[qa_fn:qa_end], \
        "submitQA must not snapshot"
    print("test_between_rounds_snapshot_wiring: OK")


def test_design_md_matches_shipped_surface() -> None:
    """Cap: DESIGN.md documents the shipped phase-1 surface. Every value it
    states for the sheet ground is a literal the served HTML also carries
    (--table hexes, the 7px inner rule, the diff-mode widening), the four new
    surfaces (transmittal slip with its attribution rule, recap gate,
    between-rounds state, carried approvals) each have their row grammar /
    gate strings documented, and the retired constructs — grid paper, the
    fixed .sheet-frame, the spinner — leave zero references behind."""
    design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
    html = (ROOT / "server.py").read_text(encoding="utf-8")

    # Sheet ground — each documented value must also be shipped verbatim.
    for literal in ("#060e1a", "#e2e8f1", "inset: 7px", "min(95vw, 1600px)"):
        assert literal in design, f"DESIGN.md missing sheet-ground value {literal!r}"
        assert literal in html, f"DESIGN.md documents {literal!r} but server.py no longer ships it"
    assert "`--table`" in design, "DESIGN.md missing the --table token"
    assert "#paper" in design, "DESIGN.md missing the #paper sheet"
    assert "edge coordinate" in design, "DESIGN.md missing the edge coordinates"

    # Transmittal slip — row grammar plus the attribution rule's two labels.
    assert "revised to your note" in design, "DESIGN.md missing the attributed revised row"
    assert "flagged & unreviewed" in design, "DESIGN.md missing the flagged row"
    assert "approved & unchanged" in design, "DESIGN.md missing the carried row"
    assert "transmittal" in design.lower(), "DESIGN.md missing the transmittal slip"

    # Recap gate, between-rounds state, carried approvals.
    assert "confirm & submit" in design, "DESIGN.md missing the recap confirm control"
    assert "recap" in design.lower(), "DESIGN.md missing the recap overlay"
    assert "submitted — the agent is revising" in design, \
        "DESIGN.md missing the between-rounds heading"
    assert "withdraw approval" in design, "DESIGN.md missing the withdraw control"
    assert "is-carried" in design, "DESIGN.md missing the carried card class"

    # Retired constructs leave no residue: the grid-paper metaphor, the fixed
    # .sheet-frame, the 24px grid, and the deleted spinner (including its
    # border-radius table row).
    assert "grid paper" not in design, "DESIGN.md still says grid paper"
    assert "grid-paper" not in design, "DESIGN.md still says grid-paper"
    assert "sheet-frame" not in design, "DESIGN.md still documents .sheet-frame"
    assert "24px" not in design, "DESIGN.md still carries a 24px grid reference"
    assert "spinner" not in design.lower(), "DESIGN.md still documents the spinner"
    print("test_design_md_matches_shipped_surface: OK")


def test_draft_plan_superseded() -> None:
    """Cap: the pre-jig draft plan is deleted — PLAN.md is the branch's only
    live plan artifact."""
    draft = ROOT / "docs" / "superpowers" / "plans" / "2026-07-16-frontend-v2-phase1.md"
    assert not draft.exists(), f"superseded draft plan still on the branch: {draft}"
    assert (ROOT / "PLAN.md").is_file(), "PLAN.md (the live plan artifact) is missing"
    print("test_draft_plan_superseded: OK")


def main() -> None:
    # Task 6 docs alignment — static file checks, no server boot.
    test_design_md_matches_shipped_surface()
    test_draft_plan_superseded()

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
        test_round1_zero_transmittal_markers(page, data)
        test_page_ships_recap_overlay(page)
        test_ready_submit_routes_through_recap(page)
        test_page_ships_between_rounds_card(page)
        test_between_rounds_snapshot_wiring(page)

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
        test_round2_serves_transmittal_slip(page, data)

    print("\nOK (14 tests)")


if __name__ == "__main__":
    main()
