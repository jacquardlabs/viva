# Inspector report — Task 1 (sheet ground), commit range 28eadb8..acdb203

Verdict: CLEAR

- Lens 1 (test self-dealing): clean. New tests assert the promised capability — both --table tokens with exact-count guard, #paper edge/inner-rule/decoration, structural nesting (#paper bounds main.shell), absence assertions unscoped over the full HTML constant; tests/test_frontend_v2_phase1.py re-asserts over HTTP from a real subprocess boot. Bookkeeping nit (below defect): Done-means item 3's tier cites test_server_diff_render.py, but the `.mode-diff #paper` needle lives in the a11y/phase-1 files; diff-render backs the "renders unchanged" half.
- Lens 2 (contract match): point-for-point against design-doc Decisions locked #1 — table #060e1a/#e2e8f1, content-bounded sheet, var(--bg) fill, var(--border2) edge, 1px inner rule at 7px inset, coordinates, corner marks, grid gone at every layer (verified no other page grid survives; reticle background-size is control decoration, not a grid). #paper is position:relative — a positioned, content-bounded left edge for phase 2's spine. DESIGN.md rewording correctly deferred to Task 6 per Not-here.
- Lens 3 (technicality gaming): none. Genuine structural replacement; deletions outright; absence assertions would fail loudly on reintroduction anywhere.
- Residual observations (not CONCERN): coordinates omit the south edge (spec doesn't prescribe edges); .paper-marks hides below 740px while the #paper border persists at all widths.
