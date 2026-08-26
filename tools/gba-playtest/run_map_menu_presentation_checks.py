#!/usr/bin/env python3
"""Issue #168 all-locales/all-features map-menu runtime regression."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLAYTEST_DIR = ROOT / "tools" / "gba-playtest"
BASE_SCENARIO = (
    PLAYTEST_DIR / "scenarios" / "starter-danger-overlay-modern-release.json"
)
FINGERPRINT = (
    PLAYTEST_DIR
    / "fingerprints"
    / "map-menu-presentation-all-locales-all-features-release.json"
)
sys.path.insert(0, str(PLAYTEST_DIR))

import gba_playtest  # noqa: E402
from probe_bindings import ElfSymbolResolver  # noqa: E402


class CheckError(RuntimeError):
    pass


def _checkpoint(base: dict, name: str, frame: int, framebuffer: bool) -> dict:
    checkpoint = copy.deepcopy(base)
    checkpoint["name"] = name
    checkpoint["frame"] = frame
    checkpoint["framebuffer"] = framebuffer
    return checkpoint


def _replace_expected(checkpoint: dict, replacements: dict[str, str]) -> dict:
    checkpoint = copy.deepcopy(checkpoint)
    for probe in checkpoint["probes"]:
        if probe["address"] in replacements:
            probe["expected"] = replacements[probe["address"]]
    return checkpoint


def _scenario_data() -> dict:
    base = json.loads(BASE_SCENARIO.read_text(encoding="utf-8"))
    before, cursor, overlay, cancel, _, _, _ = base["checkpoints"]
    common_replacements = {
        "0x020210e2": "0x00",
        "0x0202eb96": "0x00",
        "0x0202eb97": "0x00",
        "gExpansionDangerOverlayProbe+0x08": "0x00000000",
    }
    before = _replace_expected(
        before,
        {**common_replacements, "0x02021100": "0x0009"},
    )
    cursor = _replace_expected(
        cursor,
        {**common_replacements, "0x02021100": "0x0008"},
    )
    overlay = _replace_expected(
        overlay,
        {**common_replacements, "0x02021100": "0x0008"},
    )
    cancel = _replace_expected(
        cancel,
        {**common_replacements, "0x02021100": "0x0008"},
    )
    interactive = _replace_expected(
        cancel,
        {**common_replacements, "0x02021100": "0x0009"},
    )
    route_offset = 1000
    shifted_route = []
    for frame in base["frames"]:
        if frame["start"] >= 3500:
            continue
        shifted = copy.deepcopy(frame)
        shifted["start"] += route_offset
        shifted["end"] += route_offset
        shifted_route.append(shifted)
    return {
        "schema_version": 1,
        "name": "map-menu-presentation-all-locales-all-features-release",
        "description": (
            "TC-MAP-MENU-168 runtime positive on the named production release "
            "profile. A clean first-start English route reaches the real "
            "Prologue map-menu state, captures the localized Danger row and "
            "complete R-help box, selects Danger through the vanilla overlay "
            "path, cancels, and proves cursor interactivity. Charge's live "
            "eligible-unit help path remains covered by its dedicated debug "
            "runtime scenario."
        ),
        "frames": [
            *[
                copy.deepcopy(frame)
                for frame in base["frames"]
                if frame["start"] <= 285
            ],
            {"start": 730, "end": 736, "keys": ["A"]},
            *shifted_route,
            {"start": 4500, "end": 4506, "keys": ["START"]},
            {"start": 4900, "end": 4906, "keys": ["LEFT"]},
            {"start": 4970, "end": 4976, "keys": ["A"]},
            {"start": 5060, "end": 5066, "keys": ["R"]},
            {"start": 5200, "end": 5206, "keys": ["B"]},
            {"start": 5280, "end": 5286, "keys": ["A"]},
            {"start": 5440, "end": 5446, "keys": ["B"]},
            {"start": 5560, "end": 5566, "keys": ["RIGHT"]},
        ],
        "checkpoints": [
            _checkpoint(
                before,
                "prologue-player-before-optional-menu",
                4850,
                False,
            ),
            _checkpoint(cursor, "cursor-moved-map-interactive", 4950, False),
            _checkpoint(cursor, "danger-first-map-menu", 5020, True),
            _checkpoint(cursor, "danger-help-complete", 5130, True),
            _checkpoint(overlay, "danger-overlay-displayed", 5360, True),
            _checkpoint(cancel, "danger-overlay-cancelled", 5510, True),
            _checkpoint(
                interactive,
                "map-interactive-after-optional-menu",
                5610,
                False,
            ),
        ],
    }


def _semantic_failures(capture: dict, scenario: dict) -> list[str]:
    failures = []
    captured_by_name = {
        checkpoint["name"]: checkpoint
        for checkpoint in capture["checkpoints"]
    }
    for expected_checkpoint in scenario["checkpoints"]:
        actual_checkpoint = captured_by_name[expected_checkpoint["name"]]
        actual_probes = {
            probe["address"]: probe["value"]
            for probe in actual_checkpoint["probes"]
        }
        for expected_probe in expected_checkpoint["probes"]:
            expected = expected_probe.get("expected")
            if expected is None:
                continue
            actual = actual_probes.get(expected_probe["address"])
            if actual != expected:
                failures.append(
                    f"{expected_checkpoint['name']}: "
                    f"{expected_probe['address']}={actual}, expected {expected}"
                )

    framebuffer_checkpoints = [
        checkpoint
        for checkpoint in capture["checkpoints"]
        if "framebuffer_hash" in checkpoint
    ]
    if len(framebuffer_checkpoints) != 4:
        failures.append(
            "runtime: expected four framebuffer checkpoints, got "
            f"{len(framebuffer_checkpoints)}"
        )
    hashes = [
        checkpoint["framebuffer_hash"]
        for checkpoint in framebuffer_checkpoints
    ]
    if len(set(hashes)) != len(hashes):
        failures.append("runtime: menu/help/overlay framebuffers are not distinct")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--capture-fingerprint", action="store_true")
    args = parser.parse_args(argv)

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        scenario_data = _scenario_data()
        capture_data = copy.deepcopy(scenario_data)
        for checkpoint in capture_data["checkpoints"]:
            for probe in checkpoint["probes"]:
                probe.pop("expected", None)
        scenario = gba_playtest.parse_scenario_data(
            capture_data,
            source=capture_data["name"],
            symbol_resolver=ElfSymbolResolver(args.elf),
        )
        capture = gba_playtest.capture(args.rom, scenario)
        captured_path = args.out_dir / "map-menu-presentation.captured.json"
        captured_path.write_text(
            gba_playtest.serialize_fingerprint(capture),
            encoding="utf-8",
        )
        failures = _semantic_failures(capture, scenario_data)
        if failures:
            raise CheckError("\n".join(failures))

        if args.capture_fingerprint:
            baseline = dict(capture)
            baseline.pop("rom", None)
            FINGERPRINT.write_text(
                gba_playtest.serialize_fingerprint(baseline),
                encoding="utf-8",
            )
        elif not FINGERPRINT.is_file():
            failures.append(f"missing checked fingerprint: {FINGERPRINT}")
        else:
            expected = gba_playtest.validate_fingerprint(
                json.loads(FINGERPRINT.read_text(encoding="utf-8")),
                str(FINGERPRINT),
                policy="behavior",
            )
            failures.extend(
                gba_playtest.compare_fingerprints(
                    expected,
                    capture,
                    policy="behavior",
                )
            )

        print(
            "Map-menu presentation runtime checks passed: "
            "named-release Danger menu/help framebuffers pinned; "
            "overlay lifecycle cancelled; map interactive"
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
