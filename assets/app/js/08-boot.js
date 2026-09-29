
el('prefs-toggle').addEventListener('click', () => openPrefsPanel(el('prefs-toggle')));
el('prefs-close').addEventListener('click', closePrefsPanel);
el('prefs-overlay').addEventListener('click', e => {
  if (e.target === el('prefs-overlay')) closePrefsPanel();   /* backdrop click */
});

el('sort-toggle').addEventListener('click', () => {
  rState.sortMode = rState.sortMode === 'confidence' ? 'document' : 'confidence';
  applyCardSort();
});

/* ─── Init — fetch data from server, then build cards ─── */
/* ─── SSE client ────────────────────────────────────────── */
// Soft, client-side timeout for #processing-view (#119): a slow qa→review
// hand-off doesn't trip es.onerror, so this catches it instead
// (docs/headless-contract.md §6/§7). Long enough not to false-trigger.
const PROCESSING_STILL_WAITING_MS = 20000;
let processingTimer = null;

// Clears the armed timeout (if any) and removes the still-waiting banner
// (if shown) — called whenever #processing-view's own visibility changes,
// so the timer's lifecycle never diverges from the view it describes.
function clearProcessingTimer() {
  if (processingTimer) { clearTimeout(processingTimer); processingTimer = null; }
  const b = el('processing-wait-banner');
  if (b) b.remove();
}

// A position: fixed banner prepended to document.body. Skipped outright if
// the connection has actually dropped: the dead-session overlay is the
// harder signal, and it's a full-screen scrim this banner would contradict.
function showStillWaitingBanner() {
  processingTimer = null;
  if (deadSessionIsOpen()) return;
  const b = document.createElement('div');
  b.id = 'processing-wait-banner';
  b.className = 'error-banner banner-info';
  b.textContent = 'Still waiting — check the terminal.';
  document.body.prepend(b);
}

/* A round arrived that this tab cannot render. The server already refuses
   this at `/next-round`; this is the strand backstop for when it doesn't —
   the previous round stays on screen instead of freezing on "revising".
   Full `--orange` ink: a broken payload, not a slow one. */
function showRoundRefused() {
  clearProcessingTimer();       // its banner and ours would stack at top: 0
  if (el('round-refused-banner')) return;
  const b = document.createElement('div');
  b.id = 'round-refused-banner';
  b.className = 'error-banner';
  b.textContent = 'A round arrived that this tab cannot render — check the terminal.';
  document.body.prepend(b);
}

// The one removal site, called from both SSE handlers that mean "the session
// moved on". A `position: fixed` banner with no removal path outlives the thing
// it describes and sits over a perfectly good later round.
function clearRoundRefused() {
  const b = el('round-refused-banner');
  if (b) b.remove();
}

/* ─── Dead session (#174) ───────────────────────────────────
   A banner alone let a reviewer keep working into a socket that's gone.
   Three layers block it: `inert` takes pointer/Tab from the background, the
   document keydown listener catches what `inert` can't, and sendSubmit
   refuses outright. Not dismissible — only es.onopen (a real reconnect)
   clears it, so a lid-close blip can't lock out a live session. */
function deadSessionIsOpen() { return el('dead-overlay').style.display !== 'none'; }

function showDeadSession() {
  if (deadSessionIsOpen()) return;
  // Close the other modals FIRST: they sit outside setBackgroundInert's
  // subtree, and both restore focus into the background on close — after
  // this overlay takes focus, that would pull it straight back out.
  closeRecap();
  closePrefsPanel();
  closePalette();
  // And the microphone: `inert` on #paper takes pointer/Tab from the voice
  // toggle, and the keydown listener returns above the Escape-stop branch —
  // so a mic left hot here has no control left to reach.
  stopVoice('the session ended');
  // The one command this tab can honestly name. `doc_file` is a real target
  // only in review mode (parse_diff.py writes review_target.py's LABEL, e.g.
  // "PR #187"); qa has no doc, so it gets the generic line instead.
  const doc = REVIEW_DATA && REVIEW_DATA.mode === 'review' && REVIEW_DATA.doc_file;
  el('dead-cmd').textContent = doc ? '/viva-review ' + doc : '';
  el('dead-resume').style.display = doc ? '' : 'none';
  el('dead-overlay').style.display = '';
  setBackgroundInert(true);
  el('dead-panel').focus();
}

