#!/usr/bin/env python3
"""Orchestration smoke test: one real session driven the way the agent drives it.

Drives the real pipeline (parse_sections.py -> server.py -> loop.py's round-2+
subcommands) instead of hand-writing review-input JSON, guarding round sequencing,
approved-carry-forward, loop.py's derivation/cross-import rules (CLAUDE.md), SKILL.md's
prose matching the driven sequence, no skill carrying its own loop (#179), and the
diff-mode driver path (capture, arm, summaries, empty re-capture, freshness refusal).
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

BASH_BLOCK_RE = re.compile(r"```bash\n(.*?)```", re.S)

# Content-based, not fence-typed — a curl-free POST is the same defect. Scoped
# to ```bash blocks so the File Layout tree (a map, not a step) keeps its
# round-file names; "launches" matches invocation, not a bare `server.py` name.
FORBIDDEN_BASH = [
    ("launches a server", re.compile(r"\bpython3?\b[^\n]*\bserver\.py\b")),
    ("POSTs an endpoint",
     re.compile(r"\bcurl\b|/next-round|/complete|/submit\b|/abandon")),
    ("names a round file", re.compile(r"review-input-r|review-r")),
]

DOC = "## Goals\n\nShip the core.\n\n## Scope\n\nJust the core, nothing more.\n"

# A task-card plan: `### Task N` cards each containing a `## Notes` block.
# Auto-detection picks the coarser `## Notes` repeater and swallows both tasks,
# so the two splits are visibly different, making "round 2 re-splits the same way" a real assertion.
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
    """A task-card plan review, start -> submit -> rearm, through the driver.

    `--split-on` makes the round split on task cards, not top-level headings; the
    driver carries the pattern forward in the round file so `rearm` re-splits the
    same way. Round 2 asserting the same split, ids, and carried approvals proves the round-trip.
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

    A name that resolves to nothing must be refused before any state is cleared, and
    once recorded must reach round 2 the way `split_on` does, or a typed session goes
    untyped at re-parse.
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

    `rearm` takes an override the split pattern and type don't need, since depth
    changes mid-session; round N+1 without one inherits round N's depth. A resume
    is a new review, so it must not inherit the prior session's finishing pass.
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

    By the time the name resolves, the scratch carry-forward pair is already on
    disk — dying there would strand a repo that dropped its `.viva-types/` bundle.
    So: warn, keep the type recorded, and let the resume proceed."""
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

    Enumerated from `--help` so a later subcommand isn't silently exempt; each run
    gets its required flags satisfied (nonexistent paths) so argparse rejects
    `--round` itself, aimed at a throwaway `--viva-dir` so a regression never
    reaches a real `.viva/`.
    """
    sandbox = Path(tempfile.mkdtemp()) / ".viva"
    top = subprocess.run([sys.executable, str(LOOP), "--help"],
                         capture_output=True, text=True)
    assert top.returncode == 0, top.stderr
    listed = re.search(r"\{([a-z,]+)\}", top.stdout)
    assert listed, "could not read the subcommand list from --help:\n" + top.stdout
    names = listed.group(1).split(",")
    assert set(names) >= {"interview", "start", "annotate", "summarize", "arm",
                          "wait", "rearm", "finish", "abandon"}, names

    for name in names:
        h = subprocess.run([sys.executable, str(LOOP), name, "--help"],
                           capture_output=True, text=True)
        assert h.returncode == 0, h.stderr
        assert "--round" not in h.stdout, \
            "`%s` exposes a round argument — the round is derived, never passed" % name
        required = [flag for flag in ("--doc", "--sidecar", "--input", "--map")
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


def _cross_imports(path) -> set:
    imported = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    return imported


def check_every_script_cross_imports_only_schema() -> None:
    """CLAUDE.md's one-cross-import rule, asserted on every `scripts/*.py`
    filter, not `loop.py` alone. Each may import `schema` and nothing else
    among its siblings, so each stays independently testable."""
    script_paths = list((ROOT / "scripts").glob("*.py"))
    siblings = {p.stem for p in script_paths}
    for path in script_paths:
        if path.stem == "schema":
            continue
        imported = _cross_imports(path) & siblings
        assert imported == {"schema"} or not imported, \
            "%s may cross-import schema and nothing else — found %r" \
            % (path.name, sorted(imported))


def check_server_cross_imports_only_schema_and_preferences() -> None:
    """`server.py`'s documented exception to the one-cross-import rule
    (CLAUDE.md): it may import `schema` and `preferences` (both stdlib-only,
    both independently testable) and no other `scripts/*.py` sibling."""
    siblings = {p.stem for p in (ROOT / "scripts").glob("*.py")}
    server_path = ROOT / "server.py"
    imported = _cross_imports(server_path) & siblings
    assert imported == {"schema", "preferences"}, \
        "server.py may cross-import only schema and preferences — found %r" \
        % sorted(imported)


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
    """All of `/viva-review` — both branches are the driver's now."""
    return SKILL.read_text()


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


def check_no_skill_carries_its_own_loop() -> None:
    """The scope of the rule above, asserted as a closed set — now empty.

    Two flows used to drive themselves before #179 gave the driver both: `/viva-write`'s
    interview/hand-off, and `/viva-review`'s branch B. A skill growing its own loop lands
    in this set and fails here, by name.
    """
    undriven = {}
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        blocks = BASH_BLOCK_RE.findall(skill_md.read_text())
        hits = [label for block in blocks for label, pattern in FORBIDDEN_BASH
                if pattern.search(block)]
        if hits:
            undriven[skill_md] = sorted(set(hits))
    assert not undriven, (
        "a skill carries its own loop — loop.py owns that: %s"
        % {p.parent.name: hits for p, hits in undriven.items()})
    print("  ok  check_no_skill_carries_its_own_loop")


def check_rewrite_step_applies_standing_preferences() -> None:
    """`wait` prints the standing set; the rewrite step must still apply it.

    The operative directive is not reference material and must not move out of
    SKILL.md with it, or `wait` prints a standing set nothing consumes.
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

    The Skip button's `submitted_early: true` round fit neither documented branch,
    so `wait` classifies it and the table must route it.
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

    The reviewer typed the replacement; the agent must not rewrite it. The
    comment-type routing instruction and the derivation paragraph beside it
    both have to agree such a section is not approved.
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

    # A carried suggestion moves to the THREAD's exchange, not a `comments[]`
    # entry — naming the type without naming the field leaves round 2 knowing
    # to paste but not what.
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

    `loop.py` never reads `references/` — it prints the absolute path in the
    output line whose next step that file documents. Two routes: the driver's
    print sites, and a skill naming a file for a step the driver doesn't reach
    (e.g. `qa.md`).
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
    thing `loop.py`'s liveness probe finds at a `server.url`. Every POST body is
    appended to `posts` (when given) and answered `{"ok":true}`. Yields its base URL."""
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
    """`start` clears the round files and `server.url`; without a pre-flight guard
    that orphans a live session's running server (#174). Both refusal branches are
    checked — a live server names its URL, a dead one gets delete-the-file advice —
    using a **qa** payload (no `round` key) so a `probe_round`-based guard couldn't
    misread a live interview server as dead."""
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
    """`--handoff` is explicit and verified, never inferred from state: it needs a
    live server serving an interview. No `server.url`, a dead one, and a live
    REVIEW session are three distinct refusals; none parses a round or touches the file."""
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
    """`arm` gates its POST branch on liveness (`probe_input`), not the round the
    server reports (`probe_round`) — a qa payload carries no `round` key. The stub
    answers `/input` as an interview would and records what `arm` POSTs to `/next-round`."""
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


def _git_repo(td: Path, lines=("a", "b", "c")) -> Path:
    """One committed file, for a worktree or ref target. Never a `pr` target —
    CI has no `gh`. Returns the file."""
    subprocess.run(["git", "init", "-q"], cwd=str(td), check=True)
    f = td / "f.txt"
    f.write_text("\n".join(lines) + "\n")
    subprocess.run(["git", "add", "f.txt"], cwd=str(td), check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "init"], cwd=str(td), check=True)
    return f


