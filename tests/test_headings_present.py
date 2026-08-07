#!/usr/bin/env python3
"""Tests for scripts/headings_present.py — the doc-type grammar check.

The one check that ships with type bundles: given a round and a bundle, report
the bundle's expected headings the round does not carry, as a producer sidecar
`annotate.py` merges. The end-to-end case at the bottom is the load-bearing one
— a later completion check reads these results back off the round file by
`kind`, so the flag has to survive the merge, not just be printed.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "headings_present.py"
DOC_TYPES = ROOT / "scripts" / "doc_types.py"
ANNOTATE = ROOT / "scripts" / "annotate.py"
PARSE = ROOT / "scripts" / "parse_sections.py"

BUNDLE = {
    "name": "design-doc", "title": "Design doc",
    "sections": ["Problem & persona", "Proposed design", "Out of scope"],
    "checks": ["headings-present"], "default_pass": "structure",
}


def run(input_data: dict, bundle, tmp: Path) -> subprocess.CompletedProcess:
    """Run the producer with the bundle on stdin, as the documented pipe does."""
    inp = tmp / "review-input-r1.json"
    inp.write_text(json.dumps(input_data), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(inp), "--bundle", "-"],
        input=bundle if isinstance(bundle, str) else json.dumps(bundle),
        capture_output=True, text=True)


def sidecar(input_data: dict, bundle=BUNDLE) -> list:
    with tempfile.TemporaryDirectory() as td:
        r = run(input_data, bundle, Path(td))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def section(sid: str, title: str, content: str = "") -> dict:
    return {"id": sid, "title": title, "content": content or f"## {title}\n\nbody\n"}


def test_missing_headings_reported_in_bundle_order() -> None:
    flags = sidecar({"sections": [
        section("s1", "Design doc", "# Design doc\n\nintro\n"),
        section("s2", "Proposed design"),
    ]})
    assert [f["message"] for f in flags] == [
        "missing expected design-doc section: 'Problem & persona'",
        "missing expected design-doc section: 'Out of scope'",
    ], flags
    print("  ok  test_missing_headings_reported_in_bundle_order")


def test_flags_anchor_on_the_first_card() -> None:
    """`parse_sections.py`'s integrity check forbids a card whose content is not
    in the source doc, so a missing section can have no card of its own — the
    first card is the document-level anchor (checklist.py's constraint)."""
    flags = sidecar({"sections": [
        section("s1", "Design doc", "# Design doc\n\nintro\n"),
        section("s2", "Proposed design"),
    ]})
    assert flags and all(f["id"] == "s1" for f in flags), flags
    for f in flags:
        assert f["kind"] == "headings-present", f
        assert f["severity"] == "warn", f
        assert f["anchor"] == "design-doc grammar", f
    print("  ok  test_flags_anchor_on_the_first_card")


def test_complete_grammar_emits_nothing() -> None:
    assert sidecar({"sections": [
        section("s1", "Problem & persona"),
        section("s2", "Proposed design"),
        section("s3", "Out of scope"),
    ]}) == []
    print("  ok  test_complete_grammar_emits_nothing")


def test_in_body_heading_counts_as_present() -> None:
    """A `--split-on` round only promotes matching headings to cards: a plan
    split on `^Task \\d+` folds `## Not-here follow-ups` into the last card's
    body. Scanning card titles alone would report a heading the doc has."""
    plan = {"name": "plan", "title": "Build plan",
            "sections": ["Not-here follow-ups"], "checks": ["headings-present"],
            "default_pass": "structure"}
    body = ("### Task 2 — write it\n\nbody two\n\n"
            "## Not-here follow-ups\n\n- something later\n")
    assert sidecar({"sections": [
        section("s1", "Sprint plan", "# Sprint plan\n\nintro\n"),
        {"id": "s2", "title": "Task 2 — write it", "content": body},
    ]}, plan) == []
    print("  ok  test_in_body_heading_counts_as_present")


def test_matching_is_identity_not_the_fuzzy_template_match() -> None:
    """`schema.section_key` — case-folded and edge-trimmed, punctuation intact.
    Deliberately not `checklist.py._norm`, which strips all punctuation for
    tolerant template matching (CLAUDE.md keeps the two rules separate)."""
    bundle = dict(BUNDLE, sections=["Out of scope"])
    assert sidecar({"sections": [section("s1", "  OUT OF SCOPE  ")]}, bundle) == [], \
        "identity is case-folded and edge-trimmed"

    punct = dict(BUNDLE, sections=["Non-goals"])
    assert sidecar({"sections": [section("s1", "Non goals")]}, punct), \
        "identity keeps internal punctuation — a fuzzy match here would make " \
        "the grammar unenforceable and duplicate checklist.py's rule"
    print("  ok  test_matching_is_identity_not_the_fuzzy_template_match")


def test_empty_grammar_and_empty_round_emit_nothing() -> None:
    empty = dict(BUNDLE, name="pr-description", sections=[])
    assert sidecar({"sections": [section("s1", "Anything")]}, empty) == []
    assert sidecar({"sections": []}) == []
    print("  ok  test_empty_grammar_and_empty_round_emit_nothing")


def test_unusable_bundle_fails_loudly() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for bad in ("{ not json", "[]", '"design-doc"'):
            r = run({"sections": [section("s1", "A")]}, bad, tmp)
            assert r.returncode != 0, f"{bad!r} must not exit 0"
            assert r.stdout.strip() == "", f"{bad!r} printed a sidecar anyway"
    print("  ok  test_unusable_bundle_fails_loudly")


def test_results_survive_annotate_and_are_findable_by_kind() -> None:
    """The whole pipeline, as documented: resolve the type, run the check, merge
    it. A later completion check reads the results off the round file by
    `kind == "headings-present"`, so this is the contract that has to hold."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        doc = tmp / "design.md"
        doc.write_text(
            "# Design: a thing\n\n"
            "## Problem & persona\n\nwho\n\n"
            "## Proposed design\n\nwhat\n\n"
            "## User journey\n\nhow\n\n"
            "## Alternatives considered\n\nother\n\n"
            "## Open questions\n\nq\n", encoding="utf-8")
        inp = tmp / ".viva" / "review-input-r1.json"
        subprocess.run(
            [sys.executable, str(PARSE), str(doc), "--output", str(inp),
             "--round", "1", "--doc-type", "design-doc"],
            check=True, capture_output=True)

        bundle = subprocess.run([sys.executable, str(DOC_TYPES), "design-doc"],
                                capture_output=True, text=True, check=True).stdout
        flags = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(inp), "--bundle", "-"],
            input=bundle, capture_output=True, text=True, check=True).stdout
        subprocess.run(
            [sys.executable, str(ANNOTATE), "--input", str(inp),
             "--annotations", "-"],
            input=flags, capture_output=True, text=True, check=True)

        merged = json.loads(inp.read_text(encoding="utf-8"))
        assert merged["doc_type"] == "design-doc", merged.get("doc_type")
        results = [a for s in merged["sections"]
                   for a in s.get("annotations", [])
                   if a.get("kind") == "headings-present"]
        assert len(results) == 1, results
        assert "Out of scope" in results[0]["message"], results
    print("  ok  test_results_survive_annotate_and_are_findable_by_kind")


def main() -> None:
    test_missing_headings_reported_in_bundle_order()
    test_flags_anchor_on_the_first_card()
    test_complete_grammar_emits_nothing()
    test_in_body_heading_counts_as_present()
    test_matching_is_identity_not_the_fuzzy_template_match()
    test_empty_grammar_and_empty_round_emit_nothing()
    test_unusable_bundle_fails_loudly()
    test_results_survive_annotate_and_are_findable_by_kind()
    print("OK (8 tests)")


if __name__ == "__main__":
    main()
