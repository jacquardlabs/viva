#!/usr/bin/env python3
"""Doc-type resolution — viva's shipped bundles and a repo's overrides (#169).

A doc type is section grammar + check set + default pass. This filter resolves
one name to one bundle and prints it as JSON:

  python3 doc_types.py design-doc
  python3 doc_types.py design-doc --types-dir path/to/.viva-types

  {"name": "design-doc", "title": "Design doc",
   "sections": ["Problem & persona", ...],
   "checks": ["headings-present"], "default_pass": "architecture"}

`--list` prints the merged namespace instead — every resolvable name with its
title, for an intake menu when the caller named no type.

  python3 doc_types.py --list
  [{"name": "design-doc", "title": "Design doc"}, ...]

Resolution: shipped defaults live in `<plugin>/types/<name>.json`; a repo adds
or overrides via `.viva-types/<name>.json`. On a name collision the repo's file
wins WHOLESALE, not key-merged, so it can drop a shipped check as well as add one.

Bundles never live under `.viva/` — that directory is cleared every
`loop.py start` (CLAUDE.md). A type bundle is committed, shared configuration.

Every failure is loud and exits non-zero. Refused: an unknown name, a name that
is not a bare lowercase token, unreadable or malformed JSON, a missing/mistyped
required key, a `default_pass` outside `architecture|line|checks|final`, and a
bundle whose `name` disagrees with its filename.

Imports one sibling, `schema` (CLAUDE.md), for `PASS_KINDS`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import schema

# Resolved from the file, never the caller's cwd.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SHIPPED_DIR = PLUGIN_ROOT / "types"

# Repo-side override directory, relative to wherever the caller is running.
REPO_TYPES_DIR = ".viva-types"

# A bare lowercase token — the filename IS the identity.
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

    Validated once, here, at the read boundary — not at each producer that
    consumes one.
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
    # `checks[]` becomes a path (`<name with - as _>.py`), so each entry
    # must pass NAME_RE before any path is built, same as `name` does.
    for kind in bundle["checks"]:
        if not NAME_RE.match(kind):
            raise ValueError(
                f"{where}: checks entry {kind!r} is not a bare lowercase "
                f"token (matching {NAME_RE.pattern}) — it becomes the "
                f"producer script name '{kind.replace('-', '_')}.py'")
        # CHECK_KINDS fails open (CLAUDE.md): an unregistered kind is
        # invisible to round_is_complete, so warn rather than refuse to load.
        if kind not in schema.CHECK_KINDS:
            print(f"doc_types: warning: {where}: checks entry {kind!r} is "
                  f"not in schema.CHECK_KINDS — a 'checks' pass will not "
                  f"gate on it; the round closes with this check invisible",
                  file=sys.stderr)
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
    p.add_argument("name", nargs="?", help="Type name, e.g. design-doc")
    p.add_argument("--list", action="store_true",
                   help="print every resolvable name with its title (the "
                        "intake menu) instead of resolving one")
    p.add_argument("--types-dir", default=REPO_TYPES_DIR,
                   help=f"Repo bundle directory whose copies win on a name "
                        f"collision (default: {REPO_TYPES_DIR})")
    args = p.parse_args()

    if args.list:
        # Resolved, not globbed: a name in the namespace whose bundle will not
        # load must not sit in the menu as an offer that dies when picked.
        menu = []
        for name in known(Path(args.types_dir)):
            try:
                bundle = resolve(name, Path(args.types_dir))
            except ValueError as e:
                sys.exit(f"doc_types: {e}")
            menu.append({"name": bundle["name"], "title": bundle["title"]})
        json.dump(menu, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return
    # `is None`, not truthiness: an explicitly-passed empty name is a bad NAME
    # and must reach `resolve` to be refused as one, not be reported as a
    # missing argument the caller did supply.
    if args.name is None:
        sys.exit("doc_types: name is required (or pass --list)")

    try:
        bundle = resolve(args.name, Path(args.types_dir))
    except ValueError as e:
        sys.exit(f"doc_types: {e}")

    json.dump(bundle, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
