"""Parsed Build event classification fixtures for issue #177."""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from scripts.workflow_pilot import event_classifier


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).with_name("fixtures") / "event_classification.json"
LAUNCHER = ROOT / "scripts" / "workflow_pilot" / "isolated_launcher.py"


def _load_fixture() -> dict:
    with FIXTURE.open("r", encoding="utf-8") as source:
        return json.load(source)


def _decision(case: dict) -> event_classifier.EventDecision:
    return event_classifier.classify_event(
        case["event_name"],
        case["payload"],
        github_ref=case["runner"]["github_ref"],
        github_sha=case["runner"]["github_sha"],
        pr_base_sha=case["runner"]["pr_base_sha"],
        pr_head_sha=case["runner"]["pr_head_sha"],
        push_sha=case["runner"]["push_sha"],
    )


def _expected_decision(case: dict) -> dict:
    expected = dict(case["expected"])
    expected.pop("jobs")
    expected.pop("summary_success")
    return expected


def _launcher_command(case: dict, event_path: Path, output_path: Path) -> list[str]:
    runner = case["runner"]
    return [
        "/usr/bin/python3",
        "-I",
        str(LAUNCHER),
        "classify-event",
        "--event-name",
        case["event_name"],
        "--event-path",
        str(event_path),
        "--github-ref",
        runner["github_ref"],
        "--github-sha",
        runner["github_sha"],
        "--pr-base-sha",
        runner["pr_base_sha"],
        "--pr-head-sha",
        runner["pr_head_sha"],
        "--push-sha",
        runner["push_sha"],
        "--output",
        str(output_path),
    ]


