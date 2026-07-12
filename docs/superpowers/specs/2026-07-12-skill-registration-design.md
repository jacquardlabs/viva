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
| `.claude/skills/viva/SKILL.md` (relative symlink → `../../../SKILL.md`) | replaced by the real file: `git rm` the symlink, `git mv` root `SKILL.md` here, then apply the resolve-block and Q&A cross-reference edits |
| `.claude/skills/viva/brainstorming-qa.md` | `.claude/skills/viva-qa/SKILL.md` |
| `.claude/skills/viva/diff.md` | `.claude/skills/viva-diff/SKILL.md` |
| `SKILL.md` (repo root — the one real copy) | moved to `.claude/skills/viva/SKILL.md`; nothing remains at root |
| `.claude-plugin/plugin.json` | untouched — `"skills": "./.claude/skills"` now discovers three subdirectories |

**Current-state note (corrected in review round 3):** the repo holds exactly
one real `SKILL.md`, at the root; `.claude/skills/viva/SKILL.md` is a
git-tracked relative symlink to it (added in de654f8 so the plugin and
manual channels could share one file). The move above is therefore
symlink-elimination — ordered as replace-then-move so there is no separate
"delete root `SKILL.md`" step whose literal execution would leave the link
dangling and silently deregister `/viva`.

Skill `name:` frontmatter already matches the new directory names. Runtime
files (`server.py`, `scripts/`) do not move; registration never depended on
their location.

**2. The manual install channel is dropped.** The root `SKILL.md` existed
only so `git clone → ~/.claude/skills/viva` would register the core skill;
personal-skill discovery is one level deep (doc-confirmed:
code.claude.com/docs/en/skills), so that channel can never carry the
sub-skills. README's manual-clone instructions are removed and replaced with
a migration note: previously installed via git clone? Delete
`~/.claude/skills/viva` and install the plugin. Moving the root file into
`.claude/skills/viva/` (piece 1) also removes the symlink indirection that
existed to serve both channels from one file — fragility, not convenience:
git preserves the relative link, but zip downloads and Windows checkouts
don't reliably, and any deletion of the root target would leave the link
dangling, silently deregistering `/viva`.

