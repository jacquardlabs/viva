---
name: viva-write
description: Doc-first intake. Pick a document type, attach context (repo paths, issue refs, files, URLs), answer only the residual questions, and the drafted doc goes straight into editorial review in the same browser tab.
---

# viva-write

Doc-first intake: a **type** and its **attachments** start the flow. You read the
attachments, ask only what they could not answer, fill the type's section
grammar, and hand the draft to the same browser tab for editorial rounds.

`/viva-review` judges a doc that already exists. `/viva-write` is the other end
of the lifecycle — it produces the doc and then hands it to that same review,
without a second server launch. The two split the product by **intent**: am I
making a thing, or judging one.

## Invocation

```
/viva-write [type] [path/to/draft.md] [attachment …]
```

- **type** — a name `doc_types.py` resolves (`design-doc`, `plan`, `readme`,
  `pr-description`, `progress-note`, `handoff`, or one the repo committed under
  `.viva-types/`). Omitted → the interview's first question is *what's the
  deliverable*.
- **path** — where the draft lands. Omitted → asked in the interview. Nothing is
  invented under the user's repo.
- **attachment** — an issue ref (`#170`, `owner/repo#170`, a github.com
  issue/pull URL), a URL, a file path, or a directory.

Role inversion needs nothing extra: attach a doc a human wrote and you are the
editor over their manuscript, running that type's checks.

Resolve the plugin once — `$VIVA_DIR` is reused by every later command:

```bash
# Highest version wins, not newest mtime: two cached versions can carry the same
# mtime, and `ls -t` then breaks the tie by name — picking 1.24.0 over 2.0.2.
VIVA_DIR=$(find ~/.claude/plugins/cache -maxdepth 4 -path "*/jacquardlabs-marketplace/viva/*" -name server.py 2>/dev/null \
           | awk -F/ '{split($(NF-1), v, "."); printf "%09d%09d%09d\t%s\n", v[1]+0, v[2]+0, v[3]+0, $0}' \
           | sort -r | head -1 | cut -f2-)
VIVA_DIR=${VIVA_DIR%/server.py}
[ -f "$VIVA_DIR/scripts/loop.py" ] || { echo "viva-write: loop.py not found — /plugin marketplace add jacquardlabs/marketplace, then /plugin install viva@jacquardlabs-marketplace"; exit 1; }
```

---

## The flow

**type → attach → interview → draft → hand off → rounds → stamp.** Steps 5–7 are
`/viva`'s loop verbatim, driven by `loop.py`; this skill owns only what comes
before the hand-off.

**1. Resolve the type**

```bash
python3 "$VIVA_DIR/scripts/doc_types.py" <type>        # one bundle
python3 "$VIVA_DIR/scripts/doc_types.py" --list        # the menu, when none was named
```

The bundle is `{name, title, sections, checks, default_pass}`. `sections` is the
heading grammar you fill, `checks` names the producers that run before the
reviewer sees round 1, `default_pass` is the depth round 1 runs at. An unknown
name is refused here, loudly — never fall back to an untyped session.

**2. Resolve the attachments**

```bash
python3 "$VIVA_DIR/scripts/context_refs.py" <attachment …> [--max-files N] [--max-bytes N]
```

One manifest, bounded. Act on it by `kind`:

| `kind` | What you do |
|--------|-------------|
| `issue` / `pr` | Run the entry's `fetch` argv verbatim — it is already the right `gh` invocation for that ref, repo flag included. Never compose your own. |
| `file` | `Read` the entry's `path`. `"text": false` means the sniff found binary — `Read` it when it is an image or a PDF, and for anything else (an archive, a compiled object) report the ref and skip it. Never guess at contents you could not read. |
| `dir` | `Read` the files the entry lists. It expanded under the caps; nothing outside `files[]` was read. |
| `url` | `WebFetch` the entry's `url`. |

**Report `dropped[]` to the human before you go on** — one line naming the count
and the reason (`file cap`, `byte cap`, `not text`). A directory that hit a cap
is a directory you have only partly seen, and the human is the one who knows
whether the part you missed mattered. Offer the narrower ref or the bigger cap;
do not silently draft from a truncated read.

**3. Clear state and run the interview**

The interview shrinks toward zero as the attachments answer more. **It is never
deleted.** Filling a section from guessed intent is the exact failure the
grilling exists to prevent — so a question survives for every decision the
attachments do not settle, and restating an issue body is not settling it.

Ask about: the deliverable and its path when the invocation named neither; the
audience and the decision the doc has to carry; every fork the attachments leave
open; anything a section of the grammar has no material for. Do **not** ask what
an attachment already states.

```bash
[ -f .viva/server.url ] && { echo "viva-write: a session may be open at $(cat .viva/server.url 2>/dev/null) — check that tab first. Finish or abandon it there; delete .viva/server.url only if nothing is answering."; exit 1; }

mkdir -p .viva
rm -f .viva/review-input-r*.json .viva/review-r*.json .viva/open-notes.json .viva/answers.json
rm -rf .viva/attachments
```

