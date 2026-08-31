#!/usr/bin/env python3
"""Validate candidate-bound sibling-family review evidence."""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from scripts.workflow_pilot import reporter


SCHEMA_VERSION = 4
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
KNOWN_ACTIONS = {
    *READ_ONLY_ACTIONS,
    "comment",
    "dispatch-ci",
    "edit",
    "merge",
    "push",
    "request-review",
}
ARCHITECTURE_ACTIONS = {"decompose", "redesign", "retain-with-evidence"}
ACTOR_KINDS = {"bot", "service", "user"}
LARGE_TRIGGERS = {"changed-files", "changed-lines", "major-boundaries"}
LIMIT_CAPS = {
    "max_duration_minutes": 60,
    "max_findings_per_review": 50,
    "max_reviewed_files": 200,
    "max_siblings_per_finding": 5,
    "max_siblings_per_handoff": 250,
}
METRIC_BINDINGS = {
    "coordination_overhead": (
        "efficiency.pilot_coordination_minutes",
        "efficiency.metadata_maintenance_minutes",
    ),
    "findings_per_kloc": ("reviews.valid_findings_per_kloc",),
    "review_findings": ("reviews.valid_findings",),
    "review_rounds": ("reviews.rounds",),
    "time_to_clean_review": ("delivery.first_push_to_clean_review",),
}
BEHAVIOR_ROW_SPECS = {
    "actor-permission-bounds": {
        "production": {
            "predicate": "pre-review-required",
            "producer": "sealed-review-evidence",
        },
        "execution": {
            "executor": "review-family-validator",
            "consumer": "trusted-delivery-coordinator",
        },
        "representation": "actor-permission-action-and-bound-records",
        "stale_state_revalidation": "actor-and-candidate-identity-cross-check",
        "host_validation": "actor-permission-bound-reproducers",
    },
    "authority-causality": {
        "production": {
            "predicate": "candidate-evidence-present",
            "producer": "git-and-sealed-github-snapshot",
        },
        "execution": {
            "executor": "repository-authority-validator",
            "consumer": "review-family-validator",
        },
        "representation": "head-tree-commit-and-timestamp-identities",
        "stale_state_revalidation": "actual-head-and-ancestry-check",
        "host_validation": "authority-and-timestamp-reproducers",
    },
    "remote-review-metrics": {
        "production": {
            "predicate": "remote-review-evidence-present",
            "producer": "sealed-github-review-snapshot",
        },
        "execution": {
            "executor": "remote-review-gate",
            "consumer": "merge-gate-and-pilot-metrics",
        },
        "representation": "immutable-review-finding-and-metric-identities",
        "stale_state_revalidation": "current-head-review-binding",
        "host_validation": "remote-review-and-frozen-metric-reproducers",
    },
    "round-lifecycle": {
        "production": {
            "predicate": "change-request-round-observed",
            "producer": "sealed-review-and-disposition-events",
        },
        "execution": {
            "executor": "round-progression-validator",
            "consumer": "push-handoff-and-architecture-hold",
        },
        "representation": "ordered-review-and-disposition-event-sequence",
        "stale_state_revalidation": "held-round-sha-and-time-causality",
        "host_validation": "multi-hold-lifecycle-reproducers",
    },
    "sibling-family-expansion": {
        "production": {
            "predicate": "accepted-finding-observed",
            "producer": "sealed-finding-family",
        },
        "execution": {
            "executor": "family-sweep-validator",
            "consumer": "bounded-round-handoff",
        },
        "representation": "closed-family-member-result-identities",
        "stale_state_revalidation": "finding-review-and-result-sha-binding",
        "host_validation": "family-completeness-and-association-reproducers",
    },
}
REQUIRED_BEHAVIOR_ROWS = tuple(sorted(BEHAVIOR_ROW_SPECS))
RESULT_SOURCE_PATH = "scripts/workflow_pilot/tests/test_review_family.py"
REGISTERED_RESULT_CHECK_IDS = {"base-pinned-independent-review"}
COPILOT_ACTOR = "copilot-pull-request-reviewer"
ACTOR_LOGIN_RE = re.compile(
    r"^@?[A-Za-z0-9](?:[A-Za-z0-9_-]{0,98}[A-Za-z0-9])?(?:\[bot\])?$"
)
ACTOR_BOT_SUFFIX_RE = re.compile(r"(?:\[bot\]|[-_]bot)$", re.IGNORECASE)


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


def _expect_time(value: Any, label: str) -> tuple[str, datetime]:
    parsed = reporter.parse_time(value, label)
    assert parsed is not None
    return value, parsed


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


def normalize_actor_login(value: Any, label: str = "actor login") -> str:
    login = reporter.expect_string(value, label)
    if ACTOR_LOGIN_RE.fullmatch(login) is None:
        raise reporter.PilotDataError(f"{label} is not a valid actor login")
    normalized = login.removeprefix("@").casefold()
    while True:
        stripped = ACTOR_BOT_SUFFIX_RE.sub("", normalized)
        if stripped == normalized:
            break
        normalized = stripped
    if not normalized:
        raise reporter.PilotDataError(f"{label} has no normalized identity")
    return normalized


def _validate_trigger(value: Any) -> tuple[dict[str, list[str]], bool]:
    trigger = reporter.expect_object(value, "contract.trigger")
    reporter.expect_keys(
        trigger,
        "contract.trigger",
        ("risk_boundaries", "threshold_triggers"),
    )
    risks = _expect_string_list(
        trigger["risk_boundaries"],
        "contract.trigger.risk_boundaries",
        allowed=reporter.RISK_BOUNDARIES,
    )
    thresholds = _expect_string_list(
        trigger["threshold_triggers"],
        "contract.trigger.threshold_triggers",
        allowed=reporter.THRESHOLD_TRIGGERS,
    )
    if "none" in risks and len(risks) != 1:
        raise reporter.PilotDataError(
            "contract.trigger.risk_boundaries none must stand alone"
        )
    if "none" in thresholds and len(thresholds) != 1:
        raise reporter.PilotDataError(
            "contract.trigger.threshold_triggers none must stand alone"
        )
    if "risk-boundary" in thresholds and risks == ["none"]:
        raise reporter.PilotDataError(
            "contract.trigger risk-boundary requires a named risk"
        )
    required = risks != ["none"] or bool(set(thresholds) & LARGE_TRIGGERS)
    return {
        "risk_boundaries": sorted(risks),
        "threshold_triggers": sorted(thresholds),
    }, required


def _validate_limits(value: Any) -> dict[str, int]:
    limits = reporter.expect_object(value, "contract.limits")
    reporter.expect_keys(limits, "contract.limits", LIMIT_CAPS)
    result = {}
    for name, maximum in LIMIT_CAPS.items():
        amount = reporter.expect_int(
            limits[name], f"contract.limits.{name}", 1
        )
        if amount > maximum:
            raise reporter.PilotDataError(
                f"contract.limits.{name} exceeds bounded maximum {maximum}"
            )
        result[name] = amount
    return result


def _validate_behavior_rows(value: Any) -> list[dict[str, Any]]:
    rows = reporter.expect_list(value, "contract.behavior_rows")
    normalized = []
    row_ids = []
    for index, raw in enumerate(rows):
        label = f"contract.behavior_rows[{index}]"
        row = reporter.expect_object(raw, label)
        reporter.expect_keys(row, label, ("id", "evidence_result_ids"))
        row_id = reporter.expect_enum(
            row["id"], set(REQUIRED_BEHAVIOR_ROWS), f"{label}.id"
        )
        row_ids.append(row_id)
        evidence = reporter.expect_object(
            row["evidence_result_ids"], f"{label}.evidence_result_ids"
        )
        reporter.expect_keys(
            evidence,
            f"{label}.evidence_result_ids",
            EVIDENCE_CLASSES,
        )
        normalized.append(
            {
                "id": row_id,
                "evidence_result_ids": {
                    evidence_class: _expect_string_list(
                        evidence[evidence_class],
                        f"{label}.evidence_result_ids.{evidence_class}",
                    )
                    for evidence_class in EVIDENCE_CLASSES
                },
            }
        )
    reporter.expect_unique(row_ids, "contract.behavior_rows identities")
    if set(row_ids) != set(REQUIRED_BEHAVIOR_ROWS):
        raise reporter.PilotDataError(
            "contract.behavior_rows do not exactly cover the frozen inventory "
            f"(missing={sorted(set(REQUIRED_BEHAVIOR_ROWS) - set(row_ids))}, "
            f"extra={sorted(set(row_ids) - set(REQUIRED_BEHAVIOR_ROWS))})"
        )
    return sorted(normalized, key=lambda row: row["id"])


