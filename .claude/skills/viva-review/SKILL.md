---
name: viva-review
description: Human sign-off on an artifact, section by section or hunk by hunk. Point it at a markdown file, a PR number, or a git ref; the human approves or requests changes per card and the agent revises until all of it passes.
---

# viva-review

Human sign-off on an artifact an agent produced. Named after the PhD oral exam —
you present, they question, you defend and revise, and it passes only when all of
it holds up.

`/viva-write` produces a doc; `/viva-review` judges one. The split is by
**intent**, not by mechanism: what you point this at decides whether the unit of
trust is a section or a hunk.

## Invocation

```
/viva-review [target]
```

| Target | Reviewed as |
|--------|-------------|
| `docs/spec.md` | **sections** — one card per markdown heading |
| `187`, `#187`, a github.com pull URL | **hunks** — `gh pr diff` supplies the patch, merge base included |
| `HEAD~3..HEAD`, `main`, a branch, a sha | **hunks** — `git diff <ref>` |
| *(omitted)* | **hunks** — unstaged working-tree changes |

Resolve the plugin once — `$VIVA_DIR` is reused by every later command:

```bash
# Resolve the skill dir from the installed plugin cache — no personal-skill
# fallback (a leftover ~/.claude/skills/viva would shadow a fresh install).
VIVA_DIR=$(find ~/.claude/plugins/cache -maxdepth 6 -path "*/viva/*" -name server.py -print0 2>/dev/null \
           | xargs -0 -r ls -t 2>/dev/null | head -1)
VIVA_DIR=${VIVA_DIR%/server.py}
[ -f "$VIVA_DIR/scripts/loop.py" ] || { echo "viva: loop.py not found — install the viva plugin (/plugin install viva@jacquardlabs-marketplace)"; exit 1; }
```

**Dispatch on the target, never on your own reading of it:**

```bash
python3 "$VIVA_DIR/scripts/review_target.py" [target]
```

It prints `{kind, label, …}` — `doc` sends you to **A**, everything else to
**B**, where `capture` is the argv that writes the patch. Precedence is
filesystem first, then shape: a repo holding a *file* named `187` means that
file, not the PR. Pass `--kind pr|ref|doc` to override — a branch named `42`
needs it.

**A markdown target with no path given**: scan the current directory for a
single `.md` file. Two candidate readings (a lone `.md` and a dirty working
tree) is not a guess to make — say what you found and ask.

---

## Verdicts — both branches

Each card takes one or more typed comments (GitHub-style threads). The verdict
is **derived** from the card's active comments (unsettled, and carrying a note —
or, for a suggestion, replacement wording): no active comments → `approved` if
the reviewer approved, otherwise `pending`; any active comment with
`type: "changes"` or `type: "suggestion"` → section `changes`; otherwise (only
active `info` comments) → section `info`. A card carrying a live suggestion is
never approved.

| Verdict | Action |
|---------|--------|
| `approved` | Carried forward; collapsed next round, reopenable |
| `changes`/`info` | Act on each comment **by its `type`** — the hybrid rule below |
| `pending` | Carry forward unchanged; re-present next round |

**The hybrid rule.** A **`changes`** comment is a *directive* — apply its `note`
as a targeted edit **now**, so the reviewer reviews the diff. A **`suggestion`**
is a directive with the wording attached — replace the anchored span with its
`replacement` **verbatim**: character for character, no rewrite pass, no
interpretation, no improving on the reviewer's phrasing, nothing outside the
anchor. Its `note` is the reviewer's reason, never a second instruction, and no
standing preference overrides supplied wording. An un-anchored suggestion names
no span: treat it as a `changes` directive scoped to the card. An **`info`**
comment is a *question* — answer it and **do not edit**. A card is edited for an
`info` question only once the discussion escalates to a `changes` turn.

**Where you answer an `info` differs by branch, because threads do.** Branch A
carries open notes across rounds, so the answer goes into the thread (A4's
`--response`). Branch B has no threads at all — see B3. Answering "in the
thread" on a hunk writes into something that does not exist.

**Anchors.** `anchor.offset` locates the edit within the card's source and
`anchor.text` confirms it. The offset already names the occurrence the reviewer
picked, so a repeated phrase needs no guessing. `offset: -1` means that ordinal
did not resolve — `anchor.text` may still appear, just not there — so scope the
edit by the card and the note, never by the first match of a phrase that
repeats. An un-anchored comment scopes to the whole card.

Before applying or answering a comment, if `comment.attachments` is present,
`Read` each listed path — the image is context for the edit or answer. For a
carried-forward thread, act on its **latest** reviewer turn's type; a carried
suggestion turn keeps its `replacement` on the exchange, beside `note`.

