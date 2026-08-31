#!/usr/bin/env python3
"""Authoritative, nonexecuting GNU Make authority probe."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable


MAKE = Path("/usr/bin/make")
UNSHARE = Path("/usr/bin/unshare")
SUDO = Path("/usr/bin/sudo")
PYTHON = Path("/usr/bin/python3")
CC = Path("/usr/bin/cc")
LIBC = Path("/lib/x86_64-linux-gnu/libc.so.6")
LOADER = Path("/lib64/ld-linux-x86-64.so.2")
SANDBOX_EXEC = Path("scripts/validation_ownership/sandbox_exec.py")
INTERCEPTOR_SOURCE = Path(
    "scripts/validation_ownership/shell_interceptor.c"
)
MAX_SANDBOX_RUNS = 4096
MAX_DYNAMIC_PASSES = 64
TRACE_RE = re.compile(
    r"^(?P<source>.+?):[0-9]+: "
    r"(?:(?:update )?target) '(?P<target>[^']+)'"
    r"(?: due to: (?P<due>.*))?$"
)
CONSIDER_RE = re.compile(
    r"^(?P<indent> *)Considering target file '(?P<target>[^']+)'\.$"
)
READING_RE = re.compile(
    r"^Reading makefile '(?P<path>[^']+)'"
)


class MakeProbeError(RuntimeError):
    """Raised when GNU Make authority cannot be observed safely and exactly."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_NAMESPACE_LAUNCHER: dict[str, Any] | None = None


def _namespace_probe_command(*, sudo: bool) -> list[str]:
    command = [
        str(UNSHARE),
        "--mount",
        "--net",
        "--pid",
        "--fork",
        "--kill-child",
        "--propagation",
        "private",
        "/usr/bin/true",
    ]
    if sudo:
        return [str(SUDO), "-n", *command]
    return [
        str(UNSHARE),
        "--user",
        "--map-root-user",
        *command[1:],
    ]


def _tool_version(path: Path) -> str:
    completed = subprocess.run(
        [str(path), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        },
    )
    output = completed.stdout or completed.stderr
    if completed.returncode != 0 or not output.strip():
        raise MakeProbeError(f"cannot identify trusted tool {path}")
    return output.splitlines()[0]


def _select_namespace_launcher(*, refresh: bool = False) -> dict[str, Any]:
    global _NAMESPACE_LAUNCHER
    if _NAMESPACE_LAUNCHER is not None and not refresh:
        return _NAMESPACE_LAUNCHER
    probe_environment = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }
    user_probe = subprocess.run(
        _namespace_probe_command(sudo=False),
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env=probe_environment,
    )
    if user_probe.returncode == 0:
        mode = "user-namespace"
        prefix = _namespace_probe_command(sudo=False)[:-1]
    else:
        if not SUDO.is_file():
            raise MakeProbeError(
                "unprivileged user namespaces are unavailable and "
                "/usr/bin/sudo is absent"
            )
        sudo_probe = subprocess.run(
            _namespace_probe_command(sudo=True),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=probe_environment,
        )
        if sudo_probe.returncode != 0:
            raise MakeProbeError(
                "neither unprivileged nor passwordless-sudo namespace "
                "confinement is available"
            )
        mode = "sudo-drop"
        prefix = _namespace_probe_command(sudo=True)[:-1]
    result = {
        "argv_prefix": prefix,
        "mode": mode,
        "runner_gid": os.getgid(),
        "runner_uid": os.getuid(),
        "unshare": str(UNSHARE),
        "unshare_sha256": _sha256_file(UNSHARE),
        "unshare_version": _tool_version(UNSHARE),
    }
    if mode == "sudo-drop":
        result.update(
            {
                "sudo": str(SUDO),
                "sudo_sha256": _sha256_file(SUDO),
                "sudo_version": _tool_version(SUDO),
            }
        )
    _NAMESPACE_LAUNCHER = result
    return result


def _ensure_tools() -> dict[str, Any]:
    required = (MAKE, UNSHARE, PYTHON, CC, LIBC, LOADER)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise MakeProbeError(
            "authoritative Make sandbox tools are unavailable: "
            + ", ".join(missing)
        )
    completed = subprocess.run(
        [str(MAKE), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        },
    )
    if completed.returncode != 0 or not completed.stdout.startswith("GNU Make "):
        raise MakeProbeError("trusted /usr/bin/make version probe failed")
    return {
        "make": str(MAKE),
        "make_sha256": _sha256_file(MAKE),
        "make_version": completed.stdout.splitlines()[0],
        "namespace_launcher": _select_namespace_launcher(),
    }


