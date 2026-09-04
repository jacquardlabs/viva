#!/usr/bin/env python3
"""Integration test: how a live server session ends — the finish guard that
decides whether it may end, and the three routes (`/complete`, `/abandon`,
SIGTERM) that actually end it. Review and diff are gated on approvals;
Q&A is exempt by shape; diff has one escape hatch, `resolved: "empty"` (#177).

Each scenario gets its own tmpdir, so `wait_for_url` never reads a stale
`server.url` from an already-dead port.
"""
import json
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.request
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import (  # noqa: E402
    ROOT, get, poll_for, post, post_result, wait_for_url,
)

QA_INPUT = {
    "mode": "qa",
    "context": "smoke test",
    "questions": [{"id": "q1", "text": "Pick one", "choices": ["a", "b"]}],
}
# Two sections, so the guard's refusal has a count to name ("1 of 2").
REVIEW_INPUT = {
    "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
    "sections": [{"id": "s1", "title": "Goals", "content": "body"},
                 {"id": "s2", "title": "Non-goals", "content": "body"}],
}
# What `parse_diff.py` emits: review-shaped `sections`, `mode: "diff"`.
DIFF_INPUT = {
    "mode": "diff", "doc_file": "HEAD", "round": 1, "approved_ids": [],
    "sections": [{"id": "s1", "title": "a.py", "content": "@@ -1 +1 @@\n-x\n+y"}],
}
_NAMES = {"qa": ("qa-input.json", "answers.json"),
          "review": ("review-input-r1.json", "review-r1.json"),
          "diff": ("review-input-r1.json", "review-r1.json")}


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


def check_review_complete_gate() -> None:
    """A review session may not sign off on sections the human never approved.
    Four calls against one server, in session order, to prove the guard tracks
    the live round rather than a snapshot taken at first submit."""
    proc, viva, out = _launch("review", REVIEW_INPUT)
    try:
        base = wait_for_url(out)

        # (a) No round submitted yet — distinct 4xx from (b).
        status, body = post_result(base, "/complete", {})
        assert status == 400, \
            "/complete before any /submit must be refused — got %d" % status
        assert "no verdicts" in body.get("error", ""), body

        # (b) Submitted, but one section carries `changes`.
        post(base, "/submit", {"round": 1, "sections": [
            {"id": "s1", "verdict": "approved"},
            {"id": "s2", "verdict": "changes", "comments": [{"note": "fix"}]},
        ]})
        assert poll_for(out), "review-r1.json never written"
        status, body = post_result(base, "/complete", {
            "rounds_total": 1, "sections_total": 2, "sections_revised": 1})
        assert status == 409, \
            "a round with a non-approved section must be refused — got %d" % status
        assert "1 of 2" in body.get("error", ""), \
            "the refusal must name how many sections are not approved: %r" % (body,)

        # (c) Round 2 opens with no verdicts of its own — round 1's must not carry forward.
        out2 = viva / "review-r2.json"
        post(base, "/next-round",
             dict(REVIEW_INPUT, round=2, output=str(out2)))
        status, body = post_result(base, "/complete", {})
        assert status == 400, \
            "a fresh round with no verdicts must be refused — got %d" % status
        assert "no verdicts" in body.get("error", ""), body

        # (d) Every section approved — the one state that signs off.
        post(base, "/submit", {"round": 2, "sections": [
            {"id": "s1", "verdict": "approved"},
            {"id": "s2", "verdict": "approved"},
        ]})
        status, body = post_result(base, "/complete", {
            "rounds_total": 2, "sections_total": 2, "sections_revised": 1})
        assert (status, body) == (200, {"ok": True}), \
            "an all-approved round must still complete — got %d %r" % (status, body)
        assert _await_exit(proc), "an accepted /complete still shuts the server down"
        assert not (viva / "server.url").exists()
    finally:
        _cleanup(proc)


def check_diff_complete_is_gated_unless_resolved_empty() -> None:
    """#177: a diff session with a `changes` verdict is refused a plain finish
    like review, and signs off only when the caller asserts `resolved: "empty"`."""
    proc, viva, out = _launch("diff", DIFF_INPUT)
    try:
        base = wait_for_url(out)
        status, body = post_result(base, "/complete", {"resolved": "empty"})
        assert status == 400 and "no verdicts" in body.get("error", ""), \
            "a diff can be resolved empty only after the human has seen it: %r" % (body,)

        post(base, "/submit", {"round": 1, "sections": [
            {"id": "s1", "verdict": "changes",
             "comments": [{"note": "revert this hunk"}]},
        ]})
        assert poll_for(out), "review-r1.json never written"
        recorded = json.loads(out.read_text())
        assert [s["verdict"] for s in recorded["sections"]] == ["changes"], \
            "precondition: the server must hold a non-approved verdict %r" % (recorded,)

        status, body = post_result(base, "/complete", {
            "rounds_total": 1, "sections_total": 1, "sections_revised": 1})
        assert status == 409, \
            "a diff finish with changes on record must be refused — got %d %r" % (status, body)
        assert "1 of 1" in body.get("error", ""), body
        assert proc.poll() is None, "a refused finish leaves the server up"

        status, body = post_result(base, "/complete", {
            "resolved": "partial", "rounds_total": 1})
        assert status == 400 and "resolved" in body.get("error", ""), \
            "an unknown resolved value is refused, not ignored: %r" % (body,)

        status, body = post_result(base, "/complete", {
            "resolved": "empty", "rounds_total": 1, "sections_total": 1,
            "sections_revised": 1})
        assert (status, body) == (200, {"ok": True}), \
            "resolved-empty signs off a diff with changes on record — got %d %r" % (status, body)
        assert _await_exit(proc), "diff server should exit after /complete"
        assert not (viva / "server.url").exists()
    finally:
        _cleanup(proc)


