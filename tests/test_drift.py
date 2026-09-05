#!/usr/bin/env python3
"""Tests for scripts/drift.py — spec↔code drift producer (#11), mechanical part.

Checks *existence* only: a section names a file or function the working tree
no longer contains. Reads review-input JSON, resolves backtick-quoted file
paths and `name()` symbol references per section, prints a sidecar list.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "drift.py"


def run(content: str, files: dict | None = None) -> list:
    """Seed `files` into a temp root, run drift on a one-section doc, return sidecar."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        for rel, body in (files or {}).items():
            f = t / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding="utf-8")
        data = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
                "sections": [{"id": "s1", "title": "Design", "content": content}]}
        inp = t / "in.json"
        inp.write_text(json.dumps(data), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(inp), "--root", str(t)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(f"drift exited {result.returncode}:\n{result.stderr}")
        return json.loads(result.stdout)


def test_missing_file_flagged() -> None:
    out = run("The handler lives in `nope/missing.py` and does the work.")
    assert len(out) == 1, f"expected one drift flag, got {out}"
    assert out[0]["id"] == "s1"
    assert out[0]["severity"] == "error"
    assert "missing.py" in out[0]["message"]


def test_existing_file_not_flagged() -> None:
    out = run("See `real.py` for details.", files={"real.py": "x = 1\n"})
    assert out == [], f"existing file must not drift, got {out}"


def test_prose_only_section_skipped() -> None:
    out = run("This section is all prose with no code references at all.")
    assert out == [], "prose-only section must emit nothing"


def test_missing_symbol_flagged() -> None:
    # A referenced function that exists nowhere in the tree is drift (warn).
    out = run("Call `vanished_fn()` to start.", files={"app.py": "def other(): pass\n"})
    assert len(out) == 1, f"expected one symbol drift, got {out}"
    assert out[0]["severity"] == "warn"
    assert "vanished_fn" in out[0]["message"]


def test_existing_symbol_not_flagged() -> None:
    out = run("Call `real_fn()` to start.",
              files={"app.py": "def real_fn():\n    return 1\n"})
    assert out == [], f"existing symbol must not drift, got {out}"


def test_version_string_not_treated_as_file() -> None:
    # `v1.2.0` looks path-ish but is not a code file reference.
    out = run("We ship `v1.2.0` next week.")
    assert out == [], f"version string must not be flagged as a missing file, got {out}"


def test_dotted_method_call_not_flagged() -> None:
    # `data.get()` is too ambiguous to check as a symbol — must be ignored.
    out = run("It calls `data.get()` internally.")
    assert out == [], f"dotted method call must not be flagged, got {out}"


def run_scan(paths: list) -> list:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--scan", *paths],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"drift --scan exited {result.returncode}:\n{result.stderr}")
    return json.loads(result.stdout)


def test_scan_finds_a_signed_doc_and_its_references() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "spec.md").write_text(
            "# Spec\n\n## Alpha\n\nSee `mod.py`.\n\n"
            "---\n\n## Revision History\n\n"
            "Signed off via viva review — 1 round, 1 section, 0 with comments. "
            "2026-06-01\n",
            encoding="utf-8")
        out = run_scan([str(t)])
        assert len(out) == 1, out
        assert out[0]["signed"] is True
        assert out[0]["last_signoff_date"] == "2026-06-01", out
        assert out[0]["files"] == ["mod.py"], out


def test_scan_skips_an_unsigned_doc() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "draft.md").write_text("# Draft\n\nSee `mod.py`.\n", encoding="utf-8")
        assert run_scan([str(t)]) == []


def test_scan_excludes_ledger_table_references() -> None:
    """A row in the ledger's own table quoting a filename must not be
    mistaken for a doc reference — the extractor truncates at the heading."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "spec.md").write_text(
            "# Spec\n\n## Alpha\n\nSee `mod.py`.\n\n"
            "---\n\n## Revision History\n\n"
            "Signed off via viva review — 1 round, 1 section, 1 with comments. "
            "2026-06-01\n\n"
            "| Round | Section | Verdict | Note |\n"
            "|-------|---------|---------|------|\n"
            "| 1 | Alpha | changes | “drop `ledger_only.py`” |\n",
            encoding="utf-8")
        out = run_scan([str(t)])
        assert len(out) == 1, out
        assert out[0]["files"] == ["mod.py"], \
            "the ledger table's own filename must not appear in files[]"


def main() -> None:
    tests = [
        test_missing_file_flagged,
        test_existing_file_not_flagged,
        test_prose_only_section_skipped,
        test_missing_symbol_flagged,
        test_existing_symbol_not_flagged,
        test_version_string_not_treated_as_file,
        test_dotted_method_call_not_flagged,
        test_scan_finds_a_signed_doc_and_its_references,
        test_scan_skips_an_unsigned_doc,
        test_scan_excludes_ledger_table_references,
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
