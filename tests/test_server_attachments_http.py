#!/usr/bin/env python3
"""Integration: /submit writes image files and rewrites output JSON."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fixtures import PNG, PNG_B64  # noqa: E402
from _server_harness import launch_server, post  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "body"}]}
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:

        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "note": "look",
             "images": [{"data": PNG_B64, "mime": "image/png"}]},
        ]})

        out = json.loads((viva / "out1.json").read_text())
        sec = out["sections"][0]
        assert "images" not in sec, "raw base64 must not reach the output JSON"
        assert sec["attachments"] == [str(viva / "attachments" / "r1-s1-0.png")], sec
        assert Path(sec["attachments"][0]).read_bytes() == PNG, "file bytes match"
        print("OK")


if __name__ == "__main__":
    main()
