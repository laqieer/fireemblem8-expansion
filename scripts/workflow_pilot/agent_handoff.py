#!/usr/bin/env python3
"""Validate bounded exact-SHA implementation handoffs against real Git state."""

from __future__ import annotations

import argparse
import copy
import hashlib
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
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
DELIVERY_TASK_PHASES = {
    "closure",
    "completion",
    "fix_forward_revert",
    "implementation",
    "merge",
    "post_merge_build",
    "remote_completion",
}
DELIVERY_TASK_STATUSES = {
    "blocked",
    "done",
    "in_progress",
    "not_required",
    "pending",
}
DELIVERY_DEPENDENCY_TYPES = {"code_contract", "delivery_gate"}
DELIVERY_WATCHER_STATES = {"completed", "error", "running", "timeout"}
ALLOWED_CHECK_CONTRACTS = {"git-diff-check"}
LOGIN_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?(?:\[bot\])?$",
    re.IGNORECASE,
)
INPUT_SEAL_DOMAIN = b"workflow-pilot-agent-handoff-input-v1\0"
RESULT_SEAL_DOMAIN = b"workflow-pilot-agent-handoff-result-v1\0"
GIT_SEAL_DOMAIN = b"workflow-pilot-agent-handoff-git-v1\0"
CHECK_RECEIPT_SEAL_DOMAIN = b"workflow-pilot-agent-check-receipt-v1\0"
HISTORY_RECEIPT_SEAL_DOMAIN = b"workflow-pilot-agent-history-receipt-v1\0"
ZERO_SEAL = "0" * 64


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


def _parse_actor(
    login_value: Any,
    database_id_value: Any,
    label: str,
) -> dict[str, Any]:
    login = expect_string(login_value, f"{label}.login")
    if LOGIN_RE.fullmatch(login) is None:
        raise HandoffDataError(f"{label}.login is not a canonical GitHub login")
    database_id = database_id_value
    if database_id is not None:
        database_id = expect_int(database_id, f"{label}.database_id", 1)
    canonical_login = login.casefold()
    return {
        "login": canonical_login,
        "database_id": database_id,
        "identity": (
            f"github-id:{database_id}"
            if database_id is not None
            else f"github-login:{canonical_login}"
        ),
    }


def _actors_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["database_id"] is not None and right["database_id"] is not None:
        return left["database_id"] == right["database_id"]
    return left["login"] == right["login"]


def worktree_identity(repository_root: Path) -> str:
    repository_root = validate_repository_root(repository_root)
    payload = {
        "path": str(repository_root),
        "git_dir": (
            run_git(repository_root, "rev-parse", "--absolute-git-dir")
            .decode("utf-8")
            .strip()
        ),
        "origin": (
            run_git(repository_root, "remote", "get-url", "origin")
            .decode("utf-8")
            .strip()
        ),
    }
    return hashlib.sha256(
        GIT_SEAL_DOMAIN + b"worktree\0" + normalized_json(payload)
    ).hexdigest()


def _allowed_check_argv(
    contract: str,
    repository_root: Path,
    parent_sha: str,
    candidate_sha: str,
) -> list[str]:
    expect_enum(contract, ALLOWED_CHECK_CONTRACTS, "check contract")
    if contract == "git-diff-check":
        return list(
            reporter.git_command(
                repository_root,
                "diff",
                "--check",
                parent_sha,
                candidate_sha,
            )
        )
    raise HandoffDataError(f"unsupported check contract {contract!r}")


def _check_output_sha256(stdout: bytes, stderr: bytes) -> str:
    return hashlib.sha256(
        b"stdout\0" + stdout + b"\0stderr\0" + stderr
    ).hexdigest()


def seal_check_receipt(receipt: dict[str, Any]) -> str:
    payload = {
        key: value for key, value in receipt.items() if key != "seal"
    }
    return hashlib.sha256(
        CHECK_RECEIPT_SEAL_DOMAIN + normalized_json(payload)
    ).hexdigest()


