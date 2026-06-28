#!/usr/bin/env python3
"""Spec↔code drift — a viva pre-review producer (#11), mechanical part.

Design docs, ADRs, and specs name files, endpoints, and functions that may
already have drifted from the code they describe. viva already reads the repo at
rewrite time; this runs the same existence check *before* the round so the
mismatch is visible during review, not after.

  python3 drift.py --input .viva/review-input-r1.json [--root .]

Prints a sidecar annotation list (JSON) to stdout — pipe it into annotate.py.

Scope (high-precision, mechanical): per section, resolve backtick-quoted
references and check them against the working tree.
  - File path (`scripts/foo.py`) that doesn't exist → `error` drift.
  - Simple `name()` symbol with no definition anywhere in the tree → `warn` drift.
Prose-only sections with no references emit nothing.

Deliberately NOT here: stale-signature comparison. Regex signature matching
emits false drift and poisons an advisory channel reviewers must trust — that
check is the LLM-assisted pass documented in SKILL.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Extensions that mark a backtick token as a code/doc file reference (so version
# strings like `v1.2.0` and prose are not mistaken for missing files).
FILE_EXTS = {
    "py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "rb", "c", "h", "hpp",
    "cpp", "cc", "cs", "php", "swift", "kt", "scala", "sh", "bash", "sql",
    "json", "yaml", "yml", "toml", "ini", "cfg", "md", "txt", "html", "css",
}
# Where a *symbol definition* can live — real code only. Docs, config, and the
# doc-under-review (.md/.json) are excluded so a symbol named in the spec can't
# mask its own drift, and prose mentions don't count as definitions.
CODE_EXTS = {
    "py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "rb", "c", "h", "hpp",
    "cpp", "cc", "cs", "php", "swift", "kt", "scala", "sh", "bash",
}
# Directories never worth scanning for a symbol definition.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build",
             "__pycache__", ".mypy_cache", ".pytest_cache", "target", ".viva"}
_BACKTICK = re.compile(r"`([^`]+)`")
_FILE_TOKEN = re.compile(r"^[\w./-]+$")
_CALL = re.compile(r"^([A-Za-z_]\w*)\s*\([^)]*\)$")


def find_references(content: str) -> tuple:
    """Return (files, symbols) resolved from backtick spans in `content`.

    files   — tokens that look like a path with a known code/doc extension.
    symbols — bare `name(...)` calls (no dot — dotted method calls are too
              ambiguous to verify and would emit false drift).
    """
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
    """True if `name` appears as a whole word anywhere in the tree's code files.

    Conservative by design: absence is a strong drift signal; presence is enough
    to stay silent (we never claim it IS a definition — that's the LLM pass's job).
    """
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


def main() -> None:
    p = argparse.ArgumentParser(description="Spec↔code drift producer (existence checks)")
    p.add_argument("--input", required=True, help="Round review-input JSON")
    p.add_argument("--root", default=".", help="Repo root to check references against")
    args = p.parse_args()

    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"drift: cannot read {args.input}: {e}")

    sidecar = build_sidecar(data, Path(args.root))
    json.dump(sidecar, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
