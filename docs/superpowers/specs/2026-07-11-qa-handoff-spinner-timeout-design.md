# Design: Client-side timeout affordance for the qa→review processing spinner (#119)

**Date:** 2026-07-11
**Issue:** [#119](https://github.com/jacquardlabs/viva/issues/119) — "No client-side timeout
or error affordance on the qa→review hand-off's processing spinner"
**Epic:** jig-integration — "Ship two review-surface capabilities jig needs (a unified
Q&A→review session, task-card splitting for plan documents) and a versioned,
documented headless contract that reflects the surface those capabilities
actually produce, so jig can build against viva without re-parsing internals."
**Status:** Draft — pending `/gate-design-review`

---

## Problem & persona

PRODUCT.md names the reviewing human as viva's second persona:

> **The reviewing human (primary).** A developer who must sign off on a doc an
> agent produced and refuses to rubber-stamp it. Wants to see each section
> verbatim, leave one or more typed comments per section...

That human is the one left staring at `#processing-view` — the existing
"Claude is revising…" spinner (`server.py:1379-1384`) the browser shows the
moment `POST /submit` fires the `processing` SSE event
(`_push_sse("processing", {})`, `server.py:3114` as of this branch —
premortem item 1 cites `server.py:3085`, drifted by intervening commits
since it was written), and hides again only when a `round` or `complete`
event arrives. Until this story, that view has no failure or delay signal of
its own — it just spins.

That gap was tolerable while `#processing-view` had exactly one use: a
human submits section verdicts, the same Claude Code agent driving the
`SKILL.md` loop revises the flagged sections in-session, and a new round
lands within the length of one LLM turn. A stuck spinner there almost always
meant the agent process itself had died, which the tab would separately
learn about the instant the SSE connection dropped — `es.onerror`
(`server.py:2725-2733`) already renders a `Connection lost — check the
terminal.` banner for exactly that case.

