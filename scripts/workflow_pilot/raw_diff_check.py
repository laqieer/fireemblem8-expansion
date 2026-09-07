#!/usr/bin/env python3
"""Check added raw diff lines against the fixed handoff whitespace policy."""

from __future__ import annotations

import argparse
import io
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


GIT = "/usr/bin/git"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HUNK_RE = re.compile(
    rb"^@@ -[0-9]+(?:,[0-9]+)? \+([0-9]+)(?:,([0-9]+))? @@"
)
WHITESPACE_POLICY = "blank-at-eol,blank-at-eof,space-before-tab"
MAX_BYTES = 4 * 1024 * 1024
MAX_METADATA_BYTES = 4096
MAX_DIAGNOSTICS = 100
GIT_TIMEOUT_SECONDS = 30


def git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


def git_command(repository_root: Path, *arguments: str) -> tuple[str, ...]:
    return (
        GIT,
        "--no-replace-objects",
        "--literal-pathspecs",
        "-C",
        str(repository_root),
        "-c",
        f"core.whitespace={WHITESPACE_POLICY}",
        "-c",
        "core.attributesFile=/dev/null",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.ignorestat=false",
        "-c",
        "core.trustctime=true",
        "-c",
        "core.checkStat=default",
        "-c",
        "color.ui=false",
        "-c",
        "color.diff=false",
        "-c",
        "core.quotePath=true",
        "-c",
        "diff.external=",
        "-c",
        "diff.wsErrorHighlight=all",
        "-c",
        "diff.algorithm=myers",
        "-c",
        "diff.indentHeuristic=false",
        "-c",
        "diff.renames=false",
        "-c",
        "core.abbrev=40",
        *arguments,
    )


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    pid: int
    elapsed_seconds: float
    peak_rss_bytes: int


def run_process(argv, *, cwd, env, timeout=GIT_TIMEOUT_SECONDS, max_bytes=MAX_BYTES):
    """Capture an owned child, not printed exit labels or tool transport status."""
    started = time.monotonic()
    process = subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        output = {process.stdout: bytearray(), process.stderr: bytearray()}
        remaining = max_bytes
        deadline = started + timeout
        with selectors.DefaultSelector() as selector:
            for stream in output:
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                wait = deadline - time.monotonic()
                if wait <= 0:
                    raise ValueError("process timed out")
                for key, _ in selector.select(wait):
                    chunk = os.read(key.fd, min(65536, remaining + 1))
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError(f"process output exceeds {max_bytes} bytes")
                    if chunk:
                        output[key.fileobj].extend(chunk)
                    else:
                        selector.unregister(key.fileobj)
        while True:
            pid, status, usage = os.wait4(process.pid, os.WNOHANG)
            if pid:
                process.returncode = os.waitstatus_to_exitcode(status)
                break
            if time.monotonic() >= deadline:
                raise ValueError("process timed out")
            time.sleep(0.01)
        return ProcessResult(
            process.returncode, bytes(output[process.stdout]), bytes(output[process.stderr]),
            process.pid, time.monotonic() - started, usage.ru_maxrss * 1024,
        )
    finally:
        if process.returncode is None:
            # Only the session created above is ours. Never resolve a supplied PID/group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        process.stdout.close()
        process.stderr.close()


