#!/usr/bin/env python3
"""Base-owned closed assertion registry for independent review evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
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


def _result_id(
    data: dict[str, Any], assertion_id: str, finding_id: str | None
) -> str:
    identity = {
        "candidate_sha": data["candidate_sha"],
        "review_round": data["review_round"],
        "assertion_id": assertion_id,
        "finding_id": finding_id,
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
            "pull_request",
            "base_sha",
            "base_tree",
            "original_pre_review_head",
            "original_changes",
            "original_receipt_sha256",
            "assertion_program_path",
            "assertion_program_blob_oid",
            "assertion_program_argv",
            "finding_origin_sha",
            "finding_origin_tree",
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
            "trust_mode",
            "pre_review_required",
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
    pull_request = expect_int(data["pull_request"], "checker input.pull_request")
    base_sha = expect_sha(data["base_sha"], "checker input.base_sha")
    expect_sha(data["base_tree"], "checker input.base_tree")
    original_pre_review_head = expect_sha(
        data["original_pre_review_head"],
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
    assertion_program_path = Path(
        expect_string(
            data["assertion_program_path"],
            "checker input.assertion_program_path",
        )
    )
    if (
        assertion_program_path.name != "review_assertions.py"
        or not assertion_program_path.is_file()
        or assertion_program_path.is_symlink()
    ):
        raise CheckError("checker assertion program path is unavailable")
    assertion_program_blob_oid = expect_sha(
        data["assertion_program_blob_oid"],
        "checker input.assertion_program_blob_oid",
    )
    assertion_program_argv = expect_list(
        data["assertion_program_argv"],
        "checker input.assertion_program_argv",
    )
    if assertion_program_argv != [
        "/usr/bin/python3",
        "-I",
        "review_assertions.py",
        "--stdin",
    ]:
        raise CheckError("checker assertion program argv is not fixed")
    finding_origin_sha = expect_sha(
        data["finding_origin_sha"], "checker input.finding_origin_sha"
    )
    finding_origin_tree = expect_sha(
        data["finding_origin_tree"], "checker input.finding_origin_tree"
    )
    origin_root = Path(
        expect_string(data["origin_root"], "checker input.origin_root")
    )
    head_root = Path(
        expect_string(data["head_root"], "checker input.head_root")
    )
    if (
        not origin_root.is_dir()
        or not head_root.is_dir()
        or origin_root.is_symlink()
        or head_root.is_symlink()
    ):
        raise CheckError("checker assertion tree roots are unavailable")
    assertion_input_artifacts = expect_list(
        data["assertion_input_artifacts"],
        "checker input.assertion_input_artifacts",
    )
    candidate_sha = expect_sha(data["candidate_sha"], "checker input.candidate_sha")
    expect_sha(data["candidate_tree"], "checker input.candidate_tree")
    head_sha = expect_sha(data["head_sha"], "checker input.head_sha")
    if head_sha != candidate_sha:
        raise CheckError("checker input head does not equal candidate")
    review_round = expect_int(data["review_round"], "checker input.review_round")
    review_context = expect_object(
        data["review_context"], "checker input.review_context"
    )
    all_remote_reviews = [
        expect_object(review, f"checker input.all_remote_reviews[{index}]")
        for index, review in enumerate(
            expect_list(
                data["all_remote_reviews"],
                "checker input.all_remote_reviews",
            )
        )
    ]
    if (
        review_context.get("round") != review_round
        or review_context.get("candidate_sha") != candidate_sha
        or review_round > len(all_remote_reviews)
        or all_remote_reviews[review_round - 1] != review_context
    ):
        raise CheckError(
            "checker review context does not match current assertion round/head"
        )
    remote_findings = [
        expect_object(finding, f"checker input.remote_findings[{index}]")
        for index, finding in enumerate(
            expect_list(data["remote_findings"], "checker input.remote_findings")
        )
    ]
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
    original_files = sorted(
        {
            path
            for change in original_changes
            for path in (change["old_path"], change["new_path"])
            if path is not None
        }
    )
    report = _validate_report(
        data["original_pre_review"],
        repository=repository,
        pull_request=pull_request,
        base_sha=base_sha,
        candidate_sha=original_pre_review_head,
        changed_files=original_files,
        changes=original_changes,
    )
    return {
        **data,
        "repository": repository,
        "pull_request": pull_request,
        "base_sha": base_sha,
        "original_pre_review_head": original_pre_review_head,
        "original_changes": original_changes,
        "original_receipt_sha256": original_receipt_sha256,
        "assertion_program_path": assertion_program_path,
        "assertion_program_blob_oid": assertion_program_blob_oid,
        "assertion_program_argv": assertion_program_argv,
        "finding_origin_sha": finding_origin_sha,
        "finding_origin_tree": finding_origin_tree,
        "origin_root": origin_root,
        "head_root": head_root,
        "assertion_input_artifacts": assertion_input_artifacts,
        "candidate_sha": candidate_sha,
        "head_sha": head_sha,
        "review_round": review_round,
        "review_context": review_context,
        "all_remote_reviews": all_remote_reviews,
        "remote_findings": remote_findings,
        "trust_mode": trust_mode,
        "pre_review_required": data["pre_review_required"],
        "changed_files": changed_files,
        "changes": changes,
        "remote_finding_ids": remote_finding_ids,
        "limits": normalized_limits,
        "original_pre_review": report,
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
        expect_keys(request, label, ("assertion_id", "finding_id"))
        assertion_id = expect_string(request["assertion_id"], f"{label}.assertion_id")
        finding_id = request["finding_id"]
        if finding_id is not None:
            finding_id = expect_string(finding_id, f"{label}.finding_id")
        if assertion_id.startswith("registry:sibling:"):
            if finding_id is None:
                raise CheckError("member assertion requires a finding ID")
            program_request = {
                "assertion_id": assertion_id,
                "finding_id": finding_id,
                "origin_root": str(data["origin_root"]),
                "head_root": str(data["head_root"]),
            }
            disposition = assertion_id.split(":")[4]
        else:
            if finding_id is not None:
                raise CheckError("behavior assertion cannot reference a finding")
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
        command = (
            "/usr/bin/python3",
            "-I",
            str(data["assertion_program_path"]),
            "--stdin",
        )
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
            or program_result["status"] != "pass"
        ):
            raise CheckError("assertion program output contradicts request")
        output = expect_object(
            program_result["output"], "assertion program semantic output"
        )
        result_id = _result_id(data, assertion_id, finding_id)
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
