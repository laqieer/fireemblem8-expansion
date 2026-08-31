import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.workflow_pilot import reporter, review_family


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
COMPLETE = FIXTURES / "review_family_complete.json"
DEFAULT = FIXTURES / "review_family_default.json"
BASELINE_EXPECTED = FIXTURES / "baseline_expected.json"


def load(path):
    return reporter.load_json(path)


def evidence(assertion="architecture disposition"):
    return [
        {
            "kind": "host-test",
            "source": "scripts/workflow_pilot/tests/test_review_family.py",
            "assertion": assertion,
        }
    ]


def add_change_round(contract, round_number, candidate_sha):
    finding_id = f"finding-action-{round_number}"
    finding = copy.deepcopy(contract["findings"][0])
    finding["id"] = finding_id
    finding["candidate_sha"] = candidate_sha
    contract["findings"].append(finding)

    sweep = copy.deepcopy(contract["family_sweeps"][0])
    sweep["finding_id"] = finding_id
    sweep["candidate_sha"] = candidate_sha
    contract["family_sweeps"].append(sweep)
    contract["remote_reviews"].append(
        {
            "round": round_number,
            "review_id": 1000 + round_number,
            "reviewer": reporter.REVIEW_BOT,
            "candidate_sha": candidate_sha,
            "outcome": "changes-requested",
            "finding_ids": [finding_id],
        }
    )
    contract["candidate_sha"] = candidate_sha


