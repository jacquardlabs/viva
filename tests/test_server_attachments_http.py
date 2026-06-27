#!/usr/bin/env python3
"""Integration: /submit writes image files and rewrites output JSON."""
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixtures import PNG, PNG_B64  # noqa: E402


def post(base, path, payload):
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def main():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "body"}]}
    (viva / "in1.json").write_text(json.dumps(r1))
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--mode", "review",
         "--input", str(viva / "in1.json"), "--output", str(viva / "out1.json"),
         "--no-browser"], cwd=tmp)
    try:
        url_file = viva / "server.url"
        for _ in range(50):
            if url_file.exists():
                break
            time.sleep(0.2)
        assert url_file.exists(), "server failed to start within 10s"
        base = url_file.read_text().strip()

        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "note": "look",
             "images": [{"data": PNG_B64, "mime": "image/png"}]},
        ]})

        out = json.loads((viva / "out1.json").read_text())
        sec = out["sections"][0]
        assert "images" not in sec, "raw base64 must not reach the output JSON"
        assert sec["attachments"] == [str(viva / "attachments" / "r1-s1-0.png")], sec
        assert Path(sec["attachments"][0]).read_bytes() == PNG, "file bytes match"
        print("OK")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
