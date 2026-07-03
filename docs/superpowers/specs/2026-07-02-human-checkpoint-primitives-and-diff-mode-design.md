# Design: Human-Checkpoint Primitives (#82) + Diff Mode (#85)

**Date:** 2026-07-02
**Issues:** #82 (human-checkpoint primitives), #85 (diff mode: hunk-by-hunk review of agent-written code)
**Status:** Approved for implementation

---

## Context

viva currently ships two human-gate modes — `review` (section-by-section markdown review) and `qa` (batch Q&A). The brainstorming integration consumes `qa` mode by patching the superpowers `brainstorming` skill in place; this is the fragility tracked in #64.

This spec covers two coordinated features:
- **#82** formalizes the invocation contract, ships `/viva-qa` as a viva-owned primitive, and removes the install.sh patch approach
- **#85** adds `--mode diff` (hunk-by-hunk code review) as the third server mode, with a `/viva-diff` skill

Both widen the product thesis deliberately: from "human checkpoints across a document's lifecycle" to "human checkpoints across an agent's artifact lifecycle." PRODUCT.md is updated as part of this work.

---

## Architecture

Two sequential deliverables in one implementation pass.

**#82 delivers:**
1. `scripts/schema.py` — merge from the `worktree-speed` branch, add Q&A TypedDicts + validators
2. `brainstorming-qa.md` — viva-owned skill exposed as `/viva-qa`; the Q&A primitive with a documented, versioned contract
3. PRODUCT.md update — commit the widened thesis; remove `scripts/install.sh`

**#85 delivers:**
1. `scripts/parse_diff.py` — reads `git diff` output, emits one `review-input` section per hunk
2. `--mode diff` in `server.py` — third server mode; reuses the full review SPA + submit/next-round/SSE loop unchanged; adds `highlight.js` via CDN for diff syntax coloring
3. `diff.md` — viva-owned skill exposed as `/viva-diff`; orchestrates the diff review loop

Everything already in the server (comments, anchors, open notes, attachments, ledger, SSE, `/next-round`, `/complete`) reuses unchanged.

---

## #82: Human-Checkpoint Primitives

### schema.py

Merge `scripts/schema.py` from `worktree-speed` (review shapes + boundary validators already written) and add Q&A shapes:

```python
class QAQuestion(TypedDict, total=False):
    id: str           # required
    text: str         # required
    hint: str         # optional — shown below the question text
    choices: List[str]  # optional — rendered as chip buttons

class QAInput(TypedDict, total=False):
    mode: str               # "qa"
    context: str            # one-liner shown in the title block
    questions: List[QAQuestion]

class QAAnswer(TypedDict, total=False):
    id: str           # question id
    choice: str       # selected chip value
    note: str         # free-text field value
    attachments: List[str]  # server-written image paths

class QAOutput(TypedDict, total=False):
    answers: List[QAAnswer]
    submitted_early: bool
```

Add `validate_qa_input(data)` — raises `ValueError` if any question is missing `id` or `text`.

Also add a `DiffInput` TypedDict: same shape as `ReviewInput` with `mode: "diff"`. Documents what `parse_diff.py` emits. No new validator needed — `validate_review_input()` works for diff input too (same required fields).

**Boundary rule:** `validate_qa_input()` is called in `server.py` at startup when `--mode qa` is given, matching the existing `validate_review_input()` pattern for review mode.

### /viva-qa skill

New file: `brainstorming-qa.md` in the viva plugin root. Exposed as `/viva-qa` by Claude Code's skill system.

**Contract (what callers must do):**

Write `.viva/qa-input.json` matching the `QAInput` shape, then invoke the skill:

```
/viva-qa
```

The skill:
1. Resolves `$VIVA_DIR` (same pattern as SKILL.md)
2. Clears `.viva/qa-input.json` guard: exits if `.viva/server.url` exists (prior session may be running)
3. Launches the server:
   ```bash
   python3 "$VIVA_DIR/server.py" --mode qa \
     --input .viva/qa-input.json --output .viva/answers.json
   ```
