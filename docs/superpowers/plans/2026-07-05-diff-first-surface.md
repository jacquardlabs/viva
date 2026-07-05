# Diff-First Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `/viva-diff` a diff-first surface: a mode-scoped wide layout (no nested scrolling, no 700px cap) and rendering delegated to diff2html (word-level intra-line highlighting, side-by-side/line-by-line by viewport), deleting the hand-rolled alignment/rendering layer.

**Architecture:** Two independent moves. Task 1: a `mode-diff` class on `<body>` (set in the existing diff dispatch branch) drives CSS overrides that widen `.shell`/`.bottom-inner` and remove `.section-content`'s nested scroll — nothing needs to escape the card once the card is wide, so the accordion's `overflow: hidden` stays untouched. Task 2: a ~35-line `renderDiffHunk` adapter feeds each hunk (with a render-time-synthesized `---/+++` preamble) to diff2html, replacing `renderDiffTable` and everything under it; the three hard-won lessons (specificity bleed, line-number selection, cross-pane anchors) are ported to the new DOM in the same commit.

**Tech Stack:** Vanilla JS/CSS embedded in `server.py`'s `HTML` constant (no build step, no npm in the repo). diff2html@3 via jsdelivr (same CDN precedent as marked@12/dompurify@3/hljs@11). Python stdlib test harness.

## Global Constraints

- Every source file this repo ships stays stdlib-only — no npm, no build step (`CLAUDE.md`). CDN `<script>`/`<link>` tags are the established dependency mechanism, with graceful degradation when absent.
- Tests are plain `python3 tests/test_*.py` scripts with a `main()` — wiring checks only; no JS/browser harness exists. Deeper verification is a dev-only Node/Playwright attempt with an honest human-checkpoint fallback (Task 3).
- Approved spec: `docs/superpowers/specs/2026-07-05-diff-first-surface-design.md`. Exact values, do not deviate: shell/bottom-bar width `min(95vw, 1600px)`; format switch at `window.innerWidth >= 900` (side-by-side above, line-by-line below); diff2html config `drawFileList: false, matching: 'words', diffStyle: 'word'`; CDN pins `diff2html@3`.
- `section.content` stays byte-for-byte untouched on disk and over `/input` — the `---/+++` preamble is synthesized at render time only, never stored.
- Non-goals (do NOT touch): the card accordion and its `overflow: hidden`, review/QA modes' layout, `.sheet-frame`, `parse_diff.py`, `scripts/schema.py`, any endpoint.

---

### Task 1: `mode-diff` layout restyle

**Files:**
- Modify: `server.py:157-161` (insert CSS after the `.shell` rule)
- Modify: `server.py:3039-3048` (the `data.mode === 'diff'` dispatch branch)
- Test: `tests/test_server_diff_render.py`

**Interfaces:**
- Produces: the `mode-diff` class on `<body>` in diff mode — Task 2's DESIGN.md text references it; no code in Task 2 depends on it.
- Consumes: nothing.

Current code at `server.py:157-161`:

```css
.shell {
  max-width: 700px;
  margin: 0 auto;
  padding: 40px 20px 140px;
}
```

