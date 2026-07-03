# Human-Checkpoint Primitives (#82) + Diff Mode (#85) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formalize viva's Q&A primitive as `/viva-qa` (removing the fragile install.sh patch), then add hunk-by-hunk diff review as a third server mode with a `/viva-diff` skill.

**Architecture:** #82 lands first: `scripts/schema.py` (merged + Q&A shapes), `brainstorming-qa.md` skill, PRODUCT.md update. #85 builds directly on it: `scripts/parse_diff.py` emits one section per hunk, `--mode diff` in `server.py` reuses the full review SPA, and `diff.md` is the `/viva-diff` skill that orchestrates the loop.

**Tech Stack:** Python 3.8+ stdlib-only scripts; single-file server with embedded HTML/CSS/JS SPA; highlight.js 11 via CDN for diff syntax coloring; no pytest (tests run as `python3 tests/test_*.py`).

## Global Constraints

- Python 3.8+ compatibility required — use `from __future__ import annotations` in all new `.py` files
- stdlib-only — no third-party Python packages in any script or server.py
- No pytest — each test file has a `main()` and can be run as `python3 tests/test_name.py`; imports only stdlib and siblings
- CDN scripts (marked.js, DOMPurify, highlight.js) use `defer` attribute; never block render
- Tests follow the existing pattern: `ROOT = Path(__file__).resolve().parent.parent` for the project root; subprocess + `urllib` for server integration tests
- `_atomic_write` (tmp + `os.replace`) is the only allowed write path for any `.viva/*.json` file
- All new skill files live at `.claude/skills/viva/<name>.md` with frontmatter `name:` matching the desired slash-command name

---

## File Structure

### New files
| Path | Responsibility |
|------|---------------|
| `scripts/schema.py` | Shared contract: `section_key`, ledger rule, TypedDicts, boundary validators for review, Q&A, and diff |
| `.claude/skills/viva/brainstorming-qa.md` | `/viva-qa` skill — Q&A primitive |
| `.claude/skills/viva/diff.md` | `/viva-diff` skill — diff review loop |
| `scripts/parse_diff.py` | Reads `git diff` patch → emits `review-input` JSON (one section per hunk) |
| `tests/test_schema_qa.py` | Unit tests for `validate_qa_input` |
| `tests/test_parse_diff.py` | Unit tests for `parse_diff.py` |
| `tests/test_server_diff.py` | Integration tests for `--mode diff` |

### Modified files
| Path | Change |
|------|--------|
| `server.py` | Import `schema.py`; add `"diff"` to `--mode` choices; add boundary validation; add highlight.js CDN + CSS overrides; add diff mode branch in `DOMContentLoaded`; update `renderMarkdown` for hljs |
| `PRODUCT.md` | Create from `worktree-speed` branch content, update thesis + feature map for diff mode |
| `CLAUDE.md` | Create from `worktree-speed` branch content, add `parse_diff.py` and skill files to architecture section |
| `DESIGN.md` | Create from `worktree-speed` branch content (no edits needed) |
| `README.md` | Update brainstorming section; add diff mode section |
| `SKILL.md` | Add note that `/viva-qa` is now a separate callable primitive |

### Deleted files
| Path | Reason |
|------|--------|
| `scripts/install.sh` | Replaced by the `/viva-qa` inversion |

---

## Task 1: schema.py — shared contract + Q&A validators + server wiring

**Files:**
- Create: `scripts/schema.py`
- Modify: `server.py` (lines 20–21, 2435, 2687–2695)
- Create: `tests/test_schema_qa.py`

**Interfaces:**
- Produces: `section_key(title: str) -> str`, `validate_review_input(data: dict) -> None`, `validate_qa_input(data: dict) -> None` — used by `parse_diff.py` (Task 4) and `server.py` (this task)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schema_qa.py
#!/usr/bin/env python3
"""Unit tests for validate_qa_input in scripts/schema.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from schema import validate_qa_input


def test_valid_qa_input_passes():
    validate_qa_input({
        "mode": "qa",
        "context": "test topic",
        "questions": [{"id": "q1", "text": "Which?", "choices": ["A", "B"]}],
    })
    print("test_valid_qa_input_passes: OK")


def test_missing_questions_key_raises():
    try:
        validate_qa_input({"mode": "qa"})
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "questions" in str(e).lower(), str(e)
    print("test_missing_questions_key_raises: OK")


def test_question_missing_id_raises():
    try:
        validate_qa_input({"questions": [{"text": "What?"}]})
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "id" in str(e).lower(), str(e)
    print("test_question_missing_id_raises: OK")


def test_question_missing_text_raises():
    try:
        validate_qa_input({"questions": [{"id": "q1"}]})
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "text" in str(e).lower(), str(e)
    print("test_question_missing_text_raises: OK")


def test_empty_questions_list_is_valid():
    validate_qa_input({"questions": []})
    print("test_empty_questions_list_is_valid: OK")


def test_question_without_choices_is_valid():
    # choices are optional in the schema
    validate_qa_input({"questions": [{"id": "q1", "text": "Open question?"}]})
    print("test_question_without_choices_is_valid: OK")


def main() -> None:
    test_valid_qa_input_passes()
    test_missing_questions_key_raises()
    test_question_missing_id_raises()
    test_question_missing_text_raises()
    test_empty_questions_list_is_valid()
    test_question_without_choices_is_valid()
    print("\nAll schema QA tests passed.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
python3 tests/test_schema_qa.py
```
Expected: `ModuleNotFoundError: No module named 'schema'`

- [ ] **Step 3: Write scripts/schema.py**

```python
#!/usr/bin/env python3
"""Shared schema contract for viva's `.viva/*.json` round files.

This is the one module `scripts/*.py` and `server.py` import to agree on the
load-bearing pieces of the protocol:

- **Section identity** — `section_key()`, the single normalization used to match
  a section across rounds (approvals, carried annotations, diffs, open threads).
- **The ledger rule** — `verdict_to_ledger_entry()`, the single source of truth
  for which verdicts become a Revision-History row and how the note is derived.
- **The round shapes** — `TypedDict`s documenting `review-input-r{N}.json` and
  `review-r{N}.json` (documentation only; CI runs no type checker).
- **Boundary validation** — `validate_review_input()` / `validate_verdicts()` /
  `validate_qa_input()`, called where data enters the system so a missed
  producer fails loudly instead of silently corrupting a downstream reader.

stdlib-only, no runtime dependency. It is the single shared sibling: every other
script stays standalone and imports nothing but this.

