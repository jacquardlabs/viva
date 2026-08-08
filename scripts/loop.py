#!/usr/bin/env python3
"""viva's review-loop driver — the bookkeeping half of the launch → wait → act
→ rewrite loop, so SKILL.md can carry judgment work only.

Seven subcommands: `start`, `annotate`, `arm`, `wait`, `rearm`, `finish`,
`abandon`. Issues: #104, #102, #103, #125.

Three rules this file exists to keep:
  * The agent never types a round number. Every subcommand derives it from disk.
  * The agent never waits on a server that is already gone — or on a round that
    was parsed but never armed.
  * The producer seam stays open. `start --parse-only`, `start` with a standing
    preference, and `rearm --parse-only` all stop after parsing so the agent can
    run its LLM pass; `annotate` then merges the flags and `arm` ships the round.
    That order is load-bearing — the server reads the round file once, when it
    is armed.
"""
import argparse
import json
import shutil
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
# Shared by both skills and printed by this driver, so they sit at the plugin
# root rather than inside either one — `/viva-write` needing `producers.md` must
# not mean reaching into `/viva-review`'s directory.
REFERENCES = PLUGIN_ROOT / "references"

_POLL_TRIES = 100        # × _POLL_INTERVAL ≈ 10s, for a server coming up or going down
_POLL_INTERVAL = 0.1
_WAIT_INTERVAL = 0.3     # verdict poll — human review time, not computation
_HTTP_TIMEOUT = 10
# `start`'s pre-flight probe only asks "is anyone home", and the answer comes
# off loopback or not at all. Ten seconds of it would stall every start behind
# a `server.url` whose process is long gone.
_PREFLIGHT_TIMEOUT = 2


def die(msg: str, code: int = 1) -> None:
    sys.stderr.write(f"viva-loop: {msg}\n")
    raise SystemExit(code)


def warn(msg: str) -> None:
    sys.stderr.write(f"viva-loop: warning: {msg}\n")


def run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], **kw)


def run_or_die(cmd, what: str, recovery: str = "") -> None:
    """Every sibling call is checked. An unchecked one turns a failed ledger
    write into a clean exit code, which is the failure mode this driver exists
    to remove rather than relocate."""
    if run(cmd).returncode != 0:
        tail = f" {recovery}" if recovery else ""
        die(f"{what} failed: {' '.join(str(c) for c in cmd)}.{tail}")


# ── round derivation — the counter nobody holds ───────────────────────────────
def current_round(viva: Path) -> int:
    """Highest *parsed* round on disk — not necessarily the armed one; `wait`
    reconciles the two against the server. 0 when none."""
    rounds = [int(p.stem[len("review-input-r"):])
              for p in viva.glob("review-input-r*.json")
              if p.stem[len("review-input-r"):].isdigit()]
    return max(rounds, default=0)


def round_files(viva: Path, n: int) -> Tuple[Path, Path]:
    return viva / f"review-input-r{n}.json", viva / f"review-r{n}.json"


def load_json(p: Path) -> dict:
    with p.open() as fh:
        return json.load(fh)


# ── liveness — probed, not stat'ed ────────────────────────────────────────────
def server_url(viva: Path) -> Optional[str]:
    f = viva / "server.url"
    if not f.exists():
        return None
    return f.read_text().strip() or None


def _request(req: urllib.request.Request, what: str, recovery: str) -> bytes:
    """One error shape for every HTTP call. `HTTPError` is a `URLError`
    subclass, so the server's own `{"error": ...}` body — which carries the
    count the guard computed — reaches the agent instead of a traceback."""
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            body = json.loads(e.read() or b"{}")
            detail = body.get("error") or ""
        except (ValueError, OSError):
            pass
        die(f"{what}: server refused with {e.code}"
            + (f" — {detail}" if detail else "") + f". {recovery}")
    except (urllib.error.URLError, OSError) as e:
        die(f"{what}: could not reach the server ({e}). {recovery}")
    return b""  # unreachable; die() raises


