/* ═════════════════════════════════════════════════════════════
   THE DOCUMENT PRINT — doc + margin (issue #186)
   ─────────────────────────────────────────────────────────────
   Review mode's renderer: sections print open in document order as
   `gutter | prose | margin` rows, commentary beside its passage. Diff mode
   keeps the accordion (a hunk has no margin). CHECK_KINDS is injected from
   schema.py to avoid drift. ═════════════════ */
const CHECK_KINDS = __CHECK_KINDS__;
/* DOC_SCOPE_KINDS is injected for the same anti-drift reason. Different axis
   from CHECK_KINDS: that asks "does this gate a checks round", this asks
   "what is this flag ABOUT" (headings-present is in both; unregistered
   fails open as section-scope). */
const DOC_SCOPE_KINDS = __DOC_SCOPE_KINDS__;
/* Thread-status label map, injected for the same reason: a hand-kept copy
   here would drift from scripts/schema.py's Revision History wording, and a
   reviewer would read two vocabularies for the same status. */
const THREAD_STATUS_LABELS = __THREAD_STATUS_LABELS__;

/* ─── The seam: the grammar is not the print ─────────────────
   THE GRAMMAR (margin notes, glyph rail, segmented rule) belongs to anything
   that renders a section. CONTINUOUS PRINT (every section open, settled ones
   dimming in place) is review's alone. `.doc` arms the grammar, `.print`
   arms continuous print — review stamps both, diff/Q&A only the first. No
   `usesMargin()` predicate exists: it would be true at every call site. */
function isContinuousPrint() { return !!(REVIEW_DATA && REVIEW_DATA.mode === 'review'); }

/* ─── Flags: which column a producer flag belongs in ─────────
   The 70px gutter is for a glance (severity glyph + short message). A flag
   carrying an interactive jump (cross-section link, preference badge link)
   isn't a glance, so it routes to the margin via annotStripHTML instead. */
function docFlagSplit(section) {
  const titles = reviewSectionTitles();
  const gutter = [], margin = [], doc = [], decisions = [];
  (section.annotations || []).forEach(a => {
    if (!a) return;
    // A document fact, not a flag on this passage. Producers anchor these to
    // the first card (their only document-level handle), which used to paint
    // five amber lines in section 1's margin. Goes to the document slip instead.
    if (DOC_SCOPE_KINDS.includes(a.kind)) { doc.push(a); return; }
    // A confidence annotation is the agent's self-report about the whole
    // section (drives the triage sort; rendered in the spec table) — not a
    // passage flag, so it's skipped here rather than holding the gutter open.
    if (a.kind === 'confidence') return;
    // A decision (#211) carries no anchor — the answer behind the section,
    // not a passage flag — so it's words in the foot margin, not a glyph, and
    // its own bucket so several fold into one block (decisionFoldHTML).
    if (a.kind === 'decision') { decisions.push(a); return; }
    const anchorId = a.anchor != null ? String(a.anchor) : '';
    const m = a.kind === 'preference' ? PREF_ID_RE.exec(a.message || '') : null;
    const jumps = (anchorId && titles.has(anchorId)) || !!(m && PREFS_BY_ID.get(m[1]));
    (jumps ? margin : gutter).push(a);
  });
  return { gutter, margin, doc, decisions };
}

/* A section's decisions have no row to sit beside, so several stacked at the
   foot would push an empty band into the prose column. Two or more fold into
   one "N decisions" disclosure; a lone decision still reads open. */
function decisionFoldHTML(decisions) {
  if (decisions.length < 2) return annotStripHTML(decisions);
  return '<details class="decision-fold"><summary>' + decisions.length
       + ' decisions</summary>' + annotStripHTML(decisions) + '</details>';
}

const FLAG_GLYPH = { info: '&#10003;', warn: '&#9651;', error: '&#10007;' };

function flagSeverity(a) {
  return ANNOT_SEVERITIES[a.severity] ? a.severity : 'info';
}

// The rail glyph: locality and severity, nothing else. aria-hidden, because
// the margin line below carries the same flag in words.
function gutterGlyphHTML(a) {
  const sev = flagSeverity(a);
  const full = [a.kind || 'note', a.message || '', a.result ? '→ ' + a.result : '']
    .filter(Boolean).join(' · ');
  return '<span class="lflag lflag-' + sev + '" title="' + esc(full) + '" aria-hidden="true">'
    + FLAG_GLYPH[sev] + '</span>';
}

// The same flag in words, in the margin of its own row — where a 300px column
// can hold `✓ §4 defines "cold start"` without clamping it to `✓ §4 defines
// "cold`, which is what 70px of 9px type did to it.
function marginFlagHTML(a) {
  const sev = flagSeverity(a);
  return '<div class="mflag mflag-' + sev + '" title="' + esc(a.kind || 'note') + '">'
    + '<span class="g" aria-hidden="true">' + FLAG_GLYPH[sev] + '</span>'
    + '<span>' + esc(a.message || '')
    + (a.result ? '<span class="r">&rarr; ' + esc(a.result) + '</span>' : '')
    + '</span></div>';
}

/* One decision, printed once: a duplicate `result` across flags is dropped
   after the first (message still prints). Annotation is COPIED not mutated —
   specHTML/sectionBalance/documentBalance all count `a.result`. `seen` is
   per-call so placeDocFlags stays idempotent on re-sync. */
function dedupeResults(list, seen) {
  return list.map(a => {
    const r = a && a.result;
    if (!r) return a;
    if (seen.has(r)) return Object.assign({}, a, { result: undefined });
    seen.add(r);
    return a;
  });
}

/* ─── Rows ───────────────────────────────────────────────────
   Each top-level markdown block becomes one prose row, so a note can sit
   beside its paragraph rather than the whole section. Code/tables take a
   `wide` row instead. */
function docRow(wide) {
  const row = document.createElement('div');
  row.className = 'row' + (wide ? ' wide' : '');
  const rp = document.createElement('div');
  rp.className = 'rp';
  row.appendChild(rp);
  return row;
}

