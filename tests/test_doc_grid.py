#!/usr/bin/env python3
"""The doc + margin restructure (issue #186) — served-page integration tests.

#185 shipped the catalog's materials on the old accordion. This suite guards
its STRUCTURE: review mode prints the document continuously as a run of
`check gutter | prose | margin` rows, commentary sits beside the passage it
annotates, and the per-section action row is gone.

What each test group holds:

  * **The seam.** The restructure is TWO things. `.doc` is the GRAMMAR —
    three-column rows, margin notes with pins, the glyph rail, the spec table,
    per-note verbs — and every surface that renders a section wears it.
    `.print` is CONTINUOUS PRINT — all sections open at once, a settled one
    dimming in place — and review wears it alone, because a 200-hunk changeset
    read as one print is a worse surface than one hunk at a time. They shipped
    conflated behind one class and one `isDocMode()`, which is exactly why the
    restructure reached review mode only. These are the assertions that catch
    someone re-fusing them.
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
    assert "const asDoc = isContinuousPrint();" in page, \
        "the print must be gated on the predicate, not on a re-derived mode test"
    assert "const card = asDoc ? buildDocSection(s, i)" in page, \
        "review mode must build doc sections"
    assert "if (asDoc) REVIEW_DATA.sections.forEach(s => _ensureRendered(s.id));" in page, \
        "continuous print must render every section up front"
    assert "function buildDocSection(section, index)" in page, "page missing buildDocSection"
    print("test_review_mode_prints_the_document: OK")


def test_the_grammar_is_not_the_print(page: str) -> None:
    """Cap: the seam. `.doc` arms the grammar and every card container that
    renders sections gets it; `.print` arms continuous print and only review
    does. Anything print-only is keyed on `.doc-section`, which no other
    surface builds.

    There is deliberately no companion `usesMargin()` predicate: every place
    `isDocMode()` used to branch is reached only from a surface that renders
    sections, and all of them wear the margin, so the honest form of that
    question is no condition at all."""
    assert ("function isContinuousPrint() { return !!(REVIEW_DATA && "
            "REVIEW_DATA.mode === 'review'); }") in page, \
        "the print needs one predicate with one definition"
    assert "function isDocMode(" not in page, "the conflated predicate must not come back"
    assert "function usesMargin(" not in page, \
        "a predicate true at every call site advertises a surface that does not exist"
    assert "container.classList.add('doc');" in page, \
        "the grammar is unconditional for a surface that renders sections"
    assert "container.classList.toggle('print', asDoc);" in page, \
        "continuous print stays review-only"
    assert re.search(r'\.doc\.print\s*\{\s*gap:\s*22px;\s*\}', page), \
        "the print's inter-section rhythm is the print's, not the grammar's"
    print("test_the_grammar_is_not_the_print: OK")


def test_diff_keeps_the_accordion_and_loses_its_chrome(page: str) -> None:
    """Cap: what survives in diff mode and what does not.

    SURVIVES — the ACCORDION. One hunk open at a time, a real <button> head
    with aria-expanded, the animating body region, the carried gate. 52 hunks
    printed at once is a worse surface than one at a time, which is the half of
    the original scoping call that was right.

    DOES NOT — the accordion's CHROME. The annotation strip, the stacked thread
    list, the add-a-note row and the action row all stacked commentary on top
    of the hunk it was about: the same reading-order inversion the print fixed,
    in a narrower frame. They are margin objects now, on both surfaces, and
    they are deleted rather than hidden."""
    assert ('<button type="button" class="card-head" aria-expanded="false" '
            'aria-controls="rbody-${section.id}">') in page, \
        "diff mode's accordion head must stay a disclosure button"
    assert '<div class="card-body-wrap" id="rbody-${section.id}">' in page, \
        "diff mode's accordion body region changed"
    assert 'const isCarried = !asDoc && REVIEW_DATA.round > 1 && priorApprovedSet.has(s.id);' in page, \
        "carried cards must stay the accordion's path"
    assert 'isCarried ? buildCarriedCard(s) : buildReviewCard(s)' in page
    for dead in ('class="actions"', 'class="action-btn', 'class="comment-add-row"',
                 'class="comment-list"', 'function renderCommentList(',
                 'function openThreadHTML(', 'function renderHighlights('):
        assert dead not in page, f"accordion chrome must be deleted, not hidden: {dead}"
    # The open hunk is a row grid with a margin, from the SAME head-row builder
    # the print uses — one copy, so a verb added to a document review cannot go
    # missing from a diff review.
    assert "function docHeadRowHTML(id, proseHTML, opts)" in page, \
        "both builders must raise the head row from one function"
    assert "${docHeadRowHTML(section.id, " in page and "{ skip: true })}" in page, \
        "the accordion's head row must come from the shared builder"
    # `skip` is the accordion's alone: with one hunk open at a time, "not this
    # one, not now" is a real move; in the print it is just reading on.
    assert "const skip = !!(opts && opts.skip);" in page
    # The hunk never borrows the margin's track. `:has()` there would make the
    # first comment on a hunk a 328px re-layout of the lines being commented on.
    assert ".doc.print .row.wide:not(:has(> .rm)) .rp" in page, \
        "the break-out rule belongs to the print, where a wide row is one of many"
    # A pin on a code line LEADS the line and sticks: prose wraps, a diff line
    # scrolls, and a pin set after its anchor was measured at x=1052 inside a
    # 445px pane — nowhere the reviewer will ever see it.
    assert "tail.closest('.d2h-code-line, .d2h-code-side-line')" in page, \
        "a diff pin must attach to the line, not to the character offset"
    assert re.search(r'\.pin-line\s*\{[^}]*position:\s*sticky', page), \
        "a diff pin must survive a horizontal scroll of its own pane"
    # ...and a carried suggestion on a diff line takes the margin's fence, for
    # the same reason a code block does: a del/ins pair spliced into a `+` line
    # reads as neither version of anything.
    assert "mark.closest('pre, .d2h-code-line, .d2h-code-side-line')" in page, \
        "a rendered diff line is a code well too"
    print("test_diff_keeps_the_accordion_and_loses_its_chrome: OK")


def test_nothing_sits_between_the_reader_and_the_prose(page: str) -> None:
    """Cap: the reading-order fix. A round-2 run put the transmittal slip, the
    carried row, two open threads with reply boxes and a 25-line round diff
    above the paragraph all of it was about. Threads and flags now sit BESIDE
    their anchor, and the diff — the widest object on the page — ships
    collapsed above it, never expanded."""
    # The round diff lives in the head row's prose cell, collapsed — one mono
    # line, filling the space the spec table opens beside the heading (88px of
    # dead prose column, measured in a browser, before it moved there).
    assert "${diffStripHTML(id, section.diff)}" in page, \
        "the round diff belongs in the head row, above the prose and inside the measure"
    assert page.index('<h2 class="doc-head" id="rhead-${id}">') \
        < page.index('id="rseg-${id}"') \
        < page.index("${diffStripHTML(id, section.diff)}"), \
        "the diff must follow the heading and the rule, inside the head row's prose cell"
    assert "root.querySelector('#rdiff-' + id).classList.add('collapsed');" in page, \
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
    # The gutter is a glyph rail and the words live in the margin of the same
    # row — glyph for WHERE and how bad, margin for WHAT. A 70px text column at
    # 9px clamped `✓ §4 defines "cold start"` to `✓ §4 defines "cold`.
    assert "function gutterGlyphHTML(a)" in page and "function marginFlagHTML(a)" in page, \
        "a flag must be both locatable and readable"
    assert "docCell(row, 'rg').innerHTML = flags.map(gutterGlyphHTML).join('');" in page
    assert "host.innerHTML = flags.map(marginFlagHTML).join('');" in page, \
        "the flag's words belong in the margin of the row it concerns"
    assert "rm.insertBefore(host, rm.firstChild);" in page, \
        "the machine's reading of a paragraph comes before the conversation about it"
    print("test_nothing_sits_between_the_reader_and_the_prose: OK")


def test_both_columns_collapse_when_the_document_has_nothing_for_them(page: str) -> None:
    """Cap: the wasted-space rule this story owns. The composite reserves 70px
    of gutter and 300px of margin on every row; production must not. The
    decision is read off the DOM (so a comment made a moment ago counts like a
    thread that shipped with the round) and made once per document — a per-row
    or per-section decision jogs the prose column sideways as you read it."""
    assert "function updateDocColumns()" in page, "page missing the collapse rule"
    # Read off the ROUND, not the DOM. The accordion renders a section's rows
    # only when that section is opened, so a DOM read would see an empty margin
    # until the reviewer reached the first hunk carrying a note and then jog
    # every hunk sideways mid-review — the per-navigation decision this rule
    # exists to avoid. `docNotes` reads rState, so a comment made a moment ago
    # still counts, which is the property the DOM read was there for.
    assert "const gutter = sections.some(s => docFlagSplit(s).gutter.length);" in page, \
        "the gutter decision must be read off the round"
    assert ("const margin = sections.some(s => docFlagSplit(s).margin.length "
            "|| docNotes(s).length)") in page, \
        "margin flags, carried threads and this round's comments all hold the margin"
    assert "doc.classList.toggle('no-gutter', !gutter);" in page, \
        "the gutter must collapse when nothing in the round carries a flag"
    assert "doc.classList.toggle('no-margin', !margin);" in page, \
        "the margin must collapse when the round carries nothing for it"
    # With the margin gone the head row drops to two tracks and the section's
    # own controls print under its heading — pure CSS, so a collapse can never
    # move a focused control between hosts.
    # `1fr`, not the `72ch` this shipped with — the last `ch` in a grid
    # template, and the one invariant #5 was written against. It resolved
    # against the row's own font-size, so the head row came out narrower than
    # every prose row below it, and in the accordion (where no `.doc-section`
    # fixes one size) it would have resolved differently again.
    assert re.search(r'\.doc\.no-margin \.row-head\s*\{[^}]*grid-template-columns:\s*'
                     r'var\(--gutter-w\)\s+minmax\(0,\s*1fr\)', page), \
        "a collapsed margin must drop the head row to two tracks, at the rows' own measure"
    assert "ch)" not in re.search(r'\.doc[^\n]*grid-template-columns[^\n]*', page).group(0), \
        "no `ch` may appear in a grid template"
    assert re.search(r'\.doc\.no-margin \.row-head \.rm\s*\{[^}]*grid-column:\s*2', page), \
        "the head row's controls must reflow under the heading, not disappear"
    # An OPEN compose popover holds the margin as surely as a saved note does.
    # Without this the FIRST anchored comment on a bare document — precisely
    # the document the collapse rule exists for — mounts its textarea into a
    # 0px track. The head row is immune (it reflows under the heading), which
    # is why the `+ note` path hides the failure and only the select-to-comment
    # path on a clean round 1 hits it.
    assert ".rm .comment-popover.is-open" in page, \
        "an open compose popover must count as margin content"
    assert "pop.classList.add('is-open');" in page and "pop.classList.remove('is-open')" in page, \
        "the popover's open state must be a class, not a serialized style attribute"
    assert page.count("  updateDocColumns();") >= 2, \
        "both opening and closing the popover must recompute the collapse"
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
    assert '<button type="button" class="nt-btn is-pri" id="rbtn-primary-\' + id + \'">' in page, \
        "every section must keep its own approve control, on every surface"
    assert '<button type="button" class="nt-btn is-quiet" id="rcmtnote-\' + id + \'">' in page, \
        "every section must keep its own add-note control"
    assert "root.querySelector('#rbtn-primary-' + id).addEventListener" in page, \
        "and it must be wired by the one helper both builders call"
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
    # The footer carries the same grammar for the whole round — counts in, so
    # the one thing both footers share holds no mode branch. An interview has
    # no judgment/facts axis: an answer is given or it is not.
    assert "function renderFootSeg(counts, total, label)" in page, \
        "page missing the footer balance"
    assert "function reviewFootSeg(sections, total)" in page, \
        "the review page's tally speaks its sections' vocabulary"
    assert "'document balance: '" in page
    print("test_segmented_rule_states_its_counts: OK")


def test_margin_notes_and_pins_are_numbered_together(page: str) -> None:
    """Cap: the pin in the text and the note in the margin carry the same
    number, because ONE pass assigns both — two passes could disagree about
    which span is note 3. Notes order by the row their anchor lands in, so the
    numbering runs down the page the way the reader reads."""
    assert "function markAndPin(id, ordered)" in page, "page missing the mark+pin pass"
    assert "function renderHighlights(" not in page, \
        "the second marking pass had no surface left to serve and must stay gone"
    assert ".sort((a, b) => a.row - b.row || a.seq - b.seq);" in page, \
        "notes must order by the row their anchor lands in"
    # A thread owns a reply textarea, so it is placed once and never rebuilt.
    assert "if (node.parentElement !== host) host.appendChild(node);" in page, \
        "a thread already in the right cell must be left alone — moving it blurs its reply box"
    assert "sec.querySelectorAll('.rm-notes .nt').forEach(n => n.remove());" in page, \
        "only the dynamic note hosts may be rebuilt on sync"
    # One builder, every surface.
    assert "function openThreadItemHTML(t)" in page, "page missing the thread builder"
    assert "holder.innerHTML = openThreadItemHTML(t);" in page, \
        "the margin must raise a thread from the one thread builder"
    print("test_margin_notes_and_pins_are_numbered_together: OK")


def test_anchoring_reads_the_document_not_the_commentary(page: str) -> None:
    """Cap: the margin lives INSIDE `#rcontent-<id>` now, and every margin note
    echoes the wording it annotates (`.nt-quote`, `.open-thread-quote`). Three
    paths would otherwise count or mark that echo as if it were the document:

      * `occurrenceInRendered` counts over the container to pick which
        occurrence the reviewer selected — an echo inflates the ordinal, and
        `offsetInSource` then addresses a different span in the markdown, or
        none. That is the #95 bug, except the margin manufactures the repeat:
        comment on a phrase in the first paragraph, then on the same phrase in
        the third, and the second capture counts the first note's quote too.
      * `wrapNth` walks the container to place the mark, so it could highlight
        inside the commentary.
      * the mouseup handler's `.section-content` test used to mean "inside the
        document" because the popover was a sibling; now a drag across your own
        prior note would open a popover anchored to text that is not in the doc.

    One filter (`proseWalker`) answers all three. It is inert in the accordion —
    nothing there matches those classes inside `.section-content` — so both
    surfaces run the same walk rather than branching."""
    assert "function proseWalker(root)" in page, "page missing the prose-only walker"
    assert ("if (c && (c.contains('rm') || c.contains('rg') || c.contains('comment-popover')\n"
            "                  || c.contains('sug-ins')))" in page), \
        "the walker must reject the margin, the gutter, an open compose popover, " \
        "and a suggestion's proposed wording"
    # Capture: the ordinal is counted over prose.
    assert "function proseOccurrenceBefore(root, range, text)" in page
    assert "const counted = proseOccurrenceBefore(root, range, text);" in page, \
        "occurrenceInRendered must count prose only before falling back"
    assert "if (counted !== null) return counted;" in page, \
        "an unplaceable selection must fall back to the Range count, never guess"
    # Placement: the mark walks the same filter.
    fn = page.index("function wrapNth(root, needle, cls, n)")
    assert "const walk = proseWalker(root);" in page[fn:fn + 500], \
        "wrapNth must walk prose only"
    # Creation: a drag outside the prose cell is not a comment on the document.
    assert "if (!start.closest('.rp') || start.closest('.sug-ins')) return;" in page, \
        "a selection outside the prose cell — or inside proposed wording — " \
        "must not open a comment popover"
    print("test_anchoring_reads_the_document_not_the_commentary: OK")


def test_anchored_span_wears_the_reviewers_touch(page: str) -> None:
    """Cap: the ink discipline, applied to the restructure. `--touch` is the
    reviewer's touch ON THE TEXT and nothing else, so in the doc print an
    anchored span is catalog yellow and the PIN — not the highlight — carries
    whose note it is and what kind. Red and green stay confined to the
    suggestion fence, where diff semantics already own them."""
    assert re.search(r'\.doc mark\[class\^="cmt-hl-"\]\s*\{\s*background:\s*var\(--touch\);\s*'
                     r'border-bottom:\s*1\.5px solid var\(--touch-edge\)', page), \
        "an anchored span must wear catalog yellow plus the theme's edge"
    # --touch on charcoal is a 22% wash — about a 5% luminance lift, and the
    # composite has no dark rendering to check it against (its `.cf` block
    # hardcodes the white ground). The edge is what keeps the mark readable
    # there; in light the fill is already the mark, so it stays transparent.
    assert page.count('--touch-edge:') == 3, \
        "--touch-edge must be defined once per theme block"
    assert '--touch-edge: transparent;' in page and page.count('--touch-edge: #ffec8f;') == 2, \
        "the edge is transparent on the white ground and real on charcoal"
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


def test_a_suggestion_is_shown_applied(page: str) -> None:
    """Cap: a suggestion renders IN THE PROSE — the wording it replaces struck
    in the faint ink, the replacement on the same catalog yellow the anchor
    wears. Without it the reviewer reads a note *about* a sentence and never
    the sentence, which is the whole difference between a suggestion and a
    comment. Not in code: a struck line inside a code well reads as broken
    syntax, so a code suggestion falls back to the margin's −/+ fence."""
    assert "sug.className = 'sug';" in page and "was.className = 'sug-del';" in page \
        and "now.className = 'sug-ins';" in page, "page missing the inline apply"
    assert re.search(r'\.sug-del\s*\{[^}]*text-decoration:\s*line-through', page), \
        "the replaced wording must be struck"
    assert re.search(r'\.sug-ins\s*\{[^}]*background:\s*var\(--touch\)', page), \
        "the replacement must wear the reviewer's own catalog yellow"
    assert "n.placedInline = !!(repl && !n.inCode);" in page, \
        "code suggestions must not splice into the code well"
    assert "sug.append(was, now);" in page, \
        "no text node between del and ins — the gap is CSS, so it is never counted as prose"
    # Both sources of a replacement reach it: this round's comment and a
    # carried thread whose last turn was a suggestion.
    assert "function noteReplacement(n)" in page
    assert "return last.verdict === 'suggestion' ? (last.replacement || '') : '';" in page
    # The margin says so rather than printing the same two strings again.
    assert "applied above &mdash; struck wording out," in page
    assert "c.replacement && !showsInline ? suggestionFenceHTML(c)" in page, \
        "the fence is the fallback for what the prose could not show"
    print("test_a_suggestion_is_shown_applied: OK")


