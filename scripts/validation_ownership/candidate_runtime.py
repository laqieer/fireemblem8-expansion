#!/usr/bin/env python3
"""Trusted kernel-policy bootstrap for candidate registry Python."""

from __future__ import annotations

import array
import ctypes
import errno
import glob
import importlib
import io
import json
import mmap
import os
import socket
import stat
import struct
import sys
from pathlib import Path


PR_SET_NO_NEW_PRIVS = 38
SECCOMP_SET_MODE_FILTER = 1
SECCOMP_FILTER_FLAG_NEW_LISTENER = 8
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_USER_NOTIF = 0x7FC00000
SECCOMP_RET_ERRNO = 0x00050000
AUDIT_ARCH_X86_64 = 0xC000003E
BPF_LD = 0x00
BPF_W = 0x00
BPF_ABS = 0x20
BPF_JMP = 0x05
BPF_JEQ = 0x10
BPF_K = 0x00
BPF_RET = 0x06
SYS_SECCOMP = 317
SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_REFER = 1 << 13
LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14
LANDLOCK_ACCESS_FS_IOCTL_DEV = 1 << 15
BLOCKED_SYSCALLS = (
    29, 30, 31,                  # SysV shared memory
    41, 53,                      # socket/socketpair
    56, 57, 58,                  # clone/fork/vfork
    59, 322,                     # execve/execveat/fexecve
    62, 200, 234, 424,           # process signaling/pidfd signaling
    64, 65, 66,                  # SysV semaphores
    68, 69, 70, 71,              # SysV messages
    101, 310, 311,               # ptrace/process_vm
    137, 138,                    # statfs/fstatfs unsupported metadata
    134, 175, 176, 313,          # native module loading
    179,                         # quotactl
    191, 192, 193, 194, 195, 196,  # extended attributes
    253, 254, 294,               # inotify
    155, 161, 165, 272, 308,     # root/mount/namespaces
    246, 298, 304,               # kexec/perf/open_by_handle_at
    319, 320,                    # memfd/kexec_file_load
    321, 323,                    # bpf/userfaultfd
    425, 426, 427,               # io_uring
    434, 435,                    # pidfd_open/clone3
)
NOTIFIED_SYSCALLS = (
    2, 3, 4, 5, 6, 9, 10, 21,   # open/close/stat/mmap/access
    89, 217,                     # readlink/getdents64
    257, 262, 267, 269,          # *at filesystem calls
    329, 332, 437, 439,          # pkey_mprotect/statx/openat2/faccessat2
)
CONTROL_FD = 7
BOOTSTRAP_FD = 6
MAX_BOOTSTRAP_BYTES = 16 * 1024 * 1024
CONTROL_MAGIC = b"VO-SECCOMP-LISTENER-v1"
CONTROLLED_DENIAL_EXIT = 86


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


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
        ("reserved", ctypes.c_uint32),
    ]


def _statement(code: int, value: int) -> _SockFilter:
    return _SockFilter(code, 0, 0, value)


def _jump(code: int, value: int, yes: int, no: int) -> _SockFilter:
    return _SockFilter(code, yes, no, value)


def _no_new_privileges(libc) -> None:
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "prctl(PR_SET_NO_NEW_PRIVS)")


