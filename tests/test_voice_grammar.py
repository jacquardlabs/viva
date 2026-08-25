#!/usr/bin/env python3
"""The spoken grammar's own invariants — `server._VOICE_VERBS` / `_VOICE_RULES`.

viva is named after the PhD oral: the candidate submits writing and the examiner
speaks. The grammar is the examiner's half, and it is a CLOSED vocabulary like
every other vocabulary in this system — so it gets the treatment `COMMENT_TYPES`
and `VERDICTS` get: one table, and a test that fails when a value is added at a
call site instead of in the table.

What is pinned here:

1. **Coverage.** Every `schema.COMMENT_TYPES` value has something a reviewer can
   say, and the `approved` verdict does too. Adding a comment type fails until
   it is speakable — which is the whole point of keeping the table beside the
   vocabulary it serves rather than inline in the JS.
2. **No ambiguity.** No phrase is claimed by two verbs. A phrase in two rows
   would resolve by table order, which is sorted by LENGTH, so which verb won
   would depend on nothing.
3. **Longest first.** `_VOICE_RULES` is sorted longest-phrase-first, because the
   browser takes the first match and stops. Out of order, "request changes the
   retry claim is wrong" reads as the verb `changes` carrying the word
   "request" — a comment attributed to the right type with the wrong first word,
   which is exactly the kind of silent corruption speech input must not have.
4. **Already normalized.** Every phrase is in the form `normalizeUtterance`
   produces (lowercase, no punctuation, single spaces). A phrase carrying a
   capital or a comma can never match anything, and would fail silently.
5. **Every act has a handler.** The acts in the table are the acts the router
   in the page switches on. A new verb with no branch is a word that does
   nothing and reports nothing.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import schema  # noqa: E402
import server  # noqa: E402

VERBS = server._VOICE_VERBS
RULES = server._VOICE_RULES


def _voice_block() -> str:
    """The page's voice layer, banner to banner.

    Sliced rather than searched whole-page: `act` names like `save` and `next`
    are common words elsewhere in a 7,000-line frontend, so a bare `in HTML`
    would pass on an unrelated match and prove nothing.
    """
    start = server.HTML.index("/* ═══ Voice — the oral examination")
    end = server.HTML.index("/* ═══ End voice")
    return server.HTML[start:end]


def test_every_comment_type_is_speakable():
    # A carrying verb per COMMENT_TYPES value — the reviewer's typed comment
    # kinds are exactly the ones speech can produce, no more and no fewer.
    spoken = {v["type"] for v in VERBS if v["act"] == "comment"}
    assert spoken == set(schema.COMMENT_TYPES), (
        "every COMMENT_TYPES value needs a spoken verb (and no verb may invent "
        "a type): table has %s, schema has %s" % (sorted(spoken), sorted(schema.COMMENT_TYPES)))
    for verb in VERBS:
        if verb["act"] == "comment":
            assert verb["carries"], (
                "%s carries the reviewer's words, so it must be a carrying verb "
                "— a bare one could only ever stage an empty comment" % verb["type"])
    print("  ok  test_every_comment_type_is_speakable")


def test_approve_is_a_bare_verb():
    # `approved` is the one VERDICTS value a reviewer produces directly; it
    # carries no text, which is exactly why it is safe to act on immediately.
    approve = [v for v in VERBS if v["act"] == "approve"]
    assert len(approve) == 1, "exactly one approve verb"
    assert not approve[0]["carries"], (
        "approve must be bare: a carrying approve would match 'approve of the "
        "third paragraph' and sign off a section nobody approved")
    assert "approved" in schema.VERDICTS
    print("  ok  test_approve_is_a_bare_verb")


def test_no_phrase_is_claimed_twice():
    seen = {}
    for rule in RULES:
        assert rule["phrase"] not in seen, (
            "phrase %r is claimed by both %s and %s — which one wins would "
            "depend on table order, which is sorted by length"
            % (rule["phrase"], seen.get(rule["phrase"]), rule["act"]))
        seen[rule["phrase"]] = rule["act"]
    assert len(RULES) == sum(len(v["phrases"]) for v in VERBS), \
        "the flattened table must hold one rule per phrase"
    print("  ok  test_no_phrase_is_claimed_twice")


def test_rules_are_sorted_longest_phrase_first():
    lengths = [len(r["phrase"]) for r in RULES]
    assert lengths == sorted(lengths, reverse=True), (
        "the browser takes the first match and stops — out of order, "
        "'request changes …' reads as the verb `changes` carrying 'request'")
    # The concrete case the ordering exists for, asserted rather than implied.
    order = [r["phrase"] for r in RULES]
    assert order.index("request changes") < order.index("changes")
    assert order.index("suggest wording") < order.index("suggest")
    assert order.index("stop listening") < order.index("stop")
    print("  ok  test_rules_are_sorted_longest_phrase_first")


def test_phrases_are_already_normalized():
    # `normalizeUtterance` lowercases, turns punctuation into spaces and
    # collapses runs. A phrase not already in that form matches nothing, ever,
    # and reports no error — the failure mode this test exists to make loud.
    for rule in RULES:
        phrase = rule["phrase"]
        assert re.fullmatch(r"[a-z0-9']+( [a-z0-9']+)*", phrase), (
            "phrase %r is not in normalized form, so no utterance can ever "
            "match it" % phrase)
    print("  ok  test_phrases_are_already_normalized")


def test_every_act_has_a_branch_in_the_router():
    block = _voice_block()
    for act in sorted({v["act"] for v in VERBS}):
        assert "'%s'" % act in block, (
            "act %r appears in the table but nowhere in the page's router — a "
            "word that does nothing and says nothing" % act)
    # The three that must keep working with the caret in a note field, named in
    # one place in the page and checked against the table here.
    for act in ("save", "cancel", "stop"):
        assert act in {v["act"] for v in VERBS}
    assert "const VOICE_ESCAPES = ['save', 'cancel', 'stop'];" in block, \
        "the dictation escapes must stay the three verbs that close a note"
    print("  ok  test_every_act_has_a_branch_in_the_router")


def main():
    test_every_comment_type_is_speakable()
    test_approve_is_a_bare_verb()
    test_no_phrase_is_claimed_twice()
    test_rules_are_sorted_longest_phrase_first()
    test_phrases_are_already_normalized()
    test_every_act_has_a_branch_in_the_router()
    print("OK")


if __name__ == "__main__":
    main()