def execute_allowed_check(
    *,
    receipt_id: str,
    check_id: str,
    contract: str,
    repository_root: Path,
    parent_sha: str,
    candidate_sha: str,
) -> dict[str, Any]:
    repository_root = validate_repository_root(repository_root)
    expect_string(receipt_id, "receipt_id")
    expect_string(check_id, "check_id")
    expect_sha(parent_sha, "parent_sha")
    expect_sha(candidate_sha, "candidate_sha")
    argv = _allowed_check_argv(
        contract,
        repository_root,
        parent_sha,
        candidate_sha,
    )
    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        completed = subprocess.run(
            argv,
            cwd=repository_root,
            env=reporter.git_environment(offline=True),
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise HandoffDataError(
            f"cannot execute allowed check {check_id!r}: {error}"
        ) from error
    completed_at = datetime.now(timezone.utc).replace(microsecond=0)
    receipt = {
        "id": receipt_id,
        "check_id": check_id,
        "contract": contract,
        "argv": argv,
        "parent_sha": parent_sha,
        "candidate_sha": candidate_sha,
        "worktree_identity": worktree_identity(repository_root),
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
        "exit_code": completed.returncode,
        "output_sha256": _check_output_sha256(
            completed.stdout,
            completed.stderr,
        ),
    }
    receipt["seal"] = seal_check_receipt(receipt)
    return receipt


def _parse_check_receipts(
    raw_receipts: Any,
    label: str,
) -> dict[str, dict[str, Any]]:
    receipts = {}
    required = (
        "id",
        "check_id",
        "contract",
        "argv",
        "parent_sha",
        "candidate_sha",
        "worktree_identity",
        "started_at",
        "completed_at",
        "exit_code",
        "output_sha256",
        "seal",
    )
    for index, raw in enumerate(
        expect_list(raw_receipts, f"{label}.check_receipts")
    ):
        receipt_label = f"{label}.check_receipts[{index}]"
        receipt = expect_object(raw, receipt_label)
        expect_keys(receipt, receipt_label, required)
        receipt_id = expect_string(receipt["id"], f"{receipt_label}.id")
        if receipt_id in receipts:
            raise HandoffDataError(
                f"{label}.check_receipts repeats {receipt_id!r}"
            )
        expect_string(receipt["check_id"], f"{receipt_label}.check_id")
        expect_enum(
            receipt["contract"],
            ALLOWED_CHECK_CONTRACTS,
            f"{receipt_label}.contract",
        )
        argv = expect_list(receipt["argv"], f"{receipt_label}.argv")
        if not argv:
            raise HandoffDataError(f"{receipt_label}.argv must not be empty")
        for argument_index, argument in enumerate(argv):
            expect_string(
                argument,
                f"{receipt_label}.argv[{argument_index}]",
                allow_empty=True,
            )
        expect_sha(receipt["parent_sha"], f"{receipt_label}.parent_sha")
        expect_sha(receipt["candidate_sha"], f"{receipt_label}.candidate_sha")
        for field in ("worktree_identity", "output_sha256", "seal"):
            value = receipt[field]
            if (
                not isinstance(value, str)
                or reporter.SHA256_RE.fullmatch(value) is None
            ):
                raise HandoffDataError(
                    f"{receipt_label}.{field} must be a lowercase SHA-256"
                )
        started_at = parse_time(
            receipt["started_at"],
            f"{receipt_label}.started_at",
        )
        completed_at = parse_time(
            receipt["completed_at"],
            f"{receipt_label}.completed_at",
        )
        if completed_at < started_at:
            raise HandoffDataError(
                f"{receipt_label}.completed_at precedes started_at"
            )
        expect_int(receipt["exit_code"], f"{receipt_label}.exit_code", 0)
        receipts[receipt_id] = receipt
    return receipts


def _verify_check_receipt(
    receipt: dict[str, Any],
    *,
    check: dict[str, Any],
    repository_root: Path,
    parent_sha: str,
    candidate_sha: str,
) -> set[str]:
    errors = set()
    if receipt["seal"] != seal_check_receipt(receipt):
        errors.add("invalid-check-receipt")
    expected_argv = _allowed_check_argv(
        check["contract"],
        repository_root,
        parent_sha,
        candidate_sha,
    )
    if (
        receipt["check_id"] != check["id"]
        or receipt["contract"] != check["contract"]
        or receipt["argv"] != expected_argv
        or receipt["parent_sha"] != parent_sha
        or receipt["candidate_sha"] != candidate_sha
        or receipt["worktree_identity"] != worktree_identity(repository_root)
    ):
        errors.add("invalid-check-receipt")
    try:
        completed = subprocess.run(
            expected_argv,
            cwd=repository_root,
            env=reporter.git_environment(offline=True),
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise HandoffDataError(
            f"cannot verify allowed check {check['id']!r}: {error}"
        ) from error
    if (
        receipt["exit_code"] != completed.returncode
        or receipt["output_sha256"]
        != _check_output_sha256(completed.stdout, completed.stderr)
    ):
        errors.add("invalid-check-receipt")
    if completed.returncode != 0:
        errors.add("required-check-failed")
    return errors


def seal_history_receipt(receipt: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in receipt.items()
        if key != "seal" and not key.startswith("_")
    }
    return hashlib.sha256(
        HISTORY_RECEIPT_SEAL_DOMAIN + normalized_json(payload)
    ).hexdigest()


def seal_git_authority(authority: dict[str, Any]) -> str:
    return hashlib.sha256(
        GIT_SEAL_DOMAIN + normalized_json(authority)
    ).hexdigest()


def seal_handoff_result(result: dict[str, Any]) -> str:
    payload = {
        key: value for key, value in result.items() if key != "result_seal"
    }
    return hashlib.sha256(
        RESULT_SEAL_DOMAIN + normalized_json(payload)
    ).hexdigest()


def validate_prior_handoffs(raw_history: Any) -> list[dict[str, Any]]:
    history = []
    handoff_ids = []
    candidate_shas = []
    expected_previous = ZERO_SEAL
    previous_closed_at = None
    required = (
        "sequence",
        "previous_seal",
        "handoff_id",
        "owner_id",
        "owner_database_id",
        "issue",
        "pull_request",
        "candidate_sha",
        "lifecycle_state",
        "closed_at",
        "input_seal",
        "git_seal",
        "result_seal",
        "seal",
    )
    raw_receipts = copy.deepcopy(expect_list(raw_history, "prior_handoffs"))
    for index, raw in enumerate(raw_receipts):
        label = f"prior_handoffs[{index}]"
        receipt = expect_object(raw, label)
        expect_keys(receipt, label, required)
        sequence = expect_int(receipt["sequence"], f"{label}.sequence", 1)
        if sequence != index + 1:
            raise HandoffDataError(
                f"{label}.sequence must be contiguous from one"
            )
        previous_seal = receipt["previous_seal"]
        if (
            not isinstance(previous_seal, str)
            or reporter.SHA256_RE.fullmatch(previous_seal) is None
        ):
            raise HandoffDataError(
                f"{label}.previous_seal must be a lowercase SHA-256"
            )
        if previous_seal != expected_previous:
            raise HandoffDataError(
                f"{label}.previous_seal forks or reorders handoff history"
            )
        handoff_ids.append(
            expect_string(receipt["handoff_id"], f"{label}.handoff_id")
        )
        receipt["_owner"] = _parse_actor(
            receipt["owner_id"],
            receipt["owner_database_id"],
            f"{label}.owner",
        )
        expect_int(receipt["issue"], f"{label}.issue", 1)
        if receipt["pull_request"] is not None:
            expect_int(receipt["pull_request"], f"{label}.pull_request", 1)
        candidate_shas.append(
            expect_sha(receipt["candidate_sha"], f"{label}.candidate_sha")
        )
        if receipt["lifecycle_state"] != "handed_off":
            raise HandoffDataError(
                f"{label}.lifecycle_state must be 'handed_off'"
            )
        closed_at = parse_time(receipt["closed_at"], f"{label}.closed_at")
        if previous_closed_at is not None and closed_at <= previous_closed_at:
            raise HandoffDataError(
                f"{label}.closed_at reorders handoff history"
            )
        previous_closed_at = closed_at
        for field in ("input_seal", "git_seal", "result_seal", "seal"):
            value = receipt[field]
            if (
                not isinstance(value, str)
                or reporter.SHA256_RE.fullmatch(value) is None
            ):
                raise HandoffDataError(
                    f"{label}.{field} must be a lowercase SHA-256"
                )
        if receipt["seal"] != seal_history_receipt(receipt):
            raise HandoffDataError(f"{label}.seal does not verify")
        expected_previous = receipt["seal"]
        history.append(receipt)
    expect_unique(handoff_ids, "prior handoff IDs")
    expect_unique(candidate_shas, "prior handoff candidate SHAs")
    return history


def make_history_receipt(
    document: dict[str, Any],
    result: dict[str, Any],
    handoff_id: str,
) -> dict[str, Any]:
    prior = validate_prior_handoffs(document["prior_handoffs"])
    source_handoffs = {
        item["id"]: item for item in document["handoffs"]
    }
    result_handoffs = {
        item["id"]: item for item in result["handoffs"]
    }
    source = source_handoffs.get(handoff_id)
    handoff_result = result_handoffs.get(handoff_id)
    if (
        source is None
        or handoff_result is None
        or handoff_result["outcome"] != "accepted"
    ):
        raise HandoffDataError(
            f"handoff {handoff_id!r} has no accepted result to seal"
        )
    receipt = {
        "sequence": len(prior) + 1,
        "previous_seal": prior[-1]["seal"] if prior else ZERO_SEAL,
        "handoff_id": handoff_id,
        "owner_id": source["owner_id"],
        "owner_database_id": source["owner_database_id"],
        "issue": source["issue"],
        "pull_request": source["pull_request"],
        "candidate_sha": handoff_result["result_sha"],
        "lifecycle_state": "handed_off",
        "closed_at": handoff_result["closed_at"],
        "input_seal": result["input_seal"],
        "git_seal": result["git_seal"],
        "result_seal": result["result_seal"],
    }
    receipt["seal"] = seal_history_receipt(receipt)
    return receipt


def reporter_record(
    document: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    record = {
        "source_handoff_ids": sorted(
            item["id"] for item in document["handoffs"]
        ),
        "document": copy.deepcopy(document),
        "input_seal": result["input_seal"],
        "git_seal": result["git_seal"],
        "result_seal": result["result_seal"],
        "result": copy.deepcopy(result),
    }
    verify_reporter_record(record, revalidate_git=False)
    return record


def verify_reporter_record(
    raw_record: Any,
    *,
    revalidate_git: bool,
) -> dict[str, Any]:
    record = copy.deepcopy(expect_object(raw_record, "handoff reporter record"))
    expect_keys(
        record,
        "handoff reporter record",
        (
            "source_handoff_ids",
            "document",
            "input_seal",
            "git_seal",
            "result_seal",
            "result",
        ),
    )
    source_handoff_ids = expect_list(
        record["source_handoff_ids"],
        "handoff reporter record.source_handoff_ids",
    )
    for index, handoff_id in enumerate(source_handoff_ids):
        expect_string(
            handoff_id,
            f"handoff reporter record.source_handoff_ids[{index}]",
        )
    expect_unique(
        source_handoff_ids,
        "handoff reporter record.source_handoff_ids",
    )
    document = expect_object(
        record["document"],
        "handoff reporter record.document",
    )
    expect_keys(
        document,
        "handoff reporter record.document",
        (
            "schema_version",
            "repository",
            "prior_handoffs",
            "delivery_graph",
            "coordinators",
            "handoffs",
            "workflow_runs",
            "watchers",
            "remote_actions",
        ),
    )
    expect_list(
        document["handoffs"],
        "handoff reporter record.document.handoffs",
    )
    validate_prior_handoffs(document["prior_handoffs"])
    result = expect_object(
        record["result"],
        "handoff reporter record.result",
    )
    expect_keys(
        result,
        "handoff reporter record.result",
        (
            "schema_version",
            "repository",
            "coordinator_id",
            "delivery_graph",
            "handoffs",
            "watchers",
            "git_authority",
            "git_seal",
            "summary",
            "input_seal",
            "result_seal",
        ),
    )
    for field in ("input_seal", "git_seal", "result_seal"):
        value = record[field]
        if (
            not isinstance(value, str)
            or reporter.SHA256_RE.fullmatch(value) is None
        ):
            raise HandoffDataError(
                f"handoff reporter record.{field} must be a lowercase SHA-256"
            )
        if result[field] != value:
            raise HandoffDataError(
                f"handoff reporter record.{field} contradicts its result"
            )
    expected_ids = sorted(item["id"] for item in document["handoffs"])
    if source_handoff_ids != expected_ids:
        raise HandoffDataError(
            "handoff reporter record source identities contradict its document"
        )
    if record["input_seal"] != hashlib.sha256(
        INPUT_SEAL_DOMAIN + normalized_json(document)
    ).hexdigest():
        raise HandoffDataError(
            "handoff reporter record input seal does not verify"
        )
    if result["git_seal"] != seal_git_authority(result["git_authority"]):
        raise HandoffDataError(
            "handoff reporter record Git seal does not verify"
        )
    if result["result_seal"] != seal_handoff_result(result):
        raise HandoffDataError(
            "handoff reporter record result seal does not verify"
        )
    for handoff_index, handoff in enumerate(document["handoffs"]):
        _parse_check_receipts(
            handoff["check_receipts"],
            f"handoff reporter document.handoffs[{handoff_index}]",
        )
        for receipt in handoff["check_receipts"]:
            if receipt["seal"] != seal_check_receipt(receipt):
                raise HandoffDataError(
                    "handoff reporter record check receipt does not verify"
                )
    if revalidate_git:
        worktrees = {
            handoff["allowed_worktree"] for handoff in document["handoffs"]
        }
        if len(worktrees) != 1:
            raise HandoffDataError(
                "handoff reporter record must identify one source worktree"
            )
        revalidated = validate_document(document, Path(next(iter(worktrees))))
        if normalized_json(revalidated) != normalized_json(result):
            raise HandoffDataError(
                "handoff reporter record differs from Git revalidation"
            )
    return record


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
            "owner_database_id",
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
            "check_receipts",
            "result",
            "interruption",
        ),
    )
    expect_string(handoff["id"], f"{label}.id")
    expect_int(handoff["issue"], f"{label}.issue", 1)
    if handoff["pull_request"] is not None:
        expect_int(handoff["pull_request"], f"{label}.pull_request", 1)
    owner = _parse_actor(
        handoff["owner_id"],
        handoff["owner_database_id"],
        f"{label}.owner",
    )
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
    required_checks = {}
    check_evidence_ids = {}
    for check_index, raw_check in enumerate(
        expect_list(handoff["required_checks"], f"{label}.required_checks")
    ):
        check_label = f"{label}.required_checks[{check_index}]"
        check = expect_object(raw_check, check_label)
        expect_keys(
            check,
            check_label,
            ("id", "contract", "receipt_id", "evidence_id"),
        )
        check_id = expect_string(check["id"], f"{check_label}.id")
        check_ids.append(check_id)
        expect_enum(
            check["contract"],
            ALLOWED_CHECK_CONTRACTS,
            f"{check_label}.contract",
        )
        if check["receipt_id"] is not None:
            expect_string(check["receipt_id"], f"{check_label}.receipt_id")
        evidence_id = expect_string(
            check["evidence_id"],
            f"{check_label}.evidence_id",
        )
        required_evidence_ids.add(evidence_id)
        check_evidence_ids[check_id] = evidence_id
        required_checks[check_id] = check
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
    check_receipts = _parse_check_receipts(handoff["check_receipts"], label)

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
    for receipt_id, receipt in check_receipts.items():
        started_at = parse_time(
            receipt["started_at"],
            f"{label}.check_receipts[{receipt_id!r}].started_at",
        )
        completed_at = parse_time(
            receipt["completed_at"],
            f"{label}.check_receipts[{receipt_id!r}].completed_at",
        )
        if evidence_start is not None and started_at < evidence_start:
            raise HandoffDataError(
                f"{label}.check receipt {receipt_id!r} predates progressing"
            )
        if evidence_end is not None and completed_at > evidence_end:
            raise HandoffDataError(
                f"{label}.check receipt {receipt_id!r} follows its owner boundary"
            )

    handoff["_label"] = label
    handoff["_owner"] = owner
    handoff["_states"] = states
    handoff["_state_names"] = state_names
    handoff["_evidence"] = evidence
    handoff["_check_receipts"] = check_receipts
    handoff["_required_evidence_ids"] = required_evidence_ids
    handoff["_required_checks"] = required_checks
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
                "evaluation_source",
                "evaluated_at",
                "unattended_until",
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
        if availability["evaluation_source"] != "coordinator-runtime":
            raise HandoffDataError(
                f"{label}.availability.evaluation_source must be "
                "'coordinator-runtime'"
            )
        evaluated_at = parse_time(
            availability["evaluated_at"],
            f"{label}.availability.evaluated_at",
        )
        unattended_until = parse_time(
            availability["unattended_until"],
            f"{label}.availability.unattended_until",
        )
        if unattended_until < evaluated_at:
            raise HandoffDataError(
                f"{label}.availability.unattended_until precedes evaluated_at"
            )
        if availability["plan"] is not None:
            plan = expect_object(
                availability["plan"],
                f"{label}.availability.plan",
            )
            expect_keys(
                plan,
                f"{label}.availability.plan",
                (
                    "kind",
                    "available_until",
                    "evidence",
                ),
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
            evidence = expect_object(
                plan["evidence"],
                f"{label}.availability.plan.evidence",
            )
            expect_keys(
                evidence,
                f"{label}.availability.plan.evidence",
                (
                    "source",
                    "observed_at",
                    "autostop_enabled",
                    "stop_on_disconnect",
                ),
            )
            if evidence["source"] != "coordinator-runtime":
                raise HandoffDataError(
                    f"{label}.availability.plan.evidence.source must be "
                    "'coordinator-runtime'"
                )
            evidence_at = parse_time(
                evidence["observed_at"],
                f"{label}.availability.plan.evidence.observed_at",
            )
            if evidence_at != evaluated_at:
                raise HandoffDataError(
                    f"{label}.availability.plan evidence must match evaluated_at"
                )
            expect_bool(
                evidence["autostop_enabled"],
                f"{label}.availability.plan.evidence.autostop_enabled",
            )
            expect_bool(
                evidence["stop_on_disconnect"],
                f"{label}.availability.plan.evidence.stop_on_disconnect",
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
            (
                "id",
                "handoff_id",
                "actor_id",
                "actor_database_id",
                "action",
                "occurred_at",
            ),
        )
        action_ids.append(expect_string(action["id"], f"{label}.id"))
        expect_string(action["handoff_id"], f"{label}.handoff_id")
        action["_actor"] = _parse_actor(
            action["actor_id"],
            action["actor_database_id"],
            f"{label}.actor",
        )
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


def evaluate_delivery_graph(raw: Any) -> dict[str, Any]:
    graph = expect_object(raw, "delivery_graph")
    expect_keys(
        graph,
        "delivery_graph",
        (
            "relationships",
            "tasks",
            "dependencies",
            "workflow_runs",
            "watchers",
        ),
    )

    tasks: dict[str, dict[str, Any]] = {}
    tasks_by_issue_phase: dict[tuple[int, str], dict[str, Any]] = {}
    implementation_tasks_by_handoff: dict[str, dict[str, Any]] = {}
    for index, raw_task in enumerate(expect_list(graph["tasks"], "delivery_graph.tasks")):
        label = f"delivery_graph.tasks[{index}]"
        task = expect_object(raw_task, label)
        expect_keys(
            task,
            label,
            (
                "id",
                "issue",
                "pull_request",
                "phase",
                "status",
                "status_reason",
                "handoff_id",
                "candidate_sha",
            ),
        )
        task_id = expect_string(task["id"], f"{label}.id")
        if task_id in tasks:
            raise HandoffDataError(f"duplicate delivery task {task_id!r}")
        issue = expect_int(task["issue"], f"{label}.issue", 1)
        if task["pull_request"] is not None:
            expect_int(task["pull_request"], f"{label}.pull_request", 1)
        phase = expect_enum(
            task["phase"],
            DELIVERY_TASK_PHASES,
            f"{label}.phase",
        )
        status = expect_enum(
            task["status"],
            DELIVERY_TASK_STATUSES,
            f"{label}.status",
        )
        if status == "blocked":
            expect_enum(
                task["status_reason"],
                {"dependency", "owner_interrupted", "workflow_failed"},
                f"{label}.status_reason",
            )
        elif task["status_reason"] is not None:
            raise HandoffDataError(
                f"{label}.status_reason is only valid for blocked status"
            )
        if task["handoff_id"] is not None:
            expect_string(task["handoff_id"], f"{label}.handoff_id")
        expect_sha(
            task["candidate_sha"],
            f"{label}.candidate_sha",
            nullable=True,
        )
        identity = (issue, phase)
        if phase == "implementation":
            if task["handoff_id"] is None:
                raise HandoffDataError(
                    f"{label}.handoff_id is required for implementation"
                )
            if task["handoff_id"] in implementation_tasks_by_handoff:
                raise HandoffDataError(
                    f"implementation handoff {task['handoff_id']!r} "
                    "has duplicate tasks"
                )
            implementation_tasks_by_handoff[task["handoff_id"]] = task
        else:
            if task["handoff_id"] is not None:
                raise HandoffDataError(
                    f"{label}.handoff_id is only valid for implementation"
                )
            if identity in tasks_by_issue_phase:
                raise HandoffDataError(
                    f"delivery issue {issue} repeats phase {phase!r}"
                )
            tasks_by_issue_phase[identity] = task
        tasks[task_id] = task

    relationships = []
    relationship_identities = []
    for index, raw_relationship in enumerate(
        expect_list(graph["relationships"], "delivery_graph.relationships")
    ):
        label = f"delivery_graph.relationships[{index}]"
        relationship = expect_object(raw_relationship, label)
        expect_keys(
            relationship,
            label,
            ("child_issue", "parent_issue", "handoff_id", "type"),
        )
        child_issue = expect_int(
            relationship["child_issue"],
            f"{label}.child_issue",
            1,
        )
        parent_issue = expect_int(
            relationship["parent_issue"],
            f"{label}.parent_issue",
            1,
        )
        if child_issue == parent_issue:
            raise HandoffDataError(f"{label} cannot be self-referential")
        handoff_id = expect_string(
            relationship["handoff_id"],
            f"{label}.handoff_id",
        )
        relationship_type = expect_enum(
            relationship["type"],
            {"code_contract"},
            f"{label}.type",
        )
        relationship_identities.append(
            (child_issue, parent_issue, handoff_id, relationship_type)
        )
        relationships.append(relationship)
    if not relationships:
        raise HandoffDataError(
            "delivery_graph.relationships must name a code/contract dependency"
        )
    expect_unique(
        relationship_identities,
        "delivery_graph.relationships",
    )

    workflow_runs: dict[int, dict[str, Any]] = {}
    run_tasks = set()
    for index, raw_run in enumerate(
        expect_list(graph["workflow_runs"], "delivery_graph.workflow_runs")
    ):
        label = f"delivery_graph.workflow_runs[{index}]"
        run = expect_object(raw_run, label)
        expect_keys(
            run,
            label,
            (
                "id",
                "run_task",
                "head_sha",
                "status",
                "conclusion",
                "source",
            ),
        )
        run_id = expect_int(run["id"], f"{label}.id", 1)
        if run_id in workflow_runs:
            raise HandoffDataError(f"duplicate delivery workflow run {run_id}")
        run_task = expect_string(run["run_task"], f"{label}.run_task")
        task = tasks.get(run_task)
        if task is None or task["phase"] != "post_merge_build":
            raise HandoffDataError(
                f"{label}.run_task must identify a post_merge_build task"
            )
        if run_task in run_tasks:
            raise HandoffDataError(
                f"post-merge task {run_task!r} has duplicate workflow runs"
            )
        run_tasks.add(run_task)
        expect_sha(run["head_sha"], f"{label}.head_sha")
        status = expect_enum(run["status"], RUN_STATUSES, f"{label}.status")
        if status == "completed":
            expect_enum(
                run["conclusion"],
                RUN_CONCLUSIONS,
                f"{label}.conclusion",
            )
        elif run["conclusion"] is not None:
            raise HandoffDataError(
                f"{label} active status requires null conclusion"
            )
        if run["source"] != "github-actions-api":
            raise HandoffDataError(
                f"{label}.source must be authoritative 'github-actions-api'"
            )
        workflow_runs[run_id] = run

    watchers: dict[str, dict[str, Any]] = {}
    watched_runs = set()
    for index, raw_watcher in enumerate(
        expect_list(graph["watchers"], "delivery_graph.watchers")
    ):
        label = f"delivery_graph.watchers[{index}]"
        watcher = expect_object(raw_watcher, label)
        expect_keys(
            watcher,
            label,
            (
                "id",
                "run_id",
                "process_state",
            ),
        )
        watcher_id = expect_string(watcher["id"], f"{label}.id")
        if watcher_id in watchers:
            raise HandoffDataError(
                f"duplicate delivery watcher {watcher_id!r}"
            )
        run_id = expect_int(watcher["run_id"], f"{label}.run_id", 1)
        if run_id not in workflow_runs:
            raise HandoffDataError(
                f"{label}.run_id references unknown workflow run {run_id}"
            )
        if run_id in watched_runs:
            raise HandoffDataError(
                f"workflow run {run_id} has duplicate delivery watchers"
            )
        watched_runs.add(run_id)
        expect_enum(
            watcher["process_state"],
            DELIVERY_WATCHER_STATES,
            f"{label}.process_state",
        )
        watchers[watcher_id] = watcher

    rejection_codes = set()
    dependencies = []
    dependency_identities = []
    watcher_ids = set(watchers)
    for index, raw_dependency in enumerate(
        expect_list(graph["dependencies"], "delivery_graph.dependencies")
    ):
        label = f"delivery_graph.dependencies[{index}]"
        dependency = expect_object(raw_dependency, label)
        expect_keys(dependency, label, ("task", "depends_on", "type"))
        task_id = expect_string(dependency["task"], f"{label}.task")
        depends_on = expect_string(
            dependency["depends_on"],
            f"{label}.depends_on",
        )
        dependency_type = expect_enum(
            dependency["type"],
            DELIVERY_DEPENDENCY_TYPES,
            f"{label}.type",
        )
        identity = (task_id, depends_on, dependency_type)
        dependency_identities.append(identity)
        if task_id in watcher_ids or depends_on in watcher_ids:
            rejection_codes.add("watcher-todo-dependency")
            continue
        if task_id not in tasks:
            raise HandoffDataError(
                f"{label}.task references unknown delivery task {task_id!r}"
            )
        if depends_on not in tasks:
            raise HandoffDataError(
                f"{label}.depends_on references unknown delivery task "
                f"{depends_on!r}"
            )
        if task_id == depends_on:
            raise HandoffDataError(f"{label} cannot be self-referential")
        dependencies.append(dependency)
    expect_unique(dependency_identities, "delivery_graph.dependencies")

    dependency_tuples = {
        (item["task"], item["depends_on"], item["type"])
        for item in dependencies
    }
    required_edges = []
    relationship_reports = []
    child_task_ids = set()
    for relationship in relationships:
        child_issue = relationship["child_issue"]
        parent_issue = relationship["parent_issue"]
        child_task = implementation_tasks_by_handoff.get(
            relationship["handoff_id"]
        )
        parent_merge = tasks_by_issue_phase.get((parent_issue, "merge"))
        if child_task is None:
            raise HandoffDataError(
                f"child issue {child_issue} has no implementation task"
            )
        if child_task["issue"] != child_issue:
            raise HandoffDataError(
                f"handoff {relationship['handoff_id']!r} implementation "
                "task contradicts its child issue"
            )
        if parent_merge is None:
            raise HandoffDataError(
                f"parent issue {parent_issue} has no merge task"
            )
        child_task_ids.add(child_task["id"])
        required_edge = {
            "task": child_task["id"],
            "depends_on": parent_merge["id"],
            "type": "code_contract",
        }
        required_edges.append(required_edge)
        expected_tuple = (
            required_edge["task"],
            required_edge["depends_on"],
            required_edge["type"],
        )
        expected_present = expected_tuple in dependency_tuples
        if not expected_present:
            rejection_codes.add("missing-required-code-contract-edge")

        wrong_edges = []
        for dependency in dependencies:
            if dependency["task"] != child_task["id"]:
                continue
            dependency_task = tasks[dependency["depends_on"]]
            if dependency_task["issue"] != parent_issue:
                continue
            if (
                dependency["depends_on"] != parent_merge["id"]
                or dependency["type"] != "code_contract"
            ):
                wrong_edges.append(dependency)
        if wrong_edges:
            rejection_codes.add("wrong-code-contract-edge")

        relationship_reports.append(
            {
                "child_issue": child_issue,
                "parent_issue": parent_issue,
                "handoff_id": relationship["handoff_id"],
                "type": "code_contract",
                "implementation_task": {
                    "id": child_task["id"],
                    "issue": child_task["issue"],
                    "pull_request": child_task["pull_request"],
                    "status": child_task["status"],
                    "status_reason": child_task["status_reason"],
                    "handoff_id": child_task["handoff_id"],
                    "candidate_sha": child_task["candidate_sha"],
                },
                "required_edge": required_edge,
                "parent_merge_status": parent_merge["status"],
                "implementation_ready": (
                    parent_merge["status"] == "done"
                    and expected_present
                    and not wrong_edges
                ),
            }
        )

    parent_issues = sorted(
        {relationship["parent_issue"] for relationship in relationships}
    )
    recovery_reports = []
    parent_delivery_reports = []
    failed_post_merge_tasks = set()
    for parent_issue in parent_issues:
        post_merge_build = tasks_by_issue_phase.get(
            (parent_issue, "post_merge_build")
        )
        if post_merge_build is None:
            raise HandoffDataError(
                f"parent issue {parent_issue} has no post_merge_build task"
            )
        for phase in ("completion", "closure", "remote_completion"):
            task = tasks_by_issue_phase.get((parent_issue, phase))
            if task is None:
                raise HandoffDataError(
                    f"parent issue {parent_issue} has no {phase} task"
                )
            required_edge = (
                task["id"],
                post_merge_build["id"],
                "delivery_gate",
            )
            required_edges.append(
                {
                    "task": required_edge[0],
                    "depends_on": required_edge[1],
                    "type": required_edge[2],
                }
            )
            if required_edge not in dependency_tuples:
                rejection_codes.add("missing-parent-post-merge-gate")

        task_runs = [
            run
            for run in workflow_runs.values()
            if run["run_task"] == post_merge_build["id"]
        ]
        if post_merge_build["status"] == "pending":
            if task_runs:
                rejection_codes.add("watcher-run-mismatch")
        elif len(task_runs) != 1:
            rejection_codes.add("watcher-run-mismatch")
        run = task_runs[0] if len(task_runs) == 1 else None
        if run is not None and (
            post_merge_build["candidate_sha"] is None
            or post_merge_build["candidate_sha"] != run["head_sha"]
        ):
            rejection_codes.add("watcher-run-mismatch")
        run_watchers = (
            []
            if run is None
            else [
                watcher
                for watcher in watchers.values()
                if watcher["run_id"] == run["id"]
            ]
        )
        if run is not None and len(run_watchers) != 1:
            rejection_codes.add("missing-or-duplicate-watcher")
        failed_terminal = (
            run is not None
            and run["status"] == "completed"
            and run["conclusion"] != "success"
        )
        if failed_terminal:
            failed_post_merge_tasks.add(post_merge_build["id"])
        successful_terminal = (
            run is not None
            and run["status"] == "completed"
            and run["conclusion"] == "success"
        )
        active = run is not None and run["status"] in {"in_progress", "queued"}
        if successful_terminal and post_merge_build["status"] != "done":
            rejection_codes.add("watcher-run-mismatch")
        if failed_terminal and post_merge_build["status"] != "blocked":
            rejection_codes.add("watcher-run-mismatch")
        if active and post_merge_build["status"] != "in_progress":
            rejection_codes.add("watcher-run-mismatch")

        recovery = tasks_by_issue_phase.get(
            (parent_issue, "fix_forward_revert")
        )
        if recovery is None:
            raise HandoffDataError(
                f"parent issue {parent_issue} has no fix_forward_revert task"
            )
        if failed_terminal and recovery["status"] != "in_progress":
            rejection_codes.add("missing-master-recovery")
        recovery_reports.append(
            {
                "parent_issue": parent_issue,
                "required": failed_terminal,
                "task": recovery["id"],
                "status": recovery["status"],
            }
        )
        parent_delivery_reports.append(
            {
                "parent_issue": parent_issue,
                "post_merge_build_task": post_merge_build["id"],
                "head_sha": run["head_sha"] if run is not None else None,
                "authoritative_status": (
                    run["status"] if run is not None else None
                ),
                "conclusion": run["conclusion"] if run is not None else None,
                "delivery_eligible": successful_terminal,
            }
        )

    dependencies_by_task: dict[str, list[dict[str, Any]]] = {
        task_id: [] for task_id in tasks
    }
    for dependency in dependencies:
        dependencies_by_task[dependency["task"]].append(dependency)

    for task_id, task in tasks.items():
        blockers = [
            dependency["depends_on"]
            for dependency in dependencies_by_task[task_id]
            if tasks[dependency["depends_on"]]["status"] != "done"
        ]
        if task["status"] in {"done", "in_progress"} and blockers:
            rejection_codes.add("task-status-dependency-mismatch")
        if task["status"] == "not_required" and (
            dependencies_by_task[task_id]
            or task["phase"] != "fix_forward_revert"
        ):
            rejection_codes.add("task-status-dependency-mismatch")
        if task["status"] == "blocked":
            reason = task["status_reason"]
            if reason == "dependency" and not blockers:
                rejection_codes.add("task-status-dependency-mismatch")
            if (
                reason == "workflow_failed"
                and task_id not in failed_post_merge_tasks
            ):
                rejection_codes.add("task-status-dependency-mismatch")
            if (
                reason == "owner_interrupted"
                and task["phase"] != "implementation"
            ):
                rejection_codes.add("task-status-dependency-mismatch")

    ready_tasks = []
    blocked_tasks = []
    for task_id, task in sorted(tasks.items()):
        if task["status"] != "pending":
            continue
        blockers = sorted(
            dependency["depends_on"]
            for dependency in dependencies_by_task[task_id]
            if tasks[dependency["depends_on"]]["status"] != "done"
        )
        relationship_ready = all(
            item["implementation_ready"]
            for item in relationship_reports
            if item["required_edge"]["task"] == task_id
        )
        if task_id in child_task_ids and not relationship_ready:
            parent_merge_blockers = [
                item["required_edge"]["depends_on"]
                for item in relationship_reports
                if item["required_edge"]["task"] == task_id
                and item["parent_merge_status"] != "done"
            ]
            blockers.extend(parent_merge_blockers)
        blockers = sorted(set(blockers))
        if blockers:
            blocked_tasks.append({"id": task_id, "blocked_by": blockers})
        elif task_id in child_task_ids and not relationship_ready:
            blocked_tasks.append(
                {
                    "id": task_id,
                    "blocked_by": ["invalid-code-contract-edge"],
                }
            )
        else:
            ready_tasks.append(task_id)

    return {
        "relationships": relationship_reports,
        "required_edges": required_edges,
        "ready_tasks": ready_tasks,
        "blocked_tasks": blocked_tasks,
        "watchers": [
            {
                "id": watcher["id"],
                "run_id": watcher["run_id"],
                "run_task": workflow_runs[watcher["run_id"]]["run_task"],
                "process_state": watcher["process_state"],
                "head_sha": workflow_runs[watcher["run_id"]]["head_sha"],
                "authoritative_status": workflow_runs[watcher["run_id"]][
                    "status"
                ],
                "conclusion": workflow_runs[watcher["run_id"]]["conclusion"],
                "orthogonal_to_todos": True,
            }
            for watcher in watchers.values()
        ],
        "master_recovery": recovery_reports,
        "parent_delivery": parent_delivery_reports,
        "rejection_codes": sorted(rejection_codes),
    }


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
        "owner_id": handoff["_owner"]["identity"],
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
        "changed_paths": None,
        "commit_message_sha256": None,
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
            "prior_handoffs",
            "delivery_graph",
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
    delivery_graph = evaluate_delivery_graph(document["delivery_graph"])
    prior_handoffs = validate_prior_handoffs(document["prior_handoffs"])

    global_rejections = set()
    handoff_rejections = {handoff["id"]: set() for handoff in handoffs}

    def reject(code: str, handoff_id: str | None = None) -> None:
        global_rejections.add(code)
        if handoff_id is not None:
            handoff_rejections[handoff_id].add(code)

    for code in delivery_graph["rejection_codes"]:
        for handoff in handoffs:
            reject(code, handoff["id"])
    for handoff in handoffs:
        matching_relationships = [
            relationship
            for relationship in delivery_graph["relationships"]
            if relationship["child_issue"] == handoff["issue"]
            and relationship["handoff_id"] == handoff["id"]
        ]
        if not matching_relationships:
            reject("missing-handoff-code-contract", handoff["id"])
            continue
        if len(matching_relationships) != 1:
            reject("duplicate-handoff-code-contract", handoff["id"])
            continue
        task = matching_relationships[0]["implementation_task"]
        expected_candidate = (
            handoff["result"]["sha"]
            if handoff["result"] is not None
            else handoff["assigned_parent_sha"]
        )
        if (
            task["issue"] != handoff["issue"]
            or task["pull_request"] != handoff["pull_request"]
            or task["handoff_id"] != handoff["id"]
            or task["candidate_sha"] != expected_candidate
        ):
            reject("handoff-task-identity-mismatch", handoff["id"])
        if handoff["result"] is not None:
            expected_statuses = {"done"}
        elif handoff["interruption"] is not None:
            expected_statuses = {"blocked"}
        elif handoff["_state_names"][-1] == "progressing":
            expected_statuses = {"in_progress"}
        else:
            expected_statuses = {"pending"}
        if task["status"] not in expected_statuses:
            reject("handoff-task-status-mismatch", handoff["id"])
        if (
            handoff["interruption"] is not None
            and task["status_reason"] != "owner_interrupted"
        ):
            reject("handoff-task-status-mismatch", handoff["id"])
    for relationship in delivery_graph["relationships"]:
        if relationship["parent_merge_status"] == "done":
            continue
        for handoff in handoffs:
            if handoff["issue"] == relationship["child_issue"]:
                reject("code-contract-not-merged", handoff["id"])

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
            required_until = max(
                [
                    parse_time(
                        availability["evaluated_at"],
                        "coordinator availability.evaluated_at",
                    ),
                    parse_time(
                        availability["unattended_until"],
                        "coordinator availability.unattended_until",
                    ),
                    *activity_times,
                ]
            )
            if (
                availability["plan"]["evidence"]["autostop_enabled"]
                or availability["plan"]["evidence"][
                    "stop_on_disconnect"
                ]
                or available_until < required_until
            ):
                for handoff in handoffs:
                    reject("coordinator-unavailable", handoff["id"])
        if availability["mode"] == "always_on" and (
            availability["autostop_enabled"]
            or availability["stop_on_disconnect"]
        ):
            for handoff in handoffs:
                reject("coordinator-unavailable", handoff["id"])

    duplicate_handoff_ids = set()
    for index, handoff in enumerate(handoffs):
        for other in handoffs[index + 1:]:
            if _actors_match(handoff["_owner"], other["_owner"]):
                duplicate_handoff_ids.update((handoff["id"], other["id"]))
    for handoff_id in duplicate_handoff_ids:
        reject("duplicate-owner", handoff_id)
    for handoff in handoffs:
        if any(
            prior["issue"] == handoff["issue"]
            and prior["pull_request"] == handoff["pull_request"]
            and _actors_match(prior["_owner"], handoff["_owner"])
            for prior in prior_handoffs
        ):
            reject("closed-owner-reused", handoff["id"])

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

    for handoff in handoffs:
        relevant_history = [
            prior
            for prior in prior_handoffs
            if prior["issue"] == handoff["issue"]
            and prior["pull_request"] == handoff["pull_request"]
        ]
        for prior in relevant_history:
            ancestry = subprocess.run(
                reporter.git_command(
                    repository_root,
                    "merge-base",
                    "--is-ancestor",
                    prior["candidate_sha"],
                    handoff["assigned_parent_sha"],
                ),
                cwd=repository_root,
                env=reporter.git_environment(offline=True),
                check=False,
                capture_output=True,
            )
            if ancestry.returncode != 0:
                reject("prior-handoff-history-fork", handoff["id"])

    for parent_delivery in delivery_graph["parent_delivery"]:
        head_sha = parent_delivery["head_sha"]
        if head_sha is None:
            continue
        try:
            run_git(repository_root, "cat-file", "-e", f"{head_sha}^{{commit}}")
            ancestry = subprocess.run(
                reporter.git_command(
                    repository_root,
                    "merge-base",
                    "--is-ancestor",
                    head_sha,
                    actual_head,
                ),
                cwd=repository_root,
                env=reporter.git_environment(offline=True),
                check=False,
                capture_output=True,
            )
        except (HandoffDataError, OSError):
            ancestry = None
        if ancestry is None or ancestry.returncode != 0:
            for handoff in handoffs:
                reject("watcher-run-mismatch", handoff["id"])

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
                    result["commit_message_sha256"] = hashlib.sha256(
                        message.encode("utf-8")
                    ).hexdigest()
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
                    result["changed_paths"] = changed_paths
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
            referenced_receipts = set()
            for check_id, evidence_id in handoff["_check_evidence_ids"].items():
                evidence = handoff["_evidence"].get(evidence_id)
                if (
                    evidence is None
                    or evidence["kind"] != "check"
                    or evidence["status"] != "passed"
                    or evidence["exit_code"] != 0
                ):
                    reject("incomplete-check", handoff_id)
                check = handoff["_required_checks"][check_id]
                receipt_id = check["receipt_id"]
                receipt = handoff["_check_receipts"].get(receipt_id)
                if receipt_id is None or receipt is None:
                    reject("invalid-check-receipt", handoff_id)
                    continue
                referenced_receipts.add(receipt_id)
                for code in _verify_check_receipt(
                    receipt,
                    check=check,
                    repository_root=repository_root,
                    parent_sha=handoff["assigned_parent_sha"],
                    candidate_sha=handoff["result"]["sha"],
                ):
                    reject(code, handoff_id)
            if set(handoff["_check_receipts"]) != referenced_receipts:
                reject("invalid-check-receipt", handoff_id)
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
            if set(interruption["interrupted_check_ids"]) != set(
                handoff["_required_checks"]
            ):
                reject("interrupted-check-not-incomplete", handoff_id)
            for check_id in interruption["interrupted_check_ids"]:
                check = handoff["_required_checks"].get(check_id)
                if check is None:
                    reject("interrupted-check-not-incomplete", handoff_id)
                    continue
                if check["receipt_id"] is not None:
                    reject("interrupted-check-not-incomplete", handoff_id)
                evidence_id = handoff["_check_evidence_ids"].get(check_id)
                evidence = handoff["_evidence"].get(evidence_id)
                if (
                    evidence is None
                    or evidence["kind"] != "check"
                    or evidence["status"] != "incomplete"
                    or evidence["exit_code"] is not None
                ):
                    reject("interrupted-check-not-incomplete", handoff_id)
            if handoff["_check_receipts"]:
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
        if _actors_match(replacement["_owner"], handoff["_owner"]):
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
            _actors_match(action["_actor"], handoff["_owner"])
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
            parent["delivery_eligible"]
            for parent in delivery_graph["parent_delivery"]
        )
        and all(
            run["status"] == "completed" and run["conclusion"] == "success"
            for run in runs.values()
        )
    )
    recovery_count = sum(
        handoff["interruption"] is not None for handoff in handoffs
    )
    git_authority = {
        "worktree_identity": worktree_identity(repository_root),
        "branch": actual_branch,
        "head_sha": actual_head,
        "clean": not bool(status),
        "conflicts": sorted(conflicts),
        "dirty_paths": sorted(dirty_paths),
        "handoffs": [
            {
                "id": result["id"],
                "assigned_parent_sha": handoffs_by_id[result["id"]][
                    "assigned_parent_sha"
                ],
                "result_sha": result["result_sha"],
                "changed_paths": result["changed_paths"],
                "changed_lines": result["changed_lines"],
                "commit_message_sha256": result["commit_message_sha256"],
            }
            for result in results.values()
        ],
    }
    git_seal = seal_git_authority(git_authority)
    report = {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "coordinator_id": coordinator_id,
        "delivery_graph": delivery_graph,
        "handoffs": [results[handoff["id"]] for handoff in handoffs],
        "watchers": watcher_results,
        "git_authority": git_authority,
        "git_seal": git_seal,
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
    report["result_seal"] = seal_handoff_result(report)
    return report


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
