#!/usr/bin/env python3
"""Integration test: every route out of a live server actually ends the process
and removes `.viva/server.url`. Three routes, three scenarios:

- `POST /complete` — the standalone qa-mode finish sequence (#112). Before that
  fix, `/viva-qa`'s documented finish steps read `.viva/answers.json` and
  stopped; nothing ever called `POST /complete`, so the server process (and its
  2-second shutdown timer, which only starts inside that handler) ran forever.
- `POST /abandon` — the loop driver's abandon route. `loop.py abandon` is a
  different process holding no child handle (the server is launched detached)
  and `server.url` carries a URL and nothing else, so abandon reaches the server
  over HTTP. It sets `_shutdown` directly, carrying none of `/complete`'s
  sign-off meaning and none of its 2-second grace.
- `SIGTERM` — the headless-parent teardown (#125). `proc.terminate()` is the
  standard way a parent ends a subprocess it owns; unhandled, it exits `-15` and
  skips the `finally` that unlinks `server.url`, leaking the file into the next
  launch's guard.

Each scenario gets its own tmpdir: sharing one lets `wait_for_url` read a stale
`server.url` from an already-dead port and pass for the wrong reason.
"""
import json
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import ROOT, get, poll_for, post, wait_for_url  # noqa: E402

QA_INPUT = {
    "mode": "qa",
    "context": "smoke test",
    "questions": [{"id": "q1", "text": "Pick one", "choices": ["a", "b"]}],
}
REVIEW_INPUT = {
    "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
    "sections": [{"id": "s1", "title": "Goals", "content": "body"}],
}
_NAMES = {"qa": ("qa-input.json", "answers.json"),
          "review": ("review-input-r1.json", "review-r1.json")}


def _launch(mode: str, payload: dict):
    """Launch a server on a fresh tmpdir; return (proc, viva_dir, output_path)."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    in_name, out_name = _NAMES[mode]
    inp, out = viva / in_name, viva / out_name
    inp.write_text(json.dumps(payload))
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--mode", mode,
         "--input", str(inp), "--output", str(out), "--no-browser"],
        cwd=str(tmp),
    )
    return proc, viva, out


def _await_exit(proc, tries: int = 50, delay: float = 0.1) -> bool:
    """Poll for process exit; return whether it happened inside the window."""
    for _ in range(tries):
        if proc.poll() is not None:
            return True
        time.sleep(delay)
    return False


def _cleanup(proc) -> None:
    """Teardown that can't be confused with the mechanism under test — SIGKILL,
    never SIGTERM, so a failed scenario still reports an honest signal."""
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


def check_complete_shutdown() -> None:
    """A standalone qa session's finish sequence exits the process (#112)."""
    proc, viva, out = _launch("qa", QA_INPUT)
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
        assert _await_exit(proc), \
            "qa-mode server should exit after /complete — orphaned process (#112)"
        assert not (viva / "server.url").exists(), \
            "server.url must be removed once the qa-mode process shuts down"
    finally:
        _cleanup(proc)


def check_abandon_shutdown() -> None:
    """`POST /abandon` ends a live review session, carrying no sign-off."""
    proc, viva, out = _launch("review", REVIEW_INPUT)
    try:
        base = wait_for_url(out)
        t0 = time.monotonic()
        assert post(base, "/abandon", {}) == {"ok": True}, \
            "/abandon must ack before it shuts the server down"
        assert _await_exit(proc), \
            "review server should exit on /abandon — abandon has no other reach"
        # Discriminates "no 2-second grace" from a copy of /complete's timer:
        # observed exit is ~0.5-0.7s (the accept loop's 0.5s wake plus teardown),
        # a timer path could not land under 2.0s.
        assert time.monotonic() - t0 < 1.8, \
            "/abandon must set _shutdown directly — no /complete-style 2s timer"
        assert not (viva / "server.url").exists(), \
            "server.url must be removed once the abandoned process shuts down"
        assert not out.exists(), \
            "/abandon carries none of /complete's sign-off meaning — no verdicts"
    finally:
        _cleanup(proc)


def check_sigterm_shutdown() -> None:
    """SIGTERM runs the same shutdown `finally` SIGINT does (#125)."""
    proc, viva, out = _launch("review", REVIEW_INPUT)
    try:
        wait_for_url(out)
        proc.send_signal(signal.SIGTERM)  # what proc.terminate() sends on POSIX
        assert _await_exit(proc), "SIGTERM must end the server process"
        # The discriminating assertion: an *unhandled* SIGTERM also ends the
        # process, but at -15 and without running the `finally`. Exit 0 means
        # the handler set _shutdown and the accept loop wound down normally.
        assert proc.returncode == 0, \
            "SIGTERM must be handled, not fatal — got returncode %r (#125)" \
            % (proc.returncode,)
        assert not (viva / "server.url").exists(), \
            "the shutdown `finally` must unlink server.url on the SIGTERM path"
    finally:
        _cleanup(proc)


def main() -> None:
    check_complete_shutdown()
    check_abandon_shutdown()
    check_sigterm_shutdown()
    print("OK")


if __name__ == "__main__":
    main()