def test_an_anchor_that_crosses_elements_still_marks(page: str) -> None:
    """Cap: a code anchor. highlight.js splits `time.sleep(0.3)` into six token
    spans, so the phrase the reviewer selected lives in no single text node —
    wrapNth's walk marked nothing at all, and a suggestion on a line of code
    drew neither highlight nor pin. The Range fallback spans elements.

    Kept as a FALLBACK, not the primary: surroundContents splits partially
    selected elements, and diff mode's marks land inside diff2html's table
    markup where that is not a trade worth making unless the alternative is no
    mark at all."""
    assert "function wrapSpanning(root, needle, cls, n)" in page, "page missing the Range fallback"
    assert "return wrapSpanning(root, needle, cls, n);" in page, \
        "wrapNth must fall through to it, never lead with it"
    assert "range.surroundContents(mark);" in page
    assert "mark.appendChild(range.extractContents()); range.insertNode(mark);" in page, \
        "a partially-selected element must still resolve, not silently drop the mark"
    print("test_an_anchor_that_crosses_elements_still_marks: OK")


def test_each_note_carries_its_own_verb(page: str) -> None:
    """Cap: one verb per note, with its keycap, instead of a permanently-open
    reply box under two type chips — ~120px of controls on every carried
    thread whether or not the reviewer meant to say anything, which is
    affordable at the foot of an accordion card and not in a 253px margin.

    The verbs are viva's actual moves. A declined thread is waiting on
    accept-or-insist, so it leads with `Accept` (settle: the decline stands)
    against `Change anyway` (reply: an insisting reply is binding)."""
    assert "declined ? 'Accept' : 'Settle'" in page and "declined ? 'y' : 's'" in page
    assert "declined ? 'Change anyway' : 'Reply'" in page and "declined ? 'n' : 'r'" in page
    assert "(declined ? settle('is-pri') + reply() : reply() + settle('is-quiet'))" in page, \
        "a declined thread must lead with Accept as the primary"
    # The box is what a verb reveals.
    assert 'data-type="' + "' + esc(type) + '" + '" hidden>' in page, \
        "the reply box must ship hidden"
    assert "wrap.hidden = false;" in page and "setThreadReplyType(wrap, b.dataset.type);" in page
    # …and it re-opens itself rather than hiding feedback already given.
    assert ".find(c => c.cid === cid && c.reply && c.note);" in page, \
        "a reply already in rState must keep its box open across a rebuild"
    # The verb label survives settling — it names the action and carries a
    # keycap, so state is a class, not a rewritten innerHTML.
    assert "if (btn) btn.classList.toggle('is-on', !!c.settled);" in page, \
        "settling must not overwrite the verb's label"
    print("test_each_note_carries_its_own_verb: OK")


