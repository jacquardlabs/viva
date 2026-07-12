# CHANGELOG

<!-- version list -->

## v1.18.1 (2026-07-12)

### Bug Fixes

- Add xargs -r to $VIVA_DIR resolve for GNU xargs empty-input safety
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

- Register viva-qa and viva-diff as their own skills
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

### Chores

- Verified /viva entry point resolves after skill registration fix
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

### Documentation

- Close install-channel verification loop in skill-registration spec
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

- Close manual-channel shadowing gap with doc-confirmed precedence
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

- Close README fail-loud guard gap; make Task 3 verification single-machine
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

- Correct spec to symlink reality; move-not-delete plan, skill-set test
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

- Design spec for one-directory-per-skill registration
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

- Implementation plan for skill registration fix
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

- Pre-mortem register for skill-registration design
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

- Remove unverified direct-repo install channel
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

- Repoint cross-references to the new skill paths
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))

- Revise skill-registration spec per design-review findings
  ([#136](https://github.com/jacquardlabs/viva/pull/136),
  [`3d929ec`](https://github.com/jacquardlabs/viva/commit/3d929ec1a2da8e5cfe027eb8a4ae85a56d90902b))


## v1.18.0 (2026-07-12)

### Bug Fixes

- Populate titleblock and reset qa state on qa→review hand-off
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Promote coarser headings to split points in auto-detect
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- **diff**: Call /complete when re-diff comes back empty
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- **diff**: Give empty-diff finish its own report and skip the commit prompt
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

### Documentation

- Add jig-integration epic pre-mortem register
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Design doc for /next-round + /complete hardening (#112, #117, #118)
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Design doc for coarser-heading task-split absorption fix
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Design doc for diff-mode process-leak fix ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Design doc for recommended-choice flag on QA schema
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Design doc for resuming review on an already-signed-off doc
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Design doc for task-card review mode ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Design doc for the qa→review processing-spinner timeout affordance
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Design doc for the stable headless invocation contract
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Design doc for unified Q&A → review session
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Document resuming review on an already-signed-off doc
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Fix task-card-split design doc's auto-detection mechanism claim
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Pre-mortem addendum for jig-integration amendment (#112-#119)
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Restyle README with dustjacket ([#123](https://github.com/jacquardlabs/viva/pull/123),
  [`204fb52`](https://github.com/jacquardlabs/viva/commit/204fb5289624dca241cac12a90f432e940c93a03))

- Stable headless invocation contract ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- **premortem**: Design-review pre-mortem for recommended-choice flag
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- **premortem**: Design-review register for /next-round + /complete hardening (#112, #117, #118)
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- **premortem**: Design-review register for qa-resume-signed-off-doc-docs
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- **premortem**: Register for qa-handoff-spinner-timeout (design-review PROCEED TO PLAN)
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- **premortem**: Register for unified Q&A → review session
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- **studious**: Pre-mortem register for coarser-heading task-split fix
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- **studious**: Pre-mortem register for diff-mode-process-leak (design-review PROCEED TO PLAN)
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- **studious**: Pre-mortem register for task-card-split design review
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

### Features

- --split-on flag for task-card plan documents
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Harden /next-round + /complete lifecycle, security, and docs
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Soft client-side timeout on the qa→review processing spinner
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- Unified Q&A → review session hand-off ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))

- **qa**: Add recommended-choice flag to QAQuestion schema
  ([#122](https://github.com/jacquardlabs/viva/pull/122),
  [`ed004ef`](https://github.com/jacquardlabs/viva/commit/ed004ef4a0abb9d78b6949f52e92d1df060c00e5))


## v1.17.0 (2026-07-11)

### Documentation

- Add design spec for doc-identifying tab titles
  ([#108](https://github.com/jacquardlabs/viva/pull/108),
  [`1b2a111`](https://github.com/jacquardlabs/viva/commit/1b2a11117e0cc2ceda4e5f75a78129ca3728bb31))

- Add implementation plan for doc-identifying tab titles
  ([#108](https://github.com/jacquardlabs/viva/pull/108),
  [`1b2a111`](https://github.com/jacquardlabs/viva/commit/1b2a11117e0cc2ceda4e5f75a78129ca3728bb31))

### Features

- **ui**: Identify the reviewed doc in the browser tab title
  ([#108](https://github.com/jacquardlabs/viva/pull/108),
  [`1b2a111`](https://github.com/jacquardlabs/viva/commit/1b2a11117e0cc2ceda4e5f75a78129ca3728bb31))


## v1.16.0 (2026-07-05)

### Bug Fixes

- **diff-mode**: Address gate-audit Critical/Important/Minor findings
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- **diff-mode**: Apply gate-audit Important cluster
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- **diff-mode**: Clip d2h line numbers inside the collapse accordion
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- **diff-mode**: Dark-mode color scheme + hide d2h Viewed checkbox
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- **diff-mode**: Force-hide the confidence-sort toggle in diff mode
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- **diff-mode**: LCS-based line realignment for replace blocks
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- **diff-mode**: Shared table scroll, reduced density
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- **diff-mode**: Similarity-based alignment for replace-block gaps
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- **diff-mode**: Suppress Viewed toggle via fileContentToggle config
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

### Code Style

- Use em dash in setupCardSort comment for consistency
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

### Documentation

- Add design spec for /viva-diff file-header grouping
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- Add design spec for LCS-based diff block realignment
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- Add design spec for similarity-based gap-pairing
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- Add design spec for the diff-first surface ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- Add implementation plan for /viva-diff file-header grouping
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- Add implementation plan for LCS-based diff realignment
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- Add implementation plan for similarity-based gap pairing
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- Add implementation plan for the diff-first surface
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- Describe the diff-first rendering in README's Diff mode section
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- Record colorScheme + fileContentToggle in DESIGN.md's d2h config
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

### Features

- Side-by-side hunk rendering for /viva-diff; extract filepathFromTitle
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- **diff-mode**: Delegate hunk rendering to diff2html
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- **diff-mode**: Group hunk cards under a per-file header
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

- **diff-mode**: Mode-diff layout -- wide shell, single scroll context
  ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))

### Refactoring

- Apply /simplify cleanup findings ([#100](https://github.com/jacquardlabs/viva/pull/100),
  [`b1e2f54`](https://github.com/jacquardlabs/viva/commit/b1e2f54a0f96d9efb815af091f56b57144efb8b8))


## v1.15.0 (2026-07-03)

### Features

- Human-checkpoint primitives + diff mode (#82 #85)
  ([#98](https://github.com/jacquardlabs/viva/pull/98),
  [`e3345ac`](https://github.com/jacquardlabs/viva/commit/e3345ac217960678f99c03ecd536b094fcff53af))


## v1.14.4 (2026-07-03)

### Bug Fixes

- **ui**: Stop Q&A choice badge from crushing the card title
  ([#97](https://github.com/jacquardlabs/viva/pull/97),
  [`b4af98f`](https://github.com/jacquardlabs/viva/commit/b4af98f71e626fbb644343fae3b2e39f6126c50a))


## v1.14.3 (2026-07-02)

### Performance Improvements

- Cut round-load latency in the review loop and SPA
  ([#96](https://github.com/jacquardlabs/viva/pull/96),
  [`4aa02b0`](https://github.com/jacquardlabs/viva/commit/4aa02b05c781f33b5cae8966be191ad4f1a64f89))


## v1.14.2 (2026-07-02)

### Bug Fixes

- **a11y**: Scope Tab-to-advance to the active card; add skip link
  ([#94](https://github.com/jacquardlabs/viva/pull/94),
  [`568eed5`](https://github.com/jacquardlabs/viva/commit/568eed5b3a921f01c5ba65714b15da84b7275a72))


## v1.14.1 (2026-07-02)

### Bug Fixes

- **install**: Locate superpowers under any cache path; fail loud; add test
  ([#92](https://github.com/jacquardlabs/viva/pull/92),
  [`315c457`](https://github.com/jacquardlabs/viva/commit/315c457ab7682810ce49c71b895a58b88b026730))

- **server**: Reject non-dict JSON bodies on /submit and /next-round
  ([#93](https://github.com/jacquardlabs/viva/pull/93),
  [`eadb547`](https://github.com/jacquardlabs/viva/commit/eadb5472dfc0dcbdf60baf8cde7a65968bf64c9f))


## v1.14.0 (2026-07-02)

### Bug Fixes

- **ui**: Tokenize error surfaces; scale card stagger; clarify labels
  ([#91](https://github.com/jacquardlabs/viva/pull/91),
  [`457360e`](https://github.com/jacquardlabs/viva/commit/457360e6703440b99cb898b35643df4e9c517efd))

- **ui**: Tokenize error surfaces; scale card stagger; clarify labels
  ([#90](https://github.com/jacquardlabs/viva/pull/90),
  [`aeace7c`](https://github.com/jacquardlabs/viva/commit/aeace7c3f19022870110a0976503d0a730390ada))

### Features

- **api**: Standardize JSON error responses; /next-round output in body
  ([#91](https://github.com/jacquardlabs/viva/pull/91),
  [`457360e`](https://github.com/jacquardlabs/viva/commit/457360e6703440b99cb898b35643df4e9c517efd))

- **api**: Standardize JSON error responses; /next-round output in body
  ([#90](https://github.com/jacquardlabs/viva/pull/90),
  [`aeace7c`](https://github.com/jacquardlabs/viva/commit/aeace7c3f19022870110a0976503d0a730390ada))

### Refactoring

- Dead-code & defensive hygiene pass ([#91](https://github.com/jacquardlabs/viva/pull/91),
  [`457360e`](https://github.com/jacquardlabs/viva/commit/457360e6703440b99cb898b35643df4e9c517efd))


## v1.13.0 (2026-07-02)

### Features

- **api**: Standardize JSON error responses; /next-round output in body
  ([#89](https://github.com/jacquardlabs/viva/pull/89),
  [`49f09cf`](https://github.com/jacquardlabs/viva/commit/49f09cf1c3a083c3a185e643bbca66c5205252b2))

### Testing

- Extract shared server harness; add orchestration smoke test
  ([#88](https://github.com/jacquardlabs/viva/pull/88),
  [`416a76e`](https://github.com/jacquardlabs/viva/commit/416a76ea02c3c30be8e2f0befc1998c832434e8e))


## v1.12.1 (2026-07-02)

### Bug Fixes

- **a11y**: Accessibility pass on the embedded SPA
  ([#76](https://github.com/jacquardlabs/viva/pull/76),
  [`19ebaef`](https://github.com/jacquardlabs/viva/commit/19ebaef51b9921ada7a4aa3e5c92d1d7b7e798c1))

### Documentation

- Add CLAUDE.md architecture; document anchor overloading
  ([#77](https://github.com/jacquardlabs/viva/pull/77),
  [`d091c25`](https://github.com/jacquardlabs/viva/commit/d091c255f64c0ac3a3f0215c09b7f5d7181ba046))

- Add PRODUCT.md and DESIGN.md, refresh README for shipped features
  ([#72](https://github.com/jacquardlabs/viva/pull/72),
  [`b06e7c9`](https://github.com/jacquardlabs/viva/commit/b06e7c90e1dbd121fb1c5b9840aaf881fc51639f))

### Refactoring

- Centralize the .viva round-file schema in scripts/schema.py
  ([#73](https://github.com/jacquardlabs/viva/pull/73),
  [`3c69ee2`](https://github.com/jacquardlabs/viva/commit/3c69ee27b41f2bcdb1f64862c0bb1db8d8f8a938))

- **cli**: Argparse for revision_history; document annotate in-place merge
  ([#78](https://github.com/jacquardlabs/viva/pull/78),
  [`1e13491`](https://github.com/jacquardlabs/viva/commit/1e134915a14e3c474bb3d07ffc2d0e86365a0659))


## v1.12.0 (2026-06-29)

### Documentation

- Spec for per-comment image attachments ([#71](https://github.com/jacquardlabs/viva/pull/71),
  [`0426446`](https://github.com/jacquardlabs/viva/commit/04264464445b16deb3f5fffca3ca2df1cd945af1))

### Features

- Per-comment image attachments ([#71](https://github.com/jacquardlabs/viva/pull/71),
  [`0426446`](https://github.com/jacquardlabs/viva/commit/04264464445b16deb3f5fffca3ca2df1cd945af1))

- Restore comment.attachments instruction in SKILL.md
  ([#71](https://github.com/jacquardlabs/viva/pull/71),
  [`0426446`](https://github.com/jacquardlabs/viva/commit/04264464445b16deb3f5fffca3ca2df1cd945af1))

- Restore per-comment image attachments in review feedback (#66)
  ([#71](https://github.com/jacquardlabs/viva/pull/71),
  [`0426446`](https://github.com/jacquardlabs/viva/commit/04264464445b16deb3f5fffca3ca2df1cd945af1))

### Testing

- Integration test for comment image attachments
  ([#71](https://github.com/jacquardlabs/viva/pull/71),
  [`0426446`](https://github.com/jacquardlabs/viva/commit/04264464445b16deb3f5fffca3ca2df1cd945af1))


## v1.11.1 (2026-06-29)

### Bug Fixes

- **qa**: Standardize QA submit contract + add end-to-end test
  ([#70](https://github.com/jacquardlabs/viva/pull/70),
  [`28a4aba`](https://github.com/jacquardlabs/viva/commit/28a4aba0f26e00106d2b769f92ef0c26171a335b))


## v1.11.0 (2026-06-29)

### Features

- **ui**: Blueprint design pass — sheet frame, revision triangles, approval stamp
  ([#69](https://github.com/jacquardlabs/viva/pull/69),
  [`a74f119`](https://github.com/jacquardlabs/viva/commit/a74f119eaaa1b3a78716ab60d6b0454dcf763f56))


## v1.10.0 (2026-06-29)

### Bug Fixes

- **skill**: Type-aware rewrite (info = respond only, no source edit); test escalated reply appends
  changes exchange ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Keep last non-empty selection so 'comment on selection' opens the popover
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Reconcile deriveVerdict with submitReview (single source; untouched=pending)
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Settle stub is inert for verdict/button count (settle-only section stays approvable)
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Single activeComments predicate (settle-only section approvable); cut dead review-comment
  attachment promise ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

### Code Style

- **ui**: Drop anchor icons; make commented-span a clear accent callout
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Popover hint→mono, anchor quote→small italic muted (match quote convention)
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Popover save/cancel match the blueprint reticle buttons
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

### Documentation

- Design for multiple inline comments per section in viva review
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- Implementation plan for multiple inline comments per section
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **plan**: Require main() test-runner convention to match CI
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **plan**: Strengthen Task 5 s2 assertion ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **plan**: Task 6 absorbs derived dot/stats/advance rewiring from Task 5
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **skill**: Consume comments[] — derived verdict, cid threads, retire pin
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

### Features

- Multiple inline comments per section (GitHub-style threads)
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **open-notes**: Re-key threads by comment cid for multi-comment review
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **parse**: Attach cid threads to sections grouped by title
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **revision-history**: Group cid threads by section with quoted span
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **server**: Derive ledger note from comments[] on submit
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Auto-open comment popover on text selection; drop the explicit button
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Derived verdict + toggling primary button + comments[] submit
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Per-comment thread list + cid settle; retire pin button
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Reply box on open threads — GitHub-style back-and-forth until settled
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Select→popover comment creation with typed highlights
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **ui**: Typed reply chips — escalate an info thread to a changes directive; SKILL.md hybrid rule
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

### Testing

- Assert s2 derives info verdict + comment cids in end-to-end test
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- End-to-end multi-comment round-trip + legacy fallback
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- Two comments on the same span preserved with distinct cids
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))

- **open-notes**: Add main() runner so CI executes the unit test
  ([#68](https://github.com/jacquardlabs/viva/pull/68),
  [`50ce029`](https://github.com/jacquardlabs/viva/commit/50ce029a22261456d19d1ac1014761eb15eccbfd))


## v1.9.0 (2026-06-28)

### Bug Fixes

- **test**: Update open_notes test needles for pin-btn rename
  ([#65](https://github.com/jacquardlabs/viva/pull/65),
  [`697957c`](https://github.com/jacquardlabs/viva/commit/697957cd87e31afd3eb7f0db86045c4d23c834b3))

### Features

- **ui**: Replace keep-open checkbox with pin-note toggle button
  ([#65](https://github.com/jacquardlabs/viva/pull/65),
  [`697957c`](https://github.com/jacquardlabs/viva/commit/697957cd87e31afd3eb7f0db86045c4d23c834b3))


## v1.8.1 (2026-06-28)

### Bug Fixes

- **ui**: Realign line-anchor controls to the blueprint design system
  ([#45](https://github.com/jacquardlabs/viva/pull/45),
  [`37736cf`](https://github.com/jacquardlabs/viva/commit/37736cf65d8f2e61064703acfe75fc72d071ff5b))


## v1.8.0 (2026-06-28)

### Features

- Learned recurring critiques (#17) ([#25](https://github.com/jacquardlabs/viva/pull/25),
  [`0c393cb`](https://github.com/jacquardlabs/viva/commit/0c393cb2c07fdf01be51059eba3a1b855e81c37e))


## v1.7.0 (2026-06-28)

### Features

- Line anchors, open notes across rounds, confidence triage (#15, #16, #12)
  ([#24](https://github.com/jacquardlabs/viva/pull/24),
  [`6de093f`](https://github.com/jacquardlabs/viva/commit/6de093fe6d9070e807b319bebb6e4d5590755b7e))


## v1.6.0 (2026-06-28)

### Bug Fixes

- Token-boundary type inference (#13) and dash-prefixed diff lines
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

### Documentation

- Document pre-review producers in SKILL.md (#9, #10, #11, #13)
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

### Features

- Deep-link annotation anchors to conflicting sections
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

- Required-section checklist gating producer ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

- Round-to-round section diff on rewritten cards
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

- Section annotation producers + round-to-round diff (#9, #10, #11, #13, #14)
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

- Shared annotation-merge helper for producers ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))

- Spec<->code drift producer (#11), existence checks
  ([#23](https://github.com/jacquardlabs/viva/pull/23),
  [`63dd066`](https://github.com/jacquardlabs/viva/commit/63dd066eda2ec81df44a2047924445bfd81bf193))


## v1.5.0 (2026-06-27)

### Features

- Per-section annotation channel → card badges ([#22](https://github.com/jacquardlabs/viva/pull/22),
  [`533016a`](https://github.com/jacquardlabs/viva/commit/533016ae5fa26abeed6b010e5200ce5e43154a62))


## v1.4.0 (2026-06-27)

### Features

- Support Python 3.8+ and add CI test matrix ([#21](https://github.com/jacquardlabs/viva/pull/21),
  [`d8a116d`](https://github.com/jacquardlabs/viva/commit/d8a116dc948b1b38c45d422f6c5f1afd3367f1dc))


## v1.3.0 (2026-06-27)

### Code Style

- Align image-attachment UI with reticle/blueprint system
  ([#20](https://github.com/jacquardlabs/viva/pull/20),
  [`1ef9d80`](https://github.com/jacquardlabs/viva/commit/1ef9d80f20cc3a9e30142ea96962e8301540b192))

### Features

- Square buttons to reticle look, fix title-block overflow
  ([#20](https://github.com/jacquardlabs/viva/pull/20),
  [`1ef9d80`](https://github.com/jacquardlabs/viva/commit/1ef9d80f20cc3a9e30142ea96962e8301540b192))


## v1.2.0 (2026-06-27)

### Features

- Image attachments in review and Q&A feedback ([#18](https://github.com/jacquardlabs/viva/pull/18),
  [`9275844`](https://github.com/jacquardlabs/viva/commit/9275844ae40d1c031cbff78f8c0b7dac3c610688))


## v1.1.1 (2026-06-27)

### Performance Improvements

- Collapse viva startup to one round-trip, defer doc read
  ([#7](https://github.com/jacquardlabs/viva/pull/7),
  [`eac06f0`](https://github.com/jacquardlabs/viva/commit/eac06f08d251fff637c3d72ecafc37141dec72ac))


## v1.1.0 (2026-06-21)

### Bug Fixes

- 8 polish fixes across parser and server ([#6](https://github.com/jacquardlabs/viva/pull/6),
  [`f62aa3d`](https://github.com/jacquardlabs/viva/commit/f62aa3dad258354a465161c152b53a1981f6d237))

### Features

- Keyboard shortcuts, lazy markdown render, per-card skip
  ([#6](https://github.com/jacquardlabs/viva/pull/6),
  [`f62aa3d`](https://github.com/jacquardlabs/viva/commit/f62aa3dad258354a465161c152b53a1981f6d237))


## v1.0.2 (2026-06-21)

### Bug Fixes

- 8 polish fixes across parser and server ([#5](https://github.com/jacquardlabs/viva/pull/5),
  [`1572893`](https://github.com/jacquardlabs/viva/commit/1572893d3c670fa1e5e19ca56ac93261b810d2a7))


## v1.0.1 (2026-06-21)

### Continuous Integration

- Push-notify marketplace on release ([#3](https://github.com/jacquardlabs/viva/pull/3),
  [`638a832`](https://github.com/jacquardlabs/viva/commit/638a832ddcc5c90e52ba3efc9bc61963506dbb71))

### Documentation

- Rewrite README in Bryan's voice ([#2](https://github.com/jacquardlabs/viva/pull/2),
  [`83cfae0`](https://github.com/jacquardlabs/viva/commit/83cfae0f67a49f1bc8d8ae7c323991297a0ffe9a))

### Performance Improvements

- Replace LLM parsing with bundled section parser
  ([#4](https://github.com/jacquardlabs/viva/pull/4),
  [`e5e7d93`](https://github.com/jacquardlabs/viva/commit/e5e7d93cbf5ea201d92dba2eee49c1c173dad141))


## v1.0.0 (2026-06-20)

- Initial Release
