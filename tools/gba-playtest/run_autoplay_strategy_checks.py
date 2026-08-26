#!/usr/bin/env python3
"""Bounded real-CpDecide checks for generated autoplay strategy profiles."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
sys.path.insert(0, str(PLAYTEST_DIR))

import gba_playtest  # noqa: E402
import run_autoplay_bounds_checks as bounds  # noqa: E402
from probe_bindings import ElfSymbolResolver  # noqa: E402

STRATEGY_PROBE_SYMBOL = "gExpansionAutoplayStrategyRuntimeProbe"
STRATEGY_PROBE_MAGIC = 0x53545254
STRATEGY_PROBE_BINDINGS = (
    STRATEGY_PROBE_SYMBOL,
    STRATEGY_PROBE_SYMBOL + "+0x04",
    STRATEGY_PROBE_SYMBOL + "+0x08",
    STRATEGY_PROBE_SYMBOL + "+0x0c",
    STRATEGY_PROBE_SYMBOL + "+0x10",
    STRATEGY_PROBE_SYMBOL + "+0x14",
    STRATEGY_PROBE_SYMBOL + "+0x18",
    STRATEGY_PROBE_SYMBOL + "+0x1c",
)
OBJECTIVE_FIRST_ID = 0x7F2C07B5
OBJECTIVE_RUNTIME_ID = 0x5AFE4FD3


class CheckError(RuntimeError):
    pass


def _scenario(name):
    data = bounds._positive_data()
    data["name"] = name
    data["description"] = (
        "TC-AUTOPLAY-STRATEGY-001: fixed-seed bounded CpDecide_Main execution "
        "captures the terminal action/telemetry trace for one strategy profile."
    )
    data["run_until"]["checkpoint"]["probes"].extend(
        [{"address": binding, "size": 4} for binding in STRATEGY_PROBE_BINDINGS]
    )
    return data


def _capture(rom, elf, name):
    data = _scenario(name)
    scenario = gba_playtest.parse_scenario_data(
        data,
        source=name,
        symbol_resolver=ElfSymbolResolver(elf),
    )
    return gba_playtest.capture(rom, scenario)


def _symbols(nm, elf):
    completed = subprocess.run([nm, elf], capture_output=True, text=True)
    if completed.returncode:
        raise CheckError(completed.stdout + completed.stderr)
    return completed.stdout


def _check_profile(capture, label):
    values = {
        probe["address"]: int(probe["value"], 16)
        for probe in capture["checkpoints"][0]["probes"]
    }
    failures = []
    if capture["terminal"]["reason"] != "success":
        failures.append("{}: terminal was not bounded semantic success".format(label))
    if values["gExpansionAutoplayTelemetry"] != 1:
        failures.append("{}: controller did not remain COMPUTER".format(label))
    if values["gExpansionAutoplayTelemetry+0x08"] != 0:
        failures.append("{}: strategy action reported failure telemetry".format(label))
    if values["gExpansionAutoplayTelemetry+0x18"] < 1:
        failures.append("{}: no committed action was observed".format(label))
    return failures


def _probe_values(capture):
    values = {
        probe["address"]: int(probe["value"], 16)
        for probe in capture["checkpoints"][0]["probes"]
    }
    return {
        binding: values[binding]
        for binding in STRATEGY_PROBE_BINDINGS
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enabled-rom", required=True, type=Path)
    parser.add_argument("--enabled-elf", required=True, type=Path)
    parser.add_argument("--disabled-rom", required=True, type=Path)
    parser.add_argument("--disabled-elf", required=True, type=Path)
    parser.add_argument("--nm", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("TMPDIR", str(args.out_dir / "tmp"))
        Path(os.environ["TMPDIR"]).mkdir(parents=True, exist_ok=True)

        enabled_symbols = _symbols(args.nm, str(args.enabled_elf))
        disabled_symbols = _symbols(args.nm, str(args.disabled_elf))
        for symbol in (
            "ExpansionAutoplayStrategy_Aggressive",
            "ExpansionAutoplayStrategy_ObjectiveFirst",
        ):
            if symbol not in enabled_symbols:
                raise CheckError("enabled profile omitted {}".format(symbol))
            if symbol in disabled_symbols:
                raise CheckError("disabled profile retained {}".format(symbol))

        captures = {}
        for label, scenario_name, rom, elf in (
            ("enabled-first", "autoplay-strategy-enabled", args.enabled_rom, args.enabled_elf),
            ("enabled-repeat", "autoplay-strategy-enabled", args.enabled_rom, args.enabled_elf),
            (
                "disabled-fallback",
                "autoplay-strategy-disabled-fallback",
                args.disabled_rom,
                args.disabled_elf,
            ),
        ):
            capture = _capture(rom, elf, scenario_name)
            captures[label] = capture
            (args.out_dir / "{}.captured.json".format(label)).write_text(
                gba_playtest.serialize_fingerprint(capture),
                encoding="utf-8",
            )

        failures = _check_profile(captures["enabled-first"], "enabled")
        failures.extend(_check_profile(captures["disabled-fallback"], "disabled fallback"))
        failures.extend(
            gba_playtest.compare_fingerprints(
                captures["enabled-first"],
                captures["enabled-repeat"],
                policy="behavior",
            )
        )
        enabled = _probe_values(captures["enabled-first"])
        disabled = _probe_values(captures["disabled-fallback"])
        expected_enabled = {
            STRATEGY_PROBE_BINDINGS[0]: STRATEGY_PROBE_MAGIC,
            STRATEGY_PROBE_BINDINGS[1]: 1,
            STRATEGY_PROBE_BINDINGS[2]: OBJECTIVE_RUNTIME_ID,
            STRATEGY_PROBE_BINDINGS[3]: 0,
            STRATEGY_PROBE_BINDINGS[4]: 3,
            STRATEGY_PROBE_BINDINGS[5]: 3,
            STRATEGY_PROBE_BINDINGS[6]: 1,
            STRATEGY_PROBE_BINDINGS[7]: 1,
        }
        for binding, expected in expected_enabled.items():
            if enabled[binding] != expected:
                failures.append(
                    "enabled strategy probe {}={}, expected {}".format(
                        binding, enabled[binding], expected
                    )
                )
        if any(disabled[binding] != 0 for binding in STRATEGY_PROBE_BINDINGS):
            failures.append("disabled fallback unexpectedly selected a strategy")
        if failures:
            raise CheckError("\n".join(failures))

        print(
            "Autoplay strategy CpDecide checks passed: Objective-first={} "
            "move=({}, {}) Aggressive={}".format(
                enabled[STRATEGY_PROBE_BINDINGS[1]],
                enabled[STRATEGY_PROBE_BINDINGS[4]],
                enabled[STRATEGY_PROBE_BINDINGS[5]],
                enabled[STRATEGY_PROBE_BINDINGS[6]],
            )
        )
        return 0
    except (CheckError, OSError, ValueError, KeyError, gba_playtest.PlaytestError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
