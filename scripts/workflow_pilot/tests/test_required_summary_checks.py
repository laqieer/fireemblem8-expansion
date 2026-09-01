from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from scripts.workflow_pilot import required_summary_checks


ROOT = Path(__file__).resolve().parents[3]
ARTIFACT_ROOT = ROOT / "build" / "test-artifacts"
ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
CONTRACT = ROOT / ".github" / "required-summary-checks.json"
CLI = ROOT / "scripts" / "workflow_pilot" / "required_summary_checks.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CURRENT = FIXTURES / "ruleset_19088702_current.json"
DESIRED = FIXTURES / "ruleset_19088702_desired.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    path.write_bytes(required_summary_checks.normalized_json(payload))


class RequiredSummaryChecksTests(unittest.TestCase):
    def test_contract_matches_current_and_desired_fixtures(self) -> None:
        contract = required_summary_checks.validate_contract(
            required_summary_checks.load_json(CONTRACT)
        )
        self.assertEqual(contract.ruleset_identity["id"], 19088702)
        self.assertEqual(contract.repository, "laqieer/fireemblem8-expansion")
        self.assertEqual(contract.negative_proof_run_ids, (33472008301, 33472111689))
        self.assertEqual(contract.post_apply_volatile_fields, ("updated_at",))
        self.assertEqual(
            contract.status_check_contract["required"],
            [{"context": "summary", "integration_id": 15368}],
        )
        self.assertEqual(
            contract.status_check_contract["preserved_independent"],
            [{"context": "GitGuardian Security Checks", "integration_id": 46505}],
        )
        self.assertEqual(
            contract.status_check_contract["removed"],
            [
                {"context": "build", "integration_id": 15368},
                {"context": "host-tests", "integration_id": 15368},
            ],
        )
        self.assertEqual(
            contract.source_ruleset_response,
            required_summary_checks.normalize_ruleset_response(
                required_summary_checks.load_json(CURRENT),
                "current",
                volatile_fields=(),
            ),
        )
        self.assertEqual(
            contract.desired_ruleset_response,
            required_summary_checks.normalize_ruleset_response(
                required_summary_checks.load_json(DESIRED),
                "desired",
                volatile_fields=("updated_at",),
            ),
        )

    def test_preview_cli_emits_exact_patch_body_and_verify_accepts_target(self) -> None:
        sandbox = ARTIFACT_ROOT / "required-summary-checks-cli"
        sandbox.mkdir(parents=True, exist_ok=True)
        patch_path = sandbox / "patch.json"
        verified_path = sandbox / "verified.json"
        preview = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                str(CLI),
                "preview",
                "--contract",
                str(CONTRACT),
                "--live",
                str(CURRENT),
                "--output",
                str(patch_path),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(preview.returncode, 0, preview.stderr)
        contract = required_summary_checks.validate_contract(
            required_summary_checks.load_json(CONTRACT)
        )
        self.assertEqual(load_json(patch_path), contract.desired_patch_body)

        verify = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                str(CLI),
                "verify",
                "--contract",
                str(CONTRACT),
                "--live",
                str(DESIRED),
                "--output",
                str(verified_path),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertEqual(
            load_json(verified_path),
            {
                "repository": "laqieer/fireemblem8-expansion",
                "required_build_contexts": ["summary"],
                "ruleset_id": 19088702,
                "status": "ok",
                "verified_state": "desired",
            },
        )

    def test_preview_refuses_target_state_and_live_drift(self) -> None:
        contract = required_summary_checks.validate_contract(
            required_summary_checks.load_json(CONTRACT)
        )
        with self.assertRaises(required_summary_checks.RulesetContractError):
            required_summary_checks.preview_patch(
                contract,
                required_summary_checks.load_json(DESIRED),
            )

        current = load_json(CURRENT)
        current["conditions"]["ref_name"]["include"] = ["refs/heads/master"]
        with self.assertRaisesRegex(
            required_summary_checks.RulesetContractError,
            "source state",
        ):
            required_summary_checks.preview_patch(contract, current)

        current = load_json(CURRENT)
        current["bypass_actors"] = [
            {"actor_id": 8841957, "actor_type": "User", "bypass_mode": "always"}
        ]
        with self.assertRaisesRegex(
            required_summary_checks.RulesetContractError,
            "source state",
        ):
            required_summary_checks.preview_patch(contract, current)

    def test_verify_rejects_mutated_post_states(self) -> None:
        contract = required_summary_checks.validate_contract(
            required_summary_checks.load_json(CONTRACT)
        )
        desired = load_json(DESIRED)
        cases = []

        drop_gitguardian = json.loads(json.dumps(desired))
        for rule in drop_gitguardian["rules"]:
            if rule["type"] == "required_status_checks":
                rule["parameters"]["required_status_checks"] = [
                    check
                    for check in rule["parameters"]["required_status_checks"]
                    if check["context"] != "GitGuardian Security Checks"
                ]
        cases.append(("drop-gitguardian", drop_gitguardian))

        drop_code_scanning = json.loads(json.dumps(desired))
        drop_code_scanning["rules"] = [
            rule for rule in drop_code_scanning["rules"] if rule["type"] != "code_scanning"
        ]
        cases.append(("drop-code-scanning", drop_code_scanning))

        drop_review_rule = json.loads(json.dumps(desired))
        drop_review_rule["rules"] = [
            rule for rule in drop_review_rule["rules"] if rule["type"] != "pull_request"
        ]
        cases.append(("drop-review-rule", drop_review_rule))

        wrong_summary_app = json.loads(json.dumps(desired))
        for rule in wrong_summary_app["rules"]:
            if rule["type"] == "required_status_checks":
                for check in rule["parameters"]["required_status_checks"]:
                    if check["context"] == "summary":
                        check["integration_id"] = 99999
        cases.append(("wrong-summary-app", wrong_summary_app))

        extra_same_name_app = json.loads(json.dumps(desired))
        for rule in extra_same_name_app["rules"]:
            if rule["type"] == "required_status_checks":
                rule["parameters"]["required_status_checks"].append(
                    {"context": "summary", "integration_id": 99999}
                )
        cases.append(("extra-same-name-app", extra_same_name_app))

        direct_workers_retained = json.loads(json.dumps(desired))
        for rule in direct_workers_retained["rules"]:
            if rule["type"] == "required_status_checks":
                rule["parameters"]["required_status_checks"].insert(
                    0,
                    {"context": "build", "integration_id": 15368},
                )
        cases.append(("direct-workers-retained", direct_workers_retained))

        for name, payload in cases:
            with self.subTest(mutation=name):
                with self.assertRaises(required_summary_checks.RulesetContractError):
                    required_summary_checks.verify_live_ruleset(contract, payload)

    def test_contract_mutations_fail_closed(self) -> None:
        raw = load_json(CONTRACT)
        cases = []

        drop_gitguardian = json.loads(json.dumps(raw))
        for rule in drop_gitguardian["desired_ruleset_response"]["rules"]:
            if rule["type"] == "required_status_checks":
                rule["parameters"]["required_status_checks"] = [
                    check
                    for check in rule["parameters"]["required_status_checks"]
                    if check["context"] != "GitGuardian Security Checks"
                ]
        cases.append(("drop-gitguardian", drop_gitguardian))

        drop_code_scanning = json.loads(json.dumps(raw))
        drop_code_scanning["desired_ruleset_response"]["rules"] = [
            rule
            for rule in drop_code_scanning["desired_ruleset_response"]["rules"]
            if rule["type"] != "code_scanning"
        ]
        drop_code_scanning["desired_patch_body"]["rules"] = [
            rule
            for rule in drop_code_scanning["desired_patch_body"]["rules"]
            if rule["type"] != "code_scanning"
        ]
        cases.append(("drop-code-scanning", drop_code_scanning))

        drop_review_rule = json.loads(json.dumps(raw))
        drop_review_rule["desired_ruleset_response"]["rules"] = [
            rule
            for rule in drop_review_rule["desired_ruleset_response"]["rules"]
            if rule["type"] != "pull_request"
        ]
        drop_review_rule["desired_patch_body"]["rules"] = [
            rule
            for rule in drop_review_rule["desired_patch_body"]["rules"]
            if rule["type"] != "pull_request"
        ]
        cases.append(("drop-review-rule", drop_review_rule))

        wrong_summary_app = json.loads(json.dumps(raw))
        for rule in wrong_summary_app["desired_ruleset_response"]["rules"]:
            if rule["type"] == "required_status_checks":
                for check in rule["parameters"]["required_status_checks"]:
                    if check["context"] == "summary":
                        check["integration_id"] = 99999
        for rule in wrong_summary_app["desired_patch_body"]["rules"]:
            if rule["type"] == "required_status_checks":
                for check in rule["parameters"]["required_status_checks"]:
                    if check["context"] == "summary":
                        check["integration_id"] = 99999
        cases.append(("wrong-summary-app", wrong_summary_app))

        extra_same_name_app = json.loads(json.dumps(raw))
        for section in ("desired_ruleset_response", "desired_patch_body"):
            for rule in extra_same_name_app[section]["rules"]:
                if rule["type"] == "required_status_checks":
                    rule["parameters"]["required_status_checks"].append(
                        {"context": "summary", "integration_id": 99999}
                    )
        cases.append(("extra-same-name-app", extra_same_name_app))

        direct_workers_retained = json.loads(json.dumps(raw))
        for section in ("desired_ruleset_response", "desired_patch_body"):
            for rule in direct_workers_retained[section]["rules"]:
                if rule["type"] == "required_status_checks":
                    rule["parameters"]["required_status_checks"].insert(
                        0,
                        {"context": "build", "integration_id": 15368},
                    )
        cases.append(("direct-workers-retained", direct_workers_retained))

        for name, payload in cases:
            with self.subTest(contract_mutation=name):
                with self.assertRaises(required_summary_checks.RulesetContractError):
                    required_summary_checks.validate_contract(payload)

    def test_duplicate_json_keys_fail_closed(self) -> None:
        sandbox = ARTIFACT_ROOT / "required-summary-checks-duplicates"
        sandbox.mkdir(parents=True, exist_ok=True)
        duplicate = sandbox / "duplicate.json"
        duplicate.write_text('{"a":1,"a":2}\n', encoding="utf-8")
        with self.assertRaises(required_summary_checks.RulesetContractError):
            required_summary_checks.load_json(duplicate)


if __name__ == "__main__":
    unittest.main()
