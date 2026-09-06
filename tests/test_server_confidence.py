#!/usr/bin/env python3
"""Integration test: confidence triage (#12), via a `kind:"confidence"`
annotation carrying `basis`/`level`. Contract: GET /input preserves it
verbatim, and the page ships weakest-first sort keyed on those fields.

Also covers #145's `source` field: it must pass through `/input` verbatim
and the page must render it as hover text on the confidence row, with no
change to a section carrying no `source` (principle 4).
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
            "level": "low", "message": "inferred · low",
            "source": "no config or doc found naming a TTL"}
    conf_no_source = {"kind": "confidence", "severity": "info", "basis": "sourced",
                       "level": "high", "message": "sourced · high"}
    r1 = {
        "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "g", "annotations": [conf]},
            {"id": "s2", "title": "Scope", "content": "s",
             "annotations": [conf_no_source]},
        ],
    }
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:

        # Pass-through: the structured confidence annotation survives verbatim,
        # source included.
        data = get(base, "/input")
        s1 = next(s for s in data["sections"] if s["id"] == "s1")
        assert s1["annotations"][0] == conf, f"confidence annotation dropped: {s1}"
        s2 = next(s for s in data["sections"] if s["id"] == "s2")
        assert "source" not in s2["annotations"][0], \
            f"absent source must not appear: {s2}"

        # Page ships the sort toggle + weakness scoring keyed on basis/level,
        # and renders `source` as hover text on the confidence row.
        page = get_text(base, "/")
        for needle in ("weaknessScore", "sortMode", "applyCardSort",
                       "sort-toggle", "'confidence'", "basis", "level",
                       "conf.source"):
            assert needle in page, f"page missing: {needle}"

        print("OK")


if __name__ == "__main__":
    main()
