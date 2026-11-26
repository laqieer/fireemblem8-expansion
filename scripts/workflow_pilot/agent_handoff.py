#!/usr/bin/env python3
"""Exact-SHA local handoffs. Publication remains the delivery coordinator's job."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from scripts.workflow_pilot import coordinator_observations as observations
from scripts.workflow_pilot import raw_diff_check as git


SCHEMA_VERSION = 3
COPILOT_TRAILER = "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
PROHIBITED_REMOTE_ACTIONS = (
    "comment", "create_remote_ref", "dispatch_ci", "merge", "open_pull_request",
    "push", "request_review", "update_pull_request",
)
STATES = ("assignment_sent", "assignment_received", "progressing", "committed", "handed_off")
CHECK_CONTRACTS = {"git-diff-check", "protocol-json", "coordinator-check"}
METRICS = ("rom_bytes", "ram_bytes", "protocol_changes")
HOST_ONLY = ("docs/", "scripts/workflow_pilot/", "scripts/docs_check_tests/")
HOST_FILES = {".github/copilot-instructions.md", ".github/skills/development-workflow/SKILL.md"}
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_OPEN_PULL_REQUESTS = 1000
MAX_RETAINED_WATCHERS = 32768
MAX_ACTIVE_WATCHERS = 128
LOGICAL_RSS_UNAVAILABLE = "native task runtime does not expose per-owner RSS"
HandoffDataError = observations.ObservationError
require = observations.require
load_json = observations.load_json
normalized_json = observations.json_bytes
now = observations.utc_now
timestamp = observations.timestamp


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()), f"expected fields: {names}")
    return value


def text(value, *, nullable=False, allow_empty=False, maximum=16384, pattern=None):
    if value is None and nullable:
        return
    minimum = 0 if allow_empty else 1
    require(type(value) is str and minimum <= len(value) <= maximum,
            "invalid bounded string")
    require(not any(0xD800 <= ord(char) <= 0xDFFF for char in value), "invalid Unicode scalar")
    if pattern is not None:
        require(pattern.fullmatch(value) is not None, "invalid string format")


def integer(value, *, minimum=0, maximum=2**63 - 1, nullable=False):
    if value is None and nullable:
        return
    require(type(value) is int and minimum <= value <= maximum, "invalid bounded integer")


def boolean(value):
    require(type(value) is bool, "expected boolean")


def choice(value, values):
    require(value in values if type(value) in (str, type(None)) else False, "invalid enum")


def items(value, *, maximum=128, minimum=0, unique=False):
    require(type(value) is list and minimum <= len(value) <= maximum, "invalid bounded list")
    if unique:
        require(len({normalized_json(item) for item in value}) == len(value), "duplicate list item")
    return value


def ids(value, *, maximum=128, minimum=0):
    for item in items(value, maximum=maximum, minimum=minimum, unique=True):
        text(item, maximum=128, pattern=ID_RE)


def named_records(value, *, maximum=32, minimum=0):
    require(type(value) is dict and minimum <= len(value) <= maximum, "invalid named record collection")
    for key in value:
        text(key, maximum=128, pattern=ID_RE)
    return value.items()


def path(value, *, prefix=False):
    text(value, maximum=4096)
    require(not value.startswith("/") and "\\" not in value and "\0" not in value,
            "scope/input path must be relative")
    parts = value.removesuffix("/").split("/")
    require(all(part not in {"", ".", ".."} for part in parts), "noncanonical repository path")
    require(prefix or not value.endswith("/"), "file path cannot be a directory prefix")


def absolute_path(value):
    text(value, maximum=4096)
    require(Path(value).is_absolute() and str(Path(value)) == value and ".." not in Path(value).parts
            and value != "/" and not value.startswith("//") and "\0" not in value,
            "expected canonical absolute path")


def paths_overlap(left, right):
    return left == right or left in right.parents or right in left.parents


def sha(value):
    text(value, maximum=40, pattern=SHA_RE)


def validate_check_definition(check):
    fields(check, "contract evidence_id inputs")
    text(check["evidence_id"], maximum=128, pattern=ID_RE)
    choice(check["contract"], CHECK_CONTRACTS)
    for name in items(check["inputs"], maximum=16, unique=True):
        path(name)
    require(check["contract"] == "protocol-json" or not check["inputs"],
            "only a protocol-json check accepts repository input paths")
    require(check["contract"] != "protocol-json" or check["inputs"],
            "protocol-json needs explicit inputs")


def validate_required_checks(value):
    protocol_inputs, evidence = set(), set()
    for _check_id, check in named_records(value, minimum=1):
        validate_check_definition(check)
        require(protocol_inputs.isdisjoint(check["inputs"]), "overlapping protocol inputs")
        protocol_inputs.update(check["inputs"])
        evidence.add(check["evidence_id"])
    require(any(check["contract"] == "git-diff-check" for check in value.values()),
            "a real raw Git check is required")
    return evidence


def validate_assignment(value):
    native = isinstance(value, dict) and "native" in value
    fields(value, "schema_version repository id issue pull_request owner_id session_id dispatch_id "
           "assigned_parent_sha expected_branch allowed_worktree allowed_scope upstream_inputs "
           "finding_ids acceptance_criteria required_checks budgets max_lifetime_seconds "
           "max_peak_rss_bytes prohibited_remote_actions predecessor_id kind"
           + (" native" if native else ""))
    require(value["schema_version"] == SCHEMA_VERSION and type(value["schema_version"]) is int,
            "handoff schema_version must be 3")
    text(value["repository"], maximum=256, pattern=observations.REPOSITORY_RE)
    text(value["id"], maximum=128, pattern=ID_RE)
    for name in ("owner_id", "session_id", "dispatch_id"):
        text(value[name], nullable=native, maximum=128, pattern=ID_RE)
    if native:
        task = fields(value["native"], "root_session_id events_path prompt")
        text(task["root_session_id"], maximum=128, pattern=ID_RE)
        absolute_path(task["events_path"])
        text(task["prompt"])
        require(task["prompt"].count(f"[handoff:{value['id']}]") == 1,
                "native prompt must contain the assignment marker exactly once")
        require(value["dispatch_id"] is not None or value["owner_id"] is None,
                "native owner cannot precede dispatch")
        require(value["owner_id"] is not None or value["session_id"] is None,
                "native session cannot precede owner")
    integer(value["issue"], minimum=1)
    integer(value["pull_request"], minimum=1, nullable=True)
    sha(value["assigned_parent_sha"])
    text(value["expected_branch"], maximum=256)
    absolute_path(value["allowed_worktree"])
    for scope in items(value["allowed_scope"], maximum=256, minimum=1, unique=True):
        path(scope, prefix=True)
    for revision in items(value["upstream_inputs"], maximum=16, unique=True):
        sha(revision)
    ids(value["finding_ids"])
    evidence = set()
    for _criterion_id, criterion in named_records(value["acceptance_criteria"], minimum=1):
        fields(criterion, "text evidence_ids")
        text(criterion["text"])
        ids(criterion["evidence_ids"], maximum=32, minimum=1)
        evidence.update(criterion["evidence_ids"])
    evidence.difference_update(validate_required_checks(value["required_checks"]))
    require(not evidence, "criterion has no required evidence producer")
    fields(value["budgets"], "changed_lines rom_bytes ram_bytes protocol_changes")
    for limit in value["budgets"].values():
        integer(limit)
    integer(value["max_lifetime_seconds"], minimum=1, maximum=86400)
    integer(value["max_peak_rss_bytes"], minimum=1)
    actions = items(value["prohibited_remote_actions"], minimum=8, maximum=8, unique=True)
    for action in actions:
        text(action, maximum=128, pattern=ID_RE)
    require(set(actions) == set(PROHIBITED_REMOTE_ACTIONS),
            "incomplete prohibited remote actions")
    text(value["predecessor_id"], nullable=True, maximum=128, pattern=ID_RE)
    choice(value["kind"], {"initial", "review", "replacement"})
    require((value["kind"] == "initial") == (value["predecessor_id"] is None),
            "successor must name its predecessor")
    return value


def validate_result(value):
    fields(value, "schema_version assignment_id assigned_parent_sha result_sha evidence_refs")
    require(type(value["schema_version"]) is int and value["schema_version"] == SCHEMA_VERSION,
            "result schema_version must be 3")
    text(value["assignment_id"], maximum=128, pattern=ID_RE)
    sha(value["assigned_parent_sha"])
    sha(value["result_sha"])
    ids(value["evidence_refs"], maximum=32)
    return value


def load_assignment(file):
    return validate_assignment(load_json(file))


def load_result(file):
    return validate_result(load_json(file))


def parse_assignment(raw):
    return validate_assignment(observations.parse_bytes(raw))


def parse_result(raw):
    return validate_result(observations.parse_bytes(raw))


def validate_process(value):
    fields(value, "pid start_ticks boot_id runtime_handle observed_at state peak_rss_bytes "
           "rss_complete exit_code age_ms")
    integer(value["pid"], minimum=1)
    integer(value["start_ticks"], minimum=1)
    integer(value["age_ms"])
    for name in ("boot_id", "runtime_handle"):
        text(value[name], maximum=128, pattern=ID_RE)
    timestamp(value["observed_at"])
    choice(value["state"], {"running", "exited"})
    integer(value["peak_rss_bytes"], nullable=True)
    boolean(value["rss_complete"])
    integer(value["exit_code"], minimum=-128, maximum=255, nullable=True)
    require(not value["rss_complete"] or value["state"] == "exited"
            and value["exit_code"] is not None and value["peak_rss_bytes"] is not None,
            "complete RSS needs an owned OS wait result")
    require(value["state"] != "running" or value["exit_code"] is None,
            "running process cannot have an exit code")


def validate_terminal_task(value, assignment, entry):
    fields(value, "event_id observed_at root_session_id task_id owner_id session_id head_sha "
           "worktree git_identity allowed_scope success rss_peak_bytes rss_unavailable_reason")
    text(value["event_id"], maximum=128, pattern=ID_RE)
    timestamp(value["observed_at"])
    for name in ("root_session_id", "task_id", "owner_id", "session_id"):
        text(value[name], nullable=True, maximum=128, pattern=ID_RE)
    sha(value["head_sha"])
    absolute_path(value["worktree"])
    boolean(value["success"])
    integer(value["rss_peak_bytes"], nullable=True)
    text(value["rss_unavailable_reason"], maximum=256)
    scope = items(value["allowed_scope"], maximum=256, minimum=1, unique=True)
    for name in scope:
        path(name, prefix=True)
    require("native" in assignment and value["root_session_id"] == assignment["native"]["root_session_id"]
            and value["task_id"] == assignment["dispatch_id"]
            and value["owner_id"] == assignment["owner_id"]
            and assignment["session_id"] is not None
            and value["session_id"] == assignment["session_id"]
            and value["worktree"] == assignment["allowed_worktree"]
            and value["git_identity"] == entry["git_identity"]
            and scope == assignment["allowed_scope"],
            "logical task terminal identity mismatch")
    require(value["rss_peak_bytes"] is None
            and value["rss_unavailable_reason"] == LOGICAL_RSS_UNAVAILABLE,
            "logical task RSS availability mismatch")


def validate_worktree_release(value, assignment, entry):
    fields(value, "release_id observed_at assignment_id task_id owner_id session_id head_sha "
           "worktree git_identity validation_worktree validation_identity source "
           "path_absent unregistered")
    text(value["release_id"], maximum=128, pattern=ID_RE)
    timestamp(value["observed_at"])
    for name in ("assignment_id", "task_id", "owner_id", "session_id"):
        text(value[name], maximum=128, pattern=ID_RE)
    sha(value["head_sha"])
    absolute_path(value["worktree"])
    absolute_path(value["validation_worktree"])
    for name in ("git_identity", "validation_identity"):
        identity = fields(value[name], "worktree git_dir common_dir device inode")
        for path_name in ("worktree", "git_dir", "common_dir"):
            absolute_path(identity[path_name])
        integer(identity["device"])
        integer(identity["inode"], minimum=1)
    text(value["source"], maximum=64)
    require(value["source"] == "git-worktree-remove"
            and value["path_absent"] is True and value["unregistered"] is True,
            "logical worktree release was not observed")
    terminal = entry.get("terminal_task")
    require(terminal is not None and entry["process"] is None
            and terminal["success"] and entry["interruption"] is None
            and entry["result"] is not None
            and entry["events"] and entry["events"][-1]["state"] == "handed_off"
            and not entry["remote_actions"]
            and value["assignment_id"] == assignment["id"]
            and value["task_id"] == terminal["task_id"]
            and value["owner_id"] == terminal["owner_id"] == assignment["owner_id"]
            and value["session_id"] == terminal["session_id"] == assignment["session_id"]
            and value["head_sha"] == terminal["head_sha"]
            and value["worktree"] == terminal["worktree"] == assignment["allowed_worktree"]
            and value["git_identity"] == terminal["git_identity"] == entry["git_identity"]
            and value["release_id"] == worktree_release_id(assignment["id"])
            and not paths_overlap(Path(value["validation_worktree"]), Path(value["worktree"]))
            and value["validation_identity"]["worktree"] == value["validation_worktree"]
            and value["validation_identity"]["common_dir"] == value["git_identity"]["common_dir"]
            and value["validation_identity"]["git_dir"] != value["git_identity"]["git_dir"]
            and value["head_sha"] == entry["result"]["result_sha"],
            "logical worktree release identity mismatch")
    require(timestamp(value["observed_at"]) >= timestamp(terminal["observed_at"]),
            "logical worktree release predates task terminal")
    if entry["result"] is not None:
        require(value["head_sha"] == entry["result"]["result_sha"],
                "logical task terminal head mismatch")


def worktree_release_id(assignment_id):
    digest = hashlib.sha256(assignment_id.encode("utf-8")).hexdigest()
    return f"worktree-release:{digest}"


def validate_availability(value):
    fields(value, "mode observed_at valid_until autostop_enabled stop_on_disconnect plan")
    choice(value["mode"], {"always-on", "plan"})
    timestamp(value["observed_at"])
    timestamp(value["valid_until"])
    for name in ("autostop_enabled", "stop_on_disconnect"):
        if value["mode"] != "plan" or value[name] is not None:
            boolean(value[name])
    text(value["plan"], nullable=True)
    require(value["mode"] != "plan"
            or (value["plan"] is not None and bool(value["plan"].strip())),
            "availability plan is missing")


def validate_clock(value):
    fields(value, "at boot_id monotonic_ns boottime_ns")
    timestamp(value["at"])
    text(value["boot_id"], maximum=128, pattern=ID_RE)
    integer(value["monotonic_ns"])
    integer(value["boottime_ns"])


def validate_run(value):
    fields(value, "repository run_id attempt head_sha workflow_id status conclusion observed_at")
    text(value["repository"], maximum=256, pattern=observations.REPOSITORY_RE)
    for name in ("run_id", "attempt", "workflow_id"):
        integer(value[name], minimum=1)
    sha(value["head_sha"])
    choice(value["status"], {"completed", "queued", "in_progress", "waiting", "pending", "requested"})
    choice(value["conclusion"], {None, "success", "failure", "cancelled", "timed_out",
                                "action_required", "neutral", "skipped", "stale", "startup_failure"})
    require((value["status"] == "completed") == (value["conclusion"] is not None),
            "incoherent run status/conclusion")
    timestamp(value["observed_at"])


def validate_check(value):
    fields(value, "id evidence_id contract parent_sha result_sha worktree started_at completed_at "
           "exit_code pid peak_rss_bytes measurements detail")
    for name in ("id", "evidence_id"):
        text(value[name], maximum=128, pattern=ID_RE)
    choice(value["contract"], CHECK_CONTRACTS)
    sha(value["parent_sha"])
    sha(value["result_sha"])
    absolute_path(value["worktree"])
    timestamp(value["started_at"])
    if value["completed_at"] is not None:
        timestamp(value["completed_at"])
        require(timestamp(value["started_at"]) <= timestamp(value["completed_at"]), "check times reversed")
    else:
        require(value["exit_code"] is None, "unfinished check cannot claim an exit code")
    integer(value["exit_code"], minimum=-128, maximum=255, nullable=True)
    integer(value["pid"], minimum=1, nullable=True)
    integer(value["peak_rss_bytes"], nullable=True)
    fields(value["measurements"], "rom_bytes ram_bytes protocol_changes")
    for measurement in value["measurements"].values():
        integer(measurement, nullable=True)
    text(value["detail"], nullable=True)


def validate_verdict(value):
    fields(value, "assignment_id result_sha observed_at local_outcome handoff_ready "
           "rejection_codes changed_lines task_commits imported_paths ci_state")
    text(value["assignment_id"], maximum=128, pattern=ID_RE)
    sha(value["result_sha"])
    timestamp(value["observed_at"])
    choice(value["local_outcome"], {"accepted", "rejected", "in_progress", "interrupted"})
    boolean(value["handoff_ready"])
    ids(value["rejection_codes"])
    integer(value["changed_lines"], nullable=True)
    for commit in items(value["task_commits"], unique=True):
        sha(commit)
    for name in items(value["imported_paths"], maximum=256, unique=True):
        path(name)
    choice(value["ci_state"], {"absent", "pending", "success", "failure", "unknown"})
    require(value["handoff_ready"] == (value["local_outcome"] == "accepted")
            and (value["local_outcome"] != "accepted" or not value["rejection_codes"]),
            "incoherent handoff verdict")


def validate_state(value):
    fields(value, "schema_version repository coordinator_id availability clock assignments watchers"
           + (" candidates" if isinstance(value, dict) and "candidates" in value else "")
           + (" safety_publication" if isinstance(value, dict) and "safety_publication" in value else ""))
    if "candidates" in value:
        from .adaptive_gate import validate_candidate_records
        validate_candidate_records(value["candidates"])
    if "safety_publication" in value:
        from .adaptive_gate import validate_safety_publication
        validate_safety_publication(value["safety_publication"])
    require(type(value["schema_version"]) is int and value["schema_version"] == SCHEMA_VERSION,
            "state schema_version must be 3")
    text(value["repository"], maximum=256, pattern=observations.REPOSITORY_RE)
    text(value["coordinator_id"], maximum=128, pattern=ID_RE)
    if "safety_publication" in value and value["safety_publication"]["control"] is not None:
        require(value["safety_publication"]["control"]["repository"] == value["repository"],
                "safety publication control repository mismatch")
    for candidate in value.get("candidates", ()):
        if "intake_control" in candidate:
            require(candidate["intake_control"]["repository"] == value["repository"],
                    "candidate intake control repository mismatch")
    validate_availability(value["availability"])
    validate_clock(value["clock"])
    at = now()
    require(timestamp(value["clock"]["at"]) <= timestamp(at), "future clock observation")
    names, owners, sessions, dispatches, owner_processes = set(), set(), set(), set(), set()
    native_tasks, native_sources, legacy_sources = set(), set(), set()
    active = []
    for entry in items(value["assignments"]):
        native = isinstance(entry, dict) and "native_binding" in entry
        fields(entry, "assignment assigned_at git_identity events process checks result validation "
               "interruption cursors remote_actions closed_at coordination_turns"
               + (" terminal_task" if isinstance(entry, dict) and "terminal_task" in entry else "")
               + (" worktree_release" if isinstance(entry, dict) and "worktree_release" in entry else "")
               + (" native_binding" if native else ""))
        a = validate_assignment(entry["assignment"])
        require(native == ("native" in a), "native assignment/binding mismatch")
        require(a["repository"] == value["repository"], "assignment repository mismatch")
        require(a["id"] not in names
                and (a["owner_id"] is None or a["owner_id"] not in owners)
                and (a["session_id"] is None or a["session_id"] not in sessions)
                and (a["dispatch_id"] is None or a["dispatch_id"] not in dispatches),
                "duplicate/reused owner, session, assignment or dispatch")
        names.add(a["id"])
        if a["owner_id"] is not None:
            owners.add(a["owner_id"])
        if a["session_id"] is not None:
            sessions.add(a["session_id"])
        if a["dispatch_id"] is not None:
            dispatches.add(a["dispatch_id"])
        timestamp(entry["assigned_at"])
        integer(entry["coordination_turns"])
        fields(entry["git_identity"], "worktree git_dir common_dir device inode")
        for name in ("worktree", "git_dir", "common_dir"):
            absolute_path(entry["git_identity"][name])
        integer(entry["git_identity"]["device"])
        integer(entry["git_identity"]["inode"], minimum=1)
        event_names, event_ids = [], []
        last = timestamp(entry["assigned_at"])
        for event in items(entry["events"], maximum=5):
            fields(event, "state at source_id")
            choice(event["state"], set(STATES))
            text(event["source_id"], maximum=128)
            current = timestamp(event["at"])
            require(last <= current <= timestamp(at), "lifecycle observation chronology invalid")
            event_names.append(event["state"])
            event_ids.append(event["source_id"])
            last = current
        require(tuple(event_names) == STATES[:len(event_names)], "incomplete lifecycle transition")
        require(len(set(event_ids)) == len(event_ids), "duplicate lifecycle source event")
        if native:
            binding = fields(entry["native_binding"],
                             "started_id interaction_id parent_task_id owner_id session_id last_event_at")
            for name in ("started_id", "interaction_id", "parent_task_id"):
                text(binding[name], nullable=True, maximum=128, pattern=ID_RE)
            for name in ("owner_id", "session_id"):
                text(binding[name], nullable=True, maximum=128, pattern=ID_RE)
            require(binding["owner_id"] == a["owner_id"] and binding["session_id"] == a["session_id"],
                    "native owner/session identity binding mismatch")
            require((a["dispatch_id"] is not None) == ("assignment_sent" in event_names),
                    "native dispatch observation mismatch")
            require((a["dispatch_id"] is not None) == (binding["last_event_at"] is not None),
                    "native event clock mismatch")
            if binding["last_event_at"] is not None:
                require(timestamp(entry["assigned_at"]) <= timestamp(binding["last_event_at"]) <= timestamp(at),
                        "native event chronology invalid")
            require((a["owner_id"] is not None) == (binding["started_id"] is not None)
                    == (binding["interaction_id"] is not None), "native owner observation mismatch")
            require((binding["parent_task_id"] is not None) == ("assignment_received" in event_names),
                    "native receipt observation mismatch")
            require(binding["parent_task_id"] is None or a["owner_id"] is not None,
                    "native receipt lacks its owner")
            require((a["session_id"] is not None) == ("progressing" in event_names),
                    "native session observation mismatch")
            if binding["parent_task_id"] is not None:
                require(binding["parent_task_id"] not in native_tasks, "native receipt task reused")
                native_tasks.add(binding["parent_task_id"])
            source_ids = set(event_ids)
            if binding["started_id"] is not None:
                require(binding["started_id"] not in source_ids, "native start source reused")
                source_ids.add(binding["started_id"])
            require(source_ids.isdisjoint(native_sources | legacy_sources), "native lifecycle source reused")
            native_sources.update(source_ids)
        else:
            require(set(event_ids).isdisjoint(native_sources), "native lifecycle source reused")
            legacy_sources.update(event_ids)
            if "assignment_received" in event_names:
                require(a["dispatch_id"] not in native_tasks, "native receipt task reused")
                native_tasks.add(a["dispatch_id"])
        if entry["process"] is not None:
            validate_process(entry["process"])
            require(entry["process"]["runtime_handle"] == a["owner_id"], "owner process handle mismatch")
            identity = tuple(entry["process"][key] for key in ("boot_id", "pid", "start_ticks"))
            require(identity not in owner_processes, "implementation process reused")
            owner_processes.add(identity)
        if entry["validation"] is not None:
            validate_verdict(entry["validation"])
            require(entry["validation"]["assignment_id"] == a["id"], "verdict assignment mismatch")
        if entry["result"] is not None:
            validate_result(entry["result"])
            require(entry["result"]["assignment_id"] == a["id"], "delivered assignment mismatch")
        terminal_task = entry.get("terminal_task")
        if terminal_task is not None:
            validate_terminal_task(terminal_task, a, entry)
            if entry["result"] is not None:
                require(terminal_task["head_sha"] == entry["result"]["result_sha"],
                        "logical task terminal head does not match delivery")
            terminal_at = timestamp(terminal_task["observed_at"])
            require(native and terminal_at >= last
                    and timestamp(entry["native_binding"]["last_event_at"]) <= terminal_at
                    and terminal_at <= timestamp(at),
                    "logical task terminal chronology invalid")
            require(terminal_task["event_id"] not in event_ids
                    and terminal_task["event_id"] != (entry["native_binding"]["started_id"] if native else None)
                    and terminal_task["event_id"] not in native_sources | legacy_sources,
                    "logical task terminal source reused")
            if entry["process"] is None and entry["closed_at"] is not None:
                require(timestamp(entry["closed_at"]) >= terminal_at,
                        "owner closed before logical task terminal")
            native_sources.add(terminal_task["event_id"])
        worktree_release = entry.get("worktree_release")
        if worktree_release is not None:
            validate_worktree_release(worktree_release, a, entry)
            released_at = timestamp(worktree_release["observed_at"])
            require(released_at <= timestamp(at)
                        and (entry["closed_at"] is None
                             or timestamp(entry["closed_at"]) >= released_at),
                        "logical worktree release chronology invalid")
        if entry["process"] is None:
            require(entry["closed_at"] is None or worktree_release is not None,
                        "logical ownership release is unavailable")
        owner_lifetime_ms(entry, at)
        checks = set()
        for check in items(entry["checks"], maximum=32):
            validate_check(check)
            require(check["id"] not in checks, "duplicate check")
            checks.add(check["id"])
        if entry["interruption"] is not None:
            interruption = fields(entry["interruption"], "at reason oom_evidence worktree head "
                                  "dirty_paths lock_reason retained_data_sha256")
            timestamp(interruption["at"])
            choice(interruption["reason"], {"sigkill", "timeout", "rss", "process-exit"})
            text(interruption["oom_evidence"], nullable=True)
            absolute_path(interruption["worktree"])
            sha(interruption["head"])
            for name in items(interruption["dirty_paths"], maximum=256, unique=True):
                path(name)
            text(interruption["lock_reason"], maximum=256)
            text(interruption["retained_data_sha256"], maximum=64, pattern=DIGEST_RE)
            require(entry["closed_at"] is not None
                    and timestamp(interruption["at"]) == timestamp(entry["closed_at"]),
                    "interruption must match owner close")
        cursor_paths = set()
        for cursor in items(entry["cursors"], maximum=4):
            fields(cursor, "path device inode offset session_id")
            absolute_path(cursor["path"])
            require(cursor["path"] not in cursor_paths, "duplicate log cursor")
            cursor_paths.add(cursor["path"])
            for name in ("device", "inode", "offset"):
                integer(cursor[name])
            text(cursor["session_id"], nullable=True, maximum=128)
        if native:
            require(len(entry["cursors"]) == 1
                    and entry["cursors"][0]["path"] == a["native"]["events_path"]
                    and entry["cursors"][0]["session_id"] == a["native"]["root_session_id"],
                    "native root log cursor mismatch")
        for action in items(entry["remote_actions"], unique=True):
            fields(action, "id action at")
            text(action["id"], maximum=128, pattern=ID_RE)
            choice(action["action"], set(PROHIBITED_REMOTE_ACTIONS))
            timestamp(action["at"])
        if entry["closed_at"] is not None:
            require(timestamp(entry["closed_at"]) >= last, "owner closed before lifecycle observation")
        else:
            reserved_worktrees = {a["allowed_worktree"]}
            if worktree_release is not None:
                reserved_worktrees.add(worktree_release["validation_worktree"])
            active.append((a, reserved_worktrees))
    releases = [
        (entry["assignment"]["allowed_worktree"], timestamp(entry["worktree_release"]["observed_at"]),
         entry["assignment"]["id"])
        for entry in value["assignments"] if entry.get("worktree_release") is not None
    ]
    for released_path, released_at, release_owner in releases:
        for entry in value["assignments"]:
            assignment = entry["assignment"]
            if (assignment["id"] != release_owner
                    and paths_overlap(Path(assignment["allowed_worktree"]), Path(released_path))):
                require(timestamp(entry["assigned_at"]) <= released_at,
                        "released logical worktree cannot be reassigned")
    for index, (left, left_worktrees) in enumerate(active):
        for right, right_worktrees in active[index + 1:]:
            require(left["issue"] != right["issue"]
                    and (left["pull_request"] is None or left["pull_request"] != right["pull_request"])
                    and not any(paths_overlap(Path(left_path), Path(right_path))
                                for left_path in left_worktrees for right_path in right_worktrees),
                    "overlapping active owners")
    successors, initial_issues, initial_prs = set(), set(), set()
    by_id = {entry["assignment"]["id"]: entry for entry in value["assignments"]}
    for entry in value["assignments"]:
        a = entry["assignment"]
        if a["predecessor_id"] is None:
            require(a["issue"] not in initial_issues
                    and (a["pull_request"] is None or a["pull_request"] not in initial_prs),
                    "duplicate initial issue/PR lineage")
            initial_issues.add(a["issue"])
            if a["pull_request"] is not None:
                initial_prs.add(a["pull_request"])
            continue
        previous = by_id.get(a["predecessor_id"])
        require(previous is not None and a["predecessor_id"] not in successors,
                "missing predecessor or multiple successors")
        successors.add(a["predecessor_id"])
        require(previous["closed_at"] is not None
                and timestamp(entry["assigned_at"]) > timestamp(previous["closed_at"]),
                "successor overlaps previous owner")
        previous_assignment = previous["assignment"]
        require(previous_assignment["issue"] == a["issue"]
                and (previous_assignment["pull_request"] is None
                     or previous_assignment["pull_request"] == a["pull_request"]),
                "successor context mismatch")
        if a["kind"] == "replacement":
            require(previous["interruption"] is not None and previous["assignment"]["kind"] != "replacement",
                    "replacement requires one original interruption")
            require(a["allowed_worktree"] == previous["assignment"]["allowed_worktree"]
                    and a["assigned_parent_sha"] == previous["interruption"]["head"],
                    "replacement must reuse preserved worktree and observed HEAD")
        else:
            require(previous["validation"] is not None and previous["validation"]["handoff_ready"]
                    and a["assigned_parent_sha"] == previous["validation"]["result_sha"],
                    "review successor needs the previous accepted result")
    watcher_ids, watcher_processes, active_runs = set(), set(), set()
    for watcher in items(value["watchers"], maximum=MAX_RETAINED_WATCHERS):
        fields(watcher, "id coordinator_id run_id attempt head_sha process started_at ended_at "
               "exit_code run query_error")
        text(watcher["id"], maximum=128, pattern=ID_RE)
        text(watcher["coordinator_id"], maximum=128, pattern=ID_RE)
        require(watcher["id"] not in watcher_ids, "duplicate watcher ID")
        watcher_ids.add(watcher["id"])
        for name in ("run_id", "attempt"):
            integer(watcher[name], minimum=1)
        sha(watcher["head_sha"])
        validate_process(watcher["process"])
        require(watcher["process"]["runtime_handle"] == watcher["id"], "watcher process handle mismatch")
        identity = tuple(watcher["process"][key] for key in ("boot_id", "pid", "start_ticks"))
        require(identity not in owner_processes, "implementation owner cannot be the CI watcher")
        require(identity not in watcher_processes, "watcher process reused")
        watcher_processes.add(identity)
        timestamp(watcher["started_at"])
        integer(watcher["exit_code"], minimum=-128, maximum=255, nullable=True)
        text(watcher["query_error"], nullable=True)
        if watcher["ended_at"] is None:
            require(watcher["coordinator_id"] == value["coordinator_id"], "duplicate coordinator")
            require(watcher["process"]["state"] == "running" and watcher["exit_code"] is None,
                    "active watcher needs a running process and no exit code")
            identity = (watcher["run_id"], watcher["attempt"])
            require(identity not in active_runs, "duplicate active watcher")
            require(len(active_runs) < MAX_ACTIVE_WATCHERS, "active watcher limit exceeded")
            active_runs.add(identity)
        else:
            require(timestamp(watcher["ended_at"]) >= timestamp(watcher["started_at"]),
                    "watcher times reversed")
            require(watcher["process"]["state"] == "exited"
                    and watcher["exit_code"] == watcher["process"]["exit_code"],
                    "ended watcher lacks matching OS observation")
        if watcher["run"] is not None:
            validate_run(watcher["run"])
            require(all(watcher["run"][key] == watcher[key]
                        for key in ("run_id", "attempt", "head_sha"))
                    and watcher["run"]["repository"] == value["repository"],
                    "watcher/run identity mismatch")
    return value


def new_state(repository, coordinator_id, availability):
    return validate_state({"schema_version": SCHEMA_VERSION, "repository": repository,
                           "coordinator_id": coordinator_id, "availability": availability,
                           "clock": observations.clock_observation(), "assignments": [], "watchers": []})


def _git(root, *arguments):
    return git.run_git(root, *arguments)


def _ancestor(root, parent, child):
    result = git.run_process(git.git_command(root, "merge-base", "--is-ancestor", parent, child),
                             cwd=root, env=git.git_environment())
    require(result.returncode in (0, 1), "Git ancestry query failed")
    return result.returncode == 0


def list_open_pull_requests(repository):
    text(repository, maximum=256, pattern=observations.REPOSITORY_RE)
    result = git.run_process(
        ["gh", "pr", "list", "--repo", repository, "--state", "open",
         "--limit", str(MAX_OPEN_PULL_REQUESTS),
         "--json", "number,title,body,headRefName,closingIssuesReferences"],
        cwd=observations.ROOT, env=os.environ.copy(), timeout=30,
        max_bytes=observations.MAX_JSON_BYTES,
    )
    require(result.returncode == 0,
            "open pull request discovery failed: " +
            os.fsdecode(result.stderr[:1024]).strip())
    pull_requests = observations.parse_bytes(result.stdout)
    require(type(pull_requests) is list, "open pull request discovery returned a non-list")
    require(len(pull_requests) < MAX_OPEN_PULL_REQUESTS,
            "open pull request discovery reached its 1000-result bound")
    seen = set()
    for pull_request in pull_requests:
        fields(pull_request, "number title body headRefName closingIssuesReferences")
        integer(pull_request["number"], minimum=1)
        require(pull_request["number"] not in seen, "duplicate open pull request identity")
        seen.add(pull_request["number"])
        text(pull_request["title"], maximum=1024)
        text(pull_request["headRefName"], maximum=256)
        text(pull_request["body"], nullable=True, allow_empty=True)
        for reference in items(pull_request["closingIssuesReferences"], maximum=128):
            require(type(reference) is dict and "number" in reference
                    and "repository" in reference, "invalid closing-issue reference")
            integer(reference["number"], minimum=1)
            require(type(reference["repository"]) is dict,
                    "invalid closing-issue repository")
    return pull_requests


def _closing_reference_matches(reference, repository, issue):
    target = reference["repository"]
    if "nameWithOwner" in target:
        name_with_owner = target["nameWithOwner"]
        text(name_with_owner, maximum=256, pattern=observations.REPOSITORY_RE)
    else:
        owner = target.get("owner")
        require(type(owner) is dict, "closing-issue owner is missing")
        text(owner.get("login"), maximum=256)
        text(target.get("name"), maximum=256)
        name_with_owner = owner["login"] + "/" + target["name"]
    return reference["number"] == issue and name_with_owner.casefold() == repository.casefold()


def implementation_pull_request_matches(repository, issue, pull_request):
    number = str(issue)
    sources = []
    if any(_closing_reference_matches(reference, repository, issue)
           for reference in pull_request["closingIssuesReferences"]):
        sources.append("closing-issue")

    title_pattern = re.compile(
        rf"(?<![A-Za-z0-9._/-]){re.escape(repository)}\#{number}(?![A-Za-z0-9_])"
        rf"|(?<![A-Za-z0-9._/-])(?:#|issue[-_\s]+){number}(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )
    if title_pattern.search(pull_request["title"]):
        sources.append("title")

    branch_pattern = re.compile(
        rf"(?<![A-Za-z0-9._])(?:issue[-_]?{number}|{number})(?![A-Za-z0-9._])",
        re.IGNORECASE,
    )
    if branch_pattern.search(pull_request["headRefName"]):
        sources.append("branch")

    body = pull_request["body"] or ""
    repository_reference = re.escape(repository)
    reference_boundary = r"(?<![A-Za-z0-9._/-])"
    body_reference = (
        rf"{reference_boundary}(?:\#{number}|{repository_reference}\#{number}|"
        rf"https://github\.com/{repository_reference}/issues/{number})"
    )
    markdown_body_reference = (
        rf"\[(?:issue\s+)?(?:\#{number}|{repository_reference}\#{number})\]"
        rf"\(https://github\.com/{repository_reference}/issues/{number}\)"
    )
    body_directive = (
        rf"\b(?:closes?|fix(?:es)?|resolves?|implements?|address(?:es)?|refs?)\b"
        rf"\s*:?\s+(?:the\s+)?"
    )
    markdown_body_pattern = re.compile(
        body_directive + markdown_body_reference + r"(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )
    any_body_reference = (
        rf"{reference_boundary}(?:\#[0-9]+|{repository_reference}\#[0-9]+|"
        rf"https://github\.com/{repository_reference}/issues/[0-9]+)"
        r"(?![A-Za-z0-9_])"
    )
    body_pattern = re.compile(
        body_directive
        + rf"(?:issue\s+)?(?P<references>{any_body_reference}"
        + rf"(?:\s*(?:,\s*|\band\b\s*|&\s*){any_body_reference})*)",
        re.IGNORECASE,
    )
    target_body_reference_pattern = re.compile(
        body_reference + r"(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )
    plain_body = re.sub(r"\[[^\]\n]*\]\([^)\n]*\)", "", body)
    if markdown_body_pattern.search(body) or any(
        target_body_reference_pattern.search(match.group("references"))
        for match in body_pattern.finditer(plain_body)
    ):
        sources.append("body")

    if not sources:
        return None
    return {
        "number": pull_request["number"],
        "title": pull_request["title"],
        "headRefName": pull_request["headRefName"],
        "sources": sources,
    }


def preflight_issue_pr(repository, issue, selected_pr=None, expected_branch=None,
                       *, pull_requests=None):
    text(repository, maximum=256, pattern=observations.REPOSITORY_RE)
    integer(issue, minimum=1)
    integer(selected_pr, minimum=1, nullable=True)
    text(expected_branch, nullable=True, maximum=256)
    if selected_pr is not None:
        require(expected_branch is not None, "continuation requires its selected branch")

    if pull_requests is None:
        pull_requests = list_open_pull_requests(repository)
    matches = [
        match for pull_request in pull_requests
        if (match := implementation_pull_request_matches(repository, issue, pull_request)) is not None
    ]
    if len(matches) > 1:
        identities = ", ".join(
            f"#{item['number']} ({item['headRefName']})" for item in matches
        )
        raise HandoffDataError(
            f"multiple open implementation PRs for issue #{issue}: {identities}; "
            "record a disposition before continuing"
        )
    if not matches:
        require(selected_pr is None,
                f"selected PR #{selected_pr} is not an open implementation PR for issue #{issue}")
        return {
            "repository": repository,
            "issue": issue,
            "decision": "new-root",
            "pull_request": None,
            "branch": expected_branch,
        }

    match = matches[0]
    if selected_pr is None:
        raise HandoffDataError(
            f"open implementation PR #{match['number']} already exists for issue #{issue} "
            f"on branch {match['headRefName']}; continue it or record and close it before replacement"
        )
    require(selected_pr == match["number"],
            f"select existing implementation PR #{match['number']} for issue #{issue}")
    require(expected_branch == match["headRefName"],
            f"selected PR #{selected_pr} uses branch {match['headRefName']}, "
            f"not {expected_branch}")
    return {
        "repository": repository,
        "issue": issue,
        "decision": "continue-existing",
        "pull_request": match["number"],
        "branch": match["headRefName"],
    }


def observe_git(assignment):
    root = Path(assignment["allowed_worktree"])
    require(str(root.resolve(strict=True)) == str(root), "worktree path is symlinked")
    git.exact_repository_root(str(root))
    common = Path(os.fsdecode(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).strip())
    private = Path(os.fsdecode(_git(root, "rev-parse", "--absolute-git-dir")).strip())
    info = common.stat()
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").decode().strip()
    branch = None if branch == "HEAD" else branch
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    with git._directory_fd(private) as descriptor:
        try:
            index_mode = os.stat("index", dir_fd=descriptor, follow_symlinks=False).st_mode
        except FileNotFoundError:
            pass
        else:
            require(stat.S_ISREG(index_mode), "worktree index must be a nofollow regular file")
    flags = _git(root, "ls-files", "-v", "-z").split(b"\0")
    require(not any(record and (record[:1] == b"S" or record[:1].islower()) for record in flags),
            "worktree index hides tracked changes")
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                  "--ignore-submodules=none")
    dirty, conflicts = [], False
    records = status.split(b"\0")
    index = 0
    while index < len(records) and records[index]:
        record = records[index]
        dirty.append(os.fsdecode(record[3:]))
        conflicts |= record[:2] in {b"DD", b"AU", b"UD", b"UA", b"DU", b"AA", b"UU"}
        if record[:1] in {b"R", b"C"} or record[1:2] in {b"R", b"C"}:
            index += 1
            dirty.append(os.fsdecode(records[index]))
        index += 1
    unfinished = any((private / name).exists() for name in (
        "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-apply", "rebase-merge", "sequencer",
    ))
    return {
        "identity": {"worktree": str(root), "git_dir": str(private), "common_dir": str(common),
                     "device": info.st_dev, "inode": info.st_ino},
        "head": head, "branch": branch, "dirty_paths": sorted(set(dirty)),
        "conflicting": conflicts or unfinished,
    }


def availability_errors(state, at):
    current = observations.clock_observation()
    previous = state["clock"]
    a = state["availability"]
    reasons = []
    if not timestamp(a["observed_at"]) <= timestamp(at) <= timestamp(a["valid_until"]):
        reasons.append("coordinator-unavailable")
    if a["mode"] == "always-on" and (a["autostop_enabled"] or a["stop_on_disconnect"]):
        reasons.append("coordinator-unavailable")
    if (current["boot_id"] != previous["boot_id"]
            or current["monotonic_ns"] < previous["monotonic_ns"]
            or current["boottime_ns"] - current["monotonic_ns"]
            > previous["boottime_ns"] - previous["monotonic_ns"] + 5_000_000_000):
        reasons.append("coordinator-unavailable")
    return sorted(set(reasons))


def assign(state, assignment):
    validate_state(state)
    validate_assignment(assignment)
    require(not availability_errors(state, now()), "coordinator unavailable before assignment")
    native = assignment.get("native")
    if native is not None:
        require(all(assignment[name] is None for name in ("owner_id", "session_id", "dispatch_id")),
                "native assignment must be reserved before dispatch")
    current = observe_git(assignment)
    require(current["head"] == assignment["assigned_parent_sha"], "assigned parent is not current HEAD")
    require(current["branch"] == assignment["expected_branch"], "wrong assignment branch")
    require(not current["conflicting"], "conflicting worktree")
    require(not current["dirty_paths"] or assignment["kind"] == "replacement", "dirty worktree")
    if assignment["kind"] == "replacement":
        previous = find_entry(state, assignment["predecessor_id"])
        recovery = previous["interruption"]
        require(recovery is not None, "replacement lacks preserved worktree")
        require(current["identity"] == previous["git_identity"]
                and current["dirty_paths"] == recovery["dirty_paths"], "preserved worktree changed")
        lock = Path(current["identity"]["git_dir"]) / "locked"
        require(observations.read_bytes(lock, limit=4096).decode().strip() == recovery["lock_reason"],
                "recovery retention lock missing")
        require(observations.retained_data_sha256(current["identity"], current["dirty_paths"])
                == recovery["retained_data_sha256"], "retained recovery data changed")
        require(observe_git(assignment) == current, "preserved worktree changed during reassignment")
    for upstream in assignment["upstream_inputs"]:
        require(_git(Path(assignment["allowed_worktree"]), "cat-file", "-t", upstream).strip() == b"commit",
                "upstream input must name an existing exact commit")
    entry = {"assignment": assignment, "assigned_at": now(), "git_identity": current["identity"],
             "events": [], "process": None, "checks": [], "result": None, "validation": None,
             "interruption": None, "terminal_task": None, "worktree_release": None,
             "cursors": [], "remote_actions": [], "closed_at": None,
             "coordination_turns": 0}
    if native is not None:
        entry["cursors"] = [observations.cli_reservation_cursor(native["events_path"],
                                                               native["root_session_id"])]
        entry["native_binding"] = {
            "started_id": None, "interaction_id": None, "parent_task_id": None,
            "owner_id": None, "session_id": None, "last_event_at": None,
        }
    candidate = {**state, "assignments": [*state["assignments"], entry]}
    validate_state(candidate)
    preflight_issue_pr(
        assignment["repository"], assignment["issue"], assignment["pull_request"],
        assignment["expected_branch"],
    )
    state["assignments"].append(entry)
    state["clock"]["at"] = now()
    return entry


def find_entry(state, assignment_id):
    for entry in state["assignments"]:
        if entry["assignment"]["id"] == assignment_id:
            return entry
    raise HandoffDataError("unknown assignment")


def observe_remote_branch(assignment, cwd):
    endpoint = (f"repos/{assignment['repository']}/branches/"
                f"{quote(assignment['expected_branch'], safe='')}")
    result = git.run_process(
        ["gh", "api", endpoint], cwd=cwd, env=os.environ.copy(), timeout=30,
        max_bytes=observations.MAX_JSON_BYTES,
    )
    require(result.returncode == 0,
            "remote branch observation failed: " +
            os.fsdecode(result.stderr[:1024]).strip())
    branch = observations.parse_bytes(result.stdout)
    require(type(branch) is dict and branch.get("name") == assignment["expected_branch"]
            and type(branch.get("commit")) is dict
            and type(branch["commit"].get("sha")) is str,
            "remote branch response identity mismatch")
    sha(branch["commit"]["sha"])
    return branch["commit"]["sha"]


def verify_validation_worktree(assignment, expected_sha, identity):
    root = Path(assignment["allowed_worktree"])
    current = observe_git(assignment)
    require(current["head"] == expected_sha
            and current["branch"] is None
            and not current["dirty_paths"] and not current["conflicting"]
            and current["identity"]["common_dir"] == identity["common_dir"]
            and current["identity"]["git_dir"] != identity["git_dir"],
            "replacement validation worktree is not isolated at the candidate")
    ignored = _git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    require(not [path for path in ignored.split(b"\0") if path],
            "replacement validation worktree contains ignored data")
    submodules = _git(root, "submodule", "status", "--recursive").decode().splitlines()
    require(all(line.startswith("-") for line in submodules),
            "replacement validation worktree contains an initialized or conflicted submodule")
    return current


def remove_validation_worktree(coordinator_root, assignment, expected_sha, identity):
    root = Path(assignment["allowed_worktree"])
    worktrees = _git(coordinator_root, "worktree", "list", "--porcelain").decode().splitlines()
    require(os.path.lexists(root) and any(line == f"worktree {root}" for line in worktrees),
            "validation worktree is not registered at its assigned path")
    verify_validation_worktree(assignment, expected_sha, identity)
    _git(coordinator_root, "worktree", "remove", str(root))
    worktrees = _git(coordinator_root, "worktree", "list", "--porcelain").decode().splitlines()
    require(not os.path.lexists(root)
            and not any(line == f"worktree {root}" for line in worktrees),
            "validation worktree removal was not complete")


def validate_proposed_worktree_release(state, entry, release_record, clock):
    proposed_entry = {**entry, "worktree_release": release_record}
    proposed_state = {
        **state,
        "clock": clock,
        "assignments": [
            proposed_entry if candidate is entry else candidate
            for candidate in state["assignments"]
        ],
    }
    validate_state(proposed_state)


def release_logical_worktree(state, assignment_id, coordinator_worktree, validation_worktree):
    require(not availability_errors(state, now()), "coordinator unavailable before worktree release")
    entry = find_entry(state, assignment_id)
    assignment = entry["assignment"]
    terminal = entry.get("terminal_task")
    result = entry.get("result")
    require(terminal is not None and terminal["success"] and result is not None
            and entry["process"] is None and entry["interruption"] is None
            and entry["closed_at"] is None and entry.get("worktree_release") is None,
            "logical task is not eligible for worktree release")
    require(entry["events"] and entry["events"][-1]["state"] == "handed_off"
            and result["assignment_id"] == assignment_id
            and result["assigned_parent_sha"] == assignment["assigned_parent_sha"]
            and result["result_sha"] == terminal["head_sha"]
            and not entry["remote_actions"],
            "logical task delivery does not match its terminal candidate")
    require(observations.cli_cursor_caught_up(entry["cursors"][0]),
            "native log is not fully observed")

    owner_root = Path(assignment["allowed_worktree"])
    coordinator_root = Path(coordinator_worktree)
    validation_root = Path(validation_worktree)
    absolute_path(str(coordinator_root))
    absolute_path(str(validation_root))
    require(str(coordinator_root.resolve(strict=True)) == str(coordinator_root),
            "coordinator worktree path is symlinked")
    require(str(owner_root.resolve(strict=False)) == str(owner_root),
            "assigned worktree path is symlinked")
    require(str(validation_root.resolve(strict=False)) == str(validation_root),
            "validation worktree path is symlinked")
    require(not paths_overlap(validation_root, owner_root)
            and not paths_overlap(validation_root, coordinator_root)
            and not paths_overlap(coordinator_root, owner_root),
            "worktree release paths must be distinct")
    require(entry["git_identity"]["git_dir"] != entry["git_identity"]["common_dir"],
            "logical release requires a removable linked worktree")
    for other in state["assignments"]:
        if other["assignment"]["id"] != assignment_id:
            reserved_paths = [Path(other["assignment"]["allowed_worktree"])]
            other_release = other.get("worktree_release")
            if other_release is not None and other["closed_at"] is None:
                reserved_paths.append(Path(other_release["validation_worktree"]))
            require(not any(paths_overlap(validation_root, reserved)
                            for reserved in reserved_paths),
                    "validation worktree overlaps an assigned worktree")
    coordinator = {**assignment, "allowed_worktree": str(coordinator_root)}
    coordinator_git = observe_git(coordinator)
    require(coordinator_git["identity"]["common_dir"] == entry["git_identity"]["common_dir"],
            "coordinator worktree is not in the assigned repository")
    require(observe_remote_branch(assignment, coordinator_root) == result["result_sha"],
            "published branch does not match the delivered candidate")

    owner_exists = os.path.lexists(owner_root)
    validation_exists = os.path.lexists(validation_root)
    worktrees = _git(coordinator_root, "worktree", "list", "--porcelain").decode().splitlines()
    owner_registered = any(line == f"worktree {owner_root}" for line in worktrees)
    validation_registered = any(line == f"worktree {validation_root}" for line in worktrees)
    require(owner_exists == owner_registered,
            "assigned worktree path and registration disagree")
    require(validation_exists == validation_registered,
            "validation worktree path and registration disagree")

    current = None
    if owner_exists:
        require(not validation_exists, "validation worktree path already exists")
        current = observe_git(assignment)
        require(current["identity"] == entry["git_identity"]
                and current["branch"] == assignment["expected_branch"]
                and current["head"] == result["result_sha"]
                and not current["dirty_paths"] and not current["conflicting"],
                "assigned worktree is not the clean delivered candidate")
        ignored = _git(owner_root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
        require(not [path for path in ignored.split(b"\0") if path],
                "assigned worktree contains ignored data")
        submodules = _git(owner_root, "submodule", "status", "--recursive").decode().splitlines()
        require(all(line.startswith("-") for line in submodules),
                "assigned worktree contains an initialized or conflicted submodule")
    else:
        require(validation_exists,
                "removed owner worktree has no validation worktree to recover")

    validation_assignment = {**assignment, "allowed_worktree": str(validation_root)}
    created_validation = not validation_exists
    try:
        if created_validation:
            _git(coordinator_root, "worktree", "add", "--detach",
                 str(validation_root), result["result_sha"])
        validation_git = verify_validation_worktree(
            validation_assignment, result["result_sha"], entry["git_identity"],
        )
        release_record = {
            "release_id": worktree_release_id(assignment_id),
            "observed_at": now(),
            "assignment_id": assignment_id,
            "task_id": terminal["task_id"],
            "owner_id": terminal["owner_id"],
            "session_id": terminal["session_id"],
            "head_sha": result["result_sha"],
            "worktree": str(owner_root),
            "git_identity": entry["git_identity"],
            "validation_worktree": str(validation_root),
            "validation_identity": validation_git["identity"],
            "source": "git-worktree-remove",
            "path_absent": True,
            "unregistered": True,
        }
        prepared_clock = observations.clock_observation()
        release_record["observed_at"] = prepared_clock["at"]
        validate_worktree_release(release_record, assignment, entry)
        validate_proposed_worktree_release(state, entry, release_record, prepared_clock)
    except (OSError, ValueError) as prepare_error:
        if created_validation and os.path.lexists(validation_root):
            try:
                remove_validation_worktree(
                    coordinator_root, validation_assignment, result["result_sha"],
                    entry["git_identity"],
                )
            except (OSError, ValueError) as cleanup_error:
                raise HandoffDataError(
                    f"validation preparation failed ({prepare_error}); "
                    f"rollback failed ({cleanup_error})"
                ) from cleanup_error
        raise
    if owner_exists:
        try:
            _git(coordinator_root, "worktree", "remove", str(owner_root))
        except (OSError, ValueError) as removal_error:
            worktrees = _git(coordinator_root, "worktree", "list", "--porcelain").decode().splitlines()
            owner_registered = any(line == f"worktree {owner_root}" for line in worktrees)
            validation_registered = any(
                line == f"worktree {validation_root}" for line in worktrees
            )
            if os.path.lexists(owner_root) and owner_registered:
                require(os.path.lexists(validation_root) and validation_registered,
                        "failed owner removal left an unexpected validation worktree state")
                try:
                    remove_validation_worktree(
                        coordinator_root, validation_assignment, result["result_sha"],
                        entry["git_identity"],
                    )
                except (OSError, ValueError) as cleanup_error:
                    raise HandoffDataError(
                        f"owner removal failed ({removal_error}); "
                        f"validation cleanup failed ({cleanup_error})"
                    ) from cleanup_error
                raise
            elif not os.path.lexists(owner_root) and owner_registered:
                raise HandoffDataError(
                    "owner path disappeared but its Git registration remains; "
                    "validation worktree retained for manual recovery"
                ) from removal_error
            elif os.path.lexists(owner_root) != owner_registered:
                raise HandoffDataError(
                    "owner removal failed with an inconsistent registration; "
                    "validation worktree retained"
                ) from removal_error

    worktrees = _git(coordinator_root, "worktree", "list", "--porcelain").decode().splitlines()
    registered = any(line == f"worktree {owner_root}" for line in worktrees)
    validation_registered = any(line == f"worktree {validation_root}" for line in worktrees)
    require(not os.path.lexists(owner_root) and not registered and validation_registered,
            "assigned worktree removal was not complete")
    final_clock = observations.clock_observation()
    release_record["observed_at"] = final_clock["at"]
    validate_worktree_release(release_record, assignment, entry)
    validate_proposed_worktree_release(state, entry, release_record, final_clock)
    entry["worktree_release"] = release_record
    state["clock"] = final_clock
    return release_record


def _event(entry, state, source_id, observed_at=None):
    existing = [event["state"] for event in entry["events"]]
    if state in existing:
        return
    require(entry["closed_at"] is None and len(existing) < len(STATES)
            and STATES[len(existing)] == state, "lifecycle event out of order")
    entry["events"].append({
        "state": state,
        "at": observed_at if observed_at is not None else now(),
        "source_id": source_id,
    })


def observe_native_task(entry, log_path):
    a = entry["assignment"]
    task, binding = a["native"], entry["native_binding"]
    require(str(Path(log_path).absolute()) == task["events_path"], "wrong native root log")
    cursor = entry["cursors"][0]
    events, updated = observations.cli_event_batch(log_path, cursor)

    def advance_time(event):
        previous = binding["last_event_at"]
        require(previous is None or timestamp(previous) <= timestamp(event["timestamp"]),
                "native event chronology reversed")
        binding["last_event_at"] = event["timestamp"]

    for session_id, event in events:
        require(timestamp(event["timestamp"]) <= timestamp(now()), "future CLI event")
        data, kind = event["data"], event.get("type")
        is_owner_event = (
            a["owner_id"] is not None and event.get("agentId") == a["owner_id"]
            or a["session_id"] is not None and data.get("ownerNativeSessionId") == a["session_id"]
        )
        if is_owner_event:
            require(event.get("agentId") == a["owner_id"],
                    "native owner/session identity mismatch")
        source_ids = {item["source_id"] for item in entry["events"]}
        if binding["started_id"] is not None:
            source_ids.add(binding["started_id"])
        if entry.get("terminal_task") is not None:
            source_ids.add(entry["terminal_task"]["event_id"])
        if is_owner_event:
            require(event["id"] not in source_ids, "native owner event source reused")
        require(entry["closed_at"] is None or not is_owner_event,
                "closed native owner emitted an event")
        require(entry.get("terminal_task") is None or not is_owner_event,
                "terminal native task emitted another owner event")
        require(not is_owner_event or session_id == task["root_session_id"],
                "native owner event attributed to another root session")
        if (timestamp(event["timestamp"]) < timestamp(entry["assigned_at"])
                or session_id != task["root_session_id"]):
            continue
        if kind == "tool.execution_start" and data.get("toolName") == "task" and event.get("agentId") is None:
            arguments = data.get("arguments")
            prompt = arguments.get("prompt") if isinstance(arguments, dict) else None
            if not isinstance(prompt, str) or f"[handoff:{a['id']}]" not in prompt:
                continue
            require(prompt == task["prompt"], "native dispatch prompt mismatch")
            text(data.get("toolCallId"), maximum=128, pattern=ID_RE)
            require(a["dispatch_id"] is None, "duplicate native dispatch")
            advance_time(event)
            _event(entry, "assignment_sent", event["id"], event["timestamp"])
            a["dispatch_id"] = data["toolCallId"]
            continue
        if (kind == "subagent.started" and a["dispatch_id"] is not None
                and data.get("toolCallId") == a["dispatch_id"]):
            require(a["owner_id"] is None, "duplicate native owner start")
            text(event.get("agentId"), maximum=128, pattern=ID_RE)
            text(data.get("interactionId"), maximum=128, pattern=ID_RE)
            text(event["id"], maximum=128, pattern=ID_RE)
            advance_time(event)
            a["owner_id"] = event["agentId"]
            binding["started_id"] = event["id"]
            binding["interaction_id"] = data["interactionId"]
            binding["owner_id"] = event["agentId"]
            continue
        if (kind == "tool.execution_complete" and a["dispatch_id"] is not None
                and data.get("toolCallId") == a["dispatch_id"]):
            require(entry["terminal_task"] is None, "duplicate native task completion")
            require(a["owner_id"] is not None and binding["started_id"] is not None,
                    "native task completion lacks its owner start")
            require(session_id == task["root_session_id"] and event.get("agentId") is None,
                    "native task completion must be coordinator-owned")
            boolean(data.get("success"))
            current = observe_git(a)
            require(current["identity"] == entry["git_identity"],
                    "native task completed from a changed worktree")
            terminal = {
                "event_id": event["id"], "observed_at": event["timestamp"],
                "root_session_id": task["root_session_id"], "task_id": a["dispatch_id"],
                "owner_id": a["owner_id"], "session_id": a["session_id"],
                "head_sha": current["head"], "worktree": a["allowed_worktree"],
                "git_identity": current["identity"], "allowed_scope": list(a["allowed_scope"]),
                "success": data["success"],
                "rss_peak_bytes": None, "rss_unavailable_reason": LOGICAL_RSS_UNAVAILABLE,
            }
            validate_terminal_task(terminal, a, entry)
            advance_time(event)
            entry["terminal_task"] = terminal
            continue
        if a["owner_id"] is None or event.get("agentId") != a["owner_id"]:
            continue
        if kind == "user.message":
            if binding["parent_task_id"] is not None:
                if data.get("source") == f"agent-{task['root_session_id']}":
                    require(data.get("content") != task["prompt"], "duplicate native receipt")
                    require(data.get("parentAgentTaskId") == binding["parent_task_id"],
                            "native receipt task changed")
                    require(data.get("interactionId") == binding["interaction_id"],
                            "native receipt interaction changed")
                    advance_time(event)
                continue
            parent = data.get("parentAgentTaskId")
            if (data.get("source") != f"agent-{task['root_session_id']}"
                    or data.get("interactionId") != binding["interaction_id"]
                    or data.get("content") != task["prompt"]
                    or not isinstance(parent, str) or not ID_RE.fullmatch(parent)):
                continue
            advance_time(event)
            _event(entry, "assignment_received", event["id"], event["timestamp"])
            binding["parent_task_id"] = parent
        elif kind == "assistant.turn_start" and binding["parent_task_id"] is not None:
            if (data.get("parentToolCallId") == a["dispatch_id"]
                    and data.get("interactionId") == binding["interaction_id"]):
                advance_time(event)
                entry["coordination_turns"] += 1
        elif kind == "tool.execution_start" and binding["parent_task_id"] is not None:
            require(len(entry["events"]) < 4, "retired owner started more work")
            owner_session = data.get("ownerNativeSessionId")
            if not isinstance(owner_session, str) or not ID_RE.fullmatch(owner_session):
                continue
            text(data.get("toolCallId"), maximum=128, pattern=ID_RE)
            text(data.get("toolName"), maximum=128, pattern=ID_RE)
            require(data["toolCallId"] != a["dispatch_id"], "native progress reused dispatch")
            advance_time(event)
            if a["session_id"] is None:
                _event(entry, "progressing", event["id"], event["timestamp"])
                a["session_id"] = owner_session
                binding["session_id"] = owner_session
            else:
                require(a["session_id"] == owner_session, "native owner session changed")
        elif kind == "assistant.message" and a["session_id"] is not None:
            content = data.get("content")
            if (data.get("interactionId") != binding["interaction_id"] or not isinstance(content, str)
                    or not content.startswith('{"handoff_result":')):
                continue
            payload = observations.parse_bytes(content.encode())
            fields(payload, "handoff_result")
            result = validate_result(payload["handoff_result"])
            require(entry["result"] is None, "committed owner cannot deliver a second handoff")
            require(result["assignment_id"] == a["id"], "handoff message names wrong assignment")
            current = observe_git(a)
            require(current["head"] == result["result_sha"], "delivered SHA is not Git HEAD")
            advance_time(event)
            _event(entry, "committed", "git:" + current["head"], event["timestamp"])
            _event(entry, "handed_off", event["id"], event["timestamp"])
            entry["result"] = result
    entry["cursors"][0] = updated


def observe_cli(entry, log_path):
    if "native_binding" in entry:
        return observe_native_task(entry, log_path)
    a = entry["assignment"]
    cursor = next((item for item in entry["cursors"] if item["path"] == str(Path(log_path).absolute())), None)
    events, updated = observations.cli_event_batch(log_path, cursor)
    for session_id, event in events:
        require(timestamp(event["timestamp"]) <= timestamp(now()), "future CLI event")
        if timestamp(event["timestamp"]) < timestamp(entry["assigned_at"]):
            continue
        data = event["data"]
        kind = event.get("type")
        if (kind == "tool.execution_start" and data.get("toolCallId") == a["dispatch_id"]
                and data.get("toolName") in {"task", "write_agent", "bash"}):
            _event(entry, "assignment_sent", event["id"], event["timestamp"])
        if session_id != a["session_id"] or event.get("agentId", a["owner_id"]) != a["owner_id"]:
            continue
        # Receipt binds the actual dispatched task as well as its prompt marker.
        # Subagent-start/transport success or opaque event data cannot substitute.
        if (kind == "user.message" and data.get("parentAgentTaskId") == a["dispatch_id"]
                and isinstance(data.get("content"), str) and f"[handoff:{a['id']}]" in data["content"]):
            _event(entry, "assignment_received", event["id"], event["timestamp"])
        elif kind == "assistant.turn_start":
            entry["coordination_turns"] += 1
        elif kind == "tool.execution_start" and data.get("toolCallId") != a["dispatch_id"]:
            require(len(entry["events"]) < 4, "retired owner started more work")
            _event(entry, "progressing", event["id"], event["timestamp"])
        elif kind == "assistant.message":
            content = data.get("content")
            if not isinstance(content, str) or not content.startswith('{"handoff_result":'):
                continue
            payload = observations.parse_bytes(content.encode())
            fields(payload, "handoff_result")
            result = validate_result(payload["handoff_result"])
            require(entry["result"] is None, "committed owner cannot deliver a second handoff")
            require(result["assignment_id"] == a["id"], "handoff message names wrong assignment")
            current = observe_git(a)
            require(current["head"] == result["result_sha"], "delivered SHA is not Git HEAD")
            _event(entry, "committed", "git:" + current["head"], event["timestamp"])
            _event(entry, "handed_off", event["id"], event["timestamp"])
            entry["result"] = result
    if cursor is None:
        entry["cursors"].append(updated)
    else:
        entry["cursors"][entry["cursors"].index(cursor)] = updated


def bind_process(entry, pid):
    integer(pid, minimum=1)
    require(entry["process"] is None, "owner process already bound")
    root = Path(entry["assignment"]["allowed_worktree"])
    require(Path(os.readlink(f"/proc/{pid}/cwd")) == root, "process cwd does not match assigned worktree")
    entry["process"] = observations.process_identity(pid, entry["assignment"]["owner_id"])
    return entry["process"]


def record_process(entry, observed):
    validate_process(observed)
    require(entry["process"] is not None and observations.same_process(entry["process"], observed),
            "process observation identity mismatch")
    require(timestamp(observed["observed_at"]) >= timestamp(entry["process"]["observed_at"]),
            "stale process observation")
    if entry["process"]["rss_complete"]:
        require(observed == entry["process"], "terminal OS observation cannot be replaced")
    entry["process"] = observed


def _changes(root, parent, result):
    records = _git(root, "diff", "--raw", "--no-ext-diff", "--no-textconv", "--no-renames",
                   "--abbrev=40", "-z", parent, result, "--").split(b"\0")
    changes = {}
    for index in range(0, len(records) - 1, 2):
        old_mode, new_mode, old_oid, new_oid, _status = records[index].split()
        name = os.fsdecode(records[index + 1])
        path(name)
        changes[name] = (old_mode[1:], new_mode, old_oid, new_oid)
    require(len(changes) <= 256, "changed-path limit exceeded")
    return changes


def _allowed(name, scope):
    return any(name.startswith(item) if item.endswith("/") else name == item for item in scope)


def task_changes(assignment, result_sha):
    root = Path(assignment["allowed_worktree"])
    parent = assignment["assigned_parent_sha"]
    require(result_sha != parent, "stale-result")
    require(_ancestor(root, parent, result_sha), "unrelated-branch")
    commits = _git(root, "rev-list", "--first-parent", "--reverse", "--parents",
                   "--max-count=129", f"{parent}..{result_sha}").decode().splitlines()
    require(0 < len(commits) <= 128, "task-commit-limit")
    previous = parent
    used_upstream = set()
    task_commits = []
    for row in commits:
        revision, *parents = row.split()
        require(parents and parents[0] == previous, "wrong-parent")
        require(set(parents[1:]) <= set(assignment["upstream_inputs"]), "unauthorized-upstream")
        used_upstream.update(parents[1:])
        size = int(_git(root, "cat-file", "-s", revision))
        require(size <= 65536, "commit metadata too large")
        message = _git(root, "cat-file", "commit", revision).split(b"\n\n", 1)[1].decode("utf-8")
        trailers = message.strip().rsplit("\n\n", 1)[-1].splitlines()
        require(COPILOT_TRAILER in trailers, "missing-copilot-trailer")
        require(f"Copilot-Session: {assignment['session_id']}" in trailers, "missing-session-trailer")
        task_commits.append(revision)
        previous = revision
    changes = _changes(root, parent, result_sha)
    imported = set()
    for upstream in used_upstream:
        base = _git(root, "merge-base", parent, upstream).decode().strip()
        for name, change in _changes(root, base, upstream).items():
            if name in changes and changes[name][1::2] == change[1::2]:
                imported.add(name)
    owned = set(changes) - imported
    require(all(_allowed(name, assignment["allowed_scope"]) for name in owned), "scope-violation")
    blob_bytes = 0
    for name in owned:
        old_mode, new_mode, old_oid, new_oid = changes[name]
        for mode, oid in ((old_mode, old_oid), (new_mode, new_oid)):
            require(mode in {b"000000", b"100644", b"100755", b"120000"}, "unquantified-diff")
            if mode == b"000000":
                continue
            blob_bytes += int(_git(root, "cat-file", "-s", oid.decode()))
            require(blob_bytes <= git.MAX_BYTES, "changed blobs exceed 4 MiB")
            require(b"\0" not in _git(root, "cat-file", "blob", oid.decode()), "unquantified-diff")
    total = 0
    for raw in _git(root, "diff", "--numstat", "--no-ext-diff", "--no-textconv",
                    "--no-renames", "-z", parent, result_sha, "--").split(b"\0"):
        if not raw:
            continue
        added, deleted, name = raw.split(b"\t", 2)
        if os.fsdecode(name) not in owned:
            continue
        require(added != b"-" and deleted != b"-", "unquantified-diff")
        total += int(added) + int(deleted)
    return sorted(owned), total, task_commits, sorted(imported)


def _json_at(root, revision, path_name):
    entry = _git(root, "ls-tree", "-z", revision, "--", path_name).split(b"\0")[0]
    if not entry:
        return False, None
    header, actual_path = entry.split(b"\t", 1)
    mode, kind, oid = header.split()
    require(kind == b"blob" and mode in {b"100644", b"100755"}
            and os.fsdecode(actual_path) == path_name, "protocol input is not an exact regular blob")
    require(int(_git(root, "cat-file", "-s", oid.decode())) <= observations.MAX_JSON_BYTES,
            "protocol input exceeds 1 MiB")
    return True, observations.parse_bytes(_git(root, "cat-file", "blob", oid.decode()))


def _same_json_value(left, right):
    """Compare bounded parsed values/presence without Boolean-to-number coercion."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json_value(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_same_json_value(a, b) for a, b in zip(left, right))
    return left == right


