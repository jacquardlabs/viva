# Vendored browser assets

The SPA's third-party JS, CSS and fonts, pinned and served from disk by
`server.py` (`/vendor/<file>`). Nothing here is fetched at runtime — a viva
review works offline, on an air-gapped machine, and behind a proxy that blocks
jsdelivr or Google.

The JS and CSS were downloaded 2026-08-08 from jsDelivr at the exact versions
the previous `@major` ranges were resolving to; the four woff2 faces were
downloaded 2026-08-24 from Google's own font CDN. Versions are stamped into the
filenames and
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
| `fragment-mono-v6-latin.woff2` | [Fragment Mono](https://github.com/weiweihuanghuang/fragment-mono) | v6 | `https://fonts.gstatic.com/s/fragmentmono/v6/4iCr6K5wfMRRjxp0DA6-2CLnB4NHhqcL71Q.woff2` | OFL-1.1 — `LICENSE-fragment-mono-v6.txt` |
| `fragment-mono-v6-latin-ext.woff2` | Fragment Mono | v6 | `https://fonts.gstatic.com/s/fragmentmono/v6/4iCr6K5wfMRRjxp0DA6-2CLnB41HhqcL71QxtQ.woff2` | OFL-1.1 — same file |
| `fragment-mono-v6-latin-italic.woff2` | Fragment Mono | v6 | `https://fonts.gstatic.com/s/fragmentmono/v6/4iC16K5wfMRRjxp0DA6-2CLnB4Z3hK0MzVYBtA.woff2` | OFL-1.1 — same file |
| `fragment-mono-v6-latin-ext-italic.woff2` | Fragment Mono | v6 | `https://fonts.gstatic.com/s/fragmentmono/v6/4iC16K5wfMRRjxp0DA6-2CLnB4Z3iq0MzVYBtDPm.woff2` | OFL-1.1 — same file |

Four things a bump must not break.

**No hljs stylesheet.** Only highlight.js's *engine* is vendored. viva
hand-writes its own `.hljs` theme in `server.py` — a stock theme would spend
catalog yellow on syntax, which belongs to the reviewer's touch
(`tests/_server_harness.py`, `assert_ink_discipline`).

**Slim, never full.** `diff2html-ui-slim` wraps whatever `window.hljs` the page
already loaded. `diff2html-ui.min.js` embeds its own second copy of
highlight.js; taking it would double the engine and silently ignore viva's
theme.

**Latin subsets only.** Google serves Fragment Mono in three subsets per style;
only `latin` and `latin-ext` are vendored. The two `cyrillic-ext` files are 900
and 932 bytes, below the 1024-byte truncation floor
`tests/test_server_vendor_assets.py` uses to catch a half-finished download, so
adding them fails the suite on arrival. Cyrillic falls back to the system mono,
which is the correct degrade — do not re-add them.

**The faces are declared in `<style>`, not in a `<link>`.** They are
`@font-face { src: url('/vendor/…') }` rules inside the `HTML` constant's own
stylesheet. There is no remote font host left in the page at all, and
`tests/test_typography.py` forbids one by name — a reinstated Google Fonts
`<link>` is a test failure, not a quiet extra request. Bricolage Grotesque is
deliberately absent: it was an undocumented third family (DESIGN.md, "Two
families only"), and the three rules that named it now inherit `body`'s
grotesque stack.

## Bumping a version

1. `curl -sI <upstream URL with the new version>` and download that exact
   version, never a `@major` range. For a face, fetch
   `https://fonts.googleapis.com/css2?family=<Name>…` **with a modern-browser
   User-Agent** first — a plain curl UA gets legacy TTF URLs back, not the
   `fonts.gstatic.com` woff2 URLs the table above pins — then download each
   `src` from that response.
2. Name the file `<name>-<version>.min.<ext>` (`<name>-<version>-<subset>.woff2`
   for a face) and re-download the package's license file beside it.
3. Update `_VENDOR_ASSETS` in `server.py`, the URLs in the `HTML` constant, and
   this table. For a font the URL lives in an `@font-face`'s `src: url(...)`
   rather than a `<script src>`/`href` — a fourth spelling of the same place,
   and the one a bump forgets.
4. `python3 tests/test_server_vendor_assets.py` — it fails on a URL in the page
   with no route, and on a route with no file.
