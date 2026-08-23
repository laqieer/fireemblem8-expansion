#!/usr/bin/env python3
"""Run bounded, clean-boot autoplay scenarios over explicit seed lists."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import gba_playtest
from probe_bindings import ElfSymbolResolver


REPORT_FORMAT_VERSION = 1
SPECIFICATION_VERSION = 1
MAX_SEEDS = 256
MAX_JOBS = 16
MAX_METRICS = 32
MAX_GROUPS_PER_METRIC = 64
METRIC_KINDS = frozenset(
    {
        "terminal_reason",
        "emulated_frames",
        "turns",
        "committed_actions",
        "faction_group_counts",
        "event_flag_outcomes",
        "group_deltas",
    }
)
EVENT_KINDS = frozenset({"recruitment", "village", "chest"})
DELTA_KINDS = frozenset({"exp", "item", "resource"})
REQUIRED_METRIC_KINDS = frozenset(
    {
        "terminal_reason",
        "emulated_frames",
        "turns",
        "committed_actions",
        "faction_group_counts",
        "event_flag_outcomes",
    }
)
ROOT = Path(__file__).resolve().parents[2]
BUILD_ROOT = ROOT / "build"


@dataclass(frozen=True)
class MetricProbe:
    binding: str
    address: int
    size: int


@dataclass(frozen=True)
class BatchMetric:
    identifier: str
    kind: str
    definition: dict[str, Any]
    probes: tuple[MetricProbe, ...]


@dataclass(frozen=True)
class BatchSpec:
    name: str
    configuration: str
    profile: str
    fidelity: str
    seed_frame: int
    seed_probe: MetricProbe
    metrics: tuple[BatchMetric, ...]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _object(
    value: Any,
    path: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise gba_playtest.PlaytestError(f"{path} must be an object")
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required - optional)
    if missing:
        raise gba_playtest.PlaytestError(
            f"{path} is missing required field(s): {', '.join(missing)}"
        )
    if unknown:
        raise gba_playtest.PlaytestError(
            f"{path} has unknown field(s): {', '.join(unknown)}"
        )
    return value


def _name(value: Any, path: str) -> str:
    return gba_playtest._expect_name(value, path)


def _metric_probe(
    value: Any,
    path: str,
    resolver: ElfSymbolResolver,
) -> MetricProbe:
    data = _object(value, path, {"address", "size"})
    size = data["size"]
    if not _is_int(size) or size not in (1, 2, 4):
        raise gba_playtest.PlaytestError(f"{path}.size must be integer 1, 2, or 4")
    binding, address = gba_playtest._parse_address(
        data["address"],
        size,
        f"{path}.address",
        resolver,
    )
    if address is None:
        raise AssertionError("the exact ELF resolver always resolves batch probes")
    return MetricProbe(binding, address, size)


def _normalized_probe(probe: MetricProbe) -> dict[str, Any]:
    return {"address": probe.binding, "size": probe.size}


def _parse_count_groups(
    raw_groups: Any,
    path: str,
    resolver: ElfSymbolResolver,
) -> tuple[list[dict[str, Any]], tuple[MetricProbe, ...]]:
    if not isinstance(raw_groups, list) or not raw_groups:
        raise gba_playtest.PlaytestError(f"{path} must be a non-empty array")
    if len(raw_groups) > MAX_GROUPS_PER_METRIC:
        raise gba_playtest.PlaytestError(
            f"{path} has {len(raw_groups)} entries, exceeding {MAX_GROUPS_PER_METRIC}"
        )
    entries = []
    probes: list[MetricProbe] = []
    identities: set[tuple[str, str]] = set()
    for index, raw_group in enumerate(raw_groups):
        group_path = f"{path}[{index}]"
        group = _object(
            raw_group,
            group_path,
            {"faction", "group", "survivors", "casualties"},
        )
        faction = _name(group["faction"], f"{group_path}.faction")
        group_name = _name(group["group"], f"{group_path}.group")
        identity = (faction, group_name)
        if identity in identities:
            raise gba_playtest.PlaytestError(
                f"{group_path} duplicates faction/group {faction!r}/{group_name!r}"
            )
        identities.add(identity)
        survivors = _metric_probe(group["survivors"], f"{group_path}.survivors", resolver)
        casualties = _metric_probe(group["casualties"], f"{group_path}.casualties", resolver)
        entries.append(
            {
                "faction": faction,
                "group": group_name,
                "survivors": _normalized_probe(survivors),
                "casualties": _normalized_probe(casualties),
            }
        )
        probes.extend((survivors, casualties))
    entries.sort(key=lambda entry: (entry["faction"], entry["group"]))
    return entries, tuple(probes)


def _parse_event_outcomes(
    raw_events: Any,
    path: str,
    resolver: ElfSymbolResolver,
) -> tuple[list[dict[str, Any]], tuple[MetricProbe, ...]]:
    if not isinstance(raw_events, list) or not raw_events:
        raise gba_playtest.PlaytestError(f"{path} must be a non-empty array")
    if len(raw_events) > MAX_GROUPS_PER_METRIC:
        raise gba_playtest.PlaytestError(
            f"{path} has {len(raw_events)} entries, exceeding {MAX_GROUPS_PER_METRIC}"
        )
    entries = []
    probes: list[MetricProbe] = []
    identifiers: set[str] = set()
    for index, raw_event in enumerate(raw_events):
        event_path = f"{path}[{index}]"
        event = _object(
            raw_event,
            event_path,
            {"id", "kind", "probe", "success_value"},
        )
        identifier = _name(event["id"], f"{event_path}.id")
        if identifier in identifiers:
            raise gba_playtest.PlaytestError(f"{event_path}.id duplicates {identifier!r}")
        identifiers.add(identifier)
        kind = event["kind"]
        if not isinstance(kind, str) or kind not in EVENT_KINDS:
            raise gba_playtest.PlaytestError(
                f"{event_path}.kind must be one of {', '.join(sorted(EVENT_KINDS))}"
            )
        probe = _metric_probe(event["probe"], f"{event_path}.probe", resolver)
        success_value = event["success_value"]
        maximum = (1 << (probe.size * 8)) - 1
        if not _is_int(success_value) or not 0 <= success_value <= maximum:
            raise gba_playtest.PlaytestError(
                f"{event_path}.success_value must fit the {probe.size}-byte probe"
            )
        entries.append(
            {
                "id": identifier,
                "kind": kind,
                "probe": _normalized_probe(probe),
                "success_value": success_value,
            }
        )
        probes.append(probe)
    entries.sort(key=lambda entry: entry["id"])
    return entries, tuple(probes)


def _parse_group_deltas(
    raw_groups: Any,
    path: str,
    resolver: ElfSymbolResolver,
) -> tuple[list[dict[str, Any]], tuple[MetricProbe, ...]]:
    if not isinstance(raw_groups, list) or not raw_groups:
        raise gba_playtest.PlaytestError(f"{path} must be a non-empty array")
    if len(raw_groups) > MAX_GROUPS_PER_METRIC:
        raise gba_playtest.PlaytestError(
            f"{path} has {len(raw_groups)} entries, exceeding {MAX_GROUPS_PER_METRIC}"
        )
    entries = []
    probes: list[MetricProbe] = []
    identifiers: set[str] = set()
    for index, raw_group in enumerate(raw_groups):
        group_path = f"{path}[{index}]"
        group = _object(raw_group, group_path, {"id", "probe"})
        identifier = _name(group["id"], f"{group_path}.id")
        if identifier in identifiers:
            raise gba_playtest.PlaytestError(f"{group_path}.id duplicates {identifier!r}")
        identifiers.add(identifier)
        probe = _metric_probe(group["probe"], f"{group_path}.probe", resolver)
        entries.append({"id": identifier, "probe": _normalized_probe(probe)})
        probes.append(probe)
    entries.sort(key=lambda entry: entry["id"])
    return entries, tuple(probes)


def _parse_metric(
    raw: Any,
    path: str,
    resolver: ElfSymbolResolver,
) -> BatchMetric:
    data = _object(raw, path, {"id", "kind"}, {"groups", "events", "delta_kind"})
    identifier = _name(data["id"], f"{path}.id")
    kind = data["kind"]
    if not isinstance(kind, str) or kind not in METRIC_KINDS:
        raise gba_playtest.PlaytestError(
            f"{path}.kind must be one of {', '.join(sorted(METRIC_KINDS))}"
        )
    if kind in {"terminal_reason", "emulated_frames", "turns", "committed_actions"}:
        if any(field in data for field in ("groups", "events", "delta_kind")):
            raise gba_playtest.PlaytestError(f"{path}.{kind} does not accept metric payload fields")
        return BatchMetric(identifier, kind, {"id": identifier, "kind": kind}, ())
    if kind == "faction_group_counts":
        if "groups" not in data or "events" in data or "delta_kind" in data:
            raise gba_playtest.PlaytestError(f"{path}.faction_group_counts requires only groups")
        groups, probes = _parse_count_groups(data["groups"], f"{path}.groups", resolver)
        return BatchMetric(
            identifier,
            kind,
            {"id": identifier, "kind": kind, "groups": groups},
            probes,
        )
    if kind == "event_flag_outcomes":
        if "events" not in data or "groups" in data or "delta_kind" in data:
            raise gba_playtest.PlaytestError(f"{path}.event_flag_outcomes requires only events")
        events, probes = _parse_event_outcomes(data["events"], f"{path}.events", resolver)
        return BatchMetric(
            identifier,
            kind,
            {"id": identifier, "kind": kind, "events": events},
            probes,
        )
    if "groups" not in data or "delta_kind" not in data or "events" in data:
        raise gba_playtest.PlaytestError(
            f"{path}.group_deltas requires groups and delta_kind"
        )
    delta_kind = data["delta_kind"]
    if not isinstance(delta_kind, str) or delta_kind not in DELTA_KINDS:
        raise gba_playtest.PlaytestError(
            f"{path}.delta_kind must be one of {', '.join(sorted(DELTA_KINDS))}"
        )
    groups, probes = _parse_group_deltas(data["groups"], f"{path}.groups", resolver)
    return BatchMetric(
        identifier,
        kind,
        {"delta_kind": delta_kind, "groups": groups, "id": identifier, "kind": kind},
        probes,
    )


def load_specification(path: Path, resolver: ElfSymbolResolver) -> BatchSpec:
    data = gba_playtest._read_json(path)
    root = _object(
        data,
        str(path),
        {"schema_version", "name", "configuration", "profile", "seeding", "metrics"},
    )
    if (
        not _is_int(root["schema_version"])
        or root["schema_version"] != SPECIFICATION_VERSION
    ):
        raise gba_playtest.PlaytestError(
            f"{path}.schema_version must be integer {SPECIFICATION_VERSION}"
        )
    name = _name(root["name"], f"{path}.name")
    configuration = _name(root["configuration"], f"{path}.configuration")
    profile_data = _object(root["profile"], f"{path}.profile", {"id", "fidelity"})
    profile = _name(profile_data["id"], f"{path}.profile.id")
    fidelity = profile_data["fidelity"]
    if fidelity != "normal":
        raise gba_playtest.PlaytestError(
            f"{path}.profile.fidelity must be 'normal'; accelerated fidelity is "
            "not part of the #91 batch contract"
        )
    seeding = _object(root["seeding"], f"{path}.seeding", {"address", "size", "frame"})
    seed_probe = _metric_probe(
        {"address": seeding["address"], "size": seeding["size"]},
        f"{path}.seeding",
        resolver,
    )
    if not (
        0x02000000 <= seed_probe.address < 0x02040000
        or 0x03000000 <= seed_probe.address < 0x03008000
    ):
        raise gba_playtest.PlaytestError(
            f"{path}.seeding.address must resolve to writable EWRAM or IWRAM"
        )
    seed_frame = seeding["frame"]
    if not _is_int(seed_frame) or seed_frame < 0:
        raise gba_playtest.PlaytestError(f"{path}.seeding.frame must be a non-negative integer")
    metrics_data = root["metrics"]
    if not isinstance(metrics_data, list) or not metrics_data:
        raise gba_playtest.PlaytestError(f"{path}.metrics must be a non-empty array")
    if len(metrics_data) > MAX_METRICS:
        raise gba_playtest.PlaytestError(
            f"{path}.metrics has {len(metrics_data)} entries, exceeding {MAX_METRICS}"
        )
    metrics = [
        _parse_metric(raw, f"{path}.metrics[{index}]", resolver)
        for index, raw in enumerate(metrics_data)
    ]
    identifiers = [metric.identifier for metric in metrics]
    if len(identifiers) != len(set(identifiers)):
        raise gba_playtest.PlaytestError(f"{path}.metrics contains duplicate metric id(s)")
    kinds = {metric.kind for metric in metrics}
    missing_kinds = sorted(REQUIRED_METRIC_KINDS - kinds)
    if missing_kinds:
        raise gba_playtest.PlaytestError(
            f"{path}.metrics is missing required kind(s): {', '.join(missing_kinds)}"
        )
    supplied_delta_kinds = {
        metric.definition["delta_kind"]
        for metric in metrics
        if metric.kind == "group_deltas"
    }
    missing_delta_kinds = sorted(DELTA_KINDS - supplied_delta_kinds)
    if missing_delta_kinds:
        raise gba_playtest.PlaytestError(
            f"{path}.metrics is missing required group delta kind(s): "
            f"{', '.join(missing_delta_kinds)}"
        )
    return BatchSpec(
        name,
        configuration,
        profile,
        fidelity,
        seed_frame,
        seed_probe,
        tuple(sorted(metrics, key=lambda metric: metric.identifier)),
    )


def parse_seeds(value: str) -> tuple[int, ...]:
    if not value:
        raise gba_playtest.PlaytestError("--seeds must be an explicit non-empty comma list")
    seeds = []
    for index, raw in enumerate(value.split(",")):
        token = raw.strip()
        if not token:
            raise gba_playtest.PlaytestError(
                f"--seeds entry {index + 1} is empty; implicit seeds are not supported"
            )
        try:
            seed = int(token, 0)
        except ValueError as exc:
            raise gba_playtest.PlaytestError(
                f"--seeds entry {index + 1} {token!r} is not an integer"
            ) from exc
        if not 0 <= seed <= 0xFFFFFFFF:
            raise gba_playtest.PlaytestError(
                f"--seeds entry {index + 1} must be from 0 through 4294967295"
            )
        seeds.append(seed)
    if len(seeds) > MAX_SEEDS:
        raise gba_playtest.PlaytestError(
            f"--seeds has {len(seeds)} entries, exceeding the {MAX_SEEDS}-seed bound"
        )
    if len(seeds) != len(set(seeds)):
        raise gba_playtest.PlaytestError("--seeds contains duplicate values")
    return tuple(sorted(seeds))


def _terminal_probe_values(fingerprint: dict[str, Any]) -> dict[tuple[str, int], int]:
    checkpoint = fingerprint["checkpoints"][0]
    return {
        (probe["address"], probe["size"]): int(probe["value"], 16)
        for probe in checkpoint["probes"]
    }


def _metric_value(
    metric: BatchMetric,
    fingerprint: dict[str, Any],
) -> Any:
    terminal = fingerprint["terminal"]
    values = _terminal_probe_values(fingerprint)

    def probe_value(probe: dict[str, Any]) -> int:
        identity = (probe["address"], probe["size"])
        try:
            return values[identity]
        except KeyError as exc:
            raise gba_playtest.PlaytestError(
                f"metric {metric.identifier!r} needs terminal checkpoint probe "
                f"{probe['address']!r}/{probe['size']}, which was not captured"
            ) from exc

    if metric.kind == "terminal_reason":
        return terminal["reason"]
    if metric.kind == "emulated_frames":
        return terminal["frame"] + 1
    if metric.kind == "turns":
        return int(terminal["turn"]["value"], 16)
    if metric.kind == "committed_actions":
        return int(terminal["actions"]["value"], 16)
    if metric.kind == "faction_group_counts":
        return [
            {
                "casualties": probe_value(group["casualties"]),
                "faction": group["faction"],
                "group": group["group"],
                "survivors": probe_value(group["survivors"]),
            }
            for group in metric.definition["groups"]
        ]
    if metric.kind == "event_flag_outcomes":
        return [
            {
                "id": event["id"],
                "kind": event["kind"],
                "succeeded": probe_value(event["probe"]) == event["success_value"],
            }
            for event in metric.definition["events"]
        ]
    return [
        {
            "delta": probe_value(group["probe"]),
            "group": group["id"],
            "kind": metric.definition["delta_kind"],
        }
        for group in metric.definition["groups"]
    ]


def _validate_request(
    scenario: gba_playtest.Scenario,
    specification: BatchSpec,
    *,
    max_frames: int,
    max_turns: int,
    max_actions: int,
) -> None:
    if scenario.run_until is None:
        raise gba_playtest.PlaytestError(
            "batch scenarios must use schema-version-2 bounded run_until execution"
        )
    run_until = scenario.run_until
    expected_bounds = (
        ("--max-frames", max_frames, run_until.max_frames),
        (
            "--max-turns",
            max_turns,
            None if run_until.turn_limit is None else run_until.turn_limit.maximum,
        ),
        (
            "--max-actions",
            max_actions,
            None if run_until.action_limit is None else run_until.action_limit.maximum,
        ),
    )
    for option, requested, scenario_value in expected_bounds:
        if requested < 1:
            raise gba_playtest.PlaytestError(f"{option} must be a positive integer")
        if scenario_value is None:
            raise gba_playtest.PlaytestError(
                f"scenario {scenario.name!r} omits the hard bound required by {option}"
            )
        if requested != scenario_value:
            raise gba_playtest.PlaytestError(
                f"{option}={requested} must exactly match scenario bound {scenario_value}"
            )
    if specification.seed_frame >= run_until.max_frames:
        raise gba_playtest.PlaytestError(
            f"seed injection frame {specification.seed_frame} is outside the "
            f"{run_until.max_frames}-frame scenario bound"
        )
    checkpoint_probes = {
        (probe.binding, probe.size) for probe in scenario.checkpoints[0].probes
    }
    for metric in specification.metrics:
        for probe in metric.probes:
            if (probe.binding, probe.size) not in checkpoint_probes:
                raise gba_playtest.PlaytestError(
                    f"metric {metric.identifier!r} references terminal probe "
                    f"{probe.binding!r}/{probe.size} not present in scenario "
                    f"{scenario.name!r}'s terminal checkpoint"
                )


def _provenance(
    rom: dict[str, Any],
    scenario: gba_playtest.Scenario,
    specification: BatchSpec,
    *,
    max_frames: int,
    max_turns: int,
    max_actions: int,
) -> dict[str, Any]:
    return {
        "bounds": {
            "max_actions": max_actions,
            "max_frames": max_frames,
            "max_turns": max_turns,
        },
        "configuration": specification.configuration,
        "profile": {"fidelity": specification.fidelity, "id": specification.profile},
        "rom": rom,
        "scenario": {
            "name": scenario.name,
            "schema_version": scenario.schema_version,
        },
        "seed_injection": {
            "address": specification.seed_probe.binding,
            "frame": specification.seed_frame,
            "size": specification.seed_probe.size,
        },
        "specification": {
            "name": specification.name,
            "schema_version": SPECIFICATION_VERSION,
        },
    }


def serialize_report(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def run_batch(
    rom: Path,
    scenario: gba_playtest.Scenario,
    specification: BatchSpec,
    seeds: Iterable[int],
    *,
    max_jobs: int,
    max_frames: int,
    max_turns: int,
    max_actions: int,
    work_dir: Path,
) -> dict[str, Any]:
    ordered_seeds = tuple(sorted(seeds))
    if not ordered_seeds or len(ordered_seeds) > MAX_SEEDS:
        raise gba_playtest.PlaytestError(
            f"batch seed count must be from 1 through {MAX_SEEDS}"
        )
    if len(ordered_seeds) != len(set(ordered_seeds)):
        raise gba_playtest.PlaytestError("batch seeds must be unique")
    if not 1 <= max_jobs <= MAX_JOBS:
        raise gba_playtest.PlaytestError(
            f"max_jobs must be from 1 through {MAX_JOBS}"
        )
    _validate_request(
        scenario,
        specification,
        max_frames=max_frames,
        max_turns=max_turns,
        max_actions=max_actions,
    )
    requested_rom = gba_playtest.rom_provenance(rom)
    provenance = _provenance(
        requested_rom,
        scenario,
        specification,
        max_frames=max_frames,
        max_turns=max_turns,
        max_actions=max_actions,
    )

    def run_seed(seed: int) -> dict[str, Any]:
        scheduled_write = gba_playtest.ScheduledWrite(
            specification.seed_frame,
            gba_playtest.Probe(
                specification.seed_probe.binding,
                specification.seed_probe.address,
                specification.seed_probe.size,
                None,
            ),
            seed,
        )
        try:
            captured = gba_playtest.capture(
                rom,
                scenario,
                scheduled_write=scheduled_write,
                work_dir=work_dir,
            )
            if captured["rom"] != requested_rom:
                raise gba_playtest.PlaytestError(
                    "ROM provenance changed during the batch; no mixed-ROM report "
                    "is accepted"
                )
            terminal = captured["terminal"]
            metrics = {
                metric.identifier: _metric_value(metric, captured)
                for metric in specification.metrics
            }
            return {
                "metrics": metrics,
                "rom": captured["rom"],
                "seed": seed,
                "status": "success"
                if terminal["reason"] == "success"
                else "terminal_failure",
                "terminal": terminal,
            }
        except gba_playtest.PlaytestError as exc:
            return {
                "error": str(exc),
                "rom": requested_rom,
                "seed": seed,
                "status": "execution_failure",
            }

    if max_jobs == 1:
        runs = [run_seed(seed) for seed in ordered_seeds]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_jobs) as executor:
            runs = list(executor.map(run_seed, ordered_seeds))
    terminal_counts: dict[str, int] = {}
    for run in runs:
        if "terminal" in run:
            reason = run["terminal"]["reason"]
            terminal_counts[reason] = terminal_counts.get(reason, 0) + 1
    failures = sum(run["status"] != "success" for run in runs)
    distributions: dict[str, dict[str, dict[str, Any]]] = {
        metric.identifier: {} for metric in specification.metrics
    }
    for run in runs:
        for identifier, value in run.get("metrics", {}).items():
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
            bucket = distributions[identifier].setdefault(
                encoded,
                {"count": 0, "value": value},
            )
            bucket["count"] += 1
    return {
        "format_version": REPORT_FORMAT_VERSION,
        "provenance": provenance,
        "runs": runs,
        "summary": {
            "failure_count": failures,
            "metric_distributions": {
                identifier: [
                    distributions[identifier][encoded]
                    for encoded in sorted(distributions[identifier])
                ]
                for identifier in sorted(distributions)
            },
            "run_count": len(runs),
            "success_count": len(runs) - failures,
            "terminal_reasons": dict(sorted(terminal_counts.items())),
        },
    }


def validate_report(data: Any, source: str) -> dict[str, Any]:
    root = _object(data, source, {"format_version", "provenance", "runs", "summary"})
    if (
        not _is_int(root["format_version"])
        or root["format_version"] != REPORT_FORMAT_VERSION
    ):
        raise gba_playtest.PlaytestError(
            f"{source}.format_version must be integer {REPORT_FORMAT_VERSION}"
        )
    if not isinstance(root["provenance"], dict):
        raise gba_playtest.PlaytestError(f"{source}.provenance must be an object")
    if not isinstance(root["runs"], list) or not root["runs"]:
        raise gba_playtest.PlaytestError(f"{source}.runs must be a non-empty array")
    previous_seed = -1
    for index, raw_run in enumerate(root["runs"]):
        path = f"{source}.runs[{index}]"
        run = _object(raw_run, path, {"seed", "status"}, {"error", "metrics", "rom", "terminal"})
        seed = run["seed"]
        if not _is_int(seed) or not 0 <= seed <= 0xFFFFFFFF or seed <= previous_seed:
            raise gba_playtest.PlaytestError(f"{path}.seed must be unique ascending uint32")
        previous_seed = seed
        if run["status"] not in {"success", "terminal_failure", "execution_failure"}:
            raise gba_playtest.PlaytestError(f"{path}.status is unsupported")
        if run["status"] == "execution_failure":
            if set(run) != {"seed", "status", "error", "rom"} or not isinstance(
                run["error"], str
            ):
                raise gba_playtest.PlaytestError(
                    f"{path}.execution_failure must contain only a visible error"
                )
        elif set(run) != {"seed", "status", "rom", "terminal", "metrics"}:
            raise gba_playtest.PlaytestError(
                f"{path}.{run['status']} must contain terminal metrics and ROM provenance"
            )
    summary = _object(
        root["summary"],
        f"{source}.summary",
        {
            "failure_count",
            "metric_distributions",
            "run_count",
            "success_count",
            "terminal_reasons",
        },
    )
    if summary["run_count"] != len(root["runs"]):
        raise gba_playtest.PlaytestError(f"{source}.summary.run_count does not match runs")
    if not isinstance(summary["terminal_reasons"], dict):
        raise gba_playtest.PlaytestError(
            f"{source}.summary.terminal_reasons must be an object"
        )
    if not isinstance(summary["metric_distributions"], dict):
        raise gba_playtest.PlaytestError(
            f"{source}.summary.metric_distributions must be an object"
        )
    return root


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_runs = {run["seed"]: run for run in baseline["runs"]}
    candidate_runs = {run["seed"]: run for run in candidate["runs"]}
    baseline_seeds = set(baseline_runs)
    candidate_seeds = set(candidate_runs)
    changed_runs = []
    for seed in sorted(baseline_seeds & candidate_seeds):
        before = baseline_runs[seed]
        after = candidate_runs[seed]
        changes: dict[str, Any] = {}
        if before["status"] != after["status"]:
            changes["status"] = {"baseline": before["status"], "candidate": after["status"]}
        if before.get("terminal") != after.get("terminal"):
            changes["terminal"] = {
                "baseline": before.get("terminal"),
                "candidate": after.get("terminal"),
            }
        before_metrics = before.get("metrics", {})
        after_metrics = after.get("metrics", {})
        metric_changes = []
        for identifier in sorted(set(before_metrics) | set(after_metrics)):
            if before_metrics.get(identifier) != after_metrics.get(identifier):
                metric_changes.append(
                    {
                        "baseline": before_metrics.get(identifier),
                        "candidate": after_metrics.get(identifier),
                        "id": identifier,
                    }
                )
        if metric_changes:
            changes["metrics"] = metric_changes
        if before.get("error") != after.get("error"):
            changes["error"] = {
                "baseline": before.get("error"),
                "candidate": after.get("error"),
            }
        if changes:
            changed_runs.append({"changes": changes, "seed": seed})
    return {
        "comparison": {
            "added_seeds": sorted(candidate_seeds - baseline_seeds),
            "baseline_provenance": baseline["provenance"],
            "candidate_provenance": candidate["provenance"],
            "changed_runs": changed_runs,
            "removed_seeds": sorted(baseline_seeds - candidate_seeds),
        },
        "format_version": REPORT_FORMAT_VERSION,
        "notice": (
            "This comparison reports observed run and metric changes only; it "
            "does not infer statistical significance, difficulty, or balance."
        ),
    }


def _output_path(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(BUILD_ROOT.resolve())
    except ValueError as exc:
        raise gba_playtest.PlaytestError(
            f"output {path} must be under the ignored build/ directory"
        ) from exc
    if resolved.exists():
        raise gba_playtest.PlaytestError(
            f"output collision: {resolved} already exists and will not be overwritten"
        )
    return resolved


def _reserve_output(path: Path):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise gba_playtest.PlaytestError(
            f"output collision: {path} already exists and will not be overwritten"
        ) from exc
    except OSError as exc:
        raise gba_playtest.PlaytestError(f"cannot reserve output {path}: {exc}") from exc


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    run = subparsers.add_parser("run", help="write one finite deterministic seed report")
    run.add_argument("--rom", required=True, type=Path)
    run.add_argument("--elf", required=True, type=Path)
    run.add_argument("--scenario", required=True, type=Path)
    run.add_argument("--specification", required=True, type=Path)
    run.add_argument("--seeds", required=True)
    run.add_argument("--max-jobs", required=True, type=int)
    run.add_argument("--max-frames", required=True, type=int)
    run.add_argument("--max-turns", required=True, type=int)
    run.add_argument("--max-actions", required=True, type=int)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument(
        "--sram-image",
        type=Path,
        help="rejected: batch reports always clean-boot with no reusable save",
    )
    compare = subparsers.add_parser(
        "compare",
        help="write added/removed seeds and observed terminal or metric changes",
    )
    compare.add_argument("--baseline", required=True, type=Path)
    compare.add_argument("--candidate", required=True, type=Path)
    compare.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _make_parser().parse_args(argv)
    try:
        output = _output_path(args.output)
        if args.mode == "compare":
            baseline = validate_report(gba_playtest._read_json(args.baseline), str(args.baseline))
            candidate = validate_report(
                gba_playtest._read_json(args.candidate), str(args.candidate)
            )
            if args.baseline.resolve() == output or args.candidate.resolve() == output:
                raise gba_playtest.PlaytestError(
                    "comparison output must not replace either input report"
                )
            with _reserve_output(output) as reserved:
                reserved.write(serialize_report(compare_reports(baseline, candidate)))
            print(f"autoplay batch comparison written: {output}")
            return 0
        if args.sram_image is not None:
            raise gba_playtest.PlaytestError(
                "--sram-image is rejected: batch runs never reuse writable save data"
            )
        if not 1 <= args.max_jobs <= MAX_JOBS:
            raise gba_playtest.PlaytestError(
                f"--max-jobs must be from 1 through {MAX_JOBS}"
            )
        seeds = parse_seeds(args.seeds)
        resolver = ElfSymbolResolver(args.elf)
        scenario = gba_playtest.load_scenario(args.scenario, resolver)
        specification = load_specification(args.specification, resolver)
        _validate_request(
            scenario,
            specification,
            max_frames=args.max_frames,
            max_turns=args.max_turns,
            max_actions=args.max_actions,
        )
        with _reserve_output(output) as reserved:
            report = run_batch(
                args.rom,
                scenario,
                specification,
                seeds,
                max_jobs=args.max_jobs,
                max_frames=args.max_frames,
                max_turns=args.max_turns,
                max_actions=args.max_actions,
                work_dir=output.parent,
            )
            reserved.write(serialize_report(report))
        failures = report["summary"]["failure_count"]
        print(
            f"autoplay batch report written: {output} "
            f"(runs={report['summary']['run_count']} failures={failures})"
        )
        return 1 if failures else 0
    except gba_playtest.PlaytestError as exc:
        print(f"autoplay-batch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
