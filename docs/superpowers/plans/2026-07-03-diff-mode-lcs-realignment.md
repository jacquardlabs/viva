# Diff-Mode LCS Realignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/viva-diff`'s naive index-based del/add line pairing within a replace block with a real LCS (longest common subsequence) alignment, and fix the side-by-side table's per-cell scroll and density issues surfaced during live review.

**Architecture:** Two new pure functions (`lcsMatches`, `alignBlock`) slot into the existing `parseHunkRows` → `buildSxsTableHtml` → `renderDiffTable` pipeline with minimal disruption: `alignBlock` replaces the naive pairing loop inside `parseHunkRows`'s `flushChanges()`, and `buildSxsTableHtml` gains one new `'same'` row-type branch (an LCS match — identical text on both sides, rendered like a context row). CSS fixes are independent of the algorithm.

**Tech Stack:** Vanilla JS embedded in `server.py`'s `HTML` constant (no build step, no npm). Python stdlib test harness (`tests/_server_harness.py`).

## Global Constraints

- Every source file this repo ships stays stdlib-only — no npm, no build step (`CLAUDE.md`).
- Tests are plain `python3 tests/test_*.py` scripts with a `main()` printing an `OK`/pass summary — no pytest, no Node dependency in the committed test suite. There is no JS/browser test harness in this repo; deeper JS behavioral verification happens by extracting the real shipped `<script>` and running it under Node as a manual verification step, not a checked-in test (established pattern in `tests/test_server_diff_render.py` throughout this feature's history).
- Approved spec: `docs/superpowers/specs/2026-07-03-diff-mode-lcs-realignment-design.md`. Explicitly deferred (do NOT implement): intra-line/word-level highlighting within a paired-but-different row. Explicitly out of scope: any change to hunk-level parsing, binary-file handling, the fold-toggle button, the highlight cap, the mobile column-stacking breakpoint's existing `flex-direction: column` rule, or the cross-half selection guard.
- **`'same'` rows are never foldable** — they render visible, unlike top-level context runs between blocks (which stay foldable, unchanged).

---

### Task 1: LCS-based block realignment

**Files:**
- Modify: `server.py:1516-1576` (the misplaced docblock, `parseHunkRows`'s own comment, and its body)
- Modify: `server.py:1585-1627` (`buildSxsTableHtml`) — new `'same'` branch
- Modify: `server.py:1629` (`renderDiffTable`) — relocate the misplaced docblock to sit directly above this function (no signature or body change)
- Test: `tests/test_server_diff_render.py`

**Interfaces:**
- Produces: `lcsMatches(oldTexts, newTexts) -> Array<{oldIdx: number, newIdx: number}>` — pure function, ordered LCS match indices.
- Produces: `alignBlock(delBuf, addBuf) -> Array<{type: 'change', del, add} | {type: 'same', del, add}>` — consumed only by `parseHunkRows`'s `flushChanges()` in this task; no other consumer.
- Consumes: nothing new from other tasks. `delBuf`/`addBuf` entries are `{no: number, text: string}`, the same shape `parseHunkRows` already builds.

Current code at `server.py:1516-1576`:

```js
/* Render a git hunk (fenced ```diff block, git's raw +/- /space-prefixed
   text — see parse_diff.py) as a JetBrains-style side-by-side table: removed
   lines left (orange), added lines right (teal), context spanning the full
   width and collapsed behind a fold row. Pure view transform — never reads
   or writes section.content itself, which stays the verbatim fence the
   /viva-diff skill (anchor-based edit relocation) and round-to-round
   carry-forward (byte-for-byte content compare) depend on.
   Returns true on success; on a malformed/unrecognized hunk, defensively
   falls back to the normal renderMarkdown path (and propagates its return
   value, so retry-on-CDN-load bookkeeping in _ensureRendered stays correct). */
// Parse a git hunk's body lines (everything after the @@ header) into a
// flat list of row records — no HTML in this step, see buildSxsTableHtml
// for that. Consecutive removed/added lines buffer and flush as paired
// 'change' records (removed[i] ↔ added[i], padding the shorter side with
// null) — covers pure-add/pure-delete hunks and approximates typical
// remove-block-then-add-block hunks without a full LCS realignment.
// Consecutive context lines flush as one 'fold' record (with the real ids
// its rows will get, for aria-controls) followed by their 'ctx' records.
// sectionId namespaces those ids — every other generated id in this file is
// namespaced by section/comment id (rdiff-<id>, rthread-<cid>, rcard-<id>,
// ...); without it, every diff section's first fold group would collide on
// the same "sxs-g1-r0", since groupN resets to 0 on each parseHunkRows call.
function parseHunkRows(lines, oldNo, newNo, sectionId) {
  const rows = [];
  let delBuf = [], addBuf = [], ctxBuf = [], groupN = 0;

  function flushChanges() {
    const n = Math.max(delBuf.length, addBuf.length);
    for (let i = 0; i < n; i++) {
      rows.push({ type: 'change', del: delBuf[i] || null, add: addBuf[i] || null });
    }
    delBuf = []; addBuf = [];
  }
  function flushContext() {
    if (!ctxBuf.length) return;
    groupN++;
    const gid = 'sxs-' + sectionId + '-g' + groupN;
    const rowIds = ctxBuf.map((_, i) => gid + '-r' + i);
    rows.push({ type: 'fold', gid, count: ctxBuf.length, rowIds });
    ctxBuf.forEach((c, i) => rows.push({
      type: 'ctx', gid, rowId: rowIds[i], oldNo: c.oldNo, newNo: c.newNo, text: c.text,
    }));
    ctxBuf = [];
  }

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    const marker = line.charAt(0);
    if (marker === '\\') continue;   // "\ No newline at end of file" — not a content line
    if (marker === '-') { flushContext(); delBuf.push({ no: oldNo++, text: line.slice(1) }); }
    else if (marker === '+') { flushContext(); addBuf.push({ no: newNo++, text: line.slice(1) }); }
    else {
      flushChanges();
      const text = marker === ' ' ? line.slice(1) : line;
      ctxBuf.push({ oldNo: oldNo++, newNo: newNo++, text });
    }
  }
  flushChanges();
  flushContext();
  return rows;
}
```

Current code at `server.py:1590-1607` (inside `buildSxsTableHtml`, the `'change'` branch — for reference, unchanged by this task):

```js
    if (r.type === 'change') {
      const d = r.del, a = r.add;
      // An unpaired removal/addition (pure-add or pure-delete hunk, or the
      // leftover tail when one side has more lines than the other) omits
      // the other side's div entirely rather than rendering an empty
      // 50%-wide placeholder — .sxs-half's flex-grow then lets the one
      // present side fill the whole code cell instead of every line being
      // squeezed into (and needing its own horizontal scroll within) half
      // the available width while the other half sits permanently blank.
      html += '<tr class="sxs-row">'
        + '<td class="sxs-gutter sxs-gutter-del">' + (d ? d.no : '') + '</td>'
        + '<td class="sxs-gutter sxs-gutter-add">' + (a ? a.no : '') + '</td>'
        + '<td class="sxs-code sxs-change-cell">'
        +   (d ? '<div class="sxs-half sxs-del">' + codeEl(d.text) + '</div>' : '')
        +   (a ? '<div class="sxs-half sxs-add">' + codeEl(a.text) + '</div>' : '')
        + '</td></tr>';
    } else if (r.type === 'fold') {
```

- [ ] **Step 1: Write the failing test**

Open `tests/test_server_diff_render.py`. Add this test function anywhere after `test_page_ships_cross_half_selection_guard` (before `def main()`):

```python
def test_page_ships_lcs_alignment() -> None:
    """Wiring check only: the served page ships lcsMatches, alignBlock, and
    the sxs-same-row class, so a replace block's line pairing goes through
    real LCS realignment instead of naive buffer-index pairing. Does not
    execute the algorithm against a real DOM — see the plan's manual Node
    verification step for behavioral proof."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        for needle in (
            "function lcsMatches",
            "function alignBlock",
            "sxs-same-row",
        ):
            assert needle in page, f"page missing: {needle}"
        m = re.search(r"function alignBlock\(.*?\n\}", page, re.S)
        assert m, "page missing: function alignBlock"
        assert "lcsMatches(" in m.group(0), "alignBlock does not call lcsMatches"
    print("test_page_ships_lcs_alignment: OK")
```

Update `main()` to call it (add the line after `test_page_ships_cross_half_selection_guard()`):

```python
def main() -> None:
    test_page_ships_side_by_side_renderer()
    test_page_ships_filepath_helper()
    test_page_ships_file_group_header()
    test_grouped_sections_stay_file_contiguous()
    test_diff_content_served_verbatim()
    test_page_ships_diff_mode_sort_toggle_guard()
    test_page_ships_native_fold_button()
    test_page_ships_highlight_cap()
    test_page_ships_mobile_stacked_layout()
    test_page_ships_cross_half_selection_guard()
    test_page_ships_lcs_alignment()
    print("\nAll server diff-render tests passed.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_server_diff_render.py`
Expected: `AssertionError: page missing: function lcsMatches`

- [ ] **Step 3: Write the minimal implementation**

Replace `server.py:1516-1576` (the docblock + `parseHunkRows`'s comment + its body, quoted in full above) with:

```js
// Parse a git hunk's body lines (everything after the @@ header) into a
// flat list of row records — no HTML in this step, see buildSxsTableHtml
// for that. Consecutive removed/added lines buffer and flush through
// alignBlock (LCS-based realignment below) rather than naive index-pairing,
// so an incidentally-unchanged line inside a replace block becomes a 'same'
// record instead of a bogus paired 'change'. Consecutive context lines
// flush as one 'fold' record (with the real ids its rows will get, for
// aria-controls) followed by their 'ctx' records.
// sectionId namespaces those ids — every other generated id in this file is
// namespaced by section/comment id (rdiff-<id>, rthread-<cid>, rcard-<id>,
// ...); without it, every diff section's first fold group would collide on
// the same "sxs-g1-r0", since groupN resets to 0 on each parseHunkRows call.
function parseHunkRows(lines, oldNo, newNo, sectionId) {
  const rows = [];
  let delBuf = [], addBuf = [], ctxBuf = [], groupN = 0;

  function flushChanges() {
    alignBlock(delBuf, addBuf).forEach(r => rows.push(r));
    delBuf = []; addBuf = [];
  }
  function flushContext() {
    if (!ctxBuf.length) return;
    groupN++;
    const gid = 'sxs-' + sectionId + '-g' + groupN;
    const rowIds = ctxBuf.map((_, i) => gid + '-r' + i);
    rows.push({ type: 'fold', gid, count: ctxBuf.length, rowIds });
    ctxBuf.forEach((c, i) => rows.push({
      type: 'ctx', gid, rowId: rowIds[i], oldNo: c.oldNo, newNo: c.newNo, text: c.text,
    }));
    ctxBuf = [];
  }

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    const marker = line.charAt(0);
    if (marker === '\\') continue;   // "\ No newline at end of file" — not a content line
    if (marker === '-') { flushContext(); delBuf.push({ no: oldNo++, text: line.slice(1) }); }
    else if (marker === '+') { flushContext(); addBuf.push({ no: newNo++, text: line.slice(1) }); }
    else {
      flushChanges();
      const text = marker === ' ' ? line.slice(1) : line;
      ctxBuf.push({ oldNo: oldNo++, newNo: newNo++, text });
    }
  }
  flushChanges();
  flushContext();
  return rows;
}

// Standard LCS via dynamic programming — returns an ordered list of
// {oldIdx, newIdx} pairs where oldTexts[oldIdx] === newTexts[newIdx],
// forming the longest common subsequence. O(n*m) time/space; hunk replace
// blocks are hunk-scale (tens of lines at most), so this is more than fast
// enough — no need for a linear-space Myers variant.
function lcsMatches(oldTexts, newTexts) {
  const n = oldTexts.length, m = newTexts.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = oldTexts[i] === newTexts[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const matches = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (oldTexts[i] === newTexts[j]) { matches.push({ oldIdx: i, newIdx: j }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
    else j++;
  }
  return matches;
}

// Aligns one replace block's removed lines (delBuf) against its added lines
// (addBuf) using LCS, instead of pairing purely by buffer index. LCS
// matches (identical text on both sides) become 'same' records — this is
// what stops an incidentally-unchanged line inside a replace block from
// being shown as a bogus paired "change." Everything between matches (a
// "gap," on either or both sides) falls back to positional pairing — the
// same padding-the-shorter-side approach as before, just scoped to the
// smaller gap instead of the whole block.
function alignBlock(delBuf, addBuf) {
  const matches = lcsMatches(delBuf.map(d => d.text), addBuf.map(a => a.text));
  const records = [];
  let oi = 0, ni = 0;
  function flushGap(oldEnd, newEnd) {
    const gapDel = delBuf.slice(oi, oldEnd);
    const gapAdd = addBuf.slice(ni, newEnd);
    const n = Math.max(gapDel.length, gapAdd.length);
    for (let k = 0; k < n; k++) {
      records.push({ type: 'change', del: gapDel[k] || null, add: gapAdd[k] || null });
    }
  }
  matches.forEach(m => {
    flushGap(m.oldIdx, m.newIdx);
    records.push({ type: 'same', del: delBuf[m.oldIdx], add: addBuf[m.newIdx] });
    oi = m.oldIdx + 1; ni = m.newIdx + 1;
  });
  flushGap(delBuf.length, addBuf.length);
  return records;
}
```

Add the `'same'` branch to `buildSxsTableHtml`. Insert it right after the `'change'` branch closes (i.e. right before `} else if (r.type === 'fold') {`), so the branch chain reads `'change'` → `'same'` → `'fold'` → `'ctx'`:

```js
    } else if (r.type === 'same') {
      // An LCS match inside a replace block: identical text on both sides.
      // Rendered like a context row (full width, muted) and never foldable
      // — unlike top-level context runs between blocks, these are rare
      // (one incidentally-unchanged line inside an otherwise-real change)
      // and always small, so fold-state management isn't worth it here.
      html += '<tr class="sxs-row sxs-same-row">'
        + '<td class="sxs-gutter">' + r.del.no + '</td>'
        + '<td class="sxs-gutter">' + r.add.no + '</td>'
        + '<td class="sxs-code sxs-ctx">' + codeEl(r.del.text) + '</td>'
        + '</tr>';
    } else if (r.type === 'fold') {
```

Move the relocated docblock. `server.py:1629` currently reads `function renderDiffTable(target, raw, title, sectionId) {` with no docblock directly above it (that docblock was removed from above `parseHunkRows` in the replacement above — it needs to land here instead). Replace:

```js
function renderDiffTable(target, raw, title, sectionId) {
```

with:

```js
/* Render a git hunk (fenced ```diff block, git's raw +/- /space-prefixed
   text — see parse_diff.py) as a JetBrains-style side-by-side table: removed
   lines left (orange), added lines right (teal), context spanning the full
   width and collapsed behind a fold row. Pure view transform — never reads
   or writes section.content itself, which stays the verbatim fence the
   /viva-diff skill (anchor-based edit relocation) and round-to-round
   carry-forward (byte-for-byte content compare) depend on.
   Returns true on success; on a malformed/unrecognized hunk, defensively
   falls back to the normal renderMarkdown path (and propagates its return
   value, so retry-on-CDN-load bookkeeping in _ensureRendered stays correct). */
function renderDiffTable(target, raw, title, sectionId) {
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_server_diff_render.py`
Expected: all tests print `OK`, ending with `All server diff-render tests passed.`

- [ ] **Step 5: Manual verification — run the real algorithm under Node**

This is required, not optional: the wiring test only proves the functions are shipped, not that the algorithm is correct. Extract the actual shipped script and exercise `alignBlock` directly against three cases.

```bash
cd /Users/bryan/Projects/viva/.claude/worktrees/better-diffs
python3 - <<'PYEOF'
import re
src = open('server.py').read()
m = re.search(r'^HTML = r"""(.*)"""\s*$', src, re.S | re.M)
html = m.group(1)
scripts = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
open('/tmp/lcs_check.js','w').write(scripts[-1])
PYEOF
node --check /tmp/lcs_check.js && echo "SYNTAX OK"
```

Expected: `SYNTAX OK`

```bash
cat > /tmp/lcs_verify.mjs <<'NODEEOF'
import { readFileSync } from 'fs';
const script = readFileSync('/tmp/lcs_check.js', 'utf8');
const start = script.indexOf('function lcsMatches');
const end = script.indexOf('function renderDiffTable');
const block = script.slice(start, end);
const { alignBlock } = new Function(block + '\nreturn { alignBlock };')();

function line(no, text) { return { no, text }; }

// Case A: the bug scenario — an incidentally-identical line sandwiched
// between two real changes.
const a = alignBlock(
  [line(10, 'old function signature'), line(11, 'shared helper call unchanged'), line(12, 'old return statement')],
  [line(10, 'new function signature'), line(11, 'shared helper call unchanged'), line(12, 'new return statement')],
);
console.log('Case A types:', a.map(r => r.type));
console.log('Case A middle is same:', a[1].type === 'same' && a[1].del.text === 'shared helper call unchanged');

// Case B: no common lines at all — must degrade to positional pairing
// (no regression vs. the old naive behavior).
const b = alignBlock(
  [line(1, 'x'), line(2, 'y')],
  [line(1, 'p'), line(2, 'q'), line(3, 'r')],
);
console.log('Case B types:', b.map(r => r.type));
console.log('Case B pairs:', b.map(r => [r.del && r.del.text, r.add && r.add.text]));

// Case C: existing mixed-hunk case from this feature's history — no
// identical lines, must produce the same 2 change records as before.
const c = alignBlock(
  [line(2, '    print("hello", name)')],
  [line(2, '    print("hi", name)'), line(3, '    print("extra line")')],
);
console.log('Case C types:', c.map(r => r.type));
console.log('Case C pairs:', c.map(r => [r.del && r.del.text, r.add && r.add.text]));
NODEEOF
node /tmp/lcs_verify.mjs
rm -f /tmp/lcs_check.js /tmp/lcs_verify.mjs
```

Expected output:
```
Case A types: [ 'change', 'same', 'change' ]
Case A middle is same: true
Case B types: [ 'change', 'change', 'change' ]
Case B pairs: [ [ 'x', 'p' ], [ 'y', 'q' ], [ undefined, 'r' ] ]
Case C types: [ 'change', 'change' ]
Case C pairs: [
  [ '    print("hello", name)', '    print("hi", name)' ],
  [ undefined, '    print("extra line")' ]
]
```

If Case A's middle record is not `'same'`, or Case B/C don't match, do not proceed — the alignment logic has a bug. Re-check `lcsMatches`/`alignBlock` against the code above before continuing.

- [ ] **Step 6: Run the full test suite**

Run: `for f in tests/test_*.py; do python3 "$f" || echo "FAILED: $f"; done`
Expected: no `FAILED` lines (30+ files, all passing).

- [ ] **Step 7: Commit**

```bash
git add server.py tests/test_server_diff_render.py
git commit -m "$(cat <<'EOF'
fix(diff-mode): LCS-based line realignment for replace blocks

Naive index-pairing (delBuf[i] <-> addBuf[i]) within a replace block
misfired when the block wasn't a clean 1:1 line swap -- surfaced in
live review as an incidentally-unchanged line rendering as a bogus
"change" row (identical text shown on both sides). Real LCS alignment
(scoped to each replace block) finds true correspondence; only the
gaps between LCS matches fall back to positional pairing, same as
before but now scoped to what's actually ambiguous instead of the
whole block.

Also relocates a docblock that ended up describing parseHunkRows
after an earlier refactor split renderDiffTable into three functions
-- it documents renderDiffTable's contract and now sits above it again.
EOF
)"
```

Note: this commit will also include the uncommitted blank-half-rendering and fold-row-id-namespacing fixes already sitting in `server.py` from the prior gate-audit pass, since both touch the same functions this task modifies. That's expected — see the plan's context.

---

### Task 2: Shared table scroll + reduced density

**Files:**
- Modify: `server.py:546-559` (`.sxs-table`, `.sxs-code code`, `.sxs-half`)
- Modify: `server.py:586-594` (the mobile `@media (max-width: 720px)` block)
- Test: `tests/test_server_diff_render.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: no new JS interface — pure CSS. Nothing later depends on this task's specifics beyond the visual result.

Current code at `server.py:546-559`:

```css
.sxs-wrap { overflow-x: auto; }
.sxs-table { width: 100%; border-collapse: collapse; font-family: 'Fragment Mono', monospace; font-size: 11px; line-height: 1.55; }
.sxs-hunk-row td.sxs-hunk { color: var(--violet); opacity: 0.7; padding: 3px 9px; white-space: pre; }
.sxs-gutter { width: 3em; text-align: right; padding: 0 6px; color: var(--text3); opacity: 0.6; user-select: none; vertical-align: top; white-space: nowrap; }
.sxs-gutter-del { color: var(--orange); opacity: 0.85; }
.sxs-gutter-add { color: var(--teal); opacity: 0.85; }
.sxs-code { padding: 0; vertical-align: top; }
.sxs-code code { display: block; white-space: pre; padding: 1px 9px; }
.sxs-change-cell { display: flex; }
.sxs-half { flex: 1 1 50%; min-width: 0; overflow-x: auto; }
```

Current code at `server.py:586-594`:

```css
/* Below 720px (this repo's existing mobile breakpoint, see .sheet-frame),
   two 50%-wide code columns get too narrow to read. Stack removed above
   added, full width, and drop each half's own overflow-x so only the outer
   .sxs-wrap scrolls — two independently-scrolling nested regions per row
   made it easy to lose left/right alignment on overflow. */
@media (max-width: 720px) {
  .sxs-change-cell { flex-direction: column; }
  .sxs-half { overflow-x: visible; }
}
```

- [ ] **Step 1: Write the failing test**

Add this test function to `tests/test_server_diff_render.py`, after `test_page_ships_lcs_alignment` (from Task 1) and before `def main()`:

```python
def test_page_ships_shared_table_scroll() -> None:
    """Wiring check only: .sxs-half no longer sets its own overflow-x, so
    the whole table shares one horizontal scroll region (.sxs-wrap) instead
    of each cell scrolling independently. Does not measure rendered layout."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        m = re.search(r"\.sxs-half \{[^}]*\}", page)
        assert m, "page missing: .sxs-half base rule"
        assert "overflow-x" not in m.group(0), \
            f".sxs-half should no longer set its own overflow-x, found: {m.group(0)}"
    print("test_page_ships_shared_table_scroll: OK")
