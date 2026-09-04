#!/usr/bin/env python3
"""Integration test: per-section cumulative revision count (#141).

`GET /input` and the `round` SSE event attach `revision_count` to a section
only once its cumulative count this session reaches 2+ (the `.rev-mult`
threshold); computed by re-reading round files on disk, never persisted.

An unreadable historical round file (missing, corrupt JSON, or non-list
`sections`) makes the count a lower bound rather than silently zero — every
section revised this round gets `revision_count_partial: True`, and must not crash.
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


def _test_cumulative_and_sse() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()

    r1_sections = [
        {"id": "s1", "title": "Goals", "content": "goals v1"},
        {"id": "s2", "title": "Error Handling", "content": "errors v1"},
        {"id": "s3", "title": "Appendix", "content": "appendix v1"},
    ]
    r1_text = write_round(viva, 1, r1_sections)

    with launch_server(viva / "review-input-r1.json", viva / "review-r1.json",
                       cwd=tmp) as base:

        # Round 1: nothing revised yet — no revision_count on any section.
        data = get(base, "/input")
        assert all("revision_count" not in s for s in data["sections"]), \
            f"round 1 must carry no revision_count: {data['sections']}"

        # Round 2: s1 gets its first diff (cumulative 1) — plain triangle, no count.
        r2_sections = [
            {"id": "s1", "title": "Goals", "content": "goals v2",
             "diff": [{"op": "+", "text": "goals v2"}]},
            {"id": "s2", "title": "Error Handling", "content": "errors v1"},
            {"id": "s3", "title": "Appendix", "content": "appendix v1"},
        ]
        r2_text = write_round(viva, 2, r2_sections)
        next_round(base, viva, 2, r2_sections)
        data = get(base, "/input")
        s1 = by_id(data, "s1")
        assert "revision_count" not in s1, f"cumulative 1 must show no count: {s1}"

        # Round 3: s1 gets a second diff (cumulative 2) — the multiplier threshold.
        r3_sections = [
            {"id": "s1", "title": "Goals", "content": "goals v3",
             "diff": [{"op": "+", "text": "goals v3"}]},
            {"id": "s2", "title": "Error Handling", "content": "errors v1"},
            {"id": "s3", "title": "Appendix", "content": "appendix v1"},
        ]
        r3_text = write_round(viva, 3, r3_sections)
        next_round(base, viva, 3, r3_sections)
        data = get(base, "/input")
        s1 = by_id(data, "s1")
        s2 = by_id(data, "s2")
        assert s1["revision_count"] == 2, f"cumulative 2 must show count 2: {s1}"
        assert "revision_count" not in s2, \
            f"never-revised section must carry no count: {s2}"

        # No new persisted state: round files stay byte-identical to what was written.
        assert (viva / "review-input-r1.json").read_text() == r1_text
        assert (viva / "review-input-r2.json").read_text() == r2_text
        assert (viva / "review-input-r3.json").read_text() == r3_text

        # A corrupt historical round file contributes zero rather than raising,
        # but the resulting count is flagged as a lower bound, not asserted exact.
        (viva / "review-input-r2.json").write_text("{not json")
        r4_sections = [
            {"id": "s1", "title": "Goals", "content": "goals v4",
             "diff": [{"op": "+", "text": "goals v4"}]},
            {"id": "s2", "title": "Error Handling", "content": "errors v1"},
            # s3's first diff lands here, below threshold, but must still
            # carry the partial flag — the corrupted round could have pushed it over.
            {"id": "s3", "title": "Appendix", "content": "appendix v2",
             "diff": [{"op": "+", "text": "appendix v2"}]},
        ]
        write_round(viva, 4, r4_sections)
        next_round(base, viva, 4, r4_sections)
        data = get(base, "/input")
        s1 = by_id(data, "s1")
        s2 = by_id(data, "s2")
        s3 = by_id(data, "s3")
        assert s1["revision_count"] == 2, \
            f"corrupt historical round must contribute zero, not error: {s1}"
        assert s1.get("revision_count_partial") is True, \
            f"corrupt historical round must flag the count as a lower bound: {s1}"
        assert "revision_count" not in s2 and "revision_count_partial" not in s2, \
            f"never-revised section must carry neither a count nor a partial flag: {s2}"
        assert "revision_count" not in s3, \
            f"a section's first diff still shows no count below the 2+ threshold: {s3}"
        assert s3.get("revision_count_partial") is True, \
            f"a section below threshold must still flag partial history: {s3}"

        # GET /input is only fetched once at boot; every round after reaches
        # the client via the 'round' SSE event, so the count must ride that
        # push too. Connect before POSTing so this client is already registered.
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
        assert s1.get("revision_count_partial") is True, \
            f"SSE round push must carry the partial-history flag too: {s1}"

        # Styling: label uses var(--text3), not the triangle's orange; no hardcoded hex.
        page = get_text(base, "/")
        for needle in ('.rev-tri .rev-mult', 'class="rev-mult"',
                       'font-size: 9px; color: var(--text3)', '&times;'):
            assert needle in page, f"page missing: {needle}"

        # The `.rev-tri` title must name the cumulative count, distinct from
        # the sign-off stamp's "N revisions" wording for rounds_total.
        assert 'content revisions this session' in page, \
            "rev-tri title must name the cumulative count, distinct from rounds_total's wording"

        # The client must render the partial-history flag rather than
        # asserting a deflated count as fact.
        assert 'revision_count_partial' in page, \
            "page must ship the client-side check for the partial-history flag"
        assert '≥${section.revision_count} revisions, partial history' in page, \
            "rev-tri title must be able to render the partial-history signal"
        # Below the 2+ threshold, there's no number to name — must still flag incomplete.
        assert 'partial history, revision count unavailable' in page, \
            "rev-tri title must be able to flag partial history even with no count to name"


def _test_duplicate_titles_dedupe_per_round() -> None:
    """Two sections keying to the same `schema.section_key` (two `## Notes`)
    revised in one round must count as ONE revision, not two."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()

    r1_sections = [
        {"id": "s1", "title": "Notes", "content": "notes v1 a"},
        {"id": "s2", "title": "Notes", "content": "notes v1 b"},
    ]
    write_round(viva, 1, r1_sections)

    with launch_server(viva / "review-input-r1.json", viva / "review-r1.json",
                       cwd=tmp) as base:

        # Round 2: both same-keyed sections get a diff — must count as 1, not 2.
        r2_sections = [
            {"id": "s1", "title": "Notes", "content": "notes v2 a",
             "diff": [{"op": "+", "text": "notes v2 a"}]},
            {"id": "s2", "title": "Notes", "content": "notes v2 b",
             "diff": [{"op": "+", "text": "notes v2 b"}]},
        ]
        write_round(viva, 2, r2_sections)
        next_round(base, viva, 2, r2_sections)
        data = get(base, "/input")
        s1 = by_id(data, "s1")
        s2 = by_id(data, "s2")
        assert "revision_count" not in s1, \
            f"one round's duplicate-titled diffs must count as 1, not 2: {s1}"
        assert "revision_count" not in s2, \
            f"one round's duplicate-titled diffs must count as 1, not 2: {s2}"

        # Round 3: same pair revised again — cumulative must be 2, not 4.
        r3_sections = [
            {"id": "s1", "title": "Notes", "content": "notes v3 a",
             "diff": [{"op": "+", "text": "notes v3 a"}]},
            {"id": "s2", "title": "Notes", "content": "notes v3 b",
             "diff": [{"op": "+", "text": "notes v3 b"}]},
        ]
        write_round(viva, 3, r3_sections)
        next_round(base, viva, 3, r3_sections)
        data = get(base, "/input")
        s1 = by_id(data, "s1")
        s2 = by_id(data, "s2")
        assert s1["revision_count"] == 2, \
            f"two rounds of duplicate-titled diffs must cumulate to 2, not 4: {s1}"
        assert s2["revision_count"] == 2, \
            f"two rounds of duplicate-titled diffs must cumulate to 2, not 4: {s2}"


