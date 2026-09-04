#!/usr/bin/env python3
"""Resolve `/viva-write` attachments (issue refs, URLs, files, directories)
into one bounded manifest (#170).

  python3 context_refs.py '#170' docs/ PRODUCT.md https://example.com/spec
  python3 context_refs.py '#170' --max-files 5 --max-bytes 20000

Classifies and bounds; never fetches — an issue entry carries `fetch`, the
`gh` argv (a list, never a shell string) to run. A directory expansion past
the caps lands in `dropped[]` rather than truncating silently. A ref that
resolves to nothing is a loud, non-zero exit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# `owner/repo#123`, `#123`. Repo half matched strictly — interpolated into
# an argv element handed to `gh`.
ISSUE_RE = re.compile(r"^(?:(?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+))?#(?P<number>\d+)$")
# A GitHub issue/PR permalink — the form pasted out of a browser.
GH_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/"
    r"(?P<target>issues|pull)/(?P<number>\d+)(?:[/?#].*)?$")
URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Undotted directories a walk skips as noise (dotted ones are skipped below).
DENY_DIRS = frozenset({
    "node_modules", "__pycache__", "venv", "dist", "build", "target",
    "vendor", "site-packages",
})

# Bytes sniffed to tell text from binary — a decode, not a suffix allow-list.
SNIFF_BYTES = 4096

DEFAULT_MAX_FILES = 20
DEFAULT_MAX_BYTES = 120_000

# Issue/PR fields worth pulling for a draft.
ISSUE_FIELDS = "number,title,body,url,state,comments"


def die(msg: str) -> None:
    sys.exit(f"context_refs: {msg}")


def is_text(path: Path) -> bool:
    """Whether `path` reads as text — a decode of its head, not its suffix."""
    try:
        with path.open("rb") as fh:
            head = fh.read(SNIFF_BYTES)
    except OSError:
        return False
    if b"\0" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def walk_dir(root: Path) -> list:
    """Every candidate text file under `root`, sorted for deterministic
    expansion. The dot rule and `DENY_DIRS` apply to descendants only — a
    caller who names `.github/workflows` outright meant it.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d not in DENY_DIRS)
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            found.append(p)
    return sorted(found)


class Budget:
    """The caps and the running total they bound, global across every ref
    and spent in ref order."""

    def __init__(self, max_files: int, max_bytes: int):
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.files = 0
        self.bytes = 0
        self.dropped = []

    def charge(self, size: int) -> None:
        """Spend against the caps unconditionally — an explicit file ref."""
        self.files += 1
        self.bytes += size

    def offer(self, ref: str, path: str, size: int) -> bool:
        """Spend only if it fits — a directory expansion — and record the
        refusal otherwise."""
        if self.files >= self.max_files:
            self.dropped.append({"ref": ref, "path": path, "reason": "file cap"})
            return False
        if self.bytes + size > self.max_bytes:
            self.dropped.append({"ref": ref, "path": path, "reason": "byte cap"})
            return False
        self.charge(size)
        return True

    def report(self) -> dict:
        return {"max_files": self.max_files, "max_bytes": self.max_bytes,
                "files": self.files, "bytes": self.bytes,
                "over": self.bytes > self.max_bytes}


def issue_entry(ref: str, repo, number: str, target: str) -> dict:
    """One issue/PR attachment, carrying the `gh` argv that fetches it.
    `fetch` is a list, never a shell string, since `repo`/`number` are
    already regex-constrained above but not shell-escaped."""
    kind = "pr" if target == "pull" else "issue"
    fetch = ["gh", kind, "view", number, "--json", ISSUE_FIELDS]
    if repo:
        fetch += ["--repo", repo]
    return {"ref": ref, "kind": kind, "repo": repo, "number": int(number),
            "fetch": fetch}


def resolve(ref: str, root: Path, budget: Budget) -> dict:
    """One ref → one manifest entry. Raises `ValueError` on a ref that names
    nothing rather than swallowing it silently."""
    m = GH_URL_RE.match(ref)
    if m:
        return issue_entry(ref, m.group("repo"), m.group("number"), m.group("target"))
    m = ISSUE_RE.match(ref)
    if m:
        return issue_entry(ref, m.group("repo"), m.group("number"), "issues")
    if URL_RE.match(ref):
        return {"ref": ref, "kind": "url", "url": ref}

    path = (root / ref).resolve() if not Path(ref).is_absolute() else Path(ref)
    if not path.exists():
        raise ValueError(
            f"{ref!r} names nothing — expected an issue ref (`#170`, "
            f"`owner/repo#170`), a URL, or an existing file or directory")
    rel = os.path.relpath(path, root)
    if path.is_dir():
        files = []
        for p in walk_dir(path):
            if not is_text(p):
                budget.dropped.append(
                    {"ref": ref, "path": os.path.relpath(p, root),
                     "reason": "not text"})
                continue
            size = p.stat().st_size
            p_rel = os.path.relpath(p, root)
            if budget.offer(ref, p_rel, size):
                files.append({"path": p_rel, "bytes": size})
        return {"ref": ref, "kind": "dir", "path": rel, "files": files,
                "bytes": sum(f["bytes"] for f in files)}

    size = path.stat().st_size
    budget.charge(size)
    return {"ref": ref, "kind": "file", "path": rel, "bytes": size,
            "text": is_text(path)}


def build(refs, root: Path, max_files: int, max_bytes: int) -> dict:
    budget = Budget(max_files, max_bytes)
    entries = []
    for ref in refs:
        try:
            entries.append(resolve(ref, root, budget))
        except ValueError as e:
            die(str(e))
    return {"refs": entries, "dropped": budget.dropped,
            "budget": budget.report()}


def main() -> None:
    p = argparse.ArgumentParser(
        description="Resolve /viva-write attachments to a bounded manifest")
    p.add_argument("refs", nargs="+", metavar="REF",
                   help="an issue ref (`#170`, `owner/repo#170`, a github.com "
                        "issue/pull URL), a URL, a file path, or a directory")
    p.add_argument("--root", default=".",
                   help="resolve relative paths against this directory "
                        "(default: .)")
    p.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES,
                   help=f"cap on files a DIRECTORY ref expands to, counted "
                        f"across every ref (default: {DEFAULT_MAX_FILES})")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                   help=f"cap on total bytes, counted across every ref "
                        f"(default: {DEFAULT_MAX_BYTES})")
    args = p.parse_args()

    if args.max_files < 1 or args.max_bytes < 1:
        die("--max-files and --max-bytes must be at least 1")
    root = Path(args.root)
    if not root.is_dir():
        die(f"--root {args.root!r} is not a directory")

    json.dump(build(args.refs, root.resolve(), args.max_files, args.max_bytes),
              sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
