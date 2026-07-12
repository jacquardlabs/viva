#!/usr/bin/env python3
"""Recommended-choice flag on the QA schema (#114).

`QAQuestion.recommended_choice` is optional, matches a `choices` entry *by
value*, and renders as a small advisory badge on the matching chip. This
repo has no JS/browser test harness — stdlib Python only, no npm/node — so
the browser-side render is pinned as string-needle assertions against the
embedded HTML constant, matching the pattern in test_server_a11y.py /
test_server_qa_review_handoff.py. Covered here:

  1. Render is undefined-safe and additive: `buildQACard` guards on
     `q.recommended_choice !== undefined` before comparing, and only a
     matching chip gets a `.chip-badge` span — a question that omits the
     field renders byte-identical chips to before this story (pre-mortem
     risk #6).
  2. The badge is advisory only: no auto-select, no default-focus, and no
     restyle of the chip as primary — `isRecommended` never touches
     `.selected`/`classList`/`.focus()` (pre-mortem risk #1). The `hint`
     paragraph is untouched, so the recommendation's "why" still renders
     (pre-mortem risk #2).
  3. `GET /input` serves `recommended_choice` verbatim, same as every other
     QAQuestion field (no server-side stripping/transform).
  4. `validate_qa_input` runs at server *startup* (before the port binds) —
     a qa-input.json with a dangling `recommended_choice` exits 1 with the
     `viva: invalid qa-input` prefix documented in headless-contract.md §6,
     never boots a server (pre-mortem risks #3-#5).
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
from _server_harness import get, launch_server  # noqa: E402

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
    # The recommended-choice branch must not touch selection/focus state —
    # it only decides whether to append a badge <span>, nothing else.
    snippet_start = HTML.index("const choicesHtml = q.choices.map(c =>")
    snippet_end = HTML.index("}).join('');", snippet_start)
    snippet = HTML[snippet_start:snippet_end]
    assert "isRecommended" in snippet
    assert ".selected" not in snippet
    assert ".focus(" not in snippet
    assert "classList" not in snippet
    print("  ok  test_badge_is_advisory_not_selection")


def test_hint_still_renders_alongside_choices():
    # The recommendation's "why" stays in `hint` (Out of scope in the design
    # doc) — confirm the hint paragraph is untouched by this story.
    assert '<p class="section-summary">${esc(q.hint || \'\')}</p>' in HTML
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


def main() -> None:
    test_render_guards_on_undefined_recommended_choice()
    test_chip_badge_css_defined()
    test_badge_is_advisory_not_selection()
    test_hint_still_renders_alongside_choices()
    test_input_serves_recommended_choice_verbatim()
    test_startup_rejects_dangling_recommended_choice()
    print("OK (6 tests)")


if __name__ == "__main__":
    main()