That clear is `loop.py start`'s, matched deliberately: `preferences.json` is the
one survivor (cross-session, per-clone), and `open-notes.json` **must** go — a
stale store injects a prior session's threads into this session's round 2.

Write `.viva/qa-input.json` (the `QAInput` shape — `references/qa.md` at the
plugin root is the full contract; `choices` and `recommended_choice` are both
optional), then launch and wait:

```bash
python3 "$VIVA_DIR/server.py" --mode qa \
  --input .viva/qa-input.json --output .viva/answers.json &
for i in $(seq 1 100); do [ -f .viva/server.url ] && break; sleep 0.1; done
[ -f .viva/server.url ] || { echo "viva-write: server start failed"; exit 1; }

until [ -f .viva/answers.json ]; do sleep 0.3; done
cat .viva/answers.json
```

Issue the wait with a generous timeout (~10 min / 600000ms) — it is human time,
not computation. `Read` any path in an answer's `attachments`.

**`submitted_early: true` means the human stopped short**, and the questions
they skipped are exactly the decisions a draft would otherwise fill from guessed
intent. Do not draft past them. Report which are unanswered and ask for one of
two things — there is no third: draft with each unanswered decision carried into
the doc as an explicit open question rather than a guess, or `loop.py abandon`
and start over. The interview cannot be re-presented on this server: the tab has
moved to its processing card, and `/next-round` reflows into review cards only.

**Never call `/complete` here.** This server is the one the review round runs on;
completing it tears the process down out from under the hand-off. The human sits
on a processing card from their submit until step 5 pushes the round — that wait
is the drafting, and the card says so on its own after 20s.

**4. Draft**

Write the doc at the resolved path. The grammar is fixed by the bundle: one
heading per entry in `sections`, in that order, plus whatever the material needs
beyond them. Attachments supply the facts, the answers supply the residue.

**The register is fixed by `references/style.md`** at the plugin root — read it
before you write. Concise and technical: the point first, decisions stated as
fact, no preamble restating the brief, no filler, and a trim pass on your own
draft before step 5 parses it.

Cite a source a reader can open, in the prose, where they would want it
(`config.py:42`, `#170`, the URL). **The interview is not a source, and neither
is this session.** An answer settles a decision; write the decision, not
"(per the interview)" or "decided this session" — that provenance lives in the
confidence sidecar you emit at step 5, never in the doc text. Structured
citations are #145's call, not this flow's — do not invent a field for them.

**Nothing in this file is advice on what to argue.** The type fixes the
sections, the register fixes the density, the attachments fix the facts, the
human fixes the rest at the gate. If this step starts accumulating craft
guidance — how to make the case, what a good design doc says — the product has
drifted.

**5. Parse, produce, hand off**

```bash
python3 "$VIVA_DIR/scripts/parse_sections.py" <doc> \
  --output .viva/review-input-r1.json --round 1 --doc-file <doc> \
  --doc-type <type> --pass <bundle.default_pass>
```

`--pass` is not optional here: the bundle's `default_pass` is what makes a type's
depth real, and a typed session that drops it runs at no depth at all. A
`checks` default (e.g. `progress-note`) additionally holds the round open until
every check flag carries a `result`.

Then run the producers — **before the hand-off, never after**. The server reads
its round once, when it is armed, so a merge into a round it is already serving
is one the reviewer never sees; `loop.py annotate` refuses that case outright.

```bash
# one per entry in the bundle's `checks`; the script name is the check name
# with `-` as `_`
python3 "$VIVA_DIR/scripts/doc_types.py" <type> \
| python3 "$VIVA_DIR/scripts/headings_present.py" --input .viva/review-input-r1.json --bundle - \
| python3 "$VIVA_DIR/scripts/loop.py" annotate --sidecar -
```

You just wrote every section, so emit the **confidence** self-annotation now,
while the basis for each is still in hand — `sourced` for a fact an attachment
carried, `inferred` for a call you made. Write the sidecar and merge it the same
way (`producers.md` has the shape). If the preferences store holds standing
preferences, run the learned-preference producer too and merge that sidecar —
this is a write, and a recurring critique is cheaper to apply now than to have
flagged back at you.

Then hand the round to the running server:

```bash
BASE=$(cat .viva/server.url)
python3 -c "import json; d=json.load(open('.viva/review-input-r1.json')); d['output']='.viva/review-r1.json'; print(json.dumps(d))" \
  | curl -s -X POST "$BASE/next-round" -H "Content-Type: application/json" --data-binary @-
```

Same process, same `server.url`, same tab — the Q&A cards reflow into section
cards in place. `output` **must** be `.viva/review-r1.json`, distinct from the
Q&A output: reusing `.viva/answers.json` lets the first review submit overwrite
the answers you drafted from.

**6. Editorial rounds**

