#!/usr/bin/env python3
"""Unified Q&A → review session hand-off (#109).

A qa-mode server hands round-1 review sections to the SAME running server via
`/next-round` rather than a second launch — same process, same `server.url`,
distinguishable server-side only by an operational stdout line.

Covers two Critical audit fixes (`round` SSE handler must hide qa-view
unconditionally, populate the titleblock, and reset QA_DATA/qState.active so
a stray keystroke can't submit the review round early) plus a full
qa -> next-round -> review -> submit integration run.
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
from _server_harness import SERVER, get, poll_for, post, post_result, wait_for_url  # noqa: E402

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
        "the 'round' SSE handler must explicitly hide qa-view, not rely on "
        "a preceding 'processing' event"
    )
    print("  ok  test_round_handler_hides_qa_view")


def _round_handler_slice(html: str) -> str:
    start = html.index("es.addEventListener('round'")
    end = html.index("es.addEventListener('complete'")
    assert start != -1 and end != -1 and end > start
    return html[start:end]


def test_round_handler_populates_titleblock():
    """Audit fix (Critical, ux-reviewer): the hand-off `round` handler must
    populate #doc-path/#doc-title itself, via setDocTitleBlock() — previously
    only bootReviewMode() set them, which the qa boot path never calls."""
    html = server.HTML
    round_handler = _round_handler_slice(html)
    assert "setDocTitleBlock(" in round_handler, (
        "the 'round' SSE handler must call setDocTitleBlock() so a "
        "qa->review hand-off populates #doc-path/#doc-title"
    )
    # Assert setDocTitleBlock actually sets both cells, and bootReviewMode
    # still routes through the same helper (not forked into two call sites).
    fn_start = html.index("function setDocTitleBlock(")
    fn_end = html.index("\n}", fn_start)
    fn_body = html[fn_start:fn_end]
    assert "el('doc-path').textContent" in fn_body, fn_body
    assert "el('doc-title').innerHTML" in fn_body, fn_body
    boot_start = html.index("function bootReviewMode(")
    boot_end = html.index("\n}", boot_start)
    assert "setDocTitleBlock(" in html[boot_start:boot_end]
    print("  ok  test_round_handler_populates_titleblock")


def test_round_handler_resets_qa_state():
    """Audit fix (Critical, frontend-reviewer): the hand-off `round` handler
    must null QA_DATA and qState.active, or a stray post-handoff digit
    keystroke could route through the qa branch and submit the review round early."""
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
    """Defense in depth: the qa keydown branch must not fire while review
    cards are on screen, independent of stale QA_DATA/qState.active."""
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

        # Q&A phase: byte-identical to a standalone qa gate up to this point.
        served = get(base, "/input")
        assert served.get("mode") == "qa", served

        post(base, "/submit", {
            "answers": [{"id": "q1", "choice": "email", "note": ""}],
            "submitted_early": False,
        })
        assert poll_for(qa_out), "answers.json never written"
        qa_answers_snapshot = qa_out.read_text()

        # Caller's synthesis hands round-1 review sections to the SAME server.
        review_out = viva / "review-r1.json"
        review_round1 = {
            "mode": "review",
            "round": 1,
            "doc_file": "design.md",
            "sections": [
                {"id": "s1", "title": "Channel", "content": "We will use email."},
            ],
        }
        result = post(base, "/next-round", dict(review_round1, output=str(review_out)))
        assert result == {"ok": True}, result

        # Same tab contract: /input now serves the review round, same base URL.
        served2 = get(base, "/input")
        assert served2.get("mode") == "review", served2
        assert served2.get("round") == 1, served2
        assert [s["id"] for s in served2["sections"]] == ["s1"], served2

        # Regression: review output is a distinct path, so answers.json survives untouched.
        assert qa_out.read_text() == qa_answers_snapshot, \
            "qa answers.json must not be touched by the review round's /next-round"

        # Drive the review round to a verdict on that same server.
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

    # The hand-off's only signal is operational (stdout), never a wire field.
    assert "viva · qa mode ·" in out, out
    assert "viva · hand-off qa → review ·" in out, out
    # Fires exactly once: only the qa→review transition, not the review round's own /submit.
    assert out.count("viva · hand-off qa → review ·") == 1, out
    print("  ok  test_handoff_same_server_no_second_launch")


def test_standalone_qa_has_no_handoff_line():
    """No-op-when-absent: a qa server that never receives a /next-round
    prints no hand-off line."""
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


def test_a_diff_round_is_refused_by_a_qa_server():
    """#126: a qa-launched server hands off to review only. The `round` SSE
    handler never stamps `mode-diff` or injects diff2html, so `/next-round`
    refuses a `mode: "diff"` round rather than serving a broken tab."""
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
        diff_round = {
            "mode": "diff", "round": 1, "doc_file": "HEAD~1..HEAD",
            "output": str(viva / "review-r1.json"),
            "sections": [{"id": "s1", "title": "a.py hunk 1",
                          "content": "```diff\n@@ -1 +1 @@\n-a\n+b\n```"}],
        }
        status, body = post_result(base, "/next-round", diff_round)
        assert status == 400, (status, body)
        assert "'diff'" in body["error"] and "--mode qa" in body["error"], body
        served = get(base, "/input")
        assert served.get("mode") == "qa" and "questions" in served, \
            "a refused round must leave the served interview untouched: %r" % (served,)
        post(base, "/complete", {})
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)
    assert "viva · hand-off qa → review ·" not in out, out
    print("  ok  test_a_diff_round_is_refused_by_a_qa_server")


def main() -> None:
    test_round_handler_hides_qa_view()
    test_round_handler_populates_titleblock()
    test_round_handler_resets_qa_state()
    test_qa_keydown_branch_guarded_by_review_data()
    test_handoff_same_server_no_second_launch()
    test_standalone_qa_has_no_handoff_line()
    test_a_diff_round_is_refused_by_a_qa_server()
    print("OK (7 tests)")


if __name__ == "__main__":
    main()
