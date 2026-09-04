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

The last five checks guard the other half of the same contract — the skill prose
itself. The driver only removes bookkeeping from the agent if the prose stops
carrying it, so the documented sequence is asserted here beside the executed
one: no bash block in the part `loop.py` drives does the driver's job, the
rewrite step still applies standing preferences, nothing is auto-approved, a
suggested edit is applied verbatim, and every `references/` file is one some
reader is handed the path to.

**The bookkeeping rule is scoped, and the scope is the point (#170).** `loop.py`
drives doc review and the intake interview, so `/viva-review`'s branch A and all
of `/viva-write` are held to it. `/viva-review` branch B (hunks — `parse_diff.py`
and `--mode diff`, neither of which the driver knows) carries that bash
deliberately. It is enumerated below rather than exempted by a wildcard, so a
SECOND skill growing its own loop fails this test — and when #179's remaining
half extends the driver to hunks, the enumeration is the list to empty.
"""
import ast
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, launch_server, poll_for, post  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PARSE = ROOT / "scripts" / "parse_sections.py"
LOOP = ROOT / "scripts" / "loop.py"
SKILLS_DIR = ROOT / ".claude" / "skills"
SKILL = SKILLS_DIR / "viva-review" / "SKILL.md"       # the driver's own skill
WRITE_SKILL = SKILLS_DIR / "viva-write" / "SKILL.md"
REFERENCES = ROOT / "references"

# The one part of the prose `loop.py` actually drives. Branch B is a different
# parser and a different `--mode`, so the driver has nothing to offer it yet.
DRIVEN_SECTION = ("## A. Doc review", "## B. Diff review")

BASH_BLOCK_RE = re.compile(r"```bash\n(.*?)```", re.S)

# Content-based, not fence-typed: a `/next-round` POST written with
# `python3 -c urllib` is the same defect wearing a different hat. Scoped to
# ```bash blocks because the File Layout tree is a plain fenced block — a map of
# `.viva/` is orientation for a human reader, not a step the agent executes, so
# it keeps its round-file names. The $VIVA_DIR resolve block names `server.py`
# without running it, which is why "launches" is an invocation pattern rather
# than a bare filename match.
FORBIDDEN_BASH = [
    ("launches a server", re.compile(r"\bpython3?\b[^\n]*\bserver\.py\b")),
    ("POSTs an endpoint",
     re.compile(r"\bcurl\b|/next-round|/complete|/submit\b|/abandon")),
    ("names a round file", re.compile(r"review-input-r|review-r")),
]

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


def assert_printed_references_exist(stdout: str) -> None:
    """Every `references/` path a subcommand prints must be a file on disk.

    The static half of reachability lives in `check_references_are_reachable`;
    this is the runtime half — the string the agent is actually handed.
    """
    hits = re.findall(r"(\S*/references/\S+)", stdout)
    assert hits, "expected a references path in:\n" + stdout
    for hit in hits:
        assert Path(hit).is_file(), "loop.py printed a dangling path: %s" % hit


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

        # `wait` returns at once — the verdicts are already on disk — and routes
        # the agent: the classification line, plus the reference documenting the
        # step that classification sends it to.
        r = loop(viva, tmp, "wait")
        assert r.returncode == 0, f"loop wait failed:\n{r.stderr}"
        assert "=== round 1: has-work ===" in r.stdout, r.stdout
        assert_printed_references_exist(r.stdout)

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
        assert str(viva / "review-input-r3.json") in r.stdout, \
            "the seam must name the round file a producer's --input needs — " \
            "otherwise the agent is back to computing review-input-r{N}.json"
        assert_printed_references_exist(r.stdout)
        assert get(base, "/input")["round"] == 2, \
            "--parse-only must stop before arming — the server still holds round 2"

        # The seam closes with `arm`, on the round it reads off disk.
        r = loop(viva, tmp, "arm")
        assert r.returncode == 0, f"loop arm failed:\n{r.stderr}"
        assert get(base, "/input")["round"] == 3, "arm must ship the parsed round"

        # ── Sign-off: an all-approved round routes to `finish` ───────────────
        r3_out = viva / "review-r3.json"
        post(base, "/submit", {"round": 3, "submitted_early": False, "sections": [
            {"id": ids["Goals"], "verdict": "approved"},
            {"id": ids["Scope"], "verdict": "approved"},
        ]})
        assert poll_for(r3_out), "review-r3.json never written"

        r = loop(viva, tmp, "wait")
        assert r.returncode == 0, f"loop wait failed:\n{r.stderr}"
        assert "=== round 3: all-approved ===" in r.stdout, r.stdout

        r = loop(viva, tmp, "finish", "--doc", "doc.md")
        assert r.returncode == 0, f"loop finish failed:\n{r.stderr}"
        assert "## Revision History" in doc.read_text(), \
            "finish must append the ledger to the doc"
        assert_printed_references_exist(r.stdout)


def check_split_on_session() -> None:
    """A task-card plan review, driven start → submit → rearm by the driver.

    `--split-on` is what makes a `PLAN.md` round split on its task cards rather
    than on its own top-level headings, so the driver has to carry it — and
    carry it *forward*: the pattern is recorded in the round file, and `rearm`
    reads it back rather than asking the agent to re-type it. Round 2 asserting
    the same split, the same ids, and the carried approvals is what proves the
    round-trip.
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


def check_doc_type_session() -> None:
    """`--type` resolves where the name enters the system, then carries.

    The type names the round's check set, so a name that resolves to nothing has
    to be refused before any state is cleared — and once recorded it must reach
    round 2 the way `split_on` does, or a typed session silently becomes untyped
    at the first re-parse. No server here: `--parse-only` at both ends keeps the
    carry-forward the only thing under test.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "design.md"
        doc.write_text("# Design: a thing\n\n## Problem & persona\n\nwho\n\n"
                       "## Proposed design\n\nwhat\n")

        r = loop(viva, td, "start", "--doc", "design.md", "--type", "no-such-type")
        assert r.returncode != 0, "an unresolvable --type must not start a session"
        assert "unknown doc type" in r.stderr, r.stderr
        assert not (viva / "review-input-r1.json").exists(), \
            "the refusal must land before round 1 is parsed"

        r = loop(viva, td, "start", "--doc", "design.md",
                 "--type", "design-doc", "--parse-only")
        assert r.returncode == 0, r.stderr
        assert "checks: headings-present" in r.stdout, (
            "the type's check set must be named where it resolves — a check "
            "nobody is told about never runs:\n%s" % r.stdout)
        r1 = json.loads((viva / "review-input-r1.json").read_text())
        assert r1["doc_type"] == "design-doc", r1.get("doc_type")

        ids = [s["id"] for s in r1["sections"]]
        (viva / "review-r1.json").write_text(json.dumps(
            {"round": 1, "submitted_early": False,
             "sections": [{"id": i, "verdict": "approved"} for i in ids]}))

        r = loop(viva, td, "rearm", "--parse-only")
        assert r.returncode == 0, r.stderr
        r2 = json.loads((viva / "review-input-r2.json").read_text())
        assert r2["doc_type"] == "design-doc", (
            "rearm dropped the type — round 2 re-parsed as untyped: %s"
            % r2.get("doc_type"))
    print("  ok  check_doc_type_session")


def check_untyped_session_records_no_doc_type() -> None:
    """No `--type`, no key: an untyped round file stays byte-identical to what
    it was before the field existed, which is what tells `rearm` there is
    nothing to carry."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "d.md"
        doc.write_text("# T\n\n## A\n\naaa\n\n## B\n\nbbb\n")
        r = loop(viva, td, "start", "--doc", "d.md", "--parse-only")
        assert r.returncode == 0, r.stderr
        data = json.loads((viva / "review-input-r1.json").read_text())
        assert "doc_type" not in data, data
        assert "pass" not in data, (
            "and no pass either — absent is what keeps `round_is_complete` on "
            "the base rule: %s" % data.get("pass"))
    print("  ok  check_untyped_session_records_no_doc_type")


def check_pass_carries_within_a_session_not_across_a_resume() -> None:
    """The pass is round state `rearm` carries and a resume deliberately drops.

    Depth is the one round parameter a caller changes mid-session (structural,
    then line, then checks), so `rearm` takes an override the split pattern
    and the type have no use for — but round N+1 runs round N's depth when it is
    given none, or every later round silently falls back to the base rule. A
    resume is the opposite case: it is a new review of a changed doc, and
    inheriting the prior session's finishing pass would add a conjunct nobody
    asked for.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "d.md"
        body = "# T\n\n## A\n\naaa\n\n## B\n\nbbb\n"
        doc.write_text(body)

        r = loop(viva, td, "start", "--doc", "d.md",
                 "--pass", "architecture", "--posture", "hard", "--parse-only")
        assert r.returncode == 0, r.stderr
        r1 = json.loads((viva / "review-input-r1.json").read_text())
        assert r1["pass"] == {"kind": "architecture", "posture": "hard"}, r1.get("pass")

        ids = [s["id"] for s in r1["sections"]]
        approved = json.dumps({"round": 1, "submitted_early": False,
                               "sections": [{"id": i, "verdict": "approved"}
                                            for i in ids]})
        (viva / "review-r1.json").write_text(approved)

        # No override: round 2 runs at round 1's depth and posture.
        r = loop(viva, td, "rearm", "--parse-only")
        assert r.returncode == 0, r.stderr
        r2 = json.loads((viva / "review-input-r2.json").read_text())
        assert r2["pass"] == {"kind": "architecture", "posture": "hard"}, (
            "rearm dropped the pass — round 2 fell back to the base rule: %s"
            % r2.get("pass"))

        # Override: the named kind wins, and it does not inherit the carried
        # posture — `--pass` names the whole pass.
        (viva / "review-r2.json").write_text(approved)
        r = loop(viva, td, "rearm", "--pass", "checks", "--parse-only")
        assert r.returncode == 0, r.stderr
        r3 = json.loads((viva / "review-input-r3.json").read_text())
        assert r3["pass"] == {"kind": "checks"}, r3.get("pass")

        # Resume: sign the doc off, start again, and the pass must be gone.
        (viva / "review-r3.json").write_text(approved)
        doc.write_text(body + "\n---\n\n## Revision History\n\nsigned off\n")
        r = loop(viva, td, "start", "--doc", "d.md", "--parse-only")
        assert r.returncode == 0, r.stderr
        resumed = json.loads((viva / "review-input-r1.json").read_text())
        assert "pass" not in resumed, (
            "a resume must not inherit the prior session's depth — that adds a "
            "conjunct nobody asked for: %s" % resumed.get("pass"))
        assert resumed.get("approved_ids"), "the resume carried no approvals"
    print("  ok  check_pass_carries_within_a_session_not_across_a_resume")


def check_resume_warns_on_a_type_that_no_longer_resolves() -> None:
    """A carried type is resolved like any other, but not fatally.

    The name comes off the prior round file, by which point the scratch
    carry-forward pair is already on disk — dying there would strand it and
    make a repo that dropped a `.viva-types/` bundle unable to resume at all.
    So: a warning, the type still recorded, and the resume proceeds."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "d.md"
        body = "# T\n\n## A\n\naaa\n\n## B\n\nbbb\n"
        doc.write_text(body)
        subprocess.run(
            [sys.executable, str(PARSE), str(doc), "--output",
             str(viva / "review-input-r1.json"), "--round", "1",
             "--doc-file", str(doc), "--doc-type", "gone-type"],
            check=True, capture_output=True)
        first = json.loads((viva / "review-input-r1.json").read_text())
        (viva / "review-r1.json").write_text(json.dumps(
            {"round": 1, "submitted_early": False,
             "sections": [{"id": s["id"], "verdict": "approved"}
                          for s in first["sections"]]}))
        doc.write_text(body + "\n---\n\n## Revision History\n\nsigned off\n")

        r = loop(viva, td, "start", "--doc", str(doc), "--parse-only")
        assert r.returncode == 0, (
            "an unresolvable carried type must not block the resume:\n%s"
            % r.stderr)
        assert "unknown doc type" in r.stderr, r.stderr
        second = json.loads((viva / "review-input-r1.json").read_text())
        assert second.get("doc_type") == "gone-type", (
            "the resume must keep the recorded type rather than silently "
            "untyping the session: %s" % second.get("doc_type"))
        assert not (viva / "prior-review-input.json").exists(), \
            "the scratch pair must not survive the warning path either"
    print("  ok  check_resume_warns_on_a_type_that_no_longer_resolves")


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
    assert set(names) >= {"interview", "start", "annotate", "arm", "wait",
                          "rearm", "finish", "abandon"}, names

    for name in names:
        h = subprocess.run([sys.executable, str(LOOP), name, "--help"],
                           capture_output=True, text=True)
        assert h.returncode == 0, h.stderr
        assert "--round" not in h.stdout, \
            "`%s` exposes a round argument — the round is derived, never passed" % name
        required = [flag for flag in ("--doc", "--sidecar", "--input")
                    if flag in h.stdout]
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


def _numbered_step(text: str, keyword: str) -> str:
    """Body of the `**N. Title**` step whose title names `keyword`."""
    # `A4.` as well as `4.`: the merged review skill numbers its steps within a
    # branch, so the branch letter is part of the label.
    parts = re.split(r"^\*\*([A-Z]?\d+\.[^*]*)\*\*", text, flags=re.M)
    for header, body in zip(parts[1::2], parts[2::2]):
        if keyword.lower() in header.lower():
            return header + body
    raise AssertionError("SKILL.md has no numbered step naming %r" % keyword)


def _driven_prose() -> str:
    """The slice of `/viva-review` that `loop.py` drives — the invocation
    preamble plus branch A, stopping where branch B's own loop begins."""
    text = SKILL.read_text()
    start, end = text.index(DRIVEN_SECTION[0]), text.index(DRIVEN_SECTION[1])
    assert start < end, "branch A must precede branch B in %s" % SKILL
    return text[:end]


def check_skill_carries_no_bookkeeping_bash() -> None:
    """The prose half of the defect: SKILL.md retyped the loop every round."""
    text = _driven_prose()
    blocks = BASH_BLOCK_RE.findall(text)
    assert blocks, "SKILL.md has no bash block at all — the $VIVA_DIR resolve is one"
    for block in blocks:
        for label, pattern in FORBIDDEN_BASH:
            m = pattern.search(block)
            assert not m, (
                "a SKILL.md bash block %s (matched %r) — loop.py owns that:\n%s"
                % (label, m.group(0), block))
    print("  ok  check_skill_carries_no_bookkeeping_bash")


def check_only_the_undriven_flow_carries_its_own_loop() -> None:
    """The scope of the rule above, asserted as a closed set.

    One flow drives itself because `loop.py` cannot reach it yet, and it must
    say so — an undocumented second skill growing its own loop is the drift this
    catches. `/viva-write` left this set when `interview` and `start --handoff`
    landed (#179); branch B leaves it when the driver learns `parse_diff.py`.
    """
    undriven = {}
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = skill_md.read_text()
        blocks = BASH_BLOCK_RE.findall(text)
        if skill_md == SKILL:
            # Only the half the driver does not own may carry it.
            blocks = BASH_BLOCK_RE.findall(text[text.index(DRIVEN_SECTION[1]):])
        hits = [label for block in blocks for label, pattern in FORBIDDEN_BASH
                if pattern.search(block)]
        if hits:
            undriven[skill_md] = sorted(set(hits))

    assert set(undriven) == {SKILL}, (
        "skills carrying their own loop changed — expected exactly "
        "%s (branch B), got %s"
        % (SKILL.parent.name, sorted(p.parent.name for p in undriven)))
    # It must name the reason, so the exemption is a documented constraint
    # rather than an accident nobody notices.
    assert "#179" in SKILL.read_text(), (
        "%s must name the issue that would let the driver take branch B" % SKILL)
    print("  ok  check_only_the_undriven_flow_carries_its_own_loop")


def check_rewrite_step_applies_standing_preferences() -> None:
    """`wait` prints the standing set; the rewrite step must still apply it.

    The relocation moves the preferences reference material out of SKILL.md.
    The operative directive is not reference material and must not move with
    it — without it `wait` prints a standing set nothing consumes.
    """
    text = SKILL.read_text()
    step = _numbered_step(text, "rewrite").lower()
    assert "standing preference" in step, (
        "the rewrite step no longer instructs applying standing preferences — "
        "the learned-preference feature is silently dead")
    assert "wait" in step, (
        "the rewrite step names no source for the standing set; it comes from "
        "`loop.py wait`'s printed output, not a fresh command")
    assert "preferences.py" not in text, (
        "SKILL.md still invokes preferences.py — `wait` already prints the "
        "standing set, so a second read is the bookkeeping this story removed")
    print("  ok  check_rewrite_step_applies_standing_preferences")


def check_no_auto_approve_and_paused_branch_routed() -> None:
    """Nothing is auto-accepted, and the third round class has a row.

    The Skip button's `submitted_early: true` round fit neither documented
    branch, so a model in that gap improvised. `wait` classifies it; the table
    has to route it.
    """
    text = SKILL.read_text()
    low = text.lower()
    for banned in ("auto-approve", "auto approved", "too short to review"):
        assert banned not in low, (
            "SKILL.md still carries the auto-approve escape hatch (%r) — "
            "viva does not approve its own work" % banned)

    rows = [ln.lower() for ln in text.splitlines() if ln.startswith("|")]
    assert rows, "SKILL.md has no table at all"
    for klass in ("all-approved", "has-work", "submitted-early"):
        assert any(klass in ln for ln in rows), (
            "no table row routes `wait`'s %r classification" % klass)
    paused = [ln for ln in rows if "submitted-early" in ln]
    row = paused[0]
    assert "rearm" in row and "abandon" in row, (
        "the paused-reviewer row names no mechanism — re-arm returns the tab "
        "from the processing card, and `loop.py abandon` is the exit: %s" % row)
    assert row.index("rearm") < row.index("abandon"), (
        "re-arming comes first in the paused branch, before the agent asks a "
        "question in a terminal the reviewer is not watching: %s" % row)
    print("  ok  check_no_auto_approve_and_paused_branch_routed")


def check_skill_applies_suggestions_verbatim() -> None:
    """A suggestion is wording, not a brief (#166).

    The reviewer typed the replacement instead of describing it, so the one
    thing the agent must not do is rewrite it — an author that "improves" the
    phrasing hands back a diff the reviewer never asked for and cannot trust.
    The instruction lives where the agent routes by comment type, and the
    derivation paragraph beside it has to agree that such a section is not
    approved.
    """
    text = SKILL.read_text()
    rows = [ln for ln in text.splitlines() if ln.startswith("|")]
    assert any("by its `type`" in ln for ln in rows), (
        "the verdict table must route a commented card by comment type")
    # The per-type rules moved out of the table cell into the paragraph beside
    # it when doc and hunk review merged (#170) — one rule, two card shapes.
    # The sentence is the contract, not the cell it used to sit in.
    paragraphs = [" ".join(p.split()) for p in text.split("\n\n")]
    typed = [p for p in paragraphs if "**`suggestion`**" in p]
    assert typed, "SKILL.md has no comment-type rule naming the `suggestion` type"
    row = typed[0].lower()
    assert "verbatim" in row, (
        "the suggestion row does not say the wording is applied VERBATIM — "
        "without it the author rewrites what the reviewer already wrote: %s" % row)
    for banned_absence in ("no rewrite pass", "no interpretation"):
        assert banned_absence in row, (
            "the suggestion row must rule out %r explicitly: %s"
            % (banned_absence, row))
    assert "anchor" in row, "the suggestion row must scope the edit to the anchor"

    # Whitespace-flattened: the prose wraps, and where a line breaks is not the
    # contract — the sentence is.
    low = " ".join(text.lower().split())
    assert '`type: "suggestion"` → section `changes`' in low, \
        "the derivation paragraph must land a suggestion on the `changes` verdict"
    assert "carrying a live suggestion is never approved" in low, (
        "SKILL.md must state that a section holding a suggestion is not "
        "approved — the derivation is what makes it binding")

    # A carried suggestion is the same instruction one round later, but by then
    # the wording lives on the THREAD's exchange rather than on a `comments[]`
    # entry. Prose that names the type without naming the field leaves round 2
    # knowing to paste and not knowing what.
    assert "carried suggestion turn keeps its `replacement` on the exchange" in low, \
        "step 4 routes a carried suggestion turn to no field"
    threads = " ".join((REFERENCES / "open-notes.md").read_text().lower().split())
    assert "latest turn `suggestion`" in threads, \
        "open-notes.md's latest-turn rules do not route a `suggestion` turn"
    assert "apply its `replacement` verbatim" in threads, \
        "the carried-suggestion rule must say the wording is applied verbatim"
    assert "rides on the exchange" in threads, \
        "the carried-suggestion rule must name where the wording lives"
    print("  ok  check_skill_applies_suggestions_verbatim")


def check_references_are_reachable() -> None:
    """Every reference file is one the agent is *told* the path to.

    `loop.py` never reads `references/` — the agent does, and it has no
    `$VIVA_DIR` for a skill-relative path. So the driver prints the absolute
    path in the output line whose next step that file documents.

    Two routes now, not one: the driver's print sites, and a skill naming a file
    for a step the driver does not reach (`qa.md` documents the interview, which
    `loop.py` has no subcommand for). Both directions still hold — a file nothing
    names is unreachable, and a path `loop.py` prints with no file behind it
    makes "reachable" hollow.
    """
    assert REFERENCES.is_dir(), "%s does not exist" % REFERENCES
    on_disk = {p.name for p in REFERENCES.iterdir() if p.is_file()}
    assert on_disk, "references/ is empty — 'every file is reachable' is vacuous"
    # Either quote style: an f-string delimited by `"` cannot nest `"` before
    # Python 3.12, so the driver's own print sites use single quotes inside.
    printed = set(re.findall(r"""REFERENCES / ["']([^"']+)["']""", LOOP.read_text()))
    skill_prose = SKILL.read_text() + WRITE_SKILL.read_text()
    named = printed | {n for n in on_disk if n in skill_prose}
    assert on_disk == named, (
        "references/ and the paths loop.py and the skills name disagree — "
        "unreachable: %s" % sorted(on_disk - named))
    assert printed <= on_disk, (
        "loop.py prints a references path with no file behind it: %s"
        % sorted(printed - on_disk))

    # `\b` on both ends: "preferences" carries "references" as a substring, and
    # `$VIVA_DIR/scripts/preferences.py` is a legitimate command.
    interp = re.compile(r"VIVA_DIR[^\n]*\breferences\b")
    for path in [SKILL, WRITE_SKILL, LOOP] + sorted(REFERENCES.iterdir()):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            assert not interp.search(line), (
                "%s:%d routes a references path through $VIVA_DIR — the agent "
                "is told the path, never asked to compute it: %s"
                % (path.name, i, line.strip()))
    print("  ok  check_references_are_reachable")


@contextlib.contextmanager
def stub_input_server(payload: dict, posts: list = None):
    """A loopback server answering `GET /input` with `payload` — the smallest
    thing `loop.py`'s liveness probe can find at the other end of a
    `server.url`. Every POST body is appended to `posts` (when given) and
    answered `{"ok":true}`, so a test can see what `arm` hands over. Yields its
    base URL."""
    body = json.dumps(payload).encode()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):                     # noqa: N802 — stdlib's spelling
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):                    # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if posts is not None:
                posts.append((self.path, json.loads(raw or b"{}")))
            ok = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(ok)))
            self.end_headers()
            self.wfile.write(ok)

        def log_message(self, *a):            # keep the test output clean
            pass

    httpd = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)


