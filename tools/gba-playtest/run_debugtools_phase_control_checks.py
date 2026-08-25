#!/usr/bin/env python3
"""Real-ROM semantic checks for issue #124 phase-control requests."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
FINGERPRINT_DIR = PLAYTEST_DIR / "fingerprints"
BMUNIT_HEADER = REPO_ROOT / "include" / "bmunit.h"
sys.path.insert(0, str(PLAYTEST_DIR))

import gba_playtest  # noqa: E402
import run_autoplay_checks as autoplay  # noqa: E402
from probe_bindings import ElfSymbolResolver  # noqa: E402

PHASE_PROBE = "gDebugToolsProbe"
PHASE_TURN_SAMPLE = f"{PHASE_PROBE}+0x8c"
PHASE_REQUESTED_COUNT = f"{PHASE_PROBE}+0x98"
PHASE_APPLIED_COUNT = f"{PHASE_PROBE}+0x9c"
PHASE_RESTORED_COUNT = f"{PHASE_PROBE}+0xa8"
PHASE_LAST_RESULT = f"{PHASE_PROBE}+0xac"
PHASE_LAST_REQUEST_KIND = f"{PHASE_PROBE}+0xb0"
PHASE_LAST_FACTION = f"{PHASE_PROBE}+0xb4"
PHASE_LAST_MODE = f"{PHASE_PROBE}+0xb8"
FLAG_MENU_GREEN_BLOCK_ROW = 6

def _load_faction_constants():
    source = BMUNIT_HEADER.read_text(encoding="utf-8")
    values = {}
    for name in ("FACTION_BLUE", "FACTION_GREEN", "FACTION_RED"):
        match = re.search(rf"\b{name}\s*=\s*(0x[0-9A-Fa-f]+|\d+)", source)
        if match is None:
            raise RuntimeError(f"{BMUNIT_HEADER}: {name} is unavailable")
        values[name] = int(match.group(1), 0)
    return values


FACTION_CONSTANTS = _load_faction_constants()
PLAYER_FACTION = FACTION_CONSTANTS["FACTION_BLUE"]
RED_FACTION = FACTION_CONSTANTS["FACTION_RED"]
GREEN_FACTION = FACTION_CONSTANTS["FACTION_GREEN"]
PHASE_CONTROL_OK = 0
PHASE_CONTROL_REQUEST_TURN = 1
PHASE_CONTROL_REQUEST_FACTION = 2
PHASE_CONTROL_BLOCKED = 2
RELEASE_PLAYER_TURN = 1


class CheckError(RuntimeError):
    pass


def _phase_probes():
    return [
        {"address": "gPlaySt+0x0f", "size": 1},
        {"address": "gPlaySt+0x10", "size": 2},
        {"address": "gBmSt+0x14", "size": 1},
        {"address": PHASE_TURN_SAMPLE, "size": 4},
        {"address": PHASE_REQUESTED_COUNT, "size": 4},
        {"address": PHASE_APPLIED_COUNT, "size": 4},
        {"address": PHASE_RESTORED_COUNT, "size": 4},
        {"address": PHASE_LAST_RESULT, "size": 4},
        {"address": PHASE_LAST_REQUEST_KIND, "size": 4},
        {"address": PHASE_LAST_FACTION, "size": 4},
        {"address": PHASE_LAST_MODE, "size": 4},
    ]


def _release_probes():
    return [
        {"address": "gPlaySt+0x0f", "size": 1},
        {"address": "gPlaySt+0x10", "size": 2},
        {"address": "gBmSt+0x14", "size": 1},
        {"address": PHASE_PROBE, "size": 4},
    ]


def _positive_frames():
    return [
        *autoplay._load_route("savesuspend-resume-modern-debug", 16986),
        {"start": 17150, "end": 17156, "keys": ["SELECT", "L"]},
        *[
            {"start": frame, "end": frame + 6, "keys": ["DOWN"]}
            for frame in range(17250, 17610, 60)
        ],
        {"start": 17630, "end": 17636, "keys": ["A"]},
        {"start": 17750, "end": 17756, "keys": ["DOWN"]},
        {"start": 17810, "end": 17816, "keys": ["A"]},
        {"start": 17970, "end": 17976, "keys": ["RIGHT"]},
        {"start": 18120, "end": 18126, "keys": ["A"]},
        {"start": 18220, "end": 18226, "keys": ["UP"]},
        {"start": 18320, "end": 18326, "keys": ["R"]},
        {"start": 18420, "end": 18426, "keys": ["B"]},
        {"start": 18520, "end": 18526, "keys": ["A"]},
        *[
            {"start": frame, "end": frame + 4, "keys": ["A"]}
            for frame in range(23000, 24600, 60)
        ],
        {"start": 25000, "end": 25006, "keys": ["RIGHT"]},
    ]


def _release_frames():
    base = autoplay._negative_data("release")
    base_frame = max(checkpoint["frame"] for checkpoint in base["checkpoints"])
    frames = [*base["frames"]]
    frames.extend(
        (
            {
                "start": base_frame + 80,
                "end": base_frame + 86,
                "keys": ["SELECT", "L"],
            },
            {
                "start": base_frame + 180,
                "end": base_frame + 186,
                "keys": ["RIGHT"],
            },
        )
    )
    return frames, base_frame


def _positive_data():
    return {
        "schema_version": 1,
        "name": "debugtools-phase-control-modern-debug",
        "description": (
            "TC-DEBUGTOOLS-PROTOTYPE-002 positive: a clean Chapter 2 PLAYER "
            "map opens the real SELECT+L debug hub, moves to Flag/Chapter, "
            "selects Apply Turn +1, closes the hub, invokes #87's native "
            "one-phase Charge command, then observes the real red boundary "
            "and restored interactive blue map."
        ),
        "frames": _positive_frames(),
        "checkpoints": [
            {
                "name": "player-before-apply",
                "frame": 17140,
                "framebuffer": False,
                "probes": _phase_probes(),
            },
            {
                "name": "turn-requested-from-live-submenu",
                "frame": 17920,
                "framebuffer": False,
                "probes": _phase_probes(),
            },
            {
                "name": "red-boundary-observes-requested-turn",
                "frame": 19000,
                "framebuffer": False,
                "probes": _phase_probes(),
            },
            {
                "name": "next-blue-before-map-input",
                "frame": 24990,
                "framebuffer": False,
                "probes": _phase_probes(),
            },
            {
                "name": "next-blue-map-interactive",
                "frame": 25020,
                "framebuffer": False,
                "probes": _phase_probes(),
            },
        ],
    }


def _blocked_frames():
    return [
        *autoplay._load_route("savesuspend-resume-modern-debug", 16986),
        {"start": 17150, "end": 17156, "keys": ["SELECT", "L"]},
        *[
            {"start": frame, "end": frame + 6, "keys": ["DOWN"]}
            for frame in range(17250, 17610, 60)
        ],
        {"start": 17630, "end": 17636, "keys": ["A"]},
        *[
            {"start": 17750 + index * 60, "end": 17756 + index * 60, "keys": ["DOWN"]}
            for index in range(FLAG_MENU_GREEN_BLOCK_ROW)
        ],
        {"start": 18110, "end": 18116, "keys": ["A"]},
        {"start": 18270, "end": 18276, "keys": ["RIGHT"]},
        {"start": 18420, "end": 18426, "keys": ["A"]},
        {"start": 18520, "end": 18526, "keys": ["UP"]},
        {"start": 18620, "end": 18626, "keys": ["R"]},
        {"start": 18720, "end": 18726, "keys": ["B"]},
        {"start": 18820, "end": 18826, "keys": ["A"]},
        *[
            {"start": frame, "end": frame + 4, "keys": ["A"]}
            for frame in range(23200, 24800, 60)
        ],
    ]


def _blocked_data():
    return {
        "schema_version": 1,
        "name": "debugtools-phase-blocked-modern-debug",
        "description": (
            "TC-DEBUGTOOLS-PROTOTYPE-002 blocked negative: the live "
            "Flag/Chapter submenu selects Apply G Block, then #87 Charge "
            "advances the current blue phase. The blocked green phase bypasses "
            "both computer children and ordinary player input returns on the "
            "following blue phase."
        ),
        "frames": _blocked_frames(),
        "checkpoints": [
            {
                "name": "player-before-green-block",
                "frame": 17140,
                "framebuffer": False,
                "probes": _phase_probes(),
            },
            {
                "name": "green-block-requested",
                "frame": 18350,
                "framebuffer": False,
                "probes": _phase_probes(),
            },
            {
                "name": "green-block-next-blue-restored",
                "frame": 25590,
                "framebuffer": False,
                "probes": _phase_probes(),
            },
        ],
    }


def _negative_data(config):
    frames, base_frame = _release_frames()
    return {
        "schema_version": 1,
        "name": f"debugtools-phase-control-modern-{config}",
        "description": (
            "TC-DEBUGTOOLS-PROTOTYPE-002 release negative: a stable "
            "clean-boot Prologue blue PLAYER map receives the debug hotkey "
            "and a native cursor move. The hotkey is compiled out, "
            "gDebugToolsProbe remains zero, and the same player map accepts "
            "the native movement."
        ),
        "frames": frames,
        "checkpoints": [
            {
                "name": "release-blue-player-before-debug-input",
                "frame": base_frame,
                "framebuffer": False,
                "probes": _release_probes(),
            },
            {
                "name": "release-blue-player-debug-input-inert",
                "frame": base_frame + 120,
                "framebuffer": False,
                "probes": _release_probes(),
            },
            {
                "name": "release-blue-player-map-interactive",
                "frame": base_frame + 220,
                "framebuffer": False,
                "probes": _release_probes(),
            },
        ],
    }


def _capture(rom, elf, data):
    scenario = gba_playtest.parse_scenario_data(
        data,
        source=data["name"],
        symbol_resolver=ElfSymbolResolver(elf),
    )
    return gba_playtest.capture(rom, scenario)


def _checkpoint_values(capture, index):
    return {
        probe["address"]: int(probe["value"], 16)
        for probe in capture["checkpoints"][index]["probes"]
    }


def _check_positive(capture):
    before = _checkpoint_values(capture, 0)
    requested = _checkpoint_values(capture, 1)
    boundary = _checkpoint_values(capture, 2)
    restored = _checkpoint_values(capture, 3)
    interactive = _checkpoint_values(capture, 4)
    failures = []

    if before["gPlaySt+0x0f"] != PLAYER_FACTION:
        failures.append("positive: fixture did not begin in an interactive blue phase")
    if requested[PHASE_REQUESTED_COUNT] != 1:
        failures.append(
            "positive: the active debugtools-session lock did not accept "
            "Apply Turn +1 exactly once"
        )
    if requested[PHASE_LAST_REQUEST_KIND] != PHASE_CONTROL_REQUEST_TURN:
        failures.append("positive: submenu did not select the typed turn request")
    if requested[PHASE_APPLIED_COUNT] != 0:
        failures.append("positive: turn request applied before the phase boundary")
    if boundary["gPlaySt+0x0f"] != RED_FACTION:
        failures.append("positive: no red phase boundary was observed")
    if boundary["gPlaySt+0x10"] != before["gPlaySt+0x10"] + 1:
        failures.append(
            "positive: red boundary turn did not equal the selected +1 value"
        )
    if boundary[PHASE_TURN_SAMPLE] != boundary["gPlaySt+0x10"]:
        failures.append(
            "positive: phase telemetry did not sample the red event-boundary turn"
        )
    if boundary[PHASE_APPLIED_COUNT] != 1 or boundary[PHASE_RESTORED_COUNT] != 1:
        failures.append(
            "positive: boundary did not record exactly one apply/restoration"
        )
    if boundary[PHASE_LAST_RESULT] != PHASE_CONTROL_OK:
        failures.append("positive: boundary did not finish with the typed OK result")
    if restored["gPlaySt+0x0f"] != PLAYER_FACTION:
        failures.append("positive: the next blue phase was not restored")
    if interactive["gBmSt+0x14"] != restored["gBmSt+0x14"] + 1:
        failures.append("positive: map cursor did not respond after phase control")
    if restored[PHASE_APPLIED_COUNT] != 1 or restored[PHASE_RESTORED_COUNT] != 1:
        failures.append("positive: phase control was not a one-boundary request")
    return failures


def _check_blocked(capture):
    before = _checkpoint_values(capture, 0)
    requested = _checkpoint_values(capture, 1)
    restored = _checkpoint_values(capture, 2)
    failures = []

    if before["gPlaySt+0x0f"] != PLAYER_FACTION:
        failures.append("blocked: fixture did not begin in an interactive blue phase")
    if requested[PHASE_REQUESTED_COUNT] != 1:
        failures.append("blocked: Apply G Block did not record one request")
    if requested[PHASE_LAST_REQUEST_KIND] != PHASE_CONTROL_REQUEST_FACTION:
        failures.append("blocked: request was not typed as faction control")
    if requested[PHASE_LAST_FACTION] != GREEN_FACTION:
        failures.append("blocked: request did not target green")
    if requested[PHASE_LAST_MODE] != PHASE_CONTROL_BLOCKED:
        failures.append("blocked: request did not use BLOCKED mode")
    if restored["gPlaySt+0x0f"] != PLAYER_FACTION:
        failures.append("blocked: normal blue control did not return")
    if restored["gPlaySt+0x10"] != before["gPlaySt+0x10"] + 1:
        failures.append("blocked: skipped green phase did not advance to the next blue turn")
    if restored[PHASE_APPLIED_COUNT] != 1 or restored[PHASE_RESTORED_COUNT] != 1:
        failures.append("blocked: green block was not a one-phase restoration")
    return failures


def _check_negative(capture):
    failures = []
    before = _checkpoint_values(capture, 0)
    final = _checkpoint_values(capture, -1)
    for checkpoint in capture["checkpoints"]:
        values = {
            probe["address"]: int(probe["value"], 16)
            for probe in checkpoint["probes"]
        }
        if values["gPlaySt+0x0f"] != PLAYER_FACTION:
            failures.append("release: map did not remain in the blue PLAYER faction")
        if values["gPlaySt+0x10"] != RELEASE_PLAYER_TURN:
            failures.append(
                "release: map turn did not remain at the expected player turn"
            )
        for probe in checkpoint["probes"]:
            if probe["address"] == PHASE_PROBE and probe["value"] != "0x00000000":
                failures.append("release: debugtools probe changed after debug inputs")
    if final["gBmSt+0x14"] != before["gBmSt+0x14"] + 1:
        failures.append("release: native map input did not remain interactive")
    return failures


def _verify_or_capture(capture, name, capture_fingerprints):
    path = FINGERPRINT_DIR / f"{name}.json"
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


def main(argv=None):
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
        if args.config == "debug":
            cases = [
                (_positive_data(), _check_positive),
                (_blocked_data(), _check_blocked),
            ]
        else:
            cases = [(_negative_data(args.config), _check_negative)]
        failures = []
        for data, semantic_check in cases:
            capture = _capture(args.rom, args.elf, data)
            capture_path = args.out_dir / f"{data['name']}.captured.json"
            capture_path.write_text(
                gba_playtest.serialize_fingerprint(capture), encoding="utf-8"
            )
            failures.extend(semantic_check(capture))
            failures.extend(
                _verify_or_capture(capture, data["name"], args.capture_fingerprints)
            )
        if failures:
            raise CheckError("\n".join(failures))
        print(
            "Debugtools phase-control runtime check passed: "
            f"{args.config}"
        )
        return 0
    except (CheckError, OSError, ValueError, KeyError, gba_playtest.PlaytestError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
