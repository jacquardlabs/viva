#!/usr/bin/env python3
"""The SPA's third-party JS, CSS and fonts are vendored and served from disk.

Before this, five `<script>` tags and one injected `<link>` pointed at
cdn.jsdelivr.net at `@major` ranges. A review therefore needed the network, the
resolved bytes were whatever the CDN served that day, and #43 exists only
because DOMPurify can fail to load independently of marked. The assets now live
under `assets/vendor/` at exact versions and are served by this server.

Four invariants, in the order they can break:

  1. Nothing in the served page points at any remote host. Checked against
     `server.HTML` as well as a live page — `_HTML_BYTES` is one constant
     served identically to review, diff, and QA, so a static assertion on the
     constant is the mode-independent statement of "no mode fetches a CDN".
  2. Every `/vendor/...` URL in the page has a route, and every route has a
     file on disk. A page/table mismatch is the failure a version bump makes,
     and it is silent — the browser 404s and the reader gets the fallback.
  3. Every route serves those exact bytes with the right Content-Type, from a
     server whose cwd is somewhere else entirely. viva runs from the repo under
     review, so a cwd-relative resolution would work in this repo and nowhere
     else; the harness launches with `cwd=tmp` precisely to catch it.
  4. Nothing else under `/vendor/` is reachable. The route table is an
     exact-match dict, so there is no path to traverse today; the test is what
     keeps a future refactor from reintroducing a join.

Not checked here: that the browser makes zero external requests. There is no
browser harness in this repo (stdlib only, no npm/node), so "no remote host" is
proven by the absence of the host in what is served.

#79 scoped Google Fonts OUT — the two faces stayed remote while the JS and CSS
came in-tree, so a review still made a per-session request to Google and, run
offline, silently lost its typography. That scope decision is reversed: Fragment
Mono is vendored as four woff2 subsets and declared in the page's own
stylesheet, and the assertion below that once PROTECTED the font preconnect now
forbids the host outright. Bricolage Grotesque is not vendored — it was an
undocumented third family on three rules (DESIGN.md: "Two families only"), and
those rules now inherit body's grotesque stack instead.
"""
import http.client
import json
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from _server_harness import get_text, launch_server  # noqa: E402

REVIEW_INPUT = {
    "mode": "review",
    "doc_file": "SPEC.md",
    "round": 1,
    "approved_ids": [],
    "sections": [{"id": "s1", "title": "Overview", "content": "Body."}],
}


def _fetch(base: str, path: str):
    """GET and return `(status, headers, body_bytes)` — the vendor routes are
    checked on their headers and raw bytes, which the JSON/text harness
    helpers do not surface."""
    parsed = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def test_no_cdn_reference_survives_in_any_mode() -> None:
    """The one acceptance criterion, stated where it holds for every mode:
    `_HTML_BYTES` is `HTML` encoded once at import and served to review, diff,
    and QA alike, so the constant carrying no jsdelivr reference IS the
    mode-independent guarantee."""
    assert "jsdelivr" not in server.HTML, \
        "the served page still references jsdelivr"
    assert "cdn." not in server.HTML, \
        "the served page still references a CDN host"
    assert 'rel="preconnect" href="https://cdn' not in server.HTML, \
        "the jsdelivr preconnect must be gone"
    # INVERTED. This assertion used to read `"fonts.gstatic.com" in HTML` and
    # protected the font preconnect, because #79 left the faces remote. The
    # faces are vendored now, so the same line states the widened invariant:
    # the page reaches no font host either, and a reinstated <link> — the
    # obvious way to "fix" a missing glyph — fails here rather than shipping a
    # per-session request to Google.
    assert "fonts.googleapis.com" not in server.HTML, \
        "the fonts are vendored — nothing in the page may reach Google"
    assert "fonts.gstatic.com" not in server.HTML, \
        "the fonts are vendored — nothing in the page may reach Google"
    assert "preconnect" not in server.HTML, \
        "a preconnect to anywhere means the page still has a remote host"
    print("  ok  test_no_cdn_reference_survives_in_any_mode")


def test_every_vendor_url_in_the_page_has_a_route() -> None:
    """A bump edits three places (the file, the route table, the page URL).
    Miss the third and the browser 404s into the md-raw fallback with no error
    anywhere — so the page's URLs and the route table are compared directly."""
    urls = set(re.findall(r'(?:src|href)=[\'"](/vendor/[^\'"]+)[\'"]', server.HTML))
    # The mode-diff stylesheet is assigned in JS, not written as an attribute.
    urls |= set(re.findall(r"= '(/vendor/[^']+)'", server.HTML))
    # The four faces are declared in `@font-face`, not as an element attribute —
    # a third spelling, invisible to both patterns above. Without this harvest
    # the font routes read as "routes nothing in the page loads", which is the
    # right complaint about the wrong thing.
    urls |= set(re.findall(r"url\(['\"]?(/vendor/[^'\")]+)", server.HTML))
    assert len(urls) == 10, f"expected 10 vendor URLs in the page, found {sorted(urls)}"
    unrouted = urls - set(server._VENDOR_ROUTES)
    assert not unrouted, f"page references vendor URLs with no route: {sorted(unrouted)}"
    unused = set(server._VENDOR_ROUTES) - urls
    assert not unused, f"routes nothing in the page loads: {sorted(unused)}"
    print("  ok  test_every_vendor_url_in_the_page_has_a_route")


