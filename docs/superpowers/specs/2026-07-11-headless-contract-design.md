# Design: Stable Headless Invocation Contract (#111)

**Date:** 2026-07-11
**Issue:** [#111](https://github.com/jacquardlabs/viva/issues/111) — "Stable headless invocation contract"
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
> reviewer always wants.

jig's `/design` and `/plan` skills instantiate this persona one layer removed —
the same way the task-card-split design doc frames it for `/plan`. Both of
this epic's sibling stories (unified-session, task-card-split) exist because
jig plays this role and needs viva's mechanics to actually work for it. This
story is about the layer underneath that: **how does jig find out what those
mechanics *are*?**

Today the answer is "read SKILL.md." PRODUCT.md's own Known Problems section
names the shape of the gap directly:

> **Agent-side complexity.** SKILL.md carries the whole launch→wait→act→rewrite
> loop as prose with conditional round-1 branches ... so the agent shoulders
> the orchestration the code does not.

SKILL.md is authoritative prose for *viva's own* agent (Claude Code, running
`/viva` inside a session that owns the doc). It was never written to answer a
different question: "if I am a separate program (jig) that only ever launches
`server.py` as a subprocess and reads/writes its JSON files, what is the
stable surface I can build against, and what changes I need to notice on a
viva upgrade?" Answering that today means reading `server.py`'s argparse block,
`schema.py`'s validators, and three separate skill files, and inferring the
rest from behavior — exactly the "re-parsing internals" the epic goal says
jig should not have to do. Concretely, verified against the current
`server.py`/`schema.py`/skill files, an integrator has to independently
discover facts like these before a single request is safe to send:

- `--mode` gates two things only — which startup validator runs
  (`validate_review_input` vs `validate_qa_input`, `server.py:3207-3216`) and
  the printed stdout label (`server.py:3227`). The **browser's** rendered
  view is decided separately, at request time, by the `mode` field *inside*
  the JSON payload served from `/input` (`server.py:2835-2866`) — a caller
  that lets the CLI flag and the JSON field disagree gets undefined-by-contract
  behavior today, because nothing enforces they match.
- `.viva/server.url`'s location is not hardcoded to `.viva/` — it is written
  to `Path(args.output).parent / "server.url"` (`server.py:3225`). A caller
  that points `--output` somewhere other than a `.viva/` directory gets
  `server.url` written next to it, not under `.viva/`.
- The three "graceful" exit paths (SIGINT, `/complete`'s 2-second shutdown
  timer, and the two `sys.exit(f"viva: invalid ... {e}")` validation
  failures) print a single recognizable line and exit non-zero-or-zero
  predictably; every other failure (bad `--input` path, malformed input
  JSON, an unwritable output directory) is an **uncaught** Python exception —
  a multi-line traceback on stderr, exit code 1, indistinguishable by shape
  from the deliberate one-liner unless the caller already knows to look for
  the `viva: ` prefix.

None of this is a code bug this story fixes — it is exactly what "reflects
the actual shipped surface" in the acceptance criteria asks a *doc* to
capture, warts included, so jig stops discovering it by trial and error.

## Proposed design

