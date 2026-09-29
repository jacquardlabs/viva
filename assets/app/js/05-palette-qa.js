/* ═════════════════════════════════════════════════════════════
   COMMAND PALETTE (⌘K, issue #186)
   A directory of the keyboard layer, never a second interaction model:
   every verb listed here is one the page also carries as a control or a
   keycap. Built from live state on each open, so "approve section 9" names
   the section actually under the reader. ═══════════════════════════════ */
/* The interview's own directory. Every verb here is one the Q&A page also
   carries as a control or a keycap — the choices by their digits, confirm by
   `c`, skip by its button — which is the rule the palette exists under: a
   directory of the keyboard layer, never a second interaction model. */
function qaPaletteCommands() {
  const cmds = [];
  const live = qState.active ? QA_DATA.questions.find(q => q.id === qState.active) : null;
  if (live) {
    const n = QA_DATA.questions.indexOf(live) + 1;
    // Every choice, not the first nine. The digit handler binds 1-9 (one
    // keypress, `parseInt(e.key)`), so a tenth choice carries no keycap — but
    // truncating the list here left it with no place in the directory at all,
    // which is the palette lying about being the directory of the keyboard
    // layer. A digit-less row still runs from ⌘K, and the chip itself is a
    // plain <button> that Tab already reaches — so nothing here needs a `0` or
    // a letter bound to it, and binding one would collide the day a second
    // modifier-free letter shortcut lands on this page.
    live.choices.forEach((c, i) => {
      cmds.push({ label: 'Answer ' + n + ' — ' + c, key: i < 9 ? String(i + 1) : '',
                  run: () => pickQAChoice(live.id, c) });
    });
    if (qaAnswered(live.id)) {
      cmds.push({ label: 'Confirm question ' + n, key: 'c', run: () => advanceQA(live.id) });
    }
    cmds.push({ label: 'Skip question ' + n + ' for now', key: '⇥', run: () => advanceQA(live.id) });
  }
  const next = QA_DATA.questions.find(q => !qaAnswered(q.id)
                                        && q.id !== qState.active);
  if (next) cmds.push({ label: 'Jump to next unanswered', key: 'j',
                        run: () => activateQACard(next.id) });
  if (voiceSupported()) cmds.push(voicePaletteCommand());
  cmds.push({ label: 'Cycle theme', key: 't', run: () => cycleTheme() });
  return cmds;
}

function reviewPaletteCommands() {
  const cmds = [];
  const live = rState.active ? REVIEW_DATA.sections.find(s => s.id === rState.active) : null;
  if (live && deriveVerdict(live.id) !== 'approved' && !activeComments(live.id).length) {
    const n = REVIEW_DATA.sections.indexOf(live) + 1;
    cmds.push({ label: 'Approve section ' + n + ' — ' + live.title, key: '⏎',
                run: () => approveSection(live.id) });
  }
  const unblocked = REVIEW_DATA.sections.filter(s =>
    deriveVerdict(s.id) === 'pending' && !activeComments(s.id).length);
  if (unblocked.length) {
    cmds.push({ label: 'Approve all unblocked (' + unblocked.length + ')', key: '⇧⏎',
                run: () => { unblocked.forEach(s => approveSection(s.id)); } });
  }
  const openThread = nextOpenThread();
  if (openThread) cmds.push({ label: 'Jump to next open thread', key: 'j',
                              run: () => activateReviewCard(openThread) });
  // Listed only while there is a ledger to open — a verb that cannot act
  // has no place in the directory.
  if (el('ledger').style.display !== 'none') cmds.push({ label: 'Open revision ledger', key: 'l', run: openLedger });
  cmds.push({ label: 'Open recap and submit', key: 'o', run: () => openRecap() });
  if (voiceSupported()) cmds.push(voicePaletteCommand());
  cmds.push({ label: 'Cycle theme', key: 't', run: () => cycleTheme() });
  return cmds;
}

// One entry, both directories — the palette is a directory of the keyboard
// layer, and `v` means the same thing on the review page and in the interview.
function voicePaletteCommand() {
  return { label: voiceIsOn() ? 'Stop listening' : 'Start the oral examination (voice)',
           key: 'v', run: () => toggleVoice() };
}

