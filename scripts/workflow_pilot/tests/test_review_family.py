import copy
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import github_review, reporter, review_family


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CANDIDATE = "a8768e4f467c36f8bec60ee823d7d1735d3fcd45"
CURRENT_AUTHORITY = "1a6dfd06be0c6ccfddb45cfc9201b55a9ad463ef"
COMPLETE = FIXTURES / "review_family_complete.json"
COMPLETE_EVIDENCE = FIXTURES / "review_family_complete_evidence.json"
DEFAULT = FIXTURES / "review_family_default.json"
DEFAULT_EVIDENCE = FIXTURES / "review_family_default_evidence.json"
BASELINE_EXPECTED = FIXTURES / "baseline_expected.json"
GITHUB_ADAPTER = FIXTURES / "review_family_github_adapter.json"


def load(path):
    return reporter.load_json(path)


def fixture(kind="complete"):
    if kind == "complete":
        paths = (COMPLETE, COMPLETE_EVIDENCE)
    else:
        paths = (DEFAULT, DEFAULT_EVIDENCE)
    return (*tuple(load(path) for path in paths), None)


def timestamp(minute, second=0):
    return f"2026-08-31T03:{minute:02d}:{second:02d}Z"


def add_change_round(contract, evidence, round_number, minute):
    finding_id = f"FINDING_ACTION_{round_number:03d}"
    review_id = f"REMOTE_REVIEW_{round_number:03d}"
    evidence["remote_reviews"].append(
        {
            "id": 1000 + round_number,
            "node_id": review_id,
            "round": round_number,
            "reviewer_actor_id": "ACTOR_COPILOT_001",
            "candidate_sha": CANDIDATE,
            "submitted_at": timestamp(minute),
            "outcome": "changes-requested",
            "finding_ids": [finding_id],
        }
    )
    evidence["findings"].append(
        {
            "node_id": finding_id,
            "review_id": review_id,
            "candidate_sha": CANDIDATE,
            "created_at": timestamp(minute - 1, round_number),
            "family": "action",
        }
    )
    evidence["threads"].append(
        {
            "node_id": f"THREAD_ACTION_{round_number:03d}",
            "finding_id": finding_id,
            "is_resolved": False,
        }
    )
    source_sweep = contract["family_sweeps"][0]
    sweep = copy.deepcopy(source_sweep)
    sweep["finding_id"] = finding_id
    for sibling in sweep["siblings"]:
        member = sibling["member"]
        result_id = f"result-round-{round_number}-action-{member}"
        sibling["evidence_result_ids"] = [result_id]
        source_result = next(
            record
            for record in evidence["result_manifest"]
            if record["id"] == f"result-sibling-action-{member}"
        )
        result = copy.deepcopy(source_result)
        result["id"] = result_id
        evidence["result_manifest"].append(result)
    contract["family_sweeps"].append(sweep)


def add_disposition(evidence, held_round, minute):
    evidence["architecture_dispositions"].append(
        {
            "node_id": f"DISPOSITION_{held_round:03d}",
            "held_round": held_round,
            "candidate_sha": CANDIDATE,
            "actor_id": "ACTOR_IMPLEMENTER_001",
            "action": "decompose",
            "occurred_at": timestamp(minute),
        }
    )


def reseal_execution_receipt(receipt):
    receipt["seal"] = github_review.receipt_seal(receipt)


class ReviewFamilyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        cls.authority = (
            artifact_root / f"review-family-authority-{os.getpid()}"
        )
        subprocess.run(
            reporter.git_command(
                ROOT,
                "worktree",
                "add",
                "--detach",
                str(cls.authority),
                CANDIDATE,
            ),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        cls.current_authority = (
            artifact_root / f"review-family-current-authority-{os.getpid()}"
        )
        subprocess.run(
            reporter.git_command(
                ROOT,
                "worktree",
                "add",
                "--detach",
                "--no-checkout",
                str(cls.current_authority),
                CURRENT_AUTHORITY,
            ),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        subprocess.run(
            reporter.git_command(
                ROOT,
                "worktree",
                "remove",
                "--force",
                str(cls.current_authority),
            ),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            reporter.git_command(
                ROOT,
                "worktree",
                "remove",
                "--force",
                str(cls.authority),
            ),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )

    def report(self, contract, evidence, expected=None, candidate=CANDIDATE):
        return review_family.build_report(
            contract,
            evidence,
            self.authority,
            candidate,
        )

    def assert_rejected(
        self,
        contract,
        evidence,
        message,
        *,
        expected=None,
        candidate=CANDIDATE,
    ):
        with self.assertRaisesRegex(reporter.PilotDataError, message):
            self.report(
                contract,
                evidence,
                expected=expected,
                candidate=candidate,
            )

    def test_complete_fixture_expands_every_family_from_sealed_evidence(self):
        contract, evidence, expected = fixture()
        report = self.report(contract, evidence, expected)

        self.assertEqual(report["identity"]["candidate_sha"], CANDIDATE)
        self.assertEqual(
            report["identity"]["candidate_tree_oid"],
            "6a32deb6a03becec9acd37db246bb5ba08fffe9b",
        )
        self.assertEqual(
            {row["id"] for row in report["behavior_rows"]},
            set(review_family.REQUIRED_BEHAVIOR_ROWS),
        )
        self.assertEqual(
            report["findings"]["by_family"],
            {family: 1 for family in review_family.FAMILY_MEMBERS},
        )
        self.assertEqual(
            report["round_handoffs"][0]["bounds"],
            {"findings": 5, "families": 5, "siblings": 18},
        )
        self.assertFalse(report["provenance"]["authoritative"])
        self.assertFalse(report["gates"]["push_allowed"])
        self.assertFalse(report["gates"]["merge_allowed"])

    def test_default_requires_current_zero_finding_remote_review(self):
        contract, evidence, expected = fixture("default")
        report = self.report(contract, evidence, expected)

        self.assertFalse(report["trigger"]["adversarial_pre_review_required"])
        self.assertIsNone(report["actors"]["pre_reviewer"])
        self.assertTrue(report["gates"]["remote_copilot_review_required"])
        self.assertTrue(report["gates"]["current_candidate_clean"])
        self.assertFalse(report["gates"]["merge_allowed"])

        evidence["remote_reviews"] = []
        report = self.report(contract, evidence)
        self.assertTrue(report["gates"]["remote_copilot_review_required"])
        self.assertFalse(report["gates"]["current_candidate_reviewed"])
        self.assertFalse(report["gates"]["merge_allowed"])

    def test_offline_transform_cannot_self_assert_authority(self):
        contract, evidence, _ = fixture("default")
        report = self.report(contract, evidence)
        self.assertEqual(
            report["provenance"]["source"], "offline-transform-fixture"
        )
        self.assertFalse(report["provenance"]["authoritative"])
        self.assertFalse(report["provenance"]["executable_evidence_trusted"])
        self.assertFalse(report["gates"]["trusted_push_allowed"])
        self.assertFalse(report["gates"]["merge_allowed"])

        evidence["source"]["kind"] = "live-gh-api"
        self.assert_rejected(contract, evidence, "lacks collector/receipt")

    def test_canonical_evidence_source_must_be_complete(self):
        contract, evidence, _ = fixture()
        evidence["source"]["complete"] = False
        self.assert_rejected(contract, evidence, "source must be complete")

        contract, evidence, _ = fixture()
        evidence["source"]["kind"] = "self-reported-json"
        self.assert_rejected(contract, evidence, "not the canonical")

    def test_actual_head_rejects_stale_or_fabricated_candidate(self):
        contract, evidence, _ = fixture()
        fabricated = "e" * 40
        contract["candidate_sha"] = fabricated
        evidence["candidate"]["sha"] = fabricated
        self.assert_rejected(
            contract,
            evidence,
            "contract candidate does not match expected actual Git HEAD",
        )

        contract, evidence, expected = fixture()
        self.assert_rejected(
            contract,
            evidence,
            "actual Git HEAD",
            expected=expected,
            candidate="e" * 40,
        )

    def test_candidate_tree_and_source_membership_are_authoritative(self):
        contract, evidence, _ = fixture()
        evidence["pre_reviews"][0]["reviewed_files"][0] = (
            "scripts/workflow_pilot/tests/does_not_exist.py"
        )
        self.assert_rejected(contract, evidence, "no exact evidence path")

        contract, evidence, _ = fixture()
        evidence["result_source_path"] = "docs/workflow-pilot.md"
        self.assert_rejected(contract, evidence, "unrelated to the frozen")

    def test_review_and_finding_timestamps_enforce_causality(self):
        contract, evidence, _ = fixture()
        evidence["remote_reviews"][0]["submitted_at"] = timestamp(9, 30)
        self.assert_rejected(
            contract, evidence, "did not complete before remote round 1"
        )

        contract, evidence, _ = fixture("default")
        evidence["remote_reviews"][0]["submitted_at"] = timestamp(7)
        self.assert_rejected(contract, evidence, "violates commit/PR/capture")

        contract, evidence, _ = fixture()
        evidence["findings"][0]["created_at"] = timestamp(12, 30)
        self.assert_rejected(contract, evidence, "timestamp causality")

        contract, evidence, _ = fixture()
        add_change_round(contract, evidence, 2, 11)
        self.assert_rejected(
            contract, evidence, "strictly chronological"
        )

    def test_actor_aliases_case_and_bot_suffixes_cannot_overlap(self):
        aliases = (
            "Implementation-Agent",
            "implementation-agent[bot]",
            "IMPLEMENTATION-AGENT_bot",
        )
        for alias in aliases:
            contract, evidence, _ = fixture()
            evidence["actors"][1]["login"] = alias
            with self.subTest(alias=alias):
                self.assert_rejected(
                    contract, evidence, "evidence actor identities"
                )

        contract, evidence, _ = fixture()
        evidence["pre_reviews"][0]["owner_actor_id"] = "ACTOR_COPILOT_001"
        self.assert_rejected(contract, evidence, "overlaps")

        contract, evidence, _ = fixture()
        evidence["pre_reviews"].append(
            copy.deepcopy(evidence["pre_reviews"][0])
        )
        evidence["pre_reviews"][1]["id"] = "PRE_REVIEW_002"
        evidence["pre_reviews"][1]["actions"][0]["id"] = "PRE_ACTION_READ_002"
        evidence["pre_reviews"][1]["actions"][1]["id"] = "PRE_ACTION_REPORT_002"
        self.assert_rejected(contract, evidence, "exactly one fresh")

    def test_remote_actor_is_canonical_and_role_ownership_is_unique(self):
        contract, evidence, _ = fixture()
        evidence["actors"][2]["login"] = "other-reviewer[bot]"
        self.assert_rejected(contract, evidence, "canonical GitHub Copilot")

        contract, evidence, _ = fixture()
        contract["implementer_actor_id"] = "ACTOR_COPILOT_001"
        self.assert_rejected(contract, evidence, "ownership overlap")

    def test_read_only_permissions_and_actions_are_authoritative(self):
        for action in (
            "edit",
            "push",
            "comment",
            "request-review",
            "dispatch-ci",
            "merge",
        ):
            contract, evidence, _ = fixture()
            evidence["pre_reviews"][0]["actions"].append(
                {
                    "id": f"MUTATION_{action}",
                    "kind": action,
                    "occurred_at": timestamp(9, 55),
                }
            )
            with self.subTest(action=action):
                self.assert_rejected(
                    contract, evidence, "ordered exactly read-candidate"
                )

        contract, evidence, _ = fixture()
        evidence["pre_reviews"][0]["permissions"] = ["contents:write"]
        self.assert_rejected(contract, evidence, "permissions must be read-only")

        contract, evidence, _ = fixture()
        evidence["pre_reviews"][0]["actions"].reverse()
        self.assert_rejected(contract, evidence, "strictly chronological")

        contract, evidence, _ = fixture()
        duplicate = copy.deepcopy(evidence["pre_reviews"][0]["actions"][0])
        duplicate["id"] = "PRE_ACTION_READ_002"
        duplicate["occurred_at"] = timestamp(9, 20)
        evidence["pre_reviews"][0]["actions"].insert(1, duplicate)
        self.assert_rejected(
            contract, evidence, "ordered exactly read-candidate"
        )

        contract, evidence, _ = fixture()
        evidence["pre_reviews"][0]["actions"][1]["occurred_at"] = timestamp(
            9, 10
        )
        self.assert_rejected(contract, evidence, "strictly chronological")

    def test_frozen_behavior_inventory_rejects_missing_and_unknown_rows(self):
        contract, evidence, _ = fixture()
        contract["behavior_rows"].pop()
        self.assert_rejected(contract, evidence, "frozen inventory")

        contract, evidence, _ = fixture()
        contract["behavior_rows"][0]["id"] = "arbitrary-row"
        self.assert_rejected(contract, evidence, "must be one of")

    def test_result_association_mutations_fail_with_wording_unchanged(self):
        contract, evidence, _ = fixture()
        first = contract["behavior_rows"][0]["evidence_result_ids"]["positive"]
        second = contract["behavior_rows"][1]["evidence_result_ids"]["positive"]
        first[0], second[0] = second[0], first[0]
        self.assert_rejected(contract, evidence, "unrelated to behavior")

        contract, evidence, _ = fixture()
        result = next(
            record
            for record in evidence["result_manifest"]
            if record["id"] == "result-sibling-action-actions"
        )
        result["member"] = "items"
        self.assert_rejected(contract, evidence, "unrelated to action/actions")

        contract, evidence, _ = fixture()
        result = evidence["result_manifest"][0]
        result["assertion_id"] = "authority-causality:positive"
        self.assert_rejected(contract, evidence, "unrelated to behavior")

    def test_every_family_rejects_an_omitted_sibling(self):
        original = fixture()
        for index, source_sweep in enumerate(original[0]["family_sweeps"]):
            contract = copy.deepcopy(original[0])
            evidence = copy.deepcopy(original[1])
            contract["family_sweeps"][index]["siblings"].pop()
            family = next(
                finding["family"]
                for finding in evidence["findings"]
                if finding["node_id"] == source_sweep["finding_id"]
            )
            with self.subTest(family=family):
                self.assert_rejected(
                    contract, evidence, "lacks exact sibling coverage"
                )

    def test_unknown_family_member_result_action_and_state_fail_closed(self):
        mutations = []

        contract, evidence, _ = fixture()
        evidence["findings"][0]["family"] = "unknown"
        mutations.append(("family", contract, evidence))

        contract, evidence, _ = fixture()
        contract["family_sweeps"][0]["siblings"][0]["member"] = "unknown"
        mutations.append(("member", contract, evidence))

        contract, evidence, _ = fixture()
        contract["family_sweeps"][0]["siblings"][0]["result"] = "unknown"
        mutations.append(("result", contract, evidence))

        contract, evidence, _ = fixture()
        evidence["remote_reviews"][0]["outcome"] = "unknown"
        mutations.append(("state", contract, evidence))

        contract, evidence, _ = fixture()
        evidence["pre_reviews"][0]["actions"][0]["kind"] = "unknown"
        mutations.append(("action", contract, evidence))

        for label, contract, evidence in mutations:
            with self.subTest(label=label):
                self.assert_rejected(contract, evidence, "must be one of")

    def test_limits_reject_boolean_negative_unbounded_and_observed_overflow(self):
        for field, value in (
            ("max_duration_minutes", True),
            ("max_findings_per_review", -1),
            ("max_reviewed_files", 201),
            ("max_siblings_per_finding", 6),
            ("max_siblings_per_handoff", 251),
        ):
            contract, evidence, _ = fixture()
            contract["limits"][field] = value
            with self.subTest(field=field, value=value):
                self.assert_rejected(
                    contract,
                    evidence,
                    "must be an integer|must be at least|exceeds bounded maximum",
                )

        contract, evidence, _ = fixture()
        contract["limits"]["max_reviewed_files"] = 2
        self.assert_rejected(contract, evidence, "exceeds max_reviewed_files")

        contract, evidence, _ = fixture()
        evidence["pre_reviews"][0]["completed_at"] = timestamp(11)
        contract["limits"]["max_duration_minutes"] = 1
        self.assert_rejected(
            contract, evidence, "exceeds max_duration_minutes"
        )

        contract, evidence, _ = fixture()
        evidence["remote_reviews"][0]["finding_ids"] = [
            f"OVERFLOW_{index:03d}" for index in range(55)
        ]
        self.assert_rejected(
            contract, evidence, "exceeds max_findings_per_review 10"
        )

        contract, evidence, _ = fixture()
        contract["limits"]["max_siblings_per_finding"] = 2
        self.assert_rejected(
            contract, evidence, "exceeds max_siblings_per_finding"
        )

        contract, evidence, _ = fixture()
        contract["limits"]["max_siblings_per_handoff"] = 17
        self.assert_rejected(
            contract, evidence, "exceeds max_siblings_per_handoff"
        )

    def six_round_fixture(self):
        contract, evidence, _ = fixture()
        evidence["captured_at"] = timestamp(24)
        add_change_round(contract, evidence, 2, 14)
        add_change_round(contract, evidence, 3, 16)
        add_disposition(evidence, 3, 17)
        add_change_round(contract, evidence, 4, 18)
        add_change_round(contract, evidence, 5, 20)
        add_change_round(contract, evidence, 6, 22)
        add_disposition(evidence, 6, 23)
        return contract, evidence

    def test_rounds_three_and_six_hold_and_lift_independently(self):
        contract, evidence = self.six_round_fixture()
        report = self.report(contract, evidence)

        self.assertEqual(
            report["architecture_hold"]["consumed_disposition_ids"],
            ["DISPOSITION_003", "DISPOSITION_006"],
        )
        self.assertFalse(report["architecture_hold"]["required"])
        self.assertEqual(
            [
                handoff["consecutive_change_request"]
                for handoff in report["round_handoffs"]
            ],
            [1, 2, 1, 2],
        )
        self.assertFalse(report["gates"]["push_allowed"])

    def test_missing_reused_out_of_order_and_future_dispositions_fail(self):
        contract, evidence = self.six_round_fixture()
        del evidence["architecture_dispositions"][0]
        self.assert_rejected(contract, evidence, "does not match the exact held")

        contract, evidence = self.six_round_fixture()
        evidence["architecture_dispositions"][1]["held_round"] = 3
        self.assert_rejected(contract, evidence, "held rounds contains duplicates")

        contract, evidence = self.six_round_fixture()
        evidence["architecture_dispositions"].reverse()
        self.assert_rejected(contract, evidence, "not strictly ordered")

        contract, evidence = self.six_round_fixture()
        evidence["architecture_dispositions"][1]["occurred_at"] = timestamp(25)
        self.assert_rejected(contract, evidence, "follows evidence capture")

        contract, evidence = self.six_round_fixture()
        evidence["architecture_dispositions"][0]["candidate_sha"] = "e" * 40
        self.assert_rejected(contract, evidence, "does not exist")

    def test_candidate_advance_before_disposition_is_rejected(self):
        contract, evidence = self.six_round_fixture()
        evidence["candidate_advances"].append(
            {
                "node_id": "ADVANCE_PREMATURE_001",
                "candidate_sha": CANDIDATE,
                "pushed_at": timestamp(16, 30),
                "kind": "synchronize",
            }
        )
        self.assert_rejected(
            contract, evidence, "push occurred before required"
        )

    def test_current_third_round_without_disposition_retains_hold(self):
        contract, evidence, _ = fixture()
        evidence["captured_at"] = timestamp(17)
        add_change_round(contract, evidence, 2, 14)
        add_change_round(contract, evidence, 3, 16)
        report = self.report(contract, evidence)

        self.assertTrue(report["architecture_hold"]["required"])
        self.assertFalse(report["gates"]["push_allowed"])
        self.assertEqual(report["architecture_hold"]["record"]["round"], 3)

    def test_metric_bindings_preserve_frozen_issue_176_values(self):
        contract, evidence, expected = fixture("default")
        report = self.report(contract, evidence, expected)
        paths = load(BASELINE_EXPECTED)["paths"]
        for metric_paths in report["metric_bindings"].values():
            for path in metric_paths:
                with self.subTest(path=path):
                    if path == "delivery.first_push_to_clean_review":
                        self.assertTrue(
                            any(key.startswith(path + ".") for key in paths)
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

    def test_execution_receipt_seal_is_deterministic(self):
        _, evidence, _ = fixture()
        receipt = evidence["execution_receipts"][0]
        self.assertEqual(
            github_review.receipt_seal(receipt),
            receipt["seal"],
        )
        self.assertEqual(
            github_review.receipt_seal(receipt),
            github_review.receipt_seal(copy.deepcopy(receipt)),
        )

    def test_execution_receipts_reject_missing_stale_wrong_and_failed_checks(self):
        contract, evidence, _ = fixture()
        evidence["execution_receipts"] = []
        self.assert_rejected(contract, evidence, "exactly one execution receipt")

        contract, evidence, _ = fixture()
        receipt = evidence["execution_receipts"][0]
        receipt["seal"] = "f" * 64
        self.assert_rejected(contract, evidence, "execution seal")

        contract, evidence, _ = fixture()
        receipt = evidence["execution_receipts"][0]
        receipt["check_id"] = "review-family-negative-control"
        reseal_execution_receipt(receipt)
        self.assert_rejected(contract, evidence, "exactly one execution receipt")

        contract, evidence, _ = fixture()
        receipt = evidence["execution_receipts"][0]
        receipt["exit_code"] = 1
        receipt["result"] = "fail"
        reseal_execution_receipt(receipt)
        self.assert_rejected(contract, evidence, "lacks a passing")

        contract, evidence, _ = fixture()
        receipt = evidence["execution_receipts"][0]
        receipt["candidate_sha"] = "e" * 40
        receipt["id"] = "CHECK:review-family-suite:stale"
        reseal_execution_receipt(receipt)
        self.assert_rejected(contract, evidence, "does not exist")

    def test_old_result_candidate_cannot_prove_current_authority(self):
        contract, evidence, _ = fixture()

        def retarget(value):
            if isinstance(value, dict):
                return {key: retarget(item) for key, item in value.items()}
            if isinstance(value, list):
                return [retarget(item) for item in value]
            return CURRENT_AUTHORITY if value == CANDIDATE else value

        contract = retarget(contract)
        evidence = retarget(evidence)
        evidence["captured_at"] = timestamp(39)
        evidence["pull_request"]["created_at"] = timestamp(34, 30)
        evidence["candidate_advances"][0]["pushed_at"] = timestamp(34, 25)
        pre_review = evidence["pre_reviews"][0]
        pre_review["started_at"] = timestamp(35)
        pre_review["completed_at"] = timestamp(35, 30)
        pre_review["actions"][0]["occurred_at"] = timestamp(35, 5)
        pre_review["actions"][1]["occurred_at"] = timestamp(35, 25)
        evidence["remote_reviews"][0]["submitted_at"] = timestamp(38)
        for index, finding in enumerate(evidence["findings"], start=1):
            finding["created_at"] = timestamp(37, index)
        receipt = evidence["execution_receipts"][0]
        receipt["started_at"] = timestamp(35, 40)
        receipt["completed_at"] = timestamp(36)
        receipt["id"] = (
            "CHECK:review-family-suite:" + CURRENT_AUTHORITY
        )
        reseal_execution_receipt(receipt)
        evidence["result_manifest"][0]["candidate_sha"] = CANDIDATE
        with self.assertRaisesRegex(
            reporter.PilotDataError, "unrelated to behavior"
        ):
            review_family.build_report(
                contract,
                evidence,
                self.current_authority,
                CURRENT_AUTHORITY,
            )

    def test_global_node_ids_cannot_collide_across_review_and_finding(self):
        contract, evidence, _ = fixture()
        evidence["findings"][0]["node_id"] = "REMOTE_REVIEW_001"
        self.assert_rejected(
            contract, evidence, "global GitHub node identity collision"
        )

    def test_authenticated_receipt_uses_external_trust_root(self):
        contract, evidence, _ = fixture("default")
        evidence["source"]["kind"] = "authenticated-receipt"
        key = b"external-test-trust-root-key-32-bytes-minimum"
        receipt = github_review.make_authenticated_evidence_receipt(
            evidence, "test-root", key
        )
        trusted = github_review.authenticate_evidence_receipt(
            receipt, "test-root", key
        )
        report = review_family.build_report(
            contract, trusted, self.authority, CANDIDATE
        )
        self.assertTrue(report["provenance"]["authenticated_receipt"])
        self.assertTrue(report["gates"]["merge_allowed"])

        receipt["evidence"]["remote_reviews"][0]["outcome"] = (
            "changes-requested"
        )
        with self.assertRaisesRegex(
            reporter.PilotDataError, "signature is invalid"
        ):
            github_review.authenticate_evidence_receipt(
                receipt, "test-root", key
            )

    def test_unresolved_threads_block_later_clean_review(self):
        contract, evidence, _ = fixture()
        evidence["remote_reviews"].append(
            {
                "id": 1002,
                "node_id": "REMOTE_REVIEW_002",
                "round": 2,
                "reviewer_actor_id": "ACTOR_COPILOT_001",
                "candidate_sha": CANDIDATE,
                "submitted_at": timestamp(12, 30),
                "outcome": "clean",
                "finding_ids": [],
            }
        )
        evidence["source"]["kind"] = "authenticated-receipt"
        key = b"external-test-trust-root-key-32-bytes-minimum"
        authenticated = github_review.authenticate_evidence_receipt(
            github_review.make_authenticated_evidence_receipt(
                evidence, "test-root", key
            ),
            "test-root",
            key,
        )
        report = review_family.build_report(
            contract, authenticated, self.authority, CANDIDATE
        )
        self.assertTrue(report["gates"]["current_candidate_clean"])
        self.assertEqual(report["findings"]["current_unresolved"], 5)
        self.assertFalse(report["gates"]["merge_allowed"])

    def test_registered_runner_executes_real_pass_and_fail_commands(self):
        times = iter(
            (
                datetime(2026, 8, 31, 3, 35, tzinfo=timezone.utc),
                datetime(2026, 8, 31, 3, 36, tzinfo=timezone.utc),
            )
        )
        passed = github_review.run_registered_check(
            self.authority,
            CANDIDATE,
            "review-family-suite",
            clock=lambda: next(times),
        )
        self.assertEqual(passed.raw["result"], "pass")
        self.assertEqual(passed.raw["exit_code"], 0)

        fail_times = iter(
            (
                datetime(2026, 8, 31, 3, 35, tzinfo=timezone.utc),
                datetime(2026, 8, 31, 3, 36, tzinfo=timezone.utc),
            )
        )
        failed = github_review.run_registered_check(
            self.authority,
            CANDIDATE,
            "review-family-negative-control",
            clock=lambda: next(fail_times),
        )
        self.assertEqual(failed.raw["result"], "fail")
        self.assertNotEqual(failed.raw["exit_code"], 0)

    def test_live_collector_adapter_is_authoritative_and_read_only(self):
        times = iter(
            (
                datetime(2026, 8, 31, 3, 35, 20, tzinfo=timezone.utc),
                datetime(2026, 8, 31, 3, 35, 40, tzinfo=timezone.utc),
            )
        )
        check_receipt = github_review.run_registered_check(
            self.authority,
            CANDIDATE,
            "review-family-suite",
            clock=lambda: next(times),
        )
        contract, _, _ = fixture("default")
        with mock.patch.object(
            github_review.GhApiAdapter,
            "fetch",
            return_value=load(GITHUB_ADAPTER),
        ) as fetch:
            trusted = github_review.collect_live_evidence(
                contract,
                self.authority,
                CANDIDATE,
                [check_receipt],
                clock=lambda: datetime(
                    2026, 8, 31, 3, 40, tzinfo=timezone.utc
                ),
            )
        fetch.assert_called_once_with(
            "laqieer/fireemblem8-expansion", 179
        )
        report = review_family.build_report(
            contract, trusted, self.authority, CANDIDATE
        )
        self.assertTrue(report["provenance"]["live_authoritative"])
        self.assertTrue(report["provenance"]["executable_evidence_trusted"])
        self.assertTrue(report["gates"]["trusted_push_allowed"])
        self.assertTrue(report["gates"]["merge_allowed"])

    def test_live_collector_rejects_incomplete_adapter_pages(self):
        payload = load(GITHUB_ADAPTER)
        payload["data"]["repository"]["pullRequest"]["commits"][
            "pageInfo"
        ]["hasNextPage"] = True
        contract, _, _ = fixture("default")
        with mock.patch.object(
            github_review.GhApiAdapter, "fetch", return_value=payload
        ):
            with self.assertRaisesRegex(
                reporter.PilotDataError, "exceeds the bounded complete"
            ):
                github_review.collect_live_evidence(
                    contract,
                    self.authority,
                    CANDIDATE,
                    [],
                )

    def test_live_collector_rejects_reviewer_github_mutation(self):
        payload = load(GITHUB_ADAPTER)
        payload["data"]["repository"]["pullRequest"]["comments"][
            "nodes"
        ].append(
            {
                "id": "COMMENT_MUTATION_001",
                "createdAt": "2026-08-31T03:38:00Z",
                "body": "mutation",
                "author": {
                    "id": "ACTOR_COLLECTOR_001",
                    "login": "fresh-collector",
                },
            }
        )
        contract, _, _ = fixture("default")
        with mock.patch.object(
            github_review.GhApiAdapter, "fetch", return_value=payload
        ):
            with self.assertRaisesRegex(
                reporter.PilotDataError, "performed a GitHub comment action"
            ):
                github_review.collect_live_evidence(
                    contract,
                    self.authority,
                    CANDIDATE,
                    [],
                )

    def test_cli_offline_transform_is_deterministic_and_cannot_merge(self):
        command = (
            sys.executable,
            "-m",
            "scripts.workflow_pilot.review_family",
            "--repository-root",
            str(self.authority),
            "--expected-candidate",
            CANDIDATE,
            "--contract",
            str(COMPLETE.relative_to(ROOT)),
            "--evidence",
            str(COMPLETE_EVIDENCE.relative_to(ROOT)),
        )
        first = subprocess.run(
            command, cwd=ROOT, check=True, capture_output=True
        )
        second = subprocess.run(
            command, cwd=ROOT, check=True, capture_output=True
        )
        self.assertEqual(first.stdout, second.stdout)
        self.assertTrue(first.stdout.endswith(b"\n"))
        parsed = json.loads(first.stdout)
        self.assertEqual(first.stdout, reporter.normalized_json(parsed))
        self.assertFalse(parsed["provenance"]["authoritative"])
        self.assertFalse(parsed["gates"]["merge_allowed"])


if __name__ == "__main__":
    unittest.main()