The `unified-session` story (#109) gave `#processing-view` a second,
materially different use: the qa→review hand-off. There, the human submits
Q&A answers, and the wait is no longer bounded by an LLM turn in the same
process — it is bounded by an **external caller's own synthesis step**
(jig's `/design` skill drafting review sections from the answers) that
POSTs a follow-up `/next-round` payload to the same still-running server.
`docs/headless-contract.md` §6 already discloses this precisely as a caveat
rather than a fixed gap:

> **Caveat — unbounded "processing" spinner on the qa→review hand-off.**
> ...If the caller's synthesis step fails or hangs before it POSTs, the
> human's tab is stranded with no visible error... A caller building this
> hand-off should treat its synthesis step as needing its own bounded time
> budget and terminal-visible failure path, since the browser will not
> surface one.

Crucially, the SSE connection itself stays alive the whole time — the
server process is still running and its `/events` stream is still open;
only the *application-level* signal (a `round` payload) is late or never
arrives. `es.onerror` only fires on an actual connection drop
(`docs/headless-contract.md` §6, issue body), so it cannot detect this
failure mode at all. The epic pre-mortem flagged this as a live risk before
any code shipped:

> Stuck processing beat: after the human submits Q&A, `/submit` fires the
> `processing` SSE event... and the browser hides both qa/review views for
> an indeterminate spinner with no timeout. If jig's out-of-viva synthesis
> fails or never POSTs round-1, the human is stranded on the spinner...
> (`docs/studious/premortems/2026-07-11-unified-session-design.md`, item 1)

`#processing-view` is one shared view for both waits — there is no
second copy of it, and (per this same epic's `unified-session` design,
"Out of scope: Schema changes") no wire field distinguishing an
intra-loop revise from a hand-off synthesis. This story adds the missing
signal to that one shared view, without assuming which of the two waits
produced it.

## Proposed design

`#processing-view` gains a soft, client-side-only timer, started the
instant the `processing` SSE event shows it and cleared the instant `round`
or `complete` hides it. If neither arrives before the timer elapses, the
browser shows a "still waiting" banner — reusing the exact visual mechanism
the SSE `onerror` handler already established (a fixed banner prepended to
`document.body`, tokenized colors, no page reload, no blocking of any
control), so a human who has already learned to recognize "banner appears at
the top of the tab = something needs my attention" recognizes this one on
sight. Concretely:

- **Trigger:** a `setTimeout` armed in the existing `processing` SSE
  listener (`server.py:2660-2664`), cleared in the existing `round`
  (`server.py:2666-2695`) and `complete` (`server.py:2697-2723`) listeners —
  the same three listeners that already own `#processing-view`'s visibility,
  so the timer's lifecycle never diverges from the view's.
- **Banner content:** `Still waiting — check the terminal.`, deliberately
  parallel to the existing `Connection lost — check the terminal.` copy so
  the two read as one family of messages rather than two different systems.
  The "check the terminal" half of both is the load-bearing part — it
  points the human at the one place `docs/headless-contract.md` §6 already
  documents as the real signal: a caller's process exit code and stderr
  shape (`viva: invalid ...` prefix, a bare traceback, or nothing at all if
  it's simply still running).
- **Severity token, not the identical class.** `.error-banner` is styled
  with `--orange`/`--orange-bg`, which DESIGN.md's color table maps to
  "Changes / error" — an appropriate weight for "the connection actually
  dropped," but an overstatement for "no event yet, connection still open."
  This story adds one small modifier, e.g. `.error-banner.banner-info`,
  using `--violet`/`--violet-bg` (DESIGN.md: "Info / question") for
  background/text/border, keeping every structural rule (`position: fixed`,
  padding, font, `z-index`) on the shared `.error-banner` base. This is a
  handful of CSS lines, not a new component, and it makes the two banners
  visually distinguishable by actual severity rather than identical-looking
  overload.
- **At most one banner at a time.** The "still waiting" banner is only
  created if `#sse-error-banner` isn't already present, mirroring the
  idempotency check `es.onerror` itself already uses
  (`if (!el('sse-error-banner'))`, `server.py:2726`). If the SSE connection
  *actually* drops after the soft timer already fired, `onerror` additionally
  removes any still-waiting banner it finds and replaces it with the
  connection-lost one — the harder, more specific signal supersedes the
  softer one rather than the two stacking on top of each other at the same
  `position: fixed; top: 0`. This keeps the fix a strict escalation path,
  never two banners overlapping illegibly.
- **No new failure state, no cancel button.** The banner is purely
  informational — `#processing-view` keeps spinning underneath it exactly as
  before, `btn-submit`/`btn-skip` are untouched, and nothing on the wire or
  server side changes. A human who sees the banner and then, moments later,
  sees the tab reflow to `round` cards normally is not treated as having hit
  an error — the banner simply disappears (acceptance criteria: "soft"
  timeout, not a hard failure).

This composes with, rather than replaces, `docs/headless-contract.md` §6's
existing guidance that a caller's synthesis step needs "its own bounded time
budget and terminal-visible failure path." That guidance is about the
*caller's* process; this story is the matching half on the *browser's* side
— together they mean a stuck hand-off is now visible from both ends instead
of only the one the caller happens to be watching.

**Feasibility note on testing.** This repo has no browser/JS test harness
(CLAUDE.md: `server.py` is one embedded HTML/JS constant, stdlib-only, no
npm). The existing precedent for pinning a browser-side change is a
string-needle assertion against `server.HTML` — `tests/test_server_a11y.py`
and `tests/test_server_qa_review_handoff.py` ("this repo has no JS/browser
test harness... browser-side fixes are pinned as string-needle assertions
against the embedded HTML constant") already establish this pattern for
exactly this kind of fix. The build phase is expected to follow it: assert
the timeout constant, the banner-creation function, and the
`sse-error-banner` mutual-exclusion check are present in `server.HTML`, with
the actual timed appearance/disappearance verified manually in a browser per
CLAUDE.md's test conventions.

## User journey

Two journeys share the one view; both are affected identically since the
client cannot tell them apart (see Problem & persona).

**Journey A — standalone review round (unaffected in the common case).**
1. The human has been reviewing section cards and clicks submit
   (`btn-submit`) after marking verdicts.
2. `#processing-view` appears ("Claude is revising…"); the soft timer
   arms.
3. The same Claude Code agent, driving `SKILL.md`'s loop, revises the
   flagged sections and POSTs the next round within the timer's window (the
   common case for most documents) — `round` fires, the timer clears, the
   human never sees the banner. Nothing about this path changes from today.
4. On the rare document large or slow enough to outlast the timer, the
   human instead sees `Still waiting — check the terminal.` — an accurate
   signal, since checking the terminal running the agent is exactly how
   they'd confirm it's still working rather than stuck. The banner
   disappears the moment `round` eventually arrives.

**Journey B — qa→review hand-off (the case #119 was filed for).**
1. The human has been answering Q&A cards (`#qa-view`) and submits.
2. `#processing-view` appears; the soft timer arms — identical mechanism to
   Journey A, since it's the same `processing` event and the same view.
3. jig's `/design` skill reads `.viva/answers.json` and synthesizes
   review-input sections, outside viva entirely (`docs/headless-contract.md`
   §7, "qa → review hand-off").
4. **Happy path:** jig POSTs the round-1 payload to `/next-round` before the
   timer elapses; `round` fires, the tab reflows to section-review cards
   exactly as `unified-session`'s design doc describes, and the human never
   sees a banner.
5. **The case this story exists for:** jig's synthesis hangs, errors before
   it POSTs, or the caller process dies outright. The SSE connection stays
   open (the server process is still running; nothing about it crashed) so
   `onerror` never fires. Before this story, the human's tab was
   indistinguishable from Journey A's happy path — a spinner, silently,
   forever. After this story, the timer elapses and the human sees `Still
   waiting — check the terminal.`, which is now their first correct signal
   that something needs attention, matching what
   `docs/headless-contract.md` §6 already tells a caller to expect from its
   own side.

## Out of scope

- **The `round` handler's missing `Array.isArray(data.sections)` guard.**
  Issue #119's own recommendation section names this as a second fix
  ("Guard the `round` handler with `Array.isArray(data.sections)`... per
  §2, nothing enforces that a caller's CLI `--mode` and wire `mode`
  agree..."), but this story's acceptance criteria covers only the
  processing-view timeout affordance. That guard is a distinct failure
  mode — a malformed payload that *does* arrive, versus one that never
  arrives at all — and is left for separate handling rather than folded in
  here silently.
- **A hard timeout, cancel button, or automatic retry.** The acceptance
  criteria is explicit about "soft" — this story adds a visibility signal,
  not a new failure state. The human still decides what to do next (wait
  longer, check the terminal, or restart the session per `SKILL.md`'s
  existing "delete `.viva/server.url` before proceeding" guard); viva
  doesn't decide for them, matching PRODUCT.md's "advisory, never gating"
  posture applied to this affordance too.
- **Any server-side timeout.** `docs/headless-contract.md` §6 states
  plainly: "The server itself has no request or session timeout... any
  'timeout' a caller experiences is entirely its own choice." This story
  doesn't change that — it's a browser-only addition, no `server.py`
  request-handling change.
- **A wire-level marker distinguishing a hand-off wait from an intra-loop
  revise wait.** Ruled out by the sibling `unified-session` design doc
  ("Out of scope: Schema changes") for the same reason it applies here — no
  `schema.py` shape changes. The single soft timeout applies uniformly to
  both journeys above because the browser has, and should keep having, no
  way to tell them apart.
- **Rewording `#processing-view`'s static "Claude is revising…" text**,
  which is already slightly inaccurate for Journey B (jig is synthesizing, not
  Claude revising). Real, but a pre-existing copy issue orthogonal to adding
  a timeout affordance — not touched here to keep this change to exactly
  what the acceptance criteria asks for.
- **A configurable/user-adjustable timeout.** The threshold is a single
  client-side constant (see Open questions for its value), not a setting
  exposed anywhere — `preferences.json` and the rest of viva's
  configuration surface are untouched.

## Alternatives considered

1. **A hard failure state that requires the human to reload or abandon the
   session.** Rejected: the acceptance criteria calls for "soft," and a
   hand-off wait that's merely slow (not dead) would force the human to
   discard a session that was about to succeed. The banner-only approach
   costs nothing if the wait resolves late.
2. **A server-side session/request timeout that force-closes a stalled
   hand-off.** Rejected: this would be a bigger, server-side contract
   change that contradicts `docs/headless-contract.md` §6's documented
   invariant ("no request or session timeout") for every mode, not just the
   hand-off — a much larger blast radius than the affordance this story asks
   for, and one that risks killing a legitimately slow revision (Journey A)
   as a false positive.
3. **Editing `.processing-text` in place instead of a banner.** Rejected on
   the acceptance criteria's own wording — "mirroring the existing SSE
   onerror banner pattern" specifically calls out the banner mechanism, not
   just banner-like text. Reusing the actual `.error-banner` structural
   rules (with a new severity modifier, see Proposed design) is also less
   new code than inventing a second in-place text-swap mechanism inside
   `#processing-view` itself.
4. **Polling a new server endpoint to ask "has a round arrived yet /
   how long has it been."** Rejected: this is purely "how long has this tab
   been staring at a spinner," which a client-side `setTimeout` answers with
   zero server round-trips. The server has no timeout concept to poll
   against (§6), and adding one would revisit alternative 2's rejected
   scope.
5. **A distinguishing wire field so the timeout could differ by wait type
   (e.g., a longer threshold for hand-offs than intra-loop revises).**
   Rejected for the same reason as the "Out of scope" bullet above — the
   sibling story already closed the door on schema changes for this exact
   kind of signal, and a single uniform threshold is simpler while still
   satisfying the acceptance criteria.

## Operational readiness

- **Migration:** none. Client-only change inside the single embedded
  `server.HTML` constant (JS timer + CSS modifier class); no `.viva/*`
  schema or file shape touched.
- **Rollback:** revert the commit. The timer is stateless across sessions —
  no flag to unset, no persisted state to clean up, matching the rollback
  shape the sibling `unified-session` and `qa-next-round-hardening` design
  docs already used for this epic.
- **Rollout:** ships in every server start, unconditionally — consistent
  with this project's existing pattern of no feature-flag infrastructure
  (a single stdlib server, no environment-based toggles). The known,
  accepted trade-off is the false-positive case in Journey A (a legitimately
  slow revision outlasting the threshold); the threshold is a named
  constant specifically so it can be retuned later without a design change
  if that proves too aggressive in practice.
- **Observability:** the banner *is* the observability surface for this
  gap — there is no server-side log, metric, or alarm to add, consistent
  with PRODUCT.md principle 6 ("local and keyless... no hosted service") and
  the same reasoning the `qa-next-round-hardening` design doc gives for why
  this project has "no fleet to monitor, only a local subprocess whose
  stdout and exit code are already the whole observability surface." On the
  terminal side, nothing changes — `docs/headless-contract.md` §6's exit
  code and stderr table is already the caller-facing signal this banner
  points the human at.

## Open questions

- **Exact timeout duration.** This design does not pin a specific number of
  seconds. It needs to be long enough that a normal in-session revise
  (Journey A) rarely trips it, but short enough that a human in the
  hand-off case (Journey B) isn't left wondering for minutes. A value in
  the neighborhood of 15–30 seconds is a reasonable starting point, but the
  right number is better chosen from an actual timing run of a realistic
  jig synthesis (as the epic pre-mortem's item 6 already recommends timing
  the submit→reflow gap) than guessed here. Left for the build phase to
  pick and name as a single constant, with this doc's range as the
  guidance.
- **Whether the banner should escalate after a much longer wait** (e.g., a
  second-stage message past a few minutes suggesting the session may need
  to be restarted). Not required by the acceptance criteria; left as a
  possible follow-up if real usage shows the single-stage banner isn't
  enough signal for very long stalls.
- **Manual verification of the visual escalation from soft-timeout banner
  to connection-lost banner** (see Proposed design's "at most one banner at
  a time" rule) needs a browser check at build/acceptance time, since this
  repo's test harness can only pin the JS's presence and structure, not its
  timed, interactive behavior.
