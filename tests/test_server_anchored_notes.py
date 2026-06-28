#!/usr/bin/env python3
"""Integration test: a line-anchored note (issue #15).

A reviewer can select text in a rendered section and attach a note to that
specific line. The selected text travels as an `anchor` string on the verdict,
straight through /submit into the output JSON, so the agent can grep the source
for it and target the rewrite. The contract:

  - /submit is a pure pipe for `anchor` — it never strips or injects it.
  - A verdict with NO anchor is byte-identical to today (no `anchor` key).
  - The page ships the selection-capture + anchor UI so a browser can produce one.
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


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {
        "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "## Goals\nsub-second latency\n"},
            {"id": "s2", "title": "Scope", "content": "## Scope\nbody\n"},
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
        assert url_file.exists(), "server failed to start within 10s"
        base = url_file.read_text().strip()

        # An anchored changes note and a bare info note in the same submit.
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "note": "this claim is unsupported",
             "anchor": "sub-second latency"},
            {"id": "s2", "verdict": "info", "note": "what about errors?"},
        ]})

        out = json.loads((viva / "out1.json").read_text())
        s1 = next(s for s in out["sections"] if s["id"] == "s1")
        s2 = next(s for s in out["sections"] if s["id"] == "s2")

        # The anchor survives the pipe verbatim.
        assert s1.get("anchor") == "sub-second latency", f"anchor dropped: {s1}"
        # Zero-regression: an un-anchored note carries no `anchor` key at all.
        assert "anchor" not in s2, f"s2 must stay anchor-free: {s2}"

        # The page ships the selection capture + anchor controls so a browser
        # can produce the anchor in the first place.
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        for needle in ("_lastSelection", "anchorSelection", "anchor-chip",
                       "selectionchange"):
            assert needle in page, f"page missing: {needle}"

        print("OK")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
