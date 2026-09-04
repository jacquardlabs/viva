#!/usr/bin/env python3
# tests/test_parse_diff.py
"""Unit tests for scripts/parse_diff.py"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = str(ROOT / "scripts" / "parse_diff.py")
PYTHON = sys.executable

SIMPLE_DIFF = """\
diff --git a/foo.py b/foo.py
index abc1234..def5678 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,4 @@
 line 1
-old line
+new line
+extra line
 line 3
"""

TWO_HUNK_DIFF = """\
diff --git a/bar.py b/bar.py
index abc1234..def5678 100644
--- a/bar.py
+++ b/bar.py
@@ -1,3 +1,3 @@
 context
-old
+new
 context
@@ -10,3 +10,3 @@
 context2
-removed
+added
 context2
"""

BINARY_DIFF = """\
diff --git a/img.png b/img.png
index abc1234..def5678 100644
Binary files a/img.png and b/img.png differ
"""

TWO_FILE_DIFF = SIMPLE_DIFF + """\
diff --git a/bar.py b/bar.py
index abc1234..def5678 100644
--- a/bar.py
+++ b/bar.py
@@ -5,3 +5,4 @@
 x = 1
-y = 2
+y = 3
+z = 4
 w = 5
"""


def _run(patch_content: str, extra_args: list[str] | None = None):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        patch = tmp / "diff.patch"
        out = tmp / "out.json"
        patch.write_text(patch_content, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(out), "--round", "1"]
            + (extra_args or []),
            capture_output=True,
            text=True,
        )
        data = json.loads(out.read_text()) if out.exists() else None
        return result.returncode, data, result.stderr


def test_single_hunk():
    rc, data, _ = _run(SIMPLE_DIFF)
    assert rc == 0
    assert data["mode"] == "diff"
    assert data["round"] == 1
    assert len(data["sections"]) == 1
    s = data["sections"][0]
    assert s["id"] == "s1"
    assert s["title"] == "foo.py hunk 1"
    assert s["content"].startswith("```diff")
    assert "@@ -1,3 +1,4 @@" in s["content"]
    assert "-old line" in s["content"]
    assert "+new line" in s["content"]
    assert s["content"].strip().endswith("```")
    print("test_single_hunk: OK")


def test_two_hunks_same_file():
    rc, data, _ = _run(TWO_HUNK_DIFF)
    assert rc == 0
    assert len(data["sections"]) == 2
    assert data["sections"][0]["title"] == "bar.py hunk 1"
    assert data["sections"][1]["title"] == "bar.py hunk 2"
    assert data["sections"][0]["id"] == "s1"
    assert data["sections"][1]["id"] == "s2"
    print("test_two_hunks_same_file: OK")


def test_two_files_ids_are_global():
    rc, data, _ = _run(TWO_FILE_DIFF)
    assert rc == 0
    assert len(data["sections"]) == 2
    assert data["sections"][0]["title"] == "foo.py hunk 1"
    assert data["sections"][1]["title"] == "bar.py hunk 1"
    # ids must be globally sequential, not reset per file
    assert data["sections"][0]["id"] == "s1"
    assert data["sections"][1]["id"] == "s2"
    print("test_two_files_ids_are_global: OK")


def test_binary_file_requires_explicit_approval():
    rc, data, _ = _run(BINARY_DIFF)
    assert rc == 0
    assert len(data["sections"]) == 1
    s = data["sections"][0]
    assert s["title"] == "img.png hunk 1"
    assert "Binary file changed" in s["content"]
    # binary sections are NOT in approved_ids — they require explicit approval
    assert s["id"] not in data["approved_ids"]
    print("test_binary_file_requires_explicit_approval: OK")


def test_empty_diff_exits_nonzero():
    rc, _, stderr = _run("")
    assert rc != 0
    assert "no hunks found" in stderr
    print("test_empty_diff_exits_nonzero: OK")


def test_doc_file_in_output():
    rc, data, _ = _run(SIMPLE_DIFF, ["--doc-file", "HEAD~1..HEAD"])
    assert rc == 0
    assert data["doc_file"] == "HEAD~1..HEAD"
    print("test_doc_file_in_output: OK")


def test_default_doc_file():
    rc, data, _ = _run(SIMPLE_DIFF)
    assert rc == 0
    assert data["doc_file"] == "working tree"
    print("test_default_doc_file: OK")


def test_carry_forward_identical_content():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        patch = tmp / "diff.patch"
        r1_input = tmp / "r1-input.json"
        r1_verdicts = tmp / "r1-verdicts.json"
        r2_input = tmp / "r2-input.json"

        patch.write_text(SIMPLE_DIFF, encoding="utf-8")

        subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r1_input), "--round", "1"],
            check=True, capture_output=True,
        )

        r1_verdicts.write_text(json.dumps({
            "round": 1,
            "sections": [{"id": "s1", "verdict": "approved"}],
        }))

        result = subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r2_input), "--round", "2",
             "--prior-input", str(r1_input), "--prior-verdicts", str(r1_verdicts)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        r2 = json.loads(r2_input.read_text())
        assert "s1" in r2["approved_ids"], f"s1 should carry forward; got {r2['approved_ids']}"
        print("test_carry_forward_identical_content: OK")


def test_carry_forward_changed_content_requires_review():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        patch = tmp / "diff.patch"
        r1_input = tmp / "r1-input.json"
        r1_verdicts = tmp / "r1-verdicts.json"
        r2_input = tmp / "r2-input.json"

        patch.write_text(SIMPLE_DIFF, encoding="utf-8")
        subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r1_input), "--round", "1"],
            check=True, capture_output=True,
        )
        r1_verdicts.write_text(json.dumps({
            "round": 1,
            "sections": [{"id": "s1", "verdict": "approved"}],
        }))

        # Simulate agent modifying the hunk (different content)
        modified = SIMPLE_DIFF.replace("+extra line", "+DIFFERENT extra line")
        patch.write_text(modified, encoding="utf-8")

        result = subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r2_input), "--round", "2",
             "--prior-input", str(r1_input), "--prior-verdicts", str(r1_verdicts)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        r2 = json.loads(r2_input.read_text())
        assert "s1" not in r2["approved_ids"], "changed hunk must not carry forward as approved"
        print("test_carry_forward_changed_content_requires_review: OK")


def test_carry_forward_round_3_preserves_round_1_approvals():
    """Section approved in round 1 must survive into round 3 without re-review."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        patch = tmp / "diff.patch"
        r1_input = tmp / "r1-input.json"
        r1_verdicts = tmp / "r1-verdicts.json"
        r2_input = tmp / "r2-input.json"
        r2_verdicts = tmp / "r2-verdicts.json"
        r3_input = tmp / "r3-input.json"

        patch.write_text(TWO_HUNK_DIFF, encoding="utf-8")

        # Round 1
        subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r1_input), "--round", "1"],
            check=True, capture_output=True,
        )
        r1_verdicts.write_text(json.dumps({
            "round": 1,
            "sections": [
                {"id": "s1", "verdict": "approved"},
                {"id": "s2", "verdict": "changes", "note": "fix this"},
            ],
        }))

        # Round 2: s1 carries forward
        subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r2_input), "--round", "2",
             "--prior-input", str(r1_input), "--prior-verdicts", str(r1_verdicts)],
            check=True, capture_output=True,
        )
        r2 = json.loads(r2_input.read_text())
        assert "s1" in r2["approved_ids"], f"s1 should carry into round 2; got {r2['approved_ids']}"

        # Round 2 verdicts: only s2 reviewed (s1 was collapsed, not re-reviewed)
        r2_verdicts.write_text(json.dumps({
            "round": 2,
            "sections": [{"id": "s2", "verdict": "approved"}],
        }))

        # Round 3: s1 must still carry even though it's absent from round-2 verdicts
        subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r3_input), "--round", "3",
             "--prior-input", str(r2_input), "--prior-verdicts", str(r2_verdicts)],
            check=True, capture_output=True,
        )
        r3 = json.loads(r3_input.read_text())
        assert "s1" in r3["approved_ids"], (
            f"s1 must survive into round 3 without re-review; got {r3['approved_ids']}"
        )
        assert "s2" in r3["approved_ids"], f"s2 approved in round 2 must carry to round 3; got {r3['approved_ids']}"
        print("test_carry_forward_round_3_preserves_round_1_approvals: OK")


