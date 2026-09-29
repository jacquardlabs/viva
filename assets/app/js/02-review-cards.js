/* ─────────────────────────────────────────────────────────
   REVIEW MODE — build once, update surgically
───────────────────────────────────────────────────────── */
// Diff mode only: how many sections share each filepath. parse_diff.py emits
// every hunk of a file contiguously, so one pass here suffices — no lookahead
// needed while iterating in the render loop below.
function diffFileHunkCounts(sections) {
  const counts = new Map();
  sections.forEach(s => {
    const fp = filepathFromTitle(s.title);
    counts.set(fp, (counts.get(fp) || 0) + 1);
  });
  return counts;
}

function initReview() {
  _pendingMarkdown.clear();
  const container = el('review-cards');
  // Both surfaces wear the margin grammar; only review prints continuously.
  // `.doc` arms every grid rule, `.print` only the ones for every section
  // being open at once — no runtime branch needed in CSS.
  const asDoc = isContinuousPrint();
  container.classList.add('doc');
  container.classList.toggle('print', asDoc);
  // Stamped on <body> like `mode-diff`, because the page width it sets has to
  // reach the shell and the bottom bar, which are outside #review-cards.
  document.body.classList.toggle('mode-doc', asDoc);
  el('doc-hint').style.display = '';
  // The composite's bar has no progress track: the footer's segmented rule is
  // the document's progress, in state rather than in percent, and two bars
  // saying the same thing differently is one bar too many.
  el('r-progress-track').style.display = 'none';
  // `round 2 · line` — the round and the pass it was armed for, the way the
  // composite states it. `pass.kind` is boundary-validated against PASS_KINDS,
  // and a diff round can be armed with one too.
  if (REVIEW_DATA.pass && REVIEW_DATA.pass.kind) {
    el('round-badge').textContent =
      String(REVIEW_DATA.round).padStart(2, '0') + ' · ' + REVIEW_DATA.pass.kind;
  }
  const priorApprovedSet = new Set(REVIEW_DATA.approved_ids || []);
  // Pre-populate approved state for sections approved in previous rounds
  priorApprovedSet.forEach(id => {
    rState.verdicts[id] = { verdict: 'approved', note: '' };
  });
  // File-header grouping (diff mode only): a static divider ahead of each
  // contiguous run of hunks sharing a filepath. hunkCounts stays null in
  // review mode, so the check below is always false there.
  const hunkCounts = REVIEW_DATA.mode === 'diff' ? diffFileHunkCounts(REVIEW_DATA.sections) : null;
  let lastFilepath = null;
  let animIdx = 0;
  REVIEW_DATA.sections.forEach((s, i) => {
    if (hunkCounts) {
      const fp = filepathFromTitle(s.title);
      if (fp !== lastFilepath) {
        const header = document.createElement('div');
        header.className = 'file-group-header';
        const n = hunkCounts.get(fp);
        header.textContent = fp + ' · ' + n + ' hunk' + (n === 1 ? '' : 's');
        container.appendChild(header);
        lastFilepath = fp;
      }
    }
    // Sections approved in a prior round (round >= 2) collapse to carried
    // cards — a head-only line with the read-only content one reveal away.
    // Round 1 keeps the normal-card path even when a resume pre-approves ids.
    //
    // Continuous print retires that collapse in review mode (#186): a settled
    // section DIMS IN PLACE, since reading the document is still the point.
    // buildCarriedCard stays diff-mode's path, where a carried hunk has nothing to read.
    const isCarried = !asDoc && REVIEW_DATA.round > 1 && priorApprovedSet.has(s.id);
    const card = asDoc ? buildDocSection(s, i)
                       : isCarried ? buildCarriedCard(s) : buildReviewCard(s);
    // Carried cards appear instantly (no fade) — only new/changed cards get
    // the staggered fade-in, re-indexed among themselves so the stagger stays
    // tight regardless of how many sections are already carried.
    if (isCarried) {
      card.style.animation = 'none';
    } else {
      card.style.animationDelay = Math.min(0.04 + animIdx * 0.04, 0.3) + 's';
      animIdx++;
    }
    container.appendChild(card);
    // Apply approved CSS immediately for round-1 pre-approved normal cards;
    // carried cards bake their collapsed state into their own markup.
    if (!isCarried && priorApprovedSet.has(s.id)) syncReviewCard(s.id);
  });
  // Continuous print renders every section up front — nothing to open, so
  // nothing to render lazily. retryOnceScriptsLoad selects on the
  // `.md-raw`/`.d2h-pending` marker classes, not pending state, so it keeps working.
  if (asDoc) REVIEW_DATA.sections.forEach(s => _ensureRendered(s.id));
  // Where round >= 2 LANDS: the first section carrying something new (a
  // revision, or a thread the author answered) — not the first unapproved
  // section, which would re-show a flag wall the reader already read.
  // `!priorApprovedSet.has` is belt-and-braces against a resume that
  // pre-approves differently than `_load_approved` expects.
  const newBusiness = (isContinuousPrint() && REVIEW_DATA.round > 1)
    ? REVIEW_DATA.sections.find(s => !priorApprovedSet.has(s.id)
        && ((Array.isArray(s.diff) && s.diff.length > 0)
            || sectionAnswered(s, REVIEW_DATA.round)))
    : null;
  // Open first non-approved card
  const firstPending = REVIEW_DATA.sections.find(s => !priorApprovedSet.has(s.id));
  const landing = newBusiness || firstPending || REVIEW_DATA.sections[0];
  if (landing) activateReviewCard(landing.id);
  updateReviewStats();
  renderLedger();
  renderTransmittal();
  // Per-round static: doc-scope flags never change with a verdict, so this is
  // the only call site — the two verdict paths that re-render the transmittal
  // have nothing to say to it, and `/next-round` re-enters initReview.
  renderDocSlip();
  setupCardSort();
}

