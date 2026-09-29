/* ═══ Voice — the oral examination (input only) ══════════════════════════
   Speech may command but never author: a bare verb acts immediately, while a
   note/question/suggestion is STAGED in the composer and never reaches
   `addComment` until confirmed (tests/test_server_voice.py). Off by default. ══ */

// Injected from server.py's `_VOICE_RULES`, longest phrase first — see the
// table there for the two verb classes. Injected rather than restated for the
// reason `__CHECK_KINDS__` is: a second copy in JS drifts, silently.
const VOICE_RULES = __VOICE_RULES__;

const VoiceCtor = window.SpeechRecognition || window.webkitSpeechRecognition || null;
const VOICE_ACK_KEY = 'viva-voice';
// The three verbs that must still work while a note field holds the caret.
const VOICE_ESCAPES = ['save', 'cancel', 'stop'];

let _rec = null;          // constructed on first start, never at load
let _voiceOn = false;     // the reviewer's switch, not the recognizer's state
let _voiceRestarts = 0;
// The card a spoken verb last applied to. See `voiceReopen` — this is the way
// back out of the state where nothing is open.
let _voiceLastCard = null;

function voiceSupported() { return Boolean(VoiceCtor); }
function voiceIsOn() { return _voiceOn; }

/* Lowercase, punctuation to spaces, whitespace collapsed — the form every
   phrase in the table is written in. Known limitation: a hyphen inside a
   leading word can split it in two; never observed in practice, so left
   unguarded rather than fixed. */