def capture_check(entry, check_id, result_sha, trusted_executor=None, *, task_owned=True,
                  assignment=None):
    """Trusted executors only; direct checks do not claim task-owned resource deltas."""
    a = assignment or entry["assignment"]
    definition = a["required_checks"].get(check_id)
    require(definition is not None, "unknown required check")
    before = observe_git(a)
    require(before["head"] == result_sha and not before["dirty_paths"] and not before["conflicting"],
            "checks require exact clean Git HEAD")
    measurements = dict.fromkeys(METRICS)
    started = now()
    pid = rss = None
    detail = None
    if definition["contract"] == "git-diff-check":
        actual, started, completed = observations.capture_raw_check(a, result_sha)
        code, pid, rss = actual.returncode, actual.pid, actual.peak_rss_bytes
        detail = (actual.stdout + actual.stderr)[:2048].decode("utf-8", errors="replace") or None
        if code == 0 and task_owned:
            owned, _, _, _ = task_changes(a, result_sha)
            if not owned or all(name in HOST_FILES or name.startswith(HOST_ONLY) for name in owned):
                measurements.update(rom_bytes=0, ram_bytes=0)
            if (not any(check["contract"] == "protocol-json" for check in a["required_checks"].values())
                    and not any(name.endswith(".schema.json") for name in owned)):
                measurements["protocol_changes"] = 0
        completed = now()
    elif definition["contract"] == "protocol-json":
        root = Path(a["allowed_worktree"])
        measurements["protocol_changes"] = sum(
            not _same_json_value(_json_at(root, a["assigned_parent_sha"], name), _json_at(root, result_sha, name))
            for name in definition["inputs"]
        )
        code, completed = 0, now()
    else:
        require(callable(trusted_executor), "missing coordinator check executor")
        actual, measurements = trusted_executor(a, result_sha)
        require(isinstance(actual, git.ProcessResult), "executor must return an actual owned process capture")
        code, pid, rss, completed = actual.returncode, actual.pid, actual.peak_rss_bytes, now()
    after = observe_git(a)
    require(after == before, "Git/worktree changed during focused check")
    check = {"id": check_id, "evidence_id": definition["evidence_id"],
             "contract": definition["contract"], "parent_sha": a["assigned_parent_sha"],
             "result_sha": result_sha, "worktree": a["allowed_worktree"],
             "started_at": started, "completed_at": completed, "exit_code": code,
             "pid": pid, "peak_rss_bytes": rss, "measurements": measurements, "detail": detail}
    validate_check(check)
    entry["checks"] = [item for item in entry["checks"] if item["id"] != check_id] + [check]
    return check