function layoutDocRows(id) {
  const host = el('rcontent-' + id); if (!host) return;
  if (host.querySelector(':scope > .row')) return;          // already laid out
  // marked/DOMPurify missing → renderMarkdown wrote raw text, no elements.
  // One row keeps the raw fallback inside the grid instead of outside it.
  if (!host.firstElementChild) {
    if (!host.textContent) return;
    const row = docRow(false);
    row.querySelector('.rp').textContent = host.textContent;
    host.textContent = '';
    host.appendChild(row);
    return;
  }
  // The head row already prints the section title; markdown's own leading
  // heading is the same words twice, so it's removed here (the accordion's
  // `.section-content > h1:first-child` CSS hid it instead).
  const first = host.firstElementChild;
  if (first && /^H[1-3]$/.test(first.tagName)) first.remove();
  Array.from(host.children).forEach(node => {
    const wide = node.tagName === 'PRE' || node.tagName === 'TABLE'
              || node.classList.contains('table-wrap') || node.classList.contains('d2h-wrapper');
    const row = docRow(wide);
    host.appendChild(row);
    row.querySelector('.rp').appendChild(node);
  });
}

function docRows(id) {
  const host = el('rcontent-' + id);
  return host ? Array.from(host.querySelectorAll(':scope > .row')) : [];
}

/* The section's foot band: static markup from both builders, a pure query
   (like docHeadRow) so nothing here creates DOM in a render loop.
   `buildCarriedCard` builds no bands, so a carried reveal yields null. */
function docFootRow(id) {
  const sec = el('rcard-' + id);
  return sec ? sec.querySelector('.row-foot') : null;
}

// The row whose prose holds the given occurrence of `text`. Counts
// occurrences across rows in document order, matching the ordinal
// renderHighlights marks. Null when nothing matches — never misplaced.
function rowForAnchor(id, text, occurrence) {
  const t = String(text || '').trim();
  if (!t) return null;
  let n = occurrence > 0 ? occurrence : 0;
  const rows = docRows(id);
  for (const r of rows) {
    const hay = (r.querySelector('.rp') || {}).textContent || '';
    let c = 0, i = hay.indexOf(t);
    while (i >= 0) { c++; i = hay.indexOf(t, i + 1); }
    if (c > n) return r;
    n -= c;
  }
  return rows.find(r => (((r.querySelector('.rp') || {}).textContent) || '').includes(t)) || null;
}

// Side cells are created on demand, never pre-reserved: a row that gains a
// note grows a margin cell; one that never has one carries no empty box.
// Column width is a separate decision (updateDocColumns).
function docCell(row, cls) {
  let cell = row.querySelector(':scope > .' + cls);
  if (!cell) {
    cell = document.createElement('div');
    cell.className = cls;
    if (cls === 'rg') row.insertBefore(cell, row.firstChild);
    else row.appendChild(cell);
  }
  return cell;
}

/* Where a note hangs: a resolved anchor hangs in its row's margin, beside
   its passage. Unanchored/unresolved notes hang at the section's FOOT
   instead — never the head, which isn't an introduction to a whole section. */
function docNoteHost(id, row) {
  const target = row || docFootRow(id);
  if (!target) return null;
  const rm = docCell(target, 'rm');
  let host = rm.querySelector(':scope > .rm-notes');
  if (!host) {
    host = document.createElement('div');
    host.className = 'rm-notes';
    rm.appendChild(host);
  }
  return host;
}

/* ─── Notes: what the margin holds ───────────────────────────
   Two sources kept apart: a carried THREAD is built once and placed once (it
   owns a reply textarea — rebuilding mid-keystroke would steal focus).
   This round's COMMENTS are static text, rebuilt freely on every sync. */
function docNotes(section) {
  const id = section.id;
  const threads = section.open_notes || [];
  const cs = (rState.verdicts[id] || {}).comments || [];
  const out = threads.map(t => ({
    kind: 'thread', cid: t.cid, thread: t,
    comment: cs.find(c => c.cid === t.cid) || null,
    anchor: t.quote ? { text: t.quote, occurrence: 0 } : null,
  }));
  activeComments(id)
    .filter(c => !c.reply && !threads.some(t => t.cid === c.cid))
    .forEach(c => out.push({ kind: 'comment', cid: c.cid, comment: c, anchor: c.anchor || null }));
  return out;
}

// Notes in reading order: by the row their anchor lands in, then order made.
// Unanchored notes sort to the END (`rows.length`), matching where they
// render — the foot band, never the head. An anchor resolving to no row
// degrades the same way: it lands at the foot with its quote echo, no pin.
function docNotesOrdered(section) {
  const rows = docRows(section.id);
  return docNotes(section)
    .map((n, i) => {
      const r = n.anchor ? rowForAnchor(section.id, n.anchor.text, n.anchor.occurrence) : null;
      return Object.assign({}, n, { row: r ? rows.indexOf(r) : rows.length, seq: i });
    })
    .sort((a, b) => a.row - b.row || a.seq - b.seq);
}

function noteTypeOf(n) {
  if (n.kind === 'thread') {
    const last = (n.thread.exchanges || []).slice(-1)[0] || {};
    return last.verdict === 'changes' || last.verdict === 'suggestion' ? last.verdict : 'info';
  }
  return n.comment.type === 'changes' || n.comment.type === 'suggestion' ? n.comment.type : 'info';
}

// The exact wording a note proposes, from either source: this round's
// suggestion comment, or a carried thread whose last turn was one.
function noteReplacement(n) {
  if (n.kind === 'comment') return n.comment.replacement || '';
  const last = (n.thread.exchanges || []).slice(-1)[0] || {};
  return last.verdict === 'suggestion' ? (last.replacement || '') : '';
}

/* D's fence, squared: the reviewer's replacement against the wording it
   replaces. Red and green live here and nowhere else — the fence and the
   diff are the same object, and diff semantics already own those colors. */
function suggestionFenceHTML(c) {
  const was = (c.anchor || {}).text || '';
  return '<div class="fence"><div class="fence-h">suggestion &middot; ' + esc(c.cid) + '</div>'
    + (was ? '<div class="fence-ln fence-del"><span class="fence-g" aria-hidden="true">&minus;</span>'
           + '<span class="fence-tx">' + esc(was) + '</span></div>' : '')
    + '<div class="fence-ln fence-add"><span class="fence-g" aria-hidden="true">+</span>'
    + '<span class="fence-tx">' + esc(c.replacement) + '</span></div></div>';
}