def _install_landlock() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(
        SYS_LANDLOCK_CREATE_RULESET,
        0,
        0,
        LANDLOCK_CREATE_RULESET_VERSION,
    )
    if abi < 1:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "landlock ABI")
    handled = (
        LANDLOCK_ACCESS_FS_EXECUTE
        | LANDLOCK_ACCESS_FS_WRITE_FILE
        | LANDLOCK_ACCESS_FS_READ_FILE
        | LANDLOCK_ACCESS_FS_READ_DIR
        | LANDLOCK_ACCESS_FS_REMOVE_DIR
        | LANDLOCK_ACCESS_FS_REMOVE_FILE
        | LANDLOCK_ACCESS_FS_MAKE_CHAR
        | LANDLOCK_ACCESS_FS_MAKE_DIR
        | LANDLOCK_ACCESS_FS_MAKE_REG
        | LANDLOCK_ACCESS_FS_MAKE_SOCK
        | LANDLOCK_ACCESS_FS_MAKE_FIFO
        | LANDLOCK_ACCESS_FS_MAKE_BLOCK
        | LANDLOCK_ACCESS_FS_MAKE_SYM
    )
    if abi >= 2:
        handled |= LANDLOCK_ACCESS_FS_REFER
    if abi >= 3:
        handled |= LANDLOCK_ACCESS_FS_TRUNCATE
    if abi >= 5:
        handled |= LANDLOCK_ACCESS_FS_IOCTL_DEV
    ruleset = _LandlockRulesetAttr(handled)
    ruleset_fd = libc.syscall(
        SYS_LANDLOCK_CREATE_RULESET,
        ctypes.byref(ruleset),
        ctypes.sizeof(ruleset),
        0,
    )
    if ruleset_fd < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "landlock_create_ruleset")
    repository_fd = os.open(
        "/repo",
        os.O_PATH | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        rule = _LandlockPathBeneathAttr(
            LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR,
            repository_fd,
            0,
        )
        if libc.syscall(
            SYS_LANDLOCK_ADD_RULE,
            ruleset_fd,
            LANDLOCK_RULE_PATH_BENEATH,
            ctypes.byref(rule),
            0,
        ) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), "landlock_add_rule")
        _no_new_privileges(libc)
        if libc.syscall(SYS_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), "landlock_restrict_self")
    finally:
        os.close(repository_fd)
        os.close(ruleset_fd)


def _install_seccomp_listener() -> int:
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
    for syscall in NOTIFIED_SYSCALLS:
        instructions.extend(
            (
                _jump(BPF_JMP | BPF_JEQ | BPF_K, syscall, 0, 1),
                _statement(BPF_RET | BPF_K, SECCOMP_RET_USER_NOTIF),
            )
        )
    instructions.append(_statement(BPF_RET | BPF_K, SECCOMP_RET_ALLOW))
    array_value = (_SockFilter * len(instructions))(*instructions)
    program = _SockFprog(len(instructions), array_value)
    libc = ctypes.CDLL(None, use_errno=True)
    _no_new_privileges(libc)
    listener = libc.syscall(
        SYS_SECCOMP,
        SECCOMP_SET_MODE_FILTER,
        SECCOMP_FILTER_FLAG_NEW_LISTENER,
        ctypes.byref(program),
    )
    if listener < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "seccomp listener")
    return listener


def _transfer_listener(listener: int) -> None:
    channel = socket.socket(fileno=CONTROL_FD)
    descriptors = array.array("i", [listener])
    try:
        channel.sendmsg(
            [CONTROL_MAGIC],
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptors)],
        )
    finally:
        channel.close()
        os.close(listener)


def _read_bootstrap() -> dict[str, object]:
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
    if set(config) != {"admitted_imports", "program_paths"}:
        raise RuntimeError("candidate runtime config has unexpected fields")
    return config


def _main(arguments: list[str]) -> int:
    if len(arguments) < 2:
        raise RuntimeError("candidate runtime requires entrypoint and mode")
    entrypoint = arguments[0]
    config = _read_bootstrap()
    admitted_imports = frozenset(config["admitted_imports"])
    source = Path(entrypoint).read_bytes()
    code = compile(source, entrypoint, "exec", dont_inherit=True)
    for name in sorted(admitted_imports):
        importlib.import_module(name)
    _install_landlock()
    listener = _install_seccomp_listener()
    _transfer_listener(listener)
    for module_name in tuple(sys.modules):
        root = module_name.split(".", 1)[0]
        if root not in admitted_imports and module_name not in {"builtins", "sys"}:
            sys.modules.pop(module_name, None)
    sys.argv = [entrypoint, *arguments[1:]]
    globals_dict = {
        "__builtins__": __builtins__,
        "__file__": entrypoint,
        "__name__": "__main__",
        "__package__": None,
    }
    try:
        exec(code, globals_dict, globals_dict)
    except OSError:
        return CONTROLLED_DENIAL_EXIT
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException as error:
        print(f"candidate-runtime: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(125)
