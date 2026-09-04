#!/usr/bin/env python3
"""docket.py reports open viva sessions across repos (issue #173).

Read-only CLI filter: tests build fixture `.viva/` trees under a tempdir and
drive `docket.py`'s functions directly, plus one subprocess check of the CLI.
"""
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "docket.py"
sys.path.insert(0, str(ROOT / "scripts"))
import docket  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────
def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _MockServer:
    """Minimal HTTP server answering GET /input with a fixed JSON payload, to
    exercise docket.py's liveness probe without spinning up server.py. Pass a
    payload with no "round" key to stand in for a live qa server."""

    def __init__(self, payload):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib method name
                if self.path == "/input":
                    body = json.dumps(outer.payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, fmt, *args):  # silence test output
                pass

        self.payload = payload
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.thread.join(timeout=2)


# ── current_round / round_files ─────────────────────────────────────────
def test_current_round_highest_and_zero():
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        viva.mkdir()
        assert docket.current_round(viva) == 0
        _write(viva / "review-input-r1.json", {"sections": []})
        _write(viva / "review-input-r3.json", {"sections": []})
        assert docket.current_round(viva) == 3


def test_round_files_names():
    viva = Path("/nonexistent/.viva")
    inp, out = docket.round_files(viva, 5)
    assert inp.name == "review-input-r5.json"
    assert out.name == "review-r5.json"


# ── find_viva_dirs ───────────────────────────────────────────────────────
def test_find_viva_dirs_root_is_repo():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "myrepo"
        (repo / ".viva").mkdir(parents=True)
        found = docket.find_viva_dirs([str(repo)])
        assert found == [repo / ".viva"]


def test_find_viva_dirs_root_is_directory_of_repos():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "Projects"
        (root / "repo-a" / ".viva").mkdir(parents=True)
        (root / "repo-b" / ".viva").mkdir(parents=True)
        (root / "not-a-repo").mkdir(parents=True)  # no .viva — must be skipped
        found = sorted(docket.find_viva_dirs([str(root) + "/*"]))
        assert found == sorted([root / "repo-a" / ".viva", root / "repo-b" / ".viva"])


def test_find_viva_dirs_dedupes_across_globs():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "myrepo"
        (repo / ".viva").mkdir(parents=True)
        found = docket.find_viva_dirs([str(repo), str(repo)])
        assert found == [repo / ".viva"]


# ── classify: the state machine ───────────────────────────────────────────
def test_your_turn_when_armed_and_unanswered_no_server():
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        _write(viva / "review-input-r1.json",
               {"doc_file": "spec.md", "doc_type": "adr",
                "sections": [{"id": "s1", "title": "A"}]})
        info = docket.classify(viva)
        assert info["state"] == "your-turn"
        assert info["doc_file"] == "spec.md"
        assert info["doc_type"] == "adr"
        assert info["round"] == 1


def test_malformed_non_dict_review_input_does_not_crash():
    # A non-object review-input-rN.json must degrade the row, not raise
    # AttributeError out of classify() and blank the whole report.
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        _write(viva / "review-input-r1.json", [1, 2, 3])
        info = docket.classify(viva)
        assert info["state"] == "your-turn"
        assert info["doc_file"] is None
        assert info["doc_type"] is None
        assert info["round"] == 1


def test_malformed_non_dict_qa_input_does_not_crash():
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        _write(viva / "qa-input.json", ["not", "an", "object"])
        info = docket.classify(viva)
        assert info["state"] == "qa"
        assert info["context"] is None


def test_agent_working_when_verdicts_submitted_and_incomplete():
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        _write(viva / "review-input-r1.json",
               {"doc_file": "spec.md", "sections": [{"id": "s1", "title": "A"}]})
        _write(viva / "review-r1.json",
               {"sections": [{"id": "s1", "verdict": "changes"}]})
        info = docket.classify(viva)
        assert info["state"] == "agent-working"


