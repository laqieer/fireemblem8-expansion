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
from probe_bindings import (  # noqa: E402
    ElfSymbolResolver,
    ProbeBindingError,
    resolve_elf_symbol_address,
)


BENCHMARK_FORMAT_VERSION = 1
DEFAULT_SAMPLES = 3
CONFIG_APPLY_FRAME = 16500
UNIT_SIZE = 0x48
UNIT_GAMEPLAY_PROBE_LAYOUT = (
    (0x08, 2),  # level, EXP
    (0x0A, 2),  # AI flags, slot index
    (0x0C, 4),  # state
    (0x10, 2),  # map position
    (0x12, 2), (0x14, 2),  # HP, strength, skill
    (0x16, 2), (0x18, 2),  # speed, defense, resistance, luck
    (0x1A, 2), (0x1C, 2),  # constitution, rescue, ballista, movement bonuses
    (0x1E, 2),
    (0x20, 2),
    (0x22, 2),
    (0x24, 2),
    (0x26, 2),  # five inventory slots
    (0x28, 4),
    (0x2C, 4),  # weapon ranks
    (0x30, 2),  # status/torch/barrier durations
    (0x32, 2), (0x34, 2), (0x36, 2), (0x38, 2),  # supports and support bits
    (0x40, 2),  # AI configuration
    (0x42, 2), (0x44, 2),
    (0x46, 1),  # AI scripts and counter
)
ACTIVE_BLUE_SLOTS = range(1, 7)
ACTIVE_RED_SLOTS = range(1, 6)
ACTIVE_GREEN_SLOTS = range(1, 3)
# These engine globals are externally declared arrays/counters but their
# original producer does not emit ELF sizes. Keep the ABI spans explicit so
# the generated profile can bind them to the exact linked image without
# relaxing normal symbol-bound probe validation.
EVENT_AND_FLAG_SYMBOL_SPANS = {
    "gEventSlots": 0x38,
    "gEventSlotCounter": 4,
    "gChapterFlagBits": 5,
    "gPermanentFlagBits": 0x19,
}
POLICY_PROBE_SYMBOL = "gBanimPresentationPolicyHarnessProbe"
EVENT_TRACE_SYMBOL = "gExpansionAutoplayEventTrace"
EVENT_TRACE_CAPACITY = 64
EVENT_TRACE_ENTRY_WORDS = 5
EVENT_TRACE_ENTRY_OFFSET = 8
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
    for array_name, slots in (
        ("gUnitArrayBlue", ACTIVE_BLUE_SLOTS),
        ("gUnitArrayRed", ACTIVE_RED_SLOTS),
        ("gUnitArrayGreen", ACTIVE_GREEN_SLOTS),
    ):
        for slot in slots:
            base = (slot - 1) * UNIT_SIZE
            for offset, size in UNIT_GAMEPLAY_PROBE_LAYOUT:
                probes.append(
                    {
                        "address": f"{array_name}+0x{base + offset:03x}",
                        "size": size,
                    }
                )
    return probes


def _event_and_flag_probes() -> list[dict[str, object]]:
    return [
        # Event slot C and the event-script counter provide ordered engine
        # telemetry without introducing a second event router.
        {"address": "gEventSlots+0x30", "size": 4},
        {"address": "gEventSlotCounter", "size": 4},
        # Chapter flags contain EVFLAG_WIN/EVFLAG_DEFEAT_ALL. Permanent
        # flag byte zero contains EVFLAG_GAMEOVER (flag 101) as the explicit
        # loss result.
        {"address": "gChapterFlagBits", "size": 4},
        {"address": "gPermanentFlagBits", "size": 1},
    ]


def _event_transition_probes() -> list[dict[str, object]]:
    probes = [
        {"address": EVENT_TRACE_SYMBOL, "size": 4},
        {"address": f"{EVENT_TRACE_SYMBOL}+0x004", "size": 4},
    ]
    for index in range(EVENT_TRACE_CAPACITY):
        offset = EVENT_TRACE_ENTRY_OFFSET + index * EVENT_TRACE_ENTRY_WORDS * 4
        probes.extend(
            {
                "address": f"{EVENT_TRACE_SYMBOL}+0x{offset + word * 4:03x}",
                "size": 4,
            }
            for word in range(EVENT_TRACE_ENTRY_WORDS)
        )
    return probes


def _policy_runtime_probes() -> list[dict[str, object]]:
    return [
        {"address": POLICY_PROBE_SYMBOL, "size": 4},
        {"address": f"{POLICY_PROBE_SYMBOL}+0x004", "size": 4},
    ]


