# viva — Design System

## Metaphor

Parts catalog. A white page, compact type, every state visible and tabular,
nothing animated, nothing withheld — the register of a supplier's catalog that
expects to be scanned by someone who already knows what they came for. Light is
the primary theme (the ground the design was drawn on); dark is an override.
Every visual decision flows from this: square corners, full 1px rules rather
than corner ticks, a 2px ink bar closing the header and the footer, monospace
for everything the machine says, and density in place of decoration.

Superseded 2026-08-07: the blueprint/drafting-table metaphor — sheet edge,
7px inner rule, corner registration marks, A/B/C/D edge coordinates, crop-tick
control edges, cyan-on-midnight ground. It is gone at every layer, not hidden.

### Ink discipline

Four parties, one hue each, never shared. This is the load-bearing rule of the
system: a new surface picks its ink by asking whose voice it is, not by taste.

| Ink | Token | Belongs to | Appears as |
|-----|-------|-----------|-----------|
| Catalog yellow | `--touch` | the reviewer's touch **on the text** | anchored spans, applied replacement wording, palette selection — never a label, never a border, never syntax |
| Cobalt | `--acc` | the reviewer's party | their comments and suggestions, open judgment, every interactive control |
| Teal | `--machine` | the machine's party | passed checks, approved verdicts |
| Amber | `--fact` | machine-flagged open facts | a claim missing a source, an unanswered check, `info` verdicts |

Red and green are **not tokens**. They appear in exactly one place — the
suggestion fence and rendered diff lines — where diff semantics already own
them and every reviewer already reads them.

## Color tokens

All colors are CSS custom properties defined in `:root` (light — the primary
theme) with a `@media (prefers-color-scheme: dark)` override. Never use hex
literals in component styles.

| Token | Light | Dark | Semantic role |
|-------|-------|------|---------------|
| `--paper` | #ffffff | #16181a | The page (and the body ground) |
| `--sunk` | #f8f8f7 | #1c1e21 | Recessed wells: code blocks, inputs, table heads |
| `--ink` | #1d1f21 | #e6e7e8 | Rules, headings, the dispatch stamp |
| `--ink2` | #2c2e30 | #d5d7d8 | Body copy |
| `--soft` | #6b6e71 | #9a9ea1 | Labels, secondary |
| `--faint` | #b0b1ae | #5f6265 | Settled, disabled |
| `--rule` | #d9dad8 | #2f3235 | Hairline |
| `--touch` | #ffec8f | rgba(255,236,143,.22) | The reviewer's touch on the text |
| `--acc` | #2946c4 | #8fa6f5 | The reviewer's party / interactive |
| `--machine` | #0c7f6b | #4fc2a5 | The machine's party — checks, approved |
| `--fact` | #a06a12 | #d19a3f | Machine-flagged open facts |
| `--scrim` | rgba(29,31,33,.28) | rgba(10,11,12,.72) | Recap-overlay modal scrim |

Yellow is dimmed to a wash in dark rather than reused at full strength: on a
charcoal ground, full-strength yellow is a highlighter, not a touch.

### Theme selection

The reader chooses, and the choice wins over the OS. `[data-theme]` on `<html>`
cycles **system → light → dark → system**, where *system* is the **absence** of
the attribute rather than a third value — an untouched page behaves exactly as
it did before the toggle existed, and returning to system means having no
opinion recorded rather than recording "system".

Two rules make the override work in both directions:

- `:root[data-theme="dark"]` beats the bare `:root` inside the media query on
  specificity, so dark is reachable on a light-mode machine.
- The media block is scoped `:root:not([data-theme="light"])`, so it stands
  down when the reader has explicitly chosen light. Without that `:not`, the
  toggle would appear dead for exactly the reader who most needs it — someone
  on a dark-mode machine trying to see the primary theme.

`color-scheme` follows the choice so browser chrome (form controls, scrollbars,
the canvas behind unpainted area) matches the page instead of flashing the
opposite ground. The stored choice is applied by a synchronous script in
`<head>`, ahead of the stylesheet: restoring it after the body renders paints
the OS theme first and then flips, which is the flash the toggle exists to
remove.