def run_git(repository_root: Path, *arguments: str) -> bytes:
    result = run_process(
        git_command(repository_root, *arguments), cwd=repository_root, env=git_environment(),
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Git {' '.join(arguments)} failed: {detail}")
    return result.stdout


@contextmanager
def _directory_fd(path: Path):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _metadata_file(path: Path, label: str, *, read: bool = False) -> bytes | None:
    try:
        with _directory_fd(path.parent) as parent:
            metadata = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or (metadata.st_size and not read):
                raise ValueError(f"repository {label} is not permitted")
            if not read:
                return b""
            if metadata.st_size > MAX_METADATA_BYTES:
                raise ValueError(f"repository {label} exceeds 4096 bytes")
            signature = lambda value: (value.st_dev, value.st_ino, value.st_mode,
                                       value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=parent)
            try:
                if signature(os.fstat(descriptor)) != signature(metadata):
                    raise ValueError(f"repository {label} changed before read")
                raw = os.read(descriptor, MAX_METADATA_BYTES + 1)
                current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                if (len(raw) != metadata.st_size or signature(current) != signature(metadata)
                        or signature(os.fstat(descriptor)) != signature(metadata)):
                    raise ValueError(f"repository {label} changed during read")
                return raw
            finally:
                os.close(descriptor)
    except FileNotFoundError:
        return None


def _metadata_directory(raw: bytes | None, prefix: bytes, base: Path) -> Path:
    if raw is None or not raw.startswith(prefix):
        raise ValueError("repository Git directory file is malformed")
    value = raw[len(prefix):].rstrip(b"\r\n")
    if not value or any(byte in value for byte in (b"\0", b"\r", b"\n")):
        raise ValueError("repository Git directory file is malformed")
    path = base / os.fsdecode(value)
    with _directory_fd(path):
        return path


def reject_git_metadata(repository_root: Path, paths: tuple[tuple[str, str], ...]) -> None:
    entry = repository_root / ".git"
    try:
        mode = entry.lstat().st_mode
    except FileNotFoundError as error:
        raise ValueError("repository .git entry is missing") from error
    private = entry if stat.S_ISDIR(mode) else _metadata_directory(
        _metadata_file(entry, ".git entry", read=True), b"gitdir: ", repository_root
    )
    common = _metadata_file(private / "commondir", "commondir", read=True)
    roots = (private,) if common is None else (
        private, _metadata_directory(common, b"", private)
    )
    for root in roots:
        for relative, label in paths:
            _metadata_file(root / relative, label)


def exact_repository_root(value: str) -> Path:
    root = Path(value).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("repository root is not a directory")
    reject_git_metadata(root, (
        ("info/attributes", "local attributes file"),
        ("info/grafts", "graft file"),
        ("objects/info/alternates", "alternate object store"),
        ("objects/info/http-alternates", "HTTP alternate object store"),
    ))
    top = Path(run_git(root, "rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
    if root != top:
        raise ValueError(f"repository root must be exact Git top level {top}")
    return root


def validate_sha(value: str, label: str) -> str:
    if SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase full Git SHA")
    return value


def leading_space_before_tab(content: bytes) -> bool:
    leading = content[: len(content) - len(content.lstrip(b" \t"))]
    return b" \t" in leading


def trailing_blank_lines(content: bytes) -> int:
    count = 0
    for line in io.BytesIO(content):
        count = count + 1 if not line.strip(b" \t\r\n") else 0
    return count


def raw_diff_errors(
    repository_root: Path,
    parent_sha: str,
    candidate_sha: str,
) -> list[str]:
    records = run_git(
        repository_root, "diff", "--raw", "--no-ext-diff", "--no-textconv",
        "--no-renames", "--abbrev=40", "-z", parent_sha, candidate_sha, "--",
    ).split(b"\0")
    changes = []
    total = 0
    for index in range(0, len(records) - 1, 2):
        old_mode, new_mode, old_oid, new_oid, _status = records[index].split()
        oids = []
        for mode, oid in ((old_mode[1:], old_oid), (new_mode, new_oid)):
            if mode in (b"000000", b"160000"):
                oids.append(None)
                continue
            object_id = oid.decode("ascii")
            total += int(run_git(repository_root, "cat-file", "-s", object_id))
            if total > MAX_BYTES:
                raise ValueError("changed blob bytes exceed 4 MiB")
            oids.append(object_id)
        changes.append((os.fsdecode(records[index + 1]), oids))
    diff = run_git(
        repository_root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--text",
        "--no-renames",
        "--unified=0",
        "--no-color",
        "--no-prefix",
        parent_sha,
        candidate_sha,
        "--",
    )
    errors = []
    path = None
    line_number = None
    for line in io.BytesIO(diff):
        line = line.removesuffix(b"\n")
        if line.startswith(b"diff --git "):
            path = None
            line_number = None
            continue
        if line.startswith(b"+++ ") and line_number is None:
            raw_path = line[4:]
            path = raw_path.decode("utf-8", errors="surrogateescape")
            continue
        match = HUNK_RE.match(line)
        if match is not None:
            line_number = int(match.group(1))
            continue
        if line_number is None:
            continue
        if line.startswith(b"+"):
            content = line[1:]
            if content.endswith((b" ", b"\t", b"\r")):
                errors.append(f"{path}:{line_number}: blank-at-eol")
            if leading_space_before_tab(content):
                errors.append(f"{path}:{line_number}: space-before-tab")
            line_number += 1
            if len(errors) >= MAX_DIAGNOSTICS:
                return sorted(set(errors))[:MAX_DIAGNOSTICS]
        elif line.startswith(b" "):
            line_number += 1

    for path_text, (old_oid, new_oid) in changes:
        if new_oid is None:
            continue
        candidate = run_git(repository_root, "cat-file", "blob", new_oid)
        parent = run_git(repository_root, "cat-file", "blob", old_oid) if old_oid else b""
        if trailing_blank_lines(candidate) > trailing_blank_lines(parent):
            errors.append(f"{path_text}:EOF: blank-at-eof")
            if len(errors) >= MAX_DIAGNOSTICS:
                break
    return sorted(set(errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--candidate", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        repository_root = exact_repository_root(args.repository_root)
        parent_sha = validate_sha(args.parent, "parent")
        candidate_sha = validate_sha(args.candidate, "candidate")
        errors = raw_diff_errors(repository_root, parent_sha, candidate_sha)
    except (OSError, ValueError) as error:
        print(f"raw-diff-check: {error}", file=sys.stderr)
        return 2
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
