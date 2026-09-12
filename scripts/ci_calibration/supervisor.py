"""Hosted-only outer owner for the disposable diagnostic and its evidence."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import pwd
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

if __package__:
    from . import kernel, policy, runtime_view, volume_mount
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kernel
    import policy
    import runtime_view
    import volume_mount


HERE = Path(__file__).resolve().parent
LIFECYCLE = HERE.parent / "validation_ownership/lifecycle.py"
CGROOT = Path("/sys/fs/cgroup")
ROOT_ENV = {**policy.CLEAN_ENV, "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}


def apparmor_text(name):
    if not re.fullmatch(r"vo-ci180-[1-9][0-9]{0,19}", name):
        raise policy.GuardError("invalid process-only AppArmor profile name")
    return f"abi <abi/4.0>,\nprofile {name} flags=(default_allow) {{\n  userns,\n}}\n"


def io_peaks(previous, current, elapsed, peaks):
    if elapsed <= 0:
        raise policy.GuardError("nonmonotonic I/O sampling clock")
    for device, values in current.items():
        for key in ("rbytes", "wbytes", "rios", "wios", "dbytes", "dios"):
            difference = values.get(key, 0) - previous.get(device, {}).get(key, 0)
            if difference < 0:
                raise policy.GuardError("nonmonotonic cgroup I/O accounting")
            name = device + "/" + key + "_per_second"
            peaks[name] = max(peaks.get(name, 0), difference / elapsed)


def tool(argv, *, timeout=60, maximum=policy.OUTPUT_BYTES, allowed=(0,)):
    reader, writer = os.pipe2(os.O_CLOEXEC)
    child = None
    buffers = [bytearray(), bytearray()]
    deadline = time.monotonic() + timeout
    try:
        child = subprocess.Popen(
            ["/usr/bin/python3", "-I", "-S", "-B", str(LIFECYCLE), str(deadline), "--", *argv],
            stdin=reader, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ROOT_ENV, close_fds=True,
        )
        os.close(reader)
        reader = None
        with selectors.DefaultSelector() as selector:
            for index, stream in enumerate((child.stdout, child.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, index)
            total = 0
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise policy.GuardError("trusted setup command exhausted its deadline")
                for key, _ in selector.select(0.05):
                    data = os.read(key.fd, min(65536, maximum - total + 1))
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(data)
                    if total > maximum:
                        raise policy.GuardError("trusted setup output exceeded its bound")
                    buffers[key.data].extend(data)
        code = child.wait(timeout=max(0.1, deadline - time.monotonic()))
        if code not in allowed:
            detail = bytes(buffers[1]).decode("utf-8", "replace")
            if len(detail.encode()) > policy.ERROR_BYTES:
                raise policy.GuardError("trusted setup failure evidence exceeded its bound")
            raise policy.GuardError(f"trusted setup failed ({argv[0]}, {code}): {detail}")
        return code, bytes(buffers[0])
    finally:
        os.close(writer)
        if reader is not None:
            os.close(reader)
        if child is not None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                raise policy.GuardError("trusted setup cleanup is unconfirmed; retain owned state") from error
            child.stdout.close()
            child.stderr.close()


def git(root, *arguments, allowed=(0,), safe_children=()):
    return tool([
        "/usr/bin/git", "--no-replace-objects", "--no-optional-locks", "-C", str(root),
        "-c", "safe.directory=" + str(root),
        *(argument for path in safe_children for argument in ("-c", "safe.directory=" + str(root / path))),
        "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
        "-c", "core.preloadindex=false", "-c", "diff.external=",
        *arguments,
    ], maximum=32 * policy.MIB, allowed=allowed)[1]


class Artifacts:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o755, parents=False, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise policy.GuardError("artifact root is not an owned directory")
        self.root.chmod(0o755)

    def _capacity(self, name, size, append):
        if name not in policy.ARTIFACT_NAMES:
            raise policy.GuardError("artifact is not in the upload allowlist")
        before = self.root / name
        if before.is_symlink() or before.exists() and not stat.S_ISREG(before.lstat().st_mode):
            raise policy.GuardError("artifact destination is not a regular owned record")
        current = before.stat().st_size if before.exists() else 0
        maximum = (
            policy.METRICS_BYTES if name == "metrics.jsonl" else
            policy.PROGRESS_BYTES if name == "progress.jsonl" else policy.OUTPUT_BYTES
        )
        if size + (current if append else 0) > maximum:
            raise policy.GuardError("diagnostic artifact exceeded its individual bound")
        total = sum(path.stat().st_size for path in self.root.iterdir() if path.name in policy.ARTIFACT_NAMES)
        if total + size - (0 if append else current) > policy.ARTIFACT_BYTES:
            raise policy.GuardError("diagnostic artifacts exceeded their total bound")

    def write(self, name, value):
        data = policy.encoded(value) + b"\n"
        self._capacity(name, len(data), False)
        path = self.root / name
        temporary = self.root / (name + ".tmp")
        created = False
        try:
            with temporary.open("xb") as stream:
                created = True
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o644)
            temporary.replace(path)
        finally:
            if created:
                temporary.unlink(missing_ok=True)

    def append(self, name, value):
        data = policy.encoded(value) + b"\n"
        self._capacity(name, len(data), True)
        path = self.root / name
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o644)


def capacity_facts(directory):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise policy.GuardError("hosted Linux x86-64 is required")
    release = dict(
        line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line
    )
    if release.get("ID", "").strip('"') != "ubuntu":
        raise policy.GuardError("only the selected ubuntu-latest runner is supported")
    if kernel.filesystem_type(CGROOT) != kernel.CGROUP2_MAGIC:
        raise policy.GuardError("cgroup-v2 is unavailable")
    mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
    cgroup_mounts = [row for row in mounts if row[4] == str(CGROOT)]
    if len(cgroup_mounts) != 1 or cgroup_mounts[0][3] != "/" or "rw" not in cgroup_mounts[0][5].split(","):
        raise policy.GuardError("requires writable full-host cgroup-v2, not a delegated/container view")
    mount_record = cgroup_mounts[0]
    if "nsdelegate" not in mount_record[mount_record.index("-") + 3].split(","):
        raise policy.GuardError("existing cgroup namespace delegation protection is required; no remount fallback")
    if kernel.read("/proc/1/comm").strip() != b"systemd":
        raise policy.GuardError("requires the disposable hosted VM, not a job container")
    values = {}
    for line in kernel.read("/proc/meminfo").decode().splitlines():
        key, value = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable"}:
            amount, unit = value.split()
            if unit != "kB":
                raise policy.GuardError("unsupported memory observation unit")
            values[key] = int(amount) * 1024
    current = CGROOT / kernel.membership().lstrip("/")
    ancestors = []
    while True:
        row = {"path": str(current)}
        for name in ("memory", "pids"):
            maximum = current / (name + ".max")
            observed = current / (name + ".current")
            row[name + "_max"] = kernel.number(maximum, maximum=True) if maximum.exists() else None
            row[name + "_current"] = kernel.number(observed) if observed.exists() else 0
            if current != CGROOT and (not maximum.exists() or not observed.exists()):
                raise policy.GuardError("ancestor resource controllers are not observable")
        ancestors.append(row)
        if current == CGROOT:
            break
        current = current.parent
    load = kernel.read("/proc/loadavg", 4096).decode().split()
    threads_current = int(load[3].split("/")[1])
    disk = os.statvfs(directory)
    return {
        "memory_total": values["MemTotal"], "memory_available": values["MemAvailable"],
        "disk_available": disk.f_bavail * disk.f_frsize, "cpus": len(os.sched_getaffinity(0)),
        "threads_max": kernel.number("/proc/sys/kernel/threads-max"),
        "threads_current": threads_current, "cgroup_ancestors": ancestors,
        "kernel": platform.release(), "ubuntu": release.get("VERSION_ID", "").strip('"'),
        "cgroup_nsdelegate": True,
    }


class Cgroup:
    def __init__(self, name, memory, pids):
        self.path = CGROOT / name
        if not name.startswith("vo-ci180-") or self.path.parent != CGROOT:
            raise policy.GuardError("invalid cgroup ownership name")
        self.memory, self.pids = memory, pids
        self.created = False

    def prepare(self):
        self.path.mkdir(mode=0o755)
        self.created = True
        for field, value in (("memory.max", self.memory), ("memory.swap.max", 0), ("memory.oom.group", 1), ("pids.max", self.pids)):
            (self.path / field).write_text(str(value))
            if kernel.number(self.path / field) != value:
                raise policy.GuardError("cgroup limit write did not become effective")
        for name in (*policy.CGROUP_FILES, "cgroup.kill"):
            info = (self.path / name).lstat()
            if info.st_uid != 0 or not stat.S_ISREG(info.st_mode):
                raise policy.GuardError("required root-owned cgroup control is unavailable")

    def snapshot(self):
        io = {}
        for line in kernel.read(self.path / "io.stat").decode().splitlines():
            parts = line.split()
            io[parts[0]] = {key: int(value) for key, value in (item.split("=") for item in parts[1:])}
        return {
            "memory_current": kernel.number(self.path / "memory.current"),
            "memory_peak": kernel.number(self.path / "memory.peak"),
            "memory_events": kernel.fields(self.path / "memory.events"),
            "pids_current": kernel.number(self.path / "pids.current"),
            "pids_peak": kernel.number(self.path / "pids.peak"),
            "pids_events": kernel.fields(self.path / "pids.events"),
            "cpu": kernel.fields(self.path / "cpu.stat"), "io": io,
            "populated": kernel.fields(self.path / "cgroup.events")["populated"],
        }

    def empty(self):
        return kernel.fields(self.path / "cgroup.events")["populated"] == 0 and not kernel.read(self.path / "cgroup.procs").strip()

    def kill(self):
        (self.path / "cgroup.kill").write_text("1")

    def remove(self):
        if not self.created:
            return
        if not self.empty():
            raise policy.GuardError("cgroup remains populated; preserve its resources")
        self.path.rmdir()
        self.created = False


class Volume:
    def __init__(self, directory, size, uid, gid):
        self.image = directory / "workspace.img"
        self.mountpoint = directory / "volume"
        self.size = size
        self.device = None
        self.mounted = False
        self.closed = False
        self.uncertain_device = False
        self.uid, self.gid = uid, gid
        self.filesystem_device = None
        self.mount_request = directory / "mount.json"
        self.mount_id = 0

    def mount_control(self, action):
        result = policy.parse_json(tool([
            "/usr/bin/python3", "-I", "-S", "-B", str(HERE / "volume_mount.py"),
            action, str(self.mount_request), str(self.mount_id),
        ], timeout=15)[1])
        if not isinstance(result, dict) or set(result) != {"state", "mount_id", "filesystem_device"}:
            raise policy.GuardError("volume helper returned an invalid observation")
        if result["state"] not in {"mounted", "absent"} or type(result["filesystem_device"]) is not int:
            raise policy.GuardError("volume state is not confirmed")
        if result["state"] == "mounted":
            if type(result["mount_id"]) is not int or result["mount_id"] <= 0:
                raise policy.GuardError("volume mount identity is invalid")
            self.mount_id = result["mount_id"]
        elif result["mount_id"] is not None:
            raise policy.GuardError("absent volume has a false mount identity")
        return result

    def prepare(self):
        self.mountpoint.mkdir()
        with self.image.open("xb"):
            pass
        tool(["/usr/bin/fallocate", "--length", str(self.size), str(self.image)])
        tool(["/usr/sbin/mkfs.ext4", "-q", "-F", "-m", "0", "-E",
              "lazy_itable_init=0,lazy_journal_init=0", str(self.image)], timeout=60)
        self.uncertain_device = True
        device = tool(["/usr/sbin/losetup", "--find", "--show", "--nooverlap", str(self.image)])[1].decode().strip()
        if not device.startswith("/dev/loop") or not device.removeprefix("/dev/loop").isdigit():
            raise policy.GuardError("loop allocation did not return an owned loop device")
        self.device = device
        request = volume_mount.make_spec(self.image, device, self.mountpoint, self.uid, self.gid)
        self.mount_request.write_bytes(policy.encoded(request))
        self.mount_request.chmod(0o444)
        self.uncertain_device = False
        self.mounted = None
        result = self.mount_control("mount")
        if result["state"] != "mounted":
            raise policy.GuardError("volume mount was not confirmed")
        self.mounted = True
        os.chown(self.mountpoint, self.uid, self.gid)
        self.filesystem_device = self.mountpoint.stat().st_dev
        observed = os.statvfs(self.mountpoint)
        if observed.f_blocks * observed.f_frsize > self.size:
            raise policy.GuardError("workspace filesystem exceeds its allocated image")

    def close(self):
        if self.closed:
            return
        if self.uncertain_device:
            raise policy.GuardError("loop allocation identity is unconfirmed; preserve its image")
        if self.mounted is not False:
            result = self.mount_control("inspect")
            if result["state"] == "mounted":
                result = self.mount_control("unmount")
            if result["state"] != "absent":
                raise policy.GuardError("volume cleanup remains uncertain")
            self.mounted = False
        if self.device is not None:
            tool(["/usr/sbin/losetup", "--detach", self.device], timeout=15)
            self.device = None
        self.image.unlink(missing_ok=True)
        self.mount_request.unlink(missing_ok=True)
        if self.mountpoint.exists():
            self.mountpoint.rmdir()
        self.closed = True


class OutputLimitExceeded(policy.GuardError):
    pass


class Protocol:
    def __init__(self, scope, maximum, *, raw_after_ready=False):
        self.scope, self.maximum = scope, maximum
        self.total = 0
        self.stderr_total = 0
        self.output_exceeded = False
        self.buffer = bytearray()
        self.ready = False
        self.raw_after_ready = raw_after_ready
        self.finished = False

    def observe_output(self, data, *, stderr=False):
        if stderr:
            self.stderr_total += len(data)
        else:
            self.total += len(data)
        if self.total + self.stderr_total > self.maximum:
            self.output_exceeded = True
            raise OutputLimitExceeded("external combined stdout/stderr bound exceeded")

    def feed(self, data):
        self.observe_output(data)
        if self.raw_after_ready and self.ready:
            return []
        self.buffer.extend(data)
        records = []
        while b"\n" in self.buffer:
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            record = policy.parse_json(line)
            if (
                not isinstance(record, dict) or set(record) != {"scope", "kind", "data"}
                or record["scope"] != self.scope or not isinstance(record["data"], dict)
                or record["kind"] not in {"ready", "graph-start", "progress", "error", "cleanup-error", "result", "probe-result", "escaped"}
            ):
                raise policy.GuardError("foreign or malformed diagnostic protocol record")
            kind = record["kind"]
            if self.finished or (not self.ready and kind not in {"ready", "error"}):
                raise policy.GuardError("diagnostic record is out of order")
            if kind == "ready":
                if self.ready:
                    raise policy.GuardError("duplicate containment readiness")
                self.ready = True
            if kind in {"result", "probe-result"}:
                self.finished = True
            records.append(record)
            if self.raw_after_ready and self.ready:
                self.buffer.clear()
                break
        return records


def phase(owner, mode, volume, *, memory, pids, seconds):
    group = Cgroup(f"vo-ci180-{owner.scope['run_id']}-{mode}", memory, pids)
    owner.groups.append(group)
    group.prepare()
    directory = owner.control / mode
    directory.mkdir()
    scope = owner.scope["run_id"] + "/" + mode
    deadline = time.monotonic() + seconds
    config = {
        "scope": scope, "mode": mode, "cgroup": str(group.path), "cgroup_relative": "/" + group.path.name,
        "uid": owner.uid, "gid": owner.gid, "memory_max": memory, "pids_max": pids,
        "deadline": deadline, "disk_bytes": volume.size,
        "workspace_device": volume.filesystem_device, "volume": str(volume.mountpoint),
        "candidate": str(owner.candidate), "harness_code": str(HERE),
        "rootfs": str(directory / "rootfs"), "etc": str(owner.etc),
        "namespace_helper": str(owner.harness / "scripts/validation_ownership/sandbox_exec.py"),
        "host_namespaces": kernel.namespaces(), "host_listener_port": owner.listener.getsockname()[1],
        "apparmor_profile": owner.apparmor_profile,
        "tracked_paths": owner.tracked_paths,
        "runtime_manifest": owner.runtime_manifest,
        "cgroup_file_identities": {
            name: [(group.path / name).stat().st_dev, (group.path / name).stat().st_ino]
            for name in policy.CGROUP_FILES
        },
    }
    path = directory / "config.json"
    path.write_bytes(policy.encoded(config))
    path.chmod(0o444)
    argv = [
        "/usr/bin/unshare", "--mount", "--net", "--ipc", "--uts", "--pid", "--fork",
        "--kill-child", "--propagation", "private",
        "/usr/bin/python3", "-I", "-S", "-B", str(HERE / "entry.py"), str(path),
    ]
    if owner.apparmor_profile is not None:
        argv = ["/usr/bin/aa-exec", "-p", owner.apparmor_profile, "--", *argv]
    reader, writer = os.pipe2(os.O_CLOEXEC)
    child = None
    protocol = Protocol(scope, 65536 if mode == "output" else policy.OUTPUT_BYTES, raw_after_ready=mode == "output")
    result = {"mode": mode, "deadline": deadline, "graph_check_attempts": 0}
    cause = None
    last_sample = 0
    previous_io = {}
    sampled_io_peaks = {}
    sampled_disk_peak = 0
    lifetime_closed = False
    held = []
    try:
        child = subprocess.Popen(
            ["/usr/bin/python3", "-I", "-S", "-B", str(LIFECYCLE), str(deadline), "--", *argv],
            stdin=reader, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ROOT_ENV, close_fds=True,
        )
        os.close(reader)
        reader = None
        with selectors.DefaultSelector() as selector:
            for index, stream in enumerate((child.stdout, child.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, index)
            while selector.get_map():
                now = time.monotonic()
                if now - last_sample >= policy.SAMPLE_SECONDS:
                    sample = group.snapshot()
                    disk = os.statvfs(volume.mountpoint)
                    sample["disk_used_bytes"] = (disk.f_blocks - disk.f_bfree) * disk.f_frsize
                    sample["disk_available_bytes"] = disk.f_bavail * disk.f_frsize
                    sampled_disk_peak = max(sampled_disk_peak, sample["disk_used_bytes"])
                    if last_sample:
                        io_peaks(previous_io, sample["io"], now - last_sample, sampled_io_peaks)
                    previous_io = sample["io"]
                    owner.artifacts.append("metrics.jsonl", {"scope": scope, "monotonic": now, **sample})
                    last_sample = now
                    if sample["memory_events"].get("oom_kill", 0) and cause is None:
                        cause = {"type": "cgroup-oom", "message": "kernel memory.max/oom.group termination"}
                if now >= deadline and (cause is None or cause.get("type") == "lifetime-eof"):
                    cause = {"type": "deadline", "message": "original absolute phase deadline exhausted"}
                if protocol.output_exceeded or cause is not None and cause.get("type") != "lifetime-eof":
                    group.kill()
                    if writer is not None:
                        os.close(writer)
                        writer = None
                for key, _ in selector.select(0.05):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    if key.data == 1:
                        try:
                            protocol.observe_output(data, stderr=True)
                        except OutputLimitExceeded as error:
                            cause = cause or {"type": "output-bound", "message": str(error)}
                            continue
                        if not protocol.ready and protocol.stderr_total <= policy.ERROR_BYTES:
                            result["trusted_setup_stderr"] = result.get("trusted_setup_stderr", "") + data.decode("utf-8", "replace")
                        if cause is None:
                            cause = {
                                "type": "deadline" if time.monotonic() >= deadline else
                                "lifetime-eof" if lifetime_closed else "unexpected-stderr",
                                "message": "Expected caller lifetime closure" if lifetime_closed else
                                "Unframed stderr; raw candidate/scratch output is not published",
                            }
                        continue
                    try:
                        records = protocol.feed(data)
                    except OutputLimitExceeded as error:
                        cause = cause or {"type": "output-bound", "message": str(error)}
                        continue
                    except policy.GuardError as error:
                        cause = cause or {"type": "protocol", "message": str(error)}
                        continue
                    for record in records:
                        kind, value = record["kind"], record["data"]
                        if kind == "error":
                            cause = cause or {"type": "worker-error", "error": value}
                        elif kind == "cleanup-error":
                            result.setdefault("cleanup_errors", []).append(value)
                            cause = cause or {"type": "worker-cleanup", "error": value}
                        elif kind == "result":
                            result["worker"] = value
                        elif kind == "probe-result":
                            result["probe"] = value
                        elif kind == "ready":
                            result["identity"] = value
                        elif kind == "graph-start":
                            result["graph_check_attempts"] += 1
                            if mode != "graph" or result["graph_check_attempts"] != 1:
                                raise policy.GuardError("graph invocation violated the closed single-attempt scope")
                            owner.scope["graph_check_attempted"] = True
                            owner.artifacts.write("scope.json", owner.scope)
                            owner.artifacts.append("progress.jsonl", record)
                        elif kind == "escaped":
                            result["escaped"] = value
                            for pid in kernel.read(group.path / "cgroup.procs").decode().split():
                                held.append(os.pidfd_open(int(pid)))
                            if mode == "lifetime":
                                os.close(writer)
                                writer = None
                                lifetime_closed = True
                        else:
                            owner.artifacts.append("progress.jsonl", record)
                if now > deadline + 10:
                    raise policy.GuardError("watchdog/stream termination is unconfirmed")
        result["returncode"] = child.wait(timeout=5)
        result["empty_before_outer_cleanup"] = group.empty()
        if time.monotonic() >= deadline and cause is None:
            cause = {"type": "deadline", "message": "phase terminated at the original deadline"}
    except BaseException as error:
        result["supervisor_error"] = policy.error_record(error)
        cause = cause or {"type": "supervisor-error", "error": result["supervisor_error"]}
    finally:
        group.kill()
        if writer is not None:
            os.close(writer)
        if reader is not None:
            os.close(reader)
        if child is not None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                raise policy.GuardError("watchdog cleanup unconfirmed; retain containment resources") from error
            child.stdout.close()
            child.stderr.close()
            result.setdefault("returncode", child.returncode)
        until = time.monotonic() + 5
        while not group.empty() and time.monotonic() < until:
            time.sleep(0.02)
        if not group.empty():
            raise policy.GuardError("owned cgroup remains populated after kill; retain workspace")
        with selectors.DefaultSelector() as selector:
            for descriptor in held:
                selector.register(descriptor, selectors.EVENT_READ)
            if held and len(selector.select(0)) != len(held):
                raise policy.GuardError("escaped descendant pidfds did not become terminal")
        for descriptor in held:
            os.close(descriptor)
        result["kernel"] = group.snapshot()
        disk = os.statvfs(volume.mountpoint)
        result["kernel"]["disk_used_bytes"] = (disk.f_blocks - disk.f_bfree) * disk.f_frsize
        result["kernel"]["disk_available_bytes"] = disk.f_bavail * disk.f_frsize
        result["sampled_disk_peak_bytes"] = max(sampled_disk_peak, result["kernel"]["disk_used_bytes"])
        if last_sample:
            io_peaks(previous_io, result["kernel"]["io"], time.monotonic() - last_sample, sampled_io_peaks)
        result["sampled_io_peak_rates"] = sampled_io_peaks
        result["io_peak_semantics"] = "Maximum observed interval rates, not an unsampled instantaneous kernel peak."
        result["empty"] = True
        result["watchdog_reaped"] = child is None or child.returncode is not None
        result["lifetime_writer_closed"] = True
        result["caller_lifetime_control_exercised"] = lifetime_closed
        result["held_descendants_terminal"] = len(held)
        result["stdout_bytes"] = protocol.total
        result["stderr_bytes"] = protocol.stderr_total
        result["output_bytes"] = protocol.total + protocol.stderr_total
        result["output_exceeded"] = protocol.output_exceeded
        result["first_cause"] = cause
        owner.artifacts.append("metrics.jsonl", {"scope": scope, "terminal": True, **result["kernel"]})
    return result


def validate_probe(result):
    mode = result["mode"]
    if mode not in {"identity", "memory", "pids", "disk", "output", "deadline", "lifetime"}:
        raise policy.GuardError("graph or unknown mode is not an expected benign negative")
    if result.get("supervisor_error"):
        raise policy.GuardError("benign containment probe had a supervisor failure")
    if "identity" not in result or not result.get("empty") or not result.get("watchdog_reaped"):
        raise policy.GuardError("benign probe did not establish identity and terminal cleanup")
    if mode != "output" and result.get("output_exceeded"):
        raise policy.GuardError("unexpected output overflow outside the dedicated output negative")
    if mode in {"identity", "pids", "disk"}:
        if result.get("returncode") != 0 or result.get("first_cause") or "probe" not in result:
            raise policy.GuardError(f"required benign {mode} probe failed")
        if mode == "identity":
            identity = result["identity"]
            policy.validate_proc_protection(result["probe"].get("proc_protection"))
            policy.validate_nested_probe(
                result["probe"].get("nested"), uid=identity["uid"], gid=identity["gid"],
                group=identity["cgroup"], parent_user_namespace=identity["namespaces"]["user"],
            )
    elif mode == "memory":
        if result["kernel"]["memory_events"].get("oom_kill", 0) < 1:
            raise policy.GuardError("kernel memory rejection was not proved")
    elif mode == "output":
        if (
            (result.get("first_cause") or {}).get("type") != "output-bound"
            or result.get("output_exceeded") is not True or result["output_bytes"] <= 65536
        ):
            raise policy.GuardError("external output rejection was not proved")
    elif mode in {"deadline", "lifetime"}:
        if not result.get("escaped") or result["held_descendants_terminal"] < 2:
            raise policy.GuardError("real escaped-descendant cleanup was not proved")
        if mode == "deadline" and (result.get("first_cause") or {}).get("type") != "deadline":
            raise policy.GuardError("independent deadline control was not proved")
        if mode == "lifetime" and (
            not result["caller_lifetime_control_exercised"]
            or not result.get("empty_before_outer_cleanup")
            or (result.get("first_cause") or {}).get("type") not in {None, "lifetime-eof"}
        ):
            raise policy.GuardError("lifetime-pipe reaper did not independently empty the cgroup")


class Owner:
    def __init__(self, arguments, scope, artifacts):
        self.harness = Path(arguments.harness).resolve(strict=True)
        self.candidate = Path(arguments.candidate).resolve(strict=True)
        self.scope, self.artifacts = scope, artifacts
        self.groups = []
        self.volumes = []
        self.apparmor_profile = None
        self.account = None
        self.uid = self.gid = None
        self.candidate_owner = None
        self.listener = None
        self.control = Path(tempfile.mkdtemp(prefix="vo-ci180-" + scope["run_id"] + "-", dir=artifacts.root.parent))
        self.etc = self.control / "etc"
        self.etc.mkdir()
        self.tracked_paths = 0
        self.gitlinks = []
        self.runtime_manifest = None

    def prepare(self):
        if os.geteuid() != 0:
            raise policy.GuardError("hosted containment setup requires its narrow root supervisor")
        if HERE != self.harness / "scripts/ci_calibration" or LIFECYCLE.resolve() != (
            self.harness / "scripts/validation_ownership/lifecycle.py"
        ).resolve():
            raise policy.GuardError("executing harness differs from the workflow checkout")
        if self.harness == self.candidate or self.harness in self.candidate.parents or self.candidate in self.harness.parents:
            raise policy.GuardError("harness and candidate must be separate checkouts")
        if git(self.harness, "rev-parse", "HEAD").decode().strip() != self.scope["harness_sha"]:
            raise policy.GuardError("harness does not match the actual workflow SHA")
        if git(self.harness, "status", "--porcelain=v1", "--untracked-files=all").strip():
            raise policy.GuardError("workflow harness has uncommitted source changes")
        if git(self.harness, "rev-parse", "HEAD^").decode().strip() != policy.BASE:
            raise policy.GuardError("diagnostic must be one non-delivery commit directly on the frozen base")
        changed = git(self.harness, "diff", "--name-only", "-z", policy.BASE, "HEAD").split(b"\0")
        if any(
            name and name.decode() != policy.WORKFLOW and not name.decode().startswith("scripts/ci_calibration/")
            for name in changed
        ):
            raise policy.GuardError("diagnostic branch modified a production surface")
        self.source_status("before")
        tree = git(self.candidate, "ls-tree", "-rz", "--full-tree", policy.GRAPH)
        self.tracked_paths = len([row for row in tree.split(b"\0") if row])
        if git(self.candidate, "ls-tree", "-r", "--name-only", policy.GRAPH, "--", "build", "scripts/ci_calibration", policy.WORKFLOW).strip():
            raise policy.GuardError("source inventory overlaps writable build or diagnostic harness paths")
        for root in (self.harness, self.candidate, *(self.candidate / name for name in self.gitlinks)):
            found = git(root, "config", "--local", "--name-only", "--get-regexp",
                        r"^(credential\.|http\..*extraheader|url\..*insteadof|include\.|includeif\.|core\.sshcommand)",
                        allowed=(0, 1))
            if found.strip():
                raise policy.GuardError("checkout persisted credential configuration")
        self.runtime_manifest = runtime_view.observe()
        runtime_view.validate(self.runtime_manifest)
        self.scope["host_runtime_manifest"] = self.runtime_manifest
        controllers = set(kernel.read(CGROOT / "cgroup.controllers").decode().split())
        if not {"memory", "pids", "io"} <= controllers:
            raise policy.GuardError("required cgroup memory/PID/IO controllers are unavailable")
        enabled = set(kernel.read(CGROOT / "cgroup.subtree_control").decode().split())
        missing = {"memory", "pids", "io"} - enabled
        if missing:
            raise policy.GuardError("host controllers must already be enabled; no global cgroup-policy fallback")
        name = "vo180-" + self.scope["run_id"]
        try:
            pwd.getpwnam(name)
        except KeyError:
            pass
        else:
            raise policy.GuardError("one-shot worker identity already exists")
        tool(["/usr/sbin/useradd", "--system", "--no-create-home", "--user-group",
              "--shell", "/usr/sbin/nologin", name])
        self.account = name
        account = pwd.getpwnam(name)
        self.uid, self.gid = account.pw_uid, account.pw_gid
        if self.uid <= 0 or self.gid <= 0:
            raise policy.GuardError("worker account is not unprivileged")
        current = self.candidate.stat()
        self.candidate_owner = current.st_uid, current.st_gid
        build = self.candidate / "build"
        if build.exists() and (build.is_symlink() or not build.is_dir()):
            raise policy.GuardError("candidate build mountpoint is not a real directory")
        build.mkdir(exist_ok=True)
        tool(["/usr/bin/chown", "-R", "--no-dereference", f"{self.uid}:{self.gid}", str(self.candidate)], timeout=60)
        for name, content in (
            ("passwd", f"root:x:0:0:root:/nonexistent:/usr/sbin/nologin\ncalibration:x:{self.uid}:{self.gid}:diagnostic:/nonexistent:/usr/sbin/nologin\n"),
            ("group", f"root:x:0:\ncalibration:x:{self.gid}:\n"),
            ("nsswitch.conf", "passwd: files\ngroup: files\nhosts: files\n"),
            ("hosts", "127.0.0.1 localhost\n"),
        ):
            path = self.etc / name
            path.write_text(content)
            path.chmod(0o444)
        enabled_file = Path("/sys/module/apparmor/parameters/enabled")
        if enabled_file.exists() and kernel.read(enabled_file).strip() == b"Y":
            if kernel.read("/proc/self/attr/current").strip() != b"unconfined":
                raise policy.GuardError("refusing to replace an already confined supervisor AppArmor policy")
            name = "vo-ci180-" + self.scope["run_id"]
            profile = self.control / "apparmor.profile"
            profile.write_text(apparmor_text(name))
            tool(["/usr/sbin/apparmor_parser", "--skip-cache", "--jobs", "1", "--max-jobs", "1",
                  "--replace", str(profile)])
            self.apparmor_profile = name
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)

    def source_status(self, label):
        if git(self.candidate, "rev-parse", "HEAD").decode().strip() != policy.GRAPH:
            raise policy.GuardError("candidate HEAD differs from the immutable workload")
        if git(self.candidate, "rev-parse", policy.BASE + "^{commit}").decode().strip() != policy.BASE:
            raise policy.GuardError("candidate lacks the exact BASE history")
        if git(self.candidate, "status", "--porcelain=v1", "--untracked-files=all").strip():
            raise policy.GuardError("candidate source is not clean")
        gitlinks = []
        for record in git(self.candidate, "ls-tree", "-rz", "--full-tree", policy.GRAPH).split(b"\0"):
            if record and record.startswith(b"160000 commit "):
                path = record.split(b"\t", 1)[1].decode("utf-8", "strict")
                if path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/")):
                    raise policy.GuardError("noncanonical captured gitlink")
                gitlinks.append(path)
        self.gitlinks = gitlinks
        if any(line[:1] in {b"-", b"+", b"U"} for line in git(
            self.candidate, "submodule", "status", "--recursive", safe_children=gitlinks,
        ).splitlines()):
            raise policy.GuardError("candidate submodules are missing or differ from their Git pins")
        self.scope["source_status_" + label] = "exact HEAD/BASE and clean with initialized pinned submodules"

    def volume(self, name, size):
        directory = self.control / name
        directory.mkdir()
        volume = Volume(directory, size, self.uid, self.gid)
        self.volumes.append(volume)
        volume.prepare()
        return volume

    def cleanup(self):
        failures = []
        for group in self.groups:
            if not group.created:
                continue
            try:
                if not group.empty():
                    group.kill()
                    until = time.monotonic() + 5
                    while not group.empty() and time.monotonic() < until:
                        time.sleep(0.02)
                if not group.empty():
                    raise policy.GuardError("unconfirmed cgroup population")
            except (OSError, policy.GuardError) as error:
                failures.append(str(error))
        if failures:
            raise policy.GuardError("preserve all owned resources: " + "; ".join(failures))
        for volume in reversed(self.volumes):
            volume.close()
        for group in reversed(self.groups):
            group.remove()
        if self.candidate_owner is not None:
            uid, gid = self.candidate_owner
            tool(["/usr/bin/chown", "-R", "--no-dereference", f"{uid}:{gid}", str(self.candidate)], timeout=60)
        if self.apparmor_profile is not None:
            tool(["/usr/sbin/apparmor_parser", "--skip-cache", "--jobs", "1", "--max-jobs", "1",
                  "--remove", str(self.control / "apparmor.profile")])
        if self.account is not None:
            tool(["/usr/sbin/userdel", self.account])
        if self.listener is not None:
            self.listener.close()
        shutil.rmtree(self.control)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "run"))
    for name in ("harness", "candidate", "output", "event", "sha", "event-name", "run-id",
                 "attempt", "run-number", "runner-environment", "runner-os"):
        parser.add_argument("--" + name, required=True)
    return parser.parse_args()


def main():
    args = arguments()
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        raise policy.GuardError("supervisor requires isolated no-site startup")
    event = policy.parse_json(kernel.read(args.event, policy.MIB))
    scope = policy.validate_event(
        event, sha=args.sha, run_id=args.run_id, attempt=args.attempt, run_number=args.run_number,
        environment=args.runner_environment, operating_system=args.runner_os, event_name=args.event_name,
    )
    output = Path(args.output).absolute()
    if output.name != "issue180-ci-baseline-6-" + args.run_id or output.is_symlink():
        raise policy.GuardError("output does not identify the single owned artifact directory")
    artifacts = Artifacts(output)
    if args.operation == "plan":
        if (output / "scope.json").exists():
            raise policy.GuardError("one-shot scope already exists")
        scope.update(
            planned_at_monotonic=time.monotonic(), graph_launch_requested=False,
            graph_check_attempted=False, graph_check_completed=False,
            policy=policy.profile_manifest(policy.ORIGINAL_LIMITS),
            output_allowlist=list(policy.ARTIFACT_NAMES),
        )
        artifacts.write("scope.json", scope)
        return 0
    previous = policy.parse_json(kernel.read(output / "scope.json", policy.OUTPUT_BYTES))
    if any(previous.get(key) != value for key, value in scope.items()) or previous.get("graph_launch_requested") is not False:
        raise policy.GuardError("run does not match the unspent planned scope")
    scope = previous
    if (output / "result.json").exists() or (output / "preflight.json").exists():
        raise policy.GuardError("one-shot scope already has an attempted execution")
    owner = None
    first = None
    cleanup_error = None
    result = None
    preflight = []
    started = time.monotonic()
    artifacts.write("result.json", {
        "status": "preflight-started", "diagnostic_only": True,
        "production_acceptance": False, "graph_launch_requested": False,
    })
    try:
        if os.geteuid() != 0:
            raise policy.GuardError("run requires the hosted root supervisor")
        if started - previous["planned_at_monotonic"] > 15 * 60:
            raise policy.GuardError("setup consumed the reserved job/cleanup margin; graph will not start")
        os.chown(output, 0, 0)
        facts = capacity_facts(output.parent)
        policy.choose_envelope(facts)
        scope["preflight_runner_facts"] = facts
        owner = Owner(args, scope, artifacts)
        owner.prepare()
        artifacts.write("scope.json", scope)
        probe_volume = owner.volume("probe-volume", 64 * policy.MIB)
        for mode in ("identity", "memory", "pids", "disk", "output", "deadline", "lifetime"):
            if time.monotonic() - started > 5 * 60:
                raise policy.GuardError("preflight exceeded its external setup allowance")
            observed = phase(
                owner, mode, probe_volume,
                memory=64 * policy.MIB if mode == "memory" else 256 * policy.MIB,
                pids=8 if mode == "pids" else 64,
                seconds=2 if mode == "deadline" else 30,
            )
            preflight.append(observed)
            artifacts.write("preflight.json", {"status": "qualifying", "probes": preflight})
            validate_probe(observed)
        artifacts.write("preflight.json", {"status": "qualified", "probes": preflight})
        probe_volume.close()
        facts = capacity_facts(output.parent)
        envelope = policy.choose_envelope(facts)
        scope.update(runner_facts=facts, envelope=envelope)
        graph_volume = owner.volume("graph-volume", envelope["disk_bytes"])
        if time.monotonic() - started > 5 * 60:
            raise policy.GuardError("preflight/setup exceeded its reserved margin; graph will not start")
        scope["graph_launch_requested"] = True
        artifacts.write("scope.json", scope)
        result = phase(
            owner, "graph", graph_volume, memory=envelope["memory_max"],
            pids=envelope["pids_max"], seconds=policy.GRAPH_SECONDS,
        )
        if result.get("first_cause") or result.get("returncode") != 0 or result["graph_check_attempts"] != 1:
            raise policy.GuardError("single graph invocation failed; original phase cause is preserved")
        checked = policy.validate_report(result["worker"]["report"])
        if result["worker"]["report"]["coverage"]["tracked_paths"] != owner.tracked_paths:
            raise policy.GuardError("report coverage differs from the actual pinned Git inventory")
        if result["worker"].get("graph_check_completed") is not True:
            raise policy.GuardError("worker did not complete the real graph check")
        scope["graph_check_completed"] = True
        artifacts.write("report.json", result["worker"]["report"])
        result["validation"] = checked
        del result["worker"]["report"]
    except BaseException as error:
        observed_cause = result.get("first_cause") if result is not None else (
            preflight[-1].get("first_cause") if preflight else None
        )
        first = observed_cause or policy.error_record(error)
    finally:
        if owner is not None:
            try:
                owner.cleanup()
            except BaseException as error:
                cleanup_error = policy.error_record(error)
            try:
                owner.source_status("after")
            except BaseException as error:
                scope["source_status_after_error"] = policy.error_record(error)
                if first is None:
                    first = scope["source_status_after_error"]
        artifacts.write("scope.json", scope)
        artifacts.write("result.json", {
            "status": "completed-diagnostic-only" if first is None and cleanup_error is None else "failed",
            "diagnostic_only": True, "production_acceptance": False,
            "first_error": first, "phase": result, "cleanup_error": cleanup_error,
            "cleanup_confirmed": cleanup_error is None,
            "elapsed_seconds": time.monotonic() - started,
        })
    return 0 if first is None and cleanup_error is None else 1


if __name__ == "__main__":
    def interrupted(signum, _frame):
        raise policy.GuardError(f"outer supervisor interrupted by signal {signum}")

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        raise SystemExit(main())
    except (OSError, ValueError, policy.GuardError) as error:
        print(f"issue180 diagnostic supervisor: {error}", file=sys.stderr)
        raise SystemExit(1)
