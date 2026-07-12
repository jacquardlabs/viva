# Pre-mortem — One directory per skill (viva-qa / viva-diff registration)

- Design doc: docs/superpowers/specs/2026-07-12-skill-registration-design.md
- Branch: worktree-101
- SHA: d69cd4d
- Date: 2026-07-12

| # | Lane | Failure mode | Detection hint |
|---|------|--------------|----------------|
| 1 | technical | The symlink at `.claude/skills/viva/SKILL.md` isn't fully eliminated — the build edits it in place (or edits root `SKILL.md` without moving it) instead of `git rm` + `git mv`, leaving a symlink (possibly dangling) under `.claude/skills/`. | `git ls-files -s .claude/skills/ \| grep 120000` (symlink mode) should return nothing; `file .claude/skills/viva/SKILL.md` should report a regular file. |
| 2 | technical | A cross-reference to the old paths (`brainstorming-qa.md`, `diff.md`, `~/.claude/skills/viva/server.py`, root `SKILL.md`) survives the move in README, CLAUDE.md, `docs/headless-contract.md`, `server.py:27`, or the three test docstrings the design names. | `grep -rn 'brainstorming-qa\.md\|skills/viva/diff\.md\|~/.claude/skills/viva/server\.py' .` outside dated `docs/superpowers/specs`/`plans`/`premortems` should return zero hits. |
| 3 | technical | `$VIVA_DIR`'s `find \| ls -t \| head -1` resolve doesn't actually disambiguate multiple cached plugin versions — e.g. an install/extract mechanism stamps every file with the same mtime, defeating "newest wins." | With two version directories present under the plugin cache, confirm the resolve deterministically picks the expected one; re-run and confirm it's stable across repeats. |
| 4 | product | The migration note (delete `~/.claude/skills/viva` before installing) isn't prominent enough — a manual-clone user skims past it, installs the plugin, and hits the doc-confirmed shadowing (bare `/viva` silently serves stale personal-skill prose) with no way to self-diagnose. | README's install section places the delete-old-clone step ahead of or alongside the new install steps, not buried after them; check it reads as a required step, not a footnote. |
| 5 | product | PRODUCT.md's "`/viva` entry point unverified" bullet is deleted without the design's two-pass verification (clean-machine + shadowing-case) actually having been run. | The PR that removes the bullet states or links the verification evidence for both passes, including the direct-repo marketplace channel outcome. |
| 6 | technical | The direct-repo marketplace channel (`/plugin marketplace add jacquardlabs/viva`) verification is skipped or waved through, and the README ships still advertising a channel nobody confirmed works. | PR description records an explicit pass/fail for this channel, with the README lines removed/caveated if it failed. |
| 7 | product | The new structural test (`tests/test_skill_registration.py`) is written to check whatever skill directories happen to exist, rather than asserting the fixed expected set — so it would pass even if `viva` silently dropped out of registration. | Deliberately reintroduce the symlink (or delete a skill directory) locally and confirm the test fails; it must not merely validate whatever's present. |
