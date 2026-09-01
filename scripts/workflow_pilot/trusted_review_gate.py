#!/usr/bin/env python3
"""Credentialed review gate for an exact trusted base or external install."""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import hmac
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


GH = "/usr/bin/gh"
GIT = "/usr/bin/git"
BASE_CHECKER_PATH = "scripts/workflow_pilot/review_base_checker.py"
ASSERTION_PROGRAM_PATH = "scripts/workflow_pilot/review_assertions.py"
TRUSTED_GATE_PATH = "scripts/workflow_pilot/trusted_review_gate.py"
DECISION_RECORD_PATH = ".github/workflow-pilot-decisions.json"
TRUSTED_REQUIRED_PATHS = {
    "scripts/workflow_pilot/__init__.py",
    TRUSTED_GATE_PATH,
    "scripts/workflow_pilot/reporter.py",
    "scripts/workflow_pilot/review_family.py",
    BASE_CHECKER_PATH,
    ASSERTION_PROGRAM_PATH,
}
BASE_CHECKER_ARGV = (
    "/usr/bin/python3",
    "-I",
    "review_base_checker.py",
    "--input",
    "checker-input.json",
)
ASSERTION_PROGRAM_ARGV = (
    "/usr/bin/python3",
    "-I",
    "review_assertions.py",
    "--stdin",
)
ASSERTION_INPUT_PATHS = (
    DECISION_RECORD_PATH,
    ".github/workflows/build.yml",
    ".github/skills/development-workflow/SKILL.md",
    "docs/test-cases/registry.json",
    "docs/test-cases/workflow-governance.md",
    "docs/workflow-pilot.md",
    "scripts/check_docs.py",
    "scripts/docs_check_tests/test_check_docs.py",
    "scripts/docs_check_tests/test_development_workflow_skill.py",
    "scripts/workflow_pilot/__init__.py",
    "scripts/workflow_pilot/candidate_evidence.py",
    "scripts/workflow_pilot/event_classifier.py",
    "scripts/workflow_pilot/hydrate_authority.py",
    "scripts/workflow_pilot/review_assertions.py",
    "scripts/workflow_pilot/review_base_checker.py",
    "scripts/workflow_pilot/review_family.py",
    "scripts/workflow_pilot/reporter.py",
    "scripts/workflow_pilot/tests/fixtures/event_classification.json",
    "scripts/workflow_pilot/trusted_review_gate.py",
    "tests/workflows/test_build_ci_topology.py",
)
RECEIPT_DOMAIN = b"workflow-review-authenticated-envelope-v2\0"
EXECUTION_RECEIPT_DOMAIN = b"workflow-review-execution-receipt-v2\0"
RECEIPT_PURPOSE = "independent-pre-review-report"
RECEIPT_MAX_LIFETIME_SECONDS = 600
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

# Actor is not a Node in GitHub's schema. Every polymorphic Actor selection
# obtains id through a Node fragment; viewer is a concrete User.
GRAPHQL_QUERY = r"""
query ReviewFamilyEvidence($owner: String!, $name: String!, $number: Int!) {
  viewer { id login }
  repository(owner: $owner, name: $name) {
    viewerPermission
    owner { login ... on Node { id } }
    pullRequest(number: $number) {
      id
      number
      createdAt
      baseRefOid
      headRefOid
      author { login ... on Node { id } }
      commits(first: 100) {
        pageInfo { hasNextPage }
        nodes { commit { id oid pushedDate committedDate } }
      }
      timelineItems(first: 100, itemTypes: [HEAD_REF_FORCE_PUSHED_EVENT]) {
        pageInfo { hasNextPage }
        nodes {
          ... on HeadRefForcePushedEvent {
            id
            createdAt
            afterCommit { oid }
          }
        }
      }
      reviews(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          id
          databaseId
          state
          submittedAt
          body
          commit { oid }
          author { login ... on Node { id } }
          comments(first: 100) {
            pageInfo { hasNextPage }
            nodes {
              id
              createdAt
              body
              author { login ... on Node { id } }
            }
          }
        }
      }
      reviewThreads(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          id
          isResolved
          comments(first: 100) {
            pageInfo { hasNextPage }
            nodes {
              id
              createdAt
              author { login ... on Node { id } }
              pullRequestReview { id }
            }
          }
        }
      }
      comments(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          id
          createdAt
          body
          author { login ... on Node { id } }
        }
      }
    }
  }
}
"""


reporter: Any = None
review_family: Any = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _receipt_scope(
    repository: str,
    pull_request: int,
    base_sha: str,
    original_pre_review_head: str,
    key_id: str,
    key_epoch: int,
) -> dict[str, Any]:
    return {
        "repository": repository,
        "pull_request": pull_request,
        "base_sha": base_sha,
        "candidate_sha": original_pre_review_head,
        "key_id": key_id,
        "key_epoch": key_epoch,
        "purpose": RECEIPT_PURPOSE,
    }


def _preserved_receipt_bytes(
    replay_store: Path,
    *,
    repository: str,
    pull_request: int,
    base_sha: str,
    original_pre_review_head: str,
    key_id: str,
    key_epoch: int,
) -> bytes:
    scope = _receipt_scope(
        repository,
        pull_request,
        base_sha,
        original_pre_review_head,
        key_id,
        key_epoch,
    )
    scope_id = hashlib.sha256(reporter.normalized_json(scope)).hexdigest()
    path = replay_store.resolve() / f"original-{scope_id}"
    if path.is_symlink():
        raise reporter.PilotDataError(
            "preserved original pre-review is unavailable"
        )
    try:
        return path.read_bytes()
    except OSError as error:
        raise reporter.PilotDataError(
            "preserved original pre-review is unavailable"
        ) from error


def receipt_scope(
    repository: str,
    pull_request: int,
    base_sha: str,
    original_pre_review_head: str,
    key_id: str,
    key_epoch: int,
) -> dict[str, Any]:
    return _receipt_scope(
        repository,
        pull_request,
        base_sha,
        original_pre_review_head,
        key_id,
        key_epoch,
    )


def preserved_receipt_bytes(
    replay_store: Path,
    *,
    repository: str,
    pull_request: int,
    base_sha: str,
    original_pre_review_head: str,
    key_id: str,
    key_epoch: int,
) -> bytes:
    return _preserved_receipt_bytes(
        replay_store,
        repository=repository,
        pull_request=pull_request,
        base_sha=base_sha,
        original_pre_review_head=original_pre_review_head,
        key_id=key_id,
        key_epoch=key_epoch,
    )


