#!/usr/bin/env python3
"""Classify Build workflow events without trusting mutable PR metadata."""

from __future__ import annotations

import argparse
import json
import math
import re
import stat
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


MAX_EVENT_BYTES = 1024 * 1024
MAX_BRANCH_REF_BYTES = 1024
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
METADATA_FIELDS = frozenset({"body", "title"})
FULL_PR_ACTIONS = frozenset({"opened", "reopened", "synchronize"})


class EventClassificationError(ValueError):
    """The runner invocation or event file cannot be classified safely."""


@dataclass(frozen=True)
class EventDecision:
    classification: str
    expected_base: str
    reason: str
    run_expensive: bool
    expected_head: str
    full_fallback: bool
    head_valid: bool
    identity_valid: bool

    def canonical_json(self) -> str:
        return json.dumps(
            asdict(self),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _is_git_branch_ref(value: object) -> bool:
    if not isinstance(value, str) or not value or value == "@":
        return False
    try:
        if len(value.encode("utf-8")) > MAX_BRANCH_REF_BYTES:
            return False
    except UnicodeEncodeError:
        return False
    if (
        value.startswith("/")
        or value.endswith("/")
        or value.endswith(".")
        or "//" in value
        or ".." in value
        or "@{" in value
    ):
        return False
    if any(
        ord(character) < 0x20
        or ord(character) == 0x7F
        or character in " ~^:?*[\\"
        for character in value
    ):
        return False
    return all(
        component
        and not component.startswith(".")
        and not component.endswith(".lock")
        for component in value.split("/")
    )


def _full(
    reason: str,
    expected_head: str,
    expected_base: str = "",
    *,
    full_fallback: bool = False,
    head_valid: bool | None = None,
    identity_valid: bool = True,
) -> EventDecision:
    return EventDecision(
        classification="full",
        expected_base=expected_base,
        reason=reason,
        run_expensive=True,
        expected_head=expected_head,
        full_fallback=full_fallback,
        head_valid=bool(expected_head) if head_valid is None else head_valid,
        identity_valid=identity_valid,
    )


def _valid_metadata_change(name: str, value: object, pull_request: dict[str, Any]) -> bool:
    if not isinstance(value, dict) or set(value) != {"from"}:
        return False
    previous = value["from"]
    if name not in pull_request:
        return False
    current = pull_request[name]
    if name == "title":
        return (
            isinstance(previous, str)
            and bool(previous.strip())
            and isinstance(current, str)
            and bool(current.strip())
            and previous != current
        )
    return (
        (previous is None or isinstance(previous, str))
        and (current is None or isinstance(current, str))
        and previous != current
    )


def _pull_request_identity(
    pull_request: object,
    pr_head_sha: str,
    pr_base_sha: str,
) -> tuple[dict[str, Any] | None, EventDecision | None]:
    expected_head = pr_head_sha if _is_sha(pr_head_sha) else ""
    expected_base = pr_base_sha if _is_sha(pr_base_sha) else ""
    if not isinstance(pull_request, dict):
        return None, _full(
            "incomplete-pull-request",
            expected_head,
            expected_base,
            identity_valid=False,
        )
    head = pull_request.get("head")
    base = pull_request.get("base")
    payload_head = head.get("sha") if isinstance(head, dict) else None
    payload_base = base.get("sha") if isinstance(base, dict) else None
    payload_base_ref = base.get("ref") if isinstance(base, dict) else None
    missing_head = not expected_head or not _is_sha(payload_head)
    head_valid = bool(expected_head) and payload_head == expected_head
    missing_base = (
        not expected_base
        or not _is_sha(payload_base)
        or not isinstance(base, dict)
        or not _is_git_branch_ref(payload_base_ref)
    )
    if missing_head or missing_base:
        reason = (
            "missing-pull-request-identities"
            if missing_head and missing_base
            else "missing-pull-request-head"
            if missing_head
            else "missing-pull-request-base"
        )
        return None, _full(
            reason,
            expected_head,
            expected_base,
            full_fallback=head_valid and missing_base,
            head_valid=head_valid,
            identity_valid=False,
        )
    if payload_head != expected_head or payload_base != expected_base:
        return None, _full(
            "pull-request-identity-mismatch",
            expected_head,
            expected_base,
            full_fallback=head_valid,
            head_valid=head_valid,
            identity_valid=False,
        )
    return pull_request, None


def _valid_base_change(value: object, pull_request: dict[str, Any]) -> bool:
    if not isinstance(value, dict) or set(value) != {"ref", "sha"}:
        return False
    ref = value["ref"]
    sha = value["sha"]
    if (
        not isinstance(ref, dict)
        or set(ref) != {"from"}
        or not isinstance(sha, dict)
        or set(sha) != {"from"}
    ):
        return False
    previous_ref = ref["from"]
    previous_sha = sha["from"]
    current_ref = pull_request["base"]["ref"]
    current_sha = pull_request["base"]["sha"]
    return (
        _is_git_branch_ref(previous_ref)
        and _is_sha(previous_sha)
        and _is_git_branch_ref(current_ref)
        and _is_sha(current_sha)
        and previous_ref != current_ref
        and previous_sha != current_sha
    )


def classify_event(
    event_name: str,
    payload: object,
    *,
    github_ref: str,
    github_sha: str,
    pr_base_sha: str,
    pr_head_sha: str,
    push_sha: str,
) -> EventDecision:
    if not isinstance(event_name, str) or not event_name:
        raise EventClassificationError("--event-name must be nonempty")
    if not isinstance(payload, dict):
        return _full("incomplete-payload", "", identity_valid=False)

    if event_name == "pull_request":
        pull_request, identity_error = _pull_request_identity(
            payload.get("pull_request"),
            pr_head_sha,
            pr_base_sha,
        )
        if identity_error is not None:
            return identity_error
        if pull_request is None:
            return _full(
                "incomplete-pull-request",
                "",
                identity_valid=False,
            )
        action = payload.get("action")
        if action in FULL_PR_ACTIONS:
            return _full(f"pull-request-{action}", pr_head_sha, pr_base_sha)
        if action != "edited":
            return _full("unknown-pull-request-action", pr_head_sha, pr_base_sha)

        changes = payload.get("changes")
        if not isinstance(changes, dict) or not changes:
            return _full("incomplete-edit", pr_head_sha, pr_base_sha)
        changed_fields = frozenset(changes)
        if changed_fields <= METADATA_FIELDS:
            if all(
                _valid_metadata_change(name, changes[name], pull_request)
                for name in changed_fields
            ):
                return EventDecision(
                    classification="metadata-only",
                    expected_base=pr_base_sha,
                    reason="body-title-only-edit",
                    run_expensive=False,
                    expected_head=pr_head_sha,
                    full_fallback=False,
                    head_valid=True,
                    identity_valid=True,
                )
            return _full("incomplete-edit", pr_head_sha, pr_base_sha)
        if changed_fields == {"base"}:
            reason = (
                "base-edit"
                if _valid_base_change(changes["base"], pull_request)
                else "incomplete-edit"
            )
            return _full(reason, pr_head_sha, pr_base_sha)
        if "base" in changed_fields or changed_fields & METADATA_FIELDS:
            return _full("mixed-edit", pr_head_sha, pr_base_sha)
        return _full("unknown-edit", pr_head_sha, pr_base_sha)

    if event_name == "push":
        expected_head = push_sha if _is_sha(push_sha) else ""
        if (
            payload.get("ref") == "refs/heads/master"
            and github_ref == "refs/heads/master"
            and payload.get("after") == expected_head
            and github_sha == expected_head
            and expected_head
        ):
            return _full("master-push", expected_head)
        return _full(
            "incomplete-push",
            expected_head,
            head_valid=bool(expected_head),
            identity_valid=False,
        )

    if event_name == "workflow_dispatch":
        expected_head = github_sha if _is_sha(github_sha) else ""
        return _full(
            "explicit-final-dispatch",
            expected_head,
            head_valid=bool(expected_head),
            identity_valid=bool(expected_head),
        )
    return _full("unknown-event", "", identity_valid=False)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EventClassificationError(f"event JSON repeats key {key!r}")
        value[key] = item
    return value


def _reject_nonfinite_constant(value: str) -> None:
    raise EventClassificationError(f"event JSON contains non-finite number {value}")


def _parse_strict_float(value: str) -> float:
    if len(value) > 128:
        raise EventClassificationError("event JSON float literal is too long")
    try:
        decimal_value = Decimal(value)
        float_value = float(decimal_value)
    except (InvalidOperation, OverflowError, ValueError) as error:
        raise EventClassificationError(
            f"event JSON float is invalid: {value}"
        ) from error
    if not math.isfinite(float_value):
        raise EventClassificationError(f"event JSON float overflows: {value}")
    if decimal_value != 0 and float_value == 0:
        raise EventClassificationError(f"event JSON float underflows: {value}")
    return float_value


def _ensure_finite_numbers(value: object) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EventClassificationError("event JSON contains non-finite float")
        return
    if isinstance(value, dict):
        for item in value.values():
            _ensure_finite_numbers(item)
        return
    if isinstance(value, list):
        for item in value:
            _ensure_finite_numbers(item)


def load_event(path: Path) -> object:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise EventClassificationError(f"cannot inspect event payload: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise EventClassificationError("event payload must be a regular file")
    if metadata.st_size > MAX_EVENT_BYTES:
        raise EventClassificationError("event payload exceeds 1 MiB")
    try:
        raw = path.read_bytes()
        if len(raw) != metadata.st_size:
            raise EventClassificationError("event payload changed while being read")
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_constant,
            parse_float=_parse_strict_float,
        )
        _ensure_finite_numbers(value)
        return value
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EventClassificationError(f"cannot parse event payload: {error}") from error


def write_github_output(path: Path, decision: EventDecision) -> None:
    values = {
        "classification": decision.classification,
        "expected_base": decision.expected_base,
        "expected_head": decision.expected_head,
        "full_fallback": "true" if decision.full_fallback else "false",
        "head_valid": "true" if decision.head_valid else "false",
        "identity_valid": "true" if decision.identity_valid else "false",
        "reason": decision.reason,
        "run_expensive": "true" if decision.run_expensive else "false",
    }
    try:
        with path.open("a", encoding="ascii", newline="\n") as output:
            for name, value in values.items():
                output.write(f"{name}={value}\n")
    except OSError as error:
        raise EventClassificationError(f"cannot write GitHub outputs: {error}") from error


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify a GitHub Build event.")
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--github-ref", required=True)
    parser.add_argument("--github-sha", required=True)
    parser.add_argument("--pr-base-sha", required=True)
    parser.add_argument("--pr-head-sha", required=True)
    parser.add_argument("--push-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adaptive", action="store_true")
    parser.add_argument("--repository", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = load_event(args.event_path)
        decision = classify_event(
            args.event_name,
            payload,
            github_ref=args.github_ref,
            github_sha=args.github_sha,
            pr_base_sha=args.pr_base_sha,
            pr_head_sha=args.pr_head_sha,
            push_sha=args.push_sha,
        )
        selected, binding = None, None
        if args.adaptive:
            from scripts.workflow_pilot import adaptive_gate, pr_metadata
            if not pr_metadata.REPOSITORY_RE.fullmatch(args.repository):
                raise EventClassificationError("adaptive route requires exact repository")
            client = pr_metadata.GitHubClient("/usr/bin/gh")
            try:
                if args.event_name == "pull_request":
                    decision, selected, binding = adaptive_gate.route_event(
                        client, decision, payload, args.repository)
                elif args.event_name == "workflow_dispatch":
                    decision, selected, binding = adaptive_gate.route_dispatch(
                        client, decision, payload, args.repository, args.github_ref)
            except (ValueError, pr_metadata.MetadataEditError) as error:
                raise EventClassificationError(str(error)[:1000]) from error
        write_github_output(args.output, decision)
        if selected is not None:
            with args.output.open("a", encoding="ascii") as output:
                output.write(f"candidate_binding={binding}\ndecision_oid={selected.decision_oid or ''}\n")
                output.write(f"gate_mode={selected.mode}\n")
                output.write("gate_reason=" + json.dumps(selected.reason, ensure_ascii=True) + "\n")
    except EventClassificationError as error:
        print(f"build-event-classifier: {error}", file=sys.stderr)
        return 2
    print(decision.canonical_json(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
