# Design: Harden /next-round + /complete lifecycle, security, and docs (#112, #117, #118)

**Date:** 2026-07-11
**Issues:** [#112](https://github.com/jacquardlabs/viva/issues/112) — "QA-mode server never calls
/complete — sessions leak a process indefinitely"; [#117](https://github.com/jacquardlabs/viva/issues/117)
— "Add Origin/CSRF check and body-size cap to /next-round and /complete";
[#118](https://github.com/jacquardlabs/viva/issues/118) — "Reconcile brainstorming-qa.md's
/next-round example with headless-contract.md's preferred JSON-body form"
**Epic:** jig-integration — "Ship two review-surface capabilities jig needs (a unified
Q&A→review session, task-card splitting for plan documents) and a versioned,
documented headless contract that reflects the surface those capabilities
actually produce, so jig can build against viva without re-parsing internals."
**Status:** Draft — pending `/gate-design-review`

---

## Problem & persona

PRODUCT.md names the agent author as viva's first persona:

> **The agent author (primary, non-human).** Claude Code, having written a
> spec, ADR, runbook, or design doc. It needs to hand the doc to a human for
> sign-off without burning context: parse without reading the doc, wait
> without polling cost, rewrite only flagged sections, and learn what this
> reviewer always wants. The skill is tuned so the agent never makes the human
> wait on a tool round-trip and never loads the doc into context until a
> rewrite needs it.

The sibling `headless-contract` design doc (#111, this same epic) already
established that jig instantiates this persona one layer removed — a
separate program that launches `server.py` as a subprocess and drives it over
HTTP rather than running inside the reviewing agent's own session. This story
is about three concrete gaps in that instantiation, all surfaced by the same
source: **the epic jig-integration finale's security and code audit,
2026-07-11**, run after #109 (unified-session) and #110 (task-card-split)
landed and #111 (headless-contract) documented the resulting surface.

**Gap 1 — an orphaned process (#112).** `brainstorming-qa.md`'s documented
qa-mode finish steps (`.claude/skills/viva/brainstorming-qa.md`, current
steps 1–3) read `.viva/answers.json` and stop. But the 2-second shutdown
timer that tears the server down only starts inside the `POST /complete`
handler (`server.py:3181`) — nothing else fires it. A qa-only session today
runs forever until something kills it by hand. This is not hypothetical: it
was discovered live during jig's own dogfood of #109, which left its own qa
server orphaned, and turned up five more `--mode diff` server processes
already running on the same machine from unrelated sessions, some several
days old (same leak shape; diff-mode's instance is tracked separately per
issue #112's own note and is **not** this story's scope — see Out of scope).
Review-mode sessions do not have this problem: `SKILL.md`'s step 5 already
calls `POST /complete` on finish (`SKILL.md:157`); qa-mode's prose simply
never grew the equivalent step.

**Gap 2 — an exploitable asymmetry (#117).** `POST /submit` rejects a
non-loopback `Origin` header and caps the request body at
`MAX_SUBMIT_BYTES` (256 MiB) — defense-in-depth against a malicious page
driving the local write sink via CSRF (`server.py:3056-3071`). `POST
/next-round` and `POST /complete` carry neither guard. The inline comment
justifying this (`server.py:3116-3118`, "`/next-round` is called by the
agent (curl), not the browser, so the browser-CSRF threat `/submit` guards
against does not apply") is the exact reasoning issue #117's audit disproves:
a hostile page open in the developer's browser *during a live session* can
POST cross-origin to `/next-round` as a CORS simple request — `Content-Type:
text/plain` triggers no preflight, and `json.loads` parses the body
regardless of the declared content type. The `output` field in that payload
writes straight into `_output_path` (`server.py:3135`, `3158`), so the next
legitimate `/submit` lands a structured-JSON file at an attacker-chosen path
while the SSE `round` push simultaneously reflows the victim's own tab to
attacker-supplied section content. `/next-round` and `/complete` also read
`Content-Length` bytes into memory with no cap at all, unlike `/submit`'s
`MAX_SUBMIT_BYTES` (`server.py:3119-3124`, `3169-3174`). The audit rated this
Medium — the preconditions are narrow (live session, victim visits a hostile
page, guesses the random loopback port, then submits) — but `#111` promoted
`/next-round` from an internal implementation detail to a **documented,
caller-facing contract endpoint** (`docs/headless-contract.md` §5), and a
documented external contract should not silently lean on preconditions being
unlikely rather than actually closing the gap.

**Gap 3 — a contract doc that recommends one thing and ships another
(#118).** `docs/headless-contract.md` §5 states the JSON-body `output` field
is "preferred — travels like every other POST field" and calls the
`?output=` query-string form "legacy ... a fallback." `SKILL.md`'s own core
loop already uses the preferred form (`SKILL.md:145-146`, building the
payload with `d['output']=...` before piping to curl). But
`brainstorming-qa.md`'s worked hand-off example — shipped in the *same*
epic, #109 — uses exactly the fallback form
(`$BASE/next-round?output=.viva/review-r1.json`, current lines 96-100).
`headless-contract.md`'s parenthetical "existing skills use this form" is
also imprecise as a blanket claim: `SKILL.md`, the primary orchestrator,
does not. Since jig is expected to copy `brainstorming-qa.md`'s example
verbatim for exactly the hand-off flow the contract doc documents, today it
would land on the path the epic's own contract doc just declared
non-preferred.

None of these three are hypothetical or speculative — each is a finding from
a real audit against the code and docs as merged on this epic branch, matching
the acceptance criteria's four checkpoints one-for-one:

1. QA-mode server calls `POST /complete` (or equivalent) on finish — no
   orphaned process. (Gap 1)
2. `/next-round` and `/complete` get the same loopback-Origin check +
   body-size cap `/submit` already has. (Gap 2)
3. `brainstorming-qa.md`'s curl example switches to the JSON-body `output`
   form and documents the `/complete` call. (Gap 1 + Gap 3)
4. `headless-contract.md` updated to match on all three points.

## Proposed design

Three narrow, additive fixes — one per gap — plus the doc updates that keep
`headless-contract.md` truthful about the result. No new endpoint, no new
`--mode`, no schema-field change.

**1. QA-mode calls `/complete` on finish.** Add a step to
`brainstorming-qa.md`, immediately after its existing step 3 ("wait for
answers"), that calls `POST /complete` once `.viva/answers.json` exists —
mirroring the pattern `SKILL.md`'s own step 5 already uses
(`curl -s -X POST "$BASE/complete" -H "Content-Type: application/json" -d
"{...}"`). `/complete`'s handler and its 2-second shutdown timer already work
correctly today (`server.py:3167-3181`) — review-mode already relies on
them. The gap is entirely in `brainstorming-qa.md`'s prose never reaching
that call, so this is a doc-only fix for the *standalone* qa path, with no
`server.py` change needed for this gap specifically.

This step is explicitly **conditional on not handing off**. The existing
"Hand off to a review session in the same tab (#109)" section
(`brainstorming-qa.md:85-120`) already documents an alternative path where
the qa server is deliberately *not* torn down — it hands off via
`/next-round` and the resulting review loop calls its own `/complete` at
that loop's eventual finish (`SKILL.md`'s step 5 pattern, reused verbatim by
a caller driving the post-hand-off review round). Calling `/complete`
immediately after reading `answers.json` unconditionally would break that
hand-off by killing the process the hand-off is about to reuse. The revised
doc states the branch explicitly: standalone Q&A calls `/complete` right
after reading the answers; a hand-off skips that call and instead finishes
via the review round's own `/complete`, exactly as the hand-off section
already describes in prose (now made concrete with the same runnable curl
form as gap 3's fix, not left as a sentence).

**2. Uniform Origin check + body cap.** Apply the identical guard `/submit`
already runs — reject a present, non-loopback `Origin` header with 403;
parse `Content-Length`, reject a non-integer with 400; reject a length over
`MAX_SUBMIT_BYTES` with 413 — to `/next-round` and `/complete`. Per CLAUDE.md's
coding philosophy ("minimize structural drift, prefer reuse over creation"),
this factors into one small shared helper method on `Handler`
(e.g. `_check_origin_and_length(self, cap: int) -> int | None`, returning the
validated length or sending the error response itself and returning `None`)
called from all three `POST` branches, rather than tripling the existing
seven-line inline block a third time. `/submit`'s call site keeps its own
existing behavior unchanged; `/next-round` and `/complete` gain the same
call. Reuse `MAX_SUBMIT_BYTES` (256 MiB) verbatim for both — the acceptance
criteria calls for "the same ... cap `/submit` already has," not a new,
possibly-diverging limit, and introducing a second cap value would itself be
a new inconsistency for a doc to explain. The two `# No CSRF/Origin check`
comments (`server.py:3116-3118`, `3168`) are removed along with the
reasoning they encoded, since issue #117 is precisely a demonstration that
reasoning doesn't hold for an HTTP endpoint reachable from any process on the
loopback interface — including a browser tab — regardless of who a caller
*expects* to be driving it.

**3. Doc reconciliation.**
- `brainstorming-qa.md`'s hand-off curl example (current lines 96-100)
  switches from the `?output=` query-string form to the JSON-body form,
  matching `SKILL.md`'s own pattern: embed `output` in the JSON payload
  before piping it to curl, rather than appending it to the URL.
- `headless-contract.md` §5's endpoint table and its error-response
  paragraph are updated so the 403/`/submit`-only and 413/`/submit`-only
  callouts no longer read as `/submit`-exclusive — `/next-round` and
  `/complete` now return the same codes for the same reasons.
- `headless-contract.md`'s "existing callers use this form" parenthetical
  (§2, near the `output` field description) is narrowed per issue #118's own
  recommendation: once `brainstorming-qa.md`'s example is fixed, no shipped
  skill uses the query-string fallback form any longer, so the doc should say
  that plainly instead of gesturing at unnamed callers.
- `headless-contract.md`'s §7 qa→review hand-off subsection gets one added
  sentence: a hand-off does not call `/complete` right after `answers.json`
  is read; the *eventual* review round's own `/complete` finishes the
  process, matching the fixed `brainstorming-qa.md` section this doc already
  cross-references.
- **Contract version bumps 1 → 2.** `headless-contract.md`'s own rule table
  names "removing an HTTP endpoint, or changing its request/response shape"
  as bump-worthy, and is explicit that this determination is not
  self-referential license to look away from its own surface: `/next-round`
  and `/complete` previously always returned `{"ok": true}` (or a validation
  400) regardless of `Origin` or body size; a caller that sends a body over
  256 MiB, or that (unusually, but possibly, e.g. behind some
  origin-setting proxy) carries a non-loopback `Origin` header now gets a new
  403/413 response shape it did not before. This is exactly the kind of
  surface change the version marker exists to flag — understating it here
  would repeat the very failure mode issue #117 itself calls out ("a
  documented external contract shouldn't silently lean on" an unstated
  assumption). A new changelog row records the change alongside the existing
  version-1 row.

## User journey

1. jig's `/design` skill launches `--mode qa`, the human answers the
   interview, jig reads `.viva/answers.json`. With no hand-off in play, jig's
   caller code — built against `brainstorming-qa.md`'s corrected steps —
   now calls `POST /complete` before moving on, so the server process and its
   `.viva/server.url` file clean up exactly the way a review-mode session
   already does, instead of leaking a process (PRODUCT.md's "Cheap for the
   agent" principle: a leaked subprocess is a real, measured cost — "5 more
   `--mode diff` server processes ... some several days old" per #112's own
   finding, not an abstract risk).
2. That same developer, running a live review session, has a second browser
   tab open on the general web. A page in that other tab can no longer
   silently drive `/next-round` or `/complete` via a cross-origin CORS-simple
   POST — both endpoints now reject a non-loopback `Origin` exactly as
   `/submit` already does, closing the path-hijack chain #117 traced through
   to a victim's own reflowed tab.
3. jig's `/design` skill's hand-off implementation copies
   `brainstorming-qa.md`'s curl example verbatim, as issue #118 anticipated
   it would, and lands on the JSON-body form `headless-contract.md` calls
   preferred — the same pattern `SKILL.md`'s own loop already uses. One fewer
   inconsistency for jig to reconcile by reading two docs against each other.
4. A future jig maintainer (or the human running `/viva` and reading
   `README.md`'s advanced section, unaffected by this story) diffs
   `headless-contract.md`'s Contract version line (now 2) and its changelog
   row on a viva upgrade, and sees precisely what about `/next-round` and
   `/complete`'s behavior changed, without re-reading `server.py`.

This composes with, rather than replaces, the qa→review hand-off (#109) and
the headless-contract doc (#111) that this same epic already shipped — it
closes gaps those two stories' own finale audit found in what they produced,
rather than adding new capability.

## Out of scope

- **No change to `/submit` itself.** It already has the Origin check and
  body cap; it is the reference point this story brings the other two
  endpoints up to, not a target of change.
- **No change to `MAX_IMAGE_BYTES`, `ALLOWED_IMAGE_MIMES`, or any
  attachment-specific limit** (`server.py:2942-2949`) — orthogonal to this
  story's request-body guard.
- **No new endpoint, no new `--mode` value, no schema-field change.**
  `ReviewInput`/`QAInput`/`QAOutput`'s wire shapes are untouched.
- **No change to the qa→review hand-off mechanism itself (#109).** The
  QA-mode `/complete` fix touches only the standalone (non-hand-off) finish
  path; a hand-off's eventual `/complete` call, at the end of whatever review
  loop follows it, is unchanged — it already works today via the same
  mechanism review-mode sessions use.
- **No change to the 2-second shutdown grace timer's duration** or its
  purpose (giving the browser's SSE `"complete"` handler time to render).
- **No cleanup of already-orphaned processes.** This is a forward-looking
  fix to the code and docs path that creates new orphans, not a script to
  find and kill ones already running on a machine from before this fix
  ships.
- **No fix for diff-mode's analogous orphaned-process risk.** Issue #112
  explicitly notes the five stray `--mode diff` processes it found are "same
  leak shape, tracked separately" — the acceptance criteria for this story
  names QA-mode only. If diff-mode has the same gap, it is a separate,
  future story against `.claude/skills/viva/diff.md`, not folded in here.
- **No new authentication or API-key mechanism.** The Origin allowlist is
  the same defense-in-depth model `/submit` already uses (a local,
  keyless tool per PRODUCT.md principle 6), not a new security posture.
- **No rate-limiting or source-IP restriction** beyond the existing
  loopback-Origin check — matching `/submit`'s existing scope exactly, per
  the acceptance criteria's "same ... check `/submit` already has."

## Alternatives considered

1. **Copy the Origin/body-cap guard block inline into `/next-round` and
   `/complete` a second and third time**, instead of factoring a shared
   helper. Rejected: CLAUDE.md's coding philosophy explicitly prefers reuse
   over creation and calls for narrow, deep API surfaces; three near-identical
   seven-line blocks is exactly the kind of drift a shared helper exists to
   prevent, and a future fourth caller-facing `POST` endpoint should not have
   to decide whether to copy the block a fourth time.
2. **Have the server auto-shut-down once `answers.json` is written**,
   removing the need for a caller-driven `/complete` call in qa-mode at all.
   Rejected: the server cannot distinguish "answers.json written, this is a
   standalone finish" from "answers.json written, a hand-off `/next-round` is
   about to arrive" without the caller telling it — which is exactly what an
   explicit `/complete` call already is. Auto-shutdown would also special-case
   qa-mode's finish path away from review-mode's and diff-mode's, all of
   which already rely on an explicit caller-driven `/complete`; keeping one
   uniform "the caller says when it's done" mechanism across every mode is
   simpler than teaching the server to guess.
3. **A stricter or narrower Origin allowlist for `/next-round`/`/complete`**
   (e.g., matching only the exact bound port rather than any
   `127.0.0.1`/`localhost` origin) instead of reusing `/submit`'s exact
   check. Rejected: introducing a second, divergent Origin-matching rule
   across three endpoints of the same server would itself be a new
   inconsistency for a caller (and this doc) to track, and the acceptance
   criteria asks for "the same" check, not an improved one — CLAUDE.md's
   "minimize structural drift" applies directly.
4. **Leave Contract version at 1, treating the new guards as additive
   hardening rather than a breaking surface change.** Rejected: a body over
   256 MiB or a set `Origin` header that previously always got `{"ok":
   true}` from `/next-round`/`/complete` now gets a 403/413 instead — a real,
   if narrow, response-shape change by `headless-contract.md`'s own stated
   rule for when the version bumps. Treating it as a non-bump would repeat
   the exact failure mode issue #117 flags: a documented contract quietly
   not matching what changed underneath it.

## Operational readiness

- **Migration:** none — no schema, no on-disk file format, no `.viva/`
  state shape changes. `preferences.json`'s cross-session state is untouched.
- **Rollback:** revert the commit(s). Nothing downstream depends on the new
  guard's presence; a caller that never sends an oversized body or a
  non-loopback `Origin` sees no behavior difference either way.
- **Rollout:** a normal commit, no feature flag. The new guards take effect
  the next time `server.py` is launched — a session already running when the
  fix ships is unaffected until it is restarted, matching this project's
  existing precedent that `server.py` has no hot-reload path.
- **Observability — how the team knows this works or regresses:** this is a
  stdlib-only, local, keyless tool (PRODUCT.md principle 6) with no
  metrics/logging infrastructure to lean on, so the durable signal is test
  coverage, not a dashboard:
  - New regression tests, following the existing
    `tests/test_server_validation.py`/`_server_harness.py` subprocess +
    `urllib` pattern, assert: a non-loopback `Origin` gets 403 from
    `/next-round` and from `/complete`; an oversized body gets 413 from each;
    a qa-mode session's process actually exits (and `server.url` is deleted)
    after the finish sequence calls `/complete`. These run in CI across
    Python 3.8–3.13 exactly like every other `tests/test_*.py`.
  - No automated leak detector is proposed for the orphaned-process class of
    bug — matching "no hosted service" (PRODUCT.md), there is no
    process-monitoring infrastructure this project runs. The regression test
    above is the only guard against reintroducing the leak; a human noticing
    stray `server.py` processes via `ps` (as #112 itself was discovered) is
    the fallback, same as today.
- **Failure mode:** if a legitimate caller's large `/next-round` payload
  trips the new 413, or an unusual local setup (e.g. a proxy that sets an
  unexpected `Origin` header) trips the new 403, the failure is loud and
  uniform — `{"error": "<message>"}` JSON with a matching non-2xx status,
  exactly like every other endpoint's error path — rather than a silent
  drop. This matches CLAUDE.md's existing boundary-validation posture ("the
  boundary validator is what turns that into a loud failure").

## Open questions

- **Exact JSON shape for the QA-mode `/complete` summary body.**
  `headless-contract.md` §5 already documents `/complete`'s body as
  "not schema-enforced" — free-form, used only for the SSE `"complete"`
  event's payload. `SKILL.md`'s review-mode finish uses
  `{rounds_total, sections_total, sections_revised}`; a QA-mode equivalent
  (e.g. `{questions_total, questions_answered}`) has no established
  precedent to match, since this is qa-mode's first `/complete` call. Left
  as a build-phase implementation choice, not a design-level decision, since
  it has no schema or caller-visible contract implication either way.
- **Whether jig's own `/design` skill (outside this repository) needs a
  matching update once `brainstorming-qa.md`'s example changes.** Outside
  this worker's and this repo's control — flagged for epic-level tracking,
  not resolved here.
- **Whether diff-mode's analogous orphaned-process gap (noted but explicitly
  out of scope above) becomes its own follow-up issue.** Recommended, given
  issue #112's own observation that it found the same leak shape live, but
  left to backlog triage rather than decided by this design.
