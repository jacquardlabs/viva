#!/usr/bin/env python3
"""Integration test: a suggested edit round-trips /submit and reaches the ledger.

A suggestion is the third comment type (#166) — a directive with the wording
attached. This drives the surface a caller and the agent actually see: the
verdicts file the browser writes, the live `/input` ledger, the `400` a
suggestion with no wording earns at the boundary, and the page's own affordances
(the third chip, the typed highlight, the derivation that keeps such a section
out of `approved`).
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import (get, get_text, launch_server,  # noqa: E402
                             poll_for, post, post_result)

WORDING = "Ship the core in one round | no exceptions"


def _round(n: int) -> dict:
    return {
        "mode": "review",
        "doc_file": "doc.md",
        "round": n,
        "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "ship it eventually"},
            {"id": "s2", "title": "Scope", "content": "scope body"},
        ],
    }


def test_suggestion_round_trips_and_lands_in_the_ledger() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(_round(1)))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        suggestion = {
            "cid": "s1-c1", "type": "suggestion", "note": "too vague",
            "replacement": WORDING,
            "anchor": {"text": "ship it eventually", "offset": 0, "occurrence": 0},
            "open": True, "settled": False,
        }
        # The section verdict is `changes` — a suggestion is a directive, so the
        # browser's derivation never lets a section holding one read `approved`.
        post(base, "/submit", {"round": 1, "submitted_early": False, "sections": [
            {"id": "s1", "verdict": "changes", "comments": [suggestion]},
            {"id": "s2", "verdict": "approved"},
        ]})
        assert poll_for(viva / "out1.json"), "server never wrote the verdicts file"
        written = json.loads((viva / "out1.json").read_text())
        # Verbatim on disk: the payload the agent applies survives the round-trip
        # with its anchor, byte for byte.
        assert written["sections"][0]["comments"][0] == suggestion, written

        post(base, "/next-round?output=" + str(viva / "out2.json"), _round(2))
        ledger = get(base, "/input")["ledger"]
        assert ledger == [{"round": 1, "section_title": "Goals",
                           "verdict": "changes",
                           "note": "too vague — suggested: " + WORDING}], ledger
        assert WORDING in ledger[0]["note"], "the wording must be recorded verbatim"

        # A suggestion with no wording is unappliable — refused where it is
        # written, not silently at apply time. Gated on the TYPE, so a plain
        # changes comment with no replacement still passes.
        status, body = post_result(base, "/submit", {
            "round": 2, "submitted_early": False, "sections": [
                {"id": "s1", "verdict": "changes", "comments": [
                    {"cid": "s1-c1", "type": "suggestion", "note": "reword this"}]},
            ]})
        assert status == 400, (status, body)
        assert "replacement" in body.get("error", ""), body
        assert post_result(base, "/submit", {
            "round": 2, "submitted_early": False, "sections": [
                {"id": "s1", "verdict": "changes", "comments": [
                    {"cid": "s1-c1", "type": "changes", "note": "reword this"}]},
            ]})[0] == 200
    print("  ok  test_suggestion_round_trips_and_lands_in_the_ledger")


def test_page_ships_the_suggestion_affordances() -> None:
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "in1.json").write_text(json.dumps(_round(1)))
    with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
        page = get_text(base, "/")

    # The chip, the replacement field (on `.note-field`, so it inherits the
    # square-corners rule DESIGN.md declares authoritative), and the typed
    # highlight.
    for needle in (
        'class="cmt-chip cmt-chip-suggestion" data-type="suggestion">suggest wording',
        'class="note-field cmt-pop-repl"',
        "mark.cmt-hl-suggestion",
        ".cmt-chip-suggestion.is-on",
        ".v-suggestion .cmt-type",
        ".cmt-repl",
    ):
        assert needle in page, f"page missing: {needle}"

    # Derivation: a suggestion is a directive, so it lands with `changes`, and a
    # section holding one cannot be approved.
    assert ("return active.some(c => c.type === 'changes' || c.type === 'suggestion') "
            "? 'changes' : 'info';") in page, "deriveVerdict must treat a suggestion as a directive"
    # Its note is optional — the wording alone keeps the comment active.
    assert "filter(c => !c.settled && (c.note || c.replacement))" in page, \
        "activeComments must count a suggestion carrying only its wording"
    # Stale marks: the re-render clears every typed highlight it can create.
    assert "'mark.cmt-hl-changes, mark.cmt-hl-info, mark.cmt-hl-suggestion'" in page, \
        "renderHighlights must clear the suggestion mark it creates"
    # Review-mode only: a diff hunk's suggestion would be a verbatim code edit,
    # which /viva-diff carries no instruction to apply (#166 scopes it out).
    assert "const canSuggest = !REVIEW_DATA || REVIEW_DATA.mode !== 'diff';" in page, \
        "the suggestion chip must be gated out of diff mode"
    # A reply to a carried suggestion thread continues as `changes`: the reply
    # box collects prose, and a suggestion with no `replacement` is a 400.
    assert "const type = (last === 'changes' || last === 'suggestion') ? 'changes' : 'info';" in page, \
        "a suggestion thread's reply must not re-post as an empty suggestion"
    print("  ok  test_page_ships_the_suggestion_affordances")


def main() -> None:
    test_suggestion_round_trips_and_lands_in_the_ledger()
    test_page_ships_the_suggestion_affordances()
    print("OK")


if __name__ == "__main__":
    main()
