/* ─────────────────────────────────────────────────────────
   DATA
───────────────────────────────────────────────────────── */
let REVIEW_DATA = null;
let QA_DATA = null;
// Fetched once at boot alongside /input, reused for every card build after
// (including a round 2+ SSE rebuild) — never re-fetched mid-session (#142).
let PREFS_DATA = [];
let PREFS_BY_ID = new Map();

/* ─────────────────────────────────────────────────────────
   STATE
   Cards are built ONCE. All interactions do surgical DOM
   updates — no innerHTML rebuilds, no animation resets.
───────────────────────────────────────────────────────── */
const rState = { verdicts: {}, active: null };
const qState = { answers: {}, active: null };
const _pendingMarkdown = new Map(); // section id → raw markdown; deleted after first render

/* ─────────────────────────────────────────────────────────
   HELPERS
───────────────────────────────────────────────────────── */
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// `.rev-tri`'s title (tooltip) text. `revision_count_partial` means a
// historical round file couldn't be read, so any count is a lower bound —
// say "≥N", never assert N as fact. Checked on every section with a `diff`
// this round, not only ones past the 2+ threshold, since the unreadable
// round could be the one that would have pushed it over.
function revTriTooltip(round, section) {
  const base = `revised at REV ${String(round).padStart(2,'0')}`;
  if (section.revision_count >= 2) {
    return section.revision_count_partial
      ? `${base} · ≥${section.revision_count} revisions, partial history`
      : `${base} · ${section.revision_count} content revisions this session`;
  }
  return section.revision_count_partial
    ? `${base} · partial history, revision count unavailable`
    : base;
}

function tabDocName(path) {
  return (path || '').split('/').pop();
}

// Session identity, not per-event data (#172) — the repo name is fixed for
// the tab's life, stashed once where it enters rather than threaded
// through every setTabTitle call site.
let TAB_REPO = null;

function setTabTitle(...parts) {
  document.title = [TAB_REPO, ...parts].filter(Boolean).concat('viva').join(' · ');
}

// The 'processing' SSE handler's own title setter, kept distinct from
// setTabTitle: it fires the instant a round is submitted, before the
// server has a fresh doc/round, so it only has the PRIOR round's doc name.
function setProcessingTabTitle(docName) {
  document.title = [TAB_REPO, docName, 'working…'].filter(Boolean).concat('viva').join(' · ');
}

// Turn-state colors for the inline data: URI favicon — no network fetch,
// mirroring the CSS custom properties for the same states rather than a
// second palette. Swaps the <link>'s href in place; setTabTitle's sibling.
const FAVICON_COLOR = { turn: '2946c4', processing: 'a06a12', done: '0c7f6b' };
function setTabFavicon(state) {
  const color = FAVICON_COLOR[state] || FAVICON_COLOR.turn;
  const link = el('favicon-link');
  if (!link) return;
  link.href = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%23" + color + "'/%3E%3C/svg%3E";
}

/* Render verbatim markdown into el. Falls back to raw monospace text if
   marked/DOMPurify haven't loaded yet (`defer` scripts) — both are required
   before HTML is committed to the DOM, since parsing without sanitizing
   would render untrusted markdown's raw HTML unescaped. Returns true on a
   real render, false on the fallback, so callers can retry later. */
function renderMarkdown(target, md) {
  if (window.marked && window.DOMPurify) {
    const html = marked.parse(md);
    target.innerHTML = DOMPurify.sanitize(html);
    target.classList.remove('md-raw');
    if (window.hljs) {
      target.querySelectorAll('code[class^="language-"]').forEach(b => hljs.highlightElement(b));
    }
    return true;
  }
  target.classList.add('md-raw');
  target.textContent = md;
  return false;
}

// section.title for diff-mode sections is "{filepath} hunk N" (parse_diff.py).
// Strip the " hunk N" suffix to recover the filepath. Shared by
// diffFileHunkCounts and renderDiffHunk.
function filepathFromTitle(title) {
  return String(title || '').replace(/\s+hunk\s+\d+$/, '');
}

// _ensureRendered only has a section id at render time; renderDiffHunk
// needs the section's title to synthesize the file preamble diff2html
// expects. Delegates to reviewSectionTitles() — the one id→title lookup.
function sectionTitleFor(id) {
  return reviewSectionTitles().get(id) || '';
}

