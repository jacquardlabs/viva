#!/usr/bin/env python3
"""`POST /complete` under a round's `pass` (#168) — the conjunct at the wire.

`tests/test_schema.py` pins the rule; this pins that the server actually asks
it, from its own process, with a round that arrived over HTTP. Three sessions,
identical but for the round file:

  1. `fact-check` with an unanswered check flag → refused, every section
     approved. The pass ADDS a condition; approvals alone no longer close it.
  2. the same round with that flag answered → 200.
  3. the same unanswered flag with NO `pass` → 200. Absent is today's behavior
     exactly (PRODUCT.md principle 4), and this is the one place a regression
     there would be silent: an existing caller that never asked for a pass would
     start getting a 409 it cannot fix.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import schema  # noqa: E402
from _server_harness import launch_server, poll_for, post, post_result  # noqa: E402

FLAG = {"kind": "headings-present", "severity": "warn",
        "message": "missing expected design-doc section: 'Out of scope'",
        "anchor": "design-doc grammar"}

APPROVED = {"round": 1, "submitted_early": False, "sections": [
    {"id": "s1", "verdict": "approved"},
    {"id": "s2", "verdict": "approved"},
]}


def round_input(pass_spec=None, result=None) -> dict:
    """A two-section round whose first card carries the check flag."""
    flag = dict(FLAG, result=result) if result is not None else dict(FLAG)
    data = {
        "mode": "review", "doc_file": "design.md", "round": 1,
        "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Design: a thing", "content": "# Design\n",
             "annotations": [flag]},
            {"id": "s2", "title": "Proposed design", "content": "## P\n\nwhat\n"},
        ],
    }
    if pass_spec is not None:
        data["pass"] = pass_spec
    return data


def complete_after_approving_everything(data: dict) -> tuple:
    """Serve one round, approve every section, and try to sign off. Returns
    `(status, body)` from `/complete`."""
    with tempfile.TemporaryDirectory() as td:
        viva = Path(td) / ".viva"
        viva.mkdir()
        inp, out = viva / "review-input-r1.json", viva / "review-r1.json"
        inp.write_text(json.dumps(data), encoding="utf-8")
        with launch_server(inp, out, cwd=td) as base:
            post(base, "/submit", APPROVED)
            assert poll_for(out), "review-r1.json never written"
            return post_result(base, "/complete", {"rounds_total": 1})


def check_fact_check_holds_an_unanswered_flag() -> None:
    status, body = complete_after_approving_everything(
        round_input({"kind": "fact-check"}))
    assert status == 409, (
        "a fact-check round with an unanswered check flag must be refused even "
        "with every section approved — got %d %r" % (status, body))
    error = body.get("error", "")
    assert "fact-check pass" in error, (
        "the refusal must name the conjunct that held the round, not a section "
        "count the reviewer already satisfied: %r" % error)
    assert "0 of 2" not in error, error
    print("  ok  check_fact_check_holds_an_unanswered_flag")


def check_fact_check_closes_once_the_flag_is_answered() -> None:
    status, body = complete_after_approving_everything(
        round_input({"kind": "fact-check"}, result="added in round 2"))
    assert (status, body) == (200, {"ok": True}), (
        "an answered check flag must let the round close: %d %r" % (status, body))
    print("  ok  check_fact_check_closes_once_the_flag_is_answered")


def check_absent_pass_is_unchanged() -> None:
    status, body = complete_after_approving_everything(round_input())
    assert (status, body) == (200, {"ok": True}), (
        "a round with no pass must complete on approvals alone, whatever flags "
        "it carries: %d %r" % (status, body))
    print("  ok  check_absent_pass_is_unchanged")


def check_a_pass_never_signs_off_an_unapproved_round() -> None:
    """The invariant, at the wire: a pass may only add conditions. A round with
    a section still carrying `changes` is refused under every kind, with the
    section count the agent needs to recover. Enumerated from `PASS_KINDS`, so a
    fifth kind is covered here the day it lands."""
    for kind in schema.PASS_KINDS:
        with tempfile.TemporaryDirectory() as td:
            viva = Path(td) / ".viva"
            viva.mkdir()
            inp, out = viva / "review-input-r1.json", viva / "review-r1.json"
            inp.write_text(json.dumps(round_input({"kind": kind})),
                           encoding="utf-8")
            with launch_server(inp, out, cwd=td) as base:
                post(base, "/submit", {"round": 1, "sections": [
                    {"id": "s1", "verdict": "approved"},
                    {"id": "s2", "verdict": "changes",
                     "comments": [{"note": "fix"}]}]})
                assert poll_for(out), "review-r1.json never written"
                status, body = post_result(base, "/complete", {})
            assert status == 409, "%s pass signed off an unapproved round" % kind
            assert "1 of 2" in body.get("error", ""), (kind, body)
    print("  ok  check_a_pass_never_signs_off_an_unapproved_round")


def check_a_malformed_pass_is_refused_at_the_boundary() -> None:
    """The server validates a `/next-round` payload on read. A bad `pass` is a
    400 there rather than a round that quietly reverts to the base rule."""
    with tempfile.TemporaryDirectory() as td:
        viva = Path(td) / ".viva"
        viva.mkdir()
        inp, out = viva / "review-input-r1.json", viva / "review-r1.json"
        inp.write_text(json.dumps(round_input()), encoding="utf-8")
        with launch_server(inp, out, cwd=td) as base:
            for bad in ({"kind": "polish"}, {"posture": "hard"}, "line", None):
                payload = dict(round_input(), round=2,
                               output=str(viva / "review-r2.json"))
                payload["pass"] = bad
                status, body = post_result(base, "/next-round", payload)
                assert status == 400, (bad, status, body)
                assert "pass" in body.get("error", ""), (bad, body)
    print("  ok  check_a_malformed_pass_is_refused_at_the_boundary")


def main() -> None:
    check_fact_check_holds_an_unanswered_flag()
    check_fact_check_closes_once_the_flag_is_answered()
    check_absent_pass_is_unchanged()
    check_a_pass_never_signs_off_an_unapproved_round()
    check_a_malformed_pass_is_refused_at_the_boundary()
    print("OK")


if __name__ == "__main__":
    main()
