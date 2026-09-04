#!/usr/bin/env python3
"""Soft client-side timeout affordance on #processing-view (#119).

A stalled hand-off can't be told apart from a healthy revise since `es.onerror`
only fires on a dropped connection, not a slow one — this banner covers that
gap, escalating to the dead-session overlay (#174) rather than stacking with it.

String-needle assertions against the embedded HTML constant (no JS/browser
harness in this repo); timed appearance is verified manually in a browser.
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
    # Design doc's guidance range: 15-30 seconds.
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
    # Re-arming clears any stale timer first so repeats never race.
    assert "clearProcessingTimer();" in handler
    assert "processingTimer = setTimeout(showStillWaitingBanner, PROCESSING_STILL_WAITING_MS);" in handler
    print("  ok  test_processing_listener_arms_timer")


def test_round_and_complete_clear_timer():
    round_start = HTML.index("es.addEventListener('round'")
    round_end = HTML.index("es.addEventListener('complete'")
    round_handler = HTML[round_start:round_end]
    assert "clearProcessingTimer();" in round_handler, (
        "'round' handler must clear the soft timer to avoid a stale banner"
    )

    complete_start = round_end
    complete_end = HTML.index("es.onerror = () => {")
    complete_handler = HTML[complete_start:complete_end]
    assert "clearProcessingTimer();" in complete_handler, (
        "'complete' handler must clear the soft timer for the same reason"
    )
    print("  ok  test_round_and_complete_clear_timer")


def test_banner_creation_function_and_mutual_exclusion():
    fn_start = HTML.index("function showStillWaitingBanner()")
    fn_end = HTML.index("\n}", fn_start)
    fn_body = HTML[fn_start:fn_end]
    assert "Still waiting — check the terminal." in fn_body
    assert "b.id = 'processing-wait-banner';" in fn_body
    assert "b.className = 'error-banner banner-info';" in fn_body
    # At most one signal at a time: skip if the dead-session overlay (#174) is up.
    assert "if (deadSessionIsOpen()) return;" in fn_body
    print("  ok  test_banner_creation_function_and_mutual_exclusion")


def test_clear_processing_timer_removes_banner():
    fn_start = HTML.index("function clearProcessingTimer()")
    fn_end = HTML.index("\n}", fn_start)
    fn_body = HTML[fn_start:fn_end]
    assert "clearTimeout(processingTimer)" in fn_body
    assert "el('processing-wait-banner')" in fn_body
    print("  ok  test_clear_processing_timer_removes_banner")


def test_onerror_escalates_over_still_waiting_banner():
    # A dropped connection must remove any still-waiting banner and escalate
    # to the dead-session overlay (#174), never leave both showing.
    start = HTML.index("es.onerror = () => {")
    end = HTML.index("\n  };", start)
    assert end > start
    handler = HTML[start:end]
    assert "el('processing-wait-banner')" in handler
    assert ".remove();" in handler
    assert "showDeadSession();" in handler
    # The removal must precede the escalation.
    assert handler.index("processing-wait-banner") < handler.index("showDeadSession()")
    print("  ok  test_onerror_escalates_over_still_waiting_banner")


def test_banner_info_css_uses_violet_not_orange():
    # DESIGN.md: --violet is the "Info" token, distinct from --orange's
    # "Changes / error" weight used by the connection-lost banner.
    start = HTML.index(".error-banner.banner-info {")
    end = HTML.index("}", start)
    rule = HTML[start:end]
    assert "var(--violet-bg)" in rule
    assert "var(--violet)" in rule
    assert "--orange" not in rule
    print("  ok  test_banner_info_css_uses_violet_not_orange")


def test_sse_client_has_no_duplicate_timer_helpers():
    # Exactly one definition of each helper/constant, so edits can't fork the lifecycle.
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
