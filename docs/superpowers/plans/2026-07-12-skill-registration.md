# Skill Registration (viva-qa / viva-diff) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/viva-qa` and `/viva-diff` register as real, invocable Claude Code skills in every consumer install — today only `/viva` registers, because the other two are loose sibling `.md` files instead of one-directory-per-skill.

**Architecture:** Claude Code discovers one skill per directory via that directory's `SKILL.md`. Move `brainstorming-qa.md` and `diff.md` into their own `SKILL.md`-named directories, eliminate the fragile git-tracked symlink that currently stands in for viva's own `SKILL.md`, drop the manual git-clone install channel (it can never carry sub-skills — personal-skill discovery is one level deep), and make `$VIVA_DIR` resolve from the plugin cache only, failing loud on a missing install instead of silently preferring a stale personal copy.

**Tech Stack:** stdlib-only Python 3.8–3.13 (no new dependencies), Claude Code skill frontmatter (YAML-ish, `name:`/`description:` keys), bash resolve blocks inside `SKILL.md` prose, git for file moves.

## Global Constraints

- Full design: `docs/superpowers/specs/2026-07-12-skill-registration-design.md`. Pre-mortem register (verified at audit/acceptance time): `docs/studious/premortems/2026-07-12-skill-registration-design.md`.
- Branch: `worktree-101`. All commits land there — do not switch branches.
- stdlib-only, Python 3.8+ compatible. Any new `.py` file needs `from __future__ import annotations` for 3.8-safe `X | None` hints (the CI matrix runs 3.8; see `server.py:8` for the existing precedent).
- No pytest — tests are self-running scripts invoked as `python3 tests/test_<name>.py`, each function ends `print("  ok  test_name")`, `main()` calls them all and prints `"OK (N tests)"` (see `tests/test_headless_contract_doc.py` for the exact convention to match).
- Conventional commit messages (this repo runs `python-semantic-release` with the angular preset off `main` — see `pyproject.toml`): `fix:` for the registration bug fix, `docs:` for doc-only cross-reference updates, `test:` for test-only changes, `chore:` for the PRODUCT.md caveat removal. `docs:`/`chore:`/`test:` do not trigger a version bump; `fix:` does.
- Out of scope, do not touch: `server.py` logic, `scripts/*.py`, `.viva/` file schema, `.claude-plugin/plugin.json` (already correct — `"skills": "./.claude/skills"` discovers however many subdirectories exist under it, no edit needed), dated files under `docs/superpowers/specs/`, `docs/superpowers/plans/`, `docs/studious/premortems/` (historical records), `CHANGELOG.md` (auto-generated).
- The PRODUCT.md edit (Task 3) is gated on manual verification actually passing — do not delete that bullet preemptively.

---

### Task 1: Register viva-qa and viva-diff as their own skills

**Files:**
- Create: `tests/test_skill_registration.py`
- Modify: `SKILL.md` (repo root) → moved to `.claude/skills/viva/SKILL.md:12-17,39-43`
- Modify: `.claude/skills/viva/brainstorming-qa.md` → moved to `.claude/skills/viva-qa/SKILL.md:63-67`
- Modify: `.claude/skills/viva/diff.md` → moved to `.claude/skills/viva-diff/SKILL.md:30-34`

**Interfaces:**
- Consumes: nothing — this is the first task.
- Produces: the on-disk layout Task 2 and Task 3 depend on — `viva`, `viva-qa`, `viva-diff` each a directory under `.claude/skills/` holding exactly one `SKILL.md` regular file with matching `name:` frontmatter; no root `SKILL.md`; a passing `tests/test_skill_registration.py`.

- [ ] **Step 1: Write the failing structural test**

Create `tests/test_skill_registration.py`:

