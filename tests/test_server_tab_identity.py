#!/usr/bin/env python3
"""Tab self-identification (#172): a browser with several viva tabs open must
be able to tell them apart, and which one wants attention, from the tab strip
alone (title + favicon). Pins the `repo` key on `/input`/`round` SSE, the
favicon route not 404ing, and the `processing` SSE handler retitling the tab.
"""
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, launch_server, post  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server.py"


def test_input_carries_repo():
    tmp = Path(tempfile.mkdtemp())
    repo_dir = tmp / "my-repo-name"
    viva = repo_dir / ".viva"
    viva.mkdir(parents=True)
    r1 = {
        "mode": "review",
        "doc_file": "doc.md",
        "round": 1,
        "approved_ids": [],
        "sections": [{"id": "s1", "title": "Goals", "content": "goals body"}],
    }
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=repo_dir) as base:
        data = get(base, "/input")
        assert data.get("repo") == "my-repo-name", (
            f"expected repo == 'my-repo-name' (the .viva parent dir name), got {data.get('repo')!r}"
        )
    print("  ok  test_input_carries_repo")


def test_next_round_response_is_ok_with_repo_injected_server_side():
    # `repo` is fixed to `_viva_dir` for the server's lifetime, even across a
    # /next-round with a different `--output` directory.
    tmp = Path(tempfile.mkdtemp())
    repo_dir = tmp / "another-repo"
    viva = repo_dir / ".viva"
    viva.mkdir(parents=True)
    r1 = {
        "mode": "review",
        "doc_file": "doc.md",
        "round": 1,
        "approved_ids": [],
        "sections": [{"id": "s1", "title": "Goals", "content": "goals body"}],
    }
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=repo_dir) as base:
        result = post(base, "/next-round", dict(r1, round=2, output=str(viva / "out2.json")))
        assert result == {"ok": True}, f"unexpected /next-round response: {result}"
        assert get(base, "/input").get("repo") == "another-repo"
    print("  ok  test_next_round_response_is_ok_with_repo_injected_server_side")


def test_favicon_route_does_not_404():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {
        "mode": "review",
        "doc_file": "doc.md",
        "round": 1,
        "approved_ids": [],
        "sections": [{"id": "s1", "title": "Goals", "content": "goals body"}],
    }
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        try:
            resp = urllib.request.urlopen(base + "/favicon.ico", timeout=5)
            status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status in (200, 204), f"GET /favicon.ico must not 404, got {status}"
    print("  ok  test_favicon_route_does_not_404")


def test_html_declares_inline_favicon_link():
    text = SERVER.read_text(encoding="utf-8")
    assert "rel=\"icon\"" in text and "data:image/svg+xml" in text, (
        "the HTML <head> must declare an inline data: URI favicon"
    )
    assert 'id="favicon-link"' in text, (
        "the favicon <link> needs a stable id for the client-side swap to target"
    )
    print("  ok  test_html_declares_inline_favicon_link")


def test_processing_handler_retitles_the_tab():
    """Regression: the 'processing' SSE handler never called setTabTitle, so
    the tab kept its stale "your turn" title while the agent was working."""
    text = SERVER.read_text(encoding="utf-8")
    start = text.index("es.addEventListener('processing'")
    end = text.index("es.addEventListener('round'")
    assert start < end, "could not locate the 'processing' handler ahead of the 'round' handler"
    handler = text[start:end]
    assert "setProcessingTabTitle(" in handler, (
        "the 'processing' SSE handler must call setProcessingTabTitle so the tab "
        "retitles the instant the round is submitted, before the agent's response arrives"
    )
    assert "setTabFavicon(" in handler, (
        "the 'processing' SSE handler must also swap the favicon to its "
        "processing/working state"
    )
    print("  ok  test_processing_handler_retitles_the_tab")


def test_round_and_complete_handlers_also_update_favicon():
    text = SERVER.read_text(encoding="utf-8")
    assert "function setTabFavicon(state)" in text, "missing the setTabFavicon helper"
    # All three turn-state transitions must be reachable from the helper.
    for state in ("turn", "processing", "done"):
        assert "'" + state + "'" in text, f"FAVICON_COLOR is missing the {state!r} state"
    print("  ok  test_round_and_complete_handlers_also_update_favicon")


def main() -> None:
    test_input_carries_repo()
    test_next_round_response_is_ok_with_repo_injected_server_side()
    test_favicon_route_does_not_404()
    test_html_declares_inline_favicon_link()
    test_processing_handler_retitles_the_tab()
    test_round_and_complete_handlers_also_update_favicon()
    print("OK")


if __name__ == "__main__":
    main()
