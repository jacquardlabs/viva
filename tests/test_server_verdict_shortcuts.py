#!/usr/bin/env python3
"""Regression: `c` / `i` cannot submit a verdict the payload doesn't carry (#156).

The bug: both keys called `setReviewVerdict`, which wrote a raw `verdict` field.
`submitReview` never reads that field — it derives each section from
`activeComments`, which on a comment-less card is empty, so `deriveVerdict`
returned `pending`. The card badged `changes` and the payload carried nothing:
a reviewer decision lost between the screen and the file, against PRODUCT.md's
"nothing is auto-accepted".

The fix routes both keys through the comment composer with the type preselected,
so a `changes`/`info` verdict is only ever derived from an attached note. Three
halves are pinned here, because fixing any two alone reopens the hole:

1. the routing — `c` / `i` open the composer, and no raw-verdict setter survives;
2. `deriveVerdict`'s comment-less rule — the rejected alternative was to make it
   honour a bare raw verdict, which submits "changes requested" with no
   instruction;
3. the wire — what the composer produces round-trips as `changes` with its note,
   and removing that note un-derives the verdict back to `pending`.

There is no JS engine in this suite (stdlib only, no npm), so 1 and 2 are read
off the page the server actually serves and 3 is driven over HTTP — the same
split `test_server_a11y.py` and `test_server_suggestions.py` already use.
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

    `e.key === 'c'` is not unique in the page — the interview binds it to
    "confirm answer" — so every needle below is anchored inside the review
    block rather than found by a bare `index`.
    """
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
            # Bare `c` now opens a compose box; swallowing Cmd/Ctrl+C would
            # cost the reviewer copy on a page that is mostly prose.
            line = next((ln for ln in branch.splitlines() if "e.key === '%s'" % key in ln), "")
            assert "!e.metaKey && !e.ctrlKey && !e.altKey" in line, \
                "the '%s'-key branch must be guarded against Cmd/Ctrl/Alt: %s" % (key, line)

        # The raw-verdict setter is gone outright — not just unwired. `(` in the
        # needle so the two prose mentions explaining its removal don't match.
        assert "setReviewVerdict(" not in page, \
            "no raw-verdict setter may survive: a verdict the payload can't carry is the bug"

        # The composer takes the type; `openTypedComment` picks a chip rather
        # than reaching past it, so one path decides what the box means.
        assert "function openCommentPopover(id, { anchor, type } = {})" in page, \
            "openCommentPopover must accept a preselected comment type"
        opener = page[page.index("function openTypedComment(id, type)"):]
        opener = opener[:opener.index("\n}")]
        assert "classList.contains('is-open')" in opener, \
            "an open composer must be retyped in place, never rebuilt over a half-typed note"
        assert "openCommentPopover(id, { type })" in opener
        # The invariant, stated where a rename can't dodge it: this path writes
        # comments and nothing else. A raw verdict written here is the bug.
        assert "rState.verdicts" not in opener, \
            "the c/i path must not touch the raw verdict field"
        print("  ok  test_c_and_i_open_the_composer_not_a_raw_verdict")


def test_a_saved_comment_carries_its_own_undo():
    # `setReviewVerdict`'s same-key toggle-off was the only in-round keyboard
    # undo, and it went with the function. The replacement is the comment's own
    # remove control: every margin note ships one, it is wired to
    # `removeComment`, and that repaints through `syncCard` — which reads
    # `deriveVerdict`, so dropping the last comment drops the verdict with it.
    # (#157 tracks the undo that is still missing.)
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
    # The rejected alternative to this fix was teaching deriveVerdict to honour
    # `rState.verdicts[id].verdict` when there are no comments — which submits
    # "changes requested" with nothing for the revise loop to act on. With no
    # active comments the only two answers stay `approved` (the reviewer
    # stamped it) and `pending`.
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
    # The wire half: what the composer saves is a comment, and the comment is
    # what makes the section `changes` — in the verdicts file and in the ledger.
    # Submitting the same section with that comment removed derives `pending`
    # again, which is the undo the toggle-off used to be (#157 tracks the rest).
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

        # The instruction reaches the ledger — the thing the keypress used to
        # drop on the floor.
        r2 = dict(ROUND_1, round=2)
        post(base, "/next-round?output=" + str(viva / "out2.json"), r2)
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
    # A keycap hint that outlives its behaviour is the same class of bug as the
    # one being fixed: the screen saying one thing and the code doing another.
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
