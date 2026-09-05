#!/usr/bin/env python3
"""Strict stdlib schema consumer for workflow-pilot signed records."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from . import reporter


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = {
    "capability": ROOT
    / "scripts"
    / "workflow_pilot"
    / "git_publication_capability.schema.json",
    "plan": ROOT
    / "scripts"
    / "workflow_pilot"
    / "git_publication_plan.schema.json",
    "result": ROOT
    / "scripts"
    / "workflow_pilot"
    / "git_publication_result.schema.json",
}
FORMAT_VALIDATORS = {
    "rfc3339-utc-second": reporter.parse_time,
}


class SchemaError(ValueError):
    pass


def load_schema(name: str) -> dict[str, Any]:
    try:
        path = SCHEMAS[name]
    except KeyError as error:
        raise SchemaError(f"unknown signed-record schema {name!r}") from error
    try:
        schema = json.loads(path.read_text(encoding="ascii"))
    except (OSError, json.JSONDecodeError) as error:
        raise SchemaError(f"cannot load signed-record schema {name}") from error
    if not isinstance(schema, dict):
        raise SchemaError(f"signed-record schema {name} must be an object")
    return schema


def _validate_type(value: Any, expected: str, label: str) -> None:
    valid = {
        "array": isinstance(value, list),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
    }.get(expected)
    if valid is None:
        raise SchemaError(f"{label} schema uses unsupported type {expected!r}")
    if not valid:
        raise SchemaError(f"{label} must be a {expected}")


def validate(value: Any, schema: dict[str, Any], label: str = "record") -> None:
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try:
                validate(value, option, label)
            except SchemaError:
                continue
            matches += 1
        if matches != 1:
            raise SchemaError(f"{label} must match exactly one schema branch")
        return
    if "type" in schema:
        _validate_type(value, schema["type"], label)
    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{label} differs from its required constant")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{label} is not an allowlisted value")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{label} is below its minimum")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaError(f"{label} is too short")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise SchemaError(f"{label} does not match its schema pattern")
        named_format = schema.get("x-workflow-pilot-format")
        if named_format is not None:
            if schema.get("format") != "date-time":
                raise SchemaError(f"{label} named format lacks date-time binding")
            try:
                validator = FORMAT_VALIDATORS[named_format]
            except KeyError as error:
                raise SchemaError(
                    f"{label} uses an unregistered semantic format"
                ) from error
            try:
                validator(value, label)
            except reporter.PilotDataError as error:
                raise SchemaError(str(error)) from error
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaError(f"{label} has too few items")
        if schema.get("uniqueItems"):
            normalized = [
                json.dumps(item, sort_keys=True, separators=(",", ":"))
                for item in value
            ]
            if len(normalized) != len(set(normalized)):
                raise SchemaError(f"{label} contains duplicate items")
        if "items" in schema:
            for index, item in enumerate(value):
                validate(item, schema["items"], f"{label}[{index}]")
    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise SchemaError(f"{label} schema required field is malformed")
        missing = set(required) - set(value)
        if missing:
            raise SchemaError(f"{label} lacks required fields")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise SchemaError(f"{label} schema properties are malformed")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise SchemaError(f"{label} contains unrecognized fields")
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], f"{label}.{key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(
                    item,
                    schema["additionalProperties"],
                    f"{label}.{key}",
                )
        if (
            schema.get("x-workflow-pilot-outcome-union")
            == "git-publication-result-v1"
        ):
            _validate_publication_outcome(value, label)


def _validate_publication_outcome(value: dict[str, Any], label: str) -> None:
    phase = value["phase"]
    status = value["status"]
    code = value["code"]
    refs = value["refs"]
    transport = value["transport"]
    proof = value["termination_proof"]
    if phase == "ack":
        if (status, code, refs) == ("ready", "ready", None):
            return
        if status == "error" and refs is None:
            return
        raise SchemaError(f"{label} acknowledgement state is incoherent")
    if code == "ready":
        if status == "ok" and refs is None:
            return
        raise SchemaError(f"{label} readiness result is incoherent")
    issue = value["issue"]
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        raise SchemaError(f"{label} result issue is invalid")
    expected_refs = {
        f"refs/heads/workflow-pilot/issue-{issue}/authority",
        f"refs/tags/workflow-pilot/issue-{issue}/anchor",
    }
    if code == "indeterminate":
        valid_proof = proof == "unavailable" or (
            transport == "protected-local"
            and proof == "protected-receive-pack-terminated"
        )
        if status == "error" and valid_proof:
            if refs is not None and (
                not isinstance(refs, dict) or set(refs) != expected_refs
            ):
                raise SchemaError(
                    f"{label} indeterminate refs differ from exact issue pair"
                )
            return
        raise SchemaError(f"{label} indeterminate result is incoherent")
    if code in {
        "published",
        "committed-late",
        "safe-failed",
        "security-hold",
    }:
        if not isinstance(refs, dict) or set(refs) != expected_refs:
            raise SchemaError(f"{label} result refs differ from the exact issue pair")
        if code in {"published", "committed-late"}:
            if status != "ok" or any(oid is None for oid in refs.values()):
                raise SchemaError(f"{label} successful result is incoherent")
        elif status != "error":
            raise SchemaError(f"{label} non-success result is incoherent")
        if code == "safe-failed" and (
            transport != "protected-local"
            or proof != "protected-receive-pack-terminated"
        ):
            raise SchemaError(
                f"{label} safe-failed lacks local termination proof"
            )
        if code in {"published", "committed-late", "security-hold"} and (
            proof != "not-required"
        ):
            raise SchemaError(
                f"{label} terminal ref result has invalid termination proof"
            )
        return
    if status != "error" or refs is not None:
        raise SchemaError(f"{label} broker error result is incoherent")


def validate_record(record: Any, schema_name: str, label: str) -> None:
    validate(record, load_schema(schema_name), label)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a workflow-pilot signed record with semantic formats."
    )
    parser.add_argument("--schema", choices=sorted(SCHEMAS), required=True)
    parser.add_argument("--input", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        record = reporter.parse_json(
            arguments.input.read_text(encoding="ascii"),
            os.fspath(arguments.input),
        )
        validate_record(record, arguments.schema, "signed record")
    except (OSError, reporter.PilotDataError, SchemaError) as error:
        print(f"signed-schema: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
