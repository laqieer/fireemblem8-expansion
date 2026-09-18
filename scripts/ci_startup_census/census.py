"""One fixed outer startup; never a fixture mount, helper test or root report."""

from __future__ import annotations

import argparse
import ast
import ctypes
import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time


REPOSITORY = "laqieer/fireemblem8-expansion"
OWNER = "laqieer"
BRANCH = "diagnostic/issue-180-null-startup-census-1"
WORKFLOW = ".github/workflows/issue180-null-startup-census-1.yml"
BASE = "ec1dc8553419c8833a687fd8d4a6521a4e29ff7a"
CANDIDATE = "048c1bb3ab8008bbe862ad8072ed124e02fdb170"
MODE = "unsupported"
STREAM_BYTES = 256 * 1024
FACT_BYTES = 4096
RECORD_BYTES = 128 * 1024
FIXTURE_BYTES = 1024 * 1024
WATCHDOG_SECONDS, WAIT_SECONDS, OUTER_SECONDS = 30, 35, 45
NS_ARGUMENTS = (
    "/usr/bin/unshare", "--user", "--map-current-user", "--keep-caps",
    "--mount", "--fork", "--kill-child", "--propagation", "private",
)
GIT_ENV = {
    "HOME": "/nonexistent", "PATH": "/usr/bin:/bin", "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_COUNT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0",
}
ENVIRONMENT_KEYS = {
    "HOME", "LANG", "LC_ALL", "PATH", "TZ", "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_GLOBAL", "GIT_NO_REPLACE_OBJECTS", "GIT_OPTIONAL_LOCKS",
    "PYTHONDONTWRITEBYTECODE",
}
STATUS_KEYS = (
    "Uid", "Gid", "Groups", "CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb",
    "NoNewPrivs", "Seccomp", "Seccomp_filters",
)
FACT_KEYS = {
    "pid", "parent_pid", "real_effective_saved_uid", "real_effective_saved_gid",
    "groups", "status", "uid_map", "gid_map", "setgroups", "map_ownership",
    "dumpable", "namespaces", "lsm_label", "kernel", "python", "restrictions",
}
RESTRICTIONS = (
    "/proc/sys/kernel/apparmor_restrict_unprivileged_userns",
    "/proc/sys/kernel/unprivileged_userns_clone",
    "/proc/sys/user/max_user_namespaces",
    "/proc/sys/user/max_mnt_namespaces",
    "/proc/sys/fs/suid_dumpable",
    "/sys/kernel/security/lsm",
)
HARNESS_PATHS = {
    WORKFLOW, "scripts/ci_startup_census/__init__.py",
    "scripts/ci_startup_census/census.py", "scripts/ci_startup_census/test_census.py",
    "scripts/ci_startup_census/README.md",
}
CONTEXT_DIFFERENCES = (
    "Fresh diagnostic job: ubuntu-latest is a declared label, not the historical image/kernel.",
    "The earlier native suite prefix and its interpreter state are not replayed.",
    "An isolated census coordinator replaces the ordinary unittest coordinator.",
    "Read-only identity/version observations precede the otherwise fixed outer launch.",
    "Owned fixture and streams live in runner temp, not the candidate's ignored build directory.",
    "Payload stops at first outer-entry census; no original fixture or helper behavior executes.",
    "Cold startup success is not a fix or evidence that the historical context is supported.",
)


