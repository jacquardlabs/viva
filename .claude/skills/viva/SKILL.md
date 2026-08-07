---
name: viva
description: Section-by-section markdown review. Human signs off on every section; agent rewrites and loops until all approved.
---

# viva

Section-by-section markdown review. Human signs off on every section; agent rewrites and loops until all approved. Named after the PhD oral exam — you present, they question, you defend and revise.

Replaces: `plan-reviewer`

## Brainstorming Q&A

viva exposes a `/viva-qa` skill for batch Q&A sessions. The superpowers
`brainstorming` skill calls `/viva-qa` directly when viva is installed — no
`install.sh` patch is needed. See the sibling `/viva-qa` skill for the full
invocation contract.

---

## Invocation

  /viva path/to/file.md

If no path is given, scan the current directory for a single `.md` file.

`scripts/loop.py` drives the loop. It derives the round number from disk, owns
every round file and every call to the server, and refuses to start when a
previous session may still be running. You bring the judgment: what a comment
means, how to rewrite the section, what this reviewer keeps asking for.

Resolve the plugin once — `$VIVA_DIR` is reused by every later command:

```bash
# Resolve the skill dir from the installed plugin cache — no personal-skill
# fallback (a leftover ~/.claude/skills/viva would shadow a fresh install).
VIVA_DIR=$(find ~/.claude/plugins/cache -maxdepth 6 -path "*/viva/*" -name server.py -print0 2>/dev/null \
           | xargs -0 -r ls -t 2>/dev/null | head -1)
VIVA_DIR=${VIVA_DIR%/server.py}
[ -f "$VIVA_DIR/scripts/loop.py" ] || { echo "viva: loop.py not found — install the viva plugin (/plugin install viva@jacquardlabs-marketplace)"; exit 1; }
```

---

## Steps

The loop is **start → wait → route → (rewrite & re-arm | finish)**. It is tuned
so you never make the human wait on a tool round-trip and never load the doc
into context until a rewrite needs it — an all-approved round finishes without
ever reading the doc.

**1. Start** (round 1)

Do not read the `.md` first. The parser reads it from disk; you need it only
when a comment requires a rewrite (step 4).

```bash
python3 "$VIVA_DIR/scripts/loop.py" start --doc <relative/path/to/doc.md>
```

`start` clears stale state, parses round 1, launches the server, opens the
browser tab, and prints the round and `$BASE`. It reads disk to pick the round-1
branch itself: a plain launch; a **resumed sign-off**, where a doc already
carrying a `## Revision History` from a previous session gets that session's
approvals carried forward so the human re-reviews only what changed; or a stop
after parsing when the preferences store holds a standing preference. It
**refuses** when `.viva/server.url` exists — a prior session may still be
running with the reviewer's tab open. Report that; delete the file only if you
are certain no server is running (e.g. after a crash).

Pass `--split-on '<REGEX>'` for a task-card plan document: a heading is a split
point iff its title matches (`re.search`, at any depth), replacing the
split-level heuristic entirely. Zero matches is a hard error, not a fallback.
The pattern is recorded in the round file, so every later round re-splits the
same way — and `start` reads it back off the prior round when resuming a
signed-off doc, so a resume does not need it retyped.

Without it the parser splits on the highest heading level occurring more than
once (usually `##`, one level coarser past 20 sections), verbatim; content
before the first split heading becomes its own first section; a
`Revision History` section is omitted and exempt from the integrity check.
`scripts/parse_sections.py`'s module docstring is the full contract. Never parse
by hand or read the parser's output back into context.

**If `start` stops after parsing**, it prints the path of the producer contract
to read: run the preference producer, then `loop.py annotate --sidecar <path>`
and `loop.py arm`. That is the one round-1 doc read, paid deliberately, and only
once this reviewer has accumulated standing preferences.

**2. Wait for verdicts** (every round)

```bash
python3 "$VIVA_DIR/scripts/loop.py" wait
```

Blocks on human review time, then prints the verdicts, the round's id→title map,
the standing preferences, and a classification line. Read all four straight from
stdout — steps 3 and 4 reuse them instead of re-fetching. Issue it with a
generous timeout (~10 min / 600000ms); a tool's default 2 minutes would
spuriously fail mid-review, and re-issuing the identical command after a timeout
is safe. It exits non-zero when the server disappears, so a killed session ends
the wait instead of outliving it.

**3. Route the round**

The classification line routes you. Three destinations, no fourth:

| `wait` prints | What happened | Where to go |
|---------------|---------------|-------------|
| `all-approved` | every section approved | step 5 — finish |
| `has-work` | at least one section carries active comments | step 4 — rewrite, then re-arm |
| `submitted-early` | the reviewer hit *skip rest & submit* and paused | run `loop.py rearm` with no `--response` **first** — that returns their tab from the processing card to its cards — **then** report the pending count and ask whether to re-present, keep waiting, or stop. Re-present and keep waiting are both already satisfied by the re-arm: loop to step 2. Stop is `loop.py abandon`, which ends the session and reports that the doc was **not** signed off. |

Within a `has-work` round, act on each section by its verdict:

| Verdict | Action |
|---------|--------|
| `approved` | Carried forward; collapsed next round, reopenable |
| `changes`/`info` | The section carries a `comments` array. Act on each comment **by its `type`** (the hybrid rule): a **`changes`** comment is a *directive* — apply its `note` as a targeted edit **now** so the reviewer reviews the diff. A **`suggestion`** comment is a directive with the wording attached — replace the anchored span with its `replacement` **verbatim**: character for character, no rewrite pass, no interpretation, no improving on the reviewer's phrasing, and nothing outside the anchor. An **`info`** comment is a *question* — answer it in the thread and **do not edit the section**. A section is edited for an `info` thread only once the discussion escalates to a `changes` turn. |
| `pending` | Carry forward unchanged; re-present next round |

