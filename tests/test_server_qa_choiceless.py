#!/usr/bin/env python3
"""A question with no `choices` must render (#170 dogfooding).

`choices` is documented OPTIONAL — "omitting it renders a free-text field only"
(`references/qa.md`). Three client readers took it as a list anyway: the chip
builder's `q.choices.map`, the palette's `live.choices.slice`, and the digit
handler's `q.choices.length`. The first ran during render, so a single
choice-less question threw `Cannot read properties of undefined (reading 'map')`
and the boot `.catch` replaced the **entire interview** with a load-error line —
not one broken card, no cards at all.

Found by pointing `/viva-write`'s own interview at a fixture, which is where it
would always have surfaced: the residual questions that flow most naturally from
attachments ("what is the escalation window?") are exactly the ones with no
fixed answer set to offer.

Fixed at the boundary, not at the three call sites (CLAUDE.md: normalize where
data enters, because a transform applied at every call site is missed by the
next one). This repo has no JS harness — stdlib only, no npm — so the client
half is a string-needle assertion against the embedded HTML constant, matching
test_server_a11y.py and test_server_qa_review_handoff.py. The server half is a
real round trip.
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
        # The one that broke it: legal per the contract, fatal in practice.
        {"id": "q2", "text": "What is the escalation window?",
         "hint": "Nothing in the repo fixes this number."},
    ],
}


def test_boot_normalizes_choices_to_a_list():
    """The single boundary. `QA_DATA` is assigned once, on the qa boot path, and
    every downstream reader is entitled to a list from there on."""
    html = server.HTML
    start = html.index("QA_DATA = data;")
    boot = html[start:start + 400]
    assert re.search(r"if\s*\(\s*!Array\.isArray\(q\.choices\)\s*\)\s*q\.choices\s*=\s*\[\];", boot), (
        "the qa boot path must normalize a missing `choices` to [] where "
        "QA_DATA is assigned — guarding each reader instead leaves the next "
        "one to rediscover this:\n" + boot
    )
    print("  ok  test_boot_normalizes_choices_to_a_list")


def test_the_set_of_choices_readers_has_not_grown():
    """A tripwire, not an ordering proof. It cannot check that a reader runs
    after the boot normalization — only that the set of readers is the one a
    human already confirmed is downstream of it. A fourth reader fails this
    test, which is the point: someone has to look at where it runs."""
    html = server.HTML
    readers = set(re.findall(r"(\w+)\.choices\.(\w+)", html))
    assert readers == {("q", "map"), ("live", "slice"), ("q", "length")}, (
        "the set of `.choices` readers changed — confirm each still runs "
        "AFTER the boot normalization, then update this list: %s"
        % sorted(readers)
    )
    # `q.choices[n - 1]` indexes rather than calls a method, so it is not in the
    # regex above; an empty array makes the `n <= q.choices.length` guard false
    # before it is ever reached.
    assert "q.choices[n - 1]" in html
    print("  ok  test_the_set_of_choices_readers_has_not_grown")


def test_choiceless_question_serves_and_answers():
    """The round trip: a choice-less question boots, serves, and takes a
    free-text answer with no `choice`."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    qa_in, qa_out = viva / "qa-input.json", viva / "answers.json"
    qa_in.write_text(json.dumps(QA_INPUT))

    with launch_server(qa_in, qa_out, mode="qa", cwd=tmp) as base:
        served = get(base, "/input")
        assert served["mode"] == "qa", served
        q2 = next(q for q in served["questions"] if q["id"] == "q2")
        # Served through untouched: the fix is client-side normalization, not a
        # rewrite of what the caller wrote.
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
    """#121, the other half of #120. A free-text-only question can never set
    `choice` — there is no chip — so gating on `choice` alone made a typed note
    invisible to the progress stat, the dot, the auto-advance, the dispatch
    button, and the submit filter, which DROPPED the answer rather than marking
    it unanswered. The reviewer's text never reached `answers.json`.

    One predicate, every reader through it — the same boundary discipline the
    `choices` normalization above follows."""
    html = server.HTML
    fn = re.search(r"function qaAnswered\(id\)\s*\{(.*?)\n\}", html, re.S)
    assert fn, "qaAnswered() must exist as the single definition of `answered`"
    body = fn.group(1)
    assert "a.choice" in body and "a.note" in body, body
    assert ".trim()" in body, "a whitespace-only note is not an answer"
    print("  ok  test_a_typed_note_is_an_answer")


def test_every_answered_gate_routes_through_the_predicate():
    """The six readers that ask "is this answered". Set-equality on what is
    LEFT dereferencing `.choice` directly, so a gate that regresses to the old
    test — or a new one written the old way — fails here."""
    html = server.HTML
    assert html.count("qaAnswered(") >= 9, (
        "expected the predicate at its definition plus 8 call sites; found %d"
        % html.count("qaAnswered(")
    )
    # What may still touch `.choice` directly, and why. Anything else is a gate
    # that should have been routed through qaAnswered().
    direct = [ln.strip() for ln in html.split("\n")
              if re.search(r"\.choice\b", ln)
              and "choices" not in ln and "choice-chip" not in ln
              and "dataset.choice" not in ln]
    assert len(direct) == 4, "unexpected direct `.choice` use:\n" + "\n".join(direct)
    joined = " ".join(direct)
    assert "return Boolean(a.choice" in joined, "the predicate itself"
    assert "a.choice = a.choice === choice" in joined, "pickQAChoice toggling"
    assert "const choice = qState.answers[id]?.choice || null" in joined, \
        "syncQACard reads the chosen chip to select it and label the badge — " \
        "that is genuinely about `choice`, not about being answered"
    assert "choice: a.choice || ''" in joined, \
        "the submitted payload sends '' rather than null for a note-only answer"
    print("  ok  test_every_answered_gate_routes_through_the_predicate")


def test_typing_a_note_refreshes_the_indicators():
    """The second half of the defect. The predicate alone is not enough: the
    note's `input` handler stored the text and refreshed nothing, so the page
    kept reporting the question unanswered while the state held the answer.
    Only a chip click ever triggered a sync."""
    html = server.HTML
    start = html.index("qta.addEventListener('input'")
    handler = html[start:html.index("});", start)]
    assert "syncQACard(q.id)" in handler and "updateQAStats()" in handler, (
        "the note input handler must refresh the dot, the confirm button and "
        "the progress stat — a typed answer moves the same indicators a chip "
        "does:\n" + handler
    )
    print("  ok  test_typing_a_note_refreshes_the_indicators")


def test_the_contract_still_calls_choices_optional():
    """The prose the fix restores. If this line ever goes, the guard above is
    enforcing a rule nothing promises."""
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
