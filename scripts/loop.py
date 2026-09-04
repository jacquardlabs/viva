#!/usr/bin/env python3
"""viva's review-loop driver — the bookkeeping half of the launch → wait → act
→ rewrite loop, so SKILL.md can carry judgment work only.

Nine subcommands: `interview`, `start`, `annotate`, `summarize`, `arm`, `wait`,
`rearm`, `finish`, `abandon`. Issues: #104, #102, #103, #125, #177, #179.

Two review modes, one driver. A doc (`start --doc`) is parsed by
`parse_sections.py` and served `--mode review`; a diff (`start --target <pr|ref>`
or `--kind worktree`) is captured by the argv `review_target.py` prints, parsed by
`parse_diff.py`, and served `--mode diff`. Every subcommand after `start` reads
the mode off the round file — the agent types neither a mode nor a round.

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
import os
import re
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
# A diff round above this many hunks stops at a seam until every hunk carries a
# one-line `summary` (`loop.py summarize`): below it the collapsed list is
# already navigable, and the summaries are not worth the tokens (#188).
SUMMARY_THRESHOLD = 10


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


def run_stdin_or_die(cmd, text: str, what: str, recovery: str = "") -> str:
    """`run_or_die` for a sibling that reads its input on stdin and answers on
    stdout — a bundle into a check, a sidecar into `annotate.py`."""
    proc = run(cmd, input=text, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        tail = f" {recovery}" if recovery else ""
        die(f"{what} failed: {' '.join(str(c) for c in cmd)}"
            + (f" — {err}" if err else "") + f".{tail}")
    return proc.stdout


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


def _is_interview(payload: dict) -> bool:
    """A qa server serves `questions` and never `sections` — the one shape
    `start --handoff` may push a round into."""
    return "questions" in payload


def _preflight_no_live_session(viva: Path) -> None:
    """Refuse to clear over a session that may still be live.

    Two cases wear one file, and they take opposite recoveries — so ask the
    server rather than guessing from the stat. Live: the human already has the
    tab, and telling them to delete the file that points at it is how a running
    review (or the interview `/viva-write` left open) gets orphaned. Not
    answering: the `finally` that unlinks this never ran, and deleting it is
    exactly right."""
    if not (viva / "server.url").exists():
        return
    base = server_url(viva)
    payload = probe_input(base, timeout=_PREFLIGHT_TIMEOUT) if base else None
    if payload is not None:
        if _is_interview(payload):
            die(f"an interview is already open at {base} — `loop.py start "
                f"--handoff` hands a round to that tab; `loop.py abandon` "
                f"ends it.")
        die(f"a session is already open at {base} — that tab is the live "
            f"review. Finish it there, or `loop.py abandon`, before "
            f"starting another.")
    # `server_url` is None for an empty file, which is still a collision.
    where = f" ({base})" if base else ""
    die(f"{viva}/server.url exists but nothing is answering{where} — a "
        f"prior session was killed without cleaning up. Delete the file, "
        f"then re-run.")


def _clear_state(viva: Path, keep_server_url: bool = False,
                 include_answers: bool = False,
                 include_attachments: bool = True) -> None:
    """The session state clear (CLAUDE.md, State lifecycle). `preferences.json`
    is the one survivor. `start` runs it whole. `start --handoff` keeps
    `server.url` — the interview server that will receive the round owns it —
    and `attachments/`, because the interview's answers may cite files there.
    `interview` adds `answers.json`, which `start` must never touch: a hand-off
    `start` runs while the draft written from those answers is still the
    agent's source."""
    for p in list(viva.glob("review-input-r*.json")) + list(viva.glob("review-r*.json")):
        p.unlink()
    names = ["open-notes.json", "target.json", "diff.patch"]
    if not keep_server_url:
        names.append("server.url")
    if include_answers:
        names.append("answers.json")
    for name in names:
        (viva / name).unlink(missing_ok=True)
    if include_attachments:
        # Attachment filenames are deterministic, so a surviving directory
        # silently re-points a prior ledger's citations at a later session's
        # images.
        shutil.rmtree(viva / "attachments", ignore_errors=True)


