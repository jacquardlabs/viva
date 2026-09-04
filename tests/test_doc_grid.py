#!/usr/bin/env python3
"""The doc + margin restructure (#186) — served-page integration tests.

Guards the STRUCTURE: review mode prints the document continuously as
`check gutter | prose | margin` rows, commentary beside its anchor, no
per-section action row. Markup/CSS-rule/wire-format checks only.
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
    """Cap: review mode routes to the doc builder and renders every section
    up front — continuous print has nothing to open lazily."""
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
    """Cap: the seam. `.doc` arms the grammar (every section container);
    `.print` arms continuous print (review only). No `usesMargin()`
    predicate exists — every caller wears the margin already."""
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
    """Cap: SURVIVES — the accordion (one hunk open at a time, disclosure
    button, animating body, carried gate). DOES NOT — its chrome (annotation
    strip, thread list, action row): margin objects now, deleted not hidden."""
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
    # The open hunk shares the print's head/foot band builders (one copy each),
    # so a verb added to document review can't go missing from diff review.
    assert "function docHeadRowHTML(id, proseHTML)" in page, \
        "both builders must raise the head row from one function"
    assert "function docFootRowHTML(id, title, opts)" in page, \
        "both builders must raise the foot band from one function"
    assert "${docHeadRowHTML(section.id, " in page, \
        "the accordion's head row must come from the shared builder"
    assert "docFootRowHTML(section.id, section.title, { skip: true })" in page, \
        "the accordion's foot band must come from the shared builder, skip included"
    # `skip` is the accordion's alone: with one hunk open at a time, "not this
    # one, not now" is a real move; in the print it is just reading on.
    assert "const skip = !!(opts && opts.skip);" in page
    # The hunk never borrows the margin's track — a first comment must not
    # re-layout the lines being commented on. Only a CODE row takes it.
    assert ".doc.print .row.wide:not(:has(> .rm)):has(> .rp > pre, > .rp > .d2h-wrapper) .rp" in page, \
        "the break-out rule belongs to the print, where a wide row is one of many, and to code"
    # A pin on a code line leads the line and sticks: a diff line scrolls
    # horizontally, so a pin fixed to the anchor's offset would go offscreen.
    assert "tail.closest('.d2h-code-line')" in page, \
        "a diff pin must attach to the line, not to the character offset"
    assert re.search(r'\.pin-line\s*\{[^}]*position:\s*sticky', page), \
        "a diff pin must survive a horizontal scroll of its own pane"
    # A carried suggestion on a diff line takes the margin's fence too: a
    # del/ins pair spliced into a `+` line reads as neither version.
    assert "mark.closest('pre, .d2h-code-line')" in page, \
        "a rendered diff line is a code well too"
    # An anchor spanning hljs token spans must be re-inserted where its content
    # came from — `extractContents` can otherwise collapse the range up to its
    # parent, and a flattened mark then stays a sibling of the code container.
    assert "const slot = document.createTextNode('');" in page and \
        "slot.parentNode.replaceChild(mark, slot);" in page, \
        "a spanning mark must be re-inserted where its content came from"
    # A carried card renders content without the grammar (read-only, nothing
    # to annotate) and has no `.row-head` — dropping the mode guard here sent
    # an unanchored thread through `docCell(null, 'rm')`, a null-deref TypeError.
    assert "const carried = !!(card && card.classList.contains('is-carried'));" in page, \
        "the reveal must be recognised before anything is placed into it"
    assert "if (carried) return;" in page, \
        "the row pipeline must skip a carried reveal"
    assert "if (!carried) { layoutDocRows(id); placeDocFlags(id); placeDocThreads(id); renderDocMargin(id); }" in page, \
        "...on the md-raw fallback path too"
    assert "const host = row || docFootRow(id);" in page and "if (!host) return;" in page, \
        "placeDocThreads must carry the same missing-host guard as its siblings"
    # `docFootRow` must stay a QUERY, never build the band on demand — a
    # carried card has no `.row-head`, and building on demand reopens the
    # same null-deref this guards against.
    assert "function docFootRow(id)" in page and "sec.querySelector('.row-foot')" in page, \
        "the foot band must be found, never created — that is what keeps a carried reveal null"
    print("test_diff_keeps_the_accordion_and_loses_its_chrome: OK")


def test_nothing_sits_between_the_reader_and_the_prose(page: str) -> None:
    """Cap: the reading-order fix. Threads and flags now sit BESIDE their
    anchor, not stacked above it, and the round diff — the widest object on
    the page — ships collapsed, never expanded."""
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
    """Cap: the wasted-space rule. Both side columns must collapse to zero
    when the round has nothing for them, decided once per document — a
    per-row or per-section decision jogs the prose column as you read."""
    assert "function updateDocColumns()" in page, "page missing the collapse rule"
    # Read off the ROUND, not the DOM: the accordion only renders an opened
    # section's rows, so a DOM read would jog columns sideways mid-review.
    assert "const gutter = sections.some(s => docFlagSplit(s).gutter.length);" in page, \
        "the gutter decision must be read off the round"
    assert ("const margin = sections.some(s => docFlagSplit(s).margin.length "
            "|| docNotes(s).length)") in page, \
        "margin flags, carried threads and this round's comments all hold the margin"
    assert "doc.classList.toggle('no-gutter', !gutter);" in page, \
        "the gutter must collapse when nothing in the round carries a flag"
    # The PRINT keeps its margin even when empty — collapsing it would
    # rewrap the whole document the moment the first composer opened.
    assert "doc.classList.toggle('no-margin', !margin && !isContinuousPrint());" in page, \
        "the margin collapses only in the accordion; the print holds the measure"
    # The HEAD row has no margin cell in any state, so it needs no
    # collapsed-margin exemption at all — three special cases deleted, and a
    # rule that reappears is a head row that grew a margin back.
    assert ".doc.no-margin .row-head" not in page, \
        "the head row is one track; a collapse exemption for it is a margin cell returning"
    # The FOOT band keeps the twin so an unanchored flag still renders.
    # `1fr`, not `72ch`: a `ch` unit resolves against the row's own
    # font-size and would come out narrower than the prose rows (#5).
    assert re.search(r'\.doc\.no-margin \.row-foot\s*\{[^}]*grid-template-columns:\s*'
                     r'var\(--gutter-w\)\s+minmax\(0,\s*1fr\)', page), \
        "a collapsed margin must drop the foot band to two tracks, at the rows' own measure"
    # Every `.doc*` grid template, not just the first one `re.search` finds.
    for line in re.findall(r'\.doc[^\n]*grid-template-columns[^\n]*', page):
        assert "ch)" not in line, f"no `ch` may appear in a grid template: {line}"
    assert re.search(r'\.doc\.no-margin \.row-foot \.rm\s*\{[^}]*grid-column:\s*2', page), \
        "the foot band's margin must reflow under the prose, not disappear"
    # An OPEN compose popover holds the margin as surely as a saved note does —
    # without this, the first anchored comment on a bare document mounts its
    # textarea into a 0px track.
    assert ".rm .comment-popover.is-open" in page, \
        "an open compose popover must count as margin content"
    assert "pop.classList.add('is-open');" in page and "pop.classList.remove('is-open')" in page, \
        "the popover's open state must be a class, not a serialized style attribute"
    assert page.count("  updateDocColumns();") >= 2, \
        "both opening and closing the popover must recompute the collapse"
    print("test_both_columns_collapse_when_the_document_has_nothing_for_them: OK")


def test_check_kinds_is_injected_never_restated(page: str) -> None:
    """Cap: the frontend reads scripts/schema.py's registry rather than
    keeping a copy — CHECK_KINDS fails open, so a hand-kept copy would fail
    open the same silent way in the surface that draws `checks N/M`."""
    expected = "const CHECK_KINDS = " + json.dumps(list(schema.CHECK_KINDS)) + ";"
    assert expected in page, f"CHECK_KINDS must be injected from schema.py, got neither {expected!r}"
    assert "__CHECK_KINDS__" not in page, "the injection placeholder must be substituted"
    # And it is the registry the spec table and the segmented rule ask.
    assert "CHECK_KINDS.includes(a.kind)" in page, \
        "the checks row and the balance must both read the injected registry"
    # THREAD_STATUS_LABELS is injected the same way: a broken `.replace()`
    # chain leaves the placeholder in place and bricks the tab with no
    # server-side error.
    expected_labels = ("const THREAD_STATUS_LABELS = "
                       + json.dumps(dict(schema.THREAD_STATUS_LABELS)) + ";")
    assert expected_labels in page, \
        f"THREAD_STATUS_LABELS must be injected from schema.py, got neither {expected_labels!r}"
    assert "__THREAD_STATUS_LABELS__" not in page, \
        "the injection placeholder must be substituted"
    print("test_check_kinds_is_injected_never_restated: OK")


def test_every_section_keeps_a_focusable_control(page: str) -> None:
    """Cap: keyboard reach survives the action row's removal. Sections no
    longer collapse, so a note-less section would have zero focusable
    elements unless approve stays — it stays, in the margin."""
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
    order is fixed (judgment -> facts -> settled) as the colorblind-safe
    encoding, and raw counts ride in the aria-label so the claim is checkable."""
    assert "function sectionBalance(section)" in page, "page missing the balance function"
    assert "return { judgment, facts, settled, signoff };" in page
    # A section's own sign-off is an item in BOTH states — pending as well as
    # settled. It used to count only once approved, so a fresh round printed
    # `0 items · 0 open` despite the legend defining sign-off as an item.
    assert "const signoff = deriveVerdict(id) === 'approved' ? 0 : 1;" in page, \
        "a pending sign-off must count as an item, not only a settled one"
    assert "if (!signoff) settled++;" in page, \
        "an approved section's sign-off stays in `settled`"
    # It rides out as its OWN field rather than folding into `judgment`,
    # because the segmented rule must not paint it — segHTML's denominator
    # sums three fields, not four.
    assert "const total = bal.judgment + bal.facts + bal.settled;" in page, \
        "segHTML must exclude `signoff` — a pending sign-off is not a painted segment"
    # Fixed order, in the markup the reader gets.
    order = page.index("seg('seg-judgment', bal.judgment) + seg('seg-fact', bal.facts)")
    assert order > 0 and "seg('seg-settled', bal.settled)" in page[order:order + 200], \
        "segments must print judgment -> facts -> settled, always in that order"
    assert "'open: ' + bal.judgment + ' judgment, ' + bal.facts + ' fact'" in page, \
        "the segmented rule must state its raw counts in the aria-label"
    assert "if (!bal.judgment && !bal.facts) return '<div class=\"rule-s\"></div>';" in page, \
        "a section with nothing open takes the thin settled hairline, not a bar"
    # Finding 09. A PLAIN producer flag is advisory — nothing the reviewer
    # can do closes it — so it must not count as an open item; a section
    # with only producer flags now draws NO rule at all.
    sec_balance = page[page.index("function sectionBalance(section)"):]
    sec_balance = sec_balance[:sec_balance.index("\n}")]
    assert "a.severity" not in sec_balance, \
        "an advisory producer flag must not be counted as an open item"
    # The deletion above must not over-apply to a check: an unanswered
    # CHECK_KIND is answerable via `result` and gates a `checks` round.
    assert ("if (CHECK_KINDS.includes(a.kind)) { if (a.result) settled++; "
            "else facts++; return; }") in sec_balance, \
        "an answerable check stays an open fact until it carries a result"
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
    """Cap: the pin and its margin note carry the same number because ONE
    pass assigns both — two passes could disagree about which span is note 3.
    Notes order by the row their anchor lands in."""
    assert "function markAndPin(id, ordered)" in page, "page missing the mark+pin pass"
    assert "function renderHighlights(" not in page, \
        "the second marking pass had no surface left to serve and must stay gone"
    assert ".sort((a, b) => a.row - b.row || a.seq - b.seq);" in page, \
        "notes must order by the row their anchor lands in"
    # A thread owns a reply textarea, so it is placed once and never rebuilt.
    assert "if (node.parentElement !== threadHost) threadHost.appendChild(node);" in page, \
        "a thread already in the right cell must be left alone — moving it blurs its reply box"
    assert "sec.querySelectorAll('.rm-notes .nt').forEach(n => n.remove());" in page, \
        "only the dynamic note hosts may be rebuilt on sync"
    # One builder, every surface.
    assert "function openThreadItemHTML(t)" in page, "page missing the thread builder"
    assert "holder.innerHTML = openThreadItemHTML(t);" in page, \
        "the margin must raise a thread from the one thread builder"
    print("test_margin_notes_and_pins_are_numbered_together: OK")