def test_every_route_has_a_file_and_a_license() -> None:
    """The assets ship in the plugin package, and vendored third-party code
    carries its attribution — `assets/vendor/README.md` names each pin and
    points at the license text beside it."""
    for name, _ctype in server._VENDOR_ASSETS:
        path = server._VENDOR_DIR / name
        assert path.is_file(), f"vendored asset missing from the tree: {path}"
        assert path.stat().st_size > 1024, f"vendored asset looks truncated: {path}"
    readme = (server._VENDOR_DIR / "README.md").read_text()
    for name, _ctype in server._VENDOR_ASSETS:
        assert name in readme, f"assets/vendor/README.md does not record {name}"
    # Per PACKAGE, not per asset: the three diff2html bundles share one file,
    # and so do the four Fragment Mono subsets.
    licenses = list(server._VENDOR_DIR.glob("LICENSE-*"))
    assert len(licenses) == 5, \
        f"expected a license file per package (5), found {[p.name for p in licenses]}"
    print("  ok  test_every_route_has_a_file_and_a_license")


def test_no_hljs_stylesheet_is_vendored() -> None:
    """Only highlight.js's engine is vendored. viva hand-writes its own `.hljs`
    theme; a stock one would spend catalog yellow on syntax, which belongs to
    the reviewer's touch (see `assert_ink_discipline`)."""
    css = [n for n, _ in server._VENDOR_ASSETS if n.endswith(".css")]
    assert css == ["diff2html-3.4.56.min.css"], \
        f"the only vendored stylesheet is diff2html's, got {css}"
    print("  ok  test_no_hljs_stylesheet_is_vendored")


def test_routes_serve_the_files_from_a_foreign_cwd(base: str) -> None:
    """The server is launched from the repo under review, never from its own
    directory — `_VENDOR_DIR` must resolve off `__file__`. The harness runs it
    with `cwd` set to a temp dir, so a cwd-relative resolution 404s here."""
    for name, ctype in server._VENDOR_ASSETS:
        status, headers, body = _fetch(base, "/vendor/" + name)
        assert status == 200, f"/vendor/{name} returned {status}"
        assert body == (server._VENDOR_DIR / name).read_bytes(), \
            f"/vendor/{name} did not serve the on-disk bytes"
        assert headers.get("Content-Type") == ctype, \
            f"/vendor/{name} served as {headers.get('Content-Type')!r}, expected {ctype!r}"
        # Safe only because the version is in the path: an upgrade moves the
        # URL rather than changing the bytes behind it.
        assert "immutable" in headers.get("Cache-Control", ""), \
            f"/vendor/{name} missing its immutable cache header"
    print("  ok  test_routes_serve_the_files_from_a_foreign_cwd")


def test_vendor_routes_are_exact_match_only(base: str) -> None:
    """The route table is a dict keyed on the whole path and the filename is
    always a literal from it, so there is nothing to traverse. This pins that:
    a future refactor to `_VENDOR_DIR / <request tail>` would turn every line
    below into a served file."""
    for path in (
        "/vendor/../server.py",
        "/vendor/%2e%2e/server.py",
        "/vendor/..%2fserver.py",
        "/vendor/../../etc/passwd",
        "/vendor/marked-12.0.2.min.js/../../server.py",
        "/vendor/",
        "/vendor",
        "/vendor/nope.js",
        # The bare (unversioned) name a copy-paste from the old CDN URL yields.
        "/vendor/marked.min.js",
        # Same trap for a face: the version is in the filename, so an
        # unstamped guess must 404 rather than serve the current bytes.
        "/vendor/fragment-mono.woff2",
    ):
        status, _headers, _body = _fetch(base, path)
        assert status == 404, f"{path} must 404, got {status}"
    print("  ok  test_vendor_routes_are_exact_match_only")


def test_live_page_matches_the_constant(base: str) -> None:
    """Belt and braces on invariant 1: the bytes actually on the wire, from a
    booted server, carry no CDN host either."""
    page = get_text(base, "/")
    assert "jsdelivr" not in page, "the live page still references jsdelivr"
    assert "/vendor/marked-12.0.2.min.js" in page, \
        "the live page does not load the vendored marked"
    print("  ok  test_live_page_matches_the_constant")


def main() -> None:
    test_no_cdn_reference_survives_in_any_mode()
    test_every_vendor_url_in_the_page_has_a_route()
    test_every_route_has_a_file_and_a_license()
    test_no_hljs_stylesheet_is_vendored()

    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(REVIEW_INPUT))
    # cwd=tmp, deliberately: see test_routes_serve_the_files_from_a_foreign_cwd.
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        test_routes_serve_the_files_from_a_foreign_cwd(base)
        test_vendor_routes_are_exact_match_only(base)
        test_live_page_matches_the_constant(base)

    print("OK (7 tests)")


if __name__ == "__main__":
    main()
