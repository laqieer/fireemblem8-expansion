#!/usr/bin/env python3
"""Deterministic runtime checks for issue #86 bounded semantic autoplay."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
FINGERPRINT_DIR = PLAYTEST_DIR / "fingerprints"
sys.path.insert(0, str(PLAYTEST_DIR))

import gba_playtest  # noqa: E402
import run_autoplay_checks as autoplay  # noqa: E402
from probe_bindings import ElfSymbolResolver  # noqa: E402

STATE_OFFSET = autoplay.TELEMETRY_FIELDS.index("state") * 4
FAILURE_OFFSET = autoplay.TELEMETRY_FIELDS.index("failure") * 4
COMPLETE_OFFSET = autoplay.TELEMETRY_FIELDS.index("bluePhaseCompleteCount") * 4
ACTION_OFFSET = autoplay.TELEMETRY_FIELDS.index("committedActionCount") * 4
MAX_BLUE_ACTIONS = 62
MAX_CHAPTER_TURNS = 3
MAX_STALL_FRAMES = 1800
COMPUTER_PHASE_STATE = 2
FAILURE_STATE = 4


class CheckError(RuntimeError):
    pass


def _telemetry_address(offset: int) -> str:
    if offset == 0:
        return autoplay.TELEMETRY_SYMBOL
    return f"{autoplay.TELEMETRY_SYMBOL}+0x{offset:02x}"


def _comparison(address: str, size: int, operator: str, value: int) -> dict:
    return {
        "address": address,
        "size": size,
        "operator": operator,
        "value": f"0x{value:0{size * 2}x}",
    }


def _run_until(max_frames: int) -> dict:
    state_address = _telemetry_address(STATE_OFFSET)
    failure_address = _telemetry_address(FAILURE_OFFSET)
    complete_address = _telemetry_address(COMPLETE_OFFSET)
    action_address = _telemetry_address(ACTION_OFFSET)
    return {
        "max_frames": max_frames,
        "terminal_conditions": [
            {
                "reason": "success",
                "all": [
                    _comparison(
                        state_address,
                        4,
                        "eq",
                        autoplay.COMPUTER_PHASE_COMPLETE_STATE,
                    ),
                    _comparison(complete_address, 4, "ge", 1),
                    _comparison(failure_address, 4, "eq", autoplay.NO_FAILURE),
                ],
            },
            {
                "reason": "objective_failure",
                "all": [
                    _comparison(state_address, 4, "eq", FAILURE_STATE),
                ],
            },
        ],
        "stall": {
            "max_unchanged_frames": MAX_STALL_FRAMES,
            "progress": {
                "address": action_address,
                "size": 4,
            },
            "work_expected": _comparison(
                state_address,
                4,
                "eq",
                COMPUTER_PHASE_STATE,
            ),
        },
        "turn_limit": {
            "maximum": MAX_CHAPTER_TURNS,
            "address": "gPlaySt+0x10",
            "size": 2,
        },
        "action_limit": {
            "maximum": MAX_BLUE_ACTIONS,
            "address": action_address,
            "size": 4,
        },
        "checkpoint": {
            "name": "semantic-terminal",
            "framebuffer": False,
            "probes": autoplay._probes(),
        },
    }


def _positive_data() -> dict:
    fixed = autoplay._positive_data()
    return {
        "schema_version": 2,
        "name": "autoplay-bounded-computer-modern-debug",
        "description": (
            "TC-AUTOPLAY-BOUNDS-001 positive: the issue #85 Chapter 2 "
            "COMPUTER route stops at the first completed blue phase, with "
            "semantic stall, turn, action, and frame bounds."
        ),
        "frames": fixed["frames"],
        "run_until": _run_until(18001),
    }


def _negative_data(config: str) -> dict:
    fixed = autoplay._negative_data(config)
    final_frame = fixed["checkpoints"][0]["frame"]
    return {
        "schema_version": 2,
        "name": f"autoplay-bounded-player-default-modern-{config}",
        "description": (
            "TC-AUTOPLAY-BOUNDS-001 default negative: PLAYER control never "
            "matches semantic autoplay success and terminates at max_frames "
            "with zero blue computer actions."
        ),
        "frames": fixed["frames"],
        "run_until": _run_until(final_frame + 1),
    }


def _capture(rom: Path, elf: Path, data: dict) -> dict:
    resolver = ElfSymbolResolver(elf)
    scenario = gba_playtest.parse_scenario_data(
        data,
        source=data["name"],
        symbol_resolver=resolver,
    )
    return gba_playtest.capture(rom, scenario)


def _terminal_counter(capture: dict, name: str) -> int:
    record = capture["terminal"][name]
    if record is None:
        raise CheckError(f"terminal {name} counter is unexpectedly unbound")
    return int(record["value"], 16)


def _check_positive(capture: dict) -> list[str]:
    values = autoplay._values(capture["checkpoints"][0])
    failures = []
    if capture["terminal"]["reason"] != "success":
        failures.append(
            "positive: terminal reason is "
            f"{capture['terminal']['reason']!r}, expected 'success'"
        )
    expected = {
        "controller": 1,
        "state": autoplay.COMPUTER_PHASE_COMPLETE_STATE,
        "failure": autoplay.NO_FAILURE,
        "debugActivationCount": 1,
        "invalidRecordCount": 0,
    }
    for name, value in expected.items():
        if values[name] != value:
            failures.append(
                f"positive: {name}={values[name]}, expected {value}"
            )
    if values["bluePhaseStartCount"] < 1:
        failures.append("positive: blue computer phase never started")
    if values["bluePhaseCompleteCount"] != 1:
        failures.append(
            "positive: first semantic terminal has completion count "
            f"{values['bluePhaseCompleteCount']}, expected 1"
        )
    if not (1 <= values["eligibleActorCount"] <= MAX_BLUE_ACTIONS):
        failures.append(
            "positive: eligible actor count is outside 1..62: "
            f"{values['eligibleActorCount']}"
        )
    if not (1 <= values["committedActionCount"] <= MAX_BLUE_ACTIONS):
        failures.append(
            "positive: committed action count is outside 1..62: "
            f"{values['committedActionCount']}"
        )
    if values["hostileTargetCheckCount"] < 1:
        failures.append("positive: no red-hostile semantic observation")
    if values["alliedTargetCheckCount"] < 1:
        failures.append("positive: no green-allied semantic observation")
    if values["suspendWriteSuppressedCount"] < 1:
        failures.append("positive: blue transient suspend suppression was not used")
    if _terminal_counter(capture, "turn") != values["chapterTurnNumber"]:
        failures.append("positive: terminal turn does not match checkpoint telemetry")
    if _terminal_counter(capture, "actions") != values["committedActionCount"]:
        failures.append(
            "positive: terminal action count does not match checkpoint telemetry"
        )
    if capture["terminal"]["frame"] >= 18000:
        failures.append(
            "positive: semantic success did not stop before the former fixed "
            f"frame 18000 (actual {capture['terminal']['frame']})"
        )
    return failures


def _check_default(capture: dict, config: str) -> list[str]:
    failures = autoplay._check_default(capture, config)
    values = autoplay._values(capture["checkpoints"][0])
    if capture["terminal"]["reason"] != "max_frames":
        failures.append(
            f"{config} default: terminal reason "
            f"{capture['terminal']['reason']!r}, expected 'max_frames'"
        )
    if _terminal_counter(capture, "turn") != values["chapterTurnNumber"]:
        failures.append(f"{config} default: terminal turn mismatch")
    if _terminal_counter(capture, "actions") != 0:
        failures.append(f"{config} default: terminal actions are nonzero")
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
        path.write_text(
            gba_playtest.serialize_fingerprint(baseline),
            encoding="utf-8",
        )
        return []
    if not path.is_file():
        return [f"missing checked fingerprint: {path}"]
    expected = gba_playtest.validate_fingerprint(
        json.loads(path.read_text(encoding="utf-8")),
        str(path),
        policy="behavior",
    )
    return gba_playtest.compare_fingerprints(
        expected,
        capture,
        policy="behavior",
    )


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

        cases = [(_negative_data(args.config), _check_default, (args.config,))]
        if args.config == "debug":
            cases.insert(0, (_positive_data(), _check_positive, ()))

        failures = []
        summaries = []
        for data, semantic_check, check_args in cases:
            captured = _capture(args.rom, args.elf, data)
            capture_path = args.out_dir / f"{data['name']}.captured.json"
            capture_path.write_text(
                gba_playtest.serialize_fingerprint(captured),
                encoding="utf-8",
            )
            failures.extend(semantic_check(captured, *check_args))
            failures.extend(
                _verify_or_capture(
                    captured,
                    data["name"],
                    args.capture_fingerprints,
                )
            )
            values = autoplay._values(captured["checkpoints"][0])
            summaries.append(
                f"{data['name']}: reason={captured['terminal']['reason']} "
                f"frame={captured['terminal']['frame']} "
                f"actions={values['committedActionCount']} "
                f"turn={values['chapterTurnNumber']}"
            )

        if failures:
            raise CheckError("\n".join(failures))
        print("Bounded autoplay checks passed: " + "; ".join(summaries))
        return 0
    except (
        CheckError,
        OSError,
        ValueError,
        KeyError,
        gba_playtest.PlaytestError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
