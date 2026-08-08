#!/usr/bin/env python3
"""The `/viva-write` intake seam, end to end (#170).

`/viva-write` is a skill, not a script — but the sequence it prescribes crosses
four processes and one ordering constraint that is invisible in prose, so the
sequence itself is what this file pins. Everything below is the SKILL.md's steps
3–7 run for real: one `server.py --mode qa`, an interview, a draft on disk,
`parse_sections.py --doc-type --pass`, the type's check producer, `loop.py
annotate`, the `/next-round` hand-off, then `loop.py wait`/`rearm`/`finish`.

The three properties that would break silently:

  1. **`loop.py annotate` must pass its already-armed guard against a LIVE qa
     server.** That guard compares `probe_round(base)` to the round on disk;
     a qa `/input` carries no `round` key, so it returns `None` and the round-1
     annotate goes through. If that ever stops holding, every typed
     `/viva-write` session loses its check flags with no error anywhere — so it
     is asserted from both sides here: it passes before the hand-off and is
     REFUSED after it.

  2. **`--pass <bundle.default_pass>` is load-bearing.** `default_pass` had no
     consumer before this flow (`loop.py start` resolves a bundle and only
     prints it), so a `/viva-write` that forgot to pass it would look identical
     on screen and run at no depth. The two tests below differ only in the
     bundle's `default_pass` and reach opposite outcomes on an identical doc
     with an identical unanswered check flag: `architecture` signs off,
     `checks` refuses.

  3. **The hand-off keeps one server and one `server.url`.** Same process, same
     base URL, from the interview through the ledger.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import SERVER, get, poll_for, post, wait_for_url  # noqa: E402

SCRIPTS = ROOT / "scripts"

QA_INPUT = {
    "mode": "qa",
    "context": "Notification design — residual decisions",
    "questions": [
        {"id": "q1", "text": "Which channel?", "choices": ["email", "sms"]},
        {"id": "q2", "text": "Where should the draft land?"},
    ],
}

# The design-doc grammar minus "Open questions" — one missing heading, so the
# `headings-present` producer has exactly one flag to emit and both tests below
# run against the same unanswered check.
DRAFT = """# Notification design

Drafted from #170 and the interview.

## Problem & persona

On-call engineers miss incidents (#170).

## Proposed design

Email, per the interview answer.

## User journey

Alert fires, engineer reads the email.

## Out of scope

SMS.

## Alternatives considered

SMS was considered and declined.
"""

REPO_TYPE = {
    "name": "gated-note",
    "title": "Gated note",
    "sections": ["Problem & persona", "Proposed design", "User journey",
                 "Out of scope", "Alternatives considered", "Open questions"],
    "checks": ["headings-present"],
    "default_pass": "checks",
}


# ── the SKILL.md's steps, as callables ───────────────────────────────────────
def _tmp_session():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "qa-input.json").write_text(json.dumps(QA_INPUT))
    return tmp, viva


def _launch_qa(tmp: Path, viva: Path):
    """Step 3 — one server, launched in qa mode, never `/complete`d."""
    proc = subprocess.Popen(
        [sys.executable, str(SERVER), "--mode", "qa",
         "--input", str(viva / "qa-input.json"),
         "--output", str(viva / "answers.json"), "--no-browser"],
        cwd=str(tmp), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    return proc, wait_for_url(viva / "answers.json")


def _script(name, *args, cwd=None, stdin=None):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name)] + [str(a) for a in args],
        capture_output=True, text=True, input=stdin,
        cwd=str(cwd) if cwd else None)


def _parse_round1(tmp: Path, doc: str, type_name: str, pass_kind: str, types_dir=None):
    """Step 5's parse — `--doc-type` AND `--pass <bundle.default_pass>`."""
    proc = _script("parse_sections.py", doc,
                   "--output", ".viva/review-input-r1.json", "--round", "1",
                   "--doc-file", doc, "--doc-type", type_name,
                   "--pass", pass_kind, cwd=tmp)
    assert proc.returncode == 0, proc.stderr
    return proc


def _run_check(tmp: Path, type_name: str, round_file: str) -> str:
    """Step 5's producer — the bundle piped into the check, as SKILL.md shows."""
    bundle = _script("doc_types.py", type_name, cwd=tmp)
    assert bundle.returncode == 0, bundle.stderr
    flags = _script("headings_present.py", "--input", round_file, "--bundle", "-",
                    cwd=tmp, stdin=bundle.stdout)
    assert flags.returncode == 0, flags.stderr
    return flags.stdout


def _loop(tmp: Path, *args, stdin=None):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "loop.py")] + [str(a) for a in args],
        capture_output=True, text=True, input=stdin, cwd=str(tmp))


