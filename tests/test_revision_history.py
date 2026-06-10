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
    print("OK")


if __name__ == "__main__":
    main()
