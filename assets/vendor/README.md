# Vendored browser assets

The SPA's third-party JS and CSS, pinned and served from disk by `server.py`
(`/vendor/<file>`). Nothing here is fetched at runtime — a viva review works
offline, on an air-gapped machine, and behind a proxy that blocks jsdelivr.

Downloaded 2026-08-08 from jsDelivr at the exact versions the previous
`@major` ranges were resolving to. Versions are stamped into the filenames and
into the served URLs, which is what makes `Cache-Control: immutable` safe: an
upgrade changes the path, so a browser holding the old bytes never serves them
for the new pin.

| File | Package | Version | Upstream | License |
|---|---|---|---|---|
| `marked-12.0.2.min.js` | [marked](https://github.com/markedjs/marked) | 12.0.2 | `https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js` | MIT — `LICENSE-marked-12.0.2.md` |
| `purify-3.4.13.min.js` | [DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.13 | `https://cdn.jsdelivr.net/npm/dompurify@3.4.13/dist/purify.min.js` | MPL-2.0 OR Apache-2.0 — `LICENSE-dompurify-3.4.13.txt` (upstream ships the Apache-2.0 text) |
| `highlight-11.11.1.min.js` | [@highlightjs/cdn-assets](https://github.com/highlightjs/cdn-release) | 11.11.1 | `https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.11.1/highlight.min.js` | BSD-3-Clause — `LICENSE-highlightjs-11.11.1.txt` |
| `diff2html-3.4.56.min.js` | [diff2html](https://github.com/rtfpessoa/diff2html) | 3.4.56 | `https://cdn.jsdelivr.net/npm/diff2html@3.4.56/bundles/js/diff2html.min.js` | MIT — `LICENSE-diff2html-3.4.56.md` |
| `diff2html-ui-slim-3.4.56.min.js` | diff2html | 3.4.56 | `https://cdn.jsdelivr.net/npm/diff2html@3.4.56/bundles/js/diff2html-ui-slim.min.js` | MIT — same file |
| `diff2html-3.4.56.min.css` | diff2html | 3.4.56 | `https://cdn.jsdelivr.net/npm/diff2html@3.4.56/bundles/css/diff2html.min.css` | MIT — same file |

Two things a bump must not break.

**No hljs stylesheet.** Only highlight.js's *engine* is vendored. viva
hand-writes its own `.hljs` theme in `server.py` — a stock theme would spend
catalog yellow on syntax, which belongs to the reviewer's touch
(`tests/_server_harness.py`, `assert_ink_discipline`).

**Slim, never full.** `diff2html-ui-slim` wraps whatever `window.hljs` the page
already loaded. `diff2html-ui.min.js` embeds its own second copy of
highlight.js; taking it would double the engine and silently ignore viva's
theme.

## Bumping a version

1. `curl -sI <upstream URL with the new version>` and download that exact
   version, never a `@major` range.
2. Name the file `<name>-<version>.min.<ext>` and re-download the package's
   license file beside it.
3. Update `_VENDOR_ASSETS` in `server.py`, the `<script>`/`href` URLs in the
   `HTML` constant, and this table.
4. `python3 tests/test_server_vendor_assets.py` — it fails on a URL in the page
   with no route, and on a route with no file.
