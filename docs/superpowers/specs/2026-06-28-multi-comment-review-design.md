# Multiple inline comments per section in viva review

**Date:** 2026-06-28
**Status:** Designed
**Branch:** `worktree-anchor-updates`
**Baseline:** current `main` through #65 — the open-notes control is now a styled
**pin button** (`.pin-btn`, `#rpin-`, `.is-pinned`, sets `verdicts[id].open`) on
the anchor row, not a checkbox. This design **retires that pin button** (see
Threading), since under the chosen model every comment is a thread by default.

## Problem

A viva section card today carries exactly **one** verdict: the reviewer picks
`approve` / `request changes` / `need info` / `skip`, writes one note, and
optionally pins one anchor — a single selected span the agent greps for to
scope the rewrite (`server.py` ~1570–1632, submit payload ~1923). A real
section usually has more than one issue: a wrong number in line 2, an unclear
claim in line 7, a missing rollback step at the end. The reviewer can only flag
one per round, so the rest wait for later rounds or get crammed into one note
that the agent then has to disentangle.

We want GitHub-PR-style inline commenting: select any span, leave a typed
comment, repeat — many comments per section, each its own thread.

## Goal

Let a reviewer leave **N typed, independently-threaded comments per section** in
one round, each optionally anchored to a selected span, and have the agent apply
each as a targeted edit.

This is deliberately built by **extending two things viva already has** rather
than adding a commenting subsystem:

- **anchors** — one span per section today → many.
- **open notes** — threads that persist across rounds and already coexist with
  section approval → the per-comment thread unit.

A GitHub inline comment *is* an anchored thread; the design collapses to "anchors
become many, each binds a thread."

## Non-goals

- No margin rail / Google-Docs layout. Threads stack under the section body
  (chosen interaction model A). Cards are collapsible and often narrow; a rail
  is the most layout-fragile option in hand-rendered vanilla JS.
- No replacement of the round loop, the global "submit all", attachments,
  annotations/producers, confidence triage, or learned preferences. Comments
  compose with all of them unchanged.
- No rich-text or markdown inside a comment note beyond what the note field
  holds today.
- No cross-section comments. A comment belongs to exactly one section.

## Architecture

Data flow today: browser collects one `{verdict, note, anchor?}` per section →
POSTs to `/submit` → server writes `review-r{N}.json` → the agent reads it,
rewrites, re-parses, and `POST /next-round` re-arms the running server. Open
threads ride alongside in `.viva/open-notes.json`.

We change the **unit** from one note-per-section to a **list of comments per
section**, and re-key the existing open-note thread store from section title to
a stable per-comment id. Everything downstream (round re-arm, revision history,
producers) keeps its shape; it just iterates a list where it read a scalar.

### Verdict is derived, not chosen (section = gate)

The four-button verdict row collapses to a single **toggling primary button**:

- **0 comments → `✓ approve`** — signs the section off.
- **≥1 comment → `✓ done · N comments`** — records the section's comments and
  advances.

The section's `verdict` is no longer something the reviewer picks; it is
**derived** at submit:

- `comments` empty → `approved`.
- any comment `type: changes` → section `changes`.
- otherwise (only `info` comments) → section `info`.

Approval and feedback are therefore mutually exclusive in a round, which is what
the rewrite loop wants: comment → agent rewrites → section re-presents → approve
next round. `skip` (→ `pending`) stays as a secondary action for "decide later".

## Data model

Per-section `anchor: string` becomes `comments: []`. One comment:

```json
{
  "cid": "s3-c1",
  "type": "changes",
  "note": "should be 5× per the runbook",
  "anchor": { "text": "retries 3× on timeout", "offset": 412 },
  "open": true,
  "settled": false
}
```

- `cid` *(required)* — stable per-comment id, the thread key that replaces
  open-notes' section-title key. Assigned `{sectionId}-c{n}` at creation; once a
  thread carries forward, its `cid` is preserved across rounds independent of the
  section's positional id.
- `type` *(required)* — `changes` | `info`. Drives the highlight color and the
  rewrite action, exactly as the old per-section verdict did.
