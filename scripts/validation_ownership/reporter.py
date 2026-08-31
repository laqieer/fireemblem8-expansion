#!/usr/bin/env python3
"""Validate and explain the repository's fail-closed ownership graph."""

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
import stat
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from scripts import check_docs
from scripts.upstream_port import verify as workflow_verify
from scripts.workflow_pilot import reporter as pilot_reporter


GRAPH_PATH = Path(".github/validation-ownership-graph.json")
SCHEMA_PATH = Path("scripts/validation_ownership/graph.schema.json")
PROBE_ORACLE_PATH = Path("scripts/validation_ownership/probe-oracle.json")
TEST_CASE_REGISTRY_PATH = Path("docs/test-cases/registry.json")
BUILD_WORKFLOW_PATH = Path(".github/workflows/build.yml")
EXPECTED_SCHEMA_VERSION = 1
EDGE_SEAL_DOMAIN = b"validation-ownership-resolved-edges-v1\0"
GRAPH_SEAL_DOMAIN = b"validation-ownership-graph-v1\0"
SCHEMA_SEAL_DOMAIN = b"validation-ownership-schema-v1\0"
PROBE_SEAL_DOMAIN = b"validation-ownership-probe-oracle-v1\0"
REQUIRED_PROOF_KINDS = {
    "artifact_checkpoint",
    "dependency_changed",
    "pre_graduation",
}
LIFECYCLE_FAILURE_REASON = (
    "removal loses the issue #180 validation ownership invariant"
)
LIFECYCLE_CHECKS = {
    "validation-ownership-check",
    "TC-WORKFLOW-GATE-OWNERSHIP-001",
}
LIFECYCLE_TIMEOUT_SECONDS = 30
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
EDGE_TYPES = set(EDGE_TARGET_TYPES) | {"depends-on"}
REGULAR_BLOB_MODE_RE = re.compile(r"^100[0-7]{3}$")
SYMLINK_MODE = "120000"
GITLINK_MODE = "160000"


@dataclass(frozen=True)
class GitTreeEntry:
    path: str
    mode: str
    object_type: str
    object_id: str


@dataclass(frozen=True)
class ScratchDirectory:
    path: Path
    created: tuple[Path, ...]


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


def prepare_validation_scratch(root: Path) -> ScratchDirectory:
    try:
        root_lstat = os.lstat(root)
        resolved_root = root.resolve(strict=True)
    except OSError as error:
        raise OwnershipError(f"cannot inspect scratch authority root: {error}") from error
    if stat.S_ISLNK(root_lstat.st_mode) or not stat.S_ISDIR(root_lstat.st_mode):
        raise OwnershipError("scratch authority root must be a non-symlink directory")

    parts = ("build", "test-artifacts", "validation-ownership")
    created = []
    current_path = resolved_root
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        current_fd = os.open(resolved_root, flags)
    except OSError as error:
        raise OwnershipError(f"cannot open scratch authority root safely: {error}") from error
    success = False
    try:
        for part in parts:
            try:
                os.mkdir(part, mode=0o700, dir_fd=current_fd)
                created.append(current_path / part)
            except FileExistsError:
                pass
            try:
                entry_stat = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            except OSError as error:
                raise OwnershipError(
                    f"cannot lstat scratch component {current_path / part}: {error}"
                ) from error
            if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(entry_stat.st_mode):
                raise OwnershipError(
                    f"scratch component {current_path / part} must be a non-symlink directory"
                )
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except OSError as error:
                raise OwnershipError(
                    f"cannot open scratch component {current_path / part} safely: {error}"
                ) from error
            opened_stat = os.fstat(next_fd)
            if (
                opened_stat.st_dev != entry_stat.st_dev
                or opened_stat.st_ino != entry_stat.st_ino
            ):
                os.close(next_fd)
                raise OwnershipError(
                    f"scratch component {current_path / part} was replaced during validation"
                )
            os.close(current_fd)
            current_fd = next_fd
            current_path = current_path / part
            try:
                resolved_component = current_path.resolve(strict=True)
            except OSError as error:
                raise OwnershipError(
                    f"cannot resolve scratch component {current_path}: {error}"
                ) from error
            if resolved_root not in resolved_component.parents:
                raise OwnershipError(
                    f"scratch component {current_path} escapes repository root"
                )
        success = True
    finally:
        os.close(current_fd)
        if not success:
            for path in reversed(created):
                try:
                    path.rmdir()
                except OSError:
                    pass
    return ScratchDirectory(current_path, tuple(created))


def cleanup_validation_scratch(scratch: ScratchDirectory) -> None:
    for path in reversed(scratch.created):
        try:
            path.rmdir()
        except OSError:
            pass


def _validate_relative_path(relative: str | Path, label: str) -> str:
    value = Path(relative).as_posix()
    if (
        not value
        or value.startswith("/")
        or value == "."
        or ".." in Path(value).parts
        or "\0" in value
    ):
        raise OwnershipError(f"{label} must be a confined repository-relative path")
    return value


def git_tree_entries(root: Path, revision: str = "HEAD") -> dict[str, GitTreeEntry]:
    output = _git(root, "ls-tree", "-rz", "--full-tree", revision).stdout
    try:
        records = output.decode("utf-8").split("\0")
    except UnicodeDecodeError as error:
        raise OwnershipError("Git tree paths are not valid UTF-8") from error
    result = {}
    for record in records:
        if not record:
            continue
        try:
            header, path = record.split("\t", 1)
            mode, object_type, object_id = header.split(" ")
        except ValueError as error:
            raise OwnershipError("Git returned a malformed tree entry") from error
        _validate_relative_path(path, "Git tree path")
        if path in result:
            raise OwnershipError(f"Git tree repeats path {path!r}")
        result[path] = GitTreeEntry(path, mode, object_type, object_id)
    if not result:
        raise OwnershipError(f"Git tree {revision!r} contains no entries")
    return result