def persist_original_receipt(
    receipt_bytes: bytes,
    replay_store: Path,
    *,
    repository: str,
    pull_request: int,
    base_sha: str,
    original_pre_review_head: str,
    key_id: str,
    key_epoch: int,
) -> None:
    _expect_bound_modules()
    if not isinstance(receipt_bytes, bytes):
        raise reporter.PilotDataError("receipt must be immutable bytes")
    if replay_store.is_symlink():
        raise reporter.PilotDataError(
            "authenticated pre-review requires external replay authority"
        )
    replay_store = replay_store.resolve()
    if not replay_store.is_dir():
        raise reporter.PilotDataError(
            "authenticated pre-review replay store is unavailable"
        )
    scope_id = hashlib.sha256(
        reporter.normalized_json(
            receipt_scope(
                repository,
                pull_request,
                base_sha,
                original_pre_review_head,
                key_id,
                key_epoch,
            )
        )
    ).hexdigest()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    receipt_path = replay_store / f"original-{scope_id}"
    try:
        descriptor = os.open(receipt_path, flags, 0o600)
    except FileExistsError as error:
        raise reporter.PilotDataError(
            "authenticated original pre-review was already consumed or re-signed"
        ) from error
    try:
        os.write(descriptor, receipt_bytes)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _minimal_git(root: Path, *arguments: str) -> bytes:
    environment = {
        "HOME": str(root),
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    completed = subprocess.run(
        (GIT, "--no-replace-objects", "-C", str(root), *arguments),
        env=environment,
        check=False,
        capture_output=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            completed.stderr.decode("utf-8", errors="replace").strip()
            or "trusted Git command failed"
        )
    return completed.stdout


def _bind_trusted_modules(
    trusted_root: Path,
    candidate_root: Path,
    expected_base: str,
) -> None:
    """Bind imports only after proving the trusted installation boundary."""
    global reporter, review_family

    trusted_root = trusted_root.resolve()
    candidate_root = candidate_root.resolve()
    if trusted_root == candidate_root:
        raise RuntimeError("candidate checkout cannot be the trusted installation")
    for entry in sys.path:
        if not entry:
            continue
        resolved_entry = Path(entry).resolve()
        if (
            resolved_entry == candidate_root
            or candidate_root in resolved_entry.parents
            or resolved_entry in candidate_root.parents
        ):
            raise RuntimeError(
                "candidate checkout is present in trusted sys.path"
            )
    expected_script = (trusted_root / TRUSTED_GATE_PATH).resolve()
    if Path(__file__).resolve() != expected_script:
        raise RuntimeError("trusted gate was not launched from trusted root")
    head = _minimal_git(
        trusted_root, "rev-parse", "--verify", "HEAD^{commit}"
    ).decode("ascii").strip()
    if head != expected_base:
        raise RuntimeError("trusted checkout is not the exact expected PR base")
    if _minimal_git(
        trusted_root,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
        "--ignored=matching",
    ):
        raise RuntimeError(
            "trusted base checkout has tracked, index, or untracked changes"
        )

    verified_paths = set()
    pending_paths = set(TRUSTED_REQUIRED_PATHS)
    while pending_paths:
        relative = pending_paths.pop()
        if relative in verified_paths:
            continue
        source_path = (trusted_root / relative).resolve()
        if trusted_root not in source_path.parents or not source_path.is_file():
            raise RuntimeError(f"trusted Python source is unavailable: {relative}")
        records = [
            record
            for record in _minimal_git(
                trusted_root,
                "ls-tree",
                "-z",
                "--full-tree",
                expected_base,
                "--",
                relative,
            ).split(b"\0")
            if record
        ]
        if len(records) != 1:
            raise RuntimeError(
                f"trusted Python source is not an exact base-tree entry: {relative}"
            )
        metadata, raw_path = records[0].split(b"\t", 1)
        mode, kind, blob_oid = metadata.decode("ascii").split()
        if (
            raw_path.decode("utf-8") != relative
            or mode not in {"100644", "100755"}
            or kind != "blob"
        ):
            raise RuntimeError(
                f"trusted Python source has an unsafe tree entry: {relative}"
            )
        worktree_oid = _minimal_git(
            trusted_root,
            "hash-object",
            "--no-filters",
            str(source_path),
        ).decode("ascii").strip()
        if worktree_oid != blob_oid:
            raise RuntimeError(
                f"trusted Python source differs from exact base object: {relative}"
            )
        try:
            syntax = ast.parse(source_path.read_bytes(), filename=relative)
        except (OSError, SyntaxError) as error:
            raise RuntimeError(
                f"trusted Python source cannot be parsed: {relative}"
            ) from error
        for node in ast.walk(syntax):
            module_names = []
            if isinstance(node, ast.Import):
                module_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    raise RuntimeError(
                        f"trusted Python source uses an unresolved relative import: "
                        f"{relative}"
                    )
                if node.module == "scripts.workflow_pilot":
                    module_names.extend(
                        f"scripts.workflow_pilot.{alias.name}"
                        for alias in node.names
                    )
                elif node.module:
                    module_names.append(node.module)
            for module_name in module_names:
                prefix = "scripts.workflow_pilot."
                if module_name.startswith(prefix):
                    local_path = (
                        "scripts/workflow_pilot/"
                        + module_name[len(prefix) :].replace(".", "/")
                        + ".py"
                    )
                    pending_paths.add(local_path)
        verified_paths.add(relative)

    if not TRUSTED_REQUIRED_PATHS <= verified_paths:
        raise RuntimeError("trusted Python import graph is incomplete")
    forbidden_modules = {
        name
        for name in sys.modules
        if name == "scripts" or name.startswith("scripts.workflow_pilot")
    }
    if forbidden_modules:
        raise RuntimeError(
            "trusted Python package was imported before object verification"
        )
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(trusted_root))
    reporter = importlib.import_module("scripts.workflow_pilot.reporter")
    review_family = importlib.import_module("scripts.workflow_pilot.review_family")
    loaded_paths = {
        Path(reporter.__file__).resolve(),
        Path(review_family.__file__).resolve(),
    }
    if any(trusted_root not in path.parents for path in loaded_paths):
        raise RuntimeError("trusted modules did not load from trusted root")
    if _minimal_git(
        trusted_root,
        "status",
        "--porcelain=v2",
        "--untracked-files=all",
        "--ignored=matching",
    ):
        raise RuntimeError("trusted imports changed the exact base checkout")


def prepare_trusted_modules(
    trusted_root: Path,
    candidate_root: Path,
    expected_base: str,
) -> None:
    _bind_trusted_modules(trusted_root, candidate_root, expected_base)


def _expect_bound_modules() -> None:
    if reporter is None or review_family is None:
        raise RuntimeError("trusted modules are not bound")