/* Render one git hunk via diff2html: unified, line-by-line, word-level
   intra-line diffs. Pure view transform — section.content stays the
   verbatim fence other logic depends on; the ---/+++ preamble diff2html
   needs is synthesized here at render time only, from the title's
   filepath, and never stored.
   Pipeline order is load-bearing: Diff2Html.html() gives markup as a
   STRING, DOMPurify sanitizes the string, and only then does it touch the
   DOM — materializing first would let insertion-time payloads execute
   before removal. Falls back to the fenced-```diff markdown view (tagged
   d2h-pending for the load listeners to upgrade) if diff2html can't parse
   the hunk or its assets haven't loaded. */
function renderDiffHunk(target, raw, title) {
  const body = raw.replace(/^```diff\n/, '').replace(/\n```$/, '');
  if (!/^@@ /.test(body)) return renderMarkdown(target, raw);
  const cssLink = el('diff2html-css');
  if (!(window.Diff2Html && window.Diff2HtmlUI && window.DOMPurify && cssLink && cssLink.sheet)) {
    const ok = renderMarkdown(target, raw);
    if (ok) target.classList.add('d2h-pending');
    return ok;
  }
  const fp = filepathFromTitle(title);
  const diff = '--- a/' + fp + '\n+++ b/' + fp + '\n' + body;
  try {
    const rawHtml = Diff2Html.html(diff, {
      drawFileList: false,
      colorScheme: 'auto',
      matching: 'words',
      diffStyle: 'word',
      // UNIFIED, always. Side-by-side splits the hunk into two panes that
      // each scroll independently — at a 1440px viewport each pane is only
      // 445px (53 chars), while unified's 892px shows 107. Word-level
      // diffs survive either format via `diffStyle: 'word'`.
      outputFormat: 'line-by-line',
    });
    target.innerHTML = DOMPurify.sanitize(rawHtml);
  } catch (e) {
    return renderMarkdown(target, raw);
  }
  target.classList.remove('d2h-pending');
  // Line numbers are visual chrome: unselectable via CSS (anchor hygiene),
  // and hidden from screen readers here — they'd otherwise announce before
  // every code line.
  target.querySelectorAll('.d2h-code-linenumber')
    .forEach(n => n.setAttribute('aria-hidden', 'true'));
  // Slim UI wrapper constructed with an undefined diff wraps the existing
  // (sanitized) DOM; hljs is the page's own instance, passed in because the
  // slim bundle deliberately doesn't embed one.
  try {
    new Diff2HtmlUI(target, undefined, { highlight: true }, window.hljs).highlightCode();
  } catch (e) { /* syntax color only; word-level diff survives */ }
  target.classList.remove('md-raw');
  return true;
}

function el(id) { return document.getElementById(id); }
// Reduced motion is honored in script as well as in CSS: every programmatic
// scroll asks this instead of hardcoding `smooth`.
const SMOOTH = (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) ? 'auto' : 'smooth';
// The footer prints a round trip only once it is worth knowing about.
const SLOW_RTT_MS = 200;
// One status line for refusals — an approve pressed on a section with
// comments open, a save pressed on an empty box. Cleared and re-set on a
// tick so the same sentence announces twice.
function announce(text) {
  const n = el('sr-status'); if (!n) return;
  n.textContent = '';
  setTimeout(() => { n.textContent = text; }, 30);
}

function ledgerRowsHTML(entries) {
  return entries.map(e => `
    <div class="ledger-row">
      <span class="ledger-round">R${esc(e.round)}</span>
      <span class="ledger-section">${esc(e.section_title)}</span>
      <span class="ledger-verdict v-${e.verdict}">${e.verdict}</span>
      <span class="ledger-note">${e.note ? '&ldquo;' + esc(e.note) + '&rdquo;' : '&mdash;'}</span>
    </div>`).join('');
}

function renderLedger() {
  const entries = (REVIEW_DATA && REVIEW_DATA.ledger) || [];
  const panel = el('ledger');
  if (!entries.length) { panel.style.display = 'none'; return; }
  panel.style.display = '';
  el('ledger-count').textContent = entries.length;
  el('ledger-rows').innerHTML = ledgerRowsHTML(entries);
  el('ledger').classList.toggle('is-collapsed', entries.length > 2);
  const head = el('ledger-head');
  const paint = () => head.setAttribute('aria-expanded',
    el('ledger').classList.contains('is-collapsed') ? 'false' : 'true');
  paint();
  head.addEventListener('click', () => { el('ledger').classList.toggle('is-collapsed'); paint(); });
}