`GET /input` shape note: the server serves the review-input merged with a live
`ledger: [...]` key — `json.dumps({**_input_data, "ledger": _ledger})`. That
`ledger` field is injected by the server at serve time and is **not** part of
the `review-input-r{N}.json` file schema the `ReviewInput` TypedDict describes.
"""
from __future__ import annotations

from typing import List, Optional, TypedDict

# Verdicts that earn a Revision-History ledger row. `approved`/`pending` do not.
LEDGER_VERDICTS = ("changes", "info")
# Every verdict a review output section may carry.
VERDICTS = ("approved", "changes", "info", "pending")


# ── Section identity ──────────────────────────────────────────────────────────
def section_key(title: str) -> str:
    """Canonical section identity: case-folded, edge-trimmed title.

    The ONE normalization that matches a section across rounds — approvals,
    carried annotations, round diffs, and open-note threads all key on it, so a
    title edit changes identity in exactly one place.

    Deliberately distinct from `checklist.py`'s `_norm`, which strips *all*
    non-alphanumeric characters for tolerant template matching. That is a fuzzy
    match, this is an identity; do not merge the two.
    """
    return (title or "").strip().lower()


# ── Ledger rule ───────────────────────────────────────────────────────────────
def is_ledger_verdict(verdict: object) -> bool:
    """True iff a section's verdict earns a ledger row."""
    return verdict in LEDGER_VERDICTS


def ledger_note(section: dict) -> str:
    """The verbatim note for a ledger row.

    Multi-comment sections carry their notes in `comments[]`; joined with ` · `.
    Older single-note sections fall back to the section's own `note`.
    """
    comments = section.get("comments") or []
    if comments:
        return " · ".join(c.get("note", "") for c in comments if c.get("note"))
    return section.get("note", "")


def verdict_to_ledger_entry(
    rnd: int, section_title: str, section: dict
) -> Optional[dict]:
    """Single source of truth for one ledger row.

    Returns `{round, section_title, verdict, note}` for a `changes`/`info`
    section, or `None` if the verdict earns no row.
    """
    if not is_ledger_verdict(section.get("verdict")):
        return None
    return {
        "round": rnd,
        "section_title": section_title,
        "verdict": section["verdict"],
        "note": ledger_note(section),
    }


# ── Round shapes (documentation-only TypedDicts) ──────────────────────────────
class Annotation(TypedDict, total=False):
    kind: str       # required — producer tag / badge label
    severity: str   # required — info | warn | error
    message: str    # required — inline text
    anchor: str     # overloaded: hover title OR another section's id (deep-link)
    basis: str      # confidence only — sourced | inferred
    level: str      # confidence only — high | medium | low


class ReviewSection(TypedDict, total=False):
    id: str                         # required — stable id (s1, s2, …)
    title: str                      # required — heading text
    content: str                    # required — verbatim markdown
    annotations: List[Annotation]   # optional — advisory badges
    diff: dict                      # optional — round-to-round change
    open_notes: list                # optional — carried-forward threads


class ReviewInput(TypedDict, total=False):
    mode: str                       # "review"
    doc_file: str                   # relative path for the UI
    round: int                      # round number
    approved_ids: List[str]         # ids approved in prior rounds
    sections: List[ReviewSection]


class DiffInput(TypedDict, total=False):
    """Diff-mode input — same structure as ReviewInput, mode='diff'."""
    mode: str                       # "diff"
    doc_file: str                   # ref description shown in UI
    round: int
    approved_ids: List[str]
    sections: List[ReviewSection]   # one entry per hunk


class SectionVerdict(TypedDict, total=False):
    id: str        # required — section id
    verdict: str   # required — one of VERDICTS
    comments: list # optional — typed comment threads


class ReviewOutput(TypedDict, total=False):
    round: int
    submitted_early: bool
    sections: List[SectionVerdict]


# ── Q&A round shapes ──────────────────────────────────────────────────────────
class QAQuestion(TypedDict, total=False):
    id: str           # required
    text: str         # required
    hint: str         # optional — shown below the question
    choices: List[str]  # optional — rendered as chip buttons


class QAInput(TypedDict, total=False):
    mode: str                   # "qa"
    context: str                # one-liner shown in the title block
    questions: List[QAQuestion]


class QAAnswer(TypedDict, total=False):
    id: str               # question id
    choice: str           # selected chip value
    note: str             # free-text field value
    attachments: List[str]  # server-written image paths


class QAOutput(TypedDict, total=False):
    answers: List[QAAnswer]
    submitted_early: bool


# ── Boundary validation ───────────────────────────────────────────────────────
def validate_review_input(data: dict) -> None:
    """Raise `ValueError` if `data` is not a structurally valid review-input.

    Enforces only the load-bearing invariants. Also valid for diff-mode input
    (same required fields). Call at the boundary: parse scripts on write,
    server.py on read.
    """
    if not isinstance(data, dict):
        raise ValueError("review-input must be a JSON object")
    sections = data.get("sections")
    if not isinstance(sections, list):
        raise ValueError("review-input.sections must be a list")
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            raise ValueError(f"review-input.sections[{i}] must be an object")
        for field in ("id", "title", "content"):
            if not isinstance(s.get(field), str):
                raise ValueError(
                    f"review-input.sections[{i}] missing required string {field!r}"
                )


def validate_verdicts(data: dict) -> None:
    """Raise `ValueError` if `data` is not a structurally valid review output."""
    if not isinstance(data, dict):
        raise ValueError("review output must be a JSON object")
    sections = data.get("sections")
    if not isinstance(sections, list):
        raise ValueError("review output.sections must be a list")
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            raise ValueError(f"review output.sections[{i}] must be an object")
        if not isinstance(s.get("id"), str):
            raise ValueError(f"review output.sections[{i}] missing required string 'id'")
        if s.get("verdict") not in VERDICTS:
            raise ValueError(
                f"review output.sections[{i}] has invalid verdict {s.get('verdict')!r}"
            )


def validate_qa_input(data: dict) -> None:
    """Raise `ValueError` if `data` is not a structurally valid Q&A input.

    Enforces that every question has `id` and `text`. Permissive about optional
    fields (`hint`, `choices`, `context`). Call at startup when `--mode qa`.
    """
    if not isinstance(data, dict):
        raise ValueError("qa-input must be a JSON object")
    questions = data.get("questions")
    if not isinstance(questions, list):
        raise ValueError("qa-input.questions must be a list")
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            raise ValueError(f"qa-input.questions[{i}] must be an object")
        for field in ("id", "text"):
            if not isinstance(q.get(field), str):
                raise ValueError(
                    f"qa-input.questions[{i}] missing required string {field!r}"
                )
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
python3 tests/test_schema_qa.py
```
Expected output:
```
test_valid_qa_input_passes: OK
test_missing_questions_key_raises: OK
test_question_missing_id_raises: OK
test_question_missing_text_raises: OK
test_empty_questions_list_is_valid: OK
test_question_without_choices_is_valid: OK

