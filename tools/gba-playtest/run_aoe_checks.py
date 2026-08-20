#!/usr/bin/env python3
"""Booted positive/negative runtime checks for the issue #42 typed AoE API."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GBA_PLAYTEST = REPO_ROOT / "tools" / "gba-playtest" / "gba_playtest.py"
SCENARIO_DIR = REPO_ROOT / "tools" / "gba-playtest" / "scenarios"
sys.path.insert(0, str(GBA_PLAYTEST.parent))

from probe_bindings import ProbeBindingError, resolve_elf_symbol  # noqa: E402

PROBE_SYMBOL = "gExpansionAoEReferenceProbe"
PROBE_MAGIC = 0x414F4531
PROBE_FIELDS = (
    "magic",
    "enabled",
    "runCount",
    "buildResult",
    "executionOutcome",
    "sourceUnitId",
    "targetCount",
    "totalTargetCount",
    "firstTargetUnitId",
    "secondTargetUnitId",
    "firstHpBefore",
    "firstHpAfter",
    "secondHpBefore",
    "secondHpAfter",
    "appliedCount",
    "skippedCount",
    "failedCount",
    "expAwarded",
    "rangeTileCount",
    "legacyTargetCount",
    "aiPolicy",
    "animationPolicy",
    "eventPolicy",
    "savePolicy",
    "restoredOriginalHp",
)


class CheckError(RuntimeError):
    pass


def resolve_symbol(elf: Path, symbol: str) -> tuple[int, int]:
    try:
        return resolve_elf_symbol(elf, symbol)
    except ProbeBindingError as exc:
        raise CheckError(str(exc)) from exc


def route(config: str) -> tuple[list[dict], int]:
    if config not in ("debug", "release"):
        raise CheckError(f"unsupported config {config!r}")
    source = SCENARIO_DIR / f"starter-danger-overlay-modern-{config}.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    frame = data["checkpoints"][1]["frame"]
    frames = [entry for entry in data["frames"] if entry["end"] <= frame]
    return frames, frame


def build_scenario(
    config: str,
    label: str,
    symbols: dict[str, tuple[int, int]],
    include_probe: bool,
) -> tuple[dict, list[tuple[str, int, int]]]:
    frames, frame = route(config)
    reads = []
    if include_probe:
        probe_base, probe_size = symbols[PROBE_SYMBOL]
        expected_size = 4 * len(PROBE_FIELDS)
        if probe_size != expected_size:
            raise CheckError(
                f"{PROBE_SYMBOL} is {probe_size} bytes, expected {expected_size}; "
                "the C probe and runtime field schema differ"
            )
        reads.extend(
            (name, probe_base + 4 * index, 4)
            for index, name in enumerate(PROBE_FIELDS)
        )
    play_base, _ = symbols["gPlaySt"]
    map_base, _ = symbols["gBmMapSize"]
    reads.extend(
        (
            ("chapterIndex", play_base + 0x0E, 1),
            ("faction", play_base + 0x0F, 1),
            ("chapterTurnNumber", play_base + 0x10, 2),
            ("mapWidth", map_base, 2),
            ("mapHeight", map_base + 2, 2),
        )
    )
    reads.sort(key=lambda entry: (entry[1], entry[2]))
    probes = [
        {"address": f"0x{address:08x}", "size": size}
        for _, address, size in reads
    ]
    scenario = {
        "schema_version": 1,
        "name": f"aoe-{label}-modern-{config}",
        "description": (
            "Issue #42 booted semantic probe. Reuses an established real-map "
            "route, resolves every diagnostic address from this ELF, and reads "
            "only bounded scalar state."
        ),
        "frames": frames,
        "checkpoints": [
            {
                "name": f"aoe-{label}-live-map",
                "frame": frame,
                "framebuffer": False,
                "probes": probes,
            }
        ],
    }
    return scenario, reads


def capture(
    rom: Path,
    elf: Path,
    config: str,
    label: str,
    out_dir: Path,
    include_probe: bool,
) -> dict[str, int]:
    symbols = {
        name: resolve_symbol(elf, name)
        for name in (
            *((PROBE_SYMBOL,) if include_probe else ()),
            "gPlaySt",
            "gBmMapSize",
        )
    }
    scenario, reads = build_scenario(config, label, symbols, include_probe)
    scenario_path = out_dir / f"aoe-{label}-modern-{config}.json"
    capture_path = out_dir / f"aoe-{label}-modern-{config}.captured.json"
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
    if completed.returncode != 0:
        raise CheckError(
            f"gba-playtest {label} capture failed:\n"
            f"{completed.stdout}{completed.stderr}"
        )
    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    probes = captured["checkpoints"][0]["probes"]
    if len(probes) != len(reads):
        raise CheckError(
            f"{label} captured {len(probes)} values, expected {len(reads)}"
        )
    return {
        name: int(probe["value"], 16)
        for (name, _, _), probe in zip(reads, probes)
    }


def check_live_map(values: dict[str, int], label: str) -> list[str]:
    failures = []
    if values["faction"] != 0:
        failures.append(f"{label}: faction={values['faction']}, expected blue 0")
    if values["chapterTurnNumber"] < 1:
        failures.append(f"{label}: chapter turn never reached 1")
    if values["mapWidth"] == 0 or values["mapHeight"] == 0:
        failures.append(
            f"{label}: map size is {values['mapWidth']}x{values['mapHeight']}"
        )
    return failures


def check_enabled(values: dict[str, int]) -> list[str]:
    failures = check_live_map(values, "enabled")
    exact = {
        "magic": PROBE_MAGIC,
        "enabled": 1,
        "runCount": 1,
        "buildResult": 0,
        "executionOutcome": 0,
        "skippedCount": 0,
        "failedCount": 0,
        "expAwarded": 0,
        "aiPolicy": 0,
        "animationPolicy": 0,
        "eventPolicy": 0,
        "savePolicy": 0,
        "restoredOriginalHp": 1,
    }
    for name, expected in exact.items():
        if values[name] != expected:
            failures.append(
                f"enabled: {name}=0x{values[name]:X}, expected 0x{expected:X}"
            )
    if values["sourceUnitId"] == 0:
        failures.append("enabled: sourceUnitId is zero")
    if not (2 <= values["targetCount"] <= 16):
        failures.append(f"enabled: targetCount={values['targetCount']}, expected 2..16")
    if values["targetCount"] != values["totalTargetCount"]:
        failures.append(
            "enabled: bounded target set was incomplete "
            f"({values['targetCount']}/{values['totalTargetCount']})"
        )
    if values["targetCount"] != values["legacyTargetCount"]:
        failures.append(
            "enabled: legacy target mirror count differs "
            f"({values['legacyTargetCount']} vs {values['targetCount']})"
        )
    if values["appliedCount"] < 2:
        failures.append(f"enabled: appliedCount={values['appliedCount']}, expected >=2")
    if not (1 <= values["rangeTileCount"] <= 13):
        failures.append(
            f"enabled: rangeTileCount={values['rangeTileCount']}, expected 1..13"
        )
    for prefix in ("first", "second"):
        if values[f"{prefix}TargetUnitId"] == 0:
            failures.append(f"enabled: {prefix}TargetUnitId is zero")
        if values[f"{prefix}HpAfter"] != values[f"{prefix}HpBefore"] + 3:
            failures.append(
                f"enabled: {prefix} heal was "
                f"{values[f'{prefix}HpBefore']}->{values[f'{prefix}HpAfter']}, "
                "expected +3"
            )
    return failures


def check_disabled(values: dict[str, int]) -> list[str]:
    return check_live_map(values, "disabled")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enabled-rom", required=True, type=Path)
    parser.add_argument("--enabled-elf", required=True, type=Path)
    parser.add_argument("--disabled-rom", required=True, type=Path)
    parser.add_argument("--disabled-elf", required=True, type=Path)
    parser.add_argument("--config", required=True, choices=("debug", "release"))
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        enabled = capture(
            args.enabled_rom,
            args.enabled_elf,
            args.config,
            "enabled",
            args.out_dir,
            True,
        )
        try:
            resolve_symbol(args.disabled_elf, PROBE_SYMBOL)
        except CheckError as error:
            if "is missing" not in str(error):
                raise
        else:
            raise CheckError(
                f"disabled: {PROBE_SYMBOL} must be absent, not retained as "
                "zero-filled negative-control storage"
            )
        disabled = capture(
            args.disabled_rom,
            args.disabled_elf,
            args.config,
            "disabled",
            args.out_dir,
            False,
        )
        failures = check_enabled(enabled) + check_disabled(disabled)
        if failures:
            raise CheckError("\n".join(failures))
        print(
            "AoE runtime checks passed: "
            f"config={args.config} targets={enabled['targetCount']} "
            f"applied={enabled['appliedCount']} "
            f"map={enabled['mapWidth']}x{enabled['mapHeight']}; "
            "default-disabled probe symbol absent"
        )
        return 0
    except (CheckError, OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
