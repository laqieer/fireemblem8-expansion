#!/usr/bin/env python3
"""Dedicated libmGBA proof for the generated battle-animation package alias."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools" / "gba-playtest"))

from scripts.assets import banim
import gba_playtest

PROBE_FIELDS = (
    "magic",
    "selectionCount",
    "originalIndex",
    "defaultClassId",
    "aliasIndex",
    "modeCount",
    "normalDuration",
    "totalDuration",
    "resourcesReady",
    "battleEntryCount",
    "battleCompleteCount",
    "selectedBattleIndex",
    "runtimeDataConsumeCount",
)


def resolve_probe(elf: Path) -> int:
    output = subprocess.run(
        ["arm-none-eabi-nm", "-S", str(elf)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    match = re.search(
        r"^([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+\S\s+gBanimPackageRuntimeTestProbe$",
        output,
        re.MULTILINE,
    )
    if match is None:
        raise RuntimeError("test-only runtime probe was not linked")
    return int(match.group(1), 16)


def generated_define(path: Path, name: str) -> int:
    match = re.search(
        r"^#define " + re.escape(name) + r" (\d+)$",
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        raise RuntimeError(f"missing generated runtime define {name}")
    return int(match.group(1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    base = resolve_probe(args.elf)
    package = banim.load_package(
        str(REPO_ROOT),
        "assets/banim/lorm_sp1/package.json",
        "assets/banim/lorm_sp1/script.txt",
        {
            "assets/banim/lorm_sp1/package.json",
            "assets/banim/lorm_sp1/script.txt",
            "graphics/banim/banim_lorm_sp1_sheet_0.png",
        },
    )
    generated = REPO_ROOT / "build" / "generated" / "assets" / "banim" / "banim_runtime_test_defs.h"
    alias_index = generated_define(generated, "BANIM_PACKAGE_LORM_SP1_PROOF_INDEX")
    scenario = json.loads(
        (REPO_ROOT / "tools/gba-playtest/scenarios/combat.json").read_text(encoding="utf-8")
    )
    scenario["name"] = "banim-package-runtime"
    scenario["description"] = (
        "Issue #62 test-only build: the real Chapter 4 FIGHT enters the engine's "
        "scripted-animation branch once, selects the generated LORM_SP1_PROOF "
        "alias, then records the existing battle engine's entry and completion "
        "without changing normal mappings."
    )
    probes = [
        {"address": f"0x{base + index * 4:08x}", "size": 4}
        for index in range(len(PROBE_FIELDS))
    ]
    scenario["checkpoints"] = [
        {
            "name": "banim-package-runtime-default-control",
            "frame": 3285,
            "framebuffer": False,
            "probes": probes,
        },
        {
            "name": "banim-package-runtime-complete",
            "frame": 4000,
            "framebuffer": False,
            "probes": probes,
        },
    ]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    scenario_path = args.out_dir / "banim-package-runtime.json"
    scenario_path.write_text(json.dumps(scenario, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    capture = gba_playtest.capture(args.rom, gba_playtest.load_scenario(scenario_path))
    default_values = {
        name: int(probe["value"], 16)
        for name, probe in zip(PROBE_FIELDS, capture["checkpoints"][0]["probes"])
    }
    values = {
        name: int(probe["value"], 16)
        for name, probe in zip(PROBE_FIELDS, capture["checkpoints"][1]["probes"])
    }
    expected_total = sum(package.mode_durations.values())
    failures = []

    def expect(observed: dict[str, int], name: str, expected: int, checkpoint: str) -> None:
        if observed[name] != expected:
            failures.append(
                f"{checkpoint} {name}: expected {expected}, observed {observed[name]}"
            )

    for name in PROBE_FIELDS:
        expect(default_values, name, 0, "default control")

    expect(values, "magic", 0x42505431, "scripted battle")
    expect(values, "selectionCount", 1, "scripted battle")
    expect(values, "defaultClassId", 1, "scripted battle")
    expect(values, "aliasIndex", alias_index, "scripted battle")
    expect(values, "modeCount", len(package.mode_durations), "scripted battle")
    expect(values, "normalDuration", package.mode_durations["normal"], "scripted battle")
    expect(values, "totalDuration", expected_total, "scripted battle")
    expect(values, "resourcesReady", 0x1F, "scripted battle")
    expect(values, "battleEntryCount", 1, "scripted battle")
    expect(values, "battleCompleteCount", 1, "scripted battle")
    expect(values, "selectedBattleIndex", alias_index, "scripted battle")
    expect(values, "runtimeDataConsumeCount", 1, "scripted battle")
    if values["originalIndex"] == values["aliasIndex"]:
        failures.append("originalIndex unexpectedly equals the test-only alias index")
    if failures:
        raise RuntimeError("; ".join(failures))
    print("PASS: LORM_SP1_PROOF alias selected, entered, and completed exactly one real scripted battle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