def tracked_paths(root: Path) -> tuple[str, ...]:
    return tuple(sorted(git_tree_entries(root)))


class AuthorityLoader:
    """Load authority only from one validated Git tree and confined root."""

    def __init__(
        self,
        root: Path,
        entries: dict[str, GitTreeEntry],
        revision: str | None = None,
    ):
        self.root = root
        self.entries = entries
        self.revision = revision

    def entry(self, relative: str | Path, label: str) -> GitTreeEntry:
        path = _validate_relative_path(relative, label)
        entry = self.entries.get(path)
        if entry is None:
            raise OwnershipError(f"{label} {path!r} is not tracked by the selected Git tree")
        if not REGULAR_BLOB_MODE_RE.fullmatch(entry.mode) or entry.object_type != "blob":
            raise OwnershipError(
                f"{label} {path!r} must be a tracked regular blob, got "
                f"mode {entry.mode} type {entry.object_type}"
            )
        return entry

    def read_blob(self, relative: str | Path, label: str) -> bytes:
        entry = self.entry(relative, label)
        if self.revision is not None:
            completed = _git(
                self.root,
                "cat-file",
                "blob",
                entry.object_id,
            )
            return completed.stdout
        path = self.root / entry.path
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise OwnershipError(f"{label} {entry.path!r} is unavailable: {error}") from error
        if path.is_symlink() or not resolved.is_file():
            raise OwnershipError(f"{label} {entry.path!r} must be a regular file")
        if self.root not in resolved.parents:
            raise OwnershipError(f"{label} {entry.path!r} escapes repository root")
        try:
            return resolved.read_bytes()
        except OSError as error:
            raise OwnershipError(f"cannot read {label} {entry.path!r}: {error}") from error

    def read_json(self, relative: str | Path, label: str) -> Any:
        try:
            text = self.read_blob(relative, label).decode("utf-8")
        except UnicodeDecodeError as error:
            raise OwnershipError(f"{label} is not valid UTF-8") from error
        return parse_json(text, label)


def repository_status(root: Path) -> bytes:
    return _git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    ).stdout


def _generated_registry_records(
    loader: AuthorityLoader,
) -> tuple[list[dict[str, Any]], set[str]]:
    generated_modules = [
        path
        for path in loader.entries
        if path.startswith("scripts/generated_data/") and path.endswith(".py")
    ]
    if not generated_modules:
        raise OwnershipError("generated-data registry has no tracked Python authority")
    for path in generated_modules:
        loader.read_blob(path, "generated-data authority")
    generated_data_registry = importlib.import_module(
        "scripts.generated_data.registry"
    )

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
            if candidate in loader.entries:
                loader.entry(candidate, f"generated-data schema {name!r} {field}")
                paths.add(candidate)
            elif not any(
                path.startswith(candidate.rstrip("/") + "/")
                for path in loader.entries
            ):
                raise OwnershipError(
                    f"generated-data schema {name!r} references stale {field} "
                    f"{candidate!r}"
                )
    return records, paths


def _generated_registry_semantics(loader: AuthorityLoader) -> list[dict[str, str]]:
    records = []
    for path in sorted(loader.entries):
        if not path.startswith("scripts/generated_data/") or not path.endswith(".py"):
            continue
        try:
            text = loader.read_blob(path, "generated-data authority").decode("utf-8")
            syntax = ast.dump(ast.parse(text, filename=path), include_attributes=False)
        except (UnicodeDecodeError, SyntaxError) as error:
            raise OwnershipError(
                f"generated-data authority {path!r} is not valid Python: {error}"
            ) from error
        records.append({"path": path, "syntax": syntax})
    return records


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


def _load_test_case_registry(
    loader: AuthorityLoader,
) -> dict[str, dict[str, Any]]:
    registry = loader.read_json(
        TEST_CASE_REGISTRY_PATH,
        "tester-case registry",
    )
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


def _canonical_workflow_lines(text: str) -> tuple[str, ...]:
    return tuple(
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _generic_workflow_authorities(
    text: str,
) -> tuple[dict[str, Any], dict[tuple[str, str], Any]]:
    try:
        jobs = workflow_verify._workflow_job_entries(text)
    except ValueError as error:
        raise OwnershipError(f"Build workflow authority is invalid: {error}") from error
    job_records = {}
    step_records = {}
    for job_name, body in jobs:
        lines = body.splitlines(keepends=True)
        starts = [
            index
            for index, line in enumerate(lines)
            if re.match(r"^    -(?:[ \t]|\r?\n?\Z)", line)
        ]
        steps = []
        names = set()
        for position, start in enumerate(starts):
            block = "".join(
                lines[
                    start :
                    starts[position + 1]
                    if position + 1 < len(starts)
                    else len(lines)
                ]
            )
            match = re.search(r"^    - name:[ \t]*(.+?)\s*$", block, re.MULTILINE)
            if match is None:
                continue
            name = match.group(1).strip().strip("\"'")
            if not name or name in names:
                raise OwnershipError(
                    f"Build workflow job {job_name!r} has missing or duplicate step name"
                )
            names.add(name)
            record = _canonical_workflow_lines(block)
            step_records[(job_name, name)] = record
            steps.append((name, record))
        before_steps = body.split("\n    steps:", 1)[0]
        job_records[job_name] = {
            "context": tuple(sorted(_canonical_workflow_lines(before_steps))),
            "steps": tuple(steps),
        }
    return job_records, step_records


def _workflow_authorities(
    loader: AuthorityLoader,
    *,
    strict: bool,
) -> tuple[dict[str, Any], dict[tuple[str, str], Any]]:
    try:
        text = loader.read_blob(
            BUILD_WORKFLOW_PATH,
            "Build workflow authority",
        ).decode("utf-8")
    except UnicodeDecodeError as error:
        raise OwnershipError("Build workflow authority is not valid UTF-8") from error
    if strict:
        try:
            workflow_verify._parse_workflow_structure_text(text)
        except ValueError as error:
            raise OwnershipError(f"Build workflow authority is invalid: {error}") from error
    return _generic_workflow_authorities(text)


def _manual_handoff_record(
    loader: AuthorityLoader,
    relative: str,
) -> dict[str, Any]:
    if relative != ".github/manual-testing-handoff.json":
        raise OwnershipError(
            "manual handoff authority must be exactly "
            ".github/manual-testing-handoff.json"
        )
    record = loader.read_json(relative, "manual handoff contract")
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


def _strip_make_comment(line: str) -> str:
    escaped = False
    for index, character in enumerate(line):
        if character == "#" and not escaped:
            return line[:index]
        escaped = character == "\\" and not escaped
        if character != "\\":
            escaped = False
    return line


def _make_logical_lines(text: str) -> list[tuple[bool, str]]:
    result = []
    pending = ""
    for physical in text.splitlines():
        if physical.startswith("\t"):
            if not pending:
                result.append((True, physical[1:]))
                continue
            physical = physical[1:]
        stripped = physical.rstrip()
        continuation = stripped.endswith("\\")
        piece = stripped[:-1] if continuation else stripped
        pending = (pending + " " + piece.strip()).strip() if pending else piece
        if not continuation:
            result.append((False, pending))
            pending = ""
    if pending:
        result.append((False, pending))
    return result


def _make_variable_refs(values: Iterable[str]) -> set[str]:
    return {
        match.group(1)
        for value in values
        for match in re.finditer(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)", value)
    }


MAKE_ASSIGNMENT_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*(::=|:=|\?=|\+=|!=|=)\s*(.*)$"
)
MAKE_CONDITIONAL_RE = re.compile(r"^(ifeq|ifneq|ifdef|ifndef)\b(.*)$")