class CensusError(RuntimeError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode() + b"\n"


def parse_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CensusError("duplicate JSON field")
            result[key] = value
        return result

    def constant(_):
        raise CensusError("non-finite JSON value")

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def read_bounded(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise CensusError("allowlisted record exceeds its byte bound")
    return data


def write_record(path, value, *, limit=RECORD_BYTES):
    data = encoded(value)
    if len(data) > limit:
        raise CensusError("diagnostic record exceeds its byte bound")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


def unavailable(error):
    return {"available": False, "errno": error.errno, "error": type(error).__name__}


def text_fact(path, limit=FACT_BYTES):
    try:
        return {"available": True, "value": read_bounded(path, limit).decode("utf-8", "strict")}
    except OSError as error:
        return unavailable(error)
    except (CensusError, UnicodeError) as error:
        return {"available": False, "error": type(error).__name__, "reason": str(error)}


def stat_fact(path):
    try:
        value = os.stat(path, follow_symlinks=False)
        return {
            "available": True, "uid": value.st_uid, "gid": value.st_gid,
            "mode": stat.S_IMODE(value.st_mode), "device": value.st_dev, "inode": value.st_ino,
        }
    except OSError as error:
        return unavailable(error)


def namespace_fact(name):
    descriptor = None
    try:
        descriptor = os.open("/proc/self/ns/" + name, os.O_RDONLY | os.O_CLOEXEC)
        value = os.fstat(descriptor)
        result = {"available": True, "device": value.st_dev, "inode": value.st_ino}
        request = 0xB701 if name == "mnt" else 0xB702
        try:
            owner = fcntl.ioctl(descriptor, request)
            try:
                info = os.fstat(owner)
                result["owner_userns" if name == "mnt" else "parent_userns"] = {
                    "available": True, "device": info.st_dev, "inode": info.st_ino,
                }
            finally:
                os.close(owner)
        except OSError as error:
            result["owner_userns" if name == "mnt" else "parent_userns"] = unavailable(error)
        if name == "user":
            try:
                uid = bytearray(4)
                fcntl.ioctl(descriptor, 0xB704, uid, True)
                result["owner_uid"] = {"available": True, "value": struct.unpack("=I", uid)[0]}
            except OSError as error:
                result["owner_uid"] = unavailable(error)
        return result
    except OSError as error:
        return unavailable(error)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def dumpable_fact():
    libc = ctypes.CDLL(None, use_errno=True)
    value = libc.prctl(3, 0, 0, 0, 0)  # PR_GET_DUMPABLE is observational only.
    if value < 0:
        return {"available": False, "errno": ctypes.get_errno(), "error": "prctl"}
    return {"available": True, "value": value}


def collect_facts():
    raw = read_bounded("/proc/self/status", 64 * 1024).decode("ascii")
    status = {}
    for line in raw.splitlines():
        key, separator, value = line.partition(":")
        if separator and key in STATUS_KEYS:
            if key in status:
                raise CensusError("duplicate allowlisted status field")
            status[key] = value.strip()
    required = set(STATUS_KEYS) - {"Seccomp_filters"}
    if not required <= status.keys():
        raise CensusError("required caller status field unavailable")
    kernel = os.uname()
    result = {
        "pid": os.getpid(), "parent_pid": os.getppid(),
        "real_effective_saved_uid": list(os.getresuid()),
        "real_effective_saved_gid": list(os.getresgid()),
        "groups": os.getgroups(), "status": status,
        "uid_map": text_fact("/proc/self/uid_map"),
        "gid_map": text_fact("/proc/self/gid_map"),
        "setgroups": text_fact("/proc/self/setgroups"),
        "map_ownership": {name: stat_fact("/proc/self/" + name) for name in ("uid_map", "gid_map")},
        "dumpable": dumpable_fact(),
        "namespaces": {name: namespace_fact(name) for name in ("user", "mnt")},
        "lsm_label": text_fact("/proc/self/attr/current"),
        "kernel": {name: getattr(kernel, name) for name in ("sysname", "release", "version", "machine")},
        "python": {"version": list(sys.version_info[:3]), "isolated": sys.flags.isolated,
                   "no_site": sys.flags.no_site, "optimize": sys.flags.optimize},
        "restrictions": {path: text_fact(path) for path in RESTRICTIONS},
    }
    validate_facts(result)
    return result


def validate_facts(value):
    if not isinstance(value, dict) or set(value) != FACT_KEYS:
        raise CensusError("census fields differ from the closed allowlist")
    for name in ("real_effective_saved_uid", "real_effective_saved_gid", "groups"):
        items = value[name]
        if (
            not isinstance(items, list) or name != "groups" and len(items) != 3
            or any(type(item) is not int or not 0 <= item < 2**32 for item in items)
        ):
            raise CensusError("invalid actual identity census")
    if any(type(value[name]) is not int or value[name] <= 0 for name in ("pid", "parent_pid")):
        raise CensusError("invalid actual process identity")
    if (
        not isinstance(value["status"], dict)
        or not set(STATUS_KEYS) - {"Seccomp_filters"} <= value["status"].keys()
        or not value["status"].keys() <= set(STATUS_KEYS)
        or any(not isinstance(item, str) for item in value["status"].values())
        or any(not isinstance(value[name], dict) for name in (
            "namespaces", "map_ownership", "restrictions", "kernel", "python",
        ))
        or set(value["namespaces"]) != {"user", "mnt"}
        or set(value["map_ownership"]) != {"uid_map", "gid_map"}
        or set(value["restrictions"]) != set(RESTRICTIONS)
        or set(value["kernel"]) != {"sysname", "release", "version", "machine"}
        or set(value["python"]) != {"version", "isolated", "no_site", "optimize"}
    ):
        raise CensusError("unexpected status or namespace census fields")
    if len(encoded(value)) > RECORD_BYTES:
        raise CensusError("allowlisted census exceeds its total byte bound")


def identity(event, context):
    if not isinstance(event, dict):
        raise CensusError("push event must be an object")
    sha, run = context.get("GITHUB_SHA", ""), context.get("GITHUB_RUN_ID", "")
    repository = event.get("repository", {})
    sender = event.get("sender", {})
    if not isinstance(repository, dict) or not isinstance(sender, dict):
        raise CensusError("invalid push identity records")
    expected = {
        "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/" + BRANCH,
        "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_ACTOR": OWNER, "GITHUB_TRIGGERING_ACTOR": OWNER,
        "GITHUB_RUN_NUMBER": "1", "GITHUB_RUN_ATTEMPT": "1",
        "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux",
        "GITHUB_WORKFLOW_REF": REPOSITORY + "/" + WORKFLOW + "@refs/heads/" + BRANCH,
        "GITHUB_WORKFLOW_SHA": sha,
    }
    if (
        any(context.get(key) != value for key, value in expected.items())
        or re.fullmatch(r"[0-9a-f]{40}", sha) is None
        or re.fullmatch(r"[1-9][0-9]{0,19}", run) is None
        or event.get("ref") != expected["GITHUB_REF"]
        or event.get("before") != "0" * 40 or event.get("after") != sha
        or event.get("created") is not True or event.get("deleted") is not False
        or repository.get("full_name") != REPOSITORY or repository.get("private") is not False
        or sender.get("login") != OWNER
    ):
        raise CensusError("requires the first owner-created public hosted push, run1 attempt1")
    return {
        "repository": REPOSITORY, "branch": BRANCH, "workflow": WORKFLOW,
        "harness_sha": sha, "candidate_sha": CANDIDATE, "base_sha": BASE,
        "run_id": run, "run_number": 1, "run_attempt": 1,
        "diagnostic_only": True, "never_merge": True, "production_acceptance": False,
    }


def bounded_command(argv, environment, timeout):
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        result = subprocess.run(
            argv, env=environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            check=False, timeout=timeout, preexec_fn=child_limits,
        )
        stdout.seek(0)
        stderr.seek(0)
        output, errors = stdout.read(STREAM_BYTES + 1), stderr.read(STREAM_BYTES + 1)
    if len(output) > STREAM_BYTES or len(errors) > STREAM_BYTES:
        raise CensusError("fixed command exceeded its stream bound")
    return subprocess.CompletedProcess(argv, result.returncode, output, errors)


def git(root, *arguments):
    result = bounded_command(
        ["/usr/bin/git", "--no-pager", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",
         "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
        GIT_ENV, 5,
    )
    if result.returncode or len(result.stdout) > STREAM_BYTES or len(result.stderr) > FACT_BYTES:
        raise CensusError("immutable Git source check failed")
    return result.stdout


def verify_checkout(root, sha, *, harness=False):
    if git(root, "rev-parse", "HEAD").decode().strip() != sha:
        raise CensusError("checkout is not the exact selected revision")
    if git(root, "status", "--porcelain=v1", "--untracked-files=all").strip():
        raise CensusError("selected checkout is not clean")
    if harness:
        git(root, "merge-base", "--is-ancestor", BASE, sha)
        fields = git(root, "diff", "--name-status", "-z", BASE, sha).split(b"\0")
        if fields[-1:] != [b""] or len(fields) % 2 != 1:
            raise CensusError("invalid diagnostic path record")
        rows = list(zip(fields[0:-1:2], fields[1:-1:2]))
        if (
            any(kind != b"A" for kind, _ in rows)
            or {path.decode("utf-8") for _, path in rows} != HARNESS_PATHS
            or len(rows) != len(HARNESS_PATHS)
        ):
            raise CensusError("diagnostic changed an existing or unrelated path")


def source_environment(candidate):
    tree = ast.parse(read_bounded(candidate / "scripts/validation_ownership/authority.py", RECORD_BYTES))
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "ENVIRONMENT" for target in node.targets)]
    if len(values) != 1:
        raise CensusError("selected source lacks one literal ENVIRONMENT")
    value = ast.literal_eval(values[0])
    if not isinstance(value, dict) or set(value) != ENVIRONMENT_KEYS or any(
        not isinstance(item, str) for item in value.values()
    ):
        raise CensusError("selected source environment shape changed")
    return value


def load_lifecycle(candidate):
    path = candidate / "scripts/validation_ownership/lifecycle.py"
    spec = importlib.util.spec_from_file_location("startup_census_owned_lifecycle", path)
    if spec is None or spec.loader is None:
        raise CensusError("selected lifecycle interface unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixed_argv(candidate, program, fixture, uid, gid, deadline):
    if type(uid) is not int or type(gid) is not int or uid <= 0 or gid <= 0:
        raise CensusError("outer fixture requires original nonzero IDs")
    return [
        "/usr/bin/python3", "-I", "-S", "-B",
        str(candidate / "scripts/validation_ownership/lifecycle.py"), str(deadline), "--",
        *NS_ARGUMENTS, "/usr/bin/python3", "-I", "-S", "-B", str(program),
        str(candidate / "scripts/validation_ownership"), MODE, "outer", str(fixture),
        str(uid), str(gid),
    ]


def child_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (STREAM_BYTES, STREAM_BYTES))


def launch_once(argv, environment, candidate, output, lifecycle, state):
    reader = writer = child = descriptor = None
    inherited_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())

    def child_setup():
        signal.pthread_sigmask(signal.SIG_SETMASK, inherited_mask)
        child_limits()

    def close_reader():
        nonlocal reader
        if reader is not None:
            os.close(reader)
            reader = None

    def close_writer():
        nonlocal writer
        if writer is not None:
            os.close(writer)
            writer = None

    def reap():
        if child is not None:
            if child.poll() is None:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if descriptor is None:
                        raise CensusError("watchdog ownership uncertain: no pidfd")
                    signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                    child.wait(timeout=5)
            state["watchdog_reaped"] = True

    def close_descriptor():
        if descriptor is not None:
            os.close(descriptor)
        state["pidfd_closed"] = True

    try:
        with (output / "stdout").open("xb") as stdout, (output / "stderr").open("xb") as stderr:
            def acquire():
                nonlocal child, descriptor, reader, writer
                reader, writer = os.pipe2(os.O_CLOEXEC)
                state["lifetime_closed"] = False
                child = subprocess.Popen(
                    argv, cwd=candidate, stdin=reader, stdout=stdout, stderr=stderr,
                    env=environment, close_fds=True, start_new_session=True, preexec_fn=child_setup,
                )
                state["watchdog_launched"] = True
                state["watchdog_reaped"] = False
                state["watchdog_pid"] = child.pid
                descriptor = os.pidfd_open(child.pid)
                state["pidfd_closed"] = False
                os.close(reader)
                reader = None
            lifecycle.finish_cleanup([acquire])
            child.wait(timeout=WAIT_SECONDS)
        return child.returncode
    finally:
        lifecycle.finish_cleanup(
            [close_writer, close_reader, reap, close_descriptor,
             lambda: state.update(lifetime_closed=writer is None and reader is None)],
            primary=sys.exc_info()[1],
        )