**The dark palette is written twice** — once under the media query, once under
`[data-theme="dark"]` — because CSS cannot name a palette and apply it from two
selectors without a preprocessor, and viva ships no build step. That
duplication is licensed by `tests/test_theme_toggle.py`, which parses both
blocks and fails on a single drifted value. Do not "fix" the duplication by
deleting one block; both are load-bearing.

**Component aliases.** The older token names (`--bg`, `--bg2`, `--border`,
`--text`, `--teal`, `--orange`, `--violet`, …) remain as aliases pointing at the
party inks, so component styles written against them kept working when the
ground changed. They are not the source of truth — anything new takes a party
ink directly. Two aliases carry a deliberate remapping: `--orange` (the
`changes` verdict) is cobalt, because requesting a change is reviewer judgment;
`--violet` (the `info` verdict) is amber, because a question is an open fact.

### Verdict color mapping

| Verdict | Token | Badge class |
|---------|-------|-------------|
| `approved` | `--teal` | `.vbadge-approved` |
| `changes` | `--orange` | `.vbadge-changes` |
| `info` | `--violet` | `.vbadge-info` |

Three verdicts, three inks — a comment *type* is a different axis and takes no
verdict ink: a `suggestion` derives to the `changes` verdict but marks its span
in `--accent` (see Multiple inline comments).

### Annotation severity mapping

| Severity | Token | Strip class |
|----------|-------|-------------|
| `info` | `--teal` | `.annot-info` |
| `warn` | `--violet` | `.annot-warn` |
| `error` | `--orange` | `.annot-error` |

## Typography

Two families only. No exceptions.

- **Compact grotesque** (`'Helvetica Neue', Helvetica, Inter, system-ui`) —
  everything a human reads: section content, headings, prose.
- **Monospace** (`ui-monospace, 'SF Mono', Menlo`) — everything the machine
  says: labels, badges, ids, paths, round numbers, buttons and controls, code.

No display face. A catalog page earns its character from density and rules, not
from a headline font. `font-variant-numeric: tabular-nums` is set on `body`, so
every column of digits aligns without per-rule opt-in.

Label convention: 8–10px, `letter-spacing: 0.08–0.16em`, `text-transform: uppercase`, `color: var(--soft)`.

### Reading measure

Prose stops at **72ch** (`.section-content`), independent of how wide the
window or the card is. The shell is fluid to 1240px and spare width goes to the
margin conversation, never to longer lines. Wide content — code blocks, tables —
escapes the measure and scrolls inside its own container, so the page body
never scrolls sideways.

`.section-content` carries **no nested scroll**. An earlier `max-height: 60vh;
overflow-y: auto` put a second scrollbar inside every long section: the reader
scrolled a viewport to reach content already on screen, and the page scrollbar
lied about how much was left. The page is the only scroll.

### Syntax highlighting

highlight.js is applied to language-tagged blocks; the theme is viva's own, not
a preset, because a stock theme spends the reviewer's colors on syntax. The
palette obeys the ink discipline: no catalog yellow (that means the reviewer
touched the text), no red or green (fence-only). Comments recede to `--faint`
italic, keywords carry `--ink` at weight 600 rather than a hue, strings take
the machine's `--machine`, numbers take `--fact`. The result is near-monochrome
on purpose — on a catalog page, code is a specification.

## Shape

**Square corners by default.** A single grouped rule enforces it — keep it
grouped:

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

`#paper` carries no border-radius: on the catalog ground it is a bare content
wrapper with no fill, edge, or decoration of its own.

## Layout

### The page — the ground

`<body>` is the catalog page: `background: var(--paper)` and nothing else. There
is no sheet, no table beneath it, and no frame — the ground is legible as a page
without one.

`#paper` survives as the content wrapper that `<main class="shell">` sits
inside, because every layout rule and served-page test hangs off it, but it is
now bare: `position: relative; max-width: 1240px; margin: 0 auto;
background: var(--paper)`. Its sheet dress — the edge border, the `::before`
inner rule at 7px inset, the four corner registration marks, and the A/B/C/D
edge coordinates — was **deleted**, markup included, not hidden.

The skip link, bottom bar, and recap overlay sit outside `#paper`.

### Shell

