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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from . import candidate_evidence


MAX_API_BYTES = 4 * 1024 * 1024
MAX_BODY_BYTES = 1024 * 1024
MAX_COMMENT_PAGES = 10
MAX_REASON_BYTES = 4096
MAX_RUN_PAGES = 10
MAX_RUNS = 1000
PAGE_SIZE = 100
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW_PATH = ".github/workflows/build.yml"
EVIDENCE_MARKER = "<!-- workflow-pilot-candidate-evidence -->"
INTENT_MARKER = "<!-- workflow-pilot-metadata-edit-intent:v1 -->"
CONFIRMATION_MARKER = "<!-- workflow-pilot-metadata-edit-confirmation:v1 -->"
HTTP_STATUS_RE = re.compile(r"^HTTP/(?:1(?:\.[01])?|2(?:\.0)?) ([1-5][0-9]{2})(?: .*)?$")
HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
LINK_PART_RE = re.compile(r'\s*<([^>]+)>\s*;\s*rel="([^"]+)"\s*(?:,\s*|$)')
GITHUB_TIMESTAMP_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})Z$"
)
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

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
    pullRequest(number:$number){
      baseRefOid
      body
      headRefOid
      lastEditedAt
      number
      title
      url
      editor{__typename login ... on User{databaseId}}
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
    number: int
    head_sha: str
    head_ref: str
    base_sha: str
    title: str
    body: str | None
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
    author_association: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    intent: EditReceipt | None
    confirmation: EditConfirmation | None


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
    requested_fields: tuple[EditFieldDigest, ...]
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
            "requested_fields": {
                field.field: field.sha256
                for field in self.requested_fields
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

    def __post_init__(self) -> None:
        for field, value in (
            ("run_id", self.run_id),
            ("comment_id", self.comment_id),
            ("intent_comment_id", self.intent_comment_id),
            ("confirmation_comment_id", self.confirmation_comment_id),
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
            self.intent_comment_id is not None
            or self.intent_comment_url is not None
            or self.confirmation_comment_id is not None
            or self.confirmation_comment_url is not None
        ):
            raise MetadataEditError(
                "only updated, recovered, or authoritative no-op/deferred "
                "Decisions may contain intent and confirmation comments"
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


def _requested_field_digests(
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
        raise MetadataEditError("edit receipt requires a requested field")
    return tuple(fields)


def _parse_metadata_version_payload(
    payload: object,
    *,
    label: str,
) -> MetadataVersion:
    if not isinstance(payload, dict) or set(payload) != {
        "body_editor_id",
        "body_editor_login",
        "body_last_edited_at",
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
    title_values = (
        title_event_id,
        title_event_created_at,
        title_previous,
        title_current,
        title_actor_id,
        title_actor_login,
    )
    if all(value is None for value in title_values):
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
    if body_last_edited_at is None:
        if body_editor_id is not None or body_editor_login is not None:
            raise MetadataEditError(f"{label} body version is invalid")
    else:
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
        "requested_fields",
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
    requested = payload["requested_fields"]
    if (
        not isinstance(requested, dict)
        or not requested
        or not set(requested) <= {"body", "title"}
    ):
        raise MetadataEditError("edit receipt requested_fields are invalid")
    requested_fields = []
    for field in sorted(requested):
        digest = requested[field]
        if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
            raise MetadataEditError(
                f"edit receipt {field} digest is invalid"
            )
        requested_fields.append(EditFieldDigest(field, digest))
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
        requested_fields=tuple(requested_fields),
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
) -> ApiResponse:
    if len(raw.encode("utf-8")) > MAX_API_BYTES:
        raise MetadataEditError(f"{label} response exceeds 4 MiB")
    crlf_boundary = raw.find("\r\n\r\n")
    lf_boundary = raw.find("\n\n")
    if crlf_boundary >= 0 and (lf_boundary < 0 or crlf_boundary <= lf_boundary):
        line_break = "\r\n"
        boundary = crlf_boundary
        header_text = raw[:boundary]
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
    if "location" in headers:
        raise MetadataEditError(f"{label} response unexpectedly contains Location")
    if not body_text:
        if allow_empty_body:
            return ApiResponse(status, headers, None)
        raise MetadataEditError(f"{label} response body is empty")
    content_type = headers.get("content-type", "")
    _validate_json_media_type(content_type, label=label)
    return ApiResponse(status, headers, _parse_json(body_text, label))


Runner = Callable[..., subprocess.CompletedProcess[str]]


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
        input_text = None
        if body is not None:
            arguments.extend(["--input", "-"])
            input_text = json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        environment = dict(os.environ)
        environment["GH_HOST"] = "github.com"
        environment.pop("GH_REPO", None)
        try:
            completed = self.runner(
                arguments,
                check=False,
                capture_output=True,
                text=True,
                input=input_text,
                env=environment,
                timeout=30,
            )
        except subprocess.TimeoutExpired as error:
            raise MetadataEditError(f"{label} request timed out") from error
        except OSError as error:
            raise MetadataEditError(f"{label} request could not start: {error}") from error
        if completed.returncode != 0:
            detail = completed.stderr.strip()
            if len(detail) > 512:
                detail = detail[:512] + "..."
            raise MetadataEditError(
                f"{label} request failed"
                + (f": {detail}" if detail else "")
            )
        response = _parse_http_response(
            completed.stdout,
            label=label,
            allow_empty_body=method == "POST",
        )
        expected_status = (
            200 if endpoint == "graphql" else 201 if method == "POST" else 200
        )
        if response.status != expected_status:
            raise MetadataEditError(
                f"{label} returned HTTP {response.status}, expected {expected_status}"
            )
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
) -> PullRequestState:
    if not isinstance(payload, dict):
        raise MetadataEditError("pull request response must be an object")
    if _positive_int(payload.get("number"), "pull request number") != pr_number:
        raise MetadataEditError("pull request number drifted")
    _require_api_url(
        payload.get("url"),
        _endpoint(repository, f"pulls/{pr_number}"),
        field="pull request URL",
    )
    if payload.get("state") != "open":
        raise MetadataEditError("pull request must be open")
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
    _text(base.get("ref"), "pull request base ref")
    title = _text(payload.get("title"), "pull request title")
    body = payload.get("body")
    if body is not None and not isinstance(body, str):
        raise MetadataEditError("pull request body must be text or null")
    updated_at = _github_timestamp(
        payload.get("updated_at"),
        "pull request updated_at",
    )
    return PullRequestState(
        repository=repository,
        repository_id=repository_id,
        repository_owner_id=repository_owner_id,
        number=pr_number,
        head_sha=_sha(head.get("sha"), "pull request head"),
        head_ref=head_ref,
        base_sha=_sha(base.get("sha"), "pull request base"),
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


def fetch_metadata_version(
    client: GitHubClient,
    state: PullRequestState,
) -> MetadataVersion:
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
    pull = repository.get("pullRequest")
    if (
        not isinstance(pull, dict)
        or pull.get("number") != state.number
        or pull.get("url")
        != f"https://github.com/{state.repository}/pull/{state.number}"
        or pull.get("headRefOid") != state.head_sha
        or pull.get("baseRefOid") != state.base_sha
        or pull.get("title") != state.title
        or pull.get("body") != state.body
    ):
        raise MetadataEditError("metadata version pull request identity drifted")
    body_last_edited_at = pull.get("lastEditedAt")
    body_editor_id = None
    body_editor_login = None
    if body_last_edited_at is not None:
        body_last_edited_at = _timestamp_text(
            _github_timestamp(
                body_last_edited_at,
                "pull request body lastEditedAt",
            )
        )
        body_editor_id, body_editor_login = _graphql_user(
            pull.get("editor"),
            label="pull request body",
            required=True,
        )
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
        return MetadataVersion(
            None,
            None,
            None,
            None,
            None,
            None,
            body_last_edited_at,
            body_editor_id,
            body_editor_login,
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
    if current_title != state.title:
        raise MetadataEditError("latest title event does not attest current title")
    return MetadataVersion(
        event_id,
        created_at,
        previous_title,
        current_title,
        actor_id,
        actor_login,
        body_last_edited_at,
        body_editor_id,
        body_editor_login,
    )


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
    if conclusion is not None and conclusion not in RUN_CONCLUSIONS:
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
    if conclusion is not None and conclusion not in RUN_CONCLUSIONS:
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
    seen_ids = set()
    seen_numbers = set()
    previous_number = None
    exact_runs = []
    for run_id, run_number, run in visible:
        if run_id in seen_ids or run_number in seen_numbers:
            raise MetadataEditError("Build workflow runs repeat an identity")
        if previous_number is not None and run_number >= previous_number:
            raise MetadataEditError("Build workflow runs are not newest-first")
        seen_ids.add(run_id)
        seen_numbers.add(run_number)
        previous_number = run_number
        exact_runs.append(run)
    return tuple(exact_runs)


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
        requested_fields=_requested_field_digests(title=title, body=body),
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


def _receipt_fields(receipt: EditReceipt) -> dict[str, str]:
    return {field.field: field.sha256 for field in receipt.requested_fields}


def _receipt_pre_fields(receipt: EditReceipt) -> dict[str, str]:
    return {field.field: field.sha256 for field in receipt.pre_fields}


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
    for field in receipt.requested_fields:
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
    requested = set(_receipt_fields(receipt))
    if "title" in requested:
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
    if "body" in requested:
        if (
            version.body_last_edited_at is None
            or version.body_last_edited_at
            == receipt.pre_version.body_last_edited_at
            or version.body_editor_id != state.repository_owner_id
            or version.body_editor_login != state.repository.split("/", 1)[0]
        ):
            raise MetadataEditError(
                "body metadata version does not uniquely attest the edit"
            )
    elif (
        version.body_last_edited_at
        != receipt.pre_version.body_last_edited_at
        or version.body_editor_id != receipt.pre_version.body_editor_id
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
        version=version,
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
        or confirmation.metadata_version != version
        or confirmation != expected
    ):
        raise MetadataEditError("edit confirmation authority is stale or forged")


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
    requested_fields = _requested_field_digests(title=title, body=body)
    initially_matches = (
        target_title == initial.title and target_body == initial.body
    )
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
        require_full_success(latest_full)

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
            require_full_success(latest_full)

    current_version = fetch_metadata_version(client, current)
    intents, confirmations = _transaction_comments(client, current)
    latest_intent_comment = _latest_intent(intents)
    latest_confirmation_comment = (
        confirmations.get(latest_intent_comment.comment_id)
        if latest_intent_comment is not None
        else None
    )
    intent = (
        latest_intent_comment.intent
        if latest_intent_comment is not None
        else None
    )
    patch_required = not initially_matches
    if intent is not None and latest_confirmation_comment is None:
        _validate_receipt_identity(intent, current)
        _validate_receipt_watermark(intent, current_runs)
        if (
            _receipt_fields(intent)
            != {field.field: field.sha256 for field in requested_fields}
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
        decision = Decision(
            action="no-op",
            base_sha=base_sha,
            guidance=(),
            head_sha=head_sha,
            mutated=False,
            reason="requested metadata already has an authoritative edit pair",
            repository=repository,
            pr_number=pr_number,
            intent_comment_id=latest_intent_comment.comment_id,
            intent_comment_url=latest_intent_comment.html_url,
            confirmation_comment_id=latest_confirmation_comment.comment_id,
            confirmation_comment_url=latest_confirmation_comment.html_url,
        )
        if any(
            run.binding == "explicit-same"
            and run.mode in {"metadata-only", "active-metadata-only"}
            and run.run_number > intent.watermark_run_number
            for run in current_runs
        ):
            return decision
        return Decision(
            action="deferred",
            base_sha=base_sha,
            guidance=_reconcile_guidance(
                current,
                latest_confirmation_comment.comment_id,
            ),
            head_sha=head_sha,
            mutated=False,
            reason=(
                "matching metadata has an authoritative pair but its "
                "continuity run is not visible yet"
            ),
            repository=repository,
            pr_number=pr_number,
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
        )
        latest_intent_comment = _create_intent_comment(
            client,
            current,
            intent,
        )

    if intent is None or latest_intent_comment is None:
        raise MetadataEditError("metadata edit intent state is incomplete")
    mutation: dict[str, object] = {}
    if title is not None:
        mutation["title"] = title
    if body is not None:
        mutation["body"] = body
    if patch_required:
        mutation_response = client.request(
            "PATCH",
            _endpoint(repository, f"pulls/{pr_number}"),
            body=mutation,
            label="pull request metadata update",
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


def _pending_metadata(
    runs: tuple[RunState, ...],
    receipt: EditReceipt,
) -> tuple[RunState, RunState | None]:
    latest_full = _latest_full(runs)
    if latest_full is None:
        raise MetadataEditError("no exact-head full Build exists")
    require_full_success(latest_full)
    metadata = next(
        (
            run
            for run in runs
            if run.binding == "explicit-same"
            and run.mode in {"metadata-only", "active-metadata-only"}
            and run.run_number > latest_full.run_number
            and run.run_number > receipt.watermark_run_number
        ),
        None,
    )
    return latest_full, metadata


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
    receipt, intent_comment, confirmation, confirmation_comment = (
        _authoritative_edit_pair(
            client,
            initial,
            confirmation_comment_id,
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
    first_runs = list_candidate_runs(client, initial)
    _validate_receipt_watermark(receipt, first_runs)
    blocking_active = _blocking_active_runs(first_runs)
    if blocking_active:
        return Decision(
            action="deferred",
            base_sha=base_sha,
            guidance=_reconcile_guidance(initial, confirmation_comment_id),
            head_sha=head_sha,
            mutated=False,
            reason="an exact-head full or unproven Build is still active",
            repository=repository,
            pr_number=pr_number,
            run_id=blocking_active[0].run_id,
        )
    first_full, first_metadata = _pending_metadata(first_runs, receipt)
    if first_metadata is None:
        return Decision(
            action="deferred",
            base_sha=base_sha,
            guidance=_reconcile_guidance(initial, confirmation_comment_id),
            head_sha=head_sha,
            mutated=False,
            reason="the exact metadata-only run is not visible yet",
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
        or current_receipt != receipt
        or current_intent_comment.comment_id != intent_comment.comment_id
        or current_confirmation != confirmation
        or current_confirmation_comment.comment_id
        != confirmation_comment.comment_id
        or current_version != initial_version
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
    current_runs = list_candidate_runs(client, current)
    _validate_receipt_watermark(receipt, current_runs)
    current_full, current_metadata = _pending_metadata(current_runs, receipt)
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
    if association_raw is not None:
        association = _text(
            association_raw,
            f"pull request comment {comment_id} author association",
        )
    evidence_marker_count = body.count(EVIDENCE_MARKER)
    intent_marker_count = body.count(INTENT_MARKER)
    confirmation_marker_count = body.count(CONFIRMATION_MARKER)
    marker_kinds = sum(
        bool(count)
        for count in (
            evidence_marker_count,
            intent_marker_count,
            confirmation_marker_count,
        )
    )
    if marker_kinds > 1:
        raise MetadataEditError("pull request comment mixes protected markers")
    protected = (
        evidence_marker_count
        or intent_marker_count
        or confirmation_marker_count
    )
    if protected:
        marker = (
            EVIDENCE_MARKER
            if evidence_marker_count
            else INTENT_MARKER
            if intent_marker_count
            else CONFIRMATION_MARKER
        )
        if protected != 1 or not _marker_is_standalone(body, marker):
            raise MetadataEditError(
                "protected comment marker is duplicated or embedded"
            )
        if (
            author_id != repository_owner_id
            or author_login != owner
            or author_type != "User"
            or not isinstance(user, dict)
            or user.get("site_admin") is not False
            or association != "OWNER"
        ):
            raise MetadataEditError(
                f"pull request comment {comment_id} author is not the repository owner"
            )
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
    if intent_marker_count or confirmation_marker_count:
        if updated_at != created_at:
            raise MetadataEditError(
                f"metadata edit transaction comment {comment_id} was edited"
            )
        if intent_marker_count:
            intent = _parse_intent_comment_body(body)
        else:
            confirmation = _parse_confirmation_comment_body(body)
    return CommentState(
        comment_id=comment_id,
        repository=repository,
        pr_number=pr_number,
        html_url=html_url,
        body=body,
        author_id=author_id,
        author_login=author_login,
        author_type=author_type,
        author_association=association,
        created_at=created_at,
        updated_at=updated_at,
        intent=intent,
        confirmation=confirmation,
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
) -> tuple[list[CommentState], dict[int, CommentState]]:
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
    by_intent = {}
    for comment in confirmations:
        confirmation = comment.confirmation
        if confirmation is None:
            raise MetadataEditError("metadata edit confirmation is invalid")
        if confirmation.intent_comment_id in by_intent:
            raise MetadataEditError(
                "metadata edit intent has duplicate confirmations"
            )
        if not any(
            intent.comment_id == confirmation.intent_comment_id
            for intent in intents
        ):
            raise MetadataEditError(
                "metadata edit confirmation references an unknown intent"
            )
        by_intent[confirmation.intent_comment_id] = comment
    return intents, by_intent


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


def _authoritative_edit_pair(
    client: GitHubClient,
    state: PullRequestState,
    confirmation_comment_id: int,
) -> tuple[EditReceipt, CommentState, EditConfirmation, CommentState]:
    intents, confirmations = _transaction_comments(client, state)
    latest = _latest_intent(intents)
    if latest is None or latest.intent is None:
        raise MetadataEditError("metadata edit intent comment is missing")
    confirmation_comment = confirmations.get(latest.comment_id)
    if (
        confirmation_comment is None
        or confirmation_comment.comment_id != confirmation_comment_id
        or confirmation_comment.confirmation is None
    ):
        raise MetadataEditError(
            "latest metadata edit intent lacks the selected confirmation"
        )
    confirmation = confirmation_comment.confirmation
    if (
        confirmation.intent_nonce != latest.intent.nonce
        or confirmation_comment.created_at <= latest.created_at
    ):
        raise MetadataEditError("metadata edit confirmation nonce drifted")
    _validate_receipt_identity(latest.intent, state)
    return latest.intent, latest, confirmation, confirmation_comment


def update_evidence_comment(
    client: GitHubClient,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    comment_body: str,
) -> Decision:
    if comment_body.count(EVIDENCE_MARKER) != 1 or not _marker_is_standalone(
        comment_body
    ):
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
