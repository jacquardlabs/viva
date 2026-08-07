#!/usr/bin/env python3
"""Unit tests for open_notes.update — per-cid threading (multi-comment)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import open_notes  # noqa: E402
import schema  # noqa: E402


def _input():
    return {"sections": [{"id": "s1", "title": "Goals"}, {"id": "s2", "title": "Scope"}]}


def test_two_open_comments_become_two_threads():
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "changes", "note": "5x not 3x",
         "anchor": {"text": "retries 3x", "offset": 10}, "open": True, "settled": False},
        {"cid": "s1-c2", "type": "info", "note": "why stderr?", "open": True, "settled": False},
    ]}]}
    out = open_notes.update({}, 1, verdicts, _input(), {"s1-c1": "set to 5x"})
    assert set(out) == {"s1-c1", "s1-c2"}
    assert out["s1-c1"]["title"] == "Goals"
    assert out["s1-c1"]["quote"] == "retries 3x"
    assert out["s1-c1"]["status"] == "open"
    assert out["s1-c1"]["exchanges"][0] == {
        "round": 1, "verdict": "changes", "note": "5x not 3x", "response": "set to 5x"}
    assert out["s1-c2"]["exchanges"][0]["response"] == ""  # no response supplied


def test_settle_one_thread_by_cid():
    store = {"s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "x",
                       "status": "open", "exchanges": []}}
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "changes", "note": "", "open": True, "settled": True}]}]}
    out = open_notes.update(store, 2, verdicts, _input(), {})
    assert out["s1-c1"]["status"] == "settled"


def test_approving_section_settles_all_its_threads():
    store = {
        "s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "x", "status": "open", "exchanges": []},
        "s1-c2": {"cid": "s1-c2", "title": "Goals", "quote": "y", "status": "open", "exchanges": []},
        "s2-c1": {"cid": "s2-c1", "title": "Scope", "quote": "z", "status": "open", "exchanges": []},
    }
    verdicts = {"sections": [{"id": "s1", "verdict": "approved", "comments": []}]}
    out = open_notes.update(store, 2, verdicts, _input(), {})
    assert out["s1-c1"]["status"] == "settled"
    assert out["s1-c2"]["status"] == "settled"
    assert out["s2-c1"]["status"] == "open"  # untouched section stays open


def test_no_comments_is_noop():
    verdicts = {"sections": [{"id": "s1", "verdict": "approved"}]}
    assert open_notes.update({}, 1, verdicts, _input(), {}) == {}


def test_legacy_section_without_comments_is_noop():
    """A bare legacy section {anchor, note, open} with no `comments` key must
    not crash open_notes.update and must leave the store empty — an in-flight
    old round file cannot break the thread store."""
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "note": "x",
                              "anchor": "y", "open": True}]}
    assert open_notes.update({}, 1, verdicts, _input(), {}) == {}


def test_escalated_reply_appends_changes_exchange():
    """A reply that escalates an info thread to `request changes` arrives as a
    comment on the SAME cid with type "changes"; open_notes.update appends it as
    a new exchange whose verdict is "changes" — the per-turn record the hybrid
    rewrite rule reads to decide whether to edit the section."""
    store = {"s1-c1": {"cid": "s1-c1", "title": "Goals", "quote": "x",
                       "status": "open", "exchanges": [
                           {"round": 1, "verdict": "info", "note": "why?", "response": "because"}]}}
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "changes", "note": "make it configurable",
         "open": True, "settled": False, "reply": True}]}]}
    out = open_notes.update(store, 2, verdicts, _input(), {"s1-c1": "exposed RETRY_MAX"})
    exs = out["s1-c1"]["exchanges"]
    assert len(exs) == 2, exs
    assert exs[1] == {"round": 2, "verdict": "changes",
                      "note": "make it configurable", "response": "exposed RETRY_MAX"}, exs
    assert out["s1-c1"]["status"] == "open"  # still open until settled


def test_suggestion_thread_carries_its_replacement():
    """A suggestion threads like any other comment, and the WORDING rides along.

    Without `replacement` on the exchange, round N+1 re-presents the thread with
    the rationale and the wording stripped — and "apply verbatim" has nothing
    left to apply. `schema.round_is_complete`'s `final` conjunct also reads the
    latest exchange's `verdict`, so an unthreaded suggestion would silently stop
    holding the round (#166).
    """
    wording = "Ship the core in one round."
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "suggestion", "note": "too vague",
         "replacement": wording, "anchor": {"text": "ship it", "offset": 3},
         "open": True, "settled": False},
    ]}]}
    out = open_notes.update({}, 1, verdicts, _input(), {})
    assert set(out) == {"s1-c1"}, out
    assert out["s1-c1"]["exchanges"][0] == {
        "round": 1, "verdict": "suggestion", "note": "too vague",
        "response": "", "replacement": wording}, out["s1-c1"]

    # Presence-gated: a changes/info exchange is byte-identical to before.
    plain = {"sections": [{"id": "s2", "verdict": "info", "comments": [
        {"cid": "s2-c1", "type": "info", "note": "how long?",
         "open": True, "settled": False}]}]}
    assert open_notes.update({}, 1, plain, _input(), {})["s2-c1"]["exchanges"][0] == {
        "round": 1, "verdict": "info", "note": "how long?", "response": ""}


def _declined_store():
    """The store one round after the author declined `s1-c1` with grounds."""
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "changes", "note": "cut the caveat",
         "anchor": {"text": "in most cases", "offset": 4},
         "open": True, "settled": False}]}]}
    return open_notes.update({}, 1, verdicts, _input(), {},
                             {"s1-c1": "round 1 ruled the caveat load-bearing"})


def test_decline_records_grounds_and_holds_the_thread():
    """A decline is a THREAD status, not a verdict (#167).

    It rides on the same exchange the reviewer's turn created, and it resolves
    nothing: the thread is unresolved, so `parse_sections` re-presents it and
    the section stays held until the reviewer settles or insists.
    """
    out = _declined_store()
    assert out["s1-c1"]["status"] == "declined", out["s1-c1"]
    assert out["s1-c1"]["exchanges"][0] == {
        "round": 1, "verdict": "changes", "note": "cut the caveat",
        "response": "", "grounds": "round 1 ruled the caveat load-bearing"}, out
    assert schema.thread_is_unresolved(out["s1-c1"]["status"]), (
        "a declined thread must carry forward exactly as an open one does")

    # The exchange's `verdict` stays the REVIEWER's comment type — the author's
    # answer never overwrites the request. `schema._has_unresolved_suggestion`
    # reads that field, so a declined suggestion still holds a `final` round.
    assert out["s1-c1"]["exchanges"][0]["verdict"] == "changes"

    # Presence-gated: a turn nobody declined is byte-identical to before.
    plain = open_notes.update({}, 1, {"sections": [
        {"id": "s2", "verdict": "info", "comments": [
            {"cid": "s2-c1", "type": "info", "note": "how long?",
             "open": True, "settled": False}]}]}, _input(), {})
    assert plain["s2-c1"]["exchanges"][0] == {
        "round": 1, "verdict": "info", "note": "how long?", "response": ""}
    assert plain["s2-c1"]["status"] == "open"


def test_a_response_may_accompany_a_decline():
    """Grounds are why the author did not comply; a response, if any, is what
    they did instead — one turn, one exchange, both fields."""
    verdicts = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "changes", "note": "delete the claim",
         "open": True, "settled": False}]}]}
    out = open_notes.update({}, 1, verdicts, _input(),
                            {"s1-c1": "narrowed it to the measured case"},
                            {"s1-c1": "the benchmark in §2 supports it"})
    assert out["s1-c1"]["exchanges"][0]["response"] == "narrowed it to the measured case"
    assert out["s1-c1"]["exchanges"][0]["grounds"] == "the benchmark in §2 supports it"
    assert out["s1-c1"]["status"] == "declined"


def test_insisting_reopens_the_thread_and_wins():
    """The reviewer's reply to a decline returns the thread to `open`, and the
    author has no second decline on it — insisting always wins (#167)."""
    store = _declined_store()
    insist = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "changes", "note": "cut it anyway",
         "open": True, "settled": False, "reply": True}]}]}

    # Complying: the reply appends a turn and the thread is open again.
    out = open_notes.update(store, 2, insist, _input(), {"s1-c1": "cut"})
    assert out["s1-c1"]["status"] == "open", out["s1-c1"]
    assert len(out["s1-c1"]["exchanges"]) == 2

    # Declining a second time is refused — loudly, before any round ships.
    try:
        open_notes.update(store, 2, insist, _input(), {},
                          {"s1-c1": "still contradicts round 1"})
    except ValueError as e:
        assert "s1-c1" in str(e), e
    else:
        assert False, "a second decline on the same thread must be refused"

    # The refusal is per thread, not global: a fresh thread may still decline.
    fresh = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c2", "type": "changes", "note": "rename it",
         "open": True, "settled": False}]}]}
    out = open_notes.update(store, 2, fresh, _input(), {}, {"s1-c2": "the name is the API"})
    assert out["s1-c2"]["status"] == "declined"


def test_approving_the_section_settles_a_declined_thread():
    """Approving is how the reviewer ACCEPTS a decline, so the approve branch
    settles every unresolved thread on the section, not only the open ones."""
    store = _declined_store()
    assert store["s1-c1"]["status"] == "declined"
    out = open_notes.update(store, 2, {"sections": [
        {"id": "s1", "verdict": "approved", "comments": []}]}, _input(), {})
    assert out["s1-c1"]["status"] == "settled", out["s1-c1"]


def main():
    test_two_open_comments_become_two_threads()
    test_decline_records_grounds_and_holds_the_thread()
    test_a_response_may_accompany_a_decline()
    test_insisting_reopens_the_thread_and_wins()
    test_approving_the_section_settles_a_declined_thread()
    test_suggestion_thread_carries_its_replacement()
    test_settle_one_thread_by_cid()
    test_approving_section_settles_all_its_threads()
    test_no_comments_is_noop()
    test_legacy_section_without_comments_is_noop()
    test_escalated_reply_appends_changes_exchange()
    print("OK")


if __name__ == "__main__":
    main()
