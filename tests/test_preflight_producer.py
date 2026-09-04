#!/usr/bin/env python3
"""#107 asked for an auto-apply pre-flight branch; the gate scoped it down to
a pre-review producer that flags a matching standing preference as a
suggested fix, never auto-applied. Doc-contract test (style of
test_writing_register.py) asserting producers.md describes that shape.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCERS = ROOT / "references" / "producers.md"


def _bullet_text() -> str:
    """Bullet text between its header and the next heading, so assertions
    can't accidentally match unrelated prose elsewhere in the file."""
    text = PRODUCERS.read_text(encoding="utf-8")
    start = text.index("Pre-flight pre-fix")
    end = text.index("## Confidence triage", start)
    return text[start:end]


def test_bullet_exists_immediately_after_learned_preferences():
    text = PRODUCERS.read_text(encoding="utf-8")
    learned_at = text.index("**Learned preferences**")
    preflight_at = text.index("Pre-flight pre-fix")
    confidence_at = text.index("## Confidence triage")
    assert learned_at < preflight_at < confidence_at, (
        "the pre-flight producer bullet must sit after Learned preferences "
        "and before the Confidence triage heading")
    print("  ok  test_bullet_exists_immediately_after_learned_preferences")


def test_references_the_issue_and_the_gate_decision_shape():
    bullet = _bullet_text()
    assert "#107" in bullet, \
        "the bullet should cite the issue it scopes down"
    low = bullet.lower()
    assert "standing preference" in low, \
        "the producer must key off standing preferences, same store as Learned preferences"
    print("  ok  test_references_the_issue_and_the_gate_decision_shape")


def test_emits_a_concrete_suggested_fix_not_just_a_match():
    """The gate comment's exact bar: 'this critique applies here' is not
    enough — it must be 'here specifically is the fix'."""
    bullet = _bullet_text()
    low = bullet.lower()
    assert "concrete" in low and "fix" in low, \
        "the bullet must require a concrete textual fix, not just a critique match"
    assert "here specifically is the fix" in low or (
        "not just" in low and "applies here" in low
    ), "the bullet must draw the line the gate comment drew: a match alone isn't enough"
    print("  ok  test_emits_a_concrete_suggested_fix_not_just_a_match")


def test_never_auto_applies_and_stays_visible():
    bullet = _bullet_text()
    low = bullet.lower()
    assert "nothing is auto-applied" in low or "never" in low and "auto" in low, \
        "the bullet must explicitly disclaim auto-applying any fix"
    assert "visible" in low or "margin" in low, \
        "the bullet must say the suggestion surfaces visibly, same as every other annotation"
    assert "human" in low and ("decide" in low or "sees" in low), \
        "the bullet must say the human still sees and decides"
    print("  ok  test_never_auto_applies_and_stays_visible")


def test_reuses_kind_preference_and_the_bracket_id_convention():
    """No new annotation kind or schema field: the id rides as a leading
    '[id]' token in the message, exactly like Learned preferences."""
    bullet = _bullet_text()
    assert 'kind: "preference"' in bullet, (
        "the pre-flight producer must reuse kind: \"preference\" — a new "
        "annotation kind is out of scope for this issue")
    assert re.search(r"\[cite-sources\]", bullet), (
        "the bullet must show the same [id] leading-token convention as "
        "Learned preferences, e.g. [cite-sources]")
    print("  ok  test_reuses_kind_preference_and_the_bracket_id_convention")


def test_does_not_touch_confidence_triage_section():
    """Guards the no-merge-conflict instruction: this change must not alter
    anything at or after the Confidence triage heading."""
    text = PRODUCERS.read_text(encoding="utf-8")
    triage = text[text.index("## Confidence triage"):]
    assert "Pre-flight pre-fix" not in triage, (
        "the new bullet must live entirely before the Confidence triage "
        "heading, not spill into or after it")
    print("  ok  test_does_not_touch_confidence_triage_section")


def main() -> None:
    test_bullet_exists_immediately_after_learned_preferences()
    test_references_the_issue_and_the_gate_decision_shape()
    test_emits_a_concrete_suggested_fix_not_just_a_match()
    test_never_auto_applies_and_stays_visible()
    test_reuses_kind_preference_and_the_bracket_id_convention()
    test_does_not_touch_confidence_triage_section()
    print("OK")


if __name__ == "__main__":
    main()
