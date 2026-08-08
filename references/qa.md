# Batch Q&A — the human gate as a primitive

`server.py --mode qa` presents structured questions in the browser and writes the
answers back. `/viva-write` step 3 is its main caller (the residual interview);
this file is the contract for any caller that needs a batch gate directly.

Read this when you need questions answered before you can act — not to review
something, which is `/viva-review`.

## Input

The caller writes `.viva/qa-input.json` before launching:

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
non-matching value at startup. When set, the matching chip renders with a small
"recommended" badge. Advisory only: the chip is never pre-selected, defaulted, or
required, and a question that omits the field renders exactly as it always has.
Use it for a fork question where the calling agent has a genuine recommendation
and a reason — the reason itself belongs in `hint` or the choice text, not in
this field.

## Output

`.viva/answers.json`, written by the server after the human submits:

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

**A note alone is an answer.** A question with no `choices` can only be answered
in free text, so its entry comes back with `choice: ""` and a populated `note`.
Any non-empty response — a chip, a note, or both — counts as answered, and every
answered question appears in `answers.json`. Do not treat an empty `choice` as an
unanswered question; read `note` before deciding a question was skipped.

`submitted_early: true` means the human hit *skip rest & submit*. The unanswered
questions are exactly the decisions a caller would otherwise fill by guessing;
decide what your flow does about that rather than reading past it.

## Launch and wait

```bash
mkdir -p .viva
rm -f .viva/answers.json

python3 "$VIVA_DIR/server.py" --mode qa \
  --input .viva/qa-input.json --output .viva/answers.json &
for i in $(seq 1 100); do [ -f .viva/server.url ] && break; sleep 0.1; done
[ -f .viva/server.url ] || { echo "qa: server start failed"; exit 1; }

until [ -f .viva/answers.json ]; do sleep 0.3; done
cat .viva/answers.json
```

Issue the wait with a generous timeout (~10 min / 600000ms) — it is human time,
not computation.

## Finish — standalone callers only

Signal completion so the server's 2-second shutdown timer starts and the process
exits:

```bash
BASE=$(cat .viva/server.url)
curl -s -X POST "$BASE/complete" -H "Content-Type: application/json" \
  -d "{\"questions_total\": N, \"questions_answered\": M}"
```

Without this call the server process (and its `.viva/server.url`) is never torn
down — it leaks until something kills it by hand.

## Handing off to a review session in the same tab (#109)

A caller that turns the answers into review sections does **not** tear this
server down and launch a second one. The `.viva/server.url` above is still live:
POST a round-1 review payload to it and the same browser tab reflows in place
from Q&A cards to section-review cards, round 1.

**Skip the finish step above when handing off.** `/complete` right after reading
`answers.json` would tear the process down out from under the review round about
to start; the review round's own `/complete`, at its eventual finish, ends the
process instead.

```bash
BASE=$(cat .viva/server.url)
python3 -c "import json; d=json.load(open('.viva/review-input-r1.json')); d['output']='.viva/review-r1.json'; print(json.dumps(d))" \
  | curl -s -X POST "$BASE/next-round" -H "Content-Type: application/json" --data-binary @-
```

`review-input-r1.json` is the ordinary `ReviewInput` shape `parse_sections.py`
produces — nothing about its schema changes for a qa-originated round. From
there the review proceeds exactly as `/viva-review` branch A's loop, and
`loop.py` can drive it: `wait`, `rearm`, `finish`.

**`output` must be a path distinct from the `--output` this server was launched
with** (`.viva/answers.json`) — e.g. `.viva/review-r1.json`. `/next-round` and a
review round's `/submit` both write to whatever `output` names; reusing the qa
output path lets the first review `/submit` silently overwrite the answers just
read.

The server has no field marking a round as qa-originated — `ReviewInput`'s shape
is unchanged by this hand-off. A server started `--mode qa` prints a distinct
`viva · hand-off qa → review · <url>` stdout line the first time it receives a
`sections`-shaped `/next-round`, which is the signal a terminal-watching caller
has for "this is that session type." `docs/headless-contract.md` §7 is the full
account.
