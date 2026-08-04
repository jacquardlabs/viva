# Pre-mortem — Keyboard shortcut to approve the active card without a mouse

- Branch: epic/reviewer-experience--approve-shortcut
- SHA: 78d8bc5
- Date: 2026-08-03

| # | Lane | Failure mode | Detection hint |
|---|------|--------------|-----------------|
| 1 | technical | The fix lands in review mode only; diff mode shares the exact same `REVIEW_DATA`/keydown code path but its manual check gets skipped, so the same latent auto-accept bug survives on `/viva-diff`'s hunk cards. | Build evidence should show two separate manual-check confirmations (review mode and diff mode), not one. A PR citing only the review-mode check is incomplete evidence. |
| 2 | product | The in-round undo asymmetry (a carried approval gets a `withdraw approval` button; an in-round `a` approval gets no labeled equivalent) surfaces as a real complaint from a reviewer who mis-keys `a` on a comment-less card they meant to flag. | Watch for a follow-up issue about "how do I undo an approve" filed against this feature specifically, especially soon after ship. |
| 3 | technical | The `a`-key handler swap to `approveSection` ships without its paired `.kbd-legend` copy change, or the copy change lands inconsistently with what the handler actually does — leaving the UI's own documentation wrong about its own behavior. | The implementation diff should touch both the keydown handler's `a` case and the `.kbd-legend` `<dd>` text in the same commit; a diff missing either is incomplete. |
| 4 | product | The `c`/`i` legend rows' "deliberate, temporary" misleading-copy asymmetry (tracked at #156) becomes permanent because #156 never gets prioritized — a common failure mode for "fix it later" scope decisions. | Check whether #156 is still open and unassigned well after this ships; if so, the doc's "temporary" framing was optimistic. |
| 5 | technical | The added string-needle test (asserting the `a`-key branch calls `approveSection(`, not `setReviewVerdict(`) passes, but the manual browser check it explicitly can't replace (guard + timing behavior) never actually gets run, because a green test suite is easy to mistake for full coverage. | Build/PR evidence should show explicit confirmation of the manual browser check in both modes, not just a passing `tests/test_server_a11y.py`. |
| 6 | product | A reviewer used to today's (buggy) `a`-key toggle-off — press `a` twice to un-approve — is surprised post-fix when the second press does nothing, since `advanceFrom` has already moved them to a different card by the time they'd press it again. | Watch early usage/feedback for confusion specifically shaped like "I pressed a twice and it didn't undo." |
