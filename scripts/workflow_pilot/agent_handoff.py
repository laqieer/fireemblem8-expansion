#!/usr/bin/env python3
"""Validate bounded exact-SHA implementation handoffs against real Git state."""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import contextlib
import hashlib
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from scripts.workflow_pilot import reporter

SCHEMA_VERSION = 2
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
        "request_review",
        "update_pull_request",
    }
)
REMOTE_ACTIONS = PROHIBITED_REMOTE_ACTIONS | {
    "read_github",
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
IN_PROGRESS_STATE_PREFIXES = {
    COMPLETE_STATE_SEQUENCE[:1],
    COMPLETE_STATE_SEQUENCE[:2],
    COMPLETE_STATE_SEQUENCE[:3],
}
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
INPUT_SEAL_DOMAIN = b"workflow-pilot-agent-handoff-input-v2\0"
RESULT_SEAL_DOMAIN = b"workflow-pilot-agent-handoff-result-v2\0"
GIT_SEAL_DOMAIN = b"workflow-pilot-agent-handoff-git-v2\0"
CHECK_RECEIPT_SEAL_DOMAIN = b"workflow-pilot-agent-check-receipt-v2\0"
HISTORY_RECEIPT_SEAL_DOMAIN = b"workflow-pilot-agent-history-receipt-v2\0"
COORDINATOR_RECEIPT_SEAL_DOMAIN = (
    b"workflow-pilot-agent-coordinator-attestation-v2\0"
)
PR_OBSERVATION_DOMAIN = b"workflow-pilot-github-pr-observation-v1\0"
PUBLICATION_ATTESTATION_DOMAIN = (
    b"workflow-pilot-authority-publication-v1\0"
)
RESULT_ATTESTATION_DOMAIN = b"workflow-pilot-canonical-result-v1\0"
REPORTER_TRUST_ANCHOR_DOMAIN = (
    b"workflow-pilot-reporter-trust-anchor-v1\0"
)
HISTORY_OBSERVATION_SEAL_DOMAIN = (
    b"workflow-pilot-agent-history-observation-v2\0"
)
ZERO_SEAL = "0" * 64
HISTORY_REF_PREFIX = "refs/heads/workflow-pilot/authority"
HISTORY_ANCHOR_REF_PREFIX = "refs/heads/workflow-pilot/authority-anchor"
REPOSITORY_IDENTITY_REF = "refs/workflow-pilot/repository-identity"
RAW_DIFF_CHECK_PATH = Path(__file__).resolve().with_name("raw_diff_check.py")
RAW_DIFF_CHECK_REPOSITORY_PATH = "scripts/workflow_pilot/raw_diff_check.py"
HANDOFF_SCHEMA_REPOSITORY_PATH = (
    "scripts/workflow_pilot/agent_handoff.schema.json"
)
COORDINATOR_INSTALLATION_ENV = "WORKFLOW_PILOT_COORDINATOR_INSTALLATION"
REMOTE_COVERAGE_SOURCES = (
    "github-timeline",
    "github-actions-runs",
    "git-refs",
    "github-audit-log",
)
PROVEN_HOST_ONLY_PREFIXES = (
    ".github/",
    "docs/",
    "scripts/docs_check_tests/",
    "scripts/workflow_pilot/",
    "tests/workflows/",
)
TRUSTED_INSTALLATION_MAX_BYTES = 1024 * 1024
TRUSTED_INSTALLATION_READ_BYTES = TRUSTED_INSTALLATION_MAX_BYTES + 1
HISTORY_CARRIER_MAX_BYTES = 1024 * 1024
REMOTE_GIT_TIMEOUT_SECONDS = 30.0
REMOTE_GIT_TIMEOUT_CODE = "remote-git-timeout"
REMOTE_GIT_PREFLIGHT_TIMEOUT_CODE = "remote-git-preflight-timeout"
ALLOWED_CHECK_TIMEOUT_SECONDS = 30.0
ALLOWED_CHECK_TIMEOUT_CODE = "allowed-check-timeout"
ALLOWED_CHECK_REVERIFY_TIMEOUT_CODE = "allowed-check-reverify-timeout"
RSA_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex(
    "3031300d060960864801650304020105000420"
)
AUTHORITY_READ_ATTEMPTS = 3
LIVE_ATTESTATION_MAX_AGE_SECONDS = 2
TRUSTED_PUSH_SUMMARY_EXEMPT_REJECTIONS = frozenset(
    {"authoritative-run-failed", "authoritative-run-incomplete"}
)
STRUCTURAL_SUMMARY_LIVE_ONLY_REJECTIONS = frozenset(
    {"remote-coverage-incomplete"}
)
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
def _kill_transport_process_group(
    process: subprocess.Popen[bytes],
) -> None:
    with contextlib.suppress(OSError, ProcessLookupError):
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        return
    with contextlib.suppress(OSError, ProcessLookupError):
        process.kill()
def _run_bounded_process(
    *,
    argv: list[str] | tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    stdin: bytes | None,
    timeout_seconds: float,
    timeout_code: str,
    label: str,
    execute_error_label: str,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[bytes]:
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            pass_fds=pass_fds,
        )
    except OSError as error:
        raise HandoffDataError(f"cannot execute {execute_error_label}: {error}") from error
    try:
        stdout, stderr = process.communicate(stdin, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        _kill_transport_process_group(process)
        process.communicate()
        raise HandoffDataError(
            f"{timeout_code}: {label} exceeded {timeout_seconds:g}s"
        ) from error
    return subprocess.CompletedProcess(
        args=process.args,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )
def _run_bounded_git_transport(
    repository_root: Path,
    *arguments: str,
    timeout_code: str,
) -> subprocess.CompletedProcess[bytes]:
    objects = _open_transport_object_store(repository_root)
    try:
        with tempfile.TemporaryDirectory(prefix="workflow-pilot-git-") as temporary:
            sealed = Path(temporary); (sealed / "refs").mkdir()
            (sealed / "HEAD").write_text("ref: refs/heads/sealed\n")
            (sealed / "config").write_text("[core]\nrepositoryformatversion=0\nbare=true\n")
            environment = {"CURL_HOME": str(sealed), "GIT_DIR": str(sealed),
                "GIT_OBJECT_DIRECTORY": f"/proc/self/fd/{objects}", "HOME": str(sealed),
                "GIT_SSH_COMMAND": "/usr/bin/ssh -F /dev/null -oBatchMode=yes",
                "GIT_SSH_VARIANT": "ssh", "XDG_CONFIG_HOME": str(sealed),
                **reporter.git_environment(offline=False)}
            return _run_bounded_process(
                argv=reporter.git_command(sealed, *arguments), cwd=sealed, env=environment,
                stdin=None, timeout_seconds=REMOTE_GIT_TIMEOUT_SECONDS,
                timeout_code=timeout_code, label=f"Git {' '.join(arguments)}",
                execute_error_label="Git", pass_fds=(objects,))
    finally:
        os.close(objects)
def _run_git_online(repository_root: Path, *arguments: str) -> bytes:
    completed = _run_bounded_git_transport(
        repository_root,
        *arguments,
        timeout_code=REMOTE_GIT_TIMEOUT_CODE,
    )
    if completed.returncode == 0:
        return completed.stdout
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    raise HandoffDataError(
        f"Git {' '.join(arguments)} failed"
        + (f": {detail}" if detail else "")
    )
def authoritative_current_time(
    value: datetime | None,
    *,
    label: str,
) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(None)
    ):
        raise HandoffDataError(f"{label} must be an aware UTC datetime")
    return value.astimezone(timezone.utc)
def require_fresh_live_timestamp(
    observed_at: datetime,
    *,
    label: str,
    current_time: datetime,
) -> None:
    if (
        observed_at > current_time
        or (current_time - observed_at).total_seconds()
        > LIVE_ATTESTATION_MAX_AGE_SECONDS
    ):
        raise HandoffDataError(f"{label} is future-dated or stale")
def whole_second_duration(
    start: datetime,
    end: datetime,
    *,
    label: str,
) -> int | None:
    try:
        elapsed = reporter.duration_seconds(start, end, label)
    except reporter.PilotDataError as error:
        raise HandoffDataError(str(error)) from error
    if elapsed != elapsed.to_integral_value():
        return None
    return int(elapsed)
def git_commit_is_ancestor(
    repository_root: Path,
    ancestor: str,
    descendant: str,
) -> bool:
    for object_id, label in (
        (ancestor, "ancestor commit"),
        (descendant, "descendant commit"),
    ):
        expect_sha(object_id, label)
        run_git(
            repository_root,
            "cat-file",
            "-e",
            f"{object_id}^{{commit}}",
        )
    completed = subprocess.run(
        reporter.git_command(
            repository_root,
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ),
        cwd=repository_root,
        env=reporter.git_environment(offline=True),
        check=False,
        capture_output=True,
    )
    if completed.returncode in {0, 1}:
        return completed.returncode == 0
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    raise HandoffDataError(
        "Git merge-base --is-ancestor failed"
        + (f": {detail}" if detail else "")
    )
def publication_binding_expectation(
    *,
    delivery_expectation: dict[str, Any],
    pull_request: int,
    head_branch: str,
    head_oid: str,
    coordinator_database_id: int,
    current_base_oid: str,
) -> dict[str, Any]:
    return {
        "repository_id": delivery_expectation["repository_id"],
        "repository_full_name": delivery_expectation["repository_full_name"],
        "pull_request": pull_request,
        "state": "OPEN",
        "merged": False,
        "base_branch": delivery_expectation["immediate_base_branch"],
        "frozen_base_oid": delivery_expectation["immediate_base_oid"],
        "current_base_oid": current_base_oid,
        "head_branch": head_branch,
        "head_repository_full_name": delivery_expectation[
            "head_repository_full_name"
        ],
        "head_oid": head_oid,
        "coordinator_database_id": coordinator_database_id,
    }
def publication_binding_expectation_for_observation(
    delivery_expectation: dict[str, Any],
    binding: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if binding is None:
        return None
    return publication_binding_expectation(
        delivery_expectation=delivery_expectation,
        pull_request=binding["pull_request"],
        head_branch=binding["head_branch"],
        head_oid=binding["head_oid"],
        coordinator_database_id=binding["coordinator_database_id"],
        current_base_oid=binding["base_oid"],
    )
def publication_observation_digest(
    binding: dict[str, Any] | None,
) -> str | None:
    if binding is None:
        return None
    return hashlib.sha256(normalized_json(binding)).hexdigest()
def publication_history_receipt_digest(
    history_receipt: dict[str, Any] | None = None,
) -> str | None:
    if history_receipt is None:
        return None
    return hashlib.sha256(normalized_json(history_receipt)).hexdigest()
def publication_history_carrier_digest(
    history_carrier: dict[str, Any] | None = None,
) -> str | None:
    if history_carrier is None:
        return None
    return hashlib.sha256(normalized_json(history_carrier)).hexdigest()
def _validate_history_carrier_bounds(
    history_carrier: dict[str, Any],
    *,
    label: str,
) -> None:
    if len(normalized_json(history_carrier)) > HISTORY_CARRIER_MAX_BYTES:
        raise HandoffDataError(f"{label} exceeds 1 MiB")
def make_history_carrier(
    document: dict[str, Any],
    result: dict[str, Any],
    handoff_id: str,
) -> dict[str, Any]:
    history_carrier = {
        "schema_version": 1,
        "selected_handoff_id": expect_string(
            handoff_id,
            "history carrier selected_handoff_id",
        ),
        "document": copy.deepcopy(
            expect_object(document, "history carrier document")
        ),
        "result": copy.deepcopy(
            expect_object(result, "history carrier result")
        ),
    }
    _validate_history_carrier_bounds(
        history_carrier,
        label="history carrier",
    )
    return history_carrier
def _parse_history_carrier(
    raw: Any,
    *,
    label: str,
) -> dict[str, Any]:
    history_carrier = copy.deepcopy(expect_object(raw, label))
    expect_keys(
        history_carrier,
        label,
        (
            "schema_version",
            "selected_handoff_id",
            "document",
            "result",
        ),
    )
    if (
        expect_int(
            history_carrier["schema_version"],
            f"{label}.schema_version",
            1,
        )
        != 1
    ):
        raise HandoffDataError(f"{label}.schema_version must be 1")
    expect_string(
        history_carrier["selected_handoff_id"],
        f"{label}.selected_handoff_id",
    )
    expect_object(
        history_carrier["document"],
        f"{label}.document",
    )
    expect_object(
        history_carrier["result"],
        f"{label}.result",
    )
    _validate_history_carrier_bounds(
        history_carrier,
        label=label,
    )
    return history_carrier
def _null_authority_event(kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "handoff_seal": None,
        "handoff_id": None,
        "handoff_kind": None,
        "lifecycle_state": None,
        "candidate_sha": None,
        "closed_at": None,
        "operation_nonce": None,
        "consume_store_id": None,
        "consume_sequence": None,
        "consume_anchor": None,
        "assignment": None,
        "interruption_snapshot": None,
        "history_receipt": None,
        "history_carrier": None,
    }
def _history_event_from_receipt(
    receipt: dict[str, Any],
    history_carrier: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "handoff",
        "handoff_seal": receipt["seal"],
        "handoff_id": receipt["handoff_id"],
        "handoff_kind": receipt["handoff_kind"],
        "lifecycle_state": receipt["lifecycle_state"],
        "candidate_sha": receipt["candidate_sha"],
        "closed_at": receipt["closed_at"],
        "operation_nonce": receipt["operation_nonce"],
        "consume_store_id": receipt["consume_store_id"],
        "consume_sequence": receipt["consume_sequence"],
        "consume_anchor": receipt["consume_anchor"],
        "assignment": copy.deepcopy(receipt["assignment"]),
        "interruption_snapshot": copy.deepcopy(
            receipt["interruption_snapshot"]
        ),
        "history_receipt": copy.deepcopy(receipt),
        "history_carrier": copy.deepcopy(history_carrier),
    }
def _public_history_event(
    event: dict[str, Any],
    *,
    history_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    public = copy.deepcopy(event)
    public["history_carrier"] = None
    if history_receipt is not None:
        public["history_receipt"] = copy.deepcopy(history_receipt)
    return public
def _public_authority_record(
    authority: dict[str, Any],
) -> dict[str, Any]:
    public = copy.deepcopy(authority)
    public["event"] = _public_history_event(public["event"])
    return public
def _canonical_history_observation(
    reference: str,
    object_id: str,
    anchor_reference: str,
    anchor_object_id: str,
) -> dict[str, Any]:
    expect_string(reference, "history authority observation.ref")
    object_id = expect_sha(
        object_id,
        "history authority observation.object_id",
    )
    expect_string(
        anchor_reference,
        "history authority observation.anchor_ref",
    )
    anchor_object_id = expect_sha(
        anchor_object_id,
        "history authority observation.anchor_object_id",
    )
    return _history_observation(
        reference,
        object_id,
        anchor_reference,
        anchor_object_id,
        1,
    )
def _canonical_public_history_authority(
    authority: dict[str, Any],
    *,
    object_id: str,
    anchor_object_id: str,
    history_events: Any,
    event_history_receipt: dict[str, Any] | None,
    label: str,
) -> dict[str, Any]:
    issue = expect_int(authority["issue"], f"{label}.issue", 1)
    pull_request = None
    if authority["pr_binding"] is not None:
        binding = expect_object(
            authority["pr_binding"],
            f"{label}.pr_binding",
        )
        pull_request = expect_int(
            binding["pull_request"],
            f"{label}.pr_binding.pull_request",
            1,
        )
    reference = history_authority_ref(issue, pull_request)
    anchor_reference = history_anchor_ref(issue)
    object_id = expect_sha(object_id, f"{label}.object_id")
    anchor_object_id = expect_sha(
        anchor_object_id,
        f"{label}.anchor_object_id",
    )
    canonical_history_events = copy.deepcopy(
        expect_list(history_events, f"{label}.history_events")
    )
    if len(canonical_history_events) != expect_int(
        authority["handoff_sequence"],
        f"{label}.handoff_sequence",
        0,
    ):
        raise HandoffDataError(
            f"{label}.history_events do not match the canonical handoff sequence"
        )
    if authority["event"]["kind"] == "handoff":
        if event_history_receipt is None:
            raise HandoffDataError(
                f"{label}.event history_receipt is missing"
            )
        event_history_receipt = copy.deepcopy(
            expect_object(
                event_history_receipt,
                f"{label}.event history_receipt",
            )
        )
    return {
        "ref": reference,
        "object_id": object_id,
        "anchor_ref": anchor_reference,
        "anchor_object_id": anchor_object_id,
        "observation": _canonical_history_observation(
            reference,
            object_id,
            anchor_reference,
            anchor_object_id,
        ),
        "history_events": canonical_history_events,
        **_public_authority_record(authority),
        "event": _public_history_event(
            authority["event"],
            history_receipt=event_history_receipt,
        ),
    }
def require_publication_attestation_binding(
    publication: dict[str, Any],
    *,
    operation: str,
    new_head_seal: str | None,
    history_receipt: dict[str, Any] | None = None,
    history_carrier: dict[str, Any] | None = None,
    pull_request_observation: dict[str, Any] | None = None,
    binding_expectation: dict[str, Any] | None = None,
    label: str,
) -> None:
    if operation == "advance":
        if history_receipt is None:
            raise HandoffDataError(
                f"{label} advance requires a handoff receipt binding"
            )
        if history_carrier is None:
            raise HandoffDataError(
                f"{label} advance requires a handoff carrier binding"
            )
    elif history_receipt is not None:
        raise HandoffDataError(
            f"{label} non-handoff publication cannot bind a handoff receipt"
        )
    elif history_carrier is not None:
        raise HandoffDataError(
            f"{label} non-handoff publication cannot bind a handoff carrier"
        )
    expected = {
        "operation": operation,
        "new_head_seal": new_head_seal,
        "history_carrier_digest": publication_history_carrier_digest(
            history_carrier
        ),
        "history_receipt_digest": publication_history_receipt_digest(
            history_receipt
        ),
        "pull_request_observation_digest": publication_observation_digest(
            pull_request_observation
        ),
        "binding_expectation": binding_expectation,
    }
    if any(publication[field] != value for field, value in expected.items()):
        raise HandoffDataError(label)
def validate_historical_pr_binding_target(
    current_authority: dict[str, Any],
    prior_authority: dict[str, Any],
    *,
    label: str,
) -> tuple[str, str]:
    if (
        current_authority["head_seal"] is None
        or current_authority["handoff_sequence"] < 1
    ):
        raise HandoffDataError(
            f"{label} PR binding lacks a carried sealed handoff"
        )
    prior_event = expect_object(
        prior_authority["event"],
        f"{label} prior authority event",
    )
    if prior_event["kind"] != "handoff":
        raise HandoffDataError(
            f"{label} PR binding parent is not a sealed handoff"
        )
    if (
        prior_authority["sequence"] + 1 != current_authority["sequence"]
        or prior_authority["handoff_sequence"]
        != current_authority["handoff_sequence"]
        or prior_authority["head_seal"] != current_authority["head_seal"]
        or prior_event["handoff_seal"] != current_authority["head_seal"]
    ):
        raise HandoffDataError(
            f"{label} PR binding does not extend its immediately prior "
            "sealed handoff"
        )
    prior_assignment = expect_object(
        prior_event["assignment"],
        f"{label} prior sealed handoff assignment",
    )
    expected_branch = expect_string(
        prior_assignment["expected_branch"],
        f"{label} prior sealed handoff assignment.expected_branch",
    )
    expected_candidate_sha = expect_sha(
        prior_event["candidate_sha"],
        f"{label} prior sealed handoff candidate_sha",
    )
    return expected_branch, expected_candidate_sha
def load_history_authority_transition_summary(
    repository_root: Path,
    object_id: str,
    repository: str,
    issue: int,
    *,
    label: str,
) -> dict[str, Any]:
    if (
        run_git(repository_root, "cat-file", "-t", object_id)
        .decode("ascii")
        .strip()
        != "commit"
    ):
        raise HandoffDataError(f"{label} must be a commit")
    authority = _parse_authority_json(
        run_git(
            repository_root,
            "show",
            f"{object_id}:authority.json",
        ),
        label,
    )
    expect_keys(
        authority,
        label,
        (
            "schema_version",
            "repository",
            "issue",
            "sequence",
            "handoff_sequence",
            "head_seal",
            "pr_binding",
            "signer",
            "ruleset_id",
            "authorized_bypass_actors",
            "delivery_expectation",
            "publication_attestation",
            "event",
            "previous_object_id",
        ),
    )
    if expect_int(authority["schema_version"], f"{label}.schema_version", 1) != 2:
        raise HandoffDataError(f"{label} schema_version must be 2")
    if (
        expect_string(authority["repository"], f"{label}.repository")
        != repository
        or expect_int(authority["issue"], f"{label}.issue", 1) != issue
    ):
        raise HandoffDataError(f"{label} identity mismatch")
    expect_int(authority["sequence"], f"{label}.sequence", 0)
    expect_int(authority["handoff_sequence"], f"{label}.handoff_sequence", 0)
    head_seal = authority["head_seal"]
    if head_seal is not None and (
        not isinstance(head_seal, str)
        or reporter.SHA256_RE.fullmatch(head_seal) is None
    ):
        raise HandoffDataError(f"{label} has invalid head_seal")
    event = expect_object(authority["event"], f"{label}.event")
    expect_keys(
        event,
        f"{label}.event",
        (
            "kind",
            "handoff_seal",
            "handoff_id",
            "handoff_kind",
            "lifecycle_state",
            "candidate_sha",
            "closed_at",
            "operation_nonce",
            "consume_store_id",
            "consume_sequence",
            "consume_anchor",
            "assignment",
            "interruption_snapshot",
            "history_receipt",
            "history_carrier",
        ),
    )
    expect_enum(event["kind"], {"genesis", "handoff", "pr_binding"}, f"{label}.event.kind")
    return authority
def _publish_authority_updates(
    repository_root: Path,
    installation_path: Path | None,
    updates: list[tuple[str, str]],
    *,
    dry_run: bool,
) -> str:
    if len(updates) != 2:
        raise HandoffDataError(
            "authority publication requires exactly two atomic ref updates"
        )
    remote = _transport_endpoint(repository_root, installation_path)
    completed = _run_bounded_git_transport(
        repository_root,
        "push",
        "--atomic",
        *(("--dry-run",) if dry_run else ()),
        "--",
        remote,
        *(f"{object_id}:{reference}" for object_id, reference in updates),
        timeout_code=(
            REMOTE_GIT_PREFLIGHT_TIMEOUT_CODE if dry_run else REMOTE_GIT_TIMEOUT_CODE
        ),
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise HandoffDataError(
            "origin does not support the required atomic authority push"
            + (f": {detail}" if detail else "")
        )
    return remote
def require_atomic_push_capability(
    repository_root: Path,
    installation_path: Path | None,
    updates: list[tuple[str, str]],
) -> str:
    return _publish_authority_updates(
        repository_root, installation_path, updates, dry_run=True
    )
def publish_authority_updates(
    repository_root: Path,
    installation_path: Path | None,
    updates: list[tuple[str, str]],
) -> None:
    _publish_authority_updates(repository_root, installation_path, updates, dry_run=False)
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
    database_id = expect_int(
        database_id_value,
        f"{label}.database_id",
        1,
    )
    canonical_login = login.casefold()
    return {
        "login": canonical_login,
        "database_id": database_id,
        "identity": f"github-id:{database_id}",
    }
def _actors_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left["database_id"] == right["database_id"]
def user_bypass_actor(database_id: int) -> dict[str, Any]:
    return {
        "actor_type": "User",
        "actor_id": database_id,
        "database_id": database_id,
        "bypass_mode": "always",
    }
NON_USER_BYPASS_ACTOR_TYPES = frozenset(
    {
        "DeployKey",
        "Integration",
        "OrganizationAdmin",
        "RepositoryRole",
    }
)
def _parse_ruleset_bypass_actor(
    raw: Any,
    label: str,
) -> dict[str, Any]:
    bypass = expect_object(raw, label)
    actor_type = expect_string(bypass.get("actor_type"), f"{label}.actor_type")
    if actor_type == "User":
        expect_keys(
            bypass,
            label,
            ("actor_type", "actor_id", "database_id", "bypass_mode"),
        )
        actor_id = expect_int(
            bypass["actor_id"],
            f"{label}.actor_id",
            1,
        )
        database_id = expect_int(
            bypass["database_id"],
            f"{label}.database_id",
            1,
        )
        if bypass["bypass_mode"] != "always":
            raise HandoffDataError(
                f"{label}.bypass_mode must be always"
            )
        if actor_id != database_id:
            raise HandoffDataError("GitHub User bypass actor IDs do not match")
        return user_bypass_actor(database_id)
    expect_keys(
        bypass,
        label,
        ("actor_type", "actor_id", "bypass_mode"),
    )
    if actor_type not in NON_USER_BYPASS_ACTOR_TYPES:
        raise HandoffDataError(
            f"{label}.actor_type is not an explicit non-user type"
        )
    actor_id = expect_int(
        bypass["actor_id"],
        f"{label}.actor_id",
        1,
    )
    if bypass["bypass_mode"] != "always":
        raise HandoffDataError(
            f"{label}.bypass_mode must be always"
        )
    return {
        "actor_type": actor_type,
        "actor_id": actor_id,
        "bypass_mode": "always",
    }
def _parse_ruleset_bypass_actors(
    raw_actors: Any,
    label: str,
) -> list[dict[str, Any]]:
    actors = [
        _parse_ruleset_bypass_actor(raw_actor, f"{label}[{index}]")
        for index, raw_actor in enumerate(expect_list(raw_actors, label))
    ]
    expect_unique(
        ((item["actor_type"], item["actor_id"]) for item in actors),
        label,
    )
    return actors
def _normalized_json_set(
    values: Iterable[dict[str, Any]],
) -> list[bytes]:
    return sorted(normalized_json(value) for value in values)
def _expected_installation_authorized_bypass_actors(
    installation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        *(
            user_bypass_actor(actor["database_id"])
            for actor in installation["_authorized"]
        ),
        *copy.deepcopy(installation["_authorized_non_user_bypass"]),
    ]
def _require_authority_matches_installation(
    authority: dict[str, Any],
    installation: dict[str, Any],
    *,
    label: str,
) -> None:
    if (
        _parse_signer_public(authority["signer"], f"{label}.signer")
        != installation["_signer"]
    ):
        raise HandoffDataError(
            f"{label} signer does not match coordinator installation"
        )
    if (
        expect_int(authority["ruleset_id"], f"{label}.ruleset_id", 1)
        != installation["authority_protection"]["ruleset_id"]
    ):
        raise HandoffDataError(
            f"{label} ruleset_id does not match coordinator installation"
        )
    authorized_bypass_actors = _parse_ruleset_bypass_actors(
        authority["authorized_bypass_actors"],
        f"{label}.authorized_bypass_actors",
    )
    if _normalized_json_set(
        authorized_bypass_actors
    ) != _normalized_json_set(
        _expected_installation_authorized_bypass_actors(installation)
    ):
        raise HandoffDataError(
            f"{label} bypass actors do not match coordinator installation"
        )
    delivery = expect_object(
        authority["delivery_expectation"],
        f"{label}.delivery_expectation",
    )
    expect_keys(
        delivery,
        f"{label}.delivery_expectation",
        (
            "repository_id",
            "repository_full_name",
            "immediate_base_branch",
            "immediate_base_oid",
            "delivery_branch",
            "head_repository_full_name",
        ),
    )
    if (
        expect_int(
            delivery["repository_id"],
            f"{label}.delivery_expectation.repository_id",
            1,
        )
        != installation["repository_database_id"]
        or expect_string(
            delivery["repository_full_name"],
            f"{label}.delivery_expectation.repository_full_name",
        )
        != installation["repository"]
        or expect_string(
            delivery["immediate_base_branch"],
            f"{label}.delivery_expectation.immediate_base_branch",
        )
        != installation["delivery"]["immediate_base_branch"]
        or expect_string(
            delivery["delivery_branch"],
            f"{label}.delivery_expectation.delivery_branch",
        )
        != installation["delivery"]["delivery_branch"]
        or expect_string(
            delivery["head_repository_full_name"],
            f"{label}.delivery_expectation.head_repository_full_name",
        )
        != installation["delivery"]["head_repository_full_name"]
    ):
        raise HandoffDataError(
            f"{label} delivery expectation does not match coordinator installation"
        )
def _parse_signer_public(raw: Any, label: str) -> dict[str, Any]:
    signer = expect_object(raw, label)
    expect_keys(
        signer,
        label,
        (
            "algorithm",
            "key_id",
            "modulus_hex",
            "exponent",
            "service_identity",
            "isolation_attestation",
        ),
    )
    if signer["algorithm"] != "rsa-pkcs1v15-sha256":
        raise HandoffDataError(
            f"{label}.algorithm must be rsa-pkcs1v15-sha256"
        )
    key_id = signer["key_id"]
    if (
        not isinstance(key_id, str)
        or reporter.SHA256_RE.fullmatch(key_id) is None
    ):
        raise HandoffDataError(f"{label}.key_id must be a SHA-256")
    modulus_hex = expect_string(signer["modulus_hex"], f"{label}.modulus_hex")
    if (
        not re.fullmatch(r"[0-9a-f]+", modulus_hex)
        or len(modulus_hex) % 2
        or len(modulus_hex) < 512
    ):
        raise HandoffDataError(
            f"{label}.modulus_hex must be a lowercase RSA-2048+ modulus"
        )
    exponent = expect_int(signer["exponent"], f"{label}.exponent", 3)
    if exponent % 2 == 0:
        raise HandoffDataError(f"{label}.exponent must be odd")
    expect_string(
        signer["service_identity"],
        f"{label}.service_identity",
    )
    isolation = expect_object(
        signer["isolation_attestation"],
        f"{label}.isolation_attestation",
    )
    expect_keys(
        isolation,
        f"{label}.isolation_attestation",
        (
            "kind",
            "private_key_in_implementation_namespace",
            "signing_api",
        ),
    )
    if (
        isolation["kind"] != "external-isolated-service"
        or expect_bool(
            isolation["private_key_in_implementation_namespace"],
            f"{label}.isolation_attestation."
            "private_key_in_implementation_namespace",
        )
        or isolation["signing_api"] != "single-use-terminal-attestation"
    ):
        raise HandoffDataError(
            f"{label} does not attest an isolated external signer"
        )
    expected_key_id = hashlib.sha256(
        COORDINATOR_RECEIPT_SEAL_DOMAIN
        + normalized_json(
            {
                "algorithm": signer["algorithm"],
                "modulus_hex": modulus_hex,
                "exponent": exponent,
                "service_identity": signer["service_identity"],
                "isolation_attestation": isolation,
            }
        )
    ).hexdigest()
    if key_id != expected_key_id:
        raise HandoffDataError(f"{label}.key_id does not match public material")
    return signer
def _decode_canonical_base64(
    value: Any,
    label: str,
    *,
    allow_empty: bool = False,
) -> bytes:
    text = expect_string(value, label, allow_empty=allow_empty)
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HandoffDataError(f"{label} is not canonical base64") from error
    if base64.b64encode(decoded).decode("ascii") != text:
        raise HandoffDataError(f"{label} is not canonical base64")
    return decoded
def verify_external_signature(
    signer: dict[str, Any],
    payload: bytes,
    signature_value: Any,
    label: str,
) -> None:
    signature = _decode_canonical_base64(signature_value, label)
    modulus = int(signer["modulus_hex"], 16)
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        raise HandoffDataError(f"{label} has the wrong RSA size")
    signature_int = int.from_bytes(signature, "big")
    if signature_int >= modulus:
        raise HandoffDataError(f"{label} does not verify")
    encoded = pow(signature_int, signer["exponent"], modulus).to_bytes(
        size, "big"
    )
    digest_info = RSA_SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(payload).digest()
    padding_size = size - len(digest_info) - 3
    expected = b"\x00\x01" + b"\xff" * padding_size + b"\x00" + digest_info
    if padding_size < 8 or encoded != expected:
        raise HandoffDataError(f"{label} does not verify")
def signed_record_payload(domain: bytes, record: dict[str, Any]) -> bytes:
    return domain + normalized_json(
        {key: value for key, value in record.items() if key != "signature"}
    )
def reporter_trust_anchor_payload(anchor: dict[str, Any]) -> bytes:
    return signed_record_payload(REPORTER_TRUST_ANCHOR_DOMAIN, anchor)
def _load_reporter_trusted_installation(
    trusted_installation: Any,
    *,
    repository_root: Path | None,
    label: str,
) -> dict[str, Any]:
    if not isinstance(trusted_installation, Path):
        raise HandoffDataError(f"{label} must be a Path")
    if repository_root is None:
        raise HandoffDataError(f"{label} path requires repository_root")
    installation = load_coordinator_installation(repository_root, trusted_installation)
    return {
        "repository": installation["repository"],
        "repository_database_id": installation["repository_database_id"],
        "signer": copy.deepcopy(installation["_signer"]),
    }
def _verify_reporter_trust_anchor(
    raw_anchor: Any, *, expected_input_seal: str, original_authority: dict[str, Any],
    trusted_installation: dict[str, Any], current_time: datetime | None, label: str,
) -> dict[str, Any]:
    anchor = copy.deepcopy(expect_object(raw_anchor, label))
    expect_keys(anchor, label, ("input_seal", "authority_digest", "repository", "ref", "anchor_ref", "signer", "issued_at", "expires_at", "signature"))
    input_seal = anchor["input_seal"]
    if not isinstance(input_seal, str) or reporter.SHA256_RE.fullmatch(input_seal) is None:
        raise HandoffDataError(f"{label}.input_seal must be a SHA-256")
    if input_seal != expected_input_seal:
        raise HandoffDataError(f"{label}.input_seal does not match its record")
    authority_digest = anchor["authority_digest"]
    if not isinstance(authority_digest, str) or reporter.SHA256_RE.fullmatch(authority_digest) is None:
        raise HandoffDataError(f"{label}.authority_digest must be a SHA-256")
    repository = expect_string(anchor["repository"], f"{label}.repository")
    if repository != trusted_installation["repository"]:
        raise HandoffDataError(f"{label}.repository does not match trusted installation")
    ref = expect_string(anchor["ref"], f"{label}.ref")
    anchor_ref = expect_string(anchor["anchor_ref"], f"{label}.anchor_ref")
    signer = _parse_signer_public(anchor["signer"], f"{label}.signer")
    if signer != trusted_installation["signer"]:
        raise HandoffDataError(f"{label}.signer does not match trusted installation")
    issued_at = parse_time(anchor["issued_at"], f"{label}.issued_at")
    expires_at = parse_time(anchor["expires_at"], f"{label}.expires_at")
    if issued_at > expires_at:
        raise HandoffDataError(f"{label}.expires_at precedes issued_at")
    verification_time = authoritative_current_time(current_time, label=f"{label} current_time")
    if issued_at > verification_time or expires_at < verification_time:
        raise HandoffDataError(f"{label} is future-dated or expired")
    verify_external_signature(trusted_installation["signer"], reporter_trust_anchor_payload(anchor), anchor["signature"], f"{label}.signature")
    if authority_digest != hashlib.sha256(normalized_json(original_authority)).hexdigest() or repository != original_authority["repository"] or ref != original_authority["ref"] or anchor_ref != original_authority["anchor_ref"]:
        raise HandoffDataError(f"{label} does not match its record")
    return {"signer": signer, "repository_database_id": trusted_installation["repository_database_id"]}
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
def _path_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
def _trusted_path_signature(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_size,
        metadata.st_nlink,
        getattr(metadata, "st_mtime_ns", 0),
        getattr(metadata, "st_ctime_ns", 0),
    )
def _require_owner_controlled(
    metadata: os.stat_result,
    *,
    label: str,
) -> None:
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
        raise HandoffDataError(
            f"{label} must be owner-controlled and not group/other writable"
        )
def _validate_trusted_entry(
    metadata: os.stat_result,
    *,
    label: str,
    directory: bool,
    require_owner_controlled: bool = True,
    max_bytes: int | None = None,
    require_single_link: bool = False,
) -> None:
    if directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise HandoffDataError(f"{label} must be a directory")
    elif not stat.S_ISREG(metadata.st_mode):
        raise HandoffDataError(f"{label} must be a regular file")
    if require_owner_controlled:
        _require_owner_controlled(metadata, label=label)
    if directory:
        return
    if max_bytes is not None and metadata.st_size > max_bytes:
        raise HandoffDataError(f"{label} exceeds 1 MiB")
    if require_single_link and metadata.st_nlink != 1:
        raise HandoffDataError(f"{label} must not be hardlinked")
def _open_trusted_entry(
    name: str,
    *,
    dir_fd: int,
    label: str,
    directory: bool,
    require_owner_controlled: bool = True,
    max_bytes: int | None = None,
    require_single_link: bool = False,
) -> tuple[int, os.stat_result]:
    try:
        metadata = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as error:
        raise HandoffDataError(f"cannot inspect {label}: {error}") from error
    _validate_trusted_entry(
        metadata,
        label=label,
        directory=directory,
        require_owner_controlled=require_owner_controlled,
        max_bytes=max_bytes,
        require_single_link=require_single_link,
    )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=dir_fd)
    except OSError as error:
        raise HandoffDataError(f"cannot open {label}: {error}") from error
    try:
        opened = os.fstat(descriptor)
    except OSError as error:
        os.close(descriptor)
        raise HandoffDataError(f"cannot inspect opened {label}: {error}") from error
    _validate_trusted_entry(
        opened,
        label=label,
        directory=directory,
        require_owner_controlled=require_owner_controlled,
        max_bytes=max_bytes,
        require_single_link=require_single_link,
    )
    if _trusted_path_signature(opened) != _trusted_path_signature(metadata):
        os.close(descriptor)
        raise HandoffDataError(f"{label} changed before read")
    return descriptor, opened
def _read_trusted_file(
    descriptor: int,
    metadata: os.stat_result,
    *,
    label: str,
    max_bytes: int,
) -> bytes:
    raw = bytearray()
    try:
        while len(raw) < TRUSTED_INSTALLATION_READ_BYTES:
            chunk = os.read(
                descriptor,
                min(65536, TRUSTED_INSTALLATION_READ_BYTES - len(raw)),
            )
            if not chunk:
                break
            raw.extend(chunk)
        final = os.fstat(descriptor)
    except OSError as error:
        raise HandoffDataError(f"cannot read {label}: {error}") from error
    if len(raw) > max_bytes:
        raise HandoffDataError(f"{label} exceeds 1 MiB")
    if (
        _trusted_path_signature(final) != _trusted_path_signature(metadata)
        or len(raw) != metadata.st_size
    ):
        raise HandoffDataError(f"{label} changed while being read")
    return bytes(raw)
def _parse_trusted_json(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise HandoffDataError(f"{label} is not valid UTF-8") from error
    return _raise_pilot_error(reporter.parse_json, text, label)
def _open_trusted_directory_path(
    path: Path,
    *,
    label: str,
) -> tuple[Path, int]:
    absolute_path = Path(os.path.abspath(os.fspath(path)))
    components = absolute_path.parts[1:]
    try:
        descriptor = os.open(
            os.path.sep,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0),
        )
    except OSError as error:
        raise HandoffDataError(
            f"cannot open filesystem root for {label}: {error}"
        ) from error
    current_path = Path(os.path.sep)
    try:
        for index, component in enumerate(components):
            current_path /= component
            next_descriptor, _metadata = _open_trusted_entry(
                component,
                dir_fd=descriptor,
                label=f"{label} path {current_path}",
                directory=True,
                require_owner_controlled=index == len(components) - 1,
            )
            os.close(descriptor)
            descriptor = next_descriptor
    except Exception:
        os.close(descriptor)
        raise
    return absolute_path, descriptor
def _open_transport_object_store(repository_root: Path) -> int:
    repository_root = validate_repository_root(repository_root)
    common = Path(run_git(repository_root, "rev-parse", "--git-common-dir").decode().strip())
    common = common if common.is_absolute() else repository_root / common; _path, common_fd = _open_trusted_directory_path(common, label="Git common directory")
    try:
        objects_fd, _metadata = _open_trusted_entry("objects", dir_fd=common_fd, label="Git common object store", directory=True)
    finally:
        os.close(common_fd)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        for directory in ("info", "pack"):
            try:
                child = os.open(directory, flags, dir_fd=objects_fd)
            except FileNotFoundError: continue
            except OSError as error:
                raise HandoffDataError(f"cannot inspect Git object {directory}: {error}") from error
            try:
                _require_owner_controlled(os.fstat(child), label=f"Git object {directory}"); names = os.listdir(child)
            finally: os.close(child)
            if (directory == "info" and {"alternates", "http-alternates"} & set(names)) or (directory == "pack" and any(name.endswith(".promisor") for name in names)):
                raise HandoffDataError("Git common object store has external object providers")
        return objects_fd
    except Exception:
        os.close(objects_fd)
        raise
def _read_trusted_installation_member(
    installation_root: Path,
    installation_descriptor: int,
    raw_path: str,
    *,
    label: str,
    repository_root: Path,
    git_dir: Path,
) -> tuple[Path, bytes]:
    requested = Path(expect_string(raw_path, label))
    absolute_path = (
        requested
        if requested.is_absolute()
        else installation_root / requested
    )
    absolute_path = Path(os.path.abspath(os.fspath(absolute_path)))
    if not _path_within(absolute_path, installation_root):
        raise HandoffDataError(
            f"{label} must stay rooted under the coordinator installation"
        )
    if _path_within(absolute_path, repository_root) or _path_within(
        absolute_path,
        git_dir,
    ):
        raise HandoffDataError(
            f"{label} must stay outside the candidate worktree and Git dir"
        )
    relative = absolute_path.relative_to(installation_root)
    if not relative.parts:
        raise HandoffDataError(f"{label} must name a regular file")
    current_descriptor = os.dup(installation_descriptor)
    try:
        for component in relative.parts[:-1]:
            next_descriptor, _metadata = _open_trusted_entry(
                component,
                dir_fd=current_descriptor,
                label=f"{label} parent",
                directory=True,
            )
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        file_descriptor, metadata = _open_trusted_entry(
            relative.parts[-1],
            dir_fd=current_descriptor,
            label=label,
            directory=False,
            max_bytes=TRUSTED_INSTALLATION_MAX_BYTES,
            require_single_link=True,
        )
        try:
            return absolute_path, _read_trusted_file(
                file_descriptor,
                metadata,
                label=label,
                max_bytes=TRUSTED_INSTALLATION_MAX_BYTES,
            )
        finally:
            os.close(file_descriptor)
    finally:
        os.close(current_descriptor)
def load_coordinator_installation(
    repository_root: Path,
    installation_path: Path | None = None,
) -> dict[str, Any]:
    repository_root = validate_repository_root(repository_root)
    if installation_path is None:
        raw_path = os.environ.get(COORDINATOR_INSTALLATION_ENV)
        if not raw_path:
            raise HandoffDataError(
                "trusted coordinator installation is required"
            )
        installation_path = Path(raw_path)
    git_dir = Path(
        run_git(repository_root, "rev-parse", "--absolute-git-dir")
        .decode("utf-8")
        .strip()
    ).resolve()
    installation_root, installation_descriptor = _open_trusted_directory_path(
        installation_path,
        label="trusted coordinator installation",
    )
    bootstrap_descriptor = os.dup(installation_descriptor)
    try:
        if _path_within(installation_root, repository_root) or _path_within(
            installation_root,
            git_dir,
        ):
            raise HandoffDataError(
                "trusted coordinator installation must be outside the "
                "candidate worktree"
            )
        manifest_path, manifest_raw = _read_trusted_installation_member(
            installation_root,
            installation_descriptor,
            "installation.json",
            label="coordinator installation manifest",
            repository_root=repository_root,
            git_dir=git_dir,
        )
        manifest = expect_object(
            _parse_trusted_json(manifest_raw, str(manifest_path)),
            "coordinator installation",
        )
    finally:
        os.close(installation_descriptor)
    expect_keys(
        manifest,
        "coordinator installation",
        (
            "schema_version",
            "repository",
            "repository_database_id",
            "collector",
            "authorized_coordinators",
            "authorized_non_user_bypass_actors",
            "authority_protection",
            "delivery",
            "bootstrap_validator",
            "signer_public",
        ),
    )
    if expect_int(
        manifest["schema_version"],
        "coordinator installation.schema_version",
        1,
    ) != 1:
        raise HandoffDataError(
            "coordinator installation.schema_version must be 1"
        )
    expect_string(
        manifest["repository"],
        "coordinator installation.repository",
    )
    expect_int(
        manifest["repository_database_id"],
        "coordinator installation.repository_database_id",
        1,
    )
    collector = expect_object(
        manifest["collector"],
        "coordinator installation.collector",
    )
    expect_keys(
        collector,
        "coordinator installation.collector",
        ("login", "database_id"),
    )
    parsed_collector = _parse_actor(
        collector["login"],
        collector["database_id"],
        "coordinator installation.collector",
    )
    authorized = []
    for index, raw_actor in enumerate(
        expect_list(
            manifest["authorized_coordinators"],
            "coordinator installation.authorized_coordinators",
        )
    ):
        label = f"coordinator installation.authorized_coordinators[{index}]"
        actor = expect_object(raw_actor, label)
        expect_keys(actor, label, ("login", "database_id"))
        authorized.append(
            _parse_actor(
                actor["login"],
                actor["database_id"],
                label,
            )
        )
    if not authorized:
        raise HandoffDataError(
            "coordinator installation requires an authorized coordinator"
        )
    expect_unique(
        (actor["database_id"] for actor in authorized),
        "coordinator installation authorized database IDs",
    )
    non_user_bypass = []
    for index, raw_bypass in enumerate(
        expect_list(
            manifest["authorized_non_user_bypass_actors"],
            "coordinator installation.authorized_non_user_bypass_actors",
        )
    ):
        label = (
            "coordinator installation.authorized_non_user_bypass_actors"
            f"[{index}]"
        )
        bypass = expect_object(raw_bypass, label)
        expect_keys(
            bypass,
            label,
            ("actor_type", "actor_id", "bypass_mode"),
        )
        if bypass["actor_type"] not in NON_USER_BYPASS_ACTOR_TYPES:
            raise HandoffDataError(
                f"{label}.actor_type is not an explicit non-user type"
            )
        expect_int(bypass["actor_id"], f"{label}.actor_id", 1)
        if bypass["bypass_mode"] != "always":
            raise HandoffDataError(
                f"{label}.bypass_mode must be always"
            )
        non_user_bypass.append(bypass)
    expect_unique(
        (
            (item["actor_type"], item["actor_id"])
            for item in non_user_bypass
        ),
        "coordinator installation non-user bypass actors",
    )
    protection = expect_object(
        manifest["authority_protection"],
        "coordinator installation.authority_protection",
    )
    expect_keys(
        protection,
        "coordinator installation.authority_protection",
        (
            "mode",
            "ruleset_id",
            "enforcement",
            "authority_ref_prefix",
            "anchor_ref_prefix",
            "remote_url",
            "force_pushes_allowed",
            "deletions_allowed",
        ),
    )
    expect_enum(
        protection["mode"],
        {"bare-remote-config", "github-ruleset-api"},
        "coordinator installation.authority_protection.mode",
    )
    if protection["enforcement"] != "active":
        raise HandoffDataError(
            "coordinator installation authority ruleset must be active"
        )
    if (
        protection["authority_ref_prefix"] != HISTORY_REF_PREFIX
        or protection["anchor_ref_prefix"] != HISTORY_ANCHOR_REF_PREFIX
    ):
        raise HandoffDataError(
            "coordinator installation authority ruleset has wrong branch "
            "scope"
        )
    expect_int(
        protection["ruleset_id"],
        "coordinator installation.authority_protection.ruleset_id",
        1,
    )
    expect_string(protection["remote_url"], "coordinator installation authority remote_url")
    expect_bool(
        protection["force_pushes_allowed"],
        "coordinator installation.authority_protection.force_pushes_allowed",
    )
    expect_bool(
        protection["deletions_allowed"],
        "coordinator installation.authority_protection.deletions_allowed",
    )
    if protection["mode"] == "bare-remote-config":
        if protection["force_pushes_allowed"]:
            raise HandoffDataError(
                "coordinator installation bare-remote-config must reject force pushes"
            )
        if protection["deletions_allowed"]:
            raise HandoffDataError(
                "coordinator installation bare-remote-config must reject deletions"
            )
    delivery = expect_object(
        manifest["delivery"],
        "coordinator installation.delivery",
    )
    expect_keys(
        delivery,
        "coordinator installation.delivery",
        (
            "immediate_base_branch",
            "delivery_branch",
            "head_repository_full_name",
        ),
    )
    for field in (
        "immediate_base_branch",
        "delivery_branch",
        "head_repository_full_name",
    ):
        expect_string(
            delivery[field],
            f"coordinator installation.delivery.{field}",
        )
    bootstrap = expect_object(
        manifest["bootstrap_validator"],
        "coordinator installation.bootstrap_validator",
    )
    expect_keys(
        bootstrap,
        "coordinator installation.bootstrap_validator",
        ("path",),
    )
    try:
        bootstrap_path, bootstrap_source = _read_trusted_installation_member(
            installation_root,
            bootstrap_descriptor,
            bootstrap["path"],
            label="coordinator installation.bootstrap_validator.path",
            repository_root=repository_root,
            git_dir=git_dir,
        )
    finally:
        os.close(bootstrap_descriptor)
    signer = _parse_signer_public(
        manifest["signer_public"],
        "coordinator installation.signer_public",
    )
    return {
        **manifest,
        "_root": installation_root,
        "_collector": parsed_collector,
        "_authorized": authorized,
        "_authorized_non_user_bypass": non_user_bypass,
        "_bootstrap_validator": bootstrap_path,
        "_bootstrap_validator_source": bootstrap_source,
        "_signer": signer,
    }
def _transport_endpoint(repository_root: Path, installation_path: Path | None) -> str:
    if installation_path is not None and not isinstance(installation_path, Path):
        raise HandoffDataError("coordinator installation must be an external path")
    installation = load_coordinator_installation(repository_root, installation_path)
    remote = installation["authority_protection"]["remote_url"]
    if remote.startswith("-") or any(ord(character) < 32 or ord(character) == 127 for character in remote):
        raise HandoffDataError("coordinator installation remote_url is not canonical")
    repository = installation["repository"]
    if remote in {f"https://github.com/{repository}.git", f"ssh://git@github.com/{repository}.git", f"git@github.com:{repository}.git"}:
        return remote
    try:
        resolved = Path(remote).resolve(strict=True)
    except OSError as error:
        raise HandoffDataError(f"coordinator installation remote_url is unavailable: {error}") from error
    if installation["authority_protection"]["mode"] != "bare-remote-config" or not Path(remote).is_absolute() or str(resolved) != remote or not resolved.is_dir():
        raise HandoffDataError("coordinator installation remote_url is not canonical")
    return remote
def _git_blob(
    repository_root: Path,
    commit_sha: str,
    path: str,
) -> tuple[str, bytes] | None:
    raw = run_git(
        repository_root,
        "ls-tree",
        commit_sha,
        "--",
        path,
    ).decode("utf-8")
    fields = raw.rstrip("\n").split(maxsplit=3)
    if not raw:
        return None
    if (
        len(fields) != 4
        or fields[0] != "100644"
        or fields[1] != "blob"
        or fields[3] != path
    ):
        raise HandoffDataError(
            f"trusted parent checker {path!r} is not one regular blob"
        )
    blob_oid = expect_sha(fields[2], "trusted parent checker blob")
    return (
        blob_oid,
        run_git(repository_root, "cat-file", "blob", blob_oid),
    )
def _allowed_check_execution(
    contract: str,
    repository_root: Path,
    parent_sha: str,
    candidate_sha: str,
    installation_path: Path | None = None,
) -> tuple[list[str], bytes | None, dict[str, Any]]:
    expect_enum(contract, ALLOWED_CHECK_CONTRACTS, "check contract")
    if contract == "git-diff-check":
        parent_checker = _git_blob(
            repository_root,
            parent_sha,
            RAW_DIFF_CHECK_REPOSITORY_PATH,
        )
        if parent_checker is not None:
            blob_oid, source = parent_checker
            return (
                [
                    "/usr/bin/python3",
                    "-I",
                    "-",
                    "--repository-root",
                    str(repository_root),
                    "--parent",
                    parent_sha,
                    "--candidate",
                    candidate_sha,
                ],
                source,
                {
                    "mode": "trusted-parent-blob",
                    "repository_path": RAW_DIFF_CHECK_REPOSITORY_PATH,
                    "blob_oid": blob_oid,
                },
            )
        installation = load_coordinator_installation(
            repository_root,
            installation_path,
        )
        validator = installation["_bootstrap_validator_source"]
        return (
            [
                "/usr/bin/python3",
                "-I",
                "-",
                "--repository-root",
                str(repository_root),
                "--parent",
                parent_sha,
                "--candidate",
                candidate_sha,
            ],
            validator,
            {
                "mode": "external-bootstrap",
                "sha256": hashlib.sha256(validator).hexdigest(),
            },
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
    coordinator_installation: Path | None = None,
) -> dict[str, Any]:
    repository_root = validate_repository_root(repository_root)
    expect_string(receipt_id, "receipt_id")
    expect_string(check_id, "check_id")
    expect_sha(parent_sha, "parent_sha")
    expect_sha(candidate_sha, "candidate_sha")
    argv, stdin, checker_trust = _allowed_check_execution(
        contract,
        repository_root,
        parent_sha,
        candidate_sha,
        coordinator_installation,
    )
    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    completed = _run_bounded_process(
        argv=argv,
        cwd=repository_root,
        env=reporter.git_environment(offline=True),
        stdin=stdin,
        timeout_seconds=ALLOWED_CHECK_TIMEOUT_SECONDS,
        timeout_code=ALLOWED_CHECK_TIMEOUT_CODE,
        label=f"allowed check {check_id!r}",
        execute_error_label=f"allowed check {check_id!r}",
    )
    completed_at = datetime.now(timezone.utc).replace(microsecond=0)
    receipt = {
        "id": receipt_id,
        "check_id": check_id,
        "contract": contract,
        "argv": argv,
        "checker_trust": checker_trust,
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
        "checker_trust",
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
        checker_trust = expect_object(
            receipt["checker_trust"],
            f"{receipt_label}.checker_trust",
        )
        mode = expect_enum(
            checker_trust.get("mode"),
            {"external-bootstrap", "trusted-parent-blob"},
            f"{receipt_label}.checker_trust.mode",
        )
        if mode == "trusted-parent-blob":
            expect_keys(
                checker_trust,
                f"{receipt_label}.checker_trust",
                ("mode", "repository_path", "blob_oid"),
            )
            if checker_trust["repository_path"] != RAW_DIFF_CHECK_REPOSITORY_PATH:
                raise HandoffDataError(
                    f"{receipt_label}.checker_trust has wrong repository path"
                )
            expect_sha(
                checker_trust["blob_oid"],
                f"{receipt_label}.checker_trust.blob_oid",
            )
        else:
            expect_keys(
                checker_trust,
                f"{receipt_label}.checker_trust",
                ("mode", "sha256"),
            )
            if (
                not isinstance(checker_trust["sha256"], str)
                or reporter.SHA256_RE.fullmatch(checker_trust["sha256"]) is None
            ):
                raise HandoffDataError(
                    f"{receipt_label}.checker_trust.sha256 must be SHA-256"
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
    coordinator_installation: Path | None = None,
) -> set[str]:
    errors = set()
    if receipt["seal"] != seal_check_receipt(receipt):
        errors.add("invalid-check-receipt")
    expected_argv, stdin, expected_trust = _allowed_check_execution(
        check["contract"],
        repository_root,
        parent_sha,
        candidate_sha,
        coordinator_installation,
    )
    if (
        receipt["check_id"] != check["id"]
        or receipt["contract"] != check["contract"]
        or receipt["argv"] != expected_argv
        or receipt["checker_trust"] != expected_trust
        or receipt["parent_sha"] != parent_sha
        or receipt["candidate_sha"] != candidate_sha
        or receipt["worktree_identity"] != worktree_identity(repository_root)
    ):
        errors.add("invalid-check-receipt")
    completed = _run_bounded_process(
        argv=expected_argv,
        cwd=repository_root,
        env=reporter.git_environment(offline=True),
        stdin=stdin,
        timeout_seconds=ALLOWED_CHECK_TIMEOUT_SECONDS,
        timeout_code=ALLOWED_CHECK_REVERIFY_TIMEOUT_CODE,
        label=f"allowed check {check['id']!r} verification",
        execute_error_label=f"allowed check {check['id']!r} verification",
    )
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
def history_authority_ref(issue: int, pull_request: int | None) -> str:
    expect_int(issue, "history authority issue", 1)
    if pull_request is not None:
        expect_int(pull_request, "history authority pull request", 1)
    return f"{HISTORY_REF_PREFIX}/issue-{issue}"
def history_anchor_ref(issue: int) -> str:
    expect_int(issue, "history authority issue", 1)
    return f"{HISTORY_ANCHOR_REF_PREFIX}/issue-{issue}"
def _remote_ref_oid(
    repository_root: Path,
    installation_path: Path | None,
    reference: str,
    *,
    allow_missing: bool,
) -> str | None:
    output = _run_git_online(
        repository_root,
        "ls-remote",
        "--refs",
        "--",
        _transport_endpoint(repository_root, installation_path),
        reference,
    ).decode("ascii")
    lines = [line for line in output.splitlines() if line]
    if not lines:
        if allow_missing:
            return None
        raise HandoffDataError(
            f"remote authority {reference!r} is unavailable; genesis is unknown"
        )
    if len(lines) != 1:
        raise HandoffDataError(
            f"remote authority {reference!r} is ambiguous"
        )
    object_id, returned_ref = lines[0].split("\t", 1)
    if returned_ref != reference or reporter.SHA_RE.fullmatch(object_id) is None:
        raise HandoffDataError(
            f"remote authority {reference!r} returned malformed identity"
        )
    return object_id
def _fetch_remote_authority(
    repository_root: Path,
    installation_path: Path | None,
    reference: str,
    object_id: str,
) -> None:
    head_before = run_git(repository_root, "rev-parse", "HEAD")
    refs_before = run_git(
        repository_root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
    )
    fetch_head_path = Path(
        run_git(repository_root, "rev-parse", "--git-path", "FETCH_HEAD")
        .decode("utf-8")
        .strip()
    )
    if not fetch_head_path.is_absolute():
        fetch_head_path = repository_root / fetch_head_path
    fetch_head_before = (
        fetch_head_path.read_bytes() if fetch_head_path.is_file() else None
    )
    _run_git_online(
        repository_root,
        "fetch",
        "--no-write-fetch-head",
        "--no-tags",
        "--",
        _transport_endpoint(repository_root, installation_path),
        object_id,
    )
    if run_git(repository_root, "rev-parse", "HEAD") != head_before:
        raise HandoffDataError("authority fetch changed HEAD")
    if run_git(
        repository_root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
    ) != refs_before:
        raise HandoffDataError("authority fetch changed local refs")
    fetch_head_after = (
        fetch_head_path.read_bytes() if fetch_head_path.is_file() else None
    )
    if fetch_head_after != fetch_head_before:
        raise HandoffDataError("authority fetch changed FETCH_HEAD")
    fetched_type = (
        run_git(repository_root, "cat-file", "-t", object_id)
        .decode("ascii")
        .strip()
    )
    if fetched_type not in {"blob", "commit"}:
        raise HandoffDataError(
            f"remote authority {reference!r} has invalid object type"
        )
def _parse_authority_json(
    raw: bytes,
    label: str,
) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HandoffDataError(f"{label} is not UTF-8") from error
    return expect_object(
        _raise_pilot_error(reporter.parse_json, text, label),
        label,
    )
def read_remote_repository_identity(
    repository_root: Path, installation_path: Path | None,
) -> str:
    object_id = _remote_ref_oid(
        repository_root,
        installation_path,
        REPOSITORY_IDENTITY_REF,
        allow_missing=False,
    )
    _fetch_remote_authority(
        repository_root,
        installation_path,
        REPOSITORY_IDENTITY_REF,
        object_id,
    )
    if (
        run_git(repository_root, "cat-file", "-t", object_id)
        .decode("ascii")
        .strip()
        != "blob"
    ):
        raise HandoffDataError(
            "remote repository identity ref must point to a blob"
        )
    identity = _parse_authority_json(
        run_git(repository_root, "cat-file", "blob", object_id),
        "remote repository identity",
    )
    expect_keys(
        identity,
        "remote repository identity",
        ("schema_version", "repository"),
    )
    version = expect_int(
        identity["schema_version"],
        "remote repository identity.schema_version",
        1,
    )
    if version != 1:
        raise HandoffDataError(
            "remote repository identity.schema_version must be 1"
        )
    return expect_string(
        identity["repository"],
        "remote repository identity.repository",
    )
def _read_history_authority_commit(
    repository_root: Path,
    object_id: str,
    repository: str,
    issue: int,
    *,
    expected_previous_anchor_object_id: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    if (
        run_git(repository_root, "cat-file", "-t", object_id)
        .decode("ascii")
        .strip()
        != "commit"
    ):
        raise HandoffDataError(
            f"history authority object {object_id} must be a commit"
        )
    parents = (
        run_git(repository_root, "show", "-s", "--format=%P", object_id)
        .decode("ascii")
        .strip()
        .split()
    )
    authority = _parse_authority_json(
        run_git(
            repository_root,
            "show",
            f"{object_id}:authority.json",
        ),
        f"history authority object {object_id}",
    )
    expect_keys(
        authority,
        f"history authority object {object_id}",
        (
            "schema_version",
            "repository",
            "issue",
            "sequence",
            "handoff_sequence",
            "head_seal",
            "pr_binding",
            "signer",
            "ruleset_id",
            "authorized_bypass_actors",
            "delivery_expectation",
            "publication_attestation",
            "event",
            "previous_object_id",
        ),
    )
    version = expect_int(
        authority["schema_version"],
        f"history authority object {object_id}.schema_version",
        1,
    )
    if version != 2:
        raise HandoffDataError(
            f"history authority object {object_id} schema_version must be 2"
        )
    if expect_string(
        authority["repository"],
        f"history authority object {object_id}.repository",
    ) != repository:
        raise HandoffDataError(
            f"history authority object {object_id} repository mismatch"
        )
    if expect_int(
        authority["issue"],
        f"history authority object {object_id}.issue",
        1,
    ) != issue:
        raise HandoffDataError(
            f"history authority object {object_id} issue mismatch"
        )
    sequence = expect_int(
        authority["sequence"],
        f"history authority object {object_id}.sequence",
        0,
    )
    handoff_sequence = expect_int(
        authority["handoff_sequence"],
        f"history authority object {object_id}.handoff_sequence",
        0,
    )
    binding = authority["pr_binding"]
    signer = _parse_signer_public(
        authority["signer"],
        f"history authority object {object_id}.signer",
    )
    expect_int(
        authority["ruleset_id"],
        f"history authority object {object_id}.ruleset_id",
        1,
    )
    delivery = expect_object(
        authority["delivery_expectation"],
        f"history authority object {object_id}.delivery_expectation",
    )
    expect_keys(
        delivery,
        f"history authority object {object_id}.delivery_expectation",
        (
            "repository_id",
            "repository_full_name",
            "immediate_base_branch",
            "immediate_base_oid",
            "delivery_branch",
            "head_repository_full_name",
        ),
    )
    expect_int(
        delivery["repository_id"],
        f"history authority object {object_id}."
        "delivery_expectation.repository_id",
        1,
    )
    for field in (
        "repository_full_name",
        "immediate_base_branch",
        "delivery_branch",
        "head_repository_full_name",
    ):
        expect_string(
            delivery[field],
            f"history authority object {object_id}."
            f"delivery_expectation.{field}",
        )
    expect_sha(
        delivery["immediate_base_oid"],
        f"history authority object {object_id}."
        "delivery_expectation.immediate_base_oid",
    )
    delivery_repository_id = expect_int(
        delivery["repository_id"],
        f"history authority object {object_id}."
        "delivery_expectation.repository_id",
        1,
    )
    canonical_previous_anchor_object_id = expect_sha(
        expected_previous_anchor_object_id,
        f"history authority object {object_id}.expected_previous_anchor_object_id",
        nullable=True,
    )
    publication = expect_object(
        authority["publication_attestation"],
        f"history authority object {object_id}.publication_attestation",
    )
    publication_actor_id = expect_int(
        publication.get("coordinator_database_id"),
        f"history authority object {object_id}."
        "publication_attestation.coordinator_database_id",
        1,
    )
    parse_publication_attestation(
        publication,
        signer=signer,
        repository=repository,
        repository_database_id=delivery_repository_id,
        issue=issue,
        authority_ref=history_authority_ref(issue, None),
        anchor_ref=history_anchor_ref(issue),
        authority_object_id=authority["previous_object_id"],
        anchor_object_id=canonical_previous_anchor_object_id,
        ruleset_id=authority["ruleset_id"],
        authorized_bypass_actors=authority["authorized_bypass_actors"],
    )
    if binding is not None:
        binding = expect_object(
            binding,
            f"history authority object {object_id}.pr_binding",
        )
        expect_keys(
            binding,
            f"history authority object {object_id}.pr_binding",
            (
                "source",
                "repository_id",
                "repository_full_name",
                "pull_request",
                "state",
                "merged",
                "base_branch",
                "head_branch",
                "head_repository_full_name",
                "base_oid",
                "head_oid",
                "created_at",
                "coordinator_database_id",
                "observed_at",
                "authority_object_id",
                "anchor_object_id",
                "expected_handoff_branch",
                "delivery_branch",
                "signature",
            ),
        )
        parse_pull_request_observation(
            binding,
            signer=signer,
            repository=repository,
            repository_database_id=delivery_repository_id,
            authority_object_id=authority["previous_object_id"],
            anchor_object_id=canonical_previous_anchor_object_id,
        )
        expect_int(
            binding["repository_id"],
            f"history authority object {object_id}.pr_binding.repository_id",
            1,
        )
        expect_string(
            binding["repository_full_name"],
            f"history authority object {object_id}."
            "pr_binding.repository_full_name",
        )
        expect_int(
            binding["pull_request"],
            f"history authority object {object_id}.pr_binding.pull_request",
            1,
        )
        expect_string(
            binding["base_branch"],
            f"history authority object {object_id}.pr_binding.base_branch",
        )
        expect_string(
            binding["head_branch"],
            f"history authority object {object_id}.pr_binding.head_branch",
        )
        expect_string(
            binding["head_repository_full_name"],
            f"history authority object {object_id}."
            "pr_binding.head_repository_full_name",
        )
        expect_sha(
            binding["base_oid"],
            f"history authority object {object_id}.pr_binding.base_oid",
        )
        expect_sha(
            binding["head_oid"],
            f"history authority object {object_id}.pr_binding.head_oid",
        )
        for field in ("created_at", "observed_at"):
            parse_time(
                binding[field],
                f"history authority object {object_id}.pr_binding.{field}",
            )
        expect_int(
            binding["coordinator_database_id"],
            f"history authority object {object_id}."
            "pr_binding.coordinator_database_id",
            1,
        )
        if (
            binding["repository_id"] != delivery_repository_id
            or binding["repository_full_name"]
            != delivery["repository_full_name"]
            or binding["base_branch"] != delivery["immediate_base_branch"]
            or binding["delivery_branch"] != delivery["delivery_branch"]
            or binding["expected_handoff_branch"]
            != delivery["delivery_branch"]
            or binding["head_branch"] != delivery["delivery_branch"]
            or binding["head_repository_full_name"]
            != delivery["head_repository_full_name"]
        ):
            raise HandoffDataError(
                f"history authority object {object_id} PR binding "
                "contradicts frozen delivery identity"
            )
        if not git_commit_is_ancestor(
            repository_root,
            delivery["immediate_base_oid"],
            binding["base_oid"],
        ):
            raise HandoffDataError(
                f"history authority object {object_id} PR binding base is "
                "not descended from the frozen delivery base"
            )
    event = expect_object(
        authority["event"],
        f"history authority object {object_id}.event",
    )
    expect_keys(
        event,
        f"history authority object {object_id}.event",
        (
            "kind",
            "handoff_seal",
            "handoff_id",
            "handoff_kind",
            "lifecycle_state",
            "candidate_sha",
            "closed_at",
            "operation_nonce",
            "consume_store_id",
            "consume_sequence",
            "consume_anchor",
            "assignment",
            "interruption_snapshot",
            "history_receipt",
            "history_carrier",
        ),
    )
    event_kind = expect_enum(
        event["kind"],
        {"genesis", "handoff", "pr_binding"},
        f"history authority object {object_id}.event.kind",
    )
    if event["handoff_seal"] is not None:
        if (
            not isinstance(event["handoff_seal"], str)
            or reporter.SHA256_RE.fullmatch(event["handoff_seal"]) is None
        ):
            raise HandoffDataError(
                f"history authority object {object_id} has invalid event seal"
            )
    if sequence == 0:
        if (
            handoff_sequence != 0
            or authority["head_seal"] is not None
            or binding is not None
            or event_kind != "genesis"
            or event["handoff_seal"] is not None
            or any(
                event[field] is not None
                for field in (
                    "handoff_id",
                    "handoff_kind",
                    "lifecycle_state",
                    "candidate_sha",
                    "closed_at",
                    "operation_nonce",
                    "consume_store_id",
                    "consume_sequence",
                    "consume_anchor",
                    "assignment",
                    "interruption_snapshot",
                    "history_receipt",
                    "history_carrier",
                )
            )
            or authority["previous_object_id"] is not None
            or parents
        ):
            raise HandoffDataError(
                f"history authority object {object_id} has invalid genesis"
            )
    else:
        for field in ("previous_object_id",):
            value = authority[field]
            if not isinstance(value, str) or reporter.SHA_RE.fullmatch(value) is None:
                raise HandoffDataError(
                    f"history authority object {object_id} has invalid {field}"
                )
        if len(parents) != 1 or parents[0] != authority["previous_object_id"]:
            raise HandoffDataError(
                f"history authority object {object_id} forks its commit chain"
            )
        if authority["head_seal"] is not None and (
            not isinstance(authority["head_seal"], str)
            or reporter.SHA256_RE.fullmatch(authority["head_seal"]) is None
        ):
            raise HandoffDataError(
                f"history authority object {object_id} has invalid head_seal"
            )
        if event_kind == "handoff":
            history_receipt = expect_object(
                event["history_receipt"],
                f"history authority object {object_id}.event.history_receipt",
            )
            _parse_history_carrier(
                event["history_carrier"],
                label=(
                    f"history authority object {object_id}.event.history_carrier"
                ),
            )
            if (
                event["handoff_seal"] is None
                or event["handoff_seal"] != authority["head_seal"]
                or handoff_sequence < 1
                or event["handoff_id"] is None
                or event["handoff_kind"] is None
                or event["lifecycle_state"] is None
                or event["closed_at"] is None
                or event["operation_nonce"] is None
                or event["consume_store_id"] is None
                or event["consume_sequence"] is None
                or event["consume_anchor"] is None
                or event["assignment"] is None
                or history_receipt.get("seal") != event["handoff_seal"]
                or history_receipt.get("handoff_id") != event["handoff_id"]
                or history_receipt.get("handoff_kind")
                != event["handoff_kind"]
                or history_receipt.get("lifecycle_state")
                != event["lifecycle_state"]
                or history_receipt.get("candidate_sha")
                != event["candidate_sha"]
                or history_receipt.get("closed_at") != event["closed_at"]
                or history_receipt.get("operation_nonce")
                != event["operation_nonce"]
                or history_receipt.get("consume_store_id")
                != event["consume_store_id"]
                or history_receipt.get("consume_sequence")
                != event["consume_sequence"]
                or history_receipt.get("consume_anchor")
                != event["consume_anchor"]
                or history_receipt.get("assignment") != event["assignment"]
                or history_receipt.get("interruption_snapshot")
                != event["interruption_snapshot"]
            ):
                raise HandoffDataError(
                    f"history authority object {object_id} has invalid handoff event"
                )
        elif event_kind == "pr_binding":
            if (
                event["handoff_seal"] is not None
                or event["history_receipt"] is not None
                or event["history_carrier"] is not None
                or binding is None
                or any(
                    event[field] is not None
                    for field in (
                        "handoff_id",
                        "handoff_kind",
                        "lifecycle_state",
                        "candidate_sha",
                        "closed_at",
                        "operation_nonce",
                        "consume_store_id",
                        "consume_sequence",
                        "consume_anchor",
                        "assignment",
                        "interruption_snapshot",
                    )
                )
            ):
                raise HandoffDataError(
                    f"history authority object {object_id} has invalid binding event"
                )
            if canonical_previous_anchor_object_id is None:
                raise HandoffDataError(
                    f"history authority object {object_id} PR binding lacks "
                    "a canonical prior anchor"
                )
            prior_authority = load_history_authority_transition_summary(
                repository_root,
                authority["previous_object_id"],
                repository,
                issue,
                label=(
                    f"history authority object {object_id} prior sealed handoff"
                ),
            )
            expected_branch, expected_candidate_sha = (
                validate_historical_pr_binding_target(
                    authority,
                    prior_authority,
                    label=f"history authority object {object_id}",
                )
            )
            if (
                binding["head_branch"] != expected_branch
                or binding["expected_handoff_branch"] != expected_branch
                or binding["head_oid"] != expected_candidate_sha
            ):
                raise HandoffDataError(
                    f"history authority object {object_id} PR binding does "
                    "not match its immediately prior sealed handoff"
                )
        else:
            raise HandoffDataError(
                f"history authority object {object_id} replays genesis"
            )
    require_publication_attestation_binding(
        publication,
        operation={
            "genesis": "bootstrap",
            "handoff": "advance",
            "pr_binding": "bind",
        }[event_kind],
        new_head_seal=authority["head_seal"] if event_kind == "handoff" else None,
        history_carrier=(
            event["history_carrier"] if event_kind == "handoff" else None
        ),
        history_receipt=(
            event["history_receipt"] if event_kind == "handoff" else None
        ),
        pull_request_observation=(
            binding if event_kind == "pr_binding" else None
        ),
        binding_expectation=(
            publication_binding_expectation_for_observation(
                delivery,
                binding,
            )
            if event_kind == "pr_binding"
            else None
        ),
        label=(
            f"history authority object {object_id} publication does not "
            "bind its event"
        ),
    )
    return authority, parents
def _read_history_anchor_commit(
    repository_root: Path,
    object_id: str,
    repository: str,
    issue: int,
) -> tuple[dict[str, Any], list[str]]:
    if (
        run_git(repository_root, "cat-file", "-t", object_id)
        .decode("ascii")
        .strip()
        != "commit"
    ):
        raise HandoffDataError(
            f"history anchor object {object_id} must be a commit"
        )
    parents = (
        run_git(repository_root, "show", "-s", "--format=%P", object_id)
        .decode("ascii")
        .strip()
        .split()
    )
    anchor = _parse_authority_json(
        run_git(repository_root, "show", f"{object_id}:anchor.json"),
        f"history anchor object {object_id}",
    )
    expect_keys(
        anchor,
        f"history anchor object {object_id}",
        (
            "schema_version",
            "repository",
            "issue",
            "sequence",
            "authority_object_id",
            "previous_object_id",
        ),
    )
    if expect_int(
        anchor["schema_version"],
        f"history anchor object {object_id}.schema_version",
        1,
    ) != 1:
        raise HandoffDataError(
            f"history anchor object {object_id} schema_version must be 1"
        )
    if (
        expect_string(
            anchor["repository"],
            f"history anchor object {object_id}.repository",
        )
        != repository
        or expect_int(
            anchor["issue"],
            f"history anchor object {object_id}.issue",
            1,
        )
        != issue
    ):
        raise HandoffDataError(
            f"history anchor object {object_id} identity mismatch"
        )
    sequence = expect_int(
        anchor["sequence"],
        f"history anchor object {object_id}.sequence",
        0,
    )
    expect_sha(
        anchor["authority_object_id"],
        f"history anchor object {object_id}.authority_object_id",
    )
    if sequence == 0:
        if anchor["previous_object_id"] is not None or parents:
            raise HandoffDataError(
                f"history anchor object {object_id} has invalid genesis"
            )
    else:
        expect_sha(
            anchor["previous_object_id"],
            f"history anchor object {object_id}.previous_object_id",
        )
        if len(parents) != 1 or parents[0] != anchor["previous_object_id"]:
            raise HandoffDataError(
                f"history anchor object {object_id} forks its commit chain"
            )
    return anchor, parents
def _history_observation(
    reference: str,
    object_id: str,
    anchor_reference: str,
    anchor_object_id: str,
    attempt: int,
) -> dict[str, Any]:
    payload = {
        "remote": "origin",
        "ref": reference,
        "object_id": object_id,
        "anchor_ref": anchor_reference,
        "anchor_object_id": anchor_object_id,
        "attempt": attempt,
    }
    payload["token"] = hashlib.sha256(
        HISTORY_OBSERVATION_SEAL_DOMAIN + normalized_json(payload)
    ).hexdigest()
    return payload
def confirm_history_authority_observation(
    repository_root: Path,
    installation_path: Path | None,
    observation: dict[str, Any],
) -> None:
    observation = expect_object(
        observation,
        "history authority observation",
    )
    expect_keys(
        observation,
        "history authority observation",
        (
            "remote",
            "ref",
            "object_id",
            "anchor_ref",
            "anchor_object_id",
            "attempt",
            "token",
        ),
    )
    if observation["remote"] != "origin":
        raise HandoffDataError(
            "history authority observation remote must be origin"
        )
    reference = expect_string(
        observation["ref"],
        "history authority observation.ref",
    )
    object_id = expect_sha(
        observation["object_id"],
        "history authority observation.object_id",
    )
    anchor_reference = expect_string(
        observation["anchor_ref"],
        "history authority observation.anchor_ref",
    )
    anchor_object_id = expect_sha(
        observation["anchor_object_id"],
        "history authority observation.anchor_object_id",
    )
    attempt = expect_int(
        observation["attempt"],
        "history authority observation.attempt",
        1,
    )
    if attempt > AUTHORITY_READ_ATTEMPTS:
        raise HandoffDataError(
            "history authority observation attempt exceeds bound"
        )
    token = observation["token"]
    if (
        not isinstance(token, str)
        or reporter.SHA256_RE.fullmatch(token) is None
        or token
        != _history_observation(
            reference,
            object_id,
            anchor_reference,
            anchor_object_id,
            attempt,
        )["token"]
    ):
        raise HandoffDataError(
            "history authority observation token does not verify"
        )
    current = _remote_ref_oid(
        repository_root,
        installation_path,
        reference,
        allow_missing=False,
    )
    current_anchor = _remote_ref_oid(
        repository_root,
        installation_path,
        anchor_reference,
        allow_missing=False,
    )
    if current != object_id or current_anchor != anchor_object_id:
        raise HandoffDataError("authority-moved")
def _terminal_remote_state_rejections(
    repository_root: Path,
    installation_path: Path | None,
    canonical_authority: dict[str, Any],
) -> set[str]:
    delivery_ref = "refs/heads/" + canonical_authority["delivery_expectation"][
        "delivery_branch"
    ]
    current_delivery_head = _remote_ref_oid(
        repository_root,
        installation_path,
        delivery_ref,
        allow_missing=True,
    )
    binding = canonical_authority["pr_binding"]
    if binding is None:
        return (
            {"remote-coverage-incomplete"}
            if current_delivery_head is not None
            else set()
        )
    rejections = set()
    if current_delivery_head != binding["head_oid"]:
        rejections.add("remote-coverage-incomplete")
    current_base_head = _remote_ref_oid(
        repository_root,
        installation_path,
        "refs/heads/" + binding["base_branch"],
        allow_missing=True,
    )
    if current_base_head != binding["base_oid"]:
        rejections.add("remote-coverage-incomplete")
    return rejections
def read_history_authority(
    repository_root: Path,
    repository: str,
    issue: int,
    pull_request: int | None,
    *,
    observation_hook=None,
    coordinator_installation: Path | None = None,
) -> dict[str, Any]:
    repository_root = validate_repository_root(repository_root)
    installation = load_coordinator_installation(
        repository_root,
        coordinator_installation,
    )
    if installation["repository"] != repository:
        raise HandoffDataError(
            "coordinator installation repository mismatch"
        )
    reference = history_authority_ref(issue, pull_request)
    anchor_reference = history_anchor_ref(issue)
    object_id = None
    anchor_object_id = None
    observation = None
    for attempt in range(1, AUTHORITY_READ_ATTEMPTS + 1):
        observed_before = _remote_ref_oid(
            repository_root,
            coordinator_installation,
            reference,
            allow_missing=False,
        )
        anchor_before = _remote_ref_oid(
            repository_root,
            coordinator_installation,
            anchor_reference,
            allow_missing=False,
        )
        fetch_error = None
        try:
            _fetch_remote_authority(
                repository_root,
                coordinator_installation,
                reference,
                observed_before,
            )
            _fetch_remote_authority(
                repository_root,
                coordinator_installation,
                anchor_reference,
                anchor_before,
            )
        except HandoffDataError as error:
            fetch_error = error
        if observation_hook is not None:
            observation_hook(attempt, "after-fetch", observed_before)
        observed_after = _remote_ref_oid(
            repository_root,
            coordinator_installation,
            reference,
            allow_missing=False,
        )
        anchor_after = _remote_ref_oid(
            repository_root,
            coordinator_installation,
            anchor_reference,
            allow_missing=False,
        )
        if (
            observed_before != observed_after
            or anchor_before != anchor_after
        ):
            continue
        if fetch_error is not None:
            raise fetch_error
        object_id = observed_before
        anchor_object_id = anchor_before
        observation = _history_observation(
            reference,
            object_id,
            anchor_reference,
            anchor_object_id,
            attempt,
        )
        break
    if (
        object_id is None
        or anchor_object_id is None
        or observation is None
    ):
        raise HandoffDataError("authority-moved")
    anchor, anchor_parents = _read_history_anchor_commit(
        repository_root,
        anchor_object_id,
        repository,
        issue,
    )
    anchor_ids_by_sequence = {anchor["sequence"]: anchor_object_id}
    anchor_records_by_sequence = {anchor["sequence"]: anchor}
    anchor_previous_by_sequence = {
        anchor["sequence"]: anchor["previous_object_id"]
    }
    expected_anchor_sequence = anchor["sequence"]
    current_anchor = anchor
    current_anchor_parents = anchor_parents
    while expected_anchor_sequence > 0:
        if len(current_anchor_parents) != 1:
            raise HandoffDataError(
                f"history anchor {anchor_reference!r} truncates its ancestry"
            )
        anchor_parent_id = current_anchor_parents[0]
        prior_anchor, current_anchor_parents = _read_history_anchor_commit(
            repository_root,
            anchor_parent_id,
            repository,
            issue,
        )
        expected_anchor_sequence -= 1
        if prior_anchor["sequence"] != expected_anchor_sequence:
            raise HandoffDataError(
                f"history anchor {anchor_reference!r} replays or gaps sequence"
            )
        anchor_ids_by_sequence[prior_anchor["sequence"]] = anchor_parent_id
        anchor_records_by_sequence[prior_anchor["sequence"]] = prior_anchor
        anchor_previous_by_sequence[prior_anchor["sequence"]] = prior_anchor[
            "previous_object_id"
        ]
        current_anchor = prior_anchor
    authority_summary = load_history_authority_transition_summary(
        repository_root,
        object_id,
        repository,
        issue,
        label=f"history authority object {object_id}",
    )
    _require_authority_matches_installation(
        authority_summary,
        installation,
        label=f"history authority object {object_id}",
    )
    if authority_summary["sequence"] not in anchor_previous_by_sequence:
        raise HandoffDataError(
            "independent authority anchor does not match authority head"
        )
    authority, parents = _read_history_authority_commit(
        repository_root,
        object_id,
        repository,
        issue,
        expected_previous_anchor_object_id=anchor_previous_by_sequence[
            authority_summary["sequence"]
        ],
    )
    if (
        authority["delivery_expectation"]["repository_id"]
        != installation["repository_database_id"]
        or authority["delivery_expectation"]["repository_full_name"]
        != installation["repository"]
        or authority["delivery_expectation"]["immediate_base_branch"]
        != installation["delivery"]["immediate_base_branch"]
        or authority["delivery_expectation"]["delivery_branch"]
        != installation["delivery"]["delivery_branch"]
        or authority["delivery_expectation"]["head_repository_full_name"]
        != installation["delivery"]["head_repository_full_name"]
    ):
        raise HandoffDataError(
            "history authority delivery expectation does not match "
            "coordinator installation"
        )
    if (
        authority["pr_binding"] is not None
        and authority["pr_binding"]["repository_id"]
        != authority["delivery_expectation"]["repository_id"]
    ):
        raise HandoffDataError(
            "history authority PR binding repository identity mismatch"
        )
    if (
        anchor["sequence"] != authority["sequence"]
        or anchor["authority_object_id"] != object_id
        or authority["publication_attestation"]["anchor_object_id"]
        != anchor["previous_object_id"]
    ):
        raise HandoffDataError(
            "independent authority anchor does not match authority head"
        )
    authority_by_sequence = {authority["sequence"]: object_id}
    authority_records_by_sequence = {authority["sequence"]: authority}
    expected_sequence = authority["sequence"]
    current_object = object_id
    current_parents = parents
    current = authority
    while expected_sequence > 0:
        if len(current_parents) != 1:
            raise HandoffDataError(
                f"history authority {reference!r} truncates its ancestry"
            )
        current_object = current_parents[0]
        expected_sequence -= 1
        prior, current_parents = _read_history_authority_commit(
            repository_root,
            current_object,
            repository,
            issue,
            expected_previous_anchor_object_id=anchor_previous_by_sequence[
                expected_sequence
            ],
        )
        if prior["sequence"] != expected_sequence:
            raise HandoffDataError(
                f"history authority {reference!r} replays or gaps sequence"
            )
        if current["event"]["kind"] == "handoff":
            if (
                prior["handoff_sequence"] + 1
                != current["handoff_sequence"]
                or current["event"]["handoff_seal"]
                != current["head_seal"]
                or prior["pr_binding"] != current["pr_binding"]
                or prior["signer"] != current["signer"]
                or prior["ruleset_id"] != current["ruleset_id"]
                or prior["authorized_bypass_actors"]
                != current["authorized_bypass_actors"]
                or prior["delivery_expectation"]
                != current["delivery_expectation"]
            ):
                raise HandoffDataError(
                    f"history authority {reference!r} has invalid handoff "
                    "transition"
                )
        elif current["event"]["kind"] == "pr_binding":
            if (
                prior["handoff_sequence"] != current["handoff_sequence"]
                or prior["head_seal"] != current["head_seal"]
                or prior["pr_binding"] is not None
                or current["pr_binding"] is None
                or prior["signer"] != current["signer"]
                or prior["ruleset_id"] != current["ruleset_id"]
                or prior["authorized_bypass_actors"]
                != current["authorized_bypass_actors"]
                or prior["delivery_expectation"]
                != current["delivery_expectation"]
            ):
                raise HandoffDataError(
                    f"history authority {reference!r} has invalid PR binding "
                    "transition"
                )
        authority_by_sequence[prior["sequence"]] = current_object
        authority_records_by_sequence[prior["sequence"]] = prior
        current = prior
    for sequence_number, anchor_record in anchor_records_by_sequence.items():
        if anchor_record["authority_object_id"] != authority_by_sequence[
            sequence_number
        ]:
            raise HandoffDataError(
                f"history anchor {anchor_reference!r} replays or gaps sequence"
            )
    verified_history_events = []
    for sequence_number in range(1, authority["sequence"] + 1):
        current_record = authority_records_by_sequence[sequence_number]
        if current_record["event"]["kind"] != "handoff":
            continue
        verified_receipt = _verify_history_event_carrier(
            current_record["event"]["history_carrier"],
            repository_root=repository_root,
            current_authority={
                **_public_authority_record(current_record),
                "ref": reference,
                "object_id": authority_by_sequence[sequence_number],
                "anchor_ref": anchor_reference,
                "anchor_object_id": anchor_ids_by_sequence[sequence_number],
                "history_events": copy.deepcopy(verified_history_events),
            },
            label=(
                "history authority object "
                f"{authority_by_sequence[sequence_number]}.event.history_carrier"
            ),
        )
        expected_event = _history_event_from_receipt(
            verified_receipt,
            current_record["event"]["history_carrier"],
        )
        if current_record["event"] != expected_event:
            raise HandoffDataError(
                "history authority object "
                f"{authority_by_sequence[sequence_number]} has invalid handoff event"
            )
        if (
            verified_receipt["sequence"] != current_record["handoff_sequence"]
            or verified_receipt["previous_seal"]
            != (
                ZERO_SEAL
                if not verified_history_events
                else verified_history_events[-1]["history_receipt"]["seal"]
            )
            or verified_receipt["seal"] != current_record["head_seal"]
        ):
            raise HandoffDataError(
                f"history authority {reference!r} has invalid handoff transition"
            )
        verified_history_events.append(
            _public_history_event(
                expected_event,
                history_receipt=verified_receipt,
            )
        )
    binding = authority["pr_binding"]
    if (
        pull_request is not None
        and (
            binding is None
            or binding["pull_request"] != pull_request
        )
    ):
        raise HandoffDataError(
            "issue authority is not bound to the requested pull request"
        )
    return _canonical_public_history_authority(
        authority,
        object_id=object_id,
        anchor_object_id=anchor_object_id,
        history_events=verified_history_events,
        event_history_receipt=(
            verified_history_events[-1]["history_receipt"]
            if authority["event"]["kind"] == "handoff"
            else None
        ),
        label=f"history authority {reference!r}",
    )
def plan_history_authority(
    repository_root: Path,
    repository: str,
    issue: int | None,
    pull_request: int | None,
    *,
    operation: str,
    expected_object_id: str | None = None,
    expected_sequence: int | None = None,
    handoff_document: dict[str, Any] | None = None,
    handoff_result: dict[str, Any] | None = None,
    handoff_id: str | None = None,
    pull_request_observation: dict[str, Any] | None = None,
    publication_attestation: dict[str, Any] | None = None,
    coordinator_installation: Path | None = None,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    repository_root = validate_repository_root(repository_root)
    live_current_time = authoritative_current_time(
        current_time,
        label="history authority plan current_time",
    )
    installation = load_coordinator_installation(
        repository_root,
        coordinator_installation,
    )
    if installation["repository"] != repository:
        raise HandoffDataError(
            "coordinator installation repository mismatch"
        )
    if _repository_from_origin(repository_root, coordinator_installation) != repository:
        raise HandoffDataError("history authority plan repository mismatch")
    new_head_seal = history_receipt = history_carrier = expected_binding = None
    if operation == "advance":
        if (
            handoff_document is None
            or handoff_result is None
            or handoff_id is None
            or pull_request_observation is not None
        ):
            raise HandoffDataError(
                "history authority advance requires handoff document, "
                "canonical result, selected handoff ID, and no PR observation"
            )
        closed = make_history_receipt(
            copy.deepcopy(expect_object(handoff_document, "handoff document")),
            copy.deepcopy(expect_object(handoff_result, "handoff result")),
            expect_string(handoff_id, "handoff_id"),
            coordinator_installation=coordinator_installation,
            current_time=live_current_time,
        )
        if issue is not None and issue != closed["issue"]:
            raise HandoffDataError("history authority advance target issue does not match the canonical handoff issue")
        if pull_request not in {None, closed["pull_request"]}:
            raise HandoffDataError("history authority advance target pull request does not match the canonical handoff pull request")
        issue = closed["issue"]; pull_request = closed["pull_request"]
        history_receipt = copy.deepcopy(closed); new_head_seal = closed["seal"]
        history_carrier = make_history_carrier(
            copy.deepcopy(expect_object(handoff_document, "handoff document")),
            copy.deepcopy(expect_object(handoff_result, "handoff result")),
            closed["handoff_id"],
        )
    elif issue is None:
        raise HandoffDataError(
            "history authority bootstrap and bind require an explicit issue"
        )
    reference = history_authority_ref(issue, pull_request)
    anchor_reference = history_anchor_ref(issue)
    remote_object = _remote_ref_oid(
        repository_root,
        coordinator_installation,
        reference,
        allow_missing=True,
    )
    remote_anchor = _remote_ref_oid(
        repository_root,
        coordinator_installation,
        anchor_reference,
        allow_missing=True,
    )
    preflight_object = (
        run_git(repository_root, "rev-parse", "HEAD")
        .decode("ascii")
        .strip()
    )
    require_atomic_push_capability(
        repository_root,
        coordinator_installation,
        [
            (remote_object or preflight_object, reference),
            (remote_anchor or preflight_object, anchor_reference),
        ],
    )
    if operation == "bootstrap":
        if remote_object is not None or remote_anchor is not None:
            raise HandoffDataError(
                "history authority bootstrap requires absent authority and "
                "anchor branches"
            )
        if pull_request is not None:
            raise HandoffDataError(
                "history authority bootstrap must start in the no-PR namespace"
            )
        if any(
            value is not None
            for value in (
                expected_object_id,
                expected_sequence,
                handoff_document,
                handoff_result,
                handoff_id,
                pull_request_observation,
            )
        ):
            raise HandoffDataError(
                "history authority bootstrap does not accept prior state"
            )
        record = {
            "schema_version": 2,
            "repository": repository,
            "issue": issue,
            "sequence": 0,
            "handoff_sequence": 0,
            "head_seal": None,
            "pr_binding": None,
            "signer": installation["_signer"],
            "ruleset_id": installation["authority_protection"]["ruleset_id"],
            "authorized_bypass_actors": [
                *(
                    user_bypass_actor(actor["database_id"])
                    for actor in installation["_authorized"]
                ),
                *installation["_authorized_non_user_bypass"],
            ],
            "delivery_expectation": {
                "repository_id": installation["repository_database_id"],
                "repository_full_name": repository,
                "immediate_base_branch": installation["delivery"][
                    "immediate_base_branch"
                ],
                "immediate_base_oid": (
                    run_git(
                        repository_root,
                        "rev-parse",
                        "refs/heads/"
                        + installation["delivery"][
                            "immediate_base_branch"
                        ],
                    )
                    .decode("ascii")
                    .strip()
                ),
                "delivery_branch": installation["delivery"][
                    "delivery_branch"
                ],
                "head_repository_full_name": installation["delivery"][
                    "head_repository_full_name"
                ],
            },
            "event": _null_authority_event("genesis"),
            "previous_object_id": None,
        }
        expected_remote = None
        expected_anchor = None
        anchor_sequence = 0
    elif operation in {"advance", "bind"}:
        if (
            expected_object_id is None
            or expected_sequence is None
        ):
            raise HandoffDataError(
                "history authority update requires expected head and sequence"
            )
        expect_sha(expected_object_id, "expected history authority object")
        expect_int(expected_sequence, "expected history authority sequence", 0)
        current = read_history_authority(
            repository_root,
            repository,
            issue,
            None if operation == "bind" else pull_request,
            coordinator_installation=coordinator_installation,
        )
        if (
            remote_object != expected_object_id
            or current["object_id"] != expected_object_id
            or current["sequence"] != expected_sequence
            or remote_anchor != current["anchor_object_id"]
        ):
            raise HandoffDataError(
                "history authority compare-and-swap expectation is stale"
            )
        if operation == "advance":
            if (
                closed["sequence"] != current["handoff_sequence"] + 1
                or closed["previous_seal"]
                != (current["head_seal"] or ZERO_SEAL)
            ):
                raise HandoffDataError(
                    "history authority advance receipt does not extend head"
                )
            record = {
                "schema_version": 2,
                "repository": repository,
                "issue": issue,
                "sequence": expected_sequence + 1,
                "handoff_sequence": current["handoff_sequence"] + 1,
                "head_seal": new_head_seal,
                "pr_binding": current["pr_binding"],
                "signer": current["signer"],
                "ruleset_id": current["ruleset_id"],
                "authorized_bypass_actors": current[
                    "authorized_bypass_actors"
                ],
                "delivery_expectation": current["delivery_expectation"],
                "event": _history_event_from_receipt(
                    closed,
                    history_carrier,
                ),
                "previous_object_id": expected_object_id,
            }
        else:
            if (
                pull_request is None
                or pull_request_observation is None
                or new_head_seal is not None
            ):
                raise HandoffDataError(
                    "history authority PR binding requires one sealed GitHub "
                    "PR observation and no handoff seal"
                )
            if current["pr_binding"] is not None:
                raise HandoffDataError(
                    "history authority PR binding is immutable"
                )
            if not current["history_events"]:
                raise HandoffDataError(
                    "PR binding requires a protected root handoff"
                )
            root_assignment = current["history_events"][0]["assignment"]
            latest_handoff = current["history_events"][-1]
            delivery = current["delivery_expectation"]
            frozen_user_ids = {
                item["database_id"]
                for item in current["authorized_bypass_actors"]
                if item["actor_type"] == "User"
            }
            installation_user_ids = {
                actor["database_id"] for actor in installation["_authorized"]
            }
            if not frozen_user_ids:
                raise HandoffDataError(
                    "PR binding requires at least one frozen coordinator user"
                )
            if frozen_user_ids != installation_user_ids:
                raise HandoffDataError(
                    "coordinator installation authorized coordinators do not "
                    "match frozen authority"
                )
            if latest_handoff["candidate_sha"] is None:
                raise HandoffDataError(
                    "PR binding requires a committed handoff head"
                )
            observation = parse_pull_request_observation(
                pull_request_observation,
                signer=current["signer"],
                repository=repository,
                repository_database_id=installation[
                    "repository_database_id"
                ],
                authority_object_id=current["object_id"],
                anchor_object_id=current["anchor_object_id"],
                live=True,
                current_time=live_current_time,
            )
            if observation["pull_request"] != pull_request:
                raise HandoffDataError(
                    "GitHub PR observation has the wrong pull request"
                )
            if parse_time(
                observation["created_at"],
                "GitHub PR observation.created_at",
            ) > parse_time(
                observation["observed_at"],
                "GitHub PR observation.observed_at",
            ):
                raise HandoffDataError(
                    "GitHub PR observation predates PR creation"
                )
            actual_base_oid = (
                run_git(
                    repository_root,
                    "rev-parse",
                    f"refs/heads/{observation['base_branch']}",
                )
                .decode("ascii")
                .strip()
            )
            actual_head_oid = (
                run_git(
                    repository_root,
                    "rev-parse",
                    f"refs/heads/{observation['head_branch']}",
                )
                .decode("ascii")
                .strip()
            )
            if (
                observation["repository_id"] != delivery["repository_id"]
                or observation["repository_full_name"]
                != delivery["repository_full_name"]
                or observation["base_branch"]
                != delivery["immediate_base_branch"]
                or observation["head_branch"]
                != root_assignment["expected_branch"]
                or observation["head_repository_full_name"]
                != delivery["head_repository_full_name"]
                or observation["head_oid"] != latest_handoff["candidate_sha"]
            ):
                raise HandoffDataError(
                    "GitHub PR observation does not match frozen delivery "
                    "inputs"
                )
            if observation["coordinator_database_id"] not in frozen_user_ids:
                raise HandoffDataError(
                    "PR binding actor is not an authorized coordinator"
                )
            if (
                observation["base_oid"] != actual_base_oid
                or observation["head_oid"] != actual_head_oid
            ):
                raise HandoffDataError(
                    "GitHub PR observation does not match live repository state"
                )
            if not git_commit_is_ancestor(
                repository_root,
                delivery["immediate_base_oid"],
                observation["base_oid"],
            ):
                raise HandoffDataError(
                    "GitHub PR observation base is not descended from the "
                    "frozen delivery base"
                )
            if not git_commit_is_ancestor(
                repository_root,
                delivery["immediate_base_oid"],
                latest_handoff["candidate_sha"],
            ):
                raise HandoffDataError(
                    "PR binding candidate is not descended from the frozen "
                    "delivery base"
                )
            expected_binding = publication_binding_expectation(
                delivery_expectation=delivery,
                pull_request=pull_request,
                head_branch=root_assignment["expected_branch"],
                head_oid=latest_handoff["candidate_sha"],
                coordinator_database_id=observation["coordinator_database_id"],
                current_base_oid=observation["base_oid"],
            )
            binding = copy.deepcopy(observation)
            record = {
                "schema_version": 2,
                "repository": repository,
                "issue": issue,
                "sequence": expected_sequence + 1,
                "handoff_sequence": current["handoff_sequence"],
                "head_seal": current["head_seal"],
                "pr_binding": binding,
                "signer": current["signer"],
                "ruleset_id": current["ruleset_id"],
                "authorized_bypass_actors": current[
                    "authorized_bypass_actors"
                ],
                "delivery_expectation": current["delivery_expectation"],
                "event": _null_authority_event("pr_binding"),
                "previous_object_id": expected_object_id,
            }
        expected_remote = expected_object_id
        expected_anchor = current["anchor_object_id"]
        anchor_sequence = current["sequence"] + 1
    else:
        raise HandoffDataError(
            "history authority operation must be bootstrap, advance, or bind"
        )
    if publication_attestation is None:
        raise HandoffDataError(
            "authority publication requires external coordinator attestation"
        )
    parsed_publication = parse_publication_attestation(
        publication_attestation,
        signer=record["signer"],
        repository=repository,
        repository_database_id=installation["repository_database_id"],
        issue=issue,
        authority_ref=reference,
        anchor_ref=anchor_reference,
        authority_object_id=expected_remote,
        anchor_object_id=expected_anchor,
        ruleset_id=record["ruleset_id"],
        authorized_bypass_actors=record["authorized_bypass_actors"],
        live=True,
        current_time=live_current_time,
    )
    require_publication_attestation_binding(
        parsed_publication,
        operation=operation,
        new_head_seal=new_head_seal,
        history_carrier=history_carrier,
        history_receipt=history_receipt,
        pull_request_observation=pull_request_observation,
        binding_expectation=expected_binding,
        label="authority publication attestation does not bind the plan",
    )
    record["publication_attestation"] = parsed_publication
    return {
        "operation": operation,
        "remote": "origin",
        "ref": reference,
        "anchor_ref": anchor_reference,
        "expected_remote_object_id": expected_remote,
        "expected_anchor_object_id": expected_anchor,
        "record": record,
        "anchor_record_template": {
            "schema_version": 1,
            "repository": repository,
            "issue": issue,
            "sequence": anchor_sequence,
            "authority_object_id": "<new-authority-commit>",
            "previous_object_id": expected_anchor,
        },
        "owner_only": True,
        "required_actor_database_ids": sorted(
            actor["database_id"] for actor in installation["_authorized"]
        ),
        "operation_nonce": parsed_publication["operation_nonce"],
        "atomic_push": (
            "scripts.workflow_pilot.agent_handoff.publish_authority_updates("
            "<external-owner-repository>, <external-installation-path>, "
            f"<new-authority-commit>:{reference} "
            f"<new-anchor-commit>:{anchor_reference})"
        ),
    }
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
    operation_nonces = []
    expected_previous = ZERO_SEAL
    previous_closed_at = None
    required = (
        "sequence",
        "previous_seal",
        "handoff_id",
        "owner_id",
        "owner_database_id",
        "handoff_kind",
        "replaces_handoff_id",
        "issue",
        "pull_request",
        "assigned_parent_sha",
        "expected_branch",
        "allowed_worktree",
        "candidate_sha",
        "lifecycle_state",
        "interruption_snapshot",
        "assigned_at",
        "closed_at",
        "input_seal",
        "git_seal",
        "result_seal",
        "operation_nonce",
        "consume_store_id",
        "consume_sequence",
        "consume_anchor",
        "assignment",
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
        handoff_kind = expect_enum(
            receipt["handoff_kind"],
            {"root", "oom_replacement", "review_successor"},
            f"{label}.handoff_kind",
        )
        if (handoff_kind == "root") != (
            receipt["replaces_handoff_id"] is None
        ):
            raise HandoffDataError(
                f"{label}.handoff_kind contradicts predecessor"
            )
        operation_nonce = receipt["operation_nonce"]
        if (
            not isinstance(operation_nonce, str)
            or reporter.SHA256_RE.fullmatch(operation_nonce) is None
        ):
            raise HandoffDataError(
                f"{label}.operation_nonce must be a SHA-256"
            )
        operation_nonces.append(operation_nonce)
        expect_string(
            receipt["consume_store_id"],
            f"{label}.consume_store_id",
        )
        expect_int(
            receipt["consume_sequence"],
            f"{label}.consume_sequence",
            1,
        )
        if (
            not isinstance(receipt["consume_anchor"], str)
            or reporter.SHA256_RE.fullmatch(receipt["consume_anchor"]) is None
        ):
            raise HandoffDataError(
                f"{label}.consume_anchor must be a SHA-256"
            )
        assignment = expect_object(
            receipt["assignment"],
            f"{label}.assignment",
        )
        expect_keys(
            assignment,
            f"{label}.assignment",
            (
                "id",
                "issue",
                "pull_request",
                "owner_id",
                "owner_database_id",
                "handoff_kind",
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
            ),
        )
        identity_mapping = {
            "id": "handoff_id",
            "owner_id": "owner_id",
            "owner_database_id": "owner_database_id",
            "handoff_kind": "handoff_kind",
            "replaces_handoff_id": "replaces_handoff_id",
            "issue": "issue",
            "pull_request": "pull_request",
            "assigned_parent_sha": "assigned_parent_sha",
            "expected_branch": "expected_branch",
            "allowed_worktree": "allowed_worktree",
        }
        if any(
            assignment[field] != receipt[receipt_field]
            for field, receipt_field in identity_mapping.items()
        ):
            raise HandoffDataError(
                f"{label}.assignment contradicts receipt identity"
            )
        if receipt["replaces_handoff_id"] is not None:
            expect_string(
                receipt["replaces_handoff_id"],
                f"{label}.replaces_handoff_id",
            )
        expect_int(receipt["issue"], f"{label}.issue", 1)
        if receipt["pull_request"] is not None:
            expect_int(receipt["pull_request"], f"{label}.pull_request", 1)
        lifecycle_state = expect_enum(
            receipt["lifecycle_state"],
            {"handed_off", "interrupted"},
            f"{label}.lifecycle_state",
        )
        expect_sha(
            receipt["assigned_parent_sha"],
            f"{label}.assigned_parent_sha",
        )
        expect_string(
            receipt["expected_branch"],
            f"{label}.expected_branch",
        )
        expect_string(
            receipt["allowed_worktree"],
            f"{label}.allowed_worktree",
        )
        candidate_sha = expect_sha(
            receipt["candidate_sha"],
            f"{label}.candidate_sha",
            nullable=lifecycle_state == "interrupted",
        )
        if candidate_sha is not None:
            candidate_shas.append(candidate_sha)
        snapshot = receipt["interruption_snapshot"]
        if lifecycle_state == "interrupted":
            snapshot = expect_object(
                snapshot,
                f"{label}.interruption_snapshot",
            )
            expect_keys(
                snapshot,
                f"{label}.interruption_snapshot",
                ("status_sha256", "dirty_paths", "preserved_paths", "files"),
            )
            if (
                not isinstance(snapshot["status_sha256"], str)
                or reporter.SHA256_RE.fullmatch(snapshot["status_sha256"]) is None
            ):
                raise HandoffDataError(
                    f"{label}.interruption_snapshot.status_sha256 is invalid"
                )
            for field in ("dirty_paths", "preserved_paths"):
                values = expect_list(
                    snapshot[field],
                    f"{label}.interruption_snapshot.{field}",
                )
                for value_index, value in enumerate(values):
                    _validate_repository_path(
                        value,
                        f"{label}.interruption_snapshot.{field}"
                        f"[{value_index}]",
                        prefix=False,
                    )
                expect_unique(
                    values,
                    f"{label}.interruption_snapshot.{field}",
                )
            file_paths = []
            for file_index, raw_file in enumerate(
                expect_list(
                    snapshot["files"],
                    f"{label}.interruption_snapshot.files",
                )
            ):
                file_label = (
                    f"{label}.interruption_snapshot.files[{file_index}]"
                )
                file_record = expect_object(raw_file, file_label)
                expect_keys(
                    file_record,
                    file_label,
                    ("path", "mode", "sha256", "content_base64"),
                )
                path = _validate_repository_path(
                    file_record["path"],
                    f"{file_label}.path",
                    prefix=False,
                )
                file_paths.append(path)
                expect_int(file_record["mode"], f"{file_label}.mode", 0)
                content = _decode_canonical_base64(
                    file_record["content_base64"],
                    f"{file_label}.content_base64",
                    allow_empty=True,
                )
                if file_record["sha256"] != hashlib.sha256(content).hexdigest():
                    raise HandoffDataError(
                        f"{file_label}.sha256 does not match content"
                    )
            if sorted(file_paths) != sorted(snapshot["preserved_paths"]):
                raise HandoffDataError(
                    f"{label}.interruption_snapshot files do not cover "
                    "preserved paths"
                )
        elif snapshot is not None:
            raise HandoffDataError(
                f"{label}.interruption_snapshot is only valid when interrupted"
            )
        closed_at = parse_time(receipt["closed_at"], f"{label}.closed_at")
        assigned_at = parse_time(
            receipt["assigned_at"],
            f"{label}.assigned_at",
        )
        if assigned_at >= closed_at:
            raise HandoffDataError(
                f"{label}.assigned_at must precede closed_at"
            )
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
    expect_unique(operation_nonces, "prior handoff operation nonces")
    return history
def make_history_receipt(
    document: dict[str, Any],
    result: dict[str, Any],
    handoff_id: str,
    *,
    coordinator_installation: Path | None = None,
    canonical_result: dict[str, Any] | None = None,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    prior = validate_prior_handoffs(document["prior_handoffs"])
    source = next(
        (item for item in document["handoffs"] if item["id"] == handoff_id),
        None,
    )
    if source is None:
        raise HandoffDataError(
            f"handoff {handoff_id!r} has no closed result to seal"
        )
    if canonical_result is None:
        canonical_result = validate_document(
            copy.deepcopy(document),
            Path(source["allowed_worktree"]),
            coordinator_installation=coordinator_installation,
            current_time=(
                parse_time(
                    document["coordinator_receipt"]["issued_at"],
                    "coordinator_receipt.issued_at",
                )
                if current_time is None
                else current_time
            ),
        )
    else:
        canonical_result = copy.deepcopy(
            expect_object(canonical_result, "canonical handoff result")
        )
    if result != canonical_result:
        raise HandoffDataError(
            f"handoff {handoff_id!r} result does not match canonical validation output"
        )
    handoff_result = {
        item["id"]: item for item in canonical_result["handoffs"]
    }.get(handoff_id)
    summary = canonical_result["summary"]
    outcome = None if handoff_result is None else handoff_result["outcome"]
    summary_ok = (
        len(canonical_result["handoffs"]) == 1
        and outcome in {"accepted", "interrupted"}
        and summary["accepted_handoffs"] == int(outcome == "accepted")
        and summary["interrupted_handoffs"] == int(outcome == "interrupted")
        and not summary["rejected_handoffs"]
        and not summary["rejection_codes"]
        and summary["trusted_push_eligible"] is (outcome == "accepted")
    )
    if (
        handoff_result is None
        or handoff_result["rejection_codes"]
        or not summary_ok
    ):
        raise HandoffDataError(
            f"handoff {handoff_id!r} has no closed result to seal"
        )
    receipt = {
        "sequence": len(prior) + 1,
        "previous_seal": prior[-1]["seal"] if prior else ZERO_SEAL,
        "handoff_id": handoff_id,
        "owner_id": source["owner_id"],
        "owner_database_id": source["owner_database_id"],
        "handoff_kind": source["handoff_kind"],
        "replaces_handoff_id": source["replaces_handoff_id"],
        "issue": source["issue"],
        "pull_request": source["pull_request"],
        "assigned_parent_sha": source["assigned_parent_sha"],
        "expected_branch": source["expected_branch"],
        "allowed_worktree": source["allowed_worktree"],
        "candidate_sha": handoff_result["result_sha"],
        "lifecycle_state": handoff_result["state"],
        "interruption_snapshot": handoff_result.get("interruption_snapshot"),
        "assigned_at": handoff_result["assigned_at"],
        "closed_at": handoff_result["closed_at"],
        "input_seal": canonical_result["input_seal"],
        "git_seal": canonical_result["git_seal"],
        "result_seal": canonical_result["result_seal"],
        "operation_nonce": document["coordinator_receipt"]["operation"][
            "nonce"
        ],
        "consume_store_id": document["coordinator_receipt"]["operation"][
            "consume_store_id"
        ],
        "consume_sequence": document["coordinator_receipt"]["operation"][
            "consume_sequence"
        ],
        "consume_anchor": document["coordinator_receipt"]["operation"][
            "consume_anchor"
        ],
        "assignment": {
            field: copy.deepcopy(source[field])
            for field in (
                "id",
                "issue",
                "pull_request",
                "owner_id",
                "owner_database_id",
                "handoff_kind",
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
            )
        },
    }
    receipt["seal"] = seal_history_receipt(receipt)
    return receipt
def _parse_reporter_result_handoffs(raw_handoffs: Any) -> list[dict[str, Any]]:
    parsed = []
    handoff_ids = []
    for index, raw in enumerate(
        expect_list(raw_handoffs, "handoff reporter record.result.handoffs")
    ):
        label = f"handoff reporter record.result.handoffs[{index}]"
        handoff = copy.deepcopy(expect_object(raw, label))
        expect_keys(
            handoff,
            label,
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
        handoff_id = expect_string(handoff["id"], f"{label}.id")
        handoff_ids.append(handoff_id)
        expect_string(handoff["owner_id"], f"{label}.owner_id")
        expect_int(handoff["issue"], f"{label}.issue", 1)
        if handoff["pull_request"] is not None:
            expect_int(handoff["pull_request"], f"{label}.pull_request", 1)
        parse_time(handoff["assigned_at"], f"{label}.assigned_at")
        closed_at = parse_time(
            handoff["closed_at"],
            f"{label}.closed_at",
            nullable=True,
        )
        expect_enum(handoff["state"], HANDOFF_STATES, f"{label}.state")
        outcome = expect_enum(
            handoff["outcome"],
            {"accepted", "in_progress", "interrupted", "rejected"},
            f"{label}.outcome",
        )
        if handoff["result_sha"] is not None:
            expect_sha(handoff["result_sha"], f"{label}.result_sha")
        if handoff["changed_lines"] is not None:
            expect_int(handoff["changed_lines"], f"{label}.changed_lines", 0)
        changed_paths = handoff["changed_paths"]
        if changed_paths is not None:
            paths = expect_list(changed_paths, f"{label}.changed_paths")
            for path_index, path in enumerate(paths):
                _validate_repository_path(
                    path,
                    f"{label}.changed_paths[{path_index}]",
                    prefix=False,
                )
            expect_unique(paths, f"{label}.changed_paths")
        commit_message_sha256 = handoff["commit_message_sha256"]
        if commit_message_sha256 is not None:
            if (
                not isinstance(commit_message_sha256, str)
                or reporter.SHA256_RE.fullmatch(commit_message_sha256) is None
            ):
                raise HandoffDataError(
                    f"{label}.commit_message_sha256 must be a lowercase SHA-256"
                )
        expect_bool(handoff["stale_response"], f"{label}.stale_response")
        for field in (
            "lifetime_seconds",
            "peak_rss_bytes",
            "coordination_turns",
            "recovery_minutes",
        ):
            expect_int(handoff[field], f"{label}.{field}", 0)
        budget_usage = expect_object(handoff["budget_usage"], f"{label}.budget_usage")
        expect_keys(
            budget_usage,
            f"{label}.budget_usage",
            ("rom_bytes", "ram_bytes", "protocol_changes"),
        )
        for field in ("rom_bytes", "ram_bytes", "protocol_changes"):
            expect_int(
                budget_usage[field],
                f"{label}.budget_usage.{field}",
                0,
            )
        snapshot = handoff["interruption_snapshot"]
        if snapshot is not None:
            snapshot = expect_object(snapshot, f"{label}.interruption_snapshot")
            expect_keys(
                snapshot,
                f"{label}.interruption_snapshot",
                ("status_sha256", "dirty_paths", "preserved_paths", "files"),
            )
            if (
                not isinstance(snapshot["status_sha256"], str)
                or reporter.SHA256_RE.fullmatch(snapshot["status_sha256"]) is None
            ):
                raise HandoffDataError(
                    f"{label}.interruption_snapshot.status_sha256 is invalid"
                )
            for field in ("dirty_paths", "preserved_paths"):
                paths = expect_list(
                    snapshot[field],
                    f"{label}.interruption_snapshot.{field}",
                )
                for path_index, path in enumerate(paths):
                    _validate_repository_path(
                        path,
                        f"{label}.interruption_snapshot.{field}[{path_index}]",
                        prefix=False,
                    )
                expect_unique(
                    paths,
                    f"{label}.interruption_snapshot.{field}",
                )
            for file_index, raw_file in enumerate(
                expect_list(
                    snapshot["files"],
                    f"{label}.interruption_snapshot.files",
                )
            ):
                file_label = f"{label}.interruption_snapshot.files[{file_index}]"
                file_record = expect_object(raw_file, file_label)
                expect_keys(
                    file_record,
                    file_label,
                    ("path", "mode", "sha256", "content_base64"),
                )
                _validate_repository_path(
                    file_record["path"],
                    f"{file_label}.path",
                    prefix=False,
                )
                expect_int(file_record["mode"], f"{file_label}.mode", 0)
                if (
                    not isinstance(file_record["sha256"], str)
                    or reporter.SHA256_RE.fullmatch(file_record["sha256"]) is None
                ):
                    raise HandoffDataError(
                        f"{file_label}.sha256 must be a lowercase SHA-256"
                    )
                content = _decode_canonical_base64(
                    file_record["content_base64"],
                    f"{file_label}.content_base64",
                    allow_empty=True,
                )
                if file_record["sha256"] != hashlib.sha256(content).hexdigest():
                    raise HandoffDataError(
                        f"{file_label}.sha256 does not match content"
                    )
        rejection_codes = expect_list(
            handoff["rejection_codes"],
            f"{label}.rejection_codes",
        )
        for code_index, code in enumerate(rejection_codes):
            expect_enum(
                code,
                reporter.HANDOFF_REJECTION_CODES,
                f"{label}.rejection_codes[{code_index}]",
            )
        expect_unique(rejection_codes, f"{label}.rejection_codes")
        if outcome == "accepted":
            if closed_at is None or rejection_codes:
                raise HandoffDataError(
                    f"{label} accepted outcome requires closure without rejections"
                )
        elif outcome == "rejected":
            if not rejection_codes:
                raise HandoffDataError(
                    f"{label} rejected outcome requires rejection_codes"
                )
        elif outcome == "interrupted":
            if closed_at is None:
                raise HandoffDataError(
                    f"{label} interrupted outcome requires closed_at"
                )
        elif closed_at is not None:
            raise HandoffDataError(
                f"{label} in_progress outcome cannot have closed_at"
            )
        parsed.append(handoff)
    expect_unique(handoff_ids, "handoff reporter result handoff IDs")
    return parsed
def _offline_structural_reporter_rejection_codes(
    reported_handoff: dict[str, Any],
) -> set[str]:
    codes = set()
    if reported_handoff["stale_response"]:
        codes.add("stale-result")
    return codes
def _offline_structural_reporter_outcome(
    document_handoff: dict[str, Any],
    reported_handoff: dict[str, Any],
) -> str:
    if document_handoff["interruption"] is not None:
        return "interrupted"
    if document_handoff["_state_names"] in IN_PROGRESS_STATE_PREFIXES:
        return "in_progress"
    if (
        reported_handoff["rejection_codes"]
        or _offline_structural_reporter_rejection_codes(reported_handoff)
    ):
        return "rejected"
    return "accepted"
def _sealed_handoff_events(authority: dict[str, Any]) -> dict[str, dict[str, Any]]:
    events = {event["handoff_id"]: event for event in authority["history_events"]}
    if authority["event"]["kind"] == "handoff":
        events[authority["event"]["handoff_id"]] = authority["event"]
    return events
def _handoff_assignment_record(handoff: dict[str, Any]) -> dict[str, Any]:
    return {
        field: copy.deepcopy(handoff[field])
        for field in (
            "id",
            "issue",
            "pull_request",
            "owner_id",
            "owner_database_id",
            "handoff_kind",
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
        )
    }
def _historical_reporter_handoffs(
    document: dict[str, Any],
    source_root: Path,
    original_authority: dict[str, Any],
    current_authority: dict[str, Any],
) -> list[dict[str, Any]]:
    issued_at = parse_time(
        document["coordinator_receipt"]["issued_at"],
        "handoff reporter coordinator receipt issued_at",
    )
    coordinator_receipt = _parse_coordinator_receipt(
        copy.deepcopy(document["coordinator_receipt"]),
        document=copy.deepcopy(document),
        canonical_authority=copy.deepcopy(original_authority),
        expected_repository_database_id=original_authority["delivery_expectation"][
            "repository_id"
        ],
        current_time=issued_at,
    )
    events = _sealed_handoff_events(current_authority)
    rows = []
    for index, raw in enumerate(
        expect_list(
            document["handoffs"],
            "handoff reporter record.document.handoffs",
        )
    ):
        handoff = _parse_handoff(copy.deepcopy(raw), index)
        event = events.get(handoff["id"])
        if event is None:
            raise HandoffDataError(
                f"historical handoff {handoff['id']!r} is not sealed in current authority"
            )
        row = _empty_handoff_result(handoff)
        telemetry = coordinator_receipt["telemetry"].get(handoff["id"])
        if telemetry is None:
            raise HandoffDataError(
                f"historical handoff {handoff['id']!r} lacks runtime telemetry"
            )
        row["peak_rss_bytes"] = telemetry["peak_rss_bytes"]
        row["coordination_turns"] = telemetry["coordination_turns"]
        row["recovery_minutes"] = telemetry["recovery_minutes"]
        row["interruption_snapshot"] = telemetry["interruption_snapshot"]
        started_at = parse_time(telemetry["started_at"], f"historical telemetry {handoff['id']}.started_at")
        ended_at = parse_time(telemetry["ended_at"], f"historical telemetry {handoff['id']}.ended_at")
        lifetime_seconds = whole_second_duration(started_at, ended_at, label=f"historical telemetry {handoff['id']} lifetime")
        if (
            lifetime_seconds is None
            or telemetry["owner_database_id"] != handoff["_owner"]["database_id"]
            or started_at != parse_time(handoff["_states"][0]["at"], f"historical handoff {handoff['id']}.assigned_at")
            or ended_at != parse_time(handoff["_states"][-1]["at"], f"historical handoff {handoff['id']}.closed_at")
            or row["peak_rss_bytes"] > handoff["max_peak_rss_bytes"]
        ):
            raise HandoffDataError(
                f"historical handoff {handoff['id']!r} telemetry does not verify"
            )
        row["lifetime_seconds"] = lifetime_seconds
        if row["lifetime_seconds"] > handoff["max_lifetime_seconds"]:
            raise HandoffDataError(
                f"historical handoff {handoff['id']!r} lifetime does not verify"
            )
        if (
            event["assignment"] != _handoff_assignment_record(handoff)
            or event["handoff_kind"] != handoff["handoff_kind"]
            or event["closed_at"] != row["closed_at"]
            or event["operation_nonce"] != coordinator_receipt["operation_nonce"]
            or event["consume_store_id"] != coordinator_receipt["consume_store_id"]
            or event["consume_sequence"] != coordinator_receipt["consume_sequence"]
            or event["consume_anchor"] != coordinator_receipt["consume_anchor"]
        ):
            raise HandoffDataError(
                f"historical handoff {handoff['id']!r} does not match sealed authority state"
            )
        if handoff["result"] is not None:
            if (
                handoff["_state_names"] != COMPLETE_STATE_SEQUENCE
                or event["lifecycle_state"] != "handed_off"
                or event["candidate_sha"] != handoff["result"]["sha"]
                or event["interruption_snapshot"] is not None
            ):
                raise HandoffDataError(
                    f"historical accepted handoff {handoff['id']!r} does not replay cleanly"
                )
            row["outcome"] = "accepted"
            row["result_sha"] = handoff["result"]["sha"]
            commit_line = (
                run_git(source_root, "rev-list", "--parents", "-n", "1", row["result_sha"])
                .decode("ascii")
                .strip()
                .split()
            )
            if len(commit_line) != 2 or commit_line[1] != handoff["assigned_parent_sha"]:
                raise HandoffDataError(
                    f"historical accepted handoff {handoff['id']!r} has the wrong parent"
                )
            message = _commit_message(source_root, row["result_sha"])
            lines = message.split("\n")
            if not lines or lines[-1] != COPILOT_TRAILER or lines.count(COPILOT_TRAILER) != 1:
                raise HandoffDataError(
                    f"historical accepted handoff {handoff['id']!r} is missing the Copilot trailer"
                )
            row["commit_message_sha256"] = hashlib.sha256(message.encode("utf-8")).hexdigest()
            changed_paths, changed_lines = _changed_paths_and_lines(
                source_root,
                handoff["assigned_parent_sha"],
                row["result_sha"],
            )
            row["changed_paths"] = changed_paths
            row["changed_lines"] = changed_lines
            if any(not _path_is_allowed(path, handoff["allowed_scope"]) for path in changed_paths):
                raise HandoffDataError(
                    f"historical accepted handoff {handoff['id']!r} changed out-of-scope paths"
                )
            if changed_lines > handoff["budgets"]["changed_lines"]:
                raise HandoffDataError(
                    f"historical accepted handoff {handoff['id']!r} exceeds its line budget"
                )
            row["budget_usage"]["protocol_changes"] = _derive_protocol_changes(
                source_root,
                handoff["assigned_parent_sha"],
                row["result_sha"],
                changed_paths,
            )
            resource = coordinator_receipt["resources"].get(handoff["id"])
            if _resource_surfaces_changed(changed_paths):
                dependency_inputs = sorted(
                    path
                    for path in changed_paths
                    if not any(path.startswith(prefix) for prefix in PROVEN_HOST_ONLY_PREFIXES)
                )
                if (
                    resource is None
                    or not resource["closed"]
                    or resource["parent_sha"] != handoff["assigned_parent_sha"]
                    or resource["candidate_sha"] != row["result_sha"]
                    or resource["dependency_inputs"] != dependency_inputs
                ):
                    raise HandoffDataError(
                        f"historical accepted handoff {handoff['id']!r} lacks a closed resource receipt"
                    )
                row["budget_usage"]["rom_bytes"] = resource["rom_bytes"]
                row["budget_usage"]["ram_bytes"] = resource["ram_bytes"]
            elif resource is not None:
                raise HandoffDataError(
                    f"historical accepted handoff {handoff['id']!r} has an unexpected resource receipt"
                )
            for field in ("rom_bytes", "ram_bytes", "protocol_changes"):
                if row["budget_usage"][field] > handoff["budgets"][field]:
                    raise HandoffDataError(
                        f"historical accepted handoff {handoff['id']!r} exceeds its {field} budget"
                    )
            missing_evidence = handoff["_required_evidence_ids"] - set(handoff["_evidence"])
            if missing_evidence or not handoff["_evidence"]:
                raise HandoffDataError(
                    f"historical accepted handoff {handoff['id']!r} is missing required evidence"
                )
            referenced_receipts = set()
            for check_id, evidence_id in handoff["_check_evidence_ids"].items():
                evidence = handoff["_evidence"].get(evidence_id)
                if (
                    evidence is None
                    or evidence["kind"] != "check"
                    or evidence["status"] != "passed"
                    or evidence["exit_code"] != 0
                ):
                    raise HandoffDataError(
                        f"historical accepted handoff {handoff['id']!r} has an incomplete check"
                    )
                check = handoff["_required_checks"][check_id]
                receipt_id = check["receipt_id"]
                receipt = handoff["_check_receipts"].get(receipt_id)
                if receipt_id is None or receipt is None or receipt["checker_trust"]["mode"] == "external-bootstrap":
                    raise HandoffDataError(
                        f"historical accepted handoff {handoff['id']!r} has an invalid check receipt"
                    )
                referenced_receipts.add(receipt_id)
                errors = _verify_check_receipt(
                    receipt,
                    check=check,
                    repository_root=source_root,
                    parent_sha=handoff["assigned_parent_sha"],
                    candidate_sha=row["result_sha"],
                )
                if errors:
                    raise HandoffDataError(
                        f"historical accepted handoff {handoff['id']!r} check receipt does not verify"
                    )
            if set(handoff["_check_receipts"]) != referenced_receipts:
                raise HandoffDataError(
                    f"historical accepted handoff {handoff['id']!r} has unexpected check receipts"
                )
        elif handoff["interruption"] is not None:
            interruption = handoff["interruption"]
            if (
                handoff["_state_names"] != INTERRUPTED_STATE_SEQUENCE
                or event["lifecycle_state"] != "interrupted"
                or event["candidate_sha"] is not None
                or event["interruption_snapshot"] != row["interruption_snapshot"]
            ):
                raise HandoffDataError(
                    f"historical interrupted handoff {handoff['id']!r} does not replay cleanly"
                )
            if (
                row["interruption_snapshot"] is None
                or sorted(row["interruption_snapshot"]["preserved_paths"]) != sorted(interruption["preserved_paths"])
                or any(path not in row["interruption_snapshot"]["dirty_paths"] for path in interruption["preserved_paths"])
                or any(not _path_is_allowed(path, handoff["allowed_scope"]) for path in interruption["preserved_paths"])
                or interruption["host_process_actions"]
                or row["recovery_minutes"] <= 0
                or set(interruption["interrupted_check_ids"]) != set(handoff["_required_checks"])
                or handoff["_check_receipts"]
            ):
                raise HandoffDataError(
                    f"historical interrupted handoff {handoff['id']!r} does not verify"
                )
            for check_id in interruption["interrupted_check_ids"]:
                evidence = handoff["_evidence"].get(handoff["_check_evidence_ids"].get(check_id))
                if (
                    evidence is None
                    or evidence["kind"] != "check"
                    or evidence["status"] != "incomplete"
                    or evidence["exit_code"] is not None
                ):
                    raise HandoffDataError(
                        f"historical interrupted handoff {handoff['id']!r} check evidence does not verify"
                    )
            row["outcome"] = "interrupted"
        else:
            raise HandoffDataError(
                f"historical handoff {handoff['id']!r} is not a sealed closed result"
            )
        row["rejection_codes"] = []
        rows.append(row)
    return rows
def _verified_reporter_handoffs(
    document: dict[str, Any],
    source_root: Path,
    original_authority: dict[str, Any],
    current_authority: dict[str, Any],
) -> list[dict[str, Any]]:
    if (
        current_authority["object_id"] == original_authority["object_id"]
        and current_authority["anchor_object_id"] == original_authority["anchor_object_id"]
    ):
        return validate_document(
            copy.deepcopy(document),
            source_root,
            current_time=parse_time(
                document["coordinator_receipt"]["issued_at"],
                "handoff reporter coordinator receipt issued_at",
            ),
        )["handoffs"]
    return _historical_reporter_handoffs(
        document,
        source_root,
        original_authority,
        current_authority,
    )
def _verified_reporter_git_authority(
    document: dict[str, Any],
    source_root: Path,
    original_authority: dict[str, Any],
    current_authority: dict[str, Any],
    handoffs: list[dict[str, Any]],
) -> dict[str, Any]:
    if (
        current_authority["object_id"] == original_authority["object_id"]
        and current_authority["anchor_object_id"] == original_authority["anchor_object_id"]
    ):
        return validate_document(
            copy.deepcopy(document),
            source_root,
            current_time=parse_time(
                document["coordinator_receipt"]["issued_at"],
                "handoff reporter coordinator receipt issued_at",
            ),
        )["git_authority"]
    raw_handoffs = expect_list(
        document["handoffs"],
        "handoff reporter record.document.handoffs",
    )
    branches = {
        expect_string(raw["expected_branch"], f"handoff reporter document.handoffs[{index}].expected_branch")
        for index, raw in enumerate(raw_handoffs)
    }
    if len(branches) != 1:
        raise HandoffDataError("handoff reporter record Git authority has inconsistent branches")
    head_sha = next(
        (handoff["result_sha"] for handoff in reversed(handoffs) if handoff["result_sha"] is not None),
        None,
    )
    if head_sha is None:
        head_sha = next(
            (
                expect_sha(raw["assigned_parent_sha"], f"handoff reporter document.handoffs[{index}].assigned_parent_sha")
                for index, (raw, handoff) in enumerate(zip(raw_handoffs, handoffs))
                if handoff["outcome"] == "interrupted"
            ),
            None,
        )
    if head_sha is None:
        raise HandoffDataError("handoff reporter record Git authority cannot derive a head SHA")
    dirty_paths = sorted(
        {
            path
            for handoff in handoffs
            if handoff["interruption_snapshot"] is not None
            for path in handoff["interruption_snapshot"]["dirty_paths"]
        }
    )
    return {
        "worktree_identity": worktree_identity(source_root),
        "branch": next(iter(branches)),
        "head_sha": head_sha,
        "clean": not dirty_paths,
        "conflicts": [],
        "dirty_paths": dirty_paths,
        "handoffs": [
            {
                "id": handoff["id"],
                "assigned_parent_sha": expect_sha(
                    raw["assigned_parent_sha"],
                    f"handoff reporter document.handoffs[{index}].assigned_parent_sha",
                ),
                "result_sha": handoff["result_sha"],
                "changed_paths": handoff["changed_paths"],
                "changed_lines": handoff["changed_lines"],
                "commit_message_sha256": handoff["commit_message_sha256"],
            }
            for index, (raw, handoff) in enumerate(zip(raw_handoffs, handoffs))
        ],
    }
def _reporter_remote_coverage_rejections(
    document: dict[str, Any],
    document_handoffs: list[dict[str, Any]],
) -> set[str]:
    receipt = expect_object(
        document["coordinator_receipt"],
        "handoff reporter record.document.coordinator_receipt",
    )
    operation = expect_object(
        receipt["operation"],
        "handoff reporter record.document.coordinator_receipt.operation",
    )
    coverage = expect_object(
        receipt["remote_coverage"],
        "handoff reporter record.document.coordinator_receipt.remote_coverage",
    )
    coverage_start = parse_time(
        coverage["interval_start"],
        "handoff reporter remote_coverage.interval_start",
    )
    coverage_end = parse_time(
        coverage["interval_end"],
        "handoff reporter remote_coverage.interval_end",
    )
    eligibility_instant = parse_time(
        operation["eligibility_instant"],
        "handoff reporter operation.eligibility_instant",
    )
    rejections = set()
    if coverage_end != eligibility_instant:
        rejections.add("remote-coverage-incomplete")
    assignment_start = min(
        parse_time(
            handoff["_states"][0]["at"],
            f"handoff reporter handoff {handoff['id']} assignment_sent",
        )
        for handoff in document_handoffs
    )
    if coverage_start > assignment_start:
        rejections.add("remote-coverage-incomplete")
    for index, raw_action in enumerate(
        expect_list(
            coverage["observed_actions"],
            "handoff reporter record.document.coordinator_receipt."
            "remote_coverage.observed_actions",
        )
    ):
        action = _parse_remote_action(
            raw_action,
            "handoff reporter record.document.coordinator_receipt."
            f"remote_coverage.observed_actions[{index}]",
        )
        occurred_at = parse_time(
            action["occurred_at"],
            f"handoff reporter remote action {action['id']}.occurred_at",
        )
        if occurred_at < coverage_start or occurred_at > coverage_end:
            rejections.add("remote-coverage-incomplete")
            break
    incomplete_sources = any(
        not expect_bool(
            source["available"],
            "handoff reporter remote coverage source available",
        )
        or not expect_bool(
            source["complete"],
            "handoff reporter remote coverage source complete",
        )
        for source in expect_list(
            coverage["sources"],
            "handoff reporter record.document.coordinator_receipt."
            "remote_coverage.sources",
        )
    )
    if not incomplete_sources:
        return rejections
    processes = {}
    for index, raw_process in enumerate(
        expect_list(
            coverage["implementation_processes"],
            "handoff reporter record.document.coordinator_receipt."
            "remote_coverage.implementation_processes",
        )
    ):
        label = (
            "handoff reporter record.document.coordinator_receipt."
            f"remote_coverage.implementation_processes[{index}]"
        )
        process = expect_object(raw_process, label)
        expect_keys(
            process,
            label,
            (
                "handoff_id",
                "started_at",
                "ended_at",
                "credentials_available",
                "network_mode",
                "source",
            ),
        )
        handoff_id = expect_string(process["handoff_id"], f"{label}.handoff_id")
        parse_time(process["started_at"], f"{label}.started_at")
        parse_time(process["ended_at"], f"{label}.ended_at")
        expect_bool(
            process["credentials_available"],
            f"{label}.credentials_available",
        )
        expect_enum(
            process["network_mode"],
            {"allowed", "denied"},
            f"{label}.network_mode",
        )
        if process["source"] != "coordinator-launcher":
            raise HandoffDataError(f"{label}.source must be coordinator-launcher")
        if handoff_id in processes:
            raise HandoffDataError(
                f"{label}.handoff_id duplicates an implementation process"
            )
        processes[handoff_id] = process
    for handoff in document_handoffs:
        process = processes.get(handoff["id"])
        if process is None:
            rejections.add("remote-coverage-incomplete")
            break
        if (
            process["credentials_available"]
            or process["network_mode"] != "denied"
            or parse_time(
                process["started_at"],
                f"handoff reporter process {handoff['id']}.started_at",
            )
            > parse_time(
                handoff["_states"][0]["at"],
                f"handoff reporter handoff {handoff['id']} assignment_sent",
            )
            or parse_time(
                process["ended_at"],
                f"handoff reporter process {handoff['id']}.ended_at",
            )
            < parse_time(
                handoff["_states"][-1]["at"],
                f"handoff reporter handoff {handoff['id']} last_state",
            )
        ):
            rejections.add("remote-coverage-incomplete")
            break
    return rejections
def derive_reporter_result_summary(
    document: dict[str, Any],
    result: dict[str, Any],
) -> tuple[dict[str, Any], list[str], dict[str, Any], list[dict[str, Any]]]:
    document_handoffs = [
        _parse_handoff(copy.deepcopy(raw), index)
        for index, raw in enumerate(
            expect_list(
                document["handoffs"],
                "handoff reporter record.document.handoffs",
            )
        )
    ]
    reported_handoffs = _parse_reporter_result_handoffs(result["handoffs"])
    expected_ids = sorted(handoff["id"] for handoff in document_handoffs)
    if sorted(handoff["id"] for handoff in reported_handoffs) != expected_ids:
        raise HandoffDataError(
            "handoff reporter result handoff identities contradict its document"
        )
    if result["repository"] != document["repository"]:
        raise HandoffDataError(
            "handoff reporter result repository contradicts its document"
        )
    coordinators = _parse_coordinators(copy.deepcopy(document["coordinators"]))
    if coordinators:
        expected_coordinator_id = coordinators[0]["id"]
    else:
        expected_coordinator_id = None
    if result["coordinator_id"] != expected_coordinator_id:
        raise HandoffDataError(
            "handoff reporter result coordinator identity contradicts its document"
        )
    delivery_graph = evaluate_delivery_graph(copy.deepcopy(document["delivery_graph"]))
    runs = _parse_runs(copy.deepcopy(document["workflow_runs"]))
    watchers = _parse_watchers(copy.deepcopy(document["watchers"]))
    global_rejections = _reporter_remote_coverage_rejections(
        document,
        document_handoffs,
    )
    document_handoffs_by_id = {
        handoff["id"]: handoff for handoff in document_handoffs
    }
    structural_row_rejections = {
        handoff["id"]: _offline_structural_reporter_rejection_codes(handoff)
        for handoff in reported_handoffs
    }
    canonical_row_outcomes = {
        handoff["id"]: _offline_structural_reporter_outcome(
            document_handoffs_by_id[handoff["id"]],
            handoff,
        )
        for handoff in reported_handoffs
    }
    watcher_results = []
    watcher_counts = Counter(watcher["run_id"] for watcher in watchers)
    if any(count > 1 for count in watcher_counts.values()):
        global_rejections.add("duplicate-watcher")
    for run_id, run in sorted(runs.items()):
        matching = [watcher for watcher in watchers if watcher["run_id"] == run_id]
        if len(matching) != 1:
            global_rejections.add("missing-or-duplicate-watcher")
            continue
        watcher = matching[0]
        if watcher["head_sha"] != run["head_sha"]:
            global_rejections.add("watcher-run-mismatch")
        if watcher["coordinator_id"] != expected_coordinator_id:
            global_rejections.add("watcher-owner-mismatch")
        if parse_time(
            run["observed_at"],
            f"handoff reporter run {run_id}.observed_at",
        ) < parse_time(
            watcher["ended_at"],
            f"handoff reporter watcher {watcher['id']}.ended_at",
        ):
            global_rejections.add("watcher-authority-stale")
        authoritative_outcome = (
            run["conclusion"] if run["status"] == "completed" else "active"
        )
        watcher_results.append(
            {
                "run_id": run_id,
                "head_sha": run["head_sha"],
                "watcher_process_result": watcher["process_result"],
                "authoritative_outcome": authoritative_outcome,
                "reconciled": (
                    watcher["process_result"] != "success"
                    and run["status"] == "completed"
                ),
            }
        )
        if run["status"] != "completed":
            global_rejections.add("authoritative-run-incomplete")
        elif run["conclusion"] != "success":
            global_rejections.add("authoritative-run-failed")
    local_rejections = {
        code
        for handoff in reported_handoffs
        for code in (
            set(handoff["rejection_codes"])
            | structural_row_rejections[handoff["id"]]
        )
    }
    rejection_codes = sorted(local_rejections | global_rejections)
    completed = [
        handoff
        for handoff in reported_handoffs
        if canonical_row_outcomes[handoff["id"]] == "accepted"
    ]
    trusted_push_eligible = (
        len(completed) == len(reported_handoffs) == 1
        and not any(
            code
            for code in rejection_codes
            if code not in TRUSTED_PUSH_SUMMARY_EXEMPT_REJECTIONS
        )
    )
    delivery_eligible = trusted_push_eligible and all(
        parent["delivery_eligible"] for parent in delivery_graph["parent_delivery"]
    ) and all(
        run["status"] == "completed" and run["conclusion"] == "success"
        for run in runs.values()
    )
    summary = {
        "trusted_push_eligible": trusted_push_eligible,
        "delivery_eligible": delivery_eligible,
        "accepted_handoffs": len(completed),
        "rejected_handoffs": sum(
            canonical_row_outcomes[handoff["id"]] == "rejected"
            for handoff in reported_handoffs
        ),
        "interrupted_handoffs": sum(
            canonical_row_outcomes[handoff["id"]] == "interrupted"
            for handoff in reported_handoffs
        ),
        "stale_responses": sum(
            handoff["stale_response"] for handoff in reported_handoffs
        ),
        "max_owner_lifetime_seconds": max(
            handoff["lifetime_seconds"] for handoff in reported_handoffs
        ),
        "max_peak_rss_bytes": max(
            handoff["peak_rss_bytes"] for handoff in reported_handoffs
        ),
        "coordination_turns": sum(
            handoff["coordination_turns"] for handoff in reported_handoffs
        ),
        "recovery_count": sum(
            handoff["interruption"] is not None for handoff in document_handoffs
        ),
        "recovery_minutes": sum(
            handoff["recovery_minutes"] for handoff in reported_handoffs
        ),
        "rejection_codes": rejection_codes,
    }
    return summary, sorted(global_rejections), delivery_graph, watcher_results
def _parse_reporter_result_summary(
    raw_summary: Any,
    *,
    expected_summary: dict[str, Any],
) -> dict[str, Any]:
    summary = copy.deepcopy(
        expect_object(raw_summary, "handoff verification result summary")
    )
    expect_keys(
        summary,
        "handoff verification result summary",
        (
            "trusted_push_eligible",
            "delivery_eligible",
            "accepted_handoffs",
            "rejected_handoffs",
            "interrupted_handoffs",
            "stale_responses",
            "max_owner_lifetime_seconds",
            "max_peak_rss_bytes",
            "coordination_turns",
            "recovery_count",
            "recovery_minutes",
            "rejection_codes",
        ),
    )
    for field in ("trusted_push_eligible", "delivery_eligible"):
        expect_bool(
            summary[field],
            f"handoff verification result summary.{field}",
        )
    for field in (
        "accepted_handoffs",
        "rejected_handoffs",
        "interrupted_handoffs",
        "stale_responses",
        "max_owner_lifetime_seconds",
        "max_peak_rss_bytes",
        "coordination_turns",
        "recovery_count",
        "recovery_minutes",
    ):
        expect_int(
            summary[field],
            f"handoff verification result summary.{field}",
            0,
        )
    rejection_codes = expect_list(
        summary["rejection_codes"],
        "handoff verification result summary.rejection_codes",
    )
    for index, code in enumerate(rejection_codes):
        expect_enum(
            code,
            reporter.HANDOFF_REJECTION_CODES,
            f"handoff verification result summary.rejection_codes[{index}]",
        )
    expect_unique(
        rejection_codes,
        "handoff verification result summary.rejection_codes",
    )
    for field in (
        "accepted_handoffs",
        "rejected_handoffs",
        "interrupted_handoffs",
        "stale_responses",
        "max_owner_lifetime_seconds",
        "max_peak_rss_bytes",
        "coordination_turns",
        "recovery_count",
        "recovery_minutes",
    ):
        if summary[field] != expected_summary[field]:
            raise HandoffDataError(
                "handoff verification result summary does not verify"
            )
    expected_codes = set(expected_summary["rejection_codes"])
    actual_codes = set(rejection_codes)
    if not expected_codes <= actual_codes or not (
        actual_codes - expected_codes
    ) <= STRUCTURAL_SUMMARY_LIVE_ONLY_REJECTIONS:
        raise HandoffDataError(
            "handoff verification result summary does not verify"
        )
    if actual_codes == expected_codes:
        if (
            summary["trusted_push_eligible"]
            != expected_summary["trusted_push_eligible"]
            or summary["delivery_eligible"]
            != expected_summary["delivery_eligible"]
        ):
            raise HandoffDataError(
                "handoff verification result summary does not verify"
            )
    elif summary["trusted_push_eligible"] or summary["delivery_eligible"]:
        raise HandoffDataError(
            "handoff verification result summary does not verify"
        )
    return summary
def _verify_structural_reporter_handoffs(
    document: dict[str, Any],
    reported_handoffs: list[dict[str, Any]],
) -> None:
    document_handoffs = {
        handoff["id"]: handoff
        for handoff in (
            _parse_handoff(copy.deepcopy(raw), index)
            for index, raw in enumerate(
                expect_list(
                    document["handoffs"],
                    "handoff reporter record.document.handoffs",
                )
            )
        )
    }
    telemetry = {}
    for index, raw_metric in enumerate(
        expect_list(
            document["coordinator_receipt"]["runtime_telemetry"],
            "handoff reporter record.document.coordinator_receipt.runtime_telemetry",
        )
    ):
        label = (
            "handoff reporter record.document.coordinator_receipt."
            f"runtime_telemetry[{index}]"
        )
        metric = expect_object(raw_metric, label)
        expect_keys(
            metric,
            label,
            (
                "handoff_id",
                "owner_database_id",
                "started_at",
                "ended_at",
                "peak_rss_bytes",
                "coordination_turns",
                "recovery_minutes",
                "interruption_snapshot",
                "source",
            ),
        )
        handoff_id = expect_string(metric["handoff_id"], f"{label}.handoff_id")
        if handoff_id in telemetry:
            raise HandoffDataError(
                "handoff verification result handoffs do not verify"
            )
        telemetry[handoff_id] = metric
    for row in reported_handoffs:
        handoff = document_handoffs.get(row["id"])
        metric = telemetry.get(row["id"])
        if handoff is None or metric is None:
            raise HandoffDataError(
                "handoff verification result handoffs do not verify"
            )
        derived_rejections = _offline_structural_reporter_rejection_codes(row)
        if (
            row["owner_id"] != handoff["_owner"]["identity"]
            or row["issue"] != handoff["issue"]
            or row["pull_request"] != handoff["pull_request"]
            or row["assigned_at"] != handoff["_states"][0]["at"]
            or row["state"] != handoff["_state_names"][-1]
            or not derived_rejections <= set(row["rejection_codes"])
        ):
            raise HandoffDataError(
                "handoff verification result handoffs do not verify"
            )
        expected_closed_at = (
            handoff["_states"][-1]["at"]
            if handoff["_state_names"][-1] in {"handed_off", "interrupted"}
            else None
        )
        expected_result_sha = (
            None if handoff["result"] is None else handoff["result"]["sha"]
        )
        if (
            row["closed_at"] != expected_closed_at
            or row["result_sha"] != expected_result_sha
            or row["stale_response"]
            != (
                expected_result_sha is not None
                and expected_result_sha == handoff["assigned_parent_sha"]
            )
            or row["peak_rss_bytes"]
            != expect_int(metric["peak_rss_bytes"], f"{row['id']} peak_rss_bytes", 0)
            or row["coordination_turns"]
            != expect_int(
                metric["coordination_turns"],
                f"{row['id']} coordination_turns",
                0,
            )
            or row["recovery_minutes"]
            != expect_int(
                metric["recovery_minutes"],
                f"{row['id']} recovery_minutes",
                0,
            )
            or row["interruption_snapshot"] != metric["interruption_snapshot"]
        ):
            raise HandoffDataError(
                "handoff verification result handoffs do not verify"
            )
        lifetime_seconds = whole_second_duration(
            parse_time(metric["started_at"], f"{row['id']} started_at"),
            parse_time(metric["ended_at"], f"{row['id']} ended_at"),
            label=f"{row['id']} lifetime",
        )
        if lifetime_seconds is None or row["lifetime_seconds"] != lifetime_seconds:
            raise HandoffDataError(
                "handoff verification result handoffs do not verify"
            )
        expected_outcome = _offline_structural_reporter_outcome(
            handoff,
            row,
        )
        if row["outcome"] != expected_outcome:
            raise HandoffDataError(
                "handoff verification result handoffs do not verify"
            )
def _structural_reporter_git_authority(
    document: dict[str, Any],
    handoffs: list[dict[str, Any]],
    *,
    git_authority: dict[str, Any],
) -> dict[str, Any]:
    worktree_identity_value = git_authority["worktree_identity"]
    if (
        not isinstance(worktree_identity_value, str)
        or reporter.SHA256_RE.fullmatch(worktree_identity_value) is None
    ):
        raise HandoffDataError(
            "handoff verification Git authority worktree_identity does not verify"
        )
    raw_handoffs = expect_list(
        document["handoffs"],
        "handoff reporter record.document.handoffs",
    )
    branches = {
        expect_string(
            raw["expected_branch"],
            f"handoff reporter document.handoffs[{index}].expected_branch",
        )
        for index, raw in enumerate(raw_handoffs)
    }
    if len(branches) != 1:
        raise HandoffDataError(
            "handoff reporter record Git authority has inconsistent branches"
        )
    accepted_results = [
        handoff["result_sha"]
        for handoff in handoffs
        if handoff["outcome"] == "accepted" and handoff["result_sha"] is not None
    ]
    if accepted_results:
        head_sha = accepted_results[-1]
    elif any(handoff["outcome"] == "interrupted" for handoff in handoffs):
        head_sha = next(
            (
                expect_sha(
                    raw["assigned_parent_sha"],
                    "handoff reporter document.handoffs"
                    f"[{index}].assigned_parent_sha",
                )
                for index, (raw, handoff) in enumerate(
                    zip(raw_handoffs, handoffs)
                )
                if handoff["outcome"] == "interrupted"
            ),
            None,
        )
    else:
        head_sha = expect_sha(
            git_authority.get("head_sha"),
            "handoff verification result.git_authority.head_sha",
        )
    dirty_paths = sorted(
        {
            path
            for handoff in handoffs
            if handoff["interruption_snapshot"] is not None
            for path in handoff["interruption_snapshot"]["dirty_paths"]
        }
    )
    return {
        "worktree_identity": worktree_identity_value,
        "branch": next(iter(branches)),
        "head_sha": head_sha,
        "clean": not dirty_paths,
        "conflicts": [],
        "dirty_paths": dirty_paths,
        "handoffs": [
            {
                "id": handoff["id"],
                "assigned_parent_sha": expect_sha(
                    raw["assigned_parent_sha"],
                    "handoff reporter document.handoffs"
                    f"[{index}].assigned_parent_sha",
                ),
                "result_sha": handoff["result_sha"],
                "changed_paths": handoff["changed_paths"],
                "changed_lines": handoff["changed_lines"],
                "commit_message_sha256": handoff["commit_message_sha256"],
            }
            for index, (raw, handoff) in enumerate(zip(raw_handoffs, handoffs))
        ],
    }
def _verify_handoff_document_result(
    document: dict[str, Any],
    result: dict[str, Any],
    *,
    revalidate_git: bool,
    repository_root: Path | None = None,
    trusted_anchor: dict[str, Any] | None = None,
    trusted_installation: Path | None = None,
    current_time: datetime | None = None,
    current_authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expect_keys(
        document,
        "handoff verification document",
        (
            "schema_version",
            "repository",
            "prior_handoffs",
            "history_authority",
            "delivery_graph",
            "coordinators",
            "handoffs",
            "workflow_runs",
            "watchers",
            "coordinator_receipt",
        ),
    )
    expect_list(
        document["handoffs"],
        "handoff verification document.handoffs",
    )
    validate_prior_handoffs(document["prior_handoffs"])
    expect_keys(
        result,
        "handoff verification result",
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
        value = result[field]
        if (
            not isinstance(value, str)
            or reporter.SHA256_RE.fullmatch(value) is None
        ):
            raise HandoffDataError(
                f"handoff verification result.{field} must be a lowercase SHA-256"
            )
    if result["input_seal"] != hashlib.sha256(
        INPUT_SEAL_DOMAIN + normalized_json(document)
    ).hexdigest():
        raise HandoffDataError(
            "handoff verification result input seal does not verify"
        )
    if result["git_seal"] != seal_git_authority(result["git_authority"]):
        raise HandoffDataError(
            "handoff verification result Git seal does not verify"
        )
    if result["result_seal"] != seal_handoff_result(result):
        raise HandoffDataError(
            "handoff verification result seal does not verify"
        )
    reported_handoffs = _parse_reporter_result_handoffs(result["handoffs"])
    expected_summary, _global_rejection_codes, delivery_graph, watcher_results = (
        derive_reporter_result_summary(document, result)
    )
    _parse_reporter_result_summary(
        result["summary"],
        expected_summary=expected_summary,
    )
    _verify_structural_reporter_handoffs(
        document,
        reported_handoffs,
    )
    if result["delivery_graph"] != delivery_graph:
        raise HandoffDataError(
            "handoff verification delivery graph does not verify"
        )
    if result["watchers"] != watcher_results:
        raise HandoffDataError(
            "handoff verification watcher summary does not verify"
        )
    git_authority = expect_object(
        result["git_authority"],
        "handoff verification result.git_authority",
    )
    expect_keys(
        git_authority,
        "handoff verification result.git_authority",
        (
            "worktree_identity",
            "branch",
            "head_sha",
            "clean",
            "conflicts",
            "dirty_paths",
            "handoffs",
        ),
    )
    expected_structural_git_authority = _structural_reporter_git_authority(
        document,
        reported_handoffs,
        git_authority=git_authority,
    )
    if git_authority != expected_structural_git_authority:
        raise HandoffDataError(
            "handoff verification Git authority does not verify"
        )
    source_worktrees = {
        handoff["allowed_worktree"]
        for handoff in document["handoffs"]
    }
    if len(source_worktrees) != 1:
        raise HandoffDataError(
            "handoff verification must identify one source worktree"
        )
    receipt = expect_object(
        document["coordinator_receipt"],
        "handoff verification coordinator receipt",
    )
    original_authority = expect_object(
        document["history_authority"],
        "handoff verification original authority",
    )
    provided_current_authority = None
    if current_authority is not None:
        provided_current_authority = copy.deepcopy(
            expect_object(
                current_authority,
                "handoff verification current authority",
            )
        )
    repository = (
        expect_string(
            original_authority["repository"],
            "handoff verification original authority.repository",
        )
        if provided_current_authority is None
        else expect_string(
            provided_current_authority["repository"],
            "handoff verification current authority.repository",
        )
    )
    issue = (
        expect_int(
            original_authority["issue"],
            "handoff verification original authority.issue",
            1,
        )
        if provided_current_authority is None
        else expect_int(
            provided_current_authority["issue"],
            "handoff verification current authority.issue",
            1,
        )
    )
    if provided_current_authority is None:
        original_object_id = expect_sha(
            original_authority["object_id"],
            "handoff verification original authority.object_id",
        )
        original_anchor_object_id = expect_sha(
            original_authority["anchor_object_id"],
            "handoff verification original authority.anchor_object_id",
        )
        canonical_history_events = original_authority["history_events"]
    else:
        current_publication = expect_object(
            provided_current_authority["publication_attestation"],
            "handoff verification current authority.publication_attestation",
        )
        original_object_id = expect_sha(
            provided_current_authority["previous_object_id"],
            "handoff verification current authority.previous_object_id",
        )
        original_anchor_object_id = expect_sha(
            current_publication["anchor_object_id"],
            "handoff verification current authority.publication_attestation.anchor_object_id",
        )
        canonical_history_events = provided_current_authority["history_events"]
    original_signer = _parse_signer_public(
        original_authority["signer"],
        "handoff verification original signer",
    )
    if trusted_anchor is not None:
        if trusted_installation is None:
            raise HandoffDataError(
                "handoff reporter offline verification requires trusted installation"
            )
        verified_installation = _load_reporter_trusted_installation(
            trusted_installation,
            repository_root=repository_root,
            label="handoff reporter trusted installation",
        )
        trusted = _verify_reporter_trust_anchor(
            trusted_anchor,
            expected_input_seal=result["input_seal"],
            original_authority=original_authority,
            trusted_installation=verified_installation,
            current_time=current_time,
            label="handoff reporter trusted anchor",
        )
        if (
            expect_int(
                receipt["repository_database_id"],
                "handoff verification coordinator receipt.repository_database_id",
                1,
            )
            != trusted["repository_database_id"]
        ):
            raise HandoffDataError(
                "handoff reporter trusted installation does not match its record"
            )
        original_signer = trusted["signer"]
    verify_external_signature(
        original_signer,
        coordinator_attestation_payload(document),
        receipt["signature"],
        "handoff verification coordinator signature",
    )
    if not revalidate_git:
        return original_signer
    if repository_root is None:
        raise HandoffDataError(
            "handoff reporter live Git revalidation requires repository_root"
        )
    source_root = validate_repository_root(repository_root)
    original_anchor, _anchor_parents = _read_history_anchor_commit(
        source_root,
        original_anchor_object_id,
        repository,
        issue,
    )
    if original_anchor["authority_object_id"] != original_object_id:
        raise HandoffDataError(
            "historical handoff anchor does not bind original authority"
        )
    original_record, _parents = _read_history_authority_commit(
        source_root,
        original_object_id,
        repository,
        issue,
        expected_previous_anchor_object_id=original_anchor["previous_object_id"],
    )
    if original_anchor["sequence"] != original_record["sequence"]:
        raise HandoffDataError(
            "historical handoff anchor does not bind original authority"
        )
    event_history_receipt = None
    if original_record["event"]["kind"] == "handoff":
        original_history_events = expect_list(
            canonical_history_events,
            (
                "handoff verification original authority.history_events"
                if provided_current_authority is None
                else "handoff verification current authority.history_events"
            ),
        )
        event_history_receipt = (
            None
            if not original_history_events
            else original_history_events[-1]["history_receipt"]
        )
    canonical_original_authority = _canonical_public_history_authority(
        original_record,
        object_id=original_object_id,
        anchor_object_id=original_anchor_object_id,
        history_events=canonical_history_events,
        event_history_receipt=event_history_receipt,
        label="handoff verification original authority",
    )
    if original_authority != canonical_original_authority:
        raise HandoffDataError(
            "historical handoff authority object does not match the canonical Git ref"
        )
    if provided_current_authority is None:
        current_authority = read_history_authority(
            source_root,
            repository,
            issue,
            None,
        )
    else:
        current_authority = provided_current_authority
    for original_id, current_id, label in (
        (
            original_authority["object_id"],
            current_authority["object_id"],
            "authority",
        ),
        (
            original_authority["anchor_object_id"],
            current_authority["anchor_object_id"],
            "anchor",
        ),
    ):
        ancestry = subprocess.run(
            reporter.git_command(
                source_root,
                "merge-base",
                "--is-ancestor",
                original_id,
                current_id,
            ),
            cwd=source_root,
            env=reporter.git_environment(offline=True),
            check=False,
            capture_output=True,
        )
        if ancestry.returncode != 0:
            raise HandoffDataError(
                f"historical handoff {label} is not in current protected head"
            )
    for handoff_index, handoff in enumerate(document["handoffs"]):
        _parse_check_receipts(
            handoff["check_receipts"],
            f"handoff verification document.handoffs[{handoff_index}]",
        )
        for check_receipt in handoff["check_receipts"]:
            if check_receipt["seal"] != seal_check_receipt(check_receipt):
                raise HandoffDataError(
                    "handoff verification check receipt does not verify"
                )
    verified_handoffs = _verified_reporter_handoffs(
        document,
        source_root,
        original_authority,
        current_authority,
    )
    if reported_handoffs != verified_handoffs:
        raise HandoffDataError(
            "handoff verification result handoffs do not verify"
        )
    expected_git_authority = _verified_reporter_git_authority(
        document,
        source_root,
        original_authority,
        current_authority,
        verified_handoffs,
    )
    if (
        result["git_authority"] != expected_git_authority
        or result["git_seal"] != seal_git_authority(expected_git_authority)
    ):
        raise HandoffDataError(
            "handoff verification Git authority does not verify"
        )
    return original_signer
def _verify_history_event_carrier(
    raw_history_carrier: Any,
    *,
    repository_root: Path,
    current_authority: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    history_carrier = _parse_history_carrier(
        raw_history_carrier,
        label=label,
    )
    document = expect_object(
        history_carrier["document"],
        f"{label}.document",
    )
    result = expect_object(
        history_carrier["result"],
        f"{label}.result",
    )
    _verify_handoff_document_result(
        document,
        result,
        revalidate_git=True,
        repository_root=repository_root,
        current_authority=current_authority,
    )
    return make_history_receipt(
        copy.deepcopy(document),
        copy.deepcopy(result),
        expect_string(
            history_carrier["selected_handoff_id"],
            f"{label}.selected_handoff_id",
        ),
        canonical_result=copy.deepcopy(result),
    )
def reporter_record(
    document: dict[str, Any],
    result: dict[str, Any],
    result_attestation: dict[str, Any],
    *,
    trusted_anchor: dict[str, Any],
    trusted_installation: Path,
    repository_root: Path,
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
        "result_attestation": copy.deepcopy(result_attestation),
    }
    verify_reporter_record(
        record,
        revalidate_git=False,
        repository_root=repository_root,
        trusted_anchor=trusted_anchor,
        trusted_installation=trusted_installation,
    )
    return record
def verify_reporter_record(
    raw_record: Any,
    *,
    revalidate_git: bool,
    repository_root: Path | None = None,
    trusted_anchor: dict[str, Any] | None = None,
    trusted_installation: Path | None = None,
    current_time: datetime | None = None,
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
            "result_attestation",
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
    result = expect_object(
        record["result"],
        "handoff reporter record.result",
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
    if record["input_seal"] != result["input_seal"]:
        raise HandoffDataError(
            "handoff reporter record.input_seal contradicts its result"
        )
    if record["git_seal"] != result["git_seal"]:
        raise HandoffDataError(
            "handoff reporter record.git_seal contradicts its result"
        )
    if record["result_seal"] != result["result_seal"]:
        raise HandoffDataError(
            "handoff reporter record.result_seal contradicts its result"
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
    result_attestation = expect_object(
        record["result_attestation"],
        "handoff reporter result attestation",
    )
    if not revalidate_git and (
        trusted_anchor is None or trusted_installation is None
    ):
        raise HandoffDataError(
            "handoff reporter offline verification requires trusted anchor and installation"
        )
    expect_keys(
        result_attestation,
        "handoff reporter result attestation",
        (
            "signer_key_id",
            "operation_nonce",
            "consume_store_id",
            "consume_sequence",
            "consume_anchor",
            "signature",
        ),
    )
    operation = document["coordinator_receipt"]["operation"]
    original_signer = _verify_handoff_document_result(
        document,
        result,
        revalidate_git=revalidate_git,
        repository_root=repository_root,
        trusted_anchor=trusted_anchor,
        trusted_installation=trusted_installation,
        current_time=current_time,
    )
    if (
        result_attestation["signer_key_id"] != original_signer["key_id"]
        or result_attestation["operation_nonce"] != operation["nonce"]
        or result_attestation["consume_store_id"]
        != operation["consume_store_id"]
        or result_attestation["consume_sequence"]
        != operation["consume_sequence"]
        or result_attestation["consume_anchor"]
        != operation["consume_anchor"]
    ):
        raise HandoffDataError(
            "handoff reporter result attestation identity mismatch"
        )
    verify_external_signature(
        original_signer,
        result_attestation_payload(document, result),
        result_attestation["signature"],
        "handoff reporter result signature",
    )
    return record
def _repository_from_origin(
    repository_root: Path, installation_path: Path | None,
) -> str:
    origin = _transport_endpoint(repository_root, installation_path)
    repository = reporter._github_repository_from_remote(origin)  # noqa: SLF001
    if repository is None and (
        origin.startswith("file://") or Path(origin).is_absolute()
    ):
        repository = read_remote_repository_identity(repository_root, installation_path)
    if repository is None:
        raise HandoffDataError(
            "worktree origin must identify one GitHub repository or "
            "owner-provisioned local authority"
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
            "handoff_kind",
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
            "states",
            "evidence",
            "check_receipts",
            "result",
            "interruption",
            "recovery_resolution",
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
    handoff_kind = expect_enum(
        handoff["handoff_kind"],
        {"root", "oom_replacement", "review_successor"},
        f"{label}.handoff_kind",
    )
    if handoff["replaces_handoff_id"] is not None:
        expect_string(
            handoff["replaces_handoff_id"],
            f"{label}.replaces_handoff_id",
        )
    if (handoff_kind == "root") != (
        handoff["replaces_handoff_id"] is None
    ):
        raise HandoffDataError(
            f"{label}.handoff_kind contradicts predecessor"
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
    states, state_names = _parse_states(handoff["states"], label)
    evidence = _parse_evidence(handoff["evidence"], label)
    check_receipts = _parse_check_receipts(handoff["check_receipts"], label)
    result = handoff["result"]
    if result is not None:
        result = expect_object(result, f"{label}.result")
        expect_keys(result, f"{label}.result", ("sha",))
        expect_sha(result["sha"], f"{label}.result.sha")
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
        if interruption["replacement_handoff_id"] is not None:
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
    recovery_resolution = []
    for resolution_index, raw_resolution in enumerate(
        expect_list(
            handoff["recovery_resolution"],
            f"{label}.recovery_resolution",
        )
    ):
        resolution_label = (
            f"{label}.recovery_resolution[{resolution_index}]"
        )
        resolution = expect_object(raw_resolution, resolution_label)
        expect_keys(
            resolution,
            resolution_label,
            (
                "path",
                "original_sha256",
                "disposition",
                "result_path",
                "result_blob_oid",
                "reason",
            ),
        )
        _validate_repository_path(
            resolution["path"],
            f"{resolution_label}.path",
            prefix=False,
        )
        if (
            not isinstance(resolution["original_sha256"], str)
            or reporter.SHA256_RE.fullmatch(
                resolution["original_sha256"]
            )
            is None
        ):
            raise HandoffDataError(
                f"{resolution_label}.original_sha256 is invalid"
            )
        disposition = expect_enum(
            resolution["disposition"],
            {"restored", "resolved"},
            f"{resolution_label}.disposition",
        )
        if disposition == "restored":
            _validate_repository_path(
                resolution["result_path"],
                f"{resolution_label}.result_path",
                prefix=False,
            )
            expect_sha(
                resolution["result_blob_oid"],
                f"{resolution_label}.result_blob_oid",
            )
            if resolution["reason"] is not None:
                raise HandoffDataError(
                    f"{resolution_label}.reason is only for resolved content"
                )
        else:
            if resolution["result_path"] is not None:
                _validate_repository_path(
                    resolution["result_path"],
                    f"{resolution_label}.result_path",
                    prefix=False,
                )
            if resolution["result_blob_oid"] is not None:
                expect_sha(
                    resolution["result_blob_oid"],
                    f"{resolution_label}.result_blob_oid",
                )
            expect_string(
                resolution["reason"],
                f"{resolution_label}.reason",
            )
        recovery_resolution.append(resolution)
    expect_unique(
        (item["path"] for item in recovery_resolution),
        f"{label}.recovery_resolution paths",
    )
    if handoff_kind != "oom_replacement" and recovery_resolution:
        raise HandoffDataError(
            f"{label}.recovery_resolution is only for OOM replacement"
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
    handoff["_recovery_resolution"] = recovery_resolution
    return handoff
def _parse_coordinators(raw_coordinators: Any) -> list[dict[str, Any]]:
    coordinators = []
    for index, raw in enumerate(expect_list(raw_coordinators, "coordinators")):
        label = f"coordinators[{index}]"
        coordinator = expect_object(raw, label)
        expect_keys(
            coordinator,
            label,
            ("id", "login", "database_id"),
        )
        expect_string(coordinator["id"], f"{label}.id")
        coordinator["_actor"] = _parse_actor(
            coordinator["login"],
            coordinator["database_id"],
            f"{label}.actor",
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
def _parse_remote_action(raw: Any, label: str) -> dict[str, Any]:
    action = expect_object(raw, label)
    expect_keys(
        action,
        label,
        (
            "id",
            "handoff_id",
            "actor_login",
            "actor_database_id",
            "action",
            "occurred_at",
            "source",
        ),
    )
    expect_string(action["id"], f"{label}.id")
    expect_string(action["handoff_id"], f"{label}.handoff_id")
    action["_actor"] = _parse_actor(
        action["actor_login"],
        action["actor_database_id"],
        f"{label}.actor",
    )
    expect_enum(action["action"], set(REMOTE_ACTIONS), f"{label}.action")
    parse_time(action["occurred_at"], f"{label}.occurred_at")
    expect_enum(
        action["source"],
        set(REMOTE_COVERAGE_SOURCES),
        f"{label}.source",
    )
    return action
def _public_action(action: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in action.items() if not key.startswith("_")
    }
def _public_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _public_json(item)
            for key, item in value.items()
            if not key.startswith("_")
        }
    if isinstance(value, list):
        return [_public_json(item) for item in value]
    return value
def coordinator_attestation_payload(document: dict[str, Any]) -> bytes:
    payload = _public_json(document)
    receipt = expect_object(
        payload["coordinator_receipt"],
        "coordinator_receipt",
    )
    receipt.pop("signature", None)
    return (
        COORDINATOR_RECEIPT_SEAL_DOMAIN
        + normalized_json(payload)
    )
def result_attestation_payload(
    document: dict[str, Any],
    result: dict[str, Any],
) -> bytes:
    operation = document["coordinator_receipt"]["operation"]
    return RESULT_ATTESTATION_DOMAIN + normalized_json(
        {
            "document": _public_json(document),
            "result": _public_json(result),
            "operation_nonce": operation["nonce"],
            "consume_store_id": operation["consume_store_id"],
            "consume_sequence": operation["consume_sequence"],
            "consume_anchor": operation["consume_anchor"],
        }
    )
def parse_pull_request_observation(
    raw: Any,
    *,
    signer: dict[str, Any],
    repository: str,
    repository_database_id: int,
    authority_object_id: str,
    anchor_object_id: str,
    live: bool = False,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    label = "GitHub pull request observation"
    observation = copy.deepcopy(expect_object(raw, label))
    expect_keys(
        observation,
        label,
        (
            "source",
            "repository_id",
            "repository_full_name",
            "pull_request",
            "state",
            "merged",
            "base_branch",
            "head_branch",
            "head_repository_full_name",
            "base_oid",
            "head_oid",
            "created_at",
            "coordinator_database_id",
            "observed_at",
            "authority_object_id",
            "anchor_object_id",
            "expected_handoff_branch",
            "delivery_branch",
            "signature",
        ),
    )
    if observation["source"] != "github-pull-request-api":
        raise HandoffDataError(
            "PR binding must use a GitHub pull request API response"
        )
    if (
        expect_int(observation["repository_id"], f"{label}.repository_id", 1)
        != repository_database_id
        or expect_string(
            observation["repository_full_name"],
            f"{label}.repository_full_name",
        )
        != repository
        or expect_string(
            observation["head_repository_full_name"],
            f"{label}.head_repository_full_name",
        )
        != repository
    ):
        raise HandoffDataError("GitHub PR observation repository mismatch")
    expect_int(observation["pull_request"], f"{label}.pull_request", 1)
    if (
        observation["state"] != "OPEN"
        or expect_bool(observation["merged"], f"{label}.merged")
    ):
        raise HandoffDataError(
            "GitHub PR observation must be OPEN and unmerged"
        )
    for field in (
        "base_branch",
        "head_branch",
        "expected_handoff_branch",
        "delivery_branch",
    ):
        expect_string(observation[field], f"{label}.{field}")
    if (
        observation["head_branch"] != observation["expected_handoff_branch"]
        or observation["head_branch"] != observation["delivery_branch"]
    ):
        raise HandoffDataError(
            "GitHub PR observation does not match handoff/delivery branch"
        )
    expect_sha(observation["base_oid"], f"{label}.base_oid")
    expect_sha(observation["head_oid"], f"{label}.head_oid")
    parse_time(observation["created_at"], f"{label}.created_at")
    observed_at = parse_time(
        observation["observed_at"],
        f"{label}.observed_at",
    )
    if live:
        require_fresh_live_timestamp(
            observed_at,
            label="GitHub PR observation",
            current_time=authoritative_current_time(
                current_time,
                label="GitHub PR observation current_time",
            ),
        )
    expect_int(
        observation["coordinator_database_id"],
        f"{label}.coordinator_database_id",
        1,
    )
    if (
        expect_sha(
            observation["authority_object_id"],
            f"{label}.authority_object_id",
        )
        != authority_object_id
        or expect_sha(
            observation["anchor_object_id"],
            f"{label}.anchor_object_id",
        )
        != anchor_object_id
    ):
        raise HandoffDataError(
            "GitHub PR observation is stale for authority state"
        )
    verify_external_signature(
        signer,
        signed_record_payload(PR_OBSERVATION_DOMAIN, observation),
        observation["signature"],
        f"{label}.signature",
    )
    return observation
def parse_publication_attestation(
    raw: Any,
    *,
    signer: dict[str, Any],
    repository: str,
    repository_database_id: int | None,
    issue: int,
    authority_ref: str,
    anchor_ref: str,
    authority_object_id: str | None,
    anchor_object_id: str | None,
    ruleset_id: int,
    authorized_bypass_actors: list[dict[str, Any]],
    live: bool = False,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    label = "authority publication attestation"
    attestation = copy.deepcopy(expect_object(raw, label))
    expect_keys(
        attestation,
        label,
        (
            "source",
            "repository",
            "repository_id",
            "ruleset_source",
            "issue",
            "authority_object_id",
            "anchor_object_id",
            "operation_nonce",
            "operation",
            "new_head_seal",
            "history_carrier_digest",
            "history_receipt_digest",
            "pull_request_observation_digest",
            "binding_expectation",
            "observed_at",
            "coordinator_database_id",
            "ruleset_response",
            "signature",
        ),
    )
    if (
        attestation["source"] != "external-coordinator-service"
        or expect_string(attestation["repository"], f"{label}.repository")
        != repository
        or (
            repository_database_id is not None
            and expect_int(
                attestation["repository_id"],
                f"{label}.repository_id",
                1,
            )
            != repository_database_id
        )
        or attestation["ruleset_source"] != "github-rulesets-api"
        or expect_int(attestation["issue"], f"{label}.issue", 1) != issue
    ):
        raise HandoffDataError(
            "authority publication attestation identity mismatch"
        )
    if expect_sha(
        attestation["authority_object_id"],
        f"{label}.authority_object_id",
        nullable=True,
    ) != authority_object_id or expect_sha(
        attestation["anchor_object_id"],
        f"{label}.anchor_object_id",
        nullable=True,
    ) != anchor_object_id:
        raise HandoffDataError(
            "authority publication attestation is stale"
        )
    nonce = attestation["operation_nonce"]
    if (
        not isinstance(nonce, str)
        or reporter.SHA256_RE.fullmatch(nonce) is None
    ):
        raise HandoffDataError(f"{label}.operation_nonce must be a SHA-256")
    expect_enum(
        attestation["operation"],
        {"bootstrap", "advance", "bind"},
        f"{label}.operation",
    )
    new_head_seal = attestation["new_head_seal"]
    if new_head_seal is not None and (
        not isinstance(new_head_seal, str)
        or reporter.SHA256_RE.fullmatch(new_head_seal) is None
    ):
        raise HandoffDataError(
            f"{label}.new_head_seal must be a SHA-256"
        )
    for field in (
        "history_carrier_digest",
        "history_receipt_digest",
        "pull_request_observation_digest",
    ):
        value = attestation[field]
        if value is not None and (
            not isinstance(value, str)
            or reporter.SHA256_RE.fullmatch(value) is None
        ):
            raise HandoffDataError(f"{label}.{field} must be a SHA-256")
    observed_at = parse_time(
        attestation["observed_at"],
        f"{label}.observed_at",
    )
    if live:
        require_fresh_live_timestamp(
            observed_at,
            label="authority publication attestation",
            current_time=authoritative_current_time(
                current_time,
                label="authority publication attestation current_time",
            ),
        )
    coordinator_id = expect_int(
        attestation["coordinator_database_id"],
        f"{label}.coordinator_database_id",
        1,
    )
    authorized_user_ids = {
        item["database_id"]
        for item in authorized_bypass_actors
        if item["actor_type"] == "User"
    }
    if coordinator_id not in authorized_user_ids:
        raise HandoffDataError(
            "authority publication actor is not authorized"
        )
    ruleset = expect_object(
        attestation["ruleset_response"],
        f"{label}.ruleset_response",
    )
    expect_keys(
        ruleset,
        f"{label}.ruleset_response",
        (
            "id",
            "enforcement",
            "target",
            "include_refs",
            "exclude_refs",
            "update_restricted",
            "non_fast_forward_restricted",
            "deletion_restricted",
            "bypass_actors",
        ),
    )
    bypass_actors = _parse_ruleset_bypass_actors(
        ruleset["bypass_actors"],
        f"{label}.ruleset_response.bypass_actors",
    )
    for bypass in bypass_actors:
        if (
            bypass["actor_type"] != "User"
            and bypass not in authorized_bypass_actors
        ):
            raise HandoffDataError(
                "authority publication has an unauthorized typed bypass"
            )
    include_refs = expect_list(
        ruleset["include_refs"],
        f"{label}.ruleset_response.include_refs",
    )
    for index, value in enumerate(include_refs):
        expect_string(
            value,
            f"{label}.ruleset_response.include_refs[{index}]",
        )
    exclude_refs = expect_list(
        ruleset["exclude_refs"],
        f"{label}.ruleset_response.exclude_refs",
    )
    if (
        expect_int(ruleset["id"], f"{label}.ruleset_response.id", 1)
        != ruleset_id
        or ruleset["enforcement"] != "active"
        or ruleset["target"] != "branch"
        or sorted(include_refs)
        != sorted([authority_ref, anchor_ref])
        or exclude_refs != []
        or not expect_bool(
            ruleset["update_restricted"],
            f"{label}.ruleset_response.update_restricted",
        )
        or not expect_bool(
            ruleset["non_fast_forward_restricted"],
            f"{label}.ruleset_response.non_fast_forward_restricted",
        )
        or not expect_bool(
            ruleset["deletion_restricted"],
            f"{label}.ruleset_response.deletion_restricted",
        )
        or sorted(
            normalized_json(item) for item in bypass_actors
        )
        != sorted(
            normalized_json(item) for item in authorized_bypass_actors
        )
    ):
        raise HandoffDataError(
            "authority publication ruleset is unrelated or incomplete"
        )
    verify_external_signature(
        signer,
        signed_record_payload(PUBLICATION_ATTESTATION_DOMAIN, attestation),
        attestation["signature"],
        f"{label}.signature",
    )
    return attestation
def _parse_coordinator_receipt(
    raw_receipt: Any,
    *,
    document: dict[str, Any],
    canonical_authority: dict[str, Any],
    expected_repository_database_id: int,
    current_time: datetime,
) -> dict[str, Any]:
    receipt = copy.deepcopy(
        expect_object(raw_receipt, "coordinator_receipt")
    )
    expect_keys(
        receipt,
        "coordinator_receipt",
        (
            "schema_version",
            "repository",
            "repository_database_id",
            "collector_login",
            "collector_database_id",
            "issued_at",
            "operation",
            "authority_protection",
            "pull_request_observation",
            "availability",
            "remote_coverage",
            "runtime_telemetry",
            "resource_receipts",
            "signature",
        ),
    )
    if expect_int(
        receipt["schema_version"],
        "coordinator_receipt.schema_version",
        1,
    ) != 2:
        raise HandoffDataError(
            "coordinator_receipt.schema_version must be 2"
        )
    receipt_repository = expect_string(
        receipt["repository"],
        "coordinator_receipt.repository",
    )
    receipt_repository_id = expect_int(
        receipt["repository_database_id"],
        "coordinator_receipt.repository_database_id",
        1,
    )
    repository_identity_valid = (
        receipt_repository == canonical_authority["repository"]
        and receipt_repository_id == expected_repository_database_id
        and receipt_repository_id
        == canonical_authority["delivery_expectation"]["repository_id"]
    )
    collector = _parse_actor(
        receipt["collector_login"],
        receipt["collector_database_id"],
        "coordinator_receipt.collector",
    )
    attestation_valid = True
    try:
        verify_external_signature(
            canonical_authority["signer"],
            coordinator_attestation_payload(document),
            receipt["signature"],
            "coordinator receipt signature",
        )
    except HandoffDataError:
        attestation_valid = False
    issued_at = parse_time(
        receipt["issued_at"],
        "coordinator_receipt.issued_at",
    )
    operation = expect_object(
        receipt["operation"],
        "coordinator_receipt.operation",
    )
    expect_keys(
        operation,
        "coordinator_receipt.operation",
        (
            "nonce",
            "started_at",
            "implementation_terminated_at",
            "collected_through",
            "eligibility_instant",
            "implementation_terminated",
            "single_use",
            "consume_store_id",
            "consume_sequence",
            "consume_previous_anchor",
            "consume_anchor",
        ),
    )
    nonce = operation["nonce"]
    if (
        not isinstance(nonce, str)
        or reporter.SHA256_RE.fullmatch(nonce) is None
    ):
        raise HandoffDataError(
            "coordinator_receipt.operation.nonce must be a SHA-256"
        )
    consume_store_id = expect_string(
        operation["consume_store_id"],
        "coordinator_receipt.operation.consume_store_id",
    )
    consume_sequence = expect_int(
        operation["consume_sequence"],
        "coordinator_receipt.operation.consume_sequence",
        1,
    )
    consume_previous_anchor = operation["consume_previous_anchor"]
    consume_anchor = operation["consume_anchor"]
    for field, value in (
        ("consume_previous_anchor", consume_previous_anchor),
        ("consume_anchor", consume_anchor),
    ):
        if (
            not isinstance(value, str)
            or reporter.SHA256_RE.fullmatch(value) is None
        ):
            raise HandoffDataError(
                f"coordinator_receipt.operation.{field} must be a SHA-256"
            )
    expected_consume_anchor = hashlib.sha256(
        (
            consume_previous_anchor
            + ":"
            + str(consume_sequence)
            + ":"
            + nonce
        ).encode()
    ).hexdigest()
    if consume_anchor != expected_consume_anchor:
        raise HandoffDataError(
            "coordinator consume-store anchor does not verify"
        )
    operation_started = parse_time(
        operation["started_at"],
        "coordinator_receipt.operation.started_at",
    )
    implementation_terminated_at = parse_time(
        operation["implementation_terminated_at"],
        "coordinator_receipt.operation.implementation_terminated_at",
    )
    collected_through = parse_time(
        operation["collected_through"],
        "coordinator_receipt.operation.collected_through",
    )
    eligibility_instant = parse_time(
        operation["eligibility_instant"],
        "coordinator_receipt.operation.eligibility_instant",
    )
    if (
        not expect_bool(
            operation["implementation_terminated"],
            "coordinator_receipt.operation.implementation_terminated",
        )
        or not expect_bool(
            operation["single_use"],
            "coordinator_receipt.operation.single_use",
        )
        or operation_started > implementation_terminated_at
        or implementation_terminated_at > collected_through
        or collected_through != eligibility_instant
        or issued_at != collected_through
    ):
        raise HandoffDataError(
            "coordinator operation is not terminal, single-use, and atomic"
        )
    freshness_valid = True
    for field, timestamp in (
        ("coordinator receipt issued_at", issued_at),
        ("coordinator receipt eligibility instant", eligibility_instant),
    ):
        try:
            require_fresh_live_timestamp(
                timestamp,
                label=field,
                current_time=current_time,
            )
        except HandoffDataError:
            freshness_valid = False
    protection = expect_object(
        receipt["authority_protection"],
        "coordinator_receipt.authority_protection",
    )
    expect_keys(
        protection,
        "coordinator_receipt.authority_protection",
        (
            "source",
            "repository_id",
            "repository_full_name",
            "authority_ref",
            "anchor_ref",
            "authority_object_id",
            "anchor_object_id",
            "observed_at",
            "response",
        ),
    )
    if protection["source"] != "github-rulesets-api":
        raise HandoffDataError(
            "authority protection must be a live GitHub ruleset response"
        )
    repository_id = expect_int(
        protection["repository_id"],
        "coordinator_receipt.authority_protection.repository_id",
        1,
    )
    repository_full_name = expect_string(
        protection["repository_full_name"],
        "coordinator_receipt.authority_protection.repository_full_name",
    )
    if (
        repository_id != receipt_repository_id
        or repository_id != expected_repository_database_id
        or repository_full_name != receipt_repository
        or repository_full_name != canonical_authority["repository"]
    ):
        repository_identity_valid = False
    expect_string(
        protection["authority_ref"],
        "coordinator_receipt.authority_protection.authority_ref",
    )
    expect_string(
        protection["anchor_ref"],
        "coordinator_receipt.authority_protection.anchor_ref",
    )
    expect_sha(
        protection["authority_object_id"],
        "coordinator_receipt.authority_protection.authority_object_id",
    )
    expect_sha(
        protection["anchor_object_id"],
        "coordinator_receipt.authority_protection.anchor_object_id",
    )
    protection_observed_at = parse_time(
        protection["observed_at"],
        "coordinator_receipt.authority_protection.observed_at",
    )
    if (
        protection_observed_at != collected_through
    ):
        raise HandoffDataError(
            "authority protection was not observed at the eligibility instant"
        )
    ruleset = expect_object(
        protection["response"],
        "coordinator_receipt.authority_protection.response",
    )
    expect_keys(
        ruleset,
        "coordinator_receipt.authority_protection.response",
        (
            "id",
            "enforcement",
            "target",
            "include_refs",
            "exclude_refs",
            "update_restricted",
            "non_fast_forward_restricted",
            "deletion_restricted",
            "bypass_actors",
        ),
    )
    if (
        expect_int(
            ruleset["id"],
            "coordinator_receipt.authority_protection.response.id",
            1,
        )
        != canonical_authority["ruleset_id"]
        or ruleset["enforcement"] != "active"
        or ruleset["target"] != "branch"
        or sorted(
            expect_list(
                ruleset["include_refs"],
                "coordinator_receipt.authority_protection."
                "response.include_refs",
            )
        )
        != sorted(
            [canonical_authority["ref"], canonical_authority["anchor_ref"]]
        )
        or expect_list(
            ruleset["exclude_refs"],
            "coordinator_receipt.authority_protection.response.exclude_refs",
        )
        != []
        or not expect_bool(
            ruleset["update_restricted"],
            "coordinator_receipt.authority_protection."
            "response.update_restricted",
        )
        or not expect_bool(
            ruleset["non_fast_forward_restricted"],
            "coordinator_receipt.authority_protection."
            "response.non_fast_forward_restricted",
        )
        or not expect_bool(
            ruleset["deletion_restricted"],
            "coordinator_receipt.authority_protection."
            "response.deletion_restricted",
        )
    ):
        raise HandoffDataError(
            "unrelated or ineffective authority ruleset response"
        )
    bypass_actors = _parse_ruleset_bypass_actors(
        ruleset["bypass_actors"],
        "coordinator_receipt.authority_protection.response.bypass_actors",
    )
    for bypass in bypass_actors:
        if (
            bypass["actor_type"] != "User"
            and bypass not in canonical_authority["authorized_bypass_actors"]
        ):
            raise HandoffDataError(
                "authority ruleset has an unauthorized typed bypass"
            )
    if sorted(
        normalized_json(item) for item in bypass_actors
    ) != sorted(
        normalized_json(item)
        for item in canonical_authority["authorized_bypass_actors"]
    ):
        raise HandoffDataError(
            "authority ruleset bypass actors do not match frozen authority"
        )
    pull_request_observation = receipt["pull_request_observation"]
    if canonical_authority["pr_binding"] is None:
        if pull_request_observation is not None:
            raise HandoffDataError(
                "unbound authority cannot carry a PR observation"
            )
        parsed_pr_observation = None
    else:
        parsed_pr_observation = parse_pull_request_observation(
            pull_request_observation,
            signer=canonical_authority["signer"],
            repository=receipt["repository"],
            repository_database_id=receipt["repository_database_id"],
            authority_object_id=canonical_authority["pr_binding"][
                "authority_object_id"
            ],
            anchor_object_id=canonical_authority["pr_binding"][
                "anchor_object_id"
            ],
        )
        binding = canonical_authority["pr_binding"]
        if parsed_pr_observation != binding:
            raise HandoffDataError(
                "GitHub PR observation does not match authority binding"
            )
    availability = expect_object(
        receipt["availability"],
        "coordinator_receipt.availability",
    )
    expect_keys(
        availability,
        "coordinator_receipt.availability",
        (
            "mode",
            "observed_at",
            "valid_until",
            "unattended_from",
            "unattended_until",
            "autostop_enabled",
            "stop_on_disconnect",
            "enforcement_source",
        ),
    )
    expect_enum(
        availability["mode"],
        {"always_on", "local"},
        "coordinator_receipt.availability.mode",
    )
    for field in (
        "observed_at",
        "valid_until",
        "unattended_from",
        "unattended_until",
    ):
        parse_time(
            availability[field],
            f"coordinator_receipt.availability.{field}",
        )
    for field in ("autostop_enabled", "stop_on_disconnect"):
        expect_bool(
            availability[field],
            f"coordinator_receipt.availability.{field}",
        )
    if availability["enforcement_source"] != "coordinator-launcher":
        raise HandoffDataError(
            "coordinator availability must come from coordinator-launcher"
        )
    coverage = expect_object(
        receipt["remote_coverage"],
        "coordinator_receipt.remote_coverage",
    )
    expect_keys(
        coverage,
        "coordinator_receipt.remote_coverage",
        (
            "interval_start",
            "interval_end",
            "actors",
            "sources",
            "observed_actions",
            "implementation_processes",
        ),
    )
    coverage_start = parse_time(
        coverage["interval_start"],
        "coordinator_receipt.remote_coverage.interval_start",
    )
    coverage_end = parse_time(
        coverage["interval_end"],
        "coordinator_receipt.remote_coverage.interval_end",
    )
    if coverage_end < coverage_start or coverage_end != issued_at:
        raise HandoffDataError(
            "remote coverage interval is incomplete or not current"
        )
    actors = []
    actor_logins: dict[str, int] = {}
    actor_ids_by_id: dict[int, str] = {}
    for index, raw_actor in enumerate(
        expect_list(
            coverage["actors"],
            "coordinator_receipt.remote_coverage.actors",
        )
    ):
        label = f"coordinator_receipt.remote_coverage.actors[{index}]"
        actor_record = expect_object(raw_actor, label)
        expect_keys(
            actor_record,
            label,
            ("login", "database_id", "resolved_at", "source"),
        )
        actor = _parse_actor(
            actor_record["login"],
            actor_record["database_id"],
            label,
        )
        resolved_at = parse_time(
            actor_record["resolved_at"],
            f"{label}.resolved_at",
        )
        if resolved_at > issued_at:
            raise HandoffDataError(
                f"{label}.resolved_at follows receipt issuance"
            )
        if actor_record["source"] != "github-actor-api":
            raise HandoffDataError(
                f"{label}.source must be github-actor-api"
            )
        prior_id = actor_logins.setdefault(
            actor["login"],
            actor["database_id"],
        )
        prior_login = actor_ids_by_id.setdefault(
            actor["database_id"],
            actor["login"],
        )
        if (
            prior_id != actor["database_id"]
            or prior_login != actor["login"]
        ):
            raise HandoffDataError(
                "remote coverage mixes actor logins and numeric IDs"
            )
        actors.append(actor)
    if len(actors) != len(actor_logins):
        raise HandoffDataError(
            "remote coverage repeats actor resolution records"
        )
    if not any(
        _actors_match(collector, actor)
        and collector["login"] == actor["login"]
        for actor in actors
    ):
        raise HandoffDataError(
            "coordinator collector lacks authoritative actor resolution"
        )
    source_names = []
    source_actions = []
    incomplete_sources = []
    for index, raw_source in enumerate(
        expect_list(
            coverage["sources"],
            "coordinator_receipt.remote_coverage.sources",
        )
    ):
        label = f"coordinator_receipt.remote_coverage.sources[{index}]"
        source = expect_object(raw_source, label)
        expect_keys(
            source,
            label,
            (
                "name",
                "available",
                "complete",
                "total_count",
                "observed_at",
                "events",
            ),
        )
        name = expect_enum(
            source["name"],
            set(REMOTE_COVERAGE_SOURCES),
            f"{label}.name",
        )
        source_names.append(name)
        available = expect_bool(source["available"], f"{label}.available")
        complete = expect_bool(source["complete"], f"{label}.complete")
        total_count = expect_int(
            source["total_count"],
            f"{label}.total_count",
            0,
        )
        if parse_time(
            source["observed_at"],
            f"{label}.observed_at",
        ) != collected_through:
            raise HandoffDataError(
                f"{label} was not collected through eligibility"
            )
        events = expect_list(source["events"], f"{label}.events")
        if total_count != len(events) or (not available and events):
            raise HandoffDataError(
                f"{label} count/availability does not cover its events"
            )
        if not available or not complete:
            incomplete_sources.append(name)
        for event_index, raw_event in enumerate(events):
            action = _parse_remote_action(
                raw_event,
                f"{label}.events[{event_index}]",
            )
            if action["source"] != name:
                raise HandoffDataError(
                    f"{label} event source does not match its collector"
                )
            source_actions.append(action)
    if sorted(source_names) != sorted(REMOTE_COVERAGE_SOURCES):
        raise HandoffDataError(
            "remote coverage must include every required event source once"
        )
    expect_unique(source_names, "remote coverage source names")
    expect_unique(
        (action["id"] for action in source_actions),
        "remote coverage event IDs",
    )
    observed_actions = [
        _parse_remote_action(
            raw_action,
            f"coordinator_receipt.remote_coverage.observed_actions[{index}]",
        )
        for index, raw_action in enumerate(
            expect_list(
                coverage["observed_actions"],
                "coordinator_receipt.remote_coverage.observed_actions",
            )
        )
    ]
    if sorted(
        normalized_json(_public_action(action)) for action in observed_actions
    ) != sorted(
        normalized_json(_public_action(action)) for action in source_actions
    ):
        raise HandoffDataError(
            "remote coverage observed_actions omits or invents a source event"
        )
    processes = []
    for index, raw_process in enumerate(
        expect_list(
            coverage["implementation_processes"],
            "coordinator_receipt.remote_coverage.implementation_processes",
        )
    ):
        label = (
            "coordinator_receipt.remote_coverage."
            f"implementation_processes[{index}]"
        )
        process = expect_object(raw_process, label)
        expect_keys(
            process,
            label,
            (
                "handoff_id",
                "started_at",
                "ended_at",
                "credentials_available",
                "network_mode",
                "source",
            ),
        )
        expect_string(process["handoff_id"], f"{label}.handoff_id")
        started_at = parse_time(process["started_at"], f"{label}.started_at")
        ended_at = parse_time(process["ended_at"], f"{label}.ended_at")
        if ended_at < started_at:
            raise HandoffDataError(f"{label}.ended_at precedes started_at")
        expect_bool(
            process["credentials_available"],
            f"{label}.credentials_available",
        )
        expect_enum(
            process["network_mode"],
            {"allowed", "denied"},
            f"{label}.network_mode",
        )
        if process["source"] != "coordinator-launcher":
            raise HandoffDataError(
                f"{label}.source must be coordinator-launcher"
            )
        processes.append(process)
    expect_unique(
        (process["handoff_id"] for process in processes),
        "implementation process handoff IDs",
    )
    if not processes or max(
        parse_time(
            process["ended_at"],
            f"process {process['handoff_id']}.ended_at",
        )
        for process in processes
    ) != implementation_terminated_at:
        raise HandoffDataError(
            "implementation termination is not bound to process telemetry"
        )
    telemetry = {}
    for index, raw_metric in enumerate(
        expect_list(
            receipt["runtime_telemetry"],
            "coordinator_receipt.runtime_telemetry",
        )
    ):
        label = f"coordinator_receipt.runtime_telemetry[{index}]"
        metric = expect_object(raw_metric, label)
        expect_keys(
            metric,
            label,
            (
                "handoff_id",
                "owner_database_id",
                "started_at",
                "ended_at",
                "peak_rss_bytes",
                "coordination_turns",
                "recovery_minutes",
                "interruption_snapshot",
                "source",
            ),
        )
        handoff_id = expect_string(metric["handoff_id"], f"{label}.handoff_id")
        if handoff_id in telemetry:
            raise HandoffDataError(
                f"runtime telemetry repeats handoff {handoff_id!r}"
            )
        expect_int(
            metric["owner_database_id"],
            f"{label}.owner_database_id",
            1,
        )
        started_at = parse_time(metric["started_at"], f"{label}.started_at")
        ended_at = parse_time(metric["ended_at"], f"{label}.ended_at")
        if ended_at < started_at:
            raise HandoffDataError(f"{label}.ended_at precedes started_at")
        for field in (
            "peak_rss_bytes",
            "coordination_turns",
            "recovery_minutes",
        ):
            expect_int(metric[field], f"{label}.{field}", 0)
        snapshot = metric["interruption_snapshot"]
        if snapshot is not None:
            snapshot = expect_object(snapshot, f"{label}.interruption_snapshot")
            expect_keys(
                snapshot,
                f"{label}.interruption_snapshot",
                ("status_sha256", "dirty_paths", "preserved_paths", "files"),
            )
            if (
                not isinstance(snapshot["status_sha256"], str)
                or reporter.SHA256_RE.fullmatch(snapshot["status_sha256"]) is None
            ):
                raise HandoffDataError(
                    f"{label}.interruption_snapshot.status_sha256 is invalid"
                )
            for field in ("dirty_paths", "preserved_paths"):
                paths = expect_list(
                    snapshot[field],
                    f"{label}.interruption_snapshot.{field}",
                )
                for path_index, path in enumerate(paths):
                    _validate_repository_path(
                        path,
                        f"{label}.interruption_snapshot.{field}[{path_index}]",
                        prefix=False,
                    )
                expect_unique(
                    paths,
                    f"{label}.interruption_snapshot.{field}",
                )
            file_paths = []
            for file_index, raw_file in enumerate(
                expect_list(
                    snapshot["files"],
                    f"{label}.interruption_snapshot.files",
                )
            ):
                file_label = (
                    f"{label}.interruption_snapshot.files[{file_index}]"
                )
                file_record = expect_object(raw_file, file_label)
                expect_keys(
                    file_record,
                    file_label,
                    ("path", "mode", "sha256", "content_base64"),
                )
                file_paths.append(
                    _validate_repository_path(
                        file_record["path"],
                        f"{file_label}.path",
                        prefix=False,
                    )
                )
                expect_int(file_record["mode"], f"{file_label}.mode", 0)
                content = _decode_canonical_base64(
                    file_record["content_base64"],
                    f"{file_label}.content_base64",
                    allow_empty=True,
                )
                if file_record["sha256"] != hashlib.sha256(content).hexdigest():
                    raise HandoffDataError(
                        f"{file_label}.sha256 does not match content"
                    )
            if sorted(file_paths) != sorted(snapshot["preserved_paths"]):
                raise HandoffDataError(
                    f"{label}.interruption_snapshot files do not cover "
                    "preserved paths"
                )
        if metric["source"] != "coordinator-runtime":
            raise HandoffDataError(
                f"{label}.source must be coordinator-runtime"
            )
        telemetry[handoff_id] = metric
    resources = {}
    for index, raw_resource in enumerate(
        expect_list(
            receipt["resource_receipts"],
            "coordinator_receipt.resource_receipts",
        )
    ):
        label = f"coordinator_receipt.resource_receipts[{index}]"
        resource = expect_object(raw_resource, label)
        expect_keys(
            resource,
            label,
            (
                "handoff_id",
                "parent_sha",
                "candidate_sha",
                "closed",
                "sources",
                "dependency_authority",
                "dependency_inputs",
                "rom_bytes",
                "ram_bytes",
            ),
        )
        handoff_id = expect_string(
            resource["handoff_id"],
            f"{label}.handoff_id",
        )
        if handoff_id in resources:
            raise HandoffDataError(
                f"resource receipts repeat handoff {handoff_id!r}"
            )
        expect_sha(resource["parent_sha"], f"{label}.parent_sha")
        expect_sha(resource["candidate_sha"], f"{label}.candidate_sha")
        expect_bool(resource["closed"], f"{label}.closed")
        sources = expect_list(resource["sources"], f"{label}.sources")
        if sorted(sources) != ["build", "map", "resource"]:
            raise HandoffDataError(
                f"{label}.sources must close build/map/resource"
            )
        if resource["dependency_authority"] != (
            "parsed-build-dependency-closure"
        ):
            raise HandoffDataError(
                f"{label}.dependency_authority is not trusted"
            )
        dependency_inputs = expect_list(
            resource["dependency_inputs"],
            f"{label}.dependency_inputs",
        )
        for path_index, path in enumerate(dependency_inputs):
            _validate_repository_path(
                path,
                f"{label}.dependency_inputs[{path_index}]",
                prefix=False,
            )
        if dependency_inputs != sorted(set(dependency_inputs)):
            raise HandoffDataError(
                f"{label}.dependency_inputs must be unique and sorted"
            )
        for field in ("rom_bytes", "ram_bytes"):
            expect_int(resource[field], f"{label}.{field}", 0)
        resources[handoff_id] = resource
    return {
        "receipt": receipt,
        "attestation_valid": attestation_valid,
        "operation": operation,
        "operation_nonce": nonce,
        "consume_store_id": consume_store_id,
        "consume_sequence": consume_sequence,
        "consume_previous_anchor": consume_previous_anchor,
        "consume_anchor": consume_anchor,
        "implementation_terminated_at": implementation_terminated_at,
        "eligibility_instant": eligibility_instant,
        "issued_at": issued_at,
        "freshness_valid": freshness_valid,
        "repository_identity_valid": repository_identity_valid,
        "protection": protection,
        "ruleset": ruleset,
        "bypass_actors": bypass_actors,
        "pull_request_observation": parsed_pr_observation,
        "availability": availability,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "actors": actors,
        "actions": source_actions,
        "incomplete_sources": incomplete_sources,
        "processes": processes,
        "telemetry": telemetry,
        "resources": resources,
    }
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
def _json_blob_at(
    repository_root: Path,
    commit_sha: str,
    path: str,
) -> dict[str, Any] | None:
    blob = _git_blob(repository_root, commit_sha, path)
    if blob is None:
        return None
    return _parse_authority_json(
        blob[1],
        f"{path} at {commit_sha}",
    )
def _derive_protocol_changes(
    repository_root: Path,
    parent_sha: str,
    candidate_sha: str,
    changed_paths: list[str],
) -> int:
    if HANDOFF_SCHEMA_REPOSITORY_PATH not in changed_paths:
        return 0
    parent = _json_blob_at(
        repository_root,
        parent_sha,
        HANDOFF_SCHEMA_REPOSITORY_PATH,
    )
    candidate = _json_blob_at(
        repository_root,
        candidate_sha,
        HANDOFF_SCHEMA_REPOSITORY_PATH,
    )
    if parent is None or candidate is None:
        raise HandoffDataError(
            "protocol schema changes must preserve a parseable parent and "
            "candidate schema"
        )
    parent_version = expect_int(
        parent.get("protocol_version"),
        "parent handoff schema.protocol_version",
        1,
    )
    candidate_version = expect_int(
        candidate.get("protocol_version"),
        "candidate handoff schema.protocol_version",
        1,
    )
    if candidate_version <= parent_version:
        raise HandoffDataError(
            "changed handoff schema must monotonically advance protocol_version"
        )
    return candidate_version - parent_version
def _resource_surfaces_changed(changed_paths: list[str]) -> bool:
    return any(
        not any(path.startswith(prefix) for prefix in PROVEN_HOST_ONLY_PREFIXES)
        for path in changed_paths
    )
def _recovery_resolution_valid(
    repository_root: Path,
    handoff: dict[str, Any],
    snapshot: dict[str, Any],
) -> bool:
    resolutions = {
        item["path"]: item for item in handoff["_recovery_resolution"]
    }
    files = {item["path"]: item for item in snapshot["files"]}
    if set(resolutions) != set(files):
        return False
    candidate_sha = (
        handoff["result"]["sha"] if handoff["result"] is not None else None
    )
    if candidate_sha is None:
        return False
    for path, original in files.items():
        resolution = resolutions[path]
        if resolution["original_sha256"] != original["sha256"]:
            return False
        if resolution["disposition"] == "resolved":
            continue
        result_path = resolution["result_path"]
        raw_tree = (
            run_git(
                repository_root,
                "ls-tree",
                candidate_sha,
                "--",
                result_path,
            )
            .decode("utf-8")
            .rstrip("\n")
            .split(maxsplit=3)
        )
        if (
            len(raw_tree) != 4
            or int(raw_tree[0], 8) != original["mode"]
            or raw_tree[1] != "blob"
            or raw_tree[2] != resolution["result_blob_oid"]
            or hashlib.sha256(
                run_git(
                    repository_root,
                    "cat-file",
                    "blob",
                    raw_tree[2],
                )
            ).hexdigest()
            != original["sha256"]
        ):
            return False
    return True
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
        "lifetime_seconds": 0,
        "peak_rss_bytes": 0,
        "coordination_turns": 0,
        "recovery_minutes": 0,
        "budget_usage": {
            "rom_bytes": 0,
            "ram_bytes": 0,
            "protocol_changes": 0,
        },
        "interruption_snapshot": None,
        "rejection_codes": [],
    }
def validate_document(
    raw: Any,
    repository_root: Path,
    *,
    authority_hook=None,
    coordinator_installation: Path | None = None,
    current_time: datetime | None = None,
) -> dict[str, Any]:
    document = copy.deepcopy(expect_object(raw, "handoff document"))
    expect_keys(
        document,
        "handoff document",
        (
            "schema_version",
            "repository",
            "prior_handoffs",
            "history_authority",
            "delivery_graph",
            "coordinators",
            "handoffs",
            "workflow_runs",
            "watchers",
            "coordinator_receipt",
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
    live_current_time = authoritative_current_time(
        current_time,
        label="handoff validation current_time",
    )
    installation = load_coordinator_installation(
        repository_root,
        coordinator_installation,
    )
    if repository != _repository_from_origin(repository_root, coordinator_installation):
        raise HandoffDataError(
            "handoff document.repository does not match the worktree origin"
        )
    if repository != installation["repository"]:
        raise HandoffDataError(
            "handoff document.repository does not match coordinator "
            "installation"
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
    delivery_graph = evaluate_delivery_graph(document["delivery_graph"])
    prior_handoffs = validate_prior_handoffs(document["prior_handoffs"])
    lifecycles = {
        (handoff["issue"], handoff["pull_request"]) for handoff in handoffs
    }
    if len(lifecycles) != 1:
        raise HandoffDataError(
            "one handoff document must cover exactly one issue/PR lifecycle"
        )
    issue, pull_request = next(iter(lifecycles))
    supplied_authority = expect_object(
        document["history_authority"],
        "handoff document.history_authority",
    )
    expect_keys(
        supplied_authority,
        "handoff document.history_authority",
        (
            "ref",
            "object_id",
            "anchor_ref",
            "anchor_object_id",
            "history_events",
            "schema_version",
            "repository",
            "issue",
            "sequence",
            "handoff_sequence",
            "head_seal",
            "pr_binding",
            "signer",
            "ruleset_id",
            "authorized_bypass_actors",
            "delivery_expectation",
            "publication_attestation",
            "event",
            "previous_object_id",
            "observation",
        ),
    )
    canonical_authority = read_history_authority(
        repository_root,
        repository,
        issue,
        pull_request,
        observation_hook=authority_hook,
        coordinator_installation=coordinator_installation,
    )
    coordinator_receipt = _parse_coordinator_receipt(
        document["coordinator_receipt"],
        document=document,
        canonical_authority=canonical_authority,
        expected_repository_database_id=installation[
            "repository_database_id"
        ],
        current_time=live_current_time,
    )
    if supplied_authority != canonical_authority:
        raise HandoffDataError(
            "handoff history authority does not match the canonical Git ref"
        )
    if any(prior["issue"] != issue for prior in prior_handoffs):
        raise HandoffDataError(
            "prior handoff history crosses issue authorities"
        )
    binding = canonical_authority["pr_binding"]
    if pull_request is None:
        if binding is not None:
            raise HandoffDataError(
                "bound issue authority cannot return to the no-PR namespace"
            )
    elif binding is None or binding["pull_request"] != pull_request:
        raise HandoffDataError(
            "handoff pull request does not match immutable authority binding"
        )
    if any(
        prior["pull_request"] not in {None, pull_request}
        for prior in prior_handoffs
    ):
        raise HandoffDataError(
            "prior handoff history has an unrelated pull request binding"
        )
    if canonical_authority["sequence"] == 0:
        if prior_handoffs or canonical_authority["head_seal"] is not None:
            raise HandoffDataError(
                "history authority genesis contradicts prior handoffs"
            )
    elif (
        len(prior_handoffs) != canonical_authority["handoff_sequence"]
        or (
            canonical_authority["handoff_sequence"] > 0
            and not prior_handoffs
        )
        or (
            prior_handoffs
            and prior_handoffs[-1]["seal"]
            != canonical_authority["head_seal"]
        )
        or (
            not prior_handoffs
            and canonical_authority["head_seal"] is not None
        )
    ):
        raise HandoffDataError(
            "prior handoff history is reset, truncated, or not at canonical head"
        )
    if [
        event["history_receipt"]
        for event in canonical_authority["history_events"]
    ] != [
        _public_json(receipt)
        for receipt in prior_handoffs
    ]:
        raise HandoffDataError(
            "protected authority receipts do not match handoff history"
        )
    protection = coordinator_receipt["protection"]
    if (
        protection["authority_ref"] != canonical_authority["ref"]
        or protection["anchor_ref"] != canonical_authority["anchor_ref"]
        or protection["authority_object_id"]
        != canonical_authority["object_id"]
        or protection["anchor_object_id"]
        != canonical_authority["anchor_object_id"]
    ):
        raise HandoffDataError(
            "coordinator receipt cannot verify authority protection and "
            "actor authority"
        )
    global_rejections = set()
    handoff_rejections = {handoff["id"]: set() for handoff in handoffs}
    def reject(code: str, handoff_id: str | None = None) -> None:
        global_rejections.add(code)
        if handoff_id is not None:
            handoff_rejections[handoff_id].add(code)
    for handoff in handoffs:
        if (
            handoff["expected_branch"]
            != canonical_authority["delivery_expectation"][
                "delivery_branch"
            ]
        ):
            reject("unrelated-branch", handoff["id"])
    expected_user_bypass = sorted(
        normalized_json(
            user_bypass_actor(actor["database_id"])
        )
        for actor in installation["_authorized"]
    )
    frozen_user_bypass = sorted(
        normalized_json(item)
        for item in canonical_authority["authorized_bypass_actors"]
        if item["actor_type"] == "User"
    )
    if expected_user_bypass != frozen_user_bypass:
        for handoff in handoffs:
            reject("authority-ruleset-bypass-mismatch", handoff["id"])
    if (
        not coordinator_receipt["attestation_valid"]
        or not coordinator_receipt["freshness_valid"]
        or not coordinator_receipt["repository_identity_valid"]
    ):
        for handoff in handoffs:
            reject("invalid-coordinator-attestation", handoff["id"])
    lifecycle_end = max(
        parse_time(
            handoff["_states"][-1]["at"],
            f"handoff {handoff['id']} terminal state",
        )
        for handoff in handoffs
    )
    if lifecycle_end > coordinator_receipt["implementation_terminated_at"]:
        for handoff in handoffs:
            reject("implementation-not-terminated-before-collection", handoff["id"])
    observed_times = [
        parse_time(run["observed_at"], f"run {run['id']}.observed_at")
        for run in runs.values()
    ]
    observed_times.extend(
        parse_time(
            watcher["ended_at"],
            f"watcher {watcher['id']}.ended_at",
        )
        for watcher in watchers
    )
    observed_times.extend(
        parse_time(
            action["occurred_at"],
            f"remote action {action['id']}.occurred_at",
        )
        for action in coordinator_receipt["actions"]
    )
    if observed_times and max(observed_times) > coordinator_receipt[
        "eligibility_instant"
    ]:
        for handoff in handoffs:
            reject("event-after-terminal-coverage", handoff["id"])
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
        coordinator_actor = coordinators[0]["_actor"]
        if not any(
            _actors_match(coordinator_actor, actor)
            for actor in installation["_authorized"]
        ):
            for handoff in handoffs:
                reject("coordinator-actor-unauthorized", handoff["id"])
        availability = coordinator_receipt["availability"]
        observed_at = parse_time(
            availability["observed_at"],
            "coordinator availability observed_at",
        )
        valid_until = parse_time(
            availability["valid_until"],
            "coordinator availability valid_until",
        )
        unattended_from = parse_time(
            availability["unattended_from"],
            "coordinator availability unattended_from",
        )
        unattended_until = parse_time(
            availability["unattended_until"],
            "coordinator availability unattended_until",
        )
        assignment_start = min(
            parse_time(
                handoff["_states"][0]["at"],
                f"handoff {handoff['id']} assignment_sent",
            )
            for handoff in handoffs
        )
        activity_times = [
            parse_time(
                state["at"],
                f"handoff state {state['state']}",
            )
            for handoff in handoffs
            for state in handoff["_states"]
        ]
        if (
            observed_at > assignment_start
            or observed_at < parse_time(
                coordinator_receipt["operation"]["started_at"],
                "coordinator operation start",
            )
            or unattended_from > assignment_start
            or unattended_until
            < coordinator_receipt["eligibility_instant"]
            or valid_until < unattended_until
            or valid_until < coordinator_receipt["eligibility_instant"]
            or availability["autostop_enabled"]
            or availability["stop_on_disconnect"]
            or max(activity_times)
            > coordinator_receipt["implementation_terminated_at"]
        ):
            for handoff in handoffs:
                reject("coordinator-unavailable", handoff["id"])
        if (
            coordinator_receipt["coverage_start"] > assignment_start
            or coordinator_receipt["coverage_end"]
            != coordinator_receipt["eligibility_instant"]
        ):
            reject("remote-coverage-incomplete")
    duplicate_handoff_ids = set()
    resolved_actors = coordinator_receipt["actors"]
    for handoff in handoffs:
        if not any(
            _actors_match(handoff["_owner"], actor)
            and handoff["_owner"]["login"] == actor["login"]
            for actor in resolved_actors
        ):
            reject("unresolved-actor-id", handoff["id"])
    if len(coordinators) == 1 and not any(
        _actors_match(coordinators[0]["_actor"], actor)
        and coordinators[0]["_actor"]["login"] == actor["login"]
        for actor in resolved_actors
    ):
        for handoff in handoffs:
            reject("unresolved-actor-id", handoff["id"])
    all_lifecycle_records = [*prior_handoffs, *handoffs]
    roots = [
        record
        for record in all_lifecycle_records
        if record["replaces_handoff_id"] is None
    ]
    if len(roots) != 1:
        for handoff in handoffs:
            reject("root-owner-count", handoff["id"])
    lifecycle_by_id = {
        record["handoff_id"] if "handoff_id" in record else record["id"]: record
        for record in all_lifecycle_records
    }
    replacements_by_id: dict[str, list[dict[str, Any]]] = {}
    for record in all_lifecycle_records:
        replaced = record["replaces_handoff_id"]
        if replaced is not None:
            replacements_by_id.setdefault(replaced, []).append(record)
            if replaced not in lifecycle_by_id:
                for handoff in handoffs:
                    reject("orphan-replacement", handoff["id"])
    for replaced, replacements in replacements_by_id.items():
        if len(replacements) > 1:
            for handoff in handoffs:
                reject("replacement-owner-count", handoff["id"])
        parent = lifecycle_by_id.get(replaced)
        if parent is None:
            continue
        parent_state = (
            parent["lifecycle_state"]
            if "lifecycle_state" in parent
            else parent["_state_names"][-1]
        )
        child = replacements[0] if replacements else None
        if child is None:
            continue
        child_kind = child["handoff_kind"]
        expected_parent_state = (
            "interrupted"
            if child_kind == "oom_replacement"
            else "handed_off"
        )
        if child_kind == "root" or parent_state != expected_parent_state:
            for handoff in handoffs:
                reject("invalid-lifecycle-successor", handoff["id"])
            continue
        parent_closed_at = parse_time(
            parent["closed_at"]
            if "closed_at" in parent
            else parent["_states"][-1]["at"],
            f"handoff {replaced}.closed_at",
        )
        child_assigned_at = parse_time(
            child["_states"][0]["at"]
            if "_states" in child
            else child["assigned_at"],
            f"handoff successor of {replaced}.assignment_sent",
        )
        if child_assigned_at <= parent_closed_at:
            for handoff in handoffs:
                reject("overlapping-lifecycle-successor", handoff["id"])
        if child_kind == "review_successor":
            parent_candidate = (
                parent["candidate_sha"]
                if "candidate_sha" in parent
                else (
                    parent["result"]["sha"]
                    if parent["result"] is not None
                    else None
                )
            )
            if (
                parent_candidate is None
                or child["assigned_parent_sha"] != parent_candidate
            ):
                for handoff in handoffs:
                    reject("review-successor-parent-mismatch", handoff["id"])
    for index, handoff in enumerate(handoffs):
        for other in handoffs[index + 1:]:
            if _actors_match(handoff["_owner"], other["_owner"]):
                duplicate_handoff_ids.update((handoff["id"], other["id"]))
    for handoff_id in duplicate_handoff_ids:
        reject("duplicate-owner", handoff_id)
    for handoff in handoffs:
        if any(
            prior["issue"] == handoff["issue"]
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
        ]
        for prior in relevant_history:
            if prior["candidate_sha"] is None:
                continue
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
        telemetry = coordinator_receipt["telemetry"].get(handoff_id)
        if telemetry is None:
            reject("missing-runtime-telemetry", handoff_id)
        else:
            result["peak_rss_bytes"] = telemetry["peak_rss_bytes"]
            result["coordination_turns"] = telemetry["coordination_turns"]
            result["recovery_minutes"] = telemetry["recovery_minutes"]
            result["interruption_snapshot"] = telemetry[
                "interruption_snapshot"
            ]
            telemetry_start = parse_time(
                telemetry["started_at"],
                f"telemetry {handoff_id}.started_at",
            )
            telemetry_end = parse_time(
                telemetry["ended_at"],
                f"telemetry {handoff_id}.ended_at",
            )
            lifetime_seconds = whole_second_duration(
                telemetry_start,
                telemetry_end,
                label=f"telemetry {handoff_id} lifetime",
            )
            if lifetime_seconds is None:
                reject("invalid-runtime-telemetry", handoff_id)
            else:
                result["lifetime_seconds"] = lifetime_seconds
            if (
                telemetry["owner_database_id"]
                != handoff["_owner"]["database_id"]
                or telemetry_start
                != parse_time(
                    handoff["_states"][0]["at"],
                    f"handoff {handoff_id}.assignment_sent",
                )
                or telemetry_end
                != parse_time(
                    handoff["_states"][-1]["at"],
                    f"handoff {handoff_id}.last_state",
                )
            ):
                reject("invalid-runtime-telemetry", handoff_id)
        if coordinator_receipt["incomplete_sources"]:
            process = next(
                (
                    item
                    for item in coordinator_receipt["processes"]
                    if item["handoff_id"] == handoff_id
                ),
                None,
            )
            if (
                process is None
                or process["credentials_available"]
                or process["network_mode"] != "denied"
                or parse_time(
                    process["started_at"],
                    f"process {handoff_id}.started_at",
                )
                > parse_time(
                    handoff["_states"][0]["at"],
                    f"handoff {handoff_id}.assignment_sent",
                )
                or parse_time(
                    process["ended_at"],
                    f"process {handoff_id}.ended_at",
                )
                < parse_time(
                    handoff["_states"][-1]["at"],
                    f"handoff {handoff_id}.last_state",
                )
            ):
                reject("remote-coverage-incomplete")
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
        if result["peak_rss_bytes"] > handoff["max_peak_rss_bytes"]:
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
                    try:
                        protocol_changes = _derive_protocol_changes(
                            repository_root,
                            handoff["assigned_parent_sha"],
                            result_sha,
                            changed_paths,
                        )
                    except HandoffDataError:
                        reject("invalid-protocol-derivation", handoff_id)
                        protocol_changes = 0
                    result["budget_usage"][
                        "protocol_changes"
                    ] = protocol_changes
                    resource = coordinator_receipt["resources"].get(
                        handoff_id
                    )
                    if _resource_surfaces_changed(changed_paths):
                        dependency_inputs = sorted(
                            path
                            for path in changed_paths
                            if not any(
                                path.startswith(prefix)
                                for prefix in PROVEN_HOST_ONLY_PREFIXES
                            )
                        )
                        if (
                            resource is None
                            or not resource["closed"]
                            or resource["parent_sha"]
                            != handoff["assigned_parent_sha"]
                            or resource["candidate_sha"] != result_sha
                            or resource["dependency_inputs"]
                            != dependency_inputs
                        ):
                            reject(
                                "missing-closed-resource-receipt",
                                handoff_id,
                            )
                        else:
                            result["budget_usage"]["rom_bytes"] = resource[
                                "rom_bytes"
                            ]
                            result["budget_usage"]["ram_bytes"] = resource[
                                "ram_bytes"
                            ]
                    elif resource is not None:
                        reject("unexpected-resource-receipt", handoff_id)
            if status:
                reject("dirty-worktree", handoff_id)
            if conflicts:
                reject("conflicting-worktree", handoff_id)
            for field in ("rom_bytes", "ram_bytes", "protocol_changes"):
                if (
                    result["budget_usage"][field]
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
                if receipt["checker_trust"]["mode"] == "external-bootstrap":
                    reject(
                        "checker-bootstrap-not-trusted-push-eligible",
                        handoff_id,
                    )
                for code in _verify_check_receipt(
                    receipt,
                    check=check,
                    repository_root=repository_root,
                    parent_sha=handoff["assigned_parent_sha"],
                    candidate_sha=handoff["result"]["sha"],
                    coordinator_installation=coordinator_installation,
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
            snapshot = result["interruption_snapshot"]
            if (
                snapshot is None
                or sorted(snapshot["preserved_paths"])
                != sorted(interruption["preserved_paths"])
                or any(
                    path not in snapshot["dirty_paths"]
                    for path in interruption["preserved_paths"]
                )
            ):
                reject("oom-worktree-not-preserved", handoff_id)
            if any(
                not _path_is_allowed(path, handoff["allowed_scope"])
                for path in interruption["preserved_paths"]
            ):
                reject("scope-violation", handoff_id)
            if interruption["host_process_actions"]:
                reject("host-process-action-prohibited", handoff_id)
            if result["recovery_minutes"] <= 0:
                reject("invalid-recovery-telemetry", handoff_id)
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
            elif state_names not in IN_PROGRESS_STATE_PREFIXES:
                reject("incomplete-lifecycle", handoff_id)
    replacements_by_parent: dict[str, list[dict[str, Any]]] = {}
    for handoff in handoffs:
        replaced = handoff["replaces_handoff_id"]
        if replaced is not None:
            replacements_by_parent.setdefault(replaced, []).append(handoff)
            if (
                replaced not in handoffs_by_id
                and not any(
                    prior["handoff_id"] == replaced
                    for prior in prior_handoffs
                )
            ):
                reject("orphan-replacement", handoff["id"])
    for handoff in handoffs:
        interruption = handoff["interruption"]
        if interruption is None:
            continue
        replacements = replacements_by_parent.get(handoff["id"], [])
        replacement_id = interruption["replacement_handoff_id"]
        if len(replacements) > 1 or (
            len(replacements) == 1
            and replacements[0]["id"] != replacement_id
        ) or (not replacements and replacement_id is not None):
            reject("replacement-owner-count", handoff["id"])
            continue
        if not replacements:
            continue
        replacement = replacements[0]
        if replacement["handoff_kind"] != "oom_replacement":
            reject("invalid-lifecycle-successor", replacement["id"])
        if (
            replacement["result"] is not None
            and not _recovery_resolution_valid(
                repository_root,
                replacement,
                results[handoff["id"]]["interruption_snapshot"],
            )
        ):
            reject("recovery-content-not-resolved", replacement["id"])
        replacement_sent = parse_time(
            replacement["_states"][0]["at"],
            f"handoff {replacement['id']}.assignment_sent",
        )
        interrupted_at = parse_time(
            interruption["occurred_at"],
            f"handoff {handoff['id']}.interruption.occurred_at",
        )
        if replacement_sent <= interrupted_at:
            reject("replacement-assignment-not-causal", handoff["id"])
            reject("replacement-assignment-not-causal", replacement["id"])
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
    prior_by_id = {
        prior["handoff_id"]: prior for prior in prior_handoffs
    }
    for handoff in handoffs:
        replaced = handoff["replaces_handoff_id"]
        if replaced not in prior_by_id:
            continue
        prior = prior_by_id[replaced]
        assigned_at = parse_time(
            handoff["_states"][0]["at"],
            f"handoff {handoff['id']}.assignment_sent",
        )
        prior_closed = parse_time(
            prior["closed_at"],
            f"prior handoff {replaced}.closed_at",
        )
        expected_state = (
            "interrupted"
            if handoff["handoff_kind"] == "oom_replacement"
            else "handed_off"
        )
        if prior["lifecycle_state"] != expected_state or assigned_at <= prior_closed:
            reject("replacement-assignment-not-causal", handoff["id"])
        if _actors_match(prior["_owner"], handoff["_owner"]):
            reject("replacement-owner-reused", handoff["id"])
        for field in ("issue", "expected_branch", "allowed_worktree"):
            if prior[field] != handoff[field]:
                reject("replacement-context-mismatch", handoff["id"])
        expected_parent = (
            prior["assigned_parent_sha"]
            if handoff["handoff_kind"] == "oom_replacement"
            else prior["candidate_sha"]
        )
        if handoff["assigned_parent_sha"] != expected_parent:
            reject("replacement-context-mismatch", handoff["id"])
        if (
            handoff["handoff_kind"] == "oom_replacement"
            and handoff["result"] is not None
            and not _recovery_resolution_valid(
                repository_root,
                handoff,
                prior["interruption_snapshot"],
            )
        ):
            reject("recovery-content-not-resolved", handoff["id"])
    for action in coordinator_receipt["actions"]:
        handoff = handoffs_by_id.get(action["handoff_id"])
        if handoff is None:
            raise HandoffDataError(
                f"remote action {action['id']!r} references an unknown handoff"
            )
        if not any(
            _actors_match(action["_actor"], actor)
            and action["_actor"]["login"] == actor["login"]
            for actor in coordinator_receipt["actors"]
        ):
            reject("unresolved-actor-id", handoff["id"])
        occurred_at = parse_time(
            action["occurred_at"],
            f"remote action {action['id']}.occurred_at",
        )
        if (
            occurred_at < coordinator_receipt["coverage_start"]
            or occurred_at > coordinator_receipt["coverage_end"]
        ):
            reject("remote-coverage-incomplete")
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
    if authority_hook is not None:
        authority_hook(
            canonical_authority["observation"]["attempt"],
            "before-eligibility-confirm",
            canonical_authority["object_id"],
        )
    confirm_history_authority_observation(
        repository_root,
        coordinator_installation,
        canonical_authority["observation"],
    )
    for code in _terminal_remote_state_rejections(
        repository_root,
        coordinator_installation,
        canonical_authority,
    ):
        for handoff in handoffs:
            reject(code, handoff["id"])
    completed = [
        result for result in results.values() if result["outcome"] == "accepted"
    ]
    trusted_push_eligible = (
        len(completed) == len(results) == 1
        and not any(
            code
            for code in global_rejections
            if code not in {
                "authoritative-run-failed",
                "authoritative-run-incomplete",
            }
        )
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
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--worktree", type=Path)
    parser.add_argument(
        "--authority-operation",
        choices=("bootstrap", "advance", "bind"),
    )
    parser.add_argument("--repository")
    parser.add_argument("--issue", type=int)
    parser.add_argument("--pull-request", type=int)
    parser.add_argument("--expected-object-id")
    parser.add_argument("--expected-sequence", type=int)
    parser.add_argument("--handoff-id")
    parser.add_argument("--pull-request-observation", type=Path)
    parser.add_argument("--publication-attestation", type=Path)
    parser.add_argument("--coordinator-installation", type=Path)
    return parser.parse_args(argv)
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.authority_operation is not None:
            if args.worktree is None or args.repository is None:
                raise HandoffDataError(
                    "authority planning requires worktree and repository"
                )
            handoff_document = handoff_result = None
            if args.authority_operation == "advance":
                if args.issue is not None or args.pull_request is not None:
                    raise HandoffDataError("advance planning derives issue and pull request from the fixture")
                if args.fixture is None or args.handoff_id is None:
                    raise HandoffDataError(
                        "advance planning requires --fixture and --handoff-id"
                    )
                handoff_document = load_json(args.fixture)
                handoff_result = validate_document(
                    copy.deepcopy(expect_object(handoff_document, "handoff document")),
                    args.worktree,
                    coordinator_installation=args.coordinator_installation,
                )
            elif args.issue is None:
                raise HandoffDataError(
                    "bootstrap and bind planning require --issue"
                )
            elif args.fixture is not None or args.handoff_id is not None:
                raise HandoffDataError(
                    "bootstrap and bind planning do not accept handoff fixture inputs"
                )
            result = plan_history_authority(
                args.worktree,
                args.repository,
                args.issue,
                args.pull_request,
                operation=args.authority_operation,
                expected_object_id=args.expected_object_id,
                expected_sequence=args.expected_sequence,
                handoff_document=handoff_document,
                handoff_result=handoff_result,
                handoff_id=args.handoff_id,
                pull_request_observation=(
                    load_json(args.pull_request_observation)
                    if args.pull_request_observation is not None
                    else None
                ),
                publication_attestation=(
                    load_json(args.publication_attestation)
                    if args.publication_attestation is not None
                    else None
                ),
                coordinator_installation=args.coordinator_installation,
            )
        else:
            if args.fixture is None or args.worktree is None:
                raise HandoffDataError(
                    "handoff validation requires --fixture and --worktree"
                )
            result = validate_document(
                load_json(args.fixture),
                args.worktree,
                coordinator_installation=args.coordinator_installation,
            )
    except HandoffDataError as error:
        print(f"workflow-pilot handoff: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(normalized_json(result))
    if args.authority_operation is not None:
        return 0
    return 0 if result["summary"]["trusted_push_eligible"] else 2

if __name__ == "__main__":
    raise SystemExit(main())
