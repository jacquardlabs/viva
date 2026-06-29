#!/usr/bin/env python3
"""Unit tests for server.extract_attachments — the image boundary."""
import base64
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from _fixtures import PNG, PNG_B64  # noqa: E402


def _img(b64=PNG_B64, mime="image/png"):
    return {"data": b64, "mime": mime}


def run_in_tmp(fn):
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".viva").mkdir()
        fn(Path(d))


def test_valid_png_written_and_swapped():
    def body(d):
        out = str(d / ".viva" / "review-r1.json")
        data = {"sections": [{"id": "s1", "verdict": "changes", "note": "see this",
                              "images": [_img()]}]}
        result = server.extract_attachments(data, out, 1)
        sec = result["sections"][0]
        assert "images" not in sec, "images must be stripped"
        assert sec["attachments"] == [str(d / ".viva" / "attachments" / "r1-s1-0.png")], sec
        assert Path(sec["attachments"][0]).read_bytes() == PNG, "bytes must match"
        assert sec["note"] == "see this"
    run_in_tmp(body)
    print("  ok  test_valid_png_written_and_swapped")


def test_disallowed_mime_dropped():
    def body(d):
        out = str(d / ".viva" / "review-r1.json")
        data = {"sections": [{"id": "s1", "verdict": "changes",
                              "images": [_img(mime="image/svg+xml")]}]}
        result = server.extract_attachments(data, out, 1)
        sec = result["sections"][0]
        assert "images" not in sec and "attachments" not in sec, sec
        assert not (d / ".viva" / "attachments").exists() or \
            not list((d / ".viva" / "attachments").iterdir()), "nothing written"
    run_in_tmp(body)
    print("  ok  test_disallowed_mime_dropped")


def test_oversized_dropped():
    def body(d):
        out = str(d / ".viva" / "review-r1.json")
        big = base64.b64encode(b"\x00" * (server.MAX_IMAGE_BYTES + 1)).decode()
        data = {"sections": [{"id": "s1", "images": [{"data": big, "mime": "image/png"}]}]}
        result = server.extract_attachments(data, out, 1)
        assert "attachments" not in result["sections"][0]
    run_in_tmp(body)
    print("  ok  test_oversized_dropped")


def test_invalid_base64_dropped():
    def body(d):
        out = str(d / ".viva" / "review-r1.json")
        data = {"sections": [{"id": "s1", "images": [{"data": "!!!not base64!!!",
                                                       "mime": "image/png"}]}]}
        result = server.extract_attachments(data, out, 1)
        assert "attachments" not in result["sections"][0]
    run_in_tmp(body)
    print("  ok  test_invalid_base64_dropped")


def test_malicious_id_sanitized_no_traversal():
    def body(d):
        out = str(d / ".viva" / "review-r1.json")
        data = {"sections": [{"id": "../../etc/evil", "images": [_img()]}]}
        result = server.extract_attachments(data, out, 1)
        path = Path(result["sections"][0]["attachments"][0])
        assert path.parent == d / ".viva" / "attachments", f"escaped: {path}"
        assert ".." not in path.name, path.name
    run_in_tmp(body)
    print("  ok  test_malicious_id_sanitized_no_traversal")


def test_qa_answers_supported():
    def body(d):
        out = str(d / ".viva" / "answers.json")
        data = {"answers": [{"id": "q1", "choice": "A", "images": [_img()]}], "submitted_early": False}
        result = server.extract_attachments(data, out, 0)
        ans = result["answers"][0]
        assert ans["attachments"] == [str(d / ".viva" / "attachments" / "qa-q1-0.png")], ans
    run_in_tmp(body)
    print("  ok  test_qa_answers_supported")


def test_no_images_unchanged():
    def body(d):
        out = str(d / ".viva" / "review-r1.json")
        data = {"sections": [{"id": "s1", "verdict": "approved", "note": ""}]}
        result = server.extract_attachments(data, out, 1)
        assert result == {"sections": [{"id": "s1", "verdict": "approved", "note": ""}]}
    run_in_tmp(body)
    print("  ok  test_no_images_unchanged")


def test_comment_images_extracted():
    def body(d):
        out = str(d / ".viva" / "review-r1.json")
        data = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
            {"cid": "s1-c1", "type": "changes", "note": "fix this",
             "images": [_img()]}
        ]}]}
        result = server.extract_attachments(data, out, 1)
        sec = result["sections"][0]
        assert "images" not in sec, "section must not have images key"
        assert "attachments" not in sec, "section must not have attachments (only comment does)"
        cmt = sec["comments"][0]
        assert "images" not in cmt, "images must be stripped from comment"
        expected = str(d / ".viva" / "attachments" / "r1-s1-c1-0.png")
        assert cmt["attachments"] == [expected], cmt
        assert Path(cmt["attachments"][0]).read_bytes() == PNG
        assert cmt["note"] == "fix this"
    run_in_tmp(body)
    print("  ok  test_comment_images_extracted")


def test_comment_image_bad_mime_dropped():
    def body(d):
        out = str(d / ".viva" / "review-r1.json")
        data = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
            {"cid": "s1-c1", "type": "changes", "note": "note preserved",
             "images": [_img(mime="image/svg+xml")]}
        ]}]}
        result = server.extract_attachments(data, out, 1)
        cmt = result["sections"][0]["comments"][0]
        assert "images" not in cmt, "images key must always be stripped"
        assert "attachments" not in cmt, "bad mime must not produce attachments"
        assert cmt["note"] == "note preserved"
    run_in_tmp(body)
    print("  ok  test_comment_image_bad_mime_dropped")


def test_html_has_capture_wiring():
    html = server.HTML
    for needle in ("function attachImageFiles", "function renderThumbs",
                   "addEventListener('paste'", "thumb-strip", "attach-btn",
                   "images: ", "captureState"):
        assert needle in html, f"HTML missing: {needle}"
    print("  ok  test_html_has_capture_wiring")


def main():
    test_valid_png_written_and_swapped()
    test_disallowed_mime_dropped()
    test_oversized_dropped()
    test_invalid_base64_dropped()
    test_malicious_id_sanitized_no_traversal()
    test_qa_answers_supported()
    test_no_images_unchanged()
    test_comment_images_extracted()
    test_comment_image_bad_mime_dropped()
    test_html_has_capture_wiring()
    print("OK (10 tests)")


if __name__ == "__main__":
    main()
