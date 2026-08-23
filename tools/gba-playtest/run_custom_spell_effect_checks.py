#!/usr/bin/env python3
"""Run issue #77's isolated custom-spell dispatch/lifecycle probe ROM."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GBA_PLAYTEST = REPO_ROOT / "tools" / "gba-playtest" / "gba_playtest.py"
sys.path.insert(0, str(GBA_PLAYTEST.parent))

from probe_bindings import ProbeBindingError, resolve_elf_symbol  # noqa: E402

PROBE_SYMBOL = "gCustomSpellEffectTestProbe"
PROBE_MAGIC = 0x43535031
ENABLED_CASES = 0x1FF
DISABLED_CASES = 0x002
PROBE_FIELDS = (
    "magic",
    "enabled",
    "completedMask",
    "failureMask",
    "harnessEnded",
    "normalCustomDispatches",
    "normalStarts",
    "normalResourceLoads",
    "normalHits",
    "normalCleanups",
    "normalChildCreates",
    "normalChildDeletes",
    "normalFinalActive",
    "normalFinalSemaphore",
    "normalFinalSpellState",
    "vanillaDispatches",
    "vanillaCustomDispatches",
    "missingCustomDispatches",
    "missingFallbackReason",
    "missingFallbackAnimation",
    "missingResourceLoads",
    "missingFinalActive",
    "invalidCustomDispatches",
    "invalidFallbackReason",
    "invalidFallbackAnimation",
    "invalidResourceLoads",
    "invalidFinalActive",
    "reentrantCustomDispatches",
    "reentrantStarts",
    "reentrantFallbacks",
    "reentrantFallbackReason",
    "reentrantResourceLoads",
    "reentrantCleanups",
    "reentrantFinalActive",
    "reentrantFinalSemaphore",
    "resourceFailureCustomDispatches",
    "resourceFailureStarts",
    "resourceFailureFallbacks",
    "resourceFailureFallbackReason",
    "resourceFailureResourceLoads",
    "resourceFailureCleanups",
    "resourceFailureChildCreates",
    "resourceFailureFinalActive",
    "resourceFailureFinalSemaphore",
    "backgroundsCustomDispatches",
    "backgroundsFallbackReason",
    "backgroundsFallbackAnimation",
    "backgroundsResourceLoads",
    "backgroundsFinalActive",
    "semaphoreCustomDispatches",
    "semaphoreFallbackReason",
    "semaphoreFallbackAnimation",
    "semaphoreResourceLoads",
    "semaphorePreserved",
    "semaphoreFinalActive",
    "forcedCustomDispatches",
    "forcedStarts",
    "forcedCleanups",
    "forcedChildCreates",
    "forcedChildDeletes",
    "forcedFinalActive",
    "forcedFinalSemaphore",
    "finalCustomActive",
    "finalSpellCastActive",
    "finalSemaphore",
    "finalSpellState",
    "animAllocationFailures",
    "procAllocationFailures",
    "allocationFailureCleanups",
)


class CheckError(RuntimeError):
    pass


def parse_enabled(value: str) -> int:
    try:
        parsed = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--enabled must be 0 or 1") from exc
    if parsed not in (0, 1):
        raise argparse.ArgumentTypeError("--enabled must be 0 or 1")
    return parsed


def resolve_probe(elf: Path) -> tuple[int, int]:
    try:
        return resolve_elf_symbol(elf, PROBE_SYMBOL)
    except ProbeBindingError as exc:
        raise CheckError(str(exc)) from exc


def read_symbols(elf: Path) -> str:
    nm = os.environ.get("NM", "arm-none-eabi-nm")
    completed = subprocess.run(
        [nm, "-S", str(elf)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise CheckError(
            f"{nm} failed for {elf}:\n{completed.stdout}{completed.stderr}"
        )
    return completed.stdout


def check_symbol_boundary(symbols: str, enabled: int) -> None:
    public = (
        "CustomSpellEffect_Lookup",
        "CustomSpellEffect_Start",
        "CustomSpellEffect_IsActive",
    )
    for symbol in public:
        found = re.search(rf"\b{re.escape(symbol)}$", symbols, flags=re.MULTILINE)
        if enabled and found is None:
            raise CheckError(f"enabled ROM is missing public symbol {symbol}")
        if not enabled and found is not None:
            raise CheckError(f"disabled ROM unexpectedly links public symbol {symbol}")


def build_scenario(base: int, config: str, enabled: int) -> dict:
    probes = [
        {"address": f"0x{base + 4 * index:08x}", "size": 4}
        for index in range(len(PROBE_FIELDS))
    ]
    return {
        "schema_version": 1,
        "name": f"custom-spell-isolated-{config}-enabled-{enabled}",
        "description": (
            "Issue #77 test-only isolated Anim/spell lifecycle ROM. It bypasses "
            "StartGame and all chapter scripts, invokes the public "
            "StartSpellAnimation dispatch ABI, and exposes only scalar state."
        ),
        "frames": [],
        "checkpoints": [
            {
                "name": "custom-spell-isolated-complete",
                "frame": 900,
                "framebuffer": False,
                "probes": probes,
            }
        ],
    }


def capture(rom: Path, scenario_path: Path, output_path: Path) -> dict:
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
            str(output_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if completed.returncode != 0:
        raise CheckError(
            "gba-playtest custom-spell capture failed "
            f"(exit {completed.returncode}):\n{completed.stdout}{completed.stderr}"
        )
    return json.loads(output_path.read_text(encoding="utf-8"))


def read_values(captured: dict) -> dict[str, int]:
    probes = captured["checkpoints"][0]["probes"]
    if len(probes) != len(PROBE_FIELDS):
        raise CheckError(
            f"captured {len(probes)} probes, expected {len(PROBE_FIELDS)}"
        )
    return {
        name: int(probe["value"], 16)
        for name, probe in zip(PROBE_FIELDS, probes)
    }


def expected_values(enabled: int) -> dict[str, int]:
    expected = {name: 0 for name in PROBE_FIELDS}
    expected.update(
        {
            "magic": PROBE_MAGIC,
            "enabled": enabled,
            "completedMask": ENABLED_CASES if enabled else DISABLED_CASES,
            "harnessEnded": 1,
            "vanillaDispatches": 1,
            "animAllocationFailures": 1,
            "procAllocationFailures": 1,
            "allocationFailureCleanups": 2,
        }
    )
    if not enabled:
        return expected

    expected.update(
        {
            "normalCustomDispatches": 1,
            "normalStarts": 1,
            "normalResourceLoads": 2,
            "normalHits": 1,
            "normalCleanups": 1,
            "normalChildCreates": 1,
            "normalChildDeletes": 1,
            "missingCustomDispatches": 1,
            "missingFallbackReason": 1,
            "missingFallbackAnimation": 22,
            "invalidCustomDispatches": 1,
            "invalidFallbackReason": 1,
            "invalidFallbackAnimation": 22,
            "reentrantCustomDispatches": 2,
            "reentrantStarts": 1,
            "reentrantFallbacks": 1,
            "reentrantFallbackReason": 2,
            "reentrantResourceLoads": 2,
            "reentrantCleanups": 1,
            "resourceFailureCustomDispatches": 1,
            "resourceFailureFallbacks": 1,
            "resourceFailureFallbackReason": 6,
            "resourceFailureResourceLoads": 1,
            "resourceFailureCleanups": 1,
            "backgroundsCustomDispatches": 1,
            "backgroundsFallbackReason": 4,
            "backgroundsFallbackAnimation": 22,
            "semaphoreCustomDispatches": 1,
            "semaphoreFallbackReason": 3,
            "semaphoreFallbackAnimation": 22,
            "semaphorePreserved": 1,
            "forcedCustomDispatches": 1,
            "forcedStarts": 1,
            "forcedCleanups": 1,
            "forcedChildCreates": 1,
            "forcedChildDeletes": 1,
        }
    )
    return expected


def check_values(values: dict[str, int], enabled: int) -> list[str]:
    expected = expected_values(enabled)
    return [
        f"{name}=0x{values[name]:X}, expected 0x{value:X}"
        for name, value in expected.items()
        if values[name] != value
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--config", required=True, choices=("debug", "release"))
    parser.add_argument("--enabled", required=True, type=parse_enabled)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        base, size = resolve_probe(args.elf)
        expected_size = 4 * len(PROBE_FIELDS)
        if size != expected_size:
            raise CheckError(
                f"{PROBE_SYMBOL} is {size} bytes in {args.elf}, expected "
                f"{expected_size}; C probe and runner field schemas differ"
            )
        check_symbol_boundary(read_symbols(args.elf), args.enabled)
        scenario = build_scenario(base, args.config, args.enabled)
        stem = f"custom-spell-isolated-{args.config}-enabled-{args.enabled}"
        scenario_path = args.out_dir / f"{stem}.json"
        capture_path = args.out_dir / f"{stem}.captured.json"
        scenario_path.write_text(
            json.dumps(scenario, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        values = read_values(capture(args.rom, scenario_path, capture_path))
        failures = check_values(values, args.enabled)
        if failures:
            print(
                f"custom spell runtime probe FAILED ({args.config}, "
                f"enabled={args.enabled}):",
                file=sys.stderr,
            )
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1

        print(
            f"custom spell runtime probe passed: config={args.config} "
            f"enabled={args.enabled} rom={args.rom} "
            f"{PROBE_SYMBOL}=0x{base:08x}"
        )
        if args.enabled:
            print(
                "  registered dispatch, vanilla route, missing/invalid fallback, "
                "reentrancy, post-acquire failure cleanup, WITH_BACKGROUNDS, "
                "foreign semaphore preservation, forced end, and final zero "
                "ownership all matched; Anim/Proc allocation failures were "
                "recorded and cleaned without a crash"
            )
        else:
            print(
                "  default-off ROM used the vanilla LUT route and linked no "
                "custom dispatcher/runtime symbols"
            )
        return 0
    except CheckError as exc:
        print(f"run_custom_spell_effect_checks: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
