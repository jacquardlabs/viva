# Multiple Inline Comments Per Section — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a viva reviewer leave N typed, independently-threaded comments per section in one round — each optionally anchored to a selected span — replacing today's one-verdict-one-anchor-per-section model.

**Architecture:** Extend two things viva already has rather than add a subsystem: the single `anchor` string becomes a `comments[]` array, and each comment binds an open-note thread (re-keyed from section-title to a per-comment `cid`). The section verdict becomes *derived* (no comments → `approved`; any `changes` comment → `changes`; else `info`), driven by a single toggling primary button. Interaction model A: select text → inline popover → typed comment → highlight in prose + thread stacked under the section.

**Tech Stack:** Python 3.8+ stdlib (`http.server`, no deps), vanilla JS embedded as an HTML string in `server.py` (marked@12 + dompurify@3 via CDN), Python integration tests that spawn the server and assert on `/submit` output + served-page needles.

## Global Constraints

- **Python 3.8+**, standard library only — no new dependencies (`pyproject.toml`).
- **Single writer per store:** `scripts/open_notes.py` is the only writer of `.viva/open-notes.json`; `scripts/revision_history.py` and `parse_sections.py --open-notes` only read it.
- **Zero-regression contract:** a section with no `comments` must serialize and render byte-identically to today's approved/bare card; the `/submit` endpoint stays a pure pipe (never strips or injects comment keys).
- **Vocabulary:** use the existing **settle** / **open-note** / **pin** terms — comment threading state is `open` (default true) and `settled`; never "resolve".
- **Baseline:** current `main` through #65 — the open-notes control is a `.pin-btn` (`#rpin-`, `.is-pinned`); this change **retires** it.
- **Anchor model:** an anchor is `{ text, offset }` where `offset` is the character index into the section's raw markdown `content`; `text` is the selected span. Absent anchor = whole-section comment.
- **cid format:** `{sectionId}-c{n}` at creation; preserved verbatim across rounds once a thread carries forward (independent of the positional section id).
- Spec: `docs/superpowers/specs/2026-06-28-multi-comment-review-design.md`.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `scripts/open_notes.py` | Single writer of the thread store | Re-key by `cid`; iterate `comments[]` |
| `scripts/revision_history.py` | Fold threads into the ledger at sign-off | Group cid threads by section; show quoted span |
| `scripts/parse_sections.py` | Build each round's `review-input`; carry threads forward | Attach cid-keyed threads to sections by title |
| `server.py` (Python) | `/submit` pipe + ledger | Read ledger note from `comments[]` |
| `server.py` (JS/CSS) | Review card UI | comments[] state, toggling button, popover, highlights, thread list, retire pin |
| `SKILL.md` | Agent consumption contract | Rewrite verdict table, Anchors, Open-notes sections |
| `tests/` | Integration + unit coverage | New `comments[]` tests; update #65 pin needles |

Build order is backend-first (independently testable pure functions and the `/submit` contract), then the frontend (verified via served-page needles + `/submit` round-trips), then the SKILL.md consumption contract, then the headline end-to-end test.

---

### Task 1: `open_notes.py` — re-key threads by `cid`, iterate `comments[]`

