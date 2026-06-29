# Per-comment image attachments in viva review

**Date:** 2026-06-28
**Status:** Designed
**Issue:** #66

## Problem

The multiple-inline-comments refactor (#68, branch `worktree-anchor-updates`) replaced the single per-section note with a `comments[]` array and in doing so dropped image attachments on review feedback. `wireCapture` (paste / drag / 📎-attach → thumbnail strip) now serves only Q&A cards; review comments can no longer carry a screenshot. The prior section-level screenshots feature (#18) attached images to a section's single note — a shape that no longer exists under the comment model.

## Goal

Let a review comment carry image attachments, scoped per comment:

- Paste / drag / 📎-attach an image into the comment popover; render a thumbnail strip with per-image remove.
- Ship images inside each comment object on submit (`comments[i].images`).
- Extend the server's `extract_attachments` to walk `comments[].images`, validate (MIME allowlist + decoded-size cap), write `.viva/attachments/r{round}-{safe_cid}-{n}.{ext}`, and rewrite to `comments[i].attachments`.
- Restore the SKILL.md instruction: before acting on a comment, `Read` each `comment.attachments` path so the screenshot informs the edit or answer.

## Non-goals

Same as the prior screenshots spec: no image editing, no annotation, no OCR, no persistence beyond the session/round lifecycle, no appearance in the committed Revision History (images are transient context; the note text lands in the ledger).

## Data model

Images travel as base64 inside the submit payload; the server rewrites them to file paths before writing the output JSON. The comment shape at each stage:

**Browser → `/submit` (in transit only; server strips `images`):**
```json
{
  "cid": "s1-c1",
  "type": "changes",
  "note": "fix this",
  "anchor": { "text": "retries 3×", "offset": 9 },
  "open": true,
  "settled": false,
  "images": [ { "data": "<base64>", "mime": "image/png" } ]
}
```

**`review-r{N}.json` (what the agent reads):**
```json
{
  "cid": "s1-c1",
  "type": "changes",
  "note": "fix this",
  "anchor": { "text": "retries 3×", "offset": 9 },
  "open": true,
  "settled": false,
  "attachments": [ ".viva/attachments/r1-s1_c1-0.png" ]
}
```

The section-level shape is unchanged — `images` never appears at the section level for review mode. A comment with no images submits identically to today.

## Architecture

The chosen transport remains **extract-on-submit** (established in the prior screenshots spec): the browser holds images in memory, ships them inside the existing `/submit` payload, and the server decodes/validates/writes at the boundary before `write_output`. No new endpoints.

## Components

### 1. `wireCapture` — null-safe `droppable` (browser)

The existing `wireCapture` helper adds persistent listeners to the `card` element. When called from a re-openable comment popover, stale closures from prior opens would reference a detached `stripEl` (after `pop.innerHTML = ''`) and throw on `stripEl.parentElement.style.display`. Fix:

```js
// before
const droppable = () => stripEl.parentElement.style.display !== 'none';
// after
const droppable = () => stripEl.isConnected && stripEl.parentElement != null
                     && stripEl.parentElement.style.display !== 'none';
```

Stale closures short-circuit silently. This is a pure defensive improvement; Q&A behavior is unchanged.

### 2. `openCommentPopover` — capture UI (browser)

Append three elements to the popover HTML: `.thumb-strip`, `.attach-btn` (📎 attach image), and a hidden `<input type="file" accept="image/*" multiple>`. Create a per-open `captureState = {}` local, then wire it:

```js
const captureState = {};
wireCapture(() => captureState, ta, strip, attachBtn, fileInput, el('rcard-' + id));
```

The review card (`el('rcard-' + id)`) is the drag target so drops anywhere on the open card land in the comment's images. The `droppable()` check uses `strip.isConnected` — when the popover is hidden/emptied between opens, stale card-level drag listeners no-op safely.

On save, pass images to `addComment`:

```js
addComment(id, {
  type: pop.dataset.type, note,
  anchor: anchor || undefined,
  images: captureState.images?.length ? captureState.images : undefined
});
```

### 3. `addComment` — store images (browser)

Accept and spread `images` onto the comment object:

```js
function addComment(id, { type, note, anchor, images }) {
  ...
  cs.push({ cid: ..., type, note: note || '',
            ...(anchor && { anchor }),
            ...(images?.length && { images }),
            open: true, settled: false });
  ...
}
```

`submitReview` needs no change — it already spreads the full `comments[]` array including any `images` keys.

### 4. `extract_attachments` — walk `comments[].images` (server)

Extract the inner image-write loop into a private helper `_write_item_images(item, safe_id, attach_dir, rnd)` that pops `images`, validates, writes, and sets `attachments` on the item. Call it for both the existing top-level items and the new per-comment path:

```python
def extract_attachments(data, output_path, rnd):
    attach_dir = Path(output_path).parent / "attachments"
    for item in list(data.get("sections", [])) + list(data.get("answers", [])):
        if not isinstance(item, dict):
            continue
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(item.get("id", "x"))) or "x"
        _write_item_images(item, safe_id, attach_dir, rnd)
        for cmt in item.get("comments", []) or []:        # ← new
            if not isinstance(cmt, dict):
                continue
            safe_cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(cmt.get("cid", "x"))) or "x"
            _write_item_images(cmt, safe_cid, attach_dir, rnd)
    return data
```

**Filename scheme for comment images:** `r{rnd}-{safe_cid}-{n}.{ext}` (e.g. `r1-s1_c1-0.png`). The cid already encodes section and comment number; no collision is possible with section-level or Q&A attachment names.

### 5. `SKILL.md` — consume attachments (agent)

One addition in step 4 (Rewrite and re-arm), in the paragraph describing the `comments[]` loop:

> Before applying or answering a comment, if `comment.attachments` is present, `Read` each listed path — the image is context for the edit or answer.

No other SKILL.md changes. The existing open-notes and verdict-table prose covers the comment loop correctly.

## Error handling

Same policy as the prior screenshots spec: invalid base64, disallowed MIME, or oversized image → drop that image, keep the note, continue. A write failure for a single attachment drops that attachment; the submit and the rest of the payload are unaffected. A comment with zero valid images behaves identically to a comment with no images at all.

## Testing

**`tests/test_server_attachments.py`** — two new unit tests:

- `test_comment_images_extracted` — submit a section with one comment carrying a valid PNG; assert `comment.attachments` path is written with correct bytes, `images` is absent, section-level item has no `attachments` noise.
- `test_comment_image_bad_mime_dropped` — submit a comment with `image/svg+xml`; assert `attachments` absent, nothing written, `note` preserved.

Update existing `test_html_has_capture_wiring` — add `"captureState"` to the needle list, confirming the popover wires the per-open state object.

**`tests/test_server_comments_submit.py`** — one new HTTP integration test:

- `test_comment_images_survive_submit` — POST a review submit with a comment carrying a base64 PNG; assert the output JSON has `comment.attachments` set and no `images` key.

## Open questions

None.

---

## Revision History

Signed off via viva review — 1 round, 4 sections, 0 revised. 2026-06-28
