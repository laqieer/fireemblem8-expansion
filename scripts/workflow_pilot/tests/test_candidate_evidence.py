"""Semantic candidate-check context regressions for issue #177."""

from __future__ import annotations

import copy
import unittest

from scripts.workflow_pilot import candidate_evidence


HEAD = "1" * 40
BASE = "2" * 40


def _contexts(names, conclusion="success"):
    return [{"name": name, "conclusion": conclusion} for name in names]


def _run(run_id, names, conclusion="success"):
    return {
        "base_sha": BASE,
        "contexts": _contexts(names, conclusion),
        "event": "pull_request",
        "head_sha": HEAD,
        "run_id": run_id,
    }


class CandidateEvidenceTests(unittest.TestCase):
    def test_full_success_is_eligible_and_metadata_only_is_not(self):
        full = _run(1, candidate_evidence.FULL_CONTEXTS)
        metadata = _run(2, candidate_evidence.METADATA_CONTEXTS)
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
        failed_full = _run(10, candidate_evidence.FULL_CONTEXTS)
        failed_full["contexts"][-1]["conclusion"] = "failure"
        metadata = _run(11, candidate_evidence.METADATA_CONTEXTS)
        for index in range(1, 5):
            metadata["contexts"][index]["conclusion"] = "skipped"

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
        full = _run(20, candidate_evidence.FULL_CONTEXTS)
        metadata = _run(21, candidate_evidence.METADATA_CONTEXTS)
        result = candidate_evidence.evaluate_candidate_runs(
            [full, metadata],
            head_sha=HEAD,
            base_sha=BASE,
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.run_id, 20)

    def test_mode_and_context_mutations_fail_closed(self):
        full = _run(30, candidate_evidence.FULL_CONTEXTS)
        mutations = []

        mixed = copy.deepcopy(full)
        mixed["contexts"].append(
            {"name": "metadata-summary", "conclusion": "success"}
        )
        mutations.append(mixed)

        unknown = copy.deepcopy(full)
        unknown["contexts"][0]["name"] = "attacker-context"
        mutations.append(unknown)

        duplicate = copy.deepcopy(full)
        duplicate["contexts"].append(copy.deepcopy(duplicate["contexts"][0]))
        mutations.append(duplicate)

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
