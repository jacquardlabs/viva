#!/usr/bin/env python3
"""preferences.py maintains the learned-preference store (issue #17).

It is the SINGLE writer of .viva/preferences.json — the store that lets viva
learn a recurring critique and pre-apply/pre-flag it. The agent does the
semantic clustering and cross-session matching; this script does the mechanical
bookkeeping: stable ids, distinct-session counting, candidate→standing
promotion, and listing. These tests cover that mechanical contract.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "preferences.py"
sys.path.insert(0, str(ROOT / "scripts"))
import preferences  # noqa: E402


def test_record_creates_candidate():
    store = preferences.record(
        preferences.empty_store(),
        session="2026-06-20 plan.md",
        label="Cite a source for every quantitative claim",
        guidance="Numbers need a citation.",
        today="2026-06-20",
    )
    assert list(store["preferences"]) == ["cite-a-source-for-every-quantitative-claim"]
    pref = next(iter(store["preferences"].values()))
    assert pref["status"] == "candidate"
    assert pref["sessions"] == ["2026-06-20 plan.md"]
    assert pref["observations"] == 1
    assert pref["created"] == pref["updated"] == "2026-06-20"


def test_reinforce_promotes_across_sessions():
    # A single-session critique is a candidate; a SECOND distinct session
    # matching the same id promotes it to standing. This is acceptance #1.
    store = preferences.record(preferences.empty_store(),
                               session="s1", pref_id="passive-voice",
                               label="Avoid passive voice", today="2026-06-20")
    assert store["preferences"]["passive-voice"]["status"] == "candidate"
    store = preferences.record(store, session="s2", pref_id="passive-voice",
                               count=3, today="2026-06-25")
    pref = store["preferences"]["passive-voice"]
    assert pref["status"] == "standing", pref
    assert pref["sessions"] == ["s1", "s2"]
    assert pref["observations"] == 4  # 1 + 3
    assert pref["updated"] == "2026-06-25"
    assert pref["label"] == "Avoid passive voice"  # preserved on reinforce


def test_same_session_does_not_double_count():
    # Recording the same session twice must not fork a phantom second session
    # (and so must not promote on a single real session).
    store = preferences.record(preferences.empty_store(),
                               session="s1", pref_id="rollback",
                               label="Include a rollback step")
    store = preferences.record(store, session="s1", pref_id="rollback")
    pref = store["preferences"]["rollback"]
    assert pref["sessions"] == ["s1"]
    assert pref["status"] == "candidate"
    assert pref["observations"] == 2  # observations still accumulate


def test_muted_is_not_auto_promoted():
    store = preferences.record(preferences.empty_store(),
                               session="s1", pref_id="x", label="X")
    store = preferences.set_status(store, "x", "muted")
    # A second session would normally promote — but mute holds.
    store = preferences.record(store, session="s2", pref_id="x")
    assert store["preferences"]["x"]["status"] == "muted"


def test_set_status_requires_known_id():
    store = preferences.record(preferences.empty_store(),
                               session="s1", pref_id="x", label="X")
    try:
        preferences.set_status(store, "nope", "muted")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_select_filters_and_sorts():
    store = preferences.empty_store()
    store = preferences.record(store, session="s1", pref_id="b", label="Beta")
    store = preferences.record(store, session="s1", pref_id="a", label="Alpha")
    store = preferences.record(store, session="s2", pref_id="a")  # → standing
    standing = preferences.select(store, "standing")
    assert [p["id"] for p in standing] == ["a"]
    candidates = preferences.select(store, "candidate")
    assert [p["id"] for p in candidates] == ["b"]
    everything = preferences.select(store, "all")
    assert [p["label"] for p in everything] == ["Alpha", "Beta"]  # label-sorted


def test_threshold_is_configurable():
    store = preferences.record(preferences.empty_store(), session="s1",
                               pref_id="x", label="X", threshold=1)
    assert store["preferences"]["x"]["status"] == "standing"


def test_zero_regression_missing_store():
    # Listing a never-created store is empty, not an error — feature dormant.
    tmp = Path(tempfile.mkdtemp())
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "list",
         "--store", str(tmp / ".viva" / "preferences.json"),
         "--format", "json"],
        capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == []


def test_cli_roundtrip():
    tmp = Path(tempfile.mkdtemp())
    store_path = tmp / ".viva" / "preferences.json"
    common = [sys.executable, str(SCRIPT), "record", "--store", str(store_path)]
    subprocess.run(common + ["--session", "2026-06-20 a.md",
                             "--id", "cite", "--label", "Cite sources"],
                   check=True)
    subprocess.run(common + ["--session", "2026-06-25 b.md", "--id", "cite"],
                   check=True)
    store = json.loads(store_path.read_text())
    assert store["preferences"]["cite"]["status"] == "standing"
    assert store["preferences"]["cite"]["sessions"] == [
        "2026-06-20 a.md", "2026-06-25 b.md"]

    # Agent consults standing-only as JSON.
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "list", "--store", str(store_path),
         "--status", "standing", "--format", "json"],
        capture_output=True, text=True, check=True)
    assert [p["id"] for p in json.loads(out.stdout)] == ["cite"]

    # Human mutes a bad learned pref; it drops out of the standing set.
    subprocess.run([sys.executable, str(SCRIPT), "set", "--store",
                    str(store_path), "--id", "cite", "--status", "muted"],
                   check=True)
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "list", "--store", str(store_path),
         "--status", "standing", "--format", "json"],
        capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == []


def main():
    test_record_creates_candidate()
    test_reinforce_promotes_across_sessions()
    test_same_session_does_not_double_count()
    test_muted_is_not_auto_promoted()
    test_set_status_requires_known_id()
    test_select_filters_and_sorts()
    test_threshold_is_configurable()
    test_zero_regression_missing_store()
    test_cli_roundtrip()
    print("OK (9 tests)")


if __name__ == "__main__":
    main()