function hideDeadSession() {
  if (!deadSessionIsOpen()) return;
  const overlay = el('dead-overlay');
  const hadFocus = overlay.contains(document.activeElement);
  overlay.style.display = 'none';
  setBackgroundInert(false);   // clear inert BEFORE restoring focus, same order as closeRecap
  if (hadFocus) el('btn-submit').focus();
}

// Renders #processing-view's two variants: the between-rounds card (with
// the reviewer's just-submitted rows verbatim) when submitReview snapshotted
// rows, else the minimal line — qa submits and zero-row reviews never do.
function renderProcessingView() {
  const heading = el('processing-heading');
  const list    = el('processing-requests');
  const rows    = (betweenRounds && betweenRounds.rows) || [];
  if (!rows.length) {
    heading.textContent = 'Claude is revising…';
    list.style.display = 'none';
    list.innerHTML = '';
    return;
  }
  heading.textContent = 'REV ' + String(betweenRounds.round).padStart(2, '0') + ' submitted — the agent is revising';
  // Same note the reviewer wrote in the margin, same grammar — `info` is an
  // open fact and takes the fact ink, everything else takes the judgment ink.
  // A second row vocabulary for the same objects is what `.pr-*` was.
  list.innerHTML = rows.map(r =>
    '<div class="nt' + (r.type === 'info' ? ' nt-fact' : '') + '">'
    + '<div class="nh">' + esc(r.type)
    +   '<span class="pn">&middot; ' + esc(r.sectionTitle) + '</span></div>'
    + '<div class="nt-body">' + esc(r.note) + '</div>'
    + '</div>').join('');
  list.style.display = '';
}

