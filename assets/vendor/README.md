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

Each file's SHA-256 is recorded below and asserted by
`tests/test_server_vendor_assets.py` — a tampered blob, a wrong-version
download, or a compromised CDN response in a future bump fails the suite
instead of shipping as an unreadable 300KB minified diff.

| File | Package | Version | Upstream | License | SHA-256 |
|---|---|---|---|---|---|
| `marked-12.0.2.min.js` | [marked](https://github.com/markedjs/marked) | 12.0.2 | `https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js` | MIT — `LICENSE-marked-12.0.2.md` | `15fabce5b65898b32b03f5ed25e9f891a729ad4c0d6d877110a7744aa847a894` |
| `purify-3.4.13.min.js` | [DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.13 | `https://cdn.jsdelivr.net/npm/dompurify@3.4.13/dist/purify.min.js` | MPL-2.0 OR Apache-2.0 — `LICENSE-dompurify-3.4.13.txt` (upstream ships the Apache-2.0 text) | `9ab3d44d73c3e3947f9ab72e0f0bc15c7f1931d60b365ba261fc85fe59013c56` |
| `highlight-11.11.1.min.js` | [@highlightjs/cdn-assets](https://github.com/highlightjs/cdn-release) | 11.11.1 | `https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.11.1/highlight.min.js` | BSD-3-Clause — `LICENSE-highlightjs-11.11.1.txt` | `c4a399dd6f488bc97a3546e3476747b3e714c99c57b9473154c6fb8d259b9381` |
| `diff2html-3.4.56.min.js` | [diff2html](https://github.com/rtfpessoa/diff2html) | 3.4.56 | `https://cdn.jsdelivr.net/npm/diff2html@3.4.56/bundles/js/diff2html.min.js` | MIT — `LICENSE-diff2html-3.4.56.md` | `a2110a09cee157bd5466da77be02107ac81a0baa2bc1f3fe81aac8183314598e` |
| `diff2html-ui-slim-3.4.56.min.js` | diff2html | 3.4.56 | `https://cdn.jsdelivr.net/npm/diff2html@3.4.56/bundles/js/diff2html-ui-slim.min.js` | MIT — same file | `7bf8f0b288986bce9f8a8f5d4608eadd02a715ff95135a2fa28d1a10fcad2310` |
| `diff2html-3.4.56.min.css` | diff2html | 3.4.56 | `https://cdn.jsdelivr.net/npm/diff2html@3.4.56/bundles/css/diff2html.min.css` | MIT — same file | `d3ecc0e9b2b1e5c8466c19de29bed052fd0863475d25829ecc858446efded372` |
| `fragment-mono-v6-latin.woff2` | [Fragment Mono](https://github.com/weiweihuanghuang/fragment-mono) | v6 | `https://fonts.gstatic.com/s/fragmentmono/v6/4iCr6K5wfMRRjxp0DA6-2CLnB4NHhqcL71Q.woff2` | OFL-1.1 — `LICENSE-fragment-mono-v6.txt` | `4f4dc27f4a770c0d02fde800daa836c8adc0d1e423b28da74baaf0d1cc3ab96c` |
| `fragment-mono-v6-latin-ext.woff2` | Fragment Mono | v6 | `https://fonts.gstatic.com/s/fragmentmono/v6/4iCr6K5wfMRRjxp0DA6-2CLnB41HhqcL71QxtQ.woff2` | OFL-1.1 — same file | `e3085219252209a8d4128f90ff6d793315c752f73a979082d1d85d9cb46f63a4` |
| `fragment-mono-v6-latin-italic.woff2` | Fragment Mono | v6 | `https://fonts.gstatic.com/s/fragmentmono/v6/4iC16K5wfMRRjxp0DA6-2CLnB4Z3hK0MzVYBtA.woff2` | OFL-1.1 — same file | `836de8533e207499ee055cb8fad970be248d47f2ee2f20aee85cd49f783f72bb` |
| `fragment-mono-v6-latin-ext-italic.woff2` | Fragment Mono | v6 | `https://fonts.gstatic.com/s/fragmentmono/v6/4iC16K5wfMRRjxp0DA6-2CLnB4Z3iq0MzVYBtDPm.woff2` | OFL-1.1 — same file | `e991acdb2b5a26869faa5ed76665dd2cb67bec4bb25919935d2133b4002a0ec1` |

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
4. Record the new file's SHA-256 in this table's last column
   (`shasum -a 256 <file>`) — the integrity check on the bump procedure
   itself; a wrong-version download or a tampered response is otherwise a
   300KB minified diff nobody reads.
5. `python3 tests/test_server_vendor_assets.py` — it fails on a URL in the page
   with no route, on a route with no file, and now on a file whose SHA-256
   disagrees with this table.
