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
# Two skills, split by intent: am I making a thing, or judging one (#170). The
# mechanism-named trio (`viva`, `viva-qa`, `viva-diff`) is gone — `viva-review`
# absorbed doc and hunk review behind one target dispatch, and the Q&A gate is a
# reference contract (`references/qa.md`) rather than a skill you must find.
EXPECTED_SKILLS = {"viva-write", "viva-review"}
# Names that must NOT come back as directories: a stale copy alongside the new
# set would register a second skill for the same job, and the older one can win.
RETIRED_SKILLS = {"viva", "viva-qa", "viva-diff"}


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
        "was dropped; SKILL.md lives under .claude/skills/<name>/ only"
    )
    print("  ok  test_no_root_skill_md")


def test_retired_skills_are_gone():
    for name in sorted(RETIRED_SKILLS):
        assert not (SKILLS_DIR / name).exists(), (
            f"{SKILLS_DIR / name} is back — /{name} was retired into "
            f"{sorted(EXPECTED_SKILLS)}, and a surviving directory registers a "
            "second skill for the same job"
        )
    print("  ok  test_retired_skills_are_gone")


def test_shared_references_live_at_the_plugin_root():
    """Both skills read them and `loop.py` prints their paths, so they belong to
    the driver, not to either skill — `/viva-write` needing `producers.md` must
    not mean reaching into `/viva-review`'s directory."""
    references = ROOT / "references"
    expected = {"producers.md", "open-notes.md", "preferences.md", "qa.md"}
    found = {p.name for p in references.glob("*.md")}
    assert expected <= found, f"missing shared references: {sorted(expected - found)}"
    for d in sorted(SKILLS_DIR.iterdir()):
        if d.is_dir():
            assert not (d / "references").exists(), (
                f"{d / 'references'} exists — the shared set moved to "
                f"{references}; a second copy is drift waiting to happen"
            )
    print("  ok  test_shared_references_live_at_the_plugin_root")


def main():
    test_expected_skill_set_registers()
    test_skill_md_files_are_regular_files()
    test_no_loose_sibling_skill_files()
    test_no_root_skill_md()
    test_retired_skills_are_gone()
    test_shared_references_live_at_the_plugin_root()
    print("OK (6 tests)")


if __name__ == "__main__":
    main()