function commentNoteHTML(n) {
  const c = n.comment;
  const word = c.type === 'suggestion' ? 'suggestion' : c.type === 'info' ? 'question' : 'comment';
  const cls = c.type === 'info' ? ' nt-fact' : '';
  /* The fence is for a suggestion the prose couldn't show applied (code, or
     an unresolved anchor). When markAndPin DID splice it inline, the note
     says so instead of printing the same two strings twice. */
  const showsInline = !!(c.replacement && n.placedInline);
  return '<div class="nt' + cls + '" data-cid="' + esc(c.cid) + '">'
    + '<div class="nh"><span class="nh-num">' + n.num + '</span> you &mdash; ' + word
    + '<span class="pn">&middot; ' + esc(c.cid) + '</span></div>'
    + (c.anchor && c.anchor.text && c.type !== 'suggestion'
        ? '<span class="nt-quote">' + esc(c.anchor.text) + '</span>' : '')
    + (c.note ? '<div class="nt-body">' + esc(c.note) + '</div>' : '')
    + (showsInline ? '<div class="nt-applied">applied above &mdash; struck wording out, '
                   + 'replacement on yellow</div>' : '')
    + (c.replacement && !showsInline ? suggestionFenceHTML(c) : '')
    + '<div class="nt-acts">'
    +   '<button type="button" class="nt-btn is-quiet cmt-del" data-cid="' + esc(c.cid) + '">remove</button>'
    + '</div></div>';
}

/* ─── The state run ──────────────────────────────────────────
   The transmittal slip's successor at section scale, and the foot band's
   answer to "what is open here" — stated as a spec, not described. */
function sectionSpec(section) {
  const id = section.id;
  const threads = section.open_notes || [];
  const cs = (rState.verdicts[id] || {}).comments || [];
  const isSettled = cid => cs.some(c => c.cid === cid && c.settled);
  let comments = 0, suggestions = 0, declined = 0;
  threads.forEach(t => {
    if (isSettled(t.cid)) return;
    if (t.status === 'declined') { declined++; return; }
    const last = (t.exchanges || []).slice(-1)[0] || {};
    if (last.verdict === 'suggestion') suggestions++; else comments++;
  });
  activeComments(id).filter(c => !c.reply && !threads.some(t => t.cid === c.cid))
    .forEach(c => { if (c.type === 'suggestion') suggestions++; else comments++; });
  // A doc-scope check is a fact about the document, and its readout is the
  // document slip's own `checks D/T` tally — not this section's state.
  const checks = (section.annotations || []).filter(
    a => a && CHECK_KINDS.includes(a.kind) && !DOC_SCOPE_KINDS.includes(a.kind));
  return { comments, suggestions, declined,
           checks: checks.length, checksDone: checks.filter(a => a.result).length };
}

function specHTML(section) {
  const s = sectionSpec(section);
  // Nothing open and nothing checked: no state run at all. Keeps the FOOT
  // band's height independent of which section is live (renderDocSpec), and
  // leaves section 1's band as bare verbs when its only flags are doc-scope.
  const conf0 = confidenceAnnot(section);
  if (!s.comments && !s.suggestions && !s.declined && !s.checks && !conf0) return '';
  // A RUN, not a table: `.doc-apparatus`'s `role="group"`/`aria-label` names the band.
  const item = (label, value, open, title) =>
    '<span class="sp' + (open ? ' sp-open' : '') + '"' +
    (title ? ' title="' + esc(title) + '"' : '') + '>'
    + '<span class="sp-k">' + label + '</span> <span class="sp-v">' + value + '</span></span>';
  // The agent's own confidence is a state item, not a gutter flag — it's
  // what the triage sort orders on. `docFlagSplit` sends it to neither
  // column because this is its readout; drop it here and it goes invisible.
  const conf = confidenceAnnot(section);
  // Each count prints only when nonzero — three zeros were the run's usual
  // content on a typed round. A decline is OPEN judgment (sectionBalance
  // agrees), so it takes the open ink like the other two.
  return (s.comments ? item('comments open', s.comments, true) : '')
    + (s.suggestions ? item('suggestions open', s.suggestions, true) : '')
    + (s.declined ? item(THREAD_STATUS_LABELS.declined, s.declined, true) : '')
    + (s.checks ? item('checks', s.checksDone + '/' + s.checks
        + (s.checksDone === s.checks ? ' &#10003;' : ''), s.checksDone < s.checks) : '')
    + (conf ? item('agent confidence',
        [conf.basis, conf.level].filter(Boolean).map(esc).join(' &middot; ') || esc(conf.message || '—'),
        conf.level === 'low', conf.source) : '')
    + evidenceBtnHTML(conf);
}

// #106 — `path`, `path:N`, or `path:A-B` at the START of a `source` string;
// trailing free text ("&mdash; CACHE_TTL = 300") is ignored. Mirrors
// server.py's `_EVIDENCE_REF_RE` — the two must agree on what is servable.
const EVIDENCE_REF_RE = /^([\w./-]+\.\w+)(?::(\d+)(?:-(\d+))?)?/;
function evidenceRef(source) {
  const m = source ? EVIDENCE_REF_RE.exec(String(source).trim()) : null;
  return m ? m[0] : null;
}

// A confidence row with no `source`, or one that doesn't parse as a ref,
// renders no button — absent `source` is unchanged from before #106/#145.
function evidenceBtnHTML(conf) {
  const ref = conf ? evidenceRef(conf.source) : null;
  if (!ref) return '';
  return '<button type="button" class="ev-btn" data-ref="' + esc(ref)
    + '" title="show cited lines">evidence</button>';
}

/* ─── Segmented rule ─────────────────────────────────────────
   Every open item is JUDGMENT (reviewer's call) or a FACT (question,
   unanswered check, producer flag); resolved is SETTLED. Fixed order is a
   colorblind-safe second encoding; raw counts ride the aria-label. */
