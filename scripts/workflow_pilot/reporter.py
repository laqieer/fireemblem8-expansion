#!/usr/bin/env python3
"""Produce a deterministic workflow-pilot report from immutable fixtures."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
HANDOFF_FIXTURE_SCHEMA_VERSION = 2
GIT = "/usr/bin/git"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RFC3339_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
EXPECTED_PATH_RE = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
)
DELIVERY_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
REVERT_TRAILER_RE = re.compile(
    r"(?:\A|\n\n)This reverts commit ([0-9a-f]{40})\.\Z"
)
REVIEW_BOT = "copilot-pull-request-reviewer[bot]"
DECISION_RECORD_PATH = Path(".github/workflow-pilot-decisions.json")
REVIEW_THREAD_EVENT_SOURCE = "github-webhook-deliveries"
BASELINE_FIXTURE_PATH = Path(
    "scripts/workflow_pilot/tests/fixtures/baseline.json"
)
BASELINE_EXPECTED_PATH = Path(
    "scripts/workflow_pilot/tests/fixtures/baseline_expected.json"
)
REPORTER_PATH = Path("scripts/workflow_pilot/reporter.py")
REPORTER_PACKAGE_PATH = Path("scripts/workflow_pilot/__init__.py")
ISOLATED_LAUNCHER_PATH = Path("scripts/workflow_pilot/isolated_launcher.py")
REPORTER_TEST_PATH = Path("scripts/workflow_pilot/tests/test_reporter.py")
REPORTER_TEST_PACKAGE_PATH = Path("scripts/workflow_pilot/tests/__init__.py")
DELETION_PROOF_SUPPORT_PATHS = (
    BASELINE_EXPECTED_PATH,
    ISOLATED_LAUNCHER_PATH,
    REPORTER_PACKAGE_PATH,
    REPORTER_TEST_PATH,
    REPORTER_TEST_PACKAGE_PATH,
)
DELETION_PROOF_REASON = "removal loses the issue #176 baseline decision invariant"
DELETION_PROOF_TIMEOUT_SECONDS = 30
TRUSTED_JSON_MAX_BYTES = 1024 * 1024
EXECUTABLE_DELETION_PROOFS = {
    "workflow-pilot-decisions": {
        "path": DECISION_RECORD_PATH,
        "consumer": "workflow-pilot-reporter",
        "check": "workflow-pilot-tests",
    },
    "workflow-pilot-fixture": {
        "path": BASELINE_FIXTURE_PATH,
        "consumer": "workflow-pilot-reporter",
        "check": "workflow-pilot-tests",
    },
    "workflow-pilot-reporter": {
        "path": REPORTER_PATH,
        "consumer": "workflow-pilot-tests",
        "check": "workflow-pilot-tests",
    },
}

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
PR_OPEN_PHASE_EVENT_TYPES = {
    "base_changed",
    "build_saved",
    "candidate_superseded",
    "conflict_detected",
    "manual_reject",
    "metadata_maintenance",
    "pilot_coordination",
    "review_saved",
    "threshold_override_introduced",
}
POST_MERGE_EVENT_TYPES = {
    "broken_master",
    "escaped_defect",
}
HANDOFF_OUTCOMES = {"accepted", "in_progress", "interrupted", "rejected"}
HANDOFF_REJECTION_CODES = {
    "authoritative-run-failed",
    "authoritative-run-incomplete",
    "authority-ruleset-bypass-mismatch",
    "changed-lines-budget-exceeded",
    "checker-bootstrap-not-trusted-push-eligible",
    "code-contract-not-merged",
    "closed-owner-reused",
    "conflicting-worktree",
    "coordinator-unavailable",
    "coordinator-actor-unauthorized",
    "dirty-worktree",
    "duplicate-coordinator",
    "duplicate-owner",
    "duplicate-watcher",
    "duplicate-handoff-code-contract",
    "host-process-action-prohibited",
    "implementation-not-terminated-before-collection",
    "handoff-task-identity-mismatch",
    "handoff-task-status-mismatch",
    "implementation-owner-remote-action",
    "incomplete-check",
    "incomplete-evidence",
    "incomplete-lifecycle",
    "invalid-protocol-derivation",
    "invalid-recovery-telemetry",
    "invalid-runtime-telemetry",
    "invalid-check-receipt",
    "invalid-coordinator-attestation",
    "invalid-lifecycle-successor",
    "interrupted-check-not-incomplete",
    "interruption-time-mismatch",
    "missing-commit",
    "missing-copilot-trailer",
    "missing-evidence",
    "missing-or-duplicate-watcher",
    "missing-closed-resource-receipt",
    "missing-runtime-telemetry",
    "missing-required-code-contract-edge",
    "missing-parent-post-merge-gate",
    "missing-master-recovery",
    "missing-handoff-code-contract",
    "oom-worktree-not-preserved",
    "overlapping-lifecycle-successor",
    "orphan-replacement",
    "owner-lifetime-exceeded",
    "owner-rss-exceeded",
    "protocol-changes-budget-exceeded",
    "prior-handoff-history-fork",
    "ram-bytes-budget-exceeded",
    "replacement-context-mismatch",
    "replacement-assignment-not-causal",
    "replacement-owner-count",
    "replacement-owner-reused",
    "replacement-without-interruption",
    "recovery-content-not-resolved",
    "remote-coverage-incomplete",
    "root-owner-count",
    "required-check-failed",
    "result-not-worktree-head",
    "rom-bytes-budget-exceeded",
    "review-successor-parent-mismatch",
    "run-without-commit",
    "scope-violation",
    "stale-result",
    "stale-run",
    "task-status-dependency-mismatch",
    "unquantified-diff",
    "unexpected-resource-receipt",
    "unresolved-actor-id",
    "unrelated-branch",
    "watcher-todo-dependency",
    "watcher-authority-stale",
    "watcher-owner-mismatch",
    "watcher-run-mismatch",
    "event-after-terminal-coverage",
    "wrong-parent",
    "wrong-code-contract-edge",
    "wrong-worktree",
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
EXPECTED_RESULT_PATHS = frozenset(
    {
        "artifacts.invalidated_review_ids",
        "builds.action_required",
        "builds.active",
        "builds.cancelled",
        "builds.duplicate_unchanged_sha",
        "builds.failure",
        "builds.minutes",
        "builds.neutral",
        "builds.runs",
        "builds.sample_size",
        "builds.skipped",
        "builds.spotlight.action_required",
        "builds.spotlight.active",
        "builds.spotlight.cancelled",
        "builds.spotlight.failure",
        "builds.spotlight.minutes",
        "builds.spotlight.neutral",
        "builds.spotlight.pr",
        "builds.spotlight.runs",
        "builds.spotlight.skipped",
        "builds.spotlight.success",
        "builds.success",
        "classification_summary.flags.bulk_deletion",
        "classification_summary.flags.generated_only",
        "classification_summary.flags.reverted",
        "classification_summary.flags.stacked",
        "classification_summary.flags.still_running",
        "classification_summary.flags.superseded",
        "classification_summary.work_states.cancelled",
        "classification_summary.work_states.merged",
        "classification_summary.work_states.still_running",
        "computed.seal",
        "decisions.seal",
        "delivery.first_push_to_clean_review.eligible_pull_requests",
        "delivery.first_push_to_clean_review.excluded_without_complete_evidence",
        "delivery.first_push_to_clean_review.median_hours",
        "delivery.first_push_to_clean_review.pilot_ready",
        "delivery.first_push_to_clean_review.reason",
        "delivery.first_push_to_clean_review.status",
        "delivery.issue_to_merge.eligible_pull_requests",
        "delivery.issue_to_merge.excluded_without_linked_issue",
        "delivery.issue_to_merge.median_hours",
        "delivery.merged_pull_requests",
        "delivery.pr_open_to_merge_median_hours",
        "efficiency.metadata_maintenance_minutes",
        "efficiency.net_saved_minutes",
        "efficiency.pilot_coordination_minutes",
        "efficiency.saved_build_minutes",
        "efficiency.saved_review_minutes",
        "events.base_changes",
        "events.broken_master",
        "events.close_reopen_cycles",
        "events.conflicts",
        "events.escaped_defects",
        "events.manual_rejects",
        "events.reverts",
        "events.security_findings",
        "events.spotlight_pr",
        "events.superseded_candidates",
        "identities.seal",
        "reviews.changed_lines",
        "reviews.current_resolved_findings",
        "reviews.current_unresolved_findings",
        "reviews.rounds",
        "reviews.spotlight_pr",
        "reviews.superseded_rounds",
        "reviews.valid_findings",
        "reviews.valid_findings_per_kloc",
        "reviews.valid_findings_per_review",
        "schema_version",
        "snapshot.base_sha",
        "snapshot.captured_at",
        "snapshot.lifecycle_as_of",
        "snapshot.repository",
        "snapshot.window.end",
        "snapshot.window.start",
    }
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


def _json_file_signature(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid,
        metadata.st_size, metadata.st_nlink,
        getattr(metadata, "st_mtime_ns", 0), getattr(metadata, "st_ctime_ns", 0),
    )


def _load_bounded_json(path: Path, *, label: str, max_bytes: int) -> Any:
    absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        metadata = absolute.lstat()
    except OSError as error:
        raise PilotDataError(f"cannot inspect {label}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise PilotDataError(f"{label} must be a regular file")
    if metadata.st_size > max_bytes:
        raise PilotDataError(f"{label} exceeds 1 MiB")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(os.fspath(absolute), flags)
    except OSError as error:
        raise PilotDataError(f"cannot open {label}: {error}") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise PilotDataError(f"{label} must be a regular file")
        if (
            opened.st_size > max_bytes
            or _json_file_signature(opened) != _json_file_signature(metadata)
        ):
            raise PilotDataError(f"{label} changed before read")
        raw = bytearray()
        while len(raw) <= max_bytes:
            chunk = os.read(descriptor, min(65536, max_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        final = os.fstat(descriptor)
    except OSError as error:
        raise PilotDataError(f"cannot read {label}: {error}") from error
    finally:
        os.close(descriptor)
    if len(raw) > max_bytes:
        raise PilotDataError(f"{label} exceeds 1 MiB")
    if (
        _json_file_signature(final) != _json_file_signature(metadata)
        or len(raw) != metadata.st_size
    ):
        raise PilotDataError(f"{label} changed while being read")
    try:
        return parse_json(raw.decode("utf-8"), label)
    except UnicodeError as error:
        raise PilotDataError(f"{label} is not valid UTF-8") from error


def load_json(
    path: Path, *, label: str | None = None, max_bytes: int | None = None,
) -> Any:
    if max_bytes is not None:
        return _load_bounded_json(path, label=label or str(path), max_bytes=max_bytes)
    try:
        return parse_json(path.read_text(encoding="utf-8"), label or str(path))
    except OSError as error:
        raise PilotDataError(f"cannot read {label or path}: {error}") from error


def parse_json(text: str, label: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_object_no_duplicates)
    except json.JSONDecodeError as error:
        raise PilotDataError(f"invalid JSON in {label}: {error}") from error


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
    if not isinstance(value, str) or RFC3339_UTC_RE.fullmatch(value) is None:
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


def canonical_commit_message(message_bytes: bytes, sha: str) -> str:
    try:
        message = message_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PilotDataError(
            f"Git commit {sha} message is not valid UTF-8"
        ) from error
    if message.endswith("\n"):
        return message[:-1]
    return message


def canonical_revert_target(message: str, sha: str) -> str | None:
    mention_count = message.lower().count("this reverts commit")
    match = REVERT_TRAILER_RE.search(message)
    if mention_count == 0:
        return None
    if mention_count != 1 or match is None:
        raise PilotDataError(
            f"revert commit {sha} has an invalid canonical revert trailer"
        )
    return match.group(1)


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


def pull_request_for_run(
    run: dict[str, Any],
    pull_requests: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    pr = next(
        (
            candidate
            for candidate in pull_requests.values()
            if candidate["head_branch"] == run["head_branch"]
        ),
        None,
    )
    pull_ref = re.fullmatch(r"refs/pull/([1-9][0-9]*)/head", run["head_branch"])
    if pr is None and pull_ref is not None:
        pr = pull_requests.get(int(pull_ref.group(1)))
    return pr


def observed_candidate_shas(
    pull_requests: dict[int, dict[str, Any]],
    reviews: Iterable[dict[str, Any]],
    runs: Iterable[dict[str, Any]],
    events: Iterable[dict[str, Any]],
) -> dict[int, set[str]]:
    result: dict[int, set[str]] = defaultdict(set)
    for review in reviews:
        if review["commit_sha"] in pull_requests[review["pr_number"]]["commit_shas"]:
            result[review["pr_number"]].add(review["commit_sha"])
    for run in runs:
        pr = pull_request_for_run(run, pull_requests)
        if pr is not None and run["head_sha"] in pr["commit_shas"]:
            result[pr["number"]].add(run["head_sha"])
    for event in events:
        if "pr_number" not in event:
            continue
        candidates = set(pull_requests[event["pr_number"]]["commit_shas"])
        for field in ("sha", "old_sha", "new_sha"):
            if field in event and event[field] in candidates:
                result[event["pr_number"]].add(event[field])
    return result


def trusted_git_executable() -> str:
    try:
        executable = Path(GIT).resolve(strict=True)
    except OSError as error:
        raise PilotDataError(f"trusted Git executable is unavailable: {error}") from error
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise PilotDataError(f"trusted Git executable {executable} is not executable")
    return str(executable)


def git_environment(*, offline: bool) -> dict[str, str]:
    environment = {
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }
    if offline:
        environment["GIT_NO_LAZY_FETCH"] = "1"
    return environment


def git_command(repository_root: Path, *arguments: str) -> tuple[str, ...]:
    return (
        trusted_git_executable(),
        "--no-replace-objects",
        "-C",
        str(repository_root),
        *arguments,
    )


def run_git(
    repository_root: Path,
    *arguments: str,
) -> bytes:
    try:
        completed = subprocess.run(
            git_command(repository_root, *arguments),
            env=git_environment(offline=True),
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise PilotDataError(f"cannot execute Git: {error}") from error
    if completed.returncode == 0:
        return completed.stdout
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    raise PilotDataError(
        f"Git {' '.join(arguments)} failed"
        + (f": {detail}" if detail else "")
    )


def _github_repository_from_remote(remote: str) -> str | None:
    patterns = (
        r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
        r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?$",
        r"ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, remote)
        if match is not None:
            return match.group(1)
    return None
def _git_dir(repository_root: Path) -> Path:
    entry = repository_root / ".git"
    try:
        metadata = entry.lstat()
    except OSError as error:
        raise PilotDataError(f"cannot inspect repository .git entry: {error}") from error
    if stat.S_ISDIR(metadata.st_mode):
        return entry
    if not stat.S_ISREG(metadata.st_mode):
        raise PilotDataError("repository .git entry is not permitted")
    try:
        raw = entry.read_text(encoding="utf-8")
    except OSError as error:
        raise PilotDataError(f"cannot read repository .git file: {error}") from error
    prefix = "gitdir:"
    if not raw.startswith(prefix):
        raise PilotDataError("repository .git file is malformed")
    git_dir = Path(raw[len(prefix):].strip())
    if not git_dir.is_absolute():
        git_dir = repository_root / git_dir
    try:
        git_dir = git_dir.resolve(strict=True)
    except OSError as error:
        raise PilotDataError(f"repository gitdir is unavailable: {error}") from error
    if not git_dir.is_dir():
        raise PilotDataError("repository gitdir is not a directory")
    return git_dir
def _reject_git_metadata_path(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise PilotDataError(f"cannot inspect repository {label}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size:
        raise PilotDataError(f"repository {label} is not permitted")


def validate_repository_root(repository_root: Path) -> Path:
    try:
        resolved = repository_root.resolve(strict=True)
    except OSError as error:
        raise PilotDataError(
            f"repository root {repository_root} is unavailable: {error}"
        ) from error
    if not resolved.is_dir():
        raise PilotDataError(f"repository root {resolved} is not a directory")
    git_dir = _git_dir(resolved)
    for relative, label in (
        ("info/grafts", "graft file"),
        ("info/attributes", "local attributes file"),
        ("objects/info/alternates", "alternate object store"),
        ("objects/info/http-alternates", "HTTP alternate object store"),
    ):
        _reject_git_metadata_path(git_dir / relative, label)
    top_level = Path(
        run_git(resolved, "rev-parse", "--show-toplevel")
        .decode("utf-8")
        .strip()
    ).resolve()
    if top_level != resolved:
        raise PilotDataError(
            f"repository root must be the exact Git top level {top_level}"
        )
    replace_refs = run_git(
        resolved,
        "for-each-ref",
        "--format=%(refname)",
        "refs/replace/",
    )
    if replace_refs.strip():
        raise PilotDataError("repository replacement refs are not permitted")
    return resolved


def _load_git_commit_objects(
    repository_root: Path,
    shas: Iterable[str],
) -> dict[str, dict[str, Any]]:
    ordered = sorted(set(shas))
    try:
        completed = subprocess.run(
            git_command(repository_root, "cat-file", "--batch"),
            input=b"".join(f"{sha}\n".encode("ascii") for sha in ordered),
            env=git_environment(offline=True),
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise PilotDataError(f"cannot execute Git: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PilotDataError(
            "Git cat-file --batch failed" + (f": {detail}" if detail else "")
        )

    output = completed.stdout
    offset = 0
    result: dict[str, dict[str, Any]] = {}
    for requested_sha in ordered:
        header_end = output.find(b"\n", offset)
        if header_end < 0:
            raise PilotDataError(
                f"Git returned an incomplete object header for {requested_sha}"
            )
        header = output[offset:header_end].decode("ascii", errors="replace")
        offset = header_end + 1
        if header.endswith(" missing"):
            raise PilotDataError(
                f"fixture commit {requested_sha} does not exist in the repository"
            )
        fields = header.split()
        if len(fields) != 3 or fields[1] != "commit":
            raise PilotDataError(
                f"fixture identity {requested_sha} is not a Git commit object"
            )
        try:
            size = int(fields[2])
        except ValueError as error:
            raise PilotDataError(
                f"Git returned an invalid object size for {requested_sha}"
            ) from error
        payload = output[offset : offset + size]
        offset += size
        if len(payload) != size or output[offset : offset + 1] != b"\n":
            raise PilotDataError(
                f"Git returned an incomplete commit object for {requested_sha}"
            )
        offset += 1
        try:
            headers, message_bytes = payload.split(b"\n\n", 1)
            header_lines = headers.decode("utf-8").splitlines()
        except (ValueError, UnicodeDecodeError) as error:
            raise PilotDataError(
                f"Git commit {requested_sha} is not valid UTF-8 commit data"
            ) from error
        message = canonical_commit_message(message_bytes, requested_sha)
        parents = [
            line.split(" ", 1)[1]
            for line in header_lines
            if line.startswith("parent ")
        ]
        committer = next(
            (line for line in header_lines if line.startswith("committer ")),
            None,
        )
        if committer is None:
            raise PilotDataError(
                f"Git commit {requested_sha} lacks a committer timestamp"
            )
        try:
            timestamp = int(committer.rsplit(" ", 2)[1])
        except (IndexError, ValueError) as error:
            raise PilotDataError(
                f"Git commit {requested_sha} has an invalid committer timestamp"
            ) from error
        result[requested_sha] = {
            "parents": parents,
            "committed_at": datetime.fromtimestamp(
                timestamp,
                timezone.utc,
            ),
            "message": message,
        }
    if offset != len(output):
        raise PilotDataError("Git returned unexpected trailing object data")
    return result


def _git_ancestors(sha: str, graph: dict[str, list[str]]) -> set[str]:
    result = set()
    pending = [sha]
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        pending.extend(graph.get(current, ()))
    return result


def validate_repository_authority(
    repository_root: Path,
    data: dict[str, Any],
) -> dict[str, Any]:
    repository_root = validate_repository_root(repository_root)
    fixture = data["fixture"]
    remote = run_git(
        repository_root,
        "config",
        "--get",
        "remote.origin.url",
    ).decode("utf-8").strip()
    repository = _github_repository_from_remote(remote)
    if repository != fixture["repository"]:
        raise PilotDataError(
            "fixture.repository does not match the checked-out origin "
            f"({fixture['repository']!r} != {repository!r})"
        )

    commits = data["commits"]
    actual = _load_git_commit_objects(repository_root, commits)
    for sha, commit in commits.items():
        actual_commit = actual[sha]
        if actual_commit["parents"] != commit["parents"]:
            raise PilotDataError(
                f"commit {sha} parents do not match the Git object database"
            )
        fixture_time = parse_time(
            commit["committed_at"],
            f"commit {sha}.committed_at",
        )
        if actual_commit["committed_at"] != fixture_time:
            raise PilotDataError(
                f"commit {sha} timestamp does not match the Git object database"
            )
        if actual_commit["message"] != commit["message"]:
            raise PilotDataError(
                f"commit {sha} message does not match the Git object database"
            )

    base_sha = fixture["base_sha"]
    if base_sha not in actual:
        raise PilotDataError("fixture.base_sha is not a validated Git commit")
    captured = parse_time(fixture["captured_at"], "fixture.captured_at")

    def require_commit_available(
        sha: str,
        observed_at: datetime,
        label: str,
    ) -> None:
        if actual[sha]["committed_at"] > observed_at:
            raise PilotDataError(
                f"{label} predates repository commit {sha} availability"
            )

    require_commit_available(base_sha, captured, "fixture.base_sha")
    parent_graph = {
        sha: commit["parents"]
        for sha, commit in actual.items()
    }
    ancestor_cache = {
        sha: _git_ancestors(sha, parent_graph)
        for sha in commits
    }
    base_history = ancestor_cache[base_sha]

    pull_requests = data["pull_requests"]
    observed_candidates: dict[int, set[str]] = {}
    for number, pr in pull_requests.items():
        candidate_shas = set(pr["commit_shas"])
        if pr["head_sha"] not in candidate_shas:
            raise PilotDataError(
                f"PR {number} head is absent from its candidate commit identities"
            )
        if pr["state"] == "merged":
            merge_sha = pr["merge_sha"]
            merged_at = parse_time(
                pr["merged_at"],
                f"PR {number}.merged_at",
            )
            require_commit_available(
                merge_sha,
                merged_at,
                f"PR {number} merge",
            )
            merge_parents = actual[merge_sha]["parents"]
            if len(merge_parents) != 2 or merge_parents[1] != pr["head_sha"]:
                raise PilotDataError(
                    f"PR {number} merge commit does not bind its exact candidate head"
                )
            authoritative_candidates = (
                ancestor_cache[pr["head_sha"]]
                - _git_ancestors(merge_parents[0], parent_graph)
            )
            if not authoritative_candidates <= candidate_shas:
                raise PilotDataError(
                    f"PR {number} candidate identities omit its Git merge range"
                )
            if merge_sha not in base_history:
                raise PilotDataError(
                    f"PR {number} merge commit is outside the frozen base history"
                )
        else:
            parent_pr = next(
                (
                    candidate
                    for candidate in pull_requests.values()
                    if candidate["head_branch"] == pr["base_ref"]
                ),
                None,
            )
            if parent_pr is not None:
                base = parent_pr["head_sha"]
            else:
                base = (
                    run_git(
                        repository_root,
                        "merge-base",
                        base_sha,
                        pr["head_sha"],
                    )
                    .decode("ascii")
                    .strip()
                )
            authoritative_candidates = (
                ancestor_cache[pr["head_sha"]]
                - _git_ancestors(base, parent_graph)
            )
            if not authoritative_candidates <= candidate_shas:
                raise PilotDataError(
                    f"PR {number} candidate identities omit its Git history"
                )
        observed_candidates[number] = authoritative_candidates
        candidate_boundary = parse_time(
            pr["closed_at"],
            f"PR {number}.closed_at",
            nullable=True,
        ) or captured
        for sha in candidate_shas:
            require_commit_available(
                sha,
                candidate_boundary,
                f"PR {number} candidate history",
            )

    for review_id, review in data["reviews"].items():
        pr = pull_requests[review["pr_number"]]
        if review["commit_sha"] not in pr["commit_shas"]:
            raise PilotDataError(
                f"review {review_id} commit is outside PR {pr['number']} "
                "candidate history"
            )
        require_commit_available(
            review["commit_sha"],
            parse_time(
                review["submitted_at"],
                f"review {review_id}.submitted_at",
            ),
            f"review {review_id}",
        )

    for run_id, run in data["runs"].items():
        pr = pull_request_for_run(run, pull_requests)
        if pr is not None:
            if run["head_sha"] not in pr["commit_shas"]:
                raise PilotDataError(
                    f"workflow run {run_id} commit is outside PR "
                    f"{pr['number']} candidate history"
                )
        require_commit_available(
            run["head_sha"],
            parse_time(run["created_at"], f"run {run_id}.created_at"),
            f"workflow run {run_id}",
        )
        if (
            run["head_branch"] == fixture["default_branch"]
            and run["head_sha"] not in base_history
        ):
            raise PilotDataError(
                f"workflow run {run_id} is outside the frozen base history"
            )

    observed = observed_candidate_shas(
        pull_requests,
        data["reviews"].values(),
        data["runs"].values(),
        data["events"].values(),
    )
    for number, shas in observed.items():
        observed_candidates[number].update(shas)

    for event_id, event in data["events"].items():
        occurred_at = parse_time(
            event["occurred_at"],
            f"event {event_id}.occurred_at",
        )
        for field in ("sha", "old_sha", "new_sha"):
            if field in event:
                require_commit_available(
                    event[field],
                    occurred_at,
                    f"event {event_id!r} {field}",
                )

    for number, pr in pull_requests.items():
        unobserved = set(pr["commit_shas"]) - observed_candidates[number]
        if unobserved:
            raise PilotDataError(
                f"PR {number} candidate identities contain unobserved commits: "
                + ", ".join(sorted(unobserved))
            )

    reverts = []
    for sha, commit in commits.items():
        target = canonical_revert_target(commit["message"], sha)
        if target is None:
            continue
        if target not in actual:
            raise PilotDataError(
                f"revert commit {sha} targets unavailable commit {target}"
            )
        if actual[sha]["committed_at"] <= actual[target]["committed_at"]:
            raise PilotDataError(
                f"revert commit {sha} is not later than target {target}"
            )
        if target not in ancestor_cache[sha]:
            raise PilotDataError(
                f"revert commit {sha} is not descended from target {target}"
            )
        if target not in base_history or sha not in base_history:
            raise PilotDataError(
                f"revert commit {sha} and target {target} are not both in "
                "the frozen base history"
            )
        reverts.append({"commit": sha, "reverts": target})

    return {
        "repository_root": repository_root,
        "commits": actual,
        "reverts": sorted(
            reverts,
            key=lambda relation: (relation["commit"], relation["reverts"]),
        ),
    }


def load_decisions_from_commit(repository_root: Path, sha: str) -> dict[str, Any]:
    specification = f"{sha}:{DECISION_RECORD_PATH.as_posix()}"
    try:
        raw = run_git(repository_root, "show", specification)
    except PilotDataError as error:
        raise PilotDataError(
            f"commit {sha} lacks {DECISION_RECORD_PATH.as_posix()}"
        ) from error
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PilotDataError(
            f"commit {sha} decision record is not UTF-8"
        ) from error
    return expect_object(
        parse_json(text, specification),
        f"decision record at commit {sha}",
    )


def historical_override_entry(
    decisions: dict[str, Any],
    sha: str,
    pull_request: int,
    override_index: int,
) -> dict[str, Any]:
    label = f"decision record at commit {sha}"
    expect_keys(
        decisions,
        label,
        ("schema_version", "pull_requests", "artifacts"),
    )
    schema_version = expect_int(
        decisions["schema_version"],
        f"{label}.schema_version",
        1,
    )
    if schema_version != SCHEMA_VERSION:
        raise PilotDataError(
            f"{label} schema_version must be {SCHEMA_VERSION}"
        )
    records = expect_list(decisions["pull_requests"], f"{label}.pull_requests")
    expect_list(decisions["artifacts"], f"{label}.artifacts")
    matches = []
    identities = []
    for record_index, raw_record in enumerate(records):
        record_label = f"{label}.pull_requests[{record_index}]"
        record = expect_object(raw_record, record_label)
        expect_keys(
            record,
            record_label,
            (
                "pull_request",
                "risk_boundaries",
                "threshold",
                "gate_mode",
                "stack",
                "pilot",
            ),
        )
        number = expect_int(
            record["pull_request"], f"{record_label}.pull_request", 1
        )
        identities.append(number)
        if number == pull_request:
            matches.append(record)
    expect_unique(identities, f"{label} pull-request identities")
    if len(matches) != 1:
        raise PilotDataError(
            f"commit {sha} decision record lacks exact PR {pull_request} identity"
        )
    record = matches[0]
    risks = expect_list(
        record["risk_boundaries"],
        f"{label} PR {pull_request}.risk_boundaries",
    )
    if not risks:
        raise PilotDataError(
            f"{label} PR {pull_request}.risk_boundaries must not be empty"
        )
    for risk in risks:
        expect_enum(
            risk,
            RISK_BOUNDARIES,
            f"{label} PR {pull_request}.risk_boundaries member",
        )
    expect_unique(risks, f"{label} PR {pull_request}.risk_boundaries")
    if "none" in risks and len(risks) != 1:
        raise PilotDataError(
            f"{label} PR {pull_request}.risk_boundaries none must stand alone"
        )
    threshold = expect_object(
        record["threshold"],
        f"{label} PR {pull_request}.threshold",
    )
    expect_keys(
        threshold,
        f"{label} PR {pull_request}.threshold",
        ("triggers", "override_history"),
    )
    triggers = expect_list(
        threshold["triggers"],
        f"{label} PR {pull_request}.threshold.triggers",
    )
    if not triggers:
        raise PilotDataError(
            f"{label} PR {pull_request}.threshold.triggers must not be empty"
        )
    for trigger in triggers:
        expect_enum(
            trigger,
            THRESHOLD_TRIGGERS,
            f"{label} PR {pull_request}.threshold.triggers member",
        )
    expect_unique(triggers, f"{label} PR {pull_request}.threshold.triggers")
    if "none" in triggers and len(triggers) != 1:
        raise PilotDataError(
            f"{label} PR {pull_request}.threshold.triggers none must stand alone"
        )
    expect_enum(
        record["gate_mode"],
        GATE_MODES,
        f"{label} PR {pull_request}.gate_mode",
    )
    stack = expect_object(
        record["stack"],
        f"{label} PR {pull_request}.stack",
    )
    expect_keys(
        stack,
        f"{label} PR {pull_request}.stack",
        ("depth", "parent_pr", "exception_reason"),
    )
    depth = expect_int(
        stack["depth"],
        f"{label} PR {pull_request}.stack.depth",
        0,
    )
    if depth > 3:
        raise PilotDataError(
            f"{label} PR {pull_request}.stack.depth exceeds the supported maximum"
        )
    if stack["parent_pr"] is not None:
        expect_int(
            stack["parent_pr"],
            f"{label} PR {pull_request}.stack.parent_pr",
            1,
        )
    if stack["exception_reason"] is not None:
        expect_string(
            stack["exception_reason"],
            f"{label} PR {pull_request}.stack.exception_reason",
        )
    pilot = expect_object(
        record["pilot"],
        f"{label} PR {pull_request}.pilot",
    )
    expect_keys(
        pilot,
        f"{label} PR {pull_request}.pilot",
        ("included", "disposition"),
    )
    expect_bool(
        pilot["included"],
        f"{label} PR {pull_request}.pilot.included",
    )
    expect_enum(
        pilot["disposition"],
        PILOT_DISPOSITIONS,
        f"{label} PR {pull_request}.pilot.disposition",
    )
    if pilot["included"] and pilot["disposition"] in {
        "baseline-only",
        "excluded",
    }:
        raise PilotDataError(
            f"{label} PR {pull_request}.pilot inclusion contradicts disposition"
        )
    if not pilot["included"] and pilot["disposition"] in {
        "evaluate",
        "graduated",
    }:
        raise PilotDataError(
            f"{label} PR {pull_request}.pilot exclusion contradicts disposition"
        )
    history = expect_list(
        threshold["override_history"],
        f"{label} PR {pull_request}.threshold.override_history",
    )
    if override_index >= len(history):
        raise PilotDataError(
            f"commit {sha} decision record lacks PR {pull_request} threshold "
            f"override {override_index}"
        )
    entry_label = (
        f"{label} PR {pull_request}.threshold.override_history[{override_index}]"
    )
    entry = expect_object(history[override_index], entry_label)
    expect_keys(entry, entry_label, ("enabled", "reason"))
    expect_bool(entry["enabled"], f"{entry_label}.enabled")
    expect_string(entry["reason"], f"{entry_label}.reason")
    return entry


def validate_override_git_provenance(
    repository_root: Path,
    data: dict[str, Any],
    pull_request: int,
    override_index: int,
    override: dict[str, Any],
    introduction: dict[str, Any],
    first_review: dict[str, Any] | None,
) -> None:
    sha = introduction["sha"]
    pr = data["pull_requests"][pull_request]
    if sha not in pr["commit_shas"]:
        raise PilotDataError(
            f"PR {pull_request} threshold override {override_index} cites a "
            "non-candidate commit"
        )
    if not is_ancestor(sha, pr["head_sha"], data["commits"]):
        raise PilotDataError(
            f"PR {pull_request} threshold override {override_index} commit is "
            "not in the candidate ancestry"
        )
    actual_time = data["repository_authority"]["commits"][sha]["committed_at"]
    fixture_time = parse_time(
        data["commits"][sha]["committed_at"],
        f"commit {sha}.committed_at",
    )
    introduced_at = parse_time(
        introduction["occurred_at"],
        f"event {introduction['id']}.occurred_at",
    )
    if actual_time != fixture_time or introduced_at != actual_time:
        raise PilotDataError(
            f"PR {pull_request} threshold override {override_index} timestamp "
            "does not match its immutable Git commit"
        )

    expected_digest = threshold_override_digest(
        pull_request,
        override_index,
        override,
    )
    if introduction["decision_digest"] != expected_digest:
        raise PilotDataError(
            f"PR {pull_request} threshold override {override_index} digest "
            "does not match the current decision entry"
        )
    introduced_decisions = load_decisions_from_commit(repository_root, sha)
    introduced_entry = historical_override_entry(
        introduced_decisions,
        sha,
        pull_request,
        override_index,
    )
    introduced_digest = threshold_override_digest(
        pull_request,
        override_index,
        introduced_entry,
    )
    if introduced_entry != override or introduced_digest != expected_digest:
        raise PilotDataError(
            f"PR {pull_request} threshold override {override_index} differs "
            "from its immutable introduction tree"
        )

    if first_review is None:
        return
    review_sha = first_review["commit_sha"]
    review_at = parse_time(
        first_review["submitted_at"],
        f"review {first_review['id']}.submitted_at",
    )
    review_commit_time = data["repository_authority"]["commits"][review_sha][
        "committed_at"
    ]
    if review_commit_time > review_at:
        raise PilotDataError(
            f"PR {pull_request} first review predates its reviewed Git commit"
        )
    if actual_time >= review_at:
        raise PilotDataError(
            f"PR {pull_request} threshold override {override_index} commit "
            "does not predate the first review"
        )
    if not is_ancestor(sha, review_sha, data["commits"]):
        raise PilotDataError(
            f"PR {pull_request} threshold override {override_index} was not "
            "present at the first reviewed commit"
        )
    reviewed_decisions = load_decisions_from_commit(repository_root, review_sha)
    reviewed_entry = historical_override_entry(
        reviewed_decisions,
        review_sha,
        pull_request,
        override_index,
    )
    if reviewed_entry != override:
        raise PilotDataError(
            f"PR {pull_request} threshold override {override_index} changed "
            "between introduction and first review"
        )


def _validate_fixture_root(fixture: dict[str, Any]) -> None:
    schema_version = expect_int(
        fixture.get("schema_version"),
        "fixture.schema_version",
        1,
    )
    if schema_version not in {SCHEMA_VERSION, HANDOFF_FIXTURE_SCHEMA_VERSION}:
        raise PilotDataError(
            "fixture schema_version must be "
            f"{SCHEMA_VERSION} or {HANDOFF_FIXTURE_SCHEMA_VERSION}"
        )
    required = (
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
        "review_thread_event_source",
        "review_thread_events",
        "workflow_runs",
        "commits",
        "events",
        "artifacts",
        "dependency_edges",
    )
    if schema_version == HANDOFF_FIXTURE_SCHEMA_VERSION:
        required += ("implementation_handoffs",)
    expect_keys(
        fixture,
        "fixture",
        required,
    )
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
        "review_thread_events",
        "workflow_runs",
        "commits",
        "events",
        "artifacts",
        "dependency_edges",
    ):
        expect_list(fixture[field], f"fixture.{field}")
    if schema_version == HANDOFF_FIXTURE_SCHEMA_VERSION:
        expect_list(
            fixture["implementation_handoffs"],
            "fixture.implementation_handoffs",
        )


def validate_implementation_handoff_trust(raw: Any) -> dict[str, dict[str, Any]]:
    trust = expect_object(raw, "implementation_handoff_trust")
    expect_keys(trust, "implementation_handoff_trust", ("schema_version", "anchors"))
    if expect_int(trust["schema_version"], "implementation_handoff_trust.schema_version", 1) != 1:
        raise PilotDataError("implementation_handoff_trust.schema_version must be 1")
    anchors = {}
    for index, raw_anchor in enumerate(expect_list(trust["anchors"], "implementation_handoff_trust.anchors")):
        label = f"implementation_handoff_trust.anchors[{index}]"
        anchor = copy.deepcopy(expect_object(raw_anchor, label))
        input_seal = anchor.get("input_seal")
        if not isinstance(input_seal, str) or SHA256_RE.fullmatch(input_seal) is None:
            raise PilotDataError(f"{label}.input_seal must be a lowercase SHA-256")
        if input_seal in anchors:
            raise PilotDataError(f"duplicate implementation handoff trust {input_seal!r}")
        anchors[input_seal] = anchor
    return anchors


def validate_implementation_handoffs(
    fixture: dict[str, Any],
    repository_root: Path | None = None,
    implementation_handoff_trust: Any | None = None,
    implementation_handoff_installation: Any | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    if fixture["schema_version"] != HANDOFF_FIXTURE_SCHEMA_VERSION:
        return {"bundles": {}, "handoffs": {}}
    from scripts.workflow_pilot import agent_handoff
    if implementation_handoff_trust is None:
        raise PilotDataError(
            "implementation_handoffs require external trusted anchor attestations"
        )
    if implementation_handoff_installation is None:
        raise PilotDataError(
            "implementation_handoffs require an external trusted installation"
        )
    lifecycle_as_of = parse_time(fixture["lifecycle_as_of"], "fixture.lifecycle_as_of")
    bundles: dict[str, dict[str, Any]] = {}
    handoffs: dict[str, dict[str, Any]] = {}
    issue_timelines: dict[int, list[dict[str, Any]]] = defaultdict(list)
    trusted_handoffs = validate_implementation_handoff_trust(implementation_handoff_trust)
    try:
        trusted_installation = agent_handoff._coerce_reporter_trusted_installation(implementation_handoff_installation, repository_root=repository_root, label="implementation_handoff_installation")  # noqa: SLF001
    except agent_handoff.HandoffDataError as error:
        raise PilotDataError(str(error)) from error
    for index, raw in enumerate(fixture["implementation_handoffs"]):
        label = f"implementation_handoffs[{index}]"
        raw_record = expect_object(raw, label)
        input_seal = raw_record.get("input_seal")
        try:
            bundle = agent_handoff.verify_reporter_record(
                raw,
                revalidate_git=False,
                trusted_anchor=trusted_handoffs.get(input_seal),
                trusted_installation=trusted_installation,
                current_time=lifecycle_as_of,
            )
        except agent_handoff.HandoffDataError as error:
            raise PilotDataError(f"{label}: {error}") from error
        summary = expect_object(bundle["result"]["summary"], f"{label}.result.summary")
        local_rejection_codes = {
            code
            for handoff in expect_list(
                bundle["result"]["handoffs"],
                f"{label}.result.handoffs",
            )
            for code in expect_list(
                expect_object(
                    handoff,
                    f"{label}.result.handoffs item",
                )["rejection_codes"],
                f"{label}.result.handoffs.rejection_codes",
            )
        }
        (
            _derived_summary,
            derived_bundle_rejection_codes,
            _delivery_graph,
            _watchers,
        ) = agent_handoff.derive_reporter_result_summary(
            bundle["document"],
            bundle["result"],
        )
        bundle_rejection_codes = sorted(
            set(derived_bundle_rejection_codes)
            | (
                set(
                    expect_list(
                        summary["rejection_codes"],
                        f"{label}.result.summary.rejection_codes",
                    )
                )
                - local_rejection_codes
            )
        )
        bundle_trusted_push_eligible = expect_bool(
            summary["trusted_push_eligible"],
            f"{label}.result.summary.trusted_push_eligible",
        )
        identity = bundle["input_seal"]
        if identity in bundles:
            raise PilotDataError(
                f"duplicate implementation handoff bundle {identity!r}"
            )
        bundles[identity] = bundle
        document_handoffs = {
            item["id"]: item for item in bundle["document"]["handoffs"]
        }
        for handoff_index, handoff in enumerate(bundle["result"]["handoffs"]):
            handoff_label = f"{label}.result.handoffs[{handoff_index}]"
            expect_keys(
                handoff,
                handoff_label,
                (
                    "id",
                    "owner_id",
                    "issue",
                    "pull_request",
                    "assigned_at",
                    "closed_at",
                    "state",
                    "outcome",
                    "result_sha",
                    "changed_lines",
                    "changed_paths",
                    "commit_message_sha256",
                    "stale_response",
                    "lifetime_seconds",
                    "peak_rss_bytes",
                    "coordination_turns",
                    "recovery_minutes",
                    "budget_usage",
                    "interruption_snapshot",
                    "rejection_codes",
                ),
            )
            handoff_id = expect_string(handoff["id"], f"{handoff_label}.id")
            if handoff_id in handoffs:
                raise PilotDataError(
                    f"duplicate implementation handoff {handoff_id!r}"
                )
            expect_string(handoff["owner_id"], f"{handoff_label}.owner_id")
            assigned_at = parse_time(
                handoff["assigned_at"],
                f"{handoff_label}.assigned_at",
            )
            closed_at = parse_time(
                handoff["closed_at"],
                f"{handoff_label}.closed_at",
                nullable=True,
            )
            if assigned_at > lifecycle_as_of:
                raise PilotDataError(
                    f"{handoff_label}.assigned_at follows lifecycle_as_of"
                )
            if closed_at is not None:
                if closed_at <= assigned_at:
                    raise PilotDataError(
                        f"{handoff_label}.closed_at must strictly follow assigned_at"
                    )
                if closed_at > lifecycle_as_of:
                    raise PilotDataError(
                        f"{handoff_label}.closed_at follows lifecycle_as_of"
                    )
            outcome = expect_enum(
                handoff["outcome"],
                HANDOFF_OUTCOMES,
                f"{handoff_label}.outcome",
            )
            rejection_codes = expect_list(
                handoff["rejection_codes"],
                f"{handoff_label}.rejection_codes",
            )
            for code_index, code in enumerate(rejection_codes):
                expect_enum(
                    code,
                    HANDOFF_REJECTION_CODES,
                    f"{handoff_label}.rejection_codes[{code_index}]",
                )
            expect_unique(rejection_codes, f"{handoff_label}.rejection_codes")
            expect_bool(
                handoff["stale_response"],
                f"{handoff_label}.stale_response",
            )
            for field in (
                "lifetime_seconds",
                "peak_rss_bytes",
                "coordination_turns",
                "recovery_minutes",
            ):
                expect_int(
                    handoff[field],
                    f"{handoff_label}.{field}",
                    0,
                )
            budget_usage = expect_object(
                handoff["budget_usage"],
                f"{handoff_label}.budget_usage",
            )
            expect_keys(
                budget_usage,
                f"{handoff_label}.budget_usage",
                ("rom_bytes", "ram_bytes", "protocol_changes"),
            )
            for field in ("rom_bytes", "ram_bytes", "protocol_changes"):
                expect_int(
                    budget_usage[field],
                    f"{handoff_label}.budget_usage.{field}",
                    0,
                )
            if handoff["interruption_snapshot"] is not None:
                expect_object(
                    handoff["interruption_snapshot"],
                    f"{handoff_label}.interruption_snapshot",
                )
            if outcome == "accepted":
                if closed_at is None or rejection_codes:
                    raise PilotDataError(
                        f"{handoff_label} accepted outcome requires closure "
                        "without rejections"
                    )
            elif outcome == "rejected":
                if not rejection_codes:
                    raise PilotDataError(
                        f"{handoff_label} rejected outcome requires rejection_codes"
                    )
            elif outcome == "interrupted":
                if closed_at is None:
                    raise PilotDataError(
                        f"{handoff_label} interrupted outcome requires closed_at"
                    )
            elif closed_at is not None:
                raise PilotDataError(
                    f"{handoff_label} in_progress outcome cannot have closed_at"
                )
            handoff["bundle_rejection_codes"] = copy.deepcopy(
                bundle_rejection_codes
            )
            handoff["reported_outcome"] = (
                "bundle_rejected"
                if outcome == "accepted" and not bundle_trusted_push_eligible
                else outcome
            )
            issue_timelines.setdefault(handoff["issue"], []).append(
                {
                    "id": handoff_id,
                    "assigned_at": assigned_at,
                    "closed_at": closed_at or lifecycle_as_of,
                    "replaces_handoff_id": document_handoffs[handoff_id][
                        "replaces_handoff_id"
                    ],
                    "reported_outcome": handoff["reported_outcome"],
                }
            )
            handoffs[handoff_id] = handoff
    for records in issue_timelines.values():
        records.sort(key=lambda item: (item["assigned_at"], item["id"]))
        has_bound_root = False
        previous_end = None
        for record in records:
            if previous_end is not None and record["assigned_at"] <= previous_end:
                raise PilotDataError(
                    f"implementation handoff {record['id']!r} overlaps another same-issue handoff"
                )
            if record["replaces_handoff_id"] is None and has_bound_root:
                raise PilotDataError(
                    f"implementation handoff {record['id']!r} is an unrelated same-issue root"
                )
            has_bound_root = has_bound_root or record["reported_outcome"] not in {
                "bundle_rejected",
                "rejected",
            }
            previous_end = record["closed_at"]
    if set(trusted_handoffs) != set(bundles):
        raise PilotDataError(
            "implementation_handoff_trust must match bundle input seals exactly"
        )
    return {"bundles": bundles, "handoffs": handoffs}


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
    captured = parse_time(fixture["captured_at"], "fixture.captured_at")
    window = expect_object(fixture["window"], "fixture.window")
    window_start = parse_time(window["start"], "fixture.window.start")
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
        submitted = parse_time(item["submitted_at"], f"{label}.submitted_at")
        if submitted < window_start or submitted > captured:
            raise PilotDataError(
                f"{label}.submitted_at is outside the captured analysis window"
            )
        expect_sha(item["commit_sha"], f"{label}.commit_sha")
        state = expect_enum(item["state"], REVIEW_STATES, f"{label}.state")
        if item["author"] == REVIEW_BOT and state != "COMMENTED":
            raise PilotDataError(
                f"{label} Copilot review must have COMMENTED state"
            )
        threads = expect_list(item["thread_ids"], f"{label}.thread_ids")
        for thread_id in threads:
            expect_string(thread_id, f"{label}.thread_ids member")
        expect_unique(threads, f"{label}.thread_ids")
        result[review_id] = item
    return result


def validate_findings(fixture: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    captured = parse_time(fixture["captured_at"], "fixture.captured_at")
    window = expect_object(fixture["window"], "fixture.window")
    window_start = parse_time(window["start"], "fixture.window.start")
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
                "is_resolved",
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
        if created < window_start or created > captured:
            raise PilotDataError(
                f"{label}.created_at is outside the captured analysis window"
            )
        expect_bool(item["is_resolved"], f"{label}.is_resolved")
        expect_bool(item["outdated"], f"{label}.outdated")
        expect_string(item["path"], f"{label}.path")
        result[finding_id] = item
    return result


def validate_review_thread_event_source(
    fixture: dict[str, Any],
) -> dict[str, Any]:
    source = expect_object(
        fixture["review_thread_event_source"],
        "fixture.review_thread_event_source",
    )
    expect_keys(
        source,
        "fixture.review_thread_event_source",
        (
            "kind",
            "complete",
            "coverage_start",
            "coverage_end",
            "unavailable_reason",
        ),
    )
    if source["kind"] != REVIEW_THREAD_EVENT_SOURCE:
        raise PilotDataError(
            "fixture.review_thread_event_source.kind must be "
            f"{REVIEW_THREAD_EVENT_SOURCE!r}"
        )
    complete = expect_bool(
        source["complete"],
        "fixture.review_thread_event_source.complete",
    )
    coverage_start = parse_time(
        source["coverage_start"],
        "fixture.review_thread_event_source.coverage_start",
        nullable=True,
    )
    coverage_end = parse_time(
        source["coverage_end"],
        "fixture.review_thread_event_source.coverage_end",
        nullable=True,
    )
    reason = source["unavailable_reason"]
    if complete:
        if coverage_start is None or coverage_end is None:
            raise PilotDataError(
                "complete review-thread event source requires coverage bounds"
            )
        if coverage_start > coverage_end:
            raise PilotDataError(
                "review-thread event source coverage start follows its end"
            )
        if reason is not None:
            raise PilotDataError(
                "complete review-thread event source cannot have an "
                "unavailable reason"
            )
    else:
        if coverage_start is not None or coverage_end is not None:
            raise PilotDataError(
                "unavailable review-thread event source cannot claim coverage"
            )
        expect_string(
            reason,
            "fixture.review_thread_event_source.unavailable_reason",
        )
    return source


def validate_review_thread_events(
    fixture: dict[str, Any],
    source: dict[str, Any],
    pull_requests: dict[int, dict[str, Any]],
    reviews: dict[int, dict[str, Any]],
    findings: dict[int, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    required = (
        "delivery_id",
        "delivery_guid",
        "delivered_at",
        "event",
        "action",
        "repository",
        "pr_number",
        "review_id",
        "finding_id",
        "thread_id",
        "actor",
    )
    result: dict[str, dict[str, Any]] = {}
    delivery_ids = []
    captured = parse_time(fixture["captured_at"], "fixture.captured_at")
    window = expect_object(fixture["window"], "fixture.window")
    window_start = parse_time(window["start"], "fixture.window.start")
    for index, raw in enumerate(fixture["review_thread_events"]):
        label = f"review_thread_events[{index}]"
        event = expect_object(raw, label)
        expect_keys(event, label, required)
        delivery_id = expect_int(event["delivery_id"], f"{label}.delivery_id", 1)
        delivery_ids.append(delivery_id)
        delivery_guid = event["delivery_guid"]
        if (
            not isinstance(delivery_guid, str)
            or DELIVERY_GUID_RE.fullmatch(delivery_guid) is None
        ):
            raise PilotDataError(
                f"{label}.delivery_guid must be a lowercase GitHub delivery UUID"
            )
        if delivery_guid in result:
            raise PilotDataError(
                f"duplicate review-thread delivery {delivery_guid!r}"
            )
        delivered_at = parse_time(event["delivered_at"], f"{label}.delivered_at")
        if delivered_at < window_start or delivered_at > captured:
            raise PilotDataError(
                f"{label}.delivered_at is outside the captured analysis window"
            )
        if event["event"] != "pull_request_review_thread":
            raise PilotDataError(
                f"{label}.event must be 'pull_request_review_thread'"
            )
        expect_enum(event["action"], {"resolved", "unresolved"}, f"{label}.action")
        repository = expect_string(event["repository"], f"{label}.repository")
        if repository != fixture["repository"]:
            raise PilotDataError(
                f"{label}.repository contradicts fixture.repository"
            )
        pr_number = expect_int(event["pr_number"], f"{label}.pr_number", 1)
        review_id = expect_int(event["review_id"], f"{label}.review_id", 1)
        finding_id = expect_int(event["finding_id"], f"{label}.finding_id", 1)
        review = reviews.get(review_id)
        finding = findings.get(finding_id)
        if pr_number not in pull_requests:
            raise PilotDataError(f"{label} references unknown PR {pr_number}")
        if review is None or review["pr_number"] != pr_number:
            raise PilotDataError(
                f"{label} references a missing or mismatched review"
            )
        if finding is None or finding["review_id"] != review_id:
            raise PilotDataError(
                f"{label} references a missing or mismatched finding"
            )
        thread_id = expect_string(event["thread_id"], f"{label}.thread_id")
        if finding["thread_id"] != thread_id:
            raise PilotDataError(f"{label}.thread_id contradicts its finding")
        created_at = parse_time(
            finding["created_at"], f"finding {finding_id}.created_at"
        )
        if delivered_at <= created_at:
            raise PilotDataError(
                f"{label}.delivered_at must follow finding creation"
            )
        expect_string(event["actor"], f"{label}.actor")
        result[delivery_guid] = event
    expect_unique(delivery_ids, "review-thread delivery IDs")
    if not source["complete"] and result:
        raise PilotDataError(
            "unavailable review-thread event source cannot contain deliveries"
        )
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
        if started is not None and started > captured:
            raise PilotDataError(f"{label}.started_at follows the snapshot")
        if completed is not None and started is None:
            raise PilotDataError(f"{label}.completed_at requires started_at")
        if completed is not None:
            duration_seconds(started, completed, label)
        if completed is not None and completed > captured:
            raise PilotDataError(f"{label}.completed_at follows the snapshot")
        if status == "completed":
            expect_enum(conclusion, RUN_CONCLUSIONS, f"{label}.conclusion")
            if started is None:
                raise PilotDataError(f"{label} completed status requires started_at")
            if completed is None:
                raise PilotDataError(f"{label} completed status requires completed_at")
        elif status == "in_progress":
            if started is None:
                raise PilotDataError(f"{label} in_progress status requires started_at")
            if conclusion is not None or completed is not None:
                raise PilotDataError(
                    f"{label} in_progress status requires null conclusion/completed_at"
                )
        else:
            if conclusion is not None or completed is not None:
                raise PilotDataError(
                    f"{label} queued status requires null conclusion/completed_at"
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
    review_thread_source: dict[str, Any],
    review_thread_events: dict[str, dict[str, Any]],
    commits: dict[str, dict[str, Any]],
    events: dict[str, dict[str, Any]],
    artifacts: dict[str, dict[str, Any]],
    edges: dict[str, dict[str, Any]],
) -> None:
    captured = parse_time(fixture["captured_at"], "fixture.captured_at")
    lifecycle_as_of = parse_time(
        fixture["lifecycle_as_of"], "fixture.lifecycle_as_of"
    )
    if review_thread_source["complete"]:
        coverage_start = parse_time(
            review_thread_source["coverage_start"],
            "fixture.review_thread_event_source.coverage_start",
        )
        coverage_end = parse_time(
            review_thread_source["coverage_end"],
            "fixture.review_thread_event_source.coverage_end",
        )
        if findings:
            first_finding = min(
                parse_time(
                    finding["created_at"],
                    f"finding {finding['id']}.created_at",
                )
                for finding in findings.values()
            )
            if coverage_start > first_finding:
                raise PilotDataError(
                    "review-thread event coverage starts after finding history"
                )
        if coverage_end < lifecycle_as_of:
            raise PilotDataError(
                "review-thread event coverage ends before lifecycle_as_of"
            )
    events_by_finding: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in review_thread_events.values():
        delivered_at = parse_time(
            event["delivered_at"],
            f"review-thread delivery {event['delivery_guid']}.delivered_at",
        )
        if delivered_at > lifecycle_as_of:
            raise PilotDataError(
                f"review-thread delivery {event['delivery_guid']!r} follows "
                "lifecycle_as_of"
            )
        events_by_finding[event["finding_id"]].append(event)
    for finding_id, thread_events in events_by_finding.items():
        ordered = sorted(
            thread_events,
            key=lambda event: (
                parse_time(
                    event["delivered_at"],
                    f"review-thread delivery {event['delivery_guid']}.delivered_at",
                ),
                event["delivery_id"],
            ),
        )
        timestamps = [
            parse_time(
                event["delivered_at"],
                f"review-thread delivery {event['delivery_guid']}.delivered_at",
            )
            for event in ordered
        ]
        expect_unique(
            timestamps,
            f"review finding {finding_id} delivery timestamps",
        )
        resolved = ordered[-1]["action"] == "resolved"
        if findings[finding_id]["is_resolved"] != resolved:
            raise PilotDataError(
                f"review finding {finding_id} current resolution state "
                "contradicts authoritative deliveries"
            )
    if review_thread_source["complete"]:
        for finding in findings.values():
            if finding["is_resolved"] and finding["id"] not in events_by_finding:
                raise PilotDataError(
                    f"review finding {finding['id']} is resolved without an "
                    "authoritative delivery"
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
    for review_id, review in reviews.items():
        pr_number = review["pr_number"]
        if pr_number not in pull_requests:
            raise PilotDataError(f"review {review_id} references unknown PR {pr_number}")
        pr = pull_requests[pr_number]
        submitted = parse_time(
            review["submitted_at"], f"review {review_id}.submitted_at"
        )
        pr_created = parse_time(
            pr["created_at"], f"pull request {pr_number}.created_at"
        )
        if submitted < pr_created:
            raise PilotDataError(
                f"review {review_id} precedes PR {pr_number} creation"
            )
        pr_closed = parse_time(
            pr["closed_at"],
            f"pull request {pr_number}.closed_at",
            nullable=True,
        )
        if pr_closed is not None and submitted > pr_closed:
            raise PilotDataError(
                f"review {review_id} follows PR {pr_number} closure"
            )
        if review_id not in pull_requests[pr_number]["review_ids"]:
            raise PilotDataError(
                f"review {review_id} is absent from PR {pr_number}'s identity list"
            )
        if review["commit_sha"] not in commits:
            raise PilotDataError(
                f"review {review_id} references missing commit {review['commit_sha']}"
            )
        if review["commit_sha"] not in pr["commit_shas"]:
            raise PilotDataError(
                f"review {review_id} commit is outside PR {pr_number} "
                "candidate history"
            )
        commit_time = parse_time(
            commits[review["commit_sha"]]["committed_at"],
            f"commit {review['commit_sha']}.committed_at",
        )
        if submitted < commit_time:
            raise PilotDataError(
                f"review {review_id} precedes its reviewed commit"
            )
        if set(review["thread_ids"]) != review_threads.get(review_id, set()):
            raise PilotDataError(f"review {review_id} thread identity list is incomplete")
    for finding_id, finding in findings.items():
        review = reviews[finding["review_id"]]
        pr = pull_requests[review["pr_number"]]
        created = parse_time(
            finding["created_at"], f"review finding {finding_id}.created_at"
        )
        pr_created = parse_time(
            pr["created_at"], f"pull request {pr['number']}.created_at"
        )
        commit_time = parse_time(
            commits[review["commit_sha"]]["committed_at"],
            f"commit {review['commit_sha']}.committed_at",
        )
        if created < pr_created:
            raise PilotDataError(
                f"review finding {finding_id} precedes PR {pr['number']} creation"
            )
        if created < commit_time:
            raise PilotDataError(
                f"review finding {finding_id} precedes its reviewed commit"
            )
        pr_closed = parse_time(
            pr["closed_at"],
            f"pull request {pr['number']}.closed_at",
            nullable=True,
        )
        if pr_closed is not None and created > pr_closed:
            raise PilotDataError(
                f"review finding {finding_id} follows PR {pr['number']} closure"
            )
    for delivery_guid, event in review_thread_events.items():
        review = reviews[event["review_id"]]
        pr = pull_requests[event["pr_number"]]
        delivered = parse_time(
            event["delivered_at"],
            f"review-thread delivery {delivery_guid}.delivered_at",
        )
        submitted = parse_time(
            review["submitted_at"], f"review {review['id']}.submitted_at"
        )
        commit_time = parse_time(
            commits[review["commit_sha"]]["committed_at"],
            f"commit {review['commit_sha']}.committed_at",
        )
        if delivered < submitted:
            raise PilotDataError(
                f"review-thread delivery {delivery_guid!r} precedes its review"
            )
        if delivered < commit_time:
            raise PilotDataError(
                f"review-thread delivery {delivery_guid!r} precedes its commit"
            )
        pr_closed = parse_time(
            pr["closed_at"],
            f"pull request {pr['number']}.closed_at",
            nullable=True,
        )
        if pr_closed is not None and delivered > pr_closed:
            raise PilotDataError(
                f"review-thread delivery {delivery_guid!r} follows PR "
                f"{pr['number']} closure"
            )
        if delivered > captured:
            raise PilotDataError(
                f"review-thread delivery {delivery_guid!r} follows the snapshot"
            )
    observed_candidates = observed_candidate_shas(
        pull_requests,
        reviews.values(),
        fixture["workflow_runs"],
        events.values(),
    )

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
            if (
                not is_ancestor(sha, pr["head_sha"], commits)
                and sha not in observed_candidates[pr_number]
            ):
                raise PilotDataError(
                    f"PR {pr_number} commit {sha} is neither in its current "
                    "head ancestry nor observed candidate history"
                )
    for run in fixture["workflow_runs"]:
        if run["head_sha"] not in commits:
            raise PilotDataError(
                f"workflow run {run['id']} references missing commit {run['head_sha']}"
            )
    phase_events_by_pr: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event_id, event in events.items():
        occurred_at = parse_time(
            event["occurred_at"], f"event {event_id}.occurred_at"
        )
        if occurred_at > lifecycle_as_of:
            raise PilotDataError(f"event {event_id!r} follows lifecycle_as_of")
        pr = None
        if "pr_number" in event:
            pr = pull_requests[event["pr_number"]]
            pr_created = parse_time(
                pr["created_at"],
                f"pull request {pr['number']}.created_at",
            )
            if occurred_at < pr_created:
                raise PilotDataError(
                    f"event {event_id!r} precedes PR {pr['number']} creation"
                )
            pr_closed = parse_time(
                pr["closed_at"],
                f"pull request {pr['number']}.closed_at",
                nullable=True,
            )
            if event["type"] in POST_MERGE_EVENT_TYPES:
                merged_at = parse_time(
                    pr["merged_at"],
                    f"pull request {pr['number']}.merged_at",
                    nullable=True,
                )
                if merged_at is None or occurred_at < merged_at:
                    raise PilotDataError(
                        f"event {event_id!r} requires PR {pr['number']} "
                        "merge availability"
                    )
            elif (
                event["type"] != "security_finding"
                and pr_closed is not None
                and occurred_at > pr_closed
            ):
                raise PilotDataError(
                    f"event {event_id!r} follows PR {pr['number']} closure"
                )
            if (
                event["type"] == "security_finding"
                and pr_closed is not None
                and occurred_at > pr_closed
                and pr["merged_at"] is None
            ):
                raise PilotDataError(
                    f"event {event_id!r} cannot follow an unmerged PR closure"
                )
            phase_events_by_pr[pr["number"]].append(event)
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
            committed_at = parse_time(
                commits[sha]["committed_at"],
                f"commit {sha}.committed_at",
            )
            if occurred_at < committed_at:
                raise PilotDataError(
                    f"event {event_id!r} {field} predates commit availability"
                )
            if pr is not None:
                candidate_history = set(pr["commit_shas"])
                if event["type"] in POST_MERGE_EVENT_TYPES:
                    in_history = (
                        pr["merge_sha"] is not None
                        and is_ancestor(pr["merge_sha"], sha, commits)
                        and is_ancestor(sha, fixture["base_sha"], commits)
                    )
                elif (
                    event["type"] == "security_finding"
                    and pr["merged_at"] is not None
                    and occurred_at
                    >= parse_time(
                        pr["merged_at"],
                        f"pull request {pr['number']}.merged_at",
                    )
                ):
                    in_history = (
                        pr["merge_sha"] is not None
                        and is_ancestor(pr["merge_sha"], sha, commits)
                        and is_ancestor(sha, fixture["base_sha"], commits)
                    )
                else:
                    in_history = sha in candidate_history
                if not in_history:
                    raise PilotDataError(
                        f"event {event_id!r} {field} is outside PR "
                        f"{pr['number']} causal history"
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

    for pr_number, pr_events in phase_events_by_pr.items():
        pr = pull_requests[pr_number]
        final_closed = parse_time(
            pr["closed_at"],
            f"pull request {pr_number}.closed_at",
            nullable=True,
        )
        is_open = True
        phase_order = {"closed": 1, "reopened": 2}
        for event in sorted(
            pr_events,
            key=lambda item: (
                parse_time(
                    item["occurred_at"],
                    f"event {item['id']}.occurred_at",
                ),
                phase_order.get(item["type"], 0),
                item["id"],
            ),
        ):
            event_type = event["type"]
            event_at = parse_time(
                event["occurred_at"],
                f"event {event['id']}.occurred_at",
            )
            if event_type in POST_MERGE_EVENT_TYPES or (
                event_type == "security_finding"
                and final_closed is not None
                and event_at > final_closed
            ):
                continue
            if event_type == "closed":
                if not is_open:
                    raise PilotDataError(
                        f"event {event['id']!r} closes an already closed PR"
                    )
                is_open = False
            elif event_type == "reopened":
                if is_open:
                    raise PilotDataError(
                        f"event {event['id']!r} reopens an already open PR"
                    )
                if final_closed is not None and event_at >= final_closed:
                    raise PilotDataError(
                        f"event {event['id']!r} cannot reopen at or after "
                        f"PR {pr_number} final closure"
                    )
                is_open = True
            elif (
                event_type in PR_OPEN_PHASE_EVENT_TYPES
                or event_type == "security_finding"
            ) and not is_open:
                raise PilotDataError(
                    f"event {event['id']!r} occurs while PR {pr_number} is closed"
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


def validate_fixture(
    fixture: Any,
    implementation_handoff_trust: Any | None = None,
    implementation_handoff_installation: Any | None = None,
) -> dict[str, Any]:
    fixture = expect_object(fixture, "fixture")
    _validate_fixture_root(fixture)
    pull_requests = validate_pull_requests(fixture)
    issues = validate_issues(fixture)
    reviews = validate_reviews(fixture)
    findings = validate_findings(fixture)
    review_thread_source = validate_review_thread_event_source(fixture)
    review_thread_events = validate_review_thread_events(
        fixture,
        review_thread_source,
        pull_requests,
        reviews,
        findings,
    )
    runs = validate_runs(fixture)
    commits = validate_commits(fixture)
    if fixture["base_sha"] not in commits:
        raise PilotDataError("fixture.base_sha has no commit record")
    events = validate_events(fixture, pull_requests)
    artifacts = validate_artifacts(fixture)
    edges = validate_edges(fixture)
    implementation_handoffs = validate_implementation_handoffs(
        fixture,
        implementation_handoff_trust=implementation_handoff_trust,
        implementation_handoff_installation=implementation_handoff_installation,
    )
    cross_validate_fixture(
        fixture,
        pull_requests,
        issues,
        reviews,
        findings,
        review_thread_source,
        review_thread_events,
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
        "review_thread_source": review_thread_source,
        "review_thread_events": review_thread_events,
        "runs": runs,
        "commits": commits,
        "events": events,
        "artifacts": artifacts,
        "edges": edges,
        "implementation_handoff_bundles": implementation_handoffs["bundles"],
        "implementation_handoffs": implementation_handoffs["handoffs"],
    }


def validate_decisions(
    raw_decisions: Any,
    data: dict[str, Any],
    repository_root: Path | None = None,
) -> dict[str, Any]:
    decisions = expect_object(raw_decisions, "decisions")
    expect_keys(decisions, "decisions", ("schema_version", "pull_requests", "artifacts"))
    schema_version = expect_int(
        decisions["schema_version"],
        "decisions.schema_version",
        1,
    )
    if schema_version != SCHEMA_VERSION:
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
            reviews,
            key=lambda review: parse_time(
                review["submitted_at"],
                f"review {review['id']}.submitted_at",
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
            if repository_root is None:
                raise PilotDataError(
                    f"PR {number} threshold override provenance requires an "
                    "explicit repository root"
                )
            validate_override_git_provenance(
                repository_root,
                data,
                number,
                override_index,
                override,
                introduction,
                first_review,
            )

        expect_enum(record["gate_mode"], GATE_MODES, f"{label}.gate_mode")
        stack = expect_object(record["stack"], f"{label}.stack")
        expect_keys(stack, f"{label}.stack", ("depth", "parent_pr", "exception_reason"))
        expect_int(stack["depth"], f"{label}.stack.depth", 0)
        if stack["parent_pr"] is not None:
            expect_int(stack["parent_pr"], f"{label}.stack.parent_pr", 1)
        if stack["exception_reason"] is not None:
            expect_string(
                stack["exception_reason"], f"{label}.stack.exception_reason"
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

    validate_stack_decisions(pr_decisions, data)

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


def validate_stack_decisions(
    pr_decisions: dict[int, dict[str, Any]],
    data: dict[str, Any],
) -> None:
    default_branch = data["fixture"]["default_branch"]
    parents: dict[int, int] = {}

    for number, record in pr_decisions.items():
        label = f"PR decision {number}.stack"
        stack = record["stack"]
        depth = stack["depth"]
        parent = stack["parent_pr"]
        exception_reason = stack["exception_reason"]
        authoritative = data["pull_requests"][number]
        is_root = authoritative["base_ref"] == default_branch

        if depth > 3:
            raise PilotDataError(f"{label}.depth exceeds the supported maximum")
        if parent is None:
            if depth != 0 or exception_reason is not None:
                raise PilotDataError(
                    f"{label} root must have depth 0 and no exception"
                )
            if not is_root:
                raise PilotDataError(
                    f"{label} root contradicts the authoritative PR base"
                )
            continue

        if depth == 0:
            raise PilotDataError(f"{label} root cannot name a parent")
        if is_root:
            raise PilotDataError(
                f"{label} parent contradicts the authoritative PR base"
            )
        if parent == number:
            raise PilotDataError(f"{label} cannot name itself as parent")
        if parent not in data["pull_requests"]:
            raise PilotDataError(f"{label}.parent_pr has no authoritative PR")
        if parent not in pr_decisions:
            raise PilotDataError(f"{label}.parent_pr has no parent decision")
        expected_base = data["pull_requests"][parent]["head_branch"]
        if authoritative["base_ref"] != expected_base:
            raise PilotDataError(
                f"{label} parent contradicts the authoritative PR base"
            )
        if depth == 3:
            expect_string(exception_reason, f"{label}.exception_reason")
        elif exception_reason is not None:
            raise PilotDataError(
                f"{label}.exception_reason is only valid at depth three"
            )
        parents[number] = parent

    states: dict[int, str] = {}

    def visit(number: int) -> None:
        state = states.get(number)
        if state == "visiting":
            raise PilotDataError("stack decisions contain a parent cycle")
        if state == "visited":
            return
        states[number] = "visiting"
        parent = parents.get(number)
        if parent is not None:
            visit(parent)
            expected_depth = pr_decisions[parent]["stack"]["depth"] + 1
            if pr_decisions[number]["stack"]["depth"] != expected_depth:
                raise PilotDataError(
                    f"PR decision {number}.stack.depth must equal parent "
                    f"depth + 1 ({expected_depth})"
                )
        states[number] = "visited"

    for number in pr_decisions:
        visit(number)


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
        current_disposition = decision["history"][-1]["disposition"]
        required_semantic_result = (
            "pass" if current_disposition == "Delete" else "fail"
        )
        required_reason = proofs[0]["reason"]
        disposition_at = parse_time(
            decision["history"][-1]["recorded_at"],
            f"artifact decision {artifact_id}.current recorded_at",
        )
        for proof in proofs:
            proof_at = parse_time(
                proof["occurred_at"],
                f"event {proof['id']}.occurred_at",
            )
            if proof["semantic_result"] != required_semantic_result:
                raise PilotDataError(
                    f"artifact {artifact_id!r} deletion proof "
                    f"{proof['id']!r} contradicts current disposition "
                    f"{current_disposition!r}"
                )
            if proof["reason"] != required_reason:
                raise PilotDataError(
                    f"artifact {artifact_id!r} deletion proof "
                    f"{proof['id']!r} has a mixed semantic reason"
                )
            if proof["restored_result"] != "pass":
                raise PilotDataError(
                    f"artifact {artifact_id!r} deletion proof "
                    f"{proof['id']!r} did not restore"
                )
            if disposition_at <= proof_at:
                raise PilotDataError(
                    f"artifact {artifact_id!r} current disposition must "
                    "strictly follow every deletion proof"
                )


def _run_deletion_proof_check(
    artifact_root: Path,
    authority_root: Path,
    check_id: str,
) -> subprocess.CompletedProcess[bytes]:
    if check_id not in {"workflow-pilot-reporter", "workflow-pilot-tests"}:
        raise PilotDataError(
            f"deletion-proof check {check_id!r} is not allowlisted"
        )
    command = (
        "/usr/bin/python3",
        "-I",
        str(artifact_root / ISOLATED_LAUNCHER_PATH),
        "lifecycle-check",
        "--artifact-root",
        str(artifact_root),
        "--authority-root",
        str(authority_root),
        "--check",
        check_id,
    )
    environment = {"LC_ALL": "C", "PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0"}
    try:
        return subprocess.run(
            command,
            cwd=artifact_root,
            env=environment,
            check=False,
            capture_output=True,
            timeout=DELETION_PROOF_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PilotDataError(
            f"cannot execute allowlisted deletion-proof check: {error}"
        ) from error


def validate_executable_deletion_proofs(
    repository_root: Path,
    fixture_path: Path,
    decisions_path: Path,
    expected_path: Path | None,
    fixture: dict[str, Any],
    decisions: dict[str, Any],
) -> dict[str, dict[str, str]]:
    repository_root = validate_repository_root(repository_root)
    data = validate_fixture(fixture)
    validate_repository_authority(repository_root, data)
    required_inputs = {
        "fixture": (fixture_path, BASELINE_FIXTURE_PATH),
        "decisions": (decisions_path, DECISION_RECORD_PATH),
        "expected": (expected_path, BASELINE_EXPECTED_PATH),
    }
    for label, (actual, relative) in required_inputs.items():
        if actual is None:
            raise PilotDataError(
                f"--{label} is required for executable deletion proofs"
            )
        try:
            resolved = actual.resolve(strict=True)
            required = (repository_root / relative).resolve(strict=True)
        except OSError as error:
            raise PilotDataError(
                f"cannot resolve executable deletion-proof {label}: {error}"
            ) from error
        if resolved != required:
            raise PilotDataError(
                f"--{label} must identify {required} for executable deletion proofs"
            )

    artifact_records = {
        artifact["id"]: artifact for artifact in fixture["artifacts"]
    }
    decision_records = {
        decision["artifact_id"]: decision for decision in decisions["artifacts"]
    }
    allowlisted_ids = set(EXECUTABLE_DELETION_PROOFS)
    if set(artifact_records) != allowlisted_ids or set(decision_records) != allowlisted_ids:
        raise PilotDataError(
            "executable deletion proofs require the exact allowlisted artifact set"
        )

    for artifact_id, profile in EXECUTABLE_DELETION_PROOFS.items():
        artifact = artifact_records[artifact_id]
        decision = decision_records[artifact_id]
        if Path(artifact["path"]) != profile["path"]:
            raise PilotDataError(
                f"artifact {artifact_id!r} path differs from its executable allowlist"
            )
        if decision["executable_consumer"] != profile["consumer"]:
            raise PilotDataError(
                f"artifact {artifact_id!r} consumer differs from its executable allowlist"
            )
        if decision["consistency_check"] != profile["check"]:
            raise PilotDataError(
                f"artifact {artifact_id!r} check differs from its executable allowlist"
            )
        proofs = [
            event
            for event in fixture["events"]
            if event["type"] == "deletion_proof"
            and event["artifact_id"] == artifact_id
        ]
        for proof in proofs:
            if (
                proof["semantic_result"] != "fail"
                or proof["reason"] != DELETION_PROOF_REASON
                or proof["restored_result"] != "pass"
            ):
                raise PilotDataError(
                    f"artifact {artifact_id!r} proof {proof['id']!r} differs "
                    "from its executable fail/remove/restore contract"
                )

    copy_paths = {
        *DELETION_PROOF_SUPPORT_PATHS,
        *(profile["path"] for profile in EXECUTABLE_DELETION_PROOFS.values()),
    }
    try:
        sandbox_parent = repository_root / "build" / "test-artifacts"
        sandbox_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{repository_root.name}-workflow-pilot-proof-",
            dir=sandbox_parent,
        ) as temporary:
            sandbox = Path(temporary)
            for relative in copy_paths:
                source = repository_root / relative
                target = sandbox / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

            for check_id in ("workflow-pilot-reporter", "workflow-pilot-tests"):
                initial = _run_deletion_proof_check(
                    sandbox,
                    repository_root,
                    check_id,
                )
                if initial.returncode != 0:
                    detail = initial.stderr.decode(
                        "utf-8", errors="replace"
                    ).strip()
                    raise PilotDataError(
                        "stale executable deletion-proof baseline does not pass"
                        + (f": {detail}" if detail else "")
                    )

            results: dict[str, dict[str, str]] = {}
            backup_root = sandbox / ".workflow-pilot-proof-backups"
            backup_root.mkdir()
            for artifact_id, profile in EXECUTABLE_DELETION_PROOFS.items():
                relative = profile["path"]
                artifact_path = sandbox / relative
                backup_path = backup_root / artifact_id
                artifact_path.replace(backup_path)
                check_ids = tuple(
                    dict.fromkeys((profile["consumer"], profile["check"]))
                )
                removed = [
                    _run_deletion_proof_check(
                        sandbox,
                        repository_root,
                        check_id,
                    )
                    for check_id in check_ids
                ]
                backup_path.replace(artifact_path)

                if any(completed.returncode == 0 for completed in removed):
                    raise PilotDataError(
                        f"artifact {artifact_id!r} removal did not fail for "
                        "its allowlisted semantic contract"
                    )
                restored = [
                    _run_deletion_proof_check(
                        sandbox,
                        repository_root,
                        check_id,
                    )
                    for check_id in check_ids
                ]
                failed_restorations = [
                    completed
                    for completed in restored
                    if completed.returncode != 0
                ]
                if failed_restorations:
                    detail = failed_restorations[0].stderr.decode(
                        "utf-8", errors="replace"
                    ).strip()
                    raise PilotDataError(
                        f"artifact {artifact_id!r} restoration did not pass"
                        + (f": {detail}" if detail else "")
                    )
                results[artifact_id] = {
                    "removal": "fail",
                    "reason": DELETION_PROOF_REASON,
                    "restoration": "pass",
                }
            return results
    except OSError as error:
        raise PilotDataError(
            f"cannot prepare temporary deletion-proof artifact sandbox: {error}"
        ) from error


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


def finding_resolved_before(
    finding_id: int,
    boundary: datetime,
    events_by_finding: dict[int, list[dict[str, Any]]],
) -> bool:
    events = sorted(
        (
            event
            for event in events_by_finding[finding_id]
            if parse_time(
                event["delivered_at"],
                f"review-thread delivery {event['delivery_guid']}.delivered_at",
            )
            < boundary
        ),
        key=lambda event: (
            parse_time(
                event["delivered_at"],
                f"review-thread delivery {event['delivery_guid']}.delivered_at",
            ),
            event["delivery_id"],
        ),
    )
    return bool(events) and events[-1]["action"] == "resolved"


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
    clean_review_unavailable_reason = None
    findings_by_review: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for finding in data["findings"].values():
        findings_by_review[finding["review_id"]].append(finding)
    thread_events_by_finding: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in data["review_thread_events"].values():
        thread_events_by_finding[event["finding_id"]].append(event)
    runs_by_branch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in data["runs"].values():
        runs_by_branch[run["head_branch"]].append(run)
    first_push_subjects = [data["pull_requests"][fixture["spotlight_pr"]]]
    for pr in first_push_subjects:
        if not data["review_thread_source"]["complete"]:
            clean_review_unavailable_reason = data["review_thread_source"][
                "unavailable_reason"
            ]
            continue
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
            clean_review_unavailable_reason = "missing-first-push-or-review-evidence"
            continue
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
                    finding_resolved_before(
                        finding["id"],
                        review_at,
                        thread_events_by_finding,
                    )
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
            clean_review_unavailable_reason = (
                "no-authoritative-clean-review-boundary"
            )

    clean_review_available = (
        clean_review_unavailable_reason is None
        and len(first_push_durations) == len(first_push_subjects)
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
            "status": "available" if clean_review_available else "unavailable",
            "reason": None
            if clean_review_available
            else clean_review_unavailable_reason,
            "pilot_ready": clean_review_available,
            "eligible_pull_requests": len(first_push_durations),
            "excluded_without_complete_evidence": len(first_push_subjects)
            - len(first_push_durations),
            "median_hours": median_tenth(first_push_durations)
            if clean_review_available
            else None,
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
    ]
    current_resolved = sum(finding["is_resolved"] for finding in findings)
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
        "current_resolved_findings": current_resolved,
        "current_unresolved_findings": len(findings) - current_resolved,
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
        "reverts": data["repository_authority"]["reverts"],
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


def report_implementation_handoffs(
    data: dict[str, Any]
) -> dict[str, int | list[str]]:
    handoffs = list(data["implementation_handoffs"].values())
    lifecycle_as_of = parse_time(
        data["fixture"]["lifecycle_as_of"],
        "fixture.lifecycle_as_of",
    )
    lifetimes = []
    for item in handoffs:
        assigned_at = parse_time(
            item["assigned_at"],
            f"implementation handoff {item['id']}.assigned_at",
        )
        closed_at = parse_time(
            item["closed_at"],
            f"implementation handoff {item['id']}.closed_at",
            nullable=True,
        )
        elapsed = duration_seconds(
            assigned_at,
            closed_at or lifecycle_as_of,
            f"implementation handoff {item['id']} lifetime",
        )
        if elapsed != elapsed.to_integral_value():
            raise PilotDataError(
                f"implementation handoff {item['id']!r} lifetime "
                "must resolve to whole seconds"
            )
        lifetimes.append(int(elapsed))
    rejection_codes = sorted(
        {
            code
            for item in handoffs
            for code in [
                *item["rejection_codes"],
                *item["bundle_rejection_codes"],
            ]
        }
    )
    return {
        "records": len(handoffs),
        "accepted": sum(
            item["reported_outcome"] == "accepted" for item in handoffs
        ),
        "bundle_rejected": sum(
            item["reported_outcome"] == "bundle_rejected"
            for item in handoffs
        ),
        "rejected": sum(
            item["reported_outcome"] == "rejected" for item in handoffs
        ),
        "interrupted": sum(
            item["reported_outcome"] == "interrupted" for item in handoffs
        ),
        "in_progress": sum(
            item["reported_outcome"] == "in_progress" for item in handoffs
        ),
        "stale_responses": sum(
            "stale-result" in item["rejection_codes"] for item in handoffs
        ),
        "max_lifetime_seconds": max(lifetimes, default=0),
        "max_peak_rss_bytes": max(
            (item["peak_rss_bytes"] for item in handoffs),
            default=0,
        ),
        "coordination_turns": sum(
            item["coordination_turns"] for item in handoffs
        ),
        "recovery_minutes": sum(
            item["recovery_minutes"] for item in handoffs
        ),
        "rejection_codes": rejection_codes,
    }


def report_classifications(data: dict[str, Any]) -> list[dict[str, Any]]:
    runs_by_pr: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for run in data["runs"].values():
        pr = pull_request_for_run(run, data["pull_requests"])
        if pr is not None:
            runs_by_pr[pr["number"]].append(run)
    reverted_shas = {
        relation["reverts"]
        for relation in data["repository_authority"]["reverts"]
    }
    result = []
    for number, pr in sorted(data["pull_requests"].items()):
        lines = pr["additions"] + pr["deletions"]
        pr_runs = runs_by_pr[number]
        branch_shas = {run["head_sha"] for run in pr_runs}
        flags = []
        if len(branch_shas) > 1:
            flags.append("superseded")
        if any(run["status"] in {"queued", "in_progress"} for run in pr_runs):
            flags.append("still-running")
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


def report_classification_summary(
    classifications: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    work_states = {"cancelled": 0, "merged": 0, "still_running": 0}
    flags = {
        "bulk_deletion": 0,
        "generated_only": 0,
        "reverted": 0,
        "stacked": 0,
        "still_running": 0,
        "superseded": 0,
    }
    for classification in classifications:
        work_states[classification["work_state"].replace("-", "_")] += 1
        for flag in classification["flags"]:
            flags[flag.replace("-", "_")] += 1
    return {"work_states": work_states, "flags": flags}


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


IDENTITY_SEAL_DOMAIN = b"workflow-pilot-cohort-relationships-v2\0"
COMPUTED_RESULT_SEAL_DOMAIN = b"workflow-pilot-computed-results-v1\0"
DECISION_SEAL_DOMAIN = b"workflow-pilot-nonderivable-decisions-v1\0"


def _sealed_records(
    records: dict[Any, dict[str, Any]],
    unordered_fields: Iterable[str] = (),
) -> list[dict[str, Any]]:
    result = []
    for identity in sorted(records):
        record = dict(records[identity])
        for field in unordered_fields:
            if field in record:
                record[field] = sorted(record[field])
        result.append({"identity": identity, "record": record})
    return result


def cohort_identity_seal(data: dict[str, Any]) -> str:
    fixture = data["fixture"]
    cohort = {
        "artifacts": _sealed_records(data["artifacts"], ("dependency_ids",)),
        "commits": sorted(data["commits"]),
        "dependency_edges": _sealed_records(data["edges"]),
        "events": _sealed_records(data["events"]),
        "findings": _sealed_records(data["findings"]),
        "issues": _sealed_records(data["issues"]),
        "pull_requests": _sealed_records(
            data["pull_requests"],
            ("commit_shas", "files", "issue_numbers", "review_ids"),
        ),
        "review_thread_events": _sealed_records(data["review_thread_events"]),
        "review_thread_source": data["review_thread_source"],
        "reviews": _sealed_records(data["reviews"], ("thread_ids",)),
        "snapshot": {
            "base_sha": fixture["base_sha"],
            "build_workflow": fixture["build_workflow"],
            "captured_at": fixture["captured_at"],
            "default_branch": fixture["default_branch"],
            "lifecycle_as_of": fixture["lifecycle_as_of"],
            "repository": fixture["repository"],
            "spotlight_pr": fixture["spotlight_pr"],
            "window": fixture["window"],
            "workflow_sample_size": fixture["workflow_sample_size"],
        },
        "workflow_runs": _sealed_records(data["runs"]),
    }
    if data["fixture"]["schema_version"] == HANDOFF_FIXTURE_SCHEMA_VERSION:
        cohort["implementation_handoffs"] = _sealed_records(
            data["implementation_handoff_bundles"],
        )
    return hashlib.sha256(
        IDENTITY_SEAL_DOMAIN + normalized_json(cohort)
    ).hexdigest()


def computed_result_seal(result: dict[str, Any]) -> str:
    return hashlib.sha256(
        COMPUTED_RESULT_SEAL_DOMAIN + normalized_json(result)
    ).hexdigest()


def decision_semantics_seal(
    data: dict[str, Any],
    decisions: dict[str, Any],
) -> str:
    pull_requests = []
    for number, record in sorted(decisions["pull_requests"].items()):
        reviews = sorted(
            (
                review
                for review in data["reviews"].values()
                if review["pr_number"] == number
                and review["author"] == REVIEW_BOT
            ),
            key=lambda review: (
                parse_time(
                    review["submitted_at"],
                    f"review {review['id']}.submitted_at",
                ),
                review["id"],
            ),
        )
        introductions = sorted(
            (
                event
                for event in data["events"].values()
                if event["type"] == "threshold_override_introduced"
                and event["pr_number"] == number
            ),
            key=lambda event: (event["override_index"], event["id"]),
        )
        decision = copy.deepcopy(record)
        decision["risk_boundaries"] = sorted(decision["risk_boundaries"])
        decision["threshold"]["triggers"] = sorted(
            decision["threshold"]["triggers"]
        )
        pull_requests.append(
            {
                "decision": decision,
                "first_review_boundary": reviews[0] if reviews else None,
                "override_provenance": introductions,
            }
        )

    artifacts = []
    for artifact_id, record in sorted(decisions["artifacts"].items()):
        source = copy.deepcopy(data["artifacts"][artifact_id])
        source["dependency_ids"] = sorted(source["dependency_ids"])
        associations = sorted(
            (
                data["edges"][edge_id]
                for edge_id in source["dependency_ids"]
            ),
            key=lambda edge: edge["id"],
        )
        lifecycle_events = sorted(
            (
                event
                for event in data["events"].values()
                if event.get("artifact_id") == artifact_id
            ),
            key=lambda event: (
                parse_time(
                    event["occurred_at"],
                    f"event {event['id']}.occurred_at",
                ),
                event["id"],
            ),
        )
        artifacts.append(
            {
                "admission_and_disposition": record,
                "authoritative_source": source,
                "delete_when": {
                    "criterion": record["deletion_criterion"],
                    "expires_at": record["expires_at"],
                },
                "dependency_associations": associations,
                "verify_deletion": lifecycle_events,
            }
        )

    review_edges = sorted(
        (
            edge
            for edge in data["edges"].values()
            if edge["type"] == "review_depends_on"
        ),
        key=lambda edge: edge["id"],
    )
    review_events = sorted(
        (
            event
            for event in data["events"].values()
            if event["type"] == "dependency_changed"
        ),
        key=lambda event: (
            parse_time(
                event["occurred_at"],
                f"event {event['id']}.occurred_at",
            ),
            event["id"],
        ),
    )
    payload = {
        "schema_version": decisions["raw"]["schema_version"],
        "pull_requests": pull_requests,
        "artifacts": artifacts,
        "review_boundary": {
            "lifecycle_as_of": data["fixture"]["lifecycle_as_of"],
            "dependency_edges": review_edges,
            "dependency_events": review_events,
        },
    }
    return hashlib.sha256(
        DECISION_SEAL_DOMAIN + normalized_json(payload)
    ).hexdigest()


def build_report(
    fixture: Any,
    raw_decisions: Any,
    repository_root: Path | None = None,
    implementation_handoff_trust: Any | None = None,
    implementation_handoff_installation: Any | None = None,
) -> dict[str, Any]:
    if repository_root is None:
        raise PilotDataError(
            "report construction requires an explicit repository authority root"
        )
    repository_root = validate_repository_root(repository_root)
    fixture = expect_object(fixture, "fixture")
    if (
        fixture.get("schema_version") == HANDOFF_FIXTURE_SCHEMA_VERSION
        and implementation_handoff_installation is not None
    ):
        from scripts.workflow_pilot import agent_handoff

        try:
            implementation_handoff_installation = agent_handoff._coerce_reporter_trusted_installation(  # noqa: SLF001
                implementation_handoff_installation,
                repository_root=repository_root,
                label="implementation_handoff_installation",
            )
        except agent_handoff.HandoffDataError as error:
            raise PilotDataError(str(error)) from error
    data = validate_fixture(
        fixture,
        implementation_handoff_trust=implementation_handoff_trust,
        implementation_handoff_installation=implementation_handoff_installation,
    )
    data["repository_authority"] = validate_repository_authority(
        repository_root,
        data,
    )
    if data["fixture"]["schema_version"] == HANDOFF_FIXTURE_SCHEMA_VERSION:
        implementation_handoffs = validate_implementation_handoffs(
            data["fixture"],
            repository_root,
            implementation_handoff_trust,
            implementation_handoff_installation,
        )
        data["implementation_handoff_bundles"] = implementation_handoffs[
            "bundles"
        ]
        data["implementation_handoffs"] = implementation_handoffs["handoffs"]
    decisions = validate_decisions(raw_decisions, data, repository_root)
    workflow_sample(data)
    classifications = report_classifications(data)
    computed = {
        "delivery": report_delivery(data),
        "reviews": report_reviews(data),
        "builds": report_builds(data),
        "events": report_events(data),
        "efficiency": report_efficiency(data),
        "classifications": classifications,
        "classification_summary": report_classification_summary(
            classifications
        ),
        "artifacts": report_artifacts(data, decisions),
    }
    if data["fixture"]["schema_version"] == HANDOFF_FIXTURE_SCHEMA_VERSION:
        computed["implementation_handoffs"] = report_implementation_handoffs(
            data
        )
    identities = {
        "pull_requests": sorted(data["pull_requests"]),
        "issues": sorted(data["issues"]),
        "reviews": sorted(data["reviews"]),
        "findings": sorted(data["findings"]),
        "review_thread_deliveries": sorted(data["review_thread_events"]),
        "workflow_runs": sorted(data["runs"]),
        "commits": sorted(data["commits"]),
        "seal": cohort_identity_seal(data),
    }
    if data["fixture"]["schema_version"] == HANDOFF_FIXTURE_SCHEMA_VERSION:
        identities["implementation_handoffs"] = sorted(
            data["implementation_handoffs"]
        )
    return {
        "schema_version": data["fixture"]["schema_version"],
        "snapshot": {
            "repository": data["fixture"]["repository"],
            "base_sha": data["fixture"]["base_sha"],
            "captured_at": data["fixture"]["captured_at"],
            "lifecycle_as_of": data["fixture"]["lifecycle_as_of"],
            "window": data["fixture"]["window"],
        },
        "identities": identities,
        "decisions": {
            "seal": decision_semantics_seal(data, decisions),
        },
        **computed,
        "computed": {"seal": computed_result_seal(computed)},
    }


def check_expected(report: dict[str, Any], expected: Any) -> None:
    expected = expect_object(expected, "expected")
    expect_keys(expected, "expected", ("schema_version", "paths"))
    schema_version = expect_int(
        expected["schema_version"],
        "expected.schema_version",
        1,
    )
    if schema_version != SCHEMA_VERSION:
        raise PilotDataError(f"expected schema_version must be {SCHEMA_VERSION}")
    paths = expect_object(expected["paths"], "expected.paths")
    for path in paths:
        if (
            not isinstance(path, str)
            or EXPECTED_PATH_RE.fullmatch(path) is None
        ):
            raise PilotDataError(
                f"expected path {path!r} is malformed"
            )
    missing = sorted(EXPECTED_RESULT_PATHS - set(paths))
    extra = sorted(set(paths) - EXPECTED_RESULT_PATHS)
    if missing or extra:
        raise PilotDataError(
            "expected.paths must exactly match the frozen result contract "
            f"(missing={missing}, extra={extra})"
        )
    seal = paths["identities.seal"]
    if not isinstance(seal, str) or SHA256_RE.fullmatch(seal) is None:
        raise PilotDataError(
            "expected identities.seal must be a lowercase SHA-256"
        )
    for path, wanted in sorted(paths.items()):
        expect_string(path, "expected.paths key")
        value: Any = report
        for component in path.split("."):
            if not isinstance(value, dict) or component not in value:
                raise PilotDataError(f"expected path {path!r} does not exist")
            value = value[component]
        if type(value) is int:
            expect_int(wanted, f"expected path {path!r}")
        elif type(wanted) is not type(value):
            raise PilotDataError(
                f"expected path {path!r} value must have type "
                f"{type(value).__name__}"
            )
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
    parser.add_argument(
        "--expected",
        type=Path,
        help=(
            "required frozen expected values for schema version 1; version 2 "
            "operational handoff fixtures are sealed and reported directly"
        ),
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        required=True,
        help=(
            "exact checked-out Git top level whose origin and object database "
            "authorize every fixture Git fact"
        ),
    )
    parser.add_argument(
        "--implementation-handoff-trust",
        type=Path,
        help=(
            "schema version 2 only: signed trusted anchor attestations keyed "
            "by input_seal, stored outside the handoff fixture"
        ),
    )
    parser.add_argument(
        "--implementation-handoff-installation",
        type=Path,
        help=(
            "schema version 2 only: external trusted coordinator "
            "installation root that authenticates the trust sidecar"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        repository_root = validate_repository_root(args.repository_root)
        expected_decisions = (repository_root / DECISION_RECORD_PATH).resolve()
        if args.decisions.resolve() != expected_decisions:
            raise PilotDataError(
                f"--decisions must identify {expected_decisions}"
            )
        fixture = load_json(args.fixture)
        decisions = load_json(args.decisions)
        handoff_trust = None
        handoff_installation = None
        if fixture.get("schema_version") == HANDOFF_FIXTURE_SCHEMA_VERSION:
            if (
                args.implementation_handoff_trust is None
                or args.implementation_handoff_installation is None
            ):
                raise PilotDataError(
                    "schema version 2 requires --implementation-handoff-trust and "
                    "--implementation-handoff-installation"
                )
            handoff_trust = load_json(
                args.implementation_handoff_trust,
                label="implementation handoff trust sidecar",
                max_bytes=TRUSTED_JSON_MAX_BYTES,
            )
            handoff_installation = args.implementation_handoff_installation
        elif (
            args.implementation_handoff_trust is not None
            or args.implementation_handoff_installation is not None
        ):
            raise PilotDataError(
                "--implementation-handoff-* are reserved for schema version 2"
            )
        report = build_report(
            fixture,
            decisions,
            repository_root,
            implementation_handoff_trust=handoff_trust,
            implementation_handoff_installation=handoff_installation,
        )
        if fixture.get("schema_version") == SCHEMA_VERSION:
            if args.expected is None:
                raise PilotDataError(
                    "--expected is required for frozen schema version 1"
                )
            check_expected(report, load_json(args.expected))
            validate_executable_deletion_proofs(
                repository_root,
                args.fixture,
                args.decisions,
                args.expected,
                fixture,
                decisions,
            )
        elif args.expected is not None:
            raise PilotDataError(
                "--expected is reserved for frozen schema version 1"
            )
    except PilotDataError as error:
        print(f"workflow-pilot: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(normalized_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