def _normalize_make_expression(value: str) -> str:
    return " ".join(value.strip().split())


def _parse_make_authorities(
    loader: AuthorityLoader,
    requested_targets: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"declarations": [], "recipes": [], "phony": False}
    )
    variables: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen = set()

    def parse_file(relative: str, inherited_context: tuple[str, ...] = ()) -> None:
        relative = _validate_relative_path(relative, "Make include")
        identity = relative, inherited_context
        if identity in seen:
            return
        seen.add(identity)
        try:
            text = loader.read_blob(relative, "Make authority").decode("utf-8")
        except UnicodeDecodeError as error:
            raise OwnershipError(f"Make authority {relative!r} is not UTF-8") from error
        current_targets: list[str] = []
        conditional_stack = list(inherited_context)
        for recipe, raw_line in _make_logical_lines(text):
            if recipe:
                command = raw_line.strip()
                if command and not command.startswith("#"):
                    for target in current_targets:
                        targets[target]["recipes"].append(
                            {
                                "command": command,
                                "context": tuple(conditional_stack),
                            }
                        )
                continue
            current_targets = []
            line = _strip_make_comment(raw_line).strip()
            if not line:
                continue
            conditional = MAKE_CONDITIONAL_RE.match(line)
            if conditional:
                conditional_stack.append(
                    conditional.group(1)
                    + " "
                    + _normalize_make_expression(conditional.group(2))
                )
                continue
            if line == "else" or line.startswith("else "):
                if not conditional_stack:
                    raise OwnershipError(
                        f"Make authority {relative!r} has unmatched else"
                    )
                conditional_stack[-1] = "else(" + conditional_stack[-1] + ")"
                if line != "else":
                    nested = MAKE_CONDITIONAL_RE.match(line[5:].strip())
                    if nested:
                        conditional_stack[-1] = (
                            "else " + nested.group(1)
                            + " "
                            + _normalize_make_expression(nested.group(2))
                        )
                continue
            if line == "endif":
                if not conditional_stack:
                    raise OwnershipError(
                        f"Make authority {relative!r} has unmatched endif"
                    )
                conditional_stack.pop()
                continue
            include = check_docs.MAKE_INCLUDE_RE.match(line)
            if include:
                for candidate in include.group(2).split():
                    if "$" in candidate or "*" in candidate:
                        continue
                    path = Path(relative).parent / candidate
                    parse_file(path.as_posix(), tuple(conditional_stack))
                continue
            assignment = MAKE_ASSIGNMENT_RE.match(line)
            if assignment:
                variables[assignment.group(1)].append(
                    {
                        "operator": assignment.group(2),
                        "value": _normalize_make_expression(assignment.group(3)),
                        "context": tuple(conditional_stack),
                    }
                )
                continue
            if ":" not in line or ":=" in line.split(":", 1)[0]:
                continue
            lhs, rhs = line.split(":", 1)
            target_names = check_docs._split_make_line_tokens(lhs)
            if lhs.strip() == ".PHONY":
                for target in rhs.split():
                    if "$" not in target:
                        targets[target]["phony"] = True
                continue
            if not target_names:
                continue
            declaration, separator, inline_recipe = rhs.partition(";")
            target_assignment = MAKE_ASSIGNMENT_RE.match(declaration.strip())
            if target_assignment:
                record = {
                    "kind": "target-assignment",
                    "name": target_assignment.group(1),
                    "operator": target_assignment.group(2),
                    "value": _normalize_make_expression(target_assignment.group(3)),
                    "context": tuple(conditional_stack),
                }
                for target in target_names:
                    targets[target]["declarations"].append(record)
                current_targets = target_names
                continue
            normal, marker, order_only = declaration.partition("|")
            record = {
                "kind": "rule",
                "prerequisites": tuple(normal.split()),
                "order_only": tuple(order_only.split()) if marker else (),
                "context": tuple(conditional_stack),
            }
            for target in target_names:
                targets[target]["declarations"].append(record)
                if separator and inline_recipe.strip():
                    targets[target]["recipes"].append(
                        {
                            "command": inline_recipe.strip(),
                            "context": tuple(conditional_stack),
                        }
                    )
            current_targets = target_names
        if len(conditional_stack) != len(inherited_context):
            raise OwnershipError(
                f"Make authority {relative!r} has unclosed conditional"
            )

    parse_file("Makefile")
    direct = {}
    for target, record in targets.items():
        values = [
            *(
                declaration.get("value", "")
                + " "
                + " ".join(declaration.get("prerequisites", ()))
                + " | "
                + " ".join(declaration.get("order_only", ()))
                + " "
                + " ".join(declaration["context"])
                for declaration in record["declarations"]
            ),
            *(
                recipe["command"] + " " + " ".join(recipe["context"])
                for recipe in record["recipes"]
            ),
        ]
        referenced = set()
        pending = list(_make_variable_refs(values))
        while pending:
            name = pending.pop()
            if name in referenced:
                continue
            referenced.add(name)
            pending.extend(
                _make_variable_refs(
                    assignment["value"]
                    for assignment in variables.get(name, ())
                )
                - referenced
            )
        direct[target] = {
            "declarations": record["declarations"],
            "recipes": record["recipes"],
            "phony": record["phony"],
            "variables": {
                name: variables.get(name, ())
                for name in sorted(referenced)
            },
        }

    pattern_targets = [target for target in direct if "%" in target]

    def expand_prerequisite(token: str, record: dict[str, Any]) -> list[str]:
        candidates = [token]
        variable = re.fullmatch(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)", token)
        if variable:
            candidates = [
                value
                for assignment in record["variables"].get(variable.group(1), ())
                for value in assignment["value"].split()
            ]
        result = []
        for candidate in candidates:
            if candidate in direct:
                result.append(candidate)
            if "$" in candidate:
                continue
            for pattern in pattern_targets:
                regex = "^" + "".join(
                    ".+" if part == "%" else re.escape(part)
                    for part in re.split(r"(%)", pattern)
                ) + "$"
                if re.fullmatch(regex, candidate):
                    result.append(pattern)
        return list(dict.fromkeys(result))

    def child_targets(target: str) -> list[str]:
        result = []
        record = direct[target]
        for declaration in record["declarations"]:
            if declaration["kind"] != "rule":
                continue
            for prerequisite in (
                *declaration["prerequisites"],
                *declaration["order_only"],
            ):
                result.extend(expand_prerequisite(prerequisite, record))
        return list(dict.fromkeys(result))

    def authority_record(target: str) -> dict[str, Any]:
        transitive = []
        cycles = []
        visited = {target}

        def visit(current: str, stack: tuple[str, ...]) -> None:
            for child in child_targets(current):
                if child in stack:
                    cycles.append(stack + (child,))
                    continue
                if child in visited:
                    continue
                visited.add(child)
                transitive.append({"target": child, "record": direct[child]})
                visit(child, stack + (child,))

        visit(target, (target,))
        return {
            "target": target,
            "record": direct[target],
            "transitive": transitive,
            "cycles": sorted(set(cycles)),
        }

    selected = set(direct) if requested_targets is None else requested_targets
    return {
        target: authority_record(target)
        for target in sorted(selected)
        if target in direct
    }


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
    loader: AuthorityLoader,
    evidence_nodes: dict[str, dict[str, Any]],
    generated_records: list[dict[str, Any]],
    *,
    strict_workflow: bool,
) -> dict[str, dict[str, str]]:
    requested_make_targets = {
        node["authority"]["target"]
        for node in evidence_nodes.values()
        if node["authority"]["kind"] == "make-target"
    }
    make_targets = _parse_make_authorities(loader, requested_make_targets)
    workflow_jobs, workflow_steps = _workflow_authorities(
        loader,
        strict=strict_workflow,
    )
    tester_cases = _load_test_case_registry(loader)
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
            if target not in make_targets:
                raise OwnershipError(
                    f"evidence node {node_id!r} references stale Make target "
                    f"{target!r}"
                )
            fingerprint = _sha256(
                b"validation-ownership-make-target-v1\0",
                {"target": target, "record": make_targets[target]},
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
            record = _manual_handoff_record(loader, authority["path"])
            fingerprint = _sha256(
                b"validation-ownership-manual-handoff-v1\0", record
            )
            display = authority["path"]
        elif kind == "generated-data-registry":
            fingerprint = _sha256(
                b"validation-ownership-generated-registry-v1\0",
                {
                    "records": generated_records,
                    "source_semantics": _generated_registry_semantics(loader),
                },
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
    events: list[dict[str, Any]],
    evidence_nodes: dict[str, dict[str, Any]],
    edge_ids: set[str],
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

    by_id = {}
    for event in events:
        if event["id"] in by_id:
            raise OwnershipError(f"duplicate lifecycle event {event['id']!r}")
        by_id[event["id"]] = event
        if event["artifact_id"] != artifact["artifact_id"]:
            raise OwnershipError(
                f"lifecycle event {event['id']!r} references another artifact"
            )
    triggers = [
        event
        for event in events
        if event["type"] in pilot_reporter.DELETION_TRIGGER_TYPES
    ]
    proofs = [event for event in events if event["type"] == "deletion_proof"]
    kinds = {trigger["type"] for trigger in triggers}
    if kinds != REQUIRED_PROOF_KINDS or len(triggers) != len(REQUIRED_PROOF_KINDS):
        raise OwnershipError(
            "artifact lifecycle requires exactly one checkpoint, dependency "
            "change, and pre-graduation trigger"
        )
    expected_authorities = {
        "artifact_checkpoint": f"artifact:{artifact['artifact_id']}",
        "dependency_changed": "edge:generated-schema.depends",
        "pre_graduation": f"decision:{artifact['unique_decision']}",
    }
    if "generated-schema.depends" not in edge_ids:
        raise OwnershipError("lifecycle dependency authority edge is missing")
    for trigger in triggers:
        if trigger["authority"] != expected_authorities[trigger["type"]]:
            raise OwnershipError(
                f"lifecycle trigger {trigger['id']!r} has fabricated authority"
            )
    proofs_by_trigger: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for proof in proofs:
        trigger = by_id.get(proof["trigger_event_id"])
        if trigger is None or trigger["type"] not in REQUIRED_PROOF_KINDS:
            raise OwnershipError(
                f"lifecycle proof {proof['id']!r} has no authoritative trigger"
            )
        proofs_by_trigger[trigger["id"]].append(proof)
    for trigger in triggers:
        if len(proofs_by_trigger[trigger["id"]]) != 1:
            raise OwnershipError(
                f"lifecycle trigger {trigger['id']!r} requires exactly one proof"
            )
    if len(proofs) != len(triggers):
        raise OwnershipError("artifact lifecycle has an orphan proof")
    reasons = {proof["reason"] for proof in proofs}
    if reasons != {LIFECYCLE_FAILURE_REASON}:
        raise OwnershipError(
            "artifact lifecycle proofs must use the executable failure reason"
        )
    required_semantic = "pass" if current == "Delete" else "fail"
    for proof in proofs:
        try:
            occurred = pilot_reporter.parse_time(
                proof["occurred_at"], f"artifact proof {proof['id']}.occurred_at"
            )
            trigger_at = pilot_reporter.parse_time(
                by_id[proof["trigger_event_id"]]["occurred_at"],
                f"artifact trigger {proof['trigger_event_id']}.occurred_at",
            )
        except pilot_reporter.PilotDataError as error:
            raise OwnershipError(str(error)) from error
        if occurred <= trigger_at:
            raise OwnershipError(
                f"artifact proof {proof['id']!r} must strictly follow its trigger"
            )
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
    loader: AuthorityLoader,
    entries: dict[str, GitTreeEntry],
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

    generated_records, generated_paths = _generated_registry_records(loader)
    authorities = _validate_authorities(
        loader,
        evidence_nodes,
        generated_records,
        strict_workflow=True,
    )
    _validate_lifecycle(
        graph["artifact"],
        graph["lifecycle_events"],
        evidence_nodes,
        edge_ids,
    )

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
    for path, entry in sorted(entries.items()):
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
        if entry.mode == SYMLINK_MODE:
            raise OwnershipError(
                f"tracked path {path!r} is a symlink; mode 120000 is not admitted"
            )
        if entry.mode == GITLINK_MODE:
            if entry.object_type != "commit":
                raise OwnershipError(
                    f"gitlink {path!r} must identify a commit object"
                )
            if (
                matches
                or len(excluded) != 1
                or excluded[0]["applies_to"] != "gitlink"
            ):
                raise OwnershipError(
                    f"gitlink {path!r} requires one explicit fail-closed exclusion"
                )
            coverage[path] = {
                "kind": "excluded",
                "mode": entry.mode,
                "exclusion": excluded[0]["id"],
                "reason": excluded[0]["reason"],
            }
            continue
        if not REGULAR_BLOB_MODE_RE.fullmatch(entry.mode) or entry.object_type != "blob":
            raise OwnershipError(
                f"tracked path {path!r} has unsupported mode {entry.mode} "
                f"type {entry.object_type}"
            )
        if excluded:
            if (
                matches
                or len(excluded) != 1
                or excluded[0]["applies_to"] != "external-enforcement"
            ):
                raise OwnershipError(
                    f"regular blob {path!r} has an invalid fail-closed exclusion"
                )
            coverage[path] = {
                "kind": "excluded",
                "mode": entry.mode,
                "exclusion": excluded[0]["id"],
                "reason": excluded[0]["reason"],
            }
            continue
        if len(matches) == 0:
            raise OwnershipError(f"tracked path {path!r} has no ownership contract")
        if len(matches) > 1:
            identities = [rule["id"] for rule in matches]
            raise OwnershipError(
                f"tracked path {path!r} has ambiguous ownership {identities}"
            )
        coverage[path] = {
            "kind": "owned",
            "mode": entry.mode,
            "rule": matches[0]["id"],
            "surface": matches[0]["surface"],
        }

    return {
        "nodes": nodes,
        "surfaces": surfaces,
        "evidence": evidence_nodes,
        "outgoing": outgoing,
        "authorities": authorities,
        "generated_records": generated_records,
        "generated_paths": generated_paths,
        "coverage": coverage,
        "entries": entries,
    }


def validate_graph(
    graph: dict[str, Any],
    schema: dict[str, Any],
    loader: AuthorityLoader,
    entries: dict[str, GitTreeEntry],
) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise OwnershipError("graph schema must be an object")
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise OwnershipError("graph schema must use JSON Schema draft 2020-12")
    validate_json_schema(graph, schema, schema)
    return _validate_semantics(graph, loader, entries)


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
    graph: dict[str, Any],
    prior_graph: dict[str, Any],
    model: dict[str, Any],
    current_loader: AuthorityLoader,
    base_loader: AuthorityLoader,
) -> set[str]:
    if (
        SCHEMA_PATH.as_posix() not in base_loader.entries
        or PROBE_ORACLE_PATH.as_posix() not in base_loader.entries
    ):
        return {edge["id"] for edge in graph["edges"]}
    current_schema = current_loader.read_json(
        SCHEMA_PATH,
        "ownership graph schema",
    )
    base_schema = base_loader.read_json(
        SCHEMA_PATH,
        "base ownership graph schema",
    )
    current_oracle = current_loader.read_json(
        PROBE_ORACLE_PATH,
        "ownership probe oracle",
    )
    base_oracle = base_loader.read_json(
        PROBE_ORACLE_PATH,
        "base ownership probe oracle",
    )
    if current_schema != base_schema or current_oracle != base_oracle:
        return {edge["id"] for edge in graph["edges"]}
    current_nodes = {
        node["id"]: node
        for node in graph["nodes"]
        if node["kind"] == "evidence"
    }
    prior_nodes = {
        node["id"]: node
        for node in prior_graph["nodes"]
        if node["kind"] == "evidence"
    }
    prior_authorities = _validate_authorities(
        base_loader,
        prior_nodes,
        model["generated_records"],
        strict_workflow=False,
    )
    changed_nodes = {
        node_id
        for node_id in set(current_nodes) & set(prior_nodes)
        if _authority_identity(current_nodes[node_id]["authority"])
        == _authority_identity(prior_nodes[node_id]["authority"])
        and model["authorities"][node_id]["fingerprint"]
        != prior_authorities[node_id]["fingerprint"]
    }
    return {
        edge["id"]
        for candidate in (graph, prior_graph)
        for edge in candidate["edges"]
        if edge["target"] in changed_nodes
    }


def _prior_graph(loader: AuthorityLoader | None) -> dict[str, Any] | None:
    if loader is None or GRAPH_PATH.as_posix() not in loader.entries:
        return None
    return loader.read_json(
        GRAPH_PATH,
        "prior ownership graph",
    )


def _resolve_path(
    path: str,
    graph: dict[str, Any],
    model: dict[str, Any],
    base_entries: dict[str, GitTreeEntry] | None = None,
) -> dict[str, Any]:
    path = _validate_relative_path(path, "changed path")
    current_entry = model["entries"].get(path)
    base_entry = None if base_entries is None else base_entries.get(path)
    if current_entry is None and base_entry is None:
        raise OwnershipError(
            f"changed path {path!r} is absent from the current and selected base trees"
        )
    if (
        current_entry is not None
        and base_entry is not None
        and (
            current_entry.mode != base_entry.mode
            or current_entry.object_type != base_entry.object_type
        )
    ):
        raise OwnershipError(
            f"changed path {path!r} changes Git mode/type "
            f"{base_entry.mode}/{base_entry.object_type} -> "
            f"{current_entry.mode}/{current_entry.object_type}"
        )
    entry = current_entry or base_entry
    assert entry is not None
    if entry.mode == SYMLINK_MODE:
        raise OwnershipError(
            f"changed path {path!r} is a rejected 120000 symlink"
        )
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
    if entry.mode == GITLINK_MODE:
        if (
            matches
            or len(exclusions) != 1
            or exclusions[0]["applies_to"] != "gitlink"
        ):
            raise OwnershipError(
                f"changed gitlink {path!r} lacks one explicit fail-closed exclusion"
            )
        exclusion = exclusions[0]
        raise OwnershipError(
            f"changed path {path!r} is fail-closed gitlink exclusion "
            f"{exclusion['id']!r}: {exclusion['reason']}"
        )
    if not REGULAR_BLOB_MODE_RE.fullmatch(entry.mode) or entry.object_type != "blob":
        raise OwnershipError(
            f"changed path {path!r} has unsupported mode {entry.mode} "
            f"type {entry.object_type}"
        )
    if exclusions:
        if (
            matches
            or len(exclusions) != 1
            or exclusions[0]["applies_to"] != "external-enforcement"
        ):
            raise OwnershipError(
                f"changed regular blob {path!r} has an invalid exclusion"
            )
        exclusion = exclusions[0]
        raise OwnershipError(
            f"changed path {path!r} is fail-closed external enforcement "
            f"{exclusion['id']!r}: {exclusion['reason']}"
        )
    if not matches:
        raise OwnershipError(f"changed path {path!r} has no ownership contract")
    if len(matches) > 1:
        raise OwnershipError(f"changed path {path!r} has ambiguous ownership")
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
        "git_mode": entry.mode,
        "owners": owners,
    }