class EventClassifierFixtureTests(unittest.TestCase):
    def test_all_fixture_decisions_are_exact_and_deterministic(self):
        fixture = _load_fixture()
        self.assertEqual(fixture["schema_version"], 3)
        self.assertFalse(fixture["workflow_dispatch_supported"])
        self.assertEqual(
            [case["id"] for case in fixture["cases"]],
            [
                "body-only-merge-sha-ignored",
                "title-only",
                "body-and-title",
                "base-only",
                "mixed-base-and-body",
                "unknown-edit-field",
                "incomplete-body-change",
                "stacked-opened",
                "synchronize",
                "reopened",
                "missing-base",
                "missing-head",
                "missing-head-and-base",
                "master-push",
            ],
        )
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                decision = _decision(case)
                expected = _expected_decision(case)
                self.assertEqual(asdict(decision), expected)
                first = decision.canonical_json().encode("ascii")
                second = decision.canonical_json().encode("ascii")
                self.assertEqual(first, second)
                self.assertEqual(json.loads(first), expected)

    def test_missing_pr_identity_never_substitutes_the_merge_sha(self):
        fixture = _load_fixture()
        cases = {case["id"]: case for case in fixture["cases"]}
        for case_id in ("missing-base", "missing-head", "missing-head-and-base"):
            with self.subTest(case=case_id):
                case = cases[case_id]
                decision = _decision(case)
                self.assertFalse(decision.identity_valid)
                self.assertTrue(decision.run_expensive)
                self.assertNotEqual(
                    decision.expected_head,
                    case["runner"]["github_sha"],
                )
                self.assertEqual(
                    set(case["expected"]["jobs"]),
                    {"event-classifier", "summary"},
                )
                self.assertFalse(case["expected"]["summary_success"])

        body_only = cases["body-only-merge-sha-ignored"]
        decision = _decision(body_only)
        self.assertTrue(decision.identity_valid)
        self.assertEqual(
            decision.expected_head,
            body_only["runner"]["pr_head_sha"],
        )
        self.assertNotEqual(
            decision.expected_head,
            body_only["runner"]["github_sha"],
        )

    def test_disposable_github_event_files_drive_the_isolated_launcher(self):
        fixture = _load_fixture()
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-event-classifier-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            for case in fixture["cases"]:
                with self.subTest(case=case["id"]):
                    event_path = sandbox / f"{case['id']}.json"
                    output_path = sandbox / f"{case['id']}.out"
                    event_path.write_text(
                        json.dumps(case["payload"], sort_keys=True) + "\n",
                        encoding="ascii",
                    )
                    environment = dict(os.environ)
                    environment.update(
                        {
                            "GIT_DIR": str(sandbox / "redirected.git"),
                            "PYTHONPATH": str(sandbox),
                        }
                    )
                    completed = subprocess.run(
                        _launcher_command(case, event_path, output_path),
                        cwd=ROOT,
                        env=environment,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    expected = _expected_decision(case)
                    self.assertEqual(json.loads(completed.stdout), expected)
                    self.assertEqual(
                        output_path.read_text(encoding="ascii").splitlines(),
                        [
                            f"classification={expected['classification']}",
                            f"expected_base={expected['expected_base']}",
                            f"expected_head={expected['expected_head']}",
                            "identity_valid="
                            + ("true" if expected["identity_valid"] else "false"),
                            f"reason={expected['reason']}",
                            "run_expensive="
                            + ("true" if expected["run_expensive"] else "false"),
                        ],
                    )

    def test_json_parser_rejects_duplicates_size_and_nonfinite_numbers(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-event-malformed-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            invalid = {
                "duplicate": '{"action":"edited","action":"opened"}\n',
                "nan": '{"value":NaN}\n',
                "infinity": '{"value":Infinity}\n',
                "malformed": '{"action":"edited"\n',
                "negative-infinity": '{"value":-Infinity}\n',
            }
            case = _load_fixture()["cases"][0]
            for name, raw in invalid.items():
                with self.subTest(name=name):
                    event_path = sandbox / f"{name}.json"
                    output_path = sandbox / f"{name}.out"
                    event_path.write_text(raw, encoding="ascii")
                    with self.assertRaises(event_classifier.EventClassificationError):
                        event_classifier.load_event(event_path)
                    completed = subprocess.run(
                        _launcher_command(case, event_path, output_path),
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertFalse(output_path.exists())

            for failure_case in _load_fixture()["classifier_failure_cases"]:
                with self.subTest(failure_case=failure_case["id"]):
                    event_path = sandbox / f"{failure_case['id']}.json"
                    output_path = sandbox / f"{failure_case['id']}.out"
                    event_path.write_text(
                        invalid[failure_case["raw_kind"]],
                        encoding="ascii",
                    )
                    completed = subprocess.run(
                        _launcher_command(
                            failure_case,
                            event_path,
                            output_path,
                        ),
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertFalse(output_path.exists())

            for float_case in _load_fixture()["strict_float_cases"]:
                with self.subTest(float_case=float_case["id"]):
                    event_path = sandbox / f"{float_case['id']}.json"
                    event_path.write_text(
                        '{"unused":' + float_case["literal"] + "}\n",
                        encoding="ascii",
                    )
                    if float_case["accepted"]:
                        parsed = event_classifier.load_event(event_path)
                        value = parsed["unused"]
                        self.assertTrue(math.isfinite(value))
                        if float_case["id"] == "negative-zero":
                            self.assertEqual(math.copysign(1.0, value), -1.0)
                    else:
                        with self.assertRaises(
                            event_classifier.EventClassificationError
                        ):
                            event_classifier.load_event(event_path)

            metadata_overflow = sandbox / "metadata-overflow.json"
            metadata_payload = json.dumps(case["payload"], sort_keys=True)
            metadata_overflow.write_text(
                metadata_payload[:-1] + ',"unused":1e9999}\n',
                encoding="ascii",
            )
            overflow_output = sandbox / "metadata-overflow.out"
            completed = subprocess.run(
                _launcher_command(case, metadata_overflow, overflow_output),
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(overflow_output.exists())

            oversized = sandbox / "oversized.json"
            oversized.write_bytes(b" " * (event_classifier.MAX_EVENT_BYTES + 1))
            with self.assertRaisesRegex(
                event_classifier.EventClassificationError,
                "exceeds 1 MiB",
            ):
                event_classifier.load_event(oversized)
            with self.assertRaises(event_classifier.EventClassificationError):
                event_classifier._ensure_finite_numbers(
                    {"nested": [1.0, float("inf")]}
                )

    def test_metadata_records_require_real_schema_valid_transitions(self):
        case = next(
            case
            for case in _load_fixture()["cases"]
            if case["id"] == "body-and-title"
        )
        mutations = []

        same_body = copy.deepcopy(case)
        same_body["payload"]["changes"]["body"]["from"] = "New body"
        mutations.append(same_body)

        same_title = copy.deepcopy(case)
        same_title["payload"]["changes"]["title"]["from"] = "New title"
        mutations.append(same_title)

        null_title = copy.deepcopy(case)
        null_title["payload"]["changes"]["title"]["from"] = None
        mutations.append(null_title)

        blank_title = copy.deepcopy(case)
        blank_title["payload"]["changes"]["title"]["from"] = " "
        mutations.append(blank_title)

        missing_current = copy.deepcopy(case)
        del missing_current["payload"]["pull_request"]["body"]
        mutations.append(missing_current)

        invalid_current = copy.deepcopy(case)
        invalid_current["payload"]["pull_request"]["body"] = {"nested": True}
        mutations.append(invalid_current)

        nested_previous = copy.deepcopy(case)
        nested_previous["payload"]["changes"]["body"]["from"] = {"nested": True}
        mutations.append(nested_previous)

        extra_key = copy.deepcopy(case)
        extra_key["payload"]["changes"]["body"]["current"] = "New body"
        mutations.append(extra_key)

        for mutation in mutations:
            with self.subTest(payload=mutation["payload"]):
                decision = _decision(mutation)
                self.assertEqual(decision.classification, "full")
                self.assertEqual(decision.reason, "incomplete-edit")
                self.assertTrue(decision.identity_valid)
                self.assertTrue(decision.run_expensive)

        for previous, current in ((None, "New body"), ("Old body", None)):
            with self.subTest(previous=previous, current=current):
                transition = copy.deepcopy(case)
                transition["payload"]["changes"] = {"body": {"from": previous}}
                transition["payload"]["pull_request"]["body"] = current
                decision = _decision(transition)
                self.assertEqual(decision.classification, "metadata-only")

    def test_identity_mismatch_and_unknown_event_fail_closed(self):
        case = copy.deepcopy(_load_fixture()["cases"][0])
        case["runner"]["pr_head_sha"] = "9" * 40
        mismatch = _decision(case)
        unknown = event_classifier.classify_event(
            "future-event",
            {},
            github_ref="refs/unknown",
            github_sha="1" * 40,
            pr_base_sha="",
            pr_head_sha="",
            push_sha="",
        )
        self.assertFalse(mismatch.identity_valid)
        self.assertEqual(mismatch.reason, "pull-request-identity-mismatch")
        self.assertFalse(unknown.identity_valid)
        self.assertEqual(unknown.reason, "unknown-event")


if __name__ == "__main__":
    unittest.main()