def _validate_sweep_shape(value: Any) -> list[dict[str, Any]]:
    sweeps = reporter.expect_list(value, "contract.family_sweeps")
    result = []
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
        normalized_siblings = []
        for sibling_index, raw_sibling in enumerate(siblings):
            sibling_label = f"{label}.siblings[{sibling_index}]"
            sibling = reporter.expect_object(raw_sibling, sibling_label)
            reporter.expect_keys(
                sibling,
                sibling_label,
                ("member", "result", "evidence_result_ids"),
            )
            normalized_siblings.append(
                {
                    "member": reporter.expect_string(
                        sibling["member"], f"{sibling_label}.member"
                    ),
                    "result": reporter.expect_enum(
                        sibling["result"],
                        SWEEP_RESULTS,
                        f"{sibling_label}.result",
                    ),
                    "evidence_result_ids": _expect_string_list(
                        sibling["evidence_result_ids"],
                        f"{sibling_label}.evidence_result_ids",
                    ),
                }
            )
        result.append(
            {
                "finding_id": finding_id,
                "siblings": normalized_siblings,
            }
        )
    reporter.expect_unique(finding_ids, "contract.family_sweeps finding IDs")
    return result


def validate_contract(raw_contract: Any) -> dict[str, Any]:
    contract = reporter.expect_object(raw_contract, "contract")
    reporter.expect_keys(
        contract,
        "contract",
        (
            "schema_version",
            "repository",
            "pull_request",
            "candidate_sha",
            "implementer_actor_id",
            "trigger",
            "limits",
            "behavior_rows",
            "family_sweeps",
        ),
    )
    version = reporter.expect_int(
        contract["schema_version"], "contract.schema_version", 1
    )
    if version != SCHEMA_VERSION:
        raise reporter.PilotDataError(
            f"contract.schema_version must be {SCHEMA_VERSION}"
        )
    trigger, required = _validate_trigger(contract["trigger"])
    return {
        "raw": contract,
        "repository": reporter.expect_string(
            contract["repository"], "contract.repository"
        ),
        "pull_request": reporter.expect_int(
            contract["pull_request"], "contract.pull_request", 1
        ),
        "candidate_sha": reporter.expect_sha(
            contract["candidate_sha"], "contract.candidate_sha"
        ),
        "implementer_actor_id": reporter.expect_string(
            contract["implementer_actor_id"],
            "contract.implementer_actor_id",
        ),
        "trigger": trigger,
        "pre_review_required": required,
        "limits": _validate_limits(contract["limits"]),
        "behavior_rows": _validate_behavior_rows(contract["behavior_rows"]),
        "family_sweeps": _validate_sweep_shape(contract["family_sweeps"]),
    }


def _validate_actor_records(value: Any) -> dict[str, dict[str, str]]:
    records = reporter.expect_list(value, "evidence.actors")
    actors = {}
    normalized_logins = []
    for index, raw in enumerate(records):
        label = f"evidence.actors[{index}]"
        actor = reporter.expect_object(raw, label)
        reporter.expect_keys(actor, label, ("id", "login", "kind"))
        actor_id = reporter.expect_string(actor["id"], f"{label}.id")
        if actor_id in actors:
            raise reporter.PilotDataError(f"duplicate actor ID {actor_id!r}")
        login = reporter.expect_string(actor["login"], f"{label}.login")
        normalized = normalize_actor_login(login, f"{label}.login")
        normalized_logins.append(normalized)
        actors[actor_id] = {
            "id": actor_id,
            "login": login,
            "normalized_login": normalized,
            "kind": reporter.expect_enum(
                actor["kind"], ACTOR_KINDS, f"{label}.kind"
            ),
        }
    reporter.expect_unique(normalized_logins, "evidence actor identities")
    return actors


def _validate_reviewed_files(
    value: Any,
    label: str,
) -> list[str]:
    records = reporter.expect_list(value, label)
    result = [
        _validate_path(path, f"{label}[{index}]")
        for index, path in enumerate(records)
    ]
    reporter.expect_unique(result, label)
    return result


