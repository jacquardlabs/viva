# Design: Show per-section revision depth in the card header

Source: issue #141. Epic: reviewer-experience. Story slug: revision-depth.

## Problem & persona

Consumer: the human deciding to fund the work; product-reviewer Q1.

> A developer who must sign off on a doc an agent produced and refuses to
> rubber-stamp it. Wants to see each section verbatim, leave one or more typed
> comments per section (down to a line anchor), attach a screenshot, ask a
> question, keep a thread open across rounds, and have a verbatim revision
> ledger at the end. Reviews many docs, so wants recurring critiques learned
> rather than re-typed.

Today the active card header's revision triangle (`.rev-tri`, `server.py:2199`)
tells this reviewer exactly one thing: "this section changed since the round
you're looking at now" — `△ 03` means round 3 rewrote it, and nothing more.
Across the multi-round session PRODUCT.md's persona lives in — someone who
"refuses to rubber-stamp" and works section by section, round by round — the
one thing that triangle can't currently tell them is whether this section
already bounced through two or three rounds of back-and-forth before landing
on the draft in front of them now. That's exactly the section a careful
reviewer wants to slow down on before approving, and today the only way to
find out is to open the Revision History ledger, which doesn't exist until
sign-off (`scripts/revision_history.py`), or to remember it themselves,
round to round. Issue #141 names this gap directly: the card shows *that* a
section moved, never *how many times*.

## Proposed design

Consumer: product-reviewer Q2/Q6; `/plan`'s spine-building step.

The existing round badge is untouched: a section revised this round still
shows `△ NN` (`NN` = the current round, zero-padded), on the same element, in
the same place, with the same orange (blueprint-triangle) styling, exactly
when `section.diff` is truthy — the same trigger condition `server.py:2199`
already uses. This story adds exactly one thing to that element: when the
section's *cumulative* revision count for this session reaches 2 or more, a
second run of text appends inside the same `.rev-tri` element — a small
multiplier such as `2×` — styled per DESIGN.md's shared label convention
(8–10px Fragment Mono, `letter-spacing: 0.08–0.16em`, `var(--text3)`), not the
triangle's own orange. One visual element, two pieces of information, per
decision revision-depth-1 ("△ 3× — not a separate R3 badge"). A section
revised exactly once keeps today's plain `△ NN` — no multiplier renders below
a cumulative count of 2, per the acceptance criteria.

**Where the count is computed.** Server-side, in `server.py` (the SPA host),
at the two points it already hands section data to the browser: `GET /input`
(initial load / reload) and the `round` SSE event `POST /next-round` pushes
mid-session. This mirrors the one existing precedent for a wire-only,
non-schema field: `ledger` on `GET /input` is "injected by the server at
serve time and is **not** part of the `review-input-r{N}.json` file schema
that `parse_sections.py` writes" (CLAUDE.md, JSON protocol conventions). The
revision count follows the identical rule — no change to `scripts/schema.py`'s
`ReviewSection` TypedDict, no new key `parse_sections.py` or `parse_diff.py`
writes to disk, no coordinated-edit obligation under CLAUDE.md's "adding a
field to the round schema" rule, because nothing is added to that schema.

**How the count is computed.** For the round `N` currently being served, walk
the *historical* rounds `k = 1 .. N-1` and read `.viva/review-input-r{k}.json`
from the directory holding the session's round files — the same per-round
file naming `scripts/revision_history.py:76-98` already depends on to build
the Revision History ledger at sign-off. For each historical round, look up
the section by `schema.section_key(title)` (`scripts/schema.py:36-47`, the
one section-identity normalization this whole codebase already keys on) and
count one revision if that round's own JSON carries a `diff` for it — `diff`
is already the exact "this round's content differs from the prior round's"
marker `scripts/parse_sections.py:307-324` (`_compute_diffs`) writes today.
Add one more for the just-arrived round `N` itself, read directly from the
payload the server already holds in memory (`_input_data`, or the freshly
POSTed body at `/next-round`) rather than re-read from disk — the server's
own `--input`/`--output` contract treats those paths as opaque strings (see
Alternatives considered), so the current round is the one round the
computation must trust from the hand-off itself, not from a file glob.

| Round | This round's `diff`? | Historical rounds counted | Cumulative | Card shows |
|-------|-----------------------|----------------------------|------------|------------|
| 1 | no (nothing to compare against yet) | — | 0 | no triangle |
| 2 | yes — first rewrite | r1: no | 1 | `△ 02` |
| 3 | yes — reviewer asked again | r1: no, r2: yes | 2 | `△ 03 2×` |
| 4 (approved, unchanged) | — | — | — | carried card, no badge |