// Severity → CSS-slot whitelist. Anything off-list (or missing) renders as
// 'info' so a bad value can never break out of the class= attribute position.
const ANNOT_SEVERITIES = { info: 1, warn: 1, error: 1 };

// Advisory annotation strip built from section.annotations (returns '' when
// none). Maps every section id → title for the round, so an annotation
// anchored to another section can render a deep-link to it.
function reviewSectionTitles() {
  const m = new Map();
  ((typeof REVIEW_DATA !== 'undefined' && REVIEW_DATA.sections) || [])
    .forEach(s => m.set(s.id, s.title));
  return m;
}

// A kind:"preference" annotation encodes its id as a leading "[id]" token in
// the message (SKILL.md convention — no structured-field passthrough in
// annotate.py's merge). Unmatched/stale tokens fall back to plain text.
const PREF_ID_RE = /^\[([^\]]+)\]/;

function annotStripHTML(annotations) {
  if (!Array.isArray(annotations) || annotations.length === 0) return '';
  const titles = reviewSectionTitles();
  const rows = annotations.map(a => {
    a = a || {};
    const sev    = ANNOT_SEVERITIES[a.severity] ? a.severity : 'info';
    const kind   = esc(a.kind || 'note');
    const msg    = esc(a.message || '');
    const anchorId = a.anchor != null ? String(a.anchor) : '';
    // Anchor that matches a section id → clickable jump; otherwise hover title.
    const isJump = anchorId && titles.has(anchorId);
    const titleAttr = (anchorId && !isJump) ? ' title="' + esc(anchorId) + '"' : '';
    const jump = isJump
      ? '<button type="button" class="annot-jump" data-target="' + esc(anchorId)
        + '">' + esc(titles.get(anchorId) || anchorId) + ' ↗</button>'
      : '';
    // Badge-to-entry link (#142): a preference annotation whose [id] token
    // matches a fetched preference grows a second jump control, labeled
    // with the preference's own label/id, opening the preferences panel.
    let prefJump = '';
    if (a.kind === 'preference') {
      const m = PREF_ID_RE.exec(a.message || '');
      const pref = m ? PREFS_BY_ID.get(m[1]) : null;
      if (pref) {
        prefJump = '<button type="button" class="annot-jump" data-pref-id="' + esc(pref.id)
          + '">' + esc(pref.label || pref.id) + ' ↗</button>';
      }
    }
    return '<div class="annot annot-' + sev + '"' + titleAttr + '>'
         + '<span class="annot-kind">' + kind + '</span>'
         + '<span class="annot-msg">' + msg + jump + prefJump + '</span></div>';
  }).join('');
  return '<div class="annot-strip" aria-label="pre-review annotations">' + rows + '</div>';
}

