# Producers and annotations

Read this when `loop.py` stops after parsing and names this file: a producer has
to run before the round is armed. `loop.py start` stops on its own when the
preferences store holds a standing preference; `loop.py rearm --parse-only` is
how you open the same seam on round 2+. Either way the sequence is
**parse → produce → `loop.py annotate --sidecar <path>` → `loop.py arm`**, and
that order is load-bearing — the server reads the round file once, when it is
armed.

## Annotations (advisory)

Each section in the round's review-input may carry an `annotations` array. The
server renders each entry as a color-coded badge at the top of that section's
card, so the reviewer sees a flagged weak spot *before* choosing a verdict.

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

- `kind` *(required)* — short producer tag shown as the badge label (e.g.
  `grounding`, `drift`, `checklist`).
- `severity` *(required)* ∈ `info | warn | error` → color slot
  `teal | violet | orange`. Any other value renders as `info`.
- `message` *(required)* — the inline text shown beside the badge.
- `anchor` *(optional)* — a **string**: surfaced as the badge's hover title, or,
  for a contradiction flag, another section's id rendered as a jump link. Not
  the same field as a **comment's** `anchor`, which is a
  `{text, offset, occurrence?}` object naming the reviewer's selection. Same
  name, two shapes, two schemas.

Annotations never gate a verdict — the human still decides. A round with no
`annotations` renders exactly as before.

## The producer contract

A producer is a pre-review pass that writes annotations into the round's
review-input after the parse and before the arm. Producers are **opt-in**: the
default loop runs none of them, so an unflagged review behaves as today. Run one
when the user asks for that check ("ground the claims", "check for
contradictions"), when the doc type warrants it, or when `loop.py` names the
learned-preference producer. To run one at **round 1**, start the session with
`loop.py start --doc <path> --parse-only` — it stops at the seam the same way a
standing preference does, so the flags are merged before the reviewer ever sees
the round. Round 2+ is `loop.py rearm --parse-only`. The LLM passes below read the whole doc; that is
the one time the no-read fast path is traded away.

Every producer emits a sidecar list of `{id, kind, severity, message, anchor?}`
flags. Write it to `.viva/producer.json` — no round number, the driver supplies
that — and merge it:

```bash
python3 "$VIVA_DIR/scripts/loop.py" annotate --sidecar .viva/producer.json
```

A mechanical producer can pipe straight through, since `--sidecar -` reads
stdin. Its `--input` is the round file whose path `loop.py` printed when it
stopped — never a path you compute:

```bash
python3 "$VIVA_DIR/scripts/drift.py" --input <the round file loop.py printed> \
  | python3 "$VIVA_DIR/scripts/loop.py" annotate --sidecar -
```

The merge is additive (carried-forward flags survive), idempotent, and a no-op
on an empty sidecar. It keeps only `kind`/`severity`/`message`/`anchor`, plus
confidence's `basis`/`level` and a check's `result`.

A **check** producer's flag may carry `result` — what the check found for it. On
a `checks` round the flag holds the round open until it does, so re-emitting
the same flag with a `result` is how a check answers one: the merge writes the
result onto the flag already there instead of appending a twin beside it.

A new check producer must add its `kind` to `schema.CHECK_KINDS`. That registry
fails **open**: an unregistered kind is invisible to `round_is_complete`, so its
flags gate nothing and a `checks` round closes where it should have held.

Answer flags in the round you are about to arm, never in the one already armed —
the server loads its round once and replaces it only from `/next-round`, so
`loop.py annotate` refuses a round the server is currently serving.

Flag **new or changed** sections only: the parser carries a prior annotation
forward for any section whose title and content are byte-identical, and drops it
from a rewritten section, so a round-2+ producer looks only at sections without
carried flags. Each flag's `id` is the target section's id from the id→title map
`loop.py wait` printed.

### Mechanical producers (bundled scripts)

| Producer | Script | Flags |
|----------|--------|-------|
| **Checklist gating** | `checklist.py --input IN [--type spec\|adr\|runbook]` | `error` per required section missing for the doc's type. Type is inferred from the filename/H1 when `--type` is omitted; an untyped doc emits nothing. Missing-section flags land on the **first** card — the integrity check forbids a card for a section that isn't in the doc. |
| **Spec↔code drift** | `drift.py --input IN [--root .]` | `error` for a referenced file path that doesn't exist; `warn` for a simple `` `name()` `` symbol with no definition anywhere in the code. Prose-only sections emit nothing. |

### Judgment producers (LLM passes)

These need reading and reasoning, so you run them yourself: analyze the doc,
write the sidecar, merge it.

- **Claim grounding** — extract each section's checkable assertions (counts,
  signatures, file paths, behaviors), verify each against the repo, and emit a
  `warn`/`error` per unsupported or contradicted claim: the offending sentence
  plus what the repo actually shows (`file:line`, or "no match found").
  Extraction is read-only — never rewrite the section.
