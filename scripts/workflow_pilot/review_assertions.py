#!/usr/bin/env python3
"""Exact-base executable assertions for review-family evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
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
SUBJECT_ROOT = "scripts/workflow_pilot/assertion_subjects"


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


def load_subject(root: Path, family: str, member: str) -> dict[str, Any]:
    path = root / SUBJECT_ROOT / f"{family}_{member.replace('-', '_')}.json"
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_no_duplicates,
        )
    except (OSError, json.JSONDecodeError) as error:
        raise AssertionFailure("member subject is unavailable") from error
    subject = expect_object(raw, "member subject")
    expect_keys(
        subject,
        "member subject",
        (
            "schema_version",
            "family",
            "member",
            "payload",
        ),
    )
    if (
        subject["schema_version"] != 1
        or subject["family"] != family
        or subject["member"] != member
    ):
        raise AssertionFailure("member subject contradicts registry identity")
    return subject


def evaluate_subject(
    family: str, member: str, subject: dict[str, Any]
) -> dict[str, Any]:
    field_registry = {
        ("action", "actions"): ("actions", list),
        ("action", "items"): ("items", list),
        ("action", "targets"): ("targets", list),
        ("generated", "owners"): ("owners", list),
        ("generated", "outputs"): ("outputs", list),
        ("generated", "consumers"): ("consumers", list),
        ("generated", "drift-checks"): ("checks", list),
        ("lifecycle", "entries"): ("entries", list),
        ("lifecycle", "preservation"): ("preserved", list),
        ("lifecycle", "resets"): ("reset_rounds", list),
        ("lifecycle", "terminals"): ("terminals", list),
        ("resource", "enabled"): ("enabled", bool),
        ("resource", "disabled"): ("enabled", bool),
        ("wire", "producers"): ("producers", list),
        ("wire", "consumers"): ("consumers", list),
        ("wire", "validators"): ("validators", list),
        ("wire", "replay"): ("replay_keys", list),
        ("wire", "stale-bindings"): ("bindings", list),
    }
    field, expected_type = field_registry[(family, member)]
    payload = expect_object(subject["payload"], "member subject payload")
    expect_keys(payload, "member subject payload", (field,))
    value = payload[field]
    if not isinstance(value, expected_type):
        raise AssertionFailure("member subject input has the wrong type")
    if expected_type is list:
        if not value or len(value) != len(set(value)):
            raise AssertionFailure(
                "member subject input is empty or contains duplicates"
            )
        if any(not isinstance(item, str) or not item for item in value):
            raise AssertionFailure("member subject input item is invalid")
    if (family, member) == ("action", "actions") and value != [
        "read-candidate",
        "emit-local-report",
    ]:
        raise AssertionFailure("action sequence is not read-before-report")
    if (family, member) == ("lifecycle", "resets") and value != [
        "round-3",
        "round-6",
    ]:
        raise AssertionFailure("lifecycle reset rounds are not exact")
    return {"field": field, "value": value}


def execute_member(
    assertion: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
    expect_keys(
        request,
        "member request",
        (
            "assertion_id",
            "finding_id",
            "origin_root",
            "head_root",
        ),
    )
    family = assertion["family"]
    member = assertion["member"]
    origin = load_subject(Path(request["origin_root"]), family, member)
    head = load_subject(Path(request["head_root"]), family, member)
    outcome = assertion["outcome"]
    if outcome == "affected-fixed":
        try:
            evaluate_subject(family, member, origin)
        except AssertionFailure as error:
            origin_error = str(error)
        else:
            raise AssertionFailure(
                "affected-fixed origin assertion unexpectedly passed"
            )
        head_output = evaluate_subject(family, member, head)
        return {
            "program_case": f"member/{family}/{member}/affected-fixed",
            "origin_status": "fail",
            "origin_error": origin_error,
            "head_status": "pass",
            "head_semantic_output": head_output,
            "finding_id": request["finding_id"],
        }
    if outcome == "verified-unaffected":
        origin_output = evaluate_subject(family, member, origin)
        head_output = evaluate_subject(family, member, head)
        if origin_output != head_output:
            raise AssertionFailure(
                "verified-unaffected semantic outputs are not equivalent"
            )
        semantic_output_sha256 = hashlib.sha256(
            normalized_json(head_output)
        ).hexdigest()
        return {
            "program_case": f"member/{family}/{member}/verified-unaffected",
            "origin_status": "pass",
            "head_status": "pass",
            "semantic_output_sha256": semantic_output_sha256,
            "finding_id": request["finding_id"],
        }
    head_output = evaluate_subject(family, member, head)
    if head_output != {"field": "enabled", "value": False}:
        raise AssertionFailure("not-applicable predicate did not establish false")
    return {
        "program_case": "member/resource/disabled/not-applicable",
        "applicable": False,
        "reason": assertion["reason"],
        "finding_id": request["finding_id"],
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