Fluid shell, `max-width: 1240px`, centered, `padding: 32px clamp(16px, 3vw, 44px) 140px`
(the bottom value clears the fixed bar). The shell is *not* the reading measure —
`.section-content` caps prose at 72ch and the shell's extra width belongs to the
margin conversation. The bottom bar matches the shell's max-width via
`.bottom-inner`, is opaque (`var(--paper)`, no backdrop blur), and closes the
page with the same 2px ink rule that opens it.

Diff mode widens `.shell`, `.bottom-inner`, **and `#paper`** together to
`min(95vw, 1600px)` — see Diff-first layout.

### The document grid — doc + margin (#186, unreleased)

**Review mode only.** `initReview` stamps `.doc` on `#review-cards` when
`REVIEW_DATA.mode === 'review'`; that class arms every rule below. Diff mode
keeps the accordion (`buildReviewCard`, Card accordion) unchanged — a hunk is
not prose, it has no margin to annotate and no measure to hold, and a 200-hunk
changeset read as one continuous print is a worse surface than one hunk at a
time.

Sections print **open, in document order** (`buildDocSection`), every one
rendered up front. A settled section **dims in place** (`.is-approved` →
`opacity: 0.5` on its prose) rather than collapsing to a clickable row: the
point of a document review is reading the document.

Each section is a run of rows. The rendered markdown's top-level blocks become
the prose column of one row each, so a note sits beside the paragraph it
annotates rather than beside the section.

| Track | Holds | Width |
|-------|-------|-------|
| gutter (`.rg`) | producer check flags (`.lchip`), right-aligned | `--gutter-w: 98px` (70px + alley) |
| prose (`.rp`) | one markdown block | `minmax(0, 72ch)`; `.row.wide` → `minmax(0, 1fr)` for code and tables |
| margin (`.rm`) | threads, notes, spec table, section controls | `--margin-w: minmax(253px, 328px)` |

**The wasted-space rule.** Both side columns collapse to `0px`
(`.doc.no-gutter` / `.doc.no-margin`), decided **once per document** by
`updateDocColumns` reading the live DOM — so a comment made a moment ago counts
like a thread that shipped with the round. Per-row would jog the prose sideways
between paragraphs and per-section between sections; a column of text that
moves as you read it is worse than the space it saves. The 28px alley rides in
`.rg`'s `padding-right` and `.rm`'s `padding-left`, never in `column-gap`,
because a gap is drawn between zero-width tracks too. With the margin collapsed,
`.doc.no-margin .row-head` drops to two tracks and the section's controls print
under its heading — pure CSS, so a collapse never moves a focused control
between hosts. Below 920px the third column has no room to be a margin: notes
fall under their passage and the gutter narrows to a 30px glyph rail.

**Section numbers.** `N ·` in `.doc-num`, document order, 1-based, counting
carried sections.

**Actions.** The per-section action row is gone. Approve and add-note live in
the head row's margin with the note grammar (`.nt-btn`), keeping the
`rbtn-primary-`/`rcmtnote-` ids the rest of the app addresses them by;
`renderPrimaryButton` reads `.nt-btn` off the element to pick which dress to
draw, so one label rule (approve only with nothing open) serves both surfaces.
On an approved section the same control reads `↺ withdraw approval` and calls
`docWithdraw` — nothing was ever collapsed, so withdrawing is only the verdict
going back to pending. **Approve stays per-section for accessibility**: the head
is a heading now (`<h2 class="doc-head">`, `aria-labelledby` on the section, no
permanently-true `aria-expanded`), which would leave a section carrying no notes
with zero focusable elements. ⌘K is a second path to the same verbs, never the
only one.

### Margin notes and pins (#186, unreleased)

The margin holds two kinds of note, deliberately built differently:

- **Carried threads** (`section.open_notes`) are built once by
  `openThreadItemHTML` — the *same* builder the accordion uses; `.doc
  .open-thread` restyles it into the note grammar rather than forking the
  markup — and **placed once**, beside their `quote`'s row. A thread owns a
  reply textarea, so re-rendering it mid-keystroke would steal focus;
  `placeDocThreads` moves a node only when it is in the wrong cell.
- **This round's comments** are static text and rebuilt freely on every sync
  (`.rm-notes .nt`), carrying the anchor quote, the note, and — for a
  suggestion — D's `−/+` fence against the wording it replaces.

