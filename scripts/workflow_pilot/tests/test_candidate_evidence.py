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
    contexts.append(_context("event-classifier"))
    contexts.extend(
        _context(job_id)
        for job_id in candidate_evidence.WORKER_JOB_IDS
    )
    contexts.append(_context("summary", conclusion=summary))
    return _run(run_id, contexts)


def _metadata_run(run_id, worker_conclusion="skipped"):
    contexts = [_context("event-identity"), _context("event-router")]
    contexts.append(
        _context("event-classifier", candidate_evidence.METADATA_CLASSIFIER)
    )
    contexts.extend(
        _context(
            job_id,
            LITERAL_SKIPPED_NAME,
            worker_conclusion,
        )
        for job_id in candidate_evidence.WORKER_JOB_IDS
    )
    contexts.append(
        _context("summary", candidate_evidence.METADATA_ATTESTATION)
    )
    return _run(run_id, contexts)


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

    def test_green_metadata_does_not_replace_prior_full_success(self):
        result = candidate_evidence.evaluate_candidate_runs(
            [_full_run(20), _metadata_run(21)],
            head_sha=HEAD,
            base_sha=BASE,
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.run_id, 20)

    def test_runner_1215_literal_skipped_names_are_inadmissible(self):
        for conclusion in ("skipped", "success"):
            for rendered_name in ("literal", "normal"):
                with self.subTest(
                    conclusion=conclusion,
                    rendered_name=rendered_name,
                ):
                    metadata = _metadata_run(30, worker_conclusion=conclusion)
                    if rendered_name == "normal":
                        for context in metadata["contexts"]:
                            if context["job_id"] in candidate_evidence.WORKER_JOB_IDS:
                                context["name"] = context["job_id"]
                    self.assertEqual(
                        candidate_evidence.run_mode(metadata),
                        "metadata-only",
                    )
                    result = candidate_evidence.evaluate_candidate_runs(
                        [metadata],
                        head_sha=HEAD,
                        base_sha=BASE,
                    )
                    self.assertFalse(result.eligible)
                    self.assertEqual(result.mode, "metadata-only")
                    if rendered_name == "literal":
                        for context in metadata["contexts"]:
                            if (
                                context["job_id"]
                                in candidate_evidence.WORKER_JOB_IDS
                            ):
                                self.assertEqual(
                                    context["name"],
                                    LITERAL_SKIPPED_NAME,
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

        run_id = 44
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
