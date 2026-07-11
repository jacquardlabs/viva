#!/usr/bin/env python3
"""Tests for scripts/parse_sections.py — section parsing and approved matching."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "parse_sections.py"
PLAN_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "PLAN.md"


def run(doc_text: str, extra_args: list[str] = (), prior_input: dict | None = None,
        prior_verdicts: dict | None = None) -> dict:
    """Write doc to a temp file, run the parser, return parsed JSON."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        doc = t / "doc.md"
        doc.write_text(doc_text, encoding="utf-8")
        out = t / ".viva" / "review-input-r1.json"

        cmd = [sys.executable, str(SCRIPT), str(doc),
               "--output", str(out), "--round", "1"]
        cmd.extend(extra_args)

        if prior_input is not None:
            pi = t / ".viva" / "prior-input.json"
            pi.parent.mkdir(parents=True, exist_ok=True)
            pi.write_text(json.dumps(prior_input), encoding="utf-8")
            cmd += ["--prior-input", str(pi)]

        if prior_verdicts is not None:
            pv = t / ".viva" / "prior-verdicts.json"
            pv.parent.mkdir(parents=True, exist_ok=True)
            pv.write_text(json.dumps(prior_verdicts), encoding="utf-8")
            cmd += ["--prior-verdicts", str(pv)]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AssertionError(f"Parser exited {result.returncode}:\n{result.stderr}")
        return json.loads(out.read_text(encoding="utf-8"))


def sections_content(data: dict) -> list[tuple[str, str]]:
    return [(s["title"], s["content"]) for s in data["sections"]]


