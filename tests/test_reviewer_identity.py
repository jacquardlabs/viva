#!/usr/bin/env python3
"""Name the reviewer on the record (#212).

The server stamps `reviewer` from its own `--reviewer` on every output file,
dropping whatever a submitted body carries; the ledger's sign-off line names
it before the date; a lifecycle session resolves it once and every gate
stamps that name. Unnamed, every file and line is as before.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))
import schema  # noqa: E402
from _server_harness import launch_server, poll_for, post  # noqa: E402
from _session_harness import (REVIEWER, approve_all, git, loop, read,  # noqa: E402
                              repo, wait_gone)
from test_session_join import pr_patch, signed_session, stub_gh  # noqa: E402

SERVER = ROOT / "server.py"
REVISION_HISTORY = ROOT / "scripts" / "revision_history.py"
ROUND = {"mode": "review", "doc_file": "d.md", "round": 1,
         "sections": [{"id": "s1", "title": "A", "content": "a"}]}
QA = {"mode": "qa", "questions": [{"id": "q1", "text": "Scope?"}]}


def submit_once(td: Path, data: dict, body: dict, extra: tuple = ()) -> dict:
    """Launch on `data`, submit `body` (claiming `mallory`), return the output."""
    mode = data["mode"]
    (td / "in.json").write_text(json.dumps(data))
    out = td / ("answers.json" if mode == "qa" else "review-r1.json")
    with launch_server(td / "in.json", out, mode=mode, cwd=td, extra=extra) as base:
        post(base, "/submit", dict(body, reviewer="mallory"))
        assert poll_for(out)
    return json.loads(out.read_text())


def test_the_server_stamps_its_own_name() -> None:
    verdicts = {"round": 1, "mode": "review", "submitted_early": False,
                "sections": [{"id": "s1", "verdict": "approved"}]}
    answers = {"answers": [{"id": "q1", "choice": "", "note": "x"}], "submitted_early": False}
    for data, body in ((ROUND, verdicts), (QA, answers)):
        with tempfile.TemporaryDirectory() as td:
            out = submit_once(Path(td), data, body, ("--reviewer", "Ada Lovelace"))
            assert out["reviewer"] == "Ada Lovelace", (data["mode"], out)
        with tempfile.TemporaryDirectory() as td:
            out = submit_once(Path(td), data, body)
            assert "reviewer" not in out, ("unnamed: no field, and never the body's", out)
    r = subprocess.run([sys.executable, str(SERVER), "--mode", "review", "--input", "x",
                        "--output", "y", "--reviewer", " "], capture_output=True, text=True)
    assert r.returncode == 2 and "--reviewer must be a non-empty name" in r.stderr, r.stderr
    print("  ok  test_the_server_stamps_its_own_name")


def test_a_present_reviewer_is_a_name() -> None:
    schema.validate_verdicts({"sections": [], "reviewer": "Ada"})
    schema.validate_verdicts({"sections": []})
    record = {"id": "0" * 32, "repo": "o/r", "viva_dir": "/tmp/.viva",
              "gates": [{"kind": k, "state": "waiting"} for k in schema.SESSION_GATE_KINDS]}
    schema.validate_session(dict(record, reviewer="Ada"))
    for bad in ("", "  ", 5, None):
        for check, data in ((schema.validate_verdicts, {"sections": [], "reviewer": bad}),
                            (schema.validate_session, dict(record, reviewer=bad))):
            try:
                check(data)
            except ValueError as e:
                assert "reviewer must be a non-empty string" in str(e), e
                continue
            raise AssertionError(f"{check.__name__} accepted reviewer={bad!r}")
    print("  ok  test_a_present_reviewer_is_a_name")


def sign(viva: Path, doc: Path, reviewer: str | None, day: str) -> str:
    """One session's round pair, appended to `doc`; returns its sign-off line."""
    for p in viva.glob("review*-r*.json"):
        p.unlink()
    (viva / "review-input-r1.json").write_text(json.dumps(ROUND))
    out = {"round": 1, "sections": [{"id": "s1", "verdict": "approved"}]}
    if reviewer:
        out["reviewer"] = reviewer
    (viva / "review-r1.json").write_text(json.dumps(out))
    subprocess.run([sys.executable, str(REVISION_HISTORY), "--viva-dir", str(viva),
                    "--doc", str(doc), "--date", day], check=True)
    return [line for line in doc.read_text().splitlines()
            if schema.SIGNOFF_LINE_RE.match(line)][-1]


def test_the_ledger_names_each_session() -> None:
    with tempfile.TemporaryDirectory() as td:
        viva, doc = Path(td) / ".viva", Path(td) / "d.md"
        viva.mkdir()
        doc.write_text("# D\n\n## A\n\na\n")
        line = sign(viva, doc, "Ada Lovelace", "2026-10-01")
        assert line == ("Signed off via viva review — 1 round, 1 section, 0 with comments; "
                        "reviewed by Ada Lovelace. 2026-10-01"), line
        assert sign(viva, doc, "Grace Hopper", "2026-10-02").endswith(
            "; reviewed by Grace Hopper. 2026-10-02")
        assert schema.last_signoff_date(doc.read_text()) == "2026-10-02"
        text = doc.read_text()
        assert "reviewed by Ada Lovelace" in text and "reviewed by Grace Hopper" in text
        assert sign(viva, doc, None, "2026-10-03") == (
            "Signed off via viva review — 1 round, 1 section, 0 with comments. 2026-10-03"), \
            "unnamed: the line as before"
    print("  ok  test_the_ledger_names_each_session")


def every_gate_stamps(relaunch: bool) -> None:
    """Resolved once: renaming the clone between gates changes nothing, and a
    join may not name someone else — live, or relaunched after the server stopped."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main = repo(td)
        env = stub_gh(td)
        signed_session(main, td)
        if relaunch:
            r = loop(main, "abandon", "--keep-session")
            assert r.returncode == 0, r.stderr
            wait_gone(main / ".viva")
        assert read(main)["reviewer"] == REVIEWER
        spec = json.loads((main / ".viva" / "review-r1.json").read_text())
        assert spec["reviewer"] == REVIEWER, spec
        git(main, "config", "user.name", "someone else")
        pr_patch(main, td, "a\nB\nc\n")
        r = loop(main, "start", "--target", "7", "--join-session", "--reviewer", "u", env=env)
        assert r.returncode != 0 and "reviewer is 't'" in r.stderr, r.stderr
        r = loop(main, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode == 0, r.stderr
        base = (main / ".viva" / "server.url").read_text().strip()
        approve_all(base, 1)
        assert poll_for(main / ".viva" / "review-r1.json")
        diff = json.loads((main / ".viva" / "review-r1.json").read_text())
        assert diff["reviewer"] == REVIEWER, diff
        loop(main, "abandon")


def test_every_gate_stamps_the_interviews_name() -> None:
    every_gate_stamps(relaunch=False)
    every_gate_stamps(relaunch=True)
    print("  ok  test_every_gate_stamps_the_interviews_name")


def main() -> None:
    os.environ["BROWSER"] = "true"
    test_the_server_stamps_its_own_name()
    test_a_present_reviewer_is_a_name()
    test_the_ledger_names_each_session()
    test_every_gate_stamps_the_interviews_name()
    print("OK")


if __name__ == "__main__":
    main()
