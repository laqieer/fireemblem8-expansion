"""Unprivileged contained probes and the single real graph invocation."""

from __future__ import annotations

import ctypes
import dataclasses
import errno
import hashlib
import math
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import threading
import time

if __package__:
    from . import kernel, observation_failure, policy, runtime_view
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kernel
    import observation_failure
    import policy
    import runtime_view


def require_contained(config):
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        raise policy.GuardError("worker requires Python -I -S -B")
    current = kernel.status()
    identities = kernel.namespaces()
    if (
        os.getpid() != 1 or os.getuid() != config["uid"] or os.getgid() != config["gid"]
        or config["uid"] <= 0 or config["gid"] <= 0 or os.getgroups()
        or current["NoNewPrivs"] != "1"
        or any(int(current[name], 16) for name in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd"))
        or kernel.parent_death_signal() != signal.SIGKILL
    ):
        raise policy.GuardError("worker lacks real unprivileged UID/capability/NNP/PID/lifetime identity")
    if any(identities[name] == config["host_namespaces"][name] for name in policy.NAMESPACES):
        raise policy.GuardError("worker shares a protected host namespace")
    if identities["user"] != config["host_namespaces"]["user"]:
        raise policy.GuardError("unexpected coordinator user namespace/runtime ownership")
    if dict(os.environ) != policy.CLEAN_ENV:
        raise policy.GuardError("worker environment is not the exact credential-free environment")
    if kernel.membership() != config["cgroup_relative"]:
        raise policy.GuardError("worker is outside its actual cgroup")
    if not math.isfinite(config["deadline"]) or not 0 < config["deadline"] - time.monotonic() <= policy.GRAPH_SECONDS:
        raise policy.GuardError("worker lacks a live original-duration deadline")
    for name in policy.CGROUP_FILES:
        path = Path("/guard") / name
        info = path.lstat()
        if (
            info.st_uid != 0 or not stat.S_ISREG(info.st_mode)
            or kernel.filesystem_type(path) != kernel.CGROUP2_MAGIC
            or not os.statvfs(path).f_flag & os.ST_RDONLY
            or [info.st_dev, info.st_ino] != config["cgroup_file_identities"][name]
        ):
            raise policy.GuardError("cgroup guard is not the real readonly root-owned kernel object")
    for name, expected in (
        ("memory.max", config["memory_max"]), ("memory.swap.max", 0),
        ("memory.oom.group", 1), ("pids.max", config["pids_max"]),
    ):
        if kernel.number(Path("/guard") / name) != expected:
            raise policy.GuardError("effective cgroup limit differs from the configured envelope")
    if kernel.fields("/guard/cgroup.events").get("populated") != 1:
        raise policy.GuardError("worker's cgroup is not populated")
    if str(os.getpid()) not in kernel.read("/guard/cgroup.procs").decode().split():
        raise policy.GuardError("worker is not listed in its actual protected cgroup")
    if kernel.read("/proc/self/oom_score_adj").strip() != b"0":
        raise policy.GuardError("worker inherited an OOM exemption")
    for path in ("/", "/repo", "/usr", "/etc/alternatives", "/diag", "/guard"):
        if not os.statvfs(path).f_flag & os.ST_RDONLY:
            raise policy.GuardError(f"protected path is not readonly: {path}")
    if kernel.filesystem_type("/proc") != kernel.PROC_MAGIC or os.statvfs("/proc").f_flag & 15 != kernel.PROC_MOUNT_FLAGS:
        raise policy.GuardError("private process view must preserve RW/nosuid/nodev/noexec semantics")
    if int(current["Pid"]) != os.getpid() or int(kernel.status(1)["Pid"]) != 1:
        raise policy.GuardError("proc is not the actual private PID-namespace view")
    for path in ("/work", "/tmp", "/repo/build", "/run", "/var/tmp", "/dev/shm"):
        if os.stat(path).st_dev != config["workspace_device"] or os.statvfs(path).f_flag & os.ST_RDONLY:
            raise policy.GuardError("a writable alias escaped the bounded workspace filesystem")
    runtime = runtime_view.observe()
    runtime_view.validate(runtime)
    runtime_view.compare(config["runtime_manifest"], runtime)
    expected_profile = config["apparmor_profile"]
    apparmor = kernel.read("/proc/self/attr/current").decode().strip() if expected_profile is not None else "not enabled by runner"
    if expected_profile is not None and not apparmor.startswith(expected_profile + " "):
        raise policy.GuardError("worker did not inherit the narrowly scoped userns profile")
    return {
        "uid": os.getuid(), "gid": os.getgid(), "namespaces": identities,
        "cgroup": kernel.membership(), "memory_max": kernel.number("/guard/memory.max"),
        "pids_max": kernel.number("/guard/pids.max"), "no_new_privs": 1,
        "capabilities": {name: current[name] for name in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd")},
        "apparmor": apparmor, "deadline": config["deadline"],
        "workspace_device": config["workspace_device"],
        "python": sys.version, "executable": sys.executable,
        "runtime_manifest": runtime,
        "proc_mount_flags": os.statvfs("/proc").f_flag,
        "proc_policy": "Private RW process view; actual dangerous writes must reject without host capabilities.",
    }


def denied_open(path):
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        if error.errno not in {errno.EROFS, errno.EACCES, errno.EPERM}:
            raise
        return error.errno
    else:
        os.close(descriptor)
        raise policy.GuardError(f"protected writable open unexpectedly succeeded: {path}")


def denied_control_write(path, value):
    before = kernel.read(path, 4096)
    descriptor = None
    opened = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        opened = True
        os.write(descriptor, value)
    except OSError as error:
        if error.errno not in {errno.EROFS, errno.EACCES, errno.EPERM}:
            raise
        after = kernel.read(path, 4096)
        if after != before:
            raise policy.GuardError("denied control write changed actual kernel state")
        return {
            "opened": opened, "errno": error.errno,
            "before": before.decode("ascii").strip(), "after": after.decode("ascii").strip(),
        }
    finally:
        if descriptor is not None:
            os.close(descriptor)
    raise policy.GuardError("dangerous kernel/control write unexpectedly succeeded: " + path)


def proc_protection_probe():
    current = kernel.status()
    if int(current["Pid"]) != os.getpid():
        raise policy.GuardError("process view exposes a different PID namespace")
    if kernel.read("/proc/self/oom_score_adj").strip() != b"0":
        raise policy.GuardError("process has a nonzero OOM adjustment before qualification")
    denied = {
        "/proc/self/oom_score_adj": denied_control_write("/proc/self/oom_score_adj", b"-1000"),
        "/proc/self/oom_adj": denied_control_write("/proc/self/oom_adj", b"-17"),
    }
    for path in (
        "/proc/sys/kernel/kptr_restrict", "/proc/sys/vm/overcommit_memory",
        "/proc/sys/kernel/overflowuid", "/proc/sys/kernel/overflowgid",
    ):
        denied[path] = denied_control_write(path, kernel.read(path, 4096))
    try:
        sysrq = {"errno": denied_open("/proc/sysrq-trigger"), "exposed": True}
    except FileNotFoundError:
        sysrq = {"errno": errno.ENOENT, "exposed": False}
    result = {
        "uid": os.getuid(), "pid": os.getpid(), "proc_pid": int(current["Pid"]),
        "uid_map": kernel.read("/proc/self/uid_map").decode().split(),
        "proc_mount_flags": os.statvfs("/proc").f_flag,
        "denied_writes": denied, "sysrq_write_open": sysrq,
        "oom_score_adj_after": kernel.read("/proc/self/oom_score_adj").decode().strip(),
    }
    policy.validate_proc_protection(result)
    return result


def nested_probe(config, config_handoff):
    if kernel.membership() != config["cgroup_relative"] or os.getuid() != 0:
        raise policy.GuardError("nested userns probe escaped its scope")
    mapping = kernel.read("/proc/self/uid_map").decode().split()
    if mapping != ["0", str(config["uid"]), "1"]:
        raise policy.GuardError("nested UID 0 does not map to the non-root host UID")
    kernel.mount_private_proc("/proc")
    proof = {
        "uid": os.getuid(), "uid_map": kernel.read("/proc/self/uid_map").decode().split(),
        "gid_map": kernel.read("/proc/self/gid_map").decode().split(),
        "no_new_privs": kernel.status()["NoNewPrivs"],
        "namespaces": kernel.namespaces(), "cgroup": kernel.membership(),
        "config_handoff": config_handoff,
        "proc_protection": proc_protection_probe(),
    }
    point = Path("/work/cgroup-control-probe")
    point.mkdir(exist_ok=True)
    if kernel.LIBC.unshare(0x02000000):
        error = ctypes.get_errno()
        if error not in {errno.EPERM, errno.EACCES}:
            raise OSError(error, "cgroup namespace probe failed")
        proof["cgroup_control_probe"] = {"namespace_denied": error}
    elif kernel.LIBC.mount(b"cgroup2", os.fsencode(point), b"cgroup2", 2 | 4 | 8, None):
        error = ctypes.get_errno()
        if error not in {errno.EPERM, errno.EACCES}:
            raise OSError(error, "cgroup mount probe failed")
        proof["cgroup_control_probe"] = {"mount_denied": error}
    else:
        if kernel.membership() != "/":
            raise policy.GuardError("cgroup namespace did not root at the owned scope")
        denied = {}
        for name, same_value in (("memory.max", config["memory_max"]), ("pids.max", config["pids_max"])):
            try:
                with (point / name).open("w") as stream:
                    stream.write(str(same_value))
            except OSError as error:
                if error.errno not in {errno.EROFS, errno.EPERM, errno.EACCES}:
                    raise
                denied[name] = error.errno
            else:
                raise policy.GuardError("nested namespace can write a containment limit")
        proof["cgroup_control_probe"] = {"same_value_writes_denied": denied}
    print(policy.encoded(proof).decode(), flush=True)


def run_nested_probe(config):
    descriptor, receipt = kernel.open_owned_config("/guard/config.json")
    try:
        completed = subprocess.run(
            ["/usr/bin/unshare", "--user", "--map-root-user", "--mount", "--net", "--pid",
             "--fork", "--kill-child", "--propagation", "private",
             "/usr/bin/python3", "-I", "-S", "-B", "/diag/worker.py",
             "--nested-probe", str(descriptor), policy.encoded(receipt).decode()],
            env=policy.CLEAN_ENV, capture_output=True, timeout=10, pass_fds=(descriptor,),
        )
    finally:
        os.close(descriptor)
    if completed.returncode or len(completed.stdout) > 65536 or len(completed.stderr) > 65536:
        detail = completed.stderr.decode("utf-8", "replace")
        if completed.stdout:
            detail += completed.stdout.decode("utf-8", "replace")
        raise policy.GuardError("original local userns route failed; no fallback: " + detail)
    nested = policy.parse_json(completed.stdout)
    policy.validate_nested_probe(
        nested, uid=config["uid"], gid=config["gid"], group=config["cgroup_relative"],
        parent_user_namespace=config["host_namespaces"]["user"],
    )
    handoff = nested.get("config_handoff")
    if not isinstance(handoff, dict) or handoff.get("parent_identity") != receipt["identity"] or (
        handoff.get("sha256") != receipt["sha256"]
        or handoff.get("parent_user_namespace") != receipt["parent_user_namespace"]
    ):
        raise policy.GuardError("nested config descriptor does not match its parent authority")
    return nested


def identity_probe(config):
    buffer = bytearray(policy.MIB)
    for offset in range(0, len(buffer), 4096):
        buffer[offset] = 1
    denied = {path: denied_open(path) for path in ("/repo/Makefile", "/guard/memory.max", "/guard/pids.max")}
    proc_protection = proc_protection_probe()
    try:
        os.setresuid(0, 0, 0)
    except OSError as error:
        if error.errno != errno.EPERM:
            raise
    else:
        raise policy.GuardError("non-root worker acquired host UID 0")
    if socket.if_nameindex() != [(1, "lo")]:
        raise policy.GuardError("worker has a host network interface")
    connection = socket.socket()
    connection.settimeout(0.5)
    try:
        result = connection.connect_ex(("127.0.0.1", config["host_listener_port"]))
        if result == 0:
            raise policy.GuardError("worker reached the host listener")
    finally:
        connection.close()
    if Path("/home/runner").exists() or Path("/run/docker.sock").exists():
        raise policy.GuardError("host credentials/control paths are visible")
    nested = run_nested_probe(config)
    if kernel.number("/guard/memory.max") != config["memory_max"] or kernel.number("/guard/pids.max") != config["pids_max"]:
        raise policy.GuardError("nested qualification changed a containment limit")
    return {"modest_allocation_bytes": len(buffer), "denied_write_opens": denied,
            "host_network_errno": result, "proc_protection": proc_protection, "nested": nested}


def pids_probe():
    children = []
    failure = None
    try:
        for _ in range(32):
            try:
                pid = os.fork()
            except OSError as error:
                if error.errno != errno.EAGAIN:
                    raise
                failure = error.errno
                break
            if pid == 0:
                while True:
                    signal.pause()
            children.append((pid, os.pidfd_open(pid)))
        if failure is None or kernel.fields("/guard/pids.events").get("max", 0) < 1:
            raise policy.GuardError("tiny cgroup PID rejection was not observed")
        return {"errno": failure, "children": len(children), "pids_events": kernel.fields("/guard/pids.events")}
    finally:
        for pid, descriptor in children:
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            os.waitpid(pid, 0)
            os.close(descriptor)


def disk_probe(config):
    info = os.statvfs("/work")
    if info.f_blocks * info.f_frsize > config["disk_bytes"]:
        raise policy.GuardError("disk probe is not on the tiny fixed filesystem")
    path = Path("/work/disk-probe")
    written = 0
    rejected = None
    try:
        with path.open("xb", buffering=0) as output:
            for _ in range(96):
                try:
                    written += output.write(b"d" * policy.MIB)
                except OSError as error:
                    if error.errno != errno.ENOSPC:
                        raise
                    rejected = error.errno
                    break
        if rejected is None or written < policy.MIB:
            raise policy.GuardError("tiny filesystem did not enforce its capacity")
        return {"errno": rejected, "written_bytes": written, "filesystem_bytes": info.f_blocks * info.f_frsize}
    finally:
        path.unlink(missing_ok=True)


def escaped_descendant(config):
    first = os.fork()
    if first == 0:
        os.setsid()
        second = os.fork()
        if second:
            os._exit(0)
        kernel.emit(config["scope"], "escaped", {
            "pid": os.getpid(), "ppid": os.getppid(), "sid": os.getsid(0),
            "pgrp": os.getpgrp(), "cgroup": kernel.membership(),
        })
        while True:
            signal.pause()
    os.waitpid(first, 0)
    while True:
        signal.pause()


class Sampler:
    def __init__(self, scope):
        self.scope = scope
        self.budget = None
        self.phase = "candidate-import"
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="diagnostic-sampler", daemon=True)

    def snapshot(self):
        if self.budget is None:
            return {"phase": self.phase, "budget": None}
        budget = self.budget
        amounts = budget.bytes.copy()
        return {
            "phase": self.phase,
            "budget": {
                "bytes": amounts, "total": sum(amounts.values()), "runs": budget.runs,
                "states": budget.states, "failed": budget.failed, "closed": budget.closed,
            },
            "semantics": "Observed counters, not an atomic admission receipt or a live-memory measurement.",
        }

    def run(self):
        while not self.stop.wait(policy.SAMPLE_SECONDS):
            kernel.emit(self.scope, "progress", self.snapshot())

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise policy.GuardError("diagnostic sampler did not stop")


def calibration_budget(limits_type, budget_type, deadline):
    original = dataclasses.asdict(limits_type())
    classified = policy.profile_manifest(original)
    profile_type = dataclasses.make_dataclass(
        "HostedDiagnosticLimits",
        [(name, int, dataclasses.field(default=policy.POLICY_SENTINEL)) for name in policy.RELAXED],
        bases=(limits_type,), frozen=True,
    )

    @dataclasses.dataclass(kw_only=True)
    class ClockBudget(budget_type):
        original_deadline: dataclasses.InitVar[float]

        def __post_init__(self, original_deadline):
            self.started = original_deadline - self.limits.seconds

    limits = profile_type()
    budget = ClockBudget(limits=limits, original_deadline=deadline)
    if budget.deadline != deadline or dataclasses.asdict(limits_type()) != original:
        raise policy.GuardError("original clock/default policy was not preserved")
    return budget, limits, original, classified


def graph_error_record(error, sampler, observer):
    record = policy.error_record(error)
    record["counters"] = sampler.snapshot()
    record["observation_failure"] = (
        observer.capture(error) if observer is not None
        else observation_failure.unavailable("binding-not-ready")
    )
    return record


def graph(config):
    require_contained(config)
    sampler = Sampler(config["scope"])
    sampler.thread.start()
    budget = None
    observer = None
    primary = None
    try:
        sys.path.insert(0, "/repo")
        from scripts.validation_ownership.authority import git
        from scripts.validation_ownership.budget import Limits, ProbeBudget
        from scripts.validation_ownership.graph_report import check
        from scripts.validation_ownership.make_probe import ProbeSession

        budget, limits, original, classified = calibration_budget(Limits, ProbeBudget, config["deadline"])
        observer = observation_failure.Observer(ProbeSession, budget)
        sampler.budget = budget
        root = Path("/repo")
        head = git(root, budget, "rev-parse", "HEAD").decode().strip()
        base = git(root, budget, "rev-parse", policy.BASE + "^{commit}").decode().strip()
        if (head, base) != (policy.GRAPH, policy.BASE):
            raise policy.GuardError("actual candidate HEAD/BASE differ from the frozen scope")
        changed = [
            path.decode("utf-8", "strict")
            for path in git(root, budget, "diff", "--no-ext-diff", "--no-textconv",
                            "--name-only", "-z", policy.BASE, policy.GRAPH).split(b"\0") if path
        ]
        kernel.emit(config["scope"], "graph-start", {
            "head": head, "base": base, "changed_paths": changed,
            "limits": classified, "deadline": budget.deadline, "graph_check_attempts": 1,
            "semantics": "About to call check once; this marker alone does not attest body entry or completion.",
        })
        sampler.phase = "graph-report-check"
        report = check(
            root, budget=budget, revision=policy.GRAPH, base_revision=policy.BASE,
            changed_paths=changed, lifecycle=True,
        )
        validated = policy.validate_report(report)
        if report["coverage"]["tracked_paths"] != config["tracked_paths"]:
            raise policy.GuardError("report coverage differs from the real pinned Git inventory")
        if budget.limits is not limits or dataclasses.asdict(Limits()) != original:
            raise policy.GuardError("source defaults or effective diagnostic policy changed in flight")
        sampler.phase = "completed"
        return {"report": report, "validation": validated, "counters": sampler.snapshot(),
                "graph_check_attempts": 1, "graph_check_completed": True}
    except BaseException as error:
        primary = error
        kernel.emit(config["scope"], "error", graph_error_record(error, sampler, observer))
        return None
    finally:
        sampler.close()
        if budget is not None:
            try:
                budget.close()
            except BaseException as error:
                kernel.emit(config["scope"], "cleanup-error", policy.error_record(error))
                if primary is None:
                    raise


def main(config):
    proof = require_contained(config)
    kernel.emit(config["scope"], "ready", proof)
    mode = config["mode"]
    if mode == "graph":
        result = graph(config)
        if result is None:
            return 1
        kernel.emit(config["scope"], "result", result)
        return 0
    if mode == "identity":
        result = identity_probe(config)
    elif mode == "pids":
        result = pids_probe()
    elif mode == "disk":
        result = disk_probe(config)
    elif mode == "memory":
        data = bytearray(192 * policy.MIB)
        for offset in range(0, len(data), 4096):
            data[offset] = 1
        raise policy.GuardError("tiny cgroup did not enforce memory.max")
    elif mode == "output":
        while True:
            os.write(1, b"x" * 65536)
    elif mode in {"deadline", "lifetime"}:
        escaped_descendant(config)
        raise policy.GuardError("escaped descendant unexpectedly returned")
    else:
        raise policy.GuardError("unrecognized closed probe mode")
    kernel.emit(config["scope"], "probe-result", result)
    return 0


if __name__ == "__main__":
    active = None
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "--nested-probe":
            descriptor = int(sys.argv[2])
            try:
                value, handoff = kernel.inherited_config(descriptor, policy.parse_json(sys.argv[3]))
            finally:
                os.close(descriptor)
            if not isinstance(value, dict) or not isinstance(value.get("scope"), str):
                raise policy.GuardError("inherited worker config is malformed")
            active = value
            nested_probe(active, handoff)
            code = 0
        elif len(sys.argv) == 2:
            active = kernel.owned_config(sys.argv[1])
            code = main(active)
        else:
            raise policy.GuardError("worker requires its one readonly config")
    except BaseException as error:
        if active is not None:
            kernel.emit(active["scope"], "error", policy.error_record(error))
        else:
            print("worker has no admitted readonly containment config", file=sys.stderr)
        code = 1
    raise SystemExit(code)