def _mkdir_target(root: Path, target: str, *, directory: bool = False) -> Path:
    path = root / target.lstrip("/")
    if directory:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return path


def _sandbox_run(
    root: Path,
    work: Path,
    *,
    argv: list[str],
    environment: dict[str, str],
    read_only: list[tuple[Path, str]],
    writable: list[tuple[Path, str]] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    config = {
        "argv": argv,
        "cwd": "/repo",
        "environment": environment,
        "read_only": [
            [str(source.resolve(strict=True)), target]
            for source, target in read_only
        ],
        "root": str(root.resolve(strict=True)),
        "sudo_drop": _select_namespace_launcher()["mode"] == "sudo-drop",
        "runner_gid": _select_namespace_launcher()["runner_gid"],
        "runner_uid": _select_namespace_launcher()["runner_uid"],
        "writable": [
            [str(source.resolve(strict=True)), target]
            for source, target in (
                [(work, "/work")]
                + ([] if writable is None else writable)
            )
        ],
    }
    config_path = work.parent / "sandbox.json"
    config_path.write_text(
        json.dumps(config, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    command = [
        *_select_namespace_launcher()["argv_prefix"],
        str(PYTHON),
        "-I",
        str((read_only[0][0] / SANDBOX_EXEC).resolve(strict=True)),
        str(config_path),
    ]
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "UTC",
        },
    )


def _compile_interceptor(repository: Path, output: Path) -> dict[str, str]:
    source = repository / INTERCEPTOR_SOURCE
    completed = subprocess.run(
        [
            str(CC),
            "-std=c11",
            "-O2",
            "-static",
            str(source),
            "-o",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        },
    )
    if completed.returncode != 0:
        raise MakeProbeError(
            "cannot compile the trusted Make shell interceptor: "
            + completed.stderr.strip()
        )
    output.chmod(0o755)
    version = subprocess.run(
        [str(CC), "--version"],
        check=True,
        capture_output=True,
        text=True,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    ).stdout.splitlines()[0]
    return {
        "compiler": str(CC),
        "compiler_version": version,
        "interceptor_sha256": _sha256_file(output),
        "source_sha256": _sha256_file(source),
    }


def _copy_tree(loader: Any, destination: Path) -> None:
    for path, entry in sorted(loader.entries.items()):
        if entry.mode == "160000" and entry.object_type == "commit":
            _copy_gitlink(
                loader.root,
                path,
                entry.object_id,
                destination / path,
            )
            continue
        if entry.object_type != "blob" or entry.mode not in {"100644", "100755"}:
            continue
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(loader.read_blob(path, "Make probe input"))
        target.chmod(0o755 if entry.mode == "100755" else 0o644)


def _copy_gitlink(
    repository: Path,
    gitlink_path: str,
    commit: str,
    destination: Path,
) -> None:
    environment = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }
    common = subprocess.run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            "-C",
            str(repository),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()
    git_dir = Path(common) / "modules" / gitlink_path
    listing = subprocess.run(
        [
            "/usr/bin/git",
            "--no-replace-objects",
            f"--git-dir={git_dir}",
            "ls-tree",
            "-rz",
            "--full-tree",
            "-r",
            commit,
        ],
        check=False,
        capture_output=True,
        env=environment,
    )
    if listing.returncode != 0:
        raise MakeProbeError(
            f"cannot materialize exact gitlink {repository.name}@{commit}"
        )
    destination.mkdir(parents=True, exist_ok=True)
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = header.decode("ascii").split()
        if object_type != "blob" or mode not in {"100644", "100755"}:
            continue
        path = raw_path.decode("utf-8")
        content = subprocess.run(
            [
                "/usr/bin/git",
                "--no-replace-objects",
                f"--git-dir={git_dir}",
                "cat-file",
                "blob",
                object_id,
            ],
            check=True,
            capture_output=True,
            env=environment,
        ).stdout
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if mode == "100755" else 0o644)


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = memoryview(path.read_bytes())
    offset = 0
    result = []

    def take_u32() -> int:
        nonlocal offset
        if offset + 4 > len(data):
            raise MakeProbeError("shell interceptor emitted a truncated event")
        value = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        return value

    while offset < len(data):
        match = take_u32()
        mapping_count = take_u32()
        command_hash = take_u32() | (take_u32() << 32)
        argc = take_u32()
        arguments = []
        for _ in range(argc):
            size = take_u32()
            if offset + size > len(data):
                raise MakeProbeError(
                    "shell interceptor emitted a truncated argument"
                )
            arguments.append(bytes(data[offset:offset + size]).decode("utf-8"))
            offset += size
        result.append(
            {
                "arguments": arguments,
                "command_hash": f"{command_hash:016x}",
                "match": -1 if match == 0xFFFFFFFF else match,
                "mapping_count": mapping_count,
            }
        )
    return result