def _launch_server(viva: Path, mode: str, inp: Path, out: Path) -> str:
    """Launch `server.py` detached and return its base URL once `server.url`
    appears. Both of the child's streams go to `.viva/server.log`: an inherited
    stdout is held open by the grandchild, so a caller piping this driver would
    hang in `communicate()` until the server itself exited."""
    log = viva / "server.log"
    with log.open("wb") as logfh:
        proc = subprocess.Popen(
            [str(sys.executable), str(SERVER), "--mode", mode,
             "--input", str(inp), "--output", str(out)],
            stdout=logfh, stderr=logfh,
        )
    for _ in range(_POLL_TRIES):
        if (viva / "server.url").exists():
            break
        if proc.poll() is not None:
            # The headless contract documents a one-line startup error shape;
            # discarding it made every launch failure look alike.
            tail = log.read_text().strip().splitlines()
            why = tail[-1] if tail else "no output"
            die(f"server exited during startup ({why}). Full log: {log}")
        time.sleep(_POLL_INTERVAL)
    base = server_url(viva)
    if not base:
        proc.kill()
        die(f"server start timed out — no server.url appeared. Log: {log}")
    return base


# A bundle's `checks[]` name producers by the mechanical mapping
# `<name with - as _>.py` (CLAUDE.md, headings_present.py). A repo-committed
# `.viva-types/` bundle is caller input that becomes a path, so the name is
# shape-checked before it is joined to anything.
_CHECK_NAME = re.compile(r"^[a-z0-9-]+$")


def _check_script(name: str) -> Path:
    return SCRIPTS / (name.replace("-", "_") + ".py")


def _validate_checks(bundle: dict, fatal: bool = True) -> None:
    """Every check a bundle names must be a script this plugin ships — refused
    beside `resolve_doc_type`, before any state is cleared, for the same reason
    an unknown type is: a name that resolves to nothing would run the round at
    a depth whose checks never ran, silently."""
    for name in bundle.get("checks") or []:
        if not isinstance(name, str) or not _CHECK_NAME.match(name):
            why = f"type {bundle.get('name')!r} names an unusable check {name!r}"
        elif not _check_script(name).is_file():
            why = (f"type {bundle.get('name')!r} names check {name!r}, but "
                   f"{_check_script(name)} does not exist — the round would run "
                   f"at a depth whose checks never ran")
        else:
            continue
        if fatal:
            die(why)
        warn(why)


def _run_bundle_checks(bundle: dict, round_file: Path) -> int:
    """Run the type's mechanical checks and merge their flags; return the count.

    Pre-arm by construction — this runs inside `start`, between the parse and
    every branch that could arm, so `annotate`'s already-armed guard has nothing
    to protect here and `annotate.py` is called directly. The producer seam that
    follows is for the judgment producers (confidence, preferences), which are
    the agent's to run."""
    flags = []
    for name in bundle.get("checks") or []:
        script = _check_script(name)
        if not script.is_file():          # the resume path warned rather than died
            continue
        out = run_stdin_or_die(
            [sys.executable, script, "--input", round_file, "--bundle", "-"],
            json.dumps(bundle), f"check {name!r}",
            f"Round file {round_file} is parsed but not armed; fix the check "
            f"and re-run `loop.py start`.")
        try:
            emitted = json.loads(out or "[]")
        except ValueError:
            die(f"check {name!r} emitted non-JSON — see "
                f"{REFERENCES / 'producers.md'}")
        if not isinstance(emitted, list):
            die(f"check {name!r} must emit a JSON list of flags")
        flags += emitted
    if flags:
        run_stdin_or_die(
            [sys.executable, SCRIPTS / "annotate.py",
             "--input", round_file, "--annotations", "-"],
            json.dumps(flags), "check merge",
            f"{round_file} is unchanged on failure.")
    return len(flags)


def _seam_stop(round_no: int, round_file: Path, why: str,
               diff: bool = False) -> int:
    print(f"viva-loop: round {round_no} parsed, NOT armed — {why}")
    if diff:
        # A diff seam is for the hunk summaries, not a producer — there is no
        # producer contract to point at, and the merge verb is `summarize`.
        print("viva-loop: write one line per hunk, then `loop.py summarize "
              "--map <path|->` and `loop.py arm`.")
    else:
        print("viva-loop: run your producer, then `loop.py annotate --sidecar "
              "<path>` and `loop.py arm`.")
    # Named, not templated: a producer reading `--input` needs this path, and
    # computing `review-input-r{N}.json` is the counter this file exists to stop
    # the agent holding.
    print(f"viva-loop: round file → {round_file}")
    if not diff:
        print(f"viva-loop: producer contract → {REFERENCES / 'producers.md'}")
    return 0