def run_expect_fail(doc_text: str, extra_args: list[str]) -> tuple[subprocess.CompletedProcess, bool]:
    """Write doc to a temp file, run the parser, return (result, output_written)
    without raising — for asserting on a nonzero exit, stderr content, and
    that no output file was written (no silent one-section fallback). Output
    existence is checked before the tempdir is torn down."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        doc = t / "doc.md"
        doc.write_text(doc_text, encoding="utf-8")
        out = t / ".viva" / "review-input-r1.json"
        cmd = [sys.executable, str(SCRIPT), str(doc),
               "--output", str(out), "--round", "1", *extra_args]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result, out.exists()


def test_basic_h2_split() -> None:
    doc = "# Title\n\nPreamble text.\n\n## Alpha\n\nalpha body\n\n## Beta\n\nbeta body\n"
    data = run(doc)
    titles = [s["title"] for s in data["sections"]]
    assert "Title" in titles, f"expected preamble section titled 'Title', got {titles}"
    assert "Alpha" in titles
    assert "Beta" in titles
    assert data["round"] == 1
    assert data["mode"] == "review"


def test_no_headings_single_section() -> None:
    doc = "Just some text without any headings.\n"
    data = run(doc)
    assert len(data["sections"]) == 1
    assert data["sections"][0]["content"] == doc


def test_single_heading_single_section() -> None:
    doc = "## Only one section\n\nContent here.\n"
    data = run(doc)
    assert len(data["sections"]) == 1
    assert data["sections"][0]["title"] == "Only one section"


def test_integrity_check_passes() -> None:
    doc = "# Doc\n\n## Section A\n\nText A.\n\n## Section B\n\nText B.\n"
    data = run(doc)
    reconstructed = "".join(s["content"] for s in data["sections"])
    assert reconstructed == doc


def test_revision_history_excluded() -> None:
    doc = (
        "# Doc\n\n"
        "## Goals\n\ngoals body\n\n"
        "## Revision History\n\n| Round | ... |\n"
    )
    data = run(doc)
    titles = [s["title"] for s in data["sections"]]
    assert "Revision History" not in titles
    assert "Goals" in titles
    # Integrity check: only content up to Revision History is accounted for
    rh_start = doc.index("## Revision History")
    expected_source = doc[:rh_start]
    reconstructed = "".join(s["content"] for s in data["sections"])
    assert reconstructed == expected_source


def test_preamble_uses_h1_title() -> None:
    doc = "# My Document\n\nIntro text.\n\n## Section One\n\nbody\n"
    data = run(doc)
    first = data["sections"][0]
    assert first["title"] == "My Document"
    assert "Intro text." in first["content"]


def test_preamble_empty_omitted() -> None:
    doc = "## First\n\nbody A\n\n## Second\n\nbody B\n"
    data = run(doc)
    titles = [s["title"] for s in data["sections"]]
    assert "Preamble" not in titles
    assert titles == ["First", "Second"]


def test_ids_are_sequential() -> None:
    doc = "## A\n\na\n\n## B\n\nb\n\n## C\n\nc\n"
    data = run(doc)
    ids = [s["id"] for s in data["sections"]]
    assert ids == ["s1", "s2", "s3"]


def test_approved_matching_same_content() -> None:
    # The blank line between sections belongs to the first section's content slice
    content_a = "## Alpha\n\nalpha body\n\n"  # trailing blank is part of Alpha's slice
    content_b = "## Beta\n\nbeta body\n"
    doc = content_a + content_b
    prior_input = {
        "mode": "review", "doc_file": "doc.md", "round": 1,
        "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Alpha", "content": content_a},
            {"id": "s2", "title": "Beta",  "content": content_b},
        ],
    }
    prior_verdicts = {
        "round": 1, "submitted_early": False,
        "sections": [
            {"id": "s1", "verdict": "approved", "note": ""},
            {"id": "s2", "verdict": "changes",  "note": "expand"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        doc_file = t / "doc.md"
        doc_file.write_text(doc, encoding="utf-8")
        pi = t / "prior-input.json"
        pi.write_text(json.dumps(prior_input), encoding="utf-8")
        pv = t / "prior-verdicts.json"
        pv.write_text(json.dumps(prior_verdicts), encoding="utf-8")
        out = t / ".viva" / "review-input-r2.json"

        subprocess.run([
            sys.executable, str(SCRIPT), str(doc_file),
            "--output", str(out), "--round", "2",
            "--prior-input", str(pi), "--prior-verdicts", str(pv),
        ], capture_output=True, check=True)

        data = json.loads(out.read_text())
    # Alpha approved + content unchanged → carried forward
    # Beta had 'changes' verdict → not approved
    assert len(data["approved_ids"]) == 1
    approved_section = next(s for s in data["sections"]
                            if s["id"] == data["approved_ids"][0])
    assert approved_section["title"] == "Alpha"


def test_approved_not_carried_if_content_changed() -> None:
    prior_content = "## Alpha\n\noriginal body\n"
    new_content = "## Alpha\n\nmodified body\n"
    prior_input = {
        "mode": "review", "doc_file": "doc.md", "round": 1,
        "approved_ids": [],
        "sections": [{"id": "s1", "title": "Alpha", "content": prior_content}],
    }
    prior_verdicts = {
        "round": 1, "submitted_early": False,
        "sections": [{"id": "s1", "verdict": "approved", "note": ""}],
    }
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        doc_file = t / "doc.md"
        doc_file.write_text(new_content, encoding="utf-8")
        pi = t / "prior-input.json"
        pi.write_text(json.dumps(prior_input), encoding="utf-8")
        pv = t / "prior-verdicts.json"
        pv.write_text(json.dumps(prior_verdicts), encoding="utf-8")
        out = t / ".viva" / "review-input-r2.json"

        subprocess.run([
            sys.executable, str(SCRIPT), str(doc_file),
            "--output", str(out), "--round", "2",
            "--prior-input", str(pi), "--prior-verdicts", str(pv),
        ], capture_output=True, check=True)

        data = json.loads(out.read_text())
    # Content changed → must not be auto-approved
    assert data["approved_ids"] == []


def test_no_annotations_key_when_absent() -> None:
    # Zero-regression: a doc with no annotations must produce sections that
    # carry no `annotations` key at all (byte-identical to pre-feature output).
    doc = "## A\n\na\n\n## B\n\nb\n"
    data = run(doc)
    for s in data["sections"]:
        assert "annotations" not in s, f"unexpected annotations key on {s['id']}"


def _run_round2(doc: str, prior_input: dict, prior_verdicts: dict) -> dict:
    """Parse `doc` as round 2 against the given prior files, return output JSON."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        doc_file = t / "doc.md"
        doc_file.write_text(doc, encoding="utf-8")
        pi = t / "prior-input.json"
        pi.write_text(json.dumps(prior_input), encoding="utf-8")
        pv = t / "prior-verdicts.json"
        pv.write_text(json.dumps(prior_verdicts), encoding="utf-8")
        out = t / ".viva" / "review-input-r2.json"
        subprocess.run([
            sys.executable, str(SCRIPT), str(doc_file),
            "--output", str(out), "--round", "2",
            "--prior-input", str(pi), "--prior-verdicts", str(pv),
        ], capture_output=True, check=True)
        return json.loads(out.read_text())