Ship one new reference file, **`docs/headless-contract.md`**, whose audience
is explicitly a downstream program (jig or any future headless caller), not
the human running `/viva` inside Claude Code (README's audience) and not
Claude Code orchestrating the loop (SKILL.md's audience). It sits alongside
those two rather than replacing either — PRODUCT.md's principle 2 ("verbatim,
not summarized") applies to this story too: the contract doc transcribes what
the code actually does, it does not restate SKILL.md's prose in different
words.

**Structure** (each section answers one thing a caller needs before it can
integrate, in the order a caller hits the questions):

1. **Contract version** — a single top-of-file marker, `Contract version: 1`,
   following the exact precedent `scripts/preferences.py` already sets for
   *its* on-disk store (`VERSION = 1`, `preferences.json`'s own
   `"version": 1` field) — reuse over invention, per CLAUDE.md's coding
   philosophy. A short paragraph states what bumps it: removing or renaming a
   flag, changing a round-file's required field, changing an exit code's
   meaning, removing or changing the shape of an HTTP endpoint. What does
   *not* bump it: adding an optional field, adding a new endpoint, adding a
   new `--mode` value, prose clarifications. A changelog table at the bottom
   (`version | date | change`) gives every future bump a place to land — this
   directly satisfies the acceptance criterion "states a version marker so
   future breaking changes have something to bump." This version is
   deliberately **independent of `plugin.json`'s semantic-release semver** —
   that version bumps on every merged feature or fix (UI polish, bug fixes
   unrelated to this surface), so it gives jig no signal about whether *this*
   documented surface changed. A dedicated, hand-bumped integer scoped only
   to this file's documented surface is the only version number that answers
   "do I need to re-check the contract."
2. **Invocation** — `--mode {review,qa,diff}` (required, exhaustive —
   verified against `server.py`'s argparse `choices=`), `--input` (required,
   any path, read once at startup), `--output` (required, any path — where
   round verdicts / answers are written, and where `server.url` is derived
   from), `--no-browser` (skip `webbrowser.open`; server.url is still
   written and the server still serves — this is the flag a headless caller
   passes every time, since nothing else suppresses the browser-open). States
   the CLI-`--mode`-vs-JSON-`mode`-field distinction from Problem & persona
   explicitly, since every existing caller (SKILL.md, `/viva-qa`, `/viva-diff`)
   keeps them in sync by convention and a new caller needs to be told that's
   a convention, not an enforced invariant.
3. **`.viva/` round-file naming and shapes** — states plainly that the
   `.viva/` directory and the `review-input-r{N}.json` / `review-r{N}.json` /
   `qa-input.json` / `answers.json` names are a **convention** the existing
   skills follow, not something `server.py` enforces (`--input`/`--output`
   accept any path). Documents the shapes that *are* enforced —
   `validate_review_input`/`validate_verdicts`/`validate_qa_input` from
   `schema.py`, transcribed as field tables (required vs optional), matching
   the `ReviewInput`/`ReviewSection`/`SectionVerdict`/`QAInput`/`QAAnswer`
   TypedDicts. Cross-references `schema.py` as the canonical source rather
   than duplicating its docstring — this doc explains *why* a caller cares
   about each field, `schema.py` stays the one place the shape itself is
   declared (CLAUDE.md: "the schema is the contract").
4. **`server.url` lifecycle** — written once, atomically, immediately after
   the server binds its port and before it starts serving; its path is
   `dirname(--output)/server.url`, not a hardcoded `.viva/server.url`;
   deleted in the shutdown path's `finally` block on every exit route
   (SIGINT or post-`/complete`). A caller that wants to detect "is a session
   already running" polls for this file's existence exactly as SKILL.md's
   own launch guard does.
5. **The HTTP surface a caller drives** — `GET /input` (poll-once, not
   watched — a caller reads it after launch to confirm shape, though most
   callers get everything they need from the files), `POST /submit`
   (browser-only — a headless caller never calls this, it exists for the
   human's browser tab), `POST /next-round` (the endpoint a caller uses to
   advance a running session — this is also the exact mechanism the
   unified-session hand-off uses, see below), `POST /complete` (ends the
   session; starts a 2-second shutdown grace timer so the browser's SSE
   "complete" event has time to render before the process exits). States
   explicitly that `GET /events` (SSE) is the **browser's** channel, not a
   caller-facing part of this contract — a headless caller never opens it.
6. **Error and timeout semantics** — a table of the concrete exit-code /
   stderr-shape pairs from Problem & persona: `0` (graceful shutdown,
   SIGINT or post-`/complete`), `2` (argparse usage error — missing a
   required flag or an invalid `--mode` choice; stderr is argparse's own
   usage block), `1`-with-one-line-`viva:`-prefix (the two deliberate
   `sys.exit(f"viva: invalid ... {e}")` validation failures — a caller can
   pattern-match on the prefix), `1`-with-traceback (every other failure:
   bad `--input` path, malformed input JSON, an unwritable `--output`
   directory — undifferentiated Python tracebacks, because nothing in
   `server.py` catches them). States plainly that **the server itself has no
   timeout** — it blocks on `server.handle_request()` in a loop until
   shutdown; the "timeout" a caller experiences is entirely the caller's own
   choice of how long to wait on the round-file-appears poll, mirroring
   SKILL.md's own documented guidance ("issue it with a generous timeout ...
   re-issuing the identical block after a timeout is safe and idempotent").
   This section also flags, as a caveat rather than a silent omission, the
   one real gap the unified-session pre-mortem already surfaced: after a
   qa→review hand-off, the browser shows an indefinite "processing" spinner
   with no client-side timeout if the caller's synthesis step fails between
   the human's Q&A submit and the follow-up `/next-round` POST — a caller
   integrating the hand-off needs to know a stalled synthesis step strands
   the human's tab, not just the terminal-side "qa server left running"
   failure mode.
7. **Session types this contract currently produces** — two subsections,
   each grounded in what actually shipped on this epic branch, not
   aspirational behavior:
   - **qa → review hand-off** (unified-session, #109): documents that this
     is *not* a third `--mode` value — a caller launches `--mode qa` exactly
     as `/viva-qa` does today, and later POSTs an ordinary
     `sections`-shaped `ReviewInput` payload to the same server's
     `/next-round`. Documents the operational, non-payload signal
     `server.py` uses to recognize the hand-off (`"questions" in
     _input_data and "sections" in new_data`, `server.py:3156`) and the
     distinct stdout line it prints (`viva · hand-off qa → review · {url}`)
     — the same signal `.claude/skills/viva/brainstorming-qa.md` already
     documents for viva's own agent, transcribed here for a caller that
     never reads that file. Names explicitly that `ReviewInput`'s wire shape
     carries **no field** marking a round as qa-originated — this is
     inferred server-side from the *sequence* of payloads on one server
     process, not something a caller can query after the fact from the JSON
     alone.
   - **`--split-on` task-card splitting** (task-card-split, #110): documents
     that this is a `scripts/parse_sections.py` CLI flag, not a
     `server.py` flag or a new round-file field — it changes *how* round-1
     (and every subsequent round's) `sections` list is produced, not
     anything the server or the wire schema needs to know about. States the
     match rule (`re.search`, case-sensitive, any heading depth, overrides
     auto-detection entirely) and the hard-error-on-zero-matches behavior,
     transcribed from `parse_sections.py`'s own module docstring.

The doc is entirely descriptive — it changes no code, no flag, no endpoint,
no schema field. "Proposed design" here means the shape and placement of a
new file, not a behavior change to `server.py`.

## User journey

1. jig's integration author opens `docs/headless-contract.md` once, at
   integration time, instead of reading `server.py`, `schema.py`, and three
   skill files and inferring the rest.
2. They build `/design`'s Q&A phase against the **Invocation** and
   **round-file shapes** sections: launch `--mode qa` with a `QAInput`-shaped
   file, poll for the `QAOutput`-shaped answers file, exactly as
   `.claude/skills/viva/brainstorming-qa.md` already tells viva's own agent
   to do — but now jig has it from a doc scoped to *its* needs, not viva's.
3. They build the hand-off per the **qa → review hand-off** subsection:
   after synthesizing sections from the answers, POST a `ReviewInput`
   payload to the still-running server's `/next-round` instead of tearing
   the process down — with the doc's caveat about the unbounded "processing"
   spinner already in hand, so their synthesis step is written to either
   succeed within a bounded time or leave a terminal-visible failure a human
   operator can see, rather than silently stranding the browser tab.
4. They build `/plan`'s task-card review against the **`--split-on` task-card
   splitting** subsection: call `parse_sections.py --split-on '^Task \d+'`
   instead of the default heading-level auto-detection, with the
   zero-matches failure mode already documented so their own error handling
   expects it.
5. They wire up their own retry/backoff and error surfacing using the
   **error and timeout semantics** table — distinguishing a deliberate
   `viva: invalid ...` validation failure (their own payload is malformed;
   fix the caller) from an uncaught traceback (an environment problem — bad
   path, permissions) from a normal `0`/graceful shutdown, without needing
   to read `server.py` to know which is which.
6. On a future viva upgrade, jig's maintainer diffs
   `docs/headless-contract.md`'s **Contract version** line and changelog
   table, not the full commit history, to decide whether anything they built
   against might have moved.

This composes with, rather than replaces, the core review loop PRODUCT.md's
feature map already names — this story documents the surface underneath that
loop for a caller that isn't Claude Code running `/viva` itself.

## Out of scope

- **No behavior change.** No new flag, endpoint, schema field, or exit code.
  Every fact in the doc is a transcription of what `server.py`/`schema.py`/
  the skill files already do as of this epic's other two stories landing —
  confirmed against the current worktree (`3337d61`, which already includes
  both `unified-session` and `task-card-split` merged into
  `epic/jig-integration`).
- **No retiring or merging README's "Server CLI (advanced)" section or
  SKILL.md's prose.** Those stay — README serves the human running `/viva`
  inside Claude Code, SKILL.md serves Claude Code orchestrating the loop.
  This story adds a third, narrower document for a third, narrower audience
  (a headless program). Where content overlaps (e.g. `--split-on`'s match
  rule), this doc states it independently rather than "see README" — a
  version-pinned external contract should not depend on a human-facing
  quickstart's wording staying stable underneath it.
- **No machine-readable schema (OpenAPI, JSON Schema) and no
  doc-generation tooling.** CLAUDE.md is explicit that `schema.py`'s
  TypedDicts are "documentation only — CI runs no type checker," and
  PRODUCT.md's principle 6 commits to "a single stdlib-only Python server ...
  no runtime packages" — directly backing the "What we are NOT building"
  line "**Not a heavyweight dependency.** stdlib-only server; no runtime
  packages." A generated spec would need new build machinery this project
  has no other precedent for, to capture only the shapes — not the
  prose-level facts (the `server.url` directory-derivation quirk, the
  CLI-mode-vs-JSON-mode-field split, the exit-code taxonomy) that make the
  doc worth having.
- **No coverage of `GET /events` (SSE) wire format.** It is the browser
  tab's private channel; a headless caller never opens it. The doc states
  this in one sentence and stops.
- **No retroactive versioning.** `Contract version: 1` starts now; the doc
  does not attempt to reconstruct what the surface looked like at earlier
  viva releases.
- **No fix for any of the surprising behaviors this design's research
  surfaced** (CLI `--mode` and JSON `mode` field can diverge with no
  enforcement; `--mode review` with a `sections`-less payload skips startup
  validation entirely). Documenting them accurately, caveats included, is
  this story's job; changing them is a separate decision this doc does not
  make unilaterally.

## Alternatives considered

1. **Auto-generate the doc from `server.py`'s argparse and `schema.py`'s
   TypedDicts at release time.** Rejected: the project's explicit principle
   is stdlib-only with no build step, and CI runs no type checker today —
   introducing a generator would be new machinery whose sole precedent-break
   is documenting a surface that is mostly *not* reflection-discoverable
   anyway (the `server.url` path-derivation behavior, the mode-field
   distinction, and the exit-code taxonomy all live in prose comments and
   control flow, not in type signatures a generator could read).
2. **Version the doc via `plugin.json`'s existing semantic-release semver.**
   Rejected (see Proposed design, Contract version) — that version bumps on
   every merged change regardless of whether this documented surface moved,
   giving jig no targeted signal.
3. **Put the contract inside `schema.py`'s module docstring.** Rejected:
   `schema.py`'s own docstring already scopes itself to "the round shapes";
   CLAUDE.md calls it out as "the single shared module ... keep that the
   only cross-import so each stays independently testable." Folding in the
   CLI/process/HTTP surface (server.url lifecycle, exit codes, `--no-browser`)
   would blur a file the project's coding philosophy specifically wants kept
   narrow ("narrow, deep API surfaces").
4. **Extend README's "Server CLI (advanced)" section instead of a new
   file.** Rejected: audience mismatch. PRODUCT.md's Known Problems already
   flags "README lags the product" as an existing gap for its actual
   audience (the human user); growing that same section into a
   version-pinned external integration contract makes README worse at its
   job (a fast human quickstart) to make it moderately better at a job
   (stable API reference) a dedicated file does more cleanly, and gives jig
   no single anchor to diff across viva versions the way a dedicated file's
   version marker does.

## Operational readiness

- **Migration:** none — a new Markdown file, no schema or state change.
- **Rollback:** revert the file's commit. Nothing in this epic's other two
  stories, and nothing in `server.py` or `schema.py`, reads or depends on
  this file at runtime — it has no executable effect to unwind.
- **Rollout:** ships as a normal doc commit; live as of whatever commit/tag a
  downstream integrator pins to. No flag.
- **Observability — is the doc still accurate?** Two mechanisms, both
  manual, matching PRODUCT.md's "local and keyless ... no hosted service"
  (no CI/observability infra to lean on for a docs-only artifact):
  - The **Contract version** line plus its changelog table is the durable
    signal: a future change to `--mode`'s choices, a round-file's required
    field, an exit code's meaning, or an endpoint's shape is expected to bump
    the version and add a changelog row in the same commit — the same
    discipline `scripts/preferences.py`'s `VERSION = 1` already establishes
    for its own on-disk shape, applied here to a doc instead of a JSON store.
  - Proposed for the build phase (not mandated by this design, since
    CLAUDE.md's test requirement targets features/bug fixes, and this is a
    docs-only deliverable): a small `tests/test_headless_contract_doc.py`
    that asserts a handful of load-bearing facts stay true — e.g. that
    `server.py`'s argparse `--mode` choices are exactly the set the doc's
    mode table lists, and that a `Contract version: <int>` line is present
    and parses. A cheap drift guard, in the project's existing stdlib-only +
    plain-assertion test idiom, not a full schema-conformance suite.
- **Failure mode:** if the doc goes stale (server.py's actual behavior
  diverges silently from what's written), the blast radius is entirely
  external — viva itself has no runtime dependency on this file, but jig
  would be integrating against a documented behavior that no longer holds.
  That asymmetry (zero internal risk, real external risk) is why the version
  marker and changelog discipline above matter more for this doc than for a
  typical internal one.

## Open questions

- **Whether jig should pin by git ref/tag in addition to the in-file version
  marker.** The marker is deliberately git-history-agnostic (survives a
  squash-merge or repo migration); whether jig's own tooling additionally
  wants to pin a specific viva commit is an integration-time decision left
  to jig, not something this doc needs to prescribe.
- **Whether the proposed drift-guard test (see Operational readiness) lands
  in this story's build phase or as a follow-up.** Flagged as a proposal,
  not a requirement, since it is not itself a code feature or bug fix in
  CLAUDE.md's sense — left to the build phase's judgment.
- **Whether the unbounded "processing" spinner caveat (qa→review hand-off,
  Proposed design §6) belongs in this protocol doc versus being tracked as
  its own UX follow-up against `unified-session`.** This design includes it
  as a caller-facing caveat because a headless integrator needs to know
  about it *before* they can safely build the hand-off, but the underlying
  browser-side fix (if any) is out of scope for a documentation story.
