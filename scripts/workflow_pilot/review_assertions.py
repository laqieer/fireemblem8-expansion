#!/usr/bin/env python3
"""Exact-base executable assertions for review-family evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
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
    ".github/skills/development-workflow/SKILL.md",
    "docs/test-cases/registry.json",
    "docs/test-cases/workflow-governance.md",
    "docs/workflow-pilot.md",
    "scripts/docs_check_tests/test_check_docs.py",
    "scripts/docs_check_tests/test_development_workflow_skill.py",
    "scripts/workflow_pilot/candidate_evidence.py",
    "scripts/workflow_pilot/event_classifier.py",
    "scripts/workflow_pilot/review_assertions.py",
    "scripts/workflow_pilot/review_base_checker.py",
    "scripts/workflow_pilot/review_family.py",
    "scripts/workflow_pilot/trusted_review_gate.py",
    "tests/workflows/test_build_ci_topology.py",
)
WORKFLOW_FEATURE_ID = "workflow-governance"
WORKFLOW_REVIEW_FAMILY_CASE = "TC-WORKFLOW-REVIEW-FAMILY-001"
CURRENT_IMPLEMENTATION_ISSUE = (
    "https://github.com/laqieer/fireemblem8-expansion/issues/179"
)


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
        if (
            evidence["base_sha"] == evidence["head_sha"]
            or not evidence["changes"]
        ):
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
        mutated["registered_assertions"].append(
            mutated["registered_assertions"][0]
        )
    return mutated


def read_text(root: Path, relative: str) -> str:
    path = root / relative
    try:
        if not path.is_file() or path.is_symlink():
            raise AssertionFailure(f"member artifact {relative!r} is unavailable")
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise AssertionFailure(f"member artifact {relative!r} is unavailable") from error


def normalized_source(root: Path, relative: str) -> str:
    return " ".join(read_text(root, relative).split())


def load_json_file(root: Path, relative: str) -> Any:
    try:
        return json.loads(read_text(root, relative), object_pairs_hook=object_no_duplicates)
    except json.JSONDecodeError as error:
        raise AssertionFailure(f"member artifact {relative!r} is not valid JSON") from error


def load_python_ast(root: Path, relative: str) -> ast.Module:
    try:
        return ast.parse(read_text(root, relative), filename=relative)
    except SyntaxError as error:
        raise AssertionFailure(f"member artifact {relative!r} is not valid Python") from error


def assign_string_sequence(module: ast.Module, name: str) -> tuple[str, ...]:
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in statement.targets):
            value = statement.value
            if not isinstance(value, (ast.Tuple, ast.List)):
                break
            items = []
            for element in value.elts:
                if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                    raise AssertionFailure(f"{name} must be a string sequence")
                items.append(element.value)
            return tuple(items)
    raise AssertionFailure(f"{name} is unavailable")


def assign_string_constant(module: ast.Module, name: str) -> str:
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in statement.targets):
            if isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                return statement.value.value
            raise AssertionFailure(f"{name} must be a string constant")
    raise AssertionFailure(f"{name} is unavailable")


def class_annotation_fields(module: ast.Module, name: str) -> tuple[str, ...]:
    for statement in module.body:
        if not isinstance(statement, ast.ClassDef) or statement.name != name:
            continue
        fields = []
        for item in statement.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields.append(item.target.id)
        if not fields:
            raise AssertionFailure(f"{name} has no annotated fields")
        return tuple(fields)
    raise AssertionFailure(f"{name} is unavailable")


def function_string_constants(module: ast.Module, name: str) -> set[str]:
    for statement in module.body:
        if isinstance(statement, ast.FunctionDef) and statement.name == name:
            return {
                node.value
                for node in ast.walk(statement)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
    raise AssertionFailure(f"{name} is unavailable")


def count_fragment(text: str, fragment: str) -> int:
    return text.count(" ".join(fragment.split()))


def workflow_registry(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    registry = expect_object(load_json_file(root, "docs/test-cases/registry.json"), "workflow registry")
    features = expect_object(
        {item["id"]: item for item in expect_object(registry, "workflow registry")["features"]},
        "workflow registry features",
    )
    cases = expect_object(
        {item["id"]: item for item in registry["cases"]},
        "workflow registry cases",
    )
    feature = features.get(WORKFLOW_FEATURE_ID)
    case = cases.get(WORKFLOW_REVIEW_FAMILY_CASE)
    if feature is None or case is None:
        raise AssertionFailure("workflow-governance registry coverage is incomplete")
    return feature, case


def evaluate_action_actions(root: Path) -> dict[str, Any]:
    checker = load_python_ast(root, "scripts/workflow_pilot/review_base_checker.py")
    family = load_python_ast(root, "scripts/workflow_pilot/review_family.py")
    producer = assign_string_sequence(checker, "ACTION_SEQUENCE")
    consumer = assign_string_sequence(family, "READ_ONLY_ACTIONS")
    expected = ("read-candidate", "emit-local-report")
    if producer != expected or consumer != expected:
        raise AssertionFailure("read-only action sequence is not exact")
    return {"sequence": list(producer)}


def evaluate_action_items(root: Path) -> dict[str, Any]:
    checker = normalized_source(root, "scripts/workflow_pilot/review_base_checker.py")
    assertions = normalized_source(root, "scripts/workflow_pilot/review_assertions.py")
    required_checker = (
        '"finding_member": parsed["member"]',
        '"authority_binding": authority_binding',
    )
    required_assertions = (
        'binding["finding_member"] != member',
        '"finding_member": member',
    )
    if any(fragment not in checker for fragment in required_checker):
        raise AssertionFailure("member-item authority binding is incomplete")
    if any(fragment not in assertions for fragment in required_assertions):
        raise AssertionFailure("member-item assertion does not validate the bound member")
    return {"checker_binding": True, "assertion_binding": True}


def evaluate_action_targets(root: Path) -> dict[str, Any]:
    checker = load_python_ast(root, "scripts/workflow_pilot/review_base_checker.py")
    family = load_python_ast(root, "scripts/workflow_pilot/review_family.py")
    checker_statuses = function_string_constants(checker, "validate_change_records") & {
        "A",
        "D",
        "M",
        "R",
        "C",
    }
    family_statuses = function_string_constants(family, "derive_change_records") & {
        "A",
        "D",
        "M",
        "R",
        "C",
    }
    coverage = normalized_source(root, "scripts/workflow_pilot/review_base_checker.py")
    if checker_statuses != {"A", "D", "M", "R", "C"} or family_statuses != {
        "A",
        "D",
        "M",
        "R",
        "C",
    }:
        raise AssertionFailure("status-aware target coverage is incomplete")
    if "independent review does not cover every exact changed file" not in coverage:
        raise AssertionFailure("exact changed-file coverage is not enforced")
    return {"statuses": sorted(checker_statuses)}


def evaluate_generated_owners(root: Path) -> dict[str, Any]:
    feature, _ = workflow_registry(root)
    issue_urls = sorted(feature["issue_urls"])
    required_cases = sorted(feature["required_cases"])
    if CURRENT_IMPLEMENTATION_ISSUE not in issue_urls:
        raise AssertionFailure("workflow-governance registry does not claim issue #179")
    if WORKFLOW_REVIEW_FAMILY_CASE not in required_cases:
        raise AssertionFailure("workflow-governance registry does not include the review-family case")
    return {"issue_urls": issue_urls, "required_cases": required_cases}


def evaluate_generated_outputs(root: Path) -> dict[str, Any]:
    candidate = load_python_ast(root, "scripts/workflow_pilot/candidate_evidence.py")
    classifier = load_python_ast(root, "scripts/workflow_pilot/event_classifier.py")
    worker_job_ids = assign_string_sequence(candidate, "WORKER_JOB_IDS")
    full_classifier = assign_string_constant(candidate, "FULL_CLASSIFIER")
    full_attestation = assign_string_constant(candidate, "FULL_ATTESTATION")
    metadata_classifier = assign_string_constant(candidate, "METADATA_CLASSIFIER")
    metadata_attestation = assign_string_constant(candidate, "METADATA_ATTESTATION")
    decision_fields = class_annotation_fields(classifier, "EventDecision")
    if set(worker_job_ids) != {"host-tests", "build", "extended-host-tests", "legacy"}:
        raise AssertionFailure("candidate-evidence worker outputs are incomplete")
    if (
        full_classifier != "event-classifier"
        or full_attestation != "summary"
        or metadata_classifier != "metadata-classifier"
        or metadata_attestation != "metadata-summary"
    ):
        raise AssertionFailure("candidate-evidence output attestations are inconsistent")
    if set(decision_fields) != {
        "classification",
        "expected_base",
        "reason",
        "run_expensive",
        "expected_head",
        "full_fallback",
        "head_valid",
        "identity_valid",
    }:
        raise AssertionFailure("event-classifier output fields are incomplete")
    return {
        "workers": list(worker_job_ids),
        "decision_fields": list(decision_fields),
    }


def evaluate_generated_consumers(root: Path) -> dict[str, Any]:
    topology = normalized_source(root, "tests/workflows/test_build_ci_topology.py")
    if "candidate_evidence" not in topology or "event_classifier" not in topology:
        raise AssertionFailure("workflow topology tests do not consume candidate evidence and classifier outputs")
    required = (
        "metadata-only",
        "CANDIDATE_FULL_JOBS",
        "WORKFLOW_PILOT_BASELINE_GATE",
        "event_classifier.classify_event",
    )
    if any(fragment not in topology for fragment in required):
        raise AssertionFailure("workflow topology tests do not consume the generated outputs")
    return {"topology_consumer": True}


def evaluate_generated_drift_checks(root: Path) -> dict[str, Any]:
    docs = normalized_source(root, "scripts/docs_check_tests/test_check_docs.py")
    skill = normalized_source(root, "scripts/docs_check_tests/test_development_workflow_skill.py")
    if WORKFLOW_REVIEW_FAMILY_CASE not in docs or WORKFLOW_REVIEW_FAMILY_CASE not in skill:
        raise AssertionFailure("docs drift checks do not cover the review-family case")
    if WORKFLOW_FEATURE_ID not in docs or WORKFLOW_FEATURE_ID not in skill:
        raise AssertionFailure("docs drift checks do not cover workflow-governance")
    return {"docs_checks": True}


def evaluate_lifecycle_entries(root: Path) -> dict[str, Any]:
    source = normalized_source(root, "scripts/workflow_pilot/review_family.py")
    required = (
        "third-consecutive-change-request",
        "finding_handoffs",
        '"bounds": {',
        '"findings": len(finding_sweeps)',
        '"families": len(',
        '"siblings": sum(',
    )
    if any(fragment not in source for fragment in required):
        raise AssertionFailure("lifecycle hold-entry contract is incomplete")
    return {"hold_reason": "third-consecutive-change-request"}


def evaluate_lifecycle_preservation(root: Path) -> dict[str, Any]:
    source = normalized_source(root, "scripts/workflow_pilot/trusted_review_gate.py")
    required = (
        "_preserved_receipt_bytes",
        "preserved original pre-review is unavailable",
        "pre-review receipt changed during consumption",
        "original_receipt_sha256",
    )
    if any(fragment not in source for fragment in required):
        raise AssertionFailure("receipt preservation contract is incomplete")
    return {"preserved_receipt": True}


def evaluate_lifecycle_resets(root: Path) -> dict[str, Any]:
    source = normalized_source(root, "scripts/workflow_pilot/review_family.py")
    if count_fragment(source, "consecutive = 0") < 2 or count_fragment(source, "pending = None") < 2:
        raise AssertionFailure("lifecycle reset paths are incomplete")
    return {"resets": 2}


def evaluate_lifecycle_terminals(root: Path) -> dict[str, Any]:
    family = normalized_source(root, "scripts/workflow_pilot/review_family.py")
    trusted = normalized_source(root, "scripts/workflow_pilot/trusted_review_gate.py")
    required = (
        '"push_allowed": False',
        '"trusted_push_allowed": False',
        '"merge_allowed": False',
        '"structural_eligibility"',
    )
    if any(fragment not in trusted and fragment not in family for fragment in required):
        raise AssertionFailure("terminal gate contract is incomplete")
    return {"terminal_gates": True}


def evaluate_resource_enabled(root: Path) -> dict[str, Any]:
    decisions = expect_object(load_json_file(root, ".github/workflow-pilot-decisions.json"), "decision record")
    pull_requests = decisions.get("pull_requests")
    if not isinstance(pull_requests, list):
        raise AssertionFailure("decision record.pull_requests must be a list")
    matches = []
    for item in pull_requests:
        if not isinstance(item, dict):
            continue
        threshold = item.get("threshold")
        if not isinstance(threshold, dict):
            continue
        risks = sorted(item.get("risk_boundaries", []))
        triggers = sorted(threshold.get("triggers", []))
        if risks == ["lifecycle", "protocol"] and triggers == [
            "changed-files",
            "risk-boundary",
        ]:
            matches.append(item)
    if len(matches) != 1:
        raise AssertionFailure(
            "authoritative decision record does not contain one exact high-risk review-family entry"
        )
    threshold = expect_object(matches[0]["threshold"], "decision threshold")
    trigger = {
        "risk_boundaries": sorted(matches[0]["risk_boundaries"]),
        "threshold_triggers": sorted(threshold["triggers"]),
    }
    trusted = normalized_source(root, "scripts/workflow_pilot/trusted_review_gate.py")
    family = normalized_source(root, "scripts/workflow_pilot/review_family.py")
    if "_load_authoritative_trigger" not in trusted or "authoritative_trigger" not in family:
        raise AssertionFailure("enabled resource boundary does not consume the authoritative trigger")
    if not (
        trigger["risk_boundaries"] == ["lifecycle", "protocol"]
        and trigger["threshold_triggers"] == ["changed-files", "risk-boundary"]
    ):
        raise AssertionFailure("PR 189 authoritative trigger decision is incomplete")
    return trigger


def evaluate_resource_disabled(root: Path) -> dict[str, Any]:
    source = normalized_source(root, "scripts/workflow_pilot/trusted_review_gate.py")
    required = (
        '"mode": "introduction"',
        '"external_coordinator_review_required": True',
        '"trusted_push_allowed": False',
        '"merge_allowed": False',
    )
    if any(fragment not in source for fragment in required):
        raise AssertionFailure("introduction-mode disabled boundary is incomplete")
    return {"introduction_mode": True}


def evaluate_wire_producers(root: Path) -> dict[str, Any]:
    source = normalized_source(root, "scripts/workflow_pilot/trusted_review_gate.py")
    required = (
        "collect_live_evidence_bytes",
        "run_base_pinned_checker",
        '"authoritative_trigger": authoritative_trigger',
        '"execution_receipts": execution_receipts',
        '"result_manifest": [',
    )
    if any(fragment not in source for fragment in required):
        raise AssertionFailure("wire producers are incomplete")
    return {"producers": True}


def evaluate_wire_consumers(root: Path) -> dict[str, Any]:
    source = normalized_source(root, "scripts/workflow_pilot/review_family.py")
    required = (
        "validate_evidence",
        '"authoritative_trigger"',
        '"execution_receipts"',
        '"result_manifest"',
        "_validate_execution",
    )
    if any(fragment not in source for fragment in required):
        raise AssertionFailure("wire consumers are incomplete")
    return {"consumers": True}


def evaluate_wire_validators(root: Path) -> dict[str, Any]:
    checker = normalized_source(root, "scripts/workflow_pilot/review_base_checker.py")
    family = normalized_source(root, "scripts/workflow_pilot/review_family.py")
    if "validate_input" not in checker or "_validate_program_output_binding" not in checker:
        raise AssertionFailure("checker validators are incomplete")
    if "_validate_execution" not in family or "authoritative trigger decision" not in family:
        raise AssertionFailure("report validators are incomplete")
    return {"validators": True}


def evaluate_wire_replay(root: Path) -> dict[str, Any]:
    source = normalized_source(root, "scripts/workflow_pilot/trusted_review_gate.py")
    required = (
        "_verify_signed_receipt_bytes",
        "_preserved_receipt_bytes",
        "consume_nonce",
        "require_preserved",
        "_execution_receipt_seal",
    )
    if any(fragment not in source for fragment in required):
        raise AssertionFailure("replay boundary is incomplete")
    return {"replay": True}


def evaluate_wire_stale_bindings(root: Path) -> dict[str, Any]:
    trusted = normalized_source(root, "scripts/workflow_pilot/trusted_review_gate.py")
    checker = normalized_source(root, "scripts/workflow_pilot/review_base_checker.py")
    required_trusted = (
        "_live_state_digest",
        "GitHub head/review/thread state changed during gate evaluation",
        "authoritative PR head does not equal the expected remote head",
    )
    required_checker = (
        "current assertion round/head",
        "authoritative round binding",
    )
    if any(fragment not in trusted for fragment in required_trusted):
        raise AssertionFailure("trusted stale-binding checks are incomplete")
    if any(fragment not in checker for fragment in required_checker):
        raise AssertionFailure("checker stale-binding checks are incomplete")
    return {"stale_bindings": True}


MEMBER_EVALUATORS = {
    ("action", "actions"): evaluate_action_actions,
    ("action", "items"): evaluate_action_items,
    ("action", "targets"): evaluate_action_targets,
    ("generated", "owners"): evaluate_generated_owners,
    ("generated", "outputs"): evaluate_generated_outputs,
    ("generated", "consumers"): evaluate_generated_consumers,
    ("generated", "drift-checks"): evaluate_generated_drift_checks,
    ("lifecycle", "entries"): evaluate_lifecycle_entries,
    ("lifecycle", "preservation"): evaluate_lifecycle_preservation,
    ("lifecycle", "resets"): evaluate_lifecycle_resets,
    ("lifecycle", "terminals"): evaluate_lifecycle_terminals,
    ("resource", "enabled"): evaluate_resource_enabled,
    ("resource", "disabled"): evaluate_resource_disabled,
    ("wire", "producers"): evaluate_wire_producers,
    ("wire", "consumers"): evaluate_wire_consumers,
    ("wire", "validators"): evaluate_wire_validators,
    ("wire", "replay"): evaluate_wire_replay,
    ("wire", "stale-bindings"): evaluate_wire_stale_bindings,
}


def execute_behavior(
    assertion: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
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


def evaluate_member_contract(family: str, member: str, root: Path) -> dict[str, Any]:
    expected = {path for path in ASSERTION_INPUT_PATHS}
    discovered = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise AssertionFailure("member artifact tree contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise AssertionFailure("member artifact tree contains an unsafe entry")
        discovered.add(path.relative_to(root).as_posix())
    if discovered != expected:
        raise AssertionFailure("member artifact tree does not match the allowlisted production inputs")
    evaluator = MEMBER_EVALUATORS.get((family, member))
    if evaluator is None:
        raise AssertionFailure("member evaluator is not registered")
    return evaluator(root)


def execute_member(
    assertion: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
    expect_keys(
        request,
        "member request",
        (
            "assertion_id",
            "authority_binding",
            "origin_root",
            "head_root",
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
    origin_root = Path(request["origin_root"])
    head_root = Path(request["head_root"])
    outcome = assertion["outcome"]
    if outcome == "affected-fixed":
        try:
            evaluate_member_contract(family, member, origin_root)
        except AssertionFailure as error:
            origin_error = str(error)
        else:
            raise AssertionFailure(
                "affected-fixed origin assertion unexpectedly passed"
            )
        head_output = evaluate_member_contract(family, member, head_root)
        return {
            **binding_output,
            "program_case": f"member/{family}/{member}/affected-fixed",
            "origin_status": "fail",
            "origin_error": origin_error,
            "head_status": "pass",
            "head_semantic_output": head_output,
        }
    if outcome == "verified-unaffected":
        origin_output = evaluate_member_contract(family, member, origin_root)
        head_output = evaluate_member_contract(family, member, head_root)
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
    head_output = evaluate_member_contract(family, member, head_root)
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
