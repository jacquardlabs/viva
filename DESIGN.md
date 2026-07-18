# viva — Design System

## Metaphor

Blueprint/drafting-table. Dark mode: drafting board illuminated in cyan linework
on midnight blue. Light mode: blueline print on white vellum. The document is a
bounded drawing sheet resting on a flat table — no background grid at any layer.
Every visual decision flows from this metaphor: square corners, registration-mark
crop ticks on active cards, monospace labels, the sheet's edge coordinates and
corner marks, and drafting-room gestures — revision triangles, a transmittal slip
on re-issue, and an approval stamp.

## Color tokens

All colors are CSS custom properties defined in `:root` with a full `@media
(prefers-color-scheme: light)` override. Never use hex literals in component styles.

| Token | Dark | Light | Semantic role |
|-------|------|-------|---------------|
| `--bg` | #0a1727 | #f3f6fa | Sheet fill (and inset panels) |
| `--bg2` | #0f1f33 | #e9eef5 | Card / panel background |
| `--bg3` | #152840 | #dde5ef | Hover state |
| `--table` | #060e1a | #e2e8f1 | Flat table the sheet sits on (body background) |
| `--border` | #1d324e | #cdd9e8 | Default border |
| `--border2` | #2a4768 | #a8bdd4 | Emphasized border |
| `--text` | #d8e7f5 | #13293f | Primary text |
| `--text2` | #7f9cba | #446080 | Secondary text |
| `--text3` | #48648a | #8aa0b8 | Tertiary / disabled |
| `--accent` | #5cc8ff | #1271b8 | Interactive / selected |
| `--accent-dim` | rgba(92,200,255,.08) | rgba(18,113,184,.08) | Accent wash |
| `--scrim` | rgba(6,14,26,.72) | rgba(19,41,63,.32) | Recap-overlay modal scrim (midnight dark; blue-ink over vellum light) |
| `--teal` | #43e0a8 | #0c8a63 | **Approved** verdict |
| `--teal-bg` | rgba(67,224,168,.06) | rgba(12,138,99,.07) | Approved wash |
| `--orange` | #ff5a36 | #cf3f1d | **Changes / error** verdict |
| `--orange-bg` | rgba(255,90,54,.08) | rgba(207,63,29,.07) | Changes wash |
| `--violet` | #ffc857 | #9a6b00 | **Info / question** verdict |
| `--violet-bg` | rgba(255,200,87,.08) | rgba(154,107,0,.08) | Info wash |

### Verdict color mapping

| Verdict | Token | Badge class |
|---------|-------|-------------|
| `approved` | `--teal` | `.vbadge-approved` |
| `changes` | `--orange` | `.vbadge-changes` |
| `info` | `--violet` | `.vbadge-info` |

### Annotation severity mapping

| Severity | Token | Strip class |
|----------|-------|-------------|
| `info` | `--teal` | `.annot-info` |
| `warn` | `--violet` | `.annot-warn` |
| `error` | `--orange` | `.annot-error` |

## Typography

Two families only. No exceptions.

- **Bricolage Grotesque** — body text, section content, textareas, processing/complete messages.
- **Fragment Mono** — labels, badges, all monospace data (round numbers, paths, ids), small-caps headings, revision triangles, stamp lettering.

Label convention: 8–10px, `letter-spacing: 0.08–0.16em`, `text-transform: uppercase`, `color: var(--text3)`.

## Shape

**Square corners by default.** A single grouped rule under the "Blueprint geometry"
comment enforces it — keep it grouped:

```css
.card, .action-btn, .note-field, .vbadge, .btn-skip, .btn-submit,
.section-content, .choice-chip, .qa-btn,
.progress-track, .progress-fill { border-radius: 0; }
```