def outer_entry(argv):
    if len(argv) != 7 or argv[2:4] != [MODE, "outer"]:
        raise CensusError("only the fixed first outer entry is admitted")
    fixture = Path(argv[4])
    if (
        Path(argv[0]).name != "null-mount-unsupported.py"
        or fixture.name != "null-mount-unsupported"
        or Path(argv[0]).resolve().parent != fixture.resolve().parent
    ):
        raise CensusError("outer entry lost its owned fixture")
    authority = parse_json(read_bounded(fixture / "authority.json", FACT_BYTES))
    if (
        not isinstance(authority, dict)
        or set(authority) != {"identity", "candidate", "uid", "gid", "deadline"}
        or not isinstance(authority["identity"], dict)
        or authority["identity"]["candidate_sha"] != CANDIDATE
        or argv[1] != authority["candidate"] + "/scripts/validation_ownership"
        or argv[5:] != [str(authority["uid"]), str(authority["gid"])]
        or time.monotonic() >= authority["deadline"]
    ):
        raise CensusError("outer entry authority or deadline differs")
    marker = {"kind": "first-outer-entry", "identity": authority["identity"]}
    sys.stdout.buffer.write(encoded(marker))
    sys.stdout.buffer.flush()
    facts = collect_facts()
    sys.stdout.buffer.write(encoded({
        "kind": "outer-census", "identity": authority["identity"], "facts": facts,
        "stopped_before_fixture_setup": True,
    }))
    sys.stdout.buffer.flush()
    return 0


