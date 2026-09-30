#!/usr/bin/env python3
"""viva's review-loop driver — bookkeeping for launch → wait → act → rewrite,
so SKILL.md carries only judgment work.

Ten subcommands: `interview`, `start`, `annotate`, `summarize`, `arm`, `wait`,
`rearm`, `finish`, `abandon`, `session` (#104, #102, #103, #125, #177, #179,
#240). A doc
(`--doc`) is parsed by `parse_sections.py` and served `--mode review`; a diff
(`--target`/`--kind`) is captured and parsed by `parse_diff.py`, served
`--mode diff`. Every subcommand after `start` derives the round and reads the
mode off the round file — never typed.
"""
from __future__ import annotations

import argparse
import functools
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import schema

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


def load_json(p: Path) -> dict:
    with p.open(encoding="utf-8") as fh:
        return json.load(fh)


# ── liveness — probed, not stat'ed ────────────────────────────────────────────
def server_url(viva: Path) -> str | None:
    """`.viva/server.url` is repo-supplied state, so its host is constrained to
    loopback (mirrors `server.py`'s own Origin guard) — a repo committing a
    `server.url` naming an attacker's host must not turn a probe or POST into
    an SSRF against it."""
    f = viva / "server.url"
    if not f.exists():
        return None
    url = f.read_text(encoding="utf-8").strip()
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in schema.LOOPBACK_HOSTS:
        die(f"{f} names {url!r}, which is not a loopback address. Refusing to "
            f"contact it — delete the file and re-run `loop.py start`.")
    return url


def post(base: str, path: str, payload: dict, what: str, recovery: str = "") -> None:
    """The server's `{"error": ...}` body reaches the agent instead of a
    traceback — the one error shape every caller gets."""
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            resp.read()
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


