#!/usr/bin/env python3
"""Resolve bounded scenario probe expressions from an exact linked ELF."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

SYMBOL_EXPRESSION_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)(?:\+(0x[0-9a-fA-F]+))?$"
)


class ProbeBindingError(RuntimeError):
    pass


def configured_nm(nm: str | os.PathLike[str] | None = None) -> str:
    if nm is not None:
        return os.fspath(nm)

    modern_nm = os.environ.get("MODERN_NM")
    if modern_nm:
        return modern_nm

    toolchain_root = os.environ.get("MODERN_TOOLCHAIN_ROOT")
    if toolchain_root:
        return str(Path(toolchain_root) / "bin" / "arm-none-eabi-nm")

    return os.environ.get("NM", "arm-none-eabi-nm")


def resolve_elf_symbol(
    elf: Path,
    symbol: str,
    nm: str | os.PathLike[str] | None = None,
) -> tuple[int, int]:
    nm_command = configured_nm(nm)
    try:
        completed = subprocess.run(
            [nm_command, "-S", "--defined-only", str(elf)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeBindingError(
            f"ELF symbol tool {nm_command!r} timed out while reading {elf}"
        ) from exc
    except OSError as exc:
        raise ProbeBindingError(
            f"cannot launch ELF symbol tool {nm_command!r}: {exc}. "
            "Set --nm/MODERN_NM to an executable nm path, or set "
            "MODERN_TOOLCHAIN_ROOT to an arm-none-eabi toolchain root."
        ) from exc
    if completed.returncode != 0:
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"exit status {completed.returncode}"
        )
        raise ProbeBindingError(f"{nm_command} failed on {elf}: {detail}")

    pattern = re.compile(
        r"^([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+\S\s+"
        + re.escape(symbol)
        + r"$"
    )
    for line in completed.stdout.splitlines():
        match = pattern.match(line.strip())
        if match:
            return int(match.group(1), 16), int(match.group(2), 16)
    raise ProbeBindingError(f"{symbol} is missing from {elf}")


class ElfSymbolResolver:
    def __init__(
        self,
        elf: Path,
        nm: str | os.PathLike[str] | None = None,
    ):
        self.elf = elf
        self.nm = nm
        self._cache: dict[str, tuple[int, int]] = {}

    def __call__(self, symbol: str) -> tuple[int, int]:
        if symbol not in self._cache:
            self._cache[symbol] = resolve_elf_symbol(self.elf, symbol, self.nm)
        return self._cache[symbol]


def resolve_probe_expression(
    value: str,
    size: int,
    resolver: Callable[[str], tuple[int, int]],
    path: str,
) -> int:
    match = SYMBOL_EXPRESSION_RE.fullmatch(value)
    if match is None:
        raise ProbeBindingError(
            f"{path} must be a symbol expression such as "
            "'gExpansionLanguageMenuProbe+0x04'"
        )

    symbol, offset_text = match.groups()
    base, symbol_size = resolver(symbol)
    offset = int(offset_text, 16) if offset_text is not None else 0
    if offset + size > symbol_size:
        raise ProbeBindingError(
            f"{path} reads {symbol}+0x{offset:x} size {size}, past the "
            f"0x{symbol_size:x}-byte symbol in the exact ELF"
        )
    return base + offset
