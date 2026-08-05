#!/usr/bin/env python3
"""Orchestration smoke test: one real session driven the way the agent drives it.

Every other server test hand-writes a `review-input` JSON and feeds it in. This
one drives the real agent-side pipeline instead — `parse_sections.py` produces
round 1, `server.py` serves it, a verdict comes back, and every round after that
goes through `scripts/loop.py`: `rearm` re-parses and POSTs `/next-round`,
`rearm --parse-only` stops at the producer seam, and `arm` closes it. It is the
guard against the driver's round-2+ sequence drifting from what the scripts and
the server actually accept, and against the approved-carry-forward contract
breaking underneath it.

The two rules `loop.py` exists to keep are asserted here as well: the round
number is derived from disk (no subcommand accepts one), and the driver
cross-imports no sibling but `schema.py` (CLAUDE.md).
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, launch_server, poll_for, post  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PARSE = ROOT / "scripts" / "parse_sections.py"
LOOP = ROOT / "scripts" / "loop.py"

DOC = "## Goals\n\nShip the core.\n\n## Scope\n\nJust the core, nothing more.\n"

# A task-card plan: `### Task N` cards with a `## Notes` block recurring inside
# each one. Auto-detection picks the coarser repeater (`## Notes`) and swallows
# both tasks, so the two splits are visibly different — which is what makes
# "round 2 re-split the same way" a real assertion rather than a coincidence.
PLAN = (
    "# Sprint plan\n\nIntro paragraph.\n\n"
    "### Task 1: ship the flag\n\nbody one\n\n"
    "## Notes\n\nnote one\n\n"
    "### Task 2: write the test\n\nbody two\n\n"
    "## Notes\n\nnote two\n"
)
SPLIT = r"^Task \d+"

# `loop.py start` arms round 1 by launching the server the way the agent does —
# and for a human that means opening a browser tab. `$BROWSER` is registered
# preferred by `webbrowser`, so pointing it at a no-op keeps the test headless.
os.environ["BROWSER"] = "true"


def parse(doc, output, round_num, viva, prior=None):
    """Run parse_sections.py exactly as `loop.py start` does; return the JSON."""
    cmd = [sys.executable, str(PARSE), str(doc),
           "--output", str(output), "--round", str(round_num), "--doc-file", "doc.md"]
    if prior:
        cmd += ["--prior-input", str(prior[0]), "--prior-verdicts", str(prior[1])]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, f"parse_sections failed:\n{r.stderr}"
    return json.loads(Path(output).read_text())


def loop(viva, cwd, *argv):
    """Run a `loop.py` subcommand against `viva`, from `cwd` (the doc's dir —
    `doc_file` is recorded relative, as the agent passes it)."""
    return subprocess.run(
        [sys.executable, str(LOOP), "--viva-dir", str(viva)] + list(argv),
        capture_output=True, text=True, cwd=str(cwd))


def check_round_trip() -> None:
    """Round 1 by hand, every round after it through the driver."""
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
                           "note": "name the non-goals too",
                           "open": True, "settled": False}]},
        ]})
        assert poll_for(r1_out), "review-r1.json never written"

        # ── Round 2: `rearm` settles the thread, re-parses, and POSTs ────────
        # No round number, no doc path, no curl — the driver derives all three.
        r = loop(viva, tmp, "rearm",
                 "--response", ids["Scope"] + "-c1=named the non-goals")
        assert r.returncode == 0, f"loop rearm failed:\n{r.stderr}"

        r2_in = viva / "review-input-r2.json"
        assert r2_in.exists(), "rearm must re-parse the next round"
        data2 = json.loads(r2_in.read_text())
        assert data2["round"] == 2, data2
        # Goals was approved in round 1 and its content is unchanged → carried.
        assert ids["Goals"] in data2["approved_ids"], \
            f"approved section not carried forward: {data2['approved_ids']}"
        assert ids["Scope"] not in data2["approved_ids"], "changed section must not carry"

        # The agent's response landed in the open-note thread, keyed by cid.
        store = json.loads((viva / "open-notes.json").read_text())
        thread = store[ids["Scope"] + "-c1"]
        assert thread["exchanges"][-1]["response"] == "named the non-goals", thread

        # …and the running server is now serving it.
        served2 = get(base, "/input")
        assert served2["round"] == 2, served2
        assert ids["Goals"] in served2["approved_ids"], served2

        # ── Round 3: `--parse-only` stops at the producer seam ───────────────
        r2_out = viva / "review-r2.json"
        post(base, "/submit", {"round": 2, "submitted_early": False, "sections": [
            {"id": ids["Goals"], "verdict": "approved"},
            {"id": ids["Scope"], "verdict": "changes",
             "comments": [{"cid": ids["Scope"] + "-c2", "type": "changes",
                           "note": "shorter", "open": True, "settled": False}]},
        ]})
        assert poll_for(r2_out), "review-r2.json never written — rearm's `output` missed"

        r = loop(viva, tmp, "rearm", "--parse-only")
        assert r.returncode == 0, f"loop rearm --parse-only failed:\n{r.stderr}"
        assert (viva / "review-input-r3.json").exists(), \
            "--parse-only must still re-parse the next round"
        assert get(base, "/input")["round"] == 2, \
            "--parse-only must stop before arming — the server still holds round 2"

        # The seam closes with `arm`, on the round it reads off disk.
        r = loop(viva, tmp, "arm")
        assert r.returncode == 0, f"loop arm failed:\n{r.stderr}"
        assert get(base, "/input")["round"] == 3, "arm must ship the parsed round"


def check_split_on_session() -> None:
    """A task-card plan review, driven start → submit → rearm by the driver.

    `--split-on` is the flag studious's planning contract makes mandatory for a
    `PLAN.md` round, so the driver has to carry it — and carry it *forward*: the
    pattern is recorded in the round file, and `rearm` reads it back rather than
    asking the agent to re-type it. Round 2 asserting the same split, the same
    ids, and the carried approvals is what proves the round-trip.
    """
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    doc = tmp / "PLAN.md"
    doc.write_text(PLAN)

    # The discriminator, established first: without the pattern this doc splits
    # on `## Notes`, not on tasks.
    auto = parse(doc, tmp / "auto.json", 1, viva)
    assert [s["title"] for s in auto["sections"]] == \
        ["Sprint plan", "Notes", "Notes"], auto["sections"]

    r = loop(viva, tmp, "start", "--doc", "PLAN.md", "--split-on", SPLIT)
    assert r.returncode == 0, "loop start --split-on failed:\n%s" % r.stderr
    try:
        data = json.loads((viva / "review-input-r1.json").read_text())
        assert data["split_on"] == SPLIT, data.get("split_on")
        titles = [s["title"] for s in data["sections"]]
        assert titles == ["Sprint plan", "Task 1: ship the flag",
                          "Task 2: write the test"], titles
        ids = {s["title"]: s["id"] for s in data["sections"]}

        base = (viva / "server.url").read_text().strip()
        assert get(base, "/input")["round"] == 1

        task2_cid = ids["Task 2: write the test"] + "-c1"
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": ids["Sprint plan"], "verdict": "approved"},
            {"id": ids["Task 1: ship the flag"], "verdict": "approved"},
            {"id": ids["Task 2: write the test"], "verdict": "changes",
             "comments": [{"cid": task2_cid, "type": "changes",
                           "note": "name the fixture",
                           "open": True, "settled": False}]},
        ]})
        assert poll_for(viva / "review-r1.json"), "review-r1.json never written"

        # The agent's rewrite between rounds — only Task 2's body moves.
        doc.write_text(PLAN.replace("body two", "body two, naming the fixture"))

        r = loop(viva, tmp, "rearm", "--response", task2_cid + "=named the fixture")
        assert r.returncode == 0, "loop rearm failed:\n%s" % r.stderr

        r2 = json.loads((viva / "review-input-r2.json").read_text())
        # Carried, not consumed: round 2 records it too, so round 3's rearm
        # re-splits the same way rather than silently falling back.
        assert r2["split_on"] == SPLIT, r2.get("split_on")
        assert [s["title"] for s in r2["sections"]] == titles, r2["sections"]
        assert {s["title"]: s["id"] for s in r2["sections"]} == ids, \
            "section ids must be stable across a --split-on re-parse"
        assert ids["Sprint plan"] in r2["approved_ids"], r2["approved_ids"]
        assert ids["Task 1: ship the flag"] in r2["approved_ids"], r2["approved_ids"]
        assert ids["Task 2: write the test"] not in r2["approved_ids"], \
            "the rewritten task must come back for re-review"
        assert get(base, "/input")["round"] == 2, "rearm must ship round 2"
    finally:
        # `start` detaches the server, so nothing in this process holds a
        # handle on it — the driver's own exit is the teardown.
        loop(viva, tmp, "abandon")


def check_no_subcommand_takes_a_round() -> None:
    """The counter nobody holds: no subcommand accepts a round argument.

    Enumerated from `--help` rather than hardcoded, so a later eighth
    subcommand is not silently exempt. Each is run with its own required flags
    satisfied (pointed at a nonexistent path — the parse is what's under test,
    never the handler) so argparse gets far enough to reject `--round` itself
    instead of stopping at a missing required argument. Every run is aimed at a
    throwaway `--viva-dir`, so the day this assertion regresses it fails without
    a handler having reached anyone's real `.viva/`.
    """
    sandbox = Path(tempfile.mkdtemp()) / ".viva"
    top = subprocess.run([sys.executable, str(LOOP), "--help"],
                         capture_output=True, text=True)
    assert top.returncode == 0, top.stderr
    listed = re.search(r"\{([a-z,]+)\}", top.stdout)
    assert listed, "could not read the subcommand list from --help:\n" + top.stdout
    names = listed.group(1).split(",")
    assert set(names) >= {"start", "annotate", "arm", "wait", "rearm",
                          "finish", "abandon"}, names

    for name in names:
        h = subprocess.run([sys.executable, str(LOOP), name, "--help"],
                           capture_output=True, text=True)
        assert h.returncode == 0, h.stderr
        assert "--round" not in h.stdout, \
            "`%s` exposes a round argument — the round is derived, never passed" % name
        required = [flag for flag in ("--doc", "--sidecar") if flag in h.stdout]
        argv = [sys.executable, str(LOOP), "--viva-dir", str(sandbox), name]
        for flag in required:
            argv += [flag, "/nonexistent/for-parse-only"]
        r = subprocess.run(argv + ["--round", "2"], capture_output=True, text=True)
        assert r.returncode != 0 and "unrecognized arguments" in r.stderr, \
            "`%s --round 2` must be rejected — got %d %r" % (name, r.returncode, r.stderr)


def check_loop_cross_imports_only_schema() -> None:
    """CLAUDE.md's one-cross-import rule, asserted on the driver.

    `loop.py` orchestrates its siblings, so it invokes them as subprocesses;
    `schema.py` stays the single module it may import.
    """
    siblings = {p.stem for p in (ROOT / "scripts").glob("*.py")} - {"loop"}
    imported = set()
    for node in ast.walk(ast.parse(LOOP.read_text())):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported & siblings == {"schema"}, \
        "loop.py may cross-import schema and nothing else — found %r" \
        % sorted(imported & siblings)


def main() -> None:
    check_round_trip()
    check_split_on_session()
    check_no_subcommand_takes_a_round()
    check_loop_cross_imports_only_schema()
    print("OK")


if __name__ == "__main__":
    main()
