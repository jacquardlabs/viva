# Diff-Mode Similarity-Based Gap Pairing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/viva-diff`'s positional-fallback pairing (used within a replace-block "gap" — the portion where exact-match LCS finds nothing identical) with a real similarity-based global alignment, so unrelated lines that happen to share a buffer index no longer get shown side by side as if they corresponded.

**Architecture:** A weighted global-alignment DP (Needleman-Wunsch-style) over word/token-overlap (Jaccard) similarity between candidate line pairs. Only `alignBlock`'s `flushGap` closure changes — the naive positional loop is replaced with a call to the new `alignGap`. Everything upstream (the exact-match LCS pass) and downstream (`buildSxsTableHtml`'s rendering) is unchanged.

**Tech Stack:** Vanilla JS embedded in `server.py`'s `HTML` constant (no build step, no npm). Python stdlib test harness (`tests/_server_harness.py`).

## Global Constraints

- Every source file this repo ships stays stdlib-only — no npm, no build step (`CLAUDE.md`).
- Tests are plain `python3 tests/test_*.py` scripts with a `main()` printing an `OK`/pass summary — no pytest, no Node dependency in the committed test suite. Deeper JS behavioral verification happens by extracting the real shipped `<script>` and running it under Node as a manual verification step, not a checked-in test (established pattern throughout `tests/test_server_diff_render.py`'s history).
- Approved spec: `docs/superpowers/specs/2026-07-05-diff-mode-similarity-pairing-design.md`. Exact values from the spec, do not deviate: tokenizer regex `/[A-Za-z0-9_]+/g`; similarity = Jaccard (`shared / union`, `1` if both token sets are empty); `SIMILARITY_THRESHOLD = 0.2`; a pairing below threshold is disallowed as a DP option entirely (not merely deprioritized).
- Explicitly out of scope for this plan (do NOT implement): intra-line/word-level highlighting; any change to the exact-match LCS pass (`lcsMatches`), context folding, the highlight cap, the mobile CSS, or the cross-half selection guard.

---

### Task 1: Similarity-based gap alignment

**Files:**
- Modify: `server.py:1605-1632` (`alignBlock`'s docblock and its `flushGap` closure)
- Test: `tests/test_server_diff_render.py`

**Interfaces:**
- Produces: `wordTokens(line) -> Set<string>` — pure function, tokenizes one line.
- Produces: `jaccardSimilarity(lineA, lineB) -> number` (in `[0, 1]`) — pure function, calls `wordTokens` internally.
- Produces: `const SIMILARITY_THRESHOLD = 0.2` — module-level constant.
- Produces: `alignGap(gapDel, gapAdd) -> Array<{type: 'change', del, add}>` — consumed only by `alignBlock`'s `flushGap` in this task; `gapDel`/`gapAdd` are arrays of `{no: number, text: string}`, the same shape `parseHunkRows` already builds. Returns records in the exact same shape `buildSxsTableHtml`'s existing `'change'` branch already handles (either `del` or `add` may be `null`, never both).
- Consumes: nothing new from other tasks.

Current code at `server.py:1605-1632`:

```js
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

- [ ] **Step 1: Write the failing test**

Open `tests/test_server_diff_render.py`. Add this test function anywhere after `test_page_ships_cross_half_selection_guard` (before `def main()`):

```python
def test_page_ships_similarity_alignment() -> None:
    """Wiring check only: the served page ships wordTokens, jaccardSimilarity,
    SIMILARITY_THRESHOLD, and alignGap, and alignBlock's flushGap actually
    calls alignGap (not the old positional loop). Does not execute the DP
    against a real gap — see the plan's manual Node verification step for
    behavioral proof."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        for needle in (
            "function wordTokens",
            "function jaccardSimilarity",
            "const SIMILARITY_THRESHOLD = 0.2",
            "function alignGap",
        ):
            assert needle in page, f"page missing: {needle}"
        m = re.search(r"function alignBlock\(.*?\n\}", page, re.S)
        assert m, "page missing: function alignBlock"
        assert "alignGap(" in m.group(0), "alignBlock's flushGap does not call alignGap"
    print("test_page_ships_similarity_alignment: OK")
```

Update `main()` to call it (add the line after `test_page_ships_lcs_alignment()` and before `test_page_ships_shared_table_scroll()`, or anywhere after the existing diff-render tests and before the final print — exact position doesn't matter, just add it):

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
    test_page_ships_lcs_alignment()
    test_page_ships_shared_table_scroll()
    test_page_ships_cross_half_selection_guard()
    test_page_ships_similarity_alignment()
    print("\nAll server diff-render tests passed.")
```

(If the exact test names/order above don't match what's currently in the file, just add `test_page_ships_similarity_alignment()` as a new line before the final `print(...)` call — don't remove or reorder any existing calls.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_server_diff_render.py`
Expected: `AssertionError: page missing: function wordTokens`

- [ ] **Step 3: Write the minimal implementation**

Replace `server.py:1605-1632` (quoted in full above) with:

```js
// Word tokens in a line, deduplicated — the unit jaccardSimilarity compares.
// Regex-based (not split on whitespace) so punctuation between identifiers
// doesn't get swallowed into a token.
function wordTokens(line) {
  return new Set(line.match(/[A-Za-z0-9_]+/g) || []);
}

// Jaccard similarity between two lines' token sets: shared / union, in
// [0, 1]. Two token-less lines (blank or punctuation-only) are defined as
// maximally similar (both are "nothing"), rather than dividing by zero.
function jaccardSimilarity(lineA, lineB) {
  const a = wordTokens(lineA), b = wordTokens(lineB);
  if (a.size === 0 && b.size === 0) return 1;
  let shared = 0;
  a.forEach(t => { if (b.has(t)) shared++; });
  return shared / (a.size + b.size - shared);
}

// Above this score, two lines in a gap (see alignGap) are considered the
// same statement with a tweak, not two unrelated lines that happen to
// share a buffer index. Picked from a real hunk that motivated this: two
// genuine line rewrites there scored ~0.25-0.27 similarity, while a
// rejected pairing (an `if` statement vs. an unrelated comment) scored
// ~0.07 — 0.2 cleanly separates them with margin on both sides.
const SIMILARITY_THRESHOLD = 0.2;

// Aligns a replace-block "gap" (old/new lines with no exact LCS match)
// using a weighted global-alignment DP over line similarity, instead of
// pairing purely by buffer position. score[i][j] is the best total
// similarity aligning the first i old lines against the first j new
// lines; a pairing that doesn't clear SIMILARITY_THRESHOLD is disallowed
// as a DP option entirely (not merely deprioritized), so two unrelated
// lines are forced apart rather than tempted into a low-quality match.
// Standard traceback recovers the decision sequence in order, so pairs
// never cross (old line N can never pair with a new line that comes
// before old line N-1's own partner).
function alignGap(gapDel, gapAdd) {
  const M = gapDel.length, N = gapAdd.length;
  const score = Array.from({ length: M + 1 }, () => new Array(N + 1).fill(0));
  for (let i = 1; i <= M; i++) {
    for (let j = 1; j <= N; j++) {
      const sim = jaccardSimilarity(gapDel[i - 1].text, gapAdd[j - 1].text);
      const pairScore = sim >= SIMILARITY_THRESHOLD ? score[i - 1][j - 1] + sim : -Infinity;
      score[i][j] = Math.max(pairScore, score[i - 1][j], score[i][j - 1]);
    }
  }
  const records = [];
  let i = M, j = N;
  while (i > 0 || j > 0) {
    const sim = i > 0 && j > 0 ? jaccardSimilarity(gapDel[i - 1].text, gapAdd[j - 1].text) : -1;
    const pairScore = i > 0 && j > 0 && sim >= SIMILARITY_THRESHOLD ? score[i - 1][j - 1] + sim : -Infinity;
    if (i > 0 && j > 0 && score[i][j] === pairScore) {
      records.unshift({ type: 'change', del: gapDel[i - 1], add: gapAdd[j - 1] });
      i--; j--;
    } else if (i > 0 && score[i][j] === score[i - 1][j]) {
      records.unshift({ type: 'change', del: gapDel[i - 1], add: null });
      i--;
    } else {
      records.unshift({ type: 'change', del: null, add: gapAdd[j - 1] });
      j--;
    }
  }
  return records;
}

// Aligns one replace block's removed lines (delBuf) against its added lines
// (addBuf). First, LCS matches (identical text on both sides) become 'same'
// records — this stops an incidentally-unchanged line inside a replace
// block from being shown as a bogus paired "change." Everything between
// matches (a "gap," on either or both sides) is then resolved by alignGap's
// similarity-based alignment, not by buffer position — this stops an
// unrelated old/new line pair that only shares a buffer index from being
// shown as if they corresponded.
function alignBlock(delBuf, addBuf) {
  const matches = lcsMatches(delBuf.map(d => d.text), addBuf.map(a => a.text));
  const records = [];
  let oi = 0, ni = 0;
  function flushGap(oldEnd, newEnd) {
    const gapDel = delBuf.slice(oi, oldEnd);
    const gapAdd = addBuf.slice(ni, newEnd);
    alignGap(gapDel, gapAdd).forEach(r => records.push(r));
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

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_server_diff_render.py`
Expected: all tests print `OK`, ending with `All server diff-render tests passed.`

- [ ] **Step 5: Manual verification — run the real algorithm under Node**

This is required, not optional: the wiring test only proves the functions are shipped, not that the alignment is correct.

```bash
cd /Users/bryan/Projects/viva/.claude/worktrees/better-diffs
python3 - <<'PYEOF'
import re
src = open('server.py').read()
m = re.search(r'^HTML = r"""(.*)"""\s*$', src, re.S | re.M)
html = m.group(1)
scripts = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
open('/tmp/sim_check.js','w').write(scripts[-1])
PYEOF
node --check /tmp/sim_check.js && echo "SYNTAX OK"
```

Expected: `SYNTAX OK`

```bash
cat > /tmp/sim_verify.mjs <<'NODEEOF'
import { readFileSync } from 'fs';
const script = readFileSync('/tmp/sim_check.js', 'utf8');
const start = script.indexOf('function wordTokens');
const end = script.indexOf('function buildSxsTableHtml');
const block = script.slice(start, end);
const { alignGap } = new Function(block + '\nreturn { alignGap };')();

function line(no, text) { return { no, text }; }

// Case A: the real motivating hunk — 2 removed lines, 4 added, nothing
// identical. Expected: the two comment lines pair, the two `if` lines
// pair (even though not adjacent in raw order), the two leftover added
// lines render unpaired.
const a = alignGap(
  [
    line(1, '// raw text forever.'),
    line(2, 'if (!renderMarkdown(contentEl, _pendingMarkdown.get(id))) return;'),
  ],
  [
    line(1, '// raw text forever. renderDiffTable propagates the same true/false so this'),
    line(2, '// holds for its own defensive renderMarkdown fallback too.'),
    line(3, 'const rendered = isDiffHunk ? renderDiffTable(contentEl, raw, sectionTitleFor(id), id) : renderMarkdown(contentEl, raw);'),
    line(4, 'if (!rendered) return;'),
  ],
);
console.log('Case A:', JSON.stringify(a.map(r => [r.del && r.del.text, r.add && r.add.text])));

// Case B: nothing similar at all — everything must render unpaired.
const b = alignGap(
  [line(1, 'xxx yyy zzz'), line(2, 'aaa bbb ccc')],
  [line(1, 'mmm nnn ooo'), line(2, 'ppp qqq rrr')],
);
console.log('Case B:', JSON.stringify(b.map(r => [r.del && r.del.text, r.add && r.add.text])));

// Case C: a clean 1:1 high-similarity rewrite — must still pair every
// line 1:1.
const c = alignGap(
  [line(1, 'const x = compute(a, b, c);'), line(2, 'return x + offset;')],
  [line(1, 'const x = compute(a, b, c, d);'), line(2, 'return x + offset + 1;')],
);
console.log('Case C:', JSON.stringify(c.map(r => [r.del && r.del.text, r.add && r.add.text])));

// Case D: pure one-sided gap (no old lines at all) — degenerate case,
// must render every new line unpaired with no crash.
const d = alignGap([], [line(1, 'brand new line')]);
console.log('Case D:', JSON.stringify(d.map(r => [r.del && r.del.text, r.add && r.add.text])));
NODEEOF
node /tmp/sim_verify.mjs
rm -f /tmp/sim_check.js /tmp/sim_verify.mjs
```

Expected output:
```
Case A: [["// raw text forever.","// raw text forever. renderDiffTable propagates the same true/false so this"],[null,"// holds for its own defensive renderMarkdown fallback too."],[null,"const rendered = isDiffHunk ? renderDiffTable(contentEl, raw, sectionTitleFor(id), id) : renderMarkdown(contentEl, raw);"],["if (!renderMarkdown(contentEl, _pendingMarkdown.get(id))) return;","if (!rendered) return;"]]
Case B: [[null,"mmm nnn ooo"],[null,"ppp qqq rrr"],["xxx yyy zzz",null],["aaa bbb ccc",null]]
Case C: [["const x = compute(a, b, c);","const x = compute(a, b, c, d);"],["return x + offset;","return x + offset + 1;"]]
Case D: [[null,"brand new line"]]
```

If Case A's output doesn't match — specifically, if the `if` lines don't pair with each other, or the comment lines don't pair with each other, or anything pairs with a similarity below 0.2 — do not proceed. Re-check `jaccardSimilarity`/`alignGap` against the code above before continuing.

- [ ] **Step 6: Run the full test suite**

Run: `for f in tests/test_*.py; do python3 "$f" || echo "FAILED: $f"; done`
Expected: no `FAILED` lines (30+ files, all passing).

- [ ] **Step 7: Commit**

```bash
git add server.py tests/test_server_diff_render.py
git commit -m "$(cat <<'EOF'
fix(diff-mode): similarity-based alignment for replace-block gaps

The LCS realignment fixed identical-line pairing within a replace
block, but a gap where nothing is textually identical still fell back
to pairing by buffer position -- surfaced in live review as a real
`if` statement landing next to an unrelated comment purely because
they shared a row index.

alignGap replaces that fallback with a weighted global-alignment DP
(Needleman-Wunsch-style) over word/token-overlap (Jaccard) similarity.
A pairing below SIMILARITY_THRESHOLD (0.2) is disallowed as a DP
option entirely, not merely deprioritized, so unrelated lines are
forced apart rather than tempted into a low-quality match. Standard
traceback guarantees no crossing pairs.

Also includes an unrelated, already-uncommitted CSS fix from a prior
session: .sxs-gutter/.sxs-code/.sxs-fold-cell/.sxs-hunk-row td.sxs-hunk
lacked enough specificity to override a pre-existing generic
".section-content td" rule (meant for ordinary markdown tables), which
was silently forcing a border-bottom and inflated padding onto every
row of the diff table.
EOF
)"
```

Note: this commit will also include the uncommitted CSS specificity fix already sitting in `server.py` from a prior session, since it's in the same file and this task's own commit is the next natural checkpoint. That's expected — see the plan's context.

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** tokenizer regex ✓ (exact `/[A-Za-z0-9_]+/g`), Jaccard with empty-set special case ✓, `SIMILARITY_THRESHOLD = 0.2` as a named constant ✓, threshold gates DP options entirely (not just deprioritizes) ✓, global DP alignment with correct traceback (no greedy, no crossing pairs) ✓, integration only touches `alignBlock`'s `flushGap` ✓, `buildSxsTableHtml` untouched (record shape unchanged) ✓, non-goals (no intra-line highlighting, no touching the LCS pass/folding/highlight-cap/mobile-CSS/selection-guard) — none of those are touched by this task's diff ✓.
- **Placeholder scan:** no TBD/TODO; every step shows complete code and exact expected output (all four Node verification cases computed by running the real algorithm, not hand-estimated).
- **Type consistency:** `alignGap(gapDel, gapAdd)` is defined once and called once, from `alignBlock`'s `flushGap` — same signature and return shape (`{type: 'change', del, add}`, matching what `buildSxsTableHtml`'s existing `'change'` branch already consumes) throughout.