def check_start_refuses_over_a_live_session() -> None:
    """`start` clears the round files and `server.url`. Without the pre-flight
    guard it does that to a *live* session, orphaning a running server with the
    reviewer's tab still attached — unrecoverable, and invisible until someone
    notices the orphan.

    Both branches of the refusal are checked, because they take opposite
    recoveries (#174). A live server means the human already has the tab and
    must be pointed at it; only a URL with nothing behind it earns the
    delete-the-file advice.

    The live fixture serves a **qa** payload deliberately: it has no `round`
    key, so a guard that reused `probe_round` would read it as dead and tell
    the human to delete the `server.url` of the very interview server
    `/viva-write` left running."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "d.md"
        doc.write_text("# T\n\n## A\n\naaa\n\n## B\n\nbbb\n")

        # 1. Nothing answering — port 1 refuses instantly. Stale file: say so,
        #    and say what to do about it.
        (viva / "server.url").write_text("http://127.0.0.1:1\n")
        r = loop(viva, td, "start", "--doc", str(doc))
        assert r.returncode != 0, "start must refuse over an existing server.url"
        assert "nothing is answering" in r.stderr, r.stderr
        assert "Delete the file" in r.stderr, \
            "the stale branch must keep the delete-the-file recovery"
        assert not (viva / "review-input-r1.json").exists(), \
            "start must not parse a round when it refuses — the refusal is the point"
        assert (viva / "server.url").exists(), \
            "start must not delete the session's server.url"

        # 2. A live server — point at the tab, never at `rm`. An interview is
        #    named as one, with the flag that would continue it.
        with stub_input_server({"mode": "qa", "questions": []}) as base:
            (viva / "server.url").write_text(base + "\n")
            r = loop(viva, td, "start", "--doc", str(doc))
            assert r.returncode != 0, "start must refuse over a live session"
            assert base in r.stderr, \
                "a live collision must name the URL of the open tab: " + r.stderr
            assert "Delete the file" not in r.stderr, \
                "deleting a live session's server.url orphans the server"
            assert "--handoff" in r.stderr, \
                "a live interview must be named as one, with the way in: " + r.stderr
            assert not (viva / "review-input-r1.json").exists(), r.stderr
            assert (viva / "server.url").exists()
    print("  ok  check_start_refuses_over_a_live_session")


def check_start_handoff_refuses_without_an_interview() -> None:
    """`--handoff` is explicit and verified, never inferred from state: it
    needs a live server serving an interview. No `server.url`, a dead one, and
    a live REVIEW session are three distinct refusals, and none of them parses
    a round or touches the file."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "d.md"
        doc.write_text("# T\n\n## A\n\naaa\n")

        r = loop(viva, td, "start", "--doc", str(doc), "--handoff")
        assert r.returncode != 0 and "server.url does not exist" in r.stderr, r.stderr
        assert "loop.py interview" in r.stderr, "must name the step that was skipped"

        (viva / "server.url").write_text("http://127.0.0.1:1\n")
        r = loop(viva, td, "start", "--doc", str(doc), "--handoff")
        assert r.returncode != 0 and "nothing is answering" in r.stderr, r.stderr
        assert (viva / "server.url").exists()

        with stub_input_server({"mode": "review", "round": 2, "sections": []}) as base:
            (viva / "server.url").write_text(base + "\n")
            r = loop(viva, td, "start", "--doc", str(doc), "--handoff")
            assert r.returncode != 0, "a review session is not an interview"
            assert "review session" in r.stderr and "round 2" in r.stderr, r.stderr
            assert (viva / "server.url").read_text().strip() == base
        assert not (viva / "review-input-r1.json").exists(), \
            "no refusal may leave a parsed round behind"
    print("  ok  check_start_handoff_refuses_without_an_interview")


