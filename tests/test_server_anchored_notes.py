#!/usr/bin/env python3
"""Integration test: multi-comment round-trip (comments[] model).

Each section carries a `comments[]` list of {cid, type, note, anchor?, open,
settled}. The section verdict is DERIVED — never stored as a bare scalar.
The page ships renderHighlights / openCommentPopover / deriveVerdict and does
NOT ship the retired single-anchor controls (anchor-chip, rpin-, anchorSelection).

Contract verified here:
  - /submit passes comments[] through into out.json unchanged.
  - Anchored comments carry anchor.offset; un-anchored ones have no `anchor` key.
  - Derived verdict on the section matches the dominant comment type.
  - Retired page needles are absent; new interaction needles are present.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get_text, launch_server, post  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {
        "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "## Goals\nsub-second latency\n"},
            {"id": "s2", "title": "Scope", "content": "## Scope\nbody\n"},
        ],
    }
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:

        # Submit s1 with two comments (changes + info, first anchored) and
        # s2 with one un-anchored info comment.
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "comments": [
                {"cid": "s1-c1", "type": "changes", "note": "unsupported",
                 "anchor": {"text": "sub-second latency", "offset": 9}, "open": True, "settled": False},
                {"cid": "s1-c2", "type": "info", "note": "errors?", "open": True, "settled": False}]},
            {"id": "s2", "verdict": "info", "comments": [
                {"cid": "s2-c1", "type": "info", "note": "whole-section question", "open": True, "settled": False}]}]})

        out = json.loads((viva / "out1.json").read_text())
        s1 = next(s for s in out["sections"] if s["id"] == "s1")
        s2 = next(s for s in out["sections"] if s["id"] == "s2")

        # Derived verdict from dominant comment type; comments array preserved.
        assert s1["verdict"] == "changes" and len(s1["comments"]) == 2, s1
        # Each comment carries its stable cid through the pipe.
        assert [c["cid"] for c in s1["comments"]] == ["s1-c1", "s1-c2"], s1
        # Anchored comment carries offset through the pipe.
        assert s1["comments"][0]["anchor"]["offset"] == 9, s1
        # Un-anchored comment: no `anchor` key, and a lone info comment derives `info`.
        assert s2["verdict"] == "info", s2
        assert "anchor" not in s2["comments"][0], s2

        # Page ships the interaction-model needles and NOT the retired ones.
        page = get_text(base, "/")
        for needle in ("renderHighlights", "openCommentPopover", "deriveVerdict"):
            assert needle in page, f"page missing: {needle}"
        for gone in ("anchor-chip", "rpin-", "anchorSelection"):
            assert gone not in page, f"retired control still present: {gone}"

        print("OK")


if __name__ == "__main__":
    main()