All schema QA tests passed.
```

- [ ] **Step 5: Wire schema.py into server.py — import**

In `server.py`, after the existing imports block (after `from urllib.parse import parse_qs, urlparse` at line ~24), add:

```python
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts'))
from schema import validate_review_input, validate_qa_input
```

- [ ] **Step 6: Wire schema.py into server.py — validation call**

In `server.py`, in the `__main__` block, after `_input_data = load_input(args.input)`, add:

```python
    if args.mode in ("review", "diff"):
        try:
            validate_review_input(_input_data)
        except ValueError as e:
            print(f"viva · invalid input: {e}", file=sys.stderr, flush=True)
            sys.exit(1)
    elif args.mode == "qa":
        try:
            validate_qa_input(_input_data)
        except ValueError as e:
            print(f"viva · invalid qa-input: {e}", file=sys.stderr, flush=True)
            sys.exit(1)
```

- [ ] **Step 7: Verify server still starts cleanly for review mode**

```bash
python3 tests/test_server_ledger.py
```
Expected: `OK` (all existing assertions pass)

- [ ] **Step 8: Commit**

```bash
git add scripts/schema.py tests/test_schema_qa.py server.py
git commit -m "feat: add scripts/schema.py with QA shapes and wire boundary validation into server.py (#82)"
```

---

## Task 2: /viva-qa skill

**Files:**
- Create: `.claude/skills/viva/brainstorming-qa.md`

**Interfaces:**
- Consumes: `server.py --mode qa` (Task 1), `.viva/qa-input.json` (caller-written)
- Produces: `.viva/answers.json` (`QAOutput` shape from `schema.py`)

- [ ] **Step 1: Create the skill file**

```markdown
<!-- .claude/skills/viva/brainstorming-qa.md -->
---
name: viva-qa
description: Batch Q&A human gate. Present structured questions in the browser and collect answers. Write .viva/qa-input.json (QAInput shape) before invoking.
---

# viva-qa

Batch Q&A human gate. Write the questions to `.viva/qa-input.json`, invoke
`/viva-qa`, and read the answers from `.viva/answers.json`.

## Input contract

The caller writes `.viva/qa-input.json` before invoking this skill:

```json
{
  "mode": "qa",
  "context": "One-sentence description shown in the title block",
  "questions": [
    {
      "id": "q1",
      "text": "The question text",
      "hint": "Optional elaboration shown below the question",
      "choices": ["Choice A", "Choice B", "Choice C"]
    }
  ]
}
```

`choices` is optional — omitting it renders a free-text field only.

## Output contract

`.viva/answers.json` written by the server after the human submits:

```json
{
  "answers": [
    {"id": "q1", "choice": "Choice A", "note": "", "attachments": []}
  ],
  "submitted_early": false
}
```

If an answer carries an `attachments` array, `Read` each listed image path before
incorporating that answer — the image is context for how you use the answer.

## Steps

**1. Resolve skill dir and guard**

```bash
VIVA_DIR=~/.claude/skills/viva
[ -f "$VIVA_DIR/server.py" ] || \
  VIVA_DIR=$(find ~/.claude/plugins/cache -name "server.py" -path "*/viva*" -maxdepth 6 2>/dev/null \
             | xargs -I{} dirname {} | head -1)
[ -f "$VIVA_DIR/server.py" ] || { echo "viva-qa: cannot locate server.py"; exit 1; }

[ -f .viva/server.url ] && { echo "viva-qa: a prior session may still be running (.viva/server.url exists). Delete it if the server is stopped."; exit 1; }
[ -f .viva/qa-input.json ] || { echo "viva-qa: .viva/qa-input.json not found — write it before invoking /viva-qa"; exit 1; }
```

**2. Launch and wait for server**

```bash
mkdir -p .viva
rm -f .viva/answers.json

python3 "$VIVA_DIR/server.py" --mode qa \
  --input .viva/qa-input.json --output .viva/answers.json &
for i in $(seq 1 100); do [ -f .viva/server.url ] && break; sleep 0.1; done
[ -f .viva/server.url ] || { echo "viva-qa: server start failed"; exit 1; }
```

**3. Wait for answers**

```bash
until [ -f .viva/answers.json ]; do sleep 0.3; done
cat .viva/answers.json
```

Read `.viva/answers.json`. For each answer with an `attachments` field, `Read`
each listed path before acting on the answer.
```

- [ ] **Step 2: Verify the file is visible to Claude Code as a skill**

```bash
ls .claude/skills/viva/
```
Expected: both `SKILL.md` and `brainstorming-qa.md` appear.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/viva/brainstorming-qa.md
git commit -m "feat: ship /viva-qa skill — Q&A primitive with documented contract (#82)"
```

---

## Task 3: Docs + cleanup

**Files:**
- Create: `PRODUCT.md`, `CLAUDE.md`, `DESIGN.md` (ported from `worktree-speed` branch)
- Modify: `README.md`, `SKILL.md`
- Delete: `scripts/install.sh`

- [ ] **Step 1: Create PRODUCT.md**

Port from `worktree-speed` branch with the thesis and feature map updated for diff mode and the brainstorming install fragility removed from known problems (it is now resolved):

```bash
git show worktree-speed:PRODUCT.md > PRODUCT.md
```

Then edit the thesis paragraph — replace:

```
The product is the set of **human checkpoints across a document's lifecycle** —
today the review checkpoint (the core loop) and a brainstorm checkpoint (batch
Q&A before the doc exists). Every feature either is one of those checkpoints or
makes one cheaper to reach the right decision faster. A new feature earns its
place by serving a checkpoint; one that fits neither belongs to a different
product.
```

with:

```
The product is the set of **human checkpoints across an agent's artifact
lifecycle** — today the review checkpoint (section-by-section doc review), a
brainstorm checkpoint (batch Q&A before the doc exists), and a diff checkpoint
(hunk-by-hunk code review before a commit). Every feature either is one of those
checkpoints or makes one cheaper to reach the right decision faster. A new
feature earns its place by serving a checkpoint; one that fits neither belongs to
a different product.
```

Add to the feature map (after "Brainstorming Q&A"):

```
- Diff review: hunk-by-hunk review of agent-written code before commit (/viva-diff)
```

Remove from "Known problems":

```
- **Brainstorming install is fragile.** ...
```

Replace with:

```
- **README lags the product.** User-facing README documents only the core loop; several shipped feature clusters (annotations, producers, confidence triage, open notes, learned preferences, diff review) are undocumented there.
```

