from __future__ import annotations
#!/usr/bin/env python3
"""Integration tests for diff functionality in server.py.

Covers two distinct areas:
  1. Round-to-round section diff pass-through in --mode review.
  2. --mode diff: accepts DiffInput JSON and runs the full review SPA
     with diff-mode title block and highlight.js rendering.
"""
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ─── Fixtures ────────────────────────────────────────────────────────────────

DIFF_INPUT = {
    "mode": "diff",
    "doc_file": "HEAD~1..HEAD",
    "round": 1,
    "approved_ids": [],
    "sections": [
        {
            "id": "s1",
            "title": "foo.py hunk 1",
            "content": "```diff\n@@ -1,3 +1,4 @@\n line 1\n-old line\n+new line\n+extra\n line 3\n```",
        },
        {
            "id": "s2",
            "title": "bar.py hunk 1",
            "content": "```diff\n@@ -5,3 +5,3 @@\n context\n-removed\n+added\n context\n```",
        },
    ],
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def post(base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def get(base: str, path: str) -> dict:
    return json.loads(urllib.request.urlopen(base + path, timeout=5).read())


def _start_server(tmp: Path, inp: dict, mode: str = "diff") -> tuple:
    """Start server in the given mode, return (proc, base_url, output_path)."""
    viva = tmp / ".viva"
    viva.mkdir(exist_ok=True)
    inp_path = viva / "review-input-r1.json"
    out_path = viva / "review-r1.json"
    inp_path.write_text(json.dumps(inp))
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--mode", mode,
         "--input", str(inp_path), "--output", str(out_path), "--no-browser"],
        cwd=tmp,
    )
    url_file = viva / "server.url"
    for _ in range(50):
        if url_file.exists():
            break
        time.sleep(0.2)
    if not url_file.exists():
        proc.terminate()
        raise RuntimeError("server.url not created")
    return proc, url_file.read_text().strip(), str(out_path)


# ─── Legacy: round-to-round diff pass-through in --mode review ───────────────

def test_review_mode_diff_passthrough() -> None:
    """Section diff rows pass through /input and /next-round verbatim."""
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

        print("test_review_mode_diff_passthrough: OK")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ─── New: --mode diff ─────────────────────────────────────────────────────────

def test_diff_mode_input_endpoint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc, base, _ = _start_server(Path(tmp), DIFF_INPUT)
        try:
            data = get(base, "/input")
            assert data["mode"] == "diff", f"expected mode=diff, got {data.get('mode')}"
            assert len(data["sections"]) == 2
            assert data["sections"][0]["title"] == "foo.py hunk 1"
            assert data["sections"][1]["title"] == "bar.py hunk 1"
            print("test_diff_mode_input_endpoint: OK")
        finally:
            proc.terminate()
            proc.wait()


def test_diff_mode_submit_writes_output() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc, base, out_path = _start_server(Path(tmp), DIFF_INPUT)
        try:
            post(base, "/submit", {
                "round": 1,
                "sections": [
                    {"id": "s1", "verdict": "approved"},
                    {"id": "s2", "verdict": "changes", "note": "Use += instead"},
                ],
            })
            for _ in range(20):
                if Path(out_path).exists():
                    break
                time.sleep(0.1)
            assert Path(out_path).exists(), "output file not written"
            out = json.loads(Path(out_path).read_text())
            verdicts = {s["id"]: s["verdict"] for s in out["sections"]}
            assert verdicts["s1"] == "approved"
            assert verdicts["s2"] == "changes"
            print("test_diff_mode_submit_writes_output: OK")
        finally:
            proc.terminate()
            proc.wait()


def test_diff_mode_next_round() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc, base, _ = _start_server(Path(tmp), DIFF_INPUT)
        try:
            post(base, "/submit", {
                "round": 1,
                "sections": [
                    {"id": "s1", "verdict": "changes", "note": "fix this"},
                    {"id": "s2", "verdict": "approved"},
                ],
            })
            r2_input = dict(DIFF_INPUT, round=2, approved_ids=["s2"])
            r2_out = str(Path(tmp) / ".viva" / "review-r2.json")
            post(base, f"/next-round?output={r2_out}", r2_input)
            data = get(base, "/input")
            assert data["round"] == 2
            print("test_diff_mode_next_round: OK")
        finally:
            proc.terminate()
            proc.wait()


def test_diff_mode_complete_shuts_down() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc, base, _ = _start_server(Path(tmp), DIFF_INPUT)
        try:
            post(base, "/complete", {
                "rounds_total": 1, "sections_total": 2, "sections_revised": 1
            })
            # Server shuts down ~2 seconds after /complete
            for _ in range(35):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            assert proc.poll() is not None, "server should have shut down after /complete"
            print("test_diff_mode_complete_shuts_down: OK")
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait()


def test_diff_mode_complete_from_empty_diff_branch_shuts_down() -> None:
    """diff.md step 4's empty-diff branch (#116): the human requests a
    `changes` edit that fully resolves the diff before every hunk is
    individually approved, so the loop reaches `/complete` straight from
    step 4 instead of step 5 — never calling `/next-round` for that round.
    The server-side handling is call-site agnostic, so this asserts the same
    shutdown behavior `test_diff_mode_complete_shuts_down` covers for step 5,
    exercised via the new call site's actual path (submit a `changes` verdict,
    skip `/next-round`, go straight to `/complete`)."""
    with tempfile.TemporaryDirectory() as tmp:
        proc, base, _ = _start_server(Path(tmp), DIFF_INPUT)
        try:
            post(base, "/submit", {
                "round": 1,
                "sections": [
                    {"id": "s1", "verdict": "changes", "note": "revert this hunk"},
                    {"id": "s2", "verdict": "approved"},
                ],
            })
            # Step 4: re-diff comes back empty (the requested edit reverted
            # the only outstanding hunk) — go straight to /complete, no
            # /next-round in between.
            post(base, "/complete", {
                "rounds_total": 1, "sections_total": 2, "sections_revised": 1
            })
            for _ in range(35):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            assert proc.poll() is not None, \
                "server should have shut down after /complete from the empty-diff branch"
            print("test_diff_mode_complete_from_empty_diff_branch_shuts_down: OK")
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait()


# ─── Runner ───────────────────────────────────────────────────────────────────

def main() -> None:
    test_review_mode_diff_passthrough()
    test_diff_mode_input_endpoint()
    test_diff_mode_submit_writes_output()
    test_diff_mode_next_round()
    test_diff_mode_complete_shuts_down()
    test_diff_mode_complete_from_empty_diff_branch_shuts_down()
    print("\nAll server diff tests passed.")


if __name__ == "__main__":
    main()