function connectSSE() {
  const es = new EventSource('/events');

  es.addEventListener('processing', () => {
    clearRoundRefused();  // a new submit is in flight; the refused round is history
    closeRecap();       // the review it recapped is gone from under it
    closePrefsPanel();  // ditto — no full-screen backdrop survives a view swap
    // Retitle the instant the round is submitted, before the agent's response
    // arrives — otherwise the tab keeps showing the previous "your turn" REV
    // badge while the agent is actually working (#172).
    setProcessingTabTitle(REVIEW_DATA ? tabDocName(REVIEW_DATA.doc_file) : null);
    setTabFavicon('processing');
    renderProcessingView();
    el('review-view').style.display     = 'none';
    el('qa-view').style.display         = 'none';
    el('processing-view').style.display = '';
    // Retire the previous round's controls with its cards: `skip rest &
    // submit` staying live would POST a second submit for a round already in
    // flight. The bar itself stays (theme/prefs/voice); the 'round' handler restores it.
    document.querySelector('.btn-group').style.display = 'none';
    el('foot-seg').style.display = 'none';   // a rule describing a round that is over
    // #stats-area is aria-live=polite, so this announces the wait once instead
    // of leaving `blocked · N unreviewed` from the dead round on screen. The
    // page's own words for this state, from the processing heading above it.
    el('stat-pending').textContent = 'submitted — the agent is revising';
    clearProcessingTimer();
    processingTimer = setTimeout(showStillWaitingBanner, PROCESSING_STILL_WAITING_MS);
  });

  es.addEventListener('round', e => {
    const data = JSON.parse(e.data);
    // Guard BEFORE routing: everything below reads data.sections or overwrites
    // state the current round still uses, so an unrenderable payload must be
    // turned away first. See showRoundRefused for why the client refuses at all.
    if (!data || !Array.isArray(data.sections)) {
      console.error('viva: refused a round payload with no sections[]', data);
      showRoundRefused();
      return;
    }
    clearRoundRefused();
    const modeWord = data.mode === 'diff' ? 'diff' : 'review';
    closeRecap();        // a stale grid must never sit over a fresh round's cards
    closePrefsPanel();   // ditto — a fresh round's cards must never sit behind it
    REVIEW_DATA       = data;
    TAB_REPO          = data.repo || null;   // a qa→review hand-off carries it too (#172)
    // A qa → review hand-off (#109) lands here too: the qa session is done,
    // so drop QA_DATA/qState.active to keep qa-branch logic (keydown handler,
    // updateQAStats/submitQA) from picking up stale state once cards show.
    QA_DATA           = null;
    qState.active     = null;
    rState.verdicts   = {};
    rState.active     = null;
    // The snapshot's round is over — a later 'processing' event with no
    // fresh submit behind it falls back to the minimal line, never a stale
    // card.
    betweenRounds = null;
    setDocTitleBlock(data, modeWord, modeWord === 'diff' ? 'diff' : '');
    el('round-badge').textContent = String(data.round).padStart(2, '0');
    const rev = 'REV ' + String(data.round).padStart(2, '0');
    setTabTitle(tabDocName(data.doc_file), ...(data.mode === 'diff' ? ['diff', rev] : [rev]));
    setTabFavicon('turn');
    el('review-cards').innerHTML  = '';
    initReview();
    el('processing-view').style.display = 'none';
    clearProcessingTimer();
    // Hide qa-view unconditionally rather than trusting a prior 'processing'
    // event to have done so: a reconnect that missed that event (mid-
    // transition) would otherwise leave qa-view visible under the review cards.
    el('qa-view').style.display         = 'none';
    // ...and its page class with it. `mode-diff` happens to out-order `mode-qa`
    // in the stylesheet today, so a stale class wouldn't clamp the diff page —
    // but that's source order doing the work, not a rule to rely on.
    document.body.classList.remove('mode-qa');
    el('review-view').style.display     = '';
    // The whole bar restoration in one place. #foot-seg and #stat-pending need
    // no explicit restore: initReview() → updateReviewStats → reviewFootSeg →
    // renderFootSeg already sets both.
    document.querySelector('.btn-group').style.display = '';
    el('btn-skip').disabled   = false;
    el('btn-submit').disabled = false;
  });

  es.addEventListener('complete', e => {
    es.close(); // prevent onerror when server shuts down 2s later
    const data = JSON.parse(e.data);
    closePrefsPanel();  // no full-screen backdrop survives into complete-view
    stopVoice('the review is signed off');  // nothing left to command
    el('processing-view').style.display = 'none';
    clearProcessingTimer();
    el('review-view').style.display     = 'none';
    el('qa-view').style.display         = 'none';
    el('complete-view').style.display   = '';
    setTabTitle(REVIEW_DATA ? tabDocName(REVIEW_DATA.doc_file) : null, 'done');
    setTabFavicon('done');
    const r   = data.rounds_total;
    const s   = data.sections_total;
    const rev = data.sections_revised != null ? data.sections_revised : null;
    el('complete-headline').textContent = '';
    const stampSub = el('stamp-sub');
    if (stampSub) {
      // Absent counts DROP the line entirely rather than degrade it: a real
      // caller already omits `sections_total`, and "? sheets · 1 revision" in
      // the APPROVED stamp is worse than omitting it. `display:none` too,
      // since `.stamp-sub`'s margin-top would still show for an empty string.
      const counted = typeof r === 'number' && typeof s === 'number';
      stampSub.textContent = counted
        ? `${s} sheet${s !== 1 ? 's' : ''} · ${r} revision${r !== 1 ? 's' : ''}`
        : '';
      stampSub.style.display = counted ? '' : 'none';
    }
    const stampMeta = el('stamp-meta');
    if (stampMeta) stampMeta.textContent = 'viva · ' + new Date().toISOString().slice(0, 10);
    // A diff that re-captured empty signed off with nothing left to approve
    // (`resolved: "empty"`, loop.py finish); say so rather than counting
    // sections that no longer exist.
    el('complete-detail').textContent   = data.resolved === 'empty'
      ? `diff fully resolved · ${rev != null ? rev : 0} hunk${rev !== 1 ? 's' : ''} revised`
      : (rev != null ? `${rev} section${rev !== 1 ? 's' : ''} revised` : '');
    const entries = (REVIEW_DATA && REVIEW_DATA.ledger) || [];
    if (entries.length) {
      el('complete-ledger').style.display = '';
      el('complete-ledger-count').textContent = entries.length;
      el('complete-ledger-rows').innerHTML = ledgerRowsHTML(entries);
    }
    document.querySelector('.bottom-bar').style.display = 'none';
  });

  es.onerror = () => {
    // The connection actually dropping is the harder, more specific signal —
    // it supersedes any still-waiting banner already shown rather than the
    // two stacking, one over a full-screen scrim.
    const waiting = el('processing-wait-banner');
    if (waiting) waiting.remove();
    showDeadSession();
  };

  // EventSource retries on its own and onerror fires every attempt, so
  // "dropped" and "gone" look identical — a successful reconnect is the only
  // thing that tells them apart: the server outlived the drop.
  es.onopen = () => { hideDeadSession(); };
}

