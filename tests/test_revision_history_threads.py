#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import revision_history as rh  # noqa: E402


def test_threads_grouped_by_section_with_quote():
    tmp = Path(tempfile.mkdtemp())
    store = {
        "s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "retries 3x", "status": "settled",
                  "exchanges": [{"round": 1, "verdict": "changes", "note": "5x", "response": "done"}]},
        "s1-c2": {"cid": "s1-c2", "title": "Goals", "quote": "", "status": "open",
                  "exchanges": [{"round": 1, "verdict": "info", "note": "why?", "response": "because"}]},
    }
    (tmp / "open-notes.json").write_text(json.dumps(store))
    threads = rh.collect_threads(tmp)
    assert len(threads) == 2
    block = rh.build_threads_block(threads)
    assert "### Open notes" in block
    assert "Goals" in block
    assert "retries 3x" in block          # the quoted span appears
    assert "5x" in block and "why?" in block


def main():
    test_threads_grouped_by_section_with_quote()
    print("OK")


if __name__ == "__main__":
    main()
