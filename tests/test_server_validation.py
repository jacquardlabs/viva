#!/usr/bin/env python3
"""Integration test: the server validates review verdicts at the /submit boundary.

schema.validate_verdicts is wired into the POST /submit handler (gated on a
`sections` payload so Q&A is unaffected). A structurally invalid verdict must be
rejected with 400 before it can corrupt the ledger or output; a valid one passes.
"""
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def post_status(base: str, path: str, payload: dict) -> int:
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        return urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "body"}]}
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
        assert url_file.exists(), "server.url never appeared"
        base = url_file.read_text().strip()

        # Unknown verdict → rejected at the boundary
        bad = {"round": 1, "submitted_early": False,
               "sections": [{"id": "s1", "verdict": "bogus"}]}
        assert post_status(base, "/submit", bad) == 400, "invalid verdict must be 400"

        # Section missing an id → rejected
        bad2 = {"round": 1, "submitted_early": False,
                "sections": [{"verdict": "approved"}]}
        assert post_status(base, "/submit", bad2) == 400, "missing id must be 400"

        # Valid verdicts → accepted
        good = {"round": 1, "submitted_early": False,
                "sections": [{"id": "s1", "verdict": "approved"}]}
        assert post_status(base, "/submit", good) == 200, "valid submit must be 200"

        print("OK")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
