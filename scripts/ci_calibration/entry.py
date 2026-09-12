"""Trusted root-only namespace setup; candidate imports happen only after exec."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import sys
import time

if __package__:
    from . import kernel, policy, runtime_view
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kernel
    import policy
    import runtime_view


def load_namespace_helper(path):
    spec = importlib.util.spec_from_file_location("ci180_trusted_namespace_setup", path)
    if spec is None or spec.loader is None:
        raise policy.GuardError("trusted namespace helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(config_path, config):
    if os.geteuid() != 0 or config["uid"] <= 0 or config["gid"] <= 0:
        raise policy.GuardError("trusted setup requires root, followed by a non-root worker")
    if time.monotonic() >= config["deadline"]:
        raise policy.GuardError("namespace setup exhausted its original deadline")
    group = Path(config["cgroup"])
    if group.parent != Path("/sys/fs/cgroup") or not group.name.startswith("vo-ci180-"):
        raise policy.GuardError("invalid owned cgroup path")
    if kernel.filesystem_type(group) != kernel.CGROUP2_MAGIC:
        raise policy.GuardError("owned scope is not cgroup-v2")
    (group / "cgroup.procs").write_text("0")
    if kernel.membership() != "/" + group.name:
        raise policy.GuardError("trusted setup did not enter its configured cgroup")
    if any(kernel.namespaces()[name] == config["host_namespaces"][name] for name in policy.NAMESPACES):
        raise policy.GuardError("outer namespaces were not established before setup")
    if kernel.namespaces()["user"] != config["host_namespaces"]["user"]:
        raise policy.GuardError("coordinator must preserve host-root runtime ownership visibility")
    namespace = load_namespace_helper(config["namespace_helper"])
    actual_runtime = runtime_view.observe()
    runtime_view.validate(actual_runtime)
    runtime_view.compare(config["runtime_manifest"], actual_runtime)
    root = Path(config["rootfs"])
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    for name in ("repo", "usr", "etc/alternatives", "work", "tmp", "run", "var/tmp", "proc", "sys", "dev", "guard", "diag"):
        (root / name).mkdir(parents=True, exist_ok=True)
    for name in ("bin", "sbin", "lib", "lib64"):
        (root / name).symlink_to(config["runtime_manifest"]["root_aliases"][name]["target"])
    for name in policy.CGROUP_FILES:
        (root / "guard" / name).touch()
    (root / "guard/config.json").touch()
    for name in ("passwd", "group", "nsswitch.conf", "hosts"):
        (root / "etc" / name).touch()
    if Path("/etc/ld.so.cache").is_file():
        (root / "etc/ld.so.cache").touch()
    namespace.bind(root, root, executable=True)
    namespace.bind("/usr", root / "usr", executable=True)
    namespace.bind("/etc/alternatives", root / "etc/alternatives")
    namespace.bind(config["candidate"], root / "repo")
    namespace.bind(config["harness_code"], root / "diag")
    namespace.bind(config_path, root / "guard/config.json")
    for name in policy.CGROUP_FILES:
        namespace.bind(group / name, root / "guard" / name)
    for name in ("passwd", "group", "nsswitch.conf", "hosts"):
        namespace.bind(Path(config["etc"]) / name, root / "etc" / name)
    if Path("/etc/ld.so.cache").is_file():
        namespace.bind("/etc/ld.so.cache", root / "etc/ld.so.cache")
    volume = Path(config["volume"])
    for name in ("build", "tmp", "run", "var-tmp", "shm"):
        destination = volume / name
        destination.mkdir(exist_ok=True)
        os.chown(destination, config["uid"], config["gid"])
    namespace.bind(volume, root / "work", writable=True, executable=True)
    for source, target in (("build", "repo/build"), ("tmp", "tmp"), ("run", "run"), ("var-tmp", "var/tmp")):
        namespace.bind(volume / source, root / target, writable=True, executable=True)
    kernel.mount_tmpfs(root / "dev", size=policy.MIB, flags=namespace.MS_NOSUID | namespace.MS_NOEXEC)
    for name, minor in (("null", 3), ("zero", 5), ("random", 8), ("urandom", 9)):
        os.mknod(root / "dev" / name, stat.S_IFCHR | 0o666, os.makedev(1, minor))
        (root / "dev" / name).chmod(0o666)
    (root / "dev/shm").mkdir()
    namespace.recursive_attributes(root / "dev", namespace.MS_RDONLY | namespace.MS_NOSUID | namespace.MS_NOEXEC)
    namespace.bind(volume / "shm", root / "dev/shm", writable=True, executable=True)
    namespace.mount("proc", root / "proc", namespace.MS_NOSUID | namespace.MS_NODEV | namespace.MS_NOEXEC, "proc")
    (root / "proc/self/oom_score_adj").write_text("0")
    namespace.recursive_attributes(
        root / "proc", namespace.MS_RDONLY | namespace.MS_NOSUID | namespace.MS_NODEV | namespace.MS_NOEXEC,
    )
    kernel.pivot_root(root)
    os.chdir("/repo")
    os.umask(0o022)
    os.closerange(3, 65536)
    null = os.open("/dev/null", os.O_RDONLY)
    os.dup2(null, 0)
    if null > 2:
        os.close(null)
    kernel.drop_identity(config["uid"], config["gid"])
    os.execve(
        "/usr/bin/python3",
        ["/usr/bin/python3", "-I", "-S", "-B", "/diag/worker.py", "/guard/config.json"],
        policy.CLEAN_ENV,
    )


if __name__ == "__main__":
    if not sys.flags.isolated or not sys.flags.no_site or len(sys.argv) != 2:
        raise SystemExit("trusted entry requires isolated Python and one owned config")
    current = None
    try:
        current = kernel.owned_config(sys.argv[1])
        main(sys.argv[1], current)
    except BaseException as error:
        try:
            if current is None:
                raise policy.GuardError("no admitted config")
            kernel.emit(current["scope"], "error", policy.error_record(error))
        except (OSError, policy.GuardError, ValueError):
            print("trusted namespace setup failed before a bounded diagnostic channel", file=sys.stderr)
        raise SystemExit(1)
