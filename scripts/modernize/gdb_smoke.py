#!/usr/bin/env python3
"""Exercise the modern debug ELF through mGBA's remote GDB server."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time


DEFAULT_ELF = Path("build/expansion-modern/debug/aapcs/fireemblem8.elf")
DEFAULT_ROM = Path("build/expansion-modern/debug/aapcs/fireemblem8.gba")
DEFAULT_PORT = 2345


class SmokeError(RuntimeError):
    pass


def select_command(candidates: tuple[str, ...], purpose: str) -> str:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise SmokeError(f"{purpose} not found; tried: {', '.join(candidates)}")


def first_version_line(command: str) -> str:
    result = subprocess.run(
        [command, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    output = (result.stdout + result.stderr).strip().splitlines()
    return output[0] if output else f"{command} (version unavailable)"


def port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def wait_for_server(process: subprocess.Popen[str], port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SmokeError(f"mGBA exited before opening GDB port {port}")
        if not port_is_available(port):
            return
        time.sleep(0.1)
    raise SmokeError(f"mGBA did not open GDB port {port} within {timeout:.1f}s")


def gdb_command(gdb: str, elf: Path, breakpoint: str, port: int) -> list[str]:
    return [
        gdb,
        "-q",
        "-batch",
        str(elf),
        "-ex",
        "set pagination off",
        "-ex",
        "set confirm off",
        "-ex",
        f"target remote 127.0.0.1:{port}",
        "-ex",
        f"break {breakpoint}",
        "-ex",
        "continue",
        "-ex",
        'printf "GDB_SMOKE_BREAKPOINT_PC=%#x\\n", $pc',
        "-ex",
        "info registers sp lr pc cpsr",
        "-ex",
        "bt 2",
        "-ex",
        "disconnect",
    ]


def validate_gdb_output(output: str, breakpoint: str) -> None:
    required = (
        f"Breakpoint 1, {breakpoint}",
        "GDB_SMOKE_BREAKPOINT_PC=",
        f"<{breakpoint}>",
        "#0  ",
    )
    missing = [marker for marker in required if marker not in output]
    if missing:
        raise SmokeError(f"GDB session missed expected evidence: {', '.join(missing)}")


def terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_smoke(elf: Path, rom: Path, breakpoint: str, timeout: float) -> str:
    if not elf.is_file():
        raise SmokeError(f"debug ELF not found: {elf}")
    if not rom.is_file():
        raise SmokeError(f"debug ROM not found: {rom}")
    if not port_is_available(DEFAULT_PORT):
        raise SmokeError(f"TCP port {DEFAULT_PORT} is already in use")

    gdb = select_command(("arm-none-eabi-gdb", "gdb-multiarch"), "ARM GDB")
    mgba = select_command(("mgba",), "mGBA SDL frontend")

    with tempfile.TemporaryDirectory(prefix="fe8-gdb-smoke-") as temporary:
        temporary_path = Path(temporary)
        temporary_rom = temporary_path / "fireemblem8.gba"
        shutil.copyfile(rom, temporary_rom)

        log_path = temporary_path / "mgba.log"
        with log_path.open("w+", encoding="utf-8") as log:
            environment = os.environ.copy()
            environment.setdefault("SDL_VIDEODRIVER", "dummy")
            environment.setdefault("SDL_AUDIODRIVER", "dummy")
            process = subprocess.Popen(
                [mgba, "-g", str(temporary_rom)],
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
            )
            try:
                wait_for_server(process, DEFAULT_PORT, timeout)
                result = subprocess.run(
                    gdb_command(gdb, elf, breakpoint, DEFAULT_PORT),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                output = result.stdout + result.stderr
                if result.returncode != 0:
                    raise SmokeError(
                        f"GDB exited with {result.returncode}:\n{output.rstrip()}"
                    )
                validate_gdb_output(output, breakpoint)
            except (SmokeError, subprocess.TimeoutExpired) as error:
                log.flush()
                log.seek(0)
                mgba_output = log.read().strip()
                details = f"\nmGBA output:\n{mgba_output}" if mgba_output else ""
                raise SmokeError(f"{error}{details}") from error
            finally:
                terminate(process)

    return (
        f"gdb-smoke: ok -- {first_version_line(gdb)}; "
        f"{first_version_line(mgba)}; breakpoint={breakpoint}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", type=Path, default=DEFAULT_ELF)
    parser.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    parser.add_argument("--breakpoint", default="AgbMain")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        print(run_smoke(args.elf, args.rom, args.breakpoint, args.timeout))
    except (OSError, SmokeError) as error:
        print(f"gdb-smoke: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
