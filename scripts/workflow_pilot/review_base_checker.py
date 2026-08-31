#!/usr/bin/env python3
"""Standalone base-tree checker for independent review reports."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ACTOR_SUFFIX_RE = re.compile(r"(?:\[bot\]|[-_]bot)$", re.IGNORECASE)
ACTION_SEQUENCE = ("read-candidate", "emit-local-report")


class CheckError(Exception):
    pass


def object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CheckError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def parse_json_bytes(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_no_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CheckError(f"invalid JSON in {label}: {error}") from error


def expect_object(value, label):
    if not isinstance(value, dict):
        raise CheckError(f"{label} must be an object")
    return value


def expect_list(value, label):
    if not isinstance(value, list):
        raise CheckError(f"{label} must be a list")
    return value


def expect_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise CheckError(f"{label} must be a nonempty string")
    return value


def expect_keys(value, label, required):
    required = set(required)
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing:
        raise CheckError(f"{label} is missing fields: {', '.join(missing)}")
    if unknown:
        raise CheckError(f"{label} has unknown fields: {', '.join(unknown)}")


def expect_sha(value, label):
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise CheckError(f"{label} must be a full lowercase Git SHA")
    return value


def expect_time(value, label):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CheckError(f"{label} must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise CheckError(f"{label} is not a valid timestamp") from error
    if parsed.tzinfo != timezone.utc:
        raise CheckError(f"{label} must use UTC")
    return parsed


def expect_unique(values, label):
    if len(values) != len(set(values)):
        raise CheckError(f"{label} contains duplicates")


def normalize_actor(login):
    value = expect_string(login, "actor login").removeprefix("@").casefold()
    while True:
        stripped = ACTOR_SUFFIX_RE.sub("", value)
        if stripped == value:
            return value
        value = stripped


def normalized_path(value, label):
    value = expect_string(value, label)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise CheckError(f"{label} must be repository-relative")
    return value


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
            "changed_files",
            "github_finding_ids",
            "review_report",
        ),
    )
    if data["schema_version"] != SCHEMA_VERSION:
        raise CheckError(
            f"checker input.schema_version must be {SCHEMA_VERSION}"
        )
    repository = expect_string(data["repository"], "checker input.repository")
    pull_request = data["pull_request"]
    if isinstance(pull_request, bool) or not isinstance(pull_request, int):
        raise CheckError("checker input.pull_request must be an integer")
    if pull_request < 1:
        raise CheckError("checker input.pull_request must be positive")
    base_sha = expect_sha(data["base_sha"], "checker input.base_sha")
    base_tree = expect_sha(data["base_tree"], "checker input.base_tree")
    candidate_sha = expect_sha(
        data["candidate_sha"], "checker input.candidate_sha"
    )
    candidate_tree = expect_sha(
        data["candidate_tree"], "checker input.candidate_tree"
    )
    changed_files = [
        normalized_path(path, f"checker input.changed_files[{index}]")
        for index, path in enumerate(
            expect_list(data["changed_files"], "checker input.changed_files")
        )
    ]
    if not changed_files:
        raise CheckError("checker input.changed_files must not be empty")
    expect_unique(changed_files, "checker input.changed_files")
    finding_ids = [
        expect_string(value, f"checker input.github_finding_ids[{index}]")
        for index, value in enumerate(
            expect_list(
                data["github_finding_ids"],
                "checker input.github_finding_ids",
            )
        )
    ]
    expect_unique(finding_ids, "checker input.github_finding_ids")

    report = expect_object(data["review_report"], "review report")
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
            "finding_ids",
        ),
    )
    if report["schema_version"] != 1:
        raise CheckError("review report.schema_version must be 1")
    expect_string(report["report_id"], "review report.report_id")
    scalar_matches = {
        "repository": repository,
        "pull_request": pull_request,
        "base_sha": base_sha,
        "candidate_sha": candidate_sha,
    }
    for field, expected in scalar_matches.items():
        if report[field] != expected:
            raise CheckError(
                f"review report.{field} does not match checker authority"
            )
    reviewer_id = expect_string(
        report["reviewer_actor_id"], "review report.reviewer_actor_id"
    )
    implementer_id = expect_string(
        report["implementer_actor_id"],
        "review report.implementer_actor_id",
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
    completed = expect_time(
        report["completed_at"], "review report.completed_at"
    )
    if completed <= started:
        raise CheckError("review report interval is not positive")
    permissions = expect_list(
        report["permissions"], "review report.permissions"
    )
    if permissions != ["contents:read"]:
        raise CheckError("review report permissions are not exactly read-only")
    actions = expect_list(report["actions"], "review report.actions")
    if actions != list(ACTION_SEQUENCE):
        raise CheckError("review report actions are not exact read then report")
    reviewed_files = [
        normalized_path(path, f"review report.reviewed_files[{index}]")
        for index, path in enumerate(
            expect_list(report["reviewed_files"], "review report.reviewed_files")
        )
    ]
    report_findings = [
        expect_string(value, f"review report.finding_ids[{index}]")
        for index, value in enumerate(
            expect_list(report["finding_ids"], "review report.finding_ids")
        )
    ]
    expect_unique(reviewed_files, "review report.reviewed_files")
    expect_unique(report_findings, "review report.finding_ids")
    if set(reviewed_files) != set(changed_files):
        raise CheckError(
            "independent review does not cover every exact changed file"
        )
    if set(report_findings) != set(finding_ids):
        raise CheckError(
            "independent review finding IDs do not match live GitHub findings"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "repository": repository,
        "pull_request": pull_request,
        "base_sha": base_sha,
        "base_tree": base_tree,
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "report_id": report["report_id"],
        "reviewed_files": sorted(reviewed_files),
        "finding_ids": sorted(report_findings),
    }


def normalized_json(value):
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        result = validate_input(parse_json_bytes(args.input.read_bytes(), str(args.input)))
    except (OSError, CheckError) as error:
        print(f"review base checker error: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(normalized_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