The store moves from one thread per section (keyed by normalized title) to one thread per comment (keyed by `cid`). Each thread still carries its section `title` (for re-attachment, since `cid`'s section prefix is positional) and now its anchored `quote`.

**Files:**
- Modify: `scripts/open_notes.py:46-94` (`update`), `:114-139` (`main` help text)
- Test: `tests/test_open_notes_unit.py` (Create)

**Interfaces:**
- Consumes: a verdict section shaped `{ id, verdict, comments: [{ cid, type, note, anchor?: {text, offset}, open, settled }] }`. `responses` maps `cid → text`.
- Produces: store shaped `{ "<cid>": { "cid", "title", "quote", "status": "open"|"settled", "exchanges": [{round, verdict, note, response}] } }`. `update(store, round_num, verdicts, input_data, responses) -> dict` (same signature, new semantics). `norm(title)` unchanged.

- [ ] **Step 1: Write the failing unit tests**

Create `tests/test_open_notes_unit.py`:

```python
#!/usr/bin/env python3
"""Unit tests for open_notes.update — per-cid threading (multi-comment)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import open_notes  # noqa: E402


def _input():
    return {"sections": [{"id": "s1", "title": "Goals"}, {"id": "s2", "title": "Scope"}]}


def test_two_open_comments_become_two_threads():
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "changes", "note": "5x not 3x",
         "anchor": {"text": "retries 3x", "offset": 10}, "open": True, "settled": False},
        {"cid": "s1-c2", "type": "info", "note": "why stderr?", "open": True, "settled": False},
    ]}]}
    out = open_notes.update({}, 1, verdicts, _input(), {"s1-c1": "set to 5x"})
    assert set(out) == {"s1-c1", "s1-c2"}
    assert out["s1-c1"]["title"] == "Goals"
    assert out["s1-c1"]["quote"] == "retries 3x"
    assert out["s1-c1"]["status"] == "open"
    assert out["s1-c1"]["exchanges"][0] == {
        "round": 1, "verdict": "changes", "note": "5x not 3x", "response": "set to 5x"}
    assert out["s1-c2"]["exchanges"][0]["response"] == ""  # no response supplied


def test_settle_one_thread_by_cid():
    store = {"s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "x",
                       "status": "open", "exchanges": []}}
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "changes", "note": "", "open": True, "settled": True}]}]}
    out = open_notes.update(store, 2, verdicts, _input(), {})
    assert out["s1-c1"]["status"] == "settled"


def test_approving_section_settles_all_its_threads():
    store = {
        "s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "x", "status": "open", "exchanges": []},
        "s1-c2": {"cid": "s1-c2", "title": "Goals", "quote": "y", "status": "open", "exchanges": []},
        "s2-c1": {"cid": "s2-c1", "title": "Scope", "quote": "z", "status": "open", "exchanges": []},
    }
    verdicts = {"sections": [{"id": "s1", "verdict": "approved", "comments": []}]}
    out = open_notes.update(store, 2, verdicts, _input(), {})
    assert out["s1-c1"]["status"] == "settled"
    assert out["s1-c2"]["status"] == "settled"
    assert out["s2-c1"]["status"] == "open"  # untouched section stays open


def test_no_comments_is_noop():
    verdicts = {"sections": [{"id": "s1", "verdict": "approved"}]}
    assert open_notes.update({}, 1, verdicts, _input(), {}) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_open_notes_unit.py` then `python3 -m pytest tests/test_open_notes_unit.py -v`
Expected: FAIL — current `update` reads section-level `note`/`open`, not `comments[]`; `quote`/`cid` absent.

- [ ] **Step 3: Rewrite `update` to iterate comments by cid**

Replace `scripts/open_notes.py:46-94` with:

```python
def update(
    store: dict,
    round_num: int,
    verdicts: dict,
    input_data: dict,
    responses: dict,
) -> dict:
    """Apply one round's verdicts to the per-comment thread store. Pure.

    Each section carries a `comments` list; each comment is its own thread keyed
    by `cid`. For every comment:
      - open & changes/info → append an exchange (create the thread if new),
        carrying the agent's `responses[cid]`.
      - settled truthy      → mark that thread settled.
    Approving a section settles every still-open thread whose `cid` belongs to it
    (matched by the section's stable title), so approval clears the section's
    conversation. A section with no comments is a no-op (today's behavior).
    """
    titles = {s.get("id"): s.get("title", s.get("id"))
              for s in input_data.get("sections", [])}
    out = {k: {**v, "exchanges": list(v.get("exchanges", []))}
           for k, v in store.items()}

    for s in verdicts.get("sections", []):
        sid = s.get("id")
        title = titles.get(sid, sid or "?")
        verdict = s.get("verdict")
        comments = s.get("comments") or []

        if verdict == "approved":
            # Settle every open thread belonging to this section (by title).
            for thread in out.values():
                if norm(thread.get("title")) == norm(title) and thread.get("status") == "open":
                    thread["status"] = "settled"
            continue

        for c in comments:
            cid = c.get("cid")
            if not cid:
                continue
            thread = out.get(cid)
            if c.get("settled"):
                if thread:
                    thread["status"] = "settled"
                continue
            if c.get("type") in ("changes", "info") and c.get("open"):
                anchor = c.get("anchor") or {}
                if thread is None:
                    thread = {"cid": cid, "title": title, "quote": anchor.get("text", ""),
                              "status": "open", "exchanges": []}
                    out[cid] = thread
                thread["status"] = "open"
                thread["title"] = title          # keep display title fresh
                if anchor.get("text"):
                    thread["quote"] = anchor["text"]
                thread["exchanges"].append({
                    "round": round_num,
                    "verdict": c.get("type"),
                    "note": c.get("note", ""),
                    "response": responses.get(cid, ""),
                })
    return out
```

Update the module docstring (`:11-13`, `:26-32`) and `main`'s `--response` help (`:122-123`) to say the store is keyed by **`cid`** and `--response` takes `"<cid>=text"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_open_notes_unit.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/open_notes.py tests/test_open_notes_unit.py
git commit -m "feat(open-notes): re-key threads by comment cid for multi-comment review"
```

---

### Task 2: `revision_history.py` — group cid threads by section, show quoted span

The thread store is now cid-keyed and each thread carries `title` (its section) and `quote` (the anchored span). The Open-notes ledger should group threads under their section heading and show the quote so a reader knows which line each thread was about.

**Files:**
- Modify: `scripts/revision_history.py:31-63` (`collect_threads`, `build_threads_block`)
- Test: `tests/test_revision_history_threads.py` (Create)

**Interfaces:**
- Consumes: `.viva/open-notes.json` keyed by `cid`, each value `{cid, title, quote, status, exchanges}`.
- Produces: `build_threads_block(threads) -> str` Markdown; `collect_threads(viva_dir) -> list[dict]` returns threads (with `exchanges`) grouped/ordered by section title then cid.

- [ ] **Step 1: Read the current functions**

Read `scripts/revision_history.py:31-63` to see the existing `### Open notes` block format (status line + per-exchange note → response) so the grouped version preserves it.

- [ ] **Step 2: Write the failing test**

Create `tests/test_revision_history_threads.py`:

```python
#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import revision_history as rh  # noqa: E402


def test_threads_grouped_by_section_with_quote():
    tmp = Path(tempfile.mkdtemp())
    store = {
        "s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "retries 3x", "status": "settled",
                  "exchanges": [{"round": 1, "verdict": "changes", "note": "5x", "response": "done"}]},
        "s1-c2": {"cid": "s1-c2", "title": "Goals", "quote": "", "status": "open",
                  "exchanges": [{"round": 1, "verdict": "info", "note": "why?", "response": "because"}]},
    }
    (tmp / "open-notes.json").write_text(json.dumps(store))
    threads = rh.collect_threads(tmp)
    assert len(threads) == 2
    block = rh.build_threads_block(threads)
    assert "### Open notes" in block
    assert "Goals" in block
    assert "retries 3x" in block          # the quoted span appears
    assert "5x" in block and "why?" in block
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_revision_history_threads.py -v`
Expected: FAIL — `quote` not rendered, threads not grouped by section.

- [ ] **Step 4: Update `collect_threads` + `build_threads_block`**

In `collect_threads` (`:31-47`), keep filtering to threads with `exchanges` but order by `(norm title, cid)`:

```python
    threads = [t for t in store.values() if t.get("exchanges")]
    threads.sort(key=lambda t: ((t.get("title") or "").strip().lower(), t.get("cid", "")))
    return threads
```

In `build_threads_block` (`:49-63`), emit a section sub-heading when the title changes and show the quote on each thread:

```python
def build_threads_block(threads: list[dict]) -> str:
    lines = ["### Open notes", ""]
    current_title = None
    for t in threads:
        title = t.get("title", "")
        if title != current_title:
            lines.append(f"**{title}**")
            lines.append("")
            current_title = title
        status = t.get("status", "open")
        quote = t.get("quote", "")
        head = f"- _{flat(quote)}_ — {status}" if quote else f"- (whole section) — {status}"
        lines.append(head)
        for x in t.get("exchanges", []):
            note = flat(x.get("note", ""))
            resp = flat(x.get("response", ""))
            lines.append(f"  - R{x.get('round')} {x.get('verdict')}: {note}"
                         + (f" → {resp}" if resp else ""))
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_revision_history_threads.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/revision_history.py tests/test_revision_history_threads.py
git commit -m "feat(revision-history): group cid threads by section with quoted span"
```

---

### Task 3: `parse_sections.py` — attach cid threads to sections by title

`_attach_open_notes` reads the (now cid-keyed) store and must attach each section's threads as its `open_notes` array, matched by the section's stable normalized title.

**Files:**
- Modify: `scripts/parse_sections.py:275-303` (`_attach_open_notes`)
- Test: `tests/test_parse_open_notes_attach.py` (Create)

**Interfaces:**
- Consumes: cid-keyed store from Task 1.
- Produces: each section dict gains `open_notes: [{cid, quote, status, exchanges}]` (only open threads, omitted when none) — the array the server renders. Sections with no open thread gain no `open_notes` key.

- [ ] **Step 1: Read current `_attach_open_notes`**

Read `scripts/parse_sections.py:275-305`. Today it builds `open_threads = {norm_title: exchanges}` and sets `s["open_notes"] = open_threads[key]`. It must change to group multiple threads per title.

- [ ] **Step 2: Write the failing test**

Create `tests/test_parse_open_notes_attach.py`:

```python
#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import parse_sections as ps  # noqa: E402


def test_attaches_open_threads_grouped_by_title():
    tmp = Path(tempfile.mkdtemp())
    store = {
        "s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "x", "status": "open",
                  "exchanges": [{"round": 1, "verdict": "changes", "note": "n", "response": ""}]},
        "s1-c2": {"cid": "s1-c2", "title": "Goals", "quote": "y", "status": "settled",
                  "exchanges": [{"round": 1, "verdict": "info", "note": "m", "response": "r"}]},
    }
    sp = tmp / "open-notes.json"
    sp.write_text(json.dumps(store))
    sections = [{"id": "s1", "title": "Goals", "content": "g"},
                {"id": "s2", "title": "Scope", "content": "s"}]
    ps._attach_open_notes(str(sp), sections)
    # Only the OPEN thread attaches; settled threads drop from the next round.
    assert [t["cid"] for t in sections[0]["open_notes"]] == ["s1-c1"]
    assert sections[0]["open_notes"][0]["quote"] == "x"
    assert "open_notes" not in sections[1]  # no threads → stays bare
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_parse_open_notes_attach.py -v`
Expected: FAIL — current code keys a single exchanges list per title, has no cid grouping, and attaches settled threads.

- [ ] **Step 4: Rewrite `_attach_open_notes`**

Replace the body that builds `open_threads` and the attach loop (`:294-303`) with:

```python
    by_title: dict[str, list] = {}
    for t in store.values():
        if t.get("status") != "open":
            continue  # settled threads drop from later rounds
        by_title.setdefault((t.get("title") or "").strip().lower(), []).append({
            "cid": t.get("cid"),
            "quote": t.get("quote", ""),
            "status": t.get("status", "open"),
            "exchanges": t.get("exchanges", []),
        })
    for threads in by_title.values():
        threads.sort(key=lambda t: t.get("cid", ""))
    for s in new_sections:
        key = (s.get("title") or "").strip().lower()
        if key in by_title:
            s["open_notes"] = by_title[key]
```

(Keep the existing guard that returns early when `open_notes_path` is falsy or the store can't be read.)

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_parse_open_notes_attach.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/parse_sections.py tests/test_parse_open_notes_attach.py
git commit -m "feat(parse): attach cid threads to sections grouped by title"
```

---

### Task 4: `server.py` `/submit` — record ledger note from `comments[]`

The in-memory `_ledger` (server.py:2335-2342) records a `note` per changes/info section. With notes living in `comments[]`, join them so the completion ledger keeps real text. `/submit` otherwise stays a pure pipe — `comments[]` already flows through `write_output` untouched.

**Files:**
- Modify: `server.py:2335-2342` (the `_ledger.append` loop)
- Test: covered by Task 9's integration test (no isolated unit harness for the in-process ledger).

**Interfaces:**
- Consumes: submitted section `{ id, verdict, comments?: [{type, note}], note? }`.
- Produces: unchanged `_ledger` entry shape `{round, section_title, verdict, note}`; `note` now derived from comments when present.

- [ ] **Step 1: Update the ledger loop**

Replace `server.py:2335-2342` with:

```python
                for s in data.get("sections", []):
                    if s.get("verdict") in ("changes", "info"):
                        comments = s.get("comments") or []
                        note = (" · ".join(c.get("note", "") for c in comments if c.get("note"))
                                if comments else s.get("note", ""))
                        _ledger.append({
                            "round": rnd,
                            "section_title": titles.get(s.get("id"), s.get("id", "?")),
                            "verdict": s["verdict"],
                            "note": note,
                        })
```

- [ ] **Step 2: Verify the server still imports/starts**

Run: `python3 -c "import ast; ast.parse(open('server.py').read()); print('parse OK')"`
Expected: `parse OK`.

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "feat(server): derive ledger note from comments[] on submit"
```

---

### Task 5: `server.py` JS — comments[] state, derived verdict, toggling primary button

Replace the four-button verdict row + single note/anchor with one toggling primary button driven by `rState.verdicts[id].comments`. This task makes a section submit `comments[]` with a *derived* verdict; comment *creation* UI lands in Task 6.

**Files:**
- Modify: `server.py` — `buildReviewCard` (~1356-1451, the `.actions` row + note wrap), `setReviewVerdict` (~1500-1525), `submitReview` (~1914-1934), and add `deriveVerdict`/`addComment`/`renderPrimaryButton` helpers near the anchor block (~1577).
- Test: `tests/test_server_comments_submit.py` (Create)

**Interfaces:**
- Consumes: nothing new.
- Produces JS globals: `rState.verdicts[id].comments` is `[{cid, type, note, anchor?, open:true, settled:false}]`. `deriveVerdict(id) -> 'approved'|'changes'|'info'`. `addComment(id, {type, note, anchor})` pushes a comment (assigning `cid = id + '-c' + (n+1)`), `removeComment(id, cid)`, `setComment(id, cid, patch)`. `submitReview` emits `{id, verdict: deriveVerdict(id), comments}` per section.

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_server_comments_submit.py`:

```python
#!/usr/bin/env python3
"""Integration: a section submits multiple typed comments; verdict is derived."""
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def post(base, path, payload):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=5).read()


def main():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"; viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "## Goals\nretries 3x\nlog to stderr\n"},
                       {"id": "s2", "title": "Scope", "content": "## Scope\nbody\n"}]}
    (viva / "in1.json").write_text(json.dumps(r1))
    proc = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--mode", "review",
                             "--input", str(viva / "in1.json"), "--output", str(viva / "out1.json"),
                             "--no-browser"], cwd=tmp)
    try:
        uf = viva / "server.url"
        for _ in range(50):
            if uf.exists(): break
            time.sleep(0.2)
        assert uf.exists(), "server failed to start"
        base = uf.read_text().strip()

        # The page ships the new comment machinery.
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        for needle in ("deriveVerdict", "addComment", "comments"):
            assert needle in page, f"page missing: {needle}"

        # A section with two typed comments → derived verdict "changes"; a
        # comment-free section → "approved" with no comments noise.
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "comments": [
                {"cid": "s1-c1", "type": "changes", "note": "5x not 3x",
                 "anchor": {"text": "retries 3x", "offset": 9}, "open": True, "settled": False},
                {"cid": "s1-c2", "type": "info", "note": "why stderr?", "open": True, "settled": False}]},
            {"id": "s2", "verdict": "approved"}]})

        out = json.loads((viva / "out1.json").read_text())
        s1 = next(s for s in out["sections"] if s["id"] == "s1")
        s2 = next(s for s in out["sections"] if s["id"] == "s2")
        assert s1["verdict"] == "changes", s1
        assert [c["cid"] for c in s1["comments"]] == ["s1-c1", "s1-c2"], s1
        assert s1["comments"][0]["anchor"] == {"text": "retries 3x", "offset": 9}
        assert s2["verdict"] == "approved" and "comments" not in s2 or not s2.get("comments"), s2
        print("OK")
    finally:
        proc.terminate(); proc.wait(timeout=5)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_server_comments_submit.py`
Expected: FAIL on the page-needle assertion (`deriveVerdict` absent).

- [ ] **Step 3: Add the comments state helpers**

Insert near the anchor block (after `server.py:~1575`):

```javascript
/* ─── Comments (multi-comment review) ───────────────────────────
   A section owns a list of typed comments; the section verdict is DERIVED,
   never picked. No comments → approved; any `changes` comment → changes;
   otherwise info. Each comment is an open thread by default (cid-keyed). */
