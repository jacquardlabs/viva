#!/usr/bin/env python3
"""Integration test: server accumulates a verbatim ledger across rounds."""
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
    r1 = {
        "mode": "review",
        "doc_file": "doc.md",
        "round": 1,
        "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "goals body"},
            {"id": "s2", "title": "Error Handling", "content": "errors body"},
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

        # Round 1 has no ledger yet
        assert get(base, "/input").get("ledger") == [], "round 1 ledger must be empty"

        note = "Notifications should be within 30 seconds | with a pipe"
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "note": note},
            {"id": "s2", "verdict": "info", "note": "How long in the DLQ?"},
        ]})
        post(base, "/next-round?output=" + str(viva / "out2.json"), dict(r1, round=2))
        ledger = get(base, "/input")["ledger"]
        assert ledger == [
            {"round": 1, "section_title": "Goals", "verdict": "changes", "note": note},
            {"round": 1, "section_title": "Error Handling", "verdict": "info",
             "note": "How long in the DLQ?"},
        ], f"unexpected ledger: {ledger}"

        # approved adds nothing; changes with empty note still gets a row
        post(base, "/submit", {"round": 2, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "approved", "note": ""},
            {"id": "s2", "verdict": "changes", "note": ""},
        ]})
        post(base, "/next-round?output=" + str(viva / "out3.json"), dict(r1, round=3))
        ledger = get(base, "/input")["ledger"]
        assert len(ledger) == 3, f"expected 3 entries, got {len(ledger)}"
        assert ledger[2] == {"round": 2, "section_title": "Error Handling",
                             "verdict": "changes", "note": ""}
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        for needle in ('id="ledger"', 'id="complete-ledger"',
                       "function renderLedger", "function ledgerRowsHTML",
                       ".ledger-verdict.v-changes"):
            assert needle in page, f"page missing: {needle}"
        print("OK")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
