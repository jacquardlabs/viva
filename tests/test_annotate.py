#!/usr/bin/env python3
"""Tests for scripts/annotate.py — the shared annotation-merge helper.

Every producer (claim grounding, contradiction, drift, checklist) writes its
flags through this one path: a sidecar list of {id, kind, severity, message,
anchor?} merged into the round's review-input. The merge is additive
(preserves carried-forward annotations), idempotent (no duplicate on re-run),
and a no-op sidecar leaves the input byte-identical.

The last test covers the producer seam's *driver* end: `loop.py annotate`
supplies the round number so a producer call never re-templates `{N}` (#104).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "annotate.py"
LOOP = ROOT / "scripts" / "loop.py"


def run(review_input: dict, sidecar: list) -> dict:
    """Write input + sidecar to temp files, run the merge, return merged JSON."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        inp = t / "review-input.json"
        inp.write_text(json.dumps(review_input), encoding="utf-8")
        side = t / "sidecar.json"
        side.write_text(json.dumps(sidecar), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(inp), "--annotations", str(side)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(f"annotate exited {result.returncode}:\n{result.stderr}")
        return json.loads(inp.read_text(encoding="utf-8"))


def base_input(sections: list) -> dict:
    return {"mode": "review", "doc_file": "doc.md", "round": 1,
            "approved_ids": [], "sections": sections}


def test_merge_adds_annotation_to_section() -> None:
    data = base_input([{"id": "s1", "title": "Goals", "content": "body"}])
    out = run(data, [{"id": "s1", "kind": "grounding", "severity": "warn",
                      "message": "claim unsupported", "anchor": "line 3"}])
    s1 = out["sections"][0]
    assert s1["annotations"] == [
        {"kind": "grounding", "severity": "warn",
         "message": "claim unsupported", "anchor": "line 3"}
    ]


def test_merge_preserves_existing_annotations() -> None:
    # A carried-forward annotation must survive a new producer's append.
    existing = {"kind": "drift", "severity": "error", "message": "carried"}
    data = base_input([{"id": "s1", "title": "Goals", "content": "body",
                        "annotations": [existing]}])
    out = run(data, [{"id": "s1", "kind": "grounding", "severity": "warn",
                      "message": "new flag"}])
    annots = out["sections"][0]["annotations"]
    assert existing in annots, "carried-forward annotation dropped"
    assert {"kind": "grounding", "severity": "warn", "message": "new flag"} in annots
    assert len(annots) == 2


def test_merge_skips_unknown_id() -> None:
    data = base_input([{"id": "s1", "title": "Goals", "content": "body"}])
    out = run(data, [{"id": "s9", "kind": "x", "severity": "warn", "message": "orphan"}])
    assert "annotations" not in out["sections"][0], "unknown-id flag must not attach anywhere"


def test_merge_normalizes_bad_severity() -> None:
    data = base_input([{"id": "s1", "title": "Goals", "content": "body"}])
    out = run(data, [{"id": "s1", "kind": "x", "severity": "critical", "message": "m"}])
    assert out["sections"][0]["annotations"][0]["severity"] == "info"


def test_merge_is_idempotent() -> None:
    # Running the same producer twice must not duplicate an identical flag.
    data = base_input([{"id": "s1", "title": "Goals", "content": "body"}])
    flag = {"id": "s1", "kind": "grounding", "severity": "warn", "message": "dup"}
    once = run(data, [flag])
    twice = run(once, [flag])
    assert len(twice["sections"][0]["annotations"]) == 1, "identical flag duplicated"


def test_empty_sidecar_is_byte_identical() -> None:
    data = base_input([{"id": "s1", "title": "Goals", "content": "body"}])
    before = json.dumps(data, indent=2, ensure_ascii=False)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        inp = t / "in.json"
        inp.write_text(before, encoding="utf-8")
        side = t / "side.json"
        side.write_text("[]", encoding="utf-8")
        subprocess.run([sys.executable, str(SCRIPT), "--input", str(inp),
                        "--annotations", str(side)], capture_output=True, check=True)
        after = inp.read_text(encoding="utf-8")
    assert after == before, "empty sidecar must leave input byte-identical"


def test_missing_message_skipped() -> None:
    data = base_input([{"id": "s1", "title": "Goals", "content": "body"}])
    out = run(data, [{"id": "s1", "kind": "x", "severity": "warn"}])
    assert "annotations" not in out["sections"][0], "message-less flag must be skipped"


def test_confidence_basis_level_preserved() -> None:
    # Issue #40: a confidence annotation's structured sort keys must survive the
    # merge so it can route through annotate.py instead of bypassing it.
    data = base_input([{"id": "s1", "title": "Goals", "content": "body"}])
    out = run(data, [{"id": "s1", "kind": "confidence", "severity": "info",
                      "message": "inferred from context",
                      "basis": "inferred", "level": "low"}])
    annot = out["sections"][0]["annotations"][0]
    assert annot["basis"] == "inferred", annot
    assert annot["level"] == "low", annot
    # An out-of-vocab basis/level is dropped, not passed through verbatim.
    out2 = run(data, [{"id": "s1", "kind": "confidence", "severity": "info",
                       "message": "m", "basis": "bogus", "level": "huge"}])
    annot2 = out2["sections"][0]["annotations"][0]
    assert "basis" not in annot2 and "level" not in annot2, annot2


def test_split_on_survives_the_merge() -> None:
    # The producer seam is the path a task-card PLAN.md review takes whenever a
    # standing preference is in play: `start --split-on` stops after parsing,
    # `annotate` rewrites the round file in place, then `arm`. `rearm` later
    # reads `split_on` back off that same file, so a merge that rebuilt the
    # top-level dict instead of mutating it would drop the pattern and re-split
    # the next round by auto-detection — silently, with every carried approval
    # dying as the section boundaries moved.
    data = base_input([{"id": "s1", "title": "Task 1", "content": "body"}])
    data["split_on"] = r"^Task \d+"
    out = run(data, [{"id": "s1", "kind": "preference", "severity": "warn",
                      "message": "cite the source"}])
    assert out["split_on"] == r"^Task \d+", out


def test_loop_annotate_merges_into_the_derived_round() -> None:
    """`loop.py annotate --sidecar` names a sidecar and nothing else.

    The driver reads the highest `review-input-r{N}.json` on disk and merges
    there, so the producer seam never hands the round number back to the agent.
    Two rounds are on disk and only the later one may be touched; a `.viva/`
    with no round at all is a loud refusal, not a silent no-op.
    """
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        viva.mkdir()
        side = Path(tmp) / "sidecar.json"
        side.write_text(json.dumps([
            {"id": "s1", "kind": "preference", "severity": "warn",
             "message": "[cite-sources] '80% faster' has no source"}]),
            encoding="utf-8")

        empty = subprocess.run(
            [sys.executable, str(LOOP), "--viva-dir", str(viva),
             "annotate", "--sidecar", str(side)],
            capture_output=True, text=True)
        assert empty.returncode != 0, "no round on disk must refuse, not no-op"

        r1 = viva / "review-input-r1.json"
        r2 = viva / "review-input-r2.json"
        for path, rnd in ((r1, 1), (r2, 2)):
            data = base_input([{"id": "s1", "title": "Goals", "content": "body"}])
            data["round"] = rnd
            path.write_text(json.dumps(data), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(LOOP), "--viva-dir", str(viva),
             "annotate", "--sidecar", str(side)],
            capture_output=True, text=True)
        assert result.returncode == 0, f"loop annotate failed:\n{result.stderr}"
        merged = json.loads(r2.read_text(encoding="utf-8"))
        assert merged["sections"][0]["annotations"] == [
            {"kind": "preference", "severity": "warn",
             "message": "[cite-sources] '80% faster' has no source"}
        ], merged
        assert "annotations" not in json.loads(r1.read_text(encoding="utf-8"))["sections"][0], \
            "only the current round may be annotated"


def main() -> None:
    tests = [
        test_merge_adds_annotation_to_section,
        test_merge_preserves_existing_annotations,
        test_merge_skips_unknown_id,
        test_merge_normalizes_bad_severity,
        test_merge_is_idempotent,
        test_empty_sidecar_is_byte_identical,
        test_missing_message_skipped,
        test_confidence_basis_level_preserved,
        test_split_on_survives_the_merge,
        test_loop_annotate_merges_into_the_derived_round,
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
