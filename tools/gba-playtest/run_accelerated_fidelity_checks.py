#!/usr/bin/env python3
"""Paired accelerated-fidelity evidence for issue #88."""

from __future__ import annotations

import argparse
import copy
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
sys.path.insert(0, str(PLAYTEST_DIR))

import gba_playtest  # noqa: E402
import run_autoplay_bounds_checks as autoplay_bounds  # noqa: E402
import run_autoplay_checks as autoplay  # noqa: E402
from probe_bindings import ElfSymbolResolver  # noqa: E402


BENCHMARK_FORMAT_VERSION = 1
DEFAULT_SAMPLES = 3
CONFIG_APPLY_FRAME = 16500
UNIT_SIZE = 0x48
ACTIVE_BLUE_SLOTS = range(1, 7)
FROZEN_BASELINE_FRAME_COUNT = 17135
FROZEN_ACCELERATED_FRAME_COUNT = 16869


class CheckError(RuntimeError):
    pass


def _rng_probes() -> list[dict[str, object]]:
    return [
        {"address": f"gRNSeeds+0x{offset:02x}", "size": 2}
        for offset in (0, 2, 4)
    ]


def _unit_probes() -> list[dict[str, object]]:
    probes: list[dict[str, object]] = []
    for slot in ACTIVE_BLUE_SLOTS:
        base = slot * UNIT_SIZE
        for offset, size in (
            (0x0C, 4),  # state
            (0x10, 2),  # map position
            (0x12, 2),  # maximum/current HP
            (0x1E, 2),
            (0x20, 2),
            (0x22, 2),
            (0x24, 2),
            (0x26, 2),  # five inventory slots
        ):
            probes.append(
                {
                    "address": f"gUnitArrayBlue+0x{base + offset:03x}",
                    "size": size,
                }
            )
    return probes


def _endpoint_probes() -> list[dict[str, object]]:
    return [
        *autoplay._probes(),
        {"address": "gPlaySt+0x14", "size": 1},
        *_rng_probes(),
        *_unit_probes(),
    ]


def _trace_probes() -> list[dict[str, object]]:
    return [
        *autoplay._probes()[: len(autoplay.TELEMETRY_FIELDS)],
        *_rng_probes(),
    ]


def profile_data(name: str) -> dict[str, object]:
    if name not in gba_playtest.EXECUTION_PROFILE_NAMES:
        raise ValueError(f"unsupported execution profile {name!r}")
    run_until = copy.deepcopy(autoplay_bounds._run_until(18001))
    run_until["checkpoint"]["probes"] = _endpoint_probes()
    profile: dict[str, object] = {
        "name": name,
        "trace": _trace_probes(),
    }
    if name == gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY:
        profile.update(
            config_apply_frame=CONFIG_APPLY_FRAME,
            play_state_config={"address": "gPlaySt+0x40", "size": 4},
        )
    return {
        "schema_version": gba_playtest.ACCELERATED_FIDELITY_SCENARIO_SCHEMA_VERSION,
        "name": f"autoplay-{name}-computer-modern-debug",
        "description": (
            "TC-AUTOPLAY-ACCEL-001 paired clean-boot Chapter 2 COMPUTER "
            "fixture. Every input and emulated frame remains the ordinary "
            "production route; accelerated fidelity changes only the existing "
            "game-speed and battle-animation-off preferences in the isolated "
            "emulator core."
        ),
        "frames": autoplay._positive_data()["frames"],
        "run_until": run_until,
        "execution_profile": profile,
    }


def _capture(
    rom: Path,
    elf: Path,
    data: dict[str, object],
    *,
    backend_path: Path,
) -> dict:
    scenario = gba_playtest.parse_scenario_data(
        data,
        source=str(data["name"]),
        symbol_resolver=ElfSymbolResolver(elf),
    )
    return gba_playtest.capture(rom, scenario, backend_path=backend_path)


def _terminal_semantics(capture: dict) -> dict:
    terminal = capture["terminal"]
    return {
        "reason": terminal["reason"],
        "turn": terminal["turn"],
        "actions": terminal["actions"],
    }


