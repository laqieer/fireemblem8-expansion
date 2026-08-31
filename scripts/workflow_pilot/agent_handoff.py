#!/usr/bin/env python3
"""Validate bounded exact-SHA implementation handoffs against real Git state."""

from __future__ import annotations

import argparse
import copy
import hashlib
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from scripts.workflow_pilot import reporter


SCHEMA_VERSION = 1
COPILOT_TRAILER = (
    "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
)
PROHIBITED_REMOTE_ACTIONS = frozenset(
    {
        "comment",
        "create_remote_ref",
        "dispatch_ci",
        "merge",
        "open_pull_request",
        "push",
        "update_pull_request",
    }
)
REMOTE_ACTIONS = PROHIBITED_REMOTE_ACTIONS | {
    "read_github",
    "request_review",
    "watch_ci",
}
HANDOFF_STATES = {
    "assignment_sent",
    "assignment_received",
    "progressing",
    "committed",
    "handed_off",
    "interrupted",
}
COMPLETE_STATE_SEQUENCE = (
    "assignment_sent",
    "assignment_received",
    "progressing",
    "committed",
    "handed_off",
)
INTERRUPTED_STATE_SEQUENCE = (
    "assignment_sent",
    "assignment_received",
    "progressing",
    "interrupted",
)
RUN_STATUSES = {"completed", "in_progress", "queued"}
RUN_CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "neutral",
    "skipped",
    "success",
}
INPUT_SEAL_DOMAIN = b"workflow-pilot-agent-handoff-input-v1\0"
RESULT_SEAL_DOMAIN = b"workflow-pilot-agent-handoff-result-v1\0"


class HandoffDataError(Exception):
    """The handoff document cannot produce trustworthy coordination evidence."""


