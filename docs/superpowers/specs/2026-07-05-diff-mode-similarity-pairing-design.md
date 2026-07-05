# Design: Similarity-Based Line Pairing for `/viva-diff`'s Replace-Block Gaps

**Date:** 2026-07-05
**Follow-up to:** #99, the file-header-grouping follow-up, the gate-audit fix pass, and the LCS block-realignment work
**Status:** Approved for implementation

---

## Context

The LCS-based block realignment (previous phase) fixed the case where a replace block contains a line that's *textually identical* on both sides — those now correctly become a `'same'` row via exact-match LCS. But live review of this branch's own diff (dogfooding `/viva-diff` against itself) surfaced the remaining gap: when a replace block's old/new lines share **no exact match at all**, everything left over falls into a "gap," which the current code (`alignBlock`'s `flushGap`) pairs purely by buffer position — `gapDel[k]` next to `gapAdd[k]`.

A real hunk demonstrated why this is a problem. `server.py`'s `_ensureRendered` was edited from:
```
  // raw text forever.
  if (!renderMarkdown(contentEl, _pendingMarkdown.get(id))) return;
```
to:
```
  // raw text forever. renderDiffTable propagates the same true/false so this
  // holds for its own defensive renderMarkdown fallback too.
  const rendered = isDiffHunk ? renderDiffTable(contentEl, raw, sectionTitleFor(id), id) : renderMarkdown(contentEl, raw);
  if (!rendered) return;
```
2 old lines, 4 new lines, nothing textually identical. Positional pairing put the real `if (!renderMarkdown(...))` call next to the unrelated `// holds for its own...` comment — landing them together purely because they shared a buffer index, not because they correspond to each other in any real sense. Intra-line/word-level highlighting (a separately-deferred feature) would not have fixed this: it only refines an *already-decided* pair, and doesn't address which lines get paired with which in the first place.

## Approach

Replace positional gap-filling with a proper similarity-based global alignment — a weighted sequence-alignment DP (in the spirit of Needleman-Wunsch), not a greedy heuristic, so an early locally-good match can never lock in a worse pairing later in the same gap.

### Similarity metric

Word/token overlap (Jaccard), not character-level LCS:
- Tokenize a line: `line.match(/[A-Za-z0-9_]+/g) || []`, deduplicated into a `Set`.
- `similarity(a, b) = |tokensA ∩ tokensB| / |tokensA ∪ tokensB|`, in `[0, 1]`. If both token sets are empty (e.g. two blank/punctuation-only lines), define similarity as `1`.

Chosen over character-level LCS (which could reuse the existing `lcsMatches`) because it's cheaper (word counts, not character counts, so no `O(len_a·len_b)` per pair) and tracks "basically the same statement, changed a bit" more intuitively for code and prose — a renamed identifier or a changed argument still shares most of the surrounding words.

### Alignment DP

Given a gap's old lines (`gapDel`, length `M`) and new lines (`gapAdd`, length `N`), build an `(M+1)×(N+1)` table where `score[i][j]` is the best total similarity achievable aligning the first `i` old lines against the first `j` new lines:

```
score[0][j] = 0  for all j
score[i][0] = 0  for all i
score[i][j] = max(
  (sim(gapDel[i-1], gapAdd[j-1]) >= SIMILARITY_THRESHOLD)
    ? score[i-1][j-1] + sim(gapDel[i-1], gapAdd[j-1])
    : -Infinity,                                          // pair i-1 with j-1
  score[i-1][j],                                           // leave gapDel[i-1] unpaired
  score[i][j-1],                                           // leave gapAdd[j-1] unpaired
)
```

The threshold gate is what does the real work: a pairing that doesn't clear `SIMILARITY_THRESHOLD` is disallowed as an option in the DP entirely, so the algorithm is *forced* to treat clearly-unrelated lines as unpaired rather than being tempted into a low-quality match by the scoring alone. `SIMILARITY_THRESHOLD = 0.2` (a named constant, same convention as `HLJS_HIGHLIGHT_CAP`) — chosen from hand-tracing the motivating case: the two real pairs there score ~0.25–0.27, the rejected pairing scores ~0.07, so 0.2 cleanly separates them with margin on both sides.

Standard DP traceback from `score[M][N]` recovers the actual decision sequence in order, which is what guarantees no crossing pairs (a pairing that would show new-line 3 before new-line 1's own partner, when old-line ordering says otherwise, is structurally impossible — traceback only ever moves `(i,j)` down/left/diagonally-down-left, never re-visiting an index).

**Hand-traced against the motivating case, to confirm the approach actually fixes it:** the two comment lines (`// raw text forever.` / `// raw text forever. renderDiffTable propagates...`) score ~0.27 and pair. The two `if (...) return;` lines (`if (!renderMarkdown(contentEl, _pendingMarkdown.get(id))) return;` / `if (!rendered) return;`) score ~0.25 and pair — even though they are not adjacent in raw diff order (the new `if` is the 4th added line, not the 2nd), which the DP handles correctly since it aligns by best-total-similarity, not position. The old `if` line scores only ~0.07 against the unrelated `// holds for its own...` comment — well below threshold, so they're correctly kept apart, each rendering as its own row (the old `if` paired with the new `if`; the two leftover new lines — the second comment and the `const rendered = ...` line — render unpaired/full-width, correctly interspersed between the two real pairs).

## Integration

Only `alignBlock`'s `flushGap` closure changes — the naive `for` loop pairing `gapDel[k]`/`gapAdd[k]` by index is replaced with a call to the new aligner. Everything else is unchanged:
- The exact-match LCS pass (`lcsMatches`, whole-line) still runs first and is still authoritative — a 100%-confidence identical-line match is strictly better than any similarity score, so it's never second-guessed by the new logic.
- `buildSxsTableHtml`'s `'change'` branch already renders a record with either `del` or `add` as `null` full-width (from the earlier blank-half fix) — the new aligner just needs to produce records in that same `{type: 'change', del, add}` shape; no rendering changes needed.

## Non-goals

- **No intra-line/word-level highlighting.** Still a separate, still-reasonable-to-defer feature — this fix addresses the actual root cause (wrong pairing) directly, without it.
- **No change to** the exact-match LCS pass, context folding, the highlight cap, the mobile CSS, or the cross-half selection guard.

## Testing

Same approach used throughout this branch: extract the real shipped `<script>` and execute it under Node against hand-built cases, specifically:
- **The exact motivating case** (2 old lines, 4 new lines from the real hunk above) → confirm the comment lines pair together, the `if` lines pair together, and the two leftover new lines render unpaired — not the original nonsensical positional pairing.
- **A block where nothing is similar enough to pair** (e.g. totally unrelated lines on both sides) → confirm everything renders unpaired (all `del`-only then/interspersed-with all `add`-only records — order matches how the DP's base cases resolve when no pairing ever clears threshold), i.e. no regression to a forced low-quality pairing.
- **A block that's a clean 1:1 rewrite with high similarity on every line** → confirm it still pairs every line 1:1, matching prior (both naive-positional and now similarity-driven) behavior when position and similarity agree.
- **The existing mixed/add-only/delete-only/no-newline-at-EOF cases** from prior phases → confirm unaffected (pure-add or pure-delete blocks have an empty gap on one side, so the DP trivially leaves everything on the other side unpaired — same output as today).
- A wiring test in `tests/test_server_diff_render.py` asserting the new aligner function(s) and `SIMILARITY_THRESHOLD` ship in the served page, matching this file's existing wiring-test convention.
