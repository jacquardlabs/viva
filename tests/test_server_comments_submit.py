#!/usr/bin/env python3
"""Integration: a section submits multiple typed comments; verdict is derived."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get_text, launch_server, poll_for, post  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"; viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "## Goals\nretries 3x\nlog to stderr\n"},
                       {"id": "s2", "title": "Scope", "content": "## Scope\nbody\n"}]}
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        # The page ships the new comment machinery.
        page = get_text(base, "/")
        for needle in ("deriveVerdict", "addComment", "comments",
                       "openCommentPopover", "renderHighlights", "cmt-hl-changes", "add note"):
            assert needle in page, f"page missing: {needle}"

        # A section with two typed comments → derived verdict "changes"; a
        # comment-free section → "approved" with no comments noise.
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "comments": [
                {"cid": "s1-c1", "type": "changes", "note": "5x not 3x",
                 "anchor": {"text": "retries 3x", "offset": 9}, "open": True, "settled": False},
                {"cid": "s1-c2", "type": "info", "note": "why stderr?", "open": True, "settled": False}]},
            {"id": "s2", "verdict": "approved"}]})

        out = json.loads((viva / "out1.json").read_text())
        s1 = next(s for s in out["sections"] if s["id"] == "s1")
        s2 = next(s for s in out["sections"] if s["id"] == "s2")
        assert s1["verdict"] == "changes", s1
        assert [c["cid"] for c in s1["comments"]] == ["s1-c1", "s1-c2"], s1
        assert s1["comments"][0]["anchor"] == {"text": "retries 3x", "offset": 9}
        assert s2["verdict"] == "approved", s2
        assert not s2.get("comments"), s2
        print("OK")


def test_comment_images_survive_submit():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _fixtures import PNG_B64  # noqa: E402

    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"; viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "## Goals\nfoo\n"}]}
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:

        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "comments": [
                {"cid": "s1-c1", "type": "changes", "note": "update this",
                 "open": True, "settled": False,
                 "images": [{"data": PNG_B64, "mime": "image/png"}]}
            ]}
        ]})

        out_path = viva / "out1.json"
        poll_for(out_path)

        out = json.loads(out_path.read_text())
        s1 = next(s for s in out["sections"] if s["id"] == "s1")
        cmt = s1["comments"][0]
        assert "images" not in cmt, "images must be stripped by extract_attachments"
        assert "attachments" in cmt, "attachments must be set on comment"
        assert cmt["attachments"][0].endswith(".png"), cmt["attachments"]
        attach_path = Path(cmt["attachments"][0])
        assert attach_path.exists(), f"file not written: {attach_path}"
        assert "s1-c1" in attach_path.name, f"cid missing from filename: {attach_path.name}"
        print("  ok  test_comment_images_survive_submit")


if __name__ == "__main__":
    main()
    test_comment_images_survive_submit()
