#!/usr/bin/env python3
"""viva's review-loop driver — bookkeeping for launch → wait → act → rewrite,
so SKILL.md carries only judgment work.

Nine subcommands: `interview`, `start`, `annotate`, `summarize`, `arm`, `wait`,
`rearm`, `finish`, `abandon` (#104, #102, #103, #125, #177, #179). A doc
(`--doc`) is parsed by `parse_sections.py` and served `--mode review`; a diff
(`--target`/`--kind`) is captured and parsed by `parse_diff.py`, served
`--mode diff`. Every subcommand after `start` derives the round and reads the
mode off the round file — never typed.
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
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schema  # noqa: E402  — the one permitted sibling import (CLAUDE.md)

# Resolved from __file__, not a caller's $VIVA_DIR — the plugin root is this
# file's grandparent.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN_ROOT / "scripts"
SERVER = PLUGIN_ROOT / "server.py"
# Shared by both skills, so it sits at the plugin root rather than inside
# either one.
REFERENCES = PLUGIN_ROOT / "references"

_POLL_TRIES = 100        # × _POLL_INTERVAL ≈ 10s, for a server coming up or going down
_POLL_INTERVAL = 0.1
_WAIT_INTERVAL = 0.3     # verdict poll — human review time, not computation
_HTTP_TIMEOUT = 10
# Short: a pre-flight probe for "is anyone home" must not stall a start behind
# a `server.url` whose process is long gone.
_PREFLIGHT_TIMEOUT = 2
# Above this many hunks, a diff round stops until every hunk carries a
# one-line `summary` (`loop.py summarize`) — below it, not worth it (#188).
SUMMARY_THRESHOLD = 10


def die(msg: str, code: int = 1) -> None:
    sys.stderr.write(f"viva-loop: {msg}\n")
    raise SystemExit(code)


def warn(msg: str) -> None:
    sys.stderr.write(f"viva-loop: warning: {msg}\n")


def run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], **kw)


def run_or_die(cmd, what: str, recovery: str = "") -> None:
    """Every sibling call is checked, so a failed write can't exit clean."""
    if run(cmd).returncode != 0:
        tail = f" {recovery}" if recovery else ""
        die(f"{what} failed: {' '.join(str(c) for c in cmd)}.{tail}")


def run_stdin_or_die(cmd, text: str, what: str, recovery: str = "") -> str:
    """`run_or_die` for a sibling reading stdin, answering on stdout."""
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
    rounds = [schema.parse_round_input_stem(p.stem)
              for p in viva.glob(schema.round_input_glob())]
    return max((n for n in rounds if n is not None), default=0)


def round_files(viva: Path, n: int) -> Tuple[Path, Path]:
    return schema.round_file_paths(viva, n)


def load_json(p: Path) -> dict:
    with p.open() as fh:
        return json.load(fh)


# ── liveness — probed, not stat'ed ────────────────────────────────────────────
def server_url(viva: Path) -> Optional[str]:
    """`.viva/server.url` is repo-supplied state, so its host is constrained to
    loopback (mirrors `server.py`'s own Origin guard) — a repo committing a
    `server.url` naming an attacker's host must not turn a probe or POST into
    an SSRF against it."""
    f = viva / "server.url"
    if not f.exists():
        return None
    url = f.read_text().strip()
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        die(f"{f} names {url!r}, which is not a loopback address. Refusing to "
            f"contact it — delete the file and re-run `loop.py start`.")
    return url