**Numbering.** `docNotesOrdered` sorts by the row an anchor resolves into
(unanchored → the section head), then by creation order. `markAndPin` assigns
the number and writes **both** ends in one pass — the pin in the text and the
note in the margin can never disagree about which span is note 3. The pin is a
button and jumps to its own note.

**Ink.** An anchored span in the doc print wears `var(--touch)` plus a 1.5px
`var(--touch-edge)`, per the ink discipline ("the reviewer's touch ON THE
TEXT"); the **pin**, not the highlight, carries whose note it is — `.pin-you`
(`--acc`), `.pin-fact` (`--fact`), `.pin-author` (outlined `--soft`, a declined
thread). `--touch-edge` is `transparent` on the white ground, where the yellow
fill is already the mark, and `#ffec8f` on charcoal, where `--touch` is a 22%
wash worth about a 5% luminance lift. The composite settles nothing here: its
specimen hardcodes the white ground and has no dark rendering at all.

**Flags.** `docFlagSplit` sends plain severity and check flags to the 70px
gutter as `.lchip` glyphs (`✓` info, `△` warn, `✗` error; a check flag's
`result` prints under it), and any flag carrying an **interactive jump** — a
contradiction's cross-section link, a preference's badge-to-entry link — to the
margin through `annotStripHTML`, which keeps that wiring. 70px is a glance, not
a control. A `kind: "confidence"` annotation goes to neither: it is the agent's
self-report about the whole section, it drives the triage sort, and its readout
is a spec-table row — letting it into the gutter would hold 98px open on every
self-annotated document for sort metadata.

**Anchoring reads the prose, never the commentary.** The margin and the gutter
are descendants of `#rcontent-<id>`, and every margin note echoes the wording it
annotates. `proseWalker` is the single filter that rejects `.rm`, `.rg`, and an
open `.comment-popover`; `occurrenceInRendered` counts through it,
`wrapNth` marks through it, and the `mouseup` handler additionally requires
`start.closest('.rp')`. Without it a note's echo inflates the stored ordinal and
`offsetInSource` addresses a different span in the markdown — the #95 failure,
except the margin manufactures the repeat. The filter is inert in the accordion
(nothing there matches those classes inside `.section-content`), so one walk
serves both surfaces.

**The round diff** gets a full-width `.row.wide.row-diff` of its own, directly
under the head row, **shipped collapsed**. It is the widest object on the page
and what a round-2 run found stacked at full width between the reader and the
text; collapsed-above is the one place it fits without becoming the page.

### Segmented rule (#186, unreleased)

State × party under an open heading, in honest counts. `sectionBalance` owns the
single denominator:

| Segment | Ink | Counts |
|---------|-----|--------|
| judgment | `--acc` | open `changes`/`suggestion` threads and comments, plus declined threads (waiting on accept-or-insist) |
| facts | `--fact` | open `info` threads and comments, unanswered `CHECK_KINDS` flags, warn/error producer flags |
| settled | `--settled` | settled threads, answered checks, and the section's own approval |

The order is **fixed** (judgment → facts → settled) and that fixed order is the
colorblind-safe second encoding. Raw counts ride out in the `aria-label`
("open: 2 judgment, 1 fact; 1 settled"), so the honest-proportions claim is
auditable rather than asserted. Drawn **only where something is open**: a
section with nothing open takes the thin `.rule-s` hairline, because a state bar
on a settled section is decoration.

`--settled` is a token, not a party ink — settled belongs to nobody, so it is a
filled neutral (`#e3e4e2` light, `#3a3e41` dark) rather than a hue.

The **footer** (`.foot-seg`, inside the fixed bottom bar, review mode only)
carries the same grammar over the whole document, denominated in sections:
`changes` → judgment, `info` → facts, `approved` → settled. What it does not
fill is what nobody has looked at yet — unreviewed sections are the bare track,
the one honest way to draw "not yet decided" without a fourth color.

### Command palette (⌘K, #186, unreleased)

A directory of the keyboard layer, never a second interaction model: every verb
it lists is one the page also carries as a control or a keycap. `.pal` takes the
floor's materials — square, 1px ink border, selection on `var(--touch)` rather
than a tint of the accent. Built from live state on each open, so "Approve
section 9" names the section actually under the reader.

⌘K is handled **ahead of** the `TEXTAREA`/`INPUT` guard (a reviewer mid-reply is
exactly who wants "jump to next open thread" without reaching for the mouse) but
**never opens over** the prefs panel or the recap gate — both are modal and both
own Escape, so a third would leave two things claiming the same key.

## Interactive controls

### Control edges

Every selectable control (verdict actions, Q&A chips and buttons, comment
chips, attach, save/cancel) wears a **full 1px square border** on the page's own
ground. This replaced the reticle: four corner tick marks painted as eight
background gradients with no edge between them, a drafting gesture that the
catalog does not make.

The `--c` custom property (registered with `@property` so the recolor animates)
survived the change unaltered — it now feeds `border-color` instead of the
gradient stack, so every state rule still works by reassigning one property:

| State | `--c` |
|-------|-------|
| rest | `var(--rule)` |
| hover | `var(--ink)` |
| `.sel-approve` | `var(--machine)` |
| `.sel-changes` | `var(--acc)` |
| `.sel-info` | `var(--fact)` |

Controls are set in the monospace family: a button is an instruction, not prose.

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
.annot-jump,
.prefs-toggle, .prefs-close, .pref-mute-btn,
.btn-skip, .btn-submit
```

(`.annot-jump` and the three `.prefs-*`/`.pref-mute-btn` controls were added
by #142's preferences panel — `.annot-jump` predates it but had never been
added to this group; the panel's new instances of it made the gap visible.)

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

**Diff mode's surface.** Review mode prints the document continuously — see
The document grid. Everything below still governs `buildReviewCard`,
`buildCarriedCard`, and every hunk `/viva-diff` puts on screen.

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

- **Gate**: `!asDoc && REVIEW_DATA.round > 1 && priorApprovedSet.has(s.id)` — a
  round-1 boot can never render a carried card, and the accordion card markup
  is unchanged beside it. Since #186 the gate is also accordion-only: in review
  mode a carried section dims in place with its prose on the page, and
  `buildCarriedCard` is diff mode's path, where a carried hunk really does have
  nothing left to read.
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
comments → approved/pending; any `changes` or `suggestion` comment → changes;
otherwise info. A comment is active when it is unsettled and carries a note — or,
for a suggestion, replacement wording, which is the comment's whole payload.

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
- **Type chips** (`.cmt-chip`, with `.cmt-chip-changes` / `.cmt-chip-info` /
  `.cmt-chip-suggestion`) — reticle controls; the selected chip carries `.is-on` and
  recolors `--c` to `--orange` (changes), `--violet` (info), or `--accent`
  (suggestion). The suggestion chip is review-mode only.
- **Replacement field** (`.cmt-pop-repl`, on `.note-field`) — the reviewer's exact
  wording, revealed only while the suggestion chip is on (#166).
- **Save / cancel** (`.cmt-save`, `.cmt-cancel`) — reticle controls; save reads
  affirmative (`--c: var(--teal)`), cancel stays muted.
- **Inline highlight** (`mark.cmt-hl-changes`, `mark.cmt-hl-info`,
  `mark.cmt-hl-suggestion`) — the anchored span in the section body gets a `2px`
  colored bottom border and the matching `*-bg` wash (`--accent-dim` for a
  suggestion: the reviewer's own ink over the author's).
- **Comment list** (`.comment-list` → `.cmt` rows) — this round's freshly added
  comments. Each row: `.cmt-type` (mono, uppercase, colored by verdict), `.cmt-quote`
  (italic muted excerpt), `.cmt-note` (the note text), and a `.cmt-del` remove button.
  Rows divide with `1px solid var(--border)`.
- **Suggested wording** (`.cmt-repl`) — the replacement, arrow-led on its own line
  under the note it belongs to, in `--accent`. Used in both surfaces that show a
  comment: the comment list and a carried thread's exchange.
- **Decline** (`.exchange-d`) — the author's grounds for not complying with a
  carried turn, `⊘`-led between the request (`.exchange-q`) and the response
  (`.exchange-a`), `--text2` text on a `--orange` rule. Its thread head reads
  `declined` and takes the same ink (`.open-thread.is-declined`) — unresolved,
  so it keeps the settle button and the reply box: the reviewer accepts or
  insists. Not a verdict ink change; the three verdict colors are untouched.

## Blueprint elements (#69, v1.11.0)

Drafting-room gestures that extend the metaphor. All square, all monospace.
(The drawing sheet itself — `#paper` and its coordinate/corner decoration —
is the ground these gestures sit on; see Layout.)

