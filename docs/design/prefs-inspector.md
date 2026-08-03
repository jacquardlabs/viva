# Design: Preferences inspector — view and manage learned preferences from the browser UI

Source: issue #142. Epic: reviewer-experience. Story slug: prefs-inspector.

## Problem & persona

Consumer: the human deciding to fund the work; product-reviewer Q1.

> A developer who must sign off on a doc an
> agent produced and refuses to rubber-stamp it. Wants to see each section
> verbatim, leave one or more typed comments per section (down to a line
> anchor), attach a screenshot, ask a question, keep a thread open across
> rounds, and have a verbatim revision ledger at the end. Reviews many docs, so
> wants recurring critiques learned rather than re-typed.

Learned preferences (issue #17) already close half of that loop: `preferences.py`
clusters this reviewer's recurring `changes`/`info` notes into standing critiques
and the preference producer pre-flags matching sections at round 1
(`.claude/skills/viva/SKILL.md:69-90`). What's missing is the other half —
today the only way to see what viva has learned, or to correct it, is to leave
the browser tab and run `preferences.py list`/`set` in the terminal the agent
is driving. Issue #142 names the gap directly: the reviewer can't see the
standing set, can't mute a bad pattern, and can't trace a `[cite-sources] ...`
badge on a card back to the preference that produced it, without switching
context away from the review itself. That's exactly the "reviews many docs"
reviewer PRODUCT.md describes, asked to trust a black box.

## Proposed design

Consumer: product-reviewer Q2/Q6; `/plan`'s spine-building step.

**Reachable without leaving the tab.** A new control sits in the bottom bar's
stats area (`#stats-area`, `server.py:1701-1707`), alongside the existing
approved/pending counters — decision prefs-inspector-2. It's a static-label
button (no live count baked into its own text — `#stats-area` already carries
`aria-live="polite"` for the counters beside it, and a second thing changing
in that region on every mute would double the announcement surface for no
acceptance-criterion benefit). Clicking it opens an in-page panel — not a new
tab, not a new file, per the issue's explicit constraint. The panel reuses the
same modal shape the recap overlay already established
(`server.py:1718-1729`, `openRecap`/`closeRecap`/`setBackgroundInert` at
`server.py:3191-3240`): `role="dialog"`, `aria-modal="true"`, Escape and
backdrop-click close it, the background goes `inert` while it's open, and
focus lands inside on open and returns to the triggering control on close.
Only one modal is open at a time — opening the preferences panel closes the
recap if it was open, and vice versa, the same way the SSE `round`/`processing`
handlers already close the recap proactively before replacing what's under it.

**What the panel shows.** Every preference in `.viva/preferences.json`
(`preferences.select(store, "all")`, `scripts/preferences.py:162-167`),
sorted by label — candidate, standing, and muted alike, each carrying its
status, label, guidance, and the sessions that reinforced it. This is wider
than the acceptance criterion's floor ("all standing preferences visible with
label and guidance") but matches the issue's own proposal text ("Lists all
preferences with their status... and the sessions that contributed to each"),
and it's what makes the badge-link (below) resolve reliably even after a mute
changes a preference's status mid-session. A **Mute** button appears only on
rows whose status is `standing` — the one status the pre-flight pass actually
reads (`preferences.py list --status standing`, SKILL.md:71). A `candidate`
row shows read-only; muting one would have no observable effect this story's
acceptance criterion can verify (pre-flight never reads candidates), so
offering the control there is scope the criteria don't ask for. `set_status`
itself (`scripts/preferences.py:150-159`) doesn't restrict by current status —
the restriction lives entirely in which rows render a button, not in the
route — so widening it later, if a future story wants it, is a client-only
change.

**The two routes.** `GET /preferences` returns the store's preferences (all
statuses) as JSON. `POST /preferences/mute` takes a preference id and flips
its status to `muted` via `preferences.set_status()`, the same function the
CLI's `set --status muted` already calls — the route is a second caller of
the existing pure function, not a reimplementation of the status-flip logic.
Both live in `server.py`, following the same shape every existing route
already has (loopback-only origin guard and a body-size cap on the POST,
matching `_check_origin_and_length`; `{"ok": true}` on success; `{"error":
...}` with a 4xx on failure — DESIGN.md's API conventions). No new Python
state file: both read and write the same `.viva/preferences.json`
`preferences.py` already owns.

**A second writer, named where the first one claims to be the only one.**
`scripts/preferences.py`'s module docstring currently states "This script is
the SINGLE writer of `.viva/preferences.json`" (`scripts/preferences.py:5-7`).
After this story, that's no longer accurate: `server.py`'s mute route also
writes the file, from a live browser session, while a review is in progress —
the first time any part of this codebase writes browser-driven state that
survives the round-1 clear (`preferences.json` is the one file that clear
deliberately spares, CLAUDE.md's "State lifecycle"). This story updates that
docstring to name the second writer and the narrow thing it's allowed to do —
flip an existing preference to `muted`, never create one or move it to
`standing`/`candidate` — so the invariant a future reader checks against is
the true one, not a stale claim contradicted by a route they'd have to go
find in `server.py` to discover. `scripts/preferences.py` is a declared file
for exactly this one-line update, not a logic change.

`server.py` importing a module under `scripts/` isn't a new kind of coupling:
it already imports `schema.py` the same way (`server.py:29`). CLAUDE.md's
"import no sibling except `schema.py`" rule binds `scripts/*.py` modules
importing *each other*, so each stays independently testable — it says
nothing about `server.py`, which already sits on the other side of that
boundary. Importing `preferences.py`'s pure functions is that existing
direction of dependency exercised a second time, not a new one.

**Server never reuses `preferences.py`'s CLI-shaped file I/O.**
`preferences.py`'s own loader, `_load()` (`scripts/preferences.py:170-176`),
calls `sys.exit()` on a JSON parse failure — correct for a one-shot CLI
invocation, fatal if called from inside a long-running request handler: a
single corrupt `.viva/preferences.json` would take the whole review server
down mid-session. `server.py` imports `preferences.py`'s pure, non-exiting
pieces (`select`, `set_status`, `STATUSES`) the same way it already imports
`schema.py` (`server.py:29`), but does its own tolerant read for the file
itself — missing or unparseable degrades to an empty store, matching
PRODUCT.md principle 4 ("No-op when absent") exactly, and matching how
`_revision_counts` (`server.py`, shipped in the sibling revision-depth story)
already treats a historical round file it doesn't control as possibly
missing or corrupt.

**The badge-to-entry link reuses the existing message convention, not a new
field.** SKILL.md already documents how a `kind:"preference"` annotation
carries its preference id: encoded as a leading `[pref-id]` token in the
message text, because `annotate.py`'s merge only ever copies
`kind`/`severity`/`message`/`anchor` (plus `basis`/`level`, confidence-only) —
verified directly in `_clean()`, `scripts/annotate.py:44-73`, which has no
generic passthrough for an arbitrary extra field. Adding a structured field
instead (mirroring confidence's `basis`/`level`) would mean editing that
whitelist and `schema.py`'s `Annotation` TypedDict — a coordinated schema
change this story's stated boundary (two routes, no new state) doesn't call
for when the existing convention already carries the same information.
`annotStripHTML` (`server.py:2067-2088`) already has the shape for this: an
anchor that matches a known section id renders as a clickable `.annot-jump`
button instead of a plain hover title
(`server.py:2076-2082`). The preference case mirrors it — a `kind:"preference"`
row's leading `[id]` token is checked against the fetched preferences list,
and only on a match does the row grow a jump-style control, labeled with
*that preference's own label* from the server response (e.g. "Cite a source
for every quantitative claim ↗") and targeting *that preference's own id*
field — never the raw substring pulled out of the annotation text — the same
reason the existing anchor-jump path renders `esc(anchorId)` rather than
trusting the string as already safe. No match (a stale or malformed token)
falls back to the same plain, non-interactive rendering an unmatched anchor
already gets today — no new failure shape, the existing one extended to a
second kind of lookup. A badge renders linked from first paint, not upgraded
a beat later — the preferences fetch this depends on is awaited alongside the
round data before cards are first built; a slow or failed preferences fetch
degrades every badge on that render to plain text rather than delaying the
cards themselves or leaving them to silently upgrade underneath the reviewer.

Leans on PRODUCT.md principle 3 ("Advisory, never gating" — muting changes
what a *future* round-1 pre-flight pass flags, never a verdict, never
anything about the round already on screen), principle 4 ("No-op when
absent" — an empty or corrupt store is an empty panel, not an error), and
principle 6 ("Local and keyless" — no new service, the store stays the same
plain, gitignored, per-clone JSON file a human can already hand-edit).

## User journey

Consumer: product-reviewer Q3; `/plan`'s task-boundary decisions.

1. **Standing preferences exist; round 1 auto-engages.** The reviewer opens a
   doc. The preference producer has already flagged a section with a
   `kind:"preference"` `warn` annotation reading `[cite-sources] "80% faster"
   has no source` (SKILL.md's own example) — this part is unchanged, shipped
   behavior. The card's annotation strip shows that row exactly as before, now
   with a small jump control appended, labeled with the preference's label.
2. **Reviewer wants context before deciding.** They click the jump control (or
   the new "preferences" button in the bottom bar, if they just want to browse
   the whole set). The panel opens, background goes inert, focus lands inside;
   clicking via the badge additionally scrolls to and focuses that entry's row.
3. **The entry shows what's needed to decide.** Label ("Cite a source for
   every quantitative claim"), guidance, status ("standing"), and which
   sessions reinforced it — enough to judge whether this is a critique worth
   keeping or one that's outlived its usefulness (a doc type where sourcing
   genuinely doesn't apply, say).
4. **Reviewer mutes it.** They click Mute on that row. The request completes;
   the row's status flips to `muted` in place and its Mute button disappears —
   the same row, updated, not replaced or removed, since the panel shows every
   status. The panel's list is an `aria-live` region, so a screen reader
   announces the change without the reviewer needing to re-scan the list —
   acceptance criterion's aria-live requirement (DESIGN.md req #3), extended
   here to a second "updates without a full reload" surface beyond the bottom
   bar's stat counters.
5. **Reviewer closes the panel.** Escape, the close button, or a backdrop
   click — all three, mirroring the recap overlay exactly. They land back
   where they left off; nothing about the review state (active card, scroll
   position, in-progress verdicts) was touched by any of this.
6. **Verification, not just UI feedback.** `.viva/preferences.json` on disk
   now has that preference's `status` field as `"muted"` — checkable directly,
   independent of what the browser shows, which is the acceptance criterion's
   own verification method.
7. **Effect is next-session, not retroactive.** The card that's already on
   screen keeps its badge — muting doesn't un-flag a section mid-round, only
   a *future* round-1 pre-flight pass reads `--status standing`
   (SKILL.md:71), so this session's already-rendered flags are a historical
   record of what the reviewer was shown, not a live subscription. The next
   session's round 1 simply won't re-flag anything on this critique.
8. **Failure path — corrupt or missing store.** If `.viva/preferences.json`
   doesn't exist yet (no preferences learned this clone) or fails to parse
   (a truncated write, hand-editing gone wrong), `GET /preferences` returns an
   empty list rather than erroring — the panel opens to "no preferences yet"
   and the bottom-bar button stays reachable and harmless. The server process
   itself never exits on this, in deliberate contrast to `preferences.py`'s
   own CLI loader.
9. **Failure path — mute request fails.** A dropped connection or an unknown
   id (already muted by a concurrent CLI `set` in another terminal, say)
   returns an error rather than `{"ok": true}`; the row's status and Mute
   button stay exactly as they were, and the panel surfaces that the action
   didn't take — nothing silently claims success.
10. **Failure path — badge references an id the store doesn't recognize.**
    Doesn't happen via the shipped preference producer today (it only ever
    emits an id it just read from the store), but a hand-edited round file
    could carry a stale or typo'd `[id]` token. That row renders exactly like
    an annotation anchor that doesn't match any section id today — plain
    text, a hover title if `anchor` is set, no jump control — the existing
    graceful-degrade path, not a new one.

## Out of scope

- **Un-muting from the UI.** Decision prefs-inspector-1 — un-mute stays
  CLI-only (`preferences.py set --status standing`). No third route.
- **Muting a `candidate` preference from the UI.** The route doesn't
  special-case status, so this is a client-only restriction — the Mute
  control simply isn't rendered on a candidate row in this story, because
  pre-flight never reads candidates and a criterion can't verify an
  invisible effect. A natural follow-up, not built here.
- **Editing a preference's label or guidance, or creating a new one, from
  the UI.** Read-and-mute only. Recording stays entirely agent-side, at
  sign-off (SKILL.md step 5) — this story adds no way to originate a
  preference from the browser.
- **Live sync with concurrent external edits.** The panel reflects what it
  fetched; it doesn't poll, and doesn't refetch when reopened. If a human
  hand-edits `preferences.json` in a separate terminal, or another `preferences.py`
  CLI call runs, mid-session, the panel won't reflect it until the page
  reloads. Matches the project's single-reviewer, single-tab scope
  (PRODUCT.md: "Not multi-user or hosted").
- **Diff mode and Q&A mode badge-linking.** The preference producer only
  emits `kind:"preference"` annotations in review mode (SKILL.md never
  documents it for `/viva-diff` or `/viva-qa`), so a diff or Q&A card
  structurally never carries one to link — no change needed there. The
  bottom-bar toggle itself still renders in every mode, since it lives in
  the one shared bottom bar and preferences persist across all of viva's
  usage, not just review sessions.
- **Any new persisted schema field.** No `schema.py` TypedDict change, no
  new key `annotate.py` copies — the badge link is entirely a client-side
  read of the message convention SKILL.md already documents and ships.

## Alternatives considered

- **A structured `pref_id` field on the annotation**, mirroring confidence's
  `basis`/`level`. Rejected: `annotate.py`'s merge whitelist
  (`_clean()`, `scripts/annotate.py:58-73`) copies only specific fields today;
  adding one more means editing that whitelist and `schema.py`'s `Annotation`
  TypedDict — a coordinated schema edit this story's two-route boundary
  doesn't call for when the already-shipped `[id]`-in-message convention
  carries the same information end to end.
- **`GET /preferences` filtered to `standing` only**, matching the
  acceptance criterion's literal wording. Rejected: the issue's own proposal
  asks for every status, and returning only standing would silently orphan
  a badge whose preference gets muted mid-session — the exact scenario a
  reviewer opening a badge link right after muting it would hit. Returning
  everything makes the badge link stable regardless of when the mute
  happened.
- **Reusing `preferences.py`'s `_load`/`_write` directly in `server.py`.**
  Rejected for the read path: `_load()`'s `sys.exit()` on a parse failure
  (`scripts/preferences.py:170-176`) is right for a CLI invocation and wrong
  for a request handler in a process meant to stay up for the whole review.
  `server.py` calls the pure functions (`select`, `set_status`) and owns its
  own tolerant file I/O, the same division it already has with `schema.py`.
- **A confirmation step before mute**, since un-mute isn't reachable from the
  UI. Rejected as a second new interactive surface the acceptance criteria's
  "exactly one new POST route" framing doesn't ask for. Named instead as an
  open question rather than built speculatively.

## Success metrics

N/A — no measurable surface. PRODUCT.md: "A single stdlib-only Python server,
one browser tab, no API key, no hosted service" — viva has no logging or
analytics surface a mute action's usage could be read from, and this story
adds none. The qualitative check is the same one every other UI change in
this project gets: does the reviewer actually reach for the panel instead of
switching to the terminal during normal use, and does a wrong or missing
mute-effect show up immediately in dogfooding (a muted preference that keeps
getting flagged next session would be obvious and wrong on sight).

## Operational readiness

No migration: both routes read and write the exact `.viva/preferences.json`
shape `preferences.py` already produces, including its own `"version": 1`
field, untouched. No rollout beyond the normal plugin version bump.

This is, however, the first *browser-driven* write to state that survives the
round-1 clear — every other thing the UI can do (verdicts, comments,
attachments) writes to round files that reset at the next session; a mute
persists across sessions the same way a CLI-recorded preference does. That's
new enough to name here: `scripts/preferences.py`'s docstring is updated to
document the server as a second, narrow writer (status-flip to `muted` only),
so the file's own header no longer claims a "single writer" invariant this
story breaks.

Rollback needs no tooling: the store stays plain, hand-editable JSON exactly
as it was before this story. Undoing a mute is one terminal command —
`python3 scripts/preferences.py set --store .viva/preferences.json --id <id>
--status standing` (or `--status candidate`, to put it back exactly where it
was) — the same escape hatch the preferences store has always had, since
before this story existed.

The one new failure mode this design adds explicit handling for: a missing or
corrupt `.viva/preferences.json` at request time must degrade to an empty
list, never take the server process down — deliberately *not* reusing
`preferences.py`'s own CLI loader, whose `sys.exit()` on a parse failure would
turn one bad file into an outage for a live, multi-round review session with
unsaved verdicts on screen. There's no logs/metrics surface to alarm on
(PRODUCT.md, no telemetry) — the operational signal a keyless local tool has
is synchronous and in the same interaction: a failed mute leaves the row
exactly as it was, visible immediately, not a silent drop.

## Open questions

- **Un-mute requires the terminal — named, not resolved.** Decision
  prefs-inspector-1 keeps un-mute CLI-only, which cuts directly against this
  story's own "without leaving the tab" premise for the one action a reviewer
  might most want to undo quickly (an accidental click). The recovery command
  is `python3 scripts/preferences.py set --store .viva/preferences.json --id
  <id> --status standing`. Flagged in case product wants a stronger in-UI
  affordance later; this story doesn't add one, since a second route (or a
  status parameter widening the one mute route into a general status-setter)
  is exactly the scope the acceptance criteria draw the line against.
- **Should a candidate preference be mutable from the UI too**, now that the
  route itself doesn't restrict by status? Left as a follow-up rather than
  built here — see Out of scope.
- **Should the preferences panel link back to the section(s) that flagged a
  given preference**, the reverse direction of the badge-to-entry link this
  story builds? Not asked for by the acceptance criteria; flagged as a
  possible small follow-up if reviewers want to jump both ways.
