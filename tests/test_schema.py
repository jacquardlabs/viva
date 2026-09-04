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


def test_ledger_note_records_suggested_wording_verbatim():
    """A suggestion is a ledger event; the wording is the event (#166).
    Asserted on `ledger_note`'s own output, not a rendered markdown row —
    `esc_cell` escapes/flattens newlines on the way into the table."""
    wording = "Ship the core in one round | no exceptions"
    # Note + wording: the note is rationale, the wording is the payload.
    assert schema.ledger_note({"comments": [
        {"type": "suggestion", "note": "too vague", "replacement": wording},
    ]}) == "too vague — suggested: " + wording
    # A suggestion needs no note — the wording alone is a full row, and the
    # blank-note filter must not drop it.
    assert schema.ledger_note({"comments": [
        {"type": "suggestion", "note": "", "replacement": wording},
    ]}) == "suggested: " + wording
    # Mixed section: fragments join with the same ` · ` as before.
    assert schema.ledger_note({"comments": [
        {"type": "changes", "note": "5x not 3x"},
        {"type": "suggestion", "replacement": "retries 5x"},
        {"type": "info", "note": "how long?"},
    ]}) == "5x not 3x · suggested: retries 5x · how long?"
    # Tagged, not bare: the ledger row's own `verdict` column carries the
    # SECTION verdict, so the fragment is the only place the type shows.
    row = schema.verdict_to_ledger_entry(
        3, "Goals",
        {"id": "s1", "verdict": "changes",
         "comments": [{"type": "suggestion", "replacement": wording}]})
    assert row == {"round": 3, "section_title": "Goals", "verdict": "changes",
                   "note": "suggested: " + wording}, row
    assert wording in row["note"]
    print("  ok  test_ledger_note_records_suggested_wording_verbatim")


def test_comment_types_carry_the_third_type():
    # One tuple names the comment axis; open_notes.py threads on it, so a type
    # missing here never carries across a round.
    assert schema.SUGGESTION == "suggestion"
    assert schema.COMMENT_TYPES == ("changes", "info", schema.SUGGESTION)
    # A different axis from the section verdicts — a suggestion derives to
    # `changes` and is never a verdict or a ledger verdict of its own.
    assert schema.SUGGESTION not in schema.VERDICTS
    assert schema.SUGGESTION not in schema.LEDGER_VERDICTS
    print("  ok  test_comment_types_carry_the_third_type")


