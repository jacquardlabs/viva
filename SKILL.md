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

**0. Resolve viva directory** (once per session, before step 1)

```bash
VIVA_DIR=~/.claude/skills/viva
[ -f "$VIVA_DIR/server.py" ] || \
  VIVA_DIR=$(find ~/.claude/plugins/cache -name "server.py" -path "*/viva*" -maxdepth 6 2>/dev/null \
             | xargs -I{} dirname {} | head -1)
```

`$VIVA_DIR` is reused for every subsequent command — never repeat the `find`.

**1. Read context**
- Read the target `.md` file
- Optionally read `PRODUCT.md`, `DESIGN.md`, `CLAUDE.md` for context

**2. Parse sections and write review-input JSON** (run before every review round)

Use the bundled parser — it splits verbatim, runs an integrity check, handles approved-ID matching, and writes the file directly. Never parse by hand or read the output back into context.

Round 1 only: first clear stale files: `mkdir -p .viva && rm -f .viva/review-input-r*.json .viva/review-r*.json`

Round 1:
```bash
python3 "$VIVA_DIR/scripts/parse_sections.py" <doc.md> \
  --output .viva/review-input-r1.json \
  --round 1 \
  --doc-file <relative/path/to/doc.md>
```

Round 2+:
```bash
python3 "$VIVA_DIR/scripts/parse_sections.py" <doc.md> \
  --output .viva/review-input-r{N}.json \
  --round {N} \
  --doc-file <relative/path/to/doc.md> \
  --prior-input .viva/review-input-r{N-1}.json \
  --prior-verdicts .viva/review-r{N-1}.json
```

The script exits non-zero if parsing fails the integrity check. With `--prior-input` / `--prior-verdicts`, it carries forward approved IDs only when the section title matches exactly (case-insensitive) AND the content is byte-for-byte identical. Changed content requires re-review.

Parsing rules the script implements (for reference):
- Split level: the highest heading level (fewest `#`s) that occurs more than once — usually `##`. If that level yields more than 20 sections, falls back one level coarser.
- A section = one split-level heading plus everything below it up to the next split-level heading, verbatim.
- Content before the first split-level heading becomes its own first section, titled with the H1 text (or "Preamble"). Omitted if empty.
- No headings → one section containing the whole doc, titled with the filename.
- A section titled `Revision History` is omitted; its lines are exempt from the integrity check.

Edge cases:
- Doc too short to review (a few lines, no substantive content) → skip review, consider auto-approved

**3. Launch or signal server**

Round 1 — launch in background and wait for it to write its URL:
```bash
mkdir -p .viva
python3 "$VIVA_DIR/server.py" \
  --mode review \
  --input .viva/review-input-r1.json \
  --output .viva/review-r1.json &

until [ -f .viva/server.url ]; do sleep 0.5; done
BASE=$(cat .viva/server.url)
```
Server opens the browser tab on startup. `$BASE` is used for all subsequent API calls.

Round 2+ — signal the running server after writing the new input JSON:
```bash
curl -s -X POST "$BASE/next-round?output=.viva/review-r{N}.json" \
  -H "Content-Type: application/json" \
  -d @.viva/review-input-r{N}.json
```
The browser updates in place — no new tab or subprocess.

**4. Wait for round output**

Poll for the output file at half-second intervals:
```bash
until [ -f .viva/review-r{N}.json ]; do sleep 0.5; done
```
When it appears, read verdicts.

Verdicts:
| Verdict | Action |
|---------|--------|
| `approved` | Add to `approved_ids`; shown collapsed next round but can be reopened |
| `changes` | Rewrite the section in the doc using `note` as the instruction |
| `info` | Answer the question in `note`, rewrite the section incorporating that answer |
| `pending` | Carry forward unchanged; re-present next round |

**5. Rewrite** all `changes` and `info` sections directly in the source `.md` file. Preserve the section heading text exactly — title-based matching in the next round depends on it.

**6. Loop exit**

After step 5: if every section has `verdict: "approved"`, signal completion, then proceed to step 7 (append history) and step 8 (sign-off report):
```bash
curl -s -X POST "$BASE/complete" \
  -H "Content-Type: application/json" \
  -d "{\"rounds_total\": N, \"sections_total\": M, \"sections_revised\": K}"
```
Otherwise loop to step 2 (re-parse → signal server → wait).

**7. Append revision history**

After signalling completion, append the session's decision record to the doc:

```bash
python3 "$VIVA_DIR/scripts/revision_history.py" .viva <doc_file>
```

This appends `## Revision History` — a summary line plus a table of every
`changes`/`info` note, verbatim from the round files. If the heading already
exists (re-reviewed doc), the new session's block is appended under it.

**8. Sign-off report**

Summarise: how many sections, how many rounds, what was revised. Then ask:

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
