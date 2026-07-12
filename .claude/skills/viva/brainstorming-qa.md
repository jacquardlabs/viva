---
name: viva-qa
description: Batch Q&A human gate. Present structured questions in the browser and collect answers. Write .viva/qa-input.json (QAInput shape) before invoking.
---

# viva-qa

Batch Q&A human gate. Write the questions to `.viva/qa-input.json`, invoke
`/viva-qa`, and read the answers from `.viva/answers.json`.

## Input contract

The caller writes `.viva/qa-input.json` before invoking this skill:

```json
{
  "mode": "qa",
  "context": "One-sentence description shown in the title block",
  "questions": [
    {
      "id": "q1",
      "text": "The question text",
      "hint": "Optional elaboration shown below the question",
      "choices": ["Choice A", "Choice B", "Choice C"],
      "recommended_choice": "Choice A"
    }
  ]
}
```

`choices` is optional — omitting it renders a free-text field only.

`recommended_choice` is optional and must exactly match one entry in that
question's own `choices` (by value, not index) — the server rejects a
non-matching value at startup. When set, the matching chip renders with a
small "recommended" badge. This is advisory only: the chip is never
pre-selected, defaulted, or required, and a question that omits the field
renders exactly as it always has. Use it for a fork question where the
calling agent has a genuine recommendation and a reason — the reason itself
still belongs in `hint` or the choice text, not in this field.

## Output contract

`.viva/answers.json` written by the server after the human submits:

```json
{
  "answers": [
    {"id": "q1", "choice": "Choice A", "note": "", "attachments": []}
  ],
  "submitted_early": false
}
```

If an answer carries an `attachments` array, `Read` each listed image path before
incorporating that answer — the image is context for how you use the answer.

## Steps

**1. Resolve skill dir and guard**

```bash
VIVA_DIR=~/.claude/skills/viva
[ -f "$VIVA_DIR/server.py" ] || \
  VIVA_DIR=$(find ~/.claude/plugins/cache -name "server.py" -path "*/viva*" -maxdepth 6 2>/dev/null \
             | xargs -I{} dirname {} | head -1)
[ -f "$VIVA_DIR/server.py" ] || { echo "viva-qa: cannot locate server.py"; exit 1; }

[ -f .viva/server.url ] && { echo "viva-qa: a prior session may still be running (.viva/server.url exists). Delete it if the server is stopped."; exit 1; }
[ -f .viva/qa-input.json ] || { echo "viva-qa: .viva/qa-input.json not found — write it before invoking /viva-qa"; exit 1; }
```

**2. Launch and wait for server**

```bash
mkdir -p .viva
rm -f .viva/answers.json

python3 "$VIVA_DIR/server.py" --mode qa \
  --input .viva/qa-input.json --output .viva/answers.json &
for i in $(seq 1 100); do [ -f .viva/server.url ] && break; sleep 0.1; done
[ -f .viva/server.url ] || { echo "viva-qa: server start failed"; exit 1; }
```

**3. Wait for answers**

```bash
until [ -f .viva/answers.json ]; do sleep 0.3; done
cat .viva/answers.json
```

Read `.viva/answers.json`. For each answer with an `attachments` field, `Read`
each listed path before acting on the answer.

**4. Finish** (standalone Q&A only — skip this step entirely when handing off
to a review session, see below)

Signal completion so the server's 2-second shutdown timer starts and the
process exits — mirroring the pattern `SKILL.md`'s own finish step uses:

```bash
BASE=$(cat .viva/server.url)
curl -s -X POST "$BASE/complete" -H "Content-Type: application/json" \
  -d "{\"questions_total\": N, \"questions_answered\": M}"
```

Without this call the server process (and its `.viva/server.url` file) is
never torn down — it leaks indefinitely until something kills it by hand.

## Hand off to a review session in the same tab (#109)

A caller that turns the answers into review sections (e.g. jig's `/design`
skill drafting sections from the interview before handing them to a human for
sign-off) does not need to tear this server down and launch a second one. The
`.viva/server.url` this skill wrote is still live — POST a round-1 review
payload to it and the same browser tab reflows in place from Q&A cards to
section-review cards, round 1. This is **opt-in**: a caller that never does
this instead follows step 4 above and calls `/complete` when the standalone
Q&A finishes.

**Skip step 4's `/complete` call when handing off.** The hand-off reuses this
same server process — calling `/complete` right after reading
`answers.json` would tear the process down out from under the review round
about to start. The review round's own `/complete`, called at *its* eventual
finish (`SKILL.md`'s step 5 pattern, reused verbatim by whatever caller
drives the post-hand-off review round), ends the process instead.

```bash
BASE=$(cat .viva/server.url)
python3 -c "import json; d=json.load(open('.viva/review-input-r1.json')); d['output']='.viva/review-r1.json'; print(json.dumps(d))" \
  | curl -s -X POST "$BASE/next-round" -H "Content-Type: application/json" --data-binary @-
```

`review-input-r1.json` is the ordinary `ReviewInput` shape `parse_sections.py`
produces (`{"mode": "review", "round": 1, "doc_file": ..., "sections": [...]}`)
— nothing about its schema changes for a qa-originated round. From here the
review proceeds exactly as `/viva`'s own loop: wait for `.viva/review-r1.json`,
act on verdicts, `/next-round` for round 2+, `/complete` to finish.

**`output` must be a path distinct from this skill's `--output`
(`.viva/answers.json`)** — e.g. `.viva/review-r1.json`, not
`.viva/answers.json`. `/next-round` and a review round's `/submit` both write
to whatever `output` names; reusing the qa output path lets the first review
`/submit` silently overwrite the answers this step just read.

The server has no field marking a round as qa-originated — `ReviewInput`'s
shape is unchanged by this hand-off. A server started `--mode qa` prints a
distinct `viva · hand-off qa → review · <url>` stdout line the first time it
receives a `sections`-shaped `/next-round` payload, which is the signal a
terminal-watching caller (or a headless-contract consumer inspecting the
process, not the payload) has for "this is that session type."
