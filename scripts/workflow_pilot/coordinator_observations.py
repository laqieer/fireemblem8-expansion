"""Bounded observations for a trusted coordinator, not an authentication service."""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import re
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts.workflow_pilot import raw_diff_check as raw_git


MAX_JSON_BYTES = 1024 * 1024
MAX_NODES = 32768
MAX_DEPTH = 24
MAX_STRING = 16384
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ROOT = Path(__file__).resolve().parents[2]


class ObservationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ObservationError(message)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value):
    require(isinstance(value, str) and TIME_RE.fullmatch(value), "invalid UTC timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ObservationError("invalid UTC timestamp") from error


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(len(key) <= MAX_STRING, "JSON key limit exceeded")
        require(key not in result, f"duplicate JSON key: {key[:80]}")
        result[key] = value
    return result


def check_tree(value, *, string_limit=MAX_STRING):
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(depth <= MAX_DEPTH and count <= MAX_NODES, "JSON depth/node limit exceeded")
        require(type(item) in (dict, list, str, int, float, bool, type(None)),
                "not a JSON value")
        if isinstance(item, str):
            require(len(item) <= string_limit, "JSON string limit exceeded")
            require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), "invalid Unicode scalar")
        elif isinstance(item, float):
            require(math.isfinite(item), "nonfinite JSON number")
        elif type(item) is int:
            require(abs(item) <= 2**63 - 1, "JSON integer limit exceeded")
        elif isinstance(item, dict):
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def _decode_bytes(raw, string_limit):
    require(type(raw) is bytes, "public JSON input must be bytes")
    require(len(raw) <= MAX_JSON_BYTES, "JSON input exceeds 1 MiB")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _value: require(False, "nonfinite JSON number"))
        return check_tree(value, string_limit=string_limit)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ObservationError(f"invalid bounded JSON: {error}") from error


def parse_bytes(raw):
    return _decode_bytes(raw, MAX_STRING)


def json_bytes(value):
    check_tree(value)
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, sort_keys=True,
                               separators=(",", ":"))
    output = bytearray()
    for chunk in encoder.iterencode(value):
        output.extend(chunk.encode("ascii"))
        require(len(output) < MAX_JSON_BYTES, "JSON output exceeds 1 MiB")
    return bytes(output) + b"\n"


def read_bytes(path, *, limit=MAX_JSON_BYTES):
    path = Path(path).absolute()
    with raw_git._directory_fd(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode), "input must be a nofollow regular file")
            require(before.st_size <= limit, "input exceeds byte limit")
            chunks = bytearray()
            while len(chunks) <= limit:
                part = os.read(fd, min(65536, limit + 1 - len(chunks)))
                if not part:
                    break
                chunks.extend(part)
            after = os.fstat(fd)
            require(len(chunks) <= limit, "input exceeds byte limit")
            require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                    "input changed during read")
            require(len(chunks) == before.st_size, "incomplete input read")
            return bytes(chunks)
        finally:
            os.close(fd)


def load_json(path):
    try:
        return parse_bytes(read_bytes(path))
    except OSError as error:
        raise ObservationError(f"cannot read {path}: {error}") from error


