"""Semantic candidate-check context regressions for issue #177."""

from __future__ import annotations

import copy
import unittest

from scripts.workflow_pilot import candidate_evidence


HEAD = "1" * 40
BASE = "2" * 40
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
        _context(
            job_id,
            candidate_evidence.METADATA_WORKER_NAMES[job_id],
            worker_conclusion,
        )
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


class CandidateEvidenceTests(unittest.TestCase):
    def test_full_success_is_eligible_and_metadata_only_is_not(self):
        full = _full_run(1)
        metadata = _metadata_run(2)
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
        for job_id in candidate_evidence.WORKER_JOB_IDS:
            with self.subTest(job_id=job_id):
                self.assertEqual(latest[job_id], (10, "success"))
                self.assertEqual(
                    latest[candidate_evidence.METADATA_WORKER_NAMES[job_id]],
                    (11, "skipped"),
                )

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
        for job_id in candidate_evidence.WORKER_JOB_IDS:
            with self.subTest(job_id=job_id):
                self.assertEqual(latest[job_id], (20, "success"))
                self.assertEqual(
                    latest[candidate_evidence.METADATA_WORKER_NAMES[job_id]],
                    (21, "skipped"),
                )

    def test_runner_1215_literal_skipped_names_are_inadmissible(self):
        cases = []

        literal = _metadata_run(30)
        for context in literal["contexts"]:
            if context["job_id"] in candidate_evidence.WORKER_JOB_IDS:
                context["name"] = LITERAL_SKIPPED_NAME
        cases.append(("literal", literal))

        canonical = _metadata_run(31)
        for context in canonical["contexts"]:
            if context["job_id"] in candidate_evidence.WORKER_JOB_IDS:
                context["name"] = context["job_id"]
        cases.append(("canonical", canonical))

        success_shaped = _metadata_run(32, worker_conclusion="success")
        cases.append(("success-shaped", success_shaped))

        duplicate = _metadata_run(33)
        next(
            context
            for context in duplicate["contexts"]
            if context["job_id"] == "build"
        )["name"] = candidate_evidence.METADATA_WORKER_NAMES["host-tests"]
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
        failed = _classifier_failure_metadata_run(34)
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
        )["name"] = candidate_evidence.METADATA_WORKER_NAMES["build"]
        mutations.append(metadata_named_full_worker)

        missing_full_worker = _full_run(45)
        missing_full_worker["contexts"] = [
            context
            for context in missing_full_worker["contexts"]
            if context["job_id"] != "host-tests"
        ]
        mutations.append(missing_full_worker)

        missing_metadata_worker = _metadata_run(46)
        missing_metadata_worker["contexts"] = [
            context
            for context in missing_metadata_worker["contexts"]
            if context["job_id"] != "host-tests"
        ]
        mutations.append(missing_metadata_worker)

        run_id = 47
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