// Round-to-round diff block from section.diff (rows of {op, text}); '' when
// none. Presentational only — never touches a verdict. Shown by default, the
// header toggles it collapsed.
// Word-level diff of a paired removed/added line → [delHTML, addHTML] with
// changed tokens wrapped in <span class="dw">, fully escaped. Falls back to
// plain text when the pair shares too little (rewrite noise) or is too large.
function markWordDiff(a, b) {
  // Tokens are word+trailing-whitespace chunks, so bare spaces never count as
  // shared content when judging whether the pair is similar enough to mark.
  const ta = a.split(/(?<=\s)(?=\S)/);
  const tb = b.split(/(?<=\s)(?=\S)/);
  const n = ta.length, m = tb.length;
  if (!n || !m || n * m > 250000) return [esc(a), esc(b)];
  const L = [];
  for (let i = n; i >= 0; i--) L[i] = new Uint16Array(m + 1);
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      L[i][j] = ta[i] === tb[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  if (L[0][0] / Math.max(n, m) < 0.3) return [esc(a), esc(b)];
  const mark = t => '<span class="dw">' + esc(t) + '</span>';
  const oa = [], ob = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (ta[i] === tb[j]) { oa.push(esc(ta[i])); ob.push(esc(tb[j])); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) oa.push(mark(ta[i++]));
    else ob.push(mark(tb[j++]));
  }
  while (i < n) oa.push(mark(ta[i++]));
  while (j < m) ob.push(mark(tb[j++]));
  return [oa.join(''), ob.join('')];
}

function diffStripHTML(id, diff) {
  if (!Array.isArray(diff) || diff.length === 0) return '';
  const line = (cls, g, html) => '<div class="diff-line ' + cls + '">'
    + '<span class="diff-gutter">' + g + '</span>'
    + '<span class="diff-text">' + html + '</span></div>';
  const out = [];
  let k = 0;
  while (k < diff.length) {
    const d = diff[k] || {};
    if (d.op === '@') { out.push('<div class="diff-hunk">' + esc(d.text || '') + '</div>'); k++; continue; }
    if (d.op === '+') { out.push(line('diff-add', '+', esc(d.text || ''))); k++; continue; }
    if (d.op !== '-') { out.push(line('diff-ctx', ' ', esc(d.text || ''))); k++; continue; }
    // A '-' run followed by a '+' run is a rewrite: word-diff the pairs.
    const dels = []; while (k < diff.length && (diff[k] || {}).op === '-') dels.push(String((diff[k++] || {}).text || ''));
    const adds = []; while (k < diff.length && (diff[k] || {}).op === '+') adds.push(String((diff[k++] || {}).text || ''));
    const paired = Math.min(dels.length, adds.length);
    const addHTML = adds.map(esc);
    for (let p = 0; p < dels.length; p++) {
      if (p < paired) {
        const [dh, ah] = markWordDiff(dels[p], adds[p]);
        out.push(line('diff-del', '-', dh));
        addHTML[p] = ah;
      } else out.push(line('diff-del', '-', esc(dels[p])));
    }
    addHTML.forEach(h => out.push(line('diff-add', '+', h)));
  }
  const rows = out.join('');
  return '<div class="diff-block" id="rdiff-' + id + '">'
       + '<button type="button" class="diff-toggle" id="rdiff-toggle-' + id + '">'
       + '<span aria-hidden="true">&#9662;</span> changes since last round</button>'
       + '<div class="diff-body">' + rows + '</div></div>';
}