@contextlib.contextmanager
def locked_state(path):
    """One short transaction. The caller owns this path; modes confer no identity."""
    path = Path(path).absolute()
    with raw_git._directory_fd(path.parent) as parent:
        lock = os.open(path.name + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600,
                       dir_fd=parent)
        try:
            require(stat.S_ISREG(os.fstat(lock).st_mode), "state lock is not regular")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ObservationError("coordinator state is busy") from error
            state = load_json(path)
            yield state
            state["clock"]["at"] = utc_now()
            data = json_bytes(state)
            staging = path.name + ".new"
            fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(staging, path.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(staging, dir_fd=parent)
        finally:
            os.close(lock)


def _kernel_text(path, limit=65536):
    with open(path, "rb", buffering=0) as source:
        raw = source.read(limit + 1)
    require(len(raw) <= limit, "kernel observation exceeds limit")
    return raw.decode("ascii")


def boot_id():
    return _kernel_text("/proc/sys/kernel/random/boot_id", 64).strip()


def clock_observation():
    return {"at": utc_now(), "boot_id": boot_id(),
            "monotonic_ns": time.monotonic_ns(),
            "boottime_ns": time.clock_gettime_ns(time.CLOCK_BOOTTIME)}


def process_identity(pid, runtime_handle):
    require(type(pid) is int and pid > 0, "PID must be a positive integer")
    require(isinstance(runtime_handle, str) and runtime_handle, "runtime handle is required")
    text = _kernel_text(f"/proc/{pid}/stat")
    fields = text.rsplit(") ", 1)[1].split()
    require(len(fields) >= 22, "incomplete proc stat")
    status = _kernel_text(f"/proc/{pid}/status")
    high_water = re.search(r"^VmHWM:\s+(\d+) kB$", status, re.MULTILINE)
    return {
        "pid": pid, "start_ticks": int(fields[19]), "boot_id": boot_id(),
        "runtime_handle": runtime_handle, "observed_at": utc_now(),
        "age_ms": max(0, int((time.clock_gettime(time.CLOCK_BOOTTIME)
                             - int(fields[19]) / os.sysconf("SC_CLK_TCK")) * 1000)),
        "state": "exited" if fields[0] in {"Z", "X"} else "running",
        "peak_rss_bytes": int(high_water[1]) * 1024 if high_water else None,
        "rss_complete": False, "exit_code": None,
    }


def same_process(left, right):
    return all(left[key] == right[key] for key in ("pid", "start_ticks", "boot_id",
                                                  "runtime_handle"))


def sample_process(identity):
    if identity["state"] == "exited" and identity["rss_complete"]:
        return dict(identity)
    try:
        current = process_identity(identity["pid"], identity["runtime_handle"])
    except FileNotFoundError:
        current = None
    if current is None or not same_process(identity, current):
        return {**identity, "state": "exited", "observed_at": utc_now(),
                "rss_complete": False, "exit_code": None}
    return current


def observe_owned_exit(process: subprocess.Popen, identity):
    """Nonblocking adapter for a runtime that owns the actual child handle."""
    require(process.pid == identity["pid"], "child handle/PID mismatch")
    require(process.returncode is None, "runtime already consumed the child wait status")
    try:
        current = process_identity(identity["pid"], identity["runtime_handle"])
    except FileNotFoundError as error:
        raise ObservationError("owned wait identity disappeared") from error
    require(same_process(current, identity), "process identity changed")
    try:
        pid, status, usage = os.wait4(process.pid, os.WNOHANG)
    except ChildProcessError as error:
        raise ObservationError("no owned OS wait status; exit and RSS remain unknown") from error
    if not pid:
        return current
    process.returncode = os.waitstatus_to_exitcode(status)
    return {**current, "observed_at": utc_now(), "state": "exited",
            "exit_code": process.returncode, "peak_rss_bytes": usage.ru_maxrss * 1024,
            "rss_complete": True}


def cli_event_batch(path, cursor=None):
    """Read native Copilot events incrementally; no synthetic backend/exit parser."""
    path = Path(path).absolute()
    with raw_git._directory_fd(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode), "CLI log must be a regular file")
        if cursor is None:
            cursor = {"path": str(path), "device": info.st_dev, "inode": info.st_ino,
                      "offset": 0, "session_id": None}
        require((cursor["path"], cursor["device"], cursor["inode"])
                == (str(path), info.st_dev, info.st_ino), "CLI log identity changed")
        require(cursor["offset"] <= info.st_size, "CLI log was truncated")
        source.seek(cursor["offset"])
        current = dict(cursor)
        events = []
        consumed = 0
        while len(events) < 128 and consumed < MAX_JSON_BYTES:
            start = source.tell()
            line = source.readline(MAX_JSON_BYTES + 1)
            if not line:
                break
            require(len(line) <= MAX_JSON_BYTES, "CLI event exceeds 1 MiB")
            if not line.endswith(b"\n"):
                source.seek(start)
                break
            # Native tool output can exceed a public record's description limit.
            # It stays byte/depth bounded and is never copied into the state.
            event = _decode_bytes(line, MAX_JSON_BYTES)
            require(isinstance(event, dict) and isinstance(event.get("data"), dict),
                    "malformed native CLI event")
            require(isinstance(event.get("id"), str) and event["id"], "CLI event lacks ID")
            timestamp(event.get("timestamp"))
            if event.get("type") == "session.start":
                session = event["data"].get("sessionId")
                require(isinstance(session, str) and session, "CLI session lacks ID")
                require(current["session_id"] in (None, session), "CLI session changed")
                current["session_id"] = session
            events.append(event)
            consumed += len(line)
        current["offset"] = source.tell()
        return events, current


def capture_raw_check(assignment, candidate_sha):
    worktree = Path(assignment["allowed_worktree"])
    require(not ROOT.is_relative_to(worktree.resolve()), "candidate overlaps reviewed checker source")
    start = utc_now()
    result = raw_git.run_process(
        ["/usr/bin/python3", "-I", str(ROOT / "scripts/workflow_pilot/raw_diff_check.py"),
         "--repository-root", str(worktree), "--parent", assignment["assigned_parent_sha"],
         "--candidate", candidate_sha],
        cwd=ROOT, env=raw_git.git_environment(),
    )
    return result, start, utc_now()


