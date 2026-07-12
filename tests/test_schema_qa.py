#!/usr/bin/env python3
"""Unit tests for validate_qa_input in scripts/schema.py"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from schema import validate_qa_input


def test_valid_qa_input_passes():
    validate_qa_input({
        "mode": "qa",
        "context": "test topic",
        "questions": [{"id": "q1", "text": "Which?", "choices": ["A", "B"]}],
    })
    print("test_valid_qa_input_passes: OK")


def test_missing_questions_key_raises():
    try:
        validate_qa_input({"mode": "qa"})
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "questions" in str(e).lower(), str(e)
    print("test_missing_questions_key_raises: OK")


def test_question_missing_id_raises():
    try:
        validate_qa_input({"questions": [{"text": "What?"}]})
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "id" in str(e).lower(), str(e)
    print("test_question_missing_id_raises: OK")


def test_question_missing_text_raises():
    try:
        validate_qa_input({"questions": [{"id": "q1"}]})
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "text" in str(e).lower(), str(e)
    print("test_question_missing_text_raises: OK")


def test_empty_questions_list_is_valid():
    validate_qa_input({"questions": []})
    print("test_empty_questions_list_is_valid: OK")


def test_question_without_choices_is_valid():
    # choices are optional in the schema
    validate_qa_input({"questions": [{"id": "q1", "text": "Open question?"}]})
    print("test_question_without_choices_is_valid: OK")


# ── recommended_choice (issue #114) ─────────────────────────────────────────

def test_question_without_recommended_choice_is_valid():
    # Backward compat is structural: omitting the field is indistinguishable
    # from every qa-input.json written before it existed.
    validate_qa_input({
        "questions": [{"id": "q1", "text": "Which?", "choices": ["A", "B"]}],
    })
    print("test_question_without_recommended_choice_is_valid: OK")


def test_recommended_choice_matching_entry_passes():
    validate_qa_input({
        "questions": [{
            "id": "q1", "text": "Which?", "choices": ["A", "B"],
            "recommended_choice": "A",
        }],
    })
    print("test_recommended_choice_matching_entry_passes: OK")


def test_recommended_choice_not_in_choices_raises():
    # Value-based matching that doesn't exactly hit an entry (typo, trailing
    # space, case) must be a loud ValueError, not a silent no-badge misfire.
    try:
        validate_qa_input({
            "questions": [{
                "id": "q1", "text": "Which?", "choices": ["A", "B"],
                "recommended_choice": "a",
            }],
        })
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "recommended_choice" in str(e), str(e)
    print("test_recommended_choice_not_in_choices_raises: OK")


def test_recommended_choice_without_choices_raises():
    # The presence guard must precede the membership test — a missing
    # `choices` key must not raise TypeError/KeyError when tested for `in`.
    try:
        validate_qa_input({
            "questions": [{
                "id": "q1", "text": "Which?", "recommended_choice": "A",
            }],
        })
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "recommended_choice" in str(e), str(e)
    print("test_recommended_choice_without_choices_raises: OK")


def test_recommended_choice_with_non_list_choices_raises():
    try:
        validate_qa_input({
            "questions": [{
                "id": "q1", "text": "Which?", "choices": "A",
                "recommended_choice": "A",
            }],
        })
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "recommended_choice" in str(e), str(e)
    print("test_recommended_choice_with_non_list_choices_raises: OK")


def test_recommended_choice_non_string_raises():
    try:
        validate_qa_input({
            "questions": [{
                "id": "q1", "text": "Which?", "choices": ["A", "B"],
                "recommended_choice": 0,
            }],
        })
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "recommended_choice" in str(e), str(e)
    print("test_recommended_choice_non_string_raises: OK")


def main() -> None:
    test_valid_qa_input_passes()
    test_missing_questions_key_raises()
    test_question_missing_id_raises()
    test_question_missing_text_raises()
    test_empty_questions_list_is_valid()
    test_question_without_choices_is_valid()
    test_question_without_recommended_choice_is_valid()
    test_recommended_choice_matching_entry_passes()
    test_recommended_choice_not_in_choices_raises()
    test_recommended_choice_without_choices_raises()
    test_recommended_choice_with_non_list_choices_raises()
    test_recommended_choice_non_string_raises()
    print("\nAll schema QA tests passed.")


if __name__ == "__main__":
    main()