def test_anchoring_reads_the_document_not_the_commentary(page: str) -> None:
    """Cap: the margin echoes the wording it annotates, so occurrence-
    counting, mark placement, and the comment handler must all exclude that
    echo (the #95-shaped bug). One filter, `proseWalker`, answers all three."""
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
    """Cap: `--touch` is the reviewer's touch on the text and nothing else —
    an anchored span is catalog yellow and the PIN, not the highlight, carries
    whose note it is. Red/green stay confined to the suggestion fence."""
    assert re.search(r'\.doc mark\[class\^="cmt-hl-"\]\s*\{\s*background:\s*var\(--touch\);\s*'
                     r'border-bottom:\s*1\.5px solid var\(--touch-edge\)', page), \
        "an anchored span must wear catalog yellow plus the theme's edge"
    # The edge keeps the mark readable on charcoal, where the yellow wash is
    # subtle; in light the fill is already the mark, so it stays transparent.
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
    """Cap: the segmented rule's settled ink is a real token in all three
    theme blocks — the light value is nearly white on charcoal, so the dark
    side needs its own value rather than one carried across."""
    assert page.count('--settled:') == 3, \
        "--settled must be defined once per theme block (light, media dark, explicit dark)"
    assert re.search(r'--settled:\s+#e3e4e2;', page), "light --settled missing"
    assert page.count('--settled: #3a3e41;') == 2, \
        "both dark blocks must carry the same dark --settled (test_theme_toggle enforces the pair)"
    print("test_settled_token_defined_once_per_theme_block: OK")


