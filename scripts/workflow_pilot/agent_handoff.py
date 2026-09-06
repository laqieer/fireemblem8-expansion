#!/usr/bin/env python3
"""Exact-SHA local handoffs. Publication remains the delivery coordinator's job."""

from __future__ import annotations

import argparse
import math
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

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
HandoffDataError = observations.ObservationError
require = observations.require
load_json = observations.load_json
normalized_json = observations.json_bytes
now = observations.utc_now
timestamp = observations.timestamp


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()), f"expected fields: {names}")
    return value


def text(value, *, nullable=False, maximum=16384, pattern=None):
    if value is None and nullable:
        return
    require(type(value) is str and 0 < len(value) <= maximum, "invalid bounded string")
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


def sha(value):
    text(value, maximum=40, pattern=SHA_RE)


def validate_assignment(value):
    fields(value, "schema_version repository id issue pull_request owner_id session_id dispatch_id "
           "assigned_parent_sha expected_branch allowed_worktree allowed_scope upstream_inputs "
           "finding_ids acceptance_criteria required_checks budgets max_lifetime_seconds "
           "max_peak_rss_bytes prohibited_remote_actions predecessor_id kind")
    require(value["schema_version"] == SCHEMA_VERSION and type(value["schema_version"]) is int,
            "handoff schema_version must be 3")
    text(value["repository"], maximum=256, pattern=observations.REPOSITORY_RE)
    for name in ("id", "owner_id", "session_id", "dispatch_id"):
        text(value[name], maximum=128, pattern=ID_RE)
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
    for _check_id, check in named_records(value["required_checks"], minimum=1):
        fields(check, "contract evidence_id inputs")
        text(check["evidence_id"], maximum=128, pattern=ID_RE)
        choice(check["contract"], CHECK_CONTRACTS)
        for name in items(check["inputs"], maximum=16, unique=True):
            path(name)
        require(check["contract"] == "protocol-json" or not check["inputs"],
                "only a protocol-json check accepts repository input paths")
        require(check["contract"] != "protocol-json" or check["inputs"],
                "protocol-json needs explicit inputs")
        evidence.discard(check["evidence_id"])
    require(not evidence, "criterion has no required evidence producer")
    require(any(check["contract"] == "git-diff-check" for check in value["required_checks"].values()),
            "a real raw Git check is required")
    fields(value["budgets"], "changed_lines rom_bytes ram_bytes protocol_changes")
    for limit in value["budgets"].values():
        integer(limit)
    integer(value["max_lifetime_seconds"], minimum=1, maximum=86400)
    integer(value["max_peak_rss_bytes"], minimum=1)
    items(value["prohibited_remote_actions"], minimum=8, maximum=8, unique=True)
    require(set(value["prohibited_remote_actions"]) == set(PROHIBITED_REMOTE_ACTIONS),
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


def validate_availability(value):
    fields(value, "mode observed_at valid_until autostop_enabled stop_on_disconnect plan")
    choice(value["mode"], {"always-on", "plan"})
    timestamp(value["observed_at"])
    timestamp(value["valid_until"])
    for name in ("autostop_enabled", "stop_on_disconnect"):
        boolean(value[name])
    text(value["plan"], nullable=True)
    require(value["mode"] != "plan" or value["plan"] is not None, "availability plan is missing")


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
    fields(value, "schema_version repository coordinator_id availability clock assignments watchers")
    require(type(value["schema_version"]) is int and value["schema_version"] == SCHEMA_VERSION,
            "state schema_version must be 3")
    text(value["repository"], maximum=256, pattern=observations.REPOSITORY_RE)
    text(value["coordinator_id"], maximum=128, pattern=ID_RE)
    validate_availability(value["availability"])
    validate_clock(value["clock"])
    names, owners, sessions, owner_processes = set(), set(), set(), set()
    active = []
    for entry in items(value["assignments"]):
        fields(entry, "assignment assigned_at git_identity events process checks result validation "
               "interruption cursors remote_actions closed_at coordination_turns")
        a = validate_assignment(entry["assignment"])
        require(a["repository"] == value["repository"], "assignment repository mismatch")
        require(a["id"] not in names and a["owner_id"] not in owners and a["session_id"] not in sessions,
                "duplicate/reused owner, session or assignment")
        names.add(a["id"])
        owners.add(a["owner_id"])
        sessions.add(a["session_id"])
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
            require(last <= current, "lifecycle observation order reversed")
            event_names.append(event["state"])
            event_ids.append(event["source_id"])
            last = current
        require(tuple(event_names) == STATES[:len(event_names)], "incomplete lifecycle transition")
        require(len(set(event_ids)) == len(event_ids), "duplicate lifecycle source event")
        if entry["process"] is not None:
            validate_process(entry["process"])
            require(entry["process"]["runtime_handle"] == a["owner_id"], "owner process handle mismatch")
            identity = tuple(entry["process"][key] for key in ("boot_id", "pid", "start_ticks"))
            require(identity not in owner_processes, "implementation process reused")
            owner_processes.add(identity)
        checks = set()
        for check in items(entry["checks"], maximum=32):
            validate_check(check)
            require(check["id"] not in checks, "duplicate check")
            checks.add(check["id"])
        if entry["result"] is not None:
            validate_result(entry["result"])
            require(entry["result"]["assignment_id"] == a["id"], "delivered assignment mismatch")
        if entry["validation"] is not None:
            validate_verdict(entry["validation"])
            require(entry["validation"]["assignment_id"] == a["id"], "verdict assignment mismatch")
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
        cursor_paths = set()
        for cursor in items(entry["cursors"], maximum=4):
            fields(cursor, "path device inode offset session_id")
            absolute_path(cursor["path"])
            require(cursor["path"] not in cursor_paths, "duplicate log cursor")
            cursor_paths.add(cursor["path"])
            for name in ("device", "inode", "offset"):
                integer(cursor[name])
            text(cursor["session_id"], nullable=True, maximum=128)
        for action in items(entry["remote_actions"], unique=True):
            fields(action, "id action at")
            text(action["id"], maximum=128, pattern=ID_RE)
            choice(action["action"], set(PROHIBITED_REMOTE_ACTIONS))
            timestamp(action["at"])
        if entry["closed_at"] is not None:
            require(timestamp(entry["closed_at"]) >= last, "owner closed before lifecycle observation")
        else:
            active.append(a)
    for index, left in enumerate(active):
        for right in active[index + 1:]:
            require(left["issue"] != right["issue"] and left["allowed_worktree"] != right["allowed_worktree"]
                    and (left["pull_request"] is None or left["pull_request"] != right["pull_request"]),
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
        require(previous["assignment"]["issue"] == a["issue"]
                and previous["assignment"]["pull_request"] == a["pull_request"],
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
    for watcher in items(value["watchers"]):
        fields(watcher, "id coordinator_id run_id attempt head_sha process started_at ended_at "
               "exit_code run query_error")
        text(watcher["id"], maximum=128, pattern=ID_RE)
        require(watcher["id"] not in watcher_ids, "duplicate watcher ID")
        watcher_ids.add(watcher["id"])
        require(watcher["coordinator_id"] == value["coordinator_id"], "duplicate coordinator")
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
            require(watcher["process"]["state"] == "running" and watcher["exit_code"] is None,
                    "active watcher needs a running process and no exit code")
            identity = (watcher["run_id"], watcher["attempt"])
            require(identity not in active_runs, "duplicate active watcher")
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


def observe_git(assignment):
    root = Path(assignment["allowed_worktree"])
    require(str(root.resolve(strict=True)) == str(root), "worktree path is symlinked")
    git.exact_repository_root(str(root))
    common = Path(os.fsdecode(_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).strip())
    private = Path(os.fsdecode(_git(root, "rev-parse", "--absolute-git-dir")).strip())
    info = common.stat()
    branch = _git(root, "symbolic-ref", "--short", "HEAD").decode().strip()
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
             "interruption": None, "cursors": [], "remote_actions": [], "closed_at": None,
             "coordination_turns": 0}
    candidate = {**state, "assignments": [*state["assignments"], entry]}
    validate_state(candidate)
    state["assignments"].append(entry)
    state["clock"]["at"] = now()
    return entry


def find_entry(state, assignment_id):
    for entry in state["assignments"]:
        if entry["assignment"]["id"] == assignment_id:
            return entry
    raise HandoffDataError("unknown assignment")


def _event(entry, state, source_id):
    existing = [event["state"] for event in entry["events"]]
    if state in existing:
        return
    require(entry["closed_at"] is None and len(existing) < len(STATES)
            and STATES[len(existing)] == state, "lifecycle event out of order")
    entry["events"].append({"state": state, "at": now(), "source_id": source_id})


def observe_cli(entry, log_path):
    a = entry["assignment"]
    cursor = next((item for item in entry["cursors"] if item["path"] == str(Path(log_path).absolute())), None)
    events, updated = observations.cli_event_batch(log_path, cursor)
    for event in events:
        require(timestamp(event["timestamp"]) <= timestamp(now()), "future CLI event")
        data = event["data"]
        kind = event.get("type")
        if (kind == "tool.execution_start" and data.get("toolCallId") == a["dispatch_id"]
                and data.get("toolName") in {"task", "write_agent", "bash"}):
            _event(entry, "assignment_sent", event["id"])
        if updated["session_id"] != a["session_id"] or event.get("agentId", a["owner_id"]) != a["owner_id"]:
            continue
        # Receipt binds the actual dispatched task as well as its prompt marker.
        # Subagent-start/transport success or opaque event data cannot substitute.
        if (kind == "user.message" and data.get("parentAgentTaskId") == a["dispatch_id"]
                and isinstance(data.get("content"), str) and f"[handoff:{a['id']}]" in data["content"]):
            _event(entry, "assignment_received", event["id"])
        elif kind == "assistant.turn_start":
            entry["coordination_turns"] += 1
        elif kind == "tool.execution_start" and data.get("toolCallId") != a["dispatch_id"]:
            require(len(entry["events"]) < 4, "retired owner started more work")
            _event(entry, "progressing", event["id"])
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
            _event(entry, "committed", "git:" + current["head"])
            _event(entry, "handed_off", event["id"])
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
        return None
    header, actual_path = entry.split(b"\t", 1)
    mode, kind, oid = header.split()
    require(kind == b"blob" and mode in {b"100644", b"100755"}
            and os.fsdecode(actual_path) == path_name, "protocol input is not an exact regular blob")
    require(int(_git(root, "cat-file", "-s", oid.decode())) <= observations.MAX_JSON_BYTES,
            "protocol input exceeds 1 MiB")
    return observations.parse_bytes(_git(root, "cat-file", "blob", oid.decode()))


def capture_check(entry, check_id, result_sha, trusted_executor=None):
    """Only the trusted coordinator selects executors; result JSON never supplies one."""
    a = entry["assignment"]
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
    elif definition["contract"] == "protocol-json":
        root = Path(a["allowed_worktree"])
        measurements["protocol_changes"] = sum(
            _json_at(root, a["assigned_parent_sha"], name) != _json_at(root, result_sha, name)
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


def preserve_interruption(entry, reason):
    choice(reason, {"sigkill", "timeout", "rss", "process-exit"})
    require(entry["closed_at"] is None and entry["interruption"] is None, "owner already closed")
    identity = entry["process"]
    require(identity is not None, "opaque owner: process termination is unknown")
    current = observations.sample_process(identity)
    require(current["state"] == "exited", "owner process is still live")
    require(reason != "sigkill" or identity["exit_code"] == -9, "SIGKILL lacks owned OS exit observation")
    a = entry["assignment"]
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
    at = now()
    for check in entry["checks"]:
        if check["completed_at"] is None:
            check["detail"] = "interrupted before observed completion"
    entry["interruption"] = {
        "at": at, "reason": reason,
        "oom_evidence": observations.kernel_oom_evidence(identity, entry["assigned_at"], at),
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


def owner_completion_errors(process):
    codes = []
    if process is None or process["state"] != "exited":
        codes.append("owner-not-retired")
    if process is None or not process["rss_complete"]:
        codes.append("owner-rss-unknown")
    if process is None or process["exit_code"] is None:
        codes.append("owner-exit-unknown")
    elif process["exit_code"] != 0:
        codes.append("owner-exit-failed")
    return codes


def validate_handoff(state, result, *, worktree, run_checks=True):
    validate_state(state)
    validate_result(result)
    entry = find_entry(state, result["assignment_id"])
    a = entry["assignment"]
    codes = availability_errors(state, now())
    total, task_commits, imported = None, [], []
    def reject(condition, code):
        if condition:
            codes.append(code)
    reject(str(Path(worktree).absolute()) != a["allowed_worktree"], "wrong-worktree")
    reject(result["assigned_parent_sha"] != a["assigned_parent_sha"], "wrong-parent")
    reject(entry["result"] != result, "missing-handoff-observation")
    reject(tuple(event["state"] for event in entry["events"]) != STATES, "incomplete-lifecycle")
    reject(bool(entry["remote_actions"]), "implementation-owner-remote-action")
    at = now()
    lifetime = (timestamp(entry["closed_at"] or at) - timestamp(entry["assigned_at"])).total_seconds()
    reject(lifetime > a["max_lifetime_seconds"], "owner-lifetime-exceeded")
    process = entry["process"]
    codes.extend(owner_completion_errors(process))
    if process is not None:
        reject(process["age_ms"] > a["max_lifetime_seconds"] * 1000, "owner-lifetime-exceeded")
        reject(process["peak_rss_bytes"] is not None
               and process["peak_rss_bytes"] > a["max_peak_rss_bytes"], "owner-rss-exceeded")
    owned = []
    try:
        current = observe_git(a)
        reject(current["identity"] != entry["git_identity"], "wrong-worktree")
        reject(current["branch"] != a["expected_branch"], "wrong-branch")
        reject(current["head"] != result["result_sha"], "result-not-worktree-head")
        reject(bool(current["dirty_paths"]), "dirty-worktree")
        reject(current["conflicting"], "conflicting-worktree")
        owned, total, task_commits, imported = task_changes(a, result["result_sha"])
        reject(total > a["budgets"]["changed_lines"], "changed-lines-budget-exceeded")
        if run_checks and not codes:
            for check_id, definition in a["required_checks"].items():
                if definition["contract"] != "coordinator-check":
                    capture_check(entry, check_id, result["result_sha"])
    except (OSError, ValueError, UnicodeError) as error:
        known = {"stale-result", "unrelated-branch", "wrong-parent", "unauthorized-upstream",
                 "missing-copilot-trailer", "missing-session-trailer", "scope-violation",
                 "task-commit-limit", "unquantified-diff"}
        codes.append(str(error) if str(error) in known else "git-or-check-observation-failed")
    at = now()
    evidence = set(result["evidence_refs"])
    checks = {check["id"]: check for check in entry["checks"]}
    usage = dict.fromkeys(METRICS)
    if owned and all(name in HOST_FILES or name.startswith(HOST_ONLY) for name in owned):
        usage.update(rom_bytes=0, ram_bytes=0)
    elif total == 0 and not owned:
        usage.update(rom_bytes=0, ram_bytes=0)
    protocol_inputs = {name for definition in a["required_checks"].values() for name in definition["inputs"]}
    if not protocol_inputs and not any(name.endswith(".schema.json") for name in owned):
        usage["protocol_changes"] = 0
    for check_id, definition in a["required_checks"].items():
        check = checks.get(check_id)
        reject(definition["evidence_id"] not in evidence, "missing-evidence")
        if check is None:
            codes.append("missing-check")
            continue
        reject(any(check[key] != expected for key, expected in (
            ("contract", definition["contract"]), ("evidence_id", definition["evidence_id"]),
            ("parent_sha", a["assigned_parent_sha"]), ("result_sha", result["result_sha"]),
            ("worktree", a["allowed_worktree"]),
        )), "check-identity-mismatch")
        reject(check["exit_code"] is None, "incomplete-check")
        reject(check["exit_code"] is not None and check["exit_code"] != 0, "required-check-failed")
        reject(timestamp(check["started_at"]) < timestamp(entry["assigned_at"])
               or check["completed_at"] is not None
               and timestamp(check["completed_at"]) > timestamp(at), "check-time-mismatch")
        if check["exit_code"] == 0:
            for metric, measured in check["measurements"].items():
                if measured is not None:
                    usage[metric] = max(usage[metric] or 0, measured)
    for metric, measured in usage.items():
        reject(measured is None, "missing-budget-measurement")
        reject(measured is not None and measured > a["budgets"][metric],
               metric.replace("_", "-") + "-budget-exceeded")
    if (process is not None and process["state"] == "exited" and process["exit_code"] not in (None, 0)
            and entry["closed_at"] is None and entry["interruption"] is None):
        reason = {-9: "sigkill", 124: "timeout"}.get(process["exit_code"], "process-exit")
        preserve_interruption(entry, reason)
    at = now()
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
    if len(entry["events"]) >= 4 and not owner_completion_errors(process):
        entry["closed_at"] = entry["closed_at"] or now()
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
            require(not owner_completion_errors(process), "accepted report lacks observed zero owner completion")
            require(process["age_ms"] <= a["max_lifetime_seconds"] * 1000
                    and process["peak_rss_bytes"] <= a["max_peak_rss_bytes"]
                    and verdict["changed_lines"] is not None
                    and verdict["changed_lines"] <= a["budgets"]["changed_lines"],
                    "accepted report lacks measured budget compliance")
            checks = {check["id"]: check for check in entry["checks"]}
            for check_id, definition in a["required_checks"].items():
                check = checks.get(check_id)
                require(check is not None and check["exit_code"] == 0 and check["completed_at"] is not None
                        and check["result_sha"] == result["result_sha"]
                        and check["parent_sha"] == a["assigned_parent_sha"]
                        and check["worktree"] == a["allowed_worktree"]
                        and check["contract"] == definition["contract"]
                        and check["evidence_id"] == definition["evidence_id"]
                        and check["evidence_id"] in result["evidence_refs"],
                        "accepted report lacks actual focused-check observations")
        outcome = ("interrupted" if entry["interruption"] else
                   verdict["local_outcome"] if verdict else "in_progress")
        counts[outcome] += 1
        if verdict:
            rejections.update(verdict["rejection_codes"])
            stale += "stale-result" in verdict["rejection_codes"]
        end = entry["closed_at"] or state["clock"]["at"]
        lifetimes.append(max(0, math.ceil((timestamp(end) - timestamp(entry["assigned_at"])).total_seconds() * 1000)))
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
    assign_parser = commands.add_parser("assign")
    assign_parser.add_argument("--assignment", type=Path, required=True)
    observe = commands.add_parser("observe")
    observe.add_argument("--assignment-id", required=True)
    observe.add_argument("--runtime-events", type=Path)
    observe.add_argument("--pid", type=int)
    observe.add_argument("--interruption", choices=("sigkill", "timeout", "rss", "process-exit"))
    validate = commands.add_parser("validate")
    validate.add_argument("--result", type=Path, required=True)
    validate.add_argument("--worktree", type=Path, required=True)
    reconcile = commands.add_parser("reconcile-run")
    reconcile.add_argument("--run-id", type=int, required=True)
    for command in (assign_parser, observe, validate, reconcile):
        command.add_argument("--state", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
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