def post(base: str, path: str, payload: dict, what: str, recovery: str = "") -> None:
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    _request(req, what, recovery)


def probe_input(base: str, timeout: float = _HTTP_TIMEOUT) -> Optional[dict]:
    """The payload the server at `base` is serving, or None if nothing is
    answering there. File existence proves neither liveness nor armed-ness:
    SIGKILL, SIGHUP, and an OOM kill all skip the `finally` that unlinks
    `server.url`.

    This is the liveness question — is anyone home — and it is deliberately
    separate from `probe_round` below, which asks what round they are serving.
    A live *qa* server answers `/input` with an interview payload that has no
    `round` key at all (the `/viva-write` seam, CLAUDE.md), so "no round" and
    "no server" are different answers and only this one may be read as dead."""
    try:
        with urllib.request.urlopen(base + "/input", timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    # A non-dict body is a server answering something that is not ours; it is
    # alive, but no caller may `.get()` it.
    return payload if isinstance(payload, dict) else {}


def probe_round(base: str) -> Optional[int]:
    """The round the server is actually serving, or None if it is not answering
    — or is answering with no round (a qa payload)."""
    payload = probe_input(base)
    return payload.get("round") if payload is not None else None


def standing_preferences(viva: Path) -> list:
    """`[]` means this reviewer has no standing preferences. A store that exists
    but will not read is a different fact and says so on stderr — otherwise the
    preference producer silently disengages for the whole session."""
    store = viva / "preferences.json"
    if not store.exists():
        return []
    proc = run(
        [sys.executable, SCRIPTS / "preferences.py", "list", "--store", store,
         "--status", "standing", "--format", "json"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        warn(f"could not read standing preferences from {store} "
             f"(preferences.py exited {proc.returncode}); the preference "
             f"producer will not engage this session")
        return []
    try:
        return json.loads(proc.stdout or "[]")
    except ValueError:
        warn(f"could not parse standing preferences from {store} "
             f"(preferences.py emitted non-JSON); the preference producer "
             f"will not engage this session")
        return []


def resolve_doc_type(name: str, fatal: bool = True) -> Optional[dict]:
    """Resolve a type name to its bundle. The name enters the system here.

    A subprocess, not an import — `doc_types.py` is a sibling and `schema.py`
    stays the one cross-import (CLAUDE.md). `--type` resolves before `start`
    clears any state, so an unknown one costs nothing: recording a name that
    resolves to nothing would strand the round's whole check set silently.

    `fatal=False` is the resume path, where the name comes from the prior round
    file rather than the command line. It warns instead: by then the scratch
    carry-forward pair is already on disk, and a repo that dropped a
    `.viva-types/` bundle between sessions must still be able to resume.
    """
    proc = run([sys.executable, SCRIPTS / "doc_types.py", name],
               capture_output=True, text=True)
    if proc.returncode != 0:
        why = (proc.stderr or "").strip() or f"type {name!r} did not resolve"
    else:
        try:
            return json.loads(proc.stdout)
        except ValueError:
            why = f"type {name!r} resolved to output that is not JSON"
    if fatal:
        die(why)
    warn(f"{why} — carried from the prior session, so this round's checks "
         f"cannot be named; pass --type to retype the session")
    return None


def _seam_stop(round_no: int, round_file: Path, why: str) -> int:
    print(f"viva-loop: round {round_no} parsed, NOT armed — {why}")
    print("viva-loop: run your producer, then `loop.py annotate --sidecar "
          "<path>` and `loop.py arm`.")
    # Named, not templated: a producer reading `--input` needs this path, and
    # computing `review-input-r{N}.json` is the counter this file exists to stop
    # the agent holding.
    print(f"viva-loop: round file → {round_file}")
    print(f"viva-loop: producer contract → {REFERENCES / 'producers.md'}")
    return 0


# ── subcommands ───────────────────────────────────────────────────────────────
def cmd_start(args) -> int:
    viva = Path(args.viva_dir)
    doc = Path(args.doc)
    if not doc.exists():
        die(f"doc not found: {doc}")

    bundle = resolve_doc_type(args.doc_type) if args.doc_type else None

    # Pre-flight guard. `cmd_start`'s own clear below deletes the round files
    # and `server.url`; without this check it would do that to a *live* session,
    # orphaning a running server with the reviewer's tab still attached. The
    # dependency is file-local — the clear is thirty lines down, not in prose.
    if (viva / "server.url").exists():
        # Two cases wearing one file, and they take opposite recoveries — so
        # ask the server rather than guessing from the stat. Live: the human
        # already has the tab, and telling them to delete the file that points
        # at it is how a running review (or the interview server `/viva-write`
        # leaves behind) gets orphaned. Not answering: the `finally` that
        # unlinks this never ran, and deleting it is exactly right.
        base = server_url(viva)
        if base and probe_input(base, timeout=_PREFLIGHT_TIMEOUT) is not None:
            die(f"a session is already open at {base} — that tab is the live "
                f"review. Finish it there, or `loop.py abandon`, before "
                f"starting another.")
        # `server_url` is None for an empty file, which is still a collision.
        where = f" ({base})" if base else ""
        die(f"{viva}/server.url exists but nothing is answering{where} — a "
            f"prior session was killed without cleaning up. Delete the file, "
            f"then re-run.")

    viva.mkdir(parents=True, exist_ok=True)

    # Resume branch: a doc that already carries a sign-off ledger, with the
    # prior session's finishing round still on disk. Protect that pair OUTSIDE
    # the clear glob before clearing, or carry-forward dies with it.
    prior_in = prior_out = None
    prior_split_on = prior_doc_type = None
    if schema.has_revision_history(doc.read_text()):
        n = current_round(viva)
        if n:
            src_in, src_out = round_files(viva, n)
            if src_in.exists() and src_out.exists():
                prior_in = viva / "prior-review-input.json"
                prior_out = viva / "prior-review-verdicts.json"
                prior_in.write_bytes(src_in.read_bytes())
                prior_out.write_bytes(src_out.read_bytes())
                # The prior round records the pattern it was split with. Reading
                # it back is what `rearm` already does between rounds; a resume
                # is the same question one session later, and re-deciding it by
                # auto-detection changes every section's identity and silently
                # carries forward nothing.
                prior_round = load_json(prior_in)
                prior_split_on = prior_round.get("split_on")
                # The type is round state on the same terms: a resume that
                # re-decided it would silently drop the prior session's check
                # set, so it is read back here and overridden only by an
                # explicit `--type`.
                prior_doc_type = prior_round.get("doc_type")
                # The `pass` is deliberately NOT read back. Split pattern and
                # type are session identity — re-deciding either changes section
                # identity or drops the check set. Depth is a per-round decision,
                # and a resumed round 1 inheriting the prior session's finishing
                # `final` pass would add a conjunct nobody asked for. Name it
                # again with `--pass` if the new session wants it.

    for p in list(viva.glob("review-input-r*.json")) + list(viva.glob("review-r*.json")):
        p.unlink()
    for name in ("server.url", "open-notes.json"):
        (viva / name).unlink(missing_ok=True)
    # Everything under .viva/ except preferences.json is disposable and reset
    # each session (CLAUDE.md). Attachment filenames are deterministic, so a
    # surviving directory silently re-points a prior ledger's citations at a
    # later session's images.
    shutil.rmtree(viva / "attachments", ignore_errors=True)

    split_on = args.split_on if args.split_on is not None else prior_split_on
    doc_type = args.doc_type if args.doc_type is not None else prior_doc_type
    if bundle is None and doc_type is not None:
        # A resume carries the type without an explicit `--type`, so resolve it
        # here too — otherwise a resumed typed session names no check set and
        # the producers nobody is told about never run. Non-fatal: the scratch
        # pair above is already on disk, and dying here would strand it.
        bundle = resolve_doc_type(doc_type, fatal=False)
    cmd = [sys.executable, SCRIPTS / "parse_sections.py", doc,
           "--output", viva / "review-input-r1.json", "--round", "1",
           "--doc-file", args.doc]
    if split_on is not None:
        cmd += ["--split-on", split_on]
    if doc_type is not None:
        cmd += ["--doc-type", doc_type]
    if args.pass_kind is not None:
        cmd += ["--pass", args.pass_kind]
    if args.posture is not None:
        # Handed over even without `--pass`, so `parse_sections.py` — the
        # boundary — refuses a posture on no pass instead of this driver
        # silently dropping it.
        cmd += ["--posture", args.posture]
    if prior_in and prior_out:
        cmd += ["--prior-input", prior_in, "--prior-verdicts", prior_out]
    try:
        if run(cmd).returncode != 0:
            die("parse failed")
    finally:
        # One resume, nothing persists past it — including down the failure
        # path, or the next `start` reads a two-sessions-old pair.
        if prior_in:
            prior_in.unlink(missing_ok=True)
            prior_out.unlink(missing_ok=True)

    if bundle:
        # The type's check set, named once where it is resolved — the agent
        # runs the producers, and a check nobody is told about never runs.
        checks = ", ".join(bundle.get("checks") or []) or "none"
        print(f"viva-loop: doc type {bundle['name']} · checks: {checks}")

    round_file = viva / "review-input-r1.json"
    if args.parse_only:
        return _seam_stop(1, round_file, "--parse-only")
    # The producer seam. A standing preference means the preference producer
    # auto-engages, and that producer is an LLM pass — the agent's judgment
    # work, not the driver's.
    prefs = standing_preferences(viva)
    if prefs and not args.arm_anyway:
        return _seam_stop(1, round_file,
                          f"{len(prefs)} standing preference(s) in play")
    return cmd_arm(args)


def cmd_annotate(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to annotate — run `loop.py start --doc <path>` first")
    inp, _ = round_files(viva, n)
    # Annotate is a PRE-ARM step. The server loads its round once and replaces
    # it only from `/next-round`, so annotating the round it is already serving
    # writes a file nobody re-reads: `loop.py finish` would pass its own gate off
    # disk, append the ledger, and then be refused by `/complete` reading the
    # stale copy. Loud here rather than silent there. Every real seam passes —
    # round 1 annotates before any server exists, and `rearm --parse-only` leaves
    # the server on round n-1 while this writes round n.
    base = server_url(viva)
    if base and probe_round(base) == n:
        die(f"round {n} is already armed — the server at {base} holds it in "
            f"memory and would never see this merge. Annotate before arming: "
            f"finish or `rearm --parse-only` this round, annotate the next one, "
            f"then `loop.py arm`.")
    # The producer seam's driver end: the agent names its sidecar, the driver
    # names the file. `annotate.py` reads '-' from stdin, and stdin is inherited
    # here, so a piped producer works unchanged.
    run_or_die([sys.executable, SCRIPTS / "annotate.py",
                "--input", inp, "--annotations", args.sidecar],
               "annotate",
               f"Fix the sidecar and re-run; {inp} is unchanged on failure.")
    print(f"viva-loop: round {n} annotated · {inp}")
    return 0


def cmd_arm(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to arm — run `loop.py start --doc <path>` first")
    inp, out = round_files(viva, n)

    # Branch on liveness, not on the round number. A round-1 `arm` re-run after
    # a slow start would otherwise launch a second server, see the *existing*
    # server.url on the first poll tick, and print a stale base while the new
    # process binds another port — the orphaned-server failure `start`'s guard
    # exists to prevent, reintroduced one subcommand over.
    base = server_url(viva)
    if base and probe_round(base) is not None:
        payload = load_json(inp)
        payload["output"] = str(out)
        post(base, "/next-round", payload, f"arming round {n}",
             "Fix the round file and re-run `loop.py arm`.")
        print(f"viva-loop: round {n} armed · {base}")
        return 0
    if base:
        die(f"{viva}/server.url names {base}, but nothing is answering there. "
            f"Delete the stale file, then `loop.py start --doc <path>`.")

    log = viva / "server.log"
    with log.open("wb") as errfh:
        proc = subprocess.Popen(
            [str(sys.executable), str(SERVER), "--mode", "review",
             "--input", str(inp), "--output", str(out)],
            stdout=subprocess.DEVNULL, stderr=errfh,
        )
    for _ in range(_POLL_TRIES):
        if (viva / "server.url").exists():
            break
        if proc.poll() is not None:
            # The headless contract documents a one-line startup error shape;
            # discarding it to DEVNULL made every launch failure look alike.
            tail = log.read_text().strip().splitlines()
            why = tail[-1] if tail else "no output"
            die(f"server exited during startup ({why}). Full log: {log}")
        time.sleep(_POLL_INTERVAL)
    base = server_url(viva)
    if not base:
        proc.kill()
        die(f"server start timed out — no server.url appeared. Log: {log}")
    print(f"viva-loop: round {n} armed · {base}")
    return 0


def cmd_wait(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no armed round to wait on")
    inp, out = round_files(viva, n)

    while not out.exists():
        base = server_url(viva)
        if not base:
            die(f"server is gone ({viva}/server.url disappeared) and round {n} "
                f"never returned verdicts. Relaunch with "
                f"`loop.py start --doc {load_json(inp).get('doc_file', '<doc>')}` "
                f"— carried approvals are preserved.", 2)
        served = probe_round(base)
        if served is None:
            die(f"server at {base} is not answering and round {n} never "
                f"returned verdicts. Delete {viva}/server.url, then relaunch "
                f"with `loop.py start --doc "
                f"{load_json(inp).get('doc_file', '<doc>')}`.", 2)
        if served != n:
            # Parsed but never armed — `rearm --parse-only` wrote round n while
            # the server still serves round `served`, and nothing will ever
            # write this round's verdicts.
            die(f"round {n} is parsed but not armed — the server is still "
                f"serving round {served}. Run `loop.py arm` (after "
                f"`loop.py annotate` if a producer is pending).", 2)
        time.sleep(_WAIT_INTERVAL)

    verdicts = load_json(out)
    input_data = load_json(inp)

    print(json.dumps(verdicts, indent=2))
    print("=== id -> title ===")
    for s in input_data.get("sections", []):
        print(f"{s.get('id')}\t{s.get('title')}")
    print("=== standing preferences ===")
    print(json.dumps(standing_preferences(viva)))

    # The classification line — the third destination #102(1) has no routing
    # rule for today. The agent branches on this token, never on its own scan.
    # `submitted_early` is tested first deliberately: a paused round is paused
    # even when everything submitted so far was approved.
    if verdicts.get("submitted_early"):
        klass = "submitted-early"
    elif schema.round_is_complete(input_data, verdicts):
        klass = "all-approved"
    else:
        klass = "has-work"
    print(f"=== round {n}: {klass} ===")
    if klass in ("has-work", "submitted-early"):
        # The next step is the rewrite, and the rule it turns on — act on each
        # thread's *latest* reviewer turn — is documented, not obvious. A paused
        # round can carry comments too, so it needs the same pointer.
        print(f"viva-loop: thread rules for the rewrite → "
              f"{REFERENCES / 'open-notes.md'}")
    return 0


def cmd_rearm(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to re-arm — run `loop.py start --doc <path>` first")
    inp, out = round_files(viva, n)
    if not out.exists():
        die(f"round {n} has no verdicts yet — run `loop.py wait` first")

    # The doc travels in the round file the parser wrote, so the agent names
    # neither the round nor the path it already handed `start`. The split
    # pattern travels the same way: `parse_sections.py` records it on every
    # round it parses, so re-reading it here is what keeps round N+1 splitting
    # the way round 1 did. Absent key → auto-detection, unchanged.
    round_data = load_json(inp)
    doc = round_data.get("doc_file")
    split_on = round_data.get("split_on")
    doc_type = round_data.get("doc_type")
    prior_pass = round_data.get("pass")
    if not doc:
        die(f"round {n}'s input names no doc_file — cannot re-parse")
    if not Path(doc).exists():
        die(f"doc not found: {doc} (recorded as doc_file in {inp}). Re-run from "
            f"the directory the review was started in.")

    # The pass carries within the session the way the pattern and the type do —
    # round N+1 runs at round N's depth unless this call names another. It is
    # the one of the three the agent is expected to change mid-session (round 1
    # structural, round 2 line, a later one checks), so `rearm` takes the
    # override the other two have no use for.
    if args.pass_kind is not None:
        next_pass = {"kind": args.pass_kind}
        if args.posture is not None:
            next_pass["posture"] = args.posture
    else:
        next_pass = dict(prior_pass) if isinstance(prior_pass, dict) else None
        if next_pass is not None and not next_pass.get("kind"):
            die(f"round {n}'s input carries a pass with no kind — fix {inp}, or "
                f"name this round's pass with --pass")
        if args.posture is not None:
            if next_pass is None:
                die("--posture needs a pass, and round %d runs none — name one "
                    "with --pass" % n)
            next_pass["posture"] = args.posture

    store = viva / "open-notes.json"
    cmd = [sys.executable, SCRIPTS / "open_notes.py", "update",
           "--store", store, "--round", str(n), "--verdicts", out, "--input", inp]
    for response in args.response:
        cmd += ["--response", response]
    # The author's other move. `open_notes.py` owns the rule a decline can break
    # — one per thread, because insisting wins — and refuses a second one, which
    # `run_or_die` turns into a stopped round rather than a silent overwrite.
    for decline in args.decline:
        cmd += ["--decline", decline]
    run_or_die(cmd, "open-note update",
               "No round was shipped; fix the --response/--decline cids and "
               "re-run.")

    nxt_in, _ = round_files(viva, n + 1)
    cmd = [sys.executable, SCRIPTS / "parse_sections.py", doc,
           "--output", nxt_in, "--round", str(n + 1), "--doc-file", doc,
           "--prior-input", inp, "--prior-verdicts", out,
           "--open-notes", store]
    # `is not None`, not truthiness, at both ends: the driver hands the pattern
    # back exactly as recorded and lets `parse_sections.py` own what it means,
    # so no round can be split by a rule a later round quietly re-decides.
    if split_on is not None:
        cmd += ["--split-on", split_on]
    if doc_type is not None:
        cmd += ["--doc-type", doc_type]
    if next_pass is not None:
        cmd += ["--pass", next_pass["kind"]]
        if next_pass.get("posture") is not None:
            cmd += ["--posture", next_pass["posture"]]
    run_or_die(cmd, "re-parse", f"The running server still holds round {n}.")

    # The round 2+ producer seam — the same stop-after-parse `start` takes on
    # its own when a standing preference is in play. Order is load-bearing: the
    # flags must be merged before the round is shipped to the running server.
    if args.parse_only:
        return _seam_stop(n + 1, nxt_in, "--parse-only")
    return cmd_arm(args)


def cmd_finish(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to finish")
    inp, out = round_files(viva, n)
    if not out.exists():
        die(f"round {n} has no verdicts yet — nothing to finish")

    input_data, verdicts = load_json(inp), load_json(out)
    if not schema.round_is_complete(input_data, verdicts):
        by_id = {s.get("id"): s for s in verdicts.get("sections", [])}
        pending = [s.get("title") for s in input_data.get("sections", [])
                   if (by_id.get(s.get("id")) or {}).get("verdict") != "approved"]
        # `round_is_complete` is the gate; this text is only its detail. A pass
        # ADDS a conjunct, so a round can be refused with everything approved —
        # printing "0 of N not approved" there would send the agent to re-present
        # a round the reviewer already signed.
        spec = input_data.get("pass")
        kind = spec.get("kind") if isinstance(spec, dict) else None
        if not input_data.get("sections"):
            # Tested before the pass: an empty round is refused by the base rule,
            # and naming a conjunct here would blame a `architecture`/`line` pass
            # that adds none.
            why = "the round carries no sections to approve"
        elif pending:
            why = (f"{len(pending)} of {len(input_data.get('sections', []))} "
                   f"section(s) not approved — "
                   f"{', '.join(repr(t) for t in pending[:5])}")
        elif kind:
            # The recovery is the round loop, not a mid-round annotate: the
            # server holds this round in memory, so a merge into the file it was
            # armed from is one `/complete` never sees.
            why = (f"every section is approved, but the {kind} pass is not "
                   f"satisfied — a checks round holds until every check "
                   f"flag carries a result, a final round until no suggested "
                   f"edit is unresolved. Answer the flags in the NEXT round: "
                   f"`loop.py rearm --parse-only`, `loop.py annotate --sidecar "
                   f"<path>` (see {REFERENCES / 'producers.md'}), `loop.py arm`")
        else:
            why = "the round carries no sections to approve"
        die(f"refusing to finish: {why}. Nothing is auto-accepted; "
            f"re-present the round or abandon it.")

    # The doc comes off the round file, the way `rearm` reads it — a
    # caller-supplied `--doc` is how this step fails from a different cwd, and
    # by then `/complete` has already torn the server down. `--doc` stays as an
    # override for a doc that legitimately moved.
    doc = args.doc or input_data.get("doc_file")
    if not doc:
        die(f"round {n}'s input names no doc_file — pass --doc <path>")
    if not Path(doc).exists():
        die(f"doc not found: {doc}. Re-run from the directory the review was "
            f"started in, or pass --doc <path>.")

    base = server_url(viva)
    if not base:
        die(f"no live server to complete (no {viva}/server.url). The verdicts "
            f"are on disk; append the ledger by hand with `python3 "
            f"{SCRIPTS / 'revision_history.py'} --viva-dir {viva} --doc {doc}`.")

    # Everything fallible runs BEFORE the irreversible POST. `/complete` starts
    # the server's shutdown timer, so a failure after it is unrecoverable in
    # place — a second `finish` dies at "no live server".
    run_or_die([sys.executable, SCRIPTS / "open_notes.py", "update",
                "--store", viva / "open-notes.json", "--round", str(n),
                "--verdicts", out, "--input", inp],
               "open-note update",
               "The session is still live; fix and re-run `loop.py finish`.")
    run_or_die([sys.executable, SCRIPTS / "revision_history.py",
                "--viva-dir", viva, "--doc", doc],
               "revision-history append",
               "The session is still live; fix and re-run `loop.py finish`.")

    revised = sum(1 for s in verdicts.get("sections", [])
                  if s.get("verdict") in schema.LEDGER_VERDICTS)
    post(base, "/complete",
         {"rounds_total": n,
          "sections_total": len(input_data.get("sections", [])),
          "sections_revised": revised},
         "completing the session",
         f"The ledger is already appended to {doc}; the server may need "
         f"`loop.py abandon`.")
    print(f"viva-loop: signed off — {n} round(s), "
          f"{len(input_data.get('sections', []))} section(s)")
    # Only a signed-off session learns, so this is the one place the record
    # step can be named — and the clustering it asks for is judgment work.
    print(f"viva-loop: record this session's recurring critiques → "
          f"{REFERENCES / 'preferences.md'}")
    return 0


def cmd_abandon(args) -> int:
    viva = Path(args.viva_dir)
    base = server_url(viva)
    if not base:
        die(f"no live session to abandon (no {viva}/server.url)")

    # Over HTTP, not by signal: `start` detaches the server, so this process
    # holds no child handle, and `server.url` carries a URL and nothing else.
    post(base, "/abandon", {}, "abandoning the session",
         f"If it is already stopped, delete {viva}/server.url to unblock the "
         f"next `loop.py start`.")

    for _ in range(_POLL_TRIES):
        if not (viva / "server.url").exists():
            break
        time.sleep(_POLL_INTERVAL)
    if (viva / "server.url").exists():
        die(f"server acknowledged /abandon but {viva}/server.url is still "
            f"there — the process may be wedged; stop it before the next start.")

    n = current_round(viva)
    where = f" at round {n}" if n else ""
    print(f"viva-loop: session abandoned{where} — the doc was NOT signed off.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--viva-dir", default=".viva",
                    help="state directory (default: .viva)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start", help="clear state, parse round 1, arm it")
    p.add_argument("--doc", required=True)
    p.add_argument("--split-on", metavar="REGEX",
                   help="split the doc on every heading whose title matches "
                        "this regex (re.search, any depth) instead of an "
                        "auto-detected level — a task-card plan. Recorded in "
                        "the round file, so later rounds and a later resume "
                        "re-split with it.")
    p.add_argument("--type", dest="doc_type", metavar="NAME",
                   help="doc type for this session — a name `doc_types.py` "
                        "resolves (`design-doc`, `plan`, …, or one the repo "
                        "committed under `.viva-types/`). Refused here if it "
                        "does not resolve; recorded in the round file, so later "
                        "rounds and a later resume carry it.")
    p.add_argument("--pass", dest="pass_kind", choices=schema.PASS_KINDS,
                   metavar="KIND",
                   help="depth this round runs at — %s. Recorded in the round "
                        "file and carried by `rearm` to round N+1; a later "
                        "resume does NOT inherit it. Omit for a round with no "
                        "pass, which behaves exactly as it does today."
                        % "|".join(schema.PASS_KINDS))
    p.add_argument("--posture", choices=schema.PASS_POSTURES, metavar="POSTURE",
                   help="posture setting on the pass — %s, where hard licenses "
                        "the author to argue rather than concede. Needs --pass."
                        % "|".join(schema.PASS_POSTURES))
    p.add_argument("--parse-only", action="store_true",
                   help="stop after parsing so a producer can annotate round 1 "
                        "before it is armed (the opt-in producer seam)")
    p.add_argument("--arm-anyway", action="store_true",
                   help="arm even when standing preferences would open the "
                        "producer seam. Not the rejected `finish --force`: this "
                        "declines an advisory producer, it never bypasses the "
                        "human gate.")
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
    p.add_argument("--decline", action="append", default=[],
                   metavar="CID=GROUNDS",
                   help='refuse one comment instead of complying, as '
                        '"<cid>=grounds" — a criterion, a prior ruling, a '
                        'measurement (repeatable). The thread goes `declined`, '
                        'which resolves nothing: it carries to the next round '
                        'and holds its section until the reviewer settles it or '
                        'insists. Insisting wins — there is no second decline '
                        'on the same thread.')
    p.add_argument("--pass", dest="pass_kind", choices=schema.PASS_KINDS,
                   metavar="KIND",
                   help="run round N+1 at this depth instead of the one round N "
                        "recorded — %s. Omit to carry the round's pass forward "
                        "unchanged." % "|".join(schema.PASS_KINDS))
    p.add_argument("--posture", choices=schema.PASS_POSTURES, metavar="POSTURE",
                   help="re-posture the pass (%s); alone, it re-postures the "
                        "carried kind." % "|".join(schema.PASS_POSTURES))
    p.add_argument("--parse-only", action="store_true",
                   help="stop after the re-parse so a producer can annotate it")
    p.set_defaults(func=cmd_rearm)

    p = sub.add_parser("finish", help="sign off — refuses an incomplete round")
    p.add_argument("--doc", default=None,
                   help="override the doc path recorded in the round file")
    p.set_defaults(func=cmd_finish)

    p = sub.add_parser("abandon", help="end an unfinished session — the one "
                                       "exit that is not a sign-off")
    p.set_defaults(func=cmd_abandon)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