function sectionBalance(section) {
  const id = section.id;
  const threads = section.open_notes || [];
  const cs = (rState.verdicts[id] || {}).comments || [];
  const isSettled = cid => cs.some(c => c.cid === cid && c.settled);
  let judgment = 0, facts = 0, settled = 0;
  threads.forEach(t => {
    if (isSettled(t.cid)) { settled++; return; }
    const last = (t.exchanges || []).slice(-1)[0] || {};
    if (t.status === 'declined' || last.verdict === 'changes' || last.verdict === 'suggestion') judgment++;
    else facts++;
  });
  activeComments(id).filter(c => !c.reply && !threads.some(t => t.cid === c.cid))
    .forEach(c => { if (c.type === 'changes' || c.type === 'suggestion') judgment++; else facts++; });
  (section.annotations || []).forEach(a => {
    if (!a) return;
    // A DOCUMENT fact is not this section's item. Placed ABOVE the CHECK_KINDS
    // branch on purpose: `headings-present` is doc-scope AND a check kind, so
    // this stops five document facts being counted as section-1 items.
    // An answered doc-scope check contributes no `settled` here — it's
    // counted once, in documentBalance's own checks/checksDone pair.
    if (DOC_SCOPE_KINDS.includes(a.kind)) return;
    if (CHECK_KINDS.includes(a.kind)) { if (a.result) settled++; else facts++; return; }
    // A PLAIN producer flag is not an item: `.mflag` is advisory, nothing
    // the reviewer does closes it. Counting it as open painted a section with
    // one warn flag as a 100%-wide amber bar even with every check answered.
  });
  /* The section's own sign-off is an item in BOTH states — otherwise a fresh
     round with unanswered checks would print `0 items · 0 open` when the
     legend defines it as nonzero. It rides as its own field, not folded into
     `judgment`: `documentBalance` counts a pending sign-off as open, but
     `segHTML` (judgment/facts/settled only) should not paint it — a section
     whose only open item is its sign-off still draws the settled hairline. */
  const signoff = deriveVerdict(id) === 'approved' ? 0 : 1;
  if (!signoff) settled++;
  return { judgment, facts, settled, signoff };
}

function segHTML(bal) {
  const total = bal.judgment + bal.facts + bal.settled;
  if (!total) return '';
  // Nothing open: the thin settled hairline. A state bar on a settled
  // section is decoration, and decoration is what this ground removed.
  if (!bal.judgment && !bal.facts) return '<div class="rule-s"></div>';
  const pct = n => (n / total * 100).toFixed(2) + '%';
  const seg = (cls, n) => n ? '<i class="' + cls + '" style="width:' + pct(n) + '"></i>' : '';
  const label = 'open: ' + bal.judgment + ' judgment, ' + bal.facts + ' fact'
    + (bal.facts === 1 ? '' : 's') + '; ' + bal.settled + ' settled';
  return '<div class="seg" role="img" aria-label="' + esc(label) + '">'
    + seg('seg-judgment', bal.judgment) + seg('seg-fact', bal.facts)
    + seg('seg-settled', bal.settled) + '</div>';
}

/* Single-track head row: heading, number, summary, segmented rule, collapsed
   diff — no margin cell (finding 01: margin is for a note beside its
   passage, not section state). Verbs live in the foot band
   (`docFootRowHTML`). */
function docHeadRowHTML(id, proseHTML) {
  return '<div class="row row-head"><div class="rp">' + proseHTML + '</div></div>';
}

/* Foot band: what the head row's margin used to hold, laid out horizontally
   under the section. Static markup from both builders, never created on
   demand, which keeps `docFootRow` a pure query. Must stay a SIBLING of
   `.section-content`, not a child, or `docRows`/`rowForAnchor`/
   `docNotesOrdered`/`markAndPin`/`proseWalker` would have to filter it out
   of the document walk (#95). Verbs lead, state trails
   (`.spec-strip{order:2}`); `skip` is the accordion's alone — the print has
   nothing to skip TO. */
function docFootRowHTML(id, title, opts) {
  const skip = !!(opts && opts.skip);
  return '<div class="row row-foot">'
    + '<div class="rp">'
    +   '<div class="doc-apparatus" role="group" aria-label="'
    +     esc(title) + ' &mdash; state and actions">'
    +     '<div class="spec-strip" id="rspecbody-' + id + '"></div>'
    +     '<div class="nt-acts doc-acts">'
    +       '<button type="button" class="nt-btn is-pri" id="rbtn-primary-' + id + '">'
    +         '<span aria-hidden="true">&#10003;</span> approve<kbd>a</kbd></button>'
    +       '<button type="button" class="nt-btn is-quiet" id="rcmtnote-' + id + '">+ note</button>'
    +       (skip ? '<button type="button" class="nt-btn is-quiet" id="rbtn-skip-' + id + '">'
                  + '<span aria-hidden="true">&#8595;</span> skip</button>' : '')
    +     '</div>'
    +   '</div>'
    + '</div>'
    + '</div>';
}

/* Approve must stay focusable by pointer and Tab — with no action row, a
   note-less section would otherwise have no focusable element at all
   (test_server_a11y). ⌘K is a second path to the same verb, never the only
   one. `root` addresses by id, so the head/foot split is invisible here. */
function wireDocSection(root, id) {
  root.querySelector('#rbtn-primary-' + id).addEventListener('click', e => {
    e.stopPropagation();
    if (deriveVerdict(id) === 'approved') docWithdraw(id); else approveSection(id);
  });
  root.querySelector('#rcmtnote-' + id).addEventListener('click', e => {
    e.stopPropagation(); openCommentPopover(id, {});
  });
  const skip = root.querySelector('#rbtn-skip-' + id);
  if (skip) skip.addEventListener('click', e => { e.stopPropagation(); skipReviewCard(id); });

  const diffToggle = root.querySelector('#rdiff-toggle-' + id);
  if (diffToggle) {
    // Ships collapsed. "What changed since last round" is not what the reader
    // opened the document to read, and at full width above the prose it was
    // the single largest thing between them and the text.
    root.querySelector('#rdiff-' + id).classList.add('collapsed');
    diffToggle.addEventListener('click', e => {
      e.stopPropagation();
      root.querySelector('#rdiff-' + id).classList.toggle('collapsed');
    });
  }

  // A pin is a jump to its own note — the pairing works in both directions.
  root.addEventListener('click', e => {
    const pin = e.target.closest ? e.target.closest('.pin') : null;
    if (!pin) return;
    e.stopPropagation();
    const note = root.querySelector('[data-cid="' + pin.dataset.cid + '"]');
    if (note) {
      note.scrollIntoView({ behavior: SMOOTH, block: 'nearest' });
      // The note's first verb, not its reply box — the box ships hidden now,
      // and focusing a hidden field silently drops the focus on the floor.
      const target = note.querySelector('.nt-btn, textarea:not([hidden])');
      if (target) target.focus({ preventScroll: true });
    }
  });
}

/* ─── Build ──────────────────────────────────────────────────
   The section element keeps the `rcard-` id the rest of the app addresses
   sections by, so activateReviewCard, advanceFrom, the transmittal jumps
   and the Tab handler all keep working against it unchanged. */
