#!/usr/bin/env python3
"""Tests for scripts/install_hook.py — the drift-warning git hook (#143).

Installs a `post-commit` hook; idempotent; appends to an existing hook
without clobbering it. The last two tests run the INSTALLED hook for real,
in a scratch repo, with `$HOME` pointed at a fake plugin cache symlinked to
this checkout — the same `find ~/.claude/plugins/cache` resolve every skill
uses, exercised end to end rather than assumed.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install_hook.py"
MARKER = "# >>> viva drift hook (#143) >>>"


def _git(repo: Path, *args, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, env=env)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a@b.com")
    _git(repo, "config", "user.name", "test")


def _install(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)], cwd=repo,
                          capture_output=True, text=True)


def test_installs_an_executable_hook() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        _init_repo(repo)
        result = _install(repo)
        assert result.returncode == 0, result.stderr
        hook = repo / ".git" / "hooks" / "post-commit"
        assert hook.exists()
        text = hook.read_text()
        assert MARKER in text
        assert text.startswith("#!/bin/sh\n")
        assert stat.S_IMODE(hook.stat().st_mode) & 0o111, "hook must be executable"


def test_idempotent_on_a_second_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        _init_repo(repo)
        _install(repo)
        hook = repo / ".git" / "hooks" / "post-commit"
        before = hook.read_text()
        result = _install(repo)
        assert result.returncode == 0, result.stderr
        assert "already installed" in result.stdout, result.stdout
        assert hook.read_text() == before, "a second run must not duplicate the block"
        assert hook.read_text().count(MARKER) == 1


def test_appends_to_an_existing_hook_without_clobbering() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        _init_repo(repo)
        hooks_dir = repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook = hooks_dir / "post-commit"
        hook.write_text("#!/bin/sh\necho 'existing hook ran'\n")
        hook.chmod(0o755)
        result = _install(repo)
        assert result.returncode == 0, result.stderr
        assert "appended" in result.stdout, result.stdout
        text = hook.read_text()
        assert "existing hook ran" in text, "the existing hook body must survive"
        assert MARKER in text
        assert text.index("existing hook ran") < text.index(MARKER), \
            "the existing hook must run first, unmodified"


def _fake_home_with_plugin_cache(home: Path) -> None:
    """A fake `~/.claude/plugins/cache/.../viva/<ver>/` whose `server.py` and
    `scripts/` are the real ones — the `find ~/.claude/plugins/cache` resolve
    every skill (and the installed hook) uses, pointed at this checkout."""
    cache = home / ".claude" / "plugins" / "cache" / "jacquardlabs-marketplace" / "viva" / "1.0.0"
    cache.mkdir(parents=True)
    (cache / "server.py").symlink_to(ROOT / "server.py")
    (cache / "scripts").symlink_to(ROOT / "scripts")


def _run_hook_for_real(repo: Path, home: Path) -> subprocess.CompletedProcess:
    hook = repo / ".git" / "hooks" / "post-commit"
    env = dict(os.environ, HOME=str(home))
    return subprocess.run(["sh", str(hook)], cwd=repo, env=env,
                          capture_output=True, text=True)


def test_no_signed_docs_prints_nothing_and_exits_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        repo, home = t / "repo", t / "home"
        _init_repo(repo)
        _fake_home_with_plugin_cache(home)
        _install(repo)
        (repo / "readme.md").write_text("# Nothing signed here\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        result = _run_hook_for_real(repo, home)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "", f"expected silence, got: {result.stdout!r}"


def test_drift_on_a_signed_doc_warns_and_still_exits_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        repo, home = t / "repo", t / "home"
        _init_repo(repo)
        _fake_home_with_plugin_cache(home)
        _install(repo)
        (repo / "mod.py").write_text("def hello(): return 1\n")
        (repo / "spec.md").write_text(
            "# Spec\n\n## Alpha\n\nSee `mod.py`.\n\n"
            "---\n\n## Revision History\n\n"
            "Signed off via viva review — 1 round, 1 section, 0 with comments. "
            "2026-05-01\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "add spec and mod.py")

        # An unrelated commit must stay silent.
        (repo / "other.txt").write_text("x\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "unrelated")
        quiet = _run_hook_for_real(repo, home)
        assert quiet.returncode == 0, quiet.stderr
        assert quiet.stdout == "", f"expected silence on an unrelated commit: {quiet.stdout!r}"

        # A commit touching the cited file must warn, and still exit 0.
        (repo / "mod.py").write_text("def hello(): return 2\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "change mod.py")
        result = _run_hook_for_real(repo, home)
        assert result.returncode == 0, result.stderr
        assert "spec.md was signed off 2026-05-01" in result.stdout, result.stdout
        assert "references mod.py" in result.stdout, result.stdout
        assert "loop.py start --doc spec.md --recheck" in result.stdout, result.stdout


def main() -> None:
    tests = [
        test_installs_an_executable_hook,
        test_idempotent_on_a_second_run,
        test_appends_to_an_existing_hook_without_clobbering,
        test_no_signed_docs_prints_nothing_and_exits_zero,
        test_drift_on_a_signed_doc_warns_and_still_exits_zero,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
    if failed:
        sys.exit(f"\n{failed}/{len(tests)} tests failed")
    print(f"\nOK ({len(tests)} tests)")


if __name__ == "__main__":
    main()
