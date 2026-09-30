#!/usr/bin/env python3
"""Intake answers link to the spec sections they shaped (#244), #239's
test-strategy row 6: the intake gate lists, per answer, the sections named in
`### Decisions` — from `.viva/decisions.json` while the spec gate is live, from
the signed spec's block after, joined on a relaunch to the interview saved at
sign-off.
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import revision_history  # noqa: E402
import schema  # noqa: E402
import server  # noqa: E402
from _server_harness import (get, launch_server, poll_for, post,  # noqa: E402
                             post_result, shipped_source)
from _session_harness import (DRAFT, approve_all, git, loop, open_session,  # noqa: E402
                              read, record_path, repo, wait_gone)
from test_session_join import pr_patch, stub_gh  # noqa: E402

SID = "ef" * 16
STORE = {
    "problem": {"title": "Problem", "flags": [
        {"kind": "decision", "severity": "info", "message": "Scope? → the CLI only"},
        {"kind": "decision", "severity": "info", "message": "Scope? Extended → no"}]},
    "design": {"title": "Design", "flags": [
        {"kind": "decision", "severity": "info", "message": "Scope? → the CLI only"},
        {"kind": "decision", "severity": "info", "message": "Budget? → 3 days"}]},
}
LINKS = [{"message": "Scope? → the CLI only", "sections": ["Design", "Problem"]},
         {"message": "Budget? → 3 days", "sections": ["Design"]},
         {"message": "Scope? Extended → no", "sections": ["Problem"]}]
# #239's own signed block: the pre-#253 form, then a note the block must not swallow.
PRE_253 = """# Spec

## Revision History

Signed off via viva review — 1 round, 8 sections, 0 with comments. 2026-09-28

### Decisions

**Data and migrations**

- How does the diff launch find the session? → .viva/session.json

**Interfaces and contracts**

- How does the diff launch find the session? → .viva/session.json
- Does a standalone review ever show a timeline? → No

Signed off via viva review — 1 round, 8 sections, 0 with comments. 2026-09-28

**Superseded 2026-09-28** (append-only):

- "How does the diff launch find the session?" — superseded.
"""


def status_of(base: str, path: str) -> int:
    try:
        return urllib.request.urlopen(base + path, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def intake(base: str) -> dict:
    """`GET /intake`'s rows keyed by question: (answer, sections)."""
    return {r["question"]: (r["answer"], r["sections"]) for r in get(base, "/intake")["answers"]}


def test_the_block_reads_back_what_the_ledger_writes() -> None:
    assert schema.decision_links(STORE) == LINKS
    block = revision_history.build_decisions_block(LINKS)
    doc = ("# Doc\n\n### Decisions\n\n- In the body → never read — **Nope**\n\n"
           "---\n\n## Revision History\n\nSigned off. 2026-09-01\n\n### Decisions\n\n"
           "- Old → stale — **Problem**\n\nSigned off. 2026-09-02\n\n" + block
           + "\n\n### Open notes\n\n**Problem**\n\n- (whole section) — settled\n")
    assert schema.parse_decisions_block(doc) == LINKS, "the last block, bounded"
    assert schema.parse_decisions_block("# Doc\n\n### Decisions\n\n- A → b — **X**\n") == [], \
        "a block outside the ledger is prose, not a sign-off"
    assert schema.parse_decisions_block(PRE_253) == [
        {"message": "How does the diff launch find the session? → .viva/session.json",
         "sections": ["Data and migrations", "Interfaces and contracts"]},
        {"message": "Does a standalone review ever show a timeline? → No",
         "sections": ["Interfaces and contracts"]}]
    print("  ok  test_the_block_reads_back_what_the_ledger_writes")


