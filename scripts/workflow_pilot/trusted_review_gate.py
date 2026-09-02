#!/usr/bin/env python3
"""Credentialed review gate for an exact trusted base or external install."""

from __future__ import annotations

import argparse
import ast
import base64
import copy
import hashlib
import hmac
import importlib
import importlib.abc
import json
import os
import re
import secrets
import shutil
import stat
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
ASSERTION_FILE_MODES = {"100644", "100755", "120000"}
MATERIALIZED_FILE_MODES = {"100644", "100755"}
MAX_CANDIDATE_DECISION_RECORD_BYTES = 1 << 20
ASSERTION_INPUT_PATHS = (
    DECISION_RECORD_PATH,
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
RECEIPT_DOMAIN = b"workflow-review-authenticated-envelope-v2\0"
EXECUTION_RECEIPT_DOMAIN = b"workflow-review-execution-receipt-v2\0"
RECEIPT_PURPOSE = "independent-pre-review-report"
RECEIPT_MAX_LIFETIME_SECONDS = 600
MAX_AUTHENTICATED_RECEIPT_BYTES = 1 << 20
REPLAY_IO_CHUNK_SIZE = 65536
REPLAY_TEMP_NAME_ATTEMPTS = 32
REPLAY_TEMP_TOKEN_BYTES = 8
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
EXTERNAL_DECISION_COMMENT_PREFIX = "workflow-review-family-decision:v1 "
EXTERNAL_CLASSIFICATION_COMMENT_PREFIX = (
    "workflow-review-family-classification:v1 "
)
TRUSTED_SECRET_ENV_KEYS = (
    "GH_HOST",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "WORKFLOW_REVIEW_RECEIPT_KEY_ID",
    "WORKFLOW_REVIEW_RECEIPT_KEY_EPOCH",
    "WORKFLOW_REVIEW_RECEIPT_HMAC_KEY",
    "WORKFLOW_REVIEW_REPLAY_STORE",
)

# Actor is not a Node in GitHub's schema. Every polymorphic Actor selection
# obtains id through a Node fragment; viewer is a concrete User.
GRAPHQL_QUERY = r"""
query ReviewFamilyEvidence($owner: String!, $name: String!, $number: Int!) {
  viewer { __typename id login }
  repository(owner: $owner, name: $name) {
    id
    nameWithOwner
    viewerPermission
    owner { __typename login ... on Node { id } }
    pullRequest(number: $number) {
      id
      number
      createdAt
      baseRefOid
      mergeable
      headRefOid
      author { __typename login ... on Node { id } }
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
          author { __typename login ... on Node { id } }
          comments(first: 100) {
            pageInfo { hasNextPage }
            nodes {
              id
              createdAt
              updatedAt
              body
              author { __typename login ... on Node { id } }
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
              author { __typename login ... on Node { id } }
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
          updatedAt
          body
          author { __typename login ... on Node { id } }
        }
      }
    }
  }
}
"""


reporter: Any = None
review_family: Any = None
trusted_environment: dict[str, str] = {}
RFC3339_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


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


def _receipt_scope_id(
    repository: str,
    pull_request: int,
    base_sha: str,
    original_pre_review_head: str,
    key_id: str,
    key_epoch: int,
) -> str:
    return hashlib.sha256(
        reporter.normalized_json(
            _receipt_scope(
                repository,
                pull_request,
                base_sha,
                original_pre_review_head,
                key_id,
                key_epoch,
            )
        )
    ).hexdigest()


def _receipt_final_name(scope_id: str) -> str:
    return f"original-{scope_id}"


def _receipt_temp_name(scope_id: str, token: str) -> str:
    return f".original-{scope_id}.{token}.tmp"


def _is_bounded_receipt_temp_name(name: str, scope_id: str) -> bool:
    return (
        re.fullmatch(
            rf"\.original-{scope_id}\.[0-9a-f]{{{REPLAY_TEMP_TOKEN_BYTES * 2}}}\.tmp",
            name,
        )
        is not None
    )


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
    scope_id = _receipt_scope_id(
        repository,
        pull_request,
        base_sha,
        original_pre_review_head,
        key_id,
        key_epoch,
    )
    directory_fd = -1
    try:
        directory_fd = _open_replay_store_fd(replay_store)
        payload = _read_replay_receipt_bytes(
            directory_fd,
            name=_receipt_final_name(scope_id),
            scope_id=scope_id,
            allow_missing=False,
            allow_temp_link=False,
            not_found_message="preserved original pre-review is unavailable",
            invalid_message="preserved original pre-review is unavailable",
        )
        assert payload is not None
        return payload
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


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
    if len(receipt_bytes) > MAX_AUTHENTICATED_RECEIPT_BYTES:
        raise reporter.PilotDataError(
            "authenticated pre-review receipt exceeds maximum size"
        )
    scope_id = _receipt_scope_id(
        repository,
        pull_request,
        base_sha,
        original_pre_review_head,
        key_id,
        key_epoch,
    )
    final_name = _receipt_final_name(scope_id)
    directory_fd = -1
    temp_fd = -1
    temp_name: str | None = None
    temp_stat: os.stat_result | None = None
    try:
        directory_fd = _open_replay_store_fd(replay_store)
        existing_payload = _read_replay_receipt_bytes(
            directory_fd, name=final_name, scope_id=scope_id,
            expected_bytes=receipt_bytes, allow_missing=True, allow_temp_link=True,
            not_found_message="authenticated original pre-review is unavailable",
            invalid_message="authenticated original pre-review was already consumed or re-signed",
        )
        if existing_payload is not None:
            _finalize_existing_replay_receipt(directory_fd, final_name=final_name, scope_id=scope_id, expected_bytes=receipt_bytes)
            return
        temp_fd, temp_name = _create_replay_temp_file(directory_fd, scope_id)
        _write_all_bytes(
            temp_fd,
            receipt_bytes,
            error_message="authenticated original pre-review could not be published",
        )
        _fsync_descriptor(
            temp_fd,
            error_message="authenticated original pre-review could not be published",
        )
        temp_stat = _validate_temp_receipt_file(
            temp_fd,
            receipt_bytes,
            error_message="authenticated original pre-review could not be published",
        )
        try:
            _publish_replay_temp(
                temp_fd,
                directory_fd,
                final_name,
                error_message="authenticated original pre-review could not be published",
            )
        except FileExistsError:
            winner_payload = _read_replay_receipt_bytes(
                directory_fd, name=final_name, scope_id=scope_id,
                expected_bytes=receipt_bytes, allow_missing=True, allow_temp_link=True,
                not_found_message="authenticated original pre-review is unavailable",
                invalid_message="authenticated original pre-review was already consumed or re-signed",
            )
            if winner_payload is None:
                raise reporter.PilotDataError(
                    "authenticated original pre-review could not be published"
                )
            _finalize_existing_replay_receipt(directory_fd, final_name=final_name, scope_id=scope_id, expected_bytes=receipt_bytes)
            _unlink_if_same_inode(
                directory_fd, temp_name, temp_stat,
                final_name=final_name,
                scope_id=scope_id,
                expected_bytes=receipt_bytes,
                invalid_message="authenticated original pre-review could not be published",
            )
            _fsync_descriptor(
                directory_fd,
                error_message="authenticated original pre-review could not be published",
            )
            return
        _fsync_descriptor(
            directory_fd,
            error_message="authenticated original pre-review could not be published",
        )
        _unlink_if_same_inode(
            directory_fd, temp_name, temp_stat,
            final_name=final_name,
            scope_id=scope_id,
            expected_bytes=receipt_bytes,
            invalid_message="authenticated original pre-review could not be published",
        )
        _fsync_descriptor(
            directory_fd,
            error_message="authenticated original pre-review could not be published",
        )
    finally:
        cleanup_stat = temp_stat
        if cleanup_stat is None and temp_fd >= 0:
            try:
                cleanup_stat = os.fstat(temp_fd)
            except OSError:
                cleanup_stat = None
        if temp_name is not None and cleanup_stat is not None:
            _unlink_if_same_inode(directory_fd, temp_name, cleanup_stat)
        if temp_fd >= 0:
            os.close(temp_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


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


def _capture_trusted_environment() -> None:
    trusted_environment.clear()
    for key in TRUSTED_SECRET_ENV_KEYS:
        value = os.environ.pop(key, None)
        if value is not None:
            trusted_environment[key] = value


def _python_source_from_git(trusted_root: Path, expected_base: str, relative: str) -> str:
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
        raise RuntimeError(f"trusted Python source is not an exact base-tree entry: {relative}")
    metadata, raw_path = records[0].split(b"\t", 1)
    mode, kind, blob_oid = metadata.decode("ascii").split()
    if raw_path.decode("utf-8") != relative or mode not in {"100644", "100755"} or kind != "blob":
        raise RuntimeError(f"trusted Python source has an unsafe tree entry: {relative}")
    try:
        return _minimal_git(trusted_root, "cat-file", "blob", blob_oid).decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"trusted Python source cannot be decoded: {relative}") from error


class _TrustedModuleLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, modules: dict[str, dict[str, Any]]):
        self.modules = modules
    def find_spec(self, fullname, path=None, target=None):
        record = self.modules.get(fullname)
        if record is None:
            return None
        return importlib.util.spec_from_loader(
            fullname, self, origin=record["origin"], is_package=record["is_package"]
        )
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        record = self.modules[module.__spec__.name]
        module.__file__ = record["origin"]
        module.__loader__ = self
        if record["is_package"]:
            module.__path__ = []
        if record["source"] is not None:
            exec(compile(record["source"], record["origin"], "exec", dont_inherit=True), module.__dict__)


