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
rm -f .viva/server.url .viva/review-input-r*.json .viva/review-r*.json

# Parse, then launch only if the integrity check passed (&&). Bounded poll so a
# failed start can't hang the turn.
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
| `changes` | Rewrite the section using `note` as the instruction |
| `info` | Answer the `note` question, rewrite the section to incorporate the answer |
| `pending` | Carry forward unchanged; re-present next round |

- **Every section `approved`** → go to step 5 (finish).
- **Any `changes`/`info`** → rewrite and re-arm the next round (step 4).

**4. Rewrite and re-arm** (only when something changed)

Now — and only now — load what the rewrite needs. Pull a compact id→heading map (no section bodies into context):
```bash
python3 -c "import json
for s in json.load(open('.viva/review-input-r{N}.json'))['sections']: print(s['id'], s['title'], sep='\t')"
```
Read the target `.md` (and optionally `PRODUCT.md`, `DESIGN.md`, `CLAUDE.md` for context), then rewrite every `changes`/`info` section directly in the source file. Preserve each heading's text exactly — next-round title matching depends on it.

Re-parse and signal the running server in one block, then loop to step 2:
```bash
python3 "$VIVA_DIR/scripts/parse_sections.py" <doc.md> \
  --output .viva/review-input-r{N+1}.json --round {N+1} --doc-file <relative/path/to/doc.md> \
  --prior-input .viva/review-input-r{N}.json --prior-verdicts .viva/review-r{N}.json \
&& curl -s -X POST "$BASE/next-round?output=.viva/review-r{N+1}.json" \
     -H "Content-Type: application/json" -d @.viva/review-input-r{N+1}.json
```
The browser updates in place — no new tab. The parser carries an ID forward as approved only when its title matches exactly (case-insensitive) AND its content is byte-for-byte identical; changed content requires re-review.

**5. Finish** (all sections approved)

Signal completion and append the revision history in one block:
```bash
curl -s -X POST "$BASE/complete" -H "Content-Type: application/json" \
  -d "{\"rounds_total\": N, \"sections_total\": M, \"sections_revised\": K}"
python3 "$VIVA_DIR/scripts/revision_history.py" .viva <doc_file>
```
`revision_history.py` appends `## Revision History` — a summary line plus a verbatim table of every `changes`/`info` note. If the heading already exists (re-reviewed doc), the new session's block is appended under it.

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
└── review-r2.json         ← server writes after round 2
```

For brainstorming Q&A:
```
.viva/
├── qa-input.json          ← brainstorming skill writes
└── answers.json           ← server writes
```
