#!/usr/bin/env python3
"""Unit tests for open_notes.update — per-cid threading (multi-comment)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import open_notes  # noqa: E402


def _input():
    return {"sections": [{"id": "s1", "title": "Goals"}, {"id": "s2", "title": "Scope"}]}


def test_two_open_comments_become_two_threads():
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "changes", "note": "5x not 3x",
         "anchor": {"text": "retries 3x", "offset": 10}, "open": True, "settled": False},
        {"cid": "s1-c2", "type": "info", "note": "why stderr?", "open": True, "settled": False},
    ]}]}
    out = open_notes.update({}, 1, verdicts, _input(), {"s1-c1": "set to 5x"})
    assert set(out) == {"s1-c1", "s1-c2"}
    assert out["s1-c1"]["title"] == "Goals"
    assert out["s1-c1"]["quote"] == "retries 3x"
    assert out["s1-c1"]["status"] == "open"
    assert out["s1-c1"]["exchanges"][0] == {
        "round": 1, "verdict": "changes", "note": "5x not 3x", "response": "set to 5x"}
    assert out["s1-c2"]["exchanges"][0]["response"] == ""  # no response supplied


def test_settle_one_thread_by_cid():
    store = {"s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "x",
                       "status": "open", "exchanges": []}}
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "changes", "note": "", "open": True, "settled": True}]}]}
    out = open_notes.update(store, 2, verdicts, _input(), {})
    assert out["s1-c1"]["status"] == "settled"


def test_approving_section_settles_all_its_threads():
    store = {
        "s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "x", "status": "open", "exchanges": []},
        "s1-c2": {"cid": "s1-c2", "title": "Goals", "quote": "y", "status": "open", "exchanges": []},
        "s2-c1": {"cid": "s2-c1", "title": "Scope", "quote": "z", "status": "open", "exchanges": []},
    }
    verdicts = {"sections": [{"id": "s1", "verdict": "approved", "comments": []}]}
    out = open_notes.update(store, 2, verdicts, _input(), {})
    assert out["s1-c1"]["status"] == "settled"
    assert out["s1-c2"]["status"] == "settled"
    assert out["s2-c1"]["status"] == "open"  # untouched section stays open


def test_no_comments_is_noop():
    verdicts = {"sections": [{"id": "s1", "verdict": "approved"}]}
    assert open_notes.update({}, 1, verdicts, _input(), {}) == {}


def test_legacy_section_without_comments_is_noop():
    """A bare legacy section {anchor, note, open} with no `comments` key must
    not crash open_notes.update and must leave the store empty — an in-flight
    old round file cannot break the thread store."""
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "note": "x",
                              "anchor": "y", "open": True}]}
    assert open_notes.update({}, 1, verdicts, _input(), {}) == {}


def main():
    test_two_open_comments_become_two_threads()
    test_settle_one_thread_by_cid()
    test_approving_section_settles_all_its_threads()
    test_no_comments_is_noop()
    test_legacy_section_without_comments_is_noop()
    print("OK")


if __name__ == "__main__":
    main()