- [ ] **Step 2: Create CLAUDE.md**

```bash
git show worktree-speed:CLAUDE.md > CLAUDE.md
```

Then add `parse_diff.py` and the two skill files to the architecture section.

In the scripts list (item 2), after `revision_history`), change the list to read:
```
(`parse_sections`, `parse_diff`, `annotate`, `drift`, `checklist`, `open_notes`,
`preferences`, `revision_history`)
```

Add after the `schema.py` description block:

```
**New skills in this branch:** `.claude/skills/viva/brainstorming-qa.md`
(`/viva-qa` primitive) and `.claude/skills/viva/diff.md` (`/viva-diff` skill)
follow the same import-only-schema rule.
```

- [ ] **Step 3: Create DESIGN.md**

```bash
git show worktree-speed:DESIGN.md > DESIGN.md
```

No edits needed.

- [ ] **Step 4: Update README.md — brainstorming section**

Replace the entire "## Brainstorming integration" section with:

```markdown
## Brainstorming integration

viva adds a batch Q&A phase to the `brainstorming` skill via the `/viva-qa`
primitive. When viva is installed, the brainstorming skill calls `/viva-qa`
directly — no install step or patching required.

To collect Q&A answers from your own skills, write `.viva/qa-input.json` and
invoke `/viva-qa`:

```json
{
  "mode": "qa",
  "context": "Topic shown in the title block",
  "questions": [
    {"id": "q1", "text": "Which approach?", "choices": ["A", "B", "C"]}
  ]
}
```

See `.claude/skills/viva/brainstorming-qa.md` for the full contract.
```

- [ ] **Step 5: Update README.md — add diff mode section**

After the "## Verdicts" section, add:

```markdown
## Diff mode

`/viva-diff` reviews a git diff hunk-by-hunk before a commit:

```
/viva-diff [ref]
```

`ref` is a git ref or range (`HEAD~1`, `main..feature`, etc.). Omit for
unstaged working-tree changes. Each hunk becomes one review card with the
same comment, anchor, and attachment support as document review. Approved
hunks collapse; revised hunks re-present with a within-hunk diff. Sign-off
produces a ledger formatted for a commit body or PR description.

Diff mode is a separate gate from `/code-review` (which is an LLM pass).
They compose: run `/code-review` first, apply its suggestions, then
`/viva-diff` for human sign-off before committing.
```

- [ ] **Step 6: Update SKILL.md — note /viva-qa**

After the "## Setup" section in SKILL.md, add or update the brainstorming note:

```markdown
## Brainstorming Q&A

viva exposes a `/viva-qa` skill for batch Q&A sessions. The superpowers
`brainstorming` skill calls `/viva-qa` directly when viva is installed — no
`install.sh` patch is needed. See `.claude/skills/viva/brainstorming-qa.md`
for the full invocation contract.
```

Remove the existing "## Setup" section that describes running `install.sh`.

- [ ] **Step 7: Delete install.sh**

```bash
git rm scripts/install.sh
```

- [ ] **Step 8: Commit**

```bash
git add PRODUCT.md CLAUDE.md DESIGN.md README.md SKILL.md
git commit -m "feat: update docs + PRODUCT.md thesis widening; remove install.sh (#82)"
```

---

## Task 4: parse_diff.py

**Files:**
- Create: `scripts/parse_diff.py`
- Create: `tests/test_parse_diff.py`

**Interfaces:**
- Consumes: `section_key`, `validate_review_input` from `scripts/schema.py` (Task 1)
- Produces: CLI `python3 scripts/parse_diff.py <patch> --output <path> --round <N> [--doc-file STR] [--prior-input <path>] [--prior-verdicts <path>]`; writes `review-input` JSON with `mode: "diff"`, one section per hunk

- [ ] **Step 1: Write the failing tests**

```python
#!/usr/bin/env python3
# tests/test_parse_diff.py
"""Unit tests for scripts/parse_diff.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = str(ROOT / "scripts" / "parse_diff.py")
PYTHON = sys.executable

SIMPLE_DIFF = """\
diff --git a/foo.py b/foo.py
index abc1234..def5678 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 line 1
-old line
+new line
+extra line
 line 3
"""

TWO_HUNK_DIFF = """\
diff --git a/bar.py b/bar.py
index abc1234..def5678 100644
--- a/bar.py
+++ b/bar.py
@@ -1,3 +1,3 @@
 context
-old
+new
 context
@@ -10,3 +10,3 @@
 context2
-removed
+added
 context2
"""

BINARY_DIFF = """\
diff --git a/img.png b/img.png
index abc1234..def5678 100644
Binary files a/img.png and b/img.png differ
"""

TWO_FILE_DIFF = SIMPLE_DIFF + """\
diff --git a/bar.py b/bar.py
index abc1234..def5678 100644
--- a/bar.py
+++ b/bar.py
@@ -5,3 +5,4 @@
 x = 1
-y = 2
+y = 3
+z = 4
 w = 5
"""


def _run(patch_content: str, extra_args: list[str] | None = None):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        patch = tmp / "diff.patch"
        out = tmp / "out.json"
        patch.write_text(patch_content, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(out), "--round", "1"]
            + (extra_args or []),
            capture_output=True,
            text=True,
        )
        data = json.loads(out.read_text()) if out.exists() else None
        return result.returncode, data, result.stderr


def test_single_hunk():
    rc, data, _ = _run(SIMPLE_DIFF)
    assert rc == 0
    assert data["mode"] == "diff"
    assert data["round"] == 1
    assert len(data["sections"]) == 1
    s = data["sections"][0]
    assert s["id"] == "s1"
    assert s["title"] == "foo.py hunk 1"
    assert s["content"].startswith("```diff")
    assert "@@ -1,3 +1,4 @@" in s["content"]
    assert "-old line" in s["content"]
    assert "+new line" in s["content"]
    assert s["content"].strip().endswith("```")
    print("test_single_hunk: OK")


def test_two_hunks_same_file():
    rc, data, _ = _run(TWO_HUNK_DIFF)
    assert rc == 0
    assert len(data["sections"]) == 2
    assert data["sections"][0]["title"] == "bar.py hunk 1"
    assert data["sections"][1]["title"] == "bar.py hunk 2"
    assert data["sections"][0]["id"] == "s1"
    assert data["sections"][1]["id"] == "s2"
    print("test_two_hunks_same_file: OK")


def test_two_files_ids_are_global():
    rc, data, _ = _run(TWO_FILE_DIFF)
    assert rc == 0
    assert len(data["sections"]) == 2
    assert data["sections"][0]["title"] == "foo.py hunk 1"
    assert data["sections"][1]["title"] == "bar.py hunk 1"
    # ids must be globally sequential, not reset per file
    assert data["sections"][0]["id"] == "s1"
    assert data["sections"][1]["id"] == "s2"
    print("test_two_files_ids_are_global: OK")


