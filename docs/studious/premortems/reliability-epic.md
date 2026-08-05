# Epic pre-mortem: reliability

Epic: viva's review loop enforces its own invariants in code — the human gate
cannot be bypassed, the loop cannot strand itself, and the server's loopback
guarantee holds. Source: milestone 5, issues #95, #102, #103, #104, #120, #121,
#124, #125, #126, #139. Register covers cross-story failure modes only — each
story's own design-time pre-mortem (if any) covers single-story risk.

## Register

1. **The SKILL.md rewrite eats the edit made beside it.** `loop-driver` rewrites
   `.claude/skills/viva/SKILL.md` wholesale — 382 lines down toward ~80 — while
   `skill-prose-fixes` removes one specific line from that same file
   (`:134`, the auto-approve escape hatch). If `skill-prose-fixes` lands first,
   the rewrite can silently reintroduce the line it deleted; if the rewrite
   lands first, the target line no longer exists at the cited location and the
   story reads as already-done without anyone confirming the hatch is actually
   gone. The dependency edge exists to force the order, but the edge only
   protects the outcome if "landed" means merged — and the deleted line has no
   test asserting its absence, so nothing catches a silent reintroduction.
   *Names: `loop-driver`, `skill-prose-fixes`.*

2. **The `/complete` all-approved guard breaks Q&A.** #102(3) asks for the
   all-approved check to be duplicated inside the `POST /complete` handler. Q&A
   sessions also POST `/complete` (`tests/test_server_qa_complete_shutdown.py`)
   and carry no `sections` and no verdicts at all. A guard written against the
   review shape — "refuse unless every section is approved" — refuses every Q&A
   session's shutdown, turning a correctness fix into a hang on an unrelated
   mode. The guard has to be mode-aware, and the mode it must except is the one
   the story author is least likely to be holding in mind.
   *Names: `loop-driver`.*

3. **The Origin fix locks out the actual reviewer.** #124's recommended patch
   accepts only `o.hostname in ("127.0.0.1", "localhost")`, and its "additionally"
   clause floats rejecting a non-loopback `Host` header for DNS-rebinding
   defense. The server prints one URL; a reviewer who reaches the page by the
   other spelling, or through any local proxy that rewrites `Host`, then gets a
   403 on submit — after typing their review, at the one moment the product
   exists to serve. The failure is silent until submit and indistinguishable
   from a server crash from the reviewer's side.
   *Names: `origin-and-output-guard`.*

4. **The driver's re-arm meets the endpoint guard written against it.**
   `loop-driver`'s `rearm` subcommand POSTs `/next-round`, and `handoff-mode`
   adds a mode restriction to that same endpoint while `origin-and-output-guard`
   constrains its `output` field. Three stories converge on one endpoint from
   three directions, and each is individually correct: a driver that passes
   `output` on every re-arm, a guard that rejects an `output` outside the
   launched directory, and a guard that rejects a payload whose `mode` doesn't
   match the booted session. Composed, the driver's own re-arm is the request
   most likely to trip the other two.
   *Names: `loop-driver`, `handoff-mode`.*

5. **Five stories, one 4,600-line file, parallel worktrees.** `qa-free-text`,
   `origin-and-output-guard`, `handoff-mode`, `anchor-occurrence`, and
   `loop-driver` all edit `server.py`. Git will merge disjoint hunks cleanly and
   say nothing about whether the embedded JS still composes — the frontend is
   one string constant, so two stories can each add a correct handler and
   produce a page that throws. This is a scheduling risk rather than a
   story-specific design flaw: it is what the concurrency cap and the canary
   exist to bound, and it names no single story deliberately.

6. **The frontend lanes never route.** viva's entire UI lives inside
   `server.py`. `reference/audit-routing-signals.md`'s Frontend signal matches
   `*.html`, `*.css`, `*.jsx`, `*.vue` and friends, and deliberately excludes
   bare source files — so `qa-free-text`, `handoff-mode`, and
   `anchor-occurrence`, all of which are user-visible browser behavior, route as
   backend Python and draw no `ux-reviewer`, `frontend-reviewer`, or
   accessibility lane. Each will be judged on Python correctness by lanes that
   never open the page. DESIGN.md's accessibility requirements and card-state
   rules are the standard those stories are actually accountable to, and no
   automatic lane checks them. Like item 5, this is a property of the repo's
   architecture rather than a design flaw any one story can cause or avoid, so
   it names no story.

7. **`anchor.offset` is a documented contract, not a local variable.**
   DESIGN.md's JSON protocol conventions pin `comment.anchor` as `{text, offset}`
   and state that `offset` disambiguates a repeated phrase. #95 changes what
   that number means at the capture side. A story that records an occurrence
   *index* where the schema documents a source *offset* leaves every existing
   consumer — `section_key` carry-forward, the agent's rewrite targeting, the
   revision ledger — reading a number whose units silently changed, with no
   validator to catch it because `schema.py` documents the shape and not the
   semantics.
   *Names: `anchor-occurrence`.*