def begin_check(entry, check_id, result_sha, process):
    """The existing executor registers its real child before asynchronous work."""
    require(isinstance(process, subprocess.Popen), "check requires an actual owned child handle")
    definition = entry["assignment"]["required_checks"].get(check_id)
    require(definition is not None and process.returncode is None, "unknown or completed check")
    require(observations.process_identity(process.pid, "check")["state"] == "running",
            "check child is not running")
    a = entry["assignment"]
    check = {"id": check_id, "evidence_id": definition["evidence_id"], "contract": definition["contract"],
             "parent_sha": a["assigned_parent_sha"], "result_sha": result_sha,
             "worktree": a["allowed_worktree"], "started_at": now(), "completed_at": None,
             "exit_code": None, "pid": process.pid, "peak_rss_bytes": None,
             "measurements": dict.fromkeys(METRICS), "detail": None}
    validate_check(check)
    require(all(item["id"] != check_id for item in entry["checks"]), "check already registered")
    entry["checks"].append(check)
    return check


def preserve_interruption(entry, reason, *, observed_at=None):
    choice(reason, {"sigkill", "timeout", "rss", "process-exit"})
    require(entry["closed_at"] is None and entry["interruption"] is None, "owner already closed")
    identity, a = entry["process"], entry["assignment"]
    require(identity is not None, "opaque owner: process termination is unknown")
    current = observations.sample_process(identity)
    require(current["state"] == "exited", "owner process is still live")
    require(reason != "sigkill" or identity["exit_code"] == -9,
            "SIGKILL lacks owned OS exit observation")
    require(reason != "timeout" or identity["exit_code"] == 124
            or identity["age_ms"] >= a["max_lifetime_seconds"] * 1000, "timeout not observed")
    require(reason != "rss" or identity["peak_rss_bytes"] is not None
            and identity["peak_rss_bytes"] > a["max_peak_rss_bytes"], "RSS overage not observed")
    current_git = observe_git(a)
    root = Path(a["allowed_worktree"])
    require(current_git["identity"] == entry["git_identity"], "worktree identity changed")
    require(current_git["identity"]["git_dir"] != current_git["identity"]["common_dir"],
            "recovery needs a lockable linked worktree; retain original unchanged")
    lock_reason = "handoff-recovery:" + a["id"]
    # Git's existing lock is also an explicit retention reason to issue #208.
    try:
        existing_lock = observations.read_bytes(Path(current_git["identity"]["git_dir"]) / "locked", limit=4096)
    except FileNotFoundError:
        _git(root, "worktree", "lock", "--reason", lock_reason, str(root))
    else:
        require(existing_lock.decode().strip() == lock_reason, "worktree has a different retention lock")
    for name in items(current_git["dirty_paths"], maximum=256, unique=True):
        path(name)
    retained_data = observations.retained_data_sha256(current_git["identity"], current_git["dirty_paths"])
    require(observe_git(a) == current_git, "recovery worktree changed during observation")
    at = observed_at or now()
    require(timestamp(entry["assigned_at"]) <= timestamp(at) <= timestamp(now()),
            "interruption time is outside the owner lifetime")
    for check in entry["checks"]:
        if check["completed_at"] is None:
            check["detail"] = "interrupted before observed completion"
    entry["interruption"] = {
        "at": at, "reason": reason,
        "oom_evidence": (observations.kernel_oom_evidence(identity, entry["assigned_at"], at)
                         if identity is not None else None),
        "worktree": str(root), "head": current_git["head"],
        "dirty_paths": current_git["dirty_paths"], "lock_reason": lock_reason,
        "retained_data_sha256": retained_data,
    }
    entry["closed_at"] = at
    return entry["interruption"]


