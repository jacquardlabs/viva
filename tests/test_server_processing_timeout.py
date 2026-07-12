#!/usr/bin/env python3
"""Soft client-side timeout affordance on #processing-view (#119).

`#processing-view` is the shared "Claude is revising…" spinner shown between
the `processing` SSE event and whichever of `round`/`complete` arrives next
(server.py's `connectSSE()`). It has two, materially different uses — an
intra-loop revise bounded by an LLM turn in this process, and the
`unified-session` (#109) qa→review hand-off, whose wait is bounded by an
external caller's own synthesis step (docs/headless-contract.md §6/§7). The
SSE connection stays open the whole time either way, so `es.onerror` cannot
detect a merely-slow hand-off — before this story a stalled hand-off looked
identical to a healthy revise: a spinner, silently, forever.

This repo has no JS/browser test harness (CLAUDE.md: stdlib-only, no
npm/node), so — matching the established pattern in test_server_a11y.py and
test_server_qa_review_handoff.py — these are string-needle assertions
against the embedded HTML constant. The timed appearance/disappearance
itself is verified manually in a browser (design doc's "Feasibility note on
testing").
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

HTML = server.HTML


def _sse_client_slice() -> str:
    start = HTML.index("function connectSSE()")
    end = HTML.index("/* ─── Keyboard shortcuts")
    assert start != -1 and end != -1 and end > start
    return HTML[start:end]


def test_timeout_constant_in_design_docs_range():
    # Design doc: "a value in the neighborhood of 15-30 seconds is a
    # reasonable starting point" — pin both the name and that the build
    # phase picked a value inside that guidance range.
    m = re.search(r"const PROCESSING_STILL_WAITING_MS\s*=\s*(\d+)\s*;", HTML)
    assert m, "expected a single named PROCESSING_STILL_WAITING_MS constant"
    ms = int(m.group(1))
    assert 15_000 <= ms <= 30_000, (
        f"PROCESSING_STILL_WAITING_MS={ms} outside the design doc's 15-30s range"
    )
    print("  ok  test_timeout_constant_in_design_docs_range")


def test_processing_listener_arms_timer():
    start = HTML.index("es.addEventListener('processing'")
    end = HTML.index("es.addEventListener('round'")
    assert start != -1 and end != -1 and end > start
    handler = HTML[start:end]
    assert "el('processing-view').style.display = '';" in handler
    # Re-arming clears any stale timer/banner first so a repeat 'processing'
    # event can never leave two timers racing.
    assert "clearProcessingTimer();" in handler
    assert "processingTimer = setTimeout(showStillWaitingBanner, PROCESSING_STILL_WAITING_MS);" in handler
    print("  ok  test_processing_listener_arms_timer")


def test_round_and_complete_clear_timer():
    round_start = HTML.index("es.addEventListener('round'")
    round_end = HTML.index("es.addEventListener('complete'")
    round_handler = HTML[round_start:round_end]
    assert "clearProcessingTimer();" in round_handler, (
        "the 'round' handler must clear the soft timer so a late-arriving "
        "round doesn't leave a stale still-waiting banner on screen"
    )

    complete_start = round_end
    complete_end = HTML.index("es.onerror = () => {")
    complete_handler = HTML[complete_start:complete_end]
    assert "clearProcessingTimer();" in complete_handler, (
        "the 'complete' handler must clear the soft timer for the same reason"
    )
    print("  ok  test_round_and_complete_clear_timer")


def test_banner_creation_function_and_mutual_exclusion():
    fn_start = HTML.index("function showStillWaitingBanner()")
    fn_end = HTML.index("\n}", fn_start)
    fn_body = HTML[fn_start:fn_end]
    assert "Still waiting — check the terminal." in fn_body
    assert "b.id = 'processing-wait-banner';" in fn_body
    assert "b.className = 'error-banner banner-info';" in fn_body
    # At most one banner at a time: skip creation if the connection-lost
    # banner is already showing — the harder, more specific signal wins,
    # mirroring the idempotency check es.onerror itself already uses.
    assert "if (el('sse-error-banner')) return;" in fn_body
    print("  ok  test_banner_creation_function_and_mutual_exclusion")


def test_clear_processing_timer_removes_banner():
    fn_start = HTML.index("function clearProcessingTimer()")
    fn_end = HTML.index("\n}", fn_start)
    fn_body = HTML[fn_start:fn_end]
    assert "clearTimeout(processingTimer)" in fn_body
    assert "el('processing-wait-banner')" in fn_body
    print("  ok  test_clear_processing_timer_removes_banner")


def test_onerror_escalates_over_still_waiting_banner():
    # If the connection actually drops after the soft timer already fired,
    # onerror must remove any still-waiting banner it finds and replace it
    # with the connection-lost one — a strict escalation, never two banners
    # stacked at the same position: fixed; top: 0.
    start = HTML.index("es.onerror = () => {")
    end = HTML.index("\n  };", start)
    assert end > start
    handler = HTML[start:end]
    assert "el('processing-wait-banner')" in handler
    assert ".remove();" in handler
    assert "Connection lost — check the terminal." in handler
    # The removal must precede the connection-lost banner's own creation.
    assert handler.index("processing-wait-banner") < handler.index("sse-error-banner")
    print("  ok  test_onerror_escalates_over_still_waiting_banner")


def test_banner_info_css_uses_violet_not_orange():
    # DESIGN.md: --violet/--violet-bg is the "Info / question" token pair,
    # an appropriate weight for "no event yet, connection still open" as
    # distinct from --orange's "Changes / error" weight used by the
    # connection-lost banner. Structural rules (position, padding, font,
    # z-index) stay on the shared .error-banner base — this is a modifier,
    # not a second component.
    start = HTML.index(".error-banner.banner-info {")
    end = HTML.index("}", start)
    rule = HTML[start:end]
    assert "var(--violet-bg)" in rule
    assert "var(--violet)" in rule
    assert "--orange" not in rule
    print("  ok  test_banner_info_css_uses_violet_not_orange")


def test_sse_client_has_no_duplicate_timer_helpers():
    # Sanity: exactly one definition of each new helper/constant, so a
    # future edit can't accidentally fork the timer lifecycle.
    assert HTML.count("function clearProcessingTimer()") == 1
    assert HTML.count("function showStillWaitingBanner()") == 1
    assert HTML.count("const PROCESSING_STILL_WAITING_MS") == 1
    print("  ok  test_sse_client_has_no_duplicate_timer_helpers")


def main() -> None:
    test_timeout_constant_in_design_docs_range()
    test_processing_listener_arms_timer()
    test_round_and_complete_clear_timer()
    test_banner_creation_function_and_mutual_exclusion()
    test_clear_processing_timer_removes_banner()
    test_onerror_escalates_over_still_waiting_banner()
    test_banner_info_css_uses_violet_not_orange()
    test_sse_client_has_no_duplicate_timer_helpers()
    print("OK (8 tests)")


if __name__ == "__main__":
    main()