def test_validate_verdicts_requires_replacement_on_a_suggestion():
    """The wording IS the comment; an empty one is unappliable (#166)."""
    ok = {"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "suggestion", "replacement": "Ship it."}]}]}
    schema.validate_verdicts(ok)  # must not raise
    for bad in ({"cid": "s1-c1", "type": "suggestion"},
                {"cid": "s1-c1", "type": "suggestion", "replacement": ""},
                {"cid": "s1-c1", "type": "suggestion", "replacement": "   "},
                {"cid": "s1-c1", "type": "suggestion", "replacement": 7}):
        try:
            schema.validate_verdicts(
                {"sections": [{"id": "s1", "verdict": "changes", "comments": [bad]}]})
        except ValueError as e:
            assert "replacement" in str(e), e
        else:
            raise AssertionError("accepted a suggestion with no wording: %r" % bad)
    # Gated on the TYPE, so nothing written before this type existed can trip
    # it: a changes/info comment needs no replacement, and a non-dict entry in
    # comments stays as permissive as it was.
    schema.validate_verdicts({"sections": [{"id": "s1", "verdict": "changes", "comments": [
        {"cid": "s1-c1", "type": "changes", "note": "5x not 3x"},
        {"cid": "s1-c2", "type": "info", "note": "how long?"},
        "not a dict",
    ]}]})
    print("  ok  test_validate_verdicts_requires_replacement_on_a_suggestion")


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
    # `summary` — the agent's one-liner under the card title. Optional; a
    # string when present, and an empty one is legal (a section the agent
    # deliberately left undescribed carries no key, but "" is not malformed).
    schema.validate_review_input({
        "mode": "diff",
        "sections": [
            {"id": "s1", "title": "server.py hunk 1", "content": "```diff\n@@\n```",
             "summary": "guards the finish path against an unapproved round"},
            {"id": "s2", "title": "server.py hunk 2", "content": "x", "summary": ""},
            {"id": "s3", "title": "server.py hunk 3", "content": "y"},
        ],
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
        # `summary` reaches a render site under the card title, so a present
        # non-string must fail on write rather than print as `null` or
        # `[object Object]` in the one place the reviewer navigates by.
        {"sections": [{"id": "s1", "title": "T", "content": "c", "summary": None}]},
        {"sections": [{"id": "s1", "title": "T", "content": "c", "summary": 12}]},
        {"sections": [{"id": "s1", "title": "T", "content": "c",
                       "summary": ["a", "b"]}]},
        # The gate is per-section, not "the first section" — a bad value on a
        # later one must fail too.
        {"sections": [{"id": "s1", "title": "T", "content": "c", "summary": "fine"},
                      {"id": "s2", "title": "U", "content": "d", "summary": 0}]},
    ):
        try:
            schema.validate_review_input(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")
    print("  ok  test_validate_review_input_rejects_bad")


def test_id_must_be_a_bare_token():
    """A section/question `id` reaches an HTML attribute context unescaped —
    the boundary must reject anything that could break out of one, not just
    require a string (a security-posture finding)."""
    for bad_id in ('s1"', "s1<script>", "s1 two", "s1'x", "s1&amp;", "", "s" * 65):
        try:
            schema.validate_review_input({"sections": [
                {"id": bad_id, "title": "T", "content": "c"}]})
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for id {bad_id!r}")
        try:
            schema.validate_verdicts({"sections": [
                {"id": bad_id, "verdict": "approved"}]})
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for output id {bad_id!r}")
        try:
            schema.validate_qa_input({"questions": [
                {"id": bad_id, "text": "Which?"}]})
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for question id {bad_id!r}")
    # The shapes every mechanical producer actually mints still pass.
    for good_id in ("s1", "s12", "q1", "a-b_c.1"):
        schema.validate_review_input({"sections": [
            {"id": good_id, "title": "T", "content": "c"}]})
        schema.validate_verdicts({"sections": [
            {"id": good_id, "verdict": "approved"}]})
        schema.validate_qa_input({"questions": [
            {"id": good_id, "text": "Which?"}]})
    print("  ok  test_id_must_be_a_bare_token")


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
    """`schema.py` must stay pure — `round_is_complete()` is asked by
    `loop.py finish` and the server's `/complete` handler from separate
    processes, and must judge only the dicts handed to it, never disk.

    AST-walked rather than grepped, since the module docstring mentions
    `json.dumps` in prose and a substring scan would fire on that.
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
    """The (input, verdicts) signature makes a *missing* row visible — scanning
    verdicts alone cannot see a section that was never submitted."""
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
    Python's default — nothing else pins this."""
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
    """PRODUCT.md principle 4: a round with no `pass` key completes on
    approvals alone, whatever else it carries. The same state that holds a
    `checks` round open must not hold a passless one."""
    flag = [{"kind": "headings-present", "severity": "warn", "message": "missing"}]
    assert schema.round_is_complete(_round(annotations=flag), APPROVED)
    assert "pass" not in _round(), "the helper must not default a pass in"
    print("  ok  test_absent_pass_is_todays_behavior_exactly")


def test_structure_and_line_are_the_base_rule():
    """Two of the four kinds add nothing — asserted, not assumed, because the
    only thing stopping a future edit from adding a conjunct here is a test."""
    flag = [{"kind": "headings-present", "severity": "warn", "message": "missing"}]
    for kind in ("architecture", "line"):
        assert schema.round_is_complete(
            _round({"kind": kind}, annotations=flag), APPROVED), kind
        assert schema.round_is_complete(
            _round({"kind": kind, "posture": "hard"}), APPROVED), kind
    print("  ok  test_structure_and_line_are_the_base_rule")


def test_checks_pass_holds_until_every_check_flag_is_answered():
    """The added conjunct: approvals alone do not close a `checks` round. A
    check flag is an annotation whose `kind` is in `CHECK_KINDS`; an advisory
    producer's flag (drift/checklist/preference/confidence) never gates one."""
    checks_pass = {"kind": "checks"}
    unanswered = [{"kind": "headings-present", "severity": "warn",
                   "message": "missing expected design-doc section: 'Goals'"}]
    answered = [dict(unanswered[0], result="added in round 2")]

    assert not schema.round_is_complete(_round(checks_pass, unanswered), APPROVED), \
        "an unanswered check flag must hold the round even with every section approved"
    assert schema.round_is_complete(_round(checks_pass, answered), APPROVED)
    # No flags at all — the check ran and found nothing, or none ran.
    assert schema.round_is_complete(_round(checks_pass), APPROVED)
    assert schema.round_is_complete(_round(checks_pass, []), APPROVED)
    # A blank result answers nothing.
    for blank in ("", "   ", None, 0, ["x"]):
        assert not schema.round_is_complete(
            _round(checks_pass, [dict(unanswered[0], result=blank)]), APPROVED), blank
    # An advisory producer's flag is not a check flag.
    advisory = [{"kind": "drift", "severity": "error", "message": "3x vs 5x"},
                {"kind": "confidence", "severity": "warn", "basis": "inferred",
                 "level": "low", "message": "inferred · low"}]
    assert schema.round_is_complete(_round(checks_pass, advisory), APPROVED), \
        "a checks pass gates on checks, not on every advisory badge"
    print("  ok  test_checks_pass_holds_until_every_check_flag_is_answered")


def test_proof_holds_on_an_unresolved_suggested_edit():
    """`final` adds a conjunct on the comment type the popover now writes (#166):
    an unresolved suggestion holds the round even when every section is
    approved."""
    final = {"kind": "final"}
    assert schema.round_is_complete(_round(final), APPROVED)

    # A suggestion in the verdicts just submitted.
    with_suggestion = {"sections": [
        {"id": "s1", "verdict": "approved",
         "comments": [{"cid": "s1-c1", "type": "suggestion",
                       "note": "use this wording",
                       "replacement": "Ship the core in one round."}]}]}
    assert not schema.round_is_complete(_round(final), with_suggestion)
    assert schema.round_is_complete(_round(), with_suggestion), \
        "no pass, no conjunct — the same round closes without one"
    settled = {"sections": [
        {"id": "s1", "verdict": "approved",
         "comments": [{"cid": "s1-c1", "type": "suggestion", "settled": True}]}]}
    assert schema.round_is_complete(_round(final), settled)

    # A carried thread whose LATEST exchange is the suggestion — the author has
    # not answered it. An answered one (a later exchange) does not hold.
    open_thread = [{"cid": "s1-c1", "status": "open", "exchanges": [
        {"round": 1, "verdict": "suggestion", "note": "use this wording"}]}]
    answered_thread = [{"cid": "s1-c1", "status": "open", "exchanges": [
        {"round": 1, "verdict": "suggestion", "note": "use this wording"},
        {"round": 2, "verdict": "changes", "note": "and one more thing"}]}]
    assert not schema.round_is_complete(
        _round(final, open_notes=open_thread), APPROVED)
    assert schema.round_is_complete(
        _round(final, open_notes=answered_thread), APPROVED)
    print("  ok  test_proof_holds_on_an_unresolved_suggested_edit")


def test_thread_statuses_and_a_declined_suggestion_still_holds_proof():
    """A decline is a thread status, never a verdict, and resolves nothing.
    `open` and `declined` are the two unresolved statuses; a declined
    suggestion still holds a `final` round since the author's refusal is not
    a resolution (#167)."""
    assert schema.THREAD_STATUSES == ("open", "settled", "declined")
    assert schema.THREAD_DECLINED not in schema.VERDICTS
    assert schema.THREAD_DECLINED not in schema.COMMENT_TYPES
    assert schema.thread_is_unresolved("open")
    assert schema.thread_is_unresolved(schema.THREAD_DECLINED)
    assert not schema.thread_is_unresolved(schema.THREAD_SETTLED)
    assert not schema.thread_is_unresolved(None) and not schema.thread_is_unresolved("nope")

    declined_thread = [{"cid": "s1-c1", "status": schema.THREAD_DECLINED,
                        "exchanges": [{"round": 1, "verdict": "suggestion",
                                       "note": "use this wording",
                                       "grounds": "contradicts round 1"}]}]
    assert not schema.round_is_complete(
        _round({"kind": "final"}, open_notes=declined_thread), APPROVED), \
        "declining a suggested edit must not release the final conjunct"
    print("  ok  test_thread_statuses_and_a_declined_suggestion_still_holds_proof")


def test_no_pass_relaxes_the_all_approved_base():
    """THE invariant: a pass may only ADD conditions, never relax the base.
    Enumerated from `PASS_KINDS` plus `None` so a new kind is covered
    automatically; accepting any of these refused rounds would reopen the
    hole #102 closed."""
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
    """`CHECK_KINDS` is what a `checks` round recognizes as a check flag. A
    missing entry fails OPEN — the round closes when it should have held —
    so every check a shipped bundle names must be here (not the reverse)."""
    types_dir = Path(__file__).resolve().parent.parent / "types"
    named = {check
             for p in sorted(types_dir.glob("*.json"))
             for check in json.loads(p.read_text(encoding="utf-8")).get("checks", [])}
    assert named, "no shipped bundle names any check — did types/ move?"
    missing = sorted(named - set(schema.CHECK_KINDS))
    assert not missing, (
        "shipped type bundles name check(s) %s that schema.CHECK_KINDS does not "
        "carry — a checks round would never see their flags" % missing)
    print("  ok  test_check_kinds_covers_every_shipped_bundle_check")


def test_doc_scope_kinds_is_a_closed_set():
    """`DOC_SCOPE_KINDS` is the scope registry — what a producer's flag is
    ABOUT — a different axis from `CHECK_KINDS` (does it gate a `checks`
    round). Unregistered, a flag's anchor renders in section 1's margin
    instead of the document slip."""
    assert isinstance(schema.DOC_SCOPE_KINDS, tuple), "a vocabulary is a tuple"
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    # Read the producers rather than restating their strings.
    hp_src = (scripts_dir / "headings_present.py").read_text(encoding="utf-8")
    cl_src = (scripts_dir / "checklist.py").read_text(encoding="utf-8")
    assert 'KIND = "headings-present"' in hp_src, "headings_present.py's KIND moved"
    assert '"kind": "checklist"' in cl_src, "checklist.py's emitted kind moved"
    assert set(schema.DOC_SCOPE_KINDS) == {"headings-present", "checklist"}, \
        "the registry must name exactly the two document-level producers"
    # The same mechanical mapping a bundle's `checks[]` uses.
    for kind in schema.DOC_SCOPE_KINDS:
        producer = scripts_dir / (kind.replace("-", "_") + ".py")
        assert producer.exists(), f"{kind} names no producer at {producer}"
    assert set(schema.DOC_SCOPE_KINDS) != set(schema.CHECK_KINDS), \
        "scope and gating are different axes; collapsing them makes checklist gate a round"
    assert "headings-present" in schema.DOC_SCOPE_KINDS, "it is doc-scope"
    assert "headings-present" in schema.CHECK_KINDS, "...and it gates a checks round"
    print("  ok  test_doc_scope_kinds_is_a_closed_set")


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


def test_round_is_presence_gated_and_absence_normalizes():
    """`round` is optional and stays optional — a present malformed value is a
    hard failure, but an absent one is NORMALIZED rather than rejected,
    because the browser's tab title and round arithmetic both break on
    `undefined`."""
    base = {"sections": [{"id": "s1", "title": "T", "content": "c"}]}

    # Absent is valid, and normalizes to a first round.
    schema.validate_review_input(dict(base))
    d = dict(base)
    assert schema.default_round(d) is d, "normalizes in place and returns the same dict"
    assert d["round"] == 1, "an unnumbered round is a first round"

    # A present round is never overwritten.
    d = dict(base, round=7)
    schema.default_round(d)
    assert d["round"] == 7

    # Present-but-malformed is a hard failure — including `True`, which is an
    # `int` subclass and would otherwise sail through and render as round 01.
    for bad in (None, "2", 0, -1, True, 1.5, [], {}):
        try:
            schema.validate_review_input(dict(base, round=bad))
        except ValueError as e:
            assert "round" in str(e), f"the error must name the field, got {e}"
        else:
            raise AssertionError(f"round={bad!r} must be refused")

    # The normalizer is pure of policy beyond the default and never raises on a
    # shape the validator owns.
    assert schema.default_round("not a dict") == "not a dict"
    print("  ok  test_round_is_presence_gated_and_absence_normalizes")


def main():
    test_section_key_normalizes()
    test_section_key_handles_none_and_empty()
    test_is_ledger_verdict()
    test_ledger_note_joins_comments()
    test_ledger_note_falls_back_to_note()
    test_verdict_to_ledger_entry()
    test_ledger_note_records_suggested_wording_verbatim()
    test_comment_types_carry_the_third_type()
    test_validate_verdicts_requires_replacement_on_a_suggestion()
    test_validate_review_input_accepts_valid()
    test_validate_review_input_rejects_bad()
    test_id_must_be_a_bare_token()
    test_validate_verdicts_accepts_valid()
    test_validate_verdicts_rejects_bad()
    test_schema_reaches_no_io()
    test_round_is_complete_needs_a_row_per_input_section()
    test_round_is_complete_rejects_an_empty_round()
    test_absent_pass_is_todays_behavior_exactly()
    test_structure_and_line_are_the_base_rule()
    test_checks_pass_holds_until_every_check_flag_is_answered()
    test_proof_holds_on_an_unresolved_suggested_edit()
    test_thread_statuses_and_a_declined_suggestion_still_holds_proof()
    test_no_pass_relaxes_the_all_approved_base()
    test_check_kinds_covers_every_shipped_bundle_check()
    test_doc_scope_kinds_is_a_closed_set()
    test_has_revision_history_is_anchored()
    test_round_is_presence_gated_and_absence_normalizes()
    print("OK (26 tests)")


if __name__ == "__main__":
    main()
