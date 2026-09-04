# viva

Section-by-section markdown review for Claude Code. See `PRODUCT.md` for the
product definition, `DESIGN.md` for the design system and protocol conventions.

## Comment and prose style

Comments, docstrings, and this file's own prose: 2-3 lines max. State what,
and why only if it's non-obvious — cut historical narration, restated
context a reader can get from the code or `git log`, and cross-references
to other comments explaining the same thing twice. Point at an issue number
instead of retelling its story.

## Architecture

viva is a **multi-process pipeline, not a single server**. Five parts, coupled
only by JSON files under `.viva/`:

1. **`SKILL.md` — the judgment layer.** The agent still drives the loop, but it
   drives it by *calling* `scripts/loop.py`, not by executing bookkeeping prose.
   What stays in SKILL.md is the work only a model can do: rewriting from typed
   comments, answering threads, clustering preferences, and routing on the
   classification `loop.py wait` prints. What left is the bookkeeping — round
   numbers, the state clear, liveness, the finish guard.
2. **`scripts/loop.py` — the driver.** One `main()` with seven subcommands
   (`start`, `annotate`, `arm`, `wait`, `rearm`, `finish`, `abandon`). It owns
   the round counter (derived from the highest `review-input-r{N}.json` on disk,
   never passed or remembered), the three round-1 branches, liveness, and the
   refusal to finish a round the human has not approved. It **invokes its
   siblings as subprocesses** rather than importing them, so part 3's
   one-cross-import rule holds unchanged.
