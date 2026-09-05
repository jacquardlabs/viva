#!/usr/bin/env python3
"""The register is reached at both steps that write prose, and stops where the
reviewer's own wording starts.

`references/style.md` fixed a draft that narrated the interview in doc text,
restated the brief as a preamble, and justified every bullet. Pins that the
draft step reads it and disowns the interview as a source, both rewrite steps
read it, and it never overrides a `suggestion`'s verbatim wording. Static —
`test_server_orchestration.check_references_are_reachable` covers the runtime
half (the printed path exists on disk).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLE = ROOT / "references" / "style.md"
LOOP = ROOT / "scripts" / "loop.py"
WRITE_SKILL = ROOT / ".claude" / "skills" / "viva-write" / "SKILL.md"
REVIEW_SKILL = ROOT / ".claude" / "skills" / "viva-review" / "SKILL.md"


def _between(text: str, start: str, end: str) -> str:
    """The prose of one step, whitespace-flattened — the sentence is the
    contract, not where a line wraps."""
    lo = text.index(start)
    hi = text.index(end, lo)
    return " ".join(text[lo:hi].split())


def test_draft_step_reads_the_register_and_disowns_the_interview():
    step = _between(WRITE_SKILL.read_text(encoding="utf-8"),
                    "**4. Draft**", "**5. Parse")
    assert "style.md" in step, \
        "the draft step no longer names references/style.md — the register is dead"
    low = step.lower()
    assert "interview is not a source" in low, (
        "the draft step must say the interview is not a source; without it "
        "the citation rule reads as 'cite the interview' and provenance lands "
        "in the doc text")
    assert "confidence sidecar" in low, \
        "the draft step must name where provenance goes instead of the prose"
    assert re.search(r"decision\**\s*sidecar", low), \
        "the draft step must also name the decision sidecar (#211) as a home " \
        "for interview-answer provenance"
    assert "trim pass" in low, "the draft step must run the trim pass before parse"
    print("  ok  test_draft_step_reads_the_register_and_disowns_the_interview")


def test_wait_hands_out_the_register_on_a_round_with_work():
    src = LOOP.read_text(encoding="utf-8")
    wait = src[src.index("def cmd_wait"):src.index("def cmd_rearm")]
    assert re.search(r"""REFERENCES / ['"]style\.md['"]""", wait), (
        "`loop.py wait` no longer prints the register's path — the rewrite "
        "step is told to read it from `wait`'s output and nothing else names "
        "an absolute path")
    # Beside the thread rules, under the same `has-work`/`submitted-early`
    # branch: an all-approved round has no rewrite to hand it to.
    branch = wait[wait.index('klass in ("has-work", "submitted-early")'):]
    assert "style.md" in branch, \
        "the register must print under the has-work branch, not unconditionally"
    print("  ok  test_wait_hands_out_the_register_on_a_round_with_work")


def test_both_rewrite_steps_read_the_register():
    review = _between(REVIEW_SKILL.read_text(encoding="utf-8"),
                      "**A4. Rewrite", "**A5. Finish")
    assert "style.md" in review, "viva-review's A4 no longer names the register"
    assert "wait" in review.lower(), \
        "A4 must say the path comes from `wait`'s output, not a fresh command"
    write = _between(WRITE_SKILL.read_text(encoding="utf-8"),
                     "**6. Editorial rounds**", "**7. Stamp**")
    assert "style.md" in write, "viva-write's step 6 no longer names the register"
    print("  ok  test_both_rewrite_steps_read_the_register")


def test_register_stops_at_the_reviewers_wording():
    """A suggestion is wording, not a brief (#166); a register that 'tidies' it
    hands back a diff the reviewer never asked for."""
    style = " ".join(STYLE.read_text(encoding="utf-8").lower().split())
    assert "`suggestion`" in style and "verbatim" in style, \
        "style.md must say a suggestion's wording is applied verbatim"
    assert "`changes` comment wins" in style, \
        "style.md must say a changes comment asking for more wins over density"
    review = _between(REVIEW_SKILL.read_text(encoding="utf-8"),
                      "**A4. Rewrite", "**A5. Finish").lower()
    assert "verbatim" in review, \
        "A4's register paragraph must restate that a suggestion is pasted verbatim"
    print("  ok  test_register_stops_at_the_reviewers_wording")


def test_register_names_the_three_patterns_the_draft_showed():
    """The regression: provenance in doc text, a restated preamble, and
    justified bullets. Each is a heading-level prohibition, not an aside."""
    style = STYLE.read_text(encoding="utf-8")
    for pattern in ("**Provenance.**", "**Preamble.**", "**Justified bullets.**"):
        assert pattern in style, f"style.md dropped the prohibition on {pattern}"
    # The agent-era rule: the reviewer sees one card and a retriever returns
    # one chunk, so a section that leans on "as above" is broken for both.
    assert "**A section stands alone.**" in style, \
        "style.md dropped the section-independence rule"
    assert "interview is not a source" in style.lower(), \
        "style.md must disown the interview as a source"
    print("  ok  test_register_names_the_three_patterns_the_draft_showed")


def main() -> None:
    test_draft_step_reads_the_register_and_disowns_the_interview()
    test_wait_hands_out_the_register_on_a_round_with_work()
    test_both_rewrite_steps_read_the_register()
    test_register_stops_at_the_reviewers_wording()
    test_register_names_the_three_patterns_the_draft_showed()
    print("OK (5 tests)")


if __name__ == "__main__":
    main()