// Open-note thread for a card from section.open_notes (#16) — the prior
// exchange, carried across rounds until the reviewer settles it. Returns ''
// when there's no open thread.
function openNotesHTML(exchanges) {
  return (exchanges || []).map(x => {
    x = x || {};
    const v = String(x.verdict || '');
    const vClass = (v === 'changes' || v === 'info' || v === 'suggestion') ? ' v-' + v : '';
    return '<div class="exchange">'
      + '<div class="exchange-q">'
      +   '<span class="exchange-round">R' + esc(x.round) + '</span>'
      +   '<span class="exchange-verdict' + vClass + '">' + esc(v) + '</span>'
      +   '<span class="exchange-note">' + esc(x.note || '')
      +     (x.replacement ? '<span class="cmt-repl">' + esc(x.replacement) + '</span>' : '')
      +   '</span>'
      + '</div>'
      // The author's grounds for declining THAT turn, before the response,
      // because it answers the reviewer's request without resolving it. Key
      // presence, not truthiness: a decline with no grounds is still a decline.
      + (x.grounds !== undefined
          ? '<div class="exchange-d">declined: ' + esc(x.grounds) + '</div>' : '')
      + (x.response ? '<div class="exchange-a">' + esc(x.response) + '</div>' : '')
      + '</div>';
  }).join('');
}

// One carried thread as a complete element, shared by both surfaces (#186):
// the doc grid restyles `.open-thread` into the margin's note grammar rather
// than forking markup. `.nh-num` ships empty; only the margin fills it in
// place (renumberDocNotes), so the reply textarea is never rebuilt mid-keystroke.
function openThreadItemHTML(t) {
    const cid = esc(t.cid || '');
    const exs = t.exchanges || [];
    // The thread's current type carries to a reply, defaulting to info — but
    // never suggestion: the reply box collects prose, not replacement wording,
    // and a suggestion with no `replacement` is rejected server-side.
    const last = (exs.length && exs[exs.length - 1].verdict) || 'info';
    const type = (last === 'changes' || last === 'suggestion') ? 'changes' : 'info';
    const quote = t.quote ? '<span class="open-thread-quote">' + esc(t.quote) + '</span>' : '';
    // A declined thread is unresolved, not closed: the author answered and the
    // move is now the reviewer's — settle to accept, or reply to insist (which
    // always wins). Same settle/reply controls, different label and prompt.
    const declined = t.status === 'declined';
    /* One verb per note, with its keycap, rather than a permanently-open reply
       box — too much chrome for a 253px margin. Declined threads lead with
       Accept/Change anyway (settle/reply — an insisting reply always wins);
       open threads offer Reply/Settle. */
    const btn = (cls, label, key, attrs) =>
      '<button type="button" class="nt-btn ' + cls + '"' + (attrs || '') + '>'
      + label + '<kbd>' + key + '</kbd></button>';
    const settle = extra => btn('settle-btn ' + extra, declined ? 'Accept' : 'Settle',
      declined ? 'y' : 's', ' id="rsettle-' + cid + '" data-cid="' + cid + '"');
    const reply = () => btn('thread-reply-btn', declined ? 'Change anyway' : 'Reply',
      declined ? 'n' : 'r',
      ' data-cid="' + cid + '" data-type="' + (declined ? 'changes' : esc(type))
      + '" aria-expanded="false" aria-controls="rreplywrap-' + cid + '"');
    return '<div class="open-thread' + (declined ? ' is-declined' : '')
      + '" id="rthread-' + cid + '" data-cid="' + cid + '">'
      + '<div class="open-thread-head">'
      +   '<span class="nh-num" id="rnum-' + cid + '" aria-hidden="true"></span>'
      +   '<span class="open-thread-label">' + (declined ? THREAD_STATUS_LABELS.declined : THREAD_STATUS_LABELS.open)
      +   '</span><span class="pn">&middot; ' + cid + '</span>' + quote
      + '</div>'
      + '<div class="open-thread-body">' + openNotesHTML(exs) + '</div>'
      + '<div class="nt-acts">'
      +   (declined ? settle('is-pri') + reply() : reply() + settle('is-quiet'))
      + '</div>'
      // Ships hidden; a verb reveals it. wireOpenThread un-hides it on build
      // when a reply is already pending in rState, so a rebuild never loses one.
      + '<div class="thread-reply" id="rreplywrap-' + cid + '" data-cid="' + cid + '" data-type="' + esc(type) + '" hidden>'
      +   '<div class="thread-reply-chips" role="group" aria-label="Reply type">'
      +     '<button type="button" class="cmt-chip cmt-chip-changes' + (type === 'changes' ? ' is-on' : '')
      +       '" data-type="changes" aria-pressed="' + (type === 'changes') + '">request changes</button>'
      +     '<button type="button" class="cmt-chip cmt-chip-info' + (type === 'info' ? ' is-on' : '')
      +       '" data-type="info" aria-pressed="' + (type === 'info') + '">need info</button>'
      +   '</div>'
      +   '<textarea class="thread-reply-field" aria-label="Reply" id="rreply-' + cid + '" data-cid="' + cid
      +     '" placeholder="' + (declined
            ? 'A reply insists, and an insisting reply is binding.'
            : 'Reply… (switch to “request changes” to turn the discussion into an edit)')
      +     '"></textarea>'
      + '</div>'
      + '</div>';   // close .open-thread — unclosed, two threads nested
}