function buildDocSection(section, index) {
  const id = section.id;
  const sec = document.createElement('section');
  sec.className = 'doc-section';
  sec.id = 'rcard-' + id;
  sec.setAttribute('aria-labelledby', 'rhead-' + id);

  _pendingMarkdown.set(id, section.content ?? '');

  sec.innerHTML = docHeadRowHTML(id, `
        <h2 class="doc-head" id="rhead-${id}"><span class="doc-num" aria-hidden="true">${index + 1} &middot;</span> ${esc(section.title)}</h2>
        ${section.summary ? `<div class="section-summary">${esc(section.summary)}</div>` : ''}
        <div id="rseg-${id}"></div>
        ${diffStripHTML(id, section.diff)}`) + `
    <div class="section-content" id="rcontent-${id}"></div>`
    + docFootRowHTML(id, section.title) + `
    <div class="comment-popover" id="rpop-${id}" style="display:none"></div>`;

  // The live section follows the reader without scrolling — jump paths
  // (transmittal rows, pins, palette) still scroll. Print-only: in the
  // accordion the disclosure button makes a section live instead.
  sec.addEventListener('mousedown', () => activateReviewCard(id, { noScroll: true }));
  sec.addEventListener('focusin',   () => activateReviewCard(id, { noScroll: true }));

  wireDocSection(sec, id);
  return sec;
}

// Withdraw in continuous print: nothing was ever collapsed, so this is just
// the verdict reverting to pending — the prose stays put.
function docWithdraw(id) {
  if (rState.verdicts[id]) rState.verdicts[id].verdict = undefined;
  syncReviewCard(id);
  updateReviewStats();
  renderTransmittal();
}

/* ─── Place: flags, threads, notes, pins ─────────────────────
   Called once per section, then surgically on every sync. Idempotent — a
   thread already in the right cell is left alone, since moving its DOM node
   would blur a focused reply textarea. */
function placeDocFlags(id) {
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  const split = docFlagSplit(section);
  const byRow = new Map();
  // Guards against a future section-scope CHECK_KIND — today `docFlagSplit`
  // routes doc-scope kinds to the slip (`docSlipHTML`), and `headings-present`
  // is the only CHECK_KIND, so this loop sees none yet.
  const seenResults = new Set();
  split.gutter.forEach(a => {
    const row = a.anchor != null ? rowForAnchor(id, String(a.anchor), 0) : null;
    const key = row || docFootRow(id);
    if (!key) return;
    if (!byRow.has(key)) byRow.set(key, []);
    byRow.get(key).push(a);
  });
  // Glyph in the rail, words in the margin, both on the row the flag concerns.
  byRow.forEach((flags, row) => {
    docCell(row, 'rg').innerHTML = flags.map(gutterGlyphHTML).join('');
    // AFTER the rail, deliberately: the glyph's `title` also carries
    // `→ result`, but a tooltip appears one at a time and is not a wall.
    flags = dedupeResults(flags, seenResults);
    const rm = docCell(row, 'rm');
    let host = rm.querySelector(':scope > .rm-flags');
    if (!host) {
      host = document.createElement('div');
      host.className = 'rm-flags';
      // Above the threads and this round's notes: the machine's reading of the
      // paragraph comes before the conversation about it.
      rm.insertBefore(host, rm.firstChild);
    }
    host.innerHTML = flags.map(marginFlagHTML).join('');
  });
  if (split.margin.length || split.decisions.length) {
    const host = docNoteHost(id, null);
    // Idempotent: _ensureRendered can run twice on the md-raw path (eager
    // loop, then activateReviewCard) without clearing _pendingMarkdown, so
    // without this guard the strip would stack twice.
    if (host && !host.querySelector(':scope > .annot-strip, :scope > .decision-fold')) {
      host.insertAdjacentHTML('afterbegin',
        annotStripHTML(split.margin) + decisionFoldHTML(split.decisions));
      host.querySelectorAll('.annot-jump').forEach(btn => {
        btn.addEventListener('click', e => {
          e.stopPropagation();
          const prefId = btn.getAttribute('data-pref-id');
          if (prefId) openPrefsPanel(btn, prefId);
          else activateReviewCard(btn.getAttribute('data-target'));
        });
      });
    }
  }
}

function placeDocThreads(id) {
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  const threads = section.open_notes || [];
  if (!threads.length) return;
  const sec = el('rcard-' + id); if (!sec) return;
  threads.forEach(t => {
    let node = el('rthread-' + t.cid);
    if (!node) {
      const holder = document.createElement('div');
      holder.innerHTML = openThreadItemHTML(t);
      node = holder.firstElementChild;
      wireOpenThread(id, node);
      // A rebuild (the late-load retry replaces the container's innerHTML,
      // threads included) must not lose a reply the reviewer already typed —
      // the text lives in rState, so put it back in the box.
      const pending = ((rState.verdicts[id] || {}).comments || [])
        .find(c => c.cid === t.cid && c.reply && c.note);
      if (pending) {
        const field = node.querySelector('.thread-reply-field');
        if (field) field.value = pending.note;
      }
    }
    const row = t.quote ? rowForAnchor(id, t.quote, 0) : null;
    // Same guard as `placeDocFlags`/`docNoteHost`: an unanchored thread falls
    // back to the section's foot band, which a carried reveal doesn't have.
    // All three fallbacks must move together.
    const host = row || docFootRow(id);
    if (!host) return;
    const rm = docCell(host, 'rm');
    let threadHost = rm.querySelector(':scope > .rm-threads');
    if (!threadHost) {
      threadHost = document.createElement('div');
      threadHost.className = 'rm-threads';
      // Threads precede this round's fresh notes: a carried thread is older
      // business than a comment made a minute ago.
      //
      // With the head row's static `<div class="rm-notes">` gone, the foot
      // band's `.rm` starts empty and this query returns null on the first
      // call — `insertBefore(node, null)` appends, and `docNoteHost` then
      // creates `.rm-notes` after it. Flags → threads → notes still comes out
      // in the documented order, but it comes out that way from
      // `_ensureRendered`'s call order rather than from this line. Do not
      // "simplify" the insertBefore to an append: a rebuild that places a
      // thread AFTER notes already in the cell would reverse them.
      rm.insertBefore(threadHost, rm.querySelector(':scope > .rm-notes'));
    }
    if (node.parentElement !== threadHost) threadHost.appendChild(node);
  });
}