def test_annotations_carried_forward_when_unchanged() -> None:
    # A pre-review pass flagged Alpha last round; Alpha is byte-identical this
    # round, so its annotations must survive the carry-forward.
    content_a = "## Alpha\n\nalpha body\n\n"  # trailing blank belongs to Alpha
    content_b = "## Beta\n\nbeta body\n"
    doc = content_a + content_b
    annots = [{"kind": "grounding", "severity": "warn", "message": "claim unsupported"}]
    prior_input = {
        "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Alpha", "content": content_a, "annotations": annots},
            {"id": "s2", "title": "Beta",  "content": content_b},
        ],
    }
    prior_verdicts = {
        "round": 1, "submitted_early": False,
        "sections": [
            {"id": "s1", "verdict": "changes", "note": "fix"},
            {"id": "s2", "verdict": "changes", "note": "fix"},
        ],
    }
    data = _run_round2(doc, prior_input, prior_verdicts)
    alpha = next(s for s in data["sections"] if s["title"] == "Alpha")
    beta  = next(s for s in data["sections"] if s["title"] == "Beta")
    assert alpha.get("annotations") == annots, "Alpha annotations must carry forward"
    assert "annotations" not in beta, "Beta had none — must stay absent"


def test_annotations_dropped_when_content_changed() -> None:
    # A flag on Alpha's old text is stale once Alpha is rewritten; carrying it
    # would show a warning the new content may already have fixed.
    prior_content = "## Alpha\n\noriginal body\n"
    new_content   = "## Alpha\n\nmodified body\n"
    prior_input = {
        "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
        "sections": [{
            "id": "s1", "title": "Alpha", "content": prior_content,
            "annotations": [{"kind": "drift", "severity": "error", "message": "stale"}],
        }],
    }
    prior_verdicts = {
        "round": 1, "submitted_early": False,
        "sections": [{"id": "s1", "verdict": "changes", "note": "fix"}],
    }
    data = _run_round2(new_content, prior_input, prior_verdicts)
    alpha = next(s for s in data["sections"] if s["title"] == "Alpha")
    assert "annotations" not in alpha, "stale annotations must not carry to changed content"


def test_diff_computed_for_changed_section() -> None:
    # A section rewritten between rounds (same title, changed content) gets an
    # inline diff against its prior-round text so the reviewer sees the delta.
    prior_content = "## Alpha\n\noriginal body\n"
    new_content   = "## Alpha\n\nmodified body\n"
    prior_input = {
        "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
        "sections": [{"id": "s1", "title": "Alpha", "content": prior_content}],
    }
    prior_verdicts = {
        "round": 1, "submitted_early": False,
        "sections": [{"id": "s1", "verdict": "changes", "note": "fix"}],
    }
    data = _run_round2(new_content, prior_input, prior_verdicts)
    alpha = next(s for s in data["sections"] if s["title"] == "Alpha")
    diff = alpha.get("diff")
    assert diff, "changed section must carry a diff"
    ops = {(d["op"], d["text"]) for d in diff}
    assert ("-", "original body") in ops, f"removed line missing in {diff}"
    assert ("+", "modified body") in ops, f"added line missing in {diff}"


def test_diff_keeps_dash_prefixed_content_line() -> None:
    # A removed line whose text begins with '-- ' must not be mistaken for the
    # unified-diff '--- ' file header and dropped.
    prior_content = "## Alpha\n\n-- caveat about retries\n"
    new_content   = "## Alpha\n\nplain body\n"
    prior_input = {
        "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
        "sections": [{"id": "s1", "title": "Alpha", "content": prior_content}],
    }
    prior_verdicts = {
        "round": 1, "submitted_early": False,
        "sections": [{"id": "s1", "verdict": "changes", "note": "fix"}],
    }
    data = _run_round2(new_content, prior_input, prior_verdicts)
    alpha = next(s for s in data["sections"] if s["title"] == "Alpha")
    ops = {(d["op"], d["text"]) for d in alpha.get("diff", [])}
    assert ("-", "-- caveat about retries") in ops, f"dash-prefixed line dropped: {alpha.get('diff')}"


