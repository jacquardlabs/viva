#!/usr/bin/env python3
"""Integration test: HTTP API conventions — JSON errors, body-carried output.

- #32: every error response is `application/json` with an `{"error": ...}` body,
  so a client can parse any failure by content type (successes are already JSON).
- #54: `/next-round` reads its output path from the JSON body like every other
  POST; the legacy `?output=` query param still works as a fallback.
"""
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, launch_server, poll_for, post  # noqa: E402


def raw(base, path, method="GET", body=None):
    """Return (status, content_type, parsed_json) for any response, error or not."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=5)
        status, payload = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, payload = e.code, e.read()
        r = e
    return status, r.headers.get("Content-Type", ""), json.loads(payload)


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "body"}]}
    (viva / "in1.json").write_text(json.dumps(r1))
    out1 = viva / "out1.json"

    with launch_server(viva / "in1.json", out1, cwd=tmp) as base:
        # ── #32: errors are JSON with an {"error": ...} body ─────────────────
        st, ct, payload = raw(base, "/nope")
        assert st == 404 and ct == "application/json" and "error" in payload, (st, ct, payload)

        # Malformed JSON body → 400 JSON error (not text/plain).
        req = urllib.request.Request(base + "/submit", data=b"{bad json",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as e:
            assert e.code == 400, e.code
            assert e.headers.get("Content-Type") == "application/json", e.headers.items()
            assert json.loads(e.read())["error"] == "invalid json"

        # ── #54: /next-round reads `output` from the body ────────────────────
        out2 = viva / "out2.json"
        r2 = dict(r1, round=2)
        r2["output"] = str(out2)
        assert post(base, "/next-round", r2) == {"ok": True}
        served = get(base, "/input")
        assert served["round"] == 2, served
        assert "output" not in served, "control field must be stripped from review-input"
        # The new output path took effect: a submit now writes to out2, not out1.
        post(base, "/submit", {"round": 2, "submitted_early": False,
                               "sections": [{"id": "s1", "verdict": "approved"}]})
        assert poll_for(out2), "submit did not write to the body-supplied output path"

        # Missing output entirely → JSON 400.
        st, ct, payload = raw(base, "/next-round", "POST", body=dict(r1, round=3))
        assert st == 400 and ct == "application/json" and "output" in payload["error"], payload

    # ── #54: legacy ?output= query param still works (fallback) ──────────────
    # A separate .viva dir so this server's server.url can't collide with the
    # first (both would otherwise write the same path in the same parent).
    viva2 = tmp / ".viva2"
    viva2.mkdir()
    (viva2 / "in3.json").write_text(json.dumps(r1))
    with launch_server(viva2 / "in3.json", viva2 / "out3.json", cwd=tmp) as base:
        out4 = viva2 / "out4.json"
        assert post(base, "/next-round?output=" + str(out4), dict(r1, round=2)) == {"ok": True}
        assert get(base, "/input")["round"] == 2

    print("OK")


if __name__ == "__main__":
    main()
