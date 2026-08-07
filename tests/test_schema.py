#!/usr/bin/env python3
"""Unit tests for scripts/schema.py — the shared protocol contract."""
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import schema  # noqa: E402


def test_section_key_normalizes():
    assert schema.section_key("  Error Handling  ") == "error handling"
    assert schema.section_key("GOALS") == "goals"
    # Identity keeps internal punctuation/spaces — distinct from checklist._norm
    assert schema.section_key("Non-goals") == "non-goals"
    assert schema.section_key("Non-goals") != "nongoals"
    print("  ok  test_section_key_normalizes")


def test_section_key_handles_none_and_empty():
    assert schema.section_key(None) == ""
    assert schema.section_key("") == ""
    assert schema.section_key("   ") == ""
    print("  ok  test_section_key_handles_none_and_empty")


def test_is_ledger_verdict():
    assert schema.is_ledger_verdict("changes") is True
    assert schema.is_ledger_verdict("info") is True
    assert schema.is_ledger_verdict("approved") is False
    assert schema.is_ledger_verdict("pending") is False
    assert schema.is_ledger_verdict(None) is False
    print("  ok  test_is_ledger_verdict")


def test_ledger_note_joins_comments():
    section = {"comments": [
        {"note": "fix the intro"},
        {"note": "and the title"},
        {"note": ""},          # blank notes are dropped from the join
    ]}
    assert schema.ledger_note(section) == "fix the intro · and the title"
    print("  ok  test_ledger_note_joins_comments")


def test_ledger_note_falls_back_to_note():
    # Older single-note shape (no comments[]) reads `note` verbatim
    assert schema.ledger_note({"note": "shorten this"}) == "shorten this"
    # No comments, no note → empty (a changes with no text is valid)
    assert schema.ledger_note({}) == ""
    # Empty comments list falls through to note
    assert schema.ledger_note({"comments": [], "note": "x"}) == "x"
    print("  ok  test_ledger_note_falls_back_to_note")


def test_verdict_to_ledger_entry():
    row = schema.verdict_to_ledger_entry(
        2, "Error Handling",
        {"id": "s2", "verdict": "changes", "comments": [{"note": "5x not 3x"}]},
    )
    assert row == {"round": 2, "section_title": "Error Handling",
                   "verdict": "changes", "note": "5x not 3x"}, row
    # info also earns a row
    assert schema.verdict_to_ledger_entry(
        1, "Goals", {"verdict": "info", "note": "how long?"}) == {
        "round": 1, "section_title": "Goals", "verdict": "info", "note": "how long?"}
    # approved / pending earn nothing
    assert schema.verdict_to_ledger_entry(1, "Goals", {"verdict": "approved"}) is None
    assert schema.verdict_to_ledger_entry(1, "Goals", {"verdict": "pending"}) is None
    print("  ok  test_verdict_to_ledger_entry")


def test_validate_review_input_accepts_valid():
    schema.validate_review_input({
        "mode": "review", "round": 1, "approved_ids": [],
        "sections": [
            {"id": "s1", "title": "Goals", "content": "body"},
            {"id": "s2", "title": "Errors", "content": "",
             "annotations": [{"kind": "drift", "severity": "warn", "message": "x"}]},
        ],
    })
    # Empty section list is structurally valid
    schema.validate_review_input({"mode": "review", "sections": []})
    # `split_on` — the pattern a round was parsed with, carried so the next
    # round re-splits the same way. Optional; a string when present.
    schema.validate_review_input({
        "mode": "review", "split_on": r"^Task \d+",
        "sections": [{"id": "s1", "title": "Task 1", "content": "body"}],
    })
    # `doc_type` — the resolved type name, carried the same way. Optional; a
    # string when present.
    schema.validate_review_input({
        "mode": "review", "doc_type": "design-doc",
        "sections": [{"id": "s1", "title": "Problem & persona", "content": "b"}],
    })
    # `pass` — the round's depth. Every kind, with and without a posture, and
    # the posture is a key INSIDE the object, never beside it.
    for kind in schema.PASS_KINDS:
        schema.validate_review_input({
            "mode": "review", "pass": {"kind": kind},
            "sections": [{"id": "s1", "title": "Goals", "content": "b"}],
        })
        for posture in schema.PASS_POSTURES:
            schema.validate_review_input({
                "mode": "review", "pass": {"kind": kind, "posture": posture},
                "sections": [{"id": "s1", "title": "Goals", "content": "b"}],
            })
    print("  ok  test_validate_review_input_accepts_valid")


