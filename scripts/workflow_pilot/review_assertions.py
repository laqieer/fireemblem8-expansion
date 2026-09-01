#!/usr/bin/env python3
"""Exact-base executable assertions for review-family evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


FAMILY_MEMBERS = {
    "action": ("actions", "items", "targets"),
    "generated": ("owners", "outputs", "consumers", "drift-checks"),
    "lifecycle": ("entries", "preservation", "resets", "terminals"),
    "resource": ("enabled", "disabled"),
    "wire": ("producers", "consumers", "validators", "replay", "stale-bindings"),
}
BEHAVIOR_ROWS = {
    "actor-permission-bounds",
    "authority-causality",
    "remote-review-metrics",
    "round-lifecycle",
    "sibling-family-expansion",
}
EVIDENCE_CLASSES = {"positive", "adversarial", "default", "runtime"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ASSERTION_INPUT_PATHS = (
    ".github/workflow-pilot-decisions.json",
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
WORKFLOW_FEATURE_ID = "workflow-governance"
WORKFLOW_REVIEW_FAMILY_CASE = "TC-WORKFLOW-REVIEW-FAMILY-001"
CURRENT_IMPLEMENTATION_ISSUE = (
    "https://github.com/laqieer/fireemblem8-expansion/issues/179"
)
CHECKER_INPUT_FIELDS = (
    "schema_version",
    "repository",
    "repository_root",
    "pull_request",
    "base_sha",
    "base_tree",
    "original_pre_review_head",
    "original_pre_review_head_tree",
    "original_changes",
    "original_receipt_sha256",
    "review_contract",
    "original_review_receipt",
    "assertion_program_path",
    "assertion_program_blob_oid",
    "assertion_program_argv",
    "finding_origin_sha",
    "finding_origin_tree",
    "origin_root",
    "head_root",
    "assertion_input_artifacts",
    "candidate_sha",
    "candidate_tree",
    "head_sha",
    "review_round",
    "review_context",
    "all_remote_reviews",
    "remote_findings",
    "trust_mode",
    "changed_files",
    "changes",
    "remote_finding_ids",
    "limits",
    "original_pre_review",
    "round_findings",
    "assertion_requests",
)
RAW_CHECKER_INPUT_FIELDS = (
    "schema_version",
    "repository",
    "repository_root",
    "pull_request",
    "base_sha",
    "base_tree",
    "original_pre_review_head",
    "original_changes",
    "original_receipt_sha256",
    "review_contract",
    "original_review_receipt",
    "assertion_program_path",
    "assertion_program_blob_oid",
    "assertion_program_argv",
    "finding_origin_sha",
    "finding_origin_tree",
    "origin_root",
    "head_root",
    "assertion_input_artifacts",
    "candidate_sha",
    "candidate_tree",
    "head_sha",
    "review_round",
    "review_context",
    "all_remote_reviews",
    "remote_findings",
    "trust_mode",
    "changed_files",
    "changes",
    "remote_finding_ids",
    "limits",
    "original_pre_review",
    "assertion_requests",
)
CHILD_RUNNER = r"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import shutil
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


FAMILY_MEMBERS = {
    "action": ("actions", "items", "targets"),
    "generated": ("owners", "outputs", "consumers", "drift-checks"),
    "lifecycle": ("entries", "preservation", "resets", "terminals"),
    "resource": ("enabled", "disabled"),
    "wire": ("producers", "consumers", "validators", "replay", "stale-bindings"),
}
WORKFLOW_FEATURE_ID = "workflow-governance"
WORKFLOW_REVIEW_FAMILY_CASE = "TC-WORKFLOW-REVIEW-FAMILY-001"
CURRENT_IMPLEMENTATION_ISSUE = (
    "https://github.com/laqieer/fireemblem8-expansion/issues/179"
)
RAW_CHECKER_INPUT_FIELDS = (
    "schema_version",
    "repository",
    "repository_root",
    "pull_request",
    "base_sha",
    "base_tree",
    "original_pre_review_head",
    "original_changes",
    "original_receipt_sha256",
    "review_contract",
    "original_review_receipt",
    "assertion_program_path",
    "assertion_program_blob_oid",
    "assertion_program_argv",
    "finding_origin_sha",
    "finding_origin_tree",
    "origin_root",
    "head_root",
    "assertion_input_artifacts",
    "candidate_sha",
    "candidate_tree",
    "head_sha",
    "review_round",
    "review_context",
    "all_remote_reviews",
    "remote_findings",
    "trust_mode",
    "changed_files",
    "changes",
    "remote_finding_ids",
    "limits",
    "original_pre_review",
    "assertion_requests",
)


def normalized_json(value):
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def parse_time(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RuntimeError("timestamps must use RFC 3339 UTC form")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as error:
        raise RuntimeError("timestamps must be valid UTC values") from error


def format_time(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def changed_files(changes):
    return sorted(
        {
            path
            for change in changes
            for path in (change["old_path"], change["new_path"])
            if path is not None
        }
    )


def raw_checker_input(context):
    result = {
        key: copy.deepcopy(context[key])
        for key in RAW_CHECKER_INPUT_FIELDS
        if key != "remote_findings"
    }
    result["remote_findings"] = remote_findings(context)
    return result


def deny_network():
    def denied(*args, **kwargs):
        raise RuntimeError("network access denied in assertion subprocess")

    class DeniedSocket:
        def __new__(cls, *args, **kwargs):
            raise RuntimeError("network access denied in assertion subprocess")

    socket.socket = DeniedSocket
    socket.create_connection = denied
    socket.getaddrinfo = denied
    for name in ("create_server", "fromfd", "socketpair"):
        if hasattr(socket, name):
            setattr(socket, name, denied)


def import_gate():
    gate = importlib.import_module("scripts.workflow_pilot.trusted_review_gate")
    reporter = importlib.import_module("scripts.workflow_pilot.reporter")
    review_family = importlib.import_module("scripts.workflow_pilot.review_family")
    gate.reporter = reporter
    gate.review_family = review_family
    return gate, reporter, review_family


def import_checker():
    sys.argv = ["review_base_checker.py"]
    return importlib.import_module("scripts.workflow_pilot.review_base_checker")


def import_outputs():
    candidate = importlib.import_module("scripts.workflow_pilot.candidate_evidence")
    classifier = importlib.import_module("scripts.workflow_pilot.event_classifier")
    return candidate, classifier


def authoritative_trigger(gate, context):
    contract = context["review_contract"]
    if contract["trust_mode"] == "introduction":
        return None
    return gate.load_authoritative_trigger(
        contract,
        Path(context["repository_root"]),
        context["candidate_sha"],
    )


def remote_findings(context):
    return [
        {
            "node_id": finding["id"],
            "review_id": finding["review_id"],
            "candidate_sha": finding["candidate_sha"],
            "created_at": finding["created_at"],
            "author_actor_id": finding["author_actor_id"],
            "family": finding["family"],
        }
        for finding in context["remote_findings"]
    ]


def pre_review_findings(context):
    report = context["original_pre_review"]
    return [
        {
            "id": finding["id"],
            "review_id": report["report_id"],
            "candidate_sha": context["original_pre_review_head"],
            "created_at": finding["created_at"],
            "author_actor_id": report["reviewer_actor_id"],
            "family": finding["family"],
        }
        for finding in report["findings"]
    ]


def actor_records(context):
    report = context["original_pre_review"]
    contract = context["review_contract"]
    actors = [
        {
            "id": contract["implementer_actor_id"],
            "login": report["implementer_login"],
            "kind": "service",
        },
        {
            "id": report["reviewer_actor_id"],
            "login": report["reviewer_login"],
            "kind": "service",
        },
    ]
    seen = {actor["id"] for actor in actors}
    for review in context["all_remote_reviews"]:
        actor_id = review["reviewer_actor_id"]
        if actor_id not in seen:
            actors.append(
                {
                    "id": actor_id,
                    "login": "copilot-pull-request-reviewer[bot]",
                    "kind": "bot",
                }
            )
            seen.add(actor_id)
    for finding in context["remote_findings"]:
        actor_id = finding["author_actor_id"]
        if actor_id not in seen:
            actors.append(
                {
                    "id": actor_id,
                    "login": "copilot-pull-request-reviewer[bot]",
                    "kind": "bot",
                }
            )
            seen.add(actor_id)
    return actors


def pull_request_state(context):
    timestamps = [
        parse_time(context["original_pre_review"]["started_at"]),
        parse_time(context["original_review_receipt"]["issued_at"]),
        *(parse_time(review["submitted_at"]) for review in context["all_remote_reviews"]),
    ]
    created_at = format_time(min(timestamps) - timedelta(seconds=60))
    return {
        "number": context["pull_request"],
        "node_id": f"PR_{context['pull_request']}",
        "created_at": created_at,
        "base_sha": context["base_sha"],
        "head_sha": context["candidate_sha"],
        "author_actor_id": context["review_contract"]["implementer_actor_id"],
    }


def build_pre_reviews(context, trigger):
    if trigger is None or not trigger["pre_review_required"]:
        return []
    report = context["original_pre_review"]
    findings = pre_review_findings(context)
    return [
        {
            "id": report["report_id"],
            "owner_actor_id": report["reviewer_actor_id"],
            "candidate_sha": context["original_pre_review_head"],
            "started_at": report["started_at"],
            "completed_at": report["completed_at"],
            "receipt_issued_at": context["original_review_receipt"]["issued_at"],
            "permissions": report["permissions"],
            "actions": [
                {
                    "id": f"{report['report_id']}:READ",
                    "kind": "read-candidate",
                    "occurred_at": report["started_at"],
                },
                {
                    "id": f"{report['report_id']}:REPORT",
                    "kind": "emit-local-report",
                    "occurred_at": report["completed_at"],
                },
            ],
            "finding_ids": [finding["id"] for finding in findings],
            "reviewed_files": report["reviewed_files"],
            "reviewed_changes": report["reviewed_changes"],
        }
    ]


def build_threads(context):
    return [
        {
            "node_id": f"THREAD_{finding['id']}",
            "finding_id": finding["id"],
            "is_resolved": False,
        }
        for finding in context["remote_findings"]
    ]


def wire_payload_parts(context):
    gate, _, _ = import_gate()
    trigger = authoritative_trigger(gate, context)
    return {
        "gate": gate,
        "trigger": trigger,
        "captured_at": format_time(
            max(
                parse_time(context["original_review_receipt"]["issued_at"]),
                *(parse_time(review["submitted_at"]) for review in context["all_remote_reviews"]),
            )
            + timedelta(seconds=1)
        ),
        "pull_request": pull_request_state(context),
        "actors": actor_records(context),
        "pre_reviews": build_pre_reviews(context, trigger),
        "pre_review_findings": pre_review_findings(context),
        "remote_reviews": copy.deepcopy(context["all_remote_reviews"]),
        "findings": remote_findings(context),
        "threads": build_threads(context),
    }


def build_wire_payload(context):
    parts = wire_payload_parts(context)
    payload = parts["gate"].build_live_evidence_payload(
        contract=context["review_contract"],
        expected_candidate=context["candidate_sha"],
        source_kind="offline-transform-fixture",
        captured_at=parts["captured_at"],
        original_receipt_sha256=context["original_receipt_sha256"],
        pull_request=parts["pull_request"],
        authoritative_trigger=parts["trigger"],
        actors=parts["actors"],
        pre_reviews=parts["pre_reviews"],
        pre_review_findings=parts["pre_review_findings"],
        remote_reviews=parts["remote_reviews"],
        findings=parts["findings"],
        threads=parts["threads"],
        force_push_events=[],
        architecture_dispositions=[],
        execution_receipts=[],
    )
    return parts["gate"], payload


def progress_review(round_number, candidate_sha, submitted_at, outcome, finding_ids):
    return {
        "id": 1000 + round_number,
        "node_id": f"REMOTE_REVIEW_{round_number}",
        "round": round_number,
        "reviewer_actor_id": "ACTOR_COPILOT_001",
        "candidate_sha": candidate_sha,
        "submitted_at": submitted_at,
        "state": "COMMENTED",
        "body": (
            "### 🟡 Changes recommended"
            if outcome == "changes-requested"
            else "### 🟢 Approval recommended"
        ),
        "body_classification": (
            "changes-recommended"
            if outcome == "changes-requested"
            else "clean-approval"
        ),
        "body_has_findings": outcome == "changes-requested" and bool(finding_ids),
        "outcome": outcome,
        "finding_ids": finding_ids,
        "_submitted": parse_time(submitted_at),
    }


def progress_sweeps(binding):
    return {
        binding["finding_id"]: {
            "family": binding["finding_family"],
            "siblings": [
                {"member": member}
                for member in FAMILY_MEMBERS[binding["finding_family"]]
            ],
        }
    }


def probe_action_actions(context, binding):
    checker = import_checker()
    positive = checker.validate_review_action_contract(
        repository=context["repository"],
        actions=copy.deepcopy(context["original_pre_review"])["actions"],
    )
    negative = copy.deepcopy(context["original_pre_review"])
    negative["actions"] = list(reversed(positive))
    try:
        checker.validate_review_action_contract(
            repository=context["repository"],
            actions=negative["actions"],
        )
    except checker.CheckError as error:
        rejection = str(error)
    else:
        raise RuntimeError("read-only action sequence is not enforced")
    return {"sequence": positive, "rejection": rejection}


def probe_action_items(context, binding):
    checker = import_checker()
    data = {
        "round_findings": context["round_findings"],
        "candidate_sha": context["candidate_sha"],
        "candidate_tree": context["candidate_tree"],
    }
    parsed = {
        "family": "action",
        "member": "items",
        "outcome": "affected-fixed",
        "reason": None,
    }
    result = checker.bind_member_request(data, parsed, binding["finding_id"])
    if result != binding:
        raise RuntimeError("member-item authority binding is incomplete")
    wrong_family = dict(parsed)
    wrong_family["family"] = "wire"
    try:
        checker.bind_member_request(data, wrong_family, binding["finding_id"])
    except checker.CheckError as error:
        family_rejection = str(error)
    else:
        raise RuntimeError("member-item family mismatches are not rejected")
    try:
        checker.bind_member_request(data, parsed, "FINDING")
    except checker.CheckError as error:
        sentinel_rejection = str(error)
    else:
        raise RuntimeError("sentinel finding IDs are not rejected")
    return {
        "checker_binding": True,
        "family_rejection": family_rejection,
        "sentinel_rejection": sentinel_rejection,
    }


def probe_action_targets(context, binding):
    checker = import_checker()
    report = checker.validate_review_targets(
        copy.deepcopy(context["original_pre_review"])["reviewed_files"],
        copy.deepcopy(context["original_pre_review"])["reviewed_changes"],
        changed_files=changed_files(context["original_changes"]),
        changes=context["original_changes"],
    )
    negative = copy.deepcopy(context["original_pre_review"])
    negative["reviewed_files"] = report["reviewed_files"][:-1]
    if not negative["reviewed_files"]:
        negative["reviewed_files"] = []
    try:
        checker.validate_review_targets(
            negative["reviewed_files"],
            negative["reviewed_changes"],
            changed_files=changed_files(context["original_changes"]),
            changes=context["original_changes"],
        )
    except checker.CheckError as error:
        rejection = str(error)
    else:
        raise RuntimeError("exact changed-file coverage is not enforced")
    return {
        "statuses": sorted({change["status"] for change in report["reviewed_changes"]}),
        "rejection": rejection,
    }


def probe_generated_owners(context, binding):
    registry = json.loads(Path("docs/test-cases/registry.json").read_text(encoding="utf-8"))
    feature = next(item for item in registry["features"] if item["id"] == WORKFLOW_FEATURE_ID)
    case = next(item for item in registry["cases"] if item["id"] == WORKFLOW_REVIEW_FAMILY_CASE)
    if CURRENT_IMPLEMENTATION_ISSUE not in feature["issue_urls"]:
        raise RuntimeError("workflow-governance registry does not claim issue #179")
    if WORKFLOW_REVIEW_FAMILY_CASE not in feature["required_cases"]:
        raise RuntimeError("workflow-governance registry does not include the review-family case")
    if case["document"] != "docs/test-cases/workflow-governance.md":
        raise RuntimeError("workflow-governance registry case document is incorrect")
    return {
        "issue_urls": sorted(feature["issue_urls"]),
        "required_cases": sorted(feature["required_cases"]),
    }


def probe_generated_outputs(context, binding):
    candidate, classifier = import_outputs()
    contexts = [
        {"job_id": "event-identity", "name": "event-identity", "conclusion": "success"},
        {"job_id": "event-router", "name": "event-router", "conclusion": "success"},
        {"job_id": "patch-release", "name": "patch-release", "conclusion": "skipped"},
        {
            "job_id": "event-classifier",
            "name": candidate.FULL_CLASSIFIER,
            "conclusion": "success",
        },
    ]
    contexts.extend(
        {"job_id": job_id, "name": job_id, "conclusion": "success"}
        for job_id in candidate.WORKER_JOB_IDS
    )
    contexts.append(
        {"job_id": "summary", "name": candidate.FULL_ATTESTATION, "conclusion": "success"}
    )
    run = {
        "base_sha": context["base_sha"],
        "contexts": contexts,
        "event": "pull_request",
        "head_sha": context["candidate_sha"],
        "run_id": 1,
    }
    evidence = candidate.evaluate_candidate_runs(
        [run],
        head_sha=context["candidate_sha"],
        base_sha=context["base_sha"],
    )
    if not evidence.eligible or evidence.mode != "full":
        raise RuntimeError("candidate evidence outputs are incomplete")
    payload = {
        "action": "synchronize",
        "number": context["pull_request"],
        "pull_request": {
            "number": context["pull_request"],
            "head": {"sha": context["candidate_sha"]},
            "base": {"sha": context["base_sha"], "ref": "master"},
        },
    }
    decision = classifier.classify_event(
        "pull_request",
        payload,
        github_ref=f"refs/pull/{context['pull_request']}/merge",
        github_sha=context["candidate_sha"],
        pr_base_sha=context["base_sha"],
        pr_head_sha=context["candidate_sha"],
        push_sha="",
    )
    if (
        decision.classification != "full"
        or decision.expected_base != context["base_sha"]
        or decision.expected_head != context["candidate_sha"]
        or not decision.run_expensive
        or not decision.identity_valid
    ):
        raise RuntimeError("event-classifier output fields are incomplete")
    return {
        "workers": list(candidate.WORKER_JOB_IDS),
        "decision_fields": list(classifier.EventDecision.__annotations__),
    }


def probe_generated_consumers(context, binding):
    topology = importlib.import_module("tests.workflows.test_build_ci_topology")
    workflow_text = Path(".github/workflows/build.yml").read_text(encoding="utf-8")
    event = {
        "event_name": "pull_request",
        "payload": {
            "action": "synchronize",
            "number": context["pull_request"],
            "pull_request": {
                "number": context["pull_request"],
                "head": {"sha": context["candidate_sha"]},
                "base": {"sha": context["base_sha"], "ref": "master"},
            },
        },
        "runner": {
            "github_ref": f"refs/pull/{context['pull_request']}/merge",
            "github_sha": context["candidate_sha"],
            "pr_base_sha": context["base_sha"],
            "pr_head_sha": context["candidate_sha"],
            "push_sha": "",
            "pr_number": context["pull_request"],
        },
    }
    jobs = topology.triggered_jobs(workflow_text, event)
    if set(jobs) != set(topology.CANDIDATE_FULL_JOBS):
        raise RuntimeError("workflow topology tests do not evaluate candidate evidence")
    return {"jobs": sorted(jobs)}


def probe_generated_drift_checks(context, binding):
    skill = importlib.import_module("scripts.docs_check_tests.test_development_workflow_skill")
    docs_tests = importlib.import_module("scripts.docs_check_tests.test_check_docs")
    registry = json.loads(Path("docs/test-cases/registry.json").read_text(encoding="utf-8"))
    feature = next(item for item in registry["features"] if item["id"] == WORKFLOW_FEATURE_ID)
    case = next(item for item in registry["cases"] if item["id"] == WORKFLOW_REVIEW_FAMILY_CASE)
    expected_cases = [
        "TC-WORKFLOW-CI-WAIT-001",
        "TC-WORKFLOW-MANUAL-HANDOFF-001",
        "TC-WORKFLOW-STACKED-CI-001",
        "TC-WORKFLOW-BODY-EDIT-001",
        "TC-WORKFLOW-PILOT-BASELINE-001",
        "TC-WORKFLOW-REVIEW-FAMILY-001",
    ]
    if skill.compare_string_membership(
        feature["required_cases"],
        expected_cases,
        "workflow-governance.required_cases",
    ):
        raise RuntimeError("docs drift checks do not cover workflow-governance")
    if docs_tests.membership_violations(feature["required_cases"], expected_cases):
        raise RuntimeError("docs drift checks do not cover the review-family case")
    if case["document"] != "docs/test-cases/workflow-governance.md":
        raise RuntimeError("review-family case registry document drifted")
    return {
        "required_cases": feature["required_cases"],
        "document": case["document"],
    }


def probe_lifecycle_entries(context, binding):
    review_family = importlib.import_module("scripts.workflow_pilot.review_family")
    start = parse_time(context["review_context"]["submitted_at"])
    finding_ids = [binding["finding_id"]]
    reviews = [
        progress_review(1, context["candidate_sha"], format_time(start), "changes-requested", finding_ids),
        progress_review(2, context["candidate_sha"], format_time(start + timedelta(minutes=1)), "changes-requested", finding_ids),
        progress_review(3, context["candidate_sha"], format_time(start + timedelta(minutes=2)), "changes-requested", finding_ids),
    ]
    handoffs, pending, consumed = review_family.progress_rounds(
        {
            "architecture_dispositions": [],
            "remote_reviews": reviews,
            "candidate": {"sha": context["candidate_sha"]},
        },
        progress_sweeps(binding),
        set(),
    )
    if pending is None or pending["reason"] != "third-consecutive-change-request":
        raise RuntimeError("lifecycle hold-entry contract is incomplete")
    if len(handoffs) != 2 or consumed:
        raise RuntimeError("lifecycle handoff bounds are incomplete")
    return {"hold_reason": pending["reason"], "handoffs": len(handoffs)}


def probe_lifecycle_preservation(context, binding):
    gate, _, _ = import_gate()
    receipt = context["original_review_receipt"]
    receipt_bytes = normalized_json(receipt)
    replay_store = (
        Path(context["repository_root"])
        / "build"
        / "test-artifacts"
        / ("assertion-preservation-" + hashlib.sha256(receipt_bytes).hexdigest()[:12])
    )
    if replay_store.exists():
        shutil.rmtree(replay_store)
    replay_store.mkdir(parents=True)
    wrong_head = (
        context["candidate_sha"]
        if context["candidate_sha"] != context["original_pre_review_head"]
        else context["base_sha"]
    )
    try:
        gate.persist_original_receipt(
            receipt_bytes,
            replay_store,
            repository=context["repository"],
            pull_request=context["pull_request"],
            base_sha=context["base_sha"],
            original_pre_review_head=context["original_pre_review_head"],
            key_id=receipt["key_id"],
            key_epoch=receipt["key_epoch"],
        )
        preserved = gate.preserved_receipt_bytes(
            replay_store,
            repository=context["repository"],
            pull_request=context["pull_request"],
            base_sha=context["base_sha"],
            original_pre_review_head=context["original_pre_review_head"],
            key_id=receipt["key_id"],
            key_epoch=receipt["key_epoch"],
        )
        try:
            gate.preserved_receipt_bytes(
                replay_store,
                repository=context["repository"],
                pull_request=context["pull_request"],
                base_sha=context["base_sha"],
                original_pre_review_head=wrong_head,
                key_id=receipt["key_id"],
                key_epoch=receipt["key_epoch"],
            )
        except gate.reporter.PilotDataError as error:
            wrong_head_rejection = str(error)
        else:
            raise RuntimeError(
                "preserved original pre-review is not bound to the original head"
            )
    finally:
        shutil.rmtree(replay_store)
    if preserved != receipt_bytes:
        raise RuntimeError("receipt preservation is not exact")
    return {
        "receipt_sha256": hashlib.sha256(preserved).hexdigest(),
        "wrong_head_rejection": wrong_head_rejection,
    }


def probe_lifecycle_resets(context, binding):
    review_family = importlib.import_module("scripts.workflow_pilot.review_family")
    start = parse_time(context["review_context"]["submitted_at"])
    finding_ids = [binding["finding_id"]]
    reviews = [
        progress_review(1, context["candidate_sha"], format_time(start), "changes-requested", finding_ids),
        progress_review(2, context["candidate_sha"], format_time(start + timedelta(minutes=1)), "clean", []),
        progress_review(3, context["candidate_sha"], format_time(start + timedelta(minutes=2)), "changes-requested", finding_ids),
    ]
    handoffs, pending, consumed = review_family.progress_rounds(
        {
            "architecture_dispositions": [],
            "remote_reviews": reviews,
            "candidate": {"sha": context["candidate_sha"]},
        },
        progress_sweeps(binding),
        set(),
    )
    counts = [item["consecutive_change_request"] for item in handoffs]
    if counts != [1, 1] or pending is not None or consumed:
        raise RuntimeError("lifecycle reset paths are incomplete")
    return {"resets": counts}


def probe_lifecycle_terminals(context, binding):
    gate, _, _ = import_gate()
    contract = copy.deepcopy(context["review_contract"])
    contract["trust_mode"] = "introduction"
    result = gate.bootstrap_result(
        contract,
        context["base_sha"],
        context["candidate_sha"],
    )
    gates = result["gates"]
    if (
        result["bootstrap"]["mode"] != "introduction"
        or not result["bootstrap"]["external_coordinator_review_required"]
        or gates["push_allowed"]
        or gates["trusted_push_allowed"]
        or gates["merge_allowed"]
    ):
        raise RuntimeError("terminal gate contract is incomplete")
    return {"terminal_gates": True}


def probe_resource_enabled(context, binding):
    gate, _, _ = import_gate()
    trigger = authoritative_trigger(gate, context)
    if trigger is None or not trigger["pre_review_required"]:
        raise RuntimeError(
            "authoritative decision record does not contain one exact high-risk review-family entry"
        )
    return {
        "risk_boundaries": trigger["risk_boundaries"],
        "threshold_triggers": trigger["threshold_triggers"],
    }


def probe_resource_disabled(context, binding):
    gate, _, _ = import_gate()
    contract = copy.deepcopy(context["review_contract"])
    contract["trust_mode"] = "introduction"
    result = gate.bootstrap_result(
        contract,
        context["base_sha"],
        context["candidate_sha"],
    )
    if result["bootstrap"]["mode"] != "introduction" or result["gates"]["merge_allowed"]:
        raise RuntimeError("introduction-mode disabled boundary is incomplete")
    return {"introduction_mode": True}


def probe_wire_producers(context, binding):
    _, payload = build_wire_payload(context)
    if "result_manifest" not in payload or "execution_receipts" not in payload:
        raise RuntimeError("wire producers are incomplete")
    if payload["candidate"]["sha"] != context["candidate_sha"]:
        raise RuntimeError("wire producers are incomplete")
    return {
        "result_manifest_size": len(payload["result_manifest"]),
        "candidate_head_sha": payload["pull_request"]["head_sha"],
    }


def probe_wire_consumers(context, binding):
    parts = wire_payload_parts(context)
    payload = {
        "schema_version": 5,
        "repository": context["review_contract"]["repository"],
        "source": {"kind": "offline-transform-fixture", "complete": True},
        "captured_at": parts["captured_at"],
        "candidate": {"sha": context["candidate_sha"]},
        "original_pre_review_head": context["review_contract"]["original_pre_review_head"],
        "original_receipt_sha256": context["original_receipt_sha256"],
        "pull_request": parts["pull_request"],
        "authoritative_trigger": parts["trigger"],
        "result_source_path": "scripts/workflow_pilot/tests/test_review_family.py",
        "actors": parts["actors"],
        "pre_reviews": parts["pre_reviews"],
        "pre_review_findings": parts["pre_review_findings"],
        "remote_reviews": parts["remote_reviews"],
        "findings": parts["findings"],
        "threads": parts["threads"],
        "candidate_advances": [],
        "force_push_events": [],
        "architecture_dispositions": [],
        "execution_receipts": [],
        "result_manifest": [],
    }
    review_family = importlib.import_module("scripts.workflow_pilot.review_family")
    validated = review_family.validate_evidence(payload)
    return {
        "source_kind": validated["source"]["kind"],
        "result_manifest_size": len(validated["result_manifest"]),
    }


def probe_wire_validators(context, binding):
    checker = import_checker()
    positive_path, positive_blob = checker.validate_assertion_program_identity(
        Path(context["repository_root"]),
        context["base_sha"],
        Path(context["assertion_program_path"]),
        context["assertion_program_blob_oid"],
        context["assertion_program_argv"],
    )
    try:
        checker.validate_assertion_program_identity(
            Path(context["repository_root"]),
            context["base_sha"],
            Path(context["assertion_program_path"]),
            "f" * 40,
            context["assertion_program_argv"],
        )
    except checker.CheckError as error:
        rejection = str(error)
    else:
        raise RuntimeError("checker validators are incomplete")
    return {
        "program_blob_oid": positive_blob,
        "program_path": str(positive_path),
        "rejection": rejection,
    }


def probe_wire_replay(context, binding):
    gate, _, _ = import_gate()
    receipt = context["original_review_receipt"]
    receipt_bytes = normalized_json(receipt)
    replay_store = (
        Path(context["repository_root"])
        / "build"
        / "test-artifacts"
        / ("assertion-replay-" + hashlib.sha256(receipt_bytes).hexdigest()[:12])
    )
    if replay_store.exists():
        shutil.rmtree(replay_store)
    replay_store.mkdir(parents=True)
    try:
        gate.persist_original_receipt(
            receipt_bytes,
            replay_store,
            repository=context["repository"],
            pull_request=context["pull_request"],
            base_sha=context["base_sha"],
            original_pre_review_head=context["original_pre_review_head"],
            key_id=receipt["key_id"],
            key_epoch=receipt["key_epoch"],
        )
        try:
            gate.persist_original_receipt(
                receipt_bytes,
                replay_store,
                repository=context["repository"],
                pull_request=context["pull_request"],
                base_sha=context["base_sha"],
                original_pre_review_head=context["original_pre_review_head"],
                key_id=receipt["key_id"],
                key_epoch=receipt["key_epoch"],
            )
        except gate.reporter.PilotDataError as error:
            rejection = str(error)
        else:
            raise RuntimeError("replay boundary is incomplete")
    finally:
        shutil.rmtree(replay_store)
    return {"replay_rejection": rejection}


def probe_wire_stale_bindings(context, binding):
    checker = import_checker()
    positive = checker.validate_review_context_binding(
        review_round=context["review_round"],
        review_context=copy.deepcopy(context["review_context"]),
        all_remote_reviews=copy.deepcopy(context["all_remote_reviews"]),
        candidate_sha=context["candidate_sha"],
        remote_finding_ids=copy.deepcopy(context["remote_finding_ids"]),
    )
    stale_head = copy.deepcopy(context["review_context"])
    stale_head["candidate_sha"] = context["original_pre_review_head"]
    try:
        checker.validate_review_context_binding(
            review_round=context["review_round"],
            review_context=stale_head,
            all_remote_reviews=copy.deepcopy(context["all_remote_reviews"]),
            candidate_sha=context["candidate_sha"],
            remote_finding_ids=copy.deepcopy(context["remote_finding_ids"]),
        )
    except checker.CheckError as error:
        head_rejection = str(error)
    else:
        raise RuntimeError("trusted stale-binding checks are incomplete")
    stale_round = copy.deepcopy(context["review_context"])
    stale_round["round"] = (
        context["review_round"] + 1 if context["review_round"] == 1 else 1
    )
    try:
        checker.validate_review_context_binding(
            review_round=context["review_round"],
            review_context=stale_round,
            all_remote_reviews=copy.deepcopy(context["all_remote_reviews"]),
            candidate_sha=context["candidate_sha"],
            remote_finding_ids=copy.deepcopy(context["remote_finding_ids"]),
        )
    except checker.CheckError as error:
        round_rejection = str(error)
    else:
        raise RuntimeError("trusted stale-binding checks are incomplete")
    return {
        "validated_round": positive[0]["round"],
        "head_rejection": head_rejection,
        "round_rejection": round_rejection,
    }


MEMBER_PROBES = {
    ("action", "actions"): probe_action_actions,
    ("action", "items"): probe_action_items,
    ("action", "targets"): probe_action_targets,
    ("generated", "owners"): probe_generated_owners,
    ("generated", "outputs"): probe_generated_outputs,
    ("generated", "consumers"): probe_generated_consumers,
    ("generated", "drift-checks"): probe_generated_drift_checks,
    ("lifecycle", "entries"): probe_lifecycle_entries,
    ("lifecycle", "preservation"): probe_lifecycle_preservation,
    ("lifecycle", "resets"): probe_lifecycle_resets,
    ("lifecycle", "terminals"): probe_lifecycle_terminals,
    ("resource", "enabled"): probe_resource_enabled,
    ("resource", "disabled"): probe_resource_disabled,
    ("wire", "producers"): probe_wire_producers,
    ("wire", "consumers"): probe_wire_consumers,
    ("wire", "validators"): probe_wire_validators,
    ("wire", "replay"): probe_wire_replay,
    ("wire", "stale-bindings"): probe_wire_stale_bindings,
}


def main():
    deny_network()
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.getcwd())
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    key = (payload["family"], payload["member"])
    if key not in MEMBER_PROBES:
        raise RuntimeError("member evaluator is not registered")
    result = MEMBER_PROBES[key](
        payload["checker_input"],
        payload["authority_binding"],
    )
    sys.stdout.buffer.write(normalized_json(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"review assertion child error: {error}", file=sys.stderr)
        raise SystemExit(2)
"""