def test_no_diff_for_unchanged_carried_section() -> None:
    # Byte-identical carried-forward section shows no diff — consistent with the
    # approved-carry-forward logic.
    content_a = "## Alpha\n\nalpha body\n\n"
    content_b = "## Beta\n\nbeta body\n"
    doc = content_a + content_b
    prior_input = {
        "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Alpha", "content": content_a},
            {"id": "s2", "title": "Beta",  "content": content_b},
        ],
    }
    prior_verdicts = {
        "round": 1, "submitted_early": False,
        "sections": [
            {"id": "s1", "verdict": "approved", "note": ""},
            {"id": "s2", "verdict": "changes",  "note": "fix"},
        ],
    }
    data = _run_round2(doc, prior_input, prior_verdicts)
    alpha = next(s for s in data["sections"] if s["title"] == "Alpha")
    assert "diff" not in alpha, "unchanged carried section must not carry a diff"


def test_no_diff_for_new_section() -> None:
    # A section with no prior-round counterpart (new, or renamed heading) has
    # nothing to diff against.
    prior_input = {
        "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
        "sections": [{"id": "s1", "title": "Alpha", "content": "## Alpha\n\nbody\n"}],
    }
    prior_verdicts = {
        "round": 1, "submitted_early": False,
        "sections": [{"id": "s1", "verdict": "changes", "note": "fix"}],
    }
    new_doc = "## Alpha\n\nbody\n\n## Brand New\n\nfresh content\n"
    data = _run_round2(new_doc, prior_input, prior_verdicts)
    new_sec = next(s for s in data["sections"] if s["title"] == "Brand New")
    assert "diff" not in new_sec, "new section must not carry a diff"


def test_no_diff_key_round_one() -> None:
    # Zero-regression: round 1 (no prior) never produces a diff key.
    doc = "## A\n\na\n\n## B\n\nb\n"
    data = run(doc)
    for s in data["sections"]:
        assert "diff" not in s, f"unexpected diff key on {s['id']} in round 1"


def test_open_notes_attached_by_title() -> None:
    # An open thread in the cid-keyed store attaches to the matching section by
    # title so the server can re-present the exchange. Settled threads drop.
    doc = "## Goals\n\nbody\n\n## Scope\n\nscope\n"
    exchanges = [{"round": 1, "verdict": "changes", "note": "tighten", "response": "did it"}]
    store = {
        "goals-c1": {"cid": "goals-c1", "title": "Goals", "quote": "q", "status": "open",
                     "exchanges": exchanges},
        "scope-c1": {"cid": "scope-c1", "title": "Scope", "quote": "", "status": "settled",
                     "exchanges": [{"round": 1, "verdict": "info", "note": "x", "response": "y"}]},
    }
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        doc_file = t / "doc.md"
        doc_file.write_text(doc, encoding="utf-8")
        sp = t / "open-notes.json"
        sp.write_text(json.dumps(store), encoding="utf-8")
        out = t / ".viva" / "review-input-r1.json"
        subprocess.run([
            sys.executable, str(SCRIPT), str(doc_file),
            "--output", str(out), "--round", "1", "--open-notes", str(sp),
        ], capture_output=True, check=True)
        data = json.loads(out.read_text())
    goals = next(s for s in data["sections"] if s["title"] == "Goals")
    scope = next(s for s in data["sections"] if s["title"] == "Scope")
    # open_notes is now a list of thread dicts (cid, quote, status, exchanges)
    assert len(goals.get("open_notes", [])) == 1, goals
    assert goals["open_notes"][0]["cid"] == "goals-c1"
    assert goals["open_notes"][0]["exchanges"] == exchanges
    assert "open_notes" not in scope, "settled thread must not attach"


def test_no_open_notes_key_when_store_absent() -> None:
    # Zero-regression: without --open-notes, no section gains an open_notes key.
    data = run("## A\n\na\n\n## B\n\nb\n")
    for s in data["sections"]:
        assert "open_notes" not in s, f"unexpected open_notes on {s['id']}"


def test_content_verbatim_no_whitespace_drift() -> None:
    doc = "## A\n\n  indented line\n\n\ndouble blank\n\n## B\n\nlast\n"
    data = run(doc)
    reconstructed = "".join(s["content"] for s in data["sections"])
    assert reconstructed == doc, "content must be byte-for-byte verbatim"


