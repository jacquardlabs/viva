#!/usr/bin/env python3
"""Tests for scripts/doc_types.py — type-bundle resolution and repo override.

A type bundle is the doc's section grammar, its check set, and its default pass.
Resolution is the only place a name becomes a bundle, so every way it can fail —
unknown name, malformed file, a bundle that lies about its own name — has to
fail loudly here rather than downstream as an empty grammar nothing flags.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "doc_types.py"
SHIPPED = ROOT / "types"

sys.path.insert(0, str(ROOT / "scripts"))
import doc_types  # noqa: E402

PASS_KINDS = ("structure", "line", "fact-check", "proof")
# Per the design's open question 2 — the types this repo actually produces.
EXPECTED_SHIPPED = {"design-doc", "plan", "pr-description", "readme",
                    "progress-note"}


def resolve(name: str, types_dir: Path | None = None) -> subprocess.CompletedProcess:
    """Run the filter exactly as a caller does; never raises on a nonzero exit."""
    cmd = [sys.executable, str(SCRIPT), name]
    if types_dir is not None:
        cmd += ["--types-dir", str(types_dir)]
    return subprocess.run(cmd, capture_output=True, text=True)


def resolve_ok(name: str, types_dir: Path | None = None) -> dict:
    r = resolve(name, types_dir)
    assert r.returncode == 0, f"resolving {name!r} failed:\n{r.stderr}"
    return json.loads(r.stdout)


def write_bundle(directory: Path, name: str, bundle) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(bundle if isinstance(bundle, str) else json.dumps(bundle),
                    encoding="utf-8")
    return path


def test_shipped_set_resolves_and_validates() -> None:
    on_disk = {p.stem for p in SHIPPED.glob("*.json")}
    assert on_disk == EXPECTED_SHIPPED, (
        f"shipped types {sorted(on_disk)} != expected {sorted(EXPECTED_SHIPPED)}")
    for name in sorted(on_disk):
        bundle = resolve_ok(name)
        assert bundle["name"] == name, bundle
        assert isinstance(bundle["title"], str) and bundle["title"], bundle
        assert bundle["default_pass"] in PASS_KINDS, bundle
        assert all(isinstance(s, str) for s in bundle["sections"]), bundle
        assert all(isinstance(c, str) for c in bundle["checks"]), bundle
    print("  ok  test_shipped_set_resolves_and_validates")


def test_shipped_grammars_exclude_revision_history() -> None:
    """`parse_sections.py` splits the ledger off every round — `rev_line`
    truncates the source and the heading never becomes a card. A bundle that
    listed it would make `headings-present` flag every signed-off doc for a
    section that cannot exist."""
    for name in sorted(EXPECTED_SHIPPED):
        headings = [h.strip().lower() for h in resolve_ok(name)["sections"]]
        assert "revision history" not in headings, (
            f"types/{name}.json expects a 'Revision History' heading, which no "
            f"round can ever contain")
    print("  ok  test_shipped_grammars_exclude_revision_history")


def test_shipped_checks_name_a_real_producer() -> None:
    """`checks[]` is what a driver iterates to decide which producers to run, so
    a name with no script behind it is an instruction nothing can follow. The
    mapping is mechanical: `<name with - as _>.py` beside this script."""
    for name in sorted(EXPECTED_SHIPPED):
        for check in resolve_ok(name)["checks"]:
            producer = ROOT / "scripts" / (check.replace("-", "_") + ".py")
            assert producer.is_file(), (
                f"types/{name}.json names check {check!r} but {producer} does "
                f"not exist")
    print("  ok  test_shipped_checks_name_a_real_producer")


def test_repo_copy_wins_wholesale() -> None:
    """A repo's `.viva-types/<name>.json` replaces the shipped bundle, not
    key-merged into it — otherwise a repo could add a check but never drop one."""
    shipped = resolve_ok("design-doc")
    assert shipped["sections"], "fixture assumes the shipped bundle has a grammar"
    with tempfile.TemporaryDirectory() as td:
        types_dir = Path(td) / ".viva-types"
        write_bundle(types_dir, "design-doc", {
            "name": "design-doc", "title": "House design doc",
            "sections": ["Context"], "checks": [], "default_pass": "line",
        })
        got = resolve_ok("design-doc", types_dir)
    assert got["title"] == "House design doc", got
    assert got["sections"] == ["Context"], got
    assert got["checks"] == [], (
        "the shipped bundle's checks leaked through — the repo's copy must win "
        "wholesale, not merge key by key")
    assert got["default_pass"] == "line", got
    print("  ok  test_repo_copy_wins_wholesale")


def test_repo_adds_a_type_shipped_set_survives() -> None:
    with tempfile.TemporaryDirectory() as td:
        types_dir = Path(td) / ".viva-types"
        write_bundle(types_dir, "runbook", {
            "name": "runbook", "title": "Runbook",
            "sections": ["Trigger", "Steps", "Rollback"],
            "checks": ["headings-present"], "default_pass": "structure",
        })
        added = resolve_ok("runbook", types_dir)
        assert added["title"] == "Runbook", added
        # An override directory does not shadow the names it does not carry.
        assert resolve_ok("plan", types_dir)["name"] == "plan"
    print("  ok  test_repo_adds_a_type_shipped_set_survives")


def test_unknown_name_fails_loudly_and_lists_what_exists() -> None:
    with tempfile.TemporaryDirectory() as td:
        r = resolve("no-such-type", Path(td) / ".viva-types")
    assert r.returncode != 0, "an unknown type must not exit 0"
    assert r.stdout.strip() == "", "a failed resolve must print no bundle"
    assert "unknown doc type" in r.stderr, r.stderr
    assert "design-doc" in r.stderr, (
        "the failure must name the types that do resolve: %s" % r.stderr)
    print("  ok  test_unknown_name_fails_loudly_and_lists_what_exists")


def test_malformed_json_fails_loudly() -> None:
    with tempfile.TemporaryDirectory() as td:
        types_dir = Path(td) / ".viva-types"
        write_bundle(types_dir, "design-doc", "{ not json")
        r = resolve("design-doc", types_dir)
    assert r.returncode != 0, "a malformed bundle must not exit 0"
    assert r.stdout.strip() == "", r.stdout
    assert "cannot read type bundle" in r.stderr, r.stderr
    print("  ok  test_malformed_json_fails_loudly")


def test_bundle_name_must_match_its_filename() -> None:
    """The filename is the identity `--type` keys on, so `--type foo` must never
    hand back a bundle calling itself `bar`."""
    with tempfile.TemporaryDirectory() as td:
        types_dir = Path(td) / ".viva-types"
        write_bundle(types_dir, "spec", {
            "name": "design-doc", "title": "Spec", "sections": [],
            "checks": [], "default_pass": "structure",
        })
        r = resolve("spec", types_dir)
    assert r.returncode != 0, "a bundle that renames itself must be refused"
    assert "names itself" in r.stderr, r.stderr
    print("  ok  test_bundle_name_must_match_its_filename")


def test_structurally_invalid_bundles_are_refused() -> None:
    cases = {
        "missing sections": {"name": "x", "title": "X", "checks": [],
                             "default_pass": "structure"},
        "sections not strings": {"name": "x", "title": "X", "sections": [1],
                                 "checks": [], "default_pass": "structure"},
        "missing title": {"name": "x", "sections": [], "checks": [],
                          "default_pass": "structure"},
        "unknown pass": {"name": "x", "title": "X", "sections": [],
                         "checks": [], "default_pass": "vibes"},
        "not an object": ["x"],
    }
    for label, bundle in cases.items():
        with tempfile.TemporaryDirectory() as td:
            types_dir = Path(td) / ".viva-types"
            write_bundle(types_dir, "x", bundle)
            r = resolve("x", types_dir)
        assert r.returncode != 0, f"{label}: must be refused"
        assert r.stdout.strip() == "", f"{label}: printed a bundle anyway"
    print("  ok  test_structurally_invalid_bundles_are_refused")


def test_name_must_be_a_bare_token() -> None:
    """Rejected before any path is built — the name is a filename component."""
    with tempfile.TemporaryDirectory() as td:
        types_dir = Path(td) / ".viva-types"
        types_dir.mkdir(parents=True)
        for bad in ("../design-doc", "a/b", "..", "", "Design-Doc", "design doc"):
            r = resolve(bad, types_dir)
            assert r.returncode != 0, f"{bad!r} must not resolve"
            assert "is not a doc-type name" in r.stderr, (bad, r.stderr)
    print("  ok  test_name_must_be_a_bare_token")


def test_doc_types_cross_imports_no_sibling() -> None:
    """CLAUDE.md's one-cross-import rule: `schema` is the only sibling any
    script may import, and this one needs nothing from it."""
    siblings = {p.stem for p in (ROOT / "scripts").glob("*.py")} - {"doc_types"}
    imported = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    stray = (imported & siblings) - {"schema"}
    assert not stray, f"doc_types.py cross-imports {sorted(stray)}"
    print("  ok  test_doc_types_cross_imports_no_sibling")


def test_bundles_live_outside_the_cleared_state_dir() -> None:
    """`.viva/` is wiped at every `loop.py start` and `preferences.json` is its
    one documented survivor (CLAUDE.md), so neither bundle directory may sit
    inside it — a committed, shared config file is not disposable round state."""
    assert doc_types.SHIPPED_DIR == ROOT / "types", doc_types.SHIPPED_DIR
    assert doc_types.REPO_TYPES_DIR == ".viva-types", doc_types.REPO_TYPES_DIR
    for where in (str(doc_types.SHIPPED_DIR), doc_types.REPO_TYPES_DIR):
        assert not where.startswith(".viva/") and "/.viva/" not in where, where
    print("  ok  test_bundles_live_outside_the_cleared_state_dir")


def main() -> None:
    test_shipped_set_resolves_and_validates()
    test_shipped_grammars_exclude_revision_history()
    test_shipped_checks_name_a_real_producer()
    test_repo_copy_wins_wholesale()
    test_repo_adds_a_type_shipped_set_survives()
    test_unknown_name_fails_loudly_and_lists_what_exists()
    test_malformed_json_fails_loudly()
    test_bundle_name_must_match_its_filename()
    test_structurally_invalid_bundles_are_refused()
    test_name_must_be_a_bare_token()
    test_doc_types_cross_imports_no_sibling()
    test_bundles_live_outside_the_cleared_state_dir()
    print("OK (12 tests)")


if __name__ == "__main__":
    main()