def test_bar_and_footer_state_one_arithmetic(page: str) -> None:
    """Cap: the bar and the footer answer the same question with the same
    number. `documentBalance` is the single source — items, open, checks, and
    the baseline convergence measures against — so `7 items · 5 open` in the
    bar can never disagree with `blocked · 5 open` below it.

    `convergence` compares open items when the round was ARMED against open
    items now. Both ends are counted, never estimated: the baseline reads only
    round data (carried threads, unanswered checks, flags) and never live
    reviewer state, which is what makes it a baseline."""
    assert "function documentBalance()" in page, "page missing the one arithmetic"
    assert "atStart += (s.open_notes || []).length;" in page, \
        "the baseline counts carried threads, which all arrive unsettled"
    assert "open: judgment + facts, total: judgment + facts + settled" in page
    assert "'convergence ' + b.atStart + ' &rarr; <b>' + b.open + '</b>'" in page
    # The stamp is named for what it does to the document, on every surface
    # that dispatches one.
    assert ("sub.textContent = remaining > 0 ? `approve — dispatch "
            "(${remaining} unreviewed)`") in page, \
        "the footer carries one consequential stamp"
    # A measured latency, never a claimed one.
    assert "function timedFetch(url, opts)" in page and "timedFetch('/input')" in page
    assert "if (_lastRTT === null) lat.style.display = 'none';" in page, \
        "the footer must never print a latency it did not observe"
    # The composite has no progress track — the footer's rule is the progress.
    assert "el('r-progress-track').style.display = 'none';" in page
    # Document-scale settled is ink, not the section rule's gray.
    assert re.search(r'\.foot-seg \.seg-settled\s*\{\s*background:\s*var\(--ink\)', page), \
        "the document's closed mass is drawn in ink"
    assert re.search(r'\.foot-seg\s*\{[^}]*height:\s*6px', page), \
        "the footer's rule is heavier than a section's"
    print("test_bar_and_footer_state_one_arithmetic: OK")


