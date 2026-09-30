"""Shared scaffolding for the lifecycle-session tests (#240, #242): a clone
with a GitHub origin and a linked worktree, the driver, and the session
record under the git common dir."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, poll_for, post, wait_for_url  # noqa: E402

LOOP = ROOT / "scripts" / "loop.py"

# `interview` and `start` open the human's tab; a no-op browser keeps it headless.
os.environ["BROWSER"] = "true"

QA_INPUT = {"mode": "qa", "context": "Session record",
            "questions": [{"id": "q1", "text": "Scope?"}]}
DRAFT = "# Spec\n\n## Problem\n\nP.\n\n## Design\n\nD.\n"
SIGNED = DRAFT + "\n---\n\n## Revision History\n\nSigned off.\n"


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


def open_session(main: Path) -> str:
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
