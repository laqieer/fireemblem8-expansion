"""Behavioral regressions for exact-candidate pull-request metadata edits."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scripts.workflow_pilot import candidate_evidence, metadata_event, pr_metadata


ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = Path("scripts/workflow_pilot/isolated_launcher.py")
REPOSITORY = "owner/repo"
REPOSITORY_ID = 1302699505
OWNER_ID = 77
PR_NUMBER = 199
HEAD = "1" * 40
BASE = "2" * 40
NEW_HEAD = "3" * 40
HEAD_REF = "feature/issue-199"
WORKFLOW_ID = 1234
PR_CREATED_AT = "2026-09-02T00:00:00Z"
_DEFAULT_METADATA_EVENT = object()
INTENT_DRIFTS = (
    "deleted", "unmarked", "nonce", "pre-version", "timestamp",
    "author-id", "author-login", "author-type", "site-admin", "association",
    "deleted-author",
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _metadata_sha256(title: str, body: str | None) -> str:
    return _sha256(
        json.dumps(
            {"body": body, "title": title},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _body_original(body: str, materialized_at: str) -> pr_metadata.BodyOriginal:
    return pr_metadata.BodyOriginal(
        "UCE_original",
        _sha256(json.dumps(body, ensure_ascii=False, separators=(",", ":"))),
        OWNER_ID,
        "owner",
        PR_CREATED_AT,
        materialized_at,
    )


def _metadata_version(
    *,
    title_event_id: str | None = "RTE_1",
    title_event_created_at: str | None = "2026-09-03T00:00:00Z",
    title_previous: str | None = "Older title",
    title_current: str | None = "Stable title",
    title_actor_id: int | None = OWNER_ID,
    title_actor_login: str | None = "owner",
    body_last_edited_at: str | None = "2026-09-04T00:00:00Z",
    body_editor_id: int | None = OWNER_ID,
    body_editor_login: str | None = "owner",
    body_edit_total_count: int | None = None,
    body_edit_id: str | None = None,
    body_edit_created_at: str | None = None,
    body_edit_edited_at: str | None = None,
    body_edit_updated_at: str | None = None,
    original_body: str = "prior body",
) -> pr_metadata.MetadataVersion:
    if body_last_edited_at is None:
        body_edit_total_count = 0
        body_edit_id = None
        body_edit_created_at = None
        body_edit_edited_at = None
        body_edit_updated_at = None
        body_editor_id = None
        body_editor_login = None
    else:
        if body_edit_total_count is None:
            body_edit_total_count = (
                2
                if body_last_edited_at
                in {
                    "2026-09-03T00:00:00Z",
                    "2026-09-04T00:00:00Z",
                }
                else 3
            )
        if body_edit_id is None:
            body_edit_id = f"UCE_{body_edit_total_count}"
        body_edit_created_at = body_edit_created_at or body_last_edited_at
        body_edit_edited_at = body_edit_edited_at or body_last_edited_at
        body_edit_updated_at = body_edit_updated_at or body_last_edited_at
    return pr_metadata.MetadataVersion(
        title_event_id,
        title_event_created_at,
        title_previous,
        title_current,
        title_actor_id,
        title_actor_login,
        body_last_edited_at,
        body_editor_id,
        body_editor_login,
        body_edit_total_count,
        body_edit_id,
        body_edit_created_at,
        body_edit_edited_at,
        body_edit_updated_at,
        (
            _body_original(original_body, body_edit_edited_at)
            if body_edit_total_count == 2
            else None
        ),
    )


def _graphql_payload(
    state: dict,
    version: pr_metadata.MetadataVersion,
    *,
    original_body: str = "prior body",
) -> dict:
    owner = state["base"]["repo"]["owner"]["login"]
    actor = (
        {
            "__typename": "User",
            "databaseId": version.title_actor_id,
            "login": version.title_actor_login,
        }
        if version.title_event_id is not None
        else None
    )
    nodes = (
        [
            {
                "__typename": "RenamedTitleEvent",
                "actor": actor,
                "createdAt": version.title_event_created_at,
                "currentTitle": version.title_current,
                "id": version.title_event_id,
                "previousTitle": version.title_previous,
            }
        ]
        if version.title_event_id is not None
        else []
    )
    editor = (
        {
            "__typename": "User",
            "databaseId": version.body_editor_id,
            "login": version.body_editor_login,
        }
        if version.body_last_edited_at is not None
        else None
    )
    edit_nodes = []
    if version.body_edit_total_count:
        edit_nodes.append(
            {
                "createdAt": version.body_edit_created_at,
                "deletedAt": None,
                "diff": state["body"] or "",
                "editedAt": version.body_edit_edited_at,
                "editor": editor,
                "id": version.body_edit_id,
                "updatedAt": version.body_edit_updated_at,
            }
        )
        if version.body_edit_total_count > 1:
            edit_nodes.append(
                {
                    "createdAt": "2026-09-03T00:00:00Z",
                    "deletedAt": None,
                    "diff": "prior body",
                    "editedAt": "2026-09-03T00:00:00Z",
                    "editor": {
                        "__typename": "User",
                        "databaseId": OWNER_ID,
                        "login": owner,
                    },
                    "id": "UCE_previous",
                    "updatedAt": "2026-09-03T00:00:00Z",
                }
            )
        if version.body_edit_total_count == 2:
            original = version.body_original
            edit_nodes[1].update(
                createdAt=original.materialized_at,
                updatedAt=original.materialized_at,
                editedAt=original.authored_at,
                id=original.edit_id,
                diff=original_body,
                editor={
                    "__typename": "User",
                    "databaseId": original.author_id,
                    "login": original.author_login,
                },
            )
    total_count = version.body_edit_total_count
    return {
        "data": {
            "repository": {
                "databaseId": state["base"]["repo"]["id"],
                "nameWithOwner": state["base"]["repo"]["full_name"],
                "owner": {
                    "__typename": "User",
                    "databaseId": OWNER_ID,
                    "login": owner,
                },
                "pullRequest": {
                    "id": state["node_id"],
                    "databaseId": state["id"],
                    "createdAt": PR_CREATED_AT,
                    "author": {
                        "__typename": "User",
                        "databaseId": OWNER_ID,
                        "login": owner,
                    },
                    "baseRefOid": state["base"]["sha"],
                    "baseRefName": state["base"]["ref"],
                    "body": "" if state["body"] is None else state["body"],
                    "editor": editor,
                    "headRefOid": state["head"]["sha"],
                    "headRefName": state["head"]["ref"],
                    "lastEditedAt": version.body_last_edited_at,
                    "number": state["number"],
                    "timelineItems": {"nodes": nodes},
                    "title": state["title"],
                    "updatedAt": state["updated_at"],
                    "url": (
                        f"https://github.com/{REPOSITORY}/pull/{PR_NUMBER}"
                    ),
                    "userContentEdits": {
                        "nodes": edit_nodes,
                        "pageInfo": {
                            "endCursor": (
                                f"cursor-{min(total_count, 2)}"
                                if total_count
                                else None
                            ),
                            "hasNextPage": total_count > 2,
                            "hasPreviousPage": False,
                            "startCursor": "cursor-1" if total_count else None,
                        },
                        "totalCount": total_count,
                    },
                },
            }
        }
    }


def _first_body_history_controls(payload: dict) -> dict[str, dict]:
    cases = {}
    for name, field, value in (
        ("forged-initial-body", "diff", "not the pre-edit body"),
        ("forged-initial-id", "id", payload["data"]["repository"]["pullRequest"][
            "userContentEdits"
        ]["nodes"][0]["id"]),
        ("forged-authorship-time", "editedAt", "2026-09-02T00:00:01Z"),
        ("same-second-authorship", "editedAt", "2026-09-04T00:00:05Z"),
        ("earlier-materialization", "createdAt", PR_CREATED_AT),
        ("altered-original", "updatedAt", "2026-09-04T00:00:06Z"),
        ("deleted-original", "deletedAt", "2026-09-04T00:00:06Z"),
        ("null-original-diff", "diff", None),
        ("missing-original-author", "editor", None),
        ("bot-original-author", "editor", {"__typename": "Bot", "login": "bot"}),
        ("unknown-original-field", "extra", True),
    ):
        changed = copy.deepcopy(payload)
        changed["data"]["repository"]["pullRequest"]["userContentEdits"][
            "nodes"
        ][1][field] = value
        cases[name] = changed
    for subject in ("author", "editor"):
        changed = copy.deepcopy(payload)
        pull = changed["data"]["repository"]["pullRequest"]
        actor = (
            pull["author"] if subject == "author"
            else pull["userContentEdits"]["nodes"][1]["editor"]
        )
        actor["databaseId"] += 1
        cases[f"wrong-{subject}-id"] = changed
        changed = copy.deepcopy(payload)
        pull = changed["data"]["repository"]["pullRequest"]
        actor = (
            pull["author"] if subject == "author"
            else pull["userContentEdits"]["nodes"][1]["editor"]
        )
        actor["login"] = "another-user"
        cases[f"wrong-{subject}-login"] = changed
    changed = copy.deepcopy(payload)
    changed["data"]["repository"]["pullRequest"]["createdAt"] = "2026-09-01T00:00:00Z"
    cases["wrong-pr-creation"] = changed
    changed = copy.deepcopy(payload)
    history = changed["data"]["repository"]["pullRequest"]["userContentEdits"]
    history["totalCount"] = 1
    history["nodes"] = history["nodes"][:1]
    history["pageInfo"]["endCursor"] = history["pageInfo"]["startCursor"]
    cases["impossible-single-revision"] = changed
    for total in (2, 3):
        changed = copy.deepcopy(payload)
        history = changed["data"]["repository"]["pullRequest"]["userContentEdits"]
        history["totalCount"] = total
        history["pageInfo"]["hasNextPage"] = total > 2
        history["nodes"][1].update(
            createdAt="2026-09-04T00:00:04Z",
            editedAt="2026-09-04T00:00:04Z",
            updatedAt="2026-09-04T00:00:04Z",
            id="UCE_intervening", diff="intervening body",
        )
        cases[f"two-real-edits-count-{total}"] = changed
    changed = copy.deepcopy(payload)
    changed["data"]["repository"]["pullRequest"]["userContentEdits"][
        "pageInfo"
    ]["hasNextPage"] = True
    cases["incomplete-first-history"] = changed
    return cases


def _add_metadata_versions(
    client: ScriptedClient,
    *states_and_versions: tuple[dict, pr_metadata.MetadataVersion],
    original_body: str = "prior body",
) -> None:
    client.add(
        "POST",
        "graphql",
        *(
            _response(_graphql_payload(state, version, original_body=original_body))
            for state, version in states_and_versions
        ),
    )


def _endpoint(suffix: str) -> str:
    return pr_metadata._endpoint(REPOSITORY, suffix)


def _query(suffix: str, pairs: list[tuple[str, str]]) -> str:
    return pr_metadata._query_endpoint(REPOSITORY, suffix, pairs)


def _test_runs_page(page: int) -> str:
    return _query(
        "actions/workflows/build.yml/runs",
        [("per_page", "100"), ("page", str(page))],
    )


def _numeric_api_url(endpoint: str) -> str:
    prefix = f"repos/{REPOSITORY}/"
    if not endpoint.startswith(prefix):
        raise AssertionError(endpoint)
    return (
        f"https://api.github.com/repositories/{REPOSITORY_ID}/"
        + endpoint[len(prefix) :]
    )


def _pr(
    *,
    head: str = HEAD,
    base: str = BASE,
    title: str = "Stable title",
    body: str | None = "Stable body",
    updated_at: str = "2026-09-04T00:00:00Z",
) -> dict:
    return {
        "id": 9001,
        "node_id": "PR_node_199",
        "number": PR_NUMBER,
        "state": "open",
        "title": title,
        "body": body,
        "updated_at": updated_at,
        "url": f"https://api.github.com/repos/{REPOSITORY}/pulls/{PR_NUMBER}",
        "head": {"sha": head, "ref": HEAD_REF},
        "base": {
            "sha": base,
            "ref": "master",
            "repo": {
                "id": REPOSITORY_ID,
                "name": "repo",
                "full_name": REPOSITORY,
                "private": False,
                "url": f"https://api.github.com/repos/{REPOSITORY}",
                "html_url": f"https://github.com/{REPOSITORY}",
                "owner": {
                    "id": OWNER_ID,
                    "login": "owner",
                    "type": "User",
                    "site_admin": False,
                },
            },
        },
    }


def _comment(
    comment_id: int,
    body: str,
    *,
    repository: str = REPOSITORY,
    pr_number: int = PR_NUMBER,
    author_login: str = "owner",
    author_type: str = "User",
    author_association: str = "OWNER",
    author_id: int = 77,
    site_admin: bool = False,
    created_at: str = "2026-09-04T00:00:00Z",
    updated_at: str = "2026-09-04T00:00:01Z",
) -> dict:
    owner, name = repository.split("/", 1)
    return {
        "id": comment_id,
        "node_id": f"IC_{comment_id}",
        "url": (
            f"https://api.github.com/repos/{owner}/{name}/"
            f"issues/comments/{comment_id}"
        ),
        "html_url": (
            f"https://github.com/{owner}/{name}/pull/{pr_number}"
            f"#issuecomment-{comment_id}"
        ),
        "issue_url": (
            f"https://api.github.com/repos/{owner}/{name}/issues/{pr_number}"
        ),
        "body": body,
        "user": {
            "id": author_id,
            "login": author_login,
            "type": author_type,
            "site_admin": site_admin,
        },
        "author_association": author_association,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _metadata_event_payload(pre_state: dict | None = None, target: dict | None = None) -> dict:
    pre_state = _pr(body="Old body") if pre_state is None else pre_state
    target = _pr() if target is None else target
    return {
        "action": "edited",
        "number": target["number"],
        "repository": copy.deepcopy(target["base"]["repo"]),
        "sender": copy.deepcopy(target["base"]["repo"]["owner"]),
        "pull_request": copy.deepcopy(target),
        "changes": {
            name: {"from": pre_state[name]}
            for name in ("body", "title")
            if (pre_state[name] or "") != (target[name] or "")
        },
    }


def _response(
    payload: object,
    *,
    headers: dict[str, str] | None = None,
    status: int = 200,
) -> pr_metadata.ApiResponse:
    return pr_metadata.ApiResponse(status, headers or {}, payload)


def _workflow(**changes: object) -> dict:
    payload = {
        "id": WORKFLOW_ID,
        "node_id": "W_build",
        "name": "Build CI",
        "path": pr_metadata.WORKFLOW_PATH,
        "state": "active",
        "created_at": "2026-07-16T11:44:49Z",
        "updated_at": "2026-07-16T11:44:49Z",
        "url": (
            f"https://api.github.com/repos/{REPOSITORY}/"
            f"actions/workflows/{WORKFLOW_ID}"
        ),
        "html_url": (
            f"https://github.com/{REPOSITORY}/blob/master/"
            f"{pr_metadata.WORKFLOW_PATH}"
        ),
        "badge_url": (
            f"https://github.com/{REPOSITORY}/workflows/Build%20CI/badge.svg"
        ),
    }
    payload.update(changes)
    return payload


def _link(*relations: tuple[str, str]) -> str:
    return ", ".join(f'<{url}>; rel="{relation}"' for relation, url in relations)


def _job(
    name: str,
    *,
    job_id: int,
    run_id: int,
    run_attempt: int = 1,
    head_sha: str = HEAD,
    head_branch: str = HEAD_REF,
    status: str = "completed",
    conclusion: str | None = "success",
    runner_name: str | None = "GitHub Actions 1",
    created_at: str | None = "2026-09-04T00:00:01Z",
    started_at: str | None = "2026-09-04T00:00:01Z",
    completed_at: str | None = None,
) -> dict:
    return {
        "id": job_id,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_url": f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}",
        "node_id": f"CR_{job_id}",
        "head_sha": head_sha,
        "head_branch": head_branch,
        "workflow_name": "Build CI",
        "event": "pull_request",
        "url": f"https://api.github.com/repos/{REPOSITORY}/actions/jobs/{job_id}",
        "html_url": (
            f"https://github.com/{REPOSITORY}/actions/runs/{run_id}/job/{job_id}"
        ),
        "check_run_url": (
            f"https://api.github.com/repos/{REPOSITORY}/check-runs/{job_id}"
        ),
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "runner_name": runner_name,
        "created_at": created_at,
        "started_at": started_at,
        "completed_at": (
            completed_at
            if completed_at is not None
            else started_at
            if (
                status == "completed"
                and conclusion == "skipped"
                and runner_name is None
            )
            else "2026-09-04T00:00:02Z"
            if status == "completed"
            else None
        ),
        "runner_id": 1 if runner_name else None,
        "runner_group_id": 0 if runner_name else None,
        "runner_group_name": "GitHub Actions" if runner_name else None,
    }


def _full_jobs(
    run_id: int,
    run_attempt: int,
    *,
    active: bool = False,
) -> list[dict]:
    names = sorted(pr_metadata.FULL_JOB_NAMES)
    if active:
        return [
            _job(
                name,
                job_id=run_id * 100 + index,
                run_id=run_id,
                run_attempt=run_attempt,
                status="queued",
                conclusion=None,
                runner_name=None,
                started_at=None,
            )
            for index, name in enumerate(names, 1)
        ]
    jobs = [
        _job(
            name,
            job_id=run_id * 100 + index,
            run_id=run_id,
            run_attempt=run_attempt,
        )
        for index, name in enumerate(
            sorted(pr_metadata.FULL_SUCCESS_JOB_NAMES),
            1,
        )
    ]
    jobs.append(
        _job(
            "patch-release",
            job_id=run_id * 100 + len(jobs) + 1,
            run_id=run_id,
            run_attempt=run_attempt,
            conclusion="skipped",
            runner_name=None,
        )
    )
    return jobs


def _metadata_jobs(
    run_id: int,
    run_attempt: int,
    *,
    success: bool = False,
) -> list[dict]:
    jobs = []
    for index, name in enumerate(sorted(pr_metadata.METADATA_JOB_NAMES), 1):
        if name in {"extended-host-tests", "legacy", "patch-release"}:
            jobs.append(
                _job(
                    name,
                    job_id=run_id * 100 + index,
                    run_id=run_id,
                    run_attempt=run_attempt,
                    conclusion="skipped",
                    runner_name=None,
                )
            )
        elif name == "summary" and not success:
            jobs.append(
                _job(
                    name,
                    job_id=run_id * 100 + index,
                    run_id=run_id,
                    run_attempt=run_attempt,
                    conclusion="failure",
                )
            )
        else:
            jobs.append(
                _job(
                    name,
                    job_id=run_id * 100 + index,
                    run_id=run_id,
                    run_attempt=run_attempt,
                )
            )
    return jobs


def _run(
    run_id: int,
    run_number: int,
    *,
    mode: str,
    active: bool = False,
    success: bool = True,
    attempt: int = 1,
    metadata_event_payload: object = _DEFAULT_METADATA_EVENT,
) -> tuple[dict, list[dict]]:
    if active:
        status = "in_progress"
        conclusion = None
    else:
        status = "completed"
        conclusion = "success" if success else "failure"
    jobs = (
        _full_jobs(run_id, attempt, active=active)
        if mode == "full"
        else _metadata_jobs(run_id, attempt, success=success)
    )
    if mode == "metadata-only" and metadata_event_payload is not None:
        event = (
            _metadata_event_payload()
            if metadata_event_payload is _DEFAULT_METADATA_EVENT
            else metadata_event_payload
        )
        digest = metadata_event.event_digest(
            event, repository=REPOSITORY,
            run_id=run_id, run_number=run_number, run_attempt=attempt,
        )
        classifier = next(job for job in jobs if job["name"] == "metadata-classifier")
        classifier["steps"] = [{
            "name": metadata_event.STEP_PREFIX + digest,
            "number": 1, "status": "completed", "conclusion": "success",
            "started_at": classifier["started_at"],
            "completed_at": classifier["completed_at"],
        }]
    return (
        {
            "id": run_id,
            "workflow_id": WORKFLOW_ID,
            "run_number": run_number,
            "run_attempt": attempt,
            "event": "pull_request",
            "status": status,
            "conclusion": conclusion,
            "head_sha": HEAD,
            "head_branch": HEAD_REF,
            "created_at": "2026-09-04T00:00:00Z",
            "run_started_at": (
                "2026-09-04T00:00:00Z"
            ),
            "updated_at": (
                "2026-09-04T00:00:03Z"
                if active
                else "2026-09-04T00:00:03Z"
            ),
            "path": ".github/workflows/build.yml@refs/pull/199/merge",
            "url": f"https://api.github.com/repos/owner/repo/actions/runs/{run_id}",
            "pull_requests": [
                {
                    "number": PR_NUMBER,
                    "head": {"sha": HEAD},
                    "base": {"sha": BASE},
                }
            ],
        },
        jobs,
    )


def _rejection_run_drift_cases() -> dict[str, tuple[list, list]]:
    full = _run(101, 10, mode="full")
    active = _run(101, 10, mode="full", active=True)
    other_binding = copy.deepcopy(full)
    other_binding[0]["pull_requests"][0]["base"]["sha"] = NEW_HEAD
    unbound = copy.deepcopy(full)
    unbound[0]["pull_requests"] = []
    updated = copy.deepcopy(full)
    updated[0]["updated_at"] = "2026-09-04T00:00:04Z"
    failed = _run(101, 10, mode="full", success=False)
    next(job for job in failed[1] if job["name"] == "summary")["conclusion"] = "failure"
    job_identity = copy.deepcopy(full)
    job_identity[1][0] = _job(
        job_identity[1][0]["name"], job_id=10199, run_id=101
    )
    job_runner = copy.deepcopy(full)
    job_runner[1][0]["runner_name"] = "Changed runner"
    job_timing = copy.deepcopy(full)
    job_timing[1][0]["completed_at"] = "2026-09-04T00:00:03Z"
    job_progress = copy.deepcopy(active)
    job_progress[1][0] = _job(
        job_progress[1][0]["name"],
        job_id=job_progress[1][0]["id"],
        run_id=101, status="in_progress", conclusion=None,
    )
    return {
        "new-full": ([full], [_run(202, 11, mode="full", active=True), full]),
        "new-metadata": ([full], [_run(202, 11, mode="metadata-only"), full]),
        "new-attempt": ([full], [_run(101, 10, mode="full", attempt=2)]),
        "other-binding": ([full], [other_binding]),
        "unbound": ([full], [unbound]),
        "run-updated": ([full], [updated]),
        "run-conclusion": ([full], [failed]),
        "job-identity": ([full], [job_identity]),
        "job-runner": ([full], [job_runner]),
        "job-timing": ([full], [job_timing]),
        "older-run-disappeared": ([full, _run(100, 9, mode="full")], [full]),
        "watermark-disappeared": ([full], []),
        "active-run-completed": ([active], [full]),
        "active-job-progress": ([active], [job_progress]),
    }


class ScriptedClient:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[object]] = {}
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []

    def add(
        self,
        method: str,
        endpoint: str,
        *responses: object,
    ) -> None:
        self.routes.setdefault((method, endpoint), []).extend(
            copy.deepcopy(list(responses))
        )

    def add_stable_comment_pages(
        self, method: str, endpoint: str, *responses: object
    ) -> None:
        """Supply each logical page state for both complete authority walks."""
        if method != "GET" or "/comments?" not in endpoint:
            raise AssertionError("stable comment pages require a comment-list GET")
        self.add(
            method, endpoint,
            *(copy.deepcopy(response) for response in responses for _ in range(2)),
        )

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        body: dict[str, object] | None = None,
        label: str,
    ) -> pr_metadata.ApiResponse:
        del label
        self.calls.append((method, endpoint, copy.deepcopy(body)))
        route = self.routes.get((method, endpoint))
        if not route:
            raise AssertionError(f"unexpected request: {method} {endpoint}")
        response = route.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(
                method=method,
                endpoint=endpoint,
                body=copy.deepcopy(body),
            )
        if isinstance(response, pr_metadata.ApiResponse):
            return copy.deepcopy(response)
        return _response(
            copy.deepcopy(response),
            status=(
                200
                if endpoint == "graphql"
                else 201 if method == "POST" else 200
            ),
        )


def _add_pr_states(client: ScriptedClient, *states: dict) -> None:
    client.add("GET", _endpoint(f"pulls/{PR_NUMBER}"), *states)


def _api_failure(
    status: int = 422,
    *,
    returncode: int = 1,
    output: bytes | None = None,
    transport_error: Exception | None = None,
):
    raw = output if output is not None else (
        f"HTTP/2.0 {status} Error\nContent-Type: application/json\r\n\r\n"
        '{"message":"Validation Failed"}\n'
    ).encode("utf-8")

    def runner(arguments, **_kwargs):
        if transport_error is not None:
            raise transport_error
        return subprocess.CompletedProcess(
            arguments, returncode, stdout=raw,
            stderr=b"gh: Validation Failed (HTTP 422)\n",
        )

    def request(*, method, endpoint, body):
        return pr_metadata.GitHubClient("/usr/bin/true", runner=runner).request(
            method, endpoint, body=body, label="pull request metadata update"
        )

    return request


def _mutation_client(
    *,
    failure=None,
    history: tuple[dict, ...] = (),
    title: str | None = None,
    body: str = "new body",
    pre_state: dict | None = None,
    pre_version: pr_metadata.MetadataVersion | None = None,
    runs: list[tuple[dict, list[dict]]] | None = None,
    rejection_runs: list[tuple[dict, list[dict]]] | None = None,
) -> tuple[ScriptedClient, list[dict]]:
    state = _pr() if pre_state is None else pre_state
    runs = [_run(101, 10, mode="full")] if runs is None else runs
    client = ScriptedClient()
    _add_pr_states(client, state, state)
    _add_snapshot(client, runs, copies=2)
    _add_edit_transaction(
        client, title=title, body=body, pre_state=state, pre_version=pre_version
    )
    comments = copy.deepcopy(list(history))
    posts = []

    def create_comment(*, body, **_kwargs):
        comment_id = max((item["id"] for item in comments), default=400) + 1
        second = (
            6 + len(history)
            if body["body"].startswith(pr_metadata.CONFIRMATION_MARKER)
            else len(comments) + 1
        )
        timestamp = f"2026-09-04T00:00:{second:02d}Z"
        payload = _comment(
            comment_id, body["body"], created_at=timestamp, updated_at=timestamp
        )
        comments.append(payload)
        posts.append(payload)
        return payload

    client.routes[("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))] = [
        create_comment, create_comment
    ]
    comments_route = (
        "GET",
        _query(f"issues/{PR_NUMBER}/comments", [("per_page", "100"), ("page", "1")]),
    )
    client.routes[comments_route] = [
        lambda **_kwargs: copy.deepcopy(comments) for _ in range(6)
    ]
    target = _pr(
        title=title if title is not None else state["title"],
        body=body, updated_at="2026-09-04T00:00:05Z",
    )
    client.add("PATCH", _endpoint(f"pulls/{PR_NUMBER}"), failure or target)
    if failure is not None:
        _add_snapshot(client, runs if rejection_runs is None else rejection_runs)
        client.routes[("POST", "graphql")][2] = _response(
            _graphql_payload(state, pre_version or _metadata_version())
        )
    return client, posts


def _add_edit_transaction(
    client: ScriptedClient,
    *,
    title: str | None = None,
    body: str | None = None,
    response_changes: dict[str, object] | None = None,
    pre_state: dict | None = None,
    pre_version: pr_metadata.MetadataVersion | None = None,
) -> None:
    pre_state = _pr() if pre_state is None else copy.deepcopy(pre_state)
    post_state = _pr(
        title=title if title is not None else pre_state["title"],
        body=body if body is not None else pre_state["body"],
        updated_at="2026-09-04T00:00:05Z",
    )
    pre_version = _metadata_version() if pre_version is None else pre_version
    title_changed = title is not None and title != pre_state["title"]
    pre_body = "" if pre_state["body"] is None else pre_state["body"]
    body_changed = body is not None and body != pre_body
    post_version = pre_version
    if title_changed:
        post_version = replace(
            post_version,
            title_event_id="RTE_2",
            title_event_created_at="2026-09-04T00:00:05Z",
            title_previous=pre_state["title"],
            title_current=title,
            title_actor_id=OWNER_ID,
            title_actor_login="owner",
        )
    if body_changed:
        count = 2 if pre_version.body_edit_total_count == 0 else pre_version.body_edit_total_count + 1
        post_version = replace(
            post_version,
            body_last_edited_at="2026-09-04T00:00:05Z",
            body_editor_id=OWNER_ID,
            body_editor_login="owner",
            body_edit_total_count=count,
            body_edit_id=f"UCE_{count}",
            body_edit_created_at="2026-09-04T00:00:05Z",
            body_edit_edited_at="2026-09-04T00:00:05Z",
            body_edit_updated_at="2026-09-04T00:00:05Z",
            body_original=(
                _body_original(pre_body, "2026-09-04T00:00:05Z")
                if count == 2 else None
            ),
        )
    _add_metadata_versions(
        client,
        (pre_state, pre_version),
        (pre_state, pre_version),
    )
    client.add(
        "POST", "graphql", _response(_graphql_payload(
            post_state, post_version,
            original_body=pre_body if pre_version.body_edit_total_count == 0 else "prior body",
        )),
    )
    client.add("GET", _endpoint(f"pulls/{PR_NUMBER}"), pre_state)
    for (method, endpoint), responses in list(client.routes.items()):
        if (
            method == "GET"
            and "actions/" in endpoint
            and responses
        ):
            responses.append(copy.deepcopy(responses[-1]))
    intent_payload: dict[str, object] = {}
    client.add_stable_comment_pages(
        "GET",
        _query(
            f"issues/{PR_NUMBER}/comments",
            [("per_page", "100"), ("page", "1")],
        ),
        [],
        lambda **_kwargs: [copy.deepcopy(intent_payload)],
        lambda **_kwargs: [copy.deepcopy(intent_payload)],
    )

    def response(
        *,
        method: str,
        endpoint: str,
        body: dict[str, object] | None,
    ) -> dict:
        del method, endpoint
        if not isinstance(body, dict) or not isinstance(body.get("body"), str):
            raise AssertionError("receipt creation requires a comment body")
        method_name = (
            "intent"
            if pr_metadata.INTENT_MARKER in body["body"]
            else "confirmation"
        )
        comment_id = 401 if method_name == "intent" else 402
        created_at = (
            "2026-09-04T00:00:01Z"
            if method_name == "intent"
            else "2026-09-04T00:00:06Z"
        )
        payload = _comment(
            comment_id,
            body["body"],
            created_at=created_at,
            updated_at=created_at,
        )
        if method_name == "intent":
            intent_payload.clear()
            intent_payload.update(copy.deepcopy(payload))
        payload.update(response_changes or {})
        return payload

    client.add(
        "POST",
        _endpoint(f"issues/{PR_NUMBER}/comments"),
        response,
        response,
    )


def _add_snapshot(
    client: ScriptedClient,
    runs_and_jobs: list[tuple[dict, list[dict]]],
    *,
    copies: int = 1,
) -> None:
    client.add(
        "GET",
        _endpoint("actions/workflows/build.yml"),
        *(_workflow() for _ in range(copies)),
    )
    runs = [record for record, _jobs in runs_and_jobs]
    runs_endpoint = _query(
        "actions/workflows/build.yml/runs",
        [
            ("head_sha", HEAD),
            ("per_page", "100"),
            ("page", "1"),
        ],
    )
    client.add(
        "GET",
        runs_endpoint,
        *(
            {"total_count": len(runs), "workflow_runs": runs}
            for _ in range(copies)
        ),
    )
    for record, jobs in runs_and_jobs:
        if record["status"] == "completed":
            client.add(
                "GET",
                _endpoint(f"actions/runs/{record['id']}"),
                *(copy.deepcopy(record) for _ in range(copies)),
            )
        jobs_endpoint = _query(
            f"actions/runs/{record['id']}/attempts/{record['run_attempt']}/jobs",
            [("per_page", "100"), ("page", "1")],
        )
        client.add(
            "GET",
            jobs_endpoint,
            *(
                {"total_count": len(jobs), "jobs": jobs}
                for _ in range(copies)
            ),
        )


_MISSING = object()


def _cli_api_call(
    method: str,
    endpoint: str,
    *,
    payload: object = _MISSING,
    status: int = 200,
    input_text: str | None = "",
    echo_body: bool = False,
    echo_last_comment: bool = False,
) -> dict:
    call = {
        "endpoint": endpoint,
        "input": input_text,
        "method": method,
        "status": status,
    }
    if echo_body:
        call["echo_body"] = True
    if echo_last_comment:
        call["echo_last_comment"] = True
    if payload is not _MISSING:
        call["payload"] = payload
    return call


def _cli_stable_comment_walk(*pages: dict) -> list[dict]:
    """Expand one stable complete walk into its two explicit HTTP observations."""
    return [copy.deepcopy(page) for _ in range(2) for page in pages]


def _cli_snapshot_calls(
    runs_and_jobs: list[tuple[dict, list[dict]]],
) -> list[dict]:
    calls = [
        _cli_api_call(
            "GET",
            _endpoint("actions/workflows/build.yml"),
            payload=_workflow(),
        ),
        _cli_api_call(
            "GET",
            _query(
                "actions/workflows/build.yml/runs",
                [
                    ("head_sha", HEAD),
                    ("per_page", "100"),
                    ("page", "1"),
                ],
            ),
            payload={
                "total_count": len(runs_and_jobs),
                "workflow_runs": [
                    record for record, _jobs in runs_and_jobs
                ],
            },
        ),
    ]
    for record, jobs in runs_and_jobs:
        if record["status"] == "completed":
            calls.append(
                _cli_api_call(
                    "GET",
                    _endpoint(f"actions/runs/{record['id']}"),
                    payload=record,
                )
            )
        calls.append(
            _cli_api_call(
                "GET",
                _query(
                    (
                        f"actions/runs/{record['id']}/attempts/"
                        f"{record['run_attempt']}/jobs"
                    ),
                    [("per_page", "100"), ("page", "1")],
                ),
                payload={"total_count": len(jobs), "jobs": jobs},
            )
        )
    return calls


def _cli_metadata_version_call(
    state: dict,
    version: pr_metadata.MetadataVersion,
    *,
    original_body: str = "prior body",
) -> dict:
    return _cli_api_call(
        "POST",
        "graphql",
        payload=_graphql_payload(state, version, original_body=original_body),
        input_text=json.dumps(
            {
                "query": pr_metadata.METADATA_VERSION_QUERY,
                "variables": {
                    "name": "repo",
                    "number": PR_NUMBER,
                    "owner": "owner",
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )


def _cli_rejection_calls(
    body: str,
    *,
    history: tuple[dict, ...] = (),
    failure: dict | None = None,
    held: bool = False,
    recovery: bool = False,
    pre_state: dict | None = None,
    pre_version: pr_metadata.MetadataVersion | None = None,
    runs: list[tuple[dict, list[dict]]] | None = None,
    rejection_runs: list[tuple[dict, list[dict]]] | None = None,
) -> list[dict]:
    pre_state = pre_state if pre_state is not None else _pr()
    pre_version = pre_version if pre_version is not None else _metadata_version()
    runs = [_run(101, 10, mode="full")] if runs is None else runs
    first_edit = pre_version.body_edit_total_count == 0
    original_body = (pre_state["body"] or "") if first_edit else "prior body"
    target = _pr(body=body, updated_at="2026-09-04T00:00:05Z")
    target_version = _metadata_version(
        body_last_edited_at="2026-09-04T00:00:05Z",
        body_edit_total_count=2 if first_edit else pre_version.body_edit_total_count + 1,
        original_body=original_body,
    )
    state, version = (target, target_version) if recovery else (pre_state, pre_version)
    comments_endpoint = _endpoint(f"issues/{PR_NUMBER}/comments")
    list_endpoint = _query(f"issues/{PR_NUMBER}/comments", [("per_page", "100"), ("page", "1")])
    calls = []
    for _ in range(2):
        calls.extend([
            _cli_api_call("GET", _endpoint(f"pulls/{PR_NUMBER}"), payload=state),
            *_cli_snapshot_calls(runs),
        ])
    calls.extend([
        _cli_metadata_version_call(state, version, original_body=original_body),
        *_cli_stable_comment_walk(_cli_api_call("GET", list_endpoint, payload=list(history))),
    ])
    creating = not held and not recovery
    intent_id = max((comment["id"] for comment in history), default=400) + int(creating)
    intent_time = f"2026-09-04T00:00:{len(history) + 1:02d}Z"
    comments = list(history)
    if creating:
        template = _comment(intent_id, "", created_at=intent_time, updated_at=intent_time)
        calls.append(_cli_api_call(
            "POST", comments_endpoint, payload=template, status=201,
            input_text=None, echo_body=True,
        ))
        comments.append(template)
    walk = _cli_stable_comment_walk(_cli_api_call(
        "GET", list_endpoint, payload=comments, echo_last_comment=creating
    ))
    if not recovery:
        calls.extend([
            *_cli_snapshot_calls(runs),
            *copy.deepcopy(walk),
            _cli_metadata_version_call(state, version, original_body=original_body),
        ])
        if held:
            return calls
        patch = _cli_api_call(
            "PATCH", _endpoint(f"pulls/{PR_NUMBER}"), payload=target,
            input_text=json.dumps({"body": body}, separators=(",", ":"), sort_keys=True),
        )
        if failure is not None:
            patch.update(failure)
        calls.append(patch)
    if failure is not None:
        if not failure.get("definite"):
            return calls
        calls.extend([
            *_cli_snapshot_calls(runs if rejection_runs is None else rejection_runs),
            *copy.deepcopy(walk),
            _cli_metadata_version_call(state, version, original_body=original_body),
        ])
        second = len(history) + 2
    else:
        calls.extend([
            *copy.deepcopy(walk),
            _cli_metadata_version_call(target, target_version, original_body=original_body),
        ])
        second = 6 + len(history)
    terminal_time = f"2026-09-04T00:00:{second:02d}Z"
    calls.append(_cli_api_call(
        "POST", comments_endpoint,
        payload=_comment(intent_id + 1, "", created_at=terminal_time, updated_at=terminal_time),
        status=201, input_text=None, echo_body=True,
    ))
    return calls


def _canonical_decision(**changes: object) -> str:
    payload = {
        "action": "updated",
        "abort_comment_id": None,
        "abort_comment_url": None,
        "base_sha": BASE,
        "comment_id": None,
        "guidance": [],
        "head_sha": HEAD,
        "mutated": False,
        "pr_number": PR_NUMBER,
        "reason": "",
        "confirmation_comment_id": None,
        "confirmation_comment_url": None,
        "intent_comment_id": None,
        "intent_comment_url": None,
        "repository": REPOSITORY,
        "run_id": None,
    }
    payload.update(changes)
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ) + "\n"


def _receipt_payload(
    *,
    repository: str = REPOSITORY,
    repository_id: int = REPOSITORY_ID,
    pr_number: int = PR_NUMBER,
    head_sha: str = HEAD,
    base_sha: str = BASE,
    provided_fields: dict[str, str] | None = None,
    changed_fields: dict[str, str] | None = None,
    watermark_run_id: int = 101,
    watermark_run_number: int = 10,
    watermark_created_at: str = "2026-09-04T00:00:00Z",
    workflow_id: int = WORKFLOW_ID,
    workflow_path: str = pr_metadata.WORKFLOW_PATH,
    nonce: str = "a" * 64,
    pre_metadata_sha256: str | None = None,
    target_metadata_sha256: str | None = None,
    pre_version: pr_metadata.MetadataVersion | None = None,
    pre_title: str = "Stable title",
    pre_body: str | None = "Old body",
) -> dict:
    pre_version = pre_version or _metadata_version(
        body_last_edited_at="2026-09-03T00:00:00Z",
    )
    provided_fields = (
        provided_fields
        if provided_fields is not None
        else {"body": _sha256("Stable body")}
    )
    return {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "nonce": nonce,
        "pre_fields": {
            "body": _sha256(json.dumps(pre_body, separators=(",", ":"))),
            "title": _sha256(json.dumps(pre_title, separators=(",", ":"))),
        },
        "pre_metadata_sha256": (
            pre_metadata_sha256
            if pre_metadata_sha256 is not None
            else _metadata_sha256(pre_title, pre_body)
        ),
        "pre_version": pre_version.canonical_payload(),
        "pr_number": pr_number,
        "repository": repository,
        "repository_id": repository_id,
        "provided_fields": provided_fields,
        "changed_fields": (
            changed_fields
            if changed_fields is not None
            else dict(provided_fields)
        ),
        "schema_version": 1,
        "target_metadata_sha256": (
            target_metadata_sha256
            if target_metadata_sha256 is not None
            else _metadata_sha256("Stable title", "Stable body")
        ),
        "watermark": {
            "created_at": watermark_created_at,
            "run_id": watermark_run_id,
            "run_number": watermark_run_number,
        },
        "workflow": {
            "id": workflow_id,
            "path": workflow_path,
        },
    }


def _receipt(**changes: object) -> pr_metadata.EditReceipt:
    return pr_metadata._parse_edit_receipt(_receipt_payload(**changes))


def _confirmation(
    receipt: pr_metadata.EditReceipt,
    *,
    intent_comment_id: int = 401,
    version: pr_metadata.MetadataVersion | None = None,
) -> pr_metadata.EditConfirmation:
    if version is None:
        if "body" in {field.field for field in receipt.changed_fields}:
            version = replace(
                receipt.pre_version,
                body_last_edited_at="2026-09-04T00:00:00Z",
                body_editor_id=OWNER_ID,
                body_editor_login="owner",
                body_edit_total_count=receipt.pre_version.body_edit_total_count + 1,
                body_edit_id="UCE_target",
                body_edit_created_at="2026-09-04T00:00:00Z",
                body_edit_edited_at="2026-09-04T00:00:00Z",
                body_edit_updated_at="2026-09-04T00:00:00Z",
                body_original=None,
            )
        else:
            version = receipt.pre_version
    return pr_metadata.EditConfirmation(
        schema_version=1,
        repository=receipt.repository,
        repository_id=receipt.repository_id,
        pr_number=receipt.pr_number,
        head_sha=receipt.head_sha,
        base_sha=receipt.base_sha,
        intent_comment_id=intent_comment_id,
        intent_nonce=receipt.nonce,
        metadata_sha256=receipt.target_metadata_sha256,
        metadata_version=version,
    )


def _intent_comment(
    receipt: pr_metadata.EditReceipt,
    *,
    comment_id: int = 401,
    created_at: str = "2026-09-04T00:00:01Z",
    updated_at: str | None = None,
    **changes: object,
) -> dict:
    payload = _comment(
        comment_id,
        pr_metadata._intent_comment_body(receipt),
        created_at=created_at,
        updated_at=updated_at if updated_at is not None else created_at,
    )
    payload.update(changes)
    return payload


def _drifted_intent_page(comment: dict, drift: str) -> list[dict]:
    if drift == "deleted":
        return []
    comment = copy.deepcopy(comment)
    if drift == "unmarked":
        comment["body"] = "No longer an intent"
    elif drift in ("nonce", "pre-version"):
        intent = pr_metadata._parse_intent_comment_body(comment["body"])
        intent = (
            replace(intent, nonce="b" * 64 if intent.nonce != "b" * 64 else "c" * 64)
            if drift == "nonce"
            else replace(
                intent,
                pre_version=replace(intent.pre_version, title_event_id="RTE_changed"),
            )
        )
        comment["body"] = pr_metadata._intent_comment_body(intent)
    elif drift == "timestamp":
        comment["created_at"] = comment["updated_at"] = "2026-09-04T00:00:02Z"
    elif drift == "association":
        comment["author_association"] = "CONTRIBUTOR"
    elif drift == "deleted-author":
        comment["user"] = None
    else:
        field, value = {
            "author-id": ("id", OWNER_ID + 1),
            "author-login": ("login", "not-owner"),
            "author-type": ("type", "Bot"),
            "site-admin": ("site_admin", True),
        }[drift]
        comment["user"][field] = value
    return [comment]


def _confirmation_comment(
    confirmation: pr_metadata.EditConfirmation,
    *,
    comment_id: int = 402,
    created_at: str = "2026-09-04T00:00:02Z",
    updated_at: str | None = None,
    **changes: object,
) -> dict:
    payload = _comment(
        comment_id,
        pr_metadata._confirmation_comment_body(confirmation),
        created_at=created_at,
        updated_at=updated_at if updated_at is not None else created_at,
    )
    payload.update(changes)
    return payload


def _abort(
    receipt: pr_metadata.EditReceipt,
    *,
    intent_comment_id: int = 401,
    reason: str = "run-authority-drift",
    observed_state: dict | None = None,
    observed_version: pr_metadata.MetadataVersion | None = None,
) -> pr_metadata.EditAbort:
    state = observed_state or _pr()
    return pr_metadata.EditAbort(
        schema_version=1,
        repository=receipt.repository,
        repository_id=receipt.repository_id,
        pr_number=receipt.pr_number,
        intent_comment_id=intent_comment_id,
        intent_nonce=receipt.nonce,
        intent_head_sha=receipt.head_sha,
        intent_base_sha=receipt.base_sha,
        observed_head_sha=state["head"]["sha"],
        observed_base_sha=state["base"]["sha"],
        observed_metadata_sha256=_metadata_sha256(
            state["title"],
            state["body"],
        ),
        observed_version=observed_version or _metadata_version(),
        reason=reason,
    )


def _abort_comment(
    abort: pr_metadata.EditAbort,
    *,
    comment_id: int = 403,
    created_at: str = "2026-09-04T00:00:03Z",
    **changes: object,
) -> dict:
    payload = _comment(
        comment_id,
        pr_metadata._abort_comment_body(abort),
        created_at=created_at,
        updated_at=created_at,
    )
    payload.update(changes)
    return payload


def _add_transaction_comments(
    client: ScriptedClient,
    receipt: pr_metadata.EditReceipt,
    *,
    confirmation: pr_metadata.EditConfirmation | None = None,
    confirmation_comment_id: int = 402,
    comments: list[dict] | None = None,
    copies: int = 2,
) -> None:
    endpoint = _query(
        f"issues/{PR_NUMBER}/comments",
        [("per_page", "100"), ("page", "1")],
    )
    payload = (
        comments
        if comments is not None
        else [
            _intent_comment(receipt),
            _confirmation_comment(
                confirmation or _confirmation(receipt),
                comment_id=confirmation_comment_id,
            ),
        ]
    )
    client.add_stable_comment_pages(
        "GET",
        endpoint,
        *(copy.deepcopy(payload) for _ in range(copies)),
    )


def _reconcile(
    client: ScriptedClient,
    *,
    receipt: pr_metadata.EditReceipt | None = None,
    confirmation: pr_metadata.EditConfirmation | None = None,
    confirmation_comment_id: int = 402,
    comments: list[dict] | None = None,
    version: pr_metadata.MetadataVersion | None = None,
    state: dict | None = None,
    original_body: str = "prior body",
) -> pr_metadata.Decision:
    receipt = receipt or _receipt()
    workflow_route = ("GET", _endpoint("actions/workflows/build.yml"))
    if not client.routes.get(workflow_route):
        _add_snapshot(client, [_run(101, 10, mode="full")])
    if version is None:
        if "body" in {field.field for field in receipt.changed_fields}:
            version = replace(
                receipt.pre_version,
                body_last_edited_at="2026-09-04T00:00:00Z",
                body_editor_id=OWNER_ID,
                body_editor_login="owner",
                body_edit_total_count=receipt.pre_version.body_edit_total_count + 1,
                body_edit_id="UCE_target",
                body_edit_created_at="2026-09-04T00:00:00Z",
                body_edit_edited_at="2026-09-04T00:00:00Z",
                body_edit_updated_at="2026-09-04T00:00:00Z",
                body_original=None,
            )
        else:
            version = receipt.pre_version
    confirmation = confirmation or _confirmation(receipt, version=version)
    _add_transaction_comments(
        client,
        receipt,
        confirmation=confirmation,
        confirmation_comment_id=confirmation_comment_id,
        comments=comments,
    )
    _add_metadata_versions(
        client,
        *(
            (state or _pr(), version)
            for _ in range(2)
        ),
        original_body=original_body,
    )
    return pr_metadata.reconcile_metadata(
        client,
        repository=REPOSITORY,
        pr_number=PR_NUMBER,
        head_sha=HEAD,
        base_sha=BASE,
        confirmation_comment_id=confirmation_comment_id,
    )


FAKE_GH_DRIVER = r"""#!/usr/bin/python3
import json
import os
from pathlib import Path
import sys