def canonical_probe_oracle_payload(oracle: dict[str, Any]) -> dict[str, Any]:
    probes = []
    for raw_probe in oracle["probes"]:
        probe = copy.deepcopy(raw_probe)
        if "expected_owners" in probe:
            probe["expected_owners"] = sorted(
                probe["expected_owners"],
                key=lambda item: (item["edge_type"], item["evidence_id"]),
            )
        probes.append(probe)
    probes.sort(key=lambda item: item["path"])
    return {
        "schema_version": oracle["schema_version"],
        "source_case": oracle["source_case"],
        "probes": probes,
    }


def validate_probe_oracle(
    oracle: Any,
    graph: dict[str, Any],
    entries: dict[str, GitTreeEntry],
) -> dict[str, Any]:
    if not isinstance(oracle, dict):
        raise OwnershipError("probe oracle must be an object")
    expected_keys = {"schema_version", "source_case", "probes", "seal"}
    if set(oracle) != expected_keys:
        raise OwnershipError(
            f"probe oracle keys must be exactly {sorted(expected_keys)}"
        )
    if oracle["schema_version"] != 1:
        raise OwnershipError("probe oracle schema_version must be 1")
    if oracle["source_case"] != "TC-WORKFLOW-GATE-OWNERSHIP-001":
        raise OwnershipError("probe oracle has unknown source case")
    if not isinstance(oracle["probes"], list) or not oracle["probes"]:
        raise OwnershipError("probe oracle must contain probes")
    surfaces = {
        node["id"]
        for node in graph["nodes"]
        if node["kind"] == "surface"
    }
    evidence_ids = {
        node["id"]
        for node in graph["nodes"]
        if node["kind"] == "evidence"
    }
    exclusion_ids = {item["id"] for item in graph["exclusions"]}
    paths = set()
    for index, probe in enumerate(oracle["probes"]):
        label = f"probe oracle entry {index}"
        if not isinstance(probe, dict):
            raise OwnershipError(f"{label} has unknown or missing fields")
        keys = frozenset(probe)
        owned_keys = {
            "path",
            "expected_surface",
            "expected_owners",
        }
        exclusion_keys = {"path", "expected_exclusion"}
        if keys not in {frozenset(owned_keys), frozenset(exclusion_keys)}:
            raise OwnershipError(f"{label} has unknown or missing fields")
        path = _validate_relative_path(probe["path"], f"{label}.path")
        if path in paths:
            raise OwnershipError(f"probe oracle duplicates path {path!r}")
        paths.add(path)
        entry = entries.get(path)
        if (
            entry is None
            or not REGULAR_BLOB_MODE_RE.fullmatch(entry.mode)
            or entry.object_type != "blob"
        ):
            raise OwnershipError(
                f"probe oracle path {path!r} is not a current regular blob"
            )
        if keys == frozenset(exclusion_keys):
            if probe["expected_exclusion"] not in exclusion_ids:
                raise OwnershipError(
                    f"{label} references unknown exclusion "
                    f"{probe['expected_exclusion']!r}"
                )
            continue
        if probe["expected_surface"] not in surfaces:
            raise OwnershipError(
                f"{label} references unknown surface {probe['expected_surface']!r}"
            )
        expected_owners = probe["expected_owners"]
        if (
            not isinstance(expected_owners, list)
            or not expected_owners
        ):
            raise OwnershipError(f"{label} owners must be a nonempty list")
        pairs = []
        for owner_index, owner in enumerate(expected_owners):
            owner_label = f"{label}.expected_owners[{owner_index}]"
            if not isinstance(owner, dict) or set(owner) != {
                "edge_type",
                "evidence_id",
            }:
                raise OwnershipError(
                    f"{owner_label} must contain edge_type and evidence_id"
                )
            if owner["edge_type"] not in EDGE_TYPES:
                raise OwnershipError(
                    f"{owner_label} has unknown edge family {owner['edge_type']!r}"
                )
            if owner["evidence_id"] not in evidence_ids:
                raise OwnershipError(
                    f"{owner_label} has unknown evidence ID {owner['evidence_id']!r}"
                )
            pairs.append((owner["edge_type"], owner["evidence_id"]))
        if len(pairs) != len(set(pairs)):
            raise OwnershipError(f"{label} duplicates an exact owner pair")
    seal = oracle["seal"]
    if not isinstance(seal, str) or not re.fullmatch(r"[0-9a-f]{64}", seal):
        raise OwnershipError("probe oracle seal must be a lowercase SHA-256")
    payload = canonical_probe_oracle_payload(oracle)
    expected = _sha256(PROBE_SEAL_DOMAIN, payload)
    if seal != expected:
        raise OwnershipError("probe oracle seal does not match its admitted cases")
    return oracle