def test_a_suggestion_is_shown_applied(page: str) -> None:
    """Cap: a suggestion renders IN THE PROSE — struck wording plus the
    replacement in catalog yellow. Not in code: a struck line there reads as
    broken syntax, so a code suggestion falls back to the margin's −/+ fence."""
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
    """Cap: a code anchor. highlight.js splits a code line into token spans,
    so a selected phrase can span no single text node — the Range fallback
    handles it, kept as a FALLBACK since `surroundContents` splits elements."""
    assert "function wrapSpanning(root, needle, cls, n)" in page, "page missing the Range fallback"
    assert "return wrapSpanning(root, needle, cls, n);" in page, \
        "wrapNth must fall through to it, never lead with it"
    assert "range.surroundContents(mark);" in page
    assert "mark.appendChild(range.extractContents());" in page, \
        "a partially-selected element must still resolve, not silently drop the mark"
    print("test_an_anchor_that_crosses_elements_still_marks: OK")


def test_each_note_carries_its_own_verb(page: str) -> None:
    """Cap: one verb per note, with its keycap, instead of a permanently-open
    reply box — too much chrome for the margin. A declined thread leads with
    `Accept` (settle) against `Change anyway` (reply, which is binding)."""
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
    """Cap: the bar and footer answer off one source, `documentBalance`, so
    they can never disagree. `convergence` compares open items at ARM time
    vs. now, reading only round data — never live reviewer state."""
    assert "function documentBalance()" in page, "page missing the one arithmetic"
    assert "atStart += (s.open_notes || []).length;" in page, \
        "the baseline counts carried threads, which all arrive unsettled"
    assert "open: judgment + facts + signoff" in page
    assert "total: judgment + facts + settled + signoff" in page
    # Both ends of the convergence arrow count the sign-off, or the arrow
    # lies. The baseline asks `approved_ids` — the static field the round
    # shipped with — never the live verdict it's measured against.
    assert "const armedApproved = new Set(REVIEW_DATA.approved_ids || []);" in page, \
        "the baseline must read the round as armed, not as it stands now"
    assert "if (!armedApproved.has(s.id)) atStart++;" in page, \
        "a section that arrived owing a sign-off is open at the arrow's left"
    assert "'convergence ' + b.atStart + ' &rarr; <b>' + b.open + '</b>'" in page
    # The stamp names its action only — `#stat-pending` is the one place
    # the blocking count prints.
    assert "sub.textContent = 'approve — dispatch';" in page, \
        "the footer carries one consequential stamp, with no restated count"
    # A measured latency, never a claimed one.
    assert "function timedFetch(url, opts)" in page and "timedFetch('/input')" in page
    assert "if (_lastRTT === null || _lastRTT < SLOW_RTT_MS) lat.style.display = 'none';" in page, \
        "the footer must never print a latency it did not observe, nor one not worth acting on"
    # The composite has no progress track — the footer's rule is the progress.
    assert "el('r-progress-track').style.display = 'none';" in page
    # Document-scale settled is ink, not the section rule's gray.
    assert re.search(r'\.foot-seg \.seg-settled\s*\{\s*background:\s*var\(--ink\)', page), \
        "the document's closed mass is drawn in ink"
    assert re.search(r'\.foot-seg\s*\{[^}]*height:\s*6px', page), \
        "the footer's rule is heavier than a section's"
    # Finding 08: every aggregate either states what it counts or is gone.
    # (a) the cell LABELLED `approved` prints approved, not `reviewed`
    # (approved + withFeedback, per DESIGN.md's `approved N/M`).
    assert "el('r-progress-label').textContent = `${approved} / ${total}`;" in page, \
        "the cell labelled `approved` must print approved, never reviewed"
    # (b) `N approved` / `N with feedback` are DELETED, not hidden — a hidden
    # aria-live element left a stale `3 with feedback` in the DOM forever.
    for dead in ("el('stat-approved')", "el('stat-feedback')",
                 'id="stat-approved"', 'id="stat-feedback"',
                 ".stat-approved {", ".stat-feedback {"):
        assert dead not in page, f"the retired aggregate still ships: {dead}"
    # (c) the survivors define themselves, in the page, in the reader's reach.
    legend = page[page.index('<details class="kbd-legend">'):]
    legend = legend[:legend.index("</details>")]
    for term in ("<dt>item</dt>", "<dt>open</dt>", "<dt>convergence</dt>",
                 "<dt>approved</dt>", "<dt>checks</dt>"):
        assert term in legend, f"an aggregate that never defines itself: {term}"
    print("test_bar_and_footer_state_one_arithmetic: OK")


