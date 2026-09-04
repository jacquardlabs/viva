#!/usr/bin/env python3
"""Regression (#156): `c`/`i` used to call `setReviewVerdict`, writing a raw
`verdict` field that `submitReview` never reads — it derives each section from
`activeComments`, so a comment-less card silently derived `pending` and the
reviewer's decision was lost.

The fix routes both keys through the comment composer instead, so a
`changes`/`info` verdict is only ever derived from an attached note. Pins the
routing, `deriveVerdict`'s comment-less rule, and the wire round-trip — fixing
only two of the three reopens the hole. No JS engine in this suite, so 1-2 are
read off the served page and 3 is driven over HTTP.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, get_text, launch_server, poll_for, post  # noqa: E402

ROUND_1 = {
    "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
    "sections": [
        {"id": "s1", "title": "Goals", "content": "## Goals\nretries 3x\n"},
        {"id": "s2", "title": "Scope", "content": "## Scope\nbody\n"},
    ],
}


def _review_branch(page: str) -> str:
    """The keydown handler's `if (REVIEW_DATA) {` block, up to the Q&A branch.
    `e.key === 'c'` isn't unique in the page (the interview binds it too), so
    needles below anchor inside this block."""
    start = page.index("if (REVIEW_DATA) {", page.index("document.addEventListener('keydown'"))
    end = page.index("if (!REVIEW_DATA && QA_DATA && qState.active)", start)
    return page[start:end]


def test_c_and_i_open_the_composer_not_a_raw_verdict():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"; viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(ROUND_1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        page = get_text(base, "/")
        branch = _review_branch(page)

        for key, ctype in (("c", "changes"), ("i", "info")):
            call = "openTypedComment(rState.active, '%s')" % ctype
            assert call in branch, "the '%s'-key branch must call %s" % (key, call)
            # Must not swallow Cmd/Ctrl+C — the reviewer still needs copy.
            line = next((ln for ln in branch.splitlines() if "e.key === '%s'" % key in ln), "")
            assert "!e.metaKey && !e.ctrlKey && !e.altKey" in line, \
                "the '%s'-key branch must be guarded against Cmd/Ctrl/Alt: %s" % (key, line)

        # `(` in the needle so prose mentioning the removed function doesn't match.
        assert "setReviewVerdict(" not in page, \
            "no raw-verdict setter may survive: a verdict the payload can't carry is the bug"

        assert "function openCommentPopover(id, { anchor, type } = {})" in page, \
            "openCommentPopover must accept a preselected comment type"
        opener = page[page.index("function openTypedComment(id, type)"):]
        opener = opener[:opener.index("\n}")]
        assert "classList.contains('is-open')" in opener, \
            "an open composer must be retyped in place, never rebuilt over a half-typed note"
        assert "openCommentPopover(id, { type })" in opener
        # This path writes comments only; a raw verdict written here is the bug.
        assert "rState.verdicts" not in opener, \
            "the c/i path must not touch the raw verdict field"
        print("  ok  test_c_and_i_open_the_composer_not_a_raw_verdict")


def test_a_saved_comment_carries_its_own_undo():
    # The comment's own remove control replaces the old toggle-off undo:
    # wired to removeComment, which repaints via syncCard/deriveVerdict.
    # #157 tracks the undo still missing.
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"; viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(ROUND_1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        page = get_text(base, "/")
        assert "class=\"nt-btn is-quiet cmt-del\" data-cid=" in page, \
            "every comment note must ship its own remove control"
        assert "removeComment(id, b.dataset.cid)" in page, \
            "the remove control must be wired to removeComment"
        fn = page[page.index("function removeComment(id, cid)"):]
        fn = fn[:fn.index("\n}")]
        assert "syncCard(id)" in fn, \
            "removing a comment must repaint the card, which re-derives the verdict"
        print("  ok  test_a_saved_comment_carries_its_own_undo")


def test_derive_verdict_still_ignores_a_bare_raw_verdict():
    # Rejected fix: honour a bare raw verdict with no comments, which submits
    # "changes requested" with nothing for the revise loop to act on.
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"; viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(ROUND_1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        page = get_text(base, "/")
        fn = page[page.index("function deriveVerdict(id)"):]
        fn = fn[:fn.index("\n}")]
        assert ("if (active.length === 0) return rState.verdicts[id]?.verdict === "
                "'approved' ? 'approved' : 'pending';") in fn, fn
        print("  ok  test_derive_verdict_still_ignores_a_bare_raw_verdict")


def test_a_typed_comment_carries_the_verdict_and_removing_it_takes_it_back():
    # The wire half: a saved comment makes the section `changes` in both the
    # verdicts file and the ledger; removing it derives `pending` again (#157).
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"; viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(ROUND_1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "comments": [
                {"cid": "s1-c1", "type": "changes", "note": "5x not 3x",
                 "open": True, "settled": False}]},
            {"id": "s2", "verdict": "approved"}]})
        assert poll_for(viva / "out1.json"), "server never wrote the verdicts file"
        out = json.loads((viva / "out1.json").read_text())
        s1 = next(s for s in out["sections"] if s["id"] == "s1")
        assert s1["verdict"] == "changes", s1
        assert s1["comments"][0]["note"] == "5x not 3x", s1

        # The instruction reaches the ledger, not dropped on the floor.
        r2 = dict(ROUND_1, round=2)
        post(base, "/next-round", dict(r2, output=str(viva / "out2.json")))
        ledger = get(base, "/input")["ledger"]
        assert {"round": 1, "section_title": "Goals", "verdict": "changes",
                "note": "5x not 3x"} in ledger, ledger

        # Comment removed → nothing derives → `pending`, and the round cannot
        # be signed off on it.
        post(base, "/submit", {"round": 2, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "pending"},
            {"id": "s2", "verdict": "approved"}]})
        assert poll_for(viva / "out2.json"), "server never wrote the round-2 verdicts file"
        out2 = json.loads((viva / "out2.json").read_text())
        s1 = next(s for s in out2["sections"] if s["id"] == "s1")
        assert s1["verdict"] == "pending", s1
        assert not s1.get("comments"), s1
        print("  ok  test_a_typed_comment_carries_the_verdict_and_removing_it_takes_it_back")


def test_legend_states_what_the_keys_now_do():
    # A stale keycap hint is the same class of bug: screen says one thing, code another.
    from _server_harness import SERVER  # noqa: E402
    text = SERVER.read_text(encoding="utf-8")
    assert ("<dt><kbd>c</kbd></dt><dd>comment &mdash; request changes (review) "
            "&middot; confirm answer (Q&amp;A)</dd>") in text, \
        "the 'c' legend row must say it opens a comment"
    assert "<dt><kbd>i</kbd></dt><dd>comment &mdash; need info</dd>" in text, \
        "the 'i' legend row must say it opens a comment"
    print("  ok  test_legend_states_what_the_keys_now_do")


def main():
    test_c_and_i_open_the_composer_not_a_raw_verdict()
    test_a_saved_comment_carries_its_own_undo()
    test_derive_verdict_still_ignores_a_bare_raw_verdict()
    test_a_typed_comment_carries_the_verdict_and_removing_it_takes_it_back()
    test_legend_states_what_the_keys_now_do()
    print("OK")


if __name__ == "__main__":
    main()