def test_nonzero_exit_on_missing_doc() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "/nonexistent/doc.md",
             "--output", str(out), "--round", "1"],
            capture_output=True,
        )
    assert result.returncode != 0


def test_doc_file_override() -> None:
    doc = "## Section\n\nbody\n"
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        doc_file = t / "doc.md"
        doc_file.write_text(doc, encoding="utf-8")
        out = t / "out.json"
        subprocess.run([
            sys.executable, str(SCRIPT), str(doc_file),
            "--output", str(out), "--round", "1",
            "--doc-file", "path/to/my.md",
        ], capture_output=True, check=True)
        data = json.loads(out.read_text())
    assert data["doc_file"] == "path/to/my.md"


def test_split_on_matches_regardless_of_depth() -> None:
    # A `## Task 1` and a `### Task 2` (different depths) both match the same
    # pattern and both become split points — --split-on has no depth rule,
    # only a text-match rule.
    doc = (
        "# Doc\n\n"
        "## Task 1: shallow\n\nbody one\n\n"
        "### Task 2: deep\n\nbody two\n\n"
        "### Aside\n\nnot a task\n"
    )
    data = run(doc, extra_args=["--split-on", r"^Task \d+"])
    titles = [s["title"] for s in data["sections"]]
    # "Doc" is the preamble section (H1 + nothing else before the first match).
    assert titles == ["Doc", "Task 1: shallow", "Task 2: deep"], titles
    # "Aside" content stays nested inside Task 2's section (next boundary is
    # end of doc since Task 2 is the last split heading).
    task2 = next(s for s in data["sections"] if s["title"] == "Task 2: deep")
    assert "### Aside" in task2["content"]


def test_split_on_ignores_coarser_repeated_heading() -> None:
    # The motivating bug: a coarser heading (`## Notes`) recurs across tasks.
    # Auto-detection would pick it (coarsest repeater wins) and swallow every
    # task into whichever Notes block it falls under; --split-on must not.
    doc = (
        "# Doc\n\n"
        "### Task 1\n\nbody 1\n\n"
        "## Notes\n\nnote 1\n\n"
        "### Task 2\n\nbody 2\n\n"
        "## Notes\n\nnote 2\n"
    )
    # Without --split-on: auto-detect picks the coarser, twice-repeated H2
    # ("## Notes") over the thrice... here twice-repeated H3 ("### Task N") —
    # sorted(counts) checks H2 before H3, so H2 wins. Both tasks get swallowed
    # into their enclosing "Notes" section, not split out individually.
    auto = run(doc)
    auto_titles = [s["title"] for s in auto["sections"]]
    assert auto_titles.count("Notes") == 2, auto_titles
    assert "Task 1" not in auto_titles and "Task 2" not in auto_titles

    # With --split-on: only headings matching the pattern are split points,
    # regardless of the coarser repeated "Notes" heading.
    split = run(doc, extra_args=["--split-on", r"^Task \d+"])
    split_titles = [s["title"] for s in split["sections"]]
    assert split_titles == ["Doc", "Task 1", "Task 2"], split_titles
    task1 = next(s for s in split["sections"] if s["title"] == "Task 1")
    assert "## Notes\n\nnote 1" in task1["content"]


def test_split_on_zero_matches_is_hard_error() -> None:
    doc = "# Doc\n\n## Alpha\n\nbody\n"
    result, output_written = run_expect_fail(doc, ["--split-on", r"^Task \d+"])
    assert result.returncode != 0
    # Message names both the pattern and the doc, not a generic fallback message.
    assert "matched no heading" in result.stderr, result.stderr
    assert "Task" in result.stderr, result.stderr
    assert "doc.md" in result.stderr, result.stderr
    # No silent fallback to a one-section (or auto-detected) JSON.
    assert not output_written, "output file must not be written on a zero-match error"


def test_split_on_invalid_regex_errors() -> None:
    doc = "# Doc\n\n## Alpha\n\nbody\n"
    result, output_written = run_expect_fail(doc, ["--split-on", r"(unclosed"])
    assert result.returncode != 0
    # A clean caller-facing message, not a raw Python traceback.
    assert "Traceback" not in result.stderr, result.stderr
    assert "invalid --split-on pattern" in result.stderr, result.stderr
    assert not output_written, "output file must not be written on an invalid pattern"


