#!/usr/bin/env python3
"""Integration test: a standalone qa-mode session's process actually exits
(and server.url is deleted) once the finish sequence calls /complete (#112).

Before this fix, `/viva-qa`'s documented finish steps read
`.viva/answers.json` and stopped — nothing ever called `POST /complete`, so
the server process (and its 2-second shutdown timer, which only starts
inside that handler) ran forever. This drives the fixed sequence directly
against `server.py`: launch `--mode qa`, submit answers, POST `/complete`
(mirroring `/viva-qa`'s corrected step 4), and assert the process
exits and `server.url` is removed — exactly as `--mode diff`'s and
`--mode review`'s finish paths already do today.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import ROOT, get, poll_for, post, wait_for_url  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    qa_input = {
        "mode": "qa",
        "context": "smoke test",
        "questions": [{"id": "q1", "text": "Pick one", "choices": ["a", "b"]}],
    }
    inp = viva / "qa-input.json"
    out = viva / "answers.json"
    inp.write_text(json.dumps(qa_input))

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--mode", "qa",
         "--input", str(inp), "--output", str(out), "--no-browser"],
        cwd=str(tmp),
    )
    try:
        base = wait_for_url(out)
        served = get(base, "/input")
        assert served.get("mode") == "qa", served

        post(base, "/submit", {
            "answers": [{"id": "q1", "choice": "a", "note": ""}],
            "submitted_early": False,
        })
        assert poll_for(out), "answers.json never written"

        # Mirrors /viva-qa's fixed step 4: /complete once
        # answers.json exists (standalone finish, no hand-off).
        post(base, "/complete", {"questions_total": 1, "questions_answered": 1})

        # Server shuts down ~2 seconds after /complete (same timer review-
        # and diff-mode already rely on).
        for _ in range(35):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        assert proc.poll() is not None, \
            "qa-mode server should exit after /complete — orphaned process (#112)"
        assert not (viva / "server.url").exists(), \
            "server.url must be removed once the qa-mode process shuts down"

        print("OK")
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
