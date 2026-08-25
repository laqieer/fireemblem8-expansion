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


class CheckError(RuntimeError):
    pass


def _scenario(name):
    data = bounds._positive_data()
    data["name"] = name
    data["description"] = (
        "TC-AUTOPLAY-STRATEGY-001: fixed-seed bounded CpDecide_Main execution "
        "captures the terminal action/telemetry trace for one strategy profile."
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
    failures = bounds._check_positive(capture)
    if capture["terminal"]["reason"] != "success":
        failures.append("{}: terminal was not bounded semantic success".format(label))
    return failures


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
        if failures:
            raise CheckError("\n".join(failures))

        enabled = captures["enabled-first"]["checkpoints"][0]
        disabled = captures["disabled-fallback"]["checkpoints"][0]
        print(
            "Autoplay strategy CpDecide checks passed: enabled actions={} "
            "disabled-fallback actions={}".format(
                next(
                    probe["value"]
                    for probe in enabled["probes"]
                    if probe["address"].endswith("+0x18")
                ),
                next(
                    probe["value"]
                    for probe in disabled["probes"]
                    if probe["address"].endswith("+0x18")
                ),
            )
        )
        return 0
    except (CheckError, OSError, ValueError, KeyError, gba_playtest.PlaytestError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
