#!/usr/bin/env python3
"""Read-only GitHub collection and executable review-check receipts."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.workflow_pilot import reporter


GH = "/usr/bin/gh"
CHECK_RECEIPT_DOMAIN = b"workflow-review-check-receipt-v1\0"
RECEIPT_DOMAIN = b"workflow-review-authenticated-envelope-v2\0"
RECEIPT_PURPOSE = "independent-review-report"
RECEIPT_MAX_LIFETIME_SECONDS = 600
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
BASE_CHECKER_PATH = "scripts/workflow_pilot/review_base_checker.py"
BASE_CHECKER_ARGV = (
    "/usr/bin/python3",
    "-I",
    "review_base_checker.py",
    "--input",
    "checker-input.json",
)


GRAPHQL_QUERY = r"""
query ReviewFamilyEvidence($owner: String!, $name: String!, $number: Int!) {
  viewer { id login }
  repository(owner: $owner, name: $name) {
    viewerPermission
    owner { id login }
    pullRequest(number: $number) {
      id
      number
      createdAt
      headRefOid
      author { id login }
      commits(first: 100) {
        pageInfo { hasNextPage }
        nodes { commit { id oid pushedDate committedDate } }
      }
      timelineItems(first: 100, itemTypes: [HEAD_REF_FORCE_PUSHED_EVENT]) {
        pageInfo { hasNextPage }
        nodes {
          ... on HeadRefForcePushedEvent {
            id
            createdAt
            afterCommit { oid }
          }
        }
      }
      reviews(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          id
          databaseId
          state
          submittedAt
          body
          commit { oid }
          author { id login }
          comments(first: 100) {
            pageInfo { hasNextPage }
            nodes { id createdAt }
          }
        }
      }
      reviewThreads(first: 100) {
        pageInfo { hasNextPage }
        nodes {
          id
          isResolved
          comments(first: 100) {
            pageInfo { hasNextPage }
            nodes { id createdAt pullRequestReview { id } }
          }
        }
      }
      comments(first: 100) {
        pageInfo { hasNextPage }
        nodes { id createdAt body author { id login } }
      }
    }
  }
}
"""


def receipt_payload(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key != "seal"}


def receipt_seal(raw: dict[str, Any]) -> str:
    return hashlib.sha256(
        CHECK_RECEIPT_DOMAIN + reporter.normalized_json(receipt_payload(raw))
    ).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_signed_receipt_bytes(
    payload_bytes: bytes,
    *,
    repository: str,
    pull_request: int,
    candidate_sha: str,
    issued_at: str,
    expires_at: str,
    nonce: str,
    key_id: str,
    key_epoch: int,
    key: bytes,
) -> bytes:
    if not isinstance(payload_bytes, bytes):
        raise reporter.PilotDataError("receipt payload must be immutable bytes")
    if not isinstance(key, bytes) or len(key) < 32:
        raise reporter.PilotDataError(
            "receipt trust key must contain at least 32 bytes"
        )
    envelope = {
        "schema_version": 2,
        "repository": repository,
        "pull_request": pull_request,
        "candidate_sha": candidate_sha,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
        "key_id": key_id,
        "key_epoch": key_epoch,
        "purpose": RECEIPT_PURPOSE,
        "payload_b64": base64.b64encode(payload_bytes).decode("ascii"),
    }
    envelope["hmac_sha256"] = hmac.new(
        key,
        RECEIPT_DOMAIN + reporter.normalized_json(envelope),
        hashlib.sha256,
    ).hexdigest()
    return reporter.normalized_json(envelope)


def verify_signed_receipt_bytes(
    receipt_bytes: bytes,
    *,
    repository: str,
    pull_request: int,
    candidate_sha: str,
    trusted_key_id: str,
    trusted_key_epoch: int,
    trusted_key: bytes,
    current_time: datetime,
    replay_store: Path | None,
    consume_nonce: bool,
) -> bytes:
    if not isinstance(receipt_bytes, bytes):
        raise reporter.PilotDataError("receipt must be immutable bytes")
    try:
        text = receipt_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise reporter.PilotDataError("receipt is not UTF-8") from error
    envelope = reporter.expect_object(
        reporter.parse_json(text, "authenticated review receipt"),
        "authenticated review receipt",
    )
    if reporter.normalized_json(envelope) != receipt_bytes:
        raise reporter.PilotDataError(
            "authenticated review receipt is not canonical immutable bytes"
        )
    reporter.expect_keys(
        envelope,
        "authenticated review receipt",
        (
            "schema_version",
            "repository",
            "pull_request",
            "candidate_sha",
            "issued_at",
            "expires_at",
            "nonce",
            "key_id",
            "key_epoch",
            "purpose",
            "payload_b64",
            "hmac_sha256",
        ),
    )
    if envelope["schema_version"] != 2:
        raise reporter.PilotDataError(
            "authenticated review receipt.schema_version must be 2"
        )
    reporter.expect_string(
        envelope["repository"], "authenticated review receipt.repository"
    )
    reporter.expect_int(
        envelope["pull_request"],
        "authenticated review receipt.pull_request",
        1,
    )
    reporter.expect_sha(
        envelope["candidate_sha"],
        "authenticated review receipt.candidate_sha",
    )
    reporter.expect_string(
        envelope["key_id"], "authenticated review receipt.key_id"
    )
    reporter.expect_int(
        envelope["key_epoch"],
        "authenticated review receipt.key_epoch",
        1,
    )
    reporter.expect_string(
        envelope["purpose"], "authenticated review receipt.purpose"
    )
    reporter.expect_string(
        envelope["payload_b64"],
        "authenticated review receipt.payload_b64",
        allow_empty=True,
    )
    supplied_hmac = reporter.expect_string(
        envelope["hmac_sha256"],
        "authenticated review receipt.hmac_sha256",
    )
    if reporter.SHA256_RE.fullmatch(supplied_hmac) is None:
        raise reporter.PilotDataError(
            "authenticated review receipt HMAC must be lowercase SHA-256"
        )
    scope = {
        "repository": repository,
        "pull_request": pull_request,
        "candidate_sha": candidate_sha,
        "key_id": trusted_key_id,
        "key_epoch": trusted_key_epoch,
        "purpose": RECEIPT_PURPOSE,
    }
    for field, expected in scope.items():
        if envelope[field] != expected:
            raise reporter.PilotDataError(
                f"authenticated review receipt {field} is outside trusted scope"
            )
    nonce = reporter.expect_string(
        envelope["nonce"], "authenticated review receipt.nonce"
    )
    if NONCE_RE.fullmatch(nonce) is None:
        raise reporter.PilotDataError(
            "authenticated review receipt nonce is malformed"
        )
    issued = reporter.parse_time(
        envelope["issued_at"], "authenticated review receipt.issued_at"
    )
    expires = reporter.parse_time(
        envelope["expires_at"], "authenticated review receipt.expires_at"
    )
    assert issued is not None and expires is not None
    if current_time.tzinfo is None:
        raise reporter.PilotDataError(
            "trusted current time must be timezone-aware"
        )
    current_time = current_time.astimezone(timezone.utc)
    if expires <= issued:
        raise reporter.PilotDataError(
            "authenticated review receipt has an invalid lifetime"
        )
    if (expires - issued).total_seconds() > RECEIPT_MAX_LIFETIME_SECONDS:
        raise reporter.PilotDataError(
            "authenticated review receipt lifetime exceeds maximum"
        )
    if current_time < issued or current_time >= expires:
        raise reporter.PilotDataError(
            "authenticated review receipt is stale or not yet valid"
        )
    if not isinstance(trusted_key, bytes) or len(trusted_key) < 32:
        raise reporter.PilotDataError(
            "receipt trust key must contain at least 32 bytes"
        )
    signed = {
        key: value
        for key, value in envelope.items()
        if key != "hmac_sha256"
    }
    expected_hmac = hmac.new(
        trusted_key,
        RECEIPT_DOMAIN + reporter.normalized_json(signed),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_hmac, expected_hmac):
        raise reporter.PilotDataError(
            "authenticated review receipt signature is invalid"
        )
    try:
        payload = base64.b64decode(
            envelope["payload_b64"], validate=True
        )
    except (ValueError, TypeError) as error:
        raise reporter.PilotDataError(
            "authenticated review receipt payload is not canonical base64"
        ) from error
    if base64.b64encode(payload).decode("ascii") != envelope["payload_b64"]:
        raise reporter.PilotDataError(
            "authenticated review receipt payload is not canonical base64"
        )
    if consume_nonce:
        if replay_store is None:
            raise reporter.PilotDataError(
                "authenticated review receipt requires replay authority"
            )
        if replay_store.is_symlink():
            raise reporter.PilotDataError(
                "authenticated review receipt replay store is unavailable"
            )
        replay_store = replay_store.resolve()
        if not replay_store.is_dir():
            raise reporter.PilotDataError(
                "authenticated review receipt replay store is unavailable"
            )
        replay_id = hashlib.sha256(
            reporter.normalized_json({**scope, "nonce": nonce})
        ).hexdigest()
        replay_path = replay_store / replay_id
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(replay_path, flags, 0o600)
        except FileExistsError as error:
            raise reporter.PilotDataError(
                "authenticated review receipt nonce was already consumed"
            ) from error
        try:
            os.write(descriptor, hashlib.sha256(receipt_bytes).hexdigest().encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return payload


def _git_text(repository_root: Path, *arguments: str) -> str:
    return reporter.run_git(repository_root, *arguments).decode("utf-8").strip()


def run_base_pinned_checker(
    repository_root: Path,
    *,
    repository: str,
    pull_request: int,
    base_sha: str,
    candidate_sha: str,
    github_finding_ids: list[str],
    review_report_bytes: bytes,
    clock: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    root = reporter.validate_repository_root(repository_root)
    base_sha = reporter.expect_sha(base_sha, "base checker base SHA")
    candidate_sha = reporter.expect_sha(
        candidate_sha, "base checker candidate SHA"
    )
    head = _git_text(root, "rev-parse", "--verify", "HEAD^{commit}")
    if head != candidate_sha:
        raise reporter.PilotDataError(
            "base checker candidate does not match actual Git HEAD"
        )
    pre_status = reporter.run_git(root, "status", "--porcelain")
    if pre_status:
        raise reporter.PilotDataError(
            "base checker requires a clean candidate worktree"
        )
    try:
        reporter.run_git(
            root, "merge-base", "--is-ancestor", base_sha, candidate_sha
        )
    except reporter.PilotDataError as error:
        raise reporter.PilotDataError(
            "base checker base is not an ancestor of candidate"
        ) from error
    base_tree = _git_text(root, "rev-parse", f"{base_sha}^{{tree}}")
    candidate_tree = _git_text(
        root, "rev-parse", f"{candidate_sha}^{{tree}}"
    )
    checker_blob = _git_text(
        root, "rev-parse", f"{base_sha}:{BASE_CHECKER_PATH}"
    )
    checker_source = reporter.run_git(
        root, "show", f"{base_sha}:{BASE_CHECKER_PATH}"
    )
    changed_raw = reporter.run_git(
        root, "diff", "--name-only", "-z", f"{base_sha}...{candidate_sha}"
    )
    changed_files = sorted(
        path.decode("utf-8")
        for path in changed_raw.split(b"\0")
        if path
    )
    if not changed_files:
        raise reporter.PilotDataError(
            "base checker candidate has no changed files"
        )
    try:
        review_report = reporter.expect_object(
            reporter.parse_json(
                review_report_bytes.decode("utf-8"),
                "independent review report",
            ),
            "independent review report",
        )
    except UnicodeDecodeError as error:
        raise reporter.PilotDataError(
            "independent review report is not UTF-8"
        ) from error
    checker_input = {
        "schema_version": 1,
        "repository": repository,
        "pull_request": pull_request,
        "base_sha": base_sha,
        "base_tree": base_tree,
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "changed_files": changed_files,
        "github_finding_ids": sorted(github_finding_ids),
        "review_report": review_report,
    }
    artifact_root = root / "build" / "test-artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    resolved_artifact_root = artifact_root.resolve()
    if (
        os.path.commonpath((str(root), str(resolved_artifact_root)))
        != str(root)
        or artifact_root.is_symlink()
    ):
        raise reporter.PilotDataError(
            "base checker artifact root escapes repository"
        )
    sandbox = resolved_artifact_root / (
        f"review-base-check-{os.getpid()}-{candidate_sha[:12]}"
    )
    if sandbox.exists():
        raise reporter.PilotDataError(
            "base checker sandbox already exists"
        )
    sandbox.mkdir(parents=True, mode=0o700)
    checker_path = sandbox / "review_base_checker.py"
    input_path = sandbox / "checker-input.json"
    checker_path.write_bytes(checker_source)
    input_path.write_bytes(reporter.normalized_json(checker_input))
    checker_path.chmod(0o444)
    input_path.chmod(0o444)
    sandbox.chmod(0o555)
    command = (
        "/usr/bin/python3",
        "-I",
        str(checker_path),
        "--input",
        str(input_path),
    )
    environment = {
        "HOME": str(sandbox),
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
    }
    started = clock()
    try:
        completed = subprocess.run(
            command,
            cwd=sandbox,
            env=environment,
            check=False,
            capture_output=True,
            timeout=60,
        )
        finished = clock()
        post_status = reporter.run_git(root, "status", "--porcelain")
        sandbox_read_only = (
            checker_path.stat().st_mode & 0o222 == 0
            and input_path.stat().st_mode & 0o222 == 0
            and sandbox.stat().st_mode & 0o222 == 0
        )
    finally:
        sandbox.chmod(0o700)
        shutil.rmtree(sandbox)
    output = completed.stdout + b"\0stderr\0" + completed.stderr
    raw = {
        "id": f"BASE-CHECK:{base_sha}:{candidate_sha}",
        "check_id": "base-pinned-independent-review",
        "base_sha": base_sha,
        "base_tree": base_tree,
        "candidate_sha": candidate_sha,
        "candidate_tree": candidate_tree,
        "checker_path": BASE_CHECKER_PATH,
        "checker_blob_oid": checker_blob,
        "argv": list(BASE_CHECKER_ARGV),
        "changed_files": changed_files,
        "github_finding_ids": sorted(github_finding_ids),
        "review_report_sha256": hashlib.sha256(
            review_report_bytes
        ).hexdigest(),
        "read_only": sandbox_read_only,
        "pre_clean": pre_status == b"",
        "post_clean": post_status == b"",
        "started_at": _format_time(started),
        "completed_at": _format_time(finished),
        "exit_code": completed.returncode,
        "result": "pass" if completed.returncode == 0 else "fail",
        "output_sha256": hashlib.sha256(output).hexdigest(),
    }
    raw["seal"] = receipt_seal(raw)
    return raw


class GhApiAdapter:
    """Closed read-only adapter for the GitHub GraphQL API."""

    def fetch(self, repository: str, pull_request: int) -> dict[str, Any]:
        owner, separator, name = repository.partition("/")
        if not separator or not owner or not name or "/" in name:
            raise reporter.PilotDataError(
                "repository must use owner/name form"
            )
        command = (
            GH,
            "api",
            "graphql",
            "-f",
            f"query={GRAPHQL_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pull_request}",
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"GH_HOST", "GH_TOKEN", "GITHUB_TOKEN", "HOME"}
        }
        environment.update({"LC_ALL": "C", "PATH": "/usr/bin:/bin"})
        try:
            completed = subprocess.run(
                command,
                env=environment,
                check=False,
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise reporter.PilotDataError(
                f"cannot collect GitHub review evidence: {error}"
            ) from error
        if completed.returncode != 0:
            detail = completed.stderr.decode(
                "utf-8", errors="replace"
            ).strip()
            raise reporter.PilotDataError(
                "GitHub review evidence collection failed"
                + (f": {detail}" if detail else "")
            )
        return reporter.parse_json(
            completed.stdout.decode("utf-8"),
            "gh api graphql review evidence",
        )


def _expect_page_complete(connection: Any, label: str) -> list[Any]:
    connection = reporter.expect_object(connection, label)
    reporter.expect_keys(connection, label, ("pageInfo", "nodes"))
    page_info = reporter.expect_object(
        connection["pageInfo"], f"{label}.pageInfo"
    )
    reporter.expect_keys(page_info, f"{label}.pageInfo", ("hasNextPage",))
    if reporter.expect_bool(
        page_info["hasNextPage"], f"{label}.pageInfo.hasNextPage"
    ):
        raise reporter.PilotDataError(
            f"{label} exceeds the bounded complete GitHub collection"
        )
    return reporter.expect_list(connection["nodes"], f"{label}.nodes")


def _actor(raw: Any, kind: str, label: str) -> dict[str, str]:
    raw = reporter.expect_object(raw, label)
    reporter.expect_keys(raw, label, ("id", "login"))
    return {
        "id": reporter.expect_string(raw["id"], f"{label}.id"),
        "login": reporter.expect_string(raw["login"], f"{label}.login"),
        "kind": kind,
    }


def _collect_actors(records: list[dict[str, str]]) -> list[dict[str, str]]:
    actors = {}
    for actor in records:
        existing = actors.get(actor["id"])
        if existing is not None and existing != actor:
            raise reporter.PilotDataError(
                f"GitHub actor ID {actor['id']!r} changed identity"
            )
        actors[actor["id"]] = actor
    return [actors[actor_id] for actor_id in sorted(actors)]


def _parse_disposition_comments(
    comments: list[Any],
    actors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    prefix = "workflow-review-family-disposition:v1 "
    result = []
    for index, raw in enumerate(comments):
        label = f"GitHub pull-request comment[{index}]"
        comment = reporter.expect_object(raw, label)
        reporter.expect_keys(
            comment, label, ("id", "createdAt", "body", "author")
        )
        author = _actor(comment["author"], "user", f"{label}.author")
        actors.append(author)
        body = reporter.expect_string(
            comment["body"], f"{label}.body", allow_empty=True
        )
        if not body.startswith(prefix):
            continue
        payload = reporter.expect_object(
            reporter.parse_json(body[len(prefix) :], f"{label} disposition"),
            f"{label} disposition",
        )
        reporter.expect_keys(
            payload,
            f"{label} disposition",
            ("held_round", "candidate_sha", "action"),
        )
        result.append(
            {
                "node_id": reporter.expect_string(
                    comment["id"], f"{label}.id"
                ),
                "held_round": reporter.expect_int(
                    payload["held_round"],
                    f"{label}.held_round",
                    3,
                ),
                "candidate_sha": reporter.expect_sha(
                    payload["candidate_sha"],
                    f"{label}.candidate_sha",
                ),
                "actor_id": author["id"],
                "action": payload["action"],
                "occurred_at": comment["createdAt"],
            }
        )
    return result


def _build_result_manifest(
    contract: dict[str, Any],
    finding_families: dict[str, str],
    candidate_sha: str,
) -> list[dict[str, Any]]:
    result = {}
    for row in contract["behavior_rows"]:
        for evidence_class, result_ids in row["evidence_result_ids"].items():
            for result_id in result_ids:
                result[result_id] = {
                    "id": result_id,
                    "candidate_sha": candidate_sha,
                    "row_id": row["id"],
                    "evidence_class": evidence_class,
                    "family": None,
                    "member": None,
                    "assertion_id": f"{row['id']}:{evidence_class}",
                }
    for sweep in contract["family_sweeps"]:
        finding_id = sweep["finding_id"]
        if finding_id not in finding_families:
            raise reporter.PilotDataError(
                f"live family sweep references unknown finding {finding_id!r}"
            )
        family = finding_families[finding_id]
        for sibling in sweep["siblings"]:
            for result_id in sibling["evidence_result_ids"]:
                if result_id in result:
                    raise reporter.PilotDataError(
                        f"live result ID {result_id!r} is reused"
                    )
                result[result_id] = {
                    "id": result_id,
                    "candidate_sha": candidate_sha,
                    "row_id": "sibling-family-expansion",
                    "evidence_class": "adversarial",
                    "family": family,
                    "member": sibling["member"],
                    "assertion_id": f"sibling:{family}:{sibling['member']}",
                }
    return [result[result_id] for result_id in sorted(result)]


def collect_live_evidence_bytes(
    raw_contract: Any,
    repository_root: Path,
    expected_candidate: str,
    review_report: dict[str, Any],
    execution_receipt: dict[str, Any] | None,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> bytes:
    from scripts.workflow_pilot import review_family

    contract = review_family.validate_contract(raw_contract)
    root = reporter.validate_repository_root(repository_root)
    expected_candidate = reporter.expect_sha(
        expected_candidate, "live expected candidate"
    )
    adapter = GhApiAdapter()
    payload = reporter.expect_object(
        adapter.fetch(contract["repository"], contract["pull_request"]),
        "GitHub GraphQL response",
    )
    reporter.expect_keys(payload, "GitHub GraphQL response", ("data",))
    data = reporter.expect_object(payload["data"], "GitHub GraphQL data")
    reporter.expect_keys(data, "GitHub GraphQL data", ("viewer", "repository"))
    viewer = _actor(data["viewer"], "user", "GitHub viewer")
    repository = reporter.expect_object(
        data["repository"], "GitHub repository"
    )
    reporter.expect_keys(
        repository,
        "GitHub repository",
        ("viewerPermission", "owner", "pullRequest"),
    )
    if repository["viewerPermission"] != "READ":
        raise reporter.PilotDataError(
            "live adversarial reviewer must have exact GitHub READ permission"
        )
    pr = reporter.expect_object(
        repository["pullRequest"], "GitHub pull request"
    )
    reporter.expect_keys(
        pr,
        "GitHub pull request",
        (
            "id",
            "number",
            "createdAt",
            "headRefOid",
            "author",
            "commits",
            "timelineItems",
            "reviews",
            "reviewThreads",
            "comments",
        ),
    )
    if pr["number"] != contract["pull_request"]:
        raise reporter.PilotDataError(
            "live GitHub pull-request number does not match contract"
        )
    head = reporter.expect_sha(pr["headRefOid"], "GitHub pull request head")
    if head != expected_candidate:
        raise reporter.PilotDataError(
            "live GitHub pull-request head does not match expected candidate"
        )
    author = _actor(pr["author"], "user", "GitHub pull-request author")
    owner = _actor(repository["owner"], "user", "GitHub repository owner")
    actor_records = [viewer, author, owner]

    commit_nodes = _expect_page_complete(
        pr["commits"], "GitHub pull-request commits"
    )
    advances = []
    for index, raw_node in enumerate(commit_nodes):
        label = f"GitHub pull-request commit[{index}]"
        node = reporter.expect_object(raw_node, label)
        reporter.expect_keys(node, label, ("commit",))
        commit = reporter.expect_object(node["commit"], f"{label}.commit")
        reporter.expect_keys(
            commit,
            f"{label}.commit",
            ("id", "oid", "pushedDate", "committedDate"),
        )
        commit_sha = reporter.expect_sha(
            commit["oid"], f"{label}.commit.oid"
        )
        committed_at = reporter.parse_time(
            commit["committedDate"], f"{label}.commit.committedDate"
        )
        actual_commit = reporter._load_git_commit_objects(root, (commit_sha,))[
            commit_sha
        ]
        if committed_at != actual_commit["committed_at"]:
            raise reporter.PilotDataError(
                f"{label}.commit.committedDate does not match Git authority"
            )
        pushed_at = commit["pushedDate"]
        reporter.parse_time(pushed_at, f"{label}.commit.pushedDate")
        advances.append(
            {
                "node_id": reporter.expect_string(
                    commit["id"], f"{label}.commit.id"
                ),
                "candidate_sha": commit_sha,
                "pushed_at": pushed_at,
                "kind": "commit-push",
            }
        )
    force_push_nodes = _expect_page_complete(
        pr["timelineItems"], "GitHub pull-request force-push events"
    )
    for index, raw_event in enumerate(force_push_nodes):
        label = f"GitHub force-push event[{index}]"
        event = reporter.expect_object(raw_event, label)
        reporter.expect_keys(
            event, label, ("id", "createdAt", "afterCommit")
        )
        after_commit = reporter.expect_object(
            event["afterCommit"], f"{label}.afterCommit"
        )
        reporter.expect_keys(after_commit, f"{label}.afterCommit", ("oid",))
        advances.append(
            {
                "node_id": reporter.expect_string(
                    event["id"], f"{label}.id"
                ),
                "candidate_sha": reporter.expect_sha(
                    after_commit["oid"], f"{label}.afterCommit.oid"
                ),
                "pushed_at": event["createdAt"],
                "kind": "force-push",
            }
        )
    advances.sort(key=lambda advance: (advance["pushed_at"], advance["node_id"]))
    if not advances or advances[-1]["candidate_sha"] != head:
        raise reporter.PilotDataError(
            "live GitHub commit history does not terminate at current head"
        )

    review_nodes = _expect_page_complete(
        pr["reviews"], "GitHub pull-request reviews"
    )
    remote_reviews = []
    finding_records = []
    for index, raw_review in enumerate(review_nodes):
        label = f"GitHub pull-request review[{index}]"
        review = reporter.expect_object(raw_review, label)
        reporter.expect_keys(
            review,
            label,
            (
                "id",
                "databaseId",
                "state",
                "submittedAt",
                "body",
                "commit",
                "author",
                "comments",
            ),
        )
        review_actor = _actor(
            review["author"], "bot", f"{label}.author"
        )
        actor_records.append(review_actor)
        if review_actor["id"] == viewer["id"]:
            raise reporter.PilotDataError(
                "live adversarial reviewer performed a GitHub review action"
            )
        if (
            review_family.normalize_actor_login(review_actor["login"])
            != review_family.COPILOT_ACTOR
        ):
            continue
        comments = _expect_page_complete(
            review["comments"], f"{label}.comments"
        )
        state = reporter.expect_enum(
            review["state"],
            {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"},
            f"{label}.state",
        )
        body = reporter.expect_string(
            review["body"], f"{label}.body", allow_empty=True
        )
        body_has_findings = body.strip() not in {"", "No issues found."}
        review_commit = reporter.expect_object(
            review["commit"], f"{label}.commit"
        )
        reporter.expect_keys(review_commit, f"{label}.commit", ("oid",))
        review_candidate = reporter.expect_sha(
            review_commit["oid"], f"{label}.commit.oid"
        )
        finding_ids = []
        for comment_index, raw_comment in enumerate(comments):
            comment_label = f"{label}.comments[{comment_index}]"
            comment = reporter.expect_object(raw_comment, comment_label)
            reporter.expect_keys(
                comment, comment_label, ("id", "createdAt")
            )
            finding_id = reporter.expect_string(
                comment["id"], f"{comment_label}.id"
            )
            finding_ids.append(finding_id)
            finding_records.append(
                {
                    "node_id": finding_id,
                    "review_id": review["id"],
                    "candidate_sha": review_candidate,
                    "created_at": comment["createdAt"],
                    "family": None,
                }
            )
        remote_reviews.append(
            {
                "id": reporter.expect_int(
                    review["databaseId"], f"{label}.databaseId", 1
                ),
                "node_id": reporter.expect_string(
                    review["id"], f"{label}.id"
                ),
                "round": len(remote_reviews) + 1,
                "reviewer_actor_id": review_actor["id"],
                "candidate_sha": review_candidate,
                "submitted_at": review["submittedAt"],
                "state": state,
                "body": body,
                "body_has_findings": body_has_findings,
                "outcome": (
                    "changes-requested"
                    if state == "CHANGES_REQUESTED"
                    or finding_ids
                    or body_has_findings
                    else "clean"
                ),
                "finding_ids": finding_ids,
            }
        )
        if state == "APPROVED" and finding_ids:
            raise reporter.PilotDataError(
                f"{label} approved while carrying findings"
            )

    thread_nodes = _expect_page_complete(
        pr["reviewThreads"], "GitHub pull-request review threads"
    )
    threads = []
    finding_to_thread = {}
    for index, raw_thread in enumerate(thread_nodes):
        label = f"GitHub review thread[{index}]"
        thread = reporter.expect_object(raw_thread, label)
        reporter.expect_keys(
            thread, label, ("id", "isResolved", "comments")
        )
        comments = _expect_page_complete(
            thread["comments"], f"{label}.comments"
        )
        if not comments:
            raise reporter.PilotDataError(
                f"{label} has no finding comment"
            )
        first = reporter.expect_object(
            comments[0], f"{label}.comments[0]"
        )
        reporter.expect_keys(
            first,
            f"{label}.comments[0]",
            ("id", "createdAt", "pullRequestReview"),
        )
        finding_id = reporter.expect_string(
            first["id"], f"{label}.comments[0].id"
        )
        finding_to_thread[finding_id] = thread["id"]
        threads.append(
            {
                "node_id": reporter.expect_string(
                    thread["id"], f"{label}.id"
                ),
                "finding_id": finding_id,
                "is_resolved": reporter.expect_bool(
                    thread["isResolved"], f"{label}.isResolved"
                ),
            }
        )

    finding_families = {}
    for sweep in contract["family_sweeps"]:
        members = {sibling["member"] for sibling in sweep["siblings"]}
        matches = [
            family
            for family, family_members in review_family.FAMILY_MEMBERS.items()
            if members == set(family_members)
        ]
        if len(matches) != 1:
            raise reporter.PilotDataError(
                f"live sweep {sweep['finding_id']!r} does not identify "
                "one closed family"
            )
        finding_families[sweep["finding_id"]] = matches[0]
    for finding in finding_records:
        if finding["node_id"] not in finding_families:
            raise reporter.PilotDataError(
                f"live finding {finding['node_id']!r} has no contract family"
            )
        finding["family"] = finding_families[finding["node_id"]]
        if finding["node_id"] not in finding_to_thread:
            raise reporter.PilotDataError(
                f"live finding {finding['node_id']!r} has no review thread"
            )

    comment_nodes = _expect_page_complete(
        pr["comments"], "GitHub pull-request comments"
    )
    for index, raw_comment in enumerate(comment_nodes):
        comment = reporter.expect_object(
            raw_comment, f"GitHub pull-request comment[{index}]"
        )
        author = reporter.expect_object(
            comment.get("author"),
            f"GitHub pull-request comment[{index}].author",
        )
        if author.get("id") == viewer["id"]:
            raise reporter.PilotDataError(
                "live adversarial reviewer performed a GitHub comment action"
            )
    dispositions = _parse_disposition_comments(comment_nodes, actor_records)
    actor_records.append(
        {
            "id": review_report["reviewer_actor_id"],
            "login": review_report["reviewer_login"],
            "kind": "user",
        }
    )
    actors = _collect_actors(actor_records)

    pre_reviews = []
    if contract["pre_review_required"]:
        pre_reviews.append(
            {
                "id": review_report["report_id"],
                "owner_actor_id": review_report["reviewer_actor_id"],
                "candidate_sha": head,
                "started_at": review_report["started_at"],
                "completed_at": review_report["completed_at"],
                "permissions": review_report["permissions"],
                "actions": [
                    {
                        "id": f"{review_report['report_id']}:READ",
                        "kind": review_report["actions"][0],
                        "occurred_at": review_report["started_at"],
                    },
                    {
                        "id": f"{review_report['report_id']}:REPORT",
                        "kind": review_report["actions"][1],
                        "occurred_at": review_report["completed_at"],
                    },
                ],
                "finding_ids": [],
                "reviewed_files": review_report["reviewed_files"],
            }
        )
    captured_at = _format_time(clock())
    raw_evidence = {
        "schema_version": review_family.SCHEMA_VERSION,
        "repository": contract["repository"],
        "source": {"kind": "live-gh-api", "complete": True},
        "captured_at": captured_at,
        "candidate": {"sha": head},
        "pull_request": {
            "number": pr["number"],
            "node_id": pr["id"],
            "created_at": pr["createdAt"],
            "author_actor_id": author["id"],
        },
        "result_source_path": review_family.RESULT_SOURCE_PATH,
        "actors": actors,
        "trusted_disposition_actor_ids": [owner["id"]],
        "pre_reviews": pre_reviews,
        "remote_reviews": remote_reviews,
        "findings": finding_records,
        "threads": threads,
        "candidate_advances": advances,
        "architecture_dispositions": dispositions,
        "execution_receipts": (
            [execution_receipt] if execution_receipt is not None else []
        ),
        "result_manifest": _build_result_manifest(
            contract, finding_families, head
        ),
    }
    return reporter.normalized_json(raw_evidence)


def _live_state_digest(evidence_bytes: bytes) -> str:
    evidence = reporter.expect_object(
        reporter.parse_json(
            evidence_bytes.decode("utf-8"), "live state revalidation"
        ),
        "live state revalidation",
    )
    payload = {
        "repository": evidence["repository"],
        "candidate": evidence["candidate"],
        "pull_request": evidence["pull_request"],
        "actors": evidence["actors"],
        "trusted_disposition_actor_ids": evidence[
            "trusted_disposition_actor_ids"
        ],
        "remote_reviews": evidence["remote_reviews"],
        "findings": evidence["findings"],
        "threads": evidence["threads"],
        "candidate_advances": evidence["candidate_advances"],
        "architecture_dispositions": evidence[
            "architecture_dispositions"
        ],
    }
    return hashlib.sha256(reporter.normalized_json(payload)).hexdigest()


def _github_finding_ids(evidence_bytes: bytes) -> list[str]:
    evidence = reporter.expect_object(
        reporter.parse_json(
            evidence_bytes.decode("utf-8"), "live finding identities"
        ),
        "live finding identities",
    )
    finding_ids = [finding["node_id"] for finding in evidence["findings"]]
    finding_ids.extend(
        review["node_id"]
        for review in evidence["remote_reviews"]
        if review["body_has_findings"]
    )
    return sorted(finding_ids)


def _run_isolated_live_gate(
    *,
    raw_contract: Any,
    repository_root: Path,
    expected_candidate: str,
    base_sha: str,
    review_receipt_bytes: bytes,
    replay_store: Path,
    trusted_key_id: str,
    trusted_key_epoch: int,
    trusted_key: bytes,
    current_time: datetime,
    clock: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    from scripts.workflow_pilot import review_family

    contract = review_family.validate_contract(raw_contract)
    report_bytes = verify_signed_receipt_bytes(
        review_receipt_bytes,
        repository=contract["repository"],
        pull_request=contract["pull_request"],
        candidate_sha=expected_candidate,
        trusted_key_id=trusted_key_id,
        trusted_key_epoch=trusted_key_epoch,
        trusted_key=trusted_key,
        current_time=current_time,
        replay_store=replay_store,
        consume_nonce=True,
    )
    report_bytes_reverified = verify_signed_receipt_bytes(
        review_receipt_bytes,
        repository=contract["repository"],
        pull_request=contract["pull_request"],
        candidate_sha=expected_candidate,
        trusted_key_id=trusted_key_id,
        trusted_key_epoch=trusted_key_epoch,
        trusted_key=trusted_key,
        current_time=current_time,
        replay_store=None,
        consume_nonce=False,
    )
    if report_bytes_reverified != report_bytes:
        raise reporter.PilotDataError(
            "authenticated review report bytes changed after verification"
        )
    review_report = reporter.expect_object(
        reporter.parse_json(
            report_bytes.decode("utf-8"), "authenticated independent review"
        ),
        "authenticated independent review",
    )
    first_evidence = collect_live_evidence_bytes(
        raw_contract,
        repository_root,
        expected_candidate,
        review_report,
        None,
        clock=clock,
    )
    finding_ids = _github_finding_ids(first_evidence)
    checker_report_bytes = verify_signed_receipt_bytes(
        review_receipt_bytes,
        repository=contract["repository"],
        pull_request=contract["pull_request"],
        candidate_sha=expected_candidate,
        trusted_key_id=trusted_key_id,
        trusted_key_epoch=trusted_key_epoch,
        trusted_key=trusted_key,
        current_time=current_time,
        replay_store=None,
        consume_nonce=False,
    )
    execution_receipt = run_base_pinned_checker(
        repository_root,
        repository=contract["repository"],
        pull_request=contract["pull_request"],
        base_sha=base_sha,
        candidate_sha=expected_candidate,
        github_finding_ids=finding_ids,
        review_report_bytes=checker_report_bytes,
        clock=clock,
    )
    second_report_bytes = verify_signed_receipt_bytes(
        review_receipt_bytes,
        repository=contract["repository"],
        pull_request=contract["pull_request"],
        candidate_sha=expected_candidate,
        trusted_key_id=trusted_key_id,
        trusted_key_epoch=trusted_key_epoch,
        trusted_key=trusted_key,
        current_time=current_time,
        replay_store=None,
        consume_nonce=False,
    )
    second_review_report = reporter.expect_object(
        reporter.parse_json(
            second_report_bytes.decode("utf-8"),
            "reverified independent review",
        ),
        "reverified independent review",
    )
    second_evidence = collect_live_evidence_bytes(
        raw_contract,
        repository_root,
        expected_candidate,
        second_review_report,
        execution_receipt,
        clock=clock,
    )
    if _live_state_digest(first_evidence) != _live_state_digest(second_evidence):
        raise reporter.PilotDataError(
            "GitHub head/review/thread state changed during gate evaluation"
        )
    final_report_bytes = verify_signed_receipt_bytes(
        review_receipt_bytes,
        repository=contract["repository"],
        pull_request=contract["pull_request"],
        candidate_sha=expected_candidate,
        trusted_key_id=trusted_key_id,
        trusted_key_epoch=trusted_key_epoch,
        trusted_key=trusted_key,
        current_time=current_time,
        replay_store=None,
        consume_nonce=False,
    )
    if final_report_bytes != report_bytes:
        raise reporter.PilotDataError(
            "authenticated review report bytes changed before consumption"
        )
    result = review_family.build_report(
        raw_contract,
        second_evidence,
        repository_root,
        expected_candidate,
    )
    result["provenance"]["isolated_gate_evidence_complete"] = True
    result["provenance"]["base_pinned_checker"] = True
    return result


def parse_args(argv: list[str] | None = None) -> Any:
    parser = argparse.ArgumentParser(
        description="Run isolated read-only live sibling-review gate."
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--expected-candidate", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--review-receipt", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not sys.flags.isolated:
            raise reporter.PilotDataError(
                "live gate authority requires isolated Python startup"
            )
        key_id = os.environ.get("WORKFLOW_REVIEW_RECEIPT_KEY_ID")
        key_epoch_text = os.environ.get("WORKFLOW_REVIEW_RECEIPT_KEY_EPOCH")
        key = os.environ.get("WORKFLOW_REVIEW_RECEIPT_HMAC_KEY")
        replay_store = os.environ.get("WORKFLOW_REVIEW_REPLAY_STORE")
        if not key_id or not key_epoch_text or not key or not replay_store:
            raise reporter.PilotDataError(
                "isolated live gate requires external receipt key ID, epoch, "
                "HMAC key, and replay store"
            )
        try:
            key_epoch = int(key_epoch_text)
        except ValueError as error:
            raise reporter.PilotDataError(
                "receipt key epoch must be an integer"
            ) from error
        result = _run_isolated_live_gate(
            raw_contract=reporter.load_json(args.contract),
            repository_root=args.repository_root,
            expected_candidate=args.expected_candidate,
            base_sha=args.base_sha,
            review_receipt_bytes=args.review_receipt.read_bytes(),
            replay_store=Path(replay_store),
            trusted_key_id=key_id,
            trusted_key_epoch=key_epoch,
            trusted_key=key.encode("utf-8"),
            current_time=_utc_now(),
        )
        result["provenance"] = {
            "source": "isolated-live-gate",
            "authoritative": True,
            "live_authoritative": True,
            "authenticated_receipt": True,
            "base_pinned_checker": True,
            "execution_receipt_seals": result["provenance"][
                "execution_receipt_seals"
            ],
        }
        result["gates"] = {
            **result["gates"],
            "push_allowed": result["structural_eligibility"]["push"],
            "trusted_push_allowed": result["structural_eligibility"]["push"],
            "merge_allowed": result["structural_eligibility"]["merge"],
        }
    except (OSError, reporter.PilotDataError) as error:
        print(f"isolated review gate error: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(reporter.normalized_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
