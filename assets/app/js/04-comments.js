/* ─── Comments (multi-comment review) ───────────────────────────
   Verdict is DERIVED, never picked: no active comments → approved/pending;
   any `changes` or `suggestion` comment → changes; otherwise info. Same rule
   in DESIGN.md, SKILL.md, and schema.py's COMMENT_TYPES — keep in sync. */
function commentsOf(id) { return (rState.verdicts[id] ||= {}).comments ||= []; }

// Real, unsettled feedback — basis for the verdict, button count, rendered
// list, and whether a section can approve. A suggestion qualifies on its
// `replacement` alone; its note is optional rationale.
function activeComments(id) {
  return (rState.verdicts[id]?.comments || []).filter(c => !c.settled && (c.note || c.replacement));
}

function deriveVerdict(id) {
  const active = activeComments(id);
  if (active.length === 0) return rState.verdicts[id]?.verdict === 'approved' ? 'approved' : 'pending';
  return active.some(c => c.type === 'changes' || c.type === 'suggestion') ? 'changes' : 'info';
}

function addComment(id, { type, note, anchor, images, replacement }) {
  const cs = commentsOf(id);
  const n = cs.reduce((m, c) => Math.max(m, +(String(c.cid).split('-c')[1] || 0)), 0);
  cs.push({ cid: id + '-c' + (n + 1), type, note: note || '',
            ...(anchor && { anchor }),
            ...(replacement && { replacement }),
            ...(images?.length && { images }),
            open: true, settled: false });
  syncCard(id);
}

function removeComment(id, cid) {
  const v = rState.verdicts[id]; if (!v) return;
  v.comments = (v.comments || []).filter(c => c.cid !== cid);
  syncCard(id);
}

// Repaint everything that derives from a card's comments: dot, primary
// button, the margin (marks, pins, notes, spec, rule).
function syncCard(id) {
  syncReviewDot(id);
  renderPrimaryButton(id);
  // The margin is every surface's comment list now: same job, one column.
  renderDocMargin(id);
  updateReviewStats();
}

// One control, one grammar: approve reads "approve" only while nothing is
// open; an approved section offers to withdraw.
function renderPrimaryButton(id) {
  const btn = el('rbtn-primary-' + id); if (!btn) return;
  const n = activeComments(id).length;
  const approved = deriveVerdict(id) === 'approved';
  btn.className = 'nt-btn ' + (approved || n ? 'is-quiet' : 'is-pri');
  // With comments open the control cannot act, and it says so: the disabled
  // grammar plus a title naming the way out, never an enabled silent no-op.
  // No checkmark on a section whose derived verdict is `changes`.
  const refused = !approved && n > 0;
  btn.setAttribute('aria-disabled', refused ? 'true' : 'false');
  btn.title = refused ? 'approve is refused while comments are open — settle or remove them first' : '';
  btn.innerHTML = approved ? '<span aria-hidden="true">&#8634;</span> withdraw approval'
    : n ? (n + (n === 1 ? ' comment' : ' comments') + ' open')
        : '<span aria-hidden="true">&#10003;</span> approve<kbd>a</kbd>';
}

/* ─── Evidence popover (#106) ────────────────────────────────
   A `.ev-btn` click fetches `/evidence?ref=...` and shows the cited lines in
   a small fixed panel near the button — never inside `.spec-strip` itself
   (see the CSS comment), so the state run stays one line regardless. One
   panel at a time: a second click on the SAME button closes it; a click
   anywhere outside the panel closes it too. */