def _validate_pre_review_records(value: Any) -> list[dict[str, Any]]:
    records = reporter.expect_list(value, "evidence.pre_reviews")
    result = []
    identities = []
    for index, raw in enumerate(records):
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
                "permissions",
                "actions",
                "finding_ids",
                "reviewed_files",
            ),
        )
        review_id = reporter.expect_string(review["id"], f"{label}.id")
        identities.append(review_id)
        started_at, started = _expect_time(
            review["started_at"], f"{label}.started_at"
        )
        completed_at, completed = _expect_time(
            review["completed_at"], f"{label}.completed_at"
        )
        if completed < started:
            raise reporter.PilotDataError(
                f"{label} completed before it started"
            )
        actions = reporter.expect_list(review["actions"], f"{label}.actions")
        normalized_actions = []
        action_ids = []
        action_times = []
        for action_index, raw_action in enumerate(actions):
            action_label = f"{label}.actions[{action_index}]"
            action = reporter.expect_object(raw_action, action_label)
            reporter.expect_keys(
                action, action_label, ("id", "kind", "occurred_at")
            )
            action_id = reporter.expect_string(
                action["id"], f"{action_label}.id"
            )
            action_ids.append(action_id)
            occurred_at, occurred = _expect_time(
                action["occurred_at"], f"{action_label}.occurred_at"
            )
            action_times.append(occurred)
            normalized_actions.append(
                {
                    "id": action_id,
                    "kind": reporter.expect_enum(
                        action["kind"],
                        KNOWN_ACTIONS,
                        f"{action_label}.kind",
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
                "permissions": _expect_string_list(
                    review["permissions"], f"{label}.permissions"
                ),
                "actions": normalized_actions,
                "finding_ids": _expect_string_list(
                    review["finding_ids"],
                    f"{label}.finding_ids",
                    nonempty=False,
                ),
                "reviewed_files": _validate_reviewed_files(
                    review["reviewed_files"], f"{label}.reviewed_files"
                ),
            }
        )
    reporter.expect_unique(identities, "evidence.pre_reviews IDs")
    return result


def _validate_remote_review_records(value: Any) -> list[dict[str, Any]]:
    records = reporter.expect_list(value, "evidence.remote_reviews")
    result = []
    review_ids = []
    node_ids = []
    previous_time = None
    for index, raw in enumerate(records):
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
                "body_has_findings",
                "outcome",
                "finding_ids",
            ),
        )
        review_id = reporter.expect_int(review["id"], f"{label}.id", 1)
        node_id = reporter.expect_string(review["node_id"], f"{label}.node_id")
        review_ids.append(review_id)
        node_ids.append(node_id)
        round_number = reporter.expect_int(
            review["round"], f"{label}.round", 1
        )
        if round_number != index + 1:
            raise reporter.PilotDataError(
                "remote review rounds must be consecutive from 1"
            )
        submitted_at, submitted = _expect_time(
            review["submitted_at"], f"{label}.submitted_at"
        )
        if previous_time is not None and submitted <= previous_time:
            raise reporter.PilotDataError(
                "remote review timestamps must be strictly chronological"
            )
        previous_time = submitted
        state = reporter.expect_enum(
            review["state"],
            {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"},
            f"{label}.state",
        )
        body = reporter.expect_string(
            review["body"], f"{label}.body", allow_empty=True
        )
        body_has_findings = reporter.expect_bool(
            review["body_has_findings"], f"{label}.body_has_findings"
        )
        if body_has_findings != (body.strip() not in {"", "No issues found."}):
            raise reporter.PilotDataError(
                f"{label}.body_has_findings contradicts review body"
            )
        outcome = reporter.expect_enum(
            review["outcome"], REMOTE_OUTCOMES, f"{label}.outcome"
        )
        result.append(
            {
                "id": review_id,
                "node_id": node_id,
                "round": round_number,
                "reviewer_actor_id": reporter.expect_string(
                    review["reviewer_actor_id"],
                    f"{label}.reviewer_actor_id",
                ),
                "candidate_sha": reporter.expect_sha(
                    review["candidate_sha"], f"{label}.candidate_sha"
                ),
                "submitted_at": submitted_at,
                "state": state,
                "body": body,
                "body_has_findings": body_has_findings,
                "outcome": outcome,
                "finding_ids": _expect_string_list(
                    review["finding_ids"],
                    f"{label}.finding_ids",
                    nonempty=False,
                ),
            }
        )
    reporter.expect_unique(review_ids, "evidence.remote_reviews numeric IDs")
    reporter.expect_unique(node_ids, "evidence.remote_reviews node IDs")
    return result


def _validate_finding_records(value: Any) -> dict[str, dict[str, Any]]:
    records = reporter.expect_list(value, "evidence.findings")
    findings = {}
    for index, raw in enumerate(records):
        label = f"evidence.findings[{index}]"
        finding = reporter.expect_object(raw, label)
        reporter.expect_keys(
            finding,
            label,
            (
                "node_id",
                "review_id",
                "candidate_sha",
                "created_at",
                "family",
            ),
        )
        node_id = reporter.expect_string(
            finding["node_id"], f"{label}.node_id"
        )
        if node_id in findings:
            raise reporter.PilotDataError(
                f"duplicate finding node ID {node_id!r}"
            )
        created_at, _ = _expect_time(
            finding["created_at"], f"{label}.created_at"
        )
        findings[node_id] = {
            "node_id": node_id,
            "review_id": reporter.expect_string(
                finding["review_id"], f"{label}.review_id"
            ),
            "candidate_sha": reporter.expect_sha(
                finding["candidate_sha"], f"{label}.candidate_sha"
            ),
            "created_at": created_at,
            "family": reporter.expect_enum(
                finding["family"], set(FAMILY_MEMBERS), f"{label}.family"
            ),
        }
    return findings


def _validate_thread_records(value: Any) -> list[dict[str, Any]]:
    records = reporter.expect_list(value, "evidence.threads")
    result = []
    thread_ids = []
    finding_ids = []
    for index, raw in enumerate(records):
        label = f"evidence.threads[{index}]"
        thread = reporter.expect_object(raw, label)
        reporter.expect_keys(
            thread, label, ("node_id", "finding_id", "is_resolved")
        )
        node_id = reporter.expect_string(
            thread["node_id"], f"{label}.node_id"
        )
        finding_id = reporter.expect_string(
            thread["finding_id"], f"{label}.finding_id"
        )
        thread_ids.append(node_id)
        finding_ids.append(finding_id)
        result.append(
            {
                "node_id": node_id,
                "finding_id": finding_id,
                "is_resolved": reporter.expect_bool(
                    thread["is_resolved"], f"{label}.is_resolved"
                ),
            }
        )
    reporter.expect_unique(thread_ids, "evidence thread node IDs")
    reporter.expect_unique(finding_ids, "evidence thread finding IDs")
    return result


def _validate_candidate_advances(value: Any) -> list[dict[str, Any]]:
    records = reporter.expect_list(value, "evidence.candidate_advances")
    if not records:
        raise reporter.PilotDataError(
            "evidence.candidate_advances must not be empty"
        )
    result = []
    node_ids = []
    previous = None
    for index, raw in enumerate(records):
        label = f"evidence.candidate_advances[{index}]"
        advance = reporter.expect_object(raw, label)
        reporter.expect_keys(
            advance,
            label,
            ("node_id", "candidate_sha", "pushed_at", "kind"),
        )
        node_id = reporter.expect_string(
            advance["node_id"], f"{label}.node_id"
        )
        pushed_at, pushed = _expect_time(
            advance["pushed_at"], f"{label}.pushed_at"
        )
        if previous is not None and pushed <= previous:
            raise reporter.PilotDataError(
                "candidate advances are not strictly chronological"
            )
        previous = pushed
        node_ids.append(node_id)
        result.append(
            {
                "node_id": node_id,
                "candidate_sha": reporter.expect_sha(
                    advance["candidate_sha"], f"{label}.candidate_sha"
                ),
                "pushed_at": pushed_at,
                "kind": reporter.expect_enum(
                    advance["kind"],
                    {"commit-push", "force-push", "synchronize"},
                    f"{label}.kind",
                ),
            }
        )
    reporter.expect_unique(node_ids, "candidate advance node IDs")
    return result


def _validate_execution_receipts(value: Any) -> list[dict[str, Any]]:
    from scripts.workflow_pilot import github_review

    records = reporter.expect_list(value, "evidence.execution_receipts")
    result = []
    receipt_ids = []
    seals = []
    for index, raw in enumerate(records):
        label = f"evidence.execution_receipts[{index}]"
        receipt = reporter.expect_object(raw, label)
        reporter.expect_keys(
            receipt,
            label,
            (
                "id",
                "check_id",
                "base_sha",
                "base_tree",
                "candidate_sha",
                "candidate_tree",
                "checker_path",
                "checker_blob_oid",
                "argv",
                "changed_files",
                "github_finding_ids",
                "review_report_sha256",
                "read_only",
                "pre_clean",
                "post_clean",
                "started_at",
                "completed_at",
                "exit_code",
                "result",
                "output_sha256",
                "seal",
            ),
        )
        receipt_id = reporter.expect_string(receipt["id"], f"{label}.id")
        check_id = reporter.expect_enum(
            receipt["check_id"],
            REGISTERED_RESULT_CHECK_IDS,
            f"{label}.check_id",
        )
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
            receipt["result"], {"fail", "pass"}, f"{label}.result"
        )
        if (exit_code == 0) != (result_value == "pass"):
            raise reporter.PilotDataError(
                f"{label} exit code contradicts result"
            )
        output_sha256 = reporter.expect_string(
            receipt["output_sha256"], f"{label}.output_sha256"
        )
        seal = reporter.expect_string(receipt["seal"], f"{label}.seal")
        if reporter.SHA256_RE.fullmatch(output_sha256) is None:
            raise reporter.PilotDataError(
                f"{label}.output_sha256 must be a lowercase SHA-256"
            )
        if reporter.SHA256_RE.fullmatch(seal) is None:
            raise reporter.PilotDataError(
                f"{label}.seal must be a lowercase SHA-256"
            )
        if seal != github_review.receipt_seal(receipt):
            raise reporter.PilotDataError(
                f"{label} does not match its execution seal"
            )
        review_report_sha256 = reporter.expect_string(
            receipt["review_report_sha256"],
            f"{label}.review_report_sha256",
        )
        if reporter.SHA256_RE.fullmatch(review_report_sha256) is None:
            raise reporter.PilotDataError(
                f"{label}.review_report_sha256 must be a lowercase SHA-256"
            )
        receipt_ids.append(receipt_id)
        seals.append(seal)
        result.append(
            {
                "id": receipt_id,
                "check_id": check_id,
                "base_sha": reporter.expect_sha(
                    receipt["base_sha"], f"{label}.base_sha"
                ),
                "base_tree": reporter.expect_sha(
                    receipt["base_tree"], f"{label}.base_tree"
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
                    receipt["checker_blob_oid"],
                    f"{label}.checker_blob_oid",
                ),
                "argv": _expect_string_list(
                    receipt["argv"], f"{label}.argv"
                ),
                "changed_files": _expect_string_list(
                    receipt["changed_files"], f"{label}.changed_files"
                ),
                "github_finding_ids": _expect_string_list(
                    receipt["github_finding_ids"],
                    f"{label}.github_finding_ids",
                    nonempty=False,
                ),
                "review_report_sha256": review_report_sha256,
                "read_only": reporter.expect_bool(
                    receipt["read_only"], f"{label}.read_only"
                ),
                "pre_clean": reporter.expect_bool(
                    receipt["pre_clean"], f"{label}.pre_clean"
                ),
                "post_clean": reporter.expect_bool(
                    receipt["post_clean"], f"{label}.post_clean"
                ),
                "started_at": started_at,
                "completed_at": completed_at,
                "exit_code": exit_code,
                "result": result_value,
                "output_sha256": output_sha256,
                "seal": seal,
            }
        )
    reporter.expect_unique(receipt_ids, "execution receipt IDs")
    reporter.expect_unique(seals, "execution receipt seals")
    return result


