#!/usr/bin/env python3
"""Execute issue #68's real-ROM mGBA logging positive and negative controls."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAYTEST_DIR = ROOT / "tools" / "gba-playtest"
sys.path.insert(0, str(PLAYTEST_DIR))
import gba_playtest  # noqa: E402

BOOT_SCENARIO = PLAYTEST_DIR / "scenarios" / "boot.json"
READY_MESSAGE = "FE8LOG ready"
MGBA_GBA_DEBUG_CATEGORY = 10
MGBA_LOG_INFO = 0x08


def check_elf(path: Path, enabled: bool) -> None:
    nm = shutil.which("arm-none-eabi-nm")
    if nm is None:
        raise RuntimeError("arm-none-eabi-nm is required for the logging symbol check")
    completed = subprocess.run([nm, str(path)], capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"cannot inspect ELF symbols: {completed.stderr.strip()}")
    symbols = completed.stdout
    if enabled and "ExpansionLog_Write" not in symbols:
        raise RuntimeError("debug ELF is missing ExpansionLog_Write")
    if not enabled and "ExpansionLog_" in symbols:
        raise RuntimeError("release ELF retains ExpansionLog backend symbols")
    image = path.read_bytes()
    if enabled and READY_MESSAGE.encode("ascii") not in image:
        raise RuntimeError("debug ELF is missing the deterministic logging message")
    if not enabled and READY_MESSAGE.encode("ascii") in image:
        raise RuntimeError("release ELF retains the debug-only logging message")


def run_capture(rom: Path, enabled: bool) -> None:
    scenario = gba_playtest.load_scenario(BOOT_SCENARIO)
    temporary_root = Path(os.environ.get("TMPDIR", ROOT / "build" / "debuglog-tmp"))
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="debuglog-", dir=temporary_root) as temporary:
        capture_path = Path(temporary) / "mgba-debug.log"
        previous = os.environ.get("GBA_PLAYTEST_LOG_CAPTURE")
        previous_tmpdir = os.environ.get("TMPDIR")
        os.environ["GBA_PLAYTEST_LOG_CAPTURE"] = str(capture_path)
        os.environ["TMPDIR"] = str(temporary_root)
        try:
            gba_playtest.capture(rom, scenario, None, 0)
        finally:
            if previous is None:
                os.environ.pop("GBA_PLAYTEST_LOG_CAPTURE", None)
            else:
                os.environ["GBA_PLAYTEST_LOG_CAPTURE"] = previous
            if previous_tmpdir is None:
                os.environ.pop("TMPDIR", None)
            else:
                os.environ["TMPDIR"] = previous_tmpdir
        records = []
        for line in capture_path.read_text(encoding="utf-8").splitlines():
            fields = line.split("\t", 2)
            if len(fields) != 3:
                raise RuntimeError(f"malformed mGBA log record: {line!r}")
            try:
                records.append((int(fields[0]), int(fields[1]), fields[2]))
            except ValueError as exc:
                raise RuntimeError(f"malformed mGBA log record: {line!r}") from exc
    ready_records = [record for record in records if record[2] == READY_MESSAGE]
    expected = (MGBA_GBA_DEBUG_CATEGORY, MGBA_LOG_INFO, READY_MESSAGE)
    if enabled and ready_records != [expected]:
        raise RuntimeError(
            "debug mGBA capture expected exactly one gba.debug info record "
            f"{expected!r}, found {ready_records!r}"
        )
    if not enabled and ready_records:
        raise RuntimeError(
            f"release mGBA capture unexpectedly contains {READY_MESSAGE!r}: {ready_records!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--config", choices=("debug", "release"), required=True)
    args = parser.parse_args()
    if not args.rom.is_file() or not args.elf.is_file():
        raise RuntimeError("the exact built ROM and ELF must exist before logging verification")
    enabled = args.config == "debug"
    check_elf(args.elf, enabled)
    run_capture(args.rom, enabled)
    provenance = gba_playtest.rom_provenance(args.rom)
    print(
        "debuglog mGBA capture: "
        f"config={args.config} title={provenance['title']} sha1={provenance['sha1']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, gba_playtest.PlaytestError) as exc:
        print(f"debuglog check: {exc}", file=sys.stderr)
        raise SystemExit(2)