let _evidencePanel = null;
function closeEvidencePanel() {
  if (_evidencePanel) { _evidencePanel.remove(); _evidencePanel = null; }
}
function openEvidencePanel(btn) {
  if (_evidencePanel && _evidencePanel.dataset.forRef === btn.dataset.ref) {
    closeEvidencePanel();
    return;
  }
  closeEvidencePanel();
  const panel = document.createElement('div');
  panel.className = 'ev-panel';
  panel.dataset.forRef = btn.dataset.ref;
  panel.textContent = 'loading…';
  document.body.appendChild(panel);
  const r = btn.getBoundingClientRect();
  panel.style.top = Math.round(r.bottom + 4) + 'px';
  panel.style.left = Math.round(r.left) + 'px';
  _evidencePanel = panel;
  fetch('/evidence?ref=' + encodeURIComponent(btn.dataset.ref))
    .then(r2 => { if (!r2.ok) throw new Error('not found'); return r2.json(); })
    .then(d => {
      if (_evidencePanel !== panel) return; // closed/replaced while in flight
      panel.innerHTML = '<div class="ev-panel-h">' + esc(d.path) + ':'
        + d.start + '-' + d.end + '</div><pre class="ev-panel-body">'
        + esc(d.lines.join('\n')) + '</pre>';
    })
    .catch(() => {
      if (_evidencePanel === panel) panel.textContent = 'evidence unavailable';
    });
}
document.addEventListener('click', e => {
  const btn = e.target.closest('.ev-btn');
  if (btn) { e.stopPropagation(); openEvidencePanel(btn); return; }
  if (_evidencePanel && !e.target.closest('.ev-panel')) closeEvidencePanel();
});

/* ─── Selection → popover comment creation ─────────────────────
   Finishing a text selection inside a section's rendered content auto-opens
   the comment popover anchored to that selection — no extra click. `mouseup`
   is the "selection finished" signal (selectionchange fires continuously
   mid-drag). A plain click (collapsed selection), a selection outside any
   section content, or one inside the popover itself is ignored. */
document.addEventListener('mouseup', () => {
  // Defer a tick so the browser has finalized the selection after mouseup.
  setTimeout(() => {
    const sel = document.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const text = sel.toString().trim();
    if (!text) return;
    const start = toElement(sel.anchorNode);
    const content = start && start.closest ? start.closest('.section-content') : null;
    if (!content) return;
    // The margin and check gutter are descendants of the section container,
    // so `.section-content` alone doesn't mean "in the document" — the prose
    // cell (`.rp`) is the document.
    if (!start.closest('.rp') || start.closest('.sug-ins')) return;
    const m = content.id.match(/^rcontent-(.+)$/);
    if (!m) return;
    // Which occurrence of a repeated phrase was picked exists only in the
    // rendered content; read the ordinal there and resolve the same ordinal
    // against the markdown source (#95). `getRangeAt(0)`, not anchorNode/
    // focusNode — a backwards drag reports endpoints in reverse order.
    const occurrence = occurrenceInRendered(content, sel.getRangeAt(0), text);
    openCommentPopover(m[1],
      { anchor: { text, offset: offsetInSource(m[1], text, occurrence), occurrence } });
  }, 0);
});

// A selection endpoint may be a text node; normalize to its element.
function toElement(node) {
  return node && node.nodeType === 3 ? node.parentElement : node;
}

/* No cross-pane selection guard needed: a unified hunk is one column in
   source order, so every selection inside it is already contiguous. */

/* ─── Anchor resolution: rendered occurrence → source offset (#95) ─────
   A repeated phrase's identity is which occurrence — read where the
   selection happened, then resolve the same ordinal against the source. The
   ordinal survives a re-render; the offset is what the source edit uses. */

// 0-based ordinal of the selected occurrence of `text`: how many occurrences
// *begin* before the selection starts, counted over the section's own
// rendered content.
function occurrenceInRendered(root, range, text) {
  if (!root.contains || !root.contains(range.startContainer)) return 0;
  // The margin lives inside the section container and echoes annotated
  // wording (.nt-quote, .open-thread-quote); counting it would inflate the
  // ordinal (#95-style bug). Count the prose only.
  const counted = proseOccurrenceBefore(root, range, text);
  if (counted !== null) return counted;
  const all = document.createRange();
  all.selectNodeContents(root);
  const pre = document.createRange();
  pre.selectNodeContents(root);
  try { pre.setEnd(range.startContainer, range.startOffset); }
  catch (e) { return 0; }
  return countStartsBefore(all.toString(), text, pre.toString().length);
}

/* ─── Prose-only text walking ─────────────────────────────────
   Filters out the margin, check gutter, and open popover — section-container
   descendants that are not the text under review. Inert in the accordion,
   so both surfaces share this walk. */