def _validate_disposition_records(value: Any) -> list[dict[str, Any]]:
    records = reporter.expect_list(
        value, "evidence.architecture_dispositions"
    )
    result = []
    event_ids = []
    held_rounds = []
    event_times = []
    for index, raw in enumerate(records):
        label = f"evidence.architecture_dispositions[{index}]"
        event = reporter.expect_object(raw, label)
        reporter.expect_keys(
            event,
            label,
            (
                "node_id",
                "held_round",
                "candidate_sha",
                "actor_id",
                "action",
                "occurred_at",
            ),
        )
        event_id = reporter.expect_string(
            event["node_id"], f"{label}.node_id"
        )
        held_round = reporter.expect_int(
            event["held_round"], f"{label}.held_round", 3
        )
        occurred_at, occurred = _expect_time(
            event["occurred_at"], f"{label}.occurred_at"
        )
        event_ids.append(event_id)
        held_rounds.append(held_round)
        event_times.append(occurred)
        result.append(
            {
                "node_id": event_id,
                "held_round": held_round,
                "candidate_sha": reporter.expect_sha(
                    event["candidate_sha"], f"{label}.candidate_sha"
                ),
                "actor_id": reporter.expect_string(
                    event["actor_id"], f"{label}.actor_id"
                ),
                "action": reporter.expect_enum(
                    event["action"],
                    ARCHITECTURE_ACTIONS,
                    f"{label}.action",
                ),
                "occurred_at": occurred_at,
            }
        )
    reporter.expect_unique(
        event_ids, "architecture disposition node IDs"
    )
    reporter.expect_unique(held_rounds, "architecture disposition held rounds")
    if (
        event_times != sorted(event_times)
        or len(set(event_times)) != len(event_times)
        or held_rounds != sorted(held_rounds)
    ):
        raise reporter.PilotDataError(
            "architecture dispositions are not strictly ordered"
        )
    return result


def _validate_result_manifest(value: Any) -> dict[str, dict[str, Any]]:
    records = reporter.expect_list(value, "evidence.result_manifest")
    result = {}
    for index, raw in enumerate(records):
        label = f"evidence.result_manifest[{index}]"
        record = reporter.expect_object(raw, label)
        reporter.expect_keys(
            record,
            label,
            (
                "id",
                "candidate_sha",
                "row_id",
                "evidence_class",
                "family",
                "member",
                "assertion_id",
            ),
        )
        result_id = reporter.expect_string(record["id"], f"{label}.id")
        if result_id in result:
            raise reporter.PilotDataError(
                f"duplicate result ID {result_id!r}"
            )
        family = record["family"]
        if family is not None:
            family = reporter.expect_enum(
                family, set(FAMILY_MEMBERS), f"{label}.family"
            )
        member = record["member"]
        if member is not None:
            member = reporter.expect_string(member, f"{label}.member")
        result[result_id] = {
            "id": result_id,
            "candidate_sha": reporter.expect_sha(
                record["candidate_sha"], f"{label}.candidate_sha"
            ),
            "row_id": reporter.expect_enum(
                record["row_id"],
                set(REQUIRED_BEHAVIOR_ROWS),
                f"{label}.row_id",
            ),
            "evidence_class": reporter.expect_enum(
                record["evidence_class"],
                set(EVIDENCE_CLASSES),
                f"{label}.evidence_class",
            ),
            "family": family,
            "member": member,
            "assertion_id": reporter.expect_string(
                record["assertion_id"], f"{label}.assertion_id"
            ),
        }
    return result


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
            "pull_request",
            "result_source_path",
            "actors",
            "trusted_disposition_actor_ids",
            "pre_reviews",
            "remote_reviews",
            "findings",
            "threads",
            "candidate_advances",
            "architecture_dispositions",
            "execution_receipts",
            "result_manifest",
        ),
    )
    version = reporter.expect_int(
        evidence["schema_version"], "evidence.schema_version", 1
    )
    if version != SCHEMA_VERSION:
        raise reporter.PilotDataError(
            f"evidence.schema_version must be {SCHEMA_VERSION}"
        )
    captured_at, captured = _expect_time(
        evidence["captured_at"], "evidence.captured_at"
    )
    source = reporter.expect_object(evidence["source"], "evidence.source")
    reporter.expect_keys(
        source, "evidence.source", ("kind", "complete")
    )
    if source["kind"] not in {
        "authenticated-receipt",
        "live-gh-api",
        "offline-transform-fixture",
    }:
        raise reporter.PilotDataError(
            "evidence.source.kind is not the canonical review snapshot"
        )
    if reporter.expect_bool(
        source["complete"], "evidence.source.complete"
    ) is not True:
        raise reporter.PilotDataError(
            "canonical review evidence source must be complete"
        )
    candidate = reporter.expect_object(evidence["candidate"], "evidence.candidate")
    reporter.expect_keys(candidate, "evidence.candidate", ("sha",))
    pull_request = reporter.expect_object(
        evidence["pull_request"], "evidence.pull_request"
    )
    reporter.expect_keys(
        pull_request,
        "evidence.pull_request",
        ("number", "node_id", "created_at", "author_actor_id"),
    )
    created_at, _ = _expect_time(
        pull_request["created_at"], "evidence.pull_request.created_at"
    )
    result_source_path = _validate_path(
        evidence["result_source_path"], "evidence.result_source_path"
    )
    if result_source_path != RESULT_SOURCE_PATH:
        raise reporter.PilotDataError(
            "evidence result source is unrelated to the frozen result inventory"
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
            ),
        },
        "result_source_path": result_source_path,
        "pull_request": {
            "number": reporter.expect_int(
                pull_request["number"], "evidence.pull_request.number", 1
            ),
            "node_id": reporter.expect_string(
                pull_request["node_id"], "evidence.pull_request.node_id"
            ),
            "created_at": created_at,
            "author_actor_id": reporter.expect_string(
                pull_request["author_actor_id"],
                "evidence.pull_request.author_actor_id",
            ),
        },
        "actors": _validate_actor_records(evidence["actors"]),
        "trusted_disposition_actor_ids": _expect_string_list(
            evidence["trusted_disposition_actor_ids"],
            "evidence.trusted_disposition_actor_ids",
        ),
        "pre_reviews": _validate_pre_review_records(evidence["pre_reviews"]),
        "remote_reviews": _validate_remote_review_records(
            evidence["remote_reviews"]
        ),
        "findings": _validate_finding_records(evidence["findings"]),
        "threads": _validate_thread_records(evidence["threads"]),
        "candidate_advances": _validate_candidate_advances(
            evidence["candidate_advances"]
        ),
        "architecture_dispositions": _validate_disposition_records(
            evidence["architecture_dispositions"]
        ),
        "execution_receipts": _validate_execution_receipts(
            evidence["execution_receipts"]
        ),
        "result_manifest": _validate_result_manifest(
            evidence["result_manifest"]
        ),
    }

def _candidate_blob_oid(
    repository_root: Path,
    candidate_sha: str,
    path: str,
) -> str:
    raw = reporter.run_git(
        repository_root,
        "ls-tree",
        "-z",
        "--full-tree",
        candidate_sha,
        "--",
        path,
    )
    records = [record for record in raw.split(b"\0") if record]
    if len(records) != 1:
        raise reporter.PilotDataError(
            f"candidate {candidate_sha} has no exact evidence path {path!r}"
        )
    try:
        metadata, actual_path = records[0].split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        decoded_path = actual_path.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise reporter.PilotDataError(
            f"candidate tree returned malformed evidence path {path!r}"
        ) from error
    if decoded_path != path or mode not in {"100644", "100755"} or kind != "blob":
        raise reporter.PilotDataError(
            f"candidate evidence path {path!r} is not an exact source blob"
        )
    return oid


def _validate_global_node_identities(evidence: dict[str, Any]) -> None:
    identities = []

    def add(domain: str, value: str) -> None:
        normalized = reporter.expect_string(
            value, f"{domain} node identity"
        ).strip().casefold()
        identities.append((normalized, domain, value))

    for actor_id in evidence["actors"]:
        add("actor", actor_id)
    add("pull-request", evidence["pull_request"]["node_id"])
    for review in evidence["pre_reviews"]:
        add("pre-review", review["id"])
        for action in review["actions"]:
            add("pre-review-action", action["id"])
    for review in evidence["remote_reviews"]:
        add("remote-review", review["node_id"])
    for finding_id in evidence["findings"]:
        add("review-finding", finding_id)
    for thread in evidence["threads"]:
        add("review-thread", thread["node_id"])
    for advance in evidence["candidate_advances"]:
        add("candidate-advance", advance["node_id"])
    for event in evidence["architecture_dispositions"]:
        add("architecture-disposition", event["node_id"])

    by_identity: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for normalized, domain, original in identities:
        by_identity[normalized].append((domain, original))
    collisions = {
        identity: records
        for identity, records in by_identity.items()
        if len(records) > 1
    }
    if collisions:
        raise reporter.PilotDataError(
            "global GitHub node identity collision: "
            + ", ".join(
                f"{identity}={records}"
                for identity, records in sorted(collisions.items())
            )
        )