def test_activation_costs_no_layout(page: str) -> None:
    """Cap: pointing at a section must not move the page. The spec draws for
    every section with something to state; the live marker is a border plus
    a compensating negative margin, so it occupies no space."""
    assert "mount.innerHTML = specHTML(section);" in page, \
        "the spec must not be gated on which section is live"
    assert "rState.active === id ? specHTML" not in page, \
        "the live-only gate is what made activation a layout change"
    # A spec with nothing to say renders nothing, so a clean section's head
    # row is short whether or not it is live.
    assert "if (!s.comments && !s.suggestions && !s.declined && !s.checks && !conf0) return '';" in page, \
        "an all-zero spec is not a state readout"
    # The live marker is a border with a compensating negative margin.
    assert re.search(r'\.doc-section\.is-active \.doc-head\s*\{\s*border-left:\s*2px solid var\(--ink\);'
                     r'\s*margin-left:\s*-10px;\s*padding-left:\s*8px', page), \
        "the live marker must occupy no space"
    # The unanchored compose box opens in the FOOT band's margin cell:
    # `+ note` is in the same row, one cell over, so the box can't displace
    # the button that opened it — mounting in the head row's `.rm` did.
    assert "const host = row ? docNoteHost(id, row) : (foot && docCell(foot, 'rm'));" in page, \
        "`+ note` must mount its box in the foot band's margin, beside its own button"
    # Without these two, the test would pass on a change that opens a
    # composer the reviewer cannot see.
    composer = page[page.index("function openCommentPopover(") :
                    page.index("function closeCommentPopover(")]
    # `revealWithinBars`, not `scrollIntoView({block:'nearest'})`: Chrome
    # ignores `scroll-padding` for a smooth scrollIntoView.
    assert "revealWithinBars(pop);" in composer, \
        "the composer must bring itself into view, clear of the fixed bar"
    assert "ta.focus({ preventScroll: true });" in composer, \
        "...and the browser's own scroll-to-field must not undo it"
    assert "block: 'center'" not in composer, \
        "`center` yanks the prose out from under the reader; `nearest` does not"
    # A verdict repaints the balance and the spec — approve used to leave the
    # segmented rule showing the state before the stamp.
    assert "renderDocSeg(id); renderDocSpec(id);" in page, \
        "a verdict must repaint the section's own rule"
    print("test_activation_costs_no_layout: OK")


def test_the_slip_ships_collapsed(page: str) -> None:
    """Cap: #186's reading-order finding applies to the slip too — it is the
    round's cover note, above the print but COLLAPSED, so a reader meets the
    document first."""
    assert 'class="transmittal-head" id="transmittal-head" aria-expanded="false"' in page, \
        "the slip's head must be a disclosure, closed"
    assert '<div class="transmittal-rows" id="transmittal-rows" hidden>' in page, \
        "the slip's rows must ship hidden"
    assert "head.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');" in page, \
        "the disclosure state must stay in sync for screen readers"
    print("test_the_slip_ships_collapsed: OK")


