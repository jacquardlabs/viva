# Screenshots / image attachments in viva feedback

**Date:** 2026-06-27
**Status:** Implemented
**Branch:** `worktree-screenshots-in-feedback`

## Problem

When reviewing a viva document or answering a brainstorming Q&A prompt, the
user can only respond with text. Some feedback is far easier to *show* than to
type — e.g. "here's the current pricing page" or "the layout should look like
this." Today the user has to transcribe what's on screen into prose. We want to
let them paste a screenshot (or drop / pick an image file) directly into the
note field and have Claude actually see it.

## Goal

Allow image attachments on every viva feedback surface that has a note field:

1. **Review mode** — section notes for `request changes` / `need info`.
2. **Q&A mode** — brainstorming answer notes.

Claude must receive the images as real files it can `Read`, so they become
context for the rewrite / answer.

## Non-goals

- Screenshots are **not** referenced in the committed `## Revision History`.
  The image is transient context for the rewrite; the verbatim note text
  already lands in the ledger. Attachment files are round-scoped and live under
  the gitignored `.viva/`.
- No image editing, annotation, cropping, or OCR in the browser.
- No persistence of images beyond the session / round lifecycle.

## Architecture

Data flow today: browser collects per-item `note` text → POSTs JSON to
`/submit` (review) or the Q&A submit → server writes the output JSON →
Claude reads that JSON to get verdicts + notes.

We extend this flow end to end. The chosen transport is **extract-on-submit**:
the browser holds pasted images in memory and only ships them inside the
existing submit payload; the server turns them into files at the boundary,
before the output JSON is written.

### Transport decision (extract-on-submit vs upload-on-paste)

**Chosen: extract-on-submit.** The browser keeps each captured image as an
in-memory blob and renders a preview client-side from a blob/data URL (no
server round-trip for preview). Images are serialized into the existing submit
payload. The server decodes, validates, writes files, and rewrites the payload
to reference paths **before** `write_output`.

Rejected: upload-on-paste (a new `/upload` endpoint that saves each paste
immediately). It orphans files when a user pastes then flips to `approved` or
never submits, and adds a second endpoint. Extract-on-submit writes only what
is actually submitted and fits the existing single-endpoint pattern. The only
thing upload-on-paste buys — instant preview — is already free client-side.

## Components

### 1. Capture (browser)

- One capture handler feeds every note field, reachable three ways:
  - **Paste** (`Cmd/Ctrl-V`) into the textarea — the primary path, matches the
    screenshot-to-clipboard use case.
  - **Drag-and-drop** an image file onto the card.
  - **📎 attach** button → file picker fallback for already-saved files.
- Captured images render as a thumbnail strip under the textarea. Each
  thumbnail has an **×** to remove it before submit (undo for a mis-paste).
- Images are held as blobs in `rState.verdicts[id]` / `qState.answers[id]`
  alongside the existing `note`, keyed the same way.
- Multiple images per note are allowed.

### 2. Transport (browser → server)

On submit, each held image is serialized into its section/question object:

```json
{ "id": "...", "verdict": "changes", "note": "...",
  "images": [ { "data": "<base64>", "mime": "image/png" } ] }
```

The client never supplies a filename or path — only bytes and MIME type.

### 3. Server boundary (security-critical)

Before `write_output`, the server walks the submitted payload and, for each
item carrying `images`:

- Validates `mime` against an allowlist: `image/png`, `image/jpeg`,
  `image/gif`, `image/webp`. Disallowed → drop that image (note text still
  submits).
- Enforces a max decoded-size cap. Oversized → drop that image.
- **Generates the filename itself** — the client value is never trusted:
  `.viva/attachments/r{round}-{sectionId}-{index}.{ext}`, where `ext` derives
  from the validated MIME.
- Decodes the base64, writes the file.
- Replaces the item's `images` array with `attachments: [ "<project-relative
  path>" ]` in the data that gets written to the output JSON. Paths use the
  same project-relative form as `review-r{N}.json` so Claude's `Read` just
  works.

Lifecycle: `.viva/attachments/` is created on demand and cleared with the rest
of `.viva` at the start of round 1 (alongside the existing
`rm -f .viva/review-input-r*.json .viva/review-r*.json`). It is gitignored.

This applies symmetrically to review submit and Q&A submit.

### 4. Skill consumption (SKILL.md)

This is what makes the feature real — without it the UI/server work is inert.

- Document the new per-verdict field: `attachments: [paths]`.
- Update the review loop (steps 4–5): before rewriting a `changes` / `info`
  section, Claude **`Read`s each attachment path** for that section so the
  image is in context for the rewrite.
- Update the Q&A flow analogously: read any attachments on an answer before
  incorporating it.

## Error handling

- Invalid base64 / decode failure → drop that image, keep the note, continue.
- Disallowed MIME or oversized → drop that image, keep the note.
- Write failure for an attachment → drop that attachment; do not fail the whole
  submit (the existing 500-on-write-failure behavior for the output JSON is
  unchanged).
- A submit with zero valid images behaves exactly as today.

## Testing

Unit tests for the server extract/validate boundary:

- Valid PNG → file written under `.viva/attachments/`, payload rewritten with a
  project-relative `attachments` path, base64 no longer present.
- Disallowed MIME (e.g. `image/svg+xml` or `application/pdf`) → image dropped,
  note preserved.
- Oversized image → dropped, note preserved.
- Client-supplied `path`/`filename` field ignored — server-generated name used.
- Submit with no images → output identical to current behavior.

## Open questions

None outstanding. Size cap value and exact filename scheme to be finalized in
the implementation plan.

---

## Revision History

Signed off via viva review — 1 round, 9 sections, 0 revised. 2026-06-27
