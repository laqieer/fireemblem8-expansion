"""Behavioral regressions for exact-candidate pull-request metadata edits."""

from __future__ import annotations

import copy
import json
import subprocess
import unittest

from scripts.workflow_pilot import candidate_evidence, pr_metadata


REPOSITORY = "owner/repo"
PR_NUMBER = 199
HEAD = "1" * 40
BASE = "2" * 40
NEW_HEAD = "3" * 40
WORKFLOW_ID = 1234


def _endpoint(suffix: str) -> str:
    return pr_metadata._endpoint(REPOSITORY, suffix)


def _query(suffix: str, pairs: list[tuple[str, str]]) -> str:
    return pr_metadata._query_endpoint(REPOSITORY, suffix, pairs)


def _pr(*, head: str = HEAD, base: str = BASE) -> dict:
    return {
        "number": PR_NUMBER,
        "state": "open",
        "title": "Stable title",
        "body": "Stable body",
        "head": {"sha": head},
        "base": {
            "sha": base,
            "repo": {"full_name": REPOSITORY},
        },
    }


def _job(
    name: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    runner_name: str | None = "GitHub Actions 1",
    started_at: str | None = "2026-09-04T00:00:00Z",
) -> dict:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "runner_name": runner_name,
        "started_at": started_at,
    }


def _full_jobs(*, active: bool = False) -> list[dict]:
    if active:
        return [
            _job(
                name,
                status="queued",
                conclusion=None,
                runner_name=None,
                started_at=None,
            )
            for name in sorted(pr_metadata.FULL_JOB_NAMES)
        ]
    jobs = [_job(name) for name in sorted(pr_metadata.FULL_SUCCESS_JOB_NAMES)]
    jobs.append(
        _job(
            "patch-release",
            conclusion="skipped",
            runner_name=None,
            started_at=None,
        )
    )
    return jobs


