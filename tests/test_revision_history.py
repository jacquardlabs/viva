#!/usr/bin/env python3
"""revision_history.py builds the doc appendix verbatim from .viva round files."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "revision_history.py"


def write_round(viva: Path, n: int, sections: list, verdicts: list) -> None:
    (viva / f"review-input-r{n}.json").write_text(json.dumps(
        {"mode": "review", "doc_file": "doc.md", "round": n,
         "approved_ids": [], "sections": sections}))
    (viva / f"review-r{n}.json").write_text(json.dumps(
        {"round": n, "submitted_early": False, "sections": verdicts}))


def run(viva: Path, doc: Path) -> None:
    subprocess.run([sys.executable, str(SCRIPT), str(viva), str(doc), "2026-06-09"],
                   check=True)


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    doc = tmp / "doc.md"
    doc.write_text("# Doc\n\n## Goals\n\nbody\n")

    secs = [{"id": "s1", "title": "Goals", "content": "g"},
            {"id": "s2", "title": "Error Handling", "content": "e"}]
    write_round(viva, 1, secs, [
        {"id": "s1", "verdict": "changes", "note": "Use 30s | not 60s"},
        {"id": "s2", "verdict": "pending", "note": ""},
    ])
    write_round(viva, 2, secs, [
        {"id": "s1", "verdict": "approved", "note": ""},
        {"id": "s2", "verdict": "info", "note": "DLQ retention?"},
    ])

    run(viva, doc)
    text = doc.read_text()
    assert text.count("## Revision History") == 1
    assert "Signed off via viva review — 2 rounds, 2 sections, 2 revised. 2026-06-09" in text
    assert "| 1 | Goals | changes | Use 30s \\| not 60s |" in text
    assert "| 2 | Error Handling | info | DLQ retention? |" in text

    # Second sign-off session appends its own block under the same heading
    run(viva, doc)
    text = doc.read_text()
    assert text.count("## Revision History") == 1
    assert text.count("Signed off via viva review") == 2

    # Zero-feedback session: summary line only, no table
    viva2 = tmp / ".viva2"
    viva2.mkdir()
    doc2 = tmp / "doc2.md"
    doc2.write_text("# Doc2\n\nbody\n")
    write_round(viva2, 1, secs, [
        {"id": "s1", "verdict": "approved", "note": ""},
        {"id": "s2", "verdict": "approved", "note": ""},
    ])
    run(viva2, doc2)
    text2 = doc2.read_text()
    assert "0 revised" in text2
    assert "| Round |" not in text2

    # Fix 1: H3 heading must not be treated as existing ## Revision History
    viva3 = tmp / ".viva3"
    viva3.mkdir()
    doc3 = tmp / "doc3.md"
    doc3.write_text("# Doc3\n\n### Revision History\n\nsome existing h3 content\n")
    write_round(viva3, 1, secs, [
        {"id": "s1", "verdict": "approved", "note": ""},
        {"id": "s2", "verdict": "approved", "note": ""},
    ])
    run(viva3, doc3)
    text3 = doc3.read_text()
    # Must have exactly one real ## Revision History heading
    assert text3.count("\n## Revision History\n") == 1, (
        f"Expected one real ## Revision History heading; got:\n{text3}"
    )

    # Fix 2: stray backup file must not crash collect()
    viva4 = tmp / ".viva4"
    viva4.mkdir()
    doc4 = tmp / "doc4.md"
    doc4.write_text("# Doc4\n\nbody\n")
    write_round(viva4, 1, secs, [
        {"id": "s1", "verdict": "approved", "note": ""},
        {"id": "s2", "verdict": "approved", "note": ""},
    ])
    # Drop a stray backup file that should be ignored
    (viva4 / "review-input-r1-backup.json").write_text(json.dumps({"stray": True}))
    run(viva4, doc4)  # must not raise
    text4 = doc4.read_text()
    assert "## Revision History" in text4, "Expected history block to be written"

    # Fix 3: empty .viva dir → nonzero exit, doc untouched
    viva5 = tmp / ".viva5"
    viva5.mkdir()
    doc5 = tmp / "doc5.md"
    original5 = "# Doc5\n\nbody\n"
    doc5.write_text(original5)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(viva5), str(doc5), "2026-06-09"],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        f"Expected nonzero exit for empty viva dir; got 0. stderr={result.stderr!r}"
    )
    assert doc5.read_text() == original5, "Doc must be untouched when viva dir is empty"

    print("OK")


if __name__ == "__main__":
    main()
