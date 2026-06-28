#!/usr/bin/env python3
"""Integration: a section submits multiple typed comments; verdict is derived."""
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def post(base, path, payload):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=5).read()


def main():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"; viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "## Goals\nretries 3x\nlog to stderr\n"},
                       {"id": "s2", "title": "Scope", "content": "## Scope\nbody\n"}]}
    (viva / "in1.json").write_text(json.dumps(r1))
    proc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--mode", "review",
                             "--input", str(viva / "in1.json"), "--output", str(viva / "out1.json"),
                             "--no-browser"], cwd=tmp)
    try:
        uf = viva / "server.url"
        for _ in range(50):
            if uf.exists(): break
            time.sleep(0.2)
        assert uf.exists(), "server failed to start"
        base = uf.read_text().strip()

        # The page ships the new comment machinery.
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        for needle in ("deriveVerdict", "addComment", "comments"):
            assert needle in page, f"page missing: {needle}"

        # A section with two typed comments → derived verdict "changes"; a
        # comment-free section → "approved" with no comments noise.
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "comments": [
                {"cid": "s1-c1", "type": "changes", "note": "5x not 3x",
                 "anchor": {"text": "retries 3x", "offset": 9}, "open": True, "settled": False},
                {"cid": "s1-c2", "type": "info", "note": "why stderr?", "open": True, "settled": False}]},
            {"id": "s2", "verdict": "approved"}]})

        out = json.loads((viva / "out1.json").read_text())
        s1 = next(s for s in out["sections"] if s["id"] == "s1")
        s2 = next(s for s in out["sections"] if s["id"] == "s2")
        assert s1["verdict"] == "changes", s1
        assert [c["cid"] for c in s1["comments"]] == ["s1-c1", "s1-c2"], s1
        assert s1["comments"][0]["anchor"] == {"text": "retries 3x", "offset": 9}
        assert s2["verdict"] == "approved", s2
        assert not s2.get("comments"), s2
        print("OK")
    finally:
        proc.terminate(); proc.wait(timeout=5)


if __name__ == "__main__":
    main()
