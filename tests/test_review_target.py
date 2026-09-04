#!/usr/bin/env python3
"""Review-target dispatch for the merged `/viva-review` (#170).

One skill now covers both review checkpoints, so what it reviews is decided by
one classification. Two properties carry that:

  1. **Precedence is filesystem first, then shape.** A repo holding a file named
     `187` means that file, not the pull request — a target the caller can see
     in `ls` must never be silently reinterpreted. The cost is that a branch
     named `42` is unreachable by derivation, which is what `--kind` exists for.
  2. **It runs nothing.** `capture` is the argv a caller executes, as a list,
     built only from a `\\d+` number or a target that passed `REF_RE`. No `git`,
     no `gh`, no network, no repo — the same constraint that keeps
     `context_refs.py` testable.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "review_target.py"


def run(args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT)] + [str(a) for a in args],
                          capture_output=True, text=True,
                          cwd=str(cwd) if cwd else None)


def classify(args, cwd=None) -> dict:
    proc = run(args, cwd)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _repo() -> Path:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "spec.md").write_text("# Spec\n")
    (tmp / "notes.markdown").write_text("# Notes\n")
    (tmp / "server.py").write_text("x = 1\n")
    (tmp / "docs").mkdir()
    return tmp


# ── derivation ───────────────────────────────────────────────────────────────
def test_markdown_path_is_a_doc():
    tmp = _repo()
    for name in ("spec.md", "notes.markdown"):
        got = classify([name], cwd=tmp)
        assert got == {"kind": "doc", "doc": name, "label": name}, got
        # A doc has no `capture`: nothing is executed to produce it.
        assert "capture" not in got, got
    print("  ok  test_markdown_path_is_a_doc")


def test_pr_forms_all_reach_gh_pr_diff():
    tmp = _repo()
    for target in ("187", "#187"):
        got = classify([target], cwd=tmp)
        assert got["kind"] == "pr" and got["number"] == 187, got
        assert got["capture"] == ["gh", "pr", "diff", "187"], got
        assert got["repo"] is None, got
    url = classify(["https://github.com/jacquardlabs/viva/pull/187"], cwd=tmp)
    assert url["repo"] == "jacquardlabs/viva", url
    assert url["capture"][-2:] == ["--repo", "jacquardlabs/viva"], url
    assert url["label"] == "PR #187 (jacquardlabs/viva)", url
    print("  ok  test_pr_forms_all_reach_gh_pr_diff")


def test_refs_and_ranges_reach_git_diff():
    tmp = _repo()
    for target in ("HEAD~3..HEAD", "main", "feature/x", "a1b2c3d", "HEAD^",
                   "main@{upstream}"):
        got = classify([target], cwd=tmp)
        assert got["kind"] == "ref" and got["ref"] == target, got
        assert got["capture"] == ["git", "diff", target], got
    print("  ok  test_refs_and_ranges_reach_git_diff")


def test_no_target_is_the_working_tree():
    got = classify([], cwd=_repo())
    assert got == {"kind": "worktree", "label": "working tree",
                   "capture": ["git", "diff"]}, got
    print("  ok  test_no_target_is_the_working_tree")


# ── precedence ───────────────────────────────────────────────────────────────
def test_a_file_named_187_beats_the_pr_reading():
    """Filesystem first. A target the caller can see in `ls` must not be
    silently reinterpreted as a pull request."""
    tmp = _repo()
    (tmp / "187").write_text("not markdown")
    proc = run(["187"], cwd=tmp)
    # It resolves as a FILE — and, not being markdown, is refused rather than
    # falling through to `gh pr diff 187`. Falling through is the bug.
    assert proc.returncode != 0, proc.stdout
    assert "not markdown" in proc.stderr, proc.stderr
    (tmp / "187").unlink()
    (tmp / "187.md").write_text("# doc\n")
    assert classify(["187.md"], cwd=tmp)["kind"] == "doc"
    # With nothing at that path, the PR reading is restored.
    assert classify(["187"], cwd=tmp)["kind"] == "pr"
    print("  ok  test_a_file_named_187_beats_the_pr_reading")


def test_kind_overrides_the_filesystem():
    """The documented escape: `--kind pr|ref` skips the path check that would
    otherwise shadow a branch named for a number."""
    tmp = _repo()
    (tmp / "187").write_text("not markdown")
    got = classify(["187", "--kind", "pr"], cwd=tmp)
    assert got["kind"] == "pr" and got["capture"] == ["gh", "pr", "diff", "187"], got
    got = classify(["187", "--kind", "ref"], cwd=tmp)
    assert got["kind"] == "ref" and got["capture"] == ["git", "diff", "187"], got
    print("  ok  test_kind_overrides_the_filesystem")


def test_kind_disagreeing_with_its_target_is_refused():
    tmp = _repo()
    for args, needle in (
        (["HEAD~1", "--kind", "pr"], "not one"),
        (["spec.md", "--kind", "pr"], "not one"),
        (["187", "--kind", "doc"], "not one"),
        (["--kind", "ref"], "needs a target"),
        (["spec.md", "--kind", "worktree"], "takes no target"),
    ):
        proc = run(args, cwd=tmp)
        assert proc.returncode != 0, (args, proc.stdout)
        assert needle in proc.stderr, (args, proc.stderr)
    print("  ok  test_kind_disagreeing_with_its_target_is_refused")


# ── loud failures ────────────────────────────────────────────────────────────
def test_an_existing_non_markdown_file_is_refused_not_reinterpreted():
    tmp = _repo()
    proc = run(["server.py"], cwd=tmp)
    assert proc.returncode != 0 and "not markdown" in proc.stderr, proc.stderr
    print("  ok  test_an_existing_non_markdown_file_is_refused_not_reinterpreted")


def test_a_directory_is_refused():
    proc = run(["docs"], cwd=_repo())
    assert proc.returncode != 0 and "is a directory" in proc.stderr, proc.stderr
    print("  ok  test_a_directory_is_refused")


def test_capture_is_an_argv_list_no_target_can_inject():
    tmp = _repo()
    for hostile in ("main; rm -rf /", "$(id)", "a|b", "`id`", "x>y", "a b"):
        proc = run([hostile], cwd=tmp)
        assert proc.returncode != 0, f"{hostile!r} must not resolve: {proc.stdout}"
        assert "not a usable git ref" in proc.stderr, (hostile, proc.stderr)
    for good in ("187", "HEAD~1", None):
        got = classify([good] if good else [], cwd=tmp)
        capture = got["capture"]
        assert isinstance(capture, list) and all(isinstance(a, str) for a in capture)
        assert not any(c in a for a in capture for c in ";|&$`\n><"), capture
    print("  ok  test_capture_is_an_argv_list_no_target_can_inject")


SKILL = ROOT / ".claude" / "skills" / "viva-review" / "SKILL.md"


def _branch_b() -> str:
    text = SKILL.read_text()
    return text[text.index("## B. Diff review"):text.index("## Scope")]


def test_skill_dispatch_table_covers_every_kind():
    """The classifier's `KINDS` and the skill's two branches must stay in step:
    a fifth kind added here with no route in SKILL.md dispatches nowhere."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import review_target  # noqa: E402

    skill = SKILL.read_text()
    assert set(review_target.KINDS) == {"doc", "pr", "ref", "worktree"}, \
        review_target.KINDS
    assert "## A. Doc review (`kind: doc`)" in skill, "branch A must route `doc`"
    assert "## B. Diff review (`kind: pr | ref | worktree`)" in skill, \
        "branch B must route every non-doc kind"
    print("  ok  test_skill_dispatch_table_covers_every_kind")


