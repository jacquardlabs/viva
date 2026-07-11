# Design: LCS-Based Block Realignment for `/viva-diff`'s Side-by-Side Table

**Date:** 2026-07-03
**Follow-up to:** #99 (side-by-side hunk rendering), the file-header-grouping follow-up, and the gate-audit fix pass
**Status:** Approved for implementation

---

## Context

`/viva-diff`'s side-by-side hunk table (`server.py`: `parseHunkRows` → `buildSxsTableHtml` → `renderDiffTable`) pairs a hunk's removed and added lines within a "replace block" (a run of consecutive `-` lines followed by consecutive `+` lines) purely by buffer index: `delBuf[i]` next to `addBuf[i]`. Live use surfaced two related problems:

1. **Wrong pairing.** When a replace block isn't a clean 1:1 line-for-line swap — e.g. git's hunk boundaries happen to bundle an incidentally-unchanged line inside the block alongside a real change — index-pairing puts unrelated lines side by side. In one real case, an unchanged comment (`// raw text forever.`) appeared identically on both the removed and added side of a row, because it was positionally paired with itself despite git having classified it as part of the `-`/`+` region rather than context.
2. **Excess visual noise from independent per-cell scrolling and density.** Every `.sxs-half` cell scrolls horizontally on its own (`overflow-x: auto` + `min-width: 0`), so comparing two long lines means scrolling each side separately — the opposite of what a side-by-side view is for. Compounding this, the table renders quite dense (11px monospace, tight padding).

Together these made the table feel, in the reviewer's words, like "still individual line diffs" rather than a real code-block diff (the reference point given was JetBrains' side-by-side diff viewer).

## Approach

git's hunk format already gives correct line-level classification (context/removed/added) — the gap is what happens to a run of `-`/`+` lines *within* one replace block. The fix is the standard technique real diff tools use: run an LCS (longest common subsequence) alignment between the block's old-line-texts and new-line-texts, then apply the existing positional-pairing logic only to what's *left over* between LCS matches, not the whole block.

Concretely, `alignBlock(delBuf, addBuf)`:
1. Compute LCS matches between `delBuf.map(d => d.text)` and `addBuf.map(a => a.text)` via a straightforward O(n·m) dynamic-programming LCS (block sizes are hunk-scale — tens of lines at most — so DP is more than fast enough; no need for a linear-space Myers variant).
2. Walk the matches in order. Between consecutive matches (and before the first / after the last), the old/new sub-arrays that had no match are a "gap" — resolved by the **existing** positional-pairing loop (already correctly full-width for an unpaired side, from the earlier fix), just now scoped to the gap instead of the whole block.
3. Each LCS match itself becomes a new **`same`** row record — rendered exactly like a context row (full-width, `--text2`, both gutters), since the line is genuinely identical between old and new. This is what fixes the duplicated-comment bug: a line the LCS correctly identifies as unchanged no longer gets forced into a bogus split "change" row.

This reuses nearly all of the existing rendering code. `buildSxsTableHtml` gains one new `same` branch (a near-copy of the existing `ctx` branch); `parseHunkRows`'s `flushChanges()` calls `alignBlock` instead of doing naive index-pairing directly.

**Explicitly deferred, not v1:** intra-line/word-level highlighting within a paired-but-different row (highlighting only the specific token that changed, the way JetBrains does within an aligned line pair). That is a second, independent diff pass on top of line alignment. Ship correct line pairing first; revisit word-level highlighting as a separate follow-up if the gap still feels present after this lands.

## Non-algorithmic fixes (bundled in the same pass, unrelated to LCS)

- **Shared scroll, not per-cell scroll.** Remove `min-width: 0` and `overflow-x: auto` from `.sxs-half` (`server.py:555`). Standard `table-layout: auto` semantics mean the table's `width: 100%` acts as a minimum, not a cap — unbreakable (`white-space: pre`) content that can't shrink will push the table wider than its container, and the outer `.sxs-wrap` (already `overflow-x: auto`) becomes the single scroll container for the whole table. Old and new move together instead of N independent scrollbars fighting each other. The now-redundant mobile override at `server.py:593` (`.sxs-half { overflow-x: visible; }`) is removed since there's no more `auto` to override.
- **Less density.** Modest bump: `.sxs-table` font-size 11px→12px, line-height 1.55→1.6, `.sxs-code code` padding 1px 9px→3px 10px. Not a typography overhaul — the complaint was "tiring to read," not "illegible."

## Data flow

```
parseHunkRows(lines, oldNo, newNo, sectionId)
  flushChanges() now calls alignBlock(delBuf, addBuf)
    → lcsMatches(oldTexts, newTexts)          [new, pure function]
    → walks matches, emits 'same' + 'change' records  [alignBlock, new]
  flushContext() unchanged (top-level context-run folding, separate from
  in-block 'same' rows — see Non-goals)
  → returns flat row-record array (now includes 'same' alongside
    'change' | 'fold' | 'ctx')

buildSxsTableHtml(headerText, rows, lang)
  new branch: r.type === 'same' → full-width row, same styling as 'ctx'
  all other branches unchanged
```

## Non-goals

- **`same` rows within a replace block are never folded.** Top-level context runs between blocks stay foldable (unchanged, existing behavior) — but a `same` row discovered by LCS *inside* a block always renders visible. These are rare (only when a block happens to contain an incidental identical line) and typically one line; adding fold-state management for them isn't worth the complexity for v1.
- **No intra-line/word-level diffing** (see Approach — explicitly deferred).
- **No change to hunk-level parsing, binary-file handling, the fold-toggle button, the highlight cap, the mobile column-stacking breakpoint, or the cross-half selection guard** — all of that is orthogonal to line pairing and stays as-is.

## Testing

No JS/browser harness exists in this repo (consistent with every prior test in `tests/test_server_diff_render.py`). Verification follows the pattern used throughout this work: extract the real shipped `<script>` block from the `HTML` constant and execute it under Node against hand-built cases — specifically:
- The exact bug scenario: a replace block containing an incidentally-identical line between two real changes → confirm it renders as one `same` row, not a duplicated `change` row.
- A block with no common lines at all (pure replace) → confirm `alignBlock` degrades to the original positional pairing (no regression).
- The existing mixed / add-only / delete-only / no-newline-at-EOF cases from the prior test suite → confirm unaffected.
- A wiring test in `tests/test_server_diff_render.py` asserting `lcsMatches`/`alignBlock`/the `same`-row CSS class ship in the served page, matching the file's existing wiring-test convention.
