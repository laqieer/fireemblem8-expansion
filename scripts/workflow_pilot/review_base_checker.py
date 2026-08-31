#!/usr/bin/env python3
"""Base-owned closed assertion registry for independent review evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable


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
BEHAVIOR_ROWS = (
    "actor-permission-bounds",
    "authority-causality",
    "remote-review-metrics",
    "round-lifecycle",
    "sibling-family-expansion",
)
EVIDENCE_CLASSES = ("positive", "adversarial", "default", "runtime")
CHECKER_ARGV = (
    "/usr/bin/python3",
    "-I",
    "review_base_checker.py",
    "--input",
    "checker-input.json",
)


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


def expect_keys(value: dict[str, Any], label: str, required) -> None:
    required = set(required)
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise CheckError(f"{label} is missing fields: {', '.join(missing)}")
    if unknown:
        raise CheckError(f"{label} has unknown fields: {', '.join(unknown)}")


def expect_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise CheckError(f"{label} must be a full lowercase Git SHA")
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
    if report["actions"] != list(ACTION_SEQUENCE):
        raise CheckError("review report actions are not exact read then report")
    reviewed_files = [
        normalized_path(path, f"review report.reviewed_files[{index}]")
        for index, path in enumerate(
            expect_list(report["reviewed_files"], "review report.reviewed_files")
        )
    ]
    expect_unique(reviewed_files, "review report.reviewed_files")
    if set(reviewed_files) != set(changed_files):
        raise CheckError("independent review does not cover every exact changed file")
    reviewed_changes = validate_change_records(
        report["reviewed_changes"], "review report.reviewed_changes"
    )
    if reviewed_changes != changes:
        raise CheckError(
            "independent review status/blob evidence does not match exact changes"
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
        "reviewed_files": reviewed_files,
        "reviewed_changes": reviewed_changes,
        "findings": findings,
    }


def _assert_actor_permission_bounds(data: dict[str, Any]) -> None:
    report = data["review_report"]
    limits = data["limits"]
    if len(report["reviewed_files"]) > limits["max_reviewed_files"]:
        raise CheckError("reviewed files exceed the configured bound")
    if len(report["findings"]) > limits["max_findings_per_review"]:
        raise CheckError("local findings exceed the configured bound")
    started = expect_time(report["started_at"], "review report.started_at")
    completed = expect_time(report["completed_at"], "review report.completed_at")
    if (completed - started).total_seconds() > limits["max_duration_minutes"] * 60:
        raise CheckError("independent review duration exceeds the configured bound")


def _assert_authority_causality(data: dict[str, Any]) -> None:
    if data["base_sha"] == data["candidate_sha"]:
        raise CheckError("base and candidate must be distinct")
    if not data["changed_files"]:
        raise CheckError("candidate diff must not be empty")
    if not data["changes"]:
        raise CheckError("candidate status records must not be empty")


def _assert_remote_review_metrics(data: dict[str, Any]) -> None:
    remote_ids = data["remote_finding_ids"]
    expect_unique(remote_ids, "remote finding IDs")
    if any(LOCAL_FINDING_RE.fullmatch(value) for value in remote_ids):
        raise CheckError("remote finding IDs overlap the independent namespace")


def _assert_round_lifecycle(data: dict[str, Any]) -> None:
    if data["head_sha"] != data["candidate_sha"]:
        raise CheckError("assertion input head does not equal candidate")


def _assert_sibling_family_expansion(data: dict[str, Any]) -> None:
    if not isinstance(data["assertion_requests"], list):
        raise CheckError("sibling assertion inventory is unavailable")


ASSERTION_FUNCTIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "actor-permission-bounds": _assert_actor_permission_bounds,
    "authority-causality": _assert_authority_causality,
    "remote-review-metrics": _assert_remote_review_metrics,
    "round-lifecycle": _assert_round_lifecycle,
    "sibling-family-expansion": _assert_sibling_family_expansion,
}


def _behavior_assertion(
    assertion_id: str,
) -> tuple[str, str, Callable[[dict[str, Any]], None]]:
    row, separator, evidence_class = assertion_id.rpartition(":")
    if (
        not separator
        or row not in BEHAVIOR_ROWS
        or evidence_class not in EVIDENCE_CLASSES
    ):
        raise CheckError(f"assertion {assertion_id!r} is not in the closed registry")
    return row, evidence_class, ASSERTION_FUNCTIONS[row]


def _expected_result_id(assertion_id: str, inputs: Any) -> str:
    if assertion_id.startswith("sibling:"):
        inputs = expect_object(inputs, "sibling assertion inputs")
        finding_id = expect_string(
            inputs.get("finding_id"), "sibling assertion finding ID"
        )
        member = expect_string(
            inputs.get("member"), "sibling assertion member"
        )
        _, assertion_family, assertion_member = assertion_id.split(":")
        if (
            inputs.get("family") != assertion_family
            or member != assertion_member
        ):
            raise CheckError(
                "sibling assertion context does not match assertion identity"
            )
        return f"result-sibling-{finding_id}-{member}"
    return "result-" + assertion_id.replace(":", "-")


def _execute_behavior_assertion(
    data: dict[str, Any],
    assertion_id: str,
    check_id: str,
    claimed_disposition: Any,
    raw_inputs: Any,
) -> tuple[str, dict[str, Any]]:
    row, evidence_class, row_assertion = _behavior_assertion(assertion_id)
    if claimed_disposition is not None:
        raise CheckError("behavior assertion cannot claim a sibling disposition")
    expected_check_id = f"behavior:{row}:{evidence_class}:v1"
    if check_id != expected_check_id:
        raise CheckError("behavior assertion check identity is not allowlisted")
    inputs = expect_object(raw_inputs, "behavior assertion inputs")
    row_assertion(data)
    if evidence_class == "positive":
        expect_keys(
            inputs,
            "positive assertion inputs",
            ("row_id", "repository", "pull_request", "base_sha", "head_sha"),
        )
        expected = {
            "row_id": row,
            "repository": data["repository"],
            "pull_request": data["pull_request"],
            "base_sha": data["base_sha"],
            "head_sha": data["head_sha"],
        }
        if inputs != expected:
            raise CheckError("positive assertion inputs do not match exact scope")
        callable_name = "_execute_positive_assertion"
        output = {"established": "exact-scope", "row_id": row}
    elif evidence_class == "adversarial":
        expect_keys(
            inputs,
            "adversarial assertion inputs",
            ("row_id", "negative_control", "fabricated_result_id"),
        )
        if (
            inputs["row_id"] != row
            or inputs["negative_control"] != "fabricated-result-id"
            or inputs["fabricated_result_id"]
            == _expected_result_id(assertion_id, inputs)
        ):
            raise CheckError("adversarial negative control is not effective")
        callable_name = "_execute_adversarial_assertion"
        output = {
            "established": "fabricated-result-rejected",
            "row_id": row,
        }
    elif evidence_class == "default":
        expect_keys(
            inputs,
            "default assertion inputs",
            (
                "row_id",
                "trust_mode",
                "pre_review_required",
                "local_finding_count",
            ),
        )
        expected = {
            "row_id": row,
            "trust_mode": data["trust_mode"],
            "pre_review_required": data["pre_review_required"],
            "local_finding_count": len(data["review_report"]["findings"]),
        }
        if inputs != expected:
            raise CheckError("default assertion inputs do not match base context")
        callable_name = "_execute_default_assertion"
        output = {"established": "default-contract", "row_id": row}
    else:
        expect_keys(
            inputs,
            "runtime assertion inputs",
            ("row_id", "changes_sha256", "remote_finding_ids"),
        )
        expected = {
            "row_id": row,
            "changes_sha256": hashlib.sha256(
                normalized_json(data["changes"])
            ).hexdigest(),
            "remote_finding_ids": sorted(data["remote_finding_ids"]),
        }
        if inputs != expected:
            raise CheckError("runtime assertion inputs do not match execution state")
        callable_name = "_execute_runtime_assertion"
        output = {"established": "runtime-state", "row_id": row}
    return callable_name, output


def _execute_sibling_assertion(
    data: dict[str, Any],
    assertion_id: str,
    check_id: str,
    claimed_disposition: Any,
    raw_inputs: Any,
) -> tuple[str, dict[str, Any]]:
    try:
        _, family, member = assertion_id.split(":")
    except ValueError as error:
        raise CheckError("sibling assertion identity is malformed") from error
    if family not in FAMILY_MEMBERS or member not in FAMILY_MEMBERS[family]:
        raise CheckError("sibling assertion is outside the closed registry")
    disposition = expect_string(
        claimed_disposition, "sibling claimed disposition"
    )
    if disposition not in {"affected-fixed", "verified-unaffected"}:
        raise CheckError(
            "sibling disposition has no supported base-owned assertion"
        )
    expected_check_id = f"sibling:{family}:{member}:{disposition}:v1"
    if check_id != expected_check_id:
        raise CheckError("sibling outcome check identity is not allowlisted")
    inputs = expect_object(raw_inputs, "sibling assertion inputs")
    if (
        inputs.get("family") != family
        or inputs.get("member") != member
    ):
        raise CheckError("sibling assertion inputs do not match identity")
    expect_string(inputs.get("finding_id"), "sibling assertion finding ID")
    if disposition == "affected-fixed":
        expect_keys(
            inputs,
            "affected-fixed assertion inputs",
            (
                "finding_id",
                "family",
                "member",
                "changed_paths",
                "change_evidence",
            ),
        )
        paths = [
            normalized_path(path, f"affected-fixed changed_paths[{index}]")
            for index, path in enumerate(
                expect_list(inputs["changed_paths"], "affected-fixed changed_paths")
            )
        ]
        expect_unique(paths, "affected-fixed changed_paths")
        evidence = validate_change_records(
            inputs["change_evidence"], "affected-fixed change_evidence"
        )
        if not paths or any(change not in data["changes"] for change in evidence):
            raise CheckError("affected-fixed evidence is not in exact Git changes")
        if any(
            not any(
                path in {change["old_path"], change["new_path"]}
                for change in evidence
            )
            for path in paths
        ):
            raise CheckError("affected-fixed path lacks status/blob evidence")
        callable_name = "_execute_affected_fixed_assertion"
        output = {
            "established": "affected-fixed",
            "change_count": len(evidence),
        }
    else:
        expect_keys(
            inputs,
            "verified-unaffected assertion inputs",
            ("finding_id", "family", "member", "unchanged_evidence"),
        )
        evidence = expect_list(
            inputs["unchanged_evidence"], "verified-unaffected evidence"
        )
        if not evidence:
            raise CheckError("verified-unaffected evidence must not be empty")
        paths = []
        for index, raw in enumerate(evidence):
            label = f"verified-unaffected evidence[{index}]"
            item = expect_object(raw, label)
            expect_keys(
                item,
                label,
                (
                    "path",
                    "base_mode",
                    "base_blob_oid",
                    "head_mode",
                    "head_blob_oid",
                ),
            )
            path = normalized_path(item["path"], f"{label}.path")
            if (
                item["base_mode"] not in {"100644", "100755"}
                or item["head_mode"] != item["base_mode"]
                or expect_sha(item["base_blob_oid"], f"{label}.base_blob_oid")
                != expect_sha(item["head_blob_oid"], f"{label}.head_blob_oid")
            ):
                raise CheckError(
                    "verified-unaffected evidence does not bind equal blobs"
                )
            paths.append(path)
        expect_unique(paths, "verified-unaffected paths")
        callable_name = "_execute_verified_unaffected_assertion"
        output = {
            "established": "verified-unaffected",
            "path_count": len(paths),
        }
    return callable_name, output


def validate_input(raw_input: Any) -> dict[str, Any]:
    data = expect_object(raw_input, "checker input")
    expect_keys(
        data,
        "checker input",
        (
            "schema_version",
            "repository",
            "pull_request",
            "base_sha",
            "base_tree",
            "candidate_sha",
            "candidate_tree",
            "head_sha",
            "trust_mode",
            "pre_review_required",
            "changed_files",
            "changes",
            "remote_finding_ids",
            "limits",
            "review_report",
            "assertion_requests",
        ),
    )
    if data["schema_version"] != SCHEMA_VERSION:
        raise CheckError(f"checker input.schema_version must be {SCHEMA_VERSION}")
    repository = expect_string(data["repository"], "checker input.repository")
    pull_request = expect_int(data["pull_request"], "checker input.pull_request")
    base_sha = expect_sha(data["base_sha"], "checker input.base_sha")
    expect_sha(data["base_tree"], "checker input.base_tree")
    candidate_sha = expect_sha(data["candidate_sha"], "checker input.candidate_sha")
    expect_sha(data["candidate_tree"], "checker input.candidate_tree")
    head_sha = expect_sha(data["head_sha"], "checker input.head_sha")
    if head_sha != candidate_sha:
        raise CheckError("checker input head does not equal candidate")
    trust_mode = expect_string(data["trust_mode"], "checker input.trust_mode")
    if trust_mode not in {"introduction", "base-pinned"}:
        raise CheckError("checker input.trust_mode is not supported")
    if not isinstance(data["pre_review_required"], bool):
        raise CheckError("checker input.pre_review_required must be a boolean")
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
    remote_finding_ids = [
        expect_string(value, f"checker input.remote_finding_ids[{index}]")
        for index, value in enumerate(
            expect_list(
                data["remote_finding_ids"],
                "checker input.remote_finding_ids",
            )
        )
    ]
    expect_unique(remote_finding_ids, "checker input.remote_finding_ids")
    if any(LOCAL_FINDING_RE.fullmatch(value) for value in remote_finding_ids):
        raise CheckError(
            "remote finding IDs overlap the independent namespace"
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
    report = _validate_report(
        data["review_report"],
        repository=repository,
        pull_request=pull_request,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
        changed_files=changed_files,
        changes=changes,
    )
    return {
        **data,
        "repository": repository,
        "pull_request": pull_request,
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "head_sha": head_sha,
        "trust_mode": trust_mode,
        "pre_review_required": data["pre_review_required"],
        "changed_files": changed_files,
        "changes": changes,
        "remote_finding_ids": remote_finding_ids,
        "limits": normalized_limits,
        "review_report": report,
    }


def execute_registry(raw_input: Any) -> dict[str, Any]:
    data = validate_input(raw_input)
    input_sha256 = hashlib.sha256(normalized_json(raw_input)).hexdigest()
    command_id = hashlib.sha256(normalized_json(list(CHECKER_ARGV))).hexdigest()
    results = []
    result_ids = []
    for index, raw in enumerate(
        expect_list(data["assertion_requests"], "checker input.assertion_requests")
    ):
        label = f"checker input.assertion_requests[{index}]"
        request = expect_object(raw, label)
        expect_keys(
            request,
            label,
            (
                "id",
                "assertion_id",
                "check_id",
                "claimed_disposition",
                "inputs",
            ),
        )
        result_id = expect_string(request["id"], f"{label}.id")
        assertion_id = expect_string(request["assertion_id"], f"{label}.assertion_id")
        check_id = expect_string(request["check_id"], f"{label}.check_id")
        if result_id != _expected_result_id(assertion_id, request["inputs"]):
            raise CheckError(
                f"{label}.id does not match its closed assertion identity"
            )
        if assertion_id.startswith("sibling:"):
            callable_name, output = _execute_sibling_assertion(
                data,
                assertion_id,
                check_id,
                request["claimed_disposition"],
                request["inputs"],
            )
        else:
            callable_name, output = _execute_behavior_assertion(
                data,
                assertion_id,
                check_id,
                request["claimed_disposition"],
                request["inputs"],
            )
        inputs_sha256 = hashlib.sha256(
            normalized_json(request["inputs"])
        ).hexdigest()
        output_sha256 = hashlib.sha256(normalized_json(output)).hexdigest()
        result_ids.append(result_id)
        results.append(
            {
                "id": result_id,
                "assertion_id": assertion_id,
                "check_id": check_id,
                "claimed_disposition": request["claimed_disposition"],
                "callable": callable_name,
                "command_id": command_id,
                "input_sha256": input_sha256,
                "inputs_sha256": inputs_sha256,
                "output_sha256": output_sha256,
                "base_sha": data["base_sha"],
                "candidate_sha": data["candidate_sha"],
                "status": "pass",
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
