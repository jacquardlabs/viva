#!/usr/bin/env python3
"""Tests for scripts/checklist.py — required-section gating producer (#13).

Given a doc type (explicit or inferred from filename/H1), the producer checks
the parsed section headings against a per-type template and emits an `error`
annotation for each missing required section. The flag attaches to an existing
card (the first section) — synthetic placeholder cards would break the parser's
integrity invariant. An untyped doc emits nothing (reviews as today).

The script reads a review-input JSON and prints a sidecar annotation list
(consumed by annotate.py).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "checklist.py"


def run(sections: list, doc_file: str = "doc.md", extra: list = ()) -> list:
    data = {"mode": "review", "doc_file": doc_file, "round": 1,
            "approved_ids": [], "sections": sections}
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "in.json"
        inp.write_text(json.dumps(data), encoding="utf-8")
        cmd = [sys.executable, str(SCRIPT), "--input", str(inp)]
        cmd.extend(extra)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"checklist exited {result.returncode}:\n{result.stderr}")
        return json.loads(result.stdout)


def secs(*titles: str) -> list:
    return [{"id": f"s{i+1}", "title": t, "content": f"## {t}\n\nbody\n"}
            for i, t in enumerate(titles)]


def test_spec_missing_nongoals_flagged() -> None:
    out = run(secs("Problem", "Testing"), extra=["--type", "spec"])
    assert len(out) == 1, f"expected one missing-section flag, got {out}"
    flag = out[0]
    assert flag["id"] == "s1", "flag must attach to the first (existing) card"
    assert flag["severity"] == "error"
    assert "non-goals" in flag["message"].lower()


def test_complete_spec_no_annotations() -> None:
    out = run(secs("Problem", "Non-goals", "Testing strategy"), extra=["--type", "spec"])
    assert out == [], f"complete spec must emit nothing, got {out}"


def test_untyped_doc_no_gating() -> None:
    out = run(secs("Whatever", "Random"))
    assert out == [], "untyped doc must review as today (no gating)"


def test_explicit_type_overrides_inference() -> None:
    # Filename says spec; --type adr must win, so ADR sections are required.
    out = run(secs("Context", "Decision", "Consequences"),
              doc_file="my-spec.md", extra=["--type", "adr"])
    assert out == [], "explicit --type adr should pass an ADR-complete doc"


def test_inferred_type_from_filename() -> None:
    out = run(secs("Context", "Decision"), doc_file="0001-adr-caching.md")
    # ADR requires consequences; it's missing → flagged.
    assert len(out) == 1
    assert "consequences" in out[0]["message"].lower()


def test_case_and_punctuation_insensitive_match() -> None:
    # "Non Goals" (space, no hyphen) must satisfy required "Non-goals".
    out = run(secs("Problem", "Non Goals", "Testing"), extra=["--type", "spec"])
    assert out == [], f"normalized heading match failed: {out}"


def test_unrelated_filename_not_inferred() -> None:
    # 'spec' is a substring of 'inspector' — a token-boundary match must NOT
    # infer a type here, so an untyped doc reviews exactly as today.
    out = run(secs("Whatever", "Random"), doc_file="inspector.md")
    assert out == [], f"'inspector.md' must not infer a doc type, got {out}"


def test_inferred_type_from_h1_token() -> None:
    # First section title 'Spec: Caching' carries the type as a whole token.
    out = run(secs("Spec: Caching", "Problem"), doc_file="notes.md")
    # spec requires non-goals + testing → both missing → flagged.
    msgs = " ".join(f["message"].lower() for f in out)
    assert "non-goals" in msgs and "testing" in msgs, f"H1 type inference failed: {out}"


def main() -> None:
    tests = [
        test_spec_missing_nongoals_flagged,
        test_complete_spec_no_annotations,
        test_untyped_doc_no_gating,
        test_explicit_type_overrides_inference,
        test_inferred_type_from_filename,
        test_case_and_punctuation_insensitive_match,
        test_unrelated_filename_not_inferred,
        test_inferred_type_from_h1_token,
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
