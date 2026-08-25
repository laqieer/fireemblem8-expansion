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


def _parse_nm_symbols(output: str) -> dict[str, tuple[int, int]]:
    symbols = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        try:
            address = int(fields[0], 16)
            size = int(fields[1], 16)
        except ValueError:
            continue
        symbols[fields[3]] = (address, size)
    return symbols


def _check_layout_evidence(
    object_symbols: str,
    object_table: str,
    fixture_symbols: str,
    fixture_table: str,
    elf_symbols: str,
    language_symbols: str,
    map_text: str,
) -> None:
    object_entries = _parse_nm_symbols(object_symbols)
    fixture_entries = _parse_nm_symbols(fixture_symbols)
    elf_entries = _parse_nm_symbols(elf_symbols)

    stable = object_entries.get("sSaveStateStableLayout")
    shared_menu = object_entries.get("sDebugToolsMenuItemDefs")
    fixture_state = fixture_entries.get("sDebugSaveFixtureState")
    fixture_probe = fixture_entries.get("gDebugSaveFixtureProbe")
    elf_stable = elf_entries.get("sSaveStateStableLayout")
    elf_menu = elf_entries.get("sDebugToolsMenuItemDefs")
    elf_probe = elf_entries.get("gDebugToolsProbe")
    elf_fixture_state = elf_entries.get("sDebugSaveFixtureState")
    elf_fixture_probe = elf_entries.get("gDebugSaveFixtureProbe")
    language_probe = elf_entries.get("gExpansionLanguageMenuProbe")

    if not all(
        (
            stable,
            shared_menu,
            fixture_state,
            fixture_probe,
            elf_stable,
            elf_menu,
            elf_probe,
            elf_fixture_state,
            elf_fixture_probe,
            language_probe,
        )
    ):
        raise RuntimeError("missing parsed debug save-fixture layout symbol")
    if (
        stable != (0, 0x48)
        or shared_menu != (stable[0] + stable[1], 0xD8)
        or fixture_state != (0, 0x6C)
        or fixture_probe != (fixture_state[0] + fixture_state[1], 0x58)
    ):
        raise RuntimeError("shared debug menu or fixture object layout drifted")
    if "U sDebugToolsMenuItemDefs" not in language_symbols:
        raise RuntimeError("language menu no longer aliases shared debug storage")
    if "sLanguageMenuItemDefs" in language_symbols:
        raise RuntimeError("language menu emitted a separate item-definition buffer")
    if elf_menu[0] != elf_stable[0] + elf_stable[1] or elf_menu[1] != 0xD8:
        raise RuntimeError("linked shared debug menu storage drifted")
    if elf_stable[0] < elf_probe[0] + elf_probe[1]:
        raise RuntimeError("retained save-fixture storage overlaps the debug probe")
    if elf_fixture_probe[0] != elf_fixture_state[0] + elf_fixture_state[1]:
        raise RuntimeError("linked fixture state/probe span drifted")
    if language_probe[0] - (elf_menu[0] + elf_menu[1]) != 4:
        raise RuntimeError("shared menu to language probe alignment delta drifted")
    if not re.search(
        r"^00000000\s+\w+\s+O\s+ewram_data\s+00000048 "
        r"sSaveStateStableLayout$",
        object_table,
        re.MULTILINE,
    ) or not re.search(
        r"^00000048\s+\w+\s+O\s+ewram_data\s+000000d8 "
        r"sDebugToolsMenuItemDefs$",
        object_table,
        re.MULTILINE,
    ):
        raise RuntimeError("shared menu object section placement drifted")
    if not re.search(
        r"^00000000\s+\w+\s+O\s+debug_save_fixture_data\s+0000006c "
        r"sDebugSaveFixtureState$",
        fixture_table,
        re.MULTILINE,
    ) or not re.search(
        r"^0000006c\s+\w+\s+O\s+debug_save_fixture_data\s+00000058 "
        r"gDebugSaveFixtureProbe$",
        fixture_table,
        re.MULTILINE,
    ):
        raise RuntimeError("fixture object section placement drifted")
    if not re.search(
        r"^\s*ewram_data\s+0x[0-9a-fA-F]+\s+0x[0-9a-fA-F]+",
        map_text,
        re.MULTILINE,
    ) or not re.search(
        r"^\s*debug_save_fixture_data\s+0x[0-9a-fA-F]+\s+0xc4",
        map_text,
        re.MULTILINE,
    ):
        raise RuntimeError("linked EWRAM fixture section/headroom evidence drifted")


def check_layout_anchor(elf: Path) -> None:
    obj = elf.parent / "src" / "debugtools_tools.o"
    fixture_obj = elf.parent / "src" / "debug_save_fixture.o"
    language_obj = elf.parent / "src" / "expansion_language_menu.o"
    map_path = elf.with_suffix(".map")
    for path in (obj, fixture_obj, language_obj, map_path):
        if not path.is_file():
            raise RuntimeError(f"missing layout evidence input: {path}")

    def command_output(*command: str) -> str:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    _check_layout_evidence(
        command_output("arm-none-eabi-nm", "-S", str(obj)),
        command_output("arm-none-eabi-objdump", "-t", str(obj)),
        command_output("arm-none-eabi-nm", "-S", str(fixture_obj)),
        command_output("arm-none-eabi-objdump", "-t", str(fixture_obj)),
        command_output("arm-none-eabi-nm", "-S", str(elf)),
        command_output("arm-none-eabi-nm", "-S", str(language_obj)),
        map_path.read_text(encoding="utf-8"),
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
