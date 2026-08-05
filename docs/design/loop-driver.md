# Design: loop.py driver owns round numbering, liveness, and the all-approved finish guard

Source: issues #104, #102 (parts 1 and 3), #103 (parts 1 and 2), #125.
Epic: reliability. Story slug: loop-driver.

## Problem & persona

Consumer: the human deciding to fund the work; product-reviewer Q1.

> Claude Code, having written a spec, ADR, runbook, or design doc. It needs to
> hand the doc to a human for sign-off without burning context: parse without
> reading the doc, wait without polling cost, rewrite only flagged sections,
> and learn what this reviewer always wants.

The agent is viva's runtime, and today it is also viva's bookkeeper. `SKILL.md`
is 382 lines of prose the agent executes, and the bookkeeping it carries is the
part that fails:

- **The round counter has no on-disk anchor.** `{N}` is templated into every
  bash block from step 2 onward (`:140`, `:141`, `:144`, `:171–181`, `:189–194`).
  The agent is its sole holder across an hour-long human wait — exactly when
  context compaction lands.
- **The wait cannot end.** `until [ -f .viva/review-r{N}.json ]; do sleep 0.3; done`
  (`:140`) has no exit condition. A crashed or Ctrl-C'd server leaves the agent
  polling a file nothing will ever write, while `server.url` — already deleted on
  shutdown — sits unread as the liveness signal it could be.
- **`SIGTERM` skips the cleanup entirely.** Only `SIGINT` is handled
  (`server.py:4533`); the `finally` that unlinks `server.url` (`:4573`) never
  runs, so `proc.terminate()` — the standard way a headless parent ends a
  subprocess — leaks the file and trips the next launch's guard.
- **Finishing is a norm, not a check.** `POST /complete` (`server.py:4463`)
  accepts any payload at any time. Nothing but the agent's own restraint stops a
  review from completing with sections the human never approved.
- **There is a state the branches don't cover.** The Skip button writes
  `submitted_early: true` with the rest `pending` — neither "every section
  approved → finish" nor "any changes/info → rewrite" fires, and a model in that
  gap improvises.

PRODUCT.md's own Known Problems names this: *"SKILL.md carries the whole
launch→wait→act→rewrite loop as prose with conditional round-1 branches, so the
agent shoulders the orchestration the code does not."* Every defect above is a
symptom of one cause — prose is being asked to be a runtime.

## Proposed design

Consumer: product-reviewer Q2/Q6; `/build`'s spine-building step.

One new script, `scripts/loop.py`, takes the bookkeeping. The agent keeps the
judgment.

**Seven subcommands, argparse per DESIGN.md's CLI conventions:**

- `start --doc <path>` — resolves state, clears it, parses round 1. Owns the
  three round-1 branches SKILL.md currently spells out as prose (default,
  standing-preferences, resume-a-signed-off-doc): it detects which applies from
  disk. With no standing preference it goes on to arm the round itself and
  prints `$BASE`. With one, it **stops after parsing** and says so — the
  producer that must run next is an LLM pass, and that is the agent's judgment
  work, not the driver's.
- `annotate --sidecar <path>` — merges a producer's sidecar into the current
  round's `review-input` through `annotate.py`. The agent writes the sidecar;
  the driver supplies the round number, so no producer call re-templates `{N}`.
- `arm` — makes the current round file live: launches the server on round 1,
  POSTs `/next-round` on round 2+, and prints `$BASE`. Deriving which from the
  round number is exactly the bookkeeping this story moves into code.
- `wait` — blocks until the current round's verdicts land, then prints them, the
  id→title map, the standing preferences, and a **classification line**:
  `all-approved`, `has-work`, or `submitted-early`. **Exits non-zero when
  `.viva/server.url` disappears**, so a dead server ends the wait instead of
  outliving it.
- `rearm --response <cid>=<text> [--parse-only]` — updates the open-note store
  and re-parses, then arms unless `--parse-only`. Repeatable per comment.
  `--parse-only` is how the agent opens the round 2+ producer seam, naming the
  same stop-after-parse behavior `start` takes on its own; the sequence is then
  `rearm --parse-only` → `annotate` → `arm`, and that order is load-bearing.
- `finish` — settles threads, POSTs `/complete`, appends the revision history.
  **Refuses when the round is not complete.**
- `abandon` — ends an unfinished session: `POST /abandon` to the running server,
  and a report that the doc was not signed off. The one exit that is not a
  sign-off.

