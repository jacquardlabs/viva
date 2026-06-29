#!/usr/bin/env python3
"""Integration test: multi-comment round-trip (comments[] model).

Each section carries a `comments[]` list of {cid, type, note, anchor?, open,
settled}. The section verdict is DERIVED — never stored as a bare scalar.
The page ships renderHighlights / openCommentPopover / deriveVerdict and does
NOT ship the retired single-anchor controls (anchor-chip, rpin-, anchorSelection).

Contract verified here:
  - /submit passes comments[] through into out.json unchanged.
  - Anchored comments carry anchor.offset; un-anchored ones have no `anchor` key.
  - Derived verdict on the section matches the dominant comment type.
  - Retired page needles are absent; new interaction needles are present.
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

        # Submit s1 with two comments (changes + info, first anchored) and
        # s2 with one un-anchored info comment.
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "comments": [
                {"cid": "s1-c1", "type": "changes", "note": "unsupported",
                 "anchor": {"text": "sub-second latency", "offset": 9}, "open": True, "settled": False},
                {"cid": "s1-c2", "type": "info", "note": "errors?", "open": True, "settled": False}]},
            {"id": "s2", "verdict": "info", "comments": [
                {"cid": "s2-c1", "type": "info", "note": "whole-section question", "open": True, "settled": False}]}]})

        out = json.loads((viva / "out1.json").read_text())
        s1 = next(s for s in out["sections"] if s["id"] == "s1")
        s2 = next(s for s in out["sections"] if s["id"] == "s2")

        # Derived verdict from dominant comment type; comments array preserved.
        assert s1["verdict"] == "changes" and len(s1["comments"]) == 2, s1
        # Anchored comment carries offset through the pipe.
        assert s1["comments"][0]["anchor"]["offset"] == 9, s1
        # Un-anchored comment has no `anchor` key at all.
        assert "anchor" not in s2["comments"][0], s2

        # Page ships the interaction-model needles and NOT the retired ones.
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        for needle in ("renderHighlights", "openCommentPopover", "deriveVerdict"):
            assert needle in page, f"page missing: {needle}"
        for gone in ("anchor-chip", "rpin-", "anchorSelection"):
            assert gone not in page, f"retired control still present: {gone}"

        print("OK")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