def check_review_complete_refuses_a_resolved_signal() -> None:
    """`resolved` on a review server is a caller bug — refused, not ignored."""
    proc, viva, out = _launch("review", REVIEW_INPUT)
    try:
        base = wait_for_url(out)
        post(base, "/submit", {"round": 1, "sections": [
            {"id": "s1", "verdict": "approved"},
            {"id": "s2", "verdict": "approved"},
        ]})
        assert poll_for(out)
        status, body = post_result(base, "/complete", {"resolved": "empty"})
        assert status == 400 and "diff-review signal" in body.get("error", ""), \
            "resolved on a review server must be refused: %d %r" % (status, body)
        assert proc.poll() is None
        status, body = post_result(base, "/complete", {})
        assert (status, body) == (200, {"ok": True}), (status, body)
        assert _await_exit(proc)
    finally:
        _cleanup(proc)


def _sse_events(base: str, seen: list) -> threading.Thread:
    """Subscribe to /events and record every event name that arrives — makes
    "abandon carries no sign-off meaning" actually checkable."""
    def run():
        try:
            with urllib.request.urlopen(base + "/events", timeout=10) as r:
                for raw in r:
                    line = raw.decode(errors="replace").strip()
                    if line.startswith("event:"):
                        seen.append(line.split(":", 1)[1].strip())
        except Exception:
            pass  # the stream ends when the server shuts down; that is the point
    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(0.4)  # let the subscription land before the route under test
    return th


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

        # Shape exemption: `questions`/no `sections` means round_is_complete
        # is never consulted, so a 200 here proves that, not that it passed.
        status, body = post_result(
            base, "/complete", {"questions_total": 1, "questions_answered": 1})
        assert (status, body) == (200, {"ok": True}), \
            "a Q&A session must never meet the review finish guard — got %d %r" \
            % (status, body)

        # Server shuts down ~2 seconds after /complete.
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
        events = []
        _sse_events(base, events)
        t0 = time.monotonic()
        assert post(base, "/abandon", {}) == {"ok": True}, \
            "/abandon must ack before it shuts the server down"
        assert _await_exit(proc), \
            "review server should exit on /abandon — abandon has no other reach"
        # Observed exit ~0.5-0.7s; a copy of /complete's timer couldn't land under 2.0s.
        assert time.monotonic() - t0 < 1.8, \
            "/abandon must set _shutdown directly — no /complete-style 2s timer"
        assert not (viva / "server.url").exists(), \
            "server.url must be removed once the abandoned process shuts down"
        assert "complete" not in events, \
            "/abandon must carry none of /complete's sign-off meaning — the SSE "\
            "stream saw a `complete` event: %r" % (events,)
    finally:
        _cleanup(proc)


def check_loop_abandon_shutdown() -> None:
    """`loop.py abandon` ends a live session and reports it unfinished; no
    signal is sent, so `returncode == 0` proves the shutdown `finally` ran normally."""
    proc, viva, out = _launch("review", REVIEW_INPUT)
    try:
        base = wait_for_url(out)
        events = []
        _sse_events(base, events)
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "loop.py"),
             "--viva-dir", str(viva), "abandon"],
            capture_output=True, text=True, cwd=str(viva.parent))
        assert r.returncode == 0, "loop abandon failed:\n%s" % r.stderr
        assert "not signed off" in r.stdout.lower(), \
            "abandon must report the session unfinished — got %r" % (r.stdout,)
        assert _await_exit(proc), "the server must exit on `loop.py abandon`"
        assert proc.returncode == 0, \
            "abandon ends the server over HTTP, not by signal — got returncode %r" \
            % (proc.returncode,)
        assert not (viva / "server.url").exists(), \
            "`loop.py abandon` must leave no server.url for the next start's guard"
        assert "complete" not in events, \
            "`loop.py abandon` is not a sign-off — the SSE stream saw a "\
            "`complete` event: %r" % (events,)
    finally:
        _cleanup(proc)


def check_sigterm_shutdown() -> None:
    """SIGTERM runs the same shutdown `finally` SIGINT does (#125)."""
    proc, viva, out = _launch("review", REVIEW_INPUT)
    try:
        wait_for_url(out)
        proc.send_signal(signal.SIGTERM)  # what proc.terminate() sends on POSIX
        assert _await_exit(proc), "SIGTERM must end the server process"
        # An unhandled SIGTERM exits at -15 without running the `finally`.
        assert proc.returncode == 0, \
            "SIGTERM must be handled, not fatal — got returncode %r (#125)" \
            % (proc.returncode,)
        assert not (viva / "server.url").exists(), \
            "the shutdown `finally` must unlink server.url on the SIGTERM path"
    finally:
        _cleanup(proc)


def main() -> None:
    check_review_complete_gate()
    check_diff_complete_is_gated_unless_resolved_empty()
    check_review_complete_refuses_a_resolved_signal()
    check_complete_shutdown()
    check_abandon_shutdown()
    check_loop_abandon_shutdown()
    check_sigterm_shutdown()
    print("OK")


if __name__ == "__main__":
    main()