class ReviewFamilyContractTests(unittest.TestCase):
    def assert_rejected(self, contract, message):
        with self.assertRaisesRegex(reporter.PilotDataError, message):
            review_family.build_report(contract)

    def test_complete_fixture_expands_every_family(self):
        report = review_family.build_report(load(COMPLETE))

        self.assertTrue(report["pre_review"]["required"])
        self.assertTrue(report["pre_review"]["completed"])
        self.assertEqual(
            report["pre_review"]["record"]["permissions"],
            ["contents:read"],
        )
        self.assertEqual(report["findings"]["count"], 5)
        self.assertEqual(
            report["findings"]["by_family"],
            {family: 1 for family in review_family.FAMILY_MEMBERS},
        )
        handoffs = {
            handoff["family"]: handoff
            for handoff in report["findings"]["handoffs"]
        }
        self.assertEqual(set(handoffs), set(review_family.FAMILY_MEMBERS))
        for family, members in review_family.FAMILY_MEMBERS.items():
            with self.subTest(family=family):
                self.assertEqual(
                    {sibling["member"] for sibling in handoffs[family]["siblings"]},
                    set(members),
                )
                self.assertTrue(
                    all(
                        sibling["evidence"]
                        for sibling in handoffs[family]["siblings"]
                    )
                )

        self.assertEqual(len(report["round_handoffs"]), 1)
        self.assertEqual(
            report["round_handoffs"][0]["bounds"],
            {"findings": 5, "families": 5, "siblings": 18},
        )
        self.assertTrue(report["gates"]["push_allowed"])
        self.assertFalse(report["gates"]["merge_allowed"])

    def test_each_family_rejects_one_omitted_sibling(self):
        original = load(COMPLETE)
        for sweep_index, sweep in enumerate(original["family_sweeps"]):
            family = original["findings"][sweep_index]["family"]
            contract = copy.deepcopy(original)
            contract["family_sweeps"][sweep_index]["siblings"].pop()
            with self.subTest(family=family):
                self.assert_rejected(contract, "lacks exact sibling coverage")

    def test_family_sweep_rejects_missing_evidence_and_stale_sha(self):
        contract = load(COMPLETE)
        contract["family_sweeps"][0]["siblings"][0]["evidence"] = []
        self.assert_rejected(contract, "must not be empty")

        contract = load(COMPLETE)
        contract["family_sweeps"][0]["candidate_sha"] = "b" * 40
        self.assert_rejected(contract, "stale candidate binding")

    def test_unknown_family_member_result_action_and_state_fail_closed(self):
        mutations = []

        contract = load(COMPLETE)
        contract["findings"][0]["family"] = "unknown"
        mutations.append(("family", contract))

        contract = load(COMPLETE)
        contract["family_sweeps"][0]["siblings"][0]["member"] = "unknown"
        mutations.append(("member", contract))

        contract = load(COMPLETE)
        contract["family_sweeps"][0]["siblings"][0]["result"] = "unknown"
        mutations.append(("result", contract))

        contract = load(COMPLETE)
        contract["remote_reviews"][0]["outcome"] = "unknown"
        mutations.append(("state", contract))

        contract = load(COMPLETE)
        contract["pre_reviews"][0]["authorized_actions"][0] = "unknown"
        mutations.append(("action", contract))

        for label, mutation in mutations:
            with self.subTest(label=label):
                self.assert_rejected(mutation, "must be one of")

    def test_pre_review_permission_mutations_fail(self):
        for action in (
            "edit",
            "push",
            "comment",
            "request-review",
            "dispatch-ci",
            "merge",
        ):
            contract = load(COMPLETE)
            contract["pre_reviews"][0]["authorized_actions"].append(action)
            with self.subTest(action=action):
                self.assert_rejected(contract, "cannot edit, push, comment")

        contract = load(COMPLETE)
        contract["pre_reviews"][0]["permissions"] = ["contents:write"]
        self.assert_rejected(contract, "permissions must be read-only")

        contract = load(COMPLETE)
        contract["pre_reviews"][0]["performed_actions"] = ["read-candidate"]
        self.assert_rejected(contract, "performed actions must complete")

    def test_pre_review_requires_fresh_separate_single_owner(self):
        contract = load(COMPLETE)
        contract["pre_reviews"].append(copy.deepcopy(contract["pre_reviews"][0]))
        self.assert_rejected(contract, "exactly one fresh")

        contract = load(COMPLETE)
        contract["pre_reviews"][0]["owner"] = contract["implementer"]
        self.assert_rejected(contract, "separate from implementer")

        contract = load(COMPLETE)
        contract["pre_reviews"][0]["fresh"] = False
        self.assert_rejected(contract, "must be fresh")

        contract = load(COMPLETE)
        contract["pre_reviews"][0]["limits"]["max_minutes"] = 61
        self.assert_rejected(contract, "exceeds bounded maximum")

    def test_duplicate_and_overlapping_finding_ownership_fail(self):
        contract = load(COMPLETE)
        contract["pre_reviews"][0]["finding_ids"] = ["finding-action"]
        self.assert_rejected(contract, "overlapping ownership")

        contract = load(COMPLETE)
        contract["remote_reviews"][0]["finding_ids"].append("finding-action")
        self.assert_rejected(contract, "contains duplicates")

    def test_behavior_rows_require_complete_mapping_and_evidence(self):
        required = (
            "production",
            "execution",
            "representation",
            "stale_state_revalidation",
            "host_validation",
            "evidence",
        )
        for field in required:
            contract = load(COMPLETE)
            del contract["behavior_rows"][0][field]
            with self.subTest(field=field):
                self.assert_rejected(contract, "missing fields")

        for field in ("predicate", "producer"):
            contract = load(COMPLETE)
            del contract["behavior_rows"][0]["production"][field]
            with self.subTest(production=field):
                self.assert_rejected(contract, "missing fields")

        for field in ("executor", "consumer"):
            contract = load(COMPLETE)
            del contract["behavior_rows"][0]["execution"][field]
            with self.subTest(execution=field):
                self.assert_rejected(contract, "missing fields")

        for evidence_class in review_family.EVIDENCE_CLASSES:
            contract = load(COMPLETE)
            contract["behavior_rows"][0]["evidence"][evidence_class] = []
            with self.subTest(evidence=evidence_class):
                self.assert_rejected(contract, "must not be empty")

    def test_first_two_rounds_handoff_and_third_round_holds_push(self):
        contract = load(COMPLETE)
        add_change_round(contract, 2, "b" * 40)
        report = review_family.build_report(contract)
        self.assertEqual(
            [
                handoff["consecutive_change_request"]
                for handoff in report["round_handoffs"]
            ],
            [1, 2],
        )
        self.assertTrue(report["gates"]["push_allowed"])

        add_change_round(contract, 3, "c" * 40)
        report = review_family.build_report(contract)
        self.assertEqual(len(report["round_handoffs"]), 2)
        self.assertEqual(
            report["architecture_hold"]["record"],
            {
                "round": 3,
                "candidate_sha": "c" * 40,
                "reason": "third-consecutive-change-request",
            },
        )
        self.assertFalse(report["gates"]["push_allowed"])
        self.assertFalse(report["gates"]["merge_allowed"])

        contract["architecture_disposition"] = {
            "after_round": 3,
            "candidate_sha": "c" * 40,
            "action": "decompose",
            "evidence": evidence(),
        }
        report = review_family.build_report(contract)
        self.assertFalse(report["architecture_hold"]["required"])
        self.assertTrue(report["gates"]["push_allowed"])

    def test_unresolved_third_round_rejects_advanced_candidate_or_next_review(self):
        contract = load(COMPLETE)
        add_change_round(contract, 2, "b" * 40)
        add_change_round(contract, 3, "c" * 40)
        contract["candidate_sha"] = "d" * 40
        self.assert_rejected(contract, "candidate advanced")

        contract = load(COMPLETE)
        add_change_round(contract, 2, "b" * 40)
        add_change_round(contract, 3, "c" * 40)
        add_change_round(contract, 4, "d" * 40)
        self.assert_rejected(contract, "continued before architecture hold")

    def test_clean_round_resets_consecutive_progression(self):
        contract = load(COMPLETE)
        contract["remote_reviews"].append(
            {
                "round": 2,
                "review_id": 1002,
                "reviewer": reporter.REVIEW_BOT,
                "candidate_sha": "b" * 40,
                "outcome": "clean",
                "finding_ids": [],
            }
        )
        add_change_round(contract, 3, "c" * 40)
        report = review_family.build_report(contract)
        self.assertEqual(
            [
                handoff["consecutive_change_request"]
                for handoff in report["round_handoffs"]
            ],
            [1, 1],
        )
        self.assertFalse(report["architecture_hold"]["required"])

    def test_default_path_skips_pre_review_but_remote_review_remains_mandatory(self):
        contract = load(DEFAULT)
        report = review_family.build_report(contract)
        self.assertFalse(report["pre_review"]["required"])
        self.assertFalse(report["pre_review"]["completed"])
        self.assertTrue(report["gates"]["remote_copilot_review_required"])
        self.assertTrue(report["gates"]["current_candidate_clean"])
        self.assertTrue(report["gates"]["merge_allowed"])

        contract["remote_reviews"] = []
        report = review_family.build_report(contract)
        self.assertTrue(report["gates"]["remote_copilot_review_required"])
        self.assertFalse(report["gates"]["current_candidate_reviewed"])
        self.assertFalse(report["gates"]["merge_allowed"])

    def test_stale_zero_finding_remote_review_does_not_permit_merge(self):
        contract = load(DEFAULT)
        contract["candidate_sha"] = "e" * 40
        report = review_family.build_report(contract)
        self.assertFalse(report["gates"]["current_candidate_reviewed"])
        self.assertFalse(report["gates"]["current_candidate_clean"])
        self.assertFalse(report["gates"]["merge_allowed"])

    def test_trigger_enforces_enabled_and_disabled_pre_review_boundary(self):
        contract = load(COMPLETE)
        contract["pre_reviews"] = []
        self.assert_rejected(contract, "exactly one fresh")

        contract = load(DEFAULT)
        contract["pre_reviews"] = copy.deepcopy(load(COMPLETE)["pre_reviews"])
        self.assert_rejected(contract, "must not assign overlapping")

        contract = load(DEFAULT)
        contract["trigger"]["threshold_triggers"] = ["risk-boundary"]
        self.assert_rejected(contract, "requires a named risk")

    def test_remote_review_identity_and_exact_sha_binding_fail_closed(self):
        contract = load(DEFAULT)
        contract["remote_reviews"][0]["reviewer"] = "fresh-adversarial-reviewer"
        self.assert_rejected(contract, "must remain assigned to GitHub Copilot")

        contract = load(COMPLETE)
        contract["findings"][0]["candidate_sha"] = "b" * 40
        self.assert_rejected(contract, "stale candidate binding")

        contract = load(COMPLETE)
        contract["remote_reviews"][0]["round"] = 2
        self.assert_rejected(contract, "consecutive from 1")

    def test_metric_bindings_use_frozen_reporter_namespace(self):
        report = review_family.build_report(load(DEFAULT))
        paths = load(BASELINE_EXPECTED)["paths"]
        for metric_paths in report["metric_bindings"].values():
            for path in metric_paths:
                with self.subTest(path=path):
                    if path == "delivery.first_push_to_clean_review":
                        self.assertTrue(
                            any(
                                candidate.startswith(path + ".")
                                for candidate in paths
                            )
                        )
                    else:
                        self.assertIn(path, paths)

        self.assertEqual(paths["reviews.rounds"], 34)
        self.assertEqual(paths["reviews.valid_findings"], 101)
        self.assertEqual(paths["reviews.valid_findings_per_kloc"], "5.054")
        self.assertIsNone(
            paths["delivery.first_push_to_clean_review.median_hours"]
        )
        self.assertEqual(paths["efficiency.pilot_coordination_minutes"], 0)
        self.assertEqual(paths["efficiency.metadata_maintenance_minutes"], 0)

    def test_cli_output_is_canonical_and_deterministic(self):
        command = (
            sys.executable,
            "-m",
            "scripts.workflow_pilot.review_family",
            "--contract",
            str(COMPLETE.relative_to(ROOT)),
        )
        first = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        second = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        self.assertEqual(first.stdout, second.stdout)
        self.assertTrue(first.stdout.endswith(b"\n"))
        parsed = json.loads(first.stdout)
        self.assertEqual(
            first.stdout,
            reporter.normalized_json(parsed),
        )


if __name__ == "__main__":
    unittest.main()