From here `loop.py` drives, exactly as in `/viva` — it derives the round number
from disk, so you never type one:

```bash
python3 "$VIVA_DIR/scripts/loop.py" wait     # ~10 min timeout; human review time
```

`wait` prints the verdicts, the id→title map, the standing preferences, and a
classification line. Route on that line, never on your own scan:

| `wait` prints | Where to go |
|---------------|-------------|
| `all-approved` | step 7 — finish |
| `has-work`, some section carrying active comments | rewrite, then `loop.py rearm`; loop back to `wait` |
| `has-work`, **no** active comment anywhere | The round is held by its **pass**, not by a rewrite — every section is approved and the conjunct is not. This is the normal path for a `checks` bundle, not an exotic one. Reopen the producer seam: `loop.py rearm --parse-only`, re-run the type's check producer emitting a `result` on each flag, `loop.py annotate --sidecar -`, `loop.py arm`. Loop back to `wait`. `loop.py finish` names the unsatisfied conjunct if you need it spelled out. |
| `submitted-early` | run `loop.py rearm` with no `--response` **first** (it returns their tab from the processing card to its cards), then report the pending count and ask whether to re-present, keep waiting, or stop. `loop.py abandon` is stop. |

Within a `has-work` round, act on each section's `comments[]` **by comment
`type`**: a **`changes`** comment is a directive — apply it now as a targeted
edit. A **`suggestion`** carries the wording — paste its `replacement` over the
anchored span verbatim, character for character, nothing outside the anchor. An
**`info`** comment is a question — answer it in the thread and do **not** edit
the section. `anchor.offset` locates the span and `anchor.text` confirms it;
`offset: -1` means that ordinal did not resolve, so scope by the section and the
note rather than by the first match. `Read` any `comment.attachments` first.
Rewrite in the register step 4 drafted in — `wait` prints the path to
`style.md` beside the thread rules — and trim what you touched. Preserve each
heading's text exactly — next-round title matching depends on it.

```bash
python3 "$VIVA_DIR/scripts/loop.py" rearm \
  --response "s2-c1=Cited the TTL from config.py:42" \
  --response "s4-c1=Answered in thread; no edit"
```

One `--response "<cid>=<what you changed>"` per comment you rewrote or answered,
using the server's own `{sectionId}-c{n}` verbatim. Comply by default; when a
comment is wrong on the record — it contradicts this session's answers, an
attachment, or a measurement — use `--decline "<cid>=<grounds>"` instead. Taste
is not grounds. A decline settles nothing: the thread carries forward and the
section stays held until the reviewer accepts or insists, and **insisting wins**.
`loop.py rearm --pass <kind>` changes the round's depth (a structural round 1,
a line round 2, a `checks` or `final` round later); `--parse-only` reopens the
producer seam before the next round ships.

**7. Stamp**

```bash
python3 "$VIVA_DIR/scripts/loop.py" finish --doc <doc>
```

`finish` refuses any non-approved section, settles the round's threads, appends
the verbatim `## Revision History` ledger, and ends the session. Then give the
sign-off report — type, sections, rounds, what was revised — and take the stamp
the type calls for:

| Type | Stamp |
|------|-------|
| `pr-description` | `gh pr view --json number` on the current branch names the PR; `gh pr edit <n> --body-file <doc>` if it resolves, `gh pr create --body-file <doc>` if it does not |
| `handoff` | if intake attached an issue ref for the receiving team, `gh issue comment <n> --body-file <doc>` posts the handoff there; otherwise `git add <doc> && git commit -m "docs: <title>"` |
| everything else | `git add <doc> && git commit -m "docs: <title>"` |

Ask before running it — a stamp is outward-facing. A stamp that does nothing
makes this a writing tool (#165 guard 2), so do not end on the ledger alone.
Bundles carry no `stamp` field today, so this table is the mapping; moving it
into the bundle is a follow-up, not this flow's call.

Finally, cluster this session's `changes`/`info` notes into distinct recurring
critiques and record them — `finish` prints the path to `preferences.md`. A
session with no recurring critique records nothing.

---

## Reference material

`references/` at the plugin root is shared with `/viva-review`, and `loop.py`
prints the absolute path of whichever file documents the step you have reached:

- `qa.md` — the `QAInput`/`answers.json` contract step 3 uses, and the hand-off
  step 5 opts into
- `producers.md` — annotations, the producer contract, confidence
- `open-notes.md` — comment threads carried across rounds
- `preferences.md` — recurring critiques learned across sessions

## File layout

```
.viva/
├── qa-input.json          ← you write (step 3)
├── answers.json           ← server writes (step 3)
├── server.url             ← one server, launched at step 3, torn down at step 7
├── review-input-r1.json   ← parse_sections writes (step 5), you hand off
├── review-r1.json         ← the verdicts the server writes back
├── open-notes.json        ← threads carried across rounds
├── preferences.json       ← learned critiques; survives the state clear
└── attachments/           ← image attachments, written during /submit
```
