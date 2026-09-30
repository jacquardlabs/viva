#!/usr/bin/env python3
"""Session minutes at the joined diff gate's sign-off (#245), #239's
test-strategy row 7: `.viva/minutes.md` holds every row of the spec's Revision
History from either source kind, commit and comment, and every diff-gate
ledger row; nothing posts without the confirm. Also pre-mortem 5 (repeated
sign-off lines and Decisions fold to one) and 6 (an edited comment is noted,
an unreadable one refuses the finish before the record goes).
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))
import schema  # noqa: E402
from _server_harness import get, poll_for, post  # noqa: E402
from _session_harness import (approve_all, git, loop, read, record_path,  # noqa: E402
                              repo, wait_gone)
from test_session_join import pr_patch, signed_session  # noqa: E402

SKILL = ROOT / ".claude" / "skills" / "viva-review" / "SKILL.md"
PRODUCT = ROOT / "PRODUCT.md"
COMMENT_URL = "https://github.com/o/r/issues/9#issuecomment-1"
ROW = "| 1 | Design | changes | “name the gate” |"
SIGNOFF = "Signed off via viva review — 2 rounds, 2 sections, 1 with comments. 2026-09-27"
SUPERSEDED = ('**Superseded 2026-09-28** (append-only; the Decisions above stay as answered):\n\n'
              '- "Scope? → the CLI only" — superseded by Design.')
AMENDED = "**Amended 2026-09-29**: the record also carries `viva_dir`."
# #239's own shape: a pre-#253 Decisions block listing a question per section,
# the sign-off line printed three times, then the append-only notes.
SPEC = f"""# Spec

## Problem

P.

## Design

D.

---

## Revision History

{SIGNOFF}

| Round | Section | Verdict | Note |
|-------|---------|---------|------|
{ROW}

### Decisions

**Problem**

- Scope? → the CLI only

**Design**

- Scope? → the CLI only
- Budget? → 3 days

{SIGNOFF}

{SIGNOFF}


{SUPERSEDED}

{AMENDED}
"""


def stub_gh(td: Path) -> dict:
    """`gh` on PATH: logs argv; `api` serves `comment.json` (exit 1 when it is
    missing, as a deleted comment 404s); anything else prints `pr.patch`."""
    bin_dir = td / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\necho \"$@\" >> '{td / 'gh.log'}'\n"
                  f"case \"$1\" in api) cat '{td / 'comment.json'}' ;; "
                  f"*) cat '{td / 'pr.patch'}' ;; esac\n")
    gh.chmod(0o755)
    return dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def serve_comment(td: Path, body: str, updated_at: str) -> None:
    (td / "comment.json").write_text(json.dumps({"body": body, "updated_at": updated_at}))


def joined(td: Path, source: str) -> tuple:
    """A session whose signed spec reads as SPEC from `source` (`commit` or
    `comment`), joined by PR #7; returns (main, env, the diff gate's base)."""
    main = repo(td)
    env = stub_gh(td)
    signed_session(main, td, source=False)
    (main / "spec.md").write_text(SPEC)
    git(main, "add", "spec.md")
    git(main, "commit", "-q", "-m", "stamp")
    if source == "commit":
        r = loop(main, "session", "--spec-source", "commit:spec.md@HEAD", env=env)
    else:
        serve_comment(td, SPEC, "T1")
        r = loop(main, "session", "--spec-source", COMMENT_URL, env=env)
    assert r.returncode == 0, r.stderr
    pr_patch(main, td, "a\nB\nc\n")
    r = loop(main, "start", "--target", "7", "--join-session", env=env)
    assert r.returncode == 0, r.stderr
    return main, env, (main / ".viva" / "server.url").read_text().strip()


def request_changes(main: Path, base: str, note: str) -> str:
    """Round 1's one hunk gets `changes`; returns its title."""
    served = get(base, "/input")
    hunk = served["sections"][0]
    post(base, "/submit", {"round": 1, "mode": "diff", "submitted_early": False,
                           "sections": [{"id": hunk["id"], "verdict": "changes",
                                         "note": note}]})
    assert poll_for(main / ".viva" / "review-r1.json")
    return hunk["title"]