4. Waits for `.viva/answers.json` (same poll pattern as the review loop)
5. Reads and returns the answers — the skill's return value is the parsed `QAOutput` JSON

**Output:** `.viva/answers.json` matching `QAOutput`. Each `answer.attachments[]` is a list of server-written file paths; callers `Read` each path if present.

**What callers do NOT need to do:** Parse the server output themselves, manage the server lifecycle, or know anything about the browser UI. The skill owns the server.

### Brainstorming integration

`scripts/install.sh` is deleted. The README's "Brainstorming integration" section is replaced:

> The superpowers `brainstorming` skill natively calls `/viva-qa` for the Q&A phase when viva is installed. No install step required — install viva, and the brainstorming Q&A integration is active.

Mechanically: the brainstorming skill (in superpowers) writes `.viva/qa-input.json` and invokes `/viva-qa`. This is the inversion from #64's root cause: superpowers calls viva rather than viva patching superpowers.

**Migration path:** On first use after this change, if `.brainstorm-patch-version` exists, the user should re-run `git checkout` on the patched skill to restore the unpatched version. A warning in the README covers this.

### PRODUCT.md update

Update the thesis paragraph to reflect the widening:

> The product is the set of **human checkpoints across an agent's artifact lifecycle** — today the review checkpoint (section-by-section doc review), a brainstorm checkpoint (batch Q&A before the doc exists), and a diff checkpoint (hunk-by-hunk code review before a commit). Every feature either is one of those checkpoints or makes one cheaper to reach the right decision faster.

Update the feature map to add diff mode. Update "What we are NOT building" to confirm the fence lines hold (advisory-never-gating, local and keyless, no hosted service).

---

## #85: Diff Mode

### parse_diff.py

New script: `scripts/parse_diff.py`. Reads a patch file (or stdin), emits a `review-input` JSON with one section per hunk.

**Parsing rules:**

1. Split on `diff --git a/... b/...` lines — tracks the current file path
2. Within each file, split on `@@ -a,b +c,d @@` lines — each `@@` block is one hunk
3. Assign ids globally: `s1`, `s2`, ... (same as `parse_sections.py`)
4. Section title: `{filepath} hunk {N}` where N is the 1-based hunk index within that file
   - Line numbers are deliberately excluded from the title — they shift across rounds when other hunks change
   - Hunk index is stable as long as hunks don't merge or split (correct re-review if they do)
5. Section content: the hunk wrapped in a markdown fenced code block:
   ```
   ```diff
   @@ -42,8 +42,8 @@
    context line
   -removed line
   +added line
    context line
   ```
   ```
   The `@@ ... @@` header line is included in the content for display; it is NOT part of the title

**Carry-forward rule (round 2+):** Identical to `parse_sections.py` — a section carries as approved only when `section_key(title)` matches AND content is byte-for-byte identical. A modified hunk has different content → re-review required. An approved hunk whose context lines shift (from other changes) also gets a new content → re-review required. This is conservative and correct: "approve this exact change, not a shifted version of it."

**Special cases:**
- Binary file diff: emit one section per file with content `Binary file changed — no content to review` and require explicit human approval (same as any other section)
- Empty diff: exit non-zero with `parse_diff: no hunks found in diff` — caller handles the abort
- Deleted file: emit one section showing the full deletion diff
- New file: emit one section showing the full addition diff

**CLI:**
```bash
# Round 1
python3 "$VIVA_DIR/scripts/parse_diff.py" .viva/diff.patch \
  --output .viva/review-input-r1.json --round 1

# Round 2+
python3 "$VIVA_DIR/scripts/parse_diff.py" .viva/diff.patch \
  --output .viva/review-input-r2.json --round 2 \
  --prior-input .viva/review-input-r1.json \
  --prior-verdicts .viva/review-r1.json
```

No `--doc-file` or `--open-notes` flags in V1. Open notes across rounds is a future addition.

