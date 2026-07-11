# Sprint 12 plan

Short intro paragraph, so this fixture also exercises preamble handling
under `--split-on`.

### Task 1: Add the CLI flag

Add `--split-on` to `parse_sections.py`.

#### Acceptance criteria
- Flag is optional and defaults to unset.
- Default parsing (no opt-in) stays byte-for-byte unchanged.

## Notes

Coordinate with the schema owner before merging.

### Task 2: Write the fixture

Add a `PLAN.md` fixture with `### Task N` blocks.

#### Acceptance criteria
- Fixture has at least two tasks.
- Fixture reproduces the coarser-heading-wins bug the design doc describes:
  `## Notes` recurs under every task, one level coarser than `### Task N`,
  so auto-detection (no `--split-on`) picks `##` and swallows every task
  into whichever `Notes` block it falls under.

## Notes

No blockers.

### Task 3: Wire up tests

Add unit coverage in `test_parse_sections.py`.

#### Acceptance criteria
- Round-trip through a second round proves approval/annotation/diff
  carry-forward works unmodified, keyed by `schema.section_key()`.
