#!/usr/bin/env python3
"""open_notes.py maintains the round-to-round open-note store (issue #16).

It is the SINGLE writer of .viva/open-notes.json. After each round the agent
calls `update`, passing that round's verdicts, the round's review-input (for the
id→title map), and its one-line responses. The store is keyed by normalized
section title — the same stable identity approval/annotation carry-forward use,
since section ids are positional and re-assigned each round.

Lifecycle:
  - changes/info verdict with `open:true`  → append an exchange to the thread
  - `settle:true`                          → mark the thread settled
  - approved verdict                       → settles any open thread (approval
                                             settles, so an open note never gates)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "open_notes.py"
sys.path.insert(0, str(ROOT / "scripts"))
import open_notes  # noqa: E402


def test_pure_update():
    inp = {"sections": [{"id": "s1", "title": "Goals"},
                        {"id": "s2", "title": "Scope"}]}

    # R1: open a note on Goals, a bare (untracked) changes note on Scope.
    store = open_notes.update({}, 1,
        {"sections": [
            {"id": "s1", "verdict": "changes", "note": "tighten intro", "open": True},
            {"id": "s2", "verdict": "changes", "note": "no tracking here"},
        ]},
        inp, {"s1": "Shortened to two sentences."})
    assert set(store) == {"goals"}, store
    thread = store["goals"]
    assert thread["status"] == "open"
    assert thread["title"] == "Goals"
    assert thread["exchanges"] == [
        {"round": 1, "verdict": "changes", "note": "tighten intro",
         "response": "Shortened to two sentences."}
    ], thread

    # R2: a second exchange appends to the same thread (accumulates).
    store = open_notes.update(store, 2,
        {"sections": [{"id": "s1", "verdict": "info", "note": "why two?", "open": True}]},
        inp, {"s1": "Because the rest moved to Scope."})
    assert len(store["goals"]["exchanges"]) == 2
    assert store["goals"]["exchanges"][1]["round"] == 2
    assert store["goals"]["status"] == "open"

    # R3: approving the section settles its open thread (never gates sign-off).
    store = open_notes.update(store, 3,
        {"sections": [{"id": "s1", "verdict": "approved", "note": ""}]},
        inp, {})
    assert store["goals"]["status"] == "settled"
    assert len(store["goals"]["exchanges"]) == 2, "approve must not append"


def test_explicit_settle():
    inp = {"sections": [{"id": "s1", "title": "Goals"}]}
    store = open_notes.update({}, 1,
        {"sections": [{"id": "s1", "verdict": "changes", "note": "x", "open": True}]},
        inp, {})
    store = open_notes.update(store, 2,
        {"sections": [{"id": "s1", "verdict": "pending", "note": "", "settle": True}]},
        inp, {})
    assert store["goals"]["status"] == "settled"


def test_normalized_title_keying():
    # Title casing/whitespace varies; the key is normalized so a thread sticks.
    store = open_notes.update({}, 1,
        {"sections": [{"id": "s1", "verdict": "changes", "note": "a", "open": True}]},
        {"sections": [{"id": "s1", "title": "  Goals "}]}, {})
    assert "goals" in store
    store = open_notes.update(store, 2,
        {"sections": [{"id": "s1", "verdict": "changes", "note": "b", "open": True}]},
        {"sections": [{"id": "s1", "title": "GOALS"}]}, {})
    assert list(store) == ["goals"], "must not fork into a second thread"
    assert len(store["goals"]["exchanges"]) == 2


def test_zero_regression_no_open_notes():
    # A round with no open notes leaves the store empty — feature dormant.
    store = open_notes.update({}, 1,
        {"sections": [
            {"id": "s1", "verdict": "changes", "note": "x"},
            {"id": "s2", "verdict": "approved", "note": ""},
        ]},
        {"sections": [{"id": "s1", "title": "A"}, {"id": "s2", "title": "B"}]}, {})
    assert store == {}, store


def test_cli_roundtrip():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "review-input-r1.json").write_text(json.dumps(
        {"sections": [{"id": "s1", "title": "Goals"}]}))
    (viva / "review-r1.json").write_text(json.dumps(
        {"round": 1, "sections": [
            {"id": "s1", "verdict": "changes", "note": "tighten", "open": True}]}))
    subprocess.run([sys.executable, str(SCRIPT), "update",
                    "--store", str(viva / "open-notes.json"),
                    "--round", "1",
                    "--verdicts", str(viva / "review-r1.json"),
                    "--input", str(viva / "review-input-r1.json"),
                    "--response", "s1=Done."],
                   check=True)
    store = json.loads((viva / "open-notes.json").read_text())
    assert store["goals"]["exchanges"][0]["response"] == "Done.", store
    assert store["goals"]["status"] == "open"


def main():
    test_pure_update()
    test_explicit_settle()
    test_normalized_title_keying()
    test_zero_regression_no_open_notes()
    test_cli_roundtrip()
    print("OK (5 tests)")


if __name__ == "__main__":
    main()