def _handoff(base: str, viva: Path):
    """Step 5's hand-off — `output` in the BODY, the form `loop.py arm` uses."""
    payload = json.loads((viva / "review-input-r1.json").read_text())
    payload["output"] = ".viva/review-r1.json"
    return post(base, "/next-round", payload)


def _approve_all(base: str, round_no: int, ids) -> None:
    post(base, "/submit", {"round": round_no, "submitted_early": False,
                           "sections": [{"id": i, "verdict": "approved"} for i in ids]})


# ── test 1: the architecture default — an advisory flag does not hold ────────
def test_typed_draft_hands_off_and_signs_off():
    tmp, viva = _tmp_session()
    proc, base = _launch_qa(tmp, viva)
    try:
        assert get(base, "/input")["mode"] == "qa"
        post(base, "/submit", {"answers": [
            {"id": "q1", "choice": "email", "note": ""},
            {"id": "q2", "choice": "", "note": "docs/notifications.md"},
        ], "submitted_early": False})
        assert poll_for(viva / "answers.json"), "answers.json never written"
        answers_snapshot = (viva / "answers.json").read_text()

        # Step 4 — the draft lands at the path the interview named.
        (tmp / "docs").mkdir()
        doc = "docs/notifications.md"
        (tmp / doc).write_text(DRAFT)

        # Step 5 — parse at the bundle's own depth, then produce, then hand off.
        _parse_round1(tmp, doc, "design-doc", "architecture")
        sidecar = _run_check(tmp, "design-doc", ".viva/review-input-r1.json")
        assert "Open questions" in sidecar, sidecar

        # (1) The guard: the qa server is LIVE and holds no round, so the
        # round-1 annotate goes through.
        merged = _loop(tmp, "annotate", "--sidecar", "-", stdin=sidecar)
        assert merged.returncode == 0, merged.stderr

        assert _handoff(base, viva) == {"ok": True}

        served = get(base, "/input")
        assert served["mode"] == "review" and served["round"] == 1, served
        assert served["doc_type"] == "design-doc", served
        assert served["pass"] == {"kind": "architecture"}, served
        flags = served["sections"][0]["annotations"]
        assert any(a["kind"] == "headings-present" for a in flags), flags
        assert all("result" not in a for a in flags), flags

        # (1, other side): now that the server holds round 1, the SAME annotate
        # is refused — the ordering SKILL.md states is enforced, not a norm.
        late = _loop(tmp, "annotate", "--sidecar", "-", stdin=sidecar)
        assert late.returncode != 0, late.stdout
        assert "already armed" in late.stderr, late.stderr

        # Step 6 — loop.py drives from here.
        ids = [s["id"] for s in served["sections"]]
        _approve_all(base, 1, ids)
        assert poll_for(viva / "review-r1.json"), "review-r1.json never written"
        waited = _loop(tmp, "wait")
        assert waited.returncode == 0, waited.stderr
        assert "round 1: all-approved" in waited.stdout, waited.stdout

        # (3) One server, one url, start to finish.
        assert (viva / "server.url").read_text().strip() == base

        # Step 7 — the ledger, then the stamp (the stamp itself is the caller's).
        signed = _loop(tmp, "finish", "--doc", doc)
        assert signed.returncode == 0, signed.stderr
        assert "signed off" in signed.stdout, signed.stdout
        assert "## Revision History" in (tmp / doc).read_text()

        # The interview's answers are never clobbered by the review that follows.
        assert (viva / "answers.json").read_text() == answers_snapshot
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)

    assert "viva · qa mode ·" in out, out
    assert "viva · hand-off qa → review ·" in out, out
    print("  ok  test_typed_draft_hands_off_and_signs_off")


