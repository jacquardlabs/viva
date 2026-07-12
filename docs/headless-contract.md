# viva headless invocation contract

**Contract version: 1**

This document is for a program that launches `server.py` as a subprocess and
reads/writes its JSON files — a headless caller like jig — not for the human
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
- changing what an existing exit code means
- removing an HTTP endpoint, or changing its request/response shape

**Not** a version bump: adding an optional field, adding a new endpoint,
adding a new `--mode` value, wording/prose clarifications to this file.

This is independent of `plugin.json`'s semantic-release semver, which bumps
on every merged feature or fix (including ones that never touch this
surface — a browser-side CSS change, an unrelated bug fix). This integer
answers one narrower question: "did the surface I integrated against move."
It follows the precedent `scripts/preferences.py` already sets for its own
on-disk store (`VERSION = 1`).

Changelog:

| version | date | change |
|---|---|---|
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
`.claude/skills/viva/brainstorming-qa.md`, `.claude/skills/viva/diff.md`)
keeps them in sync by convention, not by an enforced invariant — a new
caller needs to keep them in sync too.

## 3. `.viva/` round-file naming and shapes

The `.viva/` directory and filenames like `review-input-r{N}.json`,
`review-r{N}.json`, `qa-input.json`, `answers.json` are a **convention** the
existing skills (`SKILL.md`, `brainstorming-qa.md`, `diff.md`) follow, not
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
  `verdict` in `{"approved", "changes", "info", "pending"}`.
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
| `mode` | conventionally set | `"review"` or `"diff"` — this is the JSON `mode` field from §2, not the CLI flag. |
| `doc_file` | no | Relative path shown in the UI. |
| `round` | no | Round number. |
| `approved_ids` | no | Section ids approved in prior rounds. |
| `sections` | **yes** | List of `ReviewSection`. |

**`ReviewSection`** (one entry per `sections[]`):

| Field | Required | Notes |
|---|---|---|
| `id` | **yes** | Stable id (`s1`, `s2`, …). |
| `title` | **yes** | Heading text. |
| `content` | **yes** | Verbatim markdown. |
| `annotations` | no | Advisory badges — `{kind, severity, message, anchor?, basis?, level?}`. See DESIGN.md for the anchor overload. |
| `diff` | no | Round-to-round change, if any. |
| `open_notes` | no | Carried-forward open-note threads. |

**`SectionVerdict`** (`review-r{N}.json`, what the server writes after a
`POST /submit`):

| Field | Required | Notes |
|---|---|---|
| `id` | **yes** | Section id. |
| `verdict` | **yes** | One of `approved`, `changes`, `info`, `pending`. |
| `comments` | no | Typed comment threads; each may carry `anchor: {text, offset}` (the reviewer's exact selection). |

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
`changes` or `info` (`approved`/`pending` earn no row).

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
  (SIGINT, or the 2-second timer `POST /complete` starts) — never left
  behind on a clean exit.
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
| `POST /next-round` | yes | The endpoint a caller uses to advance a running session: pushes a new round's JSON to the server without tearing the process down. Read `output` from the JSON body (preferred — travels like every other POST field) or the legacy `?output=` query-string param (still honored as a fallback; existing skills use this form). If the payload has `"sections"`, it is validated with `validate_review_input` before being accepted. This is also the exact mechanism the qa→review hand-off (§7) uses. |
| `POST /complete` | yes | Ends the session. Accepts an optional JSON body (existing callers pass a free-form summary, e.g. `{rounds_total, sections_total, sections_revised}` — not schema-enforced) used only for the SSE `"complete"` event's payload. Starts a 2-second shutdown timer so the browser's SSE `"complete"` handler has time to render before the process exits. |

Every error response, on any endpoint, is `application/json` with body
`{"error": "<message>"}` and a matching non-2xx status — `400` (invalid
JSON, wrong body shape, failed `validate_review_input`/`validate_verdicts`),
`403` (`/submit` only — forbidden cross-origin `Origin`), `413` (`/submit`
only — body over 256 MiB), `404` (unmatched path), `500` (`/submit` —
`IOError`/`OSError` writing the output file). A caller can distinguish any
failure from a success by content type alone, since successes are already
uniformly `{"ok": true}` JSON.

## 6. Error and timeout semantics

Process exit codes:

| Exit code | stderr shape | When |
|---|---|---|
| `0` | `viva · done` on stdout, nothing distinctive on stderr | Graceful shutdown — `SIGINT`, or the 2-second timer after `POST /complete` fires. |
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
exactly as `.claude/skills/viva/brainstorming-qa.md` does today, waits for
`answers.json`, and — instead of tearing the server down — POSTs an
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

### `--split-on` task-card splitting (`task-card-split`, #110)

This is a `scripts/parse_sections.py` CLI flag, not a `server.py` flag or a
new round-file field — it changes how a round's `sections` list gets
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