def _event_command(event: dict[str, Any]) -> str | None:
    arguments = event["arguments"]
    if (
        len(arguments) == 3
        and arguments[1] == "-c"
    ):
        return arguments[2]
    if (
        len(arguments) == 4
        and arguments[1:3] == ["-eu", "-c"]
    ):
        return arguments[3]
    if not arguments:
        return None
    aliases = {
        "/usr/bin/find": "find",
        "/usr/bin/printf": "printf",
        "/usr/bin/python3": "python3",
        "/usr/bin/uname": "uname",
        "/bin/vo-make": "/usr/bin/make",
    }
    return " ".join(
        [
            aliases.get(arguments[0], arguments[0]),
            *(
                '""' if not argument else argument
                for argument in arguments[1:]
            ),
        ]
    )


def _event_direct_arguments(event: dict[str, Any]) -> list[str] | None:
    arguments = event["arguments"]
    if (
        len(arguments) >= 3
        and arguments[-2] == "-c"
        and arguments[0] in {"/bin/sh", "/bin/bash"}
    ):
        return None
    return arguments or None


def _write_mapping(work: Path, mappings: list[dict[str, Any]]) -> None:
    directory = work / "map"
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir()
    seen = set()
    for mapping in mappings:
        value = 0xCBF29CE484222325
        for byte in mapping["command"].encode("utf-8"):
            value ^= byte
            value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        name = f"{value:016x}"
        if name in seen:
            raise MakeProbeError("Make command mapping hash collision")
        seen.add(name)
        (directory / f"{name}.cmd").write_bytes(
            mapping["command"].encode("utf-8")
        )
        (directory / f"{name}.out").write_bytes(mapping["output"])


def _make_environment(
    work: Path,
    mapping_count: int,
    extra: dict[str, str],
) -> dict[str, str]:
    return {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "MAKEFLAGS": "",
        "MAKEOVERRIDES": "",
        "MFLAGS": "",
        "GNUMAKEFLAGS": "",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "SOURCE_DATE_EPOCH": "0",
        "TMPDIR": "/work",
        "TZ": "UTC",
        "VO_COMMAND_COUNT": str(mapping_count),
        "VO_EVENT_PATH": "/work/events.bin",
        "VO_MAP_DIR": "/work/map",
        **extra,
    }


def _prepare_make_root(
    base: Path,
    interceptor: Path,
) -> tuple[Path, list[tuple[Path, str]]]:
    root = base / "make-root"
    for directory in (
        "bin",
        "dev",
        "lib/x86_64-linux-gnu",
        "lib64",
        "repo",
        "usr/bin",
        "work",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)
    shutil.copy2(interceptor, root / "bin/sh")
    shutil.copy2(interceptor, root / "bin/bash")
    shutil.copy2(interceptor, root / "bin/vo-make")
    shutil.copy2(interceptor, root / "bin/vo-shell")
    for name in (
        "arm-none-eabi-as",
        "arm-none-eabi-gcc",
        "cc",
        "find",
        "g++",
        "gcc",
        "iconv",
        "mkdir",
        "mv",
        "printf",
        "python3",
        "rm",
        "sed",
        "uname",
    ):
        shutil.copy2(interceptor, root / "usr/bin" / name)
    shutil.copy2(MAKE, root / "usr/bin/make")
    shutil.copy2(LIBC, root / "lib/x86_64-linux-gnu/libc.so.6")
    shutil.copy2(LOADER, root / "lib64/ld-linux-x86-64.so.2")
    (root / "dev/null").touch()
    return root, []


