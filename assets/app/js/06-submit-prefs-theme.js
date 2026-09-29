/* ─── Submit handlers ──────────────────────────────────────── */
// Between-rounds snapshot — the changes/info rows just sent, captured from
// rState so the 'processing' view can echo them back verbatim. In-memory
// only (never written to .viva/): a tab reload re-boots into the prior round.
let betweenRounds = null;

function snapshotBetweenRounds() {
  betweenRounds = {
    round: REVIEW_DATA.round,
    // A suggestion's note is optional, so the processing card falls back to the
    // wording — a row reading only its section title says nothing.
    rows: REVIEW_DATA.sections.flatMap(s =>
      activeComments(s.id).map(c => ({ sectionTitle: s.title, type: c.type,
                                       note: c.note || c.replacement || '' })))
  };
}

// POST a round/answer payload to /submit, surface failure, and re-enable the
// bar so the reviewer can retry. fetch() resolves (never rejects) on a
// 4xx/5xx, so a non-2xx is turned into a throw here. On success the buttons
// stay disabled — the SSE 'processing'/'round' events drive the next view.
function sendSubmit(result) {
  // The one choke point every submit passes through, review and qa alike.
  // The server behind this tab is gone (#174); a POST here fails into the
  // same silent nothing the dead-session overlay exists to end.
  if (deadSessionIsOpen()) return;
  fetch('/submit', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(result)
  })
    .then(r => {
      // 409: the server serves a different round than this tab holds (#199).
      // Nothing was written; retrying from this tab would be refused again.
      if (r.status === 409) return r.json().then(b => showRoundStale(b.current || {}));
      if (!r.ok) throw new Error('server returned ' + r.status);
    })
    .catch(err => {
      alert('Submit failed: ' + (err.message || 'network error'));
      el('btn-skip').disabled   = false;
      el('btn-submit').disabled = false;
    });
}

function submitReview(early) {
  el('btn-skip').disabled   = true;
  el('btn-submit').disabled = true;
  snapshotBetweenRounds();  // before the POST — 'processing' renders from it
  const result = {
    // The round's identity (#199): a session's spec round N and diff round 1
    // share one server, and `mode` is what tells them apart.
    round: REVIEW_DATA.round,
    mode: REVIEW_DATA.mode,
    submitted_early: early,
    sections: REVIEW_DATA.sections.map(s => {
      const v = rState.verdicts[s.id] || {};
      const comments = v.comments || [];
      const verdict = deriveVerdict(s.id);
      return { id: s.id, verdict,
               ...(comments.length && { comments }) };
    })
  };
  sendSubmit(result);
}

function submitQA(early) {
  el('btn-skip').disabled   = true;
  el('btn-submit').disabled = true;
  // Images on a question with no selected choice would be silently dropped by
  // the choice filter below — warn before discarding them.
  if (!early) {
    const orphaned = QA_DATA.questions.filter(
      q => qState.answers[q.id]?.images?.length && !qaAnswered(q.id)
    );
    if (orphaned.length &&
        !confirm(orphaned.length + ' question(s) have an attached image but no selected choice — their images will be dropped. Continue?')) {
      el('btn-skip').disabled   = false;
      el('btn-submit').disabled = false;
      return;
    }
  }
  const result = {
    answers: QA_DATA.questions
      .filter(q => qaAnswered(q.id))
      .map(q => {
        const a = qState.answers[q.id];
        return { id: q.id, choice: a.choice || '', note: a.note || '',
                 ...(a.images && a.images.length && { images: a.images }) };
      }),
    submitted_early: early
  };
  sendSubmit(result);
}

el('btn-skip').addEventListener('click', () => {
  if (REVIEW_DATA) submitReview(true);
  else             submitQA(true);
});

el('btn-submit').addEventListener('click', () => {
  if (el('btn-submit').classList.contains('disabled')) return;
  // Review/diff route through the recap gate — only #recap-confirm calls
  // submitReview(false). Q&A keeps its direct done → path.
  if (REVIEW_DATA) openRecap();
  else             submitQA(false);
});

/* ─── Recap overlay — the submit gate (review/diff modes) ────
   btn-submit's ready click opens this index of every section instead of
   submitting; only #recap-confirm calls submitReview(false). `o` toggles it,
   Escape closes it. Q&A ships no recap — done → wires straight to submitQA. */
