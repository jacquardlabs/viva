#!/usr/bin/env python3
"""Attached-context resolution for `/viva-write` intake (#170).

`context_refs.py` classifies a pile of attachments and bounds what a directory
ref expands to. Its two load-bearing properties are tested here:

  1. **It never fetches.** An issue entry carries an argv LIST built from a
     `\\d+` number and a strictly-matched `owner/repo` — so no ref can carry a
     shell metacharacter into a `gh` invocation, and no test run needs network
     or a credential (#165 guard 3, keyless).
  2. **It never truncates silently.** Everything a cap excludes lands in
     `dropped[]` with its reason. A manifest that quietly stopped reading looks
     exactly like a repo with nothing else in it, and the draft built from it
     would be missing facts nobody knows are missing.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "context_refs.py"


def run(args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT)] + [str(a) for a in args],
                          capture_output=True, text=True, cwd=str(cwd) if cwd else None)


def resolve(args, cwd=None) -> dict:
    proc = run(args, cwd)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _repo() -> Path:
    """A throwaway tree with text, binary, dotted, and denied content."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "PRODUCT.md").write_text("# Product\n" + "x" * 100)
    (tmp / "docs").mkdir()
    (tmp / "docs" / "a.md").write_text("a" * 500)
    (tmp / "docs" / "b.md").write_text("b" * 500)
    (tmp / "docs" / "c.md").write_text("c" * 500)
    (tmp / "docs" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    (tmp / "docs" / ".hidden.md").write_text("secret")
    (tmp / "docs" / "node_modules").mkdir()
    (tmp / "docs" / "node_modules" / "dep.js").write_text("module.exports={}")
    (tmp / "docs" / ".cache").mkdir()
    (tmp / "docs" / ".cache" / "junk.md").write_text("junk")
    return tmp


# ── classification ───────────────────────────────────────────────────────────
def test_issue_refs_classify_to_a_gh_argv():
    got = resolve(["#170", "jacquardlabs/viva#12"], cwd=ROOT)
    bare, scoped = got["refs"]
    assert bare["kind"] == "issue" and bare["number"] == 170 and bare["repo"] is None
    assert bare["fetch"][:4] == ["gh", "issue", "view", "170"], bare
    assert "--repo" not in bare["fetch"], bare
    assert scoped["repo"] == "jacquardlabs/viva", scoped
    assert scoped["fetch"][-2:] == ["--repo", "jacquardlabs/viva"], scoped
    print("  ok  test_issue_refs_classify_to_a_gh_argv")


def test_github_urls_classify_as_issue_or_pr_not_url():
    got = resolve(["https://github.com/jacquardlabs/viva/issues/170",
                   "https://github.com/jacquardlabs/viva/pull/187",
                   "https://example.com/spec"], cwd=ROOT)
    issue, pr, url = got["refs"]
    assert issue["kind"] == "issue" and issue["number"] == 170, issue
    # A PR fetches with `gh pr`, not `gh issue` — the two commands return
    # different bodies and only one of them carries the diff's description.
    assert pr["kind"] == "pr" and pr["fetch"][:4] == ["gh", "pr", "view", "187"], pr
    assert url["kind"] == "url" and url["url"] == "https://example.com/spec", url
    print("  ok  test_github_urls_classify_as_issue_or_pr_not_url")


def test_fetch_is_an_argv_list_no_ref_can_inject():
    """The injection guard, stated as the property it protects: `fetch` is a
    list, and every element of it is either a literal or a value that matched
    `\\d+` / `owner/repo`. A ref carrying a metacharacter cannot resolve to an
    issue at all — it fails classification and dies."""
    got = resolve(["#170"], cwd=ROOT)
    fetch = got["refs"][0]["fetch"]
    assert isinstance(fetch, list) and all(isinstance(a, str) for a in fetch), fetch
    assert not any(c in a for a in fetch for c in ";|&$`\n><"), fetch
    for hostile in ("#170; rm -rf /", "a;b/c#1", "#170`id`", "$(id)#1"):
        proc = run([hostile], cwd=ROOT)
        assert proc.returncode != 0, f"{hostile!r} must not resolve: {proc.stdout}"
        assert "names nothing" in proc.stderr, proc.stderr
    print("  ok  test_fetch_is_an_argv_list_no_ref_can_inject")


def test_a_ref_that_names_nothing_is_loud():
    proc = run(["does/not/exist.md"], cwd=ROOT)
    assert proc.returncode != 0
    assert "names nothing" in proc.stderr, proc.stderr
    print("  ok  test_a_ref_that_names_nothing_is_loud")


# ── directory expansion ──────────────────────────────────────────────────────
def test_dir_expansion_skips_binary_dotted_and_denied():
    tmp = _repo()
    got = resolve(["docs"], cwd=tmp)
    entry = got["refs"][0]
    assert entry["kind"] == "dir"
    paths = [f["path"] for f in entry["files"]]
    assert paths == ["docs/a.md", "docs/b.md", "docs/c.md"], paths
    # The binary file is reported, not vanished — a caller who attached a
    # directory of screenshots must learn that is what happened.
    assert {"ref": "docs", "path": "docs/logo.png", "reason": "not text"} \
        in got["dropped"], got["dropped"]
    # Dotted descendants and DENY_DIRS are skipped without a dropped entry:
    # they are noise, and reporting them would bury the caps that matter.
    assert not any("node_modules" in d["path"] or ".hidden" in d["path"]
                   or ".cache" in d["path"] for d in got["dropped"]), got["dropped"]
    print("  ok  test_dir_expansion_skips_binary_dotted_and_denied")


def test_dir_expansion_is_deterministic():
    tmp = _repo()
    first, second = resolve(["docs"], cwd=tmp), resolve(["docs"], cwd=tmp)
    assert first == second, (first, second)
    print("  ok  test_dir_expansion_is_deterministic")


def test_explicitly_named_dot_dir_is_walked():
    """The dot rule applies to DESCENDANTS only. A caller who typed
    `.github/workflows` meant it, and refusing the ref they named would be this
    filter overruling the attachment rather than bounding it."""
    tmp = _repo()
    got = resolve([".cache"], cwd=tmp / "docs")
    assert [f["path"] for f in got["refs"][0]["files"]] == [".cache/junk.md"], got
    print("  ok  test_explicitly_named_dot_dir_is_walked")


# ── the budget ───────────────────────────────────────────────────────────────
def test_file_cap_drops_are_reported_not_silent():
    tmp = _repo()
    got = resolve(["docs", "--max-files", "2"], cwd=tmp)
    assert [f["path"] for f in got["refs"][0]["files"]] == ["docs/a.md", "docs/b.md"]
    capped = [d for d in got["dropped"] if d["reason"] == "file cap"]
    assert [d["path"] for d in capped] == ["docs/c.md"], got["dropped"]
    assert got["budget"]["files"] == 2, got["budget"]
    print("  ok  test_file_cap_drops_are_reported_not_silent")


def test_byte_cap_drops_are_reported_not_silent():
    tmp = _repo()
    got = resolve(["docs", "--max-bytes", "1100"], cwd=tmp)
    assert [f["path"] for f in got["refs"][0]["files"]] == ["docs/a.md", "docs/b.md"]
    capped = [d for d in got["dropped"] if d["reason"] == "byte cap"]
    assert [d["path"] for d in capped] == ["docs/c.md"], got["dropped"]
    assert got["budget"]["bytes"] == 1000, got["budget"]
    print("  ok  test_byte_cap_drops_are_reported_not_silent")


def test_explicit_file_is_never_dropped_but_still_spends():
    """An explicit file ref is a deliberate choice, so it is included even past
    the caps — but its bytes still count, so a directory beside it expands into
    what is left rather than getting a fresh allowance."""
    tmp = _repo()
    got = resolve(["PRODUCT.md", "docs", "--max-bytes", "700"], cwd=tmp)
    named, expanded = got["refs"]
    assert named["kind"] == "file" and named["path"] == "PRODUCT.md", named
    # 110 bytes of PRODUCT.md are already spent, so only one 500-byte doc fits.
    assert [f["path"] for f in expanded["files"]] == ["docs/a.md"], expanded
    assert {d["path"] for d in got["dropped"] if d["reason"] == "byte cap"} \
        == {"docs/b.md", "docs/c.md"}, got["dropped"]
    print("  ok  test_explicit_file_is_never_dropped_but_still_spends")


def test_budget_over_is_flagged_when_explicit_files_exceed_it():
    tmp = _repo()
    got = resolve(["PRODUCT.md", "--max-bytes", "10"], cwd=tmp)
    assert got["refs"][0]["path"] == "PRODUCT.md", got
    assert got["budget"]["over"] is True, got["budget"]
    print("  ok  test_budget_over_is_flagged_when_explicit_files_exceed_it")


def test_caps_must_be_positive():
    proc = run(["PRODUCT.md", "--max-files", "0"], cwd=_repo())
    assert proc.returncode != 0 and "at least 1" in proc.stderr, proc.stderr
    print("  ok  test_caps_must_be_positive")


def test_binary_file_named_explicitly_is_kept_and_marked():
    """An image attached by name is context for the draft, not noise — it is
    kept, and `text: false` is how the skill knows to `Read` it as an image."""
    tmp = _repo()
    got = resolve(["docs/logo.png"], cwd=tmp)
    entry = got["refs"][0]
    assert entry["kind"] == "file" and entry["text"] is False, entry
    assert got["dropped"] == [], got["dropped"]
    print("  ok  test_binary_file_named_explicitly_is_kept_and_marked")


def main() -> None:
    test_issue_refs_classify_to_a_gh_argv()
    test_github_urls_classify_as_issue_or_pr_not_url()
    test_fetch_is_an_argv_list_no_ref_can_inject()
    test_a_ref_that_names_nothing_is_loud()
    test_dir_expansion_skips_binary_dotted_and_denied()
    test_dir_expansion_is_deterministic()
    test_explicitly_named_dot_dir_is_walked()
    test_file_cap_drops_are_reported_not_silent()
    test_byte_cap_drops_are_reported_not_silent()
    test_explicit_file_is_never_dropped_but_still_spends()
    test_budget_over_is_flagged_when_explicit_files_exceed_it()
    test_caps_must_be_positive()
    test_binary_file_named_explicitly_is_kept_and_marked()
    print("OK (13 tests)")


if __name__ == "__main__":
    main()
