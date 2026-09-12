"""Closed experiment identity and external containment policy."""

from __future__ import annotations

import errno
import hashlib
import json
import re


REPOSITORY = "laqieer/fireemblem8-expansion"
BRANCH = "calibration/issue-180-ci-baseline-1"
WORKFLOW = ".github/workflows/issue180-ci-baseline.yml"
BASE = "473c6c22a1ba340429072dac0a187de76eec1cc0"
GRAPH = "3d78dbc26f2df16cb50b0684042d84a98fbc2ea9"
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
    "result.json", "report.json",
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
    "syscalls": 2_000_000,
}
RELAXED = {
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
    "pending_bytes": "Cumulative requests/plans; this also relaxes implicit whole-record/plan limits absent #260.",
    "control_bytes": "Cumulative control/raw/decoded/replay traffic; fixed file/buffer guards remain.",
    "sandbox_bytes": "Cumulative writes; individual file and external filesystem bounds remain.",
}
RETAINED = {
    "seconds": "One original-duration absolute graph deadline, also independently enforced outside the worker.",
    "processes": "Live guest capacity is coupled to funded VM and nested process reservations.",
    "pending": "Mixed live-request, gitlink and runtime-source admission count.",
    "created_files": "Mixed cumulative creation and publication/output-tree cardinality guard.",
    "entries": "Mixed source inventory, metadata, regex and observation cardinality guard.",
    "file_bytes": "Genuine file/message/decoded-frame admission and guest RLIMIT_FSIZE boundary.",
    "process_output_bytes": "Genuine per-process captured-output boundary.",
    "address_space_bytes": "Original funded guest VM pool and independent regex-worker AS bound.",
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
    }


def profile_manifest(original):
    if original != ORIGINAL_LIMITS or set(RELAXED) | set(RETAINED) != set(original):
        raise GuardError("pinned source limit fields/defaults differ from the classified experiment")
    if set(RELAXED) & set(RETAINED):
        raise GuardError("ambiguous limit classification")
    return {
        name: {
            "original": value,
            "diagnostic": POLICY_SENTINEL if name in RELAXED else value,
            "classification": "relaxed cumulative policy" if name in RELAXED else "retained boundary",
            "reason": (RELAXED if name in RELAXED else RETAINED)[name],
        }
        for name, value in original.items()
    }


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