- `note` *(required)* — the instruction / question, same semantics as today's
  note.
- `anchor` *(optional)* — `{ text, offset }`. `text` is the selected span;
  `offset` is its character position within the section's source. **The offset
  is what makes multiple anchors unambiguous** — the same phrase can appear
  twice, or spans can overlap, and a grep on text alone (today's mechanism)
  cannot tell them apart. An **absent** `anchor` is a whole-section comment (the
  un-anchored note path preserved from today).
- `open` — every comment is an open thread **by default** (chosen threading
  model). This inverts the pre-#65 opt-in model, so the **pin button is retired**
  (see Threading); there is no longer anything to opt into.
- `settled` — set when the reviewer hits **settle** on the thread, or implicitly
  when the section is approved. Reuses the existing settle vocabulary
  (`settle-btn`, `settleOpenNotes`, the `"settle"` submit flag); a settled thread
  drops from later rounds.

### Submit payload

`submitReview` ships, per section:

```json
{ "id": "s3", "verdict": "changes", "comments": [ {…}, {…} ] }
```

Threading state lives **on each comment** (`open`, `settled`), not on the
section — the per-section `open`/`settle` flags of the old single-note model are
removed, since a section now owns a *set* of threads rather than one. The
`comments[]` array carries both freshly-added comments and the current settle
state of any thread carried forward from a prior round (by `cid`), so
`open_notes.py` can reconcile each thread from one place. `verdict` is the
derived roll-up above (so the agent's existing verdict switch keeps working with
zero changes for the empty-comments / approved case). A section with no comments
serializes `{ id, verdict: "approved" }` — byte-identical intent to today's
approved card.

## Components

### 1. Interaction — model A (browser)

- **Select text** in a rendered section → a small **popover** appears at the
  selection with two type chips (`changes` / `info`) and a note field. Choose a
  type, type the note, save → the span gets a colored **highlight** (red for
  changes, blue for info) and a **thread** appends to the list beneath the
  section body.
- **`+ add note`** affordance under the body creates an **un-anchored** comment
  (whole-section note) through the same popover, minus the highlight.
- Each thread shows its type, its quoted span (if anchored), and the note, with
  **edit / delete** before submit. Clicking a highlight scrolls to its thread;
  clicking a thread flashes its highlight.
- Keyboard: with a live selection, `c` / `i` open the popover pre-typed to that
  type; `a` approves the section (unchanged). The existing per-section hotkeys
  keep working when there is no selection.
- State moves from `rState.verdicts[id].anchor` (scalar) to
  `rState.verdicts[id].comments` (array); the `note`/`anchor`/`open` scalars are
  removed from the per-section object (attachments stay). Threading is now
  per-comment, so the section-level `open` no longer exists.
- **Anchor-row rework.** Today the row holds three buttons — `📎 attach`,
  `⚓ anchor selection`, `📌 pin note to next round` (`.pin-btn`, added by #65).
  In the new model the **pin button is removed** (threads are default-open, see
  Threading) and the **anchor-selection button is replaced** by the
  select→popover gesture; `📎 attach` stays. Settling moves onto each thread (the
  existing `settle-btn` in `openThreadHTML`, now one per `cid`).

### 2. Verdict button (browser)

- Replace the `approve / changes / info / skip` row with one primary button
  whose label and color derive from `comments.length` (`approve` green ⇄
  `done · N comments` blue) plus the secondary `skip`.
- `submitReview` builds each section object as above: derive `verdict`, emit
  `comments` (each carrying its own `open`/`settled`), keep `images`. The old
  section-level `open`/`settle` flags are gone — threading is per-comment.

### 3. Thread store — re-key open notes (`scripts/open_notes.py`)

- The single writer of `.viva/open-notes.json`. Today it keys threads by
  normalized section **title**; re-key by **`cid`** so each comment is its own
  thread. A section now owns a *set* of threads.
- `update` takes per-thread `--response "<cid>=<what changed>"` lines (was
  per-section). Approving a section settles all of its still-open `cid` threads;
  the per-thread `settle-btn` settles just that `cid`.
- `revision_history.py` folds **every** thread's full exchange (round-by-round
  note → agent response, with the original quoted span) into the **Open notes**
  ledger subsection, keyed by `cid` under its section.

### 4. Carry-forward (`scripts/parse_sections.py`)

- Carry unresolved threads forward by `cid` (not by section position), attaching
  each to its section's card with the prior exchange shown — same mechanism as
  today's `--open-notes`, iterated per comment.
- **Anchor stability across a rewrite:** a thread stores its original quoted
  span **immutably** as context. After the agent rewrites, the live highlight is
  re-attached **best-effort** by matching the stored `text` in the new source;
  if the span is gone, the thread still renders with its quote so the
  conversation never loses its referent. Re-anchoring never blocks; a missed
  match just means no highlight that round.

### 5. Skill consumption (SKILL.md)

This is what makes the feature real. The verdict table's `changes` / `info` rows
change from "rewrite the section using `note`" to:

> For each comment in the section's `comments[]`: apply its `note` as a targeted
> edit at its `anchor.offset` (or, for `info`, answer the question and fold the
> answer in at that span). An un-anchored comment scopes to the whole section.

- Rewrite (step 4) loops over `comments[]` instead of reading one `note` +
  `anchor`. The offset locates the edit; `text` confirms it.
- Open-notes update calls (steps 4–5) pass per-`cid` `--response` lines.
- The Anchors, Open-notes, and verdict-table sections of SKILL.md are rewritten
  to describe the list model; the single-`anchor` prose is removed.

## Backward compatibility

- A `review-input` with no `comments` on any section renders exactly as today
  (one approve button per card, no popover until a selection is made).
- A legacy verdict carrying a single scalar `anchor`/`note` (e.g. an in-flight
  round file) is read as a one-element `comments` array, so no round file format
  break.
- Un-anchored notes are just anchorless comments — today's primary path is a
  strict subset of the new model. No regression.
- Attachments, annotations, confidence sort, diffs, and learned preferences are
  untouched; comments are additive to each section object.

## Error handling

- Empty note on save → popover refuses to save (a comment must carry an
  instruction), mirroring today's "anchor with nothing selected" nudge.
- Offset that no longer matches on a later round → fall back to a text grep,
  then to whole-section scope; never fail the rewrite.
- Two comments anchored to the **same** span → allowed; each is its own thread,
  disambiguated by `cid`, both surfaced to the agent.
- A submit with zero comments across all sections → output is intent-identical
  to today's all-approved round.

## Testing

Extend `tests/test_server_anchored_notes.py` and the open-notes / parse tests:

- Submit a section with two anchored comments (`changes` + `info`) → payload has
  `comments[2]`, derived `verdict: "changes"`, each comment carries `cid`,
  `type`, `note`, `anchor.offset`.
- Two comments on the **same** span → two distinct `cid`s, both preserved.
- Un-anchored comment → `comments[i]` with no `anchor`, derived verdict correct.
- Zero comments → `{ verdict: "approved" }`, no `comments` key noise; output
  intent-identical to current approved card.
- Legacy single-`anchor` verdict → parsed as one-element `comments`.
- `open_notes.py`: per-`cid` threads carry forward, settle-on-approve settles
  all of a section's threads, an explicit per-thread settle settles one.
- `test_server_open_notes.py`: update the #65 pin-btn needles (`rpin-`, "pin note
  to next round") — the pin control is removed, so those assertions go; add
  coverage that every comment threads by default with no pin step.
- `parse_sections.py`: a thread whose span survived a rewrite re-anchors; a
  thread whose span was deleted still re-presents with its quote.
- `revision_history.py`: multiple per-`cid` threads under one section fold into
  the Open notes ledger.

## Open questions

None blocking. To finalize in the implementation plan: the exact popover
placement math (selection rect vs card-relative), and whether `offset` is a
char index into the raw section source or into the rendered text (raw source is
the rewrite target, so likely the former).
