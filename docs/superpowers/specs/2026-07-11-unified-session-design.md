# Design: Unified Q&A → Review Session (#109)

**Date:** 2026-07-11
**Issue:** [#109](https://github.com/jacquardlabs/viva/issues/109) — "Unified session type: Q&A → section review in one browser session"
**Epic:** jig-integration — stabilize viva's headless protocol and extend review surfaces for jig integration
**Status:** Draft — pending `/gate-design-review`

---

## Problem & persona

PRODUCT.md names the agent author as viva's first persona:

> **The agent author (primary, non-human).** Claude Code, having written a
> spec, ADR, runbook, or design doc. It needs to hand the doc to a human for
> sign-off without burning context: parse without reading the doc, wait
> without polling cost, rewrite only flagged sections, and learn what this
> reviewer always wants.

jig's `/design` skill is exactly this persona, extended one step earlier: before there's a doc to hand off, `/design` interviews the human with a batch of questions (viva's existing `qa` mode) and only then drafts the sections that go through the existing section-by-section gate (`review` mode). Today those are two separate `server.py` processes, two `.viva/server.url` files, two browser tabs — the human finishes answering questions, the tab goes idle, and moments later a second tab opens with the sections that resulted. Issue #109 names the seam directly:

> Request: a session type that opens as Q&A cards and transitions to
> section-review cards in the same tab, same server lifecycle. Removes the
> seam in /design's interview → defend-the-doc steps.

Nothing in the human reviewer's mental model changes between "answering questions about this doc" and "reviewing the sections that resulted from my answers" — it is one sitting, on one document, with one reviewer. The mechanics currently force an unnecessary context switch (find the new tab, notice the old one is dead) that the product's own principle of being "cheap for the agent" and cheap for the human doesn't extend to.

## Proposed design

viva gains a documented hand-off between two capabilities it already has, not a new review surface. A caller opens a session in `qa` mode exactly as `/viva-qa` does today — same input file, same launch, same browser experience for the human answering questions. When the human submits, the calling agent (jig) does its own synthesis work — turning the answers into round-1 review sections, exactly the same shape `parse_sections.py` produces for a standalone `/viva` launch — and hands that round-1 payload to the **same running server** instead of tearing it down and launching a second one.

In the browser, this is felt as a single continuous session: the human answers questions, sees a brief "processing" beat, and the same tab reflows in place into section-review cards, round 1, ready to comment, approve, or request changes exactly like any other viva round. Same tab, same address bar, same `server.url` throughout. Round 2+ (if the human requests changes) then proceeds exactly as the existing review loop — nothing about post-round-1 behavior changes.

This does not require teaching `server.py` a new operating mode. The running server already accepts an arbitrary round payload on `/next-round` and broadcasts it to the open tab over the existing SSE connection — that is the exact mechanism `/viva-diff` already uses to advance rounds in place without opening a new tab (SKILL.md and `diff.md` both document this pattern today). The genuinely new work is on the browser side: today, a round payload arriving over `/next-round` assumes the tab is already showing review chrome. A tab currently showing Q&A cards that received one would end up layering section-review cards underneath the still-visible Q&A view instead of cleanly replacing it — that gap is what this story closes, so the hand-off reads as one deliberate transition rather than an accident of two views overlapping.

**Standalone `/viva-qa` and `/viva` are unchanged.** The hand-off is entirely caller-opt-in: nothing about launching `server.py --mode qa` or `--mode review` changes, and a caller that never follows a Q&A round with a review-shaped `/next-round` POST sees behavior byte-identical to today. This is PRODUCT.md's fourth principle applied literally:

> **No-op when absent.** Every layer beyond the core loop degrades to exactly
> the prior behavior when its state file is missing. A plain review never pays
> for a feature it does not use.

The design also leans on principle 6, "local and keyless... a single stdlib-only Python server, one browser tab" — today a two-phase caller like jig needs two tabs to get through one interview-then-review sitting, which is a crack in that promise the moment a caller needs both phases. This story makes "one browser tab" true again for that caller.

## User journey

1. jig's `/design` skill has drafted candidate questions about a doc it's about to produce (an ADR, a plan, a spec) and wants the human's input before drafting.
2. jig opens the session: writes `.viva/qa-input.json` (unchanged `QAInput` shape) and launches — same contract as `/viva-qa` today. The browser opens showing Q&A cards.
3. The human answers questions. Identical experience to a standalone Q&A session — nothing in this story changes what the human sees at this step.
4. The human submits. jig reads `.viva/answers.json` (unchanged `QAOutput` shape) and — outside viva, this is jig's own authoring work — drafts the document sections the answers imply.
5. Instead of tearing down the qa server and launching a fresh `/viva`, jig hands the round-1 review payload to the session already running.
6. In the browser — same tab, no reload, no new URL — the view reflows from Q&A cards to section-review cards, round 1, round badge "REV 01". The human is looking at sections that came directly out of the answers they just gave, ready to review exactly as any `/viva` round: approve, request changes, comment, ask a question, attach a screenshot.
7. The human drives the review to completion exactly as the existing core loop (parse → review → rewrite → loop → sign off with ledger) — round 2+, the verbatim revision ledger, sign-off — none of that changes; this story only affects how round 1 of the review half is reached.
8. jig's `/design` skill now has both artifacts it needed — settled answers and a human-approved document — from one browser sitting instead of two.

This composes two capabilities PRODUCT.md's feature map already names as separate lines — "Brainstorming Q&A — batch design questions before the spec is written" and the core review loop — into the single sitting a caller like jig actually needs. It does not introduce a third capability.

## Out of scope

- **jig's synthesis logic.** Turning Q&A answers into review-input sections is the calling agent's authoring work, unchanged from today's split-process pattern. viva gates and transports; it does not draft.
- **Retiring the brainstorming-integration `install.sh` monkey-patch.** The acceptance criteria explicitly calls this out of scope for this story — this work unblocks that retirement, it doesn't perform it.
- **Any other mode pair.** Only the qa → review direction is in scope, matching issue #109 and the acceptance criteria. Transitioning into `diff` mode, or chaining more than one hand-off (qa → review → qa again), is not addressed here.
- **Schema changes.** `QAInput`, `QAOutput`, and `ReviewInput` in `schema.py` are unchanged by this story — the hand-off reuses the shapes that already exist for standalone qa and review launches.
- **Any behavior change to standalone `/viva-qa` or `/viva`.** The acceptance criteria requires these remain no-ops when the new capability isn't used; this design does not touch their existing contracts.

## Alternatives considered

1. **Two server processes with a client-side redirect.** Keep launching a second `server.py --mode review` process as today, but have the first tab auto-redirect to the second server's URL once it's up. Rejected: this still launches a second `server.py` process, which directly fails the acceptance criteria, and a mid-session redirect is a worse experience than an in-place reflow — a visible reload where the proposed design has none.

2. **One upfront payload with both questions and section placeholders.** Have the caller declare the review sections before the Q&A round even runs, so a single launch carries everything and no hand-off is needed. Rejected: this is a chicken-and-egg problem — the whole point of an interview-first flow is that the section content doesn't exist until the answers produce it. Forcing the caller to fabricate placeholder sections before asking anything either means a throwaway draft nobody wanted, or means the caller has already authored the doc before interviewing the human, which defeats the point of asking first and works against PRODUCT.md's promise that the agent doesn't pay a doc-read cost until a rewrite needs it.

3. **A new polling contract instead of the existing SSE broadcast.** Have the browser poll a new endpoint to detect that round 1 of review has arrived, rather than reuse the `/events` SSE connection and `round` event that diff mode's round-advance already broadcasts on the same server. Rejected: `/viva-diff` already proved `/next-round` + SSE for in-place round advances without a new tab. A second signaling path for the identical purpose is the kind of structural drift the project's coding philosophy warns against — reuse over creation.

## Operational readiness

- **Migration:** none. No on-disk schema changes, no new persistent state file. `.viva/*` round files are already disposable and session-scoped; this story adds nothing that needs to survive a session restart.
- **Rollback:** the hand-off is caller-opt-in — a caller must deliberately POST a review round-1 payload to a still-running qa server instead of calling `/complete`. If it misbehaves, a caller reverts to today's two-launch pattern; there is no server-side flag to unset because standalone `/viva-qa` and `/viva` code paths are untouched.
- **Rollout:** ships behind no flag — it is a capability a caller must actively use (writing a follow-up round-1 payload and POSTing it to the running server) rather than a change to any existing default path. Existing callers (SKILL.md's own review loop, `/viva-diff`, `/viva-qa`) are unaffected until they're rewritten to opt in.
- **Observability:** unchanged from today — `server.py`'s existing stdout line (`viva · {mode} mode · {url}`) is what a terminal-watching agent or human already inspects for every mode; the transition point gets its own line so the hand-off is visible in that same stream. There are no metrics, logs, or alarms beyond that: PRODUCT.md is explicit that viva is "local and keyless... no hosted service," so there is no fleet to monitor, only a local subprocess whose stdout and exit code are already the whole observability surface for every mode.
- **Failure mode:** if the caller's synthesis step fails after Q&A completes (can't produce review sections from the answers), the qa server is simply left running past its usual lifetime with nothing POSTed to it — the same recoverable state as any other abandoned `.viva/server.url` today. SKILL.md's existing guard already covers this: "If `.viva/server.url` exists when `/viva` starts, a previous session may still be running... delete it before proceeding."

## Open questions

- **Invocation surface.** Whether callers get a new dedicated skill that owns both phases end-to-end, or whether the hand-off is documented as an extension any caller can opt into from the existing `/viva-qa` contract by POSTing a follow-up round. Either satisfies the acceptance criteria's observable behavior; left to the implementation phase.
- **Self-description of the transitioned round.** Whether the round-1 review payload posted after a qa phase needs an explicit marker (e.g., a field recording that it followed a qa round) so the future headless-contract documentation (#111) can describe this session type distinctly, or whether "a review round POSTed to a server that started in qa mode" is sufficient. The epic pre-mortem already flags this risk directly (item 5: "undocumented new round-file shape") — whatever this story lands with must be surfaced explicitly (design doc, commit message, or code comment) so `headless-contract` doesn't have to reverse-engineer it.
- **Transition log line.** Whether the hand-off warrants its own printed line distinct from the existing per-mode startup line, for terminal-watching callers.
