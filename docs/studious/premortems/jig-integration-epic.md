# Epic pre-mortem: jig-integration

Epic: Stabilize viva's headless protocol and extend review surfaces for jig
integration. Source: issues #109, #110, #111. Register covers cross-story
failure modes only — each story's own design-time pre-mortem (if any) covers
single-story risk.

## Register

1. **Concurrent edits to shared parsing code.** `unified-session` and
   `task-card-split` both plausibly touch `scripts/parse_sections.py` and/or
   `scripts/schema.py` in parallel. A clean git merge onto the epic branch
   does not guarantee semantic compatibility — the two stories could each
   introduce a parsing convention that works alone but doesn't compose when
   both are present.

2. **Backward-compat regression on the thing #111 exists to protect.** jig
   already depends on today's CLI + JSON contract — that dependency is the
   entire reason #111 asks for a documented, versioned contract. Either
   `unified-session` or `task-card-split` could shift default `qa`/`review`
   behavior en route to adding new capability, breaking the existing
   consumer before the new contract doc even ships.

3. **Contract written against a moving target.** `headless-contract` depends
   on both other stories, but the dependency only protects the outcome if
   "landed" means merged onto the epic branch — not merely gate-passed at
   the story's own HEAD. If the epic-driver's landed/merge distinction is
   ever loosened, `headless-contract` could start against a story that
   passed its gates but hasn't actually merged, and document a surface that
   still changes underneath it.

4. **A second identity rule sneaking in.** `task-card-split`'s heading-based
   splitting must reuse `schema.py`'s `section_key()` for section identity.
   A narrow, story-scoped implementation could instead introduce a second,
   subtly different identity rule for task-card sections, silently breaking
   annotation/approval carry-forward for that path only — easy to miss
   because the default (non-split) path would still work correctly.

5. **Undocumented new round-file shape.** If `unified-session` introduces a
   new `.viva/` round-file naming pattern or shape for the combined
   session type, and that detail isn't surfaced explicitly (design doc,
   commit message, or code comment) by the time `headless-contract` starts,
   the contract doc could ship without documenting a whole session type
   even though it nominally ran after `unified-session` landed.

## Verified at epic finale

`@agent-premortem-auditor` checks each item above (REALIZED / NOT REALIZED /
CAN'T VERIFY) against the finished epic diff before the epic reaches `ready`.
