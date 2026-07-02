#!/usr/bin/env python3
"""Integration test: two comments anchored to the SAME span (offset disambiguation).

A reviewer can leave more than one comment on the exact same selected text — e.g.
"this number is wrong" (changes) AND "where's this measured?" (info) on the same
phrase. The two are distinct threads (distinct cids); the server's /submit pipe
must preserve BOTH verbatim, each with its own anchor, type, and cid. The derived
section verdict is `changes` because at least one comment is a changes comment.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import launch_server, post  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {
        "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "## Goals\nretries 3x on timeout\n"},
        ],
    }
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:

        # Two comments on the SAME span "retries 3x on timeout" — distinct cids/types.
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "comments": [
                {"cid": "s1-c1", "type": "changes", "note": "should be 5x",
                 "anchor": {"text": "retries 3x on timeout", "offset": 9},
                 "open": True, "settled": False},
                {"cid": "s1-c2", "type": "info", "note": "where is this measured?",
                 "anchor": {"text": "retries 3x on timeout", "offset": 9},
                 "open": True, "settled": False},
            ]},
        ]})

        out = json.loads((viva / "out1.json").read_text())
        s1 = next(s for s in out["sections"] if s["id"] == "s1")

        # Both comments survive the pipe, distinct cids, both anchored to the same span.
        assert len(s1["comments"]) == 2, s1
        assert [c["cid"] for c in s1["comments"]] == ["s1-c1", "s1-c2"], s1
        assert {c["type"] for c in s1["comments"]} == {"changes", "info"}, s1
        assert all(c["anchor"]["text"] == "retries 3x on timeout" for c in s1["comments"]), s1
        # Derived verdict is `changes` because at least one comment is a changes comment.
        assert s1["verdict"] == "changes", s1

        print("OK")


if __name__ == "__main__":
    main()