**The producer seam survives, because the driver owns the branch and the agent
owns the pass.** `SKILL.md:74–90` splits round 1 into parse → producer → launch,
and `:271–276` pins round 2+ producers between the re-parse and the POST. An
atomic `start`/`rearm` would close both, silently dropping the learned-preference
producer — which **auto-engages** whenever the store holds a standing preference
(`:301`, `:370`) — and disabling CLAUDE.md's documented preferred extension
point. The split above keeps the seam open without handing the round number back:
`start` decides *whether* the seam is needed by reading the store, `annotate` and
`arm` operate on a round they derive from disk, and the agent never types `{N}`.

**The relocation moves reference material; the rewrite must not drop the
directive that uses it.** Fork 2 is settled, so `:246–383` moves — and the
exposure is not inside that range. `SKILL.md:167` is the operative instruction
("**Apply learned preferences while you rewrite**"), it lives in step 4, and it
is what this story *rewrites wholesale*. Losing it there silently drops the
feature PRODUCT.md's persona section names ("learn what this reviewer always
wants"), because `wait` would print the standing set with nothing telling the
agent to apply it. The paragraph at `:364` inside the move range is a different
thing — a lifecycle-table entry whose `preferences.py list` bash block `wait`
now subsumes, so it moves with the rest and its command goes away. The criterion
is therefore written against behavior, not a line number: the slim SKILL.md's
rewrite step instructs applying standing preferences, sourced from `wait`'s
output.

**`references/` lives beside SKILL.md, and `loop.py` prints the path.**
The files sit at `.claude/skills/viva/references/`, the skill-relative
convention, not beside `scripts/`. Resolution needs an emitter because
`loop.py` never reads them — the *agent* does, and it has no `$VIVA_DIR` for a
`references/` path (register item 5 forbids that route). So `loop.py` derives
the plugin root from its own location and **prints the absolute path in the
output line that needs it**: `start`'s stop-after-parse line points at the
producer contract, and any subcommand whose next step is documented in a
reference names that file's full path. The agent is told where to read, never
asked to compute it. This needs nothing from `viva-dir-resolve`'s glob
hardening, which fixes how SKILL.md's bash finds the plugin — a mechanism the
slim skill still uses exactly once, to locate `loop.py` itself.

**The round number is derived, never held.** `loop.py` reads the highest
`review-input-r{N}.json` in `.viva/` and uses it. No counter is passed, stored,
or remembered — so compaction cannot lose it, and no new state file is added
that would need to survive the round-1 clear.

**Liveness is the file that already exists.** `server.url` is written at startup
and unlinked in the shutdown `finally`. `wait` checks it every tick; its absence
means the server is gone, and the command exits non-zero with a message naming
the relaunch. Nothing new is persisted.

**The finish guard is enforced twice, in code both times.** `loop.py finish`
checks before it POSTs, and `server.py`'s `/complete` handler checks before it
accepts — because a guard only in the caller is still a norm.

**The predicate is one named function in `schema.py`, not an inlined condition.**
Both call sites ask `schema.round_is_complete(input_data, verdicts)`, which today
returns true only when every section's verdict is `approved`. `schema.py` is the
home because `loop.py` and `server.py` are separate processes and it is the only
module CLAUDE.md lets either cross-import — it already holds `section_key()` and
`verdict_to_ledger_entry()`, the same category of single-rule contract. Anywhere
else and the predicate lands twice, which is the thing naming it prevents.
Naming it is the whole point:
milestone 10's pass types (#168) make review depth a parameter of a round, and a
*checks only* pass completes on a condition a *final* pass would reject. When
that lands, the completion rule becomes a function of the pass and changes in
one place — not a condition re-derived at two call sites and a test suite
written against a constant. Today's behavior is unchanged; only its seam is.

**Two sessions are exempt, and shape alone does not identify them.** The
discrimination `server.py` already applies at `:4386` and `:4544` is
shape-based, and it correctly excepts Q&A, which carries `questions` rather than
`sections`. It does **not** except diff mode: `parse_diff.py` emits `sections`,
so a diff session is review-shaped. `viva-diff/SKILL.md:88–97` POSTs `/complete`
when a re-diff comes back empty, and `:110–113` states that path is reached
*because at least one hunk was reverted or dropped at the reviewer's request,
not because every hunk was approved* — the latest verdicts hold `changes` by
design. A shape-only guard would 4xx that legitimate finish, leak the server,
and strand the tab on the processing card. The guard therefore fires only when
`_input_data` carries `sections` **and** its `mode` is not `"diff"`.

