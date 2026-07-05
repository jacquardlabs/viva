# Diff-Mode File-Header Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In `/viva-diff` (viva's `--mode diff`), insert a static divider row above each contiguous run of hunk-cards that belong to the same file, so a reviewer can tell at a glance which file they're looking at and how many hunks it has.

**Architecture:** Pure client-side addition inside `server.py`'s embedded `HTML` constant. `initReview()` already appends one `.card` per section into `#review-cards` in document order (which is already file-contiguous — `parse_diff.py` emits every hunk of a file before moving to the next). Two small additions: (1) a shared `filepathFromTitle(title)` helper extracted out of the existing `langFromTitle` (added in #99), reused by the new grouping code; (2) a `diffFileHunkCounts(sections)` helper plus a few lines in `initReview()`'s render loop that insert a `.file-group-header` div, gated on `REVIEW_DATA.mode === 'diff'`. No schema change, no server-side change, no new endpoint.

**Tech Stack:** Python 3 stdlib (`server.py` HTTP handler), vanilla JS embedded in the `HTML` constant (no build step, no npm), Python stdlib test harness (`tests/_server_harness.py`, subprocess + `urllib`).

## Global Constraints

- Every source file this repo ships stays stdlib-only — no npm, no build step (`CLAUDE.md`).
- `scripts/schema.py` is the only cross-import allowed between `scripts/*.py` modules — not touched by this plan, called out only because it's the boundary this plan must NOT cross (no schema change needed or wanted here).
- Tests are plain `python3 tests/test_*.py` scripts with a `main()` printing `OK`/pass summary — no pytest, no Node dependency in the committed suite (`CLAUDE.md`, and confirmed by the existing `tests/test_server_diff_render.py` docstring, which explicitly disclaims any JS/browser test harness).
- Diff-mode-only feature: `initReview()` is shared with review mode: every change here must be gated on `REVIEW_DATA.mode === 'diff'` so review mode's rendering is provably unaffected.
- Approved spec: `docs/superpowers/specs/2026-07-02-diff-mode-file-header-grouping-design.md`. Non-goals from that spec (do NOT implement): no sticky/pinned header, no collapse/expand of a file's hunks, no live approval-count in the header, no per-file added/removed line-count stats. Filepath + static hunk count only.

---

### Task 1: Extract `filepathFromTitle` shared helper

