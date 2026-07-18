# Inspector report — Task 4 (recap overlay), commit range ddd2ae1..7c05d7d

Verdict: CLEAR

- Lens 1 (test self-dealing): clean, mutation-verified. Reverting the gate to direct submitReview(false) fails the routing test; adding a second submitReview(false) call site fails the exactly-one-call-site count; the count is not comment-gameable (statement form with trailing `;` appears once, positionally pinned). Static-string tier matches the file's declared convention. Suites green at HEAD (phase1 10 tests, handoff 6 tests).
- Lens 2 (contract match): every Do clause lands — four-column grid over deriveVerdict's exact return set, row click closes + activates, o/Escape wiring, kbd legend row, btn-skip and the qa submitQA(false) branch verbatim-untouched. Diff mode covered via the shared class-gated btn-submit handler. Not-here respected: openRecap bails on !REVIEW_DATA; payload and deriveVerdict untouched; escape hatch survives.
- Lens 3 (technicality gaming): none — generic iteration over REVIEW_DATA.sections; confirm mirrors live readiness (an o-opened recap can't submit early); the SSE-close and Cmd+Enter extras tighten the gate rather than route around it.
- Minor observation (outside lenses, for the a11y audit lane): the aria-modal dialog has no real focus trap — Tab can walk into the background.
