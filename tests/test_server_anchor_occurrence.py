#!/usr/bin/env python3
"""Anchor resolution is ordinal-aware, not first-match (#95).

`offsetInSource` used to return `src.indexOf(text)` — the first occurrence in
the section's markdown source, whatever the reviewer actually selected. A
comment on a repeated phrase pointed at the wrong span; applied to a verbatim
replacement (the suggested-edit story that builds on this) it is a wrong edit.

The selection exists only in the rendered HTML, so the browser reads the
occurrence ordinal there and resolves the *same* ordinal against the source.
That ordinal rides out on the anchor as `occurrence` — a RENDERED ordinal, not
a source one; the two sequences can diverge over markdown syntax the renderer
strips, which is why `offset` can be -1 while `anchor.text` is still present in
the source. The ordinal is what makes the on-screen highlight and the stored
offset name one span.

There is no JS harness in this repo, so this follows the precedent of
`test_server_a11y.py` / `test_frontend_v2_phase1.py`: string-needle assertions
against the shipped `server.HTML` constant. One needle pins each mirrored line
of JS — the two helpers' loop bodies and `offsetInSource`'s fallback branch —
and the Python mirrors below carry the behavioral cases. An edit to any of the
three breaks its needle, which is the signal to update the mirror; without that
pinning the cases would keep passing against JS they no longer describe.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

HTML = server.HTML

# The exact JS the mirrors below reproduce. Both step by 1 rather than by
# needle length: counting and resolving must agree on overlapping repeats or
# the ordinal names a different span in the source than it did on screen.
JS_COUNT_LOOP = "while (i >= 0 && i < limit) { n++; i = hay.indexOf(needle, i + 1); }"
JS_NTH_LOOP = "while (i >= 0 && n > 0) { i = hay.indexOf(needle, i + 1); n--; }"
# The narrowing the mirror's third function carries: an overrun ordinal falls
# back only when the source holds exactly one match, never onto the first of
# several. Pinned too — without it this branch could be deleted from the JS and
# every behavioral case below would still pass against a stale mirror.
JS_FALLBACK = (
    "if (at < 0 && n > 0 && nthIndexOf(src, text, 1) < 0) return nthIndexOf(src, text, 0);"
)


# ── Mirrors of the shipped JS helpers (kept in lockstep by the needles above) ──
def count_starts_before(hay, needle, limit):
    if not needle:
        return 0
    n = 0
    i = hay.find(needle)
    while i >= 0 and i < limit:
        n += 1
        i = hay.find(needle, i + 1)
    return n


def nth_index_of(hay, needle, n):
    if not needle:
        return -1
    i = hay.find(needle)
    while i >= 0 and n > 0:
        i = hay.find(needle, i + 1)
        n -= 1
    return i


def offset_in_source(src, text, occurrence):
    n = occurrence if occurrence and occurrence > 0 else 0
    at = nth_index_of(src, text, n)
    if at < 0 and n > 0 and nth_index_of(src, text, 1) < 0:
        return nth_index_of(src, text, 0)
    return at


# ── Static assertions on the shipped SPA ──────────────────────────────────────
def test_bare_first_match_indexof_is_gone():
    # The whole bug in one needle: no code path may look up the anchor span by
    # a bare first-match search of the source.
    assert "src.indexOf(text)" not in HTML, \
        "offsetInSource must not resolve the anchor by first match"
    assert "function offsetInSource(id, text, occurrence)" in HTML, \
        "offsetInSource must take the reviewer's chosen occurrence"
    print("  ok  test_bare_first_match_indexof_is_gone")


def test_ordinal_helpers_ship_with_the_pinned_loops():
    assert "function occurrenceInRendered(root, range, text)" in HTML
    assert "function countStartsBefore(hay, needle, limit)" in HTML
    assert "function nthIndexOf(hay, needle, n)" in HTML
    assert JS_COUNT_LOOP in HTML, "countStartsBefore's loop changed — update the mirror below"
    assert JS_NTH_LOOP in HTML, "nthIndexOf's loop changed — update the mirror below"
    # offsetInSource resolves the ordinal, never a raw first match.
    assert "const at = nthIndexOf(src, text, n);" in HTML
    assert JS_FALLBACK in HTML, "offsetInSource's fallback changed — update the mirror below"
    print("  ok  test_ordinal_helpers_ship_with_the_pinned_loops")


def test_selection_reads_the_ordinal_from_the_rendered_content():
    # The ordinal is counted where the selection lives (rendered HTML) and then
    # handed to offsetInSource, which resolves it against the markdown source.
    assert "const occurrence = occurrenceInRendered(content, sel.getRangeAt(0), text);" in HTML, \
        "the ordinal must be read from the selection's own range, in document order"
    assert "{ anchor: { text, offset: offsetInSource(m[1], text, occurrence), occurrence } }" in HTML, \
        "the anchor must carry both the resolved offset and the ordinal that produced it"
    # getRangeAt(0), not anchorNode/focusNode: a backwards drag reports its
    # endpoints reversed, which would count the prefix past the selection.
    assert "!sel.rangeCount" in HTML, "the handler must bail when there is no range to read"
    # One scope, the section's own content. The prefix count used to be scoped
    # to the diff pane the selection began in, because side-by-side's facing
    # pane repeated every line; unified renders one column and there is no
    # facing pane to scope out.
    assert "if (!root.contains || !root.contains(range.startContainer)) return 0;" in HTML
    print("  ok  test_selection_reads_the_ordinal_from_the_rendered_content")


def test_highlight_follows_the_same_ordinal():
    # The rendered half of the same bug: wrapFirst marked occurrence 0 while the
    # stored offset named occurrence N. Gone in favor of an nth-aware wrap fed
    # by the anchor's own occurrence.
    assert "function wrapFirst(" not in HTML, "wrapFirst must not remain"
    assert "function wrapNth(root, needle, cls, n)" in HTML
    # One marking pass, not two. `renderHighlights` wrapped the anchors and
    # `markAndPin` wrapped them again to hang the numbers, and two passes can
    # disagree about which span is note 3; with the margin on every surface the
    # first pass has no caller left. The ordinal contract is unchanged — it is
    # `markAndPin` that carries it through to wrapNth now.
    assert "function renderHighlights(" not in HTML, \
        "the second marking pass must not come back"
    assert ("const mark = wrapNth(content, a.text, 'cmt-hl-' + type, "
            "a.occurrence > 0 ? a.occurrence : 0);") in HTML, \
        "markAndPin must pass the anchor's occurrence through to wrapNth"
    print("  ok  test_highlight_follows_the_same_ordinal")


# ── Behavioral cases against the mirrors ──────────────────────────────────────
SRC = "retries 3x here, retries 3x there, retries 3x everywhere"
FIRST, SECOND, THIRD = 0, 17, 35
assert SRC[FIRST:FIRST + 7] == "retries" and SRC[SECOND:SECOND + 7] == "retries"
assert SRC[THIRD:THIRD + 7] == "retries"


def test_each_occurrence_resolves_to_its_own_offset():
    assert offset_in_source(SRC, "retries", 0) == FIRST
    assert offset_in_source(SRC, "retries", 1) == SECOND
    assert offset_in_source(SRC, "retries", 2) == THIRD
    # The regression proper: a later occurrence must not collapse onto the first.
    assert offset_in_source(SRC, "retries", 2) != offset_in_source(SRC, "retries", 0)
    print("  ok  test_each_occurrence_resolves_to_its_own_offset")


def test_overrun_ordinal_stays_unresolved_when_ambiguous():
    # Rendered and source occurrence counts can diverge. With two or more
    # matches an overrun ordinal must report -1 (the agent falls back to
    # anchor.text + occurrence) rather than silently returning the first match,
    # which is the bug this story fixes.
    assert offset_in_source(SRC, "retries", 9) == -1
    assert offset_in_source(SRC, "no such text", 0) == -1
    assert offset_in_source(SRC, "", 0) == -1
    print("  ok  test_overrun_ordinal_stays_unresolved_when_ambiguous")


def test_overrun_ordinal_falls_back_only_when_unambiguous():
    # Exactly one match in the source: the ordinal is irrelevant, so an overrun
    # still resolves to the single unambiguous span.
    single = "the timeout is 30s"
    assert offset_in_source(single, "timeout", 3) == single.index("timeout")
    assert offset_in_source(single, "timeout", 0) == single.index("timeout")
    print("  ok  test_overrun_ordinal_falls_back_only_when_unambiguous")


def test_ordinal_counts_occurrences_that_start_before_the_selection():
    rendered = "retries 3x here, retries 3x there, retries 3x everywhere"
    # A selection beginning at each occurrence yields that occurrence's ordinal.
    assert count_starts_before(rendered, "retries", FIRST) == 0
    assert count_starts_before(rendered, "retries", SECOND) == 1
    assert count_starts_before(rendered, "retries", THIRD) == 2
    # An occurrence that starts before the selection but extends past it still
    # counts as "before" — the ordinal is by start index, not containment.
    assert count_starts_before("aaaa", "aaa", 1) == 1
    assert count_starts_before(rendered, "", 10) == 0
    print("  ok  test_ordinal_counts_occurrences_that_start_before_the_selection")


def test_rendered_ordinal_survives_markdown_syntax_the_renderer_strips():
    # The reviewer reads `retries 3x here, **retries 3x** there`, selects the
    # second "retries", and the ordinal (1) resolves to the source occurrence
    # inside the emphasis markers — not the first one.
    src = "retries 3x here, **retries 3x** there"
    rendered = "retries 3x here, retries 3x there"
    ordinal = count_starts_before(rendered, "retries", rendered.index("retries", 1))
    assert ordinal == 1
    assert offset_in_source(src, "retries", ordinal) == src.index("retries", 1)
    print("  ok  test_rendered_ordinal_survives_markdown_syntax_the_renderer_strips")


def main():
    test_bare_first_match_indexof_is_gone()
    test_ordinal_helpers_ship_with_the_pinned_loops()
    test_selection_reads_the_ordinal_from_the_rendered_content()
    test_highlight_follows_the_same_ordinal()
    test_each_occurrence_resolves_to_its_own_offset()
    test_overrun_ordinal_stays_unresolved_when_ambiguous()
    test_overrun_ordinal_falls_back_only_when_unambiguous()
    test_ordinal_counts_occurrences_that_start_before_the_selection()
    test_rendered_ordinal_survives_markdown_syntax_the_renderer_strips()
    print("OK (9 tests)")


if __name__ == "__main__":
    main()