/* ─── Command palette wiring ────────────────────────────── */
el('pal-input').addEventListener('input', e => renderPalette(e.target.value));
el('pal-open').addEventListener('click', () => openPalette());
el('qa-pal-open').addEventListener('click', () => openPalette());
el('pal-overlay').addEventListener('mousedown', e => {
  if (e.target === el('pal-overlay')) closePalette();
});

/* ─── Keyboard shortcuts ────────────────────────────────── */
function submitOnCmdEnter(e) {
  const sub = el('btn-submit');
  if (sub.classList.contains('ready') && !sub.disabled) { e.preventDefault(); sub.click(); }
}

document.addEventListener('keydown', e => {
  // Nothing on this page can reach the server any more (#174) — the
  // dead-session overlay is the one modal that doesn't close on Escape.
  // `inert` blocks pointer/Tab but not this listener, so without this
  // swallow, a/c/i and ⌘+Enter would keep mutating state behind the scrim.
  if (deadSessionIsOpen()) return;
  // ⌘K opens the palette from anywhere, including inside a textarea — a
  // reviewer mid-reply wants "jump to next open thread" without the mouse.
  // Sits ahead of the TEXTAREA/INPUT guard for that reason.
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    // Never over another dialog: the prefs panel and the recap gate are both
    // modal and both own Escape, so stacking a third would leave two things
    // claiming the same key.
    if (prefsIsOpen() || (REVIEW_DATA && recapIsOpen())) return;
    e.preventDefault();
    if (paletteIsOpen()) closePalette(); else openPalette();
    return;
  }
  // The palette is modal, and its own input is where typing goes — so its
  // keys are handled before the TEXTAREA guard would return on that input.
  if (paletteIsOpen()) {
    if (e.key === 'Escape')    { e.preventDefault(); closePalette(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); movePalette(1);  return; }
    if (e.key === 'ArrowUp')   { e.preventDefault(); movePalette(-1); return; }
    if (e.key === 'Enter') {
      e.preventDefault();
      // ⇧⏎ is printed beside `Approve all unblocked`; it runs that row
      // wherever the highlight is, or the highlighted row when it is absent.
      const all = e.shiftKey ? _palCmds.findIndex(c => c.key === '⇧⏎') : -1;
      runPalette(all >= 0 ? all : _palIdx);
      return;
    }
    return;
  }

  // Escape closes the composer from inside its own textarea — the one place
  // the TEXTAREA guard below would otherwise swallow it. An empty box
  // cancels; a box with a draft only blurs, so Escape never loses typed text.
  if (e.key === 'Escape' && !prefsIsOpen() && !(REVIEW_DATA && recapIsOpen())) {
    const pop = document.querySelector('.comment-popover.is-open');
    if (pop) {
      e.preventDefault();
      const ta = pop.querySelector('.cmt-pop-note');
      if (ta && ta.value.trim()) ta.blur(); else pop.querySelector('.cmt-cancel')?.click();
      return;
    }
  }

  /* Escape stops listening from ANYWHERE, ahead of the TEXTAREA/INPUT guard
     on purpose: staging a spoken comment puts the caret in a textarea, so
     without this the mic can't be turned off exactly when it's hottest. */
  if (e.key === 'Escape' && voiceIsOn()
      && !prefsIsOpen() && !(REVIEW_DATA && recapIsOpen())) {
    e.preventDefault(); stopVoice('you pressed Escape'); return;
  }

  const tag = document.activeElement?.tagName;
  if (tag === 'TEXTAREA' || tag === 'INPUT') return;

  // The preferences panel is reachable in every mode, so its Escape check
  // sits ahead of the REVIEW_DATA-gated block below (the recap overlay's
  // equivalent check lives inside it, since recap is review/diff-only).
  if (e.key === 'Escape' && prefsIsOpen()) { closePrefsPanel(); return; }
  // Modal, like the recap overlay: every other key is swallowed here so it
  // can't reach the card/QA shortcuts behind the backdrop. `inert` blocks
  // pointer/Tab but not this listener, and focus here is never TEXTAREA/INPUT.
  if (prefsIsOpen()) return;

  /* `v` is a MODE toggle, so it's in the theme/palette tier, not with
     `a`/`c`/`i`: it works in both interview and review, and gating on
     `rState.active` would make it dead with nothing expanded. */
  if (e.key === 'v' && !e.metaKey && !e.ctrlKey && !e.altKey && voiceSupported()) {
    e.preventDefault(); toggleVoice(); return;
  }
  // `t` is the theme control's keycap in both directories, like `v`.
  if (e.key === 't' && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault(); cycleTheme(); return;
  }

  if (REVIEW_DATA) {
    if (e.key === 'o' && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); toggleRecap(); return; }
    if (e.key === 'Escape' && recapIsOpen()) { closeRecap(); return; }
    if (recapIsOpen()) {
      // The recap is modal — card shortcuts stay inert under it; ⌘/Ctrl+Enter
      // keeps its "submit" meaning by driving the gate's own confirm control.
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); el('recap-confirm').click(); }
      return;
    }
    // The palette's other two keycaps, bound where they are printed.
    if (e.key === 'l' && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); openLedger(); return; }
    if (e.key === 'j' && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const t = nextOpenThread();
      if (t) { e.preventDefault(); activateReviewCard(t); }
      return;
    }
    // The margin's own verbs, live on the note that has focus: r/s/y/n are
    // printed on Reply / Settle / Accept / Change anyway, and a bare `s` in
    // the prose must never settle a thread the reader is not looking at.
    if (e.key.length === 1 && 'rsyn'.includes(e.key) && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const note = document.activeElement && document.activeElement.closest
        ? document.activeElement.closest('.open-thread') : null;
      const verb = note && [...note.querySelectorAll('.nt-acts .nt-btn')]
        .find(b => (b.querySelector('kbd') || {}).textContent === e.key);
      if (verb) { e.preventDefault(); verb.click(); return; }
    }
    if (e.key === 'a' && !e.metaKey && !e.ctrlKey && !e.altKey && rState.active) { e.preventDefault(); approveSection(rState.active); return; }
    // Modifier-guarded like 'a' and 'o': bare `c` opens a composer, so an
    // unguarded branch would swallow ⌘C/Ctrl+C — copy, on a page of prose.
    if (e.key === 'c' && !e.metaKey && !e.ctrlKey && !e.altKey && rState.active) { e.preventDefault(); openTypedComment(rState.active, 'changes'); return; }
    if (e.key === 'i' && !e.metaKey && !e.ctrlKey && !e.altKey && rState.active) { e.preventDefault(); openTypedComment(rState.active, 'info'); return; }
    if (e.key === 'Tab' && !e.shiftKey) {
      // Advance to the next card only while focus is inside the active card;
      // otherwise let Tab navigate natively so the skip-link, bottom-bar
      // controls, and browser chrome stay reachable (#75). Shift+Tab is always native.
      const card = rState.active ? el('rcard-' + rState.active) : null;
      if (card && card.contains(document.activeElement)) {
        e.preventDefault();
        skipReviewCard(rState.active);
        return;
      }
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { submitOnCmdEnter(e); return; }
  }

  // Guarded by !REVIEW_DATA in addition to the round handler's QA_DATA/
  // qState.active reset (#109 hand-off): belt-and-suspenders so a digit
  // keystroke can never route through the qa branch while review is on screen.
  if (!REVIEW_DATA && QA_DATA && qState.active) {
    const q = QA_DATA.questions.find(q => q.id === qState.active);
    if (q) {
      const n = parseInt(e.key, 10);
      if (!isNaN(n) && n >= 1 && n <= q.choices.length) {
        e.preventDefault();
        pickQAChoice(qState.active, q.choices[n - 1]);
        return;
      }
      // `c` confirms, the way `a` approves a section — printed on the button
      // itself, so the keyboard layer is on the control, not just the legend.
      // Free here since this whole branch is guarded on `!REVIEW_DATA`.
      if (e.key === 'c' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault(); advanceQA(qState.active); return;
      }
    }
    if (e.key === 'Tab' && !e.shiftKey) {
      const card = el('qacard-' + qState.active);
      if (card && card.contains(document.activeElement)) {
        e.preventDefault(); advanceQA(qState.active); return;
      }
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { submitOnCmdEnter(e); return; }
  }
});

