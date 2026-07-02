"""Shared harness for the server integration tests — launch, poll, HTTP.

Every `test_server_*.py` that drives a live server duplicated the same ~40 lines:
`post`/`get` helpers, a `subprocess.Popen` launch, a `server.url` poll loop, and
a `try/finally` teardown. That lives here now.

Named with a leading underscore on purpose: the CI runner is
`for f in tests/test_*.py; do python3 "$f"; done`, so this module is imported by
the tests, never executed as one. This is NOT a pytest `conftest.py` — the
project has no pytest dependency and runs each test file as a plain script.

Usage:

    from _server_harness import launch_server, get, post

    with launch_server(viva / "in.json", viva / "out.json", cwd=tmp) as base:
        assert get(base, "/input")["round"] == 1
        post(base, "/submit", {...})
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server.py"


def get(base: str, path: str) -> dict:
    """GET a JSON endpoint and decode the body."""
    return json.loads(urllib.request.urlopen(base + path, timeout=5).read())


def get_text(base: str, path: str = "/") -> str:
    """GET an endpoint and return the raw text body (e.g. the served HTML page)."""
    return urllib.request.urlopen(base + path, timeout=5).read().decode()


def post(base: str, path: str, payload: dict) -> dict:
    """POST a JSON payload and decode the JSON response body."""
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def post_status(base: str, path: str, payload: dict) -> int:
    """POST a JSON payload and return the HTTP status code (for boundary tests
    that expect a 4xx rather than a JSON body)."""
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        return urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def wait_for_url(output_path, tries: int = 50, delay: float = 0.2) -> str:
    """Poll for the `server.url` the server writes beside its output file, and
    return the base URL. Raises if it never appears."""
    url_file = Path(output_path).parent / "server.url"
    for _ in range(tries):
        if url_file.exists():
            return url_file.read_text().strip()
        time.sleep(delay)
    raise AssertionError("server.url never appeared")


def poll_for(path, tries: int = 50, delay: float = 0.2) -> bool:
    """Wait for a file (e.g. the output JSON) to appear; return whether it did."""
    path = Path(path)
    for _ in range(tries):
        if path.exists():
            return True
        time.sleep(delay)
    return False


@contextmanager
def launch_server(input_path, output_path, mode: str = "review", cwd=None):
    """Launch `server.py` on the given input/output, yield its base URL, and
    always terminate it on exit."""
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--mode", mode,
         "--input", str(input_path), "--output", str(output_path), "--no-browser"],
        cwd=str(cwd) if cwd else None,
    )
    try:
        yield wait_for_url(output_path)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
