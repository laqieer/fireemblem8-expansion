#!/usr/bin/env python3
"""Build structured full-link budget evidence for autoplay strategies."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


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


def build_report(baseline_path, disabled_paths, enabled_paths):
    baseline_report = _load(baseline_path)
    if baseline_report.get("schema") != "fe8.autoplay-strategy-pre-router-budget.v1":
        raise ValueError("unexpected autoplay strategy pre-router budget schema")
    configs = {}
    for config in ("debug", "release"):
        baseline = baseline_report["configs"][config]["floating_end"]
        disabled = _floating_end(_load(disabled_paths[config]))
        enabled = _floating_end(_load(enabled_paths[config]))
        configs[config] = {
            "pre_router": {
                "source": baseline_path,
                "floating_end": baseline,
            },
            "profiles_disabled": {
                "source": disabled_paths[config],
                "floating_end": disabled,
                "shared_router_delta_bytes": disabled - baseline,
            },
            "references_enabled": {
                "source": enabled_paths[config],
                "floating_end": enabled,
                "reference_incremental_delta_bytes": enabled - disabled,
            },
        }
    return {
        "schema": "fe8.autoplay-strategy-budget.v1",
        "metric": "__floating_end full-link ROM address delta in bytes",
        "configs": configs,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--disabled-debug", required=True)
    parser.add_argument("--disabled-release", required=True)
    parser.add_argument("--enabled-debug", required=True)
    parser.add_argument("--enabled-release", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = build_report(
        args.baseline,
        {
            "debug": args.disabled_debug,
            "release": args.disabled_release,
        },
        {
            "debug": args.enabled_debug,
            "release": args.enabled_release,
        },
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("autoplay strategy budget report written: {}".format(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