**Decorative, not interactive.** The multiplier is plain text content added
to an existing non-interactive `<span>` — today's `.rev-tri` already carries a
`title` attribute and an `aria-hidden` glyph but is not a button or a link,
and this story keeps it that way: no click handler, no expand-in-place, no
new focus target. DESIGN.md Accessibility req #1 only binds an *interactive*
element to the `:focus-visible` group rule; because this stays decorative
text, that requirement doesn't apply, and no new a11y treatment is needed
beyond what the element already has. Every color is a token
(`var(--text3)` for the count, the triangle's existing `var(--orange)`
untouched) — req #7, no hardcoded hex.

Leans on PRODUCT.md principle 3 ("Advisory, never gating" — the count
decorates, never affects a verdict), principle 4 ("No-op when absent" — a
missing or unparseable historical round file makes that round contribute
zero, never an error, so the worst case is a `.rev-tri` tooltip that flags
the round as having partial history — "≥N revisions, partial history" when
a count still clears the threshold, a number-free "partial history, revision
count unavailable" when it doesn't — instead of asserting a number it can no
longer vouch for, or silently showing no signal at all), and principle 6
("Local and keyless" — computed in-process from files already on the
reviewer's machine, no new service, no telemetry).

## User journey

Consumer: product-reviewer Q3; `/plan`'s task-boundary decisions.

This extends the existing "Round-to-round section diff on rewritten cards"
feature (PRODUCT.md's feature map) — same loop, one richer card.

1. **Round 1.** The agent parses `spec.md`, launches the server. No section
   has a prior round to diff against, so no card shows a triangle — unchanged
   from today.
2. **Round 2.** The reviewing human leaves a `changes` comment on "Error
   Handling". The agent rewrites and re-parses; `review-input-r2.json`'s
   entry for that section carries a `diff` against round 1. The card shows
   `△ 02` — cumulative count is 1, so no multiplier, identical to today.
3. **Round 3.** The reviewer isn't satisfied and leaves another `changes`
   comment on the same section. The agent rewrites again; round 3's entry
   carries a `diff` against round 2. The card now shows `△ 03 2×` — the
   reviewer sees immediately, without opening the Revision History or
   scrolling back, that this section already bounced once before this draft.
4. **Round 4.** The reviewer approves "Error Handling". Content going into
   round 4 is unchanged, so per decision revision-depth-2 its card collapses
   into a carried card (`buildCarriedCard`, `server.py:2277`) and shows no
   badge at all — the same minimal treatment carried cards already get. The
   cumulative count isn't lost, only hidden; it would resurface if the
   reviewer later withdrew the approval and the section changed again.
5. **Failure path — cross-session resume.** The reviewer resumes review days
   later, after hand-editing the doc outside viva, in a brand-new session.
   Round 1 of that new session is the only round file that exists (state
   clears between sessions); it may itself carry a `diff` against the prior
   session's finishing round. That single diff counts as 1 — a plain
   triangle, no multiplier — because history from the earlier session was
   never persisted as round files this session can read. This matches every
   other session boundary already in this codebase.
6. **Failure path — unreadable round file.** If `.viva/review-input-r2.json`
   is missing, its JSON fails to parse, or it parses but its `sections` key
   isn't a list, by the time round 4 is served (a truncated write, an
   unconventional caller that never wrote that file), that round simply
   contributes zero rather than raising an error or stalling the round — but
   because the total is now a lower bound, not an exact count, every section
   with a `diff` this round (`_with_revision_counts`, `server.py`) carries
   that caveat, not only ones whose count still clears the 2+ threshold. A
   section whose count reaches 2+ shows "≥N revisions, partial history" in
   place of "N content revisions this session"; a section whose count would
   have crossed 2+ *only* via the unreadable round — the multiplier that
   would otherwise vanish with no signal at all — instead shows a
   number-free "partial history, revision count unavailable" caveat on its
   still-bare `△ NN`. The triangle and multiplier glyph themselves are
   unchanged in either case — only the tooltip's wording degrades.
7. **Failure path — retitled section.** The count keys on
   `schema.section_key(title)` (`server.py:3678`, `server.py:3724`) — the
   same identity approvals and carried annotations already use (CLAUDE.md,
   "The schema is the contract"), not the section's `id`
   (`scripts/parse_sections.py:173` assigns `id` positionally as `s{i+1}`,
   which is less stable across a reorder than the title). A section revised
   at rounds 2 and 3 (cumulative 2, `△ 03 2×`) that gets both retitled *and*
   content-edited at round 4 breaks that key: `_compute_diffs`'s title
   lookup (`scripts/parse_sections.py:317-324`) finds no prior-round match
   under the new title, so round 4 gets no `diff` and no triangle at all —
   the same treatment any brand-new section already gets. If round 5 leaves
   the new title unchanged, round 5's `diff` lands against round 4 and the
   card shows a bare `△ 05` — but `_revision_counts`'s historical walk
   (`server.py:3657-3681`) finds no round keyed under the new title before
   round 4, so the cumulative count resets to 1. A section revised four
   times total then renders as if revised once. This is not a defect to fix
   here — it is the same title-identity behavior every other carried-forward
   signal in this codebase already has — but it does mean the acceptance
   criterion "a section revised 2+ times shows a cumulative count" does not
   hold across a retitle.

## Out of scope

- **Diff mode (`/viva-diff`) and Q&A mode.** `scripts/parse_diff.py` never
  sets a `diff` key on hunk sections — there's no round-to-round content
  comparison for diff-mode sections at all — so the triangle, and therefore
  the count, structurally cannot render there. No change to diff or Q&A card
  building.
- **Carried (collapsed, already-approved) cards.** Decision
  revision-depth-2. Already structurally excluded today: carry-forward
  requires byte-identical content while `diff` requires a content change, so
  the two states are already mutually exclusive — this story doesn't add new
  logic to enforce that, only preserves it.
- **Cross-session or lifetime revision history.** Round files reset at the
  start of every new session (CLAUDE.md's "State lifecycle" — everything
  under `.viva/` except `preferences.json` is disposable and reset each
  session). The count is scoped to the current session only, same as the
  round badge and the Revision History ledger already are.
- **Interactivity.** No click-to-expand round history, no link to the
  Revision History ledger. A future story could add that deliberately (and
  would then owe req #1's `:focus-visible` treatment); this story doesn't
  smuggle it into a label.
- **Changing the existing triangle's trigger, color, position, or the round
  badge's meaning.** All untouched.
- **Any new persisted field.** No `schema.py` TypedDict change, no new key
  `parse_sections.py` or `parse_diff.py` writes — the acceptance criteria
  rule this out explicitly, and the count is fully re-derivable from the
  `diff` markers already on disk.

## Alternatives considered

- **In-memory accumulation** — bump a per-section counter in the server
  process's memory every time `/next-round` delivers a section with a fresh
  `diff`, instead of re-deriving from round files. Rejected: not idempotent.
  SKILL.md's own round-advance step pipes a freshly-parsed round file into
  `curl -X POST .../next-round`; a retried or duplicated POST (a flaky
  network hop, a re-run block) redelivers the same round's data and would
  double-count a revision that only happened once. A wrong, inflated count on
  screen is a worse failure than an absent one — principle 4 asks for a
  graceful degrade, not a plausible-looking lie.
- **Ledger-derived counting** — count `changes`/`info` ledger rows per
  section, reusing `schema.verdict_to_ledger_entry`. Rejected: it measures a
  different quantity. A `changes` comment always produces one ledger row and
  one content edit, so those move together — but an `info` comment (a
  question, no edit, per SKILL.md's hybrid rule) also produces a ledger row
  with zero content change, and two separate `changes` comments landing on
  the same section in one round still yield exactly one content revision,
  not two. The ledger counts reviewer requests; this badge needs to count
  content revisions actually delivered.
- **Persist the count as a new schema field** parse_sections.py writes onto
  each section (e.g. a `revision_count` key baked into
  `review-input-r{N}.json`). Rejected: the acceptance criteria rule this out
  by name, and it would add state to keep in sync across `schema.py`'s
  TypedDict, `parse_sections.py`, `parse_diff.py`, and every downstream
  reader, for a value already fully reconstructable from the `diff` markers
  those same files carry today.
- **Derive the current round from disk too**, by globbing every
  `review-input-r*.json` in `.viva/` rather than trusting the just-delivered
  payload for round `N`. Rejected as the *sole* source for the current round:
  `server.py`'s own `--input`/`--output` flags are opaque paths it never
  assumes are named `review-input-r{N}.json` — only `revision_history.py`,
  invoked separately by SKILL.md with an explicit `--viva-dir`, depends on
  that naming convention. `tests/test_server_ledger.py` already launches the
  server against arbitrarily-named fixture files (`in1.json`, `out1.json`)
  and POSTs round 2's data to `/next-round` without ever writing a
  `review-input-r2.json` to disk at all — proof the server-facing contract
  makes no such promise. Reading only *historical* rounds (1..N-1) from disk
  keeps the ledger precedent (derive from round files, don't accumulate) for
  the data that's safely stable, while trusting the in-hand payload for the
  one round that might not exist on disk yet under that naming.

## Success metrics

N/A — no measurable surface. PRODUCT.md: "A single stdlib-only Python
server, one browser tab, no API key, no hosted service" — viva has no
logging or analytics surface a badge's usage could be read from, and this
story adds none. The qualitative check is the same one every other UI change
in this project gets: the reviewing human notices (or doesn't need to) during
normal use, and a wrong or missing badge is exactly the kind of thing that
shows up immediately in dogfooding a multi-round review, not something that
needs an instrumented funnel.

## Operational readiness

N/A — no migration and no rollout beyond the normal plugin version bump. The
count is a stateless, serve-time-computed decoration over round files
already on disk; there's no new file, no new endpoint, and no schema version
to migrate. If a session's round files don't follow the
`review-input-r{N}.json` naming, or a historical one is missing, corrupt, or
carries a non-list `sections` key, that round's computation contributes zero
rather than raising — but unlike `scripts/revision_history.py`'s tolerance
for a session with gaps in its round-file pairs, this isn't a silent
fallback to exactly today's plain-triangle rendering: `_revision_counts`
(`server.py`) tracks that the total is now a lower bound and
`_with_revision_counts` threads that through as `revision_count_partial` on
every section with a `diff` this round, not only ones whose count still
clears the 2+ threshold — a below-threshold section gets the caveat too,
because the unreadable round could just as easily have been the one that
would have pushed it over 2. A section whose count clears the threshold
shows "≥N revisions, partial history" in its `.rev-tri` tooltip; one that
doesn't shows a number-free "partial history, revision count unavailable"
instead of a bare, uncaveated `△ NN`. Either way, the server never asserts a
number it can no longer vouch for, and never lets a possible undercount pass
completely unsignaled. That is the one new failure mode this story adds an
alarm for; `tests/test_server_revision_count.py` exercises both the
at-threshold and below-threshold cases directly.

## Open questions

- **Resolved (acceptance-fix-2).** Checked whether the multiplier's growth
  crushes `.card-title` the way #97 crushed it for `.vbadge`: it doesn't, and
  the fix isn't the same shape. `.vbadge` was reachable for #97 because Q&A
  mode put *raw user text* of unbounded length in a `flex-shrink: 0` element;
  `b4af98f` capped that badge to `max-width: 45%` with ellipsis truncation.
  `.rev-tri` carries no user text — its content is `△` + a zero-padded
  2-digit round + at most ` NN×`, so ~8 characters is its ceiling, not a
  floor. `.card-title-wrap` is `flex: 1; min-width: 0`, so as the head row
  narrows the title column yields; `.card-title` itself carries no
  `white-space: nowrap`, so a long title wraps to a second line instead of
  overflowing or overlapping `.rev-tri` — there is no crush to fix, and no
  `max-width` cap on `.rev-tri` is warranted.
  Deliberately did **not** mirror `.carried-head .card-title`'s
  single-line-ellipsis rule (`server.py:463`) onto the active card's
  `.card-title` (`server.py:552-557`), despite the visual precedent sitting
  right there: the two elements aren't the same kind of title. A carried
  card is a collapsed, read-only preview — truncating it is the point. The
  active card is the one the reviewer is actively reading, and `.card-title`
  is its *only* on-screen render of that text for a Q&A card
  (`server.py:2859`, `q.text` — the question itself, with no second render
  site in the card body); for a review section the full title does reappear,
  verbatim, in `.section-content` once expanded (the markdown heading line
  survives round-trip in `content`, per `scripts/parse_sections.py`), but
  the Q&A case alone rules out a blanket ellipsis rule on the shared class.
  Multi-line wrap on the active head is accepted, not merely tolerated: the
  head is `min-height: 48px`, not fixed-height, so it grows to fit.
  This was checked by source inspection of the flex/overflow rules and the
  `b4af98f` precedent, not a live narrow-viewport screenshot (no browser
  available in the build environment for this round) — the CSS properties
  involved (`flex: 1`/`min-width: 0` on the yielding sibling, no `nowrap` on
  the title, a bounded-length neighbor) are what determine wrap-vs-overflow
  regardless of viewport, so this is not treated as a gap.
- **Resolved (build).** Separator between the round number and the
  multiplier inside `.rev-tri`: a plain space, no punctuation —
  `` ${section.revision_count}&times; `` renders as `△ 03 2×`
  (`server.py:2226`).
- **Resolved (acceptance-fix-1, `c07061d`).** The `title="revised at REV NN"`
  tooltip now names the cumulative count at 2+: `revTriTooltip`
  (`server.py:1764-1774`) appends `· N content revisions this session` (or
  the partial-history variants added in `da3d7d6`), deliberately worded
  "content revisions" rather than bare "N revisions" so it doesn't collide
  with the sign-off stamp's existing "N revisions" wording for
  `rounds_total` — a different quantity (open ledger finding
  `revisions-word-overloaded-tooltip-vs-stamp` tracks finishing that
  differentiation elsewhere; not reopened here).
- Whether a resumed session's round-1 diff (carried over from a *prior*
  session's sign-off) should count toward this session's multiplier at all,
  versus always starting cold at round 1. This doc defaults to counting it
  as 1 — consistent with every other part of the round-file lifecycle
  resetting at a new session — flagged here in case product wants a stronger
  signal for a doc that's been hand-edited between sessions.
