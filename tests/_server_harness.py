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


def assert_catalog_ground(text: str) -> None:
    """Shared catalog-ground needle checks — the single owner of the ground
    contract, so a chrome change edits one place (was duplicated verbatim
    across test_server_a11y and this suite).

    Replaces `assert_sheet_ground`: the drafting sheet on a flat table gave way
    to the catalog page, so the needles moved from the sheet's edge and corner
    marks to the party inks and the reading measure. CSS-rule checks are
    whitespace-tolerant regexes — the values are the design contract, the
    source alignment is not. Structural markup and aria literals stay exact.
    `text` is the served page or the HTML constant (byte-identical: the server
    serves HTML.encode())."""
    # The four party inks, each defined once per theme block. Three blocks
    # carry the palette: light `:root`, the `prefers-color-scheme: dark`
    # override, and the explicit `[data-theme="dark"]` the toggle sets. The two
    # dark blocks hold the same values by construction — `test_theme_toggle`
    # owns that invariant and fails on any drift; here we only pin the count,
    # so a fourth definition appearing anywhere is caught as a new source of
    # truth rather than a duplicate.
    for token, light in (('--paper', '#ffffff'), ('--touch', '#ffec8f'),
                         ('--acc', '#2946c4'), ('--machine', '#0c7f6b'),
                         ('--fact', '#a06a12')):
        assert re.search(re.escape(token) + r':\s+' + re.escape(light) + ';', text), \
            f"light token block missing {token}: {light}"
        assert text.count(token + ':') == 3, \
            f"{token} must be defined once per theme block (light, media dark, explicit dark)"
    assert 'background: var(--paper);' in text, "body must sit on the catalog page"
    assert re.search(r'@media \(prefers-color-scheme: dark\)', text), \
        "dark is the override; light is the primary theme"
    assert '@media (prefers-color-scheme: light)' not in text, \
        "light must be :root, not a media override — the ground inverted"

    # #paper survives as the content wrapper; its sheet dress does not.
    assert '<div id="paper">' in text, "missing the #paper wrapper"
    assert re.search(r'#paper\s*\{[^}]*max-width:\s*1240px', text), \
        "#paper missing its 1240px bound"
    assert '#paper::before' not in text, "the sheet's 7px inner rule must be gone"
    assert 'paper-marks' not in text, "sheet decoration markup must be gone"
    assert 'pcoord' not in text, "edge coordinates must be gone"
    assert text.index('<div id="paper">') < text.index('<main class="shell"'), \
        "#paper must open before main.shell"
    assert text.index('</main>') < text.index('</div><!-- /#paper -->'), \
        "#paper must close after main"
    assert re.search(r'\.mode-diff #paper\s*\{\s*max-width:\s*min\(95vw, 1600px\)', text), \
        "missing the diff-mode #paper widening rule"

    # The reading measure, and the nested scroll that is not coming back.
    assert re.search(r'\.section-content\s*\{[^}]*max-width:\s*72ch', text), \
        ".section-content must cap the prose at a 72ch measure"
    assert not re.search(r'\.section-content\s*\{[^}]*max-height:\s*60vh', text), \
        ".section-content must not nest a second scroll context inside the card"

    # ── The doc + margin grid (#186) — the ground's structure, extended into
    # this same owner rather than forked into a second contract.
    assert re.search(
        r'\.doc \.row\s*\{[^}]*grid-template-columns:\s*var\(--gutter-w\)'
        r'\s+minmax\(0,\s*72ch\)\s+var\(--margin-w\)', text), \
        "the doc row must be `check gutter | 72ch prose | margin`"
    assert re.search(r'\.doc \.row\.wide\s*\{[^}]*minmax\(0,\s*1fr\)', text), \
        "a wide row must let code and tables break out of the prose measure"
    # The wasted-space rule: both side columns collapse to zero width.
    assert re.search(r'\.doc\.no-gutter\s*\{\s*--gutter-w:\s*0px;\s*\}', text), \
        "the gutter column must collapse to 0 when nothing uses it"
    assert re.search(r'\.doc\.no-margin\s*\{\s*--margin-w:\s*0px;\s*\}', text), \
        "the margin column must collapse to 0 when nothing uses it"
    # The 28px alley rides in the side cells, never in column-gap — a gap is
    # drawn between zero-width tracks too, which would defeat the collapse.
    assert not re.search(r'\.doc \.row\s*\{[^}]*column-gap', text), \
        "the row must not use column-gap — a collapsed column would still cost its alley"
    for cell, edge in (('rg', 'padding-right'), ('rm', 'padding-left')):
        assert re.search(r'\.doc \.' + cell + r'\s*\{[^}]*' + edge + r':\s*28px', text), \
            f".{cell} must carry the 28px alley itself"
    # The segmented rule's fixed order is the colorblind-safe second encoding,
    # so the three segment inks must each exist and stay distinct.
    for cls, token in (('seg-judgment', '--acc'), ('seg-fact', '--fact'),
                       ('seg-settled', '--settled')):
        assert re.search(r'\.' + cls + r'\s*\{\s*background:\s*var\(' + token + r'\)', text), \
            f".{cls} must be inked from var({token})"


def assert_ink_discipline(text: str) -> None:
    """The syntax theme may not spend the reviewer's ink.

    Catalog yellow means "the reviewer touched this text" and red/green belong
    to the suggestion fence, where diff semantics already own them. A stock
    highlight.js theme would violate both on its first line, so this guards the
    boundary rather than the palette's taste."""
    block = text[text.index('/* ─── Syntax highlighting'):text.index('</style>')]
    assert '--touch' not in block, \
        "syntax highlighting must not use catalog yellow — that is the reviewer's touch"
    for rule in ('.hljs-comment', '.hljs-keyword', '.hljs-string'):
        assert rule in block, f"syntax theme missing {rule}"
    # Red and green appear only on the diff-line classes, never on a token class.
    for line in block.splitlines():
        if 'rgba(26,127,55' in line or 'rgba(209,36,47' in line:
            assert 'addition' in line or 'deletion' in line, \
                f"diff red/green leaked onto a syntax token: {line.strip()}"


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


def post_result(base: str, path: str, payload: dict) -> tuple:
    """POST a JSON payload and return `(status, decoded_body)`.

    For boundary tests that must tell two refusals apart by their error text and
    not only by their status code — `HTTPError` carries the server's body on
    `.read()`, so a 4xx yields its `{"error": ...}` exactly as a 200 yields its
    `{"ok": true}`."""
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


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
