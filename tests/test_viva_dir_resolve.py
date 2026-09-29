#!/usr/bin/env python3
"""Behavioral guard for the $VIVA_DIR resolve (#101 / #139).

The skills resolve from `${CLAUDE_SKILL_DIR}`, run here after the same
substitution Claude Code applies, with arguments. README keeps the cache
search for manual use; it runs against fixture caches covering #139's three
gaps. No SKILL.md may carry an argument placeholder Claude Code would eat.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKILLS_DIR = ROOT / ".claude" / "skills"
SKILL_SOURCES = [
    SKILLS_DIR / "viva-review" / "SKILL.md",
    SKILLS_DIR / "viva-write" / "SKILL.md",
]
README = ROOT / "README.md"
RESOLVE_SOURCES = SKILL_SOURCES + [README]

SKILL_RESOLVE_RE = re.compile(
    r"^# Claude Code substitutes CLAUDE_SKILL_DIR before this runs.*?\n"
    r"# and a project-skill checkout alike, so the scripts always match this text\.\n"
    r'VIVA_DIR=\$\(cd "\$\{CLAUDE_SKILL_DIR\}/\.\./\.\./\.\." 2>/dev/null && pwd\)\n'
    r'\[ -f "\$VIVA_DIR/scripts/loop\.py" \][^\n]*\n',
    re.M,
)

# Claude Code's argument placeholders (skills docs, "string substitutions"): `$N`
# and `$ARGUMENTS[N]`/`$ARGUMENTS`. Exactly one backslash escapes one.
PLACEHOLDER_RE = re.compile(r"(\\*)\$(\d|ARGUMENTS\b)")

# Starts at the rationale comment so a "simplified" `ls -t` reopens #139
# gap 2 as a match failure, not silently.
RESOLVE_RE = re.compile(
    r"# Highest version wins, not newest mtime.*?\n"
    r"# mtime, and `ls -t` then breaks the tie by name.*?\n"
    r"VIVA_DIR=\$\(find ~/\.claude/plugins/cache -maxdepth 4 "
    r'-path "\*/jacquardlabs-marketplace/viva/\*" -name server\.py 2>/dev/null \\\n'
    r".*?awk -F/ .*?split\(\$\(NF-1\).*?\n"
    r".*?\| sort -r \| head -1 \| cut -f2-\)\n"
    r"VIVA_DIR=\$\{VIVA_DIR%/server\.py\}",
    re.S,
)

# Both steps, in order. A user with no marketplace registered who copies only
# the install line hits a second, unexplained failure.
HINT_STEPS = [
    "/plugin marketplace add jacquardlabs/marketplace",
    "/plugin install viva@jacquardlabs-marketplace",
]


def _extract_resolve_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = RESOLVE_RE.search(text)
    assert m, f"{path}: resolve block not found — did the pipeline shape change?"
    return m.group(0)


def _extract_skill_resolve(path: Path) -> str:
    m = SKILL_RESOLVE_RE.search(path.read_text(encoding="utf-8"))
    assert m, f"{path}: ${{CLAUDE_SKILL_DIR}} resolve not found"
    return m.group(0)


def _unescaped_placeholders(text: str) -> list:
    return [(text.count("\n", 0, m.start()) + 1, m.group(0))
            for m in PLACEHOLDER_RE.finditer(text) if len(m.group(1)) != 1]


def _substitute(text: str, skill_dir: Path, args: list) -> str:
    """Claude Code's substitution as the docs state it: indexed args first
    (an index with no argument stays literal), then `${CLAUDE_SKILL_DIR}`."""
    def arg(m):
        if len(m.group(1)) == 1:
            return m.group(0)
        tok = m.group(2)
        if tok == "ARGUMENTS":
            return m.group(1) + " ".join(args)
        return m.group(1) + args[int(tok)] if int(tok) < len(args) else m.group(0)
    return PLACEHOLDER_RE.sub(arg, text).replace("${CLAUDE_SKILL_DIR}", str(skill_dir))


def test_no_skill_carries_an_argument_placeholder():
    """A `$0` in a resolve pipeline became the first argument, so
    `/viva-write prd` resolved `$VIVA_DIR` to nothing."""
    skills = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    assert skills, "no SKILL.md found"
    for path in skills:
        hits = _unescaped_placeholders(path.read_text(encoding="utf-8"))
        assert not hits, (
            f"{path}: unescaped argument placeholder(s) {hits} — Claude Code "
            "substitutes these with the invocation's arguments"
        )
    # The lint has teeth: the pre-fix awk line trips it, an escaped one doesn't.
    assert _unescaped_placeholders("v[3]+0, $0}'")
    assert _unescaped_placeholders("$ARGUMENTS[1] and \\\\$1")
    assert not _unescaped_placeholders("costs \\$1.00, $VIVA_DIR, $(NF-1)")
    print("  ok  test_no_skill_carries_an_argument_placeholder")


def test_skill_copies_identical():
    # Everything but the guard, whose `viva:`/`viva-write:` prefix differs by design.
    blocks = ["\n".join(_extract_skill_resolve(path).split("\n")[:3]) for path in SKILL_SOURCES]
    assert len(set(blocks)) == 1, f"the skills' resolve blocks drifted apart: {blocks}"
    print("  ok  test_skill_copies_identical")


def _run_skill_resolve(skill_md: Path, skill_dir: Path, args: list):
    """Substitute the whole file as loaded, then run its resolve bash block."""
    loaded = _substitute(skill_md.read_text(encoding="utf-8"), skill_dir, args)
    m = re.search(r"^Resolve the plugin once.*?```bash\n(.*?)```", loaded, re.S | re.M)
    assert m, f"{skill_md}: resolve bash block not found"
    block = m.group(1)
    return subprocess.run(
        ["bash", "-c", block + 'printf "%s" "$VIVA_DIR"\n'],
        capture_output=True, text=True, timeout=10,
    )


def test_skill_resolves_the_plugin_that_served_it():
    """With arguments, and with a newer version cached beside it: the scripts
    come from the plugin whose SKILL.md was loaded, never a cache search."""
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "jacquardlabs-marketplace" / "viva"
        served = cache / "2.14.0"
        for version in ("2.14.0", "9.0.0"):
            (cache / version / "scripts").mkdir(parents=True)
            (cache / version / "scripts" / "loop.py").write_text("")
        for skill_md, args in zip(SKILL_SOURCES, (["187"], ["prd", "docs/x.md"])):
            skill_dir = served / ".claude" / "skills" / skill_md.parent.name
            skill_dir.mkdir(parents=True)
            result = _run_skill_resolve(skill_md, skill_dir, args)
            assert result.returncode == 0, f"{skill_md.parent.name} {args}: {result.stdout}{result.stderr}"
            assert Path(result.stdout).resolve() == served.resolve(), (
                f"{skill_md.parent.name} {args} resolved {result.stdout!r}, expected {served}"
            )

    # Dogfooding: the same text, loaded as a project skill from this checkout.
    for skill_md in SKILL_SOURCES:
        result = _run_skill_resolve(skill_md, skill_md.parent, ["prd"])
        assert result.returncode == 0, result.stdout + result.stderr
        assert Path(result.stdout).resolve() == ROOT.resolve(), result.stdout
    print("  ok  test_skill_resolves_the_plugin_that_served_it")


def test_unsubstituted_skill_dir_fails_loud():
    """Where `${CLAUDE_SKILL_DIR}` reaches the shell unsubstituted, the guard
    prints the install hint rather than running some other copy."""
    for skill_md in SKILL_SOURCES:
        block = _extract_skill_resolve(skill_md)
        result = subprocess.run(
            ["bash", "-c", block], capture_output=True, text=True, timeout=10,
            env={k: v for k, v in os.environ.items() if k != "CLAUDE_SKILL_DIR"},
        )
        assert result.returncode == 1, result.stdout + result.stderr
        for step in HINT_STEPS:
            assert step in result.stdout, result.stdout
    print("  ok  test_unsubstituted_skill_dir_fails_loud")


def test_every_copy_names_both_install_steps():
    """The guard lines differ by design — `viva:` vs `viva-write:`, and
    README checks `server.py` where the skills check `scripts/loop.py`. Pin
    the shared recovery path instead of the whole line."""
    for path in RESOLVE_SOURCES:
        text = path.read_text(encoding="utf-8")
        guards = [line for line in text.splitlines()
                  if line.startswith('[ -f "$VIVA_DIR')]
        assert guards, f"{path}: no fail-loud guard on the resolve block"
        for guard in guards:
            for step in HINT_STEPS:
                assert step in guard, (
                    f"{path}: fail-loud hint omits {step!r} — a user with no "
                    f"marketplace registered hits a second failure:\n{guard}"
                )
    print("  ok  test_every_copy_names_both_install_steps")


def _run_resolve(search_root: Path) -> str:
    """Run the canonical resolve pipeline with its search root swapped to
    a temp directory, and return the resolved $VIVA_DIR (empty string if
    the pipeline produced nothing)."""
    block = _extract_resolve_block(README)
    script = block.replace("~/.claude/plugins/cache", str(search_root))
    script += '\nprintf "%s" "$VIVA_DIR"\n'
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"resolve script exited {result.returncode}: {result.stderr}"
    return result.stdout


def _install(cache_root: Path, marketplace: str, version: str, mtime=None) -> Path:
    """Write a fixture matching the real cache shape:
    <cache>/<marketplace>/viva/<version>/server.py."""
    version_dir = cache_root / marketplace / "viva" / version
    version_dir.mkdir(parents=True)
    server = version_dir / "server.py"
    server.write_text(f"# {marketplace} {version}\n", encoding="utf-8")
    if mtime is not None:
        os.utime(server, (mtime, mtime))
    return version_dir


def test_empty_cache_resolves_to_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        empty_root = Path(tmp) / "plugins-cache"
        empty_root.mkdir()
        resolved = _run_resolve(empty_root)
        assert resolved == "", (
            f"empty search root resolved to {resolved!r} instead of nothing — "
            "a resolve that returns some unrelated directory on a missing "
            "install is the silent-wrong-copy bug, not a fail-loud one"
        )
    print("  ok  test_empty_cache_resolves_to_nothing")


def test_identical_mtimes_pick_highest_version():
    """#139 gap 2. `ls -t` broke this tie by name, resolving 1.24.0 over
    2.0.2 — an older plugin than the one installed."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp) / "plugins-cache"
        stamp = time.time() - 3600
        older = _install(cache_root, "jacquardlabs-marketplace", "1.24.0", stamp)
        newer = _install(cache_root, "jacquardlabs-marketplace", "2.0.2", stamp)
        assert (older / "server.py").stat().st_mtime == (newer / "server.py").stat().st_mtime

        resolved = _run_resolve(cache_root)
        assert resolved == str(newer), (
            f"colliding mtimes resolved to {resolved!r}, expected {newer} — "
            "the tie-break fell back to name order"
        )
    print("  ok  test_identical_mtimes_pick_highest_version")