// The next section carrying live business, from the live section forward and
// wrapping — `open` and `declined` are both unresolved; only settled closes.
function nextOpenThread() {
  const secs = REVIEW_DATA.sections;
  const start = Math.max(0, secs.findIndex(s => s.id === rState.active)) + 1;
  const order = [...secs.slice(start), ...secs.slice(0, start)];
  const live = s => {
    const cs = (rState.verdicts[s.id] || {}).comments || [];
    return (s.open_notes || []).some(t => !cs.some(c => c.cid === t.cid && c.settled))
        || activeComments(s.id).length > 0;
  };
  const hit = order.find(live);
  return hit ? hit.id : null;
}

let _palCmds = [];
let _palIdx = 0;
let _palReturnTo = null;

function paletteIsOpen() { return el('pal-overlay').style.display !== 'none'; }

function openPalette() {
  if ((!REVIEW_DATA && !QA_DATA) || paletteIsOpen()) return;
  _palReturnTo = document.activeElement;
  el('pal-overlay').style.display = '';
  el('pal-input').value = '';
  renderPalette('');
  // Modal in fact as well as in aria: the page behind the scrim is inert
  // while it is open, and focus goes back where it came from on close.
  setBackgroundInert(true);
  el('pal-input').focus();
}

function closePalette() {
  el('pal-overlay').style.display = 'none';
  el('pal-list').innerHTML = '';
  _palCmds = [];
  setBackgroundInert(false);
  const back = _palReturnTo; _palReturnTo = null;
  if (back && back !== document.body && document.contains(back)) back.focus({ preventScroll: true });
}

function renderPalette(query) {
  const q = String(query || '').trim().toLowerCase();
  _palCmds = (REVIEW_DATA ? reviewPaletteCommands() : qaPaletteCommands())
    .filter(c => !q || c.label.toLowerCase().includes(q));
  _palIdx = 0;
  const list = el('pal-list');
  if (!_palCmds.length) { list.innerHTML = '<div class="pal-empty">no matching command</div>'; return; }
  // Options are addressed through the combobox (`aria-activedescendant`) and
  // take no tab stop of their own — Tab-then-Enter used to run row 0.
  list.innerHTML = _palCmds.map((c, i) =>
    '<button type="button" class="pal-row' + (i === 0 ? ' is-on' : '') + '" role="option" tabindex="-1"'
    + ' id="pal-row-' + i + '" aria-selected="' + (i === 0) + '" data-i="' + i + '">'
    + '<span>' + esc(c.label) + '</span><span class="k">' + esc(c.key) + '</span></button>').join('');
  el('pal-input').setAttribute('aria-activedescendant', 'pal-row-0');
  list.querySelectorAll('.pal-row').forEach(b =>
    b.addEventListener('click', () => runPalette(+b.dataset.i)));
}

function movePalette(delta) {
  const rows = el('pal-list').querySelectorAll('.pal-row');
  if (!rows.length) return;
  _palIdx = (_palIdx + delta + rows.length) % rows.length;
  rows.forEach((r, i) => {
    r.classList.toggle('is-on', i === _palIdx);
    r.setAttribute('aria-selected', String(i === _palIdx));
  });
  el('pal-input').setAttribute('aria-activedescendant', rows[_palIdx].id);
  rows[_palIdx].scrollIntoView({ block: 'nearest' });
}

function runPalette(i) {
  const cmd = _palCmds[i];
  closePalette();
  if (cmd) cmd.run();
}

