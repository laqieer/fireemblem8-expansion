#!/usr/bin/env python3
"""Validate inert sibling-family artifacts without granting authority."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.workflow_pilot import reporter


SCHEMA_VERSION = 5
FAMILY_MEMBERS = {
    "action": ("actions", "items", "targets"),
    "generated": ("owners", "outputs", "consumers", "drift-checks"),
    "lifecycle": ("entries", "preservation", "resets", "terminals"),
    "resource": ("enabled", "disabled"),
    "wire": ("producers", "consumers", "validators", "replay", "stale-bindings"),
}
EVIDENCE_CLASSES = ("positive", "adversarial", "default", "runtime")
SWEEP_RESULTS = {"affected-fixed", "not-applicable", "verified-unaffected"}
REMOTE_OUTCOMES = {"changes-requested", "clean"}
READ_ONLY_PERMISSIONS = ("contents:read",)
READ_ONLY_ACTIONS = ("read-candidate", "emit-local-report")
ARCHITECTURE_ACTIONS = {"decompose", "redesign", "retain-with-evidence"}
ACTOR_KINDS = {"bot", "service", "user"}
SERVICE_ACTOR_SOURCE = "local-service"
GITHUB_GRAPHQL_ACTOR_SOURCE = "github-graphql"
GITHUB_REST_ACTOR_SOURCE = "github-rest"
ACTOR_SOURCES = {
    SERVICE_ACTOR_SOURCE,
    GITHUB_GRAPHQL_ACTOR_SOURCE,
    GITHUB_REST_ACTOR_SOURCE,
}
ACTOR_TYPE_TO_KIND = {
    "Bot": "bot",
    "EnterpriseOwner": "user",
    "EnterpriseUserAccount": "user",
    "Mannequin": "user",
    "Organization": "user",
    "User": "user",
}
LARGE_TRIGGERS = {"changed-files", "changed-lines", "major-boundaries"}
LIMIT_CAPS = {
    "max_duration_minutes": 60,
    "max_findings_per_review": 50,
    "max_reviewed_files": 200,
    "max_siblings_per_finding": 5,
    "max_siblings_per_handoff": 250,
}
BEHAVIOR_ROW_SPECS = {
    "actor-permission-bounds": {
        "production": "immutable-pre-review-receipt",
        "execution": "base-owned-actor-and-bound-assertion",
    },
    "authority-causality": {
        "production": "authoritative-base-and-head-oids",
        "execution": "base-owned-git-authority-assertion",
    },
    "remote-review-metrics": {
        "production": "credentialed-live-github-collection",
        "execution": "base-owned-remote-state-assertion",
    },
    "round-lifecycle": {
        "production": "review-and-disposition-events",
        "execution": "base-owned-held-head-assertion",
    },
    "sibling-family-expansion": {
        "production": "local-and-remote-finding-namespaces",
        "execution": "base-owned-closed-family-assertion",
    },
}
REQUIRED_BEHAVIOR_ROWS = tuple(sorted(BEHAVIOR_ROW_SPECS))
BEHAVIOR_ASSERTION_IDS = {
    row: {
        evidence_class: f"registry:behavior:{row}:{evidence_class}:v2"
        for evidence_class in EVIDENCE_CLASSES
    }
    for row in REQUIRED_BEHAVIOR_ROWS
}
MEMBER_OUTCOME_REGISTRY = {
    family: {
        member: {"affected-fixed", "verified-unaffected"}
        for member in members
    }
    for family, members in FAMILY_MEMBERS.items()
}
RESULT_SOURCE_PATH = "scripts/workflow_pilot/tests/test_review_family.py"
ASSERTION_PROGRAM_PATH = "scripts/workflow_pilot/review_assertions.py"
TRIGGER_DECISION_PATH = reporter.DECISION_RECORD_PATH.as_posix()
ASSERTION_FILE_MODES = {"100644", "100755", "120000"}
ASSERTION_INPUT_PATHS = (
    TRIGGER_DECISION_PATH,
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
COPILOT_ACTOR = "copilot-pull-request-reviewer"
COPILOT_GRAPHQL_NODE_ID = "BOT_kgDOCnlnWA"
COPILOT_GRAPHQL_LOGIN = COPILOT_ACTOR
COPILOT_GRAPHQL_TYPE = "Bot"
COPILOT_REST_NODE_ID = reporter.REVIEW_BOT_NODE_ID
COPILOT_REST_LOGIN = reporter.REVIEW_BOT
COPILOT_REST_TYPE = reporter.REVIEW_BOT_TYPE
COPILOT_REST_DATABASE_ID = reporter.REVIEW_BOT_DATABASE_ID
COPILOT_APPROVAL_MARKER = "### 🟢 Approval recommended"
COPILOT_CHANGES_MARKER = "### 🟡 Changes recommended"
COPILOT_CLOSER_LOOK_MARKER = "### 🔵 Needs a closer look"
COPILOT_LEGACY_CLEAN_BODY = "No issues found."
COPILOT_TOP_LEVEL_MARKERS = {
    COPILOT_APPROVAL_MARKER,
    COPILOT_CHANGES_MARKER,
    COPILOT_CLOSER_LOOK_MARKER,
}
LOCAL_FINDING_RE = re.compile(r"^LOCAL-[A-Z0-9][A-Z0-9_-]{0,95}$")
ACTOR_LOGIN_RE = re.compile(
    r"^@?[A-Za-z0-9](?:[A-Za-z0-9_-]{0,98}[A-Za-z0-9])?(?:\[bot\])?$"
)
ACTOR_BOT_SUFFIX_RE = re.compile(r"(?:\[bot\]|[-_]bot)$", re.IGNORECASE)


def _expect_time(value: Any, label: str):
    parsed = reporter.parse_time(value, label)
    assert parsed is not None
    return value, parsed


def _expect_string_list(
    value: Any,
    label: str,
    *,
    allowed: set[str] | None = None,
    nonempty: bool = True,
) -> list[str]:
    values = reporter.expect_list(value, label)
    if nonempty and not values:
        raise reporter.PilotDataError(f"{label} must not be empty")
    for index, item in enumerate(values):
        reporter.expect_string(item, f"{label}[{index}]")
        if allowed is not None:
            reporter.expect_enum(item, allowed, f"{label}[{index}]")
    reporter.expect_unique(values, label)
    return values


def _validate_path(value: Any, label: str) -> str:
    source = reporter.expect_string(value, label)
    path = PurePosixPath(source)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or source != path.as_posix()
    ):
        raise reporter.PilotDataError(
            f"{label} must be a normalized repository-relative path"
        )
    return source


def _optional_mode(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in ASSERTION_FILE_MODES:
        raise reporter.PilotDataError(
            f"{label} must be null or an exact Git mode"
        )
    return value


def _optional_blob_oid(value: Any, label: str) -> str | None:
    return reporter.expect_sha(value, label, nullable=True)


def _tree_state(
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
            f"Git tree returned ambiguous path {path!r}"
        )
    try:
        metadata, raw_path = records[0].split(b"\t", 1)
        mode, kind, blob_oid = metadata.decode("ascii").split()
        actual_path = raw_path.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise reporter.PilotDataError(
            f"Git tree returned malformed path {path!r}"
        ) from error
    if actual_path != path or kind != "blob" or mode not in ASSERTION_FILE_MODES:
        raise reporter.PilotDataError(
            f"Git tree path {path!r} has an unsafe type or mode"
        )
    return {"mode": mode, "blob_oid": blob_oid}


def _validate_assertion_input_artifacts(
    value: Any, label: str
) -> list[dict[str, str | None]]:
    artifacts = []
    for index, raw in enumerate(reporter.expect_list(value, label)):
        item_label = f"{label}[{index}]"
        artifact = reporter.expect_object(raw, item_label)
        reporter.expect_keys(
            artifact,
            item_label,
            (
                "path",
                "base_mode",
                "base_blob_oid",
                "origin_mode",
                "origin_blob_oid",
                "head_mode",
                "head_blob_oid",
            ),
        )
        normalized = {
            "path": _validate_path(artifact["path"], f"{item_label}.path"),
            "base_mode": _optional_mode(
                artifact["base_mode"], f"{item_label}.base_mode"
            ),
            "base_blob_oid": _optional_blob_oid(
                artifact["base_blob_oid"], f"{item_label}.base_blob_oid"
            ),
            "origin_mode": _optional_mode(
                artifact["origin_mode"], f"{item_label}.origin_mode"
            ),
            "origin_blob_oid": _optional_blob_oid(
                artifact["origin_blob_oid"], f"{item_label}.origin_blob_oid"
            ),
            "head_mode": _optional_mode(
                artifact["head_mode"], f"{item_label}.head_mode"
            ),
            "head_blob_oid": _optional_blob_oid(
                artifact["head_blob_oid"], f"{item_label}.head_blob_oid"
            ),
        }
        for prefix in ("base", "origin", "head"):
            if (normalized[f"{prefix}_mode"] is None) != (
                normalized[f"{prefix}_blob_oid"] is None
            ):
                raise reporter.PilotDataError(
                    f"{item_label}.{prefix}_mode and {prefix}_blob_oid must both be null or both be present"
                )
        artifacts.append(normalized)
    return sorted(artifacts, key=lambda item: item["path"])


def _tree_blob(
    repository_root: Path, revision: str, path: str
) -> dict[str, str] | None:
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
        return None
    if len(records) != 1:
        raise reporter.PilotDataError(
            f"Git tree returned ambiguous path {path!r}"
        )
    try:
        metadata, raw_path = records[0].split(b"\t", 1)
        mode, kind, blob_oid = metadata.decode("ascii").split()
        actual_path = raw_path.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise reporter.PilotDataError(
            f"Git tree returned malformed path {path!r}"
        ) from error
    if (
        actual_path != path
        or kind != "blob"
        or mode not in {"100644", "100755"}
    ):
        raise reporter.PilotDataError(
            f"Git tree path {path!r} has an unsafe type or mode"
        )
    return {"mode": mode, "blob_oid": blob_oid}


def _expect_tree_identity(
    actual: dict[str, str] | None,
    *,
    mode: str | None,
    blob_oid: str | None,
    label: str,
) -> None:
    expected = (
        None
        if mode is None or blob_oid is None
        else {"mode": mode, "blob_oid": blob_oid}
    )
    if actual != expected:
        raise reporter.PilotDataError(
            f"{label} does not match exact Git tree identity"
        )


def derive_change_records(
    repository_root: Path, base_sha: str, head_sha: str
) -> list[dict[str, Any]]:
    """Derive status, path, mode, and blob identities from exact Git trees."""
    raw = reporter.run_git(
        repository_root,
        "diff",
        "--raw",
        "-z",
        "--no-abbrev",
        "-M",
        "-C",
        "--find-copies-harder",
        f"{base_sha}...{head_sha}",
        "--",
    )
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    records = []
    index = 0
    while index < len(fields):
        header = fields[index]
        index += 1
        if not header.startswith(b":"):
            raise reporter.PilotDataError("Git diff returned malformed raw status")
        try:
            old_mode, new_mode, old_oid, new_oid, status_token = (
                header[1:].decode("ascii").split()
            )
        except (UnicodeDecodeError, ValueError) as error:
            raise reporter.PilotDataError(
                "Git diff returned malformed raw metadata"
            ) from error
        status = status_token[:1]
        score_text = status_token[1:]
        if status not in {"A", "D", "M", "R", "C"}:
            raise reporter.PilotDataError(
                f"Git diff status {status_token!r} is not supported"
            )
        if (status in {"R", "C"}) != bool(score_text):
            raise reporter.PilotDataError(
                f"Git diff status {status_token!r} has an invalid score"
            )
        similarity = int(score_text) if score_text else None
        if similarity is not None and not 0 <= similarity <= 100:
            raise reporter.PilotDataError(
                f"Git diff status {status_token!r} has an invalid score"
            )
        path_count = 2 if status in {"R", "C"} else 1
        if index + path_count > len(fields):
            raise reporter.PilotDataError("Git diff omitted a status path")
        try:
            paths = [
                _validate_path(
                    fields[index + offset].decode("utf-8"),
                    "Git diff status path",
                )
                for offset in range(path_count)
            ]
        except UnicodeDecodeError as error:
            raise reporter.PilotDataError(
                "Git diff status path is not UTF-8"
            ) from error
        index += path_count

        if status == "A":
            old_path, new_path = None, paths[0]
            base_identity, head_identity = None, _tree_blob(
                repository_root, head_sha, new_path
            )
        elif status == "D":
            old_path, new_path = paths[0], None
            base_identity, head_identity = _tree_blob(
                repository_root, base_sha, old_path
            ), None
        elif status == "M":
            old_path = new_path = paths[0]
            base_identity = _tree_blob(repository_root, base_sha, old_path)
            head_identity = _tree_blob(repository_root, head_sha, new_path)
        else:
            old_path, new_path = paths
            if old_path == new_path:
                raise reporter.PilotDataError(
                    f"Git diff {status} status reuses one path"
                )
            base_identity = _tree_blob(repository_root, base_sha, old_path)
            head_identity = _tree_blob(repository_root, head_sha, new_path)

        zeros = "0" * 40
        expected_old_mode = "000000" if base_identity is None else base_identity["mode"]
        expected_new_mode = "000000" if head_identity is None else head_identity["mode"]
        expected_old_oid = zeros if base_identity is None else base_identity["blob_oid"]
        expected_new_oid = zeros if head_identity is None else head_identity["blob_oid"]
        if (
            old_mode != expected_old_mode
            or new_mode != expected_new_mode
            or old_oid != expected_old_oid
            or new_oid != expected_new_oid
        ):
            raise reporter.PilotDataError(
                f"Git diff {status} metadata disagrees with exact trees"
            )
        if (
            status in {"M", "R", "C"}
            and base_identity is not None
            and head_identity is not None
            and base_identity["mode"] != head_identity["mode"]
        ):
            raise reporter.PilotDataError(
                f"Git diff {status} status contains an unsupported mode change"
            )
        if status in {"A", "R", "C"}:
            _expect_tree_identity(
                _tree_blob(repository_root, base_sha, new_path),
                mode=None,
                blob_oid=None,
                label=f"{status} destination in base",
            )
        if status in {"D", "R"}:
            _expect_tree_identity(
                _tree_blob(repository_root, head_sha, old_path),
                mode=None,
                blob_oid=None,
                label=f"{status} source in head",
            )
        if status == "C":
            _expect_tree_identity(
                _tree_blob(repository_root, head_sha, old_path),
                mode=base_identity["mode"],
                blob_oid=base_identity["blob_oid"],
                label="copy source in head",
            )
        records.append(
            {
                "status": status,
                "similarity": similarity,
                "old_path": old_path,
                "new_path": new_path,
                "base_mode": (
                    base_identity["mode"] if base_identity is not None else None
                ),
                "base_blob_oid": (
                    base_identity["blob_oid"]
                    if base_identity is not None
                    else None
                ),
                "head_mode": (
                    head_identity["mode"] if head_identity is not None else None
                ),
                "head_blob_oid": (
                    head_identity["blob_oid"]
                    if head_identity is not None
                    else None
                ),
            }
        )
    return sorted(
        records,
        key=lambda record: (
            record["old_path"] or "",
            record["new_path"] or "",
            record["status"],
        ),
    )


def _validate_change_records(value: Any, label: str) -> list[dict[str, Any]]:
    result = []
    identities = []
    for index, raw in enumerate(reporter.expect_list(value, label)):
        item_label = f"{label}[{index}]"
        record = reporter.expect_object(raw, item_label)
        reporter.expect_keys(
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
        status = reporter.expect_enum(
            record["status"], {"A", "D", "M", "R", "C"}, f"{item_label}.status"
        )
        similarity = record["similarity"]
        if status in {"R", "C"}:
            similarity = reporter.expect_int(
                similarity, f"{item_label}.similarity", 0
            )
            if similarity > 100:
                raise reporter.PilotDataError(
                    f"{item_label}.similarity exceeds 100"
                )
        elif similarity is not None:
            raise reporter.PilotDataError(
                f"{item_label}.similarity is only valid for rename/copy"
            )

        def optional_path(field: str) -> str | None:
            value = record[field]
            return None if value is None else _validate_path(
                value, f"{item_label}.{field}"
            )

        def optional_mode(field: str) -> str | None:
            value = record[field]
            if value is None:
                return None
            if value not in {"100644", "100755"}:
                raise reporter.PilotDataError(
                    f"{item_label}.{field} has an unsafe mode"
                )
            return value

        def optional_blob(field: str) -> str | None:
            value = record[field]
            return None if value is None else reporter.expect_sha(
                value, f"{item_label}.{field}"
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
        if status == "A":
            valid = (
                normalized["old_path"] is None
                and normalized["base_mode"] is None
                and normalized["base_blob_oid"] is None
                and normalized["new_path"] is not None
                and normalized["head_mode"] is not None
                and normalized["head_blob_oid"] is not None
            )
        elif status == "D":
            valid = (
                normalized["old_path"] is not None
                and normalized["base_mode"] is not None
                and normalized["base_blob_oid"] is not None
                and normalized["new_path"] is None
                and normalized["head_mode"] is None
                and normalized["head_blob_oid"] is None
            )
        else:
            valid = all(
                normalized[field] is not None
                for field in (
                    "old_path",
                    "new_path",
                    "base_mode",
                    "base_blob_oid",
                    "head_mode",
                    "head_blob_oid",
                )
            )
            if status == "M":
                valid = valid and normalized["old_path"] == normalized["new_path"]
            else:
                valid = valid and normalized["old_path"] != normalized["new_path"]
            valid = valid and normalized["base_mode"] == normalized["head_mode"]
        if not valid:
            raise reporter.PilotDataError(
                f"{item_label} fields contradict status {status}"
            )
        identity = (
            status,
            normalized["old_path"],
            normalized["new_path"],
        )
        identities.append(identity)
        result.append(normalized)
    reporter.expect_unique(identities, f"{label} identities")
    return sorted(
        result,
        key=lambda record: (
            record["old_path"] or "",
            record["new_path"] or "",
            record["status"],
        ),
    )


def normalize_actor_login(value: Any, label: str = "actor login") -> str:
    login = reporter.expect_string(value, label)
    if ACTOR_LOGIN_RE.fullmatch(login) is None:
        raise reporter.PilotDataError(f"{label} is not a valid actor login")
    # This normalized family is for display/alias diagnostics only. Exact
    # authoritative actor authentication uses source/type/node/database IDs.
    normalized = login.removeprefix("@").casefold()
    while True:
        stripped = ACTOR_BOT_SUFFIX_RE.sub("", normalized)
        if stripped == normalized:
            break
        normalized = stripped
    if not normalized:
        raise reporter.PilotDataError(f"{label} has no normalized identity")
    return normalized


def actor_kind_from_source_type(
    source: str, type_name: str, label: str
) -> str:
    if source not in {
        GITHUB_GRAPHQL_ACTOR_SOURCE,
        GITHUB_REST_ACTOR_SOURCE,
    }:
        raise reporter.PilotDataError(
            f"{label} source {source!r} does not carry a GitHub actor type"
        )
    kind = ACTOR_TYPE_TO_KIND.get(type_name)
    if kind is None:
        raise reporter.PilotDataError(
            f"{label} must use a supported GitHub actor type"
        )
    return kind


def is_authoritative_copilot_actor(actor: dict[str, Any]) -> bool:
    if actor["source"] == GITHUB_GRAPHQL_ACTOR_SOURCE:
        return (
            actor["kind"] == "bot"
            and actor["type"] == COPILOT_GRAPHQL_TYPE
            and actor["id"] == COPILOT_GRAPHQL_NODE_ID
            and actor["login"] == COPILOT_GRAPHQL_LOGIN
        )
    if actor["source"] == GITHUB_REST_ACTOR_SOURCE:
        return (
            actor["kind"] == "bot"
            and actor["type"] == COPILOT_REST_TYPE
            and actor["id"] == COPILOT_REST_NODE_ID
            and actor["login"] == COPILOT_REST_LOGIN
            and actor["database_id"] == COPILOT_REST_DATABASE_ID
        )
    return False


def classify_copilot_body(value: Any, label: str = "Copilot review body") -> str:
    body = reporter.expect_string(value, label, allow_empty=True)
    if body == COPILOT_LEGACY_CLEAN_BODY:
        return "clean-legacy"
    lines = body.splitlines()
    if not lines:
        return "unknown"
    marker = lines[0]
    if marker not in COPILOT_TOP_LEVEL_MARKERS:
        return "unknown"
    if any(line in COPILOT_TOP_LEVEL_MARKERS for line in lines[1:]):
        return "unknown"
    if marker == COPILOT_APPROVAL_MARKER:
        return "clean-approval"
    if marker == COPILOT_CHANGES_MARKER:
        return "changes-recommended"
    return "needs-closer-look"


def normalize_trigger_fields(
    risk_boundaries: Any,
    threshold_triggers: Any,
    *,
    label: str,
) -> dict[str, list[str]]:
    risks = _expect_string_list(
        risk_boundaries,
        f"{label}.risk_boundaries",
        allowed=reporter.RISK_BOUNDARIES,
    )
    thresholds = _expect_string_list(
        threshold_triggers,
        f"{label}.threshold_triggers",
        allowed=reporter.THRESHOLD_TRIGGERS,
    )
    if "none" in risks and len(risks) != 1:
        raise reporter.PilotDataError(
            f"{label}.risk_boundaries none must stand alone"
        )
    if "none" in thresholds and len(thresholds) != 1:
        raise reporter.PilotDataError(
            f"{label}.threshold_triggers none must stand alone"
        )
    if "risk-boundary" in thresholds and risks == ["none"]:
        raise reporter.PilotDataError(
            f"{label} risk-boundary requires a named risk"
        )
    return {
        "risk_boundaries": sorted(risks),
        "threshold_triggers": sorted(thresholds),
    }


def trigger_requires_pre_review(trigger: dict[str, list[str]]) -> bool:
    return trigger["risk_boundaries"] != ["none"] or bool(
        set(trigger["threshold_triggers"]) & LARGE_TRIGGERS
    )


def _validate_trigger(value: Any) -> dict[str, list[str]]:
    trigger = reporter.expect_object(value, "contract.trigger")
    reporter.expect_keys(
        trigger,
        "contract.trigger",
        ("risk_boundaries", "threshold_triggers"),
    )
    return normalize_trigger_fields(
        trigger["risk_boundaries"],
        trigger["threshold_triggers"],
        label="contract.trigger",
    )


def _validate_authoritative_trigger(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    trigger = reporter.expect_object(
        value, "evidence.authoritative_trigger"
    )
    reporter.expect_keys(
        trigger,
        "evidence.authoritative_trigger",
        (
            "authority_kind",
            "path",
            "blob_oid",
            "comment_id",
            "comment_created_at",
            "pull_request",
            "base_sha",
            "original_pre_review_head",
            "candidate_sha",
            "risk_boundaries",
            "threshold_triggers",
            "pre_review_required",
        ),
    )
    authority_kind = reporter.expect_enum(
        trigger["authority_kind"],
        {"base-record", "external-comment"},
        "evidence.authoritative_trigger.authority_kind",
    )
    path = (
        None
        if trigger["path"] is None
        else _validate_path(
            trigger["path"], "evidence.authoritative_trigger.path"
        )
    )
    blob_oid = reporter.expect_sha(
        trigger["blob_oid"], "evidence.authoritative_trigger.blob_oid", nullable=True
    )
    comment_id = (
        None
        if trigger["comment_id"] is None
        else reporter.expect_string(
            trigger["comment_id"], "evidence.authoritative_trigger.comment_id"
        )
    )
    comment_created_at = None
    if trigger["comment_created_at"] is not None:
        comment_created_at = reporter.expect_string(
            trigger["comment_created_at"],
            "evidence.authoritative_trigger.comment_created_at",
        )
        reporter.parse_time(
            comment_created_at,
            "evidence.authoritative_trigger.comment_created_at",
        )
    if authority_kind == "base-record":
        if path != TRIGGER_DECISION_PATH or blob_oid is None:
            raise reporter.PilotDataError(
                "base-record authoritative trigger must bind .github/workflow-pilot-decisions.json"
            )
        if comment_id is not None or comment_created_at is not None:
            raise reporter.PilotDataError(
                "base-record authoritative trigger cannot carry external comment metadata"
            )
    else:
        if path is not None or blob_oid is not None:
            raise reporter.PilotDataError(
                "external-comment authoritative trigger cannot bind a repository path/blob"
            )
        if comment_id is None or comment_created_at is None:
            raise reporter.PilotDataError(
                "external-comment authoritative trigger must bind one exact trusted comment"
            )
    normalized_trigger = normalize_trigger_fields(
        trigger["risk_boundaries"],
        trigger["threshold_triggers"],
        label="evidence.authoritative_trigger",
    )
    pre_review_required = reporter.expect_bool(
        trigger["pre_review_required"],
        "evidence.authoritative_trigger.pre_review_required",
    )
    if pre_review_required != trigger_requires_pre_review(normalized_trigger):
        raise reporter.PilotDataError(
            "authoritative trigger decision pre_review_required is inconsistent"
        )
    return {
        "authority_kind": authority_kind,
        "path": path,
        "blob_oid": blob_oid,
        "comment_id": comment_id,
        "comment_created_at": comment_created_at,
        "pull_request": reporter.expect_int(
            trigger["pull_request"], "evidence.authoritative_trigger.pull_request", 1
        ),
        "base_sha": reporter.expect_sha(
            trigger["base_sha"], "evidence.authoritative_trigger.base_sha"
        ),
        "original_pre_review_head": reporter.expect_sha(
            trigger["original_pre_review_head"],
            "evidence.authoritative_trigger.original_pre_review_head",
        ),
        "candidate_sha": reporter.expect_sha(
            trigger["candidate_sha"], "evidence.authoritative_trigger.candidate_sha"
        ),
        "trigger": normalized_trigger,
        "pre_review_required": pre_review_required,
    }


def _validate_limits(value: Any) -> dict[str, int]:
    limits = reporter.expect_object(value, "contract.limits")
    reporter.expect_keys(limits, "contract.limits", LIMIT_CAPS)
    result = {}
    for name, maximum in LIMIT_CAPS.items():
        amount = reporter.expect_int(limits[name], f"contract.limits.{name}", 1)
        if amount > maximum:
            raise reporter.PilotDataError(
                f"contract.limits.{name} exceeds bounded maximum {maximum}"
            )
        result[name] = amount
    return result


def member_assertion_id(family: str, member: str, disposition: str) -> str:
    if (
        family == "resource"
        and member == "disabled"
        and disposition == "not-applicable"
    ):
        return (
            "registry:sibling:resource:disabled:not-applicable:"
            "feature-disabled-by-contract:v2"
        )
    return f"registry:sibling:{family}:{member}:{disposition}:v2"


def parse_assertion_id(assertion_id: str) -> dict[str, Any]:
    parts = assertion_id.split(":")
    if (
        len(parts) == 5
        and parts[:2] == ["registry", "behavior"]
        and parts[2] in BEHAVIOR_ROW_SPECS
        and parts[3] in EVIDENCE_CLASSES
        and parts[4] == "v2"
    ):
        return {
            "kind": "behavior",
            "row": parts[2],
            "evidence_class": parts[3],
        }
    if len(parts) not in {6, 7} or parts[:2] != ["registry", "sibling"]:
        raise reporter.PilotDataError("assertion ID is absent from exact-base registry")
    family, member, outcome = parts[2:5]
    reason = parts[5] if len(parts) == 7 else None
    if (
        family not in FAMILY_MEMBERS
        or member not in FAMILY_MEMBERS[family]
        or parts[-1] != "v2"
    ):
        raise reporter.PilotDataError("assertion member is absent from registry")
    if outcome not in {"affected-fixed", "verified-unaffected", "not-applicable"}:
        raise reporter.PilotDataError("assertion outcome is absent from registry")
    if outcome == "not-applicable":
        if (
            family,
            member,
            reason,
        ) != ("resource", "disabled", "feature-disabled-by-contract"):
            raise reporter.PilotDataError("not-applicable reason is not registered")
    elif reason is not None:
        raise reporter.PilotDataError("outcome assertion has an unexpected reason")
    return {
        "kind": "member",
        "family": family,
        "member": member,
        "outcome": outcome,
        "reason": reason,
    }


def _validate_behavior_rows(value: Any) -> list[dict[str, Any]]:
    rows = reporter.expect_list(value, "contract.behavior_rows")
    normalized = []
    row_ids = []
    for index, raw in enumerate(rows):
        label = f"contract.behavior_rows[{index}]"
        row = reporter.expect_object(raw, label)
        reporter.expect_keys(row, label, ("id", "assertions"))
        row_id = reporter.expect_enum(
            row["id"], set(REQUIRED_BEHAVIOR_ROWS), f"{label}.id"
        )
        row_ids.append(row_id)
        assertions = reporter.expect_object(
            row["assertions"], f"{label}.assertions"
        )
        reporter.expect_keys(
            assertions, f"{label}.assertions", EVIDENCE_CLASSES
        )
        normalized_assertions = {}
        for evidence_class in EVIDENCE_CLASSES:
            assertion_id = reporter.expect_string(
                assertions[evidence_class],
                f"{label}.assertions.{evidence_class}",
            )
            expected = BEHAVIOR_ASSERTION_IDS[row_id][evidence_class]
            if assertion_id != expected:
                raise reporter.PilotDataError(
                    f"{label}.{evidence_class} does not name the closed "
                    "base assertion"
                )
            normalized_assertions[evidence_class] = assertion_id
        normalized.append({"id": row_id, "assertions": normalized_assertions})
    reporter.expect_unique(row_ids, "contract.behavior_rows identities")
    if set(row_ids) != set(REQUIRED_BEHAVIOR_ROWS):
        raise reporter.PilotDataError(
            "contract.behavior_rows do not exactly cover the frozen inventory"
        )
    return sorted(normalized, key=lambda row: row["id"])


def _validate_sweeps(value: Any) -> list[dict[str, Any]]:
    sweeps = reporter.expect_list(value, "contract.family_sweeps")
    normalized = []
    finding_ids = []
    for index, raw in enumerate(sweeps):
        label = f"contract.family_sweeps[{index}]"
        sweep = reporter.expect_object(raw, label)
        reporter.expect_keys(sweep, label, ("finding_id", "siblings"))
        finding_id = reporter.expect_string(
            sweep["finding_id"], f"{label}.finding_id"
        )
        finding_ids.append(finding_id)
        siblings = reporter.expect_list(sweep["siblings"], f"{label}.siblings")
        members = [
            reporter.expect_string(
                reporter.expect_object(item, f"{label}.siblings[{position}]")[
                    "member"
                ],
                f"{label}.siblings[{position}].member",
            )
            for position, item in enumerate(siblings)
        ]
        reporter.expect_unique(members, f"{label}.siblings members")
        families = [
            family
            for family, expected_members in FAMILY_MEMBERS.items()
            if set(members) == set(expected_members)
        ]
        if len(families) != 1:
            raise reporter.PilotDataError(
                f"{label} does not identify one exact closed sibling family"
            )
        family = families[0]
        normalized_siblings = []
        for position, raw_sibling in enumerate(siblings):
            sibling_label = f"{label}.siblings[{position}]"
            sibling = reporter.expect_object(raw_sibling, sibling_label)
            reporter.expect_keys(
                sibling,
                sibling_label,
                (
                    "member",
                    "result",
                    "assertion_id",
                ),
            )
            member = reporter.expect_enum(
                sibling["member"], set(FAMILY_MEMBERS[family]), f"{sibling_label}.member"
            )
            disposition = reporter.expect_enum(
                sibling["result"], SWEEP_RESULTS, f"{sibling_label}.result"
            )
            registered = MEMBER_OUTCOME_REGISTRY[family][member]
            explicitly_permitted_not_applicable = (
                family == "resource"
                and member == "disabled"
                and disposition == "not-applicable"
            )
            if (
                disposition not in registered
                and not explicitly_permitted_not_applicable
            ):
                raise reporter.PilotDataError(
                    f"{sibling_label} disposition is not registered for "
                    f"{family}/{member}"
                )
            assertion_id = reporter.expect_string(
                sibling["assertion_id"], f"{sibling_label}.assertion_id"
            )
            expected_assertion = member_assertion_id(
                family, member, disposition
            )
            if assertion_id != expected_assertion:
                raise reporter.PilotDataError(
                    f"{sibling_label} does not name its member-specific "
                    "base assertion"
                )
            normalized_siblings.append(
                {
                    "member": member,
                    "result": disposition,
                    "assertion_id": assertion_id,
                }
            )
        if not any(
            sibling["result"] == "affected-fixed"
            for sibling in normalized_siblings
        ):
            raise reporter.PilotDataError(
                f"{label} must include at least one affected-fixed member"
            )
        normalized.append(
            {
                "finding_id": finding_id,
                "family": family,
                "siblings": sorted(
                    normalized_siblings, key=lambda sibling: sibling["member"]
                ),
            }
        )
    reporter.expect_unique(finding_ids, "contract.family_sweeps finding IDs")
    return sorted(normalized, key=lambda sweep: sweep["finding_id"])


def validate_contract(raw_contract: Any) -> dict[str, Any]:
    contract = reporter.expect_object(raw_contract, "contract")
    reporter.expect_keys(
        contract,
        "contract",
        (
            "schema_version",
            "repository",
            "pull_request",
            "base_sha",
            "original_pre_review_head",
            "candidate_sha",
            "trust_mode",
            "implementer_actor_id",
            "trigger",
            "limits",
            "behavior_rows",
            "family_sweeps",
        ),
    )
    if reporter.expect_int(
        contract["schema_version"], "contract.schema_version", 1
    ) != SCHEMA_VERSION:
        raise reporter.PilotDataError(
            f"contract.schema_version must be {SCHEMA_VERSION}"
        )
    trigger = _validate_trigger(contract["trigger"])
    base_sha = reporter.expect_sha(contract["base_sha"], "contract.base_sha")
    candidate_sha = reporter.expect_sha(
        contract["candidate_sha"], "contract.candidate_sha"
    )
    original_pre_review_head = reporter.expect_sha(
        contract["original_pre_review_head"],
        "contract.original_pre_review_head",
    )
    if base_sha == candidate_sha:
        raise reporter.PilotDataError(
            "contract base and candidate must be distinct"
        )
    return {
        "raw": contract,
        "repository": reporter.expect_string(
            contract["repository"], "contract.repository"
        ),
        "pull_request": reporter.expect_int(
            contract["pull_request"], "contract.pull_request", 1
        ),
        "base_sha": base_sha,
        "original_pre_review_head": original_pre_review_head,
        "candidate_sha": candidate_sha,
        "trust_mode": reporter.expect_enum(
            contract["trust_mode"],
            {"introduction", "base-pinned"},
            "contract.trust_mode",
        ),
        "implementer_actor_id": reporter.expect_string(
            contract["implementer_actor_id"], "contract.implementer_actor_id"
        ),
        "trigger": trigger,
        "limits": _validate_limits(contract["limits"]),
        "behavior_rows": _validate_behavior_rows(contract["behavior_rows"]),
        "family_sweeps": _validate_sweeps(contract["family_sweeps"]),
    }


def finding_family_map(contract: dict[str, Any]) -> dict[str, str]:
    return {
        sweep["finding_id"]: sweep["family"]
        for sweep in contract["family_sweeps"]
    }


def assertion_authority_binding(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    authority_root: Path,
    target_head: str,
    review_round: int,
    assertion_id: str,
    finding_id: str | None,
) -> dict[str, Any]:
    head_tree = reporter.run_git(
        authority_root, "rev-parse", f"{target_head}^{{tree}}"
    ).decode("ascii").strip()
    parsed = parse_assertion_id(assertion_id)
    if parsed["kind"] == "behavior":
        if finding_id is not None:
            raise reporter.PilotDataError(
                "behavior assertion cannot reference a finding"
            )
        return {
            "finding_id": None,
            "finding_family": None,
            "finding_member": None,
            "finding_review_id": None,
            "finding_review_round": None,
            "finding_head_sha": None,
            "finding_head_tree": None,
            "finding_origin_sha": None,
            "finding_origin_tree": None,
            "head_sha": target_head,
            "head_tree": head_tree,
        }

    if finding_id is None:
        raise reporter.PilotDataError("member assertion requires a finding ID")
    if review_round == 1:
        finding = evidence["pre_review_findings"].get(finding_id)
        if finding is None:
            raise reporter.PilotDataError(
                "member assertion finding is absent from the authoritative source round"
            )
        finding_head_sha = contract["original_pre_review_head"]
        finding_head_tree = reporter.run_git(
            authority_root,
            "rev-parse",
            f"{finding_head_sha}^{{tree}}",
        ).decode("ascii").strip()
        finding_origin_sha = contract["base_sha"]
        finding_origin_tree = reporter.run_git(
            authority_root,
            "rev-parse",
            f"{contract['base_sha']}^{{tree}}",
        ).decode("ascii").strip()
        finding_review_round = 0
    else:
        prior_review = evidence["remote_reviews"][review_round - 2]
        finding = evidence["findings"].get(finding_id)
        if finding is None or finding["review_id"] != prior_review["node_id"]:
            raise reporter.PilotDataError(
                "member assertion finding is absent from the authoritative source round"
            )
        finding_head_sha = prior_review["candidate_sha"]
        finding_head_tree = reporter.run_git(
            authority_root,
            "rev-parse",
            f"{finding_head_sha}^{{tree}}",
        ).decode("ascii").strip()
        finding_origin_sha = finding_head_sha
        finding_origin_tree = finding_head_tree
        finding_review_round = prior_review["round"]
    if finding["candidate_sha"] != finding_head_sha:
        raise reporter.PilotDataError(
            f"finding {finding_id!r} does not bind its authoritative head"
        )
    if finding["family"] != parsed["family"]:
        raise reporter.PilotDataError(
            f"finding {finding_id!r} family does not match its assertion"
        )
    return {
        "finding_id": finding_id,
        "finding_family": finding["family"],
        "finding_member": parsed["member"],
        "finding_review_id": finding["review_id"],
        "finding_review_round": finding_review_round,
        "finding_head_sha": finding_head_sha,
        "finding_head_tree": finding_head_tree,
        "finding_origin_sha": finding_origin_sha,
        "finding_origin_tree": finding_origin_tree,
        "head_sha": target_head,
        "head_tree": head_tree,
    }


def assertion_result_id(
    review_round: int,
    assertion_id: str,
    authority_binding: dict[str, Any],
) -> str:
    identity = {
        "review_round": review_round,
        "assertion_id": assertion_id,
        "authority_binding": authority_binding,
    }
    return "result-" + hashlib.sha256(
        reporter.normalized_json(identity)
    ).hexdigest()


def build_assertion_requests(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    target_head: str,
    review_round: int,
) -> list[dict[str, Any]]:
    """Derive assertion identities for one exact reviewed round and head."""
    reviews = {
        review["round"]: review for review in evidence.get("remote_reviews", [])
    }
    review = reviews.get(review_round)
    if review is None or review["candidate_sha"] != target_head:
        raise reporter.PilotDataError(
            "assertion round does not match its exact reviewed head"
        )
    requests = []
    for row in contract["behavior_rows"]:
        for evidence_class in EVIDENCE_CLASSES:
            requests.append(
                {
                    "assertion_id": row["assertions"][evidence_class],
                    "finding_id": None,
                }
            )
    if review_round == 1:
        round_finding_ids = {
            finding["id"] for finding in evidence.get("pre_review_findings", [])
        }
    else:
        round_finding_ids = set(reviews[review_round - 1]["finding_ids"])
    for sweep in contract["family_sweeps"]:
        finding_id = sweep["finding_id"]
        if finding_id not in round_finding_ids:
            continue
        requests.extend(
            {
                "assertion_id": sibling["assertion_id"],
                "finding_id": finding_id,
            }
            for sibling in sweep["siblings"]
        )
    assertion_keys = [
        (request["assertion_id"], request["finding_id"]) for request in requests
    ]
    reporter.expect_unique(assertion_keys, "derived assertion requests")
    return sorted(
        requests,
        key=lambda request: (
            request["assertion_id"],
            request["finding_id"] or "",
        ),
    )


def _validate_actors(value: Any) -> dict[str, dict[str, Any]]:
    actors = {}
    normalized = []
    for index, raw in enumerate(reporter.expect_list(value, "evidence.actors")):
        label = f"evidence.actors[{index}]"
        actor = reporter.expect_object(raw, label)
        source = reporter.expect_enum(
            actor.get("source"), ACTOR_SOURCES, f"{label}.source"
        )
        if source == SERVICE_ACTOR_SOURCE:
            reporter.expect_keys(actor, label, ("id", "login", "kind", "source"))
            kind = reporter.expect_enum(actor["kind"], {"service"}, f"{label}.kind")
            type_name = None
            database_id = None
        elif source == GITHUB_GRAPHQL_ACTOR_SOURCE:
            reporter.expect_keys(
                actor, label, ("id", "login", "kind", "source", "type")
            )
            type_name = reporter.expect_string(actor["type"], f"{label}.type")
            kind = actor_kind_from_source_type(
                source, type_name, f"{label}.type"
            )
            if actor["kind"] != kind:
                raise reporter.PilotDataError(
                    f"{label}.kind does not match the explicit GitHub actor type"
                )
            database_id = None
        else:
            reporter.expect_keys(
                actor,
                label,
                ("id", "login", "kind", "source", "type", "database_id"),
            )
            type_name = reporter.expect_string(actor["type"], f"{label}.type")
            kind = actor_kind_from_source_type(
                source, type_name, f"{label}.type"
            )
            if actor["kind"] != kind:
                raise reporter.PilotDataError(
                    f"{label}.kind does not match the explicit GitHub actor type"
                )
            database_id = reporter.expect_int(
                actor["database_id"], f"{label}.database_id", 1
            )
        actor_id = reporter.expect_string(actor["id"], f"{label}.id")
        if actor_id in actors:
            raise reporter.PilotDataError(f"duplicate actor ID {actor_id!r}")
        login = reporter.expect_string(actor["login"], f"{label}.login")
        normalized_login = normalize_actor_login(login, f"{label}.login")
        normalized.append(normalized_login)
        actors[actor_id] = {
            "id": actor_id,
            "login": login,
            "normalized_login": normalized_login,
            "kind": reporter.expect_enum(kind, ACTOR_KINDS, f"{label}.kind"),
            "source": source,
            "type": type_name,
            "database_id": database_id,
        }
    reporter.expect_unique(normalized, "evidence actor identities")
    return actors


def _validate_pre_reviews(value: Any) -> list[dict[str, Any]]:
    result = []
    ids = []
    for index, raw in enumerate(
        reporter.expect_list(value, "evidence.pre_reviews")
    ):
        label = f"evidence.pre_reviews[{index}]"
        review = reporter.expect_object(raw, label)
        reporter.expect_keys(
            review,
            label,
            (
                "id",
                "owner_actor_id",
                "candidate_sha",
                "started_at",
                "completed_at",
                "receipt_issued_at",
                "permissions",
                "actions",
                "finding_ids",
                "reviewed_files",
                "reviewed_changes",
            ),
        )
        review_id = reporter.expect_string(review["id"], f"{label}.id")
        ids.append(review_id)
        started_at, started = _expect_time(review["started_at"], f"{label}.started_at")
        completed_at, completed = _expect_time(
            review["completed_at"], f"{label}.completed_at"
        )
        issued_at, issued = _expect_time(
            review["receipt_issued_at"], f"{label}.receipt_issued_at"
        )
        if completed <= started or issued < completed:
            raise reporter.PilotDataError(
                f"{label} has a noncausal or backdated immutable receipt"
            )
        actions = []
        action_ids = []
        action_times = []
        for position, raw_action in enumerate(
            reporter.expect_list(review["actions"], f"{label}.actions")
        ):
            action_label = f"{label}.actions[{position}]"
            action = reporter.expect_object(raw_action, action_label)
            reporter.expect_keys(
                action, action_label, ("id", "kind", "occurred_at")
            )
            action_id = reporter.expect_string(action["id"], f"{action_label}.id")
            occurred_at, occurred = _expect_time(
                action["occurred_at"], f"{action_label}.occurred_at"
            )
            action_ids.append(action_id)
            action_times.append(occurred)
            actions.append(
                {
                    "id": action_id,
                    "kind": reporter.expect_string(
                        action["kind"], f"{action_label}.kind"
                    ),
                    "occurred_at": occurred_at,
                }
            )
        reporter.expect_unique(action_ids, f"{label}.actions IDs")
        if action_times != sorted(action_times) or len(set(action_times)) != len(
            action_times
        ):
            raise reporter.PilotDataError(
                f"{label}.actions are not strictly chronological"
            )
        if any(time < started or time > completed for time in action_times):
            raise reporter.PilotDataError(
                f"{label}.actions fall outside the review interval"
            )
        reviewed_files = [
            _validate_path(path, f"{label}.reviewed_files[{position}]")
            for position, path in enumerate(
                reporter.expect_list(
                    review["reviewed_files"], f"{label}.reviewed_files"
                )
            )
        ]
        reporter.expect_unique(reviewed_files, f"{label}.reviewed_files")
        reviewed_changes = _validate_change_records(
            review["reviewed_changes"], f"{label}.reviewed_changes"
        )
        result.append(
            {
                "id": review_id,
                "owner_actor_id": reporter.expect_string(
                    review["owner_actor_id"], f"{label}.owner_actor_id"
                ),
                "candidate_sha": reporter.expect_sha(
                    review["candidate_sha"], f"{label}.candidate_sha"
                ),
                "started_at": started_at,
                "completed_at": completed_at,
                "receipt_issued_at": issued_at,
                "permissions": _expect_string_list(
                    review["permissions"], f"{label}.permissions"
                ),
                "actions": actions,
                "finding_ids": _expect_string_list(
                    review["finding_ids"], f"{label}.finding_ids", nonempty=False
                ),
                "reviewed_files": reviewed_files,
                "reviewed_changes": reviewed_changes,
                "_started": started,
                "_completed": completed,
                "_issued": issued,
            }
        )
    reporter.expect_unique(ids, "evidence.pre_reviews IDs")
    return result


def _validate_remote_reviews(value: Any) -> list[dict[str, Any]]:
    result = []
    ids = []
    node_ids = []
    previous = None
    for index, raw in enumerate(
        reporter.expect_list(value, "evidence.remote_reviews")
    ):
        label = f"evidence.remote_reviews[{index}]"
        review = reporter.expect_object(raw, label)
        reporter.expect_keys(
            review,
            label,
            (
                "id",
                "node_id",
                "round",
                "reviewer_actor_id",
                "candidate_sha",
                "submitted_at",
                "state",
                "body",
                "body_classification",
                "body_has_findings",
                "outcome",
                "finding_ids",
            ),
        )
        round_number = reporter.expect_int(review["round"], f"{label}.round", 1)
        if round_number != index + 1:
            raise reporter.PilotDataError(
                "remote review rounds must be consecutive from 1"
            )
        submitted_at, submitted = _expect_time(
            review["submitted_at"], f"{label}.submitted_at"
        )
        if previous is not None and submitted <= previous:
            raise reporter.PilotDataError(
                "remote review timestamps must be strictly chronological"
            )
        previous = submitted
        body = reporter.expect_string(review["body"], f"{label}.body", allow_empty=True)
        body_classification = reporter.expect_enum(
            review["body_classification"],
            {
                "clean-approval",
                "clean-legacy",
                "changes-recommended",
                "needs-closer-look",
                "unknown",
            },
            f"{label}.body_classification",
        )
        expected_classification = classify_copilot_body(
            body, f"{label}.body"
        )
        if body_classification != expected_classification:
            raise reporter.PilotDataError(
                f"{label}.body_classification contradicts exact top-level marker"
            )
        body_has_findings = reporter.expect_bool(
            review["body_has_findings"], f"{label}.body_has_findings"
        )
        if body_has_findings != (
            body_classification not in {"clean-approval", "clean-legacy"}
        ):
            raise reporter.PilotDataError(
                f"{label}.body_has_findings contradicts review body"
            )
        review_id = reporter.expect_int(review["id"], f"{label}.id", 1)
        node_id = reporter.expect_string(review["node_id"], f"{label}.node_id")
        ids.append(review_id)
        node_ids.append(node_id)
        result.append(
            {
                "id": review_id,
                "node_id": node_id,
                "round": round_number,
                "reviewer_actor_id": reporter.expect_string(
                    review["reviewer_actor_id"], f"{label}.reviewer_actor_id"
                ),
                "candidate_sha": reporter.expect_sha(
                    review["candidate_sha"], f"{label}.candidate_sha"
                ),
                "submitted_at": submitted_at,
                "state": reporter.expect_enum(
                    review["state"],
                    {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"},
                    f"{label}.state",
                ),
                "body": body,
                "body_classification": body_classification,
                "body_has_findings": body_has_findings,
                "outcome": reporter.expect_enum(
                    review["outcome"], REMOTE_OUTCOMES, f"{label}.outcome"
                ),
                "finding_ids": _expect_string_list(
                    review["finding_ids"], f"{label}.finding_ids", nonempty=False
                ),
                "_submitted": submitted,
            }
        )
    reporter.expect_unique(ids, "evidence.remote_reviews numeric IDs")
    reporter.expect_unique(node_ids, "evidence.remote_reviews node IDs")
    return result


def _validate_findings(value: Any, label: str, *, local: bool):
    result = {}
    for index, raw in enumerate(reporter.expect_list(value, label)):
        item_label = f"{label}[{index}]"
        finding = reporter.expect_object(raw, item_label)
        key_name = "id" if local else "node_id"
        reporter.expect_keys(
            finding,
            item_label,
            (
                key_name,
                "review_id",
                "candidate_sha",
                "created_at",
                "author_actor_id",
                "family",
            ),
            optional=(
                "authority_comment_id",
                "authority_comment_created_at",
            ),
        )
        finding_id = reporter.expect_string(finding[key_name], f"{item_label}.{key_name}")
        if local and LOCAL_FINDING_RE.fullmatch(finding_id) is None:
            raise reporter.PilotDataError(
                f"{item_label}.id must use the independent LOCAL- namespace"
            )
        if not local and LOCAL_FINDING_RE.fullmatch(finding_id):
            raise reporter.PilotDataError(
                f"{item_label}.node_id overlaps the independent namespace"
            )
        if finding_id in result:
            raise reporter.PilotDataError(f"duplicate finding ID {finding_id!r}")
        created_at, created = _expect_time(
            finding["created_at"], f"{item_label}.created_at"
        )
        result[finding_id] = {
            key_name: finding_id,
            "id": finding_id,
            "review_id": reporter.expect_string(
                finding["review_id"], f"{item_label}.review_id"
            ),
            "candidate_sha": reporter.expect_sha(
                finding["candidate_sha"], f"{item_label}.candidate_sha"
            ),
            "created_at": created_at,
            "author_actor_id": reporter.expect_string(
                finding["author_actor_id"], f"{item_label}.author_actor_id"
            ),
            "family": reporter.expect_enum(
                finding["family"], set(FAMILY_MEMBERS), f"{item_label}.family"
            ),
            "authority_comment_id": (
                None
                if finding.get("authority_comment_id") is None
                else reporter.expect_string(
                    finding["authority_comment_id"],
                    f"{item_label}.authority_comment_id",
                )
            ),
            "authority_comment_created_at": (
                None
                if finding.get("authority_comment_created_at") is None
                else reporter.expect_string(
                    finding["authority_comment_created_at"],
                    f"{item_label}.authority_comment_created_at",
                )
            ),
            "_created": created,
        }
        if result[finding_id]["authority_comment_created_at"] is not None:
            reporter.parse_time(
                result[finding_id]["authority_comment_created_at"],
                f"{item_label}.authority_comment_created_at",
            )
    return result


def _validate_threads(value: Any) -> dict[str, dict[str, Any]]:
    threads = {}
    node_ids = []
    for index, raw in enumerate(reporter.expect_list(value, "evidence.threads")):
        label = f"evidence.threads[{index}]"
        thread = reporter.expect_object(raw, label)
        reporter.expect_keys(thread, label, ("node_id", "finding_id", "is_resolved"))
        finding_id = reporter.expect_string(
            thread["finding_id"], f"{label}.finding_id"
        )
        if finding_id in threads:
            raise reporter.PilotDataError(
                f"duplicate review thread finding ID {finding_id!r}"
            )
        node_id = reporter.expect_string(thread["node_id"], f"{label}.node_id")
        node_ids.append(node_id)
        threads[finding_id] = {
            "node_id": node_id,
            "finding_id": finding_id,
            "is_resolved": reporter.expect_bool(
                thread["is_resolved"], f"{label}.is_resolved"
            ),
        }
    reporter.expect_unique(node_ids, "evidence thread node IDs")
    return threads


def _validate_dispositions(value: Any) -> list[dict[str, Any]]:
    result = []
    ids = []
    held_rounds = []
    previous = None
    for index, raw in enumerate(
        reporter.expect_list(value, "evidence.architecture_dispositions")
    ):
        label = f"evidence.architecture_dispositions[{index}]"
        event = reporter.expect_object(raw, label)
        reporter.expect_keys(
            event,
            label,
            (
                "node_id",
                "held_round",
                "held_head_sha",
                "authorized_next_head_sha",
                "actor_id",
                "action",
                "occurred_at",
            ),
        )
        node_id = reporter.expect_string(event["node_id"], f"{label}.node_id")
        held_round = reporter.expect_int(
            event["held_round"], f"{label}.held_round", 3
        )
        occurred_at, occurred = _expect_time(
            event["occurred_at"], f"{label}.occurred_at"
        )
        if previous is not None and occurred <= previous:
            raise reporter.PilotDataError(
                "architecture dispositions are not strictly chronological"
            )
        previous = occurred
        ids.append(node_id)
        held_rounds.append(held_round)
        result.append(
            {
                "node_id": node_id,
                "held_round": held_round,
                "held_head_sha": reporter.expect_sha(
                    event["held_head_sha"], f"{label}.held_head_sha"
                ),
                "authorized_next_head_sha": reporter.expect_sha(
                    event["authorized_next_head_sha"],
                    f"{label}.authorized_next_head_sha",
                ),
                "actor_id": reporter.expect_string(
                    event["actor_id"], f"{label}.actor_id"
                ),
                "action": reporter.expect_enum(
                    event["action"], ARCHITECTURE_ACTIONS, f"{label}.action"
                ),
                "occurred_at": occurred_at,
                "_occurred": occurred,
            }
        )
    reporter.expect_unique(ids, "architecture disposition node IDs")
    reporter.expect_unique(held_rounds, "architecture disposition held rounds")
    return result


def _validate_force_pushes(value: Any) -> list[dict[str, Any]]:
    result = []
    ids = []
    for index, raw in enumerate(
        reporter.expect_list(value, "evidence.force_push_events")
    ):
        label = f"evidence.force_push_events[{index}]"
        event = reporter.expect_object(raw, label)
        reporter.expect_keys(event, label, ("node_id", "candidate_sha", "occurred_at"))
        node_id = reporter.expect_string(event["node_id"], f"{label}.node_id")
        ids.append(node_id)
        occurred_at, occurred = _expect_time(
            event["occurred_at"], f"{label}.occurred_at"
        )
        result.append(
            {
                "node_id": node_id,
                "candidate_sha": reporter.expect_sha(
                    event["candidate_sha"], f"{label}.candidate_sha"
                ),
                "occurred_at": occurred_at,
                "_occurred": occurred,
            }
        )
    reporter.expect_unique(ids, "force-push event node IDs")
    return result


def _validate_result_binding(value: Any, label: str) -> dict[str, Any]:
    binding = reporter.expect_object(value, label)
    reporter.expect_keys(
        binding,
        label,
        (
            "finding_id",
            "finding_family",
            "finding_member",
            "finding_review_id",
            "finding_review_round",
            "finding_head_sha",
            "finding_head_tree",
            "finding_origin_sha",
            "finding_origin_tree",
            "head_sha",
            "head_tree",
        ),
    )
    result = {
        "head_sha": reporter.expect_sha(binding["head_sha"], f"{label}.head_sha"),
        "head_tree": reporter.expect_sha(
            binding["head_tree"], f"{label}.head_tree"
        ),
    }
    if binding["finding_id"] is None:
        for field in (
            "finding_family",
            "finding_member",
            "finding_review_id",
            "finding_review_round",
            "finding_head_sha",
            "finding_head_tree",
            "finding_origin_sha",
            "finding_origin_tree",
        ):
            if binding[field] is not None:
                raise reporter.PilotDataError(
                    f"{label}.{field} must be null for behavior assertions"
                )
        return {
            "finding_id": None,
            "finding_family": None,
            "finding_member": None,
            "finding_review_id": None,
            "finding_review_round": None,
            "finding_head_sha": None,
            "finding_head_tree": None,
            "finding_origin_sha": None,
            "finding_origin_tree": None,
            **result,
        }
    family = reporter.expect_enum(
        binding["finding_family"], set(FAMILY_MEMBERS), f"{label}.finding_family"
    )
    return {
        "finding_id": reporter.expect_string(
            binding["finding_id"], f"{label}.finding_id"
        ),
        "finding_family": family,
        "finding_member": reporter.expect_enum(
            binding["finding_member"],
            set(FAMILY_MEMBERS[family]),
            f"{label}.finding_member",
        ),
        "finding_review_id": reporter.expect_string(
            binding["finding_review_id"], f"{label}.finding_review_id"
        ),
        "finding_review_round": reporter.expect_int(
            binding["finding_review_round"], f"{label}.finding_review_round", 0
        ),
        "finding_head_sha": reporter.expect_sha(
            binding["finding_head_sha"], f"{label}.finding_head_sha"
        ),
        "finding_head_tree": reporter.expect_sha(
            binding["finding_head_tree"], f"{label}.finding_head_tree"
        ),
        "finding_origin_sha": reporter.expect_sha(
            binding["finding_origin_sha"], f"{label}.finding_origin_sha"
        ),
        "finding_origin_tree": reporter.expect_sha(
            binding["finding_origin_tree"], f"{label}.finding_origin_tree"
        ),
        **result,
    }


def _validate_authority_hold_output(
    output: dict[str, Any], label: str
) -> dict[str, Any]:
    hold_reason = reporter.expect_string(
        output.get("hold_reason"), f"{label}.hold_reason"
    )
    if hold_reason != "authority-dependency-changed":
        raise reporter.PilotDataError(
            f"{label}.hold_reason is not supported"
        )
    if not reporter.expect_bool(
        output.get("external_review_required"),
        f"{label}.external_review_required",
    ):
        raise reporter.PilotDataError(
            f"{label}.external_review_required must be true"
        )
    if not reporter.expect_bool(
        output.get("fresh_base_required"),
        f"{label}.fresh_base_required",
    ):
        raise reporter.PilotDataError(
            f"{label}.fresh_base_required must be true"
        )
    dependencies = reporter.expect_list(
        output.get("authority_dependencies"),
        f"{label}.authority_dependencies",
    )
    if not dependencies:
        raise reporter.PilotDataError(
            f"{label}.authority_dependencies must not be empty"
        )
    validated = []
    for index, raw in enumerate(dependencies):
        dependency = reporter.expect_object(
            raw, f"{label}.authority_dependencies[{index}]"
        )
        reporter.expect_keys(
            dependency,
            f"{label}.authority_dependencies[{index}]",
            (
                "path",
                "base_mode",
                "base_blob_oid",
                "origin_mode",
                "origin_blob_oid",
                "head_mode",
                "head_blob_oid",
                "origin_changed",
                "head_changed",
            ),
        )
        origin_changed = reporter.expect_bool(
            dependency["origin_changed"],
            f"{label}.authority_dependencies[{index}].origin_changed",
        )
        head_changed = reporter.expect_bool(
            dependency["head_changed"],
            f"{label}.authority_dependencies[{index}].head_changed",
        )
        if not origin_changed and not head_changed:
            raise reporter.PilotDataError(
                f"{label}.authority_dependencies[{index}] does not record a changed authority state"
            )
        base_mode = _optional_mode(
            dependency["base_mode"],
            f"{label}.authority_dependencies[{index}].base_mode",
        )
        base_blob_oid = _optional_blob_oid(
            dependency["base_blob_oid"],
            f"{label}.authority_dependencies[{index}].base_blob_oid",
        )
        origin_mode = _optional_mode(
            dependency["origin_mode"],
            f"{label}.authority_dependencies[{index}].origin_mode",
        )
        origin_blob_oid = _optional_blob_oid(
            dependency["origin_blob_oid"],
            f"{label}.authority_dependencies[{index}].origin_blob_oid",
        )
        head_mode = _optional_mode(
            dependency["head_mode"],
            f"{label}.authority_dependencies[{index}].head_mode",
        )
        head_blob_oid = _optional_blob_oid(
            dependency["head_blob_oid"],
            f"{label}.authority_dependencies[{index}].head_blob_oid",
        )
        for prefix, mode, blob_oid in (
            ("base", base_mode, base_blob_oid),
            ("origin", origin_mode, origin_blob_oid),
            ("head", head_mode, head_blob_oid),
        ):
            if (mode is None) != (blob_oid is None):
                raise reporter.PilotDataError(
                    f"{label}.authority_dependencies[{index}].{prefix}_mode and {prefix}_blob_oid must both be null or both be present"
                )
        if origin_changed != (
            origin_mode != base_mode or origin_blob_oid != base_blob_oid
        ):
            raise reporter.PilotDataError(
                f"{label}.authority_dependencies[{index}].origin_changed contradicts the authority state"
            )
        if head_changed != (
            head_mode != base_mode or head_blob_oid != base_blob_oid
        ):
            raise reporter.PilotDataError(
                f"{label}.authority_dependencies[{index}].head_changed contradicts the authority state"
            )
        validated.append(
            {
                "path": _validate_path(
                    dependency["path"],
                    f"{label}.authority_dependencies[{index}].path",
                ),
                "base_mode": base_mode,
                "base_blob_oid": base_blob_oid,
                "origin_mode": origin_mode,
                "origin_blob_oid": origin_blob_oid,
                "head_mode": head_mode,
                "head_blob_oid": head_blob_oid,
                "origin_changed": origin_changed,
                "head_changed": head_changed,
            }
        )
    return {
        "hold_reason": hold_reason,
        "external_review_required": True,
        "fresh_base_required": True,
        "authority_dependencies": validated,
    }


def _validate_result_manifest(value: Any) -> dict[str, dict[str, Any]]:
    results = {}
    for index, raw in enumerate(
        reporter.expect_list(value, "evidence.result_manifest")
    ):
        label = f"evidence.result_manifest[{index}]"
        result = reporter.expect_object(raw, label)
        reporter.expect_keys(
            result,
            label,
            (
                "id",
                "assertion_id",
                "check_id",
                "claimed_disposition",
                "authority_binding",
                "program_path",
                "program_blob_oid",
                "program_argv",
                "program_case",
                "program_exit_code",
                "program_stdout_sha256",
                "command_id",
                "input_sha256",
                "inputs_sha256",
                "output",
                "output_sha256",
                "base_sha",
                "candidate_sha",
                "review_round",
                "status",
            ),
        )
        result_id = reporter.expect_string(result["id"], f"{label}.id")
        if result_id in results:
            raise reporter.PilotDataError(f"duplicate result ID {result_id!r}")
        command_id = reporter.expect_string(result["command_id"], f"{label}.command_id")
        input_sha = reporter.expect_string(
            result["input_sha256"], f"{label}.input_sha256"
        )
        inputs_sha = reporter.expect_string(
            result["inputs_sha256"], f"{label}.inputs_sha256"
        )
        output_sha = reporter.expect_string(
            result["output_sha256"], f"{label}.output_sha256"
        )
        output = reporter.expect_object(result["output"], f"{label}.output")
        if (
            reporter.SHA256_RE.fullmatch(command_id) is None
            or reporter.SHA256_RE.fullmatch(input_sha) is None
            or reporter.SHA256_RE.fullmatch(inputs_sha) is None
            or reporter.SHA256_RE.fullmatch(output_sha) is None
        ):
            raise reporter.PilotDataError(
                f"{label} command/input identity must be SHA-256"
            )
        results[result_id] = {
            "id": result_id,
            "assertion_id": reporter.expect_string(
                result["assertion_id"], f"{label}.assertion_id"
            ),
            "check_id": reporter.expect_string(
                result["check_id"], f"{label}.check_id"
            ),
            "claimed_disposition": result["claimed_disposition"],
            "authority_binding": _validate_result_binding(
                result["authority_binding"], f"{label}.authority_binding"
            ),
            "program_path": _validate_path(
                result["program_path"], f"{label}.program_path"
            ),
            "program_blob_oid": reporter.expect_sha(
                result["program_blob_oid"], f"{label}.program_blob_oid"
            ),
            "program_argv": _expect_string_list(
                result["program_argv"], f"{label}.program_argv"
            ),
            "program_case": reporter.expect_string(
                result["program_case"], f"{label}.program_case"
            ),
            "program_exit_code": reporter.expect_int(
                result["program_exit_code"], f"{label}.program_exit_code", 0
            ),
            "program_stdout_sha256": reporter.expect_string(
                result["program_stdout_sha256"],
                f"{label}.program_stdout_sha256",
            ),
            "command_id": command_id,
            "input_sha256": input_sha,
            "inputs_sha256": inputs_sha,
            "output": output,
            "output_sha256": output_sha,
            "base_sha": reporter.expect_sha(
                result["base_sha"], f"{label}.base_sha"
            ),
            "candidate_sha": reporter.expect_sha(
                result["candidate_sha"], f"{label}.candidate_sha"
            ),
            "review_round": reporter.expect_int(
                result["review_round"], f"{label}.review_round", 1
            ),
            "status": reporter.expect_enum(
                result["status"], {"pass", "hold"}, f"{label}.status"
            ),
        }
        if (
            result["program_path"]
            != "scripts/workflow_pilot/review_assertions.py"
            or result["program_argv"]
            != [
                "/usr/bin/python3",
                "-I",
                "review_assertions.py",
                "--stdin",
            ]
            or result["program_exit_code"] != 0
            or reporter.SHA256_RE.fullmatch(
                result["program_stdout_sha256"]
            )
            is None
        ):
            raise reporter.PilotDataError(
                f"{label} assertion program identity is invalid"
            )
        if hashlib.sha256(reporter.normalized_json(output)).hexdigest() != output_sha:
            raise reporter.PilotDataError(
                f"{label}.output does not match output_sha256"
            )
    return results


def _validate_execution_receipts(value: Any) -> list[dict[str, Any]]:
    receipts = []
    ids = []
    seals = []
    for index, raw in enumerate(
        reporter.expect_list(value, "evidence.execution_receipts")
    ):
        label = f"evidence.execution_receipts[{index}]"
        receipt = reporter.expect_object(raw, label)
        required = (
            "id",
            "check_id",
            "base_sha",
            "base_tree",
            "original_pre_review_head",
            "original_receipt_sha256",
            "review_round",
            "candidate_sha",
            "candidate_tree",
            "checker_path",
            "checker_blob_oid",
            "argv",
            "assertion_program_path",
            "assertion_program_blob_oid",
            "assertion_program_argv",
            "finding_origin_sha",
            "finding_origin_tree",
            "assertion_input_artifacts",
            "changed_files",
            "changes",
            "remote_finding_ids",
            "review_report_sha256",
            "checker_input_sha256",
            "assertion_results",
            "read_only",
            "pre_clean",
            "post_clean",
            "started_at",
            "completed_at",
            "exit_code",
            "result",
            "output_sha256",
            "seal",
        )
        reporter.expect_keys(receipt, label, required)
        receipt_id = reporter.expect_string(receipt["id"], f"{label}.id")
        seal = reporter.expect_string(receipt["seal"], f"{label}.seal")
        hash_fields = {
            "seal": seal,
            "review_report_sha256": reporter.expect_string(
                receipt["review_report_sha256"],
                f"{label}.review_report_sha256",
            ),
            "checker_input_sha256": reporter.expect_string(
                receipt["checker_input_sha256"],
                f"{label}.checker_input_sha256",
            ),
            "output_sha256": reporter.expect_string(
                receipt["output_sha256"], f"{label}.output_sha256"
            ),
            "original_receipt_sha256": reporter.expect_string(
                receipt["original_receipt_sha256"],
                f"{label}.original_receipt_sha256",
            ),
        }
        if any(
            reporter.SHA256_RE.fullmatch(digest) is None
            for digest in hash_fields.values()
        ):
            raise reporter.PilotDataError(
                f"{label} receipt digests must be lowercase SHA-256"
            )
        ids.append(receipt_id)
        seals.append(seal)
        started_at, started = _expect_time(
            receipt["started_at"], f"{label}.started_at"
        )
        completed_at, completed = _expect_time(
            receipt["completed_at"], f"{label}.completed_at"
        )
        if completed < started:
            raise reporter.PilotDataError(
                f"{label} completed before it started"
            )
        exit_code = reporter.expect_int(
            receipt["exit_code"], f"{label}.exit_code", 0
        )
        result_value = reporter.expect_enum(
            receipt["result"], {"fail", "pass", "hold"}, f"{label}.result"
        )
        if (exit_code == 0) != (result_value in {"pass", "hold"}):
            raise reporter.PilotDataError(
                f"{label} exit code contradicts result"
            )
        assertion_results = reporter.expect_list(
            receipt["assertion_results"], f"{label}.assertion_results"
        )
        for position, assertion_result in enumerate(assertion_results):
            reporter.expect_object(
                assertion_result, f"{label}.assertion_results[{position}]"
            )
        receipts.append(
            {
                **receipt,
                "id": receipt_id,
                "check_id": reporter.expect_enum(
                    receipt["check_id"],
                    {"base-pinned-independent-review"},
                    f"{label}.check_id",
                ),
                "base_sha": reporter.expect_sha(
                    receipt["base_sha"], f"{label}.base_sha"
                ),
                "base_tree": reporter.expect_sha(
                    receipt["base_tree"], f"{label}.base_tree"
                ),
                "original_pre_review_head": reporter.expect_sha(
                    receipt["original_pre_review_head"],
                    f"{label}.original_pre_review_head",
                ),
                "original_receipt_sha256": reporter.expect_string(
                    receipt["original_receipt_sha256"],
                    f"{label}.original_receipt_sha256",
                ),
                "review_round": reporter.expect_int(
                    receipt["review_round"], f"{label}.review_round", 1
                ),
                "candidate_sha": reporter.expect_sha(
                    receipt["candidate_sha"], f"{label}.candidate_sha"
                ),
                "candidate_tree": reporter.expect_sha(
                    receipt["candidate_tree"], f"{label}.candidate_tree"
                ),
                "checker_path": _validate_path(
                    receipt["checker_path"], f"{label}.checker_path"
                ),
                "checker_blob_oid": reporter.expect_sha(
                    receipt["checker_blob_oid"], f"{label}.checker_blob_oid"
                ),
                "argv": _expect_string_list(receipt["argv"], f"{label}.argv"),
                "assertion_program_path": _validate_path(
                    receipt["assertion_program_path"],
                    f"{label}.assertion_program_path",
                ),
                "assertion_program_blob_oid": reporter.expect_sha(
                    receipt["assertion_program_blob_oid"],
                    f"{label}.assertion_program_blob_oid",
                ),
                "assertion_program_argv": _expect_string_list(
                    receipt["assertion_program_argv"],
                    f"{label}.assertion_program_argv",
                ),
                "finding_origin_sha": reporter.expect_sha(
                    receipt["finding_origin_sha"],
                    f"{label}.finding_origin_sha",
                ),
                "finding_origin_tree": reporter.expect_sha(
                    receipt["finding_origin_tree"],
                    f"{label}.finding_origin_tree",
                ),
                "assertion_input_artifacts": _validate_assertion_input_artifacts(
                    receipt["assertion_input_artifacts"],
                    f"{label}.assertion_input_artifacts",
                ),
                "changed_files": _expect_string_list(
                    receipt["changed_files"], f"{label}.changed_files"
                ),
                "changes": _validate_change_records(
                    receipt["changes"], f"{label}.changes"
                ),
                "remote_finding_ids": _expect_string_list(
                    receipt["remote_finding_ids"],
                    f"{label}.remote_finding_ids",
                    nonempty=False,
                ),
                "assertion_results": assertion_results,
                "started_at": started_at,
                "completed_at": completed_at,
                "_started": started,
                "_completed": completed,
                "exit_code": exit_code,
                "result": result_value,
                "read_only": reporter.expect_bool(
                    receipt["read_only"], f"{label}.read_only"
                ),
                "pre_clean": reporter.expect_bool(
                    receipt["pre_clean"], f"{label}.pre_clean"
                ),
                "post_clean": reporter.expect_bool(
                    receipt["post_clean"], f"{label}.post_clean"
                ),
            }
        )
    reporter.expect_unique(ids, "execution receipt IDs")
    reporter.expect_unique(seals, "execution receipt seals")
    return receipts


def validate_evidence(raw_evidence: Any) -> dict[str, Any]:
    evidence = reporter.expect_object(raw_evidence, "evidence")
    reporter.expect_keys(
        evidence,
        "evidence",
        (
            "schema_version",
            "repository",
            "source",
            "captured_at",
            "candidate",
            "original_pre_review_head",
            "original_receipt_sha256",
            "pull_request",
            "authoritative_trigger",
            "result_source_path",
            "actors",
            "pre_reviews",
            "pre_review_findings",
            "remote_reviews",
            "findings",
            "threads",
            "candidate_advances",
            "force_push_events",
            "architecture_dispositions",
            "execution_receipts",
            "result_manifest",
        ),
    )
    if reporter.expect_int(
        evidence["schema_version"], "evidence.schema_version", 1
    ) != SCHEMA_VERSION:
        raise reporter.PilotDataError(
            f"evidence.schema_version must be {SCHEMA_VERSION}"
        )
    if evidence["candidate_advances"] != []:
        raise reporter.PilotDataError(
            "candidate_advances is retired; pushedDate cannot attest head history"
        )
    captured_at, captured = _expect_time(
        evidence["captured_at"], "evidence.captured_at"
    )
    source = reporter.expect_object(evidence["source"], "evidence.source")
    reporter.expect_keys(source, "evidence.source", ("kind", "complete"))
    reporter.expect_enum(
        source["kind"],
        {"live-gh-api", "offline-transform-fixture"},
        "evidence.source.kind",
    )
    if not reporter.expect_bool(source["complete"], "evidence.source.complete"):
        raise reporter.PilotDataError("canonical review source must be complete")
    candidate = reporter.expect_object(evidence["candidate"], "evidence.candidate")
    reporter.expect_keys(candidate, "evidence.candidate", ("sha",))
    pull_request = reporter.expect_object(
        evidence["pull_request"], "evidence.pull_request"
    )
    reporter.expect_keys(
        pull_request,
        "evidence.pull_request",
        (
            "number",
            "node_id",
            "created_at",
            "base_sha",
            "head_sha",
            "author_actor_id",
        ),
        optional=("commit_shas",),
    )
    created_at, created = _expect_time(
        pull_request["created_at"], "evidence.pull_request.created_at"
    )
    commit_shas = pull_request.get("commit_shas")
    if commit_shas is not None:
        commit_shas = reporter.expect_list(
            commit_shas, "evidence.pull_request.commit_shas"
        )
        if not commit_shas:
            raise reporter.PilotDataError(
                "evidence.pull_request.commit_shas must not be empty"
            )
        for index, sha in enumerate(commit_shas):
            reporter.expect_sha(sha, f"evidence.pull_request.commit_shas[{index}]")
        reporter.expect_unique(commit_shas, "evidence.pull_request.commit_shas")
    result_source_path = _validate_path(
        evidence["result_source_path"], "evidence.result_source_path"
    )
    if result_source_path != RESULT_SOURCE_PATH:
        raise reporter.PilotDataError(
            "evidence result source is unrelated to the frozen inventory"
        )
    original_receipt_sha256 = reporter.expect_string(
        evidence["original_receipt_sha256"],
        "evidence.original_receipt_sha256",
    )
    if reporter.SHA256_RE.fullmatch(original_receipt_sha256) is None:
        raise reporter.PilotDataError(
            "evidence.original_receipt_sha256 must be SHA-256"
        )
    return {
        "raw": evidence,
        "repository": reporter.expect_string(
            evidence["repository"], "evidence.repository"
        ),
        "source": source,
        "captured_at": captured_at,
        "captured": captured,
        "candidate": {
            "sha": reporter.expect_sha(
                candidate["sha"], "evidence.candidate.sha"
            )
        },
        "original_pre_review_head": reporter.expect_sha(
            evidence["original_pre_review_head"],
            "evidence.original_pre_review_head",
        ),
        "original_receipt_sha256": original_receipt_sha256,
        "pull_request": {
            "number": reporter.expect_int(
                pull_request["number"], "evidence.pull_request.number", 1
            ),
            "node_id": reporter.expect_string(
                pull_request["node_id"], "evidence.pull_request.node_id"
            ),
            "created_at": created_at,
            "_created": created,
            "base_sha": reporter.expect_sha(
                pull_request["base_sha"], "evidence.pull_request.base_sha"
            ),
            "head_sha": reporter.expect_sha(
                pull_request["head_sha"], "evidence.pull_request.head_sha"
            ),
            "author_actor_id": reporter.expect_string(
                pull_request["author_actor_id"],
                "evidence.pull_request.author_actor_id",
            ),
            "commit_shas": None if commit_shas is None else list(commit_shas),
        },
        "authoritative_trigger": _validate_authoritative_trigger(
            evidence["authoritative_trigger"]
        ),
        "result_source_path": result_source_path,
        "actors": _validate_actors(evidence["actors"]),
        "pre_reviews": _validate_pre_reviews(evidence["pre_reviews"]),
        "pre_review_findings": _validate_findings(
            evidence["pre_review_findings"],
            "evidence.pre_review_findings",
            local=True,
        ),
        "remote_reviews": _validate_remote_reviews(evidence["remote_reviews"]),
        "findings": _validate_findings(
            evidence["findings"], "evidence.findings", local=False
        ),
        "threads": _validate_threads(evidence["threads"]),
        "force_push_events": _validate_force_pushes(
            evidence["force_push_events"]
        ),
        "architecture_dispositions": _validate_dispositions(
            evidence["architecture_dispositions"]
        ),
        "execution_receipts": _validate_execution_receipts(
            evidence["execution_receipts"]
        ),
        "result_manifest": _validate_result_manifest(
            evidence["result_manifest"]
        ),
    }


def _repository_authority(
    repository_root: Path,
    expected_candidate: str,
    contract: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    root = reporter.validate_repository_root(repository_root)
    head = reporter.run_git(
        root, "rev-parse", "--verify", "HEAD^{commit}"
    ).decode("ascii").strip()
    if head != expected_candidate:
        raise reporter.PilotDataError(
            f"actual Git HEAD {head} does not match expected candidate"
        )
    if contract["candidate_sha"] != head or evidence["candidate"]["sha"] != head:
        raise reporter.PilotDataError(
            "contract/evidence candidate does not match actual Git HEAD"
        )
    if (
        evidence["original_pre_review_head"]
        != contract["original_pre_review_head"]
    ):
        raise reporter.PilotDataError(
            "evidence original pre-review head does not match contract"
        )
    if (
        evidence["pull_request"]["base_sha"] != contract["base_sha"]
    ):
        raise reporter.PilotDataError(
            "evidence base does not equal exact contract/authoritative PR base"
        )
    commit_shas = evidence["pull_request"]["commit_shas"]
    if commit_shas is not None and (
        evidence["pull_request"]["head_sha"] not in commit_shas
        or commit_shas[-1] != evidence["pull_request"]["head_sha"]
    ):
        raise reporter.PilotDataError(
            "authoritative PR commit history does not end at the exact remote head"
        )
    reporter.run_git(
        root, "merge-base", "--is-ancestor", contract["base_sha"], head
    )
    reporter.run_git(
        root,
        "merge-base",
        "--is-ancestor",
        contract["original_pre_review_head"],
        head,
    )
    for review in evidence["remote_reviews"]:
        reporter.run_git(
            root,
            "merge-base",
            "--is-ancestor",
            review["candidate_sha"],
            head,
        )
    remote = reporter.run_git(
        root, "config", "--get", "remote.origin.url"
    ).decode("utf-8").strip()
    if reporter._github_repository_from_remote(remote) != contract["repository"]:
        raise reporter.PilotDataError(
            "contract repository does not match actual Git origin"
        )
    shas = {
        contract["base_sha"],
        contract["original_pre_review_head"],
        head,
        *((commit_shas or ())),
        *(review["candidate_sha"] for review in evidence["pre_reviews"]),
        *(review["candidate_sha"] for review in evidence["remote_reviews"]),
        *(
            finding["candidate_sha"]
            for finding in evidence["pre_review_findings"].values()
        ),
        *(finding["candidate_sha"] for finding in evidence["findings"].values()),
        *(
            event["held_head_sha"]
            for event in evidence["architecture_dispositions"]
        ),
        *(
            event["authorized_next_head_sha"]
            for event in evidence["architecture_dispositions"]
        ),
        *(
            event["candidate_sha"] for event in evidence["force_push_events"]
        ),
    }
    commits = reporter._load_git_commit_objects(root, shas)
    changes = derive_change_records(root, contract["base_sha"], head)
    original_changes = derive_change_records(
        root, contract["base_sha"], contract["original_pre_review_head"]
    )
    changed_files = sorted(
        {
            path
            for change in changes
            for path in (change["old_path"], change["new_path"])
            if path is not None
        }
    )
    for review in evidence["pre_reviews"]:
        original_files = sorted(
            {
                path
                for change in original_changes
                for path in (change["old_path"], change["new_path"])
                if path is not None
            }
        )
        if sorted(review["reviewed_files"]) != original_files:
            raise reporter.PilotDataError(
                "pre-review does not cover exact original first-reviewed head"
            )
        if review["reviewed_changes"] != original_changes:
            raise reporter.PilotDataError(
                "pre-review status/blob evidence does not match original head"
            )
    tree = reporter.run_git(
        root, "rev-parse", f"{head}^{{tree}}"
    ).decode("ascii").strip()
    return {
        "root": root,
        "head": head,
        "tree": tree,
        "commits": commits,
        "changed_files": changed_files,
        "changes": changes,
        "original_changes": original_changes,
    }


def _validate_initial_remote_head_binding(
    evidence: dict[str, Any], authority: dict[str, Any]
) -> None:
    if not evidence["pre_reviews"] or not evidence["remote_reviews"]:
        return
    original_head = evidence["pre_reviews"][0]["candidate_sha"]
    first_remote_head = evidence["remote_reviews"][0]["candidate_sha"]
    if original_head == first_remote_head:
        return
    commit_shas = evidence["pull_request"]["commit_shas"]
    if commit_shas is None:
        raise reporter.PilotDataError(
            "authoritative PR commit history is required when the original pre-review head differs from the first remote review head"
        )
    if original_head not in commit_shas or first_remote_head not in commit_shas:
        raise reporter.PilotDataError(
            "authoritative PR commit history does not preserve the original pre-review head and first remote review head"
        )
    if commit_shas.index(original_head) >= commit_shas.index(first_remote_head):
        raise reporter.PilotDataError(
            "original pre-review head does not precede the first remote review head in authoritative PR history"
        )
    if not reporter.is_ancestor(original_head, first_remote_head, authority["commits"]):
        raise reporter.PilotDataError(
            "original pre-review head is not the non-rewritten ancestor of the first remote review head"
        )


def _global_identity_check(evidence: dict[str, Any]) -> None:
    identities = []

    def add(domain: str, value: str):
        identities.append((value.strip().casefold(), domain))

    for actor_id in evidence["actors"]:
        add("actor", actor_id)
    add("pull-request", evidence["pull_request"]["node_id"])
    if (
        evidence["authoritative_trigger"] is not None
        and evidence["authoritative_trigger"]["comment_id"] is not None
    ):
        add("authoritative-trigger-comment", evidence["authoritative_trigger"]["comment_id"])
    for review in evidence["pre_reviews"]:
        add("pre-review", review["id"])
        for action in review["actions"]:
            add("pre-review-action", action["id"])
    for finding_id in evidence["pre_review_findings"]:
        add("pre-review-finding", finding_id)
    for review in evidence["remote_reviews"]:
        add("remote-review", review["node_id"])
    for finding_id in evidence["findings"]:
        add("remote-finding", finding_id)
        comment_id = evidence["findings"][finding_id].get("authority_comment_id")
        if comment_id is not None:
            add("authoritative-family-comment", comment_id)
    for thread in evidence["threads"].values():
        add("review-thread", thread["node_id"])
    for event in evidence["force_push_events"]:
        add("force-push", event["node_id"])
    for event in evidence["architecture_dispositions"]:
        add("disposition", event["node_id"])
    grouped = defaultdict(list)
    for identity, domain in identities:
        grouped[identity].append(domain)
    collisions = {
        identity: domains
        for identity, domains in grouped.items()
        if len(domains) > 1
    }
    if collisions:
        raise reporter.PilotDataError(
            f"global node identity collision: {sorted(collisions.items())}"
        )


def _resolve_authoritative_trigger(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    authority: dict[str, Any],
) -> dict[str, Any]:
    trigger = evidence["authoritative_trigger"]
    if trigger is None:
        if contract["trust_mode"] == "introduction":
            return {
                "authoritative": False,
                "path": None,
                "blob_oid": None,
                "trigger": contract["trigger"],
                "pre_review_required": False,
            }
        raise reporter.PilotDataError(
            "base-pinned mode requires an authoritative trigger decision"
        )
    if trigger["pull_request"] != contract["pull_request"]:
        raise reporter.PilotDataError(
            "authoritative trigger decision does not match the contract PR"
        )
    if trigger["base_sha"] != contract["base_sha"]:
        raise reporter.PilotDataError(
            "authoritative trigger decision does not match the exact contract base"
        )
    if trigger["candidate_sha"] != authority["head"]:
        raise reporter.PilotDataError(
            "authoritative trigger decision does not match the exact candidate head"
        )
    if trigger["original_pre_review_head"] != contract["original_pre_review_head"]:
        raise reporter.PilotDataError(
            "authoritative trigger decision does not bind the exact initial reviewed head"
        )
    if trigger["authority_kind"] == "base-record":
        actual_blob_oid = reporter.run_git(
            authority["root"],
            "rev-parse",
            f"{contract['base_sha']}:{TRIGGER_DECISION_PATH}",
        ).decode("ascii").strip()
        if trigger["path"] != TRIGGER_DECISION_PATH or trigger["blob_oid"] != actual_blob_oid:
            raise reporter.PilotDataError(
                "authoritative trigger decision does not bind the exact base blob"
            )
    elif trigger["comment_id"] is None or trigger["comment_created_at"] is None:
        raise reporter.PilotDataError(
            "external authoritative trigger does not bind one exact trusted comment"
        )
    if trigger["trigger"] != contract["trigger"]:
        raise reporter.PilotDataError(
            "candidate trigger does not match the authoritative decision record"
        )
    return {
        "authoritative": True,
        "path": trigger["path"],
        "blob_oid": trigger["blob_oid"],
        "trigger": trigger["trigger"],
        "pre_review_required": trigger["pre_review_required"],
    }


def _validate_roles_and_causality(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    authority: dict[str, Any],
    pre_review_required: bool,
) -> dict[str, Any]:
    if contract["repository"] != evidence["repository"]:
        raise reporter.PilotDataError("contract/evidence repository mismatch")
    if contract["pull_request"] != evidence["pull_request"]["number"]:
        raise reporter.PilotDataError("contract/evidence pull-request mismatch")
    actors = evidence["actors"]
    implementer_id = contract["implementer_actor_id"]
    if implementer_id != evidence["pull_request"]["author_actor_id"]:
        raise reporter.PilotDataError(
            "contract implementer is not the authoritative PR author"
        )
    if implementer_id not in actors:
        raise reporter.PilotDataError("implementer references unknown actor")
    implementer = actors[implementer_id]
    expected_pre_reviews = 1 if pre_review_required else 0
    if len(evidence["pre_reviews"]) != expected_pre_reviews:
        raise reporter.PilotDataError(
            "contract has the wrong number of independent pre-reviews"
        )
    pre_owner = None
    if evidence["pre_reviews"]:
        pre = evidence["pre_reviews"][0]
        if pre["owner_actor_id"] not in actors:
            raise reporter.PilotDataError("pre-review owner is unknown")
        pre_owner = actors[pre["owner_actor_id"]]
        if pre_owner["normalized_login"] == implementer["normalized_login"]:
            raise reporter.PilotDataError(
                "pre-review owner overlaps implementer"
            )
        if pre["permissions"] != list(READ_ONLY_PERMISSIONS):
            raise reporter.PilotDataError(
                "pre-review permissions are not exactly read-only"
            )
        if [action["kind"] for action in pre["actions"]] != list(
            READ_ONLY_ACTIONS
        ):
            raise reporter.PilotDataError(
                "pre-review actions are not exact read then report"
            )
        limits = contract["limits"]
        if len(pre["reviewed_files"]) > limits["max_reviewed_files"]:
            raise reporter.PilotDataError("pre-review exceeds max_reviewed_files")
        if len(pre["finding_ids"]) > limits["max_findings_per_review"]:
            raise reporter.PilotDataError("pre-review exceeds max_findings_per_review")
        if (
            pre["_completed"] - pre["_started"]
        ).total_seconds() > limits["max_duration_minutes"] * 60:
            raise reporter.PilotDataError("pre-review exceeds max_duration_minutes")
    remote_actors = []
    for review in evidence["remote_reviews"]:
        actor = actors.get(review["reviewer_actor_id"])
        if actor is None or not is_authoritative_copilot_actor(actor):
            raise reporter.PilotDataError(
                "remote review actor is not the exact authoritative GitHub Copilot Bot"
            )
        if actor["normalized_login"] == implementer["normalized_login"]:
            raise reporter.PilotDataError(
                "remote reviewer overlaps implementer"
            )
        if pre_owner and actor["normalized_login"] == pre_owner["normalized_login"]:
            raise reporter.PilotDataError(
                "remote reviewer overlaps pre-review owner"
            )
        remote_actors.append(actor)
        semantic_change = (
            review["state"] == "CHANGES_REQUESTED"
            or review["body_has_findings"]
            or bool(review["finding_ids"])
        )
        if (review["outcome"] == "changes-requested") != semantic_change:
            raise reporter.PilotDataError(
                f"remote review round {review['round']} outcome contradicts "
                "state/body/inline findings"
            )
        if len(review["finding_ids"]) > contract["limits"]["max_findings_per_review"]:
            raise reporter.PilotDataError(
                f"remote review round {review['round']} exceeds finding bound"
            )
    if evidence["pre_reviews"] and evidence["remote_reviews"]:
        pre = evidence["pre_reviews"][0]
        first_remote = evidence["remote_reviews"][0]
        if pre["_issued"] >= first_remote["_submitted"]:
            raise reporter.PilotDataError(
                "pre-review receipt was backdated or re-signed after remote review"
            )
        _validate_initial_remote_head_binding(evidence, authority)
    for review in [*evidence["pre_reviews"], *evidence["remote_reviews"]]:
        observed = review.get("_started", review.get("_submitted"))
        commit_time = authority["commits"][review["candidate_sha"]]["committed_at"]
        if (
            observed < commit_time
            or observed < evidence["pull_request"]["_created"]
            or observed > evidence["captured"]
        ):
            raise reporter.PilotDataError(
                f"review {review['id']} violates commit/PR/capture causality"
            )
    for finding in [
        *evidence["pre_review_findings"].values(),
        *evidence["findings"].values(),
    ]:
        if finding["author_actor_id"] not in actors:
            raise reporter.PilotDataError(
                f"finding {finding['id']!r} has an unauthenticated author"
            )
        if finding["_created"] > evidence["captured"]:
            raise reporter.PilotDataError(
                f"finding {finding['id']!r} follows evidence capture"
            )
    for finding in evidence["findings"].values():
        if not is_authoritative_copilot_actor(actors[finding["author_actor_id"]]):
            raise reporter.PilotDataError(
                f"finding {finding['id']!r} author is not the exact authoritative GitHub Copilot Bot"
            )
    for event in evidence["force_push_events"]:
        committed = authority["commits"][event["candidate_sha"]][
            "committed_at"
        ]
        if event["_occurred"] < committed or event["_occurred"] > evidence["captured"]:
            raise reporter.PilotDataError(
                f"force-push event {event['node_id']!r} violates chronology"
            )
    for event in evidence["architecture_dispositions"]:
        if event["_occurred"] > evidence["captured"]:
            raise reporter.PilotDataError(
                f"disposition {event['node_id']!r} follows evidence capture"
            )
    return {
        "implementer": implementer,
        "pre_owner": pre_owner,
        "remote_actors": remote_actors,
    }


def _validate_findings_and_sweeps(
    contract: dict[str, Any], evidence: dict[str, Any]
):
    local = evidence["pre_review_findings"]
    remote = evidence["findings"]
    if set(evidence["threads"]) != set(remote):
        raise reporter.PilotDataError(
            "review threads do not exactly cover remote finding node IDs"
        )
    local_claims = {
        finding_id
        for review in evidence["pre_reviews"]
        for finding_id in review["finding_ids"]
    }
    if local_claims != set(local):
        raise reporter.PilotDataError(
            "pre-review finding claims do not exactly cover local findings"
        )
    remote_claims = {}
    reviews_by_node = {
        review["node_id"]: review for review in evidence["remote_reviews"]
    }
    for review in evidence["remote_reviews"]:
        for finding_id in review["finding_ids"]:
            if finding_id in remote_claims:
                raise reporter.PilotDataError(
                    f"remote finding {finding_id!r} has overlapping ownership"
                )
            remote_claims[finding_id] = review["node_id"]
    if set(remote_claims) != set(remote):
        raise reporter.PilotDataError(
            "remote review claims do not exactly cover remote findings"
        )
    pre_by_id = {review["id"]: review for review in evidence["pre_reviews"]}
    all_findings = {**local, **remote}
    if len(all_findings) != len(local) + len(remote):
        raise reporter.PilotDataError(
            "local and remote finding namespaces overlap"
        )
    for finding_id, finding in all_findings.items():
        review = (
            pre_by_id.get(finding["review_id"])
            if finding_id in local
            else reviews_by_node.get(finding["review_id"])
        )
        if review is None:
            raise reporter.PilotDataError(
                f"finding {finding_id!r} references unknown review"
            )
        if finding["candidate_sha"] != review["candidate_sha"]:
            raise reporter.PilotDataError(
                f"finding {finding_id!r} has stale candidate binding"
            )
        if finding_id in remote and finding["author_actor_id"] != review["reviewer_actor_id"]:
            raise reporter.PilotDataError(
                f"finding {finding_id!r} author does not match its exact remote review actor"
            )
        lower = review.get("_started", evidence["pull_request"]["_created"])
        upper = review.get("_completed", review.get("_submitted"))
        if finding["_created"] < lower or finding["_created"] > upper:
            raise reporter.PilotDataError(
                f"finding {finding_id!r} violates review chronology"
            )
    sweeps = {sweep["finding_id"]: sweep for sweep in contract["family_sweeps"]}
    if set(sweeps) != set(all_findings):
        raise reporter.PilotDataError(
            "family sweeps do not exactly cover local and remote findings"
        )
    family_counts = {family: 0 for family in FAMILY_MEMBERS}
    for finding_id, finding in all_findings.items():
        sweep = sweeps[finding_id]
        if sweep["family"] != finding["family"]:
            raise reporter.PilotDataError(
                f"finding {finding_id!r} family does not match its sweep"
            )
        if len(sweep["siblings"]) > contract["limits"]["max_siblings_per_finding"]:
            raise reporter.PilotDataError(
                f"finding {finding_id!r} exceeds max_siblings_per_finding"
            )
        family_counts[finding["family"]] += 1
    for review in evidence["remote_reviews"]:
        count = sum(
            len(sweeps[finding_id]["siblings"])
            for finding_id in review["finding_ids"]
        )
        if count > contract["limits"]["max_siblings_per_handoff"]:
            raise reporter.PilotDataError(
                f"remote review round {review['round']} exceeds sibling handoff bound"
            )
    return sweeps, family_counts


def _validate_execution(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    authority: dict[str, Any],
) -> list[str]:
    receipts = evidence["execution_receipts"]
    manifest = evidence["result_manifest"]
    if not receipts:
        if manifest:
            raise reporter.PilotDataError(
                "candidate result IDs have no trusted execution receipt"
            )
        return []
    reviews = evidence["remote_reviews"]
    if len(receipts) != len(reviews):
        raise reporter.PilotDataError(
            "every remote review round/head requires one execution receipt"
        )
    actual_base_tree = reporter.run_git(
        authority["root"], "rev-parse", f"{contract['base_sha']}^{{tree}}"
    ).decode("ascii").strip()
    actual_checker_blob = reporter.run_git(
        authority["root"],
        "rev-parse",
        f"{contract['base_sha']}:scripts/workflow_pilot/review_base_checker.py",
    ).decode("ascii").strip()
    actual_assertion_program_blob = reporter.run_git(
        authority["root"],
        "rev-parse",
        f"{contract['base_sha']}:{ASSERTION_PROGRAM_PATH}",
    ).decode("ascii").strip()
    expected_results = {}
    receipt_result_ids = set()
    seals = []
    previous_completed = None
    original_receipt_sha = None
    for review, receipt in zip(reviews, receipts):
        round_number = review["round"]
        target_head = review["candidate_sha"]
        target_changes = derive_change_records(
            authority["root"], contract["base_sha"], target_head
        )
        target_files = sorted(
            {
                path
                for change in target_changes
                for path in (change["old_path"], change["new_path"])
                if path is not None
            }
        )
        target_tree = reporter.run_git(
            authority["root"], "rev-parse", f"{target_head}^{{tree}}"
        ).decode("ascii").strip()
        finding_origin_sha = (
            contract["base_sha"]
            if round_number == 1
            else reviews[round_number - 2]["candidate_sha"]
        )
        finding_origin_tree = reporter.run_git(
            authority["root"],
            "rev-parse",
            f"{finding_origin_sha}^{{tree}}",
        ).decode("ascii").strip()
        assertion_input_artifacts = []
        for path in ASSERTION_INPUT_PATHS:
            base_state = _tree_state(authority["root"], contract["base_sha"], path)
            origin_state = _tree_state(authority["root"], finding_origin_sha, path)
            head_state = _tree_state(authority["root"], target_head, path)
            assertion_input_artifacts.append(
                {
                    "path": path,
                    "base_mode": base_state["mode"],
                    "base_blob_oid": base_state["blob_oid"],
                    "origin_mode": origin_state["mode"],
                    "origin_blob_oid": origin_state["blob_oid"],
                    "head_mode": head_state["mode"],
                    "head_blob_oid": head_state["blob_oid"],
                }
            )
        assertion_input_artifacts.sort(key=lambda item: item["path"])
        receipt_hold = any(
            result["status"] == "hold" for result in receipt["assertion_results"]
        )
        expected_receipt_result = "hold" if receipt_hold else "pass"
        if (
            receipt["base_sha"] != contract["base_sha"]
            or receipt["original_pre_review_head"]
            != contract["original_pre_review_head"]
            or receipt["review_round"] != round_number
            or receipt["candidate_sha"] != target_head
            or receipt["result"] != expected_receipt_result
            or receipt["exit_code"] != 0
            or not receipt["read_only"]
            or not receipt["pre_clean"]
            or not receipt["post_clean"]
        ):
            raise reporter.PilotDataError(
                f"execution receipt round {round_number} does not bind "
                "exact review round/head"
            )
        if original_receipt_sha is None:
            original_receipt_sha = receipt["original_receipt_sha256"]
        elif receipt["original_receipt_sha256"] != original_receipt_sha:
            raise reporter.PilotDataError(
                "execution receipts do not preserve one original pre-review"
            )
        if receipt["original_receipt_sha256"] != evidence[
            "original_receipt_sha256"
        ]:
            raise reporter.PilotDataError(
                "execution receipt does not bind preserved pre-review receipt"
            )
        if receipt["_started"] < authority["commits"][target_head]["committed_at"]:
            raise reporter.PilotDataError(
                f"execution receipt round {round_number} predates its head"
            )
        if receipt["_started"] < review["_submitted"]:
            raise reporter.PilotDataError(
                f"execution receipt round {round_number} predates remote review"
            )
        if receipt["_completed"] > evidence["captured"]:
            raise reporter.PilotDataError(
                f"execution receipt round {round_number} follows capture"
            )
        if (
            previous_completed is not None
            and receipt["_completed"] <= previous_completed
        ):
            raise reporter.PilotDataError(
                "execution receipts are not chronologically ordered by round"
            )
        previous_completed = receipt["_completed"]
        if (
            receipt["base_tree"] != actual_base_tree
            or receipt["candidate_tree"] != target_tree
            or receipt["checker_path"]
            != "scripts/workflow_pilot/review_base_checker.py"
            or receipt["checker_blob_oid"] != actual_checker_blob
            or receipt["argv"]
            != [
                "/usr/bin/python3",
                "-I",
                "review_base_checker.py",
                "--input",
                "checker-input.json",
            ]
            or receipt["assertion_program_path"] != ASSERTION_PROGRAM_PATH
            or receipt["assertion_program_blob_oid"]
            != actual_assertion_program_blob
            or receipt["assertion_program_argv"]
            != [
                "/usr/bin/python3",
                "-I",
                "review_assertions.py",
                "--stdin",
            ]
            or receipt["finding_origin_sha"] != finding_origin_sha
            or receipt["finding_origin_tree"] != finding_origin_tree
            or receipt["assertion_input_artifacts"]
            != assertion_input_artifacts
            or sorted(receipt["changed_files"]) != target_files
            or receipt["changes"] != target_changes
            or sorted(receipt["remote_finding_ids"])
            != sorted(review["finding_ids"])
        ):
            raise reporter.PilotDataError(
                "execution receipt does not match exact round Git/GitHub evidence"
            )
        requests = build_assertion_requests(
            contract, evidence["raw"], target_head, round_number
        )
        for request in requests:
            authority_binding = assertion_authority_binding(
                contract,
                evidence,
                authority["root"],
                target_head,
                round_number,
                request["assertion_id"],
                request["finding_id"],
            )
            result_id = assertion_result_id(
                round_number,
                request["assertion_id"],
                authority_binding,
            )
            expected_results[result_id] = {
                **request,
                "authority_binding": authority_binding,
                "candidate_sha": target_head,
                "review_round": round_number,
                "checker_input_sha256": receipt["checker_input_sha256"],
            }
        current_ids = {
            result["id"] for result in receipt["assertion_results"]
        }
        expected_current = {
            result_id
            for result_id, expected in expected_results.items()
            if expected["review_round"] == round_number
        }
        if current_ids != expected_current:
            raise reporter.PilotDataError(
                f"round {round_number} registry results are missing or fabricated"
            )
        if receipt_result_ids & current_ids:
            raise reporter.PilotDataError(
                "execution result IDs replay across rounds"
            )
        receipt_result_ids.update(current_ids)
        seals.append(receipt["seal"])
    if set(manifest) != set(expected_results):
        raise reporter.PilotDataError(
            "trusted registry results do not exactly cover derived assertions"
        )
    if receipt_result_ids != set(manifest):
        raise reporter.PilotDataError(
            "receipt assertion results do not match evidence manifest"
        )
    for result_id, result in manifest.items():
        expected = expected_results[result_id]
        expected_disposition = (
            expected["assertion_id"].split(":")[4]
            if expected["assertion_id"].startswith("registry:sibling:")
            else None
        )
        if (
            result["assertion_id"] != expected["assertion_id"]
            or result["check_id"] != expected["assertion_id"]
            or result["authority_binding"] != expected["authority_binding"]
            or result["program_blob_oid"] != actual_assertion_program_blob
            or result["base_sha"] != contract["base_sha"]
            or result["candidate_sha"] != expected["candidate_sha"]
            or result["authority_binding"]["head_sha"] != expected["candidate_sha"]
            or result["review_round"] != expected["review_round"]
            or result["claimed_disposition"] != expected_disposition
            or result["input_sha256"]
            != expected["checker_input_sha256"]
        ):
            raise reporter.PilotDataError(
                f"result {result_id!r} is fabricated or bound to another assertion"
            )
        if result["status"] == "hold":
            _validate_authority_hold_output(
                result["output"], f"evidence.result_manifest[{result_id}]"
            )
        if result["authority_binding"]["finding_id"] is not None:
            for field, value in result["authority_binding"].items():
                if result["output"].get(field) != value:
                    raise reporter.PilotDataError(
                        f"result {result_id!r} semantic output lost authoritative {field}"
                    )
    return seals


def _consume_disposition(
    event: dict[str, Any],
    hold: dict[str, Any],
    next_head: str,
    next_review: dict[str, Any] | None,
    evidence: dict[str, Any],
    forbidden_actor_ids: set[str],
) -> None:
    if (
        event["held_round"] != hold["round"]
        or event["held_head_sha"] != hold["candidate_sha"]
        or event["authorized_next_head_sha"] != next_head
        or event["authorized_next_head_sha"] == event["held_head_sha"]
    ):
        raise reporter.PilotDataError(
            "disposition does not bind exact held round/head and next head"
        )
    if event["actor_id"] not in evidence["actors"]:
        raise reporter.PilotDataError("disposition actor is not authenticated")
    if event["actor_id"] in forbidden_actor_ids:
        raise reporter.PilotDataError(
            "disposition actor overlaps implementer/reviewer/finding author"
        )
    held_at = hold["_submitted"]
    if event["_occurred"] <= held_at:
        raise reporter.PilotDataError("disposition does not follow held review")
    if next_review is not None and event["_occurred"] >= next_review["_submitted"]:
        raise reporter.PilotDataError("disposition does not precede next review")


def _progress_rounds(
    evidence: dict[str, Any],
    sweeps: dict[str, dict[str, Any]],
    forbidden_actor_ids: set[str],
):
    dispositions = evidence["architecture_dispositions"]
    disposition_index = 0
    consumed = []
    consecutive = 0
    pending = None
    handoffs = []
    reviews = evidence["remote_reviews"]
    for review in reviews:
        if pending is not None:
            if disposition_index >= len(dispositions):
                raise reporter.PilotDataError(
                    "current head advanced from held head without disposition"
                )
            event = dispositions[disposition_index]
            _consume_disposition(
                event,
                pending,
                review["candidate_sha"],
                review,
                evidence,
                forbidden_actor_ids,
            )
            consumed.append(event["node_id"])
            disposition_index += 1
            pending = None
            consecutive = 0
        if review["outcome"] == "clean":
            consecutive = 0
            continue
        consecutive += 1
        if consecutive <= 2:
            finding_sweeps = [
                sweeps[finding_id] for finding_id in review["finding_ids"]
            ]
            handoffs.append(
                {
                    "review_round": review["round"],
                    "consecutive_change_request": consecutive,
                    "candidate_sha": review["candidate_sha"],
                    "finding_handoffs": finding_sweeps,
                    "bounds": {
                        "findings": len(finding_sweeps),
                        "families": len(
                            {sweep["family"] for sweep in finding_sweeps}
                        ),
                        "siblings": sum(
                            len(sweep["siblings"]) for sweep in finding_sweeps
                        ),
                    },
                }
            )
        else:
            pending = {
                "round": review["round"],
                "candidate_sha": review["candidate_sha"],
                "submitted_at": review["submitted_at"],
                "_submitted": review["_submitted"],
                "reason": "third-consecutive-change-request",
            }
    if pending is not None:
        if disposition_index < len(dispositions):
            event = dispositions[disposition_index]
            _consume_disposition(
                event,
                pending,
                evidence["candidate"]["sha"],
                None,
                evidence,
                forbidden_actor_ids,
            )
            consumed.append(event["node_id"])
            disposition_index += 1
            pending = None
        elif evidence["candidate"]["sha"] != pending["candidate_sha"]:
            raise reporter.PilotDataError(
                "current head differs from held head without independent disposition"
            )
    if disposition_index != len(dispositions):
        raise reporter.PilotDataError(
            "architecture disposition is extra, reused, or noncausal"
        )
    return handoffs, pending, consumed


def progress_rounds(
    evidence: dict[str, Any],
    sweeps: dict[str, dict[str, Any]],
    forbidden_actor_ids: set[str],
):
    return _progress_rounds(evidence, sweeps, forbidden_actor_ids)


def build_report(
    raw_contract: Any,
    evidence_input: Any,
    repository_root: Path,
    expected_candidate: str,
) -> dict[str, Any]:
    """Validate candidate artifacts; this importable core is never authoritative."""
    if isinstance(evidence_input, bytes):
        try:
            raw_evidence = reporter.parse_json(
                evidence_input.decode("utf-8"), "immutable evidence bytes"
            )
        except UnicodeDecodeError as error:
            raise reporter.PilotDataError(
                "immutable evidence bytes are not UTF-8"
            ) from error
    else:
        raw_evidence = evidence_input
    contract = validate_contract(raw_contract)
    evidence = validate_evidence(raw_evidence)
    authority = _repository_authority(
        repository_root, expected_candidate, contract, evidence
    )
    _global_identity_check(evidence)
    trigger_authority = _resolve_authoritative_trigger(
        contract, evidence, authority
    )
    roles = _validate_roles_and_causality(
        contract,
        evidence,
        authority,
        trigger_authority["pre_review_required"],
    )
    sweeps, family_counts = _validate_findings_and_sweeps(contract, evidence)
    execution_seals = _validate_execution(contract, evidence, authority)
    forbidden_disposition_actors = {
        roles["implementer"]["id"],
        *(actor["id"] for actor in roles["remote_actors"]),
        *(
            [roles["pre_owner"]["id"]]
            if roles["pre_owner"] is not None
            else []
        ),
        *(
            finding["author_actor_id"]
            for finding in evidence["pre_review_findings"].values()
        ),
        *(
            finding["author_actor_id"]
            for finding in evidence["findings"].values()
        ),
    }
    handoffs, hold, consumed = progress_rounds(
        evidence, sweeps, forbidden_disposition_actors
    )
    latest = evidence["remote_reviews"][-1] if evidence["remote_reviews"] else None
    remote_head = evidence["pull_request"]["head_sha"]
    current_reviewed = bool(
        latest
        and remote_head == authority["head"]
        and latest["candidate_sha"] == authority["head"]
    )
    current_clean = bool(
        current_reviewed
        and latest["outcome"] == "clean"
        and not latest["finding_ids"]
        and latest["state"] != "CHANGES_REQUESTED"
        and not latest["body_has_findings"]
    )
    unresolved = sum(
        not thread["is_resolved"] for thread in evidence["threads"].values()
    )
    authority_holds = [
        {
            "id": result["id"],
            "assertion_id": result["assertion_id"],
            "finding_id": result["authority_binding"]["finding_id"],
            "hold_reason": result["output"].get("hold_reason"),
            "authority_dependencies": result["output"].get(
                "authority_dependencies", []
            ),
        }
        for result in evidence["result_manifest"].values()
        if result["status"] == "hold"
    ]
    executable_complete = bool(execution_seals)
    consumed_ids = set(consumed)
    transition_authorized = any(
        event["node_id"] in consumed_ids
        and event["held_head_sha"] == remote_head
        and event["authorized_next_head_sha"] == authority["head"]
        for event in evidence["architecture_dispositions"]
    )
    structural_push = bool(
        hold is None
        and not authority_holds
        and executable_complete
        and (current_clean or transition_authorized)
    )
    structural_merge = bool(
        structural_push and current_clean and unresolved == 0
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            "repository": contract["repository"],
            "pull_request": contract["pull_request"],
            "pull_request_node_id": evidence["pull_request"]["node_id"],
            "base_sha": contract["base_sha"],
            "remote_head_sha": evidence["pull_request"]["head_sha"],
            "original_pre_review_head": contract["original_pre_review_head"],
            "candidate_sha": authority["head"],
            "candidate_tree_oid": authority["tree"],
        },
        "provenance": {
            "source": evidence["source"]["kind"],
            "authoritative": False,
            "live_authoritative": False,
            "authenticated_receipt": False,
            "base_pinned_checker": False,
            "executable_evidence_trusted": False,
            "execution_receipt_seals": execution_seals,
        },
        "trigger": {
            **contract["trigger"],
            "adversarial_pre_review_required": trigger_authority[
                "pre_review_required"
            ],
            "authoritative": trigger_authority["authoritative"],
            "authoritative_path": trigger_authority["path"],
            "authoritative_blob_oid": trigger_authority["blob_oid"],
        },
        "limits": contract["limits"],
        "actors": {
            "implementer": roles["implementer"]["id"],
            "pre_reviewer": (
                roles["pre_owner"]["id"] if roles["pre_owner"] else None
            ),
            "remote_reviewers": sorted(
                {actor["id"] for actor in roles["remote_actors"]}
            ),
        },
        "behavior_rows": [
            {
                "id": row["id"],
                **BEHAVIOR_ROW_SPECS[row["id"]],
                "assertions": row["assertions"],
            }
            for row in contract["behavior_rows"]
        ],
        "families": {
            family: list(members) for family, members in FAMILY_MEMBERS.items()
        },
        "findings": {
            "pre_review_count": len(evidence["pre_review_findings"]),
            "remote_count": len(evidence["findings"]),
            "count": len(evidence["pre_review_findings"]) + len(evidence["findings"]),
            "current_unresolved": unresolved,
            "by_family": family_counts,
            "handoffs": [sweeps[finding_id] for finding_id in sorted(sweeps)],
        },
        "round_handoffs": handoffs,
        "architecture_hold": {
            "required": hold is not None,
            "record": (
                {
                    "round": hold["round"],
                    "candidate_sha": hold["candidate_sha"],
                    "reason": hold["reason"],
                }
                if hold
                else None
            ),
            "consumed_disposition_ids": consumed,
        },
        "authority_hold": {
            "required": bool(authority_holds),
            "reason": (
                "authority-dependency-changed" if authority_holds else None
            ),
            "external_review_required": bool(authority_holds),
            "fresh_base_required": bool(authority_holds),
            "results": authority_holds,
        },
        "gates": {
            "push_allowed": False,
            "trusted_push_allowed": False,
            "remote_copilot_review_required": True,
            "current_candidate_reviewed": current_reviewed,
            "current_candidate_clean": current_clean,
            "merge_allowed": False,
        },
        "structural_eligibility": {
            "push": structural_push,
            "merge": structural_merge,
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate inert sibling-family evidence for diagnostics."
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--expected-candidate", required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = build_report(
            reporter.load_json(args.contract),
            args.evidence.read_bytes(),
            args.repository_root,
            args.expected_candidate,
        )
    except reporter.PilotDataError as error:
        print(f"workflow review-family error: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(reporter.normalized_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
