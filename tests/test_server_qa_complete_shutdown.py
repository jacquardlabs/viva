#!/usr/bin/env python3
"""Integration test: how a live server session ends — the finish guard that
decides whether it *may* end, and the three routes that actually end it.

`POST /complete`'s guard runs first, because a refused finish never reaches a
shutdown. "Nothing is auto-accepted" is a hard product line, and a guard that
lives only in `loop.py finish` is a norm the next caller can walk around; this
is the check the server performs on its own. Three sessions, two answers:

- **review** — gated. No verdicts submitted yet and not-all-approved are two
  different agent recoveries, so they get two distinct 4xx.
- **Q&A** — exempt by shape (`questions`, no `sections`).
- **diff** — exempt by mode. `parse_diff.py` emits `sections`, so a diff
  session is review-shaped; `viva-diff/SKILL.md:109-113`'s empty-re-diff finish
  signs off with `changes` verdicts on record by design.

Then the shutdown routes — three routes, three scenarios:

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

    Four calls against one server, in the order a real session hits them, so the
    snapshot the guard reads is proved to track the live round rather than being
    written once at the first submit.
    """
    proc, viva, out = _launch("review", REVIEW_INPUT)
    try:
        base = wait_for_url(out)

        # (a) No round has been submitted at all. Its own 4xx, distinct from
        # (b): the recovery is "present the round", not "re-present it".
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

        # (c) Round 2 opens with no verdicts of its own. The snapshot belongs to
        # the round that produced it — carrying round 1's forward would let an
        # all-approved earlier round sign off a later one nobody has seen.
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


def check_diff_complete_ungated() -> None:
    """A diff session finishes with `changes` verdicts on record.

    `viva-diff/SKILL.md:109-113`: the re-diff reaches zero because a hunk was
    reverted or dropped at the reviewer's request, *not* because every hunk was
    approved — so the latest verdicts hold `changes` by design. `parse_diff.py`
    emits `sections`, so a shape-only guard would 4xx that legitimate finish,
    leak the server, and strand the tab on the processing card. The exemption is
    `mode`, which is why this asserts a 200 with the non-approved verdicts
    actually on record rather than an empty submit.
    """
    proc, viva, out = _launch("diff", DIFF_INPUT)
    try:
        base = wait_for_url(out)
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
        assert (status, body) == (200, {"ok": True}), \
            "diff mode is exempt from the finish guard — got %d %r" % (status, body)
        assert _await_exit(proc), "diff server should exit after /complete"
        assert not (viva / "server.url").exists()
    finally:
        _cleanup(proc)


def _sse_events(base: str, seen: list) -> threading.Thread:
    """Subscribe to /events and record every event name that arrives.

    This is what makes "abandon carries no sign-off meaning" checkable. The
    assertion it replaces — `not out.exists()` — could not fail under any
    implementation, because `/complete` never writes that file either (`/submit`
    does, and neither abandon scenario posts one). Adding `_push_sse("complete")`
    to the /abandon branch left both tests green; it does not now.
    """
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

        # Mirrors /viva-qa's fixed step 4: /complete once
        # answers.json exists (standalone finish, no hand-off).
        # Also the finish guard's shape exemption: this input carries
        # `questions` and no `sections`, and `round_is_complete` returns False
        # for an empty section list — so a 200 here is proof it was never
        # consulted, not proof it was satisfied.
        status, body = post_result(
            base, "/complete", {"questions_total": 1, "questions_answered": 1})
        assert (status, body) == (200, {"ok": True}), \
            "a Q&A session must never meet the review finish guard — got %d %r" \
            % (status, body)

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
        events = []
        _sse_events(base, events)
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
        assert "complete" not in events, \
            "/abandon must carry none of /complete's sign-off meaning — the SSE "\
            "stream saw a `complete` event: %r" % (events,)
    finally:
        _cleanup(proc)


def check_loop_abandon_shutdown() -> None:
    """`loop.py abandon` ends a live session and reports it unfinished.

    The driver's own route to `/abandon`, asserted independently of the two
    signal paths: nothing here sends a signal, and `returncode == 0` proves the
    shutdown `finally` ran normally rather than a `-15`/`-2` killing the
    process out from under it.
    """
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
    check_review_complete_gate()
    check_diff_complete_ungated()
    check_complete_shutdown()
    check_abandon_shutdown()
    check_loop_abandon_shutdown()
    check_sigterm_shutdown()
    print("OK")


if __name__ == "__main__":
    main()
