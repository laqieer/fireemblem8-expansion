#!/usr/bin/env python3
"""Bounded default-chapter negative for typed chapter objectives."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
FINGERPRINT_DIR = PLAYTEST_DIR / "fingerprints"
SCENARIOS_DIR = PLAYTEST_DIR / "scenarios"
sys.path.insert(0, str(PLAYTEST_DIR))
sys.path.insert(0, str(PLAYTEST_DIR / "tests"))

import gba_playtest  # noqa: E402
import run_autoplay_bounds_checks as autoplay_bounds  # noqa: E402
import sram_fixture  # noqa: E402
from probe_bindings import ElfSymbolResolver  # noqa: E402

OBJECTIVE_TELEMETRY_SYMBOL = "gExpansionChapterObjectiveTelemetry"


class CheckError(RuntimeError):
    pass


def scenario_data() -> dict:
    """Use #86's bounded default-PLAYER route, adding objective probes."""
    scenario = autoplay_bounds._negative_data("debug")
    scenario["name"] = "chapter-objectives-empty-modern-debug"
    scenario["description"] = (
        "TC-AUTOPLAY-OBJECTIVE-001 default negative: an unchanged chapter "
        "with no authored objective bundle remains inactive through #86's "
        "bounded semantic runner."
    )
    scenario["run_until"]["checkpoint"]["probes"].extend(
        [
            {"address": OBJECTIVE_TELEMETRY_SYMBOL, "size": 4},
            {"address": OBJECTIVE_TELEMETRY_SYMBOL + "+0x04", "size": 4},
            {"address": OBJECTIVE_TELEMETRY_SYMBOL + "+0x08", "size": 4},
            {"address": OBJECTIVE_TELEMETRY_SYMBOL + "+0x0c", "size": 4},
        ]
    )
    return scenario


def fixture_scenario_data() -> dict:
    """Extend the ordinary suspend/resume route with authored-objective probes."""
    source_path = SCENARIOS_DIR / "savesuspend-resume-modern-debug.json"
    scenario = json.loads(source_path.read_text(encoding="utf-8"))
    scenario["name"] = "chapter-objectives-fixture-suspend-resume"
    scenario["description"] = (
        "TC-AUTOPLAY-OBJECTIVE-001 authored fixture: execute a generated "
        "objective bundle through the ordinary Chapter 2 Suspend, reset, "
        "and Resume flow, then require reconstructed telemetry."
    )
    for checkpoint in scenario["checkpoints"]:
        if checkpoint["name"] in ("suspend-confirmed", "resumed-chapter2"):
            for probe in checkpoint["probes"]:
                probe.pop("expected", None)
            checkpoint["probes"].extend(
                [
                    {"address": OBJECTIVE_TELEMETRY_SYMBOL, "size": 4},
                    {"address": OBJECTIVE_TELEMETRY_SYMBOL + "+0x04", "size": 4},
                    {"address": OBJECTIVE_TELEMETRY_SYMBOL + "+0x08", "size": 4},
                    {"address": OBJECTIVE_TELEMETRY_SYMBOL + "+0x0c", "size": 4},
                ]
            )
    return scenario


def _values(capture: dict) -> dict[str, int]:
    probes = capture["checkpoints"][0]["probes"]
    values = {probe["address"]: int(probe["value"], 16) for probe in probes}
    return {
        "objectiveId": values[OBJECTIVE_TELEMETRY_SYMBOL],
        "state": values[OBJECTIVE_TELEMETRY_SYMBOL + "+0x04"],
        "progress": values[OBJECTIVE_TELEMETRY_SYMBOL + "+0x08"],
        "activeCount": values[OBJECTIVE_TELEMETRY_SYMBOL + "+0x0c"],
    }


def _checkpoint_values(capture: dict, name: str) -> dict[str, int]:
    for checkpoint in capture["checkpoints"]:
        if checkpoint["name"] == name:
            return {
                probe["address"]: int(probe["value"], 16)
                for probe in checkpoint["probes"]
            }
    raise CheckError("fixture objective scenario omitted checkpoint '{}'".format(name))


def _check(capture: dict) -> list[str]:
    failures = autoplay_bounds._check_default(capture, "debug")
    values = _values(capture)
    if capture["terminal"]["reason"] != "max_frames":
        failures.append(
            "default objective negative: terminal reason is {!r}, expected max_frames".format(
                capture["terminal"]["reason"]
            )
        )
    for name, value in values.items():
        if value != 0:
            failures.append("default objective negative: {}={}, expected 0".format(name, value))
    return failures