def test_binary_file_requires_explicit_approval():
    rc, data, _ = _run(BINARY_DIFF)
    assert rc == 0
    assert len(data["sections"]) == 1
    s = data["sections"][0]
    assert s["title"] == "img.png hunk 1"
    assert "Binary file changed" in s["content"]
    # binary sections are NOT in approved_ids — they require explicit approval
    assert s["id"] not in data["approved_ids"]
    print("test_binary_file_requires_explicit_approval: OK")


def test_empty_diff_exits_nonzero():
    rc, _, stderr = _run("")
    assert rc != 0
    assert "no hunks found" in stderr
    print("test_empty_diff_exits_nonzero: OK")


def test_doc_file_in_output():
    rc, data, _ = _run(SIMPLE_DIFF, ["--doc-file", "HEAD~1..HEAD"])
    assert rc == 0
    assert data["doc_file"] == "HEAD~1..HEAD"
    print("test_doc_file_in_output: OK")


def test_default_doc_file():
    rc, data, _ = _run(SIMPLE_DIFF)
    assert rc == 0
    assert data["doc_file"] == "working tree"
    print("test_default_doc_file: OK")


def test_carry_forward_identical_content():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        patch = tmp / "diff.patch"
        r1_input = tmp / "r1-input.json"
        r1_verdicts = tmp / "r1-verdicts.json"
        r2_input = tmp / "r2-input.json"

        patch.write_text(SIMPLE_DIFF, encoding="utf-8")

        subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r1_input), "--round", "1"],
            check=True, capture_output=True,
        )

        r1_verdicts.write_text(json.dumps({
            "round": 1,
            "sections": [{"id": "s1", "verdict": "approved"}],
        }))

        result = subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r2_input), "--round", "2",
             "--prior-input", str(r1_input), "--prior-verdicts", str(r1_verdicts)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        r2 = json.loads(r2_input.read_text())
        assert "s1" in r2["approved_ids"], f"s1 should carry forward; got {r2['approved_ids']}"
        print("test_carry_forward_identical_content: OK")


def test_carry_forward_changed_content_requires_review():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        patch = tmp / "diff.patch"
        r1_input = tmp / "r1-input.json"
        r1_verdicts = tmp / "r1-verdicts.json"
        r2_input = tmp / "r2-input.json"

        patch.write_text(SIMPLE_DIFF, encoding="utf-8")
        subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r1_input), "--round", "1"],
            check=True, capture_output=True,
        )
        r1_verdicts.write_text(json.dumps({
            "round": 1,
            "sections": [{"id": "s1", "verdict": "approved"}],
        }))

        # Simulate agent modifying the hunk (different content)
        modified = SIMPLE_DIFF.replace("+extra line", "+DIFFERENT extra line")
        patch.write_text(modified, encoding="utf-8")

        result = subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r2_input), "--round", "2",
             "--prior-input", str(r1_input), "--prior-verdicts", str(r1_verdicts)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        r2 = json.loads(r2_input.read_text())
        assert "s1" not in r2["approved_ids"], "changed hunk must not carry forward as approved"
        print("test_carry_forward_changed_content_requires_review: OK")