def _request(req: urllib.request.Request, what: str, recovery: str) -> bytes:
    """One error shape for every HTTP call — the server's `{"error": ...}`
    body reaches the agent instead of a traceback."""
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
    """The payload the server at `base` is serving, or None if nothing answers.
    File existence proves neither liveness nor armed-ness (a killed process
    skips the `finally` that unlinks `server.url`). This is the liveness
    question, deliberately distinct from `probe_round`: a live qa server
    answers `/input` with no `round` key, so only this one may be read as dead."""
    try:
        with urllib.request.urlopen(base + "/input", timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    # Alive but not ours: a non-dict body must not be `.get()`-ed by a caller.
    return payload if isinstance(payload, dict) else {}


def probe_round(base: str) -> Optional[int]:
    """The round actually being served, or None if not answering — or
    answering with no round (a qa payload)."""
    payload = probe_input(base)
    return payload.get("round") if payload is not None else None


def standing_preferences(viva: Path) -> list:
    """`[]` means no standing preferences. A store that exists but won't read
    is a different fact and says so on stderr, rather than silently
    disengaging the preference producer for the session."""
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
    """Resolve a type name to its bundle — the name enters the system here.
    A subprocess, not an import: `schema.py` stays the one cross-import
    (CLAUDE.md). `fatal=False` is the resume path (name from the prior round
    file, not the CLI): it warns instead, so a repo that dropped its
    `.viva-types/` bundle between sessions can still resume."""
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
    """A qa server serves `questions`, never `sections`."""
    return "questions" in payload


def _preflight_no_live_session(viva: Path) -> None:
    """Refuse to clear over a session that may still be live. Two cases wear
    one file with opposite recoveries, so ask the server rather than guessing
    from the stat: live means don't touch it, dead means delete it."""
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
    is the one survivor. `start --handoff` keeps `server.url` (the interview
    server) and `attachments/` (the answers may cite files there); `interview`
    adds `answers.json`, which `start` must never touch."""
    for p in list(viva.glob(schema.round_input_glob())) + list(viva.glob(schema.round_output_glob())):
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
        # silently re-points a prior ledger's citations at a later session.
        shutil.rmtree(viva / "attachments", ignore_errors=True)


def _launch_server(viva: Path, mode: str, inp: Path, out: Path) -> str:
    """Launch `server.py` detached; return its base URL once `server.url`
    appears. Streams go to `.viva/server.log`, not inherited stdout — an
    inherited pipe would hang a caller until the grandchild exits."""
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
            # Last line of the log: the headless contract's one-line error shape.
            tail = log.read_text().strip().splitlines()
            why = tail[-1] if tail else "no output"
            die(f"server exited during startup ({why}). Full log: {log}")
        time.sleep(_POLL_INTERVAL)
    base = server_url(viva)
    if not base:
        proc.kill()
        die(f"server start timed out — no server.url appeared. Log: {log}")
    return base


# A repo-committed `.viva-types/` bundle names checks that become a path
# (CLAUDE.md), so the name is shape-checked before it is joined to anything.
_CHECK_NAME = re.compile(r"^[a-z0-9-]+$")


def _check_script(name: str) -> Path:
    return SCRIPTS / (name.replace("-", "_") + ".py")


def _validate_checks(bundle: dict, fatal: bool = True) -> None:
    """Every check a bundle names must be a script this plugin ships — refused
    before any state is cleared, so a bad name never runs the round silently
    at a depth whose checks never ran."""
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
    """Run the type's mechanical checks and merge their flags; return the
    count. Pre-arm by construction (runs inside `start`, before any arm
    branch), so `annotate.py` is called directly rather than through `annotate`."""
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
        # A diff seam is for hunk summaries, not a producer; merge verb is `summarize`.
        print("viva-loop: write one line per hunk, then `loop.py summarize "
              "--map <path|->` and `loop.py arm`.")
    else:
        print("viva-loop: run your producer, then `loop.py annotate --sidecar "
              "<path>` and `loop.py arm`.")
    # Named, not templated: a producer needs this path without computing it.
    print(f"viva-loop: round file → {round_file}")
    if not diff:
        print(f"viva-loop: producer contract → {REFERENCES / 'producers.md'}")
    return 0


# ── diff review: the target record and the capture ───────────────────────────
def _classify(target: Optional[str], kind: Optional[str]) -> dict:
    """One target → one dispatch record, from `review_target.py`. Runs before
    the pre-flight and any clear, so a bad target costs nothing."""
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
    """The record `start` saved, and the directory its capture runs in."""
    path = viva / "target.json"
    if not path.exists():
        die(f"{path} is gone — the capture argv is not recoverable. "
            f"`loop.py abandon`, then start again.")
    record = load_json(path)
    return record, Path(record.get("cwd") or Path.cwd())


def _capture(record: dict, dest: Path, cwd: Path) -> int:
    """Run the record's `capture` argv with stdout into `dest`; return the
    size. A failed capture must never leave a patch behind — every caller
    reads a 0-byte/partial `diff.patch` as "no changes" — so on any failure
    the file is removed and the argv/stderr become the error."""
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
    record — the label (`PR #187 (o/r)`) is not itself a legal target."""
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
    for answers, print them. Never `/complete` — `start --handoff` + `arm`
    ends the interview instead."""
    viva = Path(args.viva_dir)
    qa_in = Path(args.input)
    if not qa_in.exists():
        die(f"qa-input not found: {qa_in}")
    _preflight_no_live_session(viva)
    viva.mkdir(parents=True, exist_ok=True)
    answers = viva / "answers.json"
    # A stale `answers.json` would satisfy the wait below with no answer.
    _clear_state(viva, include_answers=True)
    base = _launch_server(viva, "qa", qa_in, answers)
    # Flushed: this process now blocks on human time.
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

    # Answers verbatim, then a classification line LAST — the agent routes on
    # the token, never its own scan.
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
        # A `--target` naming a markdown file is the doc form spelled the
        # other way — filesystem first, then shape (review_target.py).
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
    # Record plus its cwd: `rearm`/`finish` re-capture from a later shell
    # whose cwd this driver does not control.
    (viva / "target.json").write_text(
        json.dumps(dict(record, cwd=str(cwd)), indent=2) + "\n")
    size = _capture(record, viva / "diff.patch", cwd)
    if size == 0:
        print(f"viva-loop: no changes to review — {record.get('label')}")
        return 0
    round_file = viva / "review-input-r1.json"
    # A non-empty patch with no hunks is a real failure: `parse_diff.py`
    # exits 1 on it and this dies rather than completing.
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

    # Pre-flight guard: without it, the clear below would orphan a live
    # session's server. `--handoff` inverts it — the live interview server is
    # the target the parsed round arms INTO, so the tab reflows from Q&A to
    # section cards; never inferred, so an abandoned interview can't quietly
    # become the next `/viva-review`'s tab.
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

    # Resume branch: a doc with a sign-off ledger and the prior session's
    # finishing round still on disk. Copy that pair OUTSIDE the clear glob
    # before clearing, or carry-forward dies with it. Never under `--handoff`
    # — the doc was drafted minutes ago in this session, so a ledger heading
    # is a false positive.
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
                # The prior round records its split pattern; read it back the
                # way `rearm` does between rounds, or re-detection silently
                # changes every section's identity.
                prior_round = load_json(prior_in)
                prior_split_on = prior_round.get("split_on")
                # Type is round state on the same terms — read back here,
                # overridden only by an explicit `--type`, or a resume
                # silently drops the prior session's check set.
                prior_doc_type = prior_round.get("doc_type")
                # `pass` is deliberately NOT read back — it's a per-round
                # decision, and inheriting the prior session's finishing
                # `final` pass would add a conjunct nobody asked for.

    # Everything under .viva/ but preferences.json resets each session
    # (CLAUDE.md); a hand-off keeps what the interview still owns.
    _clear_state(viva, keep_server_url=args.handoff,
                 include_attachments=not args.handoff)

    split_on = args.split_on if args.split_on is not None else prior_split_on
    doc_type = args.doc_type if args.doc_type is not None else prior_doc_type
    if bundle is None and doc_type is not None:
        # A resume carries the type without an explicit `--type`; resolve it
        # here too or its check set silently never runs. Non-fatal: the
        # scratch pair above is already on disk.
        bundle = resolve_doc_type(doc_type, fatal=False)
        if bundle:
            _validate_checks(bundle, fatal=False)
    round1_input, _ = schema.round_file_paths(viva, 1)
    cmd = [sys.executable, SCRIPTS / "parse_sections.py", doc,
           "--output", round1_input, "--round", "1",
           "--doc-file", args.doc]
    if split_on is not None:
        cmd += ["--split-on", split_on]
    if doc_type is not None:
        cmd += ["--doc-type", doc_type]
    if args.pass_kind is not None:
        cmd += ["--pass", args.pass_kind]
    if args.posture is not None:
        # Passed even without `--pass`, so the boundary (`parse_sections.py`)
        # refuses a posture on no pass instead of it being silently dropped.
        cmd += ["--posture", args.posture]
    if prior_in and prior_out:
        cmd += ["--prior-input", prior_in, "--prior-verdicts", prior_out]
    try:
        if run(cmd).returncode != 0:
            die("parse failed")
    finally:
        # One resume only — else the next `start` reads a stale pair.
        if prior_in:
            prior_in.unlink(missing_ok=True)
            prior_out.unlink(missing_ok=True)

    round_file = round1_input
    if bundle:
        # The type's check set is RUN here, before any branch that could arm
        # — a check the driver doesn't run here never runs.
        checks = ", ".join(bundle.get("checks") or []) or "none"
        print(f"viva-loop: doc type {bundle['name']} · checks: {checks}")
        if bundle.get("checks"):
            merged = _run_bundle_checks(bundle, round_file)
            print(f"viva-loop: checks run: {checks} · {merged} flag(s) merged")

    if args.parse_only:
        return _seam_stop(1, round_file, "--parse-only")
    # A standing preference auto-engages the preference producer, an LLM
    # pass — the agent's work, not the driver's.
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
    # Annotate is PRE-ARM only: the server loads its round once from
    # `/next-round`, so annotating an already-armed round writes a file
    # nobody re-reads (loud failure here beats a silent one at `/complete`).
    base = server_url(viva)
    if base and probe_round(base) == n:
        die(f"round {n} is already armed — the server at {base} holds it in "
            f"memory and would never see this merge. Annotate before arming: "
            f"finish or `rearm --parse-only` this round, annotate the next one, "
            f"then `loop.py arm`.")
    # The agent names its sidecar, the driver names the file; '-' reads stdin.
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

    # Branch on liveness, not the round number — a re-run after a slow start
    # would otherwise launch a second orphaned server.
    # Liveness is `probe_input`, never `probe_round`: a live qa server has no
    # `round` key, and reading that as dead broke handing a round to an open
    # `/viva-write` interview (#179).
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

    # Mode is round state, never typed — the parser wrote it.
    mode = load_json(inp).get("mode") or "review"
    if mode not in ("review", "diff"):
        die(f"round {n}'s input carries mode {mode!r} — expected review or diff")
    base = _launch_server(viva, mode, inp, out)
    print(f"viva-loop: round {n} armed · {base}")
    return 0


def cmd_summarize(args) -> int:
    """Merge a `{id: one-line summary}` map into the current round's hunks —
    the diff seam's driver end, like `annotate` for producers."""
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
            # Parsed but never armed: `rearm --parse-only` wrote round n while
            # the server still serves `served`, so no verdicts will ever land.
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

    # The classification line the agent branches on, never its own scan (#102).
    # `submitted_early` is checked first: a paused round is paused even when
    # everything submitted so far was approved.
    if verdicts.get("submitted_early"):
        klass = "submitted-early"
    elif schema.round_is_complete(input_data, verdicts):
        klass = "all-approved"
    else:
        klass = "has-work"
    print(f"=== round {n}: {klass} ===")
    # A diff round has no threads and no prose, so neither rail applies.
    if klass in ("has-work", "submitted-early") and not diff:
        # Act on each thread's *latest* reviewer turn — not obvious, so named.
        print(f"viva-loop: thread rules for the rewrite → "
              f"{REFERENCES / 'open-notes.md'}")
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

    # Doc, split pattern, and type travel in the round file the parser wrote,
    # so the agent names none of them again — round N+1 splits the way round
    # 1 did. Absent key → auto-detection, unchanged.
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

    # `pass` carries within the session like the pattern and type do — round
    # N+1 runs at round N's depth unless overridden here, since depth is the
    # one of the three expected to change mid-session.
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
    # The author's other move: `open_notes.py` refuses a second decline on
    # the same thread (insisting wins), and `run_or_die` stops the round
    # rather than silently overwriting it.
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
    # `is not None`, not truthiness: hand the pattern back exactly as
    # recorded rather than letting a later round quietly re-decide it.
    if split_on is not None:
        cmd += ["--split-on", split_on]
    if doc_type is not None:
        cmd += ["--doc-type", doc_type]
    if next_pass is not None:
        cmd += ["--pass", next_pass["kind"]]
        if next_pass.get("posture") is not None:
            cmd += ["--posture", next_pass["posture"]]
    run_or_die(cmd, "re-parse", f"The running server still holds round {n}.")

    # Round 2+ producer seam. Order is load-bearing: flags must merge before
    # the round ships to the running server.
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
    # The SAME capture as round 1, never a substitute — else a later round of
    # a PR review would silently review the working tree instead.
    size = _capture(record, viva / "diff.patch", cwd)
    if size == 0:
        # Not a sign-off: `finish` re-captures for itself before completing.
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
        # `round_is_complete` is the gate; this text is only its detail. A
        # pass ADDS a conjunct, so a round can be refused with everything
        # approved — "0 of N not approved" would be misleading here.
        spec = input_data.get("pass")
        kind = spec.get("kind") if isinstance(spec, dict) else None
        if not input_data.get("sections"):
            # Tested before the pass: an empty round fails the base rule, not
            # a conjunct an `architecture`/`line` pass doesn't even add.
            why = "the round carries no sections to approve"
        elif pending:
            why = (f"{len(pending)} of {len(input_data.get('sections', []))} "
                   f"section(s) not approved — "
                   f"{', '.join(repr(t) for t in pending[:5])}")
        elif kind:
            # Recovery is the round loop, not a mid-round annotate: the
            # server holds this round in memory and won't see the merge.
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

    # The doc comes off the round file, like `rearm` reads it — `--doc` stays
    # only as an override for a doc that legitimately moved.
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

    # Everything fallible runs BEFORE the irreversible POST: `/complete`
    # starts the server's shutdown timer, so a failure after it is stuck.
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
    # Only a signed-off session learns; the clustering asked for is judgment work.
    print(f"viva-loop: record this session's recurring critiques → "
          f"{REFERENCES / 'preferences.md'}")
    return 0


def _finish_diff(args, viva: Path, n: int, inp: Path, out: Path,
                 input_data: dict, verdicts: dict) -> int:
    """The diff finish, decided from a FRESH capture, never from memory.
    Three outcomes: empty capture sends `/complete` `resolved: "empty"`
    (#177); capture matches an all-approved round n is the normal finish;
    anything else (a hunk changed or reverted unevenly) is a `rearm`."""
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

    # Freshness: the diff must still be the one the human approved. A hunk
    # edited, added, or dropped since falls out of `approved_ids`. The scratch
    # name must not match `current_round`'s `review-input-r*.json` glob.
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
    # holds no child handle.
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