def test_the_link_source_moves_when_the_spec_gate_closes() -> None:
    qa = {"mode": "qa", "context": "Intake", "questions": [
        {"id": "q1", "text": "Scope?"}, {"id": "q2", "text": "Scope? Extended"},
        {"id": "q3", "text": "Skipped?"}]}
    spec = {"mode": "review", "doc_file": "spec.md", "round": 1, "sections": [
        {"id": "s1", "title": "Problem", "content": "P."},
        {"id": "s2", "title": "Design", "content": "D."}]}
    with tempfile.TemporaryDirectory() as td:
        viva = Path(td) / ".viva"
        viva.mkdir()
        (viva / "in.json").write_text(json.dumps(qa))
        with launch_server(viva / "in.json", viva / "answers.json", mode="session",
                           cwd=viva, extra=("--session-id", SID)) as base:
            post(base, "/submit", {"submitted_early": False, "answers": [
                {"id": "q1", "choice": "the CLI only", "note": ""},
                {"id": "q2", "choice": "no", "note": "not yet"}]})
            assert poll_for(viva / "answers.json")
            (viva / "decisions.json").write_text(json.dumps(STORE))
            (viva / schema.SPEC_DECISIONS_FILE).write_text(json.dumps({"intake": [], "decisions": [
                {"message": "Scope? → the CLI only", "sections": ["Signed"]}]}))
            post(base, "/next-round", dict(spec, output=str(viva / "review-r1.json")))
            # Spec live: decisions.json, joined on the longest question prefix;
            # a decision naming no question is its own row.
            assert intake(base) == {
                "Scope?": ("the CLI only", ["Design", "Problem"]),
                "Scope? Extended": ("no — not yet", ["Problem"]),
                "Skipped?": ("", []),
                "Budget?": ("3 days", ["Design"])}
            approve_all(base, 1)
            assert poll_for(viva / "review-r1.json")
            assert post_result(base, "/complete", {})[0] == 200
            # Spec done: the signed block, whatever decisions.json still says.
            assert intake(base) == {"Scope?": ("the CLI only", ["Signed"]),
                                    "Scope? Extended": ("no — not yet", []),
                                    "Skipped?": ("", [])}
    print("  ok  test_the_link_source_moves_when_the_spec_gate_closes")


def test_only_a_session_serves_the_intake() -> None:
    inputs = {"review": {"mode": "review", "doc_file": "d.md", "round": 1, "sections": []},
              "qa": {"mode": "qa", "questions": [{"id": "q1", "text": "Scope?"}]},
              "diff": {"mode": "diff", "doc_file": "PR", "round": 1, "sections": []}}
    for mode, data in inputs.items():
        with tempfile.TemporaryDirectory() as td:
            viva = Path(td)
            (viva / "in.json").write_text(json.dumps(data))
            (viva / "decisions.json").write_text(json.dumps(STORE))
            with launch_server(viva / "in.json", viva / "out.json", mode=mode, cwd=viva) as base:
                assert status_of(base, "/intake") == 404, mode
    print("  ok  test_only_a_session_serves_the_intake")


def sign_spec(main: Path, question: str = "Scope?") -> str:
    """A session whose answer shaped both spec sections, signed off and
    committed as its recorded source; returns the waiting server's URL."""
    (main / ".viva" / "qa-input.json").write_text(json.dumps(
        {"mode": "qa", "questions": [{"id": "q1", "text": question}]}))
    base = open_session(main)
    (main / "spec.md").write_text(DRAFT)
    r = loop(main, "start", "--doc", "spec.md", "--handoff", "--parse-only")
    assert r.returncode == 0, r.stderr
    sections = json.loads((main / ".viva" / "review-input-r1.json").read_text())["sections"]
    sidecar = main / "decisions-sidecar.json"
    sidecar.write_text(json.dumps([{"kind": "decision", "severity": "info", "id": s["id"],
                                    "message": question + " → x"} for s in sections
                                   if s["title"] in ("Problem", "Design")]))
    for argv in (["annotate", "--sidecar", str(sidecar)], ["arm"]):
        r = loop(main, *argv)
        assert r.returncode == 0, r.stderr
    assert intake(base) == {question: ("x", ["Design", "Problem"])}, "spec live"
    approve_all(base, 1)
    assert poll_for(main / ".viva" / "review-r1.json")
    r = loop(main, "finish")
    assert r.returncode == 0, r.stderr
    signed = (main / "spec.md").read_text()
    assert f"- {question} → x — **Design**, **Problem**" in signed, signed
    # Row 6: the intake lists the sections the signed `### Decisions` names,
    # beside the interview's own question texts for a relaunch to join on.
    assert json.loads((main / ".viva" / schema.SPEC_DECISIONS_FILE).read_text()) == {
        "intake": [{"question": question, "answer": "x"}],
        "decisions": schema.parse_decisions_block(signed)}
    assert intake(base) == {question: ("x", ["Design", "Problem"])}, "spec done"
    assert read(main)["intake"] == [{"question": question, "answer": "x"}], \
        "the questions ride the record, which the clear never reaches"
    git(main, "add", "spec.md")
    git(main, "commit", "-q", "-m", "stamp")
    r = loop(main, "session", "--spec-source", "commit:spec.md@HEAD")
    assert r.returncode == 0, r.stderr
    return base


