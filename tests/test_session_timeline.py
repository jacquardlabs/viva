#!/usr/bin/env python3
"""The session timeline (#243), #239's test-strategy row 5.

The timeline renders from the serve-time `session` key only: every payload the
builder reads (`GET /input`, the `round` and `complete` events) carries it on a
session server and never on a review or diff one, and the builder returns
before any DOM write without it. `GET /input` on all three standalone modes is
pinned by `test_server_session.test_standalone_servers_carry_no_session_and_check_the_round`.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import (  # noqa: E402
    get_text, launch_server, poll_for, post, post_result, shipped_source)

SID = "cd" * 16
QA = {"mode": "qa", "context": "Intake", "questions": [{"id": "q1", "text": "Scope?"}]}
SPEC = {"mode": "review", "doc_file": "spec.md", "round": 1,
        "sections": [{"id": "s1", "title": "Problem", "content": "P."}]}
DIFF = {"mode": "diff", "doc_file": "PR #7", "round": 1,
        "sections": [{"id": "s1", "title": "f.py hunk 1",
                      "content": "```diff\n@@ -1 +1 @@\n-a\n+b\n```"}]}
# #239's spec, Operations: "Waiting for implementation", backticks unrendered.
SPEC_WAITING_LINE = ("Waiting for the implementing PR. Safe to close this tab; "
                     "/viva-review <PR> reopens the session.")


def subscribe(base: str):
    stream = urllib.request.urlopen(base + "/events", timeout=5)
    time.sleep(0.05)  # let the handler thread finish registering us
    return stream


def next_event(stream, name: str) -> dict:
    """The data of the next `name` event on `stream`."""
    seen = False
    for _ in range(200):
        line = stream.readline().decode()
        if not line:
            break
        if line.startswith("event: "):
            seen = line.strip() == "event: " + name
        elif seen and line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError("no %r event on the stream" % name)


def gate_states(payload: dict) -> dict:
    return {g["kind"]: g["state"] for g in payload["session"]["gates"]}


def sign_off(base: str, viva: Path, mode: str) -> None:
    post(base, "/submit", {"round": 1, "mode": mode, "submitted_early": False,
                           "sections": [{"id": "s1", "verdict": "approved"}]})
    assert poll_for(viva / "review-r1.json")
    assert post_result(base, "/complete", {})[0] == 200


def test_standalone_events_carry_no_session() -> None:
    for mode, data in (("review", SPEC), ("diff", DIFF)):
        with tempfile.TemporaryDirectory() as td:
            viva = Path(td)
            (viva / "in.json").write_text(json.dumps(data))
            with launch_server(viva / "in.json", viva / "out.json", mode=mode, cwd=viva) as base:
                page = get_text(base, "/")
                assert ('<nav class="timeline" id="session-timeline" aria-label="Session gates" '
                        'style="display:none"></nav>') in page, "the timeline ships hidden"
                stream = subscribe(base)
                try:
                    assert post_result(base, "/next-round", dict(
                        data, round=2, output=str(viva / "review-r1.json")))[0] == 200
                    assert "session" not in next_event(stream, "round"), mode
                    post(base, "/submit", {"round": 2, "submitted_early": False,
                                           "sections": [{"id": "s1", "verdict": "approved"}]})
                    assert poll_for(viva / "review-r1.json")
                    assert post_result(base, "/complete", {})[0] == 200
                    assert "session" not in next_event(stream, "complete"), mode
                finally:
                    stream.close()
    print("  ok  test_standalone_events_carry_no_session")


def test_a_session_pushes_every_state_the_timeline_draws() -> None:
    with tempfile.TemporaryDirectory() as td:
        viva = Path(td) / ".viva"
        viva.mkdir()
        (viva / "in.json").write_text(json.dumps(QA))
        with launch_server(viva / "in.json", viva / "answers.json", mode="session",
                           cwd=viva, extra=("--session-id", SID)) as base:
            stream = subscribe(base)
            try:
                post(base, "/submit", {"answers": [{"id": "q1", "choice": "", "note": "x"}],
                                       "submitted_early": False})
                assert poll_for(viva / "answers.json")
                post(base, "/next-round", dict(SPEC, output=str(viva / "review-r1.json")))
                pushed = next_event(stream, "round")
                assert pushed["session"]["id"] == SID
                assert gate_states(pushed) == {"intake": "done", "spec": "live", "diff": "waiting"}

                # Spec sign-off: no gate live, diff waiting — the waiting line's state.
                sign_off(base, viva, "review")
                done = next_event(stream, "complete")
                assert gate_states(done) == {"intake": "done", "spec": "done", "diff": "waiting"}

                (viva / "review-r1.json").unlink()
                post(base, "/next-round", dict(DIFF, output=str(viva / "review-r1.json")))
                assert gate_states(next_event(stream, "round"))["diff"] == "live"
                sign_off(base, viva, "diff")
                assert set(gate_states(next_event(stream, "complete")).values()) == {"done"}
            finally:
                stream.close()
    print("  ok  test_a_session_pushes_every_state_the_timeline_draws")


def test_the_builder_is_gated_on_the_session_key() -> None:
    """No JS runner (CLAUDE.md), so source needles on the shipped page."""
    page = shipped_source()
    build = page[page.index("function buildSessionTimeline(d) {"):page.index("function showWaitingForDiff(")]
    assert build.index("if (!d || !d.session) return;") < build.index("el('session-timeline')"), \
        "a payload without `session` returns before the builder touches the page"
    assert "nav.style.display = '';" in build, "only the builder reveals the timeline"
    assert page.count("el('session-timeline')") == 1, "the builder is the timeline's one writer"
    assert "aria-current=\"step\"" in build, "the live gate is the current step"
    # Every site that takes a payload hands it to the builder.
    boot = page[page.index("Promise.all(["):]
    assert boot.index("buildSessionTimeline(data);") < boot.index("if (data.mode === 'review')"), \
        "boot builds before the mode branches, so the intake gate gets it"
    rnd = page[page.index("es.addEventListener('round'"):page.index("es.addEventListener('complete'")]
    assert rnd.index("!Array.isArray(data.sections)") < rnd.index("buildSessionTimeline(data);")
    done = page[page.index("es.addEventListener('complete'"):page.index("es.onerror = ")]
    assert "buildSessionTimeline(data);" in done
    stale = page[page.index("function showRoundStale("):page.index("function recheckRound(")]
    assert "buildSessionTimeline(current);" in stale
    print("  ok  test_the_builder_is_gated_on_the_session_key")


def test_the_waiting_line_is_the_specs_and_lives_in_the_timeline() -> None:
    page = shipped_source()
    assert "const WAITING_FOR_DIFF = '%s';" % SPEC_WAITING_LINE in page
    build = page[page.index("function buildSessionTimeline(d) {"):page.index("function showWaitingForDiff(")]
    assert "!liveGate(d) && d.session.gates.some(g => g.state === 'waiting')" in build, \
        "the line shows between gates only"
    assert "esc(WAITING_FOR_DIFF)" in build
    assert page.count("WAITING_FOR_DIFF") == 2, "declared once, drawn once — never under the stamp"
    # "You can close this tab." would repeat the line's own "Safe to close this tab".
    wait = page[page.index("function showWaitingForDiff("):page.index("function showRoundStale(")]
    assert "document.querySelector('.complete-hint').style.display = 'none';" in wait
    done = page[page.index("es.addEventListener('complete'"):page.index("es.onerror = ")]
    assert "document.querySelector('.complete-hint').style.display = gateAhead ? 'none' : '';" in done
    print("  ok  test_the_waiting_line_is_the_specs_and_lives_in_the_timeline")


def test_the_timeline_takes_no_reviewer_ink() -> None:
    page = shipped_source()
    css = page[page.index("/* ─── Session timeline (#243)"):page.index("/* ─── Revision ledger")]
    # The done intake gate and its section links are the only controls (#244).
    controls = re.findall(r"\n(?:\.tl-done \.tl-visit|\.ia-link) \{[^}]*\}", css)
    assert len(controls) == 2 and all("color: var(--acc);" in c for c in controls), controls
    # Cascade, not rule text: the button is `.tl-kind.tl-visit` in a `.tl-gate.tl-done`,
    # so the colour it prints is the last of its highest-specificity matches.
    matches = [(sel.count("."), i, rule) for i, (sels, rule) in enumerate(
        re.findall(r"\n([^{}\n]+) \{([^}]*)\}", css)) if "color:" in rule
        for sel in sels.split(", ")
        if set(re.findall(r"\.([\w-]+)", sel)) <= {"tl-gate", "tl-done", "tl-kind", "tl-visit"}
        and re.search(r"\.tl-(?:kind|visit)$", sel)]
    assert "color: var(--acc);" in max(matches)[2], "the done intake button prints cobalt"
    rest = css
    for c in controls:
        rest = rest.replace(c, "")
    assert "--acc" not in rest and "--touch" not in css, "no other gate is a control or a touch"
    assert "--faint" not in css, "the timeline is live copy (DESIGN.md: --faint never is)"
    assert ".tl-done .tl-mark, .tl-done .tl-kind { color: var(--machine); }" in css
    print("  ok  test_the_timeline_takes_no_reviewer_ink")


def main() -> None:
    test_standalone_events_carry_no_session()
    test_a_session_pushes_every_state_the_timeline_draws()
    test_the_builder_is_gated_on_the_session_key()
    test_the_waiting_line_is_the_specs_and_lives_in_the_timeline()
    test_the_timeline_takes_no_reviewer_ink()
    print("OK")


if __name__ == "__main__":
    main()
