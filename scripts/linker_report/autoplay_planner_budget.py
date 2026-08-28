#!/usr/bin/env python3
"""Validate the complete linked autoplay-planner ROM delta."""

import argparse
import json
import subprocess
from pathlib import Path


LIMIT = 12 * 1024
PLANNER_SYMBOLS = (
    "gExpansionAutoplayPlannerObservation",
    "gExpansionAutoplayPlannerCommand",
    "gExpansionAutoplayPlannerCampaignCheckpoint",
)
REPRESENTATIVE_HOOKS = (
    "cp_decide", "bmtarget", "bmitemuse", "bmmenu", "bmusemind",
    "fogmap", "bm", "playerphase", "bmio", "rng", "expansion_autoplay",
)


class PlannerBudgetError(ValueError):
    pass


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def floating_end(report):
    matches = [
        entry["address"]
        for entry in report.get("pinned_assignments", report.get("assignments", ()))
        if entry["name"] == "__floating_end"
    ]
    if len(matches) != 1:
        raise PlannerBudgetError("linked report must contain one __floating_end")
    return matches[0]

def region_occupied(report, name):
    matches = [
        region["occupied_bytes"] for region in report["regions"]
        if region["name"] == name
    ]
    if len(matches) != 1:
        raise PlannerBudgetError(f"linked report must contain one {name} region")
    return matches[0]


def validate_delta(delta, limit=LIMIT, source="linked profile"):
    if delta < 0:
        raise PlannerBudgetError(f"{source} enabled delta is negative: {delta}")
    if delta > limit:
        raise PlannerBudgetError(
            f"{source} exceeds complete linked planner budget: {delta} > {limit}"
        )
    return limit - delta


def _symbols(nm, elf):
    result = subprocess.run(
        [nm, str(elf)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise PlannerBudgetError(result.stderr.strip() or "nm failed")
    names = {line.split()[-1] for line in result.stdout.splitlines() if line.split()}
    return {name: name in names for name in PLANNER_SYMBOLS}

def _hook_map_evidence(path):
    text = Path(path).read_text(encoding="utf-8")
    evidence = {
        hook: f"/src/{hook}.o" in text or f"src/{hook}.o" in text
        for hook in REPRESENTATIVE_HOOKS
    }
    if not all(evidence.values()):
        raise PlannerBudgetError(
            "linked map omits representative hooks: "
            + ", ".join(name for name, present in evidence.items() if not present)
        )
    return evidence


def build_report(enabled_report, disabled_report, enabled_map, disabled_map,
                 enabled_elf, disabled_elf, nm, limit=LIMIT):
    enabled = _load(enabled_report)
    disabled = _load(disabled_report)
    enabled_end = floating_end(enabled)
    disabled_end = floating_end(disabled)
    delta = enabled_end - disabled_end
    headroom = validate_delta(delta, limit)
    ewram_delta = region_occupied(enabled, "ewram") - region_occupied(disabled, "ewram")
    iwram_delta = region_occupied(enabled, "iwram") - region_occupied(disabled, "iwram")
    if not 0 <= ewram_delta <= 4096 or iwram_delta != 0:
        raise PlannerBudgetError(
            f"linked planner RAM delta is invalid: EWRAM={ewram_delta} IWRAM={iwram_delta}"
        )
    enabled_symbols = _symbols(nm, enabled_elf)
    disabled_symbols = _symbols(nm, disabled_elf)
    if not all(enabled_symbols.values()) or any(disabled_symbols.values()):
        raise PlannerBudgetError("linked planner symbol presence is inconsistent")
    return {
        "schema": "fe8.autoplay-planner-linked-budget.v1",
        "metric": "__floating_end enabled-minus-disabled linked ROM bytes",
        "limit_bytes": limit,
        "delta_bytes": delta,
        "headroom_bytes": headroom,
        "ewram_delta_bytes": ewram_delta,
        "ewram_headroom_bytes": 4096 - ewram_delta,
        "iwram_delta_bytes": iwram_delta,
        "enabled": {
            "report": str(enabled_report), "map": str(enabled_map),
            "elf": str(enabled_elf), "floating_end": enabled_end,
            "symbols": enabled_symbols,
            "representative_hooks": _hook_map_evidence(enabled_map),
        },
        "disabled": {
            "report": str(disabled_report), "map": str(disabled_map),
            "elf": str(disabled_elf), "floating_end": disabled_end,
            "symbols": disabled_symbols,
            "representative_hooks": _hook_map_evidence(disabled_map),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "enabled-report", "disabled-report", "enabled-map", "disabled-map",
        "enabled-elf", "disabled-elf",
    ):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--nm", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=LIMIT)
    args = parser.parse_args(argv)
    report = build_report(
        args.enabled_report, args.disabled_report,
        args.enabled_map, args.disabled_map,
        args.enabled_elf, args.disabled_elf, args.nm, args.limit,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "autoplay planner linked delta: "
        f"{report['delta_bytes']}/{report['limit_bytes']} bytes "
        f"(headroom {report['headroom_bytes']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