/* ─────────────────────────────────────────────────────────
   Q&A MODE — build once, update surgically
───────────────────────────────────────────────────────── */
/* ─── Q&A on the catalog ──────────────────────────────────────
   A question is not a document section, but it holds one the same way: a
   thing to read and decide, with the machine's advice and the reviewer's own
   move beside it rather than stacked on top of it. So Q&A takes the GRAMMAR —
   `gutter | prose | margin` rows, the note grammar, per-note verbs, the
   composite's bar and footer — and not the PRINT: one question at a time is
   the point of an interview, and the accordion is what makes that true.

   The prose column is the question, numbered like a catalog entry, with its
   choices under it as chips carrying the digit that picks them. The margin is
   the machine's hint and the reviewer's own note with its attachments. The
   gutter is empty — a question carries no producer flags — so it collapses
   for good; the margin never does, because the verbs live there.

   One thing deliberately stays in the prose column: the recommended-choice
   badge. It is advice ABOUT A CONTROL, and a reviewer should not have to read
   the margin, look back, and hunt for the chip it meant. */

// Taste-first ordering (issue #175) — same discipline as the review print's
// weakest-first confidence sort (`hasConfidence` above): the reorder is a
// toggle keyed on whether the data exists at all, not a default. A batch
// where no question carries `grounds` comes back untouched, so an
// interview authored before this field existed renders in the exact
// document order it always has. Where at least one question carries
// `grounds`, taste-classed questions move first — a taste question is the
// reviewer's own call regardless of index, so it should not wait behind a
// page of machine opinions. A stable sort keeps every other relative
// ordering (including ties) exactly as authored.
function orderQAQuestions(questions) {
  if (!questions.some(q => q.grounds)) return questions;
  return questions
    .map((q, i) => ({ q, i }))
    .sort((a, b) => {
      const ta = a.q.grounds === 'taste' ? 0 : 1;
      const tb = b.q.grounds === 'taste' ? 0 : 1;
      return (ta - tb) || (a.i - b.i);
    })
    .map(x => x.q);
}

function initQA() {
  const container = el('qa-cards');
  // `no-gutter` is a constant, not a computed collapse: a question has no
  // producer flags to rail, so neither column's state changes mid-session.
  container.className = 'cards doc no-gutter';
  QA_DATA.questions.forEach((q, i) => {
    const card = buildQACard(q, i);
    card.style.animationDelay = (0.04 + i * 0.04) + 's';
    container.appendChild(card);
  });
  if (QA_DATA.questions.length > 0) {
    activateQACard(QA_DATA.questions[0].id);
  }
  updateQAStats();
}

// `grounds` classing (#175): absent, renders the plain badge (#114). `sourced`
// keeps it ambient — the citation rides in the question's own text/hint.
// `inferred` renders nothing here; see buildQACard's `groundsReveal`. `taste`
// never reaches this function — no matching chip (validate_qa_input).
function recommendedBadge(grounds) {
  if (grounds === 'sourced') {
    return '<span class="chip-badge chip-badge-sourced" title="Recommended — sourced; see the question for its citation">sourced</span>';
  }
  if (grounds === 'inferred') return '';
  return '<span class="chip-badge" title="Recommended — pick whichever you want">recommended</span>';
}