/* ─── Confidence triage (issue #12) ───────────────────────────
   Each section self-annotates with kind:"confidence" (basis: sourced|inferred,
   level: high|medium|low). The reviewer can reorder weakest-first (default:
   document order); sections with none sink to the bottom. */
const LEVEL_RANK = { low: 0, medium: 1, high: 2 };
const BASIS_RANK = { inferred: 0, sourced: 1 };

function confidenceAnnot(section) {
  return (section.annotations || []).find(a => a && a.kind === 'confidence') || null;
}

// Smaller = weaker = shown first. inferred+low → 0 (weakest); sourced+high → 5.
// No confidence annotation → 99, so unknowns sink below ranked cards while
// CSS `order` ties preserve document (DOM) order among them.
function weaknessScore(section) {
  const c = confidenceAnnot(section);
  if (!c) return 99;
  const l = LEVEL_RANK[c.level] === undefined ? 1 : LEVEL_RANK[c.level];
  const b = BASIS_RANK[c.basis] === undefined ? 1 : BASIS_RANK[c.basis];
  return l * 2 + b;
}

function applyCardSort() {
  const conf = rState.sortMode === 'confidence';
  REVIEW_DATA.sections.forEach(s => {
    const card = el('rcard-' + s.id);
    if (card) card.style.order = conf ? String(weaknessScore(s)) : '';
  });
  const btn = el('sort-toggle');
  if (btn) {
    btn.classList.toggle('is-active', conf);
    // The label names what a click DOES; the state is visible in the print.
    btn.innerHTML = conf ? '&#8645; restore document order' : '&#8645; sort weakest first';
    btn.setAttribute('aria-pressed', conf ? 'true' : 'false');
  }
}

function setupCardSort() {
  rState.sortMode = 'document';
  const bar = el('sort-bar');
  // Diff mode's file-header grouping depends on cards staying in document
  // order (CSS `order` would strand the file-group-header divs), so force the
  // toggle off here rather than relying on diff sections lacking confidence.
  const hasConfidence = REVIEW_DATA.mode !== 'diff' && REVIEW_DATA.sections.some(s => confidenceAnnot(s));
  if (bar) bar.style.display = hasConfidence ? '' : 'none';
  applyCardSort();
}

/* ─── The accordion, wearing the margin grammar ─────────────────
   Diff mode's builder: one hunk open at a time via a real disclosure button.
   Old chrome (annotation strip, thread list, note/action rows) moved to the
   margin, beside the lines it concerns, rather than stacking atop the hunk.
   The hunk itself is one `wide` row (`.d2h-wrapper`, layoutDocRows). */