function commentsOf(id) { return (rState.verdicts[id] ||= {}).comments ||= []; }

function deriveVerdict(id) {
  const cs = rState.verdicts[id]?.comments || [];
  if (cs.length === 0) return rState.verdicts[id]?.skip ? 'pending' : 'approved';
  return cs.some(c => c.type === 'changes') ? 'changes' : 'info';
}

function addComment(id, { type, note, anchor }) {
  const cs = commentsOf(id);
  const n = cs.reduce((m, c) => Math.max(m, +(String(c.cid).split('-c')[1] || 0)), 0);
  cs.push({ cid: id + '-c' + (n + 1), type, note: note || '',
            ...(anchor && { anchor }), open: true, settled: false });
  syncCard(id);
}

function removeComment(id, cid) {
  const v = rState.verdicts[id]; if (!v) return;
  v.comments = (v.comments || []).filter(c => c.cid !== cid);
  syncCard(id);
}

function setComment(id, cid, patch) {
  const c = (rState.verdicts[id]?.comments || []).find(x => x.cid === cid);
  if (c) Object.assign(c, patch);
  syncCard(id);
}

// Repaint everything that derives from a card's comments: dot, primary button,
// highlights (Task 6), thread list (Task 7).
function syncCard(id) {
  syncReviewDot(id);
  renderPrimaryButton(id);
  if (typeof renderHighlights === 'function') renderHighlights(id);
  if (typeof renderCommentList === 'function') renderCommentList(id);
  updateReviewStats();
}