def test_done_when_verdicts_complete():
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        _write(viva / "review-input-r1.json",
               {"doc_file": "spec.md", "sections": [{"id": "s1", "title": "A"}]})
        _write(viva / "review-r1.json",
               {"sections": [{"id": "s1", "verdict": "approved"}]})
        info = docket.classify(viva)
        assert info["state"] == "done"


def test_qa_when_no_review_round_but_qa_input_exists():
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        _write(viva / "qa-input.json",
               {"mode": "qa", "context": "Notification design", "questions": []})
        info = docket.classify(viva)
        assert info["state"] == "qa"
        assert info["doc_file"] is None
        assert info["context"] == "Notification design"


def test_qa_when_only_answers_present():
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        viva.mkdir()
        _write(viva / "answers.json", {"answers": []})
        info = docket.classify(viva)
        assert info["state"] == "qa"


def test_empty_when_neither_round_nor_qa():
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        viva.mkdir()
        info = docket.classify(viva)
        assert info["state"] == "empty"


def test_dead_when_server_url_names_nothing_listening():
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        _write(viva / "review-input-r2.json",
               {"doc_file": "spec.md", "sections": [{"id": "s1", "title": "A"}]})
        port = _free_port()  # freed immediately; nothing listens there
        (viva / "server.url").write_text(f"http://127.0.0.1:{port}")
        info = docket.classify(viva)
        assert info["state"] == "dead"


def test_your_turn_when_server_agrees_on_round():
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        _write(viva / "review-input-r2.json",
               {"doc_file": "spec.md", "sections": [{"id": "s1", "title": "A"}]})
        mock = _MockServer(payload={"round": 2})
        try:
            (viva / "server.url").write_text(mock.url)
            info = docket.classify(viva)
            assert info["state"] == "your-turn"
        finally:
            mock.stop()


def test_parsed_not_armed_is_not_your_turn():
    """Round 2 is parsed on disk while the live server still answers round 1.
    A naive "input exists, output doesn't" check would call this "your-turn"
    (#173) — it must report "parsed-not-armed" instead."""
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        _write(viva / "review-input-r1.json",
               {"doc_file": "spec.md", "sections": [{"id": "s1", "title": "A"}]})
        _write(viva / "review-r1.json",
               {"sections": [{"id": "s1", "verdict": "approved"}]})
        # Round 2 parsed (e.g. `rearm --parse-only`) but never armed.
        _write(viva / "review-input-r2.json",
               {"doc_file": "spec.md", "sections": [{"id": "s1", "title": "A"}]})
        mock = _MockServer(payload={"round": 1})  # still serving round 1
        try:
            (viva / "server.url").write_text(mock.url)
            info = docket.classify(viva)
            assert info["state"] == "parsed-not-armed", info
            assert info["round"] == 2
        finally:
            mock.stop()


def test_parsed_not_armed_during_viva_write_handoff_window():
    """During the `/viva-write` hand-off, the qa server has already written
    `server.url` before round 1 is parsed, so `/input` answers with a qa
    payload carrying no "round" key. Must classify as "parsed-not-armed",
    never "dead" or "your-turn"."""
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        _write(viva / "review-input-r1.json",
               {"doc_file": "spec.md", "sections": [{"id": "s1", "title": "A"}]})
        mock = _MockServer(payload={"mode": "qa", "questions": []})
        try:
            (viva / "server.url").write_text(mock.url)
            info = docket.classify(viva)
            assert info["state"] == "parsed-not-armed", info
        finally:
            mock.stop()


# ── age formatting ─────────────────────────────────────────────────────────
def test_format_age_buckets():
    now = 1_000_000.0
    assert docket.format_age(None, now) == "unknown"
    assert docket.format_age(now - 10, now) == "just now"
    assert docket.format_age(now - 120, now) == "2m ago"
    assert docket.format_age(now - 3 * 3600, now) == "3h ago"
    assert docket.format_age(now - 2 * 86400, now) == "2d ago"
    assert docket.format_age(now - 400 * 86400, now) == "1y ago"