### server.py: --mode diff

Minimal changes to `server.py`:

1. `parse_args()`: add `"diff"` to `--mode` choices: `choices=["review", "qa", "diff"]`
2. `__main__`: for `--mode diff`, call `validate_review_input(_input_data)` (same validation as review mode — diff input is structurally identical)
3. Print: `viva · diff mode · {url}` (was `viva · {args.mode} mode · {url}`)

No changes to the server's HTTP handler or the Python-side state machine — diff mode reuses `POST /submit`, `POST /next-round`, `POST /complete`, and SSE exactly as review mode does.

### SPA: diff rendering

Minimal SPA changes:

**Add highlight.js CDN** (alongside the existing marked.js and DOMPurify tags):
```html
<script defer src="https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11/highlight.min.js"></script>
```
No language pack needed — highlight.js auto-detects `diff` from the fenced block's language tag.

**After markdown render**, call `hljs.highlightElement(el)` on any `<code class="language-diff">` blocks. Wire this into `renderMarkdown()`:
```js
function renderMarkdown(target, md) {
  if (window.marked) {
    const html = marked.parse(md);
    target.innerHTML = window.DOMPurify ? DOMPurify.sanitize(html) : html;
    if (window.hljs) {
      target.querySelectorAll('code[class^="language-"]').forEach(b => hljs.highlightElement(b));
    }
  } else {
    target.classList.add('md-raw');
    target.textContent = md;
  }
}
```

**Diff mode title block**: in `DOMContentLoaded`, add a third branch:
```js
} else if (data.mode === 'diff') {
  REVIEW_DATA = data;
  el('doc-path').textContent = data.doc_file || 'diff';
  el('doc-title').innerHTML  = 'viva <em>diff</em>';
  el('round-badge').textContent = String(data.round).padStart(2, '0');
  el('review-view').style.display = '';
  initReview();
  connectSSE();
}
```

`doc_file` for diff mode is passed to `parse_diff.py` via a `--doc-file` flag (same as `parse_sections.py`). The `/viva-diff` skill sets it to the ref argument, e.g. `HEAD~1..HEAD`. If omitted, `parse_diff.py` defaults to `"working tree"`.

**No other SPA changes.** The diff card is a standard review card whose section content is a fenced diff block. Comments, anchors, open thread UI, ledger — all unchanged.

### /viva-diff skill

New file: `diff.md` in the viva plugin root. Exposed as `/viva-diff`.

**Invocation:**
```
/viva-diff [ref]
```
`ref` is a git ref or range (e.g. `HEAD~1`, `HEAD~3..HEAD`, `main`). If omitted, defaults to unstaged changes (`git diff`).

**Loop:**

**1. Capture diff and launch**
```bash
VIVA_DIR=~/.claude/skills/viva
[ -f "$VIVA_DIR/server.py" ] || \
  VIVA_DIR=$(find ~/.claude/plugins/cache -name "server.py" -path "*/viva*" -maxdepth 6 2>/dev/null \
             | xargs -I{} dirname {} | head -1)

mkdir -p .viva
rm -f .viva/server.url .viva/review-input-r*.json .viva/review-r*.json
rm -rf .viva/attachments

git diff <ref> > .viva/diff.patch
[ -s .viva/diff.patch ] || { echo "viva-diff: no changes to review"; exit 0; }

python3 "$VIVA_DIR/scripts/parse_diff.py" .viva/diff.patch \
  --output .viva/review-input-r1.json --round 1 \
&& {
  python3 "$VIVA_DIR/server.py" --mode diff \
    --input .viva/review-input-r1.json --output .viva/review-r1.json &
  for i in $(seq 1 100); do [ -f .viva/server.url ] && break; sleep 0.1; done
}
[ -f .viva/server.url ] || { echo "viva-diff: launch failed"; exit 1; }
BASE=$(cat .viva/server.url)
```

**2. Wait for verdicts** — identical to SKILL.md step 2.

