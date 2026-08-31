#!/usr/bin/env python3
"""Validate and explain the repository's fail-closed ownership graph."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from scripts import check_docs
from scripts.generated_data import registry as generated_data_registry
from scripts.upstream_port import verify as workflow_verify
from scripts.workflow_pilot import reporter as pilot_reporter


GRAPH_PATH = Path(".github/validation-ownership-graph.json")
SCHEMA_PATH = Path("scripts/validation_ownership/graph.schema.json")
TEST_CASE_REGISTRY_PATH = Path("docs/test-cases/registry.json")
BUILD_WORKFLOW_PATH = Path(".github/workflows/build.yml")
EXPECTED_SCHEMA_VERSION = 1
EDGE_SEAL_DOMAIN = b"validation-ownership-resolved-edges-v1\0"
GRAPH_SEAL_DOMAIN = b"validation-ownership-graph-v1\0"
SCHEMA_SEAL_DOMAIN = b"validation-ownership-schema-v1\0"
REQUIRED_PROOF_KINDS = {
    "artifact_checkpoint",
    "dependency_changed",
    "pre_graduation",
}
REQUIREMENT_EDGES = {
    "positive": {"owns-test"},
    "adversarial": {"adversarial-control"},
    "default-disabled": {"negative-control"},
    "compile": {"compile-owner"},
    "link": {"link-owner"},
    "runtime": {"target-scenario"},
    "generated": {"generated-by", "drift-check", "generated-consumer"},
    "shared-contract": {"dependent-profile", "negative-control"},
    "manual": {"manual-handoff"},
}
EDGE_TARGET_TYPES = {
    "owns-test": {"host"},
    "adversarial-control": {"host"},
    "compile-owner": {"compile"},
    "link-owner": {"link"},
    "target-scenario": {"runtime"},
    "generated-by": {"host"},
    "drift-check": {"host"},
    "generated-consumer": {"link"},
    "dependent-profile": {"compile", "runtime"},
    "negative-control": {"host", "compile", "link", "runtime"},
    "manual-handoff": {"manual"},
}


class OwnershipError(Exception):
    """The graph cannot prove a complete, unambiguous owner set."""


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OwnershipError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def parse_json(text: str, label: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_object_no_duplicates)
    except json.JSONDecodeError as error:
        raise OwnershipError(f"invalid JSON in {label}: {error}") from error


def load_json(path: Path) -> Any:
    try:
        return parse_json(path.read_text(encoding="utf-8"), str(path))
    except OSError as error:
        raise OwnershipError(f"cannot read {path}: {error}") from error


def normalized_json(value: Any) -> bytes:
    return pilot_reporter.normalized_json(value)


def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise OwnershipError(f"schema uses unsupported type {expected!r}")


def _resolve_schema_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise OwnershipError(f"schema uses unsupported reference {reference!r}")
    value: Any = root_schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise OwnershipError(f"schema reference {reference!r} does not resolve")
        value = value[part]
    if not isinstance(value, dict):
        raise OwnershipError(f"schema reference {reference!r} is not an object")
    return value


def validate_json_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    label: str = "graph",
) -> None:
    if "$ref" in schema:
        validate_json_schema(
            value,
            _resolve_schema_ref(root_schema, schema["$ref"]),
            root_schema,
            label,
        )
        return
    if "oneOf" in schema:
        matches = 0
        errors = []
        for candidate in schema["oneOf"]:
            try:
                validate_json_schema(value, candidate, root_schema, label)
            except OwnershipError as error:
                errors.append(str(error))
            else:
                matches += 1
        if matches != 1:
            detail = errors[0] if errors else "multiple branches matched"
            raise OwnershipError(
                f"{label} must match exactly one schema branch ({detail})"
            )
        return

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = [expected_type] if isinstance(expected_type, str) else expected_type
        if (
            not isinstance(allowed, list)
            or not allowed
            or not all(isinstance(item, str) for item in allowed)
        ):
            raise OwnershipError(f"schema type for {label} is malformed")
        if not any(_schema_type_matches(value, item) for item in allowed):
            raise OwnershipError(
                f"{label} must have type {' or '.join(allowed)}, got "
                f"{type(value).__name__}"
            )

    if "const" in schema and value != schema["const"]:
        raise OwnershipError(f"{label} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise OwnershipError(f"{label} has unknown value {value!r}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise OwnershipError(f"schema required list for {label} is malformed")
        missing = [key for key in required if key not in value]
        if missing:
            raise OwnershipError(f"{label} is missing required keys {missing}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise OwnershipError(f"schema properties for {label} are malformed")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise OwnershipError(f"{label} has unknown keys {extra}")
        for key, child in properties.items():
            if key in value:
                validate_json_schema(value[key], child, root_schema, f"{label}.{key}")

    if isinstance(value, list):
        minimum = schema.get("minItems")
        if minimum is not None and len(value) < minimum:
            raise OwnershipError(f"{label} must contain at least {minimum} items")
        if schema.get("uniqueItems"):
            identities = [normalized_json(item) for item in value]
            if len(identities) != len(set(identities)):
                raise OwnershipError(f"{label} must contain unique items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                validate_json_schema(
                    item, item_schema, root_schema, f"{label}[{index}]"
                )

    if isinstance(value, str):
        minimum = schema.get("minLength")
        if minimum is not None and len(value) < minimum:
            raise OwnershipError(f"{label} must contain at least {minimum} characters")
        pattern = schema.get("pattern")
        if pattern is not None:
            try:
                matched = re.search(pattern, value)
            except re.error as error:
                raise OwnershipError(
                    f"schema pattern for {label} is invalid: {error}"
                ) from error
            if matched is None:
                raise OwnershipError(f"{label} does not match {pattern!r}")

    if isinstance(value, int) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            raise OwnershipError(f"{label} must be at least {minimum}")


def _sha256(domain: bytes, value: Any) -> str:
    return hashlib.sha256(domain + normalized_json(value)).hexdigest()


def _read_regular_file(root: Path, relative: Path) -> bytes:
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise OwnershipError(f"required authority {relative} is unavailable: {error}") from error
    if path.is_symlink() or not resolved.is_file():
        raise OwnershipError(f"required authority {relative} must be a regular file")
    if root not in resolved.parents:
        raise OwnershipError(f"required authority {relative} escapes repository root")
    try:
        return resolved.read_bytes()
    except OSError as error:
        raise OwnershipError(f"cannot read authority {relative}: {error}") from error


def _git(
    root: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            pilot_reporter.git_command(root, *arguments),
            env=pilot_reporter.git_environment(offline=True),
            check=False,
            capture_output=True,
        )
    except (OSError, pilot_reporter.PilotDataError) as error:
        raise OwnershipError(f"cannot execute trusted Git: {error}") from error
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise OwnershipError(
            f"Git {' '.join(arguments)} failed"
            + (f": {detail}" if detail else "")
        )
    return completed


def validate_repository_root(root: Path) -> Path:
    try:
        return pilot_reporter.validate_repository_root(root)
    except pilot_reporter.PilotDataError as error:
        raise OwnershipError(str(error)) from error


def tracked_paths(root: Path) -> tuple[str, ...]:
    output = _git(root, "ls-files", "-z").stdout
    try:
        paths = output.decode("utf-8").split("\0")
    except UnicodeDecodeError as error:
        raise OwnershipError("Git tracked paths are not valid UTF-8") from error
    result = tuple(sorted(path for path in paths if path))
    if not result:
        raise OwnershipError("Git returned no tracked repository paths")
    if len(result) != len(set(result)):
        raise OwnershipError("Git returned duplicate tracked repository paths")
    return result


def repository_status(root: Path) -> bytes:
    return _git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    ).stdout


def _generated_registry_records(root: Path) -> tuple[list[dict[str, Any]], set[str]]:
    records = []
    paths: set[str] = set()
    registry = generated_data_registry.REGISTRY
    for name in registry.all_names():
        schema = registry.resolve(name)
        record = {
            "name": name,
            "version": schema.version,
            "default_source": schema.default_source,
            "default_hand_source": schema.default_hand_source,
            "default_output_name": schema.default_output_name,
            "default_inventory_path": schema.default_inventory_path,
            "dependencies": sorted(schema.dependencies()),
            "dependency_tables": list(schema.dependency_tables()),
        }
        records.append(record)
        for field in (
            "default_source",
            "default_hand_source",
            "default_inventory_path",
        ):
            candidate = record[field]
            if candidate is None:
                continue
            if not isinstance(candidate, str) or not candidate:
                raise OwnershipError(
                    f"generated-data schema {name!r} has malformed {field}"
                )
            path = root / candidate
            if path.is_file():
                paths.add(candidate)
            elif not path.is_dir():
                raise OwnershipError(
                    f"generated-data schema {name!r} references stale {field} "
                    f"{candidate!r}"
                )
    return records, paths


def _selector_identity(selector: dict[str, Any]) -> tuple[str, str]:
    return selector["kind"], selector.get("path", "")


def _selector_matches(
    selector: dict[str, Any],
    path: str,
    generated_paths: set[str],
) -> bool:
    kind = selector["kind"]
    if kind == "exact":
        return path == selector["path"]
    if kind == "prefix":
        return path.startswith(selector["path"])
    if kind == "generated-data-registry":
        return path in generated_paths
    raise OwnershipError(f"unknown selector kind {kind!r}")


def _path_rule_matches(
    rule: dict[str, Any],
    path: str,
    generated_paths: set[str],
) -> bool:
    return any(
        _selector_matches(selector, path, generated_paths)
        for selector in rule["include"]
    ) and not any(
        _selector_matches(selector, path, generated_paths)
        for selector in rule.get("exclude", [])
    )


def _load_test_case_registry(root: Path) -> dict[str, dict[str, Any]]:
    registry = load_json(root / TEST_CASE_REGISTRY_PATH)
    if not isinstance(registry, dict) or not isinstance(registry.get("cases"), list):
        raise OwnershipError("tester-case registry lacks a cases array")
    result = {}
    for index, case in enumerate(registry["cases"]):
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise OwnershipError(f"tester-case registry case {index} is malformed")
        case_id = case["id"]
        if case_id in result:
            raise OwnershipError(f"tester-case registry duplicates {case_id!r}")
        result[case_id] = case
    return result


def _workflow_authorities(
    root: Path,
) -> tuple[dict[str, Any], dict[tuple[str, str], Any]]:
    text = _read_regular_file(root, BUILD_WORKFLOW_PATH).decode("utf-8")
    try:
        _, _, jobs = workflow_verify._parse_workflow_structure_text(text)
    except (UnicodeError, ValueError) as error:
        raise OwnershipError(f"Build workflow authority is invalid: {error}") from error
    job_records = {}
    step_records = {}
    for job_name, context, steps in jobs:
        job_records[job_name] = {"context": context, "steps": steps}
        for role, step_name, fields in steps:
            if step_name is not None:
                step_records[(job_name, step_name)] = {
                    "role": role,
                    "fields": fields,
                }
    return job_records, step_records


def _manual_handoff_record(root: Path, relative: str) -> dict[str, Any]:
    record = load_json(root / relative)
    try:
        schema = record["schema"]
        eligibility = record["eligibility"]
        pre_handoff = record["pre_handoff"]
    except (KeyError, TypeError) as error:
        raise OwnershipError("manual handoff contract lacks required structure") from error
    if schema != "fe8.manual-testing-handoff.v1":
        raise OwnershipError("manual handoff contract has unknown schema")
    if set(eligibility.get("kinds", [])) != {"visual", "audio", "ux"}:
        raise OwnershipError("manual handoff kinds must be visual, audio, and ux")
    if eligibility.get("deterministic_criteria") is not False:
        raise OwnershipError("manual handoff cannot own deterministic criteria")
    if pre_handoff.get("semantic_assertions_primary") is not True:
        raise OwnershipError("manual handoff must keep semantic assertions primary")
    return record


def _make_authority_digest(root: Path) -> str:
    paths = []
    for path in tracked_paths(root):
        name = Path(path).name
        if path == "Makefile" or name.endswith(".mk"):
            paths.append(path)
    return digest_paths(root, paths, b"validation-ownership-make-authority-v1\0")


def digest_paths(root: Path, paths: Iterable[str], domain: bytes) -> str:
    digest = hashlib.sha256(domain)
    for relative in sorted(paths):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_read_regular_file(root, Path(relative)))
        digest.update(b"\0")
    return digest.hexdigest()


def _authority_identity(authority: dict[str, Any]) -> tuple[str, ...]:
    kind = authority["kind"]
    if kind == "make-target":
        return kind, authority["target"]
    if kind == "workflow-job":
        return kind, authority["job"]
    if kind == "workflow-step":
        return kind, authority["job"], authority["step"]
    if kind == "tester-case":
        return kind, authority["case_id"]
    if kind == "manual-handoff":
        return kind, authority["path"]
    if kind == "generated-data-registry":
        return (kind,)
    raise OwnershipError(f"unknown evidence authority kind {kind!r}")


def _validate_authorities(
    root: Path,
    evidence_nodes: dict[str, dict[str, Any]],
    generated_records: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    literal_targets, pattern_targets = check_docs.parse_make_targets(str(root))
    workflow_jobs, workflow_steps = _workflow_authorities(root)
    tester_cases = _load_test_case_registry(root)
    make_digest = _make_authority_digest(root)
    identities = set()
    result = {}
    for node_id, node in evidence_nodes.items():
        authority = node["authority"]
        identity = _authority_identity(authority)
        if identity in identities:
            raise OwnershipError(
                f"evidence node {node_id!r} duplicates authority {identity!r}"
            )
        identities.add(identity)
        kind = authority["kind"]
        if kind == "make-target":
            target = authority["target"]
            if not check_docs.make_target_exists(
                target, literal_targets, pattern_targets
            ):
                raise OwnershipError(
                    f"evidence node {node_id!r} references stale Make target "
                    f"{target!r}"
                )
            fingerprint = _sha256(
                b"validation-ownership-make-target-v1\0",
                {"target": target, "make_authority": make_digest},
            )
            display = f"make {target}"
        elif kind == "workflow-job":
            job = authority["job"]
            if job not in workflow_jobs:
                raise OwnershipError(
                    f"evidence node {node_id!r} references stale workflow job {job!r}"
                )
            fingerprint = _sha256(
                b"validation-ownership-workflow-job-v1\0",
                {"job": job, "record": workflow_jobs[job]},
            )
            display = f"{BUILD_WORKFLOW_PATH}:{job}"
        elif kind == "workflow-step":
            key = authority["job"], authority["step"]
            if key not in workflow_steps:
                raise OwnershipError(
                    f"evidence node {node_id!r} references stale workflow step "
                    f"{key!r}"
                )
            fingerprint = _sha256(
                b"validation-ownership-workflow-step-v1\0",
                {"job": key[0], "step": key[1], "record": workflow_steps[key]},
            )
            display = f"{BUILD_WORKFLOW_PATH}:{key[0]}:{key[1]}"
        elif kind == "tester-case":
            case_id = authority["case_id"]
            if case_id not in tester_cases:
                raise OwnershipError(
                    f"evidence node {node_id!r} references stale tester case "
                    f"{case_id!r}"
                )
            fingerprint = _sha256(
                b"validation-ownership-tester-case-v1\0",
                tester_cases[case_id],
            )
            display = f"{TEST_CASE_REGISTRY_PATH}:{case_id}"
        elif kind == "manual-handoff":
            record = _manual_handoff_record(root, authority["path"])
            fingerprint = _sha256(
                b"validation-ownership-manual-handoff-v1\0", record
            )
            display = authority["path"]
        elif kind == "generated-data-registry":
            fingerprint = _sha256(
                b"validation-ownership-generated-registry-v1\0",
                generated_records,
            )
            display = "scripts.generated_data.registry:REGISTRY"
        else:
            raise OwnershipError(f"unknown authority kind {kind!r}")
        result[node_id] = {
            "display": display,
            "fingerprint": fingerprint,
        }
    return result


def _validate_lifecycle(
    artifact: dict[str, Any],
    evidence_nodes: dict[str, dict[str, Any]],
) -> None:
    if (
        artifact["estimated_maintenance_minutes"]
        > artifact["max_maintenance_minutes"]
    ):
        raise OwnershipError("artifact exceeds its bounded maintenance cost")
    histories = artifact["history"]
    previous = None
    for index, history in enumerate(histories):
        if history["disposition"] not in pilot_reporter.ARTIFACT_DISPOSITIONS:
            raise OwnershipError(
                f"artifact history {index} has unknown disposition"
            )
        try:
            recorded = pilot_reporter.parse_time(
                history["recorded_at"], f"artifact.history[{index}].recorded_at"
            )
        except pilot_reporter.PilotDataError as error:
            raise OwnershipError(str(error)) from error
        if previous is not None and recorded <= previous:
            raise OwnershipError("artifact history is not strictly chronological")
        previous = recorded
    current = histories[-1]["disposition"]
    if artifact["expires_at"] is not None:
        try:
            expiry = pilot_reporter.parse_time(
                artifact["expires_at"], "artifact.expires_at"
            )
        except pilot_reporter.PilotDataError as error:
            raise OwnershipError(str(error)) from error
        if previous is not None and expiry <= previous and current != "Delete":
            raise OwnershipError("expired artifact is not deleted")

    proofs = artifact["lifecycle_proofs"]
    kinds = {proof["kind"] for proof in proofs}
    if kinds != REQUIRED_PROOF_KINDS or len(proofs) != len(REQUIRED_PROOF_KINDS):
        raise OwnershipError(
            "artifact lifecycle requires exactly one checkpoint, dependency "
            "change, and pre-graduation proof"
        )
    reasons = {proof["reason"] for proof in proofs}
    if len(reasons) != 1:
        raise OwnershipError("artifact lifecycle proofs have mixed reasons")
    required_semantic = "pass" if current == "Delete" else "fail"
    for proof in proofs:
        try:
            occurred = pilot_reporter.parse_time(
                proof["occurred_at"], f"artifact proof {proof['id']}.occurred_at"
            )
        except pilot_reporter.PilotDataError as error:
            raise OwnershipError(str(error)) from error
        if previous is not None and occurred >= previous:
            raise OwnershipError(
                "artifact disposition must strictly follow every lifecycle proof"
            )
        if proof["semantic_result"] != required_semantic:
            raise OwnershipError(
                f"artifact proof {proof['id']!r} contradicts disposition {current!r}"
            )
        if proof["restored_result"] != "pass":
            raise OwnershipError(
                f"artifact proof {proof['id']!r} did not restore successfully"
            )

    consumers = [
        node
        for node in evidence_nodes.values()
        if node["authority"]["kind"] == "make-target"
        and node["authority"]["target"] == artifact["executable_consumer"]
    ]
    checks = [
        node
        for node in evidence_nodes.values()
        if node["authority"]["kind"] == "tester-case"
        and node["authority"]["case_id"] == artifact["consistency_check"]
    ]
    if len(consumers) != 1:
        raise OwnershipError(
            "artifact must have exactly one executable Make consumer authority"
        )
    if len(checks) != 1:
        raise OwnershipError(
            "artifact must have exactly one tester-case consistency authority"
        )


def _detect_dependency_cycles(
    surfaces: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    dependencies: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge["type"] == "depends-on":
            dependencies[edge["source"]].append(edge["target"])
    states: dict[str, str] = {}

    def visit(node_id: str, trail: tuple[str, ...]) -> None:
        state = states.get(node_id)
        if state == "visiting":
            cycle = " -> ".join(trail + (node_id,))
            raise OwnershipError(f"surface dependency cycle has no unique owner: {cycle}")
        if state == "visited":
            return
        states[node_id] = "visiting"
        for target in dependencies[node_id]:
            if target not in surfaces:
                raise OwnershipError(
                    f"surface dependency {node_id!r} targets non-surface {target!r}"
                )
            visit(target, trail + (node_id,))
        states[node_id] = "visited"

    for node_id in surfaces:
        visit(node_id, ())


def _required_edge_types(surface: dict[str, Any]) -> set[str]:
    required = set()
    for requirement in surface["requirements"]:
        required.update(REQUIREMENT_EDGES[requirement])
    return required


def _validate_semantics(
    graph: dict[str, Any],
    root: Path,
    paths: tuple[str, ...],
) -> dict[str, Any]:
    if graph["schema_version"] != EXPECTED_SCHEMA_VERSION:
        raise OwnershipError(
            f"graph schema_version must be {EXPECTED_SCHEMA_VERSION}"
        )
    if graph["policy"] != {
        "classification": "framework-capability",
        "validation_effect": "report-only",
        "narrowing_authorized": False,
        "review_invalidation": "resolved-edge-authority",
    }:
        raise OwnershipError("graph policy must remain report-only and non-narrowing")

    nodes: dict[str, dict[str, Any]] = {}
    surfaces = {}
    evidence_nodes = {}
    for node in graph["nodes"]:
        node_id = node["id"]
        if node_id in nodes:
            raise OwnershipError(f"duplicate graph node {node_id!r}")
        nodes[node_id] = node
        if node["kind"] == "surface":
            surfaces[node_id] = node
            if node["surface_type"] == "manual" and "manual" not in node["requirements"]:
                raise OwnershipError(f"manual surface {node_id!r} lacks manual requirement")
            if node["surface_type"] != "manual" and "manual" in node["requirements"]:
                raise OwnershipError(
                    f"non-manual surface {node_id!r} claims manual ownership"
                )
        else:
            evidence_nodes[node_id] = node
    if not surfaces or not evidence_nodes:
        raise OwnershipError("graph requires both surface and evidence nodes")

    edge_ids = set()
    edge_identities = set()
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in graph["edges"]:
        edge_id = edge["id"]
        if edge_id in edge_ids:
            raise OwnershipError(f"duplicate graph edge ID {edge_id!r}")
        edge_ids.add(edge_id)
        identity = edge["type"], edge["source"], edge["target"]
        if identity in edge_identities:
            raise OwnershipError(f"duplicate graph edge owner {identity!r}")
        edge_identities.add(identity)
        if edge["source"] not in surfaces:
            raise OwnershipError(
                f"edge {edge_id!r} source must be a surface node"
            )
        if edge["target"] not in nodes:
            raise OwnershipError(
                f"edge {edge_id!r} references missing target {edge['target']!r}"
            )
        if edge["source"] == edge["target"]:
            raise OwnershipError(f"edge {edge_id!r} cannot be self-referential")
        if edge["type"] == "depends-on":
            if edge["target"] not in surfaces:
                raise OwnershipError(
                    f"edge {edge_id!r} dependency target must be a surface"
                )
        else:
            target = evidence_nodes.get(edge["target"])
            if target is None:
                raise OwnershipError(
                    f"edge {edge_id!r} owner target must be evidence"
                )
            allowed = EDGE_TARGET_TYPES[edge["type"]]
            if target["evidence_type"] not in allowed:
                raise OwnershipError(
                    f"edge {edge_id!r} requires evidence type "
                    f"{sorted(allowed)}, got {target['evidence_type']!r}"
                )
        outgoing[edge["source"]].append(edge)

    _detect_dependency_cycles(surfaces, graph["edges"])
    for surface_id, surface in surfaces.items():
        actual_dependencies = {
            edge["target"]
            for edge in outgoing[surface_id]
            if edge["type"] == "depends-on"
        }
        declared_dependencies = set(surface["dependencies"])
        if actual_dependencies != declared_dependencies:
            raise OwnershipError(
                f"surface {surface_id!r} dependency edges do not match its "
                f"declared dependencies (missing={sorted(declared_dependencies - actual_dependencies)}, "
                f"extra={sorted(actual_dependencies - declared_dependencies)})"
            )
        direct = [
            edge for edge in outgoing[surface_id] if edge["type"] != "depends-on"
        ]
        counts: dict[str, int] = defaultdict(int)
        for edge in direct:
            counts[edge["type"]] += 1
        duplicates = sorted(kind for kind, count in counts.items() if count > 1)
        if duplicates:
            raise OwnershipError(
                f"surface {surface_id!r} has ambiguous owners for {duplicates}"
            )
        required = _required_edge_types(surface)
        missing = sorted(required - set(counts))
        if missing:
            raise OwnershipError(
                f"surface {surface_id!r} is missing owner edges {missing}"
            )
        extra = sorted(set(counts) - required)
        if extra:
            raise OwnershipError(
                f"surface {surface_id!r} has inapplicable owner edges {extra}"
            )
        if surface["surface_type"] == "manual":
            deterministic = {
                "owns-test",
                "adversarial-control",
                "compile-owner",
                "link-owner",
                "target-scenario",
            }
            missing_deterministic = sorted(deterministic - set(counts))
            if missing_deterministic:
                raise OwnershipError(
                    f"manual surface {surface_id!r} cannot replace deterministic "
                    f"evidence; missing {missing_deterministic}"
                )

    generated_records, generated_paths = _generated_registry_records(root)
    authorities = _validate_authorities(root, evidence_nodes, generated_records)
    _validate_lifecycle(graph["artifact"], evidence_nodes)

    rule_ids = set()
    for rule in graph["path_rules"]:
        if rule["id"] in rule_ids:
            raise OwnershipError(f"duplicate path rule {rule['id']!r}")
        rule_ids.add(rule["id"])
        if rule["surface"] not in surfaces:
            raise OwnershipError(
                f"path rule {rule['id']!r} references missing surface"
            )
        selectors = rule["include"] + rule["exclude"]
        identities = [_selector_identity(selector) for selector in selectors]
        if len(identities) != len(set(identities)):
            raise OwnershipError(
                f"path rule {rule['id']!r} duplicates a selector"
            )

    exclusion_ids = set()
    for exclusion in graph["exclusions"]:
        if exclusion["id"] in exclusion_ids or exclusion["id"] in rule_ids:
            raise OwnershipError(f"duplicate exclusion {exclusion['id']!r}")
        exclusion_ids.add(exclusion["id"])

    coverage = {}
    for path in paths:
        matches = [
            rule
            for rule in graph["path_rules"]
            if _path_rule_matches(rule, path, generated_paths)
        ]
        excluded = [
            exclusion
            for exclusion in graph["exclusions"]
            if any(
                _selector_matches(selector, path, generated_paths)
                for selector in exclusion["include"]
            )
        ]
        if len(matches) + len(excluded) == 0:
            raise OwnershipError(f"tracked path {path!r} has no ownership contract")
        if len(matches) + len(excluded) > 1:
            identities = [rule["id"] for rule in matches] + [
                exclusion["id"] for exclusion in excluded
            ]
            raise OwnershipError(
                f"tracked path {path!r} has ambiguous ownership {identities}"
            )
        if matches:
            coverage[path] = {
                "kind": "owned",
                "rule": matches[0]["id"],
                "surface": matches[0]["surface"],
            }
        else:
            coverage[path] = {
                "kind": "excluded",
                "exclusion": excluded[0]["id"],
                "reason": excluded[0]["reason"],
            }

    return {
        "nodes": nodes,
        "surfaces": surfaces,
        "evidence": evidence_nodes,
        "outgoing": outgoing,
        "authorities": authorities,
        "generated_paths": generated_paths,
        "coverage": coverage,
    }


def validate_graph(
    graph: dict[str, Any],
    schema: dict[str, Any],
    root: Path,
    paths: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise OwnershipError("graph schema must be an object")
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise OwnershipError("graph schema must use JSON Schema draft 2020-12")
    validate_json_schema(graph, schema, schema)
    return _validate_semantics(graph, root, paths)


def _resolved_edges(
    graph: dict[str, Any],
    model: dict[str, Any],
) -> list[dict[str, Any]]:
    records = []
    for edge in sorted(graph["edges"], key=lambda item: item["id"]):
        record = copy.deepcopy(edge)
        authority = model["authorities"].get(edge["target"])
        if authority is not None:
            record["target_authority"] = authority
        records.append(record)
    return records


def edge_declaration_records(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = {node["id"]: node for node in graph.get("nodes", []) if isinstance(node, dict)}
    records = []
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict):
            continue
        target = nodes.get(edge.get("target"))
        record = copy.deepcopy(edge)
        if isinstance(target, dict) and target.get("kind") == "evidence":
            record["target_evidence_type"] = target.get("evidence_type")
            record["target_authority"] = target.get("authority")
        records.append(record)
    return sorted(records, key=lambda item: item.get("id", ""))


def compare_graph_edges(
    current: dict[str, Any],
    prior: dict[str, Any] | None,
    authority_changed_edge_ids: Iterable[str] = (),
) -> dict[str, Any]:
    if prior is None:
        return {
            "invalidated": True,
            "reason": "ownership-graph-introduced",
            "changed_edge_ids": sorted(
                edge["id"] for edge in current["edges"]
            ),
        }
    current_records = {
        record["id"]: record for record in edge_declaration_records(current)
    }
    prior_records = {
        record["id"]: record for record in edge_declaration_records(prior)
    }
    changed = sorted(
        edge_id
        for edge_id in set(current_records) | set(prior_records)
        if current_records.get(edge_id) != prior_records.get(edge_id)
    )
    current_surfaces = {
        node["id"]: {
            "surface_type": node.get("surface_type"),
            "requirements": node.get("requirements"),
            "dependencies": node.get("dependencies"),
        }
        for node in current.get("nodes", [])
        if isinstance(node, dict) and node.get("kind") == "surface"
    }
    prior_surfaces = {
        node["id"]: {
            "surface_type": node.get("surface_type"),
            "requirements": node.get("requirements"),
            "dependencies": node.get("dependencies"),
        }
        for node in prior.get("nodes", [])
        if isinstance(node, dict) and node.get("kind") == "surface"
    }
    changed_surfaces = {
        surface_id
        for surface_id in set(current_surfaces) | set(prior_surfaces)
        if current_surfaces.get(surface_id) != prior_surfaces.get(surface_id)
    }
    current_rules = {
        rule.get("id"): rule
        for rule in current.get("path_rules", [])
        if isinstance(rule, dict)
    }
    prior_rules = {
        rule.get("id"): rule
        for rule in prior.get("path_rules", [])
        if isinstance(rule, dict)
    }
    for rule_id in set(current_rules) | set(prior_rules):
        if current_rules.get(rule_id) == prior_rules.get(rule_id):
            continue
        for rule in (current_rules.get(rule_id), prior_rules.get(rule_id)):
            if isinstance(rule, dict) and isinstance(rule.get("surface"), str):
                changed_surfaces.add(rule["surface"])
    if changed_surfaces:
        changed.extend(
            edge.get("id")
            for graph in (current, prior)
            for edge in graph.get("edges", [])
            if isinstance(edge, dict)
            and edge.get("source") in changed_surfaces
            and isinstance(edge.get("id"), str)
        )
    changed.extend(authority_changed_edge_ids)
    changed = sorted(set(changed))
    return {
        "invalidated": bool(changed),
        "reason": "authoritative-graph-edge-change" if changed else "none",
        "changed_edge_ids": changed,
    }


def _authority_changed_edges(
    root: Path,
    revision: str,
    graph: dict[str, Any],
) -> set[str]:
    output = _git(root, "diff", "--name-only", "-z", revision, "--").stdout
    try:
        changed_paths = {
            path
            for path in output.decode("utf-8").split("\0")
            if path
        }
    except UnicodeDecodeError as error:
        raise OwnershipError("Git changed paths are not valid UTF-8") from error
    if SCHEMA_PATH.as_posix() in changed_paths:
        return {edge["id"] for edge in graph["edges"]}

    changed_authority_kinds = set()
    if any(path == "Makefile" or path.endswith(".mk") for path in changed_paths):
        changed_authority_kinds.add("make-target")
    if BUILD_WORKFLOW_PATH.as_posix() in changed_paths:
        changed_authority_kinds.update({"workflow-job", "workflow-step"})
    if TEST_CASE_REGISTRY_PATH.as_posix() in changed_paths:
        changed_authority_kinds.add("tester-case")
    if any(path.startswith("scripts/generated_data/") for path in changed_paths):
        changed_authority_kinds.add("generated-data-registry")

    nodes = {
        node["id"]: node
        for node in graph["nodes"]
        if node["kind"] == "evidence"
    }
    changed_nodes = set()
    for node_id, node in nodes.items():
        authority = node["authority"]
        if authority["kind"] in changed_authority_kinds:
            changed_nodes.add(node_id)
        if (
            authority["kind"] == "manual-handoff"
            and authority["path"] in changed_paths
        ):
            changed_nodes.add(node_id)
    return {
        edge["id"]
        for edge in graph["edges"]
        if edge["target"] in changed_nodes
    }


def _prior_graph(root: Path, revision: str | None) -> dict[str, Any] | None:
    if revision is None:
        return None
    completed = _git(
        root,
        "show",
        f"{revision}:{GRAPH_PATH.as_posix()}",
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        return parse_json(completed.stdout.decode("utf-8"), f"{revision}:{GRAPH_PATH}")
    except UnicodeDecodeError as error:
        raise OwnershipError("prior ownership graph is not valid UTF-8") from error


def _resolve_path(
    path: str,
    graph: dict[str, Any],
    model: dict[str, Any],
) -> dict[str, Any]:
    if not path or path.startswith("/") or ".." in Path(path).parts:
        raise OwnershipError(f"changed path {path!r} must be repository-relative")
    generated_paths = model["generated_paths"]
    matches = [
        rule
        for rule in graph["path_rules"]
        if _path_rule_matches(rule, path, generated_paths)
    ]
    exclusions = [
        exclusion
        for exclusion in graph["exclusions"]
        if any(
            _selector_matches(selector, path, generated_paths)
            for selector in exclusion["include"]
        )
    ]
    if not matches and not exclusions:
        raise OwnershipError(f"changed path {path!r} has no ownership contract")
    if len(matches) + len(exclusions) > 1:
        raise OwnershipError(f"changed path {path!r} has ambiguous ownership")
    if exclusions:
        exclusion = exclusions[0]
        raise OwnershipError(
            f"changed path {path!r} is fail-closed exclusion "
            f"{exclusion['id']!r}: {exclusion['reason']}"
        )
    rule = matches[0]
    surface = model["surfaces"][rule["surface"]]
    owners = []
    for edge in sorted(
        model["outgoing"][surface["id"]], key=lambda item: item["type"]
    ):
        if edge["type"] == "depends-on":
            continue
        target = model["evidence"][edge["target"]]
        authority = model["authorities"][edge["target"]]
        owners.append(
            {
                "edge_id": edge["id"],
                "edge_type": edge["type"],
                "evidence_id": target["id"],
                "evidence_type": target["evidence_type"],
                "gate": authority["display"],
                "reason": edge["reason"],
            }
        )
    return {
        "path": path,
        "rule": rule["id"],
        "surface": surface["id"],
        "surface_type": surface["surface_type"],
        "owners": owners,
    }


def _measure(
    graph: dict[str, Any],
    model: dict[str, Any],
) -> dict[str, Any]:
    false_positive = 0
    false_negative = 0
    probes = []
    for probe in graph["measurement"]["probes"]:
        resolution = _resolve_path(probe["path"], graph, model)
        actual = {owner["edge_type"] for owner in resolution["owners"]}
        expected = set(probe["expected_edge_types"])
        if resolution["surface"] != probe["expected_surface"]:
            false_positive += 1
            false_negative += 1
        false_positive += len(actual - expected)
        false_negative += len(expected - actual)
        probes.append(
            {
                "path": probe["path"],
                "surface": resolution["surface"],
                "edge_types": sorted(actual),
            }
        )
    artifact = graph["artifact"]
    return {
        "source_case": graph["measurement"]["source_case"],
        "probe_count": len(probes),
        "false_positive_selections": false_positive,
        "false_negative_selections": false_negative,
        "estimated_maintenance_minutes": artifact[
            "estimated_maintenance_minutes"
        ],
        "max_maintenance_minutes": artifact["max_maintenance_minutes"],
        "probes": probes,
    }


def build_report(
    graph: dict[str, Any],
    schema: dict[str, Any],
    root: Path,
    paths: tuple[str, ...],
    changed_paths: Iterable[str] = (),
    prior_graph: dict[str, Any] | None = None,
    review_comparison_requested: bool = False,
    authority_changed_edge_ids: Iterable[str] = (),
) -> dict[str, Any]:
    model = validate_graph(graph, schema, root, paths)
    resolutions = [
        _resolve_path(path, graph, model)
        for path in sorted(set(changed_paths))
    ]
    selected = {}
    for resolution in resolutions:
        for owner in resolution["owners"]:
            selected.setdefault(
                owner["evidence_id"],
                {
                    "evidence_id": owner["evidence_id"],
                    "evidence_type": owner["evidence_type"],
                    "gate": owner["gate"],
                    "reasons": [],
                },
            )["reasons"].append(
                {
                    "path": resolution["path"],
                    "edge_type": owner["edge_type"],
                    "explanation": owner["reason"],
                }
            )
    for record in selected.values():
        record["reasons"].sort(
            key=lambda item: (item["path"], item["edge_type"])
        )
    resolved_edges = _resolved_edges(graph, model)
    return {
        "schema_version": EXPECTED_SCHEMA_VERSION,
        "policy": graph["policy"],
        "coverage": {
            "tracked_paths": len(paths),
            "owned_paths": sum(
                item["kind"] == "owned" for item in model["coverage"].values()
            ),
            "fail_closed_exclusions": sum(
                item["kind"] == "excluded"
                for item in model["coverage"].values()
            ),
            "path_rules": len(graph["path_rules"]),
        },
        "artifact": {
            "artifact_id": graph["artifact"]["artifact_id"],
            "current_disposition": graph["artifact"]["history"][-1][
                "disposition"
            ],
            "executable_consumer": graph["artifact"]["executable_consumer"],
            "consistency_check": graph["artifact"]["consistency_check"],
        },
        "measurement": _measure(graph, model),
        "resolutions": resolutions,
        "selected_gates": [
            selected[key] for key in sorted(selected)
        ],
        "review_invalidation": compare_graph_edges(
            graph, prior_graph, authority_changed_edge_ids
        )
        if review_comparison_requested
        else {
            "invalidated": False,
            "reason": "comparison-not-requested",
            "changed_edge_ids": [],
        },
        "seals": {
            "schema": _sha256(SCHEMA_SEAL_DOMAIN, schema),
            "graph": _sha256(GRAPH_SEAL_DOMAIN, graph),
            "resolved_edges": _sha256(EDGE_SEAL_DOMAIN, resolved_edges),
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate whole-repository ownership and explain additive gates; "
            "this reporter never narrows or executes validation."
        )
    )
    parser.add_argument("--repository-root", required=True)
    parser.add_argument(
        "--changed",
        action="append",
        default=[],
        metavar="PATH",
        help="resolve and explain one changed or deleted repository path",
    )
    parser.add_argument(
        "--base-revision",
        help="compare authoritative graph edges with a Git revision",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        root = validate_repository_root(Path(arguments.repository_root))
        before = repository_status(root)
        graph = load_json(root / GRAPH_PATH)
        schema = load_json(root / SCHEMA_PATH)
        prior = _prior_graph(root, arguments.base_revision)
        if prior is not None:
            validate_json_schema(prior, schema, schema, "prior graph")
        authority_changed = (
            _authority_changed_edges(root, arguments.base_revision, graph)
            if arguments.base_revision is not None and prior is not None
            else set()
        )
        report = build_report(
            graph,
            schema,
            root,
            tracked_paths(root),
            arguments.changed,
            prior,
            arguments.base_revision is not None,
            authority_changed,
        )
        after = repository_status(root)
        if after != before:
            raise OwnershipError("reporter changed the repository worktree")
        sys.stdout.buffer.write(normalized_json(report))
        return 0
    except OwnershipError as error:
        print(f"validation-ownership: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
