#!/usr/bin/env python3
"""Integration test for scripts/install.sh — the brainstorming Q&A patcher.

The install is the one part of viva that reaches outside its own tree (it patches
superpowers' brainstorming skill), and it had no test. This exercises the locator
robustness (#64): it must find the upstream skill under ANY marketplace/org path,
not a hard-coded one; exclude its own patched copy; be idempotent; and fail loudly
when superpowers is absent.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install.sh"

UPSTREAM = """# Brainstorming (superpowers 2.0.0)

Steps:

1. **Understand the goal** — restate it
2. **Explore project context** — read the code
3. **Ask clarifying questions** — one at a time, understand purpose/constraints
4. **Propose a design** — write it up
"""


def make_home(tmp: Path, org: str = "some-other-marketplace", version: str = "2.0.0"):
    """A fake ~/.claude with viva installed and superpowers under an arbitrary
    org dir (deliberately NOT claude-plugins-official, to prove no hard-coding)."""
    (tmp / ".claude/skills/viva").mkdir(parents=True)
    src = tmp / f".claude/plugins/cache/{org}/superpowers/{version}/skills/brainstorming"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(UPSTREAM)
    return tmp / ".claude/skills/brainstorming/SKILL.md"


def run(home: Path):
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(["bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True)


def test_finds_superpowers_under_nonstandard_org_and_patches():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        target = make_home(tmp)
        r = run(tmp)
        assert r.returncode == 0, f"install failed:\n{r.stdout}\n{r.stderr}"
        text = target.read_text()
        assert "## Batch Q&A Phase" in text, "Q&A phase not injected"
        # Injected right after the clarifying-questions step, and the original
        # upstream content is preserved.
        assert text.index("Ask clarifying questions") < text.index("## Batch Q&A Phase")
        assert "Propose a design" in text
    print("  ok  test_finds_superpowers_under_nonstandard_org_and_patches")


def test_idempotent_rerun_does_not_double_patch():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        target = make_home(tmp)
        run(tmp)
        run(tmp)  # second run
        assert target.read_text().count("## Batch Q&A Phase") == 1, "double-patched"
    print("  ok  test_idempotent_rerun_does_not_double_patch")


def test_never_uses_its_own_patched_copy_as_source():
    # After one install the target exists and contains "superpowers" nowhere in
    # its path, so it must never be picked as the source even if superpowers is
    # later removed — the locator excludes $TARGET and requires a superpowers path.
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        target = make_home(tmp, version="2.0.0")
        run(tmp)
        first = target.read_text()
        # Remove upstream; a re-run must now FAIL rather than re-patch the target.
        import shutil
        shutil.rmtree(tmp / ".claude/plugins")
        r = run(tmp)
        assert r.returncode != 0, "should fail loudly once upstream is gone"
        assert target.read_text() == first, "target must be untouched on failure"
    print("  ok  test_never_uses_its_own_patched_copy_as_source")


def test_fails_loudly_when_superpowers_absent():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / ".claude/skills/viva").mkdir(parents=True)
        r = run(tmp)
        assert r.returncode == 1, f"expected exit 1, got {r.returncode}"
        assert "Could not find" in r.stderr and "superpowers" in r.stderr, r.stderr
        # Names where it looked, so the user can act.
        assert ".claude/plugins" in r.stderr
    print("  ok  test_fails_loudly_when_superpowers_absent")


def test_picks_newest_version():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        target = make_home(tmp, version="2.0.0")
        # Add an older and a newer version alongside.
        for v in ("1.9.0", "2.3.1"):
            s = tmp / f".claude/plugins/cache/some-other-marketplace/superpowers/{v}/skills/brainstorming"
            s.mkdir(parents=True)
            (s / "SKILL.md").write_text(UPSTREAM.replace("2.0.0", v))
        r = run(tmp)
        assert r.returncode == 0, r.stderr
        assert "2.3.1" in r.stdout, f"did not pick newest version:\n{r.stdout}"
    print("  ok  test_picks_newest_version")


def main():
    test_finds_superpowers_under_nonstandard_org_and_patches()
    test_idempotent_rerun_does_not_double_patch()
    test_never_uses_its_own_patched_copy_as_source()
    test_fails_loudly_when_superpowers_absent()
    test_picks_newest_version()
    print("OK (5 tests)")


if __name__ == "__main__":
    main()
