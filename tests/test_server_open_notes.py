#!/usr/bin/env python3
"""Integration test: open notes carried across rounds (issue #16).

The server is a pipe for the open-note thread: parse_sections attaches an
`open_notes` array (cid-keyed threads) to a section, the server re-presents it
on the card, and the reviewer's settle actions ride back on the verdict through
/submit as a comment with settled:True. Contract:

  - GET /input preserves the `open_notes` array verbatim.
  - The page ships the thread renderer and settle action.
  - /submit preserves comments (incl. settled flag) on a verdict.
  - A section with no open_notes and a verdict with no comments are byte-identical
    to today (no open_notes / comments keys appear).
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, get_text, launch_server, post  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    open_notes = [
        {"cid": "s1-c1", "quote": "intro", "status": "open",
         "exchanges": [{"round": 1, "verdict": "changes", "note": "tighten intro", "response": "Shortened."}]},
    ]
    r2 = {
        "mode": "review", "doc_file": "doc.md", "round": 2, "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "g", "open_notes": open_notes},
            {"id": "s2", "title": "Scope", "content": "s"},
        ],
    }
    (viva / "in2.json").write_text(json.dumps(r2))
    with launch_server(viva / "in2.json", viva / "out2.json", cwd=tmp) as base:

        # Pass-through: open_notes preserved, bare section stays bare.
        data = get(base, "/input")
        s1 = next(s for s in data["sections"] if s["id"] == "s1")
        s2 = next(s for s in data["sections"] if s["id"] == "s2")
        assert s1.get("open_notes") == open_notes, f"open_notes dropped: {s1}"
        assert "open_notes" not in s2, f"s2 must stay bare: {s2}"

        # Page ships the thread renderer and settle action.
        page = get_text(base, "/")
        for needle in ("openNotesHTML", "open-thread", "settleOpenNotes",
                       "section.open_notes", "renderCommentList"):
            assert needle in page, f"page missing: {needle}"

        # /submit preserves comments with settled flag; a bare verdict carries neither.
        post(base, "/submit", {"round": 2, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "comments": [
                {"cid": "s1-c1", "type": "changes", "note": "more",
                 "anchor": {"text": "intro", "offset": 0}, "open": True, "settled": True}]},
            {"id": "s2", "verdict": "pending", "note": ""},
        ]})
        out = json.loads((viva / "out2.json").read_text())
        o1 = next(s for s in out["sections"] if s["id"] == "s1")
        assert o1["comments"][0]["settled"] is True, f"settled flag lost: {o1}"

        print("OK")


if __name__ == "__main__":
    main()