// Runs immediately, not on DOMContentLoaded: this script sits at the end of
// <body>, after everything it references, so the DOM is already parsed.
// Waiting would needlessly serialize this /input fetch behind the deferred
// /vendor <script> tags, which finish before DOMContentLoaded fires anyway.
el('btn-skip').disabled   = true;
el('btn-submit').disabled = true;

// Titleblock's doc-path/doc-title cells — shared by the initial boot
// (bootReviewMode) and the in-place 'round' SSE hand-off, so a qa→review
// hand-off (#109) populates them like a fresh boot instead of leaving them blank.
function setDocTitleBlock(data, modeWord, docFallback) {
  el('doc-path').textContent    = data.doc_file || docFallback;
  el('doc-path').title          = data.doc_file || docFallback;   /* full path on hover when truncated */
  el('doc-title').innerHTML     = 'viva <em>' + modeWord + '</em>';
}

// diff2html's stylesheet and two bundles, injected only once a page turns
// diff (#198), so review and qa never fetch them. Idempotent: a later diff
// round may call it again. Versions pinned per assets/vendor/README.md.
function loadDiff2html() {
  if (el('diff2html-css')) return;
  const d2hCss = document.createElement('link');
  d2hCss.id = 'diff2html-css';
  d2hCss.rel = 'stylesheet';
  d2hCss.href = '/vendor/diff2html-3.4.56.min.css';
  const d2hJs = document.createElement('script');
  d2hJs.id = 'diff2html-script';
  d2hJs.src = '/vendor/diff2html-3.4.56.min.js';
  const d2hUi = document.createElement('script');
  d2hUi.id = 'diff2html-ui-script';
  d2hUi.src = '/vendor/diff2html-ui-slim-3.4.56.min.js';
  document.head.append(d2hCss, d2hJs, d2hUi);
  // renderDiffHunk gates on all three, so load order is moot: each arrival
  // re-renders a d2h-pending card, and the last one upgrades it.
  retryOnceScriptsLoad(['diff2html-css', 'diff2html-script', 'diff2html-ui-script'],
    '.section-content.d2h-pending');
}

