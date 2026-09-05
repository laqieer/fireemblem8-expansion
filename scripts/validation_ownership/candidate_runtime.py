#!/usr/bin/env python3
"""Trusted bootstrap for confined candidate Python registry code."""

from __future__ import annotations

import builtins
import ctypes
import errno
import glob
import importlib
import io
import json
import mmap
import os
import stat
import struct
import sys
from pathlib import Path


PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_ERRNO = 0x00050000
AUDIT_ARCH_X86_64 = 0xC000003E
BPF_LD = 0x00
BPF_W = 0x00
BPF_ABS = 0x20
BPF_JMP = 0x05
BPF_JEQ = 0x10
BPF_K = 0x00
BPF_RET = 0x06
BLOCKED_SYSCALLS = (
    29,   # shmget
    30,   # shmat
    31,   # shmctl
    41,   # socket
    53,   # socketpair
    56,   # clone
    57,   # fork
    58,   # vfork
    59,   # execve
    62,   # kill
    64,   # semget
    65,   # semop
    66,   # semctl
    68,   # msgget
    69,   # msgsnd
    70,   # msgrcv
    71,   # msgctl
    101,  # ptrace
    134,  # uselib
    175,  # init_module
    176,  # delete_module
    200,  # tkill
    234,  # tgkill
    246,  # kexec_load
    165,  # mount
    272,  # unshare
    308,  # setns
    310,  # process_vm_readv
    311,  # process_vm_writev
    313,  # finit_module
    319,  # memfd_create
    320,  # kexec_file_load
    298,  # perf_event_open
    304,  # open_by_handle_at
    321,  # bpf
    322,  # execveat/fexecve
    323,  # userfaultfd
    424,  # pidfd_send_signal
    425,  # io_uring_setup
    426,  # io_uring_enter
    427,  # io_uring_register
    434,  # pidfd_open
    435,  # clone3
)
OMISSION_EXIT = 86
OMISSION_PREFIX = "VO-OMITTED-SOURCE-v1"
BOOTSTRAP_FD = 6
MAX_BOOTSTRAP_BYTES = 16 * 1024 * 1024


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [
        ("len", ctypes.c_ushort),
        ("filter", ctypes.POINTER(_SockFilter)),
    ]


class _OmittedSource(FileNotFoundError):
    pass


class _RestrictedStat:
    __slots__ = ("st_mode", "st_size", "st_mtime_ns", "st_uid", "st_gid")

    def __init__(self, record: dict[str, object]):
        self.st_mode = int(record["mode"])
        self.st_size = int(record["size"])
        self.st_mtime_ns = int(record["mtime_ns"])
        self.st_uid = int(record["uid"])
        self.st_gid = int(record["gid"])

    def __getattr__(self, name: str) -> object:
        raise PermissionError(f"unsupported metadata field {name!r}")


class _RestrictedDirEntry:
    __slots__ = ("name", "path", "_record")

    def __init__(self, path: str, record: dict[str, object]):
        self.name = Path(path).name
        self.path = path
        self._record = record

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        del follow_symlinks
        return self._record["kind"] == "file"

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        del follow_symlinks
        return self._record["kind"] == "directory"

    def is_symlink(self) -> bool:
        return False

    def stat(self, *, follow_symlinks: bool = True) -> _RestrictedStat:
        del follow_symlinks
        return _RestrictedStat(self._record)

    def inode(self) -> int:
        raise PermissionError("inode metadata is outside the source contract")


class _RestrictedScandir:
    def __init__(self, entries: list[_RestrictedDirEntry]):
        self._iterator = iter(entries)

    def __iter__(self):
        return self

    def __next__(self) -> _RestrictedDirEntry:
        return next(self._iterator)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def close(self) -> None:
        pass


def _statement(code: int, value: int) -> _SockFilter:
    return _SockFilter(code, 0, 0, value)


def _jump(code: int, value: int, yes: int, no: int) -> _SockFilter:
    return _SockFilter(code, yes, no, value)