function buildQACard(q, index) {
  const card = document.createElement('div');
  card.className = 'card';
  card.id = 'qacard-' + q.id;

  // recommended_choice is optional (#114) — advisory only: the matching chip
  // gets a badge, nothing else (no pre-selection, no restyle as primary).
  // The digit keycap binds to the same key the keydown handler uses, 1-9.
  const choicesHtml = q.choices.map((c, i) => {
    const isRecommended = q.recommended_choice !== undefined && c === q.recommended_choice;
    const badge = isRecommended
      ? recommendedBadge(q.grounds)
      : '';
    const cap = i < 9 ? `<kbd>${i + 1}</kbd>` : '';
    return `<button class="choice-chip" data-choice="${esc(c)}"><span class="chip-label">${esc(c)}</span>${badge}${cap}</button>`;
  }).join('');
  // An inferred recommendation answers only behind a reveal (issue #175) —
  // never ambiently on the chip itself. Native <details>/<summary>, the same
  // disclosure element `.kbd-legend` already uses.
  const groundsReveal = (q.grounds === 'inferred' && q.recommended_choice !== undefined)
    ? `<details class="chip-reveal"><summary>inferred pick &mdash; show</summary>` +
      `<div class="chip-reveal-body">recommended: <strong>${esc(q.recommended_choice)}</strong></div></details>`
    : '';
  // Taste-classed questions never carry a recommended_choice at all
  // (validate_qa_input rejects the two together), so there is no chip to
  // badge — the label decorates the question's choices instead.
  const tasteLabel = q.grounds === 'taste'
    ? '<span class="chip-badge chip-badge-taste" title="No recommendation offered — this one is yours">this one is yours</span>'
    : '';

  // The disclosure head IS the question, printed once, numbered like a catalog
  // entry. The number goes INSIDE `.card-title`: `.card-title-wrap` is
  // `flex-direction: column`, so a sibling span would stack it above the text.
  const choiceless = q.choices.length === 0;
  card.innerHTML = `
    <button type="button" class="card-head" aria-expanded="false" aria-controls="qbody-${q.id}">
      <span class="dot dot-idle" id="qdot-${q.id}"></span>
      <span class="card-title-wrap">
        <span class="card-title"><span class="doc-num" aria-hidden="true">${index + 1} &middot;</span> ${esc(q.text)}</span>
      </span>
      <span class="vbadge vbadge-approved" id="qbadge-${q.id}" style="display:none"></span>
    </button>
    <div class="card-body-wrap" id="qbody-${q.id}">
      <div class="card-body-inner">
        <div class="card-body">
          <div class="row row-head${choiceless ? ' is-choiceless' : ''}">
            ${choiceless ? '' : `<div class="rp">
              <div class="rule-s"></div>
              ${tasteLabel}
              <div class="choices" id="qchoices-${q.id}">${choicesHtml}</div>
              ${groundsReveal}
            </div>`}
            <div class="rm">
              ${q.hint ? `<div class="nt nt-check"><div class="nh">hint</div><div class="nt-body">${esc(q.hint)}</div></div>` : ''}
              <div class="nt nt-compose">
                <div class="nh">you &mdash; context</div>
                <textarea class="note-field" id="qnote-${q.id}" aria-label="Context for this answer" placeholder="Optional — or paste a screenshot"></textarea>
                <div class="thumb-strip" id="qthumbs-${q.id}" aria-live="polite" style="display:none"></div>
                <button type="button" class="attach-btn" id="qattach-${q.id}">attach image</button>
                ${voiceSupported() ? `<button type="button" class="mic-btn" id="qmic-${q.id}">dictate</button>` : ''}
                <input type="file" accept="image/*" multiple style="display:none" id="qfile-${q.id}">
              </div>
              <div class="nt-acts doc-acts">
                <button type="button" class="nt-btn is-quiet" id="qconfirm-${q.id}"><span aria-hidden="true">&#10003;</span> confirm<kbd>c</kbd></button>
                <button type="button" class="nt-btn is-quiet" id="qskip-${q.id}"><span aria-hidden="true">&#8595;</span> skip</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>`;

  card.querySelector('.card-head').addEventListener('click', () => toggleQACard(q.id));

  // Guarded: a choiceless question has no chip list. Both readers of
  // `#qchoices-` (this one and `syncQACard`'s) carry the guard.
  const ch = card.querySelector('#qchoices-' + q.id);
  if (ch) ch.addEventListener('click', e => {
    const chip = e.target.closest('.choice-chip');
    if (!chip) return;
    e.stopPropagation();
    pickQAChoice(q.id, chip.dataset.choice);
  });

  const qta = card.querySelector('#qnote-' + q.id);
  qta.addEventListener('input', e => {
    if (!qState.answers[q.id]) qState.answers[q.id] = {};
    qState.answers[q.id].note = e.target.value;
    // A typed note IS an answer (#121), so it has to move the same indicators a
    // chip does. Without this the state held the text while the page kept
    // reporting the question unanswered — the counter, the dot and the confirm
    // button only ever refreshed on a chip click.
    syncQACard(q.id);
    updateQAStats();
  });
  qta.addEventListener('click', e => e.stopPropagation());

  card.querySelector('#qconfirm-' + q.id).addEventListener('click', e => { e.stopPropagation(); advanceQA(q.id); });
  card.querySelector('#qskip-'   + q.id).addEventListener('click', e => { e.stopPropagation(); advanceQA(q.id); });

  // Same contract as the review composer's mic: turn the microphone on, put
  // the caret in this box, and let the modal rule do the rest.
  const qmic = card.querySelector('#qmic-' + q.id);
  if (qmic) {
    qmic.classList.toggle('is-live', voiceIsOn());
    qmic.addEventListener('click', e => { e.stopPropagation(); startVoice(() => qta.focus()); });
  }

  wireCapture(
    () => (qState.answers[q.id] ||= {}),
    card.querySelector('#qnote-' + q.id),
    card.querySelector('#qthumbs-' + q.id),
    card.querySelector('#qattach-' + q.id),
    card.querySelector('#qfile-' + q.id),
    card
  );

  return card;
}