// The palette's and `l`'s one path to the ledger: open, expand, scroll.
function openLedger() {
  const p = el('ledger');
  if (!p || p.style.display === 'none') return;
  p.classList.remove('is-collapsed');
  el('ledger-head')?.setAttribute('aria-expanded', 'true');
  p.scrollIntoView({ behavior: SMOOTH, block: 'nearest' });
}

/* ─── Transmittal slip (round >= 2, review mode only) ────────
   One row per section: revised (to your note, or silent), flagged &
   unreviewed, or approved & unchanged. Pure classification, no DOM — diff
   mode ships none since hunk identity is positional across rounds. */
const FLAG_RANK = { error: 0, warn: 1 };

// Strongest flag severity on a section: 0 (error), 1 (warn), or null.
/* The author's turn on a thread, answered FOR THIS ROUND: a response, or
   grounds (key presence — a decline with no grounds still counts). The
   `round - 1` freshness check stops a stale answer re-reading as news later. */
function authorAnswered(t, round) {
  const last = ((t || {}).exchanges || []).slice(-1)[0] || {};
  if (Number(last.round) !== round - 1) return false;
  return Boolean(last.response) || last.grounds !== undefined;
}

function sectionAnswered(s, round) {
  return ((s || {}).open_notes || []).some(t => authorAnswered(t, round));
}

// A DOC_SCOPE flag is skipped: it's a fact about the document, not this
// section — without this, one missing type heading would brand section 1
// "flagged & unreviewed".
function flagRank(section) {
  const ranks = ((section && section.annotations) || [])
    .filter(a => a && !DOC_SCOPE_KINDS.includes(a.kind))
    .map(a => FLAG_RANK[(a || {}).severity])
    .filter(r => r !== undefined);
  return ranks.length ? Math.min(...ranks) : null;
}

function transmittalHTML(data) {
  if (!data || data.mode !== 'review' || !(data.round > 1)) return '';
  const approved = new Set(data.approved_ids || []);
  // A carried row reflects a prior-round stamp that still stands. A withdrawn
  // approval clears rState's verdict, dropping the row from carried (and
  // reappearing as flagged if it still carries annotations).
  const carriedNow = id => approved.has(id) && rState.verdicts[id]?.verdict === 'approved';
  const revisedNoted = [], revisedBare = [], flaggedErr = [], flaggedWarn = [],
        answered = [], carried = [];
  (data.sections || []).forEach(s => {
    const hasDiff  = Array.isArray(s.diff) && s.diff.length > 0;
    const hasNotes = Array.isArray(s.open_notes) && s.open_notes.length > 0;
    if (hasDiff) { (hasNotes ? revisedNoted : revisedBare).push(s); return; }
    if (carriedNow(s.id)) { carried.push(s); return; }
    // The author answered the reviewer's note with no edit — a decline (#167)
    // or a response needing none. Checked AFTER carried, since a signed-off
    // section is settled, not news; a stale answer falls through to flagRank.
    if (sectionAnswered(s, data.round)) { answered.push(s); return; }
    const rank = flagRank(s);
    if (rank !== null) { (rank === 0 ? flaggedErr : flaggedWarn).push(s); return; }
  });
  const row = (s, cls, marker, label) =>
    '<button type="button" class="transmittal-row ' + cls + '" data-target="' + esc(s.id) + '">'
    + '<span class="tr-marker" aria-hidden="true">' + marker + '</span>'
    + '<span class="tr-label">' + label + '</span>'
    + '<span class="tr-title">' + esc(s.title) + '</span></button>';
  // A revised row names its cause when the diff answers the reviewer's own
  // open note; a silent revision stays bare.
  const revisedRow = s => {
    const noted = Array.isArray(s.open_notes) && s.open_notes.length > 0;
    return row(s, noted ? 'tr-revised-note' : 'tr-revised', '&#9651;',
               noted ? 'revised to your note' : 'revised');
  };
  const rows = revisedNoted.concat(revisedBare).map(revisedRow).concat(
    // News before unreviewed machine output: an answer is the author's turn,
    // a flag is a producer's.
    answered.map(s => row(s, 'tr-answered', '&#8627;', 'answered, not revised')),
    flaggedErr.map(s => row(s, 'tr-flag-error', '&#9873;', 'flagged &amp; unreviewed')),
    flaggedWarn.map(s => row(s, 'tr-flag-warn', '&#9873;', 'flagged &amp; unreviewed')),
    carried.map(s => row(s, 'tr-approved', '&#9635;', 'approved &amp; unchanged')));
  if (!rows.length) return '';
  // The head is a disclosure, collapsed by default: the slip is the round's
  // cover note, not its content, so a reader meets the document first
  // rather than a bordered index of it (issue #186).
  return '<button type="button" class="transmittal-head" id="transmittal-head" aria-expanded="false"'
    + ' aria-controls="transmittal-rows"><span class="transmittal-title">Transmittal &middot; REV '
    + esc(String(data.round).padStart(2, '0')) + ' &middot; ' + rows.length
    + (rows.length === 1 ? ' change' : ' changes')
    + '</span><span class="transmittal-chevron" aria-hidden="true">&#9662;</span></button>'
    + '<div class="transmittal-rows" id="transmittal-rows" hidden>' + rows.join('') + '</div>';
}

