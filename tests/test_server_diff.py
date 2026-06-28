#!/usr/bin/env python3
"""Integration test: the server carries the round-to-round section `diff`
through to the client unchanged, and the page ships the diff renderer + styles.

The diff is computed by parse_sections.py and rendered inline on a rewritten
card (added/removed lines vs the prior round). The server is a dumb pipe for it
(load_input is verbatim), so the contract is: GET /input and the /next-round
push preserve the diff rows, and the page defines the renderer + collapse toggle.
"""
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def post(base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def get(base: str, path: str) -> dict:
    return json.loads(urllib.request.urlopen(base + path, timeout=5).read())


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
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--mode", "review",
         "--input", str(viva / "in1.json"), "--output", str(viva / "out1.json"),
         "--no-browser"],
        cwd=tmp,
    )
    try:
        url_file = viva / "server.url"
        for _ in range(50):
            if url_file.exists():
                break
            time.sleep(0.2)
        base = url_file.read_text().strip()

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
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        for needle in ("function diffStripHTML", "diff-block", "diff-toggle",
                       ".diff-add", ".diff-del", "section.diff"):
            assert needle in page, f"page missing: {needle}"

        print("OK")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