/* The ONE definition of "this question has an answer" (#121). A free-text-only
   question never sets `choice`, so gating on `choice` alone dropped typed
   notes from the progress stat, the dot, auto-advance, and the submit filter.
   Every reader routes through here so they can't disagree about it. */
function qaAnswered(id) {
  const a = qState.answers[id];
  if (!a) return false;
  return Boolean(a.choice || (a.note && a.note.trim()));
}

// One place a choice is picked, so the chip, the digit key and the palette
// can never disagree about what a second press means — it clears the answer.
function pickQAChoice(id, choice) {
  const a = (qState.answers[id] ||= {});
  a.choice = a.choice === choice ? null : choice;
  syncQACard(id);
  updateQAStats();
}

function activateQACard(id) {
  if (qState.active && qState.active !== id) {
    setCardExpanded(el('qacard-' + qState.active), false);
    syncQADot(qState.active);
  }
  qState.active = id;
  const card = el('qacard-' + id);
  if (card) {
    setCardExpanded(card, true);
    card.scrollIntoView({ behavior: SMOOTH, block: 'nearest' });
  }
  syncQADot(id);
}

function toggleQACard(id) {
  if (qState.active === id) {
    setCardExpanded(el('qacard-' + id), false);
    qState.active = null;
    syncQADot(id);
  } else {
    activateQACard(id);
  }
}

function advanceQA(id) {
  setCardExpanded(el('qacard-' + id), false);
  if (qaAnswered(id)) el('qacard-' + id)?.classList.add('is-approved');
  qState.active = null;
  syncQADot(id);

  const qs  = QA_DATA.questions;
  const idx = qs.findIndex(q => q.id === id);
  const next= qs.slice(idx + 1).find(q => !qaAnswered(q.id));
  if (next) setTimeout(() => activateQACard(next.id), 80);

  updateQAStats();
}

function syncQACard(id) {
  const choice = qState.answers[id]?.choice || null;

  // Chip selections. Guarded for the same reason buildQACard's wiring is: a
  // choiceless question has no `#qchoices-` element at all.
  const chEl = el('qchoices-' + id);
  if (chEl) chEl.querySelectorAll('.choice-chip').forEach(chip => {
    chip.classList.toggle('selected', chip.dataset.choice === choice);
    chip.setAttribute('aria-pressed', String(chip.dataset.choice === choice));
  });

  // Badge
  const badge = el('qbadge-' + id);
  if (choice) { badge.style.display=''; badge.textContent=choice; }
  else badge.style.display = 'none';

  // The verb's own grammar: primary once there is an answer to confirm, quiet
  // while there is not — the same rule review's approve follows, and the same
  // two classes, so one button reads the same way on both surfaces.
  const btn = el('qconfirm-' + id);
  btn.className = 'nt-btn ' + (qaAnswered(id) ? 'is-pri' : 'is-quiet');

  syncQADot(id);
}

function syncQADot(id) {
  const answered = qaAnswered(id);
  const isActive = qState.active === id;
  const dot = el('qdot-' + id);
  if (!dot) return;
  dot.className = 'dot ' + (answered ? 'dot-approved' : isActive ? 'dot-active' : 'dot-idle');
}

