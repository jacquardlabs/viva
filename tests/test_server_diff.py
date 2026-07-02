#!/usr/bin/env python3
"""Integration test: the server carries the round-to-round section `diff`
through to the client unchanged, and the page ships the diff renderer + styles.

The diff is computed by parse_sections.py and rendered inline on a rewritten
card (added/removed lines vs the prior round). The server is a dumb pipe for it
(load_input is verbatim), so the contract is: GET /input and the /next-round
push preserve the diff rows, and the page defines the renderer + collapse toggle.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, get_text, launch_server, post  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    diff = [
        {"op": " ", "text": "## Goals"},
        {"op": "-", "text": "old goal"},
        {"op": "+", "text": "new goal"},
    ]
    r1 = {
        "mode": "review",
        "doc_file": "doc.md",
        "round": 2,
        "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "goals body", "diff": diff},
            {"id": "s2", "title": "Scope", "content": "scope body"},
        ],
    }
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        # Pass-through: GET /input preserves the diff rows verbatim.
        data = get(base, "/input")
        s1 = next(s for s in data["sections"] if s["id"] == "s1")
        s2 = next(s for s in data["sections"] if s["id"] == "s2")
        assert s1.get("diff") == diff, f"diff dropped: {s1}"
        assert "diff" not in s2, f"s2 must stay bare: {s2}"

        # Pass-through across a round push: /next-round body reflected in /input.
        r2 = dict(r1, round=3)
        post(base, "/next-round?output=" + str(viva / "out2.json"), r2)
        data = get(base, "/input")
        s1 = next(s for s in data["sections"] if s["id"] == "s1")
        assert s1.get("diff") == diff, f"diff lost across round: {s1}"

        # Page ships the renderer, the diff markup hook, the collapse toggle,
        # and the add/del line styles reusing the verdict color slots.
        page = get_text(base, "/")
        for needle in ("function diffStripHTML", "diff-block", "diff-toggle",
                       ".diff-add", ".diff-del", "section.diff"):
            assert needle in page, f"page missing: {needle}"

        print("OK")


if __name__ == "__main__":
    main()