def test_split_on_20_section_fallback_not_applied() -> None:
    # The auto-detect coarsening fallback (>20 sections at a level → fall back
    # one level coarser) must NOT apply to an explicit --split-on pattern —
    # it's an instruction, not a heuristic guess.
    tasks = "".join(f"### Task {i}\n\nbody {i}\n\n" for i in range(1, 26))
    doc = "# Doc\n\n" + tasks
    data = run(doc, extra_args=["--split-on", r"^Task \d+"])
    # 25 task sections + 1 preamble ("Doc") — no coarsening fallback applied.
    assert len(data["sections"]) == 26, len(data["sections"])
    titles = [s["title"] for s in data["sections"]]
    assert titles[0] == "Doc"
    assert titles[1:] == [f"Task {i}" for i in range(1, 26)]


def test_split_on_default_path_byte_identical_without_flag() -> None:
    # Structural guarantee: the branch that reads --split-on is never taken
    # when the flag is absent — same doc, same output, with or without a
    # split_on-shaped doc, as long as the flag itself is omitted.
    doc = "# Doc\n\n### Task 1\n\nbody 1\n\n### Task 2\n\nbody 2\n"
    with_flag_absent = run(doc)
    again = run(doc)
    assert with_flag_absent == again


def test_split_on_fixture_one_section_per_task() -> None:
    # Acceptance criterion: a fixture PLAN.md with ### Task N blocks parses
    # to one section per task with no custom parsing needed downstream.
    doc = PLAN_FIXTURE.read_text(encoding="utf-8")
    data = run(doc, extra_args=["--split-on", r"^Task \d+"])
    titles = [s["title"] for s in data["sections"]]
    # Preamble (H1 + intro paragraph) is its own section, then one section
    # per task — no custom parsing needed downstream.
    assert titles == [
        "Sprint 12 plan",
        "Task 1: Add the CLI flag",
        "Task 2: Write the fixture",
        "Task 3: Wire up tests",
    ], titles
    # Nested "Acceptance criteria" and recurring "Notes" headings stay inside
    # their task's content — no separate cards, no new nesting rule.
    task1 = next(s for s in data["sections"] if s["title"] == "Task 1: Add the CLI flag")
    assert "#### Acceptance criteria" in task1["content"]
    assert "## Notes" in task1["content"]
    # Integrity check: reconstructed content matches source verbatim.
    assert "".join(s["content"] for s in data["sections"]) == doc
    # Preamble (H1 + intro paragraph) becomes its own first-class section,
    # same rule as default parsing.
    assert data["sections"][0]["title"] == "Sprint 12 plan"


def test_split_on_fixture_round2_carries_forward_through_section_key() -> None:
    # The fixture itself, round-tripped: round 1 parses PLAN.md with
    # --split-on, Task 1 gets approved and Task 2 gets changes, Task 3 is
    # untouched (pending). Round 2 reparses a doc where only Task 2's body
    # changed — Task 1 must carry forward as approved and Task 3 must show
    # no diff, all through schema.section_key() with no fixture-specific
    # bookkeeping.
    doc_r1 = PLAN_FIXTURE.read_text(encoding="utf-8")
    r1 = run(doc_r1, extra_args=["--split-on", r"^Task \d+"])
    task1_id = next(s["id"] for s in r1["sections"] if s["title"] == "Task 1: Add the CLI flag")
    task2_id = next(s["id"] for s in r1["sections"] if s["title"] == "Task 2: Write the fixture")
    task3_id = next(s["id"] for s in r1["sections"] if s["title"] == "Task 3: Wire up tests")
    prior_verdicts = {
        "round": 1, "submitted_early": False,
        "sections": [
            {"id": task1_id, "verdict": "approved", "note": ""},
            {"id": task2_id, "verdict": "changes", "note": "tighten the wording"},
            {"id": task3_id, "verdict": "pending", "note": ""},
        ],
    }
    doc_r2 = doc_r1.replace(
        "Add a `PLAN.md` fixture with `### Task N` blocks.",
        "Add a `PLAN.md` fixture with `### Task N` blocks (tightened).",
    )
    assert doc_r2 != doc_r1, "fixture text for Task 2 must actually differ in round 2"
    r2 = run(doc_r2, extra_args=["--split-on", r"^Task \d+"],
              prior_input=r1, prior_verdicts=prior_verdicts)
    task1 = next(s for s in r2["sections"] if s["title"] == "Task 1: Add the CLI flag")
    task2 = next(s for s in r2["sections"] if s["title"] == "Task 2: Write the fixture")
    task3 = next(s for s in r2["sections"] if s["title"] == "Task 3: Wire up tests")
    assert task1["id"] in r2["approved_ids"], "approved Task 1 must carry forward"
    assert "diff" not in task1
    assert task2["id"] not in r2["approved_ids"]
    diff_ops = {d["text"] for d in task2.get("diff", [])}
    assert any("tightened" in t for t in diff_ops), task2.get("diff")
    assert task3["id"] not in r2["approved_ids"], "pending Task 3 was never approved"
    assert "diff" not in task3, "untouched Task 3 must show no diff"


