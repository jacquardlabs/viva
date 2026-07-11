# Doc-Identifying Tab Titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the browser tab title identify which document (or Q&A topic) a viva session is reviewing, so multiple concurrent sessions are distinguishable in the tab bar.

**Architecture:** Two small JS helpers (`tabDocName`, `setTabTitle`) added to the `HELPERS` block of the embedded SPA in `server.py`'s `HTML` constant, then wired into the five places that currently set `document.title` by hand. No schema change, no Python change, no new state — `doc_file` and `context` already reach the client on every payload.

**Tech Stack:** Python stdlib HTTP server (`server.py`) serving an embedded vanilla-JS SPA; plain-assertion Python tests (no pytest, no browser/DOM runtime) that string-match against the `HTML` constant.

## Global Constraints

- No schema change, no new `.viva/*.json` field, no Python-side change — spec requires this stay a pure client-side (embedded JS) presentation fix (design doc: "Non-goals").
- Title format is doc-first: `<doc-basename> · <state> · viva`, joining only non-empty parts with ` · `, with `viva` always trailing (design doc: "Approach").
- Use the basename of `doc_file` (`split('/').pop()`), not the full relative path (design doc: user-selected option).
- Empty/missing `doc_file` or `context` drops that segment silently — no error, no placeholder text (design doc: "Fallback behavior").
- Tests must follow the existing plain string-assertion pattern against `server.HTML` in `tests/test_server_a11y.py` — there is no JS execution/DOM harness in this repo, so behavior can only be verified structurally in Python, plus a manual browser check (design doc: "Testing").

---

### Task 1: Doc-identifying tab titles

**Files:**
- Modify: `server.py:1378-1380` (add `tabDocName`/`setTabTitle` helpers after `esc()`)
- Modify: `server.py:2436-2449` (SSE `round` handler)
- Modify: `server.py:2451-2476` (SSE `complete` handler)
- Modify: `server.py:2561-2589` (initial `/input` fetch handler — review, diff, and Q&A branches)
- Modify: `tests/test_server_a11y.py:60-64` (strip stale title assertions from the existing test)
- Modify: `tests/test_server_a11y.py` `main()` (register the new test)

**Interfaces:**
- Produces: `tabDocName(path)` — JS function, takes a relative path string (possibly `''`/`undefined`), returns the basename (`'docs/PRODUCT.md'` → `'PRODUCT.md'`, `''` → `''`).
- Produces: `setTabTitle(...parts)` — JS function, takes any number of string arguments (some may be falsy), drops falsy ones, joins the rest with `' · '`, and always appends a trailing `'viva'` segment, assigning the result to `document.title`.
- Consumes: `data.doc_file`, `data.context`, `data.mode`, `data.round` (already present on every `/input` and SSE `round` payload — see `scripts/schema.py` `ReviewInput`); `REVIEW_DATA`/`QA_DATA` globals (`server.py:1363-1364`, already populated by the init/round handlers).

- [ ] **Step 1: Write the failing test**

