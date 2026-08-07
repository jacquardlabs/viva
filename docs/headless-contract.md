# viva headless invocation contract

**Contract version: 5**

This document is for a program that launches `server.py` as a subprocess and
reads/writes its JSON files — a headless caller — not for the human
running `/viva` inside Claude Code (see `README.md`) and not for Claude Code
orchestrating the review loop itself (see `SKILL.md`). It transcribes what
`server.py` and `scripts/schema.py` actually do; it does not restate either
of those documents in different words. Where this doc and either of those
disagree, this doc's job is to match the code, not the prose elsewhere —
file an issue if you find a mismatch.

## 1. Contract version

A single integer, bumped whenever the shipped surface below changes in a way
that could break an existing caller:

- removing or renaming a `--mode`/`--input`/`--output`/`--no-browser` flag
- changing a round-file's required field (adding or removing one, or
  changing its type)
- changing what an existing **value** of an existing field means, even when
  the field's name and type are untouched — a caller that reads the old
  meaning now reads it wrong (v5's `anchor.offset`, below)
- changing what an existing exit code means
- removing an HTTP endpoint, or changing its request/response shape
- changing when an existing endpoint succeeds, even by a condition only a new
  optional field can switch on (v3's `/complete` guard, v4's `pass`)

**Not** a version bump: adding an optional field **on its own**, adding a new
endpoint, adding a new `--mode` value, wording/prose clarifications to this
file. "On its own" is the whole distinction and it is what the last three rows
turn on — an optional field a caller may ignore costs nothing, but the moment
it can switch an endpoint's success condition (v4's `pass`) or ships alongside
a change to an existing field's meaning (v5's `anchor.offset`), the bump is for
that consequence, never for the field.

This is independent of `plugin.json`'s semantic-release semver, which bumps
on every merged feature or fix (including ones that never touch this
surface — a browser-side CSS change, an unrelated bug fix). This integer
answers one narrower question: "did the surface I integrated against move."
It follows the precedent `scripts/preferences.py` already sets for its own
on-disk store (`VERSION = 1`).

Changelog:

| version | date | change |
|---|---|---|
| 5 | 2026-08-07 | Reviewer mechanisms: a `SectionVerdict.comments[]` entry may now be typed `suggestion` (§3) — a new wire value a caller must interpret to apply the round, carrying the reviewer's exact `replacement` for the span its `anchor` names. It is applied **verbatim**, never rewritten. A suggestion derives to the section verdict `changes` (the section is not approved while one is live) and folds into the ledger with its wording tagged `suggested:`; a carried open-note exchange records the same `replacement`. `POST /submit` now `400`s a `suggestion` comment with no non-empty `replacement` — reachable only by a caller sending the new type, so no existing payload changes status. This version is the mechanisms phase of the editorial frame, and it also carries the `declined` open-note thread status: a thread in `ReviewSection.open_notes` (§3) may now report `status: "declined"` — the author's answer, not a verdict, with `VERDICTS` unchanged — and the exchange it declined carries `grounds`. A caller reading threads must handle it exactly as it handles `open`: a declined thread is unresolved, so it attaches to the next round and holds its section until the reviewer settles it (accepting) or replies (insisting, which wins — there is no second decline on a thread). Folded in from the same branch (#95): `SectionVerdict.comments[].anchor` gains an optional `occurrence` (§3), and — the part that bumps, since the new optional field alone would not — **`anchor.offset`'s value semantics changed**. It was `src.indexOf(text)` and could be `-1` only when `anchor.text` was absent from the markdown source; it is now the reviewer's chosen ordinal resolved against the source, so it can be `-1` while the text *is* present, whenever the rendered ordinal overruns the source's matches. A caller reading `-1` as "the phrase is not in the source" now reads it wrong; the correct reading is "the ordinal did not land — scope by the section rather than taking the first match." |
| 4 | 2026-08-07 | `ReviewInput` gains an optional `pass` — `{kind, posture}`, `kind` one of `architecture`/`line`/`checks`/`final` (§3). **Absent, a round completes exactly as it did at version 3**, so every existing caller is unaffected; the optional field alone would not bump (§1). What bumps is that when `pass` is present, `POST /complete`'s success condition changes — the same reasoning that bumped v3 for the round gate rather than for `/abandon`. A pass may only ADD a condition to the all-approved base and may never relax it: `checks` also requires every check flag on the round to carry a non-empty `result`, `final` also requires no unresolved suggested edit. So a caller sending a `pass` can now get a `409` on a round where every section *is* approved; its `error` text names the conjunct instead of a section count. Also: `ReviewSection.annotations[]` entries may carry an optional `result` (§3), the field those check flags are answered with. |
| 3 | 2026-08-05 | `POST /complete` now refuses an incomplete review round — `409` when any section's verdict is not `approved`, `400` when no verdicts were submitted for the currently loaded round. A caller that finishes a round the human left partly unapproved gets a status it previously never could. Two exemptions: a Q&A session (its round carries `questions`, never `sections`) and a server launched `--mode diff`. `POST /abandon` is the documented recovery from a refusal — it ends a session that cannot be signed off. The new endpoint alone would not warrant a bump (§1); the guard does. Also: the `Origin` check is now an exact host match rather than a prefix, and a request body whose `Content-Type` is not `application/json` is refused with `415`. `ReviewInput` gains an optional `split_on` (§3). |
| 2 | 2026-07-11 | `POST /next-round` and `POST /complete` now run the same loopback-`Origin` check and `MAX_SUBMIT_BYTES` body cap `POST /submit` already had — a caller sending a non-loopback `Origin` or a body over 256 MiB now gets a `403`/`413` it previously never could (see §5's endpoint table and error-response paragraph). Fixes #117. |
| 1 | 2026-07-11 | Initial contract, transcribing the surface shipped as of the `unified-session` (#109) and `task-card-split` (#110) stories. |

## 2. Invocation

```
python3 server.py --mode {review,qa,diff} --input PATH --output PATH [--no-browser]
```

| Flag | Required | Meaning |
|---|---|---|
| `--mode` | yes | One of `review`, `qa`, `diff` — exhaustive, enforced by argparse `choices=`. Gates two things only: which startup validator runs (§3) and the printed stdout label (`viva · {mode} mode · {url}`). |
| `--input` | yes | Any path. Read once, at startup, via `json.load`. Never re-read after boot — a later round's data arrives over HTTP (§5), not by re-reading this path. |
| `--output` | yes | Any path. Where round verdicts / Q&A answers get written, and the directory `server.url` (§4) is derived from. Does not need to already exist — its parent directories are created on demand (see §4). |
| `--no-browser` | no | Skips the `webbrowser.open()` call. Nothing else changes: `server.url` is still written, the server still binds and serves. This is the flag a headless caller passes on every invocation, since nothing else suppresses the browser launch. |

**The CLI `--mode` and the JSON `mode` field are two different things that
happen to share a name.** `--mode` controls only the two things above. Which
view the *browser* renders (review cards, Q&A cards, or a diff view) is
decided separately, at request time, by the `mode` field inside the JSON
object `GET /input` serves (`data.mode === 'review' | 'diff'`, else Q&A).
Nothing in `server.py` enforces that the two agree — a caller that launches
`--mode qa` but writes `"mode": "review"` into the input JSON gets
undefined-by-contract behavior. Every existing caller (`SKILL.md`,
`/viva-qa`, `/viva-diff`) keeps them in sync by convention, not by an
enforced invariant — a new caller needs to keep them in sync too.

## 3. `.viva/` round-file naming and shapes

The `.viva/` directory and filenames like `review-input-r{N}.json`,
`review-r{N}.json`, `qa-input.json`, `answers.json` are a **convention** the
existing skills (`SKILL.md`, `/viva-qa`, `/viva-diff`) follow, not
something `server.py` enforces — `--input`/`--output` accept any path. What
*is* enforced is the shape, by `scripts/schema.py`'s validators, called at
the boundary (on write by the producer, on read by the server):

- `validate_review_input(data)` — called by `server.py` at startup when
  `"sections" in data` (regardless of `--mode`; see the "review" row of the
  exit-code table in §6 for the case where a payload has no `sections` key
  at all), and by `scripts/parse_sections.py` on write. Requires
  `data.sections` to be a list; every entry must carry string `id`, `title`,
  `content`.
- `validate_verdicts(data)` — called by `server.py` on `POST /submit` when
  `"sections" in data`. Requires every section to carry a string `id` and a
  `verdict` in `{"approved", "changes", "info", "pending"}`, and every
  `suggestion` comment to carry non-empty string `replacement`.
- `validate_qa_input(data)` — called by `server.py` at startup when
  `args.mode == "qa"` (and only reached if `"sections" not in data`).
  Requires `data.questions` to be a list; every entry must carry string
  `id`, `text`. When a question carries `recommended_choice`, it must be a
  string that exactly matches an entry in that question's own `choices`.

`scripts/schema.py` is the canonical source for the field-level shapes
(`ReviewInput`, `ReviewSection`, `SectionVerdict`, `ReviewOutput`, `QAInput`,
`QAQuestion`, `QAAnswer`, `QAOutput`, `DiffInput` — all `TypedDict`s,
documentation only, since CI runs no type checker; the `validate_*`
functions above carry the enforced rules). Field tables, transcribed here so
a caller doesn't have to open that file, but not a substitute for it:

**`ReviewInput`** (`review-input-r{N}.json`, what a caller writes before a
review or diff round):

| Field | Required | Notes |
|---|---|---|
| `mode` | conventionally set | `"review"` or `"diff"` — this is the JSON `mode` field from §2, not the CLI flag. Nothing validates it, so **nothing load-bearing keys on it**: `/complete`'s diff exemption reads the server's launch `--mode`, not this field. |
| `doc_file` | no | Relative path shown in the UI. |
| `round` | no | Round number. |
| `approved_ids` | no | Section ids approved in prior rounds. |
| `split_on` | no | The `--split-on` regex this round was parsed with, recorded by `parse_sections.py`. **Absent** — not `null` — when the round used the auto-detected split level; a present non-string is a hard `validate_review_input` failure, because `loop.py rearm` hands this value straight back to `--split-on` and a `null` would silently re-split the next round by auto-detection. |
| `doc_type` | no | The doc type this session was started with (`loop.py start --type`), recorded by `parse_sections.py` and carried into every later round and a resume. Names a bundle `scripts/doc_types.py` resolves — shipped defaults in the plugin's `types/`, repo overrides in `.viva-types/`, repo wins on a name collision. **Absent** — not `null` — for an untyped session; a present non-string is a hard `validate_review_input` failure, for the same reason `split_on`'s is. Passthrough: `server.py` neither reads nor renders it. |
| `pass` | no | The depth and posture this round runs at: an object `{kind, posture}`. `kind` is required when the key is present and must be one of `architecture`, `line`, `checks`, `final`; `posture` is optional and must be `normal` or `hard` — a setting *on* the pass, never its own round field. **Absent** — not `null`, and never defaulted — for a round that runs no pass, which completes exactly as it did before this field existed. `validate_review_input` rejects a non-object `pass`, an unknown or missing `kind`, and an unknown `posture`. This is the one round field that changes when `POST /complete` succeeds (see its endpoint row in §5); `server.py` renders nothing from it. Recorded by `parse_sections.py --pass/--posture`, carried to the next round by `loop.py rearm`, deliberately **not** carried across a `loop.py start` resume the way `split_on`/`doc_type` are — depth is a per-round decision. |
| `sections` | **yes** | List of `ReviewSection`. |

**`ReviewSection`** (one entry per `sections[]`):

| Field | Required | Notes |
|---|---|---|
| `id` | **yes** | Stable id (`s1`, `s2`, …). |
| `title` | **yes** | Heading text. |
| `content` | **yes** | Verbatim markdown. |
| `annotations` | no | Advisory badges — `{kind, severity, message, anchor?, basis?, level?, result?}`. See DESIGN.md for the anchor overload. `result` is a check's finding for that flag; it is advisory like the rest, except on a `checks` round, where a flag whose `kind` names a check (`headings-present` today) holds `POST /complete` until it carries a non-empty one. |
| `diff` | no | Round-to-round change, if any. |
| `open_notes` | no | Carried-forward open-note threads, one per comment `cid`: `{cid, quote, status, exchanges}`. `status` is `open` or `declined` — the two unresolved statuses; a `settled` thread is dropped from later rounds and never appears here. Each exchange is `{round, verdict, note, response}`, where `verdict` is the *reviewer's* comment type for that turn (`changes`, `info`, `suggestion`), plus two presence-gated fields: `replacement`, the suggested wording carried verbatim, and `grounds`, the author's reason for declining that turn. Declining resolves nothing — it records an answer and leaves the thread live, so the section comes back for review. |

**`SectionVerdict`** (`review-r{N}.json`, what the server writes after a
`POST /submit`):

| Field | Required | Notes |
|---|---|---|
| `id` | **yes** | Section id. |
| `verdict` | **yes** | One of `approved`, `changes`, `info`, `pending`. |
| `comments` | no | Typed comment threads. Each carries `cid`, a `type` — one of `changes` (a directive), `info` (a question), `suggestion` (a directive with the wording attached) — and an optional `note`, and may carry `anchor: {text, offset, occurrence?}` (the reviewer's exact selection). `occurrence` is the 0-based index of that selection among the identical matches in the **rendered** section content, where the selection was made; `offset` is that same ordinal resolved against the markdown source, or `-1` when it does not resolve there. `-1` does not mean `anchor.text` is absent from the source — it means the ordinal did not land, so a caller must scope by the section rather than take the first match of a phrase that repeats. |
| `comments[].replacement` | with `type: "suggestion"` | The reviewer's exact wording for the anchored span, applied **verbatim** — no rewrite, no interpretation, nothing outside the anchor. It is the payload that makes the comment appliable, so `validate_verdicts` rejects a `suggestion` whose `replacement` is absent, non-string, or blank (`400` on `POST /submit`); the `note`, if any, is rationale rather than a second instruction. A section with a live suggestion derives to `changes`, so it cannot be approved, and the wording rides into the ledger and into a carried open-note exchange. Absent on every other type. |

The full output file (`ReviewOutput`) also carries `round` and
`submitted_early` at the top level, alongside `sections: [SectionVerdict]`.

**`QAInput`** (`qa-input.json`, what a caller writes before `--mode qa`):

| Field | Required | Notes |
|---|---|---|
| `mode` | conventionally set | `"qa"`. |
| `context` | no | One-liner shown in the title block. |
| `questions` | **yes** | List of `QAQuestion`. |

**`QAQuestion`**:

| Field | Required | Notes |
|---|---|---|
| `id` | **yes** | |
| `text` | **yes** | |
| `hint` | no | Shown below the question text. |
| `choices` | no | Rendered as chip buttons; omit for a free-text-only question. |
| `recommended_choice` | no | Must exactly match one entry in this question's `choices` (value, not index) — `validate_qa_input` rejects it otherwise. Renders as a small badge on the matching chip. Advisory only: never pre-selected, defaulted, or required; the human may pick any chip. Absent on every question written before this field existed, which renders unchanged. |

**`QAOutput`** (`answers.json`, what the server writes after the human
submits):

| Field | Required | Notes |
|---|---|---|
| `answers` | **yes** | List of `QAAnswer`. |
| `submitted_early` | no | |

**`QAAnswer`**: `id` (question id), `choice` (selected chip value, if any),
`note` (free-text field value), `attachments` (server-written image paths).

**`DiffInput`** — same shape as `ReviewInput` with `mode: "diff"`; one
`ReviewSection` entry per diff hunk.

`GET /input` (§5) serves the round-input file merged with a live
`ledger: [...]` array. That `ledger` key is injected by the server at serve
time — it is **not** part of any on-disk file's schema, and is not present
in `review-input-r{N}.json` or `qa-input.json` on disk. Each ledger row is
`{round, section_title, verdict, note}`, produced by
`schema.verdict_to_ledger_entry()` for every section whose verdict is
`changes` or `info` (`approved`/`pending` earn no row). `note` joins the
section's comment fragments with ` · `; a `suggestion`'s fragment carries the
reviewer's wording verbatim, tagged `suggested:` — the row's `verdict` is the
*section's*, so the fragment is where a reader learns wording was supplied.

## 4. `server.url` lifecycle

- Written once, atomically (temp file + `os.replace`), immediately after the
  server binds its port and before it starts serving requests.
- Its path is `Path(--output).parent / "server.url"` — **not** hardcoded to
  `.viva/server.url`. A caller that points `--output` somewhere other than a
  `.viva/` directory gets `server.url` written next to wherever `--output`
  lives.
- The directory `server.url` is written into (and any missing parent
  directories) is created on demand (`mkdir(parents=True, exist_ok=True)`) —
  a caller does not need to pre-create `--output`'s directory. This only
  matters for the boundary between "directory missing" (silently created,
  no error) and "directory unwritable" (a genuine permission failure still
  surfaces as an uncaught exception — see §6).
- Deleted in the shutdown path's `finally` block on every exit route
  (SIGINT, SIGTERM, `POST /abandon`, or the 2-second timer `POST /complete`
  starts) — never left behind on a clean exit.
- A caller that wants to detect "is a session already running" polls for
  this file's existence exactly as `SKILL.md`'s own launch guard does
  (`[ -f .viva/server.url ]`, adjusted for wherever this caller's `--output`
  lives).

## 5. The HTTP surface a caller drives

| Endpoint | Caller-facing? | Notes |
|---|---|---|
| `GET /input` | yes | Poll-once, not watched. Returns the loaded `--input` JSON merged with the live `ledger` array (§3). Most callers get everything they need from the round files directly and only use this to confirm shape. |
| `GET /events` | **no** | Server-sent events. This is the **browser tab's** private channel (round/complete/processing pushes that make the SPA reflow live) — a headless caller never opens it and this contract does not describe its wire format. |
| `POST /submit` | **no** | Browser-only. Exists for the human's browser tab to write verdicts/answers; guarded by an Origin check that rejects non-loopback origins (defense against a malicious page driving the write sink via CSRF) and a 256 MiB body cap. A headless caller never calls this. |
| `POST /next-round` | yes | The endpoint a caller uses to advance a running session: pushes a new round's JSON to the server without tearing the process down. Read `output` from the JSON body (preferred — travels like every other POST field; this is the form `SKILL.md`'s own loop and `/viva-qa`'s hand-off example both use) or the legacy `?output=` query-string param (still honored as a fallback, and still what `/viva-diff`'s re-arm step sends — narrowing that to the preferred form is a separate, future cleanup, not part of this contract change). If the payload has `"sections"`, it is validated with `validate_review_input` before being accepted. This is also the exact mechanism the qa→review hand-off (§7) uses. Guarded by the same loopback-Origin check and 256 MiB body cap as `/submit` (#117). |
| `POST /complete` | yes | Ends the session — **if the round may be signed off**. When the loaded round carries `sections` and the server was **not** launched `--mode diff`, the request is refused unless every section in that round carries an `approved` verdict in the most recent `/submit`: `400` `"no verdicts submitted for this round"` when nothing has been submitted since the round was loaded, `409` `"refusing to complete: N of M section(s) not approved"` otherwise. A round carrying a `pass` (§3) must satisfy that base **and** the pass's own conjunct — `checks`: every check flag carries a `result`; `final`: no unresolved suggested edit — so a fully approved round can also be refused `409`, with an `error` naming the pass rather than a section count. A round whose `sections` list is **empty** is refused `409 "the round carries no sections to approve"` — reachable, since `validate_review_input` accepts an empty list, and tested before the pass branch so the message never blames a conjunct for it. The recovery is the **next** round: this process loads its round once and replaces it only from `POST /next-round`, so a check answered on disk under the round already served is one this guard never sees. A pass never makes the request succeed where it would otherwise fail. Two exemptions, both by launch shape rather than by payload: a Q&A round carries `questions` and never `sections`, and a `--mode diff` server signs off with `changes` verdicts on record by design (`/viva-diff`'s empty-re-diff finish). The refusal is recoverable — `POST /abandon` ends a session that cannot be signed off, so a caller is never stuck holding a live server it cannot close. Accepts an optional JSON body (existing callers pass a free-form summary, e.g. `{rounds_total, sections_total, sections_revised}` — not schema-enforced) used only for the SSE `"complete"` event's payload. Starts a 2-second shutdown timer so the browser's SSE `"complete"` handler has time to render before the process exits. Guarded by the same loopback-Origin check and 256 MiB body cap as `/submit`. A qa-mode session's finish sequence must call this once `answers.json` exists (see `/viva-qa` step 4) unless it is handing off to a review round (§7) — otherwise the process and its `server.url` leak indefinitely. |
| `POST /abandon` | yes | Ends the session **without** finishing it — the route for a caller that decides to drop an unfinished round. Body is ignored. Sets the shutdown event immediately: no 2-second grace, and no SSE `"complete"` event, so the browser tab sees its `/events` stream drop and reports a lost connection rather than a completed review. Carries none of `/complete`'s sign-off meaning and writes no output file. Guarded by the same loopback-Origin check and 256 MiB body cap as `/submit`. |

Every error response, on any endpoint, is `application/json` with body
`{"error": "<message>"}` and a matching non-2xx status — `400` (invalid
JSON, wrong body shape, failed `validate_review_input`/`validate_verdicts`),
`403` (forbidden cross-origin `Origin` — `/submit`, `/next-round`,
`/complete`, and `/abandon` all run this check; the host must be exactly
`127.0.0.1` or `localhost` over `http`, not merely a prefix of the Origin),
`409` (`/complete` — the round is not all-approved, or its `pass`'s added
conjunct is unsatisfied; see its endpoint row),
`413` (body over 256 MiB — same four endpoints), `415` (a request body whose
`Content-Type` is not `application/json` — same four endpoints; this is what
forces a cross-origin caller into a preflight rather than a simple POST),
`404` (unmatched path), `500` (`/submit` — `IOError`/`OSError`
writing the output file). A caller can distinguish any failure from a
success by content type alone, since successes are already uniformly
`{"ok": true}` JSON.

## 6. Error and timeout semantics

Process exit codes:

| Exit code | stderr shape | When |
|---|---|---|
| `0` | `viva · done` on stdout, nothing distinctive on stderr | Graceful shutdown — `SIGINT`, `SIGTERM` (both handled, so a parent's `proc.terminate()` exits `0` here rather than dying at `-15`), `POST /abandon`, or the 2-second timer after `POST /complete` fires. |
| `2` | argparse's own usage block | A CLI usage error — a missing required flag, or `--mode` given a value outside `{review,qa,diff}`. |
| `1` | **one line**, `viva: invalid {review-input,qa-input} {path}: {message}` | One of the two deliberate `sys.exit(...)` calls: `validate_review_input`/`validate_qa_input` rejected `--input`'s contents at startup. A caller can pattern-match on the `viva: ` prefix to distinguish this from the next row. |
| `1` | **multi-line Python traceback**, no `viva: ` prefix | Every other startup failure: `--input` path doesn't exist or isn't readable, `--input`'s contents aren't valid JSON, or `--output`'s directory can't be created/written to because of a permission failure (its *absence* alone is not a failure — see §4). Nothing in `server.py` catches these; they are uncaught Python exceptions. |

**A `--mode review` (or `--mode diff`) payload with no `"sections"` key
skips startup validation entirely** — `validate_review_input` only runs when
`"sections" in data`, and the `elif` that would run `validate_qa_input`
guards on `args.mode == "qa"`. A malformed-but-`sections`-less review
payload boots the server with no validation error at all; the failure (if
any) surfaces later, indirectly, when the browser or a `/next-round` caller
hits the missing data.

**The server itself has no request or session timeout.** It blocks in a
loop on `server.handle_request()` (a 0.5-second internal socket timeout
just lets it re-check the shutdown flag — never visible to a caller) until
shutdown is signaled. Any "timeout" a caller experiences is entirely its own
choice of how long to wait on the round-file-appears poll — the same
guidance `SKILL.md` gives its own agent: issue the wait with a generous
timeout (SKILL.md uses ~10 minutes), and re-issuing the identical wait after
a timeout is safe and idempotent, since it only re-polls.

**Caveat — soft, client-side-only timeout on the "processing" spinner
(#119).** After a human submits Q&A answers and a caller synthesizes a
review payload for `POST /next-round` (§7), the browser shows a "processing"
spinner between those two events. If neither a `round` nor `complete` SSE
event arrives within ~20 seconds, the browser shows a `Still waiting — check
the terminal.` banner — informational only, the spinner keeps spinning
underneath it, and the banner disappears the moment the event eventually
arrives. This is a **browser-side visibility signal, not a server or wire
timeout**: the server still has no request or session timeout (above), the
threshold is a client-side constant with no wire representation, and nothing
about `/next-round`'s contract changes. If the caller's synthesis step fails
or hangs before it POSTs, the human now sees that banner, but the caller's
own process exit is still the only source of a precise error — the banner
just says "check the terminal," it can't say what it will find there. A
caller building this hand-off should still treat its synthesis step as
needing its own bounded time budget and terminal-visible failure path,
since the *reason* for the delay is never visible to the browser, only its
duration.

## 7. Session types this contract currently produces

### qa → review hand-off (`unified-session`, #109)

This is **not** a third `--mode` value. A caller launches `--mode qa`
exactly as `/viva-qa` does today, waits for `answers.json`, and — instead
of tearing the server down — POSTs an
ordinary `sections`-shaped `ReviewInput` payload (§3) to the same server's
still-running `/next-round`. The same browser tab reflows in place from Q&A
cards to review cards, round 1.

The server recognizes this as a hand-off purely operationally: the prior
round held on this process was Q&A-shaped (`"questions" in` the previously
loaded input) and the new payload is review-shaped (`"sections" in`
it) — `server.py`'s `handoff = "questions" in _input_data and "sections" in
new_data`. When that's true, the server prints a distinct stdout line,
`viva · hand-off qa → review · {url}`, instead of (or in addition to) the
usual `/next-round` handling — a terminal-watching caller can see the
hand-off happen without inferring it from the browser reflowing.

**`ReviewInput`'s wire shape carries no field marking a round as
qa-originated.** This is deliberate (see `unified-session`'s design doc,
"Out of scope: Schema changes") — the signal is the *sequence* of payloads
one server process has seen, not something a caller can query after the
fact from the JSON alone, or reconstruct by reading `review-input-r1.json`
in isolation.

**The `output` given to this `/next-round` call must be a path distinct
from the `--output` this session was launched with** (e.g.
`review-r1.json`, not `answers.json`) — `/next-round` and a review round's
`/submit` both write to whatever `output` currently points at, and reusing
the Q&A output path lets the first review `/submit` silently overwrite the
answers a caller just finished reading.

**A hand-off does not call `POST /complete` right after `answers.json` is
read.** Doing so would shut down the same process the hand-off is about to
reuse. Instead, the *eventual* review round's own `/complete` call, at
whatever round it finishes on, ends the process — the same mechanism
`SKILL.md`'s own review loop already uses, applied once to the whole
qa-then-review session rather than to the qa phase alone.

### `--split-on` task-card splitting (`task-card-split`, #110)

This is a `scripts/parse_sections.py` CLI flag and not a `server.py` flag. It
**is** recorded as a round-file field (`split_on`, §3) so a later round and a
later resume re-split identically — but the server neither reads nor writes it,
and it changes how a round's `sections` list gets
produced from the source document, before that JSON ever reaches
`server.py`.

```
python3 scripts/parse_sections.py PLAN.md \
  --output .viva/review-input-r1.json --round 1 \
  --split-on '^Task \d+'
```

Match rule: `re.search` (not `re.match` — the pattern need not anchor at
the start of the title), case-sensitive, tested against every heading
regardless of `#` depth. When given, `--split-on` is the **sole** selection
rule and entirely replaces the default level-counting auto-detection,
including its "coarsen one level if there are more than 20 sections"
fallback — an explicit caller-supplied pattern is not a heuristic guess
that needs that protection. Omit `--split-on` for the unchanged
auto-detect behavior.

**Zero matches is a hard error, not a silent fallback to auto-detection**:
`parse_sections.py` exits non-zero with `viva: --split-on '<pattern>'
matched no heading in <doc>`. An invalid regex is also a hard error,
`viva: invalid --split-on pattern '<pattern>': <re.error message>`.