Diff mode is left ungated deliberately, not overlooked: closing it needs
`viva-diff/SKILL.md` to send an explicit resolved-empty signal, and that file
belongs to another story. The carve-out is recorded as a follow-up below.

**The guard reads the verdicts the server itself received.** `/complete` checks
a `_last_verdicts` snapshot taken under `_data_lock` at `/submit` time, not a
re-read of `_output_path` — the file on disk can be replaced by a caller between
the two calls, and the guard must judge what the human actually submitted. The
snapshot's absence is its own case: `/complete` before any submit means no round
was ever reviewed, and returns a distinct 4xx ("no verdicts submitted") from the
not-all-approved one ("N sections not approved"), because those are two different
agent recoveries.

**`SIGTERM` joins `SIGINT`** on the existing one-line handler, so the `finally`
that already deletes `server.url` runs on the default subprocess-teardown path.

**`loop.py` is a driver, and that is a new architectural part.** CLAUDE.md
currently describes `scripts/*.py` as stateless CLI filters importing no sibling
but `schema.py`. `loop.py` orchestrates those filters, so it **invokes them as
subprocesses** and imports only `schema.py` — the rule holds as written. What
changes is CLAUDE.md's part 1: the launch→wait→act→rewrite loop stops being
prose the agent executes and becomes a script it calls. That edit is part of
this story.

## User journey

Consumer: product-reviewer Q3; `/build`'s task-boundary decisions.

The agent's whole loop, after:

1. `python3 "$VIVA_DIR/scripts/loop.py" start --doc plan.md` — one call. The
   round-1 branch selection, state clear, parse, launch, and browser open all
   happen inside it; `$BASE` and the round number are printed, not tracked. If
   it stops after parsing (a standing preference exists), the agent runs the
   preference producer, then `loop.py annotate --sidecar …` and `loop.py arm`.
2. `loop.py wait` — blocks on human review time. Returns verdicts, the id→title
   map, standing preferences, and the round's classification. If the reviewer
   Ctrl-Cs the server, this exits non-zero and says so instead of hanging.
3. **The classification line routes the agent, and there are three destinations,
   not two:**
   - `all-approved` → step 6.
   - `has-work` → step 4.
   - `submitted-early` → step 5.
4. The agent **does the judgment work**: applies each `changes` comment as a
   targeted edit, answers each `info` comment in its thread, applies standing
   preferences to the sections it touches. Then `loop.py rearm --response
   s2-c1="Shortened the intro"` — bookkeeping only. Loop to 2.