def _measure(
    oracle: dict[str, Any],
    graph: dict[str, Any],
    model: dict[str, Any],
) -> dict[str, Any]:
    false_positive = 0
    false_negative = 0
    probes = []
    for probe in oracle["probes"]:
        if "expected_exclusion" in probe:
            coverage = model["coverage"].get(probe["path"])
            actual_exclusion = (
                coverage.get("exclusion")
                if coverage is not None and coverage["kind"] == "excluded"
                else None
            )
            if actual_exclusion != probe["expected_exclusion"]:
                false_positive += int(actual_exclusion is not None)
                false_negative += 1
            probes.append(
                {
                    "path": probe["path"],
                    "exclusion": actual_exclusion,
                }
            )
            continue
        resolution = _resolve_path(probe["path"], graph, model)
        actual = {
            (owner["edge_type"], owner["evidence_id"])
            for owner in resolution["owners"]
        }
        expected = {
            (owner["edge_type"], owner["evidence_id"])
            for owner in probe["expected_owners"]
        }
        if resolution["surface"] != probe["expected_surface"]:
            false_positive += 1
            false_negative += 1
        false_positive += len(actual - expected)
        false_negative += len(expected - actual)
        probes.append(
            {
                "path": probe["path"],
                "surface": resolution["surface"],
                "owners": [
                    {"edge_type": edge_type, "evidence_id": evidence_id}
                    for edge_type, evidence_id in sorted(actual)
                ],
            }
        )
    if false_positive or false_negative:
        raise OwnershipError(
            "probe oracle selection mismatch "
            f"(false_positive={false_positive}, false_negative={false_negative})"
        )
    artifact = graph["artifact"]
    return {
        "source_case": oracle["source_case"],
        "oracle_seal": oracle["seal"],
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
    oracle: dict[str, Any],
    loader: AuthorityLoader,
    entries: dict[str, GitTreeEntry],
    changed_paths: Iterable[str] = (),
    prior_graph: dict[str, Any] | None = None,
    review_comparison_requested: bool = False,
    authority_changed_edge_ids: Iterable[str] = (),
    base_entries: dict[str, GitTreeEntry] | None = None,
) -> dict[str, Any]:
    model = validate_graph(graph, schema, loader, entries)
    oracle = validate_probe_oracle(oracle, graph, entries)
    resolutions = [
        _resolve_path(path, graph, model, base_entries)
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
            "tracked_paths": len(entries),
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
        "measurement": _measure(oracle, graph, model),
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


def run_lifecycle_check(
    artifact_root: Path,
    authority_root: Path,
    check_id: str,
) -> int:
    if check_id not in LIFECYCLE_CHECKS:
        raise OwnershipError(f"lifecycle check {check_id!r} is not allowlisted")
    authority_root = validate_repository_root(authority_root)
    scratch = prepare_validation_scratch(authority_root)
    try:
        artifact_root = artifact_root.resolve(strict=True)
        if scratch.path not in artifact_root.parents:
            raise OwnershipError("lifecycle artifact root must be in the bounded sandbox")
        graph_path = artifact_root / GRAPH_PATH
        if not graph_path.is_file() or graph_path.is_symlink():
            raise OwnershipError(
                "validation ownership graph artifact is missing: "
                + LIFECYCLE_FAILURE_REASON
            )
        graph = load_json(graph_path)
        entries = git_tree_entries(authority_root)
        loader = AuthorityLoader(authority_root, entries)
        schema = loader.read_json(SCHEMA_PATH, "ownership graph schema")
        oracle = loader.read_json(PROBE_ORACLE_PATH, "ownership probe oracle")
        report = build_report(
            graph,
            schema,
            oracle,
            loader,
            entries,
        )
        if check_id == "TC-WORKFLOW-GATE-OWNERSHIP-001":
            measurement = report["measurement"]
            if (
                measurement["false_positive_selections"] != 0
                or measurement["false_negative_selections"] != 0
            ):
                raise OwnershipError("ownership consistency check has selection loss")
        return 0
    finally:
        cleanup_validation_scratch(scratch)


def _run_lifecycle_subprocess(
    authority_root: Path,
    artifact_root: Path,
    check_id: str,
) -> subprocess.CompletedProcess[bytes]:
    command = (
        "/usr/bin/python3",
        "-I",
        str(authority_root / "scripts/validation_ownership/isolated_launcher.py"),
        "lifecycle-check",
        "--artifact-root",
        str(artifact_root),
        "--authority-root",
        str(authority_root),
        "--check",
        check_id,
    )
    try:
        return subprocess.run(
            command,
            cwd=authority_root,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0"},
            check=False,
            capture_output=True,
            timeout=LIFECYCLE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OwnershipError(f"cannot execute lifecycle proof: {error}") from error


def validate_executable_lifecycle(
    root: Path,
    graph: dict[str, Any],
) -> list[dict[str, str]]:
    events = graph["lifecycle_events"]
    triggers = {
        event["id"]: event
        for event in events
        if event["type"] in REQUIRED_PROOF_KINDS
    }
    proofs = sorted(
        (
            event
            for event in events
            if event["type"] == "deletion_proof"
        ),
        key=lambda item: item["occurred_at"],
    )
    scratch = prepare_validation_scratch(root)
    sandbox_parent = scratch.path
    source_bytes = (root / GRAPH_PATH).read_bytes()
    results = []
    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{root.name}-validation-ownership-proof-",
            dir=sandbox_parent,
        ) as temporary:
            sandbox = Path(temporary)
            artifact = sandbox / GRAPH_PATH
            artifact.parent.mkdir(parents=True)
            shutil.copy2(root / GRAPH_PATH, artifact)
            for check_id in sorted(LIFECYCLE_CHECKS):
                initial = _run_lifecycle_subprocess(root, sandbox, check_id)
                if initial.returncode != 0:
                    detail = initial.stderr.decode("utf-8", errors="replace").strip()
                    raise OwnershipError(
                        "stale executable lifecycle baseline does not pass"
                        + (f": {detail}" if detail else "")
                    )
            backup = sandbox / "validation-ownership-graph.backup"
            for proof in proofs:
                trigger = triggers[proof["trigger_event_id"]]
                artifact.replace(backup)
                removed = [
                    _run_lifecycle_subprocess(root, sandbox, check_id)
                    for check_id in sorted(LIFECYCLE_CHECKS)
                ]
                backup.replace(artifact)
                if any(item.returncode == 0 for item in removed):
                    raise OwnershipError(
                        f"lifecycle proof {proof['id']!r} removal did not fail"
                    )
                removal_details = [
                    item.stderr.decode("utf-8", errors="replace")
                    for item in removed
                ]
                if any(
                    LIFECYCLE_FAILURE_REASON not in detail
                    for detail in removal_details
                ):
                    raise OwnershipError(
                        f"lifecycle proof {proof['id']!r} lacks the named failure"
                    )
                restored = [
                    _run_lifecycle_subprocess(root, sandbox, check_id)
                    for check_id in sorted(LIFECYCLE_CHECKS)
                ]
                if any(item.returncode != 0 for item in restored):
                    raise OwnershipError(
                        f"lifecycle proof {proof['id']!r} restoration did not pass"
                    )
                results.append(
                    {
                        "trigger_event_id": trigger["id"],
                        "trigger_type": trigger["type"],
                        "proof_id": proof["id"],
                        "removal": "fail",
                        "reason": LIFECYCLE_FAILURE_REASON,
                        "restoration": "pass",
                    }
                )
    except OSError as error:
        raise OwnershipError(f"cannot prepare lifecycle sandbox: {error}") from error
    finally:
        cleanup_validation_scratch(scratch)
    if (root / GRAPH_PATH).read_bytes() != source_bytes:
        raise OwnershipError("lifecycle proof changed the source graph")
    return results


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
        entries = git_tree_entries(root)
        loader = AuthorityLoader(root, entries)
        graph = loader.read_json(GRAPH_PATH, "validation ownership graph")
        schema = loader.read_json(SCHEMA_PATH, "ownership graph schema")
        oracle = loader.read_json(PROBE_ORACLE_PATH, "ownership probe oracle")
        base_entries = (
            git_tree_entries(root, arguments.base_revision)
            if arguments.base_revision is not None
            else None
        )
        base_loader = (
            AuthorityLoader(root, base_entries, arguments.base_revision)
            if base_entries is not None
            else None
        )
        prior = _prior_graph(base_loader)
        if prior is not None:
            validate_json_schema(prior, schema, schema, "prior graph")
        model = validate_graph(graph, schema, loader, entries)
        authority_changed = (
            _authority_changed_edges(
                graph,
                prior,
                model,
                loader,
                base_loader,
            )
            if base_loader is not None and prior is not None
            else set()
        )
        report = build_report(
            graph,
            schema,
            oracle,
            loader,
            entries,
            arguments.changed,
            prior,
            arguments.base_revision is not None,
            authority_changed,
            base_entries,
        )
        report["artifact"]["executable_lifecycle"] = (
            validate_executable_lifecycle(root, graph)
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