```python
#!/usr/bin/env python3
"""Structural guard for skill registration (#101).

Claude Code's skill discovery registers one skill per directory via that
directory's SKILL.md — a loose sibling .md with skill frontmatter never
registers, and a symlinked SKILL.md is fragile across install channels
(zip downloads, Windows checkouts can drop or mishandle it). This is a
filesystem-shape test, not a Claude Code discovery test — it can't invoke
the real plugin loader, so it checks the invariants discovery depends on
instead.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / ".claude" / "skills"
EXPECTED_SKILLS = {"viva", "viva-qa", "viva-diff"}


def _frontmatter_name(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    frontmatter = text[4:end]
    m = re.search(r"^name:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
    return m.group(1) if m else None


def test_expected_skill_set_registers():
    found = {}
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.is_file():
            continue
        name = _frontmatter_name(skill_md)
        if name is not None:
            found[d.name] = name

    missing = EXPECTED_SKILLS - set(found)
    assert not missing, f"missing skill directories: {sorted(missing)}"

    for dirname, name in found.items():
        if dirname in EXPECTED_SKILLS:
            assert name == dirname, (
                f"{dirname}/SKILL.md declares name {name!r}, expected {dirname!r}"
            )
    print("  ok  test_expected_skill_set_registers")


def test_skill_md_files_are_regular_files():
    for name in EXPECTED_SKILLS:
        skill_md = SKILLS_DIR / name / "SKILL.md"
        assert skill_md.is_file(), f"{skill_md} does not exist"
        assert not skill_md.is_symlink(), (
            f"{skill_md} is a symlink — a symlinked SKILL.md is fragile "
            "across install channels and can dangle, silently "
            "deregistering the skill"
        )
    print("  ok  test_skill_md_files_are_regular_files")


def test_no_loose_sibling_skill_files():
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file() or f.name == "SKILL.md" or f.suffix != ".md":
                continue
            if _frontmatter_name(f) is not None:
                raise AssertionError(
                    f"{f} carries skill frontmatter but is not named "
                    "SKILL.md — it will never register as a skill (one "
                    "skill per directory, via that directory's SKILL.md "
                    "only)"
                )
    print("  ok  test_no_loose_sibling_skill_files")


def test_no_root_skill_md():
    root_skill_md = ROOT / "SKILL.md"
    assert not root_skill_md.exists(), (
        f"{root_skill_md} exists — the manual git-clone install channel "
        "was dropped; the only SKILL.md lives under .claude/skills/viva/"
    )
    print("  ok  test_no_root_skill_md")


def main():
    test_expected_skill_set_registers()
    test_skill_md_files_are_regular_files()
    test_no_loose_sibling_skill_files()
    test_no_root_skill_md()
    print("OK (4 tests)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the test to verify it fails against the current tree**

Run: `python3 tests/test_skill_registration.py`
Expected: `AssertionError: missing skill directories: ['viva-diff', 'viva-qa']` (fails on the first assertion — `viva-qa`/`viva-diff` directories don't exist yet).

- [ ] **Step 3: Eliminate the symlink and move viva's own SKILL.md into place**

`.claude/skills/viva/SKILL.md` is a git-tracked relative symlink to the repo-root `SKILL.md`. Replace it with the real file — `git rm` the symlink first (a `git mv` onto an existing path would refuse), then `git mv` the root file in:

```bash
git rm .claude/skills/viva/SKILL.md
git mv SKILL.md .claude/skills/viva/SKILL.md
```

Now edit `.claude/skills/viva/SKILL.md` (the file that just landed there) in two places.

Modify: `.claude/skills/viva/SKILL.md:12-17` (Q&A cross-reference — drop the repo-relative file pointer, name the sibling skill instead):

```markdown
## Brainstorming Q&A

viva exposes a `/viva-qa` skill for batch Q&A sessions. The superpowers
`brainstorming` skill calls `/viva-qa` directly when viva is installed — no
`install.sh` patch is needed. See the sibling `/viva-qa` skill for the full
invocation contract.
```

Modify: `.claude/skills/viva/SKILL.md:39-43` (resolve block — cache-only, deterministic across multiple cached versions, fails loud):

Replace:
```bash
# Resolve the skill dir once — direct path first, find only as fallback.
VIVA_DIR=~/.claude/skills/viva
[ -f "$VIVA_DIR/server.py" ] || \
  VIVA_DIR=$(find ~/.claude/plugins/cache -name "server.py" -path "*/viva*" -maxdepth 6 2>/dev/null \
             | xargs -I{} dirname {} | head -1)
