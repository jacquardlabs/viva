#!/usr/bin/env python3
"""The dead-session overlay (#174): a tab whose server is gone must say so,
not stay fully interactive with submit POSTing into a dead socket.

Asserted: it ships hidden as a modal alertdialog with no way out (no close,
Escape, or backdrop dismiss); it blocks at three layers (inert, the document
keydown listener, and `sendSubmit`); only `es.onerror` raises it, only
`es.onopen` clears it, and 'complete' closes the stream first; the resume
command is named only for a review-mode payload that carries a doc.

String-needle assertions against the embedded HTML constant — no JS harness
in this repo. The one live-server check confirms the markup actually ships.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import server  # noqa: E402
from _server_harness import get_text, launch_server  # noqa: E402

HTML = server.HTML

ROUND = {
    "round": 1,
    "mode": "review",
    "doc_file": "docs/plan.md",
    "sections": [{"id": "S1", "title": "Goals", "content": "Ship it.",
                  "level": 2, "anchor": "goals"}],
}


def test_overlay_is_an_alertdialog_that_ships_hidden():
    assert ('<div class="dead-overlay" id="dead-overlay" role="alertdialog" '
            'aria-modal="true"') in HTML, \
        "the dead-session overlay must be a named, modal alertdialog"
    assert 'aria-labelledby="dead-title" aria-describedby="dead-body"' in HTML
    assert 'id="dead-title"' in HTML and 'id="dead-body"' in HTML
    # Ships hidden — showDeadSession() is the only thing that reveals it.
    overlay = HTML[HTML.index('id="dead-overlay"'):]
    assert 'style="display:none"' in overlay[:overlay.index('>') + 1], \
        "the overlay must ship hidden"
    # Nothing inside is focusable, so the container itself takes tabindex.
    assert '<div class="dead-panel" id="dead-panel" tabindex="-1">' in HTML
    print("  ok  test_overlay_is_an_alertdialog_that_ships_hidden")


def test_overlay_has_no_way_out():
    # Unlike the recap/prefs dialogs, this one ships no close control:
    # dismissing it would return the reviewer to a tab whose submit fails.
    assert "dead-close" not in HTML, \
        "the dead-session overlay must ship no close control"
    start = HTML.index('<div class="dead-overlay"')
    end = HTML.index('<div class="pal-overlay"', start)   # markup, not a comment
    assert "<button" not in HTML[start:end], \
        "no control inside the overlay — there is nothing on the other side of it"
    # No backdrop-click dismiss, unlike the two dialogs above it.
    assert "el('dead-overlay').addEventListener" not in HTML, \
        "the overlay must not wire a backdrop dismiss"
    # Escape reaches the document keydown listener, which swallows everything
    # while the overlay is up — asserted structurally below.
    assert HTML.count("function hideDeadSession()") == 1
    assert HTML.count("hideDeadSession();") == 1, \
        "hideDeadSession must have exactly one caller — es.onopen"
    onopen = HTML.index("es.onopen = ")
    assert "hideDeadSession();" in HTML[onopen:onopen + 120], \
        "es.onopen must be hideDeadSession's only caller"
    print("  ok  test_overlay_has_no_way_out")


def test_overlay_blocks_at_all_three_layers():
    fn_start = HTML.index("function showDeadSession()")
    fn_end = HTML.index("\n}", fn_start)
    fn = HTML[fn_start:fn_end]

    # 1. inert — pointer and Tab out of #paper, the bottom bar, the skip link.
    assert "setBackgroundInert(true);" in fn
    assert "el('dead-panel').focus();" in fn
    # The other three overlays live outside that subtree and restore focus
    # into the background on close, so they must close first.
    for closer in ("closeRecap();", "closePrefsPanel();", "closePalette();"):
        assert closer in fn, f"showDeadSession must call {closer}"
    assert fn.index("closePalette();") < fn.index("setBackgroundInert(true);"), \
        "the other overlays must close before this one takes focus"

    # 2. The document keydown listener — inert never reaches it, so shortcuts
    #    would otherwise keep mutating state behind the scrim. Swallow must
    #    precede every branch, including Cmd+K.
    kd = HTML.index("document.addEventListener('keydown'")
    swallow = HTML.index("if (deadSessionIsOpen()) return;", kd)
    palette = HTML.index("(e.metaKey || e.ctrlKey) && (e.key === 'k'", kd)
    review_branch = HTML.index("if (REVIEW_DATA) {", kd)
    assert kd < swallow < palette < review_branch, \
        "the dead-session swallow must precede every keydown branch"

    # 3. sendSubmit — the one choke point both submitReview and submitQA use.
    send_start = HTML.index("function sendSubmit(result)")
    send_end = HTML.index("fetch('/submit'", send_start)
    assert "if (deadSessionIsOpen()) return;" in HTML[send_start:send_end], \
        "sendSubmit must refuse before the fetch, not after it fails"
    print("  ok  test_overlay_blocks_at_all_three_layers")


def test_only_onerror_raises_it_and_complete_never_does():
    # es.close() on 'complete' suppresses onerror for the normal shutdown —
    # a signed-off review is not a dead session.
    complete_start = HTML.index("es.addEventListener('complete'")
    onerror_start = HTML.index("es.onerror = ", complete_start)
    complete = HTML[complete_start:onerror_start]
    assert "es.close();" in complete, \
        "the complete handler must close the stream before the server shuts down"
    assert "showDeadSession" not in complete, \
        "a completed review must never raise the dead-session overlay"
    # Exactly one definition and one caller (es.onerror) — a second raiser
    # would kill a live tab on a transient 500.
    assert HTML.count("function showDeadSession()") == 1
    assert HTML.count("showDeadSession();") == 1, \
        "showDeadSession must have exactly one caller — es.onerror"
    onerror_end = HTML.index("es.onopen = ", onerror_start)
    assert "showDeadSession();" in HTML[onerror_start:onerror_end]
    print("  ok  test_only_onerror_raises_it_and_complete_never_does")


def test_resume_command_is_named_only_when_the_payload_names_a_target():
    fn_start = HTML.index("function showDeadSession()")
    fn_end = HTML.index("\n}", fn_start)
    fn = HTML[fn_start:fn_end]
    # Gated on review mode: diff/qa payloads don't carry a pasteable doc_file.
    assert "REVIEW_DATA.mode === 'review' && REVIEW_DATA.doc_file" in fn, \
        "the resume command must be gated on a review-mode payload with a doc"
    assert "el('dead-cmd').textContent = doc ? '/viva-review ' + doc : '';" in fn, \
        "the resume command must be built from the payload, never templated"
    # The skill it names must exist.
    assert (ROOT / ".claude" / "skills" / "viva-review" / "SKILL.md").is_file()
    # Both branches are written every time it's raised — no stale target
    # inherited from a prior mode.
    assert '<p class="dead-resume" id="dead-resume" style="display:none">' in HTML
    assert "el('dead-resume').style.display = doc ? '' : 'none';" in fn
    print("  ok  test_resume_command_is_named_only_when_the_payload_names_a_target")


def test_overlay_clears_the_palette_and_the_scrim_it_sits_on():
    # z-index has to clear the palette's 1200.
    m = re.search(r"\.dead-overlay \{[^}]*z-index:\s*(\d+)", HTML)
    assert m, "no .dead-overlay z-index"
    pal = re.search(r"\.pal-overlay \{[^}]*z-index:\s*(\d+)", HTML)
    assert pal and int(m.group(1)) > int(pal.group(1)), \
        "the dead-session overlay must sit above the command palette"
    # Tokenized like every other surface — no hardcoded hex.
    rule = HTML[HTML.index(".dead-panel {"):HTML.index("}", HTML.index(".dead-panel {"))]
    assert "var(--paper)" in rule and "var(--orange)" in rule
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", rule), \
        "the panel must be tokenized, not hardcoded"
    print("  ok  test_overlay_clears_the_palette_and_the_scrim_it_sits_on")


def test_overlay_reaches_the_browser():
    """The one live check: the markup ships in the page the server serves, not
    only in the constant this file imports."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        inp, out = td / "in.json", td / "out.json"
        inp.write_text(json.dumps(ROUND))
        with launch_server(inp, out, cwd=td) as base:
            page = get_text(base, "/")
    assert 'id="dead-overlay"' in page and 'role="alertdialog"' in page
    assert "This tab lost its review server." in page
    print("  ok  test_overlay_reaches_the_browser")


def main() -> None:
    test_overlay_is_an_alertdialog_that_ships_hidden()
    test_overlay_has_no_way_out()
    test_overlay_blocks_at_all_three_layers()
    test_only_onerror_raises_it_and_complete_never_does()
    test_resume_command_is_named_only_when_the_payload_names_a_target()
    test_overlay_clears_the_palette_and_the_scrim_it_sits_on()
    test_overlay_reaches_the_browser()
    print("OK (7 tests)")


if __name__ == "__main__":
    main()