**Declining.** Comply by default. When a comment is wrong on the record — it
contradicts a decision made this session, a source, or a measurement — refuse it
with grounds instead of complying. Taste is not grounds, and a decline without
them reads as a refusal to work. It settles nothing: the thread carries to the
next round marked `declined`, so the card stays held. The reviewer then settles
it (they accept) or replies (they insist) — **insisting wins**: apply the change
that round. There is no second decline on a thread.

---

## A. Doc review (`kind: doc`)

`scripts/loop.py` drives this branch. It derives the round number from disk, owns
every round file and every call to the server, and refuses to start when a
previous session may still be running. You bring the judgment.

The loop is **start → wait → route → (rewrite & re-arm | finish)**. It is tuned
so you never make the human wait on a tool round-trip and never load the doc into
context until a rewrite needs it — an all-approved round finishes without ever
reading the doc.

**A1. Start** (round 1)

Do not read the `.md` first. The parser reads it from disk; you need it only when
a comment requires a rewrite.

```bash
python3 "$VIVA_DIR/scripts/loop.py" start --doc <relative/path/to/doc.md>
```

`start` clears stale state, parses round 1, launches the server, opens the
browser tab, and prints the round and `$BASE`. It reads disk to pick the round-1
branch itself: a plain launch; a **resumed sign-off**, where a doc already
carrying a `## Revision History` gets the prior session's approvals carried
forward so the human re-reviews only what changed; or a stop after parsing when
the preferences store holds a standing preference. It **refuses** when
`.viva/server.url` exists — a prior session may still be running with the
reviewer's tab open. Report that; delete the file only if you are certain no
server is running (e.g. after a crash).

Pass `--split-on '<REGEX>'` for a task-card plan document: a heading is a split
point iff its title matches (`re.search`, at any depth), replacing the
split-level heuristic entirely. Zero matches is a hard error, not a fallback. The
pattern is recorded in the round file, so every later round re-splits the same
way — and `start` reads it back off the prior round when resuming a signed-off
doc, so a resume does not need it retyped.

Without it the parser splits on the highest heading level occurring more than
once (usually `##`, one level coarser past 20 sections), verbatim; content before
the first split heading becomes its own first section; a `Revision History`
section is omitted and exempt from the integrity check.
`scripts/parse_sections.py`'s module docstring is the full contract. Never parse
by hand or read the parser's output back into context.

Pass `--type <name>` when the doc has one (`doc_types.py --list` is the menu) —
it names the round's check set and, with `--pass <kind>`, the depth it runs at. A
doc `/viva-write` produced already carries both.

**If `start` stops after parsing**, it prints the path of the producer contract
to read: run the producer, then `loop.py annotate --sidecar <path>` and
`loop.py arm`. That is the one round-1 doc read, paid deliberately.

**A2. Wait for verdicts** (every round)

```bash
python3 "$VIVA_DIR/scripts/loop.py" wait
```

Blocks on human review time, then prints the verdicts, the round's id→title map,
the standing preferences, and a classification line. Read all four straight from
stdout — the next steps reuse them instead of re-fetching. Issue it with a
generous timeout (~10 min / 600000ms); a tool's default 2 minutes would
spuriously fail mid-review, and re-issuing the identical command after a timeout
is safe. It exits non-zero when the server disappears, so a killed session ends
the wait instead of outliving it.

**A3. Route the round.** The classification line routes you. Four destinations,
no fifth:

| `wait` prints | Where to go |
|---------------|-------------|
| `all-approved` | A5 — finish |
| `has-work`, some section carrying active comments | A4 — rewrite, then re-arm |
| `has-work`, **no** active comment anywhere | The round is held by its **pass**, not by a rewrite — every section is approved and the conjunct is not. Reopen the producer seam: `loop.py rearm --parse-only`, re-run the type's check producer emitting a `result` on each flag, `loop.py annotate --sidecar -`, `loop.py arm`. Back to A2. `loop.py finish` names the unsatisfied conjunct if you need it spelled out. |
| `submitted-early` | The reviewer hit *skip rest & submit* and paused. Run `loop.py rearm` with no `--response` **first** — that returns their tab from the processing card to its cards — **then** report the pending count and ask whether to re-present, keep waiting, or stop. Re-present and keep waiting are both already satisfied by the re-arm: back to A2. Stop is `loop.py abandon`, which ends the session and reports that the doc was **not** signed off. |

**A4. Rewrite and re-arm** (only when the round has work)

