#!/usr/bin/env python3
"""Read-only GitHub collection and executable review-check receipts."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.workflow_pilot import reporter


GH = "/usr/bin/gh"
CHECK_RECEIPT_DOMAIN = b"workflow-review-check-receipt-v1\0"
EVIDENCE_RECEIPT_DOMAIN = b"workflow-review-authenticated-evidence-v1\0"
REGISTERED_CHECK_COMMANDS = {
    "review-family-suite": (
        "/usr/bin/python3",
        "-S",
        "-m",
        "unittest",
        "scripts.workflow_pilot.tests.test_review_family",
        "-q",
    ),
    "review-family-negative-control": ("/usr/bin/false",),
}
RESULT_CHECK_IDS = {"review-family-suite"}
LIVE_REVIEWED_FILES = (
    "docs/workflow-pilot.md",
    "scripts/workflow_pilot/review_family.py",
    "scripts/workflow_pilot/tests/test_review_family.py",
)
_TRUST_TOKEN = object()


GRAPHQL_QUERY = r"""
query ReviewFamilyEvidence($owner: String!, $name: String!, $number: Int!) {
  viewer { id login }
  repository(owner: $owner, name: $name) {
    viewerPermission
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


class TrustedCheckReceipt:
    __slots__ = ("raw", "_token")

    def __init__(self, raw: dict[str, Any], token: object):
        if token is not _TRUST_TOKEN:
            raise TypeError("trusted check receipts are factory-only")
        self.raw = raw
        self._token = token


class TrustedEvidence:
    __slots__ = ("raw", "trusted_receipt_seals", "_token")

    def __init__(
        self,
        raw: dict[str, Any],
        receipt_seals: set[str],
        token: object,
    ):
        if token is not _TRUST_TOKEN:
            raise TypeError("trusted evidence is collector-only")
        self.raw = raw
        self.trusted_receipt_seals = frozenset(receipt_seals)
        self._token = token


def unwrap_evidence(value: Any) -> tuple[Any, bool, frozenset[str]]:
    if isinstance(value, TrustedEvidence) and value._token is _TRUST_TOKEN:
        return value.raw, True, value.trusted_receipt_seals
    return value, False, frozenset()


def make_authenticated_evidence_receipt(
    evidence: dict[str, Any],
    key_id: str,
    key: bytes,
) -> dict[str, Any]:
    reporter.expect_string(key_id, "evidence receipt key ID")
    if not isinstance(key, bytes) or len(key) < 32:
        raise reporter.PilotDataError(
            "evidence receipt trust key must contain at least 32 bytes"
        )
    payload = {
        "schema_version": 1,
        "key_id": key_id,
        "evidence": evidence,
    }
    payload["hmac_sha256"] = hmac.new(
        key,
        EVIDENCE_RECEIPT_DOMAIN + reporter.normalized_json(payload),
        hashlib.sha256,
    ).hexdigest()
    return payload


def authenticate_evidence_receipt(
    raw_receipt: Any,
    trusted_key_id: str,
    trusted_key: bytes,
) -> TrustedEvidence:
    receipt = reporter.expect_object(raw_receipt, "authenticated evidence receipt")
    reporter.expect_keys(
        receipt,
        "authenticated evidence receipt",
        ("schema_version", "key_id", "evidence", "hmac_sha256"),
    )
    version = reporter.expect_int(
        receipt["schema_version"],
        "authenticated evidence receipt.schema_version",
        1,
    )
    if version != 1:
        raise reporter.PilotDataError(
            "authenticated evidence receipt.schema_version must be 1"
        )
    if receipt["key_id"] != trusted_key_id:
        raise reporter.PilotDataError(
            "authenticated evidence receipt key ID is not trusted"
        )
    if not isinstance(trusted_key, bytes) or len(trusted_key) < 32:
        raise reporter.PilotDataError(
            "evidence receipt trust key must contain at least 32 bytes"
        )
    supplied = reporter.expect_string(
        receipt["hmac_sha256"],
        "authenticated evidence receipt.hmac_sha256",
    )
    payload = {
        "schema_version": version,
        "key_id": receipt["key_id"],
        "evidence": receipt["evidence"],
    }
    expected = hmac.new(
        trusted_key,
        EVIDENCE_RECEIPT_DOMAIN + reporter.normalized_json(payload),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise reporter.PilotDataError(
            "authenticated evidence receipt signature is invalid"
        )
    evidence = reporter.expect_object(
        receipt["evidence"], "authenticated evidence receipt.evidence"
    )
    execution_receipts = reporter.expect_list(
        evidence.get("execution_receipts"),
        "authenticated evidence receipt.evidence.execution_receipts",
    )
    seals = {
        reporter.expect_string(
            item.get("seal"),
            "authenticated execution receipt seal",
        )
        for item in execution_receipts
        if isinstance(item, dict)
    }
    return TrustedEvidence(evidence, seals, _TRUST_TOKEN)


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


def run_registered_check(
    repository_root: Path,
    expected_candidate: str,
    check_id: str,
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> TrustedCheckReceipt:
    root = reporter.validate_repository_root(repository_root)
    expected_candidate = reporter.expect_sha(
        expected_candidate, "registered check candidate"
    )
    if check_id not in REGISTERED_CHECK_COMMANDS:
        raise reporter.PilotDataError(
            f"registered check {check_id!r} is not allowlisted"
        )
    head = (
        reporter.run_git(root, "rev-parse", "--verify", "HEAD^{commit}")
        .decode("ascii")
        .strip()
    )
    if head != expected_candidate:
        raise reporter.PilotDataError(
            "registered check worktree HEAD does not match candidate"
        )
    if reporter.run_git(root, "status", "--porcelain").strip():
        raise reporter.PilotDataError(
            "registered check requires a clean exact-candidate worktree"
        )

    command = REGISTERED_CHECK_COMMANDS[check_id]
    environment = {
        "HOME": str(root),
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(root),
    }
    started = clock()
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise reporter.PilotDataError(
            f"registered check {check_id!r} could not execute: {error}"
        ) from error
    finished = clock()
    output = completed.stdout + b"\0stderr\0" + completed.stderr
    raw = {
        "id": f"CHECK:{check_id}:{expected_candidate}",
        "check_id": check_id,
        "candidate_sha": expected_candidate,
        "started_at": _format_time(started),
        "completed_at": _format_time(finished),
        "exit_code": completed.returncode,
        "result": "pass" if completed.returncode == 0 else "fail",
        "output_sha256": hashlib.sha256(output).hexdigest(),
    }
    raw["seal"] = receipt_seal(raw)
    return TrustedCheckReceipt(raw, _TRUST_TOKEN)


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


def collect_live_evidence(
    raw_contract: Any,
    repository_root: Path,
    expected_candidate: str,
    check_receipts: list[TrustedCheckReceipt],
    *,
    clock: Callable[[], datetime] = _utc_now,
) -> TrustedEvidence:
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
        repository, "GitHub repository", ("viewerPermission", "pullRequest")
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
    actor_records = [viewer, author]

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
                "outcome": (
                    "changes-requested" if finding_ids else "clean"
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
    actors = _collect_actors(actor_records)

    if not check_receipts:
        raise reporter.PilotDataError(
            "live collection requires executable check receipts"
        )
    raw_receipts = []
    trusted_seals = set()
    for receipt in check_receipts:
        if (
            not isinstance(receipt, TrustedCheckReceipt)
            or receipt._token is not _TRUST_TOKEN
        ):
            raise reporter.PilotDataError(
                "live collection received an untrusted check receipt"
            )
        raw_receipts.append(receipt.raw)
        trusted_seals.add(receipt.raw["seal"])
    primary = raw_receipts[0]
    pre_reviews = []
    if contract["pre_review_required"]:
        pre_reviews.append(
            {
                "id": f"PRE:{primary['seal'][:24]}",
                "owner_actor_id": viewer["id"],
                "candidate_sha": head,
                "started_at": primary["started_at"],
                "completed_at": primary["completed_at"],
                "permissions": ["contents:read"],
                "actions": [
                    {
                        "id": f"ACTION:READ:{primary['seal'][:16]}",
                        "kind": "read-candidate",
                        "occurred_at": primary["started_at"],
                    },
                    {
                        "id": f"ACTION:REPORT:{primary['seal'][:16]}",
                        "kind": "emit-local-report",
                        "occurred_at": primary["completed_at"],
                    },
                ],
                "finding_ids": [],
                "reviewed_files": list(LIVE_REVIEWED_FILES),
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
        },
        "result_source_path": review_family.RESULT_SOURCE_PATH,
        "actors": actors,
        "pre_reviews": pre_reviews,
        "remote_reviews": remote_reviews,
        "findings": finding_records,
        "threads": threads,
        "candidate_advances": advances,
        "architecture_dispositions": dispositions,
        "execution_receipts": raw_receipts,
        "result_manifest": _build_result_manifest(
            contract, finding_families, head
        ),
    }
    return TrustedEvidence(raw_evidence, trusted_seals, _TRUST_TOKEN)