def parse_outer(stdout, scope, returncode):
    rows = [parse_json(line) for line in stdout.splitlines()]
    marker = bool(rows and rows[0] == {"kind": "first-outer-entry", "identity": scope})
    facts = {"available": False, "reason": "no authenticated post-mapping census"}
    if len(rows) == 2 and marker:
        row = rows[1]
        if (
            not isinstance(row, dict)
            or set(row) != {"kind", "identity", "facts", "stopped_before_fixture_setup"}
            or row["kind"] != "outer-census" or row["identity"] != scope
            or row["stopped_before_fixture_setup"] is not True or not isinstance(row["facts"], dict)
        ):
            raise CensusError("invalid first-entry census record")
        validate_facts(row["facts"])
        facts = {"available": True, "value": row["facts"]}
    elif rows and not (len(rows) == 1 and marker and returncode != 0):
        raise CensusError("unexpected outer output or duplicate entry")
    if returncode == 0 and not facts["available"]:
        raise CensusError("successful exit lacks its actual outer-entry census")
    return marker, facts


def paths(harness, candidate, output, context):
    workspace = Path(context["GITHUB_WORKSPACE"]).resolve()
    temporary = Path(context["RUNNER_TEMP"]).resolve()
    if (
        harness.resolve() != workspace / "harness" or candidate.resolve() != workspace / "candidate"
        or output.parent.resolve() != temporary
        or output.name != "issue180-null-startup-census-1-" + context["GITHUB_RUN_ID"]
        or harness.is_symlink() or candidate.is_symlink() or output.is_symlink()
    ):
        raise CensusError("diagnostic paths escaped their separate owned roots")


