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

The last four checks guard the other half of the same contract — `SKILL.md`
itself. The driver only removes bookkeeping from the agent if the prose stops
carrying it, so the documented sequence is asserted here beside the executed
one: no bash block does the driver's job, the rewrite step still applies
standing preferences, nothing is auto-approved, and every `references/` file is
one `loop.py` prints the path to.
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
SKILL = ROOT / ".claude" / "skills" / "viva" / "SKILL.md"
REFERENCES = ROOT / ".claude" / "skills" / "viva" / "references"

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
    print("  ok  check_untyped_session_records_no_doc_type")


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


def _numbered_step(text: str, keyword: str) -> str:
    """Body of the `**N. Title**` step whose title names `keyword`."""
    parts = re.split(r"^\*\*(\d+\.[^*]*)\*\*", text, flags=re.M)
    for header, body in zip(parts[1::2], parts[2::2]):
        if keyword.lower() in header.lower():
            return header + body
    raise AssertionError("SKILL.md has no numbered step naming %r" % keyword)


def check_skill_carries_no_bookkeeping_bash() -> None:
    """The prose half of the defect: SKILL.md retyped the loop every round."""
    text = SKILL.read_text()
    blocks = BASH_BLOCK_RE.findall(text)
    assert blocks, "SKILL.md has no bash block at all — the $VIVA_DIR resolve is one"
    for block in blocks:
        for label, pattern in FORBIDDEN_BASH:
            m = pattern.search(block)
            assert not m, (
                "a SKILL.md bash block %s (matched %r) — loop.py owns that:\n%s"
                % (label, m.group(0), block))
    print("  ok  check_skill_carries_no_bookkeeping_bash")


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


def check_references_are_reachable() -> None:
    """Every reference file is one the agent is *told* the path to.

    `loop.py` never reads `references/` — the agent does, and it has no
    `$VIVA_DIR` for a skill-relative path. So the driver prints the absolute
    path in the output line whose next step that file documents. Set equality
    both ways: a file nothing prints is unreachable, and a printed path with no
    file behind it makes "reachable" hollow.
    """
    assert REFERENCES.is_dir(), "%s does not exist" % REFERENCES
    on_disk = {p.name for p in REFERENCES.iterdir() if p.is_file()}
    assert on_disk, "references/ is empty — 'every file is reachable' is vacuous"
    # Either quote style: an f-string delimited by `"` cannot nest `"` before
    # Python 3.12, so the driver's own print sites use single quotes inside.
    named = set(re.findall(r"""REFERENCES / ["']([^"']+)["']""", LOOP.read_text()))
    assert on_disk == named, (
        "references/ and the paths loop.py prints disagree — unreachable: %s; "
        "dangling: %s" % (sorted(on_disk - named), sorted(named - on_disk)))

    # `\b` on both ends: "preferences" carries "references" as a substring, and
    # `$VIVA_DIR/scripts/preferences.py` is a legitimate command.
    interp = re.compile(r"VIVA_DIR[^\n]*\breferences\b")
    for path in [SKILL, LOOP] + sorted(REFERENCES.iterdir()):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            assert not interp.search(line), (
                "%s:%d routes a references path through $VIVA_DIR — the agent "
                "is told the path, never asked to compute it: %s"
                % (path.name, i, line.strip()))
    print("  ok  check_references_are_reachable")


def check_start_refuses_over_a_live_session() -> None:
    """`start` clears the round files and `server.url`. Without the pre-flight
    guard it does that to a *live* session, orphaning a running server with the
    reviewer's tab still attached — unrecoverable, and invisible until someone
    notices the orphan."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        doc = td / "d.md"
        doc.write_text("# T\n\n## A\n\naaa\n\n## B\n\nbbb\n")
        (viva / "server.url").write_text("http://127.0.0.1:1\n")

        r = loop(viva, td, "start", "--doc", str(doc))
        assert r.returncode != 0, "start must refuse over a live session's server.url"
        assert "may still be running" in r.stderr, r.stderr
        assert not (viva / "review-input-r1.json").exists(), \
            "start must not parse a round when it refuses — the refusal is the point"
        assert (viva / "server.url").exists(), \
            "start must not delete the live session's server.url"
    print("  ok  check_start_refuses_over_a_live_session")


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


def main() -> None:
    check_round_trip()
    check_split_on_session()
    check_doc_type_session()
    check_untyped_session_records_no_doc_type()
    check_resume_warns_on_a_type_that_no_longer_resolves()
    check_no_subcommand_takes_a_round()
    check_loop_cross_imports_only_schema()
    check_skill_carries_no_bookkeeping_bash()
    check_rewrite_step_applies_standing_preferences()
    check_no_auto_approve_and_paused_branch_routed()
    check_references_are_reachable()
    check_start_refuses_over_a_live_session()
    check_start_resume_carries_prior_approvals()
    check_start_opens_the_producer_seam()
    check_wait_refuses_a_parsed_but_unarmed_round()
    print("OK")


if __name__ == "__main__":
    main()