This rule is authoritative. Two of its members (`.vbadge`, and `.progress-track` /
`.progress-fill`) also carry an earlier standalone `border-radius` declaration
(`.vbadge` 3px; progress 2px). Those are separate, earlier rules of equal
specificity — the grouped `border-radius: 0` rule appears later in the source and
wins, so all of these render square. Do not describe progress or the badge as
rounded; the standalone declarations are effectively dead.

Genuinely rounded elements (internal decorative or affordance details, not primary
surfaces) — each value taken from its own rule in the current CSS:

| Selector | Radius |
|----------|--------|
| `.dot` | 50% |
| `.processing-dot` | 50% |
| `.carried-stamp` | 2px |
| `.sort-toggle` | 3px |
| `.settle-btn` | 3px |
| `.comment-popover` | 4px (#68) |
| `.annot` | 5px |
| `.open-thread` | 5px |
| `.section-content::-webkit-scrollbar-thumb` | 5px |
| `.diff-block` | 6px |
| `.d2h-file-wrapper` (diff mode, viva override) | 6px |

The blueprint gestures (`.rev-tri`, `.approve-stamp` / `.stamp-rule`) and the
sheet itself (`#paper` and its `.paper-marks` decoration) carry no
border-radius — they are square by design, extending the drafting geometry.

## Layout

### Sheet on table — the ground

`<body>` is the flat drafting table: `background: var(--table)` and nothing
else — no background grid at any layer. The document is a bounded drawing
sheet, `#paper`, that wraps `<main class="shell">` and grows with it
(content-bounded, not a fixed viewport frame):

- **Sheet edge**: `#paper` — `position: relative; max-width: 700px;
  margin: 32px auto 96px; background: var(--bg);
  border: 1px solid var(--border2)`.
- **Inner rule**: `#paper::before` — `1px solid var(--border)` at `inset: 7px`,
  `pointer-events: none`.
- **Decoration** (`.paper-marks`, `aria-hidden="true"`, hidden below 740px):
  four corner `+` registration marks (`.pmark` — Fragment Mono 13px,
  `var(--accent)` at 0.7 opacity, hanging 7px outside each corner) and edge
  coordinates (`.pcoord` — Fragment Mono 10px, `var(--text3)`): numbers 1–4
  across the top edge (`.pc-n`), letters A–D down both side edges
  (`.pc-w` / `.pc-e`).

The skip link, bottom bar, and recap overlay sit outside `#paper`; everything
that scrolls sits on the sheet.

### Shell

Single-column shell, `max-width: 700px`, centered. Bottom bar is fixed, matches
the shell's max-width with `bottom-inner`. Shell has `padding-bottom: 140px` to
clear the bar. Do not exceed 700px in the shell (diff mode is the one exception:
`body.mode-diff` widens `.shell`, `.bottom-inner`, **and `#paper`** together to
`min(95vw, 1600px)` — see Diff-first layout).

## Interactive controls

### Reticle pattern

Corner tick marks replace a full border. Implemented via a `background` gradient
trick with the `--c` CSS custom property (registered with `@property` so the recolor
animates) for animatable color transitions. All interactive controls in this class
share the same base rule — add new controls to the selector group, do not
re-implement. Current membership:

```
.action-btn, .qa-btn, .choice-chip, .attach-btn,
.cmt-add-btn, .cmt-chip, .cmt-save, .cmt-cancel
```

(The `.cmt-*` controls were added by #68's multi-comment review.)

States:
- Idle: `--c: var(--border2)`
- Hover: `--c: var(--text3)`
- Active/selected: `--c: var(--teal|--orange|--violet)` depending on verdict

### Focus

All interactive controls must be in the `:focus-visible` group rule. Use
`outline: 1.5px solid var(--accent); outline-offset: 2px`. Do not add custom
focus styles to individual controls — add them to the group selector. Current
membership is the reticle group plus every other focusable control:

```
.card-head, .action-btn, .qa-btn, .choice-chip, .attach-btn,
.cmt-add-btn, .cmt-chip, .cmt-save, .cmt-cancel,
.settle-btn, .diff-toggle,
.carried-show, .carried-withdraw, .transmittal-row,
.recap-row, .recap-close,
.btn-skip, .btn-submit
```

## Animation

- **Card entrance**: `fadeUp` — `opacity: 0 → 1`, `translateY: 8px → 0`, `0.4s ease`.
  Stagger with `animation-delay` for list items.
- **Accordion expand/collapse**: `grid-template-rows: 0fr → 1fr`, `0.28s cubic-bezier(0.4,0,0.2,1)`.
  Never animate `height` directly.
- **Verdict dot transition**: `background 0.25s, box-shadow 0.25s`.
- **Progress bar**: `width 0.6s cubic-bezier(0.4,0,0.2,1)`.
- **Approved card fade**: `opacity 0.35s` — cards dim to 0.42 on approve, restore to 0.72 on hover.
- **Approval stamp**: `stamp-down` — `0.42s cubic-bezier(0.2,1.4,0.4,1)`, scales from 2.1 down to 1 at a fixed `-5deg` tilt. Suppressed under `prefers-reduced-motion: reduce`.
- **Between-rounds pulse**: `viva-pulse` — `opacity 1 → 0.25 → 1`, `1.6s ease-in-out infinite` on `.processing-dot`. Suppressed under `prefers-reduced-motion: reduce` (as are card entrances and the stamp).

## Card accordion

A card has three states:
- **Idle** — closed, `dot-idle`, no `is-active` class.
- **Active** — open, `dot-active` (if no verdict), `is-active` class, body animates in.
- **Approved** — closed, `dot-approved`, `is-approved` class (dimmed). Hoverable.

Only one card is active at a time. Approval auto-advances to the next unreviewed card
with an 80ms delay. The same 80ms delay applies to skip.

On round ≥ 2, sections approved in a prior round don't render as accordion
cards at all — see Carried approvals.

## Carried approvals (frontend v2 phase 1, unreleased)

On round ≥ 2, a section in `approved_ids` renders as a **carried card**
(`buildCarriedCard`) instead of an accordion card: `.card.is-carried`, a dimmed
head-only line — `opacity: 0.55`, `0.9` on hover/focus-within, kept brighter
than `.is-approved`'s 0.42 so the affordances stay discoverable. The head
carries the `carried` marker (label convention), the section title, an
`unchanged since your stamp — show` reveal (aria-expanded/aria-controls,
toggling a hidden read-only `.carried-body` whose markdown renders lazily on
first reveal), the mono `APPROVED` mini-stamp (`.carried-stamp` — Fragment Mono
9px, `var(--teal)` text and border, 2px radius, `-2deg` rotate, echoing the
completion stamp), and the `× withdraw approval` control.

Rules:

- **Gate**: `REVIEW_DATA.round > 1 && priorApprovedSet.has(s.id)` — a round-1
  boot can never render a carried card, and the accordion card markup is
  unchanged beside it.
- **Withdraw** clears the verdict back to pending and swaps in a normal
  accordion card **in place** (document order is canonical — withdrawn cards
  never reorder), opened for re-review.
- Carried cards render with no entrance fade (a long carried tail stays
  quiet) and never become `rState.active` — `activateReviewCard`'s carried
  branch scrolls + reveals instead of activating.
- The wire is untouched: prior approvals pre-populate `rState`, so submit
  records a carried section exactly as carry-forward always has — a bare
  `{id, verdict: "approved"}`, no comments.

## Transmittal slip (frontend v2 phase 1, unreleased)

The cover slip on a returned drawing: in **review mode at round ≥ 2**, a
`<nav class="transmittal">` mounts between the ledger and `#review-cards`. It
ships empty and hidden; `transmittalHTML(data)` is a pure function over the
review-input — classification and ordering only, no DOM — and
`renderTransmittal` owns the mount and the jump wiring. Header:
`Transmittal · REV 0N` (uppercased by the label style).

**Row grammar** — each section lands in exactly one row family, checked in
this order (diff first, then flags, then carried). Each row is a jump-link
`<button class="transmittal-row">` carrying a marker glyph, a mono label, and
the section title:

| Row label | Condition | Glyph | Color |
|---|---|---|---|
| `revised to your note` | `diff` present **and** `open_notes` present | △ | `--orange` |
| `revised` | `diff` present, no `open_notes` | △ | `--orange` |
| `flagged & unreviewed` | strongest annotation severity `error`, not carried | ⚑ | `--orange` |
| `flagged & unreviewed` | strongest annotation severity `warn`, not carried | ⚑ | `--violet` |
| `approved & unchanged` | member of `approved_ids` | ▣ | `--teal` |

**Attribution rule**: a revised row claims the reviewer's note as its cause
(`revised to your note`) only when `open_notes` stand behind the diff — a
silent diff renders the bare `revised`. The slip never asserts causation the
data doesn't carry. `info` annotations advise, they don't flag — only
`error`/`warn` produce flag rows, and the error partition rows before warn.

Empty families drop; all families empty → no slip. Round 1 → no slip,
unconditionally. Every row jump-activates its section through
`activateReviewCard` (whose carried branch scrolls + reveals). **Diff mode
ships no slip**: hunk identity is positional across rounds
(`{filepath} hunk N`), so a re-cut diff can renumber hunks and break the
attribution.

## Recap overlay — the submit gate (frontend v2 phase 1, unreleased)

Submit never fires blind in review/diff mode. `#recap-overlay` is a hidden
`role="dialog" aria-modal="true"` shipped in the static page; `openRecap()`
rebuilds its grid from live verdict state on every open. Each `.recap-row`
(a jump-link button) indexes one section: mono id, title, verdict dot + label
(reusing the card dot slots, colored `rv-approved` / `rv-changes` / `rv-info`
/ `rv-pending`), and active-note count (or `—`).

- `btn-submit`'s ready click in review/diff opens the overlay instead of
  submitting; the page's **only** `submitReview(false)` call site is the
  overlay's `confirm & submit` control (`#recap-confirm`), which mirrors
  `btn-submit`'s readiness class at open — a recap opened mid-review via `o`
  can't submit a round the bottom bar wouldn't.
- `o` toggles the overlay anytime in review; Escape, the `×` close, and a
  backdrop click close it; a row click closes-and-activates its section.
  Focus moves to the confirm control on open and returns to `btn-submit` on
  close if it was inside the overlay.
- `skip rest & submit` (`btn-skip`) stays a direct `submitReview(true)`
  escape hatch — no recap. Q&A ships no recap: its done → path calls
  `submitQA(false)` directly, and `openRecap` bails without `REVIEW_DATA` or
  with the review view hidden.
- The SSE `processing`/`round` handlers close a stale recap — the review it
  indexed is gone from under it.

## Between rounds (frontend v2 phase 1, unreleased)

No full-view takeover while the agent revises: `#processing-view` is the
between-rounds card. A pulsing accent dot (`.processing-dot`, 10px,
`viva-pulse`) sits over the heading `REV 0N submitted — the agent is revising`
and `.processing-requests` — the reviewer's just-submitted `changes`/`info`
rows verbatim (`.pr-row`: mono type colored `--orange`/`--violet`, section
title, untruncated note).

`submitReview` snapshots `{sectionTitle, type, note}` rows from the active
comments **before** the POST; the `processing` SSE handler renders from that
snapshot, and the `round` handler consumes it. The snapshot is deliberately
in-memory only (never written to `.viva/`): a tab reload during revision
re-boots into the prior round's view exactly as before. Zero rows — an
all-approved submit, or any Q&A submit (`submitQA` never snapshots) — fall
back to the minimal `Claude is revising…` line. The #119 soft-timeout banners
(`Still waiting — check the terminal.` / `Connection lost — check the
terminal.`) overlay this card exactly as they overlaid the old view.

## Multiple inline comments (#68, v1.10.0)

A section card hosts a list of typed comments rather than a single verdict pick. The
section verdict is **derived** from its comments, never chosen directly: no active
comments → approved/pending; any `changes` comment → changes; otherwise info.

Design elements:
- **Add row** (`.comment-add-row`) — a `.cmt-add-hint` ("select text above to comment")
  plus a reticle `.cmt-add-btn` ("+ add note"). The hint pushes the button right with
  `margin-right: auto`.
- **Comment popover** (`.comment-popover`) — the only rounded surface in the review
  body (`border-radius: 4px`, `1px solid var(--border2)`, `background: var(--bg2)`).
  Holds the quoted span, type chips, an image attach control (`.attach-btn` +
  `.thumb-strip`, per-comment attachments, #66), and save/cancel.
- **Quoted span** (`.cmt-pop-quote`) — the text being commented on, rendered as a
  focal accent callout: `background: var(--accent-dim)`, `border-left: 2px solid var(--accent)`.
- **Type chips** (`.cmt-chip`, with `.cmt-chip-changes` / `.cmt-chip-info`) — reticle
  controls; the selected chip carries `.is-on` and recolors `--c` to `--orange` (changes)
  or `--violet` (info).
- **Save / cancel** (`.cmt-save`, `.cmt-cancel`) — reticle controls; save reads
  affirmative (`--c: var(--teal)`), cancel stays muted.
- **Inline highlight** (`mark.cmt-hl-changes`, `mark.cmt-hl-info`) — the anchored span
  in the section body gets a `2px` colored bottom border and the matching `*-bg` wash.
- **Comment list** (`.comment-list` → `.cmt` rows) — this round's freshly added
  comments. Each row: `.cmt-type` (mono, uppercase, colored by verdict), `.cmt-quote`
  (italic muted excerpt), `.cmt-note` (the note text), and a `.cmt-del` remove button.
  Rows divide with `1px solid var(--border)`.

## Blueprint elements (#69, v1.11.0)

Drafting-room gestures that extend the metaphor. All square, all monospace.
(The drawing sheet itself — `#paper` and its coordinate/corner decoration —
is the ground these gestures sit on; see Layout.)

- **Revision triangle** (`.rev-tri`) — drafting's "this region changed at this rev"
  flag. Rendered as `△ NN` in Fragment Mono, `11px`, `color: var(--orange)`, keyed to
  the titleblock REV. Shown on a section head only when the section carries a diff.
- **Approval stamp** (`.approve-stamp` → `.stamp-rule`) — the "signed off" gesture on
  the complete screen. Double-ruled teal ink (`2px solid var(--teal)` plus a `::before`
  inner rule at `inset: 3px`), slammed on at a `-5deg` tilt via the `stamp-down`
  animation. Children: `.stamp-word` ("APPROVED", `2.1rem`), `.stamp-meta` ("viva ·
  <date>"), `.stamp-sub` ("N sheets · M revisions"). All Fragment Mono.

## Diff rendering (#99, superseded in-branch by diff2html delegation)

`/viva-diff` renders each hunk via [diff2html](https://github.com/rtfpessoa/diff2html)
(MIT, `diff2html@3` on jsdelivr — same CDN precedent as marked/DOMPurify/hljs).
Two bundles: the core (`diff2html.min.js`, the `Diff2Html.html` string API)
and the slim UI wrapper (`diff2html-ui-slim.min.js`, syntax highlighting
only, fed the page's own hljs — the full UI bundle embeds a second hljs
copy and is deliberately not used). The stylesheet is mode-specific and
injected by the diff dispatch branch, so review/QA sessions never fetch it.

The `renderDiffHunk` adapter strips the section's ` ```diff ` fence,
synthesizes the `---/+++` preamble from the section title's filepath at
render time (never stored — `section.content` stays byte-for-byte verbatim
for anchors and carry-forward), and renders with `diffStyle: 'word'`
(intra-line word-level emphasis), `matching: 'words'`, no file list,
`colorScheme: 'auto'` (follows `prefers-color-scheme`, like the rest of
viva), and `outputFormat` picked by viewport: side-by-side at ≥900px,
line-by-line below. **Pipeline order is load-bearing:** `Diff2Html.html`
produces a string, `DOMPurify.sanitize` runs on the string, and only the
sanitized result touches the DOM — the same sanitize-before-assign order
as `renderMarkdown` (materializing first would let insertion-time payloads
execute before removal). The whole render is try/caught, falling back to
the fenced view rather than stranding a card. Line numbers get
`aria-hidden` after render (screen readers would otherwise announce them
before every code line). Fallback chain when a CDN asset is absent —
scripts, or the injected stylesheet, gated via `link.sheet`: fenced
` ```diff ` via `renderMarkdown` (tagged `d2h-pending`, upgraded in place
by load-retry listeners on all three assets) → `md-raw` plain text. Binary
sections (parse_diff.py's plaintext sentinel, no fence) render as prose,
unchanged.

viva-side guards on the diff2html DOM: surface theming maps d2h's own
`--d2h-*` custom properties (light and dark families) to viva tokens
(`--bg`, `--bg2`, `--border`, `--border2`, `--text3`), leaving the ins/del
tints as d2h's semantic green/red; `Fragment Mono` is forced on the diff
table and file header (the two-families rule); `.d2h-file-name`/`.d2h-tag`
are hidden (the card title and file-group header already name the file —
only d2h's per-hunk `+N/−M` stats remain); a scoped td reset (the generic
`.section-content td` editorial-table rule would otherwise border/pad every
diff row); `user-select: none` on line numbers; `position: relative` +
`border-radius: 6px` on `.d2h-file-wrapper` (the containing-block fix that
keeps d2h's absolutely-positioned line numbers clipped inside the collapse
accordion, plus the documented diff-surface radius); and a cross-pane
selection guard that degrades a selection spanning both side-by-side panes
to an unanchored whole-section note.

## Diff-first layout (mode-diff)

Diff mode stamps `mode-diff` on `<body>`. Mode-scoped overrides widen
`.shell`, `.bottom-inner`, and `#paper` together to `min(95vw, 1600px)` and remove
`.section-content`'s `60vh` nested scroll — page scroll is the only
vertical scroll in diff mode. Widening the container (never escaping it)
is the load-bearing choice: it leaves `.card-body-inner`'s
`overflow: hidden` accordion animation untouched. Review/QA modes carry
no `mode-diff` class and are unaffected.

## File-header grouping (follow-up to #99, unreleased)

A static divider — `path/to/file.py · N hunks` — above each contiguous run
of `/viva-diff` hunk-cards sharing a filepath. `.file-group-header`: 9px
Fragment Mono, uppercase, `--text3` (the label convention's default), a
quiet landmark, not a heading — reads subordinate to the 13px `.card-title`.
Static only: no sticky/pinned behavior, no collapse, no live approval count,
filepath + hunk count only. Diff-mode-only; review mode's card list is
unaffected (`initReview` builds headers only when `REVIEW_DATA.mode === 'diff'`,
and as a second, independent guarantee, `setupCardSort` forces the
confidence-sort toggle off via `REVIEW_DATA.mode !== 'diff'` — so its CSS
`order` reordering can never strand a header away from its file's cards).

## Bottom bar

Fixed to viewport bottom. Glass-morphism: `backdrop-filter: blur(16px) saturate(180%)`.
Always visible. Hidden only on complete state (JS sets `display:none`). Two children:
stats area (left) and btn-group (right).

Submit button states:
- `btn-submit disabled` — visually grayed, cursor not-allowed, click blocked in handler.
- `btn-submit ready` — `var(--accent)` background, glow shadow, slightly raised on hover.

In review/diff mode a ready click opens the recap overlay rather than
submitting (see Recap overlay); Q&A's done → click submits directly.
`btn-skip` submits directly in every mode.

## Accessibility requirements

1. Every interactive element must be a native `<button>` or `<a>` — never a `<div>` with onclick.
2. Accordion controls must carry `aria-expanded` and `aria-controls`.
3. Dynamic stat updates must be in an `aria-live` region.
4. Page `<title>` must reflect current mode and round.
5. Decorative emoji in button text must be wrapped in `<span aria-hidden="true">`.
6. Decorative chrome (the sheet's `.paper-marks` decoration) must carry `aria-hidden="true"`.
7. Design system tokens must be used for all colors — no hardcoded hex in component styles.
8. A `<main>` landmark must wrap the scrollable shell.
9. Entrance, stamp, and between-rounds pulse animations must be suppressed under `prefers-reduced-motion: reduce`.

## API conventions

- All POST endpoints return `{"ok": true}` on success.
- All errors return `{"error": "..."}` as JSON with the appropriate 4xx/5xx status.
- All endpoints return `Content-Type: application/json` for JSON payloads.
- Body-only POSTs — no mixing query params and body for the same logical operation.

## CLI conventions

- All scripts use `argparse` with named flags (no `sys.argv` indexing) — every script, no exceptions.
- Scripts that read-then-write use separate `--input` (read) and `--output` (write) flags.
- Producer scripts write JSON to stdout for piping; they do not modify files.
- **One deliberate exception: `annotate.py` modifies `--input` in place** and has no `--output`. Producers pipe their flags into the same round file the server will read, and because the merge is additive and idempotent, re-running is safe. Any new script that must mutate its input in place documents the reason the same way — the default remains separate `--input`/`--output`.
- Optional arguments have sensible defaults; required arguments are validated with clear error messages.

## JSON protocol conventions

- Field names: `snake_case`.
- Boolean flags use present-tense active descriptors: `submitted_early`, `open`, `settle`.
- The same concept uses the same field name across modes: `submitted_early` is the
  shared "ended before reviewing everything" flag in every mode — never a mode-specific
  alias like `skipped`.
- Annotation schema: `{kind, severity, message, anchor?}`. Structured extensions (`basis`, `level` for confidence) are preserved through the shared merge in `scripts/annotate.py`, so a confidence flag routes through the same write path as any other annotation rather than bypassing it.
- **`anchor` is overloaded — three semantics by context.** The name is reused across the input and output schemas with different meanings and consumers; keep them straight when adding an annotation kind or a consumer:
  - *Annotation, display* (input) — a string rendered as the badge's hover `title` attribute.
  - *Annotation, navigation* (input) — when the string matches another section's `id` (the cross-section contradiction producer), it renders as a `.annot-jump` deep-link to that section instead of a hover title.
  - *Comment, selection* (output) — a `comment.anchor` object `{text, offset}`: the exact text the reviewer selected in the section, which the agent uses to scope its rewrite (`offset` disambiguates a repeated phrase). This is a structured object, not a string, and lives on comments in `review-r{N}.json` — a different shape from the annotation `anchor` above.
- `GET /input` returns the current review-input merged with `ledger: [...]` — the live running ledger. The `ledger` field is injected by the server at serve time and is **not** part of the `review-input-r{N}.json` file schema that `parse_sections.py` writes.
- The round shapes are the system's load-bearing contract, defined in one place: `scripts/schema.py` holds the TypedDicts, `section_key()` (the single section-identity normalization), `verdict_to_ledger_entry()` (the single ledger-row rule), and the boundary validators. Adding a field means updating that module and validating at the boundary (on parse write, on server read) — never at the point of use.