def _bind_event_and_flag_probe(
    probe: dict[str, object],
    resolve_address,
) -> dict[str, object]:
    binding = probe["address"]
    if not isinstance(binding, str):
        raise ValueError("event and flag probe address must be a string")
    size = probe["size"]
    if not isinstance(size, int):
        raise ValueError("event and flag probe size must be an integer")
    for symbol, span in EVENT_AND_FLAG_SYMBOL_SPANS.items():
        if binding == symbol:
            offset = 0
        elif binding.startswith(f"{symbol}+"):
            offset = int(binding[len(symbol) + 1 :], 0)
        else:
            continue
        if offset < 0 or offset + size > span:
            raise ValueError(
                f"{binding} size {size} is outside the documented {symbol} "
                f"span 0x{span:x}"
            )
        return {
            **probe,
            "address": f"0x{resolve_address(symbol) + offset:08x}",
        }
    return probe


def _bind_event_and_flag_probes(data: dict[str, object], elf: Path) -> dict[str, object]:
    bound = copy.deepcopy(data)
    addresses: dict[str, int] = {}

    def resolve_address(symbol: str) -> int:
        if symbol not in addresses:
            try:
                addresses[symbol] = resolve_elf_symbol_address(elf, symbol)
            except ProbeBindingError as exc:
                raise gba_playtest.PlaytestError(str(exc)) from exc
        return addresses[symbol]

    for probes in (
        bound["run_until"]["checkpoint"]["probes"],
        bound["execution_profile"]["trace"],
    ):
        for index, probe in enumerate(probes):
            probes[index] = _bind_event_and_flag_probe(probe, resolve_address)
    return bound


def _endpoint_probes() -> list[dict[str, object]]:
    return [
        *autoplay._probes(),
        {"address": "gPlaySt+0x14", "size": 1},
        *_rng_probes(),
        *_unit_probes(),
        *_event_and_flag_probes(),
        *_event_transition_probes(),
        *_policy_runtime_probes(),
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
    bound_data = _bind_event_and_flag_probes(data, elf)
    scenario = gba_playtest.parse_scenario_data(
        bound_data,
        source=str(bound_data["name"]),
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
        "checkpoint_probes": [
            probe
            for probe in checkpoint["probes"]
            if not probe["address"].startswith(POLICY_PROBE_SYMBOL)
        ],
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


def compare_profile_samples(expected: dict, actual: dict) -> list[str]:
    """Require every repeated profile capture to be a complete format-4 match."""
    return gba_playtest.compare_fingerprints(expected, actual, policy="exact-rom")


def _probe_values(capture: dict) -> dict[str, int]:
    return {
        probe["address"]: int(probe["value"], 16)
        for probe in capture["checkpoints"][0]["probes"]
    }


def _event_trace_failures(capture: dict) -> list[str]:
    values = _probe_values(capture)
    count = values.get(EVENT_TRACE_SYMBOL)
    overflow = values.get(f"{EVENT_TRACE_SYMBOL}+0x004")
    if count is None or overflow is None:
        return ["event transition telemetry was not captured"]
    if count == 0:
        return ["event transition telemetry captured no command commits"]
    if count > EVENT_TRACE_CAPACITY:
        return [f"event transition count={count} exceeds bounded capacity"]
    if overflow != 0:
        return ["event transition telemetry overflowed"]
    return []


def _check_capture(capture: dict, profile_name: str) -> list[str]:
    failures = autoplay_bounds._check_positive(capture)
    failures += _event_trace_failures(capture)
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
        values = _probe_values(capture)
        if values.get(POLICY_PROBE_SYMBOL) != 3:
            failures.append(
                "accelerated-fidelity: BanimPresentationPolicy_GetCurrent() "
                "did not observe BANIM_PRESENTATION_POLICY_OFF"
            )
        if values.get(f"{POLICY_PROBE_SYMBOL}+0x004", 0) == 0:
            failures.append(
                "accelerated-fidelity: BanimPresentationPolicy_GetCurrent() "
                "was never observed"
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
                differences = compare_profile_samples(baseline, current_baseline)
                differences += compare_profile_samples(accelerated, current_accelerated)
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
                    "baseline_emulated_frames": current_baseline["terminal"]["frame"]
                    + 1,
                    "accelerated_emulated_frames": current_accelerated["terminal"][
                        "frame"
                    ]
                    + 1,
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