function renderPrimaryButton(id) {
  const btn = el('rbtn-primary-' + id); if (!btn) return;
  const n = (rState.verdicts[id]?.comments || []).length;
  btn.className = 'action-btn' + (n ? ' is-changes' : ' is-approve');
  btn.innerHTML = n ? ('&#10003; done · ' + n + (n === 1 ? ' comment' : ' comments'))
                    : '&#10003; approve';
}
```

- [ ] **Step 4: Replace the action row + verdict logic**

In `buildReviewCard` replace the `.actions` block (`server.py:1380-1396`, the four buttons + `rnote-wrap` + `anchor-row` + pin) with a primary/skip pair and a comment-list mount (the list/popover render in Tasks 6–7):

```javascript
          <div class="actions">
            <button class="action-btn is-approve" id="rbtn-primary-${section.id}">&#10003; approve</button>
            <button class="action-btn" id="rbtn-skip-${section.id}" style="margin-left:auto;opacity:0.55">&#8595; skip</button>
          </div>
          <div class="comment-list" id="rclist-${section.id}"></div>
          <div class="comment-popover" id="rpop-${section.id}" style="display:none"></div>
```

Replace the four `addEventListener` calls (`:1405-1409`) and the pin handler (`:1416-1418`) with:

```javascript
  card.querySelector('#rbtn-primary-' + section.id).addEventListener('click', e => {
    e.stopPropagation(); approveSection(section.id);
  });
  card.querySelector('#rbtn-skip-' + section.id).addEventListener('click', e => {
    e.stopPropagation(); skipReviewCard(section.id);
  });
