---
name: viva-diff
description: Hunk-by-hunk code review. Human approves or requests changes per diff hunk; agent revises working-tree files and loops until all hunks are approved.
---

# viva-diff

Hunk-by-hunk code review. The unit of trust is the hunk: nothing in the diff
is considered done until a human has approved the hunk it lives in.

Named after the same discipline as `/viva` — the agent presents its changes,
the human drills every hunk, the agent defends and revises, and the diff only
passes when all of it holds up.

## Invocation

```
/viva-diff [ref]
```

`ref` is a git ref or range (`HEAD~1`, `HEAD~3..HEAD`, `main`). Omit for
unstaged working-tree changes (`git diff`). For staged-only changes use
`git diff --cached` inside the launch block.

## Steps

**1. Capture diff and launch** (round 1 — one bash block)

```bash
VIVA_DIR=~/.claude/skills/viva
[ -f "$VIVA_DIR/server.py" ] || \
  VIVA_DIR=$(find ~/.claude/plugins/cache -name "server.py" -path "*/viva*" -maxdepth 6 2>/dev/null \
             | xargs -I{} dirname {} | head -1)
[ -f "$VIVA_DIR/server.py" ] || { echo "viva-diff: cannot locate server.py"; exit 1; }

[ -f .viva/server.url ] && { echo "viva-diff: a prior session may still be running (.viva/server.url exists)"; exit 1; }

mkdir -p .viva
rm -f .viva/server.url .viva/review-input-r*.json .viva/review-r*.json
rm -rf .viva/attachments

# Capture the diff. Use the caller-supplied ref if provided.
git diff <ref> > .viva/diff.patch 2>/dev/null
# If ref is empty (unstaged): git diff > .viva/diff.patch
[ -s .viva/diff.patch ] || { echo "viva-diff: no changes to review"; exit 0; }

DOC_FILE="<ref or 'working tree'>"

python3 "$VIVA_DIR/scripts/parse_diff.py" .viva/diff.patch \
  --output .viva/review-input-r1.json --round 1 --doc-file "$DOC_FILE" \
&& {
  python3 "$VIVA_DIR/server.py" --mode diff \
    --input .viva/review-input-r1.json --output .viva/review-r1.json &
  for i in $(seq 1 100); do [ -f .viva/server.url ] && break; sleep 0.1; done
}
[ -f .viva/server.url ] || { echo "viva-diff: launch failed"; exit 1; }
BASE=$(cat .viva/server.url)
```

**2. Wait for verdicts** (every round)

```bash
until [ -f .viva/review-r{N}.json ]; do sleep 0.3; done
cat .viva/review-r{N}.json
```

Read all verdicts from stdout. The server writes the file atomically — `cat`
always sees a complete JSON.

**3. Act on verdicts**

| Verdict | Action |
|---------|--------|
| `approved` | Hunk accepted. Carries forward collapsed in next round. |
| `changes` | Apply the comment's edit to the target source file. The comment's `anchor.text` identifies the exact span; `anchor.offset` disambiguates repeated spans. Scope the edit to the hunk named in the section title (`{filepath} hunk N`). |
| `info` | Answer in the thread only. Do not edit the file until the discussion escalates to a `changes` turn (the reviewer switches chip to "request changes"). |
| `pending` | Re-present unchanged next round. |

For each section with a `changes` comment:
- Parse the section `title` to extract the filepath (`title` = `"{filepath} hunk N"`)
- Apply the targeted edit to `{filepath}` in the working tree
- Use `anchor.text` + `anchor.offset` to locate the exact span within the hunk

**Every section approved** → go to step 5.
**Any `changes`/`info`** → re-diff, re-parse, re-arm (step 4).

**4. Re-diff and re-arm**

```bash
git diff <ref> > .viva/diff.patch 2>/dev/null
[ -s .viva/diff.patch ] || { echo "viva-diff: diff is now empty — all changes may have been fully applied"; exit 0; }

python3 "$VIVA_DIR/scripts/parse_diff.py" .viva/diff.patch \
  --output .viva/review-input-r{N+1}.json --round {N+1} --doc-file "$DOC_FILE" \
  --prior-input .viva/review-input-r{N}.json \
  --prior-verdicts .viva/review-r{N}.json \
&& curl -s -X POST "$BASE/next-round?output=.viva/review-r{N+1}.json" \
     -H "Content-Type: application/json" -d @.viva/review-input-r{N+1}.json
```

The browser updates in place — no new tab. Loop to step 2.

**5. Finish** (all sections approved)

```bash
curl -s -X POST "$BASE/complete" -H "Content-Type: application/json" \
  -d "{\"rounds_total\": N, \"sections_total\": M, \"sections_revised\": K}"
```

Give the sign-off report:

> "N hunks approved across M files in R round(s). K hunks revised."

Then ask: "Commit these changes? (y/n)"

If yes, stage and commit the reviewed working-tree changes:
```bash
git add <files touched>
git commit -m "<message>"
```

## Scope

`/viva-diff` is a **human gate**, not an LLM reviewer. It composes with
`/code-review` (which is an LLM pass): run `/code-review` first to apply
automated suggestions, then `/viva-diff` for human sign-off before committing.