# ── test 2: the checks default — the same flag DOES hold ─────────────────────
def test_checks_default_pass_holds_the_round_until_the_flag_is_answered():
    tmp, viva = _tmp_session()
    (tmp / ".viva-types").mkdir()
    (tmp / ".viva-types" / "gated-note.json").write_text(json.dumps(REPO_TYPE))
    proc, base = _launch_qa(tmp, viva)
    try:
        post(base, "/submit", {"answers": [{"id": "q1", "choice": "email", "note": ""},
                                           {"id": "q2", "choice": "", "note": "note.md"}],
                               "submitted_early": False})
        assert poll_for(viva / "answers.json")

        doc = "note.md"
        (tmp / doc).write_text(DRAFT)
        _parse_round1(tmp, doc, "gated-note", REPO_TYPE["default_pass"])
        sidecar = _run_check(tmp, "gated-note", ".viva/review-input-r1.json")
        assert json.loads(sidecar), "the repo bundle's grammar must flag a gap"
        assert _loop(tmp, "annotate", "--sidecar", "-", stdin=sidecar).returncode == 0
        assert _handoff(base, viva) == {"ok": True}

        served = get(base, "/input")
        assert served["pass"] == {"kind": "checks"}, served
        ids = [s["id"] for s in served["sections"]]
        _approve_all(base, 1, ids)
        assert poll_for(viva / "review-r1.json")

        # (2) Every section approved — and the round still does not close,
        # because a `checks` pass ADDS the conjunct that every check flag carry
        # a result. This is the outcome an `architecture` bundle does not reach
        # on a byte-identical doc with a byte-identical flag.
        waited = _loop(tmp, "wait")
        assert "round 1: has-work" in waited.stdout, waited.stdout
        # `has-work` with zero active comments — the state a `checks` bundle
        # reaches by default. An agent routed to "rewrite" here hunts for
        # comments, finds none, and stalls, so the skill's table must fork on it.
        verdicts = json.loads((viva / "review-r1.json").read_text())
        assert not any(s.get("comments") for s in verdicts["sections"]), verdicts
        skill = (ROOT / ".claude" / "skills" / "viva-write" / "SKILL.md").read_text()
        assert "**no** active comment anywhere" in skill, \
            "the routing table must fork `has-work` on whether any comment is active"

        refused = _loop(tmp, "finish", "--doc", doc)
        assert refused.returncode != 0, refused.stdout
        assert "checks pass is not satisfied" in refused.stderr, refused.stderr
        assert "## Revision History" not in (tmp / doc).read_text()

        # The documented recovery: answer the flag in the round about to be
        # armed, never the one already armed.
        assert _loop(tmp, "rearm", "--parse-only").returncode == 0
        answered = [{**f, "result": "added 'Open questions'"}
                    for f in json.loads(sidecar)]
        assert _loop(tmp, "annotate", "--sidecar", "-",
                     stdin=json.dumps(answered)).returncode == 0
        assert _loop(tmp, "arm").returncode == 0

        served2 = get(base, "/input")
        assert served2["round"] == 2, served2
        answered_flags = [a for a in served2["sections"][0]["annotations"]
                          if a["kind"] == "headings-present"]
        assert answered_flags and all(a.get("result") for a in answered_flags), \
            answered_flags
        _approve_all(base, 2, [s["id"] for s in served2["sections"]])
        assert poll_for(viva / "review-r2.json")
        assert "round 2: all-approved" in _loop(tmp, "wait").stdout

        signed = _loop(tmp, "finish", "--doc", doc)
        assert signed.returncode == 0, signed.stderr
        assert "## Revision History" in (tmp / doc).read_text()
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=5)
    print("  ok  test_checks_default_pass_holds_the_round_until_the_flag_is_answered")


# ── the state clear the skill performs ───────────────────────────────────────
def test_skill_clears_open_notes_and_spares_preferences():
    """The clear in SKILL.md step 3 is `loop.py start`'s, not `/viva-diff`'s —
    which omits `open-notes.json`. A stale store would inject a prior session's
    threads into this session's round 2, so the file list is pinned here."""
    text = (ROOT / ".claude" / "skills" / "viva-write" / "SKILL.md").read_text()
    clear = text[text.index("mkdir -p .viva"):text.index("rm -rf .viva/attachments")]
    for name in ("review-input-r*.json", "review-r*.json", "open-notes.json"):
        assert name in clear, f"the state clear must remove {name}"
    assert "preferences.json" not in clear, \
        "preferences.json is the one survivor of the clear (CLAUDE.md)"
    # `/viva-qa`'s standalone step 4 ends with `/complete`, which would tear the
    # process down out from under the round this flow is about to hand it. The
    # prohibition is the one thing a caller copying that skill would drop.
    interview = text[text.index("until [ -f .viva/answers.json ]"):
                     text.index("**4. Draft**")]
    assert "Never call `/complete` here" in interview, \
        "the interview step must forbid /complete on the server it hands off to"
    assert "curl" not in interview, \
        "the interview step must issue no /complete call of its own"
    print("  ok  test_skill_clears_open_notes_and_spares_preferences")


def main() -> None:
    test_typed_draft_hands_off_and_signs_off()
    test_checks_default_pass_holds_the_round_until_the_flag_is_answered()
    test_skill_clears_open_notes_and_spares_preferences()
    print("OK (3 tests)")


if __name__ == "__main__":
    main()