def test_the_driver_runs_the_capture_this_script_prints():
    """The other end of the dispatch. `capture` is only useful if something
    runs it — and since #179 that is `loop.py`, not the skill: `start` saves
    this script's record to `target.json` and runs `record["capture"]`, and
    `rearm`/`finish` re-run the SAME record rather than naming a form. (A
    re-capture that hardcoded `git diff` would silently review the working
    tree on round 2 of a PR review — the wrong artifact, looking like a
    shrinking diff rather than an error.)

    Branch B's prose therefore shows no capture bash at all; the execution half
    is `tests/test_server_orchestration.py`'s diff checks.
    """
    capture_pr = " ".join(classify(["187"], cwd=_repo())["capture"])
    capture_ref = " ".join(classify(["HEAD~1"], cwd=_repo())["capture"][:2])
    assert capture_pr == "gh pr diff 187", capture_pr
    assert capture_ref == "git diff", capture_ref

    loop = (ROOT / "scripts" / "loop.py").read_text()
    assert 'record.get("capture")' in loop, "loop.py must run the record's capture"
    assert '"target.json"' in loop, "loop.py must persist the record for the re-capture"
    assert "_capture(record, viva / \"diff.patch\", cwd)" in loop, \
        "every capture runs from the recorded cwd"
    for bash in re.findall(r"```bash\n(.*?)```", _branch_b(), re.S):
        for needle in ("gh pr diff", "git diff", "> .viva/diff.patch", "parse_diff.py"):
            assert needle not in bash, \
                f"branch B still carries capture bash ({needle!r}) — loop.py owns it:\n{bash}"
    print("  ok  test_the_driver_runs_the_capture_this_script_prints")