def _raise_pilot_error(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except reporter.PilotDataError as error:
        raise HandoffDataError(str(error)) from error


def load_json(path: Path) -> Any:
    try:
        return _raise_pilot_error(reporter.load_json, path)
    except OSError as error:
        raise HandoffDataError(f"cannot read handoff fixture {path}: {error}") from error


def normalized_json(value: Any) -> bytes:
    return reporter.normalized_json(value)


def expect_object(value: Any, label: str) -> dict[str, Any]:
    return _raise_pilot_error(reporter.expect_object, value, label)


def expect_list(value: Any, label: str) -> list[Any]:
    return _raise_pilot_error(reporter.expect_list, value, label)


def expect_keys(value: dict[str, Any], label: str, required: Iterable[str]) -> None:
    _raise_pilot_error(reporter.expect_keys, value, label, required)


def expect_string(value: Any, label: str, allow_empty: bool = False) -> str:
    return _raise_pilot_error(reporter.expect_string, value, label, allow_empty)


def expect_int(value: Any, label: str, minimum: int | None = None) -> int:
    return _raise_pilot_error(reporter.expect_int, value, label, minimum)


def expect_bool(value: Any, label: str) -> bool:
    return _raise_pilot_error(reporter.expect_bool, value, label)


def expect_enum(value: Any, allowed: set[str], label: str) -> str:
    return _raise_pilot_error(reporter.expect_enum, value, allowed, label)


def expect_sha(value: Any, label: str, nullable: bool = False) -> str | None:
    return _raise_pilot_error(reporter.expect_sha, value, label, nullable)


def parse_time(value: Any, label: str, nullable: bool = False) -> datetime | None:
    return _raise_pilot_error(reporter.parse_time, value, label, nullable)


def expect_unique(values: Iterable[Any], label: str) -> None:
    _raise_pilot_error(reporter.expect_unique, values, label)


def run_git(repository_root: Path, *arguments: str) -> bytes:
    return _raise_pilot_error(reporter.run_git, repository_root, *arguments)


def validate_repository_root(repository_root: Path) -> Path:
    return _raise_pilot_error(reporter.validate_repository_root, repository_root)


def _repository_from_origin(repository_root: Path) -> str:
    origin = run_git(repository_root, "remote", "get-url", "origin")
    repository = reporter._github_repository_from_remote(  # noqa: SLF001
        origin.decode("utf-8").strip()
    )
    if repository is None:
        raise HandoffDataError(
            "worktree origin must identify one GitHub owner/repository"
        )
    return repository


def _validate_repository_path(value: Any, label: str, *, prefix: bool) -> str:
    path = expect_string(value, label)
    parts = Path(path.rstrip("/")).parts
    if path.startswith("/") or not parts or ".." in parts or "." in parts:
        raise HandoffDataError(f"{label} must be repository-relative")
    if prefix and not path.endswith("/"):
        return path
    return path


def _parse_states(raw_states: Any, label: str) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    states = expect_list(raw_states, f"{label}.states")
    if not states:
        raise HandoffDataError(f"{label}.states must start with assignment_sent")
    parsed = []
    names = []
    previous_at = None
    for index, raw in enumerate(states):
        state_label = f"{label}.states[{index}]"
        state = expect_object(raw, state_label)
        expect_keys(state, state_label, ("state", "at"))
        name = expect_enum(state["state"], HANDOFF_STATES, f"{state_label}.state")
        at = parse_time(state["at"], f"{state_label}.at")
        if previous_at is not None and at <= previous_at:
            raise HandoffDataError(f"{label}.states must be strictly chronological")
        previous_at = at
        parsed.append(state)
        names.append(name)
    expect_unique(names, f"{label}.states")
    if names[0] != "assignment_sent":
        raise HandoffDataError(f"{label}.states must start with assignment_sent")
    return parsed, tuple(names)


def _parse_evidence(raw_evidence: Any, label: str) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(expect_list(raw_evidence, f"{label}.evidence")):
        evidence_label = f"{label}.evidence[{index}]"
        item = expect_object(raw, evidence_label)
        expect_keys(
            item,
            evidence_label,
            ("id", "kind", "status", "exit_code", "completed_at", "detail"),
        )
        evidence_id = expect_string(item["id"], f"{evidence_label}.id")
        if evidence_id in evidence:
            raise HandoffDataError(f"{label}.evidence repeats {evidence_id!r}")
        expect_enum(
            item["kind"],
            {"acceptance", "budget", "check", "recovery"},
            f"{evidence_label}.kind",
        )
        status = expect_enum(
            item["status"],
            {"failed", "incomplete", "passed"},
            f"{evidence_label}.status",
        )
        exit_code = item["exit_code"]
        if exit_code is not None:
            expect_int(exit_code, f"{evidence_label}.exit_code", 0)
        if status == "passed" and exit_code != 0:
            raise HandoffDataError(
                f"{evidence_label} passed status requires exit_code 0"
            )
        if status == "incomplete" and exit_code is not None:
            raise HandoffDataError(
                f"{evidence_label} incomplete status requires null exit_code"
            )
        parse_time(item["completed_at"], f"{evidence_label}.completed_at")
        expect_string(item["detail"], f"{evidence_label}.detail")
        evidence[evidence_id] = item
    return evidence


def _parse_handoff(raw: Any, index: int) -> dict[str, Any]:
    label = f"handoffs[{index}]"
    handoff = expect_object(raw, label)
    expect_keys(
        handoff,
        label,
        (
            "id",
            "issue",
            "pull_request",
            "owner_id",
            "replaces_handoff_id",
            "assigned_parent_sha",
            "expected_branch",
            "allowed_worktree",
            "allowed_scope",
            "finding_ids",
            "acceptance_criteria",
            "required_checks",
            "budgets",
            "prohibited_remote_actions",
            "max_lifetime_seconds",
            "max_peak_rss_bytes",
            "coordination_turns",
            "peak_rss_bytes",
            "states",
            "evidence",
            "result",
            "interruption",
        ),
    )
    expect_string(handoff["id"], f"{label}.id")
    expect_int(handoff["issue"], f"{label}.issue", 1)
    if handoff["pull_request"] is not None:
        expect_int(handoff["pull_request"], f"{label}.pull_request", 1)
    expect_string(handoff["owner_id"], f"{label}.owner_id")
    if handoff["replaces_handoff_id"] is not None:
        expect_string(
            handoff["replaces_handoff_id"],
            f"{label}.replaces_handoff_id",
        )
    expect_sha(handoff["assigned_parent_sha"], f"{label}.assigned_parent_sha")
    expect_string(handoff["expected_branch"], f"{label}.expected_branch")
    expect_string(handoff["allowed_worktree"], f"{label}.allowed_worktree")

    allowed_scope = expect_list(handoff["allowed_scope"], f"{label}.allowed_scope")
    if not allowed_scope:
        raise HandoffDataError(f"{label}.allowed_scope must not be empty")
    for scope_index, scope in enumerate(allowed_scope):
        _validate_repository_path(
            scope,
            f"{label}.allowed_scope[{scope_index}]",
            prefix=True,
        )
    expect_unique(allowed_scope, f"{label}.allowed_scope")

    finding_ids = expect_list(handoff["finding_ids"], f"{label}.finding_ids")
    for finding_index, finding_id in enumerate(finding_ids):
        expect_string(finding_id, f"{label}.finding_ids[{finding_index}]")
    expect_unique(finding_ids, f"{label}.finding_ids")

    acceptance_ids = []
    required_evidence_ids = set()
    for criterion_index, raw_criterion in enumerate(
        expect_list(handoff["acceptance_criteria"], f"{label}.acceptance_criteria")
    ):
        criterion_label = f"{label}.acceptance_criteria[{criterion_index}]"
        criterion = expect_object(raw_criterion, criterion_label)
        expect_keys(criterion, criterion_label, ("id", "text", "evidence_ids"))
        criterion_id = expect_string(criterion["id"], f"{criterion_label}.id")
        acceptance_ids.append(criterion_id)
        expect_string(criterion["text"], f"{criterion_label}.text")
        evidence_ids = expect_list(
            criterion["evidence_ids"],
            f"{criterion_label}.evidence_ids",
        )
        if not evidence_ids:
            raise HandoffDataError(
                f"{criterion_label}.evidence_ids must not be empty"
            )
        for evidence_index, evidence_id in enumerate(evidence_ids):
            required_evidence_ids.add(
                expect_string(
                    evidence_id,
                    f"{criterion_label}.evidence_ids[{evidence_index}]",
                )
            )
        expect_unique(evidence_ids, f"{criterion_label}.evidence_ids")
    if not acceptance_ids:
        raise HandoffDataError(f"{label}.acceptance_criteria must not be empty")
    expect_unique(acceptance_ids, f"{label}.acceptance_criteria")

    check_ids = []
    check_evidence_ids = {}
    for check_index, raw_check in enumerate(
        expect_list(handoff["required_checks"], f"{label}.required_checks")
    ):
        check_label = f"{label}.required_checks[{check_index}]"
        check = expect_object(raw_check, check_label)
        expect_keys(check, check_label, ("id", "command", "evidence_id"))
        check_id = expect_string(check["id"], f"{check_label}.id")
        check_ids.append(check_id)
        expect_string(check["command"], f"{check_label}.command")
        evidence_id = expect_string(
            check["evidence_id"],
            f"{check_label}.evidence_id",
        )
        required_evidence_ids.add(evidence_id)
        check_evidence_ids[check_id] = evidence_id
    if not check_ids:
        raise HandoffDataError(f"{label}.required_checks must not be empty")
    expect_unique(check_ids, f"{label}.required_checks")

    budgets = expect_object(handoff["budgets"], f"{label}.budgets")
    expect_keys(
        budgets,
        f"{label}.budgets",
        ("changed_lines", "rom_bytes", "ram_bytes", "protocol_changes"),
    )
    for field in ("changed_lines", "rom_bytes", "ram_bytes", "protocol_changes"):
        expect_int(budgets[field], f"{label}.budgets.{field}", 0)

    prohibited = expect_list(
        handoff["prohibited_remote_actions"],
        f"{label}.prohibited_remote_actions",
    )
    for action_index, action in enumerate(prohibited):
        expect_enum(
            action,
            set(PROHIBITED_REMOTE_ACTIONS),
            f"{label}.prohibited_remote_actions[{action_index}]",
        )
    expect_unique(prohibited, f"{label}.prohibited_remote_actions")
    if set(prohibited) != PROHIBITED_REMOTE_ACTIONS:
        raise HandoffDataError(
            f"{label}.prohibited_remote_actions must exactly cover the "
            "implementation-owner remote-action boundary"
        )

    expect_int(
        handoff["max_lifetime_seconds"],
        f"{label}.max_lifetime_seconds",
        1,
    )
    expect_int(
        handoff["max_peak_rss_bytes"],
        f"{label}.max_peak_rss_bytes",
        1,
    )
    expect_int(handoff["coordination_turns"], f"{label}.coordination_turns", 0)
    expect_int(handoff["peak_rss_bytes"], f"{label}.peak_rss_bytes", 0)

    states, state_names = _parse_states(handoff["states"], label)
    evidence = _parse_evidence(handoff["evidence"], label)

    result = handoff["result"]
    if result is not None:
        result = expect_object(result, f"{label}.result")
        expect_keys(result, f"{label}.result", ("sha", "budget_usage"))
        expect_sha(result["sha"], f"{label}.result.sha")
        budget_usage = expect_object(
            result["budget_usage"],
            f"{label}.result.budget_usage",
        )
        expect_keys(
            budget_usage,
            f"{label}.result.budget_usage",
            ("rom_bytes", "ram_bytes", "protocol_changes"),
        )
        for field in ("rom_bytes", "ram_bytes", "protocol_changes"):
            expect_int(
                budget_usage[field],
                f"{label}.result.budget_usage.{field}",
                0,
            )

    interruption = handoff["interruption"]
    if interruption is not None:
        interruption = expect_object(interruption, f"{label}.interruption")
        expect_keys(
            interruption,
            f"{label}.interruption",
            (
                "kind",
                "signal",
                "occurred_at",
                "kernel_evidence",
                "interrupted_check_ids",
                "preserved_paths",
                "recovery_minutes",
                "replacement_handoff_id",
                "host_process_actions",
            ),
        )
        expect_enum(
            interruption["kind"],
            {"sigkill_oom"},
            f"{label}.interruption.kind",
        )
        signal = expect_int(
            interruption["signal"],
            f"{label}.interruption.signal",
            1,
        )
        if signal != 9:
            raise HandoffDataError(
                f"{label}.interruption.signal must be SIGKILL number 9"
            )
        parse_time(
            interruption["occurred_at"],
            f"{label}.interruption.occurred_at",
        )
        expect_string(
            interruption["kernel_evidence"],
            f"{label}.interruption.kernel_evidence",
        )
        interrupted_checks = expect_list(
            interruption["interrupted_check_ids"],
            f"{label}.interruption.interrupted_check_ids",
        )
        if not interrupted_checks:
            raise HandoffDataError(
                f"{label}.interruption.interrupted_check_ids must not be empty"
            )
        for check_index, check_id in enumerate(interrupted_checks):
            expect_string(
                check_id,
                f"{label}.interruption.interrupted_check_ids[{check_index}]",
            )
        expect_unique(
            interrupted_checks,
            f"{label}.interruption.interrupted_check_ids",
        )
        preserved_paths = expect_list(
            interruption["preserved_paths"],
            f"{label}.interruption.preserved_paths",
        )
        if not preserved_paths:
            raise HandoffDataError(
                f"{label}.interruption.preserved_paths must not be empty"
            )
        for path_index, path in enumerate(preserved_paths):
            _validate_repository_path(
                path,
                f"{label}.interruption.preserved_paths[{path_index}]",
                prefix=False,
            )
        expect_unique(
            preserved_paths,
            f"{label}.interruption.preserved_paths",
        )
        expect_int(
            interruption["recovery_minutes"],
            f"{label}.interruption.recovery_minutes",
            0,
        )
        expect_string(
            interruption["replacement_handoff_id"],
            f"{label}.interruption.replacement_handoff_id",
        )
        process_actions = expect_list(
            interruption["host_process_actions"],
            f"{label}.interruption.host_process_actions",
        )
        for action_index, action in enumerate(process_actions):
            expect_string(
                action,
                f"{label}.interruption.host_process_actions[{action_index}]",
            )

    state_times = {
        state["state"]: parse_time(state["at"], f"{label}.states.{state['state']}")
        for state in states
    }
    evidence_start = state_times.get("progressing")
    evidence_end = state_times.get("committed") or state_times.get("interrupted")
    for evidence_id, item in evidence.items():
        completed_at = parse_time(
            item["completed_at"],
            f"{label}.evidence[{evidence_id!r}].completed_at",
        )
        if evidence_start is not None and completed_at < evidence_start:
            raise HandoffDataError(
                f"{label}.evidence {evidence_id!r} predates progressing"
            )
        if evidence_end is not None and completed_at > evidence_end:
            raise HandoffDataError(
                f"{label}.evidence {evidence_id!r} follows its owner boundary"
            )

    handoff["_label"] = label
    handoff["_states"] = states
    handoff["_state_names"] = state_names
    handoff["_evidence"] = evidence
    handoff["_required_evidence_ids"] = required_evidence_ids
    handoff["_check_evidence_ids"] = check_evidence_ids
    return handoff


def _parse_coordinators(raw_coordinators: Any) -> list[dict[str, Any]]:
    coordinators = []
    for index, raw in enumerate(expect_list(raw_coordinators, "coordinators")):
        label = f"coordinators[{index}]"
        coordinator = expect_object(raw, label)
        expect_keys(coordinator, label, ("id", "availability"))
        expect_string(coordinator["id"], f"{label}.id")
        availability = expect_object(
            coordinator["availability"],
            f"{label}.availability",
        )
        expect_keys(
            availability,
            f"{label}.availability",
            (
                "mode",
                "autostop_enabled",
                "stop_on_disconnect",
                "plan",
            ),
        )
        expect_enum(
            availability["mode"],
            {"always_on", "local"},
            f"{label}.availability.mode",
        )
        expect_bool(
            availability["autostop_enabled"],
            f"{label}.availability.autostop_enabled",
        )
        expect_bool(
            availability["stop_on_disconnect"],
            f"{label}.availability.stop_on_disconnect",
        )
        if availability["plan"] is not None:
            plan = expect_object(
                availability["plan"],
                f"{label}.availability.plan",
            )
            expect_keys(
                plan,
                f"{label}.availability.plan",
                ("kind", "available_until"),
            )
            expect_enum(
                plan["kind"],
                {"always_on_takeover", "disable_triggers"},
                f"{label}.availability.plan.kind",
            )
            parse_time(
                plan["available_until"],
                f"{label}.availability.plan.available_until",
            )
        coordinators.append(coordinator)
    return coordinators


def _parse_runs(raw_runs: Any) -> dict[int, dict[str, Any]]:
    runs = {}
    for index, raw in enumerate(expect_list(raw_runs, "workflow_runs")):
        label = f"workflow_runs[{index}]"
        run = expect_object(raw, label)
        expect_keys(
            run,
            label,
            (
                "id",
                "handoff_id",
                "head_sha",
                "status",
                "conclusion",
                "observed_at",
                "source",
            ),
        )
        run_id = expect_int(run["id"], f"{label}.id", 1)
        if run_id in runs:
            raise HandoffDataError(f"duplicate workflow run {run_id}")
        expect_string(run["handoff_id"], f"{label}.handoff_id")
        expect_sha(run["head_sha"], f"{label}.head_sha")
        status = expect_enum(run["status"], RUN_STATUSES, f"{label}.status")
        conclusion = run["conclusion"]
        if status == "completed":
            expect_enum(conclusion, RUN_CONCLUSIONS, f"{label}.conclusion")
        elif conclusion is not None:
            raise HandoffDataError(
                f"{label} active status requires a null conclusion"
            )
        parse_time(run["observed_at"], f"{label}.observed_at")
        if run["source"] != "github-actions-api":
            raise HandoffDataError(
                f"{label}.source must be authoritative 'github-actions-api'"
            )
        runs[run_id] = run
    return runs


def _parse_watchers(raw_watchers: Any) -> list[dict[str, Any]]:
    watchers = []
    watcher_ids = []
    for index, raw in enumerate(expect_list(raw_watchers, "watchers")):
        label = f"watchers[{index}]"
        watcher = expect_object(raw, label)
        expect_keys(
            watcher,
            label,
            (
                "id",
                "coordinator_id",
                "run_id",
                "head_sha",
                "kind",
                "started_at",
                "ended_at",
                "process_result",
            ),
        )
        watcher_ids.append(expect_string(watcher["id"], f"{label}.id"))
        expect_string(watcher["coordinator_id"], f"{label}.coordinator_id")
        expect_int(watcher["run_id"], f"{label}.run_id", 1)
        expect_sha(watcher["head_sha"], f"{label}.head_sha")
        if watcher["kind"] != "direct_shell":
            raise HandoffDataError(f"{label}.kind must be 'direct_shell'")
        started = parse_time(watcher["started_at"], f"{label}.started_at")
        ended = parse_time(watcher["ended_at"], f"{label}.ended_at")
        if ended < started:
            raise HandoffDataError(f"{label}.ended_at precedes started_at")
        expect_enum(
            watcher["process_result"],
            {"error", "success", "timeout"},
            f"{label}.process_result",
        )
        watchers.append(watcher)
    expect_unique(watcher_ids, "watcher IDs")
    return watchers


def _parse_remote_actions(raw_actions: Any) -> list[dict[str, Any]]:
    actions = []
    action_ids = []
    for index, raw in enumerate(expect_list(raw_actions, "remote_actions")):
        label = f"remote_actions[{index}]"
        action = expect_object(raw, label)
        expect_keys(
            action,
            label,
            ("id", "handoff_id", "actor_id", "action", "occurred_at"),
        )
        action_ids.append(expect_string(action["id"], f"{label}.id"))
        expect_string(action["handoff_id"], f"{label}.handoff_id")
        expect_string(action["actor_id"], f"{label}.actor_id")
        expect_enum(action["action"], set(REMOTE_ACTIONS), f"{label}.action")
        parse_time(action["occurred_at"], f"{label}.occurred_at")
        actions.append(action)
    expect_unique(action_ids, "remote-action IDs")
    return actions


def _changed_paths_and_lines(
    repository_root: Path,
    parent_sha: str,
    result_sha: str,
) -> tuple[list[str], int]:
    names = (
        run_git(
            repository_root,
            "diff",
            "--name-only",
            "--no-renames",
            parent_sha,
            result_sha,
        )
        .decode("utf-8")
        .splitlines()
    )
    lines = 0
    for raw_line in (
        run_git(
            repository_root,
            "diff",
            "--numstat",
            "--no-renames",
            parent_sha,
            result_sha,
        )
        .decode("utf-8")
        .splitlines()
    ):
        additions, deletions, _path = raw_line.split("\t", 2)
        if additions == "-" or deletions == "-":
            raise HandoffDataError(
                "changed-line budget cannot admit an unquantified binary diff"
            )
        lines += int(additions) + int(deletions)
    return names, lines


def _path_is_allowed(path: str, scopes: list[str]) -> bool:
    return any(
        path.startswith(scope) if scope.endswith("/") else path == scope
        for scope in scopes
    )


def _commit_message(repository_root: Path, sha: str) -> str:
    raw = run_git(repository_root, "cat-file", "commit", sha)
    try:
        _headers, message = raw.split(b"\n\n", 1)
    except ValueError as error:
        raise HandoffDataError(f"commit {sha} has no message boundary") from error
    try:
        return reporter.canonical_commit_message(message, sha)
    except reporter.PilotDataError as error:
        raise HandoffDataError(str(error)) from error


def _status_paths(status: str) -> set[str]:
    paths = set()
    for line in status.splitlines():
        if len(line) >= 4:
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            paths.add(path)
    return paths


def _empty_handoff_result(handoff: dict[str, Any]) -> dict[str, Any]:
    states = handoff["_states"]
    lifetime_seconds = 0
    if len(states) >= 2:
        lifetime = _raise_pilot_error(
            reporter.duration_seconds,
            parse_time(states[0]["at"], "state start"),
            parse_time(states[-1]["at"], "state end"),
            f"handoff {handoff['id']} lifetime",
        )
        if lifetime != lifetime.to_integral_value():
            raise HandoffDataError(
                f"handoff {handoff['id']!r} lifetime must resolve to whole seconds"
            )
        lifetime_seconds = int(lifetime)
    interruption = handoff["interruption"]
    return {
        "id": handoff["id"],
        "owner_id": handoff["owner_id"],
        "issue": handoff["issue"],
        "pull_request": handoff["pull_request"],
        "assigned_at": states[0]["at"] if states else None,
        "closed_at": (
            states[-1]["at"]
            if handoff["_state_names"] in {
                COMPLETE_STATE_SEQUENCE,
                INTERRUPTED_STATE_SEQUENCE,
            }
            else None
        ),
        "state": handoff["_state_names"][-1] if states else None,
        "outcome": "in_progress",
        "result_sha": None,
        "changed_lines": None,
        "stale_response": False,
        "lifetime_seconds": lifetime_seconds,
        "peak_rss_bytes": handoff["peak_rss_bytes"],
        "coordination_turns": handoff["coordination_turns"],
        "recovery_minutes": (
            interruption["recovery_minutes"] if interruption is not None else 0
        ),
        "rejection_codes": [],
    }


def validate_document(raw: Any, repository_root: Path) -> dict[str, Any]:
    document = copy.deepcopy(expect_object(raw, "handoff document"))
    expect_keys(
        document,
        "handoff document",
        (
            "schema_version",
            "repository",
            "coordinators",
            "handoffs",
            "workflow_runs",
            "watchers",
            "remote_actions",
        ),
    )
    schema_version = expect_int(
        document["schema_version"],
        "handoff document.schema_version",
        1,
    )
    if schema_version != SCHEMA_VERSION:
        raise HandoffDataError(
            f"handoff document.schema_version must be {SCHEMA_VERSION}"
        )
    repository = expect_string(
        document["repository"],
        "handoff document.repository",
    )
    repository_root = validate_repository_root(repository_root)
    if repository != _repository_from_origin(repository_root):
        raise HandoffDataError(
            "handoff document.repository does not match the worktree origin"
        )
    input_seal = hashlib.sha256(
        INPUT_SEAL_DOMAIN + normalized_json(document)
    ).hexdigest()

    coordinators = _parse_coordinators(document["coordinators"])
    handoffs = [
        _parse_handoff(raw_handoff, index)
        for index, raw_handoff in enumerate(
            expect_list(document["handoffs"], "handoffs")
        )
    ]
    if not handoffs:
        raise HandoffDataError("handoffs must not be empty")
    handoffs_by_id = {}
    for handoff in handoffs:
        if handoff["id"] in handoffs_by_id:
            raise HandoffDataError(f"duplicate handoff {handoff['id']!r}")
        handoffs_by_id[handoff["id"]] = handoff
    runs = _parse_runs(document["workflow_runs"])
    watchers = _parse_watchers(document["watchers"])
    remote_actions = _parse_remote_actions(document["remote_actions"])

    global_rejections = set()
    handoff_rejections = {handoff["id"]: set() for handoff in handoffs}

    def reject(code: str, handoff_id: str | None = None) -> None:
        global_rejections.add(code)
        if handoff_id is not None:
            handoff_rejections[handoff_id].add(code)

    if len(coordinators) != 1:
        for handoff in handoffs:
            reject("duplicate-coordinator", handoff["id"])
        coordinator_id = coordinators[0]["id"] if coordinators else None
    else:
        coordinator_id = coordinators[0]["id"]
        availability = coordinators[0]["availability"]
        hibernation_risk = (
            availability["mode"] == "local"
            and (
                availability["autostop_enabled"]
                or availability["stop_on_disconnect"]
            )
        )
        if hibernation_risk and availability["plan"] is None:
            for handoff in handoffs:
                reject("coordinator-unavailable", handoff["id"])
        elif hibernation_risk:
            activity_times = [
                parse_time(state["at"], f"handoff state {state['state']}")
                for handoff in handoffs
                for state in handoff["_states"]
            ]
            activity_times.extend(
                parse_time(run["observed_at"], f"run {run['id']}.observed_at")
                for run in runs.values()
            )
            activity_times.extend(
                parse_time(
                    watcher["ended_at"],
                    f"watcher {watcher['id']}.ended_at",
                )
                for watcher in watchers
            )
            available_until = parse_time(
                availability["plan"]["available_until"],
                "coordinator availability plan.available_until",
            )
            if activity_times and available_until < max(activity_times):
                for handoff in handoffs:
                    reject("coordinator-unavailable", handoff["id"])
        if availability["mode"] == "always_on" and (
            availability["autostop_enabled"]
            or availability["stop_on_disconnect"]
        ):
            for handoff in handoffs:
                reject("coordinator-unavailable", handoff["id"])

    owner_counts = Counter(handoff["owner_id"] for handoff in handoffs)
    duplicate_owners = {
        owner_id for owner_id, count in owner_counts.items() if count > 1
    }
    for handoff in handoffs:
        if handoff["owner_id"] in duplicate_owners:
            reject("duplicate-owner", handoff["id"])

    actual_branch = run_git(
        repository_root,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
    ).decode("utf-8").strip()
    actual_head = run_git(repository_root, "rev-parse", "HEAD").decode("ascii").strip()
    status = run_git(
        repository_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).decode("utf-8")
    conflicts = run_git(
        repository_root,
        "diff",
        "--name-only",
        "--diff-filter=U",
    ).decode("utf-8").splitlines()
    dirty_paths = _status_paths(status)

    results = {}
    for handoff in handoffs:
        handoff_id = handoff["id"]
        result = _empty_handoff_result(handoff)
        results[handoff_id] = result
        label = handoff["_label"]

        try:
            assigned_worktree = Path(handoff["allowed_worktree"])
            allowed_worktree = assigned_worktree.resolve(strict=True)
        except OSError:
            reject("wrong-worktree", handoff_id)
        else:
            if (
                not assigned_worktree.is_absolute()
                or str(allowed_worktree) != handoff["allowed_worktree"]
                or allowed_worktree != repository_root
            ):
                reject("wrong-worktree", handoff_id)
        if handoff["expected_branch"] != actual_branch:
            reject("unrelated-branch", handoff_id)
        if handoff["peak_rss_bytes"] > handoff["max_peak_rss_bytes"]:
            reject("owner-rss-exceeded", handoff_id)
        if result["lifetime_seconds"] > handoff["max_lifetime_seconds"]:
            reject("owner-lifetime-exceeded", handoff_id)

        state_names = handoff["_state_names"]
        if handoff["result"] is not None:
            if state_names != COMPLETE_STATE_SEQUENCE:
                reject("incomplete-lifecycle", handoff_id)
            result_sha = handoff["result"]["sha"]
            result["result_sha"] = result_sha
            if result_sha == handoff["assigned_parent_sha"]:
                reject("stale-result", handoff_id)
                result["stale_response"] = True
            if result_sha != actual_head:
                reject("result-not-worktree-head", handoff_id)
            try:
                commit_line = (
                    run_git(
                        repository_root,
                        "rev-list",
                        "--parents",
                        "-n",
                        "1",
                        result_sha,
                    )
                    .decode("ascii")
                    .strip()
                    .split()
                )
            except HandoffDataError:
                reject("missing-commit", handoff_id)
            else:
                if len(commit_line) != 2 or commit_line[1] != handoff["assigned_parent_sha"]:
                    reject("wrong-parent", handoff_id)
                try:
                    message = _commit_message(repository_root, result_sha)
                except HandoffDataError:
                    reject("missing-commit", handoff_id)
                else:
                    lines = message.split("\n")
                    if (
                        not lines
                        or lines[-1] != COPILOT_TRAILER
                        or lines.count(COPILOT_TRAILER) != 1
                    ):
                        reject("missing-copilot-trailer", handoff_id)
                try:
                    changed_paths, changed_lines = _changed_paths_and_lines(
                        repository_root,
                        handoff["assigned_parent_sha"],
                        result_sha,
                    )
                except HandoffDataError:
                    reject("unquantified-diff", handoff_id)
                else:
                    result["changed_lines"] = changed_lines
                    if any(
                        not _path_is_allowed(path, handoff["allowed_scope"])
                        for path in changed_paths
                    ):
                        reject("scope-violation", handoff_id)
                    if changed_lines > handoff["budgets"]["changed_lines"]:
                        reject("changed-lines-budget-exceeded", handoff_id)
            if status:
                reject("dirty-worktree", handoff_id)
            if conflicts:
                reject("conflicting-worktree", handoff_id)
            for field in ("rom_bytes", "ram_bytes", "protocol_changes"):
                if (
                    handoff["result"]["budget_usage"][field]
                    > handoff["budgets"][field]
                ):
                    reject(f"{field.replace('_', '-')}-budget-exceeded", handoff_id)
            missing_evidence = sorted(
                handoff["_required_evidence_ids"] - set(handoff["_evidence"])
            )
            if missing_evidence:
                reject("missing-evidence", handoff_id)
            for evidence_id in handoff["_required_evidence_ids"]:
                evidence = handoff["_evidence"].get(evidence_id)
                if evidence is not None and evidence["status"] != "passed":
                    reject("incomplete-evidence", handoff_id)
            for check_id, evidence_id in handoff["_check_evidence_ids"].items():
                evidence = handoff["_evidence"].get(evidence_id)
                if (
                    evidence is None
                    or evidence["kind"] != "check"
                    or evidence["status"] != "passed"
                    or evidence["exit_code"] != 0
                ):
                    reject("incomplete-check", handoff_id)
            if not handoff["_evidence"]:
                reject("missing-evidence", handoff_id)
        elif handoff["interruption"] is not None:
            result["outcome"] = "interrupted"
            if state_names != INTERRUPTED_STATE_SEQUENCE:
                reject("incomplete-lifecycle", handoff_id)
            interruption = handoff["interruption"]
            interrupted_at = parse_time(
                interruption["occurred_at"],
                f"{label}.interruption.occurred_at",
            )
            state_at = parse_time(
                handoff["_states"][-1]["at"],
                f"{label}.states[-1].at",
            )
            if interrupted_at != state_at:
                reject("interruption-time-mismatch", handoff_id)
            if conflicts:
                reject("conflicting-worktree", handoff_id)
            if not status:
                reject("oom-worktree-not-preserved", handoff_id)
            if any(
                path not in dirty_paths
                for path in interruption["preserved_paths"]
            ):
                reject("oom-worktree-not-preserved", handoff_id)
            if any(
                not _path_is_allowed(path, handoff["allowed_scope"])
                for path in interruption["preserved_paths"]
            ):
                reject("scope-violation", handoff_id)
            if interruption["host_process_actions"]:
                reject("host-process-action-prohibited", handoff_id)
            for check_id in interruption["interrupted_check_ids"]:
                evidence_id = handoff["_check_evidence_ids"].get(check_id)
                evidence = handoff["_evidence"].get(evidence_id)
                if (
                    evidence is None
                    or evidence["kind"] != "check"
                    or evidence["status"] != "incomplete"
                    or evidence["exit_code"] is not None
                ):
                    reject("interrupted-check-not-incomplete", handoff_id)
        else:
            if state_names == COMPLETE_STATE_SEQUENCE:
                reject("missing-commit", handoff_id)
            else:
                reject("incomplete-lifecycle", handoff_id)

    replacements_by_parent: dict[str, list[dict[str, Any]]] = {}
    for handoff in handoffs:
        replaced = handoff["replaces_handoff_id"]
        if replaced is not None:
            replacements_by_parent.setdefault(replaced, []).append(handoff)
            if replaced not in handoffs_by_id:
                reject("orphan-replacement", handoff["id"])
    for handoff in handoffs:
        interruption = handoff["interruption"]
        if interruption is None:
            continue
        replacements = replacements_by_parent.get(handoff["id"], [])
        if (
            len(replacements) != 1
            or replacements[0]["id"] != interruption["replacement_handoff_id"]
        ):
            reject("replacement-owner-count", handoff["id"])
            continue
        replacement = replacements[0]
        if replacement["owner_id"] == handoff["owner_id"]:
            reject("replacement-owner-reused", handoff["id"])
        for field in (
            "issue",
            "pull_request",
            "assigned_parent_sha",
            "expected_branch",
            "allowed_worktree",
        ):
            if replacement[field] != handoff[field]:
                reject("replacement-context-mismatch", handoff["id"])

    for action in remote_actions:
        handoff = handoffs_by_id.get(action["handoff_id"])
        if handoff is None:
            raise HandoffDataError(
                f"remote action {action['id']!r} references an unknown handoff"
            )
        if (
            action["actor_id"] == handoff["owner_id"]
            and action["action"] in PROHIBITED_REMOTE_ACTIONS
        ):
            reject("implementation-owner-remote-action", handoff["id"])

    watcher_counts = Counter(watcher["run_id"] for watcher in watchers)
    if any(count > 1 for count in watcher_counts.values()):
        reject("duplicate-watcher")
    watcher_results = []
    for run_id, run in sorted(runs.items()):
        matching = [
            watcher
            for watcher in watchers
            if watcher["run_id"] == run_id
        ]
        if len(matching) != 1:
            reject("missing-or-duplicate-watcher")
            continue
        watcher = matching[0]
        if watcher["head_sha"] != run["head_sha"]:
            reject("watcher-run-mismatch")
        if watcher["coordinator_id"] != coordinator_id:
            reject("watcher-owner-mismatch")
        handoff = handoffs_by_id.get(run["handoff_id"])
        if handoff is None:
            raise HandoffDataError(
                f"workflow run {run_id} references an unknown handoff"
            )
        if handoff["result"] is None:
            reject("run-without-commit", handoff["id"])
        elif handoff["result"]["sha"] != run["head_sha"]:
            reject("stale-run", handoff["id"])
        outcome = run["conclusion"] if run["status"] == "completed" else "active"
        reconciled = (
            watcher["process_result"] != "success"
            and run["status"] == "completed"
        )
        if parse_time(
            run["observed_at"],
            f"run {run_id}.observed_at",
        ) < parse_time(
            watcher["ended_at"],
            f"watcher {watcher['id']}.ended_at",
        ):
            reject("watcher-authority-stale")
        watcher_results.append(
            {
                "run_id": run_id,
                "head_sha": run["head_sha"],
                "watcher_process_result": watcher["process_result"],
                "authoritative_outcome": outcome,
                "reconciled": reconciled,
            }
        )
        if run["status"] != "completed":
            reject("authoritative-run-incomplete")
        elif run["conclusion"] != "success":
            reject("authoritative-run-failed")
    for watcher in watchers:
        if watcher["run_id"] not in runs:
            raise HandoffDataError(
                f"watcher {watcher['id']!r} references an unknown workflow run"
            )

    for handoff_id, result in results.items():
        rejection_codes = sorted(handoff_rejections[handoff_id])
        result["rejection_codes"] = rejection_codes
        if result["outcome"] == "interrupted":
            pass
        elif results[handoff_id]["result_sha"] is not None and not rejection_codes:
            result["outcome"] = "accepted"
        elif rejection_codes:
            result["outcome"] = "rejected"

    completed = [
        result for result in results.values() if result["outcome"] == "accepted"
    ]
    trusted_push_eligible = bool(completed) and not any(
        code
        for code in global_rejections
        if code not in {"authoritative-run-failed", "authoritative-run-incomplete"}
    )
    delivery_eligible = (
        trusted_push_eligible
        and all(
            run["status"] == "completed" and run["conclusion"] == "success"
            for run in runs.values()
        )
    )
    recovery_count = sum(
        handoff["interruption"] is not None for handoff in handoffs
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "coordinator_id": coordinator_id,
        "handoffs": [results[handoff["id"]] for handoff in handoffs],
        "watchers": watcher_results,
        "summary": {
            "trusted_push_eligible": trusted_push_eligible,
            "delivery_eligible": delivery_eligible,
            "accepted_handoffs": len(completed),
            "rejected_handoffs": sum(
                result["outcome"] == "rejected" for result in results.values()
            ),
            "interrupted_handoffs": sum(
                result["outcome"] == "interrupted" for result in results.values()
            ),
            "stale_responses": sum(
                result["stale_response"] for result in results.values()
            ),
            "max_owner_lifetime_seconds": max(
                result["lifetime_seconds"] for result in results.values()
            ),
            "max_peak_rss_bytes": max(
                result["peak_rss_bytes"] for result in results.values()
            ),
            "coordination_turns": sum(
                result["coordination_turns"] for result in results.values()
            ),
            "recovery_count": recovery_count,
            "recovery_minutes": sum(
                result["recovery_minutes"] for result in results.values()
            ),
            "rejection_codes": sorted(global_rejections),
        },
        "input_seal": input_seal,
    }
    report["result_seal"] = hashlib.sha256(
        RESULT_SEAL_DOMAIN + normalized_json(report)
    ).hexdigest()
    return report


def reporter_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for handoff in result["handoffs"]:
        records.append(
            {
                "id": handoff["id"],
                "owner_id": handoff["owner_id"],
                "assigned_at": handoff["assigned_at"],
                "closed_at": handoff["closed_at"],
                "outcome": handoff["outcome"],
                "rejection_codes": handoff["rejection_codes"],
                "peak_rss_bytes": handoff["peak_rss_bytes"],
                "coordination_turns": handoff["coordination_turns"],
                "recovery_minutes": handoff["recovery_minutes"],
            }
        )
    return records


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate one immutable bounded implementation-handoff document "
            "against the exact real Git worktree."
        )
    )
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = validate_document(load_json(args.fixture), args.worktree)
    except HandoffDataError as error:
        print(f"workflow-pilot handoff: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(normalized_json(result))
    return 0 if result["summary"]["trusted_push_eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
