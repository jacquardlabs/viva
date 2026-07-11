# Doc-identifying browser tab titles

## Problem

viva's browser tab title is a static or near-static string (`viva · review ·
REV 02`, `viva · brainstorm`) that never names the document under review.
When a reviewer has multiple viva sessions open at once — reviewing several
docs in parallel, or a review session alongside a Q&A session — every tab
looks identical until clicked. There's no way to tell them apart from the tab
bar alone.

## Goal

Make the tab title identify *which* document (or Q&A topic) is under review,
so multiple concurrent viva sessions are distinguishable at a glance, even
when tab width truncates the title.

## Approach

All the data needed already reaches the client — no schema change, no script
change, no new state. `doc_file` (relative path, e.g. `docs/PRODUCT.md`)
is present in every review and diff round payload (`scripts/parse_sections.py`,
`scripts/parse_diff.py`, `scripts/schema.py`'s `ReviewInput`) and on every SSE
`round` event. Q&A payloads already carry `context` (the topic string). The
fix is contained entirely to `server.py`'s embedded `HTML` constant (the
frontend), specifically the four `document.title = ...` call sites.

**Title composition**: doc name leads (survives truncation when many tabs are
open), app name (`viva`) trails as a fixed suffix. Parts join with ` · ` and
empty parts are dropped — no dangling separators.

| State | Title |
|---|---|
| Review round | `PRODUCT.md · REV 02 · viva` |
| Diff round | `PRODUCT.md · diff · REV 02 · viva` |
| Q&A | `auth flow ideas · viva` |
| Q&A, no context | `brainstorm · viva` |
| Review/diff complete | `PRODUCT.md · done · viva` |
| Q&A complete | `done · viva` |
| Pre-load (static HTML, before `/input` resolves) | `viva` (unchanged) |

`doc_file` is a relative path (e.g. `docs/PRODUCT.md`); the title uses the
basename (`PRODUCT.md`) via `split('/').pop()` — full paths add length
without adding disambiguation in the common case, and the doc-picker approach
was already decided against full-path display for this reason (basename wins
per user decision on the design question).

**Implementation**: one small helper function, e.g. `setTabTitle(parts)`,
that filters out falsy/empty parts, joins with ` · `, and appends `viva`.
Called from:

1. Initial review load (`fetch('/input')` success handler, `mode === 'review'`)
2. Initial diff load (`fetch('/input')` success handler, `mode === 'diff'`)
3. The SSE `round` event handler (fires on every round transition for both
   review and diff modes)
4. The SSE `complete` event handler (currently the title is *never* updated
   on completion — it keeps showing the last round's title after the session
   ends; this fixes that gap)

**Side fix absorbed by this change**: the SSE `round` handler currently
hardcodes the string `review` regardless of mode, so a diff-mode round
transition shows `viva · review · REV 02` instead of a diff-labeled title.
Composing the title from `data.mode` (present on every round payload) fixes
this as a natural consequence of routing all four sites through the same
helper — not a separate change.

**Fallback behavior**: if `doc_file` is empty or missing, that segment is
simply dropped (e.g. `REV 02 · viva`) — no error, no placeholder text.

**`complete` event detail**: the SSE `complete` payload (`summary`, posted
freely by the agent to `/next-round`) does not itself carry `doc_file` or
`context` — those live only on the round/init payloads. The client already
keeps `REVIEW_DATA` (review/diff) and `QA_DATA` (Q&A) as persistent globals,
updated on init and every round, so the `complete` handler reads the doc name
/ topic and mode from whichever of those is non-null rather than from the
`complete` event's own payload.

## Non-goals

- No change to the in-page `doc-title` / `doc-path` header elements — those
  already show the doc name prominently in the UI body. This is tab-title
  only.
- No persistence, no new schema field, no server-side change. Purely a
  client-side (embedded JS) presentation fix.

## Testing

`document.title` can't be exercised by the stdlib `urllib`-based server
integration tests (no real browser/DOM). The regression this design guards
against is a future edit that updates one `document.title` call site (e.g.
adds a new mode) without updating the others, reintroducing inconsistent or
stale titles.

Add a static assertion test (plain-assertion style, matching existing unit
tests) that:
- Extracts the `HTML` constant from `server.py`.
- Asserts there are no hardcoded stale patterns like
  `'viva · review · REV '` or `'viva · diff · REV '` as literal
  `document.title =` assignments (i.e., all title-setting is routed through
  the shared helper, not string-built ad hoc at each call site).
- Asserts the helper function itself exists and the four call sites
  (`mode === 'review'` init, `mode === 'diff'` init, `round` SSE handler,
  `complete` SSE handler) each reference it.

This is a structural/regression guard, not a behavioral one — full behavioral
verification happens by running the app and inspecting the tab title, per
project convention for UI changes.