def _metadata_jobs(*, success: bool = False) -> list[dict]:
    jobs = []
    for name in sorted(pr_metadata.METADATA_JOB_NAMES):
        if name in {"extended-host-tests", "legacy", "patch-release"}:
            jobs.append(
                _job(
                    name,
                    conclusion="skipped",
                    runner_name=None,
                    started_at=None,
                )
            )
        elif name == "summary" and not success:
            jobs.append(_job(name, conclusion="failure"))
        else:
            jobs.append(_job(name))
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
        status = "queued"
        conclusion = None
    else:
        status = "completed"
        conclusion = "success" if success else "failure"
    jobs = (
        _full_jobs(active=active)
        if mode == "full"
        else _metadata_jobs(success=success)
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
    ) -> object:
        del label
        self.calls.append((method, endpoint, copy.deepcopy(body)))
        route = self.routes.get((method, endpoint))
        if not route:
            raise AssertionError(f"unexpected request: {method} {endpoint}")
        response = route.pop(0)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


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
        *(
            {
                "id": WORKFLOW_ID,
                "path": pr_metadata.WORKFLOW_PATH,
                "state": "active",
            }
            for _ in range(copies)
        ),
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

    def test_essential_override_updates_metadata_and_derives_reconciliation(self):
        client = ScriptedClient()
        active_full = _run(101, 10, mode="full", active=True)
        _add_pr_states(client, _pr(), _pr(), _pr())
        _add_snapshot(client, [active_full])
        client.add("PATCH", _endpoint(f"pulls/{PR_NUMBER}"), {"number": PR_NUMBER})

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

    def test_post_edit_identity_race_reports_deterministic_recovery(self):
        client = ScriptedClient()
        successful_full = _run(101, 10, mode="full")
        _add_pr_states(client, _pr(), _pr(), _pr(head=NEW_HEAD))
        _add_snapshot(client, [successful_full])
        client.add("PATCH", _endpoint(f"pulls/{PR_NUMBER}"), {"number": PR_NUMBER})

        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "metadata updated, but pull request identity changed concurrently",
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
            {
                "id": WORKFLOW_ID,
                "path": pr_metadata.WORKFLOW_PATH,
                "state": "active",
            },
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
            "runs?page=1",
            {"total_count": 101, "workflow_runs": first},
        )
        client.add(
            "GET",
            "runs?page=2",
            {"total_count": 101, "workflow_runs": second},
        )
        result = pr_metadata._list_counted_pages(
            client,
            endpoint_for_page=lambda page: f"runs?page={page}",
            item_key="workflow_runs",
            label="test runs",
            maximum=1000,
        )
        self.assertEqual(result, first + second)
        self.assertEqual(
            [(method, endpoint) for method, endpoint, _body in client.calls],
            [("GET", "runs?page=1"), ("GET", "runs?page=2")],
        )

    def test_counted_pagination_rejects_total_count_drift(self):
        client = ScriptedClient()
        client.add(
            "GET",
            "runs?page=1",
            {
                "total_count": 101,
                "workflow_runs": [{"id": index} for index in range(100)],
            },
        )
        client.add(
            "GET",
            "runs?page=2",
            {"total_count": 100, "workflow_runs": []},
        )
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "total_count changed",
        ):
            pr_metadata._list_counted_pages(
                client,
                endpoint_for_page=lambda page: f"runs?page={page}",
                item_key="workflow_runs",
                label="test runs",
                maximum=1000,
            )

    def test_unknown_or_mixed_run_shape_fails_closed(self):
        client = ScriptedClient()
        record, jobs = _run(101, 10, mode="full", active=True)
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

    def test_same_head_run_for_another_base_is_validated_then_ignored(self):
        client = ScriptedClient()
        other_base_record, _other_jobs = _run(202, 11, mode="full", active=True)
        other_base_record["pull_requests"][0]["base"]["sha"] = "4" * 40
        exact_full = _run(101, 10, mode="full")
        _add_pr_states(client, _pr(), _pr(), _pr())
        _add_snapshot(client, [(other_base_record, []), exact_full])
        client.add("PATCH", _endpoint(f"pulls/{PR_NUMBER}"), {"number": PR_NUMBER})

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
        _add_pr_states(client, _pr(), _pr(), _pr())
        client.add(
            "GET",
            _query(
                f"issues/{PR_NUMBER}/comments",
                [("per_page", "100"), ("page", "1")],
            ),
            [
                {"id": 300, "body": "Architecture note"},
                {
                    "id": 301,
                    "body": f"{pr_metadata.EVIDENCE_MARKER}\nOld evidence\n",
                },
            ],
        )
        client.add(
            "PATCH",
            _endpoint("issues/comments/301"),
            {"id": 301, "body": body},
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
        mutations = [call for call in client.calls if call[0] != "GET"]
        self.assertEqual(
            mutations,
            [("PATCH", _endpoint("issues/comments/301"), {"body": body})],
        )
        self.assertFalse(
            any(endpoint == _endpoint(f"pulls/{PR_NUMBER}") for _method, endpoint, _body in mutations)
        )

    def test_structured_api_argv_and_json_body_do_not_execute_input(self):
        calls = []

        def runner(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout='{"ok":true}\n',
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
        self.assertEqual(result, {"ok": True})
        arguments, kwargs = calls[0]
        self.assertIsInstance(arguments, list)
        self.assertEqual(arguments[0], client.gh_path)
        self.assertEqual(arguments[-2:], ["--input", "-"])
        self.assertEqual(json.loads(kwargs["input"]), payload)
        self.assertNotIn("shell", kwargs)
        self.assertNotIn("/cancel", " ".join(arguments))

    def test_repository_and_json_inputs_fail_closed(self):
        with self.assertRaises(pr_metadata.MetadataEditError):
            pr_metadata._repository("owner/repo;gh-run-cancel")
        with self.assertRaisesRegex(
            pr_metadata.MetadataEditError,
            "repeats key",
        ):
            pr_metadata._parse_json('{"id":1,"id":2}', "test")


if __name__ == "__main__":
    unittest.main()
