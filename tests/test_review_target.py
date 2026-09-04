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


def test_branch_b_runs_the_capture_this_script_prints():
    """The prose end of the dispatch. `capture` is only useful if the skill
    actually runs it — branch B carries no execution test of its own (it drives
    itself; #179), so this pins the two commands the classifier can emit against
    the two the skill shows, both at launch and at the re-diff.

    Both call sites matter separately: a re-diff that hardcoded `git diff` would
    silently review the working tree on round 2 of a PR review, which is the
    wrong artifact and looks like a shrinking diff rather than an error.
    """
    branch_b = _branch_b()
    capture_pr = " ".join(classify(["187"], cwd=_repo())["capture"])
    capture_ref = " ".join(classify(["HEAD~1"], cwd=_repo())["capture"][:2])
    assert capture_pr == "gh pr diff 187", capture_pr
    assert capture_ref == "git diff", capture_ref

    launch, redoff = branch_b.index("**B1."), branch_b.index("**B4.")
    for where, name in ((branch_b[launch:redoff], "B1"), (branch_b[redoff:], "B4")):
        assert "gh pr diff" in where, f"{name} must show the PR capture"
        assert "git diff" in where, f"{name} must show the ref capture"
        assert "> .viva/diff.patch" in where, f"{name} must write the patch"
    assert "SAME capture argv as B1" in branch_b[redoff:], (
        "B4 must re-run B1's capture rather than naming one form — a hardcoded "
        "`git diff` reviews the working tree on round 2 of a PR review")
    print("  ok  test_branch_b_runs_the_capture_this_script_prints")


def test_branch_b_uses_the_parser_and_mode_the_driver_lacks():
    """Why branch B drives itself, asserted rather than asserted-in-prose:
    `parse_diff.py` and `--mode diff` are the two things `loop.py` has no path
    to (`cmd_start` runs `parse_sections.py`, `cmd_arm` launches
    `--mode review`). If branch B ever stops needing both, #179 is unblocked and
    this test is the reminder."""
    branch_b = _branch_b()
    assert "parse_diff.py" in branch_b and "--mode diff" in branch_b, branch_b[:400]
    loop = (ROOT / "scripts" / "loop.py").read_text()
    assert "parse_diff" not in loop, \
        "loop.py learned parse_diff — branch B can move onto the driver (#179)"
    assert '_launch_server(viva, "review"' in loop, \
        "loop.py no longer hardcodes --mode review; re-check branch B's exemption"
    print("  ok  test_branch_b_uses_the_parser_and_mode_the_driver_lacks")


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


def test_branch_b_writes_summaries_before_it_launches_the_server():
    """#188. Branch B drives itself, so an agent runs its bash blocks in the
    order the page prints them. The server loads its round once and replaces it
    only from `POST /next-round`, so a launch that precedes the summary write
    serves the summary-less round for all of round 1 — 52 hunks titled
    `server.py hunk N`, the exact complaint #188 was filed about, with no error
    on any surface.

    Asserted on block order rather than on the prose that states the rule twice:
    a correct instruction under a mis-placed fence still boots the wrong round.
    """
    blocks = re.findall(r"```bash\n(.*?)```", _branch_b(), re.S)
    write = [i for i, b in enumerate(blocks) if 's["summary"]' in b]
    launch = [i for i, b in enumerate(blocks) if "--mode diff" in b]
    assert write, "branch B has no bash block that writes a section summary"
    assert launch, "branch B has no bash block that launches `--mode diff`"
    assert write[0] < launch[0], (
        "branch B prints its `--mode diff` launch (block %d) before the summary "
        "write (block %d) — an agent running the blocks in order arms a round "
        "the summaries never reach" % (launch[0], write[0])
    )
    print("  ok  test_branch_b_writes_summaries_before_it_launches_the_server")


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
    test_branch_b_runs_the_capture_this_script_prints()
    test_branch_b_uses_the_parser_and_mode_the_driver_lacks()
    test_branch_b_routes_info_away_from_threads_it_does_not_have()
    test_branch_b_writes_summaries_before_it_launches_the_server()
    print("OK (15 tests)")


if __name__ == "__main__":
    main()
