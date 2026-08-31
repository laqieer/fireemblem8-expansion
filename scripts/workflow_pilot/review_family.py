#!/usr/bin/env python3
"""Validate and report bounded sibling-family review evidence."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.workflow_pilot import reporter


SCHEMA_VERSION = 1
FAMILY_MEMBERS = {
    "action": ("actions", "items", "targets"),
    "generated": ("owners", "outputs", "consumers", "drift-checks"),
    "lifecycle": ("entries", "preservation", "resets", "terminals"),
    "resource": ("enabled", "disabled"),
    "wire": ("producers", "consumers", "validators", "replay", "stale-bindings"),
}
EVIDENCE_KINDS = {"host-test", "module-output", "runtime-contract"}
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
LARGE_TRIGGERS = {"changed-files", "changed-lines", "major-boundaries"}
PRE_REVIEW_LIMITS = {
    "max_files": 200,
    "max_findings": 50,
    "max_minutes": 60,
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


def _validate_source_path(value: Any, label: str) -> str:
    source = reporter.expect_string(value, label)
    path = PurePosixPath(source)
    if path.is_absolute() or ".." in path.parts or source != path.as_posix():
        raise reporter.PilotDataError(
            f"{label} must be a normalized repository-relative path"
        )
    return source


def _validate_evidence(value: Any, label: str) -> list[dict[str, str]]:
    records = reporter.expect_list(value, label)
    if not records:
        raise reporter.PilotDataError(f"{label} must not be empty")
    normalized = []
    identities = []
    for index, raw in enumerate(records):
        record_label = f"{label}[{index}]"
        record = reporter.expect_object(raw, record_label)
        reporter.expect_keys(
            record,
            record_label,
            ("kind", "source", "assertion"),
        )
        kind = reporter.expect_enum(
            record["kind"], EVIDENCE_KINDS, f"{record_label}.kind"
        )
        source = _validate_source_path(
            record["source"], f"{record_label}.source"
        )
        assertion = reporter.expect_string(
            record["assertion"], f"{record_label}.assertion"
        )
        identity = (kind, source, assertion)
        identities.append(identity)
        normalized.append(
            {"kind": kind, "source": source, "assertion": assertion}
        )
    reporter.expect_unique(identities, label)
    return sorted(
        normalized,
        key=lambda record: (
            record["kind"],
            record["source"],
            record["assertion"],
        ),
    )


def _validate_behavior_rows(value: Any) -> list[dict[str, Any]]:
    rows = reporter.expect_list(value, "contract.behavior_rows")
    if not rows:
        raise reporter.PilotDataError("contract.behavior_rows must not be empty")
    identities = []
    normalized = []
    for index, raw in enumerate(rows):
        label = f"contract.behavior_rows[{index}]"
        row = reporter.expect_object(raw, label)
        reporter.expect_keys(
            row,
            label,
            (
                "id",
                "production",
                "execution",
                "representation",
                "stale_state_revalidation",
                "host_validation",
                "evidence",
            ),
        )
        row_id = reporter.expect_string(row["id"], f"{label}.id")
        identities.append(row_id)
        production = reporter.expect_object(
            row["production"], f"{label}.production"
        )
        reporter.expect_keys(
            production,
            f"{label}.production",
            ("predicate", "producer"),
        )
        execution = reporter.expect_object(
            row["execution"], f"{label}.execution"
        )
        reporter.expect_keys(
            execution,
            f"{label}.execution",
            ("executor", "consumer"),
        )
        evidence = reporter.expect_object(row["evidence"], f"{label}.evidence")
        reporter.expect_keys(evidence, f"{label}.evidence", EVIDENCE_CLASSES)
        normalized.append(
            {
                "id": row_id,
                "production": {
                    "predicate": reporter.expect_string(
                        production["predicate"],
                        f"{label}.production.predicate",
                    ),
                    "producer": reporter.expect_string(
                        production["producer"],
                        f"{label}.production.producer",
                    ),
                },
                "execution": {
                    "executor": reporter.expect_string(
                        execution["executor"],
                        f"{label}.execution.executor",
                    ),
                    "consumer": reporter.expect_string(
                        execution["consumer"],
                        f"{label}.execution.consumer",
                    ),
                },
                "representation": reporter.expect_string(
                    row["representation"], f"{label}.representation"
                ),
                "stale_state_revalidation": reporter.expect_string(
                    row["stale_state_revalidation"],
                    f"{label}.stale_state_revalidation",
                ),
                "host_validation": reporter.expect_string(
                    row["host_validation"], f"{label}.host_validation"
                ),
                "evidence": {
                    evidence_class: _validate_evidence(
                        evidence[evidence_class],
                        f"{label}.evidence.{evidence_class}",
                    )
                    for evidence_class in EVIDENCE_CLASSES
                },
            }
        )
    reporter.expect_unique(identities, "contract.behavior_rows identities")
    return sorted(normalized, key=lambda row: row["id"])


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


def _validate_findings(value: Any) -> dict[str, dict[str, Any]]:
    findings = reporter.expect_list(value, "contract.findings")
    result = {}
    for index, raw in enumerate(findings):
        label = f"contract.findings[{index}]"
        finding = reporter.expect_object(raw, label)
        reporter.expect_keys(
            finding,
            label,
            ("id", "candidate_sha", "family"),
        )
        finding_id = reporter.expect_string(finding["id"], f"{label}.id")
        if finding_id in result:
            raise reporter.PilotDataError(
                f"duplicate review finding {finding_id!r}"
            )
        result[finding_id] = {
            "id": finding_id,
            "candidate_sha": reporter.expect_sha(
                finding["candidate_sha"], f"{label}.candidate_sha"
            ),
            "family": reporter.expect_enum(
                finding["family"], set(FAMILY_MEMBERS), f"{label}.family"
            ),
        }
    return result


def _validate_sweeps(
    value: Any,
    findings: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    sweeps = reporter.expect_list(value, "contract.family_sweeps")
    result = {}
    for index, raw in enumerate(sweeps):
        label = f"contract.family_sweeps[{index}]"
        sweep = reporter.expect_object(raw, label)
        reporter.expect_keys(
            sweep,
            label,
            ("finding_id", "candidate_sha", "siblings"),
        )
        finding_id = reporter.expect_string(
            sweep["finding_id"], f"{label}.finding_id"
        )
        if finding_id in result:
            raise reporter.PilotDataError(
                f"duplicate family sweep for finding {finding_id!r}"
            )
        if finding_id not in findings:
            raise reporter.PilotDataError(
                f"family sweep references unknown finding {finding_id!r}"
            )
        finding = findings[finding_id]
        candidate_sha = reporter.expect_sha(
            sweep["candidate_sha"], f"{label}.candidate_sha"
        )
        if candidate_sha != finding["candidate_sha"]:
            raise reporter.PilotDataError(
                f"family sweep {finding_id!r} has stale candidate binding"
            )
        siblings = reporter.expect_list(sweep["siblings"], f"{label}.siblings")
        normalized_siblings = []
        member_ids = []
        for sibling_index, raw_sibling in enumerate(siblings):
            sibling_label = f"{label}.siblings[{sibling_index}]"
            sibling = reporter.expect_object(raw_sibling, sibling_label)
            reporter.expect_keys(
                sibling,
                sibling_label,
                ("member", "result", "evidence"),
            )
            member = reporter.expect_enum(
                sibling["member"],
                set(FAMILY_MEMBERS[finding["family"]]),
                f"{sibling_label}.member",
            )
            member_ids.append(member)
            normalized_siblings.append(
                {
                    "member": member,
                    "result": reporter.expect_enum(
                        sibling["result"],
                        SWEEP_RESULTS,
                        f"{sibling_label}.result",
                    ),
                    "evidence": _validate_evidence(
                        sibling["evidence"], f"{sibling_label}.evidence"
                    ),
                }
            )
        reporter.expect_unique(member_ids, f"{label}.siblings")
        expected = set(FAMILY_MEMBERS[finding["family"]])
        actual = set(member_ids)
        if actual != expected:
            raise reporter.PilotDataError(
                f"family sweep {finding_id!r} lacks exact sibling coverage "
                f"(missing={sorted(expected - actual)}, "
                f"extra={sorted(actual - expected)})"
            )
        result[finding_id] = {
            "finding_id": finding_id,
            "candidate_sha": candidate_sha,
            "family": finding["family"],
            "siblings": sorted(
                normalized_siblings, key=lambda sibling: sibling["member"]
            ),
        }
    if set(result) != set(findings):
        raise reporter.PilotDataError(
            "family sweeps do not exactly cover accepted findings "
            f"(missing={sorted(set(findings) - set(result))}, "
            f"extra={sorted(set(result) - set(findings))})"
        )
    return result


def _validate_pre_reviews(
    value: Any,
    *,
    required: bool,
    implementer: str,
    candidate_sha: str,
    first_remote_sha: str | None,
    findings: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, set[str]]:
    reviews = reporter.expect_list(value, "contract.pre_reviews")
    expected_count = 1 if required else 0
    if len(reviews) != expected_count:
        raise reporter.PilotDataError(
            "contract must have exactly one fresh adversarial pre-review"
            if required
            else "contract must not assign overlapping pre-review ownership"
        )
    if not reviews:
        return None, set()

    review = reporter.expect_object(reviews[0], "contract.pre_reviews[0]")
    label = "contract.pre_reviews[0]"
    reporter.expect_keys(
        review,
        label,
        (
            "owner",
            "fresh",
            "candidate_sha",
            "limits",
            "permissions",
            "authorized_actions",
            "performed_actions",
            "finding_ids",
            "completed_before_remote_round",
        ),
    )
    owner = reporter.expect_string(review["owner"], f"{label}.owner")
    if owner == implementer:
        raise reporter.PilotDataError(
            "adversarial pre-review owner must be separate from implementer"
        )
    if reporter.expect_bool(review["fresh"], f"{label}.fresh") is not True:
        raise reporter.PilotDataError(
            "adversarial pre-review owner must be fresh"
        )
    bound_sha = reporter.expect_sha(
        review["candidate_sha"], f"{label}.candidate_sha"
    )
    expected_sha = first_remote_sha or candidate_sha
    if bound_sha != expected_sha:
        raise reporter.PilotDataError(
            "adversarial pre-review is not bound to the first remote candidate"
        )

    limits = reporter.expect_object(review["limits"], f"{label}.limits")
    reporter.expect_keys(limits, f"{label}.limits", PRE_REVIEW_LIMITS)
    normalized_limits = {}
    for name, maximum in PRE_REVIEW_LIMITS.items():
        amount = reporter.expect_int(
            limits[name], f"{label}.limits.{name}", 1
        )
        if amount > maximum:
            raise reporter.PilotDataError(
                f"{label}.limits.{name} exceeds bounded maximum {maximum}"
            )
        normalized_limits[name] = amount

    permissions = _expect_string_list(
        review["permissions"],
        f"{label}.permissions",
    )
    if set(permissions) != set(READ_ONLY_PERMISSIONS):
        raise reporter.PilotDataError(
            "adversarial pre-review permissions must be read-only"
        )
    authorized = _expect_string_list(
        review["authorized_actions"],
        f"{label}.authorized_actions",
        allowed=KNOWN_ACTIONS,
    )
    performed = _expect_string_list(
        review["performed_actions"],
        f"{label}.performed_actions",
        allowed=KNOWN_ACTIONS,
    )
    if set(authorized) != set(READ_ONLY_ACTIONS):
        raise reporter.PilotDataError(
            "adversarial pre-review cannot edit, push, comment, request review, "
            "dispatch CI, or merge"
        )
    if set(performed) != set(READ_ONLY_ACTIONS):
        raise reporter.PilotDataError(
            "adversarial pre-review performed actions must complete only the "
            "bounded read/report handoff"
        )

    finding_ids = set(
        _expect_string_list(
            review["finding_ids"],
            f"{label}.finding_ids",
            nonempty=False,
        )
    )
    unknown = finding_ids - set(findings)
    if unknown:
        raise reporter.PilotDataError(
            f"adversarial pre-review references unknown findings {sorted(unknown)}"
        )
    if len(finding_ids) > normalized_limits["max_findings"]:
        raise reporter.PilotDataError(
            "adversarial pre-review exceeds its finding bound"
        )
    for finding_id in finding_ids:
        if findings[finding_id]["candidate_sha"] != bound_sha:
            raise reporter.PilotDataError(
                f"pre-review finding {finding_id!r} has stale candidate binding"
            )
    completed_before = reporter.expect_int(
        review["completed_before_remote_round"],
        f"{label}.completed_before_remote_round",
        1,
    )
    if completed_before != 1:
        raise reporter.PilotDataError(
            "adversarial pre-review must complete before remote review round 1"
        )
    return {
        "owner": owner,
        "candidate_sha": bound_sha,
        "limits": normalized_limits,
        "permissions": list(READ_ONLY_PERMISSIONS),
        "actions": list(READ_ONLY_ACTIONS),
        "finding_ids": sorted(finding_ids),
        "completed_before_remote_round": completed_before,
    }, finding_ids


def _validate_remote_reviews(
    value: Any,
    findings: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    reviews = reporter.expect_list(value, "contract.remote_reviews")
    normalized = []
    review_ids = []
    owned_findings = set()
    for index, raw in enumerate(reviews):
        label = f"contract.remote_reviews[{index}]"
        review = reporter.expect_object(raw, label)
        reporter.expect_keys(
            review,
            label,
            (
                "round",
                "review_id",
                "reviewer",
                "candidate_sha",
                "outcome",
                "finding_ids",
            ),
        )
        round_number = reporter.expect_int(review["round"], f"{label}.round", 1)
        if round_number != index + 1:
            raise reporter.PilotDataError(
                "remote review rounds must be consecutive from 1"
            )
        review_id = reporter.expect_int(
            review["review_id"], f"{label}.review_id", 1
        )
        review_ids.append(review_id)
        reviewer = reporter.expect_string(
            review["reviewer"], f"{label}.reviewer"
        )
        if reviewer != reporter.REVIEW_BOT:
            raise reporter.PilotDataError(
                "remote review must remain assigned to GitHub Copilot"
            )
        candidate_sha = reporter.expect_sha(
            review["candidate_sha"], f"{label}.candidate_sha"
        )
        outcome = reporter.expect_enum(
            review["outcome"], REMOTE_OUTCOMES, f"{label}.outcome"
        )
        finding_ids = set(
            _expect_string_list(
                review["finding_ids"],
                f"{label}.finding_ids",
                nonempty=False,
            )
        )
        if outcome == "clean" and finding_ids:
            raise reporter.PilotDataError(
                f"remote review round {round_number} is clean but has findings"
            )
        if outcome == "changes-requested" and not finding_ids:
            raise reporter.PilotDataError(
                f"remote review round {round_number} requests changes without findings"
            )
        overlap = finding_ids & owned_findings
        if overlap:
            raise reporter.PilotDataError(
                f"remote review findings have overlapping ownership {sorted(overlap)}"
            )
        unknown = finding_ids - set(findings)
        if unknown:
            raise reporter.PilotDataError(
                f"remote review references unknown findings {sorted(unknown)}"
            )
        for finding_id in finding_ids:
            if findings[finding_id]["candidate_sha"] != candidate_sha:
                raise reporter.PilotDataError(
                    f"remote finding {finding_id!r} has stale candidate binding"
                )
        owned_findings.update(finding_ids)
        normalized.append(
            {
                "round": round_number,
                "review_id": review_id,
                "reviewer": reviewer,
                "candidate_sha": candidate_sha,
                "outcome": outcome,
                "finding_ids": sorted(finding_ids),
            }
        )
    reporter.expect_unique(review_ids, "contract.remote_reviews review IDs")
    return normalized, owned_findings


def _validate_architecture_disposition(
    value: Any,
) -> dict[str, Any] | None:
    if value is None:
        return None
    label = "contract.architecture_disposition"
    disposition = reporter.expect_object(value, label)
    reporter.expect_keys(
        disposition,
        label,
        ("after_round", "candidate_sha", "action", "evidence"),
    )
    return {
        "after_round": reporter.expect_int(
            disposition["after_round"], f"{label}.after_round", 3
        ),
        "candidate_sha": reporter.expect_sha(
            disposition["candidate_sha"], f"{label}.candidate_sha"
        ),
        "action": reporter.expect_enum(
            disposition["action"],
            ARCHITECTURE_ACTIONS,
            f"{label}.action",
        ),
        "evidence": _validate_evidence(
            disposition["evidence"], f"{label}.evidence"
        ),
    }


def _finding_handoff(
    finding_ids: list[str],
    sweeps: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [sweeps[finding_id] for finding_id in sorted(finding_ids)]


def _progress_rounds(
    reviews: list[dict[str, Any]],
    sweeps: dict[str, dict[str, Any]],
    disposition: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool]:
    consecutive = 0
    pending_hold = None
    disposition_used = False
    handoffs = []

    for review in reviews:
        if pending_hold is not None:
            if (
                disposition is None
                or disposition["after_round"] != pending_hold["round"]
                or disposition["candidate_sha"] != pending_hold["candidate_sha"]
            ):
                raise reporter.PilotDataError(
                    "remote review continued before architecture hold disposition"
                )
            pending_hold = None
            consecutive = 0
            disposition_used = True

        if review["outcome"] == "clean":
            consecutive = 0
            continue

        consecutive += 1
        if consecutive <= 2:
            finding_handoffs = _finding_handoff(
                review["finding_ids"], sweeps
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
                        "siblings": sum(
                            len(handoff["siblings"])
                            for handoff in finding_handoffs
                        ),
                    },
                }
            )
        else:
            pending_hold = {
                "round": review["round"],
                "candidate_sha": review["candidate_sha"],
                "reason": "third-consecutive-change-request",
            }

    if pending_hold is not None and disposition is not None:
        if (
            disposition["after_round"] == pending_hold["round"]
            and disposition["candidate_sha"] == pending_hold["candidate_sha"]
        ):
            pending_hold = None
            disposition_used = True
    if disposition is not None and not disposition_used:
        raise reporter.PilotDataError(
            "architecture disposition does not resolve a third consecutive "
            "change-request round"
        )
    return handoffs, pending_hold, disposition_used


def build_report(raw_contract: Any) -> dict[str, Any]:
    """Validate one immutable candidate review contract and derive its gates."""
    contract = reporter.expect_object(raw_contract, "contract")
    reporter.expect_keys(
        contract,
        "contract",
        (
            "schema_version",
            "repository",
            "pull_request",
            "candidate_sha",
            "implementer",
            "trigger",
            "pre_reviews",
            "behavior_rows",
            "remote_reviews",
            "findings",
            "family_sweeps",
            "architecture_disposition",
        ),
    )
    schema_version = reporter.expect_int(
        contract["schema_version"], "contract.schema_version", 1
    )
    if schema_version != SCHEMA_VERSION:
        raise reporter.PilotDataError(
            f"contract.schema_version must be {SCHEMA_VERSION}"
        )
    repository = reporter.expect_string(
        contract["repository"], "contract.repository"
    )
    pull_request = reporter.expect_int(
        contract["pull_request"], "contract.pull_request", 1
    )
    candidate_sha = reporter.expect_sha(
        contract["candidate_sha"], "contract.candidate_sha"
    )
    implementer = reporter.expect_string(
        contract["implementer"], "contract.implementer"
    )
    trigger, pre_review_required = _validate_trigger(contract["trigger"])
    behavior_rows = _validate_behavior_rows(contract["behavior_rows"])
    findings = _validate_findings(contract["findings"])
    remote_reviews, remote_finding_ids = _validate_remote_reviews(
        contract["remote_reviews"], findings
    )
    first_remote_sha = (
        remote_reviews[0]["candidate_sha"] if remote_reviews else None
    )
    pre_review, pre_review_finding_ids = _validate_pre_reviews(
        contract["pre_reviews"],
        required=pre_review_required,
        implementer=implementer,
        candidate_sha=candidate_sha,
        first_remote_sha=first_remote_sha,
        findings=findings,
    )
    overlap = pre_review_finding_ids & remote_finding_ids
    if overlap:
        raise reporter.PilotDataError(
            f"review findings have overlapping ownership {sorted(overlap)}"
        )
    owned_findings = pre_review_finding_ids | remote_finding_ids
    if owned_findings != set(findings):
        raise reporter.PilotDataError(
            "review ownership does not exactly cover accepted findings "
            f"(missing={sorted(set(findings) - owned_findings)}, "
            f"extra={sorted(owned_findings - set(findings))})"
        )
    sweeps = _validate_sweeps(contract["family_sweeps"], findings)
    disposition = _validate_architecture_disposition(
        contract["architecture_disposition"]
    )
    handoffs, architecture_hold, _ = _progress_rounds(
        remote_reviews, sweeps, disposition
    )
    if (
        architecture_hold is not None
        and candidate_sha != architecture_hold["candidate_sha"]
    ):
        raise reporter.PilotDataError(
            "candidate advanced while architecture hold was unresolved"
        )

    latest_review = remote_reviews[-1] if remote_reviews else None
    current_candidate_reviewed = (
        latest_review is not None
        and latest_review["candidate_sha"] == candidate_sha
    )
    current_candidate_clean = (
        current_candidate_reviewed
        and latest_review["outcome"] == "clean"
    )
    family_counts = {
        family: sum(
            finding["family"] == family for finding in findings.values()
        )
        for family in FAMILY_MEMBERS
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            "repository": repository,
            "pull_request": pull_request,
            "candidate_sha": candidate_sha,
        },
        "trigger": {
            **trigger,
            "adversarial_pre_review_required": pre_review_required,
        },
        "pre_review": {
            "required": pre_review_required,
            "completed": pre_review is not None,
            "record": pre_review,
        },
        "behavior_rows": behavior_rows,
        "families": {
            family: list(members)
            for family, members in FAMILY_MEMBERS.items()
        },
        "findings": {
            "count": len(findings),
            "by_family": family_counts,
            "handoffs": _finding_handoff(list(findings), sweeps),
        },
        "round_handoffs": handoffs,
        "architecture_hold": {
            "required": architecture_hold is not None,
            "record": architecture_hold,
            "disposition": disposition,
        },
        "gates": {
            "push_allowed": architecture_hold is None,
            "remote_copilot_review_required": True,
            "current_candidate_reviewed": current_candidate_reviewed,
            "current_candidate_clean": current_candidate_clean,
            "merge_allowed": bool(
                current_candidate_clean and architecture_hold is None
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
            "Validate one immutable sibling-family review contract and emit "
            "canonical JSON."
        )
    )
    parser.add_argument("--contract", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        contract = reporter.load_json(args.contract)
        report = build_report(contract)
    except reporter.PilotDataError as error:
        print(f"workflow review-family error: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(reporter.normalized_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
