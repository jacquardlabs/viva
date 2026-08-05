# Open notes — threads carried across rounds

Read this when `loop.py wait` classifies a round `has-work`: the rewrite acts on
threads, not on one-shot notes.

A `changes`/`info` note would otherwise last one round — the reviewer flags it,
you rewrite, the note is gone, and an unsatisfying rewrite makes them re-flag
from scratch. An **open note** persists round to round, accumulating the
exchange, until the reviewer **settles** it.

Every comment is an open thread **by default**. The store lives at
`.viva/open-notes.json`, keyed by each comment's stable `cid`, and
`scripts/open_notes.py` is its single writer — `loop.py rearm` and `loop.py
finish` call it for you. Each thread also carries the comment's original quoted
span, so the conversation never loses its referent even if the span is later
rewritten away.

1. Every submitted comment automatically becomes an open thread, keyed by `cid`.
2. During the rewrite you pass one `--response "<cid>=<what you changed>"` per
   comment to `loop.py rearm`. It appends the exchange to that thread. The `cid`
   is the server's own `{sectionId}-c{n}` (e.g. `s2-c1`) — use it verbatim, never
   synthesize it. Approving a section settles all of its threads.
3. The re-parse re-presents still-open threads on their cards with the full
   prior exchange **and a reply box**, so the reviewer continues the
   conversation across rounds, GitHub-style.
4. The reviewer **settles** a thread when satisfied. A settled thread drops from
   later rounds.

## A thread is a sequence of typed turns — act on the latest one

Each reviewer turn (the original comment, then each reply) carries its own
`type`. On every round, for each open thread look at the **most recent** turn:

- latest turn `info` → **respond in the thread only; do not edit the section.**
  The discussion is still open.
- latest turn `changes` → **apply the edit now** (and say what you changed in
  the thread response), so the reviewer reviews the diff.

This is how an `info` discussion becomes a change: the reviewer **escalates** by
switching their reply to *request changes*. That turn arrives as a comment on
the same `cid` with `type: "changes"`, so the rule above applies it. A fresh
`info` comment discusses until some turn is typed `changes`; a fresh `changes`
comment applies immediately. A reply that merely continues the discussion keeps
the thread's `info` type and only appends to the conversation.

## Threads compose with verdicts; they don't replace them

An open thread never blocks sign-off. A section signs off when its verdict is
`approved`; the thread is the conversation alongside that decision. At
completion `loop.py finish` folds every thread's full exchange into the ledger —
each round's note → your response, keyed by `cid`, with the original quoted span
and the thread's final status. A doc where no section carries comments has no
threads and behaves exactly as before: no store, no `open_notes` on any card, no
Open notes ledger section.
