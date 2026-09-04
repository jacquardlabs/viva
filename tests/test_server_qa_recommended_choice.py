#!/usr/bin/env python3
"""Recommended-choice flag on the QA schema (#114).

`QAQuestion.recommended_choice` is optional, matches a `choices` entry by
value, and renders as an advisory badge (never auto-select/default-focus) on
the matching chip. Server-side, it's served verbatim and validated at startup.

`grounds` (#175) classifies how a recommendation was arrived at — sourced
renders the same badge relabeled, inferred hides behind a `<details>` reveal,
taste labels the question rather than a chip, and absent behaves as before.

String-needle assertions against the embedded HTML constant (no JS/browser harness).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server.py"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from _server_harness import get, launch_server, poll_for, post  # noqa: E402

HTML = server.HTML


def test_render_guards_on_undefined_recommended_choice():
    assert "q.recommended_choice !== undefined" in HTML, (
        "buildQACard must guard on recommended_choice being undefined before "
        "comparing, so a question that omits the field never badges a chip"
    )
    print("  ok  test_render_guards_on_undefined_recommended_choice")


def test_chip_badge_css_defined():
    assert ".chip-badge {" in HTML, "recommended-choice badge CSS must exist"
    print("  ok  test_chip_badge_css_defined")


def test_badge_is_advisory_not_selection():
    # Must not touch selection/focus state — only decides whether to append a badge.
    snippet_start = HTML.index("const choicesHtml = q.choices.map((c, i) =>")
    snippet_end = HTML.index("}).join('');", snippet_start)
    snippet = HTML[snippet_start:snippet_end]
    assert "isRecommended" in snippet
    assert ".selected" not in snippet
    assert ".focus(" not in snippet
    assert "classList" not in snippet
    print("  ok  test_badge_is_advisory_not_selection")


def test_hint_still_renders_alongside_choices():
    # The recommendation's "why" stays in `hint`, rendered as a margin note.
    assert '<div class="nt nt-check"><div class="nh">hint</div>' in HTML, \
        "the hint must render as a margin note in the machine's ink"
    assert "${q.hint ? `" in HTML, "a question with no hint must print no note"
    print("  ok  test_hint_still_renders_alongside_choices")


def test_input_serves_recommended_choice_verbatim():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    qa_input = {
        "mode": "qa",
        "context": "test topic",
        "questions": [
            {"id": "q1", "text": "Retry strategy?",
             "choices": ["Exponential backoff", "Fixed interval"],
             "recommended_choice": "Exponential backoff"},
            {"id": "q2", "text": "No recommendation here?",
             "choices": ["A", "B"]},
        ],
    }
    inp = viva / "qa-input.json"
    out = viva / "answers.json"
    inp.write_text(json.dumps(qa_input))
    with launch_server(inp, out, mode="qa", cwd=tmp) as base:
        served = get(base, "/input")
        assert served["questions"][0]["recommended_choice"] == "Exponential backoff", served
        assert "recommended_choice" not in served["questions"][1], served
    print("  ok  test_input_serves_recommended_choice_verbatim")


def test_startup_rejects_dangling_recommended_choice():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    bad_input = {
        "mode": "qa",
        "questions": [
            {"id": "q1", "text": "Which?", "choices": ["A", "B"],
             "recommended_choice": "C"},
        ],
    }
    inp = viva / "qa-input.json"
    out = viva / "answers.json"
    inp.write_text(json.dumps(bad_input))
    result = subprocess.run(
        [sys.executable, str(SERVER), "--mode", "qa",
         "--input", str(inp), "--output", str(out), "--no-browser"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "viva: invalid qa-input" in result.stderr, result.stderr
    assert "recommended_choice" in result.stderr, result.stderr
    assert not (viva / "server.url").exists(), "server must not bind a port on rejection"
    print("  ok  test_startup_rejects_dangling_recommended_choice")


# ── grounds (issue #175) ─────────────────────────────────────────────────────

def test_recommended_badge_helper_covers_all_grounds():
    start = HTML.index("function recommendedBadge(grounds) {")
    end = HTML.index("\n}", start)
    snippet = HTML[start:end]
    assert "'sourced'" in snippet, "sourced must relabel the ambient badge"
    assert "'inferred'" in snippet and "return '';" in snippet, (
        "inferred must render no ambient badge on the chip"
    )
    # Fallback: absent or unknown grounds must not change the render.
    assert "recommended</span>" in snippet
    print("  ok  test_recommended_badge_helper_covers_all_grounds")


def test_inferred_recommendation_renders_behind_a_reveal():
    assert "class=\"chip-reveal\"" in HTML, "inferred must use a details/summary reveal"
    assert "<details class=\"chip-reveal\"><summary>" in HTML
    assert "q.grounds === 'inferred'" in HTML
    print("  ok  test_inferred_recommendation_renders_behind_a_reveal")


def test_taste_label_decorates_the_question_not_a_chip():
    assert "chip-badge-taste" in HTML
    assert "this one is yours" in HTML
    assert "q.grounds === 'taste'" in HTML
    print("  ok  test_taste_label_decorates_the_question_not_a_chip")


def test_order_qa_questions_no_reorder_when_grounds_absent():
    # Toggle is keyed on data presence, not a default-on reorder.
    start = HTML.index("function orderQAQuestions(questions) {")
    end = HTML.index("\n}", start)
    snippet = HTML[start:end]
    assert "questions.some(q => q.grounds)" in snippet
    assert "return questions;" in snippet
    print("  ok  test_order_qa_questions_no_reorder_when_grounds_absent")


def test_accepted_recommendation_recorded_at_submit():
    # Computed server-side: a value comparison, not an answered-gate.
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    qa_input = {
        "mode": "qa",
        "questions": [
            {"id": "q1", "text": "Retry strategy?",
             "choices": ["Exponential backoff", "Fixed interval"],
             "recommended_choice": "Exponential backoff"},
            {"id": "q2", "text": "Log format?", "choices": ["JSON", "Plain text"],
             "recommended_choice": "JSON"},
            {"id": "q3", "text": "No recommendation here?", "choices": ["A", "B"]},
        ],
    }
    inp = viva / "qa-input.json"
    out = viva / "answers.json"
    inp.write_text(json.dumps(qa_input))
    with launch_server(inp, out, mode="qa", cwd=tmp) as base:
        post(base, "/submit", {"answers": [
            {"id": "q1", "choice": "Exponential backoff", "note": ""},  # followed it
            {"id": "q2", "choice": "Plain text", "note": ""},           # went the other way
            {"id": "q3", "choice": "A", "note": ""},                    # no recommendation to accept
        ], "submitted_early": False})
        assert poll_for(out), "answers.json never written"

    answers = {a["id"]: a for a in json.loads(out.read_text())["answers"]}
    assert answers["q1"]["accepted_recommendation"] is True, answers
    assert answers["q2"]["accepted_recommendation"] is False, answers
    assert "accepted_recommendation" not in answers["q3"], answers
    print("  ok  test_accepted_recommendation_recorded_at_submit")


def test_input_serves_grounds_verbatim():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    qa_input = {
        "mode": "qa",
        "context": "test topic",
        "questions": [
            {"id": "q1", "text": "Retry strategy?",
             "choices": ["Exponential backoff", "Fixed interval"],
             "recommended_choice": "Exponential backoff", "grounds": "sourced"},
            {"id": "q2", "text": "Log format?",
             "choices": ["JSON", "Plain text"],
             "recommended_choice": "JSON", "grounds": "inferred"},
            {"id": "q3", "text": "Which name?", "choices": ["Foo", "Bar"],
             "grounds": "taste"},
            {"id": "q4", "text": "No grounds here?", "choices": ["A", "B"]},
        ],
    }
    inp = viva / "qa-input.json"
    out = viva / "answers.json"
    inp.write_text(json.dumps(qa_input))
    with launch_server(inp, out, mode="qa", cwd=tmp) as base:
        served = get(base, "/input")
        by_id = {q["id"]: q for q in served["questions"]}
        assert by_id["q1"]["grounds"] == "sourced", served
        assert by_id["q2"]["grounds"] == "inferred", served
        assert by_id["q3"]["grounds"] == "taste", served
        assert "recommended_choice" not in by_id["q3"], served
        assert "grounds" not in by_id["q4"], served
    print("  ok  test_input_serves_grounds_verbatim")


def test_startup_rejects_taste_with_recommended_choice():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    bad_input = {
        "mode": "qa",
        "questions": [
            {"id": "q1", "text": "Which?", "choices": ["A", "B"],
             "recommended_choice": "A", "grounds": "taste"},
        ],
    }
    inp = viva / "qa-input.json"
    out = viva / "answers.json"
    inp.write_text(json.dumps(bad_input))
    result = subprocess.run(
        [sys.executable, str(SERVER), "--mode", "qa",
         "--input", str(inp), "--output", str(out), "--no-browser"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert "viva: invalid qa-input" in result.stderr, result.stderr
    assert "taste" in result.stderr, result.stderr
    assert not (viva / "server.url").exists(), "server must not bind a port on rejection"
    print("  ok  test_startup_rejects_taste_with_recommended_choice")


def main() -> None:
    test_render_guards_on_undefined_recommended_choice()
    test_chip_badge_css_defined()
    test_badge_is_advisory_not_selection()
    test_hint_still_renders_alongside_choices()
    test_input_serves_recommended_choice_verbatim()
    test_startup_rejects_dangling_recommended_choice()
    test_recommended_badge_helper_covers_all_grounds()
    test_inferred_recommendation_renders_behind_a_reveal()
    test_taste_label_decorates_the_question_not_a_chip()
    test_order_qa_questions_no_reorder_when_grounds_absent()
    test_accepted_recommendation_recorded_at_submit()
    test_input_serves_grounds_verbatim()
    test_startup_rejects_taste_with_recommended_choice()
    print("OK (13 tests)")


if __name__ == "__main__":
    main()
