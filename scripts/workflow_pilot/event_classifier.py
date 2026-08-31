#!/usr/bin/env python3
"""Classify Build workflow events without trusting mutable PR metadata."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MAX_EVENT_BYTES = 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
METADATA_FIELDS = frozenset({"body", "title"})
FULL_PR_ACTIONS = frozenset({"opened", "reopened", "synchronize"})


class EventClassificationError(ValueError):
    """The runner invocation or event file cannot be classified safely."""


@dataclass(frozen=True)
class EventDecision:
    classification: str
    reason: str
    run_expensive: bool
    expected_head: str

    def canonical_json(self) -> str:
        return json.dumps(
            asdict(self),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _full(reason: str, expected_head: str) -> EventDecision:
    return EventDecision(
        classification="full",
        reason=reason,
        run_expensive=True,
        expected_head=expected_head,
    )


def _valid_metadata_change(name: str, value: object, pull_request: dict[str, Any]) -> bool:
    if not isinstance(value, dict) or set(value) != {"from"}:
        return False
    previous = value["from"]
    if previous is not None and not isinstance(previous, str):
        return False
    if name not in pull_request:
        return False
    current = pull_request[name]
    if name == "title":
        return isinstance(current, str) and bool(current)
    return current is None or isinstance(current, str)


def _valid_pull_request_identity(
    pull_request: object,
    expected_head: str,
) -> dict[str, Any] | None:
    if not isinstance(pull_request, dict):
        return None
    head = pull_request.get("head")
    base = pull_request.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        return None
    if head.get("sha") != expected_head or not _is_sha(head.get("sha")):
        return None
    if not isinstance(base.get("ref"), str) or not base["ref"]:
        return None
    if not _is_sha(base.get("sha")):
        return None
    return pull_request


def _valid_base_change(value: object, pull_request: dict[str, Any]) -> bool:
    if not isinstance(value, dict) or set(value) != {"ref"}:
        return False
    ref = value["ref"]
    if not isinstance(ref, dict) or set(ref) != {"from"}:
        return False
    previous = ref["from"]
    current = pull_request["base"]["ref"]
    return (
        isinstance(previous, str)
        and bool(previous)
        and isinstance(current, str)
        and bool(current)
        and previous != current
    )


def classify_event(
    event_name: str,
    payload: object,
    *,
    github_ref: str,
    github_sha: str,
    expected_build_sha: str,
) -> EventDecision:
    if not _is_sha(expected_build_sha):
        raise EventClassificationError("--expected-build-sha must be a full lowercase SHA")
    if not isinstance(event_name, str) or not event_name:
        raise EventClassificationError("--event-name must be nonempty")
    if not isinstance(payload, dict):
        return _full("incomplete-payload", expected_build_sha)

    if event_name == "pull_request":
        pull_request = _valid_pull_request_identity(
            payload.get("pull_request"),
            expected_build_sha,
        )
        if pull_request is None:
            return _full("incomplete-pull-request", expected_build_sha)
        action = payload.get("action")
        if action in FULL_PR_ACTIONS:
            return _full(f"pull-request-{action}", expected_build_sha)
        if action != "edited":
            return _full("unknown-pull-request-action", expected_build_sha)

        changes = payload.get("changes")
        if not isinstance(changes, dict) or not changes:
            return _full("incomplete-edit", expected_build_sha)
        changed_fields = frozenset(changes)
        if changed_fields <= METADATA_FIELDS:
            if all(
                _valid_metadata_change(name, changes[name], pull_request)
                for name in changed_fields
            ):
                return EventDecision(
                    classification="metadata-only",
                    reason="body-title-only-edit",
                    run_expensive=False,
                    expected_head=expected_build_sha,
                )
            return _full("incomplete-edit", expected_build_sha)
        if changed_fields == {"base"}:
            reason = (
                "base-edit"
                if _valid_base_change(changes["base"], pull_request)
                else "incomplete-edit"
            )
            return _full(reason, expected_build_sha)
        if "base" in changed_fields or changed_fields & METADATA_FIELDS:
            return _full("mixed-edit", expected_build_sha)
        return _full("unknown-edit", expected_build_sha)

    if event_name == "push":
        if (
            payload.get("ref") == "refs/heads/master"
            and github_ref == "refs/heads/master"
            and payload.get("after") == expected_build_sha
            and github_sha == expected_build_sha
        ):
            return _full("master-push", expected_build_sha)
        return _full("incomplete-push", expected_build_sha)

    if event_name == "workflow_dispatch":
        return _full("explicit-final-dispatch", expected_build_sha)
    return _full("unknown-event", expected_build_sha)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EventClassificationError(f"event JSON repeats key {key!r}")
        value[key] = item
    return value


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
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EventClassificationError(f"cannot parse event payload: {error}") from error


def write_github_output(path: Path, decision: EventDecision) -> None:
    values = {
        "classification": decision.classification,
        "reason": decision.reason,
        "run_expensive": "true" if decision.run_expensive else "false",
        "expected_head": decision.expected_head,
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
    parser.add_argument("--expected-build-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        decision = classify_event(
            args.event_name,
            load_event(args.event_path),
            github_ref=args.github_ref,
            github_sha=args.github_sha,
            expected_build_sha=args.expected_build_sha,
        )
        write_github_output(args.output, decision)
    except EventClassificationError as error:
        print(f"build-event-classifier: {error}", file=sys.stderr)
        return 2
    print(decision.canonical_json(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
