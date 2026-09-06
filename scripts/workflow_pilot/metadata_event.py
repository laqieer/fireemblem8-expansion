"""Bind a workflow run to its immutable edited-event metadata transition."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from . import event_classifier

if TYPE_CHECKING:
    from .pr_metadata import PullRequestState


STEP_PREFIX = "workflow-pilot-metadata-event:v1:"


def transition_digest(
    state: PullRequestState,
    *,
    run_id: int,
    run_number: int,
    run_attempt: int,
    updated_at: str,
    pre_fields: dict[str, str],
    changed_fields: dict[str, str],
) -> str:
    from . import pr_metadata

    payload = {
        "schema_version": 1,
        "repository": state.repository,
        "repository_id": state.repository_id,
        "owner_id": state.repository_owner_id,
        "pr_number": state.number,
        "pr_id": state.pull_request_id,
        "pr_node_id": state.pull_request_node_id,
        "head_sha": state.head_sha,
        "head_ref": state.head_ref,
        "base_sha": state.base_sha,
        "base_ref": state.base_ref,
        "workflow_path": pr_metadata.WORKFLOW_PATH,
        "run_id": run_id,
        "run_number": run_number,
        "run_attempt": run_attempt,
        "event_updated_at": updated_at,
        "pre_fields": pre_fields,
        "changed_fields": changed_fields,
        "target_metadata_sha256": pr_metadata._metadata_digest(state.title, state.body),
    }
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")).hexdigest()


def event_digest(
    payload: object,
    *,
    repository: str,
    run_id: int,
    run_number: int,
    run_attempt: int,
) -> str:
    from . import pr_metadata as metadata

    repository = metadata._repository(repository)
    run_id = metadata._positive_int(run_id, "event run id")
    run_number = metadata._positive_int(run_number, "event run number")
    run_attempt = metadata._positive_int(run_attempt, "event run attempt")
    if not isinstance(payload, dict) or payload.get("action") != "edited":
        raise metadata.MetadataEditError("metadata attestation requires an edited event")
    number = metadata._positive_int(payload.get("number"), "event PR number")
    state = metadata._parse_pull_request_payload(
        payload.get("pull_request"), repository, number
    )
    event_repository = payload.get("repository")
    if (
        not isinstance(event_repository, dict)
        or metadata._positive_int(event_repository.get("id"), "event repository id")
        != state.repository_id
        or event_repository.get("full_name") != repository
    ):
        raise metadata.MetadataEditError("metadata event repository identity drifted")
    for label, actor in (
        ("repository owner", event_repository.get("owner")),
        ("sender", payload.get("sender")),
    ):
        if (
            not isinstance(actor, dict)
            or metadata._positive_int(actor.get("id"), f"event {label} id")
            != state.repository_owner_id
            or actor.get("login") != repository.split("/", 1)[0]
            or actor.get("type") != "User"
            or actor.get("site_admin") is not False
        ):
            raise metadata.MetadataEditError(f"metadata event {label} identity drifted")
    changes = payload.get("changes")
    if (
        not isinstance(changes, dict)
        or not changes
        or not set(changes) <= {"body", "title"}
    ):
        raise metadata.MetadataEditError("metadata event must change only title/body")
    values = {"body": state.body, "title": state.title}
    pre = dict(values)
    for name, change in changes.items():
        if not isinstance(change, dict) or set(change) != {"from"}:
            raise metadata.MetadataEditError("metadata event change is incomplete")
        previous = change["from"]
        if name == "body" and previous is None:
            previous = ""
        if not isinstance(previous, str) or (name == "title" and not previous.strip()):
            raise metadata.MetadataEditError("metadata event previous value is invalid")
        if previous == values[name]:
            raise metadata.MetadataEditError("metadata event field did not change")
        pre[name] = previous
    return transition_digest(
        state,
        run_id=run_id,
        run_number=run_number,
        run_attempt=run_attempt,
        updated_at=metadata._timestamp_text(state.updated_at),
        pre_fields={name: metadata._field_state_digest(value) for name, value in pre.items()},
        changed_fields={
            name: metadata._content_digest(values[name]) for name in changes
        },
    )


def main(argv: list[str] | None = None) -> int:
    from . import pr_metadata

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-number", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        digest = event_digest(
            event_classifier.load_event(args.event_path),
            repository=args.repository,
            run_id=args.run_id,
            run_number=args.run_number,
            run_attempt=args.run_attempt,
        )
        with args.output.open("a", encoding="ascii", newline="\n") as stream:
            stream.write(f"digest={digest}\n")
    except (pr_metadata.MetadataEditError, event_classifier.EventClassificationError,
            OSError, UnicodeError) as error:
        print(f"metadata-event: {error}", file=sys.stderr)
        return 2
    return 0