def spec_part(minutes: str) -> str:
    return minutes.split("## Diff review")[0]


def assert_spec_ledger(minutes: str) -> None:
    """Every row of the spec's ledger, once each, and none of its body."""
    spec = spec_part(minutes)
    assert ROW in spec, minutes
    assert spec.count(SIGNOFF) == 1, "pre-mortem 5: one sign-off line"
    assert spec.count(schema.DECISIONS_HEADING) == 1, "pre-mortem 5: one Decisions block"
    assert "- Scope? → the CLI only — **Problem**, **Design**" in spec, spec
    assert "- Budget? → 3 days — **Design**" in spec, spec
    assert spec.count("Scope? → the CLI only —") == 1, "one bullet per answer"
    assert SUPERSEDED in spec and AMENDED in spec, "the notes carry verbatim"
    assert spec.index(schema.DECISIONS_HEADING) < spec.index("**Superseded"), \
        "the Decisions stay above the note that points up at them"
    assert "## Problem" not in minutes and "\nP.\n" not in minutes, "the spec body stays out"
    assert "\n\n\n" not in minutes


def test_a_commit_source_and_the_empty_finish() -> None:
    """Row 7, commit source: the diff gate's `changes` row lands beside the
    spec's ledger, on the empty re-capture finish; nothing posts."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main, env, base = joined(td, "commit")
        title = request_changes(main, base, "private: ask bob | not in the PR")
        minutes_path = main / ".viva" / "minutes.md"
        assert not minutes_path.exists(), "nothing is written before the finish"
        (td / "pr.patch").write_text("")
        r = loop(main, "finish", env=env)
        assert r.returncode == 0, r.stderr
        wait_gone(main / ".viva")
        minutes = minutes_path.read_text()
        assert minutes.startswith("**viva session minutes** · o/r#7 · spec `spec.md` at `"), minutes
        assert_spec_ledger(minutes)
        diff = minutes.split("## Diff review · o/r#7")[1]
        assert "Signed off via viva review — 1 round, 1 hunk, 1 with comments." in diff, diff
        assert f"| 1 | {title} | changes | “private: ask bob \\| not in the PR” |" in diff, diff
        assert not record_path(main).exists(), "the record goes once the minutes are written"
        # Nothing posts without the confirm: the driver only prints the command.
        assert "pr comment" not in (td / "gh.log").read_text()
        command = f"gh pr comment 7 --repo o/r --body-file {minutes_path}"
        assert r.stdout.rstrip().splitlines()[-1] == command, r.stdout
        assert "explicit yes" in r.stdout and "whole body" in r.stdout, r.stdout
        # Disposable like the rest of `.viva/`: a stale one is never re-offered.
        r = loop(main, "start", "--doc", "spec.md", "--parse-only", env=env)
        assert r.returncode == 0, r.stderr
        assert not minutes_path.exists(), "the next start clears the minutes"
    print("  ok  test_a_commit_source_and_the_empty_finish")


def comment_minutes(edited: bool) -> tuple:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main, env, base = joined(td, "comment")
        if edited:
            serve_comment(td, SPEC.replace(ROW, ROW + "\n| 2 | Design | info | “added later” |"), "T2")
        approve_all(base, 1)
        assert poll_for(main / ".viva" / "review-r1.json")
        r = loop(main, "finish", env=env)
        assert r.returncode == 0, r.stderr
        wait_gone(main / ".viva")
        assert "api repos/o/r/issues/comments/1" in (td / "gh.log").read_text()
        assert not record_path(main).exists()
        return (main / ".viva" / "minutes.md").read_text()


def test_a_comment_source_and_an_edit_after_sign_off() -> None:
    """Row 7, comment source, on the all-approved finish; pre-mortem 6: an
    edit since sign-off is read as it stands and noted above the ledger."""
    minutes = comment_minutes(edited=False)
    assert minutes.startswith(f"**viva session minutes** · o/r#7 · spec {COMMENT_URL}\n"), minutes
    assert_spec_ledger(minutes)
    assert "edited after sign-off" not in minutes
    assert "Signed off via viva review — 1 round, 1 hunk, 0 with comments." in minutes, minutes
    minutes = comment_minutes(edited=True)
    note = minutes.index("edited after sign-off (T1 → T2)")
    assert note < minutes.index("## Revision History"), minutes
    assert "| 2 | Design | info | “added later” |" in spec_part(minutes), minutes
    print("  ok  test_a_comment_source_and_an_edit_after_sign_off")


def test_an_unreadable_source_refuses_before_the_record_goes() -> None:
    """Pre-mortem 6: a deleted comment fails the finish before `/complete`, so
    the record, the server, and the verdicts all stay; re-pointing the source
    at the committed spec lets the same finish through."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main, env, base = joined(td, "comment")
        approve_all(base, 1)
        assert poll_for(main / ".viva" / "review-r1.json")
        (td / "comment.json").unlink()
        before = record_path(main).read_bytes()
        r = loop(main, "finish", env=env)
        assert r.returncode != 0, r.stdout
        assert "did not read" in r.stderr and "--spec-source" in r.stderr, r.stderr
        assert record_path(main).read_bytes() == before
        assert not (main / ".viva" / "minutes.md").exists()
        assert [g["state"] for g in get(base, "/input")["session"]["gates"]] == \
            ["done", "done", "live"], "the diff gate is still live"

        r = loop(main, "session", "--spec-source", "commit:spec.md@HEAD", env=env)
        assert r.returncode == 0, r.stderr
        r = loop(main, "finish", env=env)
        assert r.returncode == 0, r.stderr
        wait_gone(main / ".viva")
        assert_spec_ledger((main / ".viva" / "minutes.md").read_text())
        assert not record_path(main).exists()
    print("  ok  test_an_unreadable_source_refuses_before_the_record_goes")


