#!/usr/bin/env python3
"""Behavioral guard for the $VIVA_DIR resolve pipeline (#101 / #139).

Extracts the pipeline from all three source copies (both SKILL.md files
plus README.md), asserts they're identical, then runs it via a real
subprocess against constructed fixture directories. Covers #139's three
gaps: an unanchored `-path` glob, mtime-based tie-breaking instead of
version, and an incomplete fail-loud hint — plus resolving to nothing
(not some unrelated directory) on an empty search root.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RESOLVE_SOURCES = [
    ROOT / ".claude" / "skills" / "viva-review" / "SKILL.md",
    ROOT / ".claude" / "skills" / "viva-write" / "SKILL.md",
    ROOT / "README.md",
]

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


def test_all_copies_identical():
    blocks = {path: _extract_resolve_block(path) for path in RESOLVE_SOURCES}
    canonical = blocks[RESOLVE_SOURCES[0]]
    for path, block in blocks.items():
        assert block == canonical, (
            f"{path} resolve block differs from {RESOLVE_SOURCES[0]} — "
            "the hand-maintained copies have drifted apart"
        )
    print("  ok  test_all_copies_identical")


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
    block = _extract_resolve_block(RESOLVE_SOURCES[0])
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
    test_all_copies_identical()
    test_every_copy_names_both_install_steps()
    test_empty_cache_resolves_to_nothing()
    test_identical_mtimes_pick_highest_version()
    test_version_components_compare_numerically()
    test_glob_is_anchored_to_the_viva_marketplace()
    test_foreign_marketplace_alone_resolves_to_nothing()
    print("OK (7 tests)")


if __name__ == "__main__":
    main()
