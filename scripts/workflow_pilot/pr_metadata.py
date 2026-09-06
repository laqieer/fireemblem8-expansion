#!/usr/bin/env python3
"""Guard pull-request metadata edits against exact-candidate Build races."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.parse
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Literal

from . import candidate_evidence, metadata_event


MAX_API_BYTES = 4 * 1024 * 1024
MAX_BODY_BYTES = 1024 * 1024
MAX_COMMENT_PAGES = 10
MAX_REASON_BYTES = 4096
MAX_RUN_PAGES = 10
MAX_RUNS = 1000
PAGE_SIZE = 100
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMMENT_CREATION_RE = re.compile(
    r"^repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*/comments$"
)
WORKFLOW_PATH = ".github/workflows/build.yml"
EVIDENCE_MARKER = "<!-- workflow-pilot-candidate-evidence -->"
INTENT_MARKER = "<!-- workflow-pilot-metadata-edit-intent:v1 -->"
CONFIRMATION_MARKER = "<!-- workflow-pilot-metadata-edit-confirmation:v1 -->"
ABORT_MARKER = "<!-- workflow-pilot-metadata-edit-abort:v1 -->"
HTTP_STATUS_RE = re.compile(r"^HTTP/(?:1(?:\.[01])?|2(?:\.0)?) ([1-5][0-9]{2})(?: .*)?$")
HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
LINK_PART_RE = re.compile(r'\s*<([^>]+)>\s*;\s*rel="([^"]+)"\s*(?:,\s*|$)')
GITHUB_TIMESTAMP_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})Z$"
)
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
DEFINITE_PATCH_REJECTIONS = frozenset({400, 401, 403, 404, 409, 422, 429})

FULL_JOB_NAMES = frozenset(candidate_evidence.KNOWN_JOB_IDS)
METADATA_JOB_NAMES = (
    FULL_JOB_NAMES - {candidate_evidence.FULL_CLASSIFIER}
) | {candidate_evidence.METADATA_CLASSIFIER}
FULL_SUCCESS_JOB_NAMES = FULL_JOB_NAMES - {"patch-release"}
ACTIVE_RUN_STATUSES = frozenset(
    {"pending", "queued", "requested", "in_progress", "waiting"}
)
RunBinding = Literal["explicit-same", "explicit-other", "unbound"]
RUN_CONCLUSIONS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
        "success",
        "timed_out",
    }
)
METADATA_VERSION_QUERY = """query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){
    databaseId
    nameWithOwner
    owner{__typename login ... on User{databaseId}}
    pullRequest(number:$number){
      id
      databaseId
      createdAt
      author{__typename login ... on User{databaseId}}
      baseRefOid
      baseRefName
      body
      headRefOid
      headRefName
      lastEditedAt
      number
      title
      updatedAt
      url
      editor{__typename login ... on User{databaseId}}
      userContentEdits(first:2){
        totalCount
        pageInfo{hasNextPage hasPreviousPage startCursor endCursor}
        nodes{
          id createdAt editedAt updatedAt deletedAt diff
          editor{__typename login ... on User{databaseId}}
        }
      }
      timelineItems(last:100,itemTypes:[RENAMED_TITLE_EVENT]){
        nodes{__typename ... on RenamedTitleEvent{
          id createdAt previousTitle currentTitle
          actor{__typename login ... on User{databaseId}}
        }}
      }
    }
  }
}"""


class MetadataEditError(ValueError):
    """GitHub authority or an orchestration decision failed closed."""


@dataclass(frozen=True)
class PullRequestState:
    repository: str
    repository_id: int
    repository_owner_id: int
    pull_request_id: int
    pull_request_node_id: str
    number: int
    head_sha: str
    head_ref: str
    base_sha: str
    base_ref: str
    title: str
    body: str
    updated_at: datetime.datetime


@dataclass(frozen=True)
class CommentState:
    comment_id: int
    repository: str
    pr_number: int
    html_url: str
    body: str
    author_id: int | None
    author_login: str | None
    author_type: str | None
    author_site_admin: bool | None
    author_association: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    intent: EditReceipt | None
    confirmation: EditConfirmation | None
    abort: EditAbort | None


@dataclass(frozen=True)
class JobState:
    job_id: int
    run_id: int
    name: str
    status: str
    conclusion: str | None
    runner_name: str | None
    created_at: datetime.datetime
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None
    metadata_event_sha256: str | None = None


@dataclass(frozen=True)
class WorkflowAuthority:
    workflow_id: int
    name: str
    path: str


@dataclass(frozen=True)
class RunState:
    run_id: int
    workflow_id: int
    run_number: int
    run_attempt: int
    head_branch: str
    created_at: datetime.datetime
    run_started_at: datetime.datetime | None
    updated_at: datetime.datetime
    status: str
    conclusion: str | None
    binding: RunBinding
    mode: str
    jobs: tuple[JobState, ...]


@dataclass(frozen=True)
class EditFieldDigest:
    field: str
    sha256: str


@dataclass(frozen=True)
class BodyOriginal:
    edit_id: str
    body_sha256: str
    author_id: int
    author_login: str
    authored_at: str
    materialized_at: str


@dataclass(frozen=True)
class MetadataVersion:
    title_event_id: str | None
    title_event_created_at: str | None
    title_previous: str | None
    title_current: str | None
    title_actor_id: int | None
    title_actor_login: str | None
    body_last_edited_at: str | None
    body_editor_id: int | None
    body_editor_login: str | None
    body_edit_total_count: int
    body_edit_id: str | None
    body_edit_created_at: str | None
    body_edit_edited_at: str | None
    body_edit_updated_at: str | None
    body_original: BodyOriginal | None

    def canonical_payload(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EditReceipt:
    schema_version: int
    repository: str
    repository_id: int
    pr_number: int
    head_sha: str
    base_sha: str
    workflow_id: int
    workflow_path: str
    nonce: str
    pre_metadata_sha256: str
    pre_fields: tuple[EditFieldDigest, ...]
    target_metadata_sha256: str
    pre_version: MetadataVersion
    provided_fields: tuple[EditFieldDigest, ...]
    changed_fields: tuple[EditFieldDigest, ...]
    watermark_run_id: int
    watermark_run_number: int
    watermark_created_at: str

    def canonical_payload(self) -> dict[str, object]:
        return {
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "nonce": self.nonce,
            "pre_metadata_sha256": self.pre_metadata_sha256,
            "pre_fields": {
                field.field: field.sha256
                for field in self.pre_fields
            },
            "pre_version": self.pre_version.canonical_payload(),
            "pr_number": self.pr_number,
            "repository": self.repository,
            "repository_id": self.repository_id,
            "provided_fields": {
                field.field: field.sha256
                for field in self.provided_fields
            },
            "changed_fields": {
                field.field: field.sha256
                for field in self.changed_fields
            },
            "schema_version": self.schema_version,
            "target_metadata_sha256": self.target_metadata_sha256,
            "watermark": {
                "created_at": self.watermark_created_at,
                "run_id": self.watermark_run_id,
                "run_number": self.watermark_run_number,
            },
            "workflow": {
                "id": self.workflow_id,
                "path": self.workflow_path,
            },
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"


@dataclass(frozen=True)
class EditConfirmation:
    schema_version: int
    repository: str
    repository_id: int
    pr_number: int
    head_sha: str
    base_sha: str
    intent_comment_id: int
    intent_nonce: str
    metadata_sha256: str
    metadata_version: MetadataVersion

    def canonical_payload(self) -> dict[str, object]:
        return {
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "intent_comment_id": self.intent_comment_id,
            "intent_nonce": self.intent_nonce,
            "metadata_sha256": self.metadata_sha256,
            "metadata_version": self.metadata_version.canonical_payload(),
            "pr_number": self.pr_number,
            "repository": self.repository,
            "repository_id": self.repository_id,
            "schema_version": self.schema_version,
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"


@dataclass(frozen=True)
class EditAbort:
    schema_version: int
    repository: str
    repository_id: int
    pr_number: int
    intent_comment_id: int
    intent_nonce: str
    intent_head_sha: str
    intent_base_sha: str
    observed_head_sha: str
    observed_base_sha: str
    observed_metadata_sha256: str
    observed_version: MetadataVersion
    reason: str

    def canonical_payload(self) -> dict[str, object]:
        return {
            "intent_base_sha": self.intent_base_sha,
            "intent_comment_id": self.intent_comment_id,
            "intent_head_sha": self.intent_head_sha,
            "intent_nonce": self.intent_nonce,
            "observed_base_sha": self.observed_base_sha,
            "observed_head_sha": self.observed_head_sha,
            "observed_metadata_sha256": self.observed_metadata_sha256,
            "observed_version": self.observed_version.canonical_payload(),
            "pr_number": self.pr_number,
            "reason": self.reason,
            "repository": self.repository,
            "repository_id": self.repository_id,
            "schema_version": self.schema_version,
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"


@dataclass(frozen=True)
class Decision:
    action: str
    base_sha: str
    guidance: tuple[tuple[str, ...], ...]
    head_sha: str
    mutated: bool
    reason: str
    repository: str
    pr_number: int
    run_id: int | None = None
    comment_id: int | None = None
    intent_comment_id: int | None = None
    intent_comment_url: str | None = None
    confirmation_comment_id: int | None = None
    confirmation_comment_url: str | None = None
    abort_comment_id: int | None = None
    abort_comment_url: str | None = None

    def __post_init__(self) -> None:
        for field, value in (
            ("run_id", self.run_id),
            ("comment_id", self.comment_id),
            ("intent_comment_id", self.intent_comment_id),
            ("confirmation_comment_id", self.confirmation_comment_id),
            ("abort_comment_id", self.abort_comment_id),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                or value > 999999999999999999
            ):
                raise MetadataEditError(
                    f"Decision {field} must be a positive integer"
                )
        if self.run_id is not None and self.comment_id is not None:
            raise MetadataEditError(
                "Decision run_id and comment_id are mutually exclusive"
            )
        for label, comment_id, url in (
            ("intent", self.intent_comment_id, self.intent_comment_url),
            (
                "confirmation",
                self.confirmation_comment_id,
                self.confirmation_comment_url,
            ),
            ("abort", self.abort_comment_id, self.abort_comment_url),
        ):
            if (comment_id is None) != (url is None):
                raise MetadataEditError(
                    f"Decision {label} comment ID and URL must appear together"
                )
            if comment_id is not None:
                expected_url = (
                    f"https://github.com/{self.repository}/pull/{self.pr_number}"
                    f"#issuecomment-{comment_id}"
                )
                if url != expected_url:
                    raise MetadataEditError(
                        f"Decision {label} comment URL identity drifted"
                    )
        if self.action == "comment-updated":
            if self.comment_id is None or self.run_id is not None:
                raise MetadataEditError(
                    "comment-updated Decision requires only comment_id"
                )
        elif self.comment_id is not None:
            raise MetadataEditError(
                "only comment-updated Decision may contain comment_id"
            )
        if self.action in {"recovered", "updated"}:
            if (
                self.intent_comment_id is None
                or self.intent_comment_url is None
                or self.confirmation_comment_id is None
                or self.confirmation_comment_url is None
                or self.abort_comment_id is not None
                or self.abort_comment_url is not None
            ):
                raise MetadataEditError(
                    "updated Decision requires intent and confirmation comments"
                )
        elif (
            self.action in {"deferred", "no-op"}
            and self.intent_comment_id is not None
            and self.confirmation_comment_id is not None
        ):
            pass
        elif (
            self.action == "deferred"
            and self.abort_comment_id is not None
        ):
            pass
        elif (
            self.action == "deferred"
            and self.mutated is False
            and self.intent_comment_id is not None
            and self.confirmation_comment_id is None
            and self.abort_comment_id is None
        ):
            pass
        elif (
            self.intent_comment_id is not None
            or self.intent_comment_url is not None
            or self.confirmation_comment_id is not None
            or self.confirmation_comment_url is not None
            or self.abort_comment_id is not None
            or self.abort_comment_url is not None
        ):
            raise MetadataEditError(
                "transaction comments require an authoritative pair, an abort, "
                "or a read-only deferred intent hold"
            )

    def canonical_json(self) -> str:
        payload = asdict(self)
        payload["guidance"] = [list(command) for command in self.guidance]
        return json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"


@dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: dict[str, str]
    payload: object


class GitHubHTTPError(MetadataEditError):
    """A complete validated HTTP response rejected the expected API status."""

    def __init__(
        self,
        method: str,
        endpoint: str,
        response: ApiResponse,
        *,
        label: str,
        expected_status: int,
    ) -> None:
        self.method = method
        self.endpoint = endpoint
        self.response = response
        super().__init__(
            f"{label} returned HTTP {response.status}, expected {expected_status}"
        )


@dataclass(frozen=True)
class HeaderFieldPolicy:
    repeatable: bool
    separator: str = ", "


@dataclass(frozen=True)
class JobTimingPolicy:
    started: str
    completed: str


COMBINABLE_HEADER_POLICIES = {
    "cache-control": HeaderFieldPolicy(True),
    "link": HeaderFieldPolicy(True),
    "vary": HeaderFieldPolicy(True),
}
SINGLETON_HEADER_POLICY = HeaderFieldPolicy(False)
JOB_TIMING_POLICIES = {
    "completed": JobTimingPolicy("required", "required"),
    "in_progress": JobTimingPolicy("required", "null"),
    "pending": JobTimingPolicy("null", "null"),
    "queued": JobTimingPolicy("null", "null"),
    "requested": JobTimingPolicy("null", "null"),
    "waiting": JobTimingPolicy("null", "null"),
}


def _positive_int(value: object, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > 999999999999999999
    ):
        raise MetadataEditError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > 999999999999999999
    ):
        raise MetadataEditError(f"{field} must be a nonnegative integer")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise MetadataEditError(f"{field} must be a full lowercase SHA")
    return value


def _github_timestamp(
    value: object,
    field: str,
    *,
    optional: bool = False,
) -> datetime.datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise MetadataEditError(f"{field} must be a GitHub RFC3339 timestamp")
    match = GITHUB_TIMESTAMP_RE.fullmatch(value)
    if match is None:
        raise MetadataEditError(f"{field} must be a GitHub RFC3339 timestamp")
    try:
        return datetime.datetime(
            *(int(part) for part in match.groups()),
            tzinfo=datetime.timezone.utc,
        )
    except ValueError as error:
        raise MetadataEditError(
            f"{field} must be a valid GitHub RFC3339 timestamp"
        ) from error


def _text(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise MetadataEditError(f"{field} must be nonempty text")
    return value


def _repository(value: str) -> str:
    if (
        REPOSITORY_RE.fullmatch(value) is None
        or value.startswith((".", "-"))
        or "/." in value
        or "/-" in value
    ):
        raise MetadataEditError("--repository must be an owner/name slug")
    return value


def _parse_json(raw: str, label: str) -> object:
    if len(raw.encode("utf-8")) > MAX_API_BYTES:
        raise MetadataEditError(f"{label} response exceeds 4 MiB")

    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise MetadataEditError(f"{label} response repeats key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise MetadataEditError(f"{label} response contains {value}")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise MetadataEditError(f"{label} response is invalid JSON: {error}") from error

    def require_finite(item: object) -> None:
        if isinstance(item, float):
            if not math.isfinite(item):
                raise MetadataEditError(f"{label} response contains a non-finite number")
            return
        if isinstance(item, dict):
            for child in item.values():
                require_finite(child)
            return
        if isinstance(item, list):
            for child in item:
                require_finite(child)

    require_finite(value)
    return value


def _timestamp_text(value: datetime.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _metadata_digest(title: str, body: str | None) -> str:
    canonical = json.dumps(
        {"body": body, "title": title},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _content_digest(canonical)


def _field_state_digest(value: str | None) -> str:
    return _content_digest(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )


def _provided_field_digests(
    *,
    title: str | None,
    body: str | None,
) -> tuple[EditFieldDigest, ...]:
    fields = []
    if body is not None:
        fields.append(EditFieldDigest("body", _content_digest(body)))
    if title is not None:
        fields.append(EditFieldDigest("title", _content_digest(title)))
    if not fields:
        raise MetadataEditError("edit intent requires a provided field")
    return tuple(fields)


def _changed_field_digests(
    state: PullRequestState,
    *,
    title: str | None,
    body: str | None,
) -> tuple[EditFieldDigest, ...]:
    fields = []
    if body is not None and body != state.body:
        fields.append(EditFieldDigest("body", _content_digest(body)))
    if title is not None and title != state.title:
        fields.append(EditFieldDigest("title", _content_digest(title)))
    return tuple(fields)


def _parse_body_original(payload: object, *, label: str) -> BodyOriginal:
    if not isinstance(payload, dict) or set(payload) != {
        "edit_id", "body_sha256", "author_id", "author_login",
        "authored_at", "materialized_at",
    }:
        raise MetadataEditError(f"{label} original body shape is invalid")
    digest = payload["body_sha256"]
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise MetadataEditError(f"{label} original body digest is invalid")
    authored_at = _timestamp_text(
        _github_timestamp(payload["authored_at"], f"{label} original authored_at")
    )
    materialized_at = _timestamp_text(
        _github_timestamp(
            payload["materialized_at"], f"{label} original materialized_at"
        )
    )
    if authored_at >= materialized_at:
        raise MetadataEditError(f"{label} original body chronology is ambiguous")
    return BodyOriginal(
        _text(payload["edit_id"], f"{label} original edit id"),
        digest,
        _positive_int(payload["author_id"], f"{label} original author id"),
        _text(payload["author_login"], f"{label} original author login"),
        authored_at,
        materialized_at,
    )


def _parse_metadata_version_payload(
    payload: object,
    *,
    label: str,
) -> MetadataVersion:
    if not isinstance(payload, dict) or set(payload) != {
        "body_editor_id",
        "body_editor_login",
        "body_edit_created_at",
        "body_edit_edited_at",
        "body_edit_id",
        "body_edit_total_count",
        "body_edit_updated_at",
        "body_last_edited_at",
        "body_original",
        "title_actor_id",
        "title_actor_login",
        "title_current",
        "title_event_created_at",
        "title_event_id",
        "title_previous",
    }:
        raise MetadataEditError(f"{label} metadata version shape is invalid")
    title_event_id = payload["title_event_id"]
    title_event_created_at = payload["title_event_created_at"]
    title_previous = payload["title_previous"]
    title_current = payload["title_current"]
    title_actor_id = payload["title_actor_id"]
    title_actor_login = payload["title_actor_login"]
    title_identity_values = (
        title_event_id,
        title_event_created_at,
        title_previous,
        title_current,
    )
    if all(value is None for value in title_identity_values):
        if title_actor_id is not None or title_actor_login is not None:
            raise MetadataEditError(f"{label} title actor is partial")
        pass
    elif (
        not isinstance(title_event_id, str)
        or not title_event_id
        or not isinstance(title_previous, str)
        or not isinstance(title_current, str)
    ):
        raise MetadataEditError(f"{label} title version is invalid")
    else:
        title_event_created_at = _timestamp_text(
            _github_timestamp(
                title_event_created_at,
                f"{label} title event created_at",
            )
        )
        if (title_actor_id is None) != (title_actor_login is None):
            raise MetadataEditError(f"{label} title actor is partial")
        if title_actor_id is not None:
            title_actor_id = _positive_int(
                title_actor_id,
                f"{label} title actor id",
            )
            title_actor_login = _text(
                title_actor_login,
                f"{label} title actor login",
            )
    body_last_edited_at = payload["body_last_edited_at"]
    body_editor_id = payload["body_editor_id"]
    body_editor_login = payload["body_editor_login"]
    body_edit_total_count = payload["body_edit_total_count"]
    if (
        isinstance(body_edit_total_count, bool)
        or not isinstance(body_edit_total_count, int)
        or body_edit_total_count < 0
        or body_edit_total_count == 1
        or body_edit_total_count > 999999999
    ):
        raise MetadataEditError(f"{label} body edit count is invalid")
    body_edit_values = (
        payload["body_edit_id"],
        payload["body_edit_created_at"],
        payload["body_edit_edited_at"],
        payload["body_edit_updated_at"],
        body_last_edited_at,
        body_editor_id,
        body_editor_login,
    )
    if body_edit_total_count == 0:
        if any(value is not None for value in body_edit_values):
            raise MetadataEditError(f"{label} body no-edit version is invalid")
    else:
        if not all(value is not None for value in body_edit_values):
            raise MetadataEditError(f"{label} body edit version is incomplete")
        body_edit_id = _text(payload["body_edit_id"], f"{label} body edit id")
        body_edit_created_at = _timestamp_text(
            _github_timestamp(
                payload["body_edit_created_at"],
                f"{label} body edit created_at",
            )
        )
        body_edit_edited_at = _timestamp_text(
            _github_timestamp(
                payload["body_edit_edited_at"],
                f"{label} body edit edited_at",
            )
        )
        body_edit_updated_at = _timestamp_text(
            _github_timestamp(
                payload["body_edit_updated_at"],
                f"{label} body edit updated_at",
            )
        )
        if not (
            body_edit_edited_at
            <= body_edit_created_at
            <= body_edit_updated_at
        ):
            raise MetadataEditError(f"{label} body edit chronology is invalid")
        body_last_edited_at = _timestamp_text(
            _github_timestamp(
                body_last_edited_at,
                f"{label} body lastEditedAt",
            )
        )
        body_editor_id = _positive_int(
            body_editor_id,
            f"{label} body editor id",
        )
        body_editor_login = _text(
            body_editor_login,
            f"{label} body editor login",
        )
        if body_last_edited_at != body_edit_edited_at:
            raise MetadataEditError(
                f"{label} body lastEditedAt is inconsistent"
            )
    if body_edit_total_count == 0:
        body_edit_id = None
        body_edit_created_at = None
        body_edit_edited_at = None
        body_edit_updated_at = None
    body_original = None
    if body_edit_total_count == 2:
        body_original = _parse_body_original(payload["body_original"], label=label)
        if (
            body_original.edit_id == body_edit_id
            or body_original.authored_at >= body_edit_edited_at
            or not (
                body_original.materialized_at
                == body_edit_created_at
                == body_edit_updated_at
            )
        ):
            raise MetadataEditError(f"{label} original body materialization is invalid")
    elif payload["body_original"] is not None:
        raise MetadataEditError(f"{label} original body has no complete history")
    return MetadataVersion(
        title_event_id,
        title_event_created_at,
        title_previous,
        title_current,
        title_actor_id,
        title_actor_login,
        body_last_edited_at,
        body_editor_id,
        body_editor_login,
        body_edit_total_count,
        body_edit_id,
        body_edit_created_at,
        body_edit_edited_at,
        body_edit_updated_at,
        body_original,
    )


def _parse_edit_receipt(payload: object) -> EditReceipt:
    if not isinstance(payload, dict) or set(payload) != {
        "base_sha",
        "head_sha",
        "nonce",
        "pre_fields",
        "pre_metadata_sha256",
        "pre_version",
        "pr_number",
        "repository",
        "repository_id",
        "provided_fields",
        "changed_fields",
        "schema_version",
        "target_metadata_sha256",
        "watermark",
        "workflow",
    }:
        raise MetadataEditError("edit receipt shape is invalid")
    if (
        isinstance(payload["schema_version"], bool)
        or not isinstance(payload["schema_version"], int)
        or payload["schema_version"] != 1
    ):
        raise MetadataEditError("edit receipt schema_version is invalid")
    repository = payload["repository"]
    if not isinstance(repository, str):
        raise MetadataEditError("edit receipt repository is invalid")
    repository = _repository(repository)
    provided = payload["provided_fields"]
    if (
        not isinstance(provided, dict)
        or not provided
        or not set(provided) <= {"body", "title"}
    ):
        raise MetadataEditError("edit intent provided_fields are invalid")
    provided_fields = []
    for field in sorted(provided):
        digest = provided[field]
        if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
            raise MetadataEditError(
                f"edit intent provided {field} digest is invalid"
            )
        provided_fields.append(EditFieldDigest(field, digest))
    changed = payload["changed_fields"]
    if (
        not isinstance(changed, dict)
        or not changed
        or not set(changed) <= set(provided)
    ):
        raise MetadataEditError("edit intent changed_fields are invalid")
    changed_fields = []
    for field in sorted(changed):
        digest = changed[field]
        if (
            not isinstance(digest, str)
            or DIGEST_RE.fullmatch(digest) is None
            or digest != provided[field]
        ):
            raise MetadataEditError(
                f"edit intent changed {field} digest is invalid"
            )
        changed_fields.append(EditFieldDigest(field, digest))
    pre_fields = payload["pre_fields"]
    if not isinstance(pre_fields, dict) or set(pre_fields) != {"body", "title"}:
        raise MetadataEditError("edit receipt pre_fields are invalid")
    parsed_pre_fields = []
    for field in ("body", "title"):
        digest = pre_fields[field]
        if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
            raise MetadataEditError(
                f"edit receipt pre-{field} digest is invalid"
            )
        parsed_pre_fields.append(EditFieldDigest(field, digest))
    nonce = payload["nonce"]
    if not isinstance(nonce, str) or DIGEST_RE.fullmatch(nonce) is None:
        raise MetadataEditError("edit receipt nonce is invalid")
    for field in ("pre_metadata_sha256", "target_metadata_sha256"):
        digest = payload[field]
        if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
            raise MetadataEditError(f"edit receipt {field} is invalid")
    workflow = payload["workflow"]
    if not isinstance(workflow, dict) or set(workflow) != {"id", "path"}:
        raise MetadataEditError("edit receipt workflow shape is invalid")
    if workflow["path"] != WORKFLOW_PATH:
        raise MetadataEditError("edit receipt workflow path is invalid")
    watermark = payload["watermark"]
    if not isinstance(watermark, dict) or set(watermark) != {
        "created_at",
        "run_id",
        "run_number",
    }:
        raise MetadataEditError("edit receipt watermark shape is invalid")
    watermark_created_at = _github_timestamp(
        watermark["created_at"],
        "edit receipt watermark created_at",
    )
    return EditReceipt(
        schema_version=1,
        repository=repository,
        repository_id=_positive_int(
            payload["repository_id"],
            "edit receipt repository_id",
        ),
        pr_number=_positive_int(
            payload["pr_number"],
            "edit receipt pr_number",
        ),
        head_sha=_sha(payload["head_sha"], "edit receipt head"),
        base_sha=_sha(payload["base_sha"], "edit receipt base"),
        workflow_id=_positive_int(
            workflow["id"],
            "edit receipt workflow id",
        ),
        workflow_path=WORKFLOW_PATH,
        nonce=nonce,
        pre_metadata_sha256=payload["pre_metadata_sha256"],
        pre_fields=tuple(parsed_pre_fields),
        target_metadata_sha256=payload["target_metadata_sha256"],
        pre_version=_parse_metadata_version_payload(
            payload["pre_version"],
            label="edit receipt pre_version",
        ),
        provided_fields=tuple(provided_fields),
        changed_fields=tuple(changed_fields),
        watermark_run_id=_positive_int(
            watermark["run_id"],
            "edit receipt watermark run_id",
        ),
        watermark_run_number=_positive_int(
            watermark["run_number"],
            "edit receipt watermark run_number",
        ),
        watermark_created_at=_timestamp_text(watermark_created_at),
    )


def _parse_edit_confirmation(payload: object) -> EditConfirmation:
    if not isinstance(payload, dict) or set(payload) != {
        "base_sha",
        "head_sha",
        "intent_comment_id",
        "intent_nonce",
        "metadata_sha256",
        "metadata_version",
        "pr_number",
        "repository",
        "repository_id",
        "schema_version",
    }:
        raise MetadataEditError("edit confirmation shape is invalid")
    if (
        isinstance(payload["schema_version"], bool)
        or not isinstance(payload["schema_version"], int)
        or payload["schema_version"] != 1
    ):
        raise MetadataEditError("edit confirmation schema_version is invalid")
    repository = payload["repository"]
    if not isinstance(repository, str):
        raise MetadataEditError("edit confirmation repository is invalid")
    repository = _repository(repository)
    nonce = payload["intent_nonce"]
    digest = payload["metadata_sha256"]
    if not isinstance(nonce, str) or DIGEST_RE.fullmatch(nonce) is None:
        raise MetadataEditError("edit confirmation intent nonce is invalid")
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise MetadataEditError("edit confirmation metadata digest is invalid")
    return EditConfirmation(
        schema_version=1,
        repository=repository,
        repository_id=_positive_int(
            payload["repository_id"],
            "edit confirmation repository_id",
        ),
        pr_number=_positive_int(
            payload["pr_number"],
            "edit confirmation pr_number",
        ),
        head_sha=_sha(payload["head_sha"], "edit confirmation head"),
        base_sha=_sha(payload["base_sha"], "edit confirmation base"),
        intent_comment_id=_positive_int(
            payload["intent_comment_id"],
            "edit confirmation intent_comment_id",
        ),
        intent_nonce=nonce,
        metadata_sha256=digest,
        metadata_version=_parse_metadata_version_payload(
            payload["metadata_version"],
            label="edit confirmation",
        ),
    )


def _parse_edit_abort(payload: object) -> EditAbort:
    expected = {
        "intent_base_sha",
        "intent_comment_id",
        "intent_head_sha",
        "intent_nonce",
        "observed_base_sha",
        "observed_head_sha",
        "observed_metadata_sha256",
        "observed_version",
        "pr_number",
        "reason",
        "repository",
        "repository_id",
        "schema_version",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise MetadataEditError("edit abort shape is invalid")
    if payload["schema_version"] != 1:
        raise MetadataEditError("edit abort schema_version is invalid")
    reason = payload["reason"]
    if not isinstance(reason, str) or reason not in {
        "candidate-drift",
        "metadata-version-drift",
        "patch-rejected",
        "pre-state-drift",
        "run-authority-drift",
        "transaction-drift",
    }:
        raise MetadataEditError("edit abort reason is invalid")
    nonce = payload["intent_nonce"]
    digest = payload["observed_metadata_sha256"]
    repository = payload["repository"]
    if not isinstance(repository, str):
        raise MetadataEditError("edit abort repository is invalid")
    if not isinstance(nonce, str) or DIGEST_RE.fullmatch(nonce) is None:
        raise MetadataEditError("edit abort nonce is invalid")
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise MetadataEditError("edit abort metadata digest is invalid")
    return EditAbort(
        schema_version=1,
        repository=_repository(repository),
        repository_id=_positive_int(
            payload["repository_id"],
            "edit abort repository_id",
        ),
        pr_number=_positive_int(payload["pr_number"], "edit abort pr_number"),
        intent_comment_id=_positive_int(
            payload["intent_comment_id"],
            "edit abort intent_comment_id",
        ),
        intent_nonce=nonce,
        intent_head_sha=_sha(
            payload["intent_head_sha"],
            "edit abort intent head",
        ),
        intent_base_sha=_sha(
            payload["intent_base_sha"],
            "edit abort intent base",
        ),
        observed_head_sha=_sha(
            payload["observed_head_sha"],
            "edit abort observed head",
        ),
        observed_base_sha=_sha(
            payload["observed_base_sha"],
            "edit abort observed base",
        ),
        observed_metadata_sha256=digest,
        observed_version=_parse_metadata_version_payload(
            payload["observed_version"],
            label="edit abort observed version",
        ),
        reason=reason,
    )


def _split_http_parameters(value: str, *, label: str) -> list[str]:
    parts = []
    start = 0
    quoted = False
    for index, character in enumerate(value):
        if character == "\\":
            raise MetadataEditError(
                f"{label} Content-Type backslashes are forbidden"
            )
        if character == '"':
            quoted = not quoted
            continue
        if character == ";" and not quoted:
            parts.append(value[start:index])
            start = index + 1
    if quoted:
        raise MetadataEditError(f"{label} Content-Type quotation is invalid")
    parts.append(value[start:])
    return parts


def _is_http_token(value: str) -> bool:
    return bool(value) and HEADER_NAME_RE.fullmatch(value) is not None


def _valid_quoted_http_value(value: str) -> bool:
    if len(value) < 3 or value[0] != '"' or value[-1] != '"':
        return False
    for character in value[1:-1]:
        if character in {'"', "\\"}:
            return False
    return True


def _split_http_parameter(
    raw_parameter: str,
    *,
    label: str,
) -> tuple[str, str]:
    if not raw_parameter:
        raise MetadataEditError(f"{label} Content-Type parameter is empty")
    if raw_parameter.startswith(" "):
        raw_parameter = raw_parameter[1:]
        if not raw_parameter or raw_parameter.startswith(" "):
            raise MetadataEditError(
                f"{label} Content-Type parameter spacing is ambiguous"
            )
    quoted = False
    equals_index = None
    for index, character in enumerate(raw_parameter):
        if character == "\\":
            raise MetadataEditError(
                f"{label} Content-Type backslashes are forbidden"
            )
        if character == '"':
            quoted = not quoted
            continue
        if character == " " and not quoted:
            raise MetadataEditError(
                f"{label} Content-Type parameter spacing is ambiguous"
            )
        if character == "=" and not quoted:
            if equals_index is not None:
                raise MetadataEditError(
                    f"{label} Content-Type parameter has extra equals"
                )
            equals_index = index
    if quoted:
        raise MetadataEditError(f"{label} Content-Type quotation is invalid")
    if equals_index is None:
        raise MetadataEditError(f"{label} Content-Type parameter lacks equals")
    name = raw_parameter[:equals_index]
    value = raw_parameter[equals_index + 1 :]
    if not name or not value:
        raise MetadataEditError(f"{label} Content-Type parameter is empty")
    return name, value


def _validate_json_media_type(value: str, *, label: str) -> None:
    parts = _split_http_parameters(value, label=label)
    if parts[0].lower() != "application/json":
        raise MetadataEditError(f"{label} response Content-Type is not application/json")
    parameters = set()
    for raw_parameter in parts[1:]:
        name, raw_value = _split_http_parameter(
            raw_parameter,
            label=label,
        )
        name = name.lower()
        if (
            not _is_http_token(name)
            or name in parameters
            or not (
                _is_http_token(raw_value)
                or _valid_quoted_http_value(raw_value)
            )
        ):
            raise MetadataEditError(f"{label} Content-Type parameter is invalid")
        parameters.add(name)


def _normalize_http_headers(
    lines: list[str],
    *,
    label: str,
) -> dict[str, str]:
    collected: dict[str, list[str]] = {}
    for line in lines:
        if not line or line[0].isspace() or ":" not in line:
            raise MetadataEditError(f"{label} response header is invalid")
        name, value = line.split(":", 1)
        if HEADER_NAME_RE.fullmatch(name) is None:
            raise MetadataEditError(f"{label} response header name is invalid")
        if value and not value.startswith(" "):
            raise MetadataEditError(f"{label} response header lacks SP separator")
        key = name.lower()
        collected.setdefault(key, []).append(value.strip(" "))

    normalized = {}
    for name, values in collected.items():
        policy = COMBINABLE_HEADER_POLICIES.get(
            name,
            SINGLETON_HEADER_POLICY,
        )
        if len(values) > 1 and not policy.repeatable:
            raise MetadataEditError(
                f"{label} response repeats singleton header {name!r}"
            )
        if policy.repeatable:
            if any(not value for value in values):
                raise MetadataEditError(
                    f"{label} response has an empty combinable header {name!r}"
                )
            normalized[name] = policy.separator.join(values)
        else:
            normalized[name] = values[0]
    return normalized


def _parse_http_response(
    raw: str,
    *,
    label: str,
    allow_empty_body: bool,
    allow_gh_status_line: bool = False,
    allow_comment_location: bool = False,
) -> ApiResponse:
    if len(raw.encode("utf-8")) > MAX_API_BYTES:
        raise MetadataEditError(f"{label} response exceeds 4 MiB")
    crlf_boundary = raw.find("\r\n\r\n")
    lf_boundary = raw.find("\n\n")
    gh_status_line = None
    if crlf_boundary >= 0 and (lf_boundary < 0 or crlf_boundary <= lf_boundary):
        line_break = "\r\n"
        boundary = crlf_boundary
        header_text = raw[:boundary]
        first_lf = header_text.find("\n")
        if (
            allow_gh_status_line
            and first_lf > 0
            and header_text[first_lf - 1] != "\r"
        ):
            gh_status_line = header_text[:first_lf]
            header_text = header_text[first_lf + 1 :]
        for index, character in enumerate(header_text):
            if character == "\r" and (
                index + 1 >= len(header_text)
                or header_text[index + 1] != "\n"
            ):
                raise MetadataEditError(
                    f"{label} response contains a bare carriage return"
                )
            if character == "\n" and (
                index == 0 or header_text[index - 1] != "\r"
            ):
                raise MetadataEditError(
                    f"{label} response mixes HTTP line endings"
                )
    else:
        line_break = "\n"
        boundary = lf_boundary
        if boundary < 0:
            raise MetadataEditError(f"{label} response lacks HTTP headers")
        header_text = raw[:boundary]
        if "\r" in header_text:
            raise MetadataEditError(
                f"{label} response contains a bare carriage return"
            )
    boundary_marker = line_break + line_break
    body_text = raw[boundary + len(boundary_marker) :]
    lines = header_text.split(line_break)
    if gh_status_line is not None:
        lines.insert(0, gh_status_line)
    for line in lines:
        for character in line:
            code = ord(character)
            if code < 0x20 or code > 0x7E:
                raise MetadataEditError(
                    f"{label} response header contains a control or non-ASCII byte"
                )
    status_match = HTTP_STATUS_RE.fullmatch(lines[0]) if lines else None
    if status_match is None:
        raise MetadataEditError(f"{label} response status line is invalid")
    status = int(status_match.group(1))
    headers = _normalize_http_headers(lines[1:], label=label)
    if 300 <= status < 400:
        raise MetadataEditError(f"{label} request rejected redirect: HTTP {status}")
    if "location" in headers and not (status == 201 and allow_comment_location):
        raise MetadataEditError(f"{label} response unexpectedly contains Location")
    if not body_text:
        if allow_empty_body:
            return ApiResponse(status, headers, None)
        raise MetadataEditError(f"{label} response body is empty")
    content_type = headers.get("content-type", "")
    _validate_json_media_type(content_type, label=label)
    return ApiResponse(status, headers, _parse_json(body_text, label))


Runner = Callable[..., subprocess.CompletedProcess[bytes]]


class GitHubClient:
    def __init__(
        self,
        gh_path: str,
        *,
        runner: Runner = subprocess.run,
    ) -> None:
        self.gh_path = str(Path(gh_path).resolve(strict=True))
        self.runner = runner

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        body: dict[str, object] | None = None,
        label: str,
    ) -> ApiResponse:
        if method not in {"GET", "PATCH", "POST"}:
            raise MetadataEditError("unsupported GitHub API method")
        arguments = [
            self.gh_path,
            "api",
            "--hostname",
            "github.com",
            "--include",
            "--method",
            method,
            "--header",
            "Accept: application/vnd.github+json",
            "--header",
            "X-GitHub-Api-Version: 2022-11-28",
            endpoint,
        ]
        input_bytes = None
        if body is not None:
            arguments.extend(["--input", "-"])
            try:
                input_bytes = json.dumps(
                    body,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            except UnicodeEncodeError as error:
                raise MetadataEditError(f"{label} request body is not UTF-8") from error
        environment = dict(os.environ)
        environment["GH_HOST"] = "github.com"
        environment.pop("GH_REPO", None)
        try:
            completed = self.runner(
                arguments,
                check=False,
                capture_output=True,
                text=False,
                input=input_bytes,
                env=environment,
                timeout=30,
            )
        except subprocess.TimeoutExpired as error:
            raise MetadataEditError(f"{label} request timed out") from error
        except OSError as error:
            raise MetadataEditError(f"{label} request could not start: {error}") from error
        if not isinstance(completed.stdout, bytes) or not isinstance(completed.stderr, bytes):
            raise MetadataEditError(f"{label} subprocess must preserve output bytes")
        if len(completed.stdout) > MAX_API_BYTES or len(completed.stderr) > MAX_API_BYTES:
            raise MetadataEditError(f"{label} subprocess output exceeds 4 MiB")
        try:
            output = completed.stdout.decode("utf-8")
            diagnostic = completed.stderr.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MetadataEditError(f"{label} subprocess output is not UTF-8") from error
        detail = diagnostic.strip()
        if len(detail) > 512:
            detail = detail[:512] + "..."
        failure = f"{label} request failed" + (f": {detail}" if detail else "")
        if completed.returncode not in (0, 1) or (
            completed.returncode != 0 and not output
        ):
            raise MetadataEditError(failure)
        response = _parse_http_response(
            output,
            label=label,
            allow_empty_body=method == "POST",
            allow_gh_status_line=True,
            allow_comment_location=(
                method == "POST" and COMMENT_CREATION_RE.fullmatch(endpoint) is not None
            ),
        )
        expected_status = (
            200 if endpoint == "graphql" else 201 if method == "POST" else 200
        )
        if response.status != expected_status:
            raise GitHubHTTPError(
                method, endpoint, response,
                label=label, expected_status=expected_status,
            )
        if completed.returncode != 0:
            raise MetadataEditError(failure)
        return response


def _endpoint(repository: str, suffix: str) -> str:
    owner, name = repository.split("/", 1)
    return (
        f"repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(name, safe='')}/{suffix}"
    )


def _query_endpoint(repository: str, suffix: str, pairs: list[tuple[str, str]]) -> str:
    return _endpoint(repository, suffix) + "?" + urllib.parse.urlencode(pairs)


def _api_url(endpoint: str) -> str:
    return "https://api.github.com/" + endpoint.lstrip("/")


def _require_api_url(value: object, endpoint: str, *, field: str) -> None:
    if value != _api_url(endpoint):
        raise MetadataEditError(f"{field} identity drifted")


def _parse_pull_request_payload(
    payload: object,
    repository: str,
    pr_number: int,
    *,
    allow_closed: bool = False,
) -> PullRequestState:
    if not isinstance(payload, dict):
        raise MetadataEditError("pull request response must be an object")
    if _positive_int(payload.get("number"), "pull request number") != pr_number:
        raise MetadataEditError("pull request number drifted")
    pull_request_id = _positive_int(payload.get("id"), "pull request id")
    pull_request_node_id = _text(
        payload.get("node_id"),
        "pull request node id",
    )
    _require_api_url(
        payload.get("url"),
        _endpoint(repository, f"pulls/{pr_number}"),
        field="pull request URL",
    )
    allowed_states = ("open", "closed") if allow_closed else ("open",)
    if payload.get("state") not in allowed_states:
        raise MetadataEditError(
            "pull request state must be open or closed"
            if allow_closed
            else "pull request must be open"
        )
    head = payload.get("head")
    base = payload.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise MetadataEditError("pull request head/base identity is missing")
    base_repo = base.get("repo")
    if (
        not isinstance(base_repo, dict)
        or base_repo.get("full_name") != repository
    ):
        raise MetadataEditError("pull request repository identity drifted")
    repository_id = _positive_int(
        base_repo.get("id"),
        "pull request repository id",
    )
    owner, name = repository.split("/", 1)
    if (
        base_repo.get("name") != name
        or base_repo.get("private") is not False
        or base_repo.get("url") != _api_url(_endpoint(repository, ""))
        .rstrip("/")
        or base_repo.get("html_url") != f"https://github.com/{repository}"
    ):
        raise MetadataEditError("pull request repository payload is invalid")
    repo_owner = base_repo.get("owner")
    if (
        not isinstance(repo_owner, dict)
        or repo_owner.get("login") != owner
        or repo_owner.get("type") != "User"
        or repo_owner.get("site_admin") is not False
    ):
        raise MetadataEditError("pull request repository owner is invalid")
    repository_owner_id = _positive_int(
        repo_owner.get("id"),
        "pull request repository owner id",
    )
    head_ref = _text(head.get("ref"), "pull request head ref")
    base_ref = _text(base.get("ref"), "pull request base ref")
    title = _text(payload.get("title"), "pull request title")
    body = payload.get("body")
    if "body" not in payload or (body is not None and not isinstance(body, str)):
        raise MetadataEditError("pull request body must be text or null")
    if body is None:
        body = ""
    updated_at = _github_timestamp(
        payload.get("updated_at"),
        "pull request updated_at",
    )
    return PullRequestState(
        repository=repository,
        repository_id=repository_id,
        repository_owner_id=repository_owner_id,
        pull_request_id=pull_request_id,
        pull_request_node_id=pull_request_node_id,
        number=pr_number,
        head_sha=_sha(head.get("sha"), "pull request head"),
        head_ref=head_ref,
        base_sha=_sha(base.get("sha"), "pull request base"),
        base_ref=base_ref,
        title=title,
        body=body,
        updated_at=updated_at,
    )


def fetch_pull_request(
    client: GitHubClient,
    repository: str,
    pr_number: int,
) -> PullRequestState:
    response = client.request(
        "GET",
        _endpoint(repository, f"pulls/{pr_number}"),
        label="pull request",
    )
    return _parse_pull_request_payload(response.payload, repository, pr_number)


def inspect_metadata_history(
    client: GitHubClient,
    repository: str,
    pr_number: int,
) -> MetadataVersion:
    """Read metadata history without requiring a mutable, open pull request."""
    response = client.request(
        "GET",
        _endpoint(repository, f"pulls/{pr_number}"),
        label="metadata history pull request",
    )
    state = _parse_pull_request_payload(
        response.payload,
        repository,
        pr_number,
        allow_closed=True,
    )
    return fetch_metadata_version(client, state)


def require_identity(
    state: PullRequestState,
    *,
    head_sha: str,
    base_sha: str,
) -> None:
    if state.head_sha != head_sha or state.base_sha != base_sha:
        raise MetadataEditError(
            "pull request identity changed; rerun with the current exact head/base"
        )


def _graphql_user(
    raw: object,
    *,
    label: str,
    required: bool,
) -> tuple[int | None, str | None]:
    if raw is None and not required:
        return None, None
    if (
        not isinstance(raw, dict)
        or raw.get("__typename") != "User"
        or not isinstance(raw.get("login"), str)
        or not raw["login"]
    ):
        raise MetadataEditError(f"{label} actor is not the repository owner")
    return (
        _positive_int(raw.get("databaseId"), f"{label} actor id"),
        raw["login"],
    )


def _parse_body_edit_node(
    raw: object,
    *,
    label: str,
) -> tuple[str, str, str, str, int, str, str]:
    if not isinstance(raw, dict) or set(raw) != {
        "createdAt",
        "deletedAt",
        "diff",
        "editedAt",
        "editor",
        "id",
        "updatedAt",
    }:
        raise MetadataEditError(f"{label} shape is invalid")
    edit_id = _text(raw.get("id"), f"{label} id")
    created_at = _timestamp_text(
        _github_timestamp(raw.get("createdAt"), f"{label} createdAt")
    )
    edited_at = _timestamp_text(
        _github_timestamp(raw.get("editedAt"), f"{label} editedAt")
    )
    updated_at = _timestamp_text(
        _github_timestamp(raw.get("updatedAt"), f"{label} updatedAt")
    )
    if not (edited_at <= created_at <= updated_at):
        raise MetadataEditError(f"{label} chronology is invalid")
    if raw.get("deletedAt") is not None:
        raise MetadataEditError(f"{label} is deleted")
    diff = raw.get("diff")
    if not isinstance(diff, str) or len(diff.encode("utf-8")) > MAX_BODY_BYTES:
        raise MetadataEditError(f"{label} diff is invalid")
    editor_id, editor_login = _graphql_user(
        raw.get("editor"),
        label=label,
        required=True,
    )
    if editor_id is None or editor_login is None:
        raise MetadataEditError(f"{label} editor is missing")
    return (
        edit_id,
        created_at,
        edited_at,
        updated_at,
        editor_id,
        editor_login,
        diff,
    )


def _body_edit_version(
    pull: dict[str, object],
    state: PullRequestState,
) -> tuple[
    int, str | None, str | None, str | None, str | None,
    int | None, str | None, BodyOriginal | None,
]:
    connection = pull.get("userContentEdits")
    if not isinstance(connection, dict) or set(connection) != {
        "nodes",
        "pageInfo",
        "totalCount",
    }:
        raise MetadataEditError("body edit history connection is missing or invalid")
    total_count = connection["totalCount"]
    if (
        isinstance(total_count, bool)
        or not isinstance(total_count, int)
        or total_count < 0
        or total_count == 1
        or total_count > 999999999
    ):
        raise MetadataEditError("body edit history totalCount is invalid")
    nodes = connection["nodes"]
    page_info = connection["pageInfo"]
    if not isinstance(nodes, list) or len(nodes) != min(total_count, 2):
        raise MetadataEditError("body edit history cardinality is invalid")
    if not isinstance(page_info, dict) or set(page_info) != {
        "endCursor",
        "hasNextPage",
        "hasPreviousPage",
        "startCursor",
    }:
        raise MetadataEditError("body edit history pageInfo is invalid")
    has_next = page_info["hasNextPage"]
    has_previous = page_info["hasPreviousPage"]
    start_cursor = page_info["startCursor"]
    end_cursor = page_info["endCursor"]
    if not isinstance(has_next, bool) or not isinstance(has_previous, bool):
        raise MetadataEditError("body edit history pagination flags are invalid")
    if has_previous or has_next != (total_count > 2):
        raise MetadataEditError("body edit history pagination is noncanonical")
    last_edited_at = pull.get("lastEditedAt")
    pull_editor = pull.get("editor")
    if total_count == 0:
        if (
            last_edited_at is not None
            or pull_editor is not None
            or start_cursor is not None
            or end_cursor is not None
        ):
            raise MetadataEditError("body no-edit authority is inconsistent")
        return 0, None, None, None, None, None, None, None
    if (
        not isinstance(start_cursor, str)
        or not start_cursor
        or len(start_cursor.encode("utf-8")) > 1024
        or not isinstance(end_cursor, str)
        or not end_cursor
        or len(end_cursor.encode("utf-8")) > 1024
    ):
        raise MetadataEditError("body edit history cursors are invalid")
    if (total_count == 1) != (start_cursor == end_cursor):
        raise MetadataEditError("body edit history cursor range is invalid")
    parsed = [
        _parse_body_edit_node(
            raw,
            label=f"body edit node {index}",
        )
        for index, raw in enumerate(nodes)
    ]
    if len({node[0] for node in parsed}) != len(parsed):
        raise MetadataEditError("body edit history repeats a node identity")
    latest = parsed[0]
    if len(parsed) == 2 and parsed[1][2] >= latest[2]:
        raise MetadataEditError("latest body edit ordering is ambiguous")
    last_edited_at = _timestamp_text(
        _github_timestamp(last_edited_at, "pull request body lastEditedAt")
    )
    pull_editor_id, pull_editor_login = _graphql_user(
        pull_editor,
        label="pull request body",
        required=True,
    )
    if (
        latest[2] != last_edited_at
        or latest[4] != pull_editor_id
        or latest[5] != pull_editor_login
        or latest[6] != (state.body or "")
    ):
        raise MetadataEditError(
            "latest body edit is inconsistent with the pull request"
        )
    body_original = None
    if total_count == 2:
        original = parsed[1]
        authored_at = _timestamp_text(
            _github_timestamp(pull.get("createdAt"), "pull request createdAt")
        )
        author_id, author_login = _graphql_user(
            pull.get("author"), label="pull request author", required=True
        )
        # GitHub materializes the original snapshot with the first real edit.
        if (
            original[2] != authored_at
            or (original[4], original[5]) != (author_id, author_login)
            or not (
                original[1] == original[3] == latest[1] == latest[3]
            )
        ):
            raise MetadataEditError("original body snapshot authority is inconsistent")
        body_original = BodyOriginal(
            original[0],
            _field_state_digest(original[6]),
            original[4],
            original[5],
            original[2],
            original[1],
        )
    return (
        total_count,
        latest[0],
        latest[1],
        latest[2],
        latest[3],
        latest[4],
        latest[5],
        body_original,
    )


def _fetch_metadata_observation(
    client: GitHubClient,
    state: PullRequestState,
) -> tuple[PullRequestState, MetadataVersion]:
    owner, name = state.repository.split("/", 1)
    response = client.request(
        "POST",
        "graphql",
        body={
            "query": METADATA_VERSION_QUERY,
            "variables": {
                "name": name,
                "number": state.number,
                "owner": owner,
            },
        },
        label="pull request metadata version",
    )
    payload = response.payload
    if not isinstance(payload, dict) or set(payload) != {"data"}:
        raise MetadataEditError("metadata version response shape is invalid")
    data = payload["data"]
    repository = data.get("repository") if isinstance(data, dict) else None
    if (
        not isinstance(repository, dict)
        or repository.get("databaseId") != state.repository_id
        or repository.get("nameWithOwner") != state.repository
    ):
        raise MetadataEditError("metadata version repository identity drifted")
    owner_id, owner_login = _graphql_user(
        repository.get("owner"),
        label="metadata version repository owner",
        required=True,
    )
    if (
        owner_id != state.repository_owner_id
        or owner_login != state.repository.split("/", 1)[0]
    ):
        raise MetadataEditError("metadata version repository owner drifted")
    pull = repository.get("pullRequest")
    if (
        not isinstance(pull, dict)
        or pull.get("databaseId") != state.pull_request_id
        or pull.get("id") != state.pull_request_node_id
        or pull.get("number") != state.number
        or pull.get("url")
        != f"https://github.com/{state.repository}/pull/{state.number}"
        or not isinstance(pull.get("body"), str)
    ):
        raise MetadataEditError("metadata version pull request identity drifted")
    observed = replace(
        state,
        head_sha=_sha(pull.get("headRefOid"), "metadata head"),
        head_ref=_text(pull.get("headRefName"), "metadata head ref"),
        base_sha=_sha(pull.get("baseRefOid"), "metadata base"),
        base_ref=_text(pull.get("baseRefName"), "metadata base ref"),
        title=_text(pull.get("title"), "metadata title"),
        body=pull["body"],
        updated_at=_github_timestamp(pull.get("updatedAt"), "metadata updatedAt"),
    )
    (
        body_edit_total_count,
        body_edit_id,
        body_edit_created_at,
        body_last_edited_at,
        body_edit_updated_at,
        body_editor_id,
        body_editor_login,
        body_original,
    ) = _body_edit_version(pull, observed)
    timeline = pull.get("timelineItems")
    nodes = timeline.get("nodes") if isinstance(timeline, dict) else None
    if not isinstance(nodes, list) or len(nodes) > 100:
        raise MetadataEditError("title event history is invalid")
    events = []
    seen_ids = set()
    for raw in nodes:
        if not isinstance(raw, dict) or raw.get("__typename") != "RenamedTitleEvent":
            raise MetadataEditError("title event history contains an invalid node")
        event_id = _text(raw.get("id"), "title event id")
        if event_id in seen_ids:
            raise MetadataEditError("title event history repeats an identity")
        seen_ids.add(event_id)
        created_at = _timestamp_text(
            _github_timestamp(raw.get("createdAt"), "title event createdAt")
        )
        previous_title = _text(raw.get("previousTitle"), "previous title")
        current_title = _text(raw.get("currentTitle"), "current title")
        actor_id, actor_login = _graphql_user(
            raw.get("actor"),
            label="title event",
            required=False,
        )
        events.append(
            (
                created_at,
                event_id,
                previous_title,
                current_title,
                actor_id,
                actor_login,
            )
        )
    if not events:
        return observed, MetadataVersion(
            None,
            None,
            None,
            None,
            None,
            None,
            body_last_edited_at,
            body_editor_id,
            body_editor_login,
            body_edit_total_count,
            body_edit_id,
            body_edit_created_at,
            body_last_edited_at,
            body_edit_updated_at,
            body_original,
        )
    newest_time = max(event[0] for event in events)
    newest = [event for event in events if event[0] == newest_time]
    if len(newest) != 1:
        raise MetadataEditError("latest title event is ambiguous")
    (
        created_at,
        event_id,
        previous_title,
        current_title,
        actor_id,
        actor_login,
    ) = newest[0]
    if current_title != observed.title:
        raise MetadataEditError("latest title event does not attest current title")
    return observed, MetadataVersion(
        event_id,
        created_at,
        previous_title,
        current_title,
        actor_id,
        actor_login,
        body_last_edited_at,
        body_editor_id,
        body_editor_login,
        body_edit_total_count,
        body_edit_id,
        body_edit_created_at,
        body_last_edited_at,
        body_edit_updated_at,
        body_original,
    )


def fetch_metadata_version(
    client: GitHubClient,
    state: PullRequestState,
) -> MetadataVersion:
    observed, version = _fetch_metadata_observation(client, state)
    if (
        not _same_pr_contract(observed, state)
        or observed.title != state.title
        or observed.body != state.body
    ):
        raise MetadataEditError("metadata version pull request identity drifted")
    return version


def _page_count(total_count: int) -> int:
    return max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)


def _expected_page_items(total_count: int, page: int) -> int:
    pages = _page_count(total_count)
    if page > pages:
        raise MetadataEditError("pagination exceeded total_count")
    if page < pages:
        return PAGE_SIZE
    return total_count - PAGE_SIZE * (pages - 1)


def _parse_link_pages(
    link: str,
    *,
    endpoint_for_page: Callable[[int], str],
    current_page: int,
    label: str,
    repository: str,
    repository_id: int,
) -> dict[str, int]:
    if not link:
        return {}
    if len(link.encode("utf-8")) > 8192:
        raise MetadataEditError(f"{label} Link header exceeds bounds")
    relations = {}
    position = 0
    while position < len(link):
        match = LINK_PART_RE.match(link, position)
        if match is None:
            raise MetadataEditError(f"{label} Link header is malformed")
        url, relation = match.groups()
        if relation not in {"first", "last", "next", "prev"}:
            raise MetadataEditError(f"{label} Link header has an unknown relation")
        if relation in relations:
            raise MetadataEditError(f"{label} Link header repeats a relation")
        split = urllib.parse.urlsplit(url)
        if (
            split.scheme != "https"
            or split.netloc != "api.github.com"
            or split.username is not None
            or split.password is not None
            or split.fragment
            or "%" in split.query
        ):
            raise MetadataEditError(f"{label} Link relation escaped api.github.com")
        try:
            query = urllib.parse.parse_qs(
                split.query,
                keep_blank_values=True,
                strict_parsing=True,
            )
        except ValueError as error:
            raise MetadataEditError(f"{label} Link query is malformed") from error
        if "page" not in query or len(query["page"]) != 1:
            raise MetadataEditError(f"{label} Link page is missing or repeated")
        page_text = query["page"][0]
        if not page_text.isascii() or not page_text.isdigit() or page_text.startswith("0"):
            raise MetadataEditError(f"{label} Link page is invalid")
        page = int(page_text)
        if page < 1 or page > MAX_RUN_PAGES:
            raise MetadataEditError(f"{label} Link page exceeds bounds")
        expected = urllib.parse.urlsplit(_api_url(endpoint_for_page(page)))
        owner, name = repository.split("/", 1)
        repo_prefix = (
            f"/repos/{urllib.parse.quote(owner, safe='')}/"
            f"{urllib.parse.quote(name, safe='')}/"
        )
        if not expected.path.startswith(repo_prefix):
            raise MetadataEditError(f"{label} requested endpoint escaped repository")
        suffix = expected.path[len(repo_prefix) :]
        allowed_paths = {
            expected.path,
            f"/repositories/{repository_id}/{suffix}",
        }
        if (
            split.path not in allowed_paths
            or "%" in split.path
            or "//" in split.path
            or "/./" in split.path
            or "/../" in split.path
        ):
            raise MetadataEditError(f"{label} Link path drifted")
        expected_query = urllib.parse.parse_qs(
            expected.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
        if query != expected_query:
            raise MetadataEditError(f"{label} Link query drifted")
        relations[relation] = page
        position = match.end()
    if relations.get("next") == current_page:
        raise MetadataEditError(f"{label} Link pagination loops")
    return relations


def _require_counted_link_contract(
    relations: dict[str, int],
    *,
    page: int,
    total_pages: int,
    label: str,
) -> None:
    if total_pages == 1:
        expected = set()
    elif page == 1:
        expected = {"next", "last"}
    elif page < total_pages:
        expected = {"first", "last", "next", "prev"}
    else:
        expected = {"first", "prev"}
    if set(relations) != expected:
        raise MetadataEditError(f"{label} Link relations are noncanonical")
    expected_values = {
        "first": 1,
        "last": total_pages,
        "next": page + 1,
        "prev": page - 1,
    }
    for relation, relation_page in relations.items():
        if relation_page != expected_values[relation]:
            raise MetadataEditError(f"{label} Link {relation} page drifted")


def _list_counted_pages(
    client: GitHubClient,
    *,
    endpoint_for_page: Callable[[int], str],
    item_key: str,
    label: str,
    maximum: int,
    repository: str,
    repository_id: int,
) -> list[object]:
    items: list[object] = []
    expected_total = None
    pages = None
    for page in range(1, MAX_RUN_PAGES + 1):
        response = client.request(
            "GET",
            endpoint_for_page(page),
            label=f"{label} page {page}",
        )
        payload = response.payload
        if (
            not isinstance(payload, dict)
            or set(payload) != {"total_count", item_key}
        ):
            raise MetadataEditError(f"{label} response shape is invalid")
        total_count = payload["total_count"]
        if (
            isinstance(total_count, bool)
            or not isinstance(total_count, int)
            or total_count < 0
            or total_count > maximum
        ):
            raise MetadataEditError(f"{label} total_count is invalid")
        page_items = payload[item_key]
        if not isinstance(page_items, list):
            raise MetadataEditError(f"{label} items must be a list")
        if expected_total is None:
            expected_total = total_count
            pages = _page_count(total_count)
        elif total_count != expected_total:
            raise MetadataEditError(f"{label} total_count changed across pages")
        if len(page_items) != _expected_page_items(total_count, page):
            raise MetadataEditError(f"{label} page cardinality is incomplete")
        relations = _parse_link_pages(
            response.headers.get("link", ""),
            endpoint_for_page=endpoint_for_page,
            current_page=page,
            label=label,
            repository=repository,
            repository_id=repository_id,
        )
        _require_counted_link_contract(
            relations,
            page=page,
            total_pages=pages,
            label=label,
        )
        items.extend(page_items)
        if page == pages:
            break
    else:
        raise MetadataEditError(f"{label} exceeds the pagination bound")
    if expected_total is None or len(items) != expected_total:
        raise MetadataEditError(f"{label} pagination is incomplete")
    return items


def _workflow_authority(
    client: GitHubClient,
    state: PullRequestState,
) -> WorkflowAuthority:
    response = client.request(
        "GET",
        _endpoint(state.repository, "actions/workflows/build.yml"),
        label="Build workflow",
    )
    payload = response.payload
    if not isinstance(payload, dict):
        raise MetadataEditError("Build workflow response must be an object")
    workflow_id = _positive_int(payload.get("id"), "Build workflow id")
    name = _text(payload.get("name"), "Build workflow name")
    path = _text(payload.get("path"), "Build workflow path")
    if name != "Build CI" or path != WORKFLOW_PATH or payload.get("state") != "active":
        raise MetadataEditError("Build workflow identity drifted")
    _text(payload.get("node_id"), "Build workflow node id")
    _text(payload.get("created_at"), "Build workflow created_at")
    _text(payload.get("updated_at"), "Build workflow updated_at")
    _require_api_url(
        payload.get("url"),
        _endpoint(state.repository, f"actions/workflows/{workflow_id}"),
        field="Build workflow API URL",
    )
    if (
        payload.get("html_url")
        != f"https://github.com/{state.repository}/blob/master/{WORKFLOW_PATH}"
    ):
        raise MetadataEditError("Build workflow HTML URL identity drifted")
    expected_badge = (
        f"https://github.com/{state.repository}/workflows/"
        f"{urllib.parse.quote(name, safe='')}/badge.svg"
    )
    if payload.get("badge_url") != expected_badge:
        raise MetadataEditError("Build workflow badge URL identity drifted")
    return WorkflowAuthority(workflow_id, name, path)


def _validate_job_timing(
    *,
    job_id: int,
    status: str,
    conclusion: str | None,
    assigned: bool,
    created_at: datetime.datetime,
    started_at: datetime.datetime | None,
    completed_at: datetime.datetime | None,
    run_created_at: datetime.datetime,
    run_started_at: datetime.datetime | None,
    run_updated_at: datetime.datetime | None,
) -> None:
    policy = JOB_TIMING_POLICIES.get(status)
    if policy is None:
        raise MetadataEditError(f"Build job {job_id} timing status is unknown")
    if created_at < run_created_at:
        raise MetadataEditError(f"Build job {job_id} predates its workflow run")
    if run_updated_at is not None and created_at > run_updated_at:
        raise MetadataEditError(f"Build job {job_id} is newer than its workflow run")
    if policy.started == "required" and started_at is None:
        raise MetadataEditError(f"Build job {job_id} started_at is required")
    if policy.started == "null" and started_at is not None:
        raise MetadataEditError(f"Build job {job_id} started_at must be null")
    if policy.completed == "required" and completed_at is None:
        raise MetadataEditError(f"Build job {job_id} completed_at is required")
    if policy.completed == "null" and completed_at is not None:
        raise MetadataEditError(f"Build job {job_id} completed_at must be null")
    if started_at is not None:
        if created_at > started_at:
            raise MetadataEditError(f"Build job {job_id} starts before creation")
        if run_updated_at is not None and started_at > run_updated_at:
            raise MetadataEditError(f"Build job {job_id} starts after its workflow run")
        if run_started_at is not None and started_at < run_started_at:
            raise MetadataEditError(f"Build job {job_id} starts before its workflow run")
    if completed_at is None:
        return
    if run_updated_at is not None and completed_at > run_updated_at:
        raise MetadataEditError(f"Build job {job_id} completes after its workflow run")
    if not assigned and conclusion == "skipped":
        if (
            created_at != started_at
            or completed_at
            not in {
                started_at,
                started_at - datetime.timedelta(seconds=1),
            }
        ):
            raise MetadataEditError(f"Build job {job_id} skipped timing is invalid")
        return
    if started_at is None or completed_at < started_at:
        raise MetadataEditError(
            f"Build job {job_id} completion chronology is invalid"
        )


def _metadata_event_step(
    raw: dict[str, object],
    *,
    status: str,
    conclusion: str | None,
    started_at: datetime.datetime | None,
    completed_at: datetime.datetime | None,
) -> str | None:
    steps = raw.get("steps", [])
    if not isinstance(steps, list) or len(steps) > 100:
        raise MetadataEditError("metadata classifier steps are invalid")
    selected = []
    numbers = set()
    for step in steps:
        if not isinstance(step, dict):
            raise MetadataEditError("metadata classifier step is invalid")
        number = _positive_int(step.get("number"), "metadata step number")
        if number in numbers:
            raise MetadataEditError("metadata classifier repeats a step number")
        numbers.add(number)
        name = _text(step.get("name"), "metadata step name")
        if name.startswith(metadata_event.STEP_PREFIX):
            selected.append(step)
    if not selected:
        return None
    if len(selected) != 1:
        raise MetadataEditError("metadata classifier repeats event attestation")
    step = selected[0]
    digest = step["name"][len(metadata_event.STEP_PREFIX):]
    step_status = _text(step.get("status"), "metadata step status")
    step_conclusion = step.get("conclusion")
    if (
        step_status not in ACTIVE_RUN_STATUSES | {"completed"}
        or (step_conclusion is not None and (
            not isinstance(step_conclusion, str) or step_conclusion not in RUN_CONCLUSIONS
        ))
        or ((step_status == "completed") != (step_conclusion is not None))
    ):
        raise MetadataEditError("metadata event attestation step state is invalid")
    if not digest and (
        step_status in ACTIVE_RUN_STATUSES or step_conclusion == "skipped"
    ):
        return None
    if DIGEST_RE.fullmatch(digest) is None:
        raise MetadataEditError("metadata event attestation digest is invalid")
    if status != "completed":
        return None
    if (
        conclusion != "success"
        or step_status != "completed"
        or step_conclusion != "success"
    ):
        raise MetadataEditError("metadata event attestation step did not succeed")
    step_started = _github_timestamp(step.get("started_at"), "metadata step start")
    step_completed = _github_timestamp(step.get("completed_at"), "metadata step completion")
    if (
        started_at is None or completed_at is None
        or not started_at <= step_started <= step_completed <= completed_at
    ):
        raise MetadataEditError("metadata event attestation step chronology is invalid")
    return digest


def _parse_job(
    raw: object,
    *,
    state: PullRequestState,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    head_branch: str,
    workflow: WorkflowAuthority,
    run_created_at: datetime.datetime,
    run_started_at: datetime.datetime | None,
    run_updated_at: datetime.datetime | None,
) -> JobState:
    if not isinstance(raw, dict):
        raise MetadataEditError(f"Build run {run_id} job must be an object")
    job_id = _positive_int(raw.get("id"), f"Build run {run_id} job id")
    if _positive_int(raw.get("run_id"), f"Build job {job_id} run id") != run_id:
        raise MetadataEditError(f"Build job {job_id} run identity drifted")
    if "run_attempt" in raw and (
        _positive_int(raw.get("run_attempt"), f"Build job {job_id} run attempt")
        != run_attempt
    ):
        raise MetadataEditError(f"Build job {job_id} attempt identity drifted")
    if "event" in raw and raw.get("event") != "pull_request":
        raise MetadataEditError(f"Build job {job_id} event identity drifted")
    if _sha(raw.get("head_sha"), f"Build job {job_id} head") != head_sha:
        raise MetadataEditError(f"Build job {job_id} head identity drifted")
    if raw.get("head_branch") != head_branch:
        raise MetadataEditError(f"Build job {job_id} branch identity drifted")
    if raw.get("workflow_name") != workflow.name:
        raise MetadataEditError(f"Build job {job_id} workflow identity drifted")
    _text(raw.get("node_id"), f"Build job {job_id} node id")
    _require_api_url(
        raw.get("run_url"),
        _endpoint(state.repository, f"actions/runs/{run_id}"),
        field=f"Build job {job_id} run URL",
    )
    _require_api_url(
        raw.get("url"),
        _endpoint(state.repository, f"actions/jobs/{job_id}"),
        field=f"Build job {job_id} API URL",
    )
    _require_api_url(
        raw.get("check_run_url"),
        _endpoint(state.repository, f"check-runs/{job_id}"),
        field=f"Build job {job_id} check-run URL",
    )
    if (
        raw.get("html_url")
        != f"https://github.com/{state.repository}/actions/runs/{run_id}/job/{job_id}"
    ):
        raise MetadataEditError(f"Build job {job_id} HTML URL identity drifted")
    name = _text(raw.get("name"), f"Build run {run_id} job name")
    status = _text(raw.get("status"), f"Build run {run_id} job status")
    if status not in ACTIVE_RUN_STATUSES | {"completed"}:
        raise MetadataEditError(f"Build run {run_id} job status is unknown")
    conclusion = raw.get("conclusion")
    if conclusion is not None and (
        not isinstance(conclusion, str) or conclusion not in RUN_CONCLUSIONS
    ):
        raise MetadataEditError(f"Build run {run_id} job conclusion is unknown")
    if status == "completed" and conclusion is None:
        raise MetadataEditError(f"Build run {run_id} completed job lacks a conclusion")
    if status != "completed" and conclusion is not None:
        raise MetadataEditError(f"Build run {run_id} active job has a conclusion")
    runner_name = raw.get("runner_name")
    if runner_name is not None and not isinstance(runner_name, str):
        raise MetadataEditError(f"Build run {run_id} job runner is invalid")
    created_at = _github_timestamp(
        raw.get("created_at"),
        f"Build job {job_id} created_at",
    )
    started_at = _github_timestamp(
        raw.get("started_at"),
        f"Build job {job_id} started_at",
        optional=True,
    )
    completed_at = _github_timestamp(
        raw.get("completed_at"),
        f"Build job {job_id} completed_at",
        optional=True,
    )
    runner_id = raw.get("runner_id")
    runner_group_id = raw.get("runner_group_id")
    runner_group_name = raw.get("runner_group_name")
    if runner_name is None:
        if (
            runner_id is not None
            or runner_group_id is not None
            or runner_group_name is not None
        ):
            raise MetadataEditError(
                f"Build job {job_id} unassigned runner identity is inconsistent"
            )
    else:
        _positive_int(runner_id, f"Build job {job_id} runner id")
        _nonnegative_int(
            runner_group_id,
            f"Build job {job_id} runner group id",
        )
        if not isinstance(runner_group_name, str) or not runner_group_name:
            raise MetadataEditError(
                f"Build job {job_id} runner group name is invalid"
            )
    _validate_job_timing(
        job_id=job_id,
        status=status,
        conclusion=conclusion,
        assigned=runner_name is not None,
        created_at=created_at,
        started_at=started_at,
        completed_at=completed_at,
        run_created_at=run_created_at,
        run_started_at=run_started_at,
        run_updated_at=run_updated_at,
    )
    return JobState(
        job_id,
        run_id,
        name,
        status,
        conclusion,
        runner_name,
        created_at,
        started_at,
        completed_at,
        (
            _metadata_event_step(
                raw, status=status, conclusion=conclusion,
                started_at=started_at, completed_at=completed_at,
            )
            if name == candidate_evidence.METADATA_CLASSIFIER else None
        ),
    )


def _list_jobs(
    client: GitHubClient,
    state: PullRequestState,
    *,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    head_branch: str,
    workflow: WorkflowAuthority,
    run_created_at: datetime.datetime,
    run_started_at: datetime.datetime | None,
    run_updated_at: datetime.datetime | None,
) -> tuple[JobState, ...]:
    raw_jobs = _list_counted_pages(
        client,
        endpoint_for_page=lambda page: _query_endpoint(
            state.repository,
            f"actions/runs/{run_id}/attempts/{run_attempt}/jobs",
            [("per_page", str(PAGE_SIZE)), ("page", str(page))],
        ),
        item_key="jobs",
        label=f"Build run {run_id} jobs",
        maximum=MAX_RUNS,
        repository=state.repository,
        repository_id=state.repository_id,
    )
    jobs = tuple(
        _parse_job(
            raw,
            state=state,
            run_id=run_id,
            run_attempt=run_attempt,
            head_sha=head_sha,
            head_branch=head_branch,
            workflow=workflow,
            run_created_at=run_created_at,
            run_started_at=run_started_at,
            run_updated_at=run_updated_at,
        )
        for raw in raw_jobs
    )
    job_ids = [job.job_id for job in jobs]
    if len(job_ids) != len(set(job_ids)):
        raise MetadataEditError(f"Build run {run_id} repeats a job id")
    names = [job.name for job in jobs]
    if len(names) != len(set(names)):
        raise MetadataEditError(f"Build run {run_id} repeats a job name")
    return jobs


def _run_mode(
    jobs: tuple[JobState, ...],
    *,
    run_id: int,
    status: str,
) -> str:
    names = frozenset(job.name for job in jobs)
    if status == "completed":
        if names == FULL_JOB_NAMES:
            return "full"
        if names == METADATA_JOB_NAMES:
            return "metadata-only"
        raise MetadataEditError(
            f"completed Build run {run_id} has an unknown or mixed job shape"
        )

    classifier_names = names & {
        candidate_evidence.FULL_CLASSIFIER,
        candidate_evidence.METADATA_CLASSIFIER,
    }
    if classifier_names == {candidate_evidence.FULL_CLASSIFIER}:
        return "active-full"
    if classifier_names == {candidate_evidence.METADATA_CLASSIFIER}:
        classifier = next(
            job
            for job in jobs
            if job.name == candidate_evidence.METADATA_CLASSIFIER
        )
        if (
            names <= METADATA_JOB_NAMES
            and classifier.status == "completed"
            and classifier.conclusion == "success"
            and classifier.runner_name
        ):
            return "active-metadata-only"
    return "active-unknown"


def _run_binding(
    raw: dict[str, object],
    *,
    state: PullRequestState,
    run_id: int,
    head_sha: str,
    head_branch: str,
) -> RunBinding:
    bindings = raw.get("pull_requests")
    if bindings is None or bindings == []:
        return "unbound"
    if not isinstance(bindings, list):
        raise MetadataEditError(f"Build run {run_id} PR bindings are invalid")
    if len(bindings) != 1:
        raise MetadataEditError(
            f"Build run {run_id} PR bindings are ambiguous"
        )
    binding = bindings[0]
    if not isinstance(binding, dict):
        raise MetadataEditError(f"Build run {run_id} PR binding is invalid")
    head = binding.get("head")
    base = binding.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise MetadataEditError(f"Build run {run_id} PR binding is incomplete")
    number = _positive_int(binding.get("number"), "Build run PR number")
    binding_head = _sha(head.get("sha"), "Build run PR head")
    binding_base = _sha(base.get("sha"), "Build run PR base")
    if binding_head != head_sha:
        raise MetadataEditError(
            f"Build run {run_id} PR binding contradicts its head"
        )
    if number == state.number and binding_base == state.base_sha:
        if head_branch != state.head_ref:
            raise MetadataEditError(
                f"Build run {run_id} branch identity drifted"
            )
        return "explicit-same"
    return "explicit-other"


def _parse_run(
    client: GitHubClient,
    state: PullRequestState,
    workflow: WorkflowAuthority,
    raw: object,
    *,
    refresh_terminal: bool = True,
) -> tuple[int, int, RunState]:
    if not isinstance(raw, dict):
        raise MetadataEditError("Build workflow run must be an object")
    run_id = _positive_int(raw.get("id"), "Build run id")
    if (
        _positive_int(raw.get("workflow_id"), "Build run workflow_id")
        != workflow.workflow_id
    ):
        raise MetadataEditError(f"Build run {run_id} workflow identity drifted")
    run_number = _positive_int(raw.get("run_number"), "Build run number")
    run_attempt = _positive_int(raw.get("run_attempt"), "Build run attempt")
    if raw.get("event") != "pull_request":
        raise MetadataEditError(f"Build run {run_id} event is not pull_request")
    if _sha(raw.get("head_sha"), f"Build run {run_id} head") != state.head_sha:
        raise MetadataEditError(f"Build run {run_id} head identity drifted")
    head_branch = _text(raw.get("head_branch"), f"Build run {run_id} head branch")
    path = _text(raw.get("path"), f"Build run {run_id} path")
    if path != WORKFLOW_PATH and not path.startswith(WORKFLOW_PATH + "@"):
        raise MetadataEditError(f"Build run {run_id} workflow path drifted")
    url = _text(raw.get("url"), f"Build run {run_id} URL")
    split = urllib.parse.urlsplit(url)
    owner, name = state.repository.split("/", 1)
    expected_path = (
        f"/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(name, safe='')}/actions/runs/{run_id}"
    )
    if (
        split.scheme != "https"
        or split.netloc != "api.github.com"
        or split.username is not None
        or split.password is not None
        or split.path != expected_path
        or split.query
        or split.fragment
    ):
        raise MetadataEditError(f"Build run {run_id} URL identity drifted")
    binding = _run_binding(
        raw,
        state=state,
        run_id=run_id,
        head_sha=state.head_sha,
        head_branch=head_branch,
    )
    status = _text(raw.get("status"), f"Build run {run_id} status")
    if status not in ACTIVE_RUN_STATUSES | {"completed"}:
        raise MetadataEditError(f"Build run {run_id} status is unknown")
    conclusion = raw.get("conclusion")
    if conclusion is not None and (
        not isinstance(conclusion, str) or conclusion not in RUN_CONCLUSIONS
    ):
        raise MetadataEditError(f"Build run {run_id} conclusion is unknown")
    if status == "completed" and conclusion is None:
        raise MetadataEditError(f"Build run {run_id} completed without conclusion")
    if status != "completed" and conclusion is not None:
        raise MetadataEditError(f"Build run {run_id} active with conclusion")
    created_at = _github_timestamp(
        raw.get("created_at"),
        f"Build run {run_id} created_at",
    )
    run_started_at = _github_timestamp(
        raw.get("run_started_at"),
        f"Build run {run_id} run_started_at",
        optional=True,
    )
    updated_at = _github_timestamp(
        raw.get("updated_at"),
        f"Build run {run_id} updated_at",
    )
    if status == "completed":
        if (
            run_started_at is None
            or not (created_at <= run_started_at <= updated_at)
        ):
            raise MetadataEditError(
                f"Build run {run_id} completion chronology is invalid"
            )
    elif status == "in_progress":
        if (
            run_started_at is None
            or created_at > run_started_at
            or run_started_at > updated_at
        ):
            raise MetadataEditError(
                f"Build run {run_id} in-progress chronology is invalid"
            )
    elif run_started_at is not None or created_at > updated_at:
        raise MetadataEditError(f"Build run {run_id} queued chronology is invalid")
    if status == "completed" and refresh_terminal:
        refreshed_response = client.request(
            "GET",
            _endpoint(state.repository, f"actions/runs/{run_id}"),
            label=f"Build run {run_id} terminal refresh",
        )
        refreshed_id, refreshed_number, refreshed = _parse_run(
            client,
            state,
            workflow,
            refreshed_response.payload,
            refresh_terminal=False,
        )
        if (
            refreshed_id != run_id
            or refreshed_number != run_number
            or refreshed.run_attempt != run_attempt
            or refreshed.head_branch != head_branch
            or refreshed.binding != binding
            or refreshed.status != status
            or refreshed.conclusion != conclusion
            or refreshed.created_at != created_at
            or refreshed.run_started_at != run_started_at
            or refreshed.updated_at < updated_at
        ):
            raise MetadataEditError(
                f"Build run {run_id} terminal authority changed during refresh"
            )
        return refreshed_id, refreshed_number, refreshed
    jobs = _list_jobs(
        client,
        state,
        run_id=run_id,
        run_attempt=run_attempt,
        head_sha=state.head_sha,
        head_branch=head_branch,
        workflow=workflow,
        run_created_at=created_at,
        run_started_at=run_started_at,
        run_updated_at=updated_at if status == "completed" else None,
    )
    return (
        run_id,
        run_number,
        RunState(
            run_id=run_id,
            workflow_id=workflow.workflow_id,
            run_number=run_number,
            run_attempt=run_attempt,
            head_branch=head_branch,
            created_at=created_at,
            run_started_at=run_started_at,
            updated_at=updated_at,
            status=status,
            conclusion=conclusion,
            binding=binding,
            mode=_run_mode(jobs, run_id=run_id, status=status),
            jobs=jobs,
        ),
    )


def list_candidate_runs(
    client: GitHubClient,
    state: PullRequestState,
) -> tuple[RunState, ...]:
    workflow = _workflow_authority(client, state)
    raw_runs = _list_counted_pages(
        client,
        endpoint_for_page=lambda page: _query_endpoint(
            state.repository,
            "actions/workflows/build.yml/runs",
            [
                ("event", "pull_request"),
                ("head_sha", state.head_sha),
                ("per_page", str(PAGE_SIZE)),
                ("page", str(page)),
            ],
        ),
        item_key="workflow_runs",
        label="Build workflow runs",
        maximum=MAX_RUNS,
        repository=state.repository,
        repository_id=state.repository_id,
    )
    visible = tuple(
        _parse_run(
            client,
            state,
            workflow,
            raw,
        )
        for raw in raw_runs
    )
    by_id: dict[int, RunState] = {}
    number_to_id: dict[int, int] = {}
    previous_number = None
    for run_id, run_number, run in visible:
        if previous_number is not None and run_number > previous_number:
            raise MetadataEditError("Build workflow runs are not newest-first")
        previous_number = run_number
        existing_id = number_to_id.get(run_number)
        if existing_id is not None and existing_id != run_id:
            raise MetadataEditError(
                "Build workflow runs reuse a run number"
            )
        number_to_id[run_number] = run_id
        existing = by_id.get(run_id)
        if existing is None:
            by_id[run_id] = run
            continue
        if existing.run_number != run_number:
            raise MetadataEditError("Build workflow run id changed number")
        if existing.run_attempt == run.run_attempt:
            raise MetadataEditError("Build workflow runs repeat an attempt")
        if run.run_attempt > existing.run_attempt:
            by_id[run_id] = run
    return tuple(
        sorted(
            by_id.values(),
            key=lambda run: run.run_number,
            reverse=True,
        )
    )


def _jobs_by_name(run: RunState) -> dict[str, JobState]:
    return {job.name: job for job in run.jobs}


def require_full_success(run: RunState) -> None:
    if (
        run.mode != "full"
        or run.status != "completed"
        or run.conclusion != "success"
    ):
        raise MetadataEditError("newest exact full Build is not successful")
    jobs = _jobs_by_name(run)
    for name in FULL_SUCCESS_JOB_NAMES:
        job = jobs[name]
        if (
            job.status != "completed"
            or job.conclusion != "success"
            or not job.runner_name
            or not job.started_at
        ):
            raise MetadataEditError(
                f"newest exact full Build job {name} is not runner-backed success"
            )
    patch = jobs["patch-release"]
    if (
        patch.status != "completed"
        or patch.conclusion != "skipped"
        or patch.runner_name
    ):
        raise MetadataEditError("newest exact full Build patch-release shape is invalid")


def _require_essential_full_outcome(run: RunState) -> None:
    if (
        run.mode == "full"
        and run.status == "completed"
        and run.conclusion in {"failure", "cancelled"}
    ):
        return
    require_full_success(run)


def require_metadata_success(run: RunState) -> None:
    if (
        run.mode != "metadata-only"
        or run.status != "completed"
        or run.conclusion != "success"
    ):
        raise MetadataEditError("metadata continuity run is not successful")
    jobs = _jobs_by_name(run)
    for name in (
        "event-identity",
        "event-router",
        "metadata-classifier",
        "host-tests",
        "build",
        "summary",
    ):
        job = jobs[name]
        if (
            job.status != "completed"
            or job.conclusion != "success"
            or not job.runner_name
            or not job.started_at
        ):
            raise MetadataEditError(
                f"metadata continuity job {name} is not runner-backed success"
            )
    for name in ("extended-host-tests", "legacy", "patch-release"):
        job = jobs[name]
        if (
            job.status != "completed"
            or job.conclusion != "skipped"
            or job.runner_name
        ):
            raise MetadataEditError(
                f"metadata continuity job {name} is not canonical skipped"
            )


def require_metadata_failure(run: RunState) -> None:
    if (
        run.mode != "metadata-only"
        or run.status != "completed"
        or run.conclusion != "failure"
    ):
        raise MetadataEditError(
            "only a completed failed metadata continuity run may be rerun"
        )
    jobs = _jobs_by_name(run)
    for name in (
        "event-identity",
        "event-router",
        "metadata-classifier",
        "host-tests",
        "build",
    ):
        job = jobs[name]
        if (
            job.status != "completed"
            or job.conclusion != "success"
            or not job.runner_name
            or not job.started_at
        ):
            raise MetadataEditError(
                f"failed metadata continuity job {name} is not canonical success"
            )
    for name in ("extended-host-tests", "legacy", "patch-release"):
        job = jobs[name]
        if (
            job.status != "completed"
            or job.conclusion != "skipped"
            or job.runner_name
        ):
            raise MetadataEditError(
                f"failed metadata continuity job {name} is not canonical skipped"
            )
    summary = jobs["summary"]
    if (
        summary.status != "completed"
        or summary.conclusion != "failure"
        or not summary.runner_name
        or not summary.started_at
    ):
        raise MetadataEditError(
            "failed metadata continuity summary is not canonical failure"
        )


def _latest_full(runs: tuple[RunState, ...]) -> RunState | None:
    return next(
        (
            run
            for run in runs
            if run.binding == "explicit-same" and run.mode == "full"
        ),
        None,
    )


def _blocking_active_runs(runs: tuple[RunState, ...]) -> tuple[RunState, ...]:
    return tuple(
        run
        for run in runs
        if run.status in ACTIVE_RUN_STATUSES
        and run.binding != "explicit-other"
        and (
            run.binding == "unbound"
            or run.mode != "active-metadata-only"
        )
    )


def _edit_receipt(
    state: PullRequestState,
    runs: tuple[RunState, ...],
    *,
    title: str | None,
    body: str | None,
    pre_version: MetadataVersion,
    provided_fields: tuple[EditFieldDigest, ...],
    changed_fields: tuple[EditFieldDigest, ...],
) -> EditReceipt:
    if not runs:
        raise MetadataEditError(
            "edit receipt requires a complete pre-PATCH run watermark"
        )
    watermark = max(runs, key=lambda run: run.run_number)
    target_title = title if title is not None else state.title
    target_body = body if body is not None else state.body
    return EditReceipt(
        schema_version=1,
        repository=state.repository,
        repository_id=state.repository_id,
        pr_number=state.number,
        head_sha=state.head_sha,
        base_sha=state.base_sha,
        workflow_id=watermark.workflow_id,
        workflow_path=WORKFLOW_PATH,
        nonce=secrets.token_hex(32),
        pre_metadata_sha256=_metadata_digest(state.title, state.body),
        pre_fields=(
            EditFieldDigest("body", _field_state_digest(state.body)),
            EditFieldDigest("title", _field_state_digest(state.title)),
        ),
        target_metadata_sha256=_metadata_digest(target_title, target_body),
        pre_version=pre_version,
        provided_fields=provided_fields,
        changed_fields=changed_fields,
        watermark_run_id=watermark.run_id,
        watermark_run_number=watermark.run_number,
        watermark_created_at=_timestamp_text(watermark.created_at),
    )


def _validate_receipt_identity(
    receipt: EditReceipt,
    state: PullRequestState,
) -> None:
    if _parse_edit_receipt(receipt.canonical_payload()) != receipt:
        raise MetadataEditError("edit receipt is not canonical")
    if (
        receipt.schema_version != 1
        or receipt.repository != state.repository
        or receipt.repository_id != state.repository_id
        or receipt.pr_number != state.number
        or receipt.head_sha != state.head_sha
        or receipt.base_sha != state.base_sha
        or receipt.workflow_path != WORKFLOW_PATH
    ):
        raise MetadataEditError("edit receipt identity is stale or forged")


def _provided_fields(receipt: EditReceipt) -> dict[str, str]:
    return {field.field: field.sha256 for field in receipt.provided_fields}


def _changed_fields(receipt: EditReceipt) -> dict[str, str]:
    return {field.field: field.sha256 for field in receipt.changed_fields}


def _receipt_pre_fields(receipt: EditReceipt) -> dict[str, str]:
    return {field.field: field.sha256 for field in receipt.pre_fields}


def _body_version_identity(version: MetadataVersion) -> tuple[object, ...]:
    return (
        version.body_edit_total_count,
        version.body_edit_id,
        version.body_edit_created_at,
        version.body_edit_edited_at,
        version.body_edit_updated_at,
        version.body_last_edited_at,
        version.body_editor_id,
        version.body_editor_login,
        version.body_original,
    )


def _validate_receipt_target(
    receipt: EditReceipt,
    state: PullRequestState,
) -> None:
    if _metadata_digest(state.title, state.body) != receipt.target_metadata_sha256:
        raise MetadataEditError(
            "edit receipt target metadata digest does not match the pull request"
        )
    values = {
        "body": state.body,
        "title": state.title,
    }
    for field in receipt.provided_fields:
        value = values[field.field]
        if value is None or _content_digest(value) != field.sha256:
            raise MetadataEditError(
                f"edit receipt {field.field} digest does not match the pull request"
            )


def _receipt_matches_pre_state(
    receipt: EditReceipt,
    state: PullRequestState,
    version: MetadataVersion,
) -> bool:
    pre_fields = _receipt_pre_fields(receipt)
    return (
        _metadata_digest(state.title, state.body) == receipt.pre_metadata_sha256
        and _field_state_digest(state.title) == pre_fields["title"]
        and _field_state_digest(state.body) == pre_fields["body"]
        and version == receipt.pre_version
    )


def _confirmation_for_target(
    receipt: EditReceipt,
    *,
    intent_comment_id: int,
    state: PullRequestState,
    version: MetadataVersion,
) -> EditConfirmation:
    _validate_receipt_target(receipt, state)
    changed = set(_changed_fields(receipt))
    if "title" in changed:
        if (
            version.title_event_id is None
            or version.title_event_id == receipt.pre_version.title_event_id
            or version.title_previous is None
            or _field_state_digest(version.title_previous)
            != _receipt_pre_fields(receipt)["title"]
            or version.title_current != state.title
            or version.title_actor_id != state.repository_owner_id
            or version.title_actor_login != state.repository.split("/", 1)[0]
        ):
            raise MetadataEditError(
                "title metadata version does not uniquely attest the edit"
            )
    elif (
        version.title_event_id != receipt.pre_version.title_event_id
        or version.title_event_created_at
        != receipt.pre_version.title_event_created_at
    ):
        raise MetadataEditError("unrequested title metadata version changed")
    if "body" in changed:
        first_edit = receipt.pre_version.body_edit_total_count == 0
        expected_count = 2 if first_edit else receipt.pre_version.body_edit_total_count + 1
        if (
            version.body_edit_total_count != expected_count
            or version.body_edit_id is None
            or version.body_edit_id == receipt.pre_version.body_edit_id
            or version.body_editor_id != state.repository_owner_id
            or version.body_editor_login != state.repository.split("/", 1)[0]
            or (
                first_edit
                and (
                    version.body_original is None
                    or version.body_original.body_sha256
                    != _receipt_pre_fields(receipt)["body"]
                )
            )
        ):
            raise MetadataEditError(
                "body metadata version does not uniquely attest the edit"
            )
    elif (
        _body_version_identity(version)
        != _body_version_identity(receipt.pre_version)
    ):
        raise MetadataEditError("unrequested body metadata version changed")
    return EditConfirmation(
        schema_version=1,
        repository=receipt.repository,
        repository_id=receipt.repository_id,
        pr_number=receipt.pr_number,
        head_sha=receipt.head_sha,
        base_sha=receipt.base_sha,
        intent_comment_id=intent_comment_id,
        intent_nonce=receipt.nonce,
        metadata_sha256=receipt.target_metadata_sha256,
        metadata_version=version,
    )


def _validate_confirmation(
    confirmation: EditConfirmation,
    receipt: EditReceipt,
    *,
    intent_comment_id: int,
    state: PullRequestState,
    version: MetadataVersion,
) -> None:
    if _parse_edit_confirmation(confirmation.canonical_payload()) != confirmation:
        raise MetadataEditError("edit confirmation is not canonical")
    expected = _confirmation_for_target(
        receipt,
        intent_comment_id=intent_comment_id,
        state=state,
        version=confirmation.metadata_version,
    )
    confirmed_version = confirmation.metadata_version
    actor_erasure = (
        confirmed_version.title_actor_id == state.repository_owner_id
        and confirmed_version.title_actor_login
        == state.repository.split("/", 1)[0]
        and version.title_actor_id is None
        and version.title_actor_login is None
        and replace(
            version,
            title_actor_id=confirmed_version.title_actor_id,
            title_actor_login=confirmed_version.title_actor_login,
        )
        == confirmed_version
    )
    if (
        confirmation.repository != state.repository
        or confirmation.repository_id != state.repository_id
        or confirmation.pr_number != state.number
        or confirmation.head_sha != state.head_sha
        or confirmation.base_sha != state.base_sha
        or confirmation.intent_comment_id != intent_comment_id
        or confirmation.intent_nonce != receipt.nonce
        or confirmation.metadata_sha256 != receipt.target_metadata_sha256
        or (
            confirmation.metadata_version != version
            and not actor_erasure
        )
        or confirmation != expected
    ):
        raise MetadataEditError("edit confirmation authority is stale or forged")


def _metadata_versions_equivalent(
    previous: MetadataVersion,
    current: MetadataVersion,
    state: PullRequestState,
) -> bool:
    if previous == current:
        return True
    return (
        previous.title_actor_id == state.repository_owner_id
        and previous.title_actor_login == state.repository.split("/", 1)[0]
        and current.title_actor_id is None
        and current.title_actor_login is None
        and replace(
            current,
            title_actor_id=previous.title_actor_id,
            title_actor_login=previous.title_actor_login,
        )
        == previous
    )


def _validate_receipt_watermark(
    receipt: EditReceipt,
    runs: tuple[RunState, ...],
) -> None:
    watermark = next(
        (run for run in runs if run.run_id == receipt.watermark_run_id),
        None,
    )
    if (
        watermark is None
        or watermark.run_number != receipt.watermark_run_number
        or _timestamp_text(watermark.created_at) != receipt.watermark_created_at
        or watermark.workflow_id != receipt.workflow_id
    ):
        raise MetadataEditError("edit receipt watermark is stale or forged")


def _helper_command(
    mode: str,
    state: PullRequestState,
    *arguments: str,
) -> tuple[str, ...]:
    return (
        "/usr/bin/python3",
        "-I",
        "scripts/workflow_pilot/isolated_launcher.py",
        "pr-metadata",
        mode,
        "--repository",
        state.repository,
        "--pr",
        str(state.number),
        "--head-sha",
        state.head_sha,
        "--base-sha",
        state.base_sha,
        *arguments,
    )


def _comment_guidance(state: PullRequestState) -> tuple[tuple[str, ...], ...]:
    return (
        _helper_command(
            "evidence-comment",
            state,
            "--comment-file",
            "<canonical-evidence-file>",
        ),
    )


def _reconcile_guidance(
    state: PullRequestState,
    confirmation_comment_id: int,
) -> tuple[tuple[str, ...], ...]:
    return (
        _helper_command(
            "reconcile",
            state,
            "--confirmation-comment-id",
            str(confirmation_comment_id),
        ),
    )


def _same_pr_contract(
    left: PullRequestState,
    right: PullRequestState,
) -> bool:
    return (
        left.repository == right.repository
        and left.repository_id == right.repository_id
        and left.repository_owner_id == right.repository_owner_id
        and left.number == right.number
        and left.head_sha == right.head_sha
        and left.head_ref == right.head_ref
        and left.base_sha == right.base_sha
        and left.base_ref == right.base_ref
    )


def edit_metadata(
    client: GitHubClient,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    title: str | None,
    body: str | None,
    essential_reason: str | None,
) -> Decision:
    initial = fetch_pull_request(client, repository, pr_number)
    require_identity(initial, head_sha=head_sha, base_sha=base_sha)
    if title is None and body is None:
        raise MetadataEditError("edit requires --title-file, --body-file, or both")
    if title is not None and not title.strip():
        raise MetadataEditError("pull request title must not be empty")
    target_title = title if title is not None else initial.title
    target_body = body if body is not None else initial.body
    target_digest = _metadata_digest(target_title, target_body)
    provided_fields = _provided_field_digests(title=title, body=body)
    changed_fields = _changed_field_digests(
        initial,
        title=title,
        body=body,
    )
    initially_matches = not changed_fields
    initial_runs = list_candidate_runs(client, initial)
    active_full = _blocking_active_runs(initial_runs)
    latest_full = _latest_full(initial_runs)
    if not initially_matches and essential_reason is None:
        if active_full:
            return Decision(
                action="deferred",
                base_sha=base_sha,
                guidance=_comment_guidance(initial),
                head_sha=head_sha,
                mutated=False,
                reason=(
                    "an exact-head full or unproven Build is active; update "
                    "the canonical evidence comment instead"
                ),
                repository=repository,
                pr_number=pr_number,
                run_id=active_full[0].run_id,
            )
        if latest_full is None:
            return Decision(
                action="refused",
                base_sha=base_sha,
                guidance=_comment_guidance(initial),
                head_sha=head_sha,
                mutated=False,
                reason="no exact-head full Build can authorize metadata continuity",
                repository=repository,
                pr_number=pr_number,
            )
        require_full_success(latest_full)
    elif essential_reason is not None and not essential_reason.strip():
        raise MetadataEditError("--essential-reason must contain non-whitespace text")
    elif (
        essential_reason is not None
        and len(essential_reason.encode("utf-8")) > MAX_REASON_BYTES
    ):
        raise MetadataEditError("--essential-reason exceeds 4096 bytes")
    elif not initially_matches and not active_full:
        if latest_full is None:
            raise MetadataEditError(
                "essential edit has no exact-head full Build to reconcile"
            )
        _require_essential_full_outcome(latest_full)

    current = fetch_pull_request(client, repository, pr_number)
    require_identity(current, head_sha=head_sha, base_sha=base_sha)
    if current != initial:
        if essential_reason is None:
            return Decision(
                action="deferred",
                base_sha=base_sha,
                guidance=_comment_guidance(current),
                head_sha=head_sha,
                mutated=False,
                reason="pull request metadata changed before mutation",
                repository=repository,
                pr_number=pr_number,
            )
        raise MetadataEditError(
            "pull request metadata changed before essential mutation"
        )
    current_runs = list_candidate_runs(client, current)
    current_active_full = _blocking_active_runs(current_runs)
    current_latest_full = _latest_full(current_runs)
    if not initially_matches and essential_reason is None:
        if current_runs != initial_runs:
            return Decision(
                action="deferred",
                base_sha=base_sha,
                guidance=_comment_guidance(current),
                head_sha=head_sha,
                mutated=False,
                reason="exact Build run authority changed before mutation",
                repository=repository,
                pr_number=pr_number,
                run_id=(
                    current_active_full[0].run_id
                    if current_active_full
                    else None
                ),
            )
        if current_active_full:
            return Decision(
                action="deferred",
                base_sha=base_sha,
                guidance=_comment_guidance(current),
                head_sha=head_sha,
                mutated=False,
                reason=(
                    "an exact-head full or unproven Build became active before "
                    "mutation"
                ),
                repository=repository,
                pr_number=pr_number,
                run_id=current_active_full[0].run_id,
            )
        if current_latest_full is None:
            raise MetadataEditError(
                "exact full Build authority disappeared before mutation"
            )
        require_full_success(current_latest_full)
    elif not initially_matches:
        active_full = current_active_full
        latest_full = current_latest_full
        if not active_full:
            if latest_full is None:
                raise MetadataEditError(
                    "essential edit has no exact-head full Build to reconcile"
                )
            _require_essential_full_outcome(latest_full)

    current_version = fetch_metadata_version(client, current)
    intents, confirmations, aborts = _transaction_comments(client, current)
    current_workflow_id = (
        current_runs[0].workflow_id if current_runs else 0
    )
    candidate_intents = _candidate_intents(
        intents,
        current,
        current_workflow_id,
    )
    latest_active_intent = _latest_intent(
        _active_intents(
            candidate_intents,
            confirmations,
            aborts,
        )
    )
    latest_intent_comment = (
        latest_active_intent
        or _latest_ordered_intent(candidate_intents)
    )
    latest_confirmation_comment = (
        confirmations.get(latest_intent_comment.comment_id)
        if latest_intent_comment is not None
        else None
    )
    latest_abort_comment = (
        aborts.get(latest_intent_comment.comment_id)
        if latest_intent_comment is not None
        else None
    )
    intent = (
        latest_intent_comment.intent
        if latest_intent_comment is not None
        else None
    )
    patch_required = not initially_matches
    intent_created = False
    if (
        intent is not None
        and latest_confirmation_comment is None
        and latest_abort_comment is None
    ):
        _validate_receipt_identity(intent, current)
        _validate_receipt_watermark(intent, current_runs)
        if (
            _provided_fields(intent)
            != {field.field: field.sha256 for field in provided_fields}
            or intent.target_metadata_sha256 != target_digest
        ):
            raise MetadataEditError(
                "another metadata edit intent is still active"
            )
        if _receipt_matches_pre_state(intent, current, current_version):
            patch_required = True
        elif _metadata_digest(current.title, current.body) == target_digest:
            _validate_receipt_target(intent, current)
            patch_required = False
        else:
            raise MetadataEditError(
                "active metadata edit intent matches neither pre-state nor target"
            )
    elif latest_abort_comment is not None:
        if initially_matches:
            return Decision(
                action="refused",
                base_sha=base_sha,
                guidance=(),
                head_sha=head_sha,
                mutated=False,
                reason=(
                    "matching metadata has no non-aborted authoritative pair"
                ),
                repository=repository,
                pr_number=pr_number,
            )
        previous_intent = intent
        intent = _edit_receipt(
            current,
            current_runs,
            title=title,
            body=body,
            pre_version=current_version,
            provided_fields=provided_fields,
            changed_fields=_changed_field_digests(
                current,
                title=title,
                body=body,
            ),
        )
        if previous_intent is not None and intent.nonce == previous_intent.nonce:
            raise MetadataEditError("successor intent reused an aborted nonce")
        latest_intent_comment = _create_intent_comment(
            client,
            current,
            intent,
        )
        intent_created = True
        if (
            latest_intent_comment.created_at < latest_abort_comment.created_at
            or latest_intent_comment.comment_id <= latest_abort_comment.comment_id
        ):
            raise MetadataEditError(
                "successor intent is not ordered after its abort"
            )
    elif initially_matches:
        if (
            intent is None
            or latest_intent_comment is None
            or latest_confirmation_comment is None
            or latest_confirmation_comment.confirmation is None
        ):
            return Decision(
                action="refused",
                base_sha=base_sha,
                guidance=(),
                head_sha=head_sha,
                mutated=False,
                reason="matching metadata has no authoritative edit pair",
                repository=repository,
                pr_number=pr_number,
            )
        _validate_receipt_identity(intent, current)
        _validate_receipt_watermark(intent, current_runs)
        _validate_confirmation(
            latest_confirmation_comment.confirmation,
            intent,
            intent_comment_id=latest_intent_comment.comment_id,
            state=current,
            version=current_version,
        )
        current_full, authorized = _current_full_authorization(current_runs)
        if not authorized:
            run_id = current_full.run_id
            reason = "an exact-head full or unproven Build is still active"
        else:
            metadata = _transaction_metadata_run(
                current_runs, intent, state=current,
                confirmation=latest_confirmation_comment.confirmation,
            )
            if metadata is None:
                run_id = current_full.run_id
                reason = (
                    "matching metadata has an authoritative pair but its "
                    "continuity run lacks unique event attribution"
                )
            elif metadata.status in ACTIVE_RUN_STATUSES:
                run_id = metadata.run_id
                reason = "the confirmation-bound metadata run is still active"
            elif metadata.conclusion == "success":
                require_metadata_success(metadata)
                return Decision(
                    action="no-op",
                    base_sha=base_sha,
                    guidance=(),
                    head_sha=head_sha,
                    mutated=False,
                    reason=(
                        "requested metadata and its confirmation-bound "
                        "continuity run already succeed"
                    ),
                    repository=repository,
                    pr_number=pr_number,
                    run_id=metadata.run_id,
                    intent_comment_id=latest_intent_comment.comment_id,
                    intent_comment_url=latest_intent_comment.html_url,
                    confirmation_comment_id=latest_confirmation_comment.comment_id,
                    confirmation_comment_url=latest_confirmation_comment.html_url,
                )
            else:
                require_metadata_failure(metadata)
                run_id = metadata.run_id
                reason = (
                    "the confirmation-bound metadata run failed and requires "
                    "reconciliation"
                )
        return Decision(
            action="deferred",
            base_sha=base_sha,
            guidance=_reconcile_guidance(
                current,
                latest_confirmation_comment.comment_id,
            ),
            head_sha=head_sha,
            mutated=False,
            reason=reason,
            repository=repository,
            pr_number=pr_number,
            run_id=run_id,
            intent_comment_id=latest_intent_comment.comment_id,
            intent_comment_url=latest_intent_comment.html_url,
            confirmation_comment_id=latest_confirmation_comment.comment_id,
            confirmation_comment_url=latest_confirmation_comment.html_url,
        )
    else:
        intent = _edit_receipt(
            current,
            current_runs,
            title=title,
            body=body,
            pre_version=current_version,
            provided_fields=provided_fields,
            changed_fields=_changed_field_digests(
                current,
                title=title,
                body=body,
            ),
        )
        latest_intent_comment = _create_intent_comment(
            client,
            current,
            intent,
        )
        intent_created = True

    if intent is None or latest_intent_comment is None:
        raise MetadataEditError("metadata edit intent state is incomplete")
    if patch_required:
        fresh_runs = list_candidate_runs(client, current)
        fresh_intents, fresh_confirmations, fresh_aborts = _transaction_comments(
            client,
            current,
        )
        latest_intent_comment = _rebind_intent_comment(
            latest_intent_comment, fresh_intents
        )
        intent = latest_intent_comment.intent
        terminal = _terminal_intent_decision(
            current,
            latest_intent_comment,
            fresh_confirmations,
            fresh_aborts,
            mutated=intent_created,
        )
        if terminal is not None:
            return terminal
        fresh_latest = _latest_intent(
            _active_intents(
                _candidate_intents(
                    fresh_intents,
                    current,
                    fresh_runs[0].workflow_id if fresh_runs else 0,
                ),
                fresh_confirmations,
                fresh_aborts,
            )
        )
        reason = None
        if fresh_runs != current_runs:
            reason = "run-authority-drift"
        elif (
            fresh_latest is None
            or fresh_latest.comment_id != latest_intent_comment.comment_id
        ):
            reason = "transaction-drift"
        elif essential_reason is None and _blocking_active_runs(fresh_runs):
            reason = "run-authority-drift"
        observed, final_version = _fetch_metadata_observation(client, current)
        if reason is None:
            if not _same_pr_contract(observed, current):
                reason = "candidate-drift"
            elif not _receipt_matches_pre_state(intent, observed, final_version):
                reason = (
                    "metadata-version-drift"
                    if _metadata_digest(observed.title, observed.body)
                    == intent.pre_metadata_sha256
                    else "pre-state-drift"
                )
        if not intent_created:
            return Decision(
                action="deferred",
                base_sha=base_sha,
                guidance=_comment_guidance(current),
                head_sha=head_sha,
                mutated=False,
                reason=(
                    "unmatched metadata edit intent has an ambiguous earlier PATCH "
                    "outcome; no PATCH or abort is safe from pre-state; retry the "
                    "same request only to recover an authenticated applied target"
                ),
                repository=repository,
                pr_number=pr_number,
                intent_comment_id=latest_intent_comment.comment_id,
                intent_comment_url=latest_intent_comment.html_url,
            )
        if reason is not None:
            abort_comment = _create_abort_comment(
                client,
                observed,
                latest_intent_comment,
                observed_version=final_version,
                reason=reason,
            )
            return Decision(
                action="deferred",
                base_sha=base_sha,
                guidance=(),
                head_sha=head_sha,
                mutated=True,
                reason=f"metadata edit intent aborted: {reason}",
                repository=repository,
                pr_number=pr_number,
                intent_comment_id=latest_intent_comment.comment_id,
                intent_comment_url=latest_intent_comment.html_url,
                abort_comment_id=abort_comment.comment_id,
                abort_comment_url=abort_comment.html_url,
            )
        current = observed
    mutation: dict[str, object] = {}
    changed = set(_changed_fields(intent))
    if "title" in changed:
        mutation["title"] = target_title
    if "body" in changed:
        mutation["body"] = target_body
    if patch_required:
        try:
            mutation_response = client.request(
                "PATCH",
                _endpoint(repository, f"pulls/{pr_number}"),
                body=mutation,
                label="pull request metadata update",
            )
        except GitHubHTTPError as error:
            return _abort_rejected_patch(
                client, current, latest_intent_comment,
                pre_patch_runs=fresh_runs, error=error, mutated=intent_created,
            )
        after = _parse_pull_request_payload(
            mutation_response.payload,
            repository,
            pr_number,
        )
        require_identity(after, head_sha=head_sha, base_sha=base_sha)
        if (
            after.repository_id != current.repository_id
            or after.repository_owner_id != current.repository_owner_id
            or after.head_ref != current.head_ref
        ):
            raise MetadataEditError(
                "pull request mutation response repository/head-ref identity drifted"
            )
        if after.updated_at < current.updated_at:
            raise MetadataEditError(
                "pull request mutation response updated_at regressed"
            )
        if after.title != target_title or after.body != target_body:
            raise MetadataEditError(
                "pull request mutation response did not attest complete target metadata"
            )
    else:
        after = current
    fresh_intents, fresh_confirmations, fresh_aborts = _transaction_comments(
        client, after
    )
    latest_intent_comment = _rebind_intent_comment(
        latest_intent_comment, fresh_intents
    )
    intent = latest_intent_comment.intent
    terminal = _terminal_intent_decision(
        after,
        latest_intent_comment,
        fresh_confirmations,
        fresh_aborts,
        mutated=intent_created or patch_required,
    )
    if terminal is not None:
        return terminal
    after_version = fetch_metadata_version(client, after)
    confirmation = _confirmation_for_target(
        intent,
        intent_comment_id=latest_intent_comment.comment_id,
        state=after,
        version=after_version,
    )
    confirmation_comment = _create_confirmation_comment(
        client,
        after,
        confirmation,
    )
    guidance = _reconcile_guidance(after, confirmation_comment.comment_id)
    reason = (
        "metadata updated; reconcile the exact metadata-only run to close any "
        "non-atomic same-SHA Build race"
    )
    if active_full:
        reason = (
            "essential metadata updated; reconcile its metadata-only run after "
            "the newest exact-head full Build succeeds"
        )
    return Decision(
        action="updated" if patch_required else "recovered",
        base_sha=base_sha,
        guidance=guidance,
        head_sha=head_sha,
        mutated=True,
        reason=reason,
        repository=repository,
        pr_number=pr_number,
        run_id=(
            active_full[0].run_id
            if active_full
            else latest_full.run_id if latest_full is not None else None
        ),
        intent_comment_id=latest_intent_comment.comment_id,
        intent_comment_url=latest_intent_comment.html_url,
        confirmation_comment_id=confirmation_comment.comment_id,
        confirmation_comment_url=confirmation_comment.html_url,
    )


def _current_full_authorization(
    runs: tuple[RunState, ...],
) -> tuple[RunState, bool]:
    relevant = [
        run
        for run in runs
        if run.binding == "unbound"
        or (
            run.binding == "explicit-same"
            and run.mode in {"active-full", "active-unknown", "full"}
        )
    ]
    if not relevant:
        raise MetadataEditError("no exact-head full Build exists")
    newest_number = max(run.run_number for run in relevant)
    newest = [run for run in relevant if run.run_number == newest_number]
    if len(newest) != 1:
        raise MetadataEditError("newest full Build authority is ambiguous")
    run = newest[0]
    if (
        run.binding == "unbound"
        or run.status in ACTIVE_RUN_STATUSES
        or run.mode != "full"
    ):
        return run, False
    require_full_success(run)
    return run, True


def _transaction_metadata_run(
    runs: tuple[RunState, ...],
    receipt: EditReceipt,
    *,
    state: PullRequestState,
    confirmation: EditConfirmation,
) -> RunState | None:
    event_times = set()
    for field in receipt.changed_fields:
        name = (
            "body_edit_edited_at" if field.field == "body"
            else "title_event_created_at"
        )
        previous = getattr(receipt.pre_version, name)
        edited = getattr(confirmation.metadata_version, name)
        if edited is None or (previous is not None and previous >= edited):
            return None
        event_times.add(edited)
    if len(event_times) != 1:
        return None
    event_updated_at = event_times.pop()
    candidates = [
        run
        for run in runs
        if run.binding == "explicit-same"
        and run.mode in {"metadata-only", "active-metadata-only"}
        and run.run_number > receipt.watermark_run_number
    ]
    matching = []
    unproven = False
    for run in candidates:
        if run.status == "completed":
            if run.conclusion == "success":
                require_metadata_success(run)
            else:
                require_metadata_failure(run)
        classifier = _jobs_by_name(run)[candidate_evidence.METADATA_CLASSIFIER]
        if classifier.metadata_event_sha256 is None:
            unproven = True
            continue
        expected = metadata_event.transition_digest(
            state,
            run_id=run.run_id, run_number=run.run_number, run_attempt=run.run_attempt,
            updated_at=event_updated_at,
            pre_fields=_receipt_pre_fields(receipt),
            changed_fields=_changed_fields(receipt),
        )
        if classifier.metadata_event_sha256 == expected:
            matching.append(run)
    if unproven:
        return None
    identities = {
        (run.run_id, run.run_number)
        for run in matching
    }
    if len(identities) > 1:
        raise MetadataEditError(
            "multiple edit-attested metadata run identities are ambiguous"
        )
    return min(
        matching,
        key=lambda run: (run.run_number, run.run_id),
        default=None,
    )


def reconcile_metadata(
    client: GitHubClient,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    confirmation_comment_id: int,
) -> Decision:
    initial = fetch_pull_request(client, repository, pr_number)
    require_identity(initial, head_sha=head_sha, base_sha=base_sha)
    first_runs = list_candidate_runs(client, initial)
    if not first_runs:
        raise MetadataEditError("no exact-head Build run exists")
    receipt, intent_comment, confirmation, confirmation_comment = (
        _authoritative_edit_pair(
            client,
            initial,
            confirmation_comment_id,
            first_runs[0].workflow_id,
        )
    )
    initial_version = fetch_metadata_version(client, initial)
    _validate_confirmation(
        confirmation,
        receipt,
        intent_comment_id=intent_comment.comment_id,
        state=initial,
        version=initial_version,
    )
    _validate_receipt_watermark(receipt, first_runs)
    first_full, authorized = _current_full_authorization(first_runs)
    if not authorized:
        return Decision(
            action="deferred",
            base_sha=base_sha,
            guidance=_reconcile_guidance(initial, confirmation_comment_id),
            head_sha=head_sha,
            mutated=False,
            reason="an exact-head full or unproven Build is still active",
            repository=repository,
            pr_number=pr_number,
            run_id=first_full.run_id,
        )
    first_metadata = _transaction_metadata_run(
        first_runs, receipt, state=initial, confirmation=confirmation
    )
    if first_metadata is None:
        return Decision(
            action="deferred",
            base_sha=base_sha,
            guidance=_reconcile_guidance(initial, confirmation_comment_id),
            head_sha=head_sha,
            mutated=False,
            reason="no uniquely event-attested metadata-only run is available",
            repository=repository,
            pr_number=pr_number,
            run_id=first_full.run_id,
        )
    if first_metadata.status in ACTIVE_RUN_STATUSES:
        return Decision(
            action="deferred",
            base_sha=base_sha,
            guidance=_reconcile_guidance(initial, confirmation_comment_id),
            head_sha=head_sha,
            mutated=False,
            reason="the exact metadata-only run is already active",
            repository=repository,
            pr_number=pr_number,
            run_id=first_metadata.run_id,
        )
    if first_metadata.conclusion == "success":
        require_metadata_success(first_metadata)
        return Decision(
            action="complete",
            base_sha=base_sha,
            guidance=(),
            head_sha=head_sha,
            mutated=False,
            reason="metadata continuity already succeeds",
            repository=repository,
            pr_number=pr_number,
            run_id=first_metadata.run_id,
        )
    require_metadata_failure(first_metadata)

    current = fetch_pull_request(client, repository, pr_number)
    require_identity(current, head_sha=head_sha, base_sha=base_sha)
    (
        current_receipt,
        current_intent_comment,
        current_confirmation,
        current_confirmation_comment,
    ) = _authoritative_edit_pair(
        client,
        current,
        confirmation_comment_id,
        first_runs[0].workflow_id,
    )
    current_version = fetch_metadata_version(client, current)
    _validate_confirmation(
        current_confirmation,
        current_receipt,
        intent_comment_id=current_intent_comment.comment_id,
        state=current,
        version=current_version,
    )
    if (
        current != initial
        or current_intent_comment != intent_comment
        or current_confirmation_comment != confirmation_comment
        or not _metadata_versions_equivalent(
            initial_version,
            current_version,
            current,
        )
    ):
        return Decision(
            action="deferred",
            base_sha=base_sha,
            guidance=_reconcile_guidance(current, confirmation_comment_id),
            head_sha=head_sha,
            mutated=False,
            reason="metadata edit authority changed during reconciliation",
            repository=repository,
            pr_number=pr_number,
            run_id=first_metadata.run_id,
        )
    receipt = current_receipt
    current_runs = list_candidate_runs(client, current)
    _validate_receipt_watermark(receipt, current_runs)
    current_full, current_authorized = _current_full_authorization(current_runs)
    if not current_authorized:
        return Decision(
            action="deferred",
            base_sha=base_sha,
            guidance=_reconcile_guidance(current, confirmation_comment_id),
            head_sha=head_sha,
            mutated=False,
            reason="newest exact full or unproven Build is still active",
            repository=repository,
            pr_number=pr_number,
            run_id=current_full.run_id,
        )
    current_metadata = _transaction_metadata_run(
        current_runs, receipt, state=current, confirmation=current_confirmation
    )
    if (
        current_runs != first_runs
        or current_metadata is None
        or current_full.run_id != first_full.run_id
        or current_full.run_attempt != first_full.run_attempt
        or current_metadata.run_id != first_metadata.run_id
        or current_metadata.run_attempt != first_metadata.run_attempt
        or current_metadata.status != first_metadata.status
        or current_metadata.conclusion != first_metadata.conclusion
    ):
        return Decision(
            action="deferred",
            base_sha=base_sha,
            guidance=_reconcile_guidance(current, confirmation_comment_id),
            head_sha=head_sha,
            mutated=False,
            reason=(
                "exact Build run state changed during reconciliation; retry "
                "from fresh authority"
            ),
            repository=repository,
            pr_number=pr_number,
            run_id=(
                current_metadata.run_id
                if current_metadata
                else current_full.run_id
            ),
        )
    require_metadata_failure(current_metadata)
    client.request(
        "POST",
        _endpoint(repository, f"actions/runs/{current_metadata.run_id}/rerun"),
        label="metadata continuity rerun",
    )
    return Decision(
        action="rerun",
        base_sha=base_sha,
        guidance=(),
        head_sha=head_sha,
        mutated=True,
        reason="reran only the exact metadata-only continuity run",
        repository=repository,
        pr_number=pr_number,
        run_id=current_metadata.run_id,
    )


def _list_comments(
    client: GitHubClient,
    repository: str,
    pr_number: int,
    repository_id: int,
    repository_owner_id: int,
) -> list[CommentState]:
    first = _read_comment_pass(
        client, repository, pr_number, repository_id, repository_owner_id
    )
    confirmed = _read_comment_pass(
        client, repository, pr_number, repository_id, repository_owner_id
    )
    if first != confirmed:
        raise MetadataEditError(
            "pull request comments changed during enumeration; retry complete snapshots"
        )
    return confirmed


def _read_comment_pass(
    client: GitHubClient,
    repository: str,
    pr_number: int,
    repository_id: int,
    repository_owner_id: int,
) -> list[CommentState]:
    comments = []
    seen_ids = set()
    expected_last_page = None
    for page in range(1, MAX_COMMENT_PAGES + 1):
        endpoint_for_page = lambda value: _query_endpoint(
            repository,
            f"issues/{pr_number}/comments",
            [("per_page", str(PAGE_SIZE)), ("page", str(value))],
        )
        response = client.request(
            "GET",
            endpoint_for_page(page),
            label=f"pull request comments page {page}",
        )
        payload = response.payload
        if not isinstance(payload, list) or len(payload) > PAGE_SIZE:
            raise MetadataEditError("pull request comments pagination is invalid")
        relations = _parse_link_pages(
            response.headers.get("link", ""),
            endpoint_for_page=endpoint_for_page,
            current_page=page,
            label="pull request comments",
            repository=repository,
            repository_id=repository_id,
        )
        next_page = relations.get("next")
        if len(payload) < PAGE_SIZE and next_page is not None:
            raise MetadataEditError(
                "pull request comments short page reported a next link"
            )
        if next_page is not None:
            expected_relations = (
                {"next", "last"}
                if page == 1
                else {"first", "last", "next", "prev"}
            )
            if set(relations) != expected_relations:
                raise MetadataEditError(
                    "pull request comments Link relations are noncanonical"
                )
            if next_page != page + 1:
                raise MetadataEditError("pull request comments next page drifted")
            last_page = relations["last"]
            if last_page < next_page:
                raise MetadataEditError("pull request comments last page drifted")
            if expected_last_page is None:
                expected_last_page = last_page
            elif expected_last_page != last_page:
                raise MetadataEditError(
                    "pull request comments last page changed across pages"
                )
            if page > 1 and (
                relations["first"] != 1 or relations["prev"] != page - 1
            ):
                raise MetadataEditError(
                    "pull request comments previous page relations drifted"
                )
        else:
            expected_relations = set() if page == 1 else {"first", "prev"}
            if set(relations) != expected_relations:
                raise MetadataEditError(
                    "pull request comments final Link relations are noncanonical"
                )
            if page > 1 and (
                relations["first"] != 1 or relations["prev"] != page - 1
            ):
                raise MetadataEditError(
                    "pull request comments final page relations drifted"
                )
            if expected_last_page is not None and page != expected_last_page:
                raise MetadataEditError(
                    "pull request comments terminated before the last page"
                )
        for raw in payload:
            comment = _parse_comment_payload(
                raw,
                repository,
                pr_number,
                repository_owner_id,
            )
            comment_id = comment.comment_id
            if comment_id in seen_ids:
                raise MetadataEditError("pull request comments repeat an identity")
            seen_ids.add(comment_id)
            comments.append(comment)
        if next_page is None:
            return comments
    raise MetadataEditError("pull request comments exceed the pagination bound")


def _intent_comment_body(intent: EditReceipt) -> str:
    return INTENT_MARKER + "\n" + intent.canonical_json()


def _confirmation_comment_body(confirmation: EditConfirmation) -> str:
    return CONFIRMATION_MARKER + "\n" + confirmation.canonical_json()


def _abort_comment_body(abort: EditAbort) -> str:
    return ABORT_MARKER + "\n" + abort.canonical_json()


def _parse_intent_comment_body(body: str) -> EditReceipt:
    if body.count(INTENT_MARKER) != 1 or not _marker_is_standalone(
        body,
        INTENT_MARKER,
    ):
        raise MetadataEditError(
            "metadata edit intent marker is duplicated or embedded"
        )
    prefix = INTENT_MARKER + "\n"
    if not body.startswith(prefix):
        raise MetadataEditError(
            "metadata edit intent marker must be the first line"
        )
    raw_intent = body[len(prefix) :]
    intent = _parse_edit_receipt(
        _parse_json(raw_intent, "metadata edit intent comment")
    )
    if raw_intent != intent.canonical_json():
        raise MetadataEditError(
            "metadata edit intent comment JSON is not canonical"
        )
    return intent


def _parse_confirmation_comment_body(body: str) -> EditConfirmation:
    if body.count(CONFIRMATION_MARKER) != 1 or not _marker_is_standalone(
        body,
        CONFIRMATION_MARKER,
    ):
        raise MetadataEditError(
            "metadata edit confirmation marker is duplicated or embedded"
        )
    prefix = CONFIRMATION_MARKER + "\n"
    if not body.startswith(prefix):
        raise MetadataEditError(
            "metadata edit confirmation marker must be the first line"
        )
    raw_confirmation = body[len(prefix) :]
    confirmation = _parse_edit_confirmation(
        _parse_json(raw_confirmation, "metadata edit confirmation comment")
    )
    if raw_confirmation != confirmation.canonical_json():
        raise MetadataEditError(
            "metadata edit confirmation comment JSON is not canonical"
        )
    return confirmation


def _parse_abort_comment_body(body: str) -> EditAbort:
    if body.count(ABORT_MARKER) != 1 or not _marker_is_standalone(
        body,
        ABORT_MARKER,
    ):
        raise MetadataEditError(
            "metadata edit abort marker is duplicated or embedded"
        )
    prefix = ABORT_MARKER + "\n"
    if not body.startswith(prefix):
        raise MetadataEditError("metadata edit abort marker must be first")
    raw_abort = body[len(prefix) :]
    abort = _parse_edit_abort(
        _parse_json(raw_abort, "metadata edit abort comment")
    )
    if raw_abort != abort.canonical_json():
        raise MetadataEditError("metadata edit abort JSON is not canonical")
    return abort


def _protected_comment_marker(body: str) -> str | None:
    if not isinstance(body, str):
        raise MetadataEditError("comment body must be text")
    markers = [
        marker
        for marker in (
            EVIDENCE_MARKER,
            INTENT_MARKER,
            CONFIRMATION_MARKER,
            ABORT_MARKER,
        )
        if marker in body
    ]
    if len(markers) > 1:
        raise MetadataEditError("pull request comment mixes protected markers")
    if not markers:
        return None
    marker = markers[0]
    if body.count(marker) != 1 or not _marker_is_standalone(body, marker):
        raise MetadataEditError("protected comment marker is duplicated or embedded")
    return marker


def _parse_comment_payload(
    raw: object,
    repository: str,
    pr_number: int,
    repository_owner_id: int,
) -> CommentState:
    if not isinstance(raw, dict):
        raise MetadataEditError("pull request comment is invalid")
    comment_id = _positive_int(raw.get("id"), "pull request comment id")
    _require_api_url(
        raw.get("url"),
        _endpoint(repository, f"issues/comments/{comment_id}"),
        field=f"pull request comment {comment_id} URL",
    )
    _require_api_url(
        raw.get("issue_url"),
        _endpoint(repository, f"issues/{pr_number}"),
        field=f"pull request comment {comment_id} issue URL",
    )
    html_url = raw.get("html_url")
    owner, name = repository.split("/", 1)
    if (
        html_url
        != f"https://github.com/{owner}/{name}/pull/{pr_number}"
        f"#issuecomment-{comment_id}"
    ):
        raise MetadataEditError(
            f"pull request comment {comment_id} HTML URL identity drifted"
        )
    body = raw.get("body")
    if not isinstance(body, str):
        raise MetadataEditError(f"pull request comment {comment_id} body is invalid")
    user = raw.get("user")
    association_raw = raw.get("author_association")
    author_id = None
    author_login = None
    author_type = None
    author_site_admin = None
    association = None
    if user is not None:
        if not isinstance(user, dict):
            raise MetadataEditError(
                f"pull request comment {comment_id} author is invalid"
            )
        author_id = _positive_int(
            user.get("id"),
            f"pull request comment {comment_id} author id",
        )
        author_login = _text(
            user.get("login"),
            f"pull request comment {comment_id} author login",
        )
        author_type = _text(
            user.get("type"),
            f"pull request comment {comment_id} author type",
        )
        if not isinstance(user.get("site_admin"), bool):
            raise MetadataEditError(
                f"pull request comment {comment_id} site_admin is invalid"
            )
        author_site_admin = user["site_admin"]
    if association_raw is not None:
        association = _text(
            association_raw,
            f"pull request comment {comment_id} author association",
        )
    is_owner = (
        author_id == repository_owner_id
        and author_login == owner
        and author_type == "User"
        and isinstance(user, dict)
        and user.get("site_admin") is False
        and association == "OWNER"
    )
    marker = _protected_comment_marker(body) if is_owner else None
    created_at = _github_timestamp(
        raw.get("created_at"),
        f"pull request comment {comment_id} created_at",
    )
    updated_at = _github_timestamp(
        raw.get("updated_at"),
        f"pull request comment {comment_id} updated_at",
    )
    if updated_at < created_at:
        raise MetadataEditError(
            f"pull request comment {comment_id} chronology is invalid"
        )
    _text(raw.get("node_id"), f"pull request comment {comment_id} node_id")
    intent = None
    confirmation = None
    abort = None
    if marker in (INTENT_MARKER, CONFIRMATION_MARKER, ABORT_MARKER):
        if updated_at != created_at:
            raise MetadataEditError(
                f"metadata edit transaction comment {comment_id} was edited"
            )
        if marker == INTENT_MARKER:
            intent = _parse_intent_comment_body(body)
        elif marker == CONFIRMATION_MARKER:
            confirmation = _parse_confirmation_comment_body(body)
        else:
            abort = _parse_abort_comment_body(body)
    return CommentState(
        comment_id=comment_id,
        repository=repository,
        pr_number=pr_number,
        html_url=html_url,
        body=body,
        author_id=author_id,
        author_login=author_login,
        author_type=author_type,
        author_site_admin=author_site_admin,
        author_association=association,
        created_at=created_at,
        updated_at=updated_at,
        intent=intent,
        confirmation=confirmation,
        abort=abort,
    )


def _marker_is_standalone(body: str, marker: str = EVIDENCE_MARKER) -> bool:
    return sum(line.strip() == marker for line in body.splitlines()) == 1


def _create_transaction_comment(
    client: GitHubClient,
    state: PullRequestState,
    *,
    body: str,
    label: str,
) -> CommentState:
    marker = _protected_comment_marker(body)
    if marker == INTENT_MARKER:
        _parse_intent_comment_body(body)
    elif marker == CONFIRMATION_MARKER:
        _parse_confirmation_comment_body(body)
    elif marker == ABORT_MARKER:
        _parse_abort_comment_body(body)
    else:
        raise MetadataEditError("transaction comment requires a transaction marker")
    response = client.request(
        "POST",
        _endpoint(state.repository, f"issues/{state.number}/comments"),
        body={"body": body},
        label=label,
    )
    comment = _parse_comment_payload(
        response.payload,
        state.repository,
        state.number,
        state.repository_owner_id,
    )
    if "location" in response.headers:
        if response.status != 201:
            raise MetadataEditError(f"{label} creation Location requires HTTP 201")
        _require_api_url(
            response.headers["location"],
            _endpoint(state.repository, f"issues/comments/{comment.comment_id}"),
            field=f"{label} Location",
        )
    if (
        comment.body != body
    ):
        raise MetadataEditError(
            f"{label} response did not attest the transaction comment"
        )
    return comment


def _transaction_comments(
    client: GitHubClient,
    state: PullRequestState,
) -> tuple[
    list[CommentState],
    dict[int, CommentState],
    dict[int, CommentState],
]:
    comments = _list_comments(
        client,
        state.repository,
        state.number,
        state.repository_id,
        state.repository_owner_id,
    )
    intents = [
        comment for comment in comments if comment.intent is not None
    ]
    nonces = [
        comment.intent.nonce
        for comment in intents
        if comment.intent is not None
    ]
    if len(nonces) != len(set(nonces)):
        raise MetadataEditError("metadata edit intent nonce is duplicated")
    confirmations = [
        comment for comment in comments if comment.confirmation is not None
    ]
    intents_by_id = {comment.comment_id: comment for comment in intents}
    by_intent = {}
    for comment in confirmations:
        confirmation = comment.confirmation
        if confirmation is None:
            raise MetadataEditError("metadata edit confirmation is invalid")
        if confirmation.intent_comment_id in by_intent:
            raise MetadataEditError(
                "metadata edit intent has duplicate confirmations"
            )
        intent_comment = intents_by_id.get(confirmation.intent_comment_id)
        if intent_comment is None or intent_comment.intent is None:
            raise MetadataEditError(
                "metadata edit confirmation references an unknown intent"
            )
        intent = intent_comment.intent
        if (
            confirmation.repository != intent.repository
            or confirmation.repository_id != intent.repository_id
            or confirmation.pr_number != intent.pr_number
            or confirmation.head_sha != intent.head_sha
            or confirmation.base_sha != intent.base_sha
            or confirmation.intent_nonce != intent.nonce
            or comment.created_at < intent_comment.created_at
            or comment.comment_id <= intent_comment.comment_id
        ):
            raise MetadataEditError(
                "metadata edit confirmation contradicts its intent"
            )
        by_intent[confirmation.intent_comment_id] = comment
    aborts = [
        comment for comment in comments if comment.abort is not None
    ]
    abort_by_intent = {}
    for comment in aborts:
        abort = comment.abort
        if abort is None:
            raise MetadataEditError("metadata edit abort is invalid")
        if abort.intent_comment_id in abort_by_intent:
            raise MetadataEditError("metadata edit intent has duplicate aborts")
        intent_comment = intents_by_id.get(abort.intent_comment_id)
        if intent_comment is None or intent_comment.intent is None:
            raise MetadataEditError("metadata edit abort references unknown intent")
        intent = intent_comment.intent
        if (
            abort.repository != intent.repository
            or abort.repository_id != intent.repository_id
            or abort.pr_number != intent.pr_number
            or abort.intent_nonce != intent.nonce
            or abort.intent_head_sha != intent.head_sha
            or abort.intent_base_sha != intent.base_sha
            or comment.created_at < intent_comment.created_at
            or comment.comment_id <= intent_comment.comment_id
        ):
            raise MetadataEditError("metadata edit abort contradicts its intent")
        if abort.intent_comment_id in by_intent:
            raise MetadataEditError(
                "metadata edit intent has both confirmation and abort"
            )
        abort_by_intent[abort.intent_comment_id] = comment
    return intents, by_intent, abort_by_intent


def _candidate_intents(
    intents: list[CommentState],
    state: PullRequestState,
    workflow_id: int,
) -> list[CommentState]:
    return [
        comment
        for comment in intents
        if comment.intent is not None
        and comment.intent.repository == state.repository
        and comment.intent.repository_id == state.repository_id
        and comment.intent.pr_number == state.number
        and comment.intent.head_sha == state.head_sha
        and comment.intent.base_sha == state.base_sha
        and comment.intent.workflow_id == workflow_id
        and comment.intent.workflow_path == WORKFLOW_PATH
    ]


def _latest_intent(
    intents: list[CommentState],
) -> CommentState | None:
    if not intents:
        return None
    newest_created_at = max(comment.created_at for comment in intents)
    newest = [
        comment for comment in intents if comment.created_at == newest_created_at
    ]
    if len(newest) != 1:
        raise MetadataEditError("latest metadata edit intent is ambiguous")
    return newest[0]


def _latest_ordered_intent(
    intents: list[CommentState],
) -> CommentState | None:
    return max(
        intents,
        key=lambda comment: (comment.created_at, comment.comment_id),
        default=None,
    )


def _active_intents(
    intents: list[CommentState],
    confirmations: dict[int, CommentState],
    aborts: dict[int, CommentState],
) -> list[CommentState]:
    return [
        intent
        for intent in intents
        if intent.comment_id not in confirmations
        and intent.comment_id not in aborts
    ]


def _rebind_intent_comment(
    selected: CommentState,
    intents: list[CommentState],
) -> CommentState:
    fresh = next(
        (comment for comment in intents if comment.comment_id == selected.comment_id),
        None,
    )
    if fresh is None or fresh.intent is None:
        raise MetadataEditError(
            "selected metadata edit intent is missing or no longer owner-authenticated"
        )
    if fresh != selected:
        raise MetadataEditError("selected metadata edit intent changed during refresh")
    return fresh


def _terminal_intent_decision(
    state: PullRequestState,
    intent_comment: CommentState,
    confirmations: dict[int, CommentState],
    aborts: dict[int, CommentState],
    *,
    mutated: bool,
) -> Decision | None:
    confirmation = confirmations.get(intent_comment.comment_id)
    if confirmation is not None:
        return Decision(
            action="deferred",
            base_sha=state.base_sha,
            guidance=_reconcile_guidance(state, confirmation.comment_id),
            head_sha=state.head_sha,
            mutated=mutated,
            reason="metadata edit intent is already confirmed; reconcile its pair",
            repository=state.repository,
            pr_number=state.number,
            intent_comment_id=intent_comment.comment_id,
            intent_comment_url=intent_comment.html_url,
            confirmation_comment_id=confirmation.comment_id,
            confirmation_comment_url=confirmation.html_url,
        )
    abort = aborts.get(intent_comment.comment_id)
    if abort is not None:
        return Decision(
            action="deferred",
            base_sha=state.base_sha,
            guidance=(),
            head_sha=state.head_sha,
            mutated=mutated,
            reason="metadata edit intent is already aborted",
            repository=state.repository,
            pr_number=state.number,
            intent_comment_id=intent_comment.comment_id,
            intent_comment_url=intent_comment.html_url,
            abort_comment_id=abort.comment_id,
            abort_comment_url=abort.html_url,
        )
    return None


def _create_intent_comment(
    client: GitHubClient,
    state: PullRequestState,
    intent: EditReceipt,
) -> CommentState:
    comment = _create_transaction_comment(
        client,
        state,
        body=_intent_comment_body(intent),
        label="metadata edit intent comment creation",
    )
    if comment.intent != intent or comment.confirmation is not None:
        raise MetadataEditError(
            "intent comment creation response did not attest the edit intent"
        )
    return comment


def _create_confirmation_comment(
    client: GitHubClient,
    state: PullRequestState,
    confirmation: EditConfirmation,
) -> CommentState:
    comment = _create_transaction_comment(
        client,
        state,
        body=_confirmation_comment_body(confirmation),
        label="metadata edit confirmation comment creation",
    )
    if comment.confirmation != confirmation or comment.intent is not None:
        raise MetadataEditError(
            "confirmation comment creation response did not attest the edit"
        )
    return comment


def _create_abort_comment(
    client: GitHubClient,
    state: PullRequestState,
    intent_comment: CommentState,
    *,
    observed_version: MetadataVersion,
    reason: str,
) -> CommentState:
    intent = intent_comment.intent
    if intent is None:
        raise MetadataEditError("abort requires an authenticated metadata edit intent")
    abort = EditAbort(
        schema_version=1,
        repository=intent.repository,
        repository_id=intent.repository_id,
        pr_number=intent.pr_number,
        intent_comment_id=intent_comment.comment_id,
        intent_nonce=intent.nonce,
        intent_head_sha=intent.head_sha,
        intent_base_sha=intent.base_sha,
        observed_head_sha=state.head_sha,
        observed_base_sha=state.base_sha,
        observed_metadata_sha256=_metadata_digest(state.title, state.body),
        observed_version=observed_version,
        reason=reason,
    )
    comment = _create_transaction_comment(
        client,
        state,
        body=_abort_comment_body(abort),
        label="metadata edit abort comment creation",
    )
    if comment.abort != abort or comment.intent is not None:
        raise MetadataEditError(
            "abort comment creation response did not attest the abort"
        )
    return comment


def _abort_rejected_patch(
    client: GitHubClient,
    state: PullRequestState,
    intent_comment: CommentState,
    *,
    pre_patch_runs: tuple[RunState, ...],
    error: GitHubHTTPError,
    mutated: bool,
) -> Decision:
    if (
        error.method != "PATCH"
        or error.endpoint != _endpoint(state.repository, f"pulls/{state.number}")
        or error.response.status not in DEFINITE_PATCH_REJECTIONS
    ):
        raise error
    runs = list_candidate_runs(client, state)
    intents, confirmations, aborts = _transaction_comments(client, state)
    intent_comment = _rebind_intent_comment(intent_comment, intents)
    terminal = _terminal_intent_decision(
        state, intent_comment, confirmations, aborts, mutated=mutated,
    )
    if terminal is not None:
        return terminal
    if runs != pre_patch_runs:
        raise MetadataEditError(
            f"HTTP {error.response.status} rejected metadata PATCH, but complete "
            "Build run authority changed; unmatched intent remains held"
        ) from error
    intent = intent_comment.intent
    _validate_receipt_watermark(intent, runs)
    latest = _latest_intent(
        _active_intents(
            _candidate_intents(intents, state, intent.workflow_id),
            confirmations,
            aborts,
        )
    )
    if latest != intent_comment:
        raise MetadataEditError(
            f"HTTP {error.response.status} rejected metadata PATCH, but active "
            "intent selection changed; unmatched intent remains held"
        ) from error
    observed, version = _fetch_metadata_observation(client, state)
    if (
        not _same_pr_contract(observed, state)
        or not _receipt_matches_pre_state(intent, observed, version)
    ):
        raise MetadataEditError(
            f"HTTP {error.response.status} rejected metadata PATCH, but fresh "
            "candidate/pre-state/version changed; unmatched intent remains held"
        ) from error
    abort = _create_abort_comment(
        client, observed, intent_comment,
        observed_version=version, reason="patch-rejected",
    )
    return Decision(
        action="deferred",
        base_sha=observed.base_sha,
        guidance=_comment_guidance(observed),
        head_sha=observed.head_sha,
        mutated=True,
        reason=(
            f"metadata PATCH rejected (HTTP {error.response.status}); intent "
            "aborted after fresh authority checks; correct the requested values "
            "and retry with a new intent"
        ),
        repository=observed.repository,
        pr_number=observed.number,
        intent_comment_id=intent_comment.comment_id,
        intent_comment_url=intent_comment.html_url,
        abort_comment_id=abort.comment_id,
        abort_comment_url=abort.html_url,
    )


def _authoritative_edit_pair(
    client: GitHubClient,
    state: PullRequestState,
    confirmation_comment_id: int,
    workflow_id: int,
) -> tuple[EditReceipt, CommentState, EditConfirmation, CommentState]:
    intents, confirmations, _aborts = _transaction_comments(client, state)
    candidates = _candidate_intents(intents, state, workflow_id)
    if not candidates:
        raise MetadataEditError("metadata edit intent comment is missing")
    selected = [
        comment for comment in confirmations.values()
        if comment.comment_id == confirmation_comment_id
    ]
    if len(selected) != 1 or selected[0].confirmation is None:
        raise MetadataEditError("selected metadata edit confirmation is missing")
    confirmation_comment = selected[0]
    confirmation = confirmation_comment.confirmation
    intent_comment = next(
        (
            comment for comment in candidates
            if comment.comment_id == confirmation.intent_comment_id
        ),
        None,
    )
    if intent_comment is None or intent_comment.intent is None:
        raise MetadataEditError("selected confirmation has no intent for this candidate")
    if confirmation_comment.created_at < intent_comment.created_at:
        raise MetadataEditError("metadata edit confirmation nonce drifted")
    _validate_receipt_identity(intent_comment.intent, state)
    return intent_comment.intent, intent_comment, confirmation, confirmation_comment


def update_evidence_comment(
    client: GitHubClient,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    comment_body: str,
) -> Decision:
    if _protected_comment_marker(comment_body) != EVIDENCE_MARKER:
        raise MetadataEditError(
            "canonical evidence comment must contain one standalone marker"
        )
    initial = fetch_pull_request(client, repository, pr_number)
    require_identity(initial, head_sha=head_sha, base_sha=base_sha)
    comments = _list_comments(
        client,
        repository,
        pr_number,
        initial.repository_id,
        initial.repository_owner_id,
    )
    marked = []
    for comment in comments:
        if (
            comment.author_id != initial.repository_owner_id
            or comment.author_login != initial.repository.split("/", 1)[0]
            or comment.author_type != "User"
            or comment.author_site_admin is not False
            or comment.author_association != "OWNER"
        ):
            continue
        occurrences = comment.body.count(EVIDENCE_MARKER)
        if occurrences == 0:
            continue
        if occurrences != 1 or not _marker_is_standalone(comment.body):
            raise MetadataEditError(
                "canonical evidence marker is duplicated or embedded"
            )
        marked.append(comment)
    if len(marked) != 1:
        raise MetadataEditError(
            "pull request must have exactly one canonical marked evidence comment"
        )
    current = fetch_pull_request(client, repository, pr_number)
    require_identity(current, head_sha=head_sha, base_sha=base_sha)
    if current.repository_id != initial.repository_id:
        raise MetadataEditError(
            "pull request repository identity changed before comment mutation"
        )
    original = marked[0]
    comment_id = original.comment_id
    mutation_response = client.request(
        "PATCH",
        _endpoint(repository, f"issues/comments/{comment_id}"),
        body={"body": comment_body},
        label="canonical evidence comment update",
    )
    updated = _parse_comment_payload(
        mutation_response.payload,
        repository,
        pr_number,
        initial.repository_owner_id,
    )
    if (
        updated.comment_id != original.comment_id
        or updated.author_id != original.author_id
        or updated.author_login != original.author_login
        or updated.author_type != original.author_type
        or updated.author_site_admin != original.author_site_admin
        or updated.author_association != original.author_association
        or updated.body != comment_body
    ):
        raise MetadataEditError(
            "comment mutation response did not attest the requested canonical update"
        )
    return Decision(
        action="comment-updated",
        base_sha=base_sha,
        guidance=(),
        head_sha=head_sha,
        mutated=True,
        reason=(
            "canonical evidence comment updated without editing pull request "
            "metadata"
        ),
        repository=repository,
        pr_number=pr_number,
        comment_id=comment_id,
    )


def _read_text(path: Path, *, label: str, maximum: int) -> str:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise MetadataEditError(f"cannot read {label}: {error}") from error
    if len(data) > maximum:
        raise MetadataEditError(f"{label} exceeds {maximum} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MetadataEditError(f"{label} must be UTF-8") from error


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guard exact-candidate pull-request metadata edits."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in ("edit", "reconcile", "evidence-comment"):
        child = subparsers.add_parser(mode)
        child.add_argument("--repository", required=True)
        child.add_argument("--pr", type=int, required=True)
        child.add_argument("--head-sha", required=True)
        child.add_argument("--base-sha", required=True)
        if mode == "edit":
            child.add_argument("--title-file", type=Path)
            child.add_argument("--body-file", type=Path)
            child.add_argument("--essential-reason")
        elif mode == "reconcile":
            child.add_argument(
                "--confirmation-comment-id",
                type=int,
                required=True,
            )
        elif mode == "evidence-comment":
            child.add_argument("--comment-file", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if (
        arguments.mode == "edit"
        and arguments.title_file is None
        and arguments.body_file is None
    ):
        parser.error("edit requires --title-file, --body-file, or both")
    return arguments


def _resolve_gh() -> str:
    path = shutil.which("gh")
    if path is None:
        raise MetadataEditError("gh is required")
    return path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        repository = _repository(args.repository)
        pr_number = _positive_int(args.pr, "--pr")
        head_sha = _sha(args.head_sha, "--head-sha")
        base_sha = _sha(args.base_sha, "--base-sha")
        client = GitHubClient(_resolve_gh())
        if args.mode == "edit":
            title = (
                _read_text(
                    args.title_file,
                    label="title file",
                    maximum=MAX_BODY_BYTES,
                ).rstrip("\n")
                if args.title_file
                else None
            )
            body = (
                _read_text(
                    args.body_file,
                    label="body file",
                    maximum=MAX_BODY_BYTES,
                )
                if args.body_file
                else None
            )
            decision = edit_metadata(
                client,
                repository=repository,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
                title=title,
                body=body,
                essential_reason=args.essential_reason,
            )
        elif args.mode == "reconcile":
            decision = reconcile_metadata(
                client,
                repository=repository,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
                confirmation_comment_id=_positive_int(
                    args.confirmation_comment_id,
                    "--confirmation-comment-id",
                ),
            )
        else:
            decision = update_evidence_comment(
                client,
                repository=repository,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
                comment_body=_read_text(
                    args.comment_file,
                    label="comment file",
                    maximum=MAX_BODY_BYTES,
                ),
            )
    except MetadataEditError as error:
        print(f"pr-metadata: {error}", file=sys.stderr)
        return 2
    print(decision.canonical_json(), end="")
    return 0 if decision.action not in {"deferred", "refused"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
