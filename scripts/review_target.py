#!/usr/bin/env python3
"""Review-target classification — the dispatch under `/viva-review` (#170).

One skill now covers both review checkpoints, and what it reviews is decided by
what you point it at:

  python3 review_target.py docs/spec.md   → {"kind": "doc",  "doc": "docs/spec.md"}
  python3 review_target.py 187            → {"kind": "pr",   "number": 187, ...}
  python3 review_target.py HEAD~3..HEAD   → {"kind": "ref",  "ref": "HEAD~3..HEAD", ...}
  python3 review_target.py                → {"kind": "worktree", ...}

A `pr`, `ref`, or `worktree` target carries `capture` — the argv that writes the
patch to stdout (`gh pr diff 187`, `git diff HEAD~3..HEAD`). An argv LIST, never
a shell string, built only from a `\\d+` number or a target that passed `REF_RE`,
so no target can carry a shell metacharacter into it. Like `context_refs.py`,
this runs nothing: no `git`, no `gh`, no network, no repo needed to test it.

**Precedence is filesystem first, then shape**, and that order is the whole
contract. `187` is a PR number, but a repo with a *file* named `187` means the
file — checking the path first is what stops a target the caller can see in `ls`
from being silently reinterpreted as a pull request. The same rule makes a branch
literally named `42` unreachable by derivation; that is the documented cost, and
`--kind` is the escape.

Every failure is loud. A path that exists but is not markdown, a `--kind` that
disagrees with its target, and a target carrying shell punctuation are all
refused here rather than becoming a `git diff` that reviews the wrong thing.

Imports no sibling: it needs no shared vocabulary, so it takes none.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# `187`, `#187`, and the browser-pasted permalink.
PR_RE = re.compile(r"^#?(?P<number>\d+)$")
PR_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/(?P<number>\d+)(?:[/?#].*)?$")

# What may reach `git diff` as one argument. Deliberately narrower than git's own
# refname rules: every character git allows that a shell also reads specially is
# excluded, so the argv stays safe even if a caller later builds a command line
# out of it. `~`, `^`, `..`, `@{…}`, and `/` all survive — the forms anyone types.
REF_RE = re.compile(r"^[A-Za-z0-9_./^~@{}-]+$")

DOC_SUFFIXES = (".md", ".markdown")

KINDS = ("doc", "pr", "ref", "worktree")


def die(msg: str) -> None:
    sys.exit(f"review_target: {msg}")


def as_doc(target: str, root: Path):
    """A doc record, or `None` when nothing is at that path. Raises when
    something IS there and cannot be reviewed as a document — an existing path
    must never fall through to be reinterpreted as a git ref."""
    path = Path(target) if Path(target).is_absolute() else root / target
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(
            f"{target!r} is a directory — a review target is one markdown file, "
            f"a git ref, or a PR number")
    if path.suffix.lower() not in DOC_SUFFIXES:
        raise ValueError(
            f"{target!r} is a file but not markdown ({'/'.join(DOC_SUFFIXES)}) — "
            f"review a doc, or name a git ref to review the diff that changed it")
    return {"kind": "doc", "doc": target, "label": target}


def as_pr(target: str):
    """A PR record, or `None` when the target is not a number or a pull URL."""
    m = PR_URL_RE.match(target)
    repo = m.group("repo") if m else None
    if not m:
        m = PR_RE.match(target)
    if not m:
        return None
    number = m.group("number")
    return {"kind": "pr", "number": int(number), "repo": repo,
            "label": "PR #%s%s" % (number, f" ({repo})" if repo else ""),
            "capture": ["gh", "pr", "diff", number]
                       + (["--repo", repo] if repo else [])}


def as_ref(target: str):
    """A git-ref record, or `None` when the target holds punctuation a ref may
    not carry."""
    if not REF_RE.match(target):
        return None
    return {"kind": "ref", "ref": target, "label": target,
            "capture": ["git", "diff", target]}


def worktree() -> dict:
    """`git diff` with no ref — unstaged working-tree changes, the default
    `/viva-diff` shipped with."""
    return {"kind": "worktree", "label": "working tree", "capture": ["git", "diff"]}


def classify(target, root: Path, force=None) -> dict:
    """One target → one dispatch record. `target is None` is the working tree;
    `force` is a `KINDS` value that skips derivation (and, for `pr`/`ref`, skips
    the filesystem check that would otherwise shadow it)."""
    if force == "worktree" or (force is None and target is None):
        if target is not None:
            raise ValueError("--kind worktree takes no target")
        return worktree()
    if target is None:
        raise ValueError(f"--kind {force} needs a target")

    if force:
        record = {"doc": lambda: as_doc(target, root),
                  "pr": lambda: as_pr(target),
                  "ref": lambda: as_ref(target)}[force]()
        if record is None:
            raise ValueError(f"--kind {force}, but {target!r} is not one")
        return record

    # Derivation order IS the contract: an existing path wins over any shape.
    record = as_doc(target, root) or as_pr(target) or as_ref(target)
    if record is None:
        raise ValueError(
            f"{target!r} names no file, is not a PR number, and is not a usable "
            f"git ref (expected {REF_RE.pattern})")
    return record


def main() -> None:
    p = argparse.ArgumentParser(
        description="Classify a /viva-review target: doc, PR, git ref, or the "
                    "working tree")
    p.add_argument("target", nargs="?",
                   help="a markdown path, a PR number (`187`, `#187`, a "
                        "github.com pull URL), or a git ref/range. Omit for "
                        "unstaged working-tree changes.")
    p.add_argument("--root", default=".",
                   help="resolve a relative path against this directory "
                        "(default: .)")
    p.add_argument("--kind", choices=KINDS,
                   help="force the dispatch instead of deriving it — the "
                        "override for a branch whose name is all digits, or one "
                        "that collides with a file. Refused when it disagrees "
                        "with its target.")
    args = p.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        die(f"--root {args.root!r} is not a directory")

    try:
        record = classify(args.target, root.resolve(), args.kind)
    except ValueError as e:
        die(str(e))

    json.dump(record, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
