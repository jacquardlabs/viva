#!/usr/bin/env python3
"""The voice layer on the page it ships in — the two invariants and the wiring.

There is no JS engine in this suite (stdlib only, no npm), so the behavior is
read off the page the server actually serves, the same split
`test_server_a11y.py` and `test_server_verdict_shortcuts.py` use. What is pinned:

**Invariant 1 — speech may command, it may never author.** The layer stages a
transcript in the comment composer and stops; the reviewer reads it and saves.
Nothing in the voice block calls `addComment`, and nothing in it submits. A
recognizer's guess that became a comment on its own would put a sentence the
human never read into a ledger PRODUCT.md promises is verbatim, and a spoken
word would skip the recap gate a mouse cannot skip.

**Invariant 2 — off unless turned on.** No recognizer is constructed at load,
the toggle ships hidden, and where the browser has no recognizer no control is
drawn at all. PRODUCT.md principle 4: a reviewer who never enables voice must
get exactly the prior page.

Plus the wiring that makes it usable rather than merely present: Escape reaches
the switch from inside a textarea (the one place the caret is when the
microphone is hot), `v` works in the interview as well as the review, and the
transcript is announced without announcing every partial guess.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import server  # noqa: E402
from _server_harness import get_text, launch_server  # noqa: E402

HTML = server.HTML

ROUND_1 = {
    "mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
    "sections": [{"id": "s1", "title": "Goals", "content": "## Goals\nbody\n"}],
}


def _voice_block() -> str:
    """The voice layer, banner to banner — see test_voice_grammar._voice_block
    for why every needle below is anchored inside it rather than searched for
    across a 7,000-line frontend."""
    start = HTML.index("/* ═══ Voice — the oral examination")
    end = HTML.index("/* ═══ End voice")
    return HTML[start:end]


def test_grammar_is_injected_not_restated():
    assert "__VOICE_RULES__" not in HTML, "the grammar placeholder was not substituted"
    marker = "const VOICE_RULES = "
    raw = HTML[HTML.index(marker) + len(marker):]
    rules = json.loads(raw[:raw.index(";\n")])
    assert rules == [dict(r) for r in server._VOICE_RULES], \
        "the page must carry server._VOICE_RULES verbatim, not a hand-kept copy"
    assert rules[0]["phrase"] == "request changes", \
        "longest phrase first survives the JSON round-trip"
    print("  ok  test_grammar_is_injected_not_restated")


def test_speech_stages_a_comment_and_never_commits_one():
    """Invariant 1, at the seam that enforces it.

    `stageVoiceComment` opens the composer and fills its box. The save button
    already there is the only thing that makes a comment, so a transcript
    reaches the ledger only after a human read it — which is what keeps a
    spoken review inside "verbatim, not summarized".
    """
    block = _voice_block()
    assert "addComment(" not in block, (
        "the voice layer must never call addComment — a transcript that "
        "becomes a comment on its own is a sentence in the ledger nobody read")
    assert "openCommentPopover(id, { type });" in block, \
        "a spoken comment must route through the composer, like `c` and `i` do"
    assert ".cmt-save').click()" in block, \
        "'save' must drive the composer's own control, not a second save path"
    # The staged type is reported back as what the box BECAME: `suggest wording`
    # has no chip in diff mode, so asking for it does not make it so.
    assert "const became = pop.dataset.type;" in block
    print("  ok  test_speech_stages_a_comment_and_never_commits_one")


def test_spoken_submit_opens_the_recap_and_does_not_submit():
    block = _voice_block()
    assert "openRecap()" in block, "'submit' must open the gate"
    for forbidden in ("submitReview(", "submitQA(", "btn-submit"):
        assert forbidden not in block, (
            "the voice layer must not reach past the recap gate (%s): ending "
            "the round is confirmed by hand, spoken or not" % forbidden)
    # And the alias is in the table, so the reviewer's natural word still works.
    assert any(r["phrase"] == "submit" and r["act"] == "recap"
               for r in server._VOICE_RULES)
    print("  ok  test_spoken_submit_opens_the_recap_and_does_not_submit")


def test_nothing_starts_until_the_reviewer_starts_it():
    """Invariant 2 — three independent halves, because any two alone leak."""
    block = _voice_block()
    # 1. No recognizer at load: constructed once, inside the start path.
    assert HTML.count("new VoiceCtor()") == 1, \
        "the recognizer must be constructed in exactly one place"
    assert block.index("function ensureRecognizer()") < block.index("new VoiceCtor()"), \
        "the recognizer must be constructed inside ensureRecognizer, not at load"
    assert "ensureRecognizer();" in block and "function beginVoice(after)" in block
    # 2. The control ships hidden and is only revealed where it can work.
    assert '<button type="button" class="voice-toggle" id="voice-toggle" style="display:none">voice: off</button>' in HTML
    assert "if (!voiceSupported()) return;" in block, \
        "initVoice must refuse to draw a control the browser cannot back"
    # 3. The composer's and the interview's mic buttons are gated too.
    assert "voiceSupported()\n        ? '<button type=\"button\" class=\"mic-btn\">" in HTML, \
        "the composer's dictate button must be gated on support"
    assert "${voiceSupported() ? `<button type=\"button\" class=\"mic-btn\" id=\"qmic-${q.id}\">" in HTML, \
        "the interview's dictate button must be gated on support"
    print("  ok  test_nothing_starts_until_the_reviewer_starts_it")


def test_audio_leaving_the_machine_is_disclosed_once():
    block = _voice_block()
    assert "sends audio to its vendor" in block, \
        "the browser recognizer is a network service and the page must say so"
    assert "const VOICE_ACK_KEY = 'viva-voice';" in block, \
        "the acknowledgement is remembered like the theme, per-browser"
    assert "if (!voiceAcknowledged()) { showVoiceNotice(after); return; }" in block, \
        "the notice must gate the first start, not merely accompany it"
    print("  ok  test_audio_leaving_the_machine_is_disclosed_once")


def test_escape_reaches_the_switch_from_inside_a_textarea():
    """The one place the caret is when the microphone is hot.

    Staging a spoken comment focuses the composer's textarea, so every shortcut
    below the `TEXTAREA`/`INPUT` guard is unreachable exactly then. Escape sits
    ahead of that guard for the same reason ⌘K does — and never over the two
    modals that own Escape themselves.
    """
    handler = HTML[HTML.index("document.addEventListener('keydown'"):]
    guard = handler.index("if (tag === 'TEXTAREA' || tag === 'INPUT') return;")
    stop = handler.index("stopVoice('you pressed Escape')")
    assert stop < guard, \
        "Escape must stop listening from inside a note field, or nothing can"
    ahead = handler[:guard]
    assert "!prefsIsOpen() && !(REVIEW_DATA && recapIsOpen())" in ahead, \
        "Escape belongs to the prefs panel and the recap gate while they are open"
    print("  ok  test_escape_reaches_the_switch_from_inside_a_textarea")


def test_v_is_a_mode_toggle_not_a_card_shortcut():
    handler = HTML[HTML.index("document.addEventListener('keydown'"):]
    v = handler.index("e.key === 'v'")
    review = handler.index("if (REVIEW_DATA) {")
    assert v < review, \
        "`v` must sit outside the review branch — it works in the interview too"
    line = handler[v:handler.index("\n", v)]
    assert "rState.active" not in line, \
        "`v` is a mode toggle, not a verb on the card under the reader"
    assert "voiceSupported()" in line, "`v` must be inert where voice cannot run"
    # Both palettes carry it, from one definition — the palette is a directory
    # of the keyboard layer, so a key with no entry is an undocumented verb.
    assert HTML.count("if (voiceSupported()) cmds.push(voicePaletteCommand());") == 2
    assert "function voicePaletteCommand()" in HTML
    assert "<dt><kbd>v</kbd></dt>" in HTML, "`v` must be in the keyboard legend"
    print("  ok  test_v_is_a_mode_toggle_not_a_card_shortcut")


def test_the_strip_announces_readings_but_not_partial_guesses():
    assert '<div class="voice-strip" id="voice-strip" aria-live="polite" style="display:none"></div>' in HTML
    block = _voice_block()
    assert '<span class="vs-interim" aria-hidden="true">' in block, (
        "interim results must be aria-hidden — a live region reading every "
        "partial guess aloud is unusable")
    # Nothing is swallowed: an utterance that matched no verb still prints —
    # and names the verbs of the page actually on screen, since the interview
    # has no sections to approve.
    assert "'no command — try \"approve\", \"request changes …\", \"next\"'" in block, \
        "an unmatched utterance must report itself, not vanish"
    assert "'no command — try \"question …\", \"next\", or press dictate to answer aloud'" in block, \
        "the interview must be offered the interview's verbs"
    print("  ok  test_the_strip_announces_readings_but_not_partial_guesses")


def test_controls_meet_the_page_s_own_a11y_rules():
    # Native buttons, aria-hidden decorative emoji, focus-visible coverage, and
    # an accessible name that says what the control DOES rather than its state.
    assert '<span aria-hidden="true">&#127908;</span> dictate' in HTML, \
        "the microphone emoji must be aria-hidden like every other decorative glyph"
    assert HTML.count('<div class="voice-strip"') == 1
    for needle in (".voice-toggle:focus-visible", ".mic-btn:focus-visible",
                   ".voice-notice button:focus-visible"):
        assert needle in HTML, f"focus-visible group missing {needle}"
    block = _voice_block()
    assert "'Voice input is listening. Activate to stop listening.'" in block, \
        "the accessible name must state the action, not repeat the label's state"
    print("  ok  test_controls_meet_the_page_s_own_a11y_rules")


def test_the_recognizer_cannot_restart_forever():
    block = _voice_block()
    assert "if (_voiceRestarts >= 8)" in block, \
        "a recognizer that ends immediately every time needs a ceiling"
    assert "_voiceRestarts = 0;" in block, \
        "the guard must reset on real speech, or a long review hits the ceiling"
    assert "if (!_voiceOn) return;" in block, \
        "the reviewer's switch must beat the recognizer's own restart"
    print("  ok  test_the_recognizer_cannot_restart_forever")


def test_the_page_the_server_serves_carries_all_of_it():
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        viva.mkdir()
        (viva / "in.json").write_text(json.dumps(ROUND_1))
        with launch_server(viva / "in.json", viva / "out.json", cwd=tmp) as base:
            page = get_text(base, "/")
    assert "__VOICE_RULES__" not in page
    assert 'id="voice-toggle"' in page and 'id="voice-strip"' in page
    assert '"phrase": "request changes"' in page or '"phrase":"request changes"' in page
    print("  ok  test_the_page_the_server_serves_carries_all_of_it")


def main():
    test_grammar_is_injected_not_restated()
    test_speech_stages_a_comment_and_never_commits_one()
    test_spoken_submit_opens_the_recap_and_does_not_submit()
    test_nothing_starts_until_the_reviewer_starts_it()
    test_audio_leaving_the_machine_is_disclosed_once()
    test_escape_reaches_the_switch_from_inside_a_textarea()
    test_v_is_a_mode_toggle_not_a_card_shortcut()
    test_the_strip_announces_readings_but_not_partial_guesses()
    test_controls_meet_the_page_s_own_a11y_rules()
    test_the_recognizer_cannot_restart_forever()
    test_the_page_the_server_serves_carries_all_of_it()
    print("OK")


if __name__ == "__main__":
    main()
