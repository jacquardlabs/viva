#!/usr/bin/env python3
"""Unit tests for scripts/schema.py — the shared protocol contract."""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import schema  # noqa: E402


def test_section_key_normalizes():
    assert schema.section_key("  Error Handling  ") == "error handling"
    assert schema.section_key("GOALS") == "goals"
    # Identity keeps internal punctuation/spaces — distinct from checklist._norm
    assert schema.section_key("Non-goals") == "non-goals"
    assert schema.section_key("Non-goals") != "nongoals"
    print("  ok  test_section_key_normalizes")


def test_section_key_handles_none_and_empty():
    assert schema.section_key(None) == ""
    assert schema.section_key("") == ""
    assert schema.section_key("   ") == ""
    print("  ok  test_section_key_handles_none_and_empty")


def test_is_ledger_verdict():
    assert schema.is_ledger_verdict("changes") is True
    assert schema.is_ledger_verdict("info") is True
    assert schema.is_ledger_verdict("approved") is False
    assert schema.is_ledger_verdict("pending") is False
    assert schema.is_ledger_verdict(None) is False
    print("  ok  test_is_ledger_verdict")


def test_ledger_note_joins_comments():
    section = {"comments": [
        {"note": "fix the intro"},
        {"note": "and the title"},
        {"note": ""},          # blank notes are dropped from the join
    ]}
    assert schema.ledger_note(section) == "fix the intro · and the title"
    print("  ok  test_ledger_note_joins_comments")


def test_ledger_note_falls_back_to_note():
    # Older single-note shape (no comments[]) reads `note` verbatim
    assert schema.ledger_note({"note": "shorten this"}) == "shorten this"
    # No comments, no note → empty (a changes with no text is valid)
    assert schema.ledger_note({}) == ""
    # Empty comments list falls through to note
    assert schema.ledger_note({"comments": [], "note": "x"}) == "x"
    print("  ok  test_ledger_note_falls_back_to_note")


def test_verdict_to_ledger_entry():
    row = schema.verdict_to_ledger_entry(
        2, "Error Handling",
        {"id": "s2", "verdict": "changes", "comments": [{"note": "5x not 3x"}]},
    )
    assert row == {"round": 2, "section_title": "Error Handling",
                   "verdict": "changes", "note": "5x not 3x"}, row
    # info also earns a row
    assert schema.verdict_to_ledger_entry(
        1, "Goals", {"verdict": "info", "note": "how long?"}) == {
        "round": 1, "section_title": "Goals", "verdict": "info", "note": "how long?"}
    # approved / pending earn nothing
    assert schema.verdict_to_ledger_entry(1, "Goals", {"verdict": "approved"}) is None
    assert schema.verdict_to_ledger_entry(1, "Goals", {"verdict": "pending"}) is None
    print("  ok  test_verdict_to_ledger_entry")


def test_validate_review_input_accepts_valid():
    schema.validate_review_input({
        "mode": "review", "round": 1, "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "body"},
            {"id": "s2", "title": "Errors", "content": "",
             "annotations": [{"kind": "drift", "severity": "warn", "message": "x"}]},
        ],
    })
    # Empty section list is structurally valid
    schema.validate_review_input({"mode": "review", "sections": []})
    print("  ok  test_validate_review_input_accepts_valid")


def test_validate_review_input_rejects_bad():
    for bad in (
        None,
        {"sections": "nope"},
        {"sections": [{"id": "s1", "title": "T"}]},          # missing content
        {"sections": [{"id": "s1", "content": "c"}]},         # missing title
        {"sections": [{"title": "T", "content": "c"}]},       # missing id
        {"sections": ["not an object"]},
    ):
        try:
            schema.validate_review_input(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")
    print("  ok  test_validate_review_input_rejects_bad")


def test_validate_verdicts_accepts_valid():
    schema.validate_verdicts({"round": 1, "submitted_early": False, "sections": [
        {"id": "s1", "verdict": "approved"},
        {"id": "s2", "verdict": "changes", "comments": [{"note": "x"}]},
        {"id": "s3", "verdict": "pending"},
    ]})
    schema.validate_verdicts({"sections": []})
    print("  ok  test_validate_verdicts_accepts_valid")


def test_validate_verdicts_rejects_bad():
    for bad in (
        None,
        {"sections": "nope"},
        {"sections": [{"id": "s1", "verdict": "bogus"}]},   # unknown verdict
        {"sections": [{"verdict": "approved"}]},             # missing id
        {"sections": [{"id": "s1"}]},                        # missing verdict
    ):
        try:
            schema.validate_verdicts(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")
    print("  ok  test_validate_verdicts_rejects_bad")


def test_schema_reaches_no_io():
    """`round_is_complete()` is the finish guard `loop.py finish` and the
    server's `/complete` handler both ask, from two processes. It must judge the
    dicts handed to it and nothing else — a disk read would let the two call
    sites answer differently for the same round, and would make the server's
    guard readable by whoever last wrote the file rather than by what the human
    submitted.

    Checked as a module property, not a call trace: `schema.py` imports no
    filesystem or serialization module at all, so nothing in it can reach disk.
    AST-walked rather than grepped — the module docstring names `json.dumps` and
    `_input_data` while describing the server's `/input` merge, and a substring
    scan would fire on prose.
    """
    src = (Path(__file__).resolve().parent.parent / "scripts" / "schema.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    banned = {"os", "pathlib", "json", "io", "shutil", "tempfile", "subprocess"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in banned, \
                    "schema.py must stay pure — imports %s" % alias.name
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in banned, \
                "schema.py must stay pure — imports from %s" % node.module
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "open", "schema.py must stay pure — calls open()"
    print("  ok  test_schema_reaches_no_io")


def main():
    test_section_key_normalizes()
    test_section_key_handles_none_and_empty()
    test_is_ledger_verdict()
    test_ledger_note_joins_comments()
    test_ledger_note_falls_back_to_note()
    test_verdict_to_ledger_entry()
    test_validate_review_input_accepts_valid()
    test_validate_review_input_rejects_bad()
    test_validate_verdicts_accepts_valid()
    test_validate_verdicts_rejects_bad()
    test_schema_reaches_no_io()
    print("OK (11 tests)")


if __name__ == "__main__":
    main()