def _test_null_sections_history_does_not_crash() -> None:
    """A historical round file with `sections: null` (valid JSON, wrong
    shape) must not crash the request — `.get("sections", [])` returns the
    stored `None`, not the default, and iterating it raised uncaught TypeError."""
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()

    r1_sections = [{"id": "s1", "title": "Goals", "content": "goals v1"}]
    write_round(viva, 1, r1_sections)

    with launch_server(viva / "review-input-r1.json", viva / "review-r1.json",
                       cwd=tmp) as base:
        r2_sections = [{"id": "s1", "title": "Goals", "content": "goals v2",
                         "diff": [{"op": "+", "text": "goals v2"}]}]
        write_round(viva, 2, r2_sections)
        next_round(base, viva, 2, r2_sections)

        r3_sections = [{"id": "s1", "title": "Goals", "content": "goals v3",
                         "diff": [{"op": "+", "text": "goals v3"}]}]
        write_round(viva, 3, r3_sections)
        next_round(base, viva, 3, r3_sections)

        # Valid JSON, but "sections" is null — not caught by except (OSError, ValueError) alone.
        (viva / "review-input-r2.json").write_text(json.dumps({
            "mode": "review", "doc_file": "doc.md", "round": 2,
            "approved_ids": [], "sections": None,
        }))
        r4_sections = [{"id": "s1", "title": "Goals", "content": "goals v4",
                         "diff": [{"op": "+", "text": "goals v4"}]}]
        write_round(viva, 4, r4_sections)
        next_round(base, viva, 4, r4_sections)  # must not 500 / raise

        data = get(base, "/input")  # must not raise / disconnect either
        s1 = by_id(data, "s1")
        assert s1["revision_count"] == 2, \
            f"null sections in a historical round must contribute zero, not crash: {s1}"
        assert s1.get("revision_count_partial") is True, \
            f"null sections in a historical round must flag the count as a lower bound: {s1}"


def main() -> None:
    _test_cumulative_and_sse()
    _test_duplicate_titles_dedupe_per_round()
    _test_null_sections_history_does_not_crash()
    print("OK")


if __name__ == "__main__":
    main()