function updateQAStats() {
  const qs       = QA_DATA.questions;
  const answered = qs.filter(q => qaAnswered(q.id)).length;
  const total    = qs.length;
  const remaining= total - answered;

  el('qa-progress-label').textContent = `${answered} / ${total}`;
  // Same footer shape as the review page. An interview has no judgment/facts
  // axis — an answer is given or it is not — so the balance rule fills with
  // settled alone.
  el('stat-pending').innerHTML = remaining > 0
    ? `blocked &middot; ${remaining} unanswered`
    : 'ready <kbd>&#8984;&#9166;</kbd>';
  renderFootSeg({ judgment: 0, facts: 0, settled: answered }, total,
                `answers: ${answered} of ${total} questions`);

  const sub = el('btn-submit');
  sub.className = remaining === 0 ? 'btn-submit ready' : 'btn-submit disabled';
  // Same rule as updateReviewStats: aria-disabled for not-ready, the DOM
  // `disabled` attribute reserved for in-flight.
  sub.setAttribute('aria-disabled', sub.classList.contains('disabled') ? 'true' : 'false');
  sub.textContent = remaining > 0 ? `answers — dispatch (${remaining} unanswered)`
                                  : 'answers — dispatch';
}

/* ─── Image attachments ────────────────────────────────────── */
function renderThumbs(stateObj, stripEl) {
  const imgs = stateObj.images || [];
  stripEl.innerHTML = imgs.map((im, i) =>
    `<div class="thumb"><img src="data:${esc(im.mime)};base64,${im.data}" width="64" height="64" alt="Attached image ${i + 1}">` +
    `<button class="thumb-remove" data-i="${i}" title="Remove image" aria-label="Remove image">&times;</button></div>`
  ).join('');
  stripEl.style.display = imgs.length ? 'flex' : 'none';
  stripEl.querySelectorAll('.thumb-remove').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      stateObj.images.splice(Number(btn.dataset.i), 1);
      renderThumbs(stateObj, stripEl);
    });
  });
}

function attachImageFiles(stateObj, files, stripEl) {
  const list = Array.from(files || []).filter(f => f.type.startsWith('image/'));
  if (!list.length) return;
  if (!stateObj.images) stateObj.images = [];
  let pending = list.length;
  list.forEach(file => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      const comma = result.indexOf(',');
      stateObj.images.push({ data: result.slice(comma + 1), mime: file.type });
      if (--pending === 0) renderThumbs(stateObj, stripEl);
    };
    reader.onerror = () => { if (--pending === 0) renderThumbs(stateObj, stripEl); };
    reader.readAsDataURL(file);
  });
}

function wireCapture(stateGetter, textarea, stripEl, attachBtn, fileInput, card) {
  // Only accept drops when the note area holding the strip is visible — review
  // cards hide it for approved/pending verdicts, where captured images could
  // neither be seen, removed, nor read by the verdict's consumer.
  const droppable = () => stripEl.isConnected && stripEl.parentElement != null
                       && stripEl.parentElement.style.display !== 'none';
  textarea.addEventListener('paste', e => {
    const files = Array.from(e.clipboardData?.items || [])
      .filter(it => it.kind === 'file' && it.type.startsWith('image/'))
      .map(it => it.getAsFile()).filter(Boolean);
    if (files.length) { e.preventDefault(); attachImageFiles(stateGetter(), files, stripEl); }
  });
  card.addEventListener('dragover', e => {
    if (!droppable()) return;
    e.preventDefault();
    card.classList.add('is-drop-target');
  });
  card.addEventListener('dragleave', e => {
    if (e.target === card) card.classList.remove('is-drop-target');
  });
  card.addEventListener('drop', e => {
    card.classList.remove('is-drop-target');
    if (!droppable() || !e.dataTransfer?.files?.length) return;
    e.preventDefault();
    attachImageFiles(stateGetter(), e.dataTransfer.files, stripEl);
  });
  attachBtn.addEventListener('click', e => { e.stopPropagation(); fileInput.click(); });
  fileInput.addEventListener('change', () => {
    attachImageFiles(stateGetter(), fileInput.files, stripEl);
    fileInput.value = '';
  });
}