def test_qa_wears_the_grammar_not_the_print(page: str) -> None:
    """Cap: the interview takes the `.doc` grammar without continuous print
    — one question at a time keeps the accordion. Side columns are constants
    rather than a computed collapse, since neither can change mid-session."""
    assert "container.className = 'cards doc no-gutter';" in page, \
        "Q&A must wear the grammar and not the print"
    # The disclosure head is the question, printed once, numbered like a
    # catalog entry — not a dimmed index line above a restated `<h2>`. The
    # number goes INSIDE `.card-title` because `.card-title-wrap` is a column flex.
    assert ('<span class="card-title"><span class="doc-num" aria-hidden="true">'
            '${index + 1} &middot;</span> ${esc(q.text)}</span>') in page, \
        "the disclosure head is the question, numbered"
    assert '<div class="nt nt-check"><div class="nh">hint</div>' in page, \
        "the hint is a margin note in the machine's ink"
    assert 'class="nt nt-compose"' in page, \
        "the reviewer's context and its attachments live in one margin note"
    assert '<span class="chip-badge"' in page, \
        "the recommendation stays beside the control it recommends"
    # One choice per line: wrapped into a ragged row, the picking digit
    # lands differently on every row; stacked, labels and keycaps line up.
    assert re.search(r'\.choices \{[^}]*flex-direction:\s*column', page), \
        "choices must stack one per line"
    assert '<span class="chip-label">' in page and re.search(r'\.chip-label \{[^}]*flex:\s*1', page), \
        "the label takes the row so the badge and keycap ride one right edge"
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


def test_the_head_row_is_one_track(page: str) -> None:
    """Finding 01, at its root. The head row's margin cell held the state
    table, verbs, and unanchored comments beside a title-only prose cell.
    It's deleted, not shrunk: a one-track row's height is its prose's."""
    assert "function docHeadRowHTML(id, proseHTML)" in page, \
        "the head row takes prose and nothing else — no `opts`, no margin cell"
    builder = page[page.index("function docHeadRowHTML(id, proseHTML)"):]
    builder = builder[:builder.index("\n}")]
    assert 'class="rm' not in builder, \
        "a margin cell in the head row is the void returning"
    assert "docHeadRow(" not in page, \
        "the head-row query has no callers left; it must be deleted, not kept"
    # The head row's collapsed-margin exemptions are gone with it.
    assert ".doc.no-margin .row-head" not in page and ".spec {" not in page, \
        "three special cases deleted for a row that has no margin in any state"
    print("test_the_head_row_is_one_track: OK")


def test_a_whole_section_note_sits_at_the_section_foot(page: str) -> None:
    """Where everything the head margin held went: a note beside its own
    passage keeps its row's margin; everything ABOUT the whole section moves
    to a foot band under the prose."""
    # Static markup from both builders, emitted AFTER the content and as a
    # SIBLING of it — a foot row nested inside would re-enter the document
    # walk (the #95-shaped failure).
    for content, foot in (('<div class="section-content" id="rcontent-${section.id}"></div>',
                           "docFootRowHTML(section.id, section.title, { skip: true })"),
                          ('<div class="section-content" id="rcontent-${id}"></div>',
                           "docFootRowHTML(id, section.title)")):
        assert page.index(content) < page.index(foot), \
            "the foot band follows the content and is never inside it"
    # All four fallbacks move together, or they disagree.
    assert "const target = row || docFootRow(id);" in page, "docNoteHost"
    assert "const host = row || docFootRow(id);" in page, "placeDocThreads"
    assert "const key = row || docFootRow(id);" in page, "placeDocFlags"
    assert "const host = row ? docNoteHost(id, row) : (foot && docCell(foot, 'rm'));" in page, \
        "the composer"
    # An unanchored note sorts LAST — a whole-section note is read after the
    # section, not before it.
    assert "row: r ? rows.indexOf(r) : rows.length" in page, \
        "an unanchored note sorts to the foot, not to the head"
    assert "const host = docNoteHost(id, docRows(id)[n.row] || null);" in page, \
        "the out-of-range index must be stated, not left to `undefined || footRow`"
    # The ids the rest of the app addresses these by do not move.
    for anchor in ('id="rbtn-primary-', 'id="rcmtnote-', 'id="rspecbody-', 'id="rbtn-skip-'):
        assert anchor in page, f"the foot band must keep {anchor}"
    print("test_a_whole_section_note_sits_at_the_section_foot: OK")


def test_the_foot_band_states_and_acts(page: str) -> None:
    """The band's own grammar. The state is a horizontal RUN at the reading
    measure, not a five-row table in a side column; verbs lead so `approve`
    stays on the left edge the reader has been reading down."""
    # The deleted `<table>`'s `<caption>` named the state readout; `role=group`
    # with an explicit label replaces it so the band stays announced.
    assert 'class="doc-apparatus" role="group" aria-label="' in page, \
        "the band must be announced by name, as the spec table's caption was"
    assert '<table class="spec">' not in page, "the table is gone; the caption with it"
    assert re.search(r'\.spec-strip \{[^}]*order:\s*2', page) and \
        re.search(r'\.doc-acts\s*\{[^}]*order:\s*1', page), \
        "the verbs lead and the state trails, so approve never moves"
    assert re.search(r'\.spec-strip:empty\s*\{\s*display:\s*none', page), \
        "an all-zero spec renders nothing; an empty auto-margin item must not eat the row"
    assert re.search(r'\.doc \.row-foot \.rp\s*\{\s*max-width:\s*72ch', page), \
        "the band takes the reading measure, or diff mode strands approve 1200px away"
    assert re.search(r'\.doc \.row-foot\s*\{[^}]*margin-top:', page), \
        "the band closes a section; `.doc .row + .row` never reaches it"
    # The prose dims on approval; the withdraw band does not. Its selector
    # ties the hover rule's specificity, so source order AFTER it is what wins.
    assert page.index(".doc-section.is-approved:hover .rp") \
        < page.index(".doc-section.is-approved .row-foot .rp { opacity: 1; }"), \
        "the withdraw control must not dim; source order is what wins the tie"
    # The confidence readout has no other home: docFlagSplit sends a
    # `confidence` annotation to NEITHER column precisely because this is it.
    assert "item('agent confidence'," in page, \
        "dropping confidence here makes a documented feature invisible with no error"
    print("test_the_foot_band_states_and_acts: OK")