# ── roots ────────────────────────────────────────────────────────────────
def test_resolve_roots_precedence():
    old = os.environ.pop("VIVA_DOCKET_ROOTS", None)
    try:
        assert docket.resolve_roots([]) == ["~/Projects/*"]
        os.environ["VIVA_DOCKET_ROOTS"] = "/a:/b"
        assert docket.resolve_roots([]) == ["/a", "/b"]
        assert docket.resolve_roots(["/explicit"]) == ["/explicit"]
    finally:
        if old is None:
            os.environ.pop("VIVA_DOCKET_ROOTS", None)
        else:
            os.environ["VIVA_DOCKET_ROOTS"] = old


# ── CLI entrypoint ─────────────────────────────────────────────────────────
def test_cli_json_output():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "Projects"
        repo = root / "myrepo"
        _write(repo / ".viva" / "review-input-r1.json",
               {"doc_file": "spec.md", "doc_type": "adr",
                "sections": [{"id": "s1", "title": "A"}]})
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root) + "/*",
             "--format", "json"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        rows = json.loads(proc.stdout)
        assert len(rows) == 1
        assert rows[0]["repo"] == "myrepo"
        assert rows[0]["state"] == "your-turn"
        assert rows[0]["doc_file"] == "spec.md"
        assert rows[0]["doc_type"] == "adr"
        assert isinstance(rows[0]["mtime"], (int, float))


def test_cli_text_output_no_crash_and_reports_none_found():
    with tempfile.TemporaryDirectory() as tmp:
        empty_root = Path(tmp) / "Nothing"
        empty_root.mkdir()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(empty_root) + "/*",
             "--format", "text"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "no .viva/ sessions found" in proc.stdout


def test_cli_survives_one_malformed_repo_among_many():
    # A malformed review-input-rN.json in one scanned repo must not blank
    # the report for every other repo too.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "Projects"
        _write(root / "bad" / ".viva" / "review-input-r1.json", [1, 2, 3])
        _write(root / "good" / ".viva" / "review-input-r1.json",
               {"doc_file": "spec.md", "sections": [{"id": "s1", "title": "A"}]})
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root) + "/*"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "good" in proc.stdout
        assert "spec.md" in proc.stdout
        assert "bad" in proc.stdout


def test_cli_text_output_renders_table():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "Projects"
        repo = root / "myrepo"
        _write(repo / ".viva" / "review-input-r1.json",
               {"doc_file": "spec.md", "sections": [{"id": "s1", "title": "A"}]})
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root) + "/*"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "myrepo" in proc.stdout
        assert "your-turn" in proc.stdout
        assert "spec.md" in proc.stdout


def main():
    test_current_round_highest_and_zero()
    test_round_files_names()
    test_find_viva_dirs_root_is_repo()
    test_find_viva_dirs_root_is_directory_of_repos()
    test_find_viva_dirs_dedupes_across_globs()
    test_your_turn_when_armed_and_unanswered_no_server()
    test_malformed_non_dict_review_input_does_not_crash()
    test_malformed_non_dict_qa_input_does_not_crash()
    test_agent_working_when_verdicts_submitted_and_incomplete()
    test_done_when_verdicts_complete()
    test_qa_when_no_review_round_but_qa_input_exists()
    test_qa_when_only_answers_present()
    test_empty_when_neither_round_nor_qa()
    test_dead_when_server_url_names_nothing_listening()
    test_your_turn_when_server_agrees_on_round()
    test_parsed_not_armed_is_not_your_turn()
    test_parsed_not_armed_during_viva_write_handoff_window()
    test_format_age_buckets()
    test_resolve_roots_precedence()
    test_cli_json_output()
    test_cli_text_output_no_crash_and_reports_none_found()
    test_cli_survives_one_malformed_repo_among_many()
    test_cli_text_output_renders_table()
    print("OK (23 tests)")


if __name__ == "__main__":
    main()
