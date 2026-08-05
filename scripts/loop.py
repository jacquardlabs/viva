#!/usr/bin/env python3
"""viva's review-loop driver — the bookkeeping half of the launch → wait → act
→ rewrite loop, so SKILL.md can carry judgment work only.

Seven subcommands: `start`, `annotate`, `arm`, `wait`, `rearm`, `finish`,
`abandon`. Design: docs/design/loop-driver.md. Issues: #104, #102, #103, #125.

Three rules this file exists to keep:
  * The agent never types a round number. Every subcommand derives it from disk.
  * The agent never waits on a server that is already gone.
  * The producer seam stays open. `start` (with a standing preference) and
    `rearm --parse-only` stop after parsing so the agent can run its LLM pass;
    `annotate` then merges the flags and `arm` ships the round. That order is
    load-bearing — the server reads the round file once, when it is armed.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402  — the one permitted sibling import (CLAUDE.md)

# `loop.py` lives in <plugin-root>/scripts/, so the plugin root is its parent's
# parent. Everything else — sibling scripts, server.py, the reference files the
# slim SKILL.md points at — is resolved from here rather than from a caller's
# $VIVA_DIR, which is how the agent stops needing one.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN_ROOT / "scripts"
SERVER = PLUGIN_ROOT / "server.py"
REFERENCES = PLUGIN_ROOT / ".claude" / "skills" / "viva" / "references"


def die(msg: str, code: int = 1) -> None:
    sys.stderr.write("viva-loop: " + msg + "\n")
    raise SystemExit(code)


def run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], **kw)


# ── round derivation — the counter nobody holds ───────────────────────────────
def current_round(viva: Path) -> int:
    """Highest armed round on disk. 0 when none — never an argument, never
    remembered across a call."""
    rounds = []
    for p in viva.glob("review-input-r*.json"):
        stem = p.stem[len("review-input-r"):]
        if stem.isdigit():
            rounds.append(int(stem))
    return max(rounds) if rounds else 0


def round_files(viva: Path, n: int) -> Tuple[Path, Path]:
    return viva / ("review-input-r%d.json" % n), viva / ("review-r%d.json" % n)


def load_json(p: Path) -> dict:
    with p.open() as fh:
        return json.load(fh)


# ── liveness — the signal that already exists ─────────────────────────────────
def server_url(viva: Path) -> Optional[str]:
    f = viva / "server.url"
    if not f.exists():
        return None
    return f.read_text().strip() or None


def post(base: str, path: str, payload: dict) -> None:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        base + path, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def standing_preferences(viva: Path) -> list:
    store = viva / "preferences.json"
    if not store.exists():
        return []
    proc = run(
        [sys.executable, SCRIPTS / "preferences.py", "list", "--store", store,
         "--status", "standing", "--format", "json"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return []
    try:
        return json.loads(proc.stdout or "[]")
    except ValueError:
        return []


# ── subcommands ───────────────────────────────────────────────────────────────
def cmd_start(args) -> int:
    viva = Path(args.viva_dir)
    doc = Path(args.doc)
    if not doc.exists():
        die("doc not found: %s" % doc)

    # Pre-flight guard. SKILL.md:47-48 makes the clear-state block's safety
    # depend on this check having run — without it, `start` deletes a live
    # session's server.url and orphans a running server with a tab still open.
    if (viva / "server.url").exists():
        die("a prior session may still be running (%s/server.url exists). "
            "Finish or abandon it, or delete the file if you are certain the "
            "server is stopped." % viva)

    viva.mkdir(parents=True, exist_ok=True)

    # Resume branch: a doc that already carries a sign-off ledger, with the
    # prior session's finishing round still on disk. Protect that pair OUTSIDE
    # the clear glob before clearing, or carry-forward dies with it.
    resuming = "## Revision History" in doc.read_text()
    prior_in = prior_out = None
    if resuming:
        n = current_round(viva)
        if n:
            src_in, src_out = round_files(viva, n)
            if src_in.exists() and src_out.exists():
                prior_in = viva / "prior-review-input.json"
                prior_out = viva / "prior-review-verdicts.json"
                prior_in.write_bytes(src_in.read_bytes())
                prior_out.write_bytes(src_out.read_bytes())

    for p in list(viva.glob("review-input-r*.json")) + list(viva.glob("review-r*.json")):
        p.unlink()
    for name in ("server.url", "open-notes.json"):
        (viva / name).unlink() if (viva / name).exists() else None

    cmd = [sys.executable, SCRIPTS / "parse_sections.py", doc,
           "--output", viva / "review-input-r1.json", "--round", "1",
           "--doc-file", args.doc]
    if prior_in and prior_out:
        cmd += ["--prior-input", prior_in, "--prior-verdicts", prior_out]
    if run(cmd).returncode != 0:
        die("parse failed")
    if prior_in:
        prior_in.unlink(missing_ok=True)
        prior_out.unlink(missing_ok=True)

    # The producer seam. A standing preference means the preference producer
    # auto-engages, and that producer is an LLM pass — the agent's judgment
    # work, not the driver's. Stop here and say where the contract is written.
    prefs = standing_preferences(viva)
    if prefs and not args.arm_anyway:
        print("viva-loop: round 1 parsed, NOT armed — %d standing preference(s) "
              "in play." % len(prefs))
        print("viva-loop: run the preference producer, then `loop.py annotate "
              "--sidecar <path>` and `loop.py arm`.")
        print("viva-loop: producer contract → %s" % (REFERENCES / "producers.md"))
        return 0
    return cmd_arm(args)


def cmd_annotate(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to annotate — run `loop.py start --doc <path>` first")
    inp, _ = round_files(viva, n)
    # The producer seam's driver end: the agent names its sidecar, the driver
    # names the file. `annotate.py` reads '-' from stdin, and stdin is inherited
    # here, so a piped producer works unchanged.
    if run([sys.executable, SCRIPTS / "annotate.py",
            "--input", inp, "--annotations", args.sidecar]).returncode != 0:
        die("annotate failed")
    print("viva-loop: round %d annotated · %s" % (n, inp))
    return 0


def cmd_arm(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to arm — run `loop.py start --doc <path>` first")
    inp, out = round_files(viva, n)

    if n == 1:
        proc = subprocess.Popen(
            [str(sys.executable), str(SERVER), "--mode", "review",
             "--input", str(inp), "--output", str(out)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            if (viva / "server.url").exists():
                break
            if proc.poll() is not None:
                die("server exited during startup")
            time.sleep(0.1)
        base = server_url(viva)
        if not base:
            die("server start failed — no server.url appeared")
        print("viva-loop: round %d armed · %s" % (n, base))
        return 0

    base = server_url(viva)
    if not base:
        die("no live server to re-arm (no %s/server.url)" % viva)
    payload = load_json(inp)
    payload["output"] = str(out)
    post(base, "/next-round", payload)
    print("viva-loop: round %d armed · %s" % (n, base))
    return 0


def cmd_wait(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no armed round to wait on")
    inp, out = round_files(viva, n)

    while not out.exists():
        # Liveness. The file the server already deletes on shutdown is the
        # signal; without this check the poll outlives the thing it polls.
        if not (viva / "server.url").exists():
            die("server is gone (%s/server.url disappeared) and round %d never "
                "returned verdicts. Relaunch with the same doc — carried "
                "approvals are preserved." % (viva, n), 2)
        time.sleep(0.3)

    verdicts = load_json(out)
    input_data = load_json(inp)

    print(json.dumps(verdicts, indent=2))
    print("=== id -> title ===")
    for s in input_data.get("sections", []):
        print("%s\t%s" % (s.get("id"), s.get("title")))
    print("=== standing preferences ===")
    print(json.dumps(standing_preferences(viva)))

    # The classification line — the third destination #102(1) has no routing
    # rule for today. The agent branches on this token, never on its own scan.
    if verdicts.get("submitted_early"):
        klass = "submitted-early"
    elif schema.round_is_complete(input_data, verdicts):
        klass = "all-approved"
    else:
        klass = "has-work"
    print("=== round %d: %s ===" % (n, klass))
    return 0


def cmd_rearm(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to re-arm — run `loop.py start --doc <path>` first")
    inp, out = round_files(viva, n)
    if not out.exists():
        die("round %d has no verdicts yet — run `loop.py wait` first" % n)

    # The doc travels in the round file the parser wrote, so the agent names
    # neither the round nor the path it already handed `start`.
    doc = load_json(inp).get("doc_file")
    if not doc:
        die("round %d's input names no doc_file — cannot re-parse" % n)
    if not Path(doc).exists():
        die("doc not found: %s (recorded as doc_file in %s). Re-run from the "
            "directory the review was started in." % (doc, inp))

    store = viva / "open-notes.json"
    cmd = [sys.executable, SCRIPTS / "open_notes.py", "update",
           "--store", store, "--round", str(n), "--verdicts", out, "--input", inp]
    for response in args.response:
        cmd += ["--response", response]
    if run(cmd).returncode != 0:
        die("open-note update failed")

    nxt_in, _ = round_files(viva, n + 1)
    if run([sys.executable, SCRIPTS / "parse_sections.py", doc,
            "--output", nxt_in, "--round", str(n + 1), "--doc-file", doc,
            "--prior-input", inp, "--prior-verdicts", out,
            "--open-notes", store]).returncode != 0:
        die("re-parse failed")

    # The round 2+ producer seam — the same stop-after-parse `start` takes on
    # its own when a standing preference is in play. Order is load-bearing: the
    # flags must be merged before the round is shipped to the running server.
    if args.parse_only:
        print("viva-loop: round %d parsed, NOT armed (--parse-only)." % (n + 1))
        print("viva-loop: run the producer, then `loop.py annotate --sidecar "
              "<path>` and `loop.py arm`.")
        print("viva-loop: producer contract → %s" % (REFERENCES / "producers.md"))
        return 0
    return cmd_arm(args)


def cmd_finish(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to finish")
    inp, out = round_files(viva, n)
    if not out.exists():
        die("round %d has no verdicts yet — nothing to finish" % n)

    input_data, verdicts = load_json(inp), load_json(out)
    if not schema.round_is_complete(input_data, verdicts):
        by_id = {s.get("id"): s for s in verdicts.get("sections", [])}
        pending = [s.get("title") for s in input_data.get("sections", [])
                   if (by_id.get(s.get("id")) or {}).get("verdict") != "approved"]
        die("refusing to finish: %d of %d section(s) not approved — %s. "
            "Nothing is auto-accepted; re-present the round or abandon it."
            % (len(pending), len(input_data.get("sections", [])),
               ", ".join(repr(t) for t in pending[:5])))

    base = server_url(viva)
    if not base:
        die("no live server to complete (no %s/server.url)" % viva)

    run([sys.executable, SCRIPTS / "open_notes.py", "update",
         "--store", viva / "open-notes.json", "--round", str(n),
         "--verdicts", out, "--input", inp])
    revised = sum(1 for s in verdicts.get("sections", [])
                  if s.get("verdict") in schema.LEDGER_VERDICTS)
    post(base, "/complete", {"rounds_total": n,
                             "sections_total": len(input_data.get("sections", [])),
                             "sections_revised": revised})
    run([sys.executable, SCRIPTS / "revision_history.py",
         "--viva-dir", viva, "--doc", args.doc])
    print("viva-loop: signed off — %d round(s), %d section(s)"
          % (n, len(input_data.get("sections", []))))
    return 0


def cmd_abandon(args) -> int:
    viva = Path(args.viva_dir)
    base = server_url(viva)
    if not base:
        die("no live session to abandon (no %s/server.url)" % viva)

    # Over HTTP, not by signal: `start` detaches the server, so this process
    # holds no child handle, and `server.url` carries a URL and nothing else.
    try:
        post(base, "/abandon", {})
    except (urllib.error.URLError, OSError) as e:
        die("could not reach the server at %s (%s). If it is already stopped, "
            "delete %s/server.url to unblock the next `loop.py start`."
            % (base, e, viva))

    for _ in range(100):
        if not (viva / "server.url").exists():
            break
        time.sleep(0.1)
    if (viva / "server.url").exists():
        die("server acknowledged /abandon but %s/server.url is still there — "
            "the process may be wedged; stop it before the next start." % viva)

    n = current_round(viva)
    where = " at round %d" % n if n else ""
    print("viva-loop: session abandoned%s — the doc was NOT signed off." % where)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--viva-dir", default=".viva",
                    help="state directory (default: .viva)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start", help="clear state, parse round 1, arm it")
    p.add_argument("--doc", required=True)
    p.add_argument("--arm-anyway", action="store_true",
                   help="arm even when standing preferences would open the "
                        "producer seam")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("annotate", help="merge a producer sidecar into the "
                                        "current round's review-input")
    p.add_argument("--sidecar", required=True,
                   help="producer sidecar JSON list, or '-' for stdin")
    p.set_defaults(func=cmd_annotate)

    p = sub.add_parser("arm", help="make the current round file live")
    p.set_defaults(func=cmd_arm)

    p = sub.add_parser("wait", help="block for verdicts; classify the round")
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("rearm", help="settle threads, re-parse, arm the next "
                                     "round (unless --parse-only)")
    p.add_argument("--response", action="append", default=[], metavar="CID=TEXT",
                   help='what you changed for one comment, as "<cid>=text" '
                        '(repeatable)')
    p.add_argument("--parse-only", action="store_true",
                   help="stop after the re-parse so a producer can annotate it")
    p.set_defaults(func=cmd_rearm)

    p = sub.add_parser("finish", help="sign off — refuses an incomplete round")
    p.add_argument("--doc", required=True)
    p.set_defaults(func=cmd_finish)

    p = sub.add_parser("abandon", help="end an unfinished session — the one "
                                       "exit that is not a sign-off")
    p.set_defaults(func=cmd_abandon)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
