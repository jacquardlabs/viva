#!/usr/bin/env python3
"""`server.py --mode session` (#241) and round-checked `/submit` (#199).

Pins #239's test-strategy row 3: a session server accepts review → diff only
after the spec gate closes, refuses diff before it and review after it, and
keeps serving after a spec `/complete`. Also: the serve-time `session` key
exists only on a session server, and `/submit` refuses a round it isn't serving.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import (  # noqa: E402
    get, launch_server, poll_for, post, post_result, shipped_source)

SID = "ab" * 16
QA = {"mode": "qa", "context": "Intake", "questions": [{"id": "q1", "text": "Scope?"}]}
SPEC = {"mode": "review", "doc_file": "spec.md", "round": 1,
        "sections": [{"id": "s1", "title": "Problem", "content": "P."},
                     {"id": "s2", "title": "Design", "content": "D."}]}
DIFF = {"mode": "diff", "doc_file": "PR #7", "round": 1,
        "sections": [{"id": "s1", "title": "f.py hunk 1",
                      "content": "```diff\n@@ -1 +1 @@\n-a\n+b\n```"}]}


def gates(base: str) -> dict:
    return {g["kind"]: g["state"] for g in get(base, "/input")["session"]["gates"]}


def approve(base: str, rnd: int, mode: str, ids=("s1", "s2")) -> None:
    post(base, "/submit", {"round": rnd, "mode": mode, "submitted_early": False,
                           "sections": [{"id": i, "verdict": "approved"} for i in ids]})


def next_round(base: str, viva: Path, data: dict, name: str) -> tuple:
    return post_result(base, "/next-round", dict(data, output=str(viva / name)))


def session_server(viva: Path, data: dict, out: str = "out.json"):
    (viva / "in.json").write_text(json.dumps(data))
    return launch_server(viva / "in.json", viva / out, mode="session", cwd=viva,
                         extra=("--session-id", SID))


def test_the_gate_table() -> None:
    with tempfile.TemporaryDirectory() as td:
        viva = Path(td) / ".viva"
        viva.mkdir()
        with session_server(viva, QA, "answers.json") as base:
            served = get(base, "/input")
            assert served["session"]["id"] == SID, served
            assert gates(base) == {"intake": "live", "spec": "waiting", "diff": "waiting"}

            # Intake: a diff is refused, naming the gate; /complete has nothing to sign.
            st, body = next_round(base, viva, DIFF, "review-r1.json")
            assert st == 400 and "intake gate (live)" in body["error"], body
            st, body = post_result(base, "/complete", {})
            assert st == 400 and "intake gate" in body["error"], body
            post(base, "/submit", {"answers": [{"id": "q1", "choice": "", "note": "x"}],
                                   "submitted_early": False})
            assert poll_for(viva / "answers.json")

            # Hand-off: the spec gate goes live.
            assert next_round(base, viva, SPEC, "review-r1.json")[0] == 200
            assert gates(base) == {"intake": "done", "spec": "live", "diff": "waiting"}
            st, body = next_round(base, viva, DIFF, "review-r1.json")
            assert st == 400 and "spec gate (live)" in body["error"], body

            # Spec round 1 asks for changes; round 2 signs it off.
            post(base, "/submit", {"round": 1, "mode": "review", "submitted_early": False,
                                   "sections": [{"id": "s1", "verdict": "changes", "note": "n"},
                                                {"id": "s2", "verdict": "approved"}]})
            assert next_round(base, viva, dict(SPEC, round=2), "review-r2.json")[0] == 200
            assert len(get(base, "/input")["ledger"]) == 1
            approve(base, 2, "review")
            assert post_result(base, "/complete", {"rounds_total": 2})[0] == 200
            time.sleep(2.5)  # past a standalone server's shutdown timer
            assert (viva / "server.url").exists(), "a spec /complete keeps serving"
            assert gates(base) == {"intake": "done", "spec": "done", "diff": "waiting"}

            # Between gates: no round is open, and review is refused.
            st, body = post_result(base, "/complete", {})
            assert st == 400 and "spec gate (done)" in body["error"], body
            st, body = post_result(base, "/submit", {
                "round": 2, "mode": "review", "sections": [{"id": "s1", "verdict": "approved"}]})
            assert st == 409 and body["current"]["round"] == 2, body
            assert "closed" in body["error"], body
            st, body = next_round(base, viva, dict(SPEC, round=3), "review-r3.json")
            assert st == 400 and "spec gate (done)" in body["error"] \
                and "'diff' rounds" in body["error"], body

            # The diff gate opens on the diff round #242's join will POST, into
            # the same `.viva/` after its clear (keeping server.url, as --handoff does).
            for p in viva.glob("review-r*.json"):
                p.unlink()
            assert (viva / "server.url").exists()
            assert next_round(base, viva, DIFF, "review-r1.json")[0] == 200
            assert gates(base) == {"intake": "done", "spec": "done", "diff": "live"}
            served = get(base, "/input")
            assert served["mode"] == "diff" and served["ledger"] == [], \
                "a gate is a fresh epoch: the spec's ledger rows stay in its doc"
            st, _ = next_round(base, viva, dict(SPEC, round=2), "review-r2.json")
            assert st == 400, "review after the spec gate closed"

            # Spec round 1 and diff round 1 collide on number; mode tells them apart.
            st, body = post_result(base, "/submit", {
                "round": 1, "mode": "review", "sections": [{"id": "s1", "verdict": "approved"}]})
            assert st == 409 and body["current"]["mode"] == "diff", body
            st, body = post_result(base, "/submit", {
                "round": 1, "sections": [{"id": "s1", "verdict": "approved"}]})
            assert st == 409 and "mode" in body["error"], body
            assert not (viva / "review-r1.json").exists(), "a refused submit writes nothing"
            approve(base, 1, "diff", ids=("s1",))
            assert poll_for(viva / "review-r1.json")

            # The diff gate's sign-off shuts down, as a standalone diff server does.
            assert post_result(base, "/complete", {})[0] == 200
            for _ in range(50):
                if not (viva / "server.url").exists():
                    break
                time.sleep(0.1)
            assert not (viva / "server.url").exists(), "a diff /complete shuts down"
    print("  ok  test_the_gate_table")


def test_a_diff_boot_is_a_relaunched_diff_gate() -> None:
    """#242's relaunch: the session's server died between gates."""
    with tempfile.TemporaryDirectory() as td:
        viva = Path(td) / ".viva"
        viva.mkdir()
        with session_server(viva, DIFF) as base:
            assert gates(base) == {"intake": "done", "spec": "done", "diff": "live"}
            approve(base, 1, "diff", ids=())
            st, body = post_result(base, "/complete", {"resolved": "empty"})
            assert st == 200, ("a session's diff gate honors resolved: empty", body)
    print("  ok  test_a_diff_boot_is_a_relaunched_diff_gate")