function proseWalker(root) {
  return document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      for (let p = node.parentElement; p && p !== root; p = p.parentElement) {
        const c = p.classList;
        // `.sug-ins` is proposed wording, never in the document — counting
        // it would inflate every later ordinal and anchor a comment to text
        // the author never wrote. `.sug-del` is real source text and stays
        // counted.
        if (c && (c.contains('rm') || c.contains('rg') || c.contains('comment-popover')
                  || c.contains('sug-ins')))
          return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
}

// 0-based ordinal counted over prose text only, or null when the selection
// starts somewhere the walk can't place (an element boundary) — the caller
// then falls back to the Range count.
function proseOccurrenceBefore(root, range, text) {
  if (!text) return 0;
  const walk = proseWalker(root);
  let hay = '', pre = null, node;
  while ((node = walk.nextNode())) {
    if (node === range.startContainer) pre = hay.length + Math.max(0, range.startOffset);
    hay += node.nodeValue;
  }
  return pre === null ? null : countStartsBefore(hay, text, pre);
}

// Occurrences of `needle` in `hay` that start before index `limit`. Steps by 1
// so an overlapping repeat ("aa" in "aaaa") counts the same way nthIndexOf
// resolves it — the two must agree or the ordinal names a different span in
// the source than it did on screen.
function countStartsBefore(hay, needle, limit) {
  if (!needle) return 0;
  let n = 0, i = hay.indexOf(needle);
  while (i >= 0 && i < limit) { n++; i = hay.indexOf(needle, i + 1); }
  return n;
}

// Index of the `n`th (0-based) occurrence of `needle` in `hay`, -1 if there is
// no such occurrence.
function nthIndexOf(hay, needle, n) {
  if (!needle) return -1;
  let i = hay.indexOf(needle);
  while (i >= 0 && n > 0) { i = hay.indexOf(needle, i + 1); n--; }
  return i;
}

// Char offset of the reviewer's chosen occurrence in the section's raw
// markdown source. -1 means "unplaced," not "absent" — the anchor still
// stores text + occurrence, and the agent scopes by section rather than
// guessing a match.
function offsetInSource(id, text, occurrence) {
  const src = _pendingMarkdown.get(id)
    || REVIEW_DATA.sections.find(s => s.id === id)?.content || '';
  const n = occurrence > 0 ? occurrence : 0;
  const at = nthIndexOf(src, text, n);
  // Rendered and source occurrence counts can diverge (markdown syntax
  // stripped, diff chrome added). An overrun ordinal still resolves when the
  // source holds exactly one match; with two or more it stays unresolved
  // rather than silently collapsing to the first.
  if (at < 0 && n > 0 && nthIndexOf(src, text, 1) < 0) return nthIndexOf(src, text, 0);
  return at;
}

// A small popover: type chips + note field + save/cancel. `anchor` is
// {text, offset} or null (whole-section note); `type` pre-picks a chip,
// defaulting to `changes`. `suggest wording` adds a replacement field
// (review mode only — diff hunks scope it out, #166).
function openCommentPopover(id, { anchor, type } = {}) {
  const pop = el('rpop-' + id); if (!pop) return;
  pop.dataset.type = 'changes';
  const canSuggest = !REVIEW_DATA || REVIEW_DATA.mode !== 'diff';
  const captureState = {};
  // Where focus goes back to when the box closes: the control that opened
  // it, or the section's own `+ note` verb when a selection opened it.
  pop._returnTo = (document.activeElement && document.activeElement !== document.body)
    ? document.activeElement : el('rcmtnote-' + id);
  pop.innerHTML =
      '<div class="cmt-pop-row" role="group" aria-label="Comment type">'
    +   '<button type="button" class="cmt-chip cmt-chip-changes is-on" data-type="changes" aria-pressed="true">request changes</button>'
    +   '<button type="button" class="cmt-chip cmt-chip-info" data-type="info" aria-pressed="false">need info</button>'
    +   (canSuggest ? '<button type="button" class="cmt-chip cmt-chip-suggestion" data-type="suggestion" aria-pressed="false">suggest wording</button>' : '')
    + '</div>'
    + (anchor ? '<div class="cmt-pop-quote">' + esc(anchor.text) + '</div>' : '')
    + '<textarea class="note-field cmt-pop-note" aria-label="Comment" placeholder="Describe the change or question…"></textarea>'
    + '<div class="thumb-strip" style="display:none" aria-live="polite"></div>'
    + '<button type="button" class="attach-btn">attach image</button>'
    + (voiceSupported()
        ? '<button type="button" class="mic-btn">dictate</button>'
        : '')
    + '<input type="file" accept="image/*" multiple style="display:none">'
    + '<div class="cmt-pop-row"><button type="button" class="cmt-save">save</button>'
    +   '<button type="button" class="cmt-cancel">cancel</button></div>';
  // The popover composes a MARGIN note, so it opens in the margin: anchored,
  // beside its passage; unanchored, in the foot band's margin, appended
  // where the saved note itself lands (`.rm-notes`) — composer and note
  // share one `.rm`. Moved before focus, since relocating a focused node
  // blurs it.
  const row = anchor ? rowForAnchor(id, anchor.text, anchor.occurrence) : null;
  const foot = docFootRow(id);
  const host = row ? docNoteHost(id, row) : (foot && docCell(foot, 'rm'));
  if (host && pop.parentElement !== host) host.appendChild(pop);
  pop.style.display = '';
  pop.classList.add('is-open');
  // Opening the box makes the margin non-empty; closing it may let the
  // column collapse again. No `.doc.no-margin` twin needed for the composer
  // — this runs synchronously before paint, so `.is-open` has already
  // dropped `no-margin` by the time the textarea lays out.
  updateDocColumns();

  const ta        = pop.querySelector('.cmt-pop-note');
  const strip     = pop.querySelector('.thumb-strip');
  const attachBtn = pop.querySelector('.attach-btn');
  const fileInput = pop.querySelector('input[type="file"]');
  wireCapture(() => captureState, ta, strip, attachBtn, fileInput, el('rcard-' + id));

  /* One field, whose JOB changes with the type:
       changes / info  → the box is the note
       suggestion      → the box is the replacement wording, applied verbatim
     Typed text carries across the switch — a described change is usually a
     draft of its own replacement wording. A suggestion's rationale is
     optional by schema (`_comment_fragment`). */
  const PLACEHOLDERS = {
    changes:    'Describe the change or question…',
    info:       'Describe the change or question…',
    suggestion: 'Replacement wording — applied verbatim',
  };
  pop.querySelectorAll('.cmt-chip').forEach(ch => ch.addEventListener('click', () => {
    pop.dataset.type = ch.dataset.type;
    pop.querySelectorAll('.cmt-chip').forEach(c => {
      c.classList.toggle('is-on', c === ch);
      c.setAttribute('aria-pressed', String(c === ch));
    });
    ta.placeholder = PLACEHOLDERS[pop.dataset.type] || PLACEHOLDERS.changes;
    ta.focus();
  }));
  // Opening with a type is the same act as picking its chip — driven through
  // the chip so the dataset, the `is-on` mark and the placeholder can never
  // disagree with each other about what the box means.
  if (type) {
    const chip = pop.querySelector('.cmt-chip[data-type="' + type + '"]');
    if (chip) chip.click();
  }
  // `nearest`, not `center`: a tall popover aligns to its top (where the
  // chips are); a short one doesn't move the page at all. `preventScroll` on
  // focus, or the browser's own scroll-to-field lands the viewport past the
  // chips.
  ta.focus({ preventScroll: true });
  revealWithinBars(pop);
  pop.querySelector('.cmt-save').addEventListener('click', () => {
    const text = ta.value.trim();
    // A suggestion ships on its wording: the same box the other types use for
    // a note carries the replacement the author applies verbatim.
    const isSuggestion = pop.dataset.type === 'suggestion';
    if (!text) {
      const why = isSuggestion ? 'a suggestion needs replacement wording'
                               : 'a comment needs a note';
      // Said, not only shown: the placeholder swap alone was silent to a
      // screen reader and vanished on the first keystroke.
      ta.placeholder = why;
      ta.setAttribute('aria-invalid', 'true');
      ta.addEventListener('input', () => ta.removeAttribute('aria-invalid'), { once: true });
      announce(why);
      ta.focus();
      return;
    }
    addComment(id, { type: pop.dataset.type,
                     note: isSuggestion ? '' : text,
                     anchor: anchor || undefined,
                     replacement: isSuggestion ? text : undefined,
                     images: captureState.images?.length ? captureState.images : undefined });
    closeCommentPopover(id);
  });
  pop.querySelector('.cmt-cancel').addEventListener('click', () => closeCommentPopover(id));
  // Dictation just fills this box — save is still the only thing that makes
  // a comment. The button turns the mic on and returns focus; a focused note
  // field is what the modal voice rule keys on.
  const mic = pop.querySelector('.mic-btn');
  if (mic) {
    // Built after paintVoiceToggle last ran, so it paints its own live state.
    mic.classList.toggle('is-live', voiceIsOn());
    mic.addEventListener('click', () => startVoice(() => ta.focus()));
  }
}

/* Scroll a node clear of the fixed bottom bar and sticky masthead, by the
   smallest amount that does it. Chrome ignores scroll-padding for
   scrollIntoView under smooth scrolling, so a plain scrollBy reads and
   applies the paddings itself. */
function revealWithinBars(node) {
  const r = node.getBoundingClientRect();
  const cs = getComputedStyle(document.documentElement);
  const padTop = parseFloat(cs.scrollPaddingTop) || 0;
  const padBottom = parseFloat(cs.scrollPaddingBottom) || 0;
  let dy = 0;
  if (r.bottom > innerHeight - padBottom) dy = r.bottom - (innerHeight - padBottom);
  if (r.top - dy < padTop) dy = r.top - padTop;   // never push the top under the masthead
  if (dy) scrollBy({ top: dy, behavior: SMOOTH });
}

function closeCommentPopover(id) {
  const pop = el('rpop-' + id);
  // Focus goes back to what opened the box. Wiping innerHTML with focus
  // inside it dropped focus to <body>, and the next Tab restarted the page.
  const back = pop && pop._returnTo && document.contains(pop._returnTo)
    ? pop._returnTo : el('rcmtnote-' + id);
  if (pop) { pop.style.display = 'none'; pop.innerHTML = ''; pop.classList.remove('is-open'); pop._returnTo = null; }
  updateDocColumns();
  if (back && back.focus) back.focus({ preventScroll: true });
}

/* `markAndPin` is the single owner of both ends of the anchor/pin pairing —
   two separate marking passes could disagree about which span is note 3. */

// Wraps the `n`th (0-based) text-node occurrence of `needle` in a
// <mark class=cls>, per the anchor's own `occurrence`. A needle split across
// element boundaries matches nothing here (visual only — stored offset is
// unaffected). Returns the mark it created, or null.
function wrapNth(root, needle, cls, n) {
  if (!needle) return null;
  // Prose only: a margin note echoes the wording it annotates, so an
  // unfiltered walk could count — and mark — inside the commentary.
  const walk = proseWalker(root);
  let node, seen = 0;
  while ((node = walk.nextNode())) {
    let i = node.nodeValue.indexOf(needle);
    while (i >= 0) {
      if (seen === n) {
        const after = node.splitText(i);
        after.splitText(needle.length);
        const mark = document.createElement('mark');
        mark.className = cls;
        mark.textContent = after.nodeValue;
        after.replaceWith(mark);
        return mark;
      }

      seen++;
      i = node.nodeValue.indexOf(needle, i + 1);
    }
  }
  // Nothing matched inside a single text node — common for a CODE anchor:
  // highlight.js splits tokens across spans, so the phrase lives in no one
  // node. Same for prose crossing an inline <code>/<em>. Fall through to a
  // Range, which can span elements.
  return wrapSpanning(root, needle, cls, n);
}

// Wraps the nth occurrence of `needle` even across element boundaries. Kept
// as the FALLBACK, not primary: `surroundContents` splits partially-selected
// elements, which is only worth it when the alternative is no mark at all.
function wrapSpanning(root, needle, cls, n) {
  const walk = proseWalker(root);
  const nodes = [], starts = [];
  let text = '', node;
  while ((node = walk.nextNode())) { starts.push(text.length); nodes.push(node); text += node.nodeValue; }
  if (!nodes.length) return null;
  const at = nthIndexOf(text, needle, n > 0 ? n : 0);
  if (at < 0) return null;
  const locate = pos => {
    for (let i = nodes.length - 1; i >= 0; i--)
      if (starts[i] <= pos) return [nodes[i], pos - starts[i]];
    return [nodes[0], 0];
  };
  const [sn, so] = locate(at);
  const [en, eo] = locate(at + needle.length);
  const range = document.createRange();
  try { range.setStart(sn, so); range.setEnd(en, eo); } catch (e) { return null; }
  const mark = document.createElement('mark');
  mark.className = cls;
  try { range.surroundContents(mark); }
  catch (e) {
    /* Partially-selected elements: extract (splits them, each half keeping
       its own class) and re-insert under the mark. A PLACEHOLDER text node
       holds the spot — `extractContents` can collapse the range's start
       boundary up to its parent, so a plain `insertNode(mark)` would land
       the mark as a sibling of where the text came from instead. */
    try {
      const slot = document.createTextNode('');
      range.insertNode(slot);
      range.setStartAfter(slot);
      mark.appendChild(range.extractContents());
      slot.parentNode.replaceChild(mark, slot);
    } catch (e2) { return null; }
  }
  return mark;
}

/* ─── Open notes (issue #16) — settle by cid, recorded as a comment so the
   submit carries it to open_notes.py which closes the thread. ─── */
/* A reply continues the SAME cid thread (flagged `reply`, unsettled), so
   open_notes.update appends it and the agent answers again next round. An
   emptied reply clears the pending one; replies count as active feedback
   (blocking approval) but stay out of the new-comment list. */
function replyToThread(id, cid) {
  const field = el('rreply-' + cid);
  const wrap = field ? field.closest('.thread-reply') : null;
  const type = (wrap && wrap.dataset.type) || 'info';   // info = keep discussing; changes = escalate to an edit
  const note = (field ? field.value : '').trim();
  const cs = commentsOf(id);
  let c = cs.find(x => x.cid === cid);
  if (!note) {
    if (c && c.reply) rState.verdicts[id].comments = cs.filter(x => x !== c);
    syncCard(id);
    return;
  }
  if (!c) { c = { cid }; cs.push(c); }
  Object.assign(c, { type, note, open: true, settled: false, reply: true });
  syncCard(id);
}

function settleOpenNotes(id, cid) {
  const cs = commentsOf(id);
  let c = cs.find(x => x.cid === cid);
  if (!c) { c = { cid, type: 'info', note: '', open: true, settled: true }; cs.push(c); }
  else { c.settled = !c.settled; c.reply = false; }
  const thread = el('rthread-' + cid);
  const btn = el('rsettle-' + cid);
  if (thread) thread.classList.toggle('is-settled', !!c.settled);
  // The button's own label stays put — it names the verb (`Settle`, or
  // `Accept` on a declined thread) and carries a keycap, so rewriting its
  // innerHTML to report state would delete both. State is the lit class plus
  // the thread dimming, which is what the reviewer actually reads.
  if (btn) btn.classList.toggle('is-on', !!c.settled);
  syncCard(id);
}

function syncReviewDot(id) {
  const verdict  = deriveVerdict(id);
  const isActive = rState.active === id;
  const dot = el('rdot-' + id);
  if (!dot) return;
  dot.className = 'dot ' + (
    verdict === 'approved' ? 'dot-approved' :
    verdict === 'changes'  ? 'dot-changes'  :
    verdict === 'info'     ? 'dot-info'     :
    isActive               ? 'dot-active'   : 'dot-idle'
  );
}

function syncNoteInline(id) {
  const verdict = deriveVerdict(id);
  const note    = rState.verdicts[id]?.note || '';
  const inlineEl = el('rnote-inline-' + id);
  if (!inlineEl) return;
  const show = note && verdict && verdict !== 'approved' && rState.active !== id;
  inlineEl.style.display = show ? '' : 'none';
  if (show) { inlineEl.textContent = note; inlineEl.title = note; }
}

function updateReviewStats() {
  const sections = REVIEW_DATA.sections;
  const approved    = sections.filter(s => deriveVerdict(s.id) === 'approved').length;
  const withFeedback= sections.filter(s => ['changes','info'].includes(deriveVerdict(s.id))).length;
  const total    = sections.length;
  const reviewed = approved + withFeedback;
  const remaining= total - reviewed;

  el('r-progress').style.width = (reviewed / total * 100) + '%';
  // The cell is LABELLED `approved` (and DESIGN.md specifies `approved N/M`),
  // so it prints APPROVED. It printed `reviewed`, which counts sections
  // carrying feedback too — which is how the bar could read `approved 8 / 8`
  // on a round where three sections had open changes.
  el('r-progress-label').textContent = `${approved} / ${total}`;
  el('stat-pending').textContent = remaining > 0 ? `${remaining} unreviewed` : 'all reviewed';

  const sub = el('btn-submit');
  // ONE consequential stamp, named for what it does to the document rather
  // than for the HTTP verb behind it. Blocked, it says what is blocking; the
  // count comes from the same item arithmetic the bar prints, so the two can
  // never disagree.
  const openItems = documentBalance().open;
  sub.className = remaining === 0 && reviewed > 0 ? 'btn-submit ready' : 'btn-submit disabled';
  // Announce the deadness as well as draw it. NOT the `disabled` ATTRIBUTE —
  // that one already means IN FLIGHT (submitReview sets it, sendSubmit's
  // failure path and the boot fetch clear it), and openRecap's readiness
  // mirror reads it; overloading it here would re-enable a not-ready button
  // on any submit failure.
  sub.setAttribute('aria-disabled', sub.classList.contains('disabled') ? 'true' : 'false');
  // The stamp names its action and nothing else: `#stat-pending` beside it
  // already carries the blocking count, and restating it here uppercased the
  // same number a second time in the same bar.
  sub.textContent = 'approve — dispatch';
  const cap = ' <kbd>&#8984;&#9166;</kbd>';
  el('stat-pending').innerHTML = remaining > 0
    ? `blocked &middot; ${remaining} unreviewed`
    : ((openItems ? `${openItems} open` : 'ready') + cap);

  reviewFootSeg(sections, total);
  renderDocStatus();
}

/* ─── The document's condition, in items ──────────────────────
   The bar and the footer state one quantity between them — how many items
   this document holds and how many are still open — so the two can never
   disagree. An ITEM is what sectionBalance already counts: a thread, a
   comment, an unanswered check, and a section's own sign-off. A producer flag
   is advisory and is NOT an item — see sectionBalance. The vocabulary is
   stated to the reader in the `kbd-legend` disclosure, because a reviewer who
   cannot reproduce the arithmetic stops trusting it. */
function documentBalance() {
  let judgment = 0, facts = 0, settled = 0, atStart = 0, checks = 0, checksDone = 0;
  let signoff = 0;
  // Which sections arrived already signed off. The baseline reads the round as
  // ARMED, so it asks `approved_ids` — the static field the round shipped with —
  // never the live verdict, which is the thing convergence measures against it.
  const armedApproved = new Set(REVIEW_DATA.approved_ids || []);
  (REVIEW_DATA.sections || []).forEach(s => {
    const b = sectionBalance(s);
    judgment += b.judgment; facts += b.facts; settled += b.settled; signoff += b.signoff;
    // What was open when the round was ARMED: every carried thread arrives
    // unsettled, every unanswered check and flag arrives open, and every
    // section not in `approved_ids` arrives owing a sign-off. Nothing here
    // reads live reviewer state — that is what makes it a baseline to measure
    // convergence against rather than a second view of the same number.
    atStart += (s.open_notes || []).length;
    // Both ends of the arrow count the sign-off or the arrow lies: `open` now
    // includes every pending one, so a baseline that skipped them would read
    // `convergence 0 → 8` on a round where the reviewer has done nothing yet.
    if (!armedApproved.has(s.id)) atStart++;
    (s.annotations || []).forEach(a => {
      if (!a) return;
      // Only a CHECK is an annotation this baseline counts. A doc-scope flag IS
      // counted in `checks`/`checksDone` — that pair is the bar's document-level
      // `checks D/T` readout, and a fact about the document is exactly what
      // belongs in it. It is NOT counted in `atStart`, and the asymmetry is
      // load-bearing rather than sloppy: `atStart` is the LEFT of the
      // convergence arrow and `open` is the RIGHT, but `open` is a sum of
      // `sectionBalance`, which skips doc-scope. Count it on one side only and
      // a document carrying five unanswered `headings-present` flags and
      // nothing else reads `convergence 5 → 0` on a round where nothing was
      // closed. The two ends of the arrow answer the same question or the
      // arrow lies — which is also why a plain warn/error producer flag is
      // counted at NEITHER end: `sectionBalance` stopped treating an advisory
      // flag as an open item, so a baseline that still counted one would make
      // every flagged round appear to converge by exactly its flag count.
      if (CHECK_KINDS.includes(a.kind)) {
        checks++;
        if (a.result) checksDone++;
        else if (!DOC_SCOPE_KINDS.includes(a.kind)) atStart++;
      }
    });
  });
  return { judgment, facts, settled, checks, checksDone, atStart, signoff,
           open: judgment + facts + signoff,
           total: judgment + facts + settled + signoff };
}

// The last same-origin round trip this page actually measured. A real number
// or nothing — the footer never prints a latency it did not observe.
let _lastRTT = null;

function timedFetch(url, opts) {
  const t0 = performance.now();
  return fetch(url, opts).then(r => {
    _lastRTT = Math.round(performance.now() - t0);
    return r;
  });
}

// The bar's own cells, plus the footer's convergence and latency.
function renderDocStatus() {
  const b = documentBalance();
  el('r-checks').textContent = b.checksDone + '/' + b.checks
    + (b.checks && b.checksDone === b.checks ? ' ✓' : '');
  el('tb-checks').style.display = b.checks ? '' : 'none';
  el('r-items').innerHTML = b.total + ' item' + (b.total === 1 ? '' : 's')
    + ' &middot; <b>' + b.open + '</b> open';
  // Hover convenience only — `title` is not keyboard-reachable and most screen
  // readers do not announce it on a non-interactive div. The `kbd-legend`
  // term list is what actually states this vocabulary.
  el('r-items').title = 'an item is a thread, a comment, an unanswered check, '
    + 'or a section sign-off; open = judgment + facts';
  el('tb-items').style.display = '';
  el('tb-palette').style.display = '';
  // Convergence: open items when the round was armed against open items now.
  // The question a multi-round review actually asks — is the reviewer closing
  // more than they open — with both ends counted, never estimated.
  const conv = el('stat-conv');
  // Printed once the arrow has somewhere to point: on a fresh round both ends
  // are the same number, and a round compared with itself teaches nothing.
  conv.style.display = b.open !== b.atStart ? '' : 'none';
  conv.innerHTML = 'convergence ' + b.atStart + ' &rarr; <b>' + b.open + '</b>';
  conv.title = 'open items when this round was armed → open items now';
  const lat = el('stat-lat');
  // A measured number, and only one worth acting on — a local server's 11 ms
  // is machine trivia in the reviewer's bar.
  if (_lastRTT === null || _lastRTT < SLOW_RTT_MS) lat.style.display = 'none';
  else { lat.style.display = ''; lat.textContent = 'round trip ' + _lastRTT + ' ms'; }
}

/* The whole round's balance, across the footer that closes the page. Same
   grammar and same fixed order as a section's rule, one denominator: every
   section, or every question. What the bar does NOT fill is what nobody has
   looked at yet — the bare track — which is the one honest way to draw "not
   yet decided" without inventing a fourth color.

   Counts in, not sections: an interview has no judgment/facts axis (an answer
   is given or it is not), and asking this function to know that would put a
   mode branch inside the one thing both footers share. */
function renderFootSeg(counts, total, label) {
  const bar = el('foot-seg'); if (!bar) return;
  if (!total) { bar.style.display = 'none'; bar.innerHTML = ''; return; }
  const pct = n => (n / total * 100).toFixed(2) + '%';
  const seg = (cls, n) => n ? '<i class="' + cls + '" style="width:' + pct(n) + '"></i>' : '';
  bar.style.display = '';
  bar.setAttribute('role', 'img');
  bar.setAttribute('aria-label', label);
  bar.innerHTML = seg('seg-judgment', counts.judgment) + seg('seg-fact', counts.facts)
                + seg('seg-settled', counts.settled);
}

// The review page's own tally, in the vocabulary its sections carry.
function reviewFootSeg(sections, total) {
  let judgment = 0, facts = 0, settled = 0;
  sections.forEach(s => {
    const v = deriveVerdict(s.id);
    if (v === 'approved') settled++;
    else if (v === 'changes') judgment++;
    else if (v === 'info') facts++;
  });
  renderFootSeg({ judgment, facts, settled }, total,
    'document balance: ' + judgment + ' judgment, ' + facts + ' fact'
    + (facts === 1 ? '' : 's') + ', ' + settled + ' settled of ' + total + ' sections');
}