function wireDisclosure(headId, bodyId) {
  const head = el(headId), body = el(bodyId);
  if (head && body) head.addEventListener('click', () => {
    body.hidden = !body.hidden;
    head.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');
  });
}

function renderTransmittal() {
  const panel = el('transmittal');
  if (!panel) return;
  const html = transmittalHTML(REVIEW_DATA);
  if (!html) { panel.style.display = 'none'; panel.innerHTML = ''; return; }
  panel.innerHTML = html;
  panel.style.display = '';
  panel.querySelectorAll('.transmittal-row').forEach(btn => {
    btn.addEventListener('click', () => activateReviewCard(btn.dataset.target));
  });
  wireDisclosure('transmittal-head', 'transmittal-rows');
}

/* ─── The document slip ──────────────────────────────────────
   Every doc-scope flag in the round, once, in section order — stated once as
   a slip instead of five amber lines duplicated in section 1's margin. */
function docSlipHTML() {
  /* Every mode renders this, not review alone: `docFlagSplit` routes a
     doc-scope flag out of both columns unconditionally, so gating it here
     would make the flag render NOWHERE while round_is_complete still enforces it. */
  if (!REVIEW_DATA) return '';
  const flags = (REVIEW_DATA.sections || []).flatMap(s => docFlagSplit(s).doc);
  if (!flags.length) return '';
  // The checks tally rides in the head because `sectionSpec` no longer draws
  // one: today's only CHECK_KIND is doc-scope, so without this the gate would
  // have no readout anywhere while round_is_complete keeps enforcing it.
  const checks = flags.filter(a => CHECK_KINDS.includes(a.kind));
  const done = checks.filter(a => a.result).length;
  // Collapsed like the transmittal, UNLESS the document carries an error:
  // demoting a document-level error to a digit behind a disclosure is a
  // severity claim nobody made.
  const open = flags.some(a => a.severity === 'error');
  return '<button type="button" class="transmittal-head" id="doc-slip-head" aria-expanded="'
    + (open ? 'true' : 'false') + '" aria-controls="doc-slip-rows">'
    + '<span class="transmittal-title">Document &middot; ' + flags.length
    + (flags.length === 1 ? ' flag' : ' flags')
    + (checks.length ? ' &middot; checks ' + done + '/' + checks.length : '')
    + '</span><span class="transmittal-chevron" aria-hidden="true">&#9662;</span></button>'
    + '<div class="transmittal-rows" id="doc-slip-rows"' + (open ? '' : ' hidden') + '>'
    // Rows dedupe `result`; the tally above does NOT — it's computed off raw
    // flags, else `checks D/T` would misread `1/5` when all five were answered
    // with one sentence.
    + dedupeResults(flags, new Set()).map(marginFlagHTML).join('') + '</div>';
}

function renderDocSlip() {
  const panel = el('doc-slip');
  if (!panel) return;
  const html = docSlipHTML();
  if (!html) { panel.style.display = 'none'; panel.innerHTML = ''; return; }
  panel.innerHTML = html;
  panel.style.display = '';
  wireDisclosure('doc-slip-head', 'doc-slip-rows');
}

