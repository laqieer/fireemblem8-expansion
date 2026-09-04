#!/usr/bin/env python3
"""Guard pull-request metadata edits against exact-candidate Build races."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from . import candidate_evidence


MAX_API_BYTES = 4 * 1024 * 1024
MAX_BODY_BYTES = 1024 * 1024
MAX_COMMENT_PAGES = 10
MAX_REASON_BYTES = 4096
MAX_RUN_PAGES = 10
MAX_RUNS = 1000
PAGE_SIZE = 100
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW_PATH = ".github/workflows/build.yml"
EVIDENCE_MARKER = "<!-- workflow-pilot-candidate-evidence -->"

FULL_JOB_NAMES = frozenset(candidate_evidence.KNOWN_JOB_IDS)
METADATA_JOB_NAMES = (
    FULL_JOB_NAMES - {candidate_evidence.FULL_CLASSIFIER}
) | {candidate_evidence.METADATA_CLASSIFIER}
FULL_SUCCESS_JOB_NAMES = FULL_JOB_NAMES - {"patch-release"}
ACTIVE_RUN_STATUSES = frozenset(
    {"pending", "queued", "requested", "in_progress", "waiting"}
)
RUN_CONCLUSIONS = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
        "success",
        "timed_out",
    }
)


class MetadataEditError(ValueError):
    """GitHub authority or an orchestration decision failed closed."""


@dataclass(frozen=True)
class PullRequestState:
    repository: str
    number: int
    head_sha: str
    base_sha: str
    title: str
    body: str | None


@dataclass(frozen=True)
class JobState:
    name: str
    status: str
    conclusion: str | None
    runner_name: str | None
    started_at: str | None


@dataclass(frozen=True)
class RunState:
    run_id: int
    workflow_id: int
    run_number: int
    run_attempt: int
    status: str
    conclusion: str | None
    mode: str
    jobs: tuple[JobState, ...]


@dataclass(frozen=True)
class Decision:
    action: str
    base_sha: str
    guidance: tuple[tuple[str, ...], ...]
    head_sha: str
    mutated: bool
    reason: str
    repository: str
    pr_number: int
    run_id: int | None = None

    def canonical_json(self) -> str:
        payload = asdict(self)
        payload["guidance"] = [list(command) for command in self.guidance]
        return json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ) + "\n"


def _positive_int(value: object, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > 999999999999999999
    ):
        raise MetadataEditError(f"{field} must be a positive integer")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise MetadataEditError(f"{field} must be a full lowercase SHA")
    return value


def _text(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise MetadataEditError(f"{field} must be nonempty text")
    return value


def _repository(value: str) -> str:
    if (
        REPOSITORY_RE.fullmatch(value) is None
        or value.startswith((".", "-"))
        or "/." in value
        or "/-" in value
    ):
        raise MetadataEditError("--repository must be an owner/name slug")
    return value


def _parse_json(raw: str, label: str) -> object:
    if len(raw.encode("utf-8")) > MAX_API_BYTES:
        raise MetadataEditError(f"{label} response exceeds 4 MiB")

    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise MetadataEditError(f"{label} response repeats key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise MetadataEditError(f"{label} response contains {value}")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise MetadataEditError(f"{label} response is invalid JSON: {error}") from error

    def require_finite(item: object) -> None:
        if isinstance(item, float):
            if not math.isfinite(item):
                raise MetadataEditError(f"{label} response contains a non-finite number")
            return
        if isinstance(item, dict):
            for child in item.values():
                require_finite(child)
            return
        if isinstance(item, list):
            for child in item:
                require_finite(child)

    require_finite(value)
    return value


Runner = Callable[..., subprocess.CompletedProcess[str]]


class GitHubClient:
    def __init__(
        self,
        gh_path: str,
        *,
        runner: Runner = subprocess.run,
    ) -> None:
        self.gh_path = str(Path(gh_path).resolve(strict=True))
        self.runner = runner

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        body: dict[str, object] | None = None,
        label: str,
    ) -> object:
        if method not in {"GET", "PATCH", "POST"}:
            raise MetadataEditError("unsupported GitHub API method")
        arguments = [
            self.gh_path,
            "api",
            "--hostname",
            "github.com",
            "--method",
            method,
            endpoint,
        ]
        input_text = None
        if body is not None:
            arguments.extend(["--input", "-"])
            input_text = json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        environment = dict(os.environ)
        environment["GH_HOST"] = "github.com"
        environment.pop("GH_REPO", None)
        try:
            completed = self.runner(
                arguments,
                check=False,
                capture_output=True,
                text=True,
                input=input_text,
                env=environment,
                timeout=30,
            )
        except subprocess.TimeoutExpired as error:
            raise MetadataEditError(f"{label} request timed out") from error
        except OSError as error:
            raise MetadataEditError(f"{label} request could not start: {error}") from error
        if completed.returncode != 0:
            detail = completed.stderr.strip()
            if len(detail) > 512:
                detail = detail[:512] + "..."
            raise MetadataEditError(
                f"{label} request failed"
                + (f": {detail}" if detail else "")
            )
        if not completed.stdout:
            if method == "POST":
                return None
            raise MetadataEditError(f"{label} response is empty")
        return _parse_json(completed.stdout, label)


def _endpoint(repository: str, suffix: str) -> str:
    owner, name = repository.split("/", 1)
    return (
        f"repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(name, safe='')}/{suffix}"
    )


def _query_endpoint(repository: str, suffix: str, pairs: list[tuple[str, str]]) -> str:
    return _endpoint(repository, suffix) + "?" + urllib.parse.urlencode(pairs)


def fetch_pull_request(
    client: GitHubClient,
    repository: str,
    pr_number: int,
) -> PullRequestState:
    payload = client.request(
        "GET",
        _endpoint(repository, f"pulls/{pr_number}"),
        label="pull request",
    )
    if not isinstance(payload, dict):
        raise MetadataEditError("pull request response must be an object")
    if _positive_int(payload.get("number"), "pull request number") != pr_number:
        raise MetadataEditError("pull request number drifted")
    if payload.get("state") != "open":
        raise MetadataEditError("pull request must be open")
    head = payload.get("head")
    base = payload.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        raise MetadataEditError("pull request head/base identity is missing")
    base_repo = base.get("repo")
    if (
        not isinstance(base_repo, dict)
        or base_repo.get("full_name") != repository
    ):
        raise MetadataEditError("pull request repository identity drifted")
    title = _text(payload.get("title"), "pull request title")
    body = payload.get("body")
    if body is not None and not isinstance(body, str):
        raise MetadataEditError("pull request body must be text or null")
    return PullRequestState(
        repository=repository,
        number=pr_number,
        head_sha=_sha(head.get("sha"), "pull request head"),
        base_sha=_sha(base.get("sha"), "pull request base"),
        title=title,
        body=body,
    )


def require_identity(
    state: PullRequestState,
    *,
    head_sha: str,
    base_sha: str,
) -> None:
    if state.head_sha != head_sha or state.base_sha != base_sha:
        raise MetadataEditError(
            "pull request identity changed; rerun with the current exact head/base"
        )


def _page_count(total_count: int) -> int:
    return max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)


def _expected_page_items(total_count: int, page: int) -> int:
    pages = _page_count(total_count)
    if page > pages:
        raise MetadataEditError("pagination exceeded total_count")
    if page < pages:
        return PAGE_SIZE
    return total_count - PAGE_SIZE * (pages - 1)


def _list_counted_pages(
    client: GitHubClient,
    *,
    endpoint_for_page: Callable[[int], str],
    item_key: str,
    label: str,
    maximum: int,
) -> list[object]:
    items: list[object] = []
    expected_total = None
    pages = None
    for page in range(1, MAX_RUN_PAGES + 1):
        payload = client.request(
            "GET",
            endpoint_for_page(page),
            label=f"{label} page {page}",
        )
        if (
            not isinstance(payload, dict)
            or set(payload) != {"total_count", item_key}
        ):
            raise MetadataEditError(f"{label} response shape is invalid")
        total_count = payload["total_count"]
        if (
            isinstance(total_count, bool)
            or not isinstance(total_count, int)
            or total_count < 0
            or total_count > maximum
        ):
            raise MetadataEditError(f"{label} total_count is invalid")
        page_items = payload[item_key]
        if not isinstance(page_items, list):
            raise MetadataEditError(f"{label} items must be a list")
        if expected_total is None:
            expected_total = total_count
            pages = _page_count(total_count)
        elif total_count != expected_total:
            raise MetadataEditError(f"{label} total_count changed across pages")
        if len(page_items) != _expected_page_items(total_count, page):
            raise MetadataEditError(f"{label} page cardinality is incomplete")
        items.extend(page_items)
        if page == pages:
            break
    else:
        raise MetadataEditError(f"{label} exceeds the pagination bound")
    if expected_total is None or len(items) != expected_total:
        raise MetadataEditError(f"{label} pagination is incomplete")
    return items


def _workflow_id(client: GitHubClient, repository: str) -> int:
    payload = client.request(
        "GET",
        _endpoint(repository, "actions/workflows/build.yml"),
        label="Build workflow",
    )
    if not isinstance(payload, dict):
        raise MetadataEditError("Build workflow response must be an object")
    if payload.get("path") != WORKFLOW_PATH:
        raise MetadataEditError("Build workflow path drifted")
    if payload.get("state") != "active":
        raise MetadataEditError("Build workflow is not active")
    return _positive_int(payload.get("id"), "Build workflow id")


def _parse_job(raw: object, *, run_id: int) -> JobState:
    if not isinstance(raw, dict):
        raise MetadataEditError(f"Build run {run_id} job must be an object")
    name = _text(raw.get("name"), f"Build run {run_id} job name")
    status = _text(raw.get("status"), f"Build run {run_id} job status")
    if status not in ACTIVE_RUN_STATUSES | {"completed"}:
        raise MetadataEditError(f"Build run {run_id} job status is unknown")
    conclusion = raw.get("conclusion")
    if conclusion is not None and conclusion not in RUN_CONCLUSIONS:
        raise MetadataEditError(f"Build run {run_id} job conclusion is unknown")
    if status == "completed" and conclusion is None:
        raise MetadataEditError(f"Build run {run_id} completed job lacks a conclusion")
    if status != "completed" and conclusion is not None:
        raise MetadataEditError(f"Build run {run_id} active job has a conclusion")
    runner_name = raw.get("runner_name")
    if runner_name is not None and not isinstance(runner_name, str):
        raise MetadataEditError(f"Build run {run_id} job runner is invalid")
    started_at = raw.get("started_at")
    if started_at is not None and (
        not isinstance(started_at, str) or not started_at
    ):
        raise MetadataEditError(f"Build run {run_id} job start time is invalid")
    return JobState(name, status, conclusion, runner_name, started_at)


def _list_jobs(
    client: GitHubClient,
    repository: str,
    run_id: int,
    run_attempt: int,
) -> tuple[JobState, ...]:
    raw_jobs = _list_counted_pages(
        client,
        endpoint_for_page=lambda page: _query_endpoint(
            repository,
            f"actions/runs/{run_id}/attempts/{run_attempt}/jobs",
            [("per_page", str(PAGE_SIZE)), ("page", str(page))],
        ),
        item_key="jobs",
        label=f"Build run {run_id} jobs",
        maximum=MAX_RUNS,
    )
    jobs = tuple(_parse_job(raw, run_id=run_id) for raw in raw_jobs)
    names = [job.name for job in jobs]
    if len(names) != len(set(names)):
        raise MetadataEditError(f"Build run {run_id} repeats a job name")
    return jobs


def _run_mode(jobs: tuple[JobState, ...], *, run_id: int) -> str:
    names = frozenset(job.name for job in jobs)
    if names == FULL_JOB_NAMES:
        return "full"
    if names == METADATA_JOB_NAMES:
        return "metadata-only"
    raise MetadataEditError(f"Build run {run_id} has an unknown or mixed job shape")


def _parse_run(
    client: GitHubClient,
    repository: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    workflow_id: int,
    raw: object,
) -> tuple[int, int, RunState | None]:
    if not isinstance(raw, dict):
        raise MetadataEditError("Build workflow run must be an object")
    run_id = _positive_int(raw.get("id"), "Build run id")
    if _positive_int(raw.get("workflow_id"), "Build run workflow_id") != workflow_id:
        raise MetadataEditError(f"Build run {run_id} workflow identity drifted")
    run_number = _positive_int(raw.get("run_number"), "Build run number")
    run_attempt = _positive_int(raw.get("run_attempt"), "Build run attempt")
    if raw.get("event") != "pull_request":
        raise MetadataEditError(f"Build run {run_id} event is not pull_request")
    if _sha(raw.get("head_sha"), f"Build run {run_id} head") != head_sha:
        raise MetadataEditError(f"Build run {run_id} head identity drifted")
    path = _text(raw.get("path"), f"Build run {run_id} path")
    if path != WORKFLOW_PATH and not path.startswith(WORKFLOW_PATH + "@"):
        raise MetadataEditError(f"Build run {run_id} workflow path drifted")
    url = _text(raw.get("url"), f"Build run {run_id} URL")
    split = urllib.parse.urlsplit(url)
    owner, name = repository.split("/", 1)
    expected_path = (
        f"/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(name, safe='')}/actions/runs/{run_id}"
    )
    if (
        split.scheme != "https"
        or split.netloc != "api.github.com"
        or split.username is not None
        or split.password is not None
        or split.path != expected_path
        or split.query
        or split.fragment
    ):
        raise MetadataEditError(f"Build run {run_id} URL identity drifted")
    bindings = raw.get("pull_requests")
    if not isinstance(bindings, list):
        raise MetadataEditError(f"Build run {run_id} PR bindings are invalid")
    matches = 0
    for binding in bindings:
        if not isinstance(binding, dict):
            raise MetadataEditError(f"Build run {run_id} PR binding is invalid")
        head = binding.get("head")
        base = binding.get("base")
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise MetadataEditError(f"Build run {run_id} PR binding is incomplete")
        number = _positive_int(binding.get("number"), "Build run PR number")
        binding_head = _sha(head.get("sha"), "Build run PR head")
        binding_base = _sha(base.get("sha"), "Build run PR base")
        if (
            number == pr_number
            and binding_head == head_sha
            and binding_base == base_sha
        ):
            matches += 1
    if matches > 1:
        raise MetadataEditError(f"Build run {run_id} exact PR binding is ambiguous")
    status = _text(raw.get("status"), f"Build run {run_id} status")
    if status not in ACTIVE_RUN_STATUSES | {"completed"}:
        raise MetadataEditError(f"Build run {run_id} status is unknown")
    conclusion = raw.get("conclusion")
    if conclusion is not None and conclusion not in RUN_CONCLUSIONS:
        raise MetadataEditError(f"Build run {run_id} conclusion is unknown")
    if status == "completed" and conclusion is None:
        raise MetadataEditError(f"Build run {run_id} completed without conclusion")
    if status != "completed" and conclusion is not None:
        raise MetadataEditError(f"Build run {run_id} active with conclusion")
    if matches == 0:
        return run_id, run_number, None
    jobs = _list_jobs(client, repository, run_id, run_attempt)
    return (
        run_id,
        run_number,
        RunState(
            run_id=run_id,
            workflow_id=workflow_id,
            run_number=run_number,
            run_attempt=run_attempt,
            status=status,
            conclusion=conclusion,
            mode=_run_mode(jobs, run_id=run_id),
            jobs=jobs,
        ),
    )


def list_candidate_runs(
    client: GitHubClient,
    state: PullRequestState,
) -> tuple[RunState, ...]:
    workflow_id = _workflow_id(client, state.repository)
    raw_runs = _list_counted_pages(
        client,
        endpoint_for_page=lambda page: _query_endpoint(
            state.repository,
            "actions/workflows/build.yml/runs",
            [
                ("event", "pull_request"),
                ("head_sha", state.head_sha),
                ("per_page", str(PAGE_SIZE)),
                ("page", str(page)),
            ],
        ),
        item_key="workflow_runs",
        label="Build workflow runs",
        maximum=MAX_RUNS,
    )
    visible = tuple(
        _parse_run(
            client,
            state.repository,
            state.number,
            state.head_sha,
            state.base_sha,
            workflow_id,
            raw,
        )
        for raw in raw_runs
    )
    seen_ids = set()
    seen_numbers = set()
    previous_number = None
    exact_runs = []
    for run_id, run_number, run in visible:
        if run_id in seen_ids or run_number in seen_numbers:
            raise MetadataEditError("Build workflow runs repeat an identity")
        if previous_number is not None and run_number >= previous_number:
            raise MetadataEditError("Build workflow runs are not newest-first")
        seen_ids.add(run_id)
        seen_numbers.add(run_number)
        previous_number = run_number
        if run is not None:
            exact_runs.append(run)
    return tuple(exact_runs)


def _jobs_by_name(run: RunState) -> dict[str, JobState]:
    return {job.name: job for job in run.jobs}


def require_full_success(run: RunState) -> None:
    if (
        run.mode != "full"
        or run.status != "completed"
        or run.conclusion != "success"
    ):
        raise MetadataEditError("newest exact full Build is not successful")
    jobs = _jobs_by_name(run)
    for name in FULL_SUCCESS_JOB_NAMES:
        job = jobs[name]
        if (
            job.status != "completed"
            or job.conclusion != "success"
            or not job.runner_name
            or not job.started_at
        ):
            raise MetadataEditError(
                f"newest exact full Build job {name} is not runner-backed success"
            )
    patch = jobs["patch-release"]
    if (
        patch.status != "completed"
        or patch.conclusion != "skipped"
        or patch.runner_name
    ):
        raise MetadataEditError("newest exact full Build patch-release shape is invalid")


def require_metadata_success(run: RunState) -> None:
    if (
        run.mode != "metadata-only"
        or run.status != "completed"
        or run.conclusion != "success"
    ):
        raise MetadataEditError("metadata continuity run is not successful")
    jobs = _jobs_by_name(run)
    for name in (
        "event-identity",
        "event-router",
        "metadata-classifier",
        "host-tests",
        "build",
        "summary",
    ):
        job = jobs[name]
        if (
            job.status != "completed"
            or job.conclusion != "success"
            or not job.runner_name
            or not job.started_at
        ):
            raise MetadataEditError(
                f"metadata continuity job {name} is not runner-backed success"
            )
    for name in ("extended-host-tests", "legacy", "patch-release"):
        job = jobs[name]
        if (
            job.status != "completed"
            or job.conclusion != "skipped"
            or job.runner_name
        ):
            raise MetadataEditError(
                f"metadata continuity job {name} is not canonical skipped"
            )


def _latest_full(runs: tuple[RunState, ...]) -> RunState | None:
    return next((run for run in runs if run.mode == "full"), None)


def _active_full_runs(runs: tuple[RunState, ...]) -> tuple[RunState, ...]:
    return tuple(
        run for run in runs if run.mode == "full" and run.status in ACTIVE_RUN_STATUSES
    )


def _helper_command(
    mode: str,
    state: PullRequestState,
    *arguments: str,
) -> tuple[str, ...]:
    return (
        "/usr/bin/python3",
        "-I",
        "scripts/workflow_pilot/isolated_launcher.py",
        "pr-metadata",
        mode,
        "--repository",
        state.repository,
        "--pr",
        str(state.number),
        "--head-sha",
        state.head_sha,
        "--base-sha",
        state.base_sha,
        *arguments,
    )


def _comment_guidance(state: PullRequestState) -> tuple[tuple[str, ...], ...]:
    return (
        _helper_command(
            "evidence-comment",
            state,
            "--comment-file",
            "<canonical-evidence-file>",
        ),
    )


def edit_metadata(
    client: GitHubClient,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    title: str | None,
    body: str | None,
    essential_reason: str | None,
) -> Decision:
    initial = fetch_pull_request(client, repository, pr_number)
    require_identity(initial, head_sha=head_sha, base_sha=base_sha)
    if title is None and body is None:
        raise MetadataEditError("edit requires --title-file, --body-file, or both")
    if title is not None and not title.strip():
        raise MetadataEditError("pull request title must not be empty")
    if (title is None or title == initial.title) and (
        body is None or body == initial.body
    ):
        return Decision(
            "no-op",
            base_sha,
            (),
            head_sha,
            False,
            "requested metadata already matches",
            repository,
            pr_number,
        )

    runs = list_candidate_runs(client, initial)
    active_full = _active_full_runs(runs)
    latest_full = _latest_full(runs)
    if essential_reason is None:
        if active_full:
            return Decision(
                "deferred",
                base_sha,
                _comment_guidance(initial),
                head_sha,
                False,
                "an exact-head full Build is active; update the canonical evidence comment instead",
                repository,
                pr_number,
                active_full[0].run_id,
            )
        if latest_full is None:
            return Decision(
                "refused",
                base_sha,
                _comment_guidance(initial),
                head_sha,
                False,
                "no exact-head full Build can authorize metadata continuity",
                repository,
                pr_number,
            )
        require_full_success(latest_full)
    elif not essential_reason.strip():
        raise MetadataEditError("--essential-reason must contain non-whitespace text")
    elif len(essential_reason.encode("utf-8")) > MAX_REASON_BYTES:
        raise MetadataEditError("--essential-reason exceeds 4096 bytes")
    elif not active_full:
        if latest_full is None:
            raise MetadataEditError(
                "essential edit has no exact-head full Build to reconcile"
            )
        require_full_success(latest_full)

    current = fetch_pull_request(client, repository, pr_number)
    require_identity(current, head_sha=head_sha, base_sha=base_sha)
    mutation: dict[str, object] = {}
    if title is not None:
        mutation["title"] = title
    if body is not None:
        mutation["body"] = body
    client.request(
        "PATCH",
        _endpoint(repository, f"pulls/{pr_number}"),
        body=mutation,
        label="pull request metadata update",
    )
    after = fetch_pull_request(client, repository, pr_number)
    if after.head_sha != head_sha or after.base_sha != base_sha:
        raise MetadataEditError(
            "metadata updated, but pull request identity changed concurrently; "
            "do not reconcile the stale candidate and rerun against the current head/base"
        )
    guidance = (_helper_command("reconcile", after),)
    reason = (
        "metadata updated; reconcile the exact metadata-only run to close any "
        "non-atomic same-SHA Build race"
    )
    if active_full:
        reason = (
            "essential metadata updated; reconcile its metadata-only run after "
            "the newest exact-head full Build succeeds"
        )
    return Decision(
        "updated",
        base_sha,
        guidance,
        head_sha,
        True,
        reason,
        repository,
        pr_number,
        active_full[0].run_id if active_full else latest_full.run_id,
    )


def _pending_metadata(
    runs: tuple[RunState, ...],
) -> tuple[RunState, RunState | None]:
    latest_full = _latest_full(runs)
    if latest_full is None:
        raise MetadataEditError("no exact-head full Build exists")
    require_full_success(latest_full)
    metadata = next(
        (
            run
            for run in runs
            if run.mode == "metadata-only"
            and run.run_number > latest_full.run_number
        ),
        None,
    )
    return latest_full, metadata


def reconcile_metadata(
    client: GitHubClient,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
) -> Decision:
    initial = fetch_pull_request(client, repository, pr_number)
    require_identity(initial, head_sha=head_sha, base_sha=base_sha)
    first_runs = list_candidate_runs(client, initial)
    latest_full = _latest_full(first_runs)
    if latest_full is not None and latest_full.status in ACTIVE_RUN_STATUSES:
        return Decision(
            "deferred",
            base_sha,
            (_helper_command("reconcile", initial),),
            head_sha,
            False,
            "the newest exact full Build is still active",
            repository,
            pr_number,
            latest_full.run_id,
        )
    first_full, first_metadata = _pending_metadata(first_runs)
    if first_metadata is None:
        return Decision(
            "deferred",
            base_sha,
            (_helper_command("reconcile", initial),),
            head_sha,
            False,
            "the exact metadata-only run is not visible yet",
            repository,
            pr_number,
            first_full.run_id,
        )
    if first_metadata.status in ACTIVE_RUN_STATUSES:
        return Decision(
            "deferred",
            base_sha,
            (_helper_command("reconcile", initial),),
            head_sha,
            False,
            "the exact metadata-only run is already active",
            repository,
            pr_number,
            first_metadata.run_id,
        )
    if first_metadata.conclusion == "success":
        require_metadata_success(first_metadata)
        return Decision(
            "complete",
            base_sha,
            (),
            head_sha,
            False,
            "metadata continuity already succeeds",
            repository,
            pr_number,
            first_metadata.run_id,
        )

    current = fetch_pull_request(client, repository, pr_number)
    require_identity(current, head_sha=head_sha, base_sha=base_sha)
    current_runs = list_candidate_runs(client, current)
    current_full, current_metadata = _pending_metadata(current_runs)
    if (
        current_metadata is None
        or current_full.run_id != first_full.run_id
        or current_full.run_attempt != first_full.run_attempt
        or current_metadata.run_id != first_metadata.run_id
        or current_metadata.run_attempt != first_metadata.run_attempt
        or current_metadata.status != first_metadata.status
        or current_metadata.conclusion != first_metadata.conclusion
    ):
        return Decision(
            "deferred",
            base_sha,
            (_helper_command("reconcile", current),),
            head_sha,
            False,
            "exact Build run state changed during reconciliation; retry from fresh authority",
            repository,
            pr_number,
            current_metadata.run_id if current_metadata else current_full.run_id,
        )
    client.request(
        "POST",
        _endpoint(repository, f"actions/runs/{current_metadata.run_id}/rerun"),
        label="metadata continuity rerun",
    )
    return Decision(
        "rerun",
        base_sha,
        (),
        head_sha,
        True,
        "reran only the exact metadata-only continuity run",
        repository,
        pr_number,
        current_metadata.run_id,
    )


def _list_comments(
    client: GitHubClient,
    repository: str,
    pr_number: int,
) -> list[dict[str, object]]:
    comments: list[dict[str, object]] = []
    seen_ids = set()
    for page in range(1, MAX_COMMENT_PAGES + 1):
        payload = client.request(
            "GET",
            _query_endpoint(
                repository,
                f"issues/{pr_number}/comments",
                [("per_page", str(PAGE_SIZE)), ("page", str(page))],
            ),
            label=f"pull request comments page {page}",
        )
        if not isinstance(payload, list) or len(payload) > PAGE_SIZE:
            raise MetadataEditError("pull request comments pagination is invalid")
        for raw in payload:
            if not isinstance(raw, dict):
                raise MetadataEditError("pull request comment is invalid")
            comment_id = _positive_int(raw.get("id"), "pull request comment id")
            if comment_id in seen_ids:
                raise MetadataEditError("pull request comments repeat an identity")
            seen_ids.add(comment_id)
            comment_body = raw.get("body")
            if not isinstance(comment_body, str):
                raise MetadataEditError(
                    f"pull request comment {comment_id} body is invalid"
                )
            comments.append({"id": comment_id, "body": comment_body})
        if len(payload) < PAGE_SIZE:
            return comments
    raise MetadataEditError("pull request comments exceed the pagination bound")


def _marker_is_standalone(body: str) -> bool:
    return sum(line.strip() == EVIDENCE_MARKER for line in body.splitlines()) == 1


def update_evidence_comment(
    client: GitHubClient,
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    comment_body: str,
) -> Decision:
    if comment_body.count(EVIDENCE_MARKER) != 1 or not _marker_is_standalone(
        comment_body
    ):
        raise MetadataEditError(
            "canonical evidence comment must contain one standalone marker"
        )
    initial = fetch_pull_request(client, repository, pr_number)
    require_identity(initial, head_sha=head_sha, base_sha=base_sha)
    comments = _list_comments(client, repository, pr_number)
    marked = [
        comment
        for comment in comments
        if EVIDENCE_MARKER in str(comment["body"])
    ]
    if len(marked) != 1 or not _marker_is_standalone(str(marked[0]["body"])):
        raise MetadataEditError(
            "pull request must have exactly one canonical marked evidence comment"
        )
    current = fetch_pull_request(client, repository, pr_number)
    require_identity(current, head_sha=head_sha, base_sha=base_sha)
    comment_id = int(marked[0]["id"])
    client.request(
        "PATCH",
        _endpoint(repository, f"issues/comments/{comment_id}"),
        body={"body": comment_body},
        label="canonical evidence comment update",
    )
    after = fetch_pull_request(client, repository, pr_number)
    if after.head_sha != head_sha or after.base_sha != base_sha:
        raise MetadataEditError(
            "evidence comment updated, but pull request identity changed concurrently; "
            "rerun the canonical comment update for the current head/base"
        )
    return Decision(
        "comment-updated",
        base_sha,
        (),
        head_sha,
        True,
        "canonical evidence comment updated without editing pull request metadata",
        repository,
        pr_number,
        comment_id,
    )


def _read_text(path: Path, *, label: str, maximum: int) -> str:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise MetadataEditError(f"cannot read {label}: {error}") from error
    if len(data) > maximum:
        raise MetadataEditError(f"{label} exceeds {maximum} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MetadataEditError(f"{label} must be UTF-8") from error


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guard exact-candidate pull-request metadata edits."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in ("edit", "reconcile", "evidence-comment"):
        child = subparsers.add_parser(mode)
        child.add_argument("--repository", required=True)
        child.add_argument("--pr", type=int, required=True)
        child.add_argument("--head-sha", required=True)
        child.add_argument("--base-sha", required=True)
        if mode == "edit":
            child.add_argument("--title-file", type=Path)
            child.add_argument("--body-file", type=Path)
            child.add_argument("--essential-reason")
        elif mode == "evidence-comment":
            child.add_argument("--comment-file", type=Path, required=True)
    return parser.parse_args(argv)


def _resolve_gh() -> str:
    path = shutil.which("gh")
    if path is None:
        raise MetadataEditError("gh is required")
    return path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        repository = _repository(args.repository)
        pr_number = _positive_int(args.pr, "--pr")
        head_sha = _sha(args.head_sha, "--head-sha")
        base_sha = _sha(args.base_sha, "--base-sha")
        client = GitHubClient(_resolve_gh())
        if args.mode == "edit":
            title = (
                _read_text(
                    args.title_file,
                    label="title file",
                    maximum=MAX_BODY_BYTES,
                ).rstrip("\n")
                if args.title_file
                else None
            )
            body = (
                _read_text(
                    args.body_file,
                    label="body file",
                    maximum=MAX_BODY_BYTES,
                )
                if args.body_file
                else None
            )
            decision = edit_metadata(
                client,
                repository=repository,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
                title=title,
                body=body,
                essential_reason=args.essential_reason,
            )
        elif args.mode == "reconcile":
            decision = reconcile_metadata(
                client,
                repository=repository,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
            )
        else:
            decision = update_evidence_comment(
                client,
                repository=repository,
                pr_number=pr_number,
                head_sha=head_sha,
                base_sha=base_sha,
                comment_body=_read_text(
                    args.comment_file,
                    label="comment file",
                    maximum=MAX_BODY_BYTES,
                ),
            )
    except MetadataEditError as error:
        print(f"pr-metadata: {error}", file=sys.stderr)
        return 2
    print(decision.canonical_json(), end="")
    return 0 if decision.action not in {"deferred", "refused"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