def test_activation_costs_no_layout(page: str) -> None:
    """Cap: pointing at a section must not move the page. Continuous print puts
    every control on screen at once, so anything that relayouts on activation
    moves a control out from under the hand reaching for it — measured at 57px
    of section jump and 17px of button slide when the spec table was gated on
    `rState.active` and hopped from one head row to another.

    Two rules keep it still: the spec is drawn for every section that has
    something to state (never only the live one), and the live section is
    marked at its heading with a border plus a compensating negative margin,
    which occupies no space at all."""
    assert "mount.innerHTML = specHTML(section);" in page, \
        "the spec must not be gated on which section is live"
    assert "rState.active === id ? specHTML" not in page, \
        "the live-only gate is what made activation a layout change"
    # A spec with nothing to say renders nothing, so a clean section's head row
    # is short whether or not it is live.
    assert "if (!s.comments && !s.suggestions && !s.declined && !s.checks && !conf0) return '';" in page, \
        "an all-zero spec is not a state readout"
    # The live marker is a border with a compensating negative margin.
    assert re.search(r'\.doc-section\.is-active \.doc-head\s*\{\s*border-left:\s*2px solid var\(--ink\);'
                     r'\s*margin-left:\s*-10px;\s*padding-left:\s*8px', page), \
        "the live marker must occupy no space"
    # And the unanchored compose box opens BELOW the controls, so the button
    # just clicked does not move.
    assert "const host = row ? docNoteHost(id, row) : (head && docCell(head, 'rm'));" in page, \
        "`+ note` must mount its box at the foot of the margin, under the controls"
    # A verdict repaints the balance and the spec — approve used to leave the
    # segmented rule showing the state before the stamp.
    assert "renderDocSeg(id); renderDocSpec(id);" in page, \
        "a verdict must repaint the section's own rule"
    print("test_activation_costs_no_layout: OK")