// What a reply MEANS, in one place: `info` keeps the discussion going,
// `changes` turns it into an edit. The chips and the reveal verbs both set it
// here so they can never disagree about which one is lit.
function setThreadReplyType(wrap, type) {
  wrap.dataset.type = type;
  wrap.querySelectorAll('.cmt-chip').forEach(c => {
    const on = c.dataset.type === type;
    c.classList.toggle('is-on', on);
    c.setAttribute('aria-pressed', String(on));
  });
}

// The settle button + reply box wiring, lifted out of buildReviewCard so both
// surfaces bind one thread the same way. `node` is a scope, not one thread:
// the accordion passes its whole card, the margin passes a single thread.
function wireOpenThread(id, node) {
  node.querySelectorAll('.settle-btn').forEach(b =>
    b.addEventListener('click', e => { e.stopPropagation(); settleOpenNotes(id, b.dataset.cid); }));
  // `Reply` / `Change anyway` reveal the box and set what a reply MEANS:
  // insisting on a declined thread is an edit request, never a chat turn.
  node.querySelectorAll('.thread-reply-btn').forEach(b =>
    b.addEventListener('click', e => {
      e.stopPropagation();
      const wrap = node.querySelector('.thread-reply[data-cid="' + b.dataset.cid + '"]');
      if (!wrap) return;
      wrap.hidden = false;
      node.querySelectorAll('.thread-reply-btn[data-cid="' + b.dataset.cid + '"]')
        .forEach(x => x.setAttribute('aria-expanded', 'true'));
      setThreadReplyType(wrap, b.dataset.type);
      const field = wrap.querySelector('.thread-reply-field');
      if (field) field.focus({ preventScroll: true });
    }));
  node.querySelectorAll('.thread-reply').forEach(wrap => {
    const cid = wrap.dataset.cid;
    // A reply already in rState (a rebuild, or a resumed round) keeps its box
    // open — hiding it would hide feedback the reviewer has already given.
    const pending = ((rState.verdicts[id] || {}).comments || [])
      .find(c => c.cid === cid && c.reply && c.note);
    if (pending) {
      wrap.hidden = false;
      const f = wrap.querySelector('.thread-reply-field');
      if (f && !f.value) f.value = pending.note;
    }
    wrap.querySelectorAll('.cmt-chip').forEach(ch => ch.addEventListener('click', e => {
      e.stopPropagation();
      setThreadReplyType(wrap, ch.dataset.type);
      replyToThread(id, cid);   // re-tag any pending reply with the new type
    }));
    const field = wrap.querySelector('.thread-reply-field');
    field.addEventListener('input', () => replyToThread(id, cid));
    field.addEventListener('click', e => e.stopPropagation());
  });
}

// Mark every note's anchor and pin it with the note's own number. One pass
// owns both ends of the pairing, so the number in the text and the number in
// the margin can never disagree.
function markAndPin(id, ordered) {
  const content = el('rcontent-' + id); if (!content) return;
  content.querySelectorAll('.pin').forEach(p => p.remove());
  // A `.sug` unwraps back to the wording it replaced — the `del` half IS the
  // document; the `ins` half is the reviewer's proposal and was never in it.
  content.querySelectorAll('span.sug').forEach(s => {
    const was = s.querySelector('del');
    s.replaceWith(document.createTextNode(was ? was.textContent : ''));
  });
  content.querySelectorAll('mark[class^="cmt-hl-"]').forEach(m =>
    m.replaceWith(document.createTextNode(m.textContent)));
  content.normalize();
  ordered.forEach(n => {
    const a = n.anchor;
    if (!a || !a.text) return;
    const type = noteTypeOf(n);
    const mark = wrapNth(content, a.text, 'cmt-hl-' + type, a.occurrence > 0 ? a.occurrence : 0);
    if (!mark) return;
    // A rendered diff line is a code well too (`.d2h-code-line-ctn`, not
    // `<pre>`): a spliced del/ins pair reads as neither version. The −/+
    // fence in the margin carries a diff suggestion instead.
    n.inCode = !!(mark.closest && mark.closest('pre, .d2h-code-line'));
    const repl = noteReplacement(n);
    let tail = mark;
    /* A suggestion is shown APPLIED in the prose — original struck,
       replacement inserted — except in code, where a spliced del/ins reads
       as broken syntax; there the −/+ fence in the margin carries it
       instead. `n.inCode` decides which. */
    n.placedInline = !!(repl && !n.inCode);
    if (n.placedInline) {
      const sug = document.createElement('span');
      sug.className = 'sug';
      const was = document.createElement('del');
      was.className = 'sug-del';
      was.textContent = mark.textContent;
      const now = document.createElement('ins');
      now.className = 'sug-ins';
      now.textContent = repl;
      sug.append(was, now);          // no text between them — the gap is CSS,
      mark.replaceWith(sug);         // so it can never be counted as prose
      tail = now;
    }
    const declined = n.kind === 'thread' && n.thread.status === 'declined';
    const pin = document.createElement('button');
    pin.type = 'button';
    pin.className = 'pin ' + (declined ? 'pin-author' : type === 'info' ? 'pin-fact' : 'pin-you');
    pin.dataset.cid = n.cid;
    pin.textContent = String(n.num);
    pin.setAttribute('aria-label', 'Go to note ' + n.num);
    // On a diff line the pin leads the line instead of trailing the anchor
    // (`.pin-line`), since only the pin needs to survive a horizontal scroll.
    // Anchored to the LINE (`.d2h-code-line`), not `.d2h-code-line-ctn` —
    // that span doesn't exist on every line kind.
    const line = tail.closest && tail.closest('.d2h-code-line');
    if (line) { pin.classList.add('pin-line'); line.prepend(pin); }
    else tail.after(pin);
  });
}