scenario_path = Path(os.environ["FAKE_GH_SCENARIO"])
state_path = Path(os.environ["FAKE_GH_STATE"])
log_path = Path(os.environ["FAKE_GH_LOG"])
calls = json.loads(scenario_path.read_text(encoding="utf-8"))["calls"]
index = int(state_path.read_text(encoding="ascii")) if state_path.exists() else 0
if index >= len(calls):
    print("fake-gh: unexpected extra call", file=sys.stderr)
    raise SystemExit(97)
expected = calls[index]
arguments = sys.argv[1:]
if not arguments or arguments[0] != "api":
    print("fake-gh: expected api mode", file=sys.stderr)
    raise SystemExit(97)
try:
    method = arguments[arguments.index("--method") + 1]
    endpoint = next(
        item
        for item in arguments
        if item == "graphql" or item.startswith("repos/")
    )
except (StopIteration, ValueError, IndexError):
    print("fake-gh: malformed argv", file=sys.stderr)
    raise SystemExit(97)
input_text = sys.stdin.read() if "--input" in arguments else ""
if (
    method != expected["method"]
    or endpoint != expected["endpoint"]
    or (
        expected["input"] is not None
        and input_text != expected["input"]
    )
):
    print(
        "fake-gh: call mismatch "
        + json.dumps(
            {
                "actual": [method, endpoint, input_text],
                "expected": [
                    expected["method"],
                    expected["endpoint"],
                    expected["input"],
                ],
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    raise SystemExit(97)
record = {
    "argv": arguments,
    "endpoint": endpoint,
    "git_environment": sorted(
        name for name in os.environ if name.startswith("GIT_")
    ),
    "gh_host": os.environ.get("GH_HOST"),
    "gh_repo": os.environ.get("GH_REPO"),
    "input": input_text,
    "method": method,
}
with log_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, sort_keys=True) + "\n")
state_path.write_text(str(index + 1), encoding="ascii")
if "raw_response_hex" in expected:
    sys.stdout.buffer.write(bytes.fromhex(expected["raw_response_hex"]))
    sys.stderr.write(expected.get("stderr", ""))
    raise SystemExit(expected.get("returncode", 0))
status = expected["status"]
reason = "Created" if status == 201 else "OK"
sys.stdout.write(f"HTTP/2 {status} {reason}\n")
if "payload" in expected:
    payload = expected["payload"]
    if expected.get("echo_body"):
        payload = dict(payload)
        payload["body"] = json.loads(input_text)["body"]
    if expected.get("echo_last_comment"):
        prior = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        body = next(
            json.loads(record["input"])["body"]
            for record in reversed(prior)
            if record["method"] == "POST"
            and "/comments" in record["endpoint"]
        )
        payload = [dict(comment) for comment in payload]
        payload[-1]["body"] = body
    sys.stdout.write("Content-Type: application/json\n\n")
    sys.stdout.write(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
else:
    sys.stdout.write("\n")
sys.stderr.write(expected.get("stderr", ""))
raise SystemExit(expected.get("returncode", 0))
"""


class LauncherSandbox:
    def __init__(self, root: Path):
        self.root = root
        self.scenario = root / "scenario.json"
        self.state = root / "state.txt"
        self.log = root / "calls.jsonl"
        self.site_marker = root / "sitecustomize-loaded"
        driver = root / "fake_gh.py"
        driver.write_text(FAKE_GH_DRIVER, encoding="utf-8")
        gh = root / "gh"
        gh.write_text(
            '#!/bin/sh\nexec /usr/bin/python3 -I "$FAKE_GH_DRIVER" "$@"\n',
            encoding="ascii",
        )
        gh.chmod(0o755)
        (root / "sitecustomize.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "Path(os.environ['SITE_MARKER']).write_text('loaded')\n",
            encoding="ascii",
        )
        self.environment = dict(os.environ)
        self.environment.update(
            {
                "FAKE_GH_DRIVER": str(driver),
                "FAKE_GH_LOG": str(self.log),
                "FAKE_GH_SCENARIO": str(self.scenario),
                "FAKE_GH_STATE": str(self.state),
                "GH_REPO": "attacker/repository",
                "GIT_DIR": str(root / "redirected.git"),
                "PATH": f"{root}:/usr/bin:/bin",
                "PYTHONPATH": str(root),
                "SITE_MARKER": str(self.site_marker),
            }
        )

    def run(
        self,
        mode: str,
        arguments: list[str],
        calls: list[dict],
    ) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
        self.scenario.write_text(
            json.dumps({"calls": calls}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for path in (self.state, self.log):
            path.unlink(missing_ok=True)
        completed = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                str(LAUNCHER),
                "pr-metadata",
                mode,
                *arguments,
            ],
            cwd=ROOT,
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        records = (
            [
                json.loads(line)
                for line in self.log.read_text(encoding="utf-8").splitlines()
            ]
            if self.log.exists()
            else []
        )
        return completed, records


class PullRequestMetadataTests(unittest.TestCase):
    def test_active_legacy_publisher_is_pending_without_a_runner(self):
        for pending_status in ("queued", "waiting", "pending", "requested"):
            with self.subTest(pending_status=pending_status):
                client = ScriptedClient()
                active = _run(101, 10, mode="full", active=True)
                active[1].append(_job(
                    "patch-release", job_id=10199, run_id=101,
                    status=pending_status, conclusion=None,
                    runner_name=None, started_at=None,
                ))
                _add_pr_states(client, _pr())
                _add_snapshot(client, [active])
                decision = pr_metadata.edit_metadata(
                    client, repository=REPOSITORY, pr_number=PR_NUMBER,
                    head_sha=HEAD, base_sha=BASE,
                    title="Updated contract", body=None, essential_reason=None,
                )
                self.assertEqual(decision.action, "deferred")
                self.assertFalse(decision.mutated)
                self.assertTrue(all(method == "GET" for method, _, _ in client.calls))

    def test_legacy_publisher_cannot_execute_or_remain_pending_after_completion(self):
        for active, runner, status in (
            (False, None, "queued"),
            (True, "GitHub Actions 1", "queued"),
            (True, "GitHub Actions 1", "in_progress"),
        ):
            with self.subTest(active=active, runner=runner, status=status):
                client = ScriptedClient()
                record = _run(101, 10, mode="full", active=active)
                record[1].append(_job(
                    "patch-release", job_id=10199, run_id=101,
                    status=status, conclusion=None, runner_name=runner,
                    started_at=None if runner is None else "2026-09-04T00:00:01Z",
                ))
                _add_snapshot(client, [record])
                state = pr_metadata._parse_pull_request_payload(
                    _pr(), REPOSITORY, PR_NUMBER
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.list_candidate_runs(client, state)

    def test_active_full_build_refuses_default_edit_without_mutation_or_cancel(self):
        client = ScriptedClient()
        active_full = _run(101, 10, mode="full", active=True)
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(client, [active_full])

        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )

        self.assertEqual(decision.action, "deferred")
        self.assertFalse(decision.mutated)
        self.assertEqual(decision.run_id, 101)
        self.assertIn("evidence-comment", decision.guidance[0])
        self.assertFalse(any(method != "GET" for method, _endpoint, _body in client.calls))
        self.assertFalse(any("/cancel" in endpoint for _method, endpoint, _body in client.calls))

    def test_mutation_path_still_blocks_older_active_full(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                _run(303, 13, mode="full"),
                _run(302, 12, mode="full", active=True),
                _run(101, 10, mode="full"),
            ],
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.run_id, 302)
        self.assertFalse(decision.mutated)

    def test_partial_active_full_graphs_defer_without_exact_set_errors(self):
        cases = {}
        queued_record, _ = _run(110, 14, mode="full", active=True)
        queued_record.update(
            {
                "status": "queued",
                "run_started_at": None,
                "updated_at": "2026-09-04T00:00:00Z",
            }
        )
        cases["queued-zero-job"] = (queued_record, [])

        one_record, one_jobs = _run(111, 15, mode="full", active=True)
        cases["one-job"] = (
            one_record,
            [
                job
                for job in one_jobs
                if job["name"] == "event-identity"
            ],
        )

        eight_record, eight_jobs = _run(112, 16, mode="full", active=True)
        cases["eight-job-current-shape"] = (
            eight_record,
            [job for job in eight_jobs if job["name"] != "summary"],
        )

        unknown_record, unknown_jobs = _run(
            113,
            17,
            mode="full",
            active=True,
        )
        unknown_jobs = unknown_jobs[:1]
        unknown_jobs[0]["name"] = "future-job"
        cases["unknown-partial"] = (unknown_record, unknown_jobs)

        for name, run_and_jobs in cases.items():
            with self.subTest(shape=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                _add_snapshot(client, [run_and_jobs])
                decision = pr_metadata.edit_metadata(
                    client,
                    repository=REPOSITORY,
                    pr_number=PR_NUMBER,
                    head_sha=HEAD,
                    base_sha=BASE,
                    title=None,
                    body="new body",
                    essential_reason=None,
                )
                self.assertEqual(decision.action, "deferred")
                self.assertFalse(decision.mutated)
                self.assertFalse(
                    any(
                        method not in {"GET", "POST"}
                        for method, _endpoint, _body in client.calls
                    )
                )

    def test_unbound_active_runs_block_with_empty_or_missing_bindings(self):
        cases = []
        queued_record, _queued_jobs = _run(
            110,
            14,
            mode="full",
            active=True,
        )
        queued_record.update(
            {
                "pull_requests": [],
                "run_started_at": None,
                "status": "queued",
                "updated_at": "2026-09-04T00:00:00Z",
            }
        )
        cases.append(("empty-queued-zero-jobs", queued_record, []))

        active_record, active_jobs = _run(
            111,
            15,
            mode="full",
            active=True,
        )
        del active_record["pull_requests"]
        active_jobs = [
            job
            for job in active_jobs
            if job["name"] == "event-identity"
        ]
        cases.append(("missing-in-progress-partial-jobs", active_record, active_jobs))

        for name, record, jobs in cases:
            with self.subTest(case=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(
                    client,
                    [(record, jobs), _run(101, 10, mode="full")],
                    copies=2,
                )
                client.add(
                    "PATCH",
                    _endpoint(f"pulls/{PR_NUMBER}"),
                    _pr(body="new body"),
                )
                decision = pr_metadata.edit_metadata(
                    client,
                    repository=REPOSITORY,
                    pr_number=PR_NUMBER,
                    head_sha=HEAD,
                    base_sha=BASE,
                    title=None,
                    body="new body",
                    essential_reason=None,
                )
                self.assertEqual(decision.action, "deferred")
                self.assertEqual(decision.run_id, record["id"])
                self.assertFalse(decision.mutated)
                self.assertEqual(
                    sum(
                        endpoint == _endpoint("actions/workflows/build.yml")
                        for _method, endpoint, _body in client.calls
                    ),
                    1,
                )
                self.assertFalse(
                    any(
                        method != "GET"
                        for method, _endpoint, _body in client.calls
                    )
                )

    def test_unbound_terminal_run_cannot_authorize_an_edit(self):
        client = ScriptedClient()
        record, jobs = _run(101, 10, mode="full")
        record["pull_requests"] = []
        _add_pr_states(client, _pr())
        _add_snapshot(client, [(record, jobs)])
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "refused")
        self.assertIsNone(decision.run_id)
        self.assertFalse(decision.mutated)

    def test_multiple_or_contradictory_run_bindings_fail_closed(self):
        cases = {}
        multiple_record, multiple_jobs = _run(101, 10, mode="full")
        multiple_record["pull_requests"].append(
            {
                "number": PR_NUMBER + 1,
                "head": {"sha": HEAD},
                "base": {"sha": "4" * 40},
            }
        )
        cases["multiple"] = (multiple_record, multiple_jobs, "ambiguous")
        contradictory_record, contradictory_jobs = _run(102, 11, mode="full")
        contradictory_record["pull_requests"][0]["head"]["sha"] = NEW_HEAD
        cases["contradictory"] = (
            contradictory_record,
            contradictory_jobs,
            "contradicts its head",
        )
        for name, (record, jobs, message) in cases.items():
            with self.subTest(case=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                _add_snapshot(client, [(record, jobs)])
                with self.assertRaisesRegex(
                    pr_metadata.MetadataEditError,
                    message,
                ):
                    pr_metadata.edit_metadata(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        title=None,
                        body="new body",
                        essential_reason=None,
                    )
                self.assertFalse(
                    any(
                        method != "GET"
                        for method, _endpoint, _body in client.calls
                    )
                )

    def test_proven_active_metadata_run_does_not_block_stable_edit(self):
        client = ScriptedClient()
        active_metadata = _run(
            202,
            11,
            mode="metadata-only",
            active=True,
        )
        successful_full = _run(101, 10, mode="full")
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(
            client,
            [active_metadata, successful_full],
            copies=2,
        )
        client.add(
            "PATCH",
            _endpoint(f"pulls/{PR_NUMBER}"),
            _pr(
                body="new body",
                updated_at="2026-09-04T00:00:05Z",
            ),
        )
        _add_edit_transaction(client, body="new body")
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "updated")

    def test_provided_and_changed_fields_drive_exact_patch_and_versions(self):
        cases = (
            (
                "title-changed-body-same",
                "New title",
                "Stable body",
                {"title": "New title"},
                {"body", "title"},
                {"title"},
            ),
            (
                "body-changed-title-same",
                "Stable title",
                "New body",
                {"body": "New body"},
                {"body", "title"},
                {"body"},
            ),
            (
                "both-changed",
                "New title",
                "New body",
                {"body": "New body", "title": "New title"},
                {"body", "title"},
                {"body", "title"},
            ),
        )
        for (
            name,
            title,
            body,
            expected_patch,
            expected_provided,
            expected_changed,
        ) in cases:
            with self.subTest(case=name):
                client = ScriptedClient()
                successful_full = _run(101, 10, mode="full")
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(client, [successful_full], copies=2)
                client.add(
                    "PATCH",
                    _endpoint(f"pulls/{PR_NUMBER}"),
                    _pr(
                        title=title,
                        body=body,
                        updated_at="2026-09-04T00:00:05Z",
                    ),
                )
                _add_edit_transaction(
                    client,
                    title=title,
                    body=body,
                )
                decision = pr_metadata.edit_metadata(
                    client,
                    repository=REPOSITORY,
                    pr_number=PR_NUMBER,
                    head_sha=HEAD,
                    base_sha=BASE,
                    title=title,
                    body=body,
                    essential_reason=None,
                )
                self.assertEqual(decision.action, "updated")
                patch = next(
                    call
                    for call in client.calls
                    if call[:2]
                    == ("PATCH", _endpoint(f"pulls/{PR_NUMBER}"))
                )
                self.assertEqual(patch[2], expected_patch)
                intent_call = next(
                    call
                    for call in client.calls
                    if call[:2]
                    == ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
                    and pr_metadata.INTENT_MARKER in call[2]["body"]
                )
                intent = pr_metadata._parse_intent_comment_body(
                    intent_call[2]["body"]
                )
                self.assertEqual(
                    {field.field for field in intent.provided_fields},
                    expected_provided,
                )
                self.assertEqual(
                    {field.field for field in intent.changed_fields},
                    expected_changed,
                )
                if "body" not in expected_changed:
                    self.assertEqual(
                        decision.confirmation_comment_id,
                        402,
                    )

    def test_both_changed_fields_require_both_version_advances(self):
        pre = _metadata_version()
        receipt = _receipt(
            provided_fields={
                "body": _sha256("New body"),
                "title": _sha256("New title"),
            },
            changed_fields={
                "body": _sha256("New body"),
                "title": _sha256("New title"),
            },
            pre_title="Stable title",
            pre_body="Stable body",
            pre_metadata_sha256=_metadata_sha256(
                "Stable title",
                "Stable body",
            ),
            target_metadata_sha256=_metadata_sha256(
                "New title",
                "New body",
            ),
            pre_version=pre,
        )
        state = pr_metadata._parse_pull_request_payload(
            _pr(title="New title", body="New body"),
            REPOSITORY,
            PR_NUMBER,
        )
        title_only = replace(
            pre,
            title_event_id="RTE_2",
            title_event_created_at="2026-09-04T00:00:05Z",
            title_previous="Stable title",
            title_current="New title",
        )
        body_only = replace(
            pre,
            body_last_edited_at="2026-09-04T00:00:05Z",
            body_edit_total_count=pre.body_edit_total_count + 1,
            body_edit_id="UCE_2",
            body_edit_created_at="2026-09-04T00:00:05Z",
            body_edit_edited_at="2026-09-04T00:00:05Z",
            body_edit_updated_at="2026-09-04T00:00:05Z",
        )
        for name, version in (
            ("title-only", title_only),
            ("body-only", body_only),
        ):
            with self.subTest(case=name):
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._confirmation_for_target(
                        receipt,
                        intent_comment_id=401,
                        state=state,
                        version=version,
                    )

    def test_essential_override_updates_metadata_and_derives_reconciliation(self):
        client = ScriptedClient()
        active_full = _run(101, 10, mode="full", active=True)
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(client, [active_full], copies=2)
        client.add(
            "PATCH",
            _endpoint(f"pulls/{PR_NUMBER}"),
            _pr(title="Essential correction"),
        )
        _add_edit_transaction(client, title="Essential correction")

        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title="Essential correction",
            body=None,
            essential_reason="Correct the frozen compatibility contract",
        )

        self.assertEqual(decision.action, "updated")
        self.assertTrue(decision.mutated)
        self.assertIn("reconcile", decision.guidance[0])
        mutations = [
            call
            for call in client.calls
            if call[0] != "GET" and call[1] != "graphql"
        ]
        self.assertEqual(
            [(method, endpoint) for method, endpoint, _body in mutations],
            [
                ("POST", _endpoint(f"issues/{PR_NUMBER}/comments")),
                ("PATCH", _endpoint(f"pulls/{PR_NUMBER}")),
                ("POST", _endpoint(f"issues/{PR_NUMBER}/comments")),
            ],
        )
        self.assertEqual(
            mutations[1][2],
            {"title": "Essential correction"},
        )
        self.assertEqual(
            pr_metadata._parse_intent_comment_body(mutations[0][2]["body"])
            .provided_fields,
            (
                pr_metadata.EditFieldDigest(
                    "title",
                    _sha256("Essential correction"),
                ),
            ),
        )

    def test_empty_essential_reason_is_rejected(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(client, [_run(101, 10, mode="full", active=True)])
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "non-whitespace",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title="Essential correction",
                body=None,
                essential_reason=" \t",
            )
        self.assertFalse(any(method != "GET" for method, _endpoint, _body in client.calls))

    def test_essential_edit_updates_terminal_failed_full_without_success_credit(self):
        for conclusion in ("failure", "cancelled"):
            with self.subTest(conclusion=conclusion):
                client = ScriptedClient()
                failed_full = _run(101, 10, mode="full", success=False)
                failed_full[0]["conclusion"] = conclusion
                summary = next(
                    job for job in failed_full[1] if job["name"] == "summary"
                )
                summary["conclusion"] = "failure"
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(client, [failed_full], copies=2)
                client.add(
                    "PATCH",
                    _endpoint(f"pulls/{PR_NUMBER}"),
                    _pr(title="Corrected contract"),
                )
                _add_edit_transaction(client, title="Corrected contract")

                decision = pr_metadata.edit_metadata(
                    client,
                    repository=REPOSITORY,
                    pr_number=PR_NUMBER,
                    head_sha=HEAD,
                    base_sha=BASE,
                    title="Corrected contract",
                    body=None,
                    essential_reason="Replace the invalid implementation contract",
                )

                self.assertEqual(decision.action, "updated")
                self.assertTrue(decision.mutated)
                self.assertIn("reconcile", decision.guidance[0])
                self.assertEqual(
                    [
                        method for method, endpoint, _ in client.calls
                        if endpoint != "graphql" and method != "GET"
                    ],
                    ["POST", "PATCH", "POST"],
                )
                self.assertFalse(
                    any(
                        "/cancel" in endpoint or "/dispatches" in endpoint
                        for _, endpoint, _ in client.calls
                    )
                )
                observed = ScriptedClient()
                _add_snapshot(observed, [failed_full])
                state = pr_metadata._parse_pull_request_payload(
                    _pr(), REPOSITORY, PR_NUMBER
                )
                actual = pr_metadata.list_candidate_runs(observed, state)[0]
                self.assertEqual(actual.conclusion, conclusion)
                with self.assertRaisesRegex(
                    pr_metadata.MetadataEditError, "not successful"
                ):
                    pr_metadata.require_full_success(actual)

                default = ScriptedClient()
                _add_pr_states(default, _pr())
                _add_snapshot(default, [failed_full])
                with self.assertRaisesRegex(
                    pr_metadata.MetadataEditError, "not successful"
                ):
                    pr_metadata.edit_metadata(
                        default,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        title="Corrected contract",
                        body=None,
                        essential_reason=None,
                    )
                self.assertTrue(all(method == "GET" for method, _, _ in default.calls))

    def test_essential_edit_rejects_other_terminal_outcomes_at_both_snapshots(self):
        for conclusion in (
            "action_required", "neutral", "skipped", "stale",
            "startup_failure", "timed_out",
        ):
            for snapshot in ("initial", "refreshed"):
                with self.subTest(conclusion=conclusion, snapshot=snapshot):
                    client = ScriptedClient()
                    allowed = _run(101, 10, mode="full", success=False)
                    next(
                        job for job in allowed[1] if job["name"] == "summary"
                    )["conclusion"] = "failure"
                    unsupported = copy.deepcopy(allowed)
                    unsupported[0]["conclusion"] = conclusion
                    _add_pr_states(client, _pr(), _pr())
                    _add_snapshot(
                        client,
                        [unsupported if snapshot == "initial" else allowed],
                    )
                    _add_snapshot(client, [unsupported])
                    client.add(
                        "PATCH", _endpoint(f"pulls/{PR_NUMBER}"),
                        _pr(title="Corrected contract"),
                    )
                    _add_edit_transaction(client, title="Corrected contract")
                    with self.assertRaises(pr_metadata.MetadataEditError):
                        pr_metadata.edit_metadata(
                            client,
                            repository=REPOSITORY,
                            pr_number=PR_NUMBER,
                            head_sha=HEAD,
                            base_sha=BASE,
                            title="Corrected contract",
                            body=None,
                            essential_reason="Correct the contract",
                        )
                    self.assertTrue(
                        all(method == "GET" for method, _, _ in client.calls)
                    )

    def test_essential_terminal_edit_rejects_active_jobs_at_both_snapshots(self):
        for conclusion in ("failure", "cancelled"):
            for job_status in ("queued", "in_progress"):
                for snapshot in ("initial", "refreshed"):
                    with self.subTest(
                        conclusion=conclusion, job_status=job_status, snapshot=snapshot
                    ):
                        client = ScriptedClient()
                        terminal = _run(101, 10, mode="full", success=False)
                        terminal[0]["conclusion"] = conclusion
                        next(
                            job for job in terminal[1] if job["name"] == "summary"
                        )["conclusion"] = "failure"
                        inconsistent = copy.deepcopy(terminal)
                        job = next(
                            job for job in inconsistent[1] if job["name"] == "host-tests"
                        )
                        job.update(_job(
                            "host-tests", job_id=job["id"], run_id=101,
                            status=job_status, conclusion=None,
                            runner_name=None if job_status == "queued" else "GitHub Actions 1",
                            started_at=None if job_status == "queued" else job["started_at"],
                        ))
                        _add_pr_states(client, _pr(), _pr())
                        _add_snapshot(
                            client,
                            [inconsistent if snapshot == "initial" else terminal],
                        )
                        _add_snapshot(client, [inconsistent])
                        client.add(
                            "PATCH", _endpoint(f"pulls/{PR_NUMBER}"),
                            _pr(title="Corrected contract"),
                        )
                        _add_edit_transaction(client, title="Corrected contract")
                        with self.assertRaises(pr_metadata.MetadataEditError):
                            pr_metadata.edit_metadata(
                                client, repository=REPOSITORY, pr_number=PR_NUMBER,
                                head_sha=HEAD, base_sha=BASE,
                                title="Corrected contract", body=None,
                                essential_reason="Correct the contract",
                            )
                        self.assertTrue(
                            all(method == "GET" for method, _, _ in client.calls)
                        )

    def test_body_only_no_op_without_pair_is_refused(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(
            client,
            [
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
            copies=2,
        )
        _add_metadata_versions(
            client,
            (_pr(), _metadata_version()),
        )
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [],
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title="Stable title",
            body="Stable body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "refused")
        self.assertFalse(decision.mutated)
        self.assertFalse(
            any(
                method == "PATCH"
                or "/comments" in endpoint and method == "POST"
                for method, endpoint, _body in client.calls
            )
        )

    def test_identity_is_revalidated_immediately_before_edit(self):
        client = ScriptedClient()
        successful_full = _run(101, 10, mode="full")
        _add_pr_states(client, _pr(), _pr(head=NEW_HEAD))
        _add_snapshot(client, [successful_full])

        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "identity changed",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="new body",
                essential_reason=None,
            )
        self.assertFalse(any(method == "PATCH" for method, _endpoint, _body in client.calls))

    def test_default_edit_defers_when_second_snapshot_becomes_active(self):
        client = ScriptedClient()
        initial_full = _run(101, 10, mode="full")
        active_rerun = _run(101, 10, mode="full", active=True, attempt=2)
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(client, [initial_full])
        _add_snapshot(client, [active_rerun])

        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )

        self.assertEqual(decision.action, "deferred")
        self.assertFalse(decision.mutated)
        self.assertEqual(decision.run_id, 101)
        self.assertFalse(any(method == "PATCH" for method, _endpoint, _body in client.calls))

    def test_second_snapshot_partial_materialization_defers_not_errors(self):
        client = ScriptedClient()
        successful_full = _run(101, 10, mode="full")
        partial_record, partial_jobs = _run(
            202,
            11,
            mode="full",
            active=True,
        )
        partial_jobs = [
            job
            for job in partial_jobs
            if job["name"] == "event-identity"
        ]
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(client, [successful_full])
        _add_snapshot(
            client,
            [(partial_record, partial_jobs), successful_full],
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "deferred")
        self.assertFalse(decision.mutated)
        self.assertFalse(any(method == "PATCH" for method, _endpoint, _body in client.calls))

    def test_second_snapshot_unbound_active_run_blocks_mutation(self):
        client = ScriptedClient()
        successful_full = _run(101, 10, mode="full")
        unbound_record, unbound_jobs = _run(
            202,
            11,
            mode="full",
            active=True,
        )
        unbound_record["pull_requests"] = []
        unbound_jobs = [
            job
            for job in unbound_jobs
            if job["name"] == "event-identity"
        ]
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(client, [successful_full])
        _add_snapshot(
            client,
            [(unbound_record, unbound_jobs), successful_full],
        )
        client.add(
            "PATCH",
            _endpoint(f"pulls/{PR_NUMBER}"),
            _pr(body="new body"),
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.run_id, 202)
        self.assertFalse(decision.mutated)
        self.assertFalse(
            any(
                method not in {"GET", "POST"}
                for method, _endpoint, _body in client.calls
            )
        )

    def test_second_snapshot_active_metadata_graph_growth_defers(self):
        client = ScriptedClient()
        successful_full = _run(101, 10, mode="full")
        active_record, active_jobs = _run(
            202,
            11,
            mode="metadata-only",
            active=True,
        )
        classifier_only = [
            job
            for job in active_jobs
            if job["name"] == candidate_evidence.METADATA_CLASSIFIER
        ]
        grown_jobs = [
            job
            for job in active_jobs
            if job["name"]
            in {
                "event-identity",
                "event-router",
                candidate_evidence.METADATA_CLASSIFIER,
            }
        ]
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(
            client,
            [(active_record, classifier_only), successful_full],
        )
        _add_snapshot(
            client,
            [(active_record, grown_jobs), successful_full],
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "deferred")
        self.assertFalse(decision.mutated)
        self.assertFalse(any(method == "PATCH" for method, _endpoint, _body in client.calls))

    def test_default_edit_defers_when_pr_metadata_changes_before_patch(self):
        client = ScriptedClient()
        successful_full = _run(101, 10, mode="full")
        _add_pr_states(
            client,
            _pr(),
            _pr(title="Concurrent title"),
        )
        _add_snapshot(client, [successful_full])
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "deferred")
        self.assertFalse(any(method == "PATCH" for method, _endpoint, _body in client.calls))

    def test_post_edit_identity_race_reports_deterministic_recovery(self):
        client = ScriptedClient()
        successful_full = _run(101, 10, mode="full")
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(client, [successful_full], copies=2)
        _add_edit_transaction(
            client,
            body="essential correction",
        )
        client.add(
            "PATCH",
            _endpoint(f"pulls/{PR_NUMBER}"),
            _pr(
                head=NEW_HEAD,
                body="essential correction",
            ),
        )

        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "identity changed",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="essential correction",
                essential_reason="Correct the frozen delivery contract",
            )
        mutations = [call for call in client.calls if call[0] != "GET"]
        self.assertIn(
            (
                "PATCH",
                _endpoint(f"pulls/{PR_NUMBER}"),
                {"body": "essential correction"},
            ),
            mutations,
        )

    def test_pull_request_mutation_response_must_attest_requested_result(self):
        wrong_repository_id = _pr(body="new body")
        wrong_repository_id["base"]["repo"]["id"] = REPOSITORY_ID + 1
        cases = (
            (
                "body",
                None,
                "new body",
                _pr(body="Stable body"),
                "complete target metadata",
            ),
            (
                "title",
                "New title",
                None,
                _pr(title="Stable title"),
                "complete target metadata",
            ),
            (
                "repository",
                None,
                "new body",
                {
                    **_pr(body="new body"),
                    "url": "https://api.github.com/repos/other/repo/pulls/199",
                },
                "URL identity drifted",
            ),
            (
                "repository-id",
                None,
                "new body",
                wrong_repository_id,
                "repository/head-ref identity drifted",
            ),
            (
                "updated-at",
                None,
                "new body",
                _pr(
                    body="new body",
                    updated_at="2026-09-03T23:59:59Z",
                ),
                "updated_at regressed",
            ),
        )
        for name, title, body, response, message in cases:
            with self.subTest(mismatch=name):
                client = ScriptedClient()
                successful_full = _run(101, 10, mode="full")
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(client, [successful_full], copies=2)
                _add_edit_transaction(
                    client,
                    title=title,
                    body=body,
                )
                client.add(
                    "PATCH",
                    _endpoint(f"pulls/{PR_NUMBER}"),
                    response,
                )
                with self.assertRaisesRegex(
                    pr_metadata.MetadataEditError,
                    message,
                ):
                    pr_metadata.edit_metadata(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        title=title,
                        body=body,
                        essential_reason=None,
                    )

    def test_mutation_response_is_authoritative_without_stale_post_patch_get(self):
        client = ScriptedClient()
        successful_full = _run(101, 10, mode="full")
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(client, [successful_full], copies=2)
        client.add(
            "PATCH",
            _endpoint(f"pulls/{PR_NUMBER}"),
            _pr(
                body="new body",
                updated_at="2026-09-04T00:00:05Z",
            ),
        )
        _add_edit_transaction(client, body="new body")

        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "updated")
        self.assertEqual(decision.intent_comment_id, 401)
        self.assertEqual(decision.confirmation_comment_id, 402)
        self.assertEqual(
            decision.intent_comment_url,
            f"https://github.com/{REPOSITORY}/pull/{PR_NUMBER}"
            "#issuecomment-401",
        )
        self.assertEqual(
            decision.confirmation_comment_url,
            f"https://github.com/{REPOSITORY}/pull/{PR_NUMBER}"
            "#issuecomment-402",
        )
        transaction_calls = [
            call
            for call in client.calls
            if call[:2]
            == ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
        ]
        intent = pr_metadata._parse_intent_comment_body(
            transaction_calls[0][2]["body"]
        )
        self.assertEqual(
            intent.provided_fields,
            (pr_metadata.EditFieldDigest("body", _sha256("new body")),),
        )
        self.assertEqual(
            intent.target_metadata_sha256,
            _metadata_sha256("Stable title", "new body"),
        )
        self.assertRegex(intent.nonce, r"^[0-9a-f]{64}$")
        confirmation = pr_metadata._parse_confirmation_comment_body(
            transaction_calls[1][2]["body"]
        )
        self.assertEqual(confirmation.intent_comment_id, 401)
        self.assertEqual(confirmation.intent_nonce, intent.nonce)
        self.assertEqual(
            confirmation.metadata_sha256,
            intent.target_metadata_sha256,
        )
        pr_gets = [
            call
            for call in client.calls
            if call[:2] == ("GET", _endpoint(f"pulls/{PR_NUMBER}"))
        ]
        self.assertEqual(len(pr_gets), 2)
        patch_index = next(
            index
            for index, call in enumerate(client.calls)
            if call[:2] == ("PATCH", _endpoint(f"pulls/{PR_NUMBER}"))
        )
        self.assertTrue(
            all(
                index < patch_index
                for index, call in enumerate(client.calls)
                if call[:2] == ("GET", _endpoint(f"pulls/{PR_NUMBER}"))
            )
        )

    def test_transaction_comment_creation_responses_must_attest_authority(self):
        cases = (
            (
                "body",
                {"body": f"{pr_metadata.INTENT_MARKER}\n{{}}\n"},
            ),
            (
                "identity",
                {"id": 402},
            ),
            (
                "non-owner",
                {
                    "user": {
                        "id": OWNER_ID + 1,
                        "login": "attacker",
                        "type": "User",
                        "site_admin": False,
                    },
                    "author_association": "NONE",
                },
            ),
            (
                "edited",
                {"updated_at": "2026-09-04T00:00:07Z"},
            ),
        )
        for name, changes in cases:
            with self.subTest(case=name):
                client = ScriptedClient()
                successful_full = _run(101, 10, mode="full")
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(client, [successful_full], copies=2)
                client.add(
                    "PATCH",
                    _endpoint(f"pulls/{PR_NUMBER}"),
                    _pr(
                        body="new body",
                        updated_at="2026-09-04T00:00:05Z",
                    ),
                )
                _add_edit_transaction(
                    client,
                    body="new body",
                    response_changes=changes,
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.edit_metadata(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        title=None,
                        body="new body",
                        essential_reason=None,
                    )
                self.assertFalse(
                    any(
                        "/cancel" in endpoint or "/dispatches" in endpoint
                        for _method, endpoint, _body in client.calls
                    )
                )

    def test_retry_recovers_unmatched_intent_after_patch(self):
        failed = ScriptedClient()
        successful_full = _run(101, 10, mode="full")
        _add_pr_states(failed, _pr(), _pr())
        _add_snapshot(failed, [successful_full], copies=2)
        failed.add(
            "PATCH",
            _endpoint(f"pulls/{PR_NUMBER}"),
            _pr(
                body="new body",
                updated_at="2026-09-04T00:00:05Z",
            ),
        )
        _add_edit_transaction(failed, body="new body")
        confirmation_route = (
            "POST",
            _endpoint(f"issues/{PR_NUMBER}/comments"),
        )
        failed.routes[confirmation_route][1] = pr_metadata.MetadataEditError(
            "metadata edit confirmation comment creation request timed out"
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "timed out",
        ):
            pr_metadata.edit_metadata(
                failed,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="new body",
                essential_reason=None,
            )
        self.assertTrue(
            any(method == "PATCH" for method, _endpoint, _body in failed.calls)
        )
        intent_call = next(
            call
            for call in failed.calls
            if call[:2] == confirmation_route
            and pr_metadata.INTENT_MARKER in call[2]["body"]
        )
        intent = pr_metadata._parse_intent_comment_body(
            intent_call[2]["body"]
        )

        target_state = _pr(
            body="new body",
            updated_at="2026-09-04T00:00:05Z",
        )
        pre_version = _metadata_version()
        target_version = _metadata_version(
            body_last_edited_at="2026-09-04T00:00:05Z",
        )
        client = ScriptedClient()
        _add_pr_states(client, target_state, target_state)
        _add_snapshot(
            client,
            [
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
            copies=2,
        )
        _add_metadata_versions(
            client,
            (target_state, target_version),
            (target_state, target_version),
        )
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [_intent_comment(intent)],
            [_intent_comment(intent)],
        )

        def confirmation_response(
            *,
            method: str,
            endpoint: str,
            body: dict[str, object] | None,
        ) -> dict:
            del method, endpoint
            return _comment(
                402,
                body["body"],
                created_at="2026-09-04T00:00:06Z",
                updated_at="2026-09-04T00:00:06Z",
            )

        client.add(
            "POST",
            _endpoint(f"issues/{PR_NUMBER}/comments"),
            confirmation_response,
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "recovered")
        self.assertFalse(
            any(method == "PATCH" for method, _endpoint, _body in client.calls)
        )
        self.assertEqual(decision.intent_comment_id, 401)
        self.assertEqual(decision.confirmation_comment_id, 402)

    def test_retry_unmatched_intent_holds_pre_state_without_duplicate_patch(self):
        pre_state = _pr()
        target_state = _pr(
            body="new body",
            updated_at="2026-09-04T00:00:05Z",
        )
        pre_version = _metadata_version()
        target_version = _metadata_version(
            body_last_edited_at="2026-09-04T00:00:05Z",
        )
        intent = _receipt(
            provided_fields={"body": _sha256("new body")},
            pre_title="Stable title",
            pre_body="Stable body",
            pre_metadata_sha256=_metadata_sha256(
                "Stable title",
                "Stable body",
            ),
            target_metadata_sha256=_metadata_sha256(
                "Stable title",
                "new body",
            ),
            pre_version=pre_version,
        )
        client = ScriptedClient()
        _add_pr_states(client, pre_state, pre_state, pre_state)
        _add_snapshot(client, [_run(101, 10, mode="full")], copies=3)
        _add_metadata_versions(
            client,
            (pre_state, pre_version),
            (pre_state, pre_version),
            (target_state, target_version),
        )
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [_intent_comment(intent)],
            [_intent_comment(intent)],
            [_intent_comment(intent)],
        )
        client.add(
            "PATCH",
            _endpoint(f"pulls/{PR_NUMBER}"),
            target_state,
        )

        def confirmation_response(
            *,
            method: str,
            endpoint: str,
            body: dict[str, object] | None,
        ) -> dict:
            del method, endpoint
            return _comment(
                402,
                body["body"],
                created_at="2026-09-04T00:00:06Z",
                updated_at="2026-09-04T00:00:06Z",
            )

        client.add(
            "POST",
            _endpoint(f"issues/{PR_NUMBER}/comments"),
            confirmation_response,
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.intent_comment_id, 401)
        self.assertFalse(decision.mutated)
        self.assertIsNone(decision.confirmation_comment_id)
        self.assertIsNone(decision.abort_comment_id)
        self.assertFalse(any(call[0] == "PATCH" for call in client.calls))
        transaction_posts = [
            call
            for call in client.calls
            if call[:2]
            == ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
        ]
        self.assertEqual(transaction_posts, [])

    def test_post_intent_refresh_requires_complete_unchanged_selected_intent(self):
        comments_route = (
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
        )
        posts_route = ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
        target = _pr(body="new body", updated_at="2026-09-04T00:00:05Z")
        for retry in (False, True):
            for abort in (False, True):
                for drift in INTENT_DRIFTS:
                    with self.subTest(retry=retry, abort=abort, drift=drift):
                        client = ScriptedClient()
                        _add_pr_states(client, _pr(), _pr())
                        _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
                        _add_edit_transaction(client, body="new body")
                        client.add("PATCH", _endpoint(f"pulls/{PR_NUMBER}"), target)
                        if retry:
                            intent = _receipt(
                                pre_body="Stable body",
                                pre_version=_metadata_version(),
                                provided_fields={"body": _sha256("new body")},
                                target_metadata_sha256=_metadata_sha256(
                                    "Stable title", "new body"
                                ),
                            )
                            client.routes[comments_route] = [
                                [_intent_comment(intent)] for _ in range(6)
                            ]
                            client.routes[posts_route].pop(0)
                        fresh_page = client.routes[comments_route][2]

                        def changed_page(**kwargs):
                            page = (
                                fresh_page(**kwargs) if callable(fresh_page) else fresh_page
                            )
                            return _drifted_intent_page(page[0], drift)

                        client.routes[comments_route][2:4] = [changed_page, changed_page]
                        if abort:
                            observed = _pr(head=NEW_HEAD)
                            client.routes[("POST", "graphql")][1] = _response(
                                _graphql_payload(observed, _metadata_version())
                            )
                        with self.assertRaises(pr_metadata.MetadataEditError):
                            pr_metadata.edit_metadata(
                                client,
                                repository=REPOSITORY,
                                pr_number=PR_NUMBER,
                                head_sha=HEAD,
                                base_sha=BASE,
                                title=None,
                                body="new body",
                                essential_reason=None,
                            )
                        self.assertFalse(any(call[0] == "PATCH" for call in client.calls))
                        posts = [
                            call[2]["body"] for call in client.calls
                            if call[:2] == posts_route
                        ]
                        self.assertEqual(len(posts), 0 if retry else 1)
                        self.assertTrue(
                            all(body.startswith(pr_metadata.INTENT_MARKER) for body in posts)
                        )

    def test_confirmation_refresh_rejects_intent_drift_after_patch_or_on_recovery(self):
        comments_route = (
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
        )
        posts_route = ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
        target = _pr(body="new body", updated_at="2026-09-04T00:00:05Z")
        target_version = _metadata_version(body_last_edited_at="2026-09-04T00:00:05Z")
        for recovery in (False, True):
            for drift in INTENT_DRIFTS:
                with self.subTest(recovery=recovery, drift=drift):
                    client = ScriptedClient()
                    state = target if recovery else _pr()
                    _add_pr_states(client, state, state)
                    _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
                    _add_edit_transaction(client, body="new body")
                    client.add("PATCH", _endpoint(f"pulls/{PR_NUMBER}"), target)
                    if recovery:
                        intent = _receipt(
                            pre_body="Stable body",
                            pre_version=_metadata_version(),
                            provided_fields={"body": _sha256("new body")},
                            target_metadata_sha256=_metadata_sha256(
                                "Stable title", "new body"
                            ),
                        )
                        client.routes[comments_route] = [
                            [_intent_comment(intent)] for _ in range(4)
                        ]
                        client.routes[posts_route].pop(0)
                        client.routes[("POST", "graphql")] = [
                            _response(_graphql_payload(target, target_version))
                            for _ in range(2)
                        ]
                    index = 2 if recovery else 4
                    fresh_page = client.routes[comments_route][index]

                    def changed_page(**kwargs):
                        page = (
                            fresh_page(**kwargs) if callable(fresh_page) else fresh_page
                        )
                        return _drifted_intent_page(page[0], drift)

                    client.routes[comments_route][index:index + 2] = [
                        changed_page, changed_page
                    ]
                    with self.assertRaises(pr_metadata.MetadataEditError):
                        pr_metadata.edit_metadata(
                            client,
                            repository=REPOSITORY,
                            pr_number=PR_NUMBER,
                            head_sha=HEAD,
                            base_sha=BASE,
                            title=None,
                            body="new body",
                            essential_reason=None,
                        )
                    patches = [call for call in client.calls if call[0] == "PATCH"]
                    self.assertEqual(len(patches), 0 if recovery else 1)
                    posts = [
                        call[2]["body"] for call in client.calls
                        if call[:2] == posts_route
                    ]
                    self.assertEqual(len(posts), 0 if recovery else 1)
                    self.assertTrue(
                        all(body.startswith(pr_metadata.INTENT_MARKER) for body in posts)
                    )

    def test_confirmation_refresh_preserves_observed_terminal_precedence(self):
        comments_route = (
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
        )
        posts_route = ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
        target = _pr(body="new body", updated_at="2026-09-04T00:00:05Z")
        target_version = _metadata_version(body_last_edited_at="2026-09-04T00:00:05Z")
        for recovery in (False, True):
            for terminal in ("confirmation", "abort"):
                with self.subTest(recovery=recovery, terminal=terminal):
                    client = ScriptedClient()
                    state = target if recovery else _pr()
                    _add_pr_states(client, state, state)
                    _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
                    _add_edit_transaction(client, body="new body")
                    client.add("PATCH", _endpoint(f"pulls/{PR_NUMBER}"), target)
                    if recovery:
                        intent = _receipt(
                            pre_body="Stable body",
                            pre_version=_metadata_version(),
                            provided_fields={"body": _sha256("new body")},
                            target_metadata_sha256=_metadata_sha256(
                                "Stable title", "new body"
                            ),
                        )
                        client.routes[comments_route] = [
                            [_intent_comment(intent)] for _ in range(4)
                        ]
                        client.routes[posts_route].pop(0)
                        client.routes[("POST", "graphql")] = [
                            _response(_graphql_payload(target, target_version))
                        ]
                    index = 2 if recovery else 4
                    fresh_page = client.routes[comments_route][index]

                    def terminated_page(**kwargs):
                        page = (
                            fresh_page(**kwargs) if callable(fresh_page) else fresh_page
                        )
                        selected = pr_metadata._parse_intent_comment_body(page[0]["body"])
                        terminal_comment = (
                            _confirmation_comment(
                                _confirmation(selected, version=target_version),
                                created_at="2026-09-04T00:00:06Z",
                            )
                            if terminal == "confirmation"
                            else _abort_comment(
                                _abort(
                                    selected,
                                    observed_state=target,
                                    observed_version=target_version,
                                ),
                                created_at="2026-09-04T00:00:06Z",
                            )
                        )
                        successor = _intent_comment(
                            replace(
                                selected,
                                nonce="d" * 64 if selected.nonce != "d" * 64 else "e" * 64,
                            ),
                            comment_id=404,
                            created_at="2026-09-04T00:00:06Z",
                        )
                        return [page[0], terminal_comment, successor]

                    client.routes[comments_route][index:index + 2] = [
                        terminated_page, terminated_page
                    ]
                    decision = pr_metadata.edit_metadata(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        title=None,
                        body="new body",
                        essential_reason=None,
                    )
                    self.assertEqual(decision.action, "deferred")
                    self.assertEqual(decision.mutated, not recovery)
                    self.assertEqual(
                        (decision.confirmation_comment_id, decision.abort_comment_id),
                        (402, None) if terminal == "confirmation" else (None, 403),
                    )
                    patches = [call for call in client.calls if call[0] == "PATCH"]
                    self.assertEqual(len(patches), 0 if recovery else 1)
                    posts = [
                        call[2]["body"] for call in client.calls
                        if call[:2] == posts_route
                    ]
                    self.assertEqual(len(posts), 0 if recovery else 1)
                    self.assertTrue(
                        all(body.startswith(pr_metadata.INTENT_MARKER) for body in posts)
                    )

    def test_post_intent_confirmation_defers_without_conflicting_abort(self):
        pre_state = _pr()
        pre_version = _metadata_version()
        intent = _receipt(
            provided_fields={"body": _sha256("new body")},
            pre_body="Stable body",
            pre_version=pre_version,
            target_metadata_sha256=_metadata_sha256("Stable title", "new body"),
        )
        confirmation = _confirmation(
            intent, version=_metadata_version(body_last_edited_at="2026-09-04T00:00:05Z")
        )
        original_comments = [
            _intent_comment(intent),
            _confirmation_comment(confirmation, created_at="2026-09-04T00:00:06Z"),
        ]
        successful_full = _run(101, 10, mode="full")
        for run_drift, successor in ((False, False), (True, False), (False, True)):
            with self.subTest(run_drift=run_drift, successor=successor):
                client = ScriptedClient()
                _add_pr_states(client, pre_state, pre_state)
                _add_snapshot(client, [successful_full], copies=2)
                _add_snapshot(
                    client,
                    (
                        [_run(102, 11, mode="full", active=True), successful_full]
                        if run_drift
                        else [successful_full]
                    ),
                )
                _add_metadata_versions(client, (pre_state, pre_version))
                observed = copy.deepcopy(original_comments)
                if successor:
                    observed.append(
                        _intent_comment(
                            replace(intent, nonce="b" * 64),
                            comment_id=404,
                            created_at="2026-09-04T00:00:07Z",
                        )
                    )
                client.add_stable_comment_pages(
                    "GET",
                    _query(
                        f"issues/{PR_NUMBER}/comments",
                        [("per_page", "100"), ("page", "1")],
                    ),
                    [_intent_comment(intent)],
                    observed,
                )
                client.add(
                    "POST",
                    _endpoint(f"issues/{PR_NUMBER}/comments"),
                    lambda *, body, **_kwargs: _comment(
                        405,
                        body["body"],
                        created_at="2026-09-04T00:00:04Z",
                        updated_at="2026-09-04T00:00:04Z",
                    ),
                )
                decision = pr_metadata.edit_metadata(
                    client,
                    repository=REPOSITORY,
                    pr_number=PR_NUMBER,
                    head_sha=HEAD,
                    base_sha=BASE,
                    title=None,
                    body="new body",
                    essential_reason=None,
                )
                self.assertEqual(decision.action, "deferred")
                self.assertFalse(decision.mutated)
                self.assertEqual(decision.confirmation_comment_id, 402)
                self.assertIsNone(decision.abort_comment_id)
                self.assertIn("--confirmation-comment-id", decision.guidance[0])
                self.assertEqual(decision.guidance[0][-1], "402")
                self.assertFalse(
                    any(
                        method == "PATCH"
                        or endpoint == _endpoint(f"issues/{PR_NUMBER}/comments")
                        for method, endpoint, _body in client.calls
                    )
                )

        client = ScriptedClient()
        target_state = _pr(body="new body", updated_at="2026-09-04T00:00:05Z")
        _add_pr_states(client, target_state)
        _add_snapshot(
            client,
            [_run(202, 11, mode="metadata-only", success=True,
                  metadata_event_payload=_metadata_event_payload(pre_state, target_state)),
             successful_full],
        )
        decision = _reconcile(
            client,
            receipt=intent,
            confirmation=confirmation,
            comments=original_comments,
            version=confirmation.metadata_version,
            state=target_state,
        )
        self.assertEqual(decision.action, "complete")
        self.assertEqual(decision.run_id, 202)

    def test_selected_confirmation_reconciles_with_unconfirmed_successors(self):
        receipt = _receipt()
        confirmation = _confirmation(receipt)
        for created_at in ("2026-09-04T00:00:01Z", "2026-09-04T00:00:03Z"):
            with self.subTest(successor_created_at=created_at):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                _add_snapshot(
                    client,
                    [
                        _run(202, 11, mode="metadata-only", success=True),
                        _run(101, 10, mode="full"),
                    ],
                )
                decision = _reconcile(
                    client,
                    receipt=receipt,
                    confirmation=confirmation,
                    comments=[
                        _intent_comment(receipt),
                        _confirmation_comment(confirmation),
                        _intent_comment(
                            replace(receipt, nonce="b" * 64),
                            comment_id=403,
                            created_at=created_at,
                        ),
                    ],
                )
                self.assertEqual(decision.action, "complete")
                self.assertEqual(decision.run_id, 202)

    def test_created_intent_reports_its_mutation_after_observed_terminal(self):
        for terminal in ("confirmation", "abort"):
            with self.subTest(terminal=terminal):
                client = ScriptedClient()
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(client, [_run(101, 10, mode="full")], copies=3)
                _add_metadata_versions(client, (_pr(), _metadata_version()))
                observed = []

                def intent_response(*, body, **_kwargs):
                    intent = pr_metadata._parse_intent_comment_body(body["body"])
                    comment = _intent_comment(intent)
                    observed.append(comment)
                    observed.append(
                        _confirmation_comment(_confirmation(intent))
                        if terminal == "confirmation"
                        else _abort_comment(_abort(intent))
                    )
                    return comment

                client.add(
                    "POST",
                    _endpoint(f"issues/{PR_NUMBER}/comments"),
                    intent_response,
                )
                client.add_stable_comment_pages(
                    "GET",
                    _query(
                        f"issues/{PR_NUMBER}/comments",
                        [("per_page", "100"), ("page", "1")],
                    ),
                    [],
                    lambda **_kwargs: copy.deepcopy(observed),
                )
                decision = pr_metadata.edit_metadata(
                    client,
                    repository=REPOSITORY,
                    pr_number=PR_NUMBER,
                    head_sha=HEAD,
                    base_sha=BASE,
                    title=None,
                    body="new body",
                    essential_reason=None,
                )
                self.assertEqual(decision.action, "deferred")
                self.assertTrue(decision.mutated)
                self.assertEqual(decision.intent_comment_id, 401)
                self.assertEqual(
                    (decision.confirmation_comment_id, decision.abort_comment_id),
                    (402, None) if terminal == "confirmation" else (None, 403),
                )
                self.assertFalse(any(call[0] == "PATCH" for call in client.calls))
                transaction_posts = [
                    call for call in client.calls
                    if call[:2] == ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
                ]
                self.assertEqual(len(transaction_posts), 1)
                self.assertIn(
                    pr_metadata.INTENT_MARKER,
                    transaction_posts[0][2]["body"],
                )

    def test_post_intent_abort_defers_without_another_terminal(self):
        pre_state = _pr()
        pre_version = _metadata_version()
        intent = _receipt(
            provided_fields={"body": _sha256("new body")},
            pre_body="Stable body",
            pre_version=pre_version,
            target_metadata_sha256=_metadata_sha256("Stable title", "new body"),
        )
        client = ScriptedClient()
        _add_pr_states(client, pre_state, pre_state)
        _add_snapshot(client, [_run(101, 10, mode="full")], copies=3)
        _add_metadata_versions(client, (pre_state, pre_version))
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [_intent_comment(intent)],
            [_intent_comment(intent), _abort_comment(_abort(intent))],
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "deferred")
        self.assertFalse(decision.mutated)
        self.assertEqual(decision.abort_comment_id, 403)
        self.assertIsNone(decision.confirmation_comment_id)
        self.assertFalse(
            any(
                method == "PATCH"
                or endpoint == _endpoint(f"issues/{PR_NUMBER}/comments")
                for method, endpoint, _body in client.calls
            )
        )

    def test_retry_unmatched_intent_rejects_third_state(self):
        intent = _receipt(
            provided_fields={"body": _sha256("new body")},
            pre_title="Stable title",
            pre_body="Stable body",
            pre_metadata_sha256=_metadata_sha256(
                "Stable title",
                "Stable body",
            ),
            target_metadata_sha256=_metadata_sha256(
                "Stable title",
                "new body",
            ),
        )
        third_state = _pr(body="unrelated body")
        client = ScriptedClient()
        _add_pr_states(client, third_state, third_state)
        _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
        _add_metadata_versions(
            client,
            (third_state, _metadata_version()),
        )
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [_intent_comment(intent)],
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "neither pre-state nor target",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="new body",
                essential_reason=None,
            )
        self.assertFalse(
            any(method == "PATCH" for method, _endpoint, _body in client.calls)
        )

    def test_mixed_field_retry_uses_immutable_changed_set(self):
        pre_version = _metadata_version()
        intent = _receipt(
            provided_fields={
                "body": _sha256("Stable body"),
                "title": _sha256("New title"),
            },
            changed_fields={"title": _sha256("New title")},
            pre_title="Stable title",
            pre_body="Stable body",
            pre_metadata_sha256=_metadata_sha256(
                "Stable title",
                "Stable body",
            ),
            target_metadata_sha256=_metadata_sha256(
                "New title",
                "Stable body",
            ),
            pre_version=pre_version,
        )
        target_state = _pr(title="New title", body="Stable body")
        target_version = replace(
            pre_version,
            title_event_id="RTE_2",
            title_event_created_at="2026-09-04T00:00:05Z",
            title_previous="Stable title",
            title_current="New title",
        )
        client = ScriptedClient()
        _add_pr_states(client, target_state, target_state)
        _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
        _add_metadata_versions(
            client,
            (target_state, target_version),
            (target_state, target_version),
        )
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [_intent_comment(intent)],
            [_intent_comment(intent)],
        )

        def confirmation_response(
            *,
            method: str,
            endpoint: str,
            body: dict[str, object] | None,
        ) -> dict:
            del method, endpoint
            return _comment(
                402,
                body["body"],
                created_at="2026-09-04T00:00:06Z",
                updated_at="2026-09-04T00:00:06Z",
            )

        client.add(
            "POST",
            _endpoint(f"issues/{PR_NUMBER}/comments"),
            confirmation_response,
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title="New title",
            body="Stable body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "recovered")
        self.assertFalse(
            any(method == "PATCH" for method, _endpoint, _body in client.calls)
        )
        confirmation_call = next(
            call
            for call in client.calls
            if call[:2]
            == ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
        )
        confirmation = pr_metadata._parse_confirmation_comment_body(
            confirmation_call[2]["body"]
        )
        self.assertEqual(
            pr_metadata._body_version_identity(
                confirmation.metadata_version
            ),
            pr_metadata._body_version_identity(pre_version),
        )

    def test_supplied_unchanged_field_drift_fails_after_changed_only_patch(self):
        client = ScriptedClient()
        successful_full = _run(101, 10, mode="full")
        drifted = _pr(
            body="concurrent drift",
            updated_at="2026-09-04T00:00:02Z",
        )
        _add_pr_states(client, _pr(), _pr(), drifted)
        _add_snapshot(client, [successful_full], copies=3)
        drifted_version = _metadata_version(
            body_last_edited_at="2026-09-04T00:00:02Z",
        )
        _add_metadata_versions(
            client,
            (_pr(), _metadata_version()),
            (drifted, drifted_version),
        )
        intent_payload = {}
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [],
            lambda **_kwargs: [copy.deepcopy(intent_payload)],
        )

        def intent_response(
            *,
            method: str,
            endpoint: str,
            body: dict[str, object] | None,
        ) -> dict:
            del method, endpoint
            marker = (
                pr_metadata.INTENT_MARKER
                if pr_metadata.INTENT_MARKER in body["body"]
                else pr_metadata.ABORT_MARKER
            )
            payload = _comment(
                401 if marker == pr_metadata.INTENT_MARKER else 403,
                body["body"],
                created_at=(
                    "2026-09-04T00:00:01Z"
                    if marker == pr_metadata.INTENT_MARKER
                    else "2026-09-04T00:00:03Z"
                ),
                updated_at=(
                    "2026-09-04T00:00:01Z"
                    if marker == pr_metadata.INTENT_MARKER
                    else "2026-09-04T00:00:03Z"
                ),
            )
            if marker == pr_metadata.INTENT_MARKER:
                intent_payload.update(copy.deepcopy(payload))
            return payload

        client.add(
            "POST",
            _endpoint(f"issues/{PR_NUMBER}/comments"),
            intent_response,
            intent_response,
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title="New title",
            body="Stable body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.abort_comment_id, 403)
        self.assertFalse(
            any(
                call[:2] == ("PATCH", _endpoint(f"pulls/{PR_NUMBER}"))
                for call in client.calls
            )
        )
        abort_call = next(
            call
            for call in client.calls
            if call[:2]
            == ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
            and pr_metadata.ABORT_MARKER in call[2]["body"]
        )
        self.assertIn("pre-state-drift", abort_call[2]["body"])
        abort = pr_metadata._parse_abort_comment_body(abort_call[2]["body"])
        self.assertEqual(
            abort.observed_metadata_sha256,
            _metadata_sha256(drifted["title"], drifted["body"]),
        )
        self.assertEqual(abort.observed_version, drifted_version)

    def test_abort_observations_bind_actual_changed_candidate_identity(self):
        for changed in ("head", "base"):
            with self.subTest(changed=changed):
                client = ScriptedClient()
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
                _add_edit_transaction(client, body="new body")
                observed = _pr()
                observed[changed]["sha"] = NEW_HEAD
                client.routes[("POST", "graphql")][1] = _response(
                    _graphql_payload(observed, _metadata_version())
                )
                decision = pr_metadata.edit_metadata(
                    client,
                    repository=REPOSITORY,
                    pr_number=PR_NUMBER,
                    head_sha=HEAD,
                    base_sha=BASE,
                    title=None,
                    body="new body",
                    essential_reason=None,
                )
                self.assertEqual(decision.action, "deferred")
                posts = [
                    call[2]["body"] for call in client.calls
                    if call[:2] == ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
                ]
                abort = pr_metadata._parse_abort_comment_body(posts[-1])
                self.assertEqual(abort.reason, "candidate-drift")
                self.assertEqual(abort.intent_head_sha, HEAD)
                self.assertEqual(abort.intent_base_sha, BASE)
                self.assertEqual(abort.observed_head_sha, observed["head"]["sha"])
                self.assertEqual(abort.observed_base_sha, observed["base"]["sha"])
                self.assertFalse(any(call[0] == "PATCH" for call in client.calls))

    def test_unvalidated_final_observation_never_creates_cached_abort_evidence(self):
        for failure in ("repository", "updatedAt", "head", "body-version"):
            with self.subTest(failure=failure):
                client = ScriptedClient()
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
                _add_edit_transaction(client, body="new body")
                payload = _graphql_payload(_pr(), _metadata_version())
                repository = payload["data"]["repository"]
                pull = repository["pullRequest"]
                if failure == "repository":
                    repository["databaseId"] += 1
                elif failure == "updatedAt":
                    pull.pop("updatedAt")
                elif failure == "head":
                    pull["headRefOid"] = "invalid"
                else:
                    pull["body"] = "does not match the observed edit node"
                client.routes[("POST", "graphql")][1] = _response(payload)
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.edit_metadata(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        title=None,
                        body="new body",
                        essential_reason=None,
                    )
                posts = [
                    call[2]["body"] for call in client.calls
                    if call[:2] == ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
                ]
                self.assertEqual(len(posts), 1)
                self.assertTrue(posts[0].startswith(pr_metadata.INTENT_MARKER))
                self.assertFalse(any(call[0] == "PATCH" for call in client.calls))

    def test_no_op_requires_and_returns_authoritative_pair(self):
        receipt = _receipt()
        confirmation = _confirmation(receipt)
        client = ScriptedClient()
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(
            client,
            [
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
            copies=2,
        )
        _add_metadata_versions(
            client,
            (_pr(), confirmation.metadata_version),
        )
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [
                _intent_comment(receipt),
                _confirmation_comment(confirmation),
            ],
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title="Stable title",
            body="Stable body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "no-op")
        self.assertEqual(decision.intent_comment_id, 401)
        self.assertEqual(decision.confirmation_comment_id, 402)
        self.assertFalse(decision.mutated)

    def test_retry_after_confirmation_timeout_finds_maybe_created_pair(self):
        receipt = _receipt()
        confirmation = _confirmation(receipt)
        client = ScriptedClient()
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
        _add_metadata_versions(
            client,
            (_pr(), confirmation.metadata_version),
        )
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [
                _intent_comment(receipt),
                _confirmation_comment(confirmation),
            ],
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="Stable body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.intent_comment_id, 401)
        self.assertEqual(decision.confirmation_comment_id, 402)
        self.assertFalse(decision.mutated)
        self.assertFalse(
            any(
                method == "PATCH"
                or method == "POST" and endpoint != "graphql"
                for method, endpoint, _body in client.calls
            )
        )

    def test_authoritative_pair_no_op_requires_successful_bound_run(self):
        receipt = _receipt()
        confirmation = _confirmation(receipt)
        cases = []
        active_record, active_jobs = _run(
            202,
            11,
            mode="metadata-only",
            active=True,
        )
        cases.append(
            (
                "active",
                [(active_record, active_jobs), _run(101, 10, mode="full")],
                "deferred",
                202,
            )
        )
        cases.append(
            (
                "failed",
                [
                    _run(202, 11, mode="metadata-only", success=False),
                    _run(101, 10, mode="full"),
                ],
                "deferred",
                202,
            )
        )
        malformed_record, malformed_jobs = _run(
            202,
            11,
            mode="metadata-only",
            success=True,
        )
        next(job for job in malformed_jobs if job["name"] == "build")[
            "conclusion"
        ] = "failure"
        cases.append(
            (
                "malformed-success",
                [
                    (malformed_record, malformed_jobs),
                    _run(101, 10, mode="full"),
                ],
                "error",
                None,
            )
        )
        cases.append(
            (
                "newest-full-success-older-active",
                [
                    _run(303, 13, mode="full"),
                    _run(302, 12, mode="full", active=True),
                    _run(202, 11, mode="metadata-only", success=True),
                    _run(101, 10, mode="full"),
                ],
                "no-op",
                202,
            )
        )
        cases.append(
            (
                "newest-full-active",
                [
                    _run(303, 13, mode="full", active=True),
                    _run(302, 12, mode="full"),
                    _run(202, 11, mode="metadata-only", success=True),
                    _run(101, 10, mode="full"),
                ],
                "deferred",
                303,
            )
        )
        cases.append(
            (
                "newest-full-failure-older-active",
                [
                    _run(303, 13, mode="full", success=False),
                    _run(302, 12, mode="full", active=True),
                    _run(202, 11, mode="metadata-only", success=True),
                    _run(101, 10, mode="full"),
                ],
                "error",
                None,
            )
        )
        for name, runs, expected, expected_run_id in cases:
            with self.subTest(case=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(client, runs, copies=2)
                _add_metadata_versions(
                    client,
                    (_pr(), confirmation.metadata_version),
                )
                client.add_stable_comment_pages(
                    "GET",
                    _query(
                        f"issues/{PR_NUMBER}/comments",
                        [("per_page", "100"), ("page", "1")],
                    ),
                    [
                        _intent_comment(receipt),
                        _confirmation_comment(confirmation),
                    ],
                )
                if expected == "error":
                    with self.assertRaises(pr_metadata.MetadataEditError):
                        pr_metadata.edit_metadata(
                            client,
                            repository=REPOSITORY,
                            pr_number=PR_NUMBER,
                            head_sha=HEAD,
                            base_sha=BASE,
                            title="Stable title",
                            body="Stable body",
                            essential_reason=None,
                        )
                    continue
                decision = pr_metadata.edit_metadata(
                    client,
                    repository=REPOSITORY,
                    pr_number=PR_NUMBER,
                    head_sha=HEAD,
                    base_sha=BASE,
                    title="Stable title",
                    body="Stable body",
                    essential_reason=None,
                )
                self.assertEqual(decision.action, expected)
                self.assertEqual(decision.run_id, expected_run_id)
                if expected == "deferred":
                    self.assertIn(
                        "reconcile",
                        " ".join(decision.guidance[0]),
                    )

    def test_superseded_candidate_intents_do_not_block_current_candidate(self):
        old_intent = _receipt(head_sha=NEW_HEAD, nonce="b" * 64)
        old_confirmation = _confirmation(old_intent)
        client = ScriptedClient()
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(
            client,
            [
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
            copies=2,
        )
        _add_metadata_versions(client, (_pr(), _metadata_version()))
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [
                _intent_comment(old_intent),
                _confirmation_comment(old_confirmation),
            ],
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title="Stable title",
            body="Stable body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "refused")
        self.assertFalse(decision.mutated)

        client = ScriptedClient()
        current_intent = _receipt(
            provided_fields={"body": _sha256("new body")},
            changed_fields={"body": _sha256("new body")},
            pre_title="Stable title",
            pre_body="Stable body",
            pre_metadata_sha256=_metadata_sha256(
                "Stable title",
                "Stable body",
            ),
            target_metadata_sha256=_metadata_sha256(
                "Stable title",
                "new body",
            ),
            nonce="c" * 64,
        )
        target_state = _pr(body="new body")
        target_version = _confirmation(current_intent).metadata_version
        _add_pr_states(client, target_state, target_state)
        _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
        _add_metadata_versions(
            client,
            (target_state, target_version),
            (target_state, target_version),
        )
        _add_transaction_comments(
            client,
            current_intent,
            comments=[
                _intent_comment(old_intent),
                _confirmation_comment(old_confirmation),
                _intent_comment(
                    current_intent,
                    comment_id=403,
                    created_at="2026-09-04T00:00:03Z",
                ),
            ],
        )

        def response(
            *,
            method: str,
            endpoint: str,
            body: dict[str, object] | None,
        ) -> dict:
            del method, endpoint
            return _comment(
                404,
                body["body"],
                created_at="2026-09-04T00:00:04Z",
                updated_at="2026-09-04T00:00:04Z",
            )

        client.add(
            "POST",
            _endpoint(f"issues/{PR_NUMBER}/comments"),
            response,
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "recovered")
        self.assertEqual(decision.intent_comment_id, 403)
        self.assertEqual(decision.confirmation_comment_id, 404)

    def test_malformed_superseded_protected_comment_remains_fatal(self):
        old = _intent_comment(_receipt(head_sha=NEW_HEAD))
        old["body"] = f"{pr_metadata.INTENT_MARKER}\n{{}}\n"
        client = ScriptedClient()
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
        _add_metadata_versions(client, (_pr(), _metadata_version()))
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [old],
        )
        with self.assertRaises(pr_metadata.MetadataEditError):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title="Stable title",
                body="Stable body",
                essential_reason=None,
            )

    def test_nonowner_marker_text_cannot_poison_transactions(self):
        receipt = _receipt()
        confirmation = _confirmation(receipt)
        comments = []
        for index, marker in enumerate(
            (
                pr_metadata.INTENT_MARKER,
                pr_metadata.CONFIRMATION_MARKER,
                pr_metadata.ABORT_MARKER,
            ),
            1,
        ):
            comments.append(
                _comment(
                    300 + index,
                    f"{marker}\nmalformed attacker text\n",
                    author_id=OWNER_ID + index,
                    author_login=f"attacker{index}",
                    author_association="CONTRIBUTOR",
                )
            )
        comments.extend(
            [
                _intent_comment(receipt),
                _confirmation_comment(confirmation),
            ]
        )
        comments.append(
            _comment(
                399,
                f"{pr_metadata.INTENT_MARKER}\nlate bot poison\n",
                author_login="automation[bot]",
                author_type="Bot",
                author_association="NONE",
            )
        )
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
        )
        decision = _reconcile(
            client,
            receipt=receipt,
            confirmation=confirmation,
            comments=comments,
        )
        self.assertEqual(decision.action, "complete")
        self.assertEqual(decision.run_id, 202)

    def test_stale_new_candidate_is_rejected_before_run_queries(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr(head=NEW_HEAD))
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "identity changed",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="new body",
                essential_reason=None,
            )
        self.assertEqual(len(client.calls), 1)

    def test_reconciliation_reruns_only_failed_metadata_continuity(self):
        client = ScriptedClient()
        failed_metadata = _run(
            202,
            11,
            mode="metadata-only",
            success=False,
        )
        successful_full = _run(101, 10, mode="full")
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(
            client,
            [failed_metadata, successful_full],
            copies=2,
        )
        client.add(
            "POST",
            _endpoint("actions/runs/202/rerun"),
            None,
        )

        decision = _reconcile(client)

        self.assertEqual(decision.action, "rerun")
        self.assertEqual(decision.run_id, 202)
        mutations = [call for call in client.calls if call[0] != "GET"]
        reruns = [
            call
            for call in mutations
            if call[:2]
            == ("POST", _endpoint("actions/runs/202/rerun"))
        ]
        self.assertEqual(reruns, [("POST", _endpoint("actions/runs/202/rerun"), None)])
        self.assertFalse(any("/cancel" in endpoint for _method, endpoint, _body in client.calls))
        self.assertFalse(any("/dispatches" in endpoint for _method, endpoint, _body in client.calls))

    def test_reconciliation_refresh_requires_complete_unchanged_selected_pair(self):
        receipt = _receipt()
        confirmation = _confirmation(receipt)
        original = [_intent_comment(receipt), _confirmation_comment(confirmation)]
        for drift in ("unchanged", *INTENT_DRIFTS, "confirmation-timestamp"):
            with self.subTest(drift=drift):
                client = ScriptedClient()
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(
                    client,
                    [
                        _run(202, 11, mode="metadata-only", success=False),
                        _run(101, 10, mode="full"),
                    ],
                    copies=2,
                )
                _add_metadata_versions(
                    client,
                    (_pr(), confirmation.metadata_version),
                    (_pr(), confirmation.metadata_version),
                )
                if drift in ("unchanged", "confirmation-timestamp"):
                    refreshed = copy.deepcopy(original)
                    if drift == "confirmation-timestamp":
                        refreshed[1]["created_at"] = "2026-09-04T00:00:03Z"
                        refreshed[1]["updated_at"] = "2026-09-04T00:00:03Z"
                else:
                    refreshed = [
                        *_drifted_intent_page(original[0], drift),
                        copy.deepcopy(original[1]),
                    ]
                    if drift == "nonce":
                        refreshed[1] = _confirmation_comment(
                            replace(confirmation, intent_nonce="b" * 64)
                        )
                client.add_stable_comment_pages(
                    "GET",
                    _query(
                        f"issues/{PR_NUMBER}/comments",
                        [("per_page", "100"), ("page", "1")],
                    ),
                    original,
                    refreshed,
                )
                client.add("POST", _endpoint("actions/runs/202/rerun"), {})
                try:
                    decision = pr_metadata.reconcile_metadata(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        confirmation_comment_id=402,
                    )
                except pr_metadata.MetadataEditError:
                    self.assertNotEqual(drift, "unchanged")
                else:
                    self.assertEqual(
                        decision.action, "rerun" if drift == "unchanged" else "deferred"
                    )
                mutations = [
                    call for call in client.calls
                    if call[0] != "GET" and call[1] != "graphql"
                ]
                self.assertEqual(
                    mutations,
                    [("POST", _endpoint("actions/runs/202/rerun"), None)]
                    if drift == "unchanged" else [],
                )

    def test_reconciliation_rejects_every_nonfailure_terminal_conclusion(self):
        for conclusion in (
            "action_required",
            "cancelled",
            "neutral",
            "skipped",
            "stale",
            "startup_failure",
            "timed_out",
        ):
            with self.subTest(conclusion=conclusion):
                client = ScriptedClient()
                record, jobs = _run(
                    202,
                    11,
                    mode="metadata-only",
                    success=False,
                )
                record["conclusion"] = conclusion
                _add_pr_states(client, _pr())
                _add_snapshot(
                    client,
                    [(record, jobs), _run(101, 10, mode="full")],
                )
                with self.assertRaisesRegex(
                    pr_metadata.MetadataEditError,
                    "only a completed failed metadata continuity run",
                ):
                    _reconcile(client)
                self.assertFalse(
                    any(
                        method not in {"GET", "POST"}
                        for method, _endpoint, _body in client.calls
                    )
                )

    def test_reconciliation_rejects_noncanonical_failed_metadata_jobs(self):
        base_jobs = _metadata_jobs(202, 1, success=False)
        mutations = {}
        for job_name in (
            "event-identity",
            "event-router",
            "metadata-classifier",
            "host-tests",
            "build",
        ):
            jobs = copy.deepcopy(base_jobs)
            next(job for job in jobs if job["name"] == job_name)[
                "conclusion"
            ] = "failure"
            mutations[f"{job_name}-failure"] = jobs
        for job_name in ("extended-host-tests", "legacy"):
            jobs = copy.deepcopy(base_jobs)
            target = next(job for job in jobs if job["name"] == job_name)
            target["runner_name"] = "unexpected-runner"
            target["started_at"] = "2026-09-04T00:00:00Z"
            mutations[f"{job_name}-runner"] = jobs
        summary_success = copy.deepcopy(base_jobs)
        next(job for job in summary_success if job["name"] == "summary")[
            "conclusion"
        ] = "success"
        mutations["summary-success"] = summary_success

        for name, jobs in mutations.items():
            with self.subTest(mutation=name):
                client = ScriptedClient()
                record, _ = _run(
                    202,
                    11,
                    mode="metadata-only",
                    success=False,
                )
                _add_pr_states(client, _pr())
                _add_snapshot(
                    client,
                    [(record, jobs), _run(101, 10, mode="full")],
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    _reconcile(client)
                self.assertFalse(
                    any(
                        method not in {"GET", "POST"}
                        for method, _endpoint, _body in client.calls
                    )
                )

    def test_reconciliation_defers_while_metadata_run_is_active(self):
        client = ScriptedClient()
        active_metadata_record, active_metadata_jobs = _run(
            202,
            11,
            mode="metadata-only",
            active=True,
        )
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                (active_metadata_record, active_metadata_jobs),
                _run(101, 10, mode="full"),
            ],
        )

        decision = _reconcile(client)
        self.assertEqual(decision.action, "deferred")
        self.assertFalse(decision.mutated)
        self.assertFalse(
            any(
                method not in {"GET", "POST"}
                for method, _endpoint, _body in client.calls
            )
        )

    def test_reconciliation_defers_until_full_build_succeeds(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                _run(202, 11, mode="metadata-only", success=False),
                _run(101, 10, mode="full", active=True),
            ],
        )
        decision = _reconcile(client)
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.run_id, 101)
        self.assertFalse(
            any(
                method not in {"GET", "POST"}
                for method, _endpoint, _body in client.calls
            )
        )

    def test_green_metadata_cannot_reconcile_terminal_failed_full(self):
        client = ScriptedClient()
        failed_full = _run(101, 10, mode="full", success=False)
        summary = next(job for job in failed_full[1] if job["name"] == "summary")
        summary["conclusion"] = "failure"
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [_run(202, 11, mode="metadata-only", success=True), failed_full],
        )
        with self.assertRaisesRegex(pr_metadata.MetadataEditError, "not successful"):
            _reconcile(client)
        self.assertFalse(
            any(
                method == "PATCH" or "/rerun" in endpoint
                or method == "POST" and "/comments" in endpoint
                for method, endpoint, _ in client.calls
            )
        )

    def test_reconciliation_rejects_identity_change_before_rerun(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr(), _pr(head=NEW_HEAD))
        _add_snapshot(
            client,
            [
                _run(202, 11, mode="metadata-only", success=False),
                _run(101, 10, mode="full"),
            ],
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "identity changed",
        ):
            _reconcile(client)
        self.assertFalse(
            any(
                method not in {"GET", "POST"}
                for method, _endpoint, _body in client.calls
            )
        )

    def test_successful_metadata_run_is_already_reconciled(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
        )
        decision = _reconcile(client)
        self.assertEqual(decision.action, "complete")
        self.assertFalse(decision.mutated)

    def test_reconciliation_waits_past_receipt_watermark(self):
        client = ScriptedClient()
        old_success = _run(202, 11, mode="metadata-only", success=True)
        successful_full = _run(101, 10, mode="full")
        _add_pr_states(client, _pr())
        _add_snapshot(client, [old_success, successful_full])
        decision = _reconcile(
            client,
            receipt=_receipt(
                watermark_run_id=202,
                watermark_run_number=11,
            ),
        )
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.run_id, 101)
        self.assertFalse(decision.mutated)
        self.assertFalse(
            any(
                method not in {"GET", "POST"}
                for method, _endpoint, _body in client.calls
            )
        )

    def test_reconciliation_accepts_later_run_at_same_timestamp(self):
        for success in (False, True):
            with self.subTest(success=success):
                client = ScriptedClient()
                metadata = _run(
                    203,
                    12,
                    mode="metadata-only",
                    success=success,
                )
                watermark = _run(202, 11, mode="metadata-only", success=True)
                successful_full = _run(101, 10, mode="full")
                _add_pr_states(client, _pr(), *([_pr()] if not success else []))
                _add_snapshot(
                    client,
                    [metadata, watermark, successful_full],
                    copies=2 if not success else 1,
                )
                if not success:
                    client.add(
                        "POST",
                        _endpoint("actions/runs/203/rerun"),
                        None,
                    )
                decision = _reconcile(
                    client,
                    receipt=_receipt(
                        watermark_run_id=202,
                        watermark_run_number=11,
                    ),
                )
                self.assertEqual(
                    decision.action,
                    "complete" if success else "rerun",
                )
                self.assertEqual(decision.run_id, 203)

    def test_reconciliation_accepts_run_materialized_after_confirmation(self):
        client = ScriptedClient()
        record, jobs = _run(202, 11, mode="metadata-only", success=True)
        record.update(
            {
                "created_at": "2026-09-04T00:00:03Z",
                "run_started_at": "2026-09-04T00:00:03Z",
                "updated_at": "2026-09-04T00:00:06Z",
            }
        )
        for job in jobs:
            job["created_at"] = "2026-09-04T00:00:04Z"
            job["started_at"] = "2026-09-04T00:00:04Z"
            job["completed_at"] = "2026-09-04T00:00:05Z"
            if job["conclusion"] == "skipped":
                job["completed_at"] = "2026-09-04T00:00:04Z"
            for step in job.get("steps", []):
                step["started_at"] = job["started_at"]
                step["completed_at"] = job["completed_at"]
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [(record, jobs), _run(101, 10, mode="full")],
        )
        decision = _reconcile(client)
        self.assertEqual(decision.action, "complete")
        self.assertEqual(decision.run_id, 202)

    def test_full_authorization_and_transaction_metadata_are_independent(self):
        cases = (
            (
                "metadata-success-later-full-success",
                [
                    _run(303, 12, mode="full"),
                    _run(202, 11, mode="metadata-only", success=True),
                    _run(101, 10, mode="full"),
                ],
                "complete",
                202,
            ),
            (
                "metadata-active-later-full-success",
                [
                    _run(303, 12, mode="full"),
                    _run(202, 11, mode="metadata-only", active=True),
                    _run(101, 10, mode="full"),
                ],
                "deferred",
                202,
            ),
            (
                "multiple-later-full-successes",
                [
                    _run(304, 13, mode="full"),
                    _run(303, 12, mode="full"),
                    _run(202, 11, mode="metadata-only", success=True),
                    _run(101, 10, mode="full"),
                ],
                "complete",
                202,
            ),
            (
                "newest-success-older-active-full",
                [
                    _run(304, 13, mode="full"),
                    _run(303, 12, mode="full", active=True),
                    _run(202, 11, mode="metadata-only", success=True),
                    _run(101, 10, mode="full"),
                ],
                "complete",
                202,
            ),
            (
                "metadata-attempt-two-same-identity",
                [
                    _run(303, 12, mode="full"),
                    _run(
                        202,
                        11,
                        mode="metadata-only",
                        success=True,
                        attempt=2,
                    ),
                    _run(101, 10, mode="full"),
                ],
                "complete",
                202,
            ),
            (
                "same-full-id-new-attempt",
                [
                    _run(202, 11, mode="metadata-only", success=True),
                    _run(101, 10, mode="full", attempt=2),
                ],
                "complete",
                202,
            ),
        )
        for name, runs, action, run_id in cases:
            with self.subTest(case=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                _add_snapshot(client, runs)
                decision = _reconcile(client)
                self.assertEqual(decision.action, action)
                self.assertEqual(decision.run_id, run_id)

        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                _run(303, 12, mode="full", active=True),
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
        )
        decision = _reconcile(client)
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.run_id, 303)

        client = ScriptedClient()
        failed_full = _run(303, 12, mode="full", success=False)
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                failed_full,
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "newest exact full Build is not successful",
        ):
            _reconcile(client)

        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                _run(304, 13, mode="full", success=False),
                _run(303, 12, mode="full", active=True),
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "newest exact full Build is not successful",
        ):
            _reconcile(client)

    def test_failed_transaction_metadata_reruns_despite_later_full(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr(), _pr())
        runs = [
            _run(303, 12, mode="full"),
            _run(202, 11, mode="metadata-only", success=False),
            _run(101, 10, mode="full"),
        ]
        _add_snapshot(client, runs, copies=2)
        client.add(
            "POST",
            _endpoint("actions/runs/202/rerun"),
            None,
        )
        decision = _reconcile(client)
        self.assertEqual(decision.action, "rerun")
        self.assertEqual(decision.run_id, 202)
        self.assertIn(
            ("POST", _endpoint("actions/runs/202/rerun"), None),
            client.calls,
        )

    def test_multiple_post_watermark_metadata_identities_are_ambiguous(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                _run(203, 13, mode="metadata-only", success=True),
                _run(303, 12, mode="full"),
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "metadata run identities are ambiguous",
        ):
            _reconcile(client)

    def test_second_reconcile_completes_same_metadata_run_after_rerun(self):
        receipt = _receipt()
        confirmation = _confirmation(receipt)
        client = ScriptedClient()
        _add_pr_states(client, _pr(), _pr())
        failed_runs = [
            _run(303, 12, mode="full"),
            _run(202, 11, mode="metadata-only", success=False),
            _run(101, 10, mode="full"),
        ]
        _add_snapshot(client, failed_runs, copies=2)
        client.add(
            "POST",
            _endpoint("actions/runs/202/rerun"),
            None,
        )
        first = _reconcile(
            client,
            receipt=receipt,
            confirmation=confirmation,
        )
        self.assertEqual(first.action, "rerun")

        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                _run(303, 12, mode="full"),
                _run(
                    202,
                    11,
                    mode="metadata-only",
                    success=True,
                    attempt=2,
                ),
                _run(101, 10, mode="full"),
            ],
        )
        second = _reconcile(
            client,
            receipt=receipt,
            confirmation=confirmation,
        )
        self.assertEqual(second.action, "complete")
        self.assertEqual(second.run_id, 202)

    def test_reconciliation_rejects_rerun_attempt_at_watermark(self):
        client = ScriptedClient()
        old_rerun = _run(
            202,
            11,
            mode="metadata-only",
            success=True,
            attempt=2,
        )
        successful_full = _run(101, 10, mode="full")
        _add_pr_states(client, _pr())
        _add_snapshot(client, [old_rerun, successful_full])
        decision = _reconcile(
            client,
            receipt=_receipt(
                watermark_run_id=202,
                watermark_run_number=11,
            ),
        )
        self.assertEqual(decision.action, "deferred")
        self.assertFalse(decision.mutated)

    def test_unbound_runs_block_or_remain_ineligible_during_reconciliation(self):
        active_record, active_jobs = _run(
            202,
            11,
            mode="full",
            active=True,
        )
        active_record["pull_requests"] = []
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                (active_record, active_jobs[:1]),
                _run(101, 10, mode="full"),
            ],
        )
        decision = _reconcile(client)
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.run_id, 202)

        terminal_record, terminal_jobs = _run(
            203,
            12,
            mode="metadata-only",
            success=True,
        )
        terminal_record["pull_requests"] = []
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                (terminal_record, terminal_jobs),
                _run(101, 10, mode="full"),
            ],
        )
        decision = _reconcile(client)
        self.assertEqual(decision.action, "deferred")
        self.assertFalse(decision.mutated)

    def test_receipt_schema_identity_digest_and_watermark_fail_closed(self):
        invalid_payloads = []
        extra = _receipt_payload()
        extra["unexpected"] = True
        invalid_payloads.append(("extra-field", extra))
        boolean_version = _receipt_payload()
        boolean_version["schema_version"] = True
        invalid_payloads.append(("boolean-version", boolean_version))
        float_version = _receipt_payload()
        float_version["schema_version"] = 1.0
        invalid_payloads.append(("float-version", float_version))
        wrong_field = _receipt_payload(provided_fields={"labels": "a" * 64})
        invalid_payloads.append(("wrong-field", wrong_field))
        bad_digest = _receipt_payload(provided_fields={"body": "A" * 64})
        invalid_payloads.append(("bad-digest", bad_digest))
        empty_changed = _receipt_payload(changed_fields={})
        invalid_payloads.append(("empty-changed", empty_changed))
        extra_changed = _receipt_payload(changed_fields={"title": "a" * 64})
        invalid_payloads.append(("changed-not-provided", extra_changed))
        mismatched_changed = _receipt_payload(
            changed_fields={"body": "b" * 64},
        )
        invalid_payloads.append(("changed-digest-mismatch", mismatched_changed))
        bad_path = _receipt_payload(workflow_path=".github/workflows/other.yml")
        invalid_payloads.append(("workflow-path", bad_path))
        for name, payload in invalid_payloads:
            with self.subTest(schema=name):
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._parse_edit_receipt(payload)

        identity_receipts = (
            _receipt(repository="other/repo"),
            _receipt(repository_id=REPOSITORY_ID + 1),
            _receipt(pr_number=PR_NUMBER + 1),
            _receipt(head_sha=NEW_HEAD),
            _receipt(base_sha="4" * 40),
            _receipt(
                provided_fields={
                    "body": _sha256("stale body")
                }
            ),
        )
        for receipt in identity_receipts:
            with self.subTest(receipt=receipt):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                with self.assertRaises(pr_metadata.MetadataEditError):
                    _reconcile(client, receipt=receipt)

        for receipt in (
            _receipt(watermark_run_id=999),
            _receipt(watermark_run_number=999),
            _receipt(watermark_created_at="2026-09-03T23:59:59Z"),
        ):
            with self.subTest(watermark=receipt):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                _add_snapshot(client, [_run(101, 10, mode="full")])
                with self.assertRaisesRegex(
                    pr_metadata.MetadataEditError,
                    "watermark is stale or forged",
                ):
                    _reconcile(client, receipt=receipt)

        client = ScriptedClient()
        _add_pr_states(client, _pr())
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "intent comment is missing",
        ):
            _reconcile(
                client,
                receipt=_receipt(workflow_id=WORKFLOW_ID + 1),
            )

    def test_transaction_comment_authority_and_latest_selection_fail_closed(self):
        receipt = _receipt()
        confirmation = _confirmation(receipt)
        base_comment = _intent_comment(receipt)
        confirmation_comment = _confirmation_comment(confirmation)
        cases = {
            "deleted": [],
            "edited": [
                _intent_comment(
                    receipt,
                    updated_at="2026-09-04T00:00:02Z",
                ),
                confirmation_comment,
            ],
            "non-owner": [
                {
                    **base_comment,
                    "user": {
                        "id": OWNER_ID + 1,
                        "login": "attacker",
                        "type": "User",
                        "site_admin": False,
                    },
                    "author_association": "NONE",
                },
                confirmation_comment,
            ],
            "bot": [
                {
                    **base_comment,
                    "user": {
                        "id": OWNER_ID,
                        "login": "owner",
                        "type": "Bot",
                        "site_admin": False,
                    },
                },
                confirmation_comment,
            ],
            "cross-repository": [
                {
                    **base_comment,
                    "url": (
                        "https://api.github.com/repos/other/repo/"
                        "issues/comments/401"
                    ),
                },
                confirmation_comment,
            ],
            "duplicate-marker": [
                {
                    **base_comment,
                    "body": (
                        f"{pr_metadata.INTENT_MARKER}\n"
                        f"{pr_metadata.INTENT_MARKER}\n"
                    ),
                },
                confirmation_comment,
            ],
            "wrong-id": [
                _intent_comment(receipt, comment_id=403),
                confirmation_comment,
            ],
            "duplicate-nonce": [
                base_comment,
                confirmation_comment,
                _intent_comment(
                    receipt,
                    comment_id=403,
                    created_at="2026-09-04T00:00:03Z",
                ),
            ],
            "edited-confirmation": [
                base_comment,
                _confirmation_comment(
                    confirmation,
                    updated_at="2026-09-04T00:00:03Z",
                ),
            ],
            "non-owner-confirmation": [
                base_comment,
                {
                    **confirmation_comment,
                    "user": {
                        "id": OWNER_ID + 1,
                        "login": "attacker",
                        "type": "User",
                        "site_admin": False,
                    },
                    "author_association": "NONE",
                },
            ],
        }
        for name, comments in cases.items():
            with self.subTest(case=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                with self.assertRaises(pr_metadata.MetadataEditError):
                    _reconcile(
                        client,
                        receipt=receipt,
                        comments=comments,
                    )
                self.assertFalse(
                    any(
                        method not in {"GET", "POST"}
                        for method, _endpoint, _body in client.calls
                    )
                )

    def test_confirmation_may_share_intent_second_but_not_predate_it(self):
        receipt = _receipt()
        confirmation = _confirmation(receipt)
        equal_comments = [
            _intent_comment(
                receipt,
                created_at="2026-09-04T00:00:01Z",
            ),
            _confirmation_comment(
                confirmation,
                created_at="2026-09-04T00:00:01Z",
            ),
        ]
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
        )
        decision = _reconcile(
            client,
            receipt=receipt,
            confirmation=confirmation,
            comments=equal_comments,
        )
        self.assertEqual(decision.action, "complete")

        predating_comments = [
            _intent_comment(
                receipt,
                created_at="2026-09-04T00:00:02Z",
            ),
            _confirmation_comment(
                confirmation,
                created_at="2026-09-04T00:00:01Z",
            ),
        ]
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "contradicts its intent",
        ):
            _reconcile(
                client,
                receipt=receipt,
                confirmation=confirmation,
                comments=predating_comments,
            )

    def test_terminal_edges_require_independent_time_and_id_ordering(self):
        receipt = _receipt()
        confirmation = _confirmation(receipt)
        abort = _abort(receipt)

        positive_cases = (
            (
                "confirmation-equal-time-higher-id",
                [
                    _intent_comment(
                        receipt,
                        comment_id=401,
                        created_at="2026-09-04T00:00:01Z",
                    ),
                    _confirmation_comment(
                        confirmation,
                        comment_id=402,
                        created_at="2026-09-04T00:00:01Z",
                    ),
                ],
            ),
            (
                "abort-equal-time-higher-id",
                [
                    _intent_comment(
                        receipt,
                        comment_id=401,
                        created_at="2026-09-04T00:00:01Z",
                    ),
                    _abort_comment(
                        abort,
                        comment_id=403,
                        created_at="2026-09-04T00:00:01Z",
                    ),
                ],
            ),
        )
        for name, comments in positive_cases:
            with self.subTest(case=name):
                client = ScriptedClient()
                _add_receipt_comments = _query(
                    f"issues/{PR_NUMBER}/comments",
                    [("per_page", "100"), ("page", "1")],
                )
                client.add_stable_comment_pages("GET", _add_receipt_comments, comments)
                intents, confirmations, aborts = pr_metadata._transaction_comments(
                    client,
                    pr_metadata._parse_pull_request_payload(
                        _pr(),
                        REPOSITORY,
                        PR_NUMBER,
                    ),
                )
                self.assertEqual(len(intents), 1)
                self.assertEqual(
                    bool(confirmations),
                    "confirmation" in name,
                )
                self.assertEqual(bool(aborts), "abort" in name)

        negative_cases = (
            (
                "confirmation-later-time-lower-id",
                [
                    _intent_comment(
                        receipt,
                        comment_id=401,
                        created_at="2026-09-04T00:00:01Z",
                    ),
                    _confirmation_comment(
                        confirmation,
                        comment_id=400,
                        created_at="2026-09-04T00:00:02Z",
                    ),
                ],
            ),
            (
                "abort-later-time-lower-id",
                [
                    _intent_comment(
                        receipt,
                        comment_id=401,
                        created_at="2026-09-04T00:00:01Z",
                    ),
                    _abort_comment(
                        abort,
                        comment_id=400,
                        created_at="2026-09-04T00:00:02Z",
                    ),
                ],
            ),
            (
                "abort-earlier-time-higher-id",
                [
                    _intent_comment(
                        receipt,
                        comment_id=401,
                        created_at="2026-09-04T00:00:02Z",
                    ),
                    _abort_comment(
                        abort,
                        comment_id=403,
                        created_at="2026-09-04T00:00:01Z",
                    ),
                ],
            ),
        )
        for name, comments in negative_cases:
            with self.subTest(case=name):
                client = ScriptedClient()
                client.add_stable_comment_pages(
                    "GET",
                    _query(
                        f"issues/{PR_NUMBER}/comments",
                        [("per_page", "100"), ("page", "1")],
                    ),
                    comments,
                )
                with self.assertRaisesRegex(
                    pr_metadata.MetadataEditError,
                    "contradicts its intent",
                ):
                    pr_metadata._transaction_comments(
                        client,
                        pr_metadata._parse_pull_request_payload(
                            _pr(),
                            REPOSITORY,
                            PR_NUMBER,
                        ),
                    )

    def test_same_second_aborted_predecessor_and_successor_are_ordered(self):
        predecessor = _receipt(nonce="a" * 64)
        predecessor_abort = _abort(predecessor)
        successor = _receipt(nonce="b" * 64)
        successor_confirmation = _confirmation(
            successor,
            intent_comment_id=404,
        )
        comments = [
            _intent_comment(
                predecessor,
                comment_id=401,
                created_at="2026-09-04T00:00:01Z",
            ),
            _abort_comment(
                predecessor_abort,
                comment_id=403,
                created_at="2026-09-04T00:00:01Z",
            ),
            _intent_comment(
                successor,
                comment_id=404,
                created_at="2026-09-04T00:00:01Z",
            ),
            _confirmation_comment(
                successor_confirmation,
                comment_id=405,
                created_at="2026-09-04T00:00:02Z",
            ),
        ]
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
        )
        decision = _reconcile(
            client,
            receipt=successor,
            confirmation=successor_confirmation,
            confirmation_comment_id=405,
            comments=comments,
        )
        self.assertEqual(decision.action, "complete")
        self.assertEqual(decision.run_id, 202)

    def test_multiple_same_second_active_intents_remain_ambiguous(self):
        first = _receipt(nonce="a" * 64)
        second = _receipt(nonce="b" * 64)
        client = ScriptedClient()
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
        _add_metadata_versions(client, (_pr(), _metadata_version()))
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [
                _intent_comment(first, comment_id=401),
                _intent_comment(second, comment_id=402),
            ],
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "latest metadata edit intent is ambiguous",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="new body",
                essential_reason=None,
            )

    def test_conflicting_confirmation_and_abort_links_fail_closed(self):
        receipt = _receipt()
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "both confirmation and abort",
        ):
            _reconcile(
                client,
                receipt=receipt,
                confirmation=_confirmation(receipt),
                comments=[
                    _intent_comment(receipt),
                    _confirmation_comment(_confirmation(receipt)),
                    _abort_comment(_abort(receipt)),
                ],
            )

    def test_abort_comments_close_intents_and_fail_closed(self):
        receipt = _receipt(
            provided_fields={"body": _sha256("new body")},
            changed_fields={"body": _sha256("new body")},
            pre_title="Stable title",
            pre_body="Stable body",
            pre_metadata_sha256=_metadata_sha256(
                "Stable title",
                "Stable body",
            ),
            target_metadata_sha256=_metadata_sha256(
                "Stable title",
                "new body",
            ),
        )
        abort = _abort(receipt)
        client = ScriptedClient()
        target_state = _pr(body="new body")
        _add_pr_states(client, target_state, target_state)
        _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
        _add_metadata_versions(
            client,
            (target_state, _confirmation(receipt).metadata_version),
        )
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [_intent_comment(receipt), _abort_comment(abort)],
        )
        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "refused")
        self.assertFalse(
            any(method == "PATCH" for method, _endpoint, _body in client.calls)
        )

        for name, comments in (
            (
                "duplicate",
                [
                    _intent_comment(receipt),
                    _abort_comment(abort),
                    _abort_comment(abort, comment_id=404),
                ],
            ),
            (
                "forged",
                [
                    _intent_comment(receipt),
                    _abort_comment(
                        replace(abort, intent_nonce="f" * 64),
                    ),
                ],
            ),
        ):
            with self.subTest(case=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(
                    client,
                    [_run(101, 10, mode="full")],
                    copies=2,
                )
                _add_metadata_versions(client, (_pr(), _metadata_version()))
                client.add_stable_comment_pages(
                    "GET",
                    _query(
                        f"issues/{PR_NUMBER}/comments",
                        [("per_page", "100"), ("page", "1")],
                    ),
                    comments,
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.edit_metadata(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        title=None,
                        body="new body",
                        essential_reason=None,
                    )

    def test_later_direct_metadata_changes_invalidate_receipt(self):
        title_receipt = _receipt(
            provided_fields={"title": _sha256("Stable title")},
        )
        cases = (
            (
                "later-body-edit",
                _pr(
                    body="changed without helper",
                    updated_at="2026-09-04T00:00:02Z",
                ),
                title_receipt,
            ),
            (
                "unsupported-direct-edit",
                _pr(
                    title="Direct edit",
                    updated_at="2026-09-04T00:00:02Z",
                ),
                _receipt(),
            ),
        )
        for name, state, receipt in cases:
            with self.subTest(case=name):
                client = ScriptedClient()
                _add_pr_states(client, state)
                changed_version = _metadata_version(
                    title_event_id="RTE_3",
                    title_event_created_at="2026-09-04T00:00:03Z",
                    title_previous="Stable title",
                    title_current=state["title"],
                    body_last_edited_at="2026-09-04T00:00:03Z",
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    _reconcile(
                        client,
                        receipt=receipt,
                        confirmation=_confirmation(receipt),
                        version=changed_version,
                        state=state,
                    )
                self.assertFalse(
                    any(
                        method not in {"GET", "POST"}
                        for method, _endpoint, _body in client.calls
                    )
                )

        client = ScriptedClient()
        _add_pr_states(
            client,
            _pr(updated_at="2026-09-04T00:00:02Z"),
        )
        later_record, _later_jobs = _run(
            203,
            12,
            mode="metadata-only",
            active=True,
        )
        later_record.update(
            {
                "created_at": "2026-09-04T00:00:02Z",
                "run_started_at": None,
                "status": "queued",
                "updated_at": "2026-09-04T00:00:02Z",
            }
        )
        _add_snapshot(
            client,
            [(later_record, []), _run(101, 10, mode="full")],
        )
        decision = _reconcile(client)
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.run_id, 203)
        self.assertFalse(decision.mutated)

    def test_title_and_body_edit_revert_versions_invalidate_pair(self):
        cases = (
            (
                "title-revert",
                _receipt(
                    provided_fields={"title": _sha256("Stable title")},
                ),
                _metadata_version(
                    title_event_id="RTE_3",
                    title_event_created_at="2026-09-04T00:00:03Z",
                    title_previous="Temporary title",
                    title_current="Stable title",
                ),
            ),
            (
                "body-revert",
                _receipt(),
                _metadata_version(
                    body_last_edited_at="2026-09-04T00:00:03Z",
                ),
            ),
        )
        for name, receipt, current_version in cases:
            with self.subTest(case=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                with self.assertRaises(pr_metadata.MetadataEditError):
                    _reconcile(
                        client,
                        receipt=receipt,
                        confirmation=_confirmation(receipt),
                        version=current_version,
                    )

    def test_metadata_version_actor_login_and_id_are_owner_bound(self):
        cases = (
            (
                "title-login",
                _receipt(
                    provided_fields={"title": _sha256("Stable title")},
                ),
                _metadata_version(title_actor_login="attacker"),
            ),
            (
                "body-login",
                _receipt(),
                _metadata_version(body_editor_login="attacker"),
            ),
            (
                "body-id",
                _receipt(),
                _metadata_version(body_editor_id=OWNER_ID + 1),
            ),
        )
        for name, receipt, version in cases:
            with self.subTest(case=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                with self.assertRaises(pr_metadata.MetadataEditError):
                    _reconcile(
                        client,
                        receipt=receipt,
                        confirmation=_confirmation(
                            receipt,
                            version=version,
                        ),
                        version=version,
                    )

    def test_metadata_version_actor_interface_variants_fail_closed(self):
        state = _pr()
        cases = {}
        body_bot = _graphql_payload(state, _metadata_version())
        body_bot["data"]["repository"]["pullRequest"]["editor"] = {
            "__typename": "Bot",
            "login": "automation[bot]",
        }
        cases["body-bot"] = body_bot
        body_deleted = _graphql_payload(state, _metadata_version())
        body_deleted["data"]["repository"]["pullRequest"]["editor"] = None
        cases["body-deleted"] = body_deleted
        title_bot = _graphql_payload(state, _metadata_version())
        title_bot["data"]["repository"]["pullRequest"]["timelineItems"][
            "nodes"
        ][0]["actor"] = {
            "__typename": "Bot",
            "login": "automation[bot]",
        }
        cases["title-bot"] = title_bot
        for name, payload in cases.items():
            with self.subTest(case=name):
                client = ScriptedClient()
                client.add("POST", "graphql", _response(payload))
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.fetch_metadata_version(
                        client,
                        pr_metadata._parse_pull_request_payload(
                            state,
                            REPOSITORY,
                            PR_NUMBER,
                        ),
                    )

        title_deleted = _graphql_payload(
            state,
            _metadata_version(
                title_actor_id=None,
                title_actor_login=None,
            ),
        )
        title_deleted["data"]["repository"]["pullRequest"]["timelineItems"][
            "nodes"
        ][0]["actor"] = None
        client = ScriptedClient()
        client.add("POST", "graphql", _response(title_deleted))
        version = pr_metadata.fetch_metadata_version(
            client,
            pr_metadata._parse_pull_request_payload(
                state,
                REPOSITORY,
                PR_NUMBER,
            ),
        )
        self.assertIsNone(version.title_actor_id)
        self.assertIsNone(version.title_actor_login)
        receipt = _receipt(
            provided_fields={"title": _sha256("Stable title")},
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "title metadata version does not uniquely attest",
        ):
            pr_metadata._confirmation_for_target(
                receipt,
                intent_comment_id=401,
                state=pr_metadata._parse_pull_request_payload(
                    state,
                    REPOSITORY,
                    PR_NUMBER,
                ),
                version=version,
            )

    def test_body_edit_connection_contract_fails_closed(self):
        state = _pr()
        base = _graphql_payload(
            state,
            _metadata_version(
                body_edit_total_count=2,
                body_edit_id="UCE_latest",
            ),
        )
        cases = {}
        missing = copy.deepcopy(base)
        del missing["data"]["repository"]["pullRequest"]["userContentEdits"]
        cases["missing-connection"] = missing
        omitted = copy.deepcopy(base)
        omitted["data"]["repository"]["pullRequest"]["userContentEdits"] = None
        cases["permission-omission"] = omitted
        count_rollback = copy.deepcopy(base)
        count_rollback["data"]["repository"]["pullRequest"][
            "userContentEdits"
        ]["totalCount"] = 1
        cases["count-cardinality"] = count_rollback
        bad_page = copy.deepcopy(base)
        bad_page["data"]["repository"]["pullRequest"]["userContentEdits"][
            "pageInfo"
        ]["hasPreviousPage"] = True
        cases["page-info"] = bad_page
        bad_cursor = copy.deepcopy(base)
        bad_cursor["data"]["repository"]["pullRequest"]["userContentEdits"][
            "pageInfo"
        ]["endCursor"] = "cursor-1"
        cases["cursor-range"] = bad_cursor
        deleted = copy.deepcopy(base)
        deleted["data"]["repository"]["pullRequest"]["userContentEdits"][
            "nodes"
        ][0]["deletedAt"] = "2026-09-04T00:00:01Z"
        cases["deleted-latest"] = deleted
        diff = copy.deepcopy(base)
        diff["data"]["repository"]["pullRequest"]["userContentEdits"][
            "nodes"
        ][0]["diff"] = "different body"
        cases["diff-inconsistent"] = diff
        same_second = copy.deepcopy(base)
        nodes = same_second["data"]["repository"]["pullRequest"][
            "userContentEdits"
        ]["nodes"]
        nodes[1]["editedAt"] = nodes[0]["editedAt"]
        nodes[1]["createdAt"] = nodes[0]["createdAt"]
        nodes[1]["updatedAt"] = nodes[0]["updatedAt"]
        cases["same-second-multiple"] = same_second
        reused = copy.deepcopy(base)
        reused["data"]["repository"]["pullRequest"]["userContentEdits"][
            "nodes"
        ][1]["id"] = "UCE_latest"
        cases["node-reuse"] = reused
        for name, payload in cases.items():
            with self.subTest(case=name):
                client = ScriptedClient()
                client.add("POST", "graphql", _response(payload))
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.fetch_metadata_version(
                        client,
                        pr_metadata._parse_pull_request_payload(
                            state,
                            REPOSITORY,
                            PR_NUMBER,
                        ),
                    )

    def test_bodyless_rest_state_matches_nonnull_graphql_body(self):
        raw = _pr(body=None)
        version = _metadata_version(body_last_edited_at=None)
        payload = _graphql_payload(raw, version)
        payload["data"]["repository"]["pullRequest"]["body"] = ""
        client = ScriptedClient()
        client.add("POST", "graphql", _response(payload))
        state = pr_metadata._parse_pull_request_payload(raw, REPOSITORY, PR_NUMBER)
        self.assertEqual(pr_metadata.fetch_metadata_version(client, state), version)
        self.assertEqual(state.body, "")

    def test_bodyless_title_creation_and_clearing_share_canonical_empty_body(self):
        cases = (
            ("title-only", None, "New title", None, None, {"title": "New title"}, 0),
            ("unchanged-empty", None, "New title", "", None, {"title": "New title"}, 0),
            ("first-body", None, None, "New body", "New body", {"body": "New body"}, 2),
            ("clear-body", "Stable body", None, "", None, {"body": ""}, 3),
        )
        for name, initial_body, title, body, returned_body, mutation, edit_count in cases:
            with self.subTest(case=name):
                pre_state = _pr(body=initial_body)
                pre_version = _metadata_version(
                    body_last_edited_at=(
                        None if initial_body is None else "2026-09-04T00:00:00Z"
                    )
                )
                client = ScriptedClient()
                _add_pr_states(client, pre_state, pre_state)
                _add_snapshot(client, [_run(101, 10, mode="full")], copies=2)
                _add_edit_transaction(
                    client,
                    title=title,
                    body=body,
                    pre_state=pre_state,
                    pre_version=pre_version,
                )
                client.add(
                    "PATCH",
                    _endpoint(f"pulls/{PR_NUMBER}"),
                    _pr(
                        title=title if title is not None else "Stable title",
                        body=returned_body,
                        updated_at="2026-09-04T00:00:05Z",
                    ),
                )
                decision = pr_metadata.edit_metadata(
                    client,
                    repository=REPOSITORY,
                    pr_number=PR_NUMBER,
                    head_sha=HEAD,
                    base_sha=BASE,
                    title=title,
                    body=body,
                    essential_reason=None,
                )
                self.assertEqual(decision.action, "updated")
                self.assertEqual(
                    [call[2] for call in client.calls if call[0] == "PATCH"],
                    [mutation],
                )
                posted = [
                    call[2]["body"] for call in client.calls
                    if call[:2] == ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
                ]
                intent = pr_metadata._parse_intent_comment_body(posted[0])
                confirmation = pr_metadata._parse_confirmation_comment_body(posted[1])
                self.assertEqual(
                    intent.pre_metadata_sha256,
                    _metadata_sha256(
                        "Stable title", "" if initial_body is None else initial_body
                    ),
                )
                self.assertEqual(
                    confirmation.metadata_version.body_edit_total_count, edit_count
                )

    def test_empty_body_normalization_does_not_admit_missing_or_malformed_fields(self):
        raw = _pr(body=None)
        raw.pop("body")
        with self.assertRaises(pr_metadata.MetadataEditError):
            pr_metadata._parse_pull_request_payload(raw, REPOSITORY, PR_NUMBER)
        state = pr_metadata._parse_pull_request_payload(
            _pr(body=None), REPOSITORY, PR_NUMBER
        )
        for value in (None, False, 0, [], {}, "unexpected body"):
            with self.subTest(body=value):
                payload = _graphql_payload(
                    _pr(body=None), _metadata_version(body_last_edited_at=None)
                )
                payload["data"]["repository"]["pullRequest"]["body"] = value
                client = ScriptedClient()
                client.add("POST", "graphql", payload)
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.fetch_metadata_version(client, state)
        payload = _graphql_payload(
            _pr(body=None), _metadata_version(body_last_edited_at=None)
        )
        payload["data"]["repository"]["pullRequest"].pop("body")
        client = ScriptedClient()
        client.add("POST", "graphql", payload)
        with self.assertRaises(pr_metadata.MetadataEditError):
            pr_metadata.fetch_metadata_version(client, state)

    def test_body_no_edit_to_first_edit_has_unique_node_authority(self):
        pre = _metadata_version(body_last_edited_at=None)
        post = _metadata_version(
            body_last_edited_at="2026-09-04T00:00:01Z",
            body_edit_total_count=2,
            body_edit_id="UCE_first",
            original_body="",
        )
        receipt = _receipt(
            pre_version=pre,
            pre_body="",
            pre_metadata_sha256=_metadata_sha256("Stable title", ""),
            target_metadata_sha256=_metadata_sha256(
                "Stable title",
                "Stable body",
            ),
        )
        state = pr_metadata._parse_pull_request_payload(
            _pr(),
            REPOSITORY,
            PR_NUMBER,
        )
        confirmation = pr_metadata._confirmation_for_target(
            receipt,
            intent_comment_id=401,
            state=state,
            version=post,
        )
        self.assertEqual(confirmation.metadata_version.body_edit_total_count, 2)
        self.assertEqual(confirmation.metadata_version.body_edit_id, "UCE_first")

    def test_real_first_and_subsequent_body_edits_confirm_and_reconcile(self):
        cases = (
            (None, True, False), ("", True, False),
            ("Original body 雪\n", True, True), ("Stable body", False, False),
        )
        for initial_body, first, delayed in cases:
            with self.subTest(body=initial_body, first=first, delayed=delayed):
                pre = _metadata_version(
                    body_last_edited_at=None if first else "2026-09-04T00:00:00Z"
                )
                client, posts = _mutation_client(
                    pre_state=_pr(body=initial_body), pre_version=pre
                )
                if delayed:
                    pull = client.routes[("POST", "graphql")][-1].payload[
                        "data"
                    ]["repository"]["pullRequest"]
                    for node in pull["userContentEdits"]["nodes"]:
                        node["createdAt"] = node["updatedAt"] = "2026-09-04T00:00:06Z"
                decision = pr_metadata.edit_metadata(
                    client, repository=REPOSITORY, pr_number=PR_NUMBER,
                    head_sha=HEAD, base_sha=BASE, title=None, body="new body",
                    essential_reason=None,
                )
                self.assertEqual(decision.action, "updated")
                self.assertEqual([c[2] for c in client.calls if c[0] == "PATCH"], [{"body": "new body"}])
                self.assertEqual(len(posts), 2)
                receipt = pr_metadata._parse_intent_comment_body(posts[0]["body"])
                confirmation = pr_metadata._parse_confirmation_comment_body(posts[1]["body"])
                version = confirmation.metadata_version
                self.assertEqual(version.body_edit_total_count, 2 if first else 3)
                self.assertEqual(receipt.pre_version, pre)
                if first:
                    self.assertEqual(
                        version.body_original.body_sha256,
                        dict((f.field, f.sha256) for f in receipt.pre_fields)["body"],
                    )
                    self.assertNotEqual(version.body_original.edit_id, version.body_edit_id)
                    self.assertEqual(version.body_original.authored_at, PR_CREATED_AT)
                else:
                    self.assertIsNone(version.body_original)
                target = _pr(body="new body", updated_at="2026-09-04T00:00:06Z")
                reconcile = ScriptedClient()
                _add_pr_states(reconcile, target, target)
                _add_snapshot(reconcile, [
                    _run(202, 11, mode="metadata-only", success=True,
                         metadata_event_payload=_metadata_event_payload(
                             _pr(body=initial_body),
                             _pr(body="new body", updated_at="2026-09-04T00:00:05Z"),
                         )),
                    _run(101, 10, mode="full"),
                ], copies=2)
                result = _reconcile(
                    reconcile, receipt=receipt, confirmation=confirmation,
                    comments=posts, version=version, state=target,
                    original_body=(initial_body or "") if first else "prior body",
                )
                self.assertEqual(result.action, "complete")
                self.assertEqual(result.run_id, 202)
                self.assertFalse(any(m != "GET" and e != "graphql" for m, e, _ in reconcile.calls))

    def test_first_body_edit_rejects_forged_original_and_multiple_edits(self):
        pre = _metadata_version(body_last_edited_at=None)
        client, _posts = _mutation_client(pre_state=_pr(body=None), pre_version=pre)
        payload = client.routes[("POST", "graphql")][-1].payload
        for name, changed in _first_body_history_controls(payload).items():
            with self.subTest(history=name):
                client, posts = _mutation_client(pre_state=_pr(body=None), pre_version=pre)
                client.routes[("POST", "graphql")][-1] = _response(changed)
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.edit_metadata(
                        client, repository=REPOSITORY, pr_number=PR_NUMBER,
                        head_sha=HEAD, base_sha=BASE, title=None, body="new body",
                        essential_reason=None,
                    )
                self.assertEqual([c[2] for c in client.calls if c[0] == "PATCH"], [{"body": "new body"}])
                self.assertEqual(len(posts), 1)
                self.assertTrue(posts[0]["body"].startswith(pr_metadata.INTENT_MARKER))

    def test_original_body_version_schema_and_confirmation_identity_are_bound(self):
        version = _metadata_version(original_body="")
        payload = version.canonical_payload()
        self.assertEqual(
            pr_metadata._parse_metadata_version_payload(payload, label="test"), version
        )
        for field, value in (
            ("body_sha256", []), ("author_id", True), ("author_login", ""),
            ("authored_at", version.body_edit_edited_at),
            ("materialized_at", "2026-09-04T00:00:01Z"),
            ("edit_id", version.body_edit_id), ("extra", "unexpected"),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(payload)
                changed["body_original"][field] = value
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._parse_metadata_version_payload(changed, label="test")
        for original in (None, {}, []):
            with self.subTest(original=original):
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._parse_metadata_version_payload(
                        {**payload, "body_original": original}, label="test"
                    )
        receipt = _receipt(
            pre_version=_metadata_version(body_last_edited_at=None), pre_body=""
        )
        confirmation = _confirmation(receipt, version=version)
        for field, value in (
            ("edit_id", "UCE_different_original"), ("author_id", OWNER_ID + 1),
            ("author_login", "another-user"), ("authored_at", "2026-09-01T00:00:00Z"),
        ):
            with self.subTest(forged_confirmation=field):
                forged = replace(
                    version, body_original=replace(version.body_original, **{field: value})
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._validate_confirmation(
                        replace(confirmation, metadata_version=forged), receipt,
                        intent_comment_id=401,
                        state=pr_metadata._parse_pull_request_payload(_pr(), REPOSITORY, PR_NUMBER),
                        version=version,
                    )

    def test_same_second_post_confirmation_body_revert_invalidates_pair(self):
        receipt = _receipt()
        confirmed_version = _confirmation(receipt).metadata_version
        reverted_version = replace(
            confirmed_version,
            body_edit_total_count=confirmed_version.body_edit_total_count + 1,
            body_edit_id="UCE_revert",
        )
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        hidden_record, _hidden_jobs = _run(
            203,
            12,
            mode="metadata-only",
            active=True,
        )
        hidden_record["pull_requests"] = []
        _add_snapshot(
            client,
            [(hidden_record, []), _run(101, 10, mode="full")],
        )
        with self.assertRaises(pr_metadata.MetadataEditError):
            _reconcile(
                client,
                receipt=receipt,
                confirmation=_confirmation(
                    receipt,
                    version=confirmed_version,
                ),
                version=reverted_version,
            )

    def test_body_edit_count_and_node_must_advance_monotonically(self):
        pre = _metadata_version()
        receipt = _receipt(pre_version=pre)
        state = pr_metadata._parse_pull_request_payload(
            _pr(),
            REPOSITORY,
            PR_NUMBER,
        )
        for name, version in (
            (
                "count-rollback",
                replace(pre, body_edit_total_count=0),
            ),
            (
                "node-reuse",
                replace(
                    pre,
                    body_edit_total_count=pre.body_edit_total_count + 1,
                ),
            ),
            (
                "count-jump",
                replace(
                    pre,
                    body_edit_total_count=pre.body_edit_total_count + 2,
                    body_edit_id="UCE_jump",
                ),
            ),
        ):
            with self.subTest(case=name):
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._confirmation_for_target(
                        receipt,
                        intent_comment_id=401,
                        state=state,
                        version=version,
                    )

    def test_same_second_body_version_ambiguity_fails_closed(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        receipt = _receipt()
        ambiguous_version = receipt.pre_version
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "body metadata version does not uniquely attest",
        ):
            _reconcile(
                client,
                receipt=receipt,
                confirmation=_confirmation(
                    receipt,
                    version=ambiguous_version,
                ),
                version=ambiguous_version,
            )

    def test_later_evidence_comment_does_not_invalidate_receipt(self):
        client = ScriptedClient()
        _add_pr_states(
            client,
            _pr(updated_at="2026-09-04T00:00:02Z"),
        )
        _add_snapshot(
            client,
            [
                _run(202, 11, mode="metadata-only", success=True),
                _run(101, 10, mode="full"),
            ],
        )
        receipt = _receipt()
        decision = _reconcile(
            client,
            receipt=receipt,
            comments=[
                _intent_comment(receipt),
                _confirmation_comment(_confirmation(receipt)),
                _comment(
                    500,
                    f"{pr_metadata.EVIDENCE_MARKER}\nUpdated evidence\n",
                    created_at="2026-09-04T00:00:02Z",
                    updated_at="2026-09-04T00:00:02Z",
                ),
            ],
        )
        self.assertEqual(decision.action, "complete")
        self.assertEqual(decision.run_id, 202)

    def test_incomplete_run_pagination_fails_closed(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr())
        client.add(
            "GET",
            _endpoint("actions/workflows/build.yml"),
            _workflow(),
        )
        client.add(
            "GET",
            _query(
                "actions/workflows/build.yml/runs",
                [
                    ("head_sha", HEAD),
                    ("per_page", "100"),
                    ("page", "1"),
                ],
            ),
            {"total_count": 101, "workflow_runs": [object() for _ in range(99)]},
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "cardinality is incomplete",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="new body",
                essential_reason=None,
            )

    def test_counted_pagination_consumes_every_exact_page(self):
        client = ScriptedClient()
        first = [{"id": index} for index in range(100)]
        second = [{"id": 100}]
        client.add(
            "GET",
            _test_runs_page(1),
            _response(
                {"total_count": 101, "workflow_runs": first},
                headers={
                    "link": _link(
                        ("next", _numeric_api_url(_test_runs_page(2))),
                        ("last", _numeric_api_url(_test_runs_page(2))),
                    )
                },
            ),
        )
        client.add(
            "GET",
            _test_runs_page(2),
            _response(
                {"total_count": 101, "workflow_runs": second},
                headers={
                    "link": _link(
                        ("prev", _numeric_api_url(_test_runs_page(1))),
                        ("first", _numeric_api_url(_test_runs_page(1))),
                    )
                },
            ),
        )
        result = pr_metadata._list_counted_pages(
            client,
            endpoint_for_page=_test_runs_page,
            item_key="workflow_runs",
            label="test runs",
            maximum=1000,
            repository=REPOSITORY,
            repository_id=REPOSITORY_ID,
        )
        self.assertEqual(result, first + second)
        self.assertEqual(
            [(method, endpoint) for method, endpoint, _body in client.calls],
            [("GET", _test_runs_page(1)), ("GET", _test_runs_page(2))],
        )

    def test_captured_numeric_job_link_shape_is_accepted(self):
        run_id = 33886934525

        def endpoint(page: int) -> str:
            return _query(
                f"actions/runs/{run_id}/attempts/1/jobs",
                [("per_page", "100"), ("page", str(page))],
            )

        client = ScriptedClient()
        first = [{"id": index} for index in range(100)]
        second = [{"id": 100}]
        client.add(
            "GET",
            endpoint(1),
            _response(
                {"total_count": 101, "jobs": first},
                headers={
                    "link": _link(
                        ("next", _numeric_api_url(endpoint(2))),
                        ("last", _numeric_api_url(endpoint(2))),
                    )
                },
            ),
        )
        client.add(
            "GET",
            endpoint(2),
            _response(
                {"total_count": 101, "jobs": second},
                headers={
                    "link": _link(
                        ("prev", _numeric_api_url(endpoint(1))),
                        ("first", _numeric_api_url(endpoint(1))),
                    )
                },
            ),
        )
        self.assertEqual(
            pr_metadata._list_counted_pages(
                client,
                endpoint_for_page=endpoint,
                item_key="jobs",
                label="captured jobs",
                maximum=1000,
                repository=REPOSITORY,
                repository_id=REPOSITORY_ID,
            ),
            first + second,
        )

    def test_counted_pagination_rejects_total_count_drift(self):
        client = ScriptedClient()
        client.add(
            "GET",
            _test_runs_page(1),
            _response(
                {
                    "total_count": 101,
                    "workflow_runs": [{"id": index} for index in range(100)],
                },
                headers={
                    "link": _link(
                        ("next", _numeric_api_url(_test_runs_page(2))),
                        ("last", _numeric_api_url(_test_runs_page(2))),
                    )
                },
            ),
        )
        client.add(
            "GET",
            _test_runs_page(2),
            _response(
                {"total_count": 100, "workflow_runs": []},
                headers={
                    "link": _link(
                        ("prev", _numeric_api_url(_test_runs_page(1))),
                        ("first", _numeric_api_url(_test_runs_page(1))),
                    )
                },
            ),
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "total_count changed",
        ):
            pr_metadata._list_counted_pages(
                client,
                endpoint_for_page=_test_runs_page,
                item_key="workflow_runs",
                label="test runs",
                maximum=1000,
                repository=REPOSITORY,
                repository_id=REPOSITORY_ID,
            )

    def test_counted_pagination_rejects_link_contradictions_and_loops(self):
        cases = {
            "missing-last": _link(
                ("next", _numeric_api_url(_test_runs_page(2))),
            ),
            "looping-next": _link(
                ("next", _numeric_api_url(_test_runs_page(1))),
                ("last", _numeric_api_url(_test_runs_page(2))),
            ),
            "wrong-next": _link(
                ("next", _numeric_api_url(_test_runs_page(3))),
                ("last", _numeric_api_url(_test_runs_page(2))),
            ),
            "duplicate-next": (
                _link(
                    ("next", _numeric_api_url(_test_runs_page(2))),
                    ("next", _numeric_api_url(_test_runs_page(2))),
                    ("last", _numeric_api_url(_test_runs_page(2))),
                )
            ),
            "escaped-host": _link(
                ("next", "https://example.test/runs?page=2"),
                ("last", _numeric_api_url(_test_runs_page(2))),
            ),
            "wrong-numeric-repo": _link(
                (
                    "next",
                    _numeric_api_url(_test_runs_page(2)).replace(
                        str(REPOSITORY_ID),
                        str(REPOSITORY_ID + 1),
                    ),
                ),
                ("last", _numeric_api_url(_test_runs_page(2))),
            ),
            "owner-repo-drift": _link(
                (
                    "next",
                    pr_metadata._api_url(_test_runs_page(2)).replace(
                        f"/repos/{REPOSITORY}/",
                        "/repos/other/repo/",
                    ),
                ),
                ("last", _numeric_api_url(_test_runs_page(2))),
            ),
            "percent-path": _link(
                (
                    "next",
                    _numeric_api_url(_test_runs_page(2)).replace(
                        "/actions/",
                        "/%61ctions/",
                    ),
                ),
                ("last", _numeric_api_url(_test_runs_page(2))),
            ),
            "percent-query": _link(
                (
                    "next",
                    _numeric_api_url(_test_runs_page(2)).replace(
                        "page=2",
                        "page=%32",
                    ),
                ),
                ("last", _numeric_api_url(_test_runs_page(2))),
            ),
            "malformed": "not-a-link",
        }
        for name, link in cases.items():
            with self.subTest(case=name):
                client = ScriptedClient()
                client.add(
                    "GET",
                    _test_runs_page(1),
                    _response(
                        {
                            "total_count": 101,
                            "workflow_runs": [
                                {"id": index} for index in range(100)
                            ],
                        },
                        headers={"link": link},
                    ),
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._list_counted_pages(
                        client,
                        endpoint_for_page=_test_runs_page,
                        item_key="workflow_runs",
                        label="test runs",
                        maximum=1000,
                        repository=REPOSITORY,
                        repository_id=REPOSITORY_ID,
                    )

    def test_run_authority_rejects_duplicate_id_and_number(self):
        duplicate_cases = []
        first = _run(202, 11, mode="metadata-only", success=False)
        duplicate_id_record, duplicate_id_jobs = _run(
            202,
            10,
            mode="full",
        )
        duplicate_cases.append(
            ("run-id", [first, (duplicate_id_record, duplicate_id_jobs)])
        )
        duplicate_number_record, duplicate_number_jobs = _run(
            101,
            11,
            mode="full",
        )
        duplicate_cases.append(
            ("run-number", [first, (duplicate_number_record, duplicate_number_jobs)])
        )
        for name, snapshot in duplicate_cases:
            with self.subTest(identity=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                _add_snapshot(client, snapshot)
                with self.assertRaisesRegex(
                    pr_metadata.MetadataEditError,
                    "changed number|reuse a run number",
                ):
                    pr_metadata.list_candidate_runs(
                        client,
                        pr_metadata.fetch_pull_request(
                            client,
                            REPOSITORY,
                            PR_NUMBER,
                        ),
                    )

    def test_run_authority_collapses_same_identity_to_latest_attempt(self):
        client = ScriptedClient()
        attempt_one = _run(
            101,
            10,
            mode="full",
            success=False,
            attempt=1,
        )
        attempt_two = _run(
            101,
            10,
            mode="full",
            success=True,
            attempt=2,
        )
        _add_pr_states(client, _pr())
        _add_snapshot(client, [attempt_one, attempt_two])
        runs = pr_metadata.list_candidate_runs(
            client,
            pr_metadata.fetch_pull_request(
                client,
                REPOSITORY,
                PR_NUMBER,
            ),
        )
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].run_id, 101)
        self.assertEqual(runs[0].run_number, 10)
        self.assertEqual(runs[0].run_attempt, 2)
        self.assertEqual(runs[0].conclusion, "success")

    def test_enum_values_reject_unhashable_and_nonstring_json(self):
        for value in ([], {}, ["success"], {"reason": "candidate-drift"}, True, 1):
            with self.subTest(field="abort.reason", value=value):
                abort = _abort(_receipt()).canonical_payload()
                abort["reason"] = value
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._parse_edit_abort(abort)
            for field in ("run", "job"):
                with self.subTest(field=field, value=value):
                    client = ScriptedClient()
                    record, jobs = _run(101, 10, mode="full")
                    if field == "run":
                        record["conclusion"] = value
                    else:
                        jobs[0]["conclusion"] = value
                    _add_pr_states(client, _pr())
                    _add_snapshot(client, [(record, jobs)])
                    state = pr_metadata.fetch_pull_request(client, REPOSITORY, PR_NUMBER)
                    with self.assertRaises(pr_metadata.MetadataEditError):
                        pr_metadata.list_candidate_runs(client, state)

    def test_run_authority_rejects_wrong_workflow_repo_head_event_and_path(self):
        mutations = {
            "workflow": ("workflow_id", WORKFLOW_ID + 1),
            "repo-url": (
                "url",
                "https://api.github.com/repos/other/repo/actions/runs/101",
            ),
            "head": ("head_sha", NEW_HEAD),
            "branch": ("head_branch", "other"),
            "event": ("event", "push"),
            "path": ("path", ".github/workflows/other.yml"),
            "unknown-conclusion": ("conclusion", "mystery"),
            "unknown-status": ("status", "mystery"),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(identity=name):
                client = ScriptedClient()
                record, jobs = _run(101, 10, mode="full")
                record[field] = value
                _add_pr_states(client, _pr())
                _add_snapshot(client, [(record, jobs)])
                state = pr_metadata.fetch_pull_request(
                    client,
                    REPOSITORY,
                    PR_NUMBER,
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.list_candidate_runs(client, state)

    def test_workflow_payload_identity_is_fully_bound(self):
        mutations = {
            "name": {"name": "Other"},
            "node": {"node_id": None},
            "path": {"path": ".github/workflows/other.yml"},
            "state": {"state": "disabled_manually"},
            "api-url": {
                "url": (
                    "https://api.github.com/repos/other/repo/"
                    f"actions/workflows/{WORKFLOW_ID}"
                )
            },
            "html-url": {
                "html_url": (
                    f"https://github.com/{REPOSITORY}/blob/main/"
                    f"{pr_metadata.WORKFLOW_PATH}"
                )
            },
            "badge-url": {
                "badge_url": (
                    f"https://github.com/{REPOSITORY}/workflows/Other/badge.svg"
                )
            },
        }
        for name, changes in mutations.items():
            with self.subTest(identity=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                client.add(
                    "GET",
                    _endpoint("actions/workflows/build.yml"),
                    _workflow(**changes),
                )
                state = pr_metadata.fetch_pull_request(
                    client,
                    REPOSITORY,
                    PR_NUMBER,
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._workflow_authority(client, state)

    def test_job_pages_reject_mixed_run_attempt_repo_head_and_urls(self):
        mutation_builders = {
            "missing-id": lambda job: job.pop("id"),
            "duplicate-id": lambda job: job.update(
                {
                    "id": 101 * 100 + 1,
                    "url": (
                        f"https://api.github.com/repos/{REPOSITORY}/"
                        f"actions/jobs/{101 * 100 + 1}"
                    ),
                    "html_url": (
                        f"https://github.com/{REPOSITORY}/actions/runs/101/"
                        f"job/{101 * 100 + 1}"
                    ),
                    "check_run_url": (
                        f"https://api.github.com/repos/{REPOSITORY}/"
                        f"check-runs/{101 * 100 + 1}"
                    ),
                }
            ),
            "run-id": lambda job: job.update({"run_id": 999}),
            "run-attempt": lambda job: job.update({"run_attempt": 2}),
            "run-url": lambda job: job.update(
                {
                    "run_url": (
                        f"https://api.github.com/repos/{REPOSITORY}/"
                        "actions/runs/999"
                    )
                }
            ),
            "repo-url": lambda job: job.update(
                {
                    "url": (
                        "https://api.github.com/repos/other/repo/"
                        f"actions/jobs/{job['id']}"
                    )
                }
            ),
            "html-url": lambda job: job.update(
                {
                    "html_url": (
                        f"https://github.com/{REPOSITORY}/"
                        f"actions/runs/999/job/{job['id']}"
                    )
                }
            ),
            "check-run-url": lambda job: job.update(
                {
                    "check_run_url": (
                        f"https://api.github.com/repos/{REPOSITORY}/"
                        "check-runs/999"
                    )
                }
            ),
            "head": lambda job: job.update({"head_sha": NEW_HEAD}),
            "branch": lambda job: job.update({"head_branch": "other"}),
            "workflow": lambda job: job.update({"workflow_name": "Other"}),
            "event": lambda job: job.update({"event": "push"}),
            "node": lambda job: job.update({"node_id": None}),
        }
        for name, mutate in mutation_builders.items():
            with self.subTest(identity=name):
                client = ScriptedClient()
                record, jobs = _run(101, 10, mode="full")
                jobs = copy.deepcopy(jobs)
                mutate(jobs[-1])
                _add_pr_states(client, _pr())
                _add_snapshot(client, [(record, jobs)])
                state = pr_metadata.fetch_pull_request(
                    client,
                    REPOSITORY,
                    PR_NUMBER,
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.list_candidate_runs(client, state)

    def test_captured_job_shape_without_optional_attempt_or_event_is_valid(self):
        client = ScriptedClient()
        record, jobs = _run(101, 10, mode="full")
        for job in jobs:
            del job["run_attempt"]
            del job["event"]
        _add_pr_states(client, _pr())
        _add_snapshot(client, [(record, jobs)])
        state = pr_metadata.fetch_pull_request(
            client,
            REPOSITORY,
            PR_NUMBER,
        )
        runs = pr_metadata.list_candidate_runs(client, state)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].run_id, 101)

    def test_job_timing_rejects_malformed_missing_and_reversed_values(self):
        mutations = {
            "missing-created": {"created_at": None},
            "missing-started": {"started_at": None},
            "missing-completed": {"completed_at": None},
            "24-hour": {"created_at": "2026-09-04T24:00:00Z"},
            "timezone": {"created_at": "2026-09-04T00:00:01+00:00"},
            "malformed": {"created_at": "not-a-time"},
            "created-after-start": {
                "created_at": "2026-09-04T00:00:02Z",
                "started_at": "2026-09-04T00:00:01Z",
            },
            "completed-before-start": {
                "started_at": "2026-09-04T00:00:02Z",
                "completed_at": "2026-09-04T00:00:01Z",
            },
            "after-run": {
                "completed_at": "2026-09-04T00:00:04Z",
            },
            "before-run": {
                "created_at": "2026-09-03T23:59:59Z",
                "started_at": "2026-09-04T00:00:01Z",
            },
        }
        for name, changes in mutations.items():
            with self.subTest(timing=name):
                client = ScriptedClient()
                record, jobs = _run(101, 10, mode="full")
                jobs = copy.deepcopy(jobs)
                target = next(job for job in jobs if job["name"] == "build")
                target.update(changes)
                _add_pr_states(client, _pr())
                _add_snapshot(client, [(record, jobs)])
                state = pr_metadata.fetch_pull_request(
                    client,
                    REPOSITORY,
                    PR_NUMBER,
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.list_candidate_runs(client, state)

    def test_run_timing_rejects_malformed_missing_and_reversed_values(self):
        mutations = {
            "missing-created": {"created_at": None},
            "missing-started": {"run_started_at": None},
            "missing-updated": {"updated_at": None},
            "24-hour": {"created_at": "2026-09-04T24:00:00Z"},
            "timezone": {"run_started_at": "2026-09-04T00:00:00+00:00"},
            "reversed": {
                "run_started_at": "2026-09-04T00:00:03Z",
                "updated_at": "2026-09-04T00:00:02Z",
            },
        }
        for name, changes in mutations.items():
            with self.subTest(timing=name):
                client = ScriptedClient()
                record, jobs = _run(101, 10, mode="full")
                record.update(changes)
                _add_pr_states(client, _pr())
                _add_snapshot(client, [(record, jobs)])
                state = pr_metadata.fetch_pull_request(
                    client,
                    REPOSITORY,
                    PR_NUMBER,
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.list_candidate_runs(client, state)

    def test_active_run_updated_at_is_not_a_job_completion_upper_bound(self):
        client = ScriptedClient()
        record, jobs = _run(
            202,
            11,
            mode="metadata-only",
            active=True,
        )
        record["updated_at"] = "2026-09-04T00:00:01Z"
        _add_pr_states(client, _pr())
        _add_snapshot(client, [(record, jobs)])
        state = pr_metadata.fetch_pull_request(
            client,
            REPOSITORY,
            PR_NUMBER,
        )
        runs = pr_metadata.list_candidate_runs(client, state)
        self.assertEqual(runs[0].mode, "active-metadata-only")
        self.assertTrue(
            any(
                job.completed_at
                and job.completed_at > runs[0].updated_at
                for job in runs[0].jobs
            )
        )

    def test_active_run_still_enforces_intrinsic_job_chronology(self):
        client = ScriptedClient()
        record, jobs = _run(
            202,
            11,
            mode="metadata-only",
            active=True,
        )
        record["updated_at"] = "2026-09-04T00:00:01Z"
        target = next(job for job in jobs if job["name"] == "build")
        target["started_at"] = "2026-09-04T00:00:03Z"
        target["completed_at"] = "2026-09-04T00:00:02Z"
        _add_pr_states(client, _pr())
        _add_snapshot(client, [(record, jobs)])
        state = pr_metadata.fetch_pull_request(
            client,
            REPOSITORY,
            PR_NUMBER,
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "completion chronology is invalid",
        ):
            pr_metadata.list_candidate_runs(client, state)

    def test_terminal_refresh_supplies_job_completion_upper_bound(self):
        client = ScriptedClient()
        record, jobs = _run(101, 10, mode="full")
        record["updated_at"] = "2026-09-04T00:00:01Z"
        _add_pr_states(client, _pr())
        _add_snapshot(client, [(record, jobs)])
        refreshed = copy.deepcopy(record)
        refreshed["updated_at"] = "2026-09-04T00:00:03Z"
        route = ("GET", _endpoint("actions/runs/101"))
        client.routes[route][0] = refreshed
        state = pr_metadata.fetch_pull_request(
            client,
            REPOSITORY,
            PR_NUMBER,
        )
        runs = pr_metadata.list_candidate_runs(client, state)
        self.assertEqual(
            runs[0].updated_at.isoformat(),
            "2026-09-04T00:00:03+00:00",
        )

    def test_terminal_refresh_enforces_job_completion_upper_bound(self):
        client = ScriptedClient()
        record, jobs = _run(101, 10, mode="full")
        _add_pr_states(client, _pr())
        _add_snapshot(client, [(record, jobs)])
        refreshed = copy.deepcopy(record)
        refreshed["updated_at"] = "2026-09-04T00:00:01Z"
        route = ("GET", _endpoint("actions/runs/101"))
        client.routes[route][0] = refreshed
        state = pr_metadata.fetch_pull_request(
            client,
            REPOSITORY,
            PR_NUMBER,
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "completes after its workflow run",
        ):
            pr_metadata.list_candidate_runs(client, state)

    def test_queued_and_in_progress_job_timing_matches_live_shapes(self):
        client = ScriptedClient()
        record, jobs = _run(101, 10, mode="full", active=True)
        jobs = copy.deepcopy(jobs)
        active = next(job for job in jobs if job["name"] == "build")
        active.update(
            {
                "status": "in_progress",
                "runner_id": 1,
                "runner_name": "GitHub Actions 1",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "started_at": "2026-09-04T00:00:01Z",
            }
        )
        _add_pr_states(client, _pr())
        _add_snapshot(client, [(record, jobs)])
        state = pr_metadata.fetch_pull_request(
            client,
            REPOSITORY,
            PR_NUMBER,
        )
        runs = pr_metadata.list_candidate_runs(client, state)
        self.assertEqual(runs[0].status, "in_progress")

        invalid_client = ScriptedClient()
        invalid_record, invalid_jobs = _run(
            102,
            11,
            mode="full",
            active=True,
        )
        invalid_jobs = copy.deepcopy(invalid_jobs)
        queued = next(job for job in invalid_jobs if job["name"] == "build")
        queued["started_at"] = "2026-09-04T00:00:01Z"
        _add_pr_states(invalid_client, _pr())
        _add_snapshot(invalid_client, [(invalid_record, invalid_jobs)])
        invalid_state = pr_metadata.fetch_pull_request(
            invalid_client,
            REPOSITORY,
            PR_NUMBER,
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "started_at must be null",
        ):
            pr_metadata.list_candidate_runs(invalid_client, invalid_state)

    def test_live_skipped_one_second_timing_quirk_is_bounded(self):
        cases = {
            -1: ("2026-09-04T00:00:02Z", True),
            0: ("2026-09-04T00:00:03Z", True),
            1: ("2026-09-04T00:00:04Z", False),
            28: ("2026-09-04T00:00:31Z", False),
        }
        for delta, (completed_at, accepted) in cases.items():
            with self.subTest(delta=delta):
                client = ScriptedClient()
                record, jobs = _run(101, 10, mode="full")
                record["updated_at"] = "2026-09-04T00:01:00Z"
                jobs = copy.deepcopy(jobs)
                skipped = next(
                    job for job in jobs if job["name"] == "patch-release"
                )
                skipped.update(
                    {
                        "created_at": "2026-09-04T00:00:03Z",
                        "started_at": "2026-09-04T00:00:03Z",
                        "completed_at": completed_at,
                    }
                )
                _add_pr_states(client, _pr())
                _add_snapshot(client, [(record, jobs)])
                state = pr_metadata.fetch_pull_request(
                    client,
                    REPOSITORY,
                    PR_NUMBER,
                )
                if accepted:
                    self.assertEqual(
                        pr_metadata.list_candidate_runs(client, state)[0].run_id,
                        101,
                    )
                else:
                    with self.assertRaisesRegex(
                        pr_metadata.MetadataEditError,
                        "skipped timing is invalid",
                    ):
                        pr_metadata.list_candidate_runs(client, state)

    def test_unassigned_skipped_job_never_authorizes_runner_success(self):
        client = ScriptedClient()
        record, jobs = _run(101, 10, mode="full")
        jobs = copy.deepcopy(jobs)
        build = next(job for job in jobs if job["name"] == "build")
        build.update(
            {
                "conclusion": "skipped",
                "runner_id": None,
                "runner_name": None,
                "runner_group_id": None,
                "runner_group_name": None,
                "created_at": "2026-09-04T00:00:01Z",
                "started_at": "2026-09-04T00:00:01Z",
                "completed_at": "2026-09-04T00:00:01Z",
            }
        )
        _add_pr_states(client, _pr())
        _add_snapshot(client, [(record, jobs)])
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "not runner-backed success",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="new body",
                essential_reason=None,
            )

    def test_wrong_pr_or_base_binding_cannot_authorize_mutation(self):
        mutations = {
            "pr": ("number", PR_NUMBER + 1),
            "base": ("base", {"sha": "4" * 40}),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(identity=name):
                client = ScriptedClient()
                record, jobs = _run(101, 10, mode="full")
                record["pull_requests"][0][field] = value
                _add_pr_states(client, _pr())
                _add_snapshot(client, [(record, jobs)])
                decision = pr_metadata.edit_metadata(
                    client,
                    repository=REPOSITORY,
                    pr_number=PR_NUMBER,
                    head_sha=HEAD,
                    base_sha=BASE,
                    title=None,
                    body="new body",
                    essential_reason=None,
                )
                self.assertEqual(decision.action, "refused")
                self.assertFalse(
                    any(method != "GET" for method, _endpoint, _body in client.calls)
                )

        client = ScriptedClient()
        record, jobs = _run(101, 10, mode="full")
        record["pull_requests"][0]["head"] = {"sha": NEW_HEAD}
        _add_pr_states(client, _pr())
        _add_snapshot(client, [(record, jobs)])
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "contradicts its head",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="new body",
                essential_reason=None,
            )

    def test_unknown_or_mixed_run_shape_fails_closed(self):
        client = ScriptedClient()
        record, jobs = _run(101, 10, mode="full")
        jobs = [
            job
            for job in jobs
            if job["name"] != candidate_evidence.FULL_CLASSIFIER
        ]
        _add_pr_states(client, _pr())
        _add_snapshot(client, [(record, jobs)])
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "unknown or mixed job shape",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="new body",
                essential_reason=None,
            )
        self.assertFalse(any(method != "GET" for method, _endpoint, _body in client.calls))

    def test_noncanonical_successful_full_jobs_cannot_authorize_edit(self):
        client = ScriptedClient()
        record, jobs = _run(101, 10, mode="full")
        next(job for job in jobs if job["name"] == "build")[
            "conclusion"
        ] = "failure"
        _add_pr_states(client, _pr())
        _add_snapshot(client, [(record, jobs)])
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "not runner-backed success",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="new body",
                essential_reason=None,
            )
        self.assertFalse(
            any(method != "GET" for method, _endpoint, _body in client.calls)
        )

    def test_same_head_run_for_another_base_is_validated_then_ignored(self):
        client = ScriptedClient()
        other_base_record, _other_jobs = _run(202, 11, mode="full", active=True)
        other_base_record["pull_requests"][0]["base"]["sha"] = "4" * 40
        exact_full = _run(101, 10, mode="full")
        _add_pr_states(client, _pr(), _pr())
        _add_snapshot(
            client,
            [(other_base_record, []), exact_full],
            copies=2,
        )
        client.add(
            "PATCH",
            _endpoint(f"pulls/{PR_NUMBER}"),
            _pr(body="new stable body"),
        )
        _add_edit_transaction(client, body="new stable body")

        decision = pr_metadata.edit_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            title=None,
            body="new stable body",
            essential_reason=None,
        )
        self.assertEqual(decision.action, "updated")
        self.assertIn("reconcile", decision.guidance[0])
        receipt_call = next(
            call
            for call in client.calls
            if call[:2]
            == ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
        )
        self.assertEqual(
            pr_metadata._parse_intent_comment_body(
                receipt_call[2]["body"]
            ).watermark_run_id,
            202,
        )
        other_jobs_endpoint = _query(
            "actions/runs/202/attempts/1/jobs",
            [("per_page", "100"), ("page", "1")],
        )
        self.assertTrue(
            any(endpoint == other_jobs_endpoint for _method, endpoint, _body in client.calls)
        )

    def test_explicit_other_run_is_ignored_only_after_full_validation(self):
        client = ScriptedClient()
        other_record, other_jobs = _run(202, 11, mode="full")
        other_record["pull_requests"][0]["number"] = PR_NUMBER + 1
        other_record["pull_requests"][0]["base"]["sha"] = "4" * 40
        other_jobs = copy.deepcopy(other_jobs)
        other_jobs[0]["run_id"] = 999
        _add_pr_states(client, _pr())
        _add_snapshot(
            client,
            [(other_record, other_jobs), _run(101, 10, mode="full")],
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "run identity drifted",
        ):
            pr_metadata.edit_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
                title=None,
                body="new stable body",
                essential_reason=None,
            )
        self.assertFalse(
            any(method != "GET" for method, _endpoint, _body in client.calls)
        )

    def test_canonical_comment_update_uses_comment_api_only(self):
        client = ScriptedClient()
        body = (
            f"{pr_metadata.EVIDENCE_MARKER}\n"
            f"Candidate SHA: {HEAD}\n"
            "No ARM runtime test is required for this host-only orchestration change.\n"
        )
        _add_pr_states(client, _pr(), _pr())
        client.add_stable_comment_pages(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [
                _comment(
                    300,
                    "Contributor note",
                    author_login="contributor",
                    author_association="CONTRIBUTOR",
                ),
                _comment(
                    302,
                    "Bot note",
                    author_login="automation[bot]",
                    author_type="Bot",
                    author_association="NONE",
                ),
                {
                    **_comment(303, "Deleted-author note"),
                    "user": None,
                    "author_association": "NONE",
                },
                _comment(
                    301,
                    f"{pr_metadata.EVIDENCE_MARKER}\nOld evidence\n",
                ),
            ],
        )
        client.add(
            "PATCH",
            _endpoint("issues/comments/301"),
            _comment(301, body),
        )

        decision = pr_metadata.update_evidence_comment(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
            comment_body=body,
        )
        self.assertEqual(decision.action, "comment-updated")
        self.assertIsNone(decision.run_id)
        self.assertEqual(decision.comment_id, 301)
        self.assertEqual(
            decision.canonical_json(),
            _canonical_decision(
                action="comment-updated",
                comment_id=301,
                mutated=True,
                reason=(
                    "canonical evidence comment updated without editing pull "
                    "request metadata"
                ),
            ),
        )
        mutations = [call for call in client.calls if call[0] != "GET"]
        self.assertEqual(
            mutations,
            [("PATCH", _endpoint("issues/comments/301"), {"body": body})],
        )
        self.assertFalse(
            any(endpoint == _endpoint(f"pulls/{PR_NUMBER}") for _method, endpoint, _body in mutations)
        )
        self.assertEqual(
            len(
                [
                    call
                    for call in client.calls
                    if call[:2] == ("GET", _endpoint(f"pulls/{PR_NUMBER}"))
                ]
            ),
            2,
        )

    def test_decision_identifier_roles_are_strict(self):
        common = {
            "base_sha": BASE,
            "guidance": (),
            "head_sha": HEAD,
            "mutated": False,
            "reason": "fixture",
            "repository": REPOSITORY,
            "pr_number": PR_NUMBER,
        }
        run = pr_metadata.Decision(
            action="deferred",
            run_id=101,
            **common,
        )
        self.assertEqual(run.run_id, 101)
        self.assertIsNone(run.comment_id)
        comment = pr_metadata.Decision(
            action="comment-updated",
            comment_id=301,
            **common,
        )
        self.assertIsNone(comment.run_id)
        self.assertEqual(comment.comment_id, 301)
        updated = pr_metadata.Decision(
            action="updated",
            run_id=101,
            intent_comment_id=401,
            intent_comment_url=(
                f"https://github.com/{REPOSITORY}/pull/{PR_NUMBER}"
                "#issuecomment-401"
            ),
            confirmation_comment_id=402,
            confirmation_comment_url=(
                f"https://github.com/{REPOSITORY}/pull/{PR_NUMBER}"
                "#issuecomment-402"
            ),
            **common,
        )
        self.assertEqual(updated.run_id, 101)
        self.assertEqual(updated.intent_comment_id, 401)
        self.assertEqual(updated.confirmation_comment_id, 402)
        held_fields = {
            "intent_comment_id": 401,
            "intent_comment_url": (
                f"https://github.com/{REPOSITORY}/pull/{PR_NUMBER}#issuecomment-401"
            ),
        }
        held = pr_metadata.Decision(action="deferred", **common, **held_fields)
        self.assertFalse(held.mutated)
        self.assertIsNone(held.confirmation_comment_id)
        self.assertIsNone(held.abort_comment_id)
        with self.assertRaises(pr_metadata.MetadataEditError):
            replace(held, mutated=True)
        for changes in (
            {"action": "comment-updated"},
            {"action": "comment-updated", "run_id": 101},
            {"action": "comment-updated", "run_id": 101, "comment_id": 301},
            {"action": "deferred", "comment_id": 301},
            {"action": "deferred", "run_id": True},
            {"action": "deferred", "run_id": 0},
            {"action": "deferred", "run_id": 1000000000000000000},
            {"action": "updated"},
            {"action": "updated", "intent_comment_id": 401},
            {"action": "deferred", "intent_comment_id": 401},
            {"action": "updated", **held_fields},
            {"action": "no-op", **held_fields},
            {
                "action": "updated",
                "intent_comment_id": 401,
                "intent_comment_url": "https://example.test/forged",
            },
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.Decision(**common, **changes)

    def test_comment_mutation_response_must_attest_identity_and_body(self):
        desired = f"{pr_metadata.EVIDENCE_MARKER}\nNew evidence\n"
        for name, response, message in (
            (
                "body",
                _comment(
                    301,
                    f"{pr_metadata.EVIDENCE_MARKER}\nOld evidence\n",
                ),
                "did not attest",
            ),
            ("identity", _comment(302, desired), "did not attest"),
            (
                "author",
                _comment(301, desired, author_id=88),
                "did not attest",
            ),
            (
                "login",
                _comment(301, desired, author_login="other"),
                "did not attest",
            ),
            (
                "type",
                _comment(301, desired, author_type="Bot"),
                "did not attest",
            ),
            (
                "association",
                _comment(301, desired, author_association="NONE"),
                "did not attest",
            ),
            (
                "site-admin",
                _comment(301, desired, site_admin=True),
                "did not attest",
            ),
        ):
            with self.subTest(mismatch=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr(), _pr())
                client.add_stable_comment_pages(
                    "GET",
                    _query(
                        f"issues/{PR_NUMBER}/comments",
                        [("per_page", "100"), ("page", "1")],
                    ),
                    [
                        _comment(
                            301,
                            f"{pr_metadata.EVIDENCE_MARKER}\nOld evidence\n",
                        )
                    ],
                )
                client.add(
                    "PATCH",
                    _endpoint("issues/comments/301"),
                    response,
                )
                with self.assertRaisesRegex(
                    pr_metadata.MetadataEditError,
                    message,
                ):
                    pr_metadata.update_evidence_comment(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        comment_body=desired,
                    )

    def test_comment_author_and_identity_must_be_owner_scoped(self):
        mutations = {
            "missing-user": {"user": None},
            "wrong-login": {"user": {"id": 77, "login": "attacker", "type": "User", "site_admin": False}},
            "wrong-owner-id": {"user": {"id": 88, "login": "owner", "type": "User", "site_admin": False}},
            "bot": {"user": {"id": 77, "login": "owner", "type": "Bot", "site_admin": False}},
            "association": {"author_association": "NONE"},
            "site-admin": {"user": {"id": 77, "login": "owner", "type": "User", "site_admin": True}},
            "cross-repo-url": {
                "url": "https://api.github.com/repos/other/repo/issues/comments/301"
            },
            "wrong-issue": {
                "issue_url": (
                    "https://api.github.com/repos/owner/repo/issues/200"
                )
            },
            "wrong-html": {
                "html_url": (
                    "https://github.com/owner/repo/pull/200#issuecomment-301"
                )
            },
            "missing-node": {"node_id": None},
        }
        for name, changes in mutations.items():
            with self.subTest(mutation=name):
                client = ScriptedClient()
                raw = _comment(
                    301,
                    f"{pr_metadata.EVIDENCE_MARKER}\nOld evidence\n",
                )
                raw.update(changes)
                _add_pr_states(client, _pr())
                client.add_stable_comment_pages(
                    "GET",
                    _query(
                        f"issues/{PR_NUMBER}/comments",
                        [("per_page", "100"), ("page", "1")],
                    ),
                    [raw],
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.update_evidence_comment(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        comment_body=(
                            f"{pr_metadata.EVIDENCE_MARKER}\nNew evidence\n"
                        ),
                    )

    def test_replacement_evidence_rejects_transaction_markers_before_patch(self):
        for marker in (
            pr_metadata.INTENT_MARKER,
            pr_metadata.CONFIRMATION_MARKER,
            pr_metadata.ABORT_MARKER,
        ):
            for suffix in (marker, f"quoted `{marker}` text", marker + marker):
                with self.subTest(marker=marker, suffix=suffix):
                    desired = f"{pr_metadata.EVIDENCE_MARKER}\n{suffix}\n"
                    client = ScriptedClient()
                    _add_pr_states(client, _pr(), _pr())
                    client.add_stable_comment_pages(
                        "GET",
                        _query(
                            f"issues/{PR_NUMBER}/comments",
                            [("per_page", "100"), ("page", "1")],
                        ),
                        [_comment(301, f"{pr_metadata.EVIDENCE_MARKER}\nOld\n")],
                    )
                    client.add(
                        "PATCH", _endpoint("issues/comments/301"), _comment(301, desired)
                    )
                    with self.assertRaises(pr_metadata.MetadataEditError):
                        pr_metadata.update_evidence_comment(
                            client,
                            repository=REPOSITORY,
                            pr_number=PR_NUMBER,
                            head_sha=HEAD,
                            base_sha=BASE,
                            comment_body=desired,
                        )
                    self.assertFalse(
                        any(method == "PATCH" for method, _endpoint, _body in client.calls)
                    )
                    self.assertEqual(client.calls, [])

    def test_transaction_bodies_are_validated_before_creation(self):
        receipt = _receipt()
        state = pr_metadata._parse_pull_request_payload(_pr(), REPOSITORY, PR_NUMBER)
        bodies = (
            ("intent", receipt, pr_metadata._intent_comment_body(receipt)),
            (
                "confirmation",
                _confirmation(receipt),
                pr_metadata._confirmation_comment_body(_confirmation(receipt)),
            ),
            (
                "abort",
                _abort(receipt),
                pr_metadata._abort_comment_body(_abort(receipt)),
            ),
        )
        for kind, value, body in bodies:
            with self.subTest(kind=kind):
                client = ScriptedClient()
                client.add(
                    "POST",
                    _endpoint(f"issues/{PR_NUMBER}/comments"),
                    _comment(
                        405,
                        body,
                        created_at="2026-09-04T00:00:04Z",
                        updated_at="2026-09-04T00:00:04Z",
                    ),
                )
                actual = pr_metadata._create_transaction_comment(
                    client, state, body=body, label="transaction fixture"
                )
                self.assertEqual(getattr(actual, kind), value)
                self.assertEqual(len(client.calls), 1)
            for invalid in (
                body + pr_metadata.EVIDENCE_MARKER,
                body + body,
                body.splitlines()[0] + "\n{}\n",
                "unmarked content",
                pr_metadata.EVIDENCE_MARKER + "\nevidence\n",
            ):
                with self.subTest(kind=kind, invalid=invalid):
                    client = ScriptedClient()
                    with self.assertRaises(pr_metadata.MetadataEditError):
                        pr_metadata._create_transaction_comment(
                            client, state, body=invalid, label="transaction fixture"
                        )
                    self.assertEqual(client.calls, [])

    def test_created_transaction_location_matches_the_returned_comment(self):
        receipt = _receipt()
        state = pr_metadata._parse_pull_request_payload(_pr(), REPOSITORY, PR_NUMBER)
        bodies = (
            pr_metadata._intent_comment_body(receipt),
            pr_metadata._confirmation_comment_body(_confirmation(receipt)),
            pr_metadata._abort_comment_body(_abort(receipt)),
        )
        for body in bodies:
            with self.subTest(marker=body.splitlines()[0]):
                payload = _comment(
                    405, body,
                    created_at="2026-09-04T00:00:04Z",
                    updated_at="2026-09-04T00:00:04Z",
                )
                raw = (
                    "HTTP/2.0 201 Created\n"
                    "Content-Type: application/json; charset=utf-8\r\n"
                    f"Location: {payload['url']}\r\n\r\n"
                    + json.dumps(payload)
                ).encode("utf-8")

                def runner(arguments, **kwargs):
                    return subprocess.CompletedProcess(arguments, 0, stdout=raw, stderr=b"")

                client = pr_metadata.GitHubClient("/usr/bin/true", runner=runner)
                actual = pr_metadata._create_transaction_comment(
                    client, state, body=body, label="created comment fixture"
                )
                self.assertEqual(actual.comment_id, 405)
                self.assertEqual(actual.body, body)

    def test_created_transaction_location_rejects_other_resources(self):
        state = pr_metadata._parse_pull_request_payload(_pr(), REPOSITORY, PR_NUMBER)
        body = pr_metadata._intent_comment_body(_receipt())
        payload = _comment(
            405, body,
            created_at="2026-09-04T00:00:04Z",
            updated_at="2026-09-04T00:00:04Z",
        )
        locations = (
            "https://example.test/repos/owner/repo/issues/comments/405",
            "https://api.github.com/repos/owner/other/issues/comments/405",
            "https://api.github.com/repos/owner/repo/issues/comments/406",
            "https://api.github.com/repos/owner/repo/pulls/405",
            payload["url"] + "?redirect=1",
            payload["url"] + "#fragment",
            "",
        )
        for location in locations:
            with self.subTest(location=location):
                client = ScriptedClient()
                client.add(
                    "POST",
                    _endpoint(f"issues/{PR_NUMBER}/comments"),
                    _response(payload, status=201, headers={"location": location}),
                )
                with self.assertRaisesRegex(pr_metadata.MetadataEditError, "Location"):
                    pr_metadata._create_transaction_comment(
                        client, state, body=body, label="created comment fixture"
                    )

    def test_creation_location_is_not_enabled_for_other_request_contexts(self):
        raw = (
            "HTTP/2.0 201 Created\n"
            "Content-Type: application/json\r\n"
            "Location: https://api.github.com/repos/owner/repo/issues/comments/405\r\n\r\n"
            "{}"
        ).encode("utf-8")

        def runner(arguments, **kwargs):
            return subprocess.CompletedProcess(arguments, 0, stdout=raw, stderr=b"")

        for method, endpoint in (
            ("POST", _endpoint("actions/runs/101/rerun")),
            ("PATCH", _endpoint("issues/comments/405")),
            ("GET", _endpoint(f"issues/{PR_NUMBER}/comments")),
        ):
            with self.subTest(method=method, endpoint=endpoint):
                client = pr_metadata.GitHubClient("/usr/bin/true", runner=runner)
                with self.assertRaisesRegex(pr_metadata.MetadataEditError, "Location"):
                    client.request(method, endpoint, label="unexpected creation context")

    def test_comment_post_location_requires_http_created_status(self):
        payload = _comment(405, "not a creation response")
        raw = (
            "HTTP/2.0 200 OK\n"
            "Content-Type: application/json\r\n"
            f"Location: {payload['url']}\r\n\r\n"
            + json.dumps(payload)
        ).encode("utf-8")

        def runner(arguments, **kwargs):
            return subprocess.CompletedProcess(arguments, 0, stdout=raw, stderr=b"")

        client = pr_metadata.GitHubClient("/usr/bin/true", runner=runner)
        with self.assertRaisesRegex(pr_metadata.MetadataEditError, "Location"):
            client.request(
                "POST", _endpoint(f"issues/{PR_NUMBER}/comments"),
                body={"body": payload["body"]}, label="comment status fixture",
            )

    def test_duplicate_and_embedded_markers_are_rejected(self):
        cases = {
            "across-comments": [
                _comment(301, f"{pr_metadata.EVIDENCE_MARKER}\nOne\n"),
                _comment(302, f"{pr_metadata.EVIDENCE_MARKER}\nTwo\n"),
            ],
            "duplicate-one-comment": [
                _comment(
                    301,
                    f"{pr_metadata.EVIDENCE_MARKER}\n"
                    f"{pr_metadata.EVIDENCE_MARKER}\n",
                )
            ],
            "embedded": [
                _comment(
                    301,
                    f"prefix {pr_metadata.EVIDENCE_MARKER} suffix\n",
                )
            ],
        }
        for name, comments in cases.items():
            with self.subTest(marker=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr())
                client.add_stable_comment_pages(
                    "GET",
                    _query(
                        f"issues/{PR_NUMBER}/comments",
                        [("per_page", "100"), ("page", "1")],
                    ),
                    comments,
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.update_evidence_comment(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        comment_body=(
                            f"{pr_metadata.EVIDENCE_MARKER}\nNew evidence\n"
                        ),
                    )

    def test_comment_pagination_rejects_short_page_next_and_loop(self):
        page_one = _query(
            f"issues/{PR_NUMBER}/comments",
            [("per_page", "100"), ("page", "1")],
        )
        page_two_url = pr_metadata._api_url(
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "2")],
            )
        )
        cases = {
            "short-next": _link(
                ("next", page_two_url),
                ("last", page_two_url),
            ),
            "loop": _link(
                ("next", pr_metadata._api_url(page_one)),
                ("last", page_two_url),
            ),
        }
        for name, link in cases.items():
            with self.subTest(pagination=name):
                client = ScriptedClient()
                client.add_stable_comment_pages(
                    "GET",
                    page_one,
                    _response(
                        [_comment(301, "Architecture note")],
                        headers={"link": link},
                    ),
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._list_comments(
                        client,
                        REPOSITORY,
                        PR_NUMBER,
                        REPOSITORY_ID,
                        OWNER_ID,
                    )

    def test_comment_authority_rejects_deletion_shifting_protected_page_boundary(self):
        page_one = _query(
            f"issues/{PR_NUMBER}/comments",
            [("per_page", "100"), ("page", "1")],
        )
        page_two = _query(
            f"issues/{PR_NUMBER}/comments",
            [("per_page", "100"), ("page", "2")],
        )
        first_headers = {
            "link": _link(
                ("next", pr_metadata._api_url(page_two)),
                ("last", pr_metadata._api_url(page_two)),
            )
        }
        last_headers = {
            "link": _link(
                ("first", pr_metadata._api_url(page_one)),
                ("prev", pr_metadata._api_url(page_one)),
            )
        }
        receipt = _receipt()
        for kind in ("intent", "confirmation", "abort", "evidence"):
            with self.subTest(kind=kind):
                prefix = [
                    _comment(
                        number, f"ordinary {number}",
                        created_at="2026-09-04T00:00:01Z",
                        updated_at="2026-09-04T00:00:01Z",
                    )
                    for number in range(1, 101)
                ]
                prefix[0] = _comment(
                    1, "deleted contributor comment",
                    author_id=88, author_login="contributor",
                    author_association="CONTRIBUTOR",
                    created_at="2026-09-04T00:00:01Z",
                    updated_at="2026-09-04T00:00:01Z",
                )
                if kind == "intent":
                    boundary = _intent_comment(receipt, comment_id=401)
                elif kind == "confirmation":
                    prefix[49] = _intent_comment(receipt, comment_id=50)
                    boundary = _confirmation_comment(
                        _confirmation(receipt, intent_comment_id=50),
                        comment_id=401,
                    )
                elif kind == "abort":
                    prefix[49] = _intent_comment(receipt, comment_id=50)
                    boundary = _abort_comment(
                        _abort(receipt, intent_comment_id=50), comment_id=401
                    )
                else:
                    prefix[49] = _comment(
                        50, pr_metadata.EVIDENCE_MARKER + "\nfirst",
                        created_at="2026-09-04T00:00:01Z",
                        updated_at="2026-09-04T00:00:01Z",
                    )
                    boundary = _comment(
                        401, pr_metadata.EVIDENCE_MARKER + "\nsecond",
                        created_at="2026-09-04T00:00:02Z",
                        updated_at="2026-09-04T00:00:02Z",
                    )
                suffix = [
                    _comment(
                        number, f"tail {number}",
                        created_at="2026-09-04T00:00:03Z",
                        updated_at="2026-09-04T00:00:03Z",
                    )
                    for number in range(402, 451)
                ]
                client = ScriptedClient()
                client.add(
                    "GET", page_one,
                    _response(prefix, headers=first_headers),
                    _response(prefix[1:] + [boundary], headers=first_headers),
                )
                client.add(
                    "GET", page_two,
                    _response(suffix, headers=last_headers),
                    _response(suffix, headers=last_headers),
                )
                if kind == "evidence":
                    desired = pr_metadata.EVIDENCE_MARKER + "\nreplacement"
                    _add_pr_states(client, _pr(), _pr())
                    client.add(
                        "PATCH", _endpoint("issues/comments/50"),
                        _comment(
                            50, desired,
                            created_at="2026-09-04T00:00:01Z",
                            updated_at="2026-09-04T00:00:04Z",
                        ),
                    )
                    operation = lambda: pr_metadata.update_evidence_comment(
                        client, repository=REPOSITORY, pr_number=PR_NUMBER,
                        head_sha=HEAD, base_sha=BASE, comment_body=desired,
                    )
                else:
                    state = pr_metadata._parse_pull_request_payload(
                        _pr(), REPOSITORY, PR_NUMBER
                    )
                    operation = lambda: pr_metadata._transaction_comments(client, state)
                with self.assertRaises(pr_metadata.MetadataEditError):
                    operation()
                self.assertFalse(any(call[0] == "PATCH" for call in client.calls))

    def test_comment_authority_requires_two_equal_complete_ordered_observations(self):
        endpoint = _query(
            f"issues/{PR_NUMBER}/comments",
            [("per_page", "100"), ("page", "1")],
        )
        first = [_comment(1, "one"), _comment(2, "two")]
        for changed in (False, True):
            with self.subTest(changed=changed):
                client = ScriptedClient()
                client.add("GET", endpoint, first, list(reversed(first)) if changed else first)
                if changed:
                    with self.assertRaises(pr_metadata.MetadataEditError):
                        pr_metadata._list_comments(
                            client, REPOSITORY, PR_NUMBER, REPOSITORY_ID, OWNER_ID
                        )
                else:
                    actual = pr_metadata._list_comments(
                        client, REPOSITORY, PR_NUMBER, REPOSITORY_ID, OWNER_ID
                    )
                    self.assertEqual([comment.comment_id for comment in actual], [1, 2])
                self.assertEqual(len(client.calls), 2)

    def test_comment_pagination_consumes_canonical_linked_pages(self):
        client = ScriptedClient()
        page_one_endpoint = _query(
            f"issues/{PR_NUMBER}/comments",
            [("per_page", "100"), ("page", "1")],
        )
        page_two_endpoint = _query(
            f"issues/{PR_NUMBER}/comments",
            [("per_page", "100"), ("page", "2")],
        )
        page_one_url = _numeric_api_url(page_one_endpoint)
        page_two_url = _numeric_api_url(page_two_endpoint)
        client.add_stable_comment_pages(
            "GET",
            page_one_endpoint,
            _response(
                [_comment(1000 + index, f"Comment {index}") for index in range(100)],
                headers={
                    "link": _link(
                        ("next", page_two_url),
                        ("last", page_two_url),
                    )
                },
            ),
        )
        client.add_stable_comment_pages(
            "GET",
            page_two_endpoint,
            _response(
                [_comment(1100, "Last comment")],
                headers={
                    "link": _link(
                        ("prev", page_one_url),
                        ("first", page_one_url),
                    )
                },
            ),
        )
        comments = pr_metadata._list_comments(
            client,
            REPOSITORY,
            PR_NUMBER,
            REPOSITORY_ID,
            OWNER_ID,
        )
        self.assertEqual(len(comments), 101)

    def test_structured_api_argv_and_json_body_do_not_execute_input(self):
        calls = []

        def runner(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=b'HTTP/2 200 OK\nContent-Type: application/json\n\n{"ok":true}\n',
                stderr=b"",
            )

        client = pr_metadata.GitHubClient("/usr/bin/true", runner=runner)
        payload = {
            "title": '$(touch /tmp/never)"; gh run cancel 1; #',
            "body": "line\n--method DELETE\ncaf\u00e9",
        }
        result = client.request(
            "PATCH",
            "repos/owner/repo/pulls/199",
            body=payload,
            label="injection control",
        )
        self.assertEqual(result.payload, {"ok": True})
        arguments, kwargs = calls[0]
        self.assertIsInstance(arguments, list)
        self.assertEqual(arguments[0], client.gh_path)
        self.assertEqual(arguments[-2:], ["--input", "-"])
        self.assertIn("--include", arguments)
        self.assertIs(kwargs["text"], False)
        self.assertIsInstance(kwargs["input"], bytes)
        self.assertEqual(json.loads(kwargs["input"]), payload)
        self.assertNotIn("shell", kwargs)
        self.assertNotIn("/cancel", " ".join(arguments))

    def test_http_status_headers_and_redirects_fail_closed(self):
        cases = {
            "redirect": (
                "HTTP/2 302 Found\nLocation: https://example.test/\n\n",
                "rejected redirect",
            ),
            "duplicate-header": (
                "HTTP/2 200 OK\nContent-Type: application/json\n"
                "Content-Type: application/json\n\n{}\n",
                "repeats singleton header",
            ),
            "missing-headers": ('{"ok":true}\n', "lacks HTTP headers"),
            "wrong-status": (
                "HTTP/2 202 Accepted\nContent-Type: application/json\n\n{}\n",
                "expected 200",
            ),
            "wrong-content-type": (
                "HTTP/2 200 OK\nContent-Type: text/plain\n\n{}\n",
                "Content-Type is not application/json",
            ),
            "unexpected-location": (
                "HTTP/2 200 OK\nLocation: https://example.test/\n"
                "Content-Type: application/json\n\n{}\n",
                "unexpectedly contains Location",
            ),
        }
        for name, (stdout, message) in cases.items():
            with self.subTest(response=name):
                def runner(arguments, **kwargs):
                    del kwargs
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        stdout=stdout.encode("utf-8"),
                        stderr=b"",
                    )

                client = pr_metadata.GitHubClient(
                    "/usr/bin/true",
                    runner=runner,
                )
                with self.assertRaisesRegex(
                    pr_metadata.MetadataEditError,
                    message,
                ):
                    client.request(
                        "GET",
                        "repos/owner/repo/pulls/199",
                        label="HTTP fixture",
                    )

    def test_http_header_control_bytes_and_obs_fold_are_rejected(self):
        headers = {
            "content-type-cr-location": (
                "Content-Type: application/json\r"
                "Location: https://example.test/"
            ),
            "nul": "X-Test: value\x00suffix",
            "tab": "Content-Type:\tapplication/json",
            "vertical-tab": "Link: value\x0bsuffix",
            "form-feed": "Location: value\x0csuffix",
            "delete": "X-Test: value\x7fsuffix",
            "bare-lf": "X-Test: value\ninjected",
            "obs-fold": "Link: value\n continuation",
            "location-nul": "Location: https://example.test/\x00",
        }
        for name, header in headers.items():
            with self.subTest(header=name):
                raw = f"HTTP/2 200 OK\n{header}\n\n{{}}\n"
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._parse_http_response(
                        raw,
                        label="control fixture",
                        allow_empty_body=False,
                    )

    def test_http_crlf_headers_with_json_body_are_valid(self):
        response = pr_metadata._parse_http_response(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            "X-Empty: \r\n"
            "\r\n"
            '{"ok":true}\n',
            label="CRLF fixture",
            allow_empty_body=False,
        )
        self.assertEqual(response.payload, {"ok": True})

    def test_combinable_repeated_headers_and_link_relations_are_typed(self):
        next_url = _numeric_api_url(_test_runs_page(2))
        response = pr_metadata._parse_http_response(
            "HTTP/2 200 OK\n"
            "Content-Type: application/json; charset=utf-8\n"
            "Vary: Accept\n"
            "Vary: Authorization\n"
            "Cache-Control: private\n"
            "Cache-Control: max-age=60\n"
            f'Link: <{next_url}>; rel="next"\n'
            f'Link: <{next_url}>; rel="last"\n'
            "\n"
            "{}\n",
            label="repeat fixture",
            allow_empty_body=False,
        )
        self.assertEqual(response.headers["vary"], "Accept, Authorization")
        self.assertEqual(
            response.headers["cache-control"],
            "private, max-age=60",
        )
        relations = pr_metadata._parse_link_pages(
            response.headers["link"],
            endpoint_for_page=_test_runs_page,
            current_page=1,
            label="repeat fixture",
            repository=REPOSITORY,
            repository_id=REPOSITORY_ID,
        )
        self.assertEqual(relations, {"next": 2, "last": 2})

        duplicate = pr_metadata._parse_http_response(
            "HTTP/2 200 OK\n"
            "Content-Type: application/json\n"
            f'Link: <{next_url}>; rel="next"\n'
            f'Link: <{next_url}>; rel="next"\n'
            "\n"
            "{}\n",
            label="duplicate Link fixture",
            allow_empty_body=False,
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "repeats a relation",
        ):
            pr_metadata._parse_link_pages(
                duplicate.headers["link"],
                endpoint_for_page=_test_runs_page,
                current_page=1,
                label="duplicate Link fixture",
                repository=REPOSITORY,
                repository_id=REPOSITORY_ID,
            )

        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "repeats singleton header",
        ):
            pr_metadata._parse_http_response(
                "HTTP/2 200 OK\n"
                "Content-Type: application/json\n"
                "ETag: one\n"
                "ETag: two\n"
                "\n"
                "{}\n",
                label="singleton fixture",
                allow_empty_body=False,
            )

    def test_json_content_type_media_and_parameters_are_exact(self):
        accepted = (
            "application/json",
            "Application/JSON",
            "application/json; charset=utf-8",
            'application/json; profile="github"',
            'application/json; profile="a=b"',
            'application/json; profile="a;b"',
        )
        rejected = (
            "application/jsonp",
            "application/json-patch+json",
            "text/application/json",
            "application/json;",
            "application/json; charset",
            "application/json; charset=",
            "application/json; charset=utf-8; CHARSET=utf-8",
            'application/json; charset="unterminated',
            'application/json; profile="a\\"b"',
            'application/json; profile="a\\\\b"',
            'application/json; profile="a"junk',
            "application/json; profile=a=b",
            'application/json; profile=""',
            "application/json; =value",
            "application/json ; charset=utf-8",
            "application/json;  charset=utf-8",
            "application/json; charset =utf-8",
            "application/json; charset= utf-8",
            "application/json; charset=utf-8 ; profile=x",
        )
        for media_type in accepted:
            with self.subTest(accepted=media_type):
                response = pr_metadata._parse_http_response(
                    f"HTTP/2 200 OK\nContent-Type: {media_type}\n\n{{}}\n",
                    label="media fixture",
                    allow_empty_body=False,
                )
                self.assertEqual(response.payload, {})
        for media_type in rejected:
            with self.subTest(rejected=media_type):
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._parse_http_response(
                        f"HTTP/2 200 OK\nContent-Type: {media_type}\n\n{{}}\n",
                        label="media fixture",
                        allow_empty_body=False,
                    )

    def test_repository_and_json_inputs_fail_closed(self):
        with self.assertRaises(pr_metadata.MetadataEditError):
            pr_metadata._repository("owner/repo;gh-run-cancel")
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "repeats key",
        ):
            pr_metadata._parse_json('{"id":1,"id":2}', "test")

    def test_github_timestamp_parser_rejects_noncanonical_values(self):
        invalid = (
            None,
            "",
            "2026-09-04T24:00:00Z",
            "2026-09-04T23:60:00Z",
            "2026-02-30T12:00:00Z",
            "2026-09-04T12:00:00+00:00",
            "2026-09-04T12:00:00.000Z",
            "2026-9-4T12:00:00Z",
            "not-a-time",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata._github_timestamp(value, "timestamp")
        parsed = pr_metadata._github_timestamp(
            "2026-09-04T23:59:59Z",
            "timestamp",
        )
        self.assertEqual(parsed.hour, 23)

    def test_read_only_metadata_history_accepts_open_and_closed_fixtures(self):
        for state_name in ("open", "closed"):
            with self.subTest(state=state_name):
                state = _pr()
                state["state"] = state_name
                version = _metadata_version()
                client = ScriptedClient()
                _add_pr_states(client, state)
                _add_metadata_versions(client, (state, version))
                actual = pr_metadata.inspect_metadata_history(
                    client, REPOSITORY, PR_NUMBER
                )
                self.assertEqual(actual, version)
                self.assertEqual(
                    {(method, endpoint) for method, endpoint, _body in client.calls},
                    {("GET", _endpoint(f"pulls/{PR_NUMBER}")), ("POST", "graphql")},
                )
                self.assertEqual(len(client.calls), 2)

    def test_closed_fixtures_cannot_authorize_any_mutation_mode(self):
        calls = (
            (
                pr_metadata.edit_metadata,
                {"title": None, "body": "new body", "essential_reason": "urgent"},
            ),
            (pr_metadata.reconcile_metadata, {"confirmation_comment_id": 402}),
            (
                pr_metadata.update_evidence_comment,
                {"comment_body": f"{pr_metadata.EVIDENCE_MARKER}\nevidence\n"},
            ),
        )
        for operation, arguments in calls:
            with self.subTest(mode=operation.__name__):
                closed = _pr()
                closed["state"] = "closed"
                client = ScriptedClient()
                _add_pr_states(client, closed)
                with self.assertRaisesRegex(
                    pr_metadata.MetadataEditError, "pull request must be open"
                ):
                    operation(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                        **arguments,
                    )
                self.assertEqual(
                    client.calls, [("GET", _endpoint(f"pulls/{PR_NUMBER}"), None)]
                )

    def test_read_only_history_rejects_invalid_state_and_identity(self):
        cases = (
            {"state": None},
            {"state": "merged"},
            {"state": True},
            {"state": "closed", "number": PR_NUMBER + 1},
            {"state": "closed", "head": {"ref": HEAD_REF, "sha": "invalid"}},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                state = _pr()
                state.update(changes)
                client = ScriptedClient()
                _add_pr_states(client, state)
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.inspect_metadata_history(
                        client, REPOSITORY, PR_NUMBER
                    )
                self.assertEqual(len(client.calls), 1)

    def test_graphql_actor_interfaces_use_user_inline_fragments(self):
        query = "".join(pr_metadata.METADATA_VERSION_QUERY.split())
        self.assertIn(
            "author{__typenamelogin...onUser{databaseId}}",
            query,
        )
        self.assertIn(
            "actor{__typenamelogin...onUser{databaseId}}",
            query,
        )
        self.assertIn(
            "userContentEdits(first:2){totalCountpageInfo{"
            "hasNextPagehasPreviousPagestartCursorendCursor}",
            query,
        )
        self.assertIn(
            "editor{__typenamelogin...onUser{databaseId}}",
            query,
        )
        self.assertNotIn("editor{__typenamedatabaseId", query)
        self.assertNotIn("actor{__typenamedatabaseId", query)
        self.assertNotIn("author{__typenamedatabaseId", query)


class MetadataEventAttributionTests(unittest.TestCase):
    def test_producer_binds_raw_transition_and_exact_run_context(self):
        event = _metadata_event_payload()
        context = {"repository": REPOSITORY, "run_id": 202, "run_number": 11, "run_attempt": 1}
        original = metadata_event.event_digest(event, **context)
        reordered = dict(reversed(list(event.items())))
        self.assertEqual(metadata_event.event_digest(reordered, **context), original)
        for key in ("run_id", "run_number", "run_attempt"):
            changed = {**context, key: context[key] + 1}
            self.assertNotEqual(metadata_event.event_digest(event, **changed), original)
        for field in ("previous-body", "target-body", "title", "updated-at", "head", "base"):
            with self.subTest(field=field):
                changed = copy.deepcopy(event)
                pull = changed["pull_request"]
                if field == "previous-body":
                    changed["changes"]["body"]["from"] = "different previous body"
                elif field == "target-body":
                    pull["body"] = "different target body"
                elif field == "title":
                    pull["title"] = "changed supplied-unchanged title"
                elif field == "updated-at":
                    pull["updated_at"] = "2026-09-04T00:00:05Z"
                else:
                    pull[field]["sha"] = NEW_HEAD
                self.assertNotEqual(metadata_event.event_digest(changed, **context), original)
        empty = _metadata_event_payload(_pr(body="Old body"), _pr(body=None))
        null_digest = metadata_event.event_digest(empty, **context)
        empty["pull_request"]["body"] = ""
        self.assertEqual(metadata_event.event_digest(empty, **context), null_digest)

    def test_producer_rejects_unowned_malformed_or_nonmetadata_events(self):
        for drift in ("action", "number", "repository", "owner", "sender", "admin", "body", "changes", "from", "unchanged"):
            with self.subTest(drift=drift):
                event = _metadata_event_payload()
                if drift == "action":
                    event["action"] = "synchronize"
                elif drift == "number":
                    event["number"] = True
                elif drift == "repository":
                    event["repository"]["id"] += 1
                elif drift == "owner":
                    event["repository"]["owner"]["id"] += 1
                elif drift == "sender":
                    event["sender"]["id"] += 1
                elif drift == "admin":
                    event["sender"]["site_admin"] = True
                elif drift == "body":
                    event["pull_request"].pop("body")
                elif drift == "changes":
                    event["changes"]["base"] = {"from": BASE}
                elif drift == "from":
                    event["changes"]["body"] = {}
                else:
                    event["changes"]["body"]["from"] = event["pull_request"]["body"]
                with self.assertRaises(pr_metadata.MetadataEditError):
                    metadata_event.event_digest(
                        event, repository=REPOSITORY, run_id=202, run_number=11, run_attempt=1
                    )

    def pair(self, *, pre_version=None, title=None, body="new body"):
        pre_state = _pr(updated_at=(
            pre_version.body_last_edited_at if pre_version is not None
            else "2026-09-04T00:00:00Z"
        ))
        client, posts = _mutation_client(
            pre_state=pre_state, pre_version=pre_version, title=title,
            body=pre_state["body"] if body is None else body,
        )
        result = pr_metadata.edit_metadata(
            client, repository=REPOSITORY, pr_number=PR_NUMBER,
            head_sha=HEAD, base_sha=BASE, title=title, body=body,
            essential_reason=None,
        )
        self.assertEqual(result.action, "updated")
        return (
            pr_metadata._parse_intent_comment_body(posts[0]["body"]),
            pr_metadata._parse_confirmation_comment_body(posts[1]["body"]),
            posts,
            _pr(title=title or "Stable title", body=pre_state["body"] if body is None else body,
                updated_at="2026-09-04T00:00:05Z"),
        )

    def decide(self, pair, runs, *, mode="reconcile", refreshed_runs=None):
        receipt, confirmation, posts, state = pair
        if mode == "no-op":
            client, new_posts = _mutation_client(
                history=tuple(posts), pre_state=state,
                pre_version=confirmation.metadata_version, runs=runs,
            )
            result = pr_metadata.edit_metadata(
                client, repository=REPOSITORY, pr_number=PR_NUMBER,
                head_sha=HEAD, base_sha=BASE, title=None, body="new body",
                essential_reason=None,
            )
            self.assertEqual(new_posts, [])
        else:
            client = ScriptedClient()
            _add_pr_states(client, state, state)
            _add_snapshot(client, runs)
            _add_snapshot(client, runs if refreshed_runs is None else refreshed_runs)
            for run_id in (202, 303):
                client.add("POST", _endpoint(f"actions/runs/{run_id}/rerun"),
                           _response(None, status=201))
            result = _reconcile(
                client, receipt=receipt, confirmation=confirmation, comments=posts,
                state=state, version=confirmation.metadata_version,
            )
        return result, client

    def test_delayed_earlier_or_unattested_event_never_authorizes_the_edit(self):
        pair = self.pair()
        for mode in ("reconcile", "no-op"):
            for success in (False, True):
                for proof in ("absent", "skipped", "earlier"):
                    with self.subTest(mode=mode, success=success, proof=proof):
                        earlier = _run(
                            202, 11, mode="metadata-only", success=success,
                            metadata_event_payload=(
                                _metadata_event_payload() if proof == "earlier" else None
                            ),
                        )
                        if proof == "skipped":
                            classifier = next(job for job in earlier[1]
                                              if job["name"] == "metadata-classifier")
                            classifier["steps"] = [{
                                "name": metadata_event.STEP_PREFIX, "number": 1,
                                "status": "completed", "conclusion": "skipped",
                            }]
                        result, client = self.decide(
                            pair, [earlier, _run(101, 10, mode="full")], mode=mode
                        )
                        self.assertEqual(result.action, "deferred")
                        self.assertEqual(result.run_id, 101)
                        self.assertFalse(result.mutated)
                        self.assertFalse(any(
                            call[0] == "PATCH" or call[1].endswith("/rerun")
                            for call in client.calls
                        ))

    def test_matching_event_survives_an_earlier_delayed_run_and_rerun_attempt(self):
        pair = self.pair()
        event = _metadata_event_payload(_pr(), pair[3])
        for mode in ("reconcile", "no-op"):
            for success in (False, True):
                for attempt in (1, 2):
                    with self.subTest(mode=mode, success=success, attempt=attempt):
                        matching = _run(
                            303, 12, mode="metadata-only", success=success, attempt=attempt,
                            metadata_event_payload=event,
                        )
                        runs = [
                            matching, _run(202, 11, mode="metadata-only"),
                            _run(101, 10, mode="full"),
                        ]
                        result, client = self.decide(pair, runs, mode=mode)
                        expected = (
                            ("complete" if success else "rerun")
                            if mode == "reconcile" else ("no-op" if success else "deferred")
                        )
                        self.assertEqual(result.action, expected)
                        self.assertEqual(result.run_id, 303)
                        reruns = [call[1] for call in client.calls if call[1].endswith("/rerun")]
                        self.assertEqual(
                            reruns, [_endpoint("actions/runs/303/rerun")]
                            if mode == "reconcile" and not success else [],
                        )

    def test_native_version_ambiguity_and_event_instant_mismatch_hold(self):
        ordinary = self.pair()
        same_second = self.pair(pre_version=_metadata_version(
            body_last_edited_at="2026-09-04T00:00:05Z", body_edit_total_count=2,
        ))
        for label, pair, event_time in (
            ("same-second-revision", same_second, "2026-09-04T00:00:05Z"),
            ("earlier-identical-transition", ordinary, "2026-09-04T00:00:04Z"),
        ):
            with self.subTest(case=label):
                event = _metadata_event_payload(
                    _pr(), {**pair[3], "updated_at": event_time}
                )
                runs = [
                    _run(303, 12, mode="metadata-only", metadata_event_payload=event),
                    _run(101, 10, mode="full"),
                ]
                for mode in ("reconcile", "no-op"):
                    result, client = self.decide(pair, runs, mode=mode)
                    self.assertEqual(result.action, "deferred")
                    self.assertFalse(result.mutated)
                    self.assertFalse(any(call[1].endswith("/rerun") for call in client.calls))

    def test_title_and_combined_edits_require_one_attested_native_transition(self):
        for body in (None, "new body"):
            with self.subTest(body_changed=body is not None):
                pair = self.pair(title="New title", body=body)
                event = _metadata_event_payload(_pr(), pair[3])
                runs = [
                    _run(303, 12, mode="metadata-only", metadata_event_payload=event),
                    _run(101, 10, mode="full"),
                ]
                result, _client = self.decide(pair, runs)
                self.assertEqual(result.action, "complete")
                self.assertEqual(result.run_id, 303)
                if body is not None:
                    receipt, confirmation, posts, state = pair
                    confirmation = replace(
                        confirmation,
                        metadata_version=replace(
                            confirmation.metadata_version,
                            title_event_created_at="2026-09-04T00:00:04Z",
                        ),
                    )
                    pair = (
                        receipt, confirmation,
                        [posts[0], _confirmation_comment(
                            confirmation, created_at="2026-09-04T00:00:06Z"
                        )], state,
                    )
                    result, client = self.decide(pair, runs)
                    self.assertEqual(result.action, "deferred")
                    self.assertFalse(any(call[1].endswith("/rerun") for call in client.calls))

    def test_refreshed_event_proof_cannot_rebind_or_authorize_a_rerun(self):
        pair = self.pair()
        event = _metadata_event_payload(_pr(), pair[3])
        matching = _run(303, 12, mode="metadata-only", success=False,
                        metadata_event_payload=event)
        for replacement in (None, _metadata_event_payload()):
            with self.subTest(unavailable=replacement is None):
                changed = _run(303, 12, mode="metadata-only", success=False,
                               metadata_event_payload=replacement)
                result, client = self.decide(
                    pair, [matching, _run(101, 10, mode="full")],
                    refreshed_runs=[changed, _run(101, 10, mode="full")],
                )
                self.assertEqual(result.action, "deferred")
                self.assertFalse(any(call[1].endswith("/rerun") for call in client.calls))

    def test_multiple_matching_runs_or_missing_competitor_do_not_guess(self):
        pair = self.pair()
        event = _metadata_event_payload(_pr(), pair[3])
        matching = _run(303, 12, mode="metadata-only", metadata_event_payload=event)
        for other in (event, None):
            with self.subTest(matching_competitor=other is not None):
                runs = [
                    matching,
                    _run(202, 11, mode="metadata-only", metadata_event_payload=other),
                    _run(101, 10, mode="full"),
                ]
                if other is not None:
                    with self.assertRaises(pr_metadata.MetadataEditError):
                        self.decide(pair, runs)
                else:
                    result, client = self.decide(pair, runs)
                    self.assertEqual(result.action, "deferred")
                    self.assertFalse(any(call[1].endswith("/rerun") for call in client.calls))

    def test_event_step_attestation_requires_a_unique_successful_bound_step(self):
        for drift in ("digest", "empty-digest", "duplicate", "number", "status", "conclusion", "time"):
            with self.subTest(drift=drift):
                record, jobs = _run(202, 11, mode="metadata-only")
                job = next(job for job in jobs if job["name"] == "metadata-classifier")
                step = job["steps"][0]
                if drift == "digest":
                    step["name"] = metadata_event.STEP_PREFIX + "not-a-digest"
                elif drift == "empty-digest":
                    step["name"] = metadata_event.STEP_PREFIX
                elif drift == "duplicate":
                    job["steps"].append({**step, "number": 2})
                elif drift == "number":
                    step["number"] = True
                elif drift == "status":
                    step["status"] = "queued"
                elif drift == "conclusion":
                    step["conclusion"] = []
                else:
                    step["completed_at"] = "2026-09-04T00:00:03Z"
                client = ScriptedClient()
                _add_snapshot(client, [(record, jobs)])
                state = pr_metadata._parse_pull_request_payload(_pr(), REPOSITORY, PR_NUMBER)
                with self.assertRaises(pr_metadata.MetadataEditError):
                    pr_metadata.list_candidate_runs(client, state)


class PullRequestRejectedMutationTests(unittest.TestCase):
    def edit(self, client, *, title=None, body="new body", essential_reason=None):
        return pr_metadata.edit_metadata(
            client, repository=REPOSITORY, pr_number=PR_NUMBER,
            head_sha=HEAD, base_sha=BASE, title=title, body=body,
            essential_reason=essential_reason,
        )

    def test_definite_rejection_aborts_and_corrected_values_succeed(self):
        cases = [(status, _pr(), None, False) for status in (400, 401, 403, 404, 409, 422, 429)]
        cases.extend([
            (422, _pr(body=None), None, False),
            (422, _pr(), "new title", False),
            (422, _pr(), None, True),
        ])
        for status, state, title, active in cases:
            with self.subTest(status=status, empty=state["body"] is None, title=title, active=active):
                runs = [_run(101, 10, mode="full", active=active)]
                reason = "essential contract correction" if active else None
                client, posts = _mutation_client(
                    failure=_api_failure(status), pre_state=state, title=title, runs=runs
                )
                decision = self.edit(client, title=title, essential_reason=reason)
                self.assertEqual(decision.action, "deferred")
                self.assertTrue(decision.mutated)
                self.assertEqual(decision.abort_comment_id, 402)
                self.assertIsNone(decision.confirmation_comment_id)
                self.assertEqual(len(posts), 2)
                intent = pr_metadata._parse_intent_comment_body(posts[0]["body"])
                abort = pr_metadata._parse_abort_comment_body(posts[1]["body"])
                self.assertEqual(abort.intent_nonce, intent.nonce)
                self.assertEqual(abort.intent_comment_id, 401)
                self.assertEqual(abort.reason, "patch-rejected")
                self.assertEqual(abort.observed_version, _metadata_version())
                self.assertEqual(
                    abort.observed_metadata_sha256,
                    _metadata_sha256(state["title"], state["body"] or ""),
                )
                patch_index = next(
                    index for index, call in enumerate(client.calls) if call[0] == "PATCH"
                )
                refresh = client.calls[patch_index + 1:]
                self.assertEqual(refresh[0][:2], ("GET", _endpoint("actions/workflows/build.yml")))
                self.assertEqual(refresh[-2][:2], ("POST", "graphql"))
                self.assertEqual(
                    sum(call[0] == "GET" and "/comments?" in call[1] for call in refresh), 2
                )
                corrected_title = "corrected title" if title is not None else None
                retry, successor_posts = _mutation_client(
                    history=tuple(posts), pre_state=state,
                    body="corrected body", title=corrected_title, runs=runs,
                )
                corrected = self.edit(
                    retry, body="corrected body", title=corrected_title,
                    essential_reason=reason,
                )
                self.assertEqual(corrected.action, "updated")
                self.assertEqual(corrected.intent_comment_id, 403)
                self.assertEqual(corrected.confirmation_comment_id, 404)
                successor = pr_metadata._parse_intent_comment_body(successor_posts[0]["body"])
                confirmation = pr_metadata._parse_confirmation_comment_body(successor_posts[1]["body"])
                self.assertNotEqual(intent.nonce, successor.nonce)
                self.assertEqual(confirmation.intent_nonce, successor.nonce)
                self.assertGreater(successor_posts[0]["id"], posts[1]["id"])
                self.assertGreaterEqual(successor_posts[0]["created_at"], posts[1]["created_at"])
                expected = {"body": "corrected body"}
                if corrected_title is not None:
                    expected["title"] = corrected_title
                self.assertEqual([call[2] for call in retry.calls if call[0] == "PATCH"], [expected])

    def test_rejection_requires_unchanged_complete_pre_patch_runs(self):
        for drift, (before, after) in _rejection_run_drift_cases().items():
            with self.subTest(drift=drift):
                probe = ScriptedClient()
                _add_snapshot(probe, before)
                _add_snapshot(probe, after)
                state = pr_metadata._parse_pull_request_payload(_pr(), REPOSITORY, PR_NUMBER)
                self.assertNotEqual(
                    pr_metadata.list_candidate_runs(probe, state),
                    pr_metadata.list_candidate_runs(probe, state),
                )
                client, posts = _mutation_client(
                    failure=_api_failure(), runs=before, rejection_runs=after
                )
                with self.assertRaises(pr_metadata.MetadataEditError):
                    self.edit(
                        client,
                        essential_reason=(
                            "essential contract correction"
                            if before[0][0]["status"] != "completed" else None
                        ),
                    )
                self.assertEqual(len(posts), 1)
                self.assertEqual(sum(call[0] == "PATCH" for call in client.calls), 1)
                self.assertEqual(
                    sum(call[:2] == ("GET", _endpoint("actions/workflows/build.yml"))
                        for call in client.calls),
                    4,
                )
                self.assertEqual(
                    sum(call[0] == "GET" and "/comments?" in call[1] for call in client.calls),
                    6,
                )
                self.assertEqual(client.calls[-1][0], "GET")

                retry, retry_posts = _mutation_client(history=tuple(posts))
                held = self.edit(retry)
                self.assertEqual(held.action, "deferred")
                self.assertFalse(held.mutated)
                self.assertEqual(held.intent_comment_id, 401)
                self.assertIsNone(held.abort_comment_id)
                self.assertIsNone(held.confirmation_comment_id)
                self.assertEqual(retry_posts, [])
                self.assertFalse(any(call[0] == "PATCH" for call in retry.calls))

    def test_rejection_requires_selected_latest_unambiguous_active_intent(self):
        comments_route = (
            "GET",
            _query(f"issues/{PR_NUMBER}/comments", [("per_page", "100"), ("page", "1")]),
        )
        for created_at in ("2026-09-04T00:00:01Z", "2026-09-04T00:00:02Z"):
            with self.subTest(successor_created_at=created_at):
                client, posts = _mutation_client(failure=_api_failure())

                def changed(**_kwargs):
                    intent = pr_metadata._parse_intent_comment_body(posts[0]["body"])
                    successor = _intent_comment(
                        replace(intent, nonce="d" * 64 if intent.nonce != "d" * 64 else "e" * 64),
                        comment_id=402, created_at=created_at,
                    )
                    return [posts[0], successor]

                def abort_response(*, body, **_kwargs):
                    comment = _comment(
                        403, body["body"],
                        created_at="2026-09-04T00:00:03Z",
                        updated_at="2026-09-04T00:00:03Z",
                    )
                    posts.append(comment)
                    return comment

                client.routes[comments_route][4:6] = [changed, changed]
                client.routes[("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))][1] = abort_response
                with self.assertRaises(pr_metadata.MetadataEditError):
                    self.edit(client)
                self.assertEqual(len(posts), 1)
                self.assertEqual(sum(call[0] == "PATCH" for call in client.calls), 1)
                self.assertEqual(client.calls[-1][:2], comments_route)

    def test_rejection_selection_ignores_unrelated_and_superseded_comments(self):
        history = (
            _comment(399, "ordinary progress"),
            _intent_comment(
                _receipt(head_sha=NEW_HEAD), comment_id=400,
                created_at="2026-09-04T00:00:01Z",
            ),
        )
        client, posts = _mutation_client(failure=_api_failure(), history=history)
        result = self.edit(client)
        self.assertEqual(result.action, "deferred")
        self.assertEqual((result.intent_comment_id, result.abort_comment_id), (401, 402))
        self.assertEqual(len(posts), 2)
        abort = pr_metadata._parse_abort_comment_body(posts[1]["body"])
        self.assertEqual(abort.reason, "patch-rejected")
        self.assertEqual(abort.intent_comment_id, 401)

    def test_pre_patch_drift_requires_actual_final_observation_for_abort(self):
        for malformed in (False, True):
            with self.subTest(malformed_final_observation=malformed):
                client, posts = _mutation_client()
                for (method, endpoint), responses in client.routes.items():
                    if method == "GET" and "actions/" in endpoint:
                        responses.pop()
                _add_snapshot(client, [
                    _run(202, 11, mode="full", active=True),
                    _run(101, 10, mode="full"),
                ])
                state = _pr(
                    head=NEW_HEAD, body="observed drift",
                    updated_at="2026-09-04T00:00:05Z",
                )
                version = _metadata_version(body_last_edited_at="2026-09-04T00:00:05Z")
                payload = _graphql_payload(state, version)
                if malformed:
                    payload["data"]["repository"]["pullRequest"]["body"] = "not the edit node"
                client.routes[("POST", "graphql")][1] = _response(payload)
                if malformed:
                    with self.assertRaises(pr_metadata.MetadataEditError):
                        self.edit(client)
                    self.assertEqual(len(posts), 1)
                    self.assertEqual(client.calls[-1][:2], ("POST", "graphql"))
                else:
                    result = self.edit(client)
                    self.assertEqual(result.action, "deferred")
                    self.assertEqual(len(posts), 2)
                    abort = pr_metadata._parse_abort_comment_body(posts[1]["body"])
                    self.assertEqual(abort.reason, "run-authority-drift")
                    self.assertEqual(abort.observed_head_sha, NEW_HEAD)
                    self.assertEqual(abort.observed_metadata_sha256,
                                     _metadata_sha256(state["title"], state["body"]))
                    self.assertEqual(abort.observed_version, version)
                    self.assertEqual(client.calls[-2][:2], ("POST", "graphql"))
                self.assertFalse(any(call[0] == "PATCH" for call in client.calls))

    def test_ambiguous_failures_never_abort_or_duplicate_patch_on_retry(self):
        cases = {
            "unsupported-client-rejection": _api_failure(405),
            "request-timeout": _api_failure(408),
            "server-error": _api_failure(500),
            "server-unavailable": _api_failure(503),
            "accepted": _api_failure(202),
            "success-with-error-exit": _api_failure(200),
            "stderr-only": _api_failure(output=b""),
            "network": _api_failure(transport_error=OSError("connection reset")),
            "timeout": _api_failure(transport_error=subprocess.TimeoutExpired(["gh"], 30)),
            "killed": _api_failure(returncode=-9),
            "unexpected-exit": _api_failure(returncode=2),
            "invalid-json": _api_failure(
                output=b"HTTP/2 422 Error\nContent-Type: application/json\n\n{"
            ),
            "bare-cr": _api_failure(
                output=b"HTTP/2 422 Error\nContent-Type: application/json\rX-Test: yes\n\n{}"
            ),
        }
        for name, failure in cases.items():
            with self.subTest(failure=name):
                client, posts = _mutation_client(failure=failure)
                with self.assertRaises(pr_metadata.MetadataEditError):
                    self.edit(client)
                self.assertEqual(len(posts), 1)
                self.assertEqual(sum(call[0] == "PATCH" for call in client.calls), 1)
                self.assertEqual(client.calls[-1][0], "PATCH")
                retry, retried_posts = _mutation_client(history=tuple(posts))
                held = self.edit(retry)
                self.assertEqual(held.action, "deferred")
                self.assertFalse(held.mutated)
                self.assertEqual(held.intent_comment_id, 401)
                self.assertIsNone(held.abort_comment_id)
                self.assertIsNone(held.confirmation_comment_id)
                self.assertEqual(retried_posts, [])
                self.assertFalse(any(call[0] == "PATCH" for call in retry.calls))

                changed, changed_posts = _mutation_client(history=tuple(posts), body="corrected")
                with self.assertRaises(pr_metadata.MetadataEditError):
                    self.edit(changed, body="corrected")
                self.assertEqual(changed_posts, [])
                self.assertFalse(any(call[0] == "PATCH" for call in changed.calls))

                target = _pr(body="new body", updated_at="2026-09-04T00:00:05Z")
                recovered, recovered_posts = _mutation_client(history=tuple(posts), pre_state=target)
                target_version = _metadata_version(body_last_edited_at="2026-09-04T00:00:05Z")
                recovered.routes[("POST", "graphql")] = [
                    _response(_graphql_payload(target, target_version)) for _ in range(2)
                ]
                result = self.edit(recovered)
                self.assertEqual(result.action, "recovered")
                self.assertFalse(any(call[0] == "PATCH" for call in recovered.calls))
                self.assertEqual(len(recovered_posts), 1)
                confirmation = pr_metadata._parse_confirmation_comment_body(recovered_posts[0]["body"])
                self.assertEqual(confirmation.intent_comment_id, 401)
                self.assertEqual(confirmation.metadata_version, target_version)

    def test_rejection_refresh_rebinds_intent_and_requires_complete_authority(self):
        comments_route = (
            "GET",
            _query(f"issues/{PR_NUMBER}/comments", [("per_page", "100"), ("page", "1")]),
        )
        for drift in (
            *INTENT_DRIFTS, "run", "unstable-pages", "candidate", "base",
            "head-ref", "base-ref", "state", "target", "version", "unavailable",
            "owner", "repository", "pr-id", "updated-at", "body-observation",
        ):
            with self.subTest(drift=drift):
                client, posts = _mutation_client(failure=_api_failure())
                if drift in INTENT_DRIFTS:
                    client.routes[comments_route][4:6] = [
                        lambda **_kwargs: _drifted_intent_page(posts[0], drift)
                    ] * 2
                elif drift == "unstable-pages":
                    client.routes[comments_route][5] = []
                elif drift == "run":
                    client.routes[("GET", _endpoint("actions/workflows/build.yml"))][3] = (
                        pr_metadata.MetadataEditError("workflow authority unavailable")
                    )
                elif drift == "unavailable":
                    client.routes[("POST", "graphql")][2] = pr_metadata.MetadataEditError(
                        "fresh observation timed out"
                    )
                else:
                    state = _pr(head=NEW_HEAD) if drift == "candidate" else _pr()
                    version = _metadata_version()
                    if drift == "base":
                        state["base"]["sha"] = NEW_HEAD
                    elif drift in ("head-ref", "base-ref"):
                        state[drift.split("-")[0]]["ref"] = "changed-ref"
                    if drift in ("state", "target", "version"):
                        state = _pr(
                            body={
                                "state": "third state",
                                "target": "new body",
                                "version": "Stable body",
                            }[drift],
                            updated_at="2026-09-04T00:00:05Z",
                        )
                        version = _metadata_version(body_last_edited_at="2026-09-04T00:00:05Z")
                    payload = _graphql_payload(state, version)
                    repository = payload["data"]["repository"]
                    pull = repository["pullRequest"]
                    if drift == "owner":
                        repository["owner"]["databaseId"] += 1
                    elif drift == "repository":
                        repository["databaseId"] += 1
                    elif drift == "pr-id":
                        pull["databaseId"] += 1
                    elif drift == "updated-at":
                        pull.pop("updatedAt")
                    elif drift == "body-observation":
                        pull["body"] = "not the observed body revision"
                    client.routes[("POST", "graphql")][2] = _response(payload)
                with self.assertRaises(pr_metadata.MetadataEditError):
                    self.edit(client)
                self.assertEqual(len(posts), 1)
                self.assertEqual(sum(call[0] == "PATCH" for call in client.calls), 1)
                self.assertNotEqual(client.calls[-1][0], "PATCH")

    def test_rejection_refresh_preserves_existing_terminal_precedence(self):
        comments_route = (
            "GET",
            _query(f"issues/{PR_NUMBER}/comments", [("per_page", "100"), ("page", "1")]),
        )
        for terminal, run_drift in (
            ("confirmation", False), ("confirmation", True),
            ("abort", False), ("abort", True),
        ):
            with self.subTest(terminal=terminal, run_drift=run_drift):
                client, posts = _mutation_client(
                    failure=_api_failure(),
                    rejection_runs=(
                        [_run(202, 11, mode="full", active=True), _run(101, 10, mode="full")]
                        if run_drift else None
                    ),
                )

                def terminated(**_kwargs):
                    intent = pr_metadata._parse_intent_comment_body(posts[0]["body"])
                    record = (
                        _confirmation_comment(_confirmation(intent), created_at="2026-09-04T00:00:02Z")
                        if terminal == "confirmation"
                        else _abort_comment(_abort(intent), comment_id=402)
                    )
                    successor = _intent_comment(
                        replace(intent, nonce="d" * 64 if intent.nonce != "d" * 64 else "e" * 64),
                        comment_id=403, created_at="2026-09-04T00:00:04Z",
                    )
                    return [posts[0], record, successor]

                client.routes[comments_route][4:6] = [terminated, terminated]
                client.routes[("POST", "graphql")][2] = pr_metadata.MetadataEditError(
                    "terminal precedence must not require later metadata"
                )
                result = self.edit(client)
                self.assertEqual(result.action, "deferred")
                self.assertTrue(result.mutated)
                self.assertEqual(
                    (result.confirmation_comment_id, result.abort_comment_id),
                    (402, None) if terminal == "confirmation" else (None, 402),
                )
                self.assertEqual(len(posts), 1)
                self.assertEqual(sum(call[0] == "PATCH" for call in client.calls), 1)

    def test_rejected_patch_does_not_invent_abort_after_abort_delivery_failure(self):
        for maybe_created in (False, True):
            with self.subTest(maybe_created=maybe_created):
                client, posts = _mutation_client(failure=_api_failure())
                route = ("POST", _endpoint(f"issues/{PR_NUMBER}/comments"))
                create = client.routes[route][1]

                def fail_abort(**kwargs):
                    if maybe_created:
                        create(**kwargs)
                    raise pr_metadata.MetadataEditError("abort response timed out")

                client.routes[route][1] = fail_abort
                with self.assertRaises(pr_metadata.MetadataEditError):
                    self.edit(client)
                self.assertEqual(len(posts), 2 if maybe_created else 1)
                retry, retry_posts = _mutation_client(history=tuple(posts))
                result = self.edit(retry)
                self.assertEqual(result.action, "updated" if maybe_created else "deferred")
                self.assertEqual(sum(call[0] == "PATCH" for call in retry.calls), int(maybe_created))
                self.assertEqual(len(retry_posts), 2 if maybe_created else 0)

    def test_rejection_type_remains_bound_to_the_exact_patch_request(self):
        for method, endpoint in (
            ("GET", _endpoint(f"pulls/{PR_NUMBER}")),
            ("POST", "graphql"),
            ("PATCH", _endpoint("issues/comments/401")),
        ):
            with self.subTest(method=method, endpoint=endpoint):
                def wrong_request(**_kwargs):
                    return _api_failure()(method=method, endpoint=endpoint, body=None)

                client, posts = _mutation_client(failure=wrong_request)
                with self.assertRaises(pr_metadata.MetadataEditError):
                    self.edit(client)
                self.assertEqual(client.calls[-1][0], "PATCH")
                self.assertEqual(len(posts), 1)

    def test_http_error_retains_validated_status_without_trusting_diagnostic(self):
        for status in (422, 500):
            for returncode in (0, 1):
                with self.subTest(status=status, returncode=returncode):
                    with self.assertRaises(pr_metadata.MetadataEditError) as raised:
                        _api_failure(status, returncode=returncode)(
                            method="PATCH", endpoint=_endpoint(f"pulls/{PR_NUMBER}"),
                            body={"body": "new body"},
                        )
                    self.assertEqual(raised.exception.response.status, status)
                    self.assertEqual(raised.exception.response.payload, {"message": "Validation Failed"})
                    self.assertEqual(raised.exception.method, "PATCH")
                    self.assertEqual(raised.exception.endpoint, _endpoint(f"pulls/{PR_NUMBER}"))


@unittest.skipUnless(
    os.environ.get("PR_METADATA_LIVE_REPOSITORY")
    and os.environ.get("PR_METADATA_LIVE_PR"),
    "set PR_METADATA_LIVE_REPOSITORY and PR_METADATA_LIVE_PR",
)
class LivePullRequestMetadataQueryTests(unittest.TestCase):
    def test_actual_metadata_version_query_parses_existing_pull_request(self):
        repository = os.environ["PR_METADATA_LIVE_REPOSITORY"]
        pr_number = int(os.environ["PR_METADATA_LIVE_PR"])
        client = pr_metadata.GitHubClient(pr_metadata._resolve_gh())
        version = pr_metadata.inspect_metadata_history(
            client,
            repository,
            pr_number,
        )
        self.assertIsInstance(version, pr_metadata.MetadataVersion)
        self.assertGreaterEqual(version.body_edit_total_count, 0)
        if version.body_edit_total_count == 0:
            self.assertIsNone(version.body_edit_id)
            self.assertIsNone(version.body_last_edited_at)
        else:
            self.assertTrue(version.body_edit_id)
            self.assertEqual(
                version.body_edit_edited_at,
                version.body_last_edited_at,
            )
        if version.title_actor_id is not None:
            self.assertGreater(version.title_actor_id, 0)
            self.assertTrue(version.title_actor_login)
        if version.body_last_edited_at is not None:
            self.assertGreater(version.body_editor_id, 0)
            self.assertTrue(version.body_editor_login)


class PullRequestMetadataLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="workflow-pr-metadata-launcher-",
            dir=artifact_root,
        )
        self.addCleanup(self.temporary.cleanup)
        self.sandbox = LauncherSandbox(Path(self.temporary.name))

    def common_arguments(self) -> list[str]:
        return [
            "--repository",
            REPOSITORY,
            "--pr",
            str(PR_NUMBER),
            "--head-sha",
            HEAD,
            "--base-sha",
            BASE,
        ]

    def assert_isolated_calls(
        self,
        records: list[dict],
        expected_count: int,
    ) -> None:
        self.assertEqual(len(records), expected_count)
        self.assertFalse(self.sandbox.site_marker.exists())
        for record in records:
            self.assertEqual(record["gh_host"], "github.com")
            self.assertIsNone(record["gh_repo"])
            self.assertEqual(record["git_environment"], [])
            self.assertEqual(record["argv"][0], "api")
            self.assertIn("--include", record["argv"])

    def recorded_comments(self, calls, records):
        return tuple(
            {**calls[index]["payload"], "body": json.loads(record["input"])["body"]}
            for index, record in enumerate(records)
            if record["method"] == "POST" and "/comments" in record["endpoint"]
        )

    def test_first_and_subsequent_body_edit_cli_confirm_and_recover(self):
        body_path = self.sandbox.root / "body.txt"
        body_path.write_text("new body", encoding="utf-8")
        arguments = [*self.common_arguments(), "--body-file", str(body_path)]
        for initial_body, first in ((None, True), ("Original body 雪\n", True), ("Stable body", False)):
            with self.subTest(body=initial_body, first=first):
                state = _pr(body=initial_body)
                pre = _metadata_version(
                    body_last_edited_at=None if first else "2026-09-04T00:00:00Z"
                )
                calls = _cli_rejection_calls("new body", pre_state=state, pre_version=pre)
                completed, records = self.sandbox.run("edit", arguments, calls)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["action"], "updated")
                self.assert_isolated_calls(records, len(calls))
                history = self.recorded_comments(calls, records)
                self.assertEqual(len(history), 2)
                confirmation = pr_metadata._parse_confirmation_comment_body(history[1]["body"])
                self.assertEqual(confirmation.metadata_version.body_edit_total_count, 2 if first else 3)
                self.assertEqual(
                    [json.loads(r["input"]) for r in records if r["method"] == "PATCH"],
                    [{"body": "new body"}],
                )
                calls = _cli_rejection_calls(
                    "new body", pre_state=state, pre_version=pre,
                    history=history[:1], recovery=True,
                )
                completed, records = self.sandbox.run("edit", arguments, calls)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["action"], "recovered")
                self.assert_isolated_calls(records, len(calls))
                self.assertFalse(any(r["method"] == "PATCH" for r in records))
                recovered = self.recorded_comments(calls, records)
                self.assertEqual(len(recovered), 1)
                self.assertEqual(
                    pr_metadata._parse_confirmation_comment_body(recovered[0]["body"]),
                    confirmation,
                )
                target = _pr(body="new body", updated_at="2026-09-04T00:00:05Z")
                calls = [
                    _cli_api_call("GET", _endpoint(f"pulls/{PR_NUMBER}"), payload=target),
                    *_cli_snapshot_calls([
                        _run(202, 11, mode="metadata-only", success=True,
                             metadata_event_payload=_metadata_event_payload(state, target)),
                        _run(101, 10, mode="full"),
                    ]),
                    *_cli_stable_comment_walk(_cli_api_call(
                        "GET",
                        _query(
                            f"issues/{PR_NUMBER}/comments",
                            [("per_page", "100"), ("page", "1")],
                        ),
                        payload=list(history),
                    )),
                    _cli_metadata_version_call(
                        target, confirmation.metadata_version,
                        original_body=(initial_body or "") if first else "prior body",
                    ),
                ]
                completed, records = self.sandbox.run(
                    "reconcile",
                    [*self.common_arguments(), "--confirmation-comment-id", "402"],
                    calls,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, _canonical_decision(
                    action="complete", run_id=202,
                    reason="metadata continuity already succeeds",
                ))
                self.assert_isolated_calls(records, len(calls))
                self.assertFalse(any(
                    r["method"] != "GET" and r["endpoint"] != "graphql" for r in records
                ))

    def test_first_body_edit_cli_rejects_forged_original_and_multiple_edits(self):
        body_path = self.sandbox.root / "body.txt"
        body_path.write_text("new body", encoding="utf-8")
        arguments = [*self.common_arguments(), "--body-file", str(body_path)]
        calls = _cli_rejection_calls(
            "new body", pre_state=_pr(body=None),
            pre_version=_metadata_version(body_last_edited_at=None),
        )
        final_query = max(i for i, call in enumerate(calls) if call["endpoint"] == "graphql")
        controls = [
            (name, calls, payload)
            for name, payload in _first_body_history_controls(calls[final_query]["payload"]).items()
        ]
        subsequent = _cli_rejection_calls("new body")
        double_edit = copy.deepcopy(subsequent[final_query]["payload"])
        double_edit["data"]["repository"]["pullRequest"]["userContentEdits"]["totalCount"] += 1
        controls.append(("subsequent-double-edit", subsequent, double_edit))
        for name, transcript, payload in controls:
            with self.subTest(history=name):
                changed = copy.deepcopy(transcript)
                changed[final_query]["payload"] = payload
                completed, records = self.sandbox.run("edit", arguments, changed)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertEqual(completed.stdout, "")
                self.assertNotIn("Traceback", completed.stderr)
                self.assert_isolated_calls(records, final_query + 1)
                self.assertEqual(sum(r["method"] == "PATCH" for r in records), 1)
                self.assertEqual(len(self.recorded_comments(changed, records)), 1)

    def test_rejected_patch_cli_aborts_and_accepts_corrected_successor(self):
        body_path = self.sandbox.root / "body.txt"
        body_path.write_text("new body", encoding="utf-8")
        arguments = [*self.common_arguments(), "--body-file", str(body_path)]
        calls = _cli_rejection_calls("new body", failure={
            "status": 422, "payload": {"message": "Validation Failed"},
            "returncode": 1, "stderr": "gh: Validation Failed (HTTP 422)\n",
            "definite": True,
        })
        completed, records = self.sandbox.run("edit", arguments, calls)
        self.assertEqual(completed.returncode, 3, completed.stderr)
        self.assertEqual(completed.stderr, "")
        decision = json.loads(completed.stdout)
        self.assertEqual(completed.stdout, json.dumps(decision, sort_keys=True, separators=(",", ":")) + "\n")
        self.assertEqual(decision["action"], "deferred")
        self.assertEqual(decision["abort_comment_id"], 402)
        self.assertIsNone(decision["confirmation_comment_id"])
        self.assert_isolated_calls(records, len(calls))
        history = self.recorded_comments(calls, records)
        self.assertEqual(len(history), 2)
        intent = pr_metadata._parse_intent_comment_body(history[0]["body"])
        abort = pr_metadata._parse_abort_comment_body(history[1]["body"])
        self.assertEqual(abort.reason, "patch-rejected")
        self.assertEqual(abort.intent_nonce, intent.nonce)
        self.assertEqual(abort.observed_version, _metadata_version())
        self.assertEqual(records[-2]["endpoint"], "graphql")
        self.assertEqual(sum(record["method"] == "PATCH" for record in records), 1)

        body_path.write_text("corrected body", encoding="utf-8")
        calls = _cli_rejection_calls("corrected body", history=history)
        completed, records = self.sandbox.run("edit", arguments, calls)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["action"], "updated")
        self.assert_isolated_calls(records, len(calls))
        successor_comments = self.recorded_comments(calls, records)
        successor = pr_metadata._parse_intent_comment_body(successor_comments[0]["body"])
        confirmation = pr_metadata._parse_confirmation_comment_body(successor_comments[1]["body"])
        self.assertNotEqual(intent.nonce, successor.nonce)
        self.assertEqual(confirmation.intent_nonce, successor.nonce)
        self.assertGreater(confirmation.intent_comment_id, history[1]["id"])
        self.assertEqual(
            [json.loads(record["input"]) for record in records if record["method"] == "PATCH"],
            [{"body": "corrected body"}],
        )

    def test_ambiguous_patch_cli_holds_pre_state_and_recovers_applied_target(self):
        body_path = self.sandbox.root / "body.txt"
        body_path.write_text("new body", encoding="utf-8")
        arguments = [*self.common_arguments(), "--body-file", str(body_path)]
        cases = (
            {"status": 500, "payload": {"message": "Internal Server Error"}, "returncode": 1},
            {"status": 408, "payload": {"message": "Request Timeout"}, "returncode": 1},
            {"raw_response_hex": "", "returncode": 1, "stderr": "connection reset; HTTP 422\n"},
            {
                "raw_response_hex": (
                    b"HTTP/2 422 Error\nContent-Type: application/json\rX-Bad: yes\n\n{}"
                ).hex(),
                "returncode": 1,
            },
            {"status": 422, "payload": {"message": "Validation Failed"}, "returncode": 2},
            {"status": 200, "returncode": 1, "stderr": "response delivery failed\n"},
        )
        for failure in cases:
            with self.subTest(failure=failure):
                calls = _cli_rejection_calls("new body", failure=failure)
                completed, records = self.sandbox.run("edit", arguments, calls)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertEqual(completed.stdout, "")
                self.assertNotIn("Traceback", completed.stderr)
                self.assert_isolated_calls(records, len(calls))
                history = self.recorded_comments(calls, records)
                self.assertEqual(len(history), 1)
                self.assertEqual(records[-1]["method"], "PATCH")

                calls = _cli_rejection_calls("new body", history=history, held=True)
                completed, records = self.sandbox.run("edit", arguments, calls)
                self.assertEqual(completed.returncode, 3, completed.stderr)
                decision = json.loads(completed.stdout)
                self.assertEqual(decision["action"], "deferred")
                self.assertFalse(decision["mutated"])
                self.assertEqual(decision["intent_comment_id"], 401)
                self.assertIsNone(decision["abort_comment_id"])
                self.assertIsNone(decision["confirmation_comment_id"])
                self.assert_isolated_calls(records, len(calls))
                self.assertFalse(any(record["method"] == "PATCH" for record in records))
                self.assertEqual(self.recorded_comments(calls, records), ())

                calls = _cli_rejection_calls("new body", history=history, recovery=True)
                completed, records = self.sandbox.run("edit", arguments, calls)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["action"], "recovered")
                self.assert_isolated_calls(records, len(calls))
                self.assertFalse(any(record["method"] == "PATCH" for record in records))
                confirmation_comments = self.recorded_comments(calls, records)
                self.assertEqual(len(confirmation_comments), 1)
                confirmation = pr_metadata._parse_confirmation_comment_body(confirmation_comments[0]["body"])
                self.assertEqual(confirmation.intent_comment_id, 401)

    def test_rejected_patch_cli_requires_fresh_intent_and_metadata_authority(self):
        body_path = self.sandbox.root / "body.txt"
        body_path.write_text("new body", encoding="utf-8")
        arguments = [*self.common_arguments(), "--body-file", str(body_path)]
        for drift in ("missing-intent", "deleted-author", "invalid-metadata"):
            with self.subTest(drift=drift):
                calls = _cli_rejection_calls("new body", failure={
                    "status": 422, "payload": {"message": "Validation Failed"},
                    "returncode": 1, "definite": True,
                })
                if drift == "invalid-metadata":
                    calls[-2]["payload"]["data"]["repository"]["owner"]["databaseId"] += 1
                    expected_count = len(calls) - 1
                else:
                    walk = [
                        index for index, call in enumerate(calls)
                        if call["method"] == "GET" and "/comments?" in call["endpoint"]
                    ][-2:]
                    for index in walk:
                        if drift == "missing-intent":
                            calls[index]["payload"] = []
                            calls[index].pop("echo_last_comment")
                        else:
                            calls[index]["payload"][0]["user"] = None
                    expected_count = walk[-1] + 1
                completed, records = self.sandbox.run("edit", arguments, calls)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertEqual(completed.stdout, "")
                self.assertNotIn("Traceback", completed.stderr)
                self.assert_isolated_calls(records, expected_count)
                self.assertEqual(sum(record["method"] == "PATCH" for record in records), 1)
                self.assertEqual(len(self.recorded_comments(calls, records)), 1)

    def test_rejected_patch_cli_holds_changed_complete_run_authority(self):
        body_path = self.sandbox.root / "body.txt"
        body_path.write_text("new body", encoding="utf-8")
        for drift, (before, after) in _rejection_run_drift_cases().items():
            with self.subTest(drift=drift):
                arguments = [*self.common_arguments(), "--body-file", str(body_path)]
                if before[0][0]["status"] != "completed":
                    arguments.extend(["--essential-reason", "essential contract correction"])
                calls = _cli_rejection_calls(
                    "new body", runs=before, rejection_runs=after,
                    failure={
                        "status": 422, "payload": {"message": "Validation Failed"},
                        "returncode": 1, "definite": True,
                    },
                )
                completed, records = self.sandbox.run("edit", arguments, calls)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertEqual(completed.stdout, "")
                self.assertNotIn("Traceback", completed.stderr)
                self.assert_isolated_calls(records, len(calls) - 2)
                self.assertEqual(sum(record["method"] == "PATCH" for record in records), 1)
                self.assertEqual(len(self.recorded_comments(calls, records)), 1)
                self.assertEqual(records[-1]["method"], "GET")

    def test_event_attested_cli_rejects_delayed_runs_and_reruns_only_matching_event(self):
        body_path = self.sandbox.root / "body.txt"
        body_path.write_text("new body", encoding="utf-8")
        calls = _cli_rejection_calls("new body")
        completed, records = self.sandbox.run(
            "edit", [*self.common_arguments(), "--body-file", str(body_path)], calls
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        history = self.recorded_comments(calls, records)
        confirmation = pr_metadata._parse_confirmation_comment_body(history[1]["body"])
        state = _pr(body="new body", updated_at="2026-09-04T00:00:05Z")
        event = _metadata_event_payload(_pr(), state)
        event_path = self.sandbox.root / "event.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        output = self.sandbox.root / "event-output"
        before_log = self.sandbox.log.read_bytes()
        producer = subprocess.run(
            ["/usr/bin/python3", "-I", str(LAUNCHER), "attest-metadata-event",
             "--event-path", str(event_path), "--repository", REPOSITORY,
             "--run-id", "303", "--run-number", "12", "--run-attempt", "1",
             "--output", str(output)],
            cwd=ROOT, env=self.sandbox.environment, check=False,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(producer.returncode, 0, producer.stderr)
        self.assertEqual(self.sandbox.log.read_bytes(), before_log)
        digest = output.read_text().strip().removeprefix("digest=")
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        comments = _cli_stable_comment_walk(_cli_api_call(
            "GET", _query(f"issues/{PR_NUMBER}/comments", [("per_page", "100"), ("page", "1")]),
            payload=list(history),
        ))
        full = _run(101, 10, mode="full")
        earlier = _run(202, 11, mode="metadata-only")
        arguments = [*self.common_arguments(), "--confirmation-comment-id", "402"]
        for case in ("unattested", "skipped-proof", "earlier", "matching", "matching-rerun", "proof-drift"):
            with self.subTest(case=case):
                matching = _run(
                    303, 12, mode="metadata-only", success=case == "matching",
                    metadata_event_payload=event,
                )
                classifier = next(job for job in matching[1] if job["name"] == "metadata-classifier")
                self.assertEqual(classifier["steps"][0]["name"], metadata_event.STEP_PREFIX + digest)
                runs = (
                    [_run(202, 11, mode="metadata-only", metadata_event_payload=None), full]
                    if case in ("unattested", "skipped-proof") else [earlier, full]
                    if case == "earlier" else [matching, earlier, full]
                )
                if case == "skipped-proof":
                    classifier = next(job for job in runs[0][1]
                                      if job["name"] == "metadata-classifier")
                    classifier["steps"] = [{
                        "name": metadata_event.STEP_PREFIX, "number": 1,
                        "status": "completed", "conclusion": "skipped",
                    }]
                calls = [
                    _cli_api_call("GET", _endpoint(f"pulls/{PR_NUMBER}"), payload=state),
                    *_cli_snapshot_calls(runs), *copy.deepcopy(comments),
                    _cli_metadata_version_call(state, confirmation.metadata_version),
                ]
                if case in ("matching-rerun", "proof-drift"):
                    refreshed = (
                        [_run(303, 12, mode="metadata-only", success=False), earlier, full]
                        if case == "proof-drift" else runs
                    )
                    calls.extend([
                        _cli_api_call("GET", _endpoint(f"pulls/{PR_NUMBER}"), payload=state),
                        *copy.deepcopy(comments),
                        _cli_metadata_version_call(state, confirmation.metadata_version),
                        *_cli_snapshot_calls(refreshed),
                    ])
                    if case == "matching-rerun":
                        calls.append(_cli_api_call(
                            "POST", _endpoint("actions/runs/303/rerun"), status=201
                        ))
                completed, records = self.sandbox.run("reconcile", arguments, calls)
                expected = (
                    "complete" if case == "matching" else "rerun"
                    if case == "matching-rerun" else "deferred"
                )
                self.assertEqual(completed.returncode, 0 if expected != "deferred" else 3,
                                 completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["action"], expected)
                self.assert_isolated_calls(records, len(calls))
                reruns = [record["endpoint"] for record in records if record["endpoint"].endswith("/rerun")]
                self.assertEqual(reruns, [_endpoint("actions/runs/303/rerun")]
                                 if case == "matching-rerun" else [])

    def test_unhashable_run_conclusion_is_a_fail_closed_cli_error(self):
        body_path = self.sandbox.root / "body.md"
        body_path.write_text("New body\n", encoding="utf-8")
        record, jobs = _run(101, 10, mode="full")
        record["conclusion"] = []
        calls = [
            _cli_api_call("GET", _endpoint(f"pulls/{PR_NUMBER}"), payload=_pr()),
            *_cli_snapshot_calls([(record, jobs)]),
        ]
        completed, records = self.sandbox.run(
            "edit", [*self.common_arguments(), "--body-file", str(body_path)], calls
        )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertNotIn("Traceback", completed.stderr)
        self.assertIn("conclusion", completed.stderr)
        self.assertTrue(all(record["method"] == "GET" for record in records))

    def test_gh_subprocess_preserves_http_framing_bytes(self):
        body_path = self.sandbox.root / "body.md"
        body_path.write_text("Deferred body\n", encoding="utf-8")
        payload = json.dumps(_pr(), ensure_ascii=False).encode("utf-8")
        cases = (
            ("lf", b"HTTP/2 200 OK\nContent-Type: application/json\n\n", 3),
            ("crlf", b"HTTP/2 200 OK\r\nContent-Type: application/json\r\n\r\n", 3),
            (
                "gh-rendered-status",
                b"HTTP/2.0 200 OK\nContent-Type: application/json\r\n\r\n",
                3,
            ),
            (
                "bare-cr",
                b"HTTP/2 200 OK\nContent-Type: application/json\rX-Extra: yes\n\n",
                2,
            ),
            (
                "mixed-lines",
                b"HTTP/2 200 OK\r\nContent-Type: application/json\n\n",
                2,
            ),
            (
                "invalid-utf8",
                b"HTTP/2 200 OK\nContent-Type: application/json\nX-Extra: \xff\n\n",
                2,
            ),
            (
                "gh-status-mixed-fields",
                b"HTTP/2.0 200 OK\nContent-Type: application/json\r\n"
                b"X-First: yes\nX-Second: yes\r\n\r\n",
                2,
            ),
        )
        for name, headers, status in cases:
            with self.subTest(framing=name):
                if name == "gh-rendered-status":
                    with self.assertRaises(pr_metadata.MetadataEditError):
                        pr_metadata._parse_http_response(
                            (headers + payload).decode("utf-8"),
                            label="plain HTTP",
                            allow_empty_body=False,
                        )
                initial = _cli_api_call(
                    "GET", _endpoint(f"pulls/{PR_NUMBER}"), payload=_pr()
                )
                initial["raw_response_hex"] = (headers + payload + b"\n").hex()
                calls = [
                    initial,
                    *_cli_snapshot_calls([_run(101, 10, mode="full", active=True)]),
                ]
                completed, records = self.sandbox.run(
                    "edit",
                    [*self.common_arguments(), "--body-file", str(body_path)],
                    calls,
                )
                self.assertEqual(completed.returncode, status, completed.stderr)
                if status == 2:
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(len(records), 1)
                else:
                    self.assertEqual(json.loads(completed.stdout)["action"], "deferred")
                    self.assert_isolated_calls(records, len(calls))

    def test_edit_launcher_fast_defer_and_mutation_paths(self):
        body_path = self.sandbox.root / "body.md"
        body_path.write_text("Deferred body\n", encoding="utf-8")
        active_record, active_jobs = _run(
            202,
            11,
            mode="full",
            active=True,
        )
        active_jobs = [
            job
            for job in active_jobs
            if job["name"] == "event-identity"
        ]
        active_record["pull_requests"] = []
        prior_full = _run(101, 10, mode="full")
        calls = [
            _cli_api_call(
                "GET",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(),
            ),
            *_cli_snapshot_calls(
                [(active_record, active_jobs), prior_full]
            ),
        ]
        completed, records = self.sandbox.run(
            "edit",
            [
                *self.common_arguments(),
                "--body-file",
                str(body_path),
            ],
            calls,
        )
        self.assertEqual(completed.returncode, 3, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            completed.stdout,
            _canonical_decision(
                action="deferred",
                guidance=[
                    [
                        "/usr/bin/python3",
                        "-I",
                        "scripts/workflow_pilot/isolated_launcher.py",
                        "pr-metadata",
                        "evidence-comment",
                        "--repository",
                        REPOSITORY,
                        "--pr",
                        str(PR_NUMBER),
                        "--head-sha",
                        HEAD,
                        "--base-sha",
                        BASE,
                        "--comment-file",
                        "<canonical-evidence-file>",
                    ]
                ],
                reason=(
                    "an exact-head full or unproven Build is active; update "
                    "the canonical evidence comment instead"
                ),
                run_id=202,
            ),
        )
        self.assert_isolated_calls(records, len(calls))
        self.assertFalse(any(record["method"] != "GET" for record in records))

        marker = self.sandbox.root / "injection-executed"
        body = f'$(touch "{marker}")\n--method DELETE\n'
        title_path = self.sandbox.root / "title.txt"
        body_path.write_text(body, encoding="utf-8")
        title_path.write_text("CLI title\n", encoding="utf-8")
        successful_full = _run(101, 10, mode="full")
        post_state = _pr(
            title="CLI title",
            body=body,
            updated_at="2026-09-04T00:00:05Z",
        )
        post_version = _metadata_version(
            title_event_id="RTE_2",
            title_event_created_at="2026-09-04T00:00:05Z",
            title_previous="Stable title",
            title_current="CLI title",
            body_last_edited_at="2026-09-04T00:00:05Z",
        )
        calls = [
            _cli_api_call(
                "GET",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(),
            ),
            *_cli_snapshot_calls([successful_full]),
            _cli_api_call(
                "GET",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(),
            ),
            *_cli_snapshot_calls([successful_full]),
            _cli_metadata_version_call(
                _pr(),
                _metadata_version(),
            ),
            *_cli_stable_comment_walk(_cli_api_call(
                "GET",
                _query(
                    f"issues/{PR_NUMBER}/comments",
                    [("per_page", "100"), ("page", "1")],
                ),
                payload=[],
            )),
            _cli_api_call(
                "POST",
                _endpoint(f"issues/{PR_NUMBER}/comments"),
                payload=_comment(
                    401,
                    "",
                    created_at="2026-09-04T00:00:01Z",
                    updated_at="2026-09-04T00:00:01Z",
                ),
                status=201,
                input_text=None,
                echo_body=True,
            ),
            *_cli_snapshot_calls([successful_full]),
            *_cli_stable_comment_walk(_cli_api_call(
                "GET",
                _query(
                    f"issues/{PR_NUMBER}/comments",
                    [("per_page", "100"), ("page", "1")],
                ),
                payload=[
                    _comment(
                        401,
                        "",
                        created_at="2026-09-04T00:00:01Z",
                        updated_at="2026-09-04T00:00:01Z",
                    )
                ],
                echo_last_comment=True,
            )),
            _cli_metadata_version_call(_pr(), _metadata_version()),
            _cli_api_call(
                "PATCH",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=post_state,
                input_text=json.dumps(
                    {"body": body, "title": "CLI title"},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
            *_cli_stable_comment_walk(_cli_api_call(
                "GET",
                _query(
                    f"issues/{PR_NUMBER}/comments",
                    [("per_page", "100"), ("page", "1")],
                ),
                payload=[
                    _comment(
                        401,
                        "",
                        created_at="2026-09-04T00:00:01Z",
                        updated_at="2026-09-04T00:00:01Z",
                    )
                ],
                echo_last_comment=True,
            )),
            _cli_metadata_version_call(post_state, post_version),
            _cli_api_call(
                "POST",
                _endpoint(f"issues/{PR_NUMBER}/comments"),
                payload=_comment(
                    402,
                    "",
                    created_at="2026-09-04T00:00:06Z",
                    updated_at="2026-09-04T00:00:06Z",
                ),
                status=201,
                input_text=None,
                echo_body=True,
            ),
        ]
        completed, records = self.sandbox.run(
            "edit",
            [
                *self.common_arguments(),
                "--title-file",
                str(title_path),
                "--body-file",
                str(body_path),
            ],
            calls,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            completed.stdout,
            _canonical_decision(
                guidance=[
                    [
                        "/usr/bin/python3",
                        "-I",
                        "scripts/workflow_pilot/isolated_launcher.py",
                        "pr-metadata",
                        "reconcile",
                        "--repository",
                        REPOSITORY,
                        "--pr",
                        str(PR_NUMBER),
                        "--head-sha",
                        HEAD,
                        "--base-sha",
                        BASE,
                        "--confirmation-comment-id",
                        "402",
                    ]
                ],
                mutated=True,
                reason=(
                    "metadata updated; reconcile the exact metadata-only run "
                    "to close any non-atomic same-SHA Build race"
                ),
                run_id=101,
                intent_comment_id=401,
                intent_comment_url=(
                    f"https://github.com/{REPOSITORY}/pull/{PR_NUMBER}"
                    "#issuecomment-401"
                ),
                confirmation_comment_id=402,
                confirmation_comment_url=(
                    f"https://github.com/{REPOSITORY}/pull/{PR_NUMBER}"
                    "#issuecomment-402"
                ),
            ),
        )
        self.assert_isolated_calls(records, len(calls))
        self.assertFalse(marker.exists())
        patch_record = next(
            record
            for record in records
            if record["method"] == "PATCH"
        )
        patch_index = records.index(patch_record)
        self.assertEqual(records[patch_index - 1]["endpoint"], "graphql")
        self.assertEqual(records[patch_index - 1]["method"], "POST")
        self.assertEqual(
            json.loads(patch_record["input"]),
            {"body": body, "title": "CLI title"},
        )
        self.assertEqual(patch_record["argv"][-2:], ["--input", "-"])
        intent_record = next(
            record
            for record in records
            if pr_metadata.INTENT_MARKER in record["input"]
        )
        intent = pr_metadata._parse_intent_comment_body(
            json.loads(intent_record["input"])["body"]
        )
        self.assertEqual(
            intent.target_metadata_sha256,
            _metadata_sha256("CLI title", body),
        )
        self.assertEqual(
            intent.provided_fields,
            (
                pr_metadata.EditFieldDigest("body", _sha256(body)),
                pr_metadata.EditFieldDigest("title", _sha256("CLI title")),
            ),
        )
        self.assertRegex(intent.nonce, r"^[0-9a-f]{64}$")
        confirmation_record = next(
            record
            for record in records
            if pr_metadata.CONFIRMATION_MARKER in record["input"]
        )
        confirmation = pr_metadata._parse_confirmation_comment_body(
            json.loads(confirmation_record["input"])["body"]
        )
        self.assertEqual(confirmation.intent_comment_id, 401)
        self.assertEqual(confirmation.intent_nonce, intent.nonce)

    def test_reconcile_and_evidence_comment_launcher_paths(self):
        old_receipt = _receipt(
            watermark_run_id=202,
            watermark_run_number=11,
        )
        old_confirmation = _confirmation(old_receipt)
        old_success = _run(202, 11, mode="metadata-only", success=True)
        successful_full = _run(101, 10, mode="full")
        calls = [
            _cli_api_call(
                "GET",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(),
            ),
            *_cli_snapshot_calls([old_success, successful_full]),
            *_cli_stable_comment_walk(_cli_api_call(
                "GET",
                _query(
                    f"issues/{PR_NUMBER}/comments",
                    [("per_page", "100"), ("page", "1")],
                ),
                payload=[
                    _intent_comment(old_receipt),
                    _confirmation_comment(old_confirmation),
                ],
            )),
            _cli_metadata_version_call(
                _pr(),
                old_confirmation.metadata_version,
            ),
        ]
        completed, records = self.sandbox.run(
            "reconcile",
            [
                *self.common_arguments(),
                "--confirmation-comment-id",
                "402",
            ],
            calls,
        )
        self.assertEqual(completed.returncode, 3, completed.stderr)
        self.assertEqual(
            completed.stdout,
            _canonical_decision(
                action="deferred",
                guidance=[
                    [
                        "/usr/bin/python3",
                        "-I",
                        "scripts/workflow_pilot/isolated_launcher.py",
                        "pr-metadata",
                        "reconcile",
                        "--repository",
                        REPOSITORY,
                        "--pr",
                        str(PR_NUMBER),
                        "--head-sha",
                        HEAD,
                        "--base-sha",
                        BASE,
                        "--confirmation-comment-id",
                        "402",
                    ]
                ],
                reason="no uniquely event-attested metadata-only run is available",
                run_id=101,
            ),
        )
        self.assert_isolated_calls(records, len(calls))

        failed_metadata = _run(
            202,
            11,
            mode="metadata-only",
            success=False,
        )
        runs = [failed_metadata, successful_full]
        receipt = _receipt()
        confirmation = _confirmation(receipt)
        calls = [
            _cli_api_call(
                "GET",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(),
            ),
            *_cli_snapshot_calls(runs),
            *_cli_stable_comment_walk(_cli_api_call(
                "GET",
                _query(
                    f"issues/{PR_NUMBER}/comments",
                    [("per_page", "100"), ("page", "1")],
                ),
                payload=[
                    _intent_comment(receipt),
                    _confirmation_comment(confirmation),
                ],
            )),
            _cli_metadata_version_call(
                _pr(),
                confirmation.metadata_version,
            ),
            _cli_api_call(
                "GET",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(),
            ),
            *_cli_stable_comment_walk(_cli_api_call(
                "GET",
                _query(
                    f"issues/{PR_NUMBER}/comments",
                    [("per_page", "100"), ("page", "1")],
                ),
                payload=[
                    _intent_comment(receipt),
                    _confirmation_comment(confirmation),
                ],
            )),
            _cli_metadata_version_call(
                _pr(),
                confirmation.metadata_version,
            ),
            *_cli_snapshot_calls(runs),
            _cli_api_call(
                "POST",
                _endpoint("actions/runs/202/rerun"),
                status=201,
            ),
        ]
        completed, records = self.sandbox.run(
            "reconcile",
            [
                *self.common_arguments(),
                "--confirmation-comment-id",
                "402",
            ],
            calls,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            completed.stdout,
            _canonical_decision(
                action="rerun",
                mutated=True,
                reason="reran only the exact metadata-only continuity run",
                run_id=202,
            ),
        )
        self.assert_isolated_calls(records, len(calls))
        self.assertEqual(records[-1]["method"], "POST")
        self.assertEqual(records[-1]["input"], "")

        comment_body = (
            f"{pr_metadata.EVIDENCE_MARKER}\n"
            f"Candidate: `{HEAD}`\n"
        )
        comment_path = self.sandbox.root / "evidence.md"
        comment_path.write_text(comment_body, encoding="utf-8")
        calls = [
            _cli_api_call(
                "GET",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(),
            ),
            *_cli_stable_comment_walk(_cli_api_call(
                "GET",
                _query(
                    f"issues/{PR_NUMBER}/comments",
                    [("per_page", "100"), ("page", "1")],
                ),
                payload=[
                    _comment(
                        301,
                        f"{pr_metadata.EVIDENCE_MARKER}\nOld evidence\n",
                    )
                ],
            )),
            _cli_api_call(
                "GET",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(),
            ),
            _cli_api_call(
                "PATCH",
                _endpoint("issues/comments/301"),
                payload=_comment(301, comment_body),
                input_text=json.dumps(
                    {"body": comment_body},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        ]
        completed, records = self.sandbox.run(
            "evidence-comment",
            [
                *self.common_arguments(),
                "--comment-file",
                str(comment_path),
            ],
            calls,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            completed.stdout,
            _canonical_decision(
                action="comment-updated",
                comment_id=301,
                mutated=True,
                reason=(
                    "canonical evidence comment updated without editing pull "
                    "request metadata"
                ),
            ),
        )
        self.assert_isolated_calls(records, len(calls))
        output = json.loads(completed.stdout)
        self.assertIsNone(output["run_id"])
        self.assertEqual(output["comment_id"], 301)

    def test_launcher_rejects_mode_file_and_identity_inputs(self):
        cases = (
            (
                "unknown-submode",
                "unknown",
                self.common_arguments(),
                "invalid choice",
            ),
            (
                "missing-edit-file",
                "edit",
                self.common_arguments(),
                "edit requires --title-file, --body-file, or both",
            ),
            (
                "missing-comment-file",
                "evidence-comment",
                self.common_arguments(),
                "required",
            ),
            (
                "missing-confirmation-comment-id",
                "reconcile",
                self.common_arguments(),
                "required",
            ),
            (
                "caller-receipt-file-unsupported",
                "reconcile",
                [
                    *self.common_arguments(),
                    "--confirmation-comment-id",
                    "402",
                    "--receipt-file",
                    str(self.sandbox.root / "forged.json"),
                ],
                "unrecognized arguments",
            ),
            (
                "invalid-confirmation-comment-id",
                "reconcile",
                [
                    *self.common_arguments(),
                    "--confirmation-comment-id",
                    "0",
                ],
                "--confirmation-comment-id must be a positive integer",
            ),
            (
                "missing-body-path",
                "edit",
                [
                    *self.common_arguments(),
                    "--body-file",
                    str(self.sandbox.root / "missing.md"),
                ],
                "cannot read body file",
            ),
            (
                "repository-injection",
                "edit",
                [
                    "--repository",
                    "owner/repo;touch-injection",
                    "--pr",
                    str(PR_NUMBER),
                    "--head-sha",
                    HEAD,
                    "--base-sha",
                    BASE,
                    "--body-file",
                    str(self.sandbox.root / "missing.md"),
                ],
                "--repository must be an owner/name slug",
            ),
            (
                "malformed-head",
                "edit",
                [
                    "--repository",
                    REPOSITORY,
                    "--pr",
                    str(PR_NUMBER),
                    "--head-sha",
                    "$(touch injected)",
                    "--base-sha",
                    BASE,
                    "--body-file",
                    str(self.sandbox.root / "missing.md"),
                ],
                "--head-sha must be a full lowercase SHA",
            ),
        )
        for name, mode, arguments, error in cases:
            with self.subTest(case=name):
                completed, records = self.sandbox.run(
                    mode,
                    arguments,
                    [],
                )
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertIn(error, completed.stderr)
                self.assertEqual(records, [])

        completed = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                str(LAUNCHER),
                "unknown-mode",
            ],
            cwd=ROOT,
            env=self.sandbox.environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("mode must be one of", completed.stderr)
        self.assertFalse(self.sandbox.site_marker.exists())


if __name__ == "__main__":
    unittest.main()
