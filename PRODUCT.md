# viva — Product Definition

Section-by-section markdown review for Claude Code. Named after the PhD oral
exam: the agent presents its work, the human drills every section, the agent
defends and revises, and the document passes only when all of it holds up.

## Thesis

A document an agent wrote is not done because the agent says it is. viva makes
a human the gate, section by section, and makes that gate cheap enough to use
every time. The unit of trust is the section, not the document: nothing passes
until a human has approved the section it lives in.

The product is the set of **human checkpoints across an agent's artifact
lifecycle** — today the review checkpoint (section-by-section doc review), a
brainstorm checkpoint (batch Q&A before the doc exists), a diff checkpoint
(hunk-by-hunk code review before a commit), and an intake checkpoint (the
residual interview before a doc is drafted). Every feature either is one of those
checkpoints or makes one cheaper to reach the right decision faster. A new
feature earns its place by serving a checkpoint; one that fits neither belongs to
a different product.

## Personas

1. **The agent author (primary, non-human).** Claude Code, having written a
   spec, ADR, runbook, or design doc. It needs to hand the doc to a human for
   sign-off without burning context: parse without reading the doc, wait
   without polling cost, rewrite only flagged sections, and learn what this
   reviewer always wants. The skill is tuned so the agent never makes the human
   wait on a tool round-trip and never loads the doc into context until a
   rewrite needs it.

2. **The reviewing human (primary).** A developer who must sign off on a doc an
   agent produced and refuses to rubber-stamp it. Wants to see each section
   verbatim, leave one or more typed comments per section (down to a line
   anchor), attach a screenshot, ask a question, keep a thread open across
   rounds, and have a verbatim revision ledger at the end. Reviews many docs, so
   wants recurring critiques learned rather than re-typed.

## Principles

1. **The section is the unit of trust.** Comment, request changes, or ask per
   section. The document passes only when every section is approved. A pass may
   require more before a round closes; it may never require less.
2. **Verbatim, not summarized.** Cards show section content byte-for-byte; the
   ledger records the human's notes verbatim. viva never paraphrases the human
   or the doc.
3. **Advisory, never gating.** Annotations, producers, confidence, learned
   preferences, and open notes all decorate or inform — the human alone decides
   a verdict. Nothing is auto-accepted.
4. **No-op when absent.** Every layer beyond the core loop degrades to exactly
   the prior behavior when its state file is missing. A plain review never pays
   for a feature it does not use.
5. **Cheap for the agent.** Default round 1 launches in one bash block with no
   doc read; the doc enters context only when a rewrite needs it. Performance is
   a product feature, not an implementation detail.
6. **Local and keyless.** A single stdlib-only Python server, one browser tab,
   no API key, no hosted service. The reviewer's data and learned preferences
   stay on their machine (preferences are gitignored, per-clone).

## What we are NOT building

- **Not a linter or CI gate.** Producers (checklist, drift, grounding) flag;
  they never fail a build or block sign-off. A human always decides.
- **Not autonomous review.** viva does not approve its own work. The human gate
  is the product; "nothing is auto-accepted" is a hard line.
- **Not multi-user or hosted.** No accounts, no shared server, no cloud sync.
  One reviewer, one local tab, one clone. Learned preferences are per-clone, not
  shared.
- **Not a general document editor.** viva reviews and signs off; it provides no
  free editing surface. A reviewer may supply exact replacement wording for a
  span they selected — a comment with a payload, applied by the author and
  recorded in the ledger — but there is no cursor in the document.
- **Not a writing assistant.** `/viva-write` drafts, but only within rails: the
  type's grammar fixes the sections, the register (`references/style.md`) fixes
  the density, the attachments fix the facts, the interview covers the residue,
  and the human gate decides. The checkpoints are the contribution, not the
  prose. The register is a rail because it says what a doc may not carry —
  provenance, preamble, filler — not how to make its case. Craft advice — how
  to write a good design doc — belongs in a different product; its appearance
  in a skill file is the tell that this one has drifted.
- **Not a heavyweight dependency.** stdlib-only server; no runtime packages.

## Surface

Two commands, split by **intent** — am I making a thing, or judging one — rather
than by mechanism:

- **`/viva-write`** — doc-first intake: a type plus attached context, a residual
  interview, a draft, and editorial rounds in the same tab.
- **`/viva-review`** — human sign-off, dispatching on the target: a `.md` path
  reviews by section, a PR number or git ref by hunk.

The Q&A gate is a documented contract (`references/qa.md`) any caller can drive,
not a third command. Naming the surface by mechanism — `/viva`, `/viva-qa`,
`/viva-diff` — made a reviewer learn viva's internals to find the checkpoint they
wanted.

## Feature map

The core loop (parse → review → rewrite → loop → sign off with ledger) plus
opt-in layers that all funnel through the section card:

- Section-by-section verdicts: approve / changes / info / pending
- Multiple typed comments per section (GitHub-style threads); the section
  verdict is derived from its active comments, never picked directly
- Verbatim Revision History ledger appended at sign-off
- Image attachments on note fields and per comment (review and Q&A)
- Line anchors — pin a comment to a specific line/phrase within a section
- Open notes — every comment is a thread that persists across rounds until
  settled
- Round-to-round section diff on rewritten cards
- Per-section annotations rendered as card badges (advisory)
- Pre-review producers (opt-in): checklist gating, spec↔code drift,
  claim grounding, cross-section contradiction
- Confidence triage — sourced/inferred · level, with weakest-first sort
- Learned preferences — recurring critiques learned across sessions
- Brainstorming Q&A — batch design questions before the spec is written
- Diff review: hunk-by-hunk review of agent-written code before commit, on a PR
  number, a git ref, or the working tree
- Doc-type bundles — section grammar, check set, and default pass depth per type
- Doc-first intake (/viva-write): a type plus attached context (repo paths,
  issue refs, files, URLs) starts the flow; the interview covers only what the
  attachments could not answer, and the draft reaches editorial rounds in the
  same tab without a second server launch

## Known problems

- **README trails the deeper layers.** It now covers both commands, intake, doc
  types, verdicts, and pass depth, but confidence triage and the producer
  contract are still documented only in `references/` and `CLAUDE.md`.
- **Stamps are prose, not bundle data.** A type bundle carries no `stamp` field,
  so `/viva-write`'s per-type consequence (commit vs. `gh pr edit`) lives in the
  skill's table rather than in the type it belongs to.

## Feature tracker

GitHub Issues at https://github.com/jacquardlabs/viva/issues. Individual
features and bugs are tracked there; this file holds intent, not a backlog.
