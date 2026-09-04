#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import parse_sections as ps  # noqa: E402


def test_attaches_open_threads_grouped_by_title():
    tmp = Path(tempfile.mkdtemp())
    store = {
        "s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "x", "status": "open",
                  "exchanges": [{"round": 1, "verdict": "changes", "note": "n", "response": ""}]},
        "s1-c2": {"cid": "s1-c2", "title": "Goals", "quote": "y", "status": "settled",
                  "exchanges": [{"round": 1, "verdict": "info", "note": "m", "response": "r"}]},
    }
    sp = tmp / "open-notes.json"
    sp.write_text(json.dumps(store))
    sections = [{"id": "s1", "title": "Goals", "content": "g"},
                {"id": "s2", "title": "Scope", "content": "s"}]
    ps._attach_open_notes(str(sp), sections)
    # Only the OPEN thread attaches; settled threads drop from the next round.
    assert [t["cid"] for t in sections[0]["open_notes"]] == ["s1-c1"]
    assert sections[0]["open_notes"][0]["quote"] == "x"
    assert "open_notes" not in sections[1]  # no threads → stays bare


def test_declined_thread_attaches_and_holds_its_section():
    """A decline resolves nothing, so the thread attaches exactly as an open
    one does — the filter here is the whole holding mechanism (#167)."""
    tmp = Path(tempfile.mkdtemp())
    store = {
        "s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "in most cases",
                  "status": "declined", "exchanges": [
                      {"round": 1, "verdict": "changes", "note": "cut the caveat",
                       "response": "", "grounds": "round 1 ruled it load-bearing"}]},
        "s1-c2": {"cid": "s1-c2", "title": "Goals", "quote": "y", "status": "settled",
                  "exchanges": [{"round": 1, "verdict": "info", "note": "m", "response": "r"}]},
    }
    sp = tmp / "open-notes.json"
    sp.write_text(json.dumps(store))
    sections = [{"id": "s1", "title": "Goals", "content": "g"}]
    ps._attach_open_notes(str(sp), sections)
    threads = sections[0]["open_notes"]
    assert [t["cid"] for t in threads] == ["s1-c1"], threads
    assert threads[0]["status"] == "declined", threads[0]
    # The grounds ride with the exchange, or the reviewer cannot read the
    # refusal they are being asked to accept or override.
    assert threads[0]["exchanges"][0]["grounds"] == "round 1 ruled it load-bearing"


def main():
    test_attaches_open_threads_grouped_by_title()
    test_declined_thread_attaches_and_holds_its_section()
    print("OK")


if __name__ == "__main__":
    main()
