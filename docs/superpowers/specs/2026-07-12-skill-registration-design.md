# Design: One Directory Per Skill — Registering `/viva-qa` and `/viva-diff`

**Date:** 2026-07-12
**Source:** issue #101
**Branch:** worktree-101

## Problem & persona

**The agent author (primary, non-human)** — PRODUCT.md's first persona: Claude
Code, having written a spec, needing to hand work to a human gate. PRODUCT.md
defines the product as "the set of **human checkpoints across an agent's
artifact lifecycle**": the review checkpoint (`/viva`), the brainstorm
checkpoint (`/viva-qa`), and the diff checkpoint (`/viva-diff`).

Two of those three checkpoints are unreachable in every consumer install.
Claude Code's skill discovery registers **one skill per directory** via that
directory's `SKILL.md` — a plugin's `skills` path is scanned for
`<name>/SKILL.md` subdirectories, and loose `.md` files with skill frontmatter
never register (doc-confirmed:
code.claude.com/docs/en/plugins-reference). `brainstorming-qa.md`
(`name: viva-qa`) and `diff.md` (`name: viva-diff`) are loose sibling files
inside `.claude/skills/viva/`, so only `viva` registers.

Consequences observed, not hypothesized:

- A session in this repo lists `viva` in its skill registry but neither
  `viva-qa` nor `viva-diff`.
- `SKILL.md:14-16` asserts "the superpowers `brainstorming` skill calls
  `/viva-qa` directly when viva is installed" — a call path that does not
  resolve anywhere.
- Its pointer to `.claude/skills/viva/brainstorming-qa.md` is a repo-relative
  path meaningless at runtime in a consumer project.
- PRODUCT.md's Known problems already flags the `/viva` entry point as
  unverified; this confirms the problem and shows it is wider.

The reviewing human (the second primary persona) loses the same two
checkpoints — they were promised a brainstorm gate and a diff gate that no
install can invoke.

## Proposed design

What changes for the user: **installing the viva plugin registers three
skills instead of one.** `/viva` behaves exactly as today; `/viva-qa` and
`/viva-diff` become invocable everywhere the plugin is installed. Nothing
about the browser experience, the `.viva/` state files, or any server
behavior changes.

Three coordinated pieces:

**1. One directory per skill under `.claude/skills/`.**

| Today | After |
|-------|-------|
| `.claude/skills/viva/SKILL.md` | unchanged |
| `.claude/skills/viva/brainstorming-qa.md` | `.claude/skills/viva-qa/SKILL.md` |
| `.claude/skills/viva/diff.md` | `.claude/skills/viva-diff/SKILL.md` |
| `SKILL.md` (repo root) | deleted |
| `.claude-plugin/plugin.json` | untouched — `"skills": "./.claude/skills"` now discovers three subdirectories |

Skill `name:` frontmatter already matches the new directory names. Runtime
files (`server.py`, `scripts/`) do not move; registration never depended on
their location.

**2. The manual install channel is dropped.** The root `SKILL.md` existed
only so `git clone → ~/.claude/skills/viva` would register the core skill;
personal-skill discovery is one level deep (doc-confirmed:
code.claude.com/docs/en/skills), so that channel can never carry the
sub-skills. README's manual-clone instructions are removed and replaced with
a migration note: previously installed via git clone? Delete
`~/.claude/skills/viva` and install the plugin. Deleting the root copy also
ends the hand-synced duplication between root `SKILL.md` and
`.claude/skills/viva/SKILL.md` — a drift risk nothing was checking.

