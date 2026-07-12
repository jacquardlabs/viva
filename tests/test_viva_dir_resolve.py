#!/usr/bin/env python3
"""Behavioral guard for the $VIVA_DIR resolve pipeline (#101 audit follow-up).

`tests/test_skill_registration.py` checks the file-layout invariants
discovery depends on; it never executes the bash resolve block itself.
This file does — it extracts the two-line pipeline from all four source
copies (three SKILL.md files plus README.md), asserts they're identical
(so the four hand-maintained copies can't silently drift), then runs the
extracted pipeline via a real subprocess against constructed fixture
directories.

The behavior under test: `xargs -0 -r ls -t` must produce nothing when the
search finds no `server.py`, not silently run `ls -t` on the current
working directory. BSD xargs (macOS, where this file is often run locally)
already no-ops on empty stdin regardless of `-r` — so this only proves
non-regression on the platform CI actually runs on: GNU xargs
(`.github/workflows/test.yml`: `runs-on: ubuntu-latest`), where the `-r`
flag's absence is exactly the silent-wrong-copy bug commit 31f90ea fixed.
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
    ROOT / ".claude" / "skills" / "viva" / "SKILL.md",
    ROOT / ".claude" / "skills" / "viva-qa" / "SKILL.md",
    ROOT / ".claude" / "skills" / "viva-diff" / "SKILL.md",
    ROOT / "README.md",
]

RESOLVE_RE = re.compile(
    r"VIVA_DIR=\$\(find ~/\.claude/plugins/cache .*?\n"
    r".*?xargs -0 -r ls -t 2>/dev/null \| head -1\)\n"
    r"VIVA_DIR=\$\{VIVA_DIR%/server\.py\}",
)


def _extract_resolve_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = RESOLVE_RE.search(text)
    assert m, f"{path}: resolve block not found — did the pipeline shape change?"
    return m.group(0)


def test_all_four_copies_identical():
    blocks = {path: _extract_resolve_block(path) for path in RESOLVE_SOURCES}
    canonical = blocks[RESOLVE_SOURCES[0]]
    for path, block in blocks.items():
        assert block == canonical, (
            f"{path} resolve block differs from {RESOLVE_SOURCES[0]} — "
            "the four hand-maintained copies have drifted apart"
        )
    print("  ok  test_all_four_copies_identical")


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


def test_empty_cache_resolves_to_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        empty_root = Path(tmp) / "plugins-cache"
        empty_root.mkdir()
        resolved = _run_resolve(empty_root)
        assert resolved == "", (
            f"empty search root resolved to {resolved!r} instead of nothing — "
            "on GNU xargs this means ls -t ran bare against some other "
            "directory (the exact silent-wrong-copy bug xargs -r fixes)"
        )
    print("  ok  test_empty_cache_resolves_to_nothing")


def test_multiple_versions_picks_newest():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp) / "plugins-cache"
        older = cache_root / "jacquardlabs-marketplace" / "viva" / "1.0.0" / "viva"
        newer = cache_root / "jacquardlabs-marketplace" / "viva" / "1.1.0" / "viva"
        older.mkdir(parents=True)
        newer.mkdir(parents=True)
        (older / "server.py").write_text("# older\n", encoding="utf-8")
        (newer / "server.py").write_text("# newer\n", encoding="utf-8")

        now = time.time()
        os.utime(older / "server.py", (now - 100, now - 100))
        os.utime(newer / "server.py", (now, now))

        resolved = _run_resolve(cache_root)
        assert resolved == str(newer), (
            f"expected newest version dir {newer} to win, got {resolved!r}"
        )
    print("  ok  test_multiple_versions_picks_newest")


def main():
    test_all_four_copies_identical()
    test_empty_cache_resolves_to_nothing()
    test_multiple_versions_picks_newest()
    print("OK (3 tests)")


if __name__ == "__main__":
    main()
