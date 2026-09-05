#!/usr/bin/env python3
"""Integration test: `GET /evidence` (#106) — serves the lines a confidence
annotation's `source` cites.

Contract: the served set is an ALLOWLIST derived from the live round, not a
path join on request data. A `ref` the round is not currently citing 404s
before any path is resolved; one it IS citing is still confined under the
repo root, refused if any path segment is in `schema.SKIP_DIRS`, and capped
in bytes and line count. Every failure 404s identically.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, get_headers, launch_server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import schema  # noqa: E402


def _round(sections):
    return {"mode": "review", "doc_file": "doc.md", "round": 1,
            "approved_ids": [], "sections": sections}


def _conf(source, sid="s1"):
    return {"id": sid, "kind": "confidence", "severity": "warn",
            "basis": "inferred", "level": "low", "message": "inferred · low",
            "source": source}


def main():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()

    # A real file to cite, several lines long.
    (tmp / "mod.py").write_text("\n".join(f"line {i}" for i in range(1, 11)) + "\n")
    # A file outside the repo root, reachable only via a symlink or `../`.
    outside_dir = Path(tempfile.mkdtemp())
    (outside_dir / "secret.txt").write_text("outside the root\n")
    (tmp / "link.txt").symlink_to(outside_dir / "secret.txt")
    # A file under a denylisted directory.
    (viva / "internal.json").write_text("{}\n")
    # An oversized file — over MAX_EVIDENCE_BYTES.
    (tmp / "big.txt").write_text("x" * (1024 * 1024 + 1))

    r1 = _round([
        {"id": "s1", "title": "Goals", "content": "g",
         "annotations": [_conf("mod.py:2-3 — see the loop bound", "s1")]},
        {"id": "s2", "title": "Escape", "content": "e",
         "annotations": [_conf("../secret.txt:1", "s2")]},
        {"id": "s3", "title": "Symlink", "content": "s",
         "annotations": [_conf("link.txt:1", "s3")]},
        {"id": "s4", "title": "Denylisted", "content": "d",
         "annotations": [_conf(".viva/internal.json:1", "s4")]},
        {"id": "s5", "title": "Oversized", "content": "o",
         "annotations": [_conf("big.txt:1", "s5")]},
        {"id": "s6", "title": "Whole file", "content": "w",
         "annotations": [_conf("mod.py", "s6")]},
        {"id": "s7", "title": "Single line", "content": "l",
         "annotations": [_conf("mod.py:5", "s7")]},
    ])
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:

        # A listed ref serves the exact cited lines.
        data = get(base, "/evidence?ref=mod.py:2-3")
        assert data == {"path": "mod.py", "start": 2, "end": 3,
                        "lines": ["line 2", "line 3"]}, data

        # An unlisted path — a real file the round isn't citing — 404s.
        status, _ = get_headers(base, "/evidence?ref=nowhere.py:1", {})
        assert status == 404, status

        # A `../` escape, even though the round cites it, is refused: root
        # confinement catches what the allowlist alone would not.
        status, _ = get_headers(base, "/evidence?ref=../secret.txt:1", {})
        assert status == 404, status

        # A symlink resolving outside the root is refused the same way.
        status, _ = get_headers(base, "/evidence?ref=link.txt:1", {})
        assert status == 404, status

        # A denylisted directory (`.viva`, same table `drift.py` honors) is
        # refused even though it resolves inside the root.
        status, _ = get_headers(base, "/evidence?ref=.viva/internal.json:1", {})
        assert status == 404, status
        assert schema.SKIP_DIRS and ".viva" in schema.SKIP_DIRS

        # A cited file over the byte cap is refused.
        status, _ = get_headers(base, "/evidence?ref=big.txt:1", {})
        assert status == 404, status

        # A whole-file ref (no line range) reads the whole thing.
        data = get(base, "/evidence?ref=mod.py")
        assert data["start"] == 1 and data["end"] == 10, data
        assert len(data["lines"]) == 10, data

        # A single-line ref (`path:N`) reads exactly that line.
        data = get(base, "/evidence?ref=mod.py:5")
        assert data == {"path": "mod.py", "start": 5, "end": 5,
                        "lines": ["line 5"]}, data

        # No `ref` at all, or an empty one, is refused the same way.
        status, _ = get_headers(base, "/evidence", {})
        assert status == 404, status

        # The loopback-Host guard on every GET applies here too.
        status, _ = get_headers(base, "/evidence?ref=mod.py:2-3",
                                 {"Host": "evil.example"})
        assert status == 403, status

        print("OK")


if __name__ == "__main__":
    main()
