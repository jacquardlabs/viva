#!/usr/bin/env python3
"""open_notes.py maintains the round-to-round open-note store (issue #16).

Rewrote for the comments[]/cid model (Tasks 1/9). The store is keyed by
comment cid — one thread per inline comment, not one per section. The old
title-keyed tests are superseded by this rewrite and by test_open_notes_unit.py.

Lifecycle:
  - comments[] with type changes/info + open:true → create/update a thread
  - settled:true on a comment                     → mark that thread settled
  - approved verdict on section                   → settle all its open threads
  - section with no `comments` key                → no-op (legacy round files safe)
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


def _input():
    return {"sections": [{"id": "s1", "title": "Goals"}, {"id": "s2", "title": "Scope"}]}


def test_pure_update():
    # R1: open two comments on Goals; first is anchored.
    store = open_notes.update({}, 1, {"sections": [
        {"id": "s1", "verdict": "changes", "comments": [
            {"cid": "s1-c1", "type": "changes", "note": "tighten intro",
             "anchor": {"text": "retries 3x", "offset": 10}, "open": True, "settled": False},
            {"cid": "s1-c2", "type": "info", "note": "why stderr?", "open": True, "settled": False},
        ]},
    ]}, _input(), {"s1-c1": "Shortened to two sentences."})
    assert set(store) == {"s1-c1", "s1-c2"}, store
    thread = store["s1-c1"]
    assert thread["status"] == "open"
    assert thread["title"] == "Goals"
    assert thread["quote"] == "retries 3x"
    assert thread["exchanges"] == [
        {"round": 1, "verdict": "changes", "note": "tighten intro",
         "response": "Shortened to two sentences."}
    ], thread

    # R2: second exchange on s1-c1 accumulates in the same thread.
    store = open_notes.update(store, 2, {"sections": [
        {"id": "s1", "verdict": "changes", "comments": [
            {"cid": "s1-c1", "type": "changes", "note": "still too wordy",
             "open": True, "settled": False},
        ]},
    ]}, _input(), {"s1-c1": "Cut further."})
    assert len(store["s1-c1"]["exchanges"]) == 2
    assert store["s1-c1"]["exchanges"][1]["round"] == 2
    assert store["s1-c1"]["status"] == "open"

    # R3: approving the section settles all its open threads.
    store = open_notes.update(store, 3, {"sections": [
        {"id": "s1", "verdict": "approved", "comments": []},
    ]}, _input(), {})
    assert store["s1-c1"]["status"] == "settled"
    assert store["s1-c2"]["status"] == "settled"
    assert len(store["s1-c1"]["exchanges"]) == 2, "approve must not append an exchange"


def test_explicit_settle_by_cid():
    store = open_notes.update({}, 1, {"sections": [
        {"id": "s1", "verdict": "changes", "comments": [
            {"cid": "s1-c1", "type": "changes", "note": "x", "open": True, "settled": False},
        ]},
    ]}, _input(), {})
    assert store["s1-c1"]["status"] == "open"
    # Marking settled by cid in the next round closes only that thread.
    store = open_notes.update(store, 2, {"sections": [
        {"id": "s1", "verdict": "changes", "comments": [
            {"cid": "s1-c1", "type": "changes", "note": "", "open": True, "settled": True},
        ]},
    ]}, _input(), {})
    assert store["s1-c1"]["status"] == "settled"


def test_zero_regression_no_comments():
    # Sections with no `comments` key (legacy or bare) leave the store empty.
    store = open_notes.update({}, 1, {"sections": [
        {"id": "s1", "verdict": "changes", "note": "old-style bare note, no comments key"},
        {"id": "s2", "verdict": "approved"},
    ]}, _input(), {})
    assert store == {}, store


def test_cli_roundtrip():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "review-input-r1.json").write_text(json.dumps(
        {"sections": [{"id": "s1", "title": "Goals"}]}))
    (viva / "review-r1.json").write_text(json.dumps(
        {"round": 1, "sections": [{"id": "s1", "verdict": "changes", "comments": [
            {"cid": "s1-c1", "type": "changes", "note": "tighten", "open": True, "settled": False},
        ]}]}))
    subprocess.run([sys.executable, str(SCRIPT), "update",
                    "--store", str(viva / "open-notes.json"),
                    "--round", "1",
                    "--verdicts", str(viva / "review-r1.json"),
                    "--input", str(viva / "review-input-r1.json"),
                    "--response", "s1-c1=Done."],
                   check=True)
    store = json.loads((viva / "open-notes.json").read_text())
    assert store["s1-c1"]["exchanges"][0]["response"] == "Done.", store
    assert store["s1-c1"]["status"] == "open"
    assert store["s1-c1"]["title"] == "Goals"


def main():
    test_pure_update()
    test_explicit_settle_by_cid()
    test_zero_regression_no_comments()
    test_cli_roundtrip()
    print("OK (4 tests)")


if __name__ == "__main__":
    main()