# ── diff review: the target record and the capture ───────────────────────────
def _classify(target: Optional[str], kind: Optional[str]) -> dict:
    """One target → one dispatch record, from `review_target.py` — a subprocess,
    so `schema` stays the one cross-import. It runs before the pre-flight and
    before any clear, so a bad target costs nothing."""
    argv = [sys.executable, SCRIPTS / "review_target.py"]
    if target is not None:
        argv.append(target)
    if kind is not None:
        argv += ["--kind", kind]
    proc = run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        die((proc.stderr or "").strip() or "review_target.py failed")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        die("review_target.py printed non-JSON")
    return {}  # unreachable; die() raises


def _target_record(viva: Path) -> Tuple[dict, Path]:
    """The record `start` saved, and the directory its capture must run in."""
    path = viva / "target.json"
    if not path.exists():
        die(f"{path} is gone — the capture argv is not recoverable. "
            f"`loop.py abandon`, then start again.")
    record = load_json(path)
    return record, Path(record.get("cwd") or Path.cwd())


def _capture(record: dict, dest: Path, cwd: Path) -> int:
    """Run the record's `capture` argv with stdout into `dest`; return the size.

    A failed capture must never leave a patch behind: a 0-byte or partial
    `diff.patch` is read by every caller as "no changes", and a session whose
    `gh` call 403'd (the wrong account active) would sign off as fully resolved
    with nothing reviewed. So on any failure the file is removed and the argv
    and its stderr are the error."""
    argv = [str(c) for c in record.get("capture") or []]
    if not argv:
        die(f"{record.get('label')!r} has no capture argv — a doc is not a diff")
    if not cwd.is_dir():
        die(f"the capture must run from {cwd}, which is gone. Re-run from the "
            f"directory the review was started in.")
    try:
        with open(str(dest), "wb") as fh:
            proc = subprocess.Popen(argv, stdout=fh, stderr=subprocess.PIPE,
                                    cwd=str(cwd))
            _, err = proc.communicate()
    except FileNotFoundError:
        dest.unlink(missing_ok=True)
        die(f"capture failed: {argv[0]} is not on PATH ({' '.join(argv)})")
    if proc.returncode != 0:
        dest.unlink(missing_ok=True)
        die(f"capture failed ({proc.returncode}): {' '.join(argv)} — "
            f"{(err or b'').decode(errors='replace').strip()}")
    return dest.stat().st_size


def _needs_summaries(data: dict) -> bool:
    sections = data.get("sections", [])
    return (len(sections) > SUMMARY_THRESHOLD
            and any(not s.get("summary") for s in sections))


def _relaunch_hint(viva: Path) -> str:
    """The `start` that would recreate this diff session, rebuilt from the
    record — the label (`PR #187 (o/r)`, `working tree`) is not a legal
    target."""
    try:
        record = load_json(viva / "target.json")
    except (OSError, ValueError):
        return "`loop.py start --target <target>`"
    kind = record.get("kind")
    if kind == "worktree":
        return "`loop.py start --kind worktree`"
    if kind == "ref":
        return f"`loop.py start --target {record.get('ref')} --kind ref`"
    if kind == "pr":
        repo = f" (repo {record['repo']})" if record.get("repo") else ""
        return f"`loop.py start --target {record.get('number')} --kind pr`{repo}"
    return "`loop.py start --target <target>`"


def _diff_seam_or_arm(args, round_no: int, round_file: Path, data: dict) -> int:
    """The diff round's one seam: the hunk summaries. `--parse-only` holds it
    open regardless; `--arm-anyway` declines it (summaries are advisory)."""
    if args.parse_only:
        return _seam_stop(round_no, round_file, "--parse-only", diff=True)
    if _needs_summaries(data) and not args.arm_anyway:
        sections = data.get("sections", [])
        missing = sum(1 for s in sections if not s.get("summary"))
        return _seam_stop(round_no, round_file,
                          f"{missing} of {len(sections)} hunks need a summary",
                          diff=True)
    return cmd_arm(args)


