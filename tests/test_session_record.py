#!/usr/bin/env python3
"""The lifecycle session record (#240): `<git common dir>/viva/session.json`.

Pins #239's test-strategy row 2: the record is visible from a second
worktree, fails `validate_session` when malformed, has no spec source until
`loop.py session --spec-source` runs, and is gone after the session's diff
`finish` and after `abandon` — while `.viva/`'s clear never touches it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))
import schema  # noqa: E402
from _server_harness import get, poll_for, post, wait_for_url  # noqa: E402

LOOP = ROOT / "scripts" / "loop.py"

# `interview` and `start` open the human's tab; a no-op browser keeps it headless.
os.environ["BROWSER"] = "true"

QA_INPUT = {"mode": "qa", "context": "Session record",
            "questions": [{"id": "q1", "text": "Scope?"}]}
DRAFT = "# Spec\n\n## Problem\n\nP.\n\n## Design\n\nD.\n"
SIGNED = DRAFT + "\n---\n\n## Revision History\n\nSigned off.\n"


def good_record(**over) -> dict:
    record = {"id": "0" * 32, "repo": "o/r", "viva_dir": "/tmp/x/.viva",
              "gates": [{"kind": "intake", "state": "done"},
                        {"kind": "spec", "state": "live"},
                        {"kind": "diff", "state": "waiting"}]}
    record.update(over)
    return record


def git(cwd: Path, *argv) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *argv],
        cwd=str(cwd), check=True, capture_output=True, text=True).stdout.strip()


def repo(td: Path) -> Path:
    """A clone with a GitHub origin, one commit, and a linked worktree."""
    main = td / "main"
    main.mkdir()
    git(main, "init", "-q")
    git(main, "remote", "add", "origin", "git@github.com:o/r.git")
    (main / "f.txt").write_text("a\nb\nc\n")
    git(main, "add", "f.txt")
    git(main, "commit", "-q", "-m", "init")
    git(main, "worktree", "add", "-q", str(td / "wt"))
    for tree in (main, td / "wt"):
        (tree / ".viva").mkdir()
        (tree / ".viva" / "qa-input.json").write_text(json.dumps(QA_INPUT))
    return main


def loop(cwd: Path, *argv, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LOOP), "--viva-dir", str(cwd / ".viva"), *argv],
        cwd=str(cwd), capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env=env)


def record_path(main: Path) -> Path:
    return main / ".git" / "viva" / "session.json"


def read(main: Path) -> dict:
    return json.loads(record_path(main).read_text())


def gates(main: Path) -> dict:
    return {g["kind"]: g["state"] for g in read(main)["gates"]}


def open_session(main: Path):
    """`interview --session`, answered; returns the live server's base URL."""
    proc = subprocess.Popen(
        [sys.executable, str(LOOP), "interview", "--session",
         "--input", ".viva/qa-input.json"],
        cwd=str(main), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    base = wait_for_url(main / ".viva" / "answers.json")
    assert poll_for(record_path(main)), "interview --session writes the record"
    post(base, "/submit", {"answers": [{"id": "q1", "choice": "", "note": "x"}],
                           "submitted_early": False})
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 0, err
    assert f"session {read(main)['id']} · o/r" in out, out
    # `interview --session` launches `--mode session` on the record's id (#241).
    assert get(base, "/input")["session"]["id"] == read(main)["id"]
    return base


def wait_gone(viva: Path) -> None:
    for _ in range(50):
        if not (viva / "server.url").exists():
            return
        time.sleep(0.2)
    raise AssertionError("server never shut down")


def approve_all(base: str, round_no: int) -> None:
    served = get(base, "/input")
    ids = [s["id"] for s in served["sections"]]
    post(base, "/submit", {"round": round_no, "mode": served["mode"],
                           "submitted_early": False,
                           "sections": [{"id": i, "verdict": "approved"} for i in ids]})


# ── validate_session ─────────────────────────────────────────────────────────
def test_validate_session() -> None:
    schema.validate_session(good_record())
    schema.validate_session(good_record(
        pr="o/r#7", spec={"kind": "commit", "path": "d.md", "sha": "a" * 40}))
    schema.validate_session(good_record(
        spec={"kind": "comment", "url": "https://x", "updated_at": "t"}))
    bad = [
        [],
        good_record(id="xyz"),
        good_record(repo="r"),
        good_record(viva_dir="rel/.viva"),
        good_record(gates=[{"kind": "spec", "state": "live"}]),
        good_record(gates=[{"kind": "intake", "state": "waiting"},
                           {"kind": "spec", "state": "done"},
                           {"kind": "diff", "state": "waiting"}]),
        good_record(gates=[{"kind": "intake", "state": "live"},
                           {"kind": "spec", "state": "live"},
                           {"kind": "diff", "state": "waiting"}]),
        good_record(gates=[{"kind": "intake", "state": "open"},
                           {"kind": "spec", "state": "waiting"},
                           {"kind": "diff", "state": "waiting"}]),
        good_record(spec=None),
        good_record(spec={"kind": "commit", "path": "d.md", "sha": "abc"}),
        good_record(spec={"kind": "comment", "url": "https://x"}),
        good_record(pr=7),
        good_record(pr="#7"),
    ]
    for record in bad:
        try:
            schema.validate_session(record)
        except ValueError:
            continue
        raise AssertionError(f"validate_session accepted {record!r}")
    print("  ok  test_validate_session")


def test_session_is_waiting() -> None:
    between = [{"kind": "intake", "state": "done"}, {"kind": "spec", "state": "done"},
               {"kind": "diff", "state": "waiting"}]
    assert schema.session_is_waiting({"id": "x", "gates": between})
    assert not schema.session_is_waiting(good_record()), "a live spec gate"
    signed = [dict(g, state="done") for g in between]
    assert not schema.session_is_waiting({"gates": signed}), "a diff sign-off shutting down"
    for junk in (None, {}, {"gates": []}, {"gates": "done"}, [between]):
        assert not schema.session_is_waiting(junk), junk
    print("  ok  test_session_is_waiting")


def test_serves_round() -> None:
    """Armed-ness is (mode, round) off a live gate — never the number alone."""
    between = {"gates": [{"kind": "intake", "state": "done"}, {"kind": "spec", "state": "done"},
                         {"kind": "diff", "state": "waiting"}]}
    diff, spec = {"mode": "diff", "round": 1}, {"round": 1}
    assert schema.serves_round({"round": 1}, spec, 1), "a mode-less round is review"
    assert schema.serves_round(diff, diff, 1)
    assert not schema.serves_round({"round": 2}, spec, 1), "a stale round"
    assert not schema.serves_round({"mode": "review", "round": 1}, diff, 1), "mode differs"
    assert not schema.serves_round(dict(diff, session=between), diff, 1), "a waiting gate"
    for junk in (None, {}, [], {"questions": []}):
        assert not schema.serves_round(junk, spec, 1), junk
    print("  ok  test_serves_round")


# ── the lifecycle, across two worktrees ──────────────────────────────────────
def test_record_spans_worktrees_and_ends_at_the_sessions_diff() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main, wt = repo(td), td / "wt"
        base = open_session(main)
        record = read(main)
        assert len(record["id"]) == 32 and record["repo"] == "o/r", record
        assert record["viva_dir"] == str(main / ".viva"), record
        assert gates(main) == {"intake": "live", "spec": "waiting", "diff": "waiting"}

        # One per clone, and the second worktree sees it.
        r = loop(wt, "interview", "--session", "--input", ".viva/qa-input.json")
        assert r.returncode != 0 and "already open" in r.stderr, r.stderr
        r = loop(wt, "session", "--spec-source", "commit:spec.md@HEAD")
        assert r.returncode != 0 and "spec gate is waiting" in r.stderr, r.stderr

        # Hand-off: intake closes, spec goes live.
        (main / "spec.md").write_text(DRAFT)
        git(main, "add", "spec.md")
        git(main, "commit", "-q", "-m", "draft")
        draft_sha = git(main, "rev-parse", "HEAD")
        r = loop(main, "start", "--doc", "spec.md", "--handoff")
        assert r.returncode == 0, r.stderr
        assert gates(main) == {"intake": "done", "spec": "live", "diff": "waiting"}

        approve_all(base, 1)
        assert poll_for(main / ".viva" / "review-r1.json")
        r = loop(main, "finish")
        assert r.returncode == 0, r.stderr
        assert "spec gate closed" in r.stdout and "--spec-source" in r.stdout, r.stdout
        assert gates(main) == {"intake": "done", "spec": "done", "diff": "waiting"}
        # The session server idles for the diff gate; a `start` here would
        # orphan it, so it refuses and names the waiting session.
        assert get(base, "/input")["session"]["gates"][1]["state"] == "done"
        r = loop(main, "start", "--kind", "worktree")
        assert r.returncode != 0 and "waiting at" in r.stderr, r.stderr
        assert "abandon --keep-session" in r.stderr, r.stderr
        # A retried finish or a rearm between gates is refused before it
        # touches the doc or writes a round (pre-mortem #5: a doubled sign-off).
        signed = (main / "spec.md").read_text()
        for verb in ("finish", "rearm"):
            r = loop(main, verb)
            assert r.returncode != 0 and "already signed off" in r.stderr, r.stderr
        assert (main / "spec.md").read_text() == signed, "one sign-off block"
        assert not (main / ".viva" / "review-input-r2.json").exists()
        # Stopping the waiting server frees the worktree and keeps the
        # session; its PR's join relaunches it (spec: Operations).
        r = loop(main, "abandon", "--keep-session")
        assert r.returncode == 0 and "kept" in r.stdout, r.stderr
        wait_gone(main / ".viva")
        assert gates(main) == {"intake": "done", "spec": "done", "diff": "waiting"}
        r = loop(main, "abandon", "--keep-session")
        assert r.returncode != 0 and "none is answering" in r.stderr, r.stderr
        assert record_path(main).exists()
        assert "spec" not in read(main), "no spec source before `session --spec-source`"

        # A source must carry the sign-off the minutes read back.
        r = loop(wt, "session", "--spec-source", f"commit:spec.md@{draft_sha}")
        assert r.returncode != 0 and "Revision History" in r.stderr, r.stderr
        r = loop(wt, "session", "--spec-source", "commit:nope.md@HEAD")
        assert r.returncode != 0 and "not in" in r.stderr, r.stderr
        git(main, "commit", "-q", "-am", "stamp")
        signed_sha = git(main, "rev-parse", "HEAD")
        r = loop(wt, "session", "--spec-source", f"commit:./spec.md@{signed_sha[:8]}")
        assert r.returncode == 0, r.stderr
        assert read(main)["spec"] == {"kind": "commit", "path": "spec.md",
                                      "sha": signed_sha}, read(main)

        # The comment form, through a stand-in `gh`.
        bin_dir = td / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        (td / "comment.json").write_text(json.dumps(
            {"body": SIGNED, "updated_at": "2026-09-28T00:00:00Z"}))
        gh.write_text(f"#!/bin/sh\ncat '{td / 'comment.json'}'\n")
        gh.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        url = "https://github.com/o/r/issues/9#issuecomment-123"
        r = loop(wt, "session", "--spec-source",
                 "https://github.com/x/y/issues/9#issuecomment-123", env=env)
        assert r.returncode != 0 and "x/y" in r.stderr, r.stderr
        r = loop(wt, "session", "--spec-source", url, env=env)
        assert r.returncode == 0, r.stderr
        assert read(main)["spec"] == {"kind": "comment", "url": url,
                                      "updated_at": "2026-09-28T00:00:00Z"}

        # A diff that is not the session's PR — the clear spares the record,
        # and its sign-off leaves the session waiting.
        (main / "f.txt").write_text("a\nB\nc\n")
        r = loop(main, "start", "--kind", "worktree")
        assert r.returncode == 0, r.stderr
        assert record_path(main).exists(), "the state clear never touches the record"
        base = (main / ".viva" / "server.url").read_text().strip()
        approve_all(base, 1)
        assert poll_for(main / ".viva" / "review-r1.json")
        r = loop(main, "finish")
        assert r.returncode == 0, r.stderr
        wait_gone(main / ".viva")
        assert record_path(main).exists(), "an unrelated diff never ends the session"

        # The session's own PR (joined by #242; staged here by hand).
        (main / "f.txt").write_text("a\nBB\nc\n")
        r = loop(main, "start", "--kind", "worktree")
        assert r.returncode == 0, r.stderr
        target = json.loads((main / ".viva" / "target.json").read_text())
        target.update(kind="pr", repo="o/r", number=7)
        (main / ".viva" / "target.json").write_text(json.dumps(target))
        joined = dict(read(main), pr="o/r#7")
        record_path(main).write_text(json.dumps(joined))
        base = (main / ".viva" / "server.url").read_text().strip()
        approve_all(base, 1)
        assert poll_for(main / ".viva" / "review-r1.json")
        r = loop(main, "finish")
        assert r.returncode == 0, r.stderr
        assert "record removed" in r.stdout, r.stdout
        wait_gone(main / ".viva")
        assert not record_path(main).exists(), "diff finish ends the session"
    print("  ok  test_record_spans_worktrees_and_ends_at_the_sessions_diff")


def test_abandon_ends_the_session() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main, wt = repo(td), td / "wt"
        base = open_session(main)

        # `--keep-session` only stops a server between gates, not a live one.
        r = loop(main, "abandon", "--keep-session")
        assert r.returncode != 0 and "between gates" in r.stderr, r.stderr
        assert get(base, "/input")["session"]["id"] == read(main)["id"]

        # Live in `main`: another worktree may not end it.
        r = loop(wt, "abandon")
        assert r.returncode != 0 and "is live at" in r.stderr, r.stderr
        assert record_path(main).exists()

        r = loop(main, "abandon")
        assert r.returncode == 0, r.stderr
        assert "record removed" in r.stdout, r.stdout
        assert not record_path(main).exists(), "abandon ends the session"

        # A stale record, no server anywhere: abandon from either worktree.
        record_path(main).write_text(json.dumps(good_record(viva_dir=str(main / ".viva"))))
        r = loop(wt, "abandon")
        assert r.returncode == 0 and "record removed" in r.stdout, r.stderr
        assert not record_path(main).exists()

        # Malformed: every reader refuses it, and abandon clears it.
        record_path(main).write_text(json.dumps(good_record(id="nope")))
        r = loop(wt, "session", "--spec-source", "commit:f.txt@HEAD")
        assert r.returncode != 0 and "invalid session record" in r.stderr, r.stderr
        r = loop(main, "interview", "--session", "--input", ".viva/qa-input.json")
        assert r.returncode != 0 and "invalid session record" in r.stderr, r.stderr
        r = loop(wt, "abandon")
        assert "removed an invalid session record" in r.stderr, r.stderr
        assert not record_path(main).exists()
    print("  ok  test_abandon_ends_the_session")


def test_interview_session_needs_a_github_origin() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        git(td, "init", "-q")
        (td / ".viva").mkdir()
        (td / ".viva" / "qa-input.json").write_text(json.dumps(QA_INPUT))
        r = loop(td, "interview", "--session", "--input", ".viva/qa-input.json")
        assert r.returncode != 0 and "origin" in r.stderr, r.stderr
        assert not (td / ".viva" / "server.url").exists(), "refused before launch"
    print("  ok  test_interview_session_needs_a_github_origin")


def main() -> None:
    test_validate_session()
    test_session_is_waiting()
    test_serves_round()
    test_record_spans_worktrees_and_ends_at_the_sessions_diff()
    test_abandon_ends_the_session()
    test_interview_session_needs_a_github_origin()
    print("OK")


if __name__ == "__main__":
    main()