Now — and only now — load what the rewrite needs. The id→title map and the
standing preferences were already printed by `wait`; reuse them, don't re-fetch.
Read the target `.md` (and optionally `PRODUCT.md`, `DESIGN.md`, `CLAUDE.md` for
context). Loop over `comments[]` for each section and act by type, per the
verdict rules above, rewriting directly in the source file. **Preserve each
heading's text exactly** — next-round title matching depends on it.

**Apply standing preferences while you rewrite.** The doc is already open, so
consulting the standing set `wait` printed costs nothing: apply each relevant
preference to the sections you touch, so a recurring fix is already in when the
card re-presents instead of waiting for the human to flag it again. An empty set
is a no-op.

Then re-arm — one `--response "<cid>=<what you changed>"` per comment you rewrote
or answered. The `cid` is the server's own `{sectionId}-c{n}` (e.g. `s2-c1`); use
it verbatim, never synthesize it. Approving a section settles all of its threads,
so those need no response. A decline replaces that comment's `--response`.

```bash
python3 "$VIVA_DIR/scripts/loop.py" rearm \
  --response "s2-c1=Shortened the intro to two sentences" \
  --decline  "s4-c1=Contradicts the measurement in bench.md:12"
```

`open_notes.py` owns the one-decline-per-thread rule and refuses a second; the
round does not ship until you comply.

`rearm` records the exchanges, re-parses the doc, and ships the next round to the
running tab — the browser updates in place, no new tab. A section carries forward
as approved only when its title matches exactly (case-insensitive) AND its
content is byte-for-byte identical; changed content comes back for re-review.
`--pass <kind>` changes the round's depth (a structural round 1, a line round 2,
a `checks` or `final` round later). Add `--parse-only` when a producer must flag
the new round before the reviewer sees it. Back to A2.

**A5. Finish** (all sections approved)

```bash
python3 "$VIVA_DIR/scripts/loop.py" finish --doc <doc_file>
```

`finish` settles the round's remaining threads, ends the session, and appends
`## Revision History` to the doc — a summary line, a verbatim table of every
`changes`/`info` note and every suggestion's wording, and an **Open notes**
subsection carrying each thread's full exchange when any were tracked. On a
re-reviewed doc the new block is appended under the existing heading. It
**refuses** on any non-approved section and prints the pending count: nothing is
auto-accepted.

It then names the preferences reference — cluster this session's `changes`/`info`
notes into distinct critiques and record each, so a recurring one is learned. A
session with no recurring critique records nothing.

Give the sign-off report — how many sections, how many rounds, what was revised —
and ask:

> "Sign-off complete. Commit the doc to git? (y/n)"

```bash
git add <doc_file>
git commit -m "docs: sign off on <filename>"
```

---

## B. Diff review (`kind: pr | ref | worktree`)

The unit of trust is the hunk: nothing in the diff is done until a human has
approved the hunk it lives in. `loop.py` does **not** drive this branch — it
parses with `parse_sections.py` and launches `--mode review`, and a diff needs
`parse_diff.py` and `--mode diff`. Extending the driver here is #179's.

**Reviewing a PR you are not checked out on is read-only.** The rewrite step
edits working-tree files, so if you intend to revise rather than only sign off,
`gh pr checkout <n>` first and say so before starting.

**B1. Capture and launch** (round 1)

```bash
[ -f .viva/server.url ] && { echo "viva-review: a prior session may still be running (.viva/server.url exists)"; exit 1; }

mkdir -p .viva
rm -f .viva/server.url .viva/review-input-r*.json .viva/review-r*.json .viva/open-notes.json
rm -rf .viva/attachments

# `capture` is the argv review_target.py printed — run it verbatim.
gh pr diff 187 > .viva/diff.patch          # or: git diff <ref> > .viva/diff.patch
[ -s .viva/diff.patch ] || { echo "viva-review: no changes to review"; exit 0; }

DOC_FILE="<the label review_target.py printed>"

python3 "$VIVA_DIR/scripts/parse_diff.py" .viva/diff.patch \
  --output .viva/review-input-r1.json --round 1 --doc-file "$DOC_FILE" \
&& {
  python3 "$VIVA_DIR/server.py" --mode diff \
    --input .viva/review-input-r1.json --output .viva/review-r1.json &
  for i in $(seq 1 100); do [ -f .viva/server.url ] && break; sleep 0.1; done
}
[ -f .viva/server.url ] || { echo "viva-review: launch failed"; exit 1; }
BASE=$(cat .viva/server.url)
```

**B2. Wait for verdicts** (every round)

```bash
until [ -f .viva/review-r{N}.json ]; do sleep 0.3; done
cat .viva/review-r{N}.json
```