def test_document_flags_leave_the_first_section(page: str) -> None:
    """Finding 02. Whole-document producer flags anchor to `sections[0]`
    (the only document-level handle available), which used to open every
    round-1 review on five amber lines and near-zero visible prose."""
    # Injected, never restated — the same anti-drift pair CHECK_KINDS carries.
    assert ("const DOC_SCOPE_KINDS = " + json.dumps(list(schema.DOC_SCOPE_KINDS)) + ";") in page, \
        "the scope registry must be injected from scripts/schema.py"
    assert "__DOC_SCOPE_KINDS__" not in page, "the placeholder must not ship raw"
    # Routing: a third bucket, at the one routing boundary.
    assert "const gutter = [], margin = [], doc = [];" in page
    assert "if (DOC_SCOPE_KINDS.includes(a.kind)) { doc.push(a); return; }" in page, \
        "a document fact goes to neither column"
    # The slip.
    for fn in ("function documentFlags(", "function docSlipHTML(", "function renderDocSlip("):
        assert fn in page, f"the document slip is missing {fn}"
    assert page.index('id="transmittal"') < page.index('id="doc-slip"') \
        < page.index('id="review-cards"'), \
        "the slip mounts after the transmittal and above the print"
    assert 'id="doc-slip-rows"' in page and "const open = flags.some(a => a.severity === 'error');" in page, \
        "collapsed like the transmittal — unless the document carries an error"
    # EVERY mode that renders sections, not review alone — a mode gate on the
    # slip means the flag renders NOWHERE, while `round_is_complete` still
    # enforces the gate in Python with no surface to show it.
    slip_fn = page[page.index("function docSlipHTML() {"):page.index("function renderDocSlip() {")]
    assert "REVIEW_DATA.mode !== 'review'" not in slip_fn, \
        "a doc-scope flag must have a surface in diff mode too, not vanish"
    assert "if (!REVIEW_DATA) return '';" in slip_fn, \
        "the slip still needs a round to read"
    # The accessible name must not claim every row is a check — a
    # `checklist` row isn't one, only `headings-present` is.
    assert 'id="doc-slip" aria-label="Document-level flags"' in page, \
        "the slip's accessible name must not claim every row is a check"
    # The three readers whose denominator is one section skip doc-scope...
    assert "a => a && CHECK_KINDS.includes(a.kind) && !DOC_SCOPE_KINDS.includes(a.kind));" in page, \
        "sectionSpec's checks tally"
    assert "if (DOC_SCOPE_KINDS.includes(a.kind)) return;" in page, \
        "sectionBalance's annotation loop"
    assert ".filter(a => a && !DOC_SCOPE_KINDS.includes(a.kind))" in page, \
        "flagRank — or a round-2 slip brands section 1 flagged for a document fact"
    # `documentBalance` still counts a doc-scope flag in `checks`/`checksDone`
    # (the bar's readout) but must NOT count one in `atStart`, since `open`
    # sums `sectionBalance`, which skips doc-scope entirely.
    doc_balance = page[page.index("function documentBalance()"):]
    doc_balance = doc_balance[:doc_balance.index("\n}")]
    assert "checks++;" in doc_balance and "if (a.result) checksDone++;" in doc_balance, \
        "documentBalance still tallies doc-scope checks — that is the bar's readout"
    assert "else if (!DOC_SCOPE_KINDS.includes(a.kind)) atStart++;" in doc_balance, \
        "an unanswered document check must not sit at the left of the arrow alone"
    # A plain producer flag is advisory and counted at NEITHER end of the
    # arrow (finding 09) — `CHECK_KINDS` is the only annotation branch left.
    assert "a.severity" not in doc_balance, \
        "an advisory producer flag is not an item at either end of the arrow"
    print("test_document_flags_leave_the_first_section: OK")


def test_a_free_text_question_prints_its_question_once(page: str) -> None:
    """Finding 04. `.card-title` printed the question, and the entry's
    `<h2>` printed it AGAIN one line below — the old clamped index-line
    mitigation in DESIGN.md no longer applies."""
    card = page[page.index("function buildQACard(q, index)"):]
    card = card[:card.index("\nfunction ")]
    assert card.count("${esc(q.text)}") == 1, \
        "the question is printed once, in the disclosure head"
    # The clamp goes with the `<h2>`: ellipsizing a question now printed
    # nowhere else would leave it readable nowhere.
    assert "#qa-cards .card-title { white-space: normal; }" in page
    assert "#qa-cards .card.is-active .card-title { color: var(--soft)" not in page, \
        "the dim mitigation cannot be restored — the thing it mitigated is gone"
    # A choiceless question has no prose column at all, reflowed by the same
    # two-track mechanism the collapsed margin used.
    assert "#qa-cards .row-head.is-choiceless { grid-template-columns: var(--gutter-w) minmax(0, 1fr); }" in page
    assert "const choiceless = q.choices.length === 0;" in page
    # Both readers of `#qchoices-` are guarded, not just the wiring —
    # unguarded, syncQACard throws on every sync of a choiceless card.
    assert "const ch = card.querySelector('#qchoices-' + q.id);\n  if (ch) ch.addEventListener" in page, \
        "buildQACard's chip wiring must survive a missing chip list"
    assert "const chEl = el('qchoices-' + id);\n  if (chEl) chEl.querySelectorAll" in page, \
        "syncQACard must too — this is the site a partial edit leaves throwing"
    # Bounded, not un-stretched: `stretch` lines up labels and keycaps on
    # their edges; `flex-start` would trade that away.
    assert re.search(r'\.choices \{[^}]*flex-direction:\s*column[^}]*max-width:\s*328px', page), \
        "the choice run is bounded to 328px while staying stretched"
    assert re.search(r'\.chip-label \{[^}]*flex:\s*1', page)
    print("test_a_free_text_question_prints_its_question_once: OK")


