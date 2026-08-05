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
   `drift`, `checklist`, `open_notes`, `preferences`, `revision_history`). Each
   is stdlib-only, run as `python3 scripts/<name>.py`, and reads/writes JSON.
   They import no sibling **except** the shared contract, `schema.py` (below) —
   keep that the only cross-import so each stays independently testable.
4. **`server.py` — the SPA host** (~2,700 lines: a small Python HTTP handler
   wrapping one large embedded HTML/CSS/JS constant, `HTML`). The bulk being a
   frontend is intentional — one file, no build step, no npm. Don't "fix" the
   line count by splitting the constant out.
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
- **TypedDicts** (`ReviewInput`, `ReviewSection`, `Annotation`, `SectionVerdict`,
  `ReviewOutput`) documenting the round shapes. Documentation only — CI runs no
  type checker; the validators carry the enforced rules.
- **`round_is_complete()`** — the single completion rule: may this round be
  signed off? Both `loop.py finish` and `server.py`'s `POST /complete` handler
  ask it, from separate processes, so the invariant lives in one place. Pure —
  dicts in, bool out, no disk. Anything that reads `.viva/` or decides policy
  beyond the predicate does not belong here.
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
rather than leaving the omission to be re-derived (`split_on` is that case).

**Validate at the boundary** — on parse write and server read — never
at the point of use. A field that a reader forgets silently drops a feature; the
boundary validator is what turns that into a loud failure.

`GET /input` serves the review-input merged with a live `ledger: [...]` key; that
`ledger` is injected at serve time and is not part of the on-disk file schema.

**New skills in this branch:** `.claude/skills/viva-qa/SKILL.md`
(`/viva-qa` primitive) and `.claude/skills/viva-diff/SKILL.md` (`/viva-diff`
skill) follow the same import-only-schema rule.

## Extension seams

- **New pre-review check → a producer, through `annotate.py`.** Producers emit a
  sidecar list of `{id, kind, severity, message, anchor?}` flags that
  `annotate.py` merges into the round's review-input (additive, idempotent).
  This is the preferred extension point — add a producer, not a server endpoint
  or a new schema field. (Confidence annotations also carry `basis`/`level`,
  preserved through the merge.)
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