def check_arm_hands_off_into_a_live_interview() -> None:
    """`arm` gates its POST branch on liveness (`probe_input`), not on the round
    the server reports (`probe_round`): a qa payload carries no `round` key, and
    reading that as "nothing is answering" is what kept the driver out of
    `/viva-write`'s hand-off. The stub answers `/input` as an interview would
    and records what `arm` POSTs to `/next-round`."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "d.md"
        doc.write_text("# T\n\n## A\n\naaa\n\n## B\n\nbbb\n")
        parse(doc, viva / "review-input-r1.json", 1, viva)
        posts = []
        with stub_input_server({"mode": "qa", "questions": []}, posts) as base:
            (viva / "server.url").write_text(base + "\n")
            r = loop(viva, td, "arm")
            assert r.returncode == 0, r.stderr
            assert f"round 1 armed · {base}" in r.stdout, r.stdout
        assert [p for p, _ in posts] == ["/next-round"], posts
        body = posts[0][1]
        parsed = json.loads((viva / "review-input-r1.json").read_text())
        assert [s["id"] for s in body["sections"]] == \
            [s["id"] for s in parsed["sections"]], body
        assert body["output"].endswith("review-r1.json"), \
            "the verdict path travels in the body, distinct from the qa output"
    print("  ok  check_arm_hands_off_into_a_live_interview")


def check_interview_refuses_over_a_live_session() -> None:
    """`interview` inherits `start`'s pre-flight, both branches: a live server
    names its URL and never advises `rm`; a dead `server.url` is told to delete
    the file. Neither clears a thing."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        qa_in = viva / "qa-input.json"
        qa_in.write_text(json.dumps({"mode": "qa", "context": "c", "questions": [
            {"id": "q1", "text": "Which?", "choices": ["a", "b"]}]}))
        (viva / "answers.json").write_text("{}")

        with stub_input_server({"mode": "review", "round": 1, "sections": []}) as base:
            (viva / "server.url").write_text(base + "\n")
            r = loop(viva, td, "interview", "--input", str(qa_in))
            assert r.returncode != 0 and base in r.stderr, r.stderr
            assert "Delete the file" not in r.stderr, r.stderr
        assert (viva / "answers.json").exists(), "a refusal must not clear"

        (viva / "server.url").write_text("http://127.0.0.1:1\n")
        r = loop(viva, td, "interview", "--input", str(qa_in))
        assert r.returncode != 0 and "Delete the file" in r.stderr, r.stderr
        assert (viva / "answers.json").exists()
    print("  ok  check_interview_refuses_over_a_live_session")


