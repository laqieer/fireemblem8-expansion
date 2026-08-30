#!/usr/bin/env python3
"""Produce a deterministic workflow-pilot report from immutable fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVERT_RE = re.compile(r"(?im)^This reverts commit ([0-9a-f]{40})\.\s*$")
REVIEW_BOT = "copilot-pull-request-reviewer[bot]"

RISK_BOUNDARIES = {
    "abi",
    "archival",
    "generated-data",
    "lifecycle",
    "localization",
    "migration",
    "none",
    "protocol",
    "runtime",
    "save",
    "security",
}
THRESHOLD_TRIGGERS = {
    "changed-files",
    "changed-lines",
    "major-boundaries",
    "none",
    "risk-boundary",
}
GATE_MODES = {"concurrent", "review-first"}
PILOT_DISPOSITIONS = {
    "baseline-only",
    "evaluate",
    "excluded",
    "graduated",
    "paused",
}
ARTIFACT_DISPOSITIONS = {"Delete", "Derive", "Consolidate", "Graduate"}
WORK_STATES = {"closed", "merged", "open"}
REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"}
RUN_STATUSES = {"completed", "in_progress", "queued"}
RUN_CONCLUSIONS = {"action_required", "cancelled", "failure", "neutral", "skipped", "success"}
EVENT_TYPES = {
    "artifact_checkpoint",
    "base_changed",
    "broken_master",
    "build_saved",
    "candidate_superseded",
    "closed",
    "conflict_detected",
    "deletion_proof",
    "dependency_changed",
    "escaped_defect",
    "manual_reject",
    "metadata_maintenance",
    "pilot_coordination",
    "pre_graduation",
    "reopened",
    "review_saved",
    "security_finding",
    "threshold_override_introduced",
}
EDGE_TYPES = {"checks", "consumes", "derives", "review_depends_on"}
SAFETY_EVENT_TYPES = {
    "broken_master",
    "conflict_detected",
    "escaped_defect",
    "manual_reject",
    "security_finding",
}
DELETION_TRIGGER_TYPES = {
    "artifact_checkpoint",
    "dependency_changed",
    "pre_graduation",
}
GENERATED_PREFIXES = (
    "reports/",
    "src/generated/",
    "include/generated/",
)
GENERATED_SUFFIXES = (
    ".generated.json",
    ".generated.md",
)


class PilotDataError(Exception):
    """The inputs cannot produce trustworthy pilot evidence."""


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PilotDataError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_no_duplicates,
        )
    except OSError as error:
        raise PilotDataError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise PilotDataError(f"invalid JSON in {path}: {error}") from error


def normalized_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def threshold_override_digest(
    pull_request: int,
    index: int,
    override: dict[str, Any],
) -> str:
    payload = {
        "enabled": override["enabled"],
        "index": index,
        "pull_request": pull_request,
        "reason": override["reason"],
    }
    return hashlib.sha256(normalized_json(payload)).hexdigest()


def expect_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PilotDataError(f"{label} must be an object")
    return value


def expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PilotDataError(f"{label} must be a list")
    return value


def expect_keys(
    value: dict[str, Any],
    label: str,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise PilotDataError(f"{label} is missing fields: {', '.join(missing)}")
    if unknown:
        raise PilotDataError(f"{label} has unknown fields: {', '.join(unknown)}")


def expect_string(value: Any, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise PilotDataError(f"{label} must be a nonempty string")
    return value


def expect_int(value: Any, label: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PilotDataError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise PilotDataError(f"{label} must be at least {minimum}")
    return value


def expect_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise PilotDataError(f"{label} must be a boolean")
    return value


def expect_enum(value: Any, allowed: set[str], label: str) -> str:
    if value not in allowed:
        raise PilotDataError(
            f"{label} must be one of {', '.join(sorted(allowed))}, got {value!r}"
        )
    return value


def expect_sha(value: Any, label: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise PilotDataError(f"{label} must be a full lowercase Git SHA")
    return value


def parse_time(value: Any, label: str, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PilotDataError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise PilotDataError(f"{label} is not a valid timestamp") from error
    if parsed.tzinfo != timezone.utc:
        raise PilotDataError(f"{label} must use UTC")
    return parsed


def expect_unique(values: Iterable[Any], label: str) -> None:
    sequence = list(values)
    if len(sequence) != len(set(sequence)):
        raise PilotDataError(f"{label} contains duplicates")


def rounded_tenth_hours(delta_seconds: Decimal) -> Decimal:
    return (delta_seconds / Decimal(3600)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )


def median_tenth(values: list[Decimal]) -> str | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    result = ordered[midpoint]
    return str(result.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def duration_seconds(start: datetime, end: datetime, label: str) -> Decimal:
    if end < start:
        raise PilotDataError(f"{label} ends before it starts")
    return Decimal(str((end - start).total_seconds()))


def is_ancestor(
    ancestor: str,
    descendant: str,
    commits: dict[str, dict[str, Any]],
) -> bool:
    pending = [descendant]
    visited = set()
    while pending:
        candidate = pending.pop()
        if candidate == ancestor:
            return True
        if candidate in visited:
            continue
        visited.add(candidate)
        commit = commits.get(candidate)
        if commit is not None:
            pending.extend(commit["parents"])
    return False


def _validate_fixture_root(fixture: dict[str, Any]) -> None:
    expect_keys(
        fixture,
        "fixture",
        (
            "schema_version",
            "repository",
            "base_sha",
            "captured_at",
            "lifecycle_as_of",
            "window",
            "default_branch",
            "workflow_sample_size",
            "build_workflow",
            "spotlight_pr",
            "pull_requests",
            "issues",
            "reviews",
            "review_findings",
            "workflow_runs",
            "commits",
            "events",
            "artifacts",
            "dependency_edges",
        ),
    )
    if fixture["schema_version"] != SCHEMA_VERSION:
        raise PilotDataError(f"fixture schema_version must be {SCHEMA_VERSION}")
    expect_string(fixture["repository"], "fixture.repository")
    expect_sha(fixture["base_sha"], "fixture.base_sha")
    captured = parse_time(fixture["captured_at"], "fixture.captured_at")
    lifecycle_as_of = parse_time(
        fixture["lifecycle_as_of"], "fixture.lifecycle_as_of"
    )
    if lifecycle_as_of < captured:
        raise PilotDataError(
            "fixture.lifecycle_as_of must not precede fixture.captured_at"
        )
    window = expect_object(fixture["window"], "fixture.window")
    expect_keys(window, "fixture.window", ("start", "end"))
    start = parse_time(window["start"], "fixture.window.start")
    end = parse_time(window["end"], "fixture.window.end")
    if start > end:
        raise PilotDataError("fixture.window start must not follow end")
    if captured != end:
        raise PilotDataError("fixture.captured_at must equal the inclusive window end")
    expect_string(fixture["default_branch"], "fixture.default_branch")
    expect_int(fixture["workflow_sample_size"], "fixture.workflow_sample_size", 1)
    expect_string(fixture["build_workflow"], "fixture.build_workflow")
    expect_int(fixture["spotlight_pr"], "fixture.spotlight_pr", 1)
    for field in (
        "pull_requests",
        "issues",
        "reviews",
        "review_findings",
        "workflow_runs",
        "commits",
        "events",
        "artifacts",
        "dependency_edges",
    ):
        expect_list(fixture[field], f"fixture.{field}")


def validate_pull_requests(fixture: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    required = (
        "number",
        "state",
        "created_at",
        "merged_at",
        "closed_at",
        "base_ref",
        "head_branch",
        "head_sha",
        "merge_sha",
        "issue_numbers",
        "review_ids",
        "commit_shas",
        "additions",
        "deletions",
        "files",
    )
    for index, raw in enumerate(fixture["pull_requests"]):
        label = f"pull_requests[{index}]"
        item = expect_object(raw, label)
        expect_keys(item, label, required)
        number = expect_int(item["number"], f"{label}.number", 1)
        if number in result:
            raise PilotDataError(f"duplicate pull request {number}")
        state = expect_enum(item["state"], WORK_STATES, f"{label}.state")
        created = parse_time(item["created_at"], f"{label}.created_at")
        merged = parse_time(item["merged_at"], f"{label}.merged_at", nullable=True)
        closed = parse_time(item["closed_at"], f"{label}.closed_at", nullable=True)
        if merged is not None and merged < created:
            raise PilotDataError(f"{label}.merged_at precedes creation")
        if closed is not None and closed < created:
            raise PilotDataError(f"{label}.closed_at precedes creation")
        if state == "merged" and (merged is None or closed is None):
            raise PilotDataError(f"{label} merged state requires merged_at and closed_at")
        if state == "open" and (merged is not None or closed is not None):
            raise PilotDataError(f"{label} open state cannot have closure timestamps")
        if state == "closed" and (closed is None or merged is not None):
            raise PilotDataError(f"{label} closed state requires only closed_at")
        expect_string(item["base_ref"], f"{label}.base_ref")
        expect_string(item["head_branch"], f"{label}.head_branch")
        expect_sha(item["head_sha"], f"{label}.head_sha")
        expect_sha(item["merge_sha"], f"{label}.merge_sha", nullable=True)
        if state == "merged" and item["merge_sha"] is None:
            raise PilotDataError(f"{label} merged state requires merge_sha")
        for field in ("issue_numbers", "review_ids"):
            values = expect_list(item[field], f"{label}.{field}")
            for value in values:
                expect_int(value, f"{label}.{field} member", 1)
            expect_unique(values, f"{label}.{field}")
        commit_shas = expect_list(item["commit_shas"], f"{label}.commit_shas")
        for value in commit_shas:
            expect_sha(value, f"{label}.commit_shas member")
        expect_unique(commit_shas, f"{label}.commit_shas")
        expect_int(item["additions"], f"{label}.additions", 0)
        expect_int(item["deletions"], f"{label}.deletions", 0)
        files = expect_list(item["files"], f"{label}.files")
        for value in files:
            expect_string(value, f"{label}.files member")
            if value.startswith("/") or ".." in Path(value).parts:
                raise PilotDataError(f"{label}.files contains a non-repository path")
        expect_unique(files, f"{label}.files")
        result[number] = item
    if fixture["spotlight_pr"] not in result:
        raise PilotDataError("fixture.spotlight_pr has no pull-request record")
    expect_unique(
        (item["head_branch"] for item in result.values()),
        "pull-request head branches",
    )
    return result


def validate_issues(fixture: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for index, raw in enumerate(fixture["issues"]):
        label = f"issues[{index}]"
        item = expect_object(raw, label)
        expect_keys(item, label, ("number", "state", "created_at", "closed_at"))
        number = expect_int(item["number"], f"{label}.number", 1)
        if number in result:
            raise PilotDataError(f"duplicate issue {number}")
        state = expect_enum(item["state"], {"closed", "open"}, f"{label}.state")
        created = parse_time(item["created_at"], f"{label}.created_at")
        closed = parse_time(item["closed_at"], f"{label}.closed_at", nullable=True)
        if state == "closed" and closed is None:
            raise PilotDataError(f"{label} closed state requires closed_at")
        if state == "open" and closed is not None:
            raise PilotDataError(f"{label} open state cannot have closed_at")
        if closed is not None and closed < created:
            raise PilotDataError(f"{label}.closed_at precedes creation")
        result[number] = item
    return result


def validate_reviews(fixture: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    required = (
        "id",
        "pr_number",
        "author",
        "submitted_at",
        "commit_sha",
        "state",
        "thread_ids",
    )
    for index, raw in enumerate(fixture["reviews"]):
        label = f"reviews[{index}]"
        item = expect_object(raw, label)
        expect_keys(item, label, required)
        review_id = expect_int(item["id"], f"{label}.id", 1)
        if review_id in result:
            raise PilotDataError(f"duplicate review {review_id}")
        expect_int(item["pr_number"], f"{label}.pr_number", 1)
        expect_string(item["author"], f"{label}.author")
        parse_time(item["submitted_at"], f"{label}.submitted_at")
        expect_sha(item["commit_sha"], f"{label}.commit_sha")
        expect_enum(item["state"], REVIEW_STATES, f"{label}.state")
        threads = expect_list(item["thread_ids"], f"{label}.thread_ids")
        for thread_id in threads:
            expect_string(thread_id, f"{label}.thread_ids member")
        expect_unique(threads, f"{label}.thread_ids")
        result[review_id] = item
    return result


def validate_findings(fixture: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for index, raw in enumerate(fixture["review_findings"]):
        label = f"review_findings[{index}]"
        item = expect_object(raw, label)
        expect_keys(
            item,
            label,
            (
                "id",
                "review_id",
                "thread_id",
                "created_at",
                "resolved_at",
                "outdated",
                "path",
            ),
        )
        finding_id = expect_int(item["id"], f"{label}.id", 1)
        if finding_id in result:
            raise PilotDataError(f"duplicate review finding {finding_id}")
        expect_int(item["review_id"], f"{label}.review_id", 1)
        expect_string(item["thread_id"], f"{label}.thread_id")
        created = parse_time(item["created_at"], f"{label}.created_at")
        resolved = parse_time(
            item["resolved_at"], f"{label}.resolved_at", nullable=True
        )
        if resolved is not None and resolved <= created:
            raise PilotDataError(f"{label}.resolved_at must follow creation")
        expect_bool(item["outdated"], f"{label}.outdated")
        expect_string(item["path"], f"{label}.path")
        result[finding_id] = item
    return result


def validate_runs(fixture: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    required = (
        "id",
        "workflow",
        "event",
        "status",
        "conclusion",
        "created_at",
        "started_at",
        "completed_at",
        "head_sha",
        "head_branch",
        "attempt",
    )
    captured = parse_time(fixture["captured_at"], "fixture.captured_at")
    for index, raw in enumerate(fixture["workflow_runs"]):
        label = f"workflow_runs[{index}]"
        item = expect_object(raw, label)
        expect_keys(item, label, required)
        run_id = expect_int(item["id"], f"{label}.id", 1)
        if run_id in result:
            raise PilotDataError(f"duplicate workflow run {run_id}")
        expect_string(item["workflow"], f"{label}.workflow")
        expect_string(item["event"], f"{label}.event")
        status = expect_enum(item["status"], RUN_STATUSES, f"{label}.status")
        conclusion = item["conclusion"]
        created = parse_time(item["created_at"], f"{label}.created_at")
        started = parse_time(item["started_at"], f"{label}.started_at", nullable=True)
        completed = parse_time(
            item["completed_at"], f"{label}.completed_at", nullable=True
        )
        if created > captured:
            raise PilotDataError(f"{label}.created_at follows the snapshot")
        if started is not None and started < created:
            raise PilotDataError(f"{label}.started_at precedes creation")
        if completed is not None and started is None:
            raise PilotDataError(f"{label}.completed_at requires started_at")
        if completed is not None and completed > captured:
            raise PilotDataError(f"{label}.completed_at follows the snapshot")
        if status == "completed":
            expect_enum(conclusion, RUN_CONCLUSIONS, f"{label}.conclusion")
            if completed is None:
                raise PilotDataError(f"{label} completed status requires completed_at")
        else:
            if conclusion is not None or completed is not None:
                raise PilotDataError(
                    f"{label} active status requires null conclusion/completed_at"
                )
        expect_sha(item["head_sha"], f"{label}.head_sha")
        expect_string(item["head_branch"], f"{label}.head_branch")
        expect_int(item["attempt"], f"{label}.attempt", 1)
        result[run_id] = item
    return result


def validate_commits(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(fixture["commits"]):
        label = f"commits[{index}]"
        item = expect_object(raw, label)
        expect_keys(item, label, ("sha", "committed_at", "parents", "message"))
        sha = expect_sha(item["sha"], f"{label}.sha")
        if sha in result:
            raise PilotDataError(f"duplicate commit {sha}")
        parse_time(item["committed_at"], f"{label}.committed_at")
        parents = expect_list(item["parents"], f"{label}.parents")
        for parent in parents:
            expect_sha(parent, f"{label}.parents member")
        expect_unique(parents, f"{label}.parents")
        expect_string(item["message"], f"{label}.message")
        result[sha] = item
    return result


def validate_events(
    fixture: dict[str, Any],
    pull_requests: dict[int, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    shapes = {
        "artifact_checkpoint": ("id", "type", "occurred_at", "artifact_id"),
        "base_changed": (
            "id",
            "type",
            "occurred_at",
            "pr_number",
            "old_base",
            "new_base",
        ),
        "broken_master": ("id", "type", "occurred_at", "pr_number", "sha"),
        "build_saved": ("id", "type", "occurred_at", "pr_number", "minutes"),
        "candidate_superseded": (
            "id",
            "type",
            "occurred_at",
            "pr_number",
            "old_sha",
            "new_sha",
        ),
        "closed": ("id", "type", "occurred_at", "pr_number"),
        "conflict_detected": (
            "id",
            "type",
            "occurred_at",
            "pr_number",
            "sha",
        ),
        "deletion_proof": (
            "id",
            "type",
            "occurred_at",
            "artifact_id",
            "trigger_event_id",
            "semantic_result",
            "reason",
            "restored_result",
        ),
        "dependency_changed": (
            "id",
            "type",
            "occurred_at",
            "artifact_id",
            "dependency_id",
        ),
        "escaped_defect": ("id", "type", "occurred_at", "pr_number", "sha"),
        "manual_reject": ("id", "type", "occurred_at", "pr_number", "sha"),
        "metadata_maintenance": (
            "id",
            "type",
            "occurred_at",
            "pr_number",
            "minutes",
        ),
        "pilot_coordination": (
            "id",
            "type",
            "occurred_at",
            "pr_number",
            "minutes",
        ),
        "pre_graduation": ("id", "type", "occurred_at", "artifact_id"),
        "reopened": ("id", "type", "occurred_at", "pr_number"),
        "review_saved": ("id", "type", "occurred_at", "pr_number", "minutes"),
        "security_finding": ("id", "type", "occurred_at", "pr_number", "sha"),
        "threshold_override_introduced": (
            "id",
            "type",
            "occurred_at",
            "pr_number",
            "sha",
            "override_index",
            "decision_digest",
        ),
    }
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(fixture["events"]):
        label = f"events[{index}]"
        event = expect_object(raw, label)
        event_type = expect_enum(event.get("type"), EVENT_TYPES, f"{label}.type")
        expect_keys(event, label, shapes[event_type])
        event_id = expect_string(event["id"], f"{label}.id")
        if event_id in result:
            raise PilotDataError(f"duplicate event {event_id!r}")
        parse_time(event["occurred_at"], f"{label}.occurred_at")
        if "pr_number" in event:
            pr_number = expect_int(event["pr_number"], f"{label}.pr_number", 1)
            if pr_number not in pull_requests:
                raise PilotDataError(f"{label} references unknown PR {pr_number}")
        if event_type == "base_changed":
            expect_string(event["old_base"], f"{label}.old_base")
            expect_string(event["new_base"], f"{label}.new_base")
            if event["old_base"] == event["new_base"]:
                raise PilotDataError(f"{label} does not change the base")
        if event_type == "candidate_superseded":
            old_sha = expect_sha(event["old_sha"], f"{label}.old_sha")
            new_sha = expect_sha(event["new_sha"], f"{label}.new_sha")
            if old_sha == new_sha:
                raise PilotDataError(f"{label} cannot supersede a SHA with itself")
        if "sha" in event:
            expect_sha(event["sha"], f"{label}.sha")
        if "minutes" in event:
            expect_int(event["minutes"], f"{label}.minutes", 0)
        if "artifact_id" in event:
            expect_string(event["artifact_id"], f"{label}.artifact_id")
        if "dependency_id" in event:
            expect_string(event["dependency_id"], f"{label}.dependency_id")
        if event_type == "threshold_override_introduced":
            expect_int(event["override_index"], f"{label}.override_index", 0)
            digest = event["decision_digest"]
            if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
                raise PilotDataError(
                    f"{label}.decision_digest must be a lowercase SHA-256"
                )
        if event_type == "deletion_proof":
            expect_string(event["trigger_event_id"], f"{label}.trigger_event_id")
            expect_enum(
                event["semantic_result"], {"fail", "pass"}, f"{label}.semantic_result"
            )
            expect_string(event["reason"], f"{label}.reason")
            expect_enum(
                event["restored_result"], {"fail", "pass"}, f"{label}.restored_result"
            )
        result[event_id] = event
    return result


def validate_edges(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    identities: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(fixture["dependency_edges"]):
        label = f"dependency_edges[{index}]"
        edge = expect_object(raw, label)
        expect_keys(edge, label, ("id", "type", "source", "target"))
        edge_id = expect_string(edge["id"], f"{label}.id")
        if edge_id in result:
            raise PilotDataError(f"duplicate dependency edge {edge_id!r}")
        expect_enum(edge["type"], EDGE_TYPES, f"{label}.type")
        expect_string(edge["source"], f"{label}.source")
        expect_string(edge["target"], f"{label}.target")
        if edge["source"] == edge["target"]:
            raise PilotDataError(f"{label} cannot be self-referential")
        identity = (edge["type"], edge["source"], edge["target"])
        if identity in identities:
            raise PilotDataError(f"{label} duplicates an existing dependency edge")
        identities.add(identity)
        result[edge_id] = edge
    return result


def validate_artifacts(fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    paths: list[str] = []
    for index, raw in enumerate(fixture["artifacts"]):
        label = f"artifacts[{index}]"
        artifact = expect_object(raw, label)
        expect_keys(artifact, label, ("id", "path", "dependency_ids"))
        artifact_id = expect_string(artifact["id"], f"{label}.id")
        if artifact_id in result:
            raise PilotDataError(f"duplicate artifact {artifact_id!r}")
        path = expect_string(artifact["path"], f"{label}.path")
        if path.startswith("/") or ".." in Path(path).parts:
            raise PilotDataError(f"{label}.path must be repository-relative")
        dependencies = expect_list(
            artifact["dependency_ids"], f"{label}.dependency_ids"
        )
        for dependency in dependencies:
            expect_string(dependency, f"{label}.dependency_ids member")
        expect_unique(dependencies, f"{label}.dependency_ids")
        paths.append(path)
        result[artifact_id] = artifact
    expect_unique(paths, "artifact paths")
    return result


def cross_validate_fixture(
    fixture: dict[str, Any],
    pull_requests: dict[int, dict[str, Any]],
    issues: dict[int, dict[str, Any]],
    reviews: dict[int, dict[str, Any]],
    findings: dict[int, dict[str, Any]],
    commits: dict[str, dict[str, Any]],
    events: dict[str, dict[str, Any]],
    artifacts: dict[str, dict[str, Any]],
    edges: dict[str, dict[str, Any]],
) -> None:
    lifecycle_as_of = parse_time(
        fixture["lifecycle_as_of"], "fixture.lifecycle_as_of"
    )
    review_threads: dict[int, set[str]] = defaultdict(set)
    for finding in findings.values():
        review_id = finding["review_id"]
        if review_id not in reviews:
            raise PilotDataError(
                f"review finding {finding['id']} references unknown review {review_id}"
            )
        if finding["thread_id"] in review_threads[review_id]:
            raise PilotDataError(
                f"review {review_id} repeats thread {finding['thread_id']!r}"
            )
        review_threads[review_id].add(finding["thread_id"])
        resolved_at = parse_time(
            finding["resolved_at"],
            f"review finding {finding['id']}.resolved_at",
            nullable=True,
        )
        if resolved_at is not None and resolved_at > lifecycle_as_of:
            raise PilotDataError(
                f"review finding {finding['id']} resolves after lifecycle_as_of"
            )
    for review_id, review in reviews.items():
        pr_number = review["pr_number"]
        if pr_number not in pull_requests:
            raise PilotDataError(f"review {review_id} references unknown PR {pr_number}")
        if review_id not in pull_requests[pr_number]["review_ids"]:
            raise PilotDataError(
                f"review {review_id} is absent from PR {pr_number}'s identity list"
            )
        if review["commit_sha"] not in commits:
            raise PilotDataError(
                f"review {review_id} references missing commit {review['commit_sha']}"
            )
        if set(review["thread_ids"]) != review_threads.get(review_id, set()):
            raise PilotDataError(f"review {review_id} thread identity list is incomplete")
    for pr_number, pr in pull_requests.items():
        for issue_number in pr["issue_numbers"]:
            if issue_number not in issues:
                raise PilotDataError(
                    f"PR {pr_number} references missing issue {issue_number}"
                )
        for review_id in pr["review_ids"]:
            review = reviews.get(review_id)
            if review is None or review["pr_number"] != pr_number:
                raise PilotDataError(
                    f"PR {pr_number} references missing or mismatched review {review_id}"
                )
        for sha in pr["commit_shas"]:
            if sha not in commits:
                raise PilotDataError(f"PR {pr_number} references missing commit {sha}")
        for label, sha in (("head", pr["head_sha"]), ("merge", pr["merge_sha"])):
            if sha is not None and sha not in commits:
                raise PilotDataError(f"PR {pr_number} {label} SHA has no commit record")
        if pr["head_sha"] not in pr["commit_shas"]:
            raise PilotDataError(
                f"PR {pr_number} head SHA is absent from its commit identities"
            )
        if pr["merge_sha"] is not None and not is_ancestor(
            pr["head_sha"], pr["merge_sha"], commits
        ):
            raise PilotDataError(
                f"PR {pr_number} head is not an ancestor of its merge commit"
            )
        for sha in pr["commit_shas"]:
            if not is_ancestor(sha, pr["head_sha"], commits):
                raise PilotDataError(
                    f"PR {pr_number} commit {sha} is not an ancestor of its head"
                )
    for run in fixture["workflow_runs"]:
        if run["head_sha"] not in commits:
            raise PilotDataError(
                f"workflow run {run['id']} references missing commit {run['head_sha']}"
            )
    for event_id, event in events.items():
        occurred_at = parse_time(
            event["occurred_at"], f"event {event_id}.occurred_at"
        )
        if occurred_at > lifecycle_as_of:
            raise PilotDataError(f"event {event_id!r} follows lifecycle_as_of")
        if "artifact_id" in event and event["artifact_id"] not in artifacts:
            raise PilotDataError(
                f"event {event_id!r} references unknown artifact {event['artifact_id']!r}"
            )
        if event["type"] == "deletion_proof":
            trigger = events.get(event["trigger_event_id"])
            if trigger is None or trigger["type"] not in DELETION_TRIGGER_TYPES:
                raise PilotDataError(
                    f"deletion proof {event_id!r} has no valid trigger event"
                )
            if trigger.get("artifact_id") != event["artifact_id"]:
                raise PilotDataError(
                    f"deletion proof {event_id!r} targets the wrong artifact"
                )
            proof_at = parse_time(event["occurred_at"], f"event {event_id}.occurred_at")
            trigger_at = parse_time(
                trigger["occurred_at"], f"event {trigger['id']}.occurred_at"
            )
            if proof_at <= trigger_at:
                raise PilotDataError(
                    f"deletion proof {event_id!r} must strictly follow its trigger"
                )
        sha_fields = ("sha", "old_sha", "new_sha")
        for field in sha_fields:
            if field not in event:
                continue
            sha = event[field]
            if sha not in commits:
                raise PilotDataError(
                    f"event {event_id!r} {field} has no authoritative commit"
                )
            if "pr_number" in event:
                pr = pull_requests[event["pr_number"]]
                pr_history = set(pr["commit_shas"])
                if pr["merge_sha"] is not None:
                    pr_history.add(pr["merge_sha"])
                if sha not in pr_history:
                    raise PilotDataError(
                        f"event {event_id!r} {field} is outside PR "
                        f"{pr['number']} candidate/merge history"
                    )
        if event["type"] == "threshold_override_introduced":
            committed_at = parse_time(
                commits[event["sha"]]["committed_at"],
                f"commit {event['sha']}.committed_at",
            )
            if occurred_at != committed_at:
                raise PilotDataError(
                    f"threshold override event {event_id!r} occurrence does not "
                    "match its authoritative introduction commit"
                )

    edge_claims: dict[str, list[str]] = defaultdict(list)
    for artifact_id, artifact in artifacts.items():
        for edge_id in artifact["dependency_ids"]:
            if edge_id not in edges:
                raise PilotDataError(
                    f"artifact {artifact_id!r} references missing edge {edge_id!r}"
                )
            edge_claims[edge_id].append(artifact_id)

    review_dependency_targets = set()
    for edge_id, edge in edges.items():
        claims = edge_claims.get(edge_id, [])
        if len(claims) > 1:
            raise PilotDataError(
                f"dependency edge {edge_id!r} has ambiguous artifact ownership"
            )
        if edge["type"] in {"consumes", "checks"}:
            target = edge["target"]
            if target not in artifacts:
                raise PilotDataError(
                    f"{edge['type']} edge {edge_id!r} targets unknown artifact"
                )
            if claims != [target]:
                raise PilotDataError(
                    f"{edge['type']} edge {edge_id!r} is not claimed exactly by "
                    f"target artifact {target!r}"
                )
        elif claims:
            raise PilotDataError(
                f"artifact {claims[0]!r} claims non-consumer edge {edge_id!r}"
            )

        if edge["type"] == "derives":
            if edge["source"] not in artifacts or edge["target"] not in artifacts:
                raise PilotDataError(
                    f"derives edge {edge_id!r} must connect authoritative artifacts"
                )
        elif edge["type"] == "review_depends_on":
            match = re.fullmatch(r"review:([1-9][0-9]*)", edge["source"])
            review_id = int(match.group(1)) if match is not None else None
            if review_id not in reviews:
                raise PilotDataError(
                    f"review dependency edge {edge_id!r} references missing review"
                )
            review_dependency_targets.add(edge["target"])

    for event_id, event in events.items():
        if (
            event["type"] == "dependency_changed"
            and event["dependency_id"] not in review_dependency_targets
        ):
            raise PilotDataError(
                f"dependency change {event_id!r} has no review dependency edge"
            )


def validate_fixture(fixture: Any) -> dict[str, Any]:
    fixture = expect_object(fixture, "fixture")
    _validate_fixture_root(fixture)
    pull_requests = validate_pull_requests(fixture)
    issues = validate_issues(fixture)
    reviews = validate_reviews(fixture)
    findings = validate_findings(fixture)
    runs = validate_runs(fixture)
    commits = validate_commits(fixture)
    if fixture["base_sha"] not in commits:
        raise PilotDataError("fixture.base_sha has no commit record")
    events = validate_events(fixture, pull_requests)
    artifacts = validate_artifacts(fixture)
    edges = validate_edges(fixture)
    cross_validate_fixture(
        fixture,
        pull_requests,
        issues,
        reviews,
        findings,
        commits,
        events,
        artifacts,
        edges,
    )
    return {
        "fixture": fixture,
        "pull_requests": pull_requests,
        "issues": issues,
        "reviews": reviews,
        "findings": findings,
        "runs": runs,
        "commits": commits,
        "events": events,
        "artifacts": artifacts,
        "edges": edges,
    }


def validate_decisions(
    raw_decisions: Any,
    data: dict[str, Any],
) -> dict[str, Any]:
    decisions = expect_object(raw_decisions, "decisions")
    expect_keys(decisions, "decisions", ("schema_version", "pull_requests", "artifacts"))
    if decisions["schema_version"] != SCHEMA_VERSION:
        raise PilotDataError(f"decisions schema_version must be {SCHEMA_VERSION}")
    pr_records = expect_list(decisions["pull_requests"], "decisions.pull_requests")
    artifact_records = expect_list(decisions["artifacts"], "decisions.artifacts")

    pr_decisions: dict[int, dict[str, Any]] = {}
    for index, raw in enumerate(pr_records):
        label = f"decisions.pull_requests[{index}]"
        record = expect_object(raw, label)
        expect_keys(
            record,
            label,
            (
                "pull_request",
                "risk_boundaries",
                "threshold",
                "gate_mode",
                "stack",
                "pilot",
            ),
        )
        number = expect_int(record["pull_request"], f"{label}.pull_request", 1)
        if number in pr_decisions:
            raise PilotDataError(f"duplicate PR decision {number}")
        if number not in data["pull_requests"]:
            raise PilotDataError(f"PR decision {number} has no authoritative PR")
        risks = expect_list(record["risk_boundaries"], f"{label}.risk_boundaries")
        if not risks:
            raise PilotDataError(f"{label}.risk_boundaries must not be empty")
        for risk in risks:
            expect_enum(risk, RISK_BOUNDARIES, f"{label}.risk_boundaries member")
        expect_unique(risks, f"{label}.risk_boundaries")
        if "none" in risks and len(risks) != 1:
            raise PilotDataError(f"{label}.risk_boundaries none must stand alone")

        threshold = expect_object(record["threshold"], f"{label}.threshold")
        expect_keys(
            threshold,
            f"{label}.threshold",
            ("triggers", "override_history"),
        )
        triggers = expect_list(
            threshold["triggers"], f"{label}.threshold.triggers"
        )
        if not triggers:
            raise PilotDataError(f"{label}.threshold.triggers must not be empty")
        for trigger in triggers:
            expect_enum(
                trigger,
                THRESHOLD_TRIGGERS,
                f"{label}.threshold.triggers member",
            )
        expect_unique(triggers, f"{label}.threshold.triggers")
        if "none" in triggers and len(triggers) != 1:
            raise PilotDataError(f"{label}.threshold.triggers none must stand alone")

        reviews = [
            review
            for review in data["reviews"].values()
            if review["pr_number"] == number and review["author"] == REVIEW_BOT
        ]
        first_review = min(
            (
                parse_time(review["submitted_at"], f"review {review['id']}.submitted_at")
                for review in reviews
            ),
            default=None,
        )
        override_history = expect_list(
            threshold["override_history"],
            f"{label}.threshold.override_history",
        )
        introduction_events = {
            event["override_index"]: event
            for event in data["events"].values()
            if event["type"] == "threshold_override_introduced"
            and event["pr_number"] == number
        }
        if len(introduction_events) != len(
            [
                event
                for event in data["events"].values()
                if event["type"] == "threshold_override_introduced"
                and event["pr_number"] == number
            ]
        ):
            raise PilotDataError(
                f"PR {number} threshold override provenance repeats an index"
            )
        if set(introduction_events) != set(range(len(override_history))):
            raise PilotDataError(
                f"PR {number} threshold overrides lack exact authoritative "
                "introduction coverage"
            )
        for override_index, raw_override in enumerate(override_history):
            override_label = (
                f"{label}.threshold.override_history[{override_index}]"
            )
            override = expect_object(raw_override, override_label)
            expect_keys(
                override,
                override_label,
                ("enabled", "reason"),
            )
            expect_bool(override["enabled"], f"{override_label}.enabled")
            expect_string(override["reason"], f"{override_label}.reason")
            introduction = introduction_events[override_index]
            if introduction["decision_digest"] != threshold_override_digest(
                number, override_index, override
            ):
                raise PilotDataError(
                    f"PR {number} threshold override {override_index} changed after "
                    "its authoritative introduction"
                )
            introduced_at = parse_time(
                data["commits"][introduction["sha"]]["committed_at"],
                f"commit {introduction['sha']}.committed_at",
            )
            if first_review is not None and introduced_at >= first_review:
                raise PilotDataError(
                    f"PR {number} threshold override was introduced after first review"
                )

        expect_enum(record["gate_mode"], GATE_MODES, f"{label}.gate_mode")
        stack = expect_object(record["stack"], f"{label}.stack")
        expect_keys(stack, f"{label}.stack", ("depth", "parent_pr", "exception_reason"))
        depth = expect_int(stack["depth"], f"{label}.stack.depth", 0)
        if depth > 3:
            raise PilotDataError(f"{label}.stack.depth exceeds the supported maximum")
        parent_pr = stack["parent_pr"]
        exception_reason = stack["exception_reason"]
        if depth == 0:
            if parent_pr is not None or exception_reason is not None:
                raise PilotDataError(
                    f"{label}.stack root cannot name a parent or exception"
                )
            if data["pull_requests"][number]["base_ref"] != data["fixture"]["default_branch"]:
                raise PilotDataError(
                    f"{label}.stack root contradicts the authoritative PR base"
                )
        else:
            parent = expect_int(parent_pr, f"{label}.stack.parent_pr", 1)
            if parent == number:
                raise PilotDataError(f"{label}.stack cannot name itself as parent")
            if parent not in data["pull_requests"]:
                raise PilotDataError(
                    f"{label}.stack.parent_pr has no authoritative PR"
                )
            expected_base = data["pull_requests"][parent]["head_branch"]
            if data["pull_requests"][number]["base_ref"] != expected_base:
                raise PilotDataError(
                    f"{label}.stack parent contradicts the authoritative PR base"
                )
            if depth == 3:
                expect_string(
                    exception_reason, f"{label}.stack.exception_reason"
                )
            elif exception_reason is not None:
                raise PilotDataError(
                    f"{label}.stack.exception_reason is only valid at depth three"
                )

        pilot = expect_object(record["pilot"], f"{label}.pilot")
        expect_keys(pilot, f"{label}.pilot", ("included", "disposition"))
        expect_bool(pilot["included"], f"{label}.pilot.included")
        disposition = expect_enum(
            pilot["disposition"],
            PILOT_DISPOSITIONS,
            f"{label}.pilot.disposition",
        )
        if pilot["included"] and disposition in {"baseline-only", "excluded"}:
            raise PilotDataError(f"{label}.pilot inclusion contradicts disposition")
        if not pilot["included"] and disposition in {"evaluate", "graduated"}:
            raise PilotDataError(f"{label}.pilot exclusion contradicts disposition")
        pr_decisions[number] = record

    introduction_prs = {
        event["pr_number"]
        for event in data["events"].values()
        if event["type"] == "threshold_override_introduced"
    }
    orphan_introductions = sorted(introduction_prs - set(pr_decisions))
    if orphan_introductions:
        raise PilotDataError(
            "threshold override provenance has no decision record for PRs "
            + ", ".join(str(number) for number in orphan_introductions)
        )

    spotlight = data["fixture"]["spotlight_pr"]
    if spotlight not in pr_decisions:
        raise PilotDataError(f"missing required decision for spotlight PR {spotlight}")

    artifact_decisions: dict[str, dict[str, Any]] = {}
    unique_decisions: list[str] = []
    for index, raw in enumerate(artifact_records):
        label = f"decisions.artifacts[{index}]"
        record = expect_object(raw, label)
        expect_keys(
            record,
            label,
            (
                "artifact_id",
                "owner",
                "executable_consumer",
                "unique_decision",
                "consistency_check",
                "max_maintenance_minutes",
                "estimated_maintenance_minutes",
                "deletion_criterion",
                "expires_at",
                "history",
            ),
        )
        artifact_id = expect_string(record["artifact_id"], f"{label}.artifact_id")
        if artifact_id in artifact_decisions:
            raise PilotDataError(f"duplicate artifact decision {artifact_id!r}")
        if artifact_id not in data["artifacts"]:
            raise PilotDataError(
                f"artifact decision {artifact_id!r} has no authoritative artifact"
            )
        expect_string(record["owner"], f"{label}.owner")
        expect_string(
            record["executable_consumer"], f"{label}.executable_consumer"
        )
        unique_decision = expect_string(
            record["unique_decision"], f"{label}.unique_decision"
        )
        unique_decisions.append(unique_decision)
        expect_string(record["consistency_check"], f"{label}.consistency_check")
        maximum = expect_int(
            record["max_maintenance_minutes"],
            f"{label}.max_maintenance_minutes",
            1,
        )
        estimated = expect_int(
            record["estimated_maintenance_minutes"],
            f"{label}.estimated_maintenance_minutes",
            0,
        )
        if estimated > maximum:
            raise PilotDataError(f"{label} exceeds its bounded maintenance cost")
        expect_string(record["deletion_criterion"], f"{label}.deletion_criterion")
        expires_at = parse_time(record["expires_at"], f"{label}.expires_at", nullable=True)
        history = expect_list(record["history"], f"{label}.history")
        if not history:
            raise PilotDataError(f"{label}.history must not be empty")
        previous_at: datetime | None = None
        for history_index, raw_history in enumerate(history):
            history_label = f"{label}.history[{history_index}]"
            entry = expect_object(raw_history, history_label)
            expect_keys(
                entry, history_label, ("recorded_at", "disposition", "reason")
            )
            recorded_at = parse_time(
                entry["recorded_at"], f"{history_label}.recorded_at"
            )
            expect_enum(
                entry["disposition"],
                ARTIFACT_DISPOSITIONS,
                f"{history_label}.disposition",
            )
            expect_string(entry["reason"], f"{history_label}.reason")
            if previous_at is not None and recorded_at <= previous_at:
                raise PilotDataError(f"{label}.history is not strictly chronological")
            previous_at = recorded_at
        current = history[-1]["disposition"]
        lifecycle_as_of = parse_time(
            data["fixture"]["lifecycle_as_of"], "fixture.lifecycle_as_of"
        )
        if previous_at is not None and previous_at > lifecycle_as_of:
            raise PilotDataError(f"{label}.history follows lifecycle_as_of")
        if (
            expires_at is not None
            and expires_at <= lifecycle_as_of
            and current != "Delete"
        ):
            raise PilotDataError(f"artifact {artifact_id!r} is expired but not deleted")
        artifact_decisions[artifact_id] = record
    expect_unique(unique_decisions, "artifact unique decisions")

    if set(artifact_decisions) != set(data["artifacts"]):
        missing = sorted(set(data["artifacts"]) - set(artifact_decisions))
        extra = sorted(set(artifact_decisions) - set(data["artifacts"]))
        raise PilotDataError(
            "artifact decisions do not exactly cover authoritative artifacts "
            f"(missing={missing}, extra={extra})"
        )

    validate_artifact_lifecycle(data, artifact_decisions)
    return {
        "raw": decisions,
        "pull_requests": pr_decisions,
        "artifacts": artifact_decisions,
    }


def validate_artifact_lifecycle(
    data: dict[str, Any],
    artifact_decisions: dict[str, dict[str, Any]],
) -> None:
    edges = data["edges"]
    events = data["events"]
    for artifact_id, artifact in data["artifacts"].items():
        decision = artifact_decisions[artifact_id]
        dependency_edges = [edges[edge_id] for edge_id in artifact["dependency_ids"]]
        consumer_edges = [
            edge
            for edge in dependency_edges
            if edge["type"] == "consumes" and edge["target"] == artifact_id
        ]
        expected_consumer = decision["executable_consumer"]
        if len(consumer_edges) != 1 or consumer_edges[0]["source"] != expected_consumer:
            raise PilotDataError(
                f"artifact {artifact_id!r} must have exactly one executable consumer edge"
            )
        check_edges = [
            edge
            for edge in dependency_edges
            if edge["type"] == "checks" and edge["target"] == artifact_id
        ]
        if (
            len(check_edges) != 1
            or check_edges[0]["source"] != decision["consistency_check"]
        ):
            raise PilotDataError(
                f"artifact {artifact_id!r} must have exactly one consistency check edge"
            )
        triggers = [
            event
            for event in events.values()
            if event["type"] in DELETION_TRIGGER_TYPES
            and event.get("artifact_id") == artifact_id
        ]
        proofs = [
            event
            for event in events.values()
            if event["type"] == "deletion_proof"
            and event["artifact_id"] == artifact_id
        ]
        proofs_by_trigger: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for proof in proofs:
            proofs_by_trigger[proof["trigger_event_id"]].append(proof)
        for trigger in triggers:
            matching = proofs_by_trigger.get(trigger["id"], [])
            if len(matching) != 1:
                raise PilotDataError(
                    f"artifact {artifact_id!r} trigger {trigger['id']!r} "
                    "must have exactly one deletion proof"
                )
        if set(proofs_by_trigger) != {trigger["id"] for trigger in triggers}:
            raise PilotDataError(
                f"artifact {artifact_id!r} has an orphan deletion proof"
            )
        if not triggers:
            raise PilotDataError(
                f"artifact {artifact_id!r} has no lifecycle deletion-proof trigger"
            )
        trigger_types = {trigger["type"] for trigger in triggers}
        for required_type in ("artifact_checkpoint", "pre_graduation"):
            if required_type not in trigger_types:
                raise PilotDataError(
                    f"artifact {artifact_id!r} has no {required_type} deletion proof"
                )
        latest_proof = max(
            proofs,
            key=lambda proof: parse_time(
                proof["occurred_at"], f"event {proof['id']}.occurred_at"
            ),
        )
        latest_proof_at = parse_time(
            latest_proof["occurred_at"],
            f"event {latest_proof['id']}.occurred_at",
        )
        disposition_at = parse_time(
            decision["history"][-1]["recorded_at"],
            f"artifact decision {artifact_id}.current recorded_at",
        )
        if disposition_at <= latest_proof_at:
            raise PilotDataError(
                f"artifact {artifact_id!r} current disposition must strictly "
                "follow every deletion proof"
            )
        current_disposition = decision["history"][-1]["disposition"]
        if latest_proof["semantic_result"] == "pass":
            if latest_proof["restored_result"] != "pass":
                raise PilotDataError(
                    f"artifact {artifact_id!r} deletion proof did not restore"
                )
            if current_disposition != "Delete":
                raise PilotDataError(
                    f"artifact {artifact_id!r} is deletion-ready but not Delete"
                )
        else:
            if latest_proof["restored_result"] != "pass":
                raise PilotDataError(
                    f"necessary artifact {artifact_id!r} was not restored"
                )
            if current_disposition == "Delete":
                raise PilotDataError(
                    f"necessary artifact {artifact_id!r} cannot be Delete"
                )


def is_generated_path(path: str) -> bool:
    return path.startswith(GENERATED_PREFIXES) or path.endswith(GENERATED_SUFFIXES)


def workflow_sample(data: dict[str, Any]) -> list[dict[str, Any]]:
    captured = parse_time(data["fixture"]["captured_at"], "fixture.captured_at")
    eligible = [
        run
        for run in data["runs"].values()
        if parse_time(run["created_at"], f"run {run['id']}.created_at") <= captured
    ]
    eligible.sort(
        key=lambda run: (
            parse_time(run["created_at"], f"run {run['id']}.created_at"),
            run["id"],
        ),
        reverse=True,
    )
    size = data["fixture"]["workflow_sample_size"]
    if len(eligible) < size:
        raise PilotDataError(
            f"authoritative workflow fixture has {len(eligible)} runs, needs {size}"
        )
    return eligible[:size]


def run_elapsed_seconds(
    run: dict[str, Any],
    captured: datetime,
) -> Decimal:
    if run["status"] == "queued":
        return Decimal(0)
    started = parse_time(run["started_at"], f"run {run['id']}.started_at", nullable=True)
    if started is None:
        return Decimal(0)
    completed = parse_time(
        run["completed_at"], f"run {run['id']}.completed_at", nullable=True
    )
    return duration_seconds(started, completed or captured, f"run {run['id']}")


def report_delivery(data: dict[str, Any]) -> dict[str, Any]:
    fixture = data["fixture"]
    start = parse_time(fixture["window"]["start"], "fixture.window.start")
    end = parse_time(fixture["window"]["end"], "fixture.window.end")
    merged = []
    for pr in data["pull_requests"].values():
        merged_at = parse_time(pr["merged_at"], f"PR {pr['number']}.merged_at", nullable=True)
        if merged_at is not None and start <= merged_at <= end:
            merged.append(pr)
    if not merged:
        raise PilotDataError("frozen window contains no merged pull requests")

    pr_durations = []
    issue_durations = []
    for pr in merged:
        created = parse_time(pr["created_at"], f"PR {pr['number']}.created_at")
        merged_at = parse_time(pr["merged_at"], f"PR {pr['number']}.merged_at")
        pr_durations.append(
            rounded_tenth_hours(
                duration_seconds(created, merged_at, f"PR {pr['number']} delivery")
            )
        )
        if pr["issue_numbers"]:
            issue_created = min(
                parse_time(
                    data["issues"][number]["created_at"],
                    f"issue {number}.created_at",
                )
                for number in pr["issue_numbers"]
            )
            issue_durations.append(
                rounded_tenth_hours(
                    duration_seconds(
                        issue_created,
                        merged_at,
                        f"PR {pr['number']} issue-to-merge",
                    )
                )
            )

    first_push_durations = []
    findings_by_review: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for finding in data["findings"].values():
        findings_by_review[finding["review_id"]].append(finding)
    runs_by_branch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in data["runs"].values():
        runs_by_branch[run["head_branch"]].append(run)
    first_push_subjects = [data["pull_requests"][fixture["spotlight_pr"]]]
    for pr in first_push_subjects:
        branch_runs = runs_by_branch.get(pr["head_branch"], [])
        reviews = sorted(
            (
                review
                for review in data["reviews"].values()
                if review["pr_number"] == pr["number"]
                and review["author"] == REVIEW_BOT
            ),
            key=lambda review: parse_time(
                review["submitted_at"], f"review {review['id']}.submitted_at"
            ),
        )
        if not branch_runs or not reviews:
            raise PilotDataError(
                f"PR {pr['number']} lacks first-push or review evidence"
            )
        first_push = min(
            parse_time(pr["created_at"], f"PR {pr['number']}.created_at"),
            *(
                parse_time(run["created_at"], f"run {run['id']}.created_at")
                for run in branch_runs
            ),
        )
        prior_findings: list[dict[str, Any]] = []
        clean_at = None
        for review in reviews:
            review_findings = findings_by_review.get(review["id"], [])
            review_at = parse_time(
                review["submitted_at"], f"review {review['id']}.submitted_at"
            )
            if (
                not review_findings
                and all(
                    (
                        resolved_at := parse_time(
                            finding["resolved_at"],
                            f"finding {finding['id']}.resolved_at",
                            nullable=True,
                        )
                    )
                    is not None
                    and resolved_at < review_at
                    for finding in prior_findings
                )
                and is_ancestor(
                    review["commit_sha"],
                    pr["head_sha"],
                    data["commits"],
                )
            ):
                clean_at = review_at
                break
            prior_findings.extend(review_findings)
        if clean_at is not None:
            first_push_durations.append(
                rounded_tenth_hours(
                    duration_seconds(
                        first_push,
                        clean_at,
                        f"PR {pr['number']} first-push-to-clean-review",
                    )
                )
            )
        else:
            raise PilotDataError(
                f"PR {pr['number']} has no authoritative clean-review boundary"
            )

    return {
        "merged_pull_requests": len(merged),
        "pr_open_to_merge_median_hours": median_tenth(pr_durations),
        "issue_to_merge": {
            "eligible_pull_requests": len(issue_durations),
            "excluded_without_linked_issue": len(merged) - len(issue_durations),
            "median_hours": median_tenth(issue_durations),
        },
        "first_push_to_clean_review": {
            "eligible_pull_requests": len(first_push_durations),
            "excluded_without_complete_evidence": len(first_push_subjects)
            - len(first_push_durations),
            "median_hours": median_tenth(first_push_durations),
        },
    }


def report_reviews(data: dict[str, Any]) -> dict[str, Any]:
    spotlight = data["fixture"]["spotlight_pr"]
    pr = data["pull_requests"][spotlight]
    reviews = [
        review
        for review in data["reviews"].values()
        if review["pr_number"] == spotlight and review["author"] == REVIEW_BOT
    ]
    review_ids = {review["id"] for review in reviews}
    findings = [
        finding
        for finding in data["findings"].values()
        if finding["review_id"] in review_ids
        and finding["resolved_at"] is not None
    ]
    changed_lines = pr["additions"] + pr["deletions"]
    superseded_reviews = sum(
        not is_ancestor(review["commit_sha"], pr["head_sha"], data["commits"])
        for review in reviews
    )
    if changed_lines == 0:
        findings_per_kloc = None
    else:
        findings_per_kloc = str(
            (
                Decimal(len(findings)) * Decimal(1000) / Decimal(changed_lines)
            ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        )
    findings_per_review = (
        None
        if not reviews
        else str(
            (Decimal(len(findings)) / Decimal(len(reviews))).quantize(
                Decimal("0.001"), rounding=ROUND_HALF_UP
            )
        )
    )
    return {
        "spotlight_pr": spotlight,
        "rounds": len(reviews),
        "superseded_rounds": superseded_reviews,
        "valid_findings": len(findings),
        "changed_lines": changed_lines,
        "valid_findings_per_kloc": findings_per_kloc,
        "valid_findings_per_review": findings_per_review,
    }


def report_builds(data: dict[str, Any]) -> dict[str, Any]:
    fixture = data["fixture"]
    captured = parse_time(fixture["captured_at"], "fixture.captured_at")
    sample = workflow_sample(data)
    builds = [run for run in sample if run["workflow"] == fixture["build_workflow"]]
    conclusions: dict[str, int] = defaultdict(int)
    for run in builds:
        conclusions[run["conclusion"] or "active"] += 1
    grouped: dict[str, int] = defaultdict(int)
    for run in builds:
        grouped[run["head_sha"]] += 1
    duplicates = sum(count - 1 for count in grouped.values() if count > 1)
    total_seconds = sum(
        (run_elapsed_seconds(run, captured) for run in builds),
        Decimal(0),
    )

    spotlight = fixture["spotlight_pr"]
    head_branch = data["pull_requests"][spotlight]["head_branch"]
    spotlight_builds = [
        run
        for run in builds
        if run["head_branch"] == head_branch
    ]
    spotlight_seconds = sum(
        (run_elapsed_seconds(run, captured) for run in spotlight_builds),
        Decimal(0),
    )
    spotlight_conclusions: dict[str, int] = defaultdict(int)
    for run in spotlight_builds:
        spotlight_conclusions[run["conclusion"] or "active"] += 1
    return {
        "sample_size": len(sample),
        "runs": len(builds),
        "success": conclusions["success"],
        "failure": conclusions["failure"],
        "cancelled": conclusions["cancelled"],
        "action_required": conclusions["action_required"],
        "neutral": conclusions["neutral"],
        "skipped": conclusions["skipped"],
        "active": conclusions["active"],
        "minutes": int(total_seconds // Decimal(60)),
        "duplicate_unchanged_sha": duplicates,
        "spotlight": {
            "pr": spotlight,
            "runs": len(spotlight_builds),
            "success": spotlight_conclusions["success"],
            "failure": spotlight_conclusions["failure"],
            "cancelled": spotlight_conclusions["cancelled"],
            "action_required": spotlight_conclusions["action_required"],
            "neutral": spotlight_conclusions["neutral"],
            "skipped": spotlight_conclusions["skipped"],
            "active": spotlight_conclusions["active"],
            "minutes": int(spotlight_seconds // Decimal(60)),
        },
    }


def report_events(data: dict[str, Any]) -> dict[str, Any]:
    spotlight = data["fixture"]["spotlight_pr"]
    start = parse_time(
        data["fixture"]["window"]["start"], "fixture.window.start"
    )
    end = parse_time(data["fixture"]["window"]["end"], "fixture.window.end")
    in_window_events = [
        event
        for event in data["events"].values()
        if start
        <= parse_time(event["occurred_at"], f"event {event['id']}.occurred_at")
        <= end
    ]
    spotlight_counts: dict[str, int] = defaultdict(int)
    safety_counts: dict[str, int] = defaultdict(int)
    for event in in_window_events:
        if event.get("pr_number") == spotlight:
            spotlight_counts[event["type"]] += 1
        if event["type"] in SAFETY_EVENT_TYPES:
            safety_counts[event["type"]] += 1
    reverts = []
    for commit in data["commits"].values():
        match = REVERT_RE.search(commit["message"])
        if match is not None:
            target = match.group(1)
            if target not in data["commits"]:
                raise PilotDataError(
                    f"revert commit {commit['sha']} targets unavailable commit {target}"
                )
            reverts.append({"commit": commit["sha"], "reverts": target})
    branch = data["pull_requests"][spotlight]["head_branch"]
    candidate_shas = {
        run["head_sha"]
        for run in data["runs"].values()
        if run["head_branch"] == branch
    }
    derived_superseded = max(0, len(candidate_shas) - 1)
    return {
        "spotlight_pr": spotlight,
        "base_changes": spotlight_counts["base_changed"],
        "close_reopen_cycles": min(
            spotlight_counts["closed"], spotlight_counts["reopened"]
        ),
        "conflicts": safety_counts["conflict_detected"],
        "superseded_candidates": derived_superseded,
        "escaped_defects": safety_counts["escaped_defect"],
        "broken_master": safety_counts["broken_master"],
        "security_findings": safety_counts["security_finding"],
        "manual_rejects": safety_counts["manual_reject"],
        "reverts": reverts,
    }


def report_efficiency(data: dict[str, Any]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    start = parse_time(
        data["fixture"]["window"]["start"], "fixture.window.start"
    )
    end = parse_time(data["fixture"]["window"]["end"], "fixture.window.end")
    for event in data["events"].values():
        occurred = parse_time(
            event["occurred_at"], f"event {event['id']}.occurred_at"
        )
        if "minutes" in event and start <= occurred <= end:
            totals[event["type"]] += event["minutes"]
    saved = totals["build_saved"] + totals["review_saved"]
    overhead = totals["pilot_coordination"] + totals["metadata_maintenance"]
    return {
        "saved_build_minutes": totals["build_saved"],
        "saved_review_minutes": totals["review_saved"],
        "pilot_coordination_minutes": totals["pilot_coordination"],
        "metadata_maintenance_minutes": totals["metadata_maintenance"],
        "net_saved_minutes": saved - overhead,
    }


def report_classifications(data: dict[str, Any]) -> list[dict[str, Any]]:
    runs_by_branch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in data["runs"].values():
        runs_by_branch[run["head_branch"]].append(run)
    reverted_shas = {
        match.group(1)
        for commit in data["commits"].values()
        if (match := REVERT_RE.search(commit["message"])) is not None
    }
    result = []
    for number, pr in sorted(data["pull_requests"].items()):
        lines = pr["additions"] + pr["deletions"]
        branch_shas = {run["head_sha"] for run in runs_by_branch[pr["head_branch"]]}
        flags = []
        if len(branch_shas) > 1:
            flags.append("superseded")
        if pr["base_ref"] != data["fixture"]["default_branch"]:
            flags.append("stacked")
        if pr["files"] and all(is_generated_path(path) for path in pr["files"]):
            flags.append("generated-only")
        if lines >= 1000 and pr["deletions"] * 100 >= lines * 80:
            flags.append("bulk-deletion")
        if pr["merge_sha"] in reverted_shas:
            flags.append("reverted")
        work_state = {
            "merged": "merged",
            "closed": "cancelled",
            "open": "still-running",
        }[pr["state"]]
        result.append(
            {
                "pr": number,
                "work_state": work_state,
                "flags": sorted(flags),
                "current_head_sha": pr["head_sha"],
            }
        )
    return result


def report_artifacts(
    data: dict[str, Any],
    decisions: dict[str, Any],
) -> dict[str, Any]:
    invalidated_reviews = set()
    dependency_changes = [
        event
        for event in data["events"].values()
        if event["type"] == "dependency_changed"
    ]
    for edge in data["edges"].values():
        if edge["type"] != "review_depends_on":
            continue
        if not edge["source"].startswith("review:"):
            raise PilotDataError(
                f"review dependency edge {edge['id']!r} has malformed source"
            )
        try:
            review_id = int(edge["source"].split(":", 1)[1])
        except ValueError as error:
            raise PilotDataError(
                f"review dependency edge {edge['id']!r} has malformed review ID"
            ) from error
        review = data["reviews"].get(review_id)
        if review is None:
            raise PilotDataError(
                f"review dependency edge {edge['id']!r} references missing review"
            )
        submitted = parse_time(
            review["submitted_at"], f"review {review_id}.submitted_at"
        )
        if any(
            event["dependency_id"] == edge["target"]
            and parse_time(
                event["occurred_at"], f"event {event['id']}.occurred_at"
            )
            > submitted
            for event in dependency_changes
        ):
            invalidated_reviews.add(review_id)

    current = []
    for artifact_id, decision in sorted(decisions["artifacts"].items()):
        history = [
            {
                "recorded_at": entry["recorded_at"],
                "disposition": entry["disposition"],
                "reason": entry["reason"],
            }
            for entry in decision["history"]
        ]
        current.append(
            {
                "artifact_id": artifact_id,
                "current_disposition": history[-1]["disposition"],
                "history": history,
            }
        )
    return {
        "current": current,
        "invalidated_review_ids": sorted(invalidated_reviews),
    }


def build_report(fixture: Any, raw_decisions: Any) -> dict[str, Any]:
    data = validate_fixture(fixture)
    decisions = validate_decisions(raw_decisions, data)
    workflow_sample(data)
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot": {
            "repository": data["fixture"]["repository"],
            "base_sha": data["fixture"]["base_sha"],
            "captured_at": data["fixture"]["captured_at"],
            "lifecycle_as_of": data["fixture"]["lifecycle_as_of"],
            "window": data["fixture"]["window"],
        },
        "identities": {
            "pull_requests": sorted(data["pull_requests"]),
            "issues": sorted(data["issues"]),
            "reviews": sorted(data["reviews"]),
            "workflow_runs": sorted(data["runs"]),
            "commits": sorted(data["commits"]),
        },
        "delivery": report_delivery(data),
        "reviews": report_reviews(data),
        "builds": report_builds(data),
        "events": report_events(data),
        "efficiency": report_efficiency(data),
        "classifications": report_classifications(data),
        "artifacts": report_artifacts(data, decisions),
    }


def check_expected(report: dict[str, Any], expected: Any) -> None:
    expected = expect_object(expected, "expected")
    expect_keys(expected, "expected", ("schema_version", "paths"))
    if expected["schema_version"] != SCHEMA_VERSION:
        raise PilotDataError(f"expected schema_version must be {SCHEMA_VERSION}")
    paths = expect_object(expected["paths"], "expected.paths")
    for path, wanted in sorted(paths.items()):
        expect_string(path, "expected.paths key")
        value: Any = report
        for component in path.split("."):
            if not isinstance(value, dict) or component not in value:
                raise PilotDataError(f"expected path {path!r} does not exist")
            value = value[component]
        if value != wanted:
            raise PilotDataError(
                f"expected path {path!r} to be {wanted!r}, got {value!r}"
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate immutable Git/GitHub/Actions fixture data and the minimal "
            "workflow-pilot decision record, then emit canonical JSON."
        )
    )
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--expected", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        fixture = load_json(args.fixture)
        decisions = load_json(args.decisions)
        report = build_report(fixture, decisions)
        if args.expected is not None:
            check_expected(report, load_json(args.expected))
    except PilotDataError as error:
        print(f"workflow-pilot: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(normalized_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