**Files:**
- Modify: `server.py:1451-1458` (the `langFromTitle` function, added in #99)
- Test: `tests/test_server_diff_render.py`

**Interfaces:**
- Produces: `filepathFromTitle(title: string) -> string` — strips the trailing `" hunk N"` suffix off a diff-mode section title (e.g. `"src/foo.py hunk 1"` → `"src/foo.py"`). Task 2 calls this directly.
- Consumes: nothing new. `langFromTitle` already contains this exact regex inline; this task only extracts it.

Current code at `server.py:1451-1458`:

```js
// section.title for diff-mode sections is "{filepath} hunk N" (parse_diff.py).
// Strip the " hunk N" suffix, take the extension, map to an hljs language.
function langFromTitle(title) {
  const filepath = String(title || '').replace(/\s+hunk\s+\d+$/, '');
  const m = filepath.match(/\.([a-zA-Z0-9]+)$/);
  const ext = m ? m[1].toLowerCase() : '';
  return EXT_LANG[ext] || 'plaintext';
}
```

- [ ] **Step 1: Write the failing test**

Open `tests/test_server_diff_render.py`. Add a new test function anywhere after `test_page_ships_side_by_side_renderer` (e.g. directly below it):

```python
def test_page_ships_filepath_helper() -> None:
    """filepathFromTitle is extracted as its own function so both the language
    inference (langFromTitle) and the new file-grouping logic share one
    definition of 'strip the hunk suffix off a diff-mode section title'."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        assert "function filepathFromTitle" in page, "page missing: function filepathFromTitle"
    print("test_page_ships_filepath_helper: OK")
```

Also add the call to `main()`:

```python
def main() -> None:
    test_page_ships_side_by_side_renderer()
    test_page_ships_filepath_helper()
    test_diff_content_served_verbatim()
    print("\nAll server diff-render tests passed.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_server_diff_render.py`
Expected: `AssertionError: page missing: function filepathFromTitle`

- [ ] **Step 3: Write the minimal implementation**

Replace the `langFromTitle` function at `server.py:1451-1458` with:

```js
// section.title for diff-mode sections is "{filepath} hunk N" (parse_diff.py).
// Strip the " hunk N" suffix to recover the filepath. Shared by langFromTitle
// (extension → hljs language) and diffFileHunkCounts (file-header grouping).
function filepathFromTitle(title) {
  return String(title || '').replace(/\s+hunk\s+\d+$/, '');
}

// Extension → hljs language, for per-cell syntax coloring in the side-by-side
// diff table.
function langFromTitle(title) {
  const filepath = filepathFromTitle(title);
  const m = filepath.match(/\.([a-zA-Z0-9]+)$/);
  const ext = m ? m[1].toLowerCase() : '';
  return EXT_LANG[ext] || 'plaintext';
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_server_diff_render.py`
Expected: all three tests print `OK`, ending with `All server diff-render tests passed.`

Also run the full suite to confirm no regression in `langFromTitle`'s existing behavior (per-cell syntax highlighting still keys off the same extension logic):

Run: `for f in tests/test_*.py; do python3 "$f" || echo "FAILED: $f"; done`
Expected: no `FAILED` lines.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server_diff_render.py
git commit -m "$(cat <<'EOF'
refactor: extract filepathFromTitle out of langFromTitle

Shared helper for the diff-mode "{filepath} hunk N" title convention —
the file-header grouping work in the next commit reuses it instead of
duplicating the strip-suffix regex.
EOF
)"
```

---

### Task 2: Render a file-header divider above each contiguous file run

**Files:**
- Modify: `server.py:1596-1628` (`initReview()`)
- Modify: `server.py:298` (`.cards` CSS block — insert new rule after it)
- Test: `tests/test_server_diff_render.py`

**Interfaces:**
- Consumes: `filepathFromTitle(title)` from Task 1.
- Produces: `diffFileHunkCounts(sections) -> Map<string, number>` — maps each distinct filepath (as returned by `filepathFromTitle`) to how many sections share it. Not consumed elsewhere in this plan, but this is the name/shape a later task would call if the grouping logic needs reuse (e.g. a future per-file stat).

Current code at `server.py:1596-1628`:

```js
function initReview() {
  _pendingMarkdown.clear();
  const container = el('review-cards');
  const priorApprovedSet = new Set(REVIEW_DATA.approved_ids || []);
  // Pre-populate approved state for sections approved in previous rounds
  priorApprovedSet.forEach(id => {
    rState.verdicts[id] = { verdict: 'approved', note: '' };
  });
  let animIdx = 0;
  REVIEW_DATA.sections.forEach((s, i) => {
    const card = buildReviewCard(s);
    // Cards carried forward as already-approved from a prior round appear
    // instantly (no fade) — only new/changed cards get the staggered fade-in,
    // re-indexed among themselves so the stagger stays tight regardless of
    // how many sections are already approved and collapsed.
    if (REVIEW_DATA.round > 1 && priorApprovedSet.has(s.id)) {
      card.style.animation = 'none';
    } else {
      card.style.animationDelay = Math.min(0.04 + animIdx * 0.04, 0.3) + 's';
      animIdx++;
    }
    container.appendChild(card);
    // Apply approved CSS immediately for pre-approved cards
    if (priorApprovedSet.has(s.id)) syncReviewCard(s.id);
  });
  // Open first non-approved card
  const firstPending = REVIEW_DATA.sections.find(s => !priorApprovedSet.has(s.id));
  if (firstPending) activateReviewCard(firstPending.id);
  else if (REVIEW_DATA.sections.length > 0) activateReviewCard(REVIEW_DATA.sections[0].id);
  updateReviewStats();
  renderLedger();
  setupCardSort();
}
```

Current code at `server.py:297-299`:

```css
/* ─── Cards ──────────────────────────────────────────────── */
.cards { display: flex; flex-direction: column; gap: 6px; }
```

- [ ] **Step 1: Write the failing test**

Add a multi-hunk-per-file fixture and a wiring test to `tests/test_server_diff_render.py`. Insert this fixture near `DIFF_INPUT` (after its closing `}`):

```python
GROUPED_DIFF_INPUT = {
    "mode": "diff",
    "doc_file": "HEAD~1..HEAD",
    "round": 1,
    "approved_ids": [],
    "sections": [
        {
            "id": "s1",
            "title": "src/foo.py hunk 1",
            "content": "```diff\n@@ -1,2 +1,3 @@\n a\n+b\n c\n```",
        },
        {
            "id": "s2",
            "title": "src/foo.py hunk 2",
            "content": "```diff\n@@ -10,2 +11,3 @@\n x\n+y\n z\n```",
        },
        {
            "id": "s3",
            "title": "src/bar.py hunk 1",
            "content": "```diff\n@@ -1,1 +1,2 @@\n p\n+q\n```",
        },
    ],
}
```

Add the test function (after `test_page_ships_filepath_helper`):

```python
def test_page_ships_file_group_header() -> None:
    """The grouping logic and its CSS are shipped, gated on diff mode."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(GROUPED_DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        page = get_text(base, "/")
        for needle in (
            "function diffFileHunkCounts",
            "file-group-header",
            "REVIEW_DATA.mode === 'diff'",
        ):
            assert needle in page, f"page missing: {needle}"
    print("test_page_ships_file_group_header: OK")


def test_grouped_sections_stay_file_contiguous() -> None:
    """The grouping feature assumes hunks of the same file are never
    interleaved with another file's hunks — parse_diff.py guarantees this by
    construction. This pins that precondition against the fixture the SPA
    would otherwise silently mis-group if it regressed."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(GROUPED_DIFF_INPUT))
    with launch_server(viva / "in1.json", viva / "out1.json", mode="diff", cwd=tmp) as base:
        data = get(base, "/input")
        titles = [s["title"] for s in data["sections"]]
        filepaths = [t.rsplit(" hunk ", 1)[0] for t in titles]
        seen = []
        for fp in filepaths:
            if not seen or seen[-1] != fp:
                seen.append(fp)
        assert seen.count("src/foo.py") == 1 and seen.count("src/bar.py") == 1, \
            f"expected each filepath as one contiguous run, got order: {filepaths}"
    print("test_grouped_sections_stay_file_contiguous: OK")
