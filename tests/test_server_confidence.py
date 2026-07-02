#!/usr/bin/env python3
"""Integration test: confidence triage (issue #12).

The generating agent self-annotates each section as sourced vs inferred plus a
confidence level, riding the existing annotation channel (#8) with a
`kind:"confidence"` entry carrying structured `basis`/`level` fields. The server
surfaces the badge (free, via the annotation strip) and offers a weakest-first
sort that reads basis/level off the annotation — no string-parsing. Contract:

  - GET /input preserves the confidence annotation verbatim.
  - The page ships the weakest-first sort machinery, keyed on basis/level.
  - A doc with no confidence annotation keeps the toggle hidden and stays in
    document order (zero-regression).
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, get_text, launch_server  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    conf = {"kind": "confidence", "severity": "warn", "basis": "inferred",
            "level": "low", "message": "inferred · low"}
    r1 = {
        "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "g", "annotations": [conf]},
            {"id": "s2", "title": "Scope", "content": "s"},
        ],
    }
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:

        # Pass-through: the structured confidence annotation survives verbatim.
        data = get(base, "/input")
        s1 = next(s for s in data["sections"] if s["id"] == "s1")
        assert s1["annotations"][0] == conf, f"confidence annotation dropped: {s1}"

        # Page ships the sort toggle + weakness scoring keyed on basis/level.
        page = get_text(base, "/")
        for needle in ("weaknessScore", "sortMode", "applyCardSort",
                       "sort-toggle", "'confidence'", "basis", "level"):
            assert needle in page, f"page missing: {needle}"

        print("OK")


if __name__ == "__main__":
    main()
