#!/usr/bin/env python3
"""The voice layer on the page it ships in — the two invariants and the wiring.

Invariant 1: speech may command, never author — it stages a transcript in the
comment composer and stops; nothing in the voice block calls `addComment` or
submits. Invariant 2: off unless turned on — no recognizer at load, toggle
ships hidden, no control drawn where the browser has none. No JS engine in
this suite, so behavior is read off the page the server actually serves.
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
    for why needles anchor inside it rather than search the whole frontend."""
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
    """Invariant 1: `stageVoiceComment` opens the composer and fills its box;
    the existing save button is the only thing that makes a comment."""
    block = _voice_block()
    assert "addComment(" not in block, (
        "the voice layer must never call addComment — a transcript that "
        "becomes a comment on its own is a sentence in the ledger nobody read")
    assert "openCommentPopover(id, { type });" in block, \
        "a spoken comment must route through the composer, like `c` and `i` do"
    assert ".cmt-save').click()" in block, \
        "'save' must drive the composer's own control, not a second save path"
    # Staged type is reported back as what the box BECAME, not what was asked for.
    assert "const became = pop.dataset.type;" in block
    print("  ok  test_speech_stages_a_comment_and_never_commits_one")


def test_spoken_submit_opens_the_recap_and_does_not_submit():
    block = _voice_block()
    assert "openRecap()" in block, "'submit' must open the gate"
    for forbidden in ("submitReview(", "submitQA(", "btn-submit"):
        assert forbidden not in block, (
            "the voice layer must not reach past the recap gate (%s): ending "
            "the round is confirmed by hand, spoken or not" % forbidden)
    assert any(r["phrase"] == "submit" and r["act"] == "recap"
               for r in server._VOICE_RULES)
    print("  ok  test_spoken_submit_opens_the_recap_and_does_not_submit")


def test_nothing_starts_until_the_reviewer_starts_it():
    """Invariant 2, three independent halves — any two alone leak."""
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
    """Staging a spoken comment focuses the composer's textarea, so shortcuts
    below the `TEXTAREA`/`INPUT` guard are unreachable exactly then; Escape
    sits ahead of that guard for the same reason ⌘K does."""
    handler = HTML[HTML.index("document.addEventListener('keydown'"):]
    guard = handler.index("if (tag === 'TEXTAREA' || tag === 'INPUT') return;")
    stop = handler.index("stopVoice('you pressed Escape')")
    assert stop < guard, \
        "Escape must stop listening from inside a note field, or nothing can"
    ahead = handler[:guard]
    assert "!prefsIsOpen() && !(REVIEW_DATA && recapIsOpen())" in ahead, \
        "Escape belongs to the prefs panel and the recap gate while they are open"
    print("  ok  test_escape_reaches_the_switch_from_inside_a_textarea")


def test_the_router_carries_the_keydown_handler_s_guards():
    """Speech is a second input path into the same verdict state (#174):
    utterances don't go through `keydown`, so its guards must be repeated
    here or the hole it closed reopens through the microphone."""
    block = _voice_block()
    route = block.index("const hit = norm ? matchVoiceRule(norm) : null;")
    for guard in ("deadSessionIsOpen()", "voiceRoundIsLive()", "prefsIsOpen()",
                  "recapIsOpen()"):
        at = block.index(guard)
        assert at < route, (
            "%s must be checked BEFORE an utterance is routed, the way the "
            "keydown handler checks it before a keystroke" % guard)
    # The two terminal states turn the mic off at their own source — a mic
    # left hot there is one no control can reach.
    assert "stopVoice('the session ended');" in HTML
    assert "stopVoice('the review is signed off');" in HTML
    dead = HTML.index("function showDeadSession()")
    assert HTML.index("stopVoice('the session ended');", dead) < HTML.index("\n}\n", dead) + 400
    # Between rounds is NOT terminal — same tab, so the mic stays on.
    assert "voiceSay('heard', raw, 'no round on screen yet — nothing to command');" in block
    print("  ok  test_the_router_carries_the_keydown_handler_s_guards")


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
    # Both palettes carry it, from one definition.
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
    # An unmatched utterance still prints, naming the verbs on screen.
    assert "'no command — try \"approve\", \"request changes …\", \"next\"'" in block, \
        "an unmatched utterance must report itself, not vanish"
    assert "'no command — try \"question …\", \"next\", or press dictate to answer aloud'" in block, \
        "the interview must be offered the interview's verbs"
    print("  ok  test_the_strip_announces_readings_but_not_partial_guesses")


def test_controls_meet_the_page_s_own_a11y_rules():
    # Plain-text label (the mic emoji was the only colour glyph on the page).
    assert 'class="mic-btn">dictate</button>' in HTML and '&#127908;' not in HTML, \
        "dictate is a word, not an emoji"
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
    test_the_router_carries_the_keydown_handler_s_guards()
    test_v_is_a_mode_toggle_not_a_card_shortcut()
    test_the_strip_announces_readings_but_not_partial_guesses()
    test_controls_meet_the_page_s_own_a11y_rules()
    test_the_recognizer_cannot_restart_forever()
    test_the_page_the_server_serves_carries_all_of_it()
    print("OK")


if __name__ == "__main__":
    main()
