#!/usr/bin/env python3
"""Enter the validation-ownership mount/chroot sandbox and execute one command."""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path


MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_BIND = 4096
MS_REC = 16384
MS_REMOUNT = 32
PR_SET_NO_NEW_PRIVS = 38


def _mount(
    source: str | None,
    target: str,
    flags: int,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    encoded_source = None if source is None else os.fsencode(source)
    result = libc.mount(
        encoded_source,
        os.fsencode(target),
        None,
        flags,
        None,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def _bind(source: Path, target: Path, *, read_only: bool) -> None:
    _mount(str(source), str(target), MS_BIND | MS_REC)
    if read_only:
        _mount(
            None,
            str(target),
            MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV,
        )


def main() -> int:
    if not sys.flags.isolated or len(sys.argv) != 2:
        raise SystemExit("sandbox_exec requires isolated Python and one config")
    config_path = Path(sys.argv[1]).resolve(strict=True)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if set(config) != {
        "argv",
        "cwd",
        "environment",
        "read_only",
        "root",
        "writable",
    }:
        raise SystemExit("sandbox_exec config has unexpected fields")

    root = Path(config["root"]).resolve(strict=True)
    _mount(str(root), str(root), MS_BIND | MS_REC)
    for source_text, target_text in config["read_only"]:
        source = Path(source_text).resolve(strict=True)
        target = root / target_text.lstrip("/")
        _bind(source, target, read_only=True)
    _mount(
        None,
        str(root),
        MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV,
    )
    for source_text, target_text in config["writable"]:
        source = Path(source_text).resolve(strict=True)
        target = root / target_text.lstrip("/")
        _bind(source, target, read_only=False)

    os.chroot(root)
    os.chdir(config["cwd"])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "prctl")
    os.execve(
        config["argv"][0],
        config["argv"],
        config["environment"],
    )
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
