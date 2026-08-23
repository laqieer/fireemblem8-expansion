#!/usr/bin/env python3
"""Deterministic runtime checks for issue #87 one-phase Charge delegation."""

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

PLAYER_CONTROL = 0
PLAYER_PHASE_STATE = 1
NO_FAILURE = 0
SUPPORTED_ACTIONS = frozenset((0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13))


class CheckError(RuntimeError):
    pass


def _positive_data() -> dict:
    lifecycle_probes = [
        *autoplay._probes(),
        {"address": "gBmSt+0x01", "size": 1},
        {"address": "gBmSt+0x14", "size": 2},
        {"address": "gBmSt+0x16", "size": 2},
    ]
    menu_probes = [
        {"address": autoplay.TELEMETRY_SYMBOL, "size": 4},
        {"address": f"{autoplay.TELEMETRY_SYMBOL}+0x04", "size": 4},
        {"address": f"{autoplay.TELEMETRY_SYMBOL}+0x08", "size": 4},
        {"address": f"{autoplay.TELEMETRY_SYMBOL}+0x0c", "size": 4},
        {"address": f"{autoplay.TELEMETRY_SYMBOL}+0x10", "size": 4},
        {"address": "gPlaySt+0x0f", "size": 1},
        {"address": "gPlaySt+0x10", "size": 2},
        {"address": "gBmSt+0x01", "size": 1},
        {"address": "gBmSt+0x14", "size": 2},
        {"address": "gBmSt+0x16", "size": 2},
    ]
    return {
        "schema_version": 1,
        "name": "autoplay-charge-modern-debug",
        "description": (
            "TC-AUTOPLAY-CHARGE-001 positive: clean-boot Chapter 2 reaches "
            "an ordinary interactive blue phase, opens the real map menu on "
            "an empty tile, wraps to the localized Charge row, and delegates "
            "the current phase through issue #85 without its debug chord."
        ),
        "frames": [
            *autoplay._load_route("savesuspend-resume-modern-debug", 16986),
            {"start": 17150, "end": 17156, "keys": ["RIGHT"]},
            {"start": 17300, "end": 17306, "keys": ["A"]},
            {"start": 17400, "end": 17406, "keys": ["UP"]},
            {"start": 17500, "end": 17506, "keys": ["R"]},
            {"start": 17600, "end": 17606, "keys": ["A"]},
            *[
                {"start": frame, "end": frame + 4, "keys": ["A"]}
                for frame in range(18300, 21300, 60)
            ],
        ],
        "checkpoints": [
            {
                "name": "interactive-player-before-charge",
                "frame": 17290,
                "framebuffer": False,
                "probes": lifecycle_probes,
            },
            {
                "name": "map-menu-open",
                "frame": 17350,
                "framebuffer": True,
                "probes": menu_probes,
            },
            {
                "name": "charge-row-selected",
                "frame": 17450,
                "framebuffer": True,
                "probes": menu_probes,
            },
            {
                "name": "charge-r-help-domain-guard",
                "frame": 17550,
                "framebuffer": True,
                "probes": menu_probes,
            },
            {
                "name": "charge-command-dispatched",
                "frame": 17650,
                "framebuffer": True,
                "probes": menu_probes,
            },
            {
                "name": "next-blue-player-restored",
                "frame": 23100,
                "framebuffer": False,
                "probes": lifecycle_probes,
            },
        ],
    }


def _capture(rom: Path, elf: Path, data: dict) -> dict:
    scenario = gba_playtest.parse_scenario_data(
        data,
        source=data["name"],
        symbol_resolver=ElfSymbolResolver(elf),
    )
    return gba_playtest.capture(rom, scenario)