def watcher_ci_state(watcher):
    run = watcher["run"]
    if run is None or watcher["query_error"] is not None:
        return "unknown"
    if run["status"] != "completed":
        return "pending"
    return "success" if run["conclusion"] == "success" else "failure"


def ci_state(state, result_sha):
    matching = [watcher for watcher in state["watchers"] if watcher["head_sha"] == result_sha]
    if not matching:
        return "absent"
    latest = max(matching, key=lambda watcher: (watcher["run_id"], watcher["attempt"],
                                               watcher["started_at"]))
    return watcher_ci_state(latest)


def owner_completion_errors(entry):
    codes = []
    process, terminal = entry["process"], entry.get("terminal_task")
    if process is not None:
        if process["state"] != "exited":
            codes.append("owner-not-retired")
        if not process["rss_complete"]:
            codes.append("owner-rss-unknown")
        if process["exit_code"] is None:
            codes.append("owner-exit-unknown")
        elif process["exit_code"] != 0:
            codes.append("owner-exit-failed")
        if terminal is not None and not terminal["success"]:
            codes.append("owner-task-failed")
    elif terminal is not None:
        if not terminal["success"]:
            codes.append("owner-task-failed")
        if entry.get("worktree_release") is None:
            codes.append("owner-worktree-release-unavailable")
    elif "native" not in entry["assignment"]:
        codes.extend(("owner-not-retired", "owner-rss-unknown", "owner-exit-unknown"))
    else:
        codes.extend(("owner-not-retired", "owner-rss-unknown", "owner-exit-unknown"))
    return codes


