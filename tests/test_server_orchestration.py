#!/usr/bin/env python3
"""Orchestration smoke test: the round-1 and round-2 CLI sequences from SKILL.md.

Every other server test hand-writes a `review-input` JSON and feeds it in. This
one drives the real agent-side pipeline instead — `parse_sections.py` produces
the round file, `server.py` serves it, a verdict comes back, and round 2 is
re-parsed with the prior round's files and pushed via `/next-round`. It's the
guard against SKILL.md's documented flag sequences drifting from what the scripts
actually accept, and against the approved-carry-forward contract breaking.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, launch_server, poll_for, post  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PARSE = ROOT / "scripts" / "parse_sections.py"

DOC = "## Goals\n\nShip the core.\n\n## Scope\n\nJust the core, nothing more.\n"


def parse(doc, output, round_num, viva, prior=None):
    """Run parse_sections.py exactly as SKILL.md does; return the written JSON."""
    cmd = [sys.executable, str(PARSE), str(doc),
           "--output", str(output), "--round", str(round_num), "--doc-file", "doc.md"]
    if prior:
        cmd += ["--prior-input", str(prior[0]), "--prior-verdicts", str(prior[1])]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, f"parse_sections failed:\n{r.stderr}"
    return json.loads(Path(output).read_text())


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    doc = tmp / "doc.md"
    doc.write_text(DOC)

    # ── Round 1: parse the doc into a review-input ───────────────────────────
    r1_in = viva / "review-input-r1.json"
    r1_out = viva / "review-r1.json"
    data = parse(doc, r1_in, 1, viva)
    assert data["round"] == 1 and data["mode"] == "review", data
    titles = [s["title"] for s in data["sections"]]
    assert titles == ["Goals", "Scope"], titles
    assert data["approved_ids"] == [], "round 1 approves nothing"
    ids = {s["title"]: s["id"] for s in data["sections"]}

    # ── Serve round 1, submit one approve + one changes ──────────────────────
    with launch_server(r1_in, r1_out, cwd=tmp) as base:
        served = get(base, "/input")
        assert [s["title"] for s in served["sections"]] == ["Goals", "Scope"], served
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": ids["Goals"], "verdict": "approved"},
            {"id": ids["Scope"], "verdict": "changes",
             "comments": [{"cid": ids["Scope"] + "-c1", "type": "changes",
                           "note": "name the non-goals too"}]},
        ]})
        assert poll_for(r1_out), "review-r1.json never written"

        # ── Round 2: re-parse with the prior round's files ───────────────────
        r2_in = viva / "review-input-r2.json"
        r2_out = viva / "review-r2.json"
        data2 = parse(doc, r2_in, 2, viva, prior=(r1_in, r1_out))
        assert data2["round"] == 2, data2
        # Goals was approved in round 1 and its content is unchanged → carried.
        assert ids["Goals"] in data2["approved_ids"], \
            f"approved section not carried forward: {data2['approved_ids']}"
        assert ids["Scope"] not in data2["approved_ids"], "changed section must not carry"

        # ── Push round 2 to the running server ───────────────────────────────
        post(base, "/next-round?output=" + str(r2_out), data2)
        served2 = get(base, "/input")
        assert served2["round"] == 2, served2
        assert ids["Goals"] in served2["approved_ids"], served2

    print("OK")


if __name__ == "__main__":
    main()
