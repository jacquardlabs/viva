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

**1. Read context**
- Read the target `.md` file
- Optionally read `PRODUCT.md`, `DESIGN.md`, `CLAUDE.md` for context

**2. Parse sections** (run before every review round)

Split the document mechanically at markdown headings. This is parsing, not interpretation — the reviewer sees the document itself, never your summary of it. Do not paraphrase, reorder, merge topics, retitle, or trim anything.

Parsing rules:
- Split level: the highest heading level (fewest `#`s) that occurs more than once — usually `##`. If that level yields more than 20 sections, split one level higher instead.
- A section = one split-level heading plus everything below it up to the next split-level heading, verbatim — subsections, lists, tables, code blocks, byte-for-byte.
- Content between the doc title and the first split-level heading becomes its own first section, titled with the H1 text (or "Preamble" if there is no H1). Omit it only if empty.
- No headings at all → one section containing the whole doc, titled with the filename.
- A section titled `Revision History` is the sign-off record from previous
  viva sessions, not content under review: omit it from `sections`, and
  exempt its lines from the integrity check.

For each section produce:
- `id`: assign sequentially (s1, s2, s3…) — fresh each extraction pass
- `title`: the heading text exactly as written (without the `#` markers)
- `content`: the full verbatim markdown between this heading and the next split-level heading

Do the split with a small script (e.g. python) rather than by hand — hand-copying silently drops blank lines and whitespace.

Integrity check before writing the JSON: every non-heading line of the source must appear in exactly one section's `content`, unchanged. If anything was dropped or reworded, redo the parse.

Edge cases:
- Doc too short to review (a few lines, no substantive content) → skip review, consider auto-approved

**3. Match approved IDs across rounds**

Before each round (round 2+), re-parse sections from the current doc state. IDs are assigned fresh. For previously approved sections, match by **title** (exact heading text, case-insensitive) to determine which new IDs correspond to already-approved content. Collect the full set of approved IDs (newly approved from the last round's output plus all previously accumulated IDs).

A section's `content` may have changed since it was approved only if the change was a rewrite the user requested elsewhere bleeding into it — if you rewrote text inside a previously approved section, drop it from `approved_ids` so the user re-reviews it.

**4. Write review-input JSON** to `.viva/review-input-r{N}.json`

Round 1 only: first clear stale round files from any previous session in this directory: `mkdir -p .viva && rm -f .viva/review-input-r*.json .viva/review-r*.json`

```json
{
  "mode": "review",
  "doc_file": "relative/path/to/file.md",
  "round": 1,
  "approved_ids": [],
  "sections": [
    {
      "id": "s1",
      "title": "Architecture",
      "content": "Full verbatim markdown of the section body — every line\nbetween this heading and the next, JSON-escaped."
    }
  ]
}
```

**Always include ALL sections** (approved and pending) in the `sections` array every round. `approved_ids` lists the IDs of sections already approved in previous rounds — the server pre-populates those cards as approved (collapsed, green) so the user has context but can still reopen them if needed.

**5. Launch or signal server**

Round 1 — launch in background and wait for it to write its URL:
```bash
VIVA_SERVER=$(find ~/.claude/skills/viva ~/.claude/plugins/cache -name "server.py" -path "*/viva*" 2>/dev/null | head -1)
mkdir -p .viva
python3 "$VIVA_SERVER" \
  --mode review \
  --input .viva/review-input-r1.json \
  --output .viva/review-r1.json &

until [ -f .viva/server.url ]; do sleep 0.5; done
BASE=$(cat .viva/server.url)
```
Server opens the browser tab on startup. `$BASE` is used for all subsequent API calls.

Round 2+ — after writing the new input JSON (step 4), signal the running server:
```bash
curl -s -X POST "$BASE/next-round?output=.viva/review-r{N}.json" \
  -H "Content-Type: application/json" \
  -d @.viva/review-input-r{N}.json
```
The browser updates in place — no new tab or subprocess.

**6. Wait for round output**

Poll for the output file at 2-second intervals:
```bash
until [ -f .viva/review-r{N}.json ]; do sleep 2; done
```
When it appears, read verdicts.

Verdicts:
| Verdict | Action |
|---------|--------|
| `approved` | Add to `approved_ids`; shown collapsed next round but can be reopened |
| `changes` | Rewrite the section in the doc using `note` as the instruction |
| `info` | Answer the question in `note`, rewrite the section incorporating that answer |
| `pending` | Carry forward unchanged; re-present next round |

**7. Rewrite** all `changes` and `info` sections directly in the source `.md` file. Preserve the section heading text exactly — title-based matching in the next round depends on it.

**8. Loop exit**

After step 7: if every section has `verdict: "approved"`, signal completion, then proceed to step 9 (append history) and step 10 (sign-off report):
```bash
curl -s -X POST "$BASE/complete" \
  -H "Content-Type: application/json" \
  -d "{\"rounds_total\": N, \"sections_total\": M, \"sections_revised\": K}"
```
Otherwise loop to step 2 (re-extract → match → write input → POST `/next-round`).

**9. Append revision history**

After signalling completion, append the session's decision record to the doc:

```bash
VIVA_HISTORY=$(find ~/.claude/skills/viva ~/.claude/plugins/cache -name "revision_history.py" -path "*/viva*" 2>/dev/null | head -1)
python3 "$VIVA_HISTORY" .viva <doc_file>
```

This appends `## Revision History` — a summary line plus a table of every
`changes`/`info` note, verbatim from the round files. If the heading already
exists (re-reviewed doc), the new session's block is appended under it.

**10. Sign-off report**

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