def main() -> None:
    test_single_hunk()
    test_two_hunks_same_file()
    test_two_files_ids_are_global()
    test_binary_file_requires_explicit_approval()
    test_empty_diff_exits_nonzero()
    test_doc_file_in_output()
    test_default_doc_file()
    test_carry_forward_identical_content()
    test_carry_forward_changed_content_requires_review()
    print("\nAll parse_diff tests passed.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
python3 tests/test_parse_diff.py
```
Expected: `No such file or directory: '.../scripts/parse_diff.py'`

- [ ] **Step 3: Write scripts/parse_diff.py**

```python
#!/usr/bin/env python3
"""Parse git diff output into viva review-input JSON (one section per hunk).

Round 1:
  python3 parse_diff.py .viva/diff.patch \\
    --output .viva/review-input-r1.json --round 1 \\
    [--doc-file "HEAD~1..HEAD"]

Round 2+:
  python3 parse_diff.py .viva/diff.patch \\
    --output .viva/review-input-r2.json --round 2 \\
    --prior-input .viva/review-input-r1.json \\
    --prior-verdicts .viva/review-r1.json \\
    [--doc-file "HEAD~1..HEAD"]

Exits non-zero if the patch file cannot be read, if it contains no parseable
hunks, or if prior round files are specified but cannot be read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import section_key, validate_review_input


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Parse git diff into viva review-input JSON"
    )
    p.add_argument("patch", help="Path to a git diff patch file")
    p.add_argument("--output", required=True, help="Path to write review-input JSON")
    p.add_argument("--round", type=int, required=True, dest="round_num")
    p.add_argument("--doc-file", default="working tree",
                   help="Description shown in the UI title block")
    p.add_argument("--prior-input",
                   help="Prior round review-input JSON (round 2+)")
    p.add_argument("--prior-verdicts",
                   help="Prior round verdicts JSON (round 2+)")
    return p.parse_args()


def _extract_filepath(file_block: str) -> str | None:
    """Extract the target filepath from a single file-level diff block."""
    # "+++ b/path" — covers modified and new files
    m = re.search(r'^\+\+\+ b/(.+)$', file_block, re.MULTILINE)
    if m:
        return m.group(1).rstrip()
    # "--- a/path" — covers deleted files (where +++ is /dev/null)
    m = re.search(r'^--- a/(.+)$', file_block, re.MULTILINE)
    if m and m.group(1).strip() != '/dev/null':
        return m.group(1).rstrip()
    # Fallback: "diff --git a/X b/X"
    m = re.match(r'diff --git a/(.+?) b/', file_block)
    if m:
        return m.group(1).rstrip()
    return None


def _is_binary(file_block: str) -> bool:
    """True if the file block has no hunk headers (binary or pure metadata)."""
    return not re.search(r'^@@ ', file_block, re.MULTILINE)


def _parse_hunks(file_block: str, filepath: str, start_id: int) -> list[dict]:
    """Split one file's diff block into per-hunk section dicts."""
    sections: list[dict] = []
    # Split at every "@@ ... @@" line, keeping the delimiter
    parts = re.split(r'^(?=@@ )', file_block, flags=re.MULTILINE)
    hunk_index = 0
    current_id = start_id
    for part in parts:
        if not part.startswith('@@'):
            continue
        hunk_index += 1
        sections.append({
            "id": f"s{current_id}",
            "title": f"{filepath} hunk {hunk_index}",
            "content": f"```diff\n{part.rstrip()}\n```",
        })
        current_id += 1
    return sections


def parse_diff(text: str) -> list[dict]:
    """Parse a full git diff text into a flat list of section dicts."""
    sections: list[dict] = []
    # Split on "diff --git" file headers
    file_blocks = re.split(r'^(?=diff --git )', text, flags=re.MULTILINE)
    for file_block in file_blocks:
        if not file_block.strip():
            continue
        filepath = _extract_filepath(file_block)
        if filepath is None:
            continue
        start_id = len(sections) + 1
        if _is_binary(file_block):
            sections.append({
                "id": f"s{start_id}",
                "title": f"{filepath} hunk 1",
                "content": "Binary file changed — no content to review",
            })
        else:
            sections.extend(_parse_hunks(file_block, filepath, start_id))
    return sections


def _carry_forward(
    sections: list[dict],
    prior_input: dict | None,
    prior_verdicts: dict | None,
) -> list[str]:
    """Return ids of sections approved in the prior round with byte-identical content.

    A section carries forward as approved only when its normalized title matches
    a prior section AND that prior section was approved AND the content is
    byte-for-byte identical. This is the same rule parse_sections.py uses.
    """
    if not prior_input or not prior_verdicts:
        return []

    prior_by_key: dict[str, dict] = {}
    for s in prior_input.get("sections", []):
        k = section_key(s.get("title", ""))
        prior_by_key[k] = s

    prior_approved_ids = {
        sv.get("id")
        for sv in prior_verdicts.get("sections", [])
        if sv.get("verdict") == "approved"
    }

    approved_ids: list[str] = []
    for s in sections:
        k = section_key(s["title"])
        prior_s = prior_by_key.get(k)
        if (
            prior_s is not None
            and prior_s.get("id") in prior_approved_ids
            and s["content"] == prior_s.get("content")
        ):
            approved_ids.append(s["id"])
    return approved_ids


def _atomic_write(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def main() -> int:
    args = _parse_args()

    try:
        text = Path(args.patch).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"parse_diff: cannot read {args.patch}: {e}", file=sys.stderr)
        return 1

    sections = parse_diff(text)
    if not sections:
        print("parse_diff: no hunks found in diff", file=sys.stderr)
        return 1

    prior_input: dict | None = None
    prior_verdicts: dict | None = None

    if args.prior_input:
        try:
            prior_input = json.loads(
                Path(args.prior_input).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as e:
            print(f"parse_diff: cannot read prior-input: {e}", file=sys.stderr)
            return 1

    if args.prior_verdicts:
        try:
            prior_verdicts = json.loads(
                Path(args.prior_verdicts).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as e:
            print(f"parse_diff: cannot read prior-verdicts: {e}", file=sys.stderr)
            return 1

    approved_ids = _carry_forward(sections, prior_input, prior_verdicts)

    data: dict = {
        "mode": "diff",
        "doc_file": args.doc_file,
        "round": args.round_num,
        "approved_ids": approved_ids,
        "sections": sections,
    }
    validate_review_input(data)
    _atomic_write(args.output, json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to confirm they pass**

```bash
python3 tests/test_parse_diff.py
```
Expected:
```
test_single_hunk: OK
test_two_hunks_same_file: OK
test_two_files_ids_are_global: OK
test_binary_file_requires_explicit_approval: OK
test_empty_diff_exits_nonzero: OK
test_doc_file_in_output: OK
test_default_doc_file: OK
test_carry_forward_identical_content: OK
test_carry_forward_changed_content_requires_review: OK

All parse_diff tests passed.
```

- [ ] **Step 5: Commit**

```bash
git add scripts/parse_diff.py tests/test_parse_diff.py
git commit -m "feat: add parse_diff.py — git diff → review-input (one section per hunk) (#85)"
```

---

## Task 5: server.py — --mode diff + SPA diff rendering

**Files:**
- Modify: `server.py` (CDN tag, CSS, `parse_args`, `renderMarkdown`, `DOMContentLoaded`, `__main__`)
- Create: `tests/test_server_diff.py`

**Interfaces:**
- Consumes: `validate_review_input` already wired in Task 1; `DiffInput` shape from `schema.py`
- Produces: `server.py --mode diff` accepts `DiffInput` JSON, runs the full review SPA with diff-mode title block and highlight.js rendering

- [ ] **Step 1: Write the failing tests**

```python
#!/usr/bin/env python3
# tests/test_server_diff.py
"""Integration tests for server.py --mode diff"""
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DIFF_INPUT = {
    "mode": "diff",
    "doc_file": "HEAD~1..HEAD",
    "round": 1,
    "approved_ids": [],
    "sections": [
        {
            "id": "s1",
            "title": "foo.py hunk 1",
            "content": "```diff\n@@ -1,3 +1,4 @@\n line 1\n-old line\n+new line\n+extra\n line 3\n```",
        },
        {
            "id": "s2",
            "title": "bar.py hunk 1",
            "content": "```diff\n@@ -5,3 +5,3 @@\n context\n-removed\n+added\n context\n```",
        },
    ],
}


def post(base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def get(base: str, path: str) -> dict:
    return json.loads(urllib.request.urlopen(base + path, timeout=5).read())


def _start_server(tmp: Path, inp: dict) -> tuple:
    """Start diff-mode server, return (proc, base_url, output_path)."""
    viva = tmp / ".viva"
    viva.mkdir()
    inp_path = viva / "review-input-r1.json"
    out_path = viva / "review-r1.json"
    inp_path.write_text(json.dumps(inp))
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--mode", "diff",
         "--input", str(inp_path), "--output", str(out_path), "--no-browser"],
        cwd=tmp,
    )
    url_file = viva / "server.url"
    for _ in range(50):
        if url_file.exists():
            break
        time.sleep(0.2)
    if not url_file.exists():
        proc.terminate()
        raise RuntimeError("server.url not created")
    return proc, url_file.read_text().strip(), str(out_path)


def test_diff_mode_input_endpoint():
    with tempfile.TemporaryDirectory() as tmp:
        proc, base, _ = _start_server(Path(tmp), DIFF_INPUT)
        try:
            data = get(base, "/input")
            assert data["mode"] == "diff", f"expected mode=diff, got {data.get('mode')}"
            assert len(data["sections"]) == 2
            assert data["sections"][0]["title"] == "foo.py hunk 1"
            assert data["sections"][1]["title"] == "bar.py hunk 1"
            print("test_diff_mode_input_endpoint: OK")
        finally:
            proc.terminate()
            proc.wait()


def test_diff_mode_submit_writes_output():
    with tempfile.TemporaryDirectory() as tmp:
        proc, base, out_path = _start_server(Path(tmp), DIFF_INPUT)
        try:
            post(base, "/submit", {
                "round": 1,
                "sections": [
                    {"id": "s1", "verdict": "approved"},
                    {"id": "s2", "verdict": "changes", "note": "Use += instead"},
                ],
            })
            for _ in range(20):
                if Path(out_path).exists():
                    break
                time.sleep(0.1)
            assert Path(out_path).exists(), "output file not written"
            out = json.loads(Path(out_path).read_text())
            verdicts = {s["id"]: s["verdict"] for s in out["sections"]}
            assert verdicts["s1"] == "approved"
            assert verdicts["s2"] == "changes"
            print("test_diff_mode_submit_writes_output: OK")
        finally:
            proc.terminate()
            proc.wait()


def test_diff_mode_next_round():
    with tempfile.TemporaryDirectory() as tmp:
        proc, base, _ = _start_server(Path(tmp), DIFF_INPUT)
        try:
            post(base, "/submit", {
                "round": 1,
                "sections": [
                    {"id": "s1", "verdict": "changes", "note": "fix this"},
                    {"id": "s2", "verdict": "approved"},
                ],
            })
            r2_input = dict(DIFF_INPUT, round=2, approved_ids=["s2"])
            r2_out = str(Path(tmp) / ".viva" / "review-r2.json")
            post(base, f"/next-round?output={r2_out}", r2_input)
            data = get(base, "/input")
            assert data["round"] == 2
            print("test_diff_mode_next_round: OK")
        finally:
            proc.terminate()
            proc.wait()


def test_diff_mode_complete_shuts_down():
    with tempfile.TemporaryDirectory() as tmp:
        proc, base, _ = _start_server(Path(tmp), DIFF_INPUT)
        try:
            post(base, "/complete", {
                "rounds_total": 1, "sections_total": 2, "sections_revised": 1
            })
            # Server shuts down ~2 seconds after /complete
            for _ in range(35):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            assert proc.poll() is not None, "server should have shut down after /complete"
            print("test_diff_mode_complete_shuts_down: OK")
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait()


def main() -> None:
    test_diff_mode_input_endpoint()
    test_diff_mode_submit_writes_output()
    test_diff_mode_next_round()
    test_diff_mode_complete_shuts_down()
    print("\nAll server diff tests passed.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
python3 tests/test_server_diff.py
```
Expected: server launch fails because `--mode diff` is not an accepted choice.

- [ ] **Step 3: Add "diff" to --mode choices in parse_args()**

In `server.py`, find `parse_args()` (around line 2433):

```python
    p.add_argument("--mode",       required=True, choices=["review", "qa"])
```

Change to:

```python
    p.add_argument("--mode",       required=True, choices=["review", "qa", "diff"])
```

- [ ] **Step 4: Add highlight.js CDN tag**

In `server.py`, in the `HTML` constant, after the existing DOMPurify `<script>` tag (around line 35):

```html
<script defer src="https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11/highlight.min.js"></script>
```

- [ ] **Step 5: Add highlight.js CSS overrides**

In `server.py`, in the `HTML` `<style>` block, just before `</style>` (around line 1146), add:

```css
/* ─── highlight.js diff overrides — map to viva's verdict palette ─── */
/* Added/removed lines use the same teal/orange tokens as verdicts.    */
.hljs-addition { background: var(--teal-bg);   color: var(--teal);   }
.hljs-deletion  { background: var(--orange-bg); color: var(--orange); }
```

- [ ] **Step 6: Update renderMarkdown() to call hljs**

In `server.py`, in the `HTML` JS, find `function renderMarkdown(target, md)` (around line 1272). Replace it with:

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

- [ ] **Step 7: Add diff mode branch in DOMContentLoaded**

In `server.py`, in the `HTML` JS, find the `DOMContentLoaded` listener's `fetch('/input')` block (around line 2376). The current structure is:

```js
      if (data.mode === 'review') {
        ...
        connectSSE();
      } else {
        QA_DATA = data;
        ...
        connectSSE();
      }
```

Change the `} else {` to `} else if (data.mode === 'diff') { ... } else {`, inserting:

```js
      } else if (data.mode === 'diff') {
        REVIEW_DATA = data;
        el('doc-path').textContent    = data.doc_file || 'diff';
        el('doc-path').title          = data.doc_file || 'diff';
        el('doc-title').innerHTML     = 'viva <em>diff</em>';
        el('round-badge').textContent = String(data.round).padStart(2, '0');
        el('review-view').style.display = '';
        initReview();
        connectSSE();
      } else {
```

- [ ] **Step 8: Run the tests to confirm they pass**

```bash
python3 tests/test_server_diff.py
```
Expected:
```
test_diff_mode_input_endpoint: OK
test_diff_mode_submit_writes_output: OK
test_diff_mode_next_round: OK
test_diff_mode_complete_shuts_down: OK

All server diff tests passed.
```

- [ ] **Step 9: Run the full test suite to confirm no regressions**

```bash
for f in tests/test_*.py; do python3 "$f" && echo "$f: PASS" || echo "$f: FAIL"; done
```
Expected: all files exit 0.

- [ ] **Step 10: Commit**

```bash
git add server.py tests/test_server_diff.py
git commit -m "feat: add --mode diff to server + highlight.js SPA diff rendering (#85)"
```

---

## Task 6: /viva-diff skill + README update

**Files:**
- Create: `.claude/skills/viva/diff.md`
- Modify: `README.md` (verify diff section added in Task 3 is complete)

**Interfaces:**
- Consumes: `server.py --mode diff` (Task 5), `scripts/parse_diff.py` (Task 4)
- Produces: `/viva-diff` skill that orchestrates the full diff review loop

- [ ] **Step 1: Create .claude/skills/viva/diff.md**

```markdown
---
name: viva-diff
description: Hunk-by-hunk code review. Human approves or requests changes per diff hunk; agent revises working-tree files and loops until all hunks are approved.
---

# viva-diff

Hunk-by-hunk code review. The unit of trust is the hunk: nothing in the diff
is considered done until a human has approved the hunk it lives in.

Named after the same discipline as `/viva` — the agent presents its changes,
the human drills every hunk, the agent defends and revises, and the diff only
passes when all of it holds up.

## Invocation

```
/viva-diff [ref]
```

`ref` is a git ref or range (`HEAD~1`, `HEAD~3..HEAD`, `main`). Omit for
unstaged working-tree changes (`git diff`). For staged-only changes use
`git diff --cached` inside the launch block.

## Steps

**1. Capture diff and launch** (round 1 — one bash block)

```bash
VIVA_DIR=~/.claude/skills/viva
[ -f "$VIVA_DIR/server.py" ] || \
  VIVA_DIR=$(find ~/.claude/plugins/cache -name "server.py" -path "*/viva*" -maxdepth 6 2>/dev/null \
             | xargs -I{} dirname {} | head -1)
[ -f "$VIVA_DIR/server.py" ] || { echo "viva-diff: cannot locate server.py"; exit 1; }

[ -f .viva/server.url ] && { echo "viva-diff: a prior session may still be running (.viva/server.url exists)"; exit 1; }

mkdir -p .viva
rm -f .viva/server.url .viva/review-input-r*.json .viva/review-r*.json
rm -rf .viva/attachments

# Capture the diff. Use the caller-supplied ref if provided.
git diff <ref> > .viva/diff.patch 2>/dev/null
# If ref is empty (unstaged): git diff > .viva/diff.patch
[ -s .viva/diff.patch ] || { echo "viva-diff: no changes to review"; exit 0; }

DOC_FILE="<ref or 'working tree'>"

python3 "$VIVA_DIR/scripts/parse_diff.py" .viva/diff.patch \
  --output .viva/review-input-r1.json --round 1 --doc-file "$DOC_FILE" \
&& {
  python3 "$VIVA_DIR/server.py" --mode diff \
    --input .viva/review-input-r1.json --output .viva/review-r1.json &
  for i in $(seq 1 100); do [ -f .viva/server.url ] && break; sleep 0.1; done
}
[ -f .viva/server.url ] || { echo "viva-diff: launch failed"; exit 1; }
BASE=$(cat .viva/server.url)
```

**2. Wait for verdicts** (every round)

```bash
until [ -f .viva/review-r{N}.json ]; do sleep 0.3; done
cat .viva/review-r{N}.json
```

Read all verdicts from stdout. The server writes the file atomically — `cat`
always sees a complete JSON.

**3. Act on verdicts**

| Verdict | Action |
|---------|--------|
| `approved` | Hunk accepted. Carries forward collapsed in next round. |
| `changes` | Apply the comment's edit to the target source file. The comment's `anchor.text` identifies the exact span; `anchor.offset` disambiguates repeated spans. Scope the edit to the hunk named in the section title (`{filepath} hunk N`). |
| `info` | Answer in the thread only. Do not edit the file until the discussion escalates to a `changes` turn (the reviewer switches chip to "request changes"). |
| `pending` | Re-present unchanged next round. |

For each section with a `changes` comment:
- Parse the section `title` to extract the filepath (`title` = `"{filepath} hunk N"`)
- Apply the targeted edit to `{filepath}` in the working tree
- Use `anchor.text` + `anchor.offset` to locate the exact span within the hunk

**Every section approved** → go to step 5.
**Any `changes`/`info`** → re-diff, re-parse, re-arm (step 4).

**4. Re-diff and re-arm**

```bash
git diff <ref> > .viva/diff.patch 2>/dev/null
[ -s .viva/diff.patch ] || { echo "viva-diff: diff is now empty — all changes may have been fully applied"; exit 0; }

python3 "$VIVA_DIR/scripts/parse_diff.py" .viva/diff.patch \
  --output .viva/review-input-r{N+1}.json --round {N+1} --doc-file "$DOC_FILE" \
  --prior-input .viva/review-input-r{N}.json \
  --prior-verdicts .viva/review-r{N}.json \
&& curl -s -X POST "$BASE/next-round?output=.viva/review-r{N+1}.json" \
     -H "Content-Type: application/json" -d @.viva/review-input-r{N+1}.json
```

The browser updates in place — no new tab. Loop to step 2.

**5. Finish** (all sections approved)

```bash
curl -s -X POST "$BASE/complete" -H "Content-Type: application/json" \
  -d "{\"rounds_total\": N, \"sections_total\": M, \"sections_revised\": K}"
```

Give the sign-off report:

> "N hunks approved across M files in R round(s). K hunks revised."

Then ask: "Commit these changes? (y/n)"

If yes, stage and commit the reviewed working-tree changes:
```bash
git add <files touched>
git commit -m "<message>"
```

## Scope

`/viva-diff` is a **human gate**, not an LLM reviewer. It composes with
`/code-review` (which is an LLM pass): run `/code-review` first to apply
automated suggestions, then `/viva-diff` for human sign-off before committing.
```

- [ ] **Step 2: Verify the skill file is visible**

```bash
ls .claude/skills/viva/
```
Expected: `SKILL.md`, `brainstorming-qa.md`, `diff.md`

- [ ] **Step 3: Run the full test suite one final time**

```bash
for f in tests/test_*.py; do python3 "$f" && echo "$f: PASS" || echo "$f: FAIL"; done
```
Expected: all files exit 0.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/viva/diff.md
git commit -m "feat: ship /viva-diff skill — hunk-by-hunk diff review loop (#85)"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Task |
|-----------------|------|
| `schema.py` merge + Q&A TypedDicts + `validate_qa_input` | Task 1 |
| `validate_review_input` called at server startup for review+diff | Task 1 |
| `/viva-qa` skill with documented contract | Task 2 |
| `install.sh` deleted | Task 3 |
| PRODUCT.md thesis updated for diff | Task 3 |
| CLAUDE.md, DESIGN.md from worktree-speed | Task 3 |
| README brainstorming section updated | Task 3, 4 |
| `parse_diff.py` — per-hunk sections, carry-forward, binary, empty diff | Task 4 |
| `--mode diff` in `parse_args()` | Task 5 |
| `validate_review_input` called for diff mode at startup | Task 1 |
| highlight.js CDN | Task 5 |
| hljs CSS overrides matching viva token colors | Task 5 |
| `renderMarkdown` calls `hljs.highlightElement` | Task 5 |
| Diff mode branch in `DOMContentLoaded` | Task 5 |
| `/viva-diff` skill with full loop | Task 6 |
| Binary file requires explicit approval (not auto-approved) | Task 4 test |
| `doc_file` set via `--doc-file` flag | Task 4 |

### Placeholder scan

No TBD, TODO, or "similar to task N" patterns. All code blocks are complete.

### Type consistency

- `section_key` defined in Task 1, consumed in Task 4 ✓
- `validate_review_input` defined in Task 1, called by Task 4 (`parse_diff.py`) and Task 1 (`server.py`) ✓
- `validate_qa_input` defined in Task 1, called by Task 1 (`server.py` startup) ✓
- Section shape `{id, title, content}` consistent across `parse_diff.py` output and `validate_review_input` checks ✓
- `approved_ids` is a `List[str]`, populated in `_carry_forward`, consumed by SPA (existing `priorApprovedSet`) ✓