def test_the_slip_ships_collapsed(page: str) -> None:
    """Cap: #186's reading-order finding applies to the slip too. It is the
    round's cover note, not the round's content — above the print but
    COLLAPSED, so what a reader meets first is the document rather than a
    bordered index of it."""
    assert 'class="transmittal-head" id="transmittal-head" aria-expanded="false"' in page, \
        "the slip's head must be a disclosure, closed"
    assert '<div class="transmittal-rows" id="transmittal-rows" hidden>' in page, \
        "the slip's rows must ship hidden"
    assert "head.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');" in page, \
        "the disclosure state must stay in sync for screen readers"
    print("test_the_slip_ships_collapsed: OK")


def test_qa_wears_the_grammar_not_the_print(page: str) -> None:
    """Cap: the interview is a first-class surface on the same ground, and it
    takes the grammar without the print.

    GRAMMAR — the question numbered like a catalog entry in the prose column
    with its choices under it, the machine's hint and the reviewer's own note
    in the margin, verbs in the note grammar with their keycaps, the
    composite's bar and footer. NOT THE PRINT — one question at a time is the
    point of an interview, so the accordion stays.

    Both side columns are constants rather than a computed collapse: a
    question carries no producer flags to rail, and the margin always holds
    this question's verbs, so neither can change mid-session.

    One thing stays in the prose column on purpose — the recommended-choice
    badge. It is advice ABOUT A CONTROL, and a reviewer should not read the
    margin, look back, and hunt for the chip it meant."""
    assert "container.className = 'cards doc no-gutter';" in page, \
        "Q&A must wear the grammar and not the print"
    assert '<h2 class="doc-head" id="qhead-${q.id}">' in page, \
        "the question is the entry's own heading, numbered"
    assert '<div class="nt nt-check"><div class="nh">hint</div>' in page, \
        "the hint is a margin note in the machine's ink"
    assert 'class="nt nt-compose"' in page, \
        "the reviewer's context and its attachments live in one margin note"
    assert '<span class="chip-badge"' in page, \
        "the recommendation stays beside the control it recommends"
    assert 'id="qconfirm-${q.id}"><span aria-hidden="true">&#10003;</span> confirm<kbd>c</kbd>' in page, \
        "confirm is a margin verb carrying the key that performs it"
    # ...and that key is really bound, so the cap is not a claim.
    assert "if (e.key === 'c' && !e.metaKey && !e.ctrlKey && !e.altKey) {" in page
    # No progress track: the footer's segmented rule is the progress.
    assert 'id="qa-progress"' not in page.replace('id="qa-progress-label"', ''), \
        "the interview's progress is its footer rule, not a second bar"
    # One place a choice is picked, so the chip, the digit and the palette
    # cannot disagree about what a second press means.
    assert "function pickQAChoice(id, choice)" in page
    assert "pickQAChoice(q.id, chip.dataset.choice);" in page
    assert "pickQAChoice(qState.active, q.choices[n - 1]);" in page
    # The palette is a directory of that same layer, on this surface too — it
    # used to refuse to open at all without REVIEW_DATA.
    assert "return REVIEW_DATA ? reviewPaletteCommands() : qaPaletteCommands();" in page
    assert "if ((!REVIEW_DATA && !QA_DATA) || paletteIsOpen()) return;" in page, \
        "the palette must open on the interview too"
    print("test_qa_wears_the_grammar_not_the_print: OK")


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
            test_the_grammar_is_not_the_print(page)
            test_diff_keeps_the_accordion_and_loses_its_chrome(page)
            test_nothing_sits_between_the_reader_and_the_prose(page)
            test_both_columns_collapse_when_the_document_has_nothing_for_them(page)
            test_check_kinds_is_injected_never_restated(page)
            test_every_section_keeps_a_focusable_control(page)
            test_segmented_rule_states_its_counts(page)
            test_margin_notes_and_pins_are_numbered_together(page)
            test_anchoring_reads_the_document_not_the_commentary(page)
            test_anchored_span_wears_the_reviewers_touch(page)
            test_settled_token_defined_once_per_theme_block(page)
            test_a_suggestion_is_shown_applied(page)
            test_an_anchor_that_crosses_elements_still_marks(page)
            test_each_note_carries_its_own_verb(page)
            test_bar_and_footer_state_one_arithmetic(page)
            test_activation_costs_no_layout(page)
            test_the_slip_ships_collapsed(page)
            test_qa_wears_the_grammar_not_the_print(page)
            test_round2_wire_shape_unchanged(base)
    print("OK")


if __name__ == "__main__":
    main()