def _submit(base: str, round_no: int, verdicts: dict) -> None:
    post(base, "/submit", {"round": round_no, "submitted_early": False,
                           "sections": [{"id": i, "verdict": v}
                                        for i, v in verdicts.items()]})


def _server_gone(viva: Path) -> bool:
    for _ in range(50):
        if not (viva / "server.url").exists():
            return True
        time.sleep(0.2)
    return False


def check_diff_round_trip() -> None:
    """The driver's other mode, end to end: a worktree edit captured and armed
    `--mode diff`, a `changes` verdict, an edit, a re-capture into round 2, an
    approval, and a finish — with every doc-only surface refused on the way."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        viva = td / ".viva"
        viva.mkdir()
        f = _git_repo(td)
        f.write_text("a\nB\nc\n")

        r = loop(viva, td, "start", "--kind", "worktree")
        assert r.returncode == 0, r.stderr
        assert "1 hunk(s) · working tree" in r.stdout, r.stdout
        assert "round 1 armed" in r.stdout, r.stdout
        record = json.loads((viva / "target.json").read_text())
        assert record["kind"] == "worktree" and record["capture"] == ["git", "diff"], record
        assert Path(record["cwd"]) == td, "the capture's cwd is recorded"
        assert (viva / "diff.patch").stat().st_size > 0
        r1 = json.loads((viva / "review-input-r1.json").read_text())
        assert r1["mode"] == "diff" and r1["doc_file"] == "working tree", r1
        base = (viva / "server.url").read_text().strip()
        try:
            served = get(base, "/input")
            assert served["mode"] == "diff" and served["round"] == 1, served

            # A doc-only flag on a diff start is refused, loudly.
            r = loop(viva, td, "start", "--kind", "worktree", "--type", "plan")
            assert r.returncode != 0 and "doc-review flag" in r.stderr, r.stderr

            _submit(base, 1, {"s1": "changes"})
            assert poll_for(viva / "review-r1.json")
            w = loop(viva, td, "wait")
            assert w.returncode == 0 and "round 1: has-work" in w.stdout, w.stdout
            assert "open-notes.md" not in w.stdout and "style.md" not in w.stdout, \
                "a diff round has no threads and no prose rail: " + w.stdout

            r = loop(viva, td, "rearm", "--response", "s1-c1=done")
            assert r.returncode != 0 and "no threads" in r.stderr, r.stderr
            r = loop(viva, td, "finish", "--doc", "x.md")
            assert r.returncode != 0 and "--doc" in r.stderr, r.stderr

            # The agent edits; `rearm` re-captures and arms round 2 in place.
            f.write_text("a\nBB\nc\n")
            r = loop(viva, td, "rearm")
            assert r.returncode == 0, r.stderr
            assert "round 2 armed" in r.stdout, r.stdout
            served = get(base, "/input")
            assert served["round"] == 2 and served["mode"] == "diff", served
            assert served["approved_ids"] == [], "a changed hunk carries nothing"

            _submit(base, 2, {"s1": "approved"})
            assert poll_for(viva / "review-r2.json")
            assert "round 2: all-approved" in loop(viva, td, "wait").stdout
            fin = loop(viva, td, "finish")
            assert fin.returncode == 0, fin.stderr
            assert "signed off — 2 round(s), 1 hunk(s)" in fin.stdout, fin.stdout
            assert "nothing to commit" not in fin.stdout, "an approved diff is committable"
            assert _server_gone(viva), "an accepted /complete shuts the server down"
        finally:
            loop(viva, td, "abandon")
    print("  ok  check_diff_round_trip")


def check_diff_finish_from_an_empty_recapture() -> None:
    """#177. The reviewer asks for a hunk to go, the agent reverts it, and the diff
    goes empty. `rearm` reports that and arms nothing; `finish` re-captures, asserts
    `resolved: "empty"` to `/complete`, and the server signs off despite a `changes` verdict."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        viva = td / ".viva"
        viva.mkdir()
        f = _git_repo(td)
        f.write_text("a\nB\nc\n")
        assert loop(viva, td, "start", "--kind", "worktree").returncode == 0
        base = (viva / "server.url").read_text().strip()
        try:
            _submit(base, 1, {"s1": "changes"})
            assert poll_for(viva / "review-r1.json")
            f.write_text("a\nb\nc\n")                       # reverted
            r = loop(viva, td, "rearm")
            assert r.returncode == 0, r.stderr
            assert "diff is empty after re-capture" in r.stdout, r.stdout
            assert "finish" in r.stdout, "must name the verb that signs off"
            assert get(base, "/input")["round"] == 1, "nothing was armed"
            assert not (viva / "review-input-r2.json").exists()

            fin = loop(viva, td, "finish")
            assert fin.returncode == 0, fin.stderr
            assert "diff fully resolved — nothing to commit" in fin.stdout, fin.stdout
            assert "1 revised" in fin.stdout, fin.stdout
            assert _server_gone(viva), "the resolved-empty finish shuts the server down"
        finally:
            loop(viva, td, "abandon")
    print("  ok  check_diff_finish_from_an_empty_recapture")