```

Add `approveSection` (replaces the old `setReviewVerdict(id, 'approved')` path) near `setReviewVerdict`:

```javascript
// Approve = sign off this section. A section with comments cannot approve; the
// primary button only reads "approve" when comments.length === 0.
function approveSection(id) {
  if ((rState.verdicts[id]?.comments || []).length) return;  // guarded by label
  (rState.verdicts[id] ||= {}).skip = false;
  rState.verdicts[id].verdict = 'approved';
  advanceFrom(id);
}
```

Keep `setReviewVerdict` only if other callers need it; otherwise delete it and the `a/c/i` hotkey handlers move to Task 6. Extract the old "advance to next pending card" tail of `setReviewVerdict` (`:1520-1525`) into `advanceFrom(id)` so `approveSection` reuses it.

- [ ] **Step 5: Rewrite `submitReview` to emit comments + derived verdict**

Replace `submitReview` (`server.py:1914-1934`) section map with:

```javascript
    sections: REVIEW_DATA.sections.map(s => {
      const v = rState.verdicts[s.id] || {};
      const comments = v.comments || [];
      const verdict = comments.length ? (comments.some(c => c.type === 'changes') ? 'changes' : 'info')
                    : (v.skip ? 'pending' : (v.verdict || 'pending'));
      return { id: s.id, verdict,
               ...(comments.length && { comments }),
               ...(v.images && v.images.length && { images: v.images }) };
    })
```

(Drop the old `note`/`anchor`/`open`/`settle` spreads — those now live inside each comment.)

- [ ] **Step 6: Run test to verify it passes**

Run: `python3 tests/test_server_comments_submit.py`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add server.py tests/test_server_comments_submit.py
git commit -m "feat(ui): derived verdict + toggling primary button + comments[] submit"
```

---

### Task 6: `server.py` JS/CSS — select→popover create + highlight rendering

Wire the interaction: selecting text in a section opens a popover (type chips + note + save); saving calls `addComment` with `{text, offset}`; saved anchors render as colored highlights in the prose. An un-anchored "+ add note" path opens the same popover with no anchor.

**Files:**
- Modify: `server.py` — extend the selection block (`:1584-1609`), add `openCommentPopover`/`renderHighlights`/`offsetInSource`, add `+ add note` control to the card, add CSS for `.comment-popover`, `.cmt-hl-changes`, `.cmt-hl-info`. Remove the old `anchorSelection`/`clearAnchor`/`syncAnchorChip` (`:1599-1639`) and the `.anchor-row`/`.anchor-chip` CSS (`:771-808`).
- Test: extend `tests/test_server_comments_submit.py` page-needle list.