# ── subcommands ───────────────────────────────────────────────────────────────
def cmd_interview(args) -> int:
    """Run the Q&A gate (`references/qa.md`): clear, launch `--mode qa`, block
    for the answers, print them. Never `/complete` — the hand-off reuses this
    process, and `start --handoff` + `arm` is what ends the interview."""
    viva = Path(args.viva_dir)
    qa_in = Path(args.input)
    if not qa_in.exists():
        die(f"qa-input not found: {qa_in}")
    _preflight_no_live_session(viva)
    viva.mkdir(parents=True, exist_ok=True)
    answers = viva / "answers.json"
    # `start`'s clear plus the answers: a stale `answers.json` would satisfy the
    # wait below before the human typed a word.
    _clear_state(viva, include_answers=True)
    base = _launch_server(viva, "qa", qa_in, answers)
    # Flushed: this process now blocks on human time, and whoever launched it
    # needs the URL before that, not after.
    print(f"viva-loop: interview open · {base}", flush=True)

    # Same liveness contract as `wait`: never block on a server that is gone.
    while not answers.exists():
        live = server_url(viva)
        if not live:
            die(f"the interview server is gone ({viva}/server.url disappeared) "
                f"and no answers were written. Re-run `loop.py interview "
                f"--input {qa_in}`.", 2)
        if probe_input(live) is None:
            die(f"the interview server at {live} is not answering and no "
                f"answers were written. Delete {viva}/server.url, then re-run "
                f"`loop.py interview --input {qa_in}`.", 2)
        time.sleep(_WAIT_INTERVAL)

    # The answers verbatim (the server writes them atomically, so existence is
    # completeness), then one classification line LAST — the agent routes on
    # the token, never on its own scan.
    text = answers.read_text()
    print(text, end="" if text.endswith("\n") else "\n")
    try:
        early = bool(json.loads(text).get("submitted_early"))
    except (ValueError, AttributeError):
        early = False
    print(f"=== interview: {'submitted-early' if early else 'answered'} ===")
    return 0


def cmd_start(args) -> int:
    viva = Path(args.viva_dir)
    diff_form = args.target is not None or args.kind is not None
    if args.doc is not None and diff_form:
        die("--doc is the doc form and --target/--kind the diff form — pass one "
            "or the other")
    if args.doc is None and not diff_form:
        die("no target — `loop.py start --doc <path>` reviews a doc; `loop.py "
            "start --target <pr|ref>` or `loop.py start --kind worktree` reviews "
            "a diff")
    if args.handoff and diff_form:
        die("--handoff hands a round to an interview, which is a doc review — "
            "use --doc")
    if diff_form:
        record = _classify(args.target, args.kind)
        if record.get("kind") != "doc":
            return _start_diff(args, viva, record)
        # A `--target` naming a markdown file is the doc form spelled the other
        # way — filesystem first, then shape (review_target.py).
        args.doc = record["doc"]
    return _start_doc(args, viva)


def _start_diff(args, viva: Path, record: dict) -> int:
    for flag, value in (("--split-on", args.split_on), ("--type", args.doc_type),
                        ("--pass", args.pass_kind), ("--posture", args.posture)):
        if value is not None:
            die(f"{flag} is a doc-review flag; {record.get('label')} is "
                f"reviewed hunk by hunk")
    _preflight_no_live_session(viva)
    viva.mkdir(parents=True, exist_ok=True)
    # No resume branch and no preference seam: neither has hunk semantics.
    _clear_state(viva)
    cwd = Path.cwd().resolve()
    # The record verbatim plus the one thing it lacks — where its capture
    # runs. `rearm` and `finish` re-capture from a later shell whose cwd this
    # driver does not control.
    (viva / "target.json").write_text(
        json.dumps(dict(record, cwd=str(cwd)), indent=2) + "\n")
    size = _capture(record, viva / "diff.patch", cwd)
    if size == 0:
        print(f"viva-loop: no changes to review — {record.get('label')}")
        return 0
    round_file = viva / "review-input-r1.json"
    # A non-empty patch with no hunks is a real failure, not an empty diff:
    # `parse_diff.py` exits 1 on it and this dies rather than completing.
    run_or_die([sys.executable, SCRIPTS / "parse_diff.py", viva / "diff.patch",
                "--output", round_file, "--round", "1",
                "--doc-file", record.get("label") or "working tree"],
               "parse", f"The patch is at {viva / 'diff.patch'}.")
    data = load_json(round_file)
    print(f"viva-loop: {len(data.get('sections', []))} hunk(s) · "
          f"{record.get('label')}")
    return _diff_seam_or_arm(args, 1, round_file, data)


