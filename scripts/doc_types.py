#!/usr/bin/env python3
"""Doc-type resolution — viva's shipped bundles and a repo's overrides (#169).

A doc type is section grammar + check set + default pass. This filter resolves
one name to one bundle and prints it as JSON:

  python3 doc_types.py design-doc
  python3 doc_types.py design-doc --types-dir path/to/.viva-types

  {"name": "design-doc", "title": "Design doc",
   "sections": ["Problem & persona", ...],
   "checks": ["headings-present"], "default_pass": "architecture"}

Resolution: shipped defaults live in `<plugin>/types/<name>.json`; a repo adds
or overrides a type by committing `.viva-types/<name>.json`. The two directories
merge by NAME, and on a collision the repo's file wins WHOLESALE — it replaces
the shipped bundle rather than being key-merged into it, so a repo can drop a
shipped check as well as add one.

Bundles never live under `.viva/`: that directory is cleared at every
`loop.py start` and `preferences.json` is its one documented survivor
(CLAUDE.md). A type bundle is committed, shared configuration.

Every failure is loud and exits non-zero — an unresolved type must never reach a
producer as an empty grammar. Refused: an unknown name, a name that is not a
bare lowercase token, unreadable or malformed JSON, a missing/mistyped required
key, a `default_pass` outside `architecture|line|checks|final`, and a bundle
whose `name` disagrees with its filename (the filename is what `--type` keys on,
so `--type foo` must never hand back a bundle calling itself `bar`).

Imports one sibling, `schema` — the permitted cross-import (CLAUDE.md) — for
`PASS_KINDS`. The round-level `pass` field now lives in the shared contract, so
a bundle's `default_pass` is validated against the same tuple a round is.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import schema

# `doc_types.py` lives in <plugin-root>/scripts/, so the shipped bundles are its
# parent's sibling — resolved from the file, never from a caller's cwd.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_DIR = PLUGIN_ROOT / "types"

# The repo-side override directory, relative to wherever the caller is running.
REPO_TYPES_DIR = ".viva-types"

# A bare lowercase token: the filename IS the identity, so a name carrying a
# separator, a dot segment, or a space is rejected before any path is built.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def known(types_dir: Path, shipped_dir: Path = SHIPPED_DIR) -> list:
    """Every resolvable type name — the merged namespace, sorted."""
    return sorted({
        p.stem
        for d in (shipped_dir, types_dir) if d.is_dir()
        for p in d.glob("*.json")
    })


def validate_bundle(bundle: object, name: str, where: str) -> None:
    """Raise `ValueError` unless `bundle` is a structurally valid type bundle.

    The boundary is the read: a bundle is validated once, here, rather than at
    each producer that consumes one — a missing `sections` key would otherwise
    read as "this type expects no headings" at every call site that forgot.
    """
    if not isinstance(bundle, dict):
        raise ValueError(f"{where}: a type bundle must be a JSON object")
    for field in ("name", "title", "default_pass"):
        if not isinstance(bundle.get(field), str):
            raise ValueError(f"{where}: missing required string {field!r}")
    for field in ("sections", "checks"):
        value = bundle.get(field)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{where}: {field!r} must be a list of strings")
    if bundle["name"] != name:
        raise ValueError(
            f"{where}: bundle names itself {bundle['name']!r} but resolves as "
            f"{name!r} — the filename is the identity, so the two must agree")
    if bundle["default_pass"] not in schema.PASS_KINDS:
        raise ValueError(
            f"{where}: default_pass {bundle['default_pass']!r} is not one of "
            f"{'|'.join(schema.PASS_KINDS)}")


def load_bundle(path: Path, name: str) -> dict:
    """Read and validate one bundle file."""
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read type bundle {path}: {e}")
    validate_bundle(bundle, name, str(path))
    return bundle


def resolve(name: str, types_dir: Path, shipped_dir: Path = SHIPPED_DIR) -> dict:
    """Resolve `name` to a bundle — the repo's copy first, then the shipped one."""
    if not NAME_RE.match(name or ""):
        raise ValueError(
            f"{name!r} is not a doc-type name — expected a bare lowercase token "
            f"like 'design-doc' (matching {NAME_RE.pattern})")
    for source in (types_dir / f"{name}.json", shipped_dir / f"{name}.json"):
        if source.is_file():
            return load_bundle(source, name)
    available = ", ".join(known(types_dir, shipped_dir)) or "none"
    raise ValueError(
        f"unknown doc type {name!r} — no {name}.json in {types_dir} or "
        f"{shipped_dir}. Known types: {available}")


def main() -> None:
    p = argparse.ArgumentParser(description="Resolve a viva doc-type bundle")
    p.add_argument("name", help="Type name, e.g. design-doc")
    p.add_argument("--types-dir", default=REPO_TYPES_DIR,
                   help=f"Repo bundle directory whose copies win on a name "
                        f"collision (default: {REPO_TYPES_DIR})")
    args = p.parse_args()

    try:
        bundle = resolve(args.name, Path(args.types_dir))
    except ValueError as e:
        sys.exit(f"doc_types: {e}")

    json.dump(bundle, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