```

With:
```bash
# Resolve the skill dir from the installed plugin cache — no personal-skill
# fallback (a leftover ~/.claude/skills/viva would shadow a fresh install).
VIVA_DIR=$(find ~/.claude/plugins/cache -maxdepth 6 -path "*/viva/*" -name server.py -print0 2>/dev/null \
           | xargs -0 ls -t 2>/dev/null | head -1)
VIVA_DIR=${VIVA_DIR%/server.py}
[ -f "$VIVA_DIR/server.py" ] || { echo "viva: server.py not found — install the viva plugin (/plugin install viva@jacquardlabs-marketplace)"; exit 1; }
```

- [ ] **Step 4: Move brainstorming-qa.md into its own viva-qa directory**

```bash
mkdir -p .claude/skills/viva-qa
git mv .claude/skills/viva/brainstorming-qa.md .claude/skills/viva-qa/SKILL.md
```

Modify: `.claude/skills/viva-qa/SKILL.md:63-67` (same resolve-block hardening, `viva-qa:` prefix):

Replace:
```bash
VIVA_DIR=~/.claude/skills/viva
[ -f "$VIVA_DIR/server.py" ] || \
  VIVA_DIR=$(find ~/.claude/plugins/cache -name "server.py" -path "*/viva*" -maxdepth 6 2>/dev/null \
             | xargs -I{} dirname {} | head -1)
[ -f "$VIVA_DIR/server.py" ] || { echo "viva-qa: cannot locate server.py"; exit 1; }
```

With:
```bash
VIVA_DIR=$(find ~/.claude/plugins/cache -maxdepth 6 -path "*/viva/*" -name server.py -print0 2>/dev/null \
           | xargs -0 ls -t 2>/dev/null | head -1)
VIVA_DIR=${VIVA_DIR%/server.py}
[ -f "$VIVA_DIR/server.py" ] || { echo "viva-qa: server.py not found — install the viva plugin (/plugin install viva@jacquardlabs-marketplace)"; exit 1; }
```

- [ ] **Step 5: Move diff.md into its own viva-diff directory**

```bash
mkdir -p .claude/skills/viva-diff
git mv .claude/skills/viva/diff.md .claude/skills/viva-diff/SKILL.md
```

Modify: `.claude/skills/viva-diff/SKILL.md:30-34` (same resolve-block hardening, `viva-diff:` prefix):

Replace:
```bash
VIVA_DIR=~/.claude/skills/viva
[ -f "$VIVA_DIR/server.py" ] || \
  VIVA_DIR=$(find ~/.claude/plugins/cache -name "server.py" -path "*/viva*" -maxdepth 6 2>/dev/null \
             | xargs -I{} dirname {} | head -1)
[ -f "$VIVA_DIR/server.py" ] || { echo "viva-diff: cannot locate server.py"; exit 1; }
```

With:
```bash
VIVA_DIR=$(find ~/.claude/plugins/cache -maxdepth 6 -path "*/viva/*" -name server.py -print0 2>/dev/null \
           | xargs -0 ls -t 2>/dev/null | head -1)
VIVA_DIR=${VIVA_DIR%/server.py}
[ -f "$VIVA_DIR/server.py" ] || { echo "viva-diff: server.py not found — install the viva plugin (/plugin install viva@jacquardlabs-marketplace)"; exit 1; }
```

- [ ] **Step 6: Run the structural test again, verify it passes**

Run: `python3 tests/test_skill_registration.py`
Expected:
```
  ok  test_expected_skill_set_registers
  ok  test_skill_md_files_are_regular_files
  ok  test_no_loose_sibling_skill_files
  ok  test_no_root_skill_md
OK (4 tests)
```

- [ ] **Step 7: Run the full existing test suite to confirm nothing else broke**

Run: `for f in tests/test_*.py; do python3 "$f" || echo "FAILED: $f"; done`
Expected: every line ends `OK (...)`, no `FAILED:` lines. (None of the moved files are imported by Python — they're prose the agent reads — so no import breaks; this step exists to catch anything unexpected, e.g. a test that shells out and greps for the old paths.)

- [ ] **Step 8: Commit**

`git rm`/`git mv` in Steps 3–5 already staged every side of every move (the
root `SKILL.md` deletion, the symlink deletion, and all three new file
paths) — only the newly created test file needs an explicit `git add`:

```bash
git add tests/test_skill_registration.py
git commit -m "fix: register viva-qa and viva-diff as their own skills (#101)