def linker_growth(parent_map, candidate_map):
    """Measure coordinator-produced maps using the existing linker-report logic."""
    from scripts.linker_report import budget

    def usage(path):
        regions, sections, assignments = budget.parse_map(read_bytes(path, limit=raw_git.MAX_BYTES).decode())
        require(len(regions) <= 16 and len(sections) <= 1024 and len(assignments) <= 4096,
                "linker observation exceeds structural bounds")
        report = budget.generate_report(regions, sections, assignments, None)
        names = {region["name"] for region in report["regions"]}
        require(names == {"rom", "iwram", "ewram"} and not report["overflow"], "incomplete/overflowing map")
        values = {region["name"]: region["occupied_bytes"] for region in report["regions"]}
        return values["rom"], values["iwram"] + values["ewram"]

    before, after = usage(parent_map), usage(candidate_map)
    return {"rom_bytes": max(0, after[0] - before[0]), "ram_bytes": max(0, after[1] - before[1]),
            "protocol_changes": None}


def github_run(repository, run_id, attempt, head_sha):
    require(REPOSITORY_RE.fullmatch(repository), "invalid GitHub repository")
    require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0,
            "invalid run identity")
    # This process only queries the fixed GitHub endpoint from reviewed source.
    # No candidate command, cwd, config, credential helper or module is executed.
    env = {key: value for key, value in os.environ.items()
           if key in {"HOME", "GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR", "XDG_CONFIG_HOME"}}
    env.update({"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GH_HOST": "github.com",
                "GH_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"})
    result = raw_git.run_process(
        ["/usr/bin/gh", "api", f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}"],
        cwd=ROOT, env=env,
    )
    require(result.returncode == 0, "GitHub run query failed")
    response = parse_bytes(result.stdout)
    require(type(response) is dict and type(response.get("repository")) is dict,
            "malformed GitHub run response")
    require(all(type(response.get(key)) is int and response[key] > 0
                for key in ("id", "run_attempt", "workflow_id")), "malformed GitHub run IDs")
    require(response["id"] == run_id and response["run_attempt"] == attempt
            and response.get("head_sha") == head_sha
            and response.get("repository", {}).get("full_name") == repository,
            "GitHub run identity mismatch")
    status, conclusion = response.get("status"), response.get("conclusion")
    require(type(status) is str and status in {"completed", "queued", "in_progress", "waiting", "pending", "requested"},
            "unknown GitHub run status")
    require((status == "completed") == (conclusion is not None), "incoherent GitHub run state")
    require(type(conclusion) in (str, type(None))
            and conclusion in {None, "success", "failure", "cancelled", "timed_out", "action_required",
                               "neutral", "skipped", "stale", "startup_failure"},
            "unknown GitHub run conclusion")
    return {"repository": repository, "run_id": run_id, "attempt": attempt,
            "head_sha": head_sha, "workflow_id": response["workflow_id"],
            "status": status, "conclusion": conclusion, "observed_at": utc_now()}


def kernel_oom_evidence(identity, started_at, ended_at):
    if identity["exit_code"] != -9:
        return None
    env = raw_git.git_environment()
    try:
        result = raw_git.run_process(
            ["/usr/bin/journalctl", "--no-pager", "-k", "-o", "json",
             "--since", "@" + str(int(timestamp(started_at).timestamp())),
             "--until", "@" + str(int(timestamp(ended_at).timestamp()) + 1)],
            cwd=ROOT, env=env, max_bytes=MAX_JSON_BYTES,
        )
        if result.returncode:
            return None
        for line in result.stdout.splitlines():
            event = parse_bytes(line)
            if not isinstance(event, dict):
                continue
            message = event.get("MESSAGE", "")
            source_boot = event.get("_BOOT_ID")
            event_time = event.get("__REALTIME_TIMESTAMP")
            if not (isinstance(message, str) and isinstance(source_boot, str)
                    and isinstance(event_time, str) and event_time.isdecimal()):
                continue
            within = (timestamp(started_at).timestamp() * 1_000_000 <= int(event_time)
                      <= timestamp(ended_at).timestamp() * 1_000_000)
            if (within and source_boot.replace("-", "") == identity["boot_id"].replace("-", "")
                    and re.search(rf"\bKilled process {identity['pid']} \(", message)):
                return message
    except (OSError, ValueError):
        pass
    return None