**3. Act on verdicts** — same hybrid rule as SKILL.md step 3, but the target is source files in the working tree rather than a single markdown doc. For a `changes` comment on hunk `{filepath} hunk N`: the agent applies the edit to `{filepath}` directly (the anchor text from the comment scopes the edit within the hunk). For an `info` comment: answer in the thread, do not edit the file.

**4. Re-diff and re-arm**
```bash
git diff <ref> > .viva/diff.patch
python3 "$VIVA_DIR/scripts/parse_diff.py" .viva/diff.patch \
  --output .viva/review-input-r{N+1}.json --round {N+1} \
  --prior-input .viva/review-input-r{N}.json \
  --prior-verdicts .viva/review-r{N}.json \
&& curl -s -X POST "$BASE/next-round?output=.viva/review-r{N+1}.json" \
     -H "Content-Type: application/json" -d @.viva/review-input-r{N+1}.json
```

**5. Finish** — same `/complete` call as SKILL.md step 5.

Sign-off report: "N hunks approved across M files over R rounds. K hunks revised." Then ask: "Commit these changes? (y/n)"

**No learned preferences in V1** — diff mode skips the step 5 preference recording. No `--open-notes` in V1 — open threads do not carry across rounds in diff mode.

---

## What This Is NOT

- **Not a replacement for `/code-review`.** `/code-review` runs an LLM pass over the diff. `/viva-diff` is a human gate — the reviewer reads each hunk and decides. They compose: run `/code-review` first, apply fixes, then `/viva-diff` for human sign-off.
- **Not autonomous.** The agent applies only what the reviewer explicitly requests. No LLM interpretation of "fix everything."
- **Not multi-file document review.** `/viva` reviews a single markdown doc. `/viva-diff` reviews a working-tree diff. They are separate entry points with separate loops.

---

## File Inventory

### New files
| File | Purpose |
|------|---------|
| `scripts/schema.py` | Shared contract (merge from worktree-speed + Q&A shapes) |
| `brainstorming-qa.md` | `/viva-qa` skill — the Q&A primitive |
| `diff.md` | `/viva-diff` skill — the diff review loop |
| `scripts/parse_diff.py` | Git diff → review-input JSON (one section per hunk) |

### Modified files
| File | Change |
|------|--------|
| `server.py` | Add `"diff"` to `--mode` choices; add `hljs` CDN + render call; add diff mode branch in `DOMContentLoaded` |
| `PRODUCT.md` | Update thesis + feature map (from worktree-speed draft, widened for diff) |
| `CLAUDE.md` | Update architecture section (from worktree-speed, note diff.md + parse_diff.py) |
| `DESIGN.md` | Add to `docs/` (from worktree-speed) |
| `README.md` | Update brainstorming integration section; add diff mode section |
| `SKILL.md` | Note that `/viva-qa` is now a separate callable primitive |

### Deleted files
| File | Reason |
|------|--------|
| `scripts/install.sh` | Replaced by the `/viva-qa` inversion |

---

## Tests

Each new script gets a test matching the existing pattern:

| Test file | What it covers |
|-----------|----------------|
| `tests/test_parse_diff.py` | Unit: hunk splitting, title/id assignment, carry-forward, binary files, empty diff |
| `tests/test_server_diff.py` | Integration: launch server in diff mode, submit verdicts, verify output |
| `tests/test_schema_qa.py` | Unit: `validate_qa_input` accepts valid, rejects missing id/text |

Existing tests for `schema.py` are extended to cover the Q&A validators.

---

## Open Questions (deferred to implementation)

- **Syntax highlighting theme**: Override `hljs-addition`/`hljs-deletion` in the SPA's style block to map to `--teal`/`--orange` respectively, matching viva's verdict palette. Suppress the default highlight.js theme entirely (no CDN theme stylesheet); only the diff language color overrides are added.
- **`/viva-diff` ref argument syntax**: Single positional arg vs. a `--ref` flag. Simpler for the agent to pass as a positional.
