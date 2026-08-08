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
   `headings_present`, `open_notes`, `preferences`, `revision_history`). Each
   is stdlib-only, run as `python3 scripts/<name>.py`, and reads/writes JSON.
   They import no sibling **except** the shared contract, `schema.py` (below) —
   keep that the only cross-import so each stays independently testable.
4. **`server.py` — the SPA host** (7,312 lines, of which the embedded
   HTML/CSS/JS constant `HTML` — opened at line 75 — is the overwhelming
   majority; the Python HTTP handler around it is small). The bulk being a
   frontend is intentional — one file, no build step, no npm. Don't "fix" the
   line count by splitting the constant out. Its one read outside `.viva/` is
   `assets/vendor/`: six pinned third-party browser assets (#79, #144) served at
   `/vendor/<file>` from an exact-match route table, resolved off `__file__`
   rather than the cwd and read per request. Committed config, like `types/`. A
   version bump edits three places — the file, `_VENDOR_ASSETS`, and the URL in
   `HTML` — and `test_server_vendor_assets.py` compares the last two directly,
   because missing one 404s into the `md-raw` fallback with no error anywhere.
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
  and `CHECK_KINDS`. Add a value here, not at a call site.
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
  at the wire.
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

`GET /input` serves the review-input merged with a live `ledger: [...]` key; that
`ledger` is injected at serve time and is not part of the on-disk file schema.

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

**The no-bookkeeping-bash rule is scoped, deliberately.** `loop.py` drives doc
review only, so only `viva-review`'s **branch A** is held to it. Branch B (hunks
— `parse_diff.py` and `--mode diff`, neither of which the driver knows) and
`viva-write`'s pre-hand-off steps carry that bash on purpose.
`test_server_orchestration.py` enumerates those two rather than exempting by
wildcard, so a third skill growing its own loop fails; #179 is the issue that
empties the list.

## `/viva-write` — the intake end of the lifecycle

`/viva` reviews a doc that exists; `/viva-write` produces one and hands it to
that same review **in the same server process**. The flow is type → attach →
interview → draft → hand off → rounds → stamp, and it is a skill precisely
because steps 2 and 4 are model work.

`loop.py` drives everything from the hand-off on (`wait`, `rearm`, `finish`).
Only `start` is unusable here, for two file-local reasons: `cmd_start` refuses
when `.viva/server.url` exists — and by then the interview's qa server wrote it —
and `cmd_arm`'s liveness probe reads `round` off `/input`, which a qa payload has
no key for. So `/viva-write` performs `cmd_start`'s state clear itself (the same
five things; `preferences.json` survives), parses round 1 directly, and POSTs
`/next-round`. Extending the driver over that gap is #179's, not this flow's.

**Two orderings this flow depends on, both enforced rather than documented:**

- **Producers run before the hand-off, never after.** The server loads its round
  once and replaces it only from `/next-round`, so `loop.py annotate` refuses a
  round the server already holds. It *passes* before the hand-off because the
  live qa server's `probe_round` returns `None` — that is the seam, and
  `tests/test_viva_write_flow.py` asserts both sides of it.
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
- **Doc-type bundles.** A type is section grammar + check set + default pass, one
  JSON file per name: shipped defaults in `types/`, a repo's overrides in
  `.viva-types/`, the repo's copy winning **wholesale** on a name collision so it
  can drop a shipped check as well as add one. Both directories are committed
  config and deliberately outside `.viva/`, which is cleared every `start`.
  `scripts/doc_types.py` is the only place a name becomes a bundle and the read
  boundary that validates one; a bundle's `checks[]` names producers by the
  mechanical mapping `<name with - as _>.py`.
- **State lifecycle.** `preferences.json` survives the round-1 state clear (it is
  cross-session, gitignored, per-clone); everything else under `.viva/` is
  disposable and reset each session. Don't add new state that must survive
  without documenting why here. The state clear itself lives in
  `scripts/loop.py`'s `cmd_start`, not in prose — it removes the round files,
  `server.url`, `open-notes.json`, and the `attachments/` directory. The one
  documented exception: `cmd_start`'s resume branch copies a completed session's
  finishing round to `.viva/prior-review-input.json` /
  `.viva/prior-review-verdicts.json` just long enough to survive the
  clear and feed the new session's `--prior-input`/`--prior-verdicts`, then
  discards them in a `finally` — nothing new persists past that one resume.

## Tests

Stdlib-only, self-running scripts under `tests/` (each has a `main()` and prints
`OK`). CI runs every file across Python 3.8–3.13 as `python3 tests/test_*.py`;
there is no pytest dependency and no type checker. New features need a test;
match the existing subprocess + `urllib` pattern for server integration tests
(see `tests/test_server_ledger.py`) and the plain-assertion pattern for units.
