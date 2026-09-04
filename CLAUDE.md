# viva

Section-by-section markdown review for Claude Code. See `PRODUCT.md` for the
product definition, `DESIGN.md` for the design system and protocol conventions.

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
   file out": rather than filtering a single `.viva/`'s round, it sweeps
   every `.viva/` session under a set of roots and reports a status line per
   session (`--format text|json`; still stdlib-only JSON on the `json` path,
   like every other filter here). Run by a human or agent in a terminal and
   deliberately never wired into `server.py` (its own docstring explains
   why).
4. **`server.py` — the SPA host** (the embedded HTML/CSS/JS constant `HTML` is
   the overwhelming majority of it; the Python HTTP handler around it is
   small). The bulk being a
   frontend is intentional — one file, no build step, no npm. Don't "fix" the
   line count by splitting the constant out. `server.py` carries one documented
   exception to part 3's one-cross-import rule: it imports `preferences.py`
   directly, for its pure `empty_store`/`select`/`set_status` helpers, rather
   than shelling out as `loop.py` does — those three are read/derive-only, so
   the exception doesn't reopen part 3's independent-testability guarantee.
   `tests/test_server_orchestration.py`'s
   `check_server_cross_imports_only_schema_and_preferences` pins the exception
   to exactly those two modules; every other `scripts/*.py` file is still
   checked against `schema` alone. Its one read outside `.viva/` is
   `assets/vendor/`: ten pinned third-party browser assets (#79, #144) — six JS
   and CSS bundles plus four Fragment Mono woff2 subsets — served at
   `/vendor/<file>` from an exact-match route table, resolved off `__file__`
   rather than the cwd and read per request. Committed config, like `types/`. A
   version bump edits three places — the file, `_VENDOR_ASSETS`, and the URL in
   `HTML` — and `test_server_vendor_assets.py` compares the last two directly,
   because missing one 404s into the `md-raw` fallback with no error anywhere.
   For a **font** that third place is spelled `@font-face { src: url('/vendor/…') }`
   inside `HTML`'s own `<style>`, not a `<script src>`, and the test harvests
   that spelling separately — a missed font URL 404s into an invisible
   system-font fallback rather than a visible one. Nothing in the page reaches a
   remote host, fonts included; `tests/test_typography.py` forbids the host by
   name so a reinstated `<link>` fails instead of shipping.
   It also owns **`_VOICE_VERBS`/`_VOICE_RULES`** — the spoken grammar of the
   voice layer, injected as `__VOICE_RULES__` the way `__CHECK_KINDS__` is, and
   deliberately here rather than in `schema.py`: the browser is its only
   consumer, so it is UI data, not the on-disk contract. Adding a
   `COMMENT_TYPES` value fails `tests/test_voice_grammar.py` until it has
   something a reviewer can say. The layer's own invariant — speech STAGES a
   comment and never commits one, so nothing in it may call `addComment` — is
   pinned by `tests/test_server_voice.py`; DESIGN.md says why that is
   load-bearing rather than stylistic.

   **The embedded JS has no execution seam, by the same no-npm decision.**
   CI runs no JS runner, so tests verify `HTML`'s ~240 functions by asserting
   source substrings are present, never by executing them. This is a
   deliberate, accepted cost of principle 6's stdlib-only rule (`PRODUCT.md`),
   not an oversight: the alternative is a JS test runner, which is the exact
   dependency the principle refuses. It means a function carrying a product
   invariant end to end — `deriveVerdict` deriving the section verdict from
   its active comments, never a directly-picked value (`PRODUCT.md`'s Feature
   map) — is verified only by string match on the client side; the server-side
   boundary validator (`schema.validate_verdicts`) checks that a submitted
   verdict is a member of `VERDICTS`, not that it agrees with the comments
   that produced it. Closing that gap, if it is ever worth the added
   validator complexity, is a `schema.py` change, not a JS-runner one.
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
  and `headings-present` is deliberately in both. It fails open the same way: an
  unregistered kind is treated as section-scope and piles onto whichever card
  its producer anchored it to. It is injected into the frontend as
  `__DOC_SCOPE_KINDS__`, exactly as `__CHECK_KINDS__` is, for the same
  anti-drift reason.
- **`round_is_complete()`** — the single completion rule: may this round be
  signed off? Both `loop.py finish` and `server.py`'s `POST /complete` handler
  ask it, from separate processes, so the invariant lives in one place. Pure —
  dicts in, bool out, no disk. Anything that reads `.viva/` or decides policy
  beyond the predicate does not belong here.

  **The conjunct-only invariant — the milestone's load-bearing guarantee.**
  The base is every section in the round *input* carrying an `approved`
  verdict. A round's optional `pass` may only **ADD** a condition to that base
  and may never relax it: `architecture`/`line` add none, `checks` also
  requires every `CHECK_KINDS` flag to carry a non-empty `result`, `final` also
  requires no unresolved suggested edit. Run the base first and return early;
  a branch that dispatches on `pass` before checking it reopens the hole #102
  closed. Enforced at both call sites, and both enumerate `PASS_KINDS` so a
  fifth kind is covered the day it lands: `tests/test_schema.py`'s
  `test_no_pass_relaxes_the_all_approved_base` at the predicate, and
  `tests/test_server_pass.py`'s `check_a_pass_never_signs_off_an_unapproved_round`
  at the wire. The one thing that is not a conjunct is not a relaxation of the
  predicate either: a `--mode diff` server's `/complete` honors `resolved:
  "empty"` in the body (#177) — the caller's assertion that the re-capture came
  back empty, so there is nothing left to approve — and skips the predicate for
  that finish alone. `loop.py finish` derives it from a fresh capture; a round
  nobody has submitted is still refused first; any other `resolved`, or one on
  a review server, is a `400`.
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
`pass` are all that case: none renders, and `pass` reaches `server.py` only
through `round_is_complete()`. All three are also **presence-gated** in
`validate_review_input` — the key is optional, but a present *malformed* value is
a hard failure, because each is handed straight back to a `parse_sections.py`
flag and a `null` would silently revert the next round rather than fail where the
bad value was written. `split_on`/`doc_type` must be strings; `pass` is checked
three ways (an object, a `kind` in `PASS_KINDS`, and — if present — a `posture`
in `PASS_POSTURES`), because it is the one field that moves the completion gate.

A section's **`summary`** is presence-gated on the same rule (optional key,
present non-string is a hard failure) but is otherwise the opposite case: it is
per-section rather than per-round, and `server.py` *renders* it — under the card
title in both builders, `buildReviewCard`'s accordion head and `buildDocSection`'s
print. Nothing mechanical writes it; the agent does, between parsing and arming
(`viva-review` B1a), which is why no producer and no `parse_*` flag exists for
it. Loud validation matters more here than for a passthrough: a `null` would
print as `null` under the title.

**Carry rules differ per field, deliberately.** `split_on` and `doc_type` are
session identity: `loop.py rearm` carries them round to round *and* `cmd_start`'s
resume branch carries them across sessions. `pass` is a per-round decision —
`rearm` carries it, a resume does **not**, because inheriting a finished
session's `final` pass would add a conjunct nobody asked for. `summary` carries
only onto a **byte-identical** section (`parse_sections._carry_identical`, the
mechanism the annotation carry shares, and `parse_diff._carry_summaries`) —
content that changed gets a stale description, so it drops and is rewritten.

**Validate at the boundary** — on parse write and server read — never
at the point of use. A field that a reader forgets silently drops a feature; the
boundary validator is what turns that into a loud failure.

Both review-input read boundaries are **unconditional and mode-keyed**, and for
one reason: a gate that keys on the payload's own shape validates nothing when
the shape is the thing that is wrong. `POST /next-round` validates every body
(the old `if "sections" in new_data` gate let a round nested one level deep
through with `{"ok":true}`, replaced the served round, and bricked the tab with
no error on either side), and startup validation asks `args.mode`, not the
payload — the same rule `/complete`'s guard already follows, "the exemption keys
on the launch mode, not the round payload". The browser's SSE `round` handler
carries a matching refusal, which is a strand backstop rather than a second
copy of the boundary: the cost of the server being wrong is a tab frozen
forever, so the handler turns away a payload with no `sections[]` before it
overwrites `REVIEW_DATA`.

`GET /input` serves the review-input merged with a live `ledger: [...]` key; that
`ledger` is injected at serve time and is not part of the on-disk file schema.
A `repo` key (`_viva_dir.parent.name`) is injected into `GET /input` and the
`round` SSE event the same way — serve-time only, not on-disk schema — for the
browser tab's title (#172).

## Two skills, split by intent

`.claude/skills/` holds exactly two: **`viva-write`** (make a thing) and
**`viva-review`** (judge one). The mechanism-named trio — `viva`, `viva-qa`,
`viva-diff` — is gone. `viva-review` absorbed doc *and* hunk review behind one
target dispatch (`scripts/review_target.py`), and the Q&A gate became
`references/qa.md`, a contract any caller can read rather than a skill you must
know to find. `tests/test_skill_registration.py` pins both the expected set and
the retired names, because a stale directory alongside the new set registers a
second skill for the same job.

**`references/` sits at the plugin root**, not inside either skill: both read it
and `loop.py` prints its paths, so `/viva-write` needing `producers.md` must not
mean reaching into `/viva-review`'s directory.

**`references/style.md` is the register**, the one reference about the prose
itself: the point first, decisions as fact, one term per concept, no provenance
or preamble in the doc text, a trim pass before parse. Its rules are the
practitioner consensus (Google's technical-writing course and style guide,
Microsoft's, Nielsen Norman's reading studies) and it cites them, so a rule is
checkable against a source rather than defended as taste. `/viva-write` step 4 reads it before
drafting and `loop.py wait` prints its path on every `has-work` round, so both
flows rewrite in it. It is a rail, not craft — it fixes what a doc may not carry,
never what to argue — which is the line `PRODUCT.md`'s "not a writing assistant"
draws. It edits the agent's prose only: a `suggestion` is still pasted verbatim
and a `changes` comment asking for more wins. `tests/test_writing_register.py`
pins those seams.

**The no-bookkeeping-bash rule has no exemptions.** `loop.py` drives doc review,
hunk review, and the intake interview (#179), so every bash block in both skills
is held to it. `test_server_orchestration.py` asserts the set of skills carrying
their own loop is empty, by name, so a skill growing one fails.

**Two review modes, one driver.** `start --doc` is the doc form; `start --target
<pr|ref>` or `start --kind worktree` is the diff form, and the two are refused
together. The diff form runs `review_target.py` as a subprocess (filesystem
first, so `--target docs/x.md` is the doc form spelled the other way), saves the
record verbatim plus the capture's `cwd` to `.viva/target.json`, runs the
`capture` argv into `.viva/diff.patch`, and parses with `parse_diff.py`. Every
subcommand after `start` reads the mode off the round file — `arm` launches
`--mode <that>`, `rearm` and `finish` branch on it — so the agent types neither
a mode nor a round. A failed capture unlinks the patch before it dies: a 0-byte
`diff.patch` left behind would be read as "no changes" and sign the session off
resolved with nothing reviewed. Diff-mode `rearm` re-runs the recorded capture
(never a different form — a `git diff` substituted on round 2 of a PR review
reviews the working tree), reports an empty re-capture and arms nothing, and
refuses `--response`/`--decline`/`--pass`. Diff-mode `finish` re-captures for
itself: empty → `/complete` with `resolved: "empty"`; identical to round N and
all-approved → the normal finish; anything else (the human approved a hunk and
the agent kept editing) → refused, `rearm`. The summaries seam is the driver's
too: above `SUMMARY_THRESHOLD` hunks with any lacking a `summary`, `start` and
`rearm` stop after parsing, and `loop.py summarize --map <path|->` merges a
`{id: one line}` map pre-arm, refusing an armed round as `annotate` does.

## `/viva-write` — the intake end of the lifecycle

`/viva` reviews a doc that exists; `/viva-write` produces one and hands it to
that same review **in the same server process**. The flow is type → attach →
interview → draft → hand off → rounds → stamp, and it is a skill precisely
because steps 2 and 4 are model work.

`loop.py` drives all of it. `loop.py interview` runs the Q&A gate: `start`'s
state clear plus `answers.json`, the `--mode qa` launch, a liveness-aware wait
(exit 2 the moment `server.url` is gone, the contract `wait` already keeps), the
answers on stdout, and one classification line last (`answered` /
`submitted-early`). It never calls `/complete` — the hand-off reuses the
process. `loop.py start --doc … --handoff` then parses round 1 **into** that
live interview instead of refusing over it: the flag is explicit, never inferred
from a live qa payload, so an abandoned interview cannot quietly become the next
`/viva-review`'s tab; it requires a live server serving `questions`, keeps
`server.url` and `attachments/` (the answers may cite files there), never touches
`answers.json`, and skips the resume branch (a fresh draft's ledger heading is a
false positive). `arm` gates its POST branch on `probe_input` (liveness), not
`probe_round` — a qa payload carries no `round` key, and reading that as "nothing
is answering" was the second thing that kept the driver out of this flow.

**Two orderings this flow depends on, both enforced rather than documented:**

- **Producers run before the hand-off, never after.** The server loads its round
  once and replaces it only from `/next-round`, so `loop.py annotate` refuses a
  round the server already holds. It *passes* before the hand-off because the
  live qa server's `probe_round` returns `None` — that is the seam, and
  `tests/test_viva_write_flow.py` asserts both sides of it. The bundle's own
  `checks[]` no longer depend on the agent remembering the order: `start --type`
  runs them itself, between the parse and every branch that could arm, by the
  mechanical mapping `<name with - as _>.py --input <round> --bundle -` with the
  bundle on stdin, and merges the flags through `annotate.py`. A check name is
  validated beside the type, before the clear — a repo bundle naming a script
  this plugin does not ship is refused with the prior state intact. `rearm`
  does not re-run them: flags carry onto byte-identical sections with their
  `result`, and `annotate._same_flag` ignores `result`, so a re-run would land
  on the answered flag and change nothing.
- **`--pass <bundle.default_pass>` is passed explicitly.** `/viva-write` is
  `default_pass`'s first consumer — `loop.py start` resolves a bundle and only
  prints it — so a typed session that drops the flag runs at no depth and looks
  identical on screen.

`scripts/context_refs.py` is the intake's one new mechanical surface: it
classifies attachments (issue/pr refs, URLs, files, directories) and bounds what
a directory expands to, emitting a manifest with every cap-excluded file in
`dropped[]`. It **never fetches** — an issue entry carries the `gh` argv the
skill runs — which is what keeps it network-free, keyless, and independently
testable. `scripts/review_target.py` is its counterpart on the review side, with
the same no-execution rule: it classifies a target and prints the `capture` argv,
running no `git` and no `gh` itself. Its precedence is **filesystem first, then
shape** — a repo holding a file named `187` means that file, not the PR, because
a target the caller can see in `ls` must never be silently reinterpreted. The
cost is that a branch named `42` needs `--kind ref`.

## Extension seams

- **New pre-review check → a producer, through `annotate.py`.** Producers emit a
  sidecar list of `{id, kind, severity, message, anchor?, result?}` flags that
  `annotate.py` merges into the round's review-input (additive, idempotent).
  This is the preferred extension point — add a producer, not a server endpoint
  or a new schema field. (Confidence annotations also carry `basis`/`level`,
  preserved through the merge.)

  **A producer whose flags must gate a `checks` round registers its `kind`
  in `schema.CHECK_KINDS`.** That registry **fails open**: an unregistered kind
  raises no error anywhere — it simply becomes invisible to `round_is_complete`,
  so its flags gate nothing and a `checks` round closes where it should have
  held. `result` is the field such a flag is answered with, and it is answered in
  the round *about to be armed*, never the one already armed: the server loads
  its round once and replaces it only from `POST /next-round`, so a merge into
  the file on disk under a live round is one `/complete` never sees.
  `loop.py annotate` refuses that case outright.

  **A producer reporting a fact about the WHOLE DOCUMENT registers its `kind`
  in `schema.DOC_SCOPE_KINDS` as well.** Without it, `parse_sections.py`'s
  integrity check leaves the first card as the producer's only anchor and its
  flags render in the margin of section 1 — which is what put five amber lines
  in the first viewport of every typed round-1 review. Registered, they render
  once, in the document slip above the print.
- **Doc-type bundles.** A type is section grammar + check set + default pass, one
  JSON file per name: shipped defaults in `types/`, a repo's overrides in
  `.viva-types/`, the repo's copy winning **wholesale** on a name collision so it
  can drop a shipped check as well as add one. Both directories are committed
  config and deliberately outside `.viva/`, which is cleared every `start`.
  `scripts/doc_types.py` is the only place a name becomes a bundle and the read
  boundary that validates one; a bundle's `checks[]` names producers by the
  mechanical mapping `<name with - as _>.py`. Two invariants the shipped set
  keeps and `tests/test_doc_types.py` pins: a non-empty grammar names
  `headings-present` (a grammar nothing checks is decoration), and a
  `checks` default pass names at least one check (a `checks` round with no
  flags to answer closes on the base alone and the depth is a label). Every
  shipped grammar with a build in it carries a **Verification** heading, and
  `progress-note` is the handoff shape (Goal, Done, Next, Blockers, Gotchas):
  the agent-era sources converge on a runnable check in the doc and a fixed
  handoff form, and `references/style.md` cites them.
- **State lifecycle.** `preferences.json` survives the round-1 state clear (it is
  cross-session, gitignored, per-clone); everything else under `.viva/` is
  disposable and reset each session. Don't add new state that must survive
  without documenting why here. The state clear itself lives in
  `scripts/loop.py`'s `_clear_state`, not in prose — it removes the round files,
  `server.url`, `open-notes.json`, `target.json` (the diff form's dispatch record
  plus the cwd its capture runs in), `diff.patch` (the capture, re-run every
  `rearm` and `finish`), and the `attachments/` directory. `interview`
  adds `answers.json` to it (a stale one would satisfy the wait before the human
  typed a word) and `start` deliberately does not — a `start --handoff` runs
  while the draft written from those answers is still the agent's source.
  `qa-input.json` is written by the caller and cleared by nobody; it is
  `interview`'s own `--input`. `start --handoff` keeps `server.url` (the
  interview server that receives the round owns it) and `attachments/` (its
  answers may cite files there). A finished `/viva-write` leaves `qa-input.json`
  and `answers.json` beside the round files; `docket.py` classifies on the round
  files first, so its `qa` bucket is unaffected. The one documented exception:
  `cmd_start`'s resume branch copies a completed session's finishing round to
  `.viva/prior-review-input.json` / `.viva/prior-review-verdicts.json` just long
  enough to survive the clear and feed the new session's
  `--prior-input`/`--prior-verdicts`, then discards them in a `finally` —
  nothing new persists past that one resume.

## Tests

Stdlib-only, self-running scripts under `tests/` (each has a `main()` and prints
`OK`). CI runs every file across Python 3.8–3.13 as `python3 tests/test_*.py`;
there is no pytest dependency and no type checker. New features need a test;
match the existing subprocess + `urllib` pattern for server integration tests
(see `tests/test_server_ledger.py`) and the plain-assertion pattern for units.
