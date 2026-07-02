#!/usr/bin/env python3
"""Integration test: the server validates review verdicts at the /submit boundary.

schema.validate_verdicts is wired into the POST /submit handler (gated on a
`sections` payload so Q&A is unaffected). A structurally invalid verdict must be
rejected with 400 before it can corrupt the ledger or output; a valid one passes.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import launch_server, post_status  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "body"}]}
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:

        # Unknown verdict → rejected at the boundary
        bad = {"round": 1, "submitted_early": False,
               "sections": [{"id": "s1", "verdict": "bogus"}]}
        assert post_status(base, "/submit", bad) == 400, "invalid verdict must be 400"

        # Section missing an id → rejected
        bad2 = {"round": 1, "submitted_early": False,
                "sections": [{"verdict": "approved"}]}
        assert post_status(base, "/submit", bad2) == 400, "missing id must be 400"

        # Valid verdicts → accepted
        good = {"round": 1, "submitted_early": False,
                "sections": [{"id": "s1", "verdict": "approved"}]}
        assert post_status(base, "/submit", good) == 200, "valid submit must be 200"

        print("OK")


if __name__ == "__main__":
    main()