const RECAP_VERDICTS = {
  approved: { dot: 'dot-approved', cls: 'rv-approved', label: 'approved' },
  changes: { dot: 'dot-changes', cls: 'rv-changes', label: 'changes' },
  info: { dot: 'dot-info', cls: 'rv-info', label: 'info' },
  pending: { dot: 'dot-idle', cls: 'rv-pending', label: 'pending' },
};

function recapRowsHTML() {
  // Numbered as the print numbers them — `1 ·`, not the machine's `s1`.
  return REVIEW_DATA.sections.map((s, i) => {
    const v = RECAP_VERDICTS[deriveVerdict(s.id)] || RECAP_VERDICTS.pending;
    const notes = activeComments(s.id).length;
    return '<button type="button" class="recap-row" data-target="' + esc(s.id) + '">'
      + '<span class="recap-id">' + (i + 1) + '</span>'
      + '<span class="recap-row-title">' + esc(s.title) + '</span>'
      + '<span class="recap-verdict ' + v.cls + '"><span class="dot ' + v.dot + '" aria-hidden="true"></span>' + v.label + '</span>'
      + '<span class="recap-notes">' + (notes ? notes + ' note' + (notes === 1 ? '' : 's') : '&mdash;') + '</span>'
      + '</button>';
  }).join('');
}

function recapIsOpen() { return el('recap-overlay').style.display !== 'none'; }

function openRecap() {
  // Q&A ships no recap, and a hidden review-view (processing/complete) has
  // nothing to recap — the `o` shortcut lands here too, not just the
  // class-gated btn-submit click.
  if (!REVIEW_DATA || el('review-view').style.display === 'none') return;
  if (prefsIsOpen()) closePrefsPanel();   // only one modal open at a time
  el('recap-round').textContent = String(REVIEW_DATA.round).padStart(2, '0');
  const grid = el('recap-grid');
  grid.innerHTML = recapRowsHTML();
  grid.querySelectorAll('.recap-row').forEach(btn => {
    btn.addEventListener('click', () => { closeRecap(); activateReviewCard(btn.dataset.target); });
  });
  // Mirrors btn-submit's readiness so a recap opened via `o` can't submit a
  // round the bottom bar wouldn't, and can't re-arm a duplicate POST while
  // one is already in flight (`.disabled`).
  const ready = el('btn-submit').classList.contains('ready') && !el('btn-submit').disabled;
  el('recap-confirm').className = 'btn-submit ' + (ready ? 'ready' : 'disabled');
  el('recap-confirm').setAttribute('aria-disabled', ready ? 'false' : 'true');
  // Three states, reason printed rather than inferred: `disabled` is the
  // IN-FLIGHT signal, `pending` is the not-ready count.
  const inFlight = el('btn-submit').disabled;
  const pending = REVIEW_DATA.sections.filter(s => deriveVerdict(s.id) === 'pending').length;
  // btn-skip does the same job in the bar, but the bar is inert behind this
  // modal, so naming it in copy would not be enough.
  const canSkip = pending > 0 && !inFlight;
  el('recap-skip').style.display = canSkip ? '' : 'none';
  el('recap-blocked').textContent = inFlight ? 'submitted — the agent is revising'
                                  : pending ? pending + ' of ' + REVIEW_DATA.sections.length + ' unreviewed'
                                  : '';
  el('recap-overlay').style.display = '';
  setBackgroundInert(true);   // trap focus + block interaction behind the modal
  // Focus the confirm when it can act, otherwise the close — NEVER the skip;
  // that let `o` then Enter dispatch a round with every section unreviewed.
  // Runs AFTER both display flips above — focus() on `display:none` no-ops.
  (ready ? el('recap-confirm') : el('recap-close')).focus();
}

// The recap is a modal (aria-modal="true"): mark everything behind it inert
// while open, so Tab and background clicks can't reach it. A focus trap
// without hand-rolled Tab-wrap bookkeeping.
function setBackgroundInert(on) {
  ['skip-link-a', 'paper', 'bottom-bar-el'].forEach(id => {
    const node = el(id);
    if (node) node.inert = on;
  });
}