def check_interview_exits_2_when_the_server_dies() -> None:
    """The interview wait was `until [ -f .viva/answers.json ]` — the same
    dead-server infinite poll #103 fixed for review. The driver's form exits 2
    the moment `server.url` is gone, exactly as `wait` does."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        qa_in = viva / "qa-input.json"
        qa_in.write_text(json.dumps({"mode": "qa", "context": "c", "questions": [
            {"id": "q1", "text": "Which?", "choices": ["a", "b"]}]}))
        proc = subprocess.Popen(
            [sys.executable, str(LOOP), "--viva-dir", str(viva),
             "interview", "--input", str(qa_in)],
            cwd=str(td), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        base = ""
        try:
            # The driver flushes its URL line once its own startup poll has seen
            # `server.url`; reading it is what makes the delete below land AFTER
            # that poll rather than racing it.
            first = proc.stdout.readline()
            assert "interview open · " in first, \
                "the URL line must be flushed before the block: " + first
            base = first.split("interview open · ", 1)[1].strip()
            assert (viva / "server.url").read_text().strip() == base
            # The only deterministic kill: the driver detached the server and
            # holds no handle to it. The orphan is reaped below.
            (viva / "server.url").unlink()
            out, err = proc.communicate(timeout=15)
            assert proc.returncode == 2, (proc.returncode, err)
            assert "interview server is gone" in err, err
        finally:
            if proc.poll() is None:
                proc.kill()
            if base:
                try:
                    post(base, "/abandon", {})
                except Exception:             # already down — fine
                    pass
    print("  ok  check_interview_exits_2_when_the_server_dies")


def check_start_runs_the_bundles_checks() -> None:
    """A type's `checks[]` run inside `start`, between the parse and every
    branch that could arm — a check nobody is told about never runs, and a
    typed round with no flags to answer closes on the base alone. The name is
    validated beside the type, BEFORE the clear, so a repo bundle naming a
    check this plugin does not ship is refused with the prior state intact."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "d.md"
        # The design-doc grammar minus every heading: one flag per gap.
        doc.write_text("# T\n\n## Problem & persona\n\np\n")
        r = loop(viva, td, "start", "--doc", str(doc), "--type", "design-doc",
                 "--parse-only")
        assert r.returncode == 0, r.stderr
        assert "checks run: headings-present" in r.stdout, r.stdout
        assert "flag(s) merged" in r.stdout, r.stdout
        data = json.loads((viva / "review-input-r1.json").read_text())
        flags = [a for s in data["sections"] for a in s.get("annotations", [])
                 if a.get("kind") == "headings-present"]
        assert flags and all("result" not in a for a in flags), flags
        assert not (viva / "server.url").exists(), "--parse-only still stops"

        # A bundle naming a check that does not exist: refused before the clear.
        (td / ".viva-types").mkdir()
        (td / ".viva-types" / "bad.json").write_text(json.dumps({
            "name": "bad", "title": "Bad", "sections": ["A"],
            "checks": ["no-such-check"], "default_pass": "architecture"}))
        marker = viva / "review-input-r1.json"
        before = marker.read_text()
        r = loop(viva, td, "start", "--doc", str(doc), "--type", "bad")
        assert r.returncode != 0 and "no-such-check" in r.stderr, r.stderr
        assert marker.read_text() == before, \
            "a refused type must not have cleared the prior round"
    print("  ok  check_start_runs_the_bundles_checks")