// Shared boot tail for the two review-card modes (review and diff) — the
// title block, round badge, view reveal, card build, and SSE hookup are
// identical apart from the mode word and the doc-path fallback.
function bootReviewMode(data, modeWord, docFallback) {
  setDocTitleBlock(data, modeWord, docFallback);
  el('round-badge').textContent = String(data.round).padStart(2, '0');
  setTabTitle(tabDocName(data.doc_file), ...(modeWord === 'diff' ? ['diff'] : []), 'REV ' + String(data.round).padStart(2, '0'));
  setTabFavicon('turn');
  el('review-view').style.display = '';
  initReview();
  connectSSE();
}

// The preferences fetch is awaited alongside /input so the badge-to-entry
// link (PREFS_BY_ID) resolves on first paint. A failed/malformed fetch
// degrades to an empty list rather than blocking the boot — every badge
// falls back to plain rendering, the same degrade an unmatched [id] gets.
Promise.all([
  // Timed, so the footer's latency line is a measurement of this page's own
  // round trip rather than a number copied off a mock.
  timedFetch('/input').then(r => r.json()),
  fetch('/preferences').then(r => r.json()).catch(() => []),
])
  .then(([data, prefs]) => {
    TAB_REPO    = data.repo || null;   // set once at boot, every mode (#172)
    PREFS_DATA  = Array.isArray(prefs) ? prefs : [];
    PREFS_BY_ID = new Map(PREFS_DATA.map(p => [p.id, p]));
    // Ships hidden, same treatment as the confidence sort toggle
    // (references/producers.md, Confidence triage): an empty/absent store
    // has nothing to inspect or mute, so the control stays off.
    el('prefs-toggle').style.display = PREFS_DATA.length ? '' : 'none';
    el('btn-skip').disabled   = false;
    el('btn-submit').disabled = false;

    if (data.mode === 'review') {
      REVIEW_DATA = data;
      bootReviewMode(data, 'review', '');
    } else if (data.mode === 'diff') {
      REVIEW_DATA = data;
      document.body.classList.add('mode-diff');
      loadDiff2html();
      bootReviewMode(data, 'diff', 'diff');
    } else {
      // `choices` is OPTIONAL on the wire (references/qa.md) — normalized once
      // here, at the boundary, rather than guarded at each downstream reader.
      // An absent field used to throw during render, taking the whole interview down.
      QA_DATA = data;
      (QA_DATA.questions || []).forEach(q => {
        if (!Array.isArray(q.choices)) q.choices = [];
      });
      // Taste-first reorder, at the same boundary the `choices` normalization
      // above uses — see orderQAQuestions (issue #175).
      QA_DATA.questions = orderQAQuestions(QA_DATA.questions || []);
      el('qa-title').textContent        = data.context || 'Q&A phase';
      el('qa-title').title              = data.context || 'Q&A phase';   /* full topic on hover when truncated */
      el('qa-count-badge').textContent  = String(data.questions.length);
      setTabTitle(data.context || 'brainstorm');
      setTabFavicon('turn');
      // Same page cap as the review print: a column that holds a measure plus
      // a margin, and no wider. See `.mode-doc, .mode-qa` in the stylesheet.
      document.body.classList.add('mode-qa');
      el('qa-view').style.display = '';
      initQA();
      connectSSE();
    }
  })
  .catch(err => {
    document.body.innerHTML = '<p class="load-error">Failed to load session data: ' + (err.message || 'network error') + '</p>';
  });