def _install_seccomp() -> None:
    if os.uname().machine != "x86_64":
        raise RuntimeError("candidate seccomp currently requires x86_64 Linux")
    instructions = [
        _statement(BPF_LD | BPF_W | BPF_ABS, 4),
        _jump(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
        _statement(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | errno.EPERM),
        _statement(BPF_LD | BPF_W | BPF_ABS, 0),
    ]
    for syscall in BLOCKED_SYSCALLS:
        instructions.extend(
            (
                _jump(BPF_JMP | BPF_JEQ | BPF_K, syscall, 0, 1),
                _statement(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | errno.EPERM),
            )
        )
    instructions.append(_statement(BPF_RET | BPF_K, SECCOMP_RET_ALLOW))
    array = (_SockFilter * len(instructions))(*instructions)
    program = _SockFprog(len(instructions), array)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "prctl(PR_SET_NO_NEW_PRIVS)")
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(program)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "prctl(PR_SET_SECCOMP)")


def _canonical_repo_path(value: object) -> str:
    path = os.fspath(value)
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    normalized = os.path.normpath(candidate.as_posix())
    if normalized == "/repo":
        return "."
    if not normalized.startswith("/repo/"):
        raise PermissionError(f"path {path!r} is outside admitted candidate files")
    return normalized.removeprefix("/repo/")