def check_undriven_guards_point_at_the_live_tab() -> None:
    """#174 fixed the collision message in `loop.py`. `/viva-review`'s branch B
    still carries its own bash (CLAUDE.md; #179's remaining half empties it),
    so the driver's fix cannot reach it. It cannot probe without growing the
    bash #179 exists to shrink, so it does the half that needs no round-trip:
    name the URL, and make deleting the file conditional on nothing answering
    there. What is pinned is that the copy never reverts to advising `rm` first.

    `/viva-write` carries NO guard any more: `loop.py interview` and `start
    --handoff` own that refusal, probing before they advise — so a guard line
    reappearing there is the bash creeping back."""
    write_guards = [ln for ln in WRITE_SKILL.read_text().splitlines()
                    if "[ -f .viva/server.url ]" in ln]
    assert not write_guards, \
        "viva-write must not carry a server.url guard — the driver probes: %r" \
        % write_guards
    for skill_md in (SKILL,):
        # `&&` is the collision guard (file present = refuse). The `||` form is
        # the post-launch check that the server wrote its URL at all — inverse
        # test, different message, not this one's business.
        guards = [ln for ln in skill_md.read_text().splitlines()
                  if ln.lstrip().startswith("[ -f .viva/server.url ] &&")]
        assert guards, "no server.url guard found in " + skill_md.name
        for ln in guards:
            where = "{}/{}".format(skill_md.parent.name, skill_md.name)
            assert "cat .viva/server.url" in ln, \
                where + ": the collision must name the open tab's URL, not just " \
                "the file's existence: " + ln
            assert "may still be running (.viva/server.url exists)" not in ln, \
                where + ": reverted to the pre-#174 message: " + ln
            assert "only if nothing is answering" in ln, \
                where + ": deleting a live session's server.url orphans the " \
                "server, so the advice must be conditional: " + ln
    print("  ok  check_undriven_guards_point_at_the_live_tab")


