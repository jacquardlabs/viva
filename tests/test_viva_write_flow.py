#!/usr/bin/env python3
"""The `/viva-write` intake seam, end to end (#170, #179).

`/viva-write` is a skill, not a script — but the sequence it prescribes crosses
four processes and one ordering constraint that is invisible in prose, so the
sequence itself is what this file pins. Everything below is the SKILL.md's steps
3–7 run for real, and every step is a driver call: `loop.py interview` (one
`server.py --mode qa`, answered by the human), a draft on disk, `loop.py start
--doc --type --pass --handoff --parse-only` (the parse AND the bundle's checks),
`loop.py annotate` for the judgment producer, `loop.py arm` (the `/next-round`
hand-off into the same process), then `loop.py wait`/`rearm`/`finish`.

The four properties that would break silently:

  1. **`loop.py annotate` must pass its already-armed guard against a LIVE qa
     server.** That guard compares `probe_round(base)` to the round on disk;
     a qa `/input` carries no `round` key, so it returns `None` and the round-1
     annotate goes through. If that ever stops holding, every typed
     `/viva-write` session loses its confidence flags with no error anywhere —
     so it is asserted from both sides here: it passes before the hand-off and
     is REFUSED after it.

  2. **`--pass <bundle.default_pass>` is load-bearing.** `default_pass` has no
     consumer but this flow (`loop.py start` resolves a bundle and only prints
     it), so a `/viva-write` that forgot to pass it would look identical on
     screen and run at no depth. The two tests below differ only in the
     bundle's `default_pass` and reach opposite outcomes on an identical doc
     with an identical unanswered check flag: `architecture` signs off,
     `checks` refuses.

  3. **The hand-off keeps one server and one `server.url`.** Same process, same
     base URL, from the interview through the ledger — `start --handoff` leaves
     the interview's `server.url` byte-identical, and `arm` gates on liveness
     rather than on a `round` key the interview payload does not carry.

  4. **The driver owns the clear.** `interview` removes every disposable file
     `start` does plus `answers.json`; `preferences.json` survives; and a
     hand-off `start` never touches `answers.json` — the draft was written from
     it minutes earlier.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _server_harness import get, poll_for, post, wait_for_url  # noqa: E402

SCRIPTS = ROOT / "scripts"
LOOP = SCRIPTS / "loop.py"
SKILL = ROOT / ".claude" / "skills" / "viva-write" / "SKILL.md"

# `interview` opens the human's tab; `$BROWSER` is registered preferred by
# `webbrowser`, so pointing it at a no-op keeps the test headless.
os.environ["BROWSER"] = "true"

QA_INPUT = {
    "mode": "qa",
    "context": "Notification design — residual decisions",
    "questions": [
        {"id": "q1", "text": "Which channel?", "choices": ["email", "sms"]},
        {"id": "q2", "text": "Where should the draft land?"},
    ],
}

ANSWERS = [
    {"id": "q1", "choice": "email", "note": ""},
    {"id": "q2", "choice": "", "note": "docs/notifications.md"},
]

# The design-doc grammar minus "Open questions" — one missing heading, so the
# `headings-present` check has exactly one flag to emit and both tests below
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

## Verification

An alert fires in staging and the email arrives.
"""

REPO_TYPE = {
    "name": "gated-note",
    "title": "Gated note",
    "sections": ["Problem & persona", "Proposed design", "User journey",
                 "Out of scope", "Alternatives considered", "Verification",
                 "Open questions"],
    "checks": ["headings-present"],
    "default_pass": "checks",
}

# The judgment producer the skill runs itself after `start` — one confidence
# flag, merged through `annotate` against the live interview server.
CONFIDENCE = [{"id": "s1", "kind": "confidence", "severity": "info",
               "message": "sourced from #170", "basis": "sourced"}]


# ── the SKILL.md's steps, as callables ───────────────────────────────────────
def _tmp_session():
    tmp = Path(tempfile.mkdtemp())
    viva = tmp / ".viva"
    viva.mkdir()
    (viva / "qa-input.json").write_text(json.dumps(QA_INPUT))
    return tmp, viva


def _loop(tmp: Path, *args, stdin=None):
    return subprocess.run(
        [sys.executable, str(LOOP)] + [str(a) for a in args],
        capture_output=True, text=True, input=stdin, cwd=str(tmp))