```

Update `main()` to add the call after `test_page_ships_lcs_alignment()`:

```python
def main() -> None:
    test_page_ships_side_by_side_renderer()
    test_page_ships_filepath_helper()
    test_page_ships_file_group_header()
    test_grouped_sections_stay_file_contiguous()
    test_diff_content_served_verbatim()
    test_page_ships_diff_mode_sort_toggle_guard()
    test_page_ships_native_fold_button()
    test_page_ships_highlight_cap()
    test_page_ships_mobile_stacked_layout()
    test_page_ships_cross_half_selection_guard()
    test_page_ships_lcs_alignment()
    test_page_ships_shared_table_scroll()
    print("\nAll server diff-render tests passed.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_server_diff_render.py`
Expected: `AssertionError: .sxs-half should no longer set its own overflow-x, found: .sxs-half { flex: 1 1 50%; min-width: 0; overflow-x: auto; }`

- [ ] **Step 3: Write the minimal implementation**

Replace `server.py:546-555`:

```css
.sxs-wrap { overflow-x: auto; }
.sxs-table { width: 100%; border-collapse: collapse; font-family: 'Fragment Mono', monospace; font-size: 11px; line-height: 1.55; }
.sxs-hunk-row td.sxs-hunk { color: var(--violet); opacity: 0.7; padding: 3px 9px; white-space: pre; }
.sxs-gutter { width: 3em; text-align: right; padding: 0 6px; color: var(--text3); opacity: 0.6; user-select: none; vertical-align: top; white-space: nowrap; }
.sxs-gutter-del { color: var(--orange); opacity: 0.85; }
.sxs-gutter-add { color: var(--teal); opacity: 0.85; }
.sxs-code { padding: 0; vertical-align: top; }
.sxs-code code { display: block; white-space: pre; padding: 1px 9px; }
.sxs-change-cell { display: flex; }
.sxs-half { flex: 1 1 50%; min-width: 0; overflow-x: auto; }
```

with:

```css
.sxs-wrap { overflow-x: auto; }
.sxs-table { width: 100%; border-collapse: collapse; font-family: 'Fragment Mono', monospace; font-size: 12px; line-height: 1.6; }
.sxs-hunk-row td.sxs-hunk { color: var(--violet); opacity: 0.7; padding: 3px 9px; white-space: pre; }
.sxs-gutter { width: 3em; text-align: right; padding: 0 6px; color: var(--text3); opacity: 0.6; user-select: none; vertical-align: top; white-space: nowrap; }
.sxs-gutter-del { color: var(--orange); opacity: 0.85; }
.sxs-gutter-add { color: var(--teal); opacity: 0.85; }
.sxs-code { padding: 0; vertical-align: top; }
.sxs-code code { display: block; white-space: pre; padding: 3px 10px; }
.sxs-change-cell { display: flex; }
/* No min-width:0 / overflow-x:auto here — removing them lets unbreakable
   (white-space:pre) content push the whole table wider than .sxs-wrap
   instead of creating its own nested scrollbar. table-layout:auto (the
   default) means .sxs-table's width:100% is a minimum, not a hard cap, so
   the table is free to grow when content demands it; .sxs-wrap
   (overflow-x:auto) then scrolls old and new together as one unit instead
   of each cell scrolling independently. */
.sxs-half { flex: 1 1 50%; }
```

Replace `server.py:586-594`:

```css
/* Below 720px (this repo's existing mobile breakpoint, see .sheet-frame),
   two 50%-wide code columns get too narrow to read. Stack removed above
   added, full width, and drop each half's own overflow-x so only the outer
   .sxs-wrap scrolls — two independently-scrolling nested regions per row
   made it easy to lose left/right alignment on overflow. */
@media (max-width: 720px) {
  .sxs-change-cell { flex-direction: column; }
  .sxs-half { overflow-x: visible; }
}
```

with:

```css
/* Below 720px (this repo's existing mobile breakpoint, see .sheet-frame),
   two 50%-wide code columns get too narrow to read even sharing one
   scroll region (see .sxs-half above) — stack removed above added, full
   width, instead. */
@media (max-width: 720px) {
  .sxs-change-cell { flex-direction: column; }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_server_diff_render.py`
Expected: all tests print `OK`, ending with `All server diff-render tests passed.`

- [ ] **Step 5: Run the full test suite**

Run: `for f in tests/test_*.py; do python3 "$f" || echo "FAILED: $f"; done`
Expected: no `FAILED` lines.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server_diff_render.py
git commit -m "$(cat <<'EOF'
fix(diff-mode): shared table scroll, reduced density

.sxs-half no longer sets its own overflow-x/min-width, so unbreakable
(white-space:pre) content pushes the whole table wider instead of
creating a nested per-cell scrollbar -- old and new lines now scroll
together as one unit via the outer .sxs-wrap, instead of N independent
scrollbars fighting each other on long lines. Also a modest density
reduction (11px->12px, more cell padding) per live-review feedback.
EOF
)"
```

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** LCS realignment (Task 1) ✓, `'same'` rows never foldable (Task 1's `buildSxsTableHtml` branch has no fold wiring, no `data-group`) ✓, deferred word-level highlighting explicitly not implemented (not present in either task) ✓, shared scroll (Task 2) ✓, density reduction (Task 2) ✓, misplaced docblock relocation (Task 1, bundled since it's in the same functions) ✓.
- **Placeholder scan:** no TBD/TODO; every step shows complete code and exact expected output.
- **Type consistency:** `alignBlock(delBuf, addBuf)` is defined once (Task 1) and called once, from `parseHunkRows`'s `flushChanges()` — same signature throughout. The `'same'` record shape (`{type, del, add}`, both `del`/`add` populated, unlike `'change'` where either may be `null`) is defined in `alignBlock` and consumed identically in `buildSxsTableHtml`'s new branch.