```

Update `main()`:

```python
def main() -> None:
    test_page_ships_side_by_side_renderer()
    test_page_ships_filepath_helper()
    test_page_ships_file_group_header()
    test_grouped_sections_stay_file_contiguous()
    test_diff_content_served_verbatim()
    print("\nAll server diff-render tests passed.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_server_diff_render.py`
Expected: `test_grouped_sections_stay_file_contiguous: OK` passes (parse_diff.py's ordering guarantee already holds — this one is a precondition pin, not new behavior), but `test_page_ships_file_group_header` fails with `AssertionError: page missing: function diffFileHunkCounts`.

- [ ] **Step 3: Write the minimal implementation**

Add the CSS rule. Replace `server.py:297-299`:

```css
/* ─── Cards ──────────────────────────────────────────────── */
.cards { display: flex; flex-direction: column; gap: 6px; }
```

with:

```css
/* ─── Cards ──────────────────────────────────────────────── */
.cards { display: flex; flex-direction: column; gap: 6px; }

/* ─── diff-mode file grouping: static divider above each run of hunks
   belonging to the same file. Landmark, not a heading — same quiet
   typographic register as .sxs-fold-cell/.diff-toggle. ─── */
.file-group-header {
  color: var(--text2);
  font-family: 'Fragment Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 6px 2px 0;
}
```

Add `diffFileHunkCounts` right above `initReview` (`server.py:1596`):

```js
// Diff mode only: how many sections share each filepath. parse_diff.py emits
// every hunk of a file contiguously before moving to the next file, so a
// single pass building this map is enough to know each run's total up front —
// no need to look ahead while iterating in the render loop below.
function diffFileHunkCounts(sections) {
  const counts = new Map();
  sections.forEach(s => {
    const fp = filepathFromTitle(s.title);
    counts.set(fp, (counts.get(fp) || 0) + 1);
  });
  return counts;
}

function initReview() {
```

Now update the render loop inside `initReview()`. Replace:

```js
function initReview() {
  _pendingMarkdown.clear();
  const container = el('review-cards');
  const priorApprovedSet = new Set(REVIEW_DATA.approved_ids || []);
  // Pre-populate approved state for sections approved in previous rounds
  priorApprovedSet.forEach(id => {
    rState.verdicts[id] = { verdict: 'approved', note: '' };
  });
  let animIdx = 0;
  REVIEW_DATA.sections.forEach((s, i) => {
    const card = buildReviewCard(s);
```

with:

```js
function initReview() {
  _pendingMarkdown.clear();
  const container = el('review-cards');
  const priorApprovedSet = new Set(REVIEW_DATA.approved_ids || []);
  // Pre-populate approved state for sections approved in previous rounds
  priorApprovedSet.forEach(id => {
    rState.verdicts[id] = { verdict: 'approved', note: '' };
  });
  // File-header grouping (diff mode only): a static divider ahead of each
  // contiguous run of hunks sharing a filepath. hunkCounts stays null in
  // review mode, so the check below is always false there — zero behavior
  // change for review mode.
  const hunkCounts = REVIEW_DATA.mode === 'diff' ? diffFileHunkCounts(REVIEW_DATA.sections) : null;
  let lastFilepath = null;
  let animIdx = 0;
  REVIEW_DATA.sections.forEach((s, i) => {
    if (hunkCounts) {
      const fp = filepathFromTitle(s.title);
      if (fp !== lastFilepath) {
        const header = document.createElement('div');
        header.className = 'file-group-header';
        const n = hunkCounts.get(fp);
        header.textContent = fp + ' · ' + n + ' hunk' + (n === 1 ? '' : 's');
        container.appendChild(header);
        lastFilepath = fp;
      }
    }
    const card = buildReviewCard(s);
```

(The rest of `initReview()` — the animation/append/activate/stats lines after this point — is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_server_diff_render.py`
Expected: all five tests print `OK`, ending with `All server diff-render tests passed.`

Run the full suite:

Run: `for f in tests/test_*.py; do python3 "$f" || echo "FAILED: $f"; done`
Expected: no `FAILED` lines. Pay particular attention to `tests/test_server_orchestration.py` and any other review-mode test that exercises `initReview()` — they must be unaffected since `hunkCounts` is `null` outside diff mode.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server_diff_render.py
git commit -m "$(cat <<'EOF'
feat(diff-mode): group hunk cards under a per-file header

Static divider ("path/to/file.py · N hunks") above each contiguous run
of hunks sharing a filepath, so multi-hunk files are scannable at a
glance in /viva-diff. Diff-mode-only; review mode's initReview() path
is unaffected (hunkCounts stays null there).
EOF
)"
```

---

### Task 3: Manual end-to-end verification

No new files. This task has no automated-test deliverable — it exists because the automated suite (Tasks 1-2) is wiring-level only (asserts the JS is present in the served page; cannot execute it, per this repo's stdlib-only/no-npm testing constraint). This is the same gap the #99 side-by-side rendering work hit, closed the same way: exercise the real pipeline and eyeball the real render.

- [ ] **Step 1: Build a real multi-file, multi-hunk-per-file patch**

```bash
rm -rf /tmp/viva-grouping-e2e && mkdir -p /tmp/viva-grouping-e2e/src && cd /tmp/viva-grouping-e2e
git init -q && git config user.email a@b.com && git config user.name test
cat > src/foo.py <<'EOF'
def a():
    return 1


def b():
    return 2
EOF
cat > src/bar.py <<'EOF'
def c():
    return 3
EOF
git add -A && git commit -q -m init
cat > src/foo.py <<'EOF'
def a():
    return 100


def b():
    return 200
EOF
cat > src/bar.py <<'EOF'
def c():
    return 300
EOF
git diff HEAD > /tmp/viva-grouping-e2e/diff.patch
cat /tmp/viva-grouping-e2e/diff.patch
```

Expected: a patch touching `src/foo.py` (two separate hunks — the two `def` bodies are far enough apart in a bigger file to split, or reduce the blank lines between them if git merges them into one hunk; either is fine for this check as long as `src/bar.py` is a distinct file) and `src/bar.py` (one hunk).

- [ ] **Step 2: Parse and launch**

```bash
cd /Users/bryan/Projects/viva/.claude/worktrees/better-diffs
mkdir -p /tmp/viva-grouping-e2e/.viva
python3 scripts/parse_diff.py /tmp/viva-grouping-e2e/diff.patch \
  --output /tmp/viva-grouping-e2e/.viva/review-input-r1.json \
  --doc-file "working tree" --round 1
cd /tmp/viva-grouping-e2e
python3 /Users/bryan/Projects/viva/.claude/worktrees/better-diffs/server.py \
  --mode diff --input .viva/review-input-r1.json --output .viva/review-r1.json
```

This opens a browser (omit `--no-browser` specifically so it opens — the point of this task is to look at it).

- [ ] **Step 3: Confirm in the browser**

- Exactly one `src/foo.py · N hunks` header appears before `src/foo.py`'s card(s), and one `src/bar.py · 1 hunk` header before `src/bar.py`'s card — not one header per hunk.
- The very first section in the list gets a header too (not just section 2+) — this is the "first section always gets a header" edge case called out in the spec.
- Headers are visually quieter than card titles (matches the `.sxs-fold-cell`/`.diff-toggle` register) — a landmark, not competing with the card title for attention.
- No sticky/pinned behavior, no click behavior on the header (per spec non-goals) — it's inert.
- Approve/skip and keyboard tab-advance still move correctly from card to card across a file boundary (approve the last hunk of `src/foo.py`, confirm focus moves to `src/bar.py`'s card, not skipped or stuck).

- [ ] **Step 4: Clean up**

```bash
rm -rf /tmp/viva-grouping-e2e
```

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** static label (Task 2) ✓, filepath + hunk count content (Task 2's `header.textContent` line) ✓, diff-mode-only gating (Task 2's `hunkCounts` null-check + Task 3 Step 3's regression check) ✓, no sticky/collapse/live-status/line-stats (explicitly called out as non-goals in Global Constraints and re-checked in Task 3 Step 3) ✓, shared `filepathFromTitle` reuse (Task 1) ✓, first-section edge case (Task 3 Step 3) ✓.
- **Placeholder scan:** no TBD/TODO; every step shows real, complete code.
- **Type consistency:** `filepathFromTitle` (Task 1) is called by both `langFromTitle` (existing) and `diffFileHunkCounts`/the render loop (Task 2) — same name and signature throughout. `diffFileHunkCounts(sections) -> Map<string, number>` is defined once (Task 2) and not renamed elsewhere.
