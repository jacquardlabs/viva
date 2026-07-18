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
import http.client
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server.py"


def assert_sheet_ground(text: str) -> None:
    """Shared sheet-ground needle checks — the single owner of the bounded
    #paper-sheet-on-flat-table contract, so a chrome change edits one place
    (was duplicated verbatim across test_server_a11y and this suite). CSS-rule
    checks are whitespace-tolerant regexes: the values are the design contract,
    the source alignment is not — reformatting the token block or the rule must
    not break the test. Structural markup and aria literals stay exact, because
    there the literal *is* the contract. `text` is the served page or the HTML
    constant (byte-identical: the server serves HTML.encode())."""
    assert re.search(r'--table:\s+#060e1a;', text), "dark token block missing --table"
    assert re.search(r'--table:\s+#e2e8f1;', text), "light token block missing --table"
    assert text.count('--table:') == 2, "--table must be defined once per theme block"
    assert 'background: var(--table);' in text, "body must sit on the flat table"
    assert '<div id="paper">' in text, "missing the #paper sheet"
    assert re.search(r'#paper\s*\{[^}]*max-width:\s*700px[^}]*'
                     r'border:\s*1px solid var\(--border2\)', text), \
        "#paper missing its content-bounded 700px edge"
    assert re.search(r'#paper::before\s*\{[^}]*inset:\s*7px[^}]*'
                     r'border:\s*1px solid var\(--border\)', text), \
        "#paper missing the 1px inner rule at 7px inset"
    assert '<div class="paper-marks" aria-hidden="true">' in text, \
        "sheet decoration must be aria-hidden"
    assert text.count('class="pmark') == 4, "expected 4 corner registration marks"
    assert '<span class="pcoord pc-n" style="left:12.5%">1</span>' in text, \
        "missing edge coordinate numbers"
    assert '<span class="pcoord pc-w" style="top:12.5%">A</span>' in text, \
        "missing edge coordinate letters"
    assert text.index('<div id="paper">') < text.index('<main class="shell"'), \
        "#paper must open before main.shell"
    assert text.index('</main>') < text.index('</div><!-- /#paper -->'), \
        "#paper must close after main"
    assert re.search(r'\.mode-diff #paper\s*\{\s*max-width:\s*min\(95vw, 1600px\)', text), \
        "missing the diff-mode #paper widening rule"


def assert_grid_gone(text: str) -> None:
    """Shared negative check — the 24px drafting grid and the fixed
    .sheet-frame (CSS, markup, and .sf-mark corners) are gone at every layer."""
    assert 'background-size: 24px 24px' not in text, "24px grid still present"
    assert 'sheet-frame' not in text, ".sheet-frame still present"
    assert 'sf-mark' not in text, "legacy .sf-mark corner marks still present"


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


def post_headers(base: str, path: str, payload: dict, headers: dict) -> int:
    """POST a JSON payload with extra request headers (e.g. `Origin`) merged
    in atop `Content-Type`; return the HTTP status code. For boundary tests
    exercising the loopback-Origin guard shared by /submit, /next-round, and
    /complete."""
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        return urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def post_oversized(base: str, path: str, claimed_length: int) -> int:
    """POST with a `Content-Length` header that *claims* `claimed_length`
    bytes but actually sends only a couple — exercises the body-size cap
    without transferring hundreds of MiB over the wire. The server's guard
    reads and checks `Content-Length` before ever calling `self.rfile.read`,
    so it responds (and the connection closes, since the server does not run
    HTTP/1.1 keep-alive) before the declared/actual mismatch matters."""
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        conn.request("POST", path, body=b"{}",
                     headers={"Content-Type": "application/json",
                              "Content-Length": str(claimed_length)})
        resp = conn.getresponse()
        status = resp.status
        resp.read()
        return status
    finally:
        conn.close()


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
