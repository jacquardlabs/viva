#!/usr/bin/env python3
"""A PR's diff joins its lifecycle session (#242): `loop.py start --target
<pr> --join-session`.

Pins #239's test-strategy row 4 — a plain `start --target <pr>` leaves the
record untouched; a join from another repo or before `--spec-source` is
refused; with the server gone after spec sign-off, the join launches a
server whose `session.gates` read spec `done`, diff `live` — and the
pre-mortem's failures 1 (the wrong PR joins), 2 (an intervening start
orphans the waiting server), and 8 (the PR is reviewed from another worktree).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import schema  # noqa: E402
from _server_harness import get, poll_for, post  # noqa: E402
from _session_harness import (DRAFT, approve_all, gates, git, loop,  # noqa: E402
                              open_session, read, record_path, repo, wait_gone)


DOCKET = Path(__file__).resolve().parent.parent / "scripts" / "docket.py"


def stub_gh(td: Path) -> dict:
    """A stand-in `gh` on PATH: logs its argv, prints `td/pr.patch`."""
    bin_dir = td / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\necho \"$@\" >> '{td / 'gh.log'}'\n"
                  f"cat '{td / 'pr.patch'}'\n")
    gh.chmod(0o755)
    return dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def last_gh(td: Path) -> str:
    return (td / "gh.log").read_text().splitlines()[-1]


def pr_patch(main: Path, td: Path, text: str) -> None:
    """The PR's diff, made in `main` and left there, as a checkout would."""
    (main / "f.txt").write_text(text)
    (td / "pr.patch").write_text(git(main, "diff") + "\n")


def many_hunks(main: Path, td: Path) -> None:
    """A diff above the summaries threshold, so the join stops at the seam."""
    lines = [f"line {i}" for i in range(300)]
    (main / "g.txt").write_text("\n".join(lines) + "\n")
    git(main, "add", "g.txt")
    git(main, "commit", "-q", "-m", "g")
    for i in range(0, 300, 20):
        lines[i] = f"changed {i}"
    (main / "g.txt").write_text("\n".join(lines) + "\n")
    (td / "pr.patch").write_text(git(main, "diff") + "\n")


def signed_session(main: Path, td: Path, source: bool = True) -> str:
    """Open a session and sign its spec off; the server stays up waiting.
    With `source`, record the stamp's commit as the spec source."""
    base = open_session(main)
    (main / "spec.md").write_text(DRAFT)
    r = loop(main, "start", "--doc", "spec.md", "--handoff")
    assert r.returncode == 0, r.stderr
    approve_all(base, 1)
    assert poll_for(main / ".viva" / "review-r1.json")
    r = loop(main, "finish")
    assert r.returncode == 0, r.stderr
    if source:
        git(main, "add", "spec.md")
        git(main, "commit", "-q", "-m", "stamp")
        r = loop(main, "session", "--spec-source", "commit:spec.md@HEAD")
        assert r.returncode == 0, r.stderr
    return base


def status(cwd: Path) -> str:
    r = loop(cwd, "session")
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()[-1]