Edit `tests/test_server_a11y.py`. First, strip the two stale title assertions out of the existing test (they'll go stale the moment the implementation changes, and a dedicated test is clearer than folding unrelated concerns together):

```python
def test_stats_aria_live_and_dynamic_title():
    assert 'id="stats-area" aria-live="polite"' in HTML
    print("  ok  test_stats_aria_live_and_dynamic_title")
```

Then add a new test function right after it:

```python
def test_tab_title_identifies_document():
    # Tab titles lead with the doc/topic name (basename, not full path) so
    # concurrent viva sessions are distinguishable in the tab bar; 'viva' is
    # a fixed trailing suffix. All five title-setting sites (review/diff/qa
    # init, SSE round, SSE complete) route through one shared helper so a
    # future site can't drift back to a hardcoded, doc-blind title.
    assert "function tabDocName(path)" in HTML
    assert "function setTabTitle(...parts)" in HTML
    # No call site may hardcode the old doc-blind title strings.
    assert "document.title = 'viva · review · REV '" not in HTML
    assert "document.title = 'viva · diff · REV '" not in HTML
    assert "document.title = 'viva · brainstorm'" not in HTML
    # Exactly one definition + five call sites (review init, diff init, qa
    # init, SSE round, SSE complete).
    assert HTML.count("setTabTitle(") == 6, \
        "expected setTabTitle def + 5 call sites (review/diff/qa init, SSE round, SSE complete)"
    assert "setTabTitle(tabDocName(data.doc_file), 'REV ' + String(data.round).padStart(2, '0'));" in HTML
    assert "setTabTitle(tabDocName(data.doc_file), 'diff', 'REV ' + String(data.round).padStart(2, '0'));" in HTML
    assert "setTabTitle(data.context || 'brainstorm');" in HTML
    assert "setTabTitle(tabDocName(data.doc_file), ...(data.mode === 'diff' ? ['diff', rev] : [rev]));" in HTML
    assert "setTabTitle(REVIEW_DATA ? tabDocName(REVIEW_DATA.doc_file) : null, 'done');" in HTML
    print("  ok  test_tab_title_identifies_document")
```

Register it in `main()`, right after `test_stats_aria_live_and_dynamic_title()`:

```python
def main():
    test_card_head_is_button_with_aria()
    test_aria_expanded_sync_helper_exists()
    test_main_landmark_wraps_shell()
    test_skip_link_targets_main()
    test_stats_aria_live_and_dynamic_title()
    test_tab_title_identifies_document()
    test_decorative_emoji_are_aria_hidden()
    test_focus_visible_group_and_button_types()
    test_keyboard_legend_present_and_real()
    print("OK (9 tests)")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_server_a11y.py`
Expected: `AssertionError` on the first new assertion (`"function tabDocName(path)" in HTML`), since neither helper exists yet.

- [ ] **Step 3: Add the two helpers to the `HELPERS` block**

In `server.py`, immediately after the `esc()` function (currently `server.py:1378-1380`):

```javascript
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function tabDocName(path) {
  return (path || '').split('/').pop();
}

function setTabTitle(...parts) {
  document.title = parts.filter(Boolean).concat('viva').join(' · ');
}
```

- [ ] **Step 4: Wire the initial `/input` fetch handler (review, diff, Q&A branches)**

In `server.py`, replace the three `document.title = ...` lines inside the `fetch('/input')` success handler (currently `server.py:2567`, `2577`, `2586`):

Old:
```javascript
    if (data.mode === 'review') {
      REVIEW_DATA = data;
      el('doc-path').textContent    = data.doc_file || '';
      el('doc-path').title          = data.doc_file || '';   /* full path on hover when truncated */
      el('doc-title').innerHTML     = 'viva <em>review</em>';
      el('round-badge').textContent = String(data.round).padStart(2, '0');
      document.title = 'viva · review · REV ' + String(data.round).padStart(2, '0');
      el('review-view').style.display = '';
      initReview();
      connectSSE();
    } else if (data.mode === 'diff') {
      REVIEW_DATA = data;
      el('doc-path').textContent    = data.doc_file || 'diff';
      el('doc-path').title          = data.doc_file || 'diff';
      el('doc-title').innerHTML     = 'viva <em>diff</em>';
      el('round-badge').textContent = String(data.round).padStart(2, '0');
      document.title = 'viva · diff · REV ' + String(data.round).padStart(2, '0');
      el('review-view').style.display = '';
      initReview();
      connectSSE();
    } else {
      QA_DATA = data;
      el('qa-title').innerHTML          = esc(data.context || 'Q&amp;A phase');
      el('qa-title').title              = data.context || 'Q&A phase';   /* full topic on hover when truncated */
      el('qa-count-badge').textContent  = `${data.questions.length} questions`;
      document.title = 'viva · brainstorm';
      el('qa-view').style.display = '';
      initQA();
      connectSSE();
```

New:
```javascript
    if (data.mode === 'review') {
      REVIEW_DATA = data;
      el('doc-path').textContent    = data.doc_file || '';
      el('doc-path').title          = data.doc_file || '';   /* full path on hover when truncated */
      el('doc-title').innerHTML     = 'viva <em>review</em>';
      el('round-badge').textContent = String(data.round).padStart(2, '0');
      setTabTitle(tabDocName(data.doc_file), 'REV ' + String(data.round).padStart(2, '0'));
      el('review-view').style.display = '';
      initReview();
      connectSSE();
    } else if (data.mode === 'diff') {
      REVIEW_DATA = data;
      el('doc-path').textContent    = data.doc_file || 'diff';
      el('doc-path').title          = data.doc_file || 'diff';
      el('doc-title').innerHTML     = 'viva <em>diff</em>';
      el('round-badge').textContent = String(data.round).padStart(2, '0');
      setTabTitle(tabDocName(data.doc_file), 'diff', 'REV ' + String(data.round).padStart(2, '0'));
      el('review-view').style.display = '';
      initReview();
      connectSSE();
    } else {
      QA_DATA = data;
      el('qa-title').innerHTML          = esc(data.context || 'Q&amp;A phase');
      el('qa-title').title              = data.context || 'Q&A phase';   /* full topic on hover when truncated */
      el('qa-count-badge').textContent  = `${data.questions.length} questions`;
      setTabTitle(data.context || 'brainstorm');
      el('qa-view').style.display = '';
      initQA();
      connectSSE();
```

(The line after this block, the closing `}` and `.catch(...)`, is unchanged — only the three `document.title` lines and their surrounding unchanged lines shown above for anchoring.)

- [ ] **Step 5: Wire the SSE `round` handler**

`data.mode` is present on every round payload for both review and diff modes (`scripts/parse_sections.py:337` sets `"mode": "review"`, `scripts/parse_diff.py:199` sets `"mode": "diff"`; both flow through `/next-round`'s `_push_sse("round", {**new_data, ...})` unmodified). This also fixes a latent bug: the handler currently hardcodes the word `review` even when a diff-mode round arrives via SSE.

In `server.py`, replace (currently `server.py:2436-2449`):

Old:
```javascript
  es.addEventListener('round', e => {
    const data = JSON.parse(e.data);
    REVIEW_DATA       = data;
    rState.verdicts   = {};
    rState.active     = null;
    el('round-badge').textContent = String(data.round).padStart(2, '0');
    document.title = 'viva · review · REV ' + String(data.round).padStart(2, '0');
    el('review-cards').innerHTML  = '';
    initReview();
    el('processing-view').style.display = 'none';
    el('review-view').style.display     = '';
    el('btn-skip').disabled   = false;
    el('btn-submit').disabled = false;
  });
```

New:
```javascript
  es.addEventListener('round', e => {
    const data = JSON.parse(e.data);
    REVIEW_DATA       = data;
    rState.verdicts   = {};
    rState.active     = null;
    el('round-badge').textContent = String(data.round).padStart(2, '0');
    const rev = 'REV ' + String(data.round).padStart(2, '0');
    setTabTitle(tabDocName(data.doc_file), ...(data.mode === 'diff' ? ['diff', rev] : [rev]));
    el('review-cards').innerHTML  = '';
    initReview();
    el('processing-view').style.display = 'none';
    el('review-view').style.display     = '';
    el('btn-skip').disabled   = false;
    el('btn-submit').disabled = false;
  });
```

- [ ] **Step 6: Wire the SSE `complete` handler**

The `complete` handler runs for both review/diff and Q&A sessions (`connectSSE()` is called from all three init branches), so it must branch on which data global got populated. `REVIEW_DATA` is non-null only for review/diff sessions; `QA_DATA` is non-null only for Q&A sessions — so `REVIEW_DATA ? ... : null` correctly selects "doc name" vs. "no doc name" without needing a separate Q&A branch. Note this handler already declares a `const rev = ...` for a different purpose (sections-revised count) later in the function — do not reuse that name; the snippet below only adds a `setTabTitle` call and does not touch the existing `rev` declaration.

In `server.py`, insert one line right after `el('complete-view').style.display = '';` (currently `server.py:2451-2457`):

Old:
```javascript
  es.addEventListener('complete', e => {
    es.close(); // prevent onerror when server shuts down 2s later
    const data = JSON.parse(e.data);
    el('processing-view').style.display = 'none';
    el('review-view').style.display     = 'none';
    el('qa-view').style.display         = 'none';
    el('complete-view').style.display   = '';
    const r   = data.rounds_total     != null ? data.rounds_total    : '?';
```

New:
```javascript
  es.addEventListener('complete', e => {
    es.close(); // prevent onerror when server shuts down 2s later
    const data = JSON.parse(e.data);
    el('processing-view').style.display = 'none';
    el('review-view').style.display     = 'none';
    el('qa-view').style.display         = 'none';
    el('complete-view').style.display   = '';
    setTabTitle(REVIEW_DATA ? tabDocName(REVIEW_DATA.doc_file) : null, 'done');
    const r   = data.rounds_total     != null ? data.rounds_total    : '?';
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python3 tests/test_server_a11y.py`
Expected: `OK (9 tests)`

- [ ] **Step 8: Run the full test suite**

This change touches shared, widely-exercised code paths (the `/input` handler, the SSE `round`/`complete` handlers), so confirm nothing else regressed:

Run: `for f in tests/test_*.py; do python3 "$f" || echo "FAILED: $f"; done`
Expected: every file prints its own `OK (...)` line; no `FAILED:` lines.

- [ ] **Step 9: Manual browser verification**

Automated tests here only prove the JS source is wired correctly (string-match against `server.HTML`) — there is no DOM/browser test harness in this repo (see `tests/_server_harness.py`'s docstring: it drives the server over HTTP/SSE, not a browser), so `document.title` itself must be eyeballed in a real browser per this project's UI-change convention.

Create a throwaway fixture and launch the server directly:

```bash
mkdir -p /tmp/viva-title-check/.viva
cat > /tmp/viva-title-check/.viva/in1.json <<'EOF'
{
  "mode": "review",
  "doc_file": "docs/PRODUCT.md",
  "round": 1,
  "approved_ids": [],
  "sections": [
    {"id": "s1", "title": "Goals", "content": "goals body"}
  ]
}
EOF
cd /tmp/viva-title-check && python3 /Users/bryan/Projects/viva/.claude/worktrees/titles/server.py \
  --mode review --input .viva/in1.json --output .viva/out1.json
```

This prints a `http://127.0.0.1:<port>` URL and opens it in the default browser (omit `--no-browser` so it opens automatically, or add it and open the printed URL by hand). Confirm the tab reads `PRODUCT.md · REV 01 · viva`. Stop the server with Ctrl-C when done, and state explicitly in the task write-up that this was checked visually in a browser (not just via the string-match test), per this project's convention for UI changes.

- [ ] **Step 10: Commit**

```bash
git add server.py tests/test_server_a11y.py
git commit -m "feat(ui): identify the reviewed doc in the browser tab title"
```

---

## Self-Review Notes

- **Spec coverage:** Every row of the design doc's title-format table is covered — review round (Step 4/5), diff round (Step 4/5, including the mode-label difference and the SSE hardcoded-`review` bug fix), Q&A and Q&A-no-context (Step 4), review/diff complete and Q&A complete (Step 6). The `complete`-event detail (reading from `REVIEW_DATA`/`QA_DATA` rather than the `complete` payload itself, since `summary` doesn't carry `doc_file`/`context`) is implemented exactly as called out in the design doc's dedicated note. Fallback behavior (empty `doc_file`/`context` silently dropped) falls out of `setTabTitle`'s `filter(Boolean)` with no extra code. Non-goals (no schema/Python change, no `doc-title`/`doc-path` header change) are respected — every edit is confined to the embedded JS in `server.py` plus its test.
- **Placeholder scan:** No TBD/TODO; every step shows exact code, not a description of code.
- **Type consistency:** `tabDocName(path)` and `setTabTitle(...parts)` are named and called identically across all five call sites and the test assertions.
