#!/usr/bin/env python3
"""Integration test: the fixed security-header set every response carries,
the loopback-`Host` guard on every GET, and `/next-round`'s `output`
containment to `_output_root` (defence-in-depth findings from a security review).
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get_headers, launch_server, post, post_headers  # noqa: E402


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "body"}]}
    (viva / "in1.json").write_text(json.dumps(r1))

    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        # ── every response carries the fixed security-header set ───────────
        status, headers = get_headers(base, "/input", {})
        assert status == 200
        csp = headers.get("Content-Security-Policy", "")
        assert "img-src 'self' data:" in csp, \
            "CSP must allow a reviewed doc's data: image but nothing remote"
        assert "default-src 'self'" in csp
        assert "connect-src 'self'" in csp
        assert "form-action 'self'" in csp
        assert "base-uri 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Referrer-Policy") == "no-referrer"

        # The served SPA page carries the same headers too, not just the API routes.
        status, headers = get_headers(base, "/", {})
        assert status == 200
        assert "Content-Security-Policy" in headers

        # ── the loopback-Host guard on every GET ────────────────────────────
        status, _ = get_headers(base, "/input", {"Host": "evil.example"})
        assert status == 403, \
            "a GET carrying a non-loopback Host must be rejected (DNS rebinding)"
        status, _ = get_headers(base, "/preferences", {"Host": "evil.example"})
        assert status == 403
        # Exact host match, not a prefix — an attacker A record can start with "127.0.0.1".
        status, _ = get_headers(base, "/input", {"Host": "127.0.0.1.evil.example"})
        assert status == 403
        # A genuine loopback Host still works, port and all.
        loopback_host = base.split("//", 1)[1]  # "127.0.0.1:PORT"
        status, _ = get_headers(base, "/input", {"Host": loopback_host})
        assert status == 200
        status, _ = get_headers(base, "/input", {"Host": "localhost"})
        assert status == 200, "bare 'localhost', no port, must still be accepted"

        # ── /next-round's output containment to _output_root ───────────────
        # An output path outside the directory `--output` named at launch is refused.
        outside = Path(tempfile.mkdtemp()) / "elsewhere.json"
        r2 = dict(r1, round=2, output=str(outside))
        status = post_headers(base, "/next-round", r2, {})
        assert status == 400, \
            "/next-round must refuse an 'output' outside the launch directory"

        # A path inside the launch directory still succeeds.
        inside = viva / "out2.json"
        r2b = dict(r1, round=2, output=str(inside))
        assert post(base, "/next-round", r2b) == {"ok": True}, \
            "/next-round must accept an 'output' inside the launch directory"

        print("OK")


if __name__ == "__main__":
    main()
