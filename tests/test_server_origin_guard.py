#!/usr/bin/env python3
"""Integration test: /next-round and /complete get the same loopback-Origin
check and MAX_SUBMIT_BYTES body cap /submit already had (#117).

Before this fix, `/next-round` and `/complete` carried neither guard — the
inline comment justifying that ("called by the agent, not the browser")
is exactly the reasoning issue #117's audit disproved: a hostile page open
in the developer's browser during a live session can drive either endpoint
via a cross-origin CORS-simple POST. This asserts the guard `_submit`
already had is now symmetric across all three caller-facing POST endpoints.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import (  # noqa: E402
    launch_server, post, post_headers, post_oversized,
)

MAX_SUBMIT_BYTES = 256 * 1024 * 1024  # must match server.py verbatim


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "body"}]}
    (viva / "in1.json").write_text(json.dumps(r1))

    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        evil = {"Origin": "http://evil.example"}

        # ── A non-loopback Origin is rejected on every caller-facing POST ──
        r2 = dict(r1, round=2, output=str(viva / "out2.json"))
        assert post_headers(base, "/next-round", r2, evil) == 403, \
            "/next-round must reject a non-loopback Origin"
        assert post_headers(base, "/complete", {}, evil) == 403, \
            "/complete must reject a non-loopback Origin"
        assert post_headers(base, "/submit",
                             {"round": 1, "sections": []}, evil) == 403, \
            "/submit must still reject a non-loopback Origin (unchanged baseline)"

        # A loopback Origin is accepted — the check only rejects a *present,
        # non-loopback* value, matching /submit's existing behavior exactly.
        loopback = {"Origin": base}  # e.g. "http://127.0.0.1:PORT"
        r2b = dict(r1, round=2, output=str(viva / "out2b.json"))
        assert post_headers(base, "/next-round", r2b, loopback) == 200, \
            "/next-round must accept a loopback Origin"

        # ── A body over MAX_SUBMIT_BYTES is rejected with 413, same set ────
        over = MAX_SUBMIT_BYTES + 1
        assert post_oversized(base, "/next-round", over) == 413, \
            "/next-round must reject an oversized body"
        assert post_oversized(base, "/complete", over) == 413, \
            "/complete must reject an oversized body"
        assert post_oversized(base, "/submit", over) == 413, \
            "/submit must still reject an oversized body (unchanged baseline)"

        # A body at/under the cap, no Origin header at all — the ordinary
        # agent-caller case — is entirely unaffected by the new guard.
        r3 = dict(r1, round=3, output=str(viva / "out3.json"))
        assert post(base, "/next-round", r3) == {"ok": True}, \
            "an ordinary /next-round call with no Origin header must still succeed"

        print("OK")


if __name__ == "__main__":
    main()