function closeRecap() {
  const overlay = el('recap-overlay');
  if (overlay.style.display === 'none') return;
  const hadFocus = overlay.contains(document.activeElement);
  overlay.style.display = 'none';
  setBackgroundInert(false);   // clear inert BEFORE restoring focus — focus()
                               // on an element inside an inert subtree no-ops
  // Don't strand keyboard focus on the now-hidden overlay.
  if (hadFocus) el('btn-submit').focus();
}

function toggleRecap() { if (recapIsOpen()) closeRecap(); else openRecap(); }

el('recap-confirm').addEventListener('click', () => {
  // Belt-and-suspenders with openRecap's readiness mirror: never submit while
  // one is already in flight (btn-submit.disabled), so a fast reopen can't
  // fire a duplicate POST between submit and the 'processing'/'round' events.
  if (el('recap-confirm').classList.contains('disabled') || el('btn-submit').disabled) return;
  closeRecap();
  submitReview(false);
});
el('recap-close').addEventListener('click', closeRecap);
// The bar's early-submit escape hatch, reachable from inside the modal:
// setBackgroundInert marks #bottom-bar-el inert, so `btn-skip` itself cannot
// be clicked or tabbed to while the recap is open.
el('recap-skip').addEventListener('click', () => {
  if (el('btn-submit').disabled) return;   // never a second POST for a round in flight
  closeRecap();
  submitReview(true);
});
el('recap-overlay').addEventListener('click', e => {
  if (e.target === el('recap-overlay')) closeRecap();   /* backdrop click */
});

/* ─── Preferences panel — view/mute learned preferences (#142) ───
   #prefs-overlay mirrors the recap overlay's modal shape (role="dialog"
   aria-modal, Escape/backdrop/close, setBackgroundInert) but is independent:
   at most one of the two is ever open. Reachable in every mode, unlike recap. */
let _prefsTriggerEl = null;

function prefsIsOpen() { return el('prefs-overlay').style.display !== 'none'; }

function prefStatusLabel(status) {
  return status === 'standing' ? 'standing' : status === 'muted' ? 'muted' : 'candidate';
}

// Static recovery copy for a muted row — mute is one-way from this panel
// (decision prefs-inspector-1), so the CLI command that reverses it must be
// visible on the row itself. Badges already shown this round stay as a
// record; the copy makes no claim about whether this round's rewrite saw it.
function prefMutedNoteHTML(id) {
  return '<div class="pref-muted-note">muted &mdash; badges already shown this round '
    + 'stay as a record; nothing further is flagged or applied for this preference. '
    + 'restore from a terminal: <code>python3 "__PREFS_SCRIPT_PATH__" set '
    + '--store "__PREFS_STORE_PATH__" --id ' + esc(id) + ' --status standing</code></div>';
}

function prefRowHTML(p) {
  const status   = prefStatusLabel(p.status);
  const sessions = p.sessions || [];
  const obs      = p.observations || 0;
  const meta = sessions.length
    ? obs + ' observation' + (obs === 1 ? '' : 's') + ' &middot; seen in ' + sessions.length
      + ' session' + (sessions.length === 1 ? '' : 's') + ': ' + esc(sessions.join(', '))
    : 'no sessions recorded yet';
  const muteBtn = status === 'standing'
    ? '<button type="button" class="pref-mute-btn" data-id="' + esc(p.id) + '">mute</button>'
    : '';
  const mutedNote = status === 'muted' ? prefMutedNoteHTML(p.id) : '';
  return '<div class="pref-row" id="pref-row-' + esc(p.id) + '" data-id="' + esc(p.id)
    + '" data-status="' + esc(status) + '" tabindex="-1">'
    + '<div class="pref-row-head">'
    +   '<span class="pref-status pref-status-' + esc(status) + '">' + esc(status) + '</span>'
    +   '<span class="pref-label">' + esc(p.label || p.id) + '</span>'
    +   muteBtn
    + '</div>'
    + (p.guidance ? '<div class="pref-guidance">' + esc(p.guidance) + '</div>' : '')
    + '<div class="pref-meta">' + meta + '</div>'
    + mutedNote
    + '</div>';
}

function renderPrefsList() {
  el('prefs-list').innerHTML = PREFS_DATA.length
    ? PREFS_DATA.map(prefRowHTML).join('')
    : '<p class="prefs-empty">No preferences learned yet.</p>';
  el('prefs-list').querySelectorAll('.pref-mute-btn').forEach(btn => {
    btn.addEventListener('click', () => mutePreference(btn.dataset.id));
  });
}

