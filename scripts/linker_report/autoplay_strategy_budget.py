#!/usr/bin/env python3
"""Build structured full-link budget evidence for autoplay strategies."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


ROUTER_SYMBOLS = (
    "ExpansionAutoplayStrategies_TryDecide",
    "gExpansionAutoplayStrategies",
    "gExpansionAutoplayStrategyBundles",
)


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _floating_end(report):
    assignments = report.get("pinned_assignments", report.get("assignments", ()))
    matches = [
        assignment["address"]
        for assignment in assignments
        if assignment["name"] == "__floating_end"
    ]
    if len(matches) != 1:
        raise ValueError("expected one __floating_end assignment, got {}".format(matches))
    return matches[0]


def _symbol_evidence(nm, elf):
    completed = subprocess.run(
        [nm, str(elf)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    names = {
        line.split()[-1]
        for line in completed.stdout.splitlines()
        if line.split()
    }
    return {name: name in names for name in ROUTER_SYMBOLS}


def build_report(absent_paths, disabled_paths, enabled_paths, elf_paths, nm):
    configs = {}
    for config in ("debug", "release"):
        absent = _floating_end(_load(absent_paths[config]))
        disabled = _floating_end(_load(disabled_paths[config]))
        enabled = _floating_end(_load(enabled_paths[config]))
        absent_symbols = _symbol_evidence(nm, elf_paths["absent"][config])
        disabled_symbols = _symbol_evidence(nm, elf_paths["disabled"][config])
        enabled_symbols = _symbol_evidence(nm, elf_paths["enabled"][config])
        if any(absent_symbols.values()):
            raise ValueError("router-absent {} ELF retains router symbols".format(config))
        if not all(disabled_symbols.values()):
            raise ValueError("profiles-disabled {} ELF omits router symbols".format(config))
        if not all(enabled_symbols.values()):
            raise ValueError("references-enabled {} ELF omits router symbols".format(config))
        configs[config] = {
            "router_absent": {
                "source": absent_paths[config],
                "elf": elf_paths["absent"][config],
                "floating_end": absent,
                "symbols": absent_symbols,
            },
            "profiles_disabled": {
                "source": disabled_paths[config],
                "elf": elf_paths["disabled"][config],
                "floating_end": disabled,
                "shared_router_delta_bytes": disabled - absent,
                "symbols": disabled_symbols,
            },
            "references_enabled": {
                "source": enabled_paths[config],
                "elf": elf_paths["enabled"][config],
                "floating_end": enabled,
                "reference_incremental_delta_bytes": enabled - disabled,
                "symbols": enabled_symbols,
            },
        }
    return {
        "schema": "fe8.autoplay-strategy-budget.v1",
        "metric": "__floating_end full-link ROM address delta in bytes",
        "configs": configs,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nm", required=True)
    parser.add_argument("--absent-debug", required=True)
    parser.add_argument("--absent-release", required=True)
    parser.add_argument("--disabled-debug", required=True)
    parser.add_argument("--disabled-release", required=True)
    parser.add_argument("--enabled-debug", required=True)
    parser.add_argument("--enabled-release", required=True)
    parser.add_argument("--absent-debug-elf", required=True)
    parser.add_argument("--absent-release-elf", required=True)
    parser.add_argument("--disabled-debug-elf", required=True)
    parser.add_argument("--disabled-release-elf", required=True)
    parser.add_argument("--enabled-debug-elf", required=True)
    parser.add_argument("--enabled-release-elf", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = build_report(
        {
            "debug": args.absent_debug,
            "release": args.absent_release,
        },
        {
            "debug": args.disabled_debug,
            "release": args.disabled_release,
        },
        {
            "debug": args.enabled_debug,
            "release": args.enabled_release,
        },
        {
            "absent": {
                "debug": args.absent_debug_elf,
                "release": args.absent_release_elf,
            },
            "disabled": {
                "debug": args.disabled_debug_elf,
                "release": args.disabled_release_elf,
            },
            "enabled": {
                "debug": args.enabled_debug_elf,
                "release": args.enabled_release_elf,
            },
        },
        args.nm,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("autoplay strategy budget report written: {}".format(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
