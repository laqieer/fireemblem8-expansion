"""Closed experiment identity and external containment policy."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import re
import sys
import time
import traceback


REPOSITORY = "laqieer/fireemblem8-expansion"
BRANCH = "calibration/issue-180-toolchain-component-sizing-1"
WORKFLOW = ".github/workflows/issue180-toolchain-component-sizing-1.yml"
PREVIOUS_WORKFLOW = ".github/workflows/issue180-ci-baseline-20.yml"
OUTPUT_PREFIX = "issue180-toolchain-component-sizing-1-"
BASE = "ec1dc8553419c8833a687fd8d4a6521a4e29ff7a"
GRAPH = "c3e226e79ad69ad81c29acbc5e8810262ce5c8a6"
WORKLOAD_KIND = "toolchain-component-control-measurement"
FIXTURE_VERSION = "one-make-two-checker-typed-intermediate-v1"
PROFILE = "toolchain-component-control-under-global-v1"
COMPONENT_METHOD = "test_one_make_two_checker_typed_intermediate_component"
COMPONENT_CASE = "scripts.validation_ownership.tests.test_toolchain_runtime.ModernToolchainTests"
COMPONENT_TARGET = "expansion-modern-all"
STAGES = ("version", "target", "assembler", "syntax", "compile")
ROOT_TARGET = "expansion-modern-all"
ROOT_HEADER = "build/modern/release/aapcs/src/msg_data.headers.d"
MIB = 1024 * 1024
GIB = 1024 * MIB
GRAPH_SECONDS = 3600
POLICY_SENTINEL = (1 << 63) - 1
OUTPUT_BYTES = 16 * MIB
ARTIFACT_BYTES = 32 * MIB
METRICS_BYTES = 4 * MIB
PROGRESS_BYTES = 4 * MIB
ERROR_BYTES = 65536
SAMPLE_SECONDS = 5
ARTIFACT_NAMES = (
    "scope.json", "preflight.json", "metrics.jsonl", "progress.jsonl",
    "result.json",
)
CLEAN_ENV = {
    "HOME": "/nonexistent", "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
    "TZ": "UTC", "TMPDIR": "/tmp", "PYTHONDONTWRITEBYTECODE": "1",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_COUNT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",
}
NAMESPACES = ("mnt", "pid", "net", "ipc", "uts")
CGROUP_FILES = (
    "memory.max", "memory.swap.max", "memory.oom.group",
    "memory.current", "memory.peak", "memory.events",
    "pids.max", "pids.current", "pids.peak", "pids.events",
    "cgroup.events", "cgroup.procs", "io.stat", "cpu.stat",
)
ORIGINAL_LIMITS = {
    "seconds": 3600, "runs": 4096, "states": 4096, "processes": 32,
    "descendants": 16384, "pending": 32, "total_bytes": 768 * MIB,
    "snapshot_bytes": 384 * MIB, "output_bytes": 64 * MIB,
    "event_bytes": 16 * MIB, "mapping_bytes": 32 * MIB, "cache_bytes": 32 * MIB,
    "pending_bytes": MIB, "control_bytes": 32 * MIB, "sandbox_bytes": 64 * MIB,
    "created_files": 4096, "entries": 32768, "file_bytes": 16 * MIB,
    "process_output_bytes": MIB, "address_space_bytes": 512 * MIB,
    "syscalls": 2_000_000, "observations": None,
}
UNCHANGED_CUMULATIVE = {
    "runs": "Cumulative subprocess work; external deadline/PID containment remains.",
    "states": "Cumulative attempted states; the graph's fixed per-target 512-context guard remains.",
    "descendants": "Cumulative guest creations; live guest and external cgroup PID limits remain.",
    "syscalls": "Cumulative supervised work; every syscall policy decision remains.",
    "total_bytes": "Combined accounting ceiling, not memory; all nonnegative category charges remain.",
    "snapshot_bytes": "Cumulative snapshot representations/stream; blob/file/source guards remain.",
    "output_bytes": "Cumulative captured output; per-process output and per-file guards remain.",
    "event_bytes": "Cumulative events; fixed native framing and file guards remain.",
    "mapping_bytes": "Cumulative mappings; fixed per-file/request/ownership checks remain.",
    "cache_bytes": "Cumulative cache representations; real cache/replay/owner lifetimes remain.",
    "pending_bytes": "Cumulative requests/plans; the separate original 1 MiB whole-record and aggregate-plan admissions remain.",
    "sandbox_bytes": "Cumulative writes; individual file and external filesystem bounds remain.",
    "observations": "Cumulative attempted observation work; the original entries cap still bounds every capsule and inventory.",
}
RELAXED = {
    "control_bytes": "Cumulative control traffic bounded by the unchanged original aggregate; not a size estimate.",
}
RETAINED = {
    "seconds": "One original-duration absolute graph deadline, also independently enforced outside the worker.",
    "processes": "Live guest capacity is coupled to funded VM and nested process reservations.",
    "pending": "Mixed live-request, gitlink and runtime-source admission count.",
    "created_files": "Mixed cumulative creation and publication/output-tree cardinality guard.",
    "entries": "Source inventory, peer ancestry, metadata, regex and per-capsule observation cardinality guard.",
    "file_bytes": "Genuine file/message/decoded-frame admission and guest RLIMIT_FSIZE boundary.",
    "process_output_bytes": "Genuine per-process captured-output boundary.",
    "address_space_bytes": "Original funded guest VM pool and independent regex-worker AS bound.",
    **UNCHANGED_CUMULATIVE,
}
CONTROL_CEILING = ORIGINAL_LIMITS["total_bytes"]
BYTE_CATEGORIES = ("snapshot", "output", "event", "mapping", "cache", "pending", "control", "sandbox")
SESSION_COUNTERS = ("processes_used", "syscalls_used", "observations_used", "files_created")
SESSION_PEAKS = ("pending_commands_peak", "live_process_peak", "memory_peak")
ABSENT_WORKLOADS = {
    "root_check_attempts": 0, "graph_check_attempts": 0, "report_check_attempts": 0,
    "verifier_attempts": 0, "h1_attempts": 0,
}


class GuardError(RuntimeError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def parse_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise GuardError("duplicate diagnostic JSON key")
            result[key] = value
        return result

    def constant(_):
        raise GuardError("non-finite diagnostic JSON")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise GuardError("invalid diagnostic JSON") from error


def validate_event(event, *, sha, run_id, attempt, run_number, environment, operating_system, event_name):
    if not isinstance(event, dict) or any(not isinstance(event.get(name), dict) for name in ("repository", "sender")):
        raise GuardError("invalid hosted push event")
    if (
        environment != "github-hosted" or operating_system != "Linux" or event_name != "push"
        or attempt != "1" or run_number != "1"
        or not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
        or not re.fullmatch(r"[0-9a-f]{40}", sha)
        or event.get("ref") != "refs/heads/" + BRANCH
        or event.get("before") != "0" * 40 or event.get("after") != sha
        or event.get("created") is not True or event.get("deleted") is not False
        or event.get("repository", {}).get("full_name") != REPOSITORY
        or event.get("repository", {}).get("private") is not False
        or event.get("sender", {}).get("login") != REPOSITORY.split("/")[0]
    ):
        raise GuardError("requires the first owner-created public hosted branch push, run 1 attempt 1")
    return {
        "repository": REPOSITORY, "branch": BRANCH, "harness_sha": sha,
        "graph_sha": GRAPH, "base_sha": BASE, "run_id": run_id,
        "run_attempt": 1, "run_number": 1, "diagnostic_only": True,
        "production_acceptance": False, "never_merge": True,
        "workload_kind": WORKLOAD_KIND, "fixture_version": FIXTURE_VERSION,
        "component_target": COMPONENT_TARGET, "component_method": COMPONENT_CASE + "." + COMPONENT_METHOD,
        "profile": PROFILE, "source_phases": False, **ABSENT_WORKLOADS,
    }


def profile_manifest(original, *, observation_count):
    if original != ORIGINAL_LIMITS or set(RELAXED) | set(RETAINED) != set(original):
        raise GuardError("pinned source limit fields/defaults differ from the classified experiment")
    if set(RELAXED) & set(RETAINED):
        raise GuardError("ambiguous limit classification")
    if type(observation_count) is not int or observation_count != original["entries"]:
        raise GuardError("original observation_count does not preserve the declared None/entries alias")
    result = {
        name: {
            "original": value,
            "diagnostic": CONTROL_CEILING if name in RELAXED else value,
            "classification": "aggregate-derived cumulative policy" if name in RELAXED else "unchanged original limit",
            "reason": (RELAXED if name in RELAXED else RETAINED)[name],
        }
        for name, value in original.items()
    }
    result["observations"].update({
        "original_effective": observation_count,
        "diagnostic_effective": observation_count,
    })
    return result


def choose_envelope(facts):
    required = (
        "memory_total", "memory_available", "disk_available", "cpus",
        "threads_max", "threads_current",
    )
    if any(type(facts.get(key)) is not int or facts[key] < 0 for key in required):
        raise GuardError("invalid runner capacity facts")
    if not facts["memory_total"] or not facts["cpus"]:
        raise GuardError("runner capacity is empty")
    if not isinstance(facts.get("cgroup_ancestors"), list) or not facts["cgroup_ancestors"]:
        raise GuardError("effective cgroup ancestry is unavailable")
    total = facts["memory_total"]
    available = min(total, facts["memory_available"])
    pids_available = facts["threads_max"] - facts["threads_current"]
    for ancestor in facts["cgroup_ancestors"]:
        for name in ("memory", "pids"):
            maximum, current = ancestor[name + "_max"], ancestor[name + "_current"]
            if type(current) is not int or current < 0 or (
                maximum is not None and (type(maximum) is not int or maximum <= 0)
            ):
                raise GuardError("invalid ancestor cgroup facts")
            if maximum is not None:
                if name == "memory":
                    total = min(total, maximum)
                    available = min(available, maximum - current)
                else:
                    pids_available = min(pids_available, maximum - current)
    memory = min(8 * GIB, total // 2, available - 2 * GIB) // MIB * MIB
    disk = min(8 * GIB, facts["disk_available"] // 2, facts["disk_available"] - 4 * GIB) // MIB * MIB
    pids = min(256, 32 * facts["cpus"], pids_available - 64)
    if memory < GIB or disk < GIB or pids < 64:
        raise GuardError("insufficient capacity after reserved memory/disk/PID headroom")
    return {
        "memory_max": memory, "memory_swap_max": 0, "memory_oom_group": 1,
        "pids_max": pids, "disk_bytes": disk, "graph_seconds": GRAPH_SECONDS,
        "worker_output_bytes": OUTPUT_BYTES, "artifact_bytes": ARTIFACT_BYTES,
        "memory_formula": "MiB-floor(min(8 GiB, effective total/2, effective available - 2 GiB))",
        "disk_formula": "MiB-floor(min(8 GiB, available disk/2, available disk - 4 GiB))",
        "pids_formula": "min(256, 32 * available CPUs, effective available tasks - 64)",
        "effective_memory_total": total, "effective_memory_available": available,
        "effective_pids_available": pids_available,
        "memory_semantics": "Kernel cgroup memory, including its descendants; not summed RSS or all host RAM.",
    }


def diagnostic_limit(name):
    return CONTROL_CEILING if name == "control_bytes" else ORIGINAL_LIMITS[name]


def _component_fields(value, names):
    if type(value) is not dict or value.keys() != set(names.split()):
        raise GuardError("component record has missing or unknown fields")


def _component_integer(value, maximum=POLICY_SENTINEL, minimum=0):
    return type(value) is int and minimum <= value <= maximum


def validate_component_cleanup(value, *, complete=False):
    _component_fields(value, "budget_closed children waiters retained_owners session_base_removed fixture_removed")
    if any(type(value[name]) is not bool for name in ("budget_closed",)) or any(
        value[name] is not None and type(value[name]) is not bool
        for name in ("session_base_removed", "fixture_removed")
    ) or any(
        value[name] is not None and not _component_integer(value[name])
        for name in ("children", "waiters", "retained_owners")
    ):
        raise GuardError("component cleanup observations are malformed")
    if complete and value != {
        "budget_closed": True, "children": 0, "waiters": 0, "retained_owners": 0,
        "session_base_removed": True, "fixture_removed": True,
    }:
        raise GuardError("component inner ownership is not closed")
    return value


def counter_snapshot(budget, session=None):
    if budget is None:
        return {"budget": None, "session": None}
    amounts = budget.bytes.copy()
    if not amounts.keys() <= set(BYTE_CATEGORIES):
        raise GuardError("unknown observed accounting category")
    categories = {}
    for name in BYTE_CATEGORIES:
        amount = amounts.get(name, 0)
        limit = getattr(budget.limits, name + "_bytes")
        categories[name] = {
            "charged": amount, "original_cap": ORIGINAL_LIMITS[name + "_bytes"],
            "diagnostic_cap": limit, "remaining": limit - amount,
            "exceeds_original": amount > ORIGINAL_LIMITS[name + "_bytes"],
        }
    result = {
        "budget": {
            "categories": categories, "present_categories": sorted(amounts), "total": sum(amounts.values()),
            "total_cap": budget.limits.total_bytes, "runs": budget.runs, "states": budget.states,
            "planned_state_bytes": budget.planned_state_bytes, "failed": budget.failed, "closed": budget.closed,
        },
        "session": None,
    }
    if session is not None:
        if session.budget is not budget:
            raise GuardError("telemetry session differs from the issued budget")
        result["session"] = {
            **{name: getattr(session, name) for name in (*SESSION_COUNTERS, *SESSION_PEAKS)},
            "pending_commands": session.pending_commands, "parked_capsules": len(session.parked_capsules),
            "make_depth": session.make_depth, "children": len(budget.children),
            "waiters": len(budget.producer_waiters),
        }
    validate_component_counters(result)
    return result


def validate_component_counters(value, *, complete=False):
    _component_fields(value, "budget session")
    budget, session = value["budget"], value["session"]
    if budget is None:
        if complete or session is not None:
            raise GuardError("component counters lack the actual budget")
        return value
    _component_fields(
        budget, "categories present_categories total total_cap runs states planned_state_bytes failed closed",
    )
    if (
        type(budget["categories"]) is not dict or budget["categories"].keys() != set(BYTE_CATEGORIES)
        or type(budget["present_categories"]) is not list
        or any(type(name) is not str or name not in BYTE_CATEGORIES for name in budget["present_categories"])
        or len(set(budget["present_categories"])) != len(budget["present_categories"])
        or any(not _component_integer(budget[name]) for name in ("total", "total_cap", "runs", "states", "planned_state_bytes"))
        or budget["total_cap"] != ORIGINAL_LIMITS["total_bytes"]
        or type(budget["failed"]) is not bool or type(budget["closed"]) is not bool
    ):
        raise GuardError("component budget counters are malformed")
    total = 0
    for name, row in budget["categories"].items():
        _component_fields(row, "charged original_cap diagnostic_cap remaining exceeds_original")
        if (
            any(not _component_integer(row[key]) for key in ("charged", "original_cap", "diagnostic_cap", "remaining"))
            or row["original_cap"] != ORIGINAL_LIMITS[name + "_bytes"]
            or row["diagnostic_cap"] != diagnostic_limit(name + "_bytes")
            or row["remaining"] != row["diagnostic_cap"] - row["charged"]
            or type(row["exceeds_original"]) is not bool
            or row["exceeds_original"] != (row["charged"] > row["original_cap"])
            or name not in budget["present_categories"] and row["charged"] != 0
        ):
            raise GuardError("component category counters disagree with actual policy")
        total += row["charged"]
    if total != budget["total"] or total > budget["total_cap"]:
        raise GuardError("component aggregate accounting is inconsistent")
    if session is not None:
        _component_fields(session, " ".join((
            *SESSION_COUNTERS, *SESSION_PEAKS, "pending_commands", "parked_capsules",
            "make_depth", "children", "waiters",
        )))
        if any(not _component_integer(number) for number in session.values()):
            raise GuardError("component session counters are malformed")
    if complete:
        if (
            budget["failed"] is not False or budget["closed"] is not True
            or not 1 <= budget["runs"] <= ORIGINAL_LIMITS["runs"]
            or not 1 <= budget["states"] <= ORIGINAL_LIMITS["states"]
            or budget["planned_state_bytes"] > MIB or session is None
            or any(session[name] != 0 for name in (
                "pending_commands", "parked_capsules", "make_depth", "children", "waiters",
            ))
            or any(session[name] > ORIGINAL_LIMITS[limit] for name, limit in (
                ("processes_used", "descendants"), ("syscalls_used", "syscalls"),
                ("observations_used", "entries"), ("files_created", "created_files"),
                ("pending_commands_peak", "pending"), ("live_process_peak", "processes"),
                ("memory_peak", "address_space_bytes"),
            ))
        ):
            raise GuardError("component counters are incomplete, open or exceed a retained boundary")
    return value


def validate_component_observation(value):
    _component_fields(value, (
        "make_attempts make_returned checker_occurrences recipe_receipts dispatch_sequences producer_slots "
        "stage_names intermediate_versions intermediate_complete retirement_absent "
        "retired_nlinks assembly_extents content_equal bindings_distinct raw_receipts_distinct "
        "semantic_records typed_references"
    ))
    if (
        any(type(value[name]) is not int or value[name] != expected for name, expected in (
            ("make_attempts", 1), ("make_returned", 1), ("checker_occurrences", 2),
            ("recipe_receipts", 2), ("semantic_records", 1), ("typed_references", 2),
        ))
        or any(value[name] is not True for name in ("content_equal", "bindings_distinct", "raw_receipts_distinct"))
        or value["stage_names"] != [list(STAGES), list(STAGES)]
    ):
        raise GuardError("component lacks one real Make and its exact checker pair")
    for name in (
        "dispatch_sequences", "producer_slots", "intermediate_versions",
        "intermediate_complete", "retirement_absent", "retired_nlinks", "assembly_extents",
    ):
        if type(value[name]) is not list or len(value[name]) != 2:
            raise GuardError("component pair observation is incomplete")
    if (
        any(not _component_integer(number, minimum=1) for number in value["dispatch_sequences"])
        or any(not _component_integer(number, ORIGINAL_LIMITS["entries"] - 1) for number in value["producer_slots"])
        or value["dispatch_sequences"][0] >= value["dispatch_sequences"][1]
        or value["producer_slots"][0] >= value["producer_slots"][1]
        or any(type(number) is not int or number != 1 for number in value["intermediate_versions"])
        or any(flag is not True for name in ("intermediate_complete", "retirement_absent") for flag in value[name])
        or any(type(number) is not int or number != 0 for number in value["retired_nlinks"])
        or any(not _component_integer(number, ORIGINAL_LIMITS["file_bytes"]) for number in value["assembly_extents"])
        or value["assembly_extents"][0] != value["assembly_extents"][1]
    ):
        raise GuardError("component binding/content/retirement evidence is invalid")
    return value


def validate_component_result(value):
    _component_fields(value, (
        "version workload_kind fixture_version source_revision base_revision profile method target source_phases "
        "component_attempts component_completed root_check_attempts graph_check_attempts report_check_attempts "
        "verifier_attempts h1_attempts fixture observation counters cleanup"
    ))
    expected = {
        "workload_kind": WORKLOAD_KIND, "fixture_version": FIXTURE_VERSION, "source_revision": GRAPH,
        "base_revision": BASE, "profile": PROFILE, "method": COMPONENT_CASE + "." + COMPONENT_METHOD,
        "target": COMPONENT_TARGET,
    }
    if (
        any(type(value[name]) is not str or value[name] != wanted for name, wanted in expected.items())
        or type(value["version"]) is not int or value["version"] != 1
        or type(value["component_attempts"]) is not int or value["component_attempts"] != 1
        or value["component_completed"] is not True or value["source_phases"] is not False
        or any(type(value[name]) is not int or value[name] != 0 for name in ABSENT_WORKLOADS)
    ):
        raise GuardError("component result is foreign, partial or claims another workload")
    fixture = value["fixture"]
    _component_fields(fixture, "preexisting_query genuine_headers header_parents_absent text_producer empty_final_target")
    if (
        fixture["preexisting_query"] is not True or fixture["header_parents_absent"] is not True
        or fixture["text_producer"] is not False or fixture["empty_final_target"] is not True
        or not _component_integer(fixture["genuine_headers"], ORIGINAL_LIMITS["entries"], 1)
    ):
        raise GuardError("component fixture is not the selected original small source")
    validate_component_observation(value["observation"])
    validate_component_counters(value["counters"], complete=True)
    validate_component_cleanup(value["cleanup"], complete=True)
    return {
        "workload_kind": WORKLOAD_KIND, "component_only": True, "make_invocations": 1,
        "checker_invocations": 2, "diagnostic_only": True, "production_acceptance": False,
        "complete_repository_report": False,
    }


def component_error_record(error):
    chain, seen = [], set()
    current = error
    while current is not None:
        if id(current) in seen or len(seen) >= 32:
            return {"chain": chain, "complete": False, "reason": "exception-chain-bound"}
        seen.add(id(current))
        name = type.__getattribute__(type(current), "__name__")
        if type(name) is not str or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) is None:
            name = "unavailable-type"
        try:
            number = current.errno if isinstance(current, OSError) else None
        except BaseException:
            chain.append({"type": name, "errno": None})
            return {"chain": chain, "complete": False, "reason": "error-metadata-unavailable"}
        if number is not None and not _component_integer(number, 4095):
            number = None
        chain.append({"type": name, "errno": number})
        cause = BaseException.__getattribute__(current, "__cause__")
        current = cause if cause is not None else BaseException.__getattribute__(current, "__context__")
    return {"chain": chain, "complete": True, "reason": None}


def validate_component_error_record(value):
    _component_fields(value, "chain complete reason")
    if (
        type(value["chain"]) is not list or len(value["chain"]) > 32
        or type(value["complete"]) is not bool
        or value["complete"] and (value["reason"] is not None or not value["chain"])
        or not value["complete"] and value["reason"] not in {
            "exception-chain-bound", "error-metadata-unavailable", "secondary-format-failed",
        }
    ):
        raise GuardError("component error metadata is not its bounded closed record")
    for row in value["chain"]:
        _component_fields(row, "type errno")
        if (
            type(row["type"]) is not str
            or row["type"] != "unavailable-type" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", row["type"]) is None
            or row["errno"] is not None and not _component_integer(row["errno"], 4095)
        ):
            raise GuardError("component error metadata contains an unbounded or private field")
    return value


def component_secondary_error(error):
    try:
        return validate_component_error_record(component_error_record(error))
    except BaseException:
        # A failed metadata collector must not replace the operation whose
        # failure is being recorded. The unavailable record is never success.
        return {"chain": [], "complete": False, "reason": "secondary-format-failed"}


def validate_report(report):
    if not isinstance(report, dict):
        raise GuardError("graph returned no real report object")
    coverage = report.get("coverage", {})
    keys = ("tracked_paths", "owned_paths", "fail_closed_exclusions")
    if any(type(coverage.get(key)) is not int or coverage[key] < 0 for key in keys):
        raise GuardError("invalid full-report coverage")
    if coverage["tracked_paths"] < 1 or coverage["tracked_paths"] != (
        coverage["owned_paths"] + coverage["fail_closed_exclusions"]
    ):
        raise GuardError("full-report coverage does not partition the captured tree")
    measurement = report.get("measurement", {})
    if any(type(measurement.get(key)) is not int or measurement[key] != 0 for key in (
        "false_positive_selections", "false_negative_selections",
    )):
        raise GuardError("full-report oracle rejected")
    lifecycle = report.get("artifact", {}).get("executable_lifecycle")
    if not isinstance(lifecycle, list) or len(lifecycle) != 3 or any(
        not isinstance(item, dict) or item.get("removal") != "fail" or item.get("restoration") != "pass"
        or any(not isinstance(item.get(key), str) or not item[key] for key in ("proof_id", "trigger_event_id", "trigger_type"))
        for item in lifecycle
    ):
        raise GuardError("full-report lifecycle proof is incomplete")
    if any(len({item[key] for item in lifecycle}) != 3 for key in ("proof_id", "trigger_event_id")):
        raise GuardError("full-report lifecycle proof repeats an identity")
    execution = report.get("execution", {})
    if execution.get("revision") != GRAPH or execution.get("base_revision") != BASE:
        raise GuardError("full report differs from the exact CURRENT/BASE scope")
    if any(type(execution.get(key)) is not int or execution[key] < 1 for key in ("runs", "states")):
        raise GuardError("full report has no actual execution")
    return {
        "coverage": coverage, "oracle_false_positives": 0, "oracle_false_negatives": 0,
        "lifecycle_cases": len(lifecycle), "diagnostic_only": True, "production_acceptance": False,
    }


def _root_path(value):
    if (
        not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096
        or value.startswith("/") or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(character) < 32 for character in value)
    ):
        raise GuardError("root result has a noncanonical logical path")
    return value


def _root_state(value):
    if not isinstance(value, list) or len(value) > 1:
        raise GuardError("root result has an unsupported state")
    for row in value:
        if (
            not isinstance(row, list) or len(row) != 3
            or not isinstance(row[0], str) or not isinstance(row[1], str)
            or row[0] not in {"command-line", "environment"}
            or row[1] != "MODERN_ALL_C_SOURCES" or not isinstance(row[2], str)
            or len(row[2].encode("utf-8")) > ORIGINAL_LIMITS["file_bytes"]
        ):
            raise GuardError("root result has a foreign state input")
    return encoded(value)


def validate_root_toolchain(value):
    if (
        not isinstance(value, dict) or set(value) != {
            "executed", "stages", "stdin", "repository_inputs", "sdk_inputs",
        }
        or any(type(value[name]) is not int or not 1 <= value[name] <= ORIGINAL_LIMITS["entries"]
               for name in ("repository_inputs", "sdk_inputs"))
        or not isinstance(value["executed"], list) or len(value["executed"]) != 8
        or any(not isinstance(path, str) for path in value["executed"])
        or not isinstance(value["stages"], list) or len(value["stages"]) != 5
        or not isinstance(value["stdin"], list) or len(value["stdin"]) != 2
    ):
        raise GuardError("root result omitted actual toolchain observations")
    driver = "/usr/bin/arm-none-eabi-gcc"
    frontend, assembler = value["executed"][4], value["executed"][7]
    if (
        not isinstance(frontend, str)
        or not re.fullmatch(r"/usr/lib/gcc/arm-none-eabi/[A-Za-z0-9_.+-]+/cc1", frontend)
        or assembler not in {"/usr/bin/arm-none-eabi-as", "/usr/lib/arm-none-eabi/bin/as"}
        or value["executed"] != [driver, driver, driver, driver, frontend, driver, frontend, assembler]
    ):
        raise GuardError("root result has foreign compiler execution")
    for row, stage, paths in zip(
        value["stages"], ("version", "target", "assembler", "syntax", "compile"),
        ([driver], [driver], [driver], [driver, frontend], [driver, frontend, assembler]),
    ):
        if row != {"stage": stage, "executed": paths}:
            raise GuardError("root toolchain substeps are incomplete or reordered")
    for row, stage, count in zip(
        value["stdin"], ("syntax", "compile"),
        (len('#include "global.h"\n'), len("void modern_arm7tdmi_thumb_probe(void) {}\n")),
    ):
        if (
            not isinstance(row, dict) or set(row) != {"stage", "bytes", "eof"}
            or row["stage"] != stage or type(row["bytes"]) is not int or row["bytes"] != count
            or row["eof"] is not True
        ):
            raise GuardError("root result lost its actual original stdin")


def validate_root_result(value):
    if (
        not isinstance(value, dict) or set(value) != {
            "version", "workload_kind", "fixture_version", "source_revision", "base_revision",
            "target", "source_phases", "root_check_attempts", "root_check_completed",
            "graph_check_attempts", "complete_repository_report", "initial_absence",
            "observations", "planner", "cleanup",
        }
        or type(value["version"]) is not int or value["version"] != 1
        or value["workload_kind"] != WORKLOAD_KIND or value["fixture_version"] != FIXTURE_VERSION
        or value["source_revision"] != GRAPH or value["base_revision"] != BASE
        or value["target"] != ROOT_TARGET or value["source_phases"] is not True
        or type(value["root_check_attempts"]) is not int or value["root_check_attempts"] != 1
        or value["root_check_completed"] is not True
        or type(value["graph_check_attempts"]) is not int or value["graph_check_attempts"] != 0
        or value["complete_repository_report"] is not False
        or value["initial_absence"] != {"generated_c": True, "public_parents": True}
        or any(type(item) is not bool for item in value["initial_absence"].values())
        or not isinstance(value["observations"], list) or not 1 <= len(value["observations"]) <= 512
    ):
        raise GuardError("root result is incomplete, foreign or not root-only")
    states = []
    for row in value["observations"]:
        if (
            not isinstance(row, dict) or set(row) != {
                "state", "passes", "analyzed_passes", "source_files", "source_journal_closed",
                "native_jobs", "toolchain", "generated", "inputs_unchanged",
            }
            or type(row["passes"]) is not int or not 2 <= row["passes"] <= 64
            or type(row["analyzed_passes"]) is not int or row["analyzed_passes"] != row["passes"]
            or row["source_journal_closed"] is not True or row["inputs_unchanged"] is not True
            or not isinstance(row["source_files"], list) or not 1 <= len(row["source_files"]) <= ORIGINAL_LIMITS["entries"]
            or any(not isinstance(name, str) for name in row["source_files"])
            or len(set(row["source_files"])) != len(row["source_files"])
            or "Makefile" not in row["source_files"] or ROOT_HEADER not in row["source_files"]
            or not isinstance(row["native_jobs"], dict) or set(row["native_jobs"]) != {"text", "toolchain", "header"}
            or any(type(row["native_jobs"][name]) is not int or not 1 <= row["native_jobs"][name] <= ORIGINAL_LIMITS["entries"]
                   for name in ("text", "toolchain", "header"))
            or row["native_jobs"]["header"] < 5
            or not isinstance(row["toolchain"], list) or not 1 <= len(row["toolchain"]) <= row["native_jobs"]["toolchain"]
            or not isinstance(row["generated"], list) or len(row["generated"]) != 2
        ):
            raise GuardError("root result lacks completed native/phase/source observations")
        states.append(_root_state(row["state"]))
        for name in row["source_files"]:
            _root_path(name)
        for item in row["toolchain"]:
            validate_root_toolchain(item)
        generated = {}
        for item in row["generated"]:
            if (
                not isinstance(item, dict) or set(item) != {"path", "mode", "size"}
                or type(item["mode"]) is not int or item["mode"] != 0o644
                or type(item["size"]) is not int or not 1 <= item["size"] <= ORIGINAL_LIMITS["file_bytes"]
                or not isinstance(item["path"], str) or item["path"] in generated
            ):
                raise GuardError("root generated result is malformed")
            generated[_root_path(item["path"])] = item
        if set(generated) != {"src/msg_data.c", ROOT_HEADER}:
            raise GuardError("root generated result differs from the genuine fixture extent")
    planner = value["planner"]
    if (
        len(states) != len(set(states)) or not isinstance(planner, dict)
        or set(planner) != {"states", "used_domains", "enumerated_domains"}
        or not isinstance(planner["states"], list)
        or sorted(_root_state(state) for state in planner["states"]) != sorted(states)
        or planner["used_domains"] != ["MODERN_ALL_C_SOURCES"]
        or planner["enumerated_domains"] != ["MODERN_ALL_C_SOURCES"]
    ):
        raise GuardError("root result does not match the completed planner states")
    if value["cleanup"] != {
        "budget_closed": True, "children": 0, "waiters": 0, "retained_owners": 0,
        "session_base_removed": True, "fixture_removed": True,
    } or any(
        type(value["cleanup"][name]) is not int for name in ("children", "waiters", "retained_owners")
    ) or any(type(value["cleanup"][name]) is not bool for name in ("budget_closed", "session_base_removed", "fixture_removed")):
        raise GuardError("root result has incomplete or uncertain inner cleanup")
    return {
        "workload_kind": WORKLOAD_KIND, "observations": len(states), "root_only": True,
        "diagnostic_only": True, "production_acceptance": False, "complete_repository_report": False,
    }


def validate_root_counters(value):
    if (
        not isinstance(value, dict) or set(value) != {"phase", "budget", "semantics"}
        or value["phase"] != "completed-root-only" or not isinstance(value["semantics"], str)
        or not isinstance(value["budget"], dict)
        or set(value["budget"]) != {"bytes", "total", "runs", "states", "failed", "closed"}
    ):
        raise GuardError("root counters lack their actual completed diagnostic lifetime")
    budget = value["budget"]
    categories = {name[:-6] for name in RELAXED if name.endswith("_bytes") and name != "total_bytes"}
    if (
        not isinstance(budget["bytes"], dict) or not budget["bytes"]
        or not set(budget["bytes"]) <= categories
        or any(type(amount) is not int or not 0 <= amount <= POLICY_SENTINEL for amount in budget["bytes"].values())
        or any(type(budget[name]) is not int or not 1 <= budget[name] <= POLICY_SENTINEL for name in ("total", "runs", "states"))
        or budget["total"] != sum(budget["bytes"].values())
        or budget["failed"] is not False or budget["closed"] is not True
    ):
        raise GuardError("root counters are malformed, failed, reset or still open")


def validate_nested_probe(value, *, uid, gid, group, parent_user_namespace):
    if (
        not isinstance(value, dict) or type(value.get("uid")) is not int or value["uid"] != 0
        or value.get("uid_map") != ["0", str(uid), "1"]
        or value.get("gid_map") != ["0", str(gid), "1"]
        or value.get("no_new_privs") != "1" or value.get("cgroup") != group
        or not isinstance(value.get("namespaces"), dict)
        or type(value["namespaces"].get("user")) is not int
        or value["namespaces"]["user"] == parent_user_namespace
    ):
        raise GuardError("nested isolation did not preserve its real UID/NNP/cgroup binding")
    controls = value.get("cgroup_control_probe")
    if not isinstance(controls, dict):
        raise GuardError("nested containment-control qualification is missing")
    if set(controls) in ({"namespace_denied"}, {"mount_denied"}):
        outcome = next(iter(controls.values()))
        if type(outcome) is not int or outcome not in {errno.EPERM, errno.EACCES}:
            raise GuardError("nested control access failed for an unqualified reason")
    elif set(controls) == {"same_value_writes_denied"}:
        denied = controls["same_value_writes_denied"]
        if not isinstance(denied, dict) or set(denied) != {"memory.max", "pids.max"} or any(
            type(value) is not int or value not in {errno.EPERM, errno.EACCES, errno.EROFS}
            for value in denied.values()
        ):
            raise GuardError("nested namespace can reach an unqualified containment control")
    else:
        raise GuardError("unexpected nested control qualification")
    validate_proc_protection(value.get("proc_protection"))


def validate_proc_protection(value):
    paths = {
        "/proc/self/oom_score_adj", "/proc/self/oom_adj",
        "/proc/sys/kernel/kptr_restrict", "/proc/sys/vm/overcommit_memory",
        "/proc/sys/kernel/overflowuid", "/proc/sys/kernel/overflowgid",
    }
    if (
        not isinstance(value, dict) or type(value.get("pid")) is not int or value["pid"] < 1
        or value.get("proc_pid") != value["pid"] or value.get("oom_score_adj_after") != "0"
        or type(value.get("proc_mount_flags")) is not int or value["proc_mount_flags"] & 15 != 14
        or not isinstance(value.get("denied_writes"), dict) or set(value["denied_writes"]) != paths
    ):
        raise GuardError("private RW proc protection qualification is incomplete")
    for path, result in value["denied_writes"].items():
        if (
            not isinstance(result, dict) or type(result.get("opened")) is not bool
            or type(result.get("errno")) is not int or result["errno"] not in {errno.EPERM, errno.EACCES, errno.EROFS}
            or not isinstance(result.get("before"), str) or result.get("after") != result["before"]
            or path == "/proc/self/oom_score_adj" and result["before"] != "0"
        ):
            raise GuardError("dangerous proc write was not actually denied with unchanged state")
    sysrq = value.get("sysrq_write_open")
    if not isinstance(sysrq, dict) or type(sysrq.get("exposed")) is not bool or type(sysrq.get("errno")) is not int:
        raise GuardError("global sysrq control qualification is missing")
    if sysrq["errno"] not in ({errno.EPERM, errno.EACCES, errno.EROFS} if sysrq["exposed"] else {errno.ENOENT}):
        raise GuardError("global sysrq control is not protected")


def error_record(error):
    frames, seen = [], set()
    current = error
    chain = []
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current)
        raw = message.encode("utf-8", "backslashreplace")
        if len(raw) > ERROR_BYTES:
            chain.append({
                "type": type(current).__name__, "message": None,
                "message_bytes": len(raw), "message_sha256": hashlib.sha256(raw).hexdigest(),
                "evidence_overflow": "Original message exceeds the diagnostic bound; no successful report is permitted.",
            })
        else:
            chain.append({"type": type(current).__name__, "message": message})
        trace = current.__traceback__
        while trace is not None:
            code = trace.tb_frame.f_code
            if len(frames) >= 256:
                frames.append({"evidence_overflow": "Trace exceeds the bounded frame count."})
                break
            if len(code.co_filename) > 4096 or len(code.co_name) > 512:
                frames.append({
                    "line": trace.tb_lineno,
                    "identity_sha256": hashlib.sha256(encoded([code.co_filename, code.co_name])).hexdigest(),
                    "evidence_overflow": "Oversized frame identity is recorded by digest, not truncated.",
                })
            else:
                frames.append({"file": code.co_filename, "line": trace.tb_lineno, "function": code.co_name})
            trace = trace.tb_next
        if len(chain) >= 32:
            chain.append({"evidence_overflow": "Exception chain exceeds the diagnostic bound."})
            break
        current = current.__cause__ or current.__context__
    return {"chain": chain, "frames": frames}


SOURCE_REFUSAL_REVISION = "61856581bc9859f59fdd938edc219fabc07cd6ef"
SOURCE_REFUSAL_FORMAT = "namespace-refusal-metadata-v1"
SOURCE_REFUSAL_KIND = "source-refusal-attribution-not-a-proof"
SOURCE_REFUSAL_MESSAGES = (
    "deferred namespace use lacks an original parse-time snapshot",
    "exported namespace body crosses an unproven mutation interval",
)
SOURCE_REFUSAL_OMISSIONS = (
    "expression", "read_form", "unresolved", "snapshot_facts.*.read_forms", "__notes__",
)
_SOURCE_TYPE_UNAVAILABLE = "unavailable-type-name-exceeds-existing-bound"
_SOURCE_STAGES = (
    "selection", "source-data", "bounded-serialization", "retention-accounting",
    "projection", "wire-validation",
)
_SOURCE_REASONS = (
    "not-attached", "upstream-unavailable", "unsupported-source-revision",
    "unsupported-source-shape", "invalid-source-shape", "metadata-bound", "deadline",
    "projection-exception", "primary-formatter-failed", "unsupported-projection-format",
    "invalid-wire-metadata", "failure-type-over-bound",
)
_SOURCE_SITE = (
    ("path", "path"), ("logical", "integer"), ("start", "positive"), ("end", "positive"),
)
_SOURCE_OCCURRENCE = (
    ("path", "path"), ("stream_position", "integer"), ("site", ("optional", "site")),
    ("visit", ("optional", "positive")),
    ("visit_status", ("enum", ("unique-original-visit", "unavailable-or-repeated"))),
    ("rule_number", ("optional", "integer")), ("recipe_ordinal", ("optional", "integer")),
    ("active", ("optional", "boolean")),
)
_SOURCE_AGGREGATE = (
    ("status", ("literal", "unavailable")),
    ("reason", ("literal", "export aggregate has no unique source occurrence")),
)
_SOURCE_FACT = (
    ("bindings", ("optional", ("array", "binding"))),
    ("binding_status", ("enum", ("unavailable", "version-mismatch", "stored-original-binding"))),
    ("binding_version", ("optional", "integer")), ("mode_version", "integer"),
    ("source_fact_kind", ("optional", ("enum", ("exact", "header-bound")))),
    ("source_fact_version", ("optional", "integer")),
    ("original_input_flags", ("optional", "flags")),
    ("dependency_names", ("array", "name")), ("namespace_carrier", "boolean"),
    ("unsafe", "boolean"), ("target_scopes", ("array", "target")),
    ("snapshot_decision", ("enum", (
        "disabled-by-unknown-writer", "not-examined", "unsafe-binding", "ambiguous-binding",
        "not-simple", "exact-original-snapshot", "exact-original-value-unavailable",
    ))),
    ("assignment_site_status", ("literal", "unavailable-not-retained-by-global-binding")),
)
_SOURCE_METADATA = (
    ("kind", ("literal", SOURCE_REFUSAL_KIND)), ("pass", "positive"), ("exec", "positive"),
    ("scope", "scope"), ("source", "occurrence"),
    ("condition", ("enum", (
        "unresolved-selector", "direct-wildcard", "namespace-dependency", "export-namespace-dependency",
    ))),
    ("reader_names", ("array", "name")), ("carrier_path", ("array", "name")),
    ("unknown_writer", "boolean"), ("unsafe_unknown_causes", ("array", "writer")),
    ("snapshot_facts", "fact-map"), ("use_associations", ("array", "association")),
    ("association_status", ("enum", ("recorded", "unavailable-not-inferred"))),
    ("use_kind", ("enum", (
        "active-source-obligation; actual job status requires an association",
        "export-read aggregate; no unique source occurrence",
    ))),
)
_SOURCE_FAILURE = (
    ("reason", ("enum", _SOURCE_REASONS)), ("failure_type", ("optional", "type-name")),
)
_SOURCE_MEMBER = (
    ("format", ("literal", SOURCE_REFUSAL_FORMAT)),
    ("source_revision", ("literal", SOURCE_REFUSAL_REVISION)),
    ("status", ("enum", ("available", "unavailable"))),
    ("metadata", ("optional", "metadata")), ("unavailable", ("optional", "unavailable")),
    ("omitted_by_contract", ("exact-array", SOURCE_REFUSAL_OMISSIONS)),
    ("publication_errors", "publication-errors"),
)
_SOURCE_MAP_CELL = 2 * sys.getsizeof({None: None})
_SOURCE_LIST_CELL = 2 * sys.getsizeof([None])
_SOURCE_ABSENT = object()


class _SourceProjectionError(GuardError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _SourceProjection:
    """Fixed-schema measurement precedes construction; raw omitted subtrees are never walked."""

    def __init__(self, *, upstream=False, build=False, admitted=None, deadline=None):
        self.upstream, self.build, self.admitted, self.deadline = upstream, build, admitted, deadline
        self.cells = self.size = self.storage = 0
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= 0
        ):
            raise _SourceProjectionError("deadline")

    def checkpoint(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise _SourceProjectionError("deadline")

    def account(self, *, cells=0, size=0, storage=0):
        self.checkpoint()
        values = self.cells + cells, self.size + size, self.storage + storage
        if (
            values[0] > ORIGINAL_LIMITS["entries"] or values[1] > ERROR_BYTES
            or values[2] > ORIGINAL_LIMITS["file_bytes"]
            or self.admitted is not None and any(a > b for a, b in zip(values, self.admitted))
        ):
            raise _SourceProjectionError("metadata-bound")
        self.cells, self.size, self.storage = values

    def text(self, value, *, maximum=4096, pattern=None):
        if type(value) is not str:
            raise _SourceProjectionError("invalid-source-shape")
        if len(value) > maximum:
            raise _SourceProjectionError("metadata-bound")
        if not value or any(not 32 <= ord(character) <= 126 for character in value):
            raise _SourceProjectionError("invalid-source-shape")
        if pattern is not None and re.fullmatch(pattern, value) is None:
            raise _SourceProjectionError("invalid-source-shape")
        self.account(
            cells=1, size=2 + len(value) + value.count('"') + value.count("\\"),
            storage=sys.getsizeof(value),
        )
        return value if self.build else None

    def scalar(self, value, kind):
        if kind == "null":
            valid, size = value is None, 4
        elif kind == "boolean":
            valid, size = type(value) is bool, 4 if value is True else 5
        else:
            maximum = (1 << 31) - 1 if kind == "flags" else POLICY_SENTINEL
            valid = type(value) is int and int(kind == "positive") <= value <= maximum
            size, remaining = 1, value if valid else 0
            while remaining >= 10:
                size, remaining = size + 1, remaining // 10
        if not valid:
            raise _SourceProjectionError("invalid-source-shape")
        self.account(cells=1, size=size, storage=sys.getsizeof(value))
        return value if self.build else None

    @staticmethod
    def keys(value, schema, extra=()):
        if type(value) is not dict or len(value) != len(schema) + len(extra):
            raise _SourceProjectionError("unsupported-source-shape")
        names = tuple(name for name, _ in schema) + extra
        if any(type(key) is not str or key not in names for key in value):
            raise _SourceProjectionError("unsupported-source-shape")

    @staticmethod
    def variant(value, maximum):
        if type(value) is not dict or len(value) > maximum or any(type(key) is not str for key in value):
            raise _SourceProjectionError("unsupported-source-shape")

    def record(self, value, schema, *, extra=(), positional=False):
        if positional:
            if type(value) is not tuple or len(value) != len(schema):
                raise _SourceProjectionError("invalid-source-shape")
        else:
            self.keys(value, schema, extra)
        self.account(cells=1, size=2, storage=sys.getsizeof({}))
        result = {} if self.build else None
        for index, (key, kind) in enumerate(schema):
            self.account(size=1 + bool(index), storage=_SOURCE_MAP_CELL)
            self.text(key)
            field = self.value(value[index] if positional else value[key], kind)
            if result is not None:
                result[key] = field
        return result

    def array(self, value, kind, *, exact=False):
        if type(value) is not list:
            raise _SourceProjectionError("invalid-source-shape")
        if len(value) > ORIGINAL_LIMITS["entries"] - self.cells:
            raise _SourceProjectionError("metadata-bound")
        if exact and len(value) != len(kind):
            raise _SourceProjectionError("invalid-source-shape")
        self.account(cells=1, size=2, storage=sys.getsizeof([]))
        result = [] if self.build else None
        for index, child in enumerate(value):
            self.account(size=bool(index), storage=_SOURCE_LIST_CELL)
            field = self.value(child, ("literal", kind[index]) if exact else kind)
            if result is not None:
                result.append(field)
        return result

    def site(self, value, *, positional=False):
        result = self.record(value, _SOURCE_SITE, positional=positional)
        start, end = (value[2], value[3]) if positional else (value["start"], value["end"])
        if end < start:
            raise _SourceProjectionError("invalid-source-shape")
        return result

    def metadata(self, value):
        extra = ("expression", "read_form", "unresolved") if self.upstream else ()
        self.keys(value, _SOURCE_METADATA, extra)
        if self.upstream and (
            type(value["expression"]) not in (str, type(None))
            or type(value["read_form"]) not in (str, type(None))
            or type(value["unresolved"]) is not list
        ):
            raise _SourceProjectionError("invalid-source-shape")
        return self.record(value, _SOURCE_METADATA, extra=extra)

    def fact(self, value):
        extra = ("read_forms",) if self.upstream else ()
        self.keys(value, _SOURCE_FACT, extra)
        if self.upstream and type(value["read_forms"]) not in (list, tuple):
            raise _SourceProjectionError("invalid-source-shape")
        return self.record(value, _SOURCE_FACT, extra=extra)

    def fact_map(self, value):
        if type(value) is not dict:
            raise _SourceProjectionError("invalid-source-shape")
        if 2 * len(value) > ORIGINAL_LIMITS["entries"] - self.cells:
            raise _SourceProjectionError("metadata-bound")
        self.account(cells=1, size=2, storage=sys.getsizeof({}))
        result = {} if self.build else None
        for index, (name, fact) in enumerate(value.items()):
            self.account(size=1 + bool(index), storage=_SOURCE_MAP_CELL)
            self.value(name, "name")
            field = self.fact(fact)
            if result is not None:
                result[name] = field
        return result

    def value(self, value, kind):
        self.checkpoint()
        if type(kind) is tuple:
            tag, argument = kind
            if tag == "optional":
                return self.scalar(value, "null") if value is None else self.value(value, argument)
            if tag == "literal":
                if type(value) is not type(argument) or value != argument:
                    raise _SourceProjectionError("unsupported-source-shape")
                return self.text(value)
            if tag == "enum":
                if type(value) is not str or value not in argument:
                    raise _SourceProjectionError("unsupported-source-shape")
                return self.text(value)
            if tag in {"array", "exact-array"}:
                return self.array(value, argument, exact=tag == "exact-array")
            raise _SourceProjectionError("unsupported-source-shape")
        if kind in {"integer", "positive", "flags", "boolean"}:
            return self.scalar(value, kind)
        if kind == "name":
            return self.text(value, maximum=128, pattern=r"\.?[A-Za-z_][A-Za-z0-9_]*")
        if kind in {"path", "scope", "target"}:
            pattern = r"[A-Za-z0-9_./+%-]+" if kind == "target" else r"[A-Za-z0-9_./+-]+"
            result = self.text(value, pattern=pattern)
            if (
                re.search(r"(^|/)\.\.(/|$)", value)
                or kind != "target" and (
                    value.startswith("/") or value.endswith("/") or "//" in value
                    or re.search(r"(^|/)\.(/|$)", value)
                )
                or kind == "target" and value.count("%") > 1
            ):
                raise _SourceProjectionError("invalid-source-shape")
            return result
        if kind == "type-name":
            if type(value) is str and len(value) > 512:
                raise _SourceProjectionError("failure-type-over-bound")
            return self.text(
                value, maximum=512,
                pattern=None if type(value) is str and value == _SOURCE_TYPE_UNAVAILABLE else r"[A-Za-z_][A-Za-z0-9_]*",
            )
        if kind == "site":
            return self.site(value)
        if kind == "writer-site":
            return self.scalar(None, "null") if value is None else self.site(value, positional=self.upstream)
        if kind == "metadata":
            return self.metadata(value)
        if kind == "fact-map":
            return self.fact_map(value)
        if kind == "binding":
            return self.record(value, (
                ("origin", ("enum", ("default", "environment", "file", "command line", "override", "unknown", "undefined"))),
                ("flavor", ("enum", ("simple", "recursive", "unknown", "undefined"))),
            ))
        if kind == "occurrence":
            self.variant(value, len(_SOURCE_OCCURRENCE))
            return self.record(value, _SOURCE_AGGREGATE if "status" in value else _SOURCE_OCCURRENCE)
        if kind == "writer":
            self.variant(value, 5)
            writer = value.get("kind")
            kinds = ("unproved-local-binder-analysis", "local-binders", "unproved-eval-writer", "eval-writer")
            if type(writer) is not str or writer not in kinds:
                raise _SourceProjectionError("unsupported-source-shape")
            schema = (
                ("kind", ("enum", kinds)), ("path", "path"),
                ("stream_position", "integer"), ("site", "writer-site"),
            )
            if writer == "local-binders":
                schema += (("names", ("array", "name")),)
            elif writer == "eval-writer":
                schema += (("name", "name"),)
            return self.record(value, schema)
        if kind == "association":
            self.variant(value, 4)
            association = value.get("kind")
            if type(association) is not str or association not in {"unproved", "obligation", "recipe"}:
                raise _SourceProjectionError("unsupported-source-shape")
            schema = (("kind", ("literal", association)),)
            if association != "unproved":
                schema += (("target", "target"), ("ordinal", "integer"), ("job", (
                    "optional", "positive"
                ) if association == "obligation" else "positive"))
                if association == "obligation" and value.get("job") is not None:
                    raise _SourceProjectionError("invalid-source-shape")
            return self.record(value, schema)
        if kind == "failure":
            return self.record(value, _SOURCE_FAILURE)
        if kind == "unavailable":
            return self.record(value, (("stage", ("enum", _SOURCE_STAGES)), *_SOURCE_FAILURE))
        if kind == "publication-errors":
            return self.record(value, tuple((name, ("optional", "failure")) for name in (
                "formatter", "projection", "validation",
            )))
        if kind == "member":
            self.keys(value, _SOURCE_MEMBER)
            if type(value["format"]) is not str or value["format"] != SOURCE_REFUSAL_FORMAT:
                raise _SourceProjectionError("unsupported-projection-format")
            if (
                type(value["source_revision"]) is not str
                or GRAPH != SOURCE_REFUSAL_REVISION or value["source_revision"] != SOURCE_REFUSAL_REVISION
            ):
                raise _SourceProjectionError("unsupported-source-revision")
            status = value["status"]
            if (
                type(status) is not str or status not in {"available", "unavailable"}
                or status == "available" and (value["metadata"] is None or value["unavailable"] is not None)
                or status == "unavailable" and (value["metadata"] is not None or value["unavailable"] is None)
            ):
                raise _SourceProjectionError("invalid-source-shape")
            return self.record(value, _SOURCE_MEMBER)
        raise _SourceProjectionError("unsupported-source-shape")


def source_primary(error, binding):
    if type(binding) is not tuple or len(binding) != 3 or type(error) is not binding[0]:
        return False
    arguments = BaseException.__getattribute__(error, "args")
    return (
        type(arguments) is tuple and len(arguments) == 1 and type(arguments[0]) is str
        and arguments[0] in SOURCE_REFUSAL_MESSAGES
    )


def source_exception_type(error):
    name = type.__getattribute__(type(error), "__name__")
    if type(name) is not str or len(name) > 512:
        return _SOURCE_TYPE_UNAVAILABLE
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else None


def source_publication_failure(error, reason, *, primary=None):
    internal = type(error) is _SourceProjectionError
    detail = {
        "reason": error.reason if internal and type(error.reason) is str and error.reason in _SOURCE_REASONS else reason,
        "failure_type": None if internal else source_exception_type(error),
    }
    if error is not primary:
        traceback.clear_frames(error.__traceback__)
        error.__traceback__ = None
        error.__cause__ = error.__context__ = None
    return detail


def _source_envelope(metadata=None, unavailable=None, *, formatter=None, projection=None, validation=None):
    return {
        "format": SOURCE_REFUSAL_FORMAT, "source_revision": SOURCE_REFUSAL_REVISION,
        "status": "available" if unavailable is None else "unavailable",
        "metadata": metadata, "unavailable": unavailable,
        "omitted_by_contract": list(SOURCE_REFUSAL_OMISSIONS),
        "publication_errors": {"formatter": formatter, "projection": projection, "validation": validation},
    }


def validate_source_refusal(value):
    measured = _SourceProjection()
    measured.value(value, "member")
    return value


def unavailable_source_refusal(stage, reason, *, failure_type=None, formatter=None, projection=None, validation=None):
    result = _source_envelope(
        unavailable={"stage": stage, "reason": reason, "failure_type": failure_type},
        formatter=formatter, projection=projection, validation=validation,
    )
    _SourceProjection().value(result, "member")
    return result


def _project_source_refusal(error, revision, deadline, formatter):
    if type(revision) is not str or revision != SOURCE_REFUSAL_REVISION or GRAPH != SOURCE_REFUSAL_REVISION:
        raise _SourceProjectionError("unsupported-source-revision")
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= 0:
        raise _SourceProjectionError("deadline")
    values = []
    for name in ("source_attribution", "source_attribution_unavailable", "source_attribution_failure_type"):
        try:
            value = BaseException.__getattribute__(error, name)
        except AttributeError:
            value = _SOURCE_ABSENT
        values.append(value)
    data, stage, failure_type = values
    if data is not _SOURCE_ABSENT:
        if stage is not _SOURCE_ABSENT or failure_type is not _SOURCE_ABSENT:
            raise _SourceProjectionError("invalid-source-shape")
        envelope = _source_envelope(data, formatter=formatter)
    elif stage is _SOURCE_ABSENT and failure_type is _SOURCE_ABSENT:
        envelope = _source_envelope(
            unavailable={"stage": "selection", "reason": "not-attached", "failure_type": None},
            formatter=formatter,
        )
    elif type(stage) is str and stage in _SOURCE_STAGES[1:4] and failure_type is not _SOURCE_ABSENT:
        envelope = _source_envelope(
            unavailable={"stage": stage, "reason": "upstream-unavailable", "failure_type": failure_type},
            formatter=formatter,
        )
    else:
        raise _SourceProjectionError("invalid-source-shape")
    measured = _SourceProjection(upstream=True, deadline=deadline)
    measured.value(envelope, "member")
    extent = measured.cells, measured.size, measured.storage
    builder = _SourceProjection(upstream=True, build=True, admitted=extent, deadline=deadline)
    result = builder.value(envelope, "member")
    if (builder.cells, builder.size, builder.storage) != extent:
        raise _SourceProjectionError("invalid-source-shape")
    return validate_source_refusal(result)


def project_source_refusal(error, *, revision, deadline, formatter=None):
    try:
        return _project_source_refusal(error, revision, deadline, formatter)
    except BaseException as failure:
        detail = source_publication_failure(failure, "projection-exception", primary=error)
    return unavailable_source_refusal(
        "projection", detail["reason"], failure_type=detail["failure_type"],
        formatter=formatter, projection=detail,
    )


def retain_source_refusal(value):
    try:
        return validate_source_refusal(value)
    except BaseException as failure:
        detail = source_publication_failure(failure, "invalid-wire-metadata")
    return unavailable_source_refusal(
        "wire-validation", "invalid-wire-metadata", validation=detail,
    )
