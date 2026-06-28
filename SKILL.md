---
name: viva
description: Section-by-section markdown review. Human signs off on every section; agent rewrites and loops until all approved.
---

# viva

Section-by-section markdown review. Human signs off on every section; agent rewrites and loops until all approved. Named after the PhD oral exam — you present, they question, you defend and revise.

Replaces: `plan-reviewer`

## Setup

To enable brainstorming Q&A integration, run once in your Claude Code prompt:

  ! bash "$(find ~/.claude/skills/viva ~/.claude/plugins/cache -name install.sh -path "*/viva*" 2>/dev/null | head -1)"

---

## Invocation

  /viva path/to/file.md

If no path is given, scan the current directory for a single `.md` file.
If `.viva/server.url` exists when `/viva` starts, a previous session may still be running. Warn the user and do not launch a new server. If you are certain no server is running (e.g. after a crash), delete `.viva/server.url` before proceeding.

---

## Steps

The loop is **launch → wait for verdicts → act → (rewrite & re-arm | finish)**. It's tuned so the agent never makes the human wait on a tool round-trip and never loads the doc into context until a rewrite needs it — an all-approved round finishes without ever reading the doc.

**1. Parse and launch** (round 1 — one bash block, no doc read)

Do not read the `.md` into context first. The parser reads it from disk; the agent only needs the doc when a `changes`/`info` verdict requires a rewrite (step 4). Resolve the dir, clear stale state, parse, and launch in a single block:

```bash
# Resolve the skill dir once — direct path first, find only as fallback.
VIVA_DIR=~/.claude/skills/viva
[ -f "$VIVA_DIR/server.py" ] || \
  VIVA_DIR=$(find ~/.claude/plugins/cache -name "server.py" -path "*/viva*" -maxdepth 6 2>/dev/null \
             | xargs -I{} dirname {} | head -1)

# Clear stale state. Deleting server.url here is safe — the Invocation guard
# above has already confirmed no prior server is running.
mkdir -p .viva
rm -f .viva/server.url .viva/review-input-r*.json .viva/review-r*.json .viva/open-notes.json
rm -rf .viva/attachments

# Parse, then launch only if the integrity check passed (&&). Bounded poll so a
# failed start can't hang the turn.
# DEFAULT no-read path — use this single block UNLESS standing learned
# preferences exist (see the auto-engage branch right below).
python3 "$VIVA_DIR/scripts/parse_sections.py" <doc.md> \
  --output .viva/review-input-r1.json --round 1 --doc-file <relative/path/to/doc.md> \
&& {
  python3 "$VIVA_DIR/server.py" --mode review \
    --input .viva/review-input-r1.json --output .viva/review-r1.json &
  for i in $(seq 1 100); do [ -f .viva/server.url ] && break; sleep 0.1; done
}
[ -f .viva/server.url ] || { echo "viva: launch failed — parse or server start"; exit 1; }
BASE=$(cat .viva/server.url)
```

`$VIVA_DIR` and `$BASE` are reused for every later command — never repeat the `find`. The server opens the browser tab on startup and `$BASE` serves all API calls.

