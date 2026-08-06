# Design: the editorial frame — author, editor, checks

Source: spike #165 (questions 1–4). Issues #166, #167, #168, #169; #105 absorbed
by #166. Milestone: Editorial Workspace. Prerequisite: #95.

## Problem & persona

> A developer who must sign off on a doc an agent produced and refuses to
> rubber-stamp it.

viva's stated identity is the PhD oral exam: examiner and candidate, one drilling
the other. The mechanics it actually ships are editorial — a section-by-section
pass over someone else's draft, typed comments anchored to spans, threads that
persist until settled, a verbatim ledger of what changed and why. The examiner
framing makes adversarial posture the tool's identity rather than one setting it
can take, and it gives no vocabulary at all for the three things the mechanics
still lack:

- **The reviewer cannot supply wording.** A note that says "this sentence is
  clunky" costs a full round: describe, wait, rewrite, verify. The reviewer
  usually knows the exact replacement and has nowhere to put it.
- **The agent complies with everything.** Every requested change is applied.
  An author that never pushes back makes reviewer errors uncatchable, and the
  reviewer learns to distrust an approval that was never contested.
- **Depth is fixed.** Every round is the same round. There is no way to say
  "structure only, ignore wording" or "final read, nothing structural."

Replacing examiner/candidate with **author / editor / checks** names all three.
The agent authors, the human edits, machines check. Adversarial is a posture a
pass can take, never the tool's identity.

The frame is rationale, not vocabulary: everything a user sees keeps the words a
developer already knows — suggested edit, declined, check, reviewer, round.
"Pencil mark" and "stet" name the same mechanics in a register this tool's
audience has no reason to learn.

## Proposed design

### Passes — depth and posture as round parameters

`ReviewInput` gains an optional `pass` object:

```json
{ "pass": { "kind": "line", "posture": "hard" } }
```

`kind` is one of `structure` (shape and substance), `line` (wording), `fact-check`
(claims against sources), `proof` (final read). `posture` is a setting on the
pass, not a separate axis — `normal` or `hard`, where hard licenses the agent to
argue rather than concede.

**Absent means today's behavior exactly.** A round with no `pass` parses, arms,
and completes precisely as it does now, satisfying PRODUCT.md principle 4
("no-op when absent"). Four pass kinds ship without breaking a single existing
caller.

**The conjunct-only invariant.** `schema.round_is_complete` may only *add*
conditions per pass; it may never relax the all-approved base:

| Pass | Completion |
|---|---|
| absent, `structure`, `line` | every section approved |
| `fact-check` | every section approved **and** every flagged claim carries a check result |
| `proof` | every section approved **and** zero unresolved suggested edits |

A pass that could return `true` where today's rule returns `false` would reopen
the hole #102 closed — `POST /complete` accepting a round the human never
approved. No pass may do this, now or later. The rule stays pure: dicts in, bool
out, no disk.

### Doc types — shipped defaults, repo override

A type is section grammar + check set + default pass. viva ships a starting set
inside the plugin; a repo adds or overrides by committing
`.viva-types/<name>.json`, and the repo's copy wins on a name collision. Types
are the intake menu now and the extension point later.

Minimal bundle:

```json
{
  "name": "design-doc",
  "title": "Design doc",
  "sections": ["Problem & persona", "Proposed design", "Out of scope"],
  "checks": ["headings-present", "claims-sourced"],
  "default_pass": "structure"
}
```

Resolution belongs in a new stateless filter, `scripts/doc_types.py`, importing
nothing but `schema` — it merges the shipped and repo bundles by name and prints
the result as JSON. It does **not** live in `.viva/`: that directory is cleared
at every `loop.py start`, and `preferences.json` is its single documented
survivor. A type bundle is committed, shared configuration.

`ReviewInput` records the resolved type name as an optional `doc_type` string,
carried round to round the way `split_on` is.

### Suggested edits and declines — designed here, built next

A **suggested edit** is a comment with a payload: `type: "suggestion"` alongside
today's `changes` and `info`, carrying `replacement` text and reusing the
existing `anchor: {text, offset}` selection. The agent applies it verbatim — no
rewrite pass, no interpretation — and the round diff confirms the exact change
landed. It needs no new annotation layer: `Annotation` is producer-authored,
advisory, and lives on the input side, while a suggestion is reviewer-authored,
binding, and belongs with the verdicts that already thread by `cid` and fold
into the ledger.

A **decline** is the author's answer: an `open-notes` thread status beside
`open` and `settled`, carrying grounds — a criterion, a prior ruling, a
measurement. `VERDICTS` does not change; the thread stays open, so the section
stays held, and the reviewer either accepts the decline or re-requests. A
decline with no grounds renders weaker than one with them.

Both are blocked on **#95**. `offsetInSource` returns `src.indexOf(text)`, the
first match, so an anchor on repeated text points at the wrong occurrence — and
`viva-diff/SKILL.md:74` already tells the agent that `anchor.offset`
"disambiguates repeated spans," which it cannot. Today that costs a highlight.
Applied to a verbatim replacement it is a wrong edit to the wrong span, and the
documented grep fallback carries the same ambiguity. Exact character-offset
identity is a precondition for suggestions, not a later polish.

### Prose amendments