def _start_doc(args, viva: Path) -> int:
    doc = Path(args.doc)
    if not doc.exists():
        die(f"doc not found: {doc}")

    bundle = resolve_doc_type(args.doc_type) if args.doc_type else None
    if bundle:
        _validate_checks(bundle)

    # Pre-flight guard. `cmd_start`'s own clear below deletes the round files
    # and `server.url`; without this check it would do that to a *live* session,
    # orphaning a running server with the reviewer's tab still attached. The
    # dependency is file-local — the clear is forty lines down, not in prose.
    #
    # `--handoff` inverts it: the live server is the point. It is the interview
    # `/viva-write` ran, and the round parsed here is armed INTO that process so
    # the same tab reflows from Q&A cards to section cards. Explicit, never
    # inferred from the payload — an abandoned interview must not quietly become
    # the next `/viva-review`'s tab.
    if args.handoff:
        base = server_url(viva)
        if not base:
            die(f"--handoff needs a live interview to hand off to, and "
                f"{viva}/server.url does not exist. Run `loop.py interview "
                f"--input .viva/qa-input.json` first, or drop --handoff.")
        payload = probe_input(base, timeout=_PREFLIGHT_TIMEOUT)
        if payload is None:
            die(f"--handoff needs a live interview at {base}, but nothing is "
                f"answering there. Delete {viva}/server.url, then re-run "
                f"without --handoff.")
        if not _is_interview(payload):
            die(f"--handoff needs a live interview at {base}; that server is "
                f"serving a review session (round {payload.get('round', '?')}). "
                f"Finish it there, or `loop.py abandon`.")
    else:
        _preflight_no_live_session(viva)

    viva.mkdir(parents=True, exist_ok=True)

    # Resume branch: a doc that already carries a sign-off ledger, with the
    # prior session's finishing round still on disk. Protect that pair OUTSIDE
    # the clear glob before clearing, or carry-forward dies with it. Never under
    # `--handoff`: the doc was drafted minutes ago in this same session, so a
    # ledger heading in it is a false positive, and any round files on disk
    # belong to the interview's session, not to a prior sign-off.
    prior_in = prior_out = None
    prior_split_on = prior_doc_type = None
    if not args.handoff and schema.has_revision_history(doc.read_text()):
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

    # Everything under .viva/ except preferences.json is disposable and reset
    # each session (CLAUDE.md). A hand-off keeps the two things the interview
    # still owns: its `server.url` and the attachments its answers cite.
    _clear_state(viva, keep_server_url=args.handoff,
                 include_attachments=not args.handoff)

    split_on = args.split_on if args.split_on is not None else prior_split_on
    doc_type = args.doc_type if args.doc_type is not None else prior_doc_type
    if bundle is None and doc_type is not None:
        # A resume carries the type without an explicit `--type`, so resolve it
        # here too — otherwise a resumed typed session names no check set and
        # the producers nobody is told about never run. Non-fatal: the scratch
        # pair above is already on disk, and dying here would strand it.
        bundle = resolve_doc_type(doc_type, fatal=False)
        if bundle:
            _validate_checks(bundle, fatal=False)
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

    round_file = viva / "review-input-r1.json"
    if bundle:
        # The type's check set, named once where it is resolved — and RUN here,
        # before any branch that could arm. A check nobody is told about never
        # runs; a check the driver owns cannot be forgotten, and a typed round
        # with no flags to answer would otherwise close on the base alone.
        checks = ", ".join(bundle.get("checks") or []) or "none"
        print(f"viva-loop: doc type {bundle['name']} · checks: {checks}")
        if bundle.get("checks"):
            merged = _run_bundle_checks(bundle, round_file)
            print(f"viva-loop: checks run: {checks} · {merged} flag(s) merged")

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
        die("no round to annotate — run `loop.py start` first")
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
        die("no round to arm — run `loop.py start` first")
    inp, out = round_files(viva, n)

    # Branch on liveness, not on the round number. A round-1 `arm` re-run after
    # a slow start would otherwise launch a second server, see the *existing*
    # server.url on the first poll tick, and print a stale base while the new
    # process binds another port — the orphaned-server failure `start`'s guard
    # exists to prevent, reintroduced one subcommand over.
    #
    # Liveness is `probe_input`, never `probe_round`: a live qa server answers
    # `/input` with an interview payload that has no `round` key, and reading
    # that as "nothing is answering" is what kept `arm` from handing a round to
    # the interview `/viva-write` left open (#179).
    base = server_url(viva)
    if base and probe_input(base) is not None:
        payload = load_json(inp)
        payload["output"] = str(out)
        post(base, "/next-round", payload, f"arming round {n}",
             f"Fix the round file and re-run `loop.py arm` — or, if that URL "
             f"is not a viva server, delete {viva}/server.url.")
        print(f"viva-loop: round {n} armed · {base}")
        return 0
    if base:
        die(f"{viva}/server.url names {base}, but nothing is answering there. "
            f"Delete the stale file, then `loop.py start`.")

    # The mode is round state, written by the parser that produced the round,
    # never typed: `parse_sections.py` writes `review`, `parse_diff.py` `diff`.
    mode = load_json(inp).get("mode") or "review"
    if mode not in ("review", "diff"):
        die(f"round {n}'s input carries mode {mode!r} — expected review or diff")
    base = _launch_server(viva, mode, inp, out)
    print(f"viva-loop: round {n} armed · {base}")
    return 0