def test_validate_review_input_rejects_bad():
    for bad in (
        None,
        {"sections": "nope"},
        {"sections": [{"id": "s1", "title": "T"}]},          # missing content
        {"sections": [{"id": "s1", "content": "c"}]},         # missing title
        {"sections": [{"title": "T", "content": "c"}]},       # missing id
        {"sections": ["not an object"]},
        # `split_on` is the regex the next round re-parses with — a non-string
        # would reach `parse_sections.py --split-on` as a bad argument, or (for
        # None) silently drop back to auto-detection mid-session.
        {"sections": [], "split_on": 123},
        {"sections": [], "split_on": None},
        # `doc_type` is handed back to `parse_sections.py --doc-type` by both
        # `rearm` and a resume — a non-string drops the type, and with it the
        # round's check set, everywhere except where the bad value was written.
        {"sections": [], "doc_type": 123},
        {"sections": [], "doc_type": None},
        # `pass` decides which conjunct `round_is_complete` adds, so a malformed
        # one must fail on write rather than quietly reverting the round to the
        # base rule — the reviewer asked for the stricter check, not the looser.
        {"sections": [], "pass": None},
        {"sections": [], "pass": "line"},          # not an object
        {"sections": [], "pass": []},
        {"sections": [], "pass": {}},              # no kind
        {"sections": [], "pass": {"kind": "polish"}},        # unknown kind
        {"sections": [], "pass": {"kind": None}},
        {"sections": [], "pass": {"kind": "line", "posture": "brutal"}},
        {"sections": [], "pass": {"kind": "line", "posture": None}},
    ):
        try:
            schema.validate_review_input(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")
    print("  ok  test_validate_review_input_rejects_bad")


def test_validate_verdicts_accepts_valid():
    schema.validate_verdicts({"round": 1, "submitted_early": False, "sections": [
        {"id": "s1", "verdict": "approved"},
        {"id": "s2", "verdict": "changes", "comments": [{"note": "x"}]},
        {"id": "s3", "verdict": "pending"},
    ]})
    schema.validate_verdicts({"sections": []})
    print("  ok  test_validate_verdicts_accepts_valid")


def test_validate_verdicts_rejects_bad():
    for bad in (
        None,
        {"sections": "nope"},
        {"sections": [{"id": "s1", "verdict": "bogus"}]},   # unknown verdict
        {"sections": [{"verdict": "approved"}]},             # missing id
        {"sections": [{"id": "s1"}]},                        # missing verdict
    ):
        try:
            schema.validate_verdicts(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")
    print("  ok  test_validate_verdicts_rejects_bad")


def test_schema_reaches_no_io():
    """`round_is_complete()` is the finish guard `loop.py finish` and the
    server's `/complete` handler both ask, from two processes. It must judge the
    dicts handed to it and nothing else — a disk read would let the two call
    sites answer differently for the same round, and would make the server's
    guard readable by whoever last wrote the file rather than by what the human
    submitted.

    Checked as a module property, not a call trace: `schema.py` imports no
    filesystem or serialization module at all, so nothing in it can reach disk.
    AST-walked rather than grepped — the module docstring names `json.dumps` and
    `_input_data` while describing the server's `/input` merge, and a substring
    scan would fire on prose.
    """
    src = (Path(__file__).resolve().parent.parent / "scripts" / "schema.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    banned = {"os", "pathlib", "json", "io", "shutil", "tempfile", "subprocess"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in banned, \
                    "schema.py must stay pure — imports %s" % alias.name
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in banned, \
                "schema.py must stay pure — imports from %s" % node.module
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "open", "schema.py must stay pure — calls open()"
    print("  ok  test_schema_reaches_no_io")


def test_round_is_complete_needs_a_row_per_input_section():
    """The (input, verdicts) signature is what makes a *missing* row visible.

    Scanning verdicts alone cannot see a section that was never submitted, and
    no end-to-end test reaches this: every submit path sends one row per input
    section.
    """
    inp = {"sections": [{"id": "s1"}, {"id": "s2"}]}
    both = {"sections": [{"id": "s1", "verdict": "approved"},
                         {"id": "s2", "verdict": "approved"}]}
    assert schema.round_is_complete(inp, both)

    missing = {"sections": [{"id": "s1", "verdict": "approved"}]}
    assert not schema.round_is_complete(inp, missing), \
        "a section with no verdict row at all is not approved"

    one_open = {"sections": [{"id": "s1", "verdict": "approved"},
                             {"id": "s2", "verdict": "changes"}]}
    assert not schema.round_is_complete(inp, one_open)
    assert not schema.round_is_complete(inp, {}), \
        "no verdicts at all is not a complete round"
    print("  ok  test_round_is_complete_needs_a_row_per_input_section")


def test_round_is_complete_rejects_an_empty_round():
    """`all([])` is True, so the empty-sections guard deliberately inverts
    Python's default. Nothing else pins it: a server-side test asserting a 200
    would still pass if the guard were dropped, because the guard's own shape
    check exempts a sections-less payload first."""
    assert not schema.round_is_complete({"sections": []}, {"sections": []})
    assert not schema.round_is_complete({}, {})
    print("  ok  test_round_is_complete_rejects_an_empty_round")


def _round(pass_spec=None, annotations=None, open_notes=None):
    """A one-section round input, optionally carrying a pass and section state."""
    section = {"id": "s1", "title": "Goals", "content": "body"}
    if annotations is not None:
        section["annotations"] = annotations
    if open_notes is not None:
        section["open_notes"] = open_notes
    data = {"mode": "review", "sections": [section]}
    if pass_spec is not None:
        data["pass"] = pass_spec
    return data


APPROVED = {"sections": [{"id": "s1", "verdict": "approved"}]}


def test_absent_pass_is_todays_behavior_exactly():
    """PRODUCT.md principle 4, at the one place it is load-bearing: a round with
    no `pass` key completes on approvals alone, whatever else it carries.

    The same round state that holds a `fact-check` round open — an unanswered
    check flag — must not hold a passless one, or the field would have changed
    behavior for every caller that never asked for a pass."""
    flag = [{"kind": "headings-present", "severity": "warn", "message": "missing"}]
    assert schema.round_is_complete(_round(annotations=flag), APPROVED)
    assert "pass" not in _round(), "the helper must not default a pass in"
    print("  ok  test_absent_pass_is_todays_behavior_exactly")


def test_structure_and_line_are_the_base_rule():
    """Two of the four kinds add nothing — asserted, not assumed, because the
    only thing stopping a future edit from adding a conjunct here is a test."""
    flag = [{"kind": "headings-present", "severity": "warn", "message": "missing"}]
    for kind in ("structure", "line"):
        assert schema.round_is_complete(
            _round({"kind": kind}, annotations=flag), APPROVED), kind
        assert schema.round_is_complete(
            _round({"kind": kind, "posture": "hard"}), APPROVED), kind
    print("  ok  test_structure_and_line_are_the_base_rule")


def test_fact_check_holds_until_every_check_flag_is_answered():
    """The added conjunct: approvals alone do not close a `fact-check` round.

    A check flag is an annotation whose `kind` is in `CHECK_KINDS` — the handle
    `headings_present.py` documents. An advisory producer's flag is not one, so
    drift/checklist/preference/confidence flags never gate a round.
    """
    fact_check = {"kind": "fact-check"}
    unanswered = [{"kind": "headings-present", "severity": "warn",
                   "message": "missing expected design-doc section: 'Goals'"}]
    answered = [dict(unanswered[0], result="added in round 2")]

    assert not schema.round_is_complete(_round(fact_check, unanswered), APPROVED), \
        "an unanswered check flag must hold the round even with every section approved"
    assert schema.round_is_complete(_round(fact_check, answered), APPROVED)
    # No flags at all — the check ran and found nothing, or none ran.
    assert schema.round_is_complete(_round(fact_check), APPROVED)
    assert schema.round_is_complete(_round(fact_check, []), APPROVED)
    # A blank result answers nothing.
    for blank in ("", "   ", None, 0, ["x"]):
        assert not schema.round_is_complete(
            _round(fact_check, [dict(unanswered[0], result=blank)]), APPROVED), blank
    # An advisory producer's flag is not a check flag.
    advisory = [{"kind": "drift", "severity": "error", "message": "3x vs 5x"},
                {"kind": "confidence", "severity": "warn", "basis": "inferred",
                 "level": "low", "message": "inferred · low"}]
    assert schema.round_is_complete(_round(fact_check, advisory), APPROVED), \
        "a fact-check pass gates on checks, not on every advisory badge"
    print("  ok  test_fact_check_holds_until_every_check_flag_is_answered")


def test_proof_holds_on_an_unresolved_suggested_edit():
    """`proof` reduces to the base today — nothing writes a `suggestion` comment
    yet — so the conjunct is exercised against the shapes the later story will
    write, proving it is wired rather than merely declared."""
    proof = {"kind": "proof"}
    assert schema.round_is_complete(_round(proof), APPROVED)

    # A suggestion in the verdicts just submitted.
    with_suggestion = {"sections": [
        {"id": "s1", "verdict": "approved",
         "comments": [{"cid": "s1-c1", "type": "suggestion",
                       "note": "use this wording"}]}]}
    assert not schema.round_is_complete(_round(proof), with_suggestion)
    assert schema.round_is_complete(_round(), with_suggestion), \
        "no pass, no conjunct — the same round closes without one"
    settled = {"sections": [
        {"id": "s1", "verdict": "approved",
         "comments": [{"cid": "s1-c1", "type": "suggestion", "settled": True}]}]}
    assert schema.round_is_complete(_round(proof), settled)

    # A carried thread whose LATEST exchange is the suggestion — the author has
    # not answered it. An answered one (a later exchange) does not hold.
    open_thread = [{"cid": "s1-c1", "status": "open", "exchanges": [
        {"round": 1, "verdict": "suggestion", "note": "use this wording"}]}]
    answered_thread = [{"cid": "s1-c1", "status": "open", "exchanges": [
        {"round": 1, "verdict": "suggestion", "note": "use this wording"},
        {"round": 2, "verdict": "changes", "note": "and one more thing"}]}]
    assert not schema.round_is_complete(
        _round(proof, open_notes=open_thread), APPROVED)
    assert schema.round_is_complete(
        _round(proof, open_notes=answered_thread), APPROVED)
    print("  ok  test_proof_holds_on_an_unresolved_suggested_edit")


def test_no_pass_relaxes_the_all_approved_base():
    """THE invariant: a pass may only ADD conditions, never relax the base.

    Enumerated from `PASS_KINDS` plus `None` rather than a hardcoded list, so a
    fifth kind is covered the day it is added instead of being silently exempt.
    Every round below is one today's rule refuses; no pass may accept any of
    them, now or later — one that could would reopen the hole #102 closed,
    `POST /complete` accepting a round the human never approved.
    """
    inp_sections = [{"id": "s1", "title": "Goals", "content": "b"},
                    {"id": "s2", "title": "Scope", "content": "c"}]
    refused = [
        ("one section carries changes", {"sections": [
            {"id": "s1", "verdict": "approved"},
            {"id": "s2", "verdict": "changes"}]}),
        ("one section still pending", {"sections": [
            {"id": "s1", "verdict": "approved"},
            {"id": "s2", "verdict": "pending"}]}),
        ("a section has no verdict row at all", {"sections": [
            {"id": "s1", "verdict": "approved"}]}),
        ("no verdicts at all", {}),
        ("empty verdicts", {"sections": []}),
    ]
    for kind in schema.PASS_KINDS + (None,):
        data = {"mode": "review", "sections": inp_sections}
        if kind is not None:
            data["pass"] = {"kind": kind}
        for why, verdicts in refused:
            assert not schema.round_is_complete(data, verdicts), (
                "pass %r accepted a round today's rule refuses (%s) — a pass may "
                "only add conditions, never relax the all-approved base"
                % (kind, why))
        # …and the empty round stays refused under every pass too, since
        # `all([])` would otherwise say yes.
        empty = {"mode": "review", "sections": []}
        if kind is not None:
            empty["pass"] = {"kind": kind}
        assert not schema.round_is_complete(empty, {"sections": []}), kind
    print("  ok  test_no_pass_relaxes_the_all_approved_base")


def test_check_kinds_covers_every_shipped_bundle_check():
    """`CHECK_KINDS` is what a `fact-check` round recognizes as a check flag, and
    a missing entry fails OPEN — the flag becomes invisible and the round closes
    when it should have held. So every check a shipped type bundle names must be
    in it. Not the reverse: a registered kind no bundle names yet is fine.
    """
    types_dir = Path(__file__).resolve().parent.parent / "types"
    named = {check
             for p in sorted(types_dir.glob("*.json"))
             for check in json.loads(p.read_text(encoding="utf-8")).get("checks", [])}
    assert named, "no shipped bundle names any check — did types/ move?"
    missing = sorted(named - set(schema.CHECK_KINDS))
    assert not missing, (
        "shipped type bundles name check(s) %s that schema.CHECK_KINDS does not "
        "carry — a fact-check round would never see their flags" % missing)
    print("  ok  test_check_kinds_covers_every_shipped_bundle_check")


def test_has_revision_history_is_anchored():
    """Substring matching is the defect this replaces: viva's own SKILL.md and
    DESIGN.md discuss the ledger by name, and a false positive takes `start`'s
    resume branch — which can pre-approve a section the human never saw."""
    assert schema.has_revision_history("# D\n\n## Revision History\n\nrow")
    assert schema.has_revision_history("## Revision History   \n")
    assert not schema.has_revision_history(
        "the parser appends `## Revision History` at sign-off"), \
        "a mention inside backticks is not a signed-off doc"
    # Residue, documented rather than asserted away: a fenced block whose
    # content starts the line still matches. Every real mention in this repo is
    # inline-backticked mid-line, which is the reported defect and is fixed.
    assert not schema.has_revision_history("### Revision History\n"), \
        "a different heading level is a different heading"
    print("  ok  test_has_revision_history_is_anchored")


def main():
    test_section_key_normalizes()
    test_section_key_handles_none_and_empty()
    test_is_ledger_verdict()
    test_ledger_note_joins_comments()
    test_ledger_note_falls_back_to_note()
    test_verdict_to_ledger_entry()
    test_validate_review_input_accepts_valid()
    test_validate_review_input_rejects_bad()
    test_validate_verdicts_accepts_valid()
    test_validate_verdicts_rejects_bad()
    test_schema_reaches_no_io()
    test_round_is_complete_needs_a_row_per_input_section()
    test_round_is_complete_rejects_an_empty_round()
    test_absent_pass_is_todays_behavior_exactly()
    test_structure_and_line_are_the_base_rule()
    test_fact_check_holds_until_every_check_flag_is_answered()
    test_proof_holds_on_an_unresolved_suggested_edit()
    test_no_pass_relaxes_the_all_approved_base()
    test_check_kinds_covers_every_shipped_bundle_check()
    test_has_revision_history_is_anchored()
    print("OK (20 tests)")


if __name__ == "__main__":
    main()