**Interfaces:**
- Consumes: `addComment(id, {type, note, anchor})` from Task 5.
- Produces JS: `offsetInSource(id, text) -> number` (char index of `text` in the section's raw `content`, via `_pendingMarkdown`/`REVIEW_DATA`); `openCommentPopover(id, {anchor})`; `renderHighlights(id)` wraps each comment's `anchor.text` occurrence in the rendered `.section-content` with a typed `<mark>`.

- [ ] **Step 1: Add the page-needle assertions (failing)**

In `tests/test_server_comments_submit.py` Step-1 needle loop, add `"openCommentPopover"`, `"renderHighlights"`, `"cmt-hl-changes"`, `"add note"`. Re-run: FAIL until implemented.

- [ ] **Step 2: Replace selection capture to open the popover**

Replace the anchor selection handlers (`server.py:1599-1639`) with:

```javascript
let _selRange = null;  // {id, text, offset} for the live selection, or null

document.addEventListener('selectionchange', () => {
  const sel = document.getSelection();
  if (!sel || sel.isCollapsed) { _selRange = null; return; }
  const text = sel.toString().trim();
  if (!text) { _selRange = null; return; }
  const node = sel.anchorNode;
  const start = node && node.nodeType === 3 ? node.parentElement : node;
  const content = start && start.closest ? start.closest('.section-content') : null;
  if (!content) { _selRange = null; return; }
  const m = content.id.match(/^rcontent-(.+)$/);
  if (m) _selRange = { id: m[1], text, offset: offsetInSource(m[1], text) };
});

// Char offset of `text` in the section's raw markdown source — the rewrite
// target. -1 when not found (anchor still stores text; agent falls back to grep).
function offsetInSource(id, text) {
  const src = _pendingMarkdown.get(id) || '';
  return src.indexOf(text);
}

// A small popover with two type chips + a note field + save/cancel. `anchor`
// is {text, offset} or null (whole-section note).
function openCommentPopover(id, { anchor } = {}) {
  const pop = el('rpop-' + id); if (!pop) return;
  pop.dataset.type = 'changes';
  pop.innerHTML =
      '<div class="cmt-pop-row">'
    +   '<button type="button" class="cmt-chip cmt-chip-changes is-on" data-type="changes">request changes</button>'
    +   '<button type="button" class="cmt-chip cmt-chip-info" data-type="info">need info</button>'
    + '</div>'
    + (anchor ? '<div class="cmt-pop-quote">&#9875; ' + esc(anchor.text) + '</div>' : '')
    + '<textarea class="note-field cmt-pop-note" placeholder="Describe the change… or paste a screenshot"></textarea>'
    + '<div class="cmt-pop-row"><button type="button" class="cmt-save">save</button>'
    +   '<button type="button" class="cmt-cancel">cancel</button></div>';
  pop.style.display = '';
  pop.querySelectorAll('.cmt-chip').forEach(ch => ch.onclick = () => {
    pop.dataset.type = ch.dataset.type;
    pop.querySelectorAll('.cmt-chip').forEach(c => c.classList.toggle('is-on', c === ch));
  });
  const ta = pop.querySelector('.cmt-pop-note'); ta.focus();
  pop.querySelector('.cmt-save').onclick = () => {
    const note = ta.value.trim();
    if (!note) { ta.placeholder = 'a comment needs a note'; return; }
    addComment(id, { type: pop.dataset.type, note, anchor: anchor || undefined });
    closeCommentPopover(id);
  };
  pop.querySelector('.cmt-cancel').onclick = () => closeCommentPopover(id);
}

function closeCommentPopover(id) {
  const pop = el('rpop-' + id);
  if (pop) { pop.style.display = 'none'; pop.innerHTML = ''; }
}

// Re-wrap each comment's anchored span in the rendered content with a typed mark.
function renderHighlights(id) {
  const content = el('rcontent-' + id); if (!content) return;
  content.querySelectorAll('mark.cmt-hl-changes, mark.cmt-hl-info').forEach(m => {
    m.replaceWith(document.createTextNode(m.textContent));
  });
  content.normalize();
  const cs = (rState.verdicts[id]?.comments || []).filter(c => c.anchor?.text);
  cs.forEach(c => wrapFirst(content, c.anchor.text, 'cmt-hl-' + c.type));
}

// Wrap the first text-node occurrence of `needle` in a <mark class=cls>.
function wrapFirst(root, needle, cls) {
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walk.nextNode())) {
    const i = n.nodeValue.indexOf(needle);
    if (i < 0) continue;
    const after = n.splitText(i);
    after.splitText(needle.length);
    const mark = document.createElement('mark');
    mark.className = cls;
    mark.textContent = after.nodeValue;
    after.replaceWith(mark);
    return;
  }
}
```

- [ ] **Step 3: Add the create affordances to the card**

In `buildReviewCard`, after the `.section-content` div add a selection-create button row and keep the `comment-popover` mount from Task 5:

```javascript
          <div class="comment-add-row">
            <button type="button" class="cmt-add-btn" id="rcmtsel-${section.id}" title="Select text above, then add a comment pinned to it">&#9875; comment on selection</button>
            <button type="button" class="cmt-add-btn" id="rcmtnote-${section.id}">+ add note</button>
          </div>
```

Wire them in the event block:

```javascript
  card.querySelector('#rcmtsel-' + section.id).addEventListener('click', e => {
    e.stopPropagation();
    if (_selRange && _selRange.id === section.id)
      openCommentPopover(section.id, { anchor: { text: _selRange.text, offset: _selRange.offset } });
    else { const b = e.currentTarget; const p = b.textContent; b.textContent = '⚓ select text first'; setTimeout(() => b.textContent = p, 1400); }
  });
  card.querySelector('#rcmtnote-' + section.id).addEventListener('click', e => {
    e.stopPropagation(); openCommentPopover(section.id, {});
  });
```

Re-run `renderHighlights(id)` whenever a card's content renders (find where `_pendingMarkdown` is rendered into `.section-content` on first open and call `renderHighlights(id)` after).

- [ ] **Step 4: Add CSS**

Replace the removed `.anchor-row`/`.anchor-btn`/`.anchor-chip*` CSS (`server.py:770-808`) with comment styles, reusing existing color vars:

```css
/* ─── Multi-comment review ─── */
.comment-add-row { display: flex; gap: 8px; margin-top: 6px; }
.cmt-add-btn { /* same reticle-corner treatment as the old .anchor-btn */ }
mark.cmt-hl-changes { background: var(--changes-bg, rgba(224,90,90,0.22)); border-bottom: 2px solid var(--changes, #e05a5a); color: inherit; }
mark.cmt-hl-info    { background: var(--info-bg, rgba(90,140,224,0.22)); border-bottom: 2px solid var(--info, #5a8ce0); color: inherit; }
.comment-popover { border: 1px solid var(--info, #5a8ce0); border-radius: 4px; background: var(--bg2, #1a2238); padding: 8px; margin-top: 6px; }
.cmt-pop-row { display: flex; gap: 8px; align-items: center; margin: 4px 0; }
.cmt-pop-quote { font-style: italic; opacity: 0.7; margin: 4px 0; }
.cmt-chip { /* reticle chip; .is-on uses the type color */ }
.cmt-chip-changes.is-on { color: var(--changes, #e05a5a); border-color: var(--changes, #e05a5a); }
.cmt-chip-info.is-on { color: var(--info, #5a8ce0); border-color: var(--info, #5a8ce0); }
```

Match the exact `--var` names and reticle-corner mixin used by the existing `.anchor-btn`/`.action-btn` blocks (read `server.py:666-808`) so the controls inherit the blueprint look (#45).

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 tests/test_server_comments_submit.py`
Expected: `OK` (page now ships all needles; submit still round-trips).

- [ ] **Step 6: Manual smoke (optional but recommended)**

Run `/viva` on a sample doc, select text, add two comments to one section, confirm two highlights + the toggling button, submit, and inspect `.viva/review-r1.json` for `comments[]`.

- [ ] **Step 7: Commit**

```bash
git add server.py tests/test_server_comments_submit.py
git commit -m "feat(ui): select→popover comment creation with typed highlights"
```

---

### Task 7: `server.py` JS/CSS — per-section thread list + per-comment settle; retire pin

Render each section's comments (pending this round) and carried-forward threads (`section.open_notes`) as a stacked list under the body, each with type, quote, note, and a per-thread **settle** button. Remove the pin button entirely; threads are open by default.

**Files:**
- Modify: `server.py` — `openThreadHTML`/`openNotesHTML` (`:1286-1312`) to render N cid threads each with its own `settle-btn`; add `renderCommentList(id)`; rewrite `settleOpenNotes` to settle by `cid`. Remove `.pin-btn` CSS (`:756-766`), the `#rpin-` button + handler, and the `.action-btn,…,.pin-btn` / `…pin-btn:focus-visible` group memberships (`:666`, `:915`).
- Test: `tests/test_server_open_notes.py` (Modify — drop pin needles, add cid settle).

**Interfaces:**
- Consumes: `section.open_notes` = `[{cid, quote, status, exchanges}]` (Task 3); `rState.verdicts[id].comments` (Task 5).
- Produces JS: `renderCommentList(id)` paints `#rclist-<id>` from this round's comments (edit/delete each); `settleOpenNotes(id, cid)` sets `settled` on that comment/thread and ships it as a one-element comment on submit so `open_notes.py` settles it.

- [ ] **Step 1: Update the open-notes test (failing)**

In `tests/test_server_open_notes.py`:
- Change the needle loop (`:74-76`) to drop `"pin note to next round"` and `"rpin-"`; keep `"openNotesHTML"`, `"open-thread"`, `"settleOpenNotes"`, `"section.open_notes"`; add `"renderCommentList"`.
- Change the `r2` fixture `open_notes` (`:41-49`) to the new cid shape:
  `[{"cid": "s1-c1", "quote": "intro", "status": "open", "exchanges": [{"round":1,"verdict":"changes","note":"tighten intro","response":"Shortened."}]}]`
- Change the `/submit` assertion (`:79-87`) to settle by cid:
  submit `{"id":"s1","verdict":"changes","comments":[{"cid":"s1-c1","type":"changes","note":"more","anchor":{"text":"intro","offset":0},"open":True,"settled":True}]}`
  and assert `out s1 comments[0]["settled"] is True`.

Run: `python3 tests/test_server_open_notes.py` → FAIL (pin needles gone; cid path absent).

- [ ] **Step 2: Render N threads with per-cid settle**

Replace `openThreadHTML` (`server.py:1302-1312`) with a per-cid renderer:

```javascript
function openThreadHTML(section) {
  const ex = section.open_notes;
  if (!Array.isArray(ex) || ex.length === 0) return '';
  return ex.map(t => {
    const cid = esc(t.cid || '');
    const quote = t.quote ? '<span class="open-thread-quote">&#9875; ' + esc(t.quote) + '</span>' : '';
    return '<div class="open-thread" id="rthread-' + cid + '" data-cid="' + cid + '">'
      + '<div class="open-thread-head">'
      +   '<span class="open-thread-label">open note</span>' + quote
      +   '<button type="button" class="settle-btn" id="rsettle-' + cid + '" data-cid="' + cid + '">&#10003; settle</button>'
      + '</div>'
      + '<div class="open-thread-body">' + openNotesHTML(t.exchanges) + '</div>'
      + '</div>';
  }).join('');
}
```

(`openNotesHTML` is unchanged — it already maps an exchanges array.)

- [ ] **Step 3: Wire per-cid settle + comment list**

Replace `settleOpenNotes` (`server.py:1617-1624`) with a cid version that records the settle as a one-element comment so the submit carries it:

```javascript
function settleOpenNotes(id, cid) {
  const cs = commentsOf(id);
  let c = cs.find(x => x.cid === cid);
  if (!c) { c = { cid, type: 'info', note: '', open: true, settled: true }; cs.push(c); }
  else c.settled = !c.settled;
  const thread = el('rthread-' + cid);
  const btn = el('rsettle-' + cid);
  if (thread) thread.classList.toggle('is-settled', !!c.settled);
  if (btn) btn.innerHTML = c.settled ? '&#10003; settled' : '&#10003; settle';
  syncCard(id);
}

// Paint this round's freshly-added comments under the section (edit/delete each).
function renderCommentList(id) {
  const host = el('rclist-' + id); if (!host) return;
  const cs = (rState.verdicts[id]?.comments || []).filter(c => !c.settled && c.note);
  host.innerHTML = cs.map(c =>
      '<div class="cmt v-' + c.type + '" data-cid="' + esc(c.cid) + '">'
    +   '<span class="cmt-type">' + c.type + '</span>'
    +   (c.anchor?.text ? '<span class="cmt-quote">&#9875; ' + esc(c.anchor.text) + '</span>' : '')
    +   '<span class="cmt-note">' + esc(c.note) + '</span>'
    +   '<button type="button" class="cmt-del" data-cid="' + esc(c.cid) + '" title="Remove">&times;</button>'
    + '</div>').join('');
  host.querySelectorAll('.cmt-del').forEach(b =>
    b.onclick = e => { e.stopPropagation(); removeComment(id, b.dataset.cid); });
}
```

Wire the carried-thread settle buttons in `buildReviewCard` (replace the old single `#rsettle-` handler at `:1419-1420`):

```javascript
  card.querySelectorAll('.settle-btn').forEach(b =>
    b.addEventListener('click', e => { e.stopPropagation(); settleOpenNotes(section.id, b.dataset.cid); }));
```

Call `renderCommentList(section.id)` at the end of `buildReviewCard` (and it is already re-invoked by `syncCard`).

- [ ] **Step 4: Remove the pin button and its CSS**

- Delete the `#rpin-` `<button>` (already removed in Task 5's action-row rewrite — verify none remains: `grep -n "rpin-\|pin-btn\|pin note to next round" server.py` returns nothing).
- Remove `.pin-btn` from the shared selector at `server.py:666` and `:915`, and delete the `.pin-btn` rule block (`:756-766`).

- [ ] **Step 5: Add comment-list / thread CSS**

Add styles mirroring the existing `.open-thread`/`.exchange` blocks (read `:809-851`), e.g. `.cmt`, `.cmt-type`, `.cmt-quote`, `.cmt-note`, `.cmt-del`, `.open-thread-quote`, with `v-changes`/`v-info` color slots matching the highlight colors.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 tests/test_server_open_notes.py` then `python3 tests/test_server_comments_submit.py`
Expected: both `OK`.

- [ ] **Step 7: Confirm pin is fully gone**

Run: `grep -rn "rpin-\|pin-btn\|pin note to next round\|keep open" server.py tests/`
Expected: no matches.

- [ ] **Step 8: Commit**

```bash
git add server.py tests/test_server_open_notes.py
git commit -m "feat(ui): per-comment thread list + cid settle; retire pin button"
```

---

### Task 8: `SKILL.md` — verdict table, Anchors, Open-notes, pin retirement

Make the agent's consumption contract match the new model. Without this the UI/server work is inert — the agent still reads one `note`+`anchor`.

**Files:**
- Modify: `SKILL.md` — the verdict table (`:110-118`), the rewrite step's anchor prose (`:113`, `:127`), the **Open notes** section (`:291-302`), and any `keep open`/single-anchor wording.

- [ ] **Step 1: Rewrite the verdict table rows**

Replace the `changes`/`info` rows (`SKILL.md:113-114`) so they iterate `comments[]`:

> | `changes`/`info` | The section carries a `comments` array. For **each** comment: apply its `note` as a targeted edit (or, for `type: "info"`, answer the question and fold the answer in). When the comment has an `anchor`, scope the edit to `anchor.text` at `anchor.offset` in the section source (offset disambiguates a phrase that appears twice); an un-anchored comment scopes to the whole section. If a comment carries `attachments`, `Read` each first. |

Add a line noting the **derived** verdict: a section with no `comments` is `approved`; any `changes` comment makes the section `changes`, else `info`.

- [ ] **Step 2: Update step 4 (rewrite) and the open-note update calls**

- `:127` — replace the single-anchor sentence with: "loop over `comments[]`; each comment's `anchor.offset` locates its edit, `anchor.text` confirms it."
- The `open_notes.py update` examples (`:131-137`, `:154-156`) — change `--response "s2=…"` to `--response "<cid>=…"` (per comment), and note that approving a section settles all its threads.

- [ ] **Step 3: Rewrite the Open notes section**

In **Open notes (carried across rounds)** (`:291-302`): every comment is an open thread **by default** (no opt-in pin); the store is keyed by `cid`; the reviewer **settles** a thread (or approves the section) to drop it. Remove the "ticks keep open across rounds" / pin language. Note the store now carries each thread's `quote`.

- [ ] **Step 4: Verify no stale single-anchor / pin language remains**

Run: `grep -ni "keep open\|pin note\|one anchor\|anchor string\|single anchor" SKILL.md`
Expected: no matches (or only historical Revision-History text, which is exempt).

- [ ] **Step 5: Commit**

```bash
git add SKILL.md
git commit -m "docs(skill): consume comments[] — derived verdict, cid threads, retire pin"
```

---

### Task 9: End-to-end test — multi-comment round-trip + legacy fallback

One integration test that exercises the headline flow across `/submit` → `open_notes.py` → `parse_sections.py` → next round, plus the legacy single-`anchor` read.

**Files:**
- Modify: `tests/test_server_anchored_notes.py` — rename intent to multi-comment; keep a legacy-compat assertion.

**Interfaces:**
- Consumes: the full stack from Tasks 1–7.

- [ ] **Step 1: Rewrite the test for the comment model**

Replace the body of `tests/test_server_anchored_notes.py:60-81` so it:
1. Submits a section with two anchored comments (changes + info) and asserts `out` has `comments[2]` with `cid`, `type`, `anchor.offset`, derived `verdict == "changes"`.
2. Submits an un-anchored comment on another section → `comments[0]` with no `anchor`, verdict `info`.
3. Asserts the served page no longer ships the retired needles (`assert "anchor-chip" not in page`, `assert "rpin-" not in page`) and does ship `renderHighlights`, `openCommentPopover`.

```python
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "comments": [
                {"cid": "s1-c1", "type": "changes", "note": "unsupported",
                 "anchor": {"text": "sub-second latency", "offset": 9}, "open": True, "settled": False},
                {"cid": "s1-c2", "type": "info", "note": "errors?", "open": True, "settled": False}]},
            {"id": "s2", "verdict": "info", "comments": [
                {"cid": "s2-c1", "type": "info", "note": "whole-section question", "open": True, "settled": False}]}]})
        out = json.loads((viva / "out1.json").read_text())
        s1 = next(s for s in out["sections"] if s["id"] == "s1")
        s2 = next(s for s in out["sections"] if s["id"] == "s2")
        assert s1["verdict"] == "changes" and len(s1["comments"]) == 2, s1
        assert s1["comments"][0]["anchor"]["offset"] == 9, s1
        assert "anchor" not in s2["comments"][0], s2
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        for needle in ("renderHighlights", "openCommentPopover", "deriveVerdict"):
            assert needle in page, f"page missing: {needle}"
        for gone in ("anchor-chip", "rpin-", "anchorSelection"):
            assert gone not in page, f"retired control still present: {gone}"
