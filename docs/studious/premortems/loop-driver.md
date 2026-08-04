# Pre-mortem — loop.py driver owns round numbering, liveness, and the all-approved finish guard

- Branch: epic/reliability--loop-driver
- SHA: c62b1b1
- Date: 2026-08-04

Amended after design-episode round 2. Round-1 items stay visible with their
disposition so a reader can see what the design was always worried about.
Story-scoped; the cross-story register for the epic is `reliability-epic.md`.

| # | Lane | Status | Failure mode | Detection hint |
|---|------|--------|--------------|----------------|
| 1 | technical | **closed r2** | `start`/`rearm` atomic, closing the producer seam and silently dropping the auto-engaging learned-preference producer. | Closed by `start`'s stop-after-parse plus `annotate`/`arm`. Still check: seed a standing preference, confirm the flag lands in `review-input-r1.json`. |
| 2 | technical | **closed r2** | `/complete` guard keys on shape alone, so it fires on diff sessions and 4xxes `viva-diff`'s documented empty-re-diff finish. | Closed by the `mode != "diff"` condition. Still check: revert a hunk to empty the re-diff, confirm `/complete` returns 200. |
| 3 | product | **closed r2** | `submitted_early` rounds have a refusal but no routing rule; the agent finds the dead zone by tripping over `finish`. | Closed by `wait`'s classification line. Still check: the slim SKILL.md's verdict table has a third row. |
| 4 | product | **closed r2** | After *skip rest & submit*, the reviewer's tab strands on the processing card while the agent asks a question elsewhere. | Closed by re-arm-before-ask. Still check: skip mid-review and watch the browser return to cards. |
| 5 | technical | **closed r2** | `references/` resolution owned by the out-of-scope `viva-dir-resolve`, with no dependency edge. | Dissolved rather than sequenced: `references/` resolves via `Path(__file__).resolve().parent`. Check that no `$VIVA_DIR` interpolation reaches a `references/` path. |
| 6 | product | **closed r2** | Auto-approve hatch deleted as a side effect, with no criterion asserting its absence. | Closed by the added criterion. Detection: `grep -n 'auto-approved\|too short to review' .claude/skills/viva/SKILL.md` must return nothing. |
| 7 | technical | **closed r2** | `/complete`'s verdict source unspecified, so "before any submit" and "not complete" collapse to one recovery. | Closed by the `_last_verdicts` snapshot and two distinct 4xx bodies. Detection: POST `/complete` to a fresh session and read the message. |
| 8 | technical | **open (new r2)** | `abandon` depends on this story's own `SIGTERM` handler for its teardown. If the handler regresses or the signal is delivered to the wrong process, `abandon` silently becomes the leak it was written to prevent — and it is the one exit with no success artifact to notice its absence by. | `loop.py abandon` on a live session, then `ls .viva/server.url` — the file must be gone and the process reaped. Test it separately from the SIGINT path; a shared assertion would pass on SIGINT alone. |
| 9 | product | **open (new r2)** | The default-on preference-consult directive stays inline while its explanatory material moves to `references/`, leaving an orphaned rule with its rationale one file away. A later tidying pass reads it as a stray and completes the relocation, silently restoring the regression this round closed. | `grep -n 'default-on\|standing' .claude/skills/viva/SKILL.md` after any future docs pass. Guard: the criterion naming it, plus a comment in the file saying why the paragraph stays. |
| 10 | technical | **open (new r2)** | `round_is_complete()` lands in `schema.py`, which is today a pure contract module — TypedDicts, `section_key()`, `verdict_to_ledger_entry()`, validators. Adding a function that *evaluates round state* widens what the shared module is for, and the next such function has an easier argument. | Read `schema.py`'s surface after the change: anything reading `.viva/` or making a policy decision beyond validation is drift. The predicate should take already-loaded dicts and touch no disk. |