def test_a_standalone_review_writes_no_minutes() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main = repo(td)
        env = stub_gh(td)
        pr_patch(main, td, "a\nB\nc\n")
        r = loop(main, "start", "--target", "7", env=env)
        assert r.returncode == 0, r.stderr
        base = (main / ".viva" / "server.url").read_text().strip()
        approve_all(base, 1)
        assert poll_for(main / ".viva" / "review-r1.json")
        r = loop(main, "finish", env=env)
        assert r.returncode == 0, r.stderr
        wait_gone(main / ".viva")
        assert not (main / ".viva" / "minutes.md").exists()
        assert "gh pr comment" not in r.stdout
    print("  ok  test_a_standalone_review_writes_no_minutes")


def test_the_stamp_shows_the_body_and_posts_only_on_yes() -> None:
    """Pre-mortem 4 is prose the driver cannot enforce: the skill shows the
    whole body before asking, posts on an explicit yes, and gives the retry."""
    finish = re.split(r"^\*\*B5\. Finish\*\*", SKILL.read_text(), flags=re.M)[1]
    finish = " ".join(finish.split("\n## ")[0].split())
    assert "gh pr comment" in finish and "--body-file" in finish, finish
    for phrase in ("whole body", "explicit yes", "retry"):
        assert phrase in finish.lower(), phrase
    assert finish.index("whole body") < finish.index("explicit yes")
    principle = PRODUCT.read_text().split("6. **Local and keyless.**")[1].split("\n## ")[0]
    assert "minutes" in principle and "opt-in" in principle, principle
    print("  ok  test_the_stamp_shows_the_body_and_posts_only_on_yes")


def main() -> None:
    test_a_commit_source_and_the_empty_finish()
    test_a_comment_source_and_an_edit_after_sign_off()
    test_an_unreadable_source_refuses_before_the_record_goes()
    test_a_standalone_review_writes_no_minutes()
    test_the_stamp_shows_the_body_and_posts_only_on_yes()
    print("OK")


if __name__ == "__main__":
    main()