def _prepare_command_root(base: Path) -> Path:
    root = base / "command-root"
    for directory in ("dev", "repo", "usr", "work"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    for name, target in (
        ("bin", "usr/bin"),
        ("lib", "usr/lib"),
        ("lib64", "usr/lib64"),
    ):
        (root / name).symlink_to(target)
    (root / "dev/null").touch()
    return root


def _compile_scaninc(tree: Path, work: Path) -> Path:
    output = work / "scaninc"
    if output.exists():
        return output
    sources = [
        tree / "tools/scaninc" / name
        for name in (
            "scaninc.cpp",
            "c_file.cpp",
            "asm_file.cpp",
            "source_file.cpp",
        )
    ]
    completed = subprocess.run(
        [
            "/usr/bin/g++",
            "-Wall",
            "-Werror",
            "-std=c++11",
            "-O2",
            *(str(path) for path in sources),
            "-o",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        },
    )
    if completed.returncode != 0:
        raise MakeProbeError(
            "cannot compile registered scaninc authority: "
            + completed.stderr.strip()
        )
    output.chmod(0o755)
    return output


def _compile_gbagfx(tree: Path, output: Path) -> dict[str, str]:
    source_root = tree / "tools/gbagfx"
    sources = [
        source_root / name
        for name in (
            "main.c",
            "convert_png.c",
            "gfx.c",
            "jasc_pal.c",
            "lz.c",
            "rl.c",
            "util.c",
            "font.c",
        )
    ]
    completed = subprocess.run(
        [
            "/usr/bin/gcc",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-std=c11",
            "-O2",
            "-s",
            "-DPNG_SKIP_SETJMP_CHECK",
            *(str(path) for path in sources),
            "-o",
            str(output),
            "-lpng",
            "-lz",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TZ": "UTC",
        },
    )
    if completed.returncode != 0:
        raise MakeProbeError(
            "cannot compile registered gbagfx authority: "
            + completed.stderr.strip()
        )
    output.chmod(0o755)
    return {
        "compiler": "/usr/bin/gcc",
        "output_sha256": _sha256_file(output),
    }


def _execute_registered_command(
    command: str,
    contract: dict[str, Any],
    *,
    base: Path,
    direct_arguments: list[str] | None,
    tree: Path,
    work: Path,
    environment: dict[str, str],
) -> bytes:
    if contract["resolved_value"] is not None:
        if not contract["resolved_value"]:
            return b""
        return (contract["resolved_value"] + "\n").encode("utf-8")
    executed = command
    if contract["id"] == "banim-scaninc-inputs":
        _compile_scaninc(tree, work)
        executed = re.sub(
            r"^tools/scaninc/scaninc\b",
            "/work/scaninc",
            executed,
        )
    root = base / "command-root"
    if not root.exists():
        root = _prepare_command_root(base)
    if direct_arguments is None:
        argv = ["/usr/bin/bash", "-c", executed]
    else:
        executable_aliases = {
            "arm-none-eabi-as": "/usr/bin/arm-none-eabi-as",
            "arm-none-eabi-gcc": "/usr/bin/arm-none-eabi-gcc",
            "cc": "/usr/bin/cc",
            "find": "/usr/bin/find",
            "g++": "/usr/bin/g++",
            "gcc": "/usr/bin/gcc",
            "iconv": "/usr/bin/iconv",
            "mkdir": "/usr/bin/mkdir",
            "mv": "/usr/bin/mv",
            "printf": "/usr/bin/printf",
            "python3": "/usr/bin/python3",
            "rm": "/usr/bin/rm",
            "sed": "/usr/bin/sed",
            "uname": "/usr/bin/uname",
        }
        argv = [
            executable_aliases.get(
                direct_arguments[0],
                direct_arguments[0],
            ),
            *direct_arguments[1:],
        ]
    completed = _sandbox_run(
        root,
        work,
        argv=argv,
        environment={
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOURCE_DATE_EPOCH": "0",
            "TMPDIR": "/work",
            "TZ": "UTC",
            **environment,
        },
        read_only=[
            (tree, "/repo"),
            (Path("/usr"), "/usr"),
        ],
        writable=[
            (work / "build", "/repo/build"),
            (Path("/dev/null"), "/dev/null"),
        ],
    )
    if completed.returncode != 0:
        raise MakeProbeError(
            f"registered Make command {contract['id']!r} failed in confinement: "
            + _normalize(completed.stderr)
        )
    return completed.stdout.encode("utf-8")


def _normalize(text: str) -> str:
    text = text.replace("/bin/vo-make", "/usr/bin/make")
    text = text.replace("/repo/", "")
    text = text.replace("/repo", ".")
    text = text.replace("/work", "<WORK>")
    return "\n".join(line.rstrip() for line in text.splitlines())


def _trace_records(output: str) -> dict[str, dict[str, Any]]:
    lines = _normalize(output).splitlines()
    records: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for line in lines:
        match = TRACE_RE.match(line)
        if match:
            target = match.group("target")
            current = records.setdefault(
                target,
                {
                    "commands": [],
                    "reasons": [],
                    "sources": [],
                },
            )
            current["sources"].append(
                re.sub(r":[0-9]+$", "", match.group("source"))
            )
            if match.group("due"):
                current["reasons"].extend(match.group("due").split())
            continue
        stripped = line.lstrip()
        if current is not None and line and not stripped.startswith(
            (
                "Considering target file ",
                "Finished prerequisites of target file ",
                "File ",
                "Making ",
                "Must remake target ",
                "No need to remake target ",
                "Successfully remade target file ",
                "Pruning file ",
                "Reading makefile ",
                "Trying pattern rule ",
                "Updating makefiles",
                "Updating goal targets",
            )
        ):
            current["commands"].append(line)
    return {
        target: {
            "commands": record["commands"],
            "reasons": sorted(set(record["reasons"])),
            "sources": sorted(set(record["sources"])),
        }
        for target, record in records.items()
    }


def _target_graph(output: str) -> tuple[dict[str, list[str]], list[str]]:
    graph: dict[str, set[str]] = {}
    stack: list[tuple[int, str]] = []
    includes = []
    goals_started = False
    for line in _normalize(output).splitlines():
        if line.startswith("Updating goal targets"):
            goals_started = True
            stack = []
            continue
        reading = READING_RE.match(line)
        if reading:
            includes.append(reading.group("path"))
        if not goals_started:
            continue
        match = CONSIDER_RE.match(line)
        if not match:
            continue
        indent = len(match.group("indent"))
        target = match.group("target")
        graph.setdefault(target, set())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            graph.setdefault(stack[-1][1], set()).add(target)
        stack.append((indent, target))
    return (
        {
            target: sorted(children)
            for target, children in sorted(graph.items())
        },
        sorted(set(includes)),
    )


def _database_semantics(output: str) -> str:
    normalized = _normalize(output)
    marker = "\n# Files\n"
    if marker not in normalized:
        raise MakeProbeError("GNU Make database output lacks the Files section")
    result = normalized.split(marker, 1)[1]
    for end_marker in (
        "\n# files hash-table stats:",
        "\n# Finished Make data base",
    ):
        if end_marker in result:
            result = result.split(end_marker, 1)[0]
    return re.sub(r":[0-9]+", ":<LINE>", result)


def _closures(
    targets: Iterable[str],
    graph: dict[str, list[str]],
) -> dict[str, list[str]]:
    result = {}
    for target in targets:
        seen = set()
        pending = [target]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(graph.get(current, ()))
        result[target] = sorted(seen)
    return result


def _validate_includes(
    includes: Iterable[str],
    loader: Any,
) -> None:
    for raw_path in includes:
        path = raw_path
        if path == "<WORK>/probe.mk":
            continue
        if path.startswith("./"):
            path = path[2:]
        if path.startswith("/") or path.startswith("<WORK>"):
            raise MakeProbeError(
                f"GNU Make read an absolute or dynamic include {raw_path!r}"
            )
        if path in loader.entries:
            entry = loader.entries[path]
            if entry.object_type != "blob" or entry.mode not in {
                "100644",
                "100755",
            }:
                raise MakeProbeError(
                    f"GNU Make include {path!r} is not a tracked regular file"
                )
            continue
        if path.startswith("build/"):
            continue
        if any(
            entry.mode == "160000"
            and path.startswith(gitlink.rstrip("/") + "/")
            for gitlink, entry in loader.entries.items()
        ):
            continue
        raise MakeProbeError(
            f"GNU Make read an untracked include {path!r}"
        )


def _makefile_modes(loader: Any) -> list[dict[str, str]]:
    return [
        {"mode": entry.mode, "path": path}
        for path, entry in sorted(loader.entries.items())
        if path == "Makefile" or path.endswith(".mk")
    ]


def run_probe(
    loader: Any,
    targets: set[str],
    prerequisite_domains: dict[str, dict[str, Any]],
    dynamic_contracts: dict[str, dict[str, Any]],
    *,
    declared_external_names: set[str] | None = None,
    environment_names: set[str] | None = None,
    scratch_root: Path,
    symbolic_recipe_names: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run actual GNU Make for every sealed domain value/origin pair."""
    if not targets:
        return {}
    declared_external_names = (
        set()
        if declared_external_names is None
        else declared_external_names
    )
    environment_names = set() if environment_names is None else environment_names
    symbolic_recipe_names = (
        set()
        if symbolic_recipe_names is None
        else symbolic_recipe_names
    )
    unclassified = (
        declared_external_names
        - set(prerequisite_domains)
        - symbolic_recipe_names
    )
    stale_symbolic = symbolic_recipe_names - declared_external_names
    if unclassified or stale_symbolic:
        raise MakeProbeError(
            "external Make variables lack finite domains or symbolic recipe "
            f"authority (unclassified={sorted(unclassified)}, "
            f"stale_symbolic={sorted(stale_symbolic)})"
        )
    tools = _ensure_tools()
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="gnu-make-probe-",
        dir=scratch_root,
    ) as directory:
        base = Path(directory)
        tree = base / "tree"
        work = base / "work"
        tree.mkdir()
        work.mkdir()
        (work / "build").mkdir()
        _copy_tree(loader, tree)
        (tree / "build").mkdir()
        interceptor = base / "shell-interceptor"
        interceptor_authority = _compile_interceptor(tree, interceptor)
        root, read_only = _prepare_make_root(base, interceptor)
        read_only.append((tree, "/repo"))
        if (tree / "tools/scaninc").is_dir():
            scaninc_target = tree / "tools/scaninc/scaninc"
            scaninc_target.touch()
            scaninc_target.chmod(0o755)
            read_only.append((interceptor, "/repo/tools/scaninc/scaninc"))
        if (tree / "scripts/arm_compressing_linker.py").is_file():
            read_only.append(
                (
                    interceptor,
                    "/repo/scripts/arm_compressing_linker.py",
                )
            )
        gbagfx_authority = None
        if (tree / "tools/gbagfx/main.c").is_file():
            gbagfx = base / "gbagfx"
            gbagfx_authority = _compile_gbagfx(tree, gbagfx)
            gbagfx_target = tree / "tools/gbagfx/gbagfx"
            gbagfx_target.touch()
            gbagfx_target.chmod(0o755)
            read_only.append((gbagfx, "/repo/tools/gbagfx/gbagfx"))
        probe_file = work / "probe.mk"
        tracked_inputs = [
            path
            for path, entry in sorted(loader.entries.items())
            if entry.object_type == "blob"
            and entry.mode in {"100644", "100755"}
        ]
        tracked_input_rules = "\n".join(
            " ".join(tracked_inputs[index:index + 100]) + ": ;"
            for index in range(0, len(tracked_inputs), 100)
        )
        tracked_directories = sorted(
            {
                parent.as_posix()
                for path in tracked_inputs
                for parent in Path(path).parents
                if parent.as_posix() != "."
            }
        )
        tracked_directory_rules = "\n".join(
            " ".join(tracked_directories[index:index + 100]) + ": ;"
            for index in range(0, len(tracked_directories), 100)
        )
        probe_file.write_text(
            "/work/probe.mk: ;\n"
            + tracked_input_rules
            + "\n"
            + tracked_directory_rules
            + "\n"
            + ".PHONY: __validation_ownership_domain_probe\n"
            + "__validation_ownership_domain_probe: ;\n"
            + "\n".join(
                f"$(file >/work/domain-{index},$({name}))"
                for index, name in enumerate(sorted(prerequisite_domains))
            )
            + "\n",
            encoding="ascii",
        )
        mappings: list[dict[str, Any]] = []
        _write_mapping(work, mappings)
        event_path = work / "events.bin"
        normal_targets = sorted(
            target for target in targets if target != "validation-ownership-check"
        )
        target_groups = []
        if normal_targets:
            target_groups.append(normal_targets)
        if "validation-ownership-check" in targets:
            target_groups.append(["validation-ownership-check"])
        variant_results = []
        run_count = 0
        fallback_values: dict[str, str] | None = None

        def invoke(
            selected_targets: list[str],
            *,
            cli: tuple[str, str] | None = None,
            database_only: bool = False,
            environment_value: tuple[str, str] | None = None,
        ) -> dict[str, Any]:
            nonlocal run_count
            run_count += 1
            if run_count > MAX_SANDBOX_RUNS:
                raise MakeProbeError("GNU Make probe exceeds run bound")
            extra_environment = {}
            if environment_value is not None:
                extra_environment[environment_value[0]] = environment_value[1]
            public_gate = selected_targets == ["validation-ownership-check"]
            if public_gate:
                argv = [
                    "/usr/bin/make",
                    "--no-print-directory",
                    "validation-ownership-check",
                ]
            else:
                argv = [
                    "/usr/bin/make",
                    "--no-print-directory",
                    "--debug=v",
                    "--eval",
                    "export VO_COMMAND_COUNT VO_EVENT_PATH VO_MAP_DIR",
                    *(
                        ["--print-data-base", "--question"]
                        if database_only
                        else ["--trace", "-n", "-B"]
                    ),
                    "-f",
                    "Makefile",
                    "-f",
                    "/work/probe.mk",
                    "MAKE=/bin/vo-make",
                ]
            if cli is not None:
                argv.append(f"{cli[0]}={cli[1]}")
            if not public_gate:
                argv.extend(
                    [selected_targets[0]]
                    if database_only
                    else selected_targets
                )
            for _ in range(MAX_DYNAMIC_PASSES):
                _write_mapping(work, mappings)
                event_path.unlink(missing_ok=True)
                completed = _sandbox_run(
                    root,
                    work,
                    argv=argv,
                    environment=_make_environment(
                        work,
                        len(mappings),
                        extra_environment,
                    ),
                    read_only=read_only,
                    writable=[(work / "build", "/repo/build")],
                )
                events = _read_events(event_path)
                unknown = [
                    event
                    for event in events
                    if event["match"] < 0
                ]
                if public_gate:
                    public_commands = [
                        _event_command(event)
                        for event in events
                        if _event_command(event) is not None
                    ]
                    if (
                        len(public_commands) != 1
                        or re.fullmatch(
                            r"/usr/bin/python3 -I "
                            r"scripts/validation_ownership/isolated_launcher\.py "
                            r"\\\n\tcheck --repository-root \"/repo\" "
                            r"> /dev/null",
                            public_commands[0],
                        )
                        is None
                    ):
                        raise MakeProbeError(
                            "public validation-ownership-check recipe is not "
                            "the exact isolated checker"
                        )
                if not unknown or public_gate:
                    break
                replay = False
                for event in unknown:
                    command = _event_command(event)
                    if command is None:
                        raise MakeProbeError(
                            "GNU Make used an unsupported shell invocation: "
                            + repr(event)
                        )
                    matches = [
                        contract
                        for contract in dynamic_contracts.values()
                        if re.fullmatch(
                            contract.get("command_regex", r"(?!x)x"),
                            command,
                            re.DOTALL,
                        )
                    ]
                    if len(matches) != 1:
                        raise MakeProbeError(
                            "GNU Make attempted command execution without "
                            "exactly one sealed contract: " + repr(event)
                        )
                    if any(item["command"] == command for item in mappings):
                        continue
                    contract = matches[0]
                    output = _execute_registered_command(
                        command,
                        contract,
                        base=base,
                        direct_arguments=_event_direct_arguments(event),
                        tree=tree,
                        work=work,
                        environment=extra_environment,
                    )
                    mappings.append(
                        {
                            "command": command,
                            "contract": contract,
                            "output": output,
                            "suppressed_recipe": False,
                        }
                    )
                    replay |= (
                        contract["resolved_value"] is None
                        or bool(output)
                    )
                if not replay:
                    break
            else:
                raise MakeProbeError(
                    "GNU Make dynamic command expansion exceeds pass bound"
                )
            combined = completed.stdout + "\n" + completed.stderr
            if public_gate:
                graph = {"validation-ownership-check": []}
                includes = ["Makefile"]
                traces = {
                    "validation-ownership-check": {
                        "commands": [
                            event["arguments"][2]
                            for event in events
                            if len(event["arguments"]) == 3
                            and event["arguments"][1] == "-c"
                        ],
                        "reasons": [],
                        "sources": ["Makefile"],
                    }
                }
            else:
                graph, includes = _target_graph(combined)
                traces = _trace_records(combined)
                _validate_includes(includes, loader)
            if completed.returncode != 0 and not (
                database_only and completed.returncode == 1
            ):
                raise MakeProbeError(
                    "GNU Make authority probe failed: " + _normalize(combined)
                )
            return {
                "argv": argv,
                "closures": _closures(selected_targets, graph),
                "environment_assignment": environment_value,
                "graph": graph,
                "database_sha256": (
                    hashlib.sha256(
                        _database_semantics(combined).encode("utf-8")
                    ).hexdigest()
                    if database_only
                    else None
                ),
                "includes": includes,
                "origin": (
                    "command-line"
                    if cli is not None
                    else "environment"
                    if environment_value is not None
                    else "fallback"
                ),
                "traces": traces,
                "variable": None if cli is None else cli[0],
                "value": (
                    cli[1]
                    if cli is not None
                    else environment_value[1]
                    if environment_value is not None
                    else None
                ),
            }

        baseline_by_group = []
        database_baselines = []
        for group in target_groups:
            baseline_by_group.append(invoke(group))
            database_baselines.append(
                None
                if group == ["validation-ownership-check"]
                else invoke(group, database_only=True)["database_sha256"]
            )
        fallback_values = (
            {
                name: (work / f"domain-{index}")
                .read_text(encoding="utf-8")
                .removesuffix("\n")
                for index, name in enumerate(sorted(prerequisite_domains))
            }
            if normal_targets
            else {}
        )
        for group, baseline, database_baseline in zip(
            target_groups,
            baseline_by_group,
            database_baselines,
        ):
            variant_results.append(baseline)
            if group == ["validation-ownership-check"]:
                continue
            for name in sorted(prerequisite_domains):
                domain = prerequisite_domains[name]
                values = (
                    domain["values"]
                    if domain["kind"] == "explicit"
                    else [fallback_values[name]]
                )
                for value in values:
                    candidate = invoke(
                        group,
                        cli=(name, value),
                        database_only=True,
                    )
                    if candidate["database_sha256"] == database_baseline:
                        candidate["closures"] = baseline["closures"]
                        candidate["graph"] = baseline["graph"]
                        candidate["includes"] = baseline["includes"]
                        candidate["same_as_fallback"] = True
                    else:
                        candidate = invoke(group, cli=(name, value))
                    variant_results.append(candidate)
                    if name in environment_names:
                        candidate = invoke(
                            group,
                            environment_value=(name, value),
                            database_only=True,
                        )
                        if candidate["database_sha256"] == database_baseline:
                            candidate["closures"] = baseline["closures"]
                            candidate["graph"] = baseline["graph"]
                            candidate["includes"] = baseline["includes"]
                            candidate["same_as_fallback"] = True
                        else:
                            candidate = invoke(
                                group,
                                environment_value=(name, value),
                            )
                        variant_results.append(candidate)

        make_inputs = _makefile_modes(loader)
        result = {}
        for target in sorted(targets):
            target_variants = []
            baseline_semantics = None
            all_closure_items = set()
            for variant in variant_results:
                if target not in variant["closures"]:
                    continue
                closure = variant["closures"][target]
                all_closure_items.update(closure)
                semantics = (
                    baseline_semantics
                    if variant.get("same_as_fallback")
                    else {
                        "closure": closure,
                        "includes": variant["includes"],
                        "recipes": {
                            item: variant["traces"][item]
                            for item in closure
                            if item in variant["traces"]
                        },
                    }
                )
                if semantics is None:
                    raise MakeProbeError(
                        f"GNU Make target {target!r} lacks fallback authority"
                    )
                semantics_bytes = json.dumps(
                    semantics,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                semantic_hash = hashlib.sha256(semantics_bytes).hexdigest()
                record = {
                    "environment_assignment": (
                        list(variant["environment_assignment"])
                        if variant["environment_assignment"] is not None
                        else None
                    ),
                    "database_sha256": variant["database_sha256"],
                    "origin": variant["origin"],
                    "semantic_sha256": semantic_hash,
                    "value": variant["value"],
                    "variable": variant["variable"],
                }
                if variant["origin"] == "fallback":
                    baseline_semantics = semantics
                    record["semantics"] = semantics
                elif semantics != baseline_semantics:
                    record["semantics"] = semantics
                target_variants.append(record)
            result[target] = {
                "cycles": [],
                "dynamic_dependencies": [],
                "effective_exported_environment": {},
                "global_exported_environment": {},
                "prerequisite_domain_census": {
                    "generated_paths": [],
                    "unconstrained": [],
                    "used": sorted(prerequisite_domains),
                },
                "record": {
                    "interceptor": interceptor_authority,
                    "generated_gbagfx": gbagfx_authority,
                    "dynamic_commands": [
                        {
                            "authority_id": mapping["contract"]["id"],
                            "command": _normalize(mapping["command"]),
                            "output_sha256": hashlib.sha256(
                                mapping["output"]
                            ).hexdigest(),
                        }
                        for mapping in mappings
                        if mapping["contract"] is not None
                    ],
                    "make_inputs": make_inputs,
                    "probe_tools": tools,
                    "sanitized_environment": _make_environment(
                        work,
                        len(mappings),
                        {},
                    ),
                    "symbolic_recipe_names": sorted(symbolic_recipe_names),
                    "variants": target_variants,
                },
                "target": target,
                "transitive": sorted(
                    all_closure_items - {target}
                ),
                "unknown_dynamic_prerequisites": [],
                "variable_census": {
                    "ambient_undefined": [],
                    "escaped_literals": [],
                    "handled_names": [],
                    "scoped_variables": [],
                    "trusted_builtins": [],
                    "unbound": [],
                },
            }
        return result