Read all verdicts from stdout. The server writes the file atomically — `cat`
always sees complete JSON. Same ~10 minute timeout as A2.

**B3. Act on verdicts.** The verdict rules above apply with the hunk as the card,
and **one exception: there are no threads here.** `parse_diff.py` takes no
`--open-notes`, so a diff round carries none, nothing re-presents an exchange,
and there is no `--response` to record one against.

So answer an **`info`** comment **in the chat conversation with the human**, and
re-present the hunk unchanged next round. Do not write a response into a thread
that does not exist, and do not edit the file — an `info` is still a question,
not a directive. The reviewer's note is not lost either way: it lands in the
round's verdicts JSON and in the Revisions ledger the tab renders. If they
escalate to a `changes` turn, act on it then like any other `changes` comment.

For each section with a `changes` comment: parse the section `title` to extract
the filepath (`title` = `"{filepath} hunk N"`), then apply the targeted edit to
`{filepath}` in the working tree, locating the span within the hunk at
`anchor.offset`. Scope every edit to the hunk named in the title.

Every hunk approved → B5. Any `changes`/`info` → B4.

**B4. Re-diff and re-arm**

```bash
# Re-run the SAME capture argv as B1 — never a different one. A `git diff`
# substituted here on round 2 of a PR review reviews the working tree instead,
# which reads as a shrinking diff rather than as an error.
gh pr diff 187 > .viva/diff.patch          # or: git diff <ref> > .viva/diff.patch

if [ ! -s .viva/diff.patch ]; then
  echo "viva-review: diff is now empty — all changes were applied or reverted; finishing"
  curl -s -X POST "$BASE/complete" -H "Content-Type: application/json" \
    -d "{\"rounds_total\": N, \"sections_total\": M, \"sections_revised\": K}"
  exit 0
fi

python3 "$VIVA_DIR/scripts/parse_diff.py" .viva/diff.patch \
  --output .viva/review-input-r{N+1}.json --round {N+1} --doc-file "$DOC_FILE" \
  --prior-input .viva/review-input-r{N}.json \
  --prior-verdicts .viva/review-r{N}.json \
&& curl -s -X POST "$BASE/next-round?output=.viva/review-r{N+1}.json" \
     -H "Content-Type: application/json" -d @.viva/review-input-r{N+1}.json
```

The browser updates in place — no new tab. Back to B2.

**If the diff went empty and `/complete` was just called**, this session finished
but not the way B5 assumes: the diff reached zero because a hunk was reverted or
dropped at the human's request, not because every hunk was approved as-is. A hunk
that nets to nothing counts toward `sections_revised`, not the approved count.
Skip B5 and report:

> "Diff fully resolved — nothing left to review. N hunks approved, K hunks
> revised (including any reverted or dropped) across M files in R round(s)."

Then state plainly: "Working tree matches `<target>` — nothing to commit." Do not
prompt to commit; an empty diff means there is nothing to stage.

**B5. Finish** (all hunks approved)

```bash
curl -s -X POST "$BASE/complete" -H "Content-Type: application/json" \
  -d "{\"rounds_total\": N, \"sections_total\": M, \"sections_revised\": K}"
```

> "N hunks approved across M files in R round(s). K hunks revised."

Then ask: "Commit these changes? (y/n)" — and on yes, stage and commit the
reviewed working-tree changes.

---

## Scope

`/viva-review` is a **human gate**, not an LLM reviewer. It composes with
`/code-review` (which is an LLM pass): run `/code-review` first to apply
automated suggestions, then `/viva-review` for human sign-off before committing.

## Reference material

The opt-in layers live at the plugin root, under `references/`, so a plain review
never loads them:

- `producers.md` — annotations, the producer contract, confidence triage
- `open-notes.md` — comment threads carried across rounds
- `preferences.md` — recurring critiques learned across sessions
- `qa.md` — the batch-question gate, for a caller that needs one directly

`loop.py` prints the absolute path of whichever one documents the step you have
reached — read the path it printed rather than deriving it. To engage an opt-in
producer the driver has not named, pass `--parse-only` to `start` or `rearm`.

## File layout

`loop.py` writes and reads all of this on branch A; you name none of it.

```
.viva/
├── server.url             ← server writes on startup; deleted on shutdown
├── diff.patch             ← branch B only
├── review-input-r1.json   ← the round the server serves
├── review-r1.json         ← the verdicts the server writes back
├── open-notes.json        ← threads carried across rounds
├── preferences.json       ← learned critiques; survives the state clear
└── attachments/           ← image attachments, written during /submit
```
