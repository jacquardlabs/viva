#!/usr/bin/env python3
"""Integration test: GET /preferences and POST /preferences/mute (issue #142).

Covers the preferences-inspector story's own acceptance criteria — mute from
the server is verified against `.viva/preferences.json` on disk, not just the
HTTP response — plus the boundary behavior the design doc calls out
explicitly: a missing or corrupt store degrades to an empty list rather than
taking the server down (in deliberate contrast to `preferences.py`'s own
`sys.exit()`-on-parse-failure CLI loader), the mute route does not restrict
by the preference's current status (that restriction lives entirely in which
rows the client renders a mute button on), and the loopback-Origin /
body-size guards shared with every other caller-facing POST endpoint apply
here too.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import (  # noqa: E402
    get, launch_server, post, post_headers, post_oversized, post_status,
)

MAX_SUBMIT_BYTES = 256 * 1024 * 1024  # must match server.py verbatim

STORE = {
    "version": 1,
    "preferences": {
        "cite-sources": {
            "id": "cite-sources",
            "label": "Cite a source for every quantitative claim",
            "guidance": "When a section states a number, attach a citation or mark it unsourced.",
            "status": "standing",
            "observations": 3,
            "sessions": ["2026-06-20 plan.md", "2026-06-25 spec.md"],
            "created": "2026-06-20",
            "updated": "2026-06-25",
        },
        "avoid-passive": {
            "id": "avoid-passive",
            "label": "Avoid passive voice in goals",
            "guidance": "Rewrite passive constructions active.",
            "status": "candidate",
            "observations": 1,
            "sessions": ["2026-06-28 plan.md"],
            "created": "2026-06-28",
            "updated": "2026-06-28",
        },
        "retired-note": {
            "id": "retired-note",
            "label": "Zzz already muted critique",
            "guidance": "",
            "status": "muted",
            "observations": 2,
            "sessions": ["2026-05-01 old.md"],
            "created": "2026-05-01",
            "updated": "2026-05-02",
        },
    },
}


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    r1 = {"mode": "review", "doc_file": "doc.md", "round": 1, "approved_ids": [],
          "sections": [{"id": "s1", "title": "Goals", "content": "body"}]}
    (viva / "in1.json").write_text(json.dumps(r1))
    prefs_path = viva / "preferences.json"

    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:

        # ── No store on disk yet → empty list, not an error ────────────
        assert get(base, "/preferences") == [], \
            "a missing preferences.json must degrade to an empty list"

        # ── Seed the store, then GET /preferences ───────────────────────
        prefs_path.write_text(json.dumps(STORE))
        got = get(base, "/preferences")
        assert len(got) == 3, f"expected 3 preferences, got {len(got)}"
        # label-sorted, case-insensitive: Avoid… < Cite… < Zzz…
        assert [p["id"] for p in got] == ["avoid-passive", "cite-sources", "retired-note"], \
            f"preferences must be label-sorted: {[p['id'] for p in got]}"
        cite = next(p for p in got if p["id"] == "cite-sources")
        assert cite["status"] == "standing"
        assert cite["label"] == "Cite a source for every quantitative claim"
        assert cite["sessions"] == ["2026-06-20 plan.md", "2026-06-25 spec.md"]

        # ── Mute a standing preference → verified on disk, not just the
        #    HTTP response (the acceptance criterion's own verification
        #    method) ────────────────────────────────────────────────────
        assert post(base, "/preferences/mute", {"id": "cite-sources"}) == {"ok": True}
        on_disk = json.loads(prefs_path.read_text())
        assert on_disk["preferences"]["cite-sources"]["status"] == "muted", \
            "mute must persist to preferences.json on disk"
        # Untouched siblings.
        assert on_disk["preferences"]["avoid-passive"]["status"] == "candidate"
        assert on_disk["preferences"]["retired-note"]["status"] == "muted"
        # And the GET reflects it too.
        cite2 = next(p for p in get(base, "/preferences") if p["id"] == "cite-sources")
        assert cite2["status"] == "muted"

        # ── set_status doesn't restrict by current status — the route is a
        #    second caller of the same pure function the CLI uses, and the
        #    restriction to "only standing rows get a mute button" lives
        #    entirely client-side (design doc, "Out of scope"). Muting a
        #    candidate must still succeed server-side. ───────────────────
        assert post(base, "/preferences/mute", {"id": "avoid-passive"}) == {"ok": True}
        on_disk = json.loads(prefs_path.read_text())
        assert on_disk["preferences"]["avoid-passive"]["status"] == "muted"

        # ── Unknown id → 404, nothing written ───────────────────────────
        before = prefs_path.read_text()
        assert post_status(base, "/preferences/mute", {"id": "does-not-exist"}) == 404
        assert prefs_path.read_text() == before, \
            "a failed mute must not touch the store"

        # ── Missing 'id' → 400 ───────────────────────────────────────────
        assert post_status(base, "/preferences/mute", {}) == 400

        # ── Loopback-only Origin guard, shared with every other
        #    caller-facing POST endpoint ─────────────────────────────────
        evil = {"Origin": "http://evil.example"}
        assert post_headers(base, "/preferences/mute", {"id": "retired-note"}, evil) == 403
        loopback = {"Origin": base}
        assert post_headers(base, "/preferences/mute", {"id": "retired-note"}, loopback) == 200

        # ── Oversized body → 413, same cap as every other POST ──────────
        assert post_oversized(base, "/preferences/mute", MAX_SUBMIT_BYTES + 1) == 413

        # ── Corrupt store on disk → GET degrades to [] and the server
        #    stays up (in deliberate contrast to preferences.py's own
        #    sys.exit()-on-parse-failure CLI loader) ─────────────────────
        prefs_path.write_text("{not valid json")
        assert get(base, "/preferences") == [], \
            "a corrupt preferences.json must degrade to an empty list, not error"
        # The server process itself must still be alive and serving.
        assert get(base, "/input")["round"] == 1

        # ── Parseable JSON, wrong shape, degrades the same way — a
        #    json.loads() success does not imply a valid store. `[]` and a
        #    non-dict "preferences" value would otherwise crash
        #    preferences.select()'s `.values()` call inside the handler. ──
        prefs_path.write_text("[]")
        assert get(base, "/preferences") == [], \
            "a top-level JSON array must degrade to an empty list, not 500"
        prefs_path.write_text(json.dumps({"version": 1, "preferences": []}))
        assert get(base, "/preferences") == [], \
            "a non-dict 'preferences' value must degrade to an empty list, not 500"
        prefs_path.write_text(json.dumps({"version": 1, "preferences": {"bad": 5}}))
        assert get(base, "/preferences") == [], \
            "a non-dict preference entry must be dropped, not crash the sort"
        assert get(base, "/input")["round"] == 1, "server must still be alive after every bad shape"

        print("OK")


if __name__ == "__main__":
    main()