class AssertionFailure(Exception):
    pass


def normalized_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def object_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AssertionFailure(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def expect_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AssertionFailure(f"{label} must be an object")
    return value


def expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AssertionFailure(f"{label} must be a list")
    return value


def expect_string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise AssertionFailure(f"{label} must be a nonempty string")
    return value


def expect_int(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionFailure(f"{label} must be an integer")
    if value < minimum:
        raise AssertionFailure(f"{label} must be at least {minimum}")
    return value


def expect_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise AssertionFailure(f"{label} must be a full lowercase Git SHA")
    return value


def expect_keys(value: dict[str, Any], label: str, required) -> None:
    required = set(required)
    if set(value) != required:
        raise AssertionFailure(f"{label} fields do not match registry schema")


def parse_assertion(assertion_id: str):
    parts = assertion_id.split(":")
    if (
        len(parts) == 5
        and parts[:2] == ["registry", "behavior"]
        and parts[2] in BEHAVIOR_ROWS
        and parts[3] in EVIDENCE_CLASSES
        and parts[4] == "v2"
    ):
        return {
            "kind": "behavior",
            "row": parts[2],
            "evidence_class": parts[3],
        }
    if len(parts) not in {6, 7} or parts[:2] != ["registry", "sibling"]:
        raise AssertionFailure("assertion ID is absent from exact-base registry")
    family, member, outcome = parts[2:5]
    reason = parts[5] if len(parts) == 7 else None
    version = parts[-1]
    if (
        family not in FAMILY_MEMBERS
        or member not in FAMILY_MEMBERS[family]
        or version != "v2"
    ):
        raise AssertionFailure("assertion member is absent from registry")
    if outcome not in {"affected-fixed", "verified-unaffected", "not-applicable"}:
        raise AssertionFailure("assertion outcome is absent from registry")
    if outcome == "not-applicable":
        if (
            family,
            member,
            reason,
        ) != ("resource", "disabled", "feature-disabled-by-contract"):
            raise AssertionFailure("not-applicable reason is not registered")
    elif reason is not None:
        raise AssertionFailure("outcome assertion has an unexpected reason")
    return {
        "kind": "member",
        "family": family,
        "member": member,
        "outcome": outcome,
        "reason": reason,
    }


def validate_row(row: str, evidence: dict[str, Any]) -> dict[str, Any]:
    if row == "actor-permission-bounds":
        if evidence["permissions"] != ["contents:read"]:
            raise AssertionFailure("permission mutation was rejected")
        return {"permissions": evidence["permissions"]}
    if row == "authority-causality":
        if evidence["base_sha"] == evidence["head_sha"] or not evidence["changes"]:
            raise AssertionFailure("authority mutation was rejected")
        return {"change_count": len(evidence["changes"])}
    if row == "remote-review-metrics":
        if evidence["review_head"] != evidence["head_sha"]:
            raise AssertionFailure("stale remote review was rejected")
        return {"review_outcome": evidence["review_outcome"]}
    if row == "round-lifecycle":
        if evidence["rounds"] != list(range(1, len(evidence["rounds"]) + 1)):
            raise AssertionFailure("round mutation was rejected")
        return {"round_count": len(evidence["rounds"])}
    if len(evidence["registered_assertions"]) != len(
        set(evidence["registered_assertions"])
    ):
        raise AssertionFailure("duplicate assertion was rejected")
    return {"assertion_count": len(evidence["registered_assertions"])}


def mutate_row(row: str, evidence: dict[str, Any]) -> dict[str, Any]:
    mutated = json.loads(json.dumps(evidence))
    if row == "actor-permission-bounds":
        mutated["permissions"] = ["contents:write"]
    elif row == "authority-causality":
        mutated["changes"] = []
    elif row == "remote-review-metrics":
        mutated["review_head"] = "f" * 40
    elif row == "round-lifecycle":
        mutated["rounds"] = [2]
    else:
        mutated["registered_assertions"].append(mutated["registered_assertions"][0])
    return mutated


def validate_member_tree(root: Path) -> None:
    expected = set(ASSERTION_INPUT_PATHS)
    discovered = set()
    for path in root.rglob("*"):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise AssertionFailure("member artifact tree contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise AssertionFailure("member artifact tree contains an unsafe entry")
        discovered.add(path.relative_to(root).as_posix())
    if discovered != expected:
        raise AssertionFailure(
            "member artifact tree does not match the allowlisted production inputs"
        )


def run_member_probe(
    root: Path,
    family: str,
    member: str,
    checker_input: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "family": family,
        "member": member,
        "checker_input": checker_input,
        "authority_binding": binding,
    }
    completed = subprocess.run(
        ("/usr/bin/python3", "-I", "-c", CHILD_RUNNER),
        cwd=root,
        env={
            "HOME": str(root),
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
            "http_proxy": "",
            "https_proxy": "",
            "all_proxy": "",
        },
        input=normalized_json(payload),
        check=False,
        capture_output=True,
        timeout=120,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AssertionFailure(detail or "member probe subprocess failed")
    try:
        result = json.loads(
            completed.stdout.decode("utf-8"),
            object_pairs_hook=object_no_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssertionFailure("member probe subprocess returned invalid JSON") from error
    if normalized_json(result) != completed.stdout:
        raise AssertionFailure("member probe subprocess output is not canonical")
    return expect_object(result, "member probe subprocess output")


def execute_behavior(assertion: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    expect_keys(request, "behavior request", ("assertion_id", "evidence"))
    evidence = expect_object(request["evidence"], "behavior evidence")
    row = assertion["row"]
    evidence_class = assertion["evidence_class"]
    if evidence_class == "adversarial":
        try:
            validate_row(row, mutate_row(row, evidence))
        except AssertionFailure as error:
            return {
                "program_case": f"behavior/{row}/adversarial",
                "rejection_observed": True,
                "rejection": str(error),
            }
        raise AssertionFailure("adversarial program did not observe rejection")
    output = validate_row(row, evidence)
    if evidence_class == "positive":
        output["scope"] = {
            "repository": evidence["repository"],
            "pull_request": evidence["pull_request"],
        }
    elif evidence_class == "default":
        output["default_mode"] = evidence["trust_mode"]
    else:
        output["runtime_head"] = evidence["head_sha"]
        output["runtime_round"] = evidence["review_round"]
    output["program_case"] = f"behavior/{row}/{evidence_class}"
    return output


def evaluate_member_contract(
    family: str,
    member: str,
    root: Path,
    binding: dict[str, Any],
    checker_input: dict[str, Any],
) -> dict[str, Any]:
    validate_member_tree(root)
    return run_member_probe(root, family, member, checker_input, binding)


def execute_member(assertion: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    expect_keys(
        request,
        "member request",
        (
            "assertion_id",
            "authority_binding",
            "origin_root",
            "head_root",
            "checker_input",
        ),
    )
    family = assertion["family"]
    member = assertion["member"]
    binding = expect_object(request["authority_binding"], "member authority binding")
    expect_keys(
        binding,
        "member authority binding",
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
    finding_id = expect_string(binding["finding_id"], "member authority binding.finding_id")
    if binding["finding_family"] != family:
        raise AssertionFailure("member authority binding family does not match assertion")
    if binding["finding_member"] != member:
        raise AssertionFailure("member authority binding member does not match assertion")
    finding_review_id = expect_string(
        binding["finding_review_id"], "member authority binding.finding_review_id"
    )
    finding_review_round = expect_int(
        binding["finding_review_round"],
        "member authority binding.finding_review_round",
        0,
    )
    finding_head_sha = expect_sha(
        binding["finding_head_sha"], "member authority binding.finding_head_sha"
    )
    finding_head_tree = expect_sha(
        binding["finding_head_tree"], "member authority binding.finding_head_tree"
    )
    finding_origin_sha = expect_sha(
        binding["finding_origin_sha"], "member authority binding.finding_origin_sha"
    )
    finding_origin_tree = expect_sha(
        binding["finding_origin_tree"], "member authority binding.finding_origin_tree"
    )
    head_sha = expect_sha(binding["head_sha"], "member authority binding.head_sha")
    head_tree = expect_sha(binding["head_tree"], "member authority binding.head_tree")
    binding_output = {
        "finding_id": finding_id,
        "finding_family": family,
        "finding_member": member,
        "finding_review_id": finding_review_id,
        "finding_review_round": finding_review_round,
        "finding_head_sha": finding_head_sha,
        "finding_head_tree": finding_head_tree,
        "finding_origin_sha": finding_origin_sha,
        "finding_origin_tree": finding_origin_tree,
        "head_sha": head_sha,
        "head_tree": head_tree,
    }
    checker_input = expect_object(request["checker_input"], "member checker input")
    expect_keys(checker_input, "member checker input", CHECKER_INPUT_FIELDS)
    if checker_input["candidate_sha"] != head_sha or checker_input["head_sha"] != head_sha:
        raise AssertionFailure("member checker input candidate/head does not match binding")
    if checker_input["finding_origin_sha"] != finding_origin_sha:
        raise AssertionFailure("member checker input origin does not match binding")
    origin_root = Path(expect_string(request["origin_root"], "member request.origin_root"))
    head_root = Path(expect_string(request["head_root"], "member request.head_root"))
    outcome = assertion["outcome"]
    if outcome == "affected-fixed":
        try:
            evaluate_member_contract(
                family, member, origin_root, binding_output, checker_input
            )
        except AssertionFailure as error:
            origin_error = str(error)
        else:
            raise AssertionFailure("affected-fixed origin assertion unexpectedly passed")
        head_output = evaluate_member_contract(
            family, member, head_root, binding_output, checker_input
        )
        return {
            **binding_output,
            "program_case": f"member/{family}/{member}/affected-fixed",
            "origin_status": "fail",
            "origin_error": origin_error,
            "head_status": "pass",
            "head_semantic_output": head_output,
        }
    if outcome == "verified-unaffected":
        origin_output = evaluate_member_contract(
            family, member, origin_root, binding_output, checker_input
        )
        head_output = evaluate_member_contract(
            family, member, head_root, binding_output, checker_input
        )
        if origin_output != head_output:
            raise AssertionFailure(
                "verified-unaffected semantic outputs are not equivalent"
            )
        semantic_output_sha256 = hashlib.sha256(
            normalized_json(head_output)
        ).hexdigest()
        return {
            **binding_output,
            "program_case": f"member/{family}/{member}/verified-unaffected",
            "origin_status": "pass",
            "head_status": "pass",
            "semantic_output_sha256": semantic_output_sha256,
        }
    head_output = evaluate_member_contract(
        family, member, head_root, binding_output, checker_input
    )
    if head_output != {"introduction_mode": True}:
        raise AssertionFailure("not-applicable predicate did not establish false")
    return {
        **binding_output,
        "program_case": "member/resource/disabled/not-applicable",
        "applicable": False,
        "reason": assertion["reason"],
    }


def execute(request: Any) -> dict[str, Any]:
    request = expect_object(request, "assertion request")
    assertion_id = request.get("assertion_id")
    if not isinstance(assertion_id, str):
        raise AssertionFailure("assertion request lacks an assertion ID")
    assertion = parse_assertion(assertion_id)
    output = (
        execute_behavior(assertion, request)
        if assertion["kind"] == "behavior"
        else execute_member(assertion, request)
    )
    return {
        "schema_version": 1,
        "assertion_id": assertion_id,
        "status": "pass",
        "output": output,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdin", action="store_true", required=True)
    return parser.parse_args(argv)


def main(argv=None):
    parse_args(argv)
    try:
        request = json.loads(
            sys.stdin.buffer.read().decode("utf-8"),
            object_pairs_hook=object_no_duplicates,
        )
        result = execute(request)
    except (UnicodeDecodeError, json.JSONDecodeError, AssertionFailure) as error:
        print(f"review assertion error: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(normalized_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