def test_the_intake_lists_the_sections_named_in_decisions() -> None:
    """Row 6 end to end: a live join rereads the block from the recorded source."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main = repo(td)
        env = stub_gh(td)
        base = sign_spec(main)
        pr_patch(main, td, "a\nB\nc\n")
        r = loop(main, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode == 0, r.stderr
        assert not (main / ".viva" / "decisions.json").exists(), "the spec's epoch is cleared"
        assert intake(base) == {"Scope?": ("x", ["Design", "Problem"])}, "diff live"
        r = loop(main, "abandon")
        assert r.returncode == 0, r.stderr
    print("  ok  test_the_intake_lists_the_sections_named_in_decisions")


def test_a_join_that_cannot_read_the_source_keeps_the_signed_links() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main = repo(td)
        env = stub_gh(td)
        base = sign_spec(main)
        record = read(main)
        record["spec"]["path"] = "gone.md"
        record_path(main).write_text(json.dumps(record))
        pr_patch(main, td, "a\nB\nc\n")
        r = loop(main, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode == 0, r.stderr
        assert "gone.md is not in" in r.stderr and "keeps the sections" in r.stderr, r.stderr
        assert intake(base) == {"Scope?": ("x", ["Design", "Problem"])}, "the finish-time copy"
        r = loop(main, "abandon")
        assert r.returncode == 0, r.stderr
    print("  ok  test_a_join_that_cannot_read_the_source_keeps_the_signed_links")


def relaunch(question: str, lose_source: bool) -> None:
    """Sign in `main`, stop its server, join from the other worktree: the
    relaunch holds no interview, only the spec finish's saved copy of it."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main, wt = repo(td), td / "wt"
        env = stub_gh(td)
        sign_spec(main, question)
        r = loop(main, "abandon", "--keep-session")
        assert r.returncode == 0, r.stderr
        wait_gone(main / ".viva")
        if lose_source:
            record = read(main)
            record["spec"]["path"] = "gone.md"
            record_path(main).write_text(json.dumps(record))
        pr_patch(main, td, "a\nB\nc\n")
        r = loop(wt, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode == 0, r.stderr
        assert ("keeps the sections" in r.stderr) == lose_source, r.stderr
        assert read(main)["viva_dir"] == str((wt / ".viva").resolve())
        base = (wt / ".viva" / "server.url").read_text().strip()
        assert intake(base) == {question: ("x", ["Design", "Problem"])}
        r = loop(wt, "abandon")
        assert r.returncode == 0, r.stderr


def test_a_relaunch_joins_on_the_questions_saved_at_sign_off() -> None:
    # #239's own spec asks a question with an arrow in it.
    relaunch("How does one server carry review → diff when /next-round refuses?", False)
    print("  ok  test_a_relaunch_joins_on_the_questions_saved_at_sign_off")


def test_a_relaunch_that_cannot_read_the_source_keeps_the_owners_links() -> None:
    relaunch("Scope?", True)
    print("  ok  test_a_relaunch_that_cannot_read_the_source_keeps_the_owners_links")


def test_a_clear_between_gates_keeps_the_questions() -> None:
    """The session parked with `abandon --keep-session`, then an unrelated
    review clears its `.viva/`: the join still has the question texts. No
    `?`, so the decision can't be split back into question and answer."""
    question = "Name the gate that carries review → diff"
    with tempfile.TemporaryDirectory() as td:
        td = Path(td).resolve()
        main = repo(td)
        env = stub_gh(td)
        sign_spec(main, question)
        r = loop(main, "abandon", "--keep-session")
        assert r.returncode == 0, r.stderr
        wait_gone(main / ".viva")
        (main / "other.md").write_text("# Other\n\n## A\n\nA.\n")
        r = loop(main, "start", "--doc", "other.md", "--parse-only")
        assert r.returncode == 0, r.stderr
        assert not (main / ".viva" / schema.SPEC_DECISIONS_FILE).exists(), "cleared"
        pr_patch(main, td, "a\nB\nc\n")
        r = loop(main, "start", "--target", "7", "--join-session", env=env)
        assert r.returncode == 0, r.stderr
        base = (main / ".viva" / "server.url").read_text().strip()
        assert intake(base) == {question: ("x", ["Design", "Problem"])}
        r = loop(main, "abandon")
        assert r.returncode == 0, r.stderr
    print("  ok  test_a_clear_between_gates_keeps_the_questions")


def test_arrows_in_a_question_or_answer_stay_where_they_are() -> None:
    q = "How does one server carry review → diff when /next-round refuses a mode change?"
    a = "A new --mode session that accepts qa → review → diff in that order"
    links = [{"message": f"{q} → {a}", "sections": ["Design"]},
             {"message": "Loose → one? → a → b", "sections": ["Problem"]}]
    assert server._intake_rows([{"question": q, "answer": a}], links) == [
        {"question": q, "answer": a, "sections": ["Design"]},
        {"question": "Loose → one?", "answer": "a → b", "sections": ["Problem"]}], \
        "a saved question joins by prefix; a loose one splits after its `?`"
    print("  ok  test_arrows_in_a_question_or_answer_stay_where_they_are")


def test_the_done_intake_gate_is_the_one_visitable_gate() -> None:
    """No JS runner (CLAUDE.md), so source needles on the shipped page."""
    page = shipped_source()
    assert ('<section class="intake-answers" id="intake-answers" aria-label="Intake answers" '
            'style="display:none"></section>') in page, "ships hidden"
    build = page[page.index("function buildSessionTimeline(d) {"):page.index("function toggleIntakeAnswers(")]
    assert "g.kind === 'intake' && g.state === 'done'" in build, "only the done intake is a control"
    assert build.index("if (!d || !d.session) return;") < build.index("el('intake-answers')")
    assert page.count("fetch('/intake')") == 1
    panel = page[page.index("function toggleIntakeAnswers("):page.index("function showWaitingForDiff(")]
    for writer in ("<textarea", "addComment", "sendSubmit", "pickQAChoice"):
        assert writer not in panel, "the intake renders read-only: %s" % writer
    print("  ok  test_the_done_intake_gate_is_the_one_visitable_gate")


def main() -> None:
    test_the_block_reads_back_what_the_ledger_writes()
    test_the_link_source_moves_when_the_spec_gate_closes()
    test_only_a_session_serves_the_intake()
    test_the_intake_lists_the_sections_named_in_decisions()
    test_a_join_that_cannot_read_the_source_keeps_the_signed_links()
    test_a_relaunch_joins_on_the_questions_saved_at_sign_off()
    test_a_relaunch_that_cannot_read_the_source_keeps_the_owners_links()
    test_a_clear_between_gates_keeps_the_questions()
    test_arrows_in_a_question_or_answer_stay_where_they_are()
    test_the_done_intake_gate_is_the_one_visitable_gate()
    print("OK")


if __name__ == "__main__":
    main()
