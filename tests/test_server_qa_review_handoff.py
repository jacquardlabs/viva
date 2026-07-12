#!/usr/bin/env python3
"""Unified Q&A → review session hand-off (#109).

jig's `/design` skill interviews the human via `qa` mode, then — instead of
tearing the server down and launching a second `server.py --mode review` —
hands the round-1 review payload to the SAME running server via `/next-round`.
That mechanism (`/next-round` + the `round` SSE broadcast) already exists for
`/viva-diff`'s in-place round advances; this test proves it also carries a
qa-launched server across the mode boundary: same process, same `server.url`,
no second launch, and the qa-originated round is distinguishable server-side
by its own stdout line — not by any new field on the wire payload, since
`schema.py`'s `ReviewInput`/`QAInput`/`QAOutput` are unchanged by this story
(see docs/superpowers/specs/2026-07-11-unified-session-design.md, "Out of
scope: Schema changes").

Checked here (this repo has no JS/browser test harness — stdlib Python only,
no npm/node — so the browser-side fixes are pinned as string-needle
assertions against the embedded HTML constant, matching the pattern in
test_server_a11y.py / test_server_markdown_sanitize.py):
  1. A `round` SSE event must hide `qa-view` unconditionally rather than
     relying on a prior `processing` event having already done so (that
     ordering isn't guaranteed — e.g. a tab reconnecting mid-transition sees
     only `round`).
  2. The `round` handler must populate #doc-path/#doc-title itself (via the
     shared setDocTitleBlock() helper) — their only previous assignment site
     was bootReviewMode(), which the qa boot path never calls, so a
     qa-originated hand-off left the titleblock rendering blank "drawing"/
     "title" cells and no "viva review" mode word (audit finding, Critical).
  3. The `round` handler must reset QA_DATA/qState.active to null, and the
     document keydown handler's qa branch must additionally gate on
     `!REVIEW_DATA` — otherwise a leftover Q&A card selection lets a
     post-handoff digit keystroke route through the qa branch, flip
     btn-submit to 'ready' via updateQAStats(), and the class-gated click
     handler calls submitReview(false), submitting the review round early
     (audit finding, Critical).
  4. A subprocess+urllib integration run of the actual hand-off: qa phase →
     `/next-round` → review phase → review `/submit`, all against one
     `server.py` process, with a regression check that the qa output file
     (`answers.json`) is never touched by the review round that follows it.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from _server_harness import SERVER, get, poll_for, post, wait_for_url  # noqa: E402

QA_INPUT = {
    "mode": "qa",
    "context": "Notification design interview",
    "questions": [
        {"id": "q1", "text": "Channel?", "choices": ["email", "sms"]},
    ],
}


def test_round_handler_hides_qa_view():
    html = server.HTML
    start = html.index("es.addEventListener('round'")
    end = html.index("es.addEventListener('complete'")
    assert start != -1 and end != -1 and end > start
    round_handler = html[start:end]
    assert "el('qa-view').style.display         = 'none';" in round_handler, (
        "the 'round' SSE handler must explicitly hide qa-view — a review "
        "round arriving without a preceding 'processing' event (a reconnect "
        "mid hand-off) must not leave qa-view showing underneath it"
    )
    print("  ok  test_round_handler_hides_qa_view")


def _round_handler_slice(html: str) -> str:
    start = html.index("es.addEventListener('round'")
    end = html.index("es.addEventListener('complete'")
    assert start != -1 and end != -1 and end > start
    return html[start:end]


def test_round_handler_populates_titleblock():
    """Audit fix (Critical, ux-reviewer): the hand-off `round` handler must
    populate #doc-path/#doc-title itself. Before this fix their only
    assignment site was bootReviewMode(), called on the review/diff boot
    paths but never on the qa boot path nor in the round handler — so a
    qa-originated hand-off rendered the titleblock's "drawing"/"title" cells
    blank and dropped the "viva review" mode word, even though data.doc_file
    was present in the payload."""
    html = server.HTML
    round_handler = _round_handler_slice(html)
    assert "setDocTitleBlock(" in round_handler, (
        "the 'round' SSE handler must call setDocTitleBlock() so a "
        "qa->review hand-off populates #doc-path/#doc-title instead of "
        "leaving them at their qa-view blank default"
    )
    # setDocTitleBlock is now the single assignment site for both cells —
    # assert it actually sets them, so gutting the helper's body couldn't
    # leave this test green while the titleblock still renders blank.
    fn_start = html.index("function setDocTitleBlock(")
    fn_end = html.index("\n}", fn_start)
    fn_body = html[fn_start:fn_end]
    assert "el('doc-path').textContent" in fn_body, fn_body
    assert "el('doc-title').innerHTML" in fn_body, fn_body
    # bootReviewMode (initial review/diff boot) must still route through the
    # same helper — the refactor must not have forked the two call sites.
    boot_start = html.index("function bootReviewMode(")
    boot_end = html.index("\n}", boot_start)
    assert "setDocTitleBlock(" in html[boot_start:boot_end]
    print("  ok  test_round_handler_populates_titleblock")


def test_round_handler_resets_qa_state():
    """Audit fix (Critical, frontend-reviewer): the hand-off `round` handler
    must null QA_DATA and qState.active. Before this fix neither was ever
    reset, so after a qa->review hand-off a stray digit keystroke (1-9) could
    still route through the document keydown handler's qa branch, flip
    btn-submit to 'ready' via updateQAStats() on Q&A completeness, and the
    class-gated click handler would call submitReview(false) — submitting
    the review round early and mislabeling it submitted_early:false."""
    html = server.HTML
    round_handler = _round_handler_slice(html)
    assert re.search(r"QA_DATA\s*=\s*null;", round_handler), (
        "the 'round' SSE handler must reset QA_DATA to null"
    )
    assert re.search(r"qState\.active\s*=\s*null;", round_handler), (
        "the 'round' SSE handler must reset qState.active to null"
    )
    print("  ok  test_round_handler_resets_qa_state")


def test_qa_keydown_branch_guarded_by_review_data():
    """Defense in depth for the same audit finding: the document keydown
    handler's qa branch must not fire while review cards are on screen,
    independent of whether QA_DATA/qState.active happen to be stale."""
    html = server.HTML
    assert re.search(r"if\s*\(\s*!REVIEW_DATA\s*&&\s*QA_DATA\s*&&\s*qState\.active\s*\)", html), (
        "the document keydown handler's qa branch must be guarded by "
        "!REVIEW_DATA so it cannot fire while a review round is displayed"
    )
    print("  ok  test_qa_keydown_branch_guarded_by_review_data")


def test_handoff_same_server_no_second_launch():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    qa_in = viva / "qa-input.json"
    qa_out = viva / "answers.json"
    qa_in.write_text(json.dumps(QA_INPUT))

    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--mode", "qa",
         "--input", str(qa_in), "--output", str(qa_out), "--no-browser"],
        cwd=str(tmp), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        base = wait_for_url(qa_out)

        # ── Q&A phase: byte-identical to standalone /viva-qa up to this point ──
        served = get(base, "/input")
        assert served.get("mode") == "qa", served

        post(base, "/submit", {
            "answers": [{"id": "q1", "choice": "email", "note": ""}],
            "submitted_early": False,
        })
        assert poll_for(qa_out), "answers.json never written"
        qa_answers_snapshot = qa_out.read_text()

        # ── jig's synthesis (outside viva) hands round-1 review sections to
        #    the SAME running server. No second server.py process, same base URL.
        review_out = viva / "review-r1.json"
        review_round1 = {
            "mode": "review",
            "round": 1,
            "doc_file": "design.md",
            "sections": [
                {"id": "s1", "title": "Channel", "content": "We will use email."},
            ],
        }
        result = post(base, "/next-round?output=" + str(review_out), review_round1)
        assert result == {"ok": True}, result

        # Same tab contract: /input now serves the review round, same base URL.
        served2 = get(base, "/input")
        assert served2.get("mode") == "review", served2
        assert served2.get("round") == 1, served2
        assert [s["id"] for s in served2["sections"]] == ["s1"], served2

        # Regression (premortem #4): the review round's output is a distinct
        # path from the qa output, so answers.json must survive untouched.
        assert qa_out.read_text() == qa_answers_snapshot, \
            "qa answers.json must not be touched by the review round's /next-round"

        # ── Drive the review round to a verdict on that same server ──────────
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "approved"},
        ]})
        assert poll_for(review_out), "review-r1.json never written"
        assert qa_out.read_text() == qa_answers_snapshot, \
            "qa answers.json must still be untouched after the review round submits"

        review_result = json.loads(review_out.read_text())
        assert review_result["sections"][0]["verdict"] == "approved", review_result
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)

    # ── The hand-off's only signal is operational (stdout), never a wire
    #    field — see /viva-qa "Hand off to a review session in the
    #    same tab (#109)". ──
    assert "viva · qa mode ·" in out, out
    assert "viva · hand-off qa → review ·" in out, out
    # Fires exactly once: only the qa→review transition qualifies, not the
    # review round's own /submit.
    assert out.count("viva · hand-off qa → review ·") == 1, out
    print("  ok  test_handoff_same_server_no_second_launch")


def test_standalone_qa_has_no_handoff_line():
    """No-op-when-absent: a qa server that never receives a sections-shaped
    /next-round (i.e. a caller that doesn't opt in) prints no hand-off line."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    qa_in = viva / "qa-input.json"
    qa_out = viva / "answers.json"
    qa_in.write_text(json.dumps(QA_INPUT))

    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--mode", "qa",
         "--input", str(qa_in), "--output", str(qa_out), "--no-browser"],
        cwd=str(tmp), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        base = wait_for_url(qa_out)
        post(base, "/submit", {
            "answers": [{"id": "q1", "choice": "email", "note": ""}],
            "submitted_early": False,
        })
        assert poll_for(qa_out), "answers.json never written"
        post(base, "/complete", {})
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)

    assert "viva · qa mode ·" in out, out
    assert "viva · hand-off qa → review ·" not in out, out
    print("  ok  test_standalone_qa_has_no_handoff_line")


def main() -> None:
    test_round_handler_hides_qa_view()
    test_round_handler_populates_titleblock()
    test_round_handler_resets_qa_state()
    test_qa_keydown_branch_guarded_by_review_data()
    test_handoff_same_server_no_second_launch()
    test_standalone_qa_has_no_handoff_line()
    print("OK (6 tests)")


if __name__ == "__main__":
    main()
