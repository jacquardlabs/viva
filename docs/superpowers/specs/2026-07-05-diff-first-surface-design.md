# Design: Diff-First Surface for `/viva-diff` — Wide Layout + Delegated Rendering

**Date:** 2026-07-05
**Follow-up to:** #99 and every rendering phase since (file grouping, gate-audit fixes, LCS realignment, similarity pairing)
**Supersedes:** the viewport-breakout design (`2026-07-05-diff-mode-viewport-breakout-design.md`, dropped unmerged — see Rejected Approach below)
**Status:** Approved for implementation

---

## Context

`/viva-diff` is two things fused together. The **review loop** — `parse_diff` → per-hunk verdicts → anchored comments → agent revises the working tree → carry-forward → ledger — is the product: novel, stable, and proven (dogfooding it against this very branch surfaced five real bugs). The **diff renderer** is a commodity with decades of prior art, and five design cycles of rebuilding it by hand (blank-half fix, fold-id collisions, CSS specificity bleed, LCS realignment, similarity-based gap pairing, density/scroll fixes) each exposed the next constraint rather than converging. The constraints trace to one root: the renderer lives inside a shell architecturally designed for the opposite artifact — a 700px centered prose column, a card accordion whose collapse animation requires `overflow: hidden`, and a 60vh nested scroll cap meant for long markdown documents.

This design ends the fight on both fronts at once: the shell gets a diff-mode-scoped restyle (so nothing needs to escape anything), and the rendering is delegated to a mature library (so alignment, word-level highlighting, and responsive layout stop being our code).

## Rejected approach (recorded so the lesson survives)

The immediately-prior attempt — a CSS "breakout" (`width: 100vw` + `margin: calc(50% - 50vw)`) to let the table escape the 700px shell — is structurally impossible in this DOM. Two pre-existing ancestors clip it, and each alone is decisive:

- `.card-body-inner { overflow: hidden }` — not incidental; it is the mechanism that makes every card's expand/collapse accordion animation (`grid-template-rows: 0fr → 1fr`) clip correctly, shared by all three modes.
- `.section-content { max-height: 60vh; overflow-y: auto }` — per the CSS spec, a non-`visible` overflow on one axis forces the other axis's computed value to `auto`, so this element clips/scrolls horizontally too.

A normal-flow child cannot paint outside an `overflow: hidden` ancestor. Any future "escape the card" idea must clear this analysis first. The insight that unblocks everything: **widen the container instead of escaping it** — a wide shell makes the clipping ancestors harmless without touching the accordion.

## Part 1 — Layout: restyle, not rebuild

A parallel diff-mode DOM tree would orphan the verdict/comment/keyboard/SSE machinery, all keyed to card element ids (`rcard-`, `rbody-`, `rcontent-`, …). Instead, diff mode keeps the existing card DOM and gets mode-scoped CSS:

- The top-level dispatch's existing `data.mode === 'diff'` branch adds `mode-diff` to `<body>` (one line of JS — the explicit mode hook the CSS needs).
- `.mode-diff .shell, .mode-diff .bottom-inner { max-width: min(95vw, 1600px); }` — the whole card widens; the diff table widens with it; the accordion animation is untouched because nothing escapes it.
- `.mode-diff .section-content { max-height: none; overflow-y: visible; }` — removes the nested scroll for hunks (a git hunk with context folding doesn't need the 60vh cap that an arbitrary long document does). Page scroll becomes the only vertical scroll; the triple-scrollbar effect dies.
- Review and QA modes: zero change — no `.mode-diff` class, no new rules apply.

## Part 2 — Rendering: diff2html, delete the hand-rolled renderer

[diff2html](https://github.com/rtfpessoa/diff2html) (MIT) — verified against its docs: accepts raw unified-diff text, renders line-by-line and side-by-side, supports `diffStyle: 'word'` intra-line highlighting (the JetBrains feature deferred twice), integrates highlight.js, and ships browser bundles on jsdelivr with no build step. Same dependency precedent as marked/DOMPurify/hljs, which already load from jsdelivr with graceful degradation.

**Adapter (~30 lines, replacing several hundred):**
- `section.content` stays byte-for-byte untouched on disk and over `/input` — the anchor (`{text, offset}`) and carry-forward (byte-compare) contracts are unaffected. At render time only, strip the ` ```diff ` fence and synthesize the minimal `--- a/<path>` / `+++ b/<path>` preamble diff2html expects, with `<path>` from `filepathFromTitle(title)` (presentation-only; never stored).
- Output passes through `DOMPurify.sanitize` before `innerHTML` — consistent with `renderMarkdown`, and it closes the standing audit note that the hand-built diff HTML skipped DOMPurify.
- Config: `outputFormat` picked at render time by viewport (`side-by-side` at ≥900px `innerWidth`, else `line-by-line` — replaces the hand-rolled mobile stacking), `diffStyle: 'word'`, `matching: 'words'`, `drawFileList: false`, highlight enabled via the UI bundle's hljs wiring.
- Fallback chain when the CDN is absent: `renderMarkdown`'s fenced ` ```diff ` path (the pre-#99 view) → `md-raw` plain text. The existing no-op-when-absent pattern; no dead renderer kept as backup.

**Deleted outright** (plus their wiring tests, replaced by new ones): `parseHunkRows`, `lcsMatches`, `alignBlock`, `alignGap`, `wordTokens`, `jaccardSimilarity`, `SIMILARITY_THRESHOLD`, `buildSxsTableHtml`, `renderDiffTable`'s table construction, `toggleFold`, `HLJS_HIGHLIGHT_CAP`, all `.sxs-*` CSS. The LCS/similarity work was correct — but correct at the wrong altitude; diff2html owns that layer now.

## Lessons ported to the new DOM (shipped in the same change, not rediscovered later)

- **Specificity bleed:** the generic `.section-content td { border-bottom; padding }` rule (for ordinary markdown tables) will hit diff2html's tables exactly as it hit ours. A scoped reset for `.section-content` descendants of the diff2html container ships with the integration.
- **Anchor hygiene:** diff2html's line-number cells get `user-select: none` so selections can't capture line numbers into `comment.anchor.text`; the cross-column selection guard (formerly `closestSxsHalf` over `.sxs-half`) is ported to diff2html's side-by-side pane containers, degrading a cross-pane selection to an unanchored whole-section note as today.

## Kept invariants

One hunk per card (the unit of trust), file-group headers, verdict derivation, comment threads/attachments, carry-forward, the ledger, SSE, `/input` serving `content` verbatim — all untouched. No schema change, no new endpoint, stdlib-only server.

## Testing

- Wiring tests (subprocess + urllib, per repo convention): `mode-diff` hook present in the diff dispatch branch; diff2html script/CSS tags present; adapter function present with fence-strip + preamble synthesis; fallback chain present; deleted symbols absent from the served page.
- Dev-only (never committed, repo stays stdlib-only): attempt Playwright headless screenshots so visual claims can be self-verified before the human looks; if the sandbox blocks it, one consolidated human visual checkpoint at the end.
- DESIGN.md's side-by-side/`.sxs-*` sections are replaced to describe the diff2html-based surface and the `mode-diff` layout.