def check_start_resume_carries_prior_approvals() -> None:
    """`start`'s resume branch: a doc already carrying a sign-off ledger, with
    the prior round still on disk. The copy-out must happen before the clear
    glob or carry-forward dies with it, and the scratch pair must not survive.
    Also pins that a recorded `split_on` and `doc_type` are handed back —
    re-deciding the split by auto-detection changes every section's identity and
    carries nothing, and a dropped type silently takes the check set with it."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "plan.md"
        body = ("# P\n\n## Notes\n\nn\n\n### Task 1 — one\n\nx\n\n"
                "### Task 2 — two\n\ny\n")
        doc.write_text(body)
        pattern = r"(?i)^Task \d+"

        # Session one, signed off: parse with the pattern, approve everything,
        # append a ledger the way revision_history.py would.
        subprocess.run(
            [sys.executable, str(PARSE), str(doc), "--output",
             str(viva / "review-input-r1.json"), "--round", "1",
             "--doc-file", str(doc), "--split-on", pattern,
             "--doc-type", "plan"],
            check=True, capture_output=True)
        first = json.loads((viva / "review-input-r1.json").read_text())
        ids = [s["id"] for s in first["sections"]]
        (viva / "review-r1.json").write_text(json.dumps(
            {"round": 1, "submitted_early": False,
             "sections": [{"id": i, "verdict": "approved"} for i in ids]}))
        doc.write_text(body + "\n---\n\n## Revision History\n\nsigned off\n")

        r = loop(viva, td, "start", "--doc", str(doc), "--parse-only")
        assert r.returncode == 0, r.stderr

        second = json.loads((viva / "review-input-r1.json").read_text())
        assert second.get("split_on") == pattern, \
            "the resume must re-split with the pattern the prior round recorded"
        assert second.get("doc_type") == "plan", \
            "the resume must carry the prior round's doc type — dropping it " \
            "drops the session's check set: %s" % second.get("doc_type")
        assert "checks: headings-present" in r.stdout, (
            "a resumed typed session must name its checks too — carrying the "
            "type but telling nobody which producers to run is the same "
            "silence:\n%s" % r.stdout)
        assert [s["title"] for s in second["sections"]] == \
               [s["title"] for s in first["sections"]], \
            "same pattern, same sections — otherwise identity moved"
        assert second.get("approved_ids"), \
            "the resume carried no approvals — the copy ran after the clear"
        assert not (viva / "prior-review-input.json").exists(), \
            "the scratch pair must not survive the resume"
        assert not (viva / "prior-review-verdicts.json").exists()
    print("  ok  check_start_resume_carries_prior_approvals")


def check_start_opens_the_producer_seam() -> None:
    """A standing preference auto-engages the preference producer, which is an
    LLM pass — so `start` must stop after parsing rather than arm. The round
    file's absolute path has to be printed, or the agent is back to computing
    `review-input-r{N}.json`, the counter this driver exists to remove."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "d.md"
        doc.write_text("# T\n\n## A\n\naaa\n\n## B\n\nbbb\n")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "preferences.py"), "record",
             "--store", str(viva / "preferences.json"), "--session", "s1",
             "--id", "cite-sources", "--label", "Cite a source",
             "--guidance", "Attach a citation.", "--count", "2"],
            check=True, capture_output=True)
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "preferences.py"), "record",
             "--store", str(viva / "preferences.json"), "--session", "s2",
             "--id", "cite-sources", "--label", "Cite a source",
             "--guidance", "Attach a citation.", "--count", "2"],
            check=True, capture_output=True)

        r = loop(viva, td, "start", "--doc", str(doc))
        assert r.returncode == 0, r.stderr
        assert "NOT armed" in r.stdout, r.stdout
        assert not (viva / "server.url").exists(), \
            "the seam must stop before a server is launched"
        assert str(viva / "review-input-r1.json") in r.stdout, \
            "the seam must name the round file's absolute path"

        # Close the seam the way the agent does: annotate, then arm.
        sidecar = td / "flags.json"
        sidecar.write_text(json.dumps(
            [{"id": "s1", "kind": "preference", "severity": "warn",
              "message": "[cite-sources] no source"}]))
        r = loop(viva, td, "annotate", "--sidecar", str(sidecar))
        assert r.returncode == 0, r.stderr
        merged = json.loads((viva / "review-input-r1.json").read_text())
        flagged = [s for s in merged["sections"] if s.get("annotations")]
        assert flagged, "annotate did not land the producer's flag in the round"
    print("  ok  check_start_opens_the_producer_seam")