3. **`scripts/*.py` — stateless CLI filters** (`parse_sections`, `parse_diff`, `annotate`,
   `context_refs`, `review_target`, `drift`, `checklist`, `doc_types`,
   `headings_present`, `open_notes`, `preferences`, `revision_history`,
   `docket`). Each
   is stdlib-only, run as `python3 scripts/<name>.py`, and reads/writes JSON.
   They import no sibling **except** the shared contract, `schema.py` (below) —
   keep that the only cross-import so each stays independently testable.
   `docket.py` (#173) is the one exception to "one round file in, one round
   file out": it sweeps every `.viva/` session under a set of roots and
   reports a status line per session (`--format text|json`). Run by a human
   or agent in a terminal, deliberately never wired into `server.py` (its
   own docstring explains why).
4. **`server.py` — the SPA host** (the embedded HTML/CSS/JS constant `HTML` is
   the overwhelming majority of it; the Python HTTP handler around it is
   small). The bulk being a frontend is intentional — one file, no build
   step, no npm. Don't "fix" the line count by splitting the constant out.
   `server.py` carries one documented exception to part 3's one-cross-import
   rule: it imports `preferences.py` directly for its pure
   `empty_store`/`select`/`set_status` helpers, rather than shelling out as
   `loop.py` does — read/derive-only, so it doesn't reopen the
   independent-testability guarantee. `tests/test_server_orchestration.py`'s
   `check_server_cross_imports_only_schema_and_preferences` pins the
   exception to exactly those two modules. Its one read outside `.viva/` is
   `assets/vendor/`: ten pinned third-party browser assets (#79, #144) — six
   JS/CSS bundles plus four Fragment Mono woff2 subsets — served at
   `/vendor/<file>` from an exact-match route table resolved off `__file__`,
   not the cwd. A version bump edits three places — the file,
   `_VENDOR_ASSETS`, and the URL in `HTML` — and `test_server_vendor_assets.py`
   compares the last two directly, because missing one 404s into the
   `md-raw` fallback with no error anywhere. A font's third place is
   `@font-face { src: url('/vendor/…') }` in `HTML`'s `<style>`, harvested
   separately by the same test, since a missed font URL fails invisibly into
   a system font. Nothing in the page reaches a remote host, fonts included;
   `tests/test_typography.py` forbids the host by name.
   It also owns **`_VOICE_VERBS`/`_VOICE_RULES`** — the spoken grammar of the
   voice layer, injected as `__VOICE_RULES__` the way `__CHECK_KINDS__` is,
   deliberately here rather than in `schema.py` since the browser is its
   only consumer. Adding a `COMMENT_TYPES` value fails
   `tests/test_voice_grammar.py` until it has something a reviewer can say.
   The layer's invariant — speech STAGES a comment and never commits one, so
   nothing in it may call `addComment` — is pinned by
   `tests/test_server_voice.py`; DESIGN.md says why.

   **The embedded JS has no execution seam, by the same no-npm decision.**
   CI runs no JS runner, so tests verify `HTML`'s ~240 functions by asserting
   source substrings, never by executing them — the accepted cost of
   principle 6's stdlib-only rule (`PRODUCT.md`). `deriveVerdict`, which
   derives the section verdict from active comments (`PRODUCT.md`'s Feature
   map), is thus verified only by string match client-side; the server's
   `schema.validate_verdicts` checks a submitted verdict is a `VERDICTS`
   member, not that it agrees with the comments that produced it. Closing
   that gap is a `schema.py` change, not a JS-runner one.
5. **The `.viva/*.json` schema — the real contract.** See `scripts/schema.py`
   for the shapes.

## The schema is the contract

`scripts/schema.py` is the single shared module `scripts/*.py` and `server.py`
import. It holds:

- **`section_key(title)`** — the ONE section-identity normalization. Approval
  carry-forward, annotation carry-forward, round-to-round diffs, and open-note
  threads all key on it, so a title edit changes identity in exactly one place.
  Never reimplement it inline. (Note: `checklist.py._norm` is deliberately
  *different* — it strips all punctuation for tolerant template matching, a
  fuzzy match, not an identity. Don't fold the two together.)
- **`verdict_to_ledger_entry()`** — the single rule for which verdicts become a
  Revision-History row and how the note is derived (join `comments[]`, else the
  section `note`). Both the live `/input` ledger and `revision_history.py` use it.
- **TypedDicts** (`ReviewInput`, `ReviewSection`, `Annotation`, `ReviewPass`,
  `SectionVerdict`, `ReviewOutput`) documenting the round shapes. Documentation
  only — CI runs no type checker; the validators carry the enforced rules.
- **The vocabulary tuples** — the closed sets every reader agrees on:
  `VERDICTS` (a section's state), `COMMENT_TYPES` (a reviewer's per-comment
  type, including `SUGGESTION` — **a different axis from `VERDICTS`**; a
  suggestion derives to the section verdict `changes` and is never a verdict),
  `THREAD_STATUSES` plus `thread_is_unresolved()` (`open` and `declined` are
  both live; only `settled` closes — membership, never `!= settled`, so an
  unknown status is not silently treated as live), `PASS_KINDS`/`PASS_POSTURES`,
  `CHECK_KINDS`, and `DOC_SCOPE_KINDS`. Add a value here, not at a call site.

  `DOC_SCOPE_KINDS` is **a different axis from `CHECK_KINDS`** — that one asks
  "does this gate a `checks` round", this one asks "what is this flag ABOUT" —
  and `headings-present` is deliberately in both. It fails open the same way:
  an unregistered kind is treated as section-scope and piles onto whichever
  card its producer anchored it to. Injected into the frontend as
  `__DOC_SCOPE_KINDS__`, exactly as `__CHECK_KINDS__` is.
- **`round_is_complete()`** — the single completion rule: may this round be
  signed off? Both `loop.py finish` and `server.py`'s `POST /complete` handler
  ask it, from separate processes, so the invariant lives in one place. Pure —
  dicts in, bool out, no disk. Anything that reads `.viva/` or decides policy
  beyond the predicate does not belong here.

  **The conjunct-only invariant — the milestone's load-bearing guarantee.**
  The base is every section in the round *input* carrying an `approved`
  verdict. A round's optional `pass` may only **ADD** a condition to that base,
  never relax it: `architecture`/`line` add none, `checks` also requires every
  `CHECK_KINDS` flag to carry a non-empty `result`, `final` also requires no
  unresolved suggested edit. Run the base first and return early — a branch
  that dispatches on `pass` before checking it reopens the hole #102 closed.
  Enforced at both call sites, both enumerating `PASS_KINDS`:
  `tests/test_schema.py`'s `test_no_pass_relaxes_the_all_approved_base` at the
  predicate, and `tests/test_server_pass.py`'s
  `check_a_pass_never_signs_off_an_unapproved_round` at the wire. One
  exception, not a relaxation: a `--mode diff` server's `/complete` honors
  `resolved: "empty"` in the body (#177) — the caller's assertion the
  re-capture came back empty — and skips the predicate for that finish alone;
  any other `resolved`, or one on a review server, is a `400`.
- **`has_revision_history()`** — has this doc already been signed off? Anchored,
  never a substring test: `loop.py`'s resume detection and
  `revision_history.py`'s append-vs-create branch ask the same question, and a
  bare `in` also matches the phrase inside backticks (viva's own SKILL.md
  contains it).
- **`validate_review_input` / `validate_verdicts`** — boundary validators.

**Adding a field to the round schema is a coordinated edit.** Update: the
TypedDict in `schema.py`, `parse_sections.py` (the producer), `server.py`'s load
and the embedded JS that renders it, `scripts/loop.py` if the driver must carry
it between rounds, and any store script that carries it forward. A field the
server only passes through needs no `server.py` change — `load_input` is a bare
`json.load` and `/next-round` replaces `_input_data` wholesale — but say so
rather than leaving the omission to be re-derived. `split_on`, `doc_type`, and
`pass` are that case: none renders, and `pass` reaches `server.py` only through
`round_is_complete()`. All three are also **presence-gated** in
`validate_review_input` — optional key, but a present *malformed* value is a
hard failure, since each feeds a `parse_sections.py` flag and a `null` would
silently revert the next round instead of failing where it was written.
`split_on`/`doc_type` must be strings; `pass` is checked three ways (an object,
a `kind` in `PASS_KINDS`, and — if present — a `posture` in `PASS_POSTURES`),
since it's the one field that moves the completion gate.

A section's **`summary`** is presence-gated the same way but is the opposite
case: per-section, not per-round, and `server.py` *renders* it under the card
title (`buildReviewCard`, `buildDocSection`). Nothing mechanical writes it; the
agent does, between parsing and arming (`viva-review` B1a) — a `null` would
print as `null` under the title, so loud validation matters more here.

**Carry rules differ per field, deliberately.** `split_on` and `doc_type` are
session identity: `loop.py rearm` carries them round to round *and*
`cmd_start`'s resume branch carries them across sessions. `pass` is a
per-round decision — `rearm` carries it, a resume does **not**, since
inheriting a finished session's `final` pass would add an unasked-for
conjunct. `summary` carries only onto a **byte-identical** section
(`parse_sections._carry_identical`, shared with the annotation carry, and
`parse_diff._carry_summaries`) — changed content gets a stale description, so
it drops and is rewritten.

**Validate at the boundary** — parse write and server read — never at the
point of use. A field a reader forgets silently drops a feature; the boundary
validator turns that into a loud failure.

Both review-input read boundaries are **unconditional and mode-keyed**: a gate
that keys on the payload's own shape validates nothing when the shape is the
thing that's wrong. `POST /next-round` validates every body (the old
`if "sections" in new_data` gate let a round nested one level deep through
with `{"ok":true}`, replaced the served round, and bricked the tab silently),
and startup validation asks `args.mode`, not the payload — the same rule
`/complete`'s guard follows. The browser's SSE `round` handler carries a
matching refusal as a strand backstop: the cost of the server being wrong is a
tab frozen forever, so the handler turns away a payload with no `sections[]`
before overwriting `REVIEW_DATA`.

`GET /input` serves the review-input merged with a live `ledger: [...]` key,
injected at serve time and not part of the on-disk schema. A `repo` key
(`_viva_dir.parent.name`) is injected into `GET /input` and the `round` SSE
event the same way, serve-time only, for the browser tab's title (#172).

## Two skills, split by intent

`.claude/skills/` holds exactly two: **`viva-write`** (make a thing) and
**`viva-review`** (judge one). The mechanism-named trio — `viva`, `viva-qa`,
`viva-diff` — is gone. `viva-review` absorbed doc *and* hunk review behind one
target dispatch (`scripts/review_target.py`), and the Q&A gate became
`references/qa.md`, a contract any caller can read rather than a skill you must
know to find. `tests/test_skill_registration.py` pins both the expected set
and the retired names.

**`references/` sits at the plugin root**, not inside either skill: both read it
and `loop.py` prints its paths, so `/viva-write` needing `producers.md` must not
mean reaching into `/viva-review`'s directory.

**`references/style.md` is the register**, the one reference about the prose
itself: the point first, decisions as fact, one term per concept, no provenance
or preamble, a trim pass before parse. Its rules are practitioner consensus
(Google's, Microsoft's, Nielsen Norman's) and it cites them, so a rule is
checkable against a source rather than defended as taste. `/viva-write` step 4
reads it before drafting and `loop.py wait` prints its path on every
`has-work` round. It's a rail, not craft — fixes what a doc may not carry,
never what to argue, the line `PRODUCT.md`'s "not a writing assistant" draws.
It edits the agent's prose only: a `suggestion` is still pasted verbatim and a
`changes` comment asking for more wins. `tests/test_writing_register.py` pins
those seams.

**The no-bookkeeping-bash rule has no exemptions.** `loop.py` drives doc
review, hunk review, and the intake interview (#179), so every bash block in
both skills is held to it. `test_server_orchestration.py` asserts the set of
skills carrying their own loop is empty, by name.

**Two review modes, one driver.** `start --doc` is the doc form; `start
--target <pr|ref>` or `start --kind worktree` is the diff form, and the two
are refused together. The diff form runs `review_target.py` as a subprocess
(filesystem first, so `--target docs/x.md` is the doc form spelled the other
way), saves the record plus the capture's `cwd` to `.viva/target.json`, runs
the `capture` argv into `.viva/diff.patch`, and parses with `parse_diff.py`.
Every subcommand after `start` reads the mode off the round file, so the agent
types neither a mode nor a round. A failed capture unlinks the patch before it
dies — a 0-byte `diff.patch` left behind would read as "no changes" and sign
the session off with nothing reviewed. Diff-mode `rearm` re-runs the recorded
capture (never a different form), reports an empty re-capture and arms
nothing, and refuses `--response`/`--decline`/`--pass`. Diff-mode `finish`
re-captures for itself: empty → `/complete` with `resolved: "empty"`;
identical to round N and all-approved → normal finish; anything else (the
human approved a hunk and the agent kept editing) → refused, `rearm`. The summaries seam is the driver's too: above `SUMMARY_THRESHOLD`
hunks with any lacking a `summary`, `start` and `rearm` stop after parsing, and
`loop.py summarize --map <path|->` merges a `{id: one line}` map pre-arm.

## `/viva-write` — the intake end of the lifecycle

`/viva` reviews a doc that exists; `/viva-write` produces one and hands it to
that same review **in the same server process**. The flow is type → attach →
interview → draft → hand off → rounds → stamp, and it is a skill precisely
because steps 2 and 4 are model work.

`loop.py` drives all of it. `loop.py interview` runs the Q&A gate: `start`'s
state clear plus `answers.json`, the `--mode qa` launch, a liveness-aware wait,
the answers on stdout, and one classification line last (`answered` /
`submitted-early`). It never calls `/complete` — the hand-off reuses the
process. `loop.py start --doc … --handoff` then parses round 1 **into** that
live interview instead of refusing over it: the flag is explicit, never
inferred from a live qa payload, so an abandoned interview cannot quietly
become the next `/viva-review`'s tab. It requires a live server serving
`questions`, keeps `server.url` and `attachments/`, never touches
`answers.json`, and skips the resume branch (a fresh draft's ledger heading is
a false positive). `arm` gates its POST branch on `probe_input` (liveness),
not `probe_round` — a qa payload carries no `round` key.

**Two orderings this flow depends on, both enforced rather than documented:**

- **Producers run before the hand-off, never after.** The server loads its
  round once and replaces it only from `/next-round`, so `loop.py annotate`
  refuses a round the server already holds; it *passes* before the hand-off
  because the live qa server's `probe_round` returns `None`.
  `tests/test_viva_write_flow.py` asserts both sides of it. The bundle's own
  `checks[]` don't depend on the agent remembering the order: `start --type`
  runs them itself, between parse and any arming branch, by the mechanical
  mapping `<name with - as _>.py --input <round> --bundle -`, merging flags
  through `annotate.py`. A check name is validated beside the type, before the
  clear — a repo bundle naming a script this plugin does not ship is refused
  with the prior state intact. `rearm` does not re-run them: flags carry onto
  byte-identical sections with their `result`, and `annotate._same_flag`
  ignores `result`.
- **`--pass <bundle.default_pass>` is passed explicitly.** `/viva-write` is
  `default_pass`'s first consumer — `loop.py start` resolves a bundle and only
  prints it — so a typed session that drops the flag runs at no depth and looks
  identical on screen.

`scripts/context_refs.py` is the intake's one new mechanical surface: it
classifies attachments (issue/pr refs, URLs, files, directories) and bounds
what a directory expands to, emitting a manifest with every cap-excluded file
in `dropped[]`. It **never fetches** — an issue entry carries the `gh` argv the
skill runs — keeping it network-free, keyless, and independently testable.
`scripts/review_target.py` is its counterpart on the review side: it
classifies a target and prints the `capture` argv, running no `git`/`gh`
itself. Its precedence is **filesystem first, then shape** — a repo holding a
file named `187` means that file, not the PR — so a branch named `42` needs
`--kind ref`.

## Extension seams

- **New pre-review check → a producer, through `annotate.py`.** Producers emit a
  sidecar list of `{id, kind, severity, message, anchor?, result?}` flags that
  `annotate.py` merges into the round's review-input (additive, idempotent).
  This is the preferred extension point — add a producer, not a server endpoint
  or a new schema field. (Confidence annotations also carry `basis`/`level`,
  preserved through the merge.)

  **A producer whose flags must gate a `checks` round registers its `kind`
  in `schema.CHECK_KINDS`.** That registry **fails open**: an unregistered
  kind raises no error anywhere — it becomes invisible to `round_is_complete`,
  so its flags gate nothing and a `checks` round closes where it should have
  held. `result` answers such a flag, and only in the round *about to be
  armed* — the server replaces its round only from `POST /next-round`, so a
  merge into the file under a live round is one `/complete` never sees.
  `loop.py annotate` refuses that case outright.

  **A producer reporting a fact about the WHOLE DOCUMENT registers its `kind`
  in `schema.DOC_SCOPE_KINDS` as well.** Without it, `parse_sections.py`'s
  integrity check leaves the first card as the producer's only anchor and its
  flags render in section 1's margin instead. Registered, they render once,
  in the document slip above the print.
- **Doc-type bundles.** A type is section grammar + check set + default pass,
  one JSON file per name: shipped defaults in `types/`, a repo's overrides in
  `.viva-types/`, the repo's copy winning **wholesale** on a name collision so
  it can drop a shipped check as well as add one. Both directories are
  committed config, deliberately outside `.viva/`, which is cleared every
  `start`. `scripts/doc_types.py` is the only place a name becomes a bundle
  and the read boundary that validates one; a bundle's `checks[]` names
  producers by the mechanical mapping `<name with - as _>.py`. Two invariants
  the shipped set keeps and `tests/test_doc_types.py` pins: a non-empty
  grammar names `headings-present`, and a `checks` default pass names at
  least one check. Every shipped grammar with a build in it carries a
  **Verification** heading, and `progress-note` is the handoff shape (Goal,
  Done, Next, Blockers, Gotchas) — the agent-era sources `references/style.md`
  cites converge on both.
- **State lifecycle.** `preferences.json` survives the round-1 state clear (it
  is cross-session, gitignored, per-clone); everything else under `.viva/` is
  disposable and reset each session. Don't add new state that must survive
  without documenting why here. The state clear lives in `scripts/loop.py`'s
  `_clear_state`, not in prose — it removes the round files, `server.url`,
  `open-notes.json`, `target.json`, `diff.patch`, and `attachments/`.
  `interview` adds `answers.json` to it (a stale one would satisfy the wait
  before the human typed a word); `start` deliberately does not, since
  `start --handoff` runs while the draft written from those answers is still
  the agent's source. `qa-input.json` is written by the caller and cleared by
  nobody — it's `interview`'s own `--input`. `start --handoff` keeps
  `server.url` and `attachments/` since the interview server that receives the
  round owns them. A finished `/viva-write` leaves `qa-input.json` and
  `answers.json` beside the round files; `docket.py` classifies on the round
  files first, so its `qa` bucket is unaffected. One documented exception:
  `cmd_start`'s resume branch copies a completed session's finishing round to
  `.viva/prior-review-input.json` / `.viva/prior-review-verdicts.json` just
  long enough to survive the clear and feed the new session's
  `--prior-input`/`--prior-verdicts`, then discards them in a `finally`.

## Tests

Stdlib-only, self-running scripts under `tests/` (each has a `main()` and prints
`OK`). CI runs every file across Python 3.8–3.13 as `python3 tests/test_*.py`;
there is no pytest dependency and no type checker. New features need a test;
match the existing subprocess + `urllib` pattern for server integration tests
(see `tests/test_server_ledger.py`) and the plain-assertion pattern for units.
