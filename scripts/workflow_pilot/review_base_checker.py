#!/usr/bin/env python3
"""Base-owned closed assertion registry for independent review evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = 2
REGISTRY_VERSION = 1
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
LOCAL_FINDING_RE = re.compile(r"^LOCAL-[A-Z0-9][A-Z0-9_-]{0,95}$")
ACTOR_SUFFIX_RE = re.compile(r"(?:\[bot\]|[-_]bot)$", re.IGNORECASE)
ACTION_SEQUENCE = ("read-candidate", "emit-local-report")
FAMILY_MEMBERS = {
    "action": ("actions", "items", "targets"),
    "generated": ("owners", "outputs", "consumers", "drift-checks"),
    "lifecycle": ("entries", "preservation", "resets", "terminals"),
    "resource": ("enabled", "disabled"),
    "wire": ("producers", "consumers", "validators", "replay", "stale-bindings"),
}
MEMBERS_WITHOUT_VERIFIED_UNAFFECTED = set()
REGISTERED_NOT_APPLICABLE_REASONS = {
    ("resource", "disabled"): "feature-disabled-by-contract"
}
CHECKER_ARGV = (
    "/usr/bin/python3",
    "-I",
    "review_base_checker.py",
    "--input",
    "checker-input.json",
)
GIT = "/usr/bin/git"
CHECKER_RELPATH = "scripts/workflow_pilot/review_base_checker.py"
ASSERTION_PROGRAM_RELPATH = "scripts/workflow_pilot/review_assertions.py"
ASSERTION_PROGRAM_ARGV = (
    "/usr/bin/python3",
    "-I",
    "review_assertions.py",
    "--stdin",
)
ASSERTION_FILE_MODES = {"100644", "100755", "120000"}
MATERIALIZED_FILE_MODES = {"100644", "100755"}
ASSERTION_INPUT_PATHS = (
    ".github/workflow-pilot-decisions.json",
    ".github/workflows/build.yml",
    ".github/skills/development-workflow/SKILL.md",
    "docs/test-cases/registry.json",
    "docs/test-cases/workflow-governance.md",
    "docs/workflow-pilot.md",
    "scripts/__init__.py",
    "scripts/check_docs.py",
    "scripts/docs_check_tests/__init__.py",
    "scripts/docs_check_tests/test_check_docs.py",
    "scripts/docs_check_tests/test_development_workflow_skill.py",
    "scripts/workflow_pilot/__init__.py",
    "scripts/workflow_pilot/candidate_evidence.py",
    "scripts/workflow_pilot/event_classifier.py",
    "scripts/workflow_pilot/hydrate_authority.py",
    "scripts/workflow_pilot/metadata_adapter_contract.py",
    "scripts/workflow_pilot/review_assertions.py",
    "scripts/workflow_pilot/review_base_checker.py",
    "scripts/workflow_pilot/review_family.py",
    "scripts/workflow_pilot/reporter.py",
    "scripts/workflow_pilot/summary_continuity_contract.py",
    "scripts/workflow_pilot/tests/fixtures/event_classification.json",
    "scripts/workflow_pilot/trusted_review_gate.py",
    "tests/__init__.py",
    "tests/workflows/__init__.py",
    "tests/workflows/test_build_ci_topology.py",
)
BEHAVIOR_ROWS = {
    "actor-permission-bounds",
    "authority-causality",
    "remote-review-metrics",
    "round-lifecycle",
    "sibling-family-expansion",
}
EVIDENCE_CLASSES = {"positive", "adversarial", "default", "runtime"}
REMOTE_REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}
REMOTE_REVIEW_OUTCOMES = {"clean", "changes-requested"}


class CheckError(Exception):
    pass


def object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CheckError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def normalized_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def parse_json_bytes(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_no_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CheckError(f"invalid JSON in {label}: {error}") from error


def expect_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckError(f"{label} must be an object")
    return value


def expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise CheckError(f"{label} must be a list")
    return value


def expect_string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise CheckError(f"{label} must be a nonempty string")
    return value


def expect_int(value: Any, label: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheckError(f"{label} must be an integer")
    if value < minimum:
        raise CheckError(f"{label} must be at least {minimum}")
    return value


def expect_keys(value: dict[str, Any], label: str, required, optional=()) -> None:
    required = set(required)
    allowed = required | set(optional)
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise CheckError(f"{label} is missing fields: {', '.join(missing)}")
    if unknown:
        raise CheckError(f"{label} has unknown fields: {', '.join(unknown)}")


def expect_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise CheckError(f"{label} must be a full lowercase Git SHA")
    return value


def expect_optional_sha(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return expect_sha(value, label)


def expect_optional_mode(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in ASSERTION_FILE_MODES:
        raise CheckError(f"{label} must be null or an exact Git mode")
    return value


def expect_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CheckError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise CheckError(f"{label} is not a valid timestamp") from error
    if parsed.tzinfo != timezone.utc:
        raise CheckError(f"{label} must use UTC")
    return parsed


def expect_unique(values, label: str) -> None:
    if len(values) != len(set(values)):
        raise CheckError(f"{label} contains duplicates")


def git_blob_oid(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def git_environment(home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }


def run_git(repository_root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        (GIT, "--no-replace-objects", "-C", str(repository_root), *arguments),
        env=git_environment(repository_root),
        check=False,
        capture_output=True,
        timeout=60,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CheckError(detail or "trusted Git command failed")
    return completed.stdout


def resolve_directory(path: Path, label: str) -> Path:
    if not path.is_dir() or path.is_symlink():
        raise CheckError(f"{label} is unavailable")
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise CheckError(f"{label} is unavailable") from error


def read_regular_file(path: Path, label: str) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise CheckError(f"{label} is unavailable")
    try:
        return path.read_bytes()
    except OSError as error:
        raise CheckError(f"{label} is unavailable") from error


def git_commit_tree(repository_root: Path, commit_sha: str, label: str) -> str:
    try:
        commit = run_git(
            repository_root, "rev-parse", "--verify", f"{commit_sha}^{{commit}}"
        ).decode("ascii").strip()
        tree = run_git(repository_root, "rev-parse", f"{commit_sha}^{{tree}}").decode(
            "ascii"
        ).strip()
    except UnicodeDecodeError as error:
        raise CheckError(f"{label} returned a non-ASCII Git identity") from error
    except CheckError as error:
        raise CheckError(f"{label} is not available from trusted Git authority") from error
    if commit != commit_sha:
        raise CheckError(f"{label} does not resolve to the exact commit")
    return tree


def git_blob_oid_at_revision(
    repository_root: Path, revision: str, relative_path: str, label: str
) -> str:
    try:
        blob_oid = run_git(
            repository_root, "rev-parse", f"{revision}:{relative_path}"
        ).decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise CheckError(f"{label} returned a non-ASCII Git identity") from error
    except CheckError as error:
        raise CheckError(f"{label} is not available from trusted Git authority") from error
    expect_sha(blob_oid, label)
    return blob_oid


def git_file_identity_at_revision(
    repository_root: Path, revision: str, relative_path: str, label: str
) -> dict[str, str | None]:
    try:
        raw = run_git(
            repository_root,
            "ls-tree",
            "-z",
            "--full-tree",
            revision,
            "--",
            relative_path,
        )
    except CheckError as error:
        raise CheckError(f"{label} is not available from trusted Git authority") from error
    records = [record for record in raw.split(b"\0") if record]
    if not records:
        return {"mode": None, "blob_oid": None}
    if len(records) != 1:
        raise CheckError(f"{label} returned an ambiguous Git identity")
    try:
        metadata, raw_path = records[0].split(b"\t", 1)
        mode, kind, blob_oid = metadata.decode("ascii").split()
        actual_path = raw_path.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise CheckError(f"{label} returned a malformed Git identity") from error
    if actual_path != relative_path or kind != "blob" or mode not in ASSERTION_FILE_MODES:
        raise CheckError(f"{label} returned an unsafe Git identity")
    expect_sha(blob_oid, label)
    return {"mode": mode, "blob_oid": blob_oid}


def validate_repository_root(path: Path) -> Path:
    root = resolve_directory(path, "checker input.repository_root")
    try:
        top_level = run_git(root, "rev-parse", "--show-toplevel").decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise CheckError("checker input.repository_root is not UTF-8") from error
    actual_top = Path(top_level).resolve()
    if actual_top != root:
        raise CheckError(
            "checker input.repository_root must be the exact Git top-level checkout"
        )
    object_format = run_git(root, "rev-parse", "--show-object-format").decode("ascii").strip()
    if object_format != "sha1":
        raise CheckError(
            f"checker input.repository_root object format {object_format!r} is not supported; exact Git object IDs require sha1"
        )
    if run_git(root, "status", "--porcelain"):
        raise CheckError("checker input.repository_root must be clean")
    return root


def normalize_actor(login: Any) -> str:
    value = expect_string(login, "actor login").removeprefix("@").casefold()
    while True:
        stripped = ACTOR_SUFFIX_RE.sub("", value)
        if stripped == value:
            return value
        value = stripped


def normalized_path(value: Any, label: str) -> str:
    value = expect_string(value, label)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise CheckError(f"{label} must be repository-relative")
    if value != path.as_posix():
        raise CheckError(f"{label} must be normalized")
    return value


def validate_change_records(value: Any, label: str) -> list[dict[str, Any]]:
    result = []
    identities = []
    for index, raw in enumerate(expect_list(value, label)):
        item_label = f"{label}[{index}]"
        record = expect_object(raw, item_label)
        expect_keys(
            record,
            item_label,
            (
                "status",
                "similarity",
                "old_path",
                "new_path",
                "base_mode",
                "base_blob_oid",
                "head_mode",
                "head_blob_oid",
            ),
        )
        status = expect_string(record["status"], f"{item_label}.status")
        if status not in {"A", "D", "M", "R", "C"}:
            raise CheckError(f"{item_label}.status is not supported")
        similarity = record["similarity"]
        if status in {"R", "C"}:
            similarity = expect_int(similarity, f"{item_label}.similarity", 0)
            if similarity > 100:
                raise CheckError(f"{item_label}.similarity exceeds 100")
        elif similarity is not None:
            raise CheckError(
                f"{item_label}.similarity is only valid for rename/copy"
            )

        def optional_path(field):
            item = record[field]
            return None if item is None else normalized_path(
                item, f"{item_label}.{field}"
            )

        def optional_mode(field):
            item = record[field]
            if item is not None and item not in {"100644", "100755"}:
                raise CheckError(f"{item_label}.{field} has an unsafe mode")
            return item

        def optional_blob(field):
            item = record[field]
            return None if item is None else expect_sha(
                item, f"{item_label}.{field}"
            )

        normalized = {
            "status": status,
            "similarity": similarity,
            "old_path": optional_path("old_path"),
            "new_path": optional_path("new_path"),
            "base_mode": optional_mode("base_mode"),
            "base_blob_oid": optional_blob("base_blob_oid"),
            "head_mode": optional_mode("head_mode"),
            "head_blob_oid": optional_blob("head_blob_oid"),
        }
        old_present = all(
            normalized[field] is not None
            for field in ("old_path", "base_mode", "base_blob_oid")
        )
        new_present = all(
            normalized[field] is not None
            for field in ("new_path", "head_mode", "head_blob_oid")
        )
        old_absent = all(
            normalized[field] is None
            for field in ("old_path", "base_mode", "base_blob_oid")
        )
        new_absent = all(
            normalized[field] is None
            for field in ("new_path", "head_mode", "head_blob_oid")
        )
        if status == "A":
            valid = old_absent and new_present
        elif status == "D":
            valid = old_present and new_absent
        else:
            valid = (
                old_present
                and new_present
                and normalized["base_mode"] == normalized["head_mode"]
            )
            if status == "M":
                valid = valid and normalized["old_path"] == normalized["new_path"]
            else:
                valid = valid and normalized["old_path"] != normalized["new_path"]
        if not valid:
            raise CheckError(f"{item_label} contradicts status {status}")
        identities.append(
            (status, normalized["old_path"], normalized["new_path"])
        )
        result.append(normalized)
    expect_unique(identities, f"{label} identities")
    return sorted(
        result,
        key=lambda record: (
            record["old_path"] or "",
            record["new_path"] or "",
            record["status"],
        ),
    )


def _validate_report(
    raw_report: Any,
    *,
    repository: str,
    pull_request: int,
    base_sha: str,
    candidate_sha: str,
    changed_files: list[str],
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    report = expect_object(raw_report, "review report")
    expect_keys(
        report,
        "review report",
        (
            "schema_version",
            "report_id",
            "repository",
            "pull_request",
            "base_sha",
            "candidate_sha",
            "reviewer_actor_id",
            "reviewer_login",
            "implementer_actor_id",
            "implementer_login",
            "started_at",
            "completed_at",
            "permissions",
            "actions",
            "reviewed_files",
            "reviewed_changes",
            "findings",
        ),
    )
    if report["schema_version"] != 2:
        raise CheckError("review report.schema_version must be 2")
    expect_string(report["report_id"], "review report.report_id")
    for field, expected in {
        "repository": repository,
        "pull_request": pull_request,
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
    }.items():
        if report[field] != expected:
            raise CheckError(
                f"review report.{field} does not match checker authority"
            )
    reviewer_id = expect_string(
        report["reviewer_actor_id"], "review report.reviewer_actor_id"
    )
    implementer_id = expect_string(
        report["implementer_actor_id"], "review report.implementer_actor_id"
    )
    reviewer_login = expect_string(
        report["reviewer_login"], "review report.reviewer_login"
    )
    implementer_login = expect_string(
        report["implementer_login"], "review report.implementer_login"
    )
    if (
        reviewer_id.casefold() == implementer_id.casefold()
        or normalize_actor(reviewer_login) == normalize_actor(implementer_login)
    ):
        raise CheckError("reviewer and implementer identities overlap")
    started = expect_time(report["started_at"], "review report.started_at")
    completed = expect_time(report["completed_at"], "review report.completed_at")
    if completed <= started:
        raise CheckError("review report interval is not positive")
    if report["permissions"] != ["contents:read"]:
        raise CheckError("review report permissions are not exactly read-only")
    actions = validate_review_action_contract(
        repository=repository,
        actions=report["actions"],
    )
    targets = validate_review_targets(
        report["reviewed_files"],
        report["reviewed_changes"],
        changed_files=changed_files,
        changes=changes,
    )

    findings = []
    finding_ids = []
    for index, raw in enumerate(
        expect_list(report["findings"], "review report.findings")
    ):
        label = f"review report.findings[{index}]"
        finding = expect_object(raw, label)
        expect_keys(finding, label, ("id", "family", "created_at"))
        finding_id = expect_string(finding["id"], f"{label}.id")
        if LOCAL_FINDING_RE.fullmatch(finding_id) is None:
            raise CheckError(
                f"{label}.id must use the independent LOCAL- namespace"
            )
        family = expect_string(finding["family"], f"{label}.family")
        if family not in FAMILY_MEMBERS:
            raise CheckError(f"{label}.family is not registered")
        created = expect_time(finding["created_at"], f"{label}.created_at")
        if created < started or created > completed:
            raise CheckError(f"{label} falls outside the immutable review interval")
        finding_ids.append(finding_id)
        findings.append(
            {
                "id": finding_id,
                "family": family,
                "created_at": finding["created_at"],
            }
        )
    expect_unique(finding_ids, "review report local finding IDs")
    return {
        **report,
        "actions": actions,
        "reviewed_files": targets["reviewed_files"],
        "reviewed_changes": targets["reviewed_changes"],
        "findings": findings,
    }


def validate_review_report(
    raw_report: Any,
    *,
    repository: str,
    pull_request: int,
    base_sha: str,
    candidate_sha: str,
    changed_files: list[str],
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    return _validate_report(
        raw_report,
        repository=repository,
        pull_request=pull_request,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
        changed_files=changed_files,
        changes=changes,
    )


def validate_review_actions(value: Any) -> list[str]:
    actions = [
        expect_string(item, f"review report.actions[{index}]")
        for index, item in enumerate(expect_list(value, "review report.actions"))
    ]
    if actions != list(ACTION_SEQUENCE):
        raise CheckError("review report actions are not exact read then report")
    return actions


def validate_review_action_contract(*, repository: str, actions: Any) -> list[str]:
    expect_string(repository, "review report.repository")
    return validate_review_actions(actions)


def validate_review_targets(
    raw_reviewed_files: Any,
    raw_reviewed_changes: Any,
    *,
    changed_files: list[str],
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    reviewed_files = [
        normalized_path(path, f"review report.reviewed_files[{index}]")
        for index, path in enumerate(
            expect_list(raw_reviewed_files, "review report.reviewed_files")
        )
    ]
    expect_unique(reviewed_files, "review report.reviewed_files")
    if set(reviewed_files) != set(changed_files):
        raise CheckError("independent review does not cover every exact changed file")
    reviewed_changes = validate_change_records(
        raw_reviewed_changes, "review report.reviewed_changes"
    )
    if reviewed_changes != changes:
        raise CheckError(
            "independent review status/blob evidence does not match exact changes"
        )
    return {
        "reviewed_files": reviewed_files,
        "reviewed_changes": reviewed_changes,
    }


def parse_assertion_id(assertion_id: str) -> dict[str, Any]:
    parts = assertion_id.split(":")
    if (
        len(parts) == 5
        and parts[:2] == ["registry", "behavior"]
        and parts[2] in BEHAVIOR_ROWS
        and parts[3] in EVIDENCE_CLASSES
        and parts[4] == "v2"
    ):
        return {
            "kind": "behavior",
            "row": parts[2],
            "evidence_class": parts[3],
        }
    if len(parts) not in {6, 7} or parts[:2] != ["registry", "sibling"]:
        raise CheckError("assertion ID is absent from exact-base registry")
    family, member, outcome = parts[2:5]
    reason = parts[5] if len(parts) == 7 else None
    version = parts[-1]
    if (
        family not in FAMILY_MEMBERS
        or member not in FAMILY_MEMBERS[family]
        or version != "v2"
    ):
        raise CheckError("assertion member is absent from registry")
    if outcome not in {"affected-fixed", "verified-unaffected", "not-applicable"}:
        raise CheckError("assertion outcome is absent from registry")
    if (
        outcome == "verified-unaffected"
        and (family, member) in MEMBERS_WITHOUT_VERIFIED_UNAFFECTED
    ):
        raise CheckError(
            f"assertion outcome is not registered for {family}/{member}"
        )
    if outcome == "not-applicable":
        if reason != REGISTERED_NOT_APPLICABLE_REASONS.get((family, member)):
            raise CheckError("not-applicable reason is not registered")
    elif reason is not None:
        raise CheckError("outcome assertion has an unexpected reason")
    return {
        "kind": "member",
        "family": family,
        "member": member,
        "outcome": outcome,
        "reason": reason,
    }


def _validate_remote_review(raw: Any, label: str) -> dict[str, Any]:
    review = expect_object(raw, label)
    expect_keys(
        review,
        label,
        (
            "id",
            "node_id",
            "round",
            "reviewer_actor_id",
            "candidate_sha",
            "submitted_at",
            "state",
            "body",
            "body_classification",
            "body_has_findings",
            "outcome",
            "finding_ids",
        ),
    )
    if not isinstance(review["body_has_findings"], bool):
        raise CheckError(f"{label}.body_has_findings must be a boolean")
    finding_ids = [
        expect_string(item, f"{label}.finding_ids[{index}]")
        for index, item in enumerate(
            expect_list(review["finding_ids"], f"{label}.finding_ids")
        )
    ]
    expect_unique(finding_ids, f"{label}.finding_ids")
    state = expect_string(review["state"], f"{label}.state")
    if state not in REMOTE_REVIEW_STATES:
        raise CheckError(f"{label}.state is not supported")
    outcome = expect_string(review["outcome"], f"{label}.outcome")
    if outcome not in REMOTE_REVIEW_OUTCOMES:
        raise CheckError(f"{label}.outcome is not supported")
    expect_string(review["body"], f"{label}.body", allow_empty=True)
    expect_string(
        review["body_classification"],
        f"{label}.body_classification",
        allow_empty=True,
    )
    expect_time(review["submitted_at"], f"{label}.submitted_at")
    return {
        "id": expect_int(review["id"], f"{label}.id"),
        "node_id": expect_string(review["node_id"], f"{label}.node_id"),
        "round": expect_int(review["round"], f"{label}.round"),
        "reviewer_actor_id": expect_string(
            review["reviewer_actor_id"], f"{label}.reviewer_actor_id"
        ),
        "candidate_sha": expect_sha(review["candidate_sha"], f"{label}.candidate_sha"),
        "submitted_at": review["submitted_at"],
        "state": state,
        "body": review["body"],
        "body_classification": review["body_classification"],
        "body_has_findings": review["body_has_findings"],
        "outcome": outcome,
        "finding_ids": finding_ids,
    }


def _validate_remote_reviews(value: Any) -> list[dict[str, Any]]:
    reviews = [
        _validate_remote_review(raw, f"checker input.all_remote_reviews[{index}]")
        for index, raw in enumerate(
            expect_list(value, "checker input.all_remote_reviews")
        )
    ]
    if not reviews:
        raise CheckError("checker input.all_remote_reviews must not be empty")
    expect_unique([review["id"] for review in reviews], "checker input.all_remote_reviews IDs")
    expect_unique(
        [review["node_id"] for review in reviews],
        "checker input.all_remote_reviews node IDs",
    )
    expected_rounds = list(range(1, len(reviews) + 1))
    if [review["round"] for review in reviews] != expected_rounds:
        raise CheckError("checker input.all_remote_reviews rounds must be consecutive")
    return reviews


def _validate_remote_findings(
    value: Any, reviews_by_node: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    findings = {}
    claimed_by_review = {review_id: [] for review_id in reviews_by_node}
    for index, raw in enumerate(expect_list(value, "checker input.remote_findings")):
        label = f"checker input.remote_findings[{index}]"
        finding = expect_object(raw, label)
        expect_keys(
            finding,
            label,
            (
                "node_id",
                "review_id",
                "candidate_sha",
                "created_at",
                "author_actor_id",
                "family",
            ),
            (
                "authority_comment_id",
                "authority_comment_created_at",
            ),
        )
        finding_id = expect_string(finding["node_id"], f"{label}.node_id")
        if LOCAL_FINDING_RE.fullmatch(finding_id) is not None:
            raise CheckError(f"{label}.node_id must not use the LOCAL- namespace")
        if finding_id in findings:
            raise CheckError(f"duplicate remote finding ID {finding_id!r}")
        review_id = expect_string(finding["review_id"], f"{label}.review_id")
        if review_id not in reviews_by_node:
            raise CheckError(f"{label}.review_id references an unknown remote review")
        candidate_sha = expect_sha(finding["candidate_sha"], f"{label}.candidate_sha")
        if candidate_sha != reviews_by_node[review_id]["candidate_sha"]:
            raise CheckError(f"{label}.candidate_sha does not match its review head")
        family = expect_string(finding["family"], f"{label}.family")
        if family not in FAMILY_MEMBERS:
            raise CheckError(f"{label}.family is not registered")
        expect_time(finding["created_at"], f"{label}.created_at")
        author_actor_id = expect_string(
            finding["author_actor_id"], f"{label}.author_actor_id"
        )
        findings[finding_id] = {
            "id": finding_id,
            "review_id": review_id,
            "candidate_sha": candidate_sha,
            "created_at": finding["created_at"],
            "author_actor_id": author_actor_id,
            "family": family,
            "authority_comment_id": (
                expect_string(
                    finding["authority_comment_id"],
                    f"{label}.authority_comment_id",
                )
                if finding.get("authority_comment_id") is not None
                else None
            ),
            "authority_comment_created_at": (
                (
                    expect_time(
                        finding["authority_comment_created_at"],
                        f"{label}.authority_comment_created_at",
                    ),
                    finding["authority_comment_created_at"],
                )[1]
                if finding.get("authority_comment_created_at") is not None
                else None
            ),
        }
        claimed_by_review[review_id].append(finding_id)
    for review_id, review in reviews_by_node.items():
        if sorted(claimed_by_review[review_id]) != sorted(review["finding_ids"]):
            raise CheckError(
                "checker input.remote_findings do not exactly match their review findings"
            )
    return findings


def _expected_assertion_artifacts(
    repository_root: Path, base_sha: str, finding_origin_sha: str, head_sha: str
) -> list[dict[str, str | None]]:
    artifacts = []
    for path in ASSERTION_INPUT_PATHS:
        base_identity = git_file_identity_at_revision(
            repository_root,
            base_sha,
            path,
            f"checker input base production input {path!r}",
        )
        origin_identity = git_file_identity_at_revision(
            repository_root,
            finding_origin_sha,
            path,
            f"checker input origin production input {path!r}",
        )
        head_identity = git_file_identity_at_revision(
            repository_root,
            head_sha,
            path,
            f"checker input head production input {path!r}",
        )
        artifacts.append(
            {
                "path": path,
                "base_mode": base_identity["mode"],
                "base_blob_oid": base_identity["blob_oid"],
                "origin_mode": origin_identity["mode"],
                "origin_blob_oid": origin_identity["blob_oid"],
                "head_mode": head_identity["mode"],
                "head_blob_oid": head_identity["blob_oid"],
            }
        )
    return sorted(
        artifacts,
        key=lambda item: item["path"],
    )


def _validate_assertion_input_artifacts(
    value: Any, expected: list[dict[str, str | None]]
) -> list[dict[str, str | None]]:
    artifacts = []
    for index, raw in enumerate(
        expect_list(value, "checker input.assertion_input_artifacts")
    ):
        label = f"checker input.assertion_input_artifacts[{index}]"
        artifact = expect_object(raw, label)
        expect_keys(
            artifact,
            label,
            (
                "path",
                "base_mode",
                "base_blob_oid",
                "origin_mode",
                "origin_blob_oid",
                "head_mode",
                "head_blob_oid",
            ),
        )
        artifacts.append(
            {
                "path": normalized_path(artifact["path"], f"{label}.path"),
                "base_mode": expect_optional_mode(
                    artifact["base_mode"], f"{label}.base_mode"
                ),
                "base_blob_oid": expect_optional_sha(
                    artifact["base_blob_oid"], f"{label}.base_blob_oid"
                ),
                "origin_mode": expect_optional_mode(
                    artifact["origin_mode"], f"{label}.origin_mode"
                ),
                "origin_blob_oid": expect_optional_sha(
                    artifact["origin_blob_oid"], f"{label}.origin_blob_oid"
                ),
                "head_mode": expect_optional_mode(
                    artifact["head_mode"], f"{label}.head_mode"
                ),
                "head_blob_oid": expect_optional_sha(
                    artifact["head_blob_oid"], f"{label}.head_blob_oid"
                ),
            }
        )
        for prefix in ("base", "origin", "head"):
            mode = artifacts[-1][f"{prefix}_mode"]
            blob_oid = artifacts[-1][f"{prefix}_blob_oid"]
            if (mode is None) != (blob_oid is None):
                raise CheckError(
                    f"{label}.{prefix}_mode and {prefix}_blob_oid must both be null or both be present"
                )
    artifacts = sorted(artifacts, key=lambda item: item["path"])
    if artifacts != expected:
        raise CheckError(
            "checker input.assertion_input_artifacts do not match exact Git authority"
        )
    return artifacts


def _validate_materialized_root(
    root: Path, label: str, expected_identities: dict[str, dict[str, str | None]]
) -> Path:
    resolved_root = resolve_directory(root, label)
    discovered = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise CheckError(f"{label} contains a symlink or path escape")
        if path.is_dir():
            continue
        if not path.is_file():
            raise CheckError(f"{label} contains an unsafe entry")
        relative = path.relative_to(root).as_posix()
        discovered[relative] = path
    expected_files = {
        path: identity
        for path, identity in expected_identities.items()
        if identity["mode"] in MATERIALIZED_FILE_MODES
    }
    if set(discovered) != set(expected_files):
        raise CheckError(f"{label} does not exactly materialize the production inputs")
    for relative, identity in expected_files.items():
        payload = read_regular_file(
            discovered[relative], f"{label}/{relative}"
        )
        if git_blob_oid(payload) != identity["blob_oid"]:
            raise CheckError(
                f"{label}/{relative} does not match exact Git blob authority"
            )
    return resolved_root


def _behavior_binding(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": None,
        "finding_family": None,
        "finding_member": None,
        "finding_review_id": None,
        "finding_review_round": None,
        "finding_head_sha": None,
        "finding_head_tree": None,
        "finding_origin_sha": None,
        "finding_origin_tree": None,
        "head_sha": data["candidate_sha"],
        "head_tree": data["candidate_tree"],
    }


def _bind_member_request(
    data: dict[str, Any], parsed: dict[str, Any], finding_id: str
) -> dict[str, Any]:
    finding = data["round_findings"].get(finding_id)
    if finding is None:
        raise CheckError(
            "member assertion finding is absent from the authoritative source round"
        )
    if finding["family"] != parsed["family"]:
        raise CheckError("member assertion finding family does not match registry family")
    return {
        "finding_id": finding_id,
        "finding_family": finding["family"],
        "finding_member": parsed["member"],
        "finding_review_id": finding["review_id"],
        "finding_review_round": finding["review_round"],
        "finding_head_sha": finding["finding_head_sha"],
        "finding_head_tree": finding["finding_head_tree"],
        "finding_origin_sha": finding["finding_origin_sha"],
        "finding_origin_tree": finding["finding_origin_tree"],
        "head_sha": data["candidate_sha"],
        "head_tree": data["candidate_tree"],
    }


def bind_member_request(
    data: dict[str, Any], parsed: dict[str, Any], finding_id: str
) -> dict[str, Any]:
    return _bind_member_request(data, parsed, finding_id)


def _validate_review_contract_context(
    value: Any,
    *,
    repository: str,
    pull_request: int,
    base_sha: str,
    original_pre_review_head: str,
    candidate_sha: str,
    trust_mode: str,
    limits: dict[str, int],
) -> dict[str, Any]:
    contract = expect_object(value, "checker input.review_contract")
    expect_keys(
        contract,
        "checker input.review_contract",
        (
            "schema_version",
            "repository",
            "pull_request",
            "base_sha",
            "original_pre_review_head",
            "candidate_sha",
            "trust_mode",
            "implementer_actor_id",
            "trigger",
            "limits",
            "behavior_rows",
            "family_sweeps",
        ),
    )
    if contract["repository"] != repository:
        raise CheckError("checker input.review_contract.repository does not match repository")
    if contract["pull_request"] != pull_request:
        raise CheckError("checker input.review_contract.pull_request does not match pull_request")
    if contract["base_sha"] != base_sha:
        raise CheckError("checker input.review_contract.base_sha does not match base_sha")
    if contract["original_pre_review_head"] != original_pre_review_head:
        raise CheckError(
            "checker input.review_contract.original_pre_review_head does not match original_pre_review_head"
        )
    if contract["candidate_sha"] != candidate_sha:
        raise CheckError("checker input.review_contract.candidate_sha does not match candidate_sha")
    if contract["trust_mode"] != trust_mode:
        raise CheckError("checker input.review_contract.trust_mode does not match trust_mode")
    if contract["limits"] != limits:
        raise CheckError("checker input.review_contract.limits do not match limits")
    expect_string(
        contract["implementer_actor_id"],
        "checker input.review_contract.implementer_actor_id",
    )
    expect_object(contract["trigger"], "checker input.review_contract.trigger")
    expect_list(contract["behavior_rows"], "checker input.review_contract.behavior_rows")
    expect_list(contract["family_sweeps"], "checker input.review_contract.family_sweeps")
    return contract


def _validate_original_review_receipt_context(
    value: Any,
    *,
    repository: str,
    pull_request: int,
    base_sha: str,
    original_pre_review_head: str,
    original_receipt_sha256: str,
) -> dict[str, Any]:
    receipt = expect_object(value, "checker input.original_review_receipt")
    expect_keys(
        receipt,
        "checker input.original_review_receipt",
        (
            "schema_version",
            "repository",
            "pull_request",
            "base_sha",
            "candidate_sha",
            "issued_at",
            "expires_at",
            "nonce",
            "key_id",
            "key_epoch",
            "purpose",
            "payload_b64",
            "hmac_sha256",
        ),
    )
    if receipt["schema_version"] != 2:
        raise CheckError("checker input.original_review_receipt.schema_version must be 2")
    if receipt["repository"] != repository:
        raise CheckError("checker input.original_review_receipt.repository does not match repository")
    if receipt["pull_request"] != pull_request:
        raise CheckError(
            "checker input.original_review_receipt.pull_request does not match pull_request"
        )
    if receipt["base_sha"] != base_sha:
        raise CheckError("checker input.original_review_receipt.base_sha does not match base_sha")
    if receipt["candidate_sha"] != original_pre_review_head:
        raise CheckError(
            "checker input.original_review_receipt.candidate_sha does not match original_pre_review_head"
        )
    expect_time(receipt["issued_at"], "checker input.original_review_receipt.issued_at")
    expect_time(receipt["expires_at"], "checker input.original_review_receipt.expires_at")
    expect_string(receipt["nonce"], "checker input.original_review_receipt.nonce")
    expect_string(receipt["key_id"], "checker input.original_review_receipt.key_id")
    expect_int(receipt["key_epoch"], "checker input.original_review_receipt.key_epoch")
    expect_string(receipt["purpose"], "checker input.original_review_receipt.purpose")
    expect_string(
        receipt["payload_b64"], "checker input.original_review_receipt.payload_b64"
    )
    expect_string(
        receipt["hmac_sha256"], "checker input.original_review_receipt.hmac_sha256"
    )
    if hashlib.sha256(normalized_json(receipt)).hexdigest() != original_receipt_sha256:
        raise CheckError(
            "checker input.original_review_receipt does not match original_receipt_sha256"
        )
    return receipt


def _assertion_program_context(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": data["repository"],
        "repository_root": str(data["repository_root"]),
        "pull_request": data["pull_request"],
        "base_sha": data["base_sha"],
        "base_tree": data["base_tree"],
        "original_pre_review_head": data["original_pre_review_head"],
        "original_pre_review_head_tree": data["original_pre_review_head_tree"],
        "original_changes": data["original_changes"],
        "original_receipt_sha256": data["original_receipt_sha256"],
        "review_contract": data["review_contract"],
        "original_review_receipt": data["original_review_receipt"],
        "assertion_program_path": str(data["assertion_program_path"]),
        "assertion_program_blob_oid": data["assertion_program_blob_oid"],
        "assertion_program_argv": data["assertion_program_argv"],
        "finding_origin_sha": data["finding_origin_sha"],
        "finding_origin_tree": data["finding_origin_tree"],
        "base_root": str(data["base_root"]),
        "origin_root": str(data["origin_root"]),
        "head_root": str(data["head_root"]),
        "assertion_input_artifacts": data["assertion_input_artifacts"],
        "candidate_sha": data["candidate_sha"],
        "candidate_tree": data["candidate_tree"],
        "head_sha": data["head_sha"],
        "review_round": data["review_round"],
        "review_context": data["review_context"],
        "all_remote_reviews": data["all_remote_reviews"],
        "remote_findings": sorted(
            data["remote_findings"].values(),
            key=lambda finding: finding["id"],
        ),
        "captured_github_payload": data["captured_github_payload"],
        "trust_mode": data["trust_mode"],
        "changed_files": data["changed_files"],
        "changes": data["changes"],
        "remote_finding_ids": data["remote_finding_ids"],
        "limits": data["limits"],
        "original_pre_review": data["original_pre_review"],
        "round_findings": data["round_findings"],
        "assertion_requests": data["assertion_requests"],
        "invoking_checker_module_name": __name__,
        "invoking_checker_argv": list(sys.argv),
        "invoking_checker_cwd": str(Path.cwd()),
        "invoking_checker_home": os.environ.get("HOME", ""),
    }


def _validate_program_output_binding(
    output: dict[str, Any], binding: dict[str, Any]
) -> None:
    for field, expected in binding.items():
        if output.get(field) != expected:
            raise CheckError(
                f"assertion program output does not preserve authoritative {field}"
            )


def validate_program_output_binding(
    output: dict[str, Any], binding: dict[str, Any]
) -> None:
    _validate_program_output_binding(output, binding)


def validate_assertion_program_identity(
    repository_root: Path,
    base_sha: str,
    assertion_program_path: Path,
    claimed_assertion_program_blob_oid: str,
    assertion_program_argv: list[Any],
) -> tuple[Path, str]:
    if assertion_program_path.name != "review_assertions.py":
        raise CheckError("checker assertion program path is unavailable")
    if assertion_program_argv != list(ASSERTION_PROGRAM_ARGV):
        raise CheckError("checker assertion program argv is not fixed")
    assertion_program_blob_oid = git_blob_oid(
        read_regular_file(assertion_program_path, "checker assertion program path")
    )
    expected_assertion_program_blob_oid = git_blob_oid_at_revision(
        repository_root,
        base_sha,
        ASSERTION_PROGRAM_RELPATH,
        "checker assertion program",
    )
    if (
        claimed_assertion_program_blob_oid != expected_assertion_program_blob_oid
        or assertion_program_blob_oid != expected_assertion_program_blob_oid
    ):
        raise CheckError(
            "checker assertion program does not match the exact base Git object"
        )
    return assertion_program_path, expected_assertion_program_blob_oid


def validate_review_context_binding(
    *,
    review_round: int,
    review_context: Any,
    all_remote_reviews: Any,
    candidate_sha: str,
    remote_finding_ids: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    return _validate_review_context_binding(
        review_round=review_round,
        review_context=review_context,
        all_remote_reviews=all_remote_reviews,
        candidate_sha=candidate_sha,
        remote_finding_ids=remote_finding_ids,
        local_remediation=False,
    )


def validate_local_remediation_context_binding(
    *,
    review_round: int,
    review_context: Any,
    all_remote_reviews: Any,
    candidate_sha: str,
    remote_finding_ids: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    return _validate_review_context_binding(
        review_round=review_round,
        review_context=review_context,
        all_remote_reviews=all_remote_reviews,
        candidate_sha=candidate_sha,
        remote_finding_ids=remote_finding_ids,
        local_remediation=True,
    )


def _validate_remote_finding_ids(remote_finding_ids: Any) -> list[str]:
    validated_remote_finding_ids = [
        expect_string(value, f"checker input.remote_finding_ids[{index}]")
        for index, value in enumerate(
            expect_list(
                remote_finding_ids,
                "checker input.remote_finding_ids",
            )
        )
    ]
    expect_unique(validated_remote_finding_ids, "checker input.remote_finding_ids")
    if any(LOCAL_FINDING_RE.fullmatch(value) for value in validated_remote_finding_ids):
        raise CheckError(
            "remote finding IDs overlap the independent namespace"
        )
    return validated_remote_finding_ids


def _validate_review_context_binding(
    *,
    review_round: int,
    review_context: Any,
    all_remote_reviews: Any,
    candidate_sha: str,
    remote_finding_ids: Any,
    local_remediation: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    validated_review_context = _validate_remote_review(
        review_context, "checker input.review_context"
    )
    validated_all_remote_reviews = _validate_remote_reviews(all_remote_reviews)
    if (
        validated_review_context["round"] != review_round
        or review_round > len(validated_all_remote_reviews)
        or validated_all_remote_reviews[review_round - 1] != validated_review_context
    ):
        raise CheckError(
            "checker review context does not match current assertion round/head"
        )
    if local_remediation:
        if validated_review_context["candidate_sha"] == candidate_sha:
            raise CheckError(
                "checker local remediation candidate must differ from the authoritative remote head"
            )
    elif validated_review_context["candidate_sha"] != candidate_sha:
        raise CheckError(
            "checker review context does not match current assertion round/head"
        )
    validated_remote_finding_ids = _validate_remote_finding_ids(remote_finding_ids)
    if sorted(validated_remote_finding_ids) != sorted(validated_review_context["finding_ids"]):
        raise CheckError(
            "checker input.remote_finding_ids do not match the current review findings"
        )
    if local_remediation and (
        not validated_remote_finding_ids
        or validated_review_context["outcome"] == "clean"
    ):
        raise CheckError(
            "checker local remediation requires one exact authoritative remote finding set"
        )
    return (
        validated_review_context,
        validated_all_remote_reviews,
        validated_remote_finding_ids,
    )


def _result_id(review_round: int, assertion_id: str, binding: dict[str, Any]) -> str:
    identity = {
        "review_round": review_round,
        "assertion_id": assertion_id,
        "authority_binding": binding,
    }
    return "result-" + hashlib.sha256(normalized_json(identity)).hexdigest()


def validate_input(raw_input: Any) -> dict[str, Any]:
    data = expect_object(raw_input, "checker input")
    expect_keys(
        data,
        "checker input",
        (
            "schema_version",
            "repository",
            "repository_root",
            "pull_request",
            "base_sha",
            "base_tree",
            "original_pre_review_head",
            "original_changes",
            "original_receipt_sha256",
            "review_contract",
            "original_review_receipt",
            "assertion_program_path",
            "assertion_program_blob_oid",
            "assertion_program_argv",
            "finding_origin_sha",
            "finding_origin_tree",
            "base_root",
            "origin_root",
            "head_root",
            "assertion_input_artifacts",
            "candidate_sha",
            "candidate_tree",
            "head_sha",
            "review_round",
            "review_context",
            "all_remote_reviews",
            "remote_findings",
            "captured_github_payload",
            "trust_mode",
            "changed_files",
            "changes",
            "remote_finding_ids",
            "limits",
            "original_pre_review",
            "assertion_requests",
        ),
    )
    if data["schema_version"] != SCHEMA_VERSION:
        raise CheckError(f"checker input.schema_version must be {SCHEMA_VERSION}")
    repository = expect_string(data["repository"], "checker input.repository")
    repository_root = validate_repository_root(
        Path(expect_string(data["repository_root"], "checker input.repository_root"))
    )
    pull_request = expect_int(data["pull_request"], "checker input.pull_request")
    base_sha = expect_sha(data["base_sha"], "checker input.base_sha")
    base_tree = expect_sha(data["base_tree"], "checker input.base_tree")
    actual_base_tree = git_commit_tree(
        repository_root, base_sha, "checker input.base_sha"
    )
    if base_tree != actual_base_tree:
        raise CheckError("checker input.base_tree does not match trusted Git authority")
    original_pre_review_head = expect_sha(
        data["original_pre_review_head"],
        "checker input.original_pre_review_head",
    )
    original_pre_review_head_tree = git_commit_tree(
        repository_root,
        original_pre_review_head,
        "checker input.original_pre_review_head",
    )
    original_changes = validate_change_records(
        data["original_changes"], "checker input.original_changes"
    )
    original_receipt_sha256 = expect_string(
        data["original_receipt_sha256"],
        "checker input.original_receipt_sha256",
    )
    if SHA_RE.fullmatch(original_receipt_sha256) is not None or not re.fullmatch(
        r"[0-9a-f]{64}", original_receipt_sha256
    ):
        raise CheckError(
            "checker input.original_receipt_sha256 must be SHA-256"
        )
    claimed_assertion_program_blob_oid = expect_sha(
        data["assertion_program_blob_oid"],
        "checker input.assertion_program_blob_oid",
    )
    assertion_program_argv = expect_list(
        data["assertion_program_argv"],
        "checker input.assertion_program_argv",
    )
    assertion_program_path, expected_assertion_program_blob_oid = (
        validate_assertion_program_identity(
            repository_root,
            base_sha,
            Path(
                expect_string(
                    data["assertion_program_path"],
                    "checker input.assertion_program_path",
                )
            ),
            claimed_assertion_program_blob_oid,
            assertion_program_argv,
        )
    )
    checker_blob_oid = git_blob_oid(
        read_regular_file(Path(__file__), "checker registry program")
    )
    expected_checker_blob_oid = git_blob_oid_at_revision(
        repository_root,
        base_sha,
        CHECKER_RELPATH,
        "checker registry program",
    )
    if checker_blob_oid != expected_checker_blob_oid:
        raise CheckError(
            "checker registry program does not match the exact base Git object"
        )
    finding_origin_sha = expect_sha(
        data["finding_origin_sha"], "checker input.finding_origin_sha"
    )
    finding_origin_tree = expect_sha(
        data["finding_origin_tree"], "checker input.finding_origin_tree"
    )
    base_root = Path(expect_string(data["base_root"], "checker input.base_root"))
    origin_root = Path(
        expect_string(data["origin_root"], "checker input.origin_root")
    )
    head_root = Path(
        expect_string(data["head_root"], "checker input.head_root")
    )
    candidate_sha = expect_sha(data["candidate_sha"], "checker input.candidate_sha")
    if candidate_sha == base_sha:
        raise CheckError("checker input candidate and base must differ")
    candidate_tree = expect_sha(data["candidate_tree"], "checker input.candidate_tree")
    actual_candidate_tree = git_commit_tree(
        repository_root, candidate_sha, "checker input.candidate_sha"
    )
    if candidate_tree != actual_candidate_tree:
        raise CheckError(
            "checker input.candidate_tree does not match trusted Git authority"
        )
    head_sha = expect_sha(data["head_sha"], "checker input.head_sha")
    if head_sha != candidate_sha:
        raise CheckError("checker input head does not equal candidate")
    review_round = expect_int(data["review_round"], "checker input.review_round")
    review_context_candidate = expect_sha(
        expect_object(data["review_context"], "checker input.review_context").get(
            "candidate_sha"
        ),
        "checker input.review_context.candidate_sha",
    )
    local_remediation = review_context_candidate != candidate_sha
    review_context, all_remote_reviews, remote_finding_ids = (
        validate_local_remediation_context_binding(
            review_round=review_round,
            review_context=data["review_context"],
            all_remote_reviews=data["all_remote_reviews"],
            candidate_sha=candidate_sha,
            remote_finding_ids=data["remote_finding_ids"],
        )
        if local_remediation
        else validate_review_context_binding(
            review_round=review_round,
            review_context=data["review_context"],
            all_remote_reviews=data["all_remote_reviews"],
            candidate_sha=candidate_sha,
            remote_finding_ids=data["remote_finding_ids"],
        )
    )
    reviews_by_node = {
        review["node_id"]: review for review in all_remote_reviews
    }
    remote_findings = _validate_remote_findings(
        data["remote_findings"], reviews_by_node
    )
    captured_github_payload = expect_object(
        data["captured_github_payload"], "checker input.captured_github_payload"
    )
    trust_mode = expect_string(data["trust_mode"], "checker input.trust_mode")
    if trust_mode not in {"introduction", "base-pinned"}:
        raise CheckError("checker input.trust_mode is not supported")
    changed_files = [
        normalized_path(path, f"checker input.changed_files[{index}]")
        for index, path in enumerate(
            expect_list(data["changed_files"], "checker input.changed_files")
        )
    ]
    if not changed_files:
        raise CheckError("checker input.changed_files must not be empty")
    expect_unique(changed_files, "checker input.changed_files")
    changes = validate_change_records(data["changes"], "checker input.changes")
    covered_paths = {
        path
        for change in changes
        for path in (change["old_path"], change["new_path"])
        if path is not None
    }
    if covered_paths != set(changed_files):
        raise CheckError(
            "checker input changed files do not match status record paths"
        )
    limits = expect_object(data["limits"], "checker input.limits")
    expect_keys(
        limits,
        "checker input.limits",
        (
            "max_duration_minutes",
            "max_findings_per_review",
            "max_reviewed_files",
            "max_siblings_per_finding",
            "max_siblings_per_handoff",
        ),
    )
    normalized_limits = {
        name: expect_int(value, f"checker input.limits.{name}")
        for name, value in limits.items()
    }
    review_contract = _validate_review_contract_context(
        data["review_contract"],
        repository=repository,
        pull_request=pull_request,
        base_sha=base_sha,
        original_pre_review_head=original_pre_review_head,
        candidate_sha=candidate_sha,
        trust_mode=trust_mode,
        limits=normalized_limits,
    )
    original_review_receipt = _validate_original_review_receipt_context(
        data["original_review_receipt"],
        repository=repository,
        pull_request=pull_request,
        base_sha=base_sha,
        original_pre_review_head=original_pre_review_head,
        original_receipt_sha256=original_receipt_sha256,
    )
    original_files = sorted(
        {
            path
            for change in original_changes
            for path in (change["old_path"], change["new_path"])
            if path is not None
        }
    )
    report = validate_review_report(
        data["original_pre_review"],
        repository=repository,
        pull_request=pull_request,
        base_sha=base_sha,
        candidate_sha=original_pre_review_head,
        changed_files=original_files,
        changes=original_changes,
    )
    try:
        run_git(repository_root, "merge-base", "--is-ancestor", base_sha, candidate_sha)
    except CheckError as error:
        raise CheckError(
            "checker current head is not a descendant of the authoritative base"
        ) from error
    required_ancestor = (
        review_context["candidate_sha"]
        if local_remediation
        else original_pre_review_head
    )
    try:
        run_git(
            repository_root,
            "merge-base",
            "--is-ancestor",
            required_ancestor,
            candidate_sha,
        )
    except CheckError as error:
        raise CheckError(
            (
                "checker local remediation candidate is not descended from the authoritative remote head"
                if local_remediation
                else "checker current head is not descended from the original pre-review head"
            )
        ) from error
    if local_remediation:
        expected_finding_origin_sha = review_context["candidate_sha"]
        expected_finding_origin_tree = git_commit_tree(
            repository_root,
            expected_finding_origin_sha,
            "checker input.review_context.candidate_sha",
        )
        round_findings = {}
        for finding_id in review_context["finding_ids"]:
            finding = remote_findings.get(finding_id)
            if finding is None or finding["review_id"] != review_context["node_id"]:
                raise CheckError(
                    "checker local remediation findings do not match authoritative collection"
                )
            round_findings[finding_id] = {
                "family": finding["family"],
                "review_id": review_context["node_id"],
                "review_round": review_context["round"],
                "finding_head_sha": review_context["candidate_sha"],
                "finding_head_tree": expected_finding_origin_tree,
                "finding_origin_sha": review_context["candidate_sha"],
                "finding_origin_tree": expected_finding_origin_tree,
            }
    elif review_round == 1:
        expected_finding_origin_sha = original_pre_review_head
        expected_finding_origin_tree = original_pre_review_head_tree
        round_findings = {
            finding["id"]: {
                "family": finding["family"],
                "review_id": report["report_id"],
                "review_round": 0,
                "finding_head_sha": original_pre_review_head,
                "finding_head_tree": original_pre_review_head_tree,
                "finding_origin_sha": original_pre_review_head,
                "finding_origin_tree": original_pre_review_head_tree,
            }
            for finding in report["findings"]
        }
    else:
        previous_review = all_remote_reviews[review_round - 2]
        expected_finding_origin_sha = previous_review["candidate_sha"]
        expected_finding_origin_tree = git_commit_tree(
            repository_root,
            expected_finding_origin_sha,
            f"checker input.all_remote_reviews[{review_round - 2}].candidate_sha",
        )
        round_findings = {}
        for finding_id in previous_review["finding_ids"]:
            finding = remote_findings.get(finding_id)
            if finding is None or finding["review_id"] != previous_review["node_id"]:
                raise CheckError(
                    "checker previous review findings do not match authoritative collection"
                )
            round_findings[finding_id] = {
                "family": finding["family"],
                "review_id": previous_review["node_id"],
                "review_round": previous_review["round"],
                "finding_head_sha": previous_review["candidate_sha"],
                "finding_head_tree": expected_finding_origin_tree,
                "finding_origin_sha": previous_review["candidate_sha"],
                "finding_origin_tree": expected_finding_origin_tree,
            }
    actual_finding_origin_tree = git_commit_tree(
        repository_root,
        finding_origin_sha,
        "checker input.finding_origin_sha",
    )
    if (
        finding_origin_sha != expected_finding_origin_sha
        or finding_origin_tree != expected_finding_origin_tree
        or actual_finding_origin_tree != expected_finding_origin_tree
    ):
        raise CheckError(
            "checker finding origin does not match the authoritative round binding"
        )
    expected_assertion_input_artifacts = _expected_assertion_artifacts(
        repository_root,
        base_sha,
        finding_origin_sha,
        candidate_sha,
    )
    assertion_input_artifacts = _validate_assertion_input_artifacts(
        data["assertion_input_artifacts"], expected_assertion_input_artifacts
    )
    base_root_resolved = _validate_materialized_root(
        base_root,
        "checker input.base_root",
        {
            item["path"]: {
                "mode": item["base_mode"],
                "blob_oid": item["base_blob_oid"],
            }
            for item in assertion_input_artifacts
        },
    )
    origin_root_resolved = _validate_materialized_root(
        origin_root,
        "checker input.origin_root",
        {
            item["path"]: {
                "mode": item["origin_mode"],
                "blob_oid": item["origin_blob_oid"],
            }
            for item in assertion_input_artifacts
        },
    )
    head_root_resolved = _validate_materialized_root(
        head_root,
        "checker input.head_root",
        {
            item["path"]: {
                "mode": item["head_mode"],
                "blob_oid": item["head_blob_oid"],
            }
            for item in assertion_input_artifacts
        },
    )
    if (
        origin_root_resolved == head_root_resolved
        and (
            finding_origin_sha != candidate_sha
            or expected_finding_origin_tree != actual_candidate_tree
        )
    ):
        raise CheckError(
            "checker input.origin_root and head_root cannot reuse one checkout"
        )
    return {
        **data,
        "repository": repository,
        "repository_root": repository_root,
        "pull_request": pull_request,
        "base_sha": base_sha,
        "base_tree": actual_base_tree,
        "original_pre_review_head": original_pre_review_head,
        "original_pre_review_head_tree": original_pre_review_head_tree,
        "original_changes": original_changes,
        "original_receipt_sha256": original_receipt_sha256,
        "review_contract": review_contract,
        "original_review_receipt": original_review_receipt,
        "assertion_program_path": assertion_program_path,
        "assertion_program_blob_oid": expected_assertion_program_blob_oid,
        "assertion_program_argv": list(ASSERTION_PROGRAM_ARGV),
        "finding_origin_sha": expected_finding_origin_sha,
        "finding_origin_tree": expected_finding_origin_tree,
        "base_root": base_root_resolved,
        "origin_root": origin_root_resolved,
        "head_root": head_root_resolved,
        "assertion_input_artifacts": assertion_input_artifacts,
        "candidate_sha": candidate_sha,
        "candidate_tree": actual_candidate_tree,
        "head_sha": head_sha,
        "review_round": review_round,
        "review_context": review_context,
        "all_remote_reviews": all_remote_reviews,
        "remote_findings": remote_findings,
        "captured_github_payload": captured_github_payload,
        "trust_mode": trust_mode,
        "changed_files": changed_files,
        "changes": changes,
        "remote_finding_ids": remote_finding_ids,
        "limits": normalized_limits,
        "original_pre_review": report,
        "round_findings": round_findings,
    }


def execute_registry(raw_input: Any) -> dict[str, Any]:
    data = validate_input(raw_input)
    local_remediation = data["review_context"]["candidate_sha"] != data["candidate_sha"]
    input_sha256 = hashlib.sha256(normalized_json(raw_input)).hexdigest()
    command_id = hashlib.sha256(normalized_json(list(CHECKER_ARGV))).hexdigest()
    program_context = _assertion_program_context(data)
    results = []
    result_ids = []
    for index, raw in enumerate(
        expect_list(data["assertion_requests"], "checker input.assertion_requests")
    ):
        label = f"checker input.assertion_requests[{index}]"
        request = expect_object(raw, label)
        expect_keys(request, label, ("assertion_id", "finding_id"))
        assertion_id = expect_string(request["assertion_id"], f"{label}.assertion_id")
        parsed_assertion = parse_assertion_id(assertion_id)
        if local_remediation and parsed_assertion["kind"] != "member":
            raise CheckError(
                "checker local remediation cannot execute behavior assertions"
            )
        raw_finding_id = request["finding_id"]
        if parsed_assertion["kind"] == "member":
            if raw_finding_id is None:
                raise CheckError("member assertion requires a finding ID")
            finding_id = expect_string(raw_finding_id, f"{label}.finding_id")
            authority_binding = bind_member_request(
                data, parsed_assertion, finding_id
            )
            program_request = {
                "assertion_id": assertion_id,
                "authority_binding": authority_binding,
                "origin_root": str(data["origin_root"]),
                "head_root": str(data["head_root"]),
                "checker_input": program_context,
            }
            disposition = parsed_assertion["outcome"]
        else:
            if raw_finding_id is not None:
                raise CheckError("behavior assertion cannot reference a finding")
            finding_id = None
            authority_binding = _behavior_binding(data)
            program_request = {
                "assertion_id": assertion_id,
                "evidence": {
                    "repository": data["repository"],
                    "pull_request": data["pull_request"],
                    "base_sha": data["base_sha"],
                    "head_sha": data["candidate_sha"],
                    "changes": data["changes"],
                    "review_head": data["review_context"]["candidate_sha"],
                    "review_outcome": data["review_context"]["outcome"],
                    "rounds": [
                        review["round"] for review in data["all_remote_reviews"]
                    ],
                    "registered_assertions": [
                        item["assertion_id"]
                        for item in data["assertion_requests"]
                    ],
                    "permissions": data["original_pre_review"]["permissions"],
                    "trust_mode": data["trust_mode"],
                    "review_round": data["review_round"],
                },
            }
            disposition = None
        program_input = normalized_json(program_request)
        command = tuple(data["assertion_program_argv"])
        environment = {
            "HOME": str(data["assertion_program_path"].parent),
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
        }
        completed = subprocess.run(
            command,
            cwd=data["assertion_program_path"].parent,
            env=environment,
            input=program_input,
            check=False,
            capture_output=True,
            timeout=30,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode(
                "utf-8", errors="replace"
            ).strip()
            raise CheckError(
                f"base assertion program rejected {assertion_id!r}: {detail}"
            )
        try:
            program_result = expect_object(
                json.loads(
                    completed.stdout.decode("utf-8"),
                    object_pairs_hook=object_no_duplicates,
                ),
                "assertion program output",
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CheckError("assertion program output is invalid") from error
        if normalized_json(program_result) != completed.stdout:
            raise CheckError("assertion program output is not canonical")
        expect_keys(
            program_result,
            "assertion program output",
            ("schema_version", "assertion_id", "status", "output"),
        )
        if (
            program_result["schema_version"] != 1
            or program_result["assertion_id"] != assertion_id
        ):
            raise CheckError("assertion program output contradicts request")
        result_status = expect_string(
            program_result["status"], "assertion program output.status"
        )
        if result_status not in {"pass", "hold"}:
            raise CheckError("assertion program output.status is not supported")
        output = expect_object(
            program_result["output"], "assertion program semantic output"
        )
        if parsed_assertion["kind"] == "member":
            _validate_program_output_binding(output, authority_binding)
        result_id = _result_id(
            data["review_round"], assertion_id, authority_binding
        )
        inputs_sha256 = hashlib.sha256(
            program_input
        ).hexdigest()
        output_sha256 = hashlib.sha256(normalized_json(output)).hexdigest()
        result_ids.append(result_id)
        results.append(
            {
                "id": result_id,
                "assertion_id": assertion_id,
                "check_id": assertion_id,
                "claimed_disposition": disposition,
                "authority_binding": authority_binding,
                "program_path": "scripts/workflow_pilot/review_assertions.py",
                "program_blob_oid": data["assertion_program_blob_oid"],
                "program_argv": data["assertion_program_argv"],
                "program_case": expect_string(
                    output.get("program_case"),
                    "assertion program output.program_case",
                ),
                "program_exit_code": completed.returncode,
                "program_stdout_sha256": hashlib.sha256(
                    completed.stdout
                ).hexdigest(),
                "command_id": command_id,
                "input_sha256": input_sha256,
                "inputs_sha256": inputs_sha256,
                "output": output,
                "output_sha256": output_sha256,
                "base_sha": data["base_sha"],
                "candidate_sha": data["candidate_sha"],
                "review_round": data["review_round"],
                "status": result_status,
            }
        )
    if not results:
        raise CheckError("checker input.assertion_requests must not be empty")
    expect_unique(result_ids, "checker assertion result IDs")
    return {
        "schema_version": SCHEMA_VERSION,
        "registry_version": REGISTRY_VERSION,
        "input_sha256": input_sha256,
        "command_id": command_id,
        "results": results,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        result = execute_registry(
            parse_json_bytes(args.input.read_bytes(), str(args.input))
        )
    except (OSError, CheckError) as error:
        print(f"review base checker error: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(normalized_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