def _interview(tmp: Path, viva: Path):
    """Step 3 — the driver clears, launches `--mode qa`, and blocks on the
    human. Returns the blocked driver and the server it opened."""
    proc = subprocess.Popen(
        [sys.executable, str(LOOP), "interview", "--input", ".viva/qa-input.json"],
        cwd=str(tmp), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc, wait_for_url(viva / "answers.json")


def _answer(proc, base: str, answers) -> str:
    """The human submits; the driver wakes, prints the answers, and exits."""
    post(base, "/submit", {"answers": answers, "submitted_early": False})
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 0, err
    return out


def _reap(tmp: Path, viva: Path) -> None:
    """The driver detaches the server, so no test holds a child handle: the
    only reaper is the driver's own exit route. After `finish` it dies at "no
    live session", which is the point."""
    _loop(tmp, "abandon")
    for _ in range(50):
        if not (viva / "server.url").exists():
            return
        time.sleep(0.2)


def _approve_all(base: str, round_no: int, ids) -> None:
    post(base, "/submit", {"round": round_no, "submitted_early": False,
                           "sections": [{"id": i, "verdict": "approved"} for i in ids]})


def _flags(served: dict, kind: str) -> list:
    return [a for s in served["sections"] for a in s.get("annotations", [])
            if a.get("kind") == kind]


# ── test 1: the architecture default — an advisory flag does not hold ────────
def test_typed_draft_hands_off_and_signs_off():
    tmp, viva = _tmp_session()
    # (4) State the interview must clear, and the one file it must spare. The
    # store is written by `preferences.py` so `start` reads it as a real (empty
    # of standing preferences) store rather than warning on a stub.
    for stale in ("open-notes.json", "review-input-r7.json", "review-r7.json"):
        (viva / stale).write_text("{}")
    (viva / "answers.json").write_text('{"answers": [], "submitted_early": true}')
    (viva / "attachments").mkdir()
    (viva / "attachments" / "x.png").write_bytes(b"x")
    subprocess.run([sys.executable, str(SCRIPTS / "preferences.py"), "record",
                    "--store", str(viva / "preferences.json"), "--session", "s0",
                    "--id", "cite", "--label", "Cite", "--guidance", "Cite it."],
                   check=True, capture_output=True)
    prefs_before = (viva / "preferences.json").read_text()

    proc, base = _interview(tmp, viva)
    try:
        assert get(base, "/input")["mode"] == "qa"
        for gone in ("open-notes.json", "review-input-r7.json", "review-r7.json",
                     "answers.json", "attachments"):
            assert not (viva / gone).exists(), f"interview must clear {gone}"
        assert (viva / "preferences.json").read_text() == prefs_before, \
            "preferences.json is the one survivor of the clear"
        assert proc.poll() is None, "a stale answers.json must not end the interview"

        out = _answer(proc, base, ANSWERS)
        assert f"viva-loop: interview open · {base}" in out, out
        assert out.rstrip().splitlines()[-1] == "=== interview: answered ===", out
        answers_snapshot = (viva / "answers.json").read_text()
        assert json.loads(out[out.index("{"):out.rindex("}") + 1]) \
            == json.loads(answers_snapshot), "the driver prints the answers verbatim"

        # Step 4 — the draft lands at the path the interview named.
        (tmp / "docs").mkdir()
        doc = "docs/notifications.md"
        (tmp / doc).write_text(DRAFT)

        # Step 5 — parse at the bundle's own depth INTO the live interview: the
        # bundle's check runs inside `start`, the seam stays open for the
        # judgment producer, and the interview's server.url is untouched.
        url_before = (viva / "server.url").read_text()
        started = _loop(tmp, "start", "--doc", doc, "--type", "design-doc",
                        "--pass", "architecture", "--handoff", "--parse-only")
        assert started.returncode == 0, started.stderr
        assert "checks run: headings-present · 1 flag(s) merged" in started.stdout, \
            started.stdout
        assert "NOT armed" in started.stdout, started.stdout
        assert (viva / "server.url").read_text() == url_before, \
            "--handoff must keep the interview's server.url"
        assert (viva / "answers.json").read_text() == answers_snapshot, \
            "start must never clear the answers the draft was written from"
        assert get(base, "/input")["mode"] == "qa", "not armed yet"

        # (1) The guard: the qa server is LIVE and holds no round, so the
        # round-1 annotate goes through.
        merged = _loop(tmp, "annotate", "--sidecar", "-", stdin=json.dumps(CONFIDENCE))
        assert merged.returncode == 0, merged.stderr

        armed = _loop(tmp, "arm")
        assert armed.returncode == 0, armed.stderr
        assert f"round 1 armed · {base}" in armed.stdout, armed.stdout

        served = get(base, "/input")
        assert served["mode"] == "review" and served["round"] == 1, served
        assert served["doc_type"] == "design-doc", served
        assert served["pass"] == {"kind": "architecture"}, served
        checks = _flags(served, "headings-present")
        assert checks and all("result" not in a for a in checks), checks
        assert _flags(served, "confidence"), served["sections"][0]

        # (1, other side): now that the server holds round 1, the SAME annotate
        # is refused — the ordering SKILL.md states is enforced, not a norm.
        late = _loop(tmp, "annotate", "--sidecar", "-", stdin=json.dumps(CONFIDENCE))
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
        _reap(tmp, viva)

    # The server's own stdout goes to the log the driver keeps, and it is the
    # hand-off's only signal — never a wire field (references/qa.md).
    log = (viva / "server.log").read_text()
    assert "viva · qa mode ·" in log, log
    assert "viva · hand-off qa → review ·" in log, log
    print("  ok  test_typed_draft_hands_off_and_signs_off")


# ── test 2: the checks default — the same flag DOES hold ─────────────────────
def test_checks_default_pass_holds_the_round_until_the_flag_is_answered():
    tmp, viva = _tmp_session()
    (tmp / ".viva-types").mkdir()
    (tmp / ".viva-types" / "gated-note.json").write_text(json.dumps(REPO_TYPE))
    proc, base = _interview(tmp, viva)
    try:
        _answer(proc, base, [{"id": "q1", "choice": "email", "note": ""},
                             {"id": "q2", "choice": "", "note": "note.md"}])

        doc = "note.md"
        (tmp / doc).write_text(DRAFT)
        started = _loop(tmp, "start", "--doc", doc, "--type", "gated-note",
                        "--pass", REPO_TYPE["default_pass"], "--handoff",
                        "--parse-only")
        assert started.returncode == 0, started.stderr
        assert "1 flag(s) merged" in started.stdout, \
            "the repo bundle's grammar must flag a gap: " + started.stdout
        assert _loop(tmp, "arm").returncode == 0

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
        assert "**no** active comment anywhere" in SKILL.read_text(), \
            "the routing table must fork `has-work` on whether any comment is active"

        refused = _loop(tmp, "finish", "--doc", doc)
        assert refused.returncode != 0, refused.stdout
        assert "checks pass is not satisfied" in refused.stderr, refused.stderr
        assert "## Revision History" not in (tmp / doc).read_text()

        # The documented recovery: answer the flag in the round about to be
        # armed, never the one already armed. The flags come off the served
        # round; `annotate.py` matches a flag ignoring its `result`, so the
        # re-emitted twin lands on the one it answers.
        assert _loop(tmp, "rearm", "--parse-only").returncode == 0
        answered = [dict(a, id=s["id"], result="added 'Open questions'")
                    for s in served["sections"] for a in s.get("annotations", [])
                    if a.get("kind") == "headings-present"]
        assert answered
        assert _loop(tmp, "annotate", "--sidecar", "-",
                     stdin=json.dumps(answered)).returncode == 0
        assert _loop(tmp, "arm").returncode == 0

        served2 = get(base, "/input")
        assert served2["round"] == 2, served2
        answered_flags = _flags(served2, "headings-present")
        assert answered_flags and all(a.get("result") for a in answered_flags), \
            answered_flags
        _approve_all(base, 2, [s["id"] for s in served2["sections"]])
        assert poll_for(viva / "review-r2.json")
        assert "round 2: all-approved" in _loop(tmp, "wait").stdout

        signed = _loop(tmp, "finish", "--doc", doc)
        assert signed.returncode == 0, signed.stderr
        assert "## Revision History" in (tmp / doc).read_text()
    finally:
        _reap(tmp, viva)
    print("  ok  test_checks_default_pass_holds_the_round_until_the_flag_is_answered")


# ── the skill carries no bookkeeping of its own ──────────────────────────────
def test_skill_runs_the_interview_through_the_driver():
    """Step 3 used to carry the clear, the launch, and a liveness-free
    `until [ -f answers.json ]` poll; step 5 the parse, the check pipeline, and
    a curl. All of it is `loop.py`'s now. What stays in prose is the one thing
    a caller copying the qa contract would drop: never `/complete` the server
    this flow is about to hand a round to."""
    text = SKILL.read_text()
    interview = text[text.index("**3."):text.index("**4. Draft**")]
    assert 'loop.py" interview --input .viva/qa-input.json' in interview, \
        "step 3 must run the interview through the driver"
    assert "Never call `/complete` here" in interview, \
        "the interview step must forbid /complete on the server it hands off to"
    for bash in ("server.py", "until [", "rm -f", "curl"):
        assert bash not in interview, \
            f"step 3 carries bookkeeping bash ({bash!r}) the driver owns"
    handoff = text[text.index("**5."):text.index("**6.")]
    assert "--handoff" in handoff and "--parse-only" in handoff, handoff
    for bash in ("parse_sections.py", "headings_present.py", "curl", "review-input-r"):
        assert bash not in handoff, \
            f"step 5 carries bookkeeping bash ({bash!r}) the driver owns"
    print("  ok  test_skill_runs_the_interview_through_the_driver")


def main() -> None:
    test_typed_draft_hands_off_and_signs_off()
    test_checks_default_pass_holds_the_round_until_the_flag_is_answered()
    test_skill_runs_the_interview_through_the_driver()
    print("OK (3 tests)")


if __name__ == "__main__":
    main()