def probe_input(base: str, timeout: float = _HTTP_TIMEOUT) -> dict | None:
    """The payload the server at `base` is serving, or None if nothing answers.
    File existence proves neither liveness nor armed-ness (a killed process
    skips the `finally` that unlinks `server.url`). This is the liveness
    question, deliberately distinct from `holds_round`: a live qa server
    answers `/input` with no `round` key, so only this one may be read as dead."""
    try:
        with urllib.request.urlopen(base + "/input", timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    # Alive but not ours: a non-dict body must not be `.get()`-ed by a caller.
    return payload if isinstance(payload, dict) else {}


def holds_round(base: str, inp: Path, n: int) -> bool:
    """Is the server at `base` serving round `n` of THIS round file?"""
    return schema.serves_round(probe_input(base), load_json(inp), n)


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


def resolve_doc_type(name: str, fatal: bool = True) -> dict | None:
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


def _die_waiting(base: str, session: dict, why: str) -> None:
    """A session server idling between gates blocks this worktree; name both
    exits, since plain `abandon` also ends the session."""
    die(f"session {session.get('id')} is waiting at {base} for its "
        f"implementing PR's diff gate{why}. `loop.py start --target <PR> "
        f"--join-session` arms that PR's diff into it; `loop.py abandon "
        f"--keep-session` stops the server and keeps the session for its PR; "
        f"plain `loop.py abandon` ends the session.")


def _refuse_between_gates(viva: Path, verb: str) -> None:
    """Refuse `verb` against a session server between gates — it outlives the
    spec's sign-off, so a retried `finish` would append a second ledger."""
    base = server_url(viva)
    payload = probe_input(base, timeout=_PREFLIGHT_TIMEOUT) if base else None
    if payload is not None and schema.session_is_waiting(payload.get("session")):
        _die_waiting(base, payload["session"],
                     f" — its spec is already signed off, so there is nothing "
                     f"to {verb}")


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
        if schema.session_is_waiting(payload.get("session")):
            _die_waiting(base, payload["session"], "")
        die(f"a session is already open at {base} — that tab is the live "
            f"review. Finish it there, or `loop.py abandon`, before "
            f"starting another.")
    # `server_url` is None for an empty file, which is still a collision.
    where = f" ({base})" if base else ""
    record = _dead_session(viva)
    if record is not None:
        die(f"{viva}/server.url exists but nothing is answering{where} — "
            f"{_session_summary(record)} lost its server here. `loop.py start "
            f"--target <PR> --join-session` relaunches it for its PR and clears "
            f"the file itself; to end the session, delete the file, then "
            f"`loop.py abandon`. Deleting it alone keeps the session waiting.")
    die(f"{viva}/server.url exists but nothing is answering{where} — a "
        f"prior session was killed without cleaning up. Delete the file, "
        f"then re-run.")


def _dead_session(viva: Path) -> dict | None:
    """The session record whose gate this `.viva/` held, or None — read
    leniently, since a preflight must still name the stale file."""
    path = _session_path(viva)
    if path is None or not path.exists():
        return None
    try:
        record = _load_session(path)
    except (OSError, ValueError):
        return None
    return record if _owns(record, viva) else None


def _clear_state(viva: Path, keep_server_url: bool = False,
                 include_answers: bool = False,
                 include_attachments: bool = True) -> None:
    """The session state clear (CLAUDE.md, State lifecycle). `preferences.json`
    is the one survivor. `start --handoff` keeps `server.url` (the interview
    server) and `attachments/` (the answers may cite files there); `interview`
    adds `answers.json`, which `start` must never touch."""
    for p in list(viva.glob(schema.round_input_glob())) + list(viva.glob(schema.round_output_glob())):
        p.unlink()
    # `decisions.json` is this session's snapshot (#211) — the durable copy is
    # the ledger's `### Decisions` block, written at `finish`, so it resets
    # like everything else here.
    names = ["open-notes.json", "target.json", "diff.patch", "decisions.json"]
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


def _launch_server(viva: Path, mode: str, inp: Path, out: Path,
                   session_id: str | None = None) -> str:
    """Launch `server.py` detached; return its base URL once `server.url`
    appears. Streams go to `.viva/server.log`, not inherited stdout — an
    inherited pipe would hang a caller until the grandchild exits."""
    log = viva / "server.log"
    with log.open("wb") as logfh:
        proc = subprocess.Popen(
            [str(sys.executable), str(SERVER), "--mode", mode,
             "--input", str(inp), "--output", str(out),
             *(["--session-id", session_id] if session_id else [])],
            stdout=logfh, stderr=logfh,
        )
    for _ in range(_POLL_TRIES):
        if (viva / "server.url").exists():
            break
        if proc.poll() is not None:
            # Last line of the log: the headless contract's one-line error shape.
            tail = log.read_text(encoding="utf-8").strip().splitlines()
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


def _run_recheck_drift(round_file: Path) -> int:
    """A recheck's (#83) one producer: `drift.py`, merged, then `recheck.py`
    withdraws the seeded approval per flag. Returns the count withdrawn —
    computed from `approved_ids` before/after, not by parsing either
    script's printed line, since that's for a human, not this comparison."""
    before = set(load_json(round_file).get("approved_ids") or [])
    drift = run([sys.executable, SCRIPTS / "drift.py", "--input", round_file],
                capture_output=True, text=True)
    if drift.returncode != 0:
        die(f"drift producer failed:\n{drift.stderr}")
    run_stdin_or_die(
        [sys.executable, SCRIPTS / "annotate.py",
         "--input", round_file, "--annotations", "-"],
        drift.stdout, "drift merge",
        f"{round_file} may be partially annotated; fix and re-run.")
    run_or_die([sys.executable, SCRIPTS / "recheck.py", "--input", round_file],
               "recheck", f"{round_file} is unchanged on failure.")
    after = set(load_json(round_file).get("approved_ids") or [])
    return len(before - after)


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
def _classify(target: str | None, kind: str | None) -> dict:
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


def _target_record(viva: Path) -> tuple[dict, Path]:
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
        join = " --join-session" if _joined(viva) else ""
        return f"`loop.py start --target {record.get('number')} --kind pr{join}`{repo}"
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


# ── the session record (#240) ─────────────────────────────────────────────────
_ORIGIN_RE = re.compile(
    r"github\.com[:/](?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")
_COMMENT_URL_RE = re.compile(
    r"^https://github\.com/(?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)"
    r"/(?:issues|pull)/\d+#issuecomment-(?P<id>\d+)$")
_COMMIT_SOURCE_RE = re.compile(r"^commit:(?P<path>[^@]+)@(?P<rev>[^@-][^@]*)$")


def _git(cwd: Path, *argv) -> subprocess.CompletedProcess:
    return run(["git", "-C", cwd, *argv], capture_output=True, text=True)


def _session_path(viva: Path) -> Path | None:
    """`<git common dir>/viva/session.json` — one per clone, shared by every
    worktree of it. None outside a git repository."""
    root = viva.resolve().parent
    try:
        proc = _git(root, "rev-parse", "--git-common-dir")
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    # Relative in the main worktree, absolute in a linked one.
    return (root / proc.stdout.strip()).resolve() / "viva" / "session.json"


def _load_session(path: Path) -> dict:
    """Raises OSError/ValueError on a record that won't read or validate."""
    record = load_json(path)
    schema.validate_session(record)
    return record


def _read_session(viva: Path) -> tuple[Path | None, dict | None]:
    path = _session_path(viva)
    if path is None or not path.exists():
        return path, None
    try:
        return path, _load_session(path)
    except (OSError, ValueError) as e:
        die(f"invalid session record {path}: {e}. Delete it to discard the "
            f"session.")
    return path, None  # unreachable; die() raises


def _write_session(path: Path, record: dict) -> None:
    schema.validate_session(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema.atomic_write(path, json.dumps(record, indent=2) + "\n")


def _owns(record: dict | None, viva: Path) -> bool:
    """Is `viva` the `.viva/` whose epoch holds the session's live gate? The
    record is per clone; a round in another worktree must not move it."""
    return record is not None and Path(record["viva_dir"]) == viva.resolve()


def _gate(record: dict, kind: str) -> str:
    return next(g["state"] for g in record["gates"] if g["kind"] == kind)


def _close_gate(record: dict, kind: str, opens: str | None = None) -> None:
    for g in record["gates"]:
        if g["kind"] == kind:
            g["state"] = "done"
        elif g["kind"] == opens:
            g["state"] = "live"


def _origin_repo(root: Path) -> str:
    proc = _git(root, "remote", "get-url", "origin")
    m = _ORIGIN_RE.search(proc.stdout.strip()) if proc.returncode == 0 else None
    if not m:
        die("--session needs a GitHub `origin` remote — a session ends at a "
            "PR's sign-off")
    return m.group("repo")


def _pr_ref(target: dict, repo: str) -> str | None:
    """A diff target as the record's `pr` form, `owner/repo#N`; None unless a PR."""
    if target.get("kind") != "pr":
        return None
    return f"{target.get('repo') or repo}#{target.get('number')}"


def _session_state(record: dict | None) -> str:
    """What a PR review may do with this clone's session: `none`, `open`
    (intake or spec live), `unsourced`, `waiting` (joinable), or `joined`."""
    if record is None:
        return "none"
    if _gate(record, "diff") == "live":
        return "joined"
    if _gate(record, "spec") != "done":
        return "open"
    return "waiting" if "spec" in record else "unsourced"


def _session_summary(record: dict) -> str:
    """One line naming the session: its id, spec source, and PR if joined."""
    spec = record.get("spec")
    if spec is None:
        source = "no spec source recorded"
    elif spec["kind"] == "commit":
        source = f"spec {spec['path']}@{spec['sha'][:12]}"
    else:
        source = f"spec {spec['url']}"
    gates = ", ".join(f"{g['kind']} {g['state']}" for g in record["gates"])
    pr = f" · {record['pr']}" if record.get("pr") else ""
    return f"session {record['id']} · {record['repo']} · {gates} · {source}{pr}"


def _serves(payload: dict | None, record: dict) -> bool:
    """Is this probed `/input` the session's own server, by id?"""
    served = (payload or {}).get("session")
    return isinstance(served, dict) and served.get("id") == record["id"]


def _joined(viva: Path) -> dict | None:
    """The session record whose diff gate is THIS `.viva/`'s PR round, or
    None. Only `--join-session` stamps `session` into target.json, so a plain
    review of the same PR in the same dir never counts (#242)."""
    path = _session_path(viva)
    if path is None or not path.exists():
        return None
    try:
        record, target = _load_session(path), load_json(viva / "target.json")
    except (OSError, ValueError):
        return None
    if not (_owns(record, viva) and _gate(record, "diff") == "live"):
        return None
    if target.get("session") != record["id"]:
        return None
    return record if record.get("pr") == _pr_ref(target, record["repo"]) else None


def _join_target(viva: Path, target: dict
                 ) -> tuple[Path, dict, Path, dict, Callable[[], None]]:
    """Refuse a join that could land on the wrong session or PR (#242), then
    pick the `.viva/` the diff gate runs in: a waiting server's own (its
    output root is fixed at launch), else this one, where it relaunches.
    Returns (that dir, the pinned target, the record's path, the record, and
    the clear to run once a capture holds a round)."""
    if target.get("kind") != "pr":
        die(f"--join-session joins a PR; {target.get('label')} is a "
            f"{target.get('kind')} diff, which never joins a session")
    path, record = _read_session(viva)
    if record is None:
        die("--join-session: no session in this clone — `loop.py interview "
            "--session` opens one")
    state = _session_state(record)
    if state == "open":
        die(f"{_session_summary(record)} — its spec is not signed off, so no "
            f"PR can join it yet")
    if state == "unsourced":
        die(f"{_session_summary(record)} — record the signed spec first: "
            f"after the stamp, `loop.py session --spec-source <commit:path@sha "
            f"| comment URL>`")
    repo = target.get("repo") or record["repo"]
    if repo.lower() != record["repo"].lower():
        die(f"{target.get('label')} is in {repo}, but session {record['id']} "
            f"is {record['repo']} — a PR joins only its own repo's session")
    number = target["number"]
    pr = f"{record['repo']}#{number}"
    if record.get("pr") and record["pr"] != pr:
        die(f"{_session_summary(record)} — it is joined to {record['pr']}, "
            f"not {pr}; one PR per session")
    # Pinned to the session's repo: a bare number otherwise resolves against
    # whatever `gh` defaults to, which in a fork is the upstream.
    target = dict(target, repo=record["repo"], label=f"PR #{number} ({record['repo']})",
                  capture=["gh", "pr", "diff", str(number), "--repo", record["repo"]],
                  session=record["id"])

    owner = Path(record["viva_dir"])
    base = server_url(owner)
    payload = probe_input(base, timeout=_PREFLIGHT_TIMEOUT) if base else None
    if _serves(payload, record):
        if not schema.session_is_waiting(payload["session"]):
            die(f"{_session_summary(record)} — its diff gate is already live "
                f"at {base}; that tab is the review")
        # The spec's round files go; the server, its url, and the intake's
        # attachments stay, as at `--handoff`.
        return owner, target, path, record, functools.partial(
            _clear_state, owner, keep_server_url=True, include_attachments=False)
    if owner == viva.resolve() and base and payload is None:
        # The session's own server died without cleanup; the probe just said
        # nothing is home, so the relaunch clears its url itself.
        (viva / "server.url").unlink(missing_ok=True)
    _preflight_no_live_session(viva)
    return viva.resolve(), target, path, record, functools.partial(_clear_state, viva)


def _spec_source(viva: Path, ref: str, repo: str) -> dict:
    """Resolve `--spec-source` to the record's `spec`, refusing a source that
    carries no sign-off: the minutes read the ledger back from it."""
    m = _COMMIT_SOURCE_RE.match(ref)
    if m:
        # Canonical repo-root form: #245 reads the path back from the record.
        path = re.sub(r"^(\./)+", "", m.group("path"))
        root = viva.resolve().parent
        proc = _git(root, "rev-parse", "--verify", m.group("rev") + "^{commit}")
        if proc.returncode != 0:
            die(f"{m.group('rev')!r} is not a commit in this clone")
        sha = proc.stdout.strip()
        shown = _git(root, "show", f"{sha}:{path}")
        if shown.returncode != 0:
            die(f"{path} is not in {sha[:12]} — pass the repo-root path of the "
                f"signed spec")
        text, spec = shown.stdout, {"kind": "commit", "path": path, "sha": sha}
    else:
        m = _COMMENT_URL_RE.match(ref)
        if not m:
            die(f"--spec-source takes `commit:<path>@<sha>` or an issue comment "
                f"URL, not {ref!r}")
        if m.group("repo").lower() != repo.lower():
            die(f"the spec comment is in {m.group('repo')}; this session is "
                f"{repo}")
        argv = ["gh", "api", f"repos/{m.group('repo')}/issues/comments/{m.group('id')}"]
        try:
            proc = run(argv, capture_output=True, text=True)
        except FileNotFoundError:
            die("--spec-source with a comment URL needs `gh` on PATH")
        if proc.returncode != 0:
            die(f"{' '.join(argv)} failed: {proc.stderr.strip()}")
        try:
            comment = json.loads(proc.stdout)
            text, updated = comment["body"], comment["updated_at"]
            if not isinstance(text, str) or not isinstance(updated, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            die(f"{' '.join(argv)} returned no comment body")
        spec = {"kind": "comment", "url": ref, "updated_at": updated}
    if not schema.has_revision_history(text):
        die(f"{ref} carries no `## Revision History` — record the source the "
            f"stamp produced, after the stamp")
    return spec


# ── subcommands ───────────────────────────────────────────────────────────────
def cmd_interview(args) -> int:
    """Run the Q&A gate (`references/qa.md`): clear, launch `--mode qa`
    (`--mode session` with `--session`), block for answers, print them. Never
    `/complete` — `start --handoff` + `arm` ends the interview instead."""
    viva = Path(args.viva_dir)
    qa_in = Path(args.input)
    if not qa_in.exists():
        die(f"qa-input not found: {qa_in}")
    if args.session:
        session_path, record = _read_session(viva)
        if session_path is None:
            die("--session needs a git repository — the record lives in its "
                "common dir")
        if record:
            die(f"session {record['id']} is already open in this clone "
                f"({record['viva_dir']}); one per clone. `loop.py abandon` "
                f"ends it.")
        repo = _origin_repo(viva.resolve().parent)
    _preflight_no_live_session(viva)
    viva.mkdir(parents=True, exist_ok=True)
    answers = viva / "answers.json"
    # A stale `answers.json` would satisfy the wait below with no answer.
    _clear_state(viva, include_answers=True)
    # Minted before the launch: a `--mode session` server serves its id.
    session_id = uuid.uuid4().hex if args.session else None
    base = _launch_server(viva, "session" if args.session else "qa", qa_in,
                          answers, session_id)
    if args.session:
        record = {"id": session_id, "repo": repo,
                  "viva_dir": str(viva.resolve()),
                  "gates": [{"kind": k, "state": "live" if k == "intake" else "waiting"}
                            for k in schema.SESSION_GATE_KINDS]}
        _write_session(session_path, record)
        print(f"viva-loop: session {record['id']} · {repo}", flush=True)
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
    text = answers.read_text(encoding="utf-8")
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
    if args.recheck and diff_form:
        die("--recheck re-opens a signed DOC — a diff review (--target/--kind) "
            "has no ledger to recheck against")
    if args.recheck and args.handoff:
        die("--recheck re-opens a doc already signed off; --handoff hands a "
            "fresh draft to an interview still in progress — the two describe "
            "opposite states of the same doc")
    if args.join_session and not diff_form:
        die("--join-session joins a PR's diff to a session — pass "
            "--target <pr>, not a doc")
    if diff_form:
        record = _classify(args.target, args.kind)
        if record.get("kind") != "doc":
            return _start_diff(args, viva, record)
        if args.join_session:
            die(f"--join-session joins a PR's diff to a session; {record['doc']} "
                f"is a doc")
        # A `--target` naming a markdown file is the doc form spelled the
        # other way — filesystem first, then shape (review_target.py).
        args.doc = record["doc"]
    return _start_doc(args, viva)


def _start_diff(args, viva: Path, record: dict) -> int:
    for flag, value in (("--split-on", args.split_on), ("--type", args.doc_type),
                        ("--pass", args.pass_kind)):
        if value is not None:
            die(f"{flag} is a doc-review flag; {record.get('label')} is "
                f"reviewed hunk by hunk")
    if args.join_session:
        return _start_join(args, viva, record)
    _preflight_no_live_session(viva)
    _, waiting = _read_session(viva)
    if waiting is not None:
        hint = ("; if this PR implements its spec, re-run with --join-session"
                if record.get("kind") == "pr" and _session_state(waiting) == "waiting"
                else "")
        print(f"viva-loop: {_session_summary(waiting)} — this review does "
              f"not join it{hint}")
    # No resume branch and no preference seam: neither has hunk semantics.
    _clear_state(viva)
    data = _capture_round(viva, record)
    if data is None:
        return 0
    return _diff_seam_or_arm(args, 1, schema.round_file_paths(viva, 1)[0], data)


def _start_join(args, viva: Path, record: dict) -> int:
    """`start --join-session`: capture and parse aside, so a failed or empty
    capture leaves the session's `.viva/` as it was; only a round clears it."""
    viva, record, session_path, session, clear = _join_target(viva, record)
    stage = Path(tempfile.mkdtemp(prefix="viva-join-"))
    try:
        data = _capture_round(stage, record)
    except SystemExit:
        # A parse failure's error names the staged patch; keep it only then.
        if not (stage / "diff.patch").exists():
            shutil.rmtree(stage)
        raise
    if data is None:
        shutil.rmtree(stage)
        return 0
    clear()
    viva.mkdir(parents=True, exist_ok=True)
    for f in stage.iterdir():
        shutil.move(str(f), str(viva / f.name))
    stage.rmdir()
    if viva != Path(args.viva_dir).resolve():
        print(f"viva-loop: this diff gate runs in {viva} — every later "
              f"loop.py command takes the flag before its subcommand: "
              f"`loop.py --viva-dir {viva} wait`")
    args.viva_dir = viva
    # Written before the arm: `arm` reads it to relaunch `--mode session`,
    # and a failed arm is retried by the same join.
    _close_gate(session, "spec", opens="diff")
    session.update(pr=_pr_ref(record, session["repo"]), viva_dir=str(viva))
    _write_session(session_path, session)
    print(f"viva-loop: {_session_summary(session)} — diff gate joined")
    return _diff_seam_or_arm(args, 1, schema.round_file_paths(viva, 1)[0], data)


def _capture_round(viva: Path, record: dict) -> dict | None:
    """Save the target, capture its patch, and parse diff round 1 into
    `viva`; the round, or None when the capture is empty."""
    viva.mkdir(parents=True, exist_ok=True)
    cwd = Path.cwd().resolve()
    # Record plus its cwd: `rearm`/`finish` re-capture from a later shell
    # whose cwd this driver does not control.
    (viva / "target.json").write_text(
        json.dumps(dict(record, cwd=str(cwd)), indent=2) + "\n", encoding="utf-8")
    size = _capture(record, viva / "diff.patch", cwd)
    if size == 0:
        print(f"viva-loop: no changes to review — {record.get('label')}")
        return None
    round_file = schema.round_file_paths(viva, 1)[0]
    # A non-empty patch with no hunks is a real failure: `parse_diff.py`
    # exits 1 on it and this dies rather than completing.
    run_or_die([sys.executable, SCRIPTS / "parse_diff.py", viva / "diff.patch",
                "--output", round_file, "--round", "1",
                "--doc-file", record.get("label") or "working tree"],
               "parse", f"The patch is at {viva / 'diff.patch'}.")
    data = load_json(round_file)
    print(f"viva-loop: {len(data.get('sections', []))} hunk(s) · "
          f"{record.get('label')}")
    return data


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
    # A recheck skips the plain resume branch outright — the two both fire on
    # `has_revision_history`, and running the ledger seed (below) and the
    # prior-pair carry together would be two mechanisms deciding the same
    # thing. `--recheck` alone answers "was this signed off" here.
    if not args.handoff and not args.recheck and schema.has_revision_history(doc.read_text(encoding="utf-8")):
        n = current_round(viva)
        if n:
            src_in, src_out = schema.round_file_paths(viva, n)
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
    if prior_in and prior_out:
        cmd += ["--prior-input", prior_in, "--prior-verdicts", prior_out]
    if args.recheck:
        cmd += ["--recheck"]
    try:
        if run(cmd).returncode != 0:
            die("parse failed")
    finally:
        # One resume only — else the next `start` reads a stale pair.
        if prior_in:
            prior_in.unlink(missing_ok=True)
            prior_out.unlink(missing_ok=True)

    round_file = round1_input
    if args.handoff:
        session_path, record = _read_session(viva)
        if _owns(record, viva) and _gate(record, "intake") == "live":
            _close_gate(record, "intake", opens="spec")
            _write_session(session_path, record)
    if bundle:
        # The type's check set is RUN here, before any branch that could arm
        # — a check the driver doesn't run here never runs.
        checks = ", ".join(bundle.get("checks") or []) or "none"
        print(f"viva-loop: doc type {bundle['name']} · checks: {checks}")
        if bundle.get("checks"):
            merged = _run_bundle_checks(bundle, round_file)
            print(f"viva-loop: checks run: {checks} · {merged} flag(s) merged")

    if args.recheck:
        # The driver runs the producer itself, same shape as `_run_bundle_checks`
        # above: between parse and any branch that could arm.
        withdrawn = _run_recheck_drift(round_file)
        if withdrawn == 0:
            print("viva-loop: recheck — drift found nothing against the doc's "
                  "own references; nothing to re-certify")
            return 0
        print(f"viva-loop: recheck — drift withdrew approval from "
              f"{withdrawn} section(s)")

    if args.parse_only:
        return _seam_stop(1, round_file, "--parse-only")
    # A standing preference auto-engages the preference producer, an LLM
    # pass — the agent's work, not the driver's.
    prefs = standing_preferences(viva)
    if prefs and not args.arm_anyway:
        return _seam_stop(1, round_file,
                          f"{len(prefs)} standing preference(s) in play")
    return cmd_arm(args)


def _snapshot_decisions(viva: Path, round_file: Path) -> None:
    """After a merge, capture every `decision`-kind flag (#211) from
    `round_file` into `.viva/decisions.json`, keyed by `schema.section_key`.

    A round file itself can't be the durable copy: annotations carry forward
    only onto a byte-identical section (`parse_sections._carry_identical`),
    so a decision on a section a later round rewrites would otherwise vanish.
    `cmd_rearm`'s `_reapply_decisions` re-merges from this store before
    arming the next round. Additive — never deletes an entry no longer
    present in `round_file` — since a decision's flag is only ever emitted
    once, at hand-off."""
    data = load_json(round_file)
    store_path = viva / "decisions.json"
    store = {}
    if store_path.exists():
        try:
            store = json.loads(store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            store = {}
    changed = False
    for section in data.get("sections", []):
        flags = [a for a in section.get("annotations", []) or []
                 if isinstance(a, dict) and a.get("kind") == schema.DECISION_KIND]
        if not flags:
            continue
        key = schema.section_key(section.get("title", ""))
        store[key] = {"title": section.get("title", ""), "flags": flags}
        changed = True
    if changed:
        store_path.write_text(json.dumps(store, indent=2, ensure_ascii=False),
                              encoding="utf-8")


def _reapply_decisions(viva: Path, round_file: Path) -> None:
    """Re-merge `.viva/decisions.json` onto `round_file` by `section_key`,
    before it arms — the parser's byte-identical carry drops a decision
    annotation from any section the rewrite touched, and `section_key` is the
    identity a decision travels on, not the section's (re-assigned) id.
    No-op absent a store; idempotent, same as any `annotate.py` merge."""
    store_path = viva / "decisions.json"
    if not store_path.exists():
        return
    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    data = load_json(round_file)
    sidecar = []
    for section in data.get("sections", []):
        entry = store.get(schema.section_key(section.get("title", "")))
        if not entry:
            continue
        for flag in entry.get("flags", []):
            sidecar.append({**flag, "id": section["id"]})
    if not sidecar:
        return
    sidecar_path = viva / "decisions-sidecar.json"
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    try:
        run_or_die([sys.executable, SCRIPTS / "annotate.py",
                    "--input", round_file, "--annotations", sidecar_path],
                   "annotate (decisions)",
                   f"{round_file} may be partially annotated; fix and re-run.")
    finally:
        sidecar_path.unlink(missing_ok=True)


def cmd_annotate(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to annotate — run `loop.py start` first")
    inp, _ = schema.round_file_paths(viva, n)
    # Annotate is PRE-ARM only: the server loads its round once from
    # `/next-round`, so annotating an already-armed round writes a file
    # nobody re-reads (loud failure here beats a silent one at `/complete`).
    base = server_url(viva)
    if base and holds_round(base, inp, n):
        die(f"round {n} is already armed — the server at {base} holds it in "
            f"memory and would never see this merge. Annotate before arming: "
            f"finish or `rearm --parse-only` this round, annotate the next one, "
            f"then `loop.py arm`.")
    # The agent names its sidecar, the driver names the file; '-' reads stdin.
    run_or_die([sys.executable, SCRIPTS / "annotate.py",
                "--input", inp, "--annotations", args.sidecar],
               "annotate",
               f"Fix the sidecar and re-run; {inp} is unchanged on failure.")
    _snapshot_decisions(viva, inp)
    print(f"viva-loop: round {n} annotated · {inp}")
    return 0


def cmd_arm(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no round to arm — run `loop.py start` first")
    inp, out = schema.round_file_paths(viva, n)

    # Branch on liveness, not the round number — a re-run after a slow start
    # would otherwise launch a second orphaned server.
    # Liveness is `probe_input`, never `holds_round`: a live qa server has no
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
    # A session's PR diff relaunches the session, not a standalone diff server:
    # booted on a diff, it opens with intake and spec done (#242).
    session = _joined(viva) if mode == "diff" else None
    if session:
        mode = "session"
    base = _launch_server(viva, mode, inp, out, session["id"] if session else None)
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
    inp, _ = schema.round_file_paths(viva, n)
    # Pre-arm, for the reason `annotate` is: the server reads its round once.
    base = server_url(viva)
    if base and holds_round(base, inp, n):
        die(f"round {n} is already armed — the server at {base} holds it in "
            f"memory and would never see this merge. Summarize before arming.")
    try:
        raw = sys.stdin.read() if args.map == "-" else Path(args.map).read_text(encoding="utf-8")
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
    schema.atomic_write(inp, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"viva-loop: round {n} summarized · {len(summaries)} of {len(by_id)} "
          f"hunk(s) · {inp}")
    return 0


def cmd_wait(args) -> int:
    viva = Path(args.viva_dir)
    n = current_round(viva)
    if not n:
        die("no armed round to wait on")
    inp, out = schema.round_file_paths(viva, n)
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
        payload = probe_input(base)
        if payload is None:
            die(f"server at {base} is not answering and round {n} never "
                f"returned verdicts. Delete {viva}/server.url, then relaunch. "
                f"{relaunch}", 2)
        if not schema.serves_round(payload, input_data, n):
            # Parsed but never armed (`rearm --parse-only`, or a diff round
            # beside a waiting session's spec round): no verdicts will land.
            served = f"{payload.get('mode') or 'review'} round {payload['round']}" \
                if "round" in payload else "no round"
            die(f"round {n} is parsed but not armed — the server is still "
                f"serving {served}. Run `loop.py arm` (after "
                f"`loop.py annotate` if a producer is pending).", 2)
        time.sleep(_WAIT_INTERVAL)

    verdicts = load_json(out)

    print(json.dumps(verdicts, indent=2, ensure_ascii=False))
    print("=== id -> title ===")
    for s in input_data.get("sections", []):
        print(f"{s.get('id')}\t{s.get('title')}")
    print("=== standing preferences ===")
    print(json.dumps(standing_preferences(viva), ensure_ascii=False))

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
    inp, out = schema.round_file_paths(viva, n)
    if not out.exists():
        die(f"round {n} has no verdicts yet — run `loop.py wait` first")
    _refuse_between_gates(viva, "re-arm")

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
    # A per-session fact, not a per-round decision like `pass` — a recheck
    # stays a recheck for the life of the session, or round 2's finishing
    # ledger would silently read "Signed off" instead of "Re-certified".
    recheck = bool(round_data.get("recheck"))
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
    else:
        next_pass = dict(prior_pass) if isinstance(prior_pass, dict) else None
        if next_pass is not None and not next_pass.get("kind"):
            die(f"round {n}'s input carries a pass with no kind — fix {inp}, or "
                f"name this round's pass with --pass")

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

    nxt_in, _ = schema.round_file_paths(viva, n + 1)
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
    if recheck:
        cmd += ["--recheck"]
    run_or_die(cmd, "re-parse", f"The running server still holds round {n}.")
    # A decision (#211) carries by `section_key`, not by the parser's
    # byte-identical carry — re-merge it before anything downstream reads
    # this round file.
    _reapply_decisions(viva, nxt_in)

    # Round 2+ producer seam. Order is load-bearing: flags must merge before
    # the round ships to the running server.
    if args.parse_only:
        return _seam_stop(n + 1, nxt_in, "--parse-only")
    return cmd_arm(args)


def _rearm_diff(args, viva: Path, n: int, inp: Path, out: Path,
                round_data: dict) -> int:
    if args.response or args.decline or args.pass_kind is not None:
        die("a diff round carries no threads and no pass — --response, "
            "--decline, and --pass apply to doc review only")
    record, cwd = _target_record(viva)
    # The SAME capture as round 1, never a substitute — else a later round of
    # a PR review would silently review the working tree instead.
    size = _capture(record, viva / "diff.patch", cwd)
    if size == 0:
        # Not a sign-off: `finish` re-captures for itself before completing.
        print("viva-loop: diff is empty after re-capture — nothing to re-arm; "
              "`loop.py finish` signs it off")
        return 0
    nxt_in, _ = schema.round_file_paths(viva, n + 1)
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
    inp, out = schema.round_file_paths(viva, n)
    if not out.exists():
        die(f"round {n} has no verdicts yet — nothing to finish")

    input_data, verdicts = load_json(inp), load_json(out)
    # Validated here, not assumed: every other read boundary (parse_sections.py
    # on write, server.py on read) validates, and finish is the one path that
    # reads both round files straight off disk with neither in between — a
    # hand-edited or corrupted file must fail loudly here, not feed a bad
    # `sections` shape into round_is_complete or the `or []` tails below.
    try:
        schema.validate_review_input(input_data)
        schema.validate_verdicts(verdicts)
    except ValueError as e:
        die(f"invalid round {n} files: {e}")
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

    session_path, record = _read_session(viva)
    base = server_url(viva)
    if not base:
        die(f"no live server to complete (no {viva}/server.url). The verdicts "
            f"are on disk; append the ledger by hand with `python3 "
            f"{SCRIPTS / 'revision_history.py'} --viva-dir {viva} --doc {doc}`.")
    _refuse_between_gates(viva, "finish")

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
    if _owns(record, viva) and _gate(record, "spec") == "live":
        _close_gate(record, "spec")
        _write_session(session_path, record)
        print(f"viva-loop: session {record['id']} · spec gate closed — the "
              f"server stays up for the diff gate · after the stamp, `loop.py "
              f"session --spec-source <commit:path@sha | comment URL>`")
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
    session_path, session = _read_session(viva)
    # Only the joined gate's own `.viva/` ends the session — a standalone
    # review of the same PR elsewhere does not.
    closes = _joined(viva) is not None
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
        _end_session(session_path, session, closes)
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
    _end_session(session_path, session, closes)
    return 0


def _end_session(path: Path | None, record: dict | None, closes: bool) -> None:
    """The diff gate's sign-off ends the session; its record goes with it."""
    if closes:
        path.unlink(missing_ok=True)
        print(f"viva-loop: session {record['id']} signed off — record removed")


def cmd_abandon(args) -> int:
    viva = Path(args.viva_dir)
    if args.keep_session:
        return _stop_waiting_server(viva)
    session_path, record = _session_path(viva), None
    if session_path is not None and session_path.exists():
        try:
            record = _load_session(session_path)
        except (OSError, ValueError) as e:
            session_path.unlink()
            warn(f"removed an invalid session record {session_path}: {e}")
    base = server_url(viva)
    if not base:
        if record is None:
            die(f"no live session to abandon (no {viva}/server.url)")
        return _abandon_stale_session(viva, session_path, record)

    # Probed before the stop: only the session's own server ends it, not a
    # standalone review reusing its `.viva/` after `--keep-session`.
    ends = _owns(record, viva) and _serves(
        probe_input(base, timeout=_PREFLIGHT_TIMEOUT), record)
    _stop_server(viva, base)
    n = current_round(viva)
    where = f" at round {n}" if n else ""
    print(f"viva-loop: session abandoned{where} — the doc was NOT signed off.")
    if ends:
        session_path.unlink(missing_ok=True)
        print(f"viva-loop: session {record['id']} ended — record removed")
    return 0


def _stop_server(viva: Path, base: str) -> None:
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


def _stop_waiting_server(viva: Path) -> int:
    """`abandon --keep-session`: stop a session server idling between gates,
    record untouched, for its PR's join to relaunch. Refused with a gate live,
    where the kept record would claim a gate nothing serves."""
    base = server_url(viva)
    payload = probe_input(base, timeout=_PREFLIGHT_TIMEOUT) if base else None
    session = (payload or {}).get("session")
    if not schema.session_is_waiting(session):
        die("--keep-session stops only a session server waiting between gates "
            "for its implementing PR, and none is answering here. Plain "
            "`loop.py abandon` ends an unfinished session.")
    _stop_server(viva, base)
    print(f"viva-loop: server stopped — session {session.get('id')} kept for "
          f"its implementing PR; `/viva-review <PR>` reopens it.")
    return 0


def _abandon_stale_session(viva: Path, path: Path, record: dict) -> int:
    """No server here, but a session record: end it, unless another
    worktree's server is still serving it."""
    owner = Path(record["viva_dir"])
    if not _owns(record, viva):
        live = server_url(owner)
        if live and probe_input(live, timeout=_PREFLIGHT_TIMEOUT) is not None:
            die(f"session {record['id']} is live at {live} — abandon it from "
                f"there: `loop.py --viva-dir {owner} abandon`")
    path.unlink(missing_ok=True)
    print(f"viva-loop: session {record['id']} ended — record removed; no gate "
          f"was signed off by this.")
    return 0


def cmd_session(args) -> int:
    viva = Path(args.viva_dir)
    path, record = _read_session(viva)
    if args.spec_source is None:
        # Read-only: the classification line a PR review routes its join on.
        if record is not None:
            print(f"viva-loop: {_session_summary(record)}")
        print(f"=== session: {_session_state(record)} ===")
        return 0
    if record is None:
        die("no session in this clone — `loop.py interview --session` opens one")
    if _gate(record, "spec") != "done":
        die(f"session {record['id']}'s spec gate is {_gate(record, 'spec')} — "
            f"`loop.py finish` and stamp it before recording its source")
    record["spec"] = _spec_source(viva, args.spec_source, record["repo"])
    _write_session(path, record)
    print(f"viva-loop: session {record['id']} · spec source recorded "
          f"({record['spec']['kind']})")
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
    p.add_argument("--session", action="store_true",
                   help="open a lifecycle session (#239): write its record to "
                        "the git common dir, shared by every worktree, where it "
                        "survives the state clear until the diff gate signs off "
                        "or `abandon` ends it")
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
    p.add_argument("--recheck", action="store_true",
                   help="re-certification (#83): re-open a SIGNED doc "
                        "(`--doc` only), seed every section approved from its "
                        "own `## Revision History`, run drift.py, and withdraw "
                        "approval per flag before the round arms. Refuses on "
                        "an unsigned doc, and skips the plain resume branch "
                        "(one mechanism decides 'was this signed off').")
    p.add_argument("--join-session", action="store_true",
                   help="arm this PR's diff as the diff gate of the clone's "
                        "waiting session (#242): into its live server, or a "
                        "relaunched `--mode session` one. Refused for a PR in "
                        "another repo, a session already joined to another "
                        "PR, one with no recorded spec source, and any "
                        "non-PR target. Never inferred: without it a PR "
                        "review leaves the session alone.")
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
    p.add_argument("--keep-session", action="store_true",
                   help="stop a session server waiting between gates but keep "
                        "the session record for its implementing PR, freeing "
                        "this worktree for another review")
    p.set_defaults(func=cmd_abandon)

    p = sub.add_parser("session", help="print the clone's session and its "
                                       "state; with --spec-source, record the "
                                       "signed spec's source after the stamp")
    p.add_argument("--spec-source", default=None, metavar="REF",
                   help="what the stamp produced: `commit:<path>@<sha>` (path "
                        "from the repo root) or an issue comment URL. Refused "
                        "before the spec gate closes, or on a source with no "
                        "`## Revision History`. Omitted: print the session and "
                        "one line, `=== session: none|open|unsourced|waiting|"
                        "joined ===`.")
    p.set_defaults(func=cmd_session)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
