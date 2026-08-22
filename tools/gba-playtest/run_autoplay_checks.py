#!/usr/bin/env python3
"""Deterministic runtime checks for issue #85 blue-phase computer control."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
SCENARIO_DIR = PLAYTEST_DIR / "scenarios"
FINGERPRINT_DIR = PLAYTEST_DIR / "fingerprints"
sys.path.insert(0, str(PLAYTEST_DIR))

import gba_playtest  # noqa: E402
from probe_bindings import ElfSymbolResolver  # noqa: E402

TELEMETRY_SYMBOL = "gExpansionAutoplayTelemetry"
TELEMETRY_FIELDS = (
    "controller",
    "state",
    "failure",
    "bluePhaseStartCount",
    "bluePhaseCompleteCount",
    "eligibleActorCount",
    "committedActionCount",
    "lastActorSlot",
    "lastActionId",
    "lastTargetSlot",
    "lastTargetRelation",
    "hostileTargetCheckCount",
    "alliedTargetCheckCount",
    "invalidRecordCount",
    "debugActivationCount",
    "suspendWriteSuppressedCount",
)
TELEMETRY_SIZE = 4 * len(TELEMETRY_FIELDS)
PLAYER_PHASE_STATE = 1
COMPUTER_PHASE_COMPLETE_STATE = 3
NO_FAILURE = 0
SUPPORTED_ACTIONS = frozenset((0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13))


class CheckError(RuntimeError):
    pass


def _load_route(name: str, through_frame: int | None = None) -> list[dict]:
    path = SCENARIO_DIR / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    frames = data["frames"]
    if through_frame is not None:
        frames = [entry for entry in frames if entry["end"] <= through_frame]
    return frames


def _probes() -> list[dict]:
    probes = [
        {"address": TELEMETRY_SYMBOL, "size": 4},
        *[
            {"address": f"{TELEMETRY_SYMBOL}+0x{index * 4:02x}", "size": 4}
            for index in range(1, len(TELEMETRY_FIELDS))
        ],
        {"address": "gPlaySt+0x0e", "size": 1},
        {"address": "gPlaySt+0x0f", "size": 1},
        {"address": "gPlaySt+0x10", "size": 2},
    ]
    return probes


def _positive_data() -> dict:
    return {
        "schema_version": 1,
        "name": "autoplay-computer-modern-debug",
        "description": (
            "TC-AUTOPLAY-001 positive: clean-boot Chapter 2 reaches an ordinary "
            "interactive player phase, invokes the documented debug activation "
            "chord, and then lets the normal red, green, and blue computer phase "
            "pipeline record bounded semantic telemetry without selecting any "
            "player-unit action."
        ),
        "frames": [
            *_load_route("debugtools-map-hub-modern-debug", 14164),
            {
                "start": 14200,
                "end": 14206,
                "keys": ["SELECT", "START", "R"],
            },
            *[
                {"start": frame, "end": frame + 4, "keys": ["A"]}
                for frame in range(15000, 16500, 60)
            ],
        ],
        "checkpoints": [
            {
                "name": "player-default-before-activation",
                "frame": 14196,
                "framebuffer": False,
                "probes": _probes(),
            },
            {
                "name": "blue-computer-phase-progressed",
                "frame": 18000,
                "framebuffer": False,
                "probes": _probes(),
            },
        ],
    }


def _negative_data(config: str) -> dict:
    route_name = f"starter-danger-overlay-negative-modern-{config}"
    route_path = SCENARIO_DIR / f"{route_name}.json"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    frame = max(checkpoint["frame"] for checkpoint in route["checkpoints"])
    return {
        "schema_version": 1,
        "name": f"autoplay-player-default-modern-{config}",
        "description": (
            "TC-AUTOPLAY-001 negative: the established clean-boot Prologue "
            "route remains in the ordinary interactive player phase with no "
            "debug activation and zero blue computer-phase actions."
        ),
        "frames": route["frames"],
        "checkpoints": [
            {
                "name": "default-player-idle",
                "frame": frame,
                "framebuffer": False,
                "probes": _probes(),
            }
        ],
    }


def _capture(rom: Path, elf: Path, data: dict) -> dict:
    resolver = ElfSymbolResolver(elf)
    scenario = gba_playtest.parse_scenario_data(
        data, source=data["name"], symbol_resolver=resolver
    )
    return gba_playtest.capture(rom, scenario)


def _values(checkpoint: dict) -> dict[str, int]:
    values = {
        probe["address"]: int(probe["value"], 16)
        for probe in checkpoint["probes"]
    }
    result = {
        name: values[
            TELEMETRY_SYMBOL if index == 0
            else f"{TELEMETRY_SYMBOL}+0x{index * 4:02x}"
        ]
        for index, name in enumerate(TELEMETRY_FIELDS)
    }
    result["chapterIndex"] = values["gPlaySt+0x0e"]
    result["faction"] = values["gPlaySt+0x0f"]
    result["chapterTurnNumber"] = values["gPlaySt+0x10"]
    return result


def _check_default(capture: dict, config: str) -> list[str]:
    values = _values(capture["checkpoints"][-1])
    failures = []
    expected = {
        "controller": 0,
        "state": PLAYER_PHASE_STATE,
        "failure": NO_FAILURE,
        "bluePhaseStartCount": 0,
        "bluePhaseCompleteCount": 0,
        "committedActionCount": 0,
        "debugActivationCount": 0,
        "faction": 0,
    }
    for name, value in expected.items():
        if values[name] != value:
            failures.append(
                f"{config} default: {name}=0x{values[name]:X}, expected 0x{value:X}"
            )
    return failures


def _check_positive(capture: dict) -> list[str]:
    before = _values(capture["checkpoints"][0])
    after = _values(capture["checkpoints"][-1])
    failures = _check_default(
        {"checkpoints": [capture["checkpoints"][0]]}, "debug pre-activation"
    )
    if after["controller"] != 1:
        failures.append(f"positive: controller={after['controller']}, expected COMPUTER")
    if after["debugActivationCount"] != 1:
        failures.append(
            "positive: debug activation count is "
            f"{after['debugActivationCount']}, expected 1"
        )
    if after["state"] != COMPUTER_PHASE_COMPLETE_STATE:
        failures.append(
            f"positive: state={after['state']}, expected completed computer phase"
        )
    if after["failure"] != NO_FAILURE or after["invalidRecordCount"] != 0:
        failures.append(
            "positive: failure telemetry is nonzero "
            f"({after['failure']}/{after['invalidRecordCount']})"
        )
    if after["bluePhaseStartCount"] < 1 or after["bluePhaseCompleteCount"] < 1:
        failures.append("positive: blue computer phase did not start and complete")
    if after["bluePhaseCompleteCount"] > after["bluePhaseStartCount"]:
        failures.append("positive: completion count exceeds start count")
    if not (1 <= after["eligibleActorCount"] <= 62):
        failures.append(
            f"positive: eligibleActorCount={after['eligibleActorCount']}, expected 1..62"
        )
    if after["committedActionCount"] < 1:
        failures.append("positive: no legal blue action was committed")
    if not (1 <= after["lastActorSlot"] <= 0x3E):
        failures.append(
            f"positive: last actor slot {after['lastActorSlot']} is not blue"
        )
    if after["lastActionId"] not in SUPPORTED_ACTIONS:
        failures.append(
            f"positive: last action {after['lastActionId']} is unsupported"
        )
    if after["lastTargetRelation"] not in (0, 1, 2):
        failures.append(
            f"positive: invalid target relation {after['lastTargetRelation']}"
        )
    if after["hostileTargetCheckCount"] < 1:
        failures.append("positive: existing AI never classified a red target as hostile")
    if after["alliedTargetCheckCount"] < 1:
        failures.append("positive: existing AI never classified a green target as allied")
    if after["suspendWriteSuppressedCount"] < 1:
        failures.append("positive: transient blue-AI suspend suppression was not exercised")
    if after["chapterTurnNumber"] <= before["chapterTurnNumber"]:
        failures.append(
            "positive: chapter turn did not progress "
            f"({before['chapterTurnNumber']}->{after['chapterTurnNumber']})"
        )
    return failures


def _fingerprint_path(name: str) -> Path:
    return FINGERPRINT_DIR / f"{name}.json"


def _verify_or_capture(
    capture: dict, name: str, capture_fingerprints: bool
) -> list[str]:
    path = _fingerprint_path(name)
    if capture_fingerprints:
        baseline = dict(capture)
        baseline.pop("rom", None)
        path.write_text(gba_playtest.serialize_fingerprint(baseline), encoding="utf-8")
        return []
    if not path.is_file():
        return [f"missing checked fingerprint: {path}"]
    expected = gba_playtest.validate_fingerprint(
        json.loads(path.read_text(encoding="utf-8")),
        str(path),
        policy="behavior",
    )
    return gba_playtest.compare_fingerprints(expected, capture, policy="behavior")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--config", required=True, choices=("debug", "release"))
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--capture-fingerprints", action="store_true")
    args = parser.parse_args(argv)

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("TMPDIR", str(args.out_dir / "tmp"))
        Path(os.environ["TMPDIR"]).mkdir(parents=True, exist_ok=True)

        cases = [(_negative_data(args.config), _check_default)]
        if args.config == "debug":
            cases.insert(0, (_positive_data(), _check_positive))

        failures = []
        summaries = []
        for data, semantic_check in cases:
            captured = _capture(args.rom, args.elf, data)
            capture_path = args.out_dir / f"{data['name']}.captured.json"
            capture_path.write_text(
                gba_playtest.serialize_fingerprint(captured), encoding="utf-8"
            )
            if semantic_check is _check_default:
                failures.extend(semantic_check(captured, args.config))
            else:
                failures.extend(semantic_check(captured))
            failures.extend(
                _verify_or_capture(
                    captured, data["name"], args.capture_fingerprints
                )
            )
            values = _values(captured["checkpoints"][-1])
            summaries.append(
                f"{data['name']}: controller={values['controller']} "
                f"starts={values['bluePhaseStartCount']} "
                f"completions={values['bluePhaseCompleteCount']} "
                f"actions={values['committedActionCount']} "
                f"turn={values['chapterTurnNumber']}"
            )

        if failures:
            raise CheckError("\n".join(failures))
        print("Autoplay runtime checks passed: " + "; ".join(summaries))
        return 0
    except (CheckError, OSError, ValueError, KeyError, gba_playtest.PlaytestError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
