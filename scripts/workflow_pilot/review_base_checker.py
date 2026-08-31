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


def _validate_report(
    raw_report: Any,
    *,
    repository: str,
    pull_request: int,
    base_sha: str,
    candidate_sha: str,
    changed_files: list[str],
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
        "findings": findings,
    }


def _assert_actor_permission_bounds(data: dict[str, Any], context: Any) -> None:
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


def _assert_authority_causality(data: dict[str, Any], context: Any) -> None:
    if data["base_sha"] == data["candidate_sha"]:
        raise CheckError("base and candidate must be distinct")
    if not data["changed_files"]:
        raise CheckError("candidate diff must not be empty")


def _assert_remote_review_metrics(data: dict[str, Any], context: Any) -> None:
    remote_ids = data["remote_finding_ids"]
    expect_unique(remote_ids, "remote finding IDs")
    if any(LOCAL_FINDING_RE.fullmatch(value) for value in remote_ids):
        raise CheckError("remote finding IDs overlap the independent namespace")


def _assert_round_lifecycle(data: dict[str, Any], context: Any) -> None:
    if data["head_sha"] != data["candidate_sha"]:
        raise CheckError("assertion input head does not equal candidate")


def _assert_sibling_family(data: dict[str, Any], context: Any) -> None:
    context = expect_object(context, "sibling assertion context")
    expect_keys(context, "sibling assertion context", ("finding_id", "family", "member"))
    family = expect_string(context["family"], "sibling assertion family")
    member = expect_string(context["member"], "sibling assertion member")
    if family not in FAMILY_MEMBERS or member not in FAMILY_MEMBERS[family]:
        raise CheckError("sibling assertion is outside the closed family registry")
    expect_string(context["finding_id"], "sibling assertion finding ID")


def _assert_sibling_family_expansion(
    data: dict[str, Any], context: Any
) -> None:
    if context is not None:
        raise CheckError("behavior-row sibling assertion context must be null")


ASSERTION_FUNCTIONS: dict[str, Callable[[dict[str, Any], Any], None]] = {
    "actor-permission-bounds": _assert_actor_permission_bounds,
    "authority-causality": _assert_authority_causality,
    "remote-review-metrics": _assert_remote_review_metrics,
    "round-lifecycle": _assert_round_lifecycle,
    "sibling-family-expansion": _assert_sibling_family_expansion,
}


def _assertion_function(assertion_id: str) -> Callable[[dict[str, Any], Any], None]:
    if assertion_id.startswith("sibling:"):
        return _assert_sibling_family
    row, separator, evidence_class = assertion_id.rpartition(":")
    if (
        not separator
        or row not in BEHAVIOR_ROWS
        or evidence_class not in EVIDENCE_CLASSES
    ):
        raise CheckError(f"assertion {assertion_id!r} is not in the closed registry")
    return ASSERTION_FUNCTIONS[row]


def _expected_result_id(assertion_id: str, context: Any) -> str:
    if assertion_id.startswith("sibling:"):
        context = expect_object(context, "sibling assertion context")
        finding_id = expect_string(
            context.get("finding_id"), "sibling assertion finding ID"
        )
        member = expect_string(
            context.get("member"), "sibling assertion member"
        )
        _, assertion_family, assertion_member = assertion_id.split(":")
        if (
            context.get("family") != assertion_family
            or member != assertion_member
        ):
            raise CheckError(
                "sibling assertion context does not match assertion identity"
            )
        return f"result-sibling-{finding_id}-{member}"
    return "result-" + assertion_id.replace(":", "-")


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
            "changed_files",
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
    changed_files = [
        normalized_path(path, f"checker input.changed_files[{index}]")
        for index, path in enumerate(
            expect_list(data["changed_files"], "checker input.changed_files")
        )
    ]
    if not changed_files:
        raise CheckError("checker input.changed_files must not be empty")
    expect_unique(changed_files, "checker input.changed_files")
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
    )
    return {
        **data,
        "repository": repository,
        "pull_request": pull_request,
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
        "head_sha": head_sha,
        "changed_files": changed_files,
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
        expect_keys(request, label, ("id", "assertion_id", "context"))
        result_id = expect_string(request["id"], f"{label}.id")
        assertion_id = expect_string(request["assertion_id"], f"{label}.assertion_id")
        function = _assertion_function(assertion_id)
        if result_id != _expected_result_id(assertion_id, request["context"]):
            raise CheckError(
                f"{label}.id does not match its closed assertion identity"
            )
        function(data, request["context"])
        result_ids.append(result_id)
        results.append(
            {
                "id": result_id,
                "assertion_id": assertion_id,
                "callable": function.__name__,
                "command_id": command_id,
                "input_sha256": input_sha256,
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