**3. `$VIVA_DIR` resolves from the plugin cache only, failing loud.** All
three skills currently check `~/.claude/skills/viva` first — after this
change, an unsupported location that can silently shadow a fresh plugin
install with a stale copy (observed live: a pre-plugin-era copy on the
author's machine shadows any plugin install today). The resolve block in all
three skills becomes:

```bash
VIVA_DIR=$(find ~/.claude/plugins/cache -maxdepth 6 -path "*/viva/*" -name server.py -print0 2>/dev/null \
           | xargs -0 ls -t 2>/dev/null | head -1)
VIVA_DIR=${VIVA_DIR%/server.py}
[ -f "$VIVA_DIR/server.py" ] || { echo "viva: server.py not found — install the viva plugin (/plugin install viva@jacquardlabs-marketplace)"; exit 1; }
```

(each skill keeps its own error prefix: `viva:` / `viva-qa:` / `viva-diff:`).
A missing install is a loud, actionable error instead of silent version skew.
Two deliberate hardenings over the old `find` fallback, which becomes the
only path here: `-path "*/viva/*"` requires a path segment named exactly
`viva` (the old `*/viva*` glob could match any plugin or marketplace whose
name merely starts with "viva"), and candidates are ordered newest-mtime
first (`ls -t`) so that when the cache retains several plugin versions side
by side — observed live on the author's machine, where two versions of
another plugin coexist — the most recently installed one wins
deterministically instead of whichever `find` happens to emit first. An
empty find leaves `$VIVA_DIR` empty and the guard fails loud.
`$CLAUDE_PLUGIN_ROOT` is deliberately not used: docs confirm it for hooks,
MCP, and LSP configs but are silent on skill bash blocks.

The error hint and the README agree on one install identity: the hint names
the primary (marketplace) form, and README's install section leads with that
same form. The direct-repo channel, if it survives verification (see
Operational readiness), remains documented as a clearly secondary
alternative — the error-surfaced and documented install paths never
diverge.

One developer-workflow consequence, stated so it isn't discovered by
surprise: with resolution cache-only, invoking `/viva` (or either sub-skill)
inside the viva repo itself runs the *cached plugin's* runtime, not the
working tree. Exercising local edits to `server.py`/`scripts/` requires
reinstalling the plugin from the checkout — exactly the verification loop in
Operational readiness. (Project-level *registration* of the three skills in
this repo is unaffected; this is about which `server.py` runs.)

Cross-references updated in the same change:

- viva `SKILL.md`'s Brainstorming Q&A section references the sibling skill
  by name (`/viva-qa`) with the repo-relative file pointer deleted;
- CLAUDE.md's skill-path references get the new paths;
- README's Q&A contract pointer ("See
  `.claude/skills/viva/brainstorming-qa.md` for the full contract") is
  repointed to `/viva-qa` by name;
- README's "Server CLI (advanced)" and `--split-on` examples, which hardcode
  `python3 ~/.claude/skills/viva/server.py` and
  `~/.claude/skills/viva/scripts/parse_sections.py` four times, are rewritten
  atop the `$VIVA_DIR` resolve snippet above — the documented headless
  invocation survives the channel drop instead of pointing at a path that no
  longer exists;
- `docs/headless-contract.md`'s live-file mentions of
  `brainstorming-qa.md`/`diff.md` get the new names, as do the three test
  docstrings that name them: `tests/test_server_diff.py`,
  `tests/test_server_qa_complete_shutdown.py`,
  `tests/test_server_qa_review_handoff.py`;
- `server.py`'s line-27 comment naming `~/.claude/skills/viva/` as the
  install location is updated to the plugin cache (one comment line — the
  code itself stays out of scope).

Dated specs, plans, and premortems are historical records and stay
untouched. PRODUCT.md's "`/viva` entry point unverified" bullet is deleted
**only after** the verification below passes.

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
  story. This change touches the README's install section, Q&A integration
  wording and contract pointer, and the Server CLI/`--split-on` example
  paths (all enumerated above); the undocumented-feature-cluster gaps stay
  put.
- **`diff.md`'s legacy `?output=` query-param usage** and other adjacent
  cleanups named in `docs/headless-contract.md` — explicitly future work
  there, unchanged here.
- Cross-check against "What we are NOT building": no new surface, no
  dependency, no hosting; nothing on that list is approached.

## Alternatives considered

1. **Plugin-default `skills/` directory at repo root** (drop the manifest
   key). Rejected: loses project-level registration when working in the viva
   repo itself — the dogfooding path this repo's own history leans on — and
   churns every path reference for no functional gain. (Registration only:
   runtime resolution is cache-only either way; see the developer-workflow
   note in Proposed design.)
2. **`skills` array in `plugin.json`.** Doc-confirmed to accept an array of
   paths, but discovery is still one-skill-per-subdirectory, so the array
   cannot rescue loose files; it ends up doing the same moves with extra
   manifest complexity. Rejected.
3. **Keep the manual channel, degraded** (clone gets `/viva` only, README
   documents the limitation). The smallest change — rejected by decision:
   it preserves the root-file-plus-symlink indirection and a channel whose
   primary observed artifact is a stale copy shadowing real installs.
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

- the expected skill set is exactly `viva`, `viva-qa`, `viva-diff`: each
  present as a directory under `.claude/skills/` containing a `SKILL.md`
  whose frontmatter `name` equals the directory name — a vanished core
  skill fails the test, not only a malformed extra one;
- every `SKILL.md` is a regular file, not a symlink — guards the
  dangling-link fragility's return;
- no skill directory contains a sibling `.md` with skill frontmatter — the
  loose-file regression this issue exists to prevent;
- no root `SKILL.md` — the move left nothing behind.

## Operational readiness

Local, keyless tool — no logs, metrics, or alarms to stand up. The working /
failing signal is direct: after a fresh install, the skill registry lists
`viva`, `viva-qa`, and `viva-diff` (working), or an invocation fails loud at
the `$VIVA_DIR` guard with the install hint (failing).

- **Verification (gates the PRODUCT.md edit):** on a machine with the stale
  `~/.claude/skills/viva` removed, install the plugin from this branch's
  checkout, confirm all three skills register, and run `/viva-qa` far enough
  to pass the `$VIVA_DIR` resolve. The same pass also exercises the README's
  direct-repo channel (`/plugin marketplace add jacquardlabs/viva` +
  `/plugin install viva@viva`): if it works it stays documented as the
  secondary alternative; if it fails, **this change** removes or caveats
  those README lines (a one-line fix) — a freshly rewritten install section
  does not ship advertising an unverified channel.
- **Rollout:** ships in the next plugin release; existing plugin users get
  all three skills on update with no action. No state migration — nothing
  under `.viva/` is touched.
- **Rollback:** revert one commit. No data implications.
- **Migration:** README note for manual-clone users (delete the clone,
  install the plugin). The author's machine does this as part of
  verification.

## Open questions

None. The one question earlier drafts deferred — whether the README's
direct-repo marketplace channel (`/plugin marketplace add jacquardlabs/viva`)
actually works — is resolved *within* this change: the fresh-install
verification exercises it, and a failure removes or caveats those README
lines here rather than deferring to a separate issue (see Operational
readiness).
