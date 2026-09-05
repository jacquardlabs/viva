#!/usr/bin/env python3
"""Spec↔code drift — a viva pre-review producer (#11), mechanical part.

  python3 drift.py --input .viva/review-input-r1.json [--root .]

Prints a sidecar annotation list (JSON) to stdout — pipe it into annotate.py.
Per section, resolves backtick-quoted references against the working tree: a
file path that doesn't exist is `error` drift, a `name()` symbol with no
definition anywhere in the tree is `warn` drift. Prose-only sections emit
nothing.

Deliberately NOT here: stale-signature comparison — regex signature matching
emits false drift; that check is the LLM-assisted pass in SKILL.md.

Two more modes, for the drift hook (#143) rather than a review round:

  python3 drift.py --scan <path>...   # every signed .md doc's own file refs
  python3 drift.py --hook             # the installed git hook's body

`--scan` prints `[{doc, signed, last_signoff_date, files}]` — one entry per
`.md` file under a given path that already carries a `## Revision History`
(unsigned docs are skipped; there is nothing to warn about yet). References
are extracted from the doc text truncated at the ledger heading, same as
`find_references` below — a row in the ledger's own table quoting a filename
is not a doc reference. `--hook` is `--scan` at the repo root, intersected
against `git diff-tree`'s changed files for `HEAD`, printed as a warning —
see `cmd_hook`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import schema

# Extensions that mark a backtick token as a file reference, not a version
# string or prose.
FILE_EXTS = {
    "py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "rb", "c", "h", "hpp",
    "cpp", "cc", "cs", "php", "swift", "kt", "scala", "sh", "bash", "sql",
    "json", "yaml", "yml", "toml", "ini", "cfg", "md", "txt", "html", "css",
}
# Where a symbol definition can live — real code only, so a symbol named in
# the spec can't mask its own drift by matching prose.
CODE_EXTS = {
    "py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "rb", "c", "h", "hpp",
    "cpp", "cc", "cs", "php", "swift", "kt", "scala", "sh", "bash",
}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build",
             "__pycache__", ".mypy_cache", ".pytest_cache", "target", ".viva"}
_BACKTICK = re.compile(r"`([^`]+)`")
_FILE_TOKEN = re.compile(r"^[\w./-]+$")
_CALL = re.compile(r"^([A-Za-z_]\w*)\s*\([^)]*\)$")


def find_references(content: str) -> tuple:
    """Return (files, symbols) resolved from backtick spans in `content`.
    Dotted method calls are excluded — too ambiguous to verify without
    false drift."""
    files, symbols = [], []
    for raw in _BACKTICK.findall(content):
        span = raw.strip()
        call = _CALL.match(span)
        if call:
            symbols.append(call.group(1))
            continue
        if _FILE_TOKEN.match(span) and "." in span:
            ext = span.rsplit(".", 1)[1].lower()
            if ext in FILE_EXTS:
                files.append(span)
    # Stable de-dup preserving first-seen order.
    return list(dict.fromkeys(files)), list(dict.fromkeys(symbols))


def _iter_code_files(root: Path):
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lstrip(".").lower() in CODE_EXTS:
            yield path


def symbol_defined(name: str, root: Path) -> bool:
    """True if `name` appears as a whole word anywhere in the tree's code
    files. Conservative: presence stays silent, absence is the drift signal."""
    pattern = re.compile(r"\b" + re.escape(name) + r"\b")
    for path in _iter_code_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if pattern.search(text):
            return True
    return False


def build_sidecar(data: dict, root: Path) -> list:
    sidecar = []
    for section in data.get("sections", []):
        files, symbols = find_references(section.get("content", ""))
        for ref in files:
            if not (root / ref).exists():
                sidecar.append({
                    "id": section["id"], "kind": "drift", "severity": "error",
                    "message": f"doc references file `{ref}` — not found in the working tree",
                    "anchor": ref,
                })
        for name in symbols:
            if not symbol_defined(name, root):
                sidecar.append({
                    "id": section["id"], "kind": "drift", "severity": "warn",
                    "message": f"doc references `{name}()` — no definition found in the repo",
                    "anchor": name,
                })
    return sidecar


def _iter_md_files(paths: list):
    """Every `.md` file under each given path (a directory is walked, a file
    is taken as-is), skipping `SKIP_DIRS`, sorted for stable output."""
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            yield from sorted(
                q for q in p.rglob("*.md")
                if not any(part in SKIP_DIRS for part in q.relative_to(p).parts[:-1])
            )
        elif p.is_file() and p.suffix == ".md":
            yield p


def _doc_references(text: str) -> list:
    """`find_references`'s file half, over the doc truncated at its own
    `## Revision History` heading — a ledger row quoting a filename is not a
    reference the doc makes."""
    m = schema.REVISION_HISTORY_RE.search(text)
    body = text[:m.start()] if m else text
    files, _symbols = find_references(body)
    return files


def scan(paths: list) -> list:
    """Every SIGNED `.md` doc under `paths`, with the files its own text
    cites and its last sign-off/re-certification date (#143). An unsigned
    doc is skipped outright — there is nothing to warn about yet."""
    results = []
    for path in _iter_md_files(paths):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not schema.has_revision_history(text):
            continue
        results.append({
            "doc": str(path),
            "signed": True,
            "last_signoff_date": schema.last_signoff_date(text),
            "files": _doc_references(text),
        })
    return results


def _repo_root() -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(out) if out else None


def cmd_hook() -> None:
    """The installed git hook's body (#143) — advisory, never raises, never
    a nonzero exit: a warning here must never cost a commit. Prints one
    two-line warning per (signed doc, changed file it cites) pair actually
    touched by `HEAD`."""
    root = _repo_root()
    if root is None:
        return
    try:
        changed = set(subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout.splitlines())
    except (OSError, subprocess.CalledProcessError):
        return
    if not changed:
        return
    for entry in scan([str(root)]):
        hit = sorted(changed & set(entry["files"]))
        if not hit:
            continue
        # `scan` reports whatever path form it was given; the hook always
        # scans the absolute repo root, so display relative to it —
        # `git diff-tree`'s own paths are already repo-relative.
        doc_display = str(Path(entry["doc"]).relative_to(root))
        date = entry.get("last_signoff_date") or "an unknown date"
        for f in hit:
            print(f"viva: {doc_display} was signed off {date} and "
                  f"references {f} (changed in this commit).")
            print(f"      Run `loop.py start --doc {doc_display} --recheck` "
                  f"to verify the doc still holds.")


def main() -> None:
    p = argparse.ArgumentParser(description="Spec↔code drift producer (existence checks)")
    p.add_argument("--input", help="Round review-input JSON")
    p.add_argument("--root", default=".", help="Repo root to check references against")
    p.add_argument("--scan", nargs="+", metavar="PATH",
                   help="print every signed .md doc under PATH(s) and the "
                        "files it cites, as a JSON list — the drift hook's "
                        "read, usable standalone")
    p.add_argument("--hook", action="store_true",
                   help="the installed git hook's body: scan the repo root, "
                        "intersect against HEAD's changed files, print a "
                        "warning. Never raises, never exits nonzero.")
    args = p.parse_args()

    modes = sum(bool(x) for x in (args.input, args.scan, args.hook))
    if modes != 1:
        p.error("pass exactly one of --input, --scan, --hook")

    if args.hook:
        cmd_hook()
        return

    if args.scan:
        json.dump(scan(args.scan), sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return

    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"drift: cannot read {args.input}: {e}")

    sidecar = build_sidecar(data, Path(args.root))
    json.dump(sidecar, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