function normalizeUtterance(raw) {
  return String(raw || '').toLowerCase().replace(/[^a-z0-9\s']/g, ' ')
                          .replace(/\s+/g, ' ').trim();
}

// First match wins, and the table is sorted longest-first, so "request
// changes …" can never be read as the verb `changes` carrying "request".
function matchVoiceRule(norm) {
  for (let i = 0; i < VOICE_RULES.length; i++) {
    const r = VOICE_RULES[i];
    if (norm === r.phrase || (r.carries && norm.indexOf(r.phrase + ' ') === 0)) {
      return { rule: r, words: r.phrase.split(' ').length };
    }
  }
  return null;
}

// The reviewer's own text, in their own casing — taken off the RAW utterance,
// never the normalized one, because the note is what goes on the record.
function remainderOf(raw, words) {
  return String(raw).trim().split(/\s+/).slice(words).join(' ')
                    .replace(/^[\s,:;.!?-]+/, '');
}

function activeNoteField() {
  const a = document.activeElement;
  return a && a.classList && a.classList.contains('note-field') ? a : null;
}

function blurNoteField() { const f = activeNoteField(); if (f) f.blur(); }

// Land dictated text at the caret rather than replacing the box: a reviewer
// who typed half a sentence and finished it out loud keeps both halves.
function insertDictation(field, text) {
  const cur = field.value;
  const at = field.selectionStart == null ? cur.length : field.selectionStart;
  const before = cur.slice(0, at), after = cur.slice(at);
  const sep = before && !/\s$/.test(before) ? ' ' : '';
  field.value = before + sep + text + after;
  const pos = (before + sep + text).length;
  try { field.setSelectionRange(pos, pos); } catch (e) { /* detached field */ }
  // The Q&A note wires its state off `input`; the review composer reads
  // `.value` at save time. Firing the event serves the first and is inert for
  // the second, so one line covers both surfaces.
  field.dispatchEvent(new Event('input', { bubbles: true }));
}

/* ─── The strip ───────────────────────────────────────────
   Every utterance prints here with the reading it got, including ones that
   matched no verb — a reviewer must be able to tell "heard nothing" from
   "heard something and ignored it". */
function voiceSay(state, heard, read) {
  const strip = el('voice-strip');
  if (!strip) return;
  strip.style.display = '';
  strip.innerHTML = '<span class="vs-state">' + esc(state) + '</span>'
    + (heard ? '<span class="vs-heard">&ldquo;' + esc(String(heard).trim()) + '&rdquo;</span>' : '')
    + (read  ? '<span class="vs-read">' + esc(read) + '</span>' : '');
}

// Interim results are shown so the reviewer sees it hearing them, but
// `aria-hidden` so a live region doesn't announce every partial guess aloud —
// voiceSay (the final reading) is what announces.
function voiceInterim(text) {
  const strip = el('voice-strip');
  if (!strip) return;
  strip.style.display = '';
  strip.innerHTML = '<span class="vs-state">listening</span>'
    + '<span class="vs-interim" aria-hidden="true">' + esc(text) + '</span>';
}

function hideVoiceStrip() {
  const s = el('voice-strip');
  if (s) { s.style.display = 'none'; s.innerHTML = ''; }
}

/* ─── Routing one utterance ───────────────────────────────── */
// Is there still a round on screen? processing/complete views leave
// REVIEW_DATA set and rState.active null, the same state voiceReopen treats
// as "reopen where you were" — without this a verb walks stale cards.
function voiceRoundIsLive() {
  const shown = id => { const n = el(id); return n && n.style.display !== 'none'; };
  return !shown('processing-view') && !shown('complete-view');
}

function handleUtterance(raw) {
  const norm = normalizeUtterance(raw);
  if (!norm) return;
  /* Same terminal/modal guards as the keydown handler, same order (#174):
     speech is a second input path into the same verdict state. Terminal
     states also turn the mic off; between-rounds is not terminal, so the
     utterance is refused but the mic stays on. */
  if (deadSessionIsOpen()) { stopVoice('the session ended'); return; }
  if (!voiceRoundIsLive()) {
    voiceSay('heard', raw, 'no round on screen yet — nothing to command');
    return;
  }
  if (prefsIsOpen()) {
    voiceSay('heard', raw, 'the preferences panel is open — close it first');
    return;
  }
  if (REVIEW_DATA && recapIsOpen()) {
    voiceSay('heard', raw, 'the recap is open — confirm or close it by hand');
    return;
  }
  const hit = norm ? matchVoiceRule(norm) : null;
  const field = activeNoteField();
  if (field) {
    if (hit && norm === hit.rule.phrase && VOICE_ESCAPES.indexOf(hit.rule.act) >= 0) {
      voiceSay('heard', raw, runVoiceAct(hit.rule, ''));
      return;
    }
    insertDictation(field, String(raw).trim());
    voiceSay('dictated', raw, 'into the open note');
    return;
  }
  if (!hit) {
    // Named in the vocabulary of the page actually on screen: an interview has
    // no sections to approve, and offering the review's verbs there teaches the
    // reviewer the wrong three words.
    voiceSay('heard', raw, REVIEW_DATA
      ? 'no command — try "approve", "request changes …", "next"'
      : 'no command — try "question …", "next", or press dictate to answer aloud');
    return;
  }
  voiceSay('heard', raw, runVoiceAct(hit.rule, remainderOf(raw, hit.words)));
}

// Every branch RETURNS what it did, in the reviewer's words, and the strip
// prints it. A verb that acted silently would be indistinguishable from one
// that was misheard.
function runVoiceAct(rule, rest) {
  if (rule.act === 'stop') { stopVoice('you said so'); return 'stopped listening'; }
  const pop = document.querySelector('.comment-popover.is-open');
  if (rule.act === 'save') {
    if (pop) { pop.querySelector('.cmt-save').click(); return 'saved the comment'; }
    blurNoteField(); return 'nothing staged — closed the note';
  }
  if (rule.act === 'cancel') {
    if (pop) { pop.querySelector('.cmt-cancel').click(); return 'discarded the comment'; }
    blurNoteField(); return 'nothing staged — closed the note';
  }
  if (REVIEW_DATA) return runReviewVoiceAct(rule, rest);
  if (QA_DATA)     return runQAVoiceAct(rule, rest);
  return 'nothing on screen to command';
}

function runReviewVoiceAct(rule, rest) {
  /* "submit" is an alias for the RECAP, never for submitting. Ending the round
     is the one action this page already gates behind an overlay and a confirm
     click, and a spoken word does not get to skip a gate the mouse cannot. */
  if (rule.act === 'recap') { openRecap(); return 'opened the recap — confirm there to submit'; }
  const secs = REVIEW_DATA.sections;
  const id = rState.active;
  /* Approving/skipping the LAST open card leaves nothing active, and a
     hands-free reviewer has no click to reopen one — so the first verb spoken
     into that state REOPENS where they were instead of erroring forever. */
  if (!id) return voiceReopen();
  _voiceLastCard = id;
  const n = secs.findIndex(s => s.id === id) + 1;
  if (rule.act === 'approve') {
    // Same refusal the approve button carries, said out loud: a section
    // holding live comments is not one anybody can sign off.
    if (activeComments(id).length) return 'section ' + n + ' has open comments — settle them first';
    approveSection(id);
    return 'approved section ' + n;
  }
  if (rule.act === 'next') { skipReviewCard(id); return 'moved on from section ' + n; }
  if (rule.act === 'back') {
    const prev = secs[(secs.findIndex(s => s.id === id) - 1 + secs.length) % secs.length];
    activateReviewCard(prev.id);
    return 'back to section ' + (secs.indexOf(prev) + 1);
  }
  if (rule.act === 'comment') return stageVoiceComment(id, rule.type, rest);
  return 'no command';
}

// Reopen the last card a spoken verb touched, or the first if none yet.
// Carried sections have no accordion, so this walks forward to one that
// does rather than reporting success on a card that never appears.
function voiceReopen() {
  const secs = REVIEW_DATA.sections;
  const start = Math.max(0, secs.findIndex(s => s.id === _voiceLastCard));
  const order = [...secs.slice(start), ...secs.slice(0, start)];
  const hit = order.find(s => {
    const card = el('rcard-' + s.id);
    return card && !card.classList.contains('is-carried');
  });
  if (!hit) return 'nothing left open — use the recap to submit';
  _voiceLastCard = hit.id;
  activateReviewCard(hit.id);
  return 'reopened section ' + (secs.indexOf(hit) + 1) + ' — say the verb again';
}

/* The load-bearing half. Opens the composer, drops the transcript in its box,
   leaves the caret there — and stops. The reviewer reads what the recognizer
   heard and says "save" (or clicks it) to make it a comment. */
function stageVoiceComment(id, type, rest) {
  openCommentPopover(id, { type });
  const pop = el('rpop-' + id);
  const ta = pop && pop.querySelector('.cmt-pop-note');
  if (!ta) return 'could not open the composer';
  // Report what the box actually BECAME, not what was asked for: `suggest
  // wording` has no chip in diff mode, so the composer stays on `changes` and
  // saying otherwise would be a lie about the record being written.
  const became = pop.dataset.type;
  if (rest) insertDictation(ta, rest);
  // `preventScroll`, like the opener's own focus: openCommentPopover already
  // scrolled the popover into view, and a bare re-focus here would scroll
  // back to the field, leaving the type chips and quote above the fold.
  ta.focus({ preventScroll: true });
  return 'staged ' + (/^[aeiou]/.test(became) ? 'an ' : 'a ') + became
       + ' comment — say "save" to keep it';
}

function runQAVoiceAct(rule, rest) {
  if (rule.act === 'recap') return 'the interview has no recap gate';
  const qs = QA_DATA.questions;
  const id = qState.active;
  // Confirming the last question closes everything, the same dead end
  // `voiceReopen` exists for on the review page — and the same way out.
  if (!id) {
    const start = Math.max(0, qs.findIndex(q => q.id === _voiceLastCard));
    _voiceLastCard = qs[start].id;
    activateQACard(qs[start].id);
    return 'reopened question ' + (start + 1) + ' — say the verb again';
  }
  _voiceLastCard = id;
  const n = qs.findIndex(q => q.id === id) + 1;
  // The interview's own verb for "I am done here" is confirm, and it is the
  // same move as skip — `advanceQA` is what both buttons call.
  if (rule.act === 'next' || rule.act === 'approve') { advanceQA(id); return 'moved on from question ' + n; }
  if (rule.act === 'back') {
    const prev = qs[(qs.findIndex(q => q.id === id) - 1 + qs.length) % qs.length];
    activateQACard(prev.id);
    return 'back to question ' + (qs.indexOf(prev) + 1);
  }
  if (rule.act === 'comment') {
    const ta = el('qnote-' + id);
    if (!ta) return 'this question has no note field';
    if (rest) insertDictation(ta, rest);
    ta.focus();
    return 'added to question ' + n + '’s note';
  }
  return 'no command in the interview';
}

/* ─── The switch ──────────────────────────────────────────── */
function voiceAcknowledged() {
  try { return localStorage.getItem(VOICE_ACK_KEY) === 'ack'; } catch (e) { return false; }
}

/* Disclosed once rather than buried: the browser's recognizer is a network
   service — viva stays keyless and stores no audio, but the audio does
   leave the machine. The reviewer decides with that fact in front of them. */
function showVoiceNotice(after) {
  const strip = el('voice-strip');
  if (!strip) return;
  strip.style.display = '';
  strip.innerHTML = '<span class="vs-state">voice</span>'
    + '<span class="voice-notice">Your browser’s speech recognition sends audio to its vendor '
    + '(Google, in Chrome). viva itself stays keyless and keeps no recording.'
    + '<button type="button" id="voice-ack">start listening</button>'
    + '<button type="button" id="voice-nack">not now</button></span>';
  el('voice-ack').addEventListener('click', () => {
    try { localStorage.setItem(VOICE_ACK_KEY, 'ack'); } catch (e) { /* holds for this tab */ }
    beginVoice(after);
  });
  el('voice-nack').addEventListener('click', () => hideVoiceStrip());
  el('voice-ack').focus();
}

function startVoice(after) {
  if (!voiceSupported()) return;
  if (_voiceOn) { if (after) after(); return; }
  if (!voiceAcknowledged()) { showVoiceNotice(after); return; }
  beginVoice(after);
}

function beginVoice(after) {
  _voiceOn = true;
  _voiceRestarts = 0;
  paintVoiceToggle();
  ensureRecognizer();
  try { _rec.start(); } catch (e) { /* already starting; onstart still fires */ }
  voiceSay('listening', '', 'say "approve", "request changes …", "next" — Escape or "stop" to end');
  if (after) after();
}

function stopVoice(why) {
  const wasOn = _voiceOn;
  _voiceOn = false;                       // read by onend — set BEFORE stop()
  paintVoiceToggle();
  if (_rec) { try { _rec.stop(); } catch (e) { /* never started */ } }
  if (wasOn) voiceSay('off', '', why || 'microphone off');
}

function toggleVoice() {
  if (_voiceOn) stopVoice('you turned it off'); else startVoice();
}

function ensureRecognizer() {
  if (_rec) return;
  _rec = new VoiceCtor();
  _rec.continuous = true;
  _rec.interimResults = true;
  _rec.lang = document.documentElement.lang || 'en-US';

  _rec.onresult = e => {
    _voiceRestarts = 0;                   // real speech: the storm guard resets
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) handleUtterance(r[0].transcript);
      else interim += r[0].transcript;
    }
    if (interim.trim()) voiceInterim(interim.trim());
  };

  _rec.onerror = ev => {
    // A refused microphone is terminal — restarting just re-refuses. Everything
    // else (`no-speech`, `aborted`, `network`) is transient and `onend`, which
    // always follows, is the one place that decides whether to come back.
    if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
      stopVoice('the browser refused microphone access');
    }
  };

  _rec.onend = () => {
    if (!_voiceOn) return;                // the reviewer's switch wins
    /* A "continuous" recognizer is really a restarted one — Chrome ends the
       session after silence. The counter is a storm guard against an
       invisible infinite restart loop; it resets on every real result. */
    if (_voiceRestarts >= 8) { stopVoice('the recognizer kept dropping'); return; }
    _voiceRestarts++;
    setTimeout(() => { if (_voiceOn) { try { _rec.start(); } catch (e) {} } }, 250);
  };
}

function paintVoiceToggle() {
  const btn = el('voice-toggle');
  if (!btn) return;
  btn.textContent = 'voice: ' + (_voiceOn ? 'listening' : 'off');
  btn.classList.toggle('is-live', _voiceOn);
  /* The label states which state is ON, so the accessible name must say what
     the button DOES — same rule the theme toggle follows, so a screen reader
     can tell a state from an action. */
  btn.setAttribute('aria-label', _voiceOn
    ? 'Voice input is listening. Activate to stop listening.'
    : 'Voice input is off. Activate to start the oral examination.');
  document.querySelectorAll('.mic-btn').forEach(m => m.classList.toggle('is-live', _voiceOn));
}

function initVoice() {
  // No control where there is no recognizer: a button that cannot work is
  // worse than no button, and every caller already asks `voiceSupported()`
  // before drawing its own.
  if (!voiceSupported()) return;
  el('voice-toggle').style.display = '';
  paintVoiceToggle();
  el('voice-toggle').addEventListener('click', () => toggleVoice());
}
initVoice();
/* ═══ End voice ══════════════════════════════════════════════════════════ */
