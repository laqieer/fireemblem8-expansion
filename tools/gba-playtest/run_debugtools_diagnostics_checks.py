#!/usr/bin/env python3
"""Scalar-only libmGBA checks for issue #127 diagnostics."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GBA_PLAYTEST = REPO_ROOT / "tools/gba-playtest/gba_playtest.py"
SCENARIO_DIR = REPO_ROOT / "tools/gba-playtest/scenarios"
sys.path.insert(0, str(GBA_PLAYTEST.parent))

from probe_bindings import ProbeBindingError, resolve_elf_symbol  # noqa: E402


PROBE_FIELDS = (
    "captureCount",
    "lastSequence",
    "lastResult",
    "lastContext",
    "lastValidMask",
    "lastCursorUnitId",
    "ownerActive",
    "ownerStartCount",
    "restorationCount",
    "forcedTeardownCount",
    "restorationMismatchMask",
    "statePageOpenCount",
    "enginePageOpenCount",
    "titleCaptureCount",
    "emptyUnitCaptureCount",
    "battleRejectCount",
    "runtimeTestComplete",
    "mapUnitCaptureCount",
    "mapRuntimeComplete",
    "prepCaptureCount",
    "prepRuntimeComplete",
    "viewRuntimeComplete",
    "lastLockBaseline",
    "lastLockAfterRestore",
    "postViewMapIdleCount",
)


class CheckError(RuntimeError):
    pass


def symbol(elf: Path, name: str) -> tuple[int, int]:
    try:
        return resolve_elf_symbol(elf, name)
    except ProbeBindingError as exc:
        raise CheckError(str(exc)) from exc


def optional_symbol(elf: Path, name: str) -> tuple[int, int] | None:
    try:
        return resolve_elf_symbol(elf, name)
    except ProbeBindingError:
        return None


def build_scenario(
    spec: dict,
    symbols: dict[str, tuple[int, int]],
    config: str,
) -> tuple[dict, list[tuple[str, int, int]]]:
    base = json.loads(
        (SCENARIO_DIR / spec["base_scenario"]).read_text(encoding="utf-8")
    )
    frames = [
        frame
        for frame in base["frames"]
        if frame["end"] <= spec["base_frame_limit"]
    ] + list(spec["tail_frames"])
    reads: list[tuple[str, int, int]] = []

    debug_probe = symbols.get("gDebugToolsDiagnosticsProbe")
    if debug_probe is not None:
        base_address, size = debug_probe
        expected_size = 4 * len(PROBE_FIELDS)
        if size != expected_size:
            raise CheckError(
                f"gDebugToolsDiagnosticsProbe is {size} bytes, "
                f"expected {expected_size}"
            )
        reads.extend(
            (name, base_address + index * 4, 4)
            for index, name in enumerate(PROBE_FIELDS)
        )

    debugtools_base, debugtools_size = symbols["gDebugToolsProbe"]
    reads.extend(
        (f"debugtoolsWord{index}", debugtools_base + index * 4, 4)
        for index in range(debugtools_size // 4)
    )
    play_base, _ = symbols["gPlaySt"]
    bm_base, _ = symbols["gBmSt"]
    reads.extend(
        (
            ("chapterIndex", play_base + 0x0E, 1),
            ("faction", play_base + 0x0F, 1),
            ("chapterStateBits", play_base + 0x14, 1),
            ("gameLock", bm_base + 0x01, 1),
            ("cursorX", bm_base + 0x14, 2),
            ("cursorY", bm_base + 0x16, 2),
        )
    )
    reads.sort(key=lambda item: (item[1], item[2], item[0]))

    probes = [
        {"address": f"0x{address:08x}", "size": size}
        for _, address, size in reads
    ]
    checkpoints = []
    for name, frame in spec["checkpoint_frames"].items():
        checkpoint = {
            "name": f"diagnostics-{name}",
            "frame": frame,
            "framebuffer": False,
            "probes": probes,
        }
        if config == "debug" and name in ("map_runtime", "interactive"):
            checkpoint["sram_hash"] = True
        checkpoints.append(checkpoint)

    return (
        {
            "schema_version": 1,
            "name": spec["name"],
            "description": spec["description"],
            "frames": frames,
            "checkpoints": checkpoints,
        },
        reads,
    )


def capture(
    rom: Path,
    scenario: dict,
    reads: list[tuple[str, int, int]],
    out_dir: Path,
    sram_image: Path | None,
) -> list[dict[str, int | str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    scenario_path = out_dir / f"{scenario['name']}.json"
    capture_path = out_dir / f"{scenario['name']}.captured.json"
    scenario_path.write_text(
        json.dumps(scenario, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    command = [
        sys.executable,
        str(GBA_PLAYTEST),
        "capture",
        "--rom",
        str(rom),
        "--scenario",
        str(scenario_path),
        "--output",
        str(capture_path),
    ]
    if sram_image is not None:
        command.extend(("--sram-image", str(sram_image)))
    completed = subprocess.run(
        command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=3600
    )
    if completed.returncode != 0:
        raise CheckError(completed.stdout + completed.stderr)

    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    result = []
    for checkpoint in captured["checkpoints"]:
        if len(checkpoint["probes"]) != len(reads):
            raise CheckError(
                f"{checkpoint['name']} captured {len(checkpoint['probes'])} "
                f"probes, expected {len(reads)}"
            )
        values: dict[str, int | str] = {
            name: int(probe["value"], 16)
            for (name, _, _), probe in zip(reads, checkpoint["probes"])
        }
        if "sram_hash" in checkpoint:
            values["sram_hash"] = checkpoint["sram_hash"]
        result.append(values)
    return result


def expect(values: dict[str, int | str], name: str, expected: int, failures: list[str]):
    actual = values.get(name)
    if actual != expected:
        failures.append(f"{name}={actual!r}, expected {expected!r}")


def check_debug(checkpoints: list[dict[str, int | str]]) -> list[str]:
    failures: list[str] = []
    title, map_runtime, interactive = checkpoints

    for name, expected in (
        ("runtimeTestComplete", 1),
        ("titleCaptureCount", 1),
        ("battleRejectCount", 1),
        ("forcedTeardownCount", 1),
        ("restorationCount", 1),
        ("restorationMismatchMask", 0),
        ("ownerActive", 0),
    ):
        expect(title, name, expected, failures)

    for name, expected in (
        ("mapRuntimeComplete", 1),
        ("mapUnitCaptureCount", 1),
        ("emptyUnitCaptureCount", 1),
        ("restorationMismatchMask", 0),
        ("statePageOpenCount", 1),
        ("enginePageOpenCount", 1),
        ("viewRuntimeComplete", 1),
        ("ownerActive", 0),
    ):
        expect(map_runtime, name, expected, failures)

    if int(map_runtime["captureCount"]) != 6:
        failures.append(
            f"runtime capture count is {map_runtime['captureCount']}, expected 6"
        )
    if int(map_runtime["forcedTeardownCount"]) != 4:
        failures.append(
            "explicit title/map/view diagnostics teardown count is not exactly four"
        )
    if int(map_runtime["restorationCount"]) != 4:
        failures.append("all explicit diagnostics owner restorations did not total four")
    if interactive.get("sram_hash") != map_runtime.get("sram_hash"):
        failures.append("diagnostics changed whole SRAM")
    if int(interactive["postViewMapIdleCount"]) <= int(map_runtime["postViewMapIdleCount"]):
        failures.append("PlayerPhase_MainIdle did not resume after forced teardown")
    if interactive["gameLock"] != 0:
        failures.append("game lock did not return to zero after forced teardown")
    if interactive["faction"] != 0:
        failures.append("post-teardown map is not in blue phase")
    return failures


def check_release(
    checkpoints: list[dict[str, int | str]],
    probe_present: bool,
    provider_present: bool,
) -> list[str]:
    failures: list[str] = []
    values = checkpoints[0]
    if probe_present:
        failures.append("release ELF retained gDebugToolsDiagnosticsProbe")
    if not provider_present:
        failures.append("release ELF omitted the public disabled provider stub")
    for name, value in values.items():
        if name.startswith("debugtoolsWord") and value != 0:
            failures.append(f"release {name} is nonzero: {value}")
    if values["chapterIndex"] != 0x10 or values["faction"] != 0x40:
        failures.append(
            "release route did not sustain semantic world-map progression"
        )
    return failures


def check_prep(checkpoints: list[dict[str, int | str]]) -> list[str]:
    failures: list[str] = []
    values = checkpoints[0]
    for name, expected in (
        ("prepRuntimeComplete", 1),
        ("prepCaptureCount", 1),
        ("ownerActive", 0),
        ("restorationMismatchMask", 0),
    ):
        expect(values, name, expected, failures)
    if int(values["chapterStateBits"]) & 0x10 == 0:
        failures.append("live prep capture lost PLAY_FLAG_PREPSCREEN")
    if int(values["lastValidMask"]) & 0x02 == 0:
        failures.append("live prep capture did not expose map-state validity")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--config", choices=("debug", "release"), required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--sram-image", type=Path)
    args = parser.parse_args()

    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        probe = optional_symbol(args.elf, "gDebugToolsDiagnosticsProbe")
        provider = optional_symbol(args.elf, "DebugTools_CaptureDiagnostics")
        symbols = {
            "gDebugToolsProbe": symbol(args.elf, "gDebugToolsProbe"),
            "gPlaySt": symbol(args.elf, "gPlaySt"),
            "gBmSt": symbol(args.elf, "gBmSt"),
        }
        if probe is not None:
            symbols["gDebugToolsDiagnosticsProbe"] = probe
        scenario, reads = build_scenario(spec, symbols, args.config)
        checkpoints = capture(
            args.rom,
            scenario,
            reads,
            args.out_dir,
            args.sram_image,
        )
        if spec.get("kind") == "prep":
            failures = check_prep(checkpoints)
        elif args.config == "debug":
            failures = check_debug(checkpoints)
        else:
            failures = check_release(
                checkpoints, probe is not None, provider is not None
            )
    except (CheckError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1

    print(f"debugtools diagnostics runtime check passed ({args.config})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