def _check_positive(capture: dict) -> list[str]:
    before = autoplay._values(capture["checkpoints"][0])
    after = autoplay._values(capture["checkpoints"][-1])
    failures = autoplay._check_default(
        {"checkpoints": [capture["checkpoints"][0]]},
        "debug pre-Charge",
    )

    expected = {
        "controller": PLAYER_CONTROL,
        "state": PLAYER_PHASE_STATE,
        "failure": NO_FAILURE,
        "debugActivationCount": 0,
        "bluePhaseStartCount": 1,
        "bluePhaseCompleteCount": 1,
        "faction": 0,
    }
    for name, expected_value in expected.items():
        if after[name] != expected_value:
            failures.append(
                f"positive: {name}={after[name]}, expected {expected_value}"
            )
    if not (1 <= after["eligibleActorCount"] <= 62):
        failures.append(
            "positive: eligibleActorCount="
            f"{after['eligibleActorCount']}, expected 1..62"
        )
    if after["committedActionCount"] < 1:
        failures.append("positive: no delegated legal action was committed")
    if not (1 <= after["lastActorSlot"] <= 0x3E):
        failures.append(
            f"positive: last actor slot {after['lastActorSlot']} is not blue"
        )
    if after["lastActionId"] not in SUPPORTED_ACTIONS:
        failures.append(
            f"positive: last action {after['lastActionId']} is unsupported"
        )
    if after["hostileTargetCheckCount"] < 1:
        failures.append("positive: delegated AI never classified red as hostile")
    if after["alliedTargetCheckCount"] < 1:
        failures.append("positive: delegated AI never classified green as allied")
    if after["invalidRecordCount"] != 0:
        failures.append(
            "positive: invalid telemetry count is "
            f"{after['invalidRecordCount']}, expected zero"
        )
    if after["suspendWriteSuppressedCount"] < 1:
        failures.append("positive: blue-AI suspend suppression was not exercised")
    if after["chapterTurnNumber"] != before["chapterTurnNumber"] + 1:
        failures.append(
            "positive: next interactive blue turn mismatch "
            f"({before['chapterTurnNumber']}->{after['chapterTurnNumber']})"
        )
    return failures


def _verify_or_capture(
    capture: dict,
    name: str,
    capture_fingerprints: bool,
) -> list[str]:
    path = FINGERPRINT_DIR / f"{name}.json"
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
    parser.add_argument("--enabled-rom", required=True, type=Path)
    parser.add_argument("--enabled-elf", required=True, type=Path)
    parser.add_argument("--disabled-rom", required=True, type=Path)
    parser.add_argument("--disabled-elf", required=True, type=Path)
    parser.add_argument("--config", required=True, choices=("debug", "release"))
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--capture-fingerprints", action="store_true")
    args = parser.parse_args(argv)

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("TMPDIR", str(args.out_dir / "tmp"))
        Path(os.environ["TMPDIR"]).mkdir(parents=True, exist_ok=True)

        cases = [
            (
                args.disabled_rom,
                args.disabled_elf,
                autoplay._negative_data(args.config),
                lambda capture: autoplay._check_default(capture, args.config),
            )
        ]
        if args.config == "debug":
            cases.insert(
                0,
                (
                    args.enabled_rom,
                    args.enabled_elf,
                    _positive_data(),
                    _check_positive,
                ),
            )

        failures = []
        summaries = []
        for rom, elf, data, semantic_check in cases:
            captured = _capture(rom, elf, data)
            capture_path = args.out_dir / f"{data['name']}.captured.json"
            capture_path.write_text(
                gba_playtest.serialize_fingerprint(captured),
                encoding="utf-8",
            )
            failures.extend(semantic_check(captured))
            failures.extend(
                _verify_or_capture(
                    captured,
                    data["name"],
                    args.capture_fingerprints
                    and data["name"] == "autoplay-charge-modern-debug",
                )
            )
            values = autoplay._values(captured["checkpoints"][-1])
            summaries.append(
                f"{data['name']}: controller={values['controller']} "
                f"starts={values['bluePhaseStartCount']} "
                f"completions={values['bluePhaseCompleteCount']} "
                f"eligible={values['eligibleActorCount']} "
                f"actions={values['committedActionCount']} "
                f"turn={values['chapterTurnNumber']} "
                f"faction={values['faction']}"
            )

        if failures:
            raise CheckError("\n".join(failures))
        print(
            "Blue-phase delegation runtime checks passed: "
            + "; ".join(summaries)
        )
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