def test_the_stamp_never_prints_a_count_it_was_not_given(page: str) -> None:
    """Finding 10. A caller can omit `sections_total`; the handler used to
    fill the gap with a literal `'?'`, degrading the APPROVED stamp. Absent
    counts now DROP the line entirely, `display:none` included."""
    handler = page[page.index("es.addEventListener('complete'"):]
    handler = handler[:handler.index("\n  });")]
    assert "const counted = typeof r === 'number' && typeof s === 'number';" in handler, \
        "the stamp must know whether it was given both counts"
    assert "stampSub.style.display = counted ? '' : 'none';" in handler, \
        "an unknown count drops the line rather than reserving its margin"
    assert "'?'" not in handler, \
        "no question mark may reach the stamp"
    print("test_the_stamp_never_prints_a_count_it_was_not_given: OK")


def test_round2_lands_on_what_changed(page: str) -> None:
    """Finding 03. Carried annotations mean round 2 opens on the same flag
    wall round 1 did. On round >= 2, the landing prefers the first section
    with something NEW; else falls back to the first unapproved section."""
    assert "const newBusiness = (isContinuousPrint() && REVIEW_DATA.round > 1)" in page, \
        "the new landing must be gated to round >= 2 in the print"
    assert "const landing = newBusiness || firstPending || REVIEW_DATA.sections[0];" in page, \
        "with nothing new the fallback chain must be what it always was"
    # The predicate is defined ONCE and asked by two readers — the landing and
    # the transmittal's `answered` bucket. Two copies would drift.
    assert "function authorAnswered(t, round) {" in page \
        and "function sectionAnswered(s, round) {" in page, \
        "the author's-turn predicate must have one definition, and it must take " \
        "the round rather than reading a global — `transmittalHTML` is a pure " \
        "function over its own `data`"
    assert "return Boolean(last.response) || last.grounds !== undefined;" in page, \
        "a decline is an answer, and `grounds` is keyed on PRESENCE — a decline " \
        "with no grounds is still a decline, the same rule `openNotesHTML` states"
    # An exact count, deliberately: grounds-presence has exactly two homes
    # (this predicate and `openNotesHTML`) — a third would be a drifted copy.
    assert page.count(".grounds !== undefined") == 2, \
        "grounds-presence lives in openNotesHTML and the predicate, nowhere else"
    # FRESHNESS: without this, a two-round-old answer re-presents as news.
    # `- 1` because the response lands the round after the exchange.
    assert "if (Number(last.round) !== round - 1) return false;" in page, \
        "an answer is news for exactly one round"
    assert "sectionAnswered(s, data.round)" in page, \
        "the transmittal bucket asks the freshness question too"
    assert "sectionAnswered(s, REVIEW_DATA.round)" in page, \
        "...and so does the landing, from its own round"
    print("test_round2_lands_on_what_changed: OK")


def test_one_decision_prints_once(page: str) -> None:
    """Finding 03. A `checks` round used to print the SAME `result` verbatim
    on every flag it answered. A repeated `result` is now dropped, and the
    annotation is COPIED, never mutated, since `a.result` feeds counters."""
    assert "function dedupeResults(list, seen) {" in page, \
        "the Set is a parameter, so no caller can share one"
    assert "if (seen.has(r)) return Object.assign({}, a, { result: undefined });" in page, \
        "the annotation must be copied — blanking `result` in place moves a " \
        "number with no error anywhere"
    assert "const seen" not in page[:page.index("function dedupeResults(")], \
        "no module-level dedupe Set may exist above the helper"
    # The site that actually closes the finding — the slip, since
    # `docFlagSplit` routes every check-kind flag there today.
    slip = page[page.index("function docSlipHTML() {"):page.index("function renderDocSlip() {")]
    assert "dedupeResults(flags, new Set()).map(marginFlagHTML).join('')" in slip, \
        "the document slip's rows are where the repeated result wall now lives"
    # ...and the head tally must NOT see the deduped list, or five flags
    # answered with one sentence would read `checks 1/5` on a finished round.
    assert "const checks = flags.filter(a => CHECK_KINDS.includes(a.kind));" in slip, \
        "`checks D/T` counts the RAW flags — it is the only readout of the gate"
    assert "const done = checks.filter(a => a.result).length;" in slip, \
        "the answered tally counts the raw flags too"
    assert slip.index("const done =") < slip.index("dedupeResults("), \
        "the tally must be computed before anything is deduped"
    # The section path keeps the guard for the day a section-scope check kind
    # lands, and the rail keeps its tooltip: one at a time is not a wall.
    flags_fn = page[page.index("function placeDocFlags(id) {"):
                    page.index("function placeDocThreads(id) {")]
    assert "const seenResults = new Set();" in flags_fn, \
        "placeDocFlags owns its Set, constructed inside the call"
    assert "flags = dedupeResults(flags, seenResults);" in flags_fn, \
        "the callback parameter is reassigned, so the pinned render line stands"
    assert (flags_fn.index("docCell(row, 'rg').innerHTML")
            < flags_fn.index("flags = dedupeResults(")), \
        "the gutter renders first — the glyph's title keeps its full result"
    # A second parameter on marginFlagHTML would receive the ARRAY INDEX from
    # `.map` — falsy for the first flag, truthy for every other.
    assert "function marginFlagHTML(a) {" in page, \
        "marginFlagHTML takes exactly one argument; `.map` supplies the rest"
    print("test_one_decision_prints_once: OK")