def plan(harness, candidate, output, event, context):
    scope = identity(event, context)
    paths(harness, candidate, output, context)
    verify_checkout(harness, scope["harness_sha"], harness=True)
    output.mkdir(mode=0o700)
    write_record(output / "scope.json", {
        "identity": scope, "context_differences": list(CONTEXT_DIFFERENCES),
        "historical_run": "35307822585", "historical_native_job": "105483623413",
        "fixed_mode": MODE, "max_outer_attempts": 1,
        "bounds": {"watchdog_seconds": WATCHDOG_SECONDS, "wait_seconds": WAIT_SECONDS,
                   "outer_seconds": OUTER_SECONDS, "stream_bytes": STREAM_BYTES,
                   "fixture_bytes": FIXTURE_BYTES},
    })


def interrupted(signum, _frame):
    raise CensusError("coordinator interrupted by signal " + str(signum))


def run(harness, candidate, output, event, context):
    scope = identity(event, context)
    paths(harness, candidate, output, context)
    planned = parse_json(read_bounded(output / "scope.json", RECORD_BYTES))
    if planned["identity"] != scope or planned["context_differences"] != list(CONTEXT_DIFFERENCES):
        raise CensusError("planned identity or context disclosure differs")
    write_record(output / "attempt.json", {"identity": scope, "attempts": 1, "claimed_before_setup": True})
    state = {"watchdog_launched": False, "watchdog_reaped": True, "pidfd_closed": True, "lifetime_closed": True,
             "fixture_owned": False, "fixture_removed": False, "source_unchanged": False,
             "cleanup_confirmed": False}
    result = {"identity": scope, "context_differences": list(CONTEXT_DIFFERENCES),
              "first_error": None, "outer_returncode": None, "outer_entry_reached": False,
              "post_unshare_census": {"available": False, "reason": "no actual child census"},
              "cleanup": state, "production_acceptance": False, "historical_failure_fixed": False}
    fixture_root = output / "owned-fixture"
    lifecycle = None
    handlers = {}
    initial_fds = None
    start = time.monotonic()
    try:
        verify_checkout(harness, scope["harness_sha"], harness=True)
        verify_checkout(candidate, CANDIDATE)
        environment = source_environment(candidate)
        lifecycle = load_lifecycle(candidate)
        if lifecycle.owned_children():
            raise CensusError("coordinator already has unrelated children")
        initial_fds = set(os.listdir("/proc/self/fd"))
        for signum in lifecycle.TERMINATING:
            handlers[signum] = signal.signal(signum, interrupted)
        facts = collect_facts()
        version = bounded_command(["/usr/bin/unshare", "--version"], environment, 2)
        if len(version.stdout) > FACT_BYTES or len(version.stderr) > FACT_BYTES:
            raise CensusError("unshare version output exceeded its bound")
        write_record(output / "prelaunch.json", {
            "identity": scope, "facts": facts,
            "unshare_version": {"returncode": version.returncode,
                                "stdout": version.stdout.decode("utf-8", "strict"),
                                "stderr": version.stderr.decode("utf-8", "strict")},
        })
        if version.returncode:
            raise CensusError("unshare version command failed")
        uid, gid = os.getuid(), os.getgid()
        if uid <= 0 or gid <= 0 or os.geteuid() == 0:
            raise CensusError("caller is not the required ordinary nonzero user")
        fixture_root.mkdir(mode=0o700)
        state["fixture_owned"] = True
        fixture = fixture_root / "null-mount-unsupported"
        fixture.mkdir(mode=0o700)
        program = fixture_root / "null-mount-unsupported.py"
        with program.open("xb") as stream:
            stream.write(read_bounded(Path(__file__), RECORD_BYTES))
        deadline = time.monotonic() + WATCHDOG_SECONDS
        write_record(fixture / "authority.json", {
            "identity": scope, "candidate": str(candidate), "uid": uid, "gid": gid, "deadline": deadline,
        }, limit=FACT_BYTES)
        if program.stat().st_size + (fixture / "authority.json").stat().st_size > FIXTURE_BYTES:
            raise CensusError("owned fixed fixture exceeded its size bound")
        argv = fixed_argv(candidate, program, fixture, uid, gid, deadline)
        result["argv"] = argv
        write_record(output / "launch.json", {
            "identity": scope, "argv": argv, "deadline": deadline, "monotonic_start": time.monotonic(),
        })
        result["outer_returncode"] = launch_once(argv, environment, candidate, output, lifecycle, state)
        if result["outer_returncode"]:
            result["first_error"] = {"type": "outer-startup-exit", "returncode": result["outer_returncode"],
                                     "raw_stderr_artifact": "stderr"}
        stdout = read_bounded(output / "stdout", STREAM_BYTES)
        stderr = read_bounded(output / "stderr", STREAM_BYTES)
        result["stream_bytes"] = {"stdout": len(stdout), "stderr": len(stderr)}
        marker, post = parse_outer(stdout, scope, result["outer_returncode"])
        result["outer_entry_reached"], result["post_unshare_census"] = marker, post
    except (CensusError, OSError, ValueError, subprocess.SubprocessError) as error:
        record = {
            "type": type(error).__name__, "message": str(error)[:FACT_BYTES], "errno": getattr(error, "errno", None),
            "cleanup_errors": list(getattr(error, "cleanup_errors", ())),
        }
        if result["first_error"] is None:
            result["first_error"] = record
        else:
            result["secondary_error"] = record
    finally:
        unhandled = sys.exc_info()[1]
        if unhandled is not None and result["first_error"] is None:
            result["first_error"] = {"type": type(unhandled).__name__, "message": str(unhandled)[:FACT_BYTES]}
        cleanup_errors = []
        try:
            if lifecycle is not None:
                lifecycle.finish_cleanup([], handlers=handlers)
        except (OSError, RuntimeError) as error:
            cleanup_errors.append({"type": type(error).__name__, "message": str(error)[:FACT_BYTES]})
        try:
            if fixture_root.exists():
                if not state["fixture_owned"]:
                    raise CensusError("unowned pre-existing fixture retained")
                shutil.rmtree(fixture_root)
            state["fixture_removed"] = True
        except (CensusError, OSError, RuntimeError) as error:
            cleanup_errors.append({"type": type(error).__name__, "message": str(error)[:FACT_BYTES]})
        try:
            verify_checkout(candidate, CANDIDATE)
            verify_checkout(harness, scope["harness_sha"], harness=True)
            state["source_unchanged"] = True
        except (CensusError, OSError, ValueError, subprocess.SubprocessError) as error:
            cleanup_errors.append({"type": type(error).__name__, "message": str(error)[:FACT_BYTES]})
        try:
            if lifecycle is not None and initial_fds is not None:
                children = lifecycle.owned_children()
                final_fds = set(os.listdir("/proc/self/fd"))
                state["caller_children"] = children
                state["fds_before"], state["fds_after"] = len(initial_fds), len(final_fds)
                if children or initial_fds != final_fds:
                    raise CensusError("coordinator retained children or changed descriptors")
        except (OSError, RuntimeError) as error:
            cleanup_errors.append({"type": type(error).__name__, "message": str(error)[:FACT_BYTES]})
        state["errors"] = cleanup_errors
        state["cleanup_confirmed"] = (
            state["fixture_removed"] and state["source_unchanged"] and not cleanup_errors
            and all(state[name] for name in ("watchdog_reaped", "pidfd_closed", "lifetime_closed"))
        )
        result["elapsed_seconds"] = time.monotonic() - start
        write_record(output / "result.json", result)
    return 0 if result["first_error"] is None and state["cleanup_confirmed"] else 1


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if not sys.flags.isolated or not sys.flags.no_site:
        raise CensusError("diagnostic execution requires Python -I -S")
    if len(argv) == 7 and argv[2:4] == [MODE, "outer"]:
        return outer_entry(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "run"))
    for name in ("harness", "candidate", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    options = parser.parse_args(argv[1:])
    event = parse_json(read_bounded(os.environ["GITHUB_EVENT_PATH"], FIXTURE_BYTES))
    operation = plan if options.operation == "plan" else run
    return operation(options.harness, options.candidate, options.output, event, os.environ) or 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CensusError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("startup-census: " + type(error).__name__ + ": " + str(error)[:FACT_BYTES], file=sys.stderr)
        raise SystemExit(1)
