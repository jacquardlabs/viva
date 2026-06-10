#!/usr/bin/env python3
"""Append a Revision History block to a signed-off doc from .viva round files.

Usage: revision_history.py VIVA_DIR DOC_PATH [DATE]

Reads review-input-rN.json / review-rN.json pairs, collects every
changes/info verdict with its note verbatim, and appends a summary line +
table under `## Revision History` (creating the heading on first use,
appending a new session block thereafter).
"""
import json
import sys
from datetime import date
from pathlib import Path

HEADING = "## Revision History"


def esc_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def collect(viva_dir: Path) -> tuple[list[dict], int, int]:
    """Return (entries, rounds_total, sections_total) from round file pairs."""
    rounds = sorted(
        int(p.stem.rsplit("-r", 1)[1])
        for p in viva_dir.glob("review-input-r*.json")
    )
    entries: list[dict] = []
    sections_total = 0
    for n in rounds:
        inp = json.loads((viva_dir / f"review-input-r{n}.json").read_text())
        out_path = viva_dir / f"review-r{n}.json"
        if not out_path.exists():
            continue
        out = json.loads(out_path.read_text())
        titles = {s["id"]: s.get("title", s["id"]) for s in inp.get("sections", [])}
        sections_total = max(sections_total, len(inp.get("sections", [])))
        entries.extend(
            {"round": n,
             "section": titles.get(s.get("id"), s.get("id", "?")),
             "verdict": s["verdict"],
             "note": s.get("note", "")}
            for s in out.get("sections", [])
            if s.get("verdict") in ("changes", "info")
        )
    return entries, len(rounds), sections_total


def build_block(entries: list[dict], rounds_total: int,
                sections_total: int, day: str) -> str:
    revised = len({e["section"] for e in entries})
    lines = [
        f"Signed off via viva review — {rounds_total} "
        f"round{'s' if rounds_total != 1 else ''}, {sections_total} "
        f"section{'s' if sections_total != 1 else ''}, {revised} revised. {day}"
    ]
    if entries:
        lines += ["", "| Round | Section | Verdict | Note |",
                  "|-------|---------|---------|------|"]
        lines += [
            f"| {e['round']} | {esc_cell(e['section'])} | {e['verdict']} "
            f"| {esc_cell(e['note']) or '—'} |"
            for e in entries
        ]
    return "\n".join(lines)


def append_history(viva_dir: Path, doc_path: Path, day: str) -> None:
    entries, rounds_total, sections_total = collect(viva_dir)
    block = build_block(entries, rounds_total, sections_total, day)
    doc = doc_path.read_text()
    if HEADING in doc:
        new_doc = doc.rstrip("\n") + "\n\n" + block + "\n"
    else:
        new_doc = doc.rstrip("\n") + f"\n\n---\n\n{HEADING}\n\n" + block + "\n"
    doc_path.write_text(new_doc)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    day = sys.argv[3] if len(sys.argv) > 3 else date.today().isoformat()
    append_history(Path(sys.argv[1]), Path(sys.argv[2]), day)
