#!/usr/bin/env python3
"""Integration test: the server carries per-section annotations through to the
client unchanged, and the page ships the annotation-strip renderer + styles.

Annotations are advisory badges a pre-review pass writes into review-input.
The server is a dumb pipe for them (load_input is verbatim), so the contract is:
GET /input and the /next-round push must both preserve the annotations array,
and the page must define the renderer that turns them into colored badges.
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
    annots = [
        {"kind": "grounding", "severity": "warn", "message": "claim unsupported"},
        {"kind": "drift", "severity": "error", "message": "code says 30s, doc says 60s"},
    ]
    r1 = {
        "mode": "review",
        "doc_file": "doc.md",
        "round": 1,
        "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "goals body", "annotations": annots},
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

        # Pass-through: GET /input preserves the annotations array verbatim.
        data = get(base, "/input")
        s1 = next(s for s in data["sections"] if s["id"] == "s1")
        s2 = next(s for s in data["sections"] if s["id"] == "s2")
        assert s1.get("annotations") == annots, f"annotations dropped: {s1}"
        assert "annotations" not in s2, f"s2 must stay bare: {s2}"

        # Pass-through across a round push: /next-round body is reflected in /input.
        r2 = dict(r1, round=2)
        post(base, "/next-round?output=" + str(viva / "out2.json"), r2)
        data = get(base, "/input")
        s1 = next(s for s in data["sections"] if s["id"] == "s1")
        assert s1.get("annotations") == annots, f"annotations lost across round: {s1}"

        # Page ships the renderer, the strip markup hook, and the three
        # severity styles mapped onto the existing verdict color slots.
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        for needle in ("function annotStripHTML", "annot-strip",
                       ".annot-info", ".annot-warn", ".annot-error",
                       "section.annotations"):
            assert needle in page, f"page missing: {needle}"

        print("OK")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