def _boot(viva: Path, data: dict, *extra) -> subprocess.CompletedProcess:
    (viva / "in.json").write_text(json.dumps(data))
    return subprocess.run(
        [sys.executable, str(SERVER), "--input", str(viva / "in.json"),
         "--output", str(viva / "out.json"), "--no-browser", *extra],
        capture_output=True, text=True, timeout=10)


def test_session_boot_refusals() -> None:
    with tempfile.TemporaryDirectory() as td:
        viva = Path(td)
        r = _boot(viva, SPEC, "--mode", "session", "--session-id", SID)
        assert r.returncode == 1 and "viva: invalid review-input" in r.stderr \
            and "'qa' or 'diff'" in r.stderr, r.stderr
        r = _boot(viva, dict(QA, questions="nope"), "--mode", "session", "--session-id", SID)
        assert r.returncode == 1 and "viva: invalid qa-input" in r.stderr, r.stderr
        r = _boot(viva, QA, "--mode", "session")
        assert r.returncode == 2 and "--session-id" in r.stderr, r.stderr
        r = _boot(viva, QA, "--mode", "qa", "--session-id", SID)
        assert r.returncode == 2 and "--session-id" in r.stderr, r.stderr
        assert not (viva / "server.url").exists()
    print("  ok  test_session_boot_refusals")


def test_standalone_servers_carry_no_session_and_check_the_round() -> None:
    with tempfile.TemporaryDirectory() as td:
        viva = Path(td)
        for mode, data in (("review", SPEC), ("qa", QA), ("diff", DIFF)):
            (viva / "in.json").write_text(json.dumps(data))
            with launch_server(viva / "in.json", viva / "out.json", mode=mode, cwd=viva) as base:
                assert "session" not in get(base, "/input"), mode
                if mode == "qa":
                    st, body = post_result(base, "/submit", {
                        "round": 1, "sections": [{"id": "s1", "verdict": "approved"}]})
                    assert st == 409 and body["current"] == {"mode": "qa"}, body
                    continue
                st, body = post_result(base, "/submit", {
                    "round": 2, "sections": [{"id": "s1", "verdict": "approved"}]})
                assert st == 409 and body["current"] == {"mode": mode, "round": 1}, body
                st, body = post_result(base, "/submit", {"answers": []})
                assert st == 409, body
                assert not (viva / "out.json").exists(), "a stale submit writes nothing"
                # Absent `mode` still reads as the served round's on a standalone server.
                st, _ = post_result(base, "/submit", {
                    "round": 1, "sections": [{"id": "s1", "verdict": "approved"}]})
                assert st == 200
                (viva / "out.json").unlink()
    print("  ok  test_standalone_servers_carry_no_session_and_check_the_round")


def test_the_tab_restamps_per_round_and_surfaces_a_stale_submit() -> None:
    """No JS runner (CLAUDE.md), so source needles on the shipped page."""
    page = shipped_source()
    rnd = page[page.index("es.addEventListener('round'"):page.index("es.addEventListener('complete'")]
    assert "document.body.classList.toggle('mode-diff', modeWord === 'diff');" in rnd
    assert "if (modeWord === 'diff') loadDiff2html();" in rnd
    assert rnd.index("!Array.isArray(data.sections)") < rnd.index("classList.toggle('mode-diff'"), \
        "the sections[] guard still runs before anything is re-stamped"
    assert "el('complete-view').style.display   = 'none';" in rnd, \
        "a diff gate's round must take down the spec gate's stamp"
    done = page[page.index("es.addEventListener('complete'"):page.index("es.onerror = ")]
    assert "if (!gateAhead) es.close();" in done, \
        "a spec sign-off keeps the stream open for the diff gate"
    sub = page[page.index("function submitReview("):page.index("function submitQA(")]
    assert "mode: REVIEW_DATA.mode," in sub, "the round's identity rides every submit"
    send = page[page.index("function sendSubmit("):page.index("function submitReview(")]
    assert "r.status === 409" in send and "showRoundStale(" in send
    assert "if (dropped) recheckRound();" in page, "a reconnect re-checks the served round"
    print("  ok  test_the_tab_restamps_per_round_and_surfaces_a_stale_submit")


def main() -> None:
    test_the_gate_table()
    test_a_diff_boot_is_a_relaunched_diff_gate()
    test_session_boot_refusals()
    test_standalone_servers_carry_no_session_and_check_the_round()
    test_the_tab_restamps_per_round_and_surfaces_a_stale_submit()
    print("OK")


if __name__ == "__main__":
    main()
