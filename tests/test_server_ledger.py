#!/usr/bin/env python3
"""Integration test: server accumulates a verbatim ledger across rounds."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, get_text, launch_server, post  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {
        "mode": "review",
        "doc_file": "doc.md",
        "round": 1,
        "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "goals body"},
            {"id": "s2", "title": "Error Handling", "content": "errors body"},
        ],
    }
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:

        # Round 1 has no ledger yet
        assert get(base, "/input").get("ledger") == [], "round 1 ledger must be empty"

        note = "Notifications should be within 30 seconds | with a pipe"
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "note": note},
            {"id": "s2", "verdict": "info", "note": "How long in the DLQ?"},
        ]})
        post(base, "/next-round?output=" + str(viva / "out2.json"), dict(r1, round=2))
        ledger = get(base, "/input")["ledger"]
        assert ledger == [
            {"round": 1, "section_title": "Goals", "verdict": "changes", "note": note},
            {"round": 1, "section_title": "Error Handling", "verdict": "info",
             "note": "How long in the DLQ?"},
        ], f"unexpected ledger: {ledger}"

        # approved adds nothing; changes with empty note still gets a row
        post(base, "/submit", {"round": 2, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "approved", "note": ""},
            {"id": "s2", "verdict": "changes", "note": ""},
        ]})
        post(base, "/next-round?output=" + str(viva / "out3.json"), dict(r1, round=3))
        ledger = get(base, "/input")["ledger"]
        assert len(ledger) == 3, f"expected 3 entries, got {len(ledger)}"
        assert ledger[2] == {"round": 2, "section_title": "Error Handling",
                             "verdict": "changes", "note": ""}
        page = get_text(base, "/")
        for needle in ('id="ledger"', 'id="complete-ledger"',
                       "function renderLedger", "function ledgerRowsHTML",
                       ".ledger-verdict.v-changes"):
            assert needle in page, f"page missing: {needle}"

        # round is coerced to int (stored-XSS hardening)
        post(base, "/submit", {"round": "<img src=x>", "submitted_early": False,
                               "sections": [{"id": "s1", "verdict": "info", "note": "n"}]})
        post(base, "/next-round?output=" + str(viva / "out4.json"), dict(r1, round=4))
        ledger = get(base, "/input")["ledger"]
        assert ledger[3]["round"] == 0, f"round not coerced: {ledger[3]}"

        print("OK")


if __name__ == "__main__":
    main()