def _semantic_record(capture: dict) -> dict:
    checkpoint = capture["checkpoints"][0]
    return {
        # Semantic equivalence is meaningful only for paired executions of
        # the same immutable ROM image, even though it intentionally ignores
        # each profile's different emulated-frame timestamps.
        "rom": capture["rom"],
        "terminal": _terminal_semantics(capture),
        "checkpoint_probes": checkpoint["probes"],
        # Frame timing is intentionally not part of equivalence: the exact
        # ordered telemetry/RNG values must match while faster existing game
        # presentation reaches them on earlier emulated frames.
        "trace": [snapshot["probes"] for snapshot in capture["trace"]],
    }


def compare_semantics(baseline: dict, accelerated: dict) -> list[str]:
    return list(
        gba_playtest._recursive_differences(
            _semantic_record(baseline),
            _semantic_record(accelerated),
        )
    )


def _check_capture(capture: dict, profile_name: str) -> list[str]:
    failures = autoplay_bounds._check_positive(capture)
    if capture["profile"]["name"] != profile_name:
        failures.append(
            f"{profile_name}: capture profile was {capture['profile']['name']!r}"
        )
    if "framebuffer_hash" in capture["checkpoints"][0]:
        failures.append(f"{profile_name}: semantic checkpoint retained framebuffer hash")
    if profile_name == gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY:
        profile = capture["profile"]
        if profile["config_apply_frame"] != CONFIG_APPLY_FRAME:
            failures.append(
                f"accelerated-fidelity: config applied at "
                f"{profile['config_apply_frame']}, expected {CONFIG_APPLY_FRAME}"
            )
        after = int(profile["config_after"], 16)
        if not after & gba_playtest.PLAYST_CONFIG_GAME_SPEED_MASK:
            failures.append("accelerated-fidelity: existing game-speed option was not enabled")
        if (
            after & gba_playtest.PLAYST_CONFIG_ANIMATION_TYPE_MASK
        ) != gba_playtest.PLAYST_CONFIG_ANIMATION_TYPE_OFF:
            failures.append(
                "accelerated-fidelity: existing BANIM_PRESENTATION_POLICY_OFF "
                "animation option was not selected"
            )
    return failures


def _require_frozen_frame_counts(baseline: dict, accelerated: dict) -> list[str]:
    baseline_frames = baseline["terminal"]["frame"] + 1
    accelerated_frames = accelerated["terminal"]["frame"] + 1
    failures = []
    if baseline_frames != FROZEN_BASELINE_FRAME_COUNT:
        failures.append(
            f"baseline emulated frames={baseline_frames}, expected frozen "
            f"{FROZEN_BASELINE_FRAME_COUNT}"
        )
    if accelerated_frames != FROZEN_ACCELERATED_FRAME_COUNT:
        failures.append(
            f"accelerated emulated frames={accelerated_frames}, expected frozen "
            f"{FROZEN_ACCELERATED_FRAME_COUNT}"
        )
    if accelerated_frames >= baseline_frames:
        failures.append(
            f"accelerated emulated frames={accelerated_frames}, expected fewer "
            f"than baseline {baseline_frames}"
        )
    return failures


def _perturbed_trace_is_rejected(baseline: dict, accelerated: dict) -> bool:
    perturbed = copy.deepcopy(accelerated)
    final_probe = perturbed["trace"][-1]["probes"][-1]
    value = int(final_probe["value"], 16)
    final_probe["value"] = f"0x{value ^ 1:0{final_probe['size'] * 2}x}"
    return bool(compare_semantics(baseline, perturbed))


