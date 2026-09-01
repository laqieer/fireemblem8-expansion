#!/usr/bin/env python3
"""Exact-base executable assertions for review-family evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
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
_UNKNOWN = object()


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


def load_plain_module(root: Path, relative: str):
    path = root / relative
    module_name = "review_assertions_" + hashlib.sha256(
        str(path).encode("utf-8")
    ).hexdigest()
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionFailure(f"member artifact {relative!r} is not importable")
    module = importlib.util.module_from_spec(spec)
    previous_module = sys.modules.get(module_name)
    sys.modules[module_name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
    return module


def function_def(module: ast.Module, name: str) -> ast.FunctionDef:
    for statement in module.body:
        if isinstance(statement, ast.FunctionDef) and statement.name == name:
            return statement
    raise AssertionFailure(f"{name} is unavailable")


def class_def(module: ast.Module, name: str) -> ast.ClassDef:
    for statement in module.body:
        if isinstance(statement, ast.ClassDef) and statement.name == name:
            return statement
    raise AssertionFailure(f"{name} is unavailable")


def method_def(module: ast.Module, class_name: str, method_name: str) -> ast.FunctionDef:
    for statement in class_def(module, class_name).body:
        if isinstance(statement, ast.FunctionDef) and statement.name == method_name:
            return statement
    raise AssertionFailure(f"{class_name}.{method_name} is unavailable")


def constant_value(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [constant_value(item) for item in node.elts]
        if any(value is _UNKNOWN for value in values):
            return _UNKNOWN
        if isinstance(node, ast.Tuple):
            return tuple(values)
        if isinstance(node, ast.List):
            return list(values)
        return set(values)
    if isinstance(node, ast.Dict):
        pairs = []
        for key, value in zip(node.keys, node.values):
            resolved_key = constant_value(key)
            resolved_value = constant_value(value)
            if resolved_key is _UNKNOWN or resolved_value is _UNKNOWN:
                return _UNKNOWN
            pairs.append((resolved_key, resolved_value))
        return dict(pairs)
    if isinstance(node, ast.UnaryOp):
        operand = constant_value(node.operand)
        if operand is _UNKNOWN:
            return _UNKNOWN
        try:
            if isinstance(node.op, ast.Not):
                return not operand
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.Invert):
                return ~operand
        except TypeError:
            return _UNKNOWN
        return _UNKNOWN
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result = True
            for value in node.values:
                result = constant_value(value)
                if result is _UNKNOWN:
                    return _UNKNOWN
                if not result:
                    return result
            return result
        if isinstance(node.op, ast.Or):
            last = False
            for value in node.values:
                last = constant_value(value)
                if last is _UNKNOWN:
                    return _UNKNOWN
                if last:
                    return last
            return last
        return _UNKNOWN
    if isinstance(node, ast.BinOp):
        left = constant_value(node.left)
        right = constant_value(node.right)
        if left is _UNKNOWN or right is _UNKNOWN:
            return _UNKNOWN
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.BitOr):
                return left | right
            if isinstance(node.op, ast.BitAnd):
                return left & right
            if isinstance(node.op, ast.BitXor):
                return left ^ right
            if isinstance(node.op, ast.LShift):
                return left << right
            if isinstance(node.op, ast.RShift):
                return left >> right
        except (TypeError, ZeroDivisionError):
            return _UNKNOWN
        return _UNKNOWN
    if isinstance(node, ast.Compare):
        left = constant_value(node.left)
        if left is _UNKNOWN:
            return _UNKNOWN
        current = left
        for operator, comparator in zip(node.ops, node.comparators):
            right = constant_value(comparator)
            if right is _UNKNOWN:
                return _UNKNOWN
            try:
                if isinstance(operator, ast.Eq):
                    matched = current == right
                elif isinstance(operator, ast.NotEq):
                    matched = current != right
                elif isinstance(operator, ast.Is):
                    matched = current is right
                elif isinstance(operator, ast.IsNot):
                    matched = current is not right
                elif isinstance(operator, ast.Lt):
                    matched = current < right
                elif isinstance(operator, ast.LtE):
                    matched = current <= right
                elif isinstance(operator, ast.Gt):
                    matched = current > right
                elif isinstance(operator, ast.GtE):
                    matched = current >= right
                elif isinstance(operator, ast.In):
                    matched = current in right
                elif isinstance(operator, ast.NotIn):
                    matched = current not in right
                else:
                    return _UNKNOWN
            except TypeError:
                return _UNKNOWN
            if not matched:
                return False
            current = right
        return True
    return _UNKNOWN


def constant_truth(node: ast.AST) -> bool | None:
    value = constant_value(node)
    if value is _UNKNOWN:
        return None
    return bool(value)


def iter_value_nodes(value: Any):
    if isinstance(value, ast.AST):
        yield value
        for field, child in ast.iter_fields(value):
            if field in {"body", "orelse", "finalbody", "handlers", "cases"}:
                continue
            yield from iter_value_nodes(child)
        return
    if isinstance(value, list):
        for item in value:
            yield from iter_value_nodes(item)


def _is_docstring_statement(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _iter_nested_blocks(
    statement: ast.stmt, *, descend_definitions: bool
):
    if isinstance(statement, ast.If):
        truth = constant_truth(statement.test)
        if truth is True:
            yield from _iter_live_block(
                statement.body, descend_definitions=descend_definitions
            )
            return
        if truth is False:
            yield from _iter_live_block(
                statement.orelse, descend_definitions=descend_definitions
            )
            return
        yield from _iter_live_block(
            statement.body, descend_definitions=descend_definitions
        )
        yield from _iter_live_block(
            statement.orelse, descend_definitions=descend_definitions
        )
        return
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if descend_definitions:
            yield from _iter_live_block(statement.body, descend_definitions=False)
        return
    if isinstance(statement, ast.ClassDef):
        if descend_definitions:
            yield from _iter_live_block(statement.body, descend_definitions=True)
        return
    for name in ("body", "orelse", "finalbody"):
        value = getattr(statement, name, None)
        if isinstance(value, list):
            yield from _iter_live_block(value, descend_definitions=descend_definitions)
    if isinstance(statement, ast.Match):
        for case in statement.cases:
            yield from _iter_live_block(
                case.body, descend_definitions=descend_definitions
            )


def _iter_live_statement(
    statement: ast.stmt, *, descend_definitions: bool
):
    yield statement
    for field, value in ast.iter_fields(statement):
        if field in {"body", "orelse", "finalbody", "handlers", "cases"}:
            continue
        yield from iter_value_nodes(value)
    yield from _iter_nested_blocks(
        statement, descend_definitions=descend_definitions
    )


def _iter_live_block(
    statements: list[ast.stmt], *, descend_definitions: bool
):
    for statement in statements:
        if _is_docstring_statement(statement):
            continue
        yield from _iter_live_statement(
            statement, descend_definitions=descend_definitions
        )
        if isinstance(statement, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
            break


def iter_live_nodes(node: ast.AST):
    if isinstance(node, ast.Module):
        yield from _iter_live_block(node.body, descend_definitions=True)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        yield from _iter_live_block(node.body, descend_definitions=False)
    elif isinstance(node, ast.ClassDef):
        yield from _iter_live_block(node.body, descend_definitions=True)
    elif isinstance(node, ast.stmt):
        yield from _iter_live_statement(node, descend_definitions=False)
    else:
        yield from iter_value_nodes(node)
    return


def dict_entry_value(node: ast.AST, key: str) -> ast.AST | None:
    if not isinstance(node, ast.Dict):
        return None
    for raw_key, value in zip(node.keys, node.values):
        if isinstance(raw_key, ast.Constant) and raw_key.value == key:
            return value
    return None


def literal_string_set(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        raise AssertionFailure("expected a string collection literal")
    result = set()
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            raise AssertionFailure("collection contains a non-string literal")
        result.add(item.value)
    return result


def path_of(node: ast.AST) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        prefix = path_of(node.value)
        if prefix is None:
            return None
        return (*prefix, node.attr)
    return None


def is_subscript(node: ast.AST, base_name: str, key: str) -> bool:
    if not isinstance(node, ast.Subscript):
        return False
    if not isinstance(node.value, ast.Name) or node.value.id != base_name:
        return False
    slice_node = node.slice
    return isinstance(slice_node, ast.Constant) and slice_node.value == key


def has_live_call(node: ast.AST, path: tuple[str, ...]) -> bool:
    return any(
        isinstance(candidate, ast.Call) and path_of(candidate.func) == path
        for candidate in iter_live_nodes(node)
    )


def assignment_values(node: ast.AST, target_name: str) -> list[ast.AST]:
    values = []
    for candidate in iter_live_nodes(node):
        if isinstance(candidate, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == target_name
                for target in candidate.targets
            ):
                values.append(candidate.value)
        elif isinstance(candidate, ast.AnnAssign):
            target = candidate.target
            if isinstance(target, ast.Name) and target.id == target_name:
                values.append(candidate.value)
    return values


def return_values(node: ast.AST) -> list[ast.AST]:
    return [
        candidate.value
        for candidate in iter_live_nodes(node)
        if isinstance(candidate, ast.Return) and candidate.value is not None
    ]


def compare_paths(node: ast.AST) -> tuple[tuple[str, str], str, str | None] | None:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return None
    if isinstance(node.ops[0], ast.NotEq):
        operator = "!="
    elif isinstance(node.ops[0], ast.Eq):
        operator = "=="
    elif isinstance(node.ops[0], ast.Is):
        operator = "is"
    else:
        return None
    left = node.left
    right = node.comparators[0]
    left_key = None
    for base in ("binding", "finding", "review_context", "data", "trigger"):
        if isinstance(left, ast.Subscript) and isinstance(left.value, ast.Name):
            if left.value.id == base and isinstance(left.slice, ast.Constant):
                left_key = (base, left.slice.value)
    if left_key is None:
        return None
    if isinstance(right, ast.Name):
        return left_key, operator, right.id
    if isinstance(right, ast.Constant):
        return left_key, operator, str(right.value)
    return None


def call_keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


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


def sample_change_records() -> list[dict[str, Any]]:
    return [
        {
            "status": "A",
            "similarity": None,
            "old_path": None,
            "new_path": "docs/new.md",
            "base_mode": None,
            "base_blob_oid": None,
            "head_mode": "100644",
            "head_blob_oid": "1" * 40,
        },
        {
            "status": "D",
            "similarity": None,
            "old_path": "docs/deleted.md",
            "new_path": None,
            "base_mode": "100644",
            "base_blob_oid": "2" * 40,
            "head_mode": None,
            "head_blob_oid": None,
        },
        {
            "status": "M",
            "similarity": None,
            "old_path": "docs/a.md",
            "new_path": "docs/a.md",
            "base_mode": "100644",
            "base_blob_oid": "3" * 40,
            "head_mode": "100644",
            "head_blob_oid": "4" * 40,
        },
        {
            "status": "R",
            "similarity": 90,
            "old_path": "docs/old.md",
            "new_path": "docs/renamed.md",
            "base_mode": "100644",
            "base_blob_oid": "5" * 40,
            "head_mode": "100644",
            "head_blob_oid": "6" * 40,
        },
        {
            "status": "C",
            "similarity": 100,
            "old_path": "docs/source.md",
            "new_path": "docs/copied.md",
            "base_mode": "100644",
            "base_blob_oid": "7" * 40,
            "head_mode": "100644",
            "head_blob_oid": "8" * 40,
        },
    ]


def changed_paths(changes: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            path
            for change in changes
            for path in (change["old_path"], change["new_path"])
            if path is not None
        }
    )


def sample_review_report(
    changes: list[dict[str, Any]],
    *,
    actions: tuple[str, ...] | list[str],
    reviewed_files: list[str] | None = None,
    reviewed_changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "report_id": "PRE_REVIEW_001",
        "repository": "example/project",
        "pull_request": 7,
        "base_sha": "a" * 40,
        "candidate_sha": "b" * 40,
        "reviewer_actor_id": "REVIEWER_1",
        "reviewer_login": "fresh-reviewer",
        "implementer_actor_id": "IMPLEMENTER_1",
        "implementer_login": "implementer",
        "started_at": "2026-09-01T00:00:00Z",
        "completed_at": "2026-09-01T00:01:00Z",
        "permissions": ["contents:read"],
        "actions": list(actions),
        "reviewed_files": changed_paths(changes) if reviewed_files is None else reviewed_files,
        "reviewed_changes": changes if reviewed_changes is None else reviewed_changes,
        "findings": [
            {
                "id": "LOCAL-ACTION-1",
                "family": "action",
                "created_at": "2026-09-01T00:00:30Z",
            }
        ],
    }


def validate_sample_report(
    checker,
    *,
    actions: tuple[str, ...] | list[str],
    reviewed_files: list[str] | None = None,
    reviewed_changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    changes = checker.validate_change_records(sample_change_records(), "changes")
    report = sample_review_report(
        changes,
        actions=actions,
        reviewed_files=reviewed_files,
        reviewed_changes=reviewed_changes,
    )
    return checker._validate_report(
        report,
        repository="example/project",
        pull_request=7,
        base_sha="a" * 40,
        candidate_sha="b" * 40,
        changed_files=changed_paths(changes),
        changes=changes,
    )


def expect_checker_rejection(checker, callback, message: str) -> str:
    try:
        callback()
    except checker.CheckError as error:
        return str(error)
    raise AssertionFailure(message)


def evaluate_action_actions(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    checker = load_plain_module(root, "scripts/workflow_pilot/review_base_checker.py")
    family = load_python_ast(root, "scripts/workflow_pilot/review_family.py")
    producer = tuple(checker.ACTION_SEQUENCE)
    consumer = assign_string_sequence(family, "READ_ONLY_ACTIONS")
    expected = ("read-candidate", "emit-local-report")
    if producer != expected or consumer != expected:
        raise AssertionFailure("read-only action sequence is not exact")
    positive = validate_sample_report(checker, actions=producer)
    expect_checker_rejection(
        checker,
        lambda: validate_sample_report(
            checker, actions=("emit-local-report", "read-candidate")
        ),
        "read-only action sequence is not enforced",
    )
    return {"sequence": positive["actions"]}


def evaluate_action_items(
    root: Path, binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    if binding is None:
        raise AssertionFailure("member authority binding is unavailable")
    checker = load_plain_module(root, "scripts/workflow_pilot/review_base_checker.py")
    primary_finding_id = binding["finding_id"]
    alternate_finding_id = f"{primary_finding_id}-ALT"
    alternate_review_id = f"{binding['finding_review_id']}-ALT"
    data = {
        "round_findings": {
            primary_finding_id: {
                "family": "action",
                "review_id": binding["finding_review_id"],
                "review_round": binding["finding_review_round"],
                "finding_head_sha": binding["finding_head_sha"],
                "finding_head_tree": binding["finding_head_tree"],
                "finding_origin_sha": binding["finding_origin_sha"],
                "finding_origin_tree": binding["finding_origin_tree"],
            },
            alternate_finding_id: {
                "family": "action",
                "review_id": alternate_review_id,
                "review_round": binding["finding_review_round"] + 1,
                "finding_head_sha": "1" * 40,
                "finding_head_tree": "2" * 40,
                "finding_origin_sha": "3" * 40,
                "finding_origin_tree": "4" * 40,
            },
        },
        "candidate_sha": binding["head_sha"],
        "candidate_tree": binding["head_tree"],
    }
    parsed = {
        "family": "action",
        "member": "items",
        "outcome": "affected-fixed",
        "reason": None,
    }
    request_binding = checker._bind_member_request(
        data,
        parsed,
        primary_finding_id,
    )
    if request_binding != {
        "finding_id": primary_finding_id,
        "finding_family": "action",
        "finding_member": "items",
        "finding_review_id": binding["finding_review_id"],
        "finding_review_round": binding["finding_review_round"],
        "finding_head_sha": binding["finding_head_sha"],
        "finding_head_tree": binding["finding_head_tree"],
        "finding_origin_sha": binding["finding_origin_sha"],
        "finding_origin_tree": binding["finding_origin_tree"],
        "head_sha": binding["head_sha"],
        "head_tree": binding["head_tree"],
    }:
        raise AssertionFailure("member-item authority binding is incomplete")
    alternate_binding = checker._bind_member_request(
        data,
        parsed,
        alternate_finding_id,
    )
    if alternate_binding != {
        "finding_id": alternate_finding_id,
        "finding_family": "action",
        "finding_member": "items",
        "finding_review_id": alternate_review_id,
        "finding_review_round": binding["finding_review_round"] + 1,
        "finding_head_sha": "1" * 40,
        "finding_head_tree": "2" * 40,
        "finding_origin_sha": "3" * 40,
        "finding_origin_tree": "4" * 40,
        "head_sha": binding["head_sha"],
        "head_tree": binding["head_tree"],
    }:
        raise AssertionFailure("member-item authority binding is incomplete")
    return {"checker_binding": True, "assertion_binding": True}


def evaluate_action_targets(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    checker = load_plain_module(root, "scripts/workflow_pilot/review_base_checker.py")
    changes = checker.validate_change_records(sample_change_records(), "changes")
    validate_sample_report(checker, actions=checker.ACTION_SEQUENCE)
    expect_checker_rejection(
        checker,
        lambda: validate_sample_report(
            checker,
            actions=checker.ACTION_SEQUENCE,
            reviewed_files=["docs/new.md"],
        ),
        "exact changed-file coverage is not enforced",
    )
    return {"statuses": sorted({change["status"] for change in changes})}


def evaluate_generated_owners(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    feature, _ = workflow_registry(root)
    issue_urls = sorted(feature["issue_urls"])
    required_cases = sorted(feature["required_cases"])
    if CURRENT_IMPLEMENTATION_ISSUE not in issue_urls:
        raise AssertionFailure("workflow-governance registry does not claim issue #179")
    if WORKFLOW_REVIEW_FAMILY_CASE not in required_cases:
        raise AssertionFailure("workflow-governance registry does not include the review-family case")
    return {"issue_urls": issue_urls, "required_cases": required_cases}


def evaluate_generated_outputs(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    candidate = load_plain_module(root, "scripts/workflow_pilot/candidate_evidence.py")
    classifier = load_plain_module(root, "scripts/workflow_pilot/event_classifier.py")
    worker_job_ids = tuple(candidate.WORKER_JOB_IDS)
    full_classifier = candidate.FULL_CLASSIFIER
    full_attestation = candidate.FULL_ATTESTATION
    metadata_classifier = candidate.METADATA_CLASSIFIER
    metadata_attestation = candidate.METADATA_ATTESTATION
    decision_fields = tuple(classifier.EventDecision.__annotations__)
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


def evaluate_generated_consumers(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    topology = load_python_ast(root, "tests/workflows/test_build_ci_topology.py")
    triggered_jobs = function_def(topology, "_triggered_jobs")
    metadata_contexts = method_def(
        topology,
        "ConsolidatedBuildTopologyTests",
        "test_metadata_check_contexts_cannot_replace_candidate_contexts",
    )
    imports = [
        statement
        for statement in topology.body
        if isinstance(statement, ast.ImportFrom)
        and statement.module == "scripts.workflow_pilot"
    ]
    imported = {
        alias.asname or alias.name
        for statement in imports
        for alias in statement.names
    }
    if not {"candidate_evidence", "event_classifier"}.issubset(imported):
        raise AssertionFailure(
            "workflow topology tests do not import candidate evidence and classifier outputs"
        )
    if "CANDIDATE_FULL_JOBS" not in {
        target.id
        for statement in topology.body
        if isinstance(statement, ast.Assign)
        for target in statement.targets
        if isinstance(target, ast.Name)
    }:
        raise AssertionFailure("workflow topology tests do not define candidate full jobs")
    assign_string_constant(topology, "WORKFLOW_PILOT_BASELINE_GATE")
    if not has_live_call(triggered_jobs, ("event_classifier", "classify_event")):
        raise AssertionFailure("workflow topology tests do not execute event classification")
    attribute_paths = {
        path_of(node)
        for node in iter_live_nodes(metadata_contexts)
        if isinstance(node, ast.Attribute)
    }
    if not {
        ("candidate_evidence", "FULL_ATTESTATION"),
        ("candidate_evidence", "METADATA_ATTESTATION"),
        ("candidate_evidence", "FULL_CLASSIFIER"),
        ("candidate_evidence", "METADATA_CLASSIFIER"),
    }.issubset(attribute_paths):
        raise AssertionFailure("workflow topology tests do not evaluate candidate evidence")
    return {"topology_consumer": True}


def evaluate_generated_drift_checks(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    docs = load_python_ast(root, "scripts/docs_check_tests/test_check_docs.py")
    skill = load_python_ast(
        root, "scripts/docs_check_tests/test_development_workflow_skill.py"
    )
    registry_checks = method_def(
        docs,
        "TesterCaseRegistryTests",
        "test_late_shipped_contracts_are_complete_and_fail_closed",
    )
    manual_handoff = method_def(
        skill,
        "DevelopmentWorkflowSkillTests",
        "test_manual_handoff_json_contract_and_human_links",
    )
    if not any(
        isinstance(node, ast.Dict)
        and dict_entry_value(node, WORKFLOW_REVIEW_FAMILY_CASE) is not None
        for node in iter_live_nodes(registry_checks)
    ):
        raise AssertionFailure("docs drift checks do not cover the review-family case")
    if not any(
        isinstance(node, ast.Dict)
        and dict_entry_value(node, WORKFLOW_FEATURE_ID) is not None
        for node in iter_live_nodes(registry_checks)
    ):
        raise AssertionFailure("docs drift checks do not cover workflow-governance")
    if not any(
        isinstance(value, ast.List)
        and WORKFLOW_REVIEW_FAMILY_CASE in literal_string_set(value)
        for value in assignment_values(manual_handoff, "expected_cases")
    ):
        raise AssertionFailure("docs drift checks do not cover the review-family case")
    if not any(
        isinstance(node, ast.Call)
        and path_of(node.func) == ("compare_string_membership",)
        and len(node.args) >= 3
        and isinstance(node.args[1], ast.Name)
        and node.args[1].id == "expected_cases"
        and isinstance(node.args[2], ast.Constant)
        and node.args[2].value == "workflow-governance.required_cases"
        for node in iter_live_nodes(manual_handoff)
    ):
        raise AssertionFailure("docs drift checks do not cover workflow-governance")
    return {"docs_checks": True}


def evaluate_lifecycle_entries(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    family = load_python_ast(root, "scripts/workflow_pilot/review_family.py")
    progress = function_def(family, "_progress_rounds")
    pending_values = assignment_values(progress, "pending")
    if not any(
        isinstance(value, ast.Dict)
        and isinstance(dict_entry_value(value, "reason"), ast.Constant)
        and dict_entry_value(value, "reason").value
        == "third-consecutive-change-request"
        for value in pending_values
    ):
        raise AssertionFailure("lifecycle hold-entry contract is incomplete")
    append_calls = [
        node
        for node in iter_live_nodes(progress)
        if isinstance(node, ast.Call) and path_of(node.func) == ("handoffs", "append")
    ]
    if not append_calls:
        raise AssertionFailure("lifecycle handoff append is unavailable")
    if not any(
        call.args
        and isinstance(call.args[0], ast.Dict)
        and isinstance(dict_entry_value(call.args[0], "finding_handoffs"), ast.Name)
        and dict_entry_value(call.args[0], "finding_handoffs").id == "finding_sweeps"
        and isinstance((bounds := dict_entry_value(call.args[0], "bounds")), ast.Dict)
        and {key.value for key in bounds.keys if isinstance(key, ast.Constant)}
        == {"findings", "families", "siblings"}
        for call in append_calls
    ):
        raise AssertionFailure("lifecycle handoff bounds are incomplete")
    return {"hold_reason": "third-consecutive-change-request"}


def evaluate_lifecycle_preservation(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    trusted = load_python_ast(root, "scripts/workflow_pilot/trusted_review_gate.py")
    run = function_def(trusted, "_run_trusted_gate")
    main = function_def(trusted, "main")
    if not any(
        isinstance(node, ast.FunctionDef) and node.name == "_preserved_receipt_bytes"
        for node in trusted.body
    ):
        raise AssertionFailure("receipt preservation helper is unavailable")
    verify_calls = [
        node
        for node in iter_live_nodes(run)
        if isinstance(node, ast.Call)
        and path_of(node.func) == ("_verify_signed_receipt_bytes",)
    ]
    if not any(call_keyword(call, "consume_nonce") is not None for call in verify_calls):
        raise AssertionFailure("receipt preservation does not bind nonce consumption")
    if not any(call_keyword(call, "require_preserved") is not None for call in verify_calls):
        raise AssertionFailure("receipt preservation does not bind preserved replay")
    if not has_live_call(main, ("_preserved_receipt_bytes",)):
        raise AssertionFailure("receipt preservation is not reachable from main")
    return {"preserved_receipt": True}


def evaluate_lifecycle_resets(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    progress = function_def(
        load_python_ast(root, "scripts/workflow_pilot/review_family.py"),
        "_progress_rounds",
    )
    consecutive_resets = sum(
        isinstance(value, ast.Constant) and value.value == 0
        for value in assignment_values(progress, "consecutive")
    )
    pending_resets = sum(
        isinstance(value, ast.Constant) and value.value is None
        for value in assignment_values(progress, "pending")
    )
    if consecutive_resets < 2 or pending_resets < 2:
        raise AssertionFailure("lifecycle reset paths are incomplete")
    return {"resets": 2}


def evaluate_lifecycle_terminals(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    trusted = load_python_ast(root, "scripts/workflow_pilot/trusted_review_gate.py")
    bootstrap = function_def(trusted, "_bootstrap_result")
    returns = return_values(bootstrap)
    if not returns:
        raise AssertionFailure("bootstrap result is unavailable")
    if not any(
        isinstance(value, ast.Dict)
        and isinstance((gates := dict_entry_value(value, "gates")), ast.Dict)
        and isinstance(dict_entry_value(gates, "push_allowed"), ast.Constant)
        and dict_entry_value(gates, "push_allowed").value is False
        and isinstance(dict_entry_value(gates, "trusted_push_allowed"), ast.Constant)
        and dict_entry_value(gates, "trusted_push_allowed").value is False
        and isinstance(dict_entry_value(gates, "merge_allowed"), ast.Constant)
        and dict_entry_value(gates, "merge_allowed").value is False
        for value in returns
    ):
        raise AssertionFailure("terminal gate contract is incomplete")
    return {"terminal_gates": True}


def evaluate_resource_enabled(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
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
    trusted = load_python_ast(root, "scripts/workflow_pilot/trusted_review_gate.py")
    family = load_python_ast(root, "scripts/workflow_pilot/review_family.py")
    loader = function_def(trusted, "_load_authoritative_trigger")
    if not has_live_call(loader, ("reporter", "load_decisions_from_commit")):
        raise AssertionFailure("enabled resource boundary does not load base-owned trigger decisions")
    if not any(
        any(
            isinstance(node, ast.Call)
            and path_of(node.func) == ("_minimal_git",)
            and len(node.args) >= 3
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "rev-parse"
            for node in iter_value_nodes(value)
        )
        for value in assignment_values(loader, "blob_oid")
    ):
        raise AssertionFailure("enabled resource boundary does not bind the base decision blob")
    if not any(
        isinstance(node, ast.FunctionDef) and node.name == "_resolve_authoritative_trigger"
        for node in family.body
    ):
        raise AssertionFailure("enabled resource boundary does not consume the authoritative trigger")
    if not (
        trigger["risk_boundaries"] == ["lifecycle", "protocol"]
        and trigger["threshold_triggers"] == ["changed-files", "risk-boundary"]
    ):
        raise AssertionFailure("PR 189 authoritative trigger decision is incomplete")
    return trigger


def evaluate_resource_disabled(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    trusted = load_python_ast(root, "scripts/workflow_pilot/trusted_review_gate.py")
    run = function_def(trusted, "_run_trusted_gate")
    bootstrap = function_def(trusted, "_bootstrap_result")
    if not any(
        isinstance(node, ast.If)
        and any(
            isinstance(child, ast.Compare)
            and isinstance(child.left, ast.Name)
            and child.left.id == "authoritative_trigger"
            and isinstance(child.ops[0], ast.Is)
            and isinstance(child.comparators[0], ast.Constant)
            and child.comparators[0].value is None
            for child in ast.walk(node.test)
        )
        for node in iter_live_nodes(run)
    ):
        raise AssertionFailure("introduction-mode disabled boundary is incomplete")
    if not any(
        isinstance(value, ast.Dict)
        and isinstance((bootstrap_info := dict_entry_value(value, "bootstrap")), ast.Dict)
        and isinstance(dict_entry_value(bootstrap_info, "mode"), ast.Constant)
        and dict_entry_value(bootstrap_info, "mode").value == "introduction"
        for value in return_values(bootstrap)
    ):
        raise AssertionFailure("introduction-mode disabled boundary is incomplete")
    return {"introduction_mode": True}


def evaluate_wire_producers(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    trusted = load_python_ast(root, "scripts/workflow_pilot/trusted_review_gate.py")
    run = function_def(trusted, "_run_trusted_gate")
    collect = function_def(trusted, "collect_live_evidence_bytes")
    if not has_live_call(run, ("run_base_pinned_checker",)):
        raise AssertionFailure("wire producers are incomplete")
    if not any(
        isinstance(value, ast.Dict)
        and dict_entry_value(value, "authoritative_trigger") is not None
        and dict_entry_value(value, "execution_receipts") is not None
        and dict_entry_value(value, "result_manifest") is not None
        for value in assignment_values(collect, "raw_evidence")
    ):
        raise AssertionFailure("wire producers are incomplete")
    return {"producers": True}


def evaluate_wire_consumers(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    family = load_python_ast(root, "scripts/workflow_pilot/review_family.py")
    validate = function_def(family, "validate_evidence")
    build = function_def(family, "build_report")
    if not any(
        isinstance(node, ast.Call)
        and path_of(node.func) == ("reporter", "expect_keys")
        and len(node.args) >= 3
        and isinstance(node.args[2], ast.Tuple)
        and {"authoritative_trigger", "execution_receipts", "result_manifest"}.issubset(
            literal_string_set(node.args[2])
        )
        for node in iter_live_nodes(validate)
    ):
        raise AssertionFailure("wire consumers are incomplete")
    if not has_live_call(build, ("_resolve_authoritative_trigger",)) or not has_live_call(
        build, ("_validate_execution",)
    ):
        raise AssertionFailure("wire consumers are incomplete")
    return {"consumers": True}


def evaluate_wire_validators(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    checker = load_python_ast(root, "scripts/workflow_pilot/review_base_checker.py")
    family = load_python_ast(root, "scripts/workflow_pilot/review_family.py")
    execute_registry = function_def(checker, "execute_registry")
    validate_input_fn = function_def(checker, "validate_input")
    resolve_trigger = function_def(family, "_resolve_authoritative_trigger")
    if not has_live_call(execute_registry, ("_validate_program_output_binding",)):
        raise AssertionFailure("checker validators are incomplete")
    if not any(
        compare_paths(node) == (("review_context", "candidate_sha"), "!=", "candidate_sha")
        for node in iter_live_nodes(validate_input_fn)
    ):
        raise AssertionFailure("checker validators do not bind review candidate identity")
    if not any(
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Subscript)
        and is_subscript(node.left, "trigger", "trigger")
        for node in iter_live_nodes(resolve_trigger)
    ) and not has_live_call(resolve_trigger, ("reporter", "run_git")):
        raise AssertionFailure("report validators are incomplete")
    return {"validators": True}


def evaluate_wire_replay(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    trusted = load_python_ast(root, "scripts/workflow_pilot/trusted_review_gate.py")
    run = function_def(trusted, "_run_trusted_gate")
    names = {
        node.name
        for node in trusted.body
        if isinstance(node, ast.FunctionDef)
    }
    if not {
        "_verify_signed_receipt_bytes",
        "_preserved_receipt_bytes",
        "_execution_receipt_seal",
    }.issubset(names):
        raise AssertionFailure("replay boundary is incomplete")
    verify_calls = [
        node
        for node in iter_live_nodes(run)
        if isinstance(node, ast.Call)
        and path_of(node.func) == ("_verify_signed_receipt_bytes",)
    ]
    if not any(call_keyword(call, "consume_nonce") is not None for call in verify_calls):
        raise AssertionFailure("replay boundary is incomplete")
    if not any(call_keyword(call, "require_preserved") is not None for call in verify_calls):
        raise AssertionFailure("replay boundary is incomplete")
    return {"replay": True}


def evaluate_wire_stale_bindings(
    root: Path, _binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    trusted = load_python_ast(root, "scripts/workflow_pilot/trusted_review_gate.py")
    checker = load_python_ast(root, "scripts/workflow_pilot/review_base_checker.py")
    collect = function_def(trusted, "collect_live_evidence_bytes")
    validate_input_fn = function_def(checker, "validate_input")
    if not any(
        compare_paths(node) == (("review_context", "candidate_sha"), "!=", "candidate_sha")
        for node in iter_live_nodes(validate_input_fn)
    ) or not any(
        compare_paths(node) == (("review_context", "round"), "!=", "review_round")
        for node in iter_live_nodes(validate_input_fn)
    ):
        raise AssertionFailure("checker stale-binding checks are incomplete")
    if not any(
        compare_paths(node) == (("pr", "headRefOid"), "!=", "expected_remote_head")
        or (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "head"
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.NotEq)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Name)
            and node.comparators[0].id == "expected_remote_head"
        )
        for node in iter_live_nodes(collect)
    ):
        raise AssertionFailure("trusted stale-binding checks are incomplete")
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


def evaluate_member_contract(
    family: str,
    member: str,
    root: Path,
    binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected = {path for path in ASSERTION_INPUT_PATHS}
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
        raise AssertionFailure("member artifact tree does not match the allowlisted production inputs")
    evaluator = MEMBER_EVALUATORS.get((family, member))
    if evaluator is None:
        raise AssertionFailure("member evaluator is not registered")
    return evaluator(root, binding)


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
            evaluate_member_contract(family, member, origin_root, binding_output)
        except AssertionFailure as error:
            origin_error = str(error)
        else:
            raise AssertionFailure(
                "affected-fixed origin assertion unexpectedly passed"
            )
        head_output = evaluate_member_contract(family, member, head_root, binding_output)
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
            family, member, origin_root, binding_output
        )
        head_output = evaluate_member_contract(family, member, head_root, binding_output)
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
    head_output = evaluate_member_contract(family, member, head_root, binding_output)
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
