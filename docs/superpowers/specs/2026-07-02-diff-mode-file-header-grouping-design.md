# Design: File-Header Grouping for `/viva-diff`

**Date:** 2026-07-02
**Follow-up to:** #99 (side-by-side hunk rendering)
**Status:** Approved for implementation

---

## Context

`/viva-diff` renders one review card per git hunk (`scripts/parse_diff.py` emits
one `ReviewSection` per hunk, titled `"{filepath} hunk N"`). `initReview()`
(`server.py`) appends these cards into `#review-cards` in document order, which
already happens to be file-then-hunk order — `parse_diff.py` splits the patch
file-by-file and emits all of a file's hunks contiguously before moving to the
next file. But nothing in the UI currently surfaces that grouping: a file with
five hunks looks identical to five unrelated cards from five different files,
each card's title the only clue.

Goal: add a lightweight visual landmark — a static divider row above each run
of hunks belonging to the same file — so a reviewer can tell at a glance which
file they're in and how many hunks it has, without changing any interaction
model.

---

## Approach

Pure client-side rendering addition inside `initReview()`, scoped to
`REVIEW_DATA.mode === 'diff'`. Review mode's `initReview()` path — sections are
markdown headings within one document, not files — is untouched.

**Why this is safe to bolt on with zero interaction risk**, confirmed during
design:
- **No schema change.** The filepath is already fully recoverable from
  `section.title` (`"{filepath} hunk N"`); no new field, no coordinated edit
  across `parse_diff.py`/`schema.py`/the server.
- **No confidence-sort conflict.** The card-sort toggle (`setupCardSort` /
  `applyCardSort`) re-orders cards via CSS `order`, driven by
  `section.annotations` confidence data — but `parse_diff.py` never populates
  `annotations` and the `/viva-diff` skill never runs the `annotate.py`
  producer. `hasConfidence` is always false in diff mode, so the sort toggle
  stays permanently hidden there and cards never reflow. A static divider
  between cards can never end up next to the wrong file.
- **No keyboard/approve-flow coupling.** Card advance (`activateReviewCard`,
  the "next pending" lookups in `skipReviewCard`/`approveSection`) all resolve
  the next card by walking `REVIEW_DATA.sections` and looking up `#rcard-<id>`
  by id — never by DOM sibling traversal. Inserting extra non-`.card` sibling
  elements into `#review-cards` has no effect on that logic.

## Implementation

**1. Extract shared filepath parsing.** `langFromTitle` (`server.py`, added for
#99) already strips `" hunk N"` off a diff-mode section title to recover the
filepath before looking up its extension. Pull that stripping into its own
`filepathFromTitle(title)` helper; `langFromTitle` calls it instead of
duplicating the regex.

```js
function filepathFromTitle(title) {
  return String(title || '').replace(/\s+hunk\s+\d+$/, '');
}
```

**2. Group during the existing render loop.** In `initReview()`
(`server.py:1596`), the `REVIEW_DATA.sections.forEach` loop that builds and
appends each card gains one check, gated on diff mode: before appending a
card, if this section's filepath differs from the previous section's (or it's
the first section), insert a `.file-group-header` div ahead of it. The hunk
count for that header is the length of the contiguous run of sections sharing
that filepath — cheap to precompute in one pass over `REVIEW_DATA.sections`
before the render loop, since same-file hunks are always contiguous (a
property of how `parse_diff.py` builds `sections`).

```js
'<div class="file-group-header">' + esc(filepath) + ' · ' + esc(n) +
  ' hunk' + (n === 1 ? '' : 's') + '</div>'
```

**3. Styling.** Small mono-uppercase divider matching the existing
`.sxs-fold-cell` / `.diff-toggle` typographic language (9px, letter-spacing,
`--text2`) — a landmark, not a heading; it should read as quieter than a card
title, not compete with it.

## Non-goals (explicitly out of scope, per this round of design)

- No sticky/pinned header while scrolling.
- No collapse/expand of a file's hunks as a group.
- No live "N/M approved" status in the header — it is a static label computed
  once at render time, not re-rendered on verdict changes.
- No per-file line-count stats (`+12 -4`) — filepath + hunk count only.

## Testing

Extend `tests/test_server_diff_render.py` (added for #99) with a case
asserting the served page's HTML embeds `filepathFromTitle` and
`file-group-header` — a wiring check consistent with the existing tests in
that file (no browser/JS test harness in this repo, so structural/string
assertions on the served page are the ceiling for automated coverage here).
Manual verification: boot a `--mode diff` session against a real multi-file,
multi-hunk-per-file patch and confirm in a browser that each file gets exactly
one header, the count is correct, and headers don't appear when a file has
only one hunk that happens to be the first hunk overall (edge case: first
section always gets a header regardless of run length).