def _command_output(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"
    if completed.returncode:
        return f"unavailable: {completed.stderr.strip() or completed.stdout.strip()}"
    return completed.stdout.strip()


def _libmgba_version() -> str:
    package_config = _command_output(["pkg-config", "--modversion", "mgba"])
    if not package_config.startswith("unavailable:"):
        return package_config
    cli_version = _command_output(["mgba", "--version"])
    if not cli_version.startswith("unavailable:"):
        return cli_version
    package_version = _command_output(
        ["dpkg-query", "-W", "-f=${Package} ${Version}", "libmgba-dev"]
    )
    if not package_version.startswith("unavailable:"):
        return package_version
    return package_config


def _benchmark_metadata() -> dict:
    return {
        "format_version": BENCHMARK_FORMAT_VERSION,
        "host": {
            "machine": platform.machine(),
            "platform": platform.platform(aliased=True),
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        },
        "libmgba_version": _libmgba_version(),
        "repository_commit": _command_output(["git", "rev-parse", "HEAD"]),
        "runner": {
            "path": str(Path(__file__).relative_to(REPO_ROOT)),
            "python_executable": sys.executable,
        },
    }


def _write_benchmark(
    path: Path,
    configuration: str,
    rom_commit: str,
    baseline: dict,
    accelerated: dict,
    samples: list[dict],
) -> None:
    path.write_text(
        json.dumps(
            {
                **_benchmark_metadata(),
                "configuration": configuration,
                "rom_commit": rom_commit,
                "rom": baseline["rom"],
                "same_rom_provenance": baseline["rom"] == accelerated["rom"],
                "scenario": {
                    "baseline": baseline["scenario"],
                    "accelerated": accelerated["scenario"],
                },
                "emulated_frames": {
                    "baseline": baseline["terminal"]["frame"] + 1,
                    "accelerated": accelerated["terminal"]["frame"] + 1,
                    "reduction": baseline["terminal"]["frame"]
                    - accelerated["terminal"]["frame"],
                },
                "frozen_target": {
                    "baseline": FROZEN_BASELINE_FRAME_COUNT,
                    "accelerated": FROZEN_ACCELERATED_FRAME_COUNT,
                },
                "wall_clock_samples_seconds": samples,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--rom-commit",
        required=True,
        help="exact source commit embedded by the modern ROM build",
    )
    parser.add_argument(
        "--configuration",
        required=True,
        help="named ROM configuration and ABI, such as modern-debug/aapcs",
    )
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument(
        "--measure-only",
        action="store_true",
        help="report paired measured frame counts before freezing a new target",
    )
    args = parser.parse_args(argv)

    try:
        if args.samples < 1 or args.samples > 20:
            raise CheckError("--samples must be between 1 and 20")
        args.out_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("TMPDIR", str(args.out_dir / "tmp"))
        Path(os.environ["TMPDIR"]).mkdir(parents=True, exist_ok=True)
        backend = args.out_dir / "gba-playtest-accelerated-fidelity-backend"
        gba_playtest.build_backend(backend)
        baseline_data = profile_data(gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY)
        accelerated_data = profile_data(
            gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY
        )
        samples = []
        baseline = None
        accelerated = None
        for index in range(args.samples):
            started = time.perf_counter()
            current_baseline = _capture(
                args.rom,
                args.elf,
                baseline_data,
                backend_path=backend,
            )
            baseline_seconds = time.perf_counter() - started
            started = time.perf_counter()
            current_accelerated = _capture(
                args.rom,
                args.elf,
                accelerated_data,
                backend_path=backend,
            )
            accelerated_seconds = time.perf_counter() - started
            if baseline is None:
                baseline = current_baseline
                accelerated = current_accelerated
            else:
                differences = compare_semantics(baseline, current_baseline)
                differences += compare_semantics(accelerated, current_accelerated)
                if differences:
                    raise CheckError(
                        "benchmark samples were not reproducible:\n"
                        + "\n".join(f"  - {difference}" for difference in differences)
                    )
            samples.append(
                {
                    "sample": index + 1,
                    "baseline": baseline_seconds,
                    "accelerated": accelerated_seconds,
                }
            )
        assert baseline is not None and accelerated is not None
        failures = _check_capture(
            baseline,
            gba_playtest.EXECUTION_PROFILE_NORMAL_FIDELITY,
        )
        failures += _check_capture(
            accelerated,
            gba_playtest.EXECUTION_PROFILE_ACCELERATED_FIDELITY,
        )
        failures += compare_semantics(baseline, accelerated)
        if not _perturbed_trace_is_rejected(baseline, accelerated):
            failures.append("perturbed semantic trace was accepted")
        if not args.measure_only:
            failures += _require_frozen_frame_counts(baseline, accelerated)
        _write_benchmark(
            args.out_dir / "accelerated-fidelity-benchmark.json",
            args.configuration,
            args.rom_commit,
            baseline,
            accelerated,
            samples,
        )
        if failures:
            raise CheckError("\n".join(failures))
    except (CheckError, OSError, ValueError, KeyError, gba_playtest.PlaytestError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        "Accelerated-fidelity checks passed: "
        f"baseline_frames={baseline['terminal']['frame'] + 1} "
        f"accelerated_frames={accelerated['terminal']['frame'] + 1} "
        f"samples={args.samples}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