// Mutates the one row's DOM in place, never a list rebuild, so a mute never
// disturbs scroll position or any other row.
function markPrefRowMuted(id) {
  const row = el('pref-row-' + id);
  if (!row) return;
  const statusEl = row.querySelector('.pref-status');
  if (statusEl) { statusEl.textContent = 'muted'; statusEl.className = 'pref-status pref-status-muted'; }
  const btn = row.querySelector('.pref-mute-btn');
  if (btn) btn.remove();
  if (!row.querySelector('.pref-muted-note')) row.insertAdjacentHTML('beforeend', prefMutedNoteHTML(id));
  row.dataset.status = 'muted';
}

function mutePreference(id) {
  const row = el('pref-row-' + id);
  const btn = row && row.querySelector('.pref-mute-btn');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const prevLabel = btn.textContent;
  btn.textContent = 'muting…';
  fetch('/preferences/mute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: id }),
  })
    .then(r => r.json().then(respBody => {
      if (!r.ok || !respBody.ok) throw new Error((respBody && respBody.error) || 'mute failed');
    }))
    .then(() => {
      const pref = PREFS_BY_ID.get(id);
      if (pref) pref.status = 'muted';
      markPrefRowMuted(id);
      el('prefs-status').textContent = ((pref && pref.label) || id)
        + ' muted — badges already shown this round stay as a record; nothing further is flagged or applied for it.';
    })
    .catch(err => {
      btn.disabled = false;
      btn.textContent = prevLabel;
      el('prefs-status').textContent = 'Could not mute — ' + (err.message || 'request failed') + '.';
    });
}

function openPrefsPanel(triggerEl, focusPrefId) {
  if (recapIsOpen()) closeRecap();   // only one modal open at a time
  el('prefs-status').textContent = '';   // clear a stale mute announcement from a prior open
  renderPrefsList();
  _prefsTriggerEl = triggerEl || el('prefs-toggle');
  el('prefs-overlay').style.display = '';
  setBackgroundInert(true);
  const row = focusPrefId && el('pref-row-' + focusPrefId);
  if (row) { row.scrollIntoView({ block: 'center' }); row.focus(); }
  else      { el('prefs-close').focus(); }
}

function closePrefsPanel() {
  const overlay = el('prefs-overlay');
  if (overlay.style.display === 'none') return;
  const hadFocus = overlay.contains(document.activeElement);
  overlay.style.display = 'none';
  setBackgroundInert(false);   // clear inert BEFORE restoring focus, same order as closeRecap
  if (hadFocus) (_prefsTriggerEl || el('prefs-toggle')).focus();
}

/* ─── Theme toggle ──────────────────────────────────────────
   Three states, cycled: system → light → dark → system. "system" is the
   absence of the attribute, not a third value — falls back to
   `prefers-color-scheme`. The pre-paint script in <head> applies a stored
   choice; this only changes it, writing the same key. */
const THEME_CYCLE = [null, 'light', 'dark'];

function currentTheme() {
  const t = document.documentElement.dataset.theme;
  return (t === 'light' || t === 'dark') ? t : null;
}

function paintThemeToggle() {
  const t = currentTheme();
  const btn = el('theme-toggle');
  btn.textContent = 'theme: ' + (t || 'system');
  /* The label states which theme is ON, so the accessible name has to say
     what the button DOES — otherwise a screen reader hears "theme: dark" and
     cannot tell whether that is the state or the action. */
  const next = THEME_CYCLE[(THEME_CYCLE.indexOf(t) + 1) % THEME_CYCLE.length];
  btn.setAttribute('aria-label',
    'Theme: ' + (t || 'following system') + '. Activate to switch to ' + (next || 'follow system') + '.');
}

function cycleTheme() {
  const next = THEME_CYCLE[(THEME_CYCLE.indexOf(currentTheme()) + 1) % THEME_CYCLE.length];
  if (next) document.documentElement.dataset.theme = next;
  else delete document.documentElement.dataset.theme;
  try {
    if (next) localStorage.setItem('viva-theme', next);
    else localStorage.removeItem('viva-theme');
  } catch (e) { /* no storage: the choice holds for this tab only */ }
  paintThemeToggle();
}

el('theme-toggle').addEventListener('click', cycleTheme);
paintThemeToggle();