**Learned-preference auto-engage (round 1).** First check for standing learned preferences — this is a cheap state read, not a doc read:
```bash
python3 "$VIVA_DIR/scripts/preferences.py" list --store .viva/preferences.json --status standing
```
- **None** (the default until sign-offs accumulate) → use the single block above and skip to step 2. Round 1 stays a no-read launch.
- **Any standing preference** → do **not** use the atomic block. The preference producer must merge its flags *before* the server reads the input at startup, so split round 1 into three ordered steps:
  1. **Parse** (no launch yet):
     ```bash
     python3 "$VIVA_DIR/scripts/parse_sections.py" <doc.md> \
       --output .viva/review-input-r1.json --round 1 --doc-file <relative/path/to/doc.md> \
     || { echo "viva: parse failed"; exit 1; }
     ```
  2. **Run the preference producer** — read the doc, build the `kind: "preference"` sidecar for sections that repeat a standing critique, and merge it with `annotate.py` (see [Learned preferences](#learned-preferences-across-sessions) and [Judgment producers](#judgment-producers-llm-passes)).
  3. **Launch** the server, which now reads the flagged input:
     ```bash
     python3 "$VIVA_DIR/server.py" --mode review \
       --input .viva/review-input-r1.json --output .viva/review-r1.json &
     for i in $(seq 1 100); do [ -f .viva/server.url ] && break; sleep 0.1; done
     [ -f .viva/server.url ] || { echo "viva: launch failed"; exit 1; }
     BASE=$(cat .viva/server.url)
     ```
  This is the one case where round 1 reads the doc — paid deliberately, and only once the reviewer has accumulated standing preferences.

The parser splits verbatim, runs an integrity check, and writes the file directly — never parse by hand or read its output back into context. Parsing rules it implements (for reference):
- Split level: the highest heading level (fewest `#`s) that occurs more than once — usually `##`. If that level yields more than 20 sections, falls back one level coarser.
- A section = one split-level heading plus everything below it up to the next split-level heading, verbatim.
- Content before the first split-level heading becomes its own first section, titled with the H1 text (or "Preamble"). Omitted if empty.
- No headings → one section containing the whole doc, titled with the filename.
- A section titled `Revision History` is omitted; its lines are exempt from the integrity check.

Edge case: a doc too short to review (a few lines, no substantive content) → skip the server, treat as auto-approved.

**2. Wait for verdicts** (every round)

One block both waits and prints the verdicts — read them straight from stdout, no separate file read:
```bash
until [ -f .viva/review-r{N}.json ]; do sleep 0.3; done
cat .viva/review-r{N}.json
```
The server writes the file atomically (tmp + rename), so `cat` always sees complete JSON.

**3. Act on verdicts**

| Verdict | Action |
|---------|--------|
| `approved` | Carried forward; collapsed next round, reopenable |
| `changes` | Rewrite the section using `note` as the instruction. If the verdict carries an `anchor` string, that is the exact line/phrase the reviewer selected — locate it in the section's source and target the rewrite there. If the verdict carries an `attachments` array, `Read` each image path first — the screenshots are part of the instruction |
| `info` | Answer the `note` question, rewrite the section to incorporate the answer. If the verdict carries an `anchor`, the question is about that selected line. If the verdict carries an `attachments` array, `Read` each image path before answering |
| `pending` | Carry forward unchanged; re-present next round |

- **Every section `approved`** → go to step 5 (finish).
- **Any `changes`/`info`** → rewrite and re-arm the next round (step 4).

**4. Rewrite and re-arm** (only when something changed)

Now — and only now — load what the rewrite needs. Pull a compact id→heading map (no section bodies into context):
```bash
python3 -c "import json
for s in json.load(open('.viva/review-input-r{N}.json'))['sections']: print(s['id'], s['title'], sep='\t')"
```
Read the target `.md` (and optionally `PRODUCT.md`, `DESIGN.md`, `CLAUDE.md` for context), then rewrite every `changes`/`info` section directly in the source file. Preserve each heading's text exactly — next-round title matching depends on it. When a verdict carries an `anchor`, find that exact text in the section and scope the edit to that line rather than rewriting the whole section. Before rewriting a section whose verdict carries an `attachments` array, `Read` each listed path (e.g. `.viva/attachments/r2-s3-0.png`) so the screenshot informs the rewrite.

**Apply learned preferences while you rewrite.** The doc is already open, so consulting the standing set is free — pull it (`preferences.py list --store .viva/preferences.json --status standing --format json`) and apply each relevant preference to the sections you touch, so a recurring fix is already in when the card re-presents instead of waiting for the human to flag it again (see [Learned preferences](#learned-preferences-across-sessions)). An empty store is a no-op.

Then update the **open-note store** (issue #16). For every section the reviewer left *open* (verdict carried `"open": true`) or *settled* (`"settle": true`), and every section now `approved`, record the outcome — passing a one-line `--response "<id>=<what you changed>"` for each open note you just rewrote:
```bash
python3 "$VIVA_DIR/scripts/open_notes.py" update \
  --store .viva/open-notes.json --round {N} \
  --verdicts .viva/review-r{N}.json --input .viva/review-input-r{N}.json \
  --response "s2=Shortened the intro to two sentences"   # repeat per open note
```

Re-parse (passing `--open-notes` so still-open threads re-present on their cards) and signal the running server in one block, then loop to step 2:
```bash
python3 "$VIVA_DIR/scripts/parse_sections.py" <doc.md> \
  --output .viva/review-input-r{N+1}.json --round {N+1} --doc-file <relative/path/to/doc.md> \
  --prior-input .viva/review-input-r{N}.json --prior-verdicts .viva/review-r{N}.json \
  --open-notes .viva/open-notes.json \
&& curl -s -X POST "$BASE/next-round?output=.viva/review-r{N+1}.json" \
     -H "Content-Type: application/json" -d @.viva/review-input-r{N+1}.json
```
The browser updates in place — no new tab. The parser carries an ID forward as approved only when its title matches exactly (case-insensitive) AND its content is byte-for-byte identical; changed content requires re-review.

**5. Finish** (all sections approved)

Signal completion and append the revision history in one block. Run the open-note update once more for the final round first, so this round's approvals settle any still-open threads before the ledger is written:
```bash
python3 "$VIVA_DIR/scripts/open_notes.py" update \
  --store .viva/open-notes.json --round N \
  --verdicts .viva/review-rN.json --input .viva/review-input-rN.json
curl -s -X POST "$BASE/complete" -H "Content-Type: application/json" \
  -d "{\"rounds_total\": N, \"sections_total\": M, \"sections_revised\": K}"
python3 "$VIVA_DIR/scripts/revision_history.py" .viva <doc_file>
```
`revision_history.py` appends `## Revision History` — a summary line plus a verbatim table of every `changes`/`info` note. If any open notes were tracked, it adds an **Open notes** subsection with each thread's full exchange (every round's note → the agent's response) and its final status. If the heading already exists (re-reviewed doc), the new session's block is appended under it.

**Record learned preferences.** This session's notes are the training signal, so before moving on, cluster the `changes`/`info` notes you saw into distinct critiques and record each so a recurring one is learned (issue #17, [Learned preferences](#learned-preferences-across-sessions)). First `list --status all` to see existing preferences, then `record` each cluster — reuse an existing `--id` to reinforce it across sessions (the second session promotes it to standing), or create a new candidate with a short stable id:
```bash
python3 "$VIVA_DIR/scripts/preferences.py" list \
  --store .viva/preferences.json --status all
python3 "$VIVA_DIR/scripts/preferences.py" record \
  --store .viva/preferences.json --session "<date> <doc filename>" \
  --id cite-sources --label "Cite a source for every quantitative claim" \
  --guidance "When a section states a number, attach a citation or mark it unsourced." \
  --count <sections this critique hit this session>   # repeat per recurring critique
```
A session with no recurring critique records nothing; skip the step.

Then give the sign-off report — how many sections, how many rounds, what was revised — and ask:

> "Sign-off complete. Commit the doc to git? (y/n)"

If yes:
```bash
git add <doc_file>
git commit -m "docs: sign off on <filename>"
```

---

## File Layout

```
.viva/
├── server.url             ← server writes on startup; deleted on shutdown
├── review-input-r1.json   ← agent writes before round 1
├── review-r1.json         ← server writes after round 1
├── review-input-r2.json   ← agent writes before round 2 (if needed)
├── review-r2.json         ← server writes after round 2
├── open-notes.json        ← open_notes.py maintains; threads carried across rounds
├── preferences.json       ← preferences.py maintains; learned critiques, survives the round-1 clear
└── attachments/           ← server writes image attachments during /submit
```

For brainstorming Q&A:
```
.viva/
├── qa-input.json          ← brainstorming skill writes
├── answers.json           ← server writes
└── attachments/           ← server writes image attachments during /submit
```

---

## Annotations (advisory)

Each section in `review-input-r{N}.json` may carry an optional `annotations` array. The server renders each entry as a color-coded badge at the top of that section's card, so the reviewer sees an agent's flagged weak spots *before* choosing a verdict.

```json
{
  "id": "s3",
  "title": "Error Handling",
  "content": "## Error Handling\n...",
  "annotations": [
    { "kind": "grounding", "severity": "warn",  "message": "claim 'sub-second' is unsupported", "anchor": "line 12" },
    { "kind": "drift",     "severity": "error", "message": "code retries 3×, doc says 5×" }
  ]
}
```

- `kind` *(required)* — short producer tag shown as the badge label (e.g. `grounding`, `drift`, `checklist`).
- `severity` *(required)* ∈ `info | warn | error` → color slot `teal | violet | orange`. Any other value renders as `info`.
- `message` *(required)* — the inline text shown beside the badge.
- `anchor` *(optional)* — surfaced as the badge's hover title.

Annotations are **advisory**: they decorate a card, they never gate a verdict — the human still decides. A `review-input` with no `annotations` renders exactly as before.

### Producer contract

A pre-review pass is a **producer**. It writes annotations into the round's `review-input` file **after** `parse_sections.py` generates it and **before** the round is armed:

- **Round 1** — between the `parse_sections.py` call and launching `server.py --input` (the server reads the file once at startup).
- **Round 2+** — between the `parse_sections.py` re-parse and the `POST /next-round` that ships the file to the running server.

Producers are **opt-in** — the default loop in Steps runs none of them, so an unflagged review behaves exactly as today. Run a producer only when the user asks for that check (e.g. "ground the claims", "check for contradictions") or when the doc type clearly warrants it. The LLM passes (#9, #10) read the whole doc; that's the one time the [no-read fast path](#steps) is traded away, and only on request.

Every producer emits through one shared write-path — `scripts/annotate.py`, which merges a sidecar list of `{id, kind, severity, message, anchor?}` flags into the `review-input` (additive, so carried-forward flags survive; idempotent; a no-op on an empty sidecar):

```bash
python3 "$VIVA_DIR/scripts/<producer>.py" --input .viva/review-input-r{N}.json [...] \
  | python3 "$VIVA_DIR/scripts/annotate.py" --input .viva/review-input-r{N}.json --annotations -
```

Only flag **new or changed** sections: `parse_sections.py` carries a prior annotation forward for any section whose title and content are byte-identical, and drops it from a rewritten section (the producer re-flags the new text). So a round-2+ producer needs to look only at sections without carried flags.

#### Mechanical producers (bundled scripts)

| Producer | Script | Flags |
|----------|--------|-------|
| **Checklist gating** (#13) | `checklist.py --input IN [--type spec\|adr\|runbook]` | `error` per required section missing for the doc's type. Type is inferred from the filename/H1 when `--type` is omitted; an untyped doc emits nothing. Missing-section flags land on the **first** card — the integrity check forbids a card for a section that isn't in the doc. |
| **Spec↔code drift** (#11, existence) | `drift.py --input IN [--root .]` | `error` for a referenced file path that doesn't exist; `warn` for a simple `` `name()` `` symbol with no definition anywhere in the code. Prose-only sections emit nothing. |

#### Judgment producers (LLM passes)

These need reading and reasoning, so the agent runs them itself: analyze the doc, build a sidecar JSON, then merge it with `annotate.py --annotations <sidecar>`. Each flag's `id` is the target section's id from the round's `review-input` (pull the id→title map as in step 4). Run them only on request; analyze only un-flagged (new/changed) sections.

- **Claim grounding** (#9) — extract each section's checkable assertions (counts, signatures, file paths, behaviors), verify each against the repo, and emit a `warn`/`error` flag per unsupported or contradicted claim: the offending sentence + what the repo actually shows (`file:line` or "no match found"). Extraction is read-only — never rewrite the section.
- **Cross-section contradiction** (#10) — compare sections pairwise for incompatible statements (a §3 non-goal that §7 specifies; two sections with conflicting defaults). Emit a `warn` on **both** sides; set each flag's `anchor` to the *other* section's id so the badge names where the conflict lives.
- **Spec↔code signature drift** (#11, judgment) — the half `drift.py` deliberately skips: compare a described function/endpoint **signature** against the actual code and flag a mismatch (`warn`). Regex can't do this without false drift, so it's a reading task, not a script.
- **Learned preferences** (#17) — for each *standing* preference (`preferences.py list --store .viva/preferences.json --status standing --format json`), read every new/changed section and emit a `kind: "preference"` `warn` where the section repeats that learned critique: name what to change. Encode the preference id in the message (e.g. `[cite-sources] "80% faster" has no source`) — `annotate.py` keeps only `kind`/`severity`/`message`/`anchor`, so the id has to ride in the text for the human to trace the flag back. Unlike the other judgment producers, this one **auto-engages at round 1 when the store holds any standing preference** (the deliberate round-1 read above); on later rounds it is opt-in, flagging only un-flagged (new/changed) sections.

A sidecar is just a JSON list; write it to `.viva/producer-r{N}.json` and merge:

```bash
python3 "$VIVA_DIR/scripts/annotate.py" --input .viva/review-input-r{N}.json \
  --annotations .viva/producer-r{N}.json
```

### Confidence triage (sourced vs inferred)

When you generate or revise a doc, self-annotate each section with a **confidence** annotation so the reviewer's attention lands where you are weakest. It is an ordinary annotation with `kind: "confidence"` plus two structured fields:

```json
{ "kind": "confidence", "severity": "warn", "basis": "inferred", "level": "low", "message": "inferred · low" }
```

- `basis` — `sourced` (drawn from the repo, the user's input, or a cited fact) or `inferred` (your own guess or extrapolation).
- `level` — `high | medium | low` confidence in the section's correctness.
- Set `severity` to mirror the weakness (`error`/`warn` for low/inferred, `info` for high/sourced) so the badge color tracks it, and keep `message` a short human label.

Unlike the pre-review producers above, confidence is the **generating agent's own** self-annotation, emitted at write time. Append the confidence annotation **directly** to each section's `annotations` array in the round's `review-input` — do *not* route it through `annotate.py`, which keeps only `kind`/`severity`/`message`/`anchor` and would drop the `basis`/`level` fields the sort depends on. The server reads `basis`/`level` directly — never the message — to offer a **weakest-first** sort toggle (inferred + low first); document order stays the default. A section with no confidence annotation keeps document order, and a doc with none hides the toggle entirely. `parse_sections.py` carries a confidence annotation forward like any other on a byte-identical section.

---

## Open notes (carried across rounds)

A `changes`/`info` note normally lasts one round — the reviewer flags it, the agent rewrites, and the note is gone. When a rewrite doesn't actually satisfy the reviewer they would have to re-flag from scratch. An **open note** instead persists round to round, accumulating the exchange (what was asked, what the agent answered), until the reviewer **settles** it.

The store lives at `.viva/open-notes.json`, keyed by normalized section title, and `scripts/open_notes.py` is its single writer. The loop is:

1. A verdict carries `"open": true` when the reviewer ticks **keep open across rounds** on a `changes`/`info` note.
2. In step 4 you run `open_notes.py update`, passing a one-line `--response "<id>=…"` for each open note you rewrote. It appends the exchange to that section's thread.
3. The re-parse passes `--open-notes`, so still-open threads re-present on their cards next round with the full prior exchange shown.
4. The reviewer **settles** a thread (verdict `"settle": true`) when satisfied; approving the section settles its open notes too. A settled thread drops from later rounds.

Open notes **compose with verdicts, they don't replace them** — an open note never blocks sign-off on its own. A section signs off when its verdict is `approved`; the open thread is the conversation alongside that decision. At completion `revision_history.py` folds every thread's full exchange into the ledger. A doc where no note was ever kept open behaves exactly as before — no store, no `open_notes` on any card, no Open notes ledger section.

---

## Learned preferences (across sessions)

Reviewers repeat the same critiques across docs — "unsourced numbers", "passive voice", "no rollback step". viva learns these so a recurring issue is pre-applied or pre-flagged before the human re-types it (issue #17).

The store lives at `.viva/preferences.json` and `scripts/preferences.py` is its single writer. Unlike the round files, it is **not** cleared by the round-1 state reset, so it persists across sessions; it is gitignored, so learned preferences are a reviewer's own (per clone), not shared. It is plain JSON — the human can open and edit it, and `preferences.py set --status muted` retires a bad one. Preferences are **suggestions the agent applies, not rules**: a pre-applied fix still rides a normal rewrite to the human, and a pre-flag is an advisory annotation that never gates a verdict. Nothing is auto-accepted.

A preference carries a `status`:

| Status | Meaning | Consulted? |
|--------|---------|------------|
| `candidate` | observed in one session | no — recorded, waiting to recur |
| `standing` | recurred across ≥2 distinct sessions | yes — at rewrite and pre-flag time |
| `muted` | retired by the human | never; also never auto-promoted |

The semantic work — recognizing that "where's the citation for 80%?" and "unsourced stat" are one critique, and that a new cluster matches an existing preference — is judgment the agent does. `preferences.py` only does the bookkeeping: stable ids, the distinct-session count, and the candidate→standing promotion. The lifecycle is three touch points in the loop:

**Consult — step 4, default-on.** The doc is already open for the rewrite, so applying the standing set costs nothing. Pull it as JSON:
```bash
python3 "$VIVA_DIR/scripts/preferences.py" list \
  --store .viva/preferences.json --status standing --format json
```

**Pre-flag — round 1 auto, round 2+ opt-in.** A fresh incoming doc has no verdict yet, so the only way to surface a learned critique without the human typing it is the **preference producer** (see [Producers](#judgment-producers-llm-passes)). When the store holds any standing preference, it runs automatically at round 1 — the one case where the round-1 doc read is paid deliberately, and only once the reviewer has earned it by accumulating standing preferences.

**Record — step 5, at sign-off.** This session's `changes`/`info` notes are the training signal. Cluster them into distinct critiques, `list --status all` to see which existing preferences they match, then `record` each — reusing an existing `--id` to reinforce it across sessions (the second session promotes it to standing), or creating a new candidate:
```bash
python3 "$VIVA_DIR/scripts/preferences.py" record \
  --store .viva/preferences.json --session "<date> <doc filename>" \
  --id cite-sources --label "Cite a source for every quantitative claim" \
  --guidance "When a section states a number, attach a citation or mark it unsourced." \
  --count <sections this critique hit this session>
```
Use a short, stable `--id` (e.g. `cite-sources`) so a later session matches and reinforces it instead of forking a near-duplicate. Only a signed-off session learns — an abandoned review records nothing.

A project with no `preferences.json` behaves exactly as before: step 4 lists an empty set, no producer auto-engages, and nothing is recorded until the first sign-off.