# ── refusals: nothing joins that should not ─────────────────────────────────
def test_refusals_leave_the_record_and_the_server_alone() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main, wt = repo(td), td / "wt"
        env = stub_gh(td)
        pr_patch(main, td, "a\nB\nc\n")
        assert status(main) == "=== session: none ==="
        r = loop(main, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode != 0 and "no session" in r.stderr, r.stderr

        base = signed_session(main, td, source=False)
        assert status(wt) == "=== session: unsourced ===", "visible from either worktree"
        before = record_path(main).read_bytes()
        # Row 4: refused before `session --spec-source`.
        r = loop(wt, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode != 0 and "--spec-source" in r.stderr, r.stderr
        git(main, "add", "spec.md")
        git(main, "commit", "-q", "-m", "stamp")
        r = loop(main, "session", "--spec-source", "commit:spec.md@HEAD")
        assert r.returncode == 0, r.stderr
        assert status(main) == "=== session: waiting ==="
        before = record_path(main).read_bytes()

        # Row 4: a PR in another repo is refused.
        r = loop(wt, "start", "--target", "https://github.com/x/r/pull/7",
                 "--join-session", env=env)
        assert r.returncode != 0 and "x/r" in r.stderr and "o/r" in r.stderr, r.stderr
        # Working-tree, ref, and doc targets never join.
        for argv in (["--kind", "worktree"], ["--target", "HEAD", "--kind", "ref"]):
            r = loop(wt, "start", *argv, "--join-session", env=env)
            assert r.returncode != 0 and "never joins" in r.stderr, r.stderr
        r = loop(wt, "start", "--doc", "f.txt", "--join-session", env=env)
        assert r.returncode != 0 and "--join-session" in r.stderr, r.stderr
        assert record_path(main).read_bytes() == before

        # Pre-mortem 2: an intervening start in the session's own worktree is
        # refused, names the session and the join, and orphans nothing.
        sid = read(main)["id"]
        for argv in (["--doc", "spec.md"], ["--target", "7"], ["--kind", "worktree"]):
            r = loop(main, "start", *argv, env=env)
            assert r.returncode != 0, r.stdout
            assert sid in r.stderr and "--join-session" in r.stderr, r.stderr
        assert get(base, "/input")["session"]["id"] == sid, "the waiting server is untouched"

        # Row 4: a plain PR review elsewhere never joins, leaves the record
        # byte-identical, and says a session is waiting.
        r = loop(wt, "start", "--target", "7", env=env)
        assert r.returncode == 0, r.stderr
        assert f"session {sid}" in r.stdout and "does not join it" in r.stdout, r.stdout
        assert "--join-session" in r.stdout, r.stdout
        assert "--repo" not in last_gh(td), "a standalone review keeps its capture"
        standalone = (wt / ".viva" / "server.url").read_text().strip()
        assert "session" not in get(standalone, "/input")
        assert record_path(main).read_bytes() == before
        assert get(base, "/input")["session"]["id"] == sid, "nor does it orphan the spec server"
        r = loop(wt, "abandon")
        assert r.returncode == 0, r.stderr
        assert record_path(main).read_bytes() == before, "abandon elsewhere keeps the session"
        wait_gone(wt / ".viva")
        r = loop(main, "abandon")
        assert r.returncode == 0 and not record_path(main).exists(), r.stderr
    print("  ok  test_refusals_leave_the_record_and_the_server_alone")


# ── the live join, from another worktree ─────────────────────────────────────
def test_join_arms_into_the_waiting_server_from_another_worktree() -> None:
    """Pre-mortems 1 and 8: the PR found from a second worktree lands in the
    waiting server's own `.viva/`, pinned to the session's repo, and no other
    PR can take the gate afterward."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main, wt = repo(td), td / "wt"
        env = stub_gh(td)
        base = signed_session(main, td)
        sid = read(main)["id"]
        pr_patch(main, td, "a\nB\nc\n")

        r = loop(wt, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode == 0, r.stderr
        # Pre-mortem 8: the flag is global, so the hint spells it before the
        # subcommand; appended after one it exits 2.
        assert f"`loop.py --viva-dir {main / '.viva'} wait`" in r.stdout, r.stdout
        assert last_gh(td) == "pr diff 7 --repo o/r", last_gh(td)
        served = get(base, "/input")
        assert served["mode"] == "diff" and served["round"] == 1, served
        assert served["session"]["id"] == sid
        assert [g["state"] for g in served["session"]["gates"]] == ["done", "done", "live"]
        assert not (wt / ".viva" / "server.url").exists(), "no second server"
        assert not (main / ".viva" / "review-r1.json").exists(), "the spec round is cleared"
        record = read(main)
        assert record["pr"] == "o/r#7" and record["viva_dir"] == str(main / ".viva")
        assert gates(main) == {"intake": "done", "spec": "done", "diff": "live"}
        assert status(wt) == "=== session: joined ==="

        # Pre-mortem 1: the gate is taken; neither another PR nor a re-join
        # of this one can take it again.
        r = loop(wt, "start", "--target", "8", "--join-session", env=env)
        assert r.returncode != 0 and "o/r#7" in r.stderr, r.stderr
        r = loop(wt, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode != 0 and "already live" in r.stderr, r.stderr

        # A plain review of the same PR elsewhere is standalone: its sign-off
        # never ends the session whose gate another `.viva/` holds.
        r = loop(wt, "start", "--target", "7", env=env)
        assert r.returncode == 0, r.stderr
        standalone = (wt / ".viva" / "server.url").read_text().strip()
        approve_all(standalone, 1)
        assert poll_for(wt / ".viva" / "review-r1.json")
        r = loop(wt, "finish", env=env)
        assert r.returncode == 0 and "record removed" not in r.stdout, r.stdout
        wait_gone(wt / ".viva")
        assert read(main)["pr"] == "o/r#7" and get(base, "/input")["session"]["id"] == sid

        approve_all(base, 1)
        assert poll_for(main / ".viva" / "review-r1.json")
        r = loop(wt, "--viva-dir", str(main / ".viva"), "finish", env=env)
        assert r.returncode == 0, r.stderr
        assert "record removed" in r.stdout, r.stdout
        wait_gone(main / ".viva")
        assert not record_path(main).exists()
    print("  ok  test_join_arms_into_the_waiting_server_from_another_worktree")


# ── the relaunch: nothing answers ────────────────────────────────────────────
def test_join_relaunches_a_session_whose_server_died() -> None:
    """Row 4's last clause, for a server that left a stale `server.url`
    behind (a hard kill skips its cleanup)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main = repo(td)
        env = stub_gh(td)
        signed_session(main, td)
        sid = read(main)["id"]
        r = loop(main, "abandon", "--keep-session")
        assert r.returncode == 0, r.stderr
        wait_gone(main / ".viva")
        (main / ".viva" / "server.url").write_text("http://127.0.0.1:9")
        pr_patch(main, td, "a\nB\nc\n")

        # Pre-mortem 2, dead-server case: every preflight names the session
        # and its two exits rather than the bare delete-the-file rule.
        before = record_path(main).read_bytes()
        for argv in (["start", "--doc", "f.txt"], ["start", "--target", "7"],
                     ["interview", "--input", ".viva/qa-input.json"]):
            r = loop(main, *argv, env=env)
            assert r.returncode != 0, r.stdout
            assert f"session {sid}" in r.stderr, r.stderr
            assert "--join-session" in r.stderr and "abandon" in r.stderr, r.stderr
        assert (main / ".viva" / "server.url").exists()
        assert record_path(main).read_bytes() == before

        r = loop(main, "start", "--target", "#7", "--join-session", env=env)
        assert r.returncode == 0, r.stderr
        assert "--viva-dir" not in r.stdout, "the diff gate runs here"
        base = (main / ".viva" / "server.url").read_text().strip()
        served = get(base, "/input")
        assert served["session"]["id"] == sid, served.get("session")
        assert [g["state"] for g in served["session"]["gates"]] == ["done", "done", "live"]
        assert served["mode"] == "diff", served["mode"]
        assert read(main)["pr"] == "o/r#7"
        r = loop(main, "abandon")
        assert r.returncode == 0 and not record_path(main).exists(), r.stderr
    print("  ok  test_join_relaunches_a_session_whose_server_died")


def test_relaunch_from_another_worktree_through_the_summaries_seam() -> None:
    """Pre-mortem 8 with nothing answering: the gate relaunches in the
    joining worktree, the record follows it, and `arm` after the seam still
    launches the session rather than a standalone diff server."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main, wt = repo(td), td / "wt"
        env = stub_gh(td)
        signed_session(main, td)
        sid = read(main)["id"]
        r = loop(main, "abandon", "--keep-session")
        assert r.returncode == 0, r.stderr
        wait_gone(main / ".viva")
        many_hunks(main, td)

        r = loop(wt, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode == 0, r.stderr
        assert "NOT armed" in r.stdout, r.stdout
        assert read(main)["viva_dir"] == str(wt / ".viva"), read(main)
        assert not (wt / ".viva" / "server.url").exists()
        r = loop(wt, "arm", env=env)
        assert r.returncode == 0, r.stderr
        base = (wt / ".viva" / "server.url").read_text().strip()
        served = get(base, "/input")
        assert served["session"]["id"] == sid, served.get("session")
        assert served["mode"] == "diff"

        # The server that dies mid-diff names the join, not a plain start.
        (wt / ".viva" / "server.url").write_text("http://127.0.0.1:9")
        r = loop(wt, "wait", env=env)
        assert r.returncode == 2 and "--join-session" in r.stderr, r.stderr
        (wt / ".viva" / "server.url").write_text(base)

        approve_all(base, 1)
        assert poll_for(wt / ".viva" / "review-r1.json")
        r = loop(wt, "finish", env=env)
        assert r.returncode == 0 and "record removed" in r.stdout, r.stderr
        wait_gone(wt / ".viva")
        assert not record_path(main).exists()
    print("  ok  test_relaunch_from_another_worktree_through_the_summaries_seam")


# ── the gate's own `.viva/`, reused by a plain review ───────────────────────
def test_a_plain_start_in_the_joined_dir_never_joins() -> None:
    """Pre-mortem 1's fork case: after the joined server dies, a plain review
    of the same PR in the same `.viva/` must stay standalone — no session
    relaunch, and its finish leaves the record for the real join."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main = repo(td)
        env = stub_gh(td)
        base = signed_session(main, td)
        sid = read(main)["id"]
        pr_patch(main, td, "a\nB\nc\n")
        r = loop(main, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode == 0, r.stderr
        post(base, "/abandon", {})
        wait_gone(main / ".viva")
        before = record_path(main).read_bytes()

        r = loop(main, "start", "--target", "7", env=env)
        assert r.returncode == 0 and "does not join it" in r.stdout, r.stdout
        standalone = (main / ".viva" / "server.url").read_text().strip()
        assert "session" not in get(standalone, "/input"), "a plain start never relaunches the session"
        approve_all(standalone, 1)
        assert poll_for(main / ".viva" / "review-r1.json")
        r = loop(main, "finish", env=env)
        assert r.returncode == 0 and "record removed" not in r.stdout, r.stdout
        wait_gone(main / ".viva")
        assert record_path(main).read_bytes() == before

        # The real join still relaunches it afterward.
        r = loop(main, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode == 0, r.stderr
        relaunched = (main / ".viva" / "server.url").read_text().strip()
        assert get(relaunched, "/input")["session"]["id"] == sid
        r = loop(main, "abandon")
        assert r.returncode == 0 and not record_path(main).exists(), r.stderr
    print("  ok  test_a_plain_start_in_the_joined_dir_never_joins")


def test_abandoning_a_standalone_review_keeps_the_kept_session() -> None:
    """`abandon --keep-session` frees the owner `.viva/` for other reviews;
    abandoning one of those ends only its own server, not the session."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main = repo(td)
        env = stub_gh(td)
        signed_session(main, td)
        r = loop(main, "abandon", "--keep-session")
        assert r.returncode == 0, r.stderr
        wait_gone(main / ".viva")
        before = record_path(main).read_bytes()
        pr_patch(main, td, "a\nB\nc\n")

        r = loop(main, "start", "--target", "99", env=env)
        assert r.returncode == 0, r.stderr
        r = loop(main, "abandon")
        assert r.returncode == 0 and "record removed" not in r.stdout, r.stdout
        wait_gone(main / ".viva")
        assert record_path(main).read_bytes() == before
    print("  ok  test_abandoning_a_standalone_review_keeps_the_kept_session")


# ── a capture that yields no round costs the waiting session nothing ────────
def docket_state(td: Path, name: str) -> str:
    proc = subprocess.run([sys.executable, str(DOCKET), "--root", f"{td}/*",
                           "--format", "json"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return next(r["state"] for r in json.loads(proc.stdout) if r["repo"] == name)


def test_a_failed_or_empty_capture_leaves_the_waiting_session_whole() -> None:
    """The live join clears the spec's round files only once the PR's diff
    parsed into a round; before that, `.viva/`, record, and docket hold."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main, wt = repo(td), td / "wt"
        env = stub_gh(td)
        base = signed_session(main, td)
        owner = main / ".viva"
        files = {p.name: p.read_bytes() for p in owner.iterdir()
                 if p.is_file() and p.name != "server.log"}
        before = record_path(main).read_bytes()
        assert docket_state(td, "main") == "waiting"

        # No pr.patch: the stub's `cat` fails, so `gh` exits 1.
        r = loop(wt, "start", "--target", "999", "--join-session", env=env)
        assert r.returncode != 0 and "capture failed" in r.stderr, r.stderr
        (td / "pr.patch").write_text("")
        r = loop(wt, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode == 0 and "no changes to review" in r.stdout, r.stdout
        for p in owner.iterdir():
            if p.is_file() and p.name != "server.log":
                assert files.get(p.name) == p.read_bytes(), f"{p.name} changed"
        assert set(files) <= {p.name for p in owner.iterdir()}, "a spec file was cleared"
        assert not (wt / ".viva" / "target.json").exists()
        assert record_path(main).read_bytes() == before
        assert schema.session_is_waiting(get(base, "/input")["session"])
        assert docket_state(td, "main") == "waiting"

        pr_patch(main, td, "a\nB\nc\n")
        r = loop(wt, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode == 0, r.stderr
        assert get(base, "/input")["mode"] == "diff"
        assert not (owner / "review-r1.json").exists(), "the spec round clears at the join"
        r = loop(main, "abandon")
        assert r.returncode == 0 and not record_path(main).exists(), r.stderr
    print("  ok  test_a_failed_or_empty_capture_leaves_the_waiting_session_whole")


def main() -> None:
    test_refusals_leave_the_record_and_the_server_alone()
    test_join_arms_into_the_waiting_server_from_another_worktree()
    test_join_relaunches_a_session_whose_server_died()
    test_relaunch_from_another_worktree_through_the_summaries_seam()
    test_a_plain_start_in_the_joined_dir_never_joins()
    test_abandoning_a_standalone_review_keeps_the_kept_session()
    test_a_failed_or_empty_capture_leaves_the_waiting_session_whole()
    print("OK")


if __name__ == "__main__":
    main()
