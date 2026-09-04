#!/usr/bin/env python3
"""Integration test: /next-round and /complete get the same loopback-Origin
check and MAX_SUBMIT_BYTES body cap /submit already had (#117).
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

        # Only a present, non-loopback Origin is rejected, matching /submit.
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

        # No Origin header (ordinary agent caller) is unaffected by the guard.
        r3 = dict(r1, round=3, output=str(viva / "out3.json"))
        assert post(base, "/next-round", r3) == {"ok": True}, \
            "an ordinary /next-round call with no Origin header must still succeed"

        # ── /abandon joins the same guard set ──────────────────────────────
        # Only its rejection paths are safe here — an accepted call would end
        # the server mid-test.
        assert post_headers(base, "/abandon", {}, evil) == 403, \
            "/abandon must reject a non-loopback Origin"
        assert post_oversized(base, "/abandon", over) == 413, \
            "/abandon must reject an oversized body"

        # ── the case a prefix match cannot catch ───────────────────────────
        # `http://127.0.0.1.evil.example` starts with `http://127.0.0.1`, so
        # the old `startswith` guard admitted it; must match exact host.
        for sneaky in ("http://127.0.0.1.evil.example",
                       "http://localhost.evil.example",
                       "https://127.0.0.1"):
            for route in ("/submit", "/next-round", "/complete", "/abandon"):
                assert post_headers(base, route, {},
                                    {"Origin": sneaky}) == 403, \
                    "%s must reject Origin %s — exact host, not a prefix" \
                    % (route, sneaky)

        # A cross-origin text/plain POST needs no preflight; requiring JSON
        # forces one this server does not answer.
        assert post_headers(base, "/submit", {},
                            {"Content-Type": "text/plain;charset=UTF-8"}) == 415, \
            "a text/plain body must be refused before it can be read"

        print("OK")


if __name__ == "__main__":
    main()
