#!/usr/bin/env python3
"""Integration test: per-section cumulative revision count (issue #141).

Server-side, wire-only derivation — `GET /input` and the `round` SSE event
(both routed through `server._with_revision_counts`) attach `revision_count`
to a section only once its cumulative revision count this session reaches
2+, the threshold `.rev-tri`'s `.rev-mult` multiplier renders at. A section
revised exactly once keeps the plain `△ NN` triangle, no count. Computed by
re-reading `.viva/review-input-r{N}.json` round files already on disk (plus
the just-arrived round's own in-hand data); never written back to any round
file or added to `ReviewSection` — this test's "no new persisted state"
check is the acceptance criterion turned into an assertion.
"""
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, get_text, launch_server, post  # noqa: E402


def write_round(viva: Path, n: int, sections: list) -> str:
    """Write `review-input-r{n}.json` and return the exact text written, so
    callers can later assert the file was never touched again."""
    text = json.dumps({"mode": "review", "doc_file": "doc.md", "round": n,
                       "approved_ids": [], "sections": sections})
    (viva / f"review-input-r{n}.json").write_text(text)
    return text


def next_round(base: str, viva: Path, n: int, sections: list) -> None:
    post(base, "/next-round", {
        "mode": "review", "doc_file": "doc.md", "round": n,
        "approved_ids": [], "sections": sections,
        "output": str(viva / f"review-r{n}.json"),
    })


def by_id(data: dict, sid: str) -> dict:
    return next(s for s in data["sections"] if s["id"] == sid)


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()

    r1_sections = [
        {"id": "s1", "title": "Goals", "content": "goals v1"},
        {"id": "s2", "title": "Error Handling", "content": "errors v1"},
    ]
    r1_text = write_round(viva, 1, r1_sections)

    with launch_server(viva / "review-input-r1.json", viva / "review-r1.json",
                       cwd=tmp) as base:

        # Round 1: nothing revised yet — no revision_count on any section.
        data = get(base, "/input")
        assert all("revision_count" not in s for s in data["sections"]), \
            f"round 1 must carry no revision_count: {data['sections']}"

        # Round 2: s1 gets its first diff (cumulative 1). Acceptance
        # criterion: a section revised once shows the plain triangle, no
        # count — so revision_count must stay absent.
        r2_sections = [
            {"id": "s1", "title": "Goals", "content": "goals v2",
             "diff": [{"op": "+", "text": "goals v2"}]},
            {"id": "s2", "title": "Error Handling", "content": "errors v1"},
        ]
        r2_text = write_round(viva, 2, r2_sections)
        next_round(base, viva, 2, r2_sections)
        data = get(base, "/input")
        s1 = by_id(data, "s1")
        assert "revision_count" not in s1, f"cumulative 1 must show no count: {s1}"

        # Round 3: s1 gets a second diff (cumulative 2) — the multiplier
        # threshold. s2 has never been revised and must stay uncounted.
        r3_sections = [
            {"id": "s1", "title": "Goals", "content": "goals v3",
             "diff": [{"op": "+", "text": "goals v3"}]},
            {"id": "s2", "title": "Error Handling", "content": "errors v1"},
        ]
        r3_text = write_round(viva, 3, r3_sections)
        next_round(base, viva, 3, r3_sections)
        data = get(base, "/input")
        s1 = by_id(data, "s1")
        s2 = by_id(data, "s2")
        assert s1["revision_count"] == 2, f"cumulative 2 must show count 2: {s1}"
        assert "revision_count" not in s2, \
            f"never-revised section must carry no count: {s2}"

        # No new persisted state: every round file on disk is byte-identical
        # to what was written — the server never writes revision_count (or
        # anything else) back to a round file.
        assert (viva / "review-input-r1.json").read_text() == r1_text
        assert (viva / "review-input-r2.json").read_text() == r2_text
        assert (viva / "review-input-r3.json").read_text() == r3_text

        # Missing/corrupt historical round file contributes zero rather than
        # raising: corrupt round 2's file, then serve round 4. Without the
        # corruption, cumulative would be 3 (r2's diff + r3's diff + r4's
        # diff); with it, round 2 silently contributes nothing.
        (viva / "review-input-r2.json").write_text("{not json")
        r4_sections = [
            {"id": "s1", "title": "Goals", "content": "goals v4",
             "diff": [{"op": "+", "text": "goals v4"}]},
            {"id": "s2", "title": "Error Handling", "content": "errors v1"},
        ]
        write_round(viva, 4, r4_sections)
        next_round(base, viva, 4, r4_sections)
        data = get(base, "/input")
        s1 = by_id(data, "s1")
        assert s1["revision_count"] == 2, \
            f"corrupt historical round must contribute zero, not error: {s1}"

        # The browser only fetches GET /input once at boot — every round
        # after the first reaches it exclusively via the 'round' SSE event
        # (`REVIEW_DATA = data` in the event handler, no refetch). The count
        # has to ride that push too, or it silently vanishes on every round
        # but the first. Connect before POSTing so this client is registered
        # as an SSE listener (`_push_sse` only writes to already-connected
        # clients) by the time the push fires.
        stream = urllib.request.urlopen(base + "/events", timeout=5)
        time.sleep(0.05)  # let the handler thread finish registering us
        r5_sections = [
            {"id": "s1", "title": "Goals", "content": "goals v5",
             "diff": [{"op": "+", "text": "goals v5"}]},
            {"id": "s2", "title": "Error Handling", "content": "errors v1"},
        ]
        next_round(base, viva, 5, r5_sections)
        pushed = None
        saw_event = False
        for _ in range(50):
            line = stream.readline().decode()
            if not line:
                break
            if line.startswith("event: round"):
                saw_event = True
            elif saw_event and line.startswith("data: "):
                pushed = json.loads(line[len("data: "):])
                break
        stream.close()
        assert pushed is not None, "never received the 'round' SSE push"
        s1 = by_id(pushed, "s1")
        # r1: no diff, r2: corrupt (0), r3: diff, r4: diff, r5 (current): diff
        assert s1["revision_count"] == 3, \
            f"SSE round push must carry the count too, not just GET /input: {s1}"

        # Styling acceptance criteria: label convention (Fragment Mono size
        # inherited, var(--text3), not the triangle's own orange) and no
        # hardcoded hex (DESIGN.md Accessibility req #7).
        page = get_text(base, "/")
        for needle in ('.rev-tri .rev-mult', 'class="rev-mult"',
                       'font-size: 9px; color: var(--text3)', '&times;'):
            assert needle in page, f"page missing: {needle}"

        print("OK")


if __name__ == "__main__":
    main()
