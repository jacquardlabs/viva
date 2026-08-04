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

**Four subcommands, argparse per DESIGN.md's CLI conventions:**

- `start --doc <path>` — resolves state, clears it, parses round 1, launches the
  server, prints `$BASE`. Owns the three round-1 branches SKILL.md currently
  spells out as prose (default, standing-preferences, resume-a-signed-off-doc):
  it detects which applies from disk and does the right thing.
- `wait` — blocks until the current round's verdicts land, then prints them, the
  id→title map, and the standing preferences. **Exits non-zero when
  `.viva/server.url` disappears**, so a dead server ends the wait instead of
  outliving it.
- `rearm --response <cid>=<text>` — updates the open-note store, re-parses, and
  POSTs `/next-round`. Repeatable per comment.
- `finish` — settles threads, POSTs `/complete`, appends the revision history.
  **Refuses when the latest verdicts are not all-approved.**

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
accepts — because a guard only in the caller is still a norm. The server-side
check keys on **payload shape, not mode**, reusing the discrimination
`server.py` already applies at `:4386` and `:4544`: a session whose
`_input_data` carries `sections` must be all-approved; a Q&A session carries
`questions` and is unaffected.

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
   happen inside it; `$BASE` and the round number are printed, not tracked.
2. `loop.py wait` — blocks on human review time. Returns verdicts, the id→title
   map, and standing preferences on stdout. If the reviewer Ctrl-Cs the server,
   this exits non-zero and says so instead of hanging.
3. The agent reads the verdicts and **does the judgment work**: applies each
   `changes` comment as a targeted edit, answers each `info` comment in its
   thread, applies standing preferences to the sections it touches.
4. `loop.py rearm --response s2-c1="Shortened the intro"` — bookkeeping only.
   Loop to 2.
5. All approved → `loop.py finish`. Not all approved → `finish` refuses, exits
   non-zero, and prints the pending count. The agent reports that to the user
   and asks whether to re-present, wait, or abandon — the third branch that
   doesn't exist today.

The reviewer's experience is unchanged. This story moves no pixels.

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
- **Any change to the reviewer-facing UI.** No CSS, no card behavior, no
  browser-visible change of any kind.
- **`viva-qa`'s and `viva-diff`'s own loops.** They keep their prose for now;
  extending the driver to them is a later story, not a hidden half of this one.

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
  (today: every block from step 2 on). Grep-checkable.
- SKILL.md's line count falls from 382 toward ~80 of judgment work, with the
  opt-in feature docs relocated rather than deleted — measured as bytes loaded
  on a plain review, which PRODUCT.md principle 5 makes a product metric.
- A review session whose server is killed mid-wait terminates with a non-zero
  exit and a message, rather than polling until the tool's timeout.
- `POST /complete` against a session with any non-approved section returns 4xx.
  Directly testable, and the invariant PRODUCT.md sells.
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
  rule.
- **Python floor.** 3.8, matching CI's matrix — no walrus-in-comprehension, no
  `match`, no `|` union syntax at runtime.
- **Backward compatibility.** The `.viva/` file layout, the round-file schema,
  and every server endpoint's request shape are unchanged. A caller still
  driving the loop by hand with the old bash blocks keeps working; only
  `/complete` becomes stricter, and only for review-shaped sessions.
- **Tests.** Unit coverage for round derivation, liveness exit, and the finish
  refusal; a server integration test for `/complete`'s guard following
  `tests/test_server_ledger.py`'s subprocess + `urllib` pattern, including the
  **Q&A-still-completes** case that epic pre-mortem item 2 predicts breaking.

## Open questions

Consumer: the human sponsor; the next `/shape` revision round.

- **Does `rearm` pass `output` in the body, and will that survive its siblings?**
  Epic pre-mortem item 4: `handoff-mode` adds a mode restriction to
  `/next-round` and `origin-and-output-guard` constrains its `output` field.
  This story should use the canonical body form and keep `output` inside
  `.viva/`, but whether those guards land compatibly is verified at the epic
  finale, not here.
- **Where do `references/` files live so both the plugin cache and the repo
  resolve them?** SKILL.md's relocated sections need a path that works from the
  installed plugin. `viva-dir-resolve` is hardening exactly that mechanism, and
  the two stories should agree before either lands.
- **Should `start` absorb the `.viva/server.url` pre-flight guard** currently in
  SKILL.md's Invocation section, or does that stay the agent's check? Absorbing
  it is tidier; leaving it out keeps `start` from deciding whether someone
  else's session may be killed.

---

## Revision History

Signed off via viva review — 1 round, 9 sections, 0 revised. 2026-08-04