def check_finish_refuses_a_diff_that_changed_since_review() -> None:
    """The human approved a hunk; the agent kept editing. The round on disk is
    all-approved, so the server would sign it off — `finish` re-captures and
    refuses instead, because what would be signed is not what was approved."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        viva = td / ".viva"
        viva.mkdir()
        f = _git_repo(td)
        f.write_text("a\nB\nc\n")
        assert loop(viva, td, "start", "--kind", "worktree").returncode == 0
        base = (viva / "server.url").read_text().strip()
        try:
            _submit(base, 1, {"s1": "approved"})
            assert poll_for(viva / "review-r1.json")
            assert "round 1: all-approved" in loop(viva, td, "wait").stdout
            f.write_text("a\nB\nC\n")                       # edited after approval
            fin = loop(viva, td, "finish")
            assert fin.returncode != 0, fin.stdout
            assert "the diff changed since round 1" in fin.stderr, fin.stderr
            assert "Nothing is auto-accepted" in fin.stderr, fin.stderr
            assert get(base, "/input")["round"] == 1, "the server is still live"
            assert not (viva / "finish-check.json").exists(), "the scratch is gone"
            # The recovery it names: re-present, and the human sees the change.
            r = loop(viva, td, "rearm")
            assert r.returncode == 0 and "round 2 armed" in r.stdout, r.stderr
        finally:
            loop(viva, td, "abandon")
    print("  ok  check_finish_refuses_a_diff_that_changed_since_review")


def check_diff_start_stops_at_the_summaries_seam() -> None:
    """Above `SUMMARY_THRESHOLD` hunks, a diff round stops after parsing until every
    hunk carries a one-line summary (#188): the seam prints the round file and the
    `summarize` verb, launches nothing. `summarize` merges pre-arm; `--arm-anyway` declines the seam."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        viva = td / ".viva"
        viva.mkdir()
        f = _git_repo(td, [f"l{i}" for i in range(1, 101)])
        lines = f.read_text().splitlines()
        for i in range(0, 88, 8):                 # 11 edits, 8 lines apart
            lines[i] = lines[i].upper()
        f.write_text("\n".join(lines) + "\n")

        r = loop(viva, td, "start", "--kind", "worktree")
        assert r.returncode == 0, r.stderr
        data = json.loads((viva / "review-input-r1.json").read_text())
        n = len(data["sections"])
        assert n > 10, f"precondition: the fixture must exceed the threshold, got {n}"
        assert "NOT armed" in r.stdout and f"{n} of {n} hunks need a summary" in r.stdout, r.stdout
        assert "loop.py summarize" in r.stdout, r.stdout
        assert str(viva / "review-input-r1.json") in r.stdout, "the seam names the round file"
        assert "references/" not in r.stdout, "a diff seam has no producer contract"
        assert not (viva / "server.url").exists(), "the seam must stop before a launch"

        bad = loop(viva, td, "summarize", "--map", "-")
        assert bad.returncode != 0, "an empty map is not JSON"
        r = subprocess.run(
            [sys.executable, str(LOOP), "--viva-dir", str(viva), "summarize", "--map", "-"],
            input=json.dumps({"s99": "nope"}), capture_output=True, text=True, cwd=str(td))
        assert r.returncode != 0 and "unknown section id 's99'" in r.stderr, r.stderr

        summaries = {s["id"]: f"uppercases {s['title'].split()[-1]}" for s in data["sections"]}
        r = subprocess.run(
            [sys.executable, str(LOOP), "--viva-dir", str(viva), "summarize", "--map", "-"],
            input=json.dumps(summaries), capture_output=True, text=True, cwd=str(td))
        assert r.returncode == 0, r.stderr
        assert f"{n} of {n} hunk(s)" in r.stdout, r.stdout
        armed = loop(viva, td, "arm")
        assert armed.returncode == 0 and "round 1 armed" in armed.stdout, armed.stderr
        base = (viva / "server.url").read_text().strip()
        try:
            served = get(base, "/input")
            assert all(s.get("summary") for s in served["sections"]), served["sections"][0]
            # Pre-arm only: the served round would never see a later merge.
            late = subprocess.run(
                [sys.executable, str(LOOP), "--viva-dir", str(viva), "summarize", "--map", "-"],
                input=json.dumps({"s1": "again"}), capture_output=True, text=True, cwd=str(td))
            assert late.returncode != 0 and "already armed" in late.stderr, late.stderr
        finally:
            loop(viva, td, "abandon")
        assert _server_gone(viva)

        r = loop(viva, td, "start", "--kind", "worktree", "--arm-anyway")
        assert r.returncode == 0 and "round 1 armed" in r.stdout, r.stderr
        loop(viva, td, "abandon")
    print("  ok  check_diff_start_stops_at_the_summaries_seam")


def check_start_refuses_a_bad_target_before_it_clears() -> None:
    """`review_target.py` runs before the pre-flight and before the clear, so a
    target it cannot classify — or one that reads as an injection — costs
    nothing: the prior round is still on disk afterwards."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        viva = td / ".viva"
        viva.mkdir()
        _git_repo(td)
        marker = viva / "review-input-r3.json"
        marker.write_text("{}")
        r = loop(viva, td, "start", "--target", "main; rm -rf /")
        assert r.returncode != 0, "an unclassifiable target must be refused"
        assert "review_target" in r.stderr, r.stderr
        assert marker.exists(), "a refused start must not have cleared"
        assert not (viva / "target.json").exists()
        r = loop(viva, td, "start", "--doc", "x.md", "--kind", "worktree")
        assert r.returncode != 0 and "one or the other" in r.stderr, r.stderr
    print("  ok  check_start_refuses_a_bad_target_before_it_clears")


def check_capture_failure_is_not_an_empty_diff() -> None:
    """A capture that fails must never read as "no changes". The motivating case
    is a `gh pr diff` 403 from the wrong account: a 0-byte `diff.patch` would sign
    the session off as fully resolved with nothing reviewed. Here it's a `git diff` against a nonexistent ref."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        viva = td / ".viva"
        viva.mkdir()
        _git_repo(td)
        r = loop(viva, td, "start", "--target", "no-such-ref", "--kind", "ref")
        assert r.returncode != 0, "a failed capture must not complete"
        assert "capture failed" in r.stderr and "git diff no-such-ref" in r.stderr, r.stderr
        assert "no changes to review" not in r.stdout, r.stdout
        assert not (viva / "diff.patch").exists(), "a failed capture leaves no patch behind"
        assert not (viva / "review-input-r1.json").exists()
        assert not (viva / "server.url").exists()
    print("  ok  check_capture_failure_is_not_an_empty_diff")


def check_start_runs_the_bundles_checks() -> None:
    """A type's `checks[]` run inside `start`, between the parse and any arming
    branch. The name is validated beside the type, BEFORE the clear, so a repo
    bundle naming a check this plugin doesn't ship is refused with prior state intact."""
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


def check_no_skill_carries_a_server_url_guard() -> None:
    """#174 fixed the collision message in `loop.py`, and #179 put every flow on
    the driver, so no skill stats `server.url` any more. `start`/`interview` probe
    before advising; a guard line reappearing in prose is the pre-#174 bash creeping back."""
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        guards = [ln for ln in skill_md.read_text().splitlines()
                  if "[ -f .viva/server.url ]" in ln]
        assert not guards, \
            "%s must not carry a server.url guard — the driver probes: %r" \
            % (skill_md.parent.name, guards)
    print("  ok  check_no_skill_carries_a_server_url_guard")


def check_start_resume_carries_prior_approvals() -> None:
    """`start`'s resume branch: a doc already carrying a sign-off ledger. The
    copy-out must happen before the clear glob or carry-forward dies with it, and
    the scratch pair must not survive. Also pins that `split_on`/`doc_type` are
    handed back — re-detecting the split changes every section's identity."""
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
    """A standing preference auto-engages the preference producer (an LLM pass), so
    `start` must stop after parsing rather than arm. The round file's absolute path
    must be printed, or the agent is back to computing `review-input-r{N}.json`."""
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

    Three things must hold: grounds reach the store as a `declined` thread, that
    thread carries into round N+1, and a second decline after the reviewer
    insists is refused before any round ships.
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

    `open_notes.py` refuses a second decline, but can't make the author decline
    for cause or comply once the reviewer has insisted — SKILL.md carries that rule.
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
    check_every_script_cross_imports_only_schema()
    check_server_cross_imports_only_schema_and_preferences()
    check_skill_carries_no_bookkeeping_bash()
    check_no_skill_carries_its_own_loop()
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
    check_no_skill_carries_a_server_url_guard()
    check_diff_round_trip()
    check_diff_finish_from_an_empty_recapture()
    check_finish_refuses_a_diff_that_changed_since_review()
    check_diff_start_stops_at_the_summaries_seam()
    check_start_refuses_a_bad_target_before_it_clears()
    check_capture_failure_is_not_an_empty_diff()
    check_start_resume_carries_prior_approvals()
    check_start_opens_the_producer_seam()
    check_wait_refuses_a_parsed_but_unarmed_round()
    print("OK")


if __name__ == "__main__":
    main()