def owner_lifetime_ms(entry, at):
    start, cutoff = timestamp(entry["assigned_at"]), timestamp(at)
    terminal = entry.get("terminal_task")
    if entry["process"] is None and terminal is not None:
        end = timestamp(terminal["observed_at"])
    else:
        end = timestamp(entry["closed_at"]) if entry["closed_at"] is not None else cutoff
    require(start <= end <= cutoff, "invalid owner lifetime chronology")
    return math.ceil((end - start).total_seconds() * 1000)


def owner_acceptance_errors(entry, at):
    a, process = entry["assignment"], entry["process"]
    codes = owner_completion_errors(entry)
    if owner_lifetime_ms(entry, at) > a["max_lifetime_seconds"] * 1000:
        codes.append("owner-lifetime-exceeded")
    if process is not None:
        if process["age_ms"] > a["max_lifetime_seconds"] * 1000:
            codes.append("owner-lifetime-exceeded")
        if process["peak_rss_bytes"] is not None and process["peak_rss_bytes"] > a["max_peak_rss_bytes"]:
            codes.append("owner-rss-exceeded")
    return sorted(set(codes))


def resource_evidence_errors(entry, result, at):
    """Recheck captured evidence without requiring a historical worktree."""
    a = entry["assignment"]
    checks = {check["id"]: check for check in entry["checks"]}
    evidence = set(result["evidence_refs"])
    codes, protocol_counts = [], []
    usage = dict.fromkeys(METRICS)
    for check_id, definition in a["required_checks"].items():
        check = checks.get(check_id)
        if definition["evidence_id"] not in evidence:
            codes.append("missing-evidence")
        if check is None:
            codes.append("missing-check")
            continue
        errors = [code for failed, code in (
            (any(check[key] != expected for key, expected in (
                ("contract", definition["contract"]), ("evidence_id", definition["evidence_id"]),
                ("parent_sha", a["assigned_parent_sha"]), ("result_sha", result["result_sha"]),
                ("worktree", (entry.get("worktree_release") or {}).get(
                    "validation_worktree", a["allowed_worktree"])),
            )), "check-identity-mismatch"),
            (check["exit_code"] is None or check["completed_at"] is None, "incomplete-check"),
            (check["exit_code"] is not None and check["exit_code"] != 0, "required-check-failed"),
            (timestamp(check["started_at"]) < timestamp(entry["assigned_at"])
             or check["completed_at"] is not None and timestamp(check["completed_at"]) > timestamp(at),
             "check-time-mismatch"),
        ) if failed]
        codes.extend(errors)
        if errors:
            continue
        for metric, measured in check["measurements"].items():
            if metric == "protocol_changes" and definition["contract"] == "protocol-json":
                if measured is not None:
                    if measured > len(definition["inputs"]):
                        codes.append("invalid-protocol-measurement")
                    else:
                        protocol_counts.append(measured)
            elif measured is not None:
                usage[metric] = max(usage[metric] or 0, measured)
    protocol_checks = sum(check["contract"] == "protocol-json" for check in a["required_checks"].values())
    if len(protocol_counts) != protocol_checks:
        codes.append("missing-budget-measurement")
    elif protocol_checks:
        usage["protocol_changes"] = max(usage["protocol_changes"] or 0, sum(protocol_counts))
    for metric, measured in usage.items():
        if measured is None:
            codes.append("missing-budget-measurement")
        elif measured > a["budgets"][metric]:
            codes.append(metric.replace("_", "-") + "-budget-exceeded")
    return sorted(set(codes))


