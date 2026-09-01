#!/usr/bin/env python3
"""Validate, preview, and apply the issue #177 required-summary ruleset migration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RESPONSE_REQUIRED_FIELDS = frozenset(
    {
        "_links",
        "bypass_actors",
        "conditions",
        "created_at",
        "enforcement",
        "id",
        "name",
        "node_id",
        "rules",
        "source",
        "source_type",
        "target",
        "updated_at",
    }
)
PATCH_BODY_FIELDS = ("bypass_actors", "conditions", "enforcement", "name", "rules", "target")
IDENTITY_FIELDS = ("id", "name", "node_id", "source", "source_type", "target")
STABLE_RESPONSE_FIELDS = (
    "_links",
    "bypass_actors",
    "conditions",
    "created_at",
    "enforcement",
    "id",
    "name",
    "node_id",
    "rules",
    "source",
    "source_type",
    "target",
)
EXPECTED_RULE_ORDER = (
    "deletion",
    "non_fast_forward",
    "copilot_code_review",
    "pull_request",
    "required_status_checks",
    "code_scanning",
)
REVIEW_RULE_TYPES = frozenset({"copilot_code_review", "pull_request"})
REQUEST_SCOPED_FIELDS = frozenset({"current_user_can_bypass"})
VOLATILE_RESPONSE_FIELDS = frozenset({"updated_at"})
GH = "/usr/bin/gh"
HEADER_STATUS_RE = re.compile(r"^HTTP/\d+(?:\.\d+)?\s+(?P<status>\d{3})\b")
STRONG_ETAG_RE = re.compile(r'^"(?:[^"\\]|\\.)+"$')
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RULESET_ID_RE = re.compile(r"^[1-9][0-9]*$")
OWNER_COMPONENT_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
REPO_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_REPOSITORY_COMPONENT_LENGTH = 100


class RulesetContractError(ValueError):
    """The required-summary ruleset contract is invalid or stale."""


@dataclass(frozen=True)
class RulesetContract:
    repository: str
    ruleset_identity: dict[str, Any]
    status_check_contract: dict[str, Any]
    negative_proof_run_ids: tuple[int, ...]
    request_scoped_metadata_fields: tuple[str, ...]
    post_apply_volatile_fields: tuple[str, ...]
    source_ruleset_response: dict[str, Any]
    desired_ruleset_response: dict[str, Any]
    desired_patch_body: dict[str, Any]


def _json_object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RulesetContractError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def normalized_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise RulesetContractError(f"cannot read {path}: {error}") from error


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_json_bytes(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RulesetContractError(f"invalid UTF-8 in {label}: {error}") from error
    try:
        return json.loads(text, object_pairs_hook=_json_object_no_duplicates)
    except json.JSONDecodeError as error:
        raise RulesetContractError(f"invalid JSON in {label}: {error}") from error


def load_json(path: Path, *, expected_sha256: str | None = None) -> Any:
    data = _read_bytes(path)
    if expected_sha256 is not None:
        actual_sha256 = _sha256_hex(data)
        if actual_sha256 != expected_sha256:
            raise RulesetContractError(
                f"{path} sha256 differs: expected {expected_sha256}, got {actual_sha256}"
            )
    return _parse_json_bytes(data, str(path))


def _gh_environment() -> dict[str, str]:
    environment = {"LC_ALL": "C", "PATH": "/usr/bin:/bin"}
    for name in (
        "GH_CONFIG_DIR",
        "GH_HOST",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "HOME",
        "NO_COLOR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    ):
        value = os.environ.get(name)
        if value is not None:
            environment[name] = value
    return environment


def _validate_repository(repository: str) -> str:
    repository = _expect_string(repository, "repository")
    if any(char in repository for char in ("\\", "%", "?", "#")) or any(
        ord(char) < 0x20 or ord(char) == 0x7F for char in repository
    ):
        raise RulesetContractError(f"repository {repository!r} is invalid")
    if repository.count("/") != 1:
        raise RulesetContractError(f"repository {repository!r} is invalid")
    owner, name = repository.split("/")
    if not owner or not name:
        raise RulesetContractError(f"repository {repository!r} is invalid")
    if OWNER_COMPONENT_RE.fullmatch(owner) is None:
        raise RulesetContractError(f"repository {repository!r} is invalid")
    if name in {".", ".."}:
        raise RulesetContractError(f"repository {repository!r} is invalid")
    if len(name) > MAX_REPOSITORY_COMPONENT_LENGTH:
        raise RulesetContractError(f"repository {repository!r} is invalid")
    if name[0] == "." or name[-1] == ".":
        raise RulesetContractError(f"repository {repository!r} is invalid")
    if name.lower().endswith(".git"):
        raise RulesetContractError(f"repository {repository!r} is invalid")
    if REPO_COMPONENT_RE.fullmatch(name) is None:
        raise RulesetContractError(f"repository {repository!r} is invalid")
    return repository


def _validate_ruleset_id(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise RulesetContractError(f"{label} must be a positive integer")
    if isinstance(value, int):
        if value < 1:
            raise RulesetContractError(f"{label} must be a positive integer")
        return value
    if not isinstance(value, str) or RULESET_ID_RE.fullmatch(value) is None:
        raise RulesetContractError(f"{label} must be a positive integer")
    return int(value)


def _validate_sha256(value: str, label: str) -> str:
    digest = _expect_string(value, label)
    if SHA256_RE.fullmatch(digest) is None:
        raise RulesetContractError(f"{label} must be a lowercase 64-hex sha256")
    return digest


def _endpoint(repository: str, ruleset_id: int) -> str:
    return f"repos/{repository}/rulesets/{ruleset_id}"


def _load_validated_contract(
    path: Path,
    *,
    expected_sha256: str | None = None,
) -> RulesetContract:
    return validate_contract(load_json(path, expected_sha256=expected_sha256))


def _require_trusted_target(
    contract: RulesetContract,
    *,
    repository: str,
    ruleset_id: int,
) -> None:
    for label, source, expected in (
        ("contract.repository", contract.repository, repository),
        ("contract.ruleset_identity.source", contract.ruleset_identity["source"], repository),
        ("contract.ruleset_identity.id", contract.ruleset_identity["id"], ruleset_id),
        (
            "contract.source_ruleset_response.source",
            contract.source_ruleset_response["source"],
            repository,
        ),
        (
            "contract.source_ruleset_response.id",
            contract.source_ruleset_response["id"],
            ruleset_id,
        ),
        (
            "contract.desired_ruleset_response.source",
            contract.desired_ruleset_response["source"],
            repository,
        ),
        (
            "contract.desired_ruleset_response.id",
            contract.desired_ruleset_response["id"],
            ruleset_id,
        ),
    ):
        if source != expected:
            raise RulesetContractError(
                f"{label} must match the trusted apply-live target"
            )


def _run_gh_api(
    arguments: list[str],
    *,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            [GH, "api", *arguments],
            input=input_bytes,
            check=False,
            capture_output=True,
            env=_gh_environment(),
        )
    except OSError as error:
        raise RulesetContractError(f"cannot execute trusted gh CLI: {error}") from error


def _parse_http_response(raw: bytes, label: str) -> tuple[int, dict[str, str], Any]:
    text = raw.decode("utf-8")
    normalized = text.replace("\r\n", "\n")
    headers_text, separator, body = normalized.partition("\n\n")
    if not separator:
        raise RulesetContractError(f"{label} must include HTTP headers and a JSON body")
    header_lines = headers_text.splitlines()
    if not header_lines:
        raise RulesetContractError(f"{label} is missing an HTTP status line")
    match = HEADER_STATUS_RE.match(header_lines[0])
    if match is None:
        raise RulesetContractError(f"{label} has an invalid HTTP status line")
    headers: dict[str, str] = {}
    for index, line in enumerate(header_lines[1:], start=1):
        if ":" not in line:
            raise RulesetContractError(f"{label} header {index} is malformed")
        name, value = line.split(":", 1)
        key = name.strip().lower()
        if not key:
            raise RulesetContractError(f"{label} header {index} has an empty name")
        if key in headers:
            raise RulesetContractError(f"{label} repeats header {key!r}")
        headers[key] = value.strip()
    try:
        payload = json.loads(body, object_pairs_hook=_json_object_no_duplicates)
    except json.JSONDecodeError as error:
        raise RulesetContractError(f"{label} body is not valid JSON: {error}") from error
    return int(match.group("status")), headers, payload


def _strong_etag(headers: dict[str, str], label: str) -> str:
    etag = headers.get("etag")
    if etag is None:
        raise RulesetContractError(
            f"{label} did not return a strong ETag; conditional update is unavailable"
        )
    if etag.startswith("W/") or STRONG_ETAG_RE.fullmatch(etag) is None:
        raise RulesetContractError(
            f"{label} did not return a strong ETag; conditional update is unavailable"
        )
    return etag


def _expect_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RulesetContractError(f"{label} must be an object")
    return value


def _expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RulesetContractError(f"{label} must be a list")
    return value


def _expect_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise RulesetContractError(f"{label} must be a boolean")
    return value


def _expect_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RulesetContractError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise RulesetContractError(f"{label} must be at least {minimum}")
    return value


def _expect_string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise RulesetContractError(f"{label} must be a string")
    if not allow_empty and not value:
        raise RulesetContractError(f"{label} must be nonempty")
    return value


def _expect_exact_keys(
    value: dict[str, Any],
    label: str,
    expected: set[str],
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if extra:
            detail.append("extra: " + ", ".join(extra))
        raise RulesetContractError(f"{label} has unexpected fields ({'; '.join(detail)})")


def _normalize_bypass_actor(raw: Any, label: str) -> dict[str, Any]:
    actor = _expect_object(raw, label)
    _expect_exact_keys(actor, label, {"actor_id", "actor_type", "bypass_mode"})
    return {
        "actor_id": _expect_int(actor["actor_id"], f"{label}.actor_id", minimum=1),
        "actor_type": _expect_string(actor["actor_type"], f"{label}.actor_type"),
        "bypass_mode": _expect_string(actor["bypass_mode"], f"{label}.bypass_mode"),
    }


def _normalize_links(raw: Any, label: str) -> dict[str, Any]:
    links = _expect_object(raw, label)
    _expect_exact_keys(links, label, {"html", "self"})
    result = {}
    for key in ("self", "html"):
        entry = _expect_object(links[key], f"{label}.{key}")
        _expect_exact_keys(entry, f"{label}.{key}", {"href"})
        result[key] = {"href": _expect_string(entry["href"], f"{label}.{key}.href")}
    return result


def _normalize_ref_name_conditions(raw: Any, label: str) -> dict[str, Any]:
    conditions = _expect_object(raw, label)
    _expect_exact_keys(conditions, label, {"exclude", "include"})
    include = [_expect_string(item, f"{label}.include[{index}]") for index, item in enumerate(_expect_list(conditions["include"], f"{label}.include"))]
    exclude = [_expect_string(item, f"{label}.exclude[{index}]") for index, item in enumerate(_expect_list(conditions["exclude"], f"{label}.exclude"))]
    return {"exclude": exclude, "include": include}


def _normalize_conditions(raw: Any, label: str) -> dict[str, Any]:
    conditions = _expect_object(raw, label)
    _expect_exact_keys(conditions, label, {"ref_name"})
    return {"ref_name": _normalize_ref_name_conditions(conditions["ref_name"], f"{label}.ref_name")}


def _normalize_status_check_entry(raw: Any, label: str) -> dict[str, Any]:
    entry = _expect_object(raw, label)
    _expect_exact_keys(entry, label, {"context", "integration_id"})
    return {
        "context": _expect_string(entry["context"], f"{label}.context"),
        "integration_id": _expect_int(entry["integration_id"], f"{label}.integration_id", minimum=1),
    }


def _normalize_required_status_checks(raw: Any, label: str) -> list[dict[str, Any]]:
    checks = []
    seen_contexts = set()
    for index, item in enumerate(_expect_list(raw, label)):
        normalized = _normalize_status_check_entry(item, f"{label}[{index}]")
        context = normalized["context"]
        if context in seen_contexts:
            raise RulesetContractError(f"{label} repeats context {context!r}")
        seen_contexts.add(context)
        checks.append(normalized)
    return checks


def _normalize_rule(raw: Any, label: str) -> dict[str, Any]:
    rule = _expect_object(raw, label)
    rule_type = _expect_string(rule.get("type"), f"{label}.type")
    if rule_type == "deletion" or rule_type == "non_fast_forward":
        _expect_exact_keys(rule, label, {"type"})
        return {"type": rule_type}
    if rule_type == "copilot_code_review":
        _expect_exact_keys(rule, label, {"parameters", "type"})
        parameters = _expect_object(rule["parameters"], f"{label}.parameters")
        _expect_exact_keys(
            parameters,
            f"{label}.parameters",
            {"review_draft_pull_requests", "review_on_push"},
        )
        return {
            "type": rule_type,
            "parameters": {
                "review_draft_pull_requests": _expect_bool(
                    parameters["review_draft_pull_requests"],
                    f"{label}.parameters.review_draft_pull_requests",
                ),
                "review_on_push": _expect_bool(
                    parameters["review_on_push"],
                    f"{label}.parameters.review_on_push",
                ),
            },
        }
    if rule_type == "pull_request":
        _expect_exact_keys(rule, label, {"parameters", "type"})
        parameters = _expect_object(rule["parameters"], f"{label}.parameters")
        _expect_exact_keys(
            parameters,
            f"{label}.parameters",
            {
                "allowed_merge_methods",
                "dismiss_stale_reviews_on_push",
                "require_code_owner_review",
                "require_extra_approval_for_unattributed_changes",
                "require_last_push_approval",
                "required_approving_review_count",
                "required_review_thread_resolution",
                "required_reviewers",
            },
        )
        return {
            "type": rule_type,
            "parameters": {
                "allowed_merge_methods": [
                    _expect_string(
                        value,
                        f"{label}.parameters.allowed_merge_methods[{index}]",
                    )
                    for index, value in enumerate(
                        _expect_list(
                            parameters["allowed_merge_methods"],
                            f"{label}.parameters.allowed_merge_methods",
                        )
                    )
                ],
                "dismiss_stale_reviews_on_push": _expect_bool(
                    parameters["dismiss_stale_reviews_on_push"],
                    f"{label}.parameters.dismiss_stale_reviews_on_push",
                ),
                "require_code_owner_review": _expect_bool(
                    parameters["require_code_owner_review"],
                    f"{label}.parameters.require_code_owner_review",
                ),
                "require_extra_approval_for_unattributed_changes": _expect_bool(
                    parameters["require_extra_approval_for_unattributed_changes"],
                    f"{label}.parameters.require_extra_approval_for_unattributed_changes",
                ),
                "require_last_push_approval": _expect_bool(
                    parameters["require_last_push_approval"],
                    f"{label}.parameters.require_last_push_approval",
                ),
                "required_approving_review_count": _expect_int(
                    parameters["required_approving_review_count"],
                    f"{label}.parameters.required_approving_review_count",
                    minimum=0,
                ),
                "required_review_thread_resolution": _expect_bool(
                    parameters["required_review_thread_resolution"],
                    f"{label}.parameters.required_review_thread_resolution",
                ),
                "required_reviewers": [
                    _expect_string(
                        value,
                        f"{label}.parameters.required_reviewers[{index}]",
                    )
                    for index, value in enumerate(
                        _expect_list(
                            parameters["required_reviewers"],
                            f"{label}.parameters.required_reviewers",
                        )
                    )
                ],
            },
        }
    if rule_type == "required_status_checks":
        _expect_exact_keys(rule, label, {"parameters", "type"})
        parameters = _expect_object(rule["parameters"], f"{label}.parameters")
        _expect_exact_keys(
            parameters,
            f"{label}.parameters",
            {
                "do_not_enforce_on_create",
                "required_status_checks",
                "strict_required_status_checks_policy",
            },
        )
        return {
            "type": rule_type,
            "parameters": {
                "do_not_enforce_on_create": _expect_bool(
                    parameters["do_not_enforce_on_create"],
                    f"{label}.parameters.do_not_enforce_on_create",
                ),
                "required_status_checks": _normalize_required_status_checks(
                    parameters["required_status_checks"],
                    f"{label}.parameters.required_status_checks",
                ),
                "strict_required_status_checks_policy": _expect_bool(
                    parameters["strict_required_status_checks_policy"],
                    f"{label}.parameters.strict_required_status_checks_policy",
                ),
            },
        }
    if rule_type == "code_scanning":
        _expect_exact_keys(rule, label, {"parameters", "type"})
        parameters = _expect_object(rule["parameters"], f"{label}.parameters")
        _expect_exact_keys(parameters, f"{label}.parameters", {"code_scanning_tools"})
        tools = []
        seen_tools = set()
        for index, raw_tool in enumerate(
            _expect_list(
                parameters["code_scanning_tools"],
                f"{label}.parameters.code_scanning_tools",
            )
        ):
            tool = _expect_object(
                raw_tool,
                f"{label}.parameters.code_scanning_tools[{index}]",
            )
            _expect_exact_keys(
                tool,
                f"{label}.parameters.code_scanning_tools[{index}]",
                {"alerts_threshold", "security_alerts_threshold", "tool"},
            )
            name = _expect_string(
                tool["tool"],
                f"{label}.parameters.code_scanning_tools[{index}].tool",
            )
            if name in seen_tools:
                raise RulesetContractError(
                    f"{label}.parameters.code_scanning_tools repeats tool {name!r}"
                )
            seen_tools.add(name)
            tools.append(
                {
                    "alerts_threshold": _expect_string(
                        tool["alerts_threshold"],
                        f"{label}.parameters.code_scanning_tools[{index}].alerts_threshold",
                    ),
                    "security_alerts_threshold": _expect_string(
                        tool["security_alerts_threshold"],
                        f"{label}.parameters.code_scanning_tools[{index}].security_alerts_threshold",
                    ),
                    "tool": name,
                }
            )
        return {"type": rule_type, "parameters": {"code_scanning_tools": tools}}
    raise RulesetContractError(f"{label}.type {rule_type!r} is unsupported")


def _normalize_rules(raw: Any, label: str) -> list[dict[str, Any]]:
    rules = []
    seen_types = set()
    for index, rule in enumerate(_expect_list(raw, label)):
        normalized = _normalize_rule(rule, f"{label}[{index}]")
        rule_type = normalized["type"]
        if rule_type in seen_types:
            raise RulesetContractError(f"{label} repeats rule type {rule_type!r}")
        seen_types.add(rule_type)
        rules.append(normalized)
    if tuple(rule["type"] for rule in rules) != EXPECTED_RULE_ORDER:
        raise RulesetContractError(
            f"{label} must contain rules in exact order {EXPECTED_RULE_ORDER}"
        )
    return rules


def normalize_ruleset_response(
    raw: Any,
    label: str,
    *,
    volatile_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    response = _expect_object(raw, label)
    actual_fields = set(response)
    expected_fields = set(RESPONSE_REQUIRED_FIELDS) | set(REQUEST_SCOPED_FIELDS)
    missing = sorted(
        set(RESPONSE_REQUIRED_FIELDS) - actual_fields - set(volatile_fields)
    )
    extra = sorted(actual_fields - expected_fields)
    if missing or extra:
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if extra:
            detail.append("extra: " + ", ".join(extra))
        raise RulesetContractError(
            f"{label} has unexpected fields ({'; '.join(detail)})"
        )
    normalized = {
        "_links": _normalize_links(response["_links"], f"{label}._links"),
        "bypass_actors": [
            _normalize_bypass_actor(actor, f"{label}.bypass_actors[{index}]")
            for index, actor in enumerate(
                _expect_list(response["bypass_actors"], f"{label}.bypass_actors")
            )
        ],
        "conditions": _normalize_conditions(response["conditions"], f"{label}.conditions"),
        "created_at": _expect_string(response["created_at"], f"{label}.created_at"),
        "enforcement": _expect_string(response["enforcement"], f"{label}.enforcement"),
        "id": _expect_int(response["id"], f"{label}.id", minimum=1),
        "name": _expect_string(response["name"], f"{label}.name"),
        "node_id": _expect_string(response["node_id"], f"{label}.node_id"),
        "rules": _normalize_rules(response["rules"], f"{label}.rules"),
        "source": _expect_string(response["source"], f"{label}.source"),
        "source_type": _expect_string(response["source_type"], f"{label}.source_type"),
        "target": _expect_string(response["target"], f"{label}.target"),
    }
    if "current_user_can_bypass" in response:
        _expect_string(
            response["current_user_can_bypass"],
            f"{label}.current_user_can_bypass",
        )
    if "updated_at" in response:
        normalized["updated_at"] = _expect_string(
            response["updated_at"],
            f"{label}.updated_at",
        )
    return normalized


def normalize_patch_body(raw: Any, label: str) -> dict[str, Any]:
    body = _expect_object(raw, label)
    _expect_exact_keys(body, label, set(PATCH_BODY_FIELDS))
    return {
        "bypass_actors": [
            _normalize_bypass_actor(actor, f"{label}.bypass_actors[{index}]")
            for index, actor in enumerate(
                _expect_list(body["bypass_actors"], f"{label}.bypass_actors")
            )
        ],
        "conditions": _normalize_conditions(body["conditions"], f"{label}.conditions"),
        "enforcement": _expect_string(body["enforcement"], f"{label}.enforcement"),
        "name": _expect_string(body["name"], f"{label}.name"),
        "rules": _normalize_rules(body["rules"], f"{label}.rules"),
        "target": _expect_string(body["target"], f"{label}.target"),
    }


def _normalize_status_contract(raw: Any, label: str) -> dict[str, Any]:
    contract = _expect_object(raw, label)
    _expect_exact_keys(
        contract,
        label,
        {
            "do_not_enforce_on_create",
            "preserved_independent",
            "removed",
            "required",
            "strict_required_status_checks_policy",
        },
    )
    return {
        "do_not_enforce_on_create": _expect_bool(
            contract["do_not_enforce_on_create"],
            f"{label}.do_not_enforce_on_create",
        ),
        "preserved_independent": _normalize_required_status_checks(
            contract["preserved_independent"],
            f"{label}.preserved_independent",
        ),
        "removed": _normalize_required_status_checks(
            contract["removed"],
            f"{label}.removed",
        ),
        "required": _normalize_required_status_checks(
            contract["required"],
            f"{label}.required",
        ),
        "strict_required_status_checks_policy": _expect_bool(
            contract["strict_required_status_checks_policy"],
            f"{label}.strict_required_status_checks_policy",
        ),
    }


def _normalize_ruleset_identity(raw: Any, label: str) -> dict[str, Any]:
    identity = _expect_object(raw, label)
    _expect_exact_keys(identity, label, set(IDENTITY_FIELDS))
    return {
        "id": _expect_int(identity["id"], f"{label}.id", minimum=1),
        "name": _expect_string(identity["name"], f"{label}.name"),
        "node_id": _expect_string(identity["node_id"], f"{label}.node_id"),
        "source": _expect_string(identity["source"], f"{label}.source"),
        "source_type": _expect_string(identity["source_type"], f"{label}.source_type"),
        "target": _expect_string(identity["target"], f"{label}.target"),
    }


def _first_difference(expected: Any, actual: Any, path: str = "$") -> str | None:
    if type(expected) is not type(actual):
        return f"{path} type differs: expected {type(expected).__name__}, got {type(actual).__name__}"
    if isinstance(expected, dict):
        expected_keys = set(expected)
        actual_keys = set(actual)
        if expected_keys != actual_keys:
            return (
                f"{path} keys differ: expected {sorted(expected_keys)}, "
                f"got {sorted(actual_keys)}"
            )
        for key in sorted(expected_keys):
            difference = _first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference is not None:
                return difference
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path} length differs: expected {len(expected)}, got {len(actual)}"
        for index, (left, right) in enumerate(zip(expected, actual)):
            difference = _first_difference(left, right, f"{path}[{index}]")
            if difference is not None:
                return difference
        return None
    if expected != actual:
        return f"{path} differs: expected {expected!r}, got {actual!r}"
    return None


def _project_patch_body(ruleset_response: dict[str, Any]) -> dict[str, Any]:
    return {field: ruleset_response[field] for field in PATCH_BODY_FIELDS}


def _required_status_rule(ruleset_response: dict[str, Any], label: str) -> dict[str, Any]:
    matches = [rule for rule in ruleset_response["rules"] if rule["type"] == "required_status_checks"]
    if len(matches) != 1:
        raise RulesetContractError(f"{label} must contain exactly one required_status_checks rule")
    return matches[0]


def _normalized_status_tuple(check: dict[str, Any]) -> tuple[str, int]:
    return (check["context"], check["integration_id"])


def _strip_volatile(value: Any, volatile_fields: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item, volatile_fields)
            for key, item in value.items()
            if key not in volatile_fields
        }
    if isinstance(value, list):
        return [_strip_volatile(item, volatile_fields) for item in value]
    return value


def validate_contract(raw: Any) -> RulesetContract:
    contract = _expect_object(raw, "contract")
    _expect_exact_keys(
        contract,
        "contract",
        {
            "desired_patch_body",
            "desired_ruleset_response",
            "negative_proof_run_ids",
            "post_apply_volatile_fields",
            "repository",
            "request_scoped_metadata_fields",
            "ruleset_identity",
            "schema_version",
            "source_ruleset_response",
            "status_check_contract",
        },
    )
    schema_version = _expect_int(contract["schema_version"], "contract.schema_version")
    if schema_version != 3:
        raise RulesetContractError("contract.schema_version must be 3")
    repository = _expect_string(contract["repository"], "contract.repository")
    request_scoped_fields = tuple(
        _expect_string(
            value,
            f"contract.request_scoped_metadata_fields[{index}]",
        )
        for index, value in enumerate(
            _expect_list(
                contract["request_scoped_metadata_fields"],
                "contract.request_scoped_metadata_fields",
            )
        )
    )
    if request_scoped_fields != ("current_user_can_bypass",):
        raise RulesetContractError(
            "contract.request_scoped_metadata_fields must be exactly ['current_user_can_bypass']"
        )
    ruleset_identity = _normalize_ruleset_identity(
        contract["ruleset_identity"],
        "contract.ruleset_identity",
    )
    volatile_fields = tuple(
        _expect_string(
            value,
            f"contract.post_apply_volatile_fields[{index}]",
        )
        for index, value in enumerate(
            _expect_list(
                contract["post_apply_volatile_fields"],
                "contract.post_apply_volatile_fields",
            )
        )
    )
    if volatile_fields != ("updated_at",):
        raise RulesetContractError(
            "contract.post_apply_volatile_fields must be exactly ['updated_at']"
        )
    negative_proof_run_ids = tuple(
        _expect_int(
            value,
            f"contract.negative_proof_run_ids[{index}]",
            minimum=1,
        )
        for index, value in enumerate(
            _expect_list(
                contract["negative_proof_run_ids"],
                "contract.negative_proof_run_ids",
            )
        )
    )
    if len(set(negative_proof_run_ids)) != len(negative_proof_run_ids):
        raise RulesetContractError("contract.negative_proof_run_ids repeats a run ID")

    status_check_contract = _normalize_status_contract(
        contract["status_check_contract"],
        "contract.status_check_contract",
    )
    source_ruleset_response = normalize_ruleset_response(
        contract["source_ruleset_response"],
        "contract.source_ruleset_response",
        volatile_fields=(),
    )
    desired_ruleset_response = normalize_ruleset_response(
        contract["desired_ruleset_response"],
        "contract.desired_ruleset_response",
        volatile_fields=volatile_fields,
    )
    desired_patch_body = normalize_patch_body(
        contract["desired_patch_body"],
        "contract.desired_patch_body",
    )

    if ruleset_identity["source"] != repository:
        raise RulesetContractError("contract.repository must match ruleset_identity.source")

    for label, response in (
        ("contract.source_ruleset_response", source_ruleset_response),
        ("contract.desired_ruleset_response", desired_ruleset_response),
    ):
        for field in IDENTITY_FIELDS:
            if response[field] != ruleset_identity[field]:
                raise RulesetContractError(
                    f"{label}.{field} must match contract.ruleset_identity.{field}"
                )

    for field in STABLE_RESPONSE_FIELDS:
        if field == "rules":
            continue
        if source_ruleset_response[field] != desired_ruleset_response[field]:
            raise RulesetContractError(
                f"contract desired ruleset must preserve {field}"
            )

    source_rules = source_ruleset_response["rules"]
    desired_rules = desired_ruleset_response["rules"]
    if [rule["type"] for rule in source_rules] != [rule["type"] for rule in desired_rules]:
        raise RulesetContractError("contract desired ruleset must preserve exact rule ordering")
    for source_rule, desired_rule in zip(source_rules, desired_rules):
        if source_rule["type"] != desired_rule["type"]:
            raise RulesetContractError("contract desired ruleset retargets a rule type")
        if source_rule["type"] != "required_status_checks" and source_rule != desired_rule:
            raise RulesetContractError(
                f"contract desired ruleset must preserve {source_rule['type']} exactly"
            )

    source_status_rule = _required_status_rule(
        source_ruleset_response,
        "contract.source_ruleset_response",
    )
    desired_status_rule = _required_status_rule(
        desired_ruleset_response,
        "contract.desired_ruleset_response",
    )
    if source_status_rule["parameters"]["strict_required_status_checks_policy"] != status_check_contract["strict_required_status_checks_policy"]:
        raise RulesetContractError("contract source strict_required_status_checks_policy differs")
    if desired_status_rule["parameters"]["strict_required_status_checks_policy"] != status_check_contract["strict_required_status_checks_policy"]:
        raise RulesetContractError("contract desired strict_required_status_checks_policy differs")
    if source_status_rule["parameters"]["do_not_enforce_on_create"] != status_check_contract["do_not_enforce_on_create"]:
        raise RulesetContractError("contract source do_not_enforce_on_create differs")
    if desired_status_rule["parameters"]["do_not_enforce_on_create"] != status_check_contract["do_not_enforce_on_create"]:
        raise RulesetContractError("contract desired do_not_enforce_on_create differs")

    source_checks = source_status_rule["parameters"]["required_status_checks"]
    desired_checks = desired_status_rule["parameters"]["required_status_checks"]
    required_checks = status_check_contract["required"]
    preserved_checks = status_check_contract["preserved_independent"]
    removed_checks = status_check_contract["removed"]

    source_status_set = {_normalized_status_tuple(check) for check in source_checks}
    desired_status_set = {_normalized_status_tuple(check) for check in desired_checks}
    required_set = {_normalized_status_tuple(check) for check in required_checks}
    preserved_set = {_normalized_status_tuple(check) for check in preserved_checks}
    removed_set = {_normalized_status_tuple(check) for check in removed_checks}

    if len(required_set) != len(required_checks):
        raise RulesetContractError("contract.status_check_contract.required repeats a context")
    if len(preserved_set) != len(preserved_checks):
        raise RulesetContractError(
            "contract.status_check_contract.preserved_independent repeats a context"
        )
    if len(removed_set) != len(removed_checks):
        raise RulesetContractError("contract.status_check_contract.removed repeats a context")
    if required_set & preserved_set:
        raise RulesetContractError("contract status-check required and preserved sets overlap")
    if removed_set & required_set:
        raise RulesetContractError("contract status-check removed and required sets overlap")
    if removed_set & preserved_set:
        raise RulesetContractError("contract status-check removed and preserved sets overlap")
    if desired_checks != preserved_checks + required_checks:
        raise RulesetContractError(
            "contract desired required_status_checks must equal preserved_independent + required"
        )
    if not required_set <= source_status_set:
        raise RulesetContractError("contract source ruleset omits a required target status check")
    if not preserved_set <= source_status_set:
        raise RulesetContractError("contract source ruleset omits a preserved independent status check")
    if not removed_set <= source_status_set:
        raise RulesetContractError("contract source ruleset omits a removed status check")
    if removed_set & desired_status_set:
        raise RulesetContractError("contract desired ruleset still retains a removed direct worker check")
    if desired_status_set - (required_set | preserved_set):
        raise RulesetContractError("contract desired ruleset adds an undeclared status check")
    if source_status_set - desired_status_set != removed_set:
        raise RulesetContractError("contract desired ruleset removes something other than the declared direct worker checks")

    projected_patch_body = _project_patch_body(desired_ruleset_response)
    if desired_patch_body != projected_patch_body:
        raise RulesetContractError("contract.desired_patch_body must exactly match desired_ruleset_response")

    return RulesetContract(
        repository=repository,
        ruleset_identity=ruleset_identity,
        status_check_contract=status_check_contract,
        negative_proof_run_ids=negative_proof_run_ids,
        request_scoped_metadata_fields=request_scoped_fields,
        post_apply_volatile_fields=volatile_fields,
        source_ruleset_response=source_ruleset_response,
        desired_ruleset_response=desired_ruleset_response,
        desired_patch_body=desired_patch_body,
    )


def preview_patch(contract: RulesetContract, live_ruleset: Any) -> dict[str, Any]:
    normalized_live = normalize_ruleset_response(
        live_ruleset,
        "live_ruleset",
        volatile_fields=(),
    )
    source_difference = _first_difference(
        contract.source_ruleset_response,
        normalized_live,
    )
    if source_difference is not None:
        raise RulesetContractError(
            "live ruleset does not match the encoded source state: "
            + source_difference
        )
    if _first_difference(
        _strip_volatile(contract.desired_ruleset_response, contract.post_apply_volatile_fields),
        _strip_volatile(normalized_live, contract.post_apply_volatile_fields),
    ) is None:
        raise RulesetContractError("live ruleset already matches the desired post-migration state")
    return contract.desired_patch_body


def verify_live_ruleset(contract: RulesetContract, live_ruleset: Any) -> dict[str, Any]:
    normalized_live = normalize_ruleset_response(
        live_ruleset,
        "live_ruleset",
        volatile_fields=contract.post_apply_volatile_fields,
    )
    desired_difference = _first_difference(
        _strip_volatile(contract.desired_ruleset_response, contract.post_apply_volatile_fields),
        _strip_volatile(normalized_live, contract.post_apply_volatile_fields),
    )
    if desired_difference is not None:
        raise RulesetContractError(
            "live ruleset does not match the desired post-migration state: "
            + desired_difference
        )
    return {
        "repository": contract.repository,
        "required_build_contexts": [
            entry["context"] for entry in contract.status_check_contract["required"]
        ],
        "ruleset_id": contract.ruleset_identity["id"],
        "status": "ok",
        "verified_state": "desired",
    }


def apply_live(
    contract: RulesetContract,
    *,
    repository: str,
    ruleset_id: int,
) -> dict[str, Any]:
    trusted_repository = _validate_repository(repository)
    trusted_ruleset_id = _validate_ruleset_id(ruleset_id, "ruleset_id")
    _require_trusted_target(
        contract,
        repository=trusted_repository,
        ruleset_id=trusted_ruleset_id,
    )
    endpoint = _endpoint(trusted_repository, trusted_ruleset_id)

    get_response = _run_gh_api(["--include", endpoint])
    if get_response.returncode != 0:
        detail = get_response.stderr.decode("utf-8", errors="replace").strip()
        raise RulesetContractError(
            "trusted gh ruleset GET failed"
            + (f": {detail}" if detail else "")
        )
    status, headers, body = _parse_http_response(
        get_response.stdout,
        "trusted gh ruleset GET",
    )
    if status != 200:
        raise RulesetContractError(
            f"trusted gh ruleset GET returned HTTP {status}, expected 200"
        )
    etag = _strong_etag(headers, "trusted gh ruleset GET")
    patch_body = preview_patch(contract, body)

    put_response = _run_gh_api(
        [
            "--include",
            "--method",
            "PUT",
            "-H",
            f"If-Match: {etag}",
            endpoint,
            "--input",
            "-",
        ],
        input_bytes=normalized_json(patch_body),
    )
    if put_response.returncode != 0:
        stdout = put_response.stdout.decode("utf-8", errors="replace")
        stderr = put_response.stderr.decode("utf-8", errors="replace").strip()
        if "412" in stdout or "412" in stderr or "Precondition Failed" in stdout or "Precondition Failed" in stderr:
            raise RulesetContractError(
                "conditional ruleset update failed with HTTP 412 Precondition Failed; live ruleset changed concurrently"
            )
        raise RulesetContractError(
            "trusted gh ruleset PUT failed"
            + (f": {stderr}" if stderr else "")
        )
    put_status, _, _ = _parse_http_response(
        put_response.stdout,
        "trusted gh ruleset PUT",
    )
    if put_status != 200:
        raise RulesetContractError(
            f"trusted gh ruleset PUT returned HTTP {put_status}, expected 200"
        )

    refetch = _run_gh_api(["--include", endpoint])
    if refetch.returncode != 0:
        detail = refetch.stderr.decode("utf-8", errors="replace").strip()
        raise RulesetContractError(
            "trusted gh ruleset refetch failed"
            + (f": {detail}" if detail else "")
        )
    refetch_status, _, refetched_body = _parse_http_response(
        refetch.stdout,
        "trusted gh ruleset refetch",
    )
    if refetch_status != 200:
        raise RulesetContractError(
            f"trusted gh ruleset refetch returned HTTP {refetch_status}, expected 200"
        )
    return verify_live_ruleset(contract, refetched_body)


def apply_live_from_contract_path(
    contract_path: Path,
    *,
    repository: str,
    ruleset_id: Any,
    expected_contract_sha256: str,
) -> dict[str, Any]:
    trusted_repository = _validate_repository(repository)
    trusted_ruleset_id = _validate_ruleset_id(
        ruleset_id,
        "argument --ruleset-id",
    )
    trusted_contract_sha256 = _validate_sha256(
        expected_contract_sha256,
        "argument --expected-contract-sha256",
    )
    contract = _load_validated_contract(
        contract_path,
        expected_sha256=trusted_contract_sha256,
    )
    return apply_live(
        contract,
        repository=trusted_repository,
        ruleset_id=trusted_ruleset_id,
    )


def _write_output(path: Path | None, payload: Any) -> None:
    data = normalized_json(payload)
    if path is None:
        sys.stdout.buffer.write(data)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the required-summary ruleset contract and preview or apply the owner patch body.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("preview", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--contract", type=Path, required=True)
        subparser.add_argument("--live", type=Path, required=True)
        subparser.add_argument("--output", type=Path)
    apply_live_parser = subparsers.add_parser("apply-live")
    apply_live_parser.add_argument("--contract", type=Path, required=True)
    apply_live_parser.add_argument("--repository", required=True)
    apply_live_parser.add_argument("--ruleset-id", required=True)
    apply_live_parser.add_argument("--expected-contract-sha256", required=True)
    apply_live_parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "apply-live":
            _write_output(
                args.output,
                apply_live_from_contract_path(
                    args.contract,
                    repository=args.repository,
                    ruleset_id=args.ruleset_id,
                    expected_contract_sha256=args.expected_contract_sha256,
                ),
            )
            return 0
        contract = _load_validated_contract(args.contract)
        live_ruleset = load_json(args.live)
        if args.command == "preview":
            _write_output(args.output, preview_patch(contract, live_ruleset))
            return 0
        _write_output(args.output, verify_live_ruleset(contract, live_ruleset))
        return 0
    except RulesetContractError as error:
        print(f"required-summary-checks: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
