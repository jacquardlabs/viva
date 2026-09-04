#!/usr/bin/env python3
"""Append a Revision History block to a signed-off doc from .viva round files.

Usage: revision_history.py --viva-dir .viva --doc doc.md [--date 2026-06-28]

Reads review-input-rN.json / review-rN.json pairs, collects every
changes/info verdict with its note verbatim, and appends a summary line +
table under `## Revision History` (creating the heading on first use,
appending a new session block thereafter).
"""
from __future__ import annotations

import argparse
import json
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
    """Read `.viva/open-notes.json` (#16), return threads (with exchanges)
    in title order. Absent or empty → no threads."""
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
        status = t.get("status", schema.THREAD_OPEN)
        # Same status→label map the web tab uses, not the bare enum value.
        label = schema.THREAD_STATUS_LABELS.get(status, status)
        quote = t.get("quote", "")
        head = f"- _{flat(quote)}_ — {label}" if quote else f"- (whole section) — {label}"
        lines.append(head)
        for x in t.get("exchanges", []):
            note = flat(x.get("note", ""))
            # Same `suggested:` tag schema._comment_fragment uses.
            repl = flat(x.get("replacement", ""))
            resp = flat(x.get("response", ""))
            # Key presence, not truthiness: no-grounds is still a decline.
            declined = ""
            if "grounds" in x:
                grounds = flat(x.get("grounds") or "")
                declined = f" — declined: {grounds}" if grounds else " — declined"
            lines.append(f"  - R{x.get('round', '?')} {x.get('verdict', '?')}: {note}"
                         + (f" — suggested: {repl}" if repl else "")
                         + declined
                         + (f" → {resp}" if resp else ""))
        lines.append("")
    return "\n".join(lines).rstrip()


def collect(viva_dir: Path) -> tuple[list[dict], int, int]:
    """Return (entries, rounds_total, sections_total) from round file pairs."""
    rounds = sorted(
        n for p in viva_dir.glob(schema.round_input_glob())
        if (n := schema.parse_round_input_stem(p.stem)) is not None
    )
    entries: list[dict] = []
    sections_total = 0
    for n in rounds:
        inp_path, out_path = schema.round_file_paths(viva_dir, n)
        inp = json.loads(inp_path.read_text())
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
    # `with comments`, not `revised` (#178) — an `info` question earns a
    # ledger row too, with no edit behind it.
    commented = len({e["section_title"] for e in entries})
    lines = [
        f"Signed off via viva review — {rounds_total} "
        f"round{'s' if rounds_total != 1 else ''}, {sections_total} "
        f"section{'s' if sections_total != 1 else ''}, "
        f"{commented} with comments. {day}"
    ]
    if entries:
        lines += ["", "| Round | Section | Verdict | Note |",
                  "|-------|---------|---------|------|"]
        lines += [
            # Curly-quoted, matching server.py's ledgerRowsHTML rendering.
            f"| {e['round']} | {esc_cell(e['section_title'])} | {e['verdict']} "
            f"| {('“' + esc_cell(e['note']) + '”') if esc_cell(e['note']) else '—'} |"
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
    if schema.has_revision_history(doc):
        new_doc = doc.rstrip("\n") + "\n\n" + block + "\n"
    else:
        new_doc = doc.rstrip("\n") + f"\n\n---\n\n{HEADING}\n\n" + block + "\n"
    doc_path.write_text(new_doc)


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Append a Revision History block to a signed-off doc "
                    "from .viva round files.")
    p.add_argument("--viva-dir", required=True,
                   help=".viva directory holding review-input-rN/review-rN pairs")
    p.add_argument("--doc", required=True,
                   help="the signed-off markdown doc to append to (modified in place)")
    p.add_argument("--date", default=None,
                   help="ISO date for the sign-off line (defaults to today)")
    args = p.parse_args()
    day = args.date or date.today().isoformat()
    append_history(Path(args.viva_dir), Path(args.doc), day)