Two lines in PRODUCT.md carry the frame; both are load-bearing and are quoted
here so the edit is reviewed, not assumed.

Principle 1 gains a sentence:

> **The section is the unit of trust.** Comment, request changes, or ask per
> section. The document passes only when every section is approved. A pass may
> require more before a round closes; it may never require less.

The editor fence is amended rather than dropped:

> **Not a general document editor.** viva reviews and signs off; it does not
> author from scratch or provide a free editing surface. A reviewer may supply
> exact replacement wording for a span they selected — a comment with a payload,
> applied by the author and recorded in the ledger — but there is no cursor in
> the document.

DESIGN.md needs no new visual language. Blueprint-on-vellum already renders the
three inks: agent-author text, the reviewer's typed marks, machine annotations.

## User journey

1. The agent finishes a design doc and starts a review with `--type design-doc`.
   `doc_types.py` resolves the bundle: the repo's `.viva-types/design-doc.json`
   overrides the shipped default. Round 1 runs the type's `default_pass`,
   `structure`.
2. The reviewer works the cards as they do today. Structure round: three
   sections get `changes`, the rest approved. Completion is the all-approved
   base, unchanged.
3. Round 2 is a `line` pass. The reviewer selects a clumsy clause and types the
   replacement instead of describing it. The agent applies the wording verbatim;
   the round diff shows exactly that span changed.
4. One suggestion the agent declines, with grounds: the phrasing the reviewer
   proposed contradicts a decision recorded in round 1. The thread stays open,
   so the section stays held. The reviewer reads the grounds and insists; the
   agent applies it.
5. A `fact-check` pass runs the type's checks. Two claims come back without
   sources, so the round cannot close on approvals alone — the added conjunct
   holds it until both carry a result.
6. Sign-off. The ledger records the suggestions verbatim, the decline and its
   grounds, and the check results.

## Contract impact — spike question 4

| Change | Bumps? | Why |
|---|---|---|
| `pass` on `ReviewInput` | **yes** | The field is optional and absent means today, but when present it changes when `POST /complete` succeeds — observable behavior, the same reasoning that bumped v3 for the round gate rather than for `/abandon`. |
| `doc_type` on `ReviewInput` | no | Optional field, passthrough only (§1). |
| `suggestion` comment type | **yes** | A new wire value a caller must interpret to apply the round. |
| `declined` thread status | **yes** | Same — a caller reading threads must handle it. |
| Type bundle files | no | Repo configuration, never on the wire. |

Passes and mechanisms ship in separate phases, so this is two bumps: version 4
for passes, version 5 for suggestions and declines.

## Sequencing

Frame first, mechanisms second. Phase 1 settles identity in PRODUCT.md, adds
`pass` and the conjunct-only rule to `schema.py` and `server.py`, and lands doc
types with enough of a check set for `fact-check` to mean something. Phase 2
builds suggested edits and declines on top of settled vocabulary and a fixed
#95.

Building mechanisms first would ship a wire format callers integrate against,
then change the completion rule underneath them — two bumps in the opposite,
more disruptive order.

## Out of scope

- **Doc-first intake (#170) and the handoff bundle (#171).** Template-plus-context
  intake and the DS-to-engineering transfer are the phase after this one.
- **Stamp consequences.** A type's bundle carries no stamp semantics yet;
  dispatching a build or opening a PR at sign-off is phase 2.
- **Role inversion.** The grammar does not care which species holds which pen,
  but seating the agent as editor over a human-authored doc is not built here.
- **The check catalog.** This design places checks in the type bundle and makes
  `fact-check` completion depend on their results. Phase 1 ships the bundle
  format and enough of a check to exercise the added conjunct; which checks each
  type carries, and how the executable ones run, belongs to #169.
- **Multi-user.** Unchanged fence: one reviewer, one local tab, one clone.

## Alternatives considered

**A new annotation layer for suggested edits.** Rejected: `Annotation` is
input-side, producer-authored, and advisory — three properties a binding
reviewer edit does not share. Reusing the comment layer inherits threading,
carry-forward, ledger folding, and the anchor machinery already built.

**Decline as a new verdict.** Rejected: `VERDICTS` is the section's state, and a
decline is a turn in one thread on that section. A section with two suggestions,
one applied and one declined, has no coherent section-level verdict. The
open-notes store already models per-`cid` threads with a status.

**Posture as its own round field.** Rejected in favor of a pass setting. An
orthogonal posture axis multiplies four kinds into eight round shapes, and the
pairing that matters — a hard structural pass, a gentle proof — is expressible
as a setting the type's defaults can carry.

**In-doc frontmatter instead of type bundles.** Rejected: the definition would
be copied into every doc rather than shared, so a template change cannot
propagate, and the check set would be re-declared per file.

## Open questions

1. **Does a `proof` pass need a card surface that suppresses structural
   affordances?** The completion rule holds without one, but a reviewer offered
   the same card in every pass may not feel the difference.
2. **What is the shipped type set?** #169 names ten candidates; the starting set
   should be the ones this repo actually produces — design doc, plan, PR
   description, README, progress note.
3. **Do learned preferences extract from an applied suggestion?** An accepted
   edit is a critique with an exact fix attached, which is a stronger training
   signal than a note. `preferences.py` clusters notes today.