def check_wait_refuses_a_parsed_but_unarmed_round() -> None:
    """`rearm --parse-only` writes round N+1 while the server still serves N.
    If the producer step then fails and the agent falls back to `wait`, the
    round on disk is one nothing will ever write verdicts for — file existence
    proves neither liveness nor armed-ness."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        # Round 1 armed and answered; round 2 parsed but never shipped.
        (viva / "review-input-r1.json").write_text(json.dumps(
            {"mode": "review", "round": 1, "doc_file": "d.md",
             "sections": [{"id": "s1", "title": "A", "content": "## A\n"}]}))
        (viva / "review-input-r2.json").write_text(json.dumps(
            {"mode": "review", "round": 2, "doc_file": "d.md",
             "sections": [{"id": "s1", "title": "A", "content": "## A\n"}]}))
        with launch_server(viva / "review-input-r1.json",
                           viva / "review-r1.json", cwd=td) as base:
            r = loop(viva, td, "wait")
            assert r.returncode == 2, \
                "a parsed-but-unarmed round must exit 2, not hang: %r" % r
            assert "not armed" in r.stderr and "loop.py arm" in r.stderr, r.stderr
    print("  ok  check_wait_refuses_a_parsed_but_unarmed_round")


def check_decline_carries_and_insisting_wins() -> None:
    """`rearm --decline` is how the author records a refusal (#167).

    Three things have to hold end to end, through the real driver: the grounds
    reach the store as a `declined` thread; that thread carries into round N+1,
    which is the whole holding mechanism (no new one was added); and a second
    decline after the reviewer insists is refused before any round ships.
    """
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "d.md"
        doc.write_text("# T\n\n## A\n\naaa\n\n## B\n\nbbb\n")

        r = loop(viva, td, "start", "--doc", "d.md", "--parse-only")
        assert r.returncode == 0, r.stderr
        r1 = json.loads((viva / "review-input-r1.json").read_text())
        ids = {s["title"]: s["id"] for s in r1["sections"]}
        sid, other = ids["A"], ids["B"]
        cid = sid + "-c1"
        grounds = "round 1 ruled the caveat load-bearing"

        request = {"round": 1, "submitted_early": False, "sections": [
            {"id": sid, "verdict": "changes", "comments": [
                {"cid": cid, "type": "changes", "note": "cut the caveat",
                 "anchor": {"text": "aaa", "offset": 0},
                 "open": True, "settled": False}]},
            {"id": other, "verdict": "approved"}]}
        (viva / "review-r1.json").write_text(json.dumps(request))

        # The author declines instead of complying — the doc is NOT edited.
        r = loop(viva, td, "rearm", "--decline", cid + "=" + grounds, "--parse-only")
        assert r.returncode == 0, r.stderr
        thread = json.loads((viva / "open-notes.json").read_text())[cid]
        assert thread["status"] == "declined", thread
        assert thread["exchanges"][0]["grounds"] == grounds, thread

        # Held: the thread carries onto the next round's card with its grounds,
        # and the section is not carried approved. Both fall out of what already
        # existed — the unresolved filter and the approval carry-forward.
        r2 = json.loads((viva / "review-input-r2.json").read_text())
        card = next(s for s in r2["sections"] if s["title"] == "A")
        assert card["open_notes"][0]["status"] == "declined", card["open_notes"]
        assert card["open_notes"][0]["exchanges"][0]["grounds"] == grounds
        assert sid not in r2["approved_ids"], (
            "a declined request must leave its section held: %s" % r2["approved_ids"])

        # The reviewer insists — a reply on the same cid.
        insist = {"round": 2, "submitted_early": False, "sections": [
            {"id": sid, "verdict": "changes", "comments": [
                {"cid": cid, "type": "changes", "note": "cut it anyway",
                 "open": True, "settled": False, "reply": True}]},
            {"id": other, "verdict": "approved"}]}
        (viva / "review-r2.json").write_text(json.dumps(insist))

        r = loop(viva, td, "rearm", "--decline", cid + "=still contradicts round 1",
                 "--parse-only")
        assert r.returncode != 0, "a second decline on the same thread must be refused"
        assert cid in r.stderr and "insisting wins" in r.stderr, r.stderr
        assert not (viva / "review-input-r3.json").exists(), \
            "the refusal must land before the next round is parsed"
        assert len(json.loads((viva / "open-notes.json").read_text())[cid]["exchanges"]) == 1, \
            "a refused decline must not write a turn to the store"

        # Complying ships it, and the thread is open again.
        r = loop(viva, td, "rearm", "--response", cid + "=cut", "--parse-only")
        assert r.returncode == 0, r.stderr
        thread = json.loads((viva / "open-notes.json").read_text())[cid]
        assert thread["status"] == "open", thread
        assert len(thread["exchanges"]) == 2, thread
    print("  ok  check_decline_carries_and_insisting_wins")


def check_skill_carries_the_decline_rule() -> None:
    """The half of insisting-wins that only prose can carry (#167).

    `open_notes.py` can refuse a second decline; it cannot make the author
    decline for cause, or comply once the reviewer has insisted. SKILL.md is
    where that rule lives, so it is asserted here beside the executed one.
    """
    low = " ".join(SKILL.read_text().lower().split())
    assert "--decline" in low, "SKILL.md never names the flag that records a refusal"
    assert "insisting wins" in low, (
        "SKILL.md must state that the reviewer's insistence wins — an author "
        "that can re-decline has an unbounded veto")
    assert "no second decline" in low, \
        "SKILL.md must rule out a second decline on the same thread"
    assert "grounds" in low, "SKILL.md must require grounds for a decline"
    print("  ok  check_skill_carries_the_decline_rule")


def main() -> None:
    check_round_trip()
    check_split_on_session()
    check_doc_type_session()
    check_untyped_session_records_no_doc_type()
    check_pass_carries_within_a_session_not_across_a_resume()
    check_resume_warns_on_a_type_that_no_longer_resolves()
    check_no_subcommand_takes_a_round()
    check_loop_cross_imports_only_schema()
    check_skill_carries_no_bookkeeping_bash()
    check_only_the_undriven_flow_carries_its_own_loop()
    check_rewrite_step_applies_standing_preferences()
    check_no_auto_approve_and_paused_branch_routed()
    check_skill_applies_suggestions_verbatim()
    check_decline_carries_and_insisting_wins()
    check_skill_carries_the_decline_rule()
    check_references_are_reachable()
    check_start_refuses_over_a_live_session()
    check_start_handoff_refuses_without_an_interview()
    check_arm_hands_off_into_a_live_interview()
    check_interview_refuses_over_a_live_session()
    check_interview_exits_2_when_the_server_dies()
    check_start_runs_the_bundles_checks()
    check_undriven_guards_point_at_the_live_tab()
    check_start_resume_carries_prior_approvals()
    check_start_opens_the_producer_seam()
    check_wait_refuses_a_parsed_but_unarmed_round()
    print("OK")


if __name__ == "__main__":
    main()