5. **The reviewer paused** (they hit *skip rest & submit*). The agent first
   `rearm`s the round unchanged, which returns the tab from the processing card
   to its cards, and *then* reports the pending count and asks whether to
   re-present, keep waiting, or abandon. Re-arming first is the load-bearing
   order: `DESIGN.md:267–288` makes *skip rest & submit* a first-class control
   that bypasses the recap, and without the re-arm the reviewer sits on a
   pulsing "the agent is revising" card while the agent asks a question in a
   terminal they are not watching.

   **All three answers have a mechanism; none is left to improvisation.**
   *Re-present* and *keep waiting* are both already satisfied by the re-arm — the
   agent loops back to `wait`. *Abandon* is `loop.py abandon`, and it reaches the
   server **over HTTP, not by signal**: `start` launches the server detached, so
   `abandon` is a different process holding no child handle, and `server.url`
   carries the URL and nothing else — there is no pid file, no `os.getpid`, and
   no shutdown route in the repo today. What `abandon` does have is `$BASE`,
   the same handle `wait` and `rearm` already use. So the server grows one
   endpoint, `POST /abandon`, which sets `_shutdown` exactly as `/complete`'s
   timer does but carries none of its sign-off meaning; the `finally` then runs
   and deletes `server.url`.

   The `SIGTERM` handler is not what makes this work and is not redundant
   either: it covers the *other* teardown, a headless parent calling
   `proc.terminate()` on a server it owns (#125). Two exits, two mechanisms,
   one shutdown path. Leaving `abandon` as a word with no mechanism would
   reintroduce the improvisation gap this story exists to close, one branch
   further down — and naming a signal no process can send is the same gap
   wearing a verb.
6. All approved → `loop.py finish`. Called on any other state it refuses, exits
   non-zero, and prints the pending count — a backstop, not the routing
   mechanism, which is step 3.

The reviewer's experience is unchanged in review mode. The one path this story
touches is the pause above, and it touches it to remove a stranded tab that
exists today.

## Out of scope

Consumer: product-reviewer Q4.

- **The auto-approve escape hatch's replacement prose and its test.**
  `SKILL.md:134`'s "a doc too short to review → treat as auto-approved" is
  deleted here as a consequence of the rewrite, but the PRODUCT.md-aligned
  replacement ("ask the user; a skipped doc is recorded as unreviewed") and the
  test asserting the hatch stays gone belong to `skill-prose-fixes`, which
  depends on this story. Epic pre-mortem item 1 is the risk this split carries.
- **`viva-diff/SKILL.md`'s drift** (#103 part 3) and the `@@`-header hunk-shift
  warning (#103 part 4) — `skill-prose-fixes`.
- **The three sibling `server.py` stories** — `qa-free-text`,
  `origin-and-output-guard`, `handoff-mode` — and `anchor-occurrence`,
  `viva-dir-resolve`.
- **Any change to the reviewer-facing UI.** No CSS and no card behavior — no UI
  code at all. The one thing the reviewer sees differently is the paused path's
  tab returning to its cards, and that is existing `/next-round` behavior being
  invoked at a moment it currently isn't, not new interface work.
- **`viva-qa`'s and `viva-diff`'s own loops.** They keep their prose for now;
  extending the driver to them is a later story, not a hidden half of this one.
- **Gating diff mode's finish.** The `/complete` guard exempts `mode: "diff"`
  (see Proposed design). Closing that carve-out requires `viva-diff/SKILL.md` to
  send an explicit resolved-empty signal on its empty-re-diff path, and that file
  belongs to `skill-prose-fixes`. Filed as a follow-up rather than absorbed here;
  diff mode is no less gated than it is today.

## Alternatives considered

Consumer: product-reviewer Q5; future readers reconsidering a rejected path.

**Fork 1 — what `finish` does when verdicts are not all-approved.**

- a. Refuse outright; no override flag exists.
- b. Refuse, but honor `--force` with a printed warning.
- c. Warn on stderr and proceed.

**(recommended): a.** PRODUCT.md calls "nothing is auto-accepted" a hard line
and lists "not autonomous review" under what we are NOT building. An override
flag is the seam a future session talks itself through — and a flag that exists
to be used in the awkward case is a flag that gets used in the awkward case.
PRODUCT.md:61–62's adjacent "never block sign-off" bullet does not pull the
other way: it scopes *producers*, which flag advisorily, while this guard
enforces the human's own recorded verdicts rather than substituting a machine
judgment for one.
*Settled by the human at the epic interview, 2026-08-04.*

**Fork 2 — where SKILL.md's opt-in feature documentation lives.**

- a. Move to `references/`, loaded only when the feature is engaged.
- b. Keep it inline.
- c. Move to `references/` and drop the ones with no current caller.

**(recommended): a.** Roughly 140 lines (`:246–383`: annotations, producers,
confidence, open notes, preferences) load on every plain review today. PRODUCT.md
principle 5 makes agent cheapness a product feature and principle 4 says a plain
review never pays for a feature it does not use. Option c couples a docs move to
a deletion decision that deserves its own evidence.
*Settled by the human at the epic interview, 2026-08-04.*

**Rejected without a fork: teaching the agent to track `{N}` more carefully.**
Every prior attempt to fix this class of defect has been more prose. #104's
framing is that the defects *are* the prose, and a fifteenth bash block
explaining the counter more clearly is the same bet that already lost.

**Rejected: a state file holding the round number.** It would be new state under
`.viva/` that must survive the round-1 clear, and CLAUDE.md requires that to be
documented and justified. Deriving from the round files already on disk needs no
new state at all.

## Success metrics

Consumer: product-reviewer Q7; the post-ship outcome read.

- `.claude/skills/viva/SKILL.md` templates `{N}` into **zero** bash blocks
  (today: every block from step 2 on) — including the producer calls, which
  route through `loop.py annotate`. Grep-checkable.
- **Structural, not a line count:** SKILL.md contains zero bash blocks that
  launch a server, POST an endpoint, or name a round file; the loop reads as a
  sequence of `loop.py` calls. A line count alone is gameable by relocation —
  moving `:246–383` to `references/` takes 382 → ~242 with every line of
  bookkeeping prose still in place — so the count is a secondary read, not the
  metric.
- A review session whose server is killed mid-wait terminates with a non-zero
  exit and a message, rather than polling until the tool's timeout.
- `POST /complete` against a review session with any non-approved section
  returns 4xx, while a Q&A session and a diff session's empty-re-diff finish
  both still succeed. Directly testable, and the invariant PRODUCT.md sells.
- A `submitted-early` round routes to the paused-reviewer branch without the
  agent having to trip over `finish`'s refusal to discover it.
- Zero `.viva/server.url` files left behind after `SIGTERM`.

## Operational readiness

Consumer: `/review`'s operability lane; `/build`'s rollout-tier verification.

- **Failure signal.** Every `loop.py` subcommand exits non-zero with a
  one-line stderr message naming the condition and its recovery. `wait`'s
  dead-server exit names the relaunch command; `finish`'s refusal prints the
  pending-section count.
- **No new runtime dependency.** stdlib only, per PRODUCT.md's "local and
  keyless" and "not a heavyweight dependency". `loop.py` shells out to its
  siblings rather than importing them, preserving CLAUDE.md's one-cross-import
  rule — `schema.py` stays the single exception, and now also houses
  `round_is_complete()`.
- **`start` returns; the server outlives it.** The server is launched detached
  and `start` returns as soon as `.viva/server.url` appears, exactly as today's
  `SKILL.md:60` backgrounds it with `&`. Holding the child's stdout would block
  the agent's tool call for the whole review — the round-trip PRODUCT.md's
  persona section says the skill is tuned to avoid. Teardown is `/complete`,
  `loop.py abandon`, or a signal; an abandoned loop leaks a server exactly as it
  does today, and no worse.
- **Python floor.** 3.8, matching CI's matrix — no walrus-in-comprehension, no
  `match`, no `|` union syntax at runtime.
- **Backward compatibility.** The `.viva/` file layout, the round-file schema,
  and every server endpoint's request shape are unchanged. A caller still
  driving the loop by hand with the old bash blocks keeps working; only
  `/complete` becomes stricter, and only for review-shaped sessions.
- **Tests.** Unit coverage for round derivation, liveness exit, the finish
  refusal, and `wait`'s three-way classification; a server integration test for
  `/complete`'s guard following `tests/test_server_ledger.py`'s subprocess +
  `urllib` pattern, covering all four cases — review-not-all-approved refused,
  review-all-approved accepted, **Q&A accepted** (epic pre-mortem item 2), and
  **diff-mode accepted with `changes` verdicts on record** (the empty-re-diff
  finish `viva-diff/SKILL.md:110–113` documents). A producer round-trip test
  proves the seam: seed a standing preference, confirm `start` stops after
  parsing and that `annotate` + `arm` land the flag in `review-input-r1.json`.

## Open questions

Consumer: the human sponsor; the next `/shape` revision round.

- **Does `rearm` pass `output` in the body, and will that survive its siblings?**
  Epic pre-mortem item 4: `handoff-mode` adds a mode restriction to
  `/next-round` and `origin-and-output-guard` constrains its `output` field.
  This story should use the canonical body form and keep `output` inside
  `.viva/`, but whether those guards land compatibly is verified at the epic
  finale, not here.
- **Where does the `.viva/server.url` pre-flight guard live after the rewrite?**
  `SKILL.md:26` refuses to launch when the file exists, and `:47–48` states the
  dependency outright — the clear-state block's deletion "is safe *because* the
  Invocation guard has already confirmed no prior server is running." `start`
  clears state unconditionally, so the guard has to survive somewhere or `start`
  deletes a live session's `server.url` and orphans a running server with the
  reviewer's tab still open. Absorbing it into `start` is tidier; leaving it
  with the agent keeps `start` from deciding whether someone else's session may
  be killed. Either resolution is fine — losing it is not, which is why a
  criterion asserts the guard exists rather than where.
- **How much of this loop does milestone 10 re-parameterize?** The Editorial
  Workspace direction keeps the keyless constitution (Claude Code stays the
  agent runtime), so the driver's job is unchanged — but #168 turns review depth
  into a round parameter, #167 adds a comment disposition the verdict derivation
  must route, and #170 adds a fourth round-1 branch. This design anticipates the
  first with `round_is_complete()`; the other two land as arguments `loop.py`
  passes through, which is the reason to have a driver before they arrive rather
  than after. `wait`'s classification line is the likely extension point for
  both — worth confirming when #168 is designed, not now.

---

## Revision History

Signed off via viva review — 1 round, 9 sections, 0 revised. 2026-08-04

Signed off via viva review — 1 round, 9 sections, 0 revised. 2026-08-04

Signed off via viva review — 1 round, 9 sections, 0 revised. 2026-08-04