**3. `$VIVA_DIR` resolves from the plugin cache only, failing loud.** All
three skills currently check `~/.claude/skills/viva` first — after this
change, an unsupported location that can silently shadow a fresh plugin
install with a stale copy (observed live: a pre-plugin-era copy on the
author's machine shadows any plugin install today). The resolve block in all
three skills becomes:

```bash
VIVA_DIR=$(find ~/.claude/plugins/cache -name "server.py" -path "*/viva*" -maxdepth 6 2>/dev/null \
           | xargs -I{} dirname {} | head -1)
[ -f "$VIVA_DIR/server.py" ] || { echo "viva: server.py not found — install the viva plugin (/plugin install viva@jacquardlabs-marketplace)"; exit 1; }
```

(each skill keeps its own error prefix: `viva:` / `viva-qa:` / `viva-diff:`).
A missing install is a loud, actionable error instead of silent version skew.
`$CLAUDE_PLUGIN_ROOT` is deliberately not used: docs confirm it for hooks,
MCP, and LSP configs but are silent on skill bash blocks.

Cross-references updated in the same change: viva `SKILL.md`'s Brainstorming
Q&A section references the sibling skill by name (`/viva-qa`) with the
repo-relative file pointer deleted; CLAUDE.md's skill-path references get the
new paths; `docs/headless-contract.md` and two test docstrings that name
`brainstorming-qa.md`/`diff.md` as live files get the new names. Dated specs,
plans, and premortems are historical records and stay untouched. PRODUCT.md's
"`/viva` entry point unverified" bullet is deleted **only after** the
verification below passes.

Principle alignment (PRODUCT.md): this is packaging repair in service of
"the product is the set of human checkpoints" — it makes two documented
checkpoints reachable. "Cheap for the agent" (one-block launch) and "No-op
when absent" are untouched; no principle bends.

## User journey

Both journeys below are documented today and broken today; the design makes
them real without altering a single step of the working `/viva` journey.

**Brainstorm checkpoint.** The agent author finishes a brainstorming
session's question phase, writes `.viva/qa-input.json`, and invokes
`/viva-qa`. Today that invocation resolves nothing in a consumer install —
the documented integration ("the brainstorming skill calls `/viva-qa`
directly when viva is installed") dead-ends. After: the skill resolves,
`$VIVA_DIR` finds the plugin's `server.py`, the human answers the batch
questions in the browser, and `.viva/answers.json` comes back. No step of
the Q&A contract changes.

**Diff checkpoint.** The reviewing human (or the agent, before committing)
invokes `/viva-diff [ref]`. Today: unresolvable outside this repo. After:
hunk-by-hunk review exactly as `diff.md` already specifies.

**Review checkpoint (unchanged).** `/viva path/to/doc.md` — identical
before and after; the only difference is `$VIVA_DIR` resolving via the
plugin cache instead of preferring a possibly stale `~/.claude/skills/viva`.

**One journey changes by subtraction:** a user on the manual git-clone
install no longer has a supported path. Their existing copy keeps running
its old prose (skill edits only reach plugin users), and the README
migration note tells them how to move.

## Out of scope

- **Manual-channel parity via symlinks.** Deliberately not built — the
  channel is dropped instead (decision recorded in this design).
- **Any change to `server.py`, `scripts/`, the `.viva/` schema, or state
  lifecycle.** This is file layout and skill prose only.
- **Adopting `$CLAUDE_PLUGIN_ROOT`.** Undocumented for skill bash blocks;
  revisit if docs firm it up.
- **README feature-coverage lag** (PRODUCT.md Known problem #1) — separate
  story; only the install section and the Q&A integration wording change
  here.
- **`diff.md`'s legacy `?output=` query-param usage** and other adjacent
  cleanups named in `docs/headless-contract.md` — explicitly future work
  there, unchanged here.
- Cross-check against "What we are NOT building": no new surface, no
  dependency, no hosting; nothing on that list is approached.

## Alternatives considered

1. **Plugin-default `skills/` directory at repo root** (drop the manifest
   key). Rejected: loses project-level registration when working in the viva
   repo itself — the dogfooding path this repo's own history leans on — and
   churns every path reference for no functional gain.
2. **`skills` array in `plugin.json`.** Doc-confirmed to accept an array of
   paths, but discovery is still one-skill-per-subdirectory, so the array
   cannot rescue loose files; it ends up doing the same moves with extra
   manifest complexity. Rejected.
3. **Keep the manual channel, degraded** (clone gets `/viva` only, README
   documents the limitation). The smallest change — rejected by decision:
   it preserves the hand-synced root duplicate and a channel whose primary
   observed artifact is a stale copy shadowing real installs.
4. **Full manual-channel parity via clone-anywhere + three symlinks.**
   Rejected: fiddly setup, requires resolution-logic changes, and props up a
   channel the one-command plugin install obsoletes.
5. **Keep `~/.claude/skills/viva` as a resolution fallback** (cache first,
   legacy second). Rejected: when the cache lookup misses, a years-stale
   `server.py` silently serves — subtle version skew is a worse failure than
   a loud "install the plugin."
6. **Do nothing; document plugin-only invocation in README.** Rejected:
   leaves `SKILL.md`'s asserted call path broken and two of three
   checkpoints unreachable — the bug, restated as documentation.

## Testing

New stdlib self-running test, `tests/test_skill_registration.py` (matches the
existing `main()` + `OK` convention), asserting the structural invariant
discovery depends on:

- every directory under `.claude/skills/` contains a `SKILL.md` whose
  frontmatter `name` equals the directory name;
- no skill directory contains a sibling `.md` with skill frontmatter — the
  loose-file regression this issue exists to prevent;
- no root `SKILL.md` — guards the duplicate's return.

## Operational readiness

Local, keyless tool — no logs, metrics, or alarms to stand up. The working /
failing signal is direct: after a fresh install, the skill registry lists
`viva`, `viva-qa`, and `viva-diff` (working), or an invocation fails loud at
the `$VIVA_DIR` guard with the install hint (failing).

- **Verification (gates the PRODUCT.md edit):** on a machine with the stale
  `~/.claude/skills/viva` removed, install the plugin from this branch's
  checkout, confirm all three skills register, and run `/viva-qa` far enough
  to pass the `$VIVA_DIR` resolve.
- **Rollout:** ships in the next plugin release; existing plugin users get
  all three skills on update with no action. No state migration — nothing
  under `.viva/` is touched.
- **Rollback:** revert one commit. No data implications.
- **Migration:** README note for manual-clone users (delete the clone,
  install the plugin). The author's machine does this as part of
  verification.

## Open questions

- The README also documents `/plugin marketplace add jacquardlabs/viva`
  (direct-repo marketplace). Whether that channel works without a
  `marketplace.json` in this repo is unverified — check during the
  fresh-install verification; if broken, it is a separate issue, not this
  one.
