#!/usr/bin/env python3
"""Run bounded, clean-boot autoplay scenarios over explicit seed lists."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import gba_playtest
from probe_bindings import ElfSymbolResolver


REPORT_FORMAT_VERSION = 2
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
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _scenario_probe(probe: gba_playtest.Probe) -> dict[str, Any]:
    return {"address": probe.binding, "size": probe.size}


def _scenario_definition(scenario: gba_playtest.Scenario) -> dict[str, Any]:
    if scenario.run_until is None:
        raise gba_playtest.PlaytestError(
            "batch scenario normalization requires bounded run_until execution"
        )
    run_until = scenario.run_until
    definition: dict[str, Any] = {
        "frames": [
            {
                "end": frame_range.end,
                "keys": [
                    key
                    for key, bit in gba_playtest.KEY_BITS.items()
                    if frame_range.key_mask & bit
                ],
                "start": frame_range.start,
            }
            for frame_range in scenario.inputs
        ],
        "name": scenario.name,
        "run_until": {
            "checkpoint": {},
            "max_frames": run_until.max_frames,
            "terminal_conditions": [
                {
                    "all": [
                        {
                            **_scenario_probe(comparison.probe),
                            "operator": comparison.operator,
                            "value": (
                                f"0x{comparison.value:0{comparison.probe.size * 2}x}"
                            ),
                        }
                        for comparison in condition.comparisons
                    ],
                    "reason": condition.reason,
                }
                for condition in run_until.terminal_conditions
            ],
        },
        "schema_version": scenario.schema_version,
    }
    checkpoint = scenario.checkpoints[0]
    checkpoint_definition: dict[str, Any] = {
        "framebuffer": checkpoint.framebuffer,
        "name": checkpoint.name,
        "probes": [_scenario_probe(probe) for probe in checkpoint.probes],
    }
    if checkpoint.expected_framebuffer_hash is not None:
        checkpoint_definition["expected_framebuffer_hash"] = (
            checkpoint.expected_framebuffer_hash
        )
    if checkpoint.sram_hash:
        checkpoint_definition["sram_hash"] = True
    if checkpoint.expected_sram_hash is not None:
        checkpoint_definition["expected_sram_hash"] = checkpoint.expected_sram_hash
    if checkpoint.sram_hash_exclude_ranges:
        checkpoint_definition["sram_hash_exclude_ranges"] = [
            {"length": length, "offset": offset}
            for offset, length in checkpoint.sram_hash_exclude_ranges
        ]
    if checkpoint.regions:
        checkpoint_definition["regions"] = [
            {
                "height": region.height,
                "name": region.name,
                "width": region.width,
                "x": region.x,
                "y": region.y,
                **(
                    {"expected_hash": region.expected_hash}
                    if region.expected_hash is not None
                    else {}
                ),
            }
            for region in checkpoint.regions
        ]
    if checkpoint.pixel_probes:
        checkpoint_definition["pixel_probes"] = [
            {
                "x": pixel.x,
                "y": pixel.y,
                **({"expected": pixel.expected} if pixel.expected is not None else {}),
            }
            for pixel in checkpoint.pixel_probes
        ]
    definition["run_until"]["checkpoint"] = checkpoint_definition
    if run_until.stall is not None:
        work_expected = run_until.stall.work_expected
        definition["run_until"]["stall"] = {
            "max_unchanged_frames": run_until.stall.max_unchanged_frames,
            "progress": _scenario_probe(run_until.stall.progress),
            "work_expected": {
                **_scenario_probe(work_expected.probe),
                "operator": work_expected.operator,
                "value": (
                    f"0x{work_expected.value:0{work_expected.probe.size * 2}x}"
                ),
            },
        }
    for field, counter in (
        ("turn_limit", run_until.turn_limit),
        ("action_limit", run_until.action_limit),
    ):
        if counter is not None:
            definition["run_until"][field] = {
                **_scenario_probe(counter.probe),
                "maximum": counter.maximum,
            }
    return definition


def _specification_definition(specification: BatchSpec) -> dict[str, Any]:
    return {
        "configuration": specification.configuration,
        "metrics": [metric.definition for metric in specification.metrics],
        "name": specification.name,
        "profile": {
            "fidelity": specification.fidelity,
            "id": specification.profile,
        },
        "schema_version": SPECIFICATION_VERSION,
        "seeding": {
            **_normalized_probe(specification.seed_probe),
            "frame": specification.seed_frame,
            "resolved_address": specification.seed_probe.address,
        },
    }


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


def _validate_required_metric_contract(
    definitions: Iterable[dict[str, Any]],
    path: str,
) -> None:
    rows = tuple(definitions)
    identifiers = [row["id"] for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise gba_playtest.PlaytestError(f"{path} contains duplicate metric id(s)")
    kinds = [row["kind"] for row in rows]
    for kind in sorted(REQUIRED_METRIC_KINDS):
        count = kinds.count(kind)
        if count > 1:
            raise gba_playtest.PlaytestError(
                f"{path} must contain exactly one required metric kind {kind!r}; "
                f"found {count}"
            )
    for kind in sorted(REQUIRED_METRIC_KINDS):
        if kind not in kinds:
            raise gba_playtest.PlaytestError(
                f"{path} must contain exactly one required metric kind {kind!r}; "
                "found 0"
            )
    delta_kinds = [
        row["delta_kind"] for row in rows if row["kind"] == "group_deltas"
    ]
    for delta_kind in sorted(DELTA_KINDS):
        count = delta_kinds.count(delta_kind)
        if count > 1:
            raise gba_playtest.PlaytestError(
                f"{path} must contain exactly one required group delta kind "
                f"{delta_kind!r}; found {count}"
            )
    for delta_kind in sorted(DELTA_KINDS):
        if delta_kind not in delta_kinds:
            raise gba_playtest.PlaytestError(
                f"{path} must contain exactly one required group delta kind "
                f"{delta_kind!r}; found 0"
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
    _validate_required_metric_contract(
        (metric.definition for metric in metrics),
        f"{path}.metrics",
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


def _validate_seed_values(
    seeds: Iterable[Any],
    seed_probe: MetricProbe,
) -> tuple[int, ...]:
    values = tuple(seeds)
    if not values or len(values) > MAX_SEEDS:
        raise gba_playtest.PlaytestError(
            f"batch seed count must be from 1 through {MAX_SEEDS}"
        )
    maximum = (1 << (seed_probe.size * 8)) - 1
    for index, seed in enumerate(values):
        if not _is_int(seed) or not 0 <= seed <= maximum:
            raise gba_playtest.PlaytestError(
                f"batch seed {index + 1} must be an integer from 0 through "
                f"{maximum} for the {seed_probe.size}-byte seed probe"
            )
    if len(values) != len(set(values)):
        raise gba_playtest.PlaytestError("batch seeds must be unique")
    return tuple(sorted(values))


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
    if scenario.schema_version != gba_playtest.RUN_UNTIL_SCENARIO_SCHEMA_VERSION:
        raise gba_playtest.PlaytestError(
            "batch scenarios must use schema_version exactly 2"
        )
    if scenario.execution_profile is not None:
        raise gba_playtest.PlaytestError(
            "batch scenarios must omit execution_profile; the #91 collector "
            "accepts normal fidelity only"
        )
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
    scenario_definition = _scenario_definition(scenario)
    specification_definition = _specification_definition(specification)
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
            "definition": scenario_definition,
            "definition_sha256": _canonical_sha256(scenario_definition),
            "name": scenario.name,
            "schema_version": scenario.schema_version,
        },
        "seed_injection": {
            "address": specification.seed_probe.binding,
            "frame": specification.seed_frame,
            "resolved_address": specification.seed_probe.address,
            "size": specification.seed_probe.size,
        },
        "specification": {
            "definition": specification_definition,
            "definition_sha256": _canonical_sha256(specification_definition),
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
    ordered_seeds = _validate_seed_values(seeds, specification.seed_probe)
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

    def run_seed(seed: int, backend: Path) -> dict[str, Any]:
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
                backend_path=backend,
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

    try:
        with tempfile.TemporaryDirectory(
            prefix="autoplay-batch-backend-",
            dir=work_dir,
        ) as backend_directory:
            backend = Path(backend_directory) / "gba-playtest-backend"
            gba_playtest.build_backend(backend)
            if max_jobs == 1:
                runs = [run_seed(seed, backend) for seed in ordered_seeds]
            else:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=max_jobs
                ) as executor:
                    runs = list(
                        executor.map(
                            lambda seed: run_seed(seed, backend),
                            ordered_seeds,
                        )
                    )
    except OSError as exc:
        raise gba_playtest.PlaytestError(
            f"cannot create shared batch backend workspace under {work_dir}: {exc}"
        ) from exc
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


def _validate_digest(value: Any, path: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise gba_playtest.PlaytestError(
            f"{path} must be 64 lowercase hexadecimal SHA-256 digits"
        )
    return value


def _validate_rom(value: Any, path: str) -> dict[str, Any]:
    rom = _object(value, path, {"game_code", "sha1", "size", "title"})
    if not isinstance(rom["sha1"], str) or re.fullmatch(r"[0-9a-f]{40}", rom["sha1"]) is None:
        raise gba_playtest.PlaytestError(f"{path}.sha1 must be 40 lowercase hex digits")
    if not _is_int(rom["size"]) or rom["size"] <= 0:
        raise gba_playtest.PlaytestError(f"{path}.size must be a positive integer")
    for field, limit in (("title", 48), ("game_code", 16)):
        if not isinstance(rom[field], str) or len(rom[field]) > limit:
            raise gba_playtest.PlaytestError(
                f"{path}.{field} must be a string no longer than {limit} characters"
            )
    return rom


def _validate_probe_definition(value: Any, path: str) -> dict[str, Any]:
    probe = _object(value, path, {"address", "size"})
    size = probe["size"]
    if not _is_int(size) or size not in (1, 2, 4):
        raise gba_playtest.PlaytestError(f"{path}.size must be integer 1, 2, or 4")
    gba_playtest._parse_address(probe["address"], size, f"{path}.address")
    return probe


def _validate_seed_binding(value: Any, path: str) -> dict[str, Any]:
    seed = _object(
        value,
        path,
        {"address", "frame", "resolved_address", "size"},
    )
    probe = _validate_probe_definition(
        {"address": seed["address"], "size": seed["size"]},
        path,
    )
    resolved_address = seed["resolved_address"]
    if not _is_int(resolved_address):
        raise gba_playtest.PlaytestError(
            f"{path}.resolved_address must be an integer"
        )
    if resolved_address % probe["size"]:
        raise gba_playtest.PlaytestError(
            f"{path}.resolved_address must be aligned to size {probe['size']}"
        )
    if not any(
        start <= resolved_address
        and resolved_address + probe["size"] <= end
        for start, end in gba_playtest.WRITABLE_WORK_RAM_RANGES
    ):
        raise gba_playtest.PlaytestError(
            f"{path}.resolved_address write range must fit entirely within "
            "writable EWRAM or IWRAM"
        )
    _, literal_address = gba_playtest._parse_address(
        probe["address"],
        probe["size"],
        f"{path}.address",
    )
    if literal_address is not None and literal_address != resolved_address:
        raise gba_playtest.PlaytestError(
            f"{path}.resolved_address does not match literal address"
        )
    if not _is_int(seed["frame"]) or seed["frame"] < 0:
        raise gba_playtest.PlaytestError(
            f"{path}.frame must be a non-negative integer"
        )
    return seed


def _validate_metric_definition(value: Any, path: str) -> dict[str, Any]:
    metric = _object(value, path, {"id", "kind"}, {"delta_kind", "events", "groups"})
    _name(metric["id"], f"{path}.id")
    kind = metric["kind"]
    if not isinstance(kind, str) or kind not in METRIC_KINDS:
        raise gba_playtest.PlaytestError(
            f"{path}.kind must be one of {', '.join(sorted(METRIC_KINDS))}"
        )
    payload_fields = set(metric) - {"id", "kind"}
    if kind in {"terminal_reason", "emulated_frames", "turns", "committed_actions"}:
        if payload_fields:
            raise gba_playtest.PlaytestError(f"{path}.{kind} must not have payload fields")
        return metric
    if kind == "faction_group_counts":
        if payload_fields != {"groups"}:
            raise gba_playtest.PlaytestError(f"{path}.{kind} requires only groups")
        groups = metric["groups"]
        if not isinstance(groups, list) or not groups:
            raise gba_playtest.PlaytestError(f"{path}.groups must be a non-empty array")
        if len(groups) > MAX_GROUPS_PER_METRIC:
            raise gba_playtest.PlaytestError(
                f"{path}.groups has {len(groups)} entries, exceeding "
                f"{MAX_GROUPS_PER_METRIC}"
            )
        previous: tuple[str, str] | None = None
        for index, value_group in enumerate(groups):
            group_path = f"{path}.groups[{index}]"
            group = _object(
                value_group,
                group_path,
                {"casualties", "faction", "group", "survivors"},
            )
            identity = (
                _name(group["faction"], f"{group_path}.faction"),
                _name(group["group"], f"{group_path}.group"),
            )
            if previous is not None and identity <= previous:
                raise gba_playtest.PlaytestError(
                    f"{group_path} must be sorted and unique by faction/group"
                )
            previous = identity
            _validate_probe_definition(group["survivors"], f"{group_path}.survivors")
            _validate_probe_definition(group["casualties"], f"{group_path}.casualties")
        return metric
    if kind == "event_flag_outcomes":
        if payload_fields != {"events"}:
            raise gba_playtest.PlaytestError(f"{path}.{kind} requires only events")
        events = metric["events"]
        if not isinstance(events, list) or not events:
            raise gba_playtest.PlaytestError(f"{path}.events must be a non-empty array")
        if len(events) > MAX_GROUPS_PER_METRIC:
            raise gba_playtest.PlaytestError(
                f"{path}.events has {len(events)} entries, exceeding "
                f"{MAX_GROUPS_PER_METRIC}"
            )
        previous_id: str | None = None
        for index, value_event in enumerate(events):
            event_path = f"{path}.events[{index}]"
            event = _object(
                value_event,
                event_path,
                {"id", "kind", "probe", "success_value"},
            )
            identifier = _name(event["id"], f"{event_path}.id")
            if previous_id is not None and identifier <= previous_id:
                raise gba_playtest.PlaytestError(
                    f"{event_path}.id must be sorted and unique"
                )
            previous_id = identifier
            if not isinstance(event["kind"], str) or event["kind"] not in EVENT_KINDS:
                raise gba_playtest.PlaytestError(
                    f"{event_path}.kind must be one of {', '.join(sorted(EVENT_KINDS))}"
                )
            probe = _validate_probe_definition(event["probe"], f"{event_path}.probe")
            maximum = (1 << (probe["size"] * 8)) - 1
            if not _is_int(event["success_value"]) or not 0 <= event["success_value"] <= maximum:
                raise gba_playtest.PlaytestError(
                    f"{event_path}.success_value must fit the declared probe"
                )
        return metric
    if payload_fields != {"delta_kind", "groups"}:
        raise gba_playtest.PlaytestError(
            f"{path}.group_deltas requires only delta_kind and groups"
        )
    if not isinstance(metric["delta_kind"], str) or metric["delta_kind"] not in DELTA_KINDS:
        raise gba_playtest.PlaytestError(
            f"{path}.delta_kind must be one of {', '.join(sorted(DELTA_KINDS))}"
        )
    groups = metric["groups"]
    if not isinstance(groups, list) or not groups:
        raise gba_playtest.PlaytestError(f"{path}.groups must be a non-empty array")
    if len(groups) > MAX_GROUPS_PER_METRIC:
        raise gba_playtest.PlaytestError(
            f"{path}.groups has {len(groups)} entries, exceeding "
            f"{MAX_GROUPS_PER_METRIC}"
        )
    previous_id = None
    for index, value_group in enumerate(groups):
        group_path = f"{path}.groups[{index}]"
        group = _object(value_group, group_path, {"id", "probe"})
        identifier = _name(group["id"], f"{group_path}.id")
        if previous_id is not None and identifier <= previous_id:
            raise gba_playtest.PlaytestError(
                f"{group_path}.id must be sorted and unique"
            )
        previous_id = identifier
        _validate_probe_definition(group["probe"], f"{group_path}.probe")
    return metric


def _validate_provenance(
    value: Any,
    path: str,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    gba_playtest.Scenario,
]:
    provenance = _object(
        value,
        path,
        {
            "bounds",
            "configuration",
            "profile",
            "rom",
            "scenario",
            "seed_injection",
            "specification",
        },
    )
    bounds = _object(
        provenance["bounds"],
        f"{path}.bounds",
        {"max_actions", "max_frames", "max_turns"},
    )
    for field in ("max_actions", "max_frames", "max_turns"):
        if not _is_int(bounds[field]) or bounds[field] < 1:
            raise gba_playtest.PlaytestError(
                f"{path}.bounds.{field} must be a positive integer"
            )
    _name(provenance["configuration"], f"{path}.configuration")
    profile = _object(provenance["profile"], f"{path}.profile", {"fidelity", "id"})
    _name(profile["id"], f"{path}.profile.id")
    if profile["fidelity"] != "normal":
        raise gba_playtest.PlaytestError(f"{path}.profile.fidelity must be 'normal'")
    _validate_rom(provenance["rom"], f"{path}.rom")
    scenario = _object(
        provenance["scenario"],
        f"{path}.scenario",
        {"definition", "definition_sha256", "name", "schema_version"},
    )
    _name(scenario["name"], f"{path}.scenario.name")
    if (
        not _is_int(scenario["schema_version"])
        or scenario["schema_version"] != gba_playtest.RUN_UNTIL_SCENARIO_SCHEMA_VERSION
    ):
        raise gba_playtest.PlaytestError(f"{path}.scenario.schema_version must be integer 2")
    scenario_digest = _validate_digest(
        scenario["definition_sha256"],
        f"{path}.scenario.definition_sha256",
    )
    parsed_scenario = gba_playtest.parse_scenario_data(
        scenario["definition"],
        f"{path}.scenario.definition",
    )
    if (
        parsed_scenario.schema_version
        != gba_playtest.RUN_UNTIL_SCENARIO_SCHEMA_VERSION
        or parsed_scenario.execution_profile is not None
    ):
        raise gba_playtest.PlaytestError(
            f"{path}.scenario.definition must describe normal schema-version-2 execution"
        )
    normalized_scenario = _scenario_definition(parsed_scenario)
    if normalized_scenario != scenario["definition"]:
        raise gba_playtest.PlaytestError(
            f"{path}.scenario.definition must be canonical normalized scenario JSON"
        )
    if parsed_scenario.name != scenario["name"]:
        raise gba_playtest.PlaytestError(
            f"{path}.scenario.name does not match its definition"
        )
    if _canonical_sha256(normalized_scenario) != scenario_digest:
        raise gba_playtest.PlaytestError(
            f"{path}.scenario.definition_sha256 does not match its definition"
        )
    run_until = parsed_scenario.run_until
    assert run_until is not None
    if run_until.turn_limit is None:
        raise gba_playtest.PlaytestError(
            f"{path}.scenario.definition.run_until.turn_limit is required"
        )
    if run_until.action_limit is None:
        raise gba_playtest.PlaytestError(
            f"{path}.scenario.definition.run_until.action_limit is required"
        )
    expected_bounds = {
        "max_actions": run_until.action_limit.maximum,
        "max_frames": run_until.max_frames,
        "max_turns": run_until.turn_limit.maximum,
    }
    if bounds != expected_bounds:
        raise gba_playtest.PlaytestError(
            f"{path}.bounds must exactly match canonical scenario run_until "
            "frame/turn/action limits"
        )
    seed = _validate_seed_binding(
        provenance["seed_injection"],
        f"{path}.seed_injection",
    )
    if seed["frame"] >= run_until.max_frames:
        raise gba_playtest.PlaytestError(
            f"{path}.seed_injection.frame must be below canonical "
            f"scenario max_frames {run_until.max_frames}"
        )
    specification = _object(
        provenance["specification"],
        f"{path}.specification",
        {"definition", "definition_sha256", "name", "schema_version"},
    )
    _name(specification["name"], f"{path}.specification.name")
    if (
        not _is_int(specification["schema_version"])
        or specification["schema_version"] != SPECIFICATION_VERSION
    ):
        raise gba_playtest.PlaytestError(
            f"{path}.specification.schema_version must be integer {SPECIFICATION_VERSION}"
        )
    specification_digest = _validate_digest(
        specification["definition_sha256"],
        f"{path}.specification.definition_sha256",
    )
    definition = _object(
        specification["definition"],
        f"{path}.specification.definition",
        {"configuration", "metrics", "name", "profile", "schema_version", "seeding"},
    )
    _name(definition["name"], f"{path}.specification.definition.name")
    if (
        not _is_int(definition["schema_version"])
        or definition["schema_version"] != SPECIFICATION_VERSION
    ):
        raise gba_playtest.PlaytestError(
            f"{path}.specification.definition.schema_version must be integer "
            f"{SPECIFICATION_VERSION}"
        )
    _name(
        definition["configuration"],
        f"{path}.specification.definition.configuration",
    )
    definition_profile = _object(
        definition["profile"],
        f"{path}.specification.definition.profile",
        {"fidelity", "id"},
    )
    _name(definition_profile["id"], f"{path}.specification.definition.profile.id")
    if definition_profile["fidelity"] != "normal":
        raise gba_playtest.PlaytestError(
            f"{path}.specification.definition.profile.fidelity must be 'normal'"
        )
    definition_seed = _validate_seed_binding(
        definition["seeding"],
        f"{path}.specification.definition.seeding",
    )
    if definition["name"] != specification["name"]:
        raise gba_playtest.PlaytestError(
            f"{path}.specification.name does not match its definition"
        )
    if definition["schema_version"] != specification["schema_version"]:
        raise gba_playtest.PlaytestError(
            f"{path}.specification.schema_version does not match its definition"
        )
    if definition["configuration"] != provenance["configuration"]:
        raise gba_playtest.PlaytestError(
            f"{path}.specification.definition.configuration does not match provenance"
        )
    if definition["profile"] != provenance["profile"]:
        raise gba_playtest.PlaytestError(
            f"{path}.specification.definition.profile does not match provenance"
        )
    if definition["seeding"] != provenance["seed_injection"]:
        raise gba_playtest.PlaytestError(
            f"{path}.specification.definition.seeding does not match provenance"
        )
    if _canonical_sha256(definition) != specification_digest:
        raise gba_playtest.PlaytestError(
            f"{path}.specification.definition_sha256 does not match its definition"
        )
    metrics = definition["metrics"]
    if not isinstance(metrics, list) or not metrics:
        raise gba_playtest.PlaytestError(
            f"{path}.specification.definition.metrics must be a non-empty array"
        )
    if len(metrics) > MAX_METRICS:
        raise gba_playtest.PlaytestError(
            f"{path}.specification.definition.metrics has {len(metrics)} entries, "
            f"exceeding {MAX_METRICS}"
        )
    metric_definitions: dict[str, dict[str, Any]] = {}
    previous_id: str | None = None
    for index, raw_metric in enumerate(metrics):
        metric_path = f"{path}.specification.definition.metrics[{index}]"
        metric = _validate_metric_definition(raw_metric, metric_path)
        identifier = metric["id"]
        if previous_id is not None and identifier <= previous_id:
            raise gba_playtest.PlaytestError(
                f"{metric_path}.id must be sorted and unique"
            )
        previous_id = identifier
        metric_definitions[identifier] = metric
    _validate_required_metric_contract(
        metric_definitions.values(),
        f"{path}.specification.definition.metrics",
    )
    return provenance, metric_definitions, parsed_scenario


def _validate_counter(
    value: Any,
    path: str,
    expected: gba_playtest.Probe,
    maximum: int,
) -> int:
    if value is None:
        raise gba_playtest.PlaytestError(
            f"{path} must be non-null for the declared scenario counter"
        )
    counter = _object(value, path, {"address", "size", "value"})
    probe = _validate_probe_definition(
        {"address": counter["address"], "size": counter["size"]},
        path,
    )
    binding, _ = gba_playtest._parse_address(
        probe["address"],
        probe["size"],
        f"{path}.address",
    )
    if (binding, probe["size"]) != (expected.binding, expected.size):
        raise gba_playtest.PlaytestError(
            f"{path} address/size must match the canonical scenario counter "
            f"{expected.binding!r}/{expected.size}"
        )
    pattern = re.compile(rf"^0x[0-9a-f]{{{probe['size'] * 2}}}$")
    if not isinstance(counter["value"], str) or pattern.fullmatch(counter["value"]) is None:
        raise gba_playtest.PlaytestError(
            f"{path}.value must be lowercase hexadecimal matching size"
        )
    parsed = int(counter["value"], 16)
    if parsed > maximum:
        raise gba_playtest.PlaytestError(
            f"{path}.value {parsed} exceeds declared bound {maximum}"
        )
    return parsed


def _validate_terminal(
    value: Any,
    path: str,
    scenario: gba_playtest.Scenario,
) -> tuple[dict[str, Any], int, int]:
    terminal = _object(value, path, {"actions", "frame", "reason", "turn"})
    if not isinstance(terminal["reason"], str) or terminal["reason"] not in gba_playtest.TERMINAL_REASONS:
        raise gba_playtest.PlaytestError(
            f"{path}.reason must be one of {', '.join(gba_playtest.TERMINAL_REASONS)}"
        )
    run_until = scenario.run_until
    assert run_until is not None
    assert run_until.turn_limit is not None
    assert run_until.action_limit is not None
    if (
        not _is_int(terminal["frame"])
        or terminal["frame"] < 0
        or terminal["frame"] >= run_until.max_frames
    ):
        raise gba_playtest.PlaytestError(
            f"{path}.frame must be an integer from 0 through "
            f"{run_until.max_frames - 1}"
        )
    turn = _validate_counter(
        terminal["turn"],
        f"{path}.turn",
        run_until.turn_limit.probe,
        run_until.turn_limit.maximum,
    )
    actions = _validate_counter(
        terminal["actions"],
        f"{path}.actions",
        run_until.action_limit.probe,
        run_until.action_limit.maximum,
    )
    return terminal, turn, actions


def _validate_nonnegative_int(value: Any, path: str) -> None:
    if not _is_int(value) or value < 0:
        raise gba_playtest.PlaytestError(f"{path} must be a non-negative integer")


def _validate_probe_width_value(
    value: Any,
    probe: dict[str, Any],
    path: str,
) -> None:
    maximum = (1 << (probe["size"] * 8)) - 1
    if not _is_int(value) or not 0 <= value <= maximum:
        raise gba_playtest.PlaytestError(
            f"{path} must be an integer from 0 through {maximum} for the "
            f"declared {probe['size']}-byte probe"
        )


def _validate_metric_value(value: Any, definition: dict[str, Any], path: str) -> None:
    kind = definition["kind"]
    if kind == "terminal_reason":
        if not isinstance(value, str) or value not in gba_playtest.TERMINAL_REASONS:
            raise gba_playtest.PlaytestError(f"{path} must be a supported terminal reason")
        return
    if kind in {"emulated_frames", "turns", "committed_actions"}:
        _validate_nonnegative_int(value, path)
        return
    if not isinstance(value, list):
        raise gba_playtest.PlaytestError(f"{path} must be an array for metric kind {kind}")
    if kind == "faction_group_counts":
        expected = definition["groups"]
        if len(value) != len(expected):
            raise gba_playtest.PlaytestError(
                f"{path} must contain {len(expected)} faction/group record(s)"
            )
        for index, (raw_entry, expected_entry) in enumerate(zip(value, expected)):
            entry_path = f"{path}[{index}]"
            entry = _object(
                raw_entry,
                entry_path,
                {"casualties", "faction", "group", "survivors"},
            )
            if (entry["faction"], entry["group"]) != (
                expected_entry["faction"],
                expected_entry["group"],
            ):
                raise gba_playtest.PlaytestError(
                    f"{entry_path} does not match its declared faction/group"
                )
            _validate_probe_width_value(
                entry["survivors"],
                expected_entry["survivors"],
                f"{entry_path}.survivors",
            )
            _validate_probe_width_value(
                entry["casualties"],
                expected_entry["casualties"],
                f"{entry_path}.casualties",
            )
        return
    if kind == "event_flag_outcomes":
        expected = definition["events"]
        if len(value) != len(expected):
            raise gba_playtest.PlaytestError(
                f"{path} must contain {len(expected)} event outcome(s)"
            )
        for index, (raw_entry, expected_entry) in enumerate(zip(value, expected)):
            entry_path = f"{path}[{index}]"
            entry = _object(raw_entry, entry_path, {"id", "kind", "succeeded"})
            if (entry["id"], entry["kind"]) != (
                expected_entry["id"],
                expected_entry["kind"],
            ):
                raise gba_playtest.PlaytestError(
                    f"{entry_path} does not match its declared event"
                )
            if not isinstance(entry["succeeded"], bool):
                raise gba_playtest.PlaytestError(f"{entry_path}.succeeded must be boolean")
        return
    expected = definition["groups"]
    if len(value) != len(expected):
        raise gba_playtest.PlaytestError(
            f"{path} must contain {len(expected)} group delta(s)"
        )
    for index, (raw_entry, expected_entry) in enumerate(zip(value, expected)):
        entry_path = f"{path}[{index}]"
        entry = _object(raw_entry, entry_path, {"delta", "group", "kind"})
        if (entry["group"], entry["kind"]) != (
            expected_entry["id"],
            definition["delta_kind"],
        ):
            raise gba_playtest.PlaytestError(
                f"{entry_path} does not match its declared group delta"
            )
        _validate_probe_width_value(
            entry["delta"],
            expected_entry["probe"],
            f"{entry_path}.delta",
        )


def _metric_distributions(
    runs: list[dict[str, Any]],
    metric_definitions: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    distributions: dict[str, dict[str, dict[str, Any]]] = {
        identifier: {} for identifier in metric_definitions
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
        identifier: [
            distributions[identifier][encoded]
            for encoded in sorted(distributions[identifier])
        ]
        for identifier in sorted(distributions)
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
    provenance, metric_definitions, scenario = _validate_provenance(
        root["provenance"],
        f"{source}.provenance",
    )
    if not isinstance(root["runs"], list) or not root["runs"]:
        raise gba_playtest.PlaytestError(f"{source}.runs must be a non-empty array")
    if len(root["runs"]) > MAX_SEEDS:
        raise gba_playtest.PlaytestError(
            f"{source}.runs has {len(root['runs'])} entries, exceeding the "
            f"{MAX_SEEDS}-run limit"
        )
    seed_size = provenance["seed_injection"]["size"]
    maximum_seed = (1 << (seed_size * 8)) - 1
    previous_seed = -1
    for index, raw_run in enumerate(root["runs"]):
        path = f"{source}.runs[{index}]"
        run = _object(raw_run, path, {"seed", "status"}, {"error", "metrics", "rom", "terminal"})
        seed = run["seed"]
        if (
            not _is_int(seed)
            or not 0 <= seed <= maximum_seed
            or seed <= previous_seed
        ):
            raise gba_playtest.PlaytestError(
                f"{path}.seed must be unique ascending and fit the "
                f"{seed_size}-byte seed probe"
            )
        previous_seed = seed
        if not isinstance(run["status"], str) or run["status"] not in {
            "success",
            "terminal_failure",
            "execution_failure",
        }:
            raise gba_playtest.PlaytestError(f"{path}.status is unsupported")
        if run["status"] == "execution_failure":
            if set(run) != {"seed", "status", "error", "rom"}:
                raise gba_playtest.PlaytestError(
                    f"{path}.execution_failure must contain only error and ROM provenance"
                )
            if not isinstance(run["error"], str) or not run["error"]:
                raise gba_playtest.PlaytestError(
                    f"{path}.error must be a non-empty string"
                )
        elif set(run) != {"seed", "status", "rom", "terminal", "metrics"}:
            raise gba_playtest.PlaytestError(
                f"{path}.{run['status']} must contain terminal metrics and ROM provenance"
            )
        rom = _validate_rom(run["rom"], f"{path}.rom")
        if rom != provenance["rom"]:
            raise gba_playtest.PlaytestError(
                f"{path}.rom must exactly match report provenance"
            )
        if run["status"] == "execution_failure":
            continue
        terminal, turn_value, action_value = _validate_terminal(
            run["terminal"],
            f"{path}.terminal",
            scenario,
        )
        if (run["status"] == "success") != (terminal["reason"] == "success"):
            raise gba_playtest.PlaytestError(
                f"{path}.status does not match terminal reason {terminal['reason']!r}"
            )
        metrics = run["metrics"]
        if not isinstance(metrics, dict):
            raise gba_playtest.PlaytestError(f"{path}.metrics must be an object")
        if set(metrics) != set(metric_definitions):
            raise gba_playtest.PlaytestError(
                f"{path}.metrics must contain exactly the declared metric ids"
            )
        for identifier, definition in metric_definitions.items():
            _validate_metric_value(
                metrics[identifier],
                definition,
                f"{path}.metrics.{identifier}",
            )
            kind = definition["kind"]
            if kind == "terminal_reason" and metrics[identifier] != terminal["reason"]:
                raise gba_playtest.PlaytestError(
                    f"{path}.metrics.{identifier} does not match terminal.reason"
                )
            if kind == "emulated_frames" and metrics[identifier] != terminal["frame"] + 1:
                raise gba_playtest.PlaytestError(
                    f"{path}.metrics.{identifier} does not match terminal.frame"
                )
            if kind in {"turns", "committed_actions"}:
                counter_name = "turn" if kind == "turns" else "actions"
                expected_value = turn_value if kind == "turns" else action_value
                if metrics[identifier] != expected_value:
                    raise gba_playtest.PlaytestError(
                        f"{path}.metrics.{identifier} does not match "
                        f"terminal.{counter_name}"
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
    for field in ("failure_count", "run_count", "success_count"):
        _validate_nonnegative_int(summary[field], f"{source}.summary.{field}")
    run_count = len(root["runs"])
    success_count = sum(run["status"] == "success" for run in root["runs"])
    if summary["run_count"] != run_count:
        raise gba_playtest.PlaytestError(f"{source}.summary.run_count does not match runs")
    if summary["success_count"] != success_count:
        raise gba_playtest.PlaytestError(
            f"{source}.summary.success_count does not match runs"
        )
    if summary["failure_count"] != run_count - success_count:
        raise gba_playtest.PlaytestError(
            f"{source}.summary.failure_count does not match runs"
        )
    terminal_counts: dict[str, int] = {}
    for run in root["runs"]:
        if "terminal" in run:
            reason = run["terminal"]["reason"]
            terminal_counts[reason] = terminal_counts.get(reason, 0) + 1
    if not isinstance(summary["terminal_reasons"], dict):
        raise gba_playtest.PlaytestError(
            f"{source}.summary.terminal_reasons must be an object"
        )
    for reason, count in summary["terminal_reasons"].items():
        if not isinstance(reason, str) or reason not in gba_playtest.TERMINAL_REASONS:
            raise gba_playtest.PlaytestError(
                f"{source}.summary.terminal_reasons has unsupported reason {reason!r}"
            )
        if not _is_int(count) or count < 1:
            raise gba_playtest.PlaytestError(
                f"{source}.summary.terminal_reasons.{reason} must be a positive integer"
            )
    if summary["terminal_reasons"] != dict(sorted(terminal_counts.items())):
        raise gba_playtest.PlaytestError(
            f"{source}.summary.terminal_reasons does not match runs"
        )
    distributions = summary["metric_distributions"]
    if not isinstance(distributions, dict):
        raise gba_playtest.PlaytestError(
            f"{source}.summary.metric_distributions must be an object"
        )
    if set(distributions) != set(metric_definitions):
        raise gba_playtest.PlaytestError(
            f"{source}.summary.metric_distributions must contain exactly the "
            "declared metric ids"
        )
    for identifier, definition in metric_definitions.items():
        buckets = distributions[identifier]
        if not isinstance(buckets, list):
            raise gba_playtest.PlaytestError(
                f"{source}.summary.metric_distributions.{identifier} must be an array"
            )
        previous_encoded: str | None = None
        for index, raw_bucket in enumerate(buckets):
            bucket_path = (
                f"{source}.summary.metric_distributions.{identifier}[{index}]"
            )
            bucket = _object(raw_bucket, bucket_path, {"count", "value"})
            if not _is_int(bucket["count"]) or bucket["count"] < 1:
                raise gba_playtest.PlaytestError(
                    f"{bucket_path}.count must be a positive integer"
                )
            _validate_metric_value(bucket["value"], definition, f"{bucket_path}.value")
            encoded = json.dumps(
                bucket["value"],
                sort_keys=True,
                separators=(",", ":"),
            )
            if previous_encoded is not None and encoded <= previous_encoded:
                raise gba_playtest.PlaytestError(
                    f"{bucket_path}.value must be sorted and unique"
                )
            previous_encoded = encoded
    expected_distributions = _metric_distributions(root["runs"], metric_definitions)
    if distributions != expected_distributions:
        raise gba_playtest.PlaytestError(
            f"{source}.summary.metric_distributions does not match run metrics"
        )
    return root


def _provenance_changes(
    baseline: Any,
    candidate: Any,
    path: str = "",
) -> list[dict[str, Any]]:
    if isinstance(baseline, dict) and isinstance(candidate, dict):
        changes = []
        for key in sorted(set(baseline) | set(candidate)):
            child_path = f"{path}.{key}" if path else key
            if key not in baseline:
                changes.append(
                    {
                        "baseline": "<absent>",
                        "candidate": candidate[key],
                        "field": child_path,
                    }
                )
            elif key not in candidate:
                changes.append(
                    {
                        "baseline": baseline[key],
                        "candidate": "<absent>",
                        "field": child_path,
                    }
                )
            else:
                changes.extend(
                    _provenance_changes(
                        baseline[key],
                        candidate[key],
                        child_path,
                    )
                )
        return changes
    if baseline != candidate:
        return [{"baseline": baseline, "candidate": candidate, "field": path}]
    return []


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
            "provenance_changes": _provenance_changes(
                baseline["provenance"],
                candidate["provenance"],
            ),
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


def _temporary_output_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.tmp")


@contextmanager
def _atomic_output(path: Path):
    temporary = _temporary_output_path(path)
    stream = None
    published = False
    destination_linked = False
    staged_identity: tuple[int, int] | None = None

    def fsync_directory() -> None:
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def remove_link_if_staged() -> None:
        if not destination_linked or staged_identity is None:
            return
        try:
            current = path.stat()
            if (current.st_dev, current.st_ino) == staged_identity:
                path.unlink()
        except FileNotFoundError:
            pass

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = temporary.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise gba_playtest.PlaytestError(
            f"temporary output collision: {temporary} is already reserved"
        ) from exc
    except OSError as exc:
        raise gba_playtest.PlaytestError(
            f"cannot reserve temporary output {temporary}: {exc}"
        ) from exc
    try:
        yield stream
        stream.flush()
        os.fsync(stream.fileno())
        staged = os.fstat(stream.fileno())
        staged_identity = (staged.st_dev, staged.st_ino)
        stream.close()
        stream = None
        try:
            os.link(temporary, path)
            destination_linked = True
        except FileExistsError as exc:
            raise gba_playtest.PlaytestError(
                f"output collision: {path} was created while the report was running "
                "and will not be overwritten"
            ) from exc
        fsync_directory()
        temporary.unlink()
        fsync_directory()
        published = True
    except OSError as exc:
        raise gba_playtest.PlaytestError(
            f"cannot atomically publish output {path}: {exc}"
        ) from exc
    finally:
        if stream is not None:
            stream.close()
        if not published:
            remove_link_if_staged()
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise gba_playtest.PlaytestError(
                    f"cannot remove failed temporary output {temporary}: {exc}"
                ) from exc


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
            with _atomic_output(output) as reserved:
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
        seeds = _validate_seed_values(seeds, specification.seed_probe)
        with _atomic_output(output) as reserved:
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
            validate_report(report, "<generated autoplay batch report>")
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