def test_summary_carries_only_onto_an_unchanged_hunk():
    """The agent writes a hunk's summary between parsing and arming (#188);
    round 2 must keep it only on the unchanged hunk (s1), and drop it — not
    leave it stale — on the one that was edited (s2)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        patch = tmp / "diff.patch"
        r1_input = tmp / "r1-input.json"
        r1_verdicts = tmp / "r1-verdicts.json"
        r2_input = tmp / "r2-input.json"

        patch.write_text(TWO_HUNK_DIFF, encoding="utf-8")
        subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r1_input), "--round", "1"],
            check=True, capture_output=True,
        )

        # The agent's pass over round 1, exactly as SKILL.md B1a writes it.
        r1 = json.loads(r1_input.read_text())
        r1["sections"][0]["summary"] = "renames the first context guard"
        r1["sections"][1]["summary"] = "drops the removed branch"
        r1_input.write_text(json.dumps(r1))

        r1_verdicts.write_text(json.dumps({
            "round": 1,
            "sections": [
                {"id": "s1", "verdict": "approved"},
                {"id": "s2", "verdict": "changes", "comments": [{"note": "redo"}]},
            ],
        }))

        # The agent revises the second hunk; the first is untouched.
        patch.write_text(TWO_HUNK_DIFF.replace("+added", "+revised"), encoding="utf-8")

        result = subprocess.run(
            [PYTHON, SCRIPT, str(patch), "--output", str(r2_input), "--round", "2",
             "--prior-input", str(r1_input), "--prior-verdicts", str(r1_verdicts)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        r2 = {s["id"]: s for s in json.loads(r2_input.read_text())["sections"]}
        assert r2["s1"].get("summary") == "renames the first context guard", (
            f"an unchanged hunk must keep its summary; got {r2['s1'].get('summary')!r}"
        )
        assert "summary" not in r2["s2"], (
            "a changed hunk must arrive with NO summary — a stale one describes "
            f"the previous edit; got {r2['s2'].get('summary')!r}"
        )
        print("test_summary_carries_only_onto_an_unchanged_hunk: OK")


def test_no_summary_key_when_the_agent_wrote_none():
    """A round nobody summarized stays byte-identical to what it was before
    this field existed — no empty `summary` key on any section."""
    rc, data, _ = _run(TWO_FILE_DIFF)
    assert rc == 0
    for s in data["sections"]:
        assert "summary" not in s, f"{s['id']} gained an unasked-for summary key"
    print("test_no_summary_key_when_the_agent_wrote_none: OK")


# TWO_HUNK_DIFF with one line added to hunk 1. Hunk 2's body is untouched, but
# its header moves from `@@ -10,3 +10,3 @@` to `@@ -10,3 +11,3 @@` — the shift
# every edit above a hunk produces.
SHIFTED_DIFF = TWO_HUNK_DIFF.replace(
    "@@ -1,3 +1,3 @@\n context\n-old\n+new\n context\n",
    "@@ -1,3 +1,4 @@\n context\n-old\n+new\n+another\n context\n",
).replace("@@ -10,3 +10,3 @@", "@@ -10,3 +11,3 @@")
assert SHIFTED_DIFF != TWO_HUNK_DIFF and "+11,3" in SHIFTED_DIFF


def _round_pair(tmp: Path, r1_patch: str, r1_edit, r1_verdicts: dict, r2_patch: str):
    """Parse round 1, let `r1_edit` touch the round file, write verdicts, parse
    round 2 with both as priors. Returns round 2's data."""
    patch = tmp / "diff.patch"
    r1_input = tmp / "r1-input.json"
    r1_out = tmp / "r1-verdicts.json"
    r2_input = tmp / "r2-input.json"
    patch.write_text(r1_patch, encoding="utf-8")
    subprocess.run([PYTHON, SCRIPT, str(patch), "--output", str(r1_input), "--round", "1"],
                   check=True, capture_output=True)
    r1 = json.loads(r1_input.read_text())
    r1_edit(r1)
    r1_input.write_text(json.dumps(r1))
    r1_out.write_text(json.dumps(r1_verdicts))
    patch.write_text(r2_patch, encoding="utf-8")
    result = subprocess.run(
        [PYTHON, SCRIPT, str(patch), "--output", str(r2_input), "--round", "2",
         "--prior-input", str(r1_input), "--prior-verdicts", str(r1_out)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(r2_input.read_text())


def test_carry_forward_survives_a_line_shift_above():
    """#103 item 4b: a `@@` header shift from an earlier hunk growing must not
    drop an unrelated hunk's carried approval. s2's body is unchanged and must
    carry; s1's body changed and must not."""
    with tempfile.TemporaryDirectory() as tmp:
        r2 = _round_pair(
            Path(tmp), TWO_HUNK_DIFF, lambda r1: None,
            {"round": 1, "sections": [
                {"id": "s1", "verdict": "changes", "comments": [{"note": "add one"}]},
                {"id": "s2", "verdict": "approved"},
            ]},
            SHIFTED_DIFF)
        by_id = {s["id"]: s for s in r2["sections"]}
        assert "@@ -10,3 +11,3 @@" in by_id["s2"]["content"], \
            "precondition: the fixture must actually shift hunk 2's header"
        assert "s2" in r2["approved_ids"], (
            "an approved hunk whose only change is its @@ header must carry; "
            f"got {r2['approved_ids']}")
        assert "s1" not in r2["approved_ids"], \
            "the hunk that actually changed must still be re-reviewed"
        print("test_carry_forward_survives_a_line_shift_above: OK")


def test_summary_survives_a_line_shift_above():
    """Same shift, same identity: the summary carry keys on the hunk body too,
    so s2 keeps its one-liner and s1 (body changed) arrives without one."""
    def summarize(r1):
        r1["sections"][0]["summary"] = "renames the first context guard"
        r1["sections"][1]["summary"] = "drops the removed branch"
    with tempfile.TemporaryDirectory() as tmp:
        r2 = _round_pair(
            Path(tmp), TWO_HUNK_DIFF, summarize,
            {"round": 1, "sections": [
                {"id": "s1", "verdict": "changes", "comments": [{"note": "add one"}]},
                {"id": "s2", "verdict": "approved"},
            ]},
            SHIFTED_DIFF)
        by_id = {s["id"]: s for s in r2["sections"]}
        assert by_id["s2"].get("summary") == "drops the removed branch", \
            f"a header-only shift must keep the summary; got {by_id['s2'].get('summary')!r}"
        assert "summary" not in by_id["s1"], \
            "a hunk whose body changed must arrive with no summary"
        print("test_summary_survives_a_line_shift_above: OK")


def main() -> None:
    test_single_hunk()
    test_two_hunks_same_file()
    test_two_files_ids_are_global()
    test_binary_file_requires_explicit_approval()
    test_empty_diff_exits_nonzero()
    test_doc_file_in_output()
    test_default_doc_file()
    test_carry_forward_identical_content()
    test_carry_forward_changed_content_requires_review()
    test_carry_forward_round_3_preserves_round_1_approvals()
    test_summary_carries_only_onto_an_unchanged_hunk()
    test_no_summary_key_when_the_agent_wrote_none()
    test_carry_forward_survives_a_line_shift_above()
    test_summary_survives_a_line_shift_above()
    print("\nAll parse_diff tests passed.")


if __name__ == "__main__":
    main()
