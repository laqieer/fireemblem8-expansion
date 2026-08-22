#!/usr/bin/env python3
"""Capture the real Chapter 4 scripted hit under standard and off policies."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GBA_PLAYTEST = REPO_ROOT / "tools" / "gba-playtest" / "gba_playtest.py"
COMBAT_SCENARIO = REPO_ROOT / "tools" / "gba-playtest" / "scenarios" / "combat.json"
sys.path.insert(0, str(GBA_PLAYTEST.parent))

from probe_bindings import ProbeBindingError, resolve_elf_symbol  # noqa: E402

PROBE_SYMBOL = "gBanimPresentationRuntimeProbe"
PROBE_FIELDS = (
    "policyId",
    "realHitPathObserved",
    "hitEffectsEnabled",
    "paletteFlashEnabled",
    "paletteFlashStarted",
    "hitNumbersVisible",
    "damageNumbersVisible",
    "critNumbersVisible",
    "autoLaunchArmed",
)
FIGHT_OBSERVATION_START = 3000
FIGHT_OBSERVATION_END = 8000
FIGHT_OBSERVATION_STEP = 5
UNIT_MAX_HP_OFFSET = 0x12
UNIT_CUR_HP_OFFSET = 0x13


class CheckError(RuntimeError):
    pass


def resolve_symbol(elf: Path, symbol: str) -> tuple[int, int]:
    try:
        return resolve_elf_symbol(elf, symbol)
    except ProbeBindingError as exc:
        raise CheckError(str(exc)) from exc


def resolve_probe(elf: Path) -> tuple[int, int]:
    address, size = resolve_symbol(elf, PROBE_SYMBOL)
    if size != 4 * len(PROBE_FIELDS):
        raise CheckError(
            f"{PROBE_SYMBOL} is {size} bytes, expected {4 * len(PROBE_FIELDS)}"
        )
    unit_base, _ = resolve_symbol(elf, "gUnitArrayRed")
    return address, unit_base


def capture(rom: Path, elf: Path, label: str, out_dir: Path) -> dict[str, int]:
    probe_base, unit_base = resolve_probe(elf)
    combat = json.loads(COMBAT_SCENARIO.read_text(encoding="utf-8"))
    probes = [
        {"address": f"0x{unit_base + UNIT_MAX_HP_OFFSET:08x}", "size": 1},
        {"address": f"0x{unit_base + UNIT_CUR_HP_OFFSET:08x}", "size": 1},
        *[
            {"address": f"0x{probe_base + 4 * index:08x}", "size": 4}
            for index, _ in enumerate(PROBE_FIELDS)
        ],
    ]
    scenario = {
        "schema_version": 1,
        "name": f"banim-presentation-{label}",
        "description": (
            "Internal issue #58 runtime evidence. Reuses combat.json's "
            "debug-only Chapter 4 route to its real scripted FIGHT. The test "
            "profile also arms the existing pending-launch handoff directly; "
            "the original inputs are retained to preserve the established "
            "title/world-map timing."
        ),
        "frames": combat["frames"],
        "checkpoints": [
            {
                "name": f"real-scripted-hit-{frame}",
                "frame": frame,
                "framebuffer": False,
                "probes": probes,
            }
            for frame in range(
                FIGHT_OBSERVATION_START,
                FIGHT_OBSERVATION_END + 1,
                FIGHT_OBSERVATION_STEP,
            )
        ],
    }
    scenario_path = out_dir / f"banim-presentation-{label}.json"
    capture_path = out_dir / f"banim-presentation-{label}.captured.json"
    scenario_path.write_text(
        json.dumps(scenario, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(GBA_PLAYTEST),
            "capture",
            "--rom",
            str(rom),
            "--scenario",
            str(scenario_path),
            "--output",
            str(capture_path),
        ],
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if completed.returncode:
        raise CheckError(
            f"gba-playtest {label} capture failed:\n{completed.stdout}{completed.stderr}"
        )

    expected_count = 2 + len(PROBE_FIELDS)
    saw_live_enemy = False
    for checkpoint in json.loads(capture_path.read_text(encoding="utf-8"))["checkpoints"]:
        values = [int(probe["value"], 16) for probe in checkpoint["probes"]]
        if len(values) != expected_count:
            raise CheckError(
                f"{label}: {checkpoint['name']} captured {len(values)} probes, "
                f"expected {expected_count}"
            )
        resolved = dict(zip(("enemyMaxHp", "enemyCurHp", *PROBE_FIELDS), values))
        if resolved["enemyMaxHp"] == 15 and resolved["enemyCurHp"] == 15:
            saw_live_enemy = True
        if (
            saw_live_enemy
            and resolved["enemyCurHp"] == 0
            and resolved["realHitPathObserved"] == 1
        ):
            resolved["enemyMaxHpBefore"] = 15
            resolved["enemyCurHpBefore"] = 15
            resolved["enemyCurHpAfter"] = 0
            return resolved

    raise CheckError(
        f"{label}: no checkpoint in frames "
        f"{FIGHT_OBSERVATION_START}..{FIGHT_OBSERVATION_END} proved the real scripted hit"
    )


def check(values: dict[str, int], label: str, expected: dict[str, int]) -> list[str]:
    failures = []
    if (
        values["enemyMaxHpBefore"] != 15
        or values["enemyCurHpBefore"] != 15
        or values["enemyCurHpAfter"] != 0
    ):
        failures.append(
            f"{label}: scripted FIGHT HP transition was "
            f"{values['enemyMaxHpBefore']}/{values['enemyCurHpBefore']}"
            f"->{values['enemyCurHpAfter']}, expected 15/15->0"
        )
    for name, value in expected.items():
        if values[name] != value:
            failures.append(f"{label}: {name}={values[name]}, expected {value}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standard-rom", required=True, type=Path)
    parser.add_argument("--standard-elf", required=True, type=Path)
    parser.add_argument("--off-rom", required=True, type=Path)
    parser.add_argument("--off-elf", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        standard = capture(args.standard_rom, args.standard_elf, "standard", args.out_dir)
        off = capture(args.off_rom, args.off_elf, "off", args.out_dir)
        failures = check(
            standard,
            "standard",
            {
                "policyId": 0,
                "realHitPathObserved": 1,
                "hitEffectsEnabled": 1,
                "paletteFlashEnabled": 1,
                "paletteFlashStarted": 0,
                "hitNumbersVisible": 1,
                "damageNumbersVisible": 1,
                "critNumbersVisible": 1,
                "autoLaunchArmed": 1,
            },
        )
        failures += check(
            off,
            "off",
            {
                "policyId": 3,
                "realHitPathObserved": 1,
                "hitEffectsEnabled": 0,
                "paletteFlashEnabled": 0,
                "paletteFlashStarted": 0,
                "hitNumbersVisible": 0,
                "damageNumbersVisible": 0,
                "critNumbersVisible": 0,
                "autoLaunchArmed": 1,
            },
        )
        if failures:
            raise CheckError("\n".join(failures))
    except (CheckError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        "Battle-presentation runtime checks passed: "
        "real Ch4 scripted hit standard=enabled off=suppressed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