Current code at `server.py:3039-3048` (inside the `/input` fetch's `.then`):

```js
    } else if (data.mode === 'diff') {
      REVIEW_DATA = data;
      el('doc-path').textContent    = data.doc_file || 'diff';
      el('doc-path').title          = data.doc_file || 'diff';
      el('doc-title').innerHTML     = 'viva <em>diff</em>';
      el('round-badge').textContent = String(data.round).padStart(2, '0');
      document.title = 'viva · diff · REV ' + String(data.round).padStart(2, '0');
      el('review-view').style.display = '';
      initReview();
      connectSSE();
```

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server_diff_render.py`, after `test_page_ships_similarity_alignment` and before `def main()` (line numbers may have shifted — insert before `def main()` regardless):

```python
def test_page_ships_mode_diff_layout() -> None:
    """Wiring check only: the diff dispatch branch stamps mode-diff on <body>,
    and the mode-scoped CSS overrides (wide shell/bottom bar, no nested
    section scroll) ship in the served page. Does not measure rendered
    layout — that's the Task 3 visual checkpoint."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        m = re.search(r"mode === 'diff'\) \{(.*?)\} else", page, re.S)
        assert m, "page missing: diff dispatch branch"
        assert "document.body.classList.add('mode-diff')" in m.group(1), \
            "diff branch does not stamp mode-diff on body"
        m = re.search(r"\.mode-diff \.shell,\s*\.mode-diff \.bottom-inner \{[^}]*\}", page)
        assert m and "min(95vw, 1600px)" in m.group(0), \
            "page missing: mode-diff wide shell/bottom-bar rule"
        m = re.search(r"\.mode-diff \.section-content \{[^}]*\}", page)
        assert m and "max-height: none" in m.group(0) and "overflow-y: visible" in m.group(0), \
            "page missing: mode-diff nested-scroll removal"
    print("test_page_ships_mode_diff_layout: OK")
```

Add `test_page_ships_mode_diff_layout()` to `main()` before the final `print(...)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_server_diff_render.py`
Expected: `AssertionError: diff branch does not stamp mode-diff on body`

- [ ] **Step 3: Write the minimal implementation**

Insert after the `.shell` rule (`server.py:161`):

```css
/* ─── Diff-first layout (mode-diff) ──────────────────────────
   Code diffs want the opposite of the 700px prose column: width, and one
   scroll context. body.mode-diff (stamped by the diff dispatch branch)
   widens the shell and bottom bar together and removes .section-content's
   nested 60vh scroll — a hunk with context folding doesn't need the cap an
   arbitrary long document does, and page scroll becomes the only vertical
   scroll. Widening the container (instead of escaping it) is what keeps
   .card-body-inner's overflow:hidden accordion animation untouched — see
   the Rejected Approach note in the diff-first-surface design doc. */
.mode-diff .shell, .mode-diff .bottom-inner { max-width: min(95vw, 1600px); }
.mode-diff .section-content { max-height: none; overflow-y: visible; }
```

In the dispatch branch, insert one line after `REVIEW_DATA = data;`:

```js
    } else if (data.mode === 'diff') {
      REVIEW_DATA = data;
      document.body.classList.add('mode-diff');
      el('doc-path').textContent    = data.doc_file || 'diff';
```

(The rest of the branch is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_server_diff_render.py`
Expected: all tests print `OK`.

- [ ] **Step 5: Run the full test suite**

Run: `for f in tests/test_*.py; do python3 "$f" || echo "FAILED: $f"; done`
Expected: no `FAILED` lines.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server_diff_render.py
git commit -m "$(cat <<'EOF'
feat(diff-mode): mode-diff layout -- wide shell, single scroll context

Diff mode stamps body.mode-diff; mode-scoped CSS widens .shell and
.bottom-inner to min(95vw, 1600px) and removes .section-content's
nested 60vh scroll. Widening the container instead of escaping it is
what the failed viewport-breakout attempt taught: nothing fights
.card-body-inner's overflow:hidden accordion once the card itself is
wide. Review/QA modes have no mode-diff class and are untouched.
EOF
)"
```

---

### Task 2: diff2html rendering + deletion of the hand-rolled renderer

**Files:**
- Modify: `server.py:41-43` (CDN tags), `server.py:1492-1806` region (renderer JS), `server.py:2178-2220` (`_ensureRendered` + retry), `server.py:2371-2403` (mouseup guard), `server.py` `.sxs-*` CSS (~546-608, plus `.sxs-fold-btn:focus-visible` at ~1142 and the `.file-group-header` comment at ~302)
- Modify: `DESIGN.md` (replace the side-by-side section; amend the Layout section)
- Test: `tests/test_server_diff_render.py` (delete 8 tests, rewrite 1, add 2)

**Interfaces:**
- Produces: `renderDiffHunk(target, raw, title) -> boolean` — called only by `_ensureRendered`. Returns `true` on a diff2html render, or propagates `renderMarkdown`'s boolean on the fallback path.
- Produces: `closestD2hPane(node) -> Element|null` — called only by the mouseup handler.
- Consumes: `filepathFromTitle(title)` (kept — also used by `diffFileHunkCounts`), `sectionTitleFor(id)` (kept), `renderMarkdown` (kept).

**Grep before editing** — line numbers below were captured at commit `208f09c` and will have shifted after Task 1. The quoted code content is authoritative; locate each site with:
`grep -n "^function renderDiffTable\|^function _ensureRendered\|^const EXT_LANG\|^function closestSxsHalf\|crossesHalves\|retryFallbackMarkdownOnceDepsLoad" server.py`

- [ ] **Step 1: Rewrite the test file's diff-renderer coverage (failing first)**

In `tests/test_server_diff_render.py`:

**Delete these 8 test functions entirely** (and their calls in `main()`): `test_page_ships_side_by_side_renderer`, `test_page_ships_native_fold_button`, `test_page_ships_highlight_cap`, `test_page_ships_mobile_stacked_layout`, `test_page_ships_cross_half_selection_guard`, `test_page_ships_lcs_alignment`, `test_page_ships_shared_table_scroll`, `test_page_ships_similarity_alignment`.

**Keep unchanged:** `test_page_ships_file_group_header`, `test_grouped_sections_stay_file_contiguous`, `test_diff_content_served_verbatim`, `test_page_ships_diff_mode_sort_toggle_guard`, `test_page_ships_mode_diff_layout` (from Task 1).

**Rewrite** `test_page_ships_filepath_helper` (its `langFromTitle` assertion targets a function this task deletes):

```python
def test_page_ships_filepath_helper() -> None:
    """filepathFromTitle stays the single definition of 'strip the hunk
    suffix off a diff-mode section title' — both diffFileHunkCounts
    (file grouping) and renderDiffHunk (preamble synthesis) call it from
    their own function bodies."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        assert "function filepathFromTitle" in page, "page missing: function filepathFromTitle"
        for caller in ("diffFileHunkCounts", "renderDiffHunk"):
            m = re.search(r"function " + caller + r"\(.*?\n\}", page, re.S)
            assert m, f"page missing: function {caller}"
            assert "filepathFromTitle(" in m.group(0), \
                f"{caller} does not call filepathFromTitle — reuse not confirmed"
    print("test_page_ships_filepath_helper: OK")
```

**Add two new tests** (before `def main()`):

```python
def test_page_ships_diff2html_renderer() -> None:
    """Wiring check only: the served page loads diff2html@3 (script + css,
    both with ids so the load-retry listener can attach), ships the
    renderDiffHunk adapter with the spec's exact config (word-level diffs,
    words matching, no file list, viewport-picked output format, DOMPurify
    sanitize), and no longer ships any of the hand-rolled renderer."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        for needle in (
            'id="diff2html-script" src="https://cdn.jsdelivr.net/npm/diff2html@3/bundles/js/diff2html-ui.min.js"',
            'id="diff2html-css" href="https://cdn.jsdelivr.net/npm/diff2html@3/bundles/css/diff2html.min.css"',
        ):
            assert needle in page, f"page missing: {needle}"
        m = re.search(r"function renderDiffHunk\(.*?\n\}", page, re.S)
        assert m, "page missing: function renderDiffHunk"
        body = m.group(0)
        for needle in (
            "diffStyle: 'word'",
            "matching: 'words'",
            "drawFileList: false",
            "window.innerWidth >= 900 ? 'side-by-side' : 'line-by-line'",
            "DOMPurify.sanitize",
            "filepathFromTitle(",
        ):
            assert needle in body, f"renderDiffHunk missing: {needle}"
        # The hand-rolled renderer is deleted, not just bypassed. 'sxs' had no
        # other meaning anywhere in the page, so its total absence is the
        # strongest cheap deletion check available to this harness.
        for gone in ("function renderDiffTable", "function alignBlock",
                     "function lcsMatches", "function alignGap",
                     "function buildSxsTableHtml", "function toggleFold",
                     "HLJS_HIGHLIGHT_CAP", "sxs"):
            assert gone not in page, f"page still ships deleted symbol: {gone}"
    print("test_page_ships_diff2html_renderer: OK")


def test_page_ships_d2h_guards() -> None:
    """Wiring check only: the three ported lessons ship — the scoped td reset
    (specificity bleed), user-select:none on d2h line numbers (anchor
    hygiene), the cross-pane selection guard in the mouseup handler, and
    the d2h load-retry listener (the hljs-race lesson from gate-audit)."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        for needle in (
            ".section-content .d2h-wrapper td",
            ".section-content .d2h-code-linenumber",
            "user-select: none",
            "function closestD2hPane",
            "closestD2hPane(sel.anchorNode) !== closestD2hPane(sel.focusNode)",
            "d2h-pending",
            "el('diff2html-script')",
        ):
            assert needle in page, f"page missing: {needle}"
    print("test_page_ships_d2h_guards: OK")