```

- [ ] **Step 2: Add a legacy-compat unit check**

Append a small unit test `tests/test_open_notes_unit.py::test_legacy_section_anchor_ignored` asserting `open_notes.update` treats a section with a bare legacy `{anchor, note, open}` (no `comments`) as a no-op (no crash), so an in-flight old round file can't break the store.

```python
def test_legacy_section_without_comments_is_noop():
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "note": "x",
                              "anchor": "y", "open": True}]}
    assert open_notes.update({}, 1, verdicts, _input(), {}) == {}
```

- [ ] **Step 3: Run the full suite**

Run: `python3 -m pytest tests/ -v` (and any bespoke `python3 tests/test_*.py` runners the repo uses — check `scripts/` or CI config).
Expected: all PASS, including the existing attachment/annotation/diff tests (zero-regression).

- [ ] **Step 4: Commit**

```bash
git add tests/test_server_anchored_notes.py tests/test_open_notes_unit.py
git commit -m "test: end-to-end multi-comment round-trip + legacy fallback"
```

---

## Self-Review

**Spec coverage:**
- Data model `comments[]` with `{cid, type, note, anchor:{text,offset}, open, settled}` → Tasks 1, 5.
- Derived verdict / toggling button → Task 5.
- Interaction model A (popover + highlights) → Task 6.
- Per-comment threads, settle, retire pin → Tasks 1, 7; SKILL.md Task 8.
- `open_notes.py` cid re-key → Task 1; `parse_sections.py` attach → Task 3; `revision_history.py` → Task 2; server ledger → Task 4.
- Anchor offset disambiguation → Tasks 5 (`offsetInSource`), 6, 8.
- Backward compatibility (no comments == today; legacy single-anchor) → Tasks 5, 9.
- SKILL.md consumption → Task 8.
- Tests for both test files + new units → throughout; #65 pin needles updated in Task 7.

**Known seams to verify during execution (not placeholders — confirm against live code):**
- Exact `--var` names and the reticle-corner mixin for buttons/chips (read `server.py:666-808` before writing Task 6/7 CSS).
- Where `_pendingMarkdown` renders into `.section-content` on first card open — `renderHighlights(id)` must be called *after* that render (Task 6 Step 3).
- Whether `setReviewVerdict` has callers besides the deleted buttons before removing it (Task 5 Step 4).
- Attachments stay **per-section** (`images` on the section object) — per-comment attachments are out of scope (YAGNI); confirm `extract_attachments` still keys by section id.

**Type consistency:** comment shape `{cid, type, note, anchor:{text,offset}, open, settled}` is identical across Tasks 1/5/6/7/9; thread record `{cid, title, quote, status, exchanges}` identical across Tasks 1/2/3; `open_notes` attachment shape `{cid, quote, status, exchanges}` identical across Tasks 3/7. `deriveVerdict` logic matches `submitReview`'s inline roll-up (Task 5).
