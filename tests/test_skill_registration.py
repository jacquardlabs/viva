#!/usr/bin/env python3
"""Structural guard for skill registration (#101).

Claude Code's skill discovery registers one skill per directory via that
directory's SKILL.md — a loose sibling .md with skill frontmatter never
registers, and a symlinked SKILL.md is fragile across install channels
(zip downloads, Windows checkouts can drop or mishandle it). This is a
filesystem-shape test, not a Claude Code discovery test — it can't invoke
the real plugin loader, so it checks the invariants discovery depends on
instead.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / ".claude" / "skills"
EXPECTED_SKILLS = {"viva", "viva-qa", "viva-diff"}


def _frontmatter_name(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    frontmatter = text[4:end]
    m = re.search(r"^name:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
    return m.group(1) if m else None


def test_expected_skill_set_registers():
    found = {}
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.is_file():
            continue
        name = _frontmatter_name(skill_md)
        if name is not None:
            found[d.name] = name

    missing = EXPECTED_SKILLS - set(found)
    assert not missing, f"missing skill directories: {sorted(missing)}"

    for dirname, name in found.items():
        if dirname in EXPECTED_SKILLS:
            assert name == dirname, (
                f"{dirname}/SKILL.md declares name {name!r}, expected {dirname!r}"
            )
    print("  ok  test_expected_skill_set_registers")


def test_skill_md_files_are_regular_files():
    for name in EXPECTED_SKILLS:
        skill_md = SKILLS_DIR / name / "SKILL.md"
        assert skill_md.is_file(), f"{skill_md} does not exist"
        assert not skill_md.is_symlink(), (
            f"{skill_md} is a symlink — a symlinked SKILL.md is fragile "
            "across install channels and can dangle, silently "
            "deregistering the skill"
        )
    print("  ok  test_skill_md_files_are_regular_files")


def test_no_loose_sibling_skill_files():
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file() or f.name == "SKILL.md" or f.suffix != ".md":
                continue
            if _frontmatter_name(f) is not None:
                raise AssertionError(
                    f"{f} carries skill frontmatter but is not named "
                    "SKILL.md — it will never register as a skill (one "
                    "skill per directory, via that directory's SKILL.md "
                    "only)"
                )
    print("  ok  test_no_loose_sibling_skill_files")


def test_no_root_skill_md():
    root_skill_md = ROOT / "SKILL.md"
    assert not root_skill_md.exists(), (
        f"{root_skill_md} exists — the manual git-clone install channel "
        "was dropped; the only SKILL.md lives under .claude/skills/viva/"
    )
    print("  ok  test_no_root_skill_md")


def main():
    test_expected_skill_set_registers()
    test_skill_md_files_are_regular_files()
    test_no_loose_sibling_skill_files()
    test_no_root_skill_md()
    print("OK (4 tests)")


if __name__ == "__main__":
    main()