Claude Code discovers one skill per directory via that directory's
SKILL.md. brainstorming-qa.md and diff.md were loose sibling files
inside .claude/skills/viva/ and never registered. Move each into its
own SKILL.md-named directory, and replace the fragile git-tracked
symlink that stood in for viva's own SKILL.md with the real file."
```

Verify with `git status` before committing: no path should remain at
repo-root `SKILL.md`, and `.claude/skills/viva/SKILL.md` should show as a
renamed/added regular file, not a symlink.

---

### Task 2: Update cross-references and docstrings to the new paths

**Files:**
- Modify: `README.md:30-52,116,144-158,168-180`
- Modify: `CLAUDE.md:55-57`
- Modify: `docs/headless-contract.md:63,71,201,202,266`
- Modify: `server.py:25-27`
- Modify: `tests/test_server_diff.py:230`
- Modify: `tests/test_server_qa_complete_shutdown.py:5,10,54`
- Modify: `tests/test_server_qa_review_handoff.py:217`

**Interfaces:**
- Consumes: the file layout Task 1 produced (paths this task's edits point at must exist — run Task 1 first).
- Produces: no dead references to the old paths anywhere outside historical/dated docs; the manual install channel replaced by a migration note carrying the doc-confirmed shadowing warning.

- [ ] **Step 1: Update README.md's install section — drop the manual-clone channel, add the migration note**

Modify: `README.md:30-52`. Replace:
```markdown
## Install

Install via the Jacquard Labs marketplace:

```bash
/plugin marketplace add jacquardlabs/marketplace
/plugin install viva@jacquardlabs-marketplace
```

Or install this plugin directly:

```bash
/plugin marketplace add jacquardlabs/viva
/plugin install viva@viva
```

Requires Python 3.8+ and Claude Code.

Or install manually via git clone:

```bash
git clone https://github.com/jacquardlabs/viva ~/.claude/skills/viva
```
```

With:
```markdown
## Install

Install via the Jacquard Labs marketplace:

```bash
/plugin marketplace add jacquardlabs/marketplace
/plugin install viva@jacquardlabs-marketplace
```

Or install this plugin directly:

```bash
/plugin marketplace add jacquardlabs/viva
/plugin install viva@viva
```

Requires Python 3.8+ and Claude Code.

**Previously installed via `git clone`?** Delete `~/.claude/skills/viva`
before installing the plugin above. Leaving it in place registers a second
`viva` skill — personal skills take invocation precedence over a plugin
skill of the same name, so bare `/viva` would keep running your old cloned
copy indefinitely while `/viva-qa` and `/viva-diff` (which the clone never
had) run the current plugin version.
```

- [ ] **Step 2: Update README.md's Q&A contract pointer**

Modify: `README.md:116`. Replace:
```markdown
See `.claude/skills/viva/brainstorming-qa.md` for the full contract.
```
With:
```markdown
See the `/viva-qa` skill for the full contract.
```

- [ ] **Step 3: Update README.md's Server CLI and `--split-on` examples to resolve `$VIVA_DIR` from the plugin cache**

Modify: `README.md:144-158`. Replace:
```markdown
## Server CLI (advanced)

```bash
# Review mode
python3 ~/.claude/skills/viva/server.py \
  --mode review \
  --input .viva/review-input-r1.json \
  --output .viva/review-r1.json

# Q&A mode (brainstorming integration)
python3 ~/.claude/skills/viva/server.py \
  --mode qa \
  --input .viva/qa-input.json \
  --output .viva/answers.json
```
```

With:
```markdown
## Server CLI (advanced)

Resolve `$VIVA_DIR` from the installed plugin cache first — the same
resolve every skill uses internally:

```bash
VIVA_DIR=$(find ~/.claude/plugins/cache -maxdepth 6 -path "*/viva/*" -name server.py -print0 2>/dev/null \
           | xargs -0 ls -t 2>/dev/null | head -1)
VIVA_DIR=${VIVA_DIR%/server.py}

# Review mode
python3 "$VIVA_DIR/server.py" \
  --mode review \
  --input .viva/review-input-r1.json \
  --output .viva/review-r1.json

# Q&A mode (brainstorming integration)
python3 "$VIVA_DIR/server.py" \
  --mode qa \
  --input .viva/qa-input.json \
  --output .viva/answers.json
```
```

Modify: `README.md:168-180` (`--split-on` examples — now three lines lower than the original numbering after Step 3's insertion above; locate by content, not line number). Replace:
```markdown
```bash
# Round 1
python3 ~/.claude/skills/viva/scripts/parse_sections.py PLAN.md \
  --output .viva/review-input-r1.json --round 1 \
  --split-on '^Task \d+'

# Round 2+
python3 ~/.claude/skills/viva/scripts/parse_sections.py PLAN.md \
  --output .viva/review-input-r2.json --round 2 \
  --prior-input .viva/review-input-r1.json \
  --prior-verdicts .viva/review-r1.json \
  --split-on '^Task \d+'
```
```

With:
```markdown
```bash
# Round 1 ($VIVA_DIR resolved as in Server CLI above)
python3 "$VIVA_DIR/scripts/parse_sections.py" PLAN.md \
  --output .viva/review-input-r1.json --round 1 \
  --split-on '^Task \d+'

# Round 2+
python3 "$VIVA_DIR/scripts/parse_sections.py" PLAN.md \
  --output .viva/review-input-r2.json --round 2 \
  --prior-input .viva/review-input-r1.json \
  --prior-verdicts .viva/review-r1.json \
  --split-on '^Task \d+'
```
```

- [ ] **Step 4: Update CLAUDE.md's skill-path references**

Modify: `CLAUDE.md:55-57`. Replace:
```markdown
**New skills in this branch:** `.claude/skills/viva/brainstorming-qa.md`
(`/viva-qa` primitive) and `.claude/skills/viva/diff.md` (`/viva-diff` skill)
follow the same import-only-schema rule.
```
With:
```markdown
**New skills in this branch:** `.claude/skills/viva-qa/SKILL.md`
(`/viva-qa` primitive) and `.claude/skills/viva-diff/SKILL.md` (`/viva-diff`
skill) follow the same import-only-schema rule.
```

- [ ] **Step 5: Update docs/headless-contract.md's five live-file mentions**

Modify: `docs/headless-contract.md:63` (inside a paragraph spanning ~60-66). Replace:
```markdown
Nothing in `server.py` enforces that the two agree — a caller that launches
`--mode qa` but writes `"mode": "review"` into the input JSON gets
undefined-by-contract behavior. Every existing caller (`SKILL.md`,
`.claude/skills/viva/brainstorming-qa.md`, `.claude/skills/viva/diff.md`)
keeps them in sync by convention, not by an enforced invariant — a new
caller needs to keep them in sync too.
```
With:
```markdown
Nothing in `server.py` enforces that the two agree — a caller that launches
`--mode qa` but writes `"mode": "review"` into the input JSON gets
undefined-by-contract behavior. Every existing caller (`SKILL.md`,
`/viva-qa`, `/viva-diff`) keeps them in sync by convention, not by an
enforced invariant — a new caller needs to keep them in sync too.
```

Modify: `docs/headless-contract.md:71`. Replace:
```markdown
existing skills (`SKILL.md`, `brainstorming-qa.md`, `diff.md`) follow, not
```
With:
```markdown
existing skills (`SKILL.md`, `/viva-qa`, `/viva-diff`) follow, not
```

Modify: `docs/headless-contract.md:201` (table row — replace the two filename mentions inside the `POST /next-round` cell). Replace:
```markdown
| `POST /next-round` | yes | The endpoint a caller uses to advance a running session: pushes a new round's JSON to the server without tearing the process down. Read `output` from the JSON body (preferred — travels like every other POST field; this is the form `SKILL.md`'s own loop and `brainstorming-qa.md`'s hand-off example both use) or the legacy `?output=` query-string param (still honored as a fallback, and still what `diff.md`'s re-arm step sends — narrowing that to the preferred form is a separate, future cleanup, not part of this contract change). If the payload has `"sections"`, it is validated with `validate_review_input` before being accepted. This is also the exact mechanism the qa→review hand-off (§7) uses. Guarded by the same loopback-Origin check and 256 MiB body cap as `/submit` (#117). |
```
With:
```markdown
| `POST /next-round` | yes | The endpoint a caller uses to advance a running session: pushes a new round's JSON to the server without tearing the process down. Read `output` from the JSON body (preferred — travels like every other POST field; this is the form `SKILL.md`'s own loop and `/viva-qa`'s hand-off example both use) or the legacy `?output=` query-string param (still honored as a fallback, and still what `/viva-diff`'s re-arm step sends — narrowing that to the preferred form is a separate, future cleanup, not part of this contract change). If the payload has `"sections"`, it is validated with `validate_review_input` before being accepted. This is also the exact mechanism the qa→review hand-off (§7) uses. Guarded by the same loopback-Origin check and 256 MiB body cap as `/submit` (#117). |
```

Modify: `docs/headless-contract.md:202`. Replace:
```markdown
| `POST /complete` | yes | Ends the session. Accepts an optional JSON body (existing callers pass a free-form summary, e.g. `{rounds_total, sections_total, sections_revised}` — not schema-enforced) used only for the SSE `"complete"` event's payload. Starts a 2-second shutdown timer so the browser's SSE `"complete"` handler has time to render before the process exits. Guarded by the same loopback-Origin check and 256 MiB body cap as `/submit`. A qa-mode session's finish sequence must call this once `answers.json` exists (see `brainstorming-qa.md` step 4) unless it is handing off to a review round (§7) — otherwise the process and its `server.url` leak indefinitely. |
```
With:
```markdown
| `POST /complete` | yes | Ends the session. Accepts an optional JSON body (existing callers pass a free-form summary, e.g. `{rounds_total, sections_total, sections_revised}` — not schema-enforced) used only for the SSE `"complete"` event's payload. Starts a 2-second shutdown timer so the browser's SSE `"complete"` handler has time to render before the process exits. Guarded by the same loopback-Origin check and 256 MiB body cap as `/submit`. A qa-mode session's finish sequence must call this once `answers.json` exists (see `/viva-qa` step 4) unless it is handing off to a review round (§7) — otherwise the process and its `server.url` leak indefinitely. |
```

Modify: `docs/headless-contract.md:266`. Replace:
```markdown
This is **not** a third `--mode` value. A caller launches `--mode qa`
exactly as `.claude/skills/viva/brainstorming-qa.md` does today, waits for
`answers.json`, and — instead of tearing the server down — POSTs an
```
With:
```markdown
This is **not** a third `--mode` value. A caller launches `--mode qa`
exactly as `/viva-qa` does today, waits for `answers.json`, and — instead
of tearing the server down — POSTs an
```

- [ ] **Step 6: Update server.py's install-location comment**

Modify: `server.py:25-27`. Replace:
```python
# The sibling `scripts/` dir holds the shared schema contract (section_key, the
# ledger rule, boundary validation). It sits beside server.py in both the repo
# and the installed skill (`~/.claude/skills/viva/{server.py,scripts/}`).
```
With:
```python
# The sibling `scripts/` dir holds the shared schema contract (section_key, the
# ledger rule, boundary validation). It sits beside server.py in both the repo
# and the installed plugin cache (`~/.claude/plugins/cache/**/viva/{server.py,scripts/}`).
```

- [ ] **Step 7: Update the three test docstrings**

Modify: `tests/test_server_diff.py:230`. Replace:
```python
    """diff.md step 4's empty-diff branch (#116): the human requests a
```
With:
```python
    """/viva-diff step 4's empty-diff branch (#116): the human requests a
```

Modify: `tests/test_server_qa_complete_shutdown.py:5`. Replace:
```python
Before this fix, `brainstorming-qa.md`'s documented finish steps read
```
With:
```python
Before this fix, `/viva-qa`'s documented finish steps read
```

Modify: `tests/test_server_qa_complete_shutdown.py:10`. Replace:
```python
(mirroring `brainstorming-qa.md`'s corrected step 4), and assert the process
```
With:
```python
(mirroring `/viva-qa`'s corrected step 4), and assert the process
```

Modify: `tests/test_server_qa_complete_shutdown.py:54`. Replace:
```python
        # Mirrors brainstorming-qa.md's fixed step 4: /complete once
```
With:
```python
        # Mirrors /viva-qa's fixed step 4: /complete once
```

Modify: `tests/test_server_qa_review_handoff.py:217`. Replace:
```python
    #    field — see brainstorming-qa.md "Hand off to a review session in the
```
With:
```python
    #    field — see /viva-qa "Hand off to a review session in the
```

- [ ] **Step 8: Grep sweep — confirm zero stale references remain**

Run:
```bash
grep -rn 'brainstorming-qa\.md\|skills/viva/diff\.md\|skills/viva/server\.py\|skills/viva/scripts' \
  --include='*.md' --include='*.py' .
```
Expected: no output. (`docs/superpowers/specs/2026-07-12-skill-registration-design.md`, `docs/superpowers/plans/2026-07-12-skill-registration.md` — this plan itself — and `docs/studious/premortems/2026-07-12-skill-registration-design.md` are the only files that should ever have matched, and they discuss the old paths as history/instructions, not live references; if the grep matches inside any of those three files with `--include='*.md'`, that is expected and not a bug — re-run excluding `docs/superpowers/` and `docs/studious/premortems/` if the raw count is confusing:)
```bash
grep -rn 'brainstorming-qa\.md\|skills/viva/diff\.md\|skills/viva/server\.py\|skills/viva/scripts' \
  --include='*.md' --include='*.py' . \
  | grep -v '^\./docs/superpowers/' | grep -v '^\./docs/studious/premortems/'
```
Expected: no output.

- [ ] **Step 9: Run the full test suite again**

Run: `for f in tests/test_*.py; do python3 "$f" || echo "FAILED: $f"; done`
Expected: every line ends `OK (...)`, no `FAILED:` lines.

- [ ] **Step 10: Commit**

```bash
git add README.md CLAUDE.md docs/headless-contract.md server.py \
  tests/test_server_diff.py tests/test_server_qa_complete_shutdown.py \
  tests/test_server_qa_review_handoff.py
git commit -m "docs: repoint cross-references to the new skill paths (#101)

README's install section drops the manual git-clone channel in favor
of a migration note warning about personal-skill shadowing; the Q&A
contract pointer and Server CLI / --split-on examples are updated to
resolve \$VIVA_DIR from the plugin cache. CLAUDE.md, the headless
contract doc, server.py's install-location comment, and three test
docstrings are repointed to the new file names."
```

---

### Task 3: Manual install verification, then close the loop

This task cannot be scripted inside a coding session — `/plugin marketplace add`, `/plugin install`, and skill invocation are interactive Claude Code actions performed in a live session, not shell commands a test suite can drive. **Perform Steps 1–3 by hand in a real Claude Code session**, then apply Steps 4–6 based on what you observe.

**Files:**
- Modify: `README.md` (conditionally — only if Step 3 finds the direct-repo channel broken)
- Modify: `PRODUCT.md` (only after Steps 1–2 both pass)

**Interfaces:**
- Consumes: this branch's checkout, installed locally as a plugin via a local-marketplace path (Steps 1–2 toggle `~/.claude/skills/viva`'s presence on one machine — no second machine needed); the real `jacquardlabs/viva` GitHub repo for Step 3's channel check.
- Produces: the two-pass verification evidence the design's Operational readiness section requires, and the PRODUCT.md caveat removal it gates.

**No second machine required.** Both passes run on one machine by toggling
the *state* the design cares about (personal clone present vs. absent), not
by finding separate hardware. Do not use `claude --plugin-dir` for this —
doc-confirmed, that flag loads straight from disk and never touches
`~/.claude/plugins/cache`, so it would exercise skill discovery correctly
but give a **false** `server.py not found` on the `$VIVA_DIR` guard (which
deliberately only searches the cache) even though the plugin is loaded
correctly. Use a **local marketplace path** instead — doc-confirmed to copy
into `~/.claude/plugins/cache` at install time, exactly like a real
consumer install, so it exercises the actual resolve logic this design
ships. Run everything below from *outside* this worktree (a fresh shell in
your home directory is fine) so project-level skill discovery inside the
viva repo itself can't mask a plugin-level registration failure.

- [ ] **Step 1: MANUAL — clean-machine pass (personal clone absent)**

```bash
# Get the stale clone out of the way — don't delete it yet, Step 2 reuses it.
mv ~/.claude/skills/viva ~/.claude/skills/viva.bak 2>/dev/null || true
```

In a Claude Code session, from a directory outside this worktree:
```
/plugin marketplace add /Users/bryan/Projects/viva/.claude/worktrees/101
```
Note the marketplace name it reports registering under (likely `viva`, the
plugin's own name — use whatever it actually prints), then:
```
/plugin install viva@<reported-marketplace-name>
```
1. Confirm all three skills are listed in the skill registry: `viva`,
   `viva-qa`, `viva-diff`.
2. Write a minimal `.viva/qa-input.json` (see
   `.claude/skills/viva-qa/SKILL.md`'s Input contract) and invoke
   `/viva-qa` far enough to confirm the `$VIVA_DIR` resolve succeeds
   (server starts, browser tab opens) rather than hitting the `server.py
   not found` guard.

Record the outcome (pass/fail) before continuing. Leave the plugin
installed — Step 2 reuses it.

- [ ] **Step 2: MANUAL — shadowing-case pass (personal clone present)**

```bash
# Restore the stale clone — this is the actual pre-existing personal
# skill this design's migration note warns about, not a simulated one.
mv ~/.claude/skills/viva.bak ~/.claude/skills/viva
```

With the plugin still installed from Step 1 and the clone now back:
1. Invoke bare `/viva` and confirm it resolves to the **personal** skill
   (the old clone) — the doc-confirmed precedence this design's migration
   note now warns about.
2. Invoke `/viva-qa` or `/viva-diff` and confirm they resolve to the
   **plugin** version (the clone never had these).
3. Delete the clone for real this time (matching the actual README
   migration instruction, not the temporary rename from Step 1):
   ```bash
   rm -rf ~/.claude/skills/viva
   ```
4. Invoke bare `/viva` again and confirm it now resolves to the plugin
   version.

Record the outcome before continuing.

- [ ] **Step 3: MANUAL — direct-repo install channel check**

```
/plugin marketplace add jacquardlabs/viva
/plugin install viva@viva
```
Record whether this succeeds or fails.

- [ ] **Step 4: If Step 3 failed, remove or caveat the direct-repo channel in README**

Only if Step 3 failed. Modify `README.md`'s install section (the "Or install this plugin directly" block added back in Task 2 Step 1) — either delete that block entirely, or add a one-line caveat noting it is currently broken and linking the tracking issue. Commit this change together with Step 6 below, or on its own with:
```bash
git add README.md
git commit -m "docs: caveat unverified direct-repo install channel (#101)"
```
If Step 3 passed, skip this step — no change needed, the channel stays documented as the secondary alternative as-is.

- [ ] **Step 5: Remove PRODUCT.md's "/viva entry point unverified" bullet**

Only if Steps 1 and 2 both passed. Modify `PRODUCT.md`'s `## Known problems` section. Replace:
```markdown
- **`/viva` entry point unverified.** Documented as a slash command; the plugin
  ships a skill with no commands manifest — confirm the invocation resolves.
```
With: (delete the bullet entirely — remove those two lines, no replacement text)

If Steps 1 or 2 failed, do not make this edit — leave the bullet in place and instead file a follow-up issue describing what broke.

- [ ] **Step 6: Commit**

```bash
git add PRODUCT.md
git commit -m "chore: verified /viva entry point resolves after skill registration fix (#101)

Two-pass manual verification (clean-machine install, shadowing-case
with a stale personal clone present) both passed. Removes the Known
problems caveat this design's own Operational readiness section
gated the edit on."
```

---

## Post-implementation

`/gate-audit` verifies the pre-mortem register's technical-lane items (symlink elimination, cross-reference completeness, `$VIVA_DIR` determinism). `/gate-acceptance` verifies the product-lane items (migration-note prominence, PRODUCT.md edit gated on real verification, direct-repo channel outcome, the structural test's ability to actually catch a regression). Both read `docs/studious/premortems/2026-07-12-skill-registration-design.md`.