def test_version_components_compare_numerically():
    """1.9.0 sorts after 1.24.0 lexically. Zero-padding each component is
    what makes plain `sort -r` correct here, so pin it."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp) / "plugins-cache"
        now = time.time()
        # Stamp the *older* version newest, so an mtime-based resolve would
        # get this wrong too.
        _install(cache_root, "jacquardlabs-marketplace", "1.9.0", now)
        winner = _install(cache_root, "jacquardlabs-marketplace", "1.24.0", now - 500)

        resolved = _run_resolve(cache_root)
        assert resolved == str(winner), (
            f"expected 1.24.0 to beat 1.9.0, got {resolved!r} — the dotted "
            "components are being compared as text, not numbers"
        )
    print("  ok  test_version_components_compare_numerically")


def test_glob_is_anchored_to_the_viva_marketplace():
    """#139 gap 1. `*/viva/*` matched any cached plugin with a `viva/` path
    segment; a higher version under a foreign marketplace must lose to the
    real install, not win it."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp) / "plugins-cache"
        now = time.time()
        _install(cache_root, "someone-else-marketplace", "9.9.9", now)
        real = _install(cache_root, "jacquardlabs-marketplace", "1.0.0", now - 500)

        resolved = _run_resolve(cache_root)
        assert resolved == str(real), (
            f"resolved to {resolved!r}, expected {real} — the -path glob is "
            "not anchored to the viva marketplace, so a foreign plugin with a "
            "`viva/` path segment can win"
        )
    print("  ok  test_glob_is_anchored_to_the_viva_marketplace")


def test_foreign_marketplace_alone_resolves_to_nothing():
    """The anchor must fail closed: no jacquardlabs install means no resolve,
    so the guard prints the (now complete) install hint."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp) / "plugins-cache"
        _install(cache_root, "someone-else-marketplace", "9.9.9")
        resolved = _run_resolve(cache_root)
        assert resolved == "", (
            f"a foreign marketplace alone resolved to {resolved!r} — the "
            "anchor must fail closed, not fall back to whatever matches"
        )
    print("  ok  test_foreign_marketplace_alone_resolves_to_nothing")


def main():
    test_no_skill_carries_an_argument_placeholder()
    test_skill_copies_identical()
    test_skill_resolves_the_plugin_that_served_it()
    test_unsubstituted_skill_dir_fails_loud()
    test_every_copy_names_both_install_steps()
    test_empty_cache_resolves_to_nothing()
    test_identical_mtimes_pick_highest_version()
    test_version_components_compare_numerically()
    test_glob_is_anchored_to_the_viva_marketplace()
    test_foreign_marketplace_alone_resolves_to_nothing()
    print("OK (10 tests)")


if __name__ == "__main__":
    main()
