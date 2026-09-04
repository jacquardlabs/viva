"""Regression pins for the 2026-09 UX walk of the review print.

Every assertion here is a defect that was reproduced in a browser against a
live round and then fixed; each one names the fix it guards. Static needles
over the page source, in the repo's own pattern — no browser in CI.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402

HTML = server.HTML


def _rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{[^}]*\}", HTML)
    assert m, f"page missing rule: {selector}"
    return m.group(0)


def _keydown() -> str:
    start = HTML.index("document.addEventListener('keydown', e => {")
    return HTML[start:]


def test_the_print_holds_its_measure():
    # 1/2. Prose ran 128–159 characters a line on every note-less round, then
    # the whole document rewrapped when the first composer opened. The print
    # never collapses its margin; only the accordion does.
    assert "doc.classList.toggle('no-margin', !margin && !isContinuousPrint());" in HTML
    print("  ok  test_the_print_holds_its_measure")


def test_programmatic_scrolls_clear_the_fixed_bars():
    # 3. The composer opened under the fixed bottom bar; 22. the masthead is
    # sticky, so jumps land beneath it too.
    html_rule = _rule("html")
    assert "scroll-padding-bottom:" in html_rule and "scroll-padding-top:" in html_rule
    assert "position: sticky" in _rule(".mode-doc .header")
    # Chrome ignores scroll-padding for a smooth `scrollIntoView`, so the
    # composer reads the paddings itself and scrolls by the difference.
    assert "revealWithinBars(pop);" in HTML and "function revealWithinBars(node)" in HTML
    assert "pop.scrollIntoView({ block: 'nearest' })" not in HTML
    print("  ok  test_programmatic_scrolls_clear_the_fixed_bars")


def test_escape_closes_the_composer():
    # 4. Only the mouse `cancel` closed it. An empty box cancels, a draft
    # only blurs, and it sits ahead of the TEXTAREA guard that used to eat it.
    kd = _keydown()
    esc = kd.index("const pop = document.querySelector('.comment-popover.is-open');")
    guard = kd.index("if (tag === 'TEXTAREA' || tag === 'INPUT') return;")
    assert esc < guard, "the composer's Escape must run before the textarea guard"
    assert "if (ta && ta.value.trim()) ta.blur(); else pop.querySelector('.cmt-cancel')?.click();" in kd
    print("  ok  test_escape_closes_the_composer")


def test_live_copy_never_wears_the_settled_ink():
    # 6. --faint (2.15:1 light, 2.90:1 dark) carried the blocked status, the
    # dispatch label, the only comment-gesture hint, the legend, approved
    # headings, and the focused field border.
    for sel in (".stat-pending", ".stat-lat", ".doc-hint", ".kbd-legend",
                ".btn-submit.disabled", ".doc-section.is-approved .doc-head",
                ".note-field:focus", ".thread-reply-field:focus",
                ".rv-pending", ".recap-notes", ".recap-id",
                ".carried-show, .carried-withdraw", ".pal-input::placeholder",
                ".note-field::placeholder", ".thread-reply-field::placeholder"):
        body = _rule(sel)
        assert "--faint" not in body and "--text3" not in body, f"{sel} wears the settled ink"
    assert ".doc-section.is-approved .rp { opacity: 0.72; }" in HTML, \
        "approved prose dims to the floor that still clears 4.5:1, not below it"
    assert ".doc-section.is-approved .row-head .rp { opacity: 1; }" in HTML, \
        "the heading is not dimmed twice"
    print("  ok  test_live_copy_never_wears_the_settled_ink")


def test_the_recap_never_opens_on_the_escape_hatch():
    # 5. `o` then Enter dispatched the round with every section skipped.
    assert "(ready ? el('recap-confirm') : el('recap-close')).focus();" in HTML
    assert "el('recap-skip').focus()" not in HTML
    print("  ok  test_the_recap_never_opens_on_the_escape_hatch")


def test_one_number_prints_once():
    # 7. The same count rendered five times in the first viewport, plus 24
    # zero counters across eight foot bands.
    assert "sub.textContent = 'approve — dispatch';" in HTML
    assert "conv.style.display = b.open !== b.atStart ? '' : 'none';" in HTML
    assert "(s.comments ? item('comments open', s.comments, true) : '')" in HTML
    assert "(s.declined ? item('author kept as-is', s.declined, true) : '')" in HTML, \
        "a decline is open judgment and prints in the open ink"
    assert "_lastRTT < SLOW_RTT_MS" in HTML
    print("  ok  test_one_number_prints_once")


def test_the_invitation_leads_the_print():
    # 8. The only line teaching the core gesture sat after section 8.
    assert HTML.index('id="doc-hint"') < HTML.index('id="review-cards"')
    assert HTML.index('id="doc-hint"') < HTML.index('id="sort-toggle"')
    assert "color: var(--soft)" in _rule(".doc-hint")
    print("  ok  test_the_invitation_leads_the_print")


def test_approval_and_activation_move_nothing():
    # 9. Approving drew a rule under the heading and shifted the page 17px;
    # 10. the `a` keycap printed on all eight buttons but acted on one.
    assert 'min-height: 5px' in _rule('.doc .row-head [id^="rseg-"]')
    assert ".doc-section:not(.is-active) .doc-acts kbd { visibility: hidden; }" in HTML
    print("  ok  test_approval_and_activation_move_nothing")


def test_keyboard_paths_are_real():
    # 11. Shift+Tab deactivated the section instead of moving back.
    # 13. The palette advertised j/l/t/⇧⏎ and bound none of them.
    # 16. Margin-note keycaps r/s/y/n rendered unbound.
    kd = _keydown()
    assert kd.count("if (e.key === 'Tab' && !e.shiftKey)") == 2
    assert "el('rbtn-primary-' + next.id)?.focus({ preventScroll: true });" in HTML, \
        "Tab advances focus with the section"
    pal = HTML[HTML.index("function reviewPaletteCommands"):HTML.index("function voicePaletteCommand")]
    for key in re.findall(r"key: '([a-z])'", pal):
        assert f"e.key === '{key}'" in kd, f"the palette prints `{key}` but nothing binds it"
    assert "_palCmds.findIndex(c => c.key === '⇧⏎')" in kd
    assert "'rsyn'.includes(e.key)" in kd
    assert "if (el('ledger').style.display !== 'none') cmds.push({ label: 'Open revision ledger', key: 'l', run: openLedger });" in HTML, \
        "a verb that cannot act is not listed"
    for kbd in ("<kbd>t</kbd>", "<kbd>l</kbd>", "<kbd>j</kbd>", "<kbd>Esc</kbd>"):
        assert kbd in HTML, f"legend missing {kbd}"
    assert "<dd>submit all</dd>" not in HTML, "the legend names a label the page never shows"
    print("  ok  test_keyboard_paths_are_real")


def test_focus_returns_where_it_came_from():
    # 12. Closing the palette or the composer dropped focus to <body>.
    op = HTML[HTML.index("function openPalette"):HTML.index("function renderPalette")]
    assert "_palReturnTo = document.activeElement;" in op and "setBackgroundInert(true);" in op
    assert "setBackgroundInert(false);" in op and "back.focus({ preventScroll: true })" in op
    cl = HTML[HTML.index("function closeCommentPopover"):]
    cl = cl[:cl.index("\n}\n") + 3]
    assert "pop._returnTo" in cl and "back.focus({ preventScroll: true })" in cl
    assert 'aria-activedescendant' in HTML and 'class="pal-row' in HTML and 'tabindex="-1"' in HTML
    print("  ok  test_focus_returns_where_it_came_from")


def test_refusals_are_said_not_swallowed():
    # 14. Empty save rewrote the placeholder in silence; 15. `done · N
    # comments` was an enabled button that did nothing.
    assert 'id="sr-status" role="status" aria-live="polite"' in HTML
    assert "function announce(text)" in HTML
    assert "ta.setAttribute('aria-invalid', 'true');" in HTML
    assert "btn.setAttribute('aria-disabled', refused ? 'true' : 'false');" in HTML
    assert "&#10003; done ·" not in HTML, "no checkmark on a section whose verdict is changes"
    assert "announce('approve is refused while comments are open" in HTML
    print("  ok  test_refusals_are_said_not_swallowed")


def test_the_print_does_not_animate():
    # 17. Per-section fadeUp outside the reduced-motion block; five smooth
    # scrolls hardcoded.
    assert ".doc-section { position: relative; }" in HTML
    assert not re.search(r"\.doc-section \{[^}]*animation", HTML)
    rm = re.search(r"@media \(prefers-reduced-motion: reduce\) \{.*?\n\}", HTML, re.S)
    assert rm and "html { scroll-behavior: auto; }" in rm.group(0) and ".doc-section" in rm.group(0)
    assert "behavior: 'smooth'" not in HTML, "every programmatic scroll asks SMOOTH"
    print("  ok  test_the_print_does_not_animate")


def test_the_bar_s_controls_keep_one_grammar():
    # 18. ⚡ on skip read as ⌥; the escape hatch rested heavier than the
    # primary and lightened on hover. 19. prefs toggle had its own radius,
    # border, and face; save was teal; emoji were the only colour glyphs.
    for gone in ("&#9889;", "&#128206;", "&#127908;"):
        assert gone not in HTML, gone
    assert "border: 1px solid var(--rule)" in _rule(".btn-skip")
    assert ".btn-skip:hover { border-color: var(--ink); color: var(--ink); }" in HTML
    assert ".sort-toggle:hover { color: var(--ink); border-color: var(--ink); }" in HTML
    prefs = _rule(".prefs-toggle")
    assert "border-radius: 0" in prefs and "var(--rule)" in prefs and "ui-monospace" in prefs
    assert ".cmt-save { --c: var(--acc); color: var(--acc); }" in HTML
    assert ".lflag-error { color: var(--fact)" in HTML and "var(--violet)" in _rule(".annot-error")
    print("  ok  test_the_bar_s_controls_keep_one_grammar")


def test_structure_and_labels():
    # 20/21/24/26 and the a11y lane: chips do not break mid-label, the recap
    # numbers like the print, tables take their own width, the title is the
    # h1, every field has a name, the ledger head is a button.
    assert "white-space: nowrap" in _rule(".cmt-chip")
    assert "'<span class=\"recap-id\">' + (i + 1) + '</span>'" in HTML
    assert ".doc .rp > table, .doc .rp > .table-wrap > table { width: auto; max-width: 100%; }" in HTML
    # Tables hold the measure; only code takes the margin's track.
    assert ".doc.print .row.wide:not(:has(> .rm)):has(> .rp > pre, > .rp > .d2h-wrapper) .rp { grid-column: 2 / 4; }" in HTML
    assert ".doc.print .row.wide:not(:has(> .rm)) .rp {" not in HTML
    assert '<h1 class="tb-val mono" id="doc-path"></h1>' in HTML, "the document's name is the page's h1"
    for label in ('aria-label="Comment"', 'aria-label="Reply"',
                  'aria-label="Context for this answer"', 'aria-label="Command"'):
        assert label in HTML, f"unlabeled field: {label}"
    assert '<button type="button" class="ledger-head" id="ledger-head" aria-expanded="true" aria-controls="ledger-body">' in HTML
    assert "wrap.inert = !expanded;" in HTML, "a collapsed accordion body leaves the tab order"
    assert 'aria-pressed="true">request changes' in HTML
    summary = re.search(r"\n\.section-summary\s*\{[^}]*\}", HTML).group(0)
    assert "ui-monospace" in summary and "var(--soft)" in summary
    assert "'&#8645; restore document order' : '&#8645; sort weakest first'" in HTML
    print("  ok  test_structure_and_labels")


def test_narrow_widths():
    # 28/29/30. The bar wraps under 920px, controls become touch targets, and
    # the voice strip aligns with the bar.
    narrow = [m.group(0) for m in re.finditer(r"@media \(max-width: 920px\) \{.*?\n\}", HTML, re.S)]
    joined = "\n".join(narrow)
    assert ".bottom-inner { flex-wrap: wrap" in joined
    assert "min-height: 44px" in joined
    assert ".mode-doc .voice-strip, .mode-qa .voice-strip { max-width: 1054px; }" in HTML
    print("  ok  test_narrow_widths")


def main() -> None:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("OK")


if __name__ == "__main__":
    main()