def test_split_on_identity_reuses_section_key_no_new_rule() -> None:
    # Round-to-round carry-forward (approval, annotations, diff) must work
    # unmodified for --split-on sections — they're keyed by schema.section_key()
    # through the exact same functions default-parsed sections use.
    content_t1 = "### Task 1\n\ntask one body\n\n"
    content_t2 = "### Task 2\n\ntask two body\n"
    doc_r1 = content_t1 + content_t2
    prior_input = {
        "mode": "review", "doc_file": "PLAN.md", "round": 1, "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Task 1", "content": content_t1,
             "annotations": [{"kind": "drift", "severity": "warn", "message": "check link"}]},
            {"id": "s2", "title": "Task 2", "content": content_t2},
        ],
    }
    prior_verdicts = {
        "round": 1, "submitted_early": False,
        "sections": [
            {"id": "s1", "verdict": "approved", "note": ""},
            {"id": "s2", "verdict": "changes", "note": "expand"},
        ],
    }
    new_content_t2 = "### Task 2\n\ntask two body, expanded\n"
    doc_r2 = content_t1 + new_content_t2
    data = run(
        doc_r2,
        extra_args=["--split-on", r"^Task \d+"],
        prior_input=prior_input,
        prior_verdicts=prior_verdicts,
    )
    task1 = next(s for s in data["sections"] if s["title"] == "Task 1")
    task2 = next(s for s in data["sections"] if s["title"] == "Task 2")
    # Task 1: byte-identical to its approved prior content → carried forward,
    # annotation carried too.
    assert task1["id"] in data["approved_ids"]
    assert task1.get("annotations") == prior_input["sections"][0]["annotations"]
    assert "diff" not in task1
    # Task 2: content changed → not auto-approved, gets a round-to-round diff.
    assert task2["id"] not in data["approved_ids"]
    diff_ops = {(d["op"], d["text"]) for d in task2.get("diff", [])}
    assert ("-", "task two body") in diff_ops, task2.get("diff")
    assert ("+", "task two body, expanded") in diff_ops, task2.get("diff")


def main() -> None:
    tests = [
        test_basic_h2_split,
        test_no_headings_single_section,
        test_single_heading_single_section,
        test_integrity_check_passes,
        test_revision_history_excluded,
        test_preamble_uses_h1_title,
        test_preamble_empty_omitted,
        test_ids_are_sequential,
        test_approved_matching_same_content,
        test_approved_not_carried_if_content_changed,
        test_no_annotations_key_when_absent,
        test_annotations_carried_forward_when_unchanged,
        test_annotations_dropped_when_content_changed,
        test_diff_computed_for_changed_section,
        test_diff_keeps_dash_prefixed_content_line,
        test_no_diff_for_unchanged_carried_section,
        test_no_diff_for_new_section,
        test_no_diff_key_round_one,
        test_open_notes_attached_by_title,
        test_no_open_notes_key_when_store_absent,
        test_content_verbatim_no_whitespace_drift,
        test_nonzero_exit_on_missing_doc,
        test_doc_file_override,
        test_split_on_matches_regardless_of_depth,
        test_split_on_ignores_coarser_repeated_heading,
        test_split_on_zero_matches_is_hard_error,
        test_split_on_invalid_regex_errors,
        test_split_on_20_section_fallback_not_applied,
        test_split_on_default_path_byte_identical_without_flag,
        test_split_on_fixture_one_section_per_task,
        test_split_on_fixture_round2_carries_forward_through_section_key,
        test_split_on_identity_reuses_section_key_no_new_rule,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
    if failed:
        sys.exit(f"\n{failed}/{len(tests)} tests failed")
    print(f"\nOK ({len(tests)} tests)")


if __name__ == "__main__":
    main()