def cmd_summarize(args) -> int:
    """Merge a `{id: one-line summary}` map into the current round's hunks —
    the diff seam's driver end, the way `annotate` is the producer seam's."""
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to summarize — run `loop.py start --target <pr|ref>` or "
            "`loop.py start --kind worktree` first")
    inp, _ = round_files(viva, n)
    # Pre-arm, for the reason `annotate` is: the server reads its round once.
    base = server_url(viva)
    if base and probe_round(base) == n:
        die(f"round {n} is already armed — the server at {base} holds it in "
            f"memory and would never see this merge. Summarize before arming.")
    try:
        raw = sys.stdin.read() if args.map == "-" else Path(args.map).read_text()
        summaries = json.loads(raw)
    except OSError as e:
        die(f"cannot read --map: {e}")
    except ValueError:
        die("--map must be JSON")
    if not isinstance(summaries, dict):
        die("--map must be a JSON object of {id: summary}")
    data = load_json(inp)
    by_id = {s.get("id"): s for s in data.get("sections", [])}
    for sid, text in summaries.items():
        if sid not in by_id:
            die(f"unknown section id {sid!r} — round {n} carries "
                f"s1…s{len(by_id)}")
        if not isinstance(text, str) or not text.strip():
            die(f"summary for {sid} must be a non-empty string")
        by_id[sid]["summary"] = text.strip()
    try:
        schema.validate_review_input(data)
    except ValueError as e:
        die(f"invalid review-input after the merge: {e}")
    tmp = inp.with_name(inp.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(str(tmp), str(inp))
    print(f"viva-loop: round {n} summarized · {len(summaries)} of {len(by_id)} "
          f"hunk(s) · {inp}")
    return 0


def cmd_wait(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no armed round to wait on")
    inp, out = round_files(viva, n)
    input_data = load_json(inp)
    diff = input_data.get("mode") == "diff"
    if diff:
        relaunch = f"Relaunch with {_relaunch_hint(viva)}."
    else:
        relaunch = (f"Relaunch with `loop.py start --doc "
                    f"{input_data.get('doc_file', '<doc>')}` — carried "
                    f"approvals are preserved.")

    while not out.exists():
        base = server_url(viva)
        if not base:
            die(f"server is gone ({viva}/server.url disappeared) and round {n} "
                f"never returned verdicts. {relaunch}", 2)
        served = probe_round(base)
        if served is None:
            die(f"server at {base} is not answering and round {n} never "
                f"returned verdicts. Delete {viva}/server.url, then relaunch. "
                f"{relaunch}", 2)
        if served != n:
            # Parsed but never armed — `rearm --parse-only` wrote round n while
            # the server still serves round `served`, and nothing will ever
            # write this round's verdicts.
            die(f"round {n} is parsed but not armed — the server is still "
                f"serving round {served}. Run `loop.py arm` (after "
                f"`loop.py annotate` if a producer is pending).", 2)
        time.sleep(_WAIT_INTERVAL)

    verdicts = load_json(out)

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
    # A diff round has no threads and no prose, so neither rail applies.
    if klass in ("has-work", "submitted-early") and not diff:
        # The next step is the rewrite, and the rule it turns on — act on each
        # thread's *latest* reviewer turn — is documented, not obvious. A paused
        # round can carry comments too, so it needs the same pointer.
        print(f"viva-loop: thread rules for the rewrite → "
              f"{REFERENCES / 'open-notes.md'}")
        # The register is the other half of the rewrite: a section rewritten
        # loose re-presents with the same filler the reviewer just flagged.
        print(f"viva-loop: register for the rewrite → "
              f"{REFERENCES / 'style.md'}")
    return 0


def cmd_rearm(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to re-arm — run `loop.py start` first")
    inp, out = round_files(viva, n)
    if not out.exists():
        die(f"round {n} has no verdicts yet — run `loop.py wait` first")

    # The doc travels in the round file the parser wrote, so the agent names
    # neither the round nor the path it already handed `start`. The split
    # pattern travels the same way: `parse_sections.py` records it on every
    # round it parses, so re-reading it here is what keeps round N+1 splitting
    # the way round 1 did. Absent key → auto-detection, unchanged.
    round_data = load_json(inp)
    if round_data.get("mode") == "diff":
        return _rearm_diff(args, viva, n, inp, out, round_data)
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


def _rearm_diff(args, viva: Path, n: int, inp: Path, out: Path,
                round_data: dict) -> int:
    if args.response or args.decline or args.pass_kind is not None \
            or args.posture is not None:
        die("a diff round carries no threads and no pass — --response, "
            "--decline, --pass, and --posture apply to doc review only")
    record, cwd = _target_record(viva)
    # The SAME capture as round 1, never a different one: a `git diff`
    # substituted on round 2 of a PR review reviews the working tree instead,
    # which reads as a shrinking diff rather than as an error.
    size = _capture(record, viva / "diff.patch", cwd)
    if size == 0:
        # Not a sign-off: `finish` owns `/complete` in every mode, and it
        # re-captures for itself rather than trusting this line.
        print("viva-loop: diff is empty after re-capture — nothing to re-arm; "
              "`loop.py finish` signs it off")
        return 0
    nxt_in, _ = round_files(viva, n + 1)
    run_or_die([sys.executable, SCRIPTS / "parse_diff.py", viva / "diff.patch",
                "--output", nxt_in, "--round", str(n + 1),
                "--doc-file", round_data.get("doc_file") or record.get("label")
                or "working tree",
                "--prior-input", inp, "--prior-verdicts", out],
               "re-parse", f"The running server still holds round {n}.")
    return _diff_seam_or_arm(args, n + 1, nxt_in, load_json(nxt_in))


def cmd_finish(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to finish")
    inp, out = round_files(viva, n)
    if not out.exists():
        die(f"round {n} has no verdicts yet — nothing to finish")

    input_data, verdicts = load_json(inp), load_json(out)
    if input_data.get("mode") == "diff":
        return _finish_diff(args, viva, n, inp, out, input_data, verdicts)
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


def _finish_diff(args, viva: Path, n: int, inp: Path, out: Path,
                 input_data: dict, verdicts: dict) -> int:
    """The diff finish, decided from a FRESH capture, never from memory.

    Three outcomes. The capture is empty: every hunk was applied or reverted at
    the reviewer's request, so there is nothing left to approve — `/complete`
    is sent `resolved: "empty"`, the one signal the server's diff gate honors
    (#177). The capture equals round n and round n is all-approved: the normal
    finish. Anything else — a hunk changed since the human approved it, or one
    reverted without the rest being re-presented — is a `rearm`, because the
    server's gate would (rightly) refuse it and the human has not seen it."""
    if args.doc:
        die("--doc is a doc-review override; a diff session records its target "
            "in target.json")
    record, cwd = _target_record(viva)
    base = server_url(viva)
    if not base:
        die(f"no live server to complete (no {viva}/server.url). The verdicts "
            f"are on disk; nothing was written to the working tree.")
    sections = input_data.get("sections", [])
    revised = sum(1 for s in verdicts.get("sections", [])
                  if s.get("verdict") in schema.LEDGER_VERDICTS)
    summary = {"rounds_total": n, "sections_total": len(sections),
               "sections_revised": revised}

    size = _capture(record, viva / "diff.patch", cwd)
    if size == 0:
        post(base, "/complete", dict(summary, resolved="empty"),
             "completing the session", "The server may need `loop.py abandon`.")
        print("viva-loop: diff fully resolved — nothing to commit")
        print(f"viva-loop: signed off — {n} round(s), {len(sections)} hunk(s), "
              f"{revised} revised")
        return 0

    if not schema.round_is_complete(input_data, verdicts):
        by_id = {s.get("id"): s for s in verdicts.get("sections", [])}
        pending = [s.get("title") for s in sections
                   if (by_id.get(s.get("id")) or {}).get("verdict") != "approved"]
        if not sections:
            why = "the round carries no hunks to approve"
        else:
            why = (f"{len(pending)} of {len(sections)} hunk(s) not approved — "
                   f"{', '.join(repr(t) for t in pending[:5])}")
        die(f"refusing to finish: {why}. Nothing is auto-accepted; re-present "
            f"the round or abandon it.")

    # Freshness: the diff as it stands must be the diff the human approved.
    # `parse_diff.py` with round n as prior carries an approval only onto a hunk
    # whose title and body match, so a hunk edited, added, or dropped since the
    # sign-off falls out of `approved_ids`. The scratch name must not match
    # `current_round`'s `review-input-r*.json` glob.
    scratch = viva / "finish-check.json"
    try:
        run_or_die([sys.executable, SCRIPTS / "parse_diff.py",
                    viva / "diff.patch", "--output", scratch, "--round", str(n),
                    "--doc-file", input_data.get("doc_file") or "working tree",
                    "--prior-input", inp, "--prior-verdicts", out],
                   "re-parse",
                   "The session is still live; fix and re-run `loop.py finish`.")
        fresh = load_json(scratch)
    finally:
        scratch.unlink(missing_ok=True)
        scratch.with_name(scratch.name + ".tmp").unlink(missing_ok=True)
    carried = set(fresh.get("approved_ids", []))
    fresh_ids = [s.get("id") for s in fresh.get("sections", [])]
    if len(fresh_ids) != len(sections) or not all(i in carried for i in fresh_ids):
        die(f"the diff changed since round {n} was reviewed — `loop.py rearm` "
            f"to re-present it. Nothing is auto-accepted.")

    post(base, "/complete", summary, "completing the session",
         "The server may need `loop.py abandon`.")
    print(f"viva-loop: signed off — {n} round(s), {len(sections)} hunk(s), "
          f"{revised} revised")
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

    p = sub.add_parser("interview", help="clear state, run the Q&A interview, "
                                         "print the answers")
    p.add_argument("--input", required=True, metavar="PATH",
                   help="the QAInput JSON the caller wrote (references/qa.md). "
                        "Answers land in .viva/answers.json and on stdout; "
                        "the server stays up for `start --handoff`.")
    p.set_defaults(func=cmd_interview)

    p = sub.add_parser("start", help="clear state, parse round 1, arm it — a "
                                     "doc (--doc) or a diff (--target/--kind)")
    p.add_argument("--doc", default=None, metavar="PATH",
                   help="the markdown doc to review, section by section")
    p.add_argument("--target", default=None, metavar="TARGET",
                   help="a PR number/URL or a git ref to review hunk by hunk "
                        "(`review_target.py` classifies it; a markdown path "
                        "here is the doc form). The working tree takes no "
                        "target: `--kind worktree` alone.")
    p.add_argument("--kind", default=None,
                   choices=("doc", "pr", "ref", "worktree"),
                   help="force the target's kind — a branch named `42` needs "
                        "`--kind ref`; `--kind worktree` reviews unstaged "
                        "changes and takes no --target")
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
                   help="arm even when standing preferences (a doc) or missing "
                        "hunk summaries (a diff) would open the seam. Not the "
                        "rejected `finish --force`: this declines an advisory "
                        "step, it never bypasses the human gate.")
    p.add_argument("--handoff", action="store_true",
                   help="hand round 1 to the live interview at .viva/server.url "
                        "instead of refusing over it — the /viva-write seam. "
                        "Requires a live qa session; the round is armed into "
                        "that same process and its tab reflows in place.")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("annotate", help="merge a producer sidecar into the "
                                        "current round's review-input")
    p.add_argument("--sidecar", required=True,
                   help="producer sidecar JSON list, or '-' for stdin")
    p.set_defaults(func=cmd_annotate)

    p = sub.add_parser("summarize", help="merge one-line hunk summaries into "
                                         "the current diff round (pre-arm)")
    p.add_argument("--map", required=True, metavar="PATH",
                   help="JSON object of {section id: summary}, or '-' for stdin")
    p.set_defaults(func=cmd_summarize)

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
    p.add_argument("--arm-anyway", action="store_true",
                   help="diff review: arm even when new hunks lack a summary")
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