def _validate_threads(
    evidence: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    threads = {thread["finding_id"]: thread for thread in evidence["threads"]}
    if set(threads) != set(evidence["findings"]):
        raise reporter.PilotDataError(
            "review threads do not exactly cover finding node IDs "
            f"(missing={sorted(set(evidence['findings']) - set(threads))}, "
            f"extra={sorted(set(threads) - set(evidence['findings']))})"
        )
    return threads


def _validate_candidate_advance_causality(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    authority: dict[str, Any],
) -> None:
    advances = evidence["candidate_advances"]
    if advances[-1]["candidate_sha"] != authority["head"]:
        raise reporter.PilotDataError(
            "candidate advance history does not terminate at actual Git HEAD"
        )
    observed_candidates = {
        review["candidate_sha"] for review in evidence["pre_reviews"]
    }
    observed_candidates.update(
        review["candidate_sha"] for review in evidence["remote_reviews"]
    )
    observed_candidates.update(
        finding["candidate_sha"]
        for finding in evidence["findings"].values()
    )
    observed_candidates.add(contract["candidate_sha"])
    advanced_candidates = {
        advance["candidate_sha"] for advance in advances
    }
    if not observed_candidates <= advanced_candidates:
        raise reporter.PilotDataError(
            "candidate advance history omits reviewed candidates "
            f"{sorted(observed_candidates - advanced_candidates)}"
        )
    for advance in advances:
        pushed = reporter.parse_time(
            advance["pushed_at"],
            f"candidate advance {advance['node_id']}.pushed_at",
        )
        assert pushed is not None
        committed = authority["commits"][advance["candidate_sha"]][
            "committed_at"
        ]
        if pushed < committed or pushed > evidence["captured"]:
            raise reporter.PilotDataError(
                f"candidate advance {advance['node_id']} violates "
                "commit/capture causality"
            )


def validate_repository_authority(
    repository_root: Path,
    expected_candidate: str,
    contract: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    root = reporter.validate_repository_root(repository_root)
    expected_candidate = reporter.expect_sha(
        expected_candidate, "expected candidate"
    )
    actual_head = (
        reporter.run_git(root, "rev-parse", "--verify", "HEAD^{commit}")
        .decode("ascii")
        .strip()
    )
    if actual_head != expected_candidate:
        raise reporter.PilotDataError(
            f"actual Git HEAD {actual_head} does not match expected candidate "
            f"{expected_candidate}"
        )
    if contract["candidate_sha"] != expected_candidate:
        raise reporter.PilotDataError(
            "contract candidate does not match expected actual Git HEAD"
        )
    if evidence["candidate"]["sha"] != expected_candidate:
        raise reporter.PilotDataError(
            "GitHub evidence candidate does not match expected actual Git HEAD"
        )
    remote = (
        reporter.run_git(root, "config", "--get", "remote.origin.url")
        .decode("utf-8")
        .strip()
    )
    repository = reporter._github_repository_from_remote(remote)
    if repository != contract["repository"] or repository != evidence["repository"]:
        raise reporter.PilotDataError(
            "contract/evidence repository does not match actual Git origin"
        )

    candidate_shas = {
        expected_candidate,
        *(review["candidate_sha"] for review in evidence["pre_reviews"]),
        *(review["candidate_sha"] for review in evidence["remote_reviews"]),
        *(finding["candidate_sha"] for finding in evidence["findings"].values()),
        *(
            event["candidate_sha"]
            for event in evidence["architecture_dispositions"]
        ),
        *(
            advance["candidate_sha"]
            for advance in evidence["candidate_advances"]
        ),
        *(
            receipt["candidate_sha"]
            for receipt in evidence["execution_receipts"]
        ),
        *(
            receipt["base_sha"]
            for receipt in evidence["execution_receipts"]
        ),
        *(
            result["candidate_sha"]
            for result in evidence["result_manifest"].values()
        ),
    }
    commit_objects = reporter._load_git_commit_objects(root, candidate_shas)
    actual_tree = (
        reporter.run_git(root, "rev-parse", f"{expected_candidate}^{{tree}}")
        .decode("ascii")
        .strip()
    )
    for candidate_sha in candidate_shas:
        try:
            reporter.run_git(
                root,
                "merge-base",
                "--is-ancestor",
                candidate_sha,
                expected_candidate,
            )
        except reporter.PilotDataError as error:
            raise reporter.PilotDataError(
                f"evidence candidate {candidate_sha} is not in actual HEAD history"
            ) from error

    blob_records = []
    for review in evidence["pre_reviews"]:
        blob_records.extend(
            (review["candidate_sha"], path)
            for path in review["reviewed_files"]
        )
    blob_records.extend(
        (result["candidate_sha"], evidence["result_source_path"])
        for result in evidence["result_manifest"].values()
    )
    for candidate_sha, path in set(blob_records):
        _candidate_blob_oid(root, candidate_sha, path)
    return {
        "repository_root": root,
        "head": actual_head,
        "commits": commit_objects,
        "tree_oid": actual_tree,
    }


def _require_actor(
    actor_id: str,
    actors: dict[str, dict[str, str]],
    label: str,
) -> dict[str, str]:
    if actor_id not in actors:
        raise reporter.PilotDataError(
            f"{label} references unknown actor {actor_id!r}"
        )
    return actors[actor_id]


def _validate_review_causality(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    authority: dict[str, Any],
) -> dict[str, Any]:
    if contract["repository"] != evidence["repository"]:
        raise reporter.PilotDataError(
            "contract repository does not match sealed GitHub evidence"
        )
    if contract["pull_request"] != evidence["pull_request"]["number"]:
        raise reporter.PilotDataError(
            "contract pull request does not match sealed GitHub evidence"
        )
    actors = evidence["actors"]
    implementer = _require_actor(
        contract["implementer_actor_id"], actors, "contract implementer"
    )
    if contract["implementer_actor_id"] != evidence["pull_request"][
        "author_actor_id"
    ]:
        raise reporter.PilotDataError(
            "contract implementer is not the authoritative pull-request author"
        )
    for actor_id in evidence["trusted_disposition_actor_ids"]:
        _require_actor(actor_id, actors, "trusted disposition owner")
    pre_reviews = evidence["pre_reviews"]
    expected_pre_reviews = 1 if contract["pre_review_required"] else 0
    if len(pre_reviews) != expected_pre_reviews:
        raise reporter.PilotDataError(
            "contract must have exactly one fresh adversarial pre-review"
            if expected_pre_reviews
            else "contract must not assign disabled pre-review ownership"
        )
    remote_reviews = evidence["remote_reviews"]
    remote_actors = []
    for review in remote_reviews:
        actor = _require_actor(
            review["reviewer_actor_id"],
            actors,
            f"remote review {review['node_id']}",
        )
        if actor["kind"] != "bot" or actor["normalized_login"] != COPILOT_ACTOR:
            raise reporter.PilotDataError(
                "remote review actor is not canonical GitHub Copilot"
            )
        remote_actors.append(actor)
        if actor["normalized_login"] == implementer["normalized_login"]:
            raise reporter.PilotDataError(
                "implementer and remote reviewer ownership overlap"
            )

    pre_owner = None
    if pre_reviews:
        review = pre_reviews[0]
        pre_owner = _require_actor(
            review["owner_actor_id"], actors, "adversarial pre-review"
        )
        role_logins = {
            implementer["normalized_login"],
            *(actor["normalized_login"] for actor in remote_actors),
        }
        if pre_owner["normalized_login"] in role_logins:
            raise reporter.PilotDataError(
                "adversarial pre-review owner overlaps implementer or remote reviewer"
            )
        if set(review["permissions"]) != set(READ_ONLY_PERMISSIONS):
            raise reporter.PilotDataError(
                "adversarial pre-review permissions must be read-only"
            )
        action_kinds = [action["kind"] for action in review["actions"]]
        if action_kinds != list(READ_ONLY_ACTIONS):
            raise reporter.PilotDataError(
                "adversarial pre-review actions must be ordered exactly "
                "read-candidate then emit-local-report"
            )
        limits = contract["limits"]
        if len(review["reviewed_files"]) > limits["max_reviewed_files"]:
            raise reporter.PilotDataError(
                "adversarial pre-review exceeds max_reviewed_files"
            )
        if len(review["finding_ids"]) > limits["max_findings_per_review"]:
            raise reporter.PilotDataError(
                "adversarial pre-review exceeds max_findings_per_review"
            )
        started = reporter.parse_time(
            review["started_at"], f"pre-review {review['id']}.started_at"
        )
        completed = reporter.parse_time(
            review["completed_at"], f"pre-review {review['id']}.completed_at"
        )
        assert started is not None and completed is not None
        if (completed - started).total_seconds() > (
            limits["max_duration_minutes"] * 60
        ):
            raise reporter.PilotDataError(
                "adversarial pre-review exceeds max_duration_minutes"
            )
        if remote_reviews:
            first_remote = reporter.parse_time(
                remote_reviews[0]["submitted_at"],
                "first remote review submitted_at",
            )
            assert first_remote is not None
            if completed >= first_remote:
                raise reporter.PilotDataError(
                    "adversarial pre-review did not complete before remote round 1"
                )
            if review["candidate_sha"] != remote_reviews[0]["candidate_sha"]:
                raise reporter.PilotDataError(
                    "adversarial pre-review is not bound to first remote candidate"
                )
        elif review["candidate_sha"] != contract["candidate_sha"]:
            raise reporter.PilotDataError(
                "adversarial pre-review is not bound to current candidate"
            )

    pr_created = reporter.parse_time(
        evidence["pull_request"]["created_at"],
        "evidence.pull_request.created_at",
    )
    captured = evidence["captured"]
    assert pr_created is not None
    for review in [*pre_reviews, *remote_reviews]:
        start_field = "started_at" if "started_at" in review else "submitted_at"
        observed = reporter.parse_time(
            review[start_field], f"review {review['id']} {start_field}"
        )
        assert observed is not None
        commit_time = authority["commits"][review["candidate_sha"]][
            "committed_at"
        ]
        if observed < commit_time or observed < pr_created or observed > captured:
            raise reporter.PilotDataError(
                f"review {review['id']} violates commit/PR/capture causality"
            )
    return {
        "implementer": implementer,
        "pre_owner": pre_owner,
        "remote_actors": remote_actors,
    }


def _validate_findings_and_sweeps(
    contract: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, int], set[str]]:
    findings = evidence["findings"]
    review_by_id = {
        review["id"]: review for review in evidence["pre_reviews"]
    }
    review_by_id.update(
        {review["node_id"]: review for review in evidence["remote_reviews"]}
    )
    claims = {}
    for review_id, review in review_by_id.items():
        limit = contract["limits"]["max_findings_per_review"]
        if len(review["finding_ids"]) > limit:
            raise reporter.PilotDataError(
                f"review {review_id!r} exceeds max_findings_per_review {limit}"
            )
        for finding_id in review["finding_ids"]:
            if finding_id in claims:
                raise reporter.PilotDataError(
                    f"finding {finding_id!r} has overlapping review ownership"
                )
            claims[finding_id] = review_id
    if set(claims) != set(findings):
        raise reporter.PilotDataError(
            "review finding claims do not exactly cover sealed findings "
            f"(missing={sorted(set(findings) - set(claims))}, "
            f"extra={sorted(set(claims) - set(findings))})"
        )
    for finding_id, finding in findings.items():
        review_id = claims[finding_id]
        review = review_by_id[review_id]
        if finding["review_id"] != review_id:
            raise reporter.PilotDataError(
                f"finding {finding_id!r} review identity does not match its owner"
            )
        if finding["candidate_sha"] != review["candidate_sha"]:
            raise reporter.PilotDataError(
                f"finding {finding_id!r} has stale candidate binding"
            )
        created = reporter.parse_time(
            finding["created_at"], f"finding {finding_id}.created_at"
        )
        assert created is not None
        if "started_at" in review:
            lower = reporter.parse_time(
                review["started_at"], f"pre-review {review_id}.started_at"
            )
            upper = reporter.parse_time(
                review["completed_at"], f"pre-review {review_id}.completed_at"
            )
        else:
            lower = reporter.parse_time(
                evidence["pull_request"]["created_at"],
                "evidence.pull_request.created_at",
            )
            upper = reporter.parse_time(
                review["submitted_at"], f"remote review {review_id}.submitted_at"
            )
        assert lower is not None and upper is not None
        if created < lower or created > upper:
            raise reporter.PilotDataError(
                f"finding {finding_id!r} violates review timestamp causality"
            )

    for review in evidence["remote_reviews"]:
        requests_changes = (
            review["state"] == "CHANGES_REQUESTED"
            or review["body_has_findings"]
            or bool(review["finding_ids"])
        )
        if review["outcome"] == "clean" and requests_changes:
            raise reporter.PilotDataError(
                f"remote review round {review['round']} is not semantically clean"
            )
        if review["outcome"] == "changes-requested" and not requests_changes:
            raise reporter.PilotDataError(
                f"remote review round {review['round']} requests changes "
                "without state/body/inline evidence"
            )

    sweeps = {}
    referenced_results = set()
    family_counts = {family: 0 for family in FAMILY_MEMBERS}
    for sweep in contract["family_sweeps"]:
        finding_id = sweep["finding_id"]
        if finding_id not in findings:
            raise reporter.PilotDataError(
                f"family sweep references unknown finding {finding_id!r}"
            )
        finding = findings[finding_id]
        family = finding["family"]
        members = [
            reporter.expect_enum(
                sibling["member"],
                set(FAMILY_MEMBERS[family]),
                f"family sweep {finding_id!r} member",
            )
            for sibling in sweep["siblings"]
        ]
        reporter.expect_unique(
            members, f"family sweep {finding_id!r} sibling members"
        )
        expected_members = set(FAMILY_MEMBERS[family])
        if set(members) != expected_members:
            raise reporter.PilotDataError(
                f"family sweep {finding_id!r} lacks exact sibling coverage "
                f"(missing={sorted(expected_members - set(members))}, "
                f"extra={sorted(set(members) - expected_members)})"
            )
        if len(members) > contract["limits"]["max_siblings_per_finding"]:
            raise reporter.PilotDataError(
                f"family sweep {finding_id!r} exceeds max_siblings_per_finding"
            )
        normalized_siblings = []
        for sibling in sweep["siblings"]:
            member = sibling["member"]
            for result_id in sibling["evidence_result_ids"]:
                if result_id in referenced_results:
                    raise reporter.PilotDataError(
                        f"result {result_id!r} is reused across evidence claims"
                    )
                referenced_results.add(result_id)
                if result_id not in evidence["result_manifest"]:
                    raise reporter.PilotDataError(
                        f"sibling evidence references unknown result {result_id!r}"
                    )
                result = evidence["result_manifest"][result_id]
                expected_assertion = f"sibling:{family}:{member}"
                if (
                    result["row_id"] != "sibling-family-expansion"
                    or result["family"] != family
                    or result["member"] != member
                    or result["assertion_id"] != expected_assertion
                    or result["candidate_sha"] != finding["candidate_sha"]
                ):
                    raise reporter.PilotDataError(
                        f"result {result_id!r} is unrelated to "
                        f"{family}/{member}/{finding['candidate_sha']}"
                    )
            normalized_siblings.append(
                {
                    **sibling,
                    "registered_check_id": "base-pinned-independent-review",
                }
            )
        sweeps[finding_id] = {
            "finding_id": finding_id,
            "candidate_sha": finding["candidate_sha"],
            "family": family,
            "siblings": sorted(
                normalized_siblings, key=lambda sibling: sibling["member"]
            ),
        }
        family_counts[family] += 1
    if set(sweeps) != set(findings):
        raise reporter.PilotDataError(
            "family sweeps do not exactly cover sealed findings "
            f"(missing={sorted(set(findings) - set(sweeps))}, "
            f"extra={sorted(set(sweeps) - set(findings))})"
        )
    for review in evidence["remote_reviews"]:
        sibling_count = sum(
            len(sweeps[finding_id]["siblings"])
            for finding_id in review["finding_ids"]
        )
        if sibling_count > contract["limits"]["max_siblings_per_handoff"]:
            raise reporter.PilotDataError(
                f"remote review round {review['round']} exceeds "
                "max_siblings_per_handoff"
            )
    return sweeps, family_counts, referenced_results


def _validate_behavior_evidence(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    referenced_results: set[str],
) -> list[dict[str, Any]]:
    rows = []
    for row in contract["behavior_rows"]:
        row_id = row["id"]
        for evidence_class, result_ids in row["evidence_result_ids"].items():
            for result_id in result_ids:
                if result_id in referenced_results:
                    raise reporter.PilotDataError(
                        f"result {result_id!r} is reused across evidence claims"
                    )
                referenced_results.add(result_id)
                if result_id not in evidence["result_manifest"]:
                    raise reporter.PilotDataError(
                        f"behavior evidence references unknown result {result_id!r}"
                    )
                result = evidence["result_manifest"][result_id]
                if (
                    result["row_id"] != row_id
                    or result["evidence_class"] != evidence_class
                    or result["family"] is not None
                    or result["member"] is not None
                    or result["assertion_id"]
                    != f"{row_id}:{evidence_class}"
                    or result["candidate_sha"] != contract["candidate_sha"]
                ):
                    raise reporter.PilotDataError(
                        f"result {result_id!r} is unrelated to behavior "
                        f"{row_id}/{evidence_class}"
                    )
        rows.append(
            {
                "id": row_id,
                **BEHAVIOR_ROW_SPECS[row_id],
                "registered_check_id": "base-pinned-independent-review",
                "evidence_result_ids": row["evidence_result_ids"],
            }
        )
    if referenced_results != set(evidence["result_manifest"]):
        raise reporter.PilotDataError(
            "result manifest is not exactly consumed by behavior/family evidence "
            f"(unused={sorted(set(evidence['result_manifest']) - referenced_results)})"
        )
    return sorted(rows, key=lambda row: row["id"])


def _validate_executable_results(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    authority: dict[str, Any],
) -> list[str]:
    from scripts.workflow_pilot import github_review

    receipts_by_check: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for receipt in evidence["execution_receipts"]:
        receipts_by_check[receipt["check_id"]].append(receipt)
    if not receipts_by_check:
        return []
    required_checks = set(REGISTERED_RESULT_CHECK_IDS)
    accepted_receipts = []
    for check_id in sorted(required_checks):
        receipts = receipts_by_check.get(check_id, [])
        if len(receipts) != 1:
            raise reporter.PilotDataError(
                f"result check {check_id!r} must have exactly one execution receipt"
            )
        receipt = receipts[0]
        if (
            receipt["candidate_sha"] != contract["candidate_sha"]
            or receipt["result"] != "pass"
            or receipt["exit_code"] != 0
            or not receipt["read_only"]
            or not receipt["pre_clean"]
            or not receipt["post_clean"]
        ):
            raise reporter.PilotDataError(
                f"result check {check_id!r} lacks a passing current-candidate receipt"
            )
        completed = reporter.parse_time(
            receipt["completed_at"],
            f"execution receipt {receipt['id']}.completed_at",
        )
        assert completed is not None
        started = reporter.parse_time(
            receipt["started_at"],
            f"execution receipt {receipt['id']}.started_at",
        )
        assert started is not None
        committed = authority["commits"][receipt["candidate_sha"]][
            "committed_at"
        ]
        if started < committed:
            raise reporter.PilotDataError(
                f"execution receipt {receipt['id']} predates its candidate"
            )
        if completed > evidence["captured"]:
            raise reporter.PilotDataError(
                f"execution receipt {receipt['id']} follows evidence capture"
            )
        accepted_receipts.append(receipt["seal"])
        root = authority["repository_root"]
        actual_base_tree = (
            reporter.run_git(
                root, "rev-parse", f"{receipt['base_sha']}^{{tree}}"
            )
            .decode("ascii")
            .strip()
        )
        actual_candidate_tree = (
            reporter.run_git(
                root, "rev-parse", f"{receipt['candidate_sha']}^{{tree}}"
            )
            .decode("ascii")
            .strip()
        )
        actual_checker_blob = (
            reporter.run_git(
                root,
                "rev-parse",
                f"{receipt['base_sha']}:{github_review.BASE_CHECKER_PATH}",
            )
            .decode("ascii")
            .strip()
        )
        changed_files = sorted(
            path.decode("utf-8")
            for path in reporter.run_git(
                root,
                "diff",
                "--name-only",
                "-z",
                f"{receipt['base_sha']}...{receipt['candidate_sha']}",
            ).split(b"\0")
            if path
        )
        expected_finding_ids = sorted(
            [
                *evidence["findings"],
                *(
                    review["node_id"]
                    for review in evidence["remote_reviews"]
                    if review["body_has_findings"]
                ),
            ]
        )
        if (
            receipt["base_tree"] != actual_base_tree
            or receipt["candidate_tree"] != actual_candidate_tree
            or receipt["checker_path"] != github_review.BASE_CHECKER_PATH
            or receipt["checker_blob_oid"] != actual_checker_blob
            or receipt["argv"] != list(github_review.BASE_CHECKER_ARGV)
            or receipt["changed_files"] != changed_files
            or receipt["github_finding_ids"]
            != expected_finding_ids
        ):
            raise reporter.PilotDataError(
                f"result check {check_id!r} receipt does not match "
                "base/candidate/checker/diff/finding authority"
            )
    extra_checks = set(receipts_by_check) - required_checks
    if extra_checks:
        raise reporter.PilotDataError(
            f"execution receipts contain unrelated checks {sorted(extra_checks)}"
        )
    return accepted_receipts


def _consume_disposition(
    event: dict[str, Any],
    hold: dict[str, Any],
    evidence: dict[str, Any],
    next_review: dict[str, Any] | None,
    actors: dict[str, dict[str, str]],
    pre_owner_id: str | None,
) -> None:
    if (
        event["held_round"] != hold["round"]
        or event["candidate_sha"] != hold["candidate_sha"]
    ):
        raise reporter.PilotDataError(
            "architecture disposition does not match the exact held round/SHA"
        )
    event_id = event["node_id"]
    _require_actor(event["actor_id"], actors, f"disposition {event_id}")
    if event["actor_id"] not in evidence["trusted_disposition_actor_ids"]:
        raise reporter.PilotDataError(
            "architecture disposition actor is not repository owner/trusted "
            "coordinator"
        )
    if event["actor_id"] == pre_owner_id:
        raise reporter.PilotDataError(
            "read-only pre-review owner cannot dispose architecture holds"
        )
    occurred = reporter.parse_time(
        event["occurred_at"], f"disposition {event_id}.occurred_at"
    )
    held_at = reporter.parse_time(
        hold["submitted_at"], f"held review {hold['round']}.submitted_at"
    )
    assert occurred is not None and held_at is not None
    if occurred <= held_at:
        raise reporter.PilotDataError(
            "architecture disposition does not follow its held review"
        )
    premature_pushes = [
        advance["node_id"]
        for advance in evidence["candidate_advances"]
        if held_at
        < reporter.parse_time(
            advance["pushed_at"],
            f"candidate advance {advance['node_id']}.pushed_at",
        )
        < occurred
    ]
    if premature_pushes:
        raise reporter.PilotDataError(
            "candidate push occurred before required architecture disposition: "
            + ", ".join(premature_pushes)
        )
    if next_review is not None:
        next_at = reporter.parse_time(
            next_review["submitted_at"],
            f"remote review {next_review['round']}.submitted_at",
        )
        assert next_at is not None
        if occurred >= next_at:
            raise reporter.PilotDataError(
                "architecture disposition does not precede the next review"
            )


def _progress_rounds(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    sweeps: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    reviews = evidence["remote_reviews"]
    dispositions = evidence["architecture_dispositions"]
    disposition_index = 0
    consumed = []
    consecutive = 0
    pending_hold = None
    handoffs = []
    pre_owner_id = (
        evidence["pre_reviews"][0]["owner_actor_id"]
        if evidence["pre_reviews"]
        else None
    )
    for index, review in enumerate(reviews):
        if pending_hold is not None:
            if disposition_index >= len(dispositions):
                raise reporter.PilotDataError(
                    "remote review continued before architecture hold disposition"
                )
            event = dispositions[disposition_index]
            _consume_disposition(
                event,
                pending_hold,
                evidence,
                review,
                evidence["actors"],
                pre_owner_id,
            )
            consumed.append(event["node_id"])
            disposition_index += 1
            pending_hold = None
            consecutive = 0

        if review["outcome"] == "clean":
            consecutive = 0
            continue
        consecutive += 1
        if consecutive <= 2:
            finding_handoffs = [
                sweeps[finding_id] for finding_id in review["finding_ids"]
            ]
            sibling_count = sum(
                len(handoff["siblings"]) for handoff in finding_handoffs
            )
            handoffs.append(
                {
                    "review_round": review["round"],
                    "consecutive_change_request": consecutive,
                    "candidate_sha": review["candidate_sha"],
                    "finding_handoffs": finding_handoffs,
                    "bounds": {
                        "findings": len(finding_handoffs),
                        "families": len(
                            {handoff["family"] for handoff in finding_handoffs}
                        ),
                        "siblings": sibling_count,
                    },
                }
            )
        else:
            pending_hold = {
                "round": review["round"],
                "candidate_sha": review["candidate_sha"],
                "submitted_at": review["submitted_at"],
                "reason": "third-consecutive-change-request",
            }

    if pending_hold is not None and disposition_index < len(dispositions):
        event = dispositions[disposition_index]
        _consume_disposition(
            event,
            pending_hold,
            evidence,
            None,
            evidence["actors"],
            pre_owner_id,
        )
        consumed.append(event["node_id"])
        disposition_index += 1
        pending_hold = None
    if pending_hold is not None:
        held_at = reporter.parse_time(
            pending_hold["submitted_at"],
            f"held review {pending_hold['round']}.submitted_at",
        )
        assert held_at is not None
        premature = [
            advance["node_id"]
            for advance in evidence["candidate_advances"]
            if reporter.parse_time(
                advance["pushed_at"],
                f"candidate advance {advance['node_id']}.pushed_at",
            )
            > held_at
        ]
        if premature:
            raise reporter.PilotDataError(
                "candidate push occurred while architecture hold was unresolved: "
                + ", ".join(premature)
            )
    if disposition_index != len(dispositions):
        raise reporter.PilotDataError(
            "architecture disposition is extra, reused, or not causal"
        )
    return handoffs, pending_hold, consumed


def _validate_global_timestamps(
    evidence: dict[str, Any],
) -> None:
    captured = evidence["captured"]
    for review in evidence["pre_reviews"]:
        completed = reporter.parse_time(
            review["completed_at"], f"pre-review {review['id']}.completed_at"
        )
        assert completed is not None
        if completed > captured:
            raise reporter.PilotDataError(
                f"pre-review {review['id']} follows evidence capture"
            )
    for review in evidence["remote_reviews"]:
        submitted = reporter.parse_time(
            review["submitted_at"], f"remote review {review['node_id']}.submitted_at"
        )
        assert submitted is not None
        if submitted > captured:
            raise reporter.PilotDataError(
                f"remote review {review['node_id']} follows evidence capture"
            )
    for finding in evidence["findings"].values():
        created = reporter.parse_time(
            finding["created_at"], f"finding {finding['node_id']}.created_at"
        )
        assert created is not None
        if created > captured:
            raise reporter.PilotDataError(
                f"finding {finding['node_id']} follows evidence capture"
            )
    for event in evidence["architecture_dispositions"]:
        occurred = reporter.parse_time(
            event["occurred_at"],
            f"disposition {event['node_id']}.occurred_at",
        )
        assert occurred is not None
        if occurred > captured:
            raise reporter.PilotDataError(
                f"architecture disposition {event['node_id']} "
                "follows evidence capture"
            )
    for advance in evidence["candidate_advances"]:
        pushed = reporter.parse_time(
            advance["pushed_at"],
            f"candidate advance {advance['node_id']}.pushed_at",
        )
        assert pushed is not None
        if pushed > captured:
            raise reporter.PilotDataError(
                f"candidate advance {advance['node_id']} follows evidence capture"
            )


def build_report(
    raw_contract: Any,
    evidence_input: Any,
    repository_root: Path,
    expected_candidate: str,
) -> dict[str, Any]:
    """Validate immutable evidence structurally without granting authority."""
    if isinstance(evidence_input, bytes):
        try:
            evidence_text = evidence_input.decode("utf-8")
        except UnicodeDecodeError as error:
            raise reporter.PilotDataError(
                "immutable evidence bytes are not UTF-8"
            ) from error
        raw_evidence = reporter.parse_json(
            evidence_text, "immutable evidence bytes"
        )
    else:
        raw_evidence = evidence_input
    contract = validate_contract(raw_contract)
    evidence = validate_evidence(raw_evidence)
    authority = validate_repository_authority(
        repository_root,
        expected_candidate,
        contract,
        evidence,
    )
    _validate_global_node_identities(evidence)
    _validate_global_timestamps(evidence)
    threads = _validate_threads(evidence)
    _validate_candidate_advance_causality(contract, evidence, authority)
    actors = _validate_review_causality(contract, evidence, authority)
    sweeps, family_counts, referenced_results = _validate_findings_and_sweeps(
        contract, evidence
    )
    behavior_rows = _validate_behavior_evidence(
        contract, evidence, referenced_results
    )
    execution_receipt_seals = _validate_executable_results(
        contract, evidence, authority
    )
    handoffs, architecture_hold, consumed_dispositions = _progress_rounds(
        contract, evidence, sweeps
    )

    latest_review = (
        evidence["remote_reviews"][-1] if evidence["remote_reviews"] else None
    )
    current_candidate_reviewed = (
        latest_review is not None
        and latest_review["candidate_sha"] == authority["head"]
    )
    current_candidate_clean = (
        current_candidate_reviewed
        and latest_review["outcome"] == "clean"
        and not latest_review["finding_ids"]
    )
    unresolved_findings = sum(
        not thread["is_resolved"] for thread in threads.values()
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            "repository": contract["repository"],
            "pull_request": contract["pull_request"],
            "pull_request_node_id": evidence["pull_request"]["node_id"],
            "candidate_sha": authority["head"],
            "candidate_tree_oid": authority["tree_oid"],
        },
        "provenance": {
            "source": evidence["source"]["kind"],
            "authoritative": False,
            "live_authoritative": False,
            "authenticated_receipt": False,
            "executable_evidence_trusted": False,
            "execution_receipt_seals": execution_receipt_seals,
        },
        "trigger": {
            **contract["trigger"],
            "adversarial_pre_review_required": contract["pre_review_required"],
        },
        "limits": contract["limits"],
        "actors": {
            "implementer": actors["implementer"]["id"],
            "pre_reviewer": (
                actors["pre_owner"]["id"] if actors["pre_owner"] else None
            ),
            "remote_reviewers": sorted(
                {actor["id"] for actor in actors["remote_actors"]}
            ),
        },
        "behavior_rows": behavior_rows,
        "families": {
            family: list(members)
            for family, members in FAMILY_MEMBERS.items()
        },
        "findings": {
            "count": len(evidence["findings"]),
            "current_unresolved": unresolved_findings,
            "by_family": family_counts,
            "handoffs": [sweeps[finding_id] for finding_id in sorted(sweeps)],
        },
        "round_handoffs": handoffs,
        "architecture_hold": {
            "required": architecture_hold is not None,
            "record": (
                {
                    "round": architecture_hold["round"],
                    "candidate_sha": architecture_hold["candidate_sha"],
                    "reason": architecture_hold["reason"],
                }
                if architecture_hold
                else None
            ),
            "consumed_disposition_ids": consumed_dispositions,
        },
        "gates": {
            "push_allowed": False,
            "trusted_push_allowed": False,
            "remote_copilot_review_required": True,
            "current_candidate_reviewed": current_candidate_reviewed,
            "current_candidate_clean": current_candidate_clean,
            "merge_allowed": False,
        },
        "structural_eligibility": {
            "push": architecture_hold is None,
            "merge": bool(
                current_candidate_clean
                and unresolved_findings == 0
                and architecture_hold is None
            ),
        },
        "metric_bindings": {
            metric: list(paths)
            for metric, paths in METRIC_BINDINGS.items()
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate sealed sibling-family evidence against an exact Git HEAD "
            "and emit canonical JSON."
        )
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--expected-candidate", required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        contract = reporter.load_json(args.contract)
        evidence = args.evidence.read_bytes()
        report = build_report(
            contract,
            evidence,
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