The verdict is **derived** from the section's active comments (unsettled, and
carrying a note — or, for a suggestion, replacement wording): no active comments
→ `approved` if the reviewer approved, otherwise `pending`; any active comment
with `type: "changes"` or `type: "suggestion"` → section `changes`; otherwise
(only active `info` comments) → section `info`. A section carrying a live
suggestion is never approved.

**4. Rewrite and re-arm** (only when the round has work)

Now — and only now — load what the rewrite needs. The id→title map and the
standing preferences were already printed by `loop.py wait`; reuse them, don't
re-fetch. Read the target `.md` (and optionally `PRODUCT.md`, `DESIGN.md`,
`CLAUDE.md` for context).

Loop over `comments[]` for each section and act **by type**, as the verdict
table says. Before applying or answering a comment, if `comment.attachments` is
present, `Read` each listed path — the image is context for the edit or answer.
For a **`changes`** comment, rewrite directly in the source file:
`anchor.offset` locates the edit within the section source and `anchor.text`
confirms it. The offset already names the occurrence the reviewer picked, so a
repeated phrase needs no guessing. `offset: -1` means that ordinal did not
resolve — `anchor.text` may still appear in the source, just not there — so
scope the edit by the section and the note, never by the first match of a
phrase that repeats. An un-anchored comment scopes to the whole section. For a
**`suggestion`** the same anchor rules place the edit and `replacement` is what
goes there — paste it, do not compose it. Its `note` is the reviewer's reason,
never a second instruction, and no standing preference overrides supplied
wording. An un-anchored suggestion names no span: treat it as a `changes`
directive scoped to the section rather than guessing where the wording lands.
For an **`info`** comment, do not edit the source — answer in the thread
response only. For a carried-forward thread, act on its **latest** reviewer
turn's type the same way — a carried suggestion turn keeps its `replacement` on
the exchange, beside `note`; `wait` prints the path to the thread rules when the
round has work. Preserve each heading's text exactly —
next-round title matching depends on it.

**Apply standing preferences while you rewrite.** The doc is already open, so
consulting the standing set `wait` printed costs nothing: apply each relevant
preference to the sections you touch, so a recurring fix is already in when the
card re-presents instead of waiting for the human to flag it again. An empty set
is a no-op.

Then re-arm — one `--response "<cid>=<what you changed>"` per comment you
rewrote or answered. The `cid` is the server's own `{sectionId}-c{n}` (e.g.
`s2-c1`); use it verbatim, never synthesize it. Approving a section settles all
of its threads, so those need no response.

```bash
python3 "$VIVA_DIR/scripts/loop.py" rearm \
  --response "s2-c1=Shortened the intro to two sentences" \
  --response "s4-c1=Answered in thread; no edit"
```

`rearm` records the exchanges, re-parses the doc, and ships the next round to
the running tab — the browser updates in place, no new tab. A section carries
forward as approved only when its title matches exactly (case-insensitive) AND
its content is byte-for-byte identical; changed content comes back for
re-review. Add `--parse-only` when a producer must flag the new round before the
reviewer sees it; `loop.py annotate --sidecar <path>` then `loop.py arm` closes
that seam. Loop to step 2.

**5. Finish** (all sections approved)

```bash
python3 "$VIVA_DIR/scripts/loop.py" finish --doc <doc_file>
```

`finish` settles the round's remaining threads, ends the session, and appends
`## Revision History` to the doc — a summary line, a verbatim table of every
`changes`/`info` note and every suggestion's wording, and an **Open notes**
subsection carrying each thread's full exchange when any were tracked. On a
re-reviewed doc the new block is appended under the existing heading. It
**refuses** on any non-approved section and prints the pending count: nothing
is auto-accepted.

It then names the preferences reference — cluster this session's `changes`/`info`
notes into distinct critiques and record each, so a recurring one is learned. A
session with no recurring critique records nothing.

Give the sign-off report — how many sections, how many rounds, what was revised
— and ask:

> "Sign-off complete. Commit the doc to git? (y/n)"

If yes:
```bash
git add <doc_file>
git commit -m "docs: sign off on <filename>"
```

---

## Reference material

The opt-in layers live beside this file, under `references/`, so a plain review
never loads them:

- `producers.md` — annotations, the producer contract, confidence triage
- `open-notes.md` — comment threads carried across rounds
- `preferences.md` — recurring critiques learned across sessions

`loop.py` prints the absolute path of whichever one documents the step you have
reached — read the path it printed rather than deriving it. To engage an opt-in
producer the driver has not named, pass `--parse-only` to `start` or `rearm`:
that stops at the seam and prints both the round file and `producers.md`.

---

## File Layout

`loop.py` writes and reads all of this; you name none of it.

```
.viva/
├── server.url             ← server writes on startup; deleted on shutdown
├── review-input-r1.json   ← the round the server serves
├── review-r1.json         ← the verdicts the server writes back
├── open-notes.json        ← threads carried across rounds
├── preferences.json       ← learned critiques; survives the state clear
└── attachments/           ← image attachments, written during /submit
```

For brainstorming Q&A:
```
.viva/
├── qa-input.json          ← brainstorming skill writes
├── answers.json           ← server writes
└── attachments/           ← server writes image attachments during /submit
```
