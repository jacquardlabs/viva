#!/usr/bin/env python3
"""The MIT grant actually exists and agrees with the manifest (#194).

GitHub's license detector reads the root LICENSE file, not plugin.json's
`"license": "MIT"` field. Checks LICENSE exists, is canonical MIT, and its
copyright holder matches plugin.json's author.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENSE = ROOT / "LICENSE"
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
README = ROOT / "README.md"

# The two sentences and the disclaimer that separate canonical MIT from a
# summary of it. "contains the word MIT" passes for a paraphrase; these do not.
GRANT = (
    "Permission is hereby granted, free of charge, to any person obtaining a "
    "copy of this software and associated documentation files (the "
    '"Software"), to deal in the Software without restriction'
)
NOTICE = (
    "The above copyright notice and this permission notice shall be included "
    "in all copies or substantial portions of the Software."
)
DISCLAIMER = (
    'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS '
    "OR IMPLIED"
)


def _flowed(path: Path) -> str:
    """Line wrapping is not part of the licence text — compare on one line."""
    return " ".join(path.read_text(encoding="utf-8").split())


def test_license_file_exists_at_the_root():
    assert LICENSE.is_file(), (
        "no LICENSE at the repo root — GitHub's detector reads this file, not "
        "plugin.json, so without it the repo reports license: null"
    )
    print("  ok  test_license_file_exists_at_the_root")


def test_license_is_canonical_mit():
    text = LICENSE.read_text(encoding="utf-8")
    assert text.startswith("MIT License\n"), (
        "LICENSE must open with the bare `MIT License` header — a preamble "
        "line above it is a common way to fail licence detection"
    )
    flowed = _flowed(LICENSE)
    for clause in (GRANT, NOTICE, DISCLAIMER):
        assert clause in flowed, "LICENSE is missing canonical MIT text: " + clause
    print("  ok  test_license_is_canonical_mit")


def test_copyright_line_matches_the_manifest_author():
    author = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["author"]["name"]
    pattern = r"^Copyright \(c\) \d{4} " + re.escape(author) + r"$"
    lines = LICENSE.read_text(encoding="utf-8").splitlines()
    assert any(re.match(pattern, ln) for ln in lines), (
        "LICENSE carries no `Copyright (c) <year> {}` line — the holder must "
        "match plugin.json's author.name".format(author)
    )
    print("  ok  test_copyright_line_matches_the_manifest_author")


def test_manifest_declares_the_same_license():
    declared = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["license"]
    assert declared == "MIT", (
        "plugin.json declares {!r} but LICENSE grants MIT — the manifest and "
        "the grant must agree".format(declared)
    )
    print("  ok  test_manifest_declares_the_same_license")


def test_readme_carries_no_todo_about_the_license():
    """Scoped to LICENSE deliberately: other README sections carry their own
    TODOs, and this test must not fail on somebody else's."""
    for i, line in enumerate(README.read_text(encoding="utf-8").splitlines(), 1):
        if re.search(r"TODO", line) and re.search(r"LICENSE", line, re.IGNORECASE):
            raise AssertionError(
                "README.md:{} still flags the licence as missing: {}".format(
                    i, line.strip()
                )
            )
    print("  ok  test_readme_carries_no_todo_about_the_license")


def main():
    test_license_file_exists_at_the_root()
    test_license_is_canonical_mit()
    test_copyright_line_matches_the_manifest_author()
    test_manifest_declares_the_same_license()
    test_readme_carries_no_todo_about_the_license()
    print("OK (5 tests)")


if __name__ == "__main__":
    main()
