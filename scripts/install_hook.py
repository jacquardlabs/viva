#!/usr/bin/env python3
"""Install the viva drift-warning git hook (#143).

  python3 install_hook.py

Opt-in only — never run by anything else in this plugin. Installs a
`post-commit` hook that, after a commit, warns when a file it touched is one
a SIGNED doc's own text cites (`drift.py --hook`). Advisory: it never blocks
the commit, and is a no-op in a repo with no signed docs.

Idempotent: re-running when the hook is already installed changes nothing.
Appends to an existing `post-commit` rather than overwriting it, so an
existing hook (pre-commit linting, whatever else) survives. The hook body
resolves `$VIVA_DIR` at RUN TIME, the same resolve every skill uses — never
baked in at install time, which would break silently on the next plugin
version.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

MARKER_BEGIN = "# >>> viva drift hook (#143) >>>"
MARKER_END = "# <<< viva drift hook (#143) <<<"

# Same $VIVA_DIR resolve as every skill's bash block (README, SKILL.md):
# highest cached version wins, by version number, not mtime.
HOOK_BLOCK = f"""{MARKER_BEGIN}
# Installed by scripts/install_hook.py — advisory only, never blocks the commit.
VIVA_DIR=$(find ~/.claude/plugins/cache -maxdepth 4 -path "*/jacquardlabs-marketplace/viva/*" -name server.py 2>/dev/null \\
           | awk -F/ '{{split($(NF-1), v, "."); printf "%09d%09d%09d\\t%s\\n", v[1]+0, v[2]+0, v[3]+0, $0}}' \\
           | sort -r | head -1 | cut -f2-)
VIVA_DIR=${{VIVA_DIR%/server.py}}
if [ -n "$VIVA_DIR" ] && [ -f "$VIVA_DIR/scripts/drift.py" ]; then
    python3 "$VIVA_DIR/scripts/drift.py" --hook 2>/dev/null || true
fi
{MARKER_END}
"""


def _repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("install_hook: not inside a git repository")
    return Path(out)


def hooks_dir(repo_root: Path) -> Path:
    """`core.hooksPath` if set (a repo can redirect its hooks anywhere);
    otherwise `git rev-parse --git-path hooks`, which resolves to the
    COMMON dir's hooks/ even from inside a worktree, where `.git` is a file,
    not a directory."""
    configured = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if configured.returncode == 0 and configured.stdout.strip():
        p = Path(configured.stdout.strip())
        return p if p.is_absolute() else repo_root / p
    out = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    p = Path(out)
    return p if p.is_absolute() else repo_root / p


def install(hook_path: Path) -> str:
    """Write or append the guarded block; return what happened, for the
    caller to report. Idempotent — a second run changes nothing."""
    if hook_path.exists():
        text = hook_path.read_text(encoding="utf-8")
        if MARKER_BEGIN in text:
            return "already installed, unchanged"
        if not text.endswith("\n"):
            text += "\n"
        hook_path.write_text(text + "\n" + HOOK_BLOCK, encoding="utf-8")
        action = "appended to the existing hook"
    else:
        hook_path.write_text("#!/bin/sh\n" + HOOK_BLOCK, encoding="utf-8")
        action = "installed"
    hook_path.chmod(hook_path.stat().st_mode | 0o111)
    return action


def main() -> None:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    root = _repo_root()
    hdir = hooks_dir(root)
    hdir.mkdir(parents=True, exist_ok=True)
    hook_path = hdir / "post-commit"
    action = install(hook_path)
    print(f"viva: drift hook {action} — {hook_path}")


if __name__ == "__main__":
    main()
