#!/usr/bin/env python3
"""Integration test: QA mode serves questions and writes a real answers.json.

QA mode is the brainstorming integration's only runtime, yet every other
integration test launches `--mode review`. This exercises the full QA round
trip — `/input` serves the question payload, `/submit` accepts an `answers`
payload, and the server writes `answers.json` with the early-exit flag and
`qa-`-prefixed attachments.
"""
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
    qa_input = {
        "mode": "qa",
        "title": "Brainstorm: notification design",
        "questions": [
            {"id": "q1", "text": "Channel?", "hint": "pick one",
             "choices": ["email", "sms", "push"]},
            {"id": "q2", "text": "Latency target?",
             "choices": ["1s", "30s", "5m"]},
        ],
    }
    inp = viva / "qa-input.json"
    out = viva / "answers.json"
    inp.write_text(json.dumps(qa_input))
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--mode", "qa",
         "--input", str(inp), "--output", str(out), "--no-browser"],
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

        # /input serves the question payload verbatim
        served = get(base, "/input")
        assert served.get("mode") == "qa", served
        assert [q["id"] for q in served["questions"]] == ["q1", "q2"], served
        assert served["questions"][0]["choices"] == ["email", "sms", "push"], served

        # Submit one answered question (with an image) and one skipped, early
        post(base, "/submit", {
            "answers": [
                {"id": "q1", "choice": "email", "note": "team uses it",
                 "images": [{"data": PNG_B64, "mime": "image/png"}]},
            ],
            "submitted_early": True,
        })

        for _ in range(50):
            if out.exists():
                break
            time.sleep(0.2)
        assert out.exists(), "answers.json never written"
        result = json.loads(out.read_text())

        # Early-exit flag uses the standardized name (matches review mode)
        assert result.get("submitted_early") is True, result
        assert "skipped" not in result, "legacy `skipped` key must be gone"

        ans = result["answers"]
        assert len(ans) == 1, ans
        assert ans[0]["id"] == "q1", ans
        assert ans[0]["choice"] == "email", ans
        assert ans[0]["note"] == "team uses it", ans

        # Attachment is written with a `qa-` prefix, never `r0-`
        atts = ans[0]["attachments"]
        assert atts == [str(viva / "attachments" / "qa-q1-0.png")], atts
        assert Path(atts[0]).read_bytes() == PNG, "attachment bytes must match"
        assert "images" not in ans[0], "inline images must be stripped"

        print("OK")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