function renderDocMargin(id) {
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  const sec = el('rcard-' + id); if (!sec) return;
  // Only the dynamic hosts are wiped. Thread notes and their reply textareas
  // are never rebuilt, since moving a focused reply textarea would blur it.
  sec.querySelectorAll('.rm-notes .nt').forEach(n => n.remove());

  const ordered = docNotesOrdered(section);
  ordered.forEach((n, i) => { n.num = i + 1; });
  // Marking runs FIRST. It is what decides whether a suggestion could be shown
  // applied in the prose (`placedInline`) or has to fall back to the margin's
  // −/+ fence, and the note is written from that answer.
  markAndPin(id, ordered);
  ordered.forEach(n => {
    if (n.kind === 'thread') {
      const numEl = el('rnum-' + n.cid);
      if (numEl) numEl.textContent = String(n.num);
      const node = el('rthread-' + n.cid);
      if (!node) return;
      node.classList.toggle('is-settled', !!(n.comment && n.comment.settled));
      // A carried suggestion the prose couldn't show applied (a code anchor)
      // gets the −/+ fence, once — printing it again in prose would repeat
      // both strings.
      const repl = noteReplacement(n);
      if (repl && n.inCode && !node.querySelector('.fence')) {
        const body = node.querySelector('.open-thread-body');
        if (body) body.insertAdjacentHTML('beforeend', suggestionFenceHTML({
          cid: n.cid, replacement: repl, anchor: n.anchor }));
      }
      return;
    }
    // An unanchored note carries `row === rows.length`, so the index read is
    // out of range and yields undefined; `|| null` states that rather than
    // leaning on `undefined || footRow` inside docNoteHost.
    const host = docNoteHost(id, docRows(id)[n.row] || null);
    if (host) host.insertAdjacentHTML('beforeend', commentNoteHTML(n));
  });
  sec.querySelectorAll('.rm-notes .cmt-del').forEach(b =>
    b.addEventListener('click', e => { e.stopPropagation(); removeComment(id, b.dataset.cid); }));

  renderDocSeg(id);
  renderDocSpec(id);
  updateDocColumns();
}

function renderDocSeg(id) {
  const mount = el('rseg-' + id); if (!mount) return;
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  mount.innerHTML = segHTML(sectionBalance(section));
}

/* Drawn for every section with something to state, not only the live one —
   gating on `rState.active` would move the state readout on activation,
   shifting layout under the cursor. The live section is marked at its
   heading instead (border + negative margin), which costs no layout. */
function renderDocSpec(id) {
  const mount = el('rspecbody-' + id); if (!mount) return;
  const section = REVIEW_DATA.sections.find(s => s.id === id); if (!section) return;
  mount.innerHTML = specHTML(section);
}

/* The wasted-space rule, decided once for the whole round — reads off
   REVIEW_DATA/rState, not the DOM, because the accordion renders a section's
   rows only when opened; a DOM read would jog every hunk sideways as the
   reviewer navigates. `docNotes` reads rState, so a fresh comment counts the
   same as one that shipped with the round. */
function updateDocColumns() {
  const doc = el('review-cards');
  if (!doc || !doc.classList.contains('doc')) return;
  const sections = (REVIEW_DATA && REVIEW_DATA.sections) || [];
  const gutter = sections.some(s => docFlagSplit(s).gutter.length);
  // An open compose popover holds the margin open like a saved note does —
  // without it, the first anchored comment on a bare doc mounts its textarea
  // into a 0px track. `.is-open` is checked rather than the inline style,
  // which is the browser's business, not a selector's.
  //
  // `split.gutter` is deliberately NOT counted: a gutter flag with no
  // resolved row still lands in the foot band's margin, which is what the
  // `.doc.no-margin .row-foot` CSS twin covers instead of widening this check.
  const margin = sections.some(s => {
    const split = docFlagSplit(s);
    return split.margin.length || split.decisions.length || docNotes(s).length;
  })
    || !!doc.querySelector('.rm .comment-popover.is-open');
  doc.classList.toggle('no-gutter', !gutter);
  // The print never collapses its margin — an empty margin is still the
  // measure, so the prose stays at a fixed width instead of rewrapping the
  // moment the first composer opens. The accordion still collapses.
  doc.classList.toggle('no-margin', !margin && !isContinuousPrint());
}

// Open/close a card, keeping the header button's aria-expanded in sync.
// `is-active` is the single source of truth for "expanded", so every site that
// flips it routes through here — otherwise aria-expanded desyncs on auto-advance
// or programmatic activation and lies to screen readers.
function setCardExpanded(cardEl, expanded) {
  if (!cardEl) return;
  cardEl.classList.toggle('is-active', expanded);
  const head = cardEl.querySelector('.card-head');
  if (head) head.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  // A body clipped to 0fr is still in the tab order unless it is inert — a
  // keyboard reader walked nine invisible stops per closed card. Focus that
  // was inside moves to the head rather than being dropped on the floor.
  const wrap = cardEl.querySelector('.card-body-wrap');
  if (wrap) {
    if (!expanded && wrap.contains(document.activeElement) && head) head.focus({ preventScroll: true });
    wrap.inert = !expanded;
  }
}

// `opts.noScroll` marks a passive activation (pointer/tab in continuous
// print) — the live section follows without the page moving. Every explicit
// jump (transmittal row, pin, palette, annotation link) omits it and scrolls.
function activateReviewCard(id, opts) {
  // A carried card has no accordion body to activate — reveal its read-only
  // content and scroll to it instead (annotation jumps, all-carried resumes).
  // It never becomes rState.active: active means "under review".
  const target = el('rcard-' + id);
  if (target && target.classList.contains('is-carried')) {
    setCarriedShown(id, true);
    target.scrollIntoView({ behavior: SMOOTH, block: 'nearest' });
    return;
  }
  const prev = rState.active;
  // Deactivate previous
  if (prev && prev !== id) {
    setCardExpanded(el('rcard-' + prev), false);
    syncReviewDot(prev);
    syncNoteInline(prev);
  }
  rState.active = id;
  _ensureRendered(id);
  const card = el('rcard-' + id);
  if (card) {
    setCardExpanded(card, true);
    if (!(opts && opts.noScroll)) card.scrollIntoView({ behavior: SMOOTH, block: 'nearest' });
  }
  syncReviewDot(id);
}

