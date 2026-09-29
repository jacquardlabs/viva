#!/usr/bin/env python3
"""`server.HTML` is composed from `assets/app/` at import (#203).

Covers: the composed template is byte-identical to the inline constant it
replaced, every part is listed exactly once and exists, no part exceeds the
size cap, `server.py` carries no CSS or JS, and a server launched from a
foreign cwd serves the composed page.
"""
import ast
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from _server_harness import get_text, launch_server  # noqa: E402

# sha256 of the pre-split inline template at 082df19. A deliberate frontend
# change re-pins it; an accidental reorder or dropped byte fails here first.
TEMPLATE_SHA256 = "0983503a1de3e316a789bacfefdf0c1f259b50f8048438f85ade3a07261fccaf"
MAX_PART_LINES = 1200

REVIEW_INPUT = {
    "mode": "review",
    "doc_file": "SPEC.md",
    "round": 1,
    "approved_ids": [],
    "sections": [{"id": "s1", "title": "Overview", "content": "Body."}],
}


def _template() -> str:
    return "".join((server._APP_DIR / p).read_text(encoding="utf-8")
                   for p in server._APP_PARTS)


def test_composed_template_matches_the_pin() -> None:
    digest = hashlib.sha256(_template().encode("utf-8")).hexdigest()
    assert digest == TEMPLATE_SHA256, \
        f"composed template sha256 {digest} != pinned {TEMPLATE_SHA256}"
    print("  ok  test_composed_template_matches_the_pin")


def test_every_part_is_listed_once_and_exists() -> None:
    listed = list(server._APP_PARTS)
    assert len(listed) == len(set(listed)), "a part is listed twice"
    on_disk = sorted(str(p.relative_to(server._APP_DIR))
                     for p in server._APP_DIR.rglob("*") if p.is_file())
    assert sorted(listed) == on_disk, \
        f"parts and files disagree: listed-only {set(listed) - set(on_disk)}, " \
        f"unlisted {set(on_disk) - set(listed)}"
    print("  ok  test_every_part_is_listed_once_and_exists")


def test_no_part_exceeds_the_cap() -> None:
    lines = {p: (server._APP_DIR / p).read_text(encoding="utf-8").count("\n")
             for p in server._APP_PARTS}
    over = {p: n for p, n in lines.items() if n > MAX_PART_LINES}
    assert not over, f"parts over {MAX_PART_LINES} lines: {over}"
    print("  ok  test_no_part_exceeds_the_cap")


def test_server_py_carries_no_css_or_js() -> None:
    """No inline frontend constant survives: the longest string literal in
    server.py is far below one CSS or JS part."""
    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
    longest = max((len(n.value) for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)), default=0)
    assert longest < 5000, f"server.py carries a {longest}-char string literal"
    print("  ok  test_server_py_carries_no_css_or_js")


def test_served_from_a_foreign_cwd() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        viva = Path(tmp) / ".viva"
        viva.mkdir()
        (viva / "in1.json").write_text(json.dumps(REVIEW_INPUT), encoding="utf-8")
        with launch_server(viva / "in1.json", viva / "out1.json", cwd=tmp) as base:
            page = get_text(base, "/")
    assert page.startswith("<!DOCTYPE html>") and page.rstrip().endswith("</html>"), \
        "the served page is not the composed document"
    print("  ok  test_served_from_a_foreign_cwd")


def main() -> None:
    test_composed_template_matches_the_pin()
    test_every_part_is_listed_once_and_exists()
    test_no_part_exceeds_the_cap()
    test_server_py_carries_no_css_or_js()
    test_served_from_a_foreign_cwd()
    print("OK (5 tests)")


if __name__ == "__main__":
    main()