```

Update `main()` to exactly:

```python
def main() -> None:
    test_page_ships_filepath_helper()
    test_page_ships_file_group_header()
    test_grouped_sections_stay_file_contiguous()
    test_diff_content_served_verbatim()
    test_page_ships_diff_mode_sort_toggle_guard()
    test_page_ships_mode_diff_layout()
    test_page_ships_diff2html_renderer()
    test_page_ships_d2h_guards()
    print("\nAll server diff-render tests passed.")
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `python3 tests/test_server_diff_render.py`
Expected: `AssertionError` mentioning `diff2html-script` (first missing needle).

- [ ] **Step 3: Implement — CDN tags and the adapter**

**(a)** After the hljs `<script>` tag (`server.py:43`), add:

```html
<link rel="stylesheet" id="diff2html-css" href="https://cdn.jsdelivr.net/npm/diff2html@3/bundles/css/diff2html.min.css">
<script defer id="diff2html-script" src="https://cdn.jsdelivr.net/npm/diff2html@3/bundles/js/diff2html-ui.min.js"></script>
```

**(b)** Replace the entire block from `const EXT_LANG = {` (currently ~1492) through the end of `function toggleFold` (currently ~1806) — i.e. delete `EXT_LANG`, `HLJS_HIGHLIGHT_CAP`, `langFromTitle`, `parseHunkRows`, `lcsMatches`, `wordTokens`, `jaccardSimilarity`, `SIMILARITY_THRESHOLD`, `alignGap`, `alignBlock`, `buildSxsTableHtml`, `renderDiffTable`, `toggleFold`, **keeping** `filepathFromTitle` and `sectionTitleFor` (they sit inside this range — preserve them verbatim, updating only `filepathFromTitle`'s comment as shown) — with:

```js
// section.title for diff-mode sections is "{filepath} hunk N" (parse_diff.py).
// Strip the " hunk N" suffix to recover the filepath. Shared by
// diffFileHunkCounts (file-header grouping) and renderDiffHunk (---/+++
// preamble synthesis).
function filepathFromTitle(title) {
  return String(title || '').replace(/\s+hunk\s+\d+$/, '');
}

// _ensureRendered only has a section id at render time; renderDiffHunk
// needs the section's title to synthesize the file preamble diff2html
// expects.
function sectionTitleFor(id) {
  const s = (REVIEW_DATA && REVIEW_DATA.sections || []).find(sec => sec.id === id);
  return s ? s.title : '';
}

/* Render one git hunk via diff2html: side-by-side above 900px viewport,
   line-by-line below, word-level intra-line diffs. Pure view transform —
   section.content stays the verbatim fence the /viva-diff skill
   (anchor-based edit relocation) and round-to-round carry-forward
   (byte-for-byte compare) depend on; the ---/+++ preamble diff2html needs
   to parse a bare @@ hunk is synthesized here at render time only, from
   the section title's filepath, and never stored.
   Output is committed through DOMPurify.sanitize like renderMarkdown's —
   diff2html's static output carries no listeners we rely on, so
   sanitizing the rendered markup is safe. Syntax coloring
   (ui.highlightCode) is an enhancement: if it throws, the word-level
   ins/del emphasis from diff2html itself still renders.
   Fallback when diff2html hasn't loaded: the fenced-```diff markdown view
   (the pre-#99 renderer), tagged d2h-pending so the load listener below
   upgrades it in place once the script arrives. Returns renderMarkdown's
   boolean on that path so _ensureRendered's retry bookkeeping stays
   correct. */
function renderDiffHunk(target, raw, title) {
  const body = raw.replace(/^```diff\n/, '').replace(/\n```$/, '');
  if (!/^@@ /.test(body)) return renderMarkdown(target, raw);
  if (!(window.Diff2HtmlUI && window.DOMPurify)) {
    const ok = renderMarkdown(target, raw);
    if (ok) target.classList.add('d2h-pending');
    return ok;
  }
  target.classList.remove('d2h-pending');
  const fp = filepathFromTitle(title);
  const diff = '--- a/' + fp + '\n+++ b/' + fp + '\n' + body;
  const ui = new Diff2HtmlUI(target, diff, {
    drawFileList: false,
    matching: 'words',
    diffStyle: 'word',
    outputFormat: window.innerWidth >= 900 ? 'side-by-side' : 'line-by-line',
  });
  ui.draw();
  try { ui.highlightCode(); } catch (e) { /* syntax color only; word-level diff survives */ }
  target.innerHTML = DOMPurify.sanitize(target.innerHTML);
  target.classList.remove('md-raw');
  return true;
}
```

- [ ] **Step 4: Implement — rewire `_ensureRendered` and add the d2h retry**

Replace `_ensureRendered` (grep `^function _ensureRendered`) with:

```js
function _ensureRendered(id) {
  if (!_pendingMarkdown.has(id)) return;
  const contentEl = el('rcontent-' + id);
  if (!contentEl) return;
  const raw = _pendingMarkdown.get(id);
  // Diff mode's hunk content (a fenced ```diff block) renders via diff2html
  // (renderDiffHunk). Binary-change sections have no fence (parse_diff.py's
  // plaintext sentinel) and fall through to renderMarkdown unchanged.
  const isDiffHunk = REVIEW_DATA && REVIEW_DATA.mode === 'diff' && /^```diff\n/.test(raw);
  const rendered = isDiffHunk ? renderDiffHunk(contentEl, raw, sectionTitleFor(id)) : renderMarkdown(contentEl, raw);
  if (!rendered) return;
  // A d2h-pending card rendered successfully as fenced markdown but is
  // waiting for diff2html to load — keep its source so the diff2html-script
  // load listener below can re-render it properly. Deleting it here would
  // strand the card on the fallback view forever (the same
  // late-loading-dependency lesson as the marked/DOMPurify retry, and the
  // hljs race the gate-audit pass caught).
  if (!contentEl.classList.contains('d2h-pending')) _pendingMarkdown.delete(id);
  renderHighlights(id);
}
```

Directly after the existing `retryFallbackMarkdownOnceDepsLoad` IIFE (grep `retryFallbackMarkdownOnceDepsLoad`), add:

```js
// Same retry pattern for diff2html: a diff-mode card opened before
// diff2html-ui.min.js finished loading rendered as the fenced-markdown
// fallback (class d2h-pending, still in _pendingMarkdown). Upgrade those
// cards in place once the script lands.
(function retryDiffHunksOnceD2hLoads() {
  const script = el('diff2html-script');
  if (!script) return;
  script.addEventListener('load', () => {
    document.querySelectorAll('.section-content.d2h-pending').forEach(contentEl => {
      const m = contentEl.id.match(/^rcontent-(.+)$/);
      if (m) _ensureRendered(m[1]);
    });
  }, { once: true });
})();
```

- [ ] **Step 5: Implement — port the selection guard**

In the mouseup handler (grep `crossesHalves`), replace the comment + two lines with:

```js
    // diff2html's side-by-side mode renders old/new as two adjacent panes.
    // A drag crossing panes (or starting/ending outside them) yields
    // DOM-order text that is not a contiguous substring of the raw hunk —
    // anchoring a comment to it would silently defeat offsetInSource and
    // the /viva-diff skill's grep fallback, so it degrades to an unanchored
    // whole-section note. Same guard the hand-rolled table carried.
    const crossesPanes = closestD2hPane(sel.anchorNode) !== closestD2hPane(sel.focusNode);
    openCommentPopover(m[1], crossesPanes ? {} : { anchor: { text, offset: offsetInSource(m[1], text) } });
```

Replace `closestSxsHalf` (grep `^function closestSxsHalf`) with:

```js
// Closest diff2html side-by-side pane ancestor of a selection endpoint, or
// null outside one (line-by-line mode, review-mode content) — there the
// comparison is null !== null, a no-op, preserving the anchored path.
function closestD2hPane(node) {
  const el = node && node.nodeType === 3 ? node.parentElement : node;
  return el && el.closest ? el.closest('.d2h-file-side-diff') : null;
}
```

- [ ] **Step 6: Implement — CSS swap**

Delete the entire `.sxs-*` CSS region: from the `/* ─── side-by-side hunk rendering (diff mode, issue #99) ...` comment block (grep `side-by-side hunk rendering`) through `.sxs-fold-btn.is-open { color: var(--text); }` **and** the `@media (max-width: 720px)` block that follows it containing `.sxs-change-cell { flex-direction: column; }` (delete the whole media block — its only rule is the sxs one). Also remove `.sxs-fold-btn:focus-visible` from the focus-visible group selector list (grep `sxs-fold-btn:focus-visible` — delete that one selector line, keeping the rest of the group intact), and in the `.file-group-header` comment (grep `sxs-fold-cell`), change `same quiet typographic register as .sxs-fold-cell/.diff-toggle` to `same quiet typographic register as .diff-toggle`.

In the deleted region's place, add:

```css
/* ─── diff2html output (diff mode) ─────────────────────────
   Rendering is delegated to diff2html (see renderDiffHunk); these rules
   are viva-side guards, not a theme. The scoped td reset carries the
   specificity lesson from the hand-rolled table: .section-content td (the
   generic editorial-markdown-table rule) would otherwise chop every diff
   row into bordered, padded cells. Line-number cells are unselectable so
   a drag can't capture line numbers into comment.anchor.text. */
.section-content .d2h-wrapper td { border-bottom: none; padding: 0; }
.section-content .d2h-code-linenumber,
.section-content .d2h-code-side-linenumber { user-select: none; }
```

- [ ] **Step 7: Implement — DESIGN.md**

Replace the entire `## Side-by-side hunk rendering (#99)` section body (heading through the last bullet before `## File-header grouping`) with:

```markdown
## Diff rendering (#99, superseded in-branch by diff2html delegation)

`/viva-diff` renders each hunk via [diff2html](https://github.com/rtfpessoa/diff2html)
(MIT, `diff2html@3` on jsdelivr — same CDN precedent as marked/DOMPurify/hljs).
The `renderDiffHunk` adapter strips the section's ` ```diff ` fence,
synthesizes the `---/+++` preamble from the section title's filepath at
render time (never stored — `section.content` stays byte-for-byte verbatim
for anchors and carry-forward), and draws with `diffStyle: 'word'`
(intra-line word-level emphasis), `matching: 'words'`, no file list, and
`outputFormat` picked by viewport: side-by-side at ≥900px, line-by-line
below. Output is committed through `DOMPurify.sanitize`. Fallback chain
when the CDN is absent: fenced ` ```diff ` via `renderMarkdown` (tagged
`d2h-pending`, upgraded in place when the script loads) → `md-raw` plain
text. Binary sections (parse_diff.py's plaintext sentinel, no fence)
render as prose, unchanged. viva-side guards on the diff2html DOM: a
scoped td reset (the generic `.section-content td` editorial-table rule
would otherwise border/pad every diff row), `user-select: none` on line
numbers, and a cross-pane selection guard that degrades a selection
spanning both side-by-side panes to an unanchored whole-section note.

## Diff-first layout (mode-diff)

Diff mode stamps `mode-diff` on `<body>`. Mode-scoped overrides widen
`.shell` and `.bottom-inner` together to `min(95vw, 1600px)` and remove
`.section-content`'s `60vh` nested scroll — page scroll is the only
vertical scroll in diff mode. Widening the container (never escaping it)
is the load-bearing choice: it leaves `.card-body-inner`'s
`overflow: hidden` accordion animation untouched. Review/QA modes carry
no `mode-diff` class and are unaffected.
```

In DESIGN.md's `## Layout` section, change the sentence `Do not exceed 700px in the shell.` to `Do not exceed 700px in the shell (diff mode is the one exception: body.mode-diff widens it — see Diff-first layout).`

- [ ] **Step 8: Run tests to verify they pass**

Run: `python3 tests/test_server_diff_render.py`
Expected: all 8 tests print `OK`.

- [ ] **Step 9: Dev-only Node verification of the adapter's input contract (attempt; honest fallback)**

The real risk diff2html carries: does it parse a bare hunk with only a synthesized `---/+++` preamble (no `diff --git` line)? Verify with the actual pinned bundle if the sandbox allows network:

```bash
curl -sL "https://cdn.jsdelivr.net/npm/diff2html@3/bundles/js/diff2html.min.js" -o /tmp/d2h-core.js \
  && curl -sL "https://cdn.jsdelivr.net/npm/diff2html@3/bundles/css/diff2html.min.css" -o /tmp/d2h.css \
  && node -e "
const D2H = require('/tmp/d2h-core.js');
const diff = '--- a/server.py\n+++ b/server.py\n@@ -1,3 +1,4 @@\n ctx line\n-old line here\n+new line here\n+added line\n ctx line 2\n';
const html = D2H.html(diff, { drawFileList: false, matching: 'words', diffStyle: 'word', outputFormat: 'side-by-side' });
console.log('parsed file block:', html.includes('d2h-file-wrapper'));
console.log('side-by-side panes:', (html.match(/d2h-file-side-diff/g) || []).length);
console.log('word-level ins/del:', html.includes('<ins>') && html.includes('<del>'));
console.log('content present:', html.includes('old line here') && html.includes('new line here'));
" \
  && grep -c "d2h-code-side-linenumber\|d2h-code-linenumber\|d2h-file-side-diff" /tmp/d2h.css
```

Expected: `parsed file block: true`, `side-by-side panes: 2`, `word-level ins/del: true`, `content present: true`, and a nonzero grep count (confirming the guard selectors in Steps 5-6 name real classes in the shipped CSS).
If curl cannot reach jsdelivr from the sandbox: record that in the report, and this verification moves into Task 3's human checkpoint — do not silently skip it.
If the guard-selector grep returns 0 for any class: read `/tmp/d2h.css` to find the real line-number/pane class names and correct Steps 5-6's selectors to match before committing.

- [ ] **Step 10: Run the full test suite**

Run: `for f in tests/test_*.py; do python3 "$f" || echo "FAILED: $f"; done`
Expected: no `FAILED` lines.

- [ ] **Step 11: Commit**

```bash
git add server.py tests/test_server_diff_render.py DESIGN.md
git commit -m "$(cat <<'EOF'
feat(diff-mode): delegate hunk rendering to diff2html

Replaces the hand-rolled side-by-side renderer (parseHunkRows,
lcsMatches, alignBlock, alignGap, wordTokens, jaccardSimilarity,
buildSxsTableHtml, renderDiffTable, toggleFold, HLJS_HIGHLIGHT_CAP,
all .sxs-* CSS -- several hundred lines) with a ~35-line adapter over
diff2html@3 (jsdelivr, same CDN precedent as marked/DOMPurify/hljs):
word-level intra-line diffs, side-by-side >=900px / line-by-line
below, DOMPurify-sanitized output, fenced-markdown fallback with an
in-place upgrade when the script loads late.

section.content stays byte-for-byte verbatim -- the ---/+++ preamble
diff2html needs is synthesized at render time from the section title
and never stored, so anchors and carry-forward are untouched.

Ports the three lessons from the hand-rolled era to the new DOM in
the same change: scoped td reset (the .section-content td editorial-
table rule bled borders/padding into diff rows before), unselectable
line numbers (anchor hygiene), and the cross-pane selection guard
(cross-pane drags degrade to unanchored notes).
EOF
)"
```

---

### Task 3: End-to-end verification (live server + visual checkpoint)

No new production code. The wiring tests cannot see pixels; this task closes that gap as far as the sandbox allows and ends with an explicit human checkpoint — the pattern that caught every real rendering bug on this branch.

- [ ] **Step 1: Relaunch `/viva-diff` against this branch's own diff**

```bash
cd /Users/bryan/Projects/viva/.claude/worktrees/better-diffs
pkill -f "server.py --mode diff" 2>/dev/null; sleep 0.5
rm -f .viva/server.url .viva/review-input-r*.json .viva/review-r*.json
rm -rf .viva/attachments
git diff origin/main > .viva/diff.patch
python3 scripts/parse_diff.py .viva/diff.patch \
  --output .viva/review-input-r1.json --round 1 --doc-file "origin/main"
python3 server.py --mode diff \
  --input .viva/review-input-r1.json --output .viva/review-r1.json &
for i in $(seq 1 100); do [ -f .viva/server.url ] && break; sleep 0.1; done
URL=$(cat .viva/server.url); echo "URL: $URL"
curl -sS -o /dev/null -w "HTTP %{http_code}\n" "$URL/" --max-time 3
```

Expected: `HTTP 200`. Report the URL only after the curl check passes (lesson: a mis-reported port cost a round-trip earlier on this branch).

- [ ] **Step 2: Attempt dev-only headless screenshots (never committed)**

```bash
cd /tmp && mkdir -p viva-shots && cd viva-shots
npm init -y >/dev/null 2>&1 && npm i playwright >/dev/null 2>&1 && npx playwright install chromium >/dev/null 2>&1 \
  && node -e "
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1500, height: 1000 } });
  await p.goto(process.env.VIVA_URL);
  await p.waitForTimeout(1500);
  await p.click('.card-head');            // open the first hunk card
  await p.waitForTimeout(800);
  await p.screenshot({ path: 'diff-wide.png', fullPage: false });
  await p.setViewportSize({ width: 700, height: 1000 });
  await p.waitForTimeout(500);
  await p.screenshot({ path: 'diff-narrow.png' });
  await b.close();
})();" \
  || echo "PLAYWRIGHT UNAVAILABLE — visual verification falls to the human checkpoint"
```

If screenshots were produced, Read them and verify: the diff renders via diff2html (side-by-side panes at 1500px width, using most of the viewport), no nested scrollbars inside the card, word-level ins/del emphasis visible, line numbers present, and no `.section-content td` border striping. Fix anything wrong before Step 3.

- [ ] **Step 3: Human visual checkpoint (blocking)**

Ask the human to open the URL and confirm, before anything merges: wide layout engages and scales with the window; side-by-side reads correctly on a real mixed hunk (the `_ensureRendered` hunk is the canonical test case); word-level highlighting shows; selecting text and commenting still anchors correctly within one pane and degrades to an unanchored note across panes; approve/skip/keyboard flow unchanged; a binary section still renders as prose. This checkpoint is the merge gate for the visual half of this branch.

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** mode-diff class + wide shell/bottom bar + nested-scroll removal (Task 1) ✓; diff2html tags/adapter/config/sanitize/fallback-with-retry (Task 2, Steps 3-4) ✓; preamble synthesis, content byte-for-byte (adapter code + kept `test_diff_content_served_verbatim`) ✓; deletion list incl. `.sxs-*` CSS and tests (Task 2, Steps 1, 3b, 6) ✓; three ported lessons (Steps 5-6, tested by `test_page_ships_d2h_guards`) ✓; DESIGN.md replacement + Layout amendment (Step 7) ✓; Playwright attempt + human checkpoint (Task 3) ✓; non-goals — no task touches the accordion, `.sheet-frame`, review/QA layout, parse_diff, schema, or endpoints ✓.
- **Placeholder scan:** none. One deliberately-hedged step (Task 2 Step 9's guard-selector grep with a correct-the-selectors instruction) is an explicit verification with a concrete remedy, not a placeholder.
- **Type consistency:** `renderDiffHunk(target, raw, title)` — defined Step 3, called with exactly that arity in Step 4's `_ensureRendered` (the old 4th `sectionId` arg is gone along with the fold ids that needed it). `closestD2hPane` defined and consumed in Step 5. `filepathFromTitle`/`sectionTitleFor` signatures unchanged from the code they're preserved out of.