def validate_handoff(state, result, *, worktree, run_checks=True):
    validate_state(state)
    validate_result(result)
    entry = find_entry(state, result["assignment_id"])
    a = entry["assignment"]
    release = entry.get("worktree_release")
    validation_assignment = (
        {**a, "allowed_worktree": release["validation_worktree"]}
        if release is not None else a
    )
    expected_worktree = validation_assignment["allowed_worktree"]
    codes = availability_errors(state, now())
    total, task_commits, imported = None, [], []
    def reject(condition, code):
        if condition:
            codes.append(code)
    if "native_binding" in entry:
        observe_native_task(entry, a["native"]["events_path"])
        reject(not observations.cli_cursor_caught_up(entry["cursors"][0]), "native-log-unread")
    reject(str(Path(worktree).absolute()) != expected_worktree, "wrong-worktree")
    reject(result["assigned_parent_sha"] != a["assigned_parent_sha"], "wrong-parent")
    reject(entry["result"] != result, "missing-handoff-observation")
    reject(tuple(event["state"] for event in entry["events"]) != STATES, "incomplete-lifecycle")
    reject(bool(entry["remote_actions"]), "implementation-owner-remote-action")
    process = entry["process"]
    codes.extend(owner_acceptance_errors(entry, now()))
    try:
        current = observe_git(validation_assignment)
        expected_identity = (
            release["validation_identity"] if release is not None else entry["git_identity"]
        )
        reject(current["identity"] != expected_identity, "wrong-worktree")
        expected_branch = None if release is not None else a["expected_branch"]
        reject(current["branch"] != expected_branch, "wrong-branch")
        reject(current["head"] != result["result_sha"], "result-not-worktree-head")
        reject(bool(current["dirty_paths"]), "dirty-worktree")
        reject(current["conflicting"], "conflicting-worktree")
        if release is not None:
            ignored = _git(worktree, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
            reject(bool([path for path in ignored.split(b"\0") if path]), "ignored-worktree")
            reject(observe_remote_branch(a, worktree) != result["result_sha"],
                   "remote-head-mismatch")
        _, total, task_commits, imported = task_changes(validation_assignment, result["result_sha"])
        reject(total > a["budgets"]["changed_lines"], "changed-lines-budget-exceeded")
        if run_checks and not codes:
            for check_id, definition in a["required_checks"].items():
                if definition["contract"] != "coordinator-check":
                    capture_check(entry, check_id, result["result_sha"],
                                  assignment=validation_assignment)
    except (OSError, ValueError, UnicodeError) as error:
        known = {"stale-result", "unrelated-branch", "wrong-parent", "unauthorized-upstream",
                 "missing-copilot-trailer", "missing-session-trailer", "scope-violation",
                 "task-commit-limit", "unquantified-diff"}
        codes.append(str(error) if str(error) in known else "git-or-check-observation-failed")
    codes.extend(resource_evidence_errors(entry, result, now()))
    if (process is not None and process["state"] == "exited" and process["exit_code"] not in (None, 0)
            and entry["closed_at"] is None and entry["interruption"] is None):
        reason = {-9: "sigkill", 124: "timeout"}.get(process["exit_code"], "process-exit")
        preserve_interruption(entry, reason)
    at = now()
    codes.extend(owner_acceptance_errors(entry, at))
    if "native_binding" in entry:
        reject(not observations.cli_cursor_caught_up(entry["cursors"][0]), "native-log-unread")
    codes = sorted(set(codes))
    outcome = "rejected" if codes else "accepted"
    if entry["interruption"] is not None:
        outcome = "interrupted"
    elif len(entry["events"]) < 4:
        outcome = "in_progress"
    verdict = {"assignment_id": a["id"], "result_sha": result["result_sha"], "observed_at": at,
               "local_outcome": outcome, "handoff_ready": outcome == "accepted",
               "rejection_codes": codes, "changed_lines": total, "task_commits": task_commits,
               "imported_paths": imported, "ci_state": ci_state(state, result["result_sha"])}
    validate_verdict(verdict)
    entry["validation"] = verdict
    if (len(entry["events"]) >= 4 and not owner_completion_errors(entry)
            and "native-log-unread" not in codes):
        entry["closed_at"] = entry["closed_at"] or at
    return verdict


def reserve_watcher(state, watcher_id, run_id, attempt, head_sha, pid):
    validate_state(state)
    for previous in state["watchers"]:
        if (previous["run_id"], previous["attempt"]) == (run_id, attempt):
            require(previous["ended_at"] is not None
                    and observations.sample_process(previous["process"])["state"] == "exited",
                    "previous watcher has not terminated")
    process = observations.process_identity(pid, watcher_id)
    require(process["state"] == "running", "watcher process is not running")
    watcher = {"id": watcher_id, "coordinator_id": state["coordinator_id"], "run_id": run_id,
               "attempt": attempt, "head_sha": head_sha,
               "process": process, "started_at": now(),
               "ended_at": None, "exit_code": None, "run": None, "query_error": None}
    validate_state({**state, "watchers": [*state["watchers"], watcher]})
    state["watchers"].append(watcher)
    return watcher


def finish_watcher(state, watcher_id, process=None):
    watcher = next((item for item in state["watchers"] if item["id"] == watcher_id), None)
    require(watcher is not None and watcher["ended_at"] is None, "unknown or retired watcher")
    observed = (observations.observe_owned_exit(process, watcher["process"]) if process is not None
                else observations.sample_process(watcher["process"]))
    require(observed["state"] == "exited", "watcher is still running")
    watcher.update(process=observed, ended_at=now(), exit_code=observed["exit_code"])
    return watcher


def reconcile_run(state, run_id):
    validate_state(state)
    matching = [item for item in state["watchers"] if item["run_id"] == run_id]
    require(matching, "unknown recorded run")
    watcher = max(matching, key=lambda item: (item["attempt"], item["started_at"]))
    try:
        watcher["run"] = observations.github_run(state["repository"], run_id, watcher["attempt"],
                                                 watcher["head_sha"])
        watcher["query_error"] = None
    except (OSError, ValueError) as error:
        watcher["query_error"] = str(error)[:2048]
    return watcher


def summarize_handoffs(state):
    validate_state(state)
    counts = {key: 0 for key in ("accepted", "rejected", "interrupted", "in_progress")}
    rejections, lifetimes, rss = set(), [], []
    stale = turns = recovery_ms = unknown_rss = 0
    for entry in state["assignments"]:
        verdict = entry["validation"]
        if verdict is not None and verdict["local_outcome"] == "accepted":
            a, result, process = entry["assignment"], entry["result"], entry["process"]
            require(result is not None and result["result_sha"] == verdict["result_sha"]
                    and result["assigned_parent_sha"] == a["assigned_parent_sha"]
                    and entry["closed_at"] is not None and len(entry["events"]) == 5
                    and not entry["remote_actions"] and entry["interruption"] is None,
                    "accepted report lacks its complete handoff observations")
            require(not owner_acceptance_errors(entry, entry["closed_at"]),
                    "accepted report lacks budget-compliant zero owner completion")
            require(verdict["changed_lines"] is not None
                    and verdict["changed_lines"] <= a["budgets"]["changed_lines"],
                    "accepted report lacks measured budget compliance")
            require(not resource_evidence_errors(entry, result, verdict["observed_at"]),
                    "accepted report lacks complete, budget-compliant focused-check observations")
        outcome = ("interrupted" if entry["interruption"] else
                   verdict["local_outcome"] if verdict else "in_progress")
        counts[outcome] += 1
        if verdict:
            rejections.update(verdict["rejection_codes"])
            stale += "stale-result" in verdict["rejection_codes"]
        end = entry["closed_at"] or state["clock"]["at"]
        terminal = entry.get("terminal_task")
        if entry["process"] is None and terminal is not None:
            end = max(
                timestamp(end), timestamp(terminal["observed_at"])
            ).isoformat().replace("+00:00", "Z")
        lifetimes.append(owner_lifetime_ms(entry, end))
        process = entry["process"]
        if process is None or not process["rss_complete"]:
            unknown_rss += 1
        else:
            rss.append(process["peak_rss_bytes"])
        turns += entry["coordination_turns"]
        predecessor = entry["assignment"]["predecessor_id"]
        if entry["assignment"]["kind"] == "replacement":
            before = find_entry(state, predecessor)
            recovery_ms += math.ceil((timestamp(entry["assigned_at"])
                                      - timestamp(before["interruption"]["at"])).total_seconds() * 1000)
    return {"repository": state["repository"], "observed_at": state["clock"]["at"],
            "records": len(state["assignments"]), **counts, "stale_responses": stale,
            "max_lifetime_ms": max(lifetimes, default=None), "max_peak_rss_bytes": max(rss, default=None),
            "unknown_rss_records": unknown_rss, "coordination_turns": turns,
            "recovery_ms": recovery_ms, "rejection_codes": sorted(rejections),
            "runs": [{"run_id": item["run_id"], "attempt": item["attempt"],
                      "ci_state": watcher_ci_state(item)} for item in state["watchers"]]}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--repository", required=True)
    preflight.add_argument("--issue", type=int, required=True)
    preflight.add_argument("--selected-pr", type=int)
    preflight.add_argument("--expected-branch")
    assign_parser = commands.add_parser("assign")
    assign_parser.add_argument("--assignment", type=Path, required=True)
    observe = commands.add_parser("observe")
    observe.add_argument("--assignment-id", required=True)
    observe.add_argument("--runtime-events", type=Path)
    observe.add_argument("--pid", type=int)
    observe.add_argument("--interruption", choices=("sigkill", "timeout", "rss", "process-exit"))
    release = commands.add_parser("release-worktree")
    release.add_argument("--assignment-id", required=True)
    release.add_argument("--coordinator-worktree", type=Path, required=True)
    release.add_argument("--validation-worktree", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--result", type=Path, required=True)
    validate.add_argument("--worktree", type=Path, required=True)
    reconcile = commands.add_parser("reconcile-run")
    reconcile.add_argument("--run-id", type=int, required=True)
    for command in (assign_parser, observe, release, validate, reconcile):
        command.add_argument("--state", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.operation == "preflight":
            output = preflight_issue_pr(
                args.repository, args.issue, args.selected_pr, args.expected_branch,
            )
            sys.stdout.buffer.write(observations.json_bytes(output))
            return 0
        with observations.locked_state(args.state) as state:
            validate_state(state)
            if args.operation == "assign":
                output = assign(state, load_assignment(args.assignment))
            elif args.operation == "observe":
                entry = find_entry(state, args.assignment_id)
                require(args.runtime_events or args.pid or args.interruption, "observe needs an actual source")
                if args.runtime_events:
                    observe_cli(entry, args.runtime_events)
                if args.pid:
                    if entry["process"] is None:
                        bind_process(entry, args.pid)
                    else:
                        require(args.pid == entry["process"]["pid"], "PID mismatch")
                        record_process(entry, observations.sample_process(entry["process"]))
                if args.interruption:
                    preserve_interruption(entry, args.interruption)
                output = entry
            elif args.operation == "release-worktree":
                output = release_logical_worktree(
                    state, args.assignment_id, args.coordinator_worktree, args.validation_worktree,
                )
            elif args.operation == "validate":
                output = validate_handoff(state, load_result(args.result), worktree=args.worktree)
            else:
                output = reconcile_run(state, args.run_id)
            validate_state(state)
            # Keep the boot/suspend baseline until the coordinator makes a new
            # availability decision; an ordinary observation cannot clear an outage.
            state["clock"]["at"] = now()
        sys.stdout.buffer.write(normalized_json(output))
        return 2 if args.operation == "validate" and not output["handoff_ready"] else 0
    except (OSError, ValueError) as error:
        print(f"workflow-pilot handoff: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