- **Revision triangle** (`.rev-tri`) — drafting's "this region changed at this rev"
  flag. Rendered as `△ NN` in Fragment Mono, `11px`, `color: var(--orange)`, keyed to
  the titleblock REV. Shown on a section head only when the section carries a diff.
  When the section's *cumulative* revision count this session reaches 2+, a
  `.rev-mult` child span appends a multiplier inside the same element — e.g.
  `△ 03 2×` — styled per the label convention (`9px` Fragment Mono, inherited;
  `color: var(--text3)`, not the triangle's own orange). One visual element, two
  pieces of information (issue #141): a section revised exactly once still shows
  the plain `△ NN`, no multiplier. Decorative text, not interactive. The count is
  computed server-side from `.viva/review-input-r{N}.json` round files — never
  persisted as a schema field (see JSON protocol conventions below).
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
stats area (left) and btn-group (right). The stats area also holds the
`.prefs-toggle` button (#142) — see Preferences panel below.

Submit button states:
- `btn-submit disabled` — visually grayed, cursor not-allowed, click blocked in handler.
- `btn-submit ready` — `var(--accent)` background, glow shadow, slightly raised on hover.

## Preferences panel (issue #142, unreleased)

A `.prefs-toggle` button sits in the bottom bar's `#stats-area`, alongside the
approved/pending counters — a static label ("preferences"), never an
interpolated count in its own text, so it never competes with the counters
for that region's `aria-live="polite"` announcement. It ships `display:none`
and is revealed only once the boot fetch confirms the store holds at least
one preference — the same empty-store treatment the confidence-sort toggle
already gets (a clone with nothing to inspect or mute gets no control that
opens onto an empty panel). Clicking it opens
`#prefs-overlay`, a second modal built on the exact `openRecap`/`closeRecap`/
`setBackgroundInert` shape the Recap overlay established: `role="dialog"
aria-modal="true"`, Escape/backdrop-click/`×` all dismiss it, the background
(`#paper`, `#bottom-bar-el`, the skip link) goes `inert` while it's open, and
focus lands inside on open and returns to whichever control opened it — the
bottom-bar toggle, or an annotation badge that jumped to a specific entry —
on close. Only one of the two overlays is ever open at a time: opening
either closes the other first. Unlike the recap overlay, it's reachable in
every mode (review, diff, qa), since it lives in the one shared bottom bar
and preferences aren't review-specific.

**Contents.** Every preference from `GET /preferences` (all statuses,
label-sorted server-side via `preferences.select(store, "all")`), each row
carrying its status (`.pref-status-standing|candidate|muted`), label,
guidance, and observation/session detail — `.pref-meta` renders the
observation count *and* the sessions that reinforced it (the issue's own
proposal text, wider than the acceptance criterion's floor). Only a
`standing` row renders a **mute**
control (`.pref-mute-btn`) — `candidate` rows are read-only (pre-flight
never reads them) and `muted` rows already are. A successful
`POST /preferences/mute` updates that one row's DOM node in place — status
text, mute button removed, a muted-row note appended — never a full-list
rebuild, and announces the change through `#prefs-status`, a dedicated
one-line `aria-live="polite"` element.

**`#prefs-list` itself does not carry `aria-live`** — this is a deliberate
departure from a naive "the list is the live region" reading: scoping the
live announcement to the one-line status instead of the whole list avoids a
screen reader reading out every row's text on open, not just the one status
change after a mute.

**Muted-row copy.** Every `muted` row carries two static lines: that badges
already shown this round stay as a record and nothing further is flagged or
applied for that preference (no "next session" claim — `--status standing`
has three SKILL.md readers, not one, including step 4's post-submit rewrite
consult, so a mute during round N can still reach round N's own rewrite; the
only true claim is "not retroactive to a badge already on screen") and the
terminal command that reverses it. That command interpolates the server's
own resolved path (`Path(__file__).resolve().parent / "scripts" /
"preferences.py"`, `server.py:28-41`) rather than the shell variable
`$VIVA_DIR` — that name is local to the `find` SKILL.md's own bash block
computes it with (viva `SKILL.md`, Invocation) and is never
exported, so a literal `"$VIVA_DIR/..."` pasted into a fresh terminal 404s —
mute is one-way from this panel (decision prefs-inspector-1), so the
recovery path has to be visible on the row, and runnable, not just known to
exist.

**Badge-to-entry link.** A `kind:"preference"` annotation's `.annot-jump`
badge (the existing anchor-jump control, extended) grows a second variant
when the leading `[id]` token in its message (SKILL.md's own encoding
convention) matches a fetched preference; clicking it opens the panel
scrolled to and focused on that row (`.pref-row[tabindex="-1"]`, given a
visible `:focus` ring — not `:focus-visible`, since the jump lands via a
programmatic `.focus()` call after a mouse click, a case `:focus-visible`
generally suppresses — so the ring confirms where the jump landed
regardless of input method). `PREFS_DATA`/`PREFS_BY_ID` are fetched once at boot, alongside
`/input` (`Promise.all`, never sequential), and reused for every card build
after — including the SSE `round` rebuild on round 2+ — never re-fetched
mid-session, so a badge stays linked across rounds without a second
round-trip per render. No match (a stale or malformed token) falls back to
the annotation's plain, non-interactive rendering — the same degrade an
unmatched anchor already gets.

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
- A `kind:"preference"` annotation encodes its preference id as a leading `[id]` token in `message` (e.g. `[cite-sources] "80% faster" has no source`) rather than a structured field — `annotate.py`'s merge whitelist has no generic passthrough, so widening it for one more field is scope a message-embedded convention doesn't need. The browser resolves the token client-side against `GET /preferences` to render the badge-to-entry link (see Preferences panel).
- `GET /preferences` returns every preference — all statuses, `preferences.select(store, "all")` — as a bare JSON array, matching `preferences.py list --format json`'s own output shape (no wrapping object). `POST /preferences/mute` takes `{"id": "<pref-id>"}` and flips that preference's status to `muted` via `preferences.set_status()` (404 if the id isn't in the store). Neither route is part of the round-file (`review-input-r{N}.json`/`review-r{N}.json`) schema — both read and write `.viva/preferences.json` directly, independent of the round in progress.
- **`anchor` is overloaded — three semantics by context.** The name is reused across the input and output schemas with different meanings and consumers; keep them straight when adding an annotation kind or a consumer:
  - *Annotation, display* (input) — a string rendered as the badge's hover `title` attribute.
  - *Annotation, navigation* (input) — when the string matches another section's `id` (the cross-section contradiction producer), it renders as a `.annot-jump` deep-link to that section instead of a hover title.
  - *Comment, selection* (output) — a `comment.anchor` object `{text, offset, occurrence?}`: the exact text the reviewer selected in the section, which the agent uses to scope its rewrite. The selection exists only in the rendered HTML, so `occurrence` is the 0-based index of that selection among the identical matches **there**, and `offset` is that same ordinal resolved against the markdown source (`-1` when it does not resolve — the two sequences can diverge over markdown syntax the renderer strips). The ordinal is what makes the on-screen highlight and the stored offset name one span, and the only thing that survives a re-render; a repeated phrase looked up by `anchor.text` alone lands on the first match, which is the bug #95 fixed. This is a structured object, not a string, and lives on comments in `review-r{N}.json` — a different shape from the annotation `anchor` above.
- `GET /input` returns the current review-input merged with `ledger: [...]` — the live running ledger. The `ledger` field is injected by the server at serve time and is **not** part of the `review-input-r{N}.json` file schema that `parse_sections.py` writes.
- Both `GET /input` and the `round` SSE event similarly attach `revision_count` to a section object — but only when that section's cumulative revision count this session reaches 2+ (issue #141). Injected by the server at serve time, derived by re-reading `.viva/review-input-r{N}.json` round files already on disk; never written to any round file and not part of `ReviewSection`.
- The round shapes are the system's load-bearing contract, defined in one place: `scripts/schema.py` holds the TypedDicts, `section_key()` (the single section-identity normalization), `verdict_to_ledger_entry()` (the single ledger-row rule), and the boundary validators. Adding a field means updating that module and validating at the boundary (on parse write, on server read) — never at the point of use.
