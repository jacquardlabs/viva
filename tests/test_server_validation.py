#!/usr/bin/env python3
"""Integration test: the server validates at both caller-facing round boundaries.

/submit: an invalid verdict is rejected 400 before it can corrupt the ledger
or output. /next-round: validation runs on EVERY body (previously gated on a
`sections` key, which let a nested round through and bricked the tab silently)
and a refused body must leave the served round untouched.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _server_harness import get, launch_server, post_status  # noqa: E402


def _integration() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "body"}]}
    (viva / "in1.json").write_text(json.dumps(r1))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:

        # Unknown verdict → rejected at the boundary
        bad = {"round": 1, "submitted_early": False,
               "sections": [{"id": "s1", "verdict": "bogus"}]}
        assert post_status(base, "/submit", bad) == 400, "invalid verdict must be 400"

        # Section missing an id → rejected
        bad2 = {"round": 1, "submitted_early": False,
                "sections": [{"verdict": "approved"}]}
        assert post_status(base, "/submit", bad2) == 400, "missing id must be 400"

        # Valid verdicts → accepted
        good = {"round": 1, "submitted_early": False,
                "sections": [{"id": "s1", "verdict": "approved"}]}
        assert post_status(base, "/submit", good) == 200, "valid submit must be 200"

        # ── /next-round validates every body, not only a `sections`-shaped one ──
        out2 = str(viva / "out2.json")

        # The shape that caused this: the round nested one level deep.
        nested = {"round": r1, "output": out2}
        assert post_status(base, "/next-round", nested) == 400, \
            "a round nested one level deep must be refused, not served"

        # A 400 is worthless if _input_data moved anyway.
        served = get(base, "/input")
        assert served["round"] == 1, \
            "a refused /next-round must leave the served round untouched: %r" % (served,)
        assert [s["id"] for s in served["sections"]] == ["s1"], served["sections"]

        # /next-round is review-shaped ONLY; a qa-shaped body bricked the tab too.
        assert post_status(base, "/next-round",
                           {"questions": [], "output": out2}) == 400, \
            "a questions-shaped body must be refused at /next-round"
        assert get(base, "/input")["round"] == 1, "still untouched"

        # The gate is not over-tight: a well-formed next round still lands.
        assert post_status(base, "/next-round", dict(r1, round=2, output=out2)) == 200, \
            "a well-formed round must still be accepted"
        assert get(base, "/input")["round"] == 2, "the accepted round is served"

        # ── `round` at the read boundary: normalized, not required ──────────
        # Absent is legal on the wire; the boundary defaults it to 1 rather
        # than refusing a round the caller was entitled to send.
        roundless = {k: v for k, v in r1.items() if k != "round"}
        assert post_status(base, "/next-round", dict(roundless, output=out2)) == 200, \
            "an unnumbered round is legal and must still be accepted"
        assert get(base, "/input")["round"] == 1, \
            "an absent round must be served as 1, never as null or missing"

        # A present-but-malformed round is still a hard failure.
        for bad in (None, "2", 0, True):
            assert post_status(base, "/next-round",
                               dict(r1, round=bad, output=out2)) == 400, \
                f"round={bad!r} must be refused at the boundary"
        assert get(base, "/input")["round"] == 1, "and must not have replaced the round"

        # ── `mode` at /next-round: must agree with the launch mode (#126) ──
        # Absent is legal and reads as "review".
        modeless = {k: v for k, v in r1.items() if k != "mode"}
        assert post_status(base, "/next-round",
                           dict(modeless, round=3, output=out2)) == 200, \
            "a modeless round is a review round and must still be accepted"
        assert get(base, "/input")["round"] == 3
        # ...while a diff round on this review server is refused, untouched.
        assert post_status(base, "/next-round",
                           dict(r1, mode="diff", round=4, output=out2)) == 400, \
            "a diff round must not replace a review server's round"
        assert get(base, "/input")["round"] == 3, "still untouched"

    # The startup boundary normalizes too — the same file, launched cold.
    (viva / "in3.json").write_text(json.dumps(roundless))
    with launch_server(viva / "in3.json", viva / "out3.json", cwd=tmp) as base:
        assert get(base, "/input")["round"] == 1, \
            "a roundless input file must boot and serve round 1"

    print("  ok  test_next_round_refuses_what_it_cannot_serve")


def test_the_round_handler_guards_before_it_routes():
    """No JS engine here, so this is a source needle: the SSE `round` handler
    must turn away a payload with no sections[] BEFORE it overwrites
    REVIEW_DATA, instead of throwing inside initReview and freezing the tab.
    """
    import server  # noqa: E402  (repo root is on sys.path)
    page = server.HTML

    handler = page.index("es.addEventListener('round', e => {")
    body = page[handler:page.index("es.addEventListener('complete'", handler)]

    assert "if (!data || !Array.isArray(data.sections)) {" in body, \
        "the round handler must refuse a payload with no sections[]"
    assert "showRoundRefused();" in body and "return;" in body

    # The guard must precede every consumer of the payload — the first
    # assignment that matters is REVIEW_DATA.
    assert body.index("Array.isArray(data.sections)") < body.index("REVIEW_DATA       = data;"), \
        "the guard must run before any state is overwritten"

    # The banner has a removal path, or it outlives the round it describes.
    assert "function clearRoundRefused()" in page
    assert page.count("clearRoundRefused();") == 2, \
        "cleared from both the 'processing' and 'round' handlers"
    assert re.search(r"function showRoundRefused\(\)[^}]*clearProcessingTimer\(\);", page), \
        "the two fixed banners must not stack at top: 0"

    print("  ok  test_the_round_handler_guards_before_it_routes")


def main() -> None:
    _integration()
    test_the_round_handler_guards_before_it_routes()
    print("OK")


if __name__ == "__main__":
    main()
