# Pre-mortem — Frontend v2 phase 1 (transmittal rounds and sheet ground)

- Design doc: docs/superpowers/specs/2026-07-16-frontend-v2-target-design.md
- Branch: feat/frontend-v2-phase1
- SHA: f062cdf
- Date: 2026-07-17

| # | Lane | Failure mode | Detection hint |
|---|------|--------------|----------------|
| 1 | technical | The slip's attribution split silently doesn't ship — every `section.diff` gets labeled "revised to your note," misattributing cascade edits the reviewer never asked for (violates the doc's stated rule and principle 2) | Fixture with `diff` present and `open_notes` absent → the slip row must read neutral "revised"; check the wording branch in the slip builder in `server.py` |
| 2 | technical | Between-rounds card ships without the decided reload behavior — a tab reload during revision shows a blank card or stale snapshot instead of re-booting into the prior round's view exactly as today | Reload the tab during `processing` in the dogfood; check the SSE handler's empty-snapshot fallback and that no snapshot persistence was added to `.viva/` |
| 3 | technical | Recap gating rewires the shared `btn-submit`; a missed mode guard routes Q&A's `done →` through review's recap or dead-ends the button | Run a `--mode qa` session end to end; check the submit listener's `REVIEW_DATA`/`QA_DATA` branch |
| 4 | technical | Replacing `.sheet-frame` with `#paper` breaks `.mode-diff` widening or bottom-bar clearance (padding responsibilities move from shell to paper) | Diff session at ~1600px and review at 720px; confirm `.mode-diff #paper` rule exists and content clears the fixed bar |
| 5 | product | Carried-collapse reads too quiet — reviewers stop re-reading anything and rounds get rubber-stamped, drifting trust | Dogfood: count expands of "unchanged since your stamp — show"; confirm the affordance and "× withdraw approval" are discoverable |
| 6 | product | A slip jump row targets a carried (head-only) card and `activateReviewCard` no-ops, stranding the reviewer at a dead link | Click the ▣ carried row in the dogfood; check the jump handler handles carried ids (scroll + expand, not activate) |
| 7 | product | The recap overlay reads as a speed bump rather than a safety net at every-round frequency, taxing the persona who "reviews many docs" | Dogfood note at plan Task 6; verify `skip rest & submit` remains a direct escape hatch in code |
