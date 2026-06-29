#!/usr/bin/env python3
"""Append a Revision History block to a signed-off doc from .viva round files.

Usage: revision_history.py VIVA_DIR DOC_PATH [DATE]

Reads review-input-rN.json / review-rN.json pairs, collects every
changes/info verdict with its note verbatim, and appends a summary line +
table under `## Revision History` (creating the heading on first use,
appending a new session block thereafter).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import schema

HEADING = "## Revision History"


def esc_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def flat(text: str) -> str:
    """Collapse newlines for a single-line list item, verbatim otherwise."""
    return " ".join((text or "").split())


def collect_threads(viva_dir: Path) -> list[dict]:
    """Read the open-note store, return threads (with exchanges) in title order.

    The store is this session's `.viva/open-notes.json`, the single source of
    truth for notes that carried across rounds (issue #16). Absent or empty →
    no threads, so the ledger is byte-identical to a no-open-note session.
    """
    path = viva_dir / "open-notes.json"
    if not path.exists():
        return []
    try:
        store = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    threads = [t for t in store.values() if t.get("exchanges")]
    threads.sort(key=lambda t: ((t.get("title") or "").strip().lower(), t.get("cid", "")))
    return threads


def build_threads_block(threads: list[dict]) -> str:
    """Render open-note threads grouped by section heading, with quoted span."""
    lines = ["### Open notes", ""]
    current_title = None
    for t in threads:
        title = t.get("title", "")
        if title != current_title:
            lines.append(f"**{title}**")
            lines.append("")
            current_title = title
        status = t.get("status", "open")
        quote = t.get("quote", "")
        head = f"- _{flat(quote)}_ — {status}" if quote else f"- (whole section) — {status}"
        lines.append(head)
        for x in t.get("exchanges", []):
            note = flat(x.get("note", ""))
            resp = flat(x.get("response", ""))
            lines.append(f"  - R{x.get('round')} {x.get('verdict')}: {note}"
                         + (f" → {resp}" if resp else ""))
        lines.append("")
    return "\n".join(lines).rstrip()


def collect(viva_dir: Path) -> tuple[list[dict], int, int]:
    """Return (entries, rounds_total, sections_total) from round file pairs."""
    rounds = sorted(
        int(m.group(1))
        for p in viva_dir.glob("review-input-r*.json")
        if (m := re.fullmatch(r"review-input-r(\d+)", p.stem))
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
            e for s in out.get("sections", [])
            if (e := schema.verdict_to_ledger_entry(
                n, titles.get(s.get("id"), s.get("id", "?")), s)) is not None
        )
    return entries, len(rounds), sections_total


def build_block(entries: list[dict], rounds_total: int,
                sections_total: int, day: str) -> str:
    revised = len({e["section_title"] for e in entries})
    lines = [
        f"Signed off via viva review — {rounds_total} "
        f"round{'s' if rounds_total != 1 else ''}, {sections_total} "
        f"section{'s' if sections_total != 1 else ''}, {revised} revised. {day}"
    ]
    if entries:
        lines += ["", "| Round | Section | Verdict | Note |",
                  "|-------|---------|---------|------|"]
        lines += [
            f"| {e['round']} | {esc_cell(e['section_title'])} | {e['verdict']} "
            f"| {esc_cell(e['note']) or '—'} |"
            for e in entries
        ]
    return "\n".join(lines)


def append_history(viva_dir: Path, doc_path: Path, day: str) -> None:
    entries, rounds_total, sections_total = collect(viva_dir)
    if rounds_total == 0:
        sys.exit(f"no review round files found in {viva_dir}")
    block = build_block(entries, rounds_total, sections_total, day)
    threads = collect_threads(viva_dir)
    if threads:
        block = block + "\n\n" + build_threads_block(threads)
    doc = doc_path.read_text()
    if re.search(r"(?m)^## Revision History\s*$", doc):
        new_doc = doc.rstrip("\n") + "\n\n" + block + "\n"
    else:
        new_doc = doc.rstrip("\n") + f"\n\n---\n\n{HEADING}\n\n" + block + "\n"
    doc_path.write_text(new_doc)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    day = sys.argv[3] if len(sys.argv) > 3 else date.today().isoformat()
    append_history(Path(sys.argv[1]), Path(sys.argv[2]), day)