- **Cross-section contradiction** — compare sections pairwise for incompatible
  statements (a §3 non-goal that §7 specifies; two sections with conflicting
  defaults). Emit a `warn` on **both** sides, each flag's `anchor` set to the
  *other* section's id.
- **Spec↔code signature drift** — the half `drift.py` skips: compare a described
  function/endpoint **signature** against the actual code and `warn` on a
  mismatch. Regex can't do this without false drift.
- **Learned preferences** — the one producer that auto-engages. For each
  standing preference, read every new/changed section and emit a
  `kind: "preference"` `warn` where the section repeats that learned critique,
  naming what to change. Encode the preference id in the message (e.g.
  `[cite-sources] "80% faster" has no source`) — the merge keeps only
  `kind`/`severity`/`message`/`anchor`, so the id has to ride in the text for
  the human to trace the flag back. See `preferences.md`.
- **Pre-flight pre-fix** (#107) — runs before or alongside Learned preferences,
  never in place of it. For each standing preference, read every new/changed
  section and, where the preference's guidance clearly implies a concrete
  textual fix — not just "this critique applies here" but "here specifically
  is the fix" — emit a `kind: "preference"` `warn` whose message names the fix
  itself, not only the critique. Same id-encoding convention as Learned
  preferences: `[cite-sources] add a citation after "80% faster", e.g. "(see
  bench.md)"`. Nothing is auto-applied — this producer only ever emits a
  suggested pre-fix as a visible annotation; the human sees it in the margin
  like any other flag and decides whether to take it, same as every other
  annotation in viva. When the preference doesn't clearly imply a specific
  fix, this producer stays silent on that section and leaves the critique to
  Learned preferences alone — it never guesses.

## Confidence triage (sourced vs inferred)

When you generate or revise a doc, self-annotate each section with a
**confidence** annotation so the reviewer's attention lands where you are
weakest:

```json
{ "kind": "confidence", "severity": "warn", "basis": "inferred", "level": "low", "message": "inferred · low" }
```

- `basis` — `sourced` (drawn from the repo, the user's input, or a cited fact)
  or `inferred` (your own guess or extrapolation).
- `level` — `high | medium | low` confidence in the section's correctness.
- Mirror the weakness in `severity` (`error`/`warn` for low/inferred, `info` for
  high/sourced) so the badge color tracks it; keep `message` a short label.

Unlike the producers above, confidence is the generating agent's own
self-annotation, emitted at write time. Route it through `loop.py annotate` like
any other sidecar: `annotate.py`'s merge preserves `basis`/`level` (issue #40),
so the sort's fields survive it. Under the driver this is the only route — the
alternative, editing the round file's `annotations` array in place, needs a path
you are not meant to compute. The server reads
`basis`/`level` directly — never the message — to offer a **weakest-first** sort
toggle; document order stays the default. A section with no confidence
annotation keeps document order, and a doc with none hides the toggle entirely.