def _main(arguments: list[str]) -> int:
    if len(arguments) < 2:
        raise RuntimeError("candidate runtime requires entrypoint and mode")
    entrypoint = arguments[0]
    chunks = []
    total = 0
    try:
        while True:
            chunk = os.read(BOOTSTRAP_FD, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BOOTSTRAP_BYTES:
                raise RuntimeError("candidate bootstrap config exceeds byte bound")
            chunks.append(chunk)
    finally:
        os.close(BOOTSTRAP_FD)
    config = json.loads(b"".join(chunks).decode("ascii"))
    if set(config) != {
        "admitted_imports",
        "admitted_paths",
        "metadata",
        "nonce",
        "omitted_source",
        "program_paths",
    }:
        raise RuntimeError("candidate runtime config has unexpected fields")
    nonce = config["nonce"]
    admitted_imports = frozenset(config["admitted_imports"])
    admitted_paths = frozenset(config["admitted_paths"])
    program_paths = frozenset(config["program_paths"])
    visible_paths = admitted_paths | program_paths
    omitted = config["omitted_source"]
    metadata = config["metadata"]
    if (
        not isinstance(nonce, str)
        or len(nonce) != 64
        or omitted is not None
        and omitted not in admitted_paths
    ):
        raise RuntimeError("candidate runtime authority is invalid")

    source_path = Path(entrypoint)
    source = source_path.read_bytes()
    code = compile(source, entrypoint, "exec", dont_inherit=True)
    for name in sorted(admitted_imports):
        if name in {"ctypes", "_ctypes"}:
            raise RuntimeError("ctypes is never admitted to candidate code")
        importlib.import_module(name)

    original_import = builtins.__import__
    original_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    descriptor_paths: dict[int, str] = {}

    def authorize(value: object, *, directory: bool = False) -> str:
        relative = _canonical_repo_path(value)
        if relative == omitted:
            raise _OmittedSource(relative)
        if directory:
            prefix = "" if relative == "." else relative + "/"
            if not any(
                path == relative or path.startswith(prefix)
                for path in visible_paths
            ):
                raise PermissionError(
                    f"directory {relative!r} is outside admitted candidate files"
                )
        elif relative not in visible_paths:
            raise PermissionError(
                f"path {relative!r} is outside admitted candidate files"
            )
        return relative

    def restricted_import(
        name: str,
        globals=None,
        locals=None,
        fromlist=(),
        level: int = 0,
    ):
        root = name.split(".", 1)[0]
        if level == 0 and root not in admitted_imports:
            raise ImportError(f"candidate import {name!r} is not admitted")
        return original_import(name, globals, locals, fromlist, level)

    def restricted_open(file, mode="r", *args, **kwargs):
        if any(character in mode for character in "wax+"):
            raise PermissionError("candidate file writes are forbidden")
        relative = authorize(file)
        stream = original_open(file, mode, *args, **kwargs)
        descriptor_paths[stream.fileno()] = relative
        return stream

    def restricted_io_open(file, mode="r", *args, **kwargs):
        if isinstance(file, int):
            return original_io_open(file, mode, *args, **kwargs)
        if any(character in mode for character in "wax+"):
            raise PermissionError("candidate file writes are forbidden")
        relative = authorize(file)
        stream = original_io_open(file, mode, *args, **kwargs)
        descriptor_paths[stream.fileno()] = relative
        return stream

    def restricted_os_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None or flags & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        ):
            raise PermissionError("candidate os.open mode is forbidden")
        relative = authorize(path)
        descriptor = original_os_open(path, flags, mode)
        descriptor_paths[descriptor] = relative
        return descriptor

    def restricted_stat(path, *, dir_fd=None, follow_symlinks=True):
        del follow_symlinks
        if dir_fd is not None:
            raise PermissionError("candidate dir_fd metadata is forbidden")
        relative = authorize(path, directory=True)
        try:
            record = metadata[relative]
        except KeyError as error:
            raise PermissionError(
                f"metadata for {relative!r} is outside the source contract"
            ) from error
        return _RestrictedStat(record)

    def restricted_fstat(descriptor: int):
        try:
            relative = descriptor_paths[descriptor]
            record = metadata[relative]
        except KeyError as error:
            raise PermissionError(
                "descriptor metadata is outside the source contract"
            ) from error
        return _RestrictedStat(record)

    def restricted_access(path, mode, *, dir_fd=None, effective_ids=False, follow_symlinks=True):
        del mode, effective_ids, follow_symlinks
        if dir_fd is not None:
            raise PermissionError("candidate dir_fd access is forbidden")
        try:
            authorize(path, directory=True)
        except (FileNotFoundError, PermissionError):
            return False
        return True

    def restricted_scandir(path="."):
        relative = authorize(path, directory=True)
        prefix = "" if relative == "." else relative + "/"
        entries = []
        for candidate, record in metadata.items():
            if candidate == relative or not candidate.startswith(prefix):
                continue
            suffix = candidate[len(prefix):]
            if "/" in suffix:
                continue
            entries.append(_RestrictedDirEntry(f"/repo/{candidate}", record))
        return _RestrictedScandir(sorted(entries, key=lambda entry: entry.name))

    def restricted_listdir(path="."):
        return [entry.name for entry in restricted_scandir(path)]

    def audit(event: str, audit_arguments: tuple[object, ...]) -> None:
        if event in {
            "ctypes.dlopen",
            "ctypes.dlsym",
            "object.__getattr__",
            "os.fork",
            "os.forkpty",
            "os.posix_spawn",
            "os.posix_spawnp",
            "os.system",
            "pty.spawn",
            "socket.__new__",
            "subprocess.Popen",
            "sys._current_frames",
            "sys._getframe",
            "sys._getframemodulename",
            "sys.setprofile",
            "sys.settrace",
        }:
            raise PermissionError(f"candidate audit event {event!r} is forbidden")
        if event == "exec":
            candidate_code = audit_arguments[0]
            if getattr(candidate_code, "co_filename", None) != entrypoint:
                raise PermissionError("candidate dynamic exec is forbidden")

    builtins.__import__ = restricted_import
    builtins.open = restricted_open
    io.open = restricted_io_open
    os.open = restricted_os_open
    os.stat = restricted_stat
    os.lstat = restricted_stat
    os.fstat = restricted_fstat
    os.access = restricted_access
    os.scandir = restricted_scandir
    os.listdir = restricted_listdir
    os.chdir = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        PermissionError("candidate chdir is forbidden")
    )
    os.readlink = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        PermissionError("candidate readlink is forbidden")
    )
    _install_seccomp()
    sys.addaudithook(audit)
    for module_name in tuple(sys.modules):
        root = module_name.split(".", 1)[0]
        if root not in admitted_imports and module_name not in {"builtins", "sys"}:
            sys.modules.pop(module_name, None)
    sys.argv = [entrypoint, *arguments[1:]]
    globals_dict = {
        "__builtins__": builtins,
        "__file__": entrypoint,
        "__name__": "__main__",
        "__package__": None,
    }
    try:
        exec(code, globals_dict, globals_dict)
    except _OmittedSource as error:
        if omitted is None or error.args != (omitted,):
            raise
        sys.stdout.flush()
        sys.stderr.write(f"{OMISSION_PREFIX} {nonce} {omitted}\n")
        sys.stderr.flush()
        return OMISSION_EXIT
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException as error:
        print(f"candidate-runtime: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(125)
