#!/usr/bin/env python3
"""Drift guard for docs/headless-contract.md (#111).

Not a schema-conformance suite — a cheap check that a handful of load-bearing
facts the doc states in prose stay true: `server.py`'s `--mode` choices match
the doc's Invocation table, the doc carries a parseable `Contract version:`
marker, and the `/submit` body-size cap the doc quotes matches the constant
`server.py` actually enforces. If any of these drift, the doc is wrong and
should be updated (and the Contract version bumped if the drift is a
breaking one — see CLAUDE.md's "boundary validator" principle applied to a
doc instead of a JSON store).
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "headless-contract.md"
SERVER = ROOT / "server.py"


def _doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_doc_exists():
    assert DOC.is_file(), f"{DOC} not found"
    print("  ok  test_doc_exists")


def test_contract_version_marker_present_and_parses():
    text = _doc_text()
    m = re.search(r"Contract version:\s*(\d+)", text)
    assert m, "docs/headless-contract.md is missing a 'Contract version: <int>' marker"
    int(m.group(1))  # must parse as an integer
    print("  ok  test_contract_version_marker_present_and_parses")


def test_mode_choices_match_argparse():
    # `python3 server.py --help` runs argparse's help path and exits 0 before
    # any port binding or input loading — safe to introspect via subprocess
    # without launching a real server.
    out = subprocess.run(
        [sys.executable, str(SERVER), "--help"],
        capture_output=True, text=True, timeout=10,
    ).stdout
    m = re.search(r"--mode \{([\w,]+)\}", out)
    assert m, f"could not find --mode choices in --help output: {out!r}"
    actual_choices = set(m.group(1).split(","))

    text = _doc_text()
    m = re.search(r"--mode \{([\w,]+)\}", text)
    assert m, "docs/headless-contract.md does not state --mode's choices as {a,b,c}"
    doc_choices = set(m.group(1).split(","))

    assert actual_choices == doc_choices, (
        f"server.py --mode choices {actual_choices} != "
        f"doc's stated choices {doc_choices}"
    )
    print("  ok  test_mode_choices_match_argparse")


def test_submit_size_cap_matches_doc():
    server_text = SERVER.read_text(encoding="utf-8")
    m = re.search(r"MAX_SUBMIT_BYTES\s*=\s*(\d+)\s*\*\s*(\d+)\s*\*\s*(\d+)", server_text)
    assert m, "could not find MAX_SUBMIT_BYTES in server.py"
    actual_mib = int(m.group(1))  # e.g. 256 * 1024 * 1024 -> 256 MiB

    doc_text = _doc_text()
    m = re.search(r"(\d+)\s*MiB", doc_text)
    assert m, "docs/headless-contract.md does not quote the /submit body-size cap in MiB"
    doc_mib = int(m.group(1))

    assert actual_mib == doc_mib, (
        f"server.py's MAX_SUBMIT_BYTES is {actual_mib} MiB but the doc says {doc_mib} MiB"
    )
    print("  ok  test_submit_size_cap_matches_doc")


def test_exit_code_2_is_argparse_usage_error():
    # A missing required flag is an argparse usage error — exit 2, per the
    # doc's error-semantics table.
    result = subprocess.run(
        [sys.executable, str(SERVER), "--mode", "review"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2, f"expected exit 2, got {result.returncode}"
    print("  ok  test_exit_code_2_is_argparse_usage_error")


def main():
    test_doc_exists()
    test_contract_version_marker_present_and_parses()
    test_mode_choices_match_argparse()
    test_submit_size_cap_matches_doc()
    test_exit_code_2_is_argparse_usage_error()
    print("OK (5 tests)")


if __name__ == "__main__":
    main()