function _ensureRendered(id) {
  if (!_pendingMarkdown.has(id)) return;
  const contentEl = el('rcontent-' + id);
  if (!contentEl) return;
  const raw = _pendingMarkdown.get(id);
  // Diff mode's hunk content (a fenced ```diff block) renders via diff2html
  // (renderDiffHunk). Binary-change sections have no fence (parse_diff.py's
  // plaintext sentinel) and fall through to renderMarkdown unchanged.
  const isDiffHunk = REVIEW_DATA && REVIEW_DATA.mode === 'diff' && /^```diff\n/.test(raw);
  const rendered = isDiffHunk ? renderDiffHunk(contentEl, raw, sectionTitleFor(id)) : renderMarkdown(contentEl, raw);
  /* A CARRIED card is read-only: no `.row-head`/`.row-foot` (buildCarriedCard
     never builds either band). Running the normal pipeline over it grids a
     body nobody can comment on, and an unanchored carried thread would take
     `placeDocThreads` down `docCell(null, 'rm')`. */
  const card = el('rcard-' + id);
  const carried = !!(card && card.classList.contains('is-carried'));
  if (!rendered) {
    // marked/DOMPurify haven't landed yet: content is raw text, not blocks.
    // The doc print still grids it as one row so a renderer-less boot still
    // reads as a document. The retry re-renders in place once the scripts
    // arrive.
    if (!carried) { layoutDocRows(id); placeDocFlags(id); placeDocThreads(id); renderDocMargin(id); }
    return;
  }
  // A d2h-pending card rendered as fenced markdown but is waiting on
  // diff2html — keep its source so the load listener below can re-render it;
  // deleting it here would strand the card on the fallback view forever.
  if (!contentEl.classList.contains('d2h-pending')) _pendingMarkdown.delete(id);
  if (carried) return;
  // The doc grid distributes the freshly rendered blocks into rows before
  // anything is placed beside them — a note cannot find its paragraph until
  // the paragraph is a row.
  layoutDocRows(id);
  placeDocFlags(id);
  placeDocThreads(id);
  renderDocMargin(id);
}

// One-time-per-script retry for late-loading renderers: a card opened before
// its renderer's deps landed stays tagged .md-raw or .d2h-pending. Re-render
// every card with that marker once the script(s) land — attaching to every
// script means load order never matters. Scoped to the marker class so
// cards not yet opened stay lazily unrendered.
function retryOnceScriptsLoad(scriptIds, selector) {
  const retry = () => {
    document.querySelectorAll(selector).forEach(contentEl => {
      const m = contentEl.id.match(/^rcontent-(.+)$/);
      if (m) _ensureRendered(m[1]);
    });
  };
  scriptIds.forEach(scriptId => {
    const script = el(scriptId);
    if (script) script.addEventListener('load', retry, { once: true });
  });
}
retryOnceScriptsLoad(['marked-script', 'dompurify-script'], '.section-content.md-raw');
// The three d2h assets get their retry at injection (loadDiff2html), when the
// elements exist; attaching here would silently no-op on a null element.

function skipReviewCard(id) {
  setCardExpanded(el('rcard-' + id), false);
  rState.active = null;
  syncReviewDot(id);
  const sections = REVIEW_DATA.sections;
  const idx = sections.findIndex(s => s.id === id);
  const rest = [...sections.slice(idx + 1), ...sections.slice(0, idx)];
  const next = rest.find(s => !rState.verdicts[s.id]?.verdict);
  if (next) setTimeout(() => {
    activateReviewCard(next.id);
    // Tab advanced the section; focus advances with it, or the reader is
    // left focused on a control in the section they just left.
    el('rbtn-primary-' + next.id)?.focus({ preventScroll: true });
  }, 80);
}

function toggleReviewCard(id) {
  if (rState.active === id) {
    setCardExpanded(el('rcard-' + id), false);
    rState.active = null;
    syncReviewDot(id);
    syncNoteInline(id);
  } else {
    activateReviewCard(id);
  }
}

// Advance past a just-decided card: close it, add is-approved CSS, auto-advance
// to the next unreviewed card. Does NOT call sync/stats — caller handles that.
function advanceFrom(id) {
  setCardExpanded(el('rcard-' + id), false);
  el('rcard-' + id)?.classList.add('is-approved');
  rState.active = null;
  const sections = REVIEW_DATA.sections;
  const idx = sections.findIndex(s => s.id === id);
  const next = sections.slice(idx + 1).find(s => deriveVerdict(s.id) !== 'approved');
  if (next) setTimeout(() => activateReviewCard(next.id), 80);
}

// Approve = sign off this section. A section with comments cannot approve; the
// primary button only reads "approve" when comments.length === 0.
function approveSection(id) {
  if (activeComments(id).length) {          // guarded by label AND said aloud
    announce('approve is refused while comments are open — settle or remove them first');
    return;
  }
  (rState.verdicts[id] ||= {}).skip = false;
  rState.verdicts[id].verdict = 'approved';
  advanceFrom(id);
  syncReviewCard(id);
  updateReviewStats();
}

/* `c` / `i` open the composer with that type pre-picked. Neither key writes
   a verdict directly — verdict is always DERIVED from `activeComments`
   (#156), so it always carries the note the revise loop acts on. Cancel or
   remove the saved comment to un-derive it. */
function openTypedComment(id, type) {
  const pop = el('rpop-' + id);
  // Already composing: switch the type on the open box. Re-opening rewrites
  // its innerHTML, which would discard a half-typed note and any image
  // already attached. The chip is the one selection path, so drive the chip.
  if (pop && pop.classList.contains('is-open')) {
    const chip = pop.querySelector('.cmt-chip[data-type="' + type + '"]');
    if (chip) chip.click();
    return;
  }
  // A section that already carries comments gets ANOTHER one, not a re-open of
  // the last: a section owns a list (#68), and each comment is its own thread.
  openCommentPopover(id, { type });
}

function syncReviewCard(id) {
  const verdict = rState.verdicts[id]?.verdict || null;

  // Approved dimming
  el('rcard-' + id)?.classList.toggle('is-approved', verdict === 'approved');

  // Dot
  syncReviewDot(id);

  // Badge
  const badge = el('rbadge-' + id);
  if (badge) {
    if (verdict === 'approved') { badge.style.display=''; badge.className='vbadge vbadge-approved'; badge.textContent='approved'; }
    else if (verdict === 'changes') { badge.style.display=''; badge.className='vbadge vbadge-changes'; badge.textContent='changes'; }
    else if (verdict === 'info')    { badge.style.display=''; badge.className='vbadge vbadge-info';    badge.textContent='info'; }
    else badge.style.display = 'none';
  }

  // Primary button
  renderPrimaryButton(id);

  // A verdict feeds both the section's balance (an approval is a settled
  // item) and its spec — neither repaints from the comment path, so approve
  // must call both directly.
  renderDocSeg(id); renderDocSpec(id);

  syncNoteInline(id);
}

