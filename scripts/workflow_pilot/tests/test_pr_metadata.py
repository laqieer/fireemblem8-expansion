"""Behavioral regressions for exact-candidate pull-request metadata edits."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.workflow_pilot import candidate_evidence, pr_metadata


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
) -> dict:
    return {
        "number": PR_NUMBER,
        "state": "open",
        "title": title,
        "body": body,
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
        "created_at": "2026-09-04T00:00:00Z",
        "updated_at": "2026-09-04T00:00:01Z",
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
        if isinstance(response, pr_metadata.ApiResponse):
            return copy.deepcopy(response)
        return _response(
            copy.deepcopy(response),
            status=201 if method == "POST" else 200,
        )


def _add_pr_states(client: ScriptedClient, *states: dict) -> None:
    client.add("GET", _endpoint(f"pulls/{PR_NUMBER}"), *states)


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
            ("event", "pull_request"),
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
    input_text: str = "",
) -> dict:
    call = {
        "endpoint": endpoint,
        "input": input_text,
        "method": method,
        "status": status,
    }
    if payload is not _MISSING:
        call["payload"] = payload
    return call


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
                    ("event", "pull_request"),
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


def _canonical_decision(**changes: object) -> str:
    payload = {
        "action": "updated",
        "base_sha": BASE,
        "comment_id": None,
        "guidance": [],
        "head_sha": HEAD,
        "mutated": False,
        "pr_number": PR_NUMBER,
        "reason": "",
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
    endpoint = next(item for item in arguments if item.startswith("repos/"))
except (StopIteration, ValueError, IndexError):
    print("fake-gh: malformed argv", file=sys.stderr)
    raise SystemExit(97)
input_text = sys.stdin.read() if "--input" in arguments else ""
if (
    method != expected["method"]
    or endpoint != expected["endpoint"]
    or input_text != expected["input"]
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
status = expected["status"]
reason = "Created" if status == 201 else "OK"
sys.stdout.write(f"HTTP/2 {status} {reason}\n")
if "payload" in expected:
    sys.stdout.write("Content-Type: application/json\n\n")
    sys.stdout.write(
        json.dumps(
            expected["payload"],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
else:
    sys.stdout.write("\n")
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
    def test_active_full_build_refuses_default_edit_without_mutation_or_cancel(self):
        client = ScriptedClient()
        active_full = _run(101, 10, mode="full", active=True)
        _add_pr_states(client, _pr())
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
                    any(method != "GET" for method, _endpoint, _body in client.calls)
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
        self.assertEqual(decision.action, "updated")

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
            call for call in client.calls if call[0] != "GET"
        ]
        self.assertEqual(
            mutations,
            [
                (
                    "PATCH",
                    _endpoint(f"pulls/{PR_NUMBER}"),
                    {"title": "Essential correction"},
                )
            ],
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

    def test_body_only_no_op_performs_no_run_query_or_mutation(self):
        client = ScriptedClient()
        _add_pr_states(client, _pr())
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
        self.assertEqual(decision.action, "no-op")
        self.assertEqual(len(client.calls), 1)

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
        self.assertEqual(
            [call for call in client.calls if call[0] != "GET"],
            [
                (
                    "PATCH",
                    _endpoint(f"pulls/{PR_NUMBER}"),
                    {"body": "essential correction"},
                )
            ],
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
                "requested body",
            ),
            (
                "title",
                "New title",
                None,
                _pr(title="Stable title"),
                "requested title",
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
        )
        for name, title, body, response, message in cases:
            with self.subTest(mismatch=name):
                client = ScriptedClient()
                successful_full = _run(101, 10, mode="full")
                _add_pr_states(client, _pr(), _pr())
                _add_snapshot(client, [successful_full], copies=2)
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
        self.assertEqual(decision.action, "updated")
        pr_gets = [
            call
            for call in client.calls
            if call[:2] == ("GET", _endpoint(f"pulls/{PR_NUMBER}"))
        ]
        self.assertEqual(len(pr_gets), 2)

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

        decision = pr_metadata.reconcile_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
        )

        self.assertEqual(decision.action, "rerun")
        self.assertEqual(decision.run_id, 202)
        mutations = [call for call in client.calls if call[0] != "GET"]
        self.assertEqual(
            mutations,
            [("POST", _endpoint("actions/runs/202/rerun"), None)],
        )
        self.assertFalse(any("/cancel" in endpoint for _method, endpoint, _body in client.calls))
        self.assertFalse(any("/dispatches" in endpoint for _method, endpoint, _body in client.calls))

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
                    pr_metadata.reconcile_metadata(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                    )
                self.assertFalse(
                    any(method != "GET" for method, _endpoint, _body in client.calls)
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
        for job_name in ("extended-host-tests", "legacy", "patch-release"):
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
                    pr_metadata.reconcile_metadata(
                        client,
                        repository=REPOSITORY,
                        pr_number=PR_NUMBER,
                        head_sha=HEAD,
                        base_sha=BASE,
                    )
                self.assertFalse(
                    any(method != "GET" for method, _endpoint, _body in client.calls)
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

        decision = pr_metadata.reconcile_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
        )
        self.assertEqual(decision.action, "deferred")
        self.assertFalse(decision.mutated)
        self.assertFalse(any(method != "GET" for method, _endpoint, _body in client.calls))

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
        decision = pr_metadata.reconcile_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
        )
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.run_id, 101)
        self.assertFalse(any(method != "GET" for method, _endpoint, _body in client.calls))

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
            pr_metadata.reconcile_metadata(
                client,
                repository=REPOSITORY,
                pr_number=PR_NUMBER,
                head_sha=HEAD,
                base_sha=BASE,
            )
        self.assertFalse(any(method != "GET" for method, _endpoint, _body in client.calls))

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
        decision = pr_metadata.reconcile_metadata(
            client,
            repository=REPOSITORY,
            pr_number=PR_NUMBER,
            head_sha=HEAD,
            base_sha=BASE,
        )
        self.assertEqual(decision.action, "complete")
        self.assertFalse(decision.mutated)

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
                    ("event", "pull_request"),
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
                    "repeat an identity",
                ):
                    pr_metadata.list_candidate_runs(
                        client,
                        pr_metadata.fetch_pull_request(
                            client,
                            REPOSITORY,
                            PR_NUMBER,
                        ),
                    )

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
            "head": ("head", {"sha": NEW_HEAD}),
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
        other_jobs_endpoint = _query(
            "actions/runs/202/attempts/1/jobs",
            [("per_page", "100"), ("page", "1")],
        )
        self.assertFalse(
            any(endpoint == other_jobs_endpoint for _method, endpoint, _body in client.calls)
        )

    def test_canonical_comment_update_uses_comment_api_only(self):
        client = ScriptedClient()
        body = (
            f"{pr_metadata.EVIDENCE_MARKER}\n"
            f"Candidate SHA: {HEAD}\n"
            "No ARM runtime test is required for this host-only orchestration change.\n"
        )
        _add_pr_states(client, _pr(), _pr())
        client.add(
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

    def test_decision_run_and_comment_ids_are_strictly_mutually_exclusive(self):
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
        for changes in (
            {"action": "comment-updated"},
            {"action": "comment-updated", "run_id": 101},
            {"action": "comment-updated", "run_id": 101, "comment_id": 301},
            {"action": "deferred", "comment_id": 301},
            {"action": "deferred", "run_id": True},
            {"action": "deferred", "run_id": 0},
            {"action": "deferred", "run_id": 1000000000000000000},
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
                "repository owner",
            ),
        ):
            with self.subTest(mismatch=name):
                client = ScriptedClient()
                _add_pr_states(client, _pr(), _pr())
                client.add(
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
                client.add(
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
                client.add(
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
                client.add(
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
        client.add(
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
        client.add(
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
                stdout='HTTP/2 200 OK\nContent-Type: application/json\n\n{"ok":true}\n',
                stderr="",
            )

        client = pr_metadata.GitHubClient("/usr/bin/true", runner=runner)
        payload = {
            "title": '$(touch /tmp/never)"; gh run cancel 1; #',
            "body": "line\n--method DELETE",
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
                        stdout=stdout,
                        stderr="",
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
        calls = [
            _cli_api_call(
                "GET",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(),
            ),
            *_cli_snapshot_calls([(active_record, active_jobs)]),
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
                    "an exact-head full Build is active; update the canonical "
                    "evidence comment instead"
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
            _cli_api_call(
                "PATCH",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(title="CLI title", body=body),
                input_text=json.dumps(
                    {"body": body, "title": "CLI title"},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
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
                    ]
                ],
                mutated=True,
                reason=(
                    "metadata updated; reconcile the exact metadata-only run "
                    "to close any non-atomic same-SHA Build race"
                ),
                run_id=101,
            ),
        )
        self.assert_isolated_calls(records, len(calls))
        self.assertFalse(marker.exists())
        self.assertEqual(
            json.loads(records[-1]["input"]),
            {"body": body, "title": "CLI title"},
        )
        self.assertEqual(records[-1]["argv"][-2:], ["--input", "-"])

    def test_reconcile_and_evidence_comment_launcher_paths(self):
        failed_metadata = _run(
            202,
            11,
            mode="metadata-only",
            success=False,
        )
        successful_full = _run(101, 10, mode="full")
        runs = [failed_metadata, successful_full]
        calls = [
            _cli_api_call(
                "GET",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(),
            ),
            *_cli_snapshot_calls(runs),
            _cli_api_call(
                "GET",
                _endpoint(f"pulls/{PR_NUMBER}"),
                payload=_pr(),
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
            self.common_arguments(),
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
            _cli_api_call(
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
            ),
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
