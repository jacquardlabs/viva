# Learned preferences — across sessions

Read this at sign-off, when `loop.py finish` names this file: this session's
notes are the training signal, and recording them is judgment work.

Reviewers repeat the same critiques across docs — "unsourced numbers", "passive
voice", "no rollback step". viva learns these so a recurring issue is pre-applied
or pre-flagged before the human re-types it.

The store lives at `.viva/preferences.json`. `scripts/preferences.py` is its
primary writer; the running server's `POST /preferences/mute` route is a second,
narrower one — it only ever flips an existing preference to `muted`, from an
in-page panel the reviewer opens without leaving the tab. It never creates a
preference or promotes one. Unlike the round files, the store is **not** cleared
by `loop.py start`, so it persists across sessions; it is gitignored, so learned
preferences are a reviewer's own, per clone. It is plain JSON — the human can
edit it directly, and muting retires a bad one (`preferences.py set --status
muted`; un-muting is CLI-only, `set --status standing`).

Preferences are **suggestions you apply, not rules**: a pre-applied fix still
rides a normal rewrite to the human, and a pre-flag is an advisory annotation
that never gates a verdict. Nothing is auto-accepted.

| Status | Meaning | Consulted? |
|--------|---------|------------|
| `candidate` | observed in one session | no — recorded, waiting to recur |
| `standing` | recurred across ≥2 distinct sessions | yes — at rewrite and pre-flag time |
| `muted` | retired by the human | never; also never auto-promoted |

The semantic work — recognizing that "where's the citation for 80%?" and
"unsourced stat" are one critique, and that a new cluster matches an existing
preference — is yours. `preferences.py` only does the bookkeeping: stable ids,
the distinct-session count, and the candidate→standing promotion.

## Three touch points

**Consult — at every rewrite, default-on.** The doc is already open, so applying
the standing set costs nothing. `loop.py wait` already printed it under
`=== standing preferences ===`; don't re-read it.

**Pre-flag — round 1, automatic.** A fresh incoming doc has no verdict yet, so
the only way to surface a learned critique without the human typing it is the
preference producer (`producers.md`). `loop.py start` stops after parsing
whenever the store holds a standing preference, which is the round-1 doc read
paid deliberately — and only once the reviewer has earned it.

**Record — at sign-off.** Cluster this session's `changes`/`info` notes into
distinct critiques, list the existing preferences to see which ones they match,
then record each:

```bash
python3 "$VIVA_DIR/scripts/preferences.py" list \
  --store .viva/preferences.json --status all
python3 "$VIVA_DIR/scripts/preferences.py" record \
  --store .viva/preferences.json --session "<date> <doc filename>" \
  --id cite-sources --label "Cite a source for every quantitative claim" \
  --guidance "When a section states a number, attach a citation or mark it unsourced." \
  --count <sections this critique hit this session>
```

Reuse an existing `--id` to reinforce a preference across sessions — the second
session promotes it to standing — or create a new candidate with a short, stable
id, so a later session matches it instead of forking a near-duplicate. A session
with no recurring critique records nothing. Only a signed-off session learns; an
abandoned review records nothing.

A project with no `preferences.json` behaves exactly as before: `wait` prints an
empty set, no producer auto-engages, and nothing is recorded until the first
sign-off.
