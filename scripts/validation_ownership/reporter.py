#!/usr/bin/env python3
"""Validate and explain the repository's fail-closed ownership graph."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
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
MAKE_DYNAMIC_PATH = Path(
    ".github/validation-ownership-make-dynamics.json"
)
_MAKE_AUTHORITY_CACHE: dict[
    tuple[Any, ...],
    dict[str, dict[str, Any]],
] = {}
_MAKE_AUTHORITY_CACHE_LIMIT = 16
TEST_CASE_REGISTRY_PATH = Path("docs/test-cases/registry.json")
BUILD_WORKFLOW_PATH = Path(".github/workflows/build.yml")
EXPECTED_SCHEMA_VERSION = 1
EDGE_SEAL_DOMAIN = b"validation-ownership-resolved-edges-v1\0"
GRAPH_SEAL_DOMAIN = b"validation-ownership-graph-v1\0"
SCHEMA_SEAL_DOMAIN = b"validation-ownership-schema-v1\0"
PROBE_SEAL_DOMAIN = b"validation-ownership-probe-oracle-v1\0"
MAKE_DYNAMIC_SEAL_DOMAIN = b"validation-ownership-make-dynamics-v1\0"
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
        scratch_root: Path | None = None,
    ):
        self.root = root
        self.entries = entries
        self.revision = revision
        self.scratch_root = scratch_root

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
    try:
        from scripts.validation_ownership import make_probe

        output, probe_authority = make_probe.probe_generated_registry(
            loader,
            scratch_root=(
                loader.scratch_root
                if loader.scratch_root is not None
                else (
                    loader.root
                    / "build"
                    / "test-artifacts"
                    / "validation-ownership"
                )
            ),
        )
    except make_probe.MakeProbeError as error:
        raise OwnershipError(str(error)) from error
    try:
        decoded = output.decode("utf-8")
    except UnicodeDecodeError as error:
        raise OwnershipError(
            "candidate generated-data registry output is not UTF-8"
        ) from error
    records = parse_json(decoded, "candidate generated-data registry output")
    if not isinstance(records, list) or not records:
        raise OwnershipError(
            "candidate generated-data registry output must be a nonempty list"
        )
    paths: set[str] = set()
    names = set()
    ordered_names = []
    fields = {
        "name",
        "version",
        "default_source",
        "default_hand_source",
        "default_output_name",
        "default_inventory_path",
        "dependencies",
        "dependency_tables",
    }
    for index, record in enumerate(records):
        label = f"candidate generated-data registry record {index}"
        if not isinstance(record, dict) or set(record) != fields:
            raise OwnershipError(f"{label} has invalid fields")
        name = record["name"]
        if (
            not isinstance(name, str)
            or not name
            or name in names
            or not isinstance(record["version"], int)
            or isinstance(record["version"], bool)
            or record["version"] < 1
            or (
                record["default_output_name"] is not None
                and (
                    not isinstance(record["default_output_name"], str)
                    or not record["default_output_name"]
                )
            )
        ):
            raise OwnershipError(f"{label} has invalid identity fields")
        names.add(name)
        ordered_names.append(name)
        for list_field in ("dependencies", "dependency_tables"):
            values = record[list_field]
            if (
                not isinstance(values, list)
                or values != list(dict.fromkeys(values))
                or not all(isinstance(value, str) and value for value in values)
                or (
                    list_field == "dependencies"
                    and values != sorted(values)
                )
            ):
                raise OwnershipError(f"{label}.{list_field} is invalid")
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
    if ordered_names != sorted(ordered_names):
        raise OwnershipError(
            "candidate generated-data registry names are not sorted"
        )
    for record in records:
        record["probe_authority"] = probe_authority
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



def canonical_make_dynamic_payload(data: dict[str, Any]) -> dict[str, Any]:
    contracts = []
    for raw in data["contracts"]:
        contract = copy.deepcopy(raw)
        for field in (
            "input_files",
            "input_variables",
            "automatic_inputs",
            "owning_evidence_ids",
        ):
            contract[field] = sorted(contract[field])
        contracts.append(contract)
    contracts.sort(key=lambda item: item["id"])
    if data["schema_version"] == 1:
        return {"schema_version": 1, "contracts": contracts}
    ambient_inputs = copy.deepcopy(data["ambient_inputs"])
    ambient_inputs["allowed_names"] = sorted(ambient_inputs["allowed_names"])
    ambient_inputs["allowed_sources"] = sorted(
        ambient_inputs["allowed_sources"]
    )
    if data["schema_version"] >= 3:
        for field in (
            "undefined_names",
            "trusted_builtins",
            "scoped_variables",
            "escaped_literals",
        ):
            ambient_inputs[field] = sorted(
                ambient_inputs[field],
                key=(
                    (lambda item: item["name"])
                    if field != "undefined_names"
                    else None
                ),
            )
    result = {
        "schema_version": data["schema_version"],
        "contracts": contracts,
        "ambient_inputs": ambient_inputs,
    }
    if data["schema_version"] >= 3:
        execution_controls = copy.deepcopy(data["execution_controls"])
        for field in (
            "scrubbed_variables",
            "allowed_flag_patterns",
            "forbidden_modes",
        ):
            execution_controls[field] = sorted(execution_controls[field])
        result["execution_controls"] = execution_controls
    if data["schema_version"] >= 4:
        prerequisite_domains = copy.deepcopy(data["prerequisite_domains"])
        prerequisite_domains["tracked_fallback_names"] = sorted(
            prerequisite_domains["tracked_fallback_names"]
        )
        if data["schema_version"] >= 5:
            prerequisite_domains["symbolic_recipe_names"] = sorted(
                prerequisite_domains["symbolic_recipe_names"]
            )
        for domain in prerequisite_domains["explicit"]:
            domain["values"] = sorted(domain["values"])
        prerequisite_domains["explicit"].sort(key=lambda item: item["name"])
        for generated in prerequisite_domains["generated_paths"]:
            generated["authority_files"] = sorted(generated["authority_files"])
        prerequisite_domains["generated_paths"].sort(
            key=lambda item: item["path"]
        )
        result["prerequisite_domains"] = prerequisite_domains
    return result


def _source_semantics(path: str, content: bytes) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise OwnershipError(f"dynamic dependency input {path!r} is not UTF-8") from error
    if path.endswith(".py"):
        try:
            return ast.dump(
                ast.parse(text, filename=path),
                include_attributes=False,
            )
        except SyntaxError as error:
            raise OwnershipError(
                f"dynamic dependency tool {path!r} is invalid Python: {error}"
            ) from error
    return "\n".join(
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "//"))
    )


def load_make_dynamic_contracts(
    loader: AuthorityLoader,
    *,
    required: bool,
) -> dict[str, dict[str, Any]]:
    path = MAKE_DYNAMIC_PATH.as_posix()
    if path not in loader.entries:
        if required:
            raise OwnershipError("Make dynamic dependency registry is not tracked")
        return {}
    data = loader.read_json(MAKE_DYNAMIC_PATH, "Make dynamic dependency registry")
    if not isinstance(data, dict):
        raise OwnershipError("Make dynamic dependency registry has invalid fields")
    schema_version = data.get("schema_version")
    expected_fields = {
        "schema_version",
        "contracts",
        "seal",
    }
    if schema_version in {2, 3, 4, 5}:
        expected_fields.add("ambient_inputs")
    if schema_version == 3:
        expected_fields.add("execution_controls")
    if schema_version in {4, 5}:
        expected_fields.update({"execution_controls", "prerequisite_domains"})
    if set(data) != expected_fields:
        raise OwnershipError("Make dynamic dependency registry has invalid fields")
    if schema_version not in {1, 2, 3, 4, 5} or not isinstance(
        data["contracts"],
        list,
    ):
        raise OwnershipError("Make dynamic dependency registry schema is invalid")
    if schema_version in {2, 3, 4, 5}:
        ambient = data["ambient_inputs"]
        ambient_fields = {
            "allowed_names",
            "allowed_sources",
            "value_policy",
            "provenance",
            "evidence_binding",
        }
        if schema_version >= 3:
            ambient_fields.update(
                {
                    "undefined_names",
                    "trusted_builtins",
                    "scoped_variables",
                    "escaped_literals",
                }
            )
        if not isinstance(ambient, dict) or set(ambient) != ambient_fields:
            raise OwnershipError("Make ambient input registry has invalid fields")
        allowed_names = ambient["allowed_names"]
        if (
            not isinstance(allowed_names, list)
            or allowed_names != sorted(set(allowed_names))
            or not all(
                isinstance(name, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                for name in allowed_names
            )
        ):
            raise OwnershipError(
                "Make ambient input registry names must be sorted unique variables"
            )
        if ambient["allowed_sources"] != [
            "command-line",
            "process-environment",
        ]:
            raise OwnershipError(
                "Make ambient input registry sources must be command-line and "
                "process-environment"
            )
        if ambient["value_policy"] != "symbolic-no-host-value":
            raise OwnershipError("Make ambient input value policy is invalid")
        if ambient["provenance"] != "gnu-make-import-before-default":
            raise OwnershipError("Make ambient input provenance is invalid")
        if ambient["evidence_binding"] != "consuming-make-target":
            raise OwnershipError("Make ambient input evidence binding is invalid")
        if schema_version >= 3:
            undefined_names = ambient["undefined_names"]
            if (
                not isinstance(undefined_names, list)
                or undefined_names != sorted(set(undefined_names))
                or not all(
                    isinstance(name, str)
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                    for name in undefined_names
                )
                or set(undefined_names) & set(allowed_names)
            ):
                raise OwnershipError(
                    "Make undefined ambient names must be sorted, unique, "
                    "and separate from default inputs"
                )
            typed_fields = {
                "trusted_builtins": {
                    "CURDIR": ("repository-root", "<trusted-builtin:CURDIR>"),
                    "MAKE": ("recursive-make", "<trusted-builtin:MAKE>"),
                    "MAKEFLAGS": (
                        "guarded-execution-flags",
                        "<trusted-builtin:MAKEFLAGS>",
                    ),
                    "MAKECMDGOALS": (
                        "requested-goals",
                        "<trusted-builtin:MAKECMDGOALS>",
                    ),
                    "MAKE_RESTARTS": (
                        "restart-count",
                        "<trusted-builtin:MAKE_RESTARTS>",
                    ),
                },
                "scoped_variables": {
                    "*": ("automatic-variable", "<automatic-variable:*>"),
                    "1": ("call-argument", "<call-argument:1>"),
                    "2": ("call-argument", "<call-argument:2>"),
                    "3": ("call-argument", "<call-argument:3>"),
                    "4": ("call-argument", "<call-argument:4>"),
                    "GENERATED_DATA_LINKED_SYMBOL_PREFIX_characters": (
                        "computed-empty",
                        "<computed-empty:GENERATED_DATA_LINKED_SYMBOL_PREFIX_characters>",
                    ),
                    "GENERATED_DATA_LINKED_SYMBOL_PREFIX_classes": (
                        "computed-empty",
                        "<computed-empty:GENERATED_DATA_LINKED_SYMBOL_PREFIX_classes>",
                    ),
                    "GENERATED_DATA_LINKED_SYMBOL_PREFIX_items": (
                        "computed-empty",
                        "<computed-empty:GENERATED_DATA_LINKED_SYMBOL_PREFIX_items>",
                    ),
                    "<": ("automatic-variable", "<automatic-variable:<>"),
                    "@": ("automatic-variable", "<automatic-variable:@>"),
                    "@D": ("automatic-variable", "<automatic-variable:@D>"),
                    "^": ("automatic-variable", "<automatic-variable:^>"),
                    "t": ("foreach-iteration", "<scoped-variable:t>"),
                },
                "escaped_literals": {
                    "sort": (
                        "escaped-shell-literal",
                        "<escaped-shell-literal:sort>",
                    ),
                },
            }
            for field, expected in typed_fields.items():
                values = ambient[field]
                if (
                    not isinstance(values, list)
                    or any(
                        not isinstance(item, dict)
                        or set(item) != {"name", "authority", "value"}
                        for item in values
                    )
                    or {
                        item["name"]: (
                            item["authority"],
                            item["value"],
                        )
                        for item in values
                    }
                    != expected
                ):
                    raise OwnershipError(
                        f"Make {field.replace('_', ' ')} contract is invalid"
                    )
            controls = data["execution_controls"]
            if not isinstance(controls, dict) or set(controls) != {
                "scrubbed_variables",
                "allowed_flag_patterns",
                "forbidden_modes",
                "override_policy",
            }:
                raise OwnershipError("Make execution control registry is invalid")
            if controls["scrubbed_variables"] != [
                "GNUMAKEFLAGS",
                "MAKEFLAGS",
                "MAKEOVERRIDES",
                "MFLAGS",
            ]:
                raise OwnershipError(
                    "Make execution control scrub list is invalid"
                )
            if controls["allowed_flag_patterns"] != [
                "--jobserver-auth=*",
                "--jobserver-fds=*",
                "--no-print-directory",
                "-j*",
                "j*",
            ]:
                raise OwnershipError(
                    "Make execution control allowed flags are invalid"
                )
            if controls["forbidden_modes"] != [
                "dry-run",
                "ignore-errors",
                "question",
                "silent",
                "touch",
                "unmodeled",
            ]:
                raise OwnershipError(
                    "Make execution control forbidden modes are invalid"
                )
            if controls["override_policy"] != "reject-nonempty":
                raise OwnershipError(
                    "Make execution control override policy is invalid"
                )
        if schema_version >= 4:
            domains = data["prerequisite_domains"]
            domain_fields = {
                "tracked_fallback_names",
                "explicit",
                "generated_paths",
                "max_variants",
                "max_words",
                "value_policy",
                "target_policy",
            }
            if schema_version >= 5:
                domain_fields.add("symbolic_recipe_names")
            if not isinstance(domains, dict) or set(domains) != domain_fields:
                raise OwnershipError("Make prerequisite domains are invalid")
            tracked = domains["tracked_fallback_names"]
            symbolic_recipe_names = domains.get("symbolic_recipe_names", [])
            if (
                not isinstance(tracked, list)
                or tracked != sorted(set(tracked))
                or not all(
                    isinstance(name, str)
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                    for name in tracked
                )
            ):
                raise OwnershipError(
                    "Make tracked-fallback prerequisite names are invalid"
                )
            if (
                not isinstance(symbolic_recipe_names, list)
                or symbolic_recipe_names
                != sorted(set(symbolic_recipe_names))
                or not all(
                    isinstance(name, str)
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                    for name in symbolic_recipe_names
                )
                or set(symbolic_recipe_names) & set(tracked)
            ):
                raise OwnershipError(
                    "Make symbolic recipe names are invalid"
                )
            explicit = domains["explicit"]
            if not isinstance(explicit, list):
                raise OwnershipError(
                    "Make explicit prerequisite domains are invalid"
                )
            explicit_names = set()
            for domain in explicit:
                if (
                    not isinstance(domain, dict)
                    or set(domain) != {"name", "values"}
                    or not isinstance(domain["name"], str)
                    or not re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_]*",
                        domain["name"],
                    )
                    or domain["name"] in explicit_names
                    or domain["name"] in tracked
                    or domain["name"] in symbolic_recipe_names
                    or not isinstance(domain["values"], list)
                    or domain["values"] != sorted(set(domain["values"]))
                    or not all(isinstance(value, str) for value in domain["values"])
                    or len(domain["values"]) < 2
                    or len(domain["values"]) > 4096
                    or sum(
                        len(value.split())
                        for value in domain["values"]
                    )
                    > 20000
                ):
                    raise OwnershipError(
                        "Make explicit prerequisite domain is malformed"
                    )
                explicit_names.add(domain["name"])
            generated_paths = domains["generated_paths"]
            if (
                    not isinstance(generated_paths, list)
                    or any(
                        not isinstance(item, dict)
                        or set(item) != {"path", "authority_files"}
                        or not isinstance(item["path"], str)
                        or not isinstance(item["authority_files"], list)
                        or not item["authority_files"]
                        or item["authority_files"]
                        != sorted(set(item["authority_files"]))
                        for item in generated_paths
                    )
                    or len({item["path"] for item in generated_paths})
                    != len(generated_paths)
            ):
                    raise OwnershipError(
                        "Make generated prerequisite paths are malformed"
                    )
            if (
                not isinstance(domains["max_variants"], int)
                or isinstance(domains["max_variants"], bool)
                or domains["max_variants"] != 4096
                or not isinstance(domains["max_words"], int)
                or isinstance(domains["max_words"], bool)
                or domains["max_words"] != 20000
                or domains["value_policy"] != "finite-exact-or-tracked-fallback"
                or domains["target_policy"]
                != "defined-pattern-or-tracked-tree"
            ):
                raise OwnershipError(
                    "Make prerequisite domain bounds or policy are invalid"
                )
    seal = data["seal"]
    if not isinstance(seal, str) or not re.fullmatch(r"[0-9a-f]{64}", seal):
        raise OwnershipError("Make dynamic dependency registry seal is invalid")
    expected_seal = _sha256(
        MAKE_DYNAMIC_SEAL_DOMAIN,
        canonical_make_dynamic_payload(data),
    )
    if seal != expected_seal:
        raise OwnershipError("Make dynamic dependency registry seal does not match")
    result = {}
    ids = set()
    for index, contract in enumerate(data["contracts"]):
        label = f"Make dynamic dependency contract {index}"
        fields = {
            "id",
            "expression",
            "tool",
            "input_files",
            "input_variables",
            "automatic_inputs",
            "resolved_value",
            "owning_evidence_ids",
        }
        if schema_version >= 5:
            fields.add("command_regex")
        if not isinstance(contract, dict) or set(contract) != fields:
            raise OwnershipError(f"{label} has invalid fields")
        if (
            not isinstance(contract["id"], str)
            or not contract["id"]
            or contract["id"] in ids
        ):
            raise OwnershipError(f"{label} has duplicate or invalid ID")
        ids.add(contract["id"])
        expression = contract["expression"]
        if (
            not isinstance(expression, str)
            or not expression.startswith("$(shell ")
            or not expression.endswith(")")
            or expression in result
        ):
            raise OwnershipError(f"{label} has duplicate or invalid shell expression")
        if contract["resolved_value"] is not None and not isinstance(
            contract["resolved_value"], str
        ):
            raise OwnershipError(f"{label}.resolved_value must be string or null")
        if schema_version >= 5:
            command_regex = contract["command_regex"]
            if (
                not isinstance(command_regex, str)
                or not command_regex.startswith("^")
                or not command_regex.endswith("$")
                or len(command_regex) > 8192
            ):
                raise OwnershipError(f"{label}.command_regex is invalid")
            try:
                re.compile(command_regex)
            except re.error as error:
                raise OwnershipError(
                    f"{label}.command_regex is invalid: {error}"
                ) from error
        for field in (
            "input_files",
            "input_variables",
            "automatic_inputs",
            "owning_evidence_ids",
        ):
            values = contract[field]
            if (
                not isinstance(values, list)
                or len(values) != len(set(values))
                or not all(isinstance(value, str) and value for value in values)
            ):
                raise OwnershipError(f"{label}.{field} must contain unique strings")
        tool = _validate_relative_path(contract["tool"], f"{label}.tool")
        tool_content = loader.read_blob(tool, f"{label}.tool")
        semantics = {
            "tool": {
                "path": tool,
                "semantics": (
                    "observed-by-authoritative-gnu-make"
                    if tool == "Makefile" or tool.endswith(".mk")
                    else _source_semantics(
                        tool,
                        tool_content,
                    )
                ),
            },
            "inputs": [],
        }
        for input_path in contract["input_files"]:
            input_path = _validate_relative_path(input_path, f"{label}.input")
            semantics["inputs"].append(
                {
                    "path": input_path,
                    "semantics": _source_semantics(
                        input_path,
                        loader.read_blob(input_path, f"{label}.input"),
                    ),
                }
            )
        result[expression] = {
            **contract,
            "authority_semantics": semantics,
        }
    return result


def load_make_ambient_contracts(
    loader: AuthorityLoader,
    *,
    required: bool,
) -> dict[str, dict[str, Any]]:
    path = MAKE_DYNAMIC_PATH.as_posix()
    if path not in loader.entries:
        if required:
            raise OwnershipError("Make ambient input registry is not tracked")
        return {}
    data = loader.read_json(MAKE_DYNAMIC_PATH, "Make ambient input registry")
    load_make_dynamic_contracts(loader, required=required)
    if data["schema_version"] == 1:
        return {}
    ambient = data["ambient_inputs"]
    names = [
        (name, "default")
        for name in ambient["allowed_names"]
    ]
    if data["schema_version"] >= 3:
        names.extend(
            (name, "undefined")
            for name in ambient["undefined_names"]
        )
    return {
        name: {
            "name": name,
            "category": category,
            "allowed_sources": ambient["allowed_sources"],
            "value_policy": ambient["value_policy"],
            "provenance": ambient["provenance"],
            "evidence_binding": ambient["evidence_binding"],
        }
        for name, category in names
    }


def load_make_typed_variable_contracts(
    loader: AuthorityLoader,
    *,
    required: bool,
) -> tuple[dict[str, dict[str, str]], ...]:
    path = MAKE_DYNAMIC_PATH.as_posix()
    if path not in loader.entries:
        if required:
            raise OwnershipError("Make variable authority registry is not tracked")
        return {}, {}, {}
    data = loader.read_json(MAKE_DYNAMIC_PATH, "Make variable authority registry")
    load_make_dynamic_contracts(loader, required=required)
    if data["schema_version"] < 3:
        return {}, {}, {}
    ambient = data["ambient_inputs"]
    return tuple(
        {
            item["name"]: item
            for item in ambient[field]
        }
        for field in (
            "trusted_builtins",
            "scoped_variables",
            "escaped_literals",
        )
    )


def load_make_prerequisite_domains(
    loader: AuthorityLoader,
    *,
    required: bool,
) -> dict[str, dict[str, Any]]:
    path = MAKE_DYNAMIC_PATH.as_posix()
    if path not in loader.entries:
        if required:
            raise OwnershipError("Make prerequisite domain registry is not tracked")
        return {}
    data = loader.read_json(MAKE_DYNAMIC_PATH, "Make prerequisite domain registry")
    load_make_dynamic_contracts(loader, required=required)
    if data["schema_version"] < 4:
        return {}
    domains = data["prerequisite_domains"]
    result = {
        name: {
            "name": name,
            "kind": "tracked-fallback",
        }
        for name in domains["tracked_fallback_names"]
    }
    result.update(
        {
            domain["name"]: {
                "name": domain["name"],
                "kind": "explicit",
                "values": domain["values"],
            }
            for domain in domains["explicit"]
        }
    )
    return result


def load_make_generated_prerequisite_paths(
    loader: AuthorityLoader,
    *,
    required: bool,
) -> dict[str, dict[str, Any]]:
    path = MAKE_DYNAMIC_PATH.as_posix()
    if path not in loader.entries:
        if required:
            raise OwnershipError("Make generated prerequisite registry is not tracked")
        return {}
    data = loader.read_json(MAKE_DYNAMIC_PATH, "Make generated prerequisite registry")
    load_make_dynamic_contracts(loader, required=required)
    if data["schema_version"] < 4:
        return {}
    result = {}
    for item in data["prerequisite_domains"]["generated_paths"]:
        generated_path = _validate_relative_path(
            item["path"],
            "Make generated prerequisite path",
        )
        authorities = []
        for authority_path in item["authority_files"]:
            authority_path = _validate_relative_path(
                authority_path,
                "Make generated prerequisite authority",
            )
            authorities.append(
                {
                    "path": authority_path,
                    "semantics": _source_semantics(
                        authority_path,
                        loader.read_blob(
                            authority_path,
                            "Make generated prerequisite authority",
                        ),
                    ),
                }
            )
        result[generated_path] = {
            "path": generated_path,
            "authority_files": authorities,
        }
    return result


def load_make_symbolic_recipe_names(
    loader: AuthorityLoader,
    *,
    required: bool,
) -> set[str]:
    path = MAKE_DYNAMIC_PATH.as_posix()
    if path not in loader.entries:
        if required:
            raise OwnershipError("Make symbolic recipe registry is not tracked")
        return set()
    data = loader.read_json(MAKE_DYNAMIC_PATH, "Make symbolic recipe registry")
    load_make_dynamic_contracts(loader, required=required)
    if data["schema_version"] < 5:
        return set()
    return set(data["prerequisite_domains"]["symbolic_recipe_names"])



def _parse_make_authorities(
    loader: AuthorityLoader,
    requested_targets: set[str] | None = None,
    *,
    require_dynamic_contracts: bool = False,
) -> dict[str, dict[str, Any]]:
    if requested_targets is None:
        raise OwnershipError(
            "authoritative GNU Make probing requires explicit target roots"
        )
    dynamic_contracts = load_make_dynamic_contracts(
        loader,
        required=require_dynamic_contracts,
    )
    prerequisite_domains = load_make_prerequisite_domains(
        loader,
        required=require_dynamic_contracts,
    )
    generated_paths = load_make_generated_prerequisite_paths(
        loader,
        required=require_dynamic_contracts,
    )
    ambient_contracts = load_make_ambient_contracts(
        loader,
        required=require_dynamic_contracts,
    )
    symbolic_recipe_names = load_make_symbolic_recipe_names(
        loader,
        required=require_dynamic_contracts,
    )
    (
        trusted_builtins,
        scoped_variables,
        escaped_literals,
    ) = load_make_typed_variable_contracts(
        loader,
        required=require_dynamic_contracts,
    )
    cache_key = _make_authority_cache_key(
        loader,
        requested_targets,
        require_dynamic_contracts,
    )
    cached = _MAKE_AUTHORITY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        from scripts.validation_ownership import make_probe

        result = make_probe.run_probe(
            loader,
            requested_targets,
            prerequisite_domains,
            dynamic_contracts,
            declared_external_names=set(ambient_contracts),
            environment_names=set(ambient_contracts),
            generated_path_names=set(generated_paths),
            symbolic_recipe_names=symbolic_recipe_names,
            ambient_undefined_names={
                name
                for name, contract in ambient_contracts.items()
                if contract["category"] == "undefined"
            },
            escaped_literal_names=escaped_literals,
            scoped_variable_names=scoped_variables,
            trusted_builtin_names=trusted_builtins,
            trusted_reference_names={
                *trusted_builtins,
                *scoped_variables,
                *escaped_literals,
            },
            scratch_root=(
                loader.scratch_root
                if loader.scratch_root is not None
                else (
                    loader.root
                    / "build"
                    / "test-artifacts"
                    / "validation-ownership"
                )
            ),
        )
    except make_probe.MakeProbeError as error:
        raise OwnershipError(str(error)) from error
    dynamic_values = sorted(
        dynamic_contracts.values(),
        key=lambda item: item["id"],
    )
    for authority in result.values():
        authority["dynamic_dependencies"] = dynamic_values
    if len(_MAKE_AUTHORITY_CACHE) >= _MAKE_AUTHORITY_CACHE_LIMIT:
        _MAKE_AUTHORITY_CACHE.pop(next(iter(_MAKE_AUTHORITY_CACHE)))
    _MAKE_AUTHORITY_CACHE[cache_key] = result
    return result


def _make_authority_state(
    loader: AuthorityLoader,
) -> tuple[tuple[str, ...], ...]:
    if loader.revision is not None:
        return tuple(
            (
                path,
                entry.mode,
                entry.object_type,
                entry.object_id,
            )
            for path, entry in sorted(loader.entries.items())
        )

    head_entries = git_tree_entries(loader.root)
    output = _git(
        loader.root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--name-only",
        "-z",
        "HEAD",
        "--",
    ).stdout
    try:
        changed_paths = {
            path
            for path in output.decode("utf-8").split("\0")
            if path
        }
    except UnicodeDecodeError as error:
        raise OwnershipError("Git returned a non-UTF-8 changed path") from error

    records = []
    for path, entry in sorted(loader.entries.items()):
        head_entry = head_entries.get(path)
        if head_entry == entry and path not in changed_paths:
            identity = entry.object_id
        elif entry.mode == GITLINK_MODE:
            identity = f"gitlink:{entry.object_id}"
        else:
            candidate = loader.root / path
            try:
                path_stat = os.lstat(candidate)
            except FileNotFoundError:
                identity = "worktree:missing"
            except OSError as error:
                raise OwnershipError(
                    f"cannot inspect Make cache path {path!r}: {error}"
                ) from error
            else:
                if stat.S_ISLNK(path_stat.st_mode):
                    try:
                        target = os.readlink(candidate)
                    except OSError as error:
                        raise OwnershipError(
                            f"cannot read Make cache symlink {path!r}: {error}"
                        ) from error
                    identity = f"worktree:symlink:{target}"
                elif stat.S_ISREG(path_stat.st_mode):
                    mode = "100755" if path_stat.st_mode & 0o111 else "100644"
                    digest = hashlib.sha256(
                        loader.read_blob(path, "Make cache authority")
                    ).hexdigest()
                    identity = f"worktree:{mode}:{digest}"
                else:
                    identity = f"worktree:special:{stat.S_IFMT(path_stat.st_mode):o}"
        records.append(
            (
                path,
                entry.mode,
                entry.object_type,
                identity,
            )
        )
    return tuple(records)


def _make_authority_cache_key(
    loader: AuthorityLoader,
    requested_targets: Iterable[str],
    require_dynamic_contracts: bool,
) -> tuple[Any, ...]:
    return (
        "authoritative-gnu-make-v2",
        str(loader.root),
        loader.revision,
        tuple(sorted(requested_targets)),
        _make_authority_state(loader),
        require_dynamic_contracts,
    )


def _same_make_authority_tree(
    current_loader: AuthorityLoader,
    base_loader: AuthorityLoader,
) -> bool:
    return _make_authority_state(current_loader) == _make_authority_state(
        base_loader
    )


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
    make_targets = (
        _parse_make_authorities(
            loader,
            requested_make_targets,
            require_dynamic_contracts=True,
        )
        if requested_make_targets
        else {}
    )
    ambient_contracts = load_make_ambient_contracts(loader, required=True)
    (
        trusted_builtins,
        scoped_variables,
        escaped_literals,
    ) = load_make_typed_variable_contracts(loader, required=True)
    expected_census = {
        "ambient_undefined": {
            name
            for name, contract in ambient_contracts.items()
            if contract["category"] == "undefined"
        },
        "trusted_builtins": set(trusted_builtins),
        "scoped_variables": set(scoped_variables),
        "escaped_literals": set(escaped_literals),
    }
    actual_census = {
        key: {
            name
            for target in make_targets.values()
            for name in target["variable_census"][key]
        }
        for key in expected_census
    }
    if requested_make_targets and actual_census != expected_census:
        raise OwnershipError(
            "Make variable authority census does not match the sealed "
            f"registry (actual={actual_census!r}, expected={expected_census!r})"
        )
    prerequisite_domains = load_make_prerequisite_domains(loader, required=True)
    actual_prerequisite_domains = {
        name
        for target in make_targets.values()
        for name in target["prerequisite_domain_census"]["used"]
    }
    if (
        requested_make_targets
        and actual_prerequisite_domains != set(prerequisite_domains)
    ):
        raise OwnershipError(
            "Make prerequisite domain census does not match the sealed "
            f"registry (actual={sorted(actual_prerequisite_domains)}, "
            f"expected={sorted(prerequisite_domains)})"
        )
    symbolic_recipe_names = load_make_symbolic_recipe_names(
        loader,
        required=True,
    )
    actual_symbolic_recipe_names = {
        name
        for target in make_targets.values()
        for name in target["record"]["symbolic_recipe_names"]
    }
    if (
        requested_make_targets
        and actual_symbolic_recipe_names != symbolic_recipe_names
    ):
        raise OwnershipError(
            "Make symbolic recipe census does not match the sealed registry "
            f"(actual={sorted(actual_symbolic_recipe_names)}, "
            f"expected={sorted(symbolic_recipe_names)})"
        )
    generated_prerequisites = load_make_generated_prerequisite_paths(
        loader,
        required=True,
    )
    actual_generated_prerequisites = {
        path
        for target in make_targets.values()
        for path in target["prerequisite_domain_census"]["generated_paths"]
    }
    if (
        requested_make_targets
        and actual_generated_prerequisites != set(generated_prerequisites)
    ):
        raise OwnershipError(
            "Make generated prerequisite census does not match the sealed "
            f"registry (actual={sorted(actual_generated_prerequisites)}, "
            f"expected={sorted(generated_prerequisites)})"
        )
    workflow_jobs, workflow_steps = _workflow_authorities(
        loader,
        strict=strict_workflow,
    )
    tester_cases = _load_test_case_registry(loader)
    identities = set()
    result = {}
    dynamic_owners: dict[str, set[str]] = defaultdict(set)
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
            target_authority = copy.deepcopy(make_targets[target])
            target_authority["dynamic_dependencies"] = [
                contract
                for contract in target_authority["dynamic_dependencies"]
                if node_id in contract["owning_evidence_ids"]
            ]
            fingerprint = _sha256(
                b"validation-ownership-make-target-v1\0",
                {"target": target, "record": target_authority},
            )
            for contract in target_authority["dynamic_dependencies"]:
                dynamic_owners[contract["id"]].add(node_id)
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
    contracts = load_make_dynamic_contracts(loader, required=True)
    for contract in contracts.values() if requested_make_targets else ():
        expected = set(contract["owning_evidence_ids"])
        unknown = sorted(expected - set(evidence_nodes))
        if unknown:
            raise OwnershipError(
                f"Make dynamic contract {contract['id']!r} has unknown owners {unknown}"
            )
        actual = dynamic_owners.get(contract["id"], set())
        if actual != expected:
            raise OwnershipError(
                f"Make dynamic contract {contract['id']!r} owner mismatch "
                f"(expected={sorted(expected)}, actual={sorted(actual)})"
            )
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
    authority_nodes: dict[tuple[str, ...], str] = {}
    for node_id, node in evidence_nodes.items():
        identity = _authority_identity(node["authority"])
        previous = authority_nodes.get(identity)
        if previous is not None:
            raise OwnershipError(
                f"evidence node {node_id!r} duplicates authority {identity!r}"
            )
        authority_nodes[identity] = node_id

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

    _validate_lifecycle(
        graph["artifact"],
        graph["lifecycle_events"],
        evidence_nodes,
        edge_ids,
    )
    generated_records, generated_paths = _generated_registry_records(loader)
    authorities = _validate_authorities(
        loader,
        evidence_nodes,
        generated_records,
        strict_workflow=True,
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
    if MAKE_DYNAMIC_PATH.as_posix() not in base_loader.entries:
        make_nodes = {
            node_id
            for node_id, node in current_nodes.items()
            if node["authority"]["kind"] == "make-target"
        }
        return {
            edge["id"]
            for edge in graph["edges"]
            if edge["target"] in make_nodes
        }
    probe_paths = {
        "scripts/validation_ownership/generated_registry_probe.py",
        "scripts/validation_ownership/make_probe.py",
        "scripts/validation_ownership/sandbox_exec.py",
        "scripts/validation_ownership/shell_interceptor.c",
    }
    if not probe_paths <= set(base_loader.entries):
        make_nodes = {
            node_id
            for node_id, node in current_nodes.items()
            if node["authority"]["kind"] == "make-target"
        }
        return {
            edge["id"]
            for edge in graph["edges"]
            if edge["target"] in make_nodes
        }
    make_node_ids = {
        node_id
        for node_id, node in current_nodes.items()
        if node["authority"]["kind"] == "make-target"
    }
    same_make_authority = _same_make_authority_tree(
        current_loader,
        base_loader,
    )
    if same_make_authority:
        prior_authorities = _validate_authorities(
            base_loader,
            {
                node_id: node
                for node_id, node in prior_nodes.items()
                if node_id not in make_node_ids
            },
            model["generated_records"],
            strict_workflow=False,
        )
        prior_authorities.update(
            {
                node_id: model["authorities"][node_id]
                for node_id in make_node_ids
            }
        )
    else:
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
    covered_pairs: dict[str, set[tuple[str, str]]] = defaultdict(set)
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
        covered_pairs[probe["expected_surface"]].update(pairs)
    missing_surfaces = sorted(surfaces - set(covered_pairs))
    if missing_surfaces:
        raise OwnershipError(
            f"probe oracle leaves graph surfaces unprobed: {missing_surfaces}"
        )
    graph_pairs: dict[str, set[tuple[str, str]]] = {
        surface: set()
        for surface in surfaces
    }
    for edge in graph["edges"]:
        if edge["type"] != "depends-on":
            graph_pairs[edge["source"]].add(
                (edge["type"], edge["target"])
            )
    incomplete_edges = {
        surface: {
            "missing": sorted(graph_pairs[surface] - covered_pairs[surface]),
            "unexpected": sorted(covered_pairs[surface] - graph_pairs[surface]),
        }
        for surface in sorted(surfaces)
        if covered_pairs[surface] != graph_pairs[surface]
    }
    if incomplete_edges:
        raise OwnershipError(
            f"probe oracle does not cover exact owned edges: {incomplete_edges}"
        )
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
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if model is None:
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
        validate_json_schema(
            graph,
            schema,
            schema,
            "$",
        )
        validate_probe_oracle(oracle, graph, entries)
        model = validate_graph(graph, schema, loader, entries)
        measurement = _measure(oracle, graph, model)
        if check_id == "TC-WORKFLOW-GATE-OWNERSHIP-001":
            cases = _load_test_case_registry(loader)
            if check_id not in cases:
                raise OwnershipError(
                    "ownership consistency tester case is stale"
                )
        if (
            measurement["false_positive_selections"] != 0
            or measurement["false_negative_selections"] != 0
        ):
            raise OwnershipError(
                "ownership lifecycle consumer has exact owner-pair selection loss"
            )
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


def _run_lifecycle_direct(
    authority_root: Path,
    artifact_root: Path,
    check_id: str,
) -> subprocess.CompletedProcess[bytes]:
    try:
        returncode = run_lifecycle_check(
            artifact_root,
            authority_root,
            check_id,
        )
        detail = b""
    except OwnershipError as error:
        returncode = 1
        detail = str(error).encode("utf-8")
    return subprocess.CompletedProcess(
        args=(check_id,),
        returncode=returncode,
        stdout=b"",
        stderr=detail,
    )


def _assert_lifecycle_consistency_identities(root: Path) -> None:
    consistency_checks = sorted(
        check_id
        for check_id in LIFECYCLE_CHECKS
        if check_id != "validation-ownership-check"
    )
    if not consistency_checks:
        return
    entries = git_tree_entries(root)
    loader = AuthorityLoader(root, entries)
    cases = _load_test_case_registry(loader)
    for check_id in consistency_checks:
        if check_id not in cases:
            raise OwnershipError(
                "ownership consistency tester case is stale"
            )


def validate_executable_lifecycle(
    root: Path,
    graph: dict[str, Any],
    *,
    baseline_validated: bool = False,
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
        _assert_lifecycle_consistency_identities(root)
        with tempfile.TemporaryDirectory(
            prefix=f".{root.name}-validation-ownership-proof-",
            dir=sandbox_parent,
        ) as temporary:
            sandbox = Path(temporary)
            artifact = sandbox / GRAPH_PATH
            artifact.parent.mkdir(parents=True)
            shutil.copy2(root / GRAPH_PATH, artifact)
            if not baseline_validated:
                initial = _run_lifecycle_direct(
                    root,
                    sandbox,
                    "validation-ownership-check",
                )
                if initial.returncode != 0:
                    detail = initial.stderr.decode(
                        "utf-8",
                        errors="replace",
                    ).strip()
                    raise OwnershipError(
                        "stale executable lifecycle baseline does not pass"
                        + (f": {detail}" if detail else "")
                    )
            backup = sandbox / "validation-ownership-graph.backup"
            for proof in proofs:
                trigger = triggers[proof["trigger_event_id"]]
                artifact.replace(backup)
                removed = _run_lifecycle_direct(
                    root,
                    sandbox,
                    "validation-ownership-check",
                )
                backup.replace(artifact)
                if removed.returncode == 0:
                    raise OwnershipError(
                        f"lifecycle proof {proof['id']!r} removal did not fail"
                    )
                removal_detail = removed.stderr.decode(
                    "utf-8",
                    errors="replace",
                )
                if LIFECYCLE_FAILURE_REASON not in removal_detail:
                    raise OwnershipError(
                        f"lifecycle proof {proof['id']!r} lacks the named failure"
                    )
                restored = _run_lifecycle_direct(
                    root,
                    sandbox,
                    "validation-ownership-check",
                )
                if restored.returncode != 0:
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
            model,
        )
        report["artifact"]["executable_lifecycle"] = (
            validate_executable_lifecycle(
                root,
                graph,
                baseline_validated=True,
            )
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
