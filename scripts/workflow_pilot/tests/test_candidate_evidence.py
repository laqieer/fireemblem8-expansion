"""Semantic candidate-check context regressions for issue #177."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.workflow_pilot import candidate_evidence


HEAD = "1" * 40
BASE = "2" * 40
FIXTURES = Path(__file__).with_name("fixtures")
LIVE_METADATA_JOBS = (
    FIXTURES / "live_metadata_jobs_33472008301.json",
    FIXTURES / "live_metadata_jobs_33472111689.json",
)
LITERAL_SKIPPED_NAME = (
    "${{ needs.event-classifier.result == 'success' && "
    "needs.event-classifier.outputs.classification == 'metadata-only' && "
    "'metadata-worker-skipped' || 'worker' }}"
)


def _context(job_id, name=None, conclusion="success"):
    return {
        "conclusion": conclusion,
        "job_id": job_id,
        "name": job_id if name is None else name,
    }


def _run(run_id, contexts):
    return {
        "base_sha": BASE,
        "contexts": contexts,
        "event": "pull_request",
        "head_sha": HEAD,
        "run_id": run_id,
    }


def _full_run(run_id, summary="success"):
    contexts = [_context("event-identity"), _context("event-router")]
    contexts.append(_context("patch-release", conclusion="skipped"))
    contexts.append(_context("event-classifier"))
    contexts.extend(
        _context(job_id)
        for job_id in candidate_evidence.WORKER_JOB_IDS
    )
    contexts.append(_context("summary", conclusion=summary))
    return _run(run_id, contexts)


def _metadata_run(run_id, worker_conclusion="skipped"):
    contexts = [_context("event-identity"), _context("event-router")]
    contexts.append(_context("patch-release", conclusion="skipped"))
    contexts.append(
        _context("event-classifier", candidate_evidence.METADATA_CLASSIFIER)
    )
    contexts.extend(
        _context(job_id, conclusion=worker_conclusion)
        for job_id in candidate_evidence.WORKER_JOB_IDS
    )
    contexts.append(
        _context("summary", candidate_evidence.METADATA_ATTESTATION)
    )
    return _run(run_id, contexts)


def _classifier_failure_metadata_run(run_id):
    run = _metadata_run(run_id)
    next(
        context
        for context in run["contexts"]
        if context["job_id"] == "event-classifier"
    )["conclusion"] = "failure"
    summary = next(
        context
        for context in run["contexts"]
        if context["job_id"] == "summary"
    )
    summary["name"] = candidate_evidence.FULL_ATTESTATION
    summary["conclusion"] = "failure"
    return run


def _run_from_jobs_fixture(path: Path, *, base_sha: str = BASE, head_sha: str = HEAD):
    fixture = json.loads(path.read_text(encoding="utf-8"))
    mapping = {
        "event-identity": "event-identity",
        "event-router": "event-router",
        "event-classifier": "event-classifier",
        "metadata-classifier": "event-classifier",
        "patch-release": "patch-release",
        "summary": "summary",
        "metadata-summary": "summary",
    }

    def stable_job_id(name: str) -> str:
        if name in mapping:
            return mapping[name]
        for job_id in candidate_evidence.WORKER_JOB_IDS:
            if name.endswith(f"'{job_id}' }}}}"):
                return job_id
        raise KeyError(name)

    return {
        "base_sha": base_sha,
        "contexts": [
            {
                "conclusion": job["conclusion"],
                "job_id": stable_job_id(job["name"]),
                "name": job["name"],
            }
            for job in fixture["jobs"]
        ],
        "event": "pull_request",
        "head_sha": head_sha,
        "run_id": fixture["run_id"],
    }


class CandidateEvidenceTests(unittest.TestCase):
    def test_full_success_is_eligible_and_metadata_only_is_not(self):
        full = _full_run(1)
        metadata = _metadata_run(2)
        self.assertEqual(
            candidate_evidence.REQUIRED_BUILD_CONTEXTS,
            frozenset({"summary"}),
        )
        self.assertTrue(
            candidate_evidence.evaluate_candidate_runs(
                [full],
                head_sha=HEAD,
                base_sha=BASE,
            ).eligible
        )
        result = candidate_evidence.evaluate_candidate_runs(
            [metadata],
            head_sha=HEAD,
            base_sha=BASE,
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.mode, "metadata-only")

    def test_failed_full_then_green_metadata_remains_ineligible(self):
        failed_full = _full_run(10, summary="failure")
        metadata = _metadata_run(11)

        result = candidate_evidence.evaluate_candidate_runs(
            [failed_full, metadata],
            head_sha=HEAD,
            base_sha=BASE,
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.mode, "full")
        self.assertEqual(result.run_id, 10)
        latest = candidate_evidence.latest_contexts([failed_full, metadata])
        self.assertEqual(latest["summary"], (10, "failure"))
        self.assertEqual(latest["metadata-summary"], (11, "success"))
        for context_name in candidate_evidence.REQUIRED_BUILD_CONTEXTS:
            with self.subTest(required=context_name):
                self.assertEqual(latest[context_name], (10, "failure"))
        for job_id in candidate_evidence.WORKER_JOB_IDS:
            with self.subTest(job_id=job_id):
                self.assertEqual(latest[job_id], (11, "skipped"))

    def test_green_metadata_does_not_replace_prior_full_success(self):
        full = _full_run(20)
        metadata = _metadata_run(21)
        result = candidate_evidence.evaluate_candidate_runs(
            [full, metadata],
            head_sha=HEAD,
            base_sha=BASE,
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.run_id, 20)
        latest = candidate_evidence.latest_contexts([full, metadata])
        self.assertEqual(latest["summary"], (20, "success"))
        self.assertEqual(latest["metadata-summary"], (21, "success"))
        for context_name in candidate_evidence.REQUIRED_BUILD_CONTEXTS:
            with self.subTest(required=context_name):
                self.assertEqual(latest[context_name], (20, "success"))
        for job_id in candidate_evidence.WORKER_JOB_IDS:
            with self.subTest(job_id=job_id):
                self.assertEqual(latest[job_id], (21, "skipped"))

    def test_live_literal_worker_names_are_rejected_as_negative_proof(self):
        exact = _metadata_run(30)
        self.assertEqual(candidate_evidence.run_mode(exact), "metadata-only")
        self.assertFalse(
            candidate_evidence.evaluate_candidate_runs(
                [exact],
                head_sha=HEAD,
                base_sha=BASE,
            ).eligible
        )

        cases = []
        for path, expected_run_id in zip(
            LIVE_METADATA_JOBS,
            (33472008301, 33472111689),
        ):
            live_jobs = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(live_jobs["run_id"], expected_run_id)
            self.assertEqual(
                [
                    job["name"]
                    for job in live_jobs["jobs"]
                    if job["conclusion"] == "skipped"
                ],
                [
                    job["name"]
                    for job in live_jobs["jobs"]
                    if job["runner_name"] is None
                ],
            )
            drifted_live = _run_from_jobs_fixture(path)
            for job_id in candidate_evidence.WORKER_JOB_IDS:
                with self.subTest(run_id=expected_run_id, job_id=job_id):
                    literal_name = next(
                        context["name"]
                        for context in drifted_live["contexts"]
                        if context["job_id"] == job_id
                    )
                    self.assertNotEqual(literal_name, job_id)
            for context in drifted_live["contexts"]:
                if context["job_id"] in candidate_evidence.WORKER_JOB_IDS:
                    self.assertEqual(context["conclusion"], "skipped")
                    self.assertIn("github.event_name == 'pull_request'", context["name"])
            cases.append((f"live-literal-drift-{expected_run_id}", drifted_live))

        generic_literal = _metadata_run(31)
        for context in generic_literal["contexts"]:
            if context["job_id"] in candidate_evidence.WORKER_JOB_IDS:
                context["name"] = LITERAL_SKIPPED_NAME
        cases.append(("generic-literal", generic_literal))

        canonical = _metadata_run(32)
        for context in canonical["contexts"]:
            if context["job_id"] in candidate_evidence.WORKER_JOB_IDS:
                context["name"] = f"metadata-{context['job_id']}-skipped"
        cases.append(("evaluated-metadata-name", canonical))

        success_shaped = _metadata_run(33, worker_conclusion="success")
        cases.append(("success-shaped", success_shaped))

        duplicate = _metadata_run(34)
        next(
            context
            for context in duplicate["contexts"]
            if context["job_id"] == "build"
        )["name"] = "host-tests"
        cases.append(("duplicate", duplicate))

        for name, mutation in cases:
            with self.subTest(mutation=name):
                with self.assertRaises(candidate_evidence.CandidateEvidenceError):
                    candidate_evidence.run_mode(mutation)
                with self.assertRaises(candidate_evidence.CandidateEvidenceError):
                    candidate_evidence.latest_contexts([mutation])
                with self.assertRaises(candidate_evidence.CandidateEvidenceError):
                    candidate_evidence.evaluate_candidate_runs(
                        [mutation],
                        head_sha=HEAD,
                        base_sha=BASE,
                    )

    def test_classifier_failure_metadata_shape_is_rejected(self):
        failed = _classifier_failure_metadata_run(35)
        with self.assertRaisesRegex(
            candidate_evidence.CandidateEvidenceError,
            "attest different modes",
        ):
            candidate_evidence.run_mode(failed)
        with self.assertRaises(candidate_evidence.CandidateEvidenceError):
            candidate_evidence.latest_contexts([failed])
        with self.assertRaises(candidate_evidence.CandidateEvidenceError):
            candidate_evidence.evaluate_candidate_runs(
                [failed],
                head_sha=HEAD,
                base_sha=BASE,
            )

    def test_mode_and_context_mutations_fail_closed(self):
        mutations = []

        mixed = _full_run(40)
        next(
            context
            for context in mixed["contexts"]
            if context["job_id"] == "event-classifier"
        )["name"] = candidate_evidence.METADATA_CLASSIFIER
        mutations.append(mixed)

        unknown = _full_run(41)
        unknown["contexts"][0]["job_id"] = "attacker-job"
        mutations.append(unknown)

        duplicate = _full_run(42)
        duplicate["contexts"].append(copy.deepcopy(duplicate["contexts"][0]))
        mutations.append(duplicate)

        renamed_full_worker = _full_run(43)
        next(
            context
            for context in renamed_full_worker["contexts"]
            if context["job_id"] == "build"
        )["name"] = "literal-or-dynamic-name"
        mutations.append(renamed_full_worker)

        metadata_named_full_worker = _full_run(44)
        next(
            context
            for context in metadata_named_full_worker["contexts"]
            if context["job_id"] == "build"
        )["name"] = "metadata-build-skipped"
        mutations.append(metadata_named_full_worker)

        metadata_literal_full_worker = _full_run(45)
        next(
            context
            for context in metadata_literal_full_worker["contexts"]
            if context["job_id"] == "build"
        )["name"] = next(
            context["name"]
            for context in _run_from_jobs_fixture(LIVE_METADATA_JOBS[0])["contexts"]
            if context["job_id"] == "build"
        )
        mutations.append(metadata_literal_full_worker)

        missing_full_worker = _full_run(46)
        missing_full_worker["contexts"] = [
            context
            for context in missing_full_worker["contexts"]
            if context["job_id"] != "host-tests"
        ]
        mutations.append(missing_full_worker)

        missing_metadata_worker = _metadata_run(47)
        missing_metadata_worker["contexts"] = [
            context
            for context in missing_metadata_worker["contexts"]
            if context["job_id"] != "host-tests"
        ]
        mutations.append(missing_metadata_worker)

        run_id = 48
        for mode, factory in (
            ("full", _full_run),
            ("metadata", _metadata_run),
        ):
            for conclusion in ("missing", "failure", "skipped"):
                identity = factory(run_id)
                identity_context = next(
                    context
                    for context in identity["contexts"]
                    if context["job_id"] == "event-identity"
                )
                if conclusion == "missing":
                    identity["contexts"].remove(identity_context)
                else:
                    identity_context["conclusion"] = conclusion
                mutations.append(identity)
                run_id += 1

        renamed_identity = _metadata_run(run_id)
        next(
            context
            for context in renamed_identity["contexts"]
            if context["job_id"] == "event-identity"
        )["name"] = "trusted-setup"
        mutations.append(renamed_identity)
        run_id += 1

        for mode, factory in (
            ("full", _full_run),
            ("metadata", _metadata_run),
        ):
            for conclusion in ("missing", "failure", "skipped"):
                router = factory(run_id)
                router_context = next(
                    context
                    for context in router["contexts"]
                    if context["job_id"] == "event-router"
                )
                if conclusion == "missing":
                    router["contexts"].remove(router_context)
                else:
                    router_context["conclusion"] = conclusion
                mutations.append(router)
                run_id += 1

        renamed_router = _metadata_run(run_id)
        next(
            context
            for context in renamed_router["contexts"]
            if context["job_id"] == "event-router"
        )["name"] = "router-setup"
        mutations.append(renamed_router)

        duplicate_router = _full_run(run_id + 1)
        router_context = next(
            context
            for context in duplicate_router["contexts"]
            if context["job_id"] == "event-router"
        )
        duplicate_router["contexts"].append(copy.deepcopy(router_context))
        mutations.append(duplicate_router)
        run_id += 2

        for mode, factory in (
            ("full", _full_run),
            ("metadata", _metadata_run),
        ):
            for conclusion in ("missing", "success", "failure"):
                patch = factory(run_id)
                patch_context = next(
                    context
                    for context in patch["contexts"]
                    if context["job_id"] == "patch-release"
                )
                if conclusion == "missing":
                    patch["contexts"].remove(patch_context)
                else:
                    patch_context["conclusion"] = conclusion
                mutations.append(patch)
                run_id += 1

        renamed_patch = _full_run(run_id)
        next(
            context
            for context in renamed_patch["contexts"]
            if context["job_id"] == "patch-release"
        )["name"] = "patch-publisher"
        mutations.append(renamed_patch)
        run_id += 1

        duplicate_patch = _metadata_run(run_id)
        patch_context = next(
            context
            for context in duplicate_patch["contexts"]
            if context["job_id"] == "patch-release"
        )
        duplicate_patch["contexts"].append(copy.deepcopy(patch_context))
        mutations.append(duplicate_patch)

        for mutation in mutations:
            with self.subTest(contexts=mutation["contexts"]):
                with self.assertRaises(candidate_evidence.CandidateEvidenceError):
                    candidate_evidence.evaluate_candidate_runs(
                        [mutation],
                        head_sha=HEAD,
                        base_sha=BASE,
                    )


if __name__ == "__main__":
    unittest.main()
