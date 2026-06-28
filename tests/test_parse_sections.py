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
        test_no_diff_for_unchanged_carried_section,
        test_no_diff_for_new_section,
        test_no_diff_key_round_one,
        test_content_verbatim_no_whitespace_drift,
        test_nonzero_exit_on_missing_doc,
        test_doc_file_override,
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