def _verify_signed_receipt_bytes(
    receipt_bytes: bytes,
    *,
    repository: str,
    pull_request: int,
    base_sha: str,
    candidate_sha: str,
    trusted_key_id: str,
    trusted_key_epoch: int,
    trusted_key: bytes,
    current_time: datetime,
    replay_store: Path | None,
    consume_nonce: bool,
    require_current_time: bool = True,
    require_preserved: bool = False,
) -> tuple[bytes, dict[str, Any]]:
    _expect_bound_modules()
    if not isinstance(receipt_bytes, bytes):
        raise reporter.PilotDataError("receipt must be immutable bytes")
    try:
        text = receipt_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise reporter.PilotDataError("receipt is not UTF-8") from error
    envelope = reporter.expect_object(
        reporter.parse_json(text, "authenticated pre-review receipt"),
        "authenticated pre-review receipt",
    )
    if reporter.normalized_json(envelope) != receipt_bytes:
        raise reporter.PilotDataError(
            "authenticated pre-review receipt is not canonical immutable bytes"
        )
    reporter.expect_keys(
        envelope,
        "authenticated pre-review receipt",
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
    if envelope["schema_version"] != 2:
        raise reporter.PilotDataError(
            "authenticated pre-review receipt.schema_version must be 2"
        )
    scope = _receipt_scope(
        repository,
        pull_request,
        base_sha,
        candidate_sha,
        trusted_key_id,
        trusted_key_epoch,
    )
    for field, expected in scope.items():
        if envelope[field] != expected:
            raise reporter.PilotDataError(
                f"authenticated pre-review receipt {field} is outside trusted scope"
            )
    nonce = reporter.expect_string(
        envelope["nonce"], "authenticated pre-review receipt.nonce"
    )
    if NONCE_RE.fullmatch(nonce) is None:
        raise reporter.PilotDataError("authenticated pre-review nonce is malformed")
    issued = reporter.parse_time(
        envelope["issued_at"], "authenticated pre-review receipt.issued_at"
    )
    expires = reporter.parse_time(
        envelope["expires_at"], "authenticated pre-review receipt.expires_at"
    )
    assert issued is not None and expires is not None
    current_time = current_time.astimezone(timezone.utc)
    if expires <= issued:
        raise reporter.PilotDataError("authenticated pre-review lifetime is invalid")
    if (expires - issued).total_seconds() > RECEIPT_MAX_LIFETIME_SECONDS:
        raise reporter.PilotDataError(
            "authenticated pre-review lifetime exceeds maximum"
        )
    if require_current_time and (
        current_time < issued or current_time >= expires
    ):
        raise reporter.PilotDataError(
            "authenticated pre-review receipt is stale or not yet valid"
        )
    if not isinstance(trusted_key, bytes) or len(trusted_key) < 32:
        raise reporter.PilotDataError("receipt trust key must contain 32 bytes")
    supplied_hmac = reporter.expect_string(
        envelope["hmac_sha256"], "authenticated pre-review receipt.hmac_sha256"
    )
    signed = {
        key: value for key, value in envelope.items() if key != "hmac_sha256"
    }
    expected_hmac = hmac.new(
        trusted_key,
        RECEIPT_DOMAIN + reporter.normalized_json(signed),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_hmac, expected_hmac):
        raise reporter.PilotDataError(
            "authenticated pre-review receipt signature is invalid"
        )
    try:
        payload = base64.b64decode(envelope["payload_b64"], validate=True)
    except (ValueError, TypeError) as error:
        raise reporter.PilotDataError(
            "authenticated pre-review payload is not canonical base64"
        ) from error
    if base64.b64encode(payload).decode("ascii") != envelope["payload_b64"]:
        raise reporter.PilotDataError(
            "authenticated pre-review payload is not canonical base64"
        )
    if consume_nonce and require_preserved:
        raise reporter.PilotDataError(
            "pre-review cannot be consumed and preserved simultaneously"
        )
    if consume_nonce or require_preserved:
        if replay_store is None or replay_store.is_symlink():
            raise reporter.PilotDataError(
                "authenticated pre-review requires external replay authority"
            )
        replay_store = replay_store.resolve()
        if not replay_store.is_dir():
            raise reporter.PilotDataError(
                "authenticated pre-review replay store is unavailable"
            )
        if consume_nonce:
            persist_original_receipt(
                receipt_bytes,
                replay_store,
                repository=repository,
                pull_request=pull_request,
                base_sha=base_sha,
                original_pre_review_head=candidate_sha,
                key_id=trusted_key_id,
                key_epoch=trusted_key_epoch,
            )
        else:
            preserved = preserved_receipt_bytes(
                replay_store,
                repository=repository,
                pull_request=pull_request,
                base_sha=base_sha,
                original_pre_review_head=candidate_sha,
                key_id=trusted_key_id,
                key_epoch=trusted_key_epoch,
            )
            if not hmac.compare_digest(preserved, receipt_bytes):
                raise reporter.PilotDataError(
                    "preserved original pre-review bytes changed"
                )
    return payload, envelope


def _execution_receipt_seal(raw: dict[str, Any], key: bytes) -> str:
    payload = {name: value for name, value in raw.items() if name != "seal"}
    return hmac.new(
        key,
        EXECUTION_RECEIPT_DOMAIN + reporter.normalized_json(payload),
        hashlib.sha256,
    ).hexdigest()


def _git_text(repository_root: Path, *arguments: str) -> str:
    return reporter.run_git(repository_root, *arguments).decode("utf-8").strip()


def _base_contains_gate(repository_root: Path, base_sha: str) -> bool:
    for path in (
        TRUSTED_GATE_PATH,
        BASE_CHECKER_PATH,
        ASSERTION_PROGRAM_PATH,
        *ASSERTION_INPUT_PATHS,
    ):
        try:
            reporter.run_git(repository_root, "cat-file", "-e", f"{base_sha}:{path}")
        except reporter.PilotDataError:
            return False
    return True


def _parse_trigger_decision_records(
    decisions: dict[str, Any],
    *,
    pull_request: int,
    label: str,
) -> tuple[dict[str, Any] | None, bool]:
    reporter.expect_keys(
        decisions,
        label,
        ("schema_version", "pull_requests", "artifacts"),
    )
    reporter.expect_int(decisions["schema_version"], f"{label}.schema_version", 1)
    records = reporter.expect_list(decisions["pull_requests"], f"{label}.pull_requests")
    reporter.expect_list(decisions["artifacts"], f"{label}.artifacts")
    seen = set()
    match = None
    for index, raw_record in enumerate(records):
        record_label = f"{label}.pull_requests[{index}]"
        record = reporter.expect_object(raw_record, record_label)
        reporter.expect_keys(
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
        number = reporter.expect_int(
            record["pull_request"], f"{record_label}.pull_request", 1
        )
        if number in seen:
            raise reporter.PilotDataError(
                f"{label} repeats PR {number}"
            )
        seen.add(number)
        if number != pull_request:
            continue
        threshold = reporter.expect_object(record["threshold"], f"{record_label}.threshold")
        reporter.expect_keys(
            threshold,
            f"{record_label}.threshold",
            ("triggers", "override_history"),
        )
        trigger = review_family.normalize_trigger_fields(
            record["risk_boundaries"],
            threshold["triggers"],
            label=record_label,
        )
        match = {
            "pull_request": number,
            "trigger": trigger,
            "pre_review_required": review_family.trigger_requires_pre_review(trigger),
        }
    return match, bool(records)


def _load_authoritative_trigger(
    contract: dict[str, Any],
    repository_root: Path,
    candidate_sha: str,
    *,
    decision_record_path: Path | None = None,
) -> dict[str, Any] | None:
    root = reporter.validate_repository_root(repository_root)
    if decision_record_path is not None:
        raise reporter.PilotDataError(
            "trusted trigger authority must derive from the exact base commit"
        )
    try:
        decisions = reporter.load_decisions_from_commit(root, contract["base_sha"])
    except reporter.PilotDataError as error:
        raise reporter.PilotDataError(
            "authoritative trigger decision record is unavailable from the exact base commit"
        ) from error
    authoritative, _ = _parse_trigger_decision_records(
        decisions,
        pull_request=contract["pull_request"],
        label=f"decision record at commit {contract['base_sha']}",
    )
    if authoritative is None:
        return None
    if authoritative["trigger"] != contract["trigger"]:
        raise reporter.PilotDataError(
            "candidate trigger does not match the authoritative decision record"
        )
    blob_oid = _minimal_git(
        root,
        "rev-parse",
        f"{contract['base_sha']}:{DECISION_RECORD_PATH}",
    ).decode("ascii").strip()
    candidate_path = (root / DECISION_RECORD_PATH).resolve()
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise reporter.PilotDataError(
            "candidate decision record is unavailable for drift validation"
        )
    try:
        candidate_decisions = reporter.expect_object(
            reporter.parse_json(
                candidate_path.read_text(encoding="utf-8"),
                str(candidate_path),
            ),
            "candidate trigger decisions",
        )
    except OSError as error:
        raise reporter.PilotDataError(
            "candidate decision record is unavailable for drift validation"
        ) from error
    candidate_record, _ = _parse_trigger_decision_records(
        candidate_decisions,
        pull_request=contract["pull_request"],
        label="candidate trigger decisions",
    )
    if candidate_record is not None and candidate_record["trigger"] != authoritative["trigger"]:
        raise reporter.PilotDataError(
            "candidate decision record drifts from the authoritative base decision"
        )
    return {
        "path": DECISION_RECORD_PATH,
        "blob_oid": blob_oid,
        "pull_request": contract["pull_request"],
        "base_sha": contract["base_sha"],
        "candidate_sha": candidate_sha,
        "risk_boundaries": authoritative["trigger"]["risk_boundaries"],
        "threshold_triggers": authoritative["trigger"]["threshold_triggers"],
        "pre_review_required": authoritative["pre_review_required"],
    }


def load_authoritative_trigger(
    contract: dict[str, Any],
    repository_root: Path,
    candidate_sha: str,
    *,
    decision_record_path: Path | None = None,
) -> dict[str, Any] | None:
    return _load_authoritative_trigger(
        contract,
        repository_root,
        candidate_sha,
        decision_record_path=decision_record_path,
    )


def build_result_manifest(
    execution_receipts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        result
        for receipt in execution_receipts
        for result in receipt["assertion_results"]
    ]


def build_live_evidence_payload(
    *,
    contract: dict[str, Any],
    expected_candidate: str,
    source_kind: str,
    captured_at: str,
    original_receipt_sha256: str,
    pull_request: dict[str, Any],
    authoritative_trigger: dict[str, Any] | None,
    actors: list[dict[str, str]],
    pre_reviews: list[dict[str, Any]],
    pre_review_findings: list[dict[str, Any]],
    remote_reviews: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    threads: list[dict[str, Any]],
    force_push_events: list[dict[str, Any]],
    architecture_dispositions: list[dict[str, Any]],
    execution_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    _expect_bound_modules()
    return {
        "schema_version": review_family.SCHEMA_VERSION,
        "repository": contract["repository"],
        "source": {"kind": source_kind, "complete": True},
        "captured_at": captured_at,
        "candidate": {"sha": expected_candidate},
        "original_pre_review_head": contract["original_pre_review_head"],
        "original_receipt_sha256": original_receipt_sha256,
        "pull_request": pull_request,
        "authoritative_trigger": authoritative_trigger,
        "result_source_path": review_family.RESULT_SOURCE_PATH,
        "actors": actors,
        "pre_reviews": pre_reviews,
        "pre_review_findings": pre_review_findings,
        "remote_reviews": remote_reviews,
        "findings": findings,
        "threads": threads,
        "candidate_advances": [],
        "force_push_events": force_push_events,
        "architecture_dispositions": architecture_dispositions,
        "execution_receipts": execution_receipts,
        "result_manifest": build_result_manifest(execution_receipts),
    }


def run_base_pinned_checker(
    repository_root: Path,
    *,
    contract: dict[str, Any],
    candidate_sha: str,
    review_round: int,
    review_context: dict[str, Any],
    all_remote_reviews: list[dict[str, Any]],
    remote_findings: list[dict[str, Any]],
    remote_finding_ids: list[str],
    original_review_report_bytes: bytes,
    original_review_receipt: dict[str, Any],
    original_receipt_sha256: str,
    assertion_requests: list[dict[str, Any]],
    trusted_key: bytes,
    clock: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Execute only the checker blob at the exact authoritative PR base."""
    root = reporter.validate_repository_root(repository_root)
    base_sha = contract["base_sha"]
    current_head = _git_text(root, "rev-parse", "--verify", "HEAD^{commit}")
    if current_head != contract["candidate_sha"]:
        raise reporter.PilotDataError(
            "base checker checkout does not match current PR head"
        )
    pre_status = reporter.run_git(root, "status", "--porcelain")
    if pre_status:
        raise reporter.PilotDataError(
            "base checker requires a clean candidate worktree"
        )
    reporter.run_git(root, "merge-base", "--is-ancestor", base_sha, candidate_sha)
    reporter.run_git(
        root,
        "merge-base",
        "--is-ancestor",
        contract["original_pre_review_head"],
        current_head,
    )
    base_tree = _git_text(root, "rev-parse", f"{base_sha}^{{tree}}")
    candidate_tree = _git_text(root, "rev-parse", f"{candidate_sha}^{{tree}}")
    checker_blob = _git_text(root, "rev-parse", f"{base_sha}:{BASE_CHECKER_PATH}")
    checker_source = reporter.run_git(root, "show", f"{base_sha}:{BASE_CHECKER_PATH}")
    assertion_program_blob = _git_text(
        root, "rev-parse", f"{base_sha}:{ASSERTION_PROGRAM_PATH}"
    )
    assertion_program_source = reporter.run_git(
        root, "show", f"{base_sha}:{ASSERTION_PROGRAM_PATH}"
    )
    finding_origin_sha = (
        base_sha
        if review_round == 1
        else all_remote_reviews[review_round - 2]["candidate_sha"]
    )
    finding_origin_tree = _git_text(
        root, "rev-parse", f"{finding_origin_sha}^{{tree}}"
    )
    changes = review_family.derive_change_records(root, base_sha, candidate_sha)
    original_changes = review_family.derive_change_records(
        root, base_sha, contract["original_pre_review_head"]
    )
    changed_files = sorted(
        {
            path
            for change in changes
            for path in (change["old_path"], change["new_path"])
            if path is not None
        }
    )
    if not changed_files:
        raise reporter.PilotDataError("base checker candidate has no changed files")
    try:
        review_report = reporter.expect_object(
            reporter.parse_json(
                original_review_report_bytes.decode("utf-8"),
                "independent pre-review report",
            ),
            "independent pre-review report",
        )
    except UnicodeDecodeError as error:
        raise reporter.PilotDataError(
            "independent pre-review report is not UTF-8"
        ) from error
    review_contract = dict(contract["raw"])
    review_contract["candidate_sha"] = candidate_sha
    review_contract["trust_mode"] = contract["trust_mode"]
    checker_input = {
        "schema_version": 2,
        "repository": contract["repository"],
        "repository_root": str(root),
        "pull_request": contract["pull_request"],
        "base_sha": base_sha,
        "base_tree": base_tree,
        "original_pre_review_head": contract["original_pre_review_head"],
        "original_changes": original_changes,
        "original_receipt_sha256": original_receipt_sha256,
        "review_contract": review_contract,
        "original_review_receipt": original_review_receipt,
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "head_sha": candidate_sha,
        "review_round": review_round,
        "review_context": review_context,
        "all_remote_reviews": all_remote_reviews,
        "remote_findings": remote_findings,
        "trust_mode": contract["trust_mode"],
        "changed_files": changed_files,
        "changes": changes,
        "remote_finding_ids": sorted(remote_finding_ids),
        "limits": contract["limits"],
        "original_pre_review": review_report,
        "assertion_requests": assertion_requests,
    }
    checker_input_bytes = reporter.normalized_json(checker_input)
    artifact_root = root / "build" / "test-artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    resolved_artifact_root = artifact_root.resolve()
    if artifact_root.is_symlink() or root not in resolved_artifact_root.parents:
        raise reporter.PilotDataError("base checker artifact root escapes repository")
    sandbox = resolved_artifact_root / (
        f"review-base-check-{os.getpid()}-{candidate_sha[:12]}"
    )
    if sandbox.exists():
        raise reporter.PilotDataError("base checker sandbox already exists")
    sandbox.mkdir(mode=0o700)
    checker_path = sandbox / "review_base_checker.py"
    assertion_program_path = sandbox / "review_assertions.py"
    input_path = sandbox / "checker-input.json"
    origin_root = sandbox / "origin"
    head_root = sandbox / "head"
    origin_root.mkdir()
    head_root.mkdir()
    assertion_input_artifacts = []
    for relative in ASSERTION_INPUT_PATHS:
        origin_bytes = reporter.run_git(
            root, "show", f"{finding_origin_sha}:{relative}"
        )
        head_bytes = reporter.run_git(root, "show", f"{candidate_sha}:{relative}")
        origin_target = origin_root / relative
        head_target = head_root / relative
        origin_target.parent.mkdir(parents=True, exist_ok=True)
        head_target.parent.mkdir(parents=True, exist_ok=True)
        origin_target.write_bytes(origin_bytes)
        head_target.write_bytes(head_bytes)
        origin_target.chmod(0o444)
        head_target.chmod(0o444)
        assertion_input_artifacts.append(
            {
                "path": relative,
                "origin_blob_oid": _git_text(
                    root,
                    "rev-parse",
                    f"{finding_origin_sha}:{relative}",
                ),
                "head_blob_oid": _git_text(
                    root, "rev-parse", f"{candidate_sha}:{relative}"
                ),
            }
        )
    checker_path.write_bytes(checker_source)
    assertion_program_path.write_bytes(assertion_program_source)
    checker_input["assertion_program_path"] = str(assertion_program_path)
    checker_input["assertion_program_blob_oid"] = assertion_program_blob
    checker_input["assertion_program_argv"] = list(ASSERTION_PROGRAM_ARGV)
    checker_input["finding_origin_sha"] = finding_origin_sha
    checker_input["finding_origin_tree"] = finding_origin_tree
    checker_input["origin_root"] = str(origin_root)
    checker_input["head_root"] = str(head_root)
    checker_input["assertion_input_artifacts"] = assertion_input_artifacts
    checker_input_bytes = reporter.normalized_json(checker_input)
    input_path.write_bytes(checker_input_bytes)
    checker_path.chmod(0o444)
    assertion_program_path.chmod(0o444)
    input_path.chmod(0o444)
    for directory in sorted(
        {
            path
            for root_path in (origin_root, head_root)
            for target in root_path.rglob("*")
            for path in ([target] if target.is_dir() else [])
        },
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        directory.chmod(0o555)
    origin_root.chmod(0o555)
    head_root.chmod(0o555)
    sandbox.chmod(0o555)
    command = (
        "/usr/bin/python3",
        "-I",
        str(checker_path),
        "--input",
        str(input_path),
    )
    environment = {
        "HOME": str(sandbox),
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
    }
    started = clock()
    try:
        completed = subprocess.run(
            command,
            cwd=sandbox,
            env=environment,
            check=False,
            capture_output=True,
            timeout=60,
        )
        finished = clock()
        post_status = reporter.run_git(root, "status", "--porcelain")
        read_only = (
            checker_path.stat().st_mode & 0o222 == 0
            and assertion_program_path.stat().st_mode & 0o222 == 0
            and input_path.stat().st_mode & 0o222 == 0
            and sandbox.stat().st_mode & 0o222 == 0
            and all(
                path.stat().st_mode & 0o222 == 0
                for root_path in (origin_root, head_root)
                for path in (root_path, *root_path.rglob("*"))
            )
        )
    finally:
        sandbox.chmod(0o700)
        for root_path in (origin_root, head_root):
            for directory in sorted(
                (path for path in root_path.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            ):
                directory.chmod(0o700)
            root_path.chmod(0o700)
        shutil.rmtree(sandbox)
    output = completed.stdout + b"\0stderr\0" + completed.stderr
    parsed_output = None
    if completed.returncode == 0:
        parsed_output = reporter.expect_object(
            reporter.parse_json(
                completed.stdout.decode("utf-8"), "base checker output"
            ),
            "base checker output",
        )
        reporter.expect_keys(
            parsed_output,
            "base checker output",
            (
                "schema_version",
                "registry_version",
                "input_sha256",
                "command_id",
                "results",
            ),
        )
        expected_input_sha256 = hashlib.sha256(checker_input_bytes).hexdigest()
        if (
            parsed_output["schema_version"] != 2
            or parsed_output["registry_version"] != 1
            or parsed_output["input_sha256"] != expected_input_sha256
        ):
            raise reporter.PilotDataError(
                "base checker output does not bind its exact immutable input"
            )
        assertion_results = reporter.expect_list(
            parsed_output["results"], "base checker output.results"
        )
        if any(
            reporter.expect_object(
                result, f"base checker output.results[{index}]"
            ).get("input_sha256")
            != expected_input_sha256
            for index, result in enumerate(assertion_results)
        ):
            raise reporter.PilotDataError(
                "base checker result does not bind its exact immutable input"
            )
    raw = {
        "id": f"BASE-CHECK:{base_sha}:{candidate_sha}:ROUND:{review_round}",
        "check_id": "base-pinned-independent-review",
        "base_sha": base_sha,
        "base_tree": base_tree,
        "original_pre_review_head": contract["original_pre_review_head"],
        "original_receipt_sha256": original_receipt_sha256,
        "review_round": review_round,
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "checker_path": BASE_CHECKER_PATH,
        "checker_blob_oid": checker_blob,
        "argv": list(BASE_CHECKER_ARGV),
        "assertion_program_path": ASSERTION_PROGRAM_PATH,
        "assertion_program_blob_oid": assertion_program_blob,
        "assertion_program_argv": list(ASSERTION_PROGRAM_ARGV),
        "finding_origin_sha": finding_origin_sha,
        "finding_origin_tree": finding_origin_tree,
        "assertion_input_artifacts": assertion_input_artifacts,
        "changed_files": changed_files,
        "changes": changes,
        "remote_finding_ids": sorted(remote_finding_ids),
        "review_report_sha256": hashlib.sha256(
            original_review_report_bytes
        ).hexdigest(),
        "checker_input_sha256": hashlib.sha256(checker_input_bytes).hexdigest(),
        "assertion_results": parsed_output["results"] if parsed_output else [],
        "read_only": read_only,
        "pre_clean": pre_status == b"",
        "post_clean": post_status == b"",
        "started_at": _format_time(started),
        "completed_at": _format_time(finished),
        "exit_code": completed.returncode,
        "result": "pass" if completed.returncode == 0 else "fail",
        "output_sha256": hashlib.sha256(output).hexdigest(),
    }
    raw["seal"] = _execution_receipt_seal(raw, trusted_key)
    return raw


class GhApiAdapter:
    """Closed read-only adapter for the GitHub GraphQL API."""

    def fetch(self, repository: str, pull_request: int) -> dict[str, Any]:
        owner, separator, name = repository.partition("/")
        if not separator or not owner or not name or "/" in name:
            raise reporter.PilotDataError("repository must use owner/name form")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"GH_HOST", "GH_TOKEN", "GITHUB_TOKEN", "HOME"}
        }
        environment.update({"LC_ALL": "C", "PATH": "/usr/bin:/bin"})
        completed = subprocess.run(
            (
                GH,
                "api",
                "graphql",
                "-f",
                f"query={GRAPHQL_QUERY}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"number={pull_request}",
            ),
            env=environment,
            check=False,
            capture_output=True,
            timeout=60,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise reporter.PilotDataError(
                "GitHub review evidence collection failed"
                + (f": {detail}" if detail else "")
            )
        return reporter.parse_json(
            completed.stdout.decode("utf-8"), "gh api graphql review evidence"
        )


def _expect_page_complete(connection: Any, label: str) -> list[Any]:
    connection = reporter.expect_object(connection, label)
    reporter.expect_keys(connection, label, ("pageInfo", "nodes"))
    page_info = reporter.expect_object(connection["pageInfo"], f"{label}.pageInfo")
    reporter.expect_keys(page_info, f"{label}.pageInfo", ("hasNextPage",))
    if reporter.expect_bool(
        page_info["hasNextPage"], f"{label}.pageInfo.hasNextPage"
    ):
        raise reporter.PilotDataError(
            f"{label} exceeds the bounded complete GitHub collection"
        )
    return reporter.expect_list(connection["nodes"], f"{label}.nodes")


def _actor(raw: Any, kind: str, label: str) -> dict[str, str]:
    raw = reporter.expect_object(raw, label)
    reporter.expect_keys(raw, label, ("id", "login"))
    return {
        "id": reporter.expect_string(raw["id"], f"{label}.id"),
        "login": reporter.expect_string(raw["login"], f"{label}.login"),
        "kind": kind,
    }


def _collect_actors(records: list[dict[str, str]]) -> list[dict[str, str]]:
    actors = {}
    for actor in records:
        existing = actors.get(actor["id"])
        if existing is not None and existing != actor:
            raise reporter.PilotDataError(
                f"GitHub actor ID {actor['id']!r} changed identity"
            )
        actors[actor["id"]] = actor
    return [actors[actor_id] for actor_id in sorted(actors)]


def _parse_disposition_comments(
    comments: list[Any], actors: list[dict[str, str]]
) -> list[dict[str, Any]]:
    prefix = "workflow-review-family-disposition:v2 "
    result = []
    for index, raw in enumerate(comments):
        label = f"GitHub pull-request comment[{index}]"
        comment = reporter.expect_object(raw, label)
        reporter.expect_keys(comment, label, ("id", "createdAt", "body", "author"))
        author = _actor(comment["author"], "user", f"{label}.author")
        actors.append(author)
        body = reporter.expect_string(comment["body"], f"{label}.body", allow_empty=True)
        if not body.startswith(prefix):
            continue
        payload = reporter.expect_object(
            reporter.parse_json(body[len(prefix) :], f"{label} disposition"),
            f"{label} disposition",
        )
        reporter.expect_keys(
            payload,
            f"{label} disposition",
            ("held_round", "held_head_sha", "authorized_next_head_sha", "action"),
        )
        result.append(
            {
                "node_id": reporter.expect_string(comment["id"], f"{label}.id"),
                "held_round": reporter.expect_int(
                    payload["held_round"], f"{label}.held_round", 3
                ),
                "held_head_sha": reporter.expect_sha(
                    payload["held_head_sha"], f"{label}.held_head_sha"
                ),
                "authorized_next_head_sha": reporter.expect_sha(
                    payload["authorized_next_head_sha"],
                    f"{label}.authorized_next_head_sha",
                ),
                "actor_id": author["id"],
                "action": payload["action"],
                "occurred_at": comment["createdAt"],
            }
        )
    return result


def collect_live_evidence_bytes(
    raw_contract: Any,
    repository_root: Path,
    expected_remote_head: str,
    expected_candidate: str,
    review_report: dict[str, Any],
    receipt_envelope: dict[str, Any],
    execution_receipts: list[dict[str, Any]],
    *,
    authoritative_trigger: dict[str, Any] | None = None,
    adapter: GhApiAdapter | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> bytes:
    """Collect credentialed state without executing any candidate module."""
    contract = review_family.validate_contract(raw_contract)
    root = reporter.validate_repository_root(repository_root)
    local_head = reporter.run_git(
        root, "rev-parse", "--verify", "HEAD^{commit}"
    ).decode("ascii").strip()
    if local_head != expected_candidate or contract["candidate_sha"] != expected_candidate:
        raise reporter.PilotDataError(
            "candidate checkout does not match the exact local candidate head"
        )
    payload = reporter.expect_object(
        (adapter or GhApiAdapter()).fetch(
            contract["repository"], contract["pull_request"]
        ),
        "GitHub GraphQL response",
    )
    reporter.expect_keys(payload, "GitHub GraphQL response", ("data",))
    data = reporter.expect_object(payload["data"], "GitHub GraphQL data")
    reporter.expect_keys(data, "GitHub GraphQL data", ("viewer", "repository"))
    viewer = _actor(data["viewer"], "user", "GitHub viewer")
    repository = reporter.expect_object(data["repository"], "GitHub repository")
    reporter.expect_keys(
        repository,
        "GitHub repository",
        ("viewerPermission", "owner", "pullRequest"),
    )
    if repository["viewerPermission"] != "READ":
        raise reporter.PilotDataError(
            "trusted GitHub collector must have exact READ permission"
        )
    pr = reporter.expect_object(repository["pullRequest"], "GitHub pull request")
    reporter.expect_keys(
        pr,
        "GitHub pull request",
        (
            "id",
            "number",
            "createdAt",
            "baseRefOid",
            "headRefOid",
            "author",
            "commits",
            "timelineItems",
            "reviews",
            "reviewThreads",
            "comments",
        ),
    )
    base = reporter.expect_sha(pr["baseRefOid"], "GitHub pull request base")
    head = reporter.expect_sha(pr["headRefOid"], "GitHub pull request head")
    if base != contract["base_sha"]:
        raise reporter.PilotDataError(
            "contract/trusted checker base does not equal authoritative PR base OID"
        )
    if head != expected_remote_head:
        raise reporter.PilotDataError(
            "authoritative PR head does not equal the expected remote head"
        )
    if review_report["candidate_sha"] != contract["original_pre_review_head"]:
        raise reporter.PilotDataError(
            "immutable pre-review does not bind original first-reviewed head"
        )
    author = _actor(pr["author"], "user", "GitHub pull-request author")
    owner = _actor(repository["owner"], "user", "GitHub repository owner")
    actor_records = [viewer, author, owner]

    # pushedDate is nullable metadata and is never reconstructed into head
    # authority. The authoritative head is headRefOid above.
    for index, raw_node in enumerate(
        _expect_page_complete(pr["commits"], "GitHub pull-request commits")
    ):
        label = f"GitHub pull-request commit[{index}]"
        node = reporter.expect_object(raw_node, label)
        reporter.expect_keys(node, label, ("commit",))
        commit = reporter.expect_object(node["commit"], f"{label}.commit")
        reporter.expect_keys(
            commit,
            f"{label}.commit",
            ("id", "oid", "pushedDate", "committedDate"),
        )
        commit_sha = reporter.expect_sha(commit["oid"], f"{label}.commit.oid")
        committed_at = reporter.parse_time(
            commit["committedDate"], f"{label}.commit.committedDate"
        )
        actual = reporter._load_git_commit_objects(root, (commit_sha,))[commit_sha]
        if committed_at != actual["committed_at"]:
            raise reporter.PilotDataError(
                f"{label}.commit.committedDate does not match Git authority"
            )
        if commit["pushedDate"] is not None:
            reporter.parse_time(commit["pushedDate"], f"{label}.commit.pushedDate")
    force_push_events = []
    for index, raw_event in enumerate(
        _expect_page_complete(
            pr["timelineItems"], "GitHub pull-request force-push events"
        )
    ):
        label = f"GitHub force-push event[{index}]"
        event = reporter.expect_object(raw_event, label)
        reporter.expect_keys(event, label, ("id", "createdAt", "afterCommit"))
        after_commit = reporter.expect_object(
            event["afterCommit"], f"{label}.afterCommit"
        )
        reporter.expect_keys(after_commit, f"{label}.afterCommit", ("oid",))
        force_push_events.append(
            {
                "node_id": reporter.expect_string(event["id"], f"{label}.id"),
                "candidate_sha": reporter.expect_sha(
                    after_commit["oid"], f"{label}.afterCommit.oid"
                ),
                "occurred_at": event["createdAt"],
            }
        )

    remote_reviews = []
    remote_findings = []
    review_nodes = _expect_page_complete(
        pr["reviews"], "GitHub pull-request reviews"
    )
    parsed_reviews = []
    for index, raw_review in enumerate(review_nodes):
        label = f"GitHub pull-request review[{index}]"
        review = reporter.expect_object(raw_review, label)
        reporter.expect_keys(
            review,
            label,
            (
                "id",
                "databaseId",
                "state",
                "submittedAt",
                "body",
                "commit",
                "author",
                "comments",
            ),
        )
        review_actor = _actor(review["author"], "bot", f"{label}.author")
        actor_records.append(review_actor)
        if (
            review_family.normalize_actor_login(review_actor["login"])
            != review_family.COPILOT_ACTOR
        ):
            continue
        comments = _expect_page_complete(review["comments"], f"{label}.comments")
        state = reporter.expect_enum(
            review["state"],
            {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"},
            f"{label}.state",
        )
        body = reporter.expect_string(review["body"], f"{label}.body", allow_empty=True)
        body_classification = review_family.classify_copilot_body(
            body, f"{label}.body"
        )
        review_commit = reporter.expect_object(review["commit"], f"{label}.commit")
        reporter.expect_keys(review_commit, f"{label}.commit", ("oid",))
        review_candidate = reporter.expect_sha(
            review_commit["oid"], f"{label}.commit.oid"
        )
        finding_ids = []
        for comment_index, raw_comment in enumerate(comments):
            comment_label = f"{label}.comments[{comment_index}]"
            comment = reporter.expect_object(raw_comment, comment_label)
            reporter.expect_keys(
                comment,
                comment_label,
                ("id", "createdAt", "body", "author"),
            )
            finding_author = _actor(
                comment["author"], "bot", f"{comment_label}.author"
            )
            actor_records.append(finding_author)
            finding_id = reporter.expect_string(
                comment["id"], f"{comment_label}.id"
            )
            finding_ids.append(finding_id)
            remote_findings.append(
                {
                    "node_id": finding_id,
                    "review_id": review["id"],
                    "candidate_sha": review_candidate,
                    "created_at": comment["createdAt"],
                    "author_actor_id": finding_author["id"],
                    "family": None,
                }
            )
        body_has_findings = body_classification not in {
            "clean-approval",
            "clean-legacy",
        }
        parsed_reviews.append(
            {
                "id": reporter.expect_int(review["databaseId"], f"{label}.databaseId"),
                "node_id": reporter.expect_string(review["id"], f"{label}.id"),
                "reviewer_actor_id": review_actor["id"],
                "candidate_sha": review_candidate,
                "submitted_at": review["submittedAt"],
                "state": state,
                "body": body,
                "body_classification": body_classification,
                "body_has_findings": body_has_findings,
                "outcome": (
                    "changes-requested"
                    if state == "CHANGES_REQUESTED"
                    or body_has_findings
                    or finding_ids
                    else "clean"
                ),
                "finding_ids": finding_ids,
            }
        )
    parsed_reviews.sort(key=lambda item: (item["submitted_at"], item["node_id"]))
    for round_number, review in enumerate(parsed_reviews, 1):
        remote_reviews.append({**review, "round": round_number})

    threads = []
    finding_to_thread = {}
    for index, raw_thread in enumerate(
        _expect_page_complete(pr["reviewThreads"], "GitHub pull-request threads")
    ):
        label = f"GitHub review thread[{index}]"
        thread = reporter.expect_object(raw_thread, label)
        reporter.expect_keys(thread, label, ("id", "isResolved", "comments"))
        comments = _expect_page_complete(thread["comments"], f"{label}.comments")
        if not comments:
            raise reporter.PilotDataError(f"{label} has no finding comment")
        first = reporter.expect_object(comments[0], f"{label}.comments[0]")
        reporter.expect_keys(
            first,
            f"{label}.comments[0]",
            ("id", "createdAt", "author", "pullRequestReview"),
        )
        finding_id = reporter.expect_string(
            first["id"], f"{label}.comments[0].id"
        )
        finding_to_thread[finding_id] = thread["id"]
        threads.append(
            {
                "node_id": reporter.expect_string(thread["id"], f"{label}.id"),
                "finding_id": finding_id,
                "is_resolved": reporter.expect_bool(
                    thread["isResolved"], f"{label}.isResolved"
                ),
            }
        )

    finding_families = review_family.finding_family_map(contract)
    for finding in remote_findings:
        finding_id = finding["node_id"]
        if finding_id not in finding_families:
            raise reporter.PilotDataError(
                f"remote finding {finding_id!r} has no candidate family sweep"
            )
        finding["family"] = finding_families[finding_id]
        if finding_id not in finding_to_thread:
            raise reporter.PilotDataError(
                f"remote finding {finding_id!r} has no review thread"
            )

    comment_nodes = _expect_page_complete(
        pr["comments"], "GitHub pull-request comments"
    )
    dispositions = _parse_disposition_comments(comment_nodes, actor_records)
    actor_records.append(
        {
            "id": review_report["reviewer_actor_id"],
            "login": review_report["reviewer_login"],
            "kind": "service",
        }
    )
    actors = _collect_actors(actor_records)
    pre_findings = [
        {
            "id": finding["id"],
            "review_id": review_report["report_id"],
            "candidate_sha": contract["original_pre_review_head"],
            "created_at": finding["created_at"],
            "author_actor_id": review_report["reviewer_actor_id"],
            "family": finding["family"],
        }
        for finding in review_report["findings"]
    ]
    for finding in pre_findings:
        expected_family = finding_families.get(finding["id"])
        if expected_family != finding["family"]:
            raise reporter.PilotDataError(
                f"local finding {finding['id']!r} family sweep does not match "
                "the immutable pre-review receipt"
            )
    pre_reviews = []
    if authoritative_trigger is not None and authoritative_trigger["pre_review_required"]:
        pre_reviews.append(
            {
                "id": review_report["report_id"],
                "owner_actor_id": review_report["reviewer_actor_id"],
                "candidate_sha": contract["original_pre_review_head"],
                "started_at": review_report["started_at"],
                "completed_at": review_report["completed_at"],
                "receipt_issued_at": receipt_envelope["issued_at"],
                "permissions": review_report["permissions"],
                "actions": [
                    {
                        "id": f"{review_report['report_id']}:READ",
                        "kind": "read-candidate",
                        "occurred_at": review_report["started_at"],
                    },
                    {
                        "id": f"{review_report['report_id']}:REPORT",
                        "kind": "emit-local-report",
                        "occurred_at": review_report["completed_at"],
                    },
                ],
                "finding_ids": [finding["id"] for finding in pre_findings],
                "reviewed_files": review_report["reviewed_files"],
                "reviewed_changes": review_report["reviewed_changes"],
            }
        )
    raw_evidence = build_live_evidence_payload(
        contract=contract,
        expected_candidate=expected_candidate,
        source_kind="live-gh-api",
        captured_at=_format_time(clock()),
        original_receipt_sha256=hashlib.sha256(
            reporter.normalized_json(receipt_envelope)
        ).hexdigest(),
        pull_request={
            "number": pr["number"],
            "node_id": pr["id"],
            "created_at": pr["createdAt"],
            "base_sha": base,
            "head_sha": head,
            "author_actor_id": author["id"],
        },
        authoritative_trigger=authoritative_trigger,
        actors=actors,
        pre_reviews=pre_reviews,
        pre_review_findings=pre_findings,
        remote_reviews=remote_reviews,
        findings=remote_findings,
        threads=threads,
        force_push_events=force_push_events,
        architecture_dispositions=dispositions,
        execution_receipts=execution_receipts,
    )
    return reporter.normalized_json(raw_evidence)


def _live_state_digest(evidence_bytes: bytes) -> str:
    evidence = reporter.expect_object(
        reporter.parse_json(evidence_bytes.decode("utf-8"), "live state"),
        "live state",
    )
    payload = {
        name: evidence[name]
        for name in (
            "repository",
            "candidate",
            "pull_request",
            "actors",
            "remote_reviews",
            "findings",
            "threads",
            "force_push_events",
            "architecture_dispositions",
        )
    }
    return hashlib.sha256(reporter.normalized_json(payload)).hexdigest()


def _bootstrap_result(contract: dict[str, Any], base_sha: str, head_sha: str):
    return {
        "schema_version": review_family.SCHEMA_VERSION,
        "identity": {
            "repository": contract["repository"],
            "pull_request": contract["pull_request"],
            "base_sha": base_sha,
            "original_pre_review_head": contract["original_pre_review_head"],
            "candidate_sha": head_sha,
        },
        "bootstrap": {
            "mode": "introduction",
            "trusted_checker_present_in_base": False,
            "external_coordinator_review_required": True,
        },
        "provenance": {
            "source": "trusted-base-bootstrap",
            "authoritative": True,
            "live_authoritative": True,
            "authenticated_receipt": False,
            "base_pinned_checker": False,
            "executable_evidence_trusted": False,
            "execution_receipt_seals": [],
        },
        "gates": {
            "push_allowed": False,
            "trusted_push_allowed": False,
            "remote_copilot_review_required": True,
            "current_candidate_reviewed": False,
            "current_candidate_clean": False,
            "merge_allowed": False,
        },
    }


def bootstrap_result(contract: dict[str, Any], base_sha: str, head_sha: str):
    return _bootstrap_result(contract, base_sha, head_sha)


def _run_trusted_gate(
    *,
    raw_contract: Any,
    repository_root: Path,
    expected_candidate: str,
    expected_remote_head: str | None,
    expected_base: str,
    review_receipt_bytes: bytes,
    replay_store: Path,
    trusted_key_id: str,
    trusted_key_epoch: int,
    trusted_key: bytes,
    current_time: datetime,
    pre_review_state: str = "new",
    decision_record_path: Path | None = None,
    adapter: GhApiAdapter | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    contract = review_family.validate_contract(raw_contract)
    expected_remote_head = (
        expected_candidate
        if expected_remote_head is None
        else reporter.expect_sha(expected_remote_head, "expected remote head")
    )
    if pre_review_state not in {"new", "preserved"}:
        raise reporter.PilotDataError("pre-review state is not supported")
    if contract["base_sha"] != expected_base:
        raise reporter.PilotDataError(
            "contract base does not equal externally authoritative PR base"
        )
    root = reporter.validate_repository_root(repository_root)
    local_head = reporter.run_git(
        root, "rev-parse", "--verify", "HEAD^{commit}"
    ).decode("ascii").strip()
    if local_head != expected_candidate:
        raise reporter.PilotDataError(
            "candidate checkout does not match the exact local candidate head"
        )
    if expected_remote_head != expected_candidate:
        try:
            reporter.run_git(
                root,
                "merge-base",
                "--is-ancestor",
                expected_remote_head,
                expected_candidate,
            )
        except reporter.PilotDataError as error:
            raise reporter.PilotDataError(
                "proposed local candidate is not a descendant of the current remote head"
            ) from error
    report_bytes, envelope = _verify_signed_receipt_bytes(
        review_receipt_bytes,
        repository=contract["repository"],
        pull_request=contract["pull_request"],
        base_sha=expected_base,
        candidate_sha=contract["original_pre_review_head"],
        trusted_key_id=trusted_key_id,
        trusted_key_epoch=trusted_key_epoch,
        trusted_key=trusted_key,
        current_time=current_time,
        replay_store=None,
        consume_nonce=False,
        require_current_time=False,
    )
    review_report = reporter.expect_object(
        reporter.parse_json(
            report_bytes.decode("utf-8"), "authenticated independent pre-review"
        ),
        "authenticated independent pre-review",
    )
    base_supports_gate = _base_contains_gate(repository_root, expected_base)
    authoritative_trigger = None
    if contract["trust_mode"] != "introduction" and base_supports_gate:
        authoritative_trigger = load_authoritative_trigger(
            contract,
            repository_root,
            expected_candidate,
            decision_record_path=decision_record_path,
        )
    first_evidence_bytes = collect_live_evidence_bytes(
        raw_contract,
        repository_root,
        expected_remote_head,
        expected_candidate,
        review_report,
        envelope,
        [],
        authoritative_trigger=authoritative_trigger,
        adapter=adapter,
        clock=clock,
    )
    first_evidence = reporter.expect_object(
        reporter.parse_json(first_evidence_bytes.decode("utf-8"), "first evidence"),
        "first evidence",
    )
    if (
        contract["trust_mode"] == "introduction"
        or not base_supports_gate
        or authoritative_trigger is None
    ):
        return bootstrap_result(contract, expected_base, expected_candidate)
    if contract["trust_mode"] != "base-pinned":
        raise reporter.PilotDataError("steady-state gate requires base-pinned mode")
    completed = reporter.parse_time(
        review_report["completed_at"], "independent pre-review completed_at"
    )
    issued = reporter.parse_time(envelope["issued_at"], "pre-review receipt issued_at")
    expires = reporter.parse_time(
        envelope["expires_at"], "pre-review receipt expires_at"
    )
    assert completed is not None and issued is not None and expires is not None
    if issued < completed:
        raise reporter.PilotDataError(
            "pre-review receipt was backdated before report completion"
        )
    if first_evidence["remote_reviews"]:
        first_remote = reporter.parse_time(
            first_evidence["remote_reviews"][0]["submitted_at"],
            "first remote review submitted_at",
        )
        assert first_remote is not None
        if issued >= first_remote or expires <= first_remote:
            raise reporter.PilotDataError(
                "pre-review receipt was not valid before first remote review"
            )
    consumed_report, consumed_envelope = _verify_signed_receipt_bytes(
        review_receipt_bytes,
        repository=contract["repository"],
        pull_request=contract["pull_request"],
        base_sha=expected_base,
        candidate_sha=contract["original_pre_review_head"],
        trusted_key_id=trusted_key_id,
        trusted_key_epoch=trusted_key_epoch,
        trusted_key=trusted_key,
        current_time=current_time,
        replay_store=replay_store,
        consume_nonce=pre_review_state == "new",
        require_current_time=False,
        require_preserved=pre_review_state == "preserved",
    )
    if consumed_report != report_bytes or consumed_envelope != envelope:
        raise reporter.PilotDataError("pre-review receipt changed during consumption")
    original_receipt_sha256 = hashlib.sha256(review_receipt_bytes).hexdigest()
    execution_receipts = []
    for review in first_evidence["remote_reviews"]:
        assertion_requests = review_family.build_assertion_requests(
            contract,
            first_evidence,
            review["candidate_sha"],
            review["round"],
        )
        receipt = run_base_pinned_checker(
            repository_root,
            contract=contract,
            candidate_sha=review["candidate_sha"],
            review_round=review["round"],
            review_context=review,
            all_remote_reviews=first_evidence["remote_reviews"],
            remote_findings=first_evidence["findings"],
            remote_finding_ids=review["finding_ids"],
            original_review_report_bytes=report_bytes,
            original_review_receipt=envelope,
            original_receipt_sha256=original_receipt_sha256,
            assertion_requests=assertion_requests,
            trusted_key=trusted_key,
            clock=clock,
        )
        if not hmac.compare_digest(
            receipt["seal"],
            _execution_receipt_seal(receipt, trusted_key),
        ):
            raise reporter.PilotDataError("execution receipt HMAC is invalid")
        execution_receipts.append(receipt)
    second_evidence = collect_live_evidence_bytes(
        raw_contract,
        repository_root,
        expected_remote_head,
        expected_candidate,
        review_report,
        envelope,
        execution_receipts,
        authoritative_trigger=authoritative_trigger,
        adapter=adapter,
        clock=clock,
    )
    if _live_state_digest(first_evidence_bytes) != _live_state_digest(second_evidence):
        raise reporter.PilotDataError(
            "GitHub head/review/thread state changed during gate evaluation"
        )
    result = review_family.build_report(
        raw_contract, second_evidence, repository_root, expected_candidate
    )
    result["bootstrap"] = {
        "mode": "base-pinned",
        "trusted_checker_present_in_base": True,
        "external_coordinator_review_required": False,
    }
    result["provenance"] = {
        "source": "trusted-base-live-gate",
        "authoritative": True,
        "live_authoritative": True,
        "authenticated_receipt": True,
        "base_pinned_checker": True,
        "executable_evidence_trusted": True,
        "execution_receipt_seals": [
            receipt["seal"] for receipt in execution_receipts
        ],
    }
    result["gates"] = {
        **result["gates"],
        "push_allowed": result["structural_eligibility"]["push"],
        "trusted_push_allowed": result["structural_eligibility"]["push"],
        "merge_allowed": result["structural_eligibility"]["merge"],
    }
    return result


def parse_args(argv: list[str] | None = None) -> Any:
    parser = argparse.ArgumentParser(
        description="Run the exact-base trusted live sibling-review gate."
    )
    parser.add_argument("--trusted-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--expected-base", required=True)
    parser.add_argument("--expected-candidate", required=True)
    parser.add_argument("--expected-remote-head")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--decision-record", type=Path)
    parser.add_argument("--review-receipt", type=Path)
    parser.add_argument(
        "--pre-review-state",
        choices=("new", "preserved"),
        default="new",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not sys.flags.isolated:
            raise RuntimeError("trusted gate requires /usr/bin/python3 -I")
        prepare_trusted_modules(
            args.trusted_root,
            args.candidate_root,
            args.expected_base,
        )
    except (OSError, RuntimeError) as error:
        print(f"trusted review gate error: {error}", file=sys.stderr)
        return 2
    try:
        key_id = os.environ.get("WORKFLOW_REVIEW_RECEIPT_KEY_ID")
        key_epoch_text = os.environ.get("WORKFLOW_REVIEW_RECEIPT_KEY_EPOCH")
        key = os.environ.get("WORKFLOW_REVIEW_RECEIPT_HMAC_KEY")
        replay_store = os.environ.get("WORKFLOW_REVIEW_REPLAY_STORE")
        if not key_id or not key_epoch_text or not key or not replay_store:
            raise reporter.PilotDataError(
                "trusted gate requires external key ID, epoch, HMAC key, and "
                "replay store"
            )
        try:
            key_epoch = int(key_epoch_text)
        except ValueError as error:
            raise reporter.PilotDataError(
                "receipt key epoch must be an integer"
            ) from error
        raw_contract = reporter.load_json(args.contract)
        if args.pre_review_state == "new":
            if args.review_receipt is None:
                raise reporter.PilotDataError(
                    "new pre-review state requires receipt bytes"
                )
            review_receipt_bytes = args.review_receipt.read_bytes()
        else:
            if args.review_receipt is not None:
                raise reporter.PilotDataError(
                    "preserved pre-review state must load trusted stored bytes"
                )
            review_receipt_bytes = preserved_receipt_bytes(
                Path(replay_store),
                repository=reporter.expect_string(
                    raw_contract.get("repository"), "contract.repository"
                ),
                pull_request=reporter.expect_int(
                    raw_contract.get("pull_request"),
                    "contract.pull_request",
                    1,
                ),
                base_sha=reporter.expect_sha(
                    raw_contract.get("base_sha"), "contract.base_sha"
                ),
                original_pre_review_head=reporter.expect_sha(
                    raw_contract.get("original_pre_review_head"),
                    "contract.original_pre_review_head",
                ),
                key_id=key_id,
                key_epoch=key_epoch,
            )
        result = _run_trusted_gate(
            raw_contract=raw_contract,
            repository_root=args.candidate_root,
            expected_candidate=args.expected_candidate,
            expected_remote_head=args.expected_remote_head,
            expected_base=args.expected_base,
            review_receipt_bytes=review_receipt_bytes,
            replay_store=Path(replay_store),
            trusted_key_id=key_id,
            trusted_key_epoch=key_epoch,
            trusted_key=key.encode("utf-8"),
            current_time=_utc_now(),
            pre_review_state=args.pre_review_state,
            decision_record_path=args.decision_record,
        )
    except (OSError, reporter.PilotDataError) as error:
        print(f"trusted review gate error: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(reporter.normalized_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
