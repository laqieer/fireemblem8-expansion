import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.workflow_pilot import reporter, review_family


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CANDIDATE = "a8768e4f467c36f8bec60ee823d7d1735d3fcd45"
BASE = "853cff1eb7bdb3ecce46f780473e81be73e24315"
COMPLETE = FIXTURES / "review_family_complete.json"
COMPLETE_EVIDENCE = FIXTURES / "review_family_complete_evidence.json"
DEFAULT = FIXTURES / "review_family_default.json"
DEFAULT_EVIDENCE = FIXTURES / "review_family_default_evidence.json"
COPILOT_ACTOR_ID = review_family.COPILOT_GRAPHQL_NODE_ID

def load(path):
    return reporter.load_json(path)

def fixture(kind="complete"):
    if kind == "complete":
        return load(COMPLETE), load(COMPLETE_EVIDENCE)
    return load(DEFAULT), load(DEFAULT_EVIDENCE)

def parsed_time(minute, second=0):
    return datetime(2026, 8, 31, 4, minute, second, tzinfo=timezone.utc)

def round_record(number, head, minute):
    return {
        "id": 1000 + number,
        "node_id": f"REMOTE_{number}",
        "round": number,
        "reviewer_actor_id": "COPILOT",
        "candidate_sha": head,
        "submitted_at": f"2026-08-31T04:{minute:02d}:00Z",
        "_submitted": parsed_time(minute),
        "state": "CHANGES_REQUESTED",
        "body": "### 🟡 Changes recommended",
        "body_classification": "changes-recommended",
        "body_has_findings": True,
        "outcome": "changes-requested",
        "finding_ids": [],
    }

def disposition(round_number, held, next_head, minute, actor="COORDINATOR"):
    return {
        "node_id": f"DISPOSITION_{round_number}",
        "held_round": round_number,
        "held_head_sha": held,
        "authorized_next_head_sha": next_head,
        "actor_id": actor,
        "action": "redesign",
        "occurred_at": f"2026-08-31T04:{minute:02d}:00Z",
        "_occurred": parsed_time(minute),
    }


class ReviewFamilyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        cls.authority = artifact_root / f"review-family-authority-{os.getpid()}"
        subprocess.run(
            reporter.git_command(
                ROOT, "worktree", "add", "--detach", str(cls.authority), CANDIDATE
            ),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
    @classmethod
    def tearDownClass(cls):
        subprocess.run(
            reporter.git_command(
                ROOT, "worktree", "remove", "--force", str(cls.authority)
            ),
            env=reporter.git_environment(offline=True),
            check=True,
            capture_output=True,
        )
    def report(self, contract, evidence, candidate=CANDIDATE):
        return review_family.build_report(
            contract, evidence, self.authority, candidate
        )
    def assert_rejected(self, contract, evidence, message, candidate=CANDIDATE):
        with self.assertRaisesRegex(reporter.PilotDataError, message):
            self.report(contract, evidence, candidate)
    def test_complete_fixture_preserves_five_closed_families(self):
        contract, evidence = fixture()
        report = self.report(contract, evidence)
        self.assertEqual(report["identity"]["base_sha"], BASE)
        self.assertEqual(
            set(report["families"]), set(review_family.FAMILY_MEMBERS)
        )
        self.assertEqual(
            report["findings"]["by_family"],
            {family: 1 for family in review_family.FAMILY_MEMBERS},
        )
        self.assertEqual(
            report["round_handoffs"][0]["bounds"],
            {"findings": 5, "families": 5, "siblings": 18},
        )
        self.assertTrue(report["trigger"]["adversarial_pre_review_required"])
        self.assertTrue(report["trigger"]["authoritative"])
        self.assertFalse(report["provenance"]["authoritative"])
        self.assertFalse(report["structural_eligibility"]["merge"])
        self.assertFalse(report["gates"]["merge_allowed"])
    def test_default_requires_current_clean_remote_copilot_review(self):
        contract, evidence = fixture("default")
        report = self.report(contract, evidence)
        self.assertTrue(report["gates"]["current_candidate_reviewed"])
        self.assertTrue(report["gates"]["current_candidate_clean"])
        self.assertFalse(report["trigger"]["adversarial_pre_review_required"])
        self.assertTrue(report["trigger"]["authoritative"])
        self.assertFalse(report["structural_eligibility"]["merge"])
        evidence["remote_reviews"] = []
        report = self.report(contract, evidence)
        self.assertFalse(report["gates"]["current_candidate_reviewed"])
        self.assertTrue(report["gates"]["remote_copilot_review_required"])
    def test_authoritative_trigger_is_required_and_exact_for_base_pinned_mode(self):
        contract, evidence = fixture("default")
        evidence["authoritative_trigger"]["risk_boundaries"] = ["lifecycle"]
        evidence["authoritative_trigger"]["threshold_triggers"] = ["risk-boundary"]
        evidence["authoritative_trigger"]["pre_review_required"] = True
        self.assert_rejected(
            contract,
            evidence,
            "candidate trigger does not match the authoritative decision record",
        )
        contract, evidence = fixture("default")
        contract["trust_mode"] = "base-pinned"
        evidence["authoritative_trigger"] = None
        self.assert_rejected(
            contract,
            evidence,
            "base-pinned mode requires an authoritative trigger decision",
        )
        contract, evidence = fixture("default")
        evidence["authoritative_trigger"]["pull_request"] = 902
        self.assert_rejected(contract, evidence, "contract PR")
        contract, evidence = fixture("default")
        evidence["authoritative_trigger"]["base_sha"] = "f" * 40
        self.assert_rejected(contract, evidence, "exact contract base")
        contract, evidence = fixture("default")
        evidence["authoritative_trigger"]["candidate_sha"] = "f" * 40
        self.assert_rejected(contract, evidence, "exact candidate head")
        contract, evidence = fixture("default")
        evidence["authoritative_trigger"]["blob_oid"] = "f" * 40
        self.assert_rejected(contract, evidence, "exact base blob")
    def test_exact_base_and_head_are_not_ancestor_substitutions(self):
        contract, evidence = fixture("default")
        evidence["pull_request"]["base_sha"] = "e" * 40
        evidence["pull_request"]["mergeable"] = "MERGEABLE"
        self.assert_rejected(contract, evidence, "live base tip")
        contract, evidence = fixture("default")
        contract["base_sha"] = CANDIDATE
        evidence["pull_request"]["base_sha"] = CANDIDATE
        self.assert_rejected(contract, evidence, "base|changed files")
        contract, evidence = fixture("default")
        self.assert_rejected(contract, evidence, "actual Git HEAD", "f" * 40)
    def test_initial_pre_review_head_may_precede_first_remote_head_with_bounded_history(self):
        a, b, c = "a" * 40, "b" * 40, "c" * 40
        authority = {"commits": {a: {"parents": []}, b: {"parents": [a]}, c: {"parents": []}}}
        def check(remote_head, history):
            review_family._validate_initial_remote_head_binding(
                {"pre_reviews": [{"candidate_sha": a}], "remote_reviews": [{"candidate_sha": remote_head}], "pull_request": {"commit_shas": history}},
                authority,
            )
        check(a, None)
        check(b, [a, b])
        for remote_head, history, pattern in (
            (b, None, "commit history is required"),
            (b, [b], "does not preserve"),
            (b, [b, a], "does not precede"),
            (c, [a, c], "non-rewritten ancestor"),
        ):
            with self.subTest(remote_head=remote_head, history=history), self.assertRaisesRegex(reporter.PilotDataError, pattern):
                check(remote_head, history)
    def test_candidate_result_ids_cannot_select_executable_evidence(self):
        contract, evidence = fixture("default")
        contract["behavior_rows"][0]["assertions"]["positive"] = (
            "candidate-claims-pass"
        )
        self.assert_rejected(contract, evidence, "closed base assertion")
        contract, evidence = fixture("default")
        evidence["result_manifest"] = [
            {
                "id": "candidate-claims-pass",
                "assertion_id": "candidate:pass",
                "check_id": "candidate:pass",
                "claimed_disposition": None,
                "authority_binding": {
                    "finding_id": None,
                    "finding_family": None,
                    "finding_member": None,
                    "finding_review_id": None,
                    "finding_review_round": None,
                    "finding_head_sha": None,
                    "finding_head_tree": None,
                    "finding_origin_sha": None,
                    "finding_origin_tree": None,
                    "head_sha": CANDIDATE,
                    "head_tree": "1" * 40,
                },
                "program_path": "scripts/workflow_pilot/review_assertions.py",
                "program_blob_oid": "e" * 40,
                "program_argv": [
                    "/usr/bin/python3",
                    "-I",
                    "review_assertions.py",
                    "--stdin",
                ],
                "program_case": "candidate/self-auth",
                "program_exit_code": 0,
                "program_stdout_sha256": "f" * 64,
                "command_id": "a" * 64,
                "input_sha256": "b" * 64,
                "inputs_sha256": "c" * 64,
                "output": {"candidate": "claims-pass"},
                "output_sha256": "d" * 64,
                "base_sha": BASE,
                "candidate_sha": CANDIDATE,
                "review_round": 1,
                "status": "pass",
            }
        ]
        evidence["result_manifest"][0]["output_sha256"] = hashlib.sha256(
            reporter.normalized_json(
                evidence["result_manifest"][0]["output"]
            )
        ).hexdigest()
        self.assert_rejected(contract, evidence, "no trusted execution receipt")
    def test_candidate_can_only_reference_member_specific_registry_ids(self):
        contract, evidence = fixture()
        review_family.validate_contract(contract)
        serialized = json.dumps(contract)
        self.assertNotIn("changed_paths", serialized)
        self.assertNotIn("unchanged_paths", serialized)
        self.assertNotIn("Makefile", serialized)
        contract["family_sweeps"][0]["siblings"][1]["assertion_id"] = (
            "registry:sibling:action:targets:verified-unaffected:v2"
        )
        self.assert_rejected(contract, evidence, "member-specific")
        for sweep_index, sibling_index, family, member in (
            (0, 1, "action", "items"),
            (2, 0, "lifecycle", "entries"),
            (4, 4, "wire", "stale-bindings"),
        ):
            contract, _ = fixture()
            siblings = contract["family_sweeps"][sweep_index]["siblings"]
            sibling = siblings[sibling_index]
            sibling["result"] = "verified-unaffected"
            sibling["assertion_id"] = (
                f"registry:sibling:{family}:{member}:verified-unaffected:v2"
            )
            fallback = next(item for item in siblings if item["member"] != member)
            fallback["result"] = "affected-fixed"
            fallback["assertion_id"] = (
                f"registry:sibling:{family}:{fallback['member']}:affected-fixed:v2"
            )
            with self.subTest(family=family, member=member):
                review_family.validate_contract(contract)
        contract, _ = fixture()
        second = contract["family_sweeps"][0]["siblings"][0]
        second["result"] = "affected-fixed"
        second["assertion_id"] = (
            "registry:sibling:action:actions:affected-fixed:v2"
        )
        validated = review_family.validate_contract(contract)
        self.assertEqual(
            [
                sibling["result"]
                for sibling in validated["family_sweeps"][0]["siblings"]
            ].count("affected-fixed"),
            2,
        )
        contract, evidence = fixture()
        disabled = contract["family_sweeps"][3]["siblings"][1]
        disabled["result"] = "not-applicable"
        disabled["assertion_id"] = (
            "registry:sibling:resource:disabled:not-applicable:"
            "feature-disabled-by-contract:v2"
        )
        review_family.validate_contract(contract)
    def test_every_finding_sweep_requires_at_least_one_affected_fixed_member(self):
        contract, evidence = fixture()
        for sibling in contract["family_sweeps"][1]["siblings"]:
            sibling["result"] = "verified-unaffected"
            sibling["assertion_id"] = review_family.member_assertion_id(
                "generated", sibling["member"], "verified-unaffected"
            )
        self.assert_rejected(
            contract,
            evidence,
            "must include at least one affected-fixed member",
        )
    def test_local_pre_review_findings_are_distinct_and_not_backdated(self):
        contract, evidence = fixture()
        pre = evidence["pre_reviews"][0]
        pre["finding_ids"] = ["LOCAL-ACTION-1"]
        evidence["pre_review_findings"] = [
            {
                "id": "LOCAL-ACTION-1",
                "review_id": pre["id"],
                "candidate_sha": CANDIDATE,
                "created_at": "2026-08-31T03:09:30Z",
                "author_actor_id": pre["owner_actor_id"],
                "family": "action",
            }
        ]
        sweep = copy.deepcopy(contract["family_sweeps"][0])
        sweep["finding_id"] = "LOCAL-ACTION-1"
        contract["family_sweeps"].append(sweep)
        report = self.report(contract, evidence)
        self.assertEqual(report["findings"]["pre_review_count"], 1)
        self.assertEqual(report["findings"]["remote_count"], 5)
        evidence["pre_reviews"][0]["receipt_issued_at"] = (
            evidence["remote_reviews"][0]["submitted_at"]
        )
        self.assert_rejected(contract, evidence, "backdated or re-signed")
    def test_local_pre_review_binding_uses_original_pre_review_head_as_origin(self):
        contract, evidence = fixture()
        evidence["pre_reviews"][0]["finding_ids"] = ["LOCAL-ACTION-1"]
        evidence["pre_review_findings"] = [{"id": "LOCAL-ACTION-1", "review_id": evidence["pre_reviews"][0]["id"], "candidate_sha": CANDIDATE, "created_at": "2026-08-31T03:09:30Z", "author_actor_id": evidence["pre_reviews"][0]["owner_actor_id"], "family": "action"}]
        binding = review_family.assertion_authority_binding(review_family.validate_contract(contract), review_family.validate_evidence(evidence), self.authority, CANDIDATE, 1, review_family.member_assertion_id("action", "items", "affected-fixed"), "LOCAL-ACTION-1")
        self.assertEqual((binding["finding_head_sha"], binding["finding_origin_sha"], binding["head_sha"]), (CANDIDATE, CANDIDATE, CANDIDATE))
        self.assertNotEqual(binding["finding_origin_sha"], BASE)
    def test_local_and_remote_namespaces_cannot_overlap(self):
        contract, evidence = fixture()
        evidence["findings"][0]["node_id"] = "LOCAL-ACTION-1"
        self.assert_rejected(contract, evidence, "independent namespace")
    def test_actor_aliases_and_copilot_identity_are_enforced(self):
        contract, evidence = fixture()
        evidence["actors"][1]["login"] = "IMPLEMENTATION-AGENT_bot"
        self.assert_rejected(contract, evidence, "actor identities")
        contract, evidence = fixture()
        evidence["actors"][2]["login"] = "copilot-pull-request-reviewer-bot"
        self.assert_rejected(
            contract,
            evidence,
            "exact authoritative GitHub Copilot Bot",
        )
        contract, evidence = fixture("default")
        evidence["actors"][1] = {
            "id": review_family.COPILOT_REST_NODE_ID,
            "login": review_family.COPILOT_REST_LOGIN,
            "kind": "bot",
            "source": review_family.GITHUB_REST_ACTOR_SOURCE,
            "type": review_family.COPILOT_REST_TYPE,
            "database_id": review_family.COPILOT_REST_DATABASE_ID,
        }
        report = self.report(contract, evidence)
        self.assertTrue(report["gates"]["current_candidate_reviewed"])
        for field, value in (
            ("login", "copilot-pull-request-reviewer[bot]",),
            ("id", "BOT_kgDOCnlnWB",),
            ("type", "User",),
        ):
            contract, evidence = fixture()
            evidence["actors"][2][field] = value
            if field == "type":
                evidence["actors"][2]["kind"] = "user"
            with self.subTest(field=field, value=value):
                self.assert_rejected(
                    contract,
                    evidence,
                    "exact authoritative GitHub Copilot Bot",
                )
        contract, evidence = fixture("default")
        evidence["actors"][1]["source"] = review_family.GITHUB_REST_ACTOR_SOURCE
        evidence["actors"][1]["login"] = review_family.COPILOT_REST_LOGIN
        evidence["actors"][1]["type"] = review_family.COPILOT_REST_TYPE
        evidence["actors"][1]["database_id"] = review_family.COPILOT_REST_DATABASE_ID + 1
        self.assert_rejected(
            contract,
            evidence,
            "exact authoritative GitHub Copilot Bot",
        )
    def test_remote_findings_must_match_the_exact_review_actor(self):
        contract, evidence = fixture()
        evidence["findings"][0]["author_actor_id"] = "ACTOR_PRE_REVIEWER_001"
        self.assert_rejected(
            contract,
            evidence,
            "exact remote review actor|exact authoritative GitHub Copilot Bot",
        )
    def test_changes_requested_body_and_unresolved_threads_never_merge(self):
        contract, evidence = fixture("default")
        review = evidence["remote_reviews"][0]
        review["state"] = "CHANGES_REQUESTED"
        review["outcome"] = "changes-requested"
        report = self.report(contract, evidence)
        self.assertFalse(report["gates"]["current_candidate_clean"])
        contract, evidence = fixture("default")
        review = evidence["remote_reviews"][0]
        review["body"] = "Please fix the authority boundary."
        review["body_classification"] = "unknown"
        review["body_has_findings"] = True
        review["outcome"] = "changes-requested"
        report = self.report(contract, evidence)
        self.assertFalse(report["gates"]["current_candidate_clean"])
        contract, evidence = fixture()
        evidence["remote_reviews"].append(
            {
                "id": 1002,
                "node_id": "REMOTE_REVIEW_002",
                "round": 2,
                "reviewer_actor_id": COPILOT_ACTOR_ID,
                "candidate_sha": CANDIDATE,
                "submitted_at": "2026-08-31T03:12:30Z",
                "state": "COMMENTED",
                "body": "### 🟢 Approval recommended",
                "body_classification": "clean-approval",
                "body_has_findings": False,
                "outcome": "clean",
                "finding_ids": [],
            }
        )
        report = self.report(contract, evidence)
        self.assertTrue(report["gates"]["current_candidate_clean"])
        self.assertEqual(report["findings"]["current_unresolved"], 5)
        self.assertFalse(report["structural_eligibility"]["merge"])
    def test_pr183_clean_body_and_exact_top_level_marker_parser(self):
        clean_body = fixture("default")[1]["remote_reviews"][0]["body"]
        self.assertIn(
            "consistently satisfy the frozen issue #176 contract",
            clean_body,
        )
        self.assertEqual(
            review_family.classify_copilot_body(clean_body),
            "clean-approval",
        )
        for body, classification in (
            ("### 🟡 Changes recommended\n\nFix the finding.", "changes-recommended"),
            ("### 🔵 Needs a closer look\n\nReview manually.", "needs-closer-look"),
            ("No issues found.", "clean-legacy"),
            ("", "unknown"),
            ("> ### 🟢 Approval recommended", "unknown"),
            (
                "Unrecognized summary\n\n### 🟢 Approval recommended",
                "unknown",
            ),
            (
                "### 🟢 Approval recommended\n\n### 🟡 Changes recommended",
                "unknown",
            ),
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    review_family.classify_copilot_body(body),
                    classification,
                )
    def test_global_node_ids_are_unique_across_all_domains(self):
        contract, evidence = fixture()
        evidence["findings"][0]["node_id"] = "REMOTE_REVIEW_001"
        self.assert_rejected(contract, evidence, "global node identity collision")
    def test_normal_fast_forward_after_hold_requires_disposition(self):
        head_a = "a" * 40
        head_b = "b" * 40
        evidence = {
            "remote_reviews": [
                round_record(1, head_a, 1),
                round_record(2, head_a, 2),
                round_record(3, head_a, 3),
            ],
            "architecture_dispositions": [],
            "candidate": {"sha": head_b},
            "actors": {"COORDINATOR": {"id": "COORDINATOR"}},
        }
        with self.assertRaisesRegex(
            reporter.PilotDataError, "differs from held head"
        ):
            review_family._progress_rounds(
                evidence, {}, {"IMPLEMENTER", "COPILOT"}
            )
        evidence["architecture_dispositions"] = [
            disposition(3, head_a, head_b, 4)
        ]
        _, hold, consumed = review_family._progress_rounds(
            evidence, {}, {"IMPLEMENTER", "COPILOT"}
        )
        self.assertIsNone(hold)
        self.assertEqual(consumed, ["DISPOSITION_3"])
    def test_disposition_actor_overlap_and_replay_fail(self):
        head_a = "a" * 40
        head_b = "b" * 40
        evidence = {
            "remote_reviews": [
                round_record(1, head_a, 1),
                round_record(2, head_a, 2),
                round_record(3, head_a, 3),
            ],
            "architecture_dispositions": [
                disposition(3, head_a, head_b, 4, actor="IMPLEMENTER")
            ],
            "candidate": {"sha": head_b},
            "actors": {
                "IMPLEMENTER": {"id": "IMPLEMENTER"},
                "COORDINATOR": {"id": "COORDINATOR"},
            },
        }
        with self.assertRaisesRegex(reporter.PilotDataError, "overlaps"):
            review_family._progress_rounds(
                evidence, {}, {"IMPLEMENTER", "COPILOT"}
            )
        evidence["architecture_dispositions"] = [
            disposition(3, head_a, head_b, 4),
            {
                **disposition(3, head_a, head_b, 5),
                "node_id": "REPLAYED_DISPOSITION",
            },
        ]
        with self.assertRaisesRegex(reporter.PilotDataError, "extra|reused"):
            review_family._progress_rounds(
                evidence, {}, {"IMPLEMENTER", "COPILOT"}
            )
    def test_round_three_and_six_holds_are_independent(self):
        head_a = "a" * 40
        head_b = "b" * 40
        head_c = "c" * 40
        evidence = {
            "remote_reviews": [
                round_record(1, head_a, 1),
                round_record(2, head_a, 2),
                round_record(3, head_a, 3),
                round_record(4, head_b, 5),
                round_record(5, head_b, 6),
                round_record(6, head_b, 7),
            ],
            "architecture_dispositions": [
                disposition(3, head_a, head_b, 4),
                disposition(6, head_b, head_c, 8),
            ],
            "candidate": {"sha": head_c},
            "actors": {"COORDINATOR": {"id": "COORDINATOR"}},
        }
        handoffs, hold, consumed = review_family._progress_rounds(
            evidence, {}, {"IMPLEMENTER", "COPILOT"}
        )
        self.assertIsNone(hold)
        self.assertEqual(
            consumed, ["DISPOSITION_3", "DISPOSITION_6"]
        )
        self.assertEqual(
            [item["consecutive_change_request"] for item in handoffs],
            [1, 2, 1, 2],
        )
    def test_pushed_date_history_is_explicitly_retired(self):
        contract, evidence = fixture("default")
        evidence["candidate_advances"] = [
            {
                "node_id": "SYNTHETIC_PUSH",
                "candidate_sha": CANDIDATE,
                "pushed_at": "2026-08-31T03:08:05Z",
                "kind": "commit-push",
            }
        ]
        self.assert_rejected(contract, evidence, "pushedDate cannot attest")
    def test_status_records_cover_add_delete_modify_rename_and_copy(self):
        repository = (
            ROOT
            / "build"
            / "test-artifacts"
            / f"review-statuses-{os.getpid()}"
        )
        repository.mkdir()
        try:
            subprocess.run(
                reporter.git_command(repository, "init", "-q"),
                env=reporter.git_environment(offline=True),
                check=True,
            )
            for key, value in (
                ("user.email", "test@example.com"),
                ("user.name", "Status Test"),
            ):
                subprocess.run(
                    reporter.git_command(repository, "config", key, value),
                    env=reporter.git_environment(offline=True),
                    check=True,
                )
            for name, content in (
                ("deleted.txt", "delete me\n"),
                ("modified.txt", "before\n"),
                ("rename-old.txt", "rename identity\n"),
                ("copy-old.txt", "copy identity\n"),
            ):
                (repository / name).write_text(content, encoding="utf-8")
            subprocess.run(
                reporter.git_command(repository, "add", "."),
                env=reporter.git_environment(offline=True),
                check=True,
            )
            subprocess.run(
                reporter.git_command(repository, "commit", "-q", "-m", "base"),
                env=reporter.git_environment(offline=True),
                check=True,
            )
            base = subprocess.run(
                reporter.git_command(repository, "rev-parse", "HEAD"),
                env=reporter.git_environment(offline=True),
                check=True,
                capture_output=True,
            ).stdout.decode().strip()
            (repository / "deleted.txt").unlink()
            (repository / "modified.txt").write_text("after\n", encoding="utf-8")
            (repository / "rename-old.txt").rename(repository / "rename-new.txt")
            shutil.copy2(repository / "copy-old.txt", repository / "copy-new.txt")
            (repository / "added.txt").write_text("added\n", encoding="utf-8")
            subprocess.run(
                reporter.git_command(repository, "add", "-A"),
                env=reporter.git_environment(offline=True),
                check=True,
            )
            subprocess.run(
                reporter.git_command(repository, "commit", "-q", "-m", "head"),
                env=reporter.git_environment(offline=True),
                check=True,
            )
            head = subprocess.run(
                reporter.git_command(repository, "rev-parse", "HEAD"),
                env=reporter.git_environment(offline=True),
                check=True,
                capture_output=True,
            ).stdout.decode().strip()
            records = review_family.derive_change_records(
                repository, base, head
            )
            self.assertEqual(
                {record["status"] for record in records},
                {"A", "D", "M", "R", "C"},
            )
            deleted = next(
                record for record in records if record["status"] == "D"
            )
            self.assertEqual(deleted["old_path"], "deleted.txt")
            self.assertIsNotNone(deleted["base_blob_oid"])
            self.assertIsNone(deleted["head_blob_oid"])
        finally:
            shutil.rmtree(repository)
    def test_issue_179_deleted_entrypoint_has_base_blob_and_head_absence(self):
        records = review_family.derive_change_records(
            ROOT,
            "40d17217c7747c22451a719d75bd48fbd502595d",
            "3feb9ee1827b0390198b6b9bf4cb8d3743518b05",
        )
        deleted = next(
            record
            for record in records
            if record["old_path"]
            == "scripts/workflow_pilot/isolated_review_gate.py"
        )
        self.assertEqual(deleted["status"], "D")
        self.assertIsNotNone(deleted["base_blob_oid"])
        self.assertIsNone(deleted["new_path"])
        self.assertIsNone(deleted["head_blob_oid"])
    def test_status_record_path_mode_and_status_spoofs_fail(self):
        record = review_family.derive_change_records(
            ROOT,
            "40d17217c7747c22451a719d75bd48fbd502595d",
            "3feb9ee1827b0390198b6b9bf4cb8d3743518b05",
        )[0]
        for field, value, message in (
            ("status", "X", "must be one of"),
            ("old_path", "../escape", "normalized repository-relative"),
            ("head_mode", "100755", "contradict|unsafe"),
        ):
            mutated = copy.deepcopy(record)
            mutated[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                reporter.PilotDataError, message
            ):
                review_family._validate_change_records(
                    [mutated], "spoofed changes"
                )
    def test_cli_is_deterministic_and_never_authoritative(self):
        command = (
            sys.executable,
            "-m",
            "scripts.workflow_pilot.review_family",
            "--repository-root",
            str(self.authority),
            "--expected-candidate",
            CANDIDATE,
            "--contract",
            str(DEFAULT.relative_to(ROOT)),
            "--evidence",
            str(DEFAULT_EVIDENCE.relative_to(ROOT)),
        )
        first = subprocess.run(
            command, cwd=ROOT, check=True, capture_output=True
        )
        second = subprocess.run(
            command, cwd=ROOT, check=True, capture_output=True
        )
        self.assertEqual(first.stdout, second.stdout)
        parsed = json.loads(first.stdout)
        self.assertFalse(parsed["provenance"]["authoritative"])
        self.assertFalse(parsed["gates"]["merge_allowed"])


if __name__ == "__main__":
    unittest.main()