def test_the_driver_knows_the_diff_parser_and_mode():
    """The tripwire that used to say why branch B drove itself, inverted:
    `parse_diff.py` and `--mode diff` were the two things `loop.py` had no path
    to. Now it parses a diff with the one and launches with a mode read off the
    round file — never a hardcoded `review` — so the prose names neither."""
    loop = (ROOT / "scripts" / "loop.py").read_text()
    assert "parse_diff.py" in loop, "loop.py must parse a diff round"
    assert '_launch_server(viva, "review"' not in loop, \
        "arm must not hardcode the launch mode"
    assert "_launch_server(viva, mode, inp, out)" in loop, \
        "arm launches with the mode the round file carries"
    # Prose may still name the parser (B3's `info` exception rests on what
    # `parse_diff.py` lacks); no bash block may run it or launch the mode.
    for bash in re.findall(r"```bash\n(.*?)```", _branch_b(), re.S):
        assert "--mode diff" not in bash and "parse_diff.py" not in bash, \
            "branch B must run neither the parser nor the mode — the driver owns both:\n" + bash
    print("  ok  test_the_driver_knows_the_diff_parser_and_mode")


def test_branch_b_routes_info_away_from_threads_it_does_not_have():
    """#103. `parse_diff.py` takes no `--open-notes`, so a diff round carries no
    threads — there is nothing to answer into and no `--response` to record one
    against. The 2.0 merge gave both branches one shared verdict section whose
    `info` rule said "answer it in the thread", generalizing a doc-mode-only
    instruction onto hunks; an agent following it answered into a void on every
    `info` comment in a PR review.

    Pinned against the parser's ACTUAL flags rather than against prose alone, so
    the day `parse_diff.py` grows open-notes support this fails and someone
    re-reads the exception instead of leaving it stale in the other direction.
    """
    help_text = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "parse_diff.py"), "--help"],
        capture_output=True, text=True).stdout
    assert "--open-notes" not in help_text, (
        "parse_diff.py now takes --open-notes — branch B may have threads after "
        "all, so re-read its `info` exception:\n" + help_text
    )

    branch_b = _branch_b()
    assert "no threads here" in branch_b, \
        "branch B must state that it carries no threads"
    assert "chat conversation" in branch_b, \
        "branch B must name where an `info` is actually answered"
    assert "answer it in the thread" not in branch_b, (
        "branch B must not route an `info` into a thread — that is the doc-mode "
        "instruction this exception exists to override"
    )
    # The shared rule must hand off rather than assert one answer route for both.
    shared = SKILL.read_text()[:SKILL.read_text().index("## A. Doc review")]
    assert "differs by branch" in shared, (
        "the shared verdict section must say where an `info` is answered depends "
        "on the branch, instead of stating one route as universal"
    )
    print("  ok  test_branch_b_routes_info_away_from_threads_it_does_not_have")


def test_branch_b_summarizes_at_the_seam_before_it_arms():
    """#188. The server loads its round once and replaces it only from
    `POST /next-round`, so a summary written after the launch serves a
    summary-less round for all of round 1 — 52 hunks titled `server.py hunk N`,
    the exact complaint #188 was filed about. The driver holds the seam
    (`start`/`rearm` stop above the threshold; `summarize` refuses an armed
    round — `tests/test_server_orchestration.py` executes both), and the prose
    must present the verbs in that order: an agent runs the blocks as printed.
    """
    bash = "\n".join(re.findall(r"```bash\n(.*?)```", _branch_b(), re.S))
    assert 'loop.py" summarize' in bash, "branch B has no bash that runs `loop.py summarize`"
    assert 'loop.py" arm' in bash, "branch B has no bash that runs `loop.py arm`"
    assert bash.index('loop.py" summarize') < bash.index('loop.py" arm'), (
        "branch B prints `arm` before `summarize` — an agent running the "
        "blocks in order arms a round the summaries never reach"
    )
    print("  ok  test_branch_b_summarizes_at_the_seam_before_it_arms")


def main() -> None:
    test_markdown_path_is_a_doc()
    test_pr_forms_all_reach_gh_pr_diff()
    test_refs_and_ranges_reach_git_diff()
    test_no_target_is_the_working_tree()
    test_a_file_named_187_beats_the_pr_reading()
    test_kind_overrides_the_filesystem()
    test_kind_disagreeing_with_its_target_is_refused()
    test_an_existing_non_markdown_file_is_refused_not_reinterpreted()
    test_a_directory_is_refused()
    test_capture_is_an_argv_list_no_target_can_inject()
    test_skill_dispatch_table_covers_every_kind()
    test_the_driver_runs_the_capture_this_script_prints()
    test_the_driver_knows_the_diff_parser_and_mode()
    test_branch_b_routes_info_away_from_threads_it_does_not_have()
    test_branch_b_summarizes_at_the_seam_before_it_arms()
    print("OK (15 tests)")


if __name__ == "__main__":
    main()
