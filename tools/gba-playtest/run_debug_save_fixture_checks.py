#!/usr/bin/env python3
"""Run TC-DEBUGSAVE-001 generated-source libmGBA checks."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYTEST = REPO_ROOT / "tools" / "gba-playtest" / "gba_playtest.py"
FIXTURE_DIR = REPO_ROOT / "tools" / "gba-playtest" / "tests"
sys.path.insert(0, str(FIXTURE_DIR))

import sram_fixture


def _verify(
    rom: Path,
    elf: Path,
    scenario_name: str,
    sram_image: Path,
) -> None:
    scenario = REPO_ROOT / "tools" / "gba-playtest" / "scenarios" / scenario_name
    expected = REPO_ROOT / "tools" / "gba-playtest" / "fingerprints" / scenario_name
    subprocess.run(
        [
            sys.executable,
            str(PLAYTEST),
            "verify",
            "--rom",
            str(rom),
            "--elf",
            str(elf),
            "--scenario",
            str(scenario),
            "--sram-image",
            str(sram_image),
            "--expected",
            str(expected),
            "--policy",
            "behavior",
        ],
        check=True,
        cwd=REPO_ROOT,
    )


def _check_symbols(elf: Path, config: str) -> None:
    output = subprocess.run(
        ["arm-none-eabi-nm", str(elf)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    fixture_symbols = [
        line
        for line in output.splitlines()
        if "DebugSaveFixture_" in line
        or "gDebugSaveFixtureProbe" in line
        or "ReadGameSaveFromImage" in line
        or "ReadSuspendSaveFromImage" in line
    ]

    if config == "debug" and not any(
        "gDebugSaveFixtureProbe" in line for line in fixture_symbols
    ):
        raise RuntimeError("debug fixture probe is missing from the debug ELF")
    if config == "release" and fixture_symbols:
        raise RuntimeError(
            "release ELF retained debug save-fixture symbols: "
            + ", ".join(fixture_symbols)
        )


def check_layout_anchor(elf: Path) -> None:
    obj = elf.parent / "src" / "debugtools_tools.o"
    fixture_obj = elf.parent / "src" / "debug_save_fixture.o"
    if not obj.is_file():
        raise RuntimeError(f"missing debugtools object for layout check: {obj}")
    if not fixture_obj.is_file():
        raise RuntimeError(
            f"missing debug save-fixture object for layout check: {fixture_obj}"
        )

    object_symbols = subprocess.run(
        ["arm-none-eabi-nm", "-S", str(obj)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    object_table = subprocess.run(
        ["arm-none-eabi-objdump", "-t", str(obj)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    fixture_object_symbols = subprocess.run(
        ["arm-none-eabi-nm", "-S", str(fixture_obj)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    fixture_object_table = subprocess.run(
        ["arm-none-eabi-objdump", "-t", str(fixture_obj)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    elf_symbols = subprocess.run(
        ["arm-none-eabi-nm", "-S", str(elf)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    object_anchor = re.search(
        r"^00000000 00000048 \w sSaveStateStableLayout$",
        object_symbols,
        re.MULTILINE,
    )
    object_section = re.search(
        r"^00000000\s+\w+\s+O\s+ewram_data\s+00000048 "
        r"sSaveStateStableLayout$",
        object_table,
        re.MULTILINE,
    )
    elf_anchor = re.search(
        r"^0203189c 00000048 \w sSaveStateStableLayout$",
        elf_symbols,
        re.MULTILINE,
    )
    stable_probe = re.search(
        r"^02031818 00000084 \w gDebugToolsProbe$",
        elf_symbols,
        re.MULTILINE,
    )
    shared_menu_storage = re.search(
        r"^00000048 000000d8 \w sDebugToolsMenuItemDefs$",
        object_symbols,
        re.MULTILINE,
    )
    shared_menu_section = re.search(
        r"^00000048\s+\w+\s+O\s+ewram_data\s+000000d8 "
        r"sDebugToolsMenuItemDefs$",
        object_table,
        re.MULTILINE,
    )
    fixture_probe = re.search(
        r"^0000006c 00000058 \w gDebugSaveFixtureProbe$",
        fixture_object_symbols,
        re.MULTILINE,
    )
    fixture_state = re.search(
        r"^00000000 0000006c \w sDebugSaveFixtureState$",
        fixture_object_symbols,
        re.MULTILINE,
    )
    fixture_probe_section = re.search(
        r"^0000006c\s+\w+\s+O\s+debug_save_fixture_data\s+00000058 "
        r"gDebugSaveFixtureProbe$",
        fixture_object_table,
        re.MULTILINE,
    )
    fixture_state_section = re.search(
        r"^00000000\s+\w+\s+O\s+debug_save_fixture_data\s+0000006c "
        r"sDebugSaveFixtureState$",
        fixture_object_table,
        re.MULTILINE,
    )
    recovered_menu_bytes = (3 * 4 + 6) * 0x24 - 0xD8

    if not all(
        (
            object_anchor,
            object_section,
            elf_anchor,
            stable_probe,
            shared_menu_storage,
            shared_menu_section,
            fixture_probe,
            fixture_state,
            fixture_probe_section,
            fixture_state_section,
        )
    ):
        raise RuntimeError(
            "debug Save State layout, shared menu storage, or the 0x100-byte "
            "fixture state/preview/probe contract drifted"
        )
    if recovered_menu_bytes < 0x1B0:
        raise RuntimeError(
            "shared debugtools menu storage recovered less than 432 bytes "
            "of persistent EWRAM"
        )


def _check_exact_positive_hash() -> None:
    path = (
        REPO_ROOT
        / "tools"
        / "gba-playtest"
        / "fingerprints"
        / "debug-save-fixture-positive-modern-debug.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    hashes = {
        checkpoint["sram_hash"]
        for checkpoint in data["checkpoints"]
    }
    if len(hashes) != 1:
        raise RuntimeError(
            "positive fixture checkpoints do not preserve one exact SRAM hash: "
            + ", ".join(sorted(hashes))
        )
    only = next(iter(hashes))
    if not only.startswith("fnv1a64-sram:"):
        raise RuntimeError(f"positive fixture uses a normalized SRAM hash: {only}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--config", choices=("debug", "release"), required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    source = sram_fixture.write_debug_save_fixture_source(
        args.out_dir / "debug-save-fixture-source.sav",
        REPO_ROOT,
        include_runtime_roster=args.config == "debug",
    )

    _check_symbols(args.elf, args.config)

    if args.config == "release":
        _verify(
            args.rom,
            args.elf,
            "debug-save-fixture-modern-release.json",
            source,
        )
        print("PASS: release omits fixture symbols and ignores debug input")
        return 0

    check_layout_anchor(args.elf)

    current = sram_fixture.write_deterministic_current_fixture(
        args.out_dir / "debug-save-fixture-current.sav",
        REPO_ROOT,
    )
    incompatible = sram_fixture.write_fixture(
        args.out_dir / "debug-save-fixture-incompatible.sav",
        sram_fixture.STATE_CONFIG_INCOMPATIBLE,
        REPO_ROOT,
    )

    _verify(
        args.rom,
        args.elf,
        "debug-save-fixture-positive-modern-debug.json",
        source,
    )
    _verify(
        args.rom,
        args.elf,
        "debug-save-fixture-cancel-modern-debug.json",
        source,
    )
    _verify(
        args.rom,
        args.elf,
        "debug-save-fixture-invalid-modern-debug.json",
        current,
    )
    _verify(
        args.rom,
        args.elf,
        "debug-save-fixture-incompatible-modern-debug.json",
        incompatible,
    )
    _verify(
        args.rom,
        args.elf,
        "debug-save-fixture-interruption-modern-debug.json",
        source,
    )
    _check_exact_positive_hash()
    print(
        "PASS: volatile fixture positive/cancel/invalid/incompatible/"
        "interruption/reset paths preserve exact SRAM"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
