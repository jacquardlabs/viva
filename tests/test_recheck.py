#!/usr/bin/env python3
"""Tests for scripts/recheck.py — withdraws a recheck's (#83) seeded approval
per drift flag. Pure and kind-scoped: only a section carrying a withdrawing
annotation loses its seeded approval."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "recheck.py"


def run(data: dict, extra_args: list[str] = ()) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "review-input-r1.json"
        inp.write_text(json.dumps(data), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(inp), *extra_args],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(f"recheck exited {result.returncode}:\n{result.stderr}")
        return json.loads(inp.read_text(encoding="utf-8")), result.stdout


def base(sections: list) -> dict:
    return {"mode": "review", "doc_file": "doc.md", "round": 1, "recheck": True,
            "approved_ids": [s["id"] for s in sections], "sections": sections}


def test_flagged_section_loses_approval() -> None:
    data = base([
        {"id": "s1", "title": "Goals", "content": "g", "annotations": [
            {"kind": "drift", "severity": "error", "message": "file gone"}]},
        {"id": "s2", "title": "Scope", "content": "s"},
    ])
    out, stdout = run(data)
    assert out["approved_ids"] == ["s2"], out
    assert "1 section(s) withdrawn" in stdout, stdout


def test_unflagged_section_keeps_approval() -> None:
    data = base([{"id": "s1", "title": "Goals", "content": "g"}])
    out, stdout = run(data)
    assert out["approved_ids"] == ["s1"], out
    assert "0 section(s) withdrawn" in stdout, stdout


def test_scoped_to_the_named_kind() -> None:
    """A non-drift annotation (confidence, preference, …) must not withdraw
    approval — only a withdrawing kind does, `drift` by default."""
    data = base([
        {"id": "s1", "title": "Goals", "content": "g", "annotations": [
            {"kind": "confidence", "severity": "info", "basis": "sourced",
             "level": "high", "message": "sourced · high"}]},
    ])
    out, _ = run(data)
    assert out["approved_ids"] == ["s1"], \
        "a confidence annotation must not withdraw approval"


def test_explicit_kind_flag_widens_or_narrows_the_default() -> None:
    data = base([
        {"id": "s1", "title": "Goals", "content": "g", "annotations": [
            {"kind": "grounding", "severity": "warn", "message": "unsupported claim"}]},
        {"id": "s2", "title": "Scope", "content": "s", "annotations": [
            {"kind": "drift", "severity": "error", "message": "file gone"}]},
    ])
    # Default kind (drift) withdraws only s2.
    out, _ = run(data)
    assert out["approved_ids"] == ["s1"], out
    # --kind grounding withdraws only s1, and drops the drift-only default.
    out2, _ = run(data, ["--kind", "grounding"])
    assert out2["approved_ids"] == ["s2"], out2
    # Repeated --kind withdraws both.
    out3, _ = run(data, ["--kind", "grounding", "--kind", "drift"])
    assert out3["approved_ids"] == [], out3


def test_idempotent_on_a_re_run() -> None:
    data = base([
        {"id": "s1", "title": "Goals", "content": "g", "annotations": [
            {"kind": "drift", "severity": "error", "message": "file gone"}]},
    ])
    once, _ = run(data)
    twice, stdout = run(once)
    assert twice == once, twice
    assert "0 section(s) withdrawn" in stdout, \
        "a section already withdrawn must not be counted again"


def test_no_approved_ids_key_is_a_no_op() -> None:
    data = {"mode": "review", "doc_file": "doc.md", "round": 1, "recheck": True,
            "sections": [{"id": "s1", "title": "Goals", "content": "g",
                          "annotations": [{"kind": "drift", "severity": "error",
                                           "message": "file gone"}]}]}
    out, stdout = run(data)
    assert "approved_ids" not in out, out
    assert "0 section(s) withdrawn" in stdout, stdout


def main() -> None:
    tests = [
        test_flagged_section_loses_approval,
        test_unflagged_section_keeps_approval,
        test_scoped_to_the_named_kind,
        test_explicit_kind_flag_widens_or_narrows_the_default,
        test_idempotent_on_a_re_run,
        test_no_approved_ids_key_is_a_no_op,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
    if failed:
        sys.exit(f"\n{failed}/{len(tests)} tests failed")
    print(f"\nOK ({len(tests)} tests)")


if __name__ == "__main__":
    main()
