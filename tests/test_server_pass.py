#!/usr/bin/env python3
"""`POST /complete` under a round's `pass` (#168) — the conjunct at the wire.

`tests/test_schema.py` pins the rule; this pins that the server actually asks
it, over HTTP, with an unanswered check flag, an answered one, and no pass.
"""
import json
import subprocess
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
    """Serve one round, approve every section, and try to sign off via `/complete`."""
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
        round_input({"kind": "checks"}))
    assert status == 409, (
        "a checks round with an unanswered check flag must be refused even "
        "with every section approved — got %d %r" % (status, body))
    error = body.get("error", "")
    assert "checks pass" in error, (
        "the refusal must name the conjunct that held the round, not a section "
        "count the reviewer already satisfied: %r" % error)
    assert "0 of 2" not in error, error
    print("  ok  check_fact_check_holds_an_unanswered_flag")


def check_fact_check_closes_once_the_flag_is_answered() -> None:
    status, body = complete_after_approving_everything(
        round_input({"kind": "checks"}, result="added in round 2"))
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
    """A pass may only add conditions: an unapproved section is refused under
    every kind. Enumerated from `PASS_KINDS` so a fifth kind is covered here."""
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
    """A bad `pass` is a 400 on `/next-round` rather than quietly reverting
    to the base rule."""
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


def check_annotate_refuses_the_round_the_server_is_serving() -> None:
    """A check flag is answered in the NEXT round, never the armed one — a
    merge into the armed round's file is invisible to `/complete`, so
    `annotate` refuses it outright."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        viva = td / ".viva"
        viva.mkdir()
        (td / "d.md").write_text("# T\n", encoding="utf-8")
        inp, out = viva / "review-input-r1.json", viva / "review-r1.json"
        inp.write_text(json.dumps(round_input({"kind": "checks"})),
                       encoding="utf-8")
        sidecar = td / "sidecar.json"
        sidecar.write_text(json.dumps([dict(FLAG, id="s1", result="sourced")]),
                           encoding="utf-8")
        loop_py = Path(__file__).resolve().parent.parent / "scripts" / "loop.py"
        with launch_server(inp, out, cwd=td):
            r = subprocess.run(
                [sys.executable, str(loop_py), "--viva-dir", str(viva),
                 "annotate", "--sidecar", str(sidecar)],
                capture_output=True, text=True, cwd=str(td))
        assert r.returncode != 0, (
            "annotate merged into the round the server is serving — the server "
            "would never see it: %s" % r.stdout)
        assert "already armed" in r.stderr, r.stderr
        assert "result" not in json.dumps(json.loads(inp.read_text())), \
            "the refused merge must leave the round file untouched"
    print("  ok  check_annotate_refuses_the_round_the_server_is_serving")


def main() -> None:
    check_fact_check_holds_an_unanswered_flag()
    check_fact_check_closes_once_the_flag_is_answered()
    check_absent_pass_is_unchanged()
    check_a_pass_never_signs_off_an_unapproved_round()
    check_a_malformed_pass_is_refused_at_the_boundary()
    check_annotate_refuses_the_round_the_server_is_serving()
    print("OK")


if __name__ == "__main__":
    main()