def _bind_trusted_modules(
    trusted_root: Path,
    candidate_root: Path,
    expected_base: str,
) -> None:
    """Bind imports only after proving the trusted installation boundary."""
    global reporter, review_family

    trusted_root = trusted_root.resolve()
    candidate_root = candidate_root.resolve()
    _capture_trusted_environment()
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
    object_format = _minimal_git(
        trusted_root, "rev-parse", "--show-object-format"
    ).decode("ascii").strip()
    if object_format != "sha1":
        raise RuntimeError(
            f"trusted checkout object format {object_format!r} is not supported; exact Git object IDs require sha1"
        )
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
    verified_sources = {}
    while pending_paths:
        relative = pending_paths.pop()
        if relative in verified_paths:
            continue
        source = _python_source_from_git(trusted_root, expected_base, relative)
        verified_sources[relative] = source
        try:
            syntax = ast.parse(source, filename=relative)
        except SyntaxError as error:
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
    modules = {"scripts": {"source": None, "origin": str(trusted_root / "scripts"), "is_package": True}}
    for relative, source in verified_sources.items():
        if relative.endswith("/__init__.py"):
            module_name = relative[: -len("/__init__.py")].replace("/", ".")
            is_package = True
        elif relative.endswith(".py"):
            module_name = relative[:-3].replace("/", ".")
            is_package = False
        else:
            raise RuntimeError(f"trusted Python source has an invalid module path: {relative}")
        modules[module_name] = {
            "source": source,
            "origin": str(trusted_root / relative),
            "is_package": is_package,
        }
    loader = _TrustedModuleLoader(modules)
    previous = sys.dont_write_bytecode
    saved_meta_path = list(sys.meta_path)
    try:
        sys.dont_write_bytecode = True
        sys.meta_path.insert(0, loader)
        reporter = importlib.import_module("scripts.workflow_pilot.reporter")
        review_family = importlib.import_module("scripts.workflow_pilot.review_family")
    finally:
        sys.dont_write_bytecode = previous
        sys.meta_path[:] = saved_meta_path
    loaded_paths = {
        Path(reporter.__spec__.origin),
        Path(review_family.__spec__.origin),
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
    if len(receipt_bytes) > MAX_AUTHENTICATED_RECEIPT_BYTES:
        raise reporter.PilotDataError(
            "authenticated pre-review receipt exceeds maximum size"
        )
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
        if replay_store is None:
            raise reporter.PilotDataError(
                "authenticated pre-review requires external replay authority"
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


def _assertion_input_state(
    repository_root: Path, revision: str, path: str
) -> dict[str, str | None]:
    raw = reporter.run_git(
        repository_root,
        "ls-tree",
        "-z",
        "--full-tree",
        revision,
        "--",
        path,
    )
    records = [record for record in raw.split(b"\0") if record]
    if not records:
        return {"mode": None, "blob_oid": None}
    if len(records) != 1:
        raise reporter.PilotDataError(
            f"Git tree returned ambiguous assertion input path {path!r}"
        )
    try:
        metadata, raw_path = records[0].split(b"\t", 1)
        mode, kind, blob_oid = metadata.decode("ascii").split()
        actual_path = raw_path.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise reporter.PilotDataError(
            f"Git tree returned malformed assertion input path {path!r}"
        ) from error
    if actual_path != path or kind != "blob" or mode not in ASSERTION_FILE_MODES:
        raise reporter.PilotDataError(
            f"Git tree assertion input path {path!r} has an unsafe type or mode"
        )
    return {"mode": mode, "blob_oid": blob_oid}


def _git_blob_oid(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def _same_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _file_open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _replay_temp_open_flags() -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _open_directory_fd(
    path: str | os.PathLike[str],
    *,
    dir_fd: int | None,
    label: str,
    owner_uid: int,
) -> tuple[int, os.stat_result]:
    try:
        if dir_fd is None:
            descriptor = os.open(path, _directory_open_flags())
        else:
            descriptor = os.open(path, _directory_open_flags(), dir_fd=dir_fd)
    except OSError as error:
        raise reporter.PilotDataError(f"{label} is unavailable for drift validation") from error
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        os.close(descriptor)
        raise reporter.PilotDataError(f"{label} is unavailable for drift validation") from error
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner_uid:
        os.close(descriptor)
        raise reporter.PilotDataError(f"{label} is unavailable for drift validation")
    return descriptor, metadata


def _open_replay_store_fd(replay_store: Path) -> int:
    message = "authenticated pre-review replay store is unavailable"
    replay_store = Path(replay_store)
    parts = [
        part
        for part in replay_store.parts
        if part not in (os.path.sep, "", ".")
    ]
    if any(part == ".." for part in parts):
        raise reporter.PilotDataError(message)
    current_fd = -1
    root = os.path.sep if replay_store.is_absolute() else "."
    try:
        current_fd = os.open(root, _directory_open_flags())
        metadata = os.fstat(current_fd)
        if not stat.S_ISDIR(metadata.st_mode):
            raise reporter.PilotDataError(message)
        for part in parts:
            next_fd = os.open(part, _directory_open_flags(), dir_fd=current_fd)
            next_metadata = os.fstat(next_fd)
            if not stat.S_ISDIR(next_metadata.st_mode):
                os.close(next_fd)
                raise reporter.PilotDataError(message)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except OSError as error:
        if current_fd >= 0:
            os.close(current_fd)
        raise reporter.PilotDataError(message) from error
    except Exception:
        if current_fd >= 0:
            os.close(current_fd)
        raise


def _read_exact_bytes(
    descriptor: int,
    *,
    size: int,
    error_message: str,
) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        try:
            chunk = os.read(descriptor, min(REPLAY_IO_CHUNK_SIZE, remaining))
        except InterruptedError:
            continue
        except OSError as error:
            raise reporter.PilotDataError(error_message) from error
        if not chunk:
            raise reporter.PilotDataError(error_message)
        chunks.append(chunk)
        remaining -= len(chunk)
    while True:
        try:
            trailing = os.read(descriptor, 1)
            break
        except InterruptedError:
            continue
        except OSError as error:
            raise reporter.PilotDataError(error_message) from error
    if trailing:
        raise reporter.PilotDataError(error_message)
    return b"".join(chunks)


def _write_all_bytes(
    descriptor: int,
    payload: bytes,
    *,
    error_message: str,
) -> None:
    view = memoryview(payload)
    written_total = 0
    while written_total < len(payload):
        try:
            written = os.write(descriptor, view[written_total:])
        except InterruptedError:
            continue
        except OSError as error:
            raise reporter.PilotDataError(error_message) from error
        if written <= 0:
            raise reporter.PilotDataError(error_message)
        written_total += written


def _fsync_descriptor(descriptor: int, *, error_message: str) -> None:
    try:
        os.fsync(descriptor)
    except OSError as error:
        raise reporter.PilotDataError(error_message) from error


def _same_inode_entries(
    directory_fd: int,
    metadata: os.stat_result,
    *,
    error_message: str,
) -> list[str]:
    try:
        entries = os.listdir(f"/proc/self/fd/{directory_fd}")
    except OSError as error:
        raise reporter.PilotDataError(error_message) from error
    matches = []
    for entry in entries:
        try:
            current = os.stat(entry, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise reporter.PilotDataError(error_message) from error
        if _same_inode(current, metadata):
            matches.append(entry)
    return matches


def _validate_replay_file_topology(
    directory_fd: int,
    *,
    name: str,
    scope_id: str,
    metadata: os.stat_result,
    allow_temp_link: bool,
    error_message: str,
) -> list[str]:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise reporter.PilotDataError(error_message)
    aliases = _same_inode_entries(
        directory_fd,
        metadata,
        error_message=error_message,
    )
    if name not in aliases or len(aliases) != metadata.st_nlink:
        raise reporter.PilotDataError(error_message)
    extras = [entry for entry in aliases if entry != name]
    if not extras:
        if metadata.st_nlink != 1:
            raise reporter.PilotDataError(error_message)
        return []
    if (
        not allow_temp_link
        or metadata.st_nlink != 2
        or len(extras) != 1
        or not _is_bounded_receipt_temp_name(extras[0], scope_id)
    ):
        raise reporter.PilotDataError(error_message)
    return extras


def _read_replay_receipt_bytes(
    directory_fd: int,
    *,
    name: str,
    scope_id: str,
    expected_bytes: bytes | None = None,
    allow_missing: bool,
    allow_temp_link: bool,
    not_found_message: str,
    invalid_message: str,
) -> bytes | None:
    descriptor = -1
    try:
        try:
            pre_open = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError as error:
            if allow_missing:
                return None
            raise reporter.PilotDataError(not_found_message) from error
        except OSError as error:
            raise reporter.PilotDataError(invalid_message) from error
        try:
            descriptor = os.open(name, _file_open_flags(), dir_fd=directory_fd)
            opened = os.fstat(descriptor)
        except FileNotFoundError as error:
            if allow_missing:
                return None
            raise reporter.PilotDataError(not_found_message) from error
        except OSError as error:
            raise reporter.PilotDataError(invalid_message) from error
        if (
            not _same_inode(pre_open, opened)
            or opened.st_size > MAX_AUTHENTICATED_RECEIPT_BYTES
        ):
            raise reporter.PilotDataError(invalid_message)
        _validate_replay_file_topology(
            directory_fd,
            name=name,
            scope_id=scope_id,
            metadata=opened,
            allow_temp_link=allow_temp_link,
            error_message=invalid_message,
        )
        if expected_bytes is not None and opened.st_size != len(expected_bytes):
            raise reporter.PilotDataError(invalid_message)
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
        except OSError as error:
            raise reporter.PilotDataError(invalid_message) from error
        payload = _read_exact_bytes(
            descriptor,
            size=opened.st_size,
            error_message=invalid_message,
        )
        if expected_bytes is not None and not hmac.compare_digest(
            payload, expected_bytes
        ):
            raise reporter.PilotDataError(invalid_message)
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _create_replay_temp_file(directory_fd: int, scope_id: str) -> tuple[int, str]:
    error_message = "authenticated original pre-review could not be published"
    for _attempt in range(REPLAY_TEMP_NAME_ATTEMPTS):
        name = _receipt_temp_name(
            scope_id,
            secrets.token_hex(REPLAY_TEMP_TOKEN_BYTES),
        )
        try:
            descriptor = os.open(
                name,
                _replay_temp_open_flags(),
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        except OSError as error:
            raise reporter.PilotDataError(error_message) from error
        try:
            metadata = os.fstat(descriptor)
        except OSError as error:
            os.close(descriptor)
            raise reporter.PilotDataError(error_message) from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size != 0
        ):
            os.close(descriptor)
            raise reporter.PilotDataError(error_message)
        return descriptor, name
    raise reporter.PilotDataError(error_message)


def _validate_temp_receipt_file(
    descriptor: int,
    expected_bytes: bytes,
    *,
    error_message: str,
) -> os.stat_result:
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise reporter.PilotDataError(error_message) from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size != len(expected_bytes)
    ):
        raise reporter.PilotDataError(error_message)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as error:
        raise reporter.PilotDataError(error_message) from error
    payload = _read_exact_bytes(
        descriptor,
        size=len(expected_bytes),
        error_message=error_message,
    )
    if not hmac.compare_digest(payload, expected_bytes):
        raise reporter.PilotDataError(error_message)
    return metadata


def _publish_replay_temp(
    descriptor: int,
    directory_fd: int,
    final_name: str,
    *,
    error_message: str,
) -> None:
    try:
        os.link(
            f"/proc/self/fd/{descriptor}",
            final_name,
            dst_dir_fd=directory_fd,
        )
    except FileExistsError:
        raise
    except OSError as error:
        raise reporter.PilotDataError(error_message) from error


def _finalize_existing_replay_receipt(
    directory_fd: int,
    *,
    final_name: str,
    scope_id: str,
    expected_bytes: bytes,
) -> None:
    try:
        metadata = os.stat(final_name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as error:
        raise reporter.PilotDataError(
            "authenticated original pre-review was already consumed or re-signed"
        ) from error
    aliases = _validate_replay_file_topology(
        directory_fd,
        name=final_name,
        scope_id=scope_id,
        metadata=metadata,
        allow_temp_link=True,
        error_message="authenticated original pre-review was already consumed or re-signed",
    )
    if aliases:
        _unlink_if_same_inode(
            directory_fd, aliases[0], metadata,
            final_name=final_name,
            scope_id=scope_id,
            expected_bytes=expected_bytes,
            invalid_message="authenticated original pre-review could not be published",
        )
        _fsync_descriptor(
            directory_fd,
            error_message="authenticated original pre-review could not be published",
        )
    if _read_replay_receipt_bytes(
        directory_fd, name=final_name, scope_id=scope_id,
        expected_bytes=expected_bytes, allow_missing=False, allow_temp_link=False,
        not_found_message="authenticated original pre-review was already consumed or re-signed",
        invalid_message="authenticated original pre-review was already consumed or re-signed",
    ) is None:
        raise reporter.PilotDataError(
            "authenticated original pre-review was already consumed or re-signed"
        )


def _unlink_if_same_inode(
    directory_fd: int,
    name: str,
    metadata: os.stat_result,
    *,
    final_name: str | None = None,
    scope_id: str | None = None,
    expected_bytes: bytes | None = None,
    invalid_message: str = "authenticated original pre-review could not be published",
) -> None:
    if directory_fd < 0:
        return
    try:
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if final_name is not None and scope_id is not None and expected_bytes is not None:
            if _read_replay_receipt_bytes(
                directory_fd, name=final_name, scope_id=scope_id,
                expected_bytes=expected_bytes, allow_missing=False, allow_temp_link=False,
                not_found_message=invalid_message,
                invalid_message=invalid_message,
            ) is None:
                raise reporter.PilotDataError(invalid_message)
        return
    except OSError as error:
        raise reporter.PilotDataError(invalid_message) from error
    if not _same_inode(current, metadata):
        return
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        if final_name is not None and scope_id is not None and expected_bytes is not None:
            if _read_replay_receipt_bytes(
                directory_fd, name=final_name, scope_id=scope_id,
                expected_bytes=expected_bytes, allow_missing=False, allow_temp_link=False,
                not_found_message=invalid_message,
                invalid_message=invalid_message,
            ) is None:
                raise reporter.PilotDataError(invalid_message)
    except OSError as error:
        raise reporter.PilotDataError(invalid_message) from error


def _relative_no_follow_stat(
    name: str,
    *,
    dir_fd: int,
    label: str,
) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as error:
        raise reporter.PilotDataError(f"{label} is unavailable for drift validation") from error


def _ensure_current_path_matches_pinned(
    repository_root: Path,
    relative: Path,
    *,
    root_stat: os.stat_result,
    parent_stats: list[os.stat_result],
    leaf_stat: os.stat_result,
) -> None:
    label = "candidate decision record"
    try:
        current_root = os.lstat(repository_root)
    except OSError as error:
        raise reporter.PilotDataError(f"{label} changed during no-follow access") from error
    if (
        stat.S_ISLNK(current_root.st_mode)
        or not stat.S_ISDIR(current_root.st_mode)
        or not _same_inode(current_root, root_stat)
    ):
        raise reporter.PilotDataError(f"{label} changed during no-follow access")
    current = repository_root
    for part, pinned in zip(relative.parts[:-1], parent_stats):
        current = current / part
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise reporter.PilotDataError(f"{label} changed during no-follow access") from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or not _same_inode(metadata, pinned)
        ):
            raise reporter.PilotDataError(f"{label} changed during no-follow access")
    current = repository_root.joinpath(*relative.parts)
    try:
        metadata = os.lstat(current)
    except OSError as error:
        raise reporter.PilotDataError(f"{label} changed during no-follow access") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or not _same_inode(metadata, leaf_stat)
    ):
        raise reporter.PilotDataError(f"{label} changed during no-follow access")


def _read_candidate_decision_record_bytes(
    repository_root: Path,
    *,
    expected_blob_oid: str,
) -> bytes:
    label = "candidate decision record"
    relative = Path(DECISION_RECORD_PATH)
    if relative.is_absolute() or ".." in relative.parts:
        raise reporter.PilotDataError(
            f"{label} path escapes repository root"
        )
    root_fd = -1
    parent_fds: list[int] = []
    leaf_fd = -1
    try:
        try:
            root_lstat = os.lstat(repository_root)
        except OSError as error:
            raise reporter.PilotDataError(
                f"{label} is unavailable for drift validation"
            ) from error
        root_fd, root_stat = _open_directory_fd(
            repository_root,
            dir_fd=None,
            label=label,
            owner_uid=root_lstat.st_uid,
        )
        if not _same_inode(root_lstat, root_stat):
            raise reporter.PilotDataError(
                f"{label} changed during no-follow access"
            )
        owner_uid = root_stat.st_uid
        current_fd = root_fd
        parent_stats: list[os.stat_result] = []
        for part in relative.parts[:-1]:
            parent_fd, parent_stat = _open_directory_fd(
                part,
                dir_fd=current_fd,
                label=label,
                owner_uid=owner_uid,
            )
            parent_fds.append(parent_fd)
            parent_stats.append(parent_stat)
            current_fd = parent_fd
        leaf_name = relative.parts[-1]
        pre_leaf_stat = _relative_no_follow_stat(
            leaf_name,
            dir_fd=current_fd,
            label=label,
        )
        if (
            not stat.S_ISREG(pre_leaf_stat.st_mode)
            or pre_leaf_stat.st_uid != owner_uid
            or pre_leaf_stat.st_size > MAX_CANDIDATE_DECISION_RECORD_BYTES
        ):
            raise reporter.PilotDataError(
                f"{label} is unavailable for drift validation"
            )
        try:
            leaf_fd = os.open(leaf_name, _file_open_flags(), dir_fd=current_fd)
            opened_stat = os.fstat(leaf_fd)
        except OSError as error:
            raise reporter.PilotDataError(
                f"{label} is unavailable for drift validation"
            ) from error
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_uid != owner_uid
            or opened_stat.st_size > MAX_CANDIDATE_DECISION_RECORD_BYTES
        ):
            raise reporter.PilotDataError(
                f"{label} is unavailable for drift validation"
            )
        if not _same_inode(opened_stat, pre_leaf_stat):
            raise reporter.PilotDataError(
                f"{label} changed during no-follow access"
            )
        chunks = []
        while True:
            chunk = os.read(leaf_fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        if leaf_fd >= 0:
            os.close(leaf_fd)
        for descriptor in reversed(parent_fds):
            os.close(descriptor)
        if root_fd >= 0:
            os.close(root_fd)
    payload = b"".join(chunks)
    if _git_blob_oid(payload) != expected_blob_oid:
        raise reporter.PilotDataError(
            f"{label} differs from the exact candidate tree blob"
        )
    _ensure_current_path_matches_pinned(
        repository_root,
        relative,
        root_stat=root_stat,
        parent_stats=parent_stats,
        leaf_stat=opened_stat,
    )
    return payload


def _base_contains_gate(repository_root: Path, base_sha: str) -> bool:
    for path in TRUSTED_REQUIRED_PATHS:
        try:
            state = _assertion_input_state(repository_root, base_sha, path)
        except reporter.PilotDataError:
            return False
        if state["mode"] not in MATERIALIZED_FILE_MODES:
            return False
    for path in ASSERTION_INPUT_PATHS:
        try:
            state = _assertion_input_state(repository_root, base_sha, path)
        except reporter.PilotDataError:
            return False
        if state["mode"] is None and path.endswith("/__init__.py"):
            continue
        if state["mode"] not in MATERIALIZED_FILE_MODES:
            return False
    return True


def _normalize_trigger_decision_record(
    record: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    reporter.expect_keys(
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
    number = reporter.expect_int(
        record["pull_request"], f"{label}.pull_request", 1
    )
    threshold = reporter.expect_object(record["threshold"], f"{label}.threshold")
    reporter.expect_keys(
        threshold,
        f"{label}.threshold",
        ("triggers", "override_history"),
    )
    override_history = []
    for index, raw_override in enumerate(
        reporter.expect_list(
            threshold["override_history"],
            f"{label}.threshold.override_history",
        )
    ):
        override_label = f"{label}.threshold.override_history[{index}]"
        override = reporter.expect_object(raw_override, override_label)
        reporter.expect_keys(override, override_label, ("enabled", "reason"))
        override_history.append(
            {
                "enabled": reporter.expect_bool(
                    override["enabled"], f"{override_label}.enabled"
                ),
                "reason": reporter.expect_string(
                    override["reason"], f"{override_label}.reason"
                ),
            }
        )
    stack = reporter.expect_object(record["stack"], f"{label}.stack")
    reporter.expect_keys(
        stack,
        f"{label}.stack",
        ("depth", "parent_pr", "exception_reason"),
    )
    parent_pr = stack["parent_pr"]
    if parent_pr is not None:
        parent_pr = reporter.expect_int(
            parent_pr, f"{label}.stack.parent_pr", 1
        )
    exception_reason = stack["exception_reason"]
    if exception_reason is not None:
        exception_reason = reporter.expect_string(
            exception_reason, f"{label}.stack.exception_reason"
        )
    pilot = reporter.expect_object(record["pilot"], f"{label}.pilot")
    reporter.expect_keys(pilot, f"{label}.pilot", ("included", "disposition"))
    trigger = review_family.normalize_trigger_fields(
        record["risk_boundaries"],
        threshold["triggers"],
        label=label,
    )
    return {
        "pull_request": number,
        "trigger": trigger,
        "pre_review_required": review_family.trigger_requires_pre_review(trigger),
        "threshold_override_history": override_history,
        "gate_mode": reporter.expect_enum(
            record["gate_mode"], reporter.GATE_MODES, f"{label}.gate_mode"
        ),
        "stack": {
            "depth": reporter.expect_int(
                stack["depth"], f"{label}.stack.depth", 0
            ),
            "parent_pr": parent_pr,
            "exception_reason": exception_reason,
        },
        "pilot": {
            "included": reporter.expect_bool(
                pilot["included"], f"{label}.pilot.included"
            ),
            "disposition": reporter.expect_enum(
                pilot["disposition"],
                reporter.PILOT_DISPOSITIONS,
                f"{label}.pilot.disposition",
            ),
        },
    }


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
        normalized_record = _normalize_trigger_decision_record(
            record, label=record_label
        )
        number = normalized_record["pull_request"]
        if number in seen:
            raise reporter.PilotDataError(
                f"{label} repeats PR {number}"
            )
        seen.add(number)
        if number != pull_request:
            continue
        match = normalized_record
    return match, bool(records)


def _load_base_trigger_record(
    repository_root: Path,
    *,
    base_sha: str,
    pull_request: int,
) -> tuple[dict[str, Any] | None, str]:
    decisions = reporter.load_decisions_from_commit(repository_root, base_sha)
    record, _ = _parse_trigger_decision_records(
        decisions,
        pull_request=pull_request,
        label=f"decision record at commit {base_sha}",
    )
    state = _assertion_input_state(repository_root, base_sha, DECISION_RECORD_PATH)
    if state["mode"] not in MATERIALIZED_FILE_MODES or state["blob_oid"] is None:
        raise reporter.PilotDataError(
            "authoritative trigger decision record has an unsafe type or mode"
        )
    return record, state["blob_oid"]


def _load_candidate_trigger_record(
    repository_root: Path,
    *,
    candidate_sha: str,
    pull_request: int,
) -> tuple[dict[str, Any], str]:
    state = _assertion_input_state(repository_root, candidate_sha, DECISION_RECORD_PATH)
    if state["mode"] is None:
        raise reporter.PilotDataError(
            "candidate decision record is unavailable for drift validation"
        )
    if state["mode"] not in MATERIALIZED_FILE_MODES or state["blob_oid"] is None:
        raise reporter.PilotDataError(
            "candidate decision record has an unsafe type or mode"
        )
    try:
        raw = _read_candidate_decision_record_bytes(
            repository_root,
            expected_blob_oid=state["blob_oid"],
        ).decode("utf-8")
    except UnicodeDecodeError as error:
        raise reporter.PilotDataError(
            "candidate decision record is not valid UTF-8"
        ) from error
    candidate_decisions = reporter.expect_object(
        reporter.parse_json(raw, str(repository_root / DECISION_RECORD_PATH)),
        "candidate trigger decisions",
    )
    record, _ = _parse_trigger_decision_records(
        candidate_decisions,
        pull_request=pull_request,
        label="candidate trigger decisions",
    )
    if record is None:
        raise reporter.PilotDataError(
            "candidate decision record does not contain the exact contract PR"
        )
    return record, state["blob_oid"]


def _parse_external_trigger_comment(
    comments: list[Any],
    *,
    viewer: dict[str, Any],
    repository_id: str | None,
    repository_name: str | None,
    contract: dict[str, Any],
    initial_remote_head: str,
    first_remote_review_at: datetime | None,
    actors: list[dict[str, Any]],
) -> dict[str, Any] | None:
    matches = []
    for index, raw in enumerate(comments):
        label = f"GitHub pull-request comment[{index}]"
        comment, body = _comment_object_and_body(raw, label)
        if not body.startswith(EXTERNAL_DECISION_COMMENT_PREFIX):
            continue
        created = _require_unedited_comment(comment, label)
        _require_exact_viewer_comment_author(
            comment,
            viewer=viewer,
            actors=actors,
            label=label,
        )
        if first_remote_review_at is not None and created >= first_remote_review_at:
            raise reporter.PilotDataError(
                f"{label} decision preregistration is not before first remote review"
            )
        payload = _canonical_prefixed_comment_payload(
            body,
            EXTERNAL_DECISION_COMMENT_PREFIX,
            f"{label} decision preregistration",
        )
        reporter.expect_keys(
            payload,
            f"{label} decision preregistration",
            (
                "repository_id",
                "repository",
                "pull_request",
                "base_sha",
                "original_pre_review_head",
                "candidate_sha",
                "decision",
            ),
        )
        if repository_id is None or repository_name is None:
            raise reporter.PilotDataError(
                f"{label} decision preregistration lacks repository authority context"
            )
        if payload["repository_id"] != repository_id or payload["repository"] != repository_name:
            raise reporter.PilotDataError(
                f"{label} decision preregistration does not bind the exact repository"
            )
        if (
            reporter.expect_int(
                payload["pull_request"],
                f"{label} decision preregistration.pull_request",
                1,
            )
            != contract["pull_request"]
        ):
            raise reporter.PilotDataError(
                f"{label} decision preregistration does not bind the exact PR"
            )
        if (
            reporter.expect_sha(
                payload["base_sha"],
                f"{label} decision preregistration.base_sha",
            )
            != contract["base_sha"]
        ):
            raise reporter.PilotDataError(
                f"{label} decision preregistration does not bind the exact base"
            )
        original_head = reporter.expect_sha(
            payload["original_pre_review_head"],
            f"{label} decision preregistration.original_pre_review_head",
        )
        preregistered_head = reporter.expect_sha(
            payload["candidate_sha"],
            f"{label} decision preregistration.candidate_sha",
        )
        if original_head != contract["original_pre_review_head"]:
            raise reporter.PilotDataError(f"{label} decision preregistration lost the initial reviewed head")
        if preregistered_head != initial_remote_head:
            raise reporter.PilotDataError(
                f"{label} decision preregistration does not bind the exact initial remote review head"
            )
        decision = _normalize_trigger_decision_record(
            reporter.expect_object(
                payload["decision"], f"{label} decision preregistration.decision"
            ),
            label=f"{label} decision preregistration.decision",
        )
        if decision["pull_request"] != contract["pull_request"]:
            raise reporter.PilotDataError(
                f"{label} decision preregistration does not bind the exact PR decision"
            )
        matches.append(
            {
                "authority_kind": "external-comment",
                "path": None,
                "blob_oid": None,
                "comment_id": reporter.expect_string(comment["id"], f"{label}.id"),
                "comment_created_at": reporter.expect_string(
                    comment["createdAt"], f"{label}.createdAt"
                ),
                "pull_request": contract["pull_request"],
                "base_sha": contract["base_sha"],
                "original_pre_review_head": contract["original_pre_review_head"],
                "candidate_sha": preregistered_head,
                "risk_boundaries": decision["trigger"]["risk_boundaries"],
                "threshold_triggers": decision["trigger"]["threshold_triggers"],
                "pre_review_required": review_family.trigger_requires_pre_review(
                    decision["trigger"]
                ),
                "_record": decision,
            }
        )
    if len(matches) > 1:
        raise reporter.PilotDataError(
            "external decision preregistration comments are not unique"
        )
    return matches[0] if matches else None


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
        authoritative, blob_oid = _load_base_trigger_record(
            root,
            base_sha=contract["base_sha"],
            pull_request=contract["pull_request"],
        )
    except reporter.PilotDataError as error:
        raise reporter.PilotDataError(
            "authoritative trigger decision record is unavailable from the exact base commit"
        ) from error
    if authoritative is None:
        return None
    if authoritative["trigger"] != contract["trigger"]:
        raise reporter.PilotDataError(
            "candidate trigger does not match the authoritative decision record"
        )
    candidate_record, _candidate_blob_oid = _load_candidate_trigger_record(
        root,
        candidate_sha=candidate_sha,
        pull_request=contract["pull_request"],
    )
    if candidate_record != authoritative:
        raise reporter.PilotDataError(
            "candidate decision record drifts from the authoritative base decision"
        )
    return {
        "authority_kind": "base-record",
        "path": DECISION_RECORD_PATH,
        "blob_oid": blob_oid,
        "comment_id": None,
        "comment_created_at": None,
        "pull_request": contract["pull_request"],
        "base_sha": contract["base_sha"],
        "original_pre_review_head": contract["original_pre_review_head"],
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
    execution_receipts: list[dict[str, Any]],
    local_remediation_receipt: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    result = [
        result
        for receipt in execution_receipts
        for result in receipt["assertion_results"]
    ]
    if local_remediation_receipt is not None:
        result.extend(local_remediation_receipt["assertion_results"])
    return result


def build_live_evidence_payload(
    *,
    contract: dict[str, Any],
    expected_candidate: str,
    source_kind: str,
    captured_at: str,
    original_receipt_sha256: str,
    pull_request: dict[str, Any],
    authoritative_trigger: dict[str, Any] | None,
    actors: list[dict[str, Any]],
    pre_reviews: list[dict[str, Any]],
    pre_review_findings: list[dict[str, Any]],
    remote_reviews: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    threads: list[dict[str, Any]],
    force_push_events: list[dict[str, Any]],
    architecture_dispositions: list[dict[str, Any]],
    execution_receipts: list[dict[str, Any]],
    local_remediation_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _expect_bound_modules()
    payload = {
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
        "result_manifest": build_result_manifest(
            execution_receipts, local_remediation_receipt
        ),
    }
    if local_remediation_receipt is not None:
        payload["local_remediation_receipt"] = local_remediation_receipt
    return payload


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
    captured_github_payload: dict[str, Any],
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
    review_head_sha = reporter.expect_sha(
        review_context.get("candidate_sha"),
        "base checker review context candidate_sha",
    )
    try:
        reporter.run_git(root, "merge-base", "--is-ancestor", base_sha, candidate_sha)
    except reporter.PilotDataError as error:
        raise reporter.PilotDataError(
            "base checker candidate is not descended from the authoritative base"
        ) from error
    try:
        reporter.run_git(
            root,
            "merge-base",
            "--is-ancestor",
            contract["original_pre_review_head"],
            current_head,
        )
    except reporter.PilotDataError as error:
        raise reporter.PilotDataError(
            "base checker current head is not descended from the original pre-review head"
        ) from error
    base_tree = _git_text(root, "rev-parse", f"{base_sha}^{{tree}}")
    review_head_tree = _git_text(root, "rev-parse", f"{review_head_sha}^{{tree}}")
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
        review_head_sha
        if review_head_sha != candidate_sha
        else base_sha
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
        "captured_github_payload": captured_github_payload,
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
    base_root = sandbox / "base"
    origin_root = sandbox / "origin"
    head_root = sandbox / "head"
    probe_root = sandbox / ".assertion-probes"
    base_root.mkdir()
    origin_root.mkdir()
    head_root.mkdir()
    probe_root.mkdir(mode=0o700)
    assertion_input_artifacts = []
    for relative in ASSERTION_INPUT_PATHS:
        base_state = _assertion_input_state(root, base_sha, relative)
        origin_state = _assertion_input_state(root, finding_origin_sha, relative)
        head_state = _assertion_input_state(root, candidate_sha, relative)
        for root_path, revision, state in (
            (base_root, base_sha, base_state),
            (origin_root, finding_origin_sha, origin_state),
            (head_root, candidate_sha, head_state),
        ):
            if state["mode"] not in MATERIALIZED_FILE_MODES:
                continue
            target = root_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(reporter.run_git(root, "show", f"{revision}:{relative}"))
            target.chmod(0o444)
        assertion_input_artifacts.append(
            {
                "path": relative,
                "base_mode": base_state["mode"],
                "base_blob_oid": base_state["blob_oid"],
                "origin_mode": origin_state["mode"],
                "origin_blob_oid": origin_state["blob_oid"],
                "head_mode": head_state["mode"],
                "head_blob_oid": head_state["blob_oid"],
            }
        )
    checker_path.write_bytes(checker_source)
    assertion_program_path.write_bytes(assertion_program_source)
    checker_input["assertion_program_path"] = str(assertion_program_path)
    checker_input["assertion_program_blob_oid"] = assertion_program_blob
    checker_input["assertion_program_argv"] = list(ASSERTION_PROGRAM_ARGV)
    checker_input["finding_origin_sha"] = finding_origin_sha
    checker_input["finding_origin_tree"] = finding_origin_tree
    checker_input["base_root"] = str(base_root)
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
            for root_path in (base_root, origin_root, head_root)
            for target in root_path.rglob("*")
            for path in ([target] if target.is_dir() else [])
        },
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        directory.chmod(0o555)
    base_root.chmod(0o555)
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
                for root_path in (base_root, origin_root, head_root)
                for path in (root_path, *root_path.rglob("*"))
            )
        )
    finally:
        sandbox.chmod(0o700)
        for root_path in (base_root, origin_root, head_root):
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
        "review_head_sha": review_head_sha,
        "review_head_tree": review_head_tree,
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
        "result": (
            "hold"
            if parsed_output
            and any(
                reporter.expect_object(
                    item, "base checker output.results[]"
                )["status"]
                == "hold"
                for item in parsed_output["results"]
            )
            else "pass"
            if completed.returncode == 0
            else "fail"
        ),
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
            for key, value in trusted_environment.items()
            if key in {"GH_HOST", "GH_TOKEN", "GITHUB_TOKEN", "HOME"}
        }
        if "HOME" not in environment and "HOME" in os.environ:
            environment["HOME"] = os.environ["HOME"]
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


class _RecordingAdapter:
    def __init__(self, delegate):
        self._delegate = delegate
        self.last_payload = None

    def fetch(self, repository: str, pull_request: int) -> dict[str, Any]:
        payload = self._delegate.fetch(repository, pull_request)
        self.last_payload = copy.deepcopy(payload)
        return copy.deepcopy(payload)


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


def _actor(raw: Any, label: str) -> dict[str, Any]:
    _expect_bound_modules()
    raw = reporter.expect_object(raw, label)
    if set(raw) == {"__typename", "id", "login"}:
        type_name = reporter.expect_string(raw["__typename"], f"{label}.__typename")
        return {
            "id": reporter.expect_string(raw["id"], f"{label}.id"),
            "login": reporter.expect_string(raw["login"], f"{label}.login"),
            "kind": review_family.actor_kind_from_source_type(
                review_family.GITHUB_GRAPHQL_ACTOR_SOURCE,
                type_name,
                f"{label}.__typename",
            ),
            "source": review_family.GITHUB_GRAPHQL_ACTOR_SOURCE,
            "type": type_name,
        }
    if set(raw) == {"type", "node_id", "id", "login"}:
        type_name = reporter.expect_string(raw["type"], f"{label}.type")
        return {
            "id": reporter.expect_string(raw["node_id"], f"{label}.node_id"),
            "login": reporter.expect_string(raw["login"], f"{label}.login"),
            "kind": review_family.actor_kind_from_source_type(
                review_family.GITHUB_REST_ACTOR_SOURCE,
                type_name,
                f"{label}.type",
            ),
            "source": review_family.GITHUB_REST_ACTOR_SOURCE,
            "type": type_name,
            "database_id": reporter.expect_int(raw["id"], f"{label}.id", 1),
        }
    raise reporter.PilotDataError(
        f"{label} must use the explicit GraphQL or REST actor shape"
    )


def _graphql_actor(raw: Any, label: str) -> dict[str, Any]:
    actor = _actor(raw, label)
    if actor["source"] != review_family.GITHUB_GRAPHQL_ACTOR_SOURCE:
        raise reporter.PilotDataError(
            f"{label} must use the authoritative GraphQL actor shape"
        )
    return actor


def _require_authoritative_copilot_actor(
    actor: dict[str, Any], label: str
) -> None:
    if not review_family.is_authoritative_copilot_actor(actor):
        raise reporter.PilotDataError(
            f"{label} is not the exact authoritative GitHub Copilot Bot"
        )


def _collect_actors(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actors = {}
    for actor in records:
        existing = actors.get(actor["id"])
        if existing is not None and existing != actor:
            raise reporter.PilotDataError(
                f"GitHub actor ID {actor['id']!r} changed identity"
            )
        actors[actor["id"]] = actor
    return [actors[actor_id] for actor_id in sorted(actors)]


def _comment_object_and_body(raw: Any, label: str) -> tuple[dict[str, Any], str]:
    comment = reporter.expect_object(raw, label)
    reporter.expect_keys(
        comment,
        label,
        ("id", "createdAt", "updatedAt", "body", "author"),
    )
    _comment_created_time(comment, label)
    _comment_updated_time(comment, label)
    body = reporter.expect_string(comment["body"], f"{label}.body", allow_empty=True)
    return comment, body


def _canonical_prefixed_comment_payload(
    body: str, prefix: str, label: str
) -> dict[str, Any]:
    payload_text = body[len(prefix) :]
    payload = reporter.expect_object(
        reporter.parse_json(payload_text, label), label
    )
    canonical = reporter.normalized_json(payload).decode("ascii").rstrip("\n")
    if payload_text != canonical:
        raise reporter.PilotDataError(f"{label} is not canonical closed JSON")
    return payload


def _comment_created_time(comment: dict[str, Any], label: str) -> tuple[str, datetime]:
    created_at = reporter.expect_string(comment["createdAt"], f"{label}.createdAt")
    if RFC3339_UTC_TIMESTAMP_RE.fullmatch(created_at) is None:
        raise reporter.PilotDataError(
            f"{label}.createdAt must be an RFC 3339 UTC timestamp"
        )
    created = reporter.parse_time(created_at, f"{label}.createdAt")
    assert created is not None
    return created_at, created


def _comment_updated_time(comment: dict[str, Any], label: str) -> tuple[str, datetime]:
    updated_at = reporter.expect_string(comment["updatedAt"], f"{label}.updatedAt")
    if RFC3339_UTC_TIMESTAMP_RE.fullmatch(updated_at) is None:
        raise reporter.PilotDataError(
            f"{label}.updatedAt must be an RFC 3339 UTC timestamp"
        )
    updated = reporter.parse_time(updated_at, f"{label}.updatedAt")
    assert updated is not None
    return updated_at, updated


def _require_unedited_comment(comment: dict[str, Any], label: str) -> datetime:
    created_at, created = _comment_created_time(comment, label)
    updated_at, updated = _comment_updated_time(comment, label)
    if updated_at != created_at or updated != created:
        raise reporter.PilotDataError(f"{label} must not be edited")
    return created


def _require_exact_viewer_comment_author(
    comment: dict[str, Any],
    *,
    viewer: dict[str, Any],
    actors: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    author = _graphql_actor(comment["author"], f"{label}.author")
    actors.append(author)
    if author != viewer:
        raise reporter.PilotDataError(
            f"{label}.author is not the exact trusted coordinator actor"
        )
    return author


def _parse_disposition_comments(
    comments: list[Any],
    *,
    viewer: dict[str, Any],
    actors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prefix = "workflow-review-family-disposition:v2 "
    result = []
    for index, raw in enumerate(comments):
        label = f"GitHub pull-request comment[{index}]"
        comment, body = _comment_object_and_body(raw, label)
        if not body.startswith(prefix):
            continue
        _require_unedited_comment(comment, label)
        author = _require_exact_viewer_comment_author(
            comment,
            viewer=viewer,
            actors=actors,
            label=label,
        )
        payload = _canonical_prefixed_comment_payload(
            body,
            prefix,
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


def _parse_authoritative_family_classifications(
    comments: list[Any],
    *,
    viewer: dict[str, Any],
    repository_id: str | None,
    repository_name: str | None,
    contract: dict[str, Any],
    remote_reviews: list[dict[str, Any]],
    remote_findings: list[dict[str, Any]],
    actors: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    reviews_by_id = {review["node_id"]: review for review in remote_reviews}
    next_review_times = {
        remote_reviews[index]["node_id"]: remote_reviews[index + 1]["_submitted"]
        for index in range(len(remote_reviews) - 1)
    }
    findings_by_id = {finding["node_id"]: finding for finding in remote_findings}
    result: dict[str, dict[str, str]] = {}
    seen_reviews = set()
    for index, raw in enumerate(comments):
        label = f"GitHub pull-request comment[{index}]"
        comment, body = _comment_object_and_body(raw, label)
        if not body.startswith(EXTERNAL_CLASSIFICATION_COMMENT_PREFIX):
            continue
        created = _require_unedited_comment(comment, label)
        _require_exact_viewer_comment_author(
            comment,
            viewer=viewer,
            actors=actors,
            label=label,
        )
        payload = _canonical_prefixed_comment_payload(
            body,
            EXTERNAL_CLASSIFICATION_COMMENT_PREFIX,
            f"{label} family classification",
        )
        reporter.expect_keys(
            payload,
            f"{label} family classification",
            (
                "repository_id",
                "repository",
                "pull_request",
                "base_sha",
                "original_pre_review_head",
                "review_id",
                "candidate_sha",
                "findings",
            ),
        )
        if repository_id is None or repository_name is None:
            raise reporter.PilotDataError(
                f"{label} family classification lacks repository authority context"
            )
        if payload["repository_id"] != repository_id or payload["repository"] != repository_name:
            raise reporter.PilotDataError(
                f"{label} family classification does not bind the exact repository"
            )
        if (
            reporter.expect_int(
                payload["pull_request"],
                f"{label} family classification.pull_request",
                1,
            )
            != contract["pull_request"]
        ):
            raise reporter.PilotDataError(
                f"{label} family classification does not bind the exact PR"
            )
        if (
            reporter.expect_sha(
                payload["base_sha"],
                f"{label} family classification.base_sha",
            )
            != contract["base_sha"]
        ):
            raise reporter.PilotDataError(
                f"{label} family classification does not bind the exact base"
            )
        if (
            reporter.expect_sha(
                payload["original_pre_review_head"],
                f"{label} family classification.original_pre_review_head",
            )
            != contract["original_pre_review_head"]
        ):
            raise reporter.PilotDataError(
                f"{label} family classification does not bind the exact initial reviewed head"
            )
        review_id = reporter.expect_string(
            payload["review_id"], f"{label} family classification.review_id"
        )
        review = reviews_by_id.get(review_id)
        if review is None:
            continue
        if review_id in seen_reviews:
            raise reporter.PilotDataError(
                "external family classification comments are not unique per review"
            )
        seen_reviews.add(review_id)
        if (
            reporter.expect_sha(
                payload["candidate_sha"],
                f"{label} family classification.candidate_sha",
            )
            != review["candidate_sha"]
        ):
            raise reporter.PilotDataError(
                f"{label} family classification does not bind the exact reviewed head"
            )
        if created <= review["_submitted"]:
            raise reporter.PilotDataError(
                f"{label} family classification does not follow its authoritative review"
            )
        next_review_at = next_review_times.get(review_id)
        if next_review_at is not None and created >= next_review_at:
            raise reporter.PilotDataError(
                f"{label} family classification does not precede the next authoritative review"
            )
        mappings = reporter.expect_list(
            payload["findings"], f"{label} family classification.findings"
        )
        if not mappings:
            raise reporter.PilotDataError(
                f"{label} family classification.findings must not be empty"
            )
        local_ids = set()
        for position, raw_mapping in enumerate(mappings):
            mapping_label = f"{label} family classification.findings[{position}]"
            mapping = reporter.expect_object(raw_mapping, mapping_label)
            reporter.expect_keys(
                mapping, mapping_label, ("finding_id", "family")
            )
            finding_id = reporter.expect_string(
                mapping["finding_id"], f"{mapping_label}.finding_id"
            )
            if finding_id in local_ids:
                raise reporter.PilotDataError(
                    f"{label} family classification repeats finding {finding_id!r}"
                )
            local_ids.add(finding_id)
            finding = findings_by_id.get(finding_id)
            if finding is None:
                raise reporter.PilotDataError(
                    f"{label} family classification references an unknown authoritative finding"
                )
            if finding["review_id"] != review_id:
                raise reporter.PilotDataError(
                    f"{label} family classification finding does not belong to its authoritative review"
                )
            finding_created = reporter.parse_time(
                finding["created_at"], f"remote finding {finding_id}.created_at"
            )
            assert finding_created is not None
            if created < finding_created:
                raise reporter.PilotDataError(
                    f"{label} family classification predates authoritative finding creation"
                )
            if finding_id in result:
                raise reporter.PilotDataError(
                    f"authoritative family classification repeats finding {finding_id!r}"
                )
            result[finding_id] = {
                "family": reporter.expect_enum(
                    mapping["family"], set(review_family.FAMILY_MEMBERS), f"{mapping_label}.family"
                ),
                "comment_id": reporter.expect_string(comment["id"], f"{label}.id"),
                "comment_created_at": reporter.expect_string(
                    comment["createdAt"], f"{label}.createdAt"
                ),
            }
    return result


def collect_live_evidence_bytes(
    raw_contract: Any,
    repository_root: Path,
    expected_base: str,
    expected_remote_head: str,
    expected_candidate: str,
    review_report: dict[str, Any],
    receipt_envelope: dict[str, Any],
    execution_receipts: list[dict[str, Any]],
    *,
    local_remediation_receipt: dict[str, Any] | None = None,
    authoritative_trigger: dict[str, Any] | None = None,
    adapter: GhApiAdapter | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> bytes:
    """Collect credentialed state without executing any candidate module."""
    contract = review_family.validate_contract(raw_contract)
    root = reporter.validate_repository_root(repository_root)
    expected_base = reporter.expect_sha(expected_base, "expected base")
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
    viewer = _graphql_actor(data["viewer"], "GitHub viewer")
    repository = reporter.expect_object(data["repository"], "GitHub repository")
    reporter.expect_keys(
        repository,
        "GitHub repository",
        ("viewerPermission", "owner", "pullRequest"),
        optional=("id", "nameWithOwner"),
    )
    repository_id = (
        reporter.expect_string(repository["id"], "GitHub repository.id")
        if "id" in repository
        else None
    )
    repository_name = (
        reporter.expect_string(
            repository["nameWithOwner"], "GitHub repository.nameWithOwner"
        )
        if "nameWithOwner" in repository
        else None
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
            "mergeable",
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
    mergeable = reporter.expect_enum(
        pr["mergeable"],
        {"MERGEABLE", "CONFLICTING", "UNKNOWN"},
        "GitHub pull request mergeable",
    )
    head = reporter.expect_sha(pr["headRefOid"], "GitHub pull request head")
    if base != expected_base:
        raise reporter.PilotDataError(
            "authoritative PR base does not equal the expected current live base tip"
        )
    if head != expected_remote_head:
        raise reporter.PilotDataError(
            "authoritative PR head does not equal the expected remote head"
        )
    if review_report["candidate_sha"] != contract["original_pre_review_head"]:
        raise reporter.PilotDataError(
            "immutable pre-review does not bind original first-reviewed head"
        )
    author = _graphql_actor(pr["author"], "GitHub pull-request author")
    owner = _graphql_actor(repository["owner"], "GitHub repository owner")
    actor_records = [viewer, author, owner]

    # pushedDate is nullable metadata and is never reconstructed into head
    # authority. The authoritative head is headRefOid above.
    commit_shas = []
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
        commit_shas.append(commit_sha)
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
        if review["author"] is None:
            continue
        review_actor = _graphql_actor(review["author"], f"{label}.author")
        is_authoritative_copilot = review_family.is_authoritative_copilot_actor(
            review_actor
        )
        if not is_authoritative_copilot:
            continue
        actor_records.append(review_actor)
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
        submitted_at = reporter.expect_string(
            review["submittedAt"], f"{label}.submittedAt"
        )
        submitted = reporter.parse_time(
            submitted_at, f"{label}.submittedAt"
        )
        assert submitted is not None
        review_commit = reporter.expect_object(review["commit"], f"{label}.commit")
        reporter.expect_keys(review_commit, f"{label}.commit", ("oid",))
        review_candidate = reporter.expect_sha(
            review_commit["oid"], f"{label}.commit.oid"
        )
        finding_ids = []
        for comment_index, raw_comment in enumerate(comments):
            comment_label = f"{label}.comments[{comment_index}]"
            comment, _body = _comment_object_and_body(raw_comment, comment_label)
            _require_unedited_comment(comment, comment_label)
            finding_author = _graphql_actor(comment["author"], f"{comment_label}.author")
            actor_records.append(finding_author)
            _require_authoritative_copilot_actor(
                finding_author, f"{comment_label}.author"
            )
            if finding_author["id"] != review_actor["id"]:
                raise reporter.PilotDataError(
                    f"{comment_label}.author does not match the exact authoritative review actor"
                )
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
                "submitted_at": submitted_at,
                "_submitted": submitted,
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

    comment_nodes = _expect_page_complete(
        pr["comments"], "GitHub pull-request comments"
    )
    resolved_authoritative_trigger = authoritative_trigger
    if resolved_authoritative_trigger is None and contract["trust_mode"] != "introduction":
        resolved_authoritative_trigger = load_authoritative_trigger(
            contract,
            repository_root,
            expected_candidate,
        )
    if resolved_authoritative_trigger is None:
        external_trigger = _parse_external_trigger_comment(
            comment_nodes,
            viewer=viewer,
            repository_id=repository_id,
            repository_name=repository_name,
            contract=contract,
            initial_remote_head=(
                remote_reviews[0]["candidate_sha"]
                if remote_reviews
                else expected_remote_head
            ),
            first_remote_review_at=(
                remote_reviews[0]["_submitted"] if remote_reviews else None
            ),
            actors=actor_records,
        )
        if external_trigger is not None:
            candidate_record, _candidate_blob_oid = _load_candidate_trigger_record(
                root,
                candidate_sha=expected_candidate,
                pull_request=contract["pull_request"],
            )
            if candidate_record != external_trigger["_record"]:
                raise reporter.PilotDataError(
                    "candidate decision record drifts from trusted preregistration decision authority"
                )
            resolved_authoritative_trigger = {
                name: value
                for name, value in external_trigger.items()
                if not name.startswith("_")
            }

    family_classifications = _parse_authoritative_family_classifications(
        comment_nodes,
        viewer=viewer,
        repository_id=repository_id,
        repository_name=repository_name,
        contract=contract,
        remote_reviews=remote_reviews,
        remote_findings=remote_findings,
        actors=actor_records,
    )
    for finding in remote_findings:
        finding_id = finding["node_id"]
        if finding_id not in family_classifications:
            raise reporter.PilotDataError(
                f"remote finding {finding_id!r} has no authoritative family classification"
            )
        finding["family"] = family_classifications[finding_id]["family"]
        finding["authority_comment_id"] = family_classifications[finding_id][
            "comment_id"
        ]
        finding["authority_comment_created_at"] = family_classifications[
            finding_id
        ]["comment_created_at"]

    threads = []
    accepted_findings = {
        finding["node_id"]: finding for finding in remote_findings
    }
    finding_to_thread = {}
    finding_to_thread_actor = {}
    for index, raw_thread in enumerate(
        _expect_page_complete(pr["reviewThreads"], "GitHub pull-request threads")
    ):
        label = f"GitHub review thread[{index}]"
        thread = reporter.expect_object(raw_thread, label)
        reporter.expect_keys(thread, label, ("id", "isResolved", "comments"))
        comments = _expect_page_complete(thread["comments"], f"{label}.comments")
        if not comments:
            continue
        first = reporter.expect_object(comments[0], f"{label}.comments[0]")
        finding_id = first.get("id")
        if not isinstance(finding_id, str) or not finding_id.strip():
            continue
        accepted_finding = accepted_findings.get(finding_id)
        if accepted_finding is None:
            continue
        reporter.expect_keys(
            first,
            f"{label}.comments[0]",
            ("id", "createdAt", "author", "pullRequestReview"),
        )
        if first["author"] is None:
            continue
        thread_review = reporter.expect_object(
            first["pullRequestReview"],
            f"{label}.comments[0].pullRequestReview",
        )
        reporter.expect_keys(
            thread_review,
            f"{label}.comments[0].pullRequestReview",
            ("id",),
        )
        review_id = reporter.expect_string(
            thread_review["id"], f"{label}.comments[0].pullRequestReview.id"
        )
        if review_id != accepted_finding["review_id"]:
            raise reporter.PilotDataError(
                f"remote finding {finding_id!r} review thread does not match its exact authoritative review"
            )
        if first["createdAt"] != accepted_finding["created_at"]:
            raise reporter.PilotDataError(
                f"remote finding {finding_id!r} review thread root does not preserve its exact authoritative chronology"
            )
        thread_author = _graphql_actor(
            first["author"], f"{label}.comments[0].author"
        )
        actor_records.append(thread_author)
        if finding_id in finding_to_thread:
            raise reporter.PilotDataError(
                f"remote finding {finding_id!r} has duplicate review threads"
            )
        finding_to_thread[finding_id] = thread["id"]
        finding_to_thread_actor[finding_id] = thread_author["id"]
        threads.append(
            {
                "node_id": reporter.expect_string(thread["id"], f"{label}.id"),
                "finding_id": finding_id,
                "is_resolved": reporter.expect_bool(
                    thread["isResolved"], f"{label}.isResolved"
                ),
            }
        )

    candidate_families = review_family.finding_family_map(contract)
    for finding in remote_findings:
        finding_id = finding["node_id"]
        expected_family = candidate_families.get(finding_id)
        if expected_family is None:
            raise reporter.PilotDataError(
                f"remote finding {finding_id!r} has no candidate family sweep"
            )
        if expected_family != finding["family"]:
            raise reporter.PilotDataError(
                f"remote finding {finding_id!r} family-authority-drift: "
                "candidate family sweep does not match trusted classification"
            )
        if finding_id not in finding_to_thread:
            raise reporter.PilotDataError(
                f"remote finding {finding_id!r} has no review thread"
            )
        if finding_to_thread_actor.get(finding_id) != finding["author_actor_id"]:
            raise reporter.PilotDataError(
                f"remote finding {finding_id!r} thread author does not match the exact authoritative review actor"
            )

    dispositions = _parse_disposition_comments(
        comment_nodes,
        viewer=viewer,
        actors=actor_records,
    )
    actor_records.append(
        {
            "id": review_report["reviewer_actor_id"],
            "login": review_report["reviewer_login"],
            "kind": "service",
            "source": review_family.SERVICE_ACTOR_SOURCE,
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
        expected_family = candidate_families.get(finding["id"])
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
    serialized_remote_reviews = [
        {
            name: value
            for name, value in review.items()
            if not name.startswith("_")
        }
        for review in remote_reviews
    ]
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
            "mergeable": mergeable,
            "head_sha": head,
            "author_actor_id": author["id"],
            "commit_shas": commit_shas,
        },
        authoritative_trigger=resolved_authoritative_trigger,
        actors=actors,
        pre_reviews=pre_reviews,
        pre_review_findings=pre_findings,
        remote_reviews=serialized_remote_reviews,
        findings=remote_findings,
        threads=threads,
        force_push_events=force_push_events,
        architecture_dispositions=dispositions,
        execution_receipts=execution_receipts,
        local_remediation_receipt=local_remediation_receipt,
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
            "authoritative_trigger",
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
    adapter = _RecordingAdapter(adapter or GhApiAdapter())
    expected_base = reporter.expect_sha(expected_base, "expected base")
    expected_remote_head = (
        expected_candidate
        if expected_remote_head is None
        else reporter.expect_sha(expected_remote_head, "expected remote head")
    )
    if pre_review_state not in {"new", "preserved"}:
        raise reporter.PilotDataError("pre-review state is not supported")
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
        base_sha=contract["base_sha"],
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
    base_supports_gate = _base_contains_gate(repository_root, contract["base_sha"])
    first_evidence_bytes = collect_live_evidence_bytes(
        raw_contract,
        repository_root,
        expected_base,
        expected_remote_head,
        expected_candidate,
        review_report,
        envelope,
        [],
        authoritative_trigger=None,
        adapter=adapter,
        clock=clock,
    )
    if adapter.last_payload is None:
        raise reporter.PilotDataError(
            "trusted GitHub collector did not preserve the authoritative payload"
        )
    first_evidence = reporter.expect_object(
        reporter.parse_json(first_evidence_bytes.decode("utf-8"), "first evidence"),
        "first evidence",
    )
    if (
        contract["trust_mode"] == "introduction"
        or not base_supports_gate
    ):
        return bootstrap_result(contract, contract["base_sha"], expected_candidate)
    authoritative_trigger = first_evidence["authoritative_trigger"]
    if authoritative_trigger is None:
        raise reporter.PilotDataError(
            "base-pinned mode requires authoritative trigger decision or trusted preregistration evidence"
        )
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
        base_sha=contract["base_sha"],
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
            captured_github_payload=adapter.last_payload,
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
    local_remediation_receipt = None
    latest_review = (
        first_evidence["remote_reviews"][-1]
        if first_evidence["remote_reviews"]
        else None
    )
    if (
        expected_remote_head != expected_candidate
        and latest_review is not None
        and latest_review["candidate_sha"] == expected_remote_head
        and latest_review["outcome"] != "clean"
        and latest_review["finding_ids"]
    ):
        local_remediation_receipt = run_base_pinned_checker(
            repository_root,
            contract=contract,
            candidate_sha=expected_candidate,
            review_round=latest_review["round"],
            review_context=latest_review,
            all_remote_reviews=first_evidence["remote_reviews"],
            remote_findings=first_evidence["findings"],
            remote_finding_ids=latest_review["finding_ids"],
            captured_github_payload=adapter.last_payload,
            original_review_report_bytes=report_bytes,
            original_review_receipt=envelope,
            original_receipt_sha256=original_receipt_sha256,
            assertion_requests=review_family.build_local_remediation_requests(
                contract,
                first_evidence,
                expected_candidate,
                latest_review["round"],
            ),
            trusted_key=trusted_key,
            clock=clock,
        )
        if not hmac.compare_digest(
            local_remediation_receipt["seal"],
            _execution_receipt_seal(local_remediation_receipt, trusted_key),
        ):
            raise reporter.PilotDataError("local remediation receipt HMAC is invalid")
    second_evidence = collect_live_evidence_bytes(
        raw_contract,
        repository_root,
        expected_base,
        expected_remote_head,
        expected_candidate,
        review_report,
        envelope,
        execution_receipts,
        local_remediation_receipt=local_remediation_receipt,
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
            receipt["seal"]
            for receipt in (
                [*execution_receipts]
                + (
                    [local_remediation_receipt]
                    if local_remediation_receipt is not None
                    else []
                )
            )
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
        key_id = trusted_environment.get("WORKFLOW_REVIEW_RECEIPT_KEY_ID")
        key_epoch_text = trusted_environment.get("WORKFLOW_REVIEW_RECEIPT_KEY_EPOCH")
        key = trusted_environment.get("WORKFLOW_REVIEW_RECEIPT_HMAC_KEY")
        replay_store = trusted_environment.get("WORKFLOW_REVIEW_REPLAY_STORE")
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