function buildReviewCard(section) {
  const card = document.createElement('div');
  card.className = 'card';
  card.id = 'rcard-' + section.id;

  // Store raw markdown for lazy render on first open
  _pendingMarkdown.set(section.id, section.content ?? '');

  card.innerHTML = `
    <button type="button" class="card-head" aria-expanded="false" aria-controls="rbody-${section.id}">
      <span class="dot dot-idle" id="rdot-${section.id}"></span>
      <span class="card-title-wrap">
        <span class="card-title">${esc(section.title)}</span>
        ${section.summary ? `<span class="section-summary">${esc(section.summary)}</span>` : ''}
        <span class="note-inline" id="rnote-inline-${section.id}" style="display:none"></span>
      </span>
      ${section.diff ? `<span class="rev-tri" title="${revTriTooltip(REVIEW_DATA.round, section)}"><span aria-hidden="true">&#9651;</span> ${String(REVIEW_DATA.round).padStart(2,'0')}${section.revision_count >= 2 ? `<span class="rev-mult"> ${section.revision_count}&times;</span>` : ''}</span>` : ''}
      <span class="vbadge" id="rbadge-${section.id}" style="display:none"></span>
    </button>
    <div class="card-body-wrap" id="rbody-${section.id}">
      <div class="card-body-inner">
        <div class="card-body">
          ${docHeadRowHTML(section.id, `<div id="rseg-${section.id}"></div>${diffStripHTML(section.id, section.diff)}`)}
          <div class="section-content" id="rcontent-${section.id}"></div>
          ${docFootRowHTML(section.id, section.title, { skip: true })}
          <div class="comment-popover" id="rpop-${section.id}" style="display:none"></div>
        </div>
      </div>
    </div>`;

  card.querySelector('.card-head').addEventListener('click', () => {
    toggleReviewCard(section.id);
  });

  wireDocSection(card, section.id);
  return card;
}

/* ─── Carried cards (round >= 2 prior approvals) ────────────────
   A section approved in a prior round collapses to a dimmed, head-only line:
   marker, title, reveal toggle, APPROVED stamp, withdraw control. No
   comment machinery — withdrawing turns it back into a normal card. */
function buildCarriedCard(section) {
  const card = document.createElement('div');
  card.className = 'card is-carried';
  card.id = 'rcard-' + section.id;

  // Keep raw markdown for lazy render on first reveal (same path live cards use).
  _pendingMarkdown.set(section.id, section.content ?? '');

  card.innerHTML = `
    <div class="carried-head">
      <span class="carried-marker">carried</span>
      <span class="card-title">${esc(section.title)}</span>
      <button type="button" class="carried-show" id="rcarried-show-${section.id}" aria-expanded="false" aria-controls="rcarried-body-${section.id}">unchanged since your stamp &mdash; show</button>
      <span class="carried-stamp">APPROVED</span>
      <button type="button" class="carried-withdraw" id="rcarried-withdraw-${section.id}" title="withdraw approval &mdash; reopen this section for review"><span aria-hidden="true">&times;</span> withdraw approval</button>
    </div>
    <div class="carried-body" id="rcarried-body-${section.id}" hidden>
      <div class="section-content" id="rcontent-${section.id}"></div>
    </div>`;

  // The whole head line toggles the reveal (mouse convenience); the show
  // button is the focusable, aria-wired affordance for the same action.
  card.querySelector('.carried-head').addEventListener('click', () => {
    setCarriedShown(section.id, el('rcarried-body-' + section.id).hidden);
  });
  card.querySelector('#rcarried-show-' + section.id).addEventListener('click', e => {
    e.stopPropagation();
    setCarriedShown(section.id, el('rcarried-body-' + section.id).hidden);
  });
  card.querySelector('#rcarried-withdraw-' + section.id).addEventListener('click', e => {
    e.stopPropagation(); withdrawApproval(section.id);
  });
  return card;
}

// Reveal/hide a carried card's read-only content, keeping the show button's
// label and aria-expanded in sync. Rendering stays lazy via _ensureRendered.
function setCarriedShown(id, shown) {
  const body = el('rcarried-body-' + id); if (!body) return;
  body.hidden = !shown;
  if (shown) _ensureRendered(id);
  const btn = el('rcarried-show-' + id);
  if (btn) {
    btn.setAttribute('aria-expanded', shown ? 'true' : 'false');
    btn.innerHTML = 'unchanged since your stamp &mdash; ' + (shown ? 'hide' : 'show');
  }
}

// Withdraw a carried approval: clear the verdict, swap the collapsed carried
// card for a normal accordion card, opened for re-review. The fresh card
// replaces the carried one in place — document order stays canonical.
function withdrawApproval(id) {
  if (rState.verdicts[id]) rState.verdicts[id].verdict = undefined;
  const section = REVIEW_DATA.sections.find(s => s.id === id);
  const old = el('rcard-' + id);
  if (!section || !old) return;
  // buildReviewCard re-arms _pendingMarkdown, so content re-renders lazily
  // even if the carried reveal already consumed it.
  old.replaceWith(buildReviewCard(section));
  activateReviewCard(id);
  updateReviewStats();
  renderTransmittal();   // the withdrawn section is no longer "approved & unchanged"
}