def test_the_voice_composer_stays_where_it_opened(page: str) -> None:
    """Finding 14, second half. `stageVoiceComment` re-focuses the field
    after `openCommentPopover` scrolls it into view — a bare `focus()` there
    would undo that scroll and land the type chips above the fold."""
    voice = page[page.index("function stageVoiceComment(id, type, rest) {"):]
    voice = voice[:voice.index("function runQAVoiceAct(")]
    assert "ta.focus({ preventScroll: true });" in voice, \
        "the voice path's re-focus must not undo the opener's scroll"
    assert "ta.focus();" not in voice, \
        "no bare focus may survive in the voice staging path"
    print("test_the_voice_composer_stays_where_it_opened: OK")


def test_every_choice_has_a_keyboard_path(page: str) -> None:
    """Finding 15. The palette used to truncate at nine, leaving choices 10
    and 11 with no ⌘K path or keycap. The digit handler binds 1-9 only, so
    a tenth choice legitimately carries no cap — Tab reaches it instead."""
    palette = page[page.index("function qaPaletteCommands() {"):]
    palette = palette[:palette.index("\n}\n")]
    assert "live.choices.forEach((c, i) => {" in palette, \
        "the palette must list every choice"
    assert "slice(0, 9)" not in palette, \
        "the directory of the keyboard layer may not truncate itself"
    assert "key: i < 9 ? String(i + 1) : ''" in palette, \
        "a keycap is printed only where one is actually bound"
    assert "const cap = i < 9 ? `<kbd>${i + 1}</kbd>` : '';" in page, \
        "the chip's printed cap and the palette's bound key share one ceiling"
    assert "if (!isNaN(n) && n >= 1 && n <= q.choices.length)" in page, \
        "the digit handler's own ceiling is unchanged"
    print("test_every_choice_has_a_keyboard_path: OK")


def test_the_dispatch_controls_never_wrap(page: str) -> None:
    """Finding 16, cosmetic. `.btn-group`'s default `flex-shrink: 1` broke
    `skip rest & submit` onto three lines at narrow viewports; `.stats`
    wraps and absorbs the width instead."""
    assert re.search(r"\.btn-group \{[^}]*flex:\s*0 0 auto", page), \
        "the dispatch group is one unit and does not shrink"
    assert re.search(r"\.btn-group \{[^}]*display:\s*flex", page), \
        "`display: flex` stays in the stylesheet — the SSE `round` handler " \
        "restores this group with `style.display = ''` and falls back to it"
    assert re.search(r"\.btn-skip \{[^}]*white-space:\s*nowrap", page), \
        "a button narrower than its label must not break the label"
    assert re.search(r"\.btn-submit \{[^}]*white-space:\s*nowrap", page), \
        "...and its sibling takes the same rule"
    # The bar's own row does NOT wrap — a flex container breaks its line
    # BEFORE it shrinks anything, so `wrap` here would skip the shrink
    # that lets the row fit.
    assert re.search(r"\.bottom-inner \{[^}]*flex-wrap:\s*nowrap", page), \
        "the bar's row must squeeze `.stats` rather than break its own line"
    assert re.search(r"\.stats \{[^}]*flex-wrap:\s*wrap", page), \
        "`.stats` is the item that absorbs the width now the buttons will not"
    # `.stats` needs to shrink below its content width to do that.
    # `0 1 auto`, never `1 1 auto` — grow would push the stamp off the row.
    assert re.search(r"\.stats \{[^}]*flex:\s*0 1 auto", page), \
        "`.stats` must shrink and never grow"
    assert re.search(r"\.stats \{[^}]*min-width:\s*0", page), \
        "without min-width:0 a flex item will not shrink past its content"
    print("test_the_dispatch_controls_never_wrap: OK")


def test_round2_wire_shape_unchanged(base: str) -> None:
    """Hold: no schema change. The restructure is a rendering change over
    the shapes #184 already ships — `GET /input` serves the same round data
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
            test_the_head_row_is_one_track(page)
            test_a_whole_section_note_sits_at_the_section_foot(page)
            test_the_foot_band_states_and_acts(page)
            test_document_flags_leave_the_first_section(page)
            test_qa_wears_the_grammar_not_the_print(page)
            test_a_free_text_question_prints_its_question_once(page)
            test_the_stamp_never_prints_a_count_it_was_not_given(page)
            test_round2_lands_on_what_changed(page)
            test_one_decision_prints_once(page)
            test_the_voice_composer_stays_where_it_opened(page)
            test_every_choice_has_a_keyboard_path(page)
            test_the_dispatch_controls_never_wrap(page)
            test_round2_wire_shape_unchanged(base)
    print("OK")


if __name__ == "__main__":
    main()
