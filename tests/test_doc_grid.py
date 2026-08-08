#!/usr/bin/env python3
"""The doc + margin restructure (issue #186) — served-page integration tests.

#185 shipped the catalog's materials on the old accordion. This suite guards
its STRUCTURE: review mode prints the document continuously as a run of
`check gutter | prose | margin` rows, commentary sits beside the passage it
annotates, and the per-section action row is gone.

What each test group holds:

  * **Mode gate.** The restructure is review-mode only. `buildReviewCard` and
    every accordion premise stay exactly as they were for diff mode, because a
    200-hunk changeset read as one continuous print is a worse surface than one
    hunk at a time. This is the assertion that catches someone "simplifying"
    the two paths into one.
  * **Reading order.** Nothing secondary may sit between the reader and the
    prose. The round diff — the widest object on the page, and what a round-2
    run found stacked at full width above the text — ships collapsed.
  * **The wasted-space rule.** Both side columns collapse to zero. This is the
    thing most likely to be lost, because the composite it was drawn from
    reserves both columns on every row.
  * **The schema contract.** `CHECK_KINDS` is injected from `scripts/schema.py`,
    never restated in JS. The registry fails open — an unregistered kind is
    silently invisible — so a hand-kept second copy would fail open in the very
    surface that draws `checks N/M`.
  * **Keyboard reach.** With the action row gone, a section carrying no notes
    would hold no focusable element at all. Approve stays on every section.

Rendered layout is out of scope here, same as the rest of the suite: these are
markup, CSS-rule and wire-format checks against the page the server actually
serves.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import schema  # noqa: E402
from _server_harness import get, get_text, launch_server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


REVIEW_INPUT_R2 = {
    "round": 2,
    "mode": "review",
    "doc_file": "docs/premortem.md",
    "approved_ids": ["s1"],
    "sections": [
        {"id": "s1", "title": "Regrows the middle layer",
         "content": "## Regrows the middle layer\n\nLosing it is quiet."},
        {"id": "s2", "title": "One human, N threads",
         "content": "## One human, N threads\n\nThe counter-math: 60-135 min/day of judgment.\n\n"
                    "But the baseline is not zero.",
         "annotations": [
             {"id": "a1", "kind": "source-check", "severity": "warn",
              "message": "no source for figure",
              "anchor": "60-135 min/day of judgment"},
             {"id": "a2", "kind": "headings-present", "severity": "info",
              "message": "§4 defines cold start", "result": "confirmed"},
         ],
         "open_notes": [
             {"cid": "s2-c1", "status": "open", "quote": "60-135 min/day of judgment",
              "exchanges": [{"round": 1, "verdict": "changes",
                             "note": "Cite the numbers.", "response": "Marked as estimates."}]},
         ],
         "diff": [{"op": "-", "text": "old wording"}, {"op": "+", "text": "new wording"}]},
        {"id": "s3", "title": "The docket view",
         "content": "## The docket view\n\nEvery row is an action you owe someone."},
    ],
}


def test_review_mode_prints_the_document(page: str, data: dict) -> None:
    """Cap: review mode routes to the doc builder and renders every section up
    front. Continuous print has nothing to open, so there is nothing to render
    lazily — a reader of a document review is reading the document."""
    assert data["mode"] == "review" and data["round"] == 2, data
    assert "const asDoc = REVIEW_DATA.mode === 'review';" in page, \
        "the doc print must be gated on review mode"
    assert "container.classList.toggle('doc', asDoc);" in page, \
        ".doc is what arms every grid rule — it must be stamped by initReview"
    assert "const card = asDoc ? buildDocSection(s, i)" in page, \
        "review mode must build doc sections"
    assert "if (asDoc) REVIEW_DATA.sections.forEach(s => _ensureRendered(s.id));" in page, \
        "continuous print must render every section up front"
    assert "function buildDocSection(section, index)" in page, "page missing buildDocSection"
    print("test_review_mode_prints_the_document: OK")


def test_diff_mode_keeps_the_accordion(page: str) -> None:
    """Hold: the restructure is additive. Every accordion premise diff mode
    depends on is untouched — the card head is still a real <button> with
    aria-expanded, the body region still animates, and the carried gate still
    routes to buildCarriedCard. A hunk is not prose: it has no margin to
    annotate and no measure to hold."""
    assert ('<button type="button" class="card-head" aria-expanded="false" '
            'aria-controls="rbody-${section.id}">') in page, \
        "diff mode's accordion head must stay a disclosure button"
    assert '<div class="card-body-wrap" id="rbody-${section.id}">' in page, \
        "diff mode's accordion body region changed"
    assert '<button type="button" class="action-btn is-approve" id="rbtn-primary-${section.id}">' in page, \
        "diff mode's action row changed"
    assert 'const isCarried = !asDoc && REVIEW_DATA.round > 1 && priorApprovedSet.has(s.id);' in page, \
        "carried cards must stay the accordion's path"
    assert 'isCarried ? buildCarriedCard(s) : buildReviewCard(s)' in page
    print("test_diff_mode_keeps_the_accordion: OK")


def test_nothing_sits_between_the_reader_and_the_prose(page: str) -> None:
    """Cap: the reading-order fix. A round-2 run put the transmittal slip, the
    carried row, two open threads with reply boxes and a 25-line round diff
    above the paragraph all of it was about. Threads and flags now sit BESIDE
    their anchor, and the diff — the widest object on the page — ships
    collapsed above it, never expanded."""
    # The round diff gets a full-width row of its own, shipped collapsed.
    assert "class=\"row wide row-diff\" id=\"rdiffrow-${id}\"" in page, \
        "the round diff needs its own full-width row"
    assert "sec.querySelector('#rdiff-' + id).classList.add('collapsed');" in page, \
        "the round diff must ship collapsed — it is not what the reader opened the document to read"
    # Threads and this round's notes are placed against their anchor's row.
    assert "function rowForAnchor(id, text, occurrence)" in page, \
        "page missing the anchor-to-row resolver"
    assert "const row = t.quote ? rowForAnchor(id, t.quote, 0) : null;" in page, \
        "a carried thread must be placed beside its own quote"
    # The per-section action row and its hint are gone from the doc print; the
    # invitation is printed once at the foot of the whole document.
    assert '<div class="doc-hint" id="doc-hint"' in page, \
        "the select-to-comment hint must be one document-level line"
    print("test_nothing_sits_between_the_reader_and_the_prose: OK")


def test_both_columns_collapse_when_the_document_has_nothing_for_them(page: str) -> None:
    """Cap: the wasted-space rule this story owns. The composite reserves 70px
    of gutter and 300px of margin on every row; production must not. The
    decision is read off the DOM (so a comment made a moment ago counts like a
    thread that shipped with the round) and made once per document — a per-row
    or per-section decision jogs the prose column sideways as you read it."""
    assert "function updateDocColumns()" in page, "page missing the collapse rule"
    assert "doc.classList.toggle('no-gutter', !doc.querySelector('.rg .lchip'));" in page, \
        "the gutter must collapse when no row carries a check chip"
    assert ("doc.classList.toggle('no-margin',\n"
            "    !doc.querySelector('.rm-notes .nt, .rm-notes .annot, .rm-threads .open-thread'));") in page, \
        "the margin must collapse when the document carries no notes"
    # With the margin gone the head row drops to two tracks and the section's
    # own controls print under its heading — pure CSS, so a collapse can never
    # move a focused control between hosts.
    assert re.search(r'\.doc\.no-margin \.row-head\s*\{[^}]*grid-template-columns:\s*'
                     r'var\(--gutter-w\)\s+minmax\(0,\s*72ch\)', page), \
        "a collapsed margin must drop the head row to two tracks"
    assert re.search(r'\.doc\.no-margin \.row-head \.rm\s*\{[^}]*grid-column:\s*2', page), \
        "the head row's controls must reflow under the heading, not disappear"
    print("test_both_columns_collapse_when_the_document_has_nothing_for_them: OK")


def test_check_kinds_is_injected_never_restated(page: str) -> None:
    """Cap: the frontend reads scripts/schema.py's registry rather than keeping
    a copy. CHECK_KINDS fails open by design — an unregistered kind raises
    nowhere, it just becomes invisible — so a second, hand-kept copy would fail
    open the same silent way in the one surface that draws `checks N/M`."""
    expected = "const CHECK_KINDS = " + json.dumps(list(schema.CHECK_KINDS)) + ";"
    assert expected in page, f"CHECK_KINDS must be injected from schema.py, got neither {expected!r}"
    assert "__CHECK_KINDS__" not in page, "the injection placeholder must be substituted"
    # And it is the registry the spec table and the segmented rule ask.
    assert "CHECK_KINDS.includes(a.kind)" in page, \
        "the checks row and the balance must both read the injected registry"
    print("test_check_kinds_is_injected_never_restated: OK")


def test_every_section_keeps_a_focusable_control(page: str) -> None:
    """Cap: keyboard reach survives the action row's removal. Sections no longer
    collapse, so the head is a heading and not a disclosure button — which
    leaves a section carrying zero notes with zero focusable elements unless
    approve stays. It stays, in the margin, and the palette is a second path to
    the same verb rather than the only one."""
    assert 'class="nt-btn is-pri" id="rbtn-primary-${id}"' in page, \
        "every doc section must keep its own approve control"
    assert 'class="nt-btn is-quiet" id="rcmtnote-${id}"' in page, \
        "every doc section must keep its own add-note control"
    # The heading is a heading, and the section is labelled by it.
    assert 'sec.setAttribute(\'aria-labelledby\', \'rhead-\' + id);' in page, \
        "a doc section must be labelled by its own heading"
    assert '<h2 class="doc-head" id="rhead-${id}">' in page, \
        "the section head must be a heading, not a button, once nothing collapses"
    # No permanently-true disclosure state: aria-expanded belongs to the
    # accordion, and the doc print has nothing to expand.
    assert page.count('class="card-head" aria-expanded="false" aria-controls=') == 2, \
        "aria-expanded must stay on the two accordion heads only (review card, QA card)"
    # ⌘K is a second path, and it never stacks on another modal.
    assert "if (paletteIsOpen()) closePalette(); else openPalette();" in page
    assert "if (prefsIsOpen() || (REVIEW_DATA && recapIsOpen())) return;" in page, \
        "the palette must not open over the prefs panel or the recap gate"
    print("test_every_section_keeps_a_focusable_control: OK")


def test_segmented_rule_states_its_counts(page: str) -> None:
    """Cap: honest proportions, auditable. One function owns the denominator,
    the order is fixed (judgment -> facts -> settled) as the colorblind-safe
    second encoding, and the raw counts ride out in the aria-label so the claim
    can be checked rather than taken on faith. A section with nothing open gets
    the thin hairline: a state bar there is decoration."""
    assert "function sectionBalance(section)" in page, "page missing the balance function"
    assert "return { judgment, facts, settled };" in page
    # Fixed order, in the markup the reader gets.
    order = page.index("seg('seg-judgment', bal.judgment) + seg('seg-fact', bal.facts)")
    assert order > 0 and "seg('seg-settled', bal.settled)" in page[order:order + 200], \
        "segments must print judgment -> facts -> settled, always in that order"
    assert "'open: ' + bal.judgment + ' judgment, ' + bal.facts + ' fact'" in page, \
        "the segmented rule must state its raw counts in the aria-label"
    assert "if (!bal.judgment && !bal.facts) return '<div class=\"rule-s\"></div>';" in page, \
        "a section with nothing open takes the thin settled hairline, not a bar"
    # The footer carries the same grammar for the whole document.
    assert "function renderFootSeg(sections, total)" in page, "page missing the footer balance"
    assert "'document balance: '" in page
    print("test_segmented_rule_states_its_counts: OK")


def test_margin_notes_and_pins_are_numbered_together(page: str) -> None:
    """Cap: the pin in the text and the note in the margin carry the same
    number, because ONE pass assigns both — two passes could disagree about
    which span is note 3. Notes order by the row their anchor lands in, so the
    numbering runs down the page the way the reader reads."""
    assert "function markAndPin(id, ordered)" in page, "page missing the mark+pin pass"
    assert "if (isDocMode()) return;" in page, \
        "renderHighlights must yield the doc print's marking to markAndPin"
    assert ".sort((a, b) => a.row - b.row || a.seq - b.seq);" in page, \
        "notes must order by the row their anchor lands in"
    # A thread owns a reply textarea, so it is placed once and never rebuilt.
    assert "if (node.parentElement !== host) host.appendChild(node);" in page, \
        "a thread already in the right cell must be left alone — moving it blurs its reply box"
    assert "sec.querySelectorAll('.rm-notes .nt').forEach(n => n.remove());" in page, \
        "only the dynamic note hosts may be rebuilt on sync"
    # One builder, two surfaces.
    assert "function openThreadItemHTML(t)" in page and "ex.map(openThreadItemHTML).join('')" in page, \
        "the accordion and the margin must build a thread from one function"
    print("test_margin_notes_and_pins_are_numbered_together: OK")


def test_anchored_span_wears_the_reviewers_touch(page: str) -> None:
    """Cap: the ink discipline, applied to the restructure. `--touch` is the
    reviewer's touch ON THE TEXT and nothing else, so in the doc print an
    anchored span is catalog yellow and the PIN — not the highlight — carries
    whose note it is and what kind. Red and green stay confined to the
    suggestion fence, where diff semantics already own them."""
    assert re.search(r'\.doc mark\[class\^="cmt-hl-"\]\s*\{\s*background:\s*var\(--touch\)', page), \
        "an anchored span in the doc print must wear catalog yellow"
    assert re.search(r'\.pin-you\s*\{\s*background:\s*var\(--acc\)', page), \
        "the reviewer's pin takes their own party ink"
    assert re.search(r'\.pin-author\s*\{[^}]*border:\s*1\.5px solid var\(--soft\)', page), \
        "the author's pin is outlined in the neutral ink, not the reviewer's"
    fence = page[page.index('/* ─── Suggestion fence'):page.index('/* ─── Command palette')]
    assert 'rgba(209,36,47' in fence and 'rgba(26,127,55' in fence, \
        "the fence keeps diff red/green"
    assert '--touch' not in fence, "the fence must not spend the reviewer's yellow"
    print("test_anchored_span_wears_the_reviewers_touch: OK")


def test_settled_token_defined_once_per_theme_block(page: str) -> None:
    """Cap: the segmented rule's settled ink is a real token in all three theme
    blocks. The composite's `#e3e4e2` is nearly white on charcoal, so the dark
    side needs its own value rather than the light one carried across — the
    same rule every party ink already follows."""
    assert page.count('--settled:') == 3, \
        "--settled must be defined once per theme block (light, media dark, explicit dark)"
    assert re.search(r'--settled:\s+#e3e4e2;', page), "light --settled missing"
    assert page.count('--settled: #3a3e41;') == 2, \
        "both dark blocks must carry the same dark --settled (test_theme_toggle enforces the pair)"
    print("test_settled_token_defined_once_per_theme_block: OK")


def test_round2_wire_shape_unchanged(base: str) -> None:
    """Hold: no schema change. The restructure is a rendering change over the
    shapes #184 already ships — GET /input serves the same round, the same
    per-section `diff`/`open_notes`/`annotations`, and the same `approved_ids`
    the accordion read."""
    data = get(base, "/input")
    by_id = {s["id"]: s for s in data["sections"]}
    assert data["approved_ids"] == ["s1"], data
    assert by_id["s2"]["open_notes"][0]["cid"] == "s2-c1", by_id["s2"]
    assert by_id["s2"]["diff"] == REVIEW_INPUT_R2["sections"][1]["diff"], by_id["s2"]
    assert {a["kind"] for a in by_id["s2"]["annotations"]} == {"source-check", "headings-present"}
    print("test_round2_wire_shape_unchanged: OK")


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        viva.mkdir()
        inp = viva / "review-input-r2.json"
        inp.write_text(json.dumps(REVIEW_INPUT_R2))
        with launch_server(inp, viva / "out2.json", cwd=tmp) as base:
            page = get_text(base)
            data = get(base, "/input")
            test_review_mode_prints_the_document(page, data)
            test_diff_mode_keeps_the_accordion(page)
            test_nothing_sits_between_the_reader_and_the_prose(page)
            test_both_columns_collapse_when_the_document_has_nothing_for_them(page)
            test_check_kinds_is_injected_never_restated(page)
            test_every_section_keeps_a_focusable_control(page)
            test_segmented_rule_states_its_counts(page)
            test_margin_notes_and_pins_are_numbered_together(page)
            test_anchored_span_wears_the_reviewers_touch(page)
            test_settled_token_defined_once_per_theme_block(page)
            test_round2_wire_shape_unchanged(base)
    print("OK")


if __name__ == "__main__":
    main()
