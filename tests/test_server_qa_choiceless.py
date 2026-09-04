#!/usr/bin/env python3
"""A question with no `choices` must render (#170 dogfooding).

`choices` is documented optional, but readers that dereferenced it as a list
(`q.choices.map` etc.) threw and the boot `.catch` blanked the entire
interview. Fixed at the boundary (normalize once where QA_DATA is assigned),
not at each call site.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from _server_harness import get, launch_server, poll_for, post  # noqa: E402

QA_INPUT = {
    "mode": "qa",
    "context": "Residual decisions",
    "questions": [
        {"id": "q1", "text": "Who signs off?", "choices": ["Leads", "On-call"]},
        # Legal per the contract (choices optional), fatal in practice pre-fix.
        {"id": "q2", "text": "What is the escalation window?",
         "hint": "Nothing in the repo fixes this number."},
    ],
}


def test_boot_normalizes_choices_to_a_list():
    """The single boundary: `QA_DATA = data;` normalizes `choices` to a list once."""
    html = server.HTML
    start = html.index("QA_DATA = data;")
    boot = html[start:start + 400]
    assert re.search(r"if\s*\(\s*!Array\.isArray\(q\.choices\)\s*\)\s*q\.choices\s*=\s*\[\];", boot), (
        "the qa boot path must normalize a missing `choices` to [] where "
        "QA_DATA is assigned:\n" + boot
    )
    print("  ok  test_boot_normalizes_choices_to_a_list")


def test_the_set_of_choices_readers_has_not_grown():
    """Tripwire: the set of `.choices` readers, all confirmed downstream of the
    boot normalization. A new reader fails this until confirmed and added."""
    html = server.HTML
    readers = set(re.findall(r"(\w+)\.choices\.(\w+)", html))
    assert readers == {("q", "map"), ("live", "forEach"), ("q", "length")}, (
        "the set of `.choices` readers changed — confirm each still runs "
        "AFTER the boot normalization, then update this list: %s"
        % sorted(readers)
    )
    # `q.choices[n - 1]` indexes rather than calls a method, so isn't in the regex above.
    assert "q.choices[n - 1]" in html
    print("  ok  test_the_set_of_choices_readers_has_not_grown")


def test_choiceless_question_serves_and_answers():
    """Round trip: a choice-less question boots, serves, and takes a free-text answer."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    qa_in, qa_out = viva / "qa-input.json", viva / "answers.json"
    qa_in.write_text(json.dumps(QA_INPUT))

    with launch_server(qa_in, qa_out, mode="qa", cwd=tmp) as base:
        served = get(base, "/input")
        assert served["mode"] == "qa", served
        q2 = next(q for q in served["questions"] if q["id"] == "q2")
        # Served untouched: the fix is client-side normalization.
        assert "choices" not in q2, q2

        post(base, "/submit", {"answers": [
            {"id": "q1", "choice": "Leads", "note": ""},
            {"id": "q2", "choice": "", "note": "5 minutes, per the SLO doc"},
        ], "submitted_early": False})
        assert poll_for(qa_out), "answers.json never written"

    answers = {a["id"]: a for a in json.loads(qa_out.read_text())["answers"]}
    assert answers["q2"]["note"] == "5 minutes, per the SLO doc", answers
    assert answers["q2"]["choice"] == "", answers
    print("  ok  test_choiceless_question_serves_and_answers")


def test_a_typed_note_is_an_answer():
    """#121: gating on `choice` alone made a typed note invisible to every
    "answered" reader, dropping it rather than marking it unanswered.
    One predicate now, every reader routed through it."""
    html = server.HTML
    fn = re.search(r"function qaAnswered\(id\)\s*\{(.*?)\n\}", html, re.S)
    assert fn, "qaAnswered() must exist as the single definition of `answered`"
    body = fn.group(1)
    assert "a.choice" in body and "a.note" in body, body
    assert ".trim()" in body, "a whitespace-only note is not an answer"
    print("  ok  test_a_typed_note_is_an_answer")


def test_every_answered_gate_routes_through_the_predicate():
    """Every "is this answered" gate routes through `qaAnswered()`; a gate
    that regresses to checking `.choice` directly fails here."""
    html = server.HTML
    assert html.count("qaAnswered(") >= 9, (
        "expected the predicate at its definition plus 8 call sites; found %d"
        % html.count("qaAnswered(")
    )
    # What may still touch `.choice` directly; anything else should route through qaAnswered().
    direct = [ln.strip() for ln in html.split("\n")
              if re.search(r"\.choice\b", ln)
              and "choices" not in ln and "choice-chip" not in ln
              and "dataset.choice" not in ln]
    assert len(direct) == 4, "unexpected direct `.choice` use:\n" + "\n".join(direct)
    joined = " ".join(direct)
    assert "return Boolean(a.choice" in joined, "the predicate itself"
    assert "a.choice = a.choice === choice" in joined, "pickQAChoice toggling"
    assert "const choice = qState.answers[id]?.choice || null" in joined, \
        "syncQACard reads the chosen chip — genuinely about `choice`, not answered-ness"
    assert "choice: a.choice || ''" in joined, \
        "the submitted payload sends '' rather than null for a note-only answer"
    print("  ok  test_every_answered_gate_routes_through_the_predicate")


def test_typing_a_note_refreshes_the_indicators():
    """The note `input` handler must refresh state too, not just store text —
    otherwise the page kept reporting an answered question as unanswered."""
    html = server.HTML
    start = html.index("qta.addEventListener('input'")
    handler = html[start:html.index("});", start)]
    assert "syncQACard(q.id)" in handler and "updateQAStats()" in handler, (
        "the note input handler must refresh the dot and progress stat:\n" + handler
    )
    print("  ok  test_typing_a_note_refreshes_the_indicators")


def test_the_contract_still_calls_choices_optional():
    """references/qa.md must still document `choices` as optional."""
    qa_md = (ROOT / "references" / "qa.md").read_text()
    assert "`choices` is optional" in qa_md, \
        "references/qa.md must still document `choices` as optional"
    print("  ok  test_the_contract_still_calls_choices_optional")


def main() -> None:
    test_boot_normalizes_choices_to_a_list()
    test_the_set_of_choices_readers_has_not_grown()
    test_choiceless_question_serves_and_answers()
    test_a_typed_note_is_an_answer()
    test_every_answered_gate_routes_through_the_predicate()
    test_typing_a_note_refreshes_the_indicators()
    test_the_contract_still_calls_choices_optional()
    print("OK (7 tests)")


if __name__ == "__main__":
    main()