def _check_fixture(capture: dict) -> list[str]:
    """Prove a real authored table is re-evaluated after ReadSuspendSave."""
    failures = []
    suspended = _checkpoint_values(capture, "suspend-confirmed")
    resumed = _checkpoint_values(capture, "resumed-chapter2")
    objective_bindings = (
        OBJECTIVE_TELEMETRY_SYMBOL,
        OBJECTIVE_TELEMETRY_SYMBOL + "+0x04",
        OBJECTIVE_TELEMETRY_SYMBOL + "+0x08",
        OBJECTIVE_TELEMETRY_SYMBOL + "+0x0c",
    )

    for address, expected in (
        ("0x020210b2", 2),
        ("0x020210b3", 0),
    ):
        if suspended.get(address) != expected or resumed.get(address) != expected:
            failures.append(
                "fixture suspend/resume: {} changed state ({} -> {}), expected {}".format(
                    address, suspended.get(address), resumed.get(address), expected
                )
            )

    if suspended.get(OBJECTIVE_TELEMETRY_SYMBOL, 0) == 0:
        failures.append("fixture suspend/resume: no authored objective was active before Suspend")
    if suspended.get(OBJECTIVE_TELEMETRY_SYMBOL + "+0x04", 0) == 0:
        failures.append("fixture suspend/resume: active authored objective had inactive status before Suspend")

    for address in objective_bindings:
        if resumed.get(address) != suspended.get(address):
            failures.append(
                "fixture suspend/resume: telemetry {} changed across Resume ({} != {})".format(
                    address, resumed.get(address), suspended.get(address)
                )
            )

    return failures


def _fingerprint_path() -> Path:
    return FINGERPRINT_DIR / "chapter-objectives-empty-modern-debug.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--fixture-rom", required=True, type=Path)
    parser.add_argument("--fixture-elf", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--capture-fingerprint", action="store_true")
    args = parser.parse_args(argv)

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("TMPDIR", str(args.out_dir / "tmp"))
        Path(os.environ["TMPDIR"]).mkdir(parents=True, exist_ok=True)
        scenario = scenario_data()
        capture = gba_playtest.capture(
            args.rom,
            gba_playtest.parse_scenario_data(
                scenario,
                source=scenario["name"],
                symbol_resolver=ElfSymbolResolver(args.elf),
            ),
        )
        capture_path = args.out_dir / (scenario["name"] + ".captured.json")
        capture_path.write_text(gba_playtest.serialize_fingerprint(capture), encoding="utf-8")

        failures = _check(capture)
        path = _fingerprint_path()
        if args.capture_fingerprint:
            baseline = dict(capture)
            baseline.pop("rom", None)
            path.write_text(gba_playtest.serialize_fingerprint(baseline), encoding="utf-8")
        elif not path.is_file():
            failures.append("missing checked fingerprint: {}".format(path))
        else:
            expected = gba_playtest.validate_fingerprint(
                json.loads(path.read_text(encoding="utf-8")), str(path), policy="behavior"
            )
            failures.extend(gba_playtest.compare_fingerprints(expected, capture, policy="behavior"))

        fixture_dir = args.out_dir / "authored-fixture"
        fixture_sram = sram_fixture.write_fixture(
            fixture_dir / "suspend-resume-current.sav", sram_fixture.STATE_CURRENT
        )
        fixture_capture = gba_playtest.capture(
            args.fixture_rom,
            gba_playtest.parse_scenario_data(
                fixture_scenario_data(),
                source="chapter-objectives-fixture-suspend-resume",
                symbol_resolver=ElfSymbolResolver(args.fixture_elf),
            ),
            sram_image=fixture_sram,
        )
        (fixture_dir / "chapter-objectives-fixture-suspend-resume.captured.json").write_text(
            gba_playtest.serialize_fingerprint(fixture_capture), encoding="utf-8"
        )
        failures.extend(_check_fixture(fixture_capture))

        if failures:
            raise CheckError("\n".join(failures))
        values = _values(capture)
        print(
            "Chapter objective checks passed: default reason={} frame={} active={} "
            "and authored fixture reconstructed after Suspend/Resume".format(
                capture["terminal"]["reason"], capture["terminal"]["frame"], values["activeCount"]
            )
        )
        return 0
    except (CheckError, OSError, ValueError, KeyError, gba_playtest.PlaytestError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
