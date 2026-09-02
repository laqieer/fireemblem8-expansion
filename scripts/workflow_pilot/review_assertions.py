#!/usr/bin/env python3
"""Exact-base executable assertions for review-family evidence."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
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
ASSERTION_FILE_MODES = {"100644", "100755", "120000"}
MATERIALIZED_FILE_MODES = {"100644", "100755"}
ASSERTION_INPUT_PATHS = (
    ".github/workflow-pilot-decisions.json",
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
AUTHORITY_ROOTS = {
    ("global", None): (("file", "scripts/workflow_pilot/review_assertions.py"),),
    ("action", "actions"): (("file", "scripts/workflow_pilot/review_base_checker.py"),),
    ("action", "items"): (("file", "scripts/workflow_pilot/review_base_checker.py"),),
    ("action", "targets"): (("file", "scripts/workflow_pilot/review_base_checker.py"),),
    ("generated", "owners"): (),
    ("generated", "outputs"): (
        ("module", "scripts.workflow_pilot.candidate_evidence"),
        ("module", "scripts.workflow_pilot.event_classifier"),
    ),
    ("generated", "consumers"): (("module", "tests.workflows.test_build_ci_topology"),),
    ("generated", "drift-checks"): (
        ("module", "scripts.docs_check_tests.test_check_docs"),
        ("module", "scripts.docs_check_tests.test_development_workflow_skill"),
    ),
    ("lifecycle", "entries"): (("module", "scripts.workflow_pilot.review_family"),),
    ("lifecycle", "preservation"): (
        ("module", "scripts.workflow_pilot.reporter"),
        ("module", "scripts.workflow_pilot.review_family"),
        ("module", "scripts.workflow_pilot.trusted_review_gate"),
    ),
    ("lifecycle", "resets"): (("module", "scripts.workflow_pilot.review_family"),),
    ("lifecycle", "terminals"): (
        ("module", "scripts.workflow_pilot.reporter"),
        ("module", "scripts.workflow_pilot.review_family"),
        ("module", "scripts.workflow_pilot.trusted_review_gate"),
    ),
    ("resource", "enabled"): (
        ("module", "scripts.workflow_pilot.reporter"),
        ("module", "scripts.workflow_pilot.review_family"),
        ("module", "scripts.workflow_pilot.trusted_review_gate"),
    ),
    ("resource", "disabled"): (
        ("module", "scripts.workflow_pilot.reporter"),
        ("module", "scripts.workflow_pilot.review_family"),
        ("module", "scripts.workflow_pilot.trusted_review_gate"),
    ),
    ("wire", "producers"): (
        ("module", "scripts.workflow_pilot.reporter"),
        ("module", "scripts.workflow_pilot.review_family"),
        ("module", "scripts.workflow_pilot.trusted_review_gate"),
    ),
    ("wire", "consumers"): (
        ("module", "scripts.workflow_pilot.reporter"),
        ("module", "scripts.workflow_pilot.review_family"),
        ("module", "scripts.workflow_pilot.trusted_review_gate"),
    ),
    ("wire", "validators"): (
        ("file", "scripts/workflow_pilot/review_base_checker.py"),
        ("module", "scripts.workflow_pilot.review_family"),
    ),
    ("wire", "replay"): (
        ("module", "scripts.workflow_pilot.reporter"),
        ("module", "scripts.workflow_pilot.review_family"),
        ("module", "scripts.workflow_pilot.trusted_review_gate"),
    ),
    ("wire", "stale-bindings"): (
        ("file", "scripts/workflow_pilot/review_base_checker.py"),
        ("module", "scripts.workflow_pilot.reporter"),
        ("module", "scripts.workflow_pilot.review_family"),
        ("module", "scripts.workflow_pilot.trusted_review_gate"),
    ),
}
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
    "base_root",
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
    "captured_github_payload",
    "trust_mode",
    "changed_files",
    "changes",
    "remote_finding_ids",
    "limits",
    "original_pre_review",
    "round_findings",
    "assertion_requests",
    "invoking_checker_module_name",
    "invoking_checker_argv",
    "invoking_checker_cwd",
    "invoking_checker_home",
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
    "base_root",
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
    "captured_github_payload",
    "trust_mode",
    "changed_files",
    "changes",
    "remote_finding_ids",
    "limits",
    "original_pre_review",
    "assertion_requests",
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


def expect_optional_sha(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return expect_sha(value, label)


def expect_optional_mode(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in ASSERTION_FILE_MODES:
        raise AssertionFailure(f"{label} must be null or an exact Git mode")
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
    if not discovered <= expected:
        raise AssertionFailure(
            "member artifact tree does not match the allowlisted production inputs"
        )


def read_text(root: Path, relative: str) -> str:
    path = root / relative
    try:
        if not path.is_file() or path.is_symlink():
            raise AssertionFailure(f"member artifact {relative!r} is unavailable")
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise AssertionFailure(f"member artifact {relative!r} is unavailable") from error


def load_json_file(root: Path, relative: str) -> Any:
    try:
        return json.loads(
            read_text(root, relative), object_pairs_hook=object_no_duplicates
        )
    except json.JSONDecodeError as error:
        raise AssertionFailure(f"member artifact {relative!r} is not valid JSON") from error


def git_blob_oid(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def blob_oid_for_root(root: Path, relative: str) -> str:
    path = root / relative
    try:
        if not path.is_file() or path.is_symlink():
            raise AssertionFailure(f"member artifact {relative!r} is unavailable")
        return git_blob_oid(path.read_bytes())
    except OSError as error:
        raise AssertionFailure(f"member artifact {relative!r} is unavailable") from error


def _attribute_path(node: ast.AST) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _top_level_assignments(syntax: ast.Module) -> dict[str, ast.AST]:
    assignments = {}
    for statement in syntax.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                assignments[target.id] = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            if statement.value is not None:
                assignments[statement.target.id] = statement.value
    return assignments


def _evaluate_static_value(
    node: ast.AST,
    *,
    base_root: Path,
    current_relative: str,
    assignments: dict[str, ast.AST],
    stack: set[str] | None = None,
) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return str((base_root / current_relative).resolve())
        if node.id not in assignments:
            raise AssertionFailure(
                f"authority dependency expression references unknown name {node.id!r}"
            )
        active = set() if stack is None else set(stack)
        if node.id in active:
            raise AssertionFailure(
                f"authority dependency expression is cyclic at {node.id!r}"
            )
        active.add(node.id)
        return _evaluate_static_value(
            assignments[node.id],
            base_root=base_root,
            current_relative=current_relative,
            assignments=assignments,
            stack=active,
        )
    if isinstance(node, ast.Call):
        call_name = _attribute_path(node.func)
        args = list(node.args)
        kwargs = {keyword.arg: keyword.value for keyword in node.keywords}
        if call_name == "os.path.join":
            values = [
                _evaluate_static_value(
                    argument,
                    base_root=base_root,
                    current_relative=current_relative,
                    assignments=assignments,
                    stack=stack,
                )
                for argument in args
            ]
            if any(not isinstance(value, str) for value in values):
                raise AssertionFailure("authority dependency join arguments must be strings")
            return os.path.join(*values)
        if call_name == "os.path.dirname":
            if len(args) != 1:
                raise AssertionFailure("authority dependency dirname arguments are invalid")
            value = _evaluate_static_value(
                args[0],
                base_root=base_root,
                current_relative=current_relative,
                assignments=assignments,
                stack=stack,
            )
            if not isinstance(value, str):
                raise AssertionFailure("authority dependency dirname argument must be a string")
            return os.path.dirname(value)
        if call_name == "os.path.abspath":
            if len(args) != 1:
                raise AssertionFailure("authority dependency abspath arguments are invalid")
            value = _evaluate_static_value(
                args[0],
                base_root=base_root,
                current_relative=current_relative,
                assignments=assignments,
                stack=stack,
            )
            if not isinstance(value, str):
                raise AssertionFailure("authority dependency abspath argument must be a string")
            return os.path.abspath(value)
        if call_name == "Path":
            if len(args) != 1:
                raise AssertionFailure("authority dependency Path arguments are invalid")
            value = _evaluate_static_value(
                args[0],
                base_root=base_root,
                current_relative=current_relative,
                assignments=assignments,
                stack=stack,
            )
            if not isinstance(value, str):
                raise AssertionFailure("authority dependency Path argument must be a string")
            return Path(value)
        if call_name == "Path.resolve":
            owner = _evaluate_static_value(
                node.func.value,
                base_root=base_root,
                current_relative=current_relative,
                assignments=assignments,
                stack=stack,
            )
            if args or kwargs or not isinstance(owner, Path):
                raise AssertionFailure("authority dependency Path.resolve call is invalid")
            return owner.resolve()
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _evaluate_static_value(
            node.left,
            base_root=base_root,
            current_relative=current_relative,
            assignments=assignments,
            stack=stack,
        )
        right = _evaluate_static_value(
            node.right,
            base_root=base_root,
            current_relative=current_relative,
            assignments=assignments,
            stack=stack,
        )
        if not isinstance(left, Path) or not isinstance(right, str):
            raise AssertionFailure("authority dependency path division is invalid")
        return left / right
    raise AssertionFailure("authority dependency expression is not statically resolvable")


def _allowed_path(relative: str, allowed_paths: set[str]) -> str:
    normalized = relative.replace("\\", "/")
    if normalized not in allowed_paths:
        raise AssertionFailure(
            f"authority dependency {normalized!r} is outside the closed allowlist"
        )
    return normalized


def _resolve_dynamic_file_dependency(
    value: Any, *, base_root: Path, allowed_paths: set[str]
) -> str:
    if isinstance(value, Path):
        resolved = value.resolve()
    elif isinstance(value, str):
        path = Path(value)
        if not path.is_absolute():
            raise AssertionFailure(
                "authority dependency file loader must use an exact absolute path"
            )
        resolved = path.resolve()
    else:
        raise AssertionFailure("authority dependency file loader path is invalid")
    root = base_root.resolve()
    if root != resolved and root not in resolved.parents:
        raise AssertionFailure("authority dependency file loader escapes the trusted root")
    relative = resolved.relative_to(root).as_posix()
    return _allowed_path(relative, allowed_paths)


def _resolve_local_module_entries(
    base_root: Path,
    module_name: str,
    allowed_paths: set[str],
    *,
    allow_missing: bool = False,
) -> tuple[tuple[str, bool], ...] | None:
    if not module_name or module_name.startswith("."):
        raise AssertionFailure(f"authority module name {module_name!r} is invalid")
    segments = module_name.split(".")
    if any(not segment for segment in segments):
        raise AssertionFailure(f"authority module name {module_name!r} is invalid")
    file_rel = "/".join(segments) + ".py"
    package_rel = "/".join(segments) + "/__init__.py"
    directory = base_root.joinpath(*segments)
    file_path = base_root / file_rel
    package_path = base_root / package_rel
    file_exists = file_path.is_file()
    package_exists = package_path.is_file()
    directory_exists = directory.is_dir()
    if not file_exists and not package_exists and not directory_exists:
        if allow_missing:
            return None
        raise AssertionFailure(
            f"authority module import {module_name!r} is unavailable"
        )
    if file_exists and package_exists:
        raise AssertionFailure(
            f"authority module import {module_name!r} is ambiguous"
        )
    entries = []
    prefix = []
    for segment in segments[:-1]:
        prefix.append(segment)
        package_prefix = "/".join(prefix) + "/__init__.py"
        package_dir = base_root.joinpath(*prefix)
        prefix_init = base_root / package_prefix
        if package_dir.is_dir():
            entries.append(
                (
                    _allowed_path(package_prefix, allowed_paths),
                    prefix_init.is_file(),
                )
            )
            continue
        raise AssertionFailure(f"authority package {'.'.join(prefix)!r} is unavailable")
    if package_exists:
        entries.append((_allowed_path(package_rel, allowed_paths), True))
        return tuple(entries)
    if file_exists:
        entries.append((_allowed_path(file_rel, allowed_paths), True))
        return tuple(entries)
    if directory_exists:
        entries.append((_allowed_path(package_rel, allowed_paths), False))
        return tuple(entries)
    if allow_missing:
        return None
    raise AssertionFailure(
        f"authority module import {module_name!r} does not resolve to a file"
    )


def _module_name_for_relative(current_relative: str) -> str:
    if current_relative.endswith("/__init__.py"):
        return current_relative[: -len("/__init__.py")].replace("/", ".")
    if current_relative.endswith(".py"):
        return current_relative[:-3].replace("/", ".")
    raise AssertionFailure(
        f"authority source {current_relative!r} does not map to a Python module"
    )


def _resolve_import_from_module(
    statement: ast.ImportFrom, current_relative: str
) -> str:
    if statement.level == 0:
        if statement.module is None:
            raise AssertionFailure(
                f"authority source {current_relative!r} has an unresolved import"
            )
        return statement.module
    package_segments = _module_name_for_relative(current_relative).split(".")
    if not current_relative.endswith("/__init__.py"):
        package_segments = package_segments[:-1]
    trim = statement.level - 1
    if trim > len(package_segments):
        raise AssertionFailure(
            f"authority source {current_relative!r} escapes its package root"
        )
    base_segments = package_segments[: len(package_segments) - trim]
    if statement.module:
        return ".".join((*base_segments, statement.module))
    if not base_segments:
        raise AssertionFailure(
            f"authority source {current_relative!r} has an unresolved import"
        )
    return ".".join(base_segments)


def _dependency_specs_from_statement(
    statement: ast.stmt,
    *,
    base_root: Path,
    current_relative: str,
    assignments: dict[str, ast.AST],
    allowed_paths: set[str],
) -> list[tuple[str, str]]:
    result = []
    if isinstance(statement, ast.Import):
        return [("module-optional", alias.name) for alias in statement.names]
    if isinstance(statement, ast.ImportFrom):
        module_name = _resolve_import_from_module(statement, current_relative)
        result.append(("module-optional", module_name))
        for alias in statement.names:
            if alias.name == "*":
                raise AssertionFailure(
                    f"authority source {current_relative!r} uses a wildcard import"
                )
            submodule = _resolve_local_module_entries(
                base_root,
                f"{module_name}.{alias.name}",
                allowed_paths,
                allow_missing=True,
            )
            if submodule is not None:
                result.append(("module-optional", f"{module_name}.{alias.name}"))
        return result
    value = None
    if isinstance(statement, ast.Assign):
        value = statement.value
    elif isinstance(statement, ast.AnnAssign):
        value = statement.value
    elif isinstance(statement, ast.Expr):
        value = statement.value
    if value is None:
        return result
    for node in ast.walk(value):
        if not isinstance(node, ast.Call):
            continue
        call_name = _attribute_path(node.func)
        if call_name == "importlib.import_module":
            if not node.args:
                raise AssertionFailure("authority dynamic import is missing its module name")
            module_name = _evaluate_static_value(
                node.args[0],
                base_root=base_root,
                current_relative=current_relative,
                assignments=assignments,
            )
            if not isinstance(module_name, str):
                raise AssertionFailure("authority dynamic import did not resolve to a module name")
            result.append(("module-optional", module_name))
        elif call_name == "importlib.util.spec_from_file_location":
            if len(node.args) < 2:
                raise AssertionFailure(
                    "authority file loader is missing its source path"
                )
            path_value = _evaluate_static_value(
                node.args[1],
                base_root=base_root,
                current_relative=current_relative,
                assignments=assignments,
            )
            result.append(
                (
                    "file",
                    _resolve_dynamic_file_dependency(
                        path_value,
                        base_root=base_root,
                        allowed_paths=allowed_paths,
                    ),
                )
            )
    return result


def resolve_authority_import_closure(
    base_root: Path,
    roots: tuple[tuple[str, str], ...],
    *,
    allowed_paths: set[str] | None = None,
) -> tuple[str, ...]:
    allowed = set(ASSERTION_INPUT_PATHS if allowed_paths is None else allowed_paths)
    pending = list(roots)
    resolved_paths = set()
    scanned_paths = set()
    while pending:
        kind, value = pending.pop()
        if kind in {"module", "module-optional"}:
            entries = _resolve_local_module_entries(
                base_root,
                value,
                allowed,
                allow_missing=kind == "module-optional",
            )
            if entries is None:
                continue
            for relative, should_scan in reversed(entries):
                resolved_paths.add(relative)
                if should_scan and relative not in scanned_paths:
                    pending.append(("file", relative))
            continue
        if kind != "file":
            raise AssertionFailure(f"authority dependency kind {kind!r} is unsupported")
        relative = _allowed_path(value, allowed)
        resolved_paths.add(relative)
        if relative in scanned_paths:
            continue
        source = read_text(base_root, relative)
        try:
            syntax = ast.parse(source, filename=relative)
        except SyntaxError as error:
            raise AssertionFailure(
                f"authority dependency {relative!r} is not valid Python"
            ) from error
        assignments = _top_level_assignments(syntax)
        for statement in syntax.body:
            pending.extend(
                _dependency_specs_from_statement(
                    statement,
                    base_root=base_root,
                    current_relative=relative,
                    assignments=assignments,
                    allowed_paths=allowed,
                )
            )
        scanned_paths.add(relative)
    return tuple(sorted(resolved_paths))


def authority_dependency_paths(
    family: str,
    member: str,
    *,
    base_root: Path,
    allowed_paths: set[str] | None = None,
) -> tuple[str, ...]:
    roots = (*AUTHORITY_ROOTS[("global", None)], *AUTHORITY_ROOTS[(family, member)])
    return resolve_authority_import_closure(
        base_root,
        roots,
        allowed_paths=allowed_paths,
    )


def load_standalone_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionFailure(f"{path.name} is unavailable")
    module = importlib.util.module_from_spec(spec)
    previous_module = sys.modules.get(module_name)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
    return module


def checker_cli_runtime(checker_input: dict[str, Any]) -> dict[str, Any]:
    module_name = expect_string(
        checker_input["invoking_checker_module_name"],
        "member checker input.invoking_checker_module_name",
    )
    argv = [
        expect_string(value, f"member checker input.invoking_checker_argv[{index}]")
        for index, value in enumerate(
            expect_list(
                checker_input["invoking_checker_argv"],
                "member checker input.invoking_checker_argv",
            )
        )
    ]
    cwd = Path(
        expect_string(
            checker_input["invoking_checker_cwd"],
            "member checker input.invoking_checker_cwd",
        )
    )
    home = Path(
        expect_string(
            checker_input["invoking_checker_home"],
            "member checker input.invoking_checker_home",
        )
    )
    if module_name != "__main__":
        raise AssertionFailure("action member did not run beneath the public checker CLI")
    if len(argv) != 3 or argv[1] != "--input":
        raise AssertionFailure("action member checker argv is not exact")
    checker_path = Path(argv[0])
    input_path = Path(argv[2])
    if (
        checker_path.name != "review_base_checker.py"
        or input_path.name != "checker-input.json"
    ):
        raise AssertionFailure("action member checker argv is not exact")
    try:
        if (
            checker_path.resolve().parent != cwd.resolve()
            or input_path.resolve().parent != cwd.resolve()
            or home.resolve() != cwd.resolve()
        ):
            raise AssertionFailure("action member checker runtime path is not exact")
    except OSError as error:
        raise AssertionFailure("action member checker runtime path is unavailable") from error
    return {
        "module_name": module_name,
        "argv": [checker_path.name, "--input", input_path.name],
        "cwd_name": cwd.name,
    }


def parse_utc_time(value: Any, label: str) -> datetime:
    text = expect_string(value, label)
    if not text.endswith("Z"):
        raise AssertionFailure(f"{label} must use RFC 3339 UTC form")
    try:
        return datetime.fromisoformat(text[:-1] + "+00:00").astimezone(
            timezone.utc
        )
    except ValueError as error:
        raise AssertionFailure(f"{label} must be a valid UTC timestamp") from error


def format_utc_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def changed_files(changes: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            path
            for change in changes
            for path in (change["old_path"], change["new_path"])
            if path is not None
        }
    )


def import_modules_from_root(root: Path, *module_names: str) -> tuple[Any, ...]:
    saved_path = list(sys.path)
    saved_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "scripts"
        or name.startswith("scripts.")
        or name == "tests"
        or name.startswith("tests.")
    }
    for name in list(saved_modules):
        sys.modules.pop(name, None)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root.resolve()))
    try:
        return tuple(importlib.import_module(name) for name in module_names)
    except Exception as error:
        raise AssertionFailure(
            f"authority module import failed for {root}"
        ) from error
    finally:
        sys.dont_write_bytecode = previous
        sys.path[:] = saved_path
        for name in list(sys.modules):
            if (
                name == "scripts"
                or name.startswith("scripts.")
                or name == "tests"
                or name.startswith("tests.")
            ):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)


def load_base_gate_modules(base_root: Path):
    reporter_module, review_family_module, gate_module = import_modules_from_root(
        base_root,
        "scripts.workflow_pilot.reporter",
        "scripts.workflow_pilot.review_family",
        "scripts.workflow_pilot.trusted_review_gate",
    )
    gate_module.reporter = reporter_module
    gate_module.review_family = review_family_module
    return gate_module, reporter_module, review_family_module


def load_base_generated_modules(base_root: Path):
    return import_modules_from_root(
        base_root,
        "scripts.workflow_pilot.candidate_evidence",
        "scripts.workflow_pilot.event_classifier",
    )


def authoritative_trigger(
    gate_module: Any, checker_input: dict[str, Any]
) -> dict[str, Any] | None:
    contract = checker_input["review_contract"]
    if contract["trust_mode"] == "introduction":
        return None
    return gate_module.load_authoritative_trigger(
        contract,
        Path(checker_input["repository_root"]),
        checker_input["candidate_sha"],
    )


class StaticPayloadAdapter:
    def __init__(self, payload: dict[str, Any]):
        self._payload = copy.deepcopy(payload)

    def fetch(self, repository: str, pull_request: int) -> dict[str, Any]:
        return copy.deepcopy(self._payload)


def _wire_clock(checker_input: dict[str, Any]):
    issued = parse_utc_time(
        checker_input["original_review_receipt"]["issued_at"],
        "member checker input.original_review_receipt.issued_at",
    )
    review_times = [
        parse_utc_time(
            review["submitted_at"],
            f"member checker input.all_remote_reviews[{index}].submitted_at",
        )
        for index, review in enumerate(checker_input["all_remote_reviews"])
    ]
    anchor = max([issued, *review_times])
    return lambda: anchor + timedelta(seconds=1)


def _wire_payload(
    gate_module: Any, checker_input: dict[str, Any]
) -> dict[str, Any]:
    current_head = gate_module._git_text(
        Path(checker_input["repository_root"]), "rev-parse", "--verify", "HEAD^{commit}"
    )
    review_head = checker_input["review_context"]["candidate_sha"]
    contract = copy.deepcopy(checker_input["review_contract"])
    contract["candidate_sha"] = current_head
    evidence_bytes = gate_module.collect_live_evidence_bytes(
        contract,
        Path(checker_input["repository_root"]),
        current_head,
        current_head,
        copy.deepcopy(checker_input["original_pre_review"]),
        copy.deepcopy(checker_input["original_review_receipt"]),
        [],
        authoritative_trigger=authoritative_trigger(gate_module, checker_input),
        adapter=StaticPayloadAdapter(checker_input["captured_github_payload"]),
        clock=_wire_clock(checker_input),
    )
    try:
        payload = json.loads(
            evidence_bytes.decode("utf-8"), object_pairs_hook=object_no_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AssertionFailure("wire payload is not valid JSON") from error
    if normalized_json(payload) != evidence_bytes:
        raise AssertionFailure("wire payload is not canonical")
    payload = expect_object(payload, "wire payload")
    if payload["pull_request"]["head_sha"] != current_head:
        raise AssertionFailure("wire payload current head is incomplete")
    review = next(
        (
            item for item in payload["remote_reviews"]
            if item["node_id"] == checker_input["review_context"]["node_id"]
        ),
        None,
    )
    if (
        review is None
        or review["round"] != checker_input["review_round"]
        or review["candidate_sha"] != review_head
    ):
        raise AssertionFailure("wire payload historical round binding is incomplete")
    if checker_input["review_round"] > 1:
        previous = checker_input["all_remote_reviews"][checker_input["review_round"] - 2]
        findings = {item["node_id"] for item in payload["findings"]}
        threads = {item["finding_id"] for item in payload["threads"]}
        if any(finding_id not in findings or finding_id not in threads for finding_id in previous["finding_ids"]):
            raise AssertionFailure("wire payload historical findings/threads are incomplete")
    if review_head != current_head:
        try:
            run_git(
                Path(checker_input["repository_root"]),
                "merge-base",
                "--is-ancestor",
                review_head,
                current_head,
            )
        except RuntimeError as error:
            raise AssertionFailure("wire payload historical head is not preserved in current PR history") from error
    return payload


def _offline_wire_payload(
    gate_module: Any, live_payload: dict[str, Any], checker_input: dict[str, Any]
) -> dict[str, Any]:
    return expect_object(
        gate_module.build_live_evidence_payload(
            contract=checker_input["review_contract"],
            expected_candidate=live_payload["candidate"]["sha"],
            source_kind="offline-transform-fixture",
            captured_at=live_payload["captured_at"],
            original_receipt_sha256=live_payload["original_receipt_sha256"],
            pull_request=copy.deepcopy(live_payload["pull_request"]),
            authoritative_trigger=copy.deepcopy(
                live_payload["authoritative_trigger"]
            ),
            actors=copy.deepcopy(live_payload["actors"]),
            pre_reviews=copy.deepcopy(live_payload["pre_reviews"]),
            pre_review_findings=copy.deepcopy(live_payload["pre_review_findings"]),
            remote_reviews=copy.deepcopy(live_payload["remote_reviews"]),
            findings=copy.deepcopy(live_payload["findings"]),
            threads=copy.deepcopy(live_payload["threads"]),
            force_push_events=copy.deepcopy(live_payload["force_push_events"]),
            architecture_dispositions=copy.deepcopy(
                live_payload["architecture_dispositions"]
            ),
            execution_receipts=copy.deepcopy(live_payload["execution_receipts"]),
        ),
        "offline wire payload",
    )


def comparable_wire_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(payload)
    source = expect_object(normalized["source"], "wire payload.source")
    source["kind"] = "shared"
    return normalized


def progress_review(
    round_number: int,
    candidate_sha: str,
    submitted_at: str,
    outcome: str,
    finding_ids: list[str],
) -> dict[str, Any]:
    return {
        "id": 1000 + round_number,
        "node_id": f"REMOTE_REVIEW_{round_number}",
        "round": round_number,
        "reviewer_actor_id": "ACTOR_COPILOT_001",
        "candidate_sha": candidate_sha,
        "submitted_at": submitted_at,
        "state": (
            "CHANGES_REQUESTED" if outcome == "changes-requested" else "COMMENTED"
        ),
        "body": "changes requested" if outcome == "changes-requested" else "approval",
        "body_classification": (
            "changes-recommended"
            if outcome == "changes-requested"
            else "clean-approval"
        ),
        "body_has_findings": outcome == "changes-requested" and bool(finding_ids),
        "outcome": outcome,
        "finding_ids": finding_ids,
        "_submitted": parse_utc_time(
            submitted_at, f"progress review {round_number} submitted_at"
        ),
    }


def progress_sweeps(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        binding["finding_id"]: {
            "family": binding["finding_family"],
            "siblings": [
                {"member": member}
                for member in FAMILY_MEMBERS[binding["finding_family"]]
            ],
        }
    }


def find_registry_entry(items: list[Any], key: str, value: str, label: str) -> dict[str, Any]:
    for item in items:
        entry = expect_object(item, label)
        if entry.get(key) == value:
            return entry
    raise AssertionFailure(f"{label} {value!r} is unavailable")


def assertion_artifact_index(
    checker_input: dict[str, Any]
) -> dict[str, dict[str, str | None]]:
    artifacts = expect_list(
        checker_input["assertion_input_artifacts"],
        "member checker input.assertion_input_artifacts",
    )
    index = {}
    for position, raw in enumerate(artifacts):
        item = expect_object(raw, f"member checker input.assertion_input_artifacts[{position}]")
        expect_keys(
            item,
            f"member checker input.assertion_input_artifacts[{position}]",
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
        path = expect_string(item["path"], f"member checker input.assertion_input_artifacts[{position}].path")
        index[path] = {
            "base_mode": expect_optional_mode(
                item["base_mode"],
                f"member checker input.assertion_input_artifacts[{position}].base_mode",
            ),
            "base_blob_oid": expect_optional_sha(
                item["base_blob_oid"],
                f"member checker input.assertion_input_artifacts[{position}].base_blob_oid",
            ),
            "origin_mode": expect_optional_mode(
                item["origin_mode"],
                f"member checker input.assertion_input_artifacts[{position}].origin_mode",
            ),
            "origin_blob_oid": expect_optional_sha(
                item["origin_blob_oid"],
                f"member checker input.assertion_input_artifacts[{position}].origin_blob_oid",
            ),
            "head_mode": expect_optional_mode(
                item["head_mode"],
                f"member checker input.assertion_input_artifacts[{position}].head_mode",
            ),
            "head_blob_oid": expect_optional_sha(
                item["head_blob_oid"],
                f"member checker input.assertion_input_artifacts[{position}].head_blob_oid",
            ),
        }
        for prefix in ("base", "origin", "head"):
            mode = index[path][f"{prefix}_mode"]
            blob_oid = index[path][f"{prefix}_blob_oid"]
            if (mode is None) != (blob_oid is None):
                raise AssertionFailure(
                    f"member checker input.assertion_input_artifacts[{position}].{prefix}_mode and {prefix}_blob_oid must both be null or both be present"
                )
    return index


def authority_dependency_records(
    family: str,
    member: str,
    *,
    base_root: Path,
    checker_input: dict[str, Any],
) -> list[dict[str, Any]]:
    artifact_index = assertion_artifact_index(checker_input)
    result = []
    for path in authority_dependency_paths(family, member, base_root=base_root):
        if path not in artifact_index:
            raise AssertionFailure(f"authority dependency {path!r} is unavailable")
        base_mode = artifact_index[path]["base_mode"]
        base_blob_oid = artifact_index[path]["base_blob_oid"]
        origin_mode = artifact_index[path]["origin_mode"]
        origin_blob_oid = artifact_index[path]["origin_blob_oid"]
        head_mode = artifact_index[path]["head_mode"]
        head_blob_oid = artifact_index[path]["head_blob_oid"]
        origin_changed = (
            origin_mode != base_mode or origin_blob_oid != base_blob_oid
        )
        head_changed = head_mode != base_mode or head_blob_oid != base_blob_oid
        if origin_changed or head_changed:
            result.append(
                {
                    "path": path,
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
    return result


def authority_hold_output(
    family: str,
    member: str,
    binding: dict[str, Any],
    changed_dependencies: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        **binding,
        "program_case": f"member/{family}/{member}/authority-change-hold",
        "hold_reason": "authority-dependency-changed",
        "authority_dependencies": changed_dependencies,
        "external_review_required": True,
        "fresh_base_required": True,
    }


def load_base_checker(base_root: Path):
    return load_standalone_module(
        base_root / "scripts/workflow_pilot/review_base_checker.py",
        "review_base_checker_authority",
    )


def evaluate_action_member(
    member: str,
    *,
    base_root: Path,
    checker_input: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    checker = load_base_checker(base_root)
    runtime = checker_cli_runtime(checker_input)
    if member == "actions":
        sequence = checker.validate_review_action_contract(
            repository=checker_input["repository"],
            actions=copy.deepcopy(checker_input["original_pre_review"]["actions"]),
        )
        return {"sequence": sequence, "checker_cli": runtime}
    if member == "items":
        derived = checker.bind_member_request(
            {
                "round_findings": copy.deepcopy(checker_input["round_findings"]),
                "candidate_sha": checker_input["candidate_sha"],
                "candidate_tree": checker_input["candidate_tree"],
            },
            {
                "family": "action",
                "member": "items",
                "outcome": "affected-fixed",
                "reason": None,
            },
            binding["finding_id"],
        )
        if derived != binding:
            raise AssertionFailure("member-item authority binding is incomplete")
        return {
            "binding_sha256": hashlib.sha256(normalized_json(derived)).hexdigest(),
            "checker_cli": runtime,
        }
    reviewed = checker.validate_review_targets(
        copy.deepcopy(checker_input["original_pre_review"]["reviewed_files"]),
        copy.deepcopy(checker_input["original_pre_review"]["reviewed_changes"]),
        changed_files=sorted(
            {
                path
                for change in checker_input["original_changes"]
                for path in (change["old_path"], change["new_path"])
                if path is not None
            }
        ),
        changes=copy.deepcopy(checker_input["original_changes"]),
    )
    return {
        "statuses": sorted({change["status"] for change in reviewed["reviewed_changes"]}),
        "checker_cli": runtime,
    }


def evaluate_generated_outputs(
    *, base_root: Path, checker_input: dict[str, Any]
) -> dict[str, Any]:
    candidate_module, classifier_module = load_base_generated_modules(base_root)
    run_id = expect_int(
        checker_input["review_context"]["id"],
        "member checker input.review_context.id",
        1,
    )
    contexts = [
        {
            "job_id": "event-identity",
            "name": "event-identity",
            "conclusion": "success",
        },
        {
            "job_id": "event-router",
            "name": "event-router",
            "conclusion": "success",
        },
        {
            "job_id": "patch-release",
            "name": "patch-release",
            "conclusion": "skipped",
        },
        {
            "job_id": "event-classifier",
            "name": candidate_module.FULL_CLASSIFIER,
            "conclusion": "success",
        },
    ]
    contexts.extend(
        {"job_id": job_id, "name": job_id, "conclusion": "success"}
        for job_id in candidate_module.WORKER_JOB_IDS
    )
    contexts.append(
        {
            "job_id": "summary",
            "name": candidate_module.FULL_ATTESTATION,
            "conclusion": "success",
        }
    )
    evidence = candidate_module.evaluate_candidate_runs(
        [
            {
                "base_sha": checker_input["base_sha"],
                "contexts": contexts,
                "event": "pull_request",
                "head_sha": checker_input["candidate_sha"],
                "run_id": run_id,
            }
        ],
        head_sha=checker_input["candidate_sha"],
        base_sha=checker_input["base_sha"],
    )
    if not evidence.eligible or evidence.mode != "full":
        raise AssertionFailure("candidate evidence outputs are incomplete")
    payload = {
        "action": "synchronize",
        "number": checker_input["pull_request"],
        "pull_request": {
            "number": checker_input["pull_request"],
            "head": {"sha": checker_input["candidate_sha"]},
            "base": {"sha": checker_input["base_sha"], "ref": "master"},
        },
    }
    decision = classifier_module.classify_event(
        "pull_request",
        payload,
        github_ref=f"refs/pull/{checker_input['pull_request']}/merge",
        github_sha=checker_input["candidate_sha"],
        pr_base_sha=checker_input["base_sha"],
        pr_head_sha=checker_input["candidate_sha"],
        push_sha="",
    )
    if (
        decision.classification != "full"
        or decision.expected_base != checker_input["base_sha"]
        or decision.expected_head != checker_input["candidate_sha"]
        or not decision.run_expensive
        or not decision.identity_valid
    ):
        raise AssertionFailure("event-classifier output fields are incomplete")
    identities = []
    for path in (
        "scripts/workflow_pilot/candidate_evidence.py",
        "scripts/workflow_pilot/event_classifier.py",
    ):
        identities.append(
            {
                "path": path,
                "base_blob_oid": blob_oid_for_root(base_root, path),
                "head_blob_oid": blob_oid_for_root(
                    Path(checker_input["head_root"]), path
                ),
                "origin_blob_oid": blob_oid_for_root(
                    Path(checker_input["origin_root"]), path
                ),
            }
        )
    return {
        "authoritative_inputs": {
            "repository": checker_input["repository"],
            "pull_request": checker_input["pull_request"],
            "base_sha": checker_input["base_sha"],
            "candidate_sha": checker_input["candidate_sha"],
        },
        "candidate_run_id": run_id,
        "candidate_run_mode": evidence.mode,
        "decision_fields": list(classifier_module.EventDecision.__annotations__),
        "dependency_identities": identities,
    }


def evaluate_generated_owners(root: Path) -> dict[str, Any]:
    registry = load_json_file(root, "docs/test-cases/registry.json")
    feature = find_registry_entry(
        expect_list(registry["features"], "registry.features"),
        "id",
        WORKFLOW_FEATURE_ID,
        "registry feature",
    )
    case = find_registry_entry(
        expect_list(registry["cases"], "registry.cases"),
        "id",
        WORKFLOW_REVIEW_FAMILY_CASE,
        "registry case",
    )
    if CURRENT_IMPLEMENTATION_ISSUE not in feature["issue_urls"]:
        raise AssertionFailure("workflow-governance registry does not claim issue #179")
    if WORKFLOW_REVIEW_FAMILY_CASE not in feature["required_cases"]:
        raise AssertionFailure(
            "workflow-governance registry does not include the review-family case"
        )
    if case["document"] != "docs/test-cases/workflow-governance.md":
        raise AssertionFailure(
            "workflow-governance registry case document is incorrect"
        )
    return {
        "issue_urls": sorted(feature["issue_urls"]),
        "required_cases": sorted(feature["required_cases"]),
    }


def evaluate_generated_consumers(
    root: Path,
    *,
    base_root: Path,
    checker_input: dict[str, Any],
) -> dict[str, Any]:
    (topology_module,) = import_modules_from_root(
        base_root, "tests.workflows.test_build_ci_topology"
    )
    workflow_text = read_text(root, ".github/workflows/build.yml")
    event = {
        "event_name": "pull_request",
        "payload": {
            "action": "synchronize",
            "number": checker_input["pull_request"],
            "pull_request": {
                "number": checker_input["pull_request"],
                "head": {"sha": checker_input["candidate_sha"]},
                "base": {"sha": checker_input["base_sha"], "ref": "master"},
            },
        },
        "runner": {
            "github_ref": f"refs/pull/{checker_input['pull_request']}/merge",
            "github_sha": checker_input["candidate_sha"],
            "pr_base_sha": checker_input["base_sha"],
            "pr_head_sha": checker_input["candidate_sha"],
            "push_sha": "",
            "pr_number": checker_input["pull_request"],
        },
    }
    jobs = topology_module.triggered_jobs(workflow_text, event)
    if set(jobs) != set(topology_module.CANDIDATE_FULL_JOBS):
        raise AssertionFailure(
            "workflow topology tests do not evaluate candidate evidence"
        )
    return {"jobs": sorted(jobs)}


def evaluate_generated_drift_checks(root: Path, *, base_root: Path) -> dict[str, Any]:
    skill_module, docs_module = import_modules_from_root(
        base_root,
        "scripts.docs_check_tests.test_development_workflow_skill",
        "scripts.docs_check_tests.test_check_docs",
    )
    registry = load_json_file(root, "docs/test-cases/registry.json")
    feature = find_registry_entry(
        expect_list(registry["features"], "registry.features"),
        "id",
        WORKFLOW_FEATURE_ID,
        "registry feature",
    )
    case = find_registry_entry(
        expect_list(registry["cases"], "registry.cases"),
        "id",
        WORKFLOW_REVIEW_FAMILY_CASE,
        "registry case",
    )
    expected_cases = [
        "TC-WORKFLOW-CI-WAIT-001",
        "TC-WORKFLOW-MANUAL-HANDOFF-001",
        "TC-WORKFLOW-STACKED-CI-001",
        "TC-WORKFLOW-BODY-EDIT-001",
        "TC-WORKFLOW-PILOT-BASELINE-001",
        "TC-WORKFLOW-REVIEW-FAMILY-001",
    ]
    if skill_module.compare_string_membership(
        feature["required_cases"],
        expected_cases,
        "workflow-governance.required_cases",
    ):
        raise AssertionFailure("docs drift checks do not cover workflow-governance")
    if docs_module.membership_violations(feature["required_cases"], expected_cases):
        raise AssertionFailure("docs drift checks do not cover the review-family case")
    if case["document"] != "docs/test-cases/workflow-governance.md":
        raise AssertionFailure("review-family case registry document drifted")
    return {
        "required_cases": feature["required_cases"],
        "document": case["document"],
    }


def evaluate_lifecycle_entries(
    checker_input: dict[str, Any], binding: dict[str, Any], base_root: Path
) -> dict[str, Any]:
    _, _, review_family_module = load_base_gate_modules(base_root)
    start = parse_utc_time(
        checker_input["review_context"]["submitted_at"],
        "member checker input.review_context.submitted_at",
    )
    finding_ids = [binding["finding_id"]]
    reviews = [
        progress_review(
            1, checker_input["candidate_sha"], format_utc_time(start), "changes-requested", finding_ids
        ),
        progress_review(
            2,
            checker_input["candidate_sha"],
            format_utc_time(start + timedelta(minutes=1)),
            "changes-requested",
            finding_ids,
        ),
        progress_review(
            3,
            checker_input["candidate_sha"],
            format_utc_time(start + timedelta(minutes=2)),
            "changes-requested",
            finding_ids,
        ),
    ]
    handoffs, pending, consumed = review_family_module.progress_rounds(
        {
            "architecture_dispositions": [],
            "remote_reviews": reviews,
            "candidate": {"sha": checker_input["candidate_sha"]},
        },
        progress_sweeps(binding),
        set(),
    )
    if pending is None or pending["reason"] != "third-consecutive-change-request":
        raise AssertionFailure("lifecycle hold-entry contract is incomplete")
    if len(handoffs) != 2 or consumed:
        raise AssertionFailure("lifecycle handoff bounds are incomplete")
    return {"hold_reason": pending["reason"], "handoffs": len(handoffs)}


def _receipt_store(root: Path, prefix: str, receipt: dict[str, Any]) -> Path:
    receipt_bytes = normalized_json(receipt)
    return (
        root
        / "build"
        / "test-artifacts"
        / (
            prefix
            + "-"
            + hashlib.sha256(receipt_bytes).hexdigest()[:12]
        )
    )


def evaluate_lifecycle_preservation(
    checker_input: dict[str, Any], base_root: Path
) -> dict[str, Any]:
    gate_module, _, _ = load_base_gate_modules(base_root)
    receipt = copy.deepcopy(checker_input["original_review_receipt"])
    receipt_bytes = normalized_json(receipt)
    replay_store = _receipt_store(
        Path(checker_input["repository_root"]), "assertion-preservation", receipt
    )
    if replay_store.exists():
        shutil.rmtree(replay_store)
    replay_store.mkdir(parents=True)
    wrong_head = (
        checker_input["candidate_sha"]
        if checker_input["candidate_sha"] != checker_input["original_pre_review_head"]
        else checker_input["base_sha"]
    )
    try:
        gate_module.persist_original_receipt(
            receipt_bytes,
            replay_store,
            repository=checker_input["repository"],
            pull_request=checker_input["pull_request"],
            base_sha=checker_input["base_sha"],
            original_pre_review_head=checker_input["original_pre_review_head"],
            key_id=receipt["key_id"],
            key_epoch=receipt["key_epoch"],
        )
        preserved = gate_module.preserved_receipt_bytes(
            replay_store,
            repository=checker_input["repository"],
            pull_request=checker_input["pull_request"],
            base_sha=checker_input["base_sha"],
            original_pre_review_head=checker_input["original_pre_review_head"],
            key_id=receipt["key_id"],
            key_epoch=receipt["key_epoch"],
        )
        try:
            gate_module.preserved_receipt_bytes(
                replay_store,
                repository=checker_input["repository"],
                pull_request=checker_input["pull_request"],
                base_sha=checker_input["base_sha"],
                original_pre_review_head=wrong_head,
                key_id=receipt["key_id"],
                key_epoch=receipt["key_epoch"],
            )
        except gate_module.reporter.PilotDataError as error:
            wrong_head_rejection = str(error)
        else:
            raise AssertionFailure(
                "preserved original pre-review is not bound to the original head"
            )
    finally:
        shutil.rmtree(replay_store)
    if preserved != receipt_bytes:
        raise AssertionFailure("receipt preservation is not exact")
    return {
        "receipt_sha256": hashlib.sha256(preserved).hexdigest(),
        "wrong_head_rejection": wrong_head_rejection,
    }


def evaluate_lifecycle_resets(
    checker_input: dict[str, Any], binding: dict[str, Any], base_root: Path
) -> dict[str, Any]:
    _, _, review_family_module = load_base_gate_modules(base_root)
    start = parse_utc_time(
        checker_input["review_context"]["submitted_at"],
        "member checker input.review_context.submitted_at",
    )
    finding_ids = [binding["finding_id"]]
    reviews = [
        progress_review(
            1, checker_input["candidate_sha"], format_utc_time(start), "changes-requested", finding_ids
        ),
        progress_review(
            2,
            checker_input["candidate_sha"],
            format_utc_time(start + timedelta(minutes=1)),
            "clean",
            [],
        ),
        progress_review(
            3,
            checker_input["candidate_sha"],
            format_utc_time(start + timedelta(minutes=2)),
            "changes-requested",
            finding_ids,
        ),
    ]
    handoffs, pending, consumed = review_family_module.progress_rounds(
        {
            "architecture_dispositions": [],
            "remote_reviews": reviews,
            "candidate": {"sha": checker_input["candidate_sha"]},
        },
        progress_sweeps(binding),
        set(),
    )
    counts = [item["consecutive_change_request"] for item in handoffs]
    if counts != [1, 1] or pending is not None or consumed:
        raise AssertionFailure("lifecycle reset paths are incomplete")
    return {"resets": counts}


def evaluate_lifecycle_terminals(
    checker_input: dict[str, Any], base_root: Path
) -> dict[str, Any]:
    gate_module, _, _ = load_base_gate_modules(base_root)
    contract = copy.deepcopy(checker_input["review_contract"])
    contract["trust_mode"] = "introduction"
    result = gate_module.bootstrap_result(
        contract,
        checker_input["base_sha"],
        checker_input["candidate_sha"],
    )
    gates = result["gates"]
    if (
        result["bootstrap"]["mode"] != "introduction"
        or not result["bootstrap"]["external_coordinator_review_required"]
        or gates["push_allowed"]
        or gates["trusted_push_allowed"]
        or gates["merge_allowed"]
    ):
        raise AssertionFailure("terminal gate contract is incomplete")
    return {"terminal_gates": True}


def evaluate_resource_enabled(
    checker_input: dict[str, Any], base_root: Path
) -> dict[str, Any]:
    gate_module, _, _ = load_base_gate_modules(base_root)
    trigger = authoritative_trigger(gate_module, checker_input)
    if trigger is None or not trigger["pre_review_required"]:
        raise AssertionFailure(
            "authoritative decision record does not contain one exact high-risk review-family entry"
        )
    return {
        "risk_boundaries": trigger["risk_boundaries"],
        "threshold_triggers": trigger["threshold_triggers"],
    }


def evaluate_resource_disabled(
    checker_input: dict[str, Any], base_root: Path
) -> dict[str, Any]:
    gate_module, _, _ = load_base_gate_modules(base_root)
    contract = copy.deepcopy(checker_input["review_contract"])
    contract["trust_mode"] = "introduction"
    result = gate_module.bootstrap_result(
        contract,
        checker_input["base_sha"],
        checker_input["candidate_sha"],
    )
    if (
        result["bootstrap"]["mode"] != "introduction"
        or result["gates"]["merge_allowed"]
    ):
        raise AssertionFailure("introduction-mode disabled boundary is incomplete")
    return {"introduction_mode": True}


def evaluate_wire_producers(
    checker_input: dict[str, Any], base_root: Path
) -> dict[str, Any]:
    gate_module, _, _ = load_base_gate_modules(base_root)
    live_payload = _wire_payload(gate_module, checker_input)
    if not {
        "result_manifest",
        "execution_receipts",
        "authoritative_trigger",
    }.issubset(live_payload):
        raise AssertionFailure("wire producers are incomplete")
    offline_payload = _offline_wire_payload(gate_module, live_payload, checker_input)
    if not {
        "result_manifest",
        "execution_receipts",
        "authoritative_trigger",
    }.issubset(offline_payload):
        raise AssertionFailure("wire producers are incomplete")
    if comparable_wire_payload(live_payload) != comparable_wire_payload(
        offline_payload
    ):
        raise AssertionFailure("wire producers are incomplete")
    if live_payload["source"]["kind"] != "live-gh-api":
        raise AssertionFailure("wire producers are incomplete")
    if offline_payload["source"]["kind"] != "offline-transform-fixture":
        raise AssertionFailure("wire producers are incomplete")
    return {
        "live_source_kind": live_payload["source"]["kind"],
        "offline_source_kind": offline_payload["source"]["kind"],
        "result_manifest_size": len(live_payload["result_manifest"]),
    }


def evaluate_wire_consumers(
    checker_input: dict[str, Any], base_root: Path
) -> dict[str, Any]:
    gate_module, _, review_family_module = load_base_gate_modules(base_root)
    live_payload = _wire_payload(gate_module, checker_input)
    if not {
        "result_manifest",
        "execution_receipts",
        "authoritative_trigger",
    }.issubset(live_payload):
        raise AssertionFailure("wire consumers are incomplete")
    offline_payload = _offline_wire_payload(gate_module, live_payload, checker_input)
    if not {
        "result_manifest",
        "execution_receipts",
        "authoritative_trigger",
    }.issubset(offline_payload):
        raise AssertionFailure("wire consumers are incomplete")
    try:
        validated_live = review_family_module.validate_evidence(live_payload)
        validated_offline = review_family_module.validate_evidence(offline_payload)
    except Exception as error:
        raise AssertionFailure("wire consumers are incomplete") from error
    comparable_live = copy.deepcopy(validated_live)
    comparable_offline = copy.deepcopy(validated_offline)
    comparable_live["source"]["kind"] = "shared"
    comparable_live["raw"]["source"]["kind"] = "shared"
    comparable_offline["source"]["kind"] = "shared"
    comparable_offline["raw"]["source"]["kind"] = "shared"
    if comparable_live != comparable_offline:
        raise AssertionFailure("wire consumers are incomplete")
    return {
        "source_kinds": sorted(
            {
                validated_live["source"]["kind"],
                validated_offline["source"]["kind"],
            }
        ),
        "result_manifest_size": len(validated_live["result_manifest"]),
    }


def evaluate_wire_validators(
    checker_input: dict[str, Any], base_root: Path
) -> dict[str, Any]:
    checker = load_base_checker(base_root)
    positive_path, positive_blob = checker.validate_assertion_program_identity(
        Path(checker_input["repository_root"]),
        checker_input["base_sha"],
        Path(checker_input["assertion_program_path"]),
        checker_input["assertion_program_blob_oid"],
        checker_input["assertion_program_argv"],
    )
    try:
        checker.validate_assertion_program_identity(
            Path(checker_input["repository_root"]),
            checker_input["base_sha"],
            Path(checker_input["assertion_program_path"]),
            "f" * 40,
            checker_input["assertion_program_argv"],
        )
    except checker.CheckError as error:
        rejection = str(error)
    else:
        raise AssertionFailure("checker validators are incomplete")
    return {
        "program_blob_oid": positive_blob,
        "program_path": str(positive_path),
        "rejection": rejection,
    }


def evaluate_wire_replay(
    checker_input: dict[str, Any], base_root: Path
) -> dict[str, Any]:
    gate_module, _, _ = load_base_gate_modules(base_root)
    receipt = copy.deepcopy(checker_input["original_review_receipt"])
    receipt_bytes = normalized_json(receipt)
    replay_store = _receipt_store(Path(checker_input["repository_root"]), "assertion-replay", receipt)
    publish = {
        "repository": checker_input["repository"],
        "pull_request": checker_input["pull_request"],
        "base_sha": checker_input["base_sha"],
        "original_pre_review_head": checker_input["original_pre_review_head"],
        "key_id": receipt["key_id"],
        "key_epoch": receipt["key_epoch"],
    }
    if replay_store.exists(): shutil.rmtree(replay_store)
    replay_store.mkdir(parents=True)
    try:
        final_name = gate_module._receipt_final_name(gate_module._receipt_scope_id(**publish))
        gate_module.persist_original_receipt(receipt_bytes, replay_store, **publish)
        gate_module.persist_original_receipt(receipt_bytes, replay_store, **publish)
        preserved = gate_module.preserved_receipt_bytes(replay_store, **publish)
        try:
            gate_module.persist_original_receipt(normalized_json({**receipt, "nonce": f"{receipt['nonce']}-replay"}), replay_store, **publish)
        except gate_module.reporter.PilotDataError as error:
            rejection = str(error)
        else:
            raise AssertionFailure("replay boundary is incomplete")
        entries = sorted(path.name for path in replay_store.iterdir())
    finally:
        shutil.rmtree(replay_store)
    if preserved != receipt_bytes or entries != [final_name]:
        raise AssertionFailure("replay store changed across idempotent persist")
    return {"replay_entries": entries, "replay_sha256": hashlib.sha256(preserved).hexdigest(), "replay_rejection": rejection}


def evaluate_wire_stale_bindings(
    checker_input: dict[str, Any], base_root: Path
) -> dict[str, Any]:
    checker = load_base_checker(base_root)
    positive = checker.validate_review_context_binding(
        review_round=checker_input["review_round"],
        review_context=copy.deepcopy(checker_input["review_context"]),
        all_remote_reviews=copy.deepcopy(checker_input["all_remote_reviews"]),
        candidate_sha=checker_input["candidate_sha"],
        remote_finding_ids=copy.deepcopy(checker_input["remote_finding_ids"]),
    )
    stale_head = copy.deepcopy(checker_input["review_context"])
    stale_head["candidate_sha"] = checker_input["original_pre_review_head"]
    try:
        checker.validate_review_context_binding(
            review_round=checker_input["review_round"],
            review_context=stale_head,
            all_remote_reviews=copy.deepcopy(checker_input["all_remote_reviews"]),
            candidate_sha=checker_input["candidate_sha"],
            remote_finding_ids=copy.deepcopy(checker_input["remote_finding_ids"]),
        )
    except checker.CheckError as error:
        head_rejection = str(error)
    else:
        raise AssertionFailure("trusted stale-binding checks are incomplete")
    stale_round = copy.deepcopy(checker_input["review_context"])
    stale_round["round"] = (
        checker_input["review_round"] + 1
        if checker_input["review_round"] == 1
        else 1
    )
    try:
        checker.validate_review_context_binding(
            review_round=checker_input["review_round"],
            review_context=stale_round,
            all_remote_reviews=copy.deepcopy(checker_input["all_remote_reviews"]),
            candidate_sha=checker_input["candidate_sha"],
            remote_finding_ids=copy.deepcopy(checker_input["remote_finding_ids"]),
        )
    except checker.CheckError as error:
        round_rejection = str(error)
    else:
        raise AssertionFailure("trusted stale-binding checks are incomplete")
    return {
        "validated_round": positive[0]["round"],
        "head_rejection": head_rejection,
        "round_rejection": round_rejection,
    }


def evaluate_member_dispatch(
    family: str,
    member: str,
    root: Path,
    *,
    base_root: Path,
    checker_input: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    if family == "action":
        return evaluate_action_member(
            member,
            base_root=base_root,
            checker_input=checker_input,
            binding=binding,
        )
    if (family, member) == ("generated", "owners"):
        return evaluate_generated_owners(root)
    if (family, member) == ("generated", "outputs"):
        return evaluate_generated_outputs(
            base_root=base_root, checker_input=checker_input
        )
    if (family, member) == ("generated", "consumers"):
        return evaluate_generated_consumers(
            root, base_root=base_root, checker_input=checker_input
        )
    if (family, member) == ("generated", "drift-checks"):
        return evaluate_generated_drift_checks(root, base_root=base_root)
    if (family, member) == ("lifecycle", "entries"):
        return evaluate_lifecycle_entries(checker_input, binding, base_root)
    if (family, member) == ("lifecycle", "preservation"):
        return evaluate_lifecycle_preservation(checker_input, base_root)
    if (family, member) == ("lifecycle", "resets"):
        return evaluate_lifecycle_resets(checker_input, binding, base_root)
    if (family, member) == ("lifecycle", "terminals"):
        return evaluate_lifecycle_terminals(checker_input, base_root)
    if (family, member) == ("resource", "enabled"):
        return evaluate_resource_enabled(checker_input, base_root)
    if (family, member) == ("resource", "disabled"):
        return evaluate_resource_disabled(checker_input, base_root)
    if (family, member) == ("wire", "producers"):
        return evaluate_wire_producers(checker_input, base_root)
    if (family, member) == ("wire", "consumers"):
        return evaluate_wire_consumers(checker_input, base_root)
    if (family, member) == ("wire", "validators"):
        return evaluate_wire_validators(checker_input, base_root)
    if (family, member) == ("wire", "replay"):
        return evaluate_wire_replay(checker_input, base_root)
    if (family, member) == ("wire", "stale-bindings"):
        return evaluate_wire_stale_bindings(checker_input, base_root)
    raise AssertionFailure("member evaluator is not registered")


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
    base_root: Path,
    binding: dict[str, Any],
    checker_input: dict[str, Any],
) -> dict[str, Any]:
    validate_member_tree(base_root)
    validate_member_tree(root)
    return evaluate_member_dispatch(
        family,
        member,
        root,
        base_root=base_root,
        checker_input=checker_input,
        binding=binding,
    )


def execute_member(assertion: dict[str, Any], request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
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
    base_root = Path(expect_string(request["checker_input"]["base_root"], "member checker input.base_root"))
    origin_root = Path(expect_string(request["origin_root"], "member request.origin_root"))
    head_root = Path(expect_string(request["head_root"], "member request.head_root"))
    changed_dependencies = authority_dependency_records(
        family,
        member,
        base_root=base_root,
        checker_input=checker_input,
    )
    if changed_dependencies:
        return (
            "hold",
            authority_hold_output(
                family,
                member,
                binding_output,
                changed_dependencies,
            ),
        )
    outcome = assertion["outcome"]
    if outcome == "affected-fixed":
        try:
            evaluate_member_contract(
                family,
                member,
                origin_root,
                base_root,
                binding_output,
                checker_input,
            )
        except AssertionFailure as error:
            origin_error = str(error)
        else:
            raise AssertionFailure("affected-fixed origin assertion unexpectedly passed")
        head_output = evaluate_member_contract(
            family,
            member,
            head_root,
            base_root,
            binding_output,
            checker_input,
        )
        return (
            "pass",
            {
                **binding_output,
                "program_case": f"member/{family}/{member}/affected-fixed",
                "origin_status": "fail",
                "origin_error": origin_error,
                "head_status": "pass",
                "head_semantic_output": head_output,
            },
        )
    if outcome == "verified-unaffected":
        origin_output = evaluate_member_contract(
            family,
            member,
            origin_root,
            base_root,
            binding_output,
            checker_input,
        )
        head_output = evaluate_member_contract(
            family,
            member,
            head_root,
            base_root,
            binding_output,
            checker_input,
        )
        if origin_output != head_output:
            raise AssertionFailure(
                "verified-unaffected semantic outputs are not equivalent"
            )
        semantic_output_sha256 = hashlib.sha256(
            normalized_json(head_output)
        ).hexdigest()
        return (
            "pass",
            {
                **binding_output,
                "program_case": f"member/{family}/{member}/verified-unaffected",
                "origin_status": "pass",
                "head_status": "pass",
                "semantic_output_sha256": semantic_output_sha256,
            },
        )
    head_output = evaluate_member_contract(
        family,
        member,
        head_root,
        base_root,
        binding_output,
        checker_input,
    )
    if head_output != {"introduction_mode": True}:
        raise AssertionFailure("not-applicable predicate did not establish false")
    return (
        "pass",
        {
            **binding_output,
            "program_case": "member/resource/disabled/not-applicable",
            "applicable": False,
            "reason": assertion["reason"],
        },
    )


def execute(request: Any) -> dict[str, Any]:
    request = expect_object(request, "assertion request")
    assertion_id = request.get("assertion_id")
    if not isinstance(assertion_id, str):
        raise AssertionFailure("assertion request lacks an assertion ID")
    assertion = parse_assertion(assertion_id)
    if assertion["kind"] == "behavior":
        status = "pass"
        output = execute_behavior(assertion, request)
    else:
        status, output = execute_member(assertion, request)
    return {
        "schema_version": 1,
        "assertion_id": assertion_id,
        "status": status,
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
