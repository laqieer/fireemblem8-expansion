"""Parsed Build event classification fixtures for issue #177."""

from __future__ import annotations

import json
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


class EventClassifierFixtureTests(unittest.TestCase):
    def test_all_fixture_decisions_are_exact_and_deterministic(self):
        fixture = _load_fixture()
        self.assertEqual(fixture["schema_version"], 1)
        self.assertFalse(fixture["workflow_dispatch_supported"])
        self.assertEqual(
            [case["id"] for case in fixture["cases"]],
            [
                "body-only",
                "title-only",
                "body-and-title",
                "base-only",
                "mixed-base-and-body",
                "unknown-edit-field",
                "incomplete-body-change",
                "opened",
                "synchronize",
                "reopened",
                "master-push",
            ],
        )
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                decision = event_classifier.classify_event(
                    case["event_name"],
                    case["payload"],
                    github_ref=case["github_ref"],
                    github_sha=case["github_sha"],
                    expected_build_sha=case["expected_build_sha"],
                )
                expected = dict(case["expected"])
                expected.pop("jobs")
                self.assertEqual(asdict(decision), expected)
                first = decision.canonical_json().encode("ascii")
                second = decision.canonical_json().encode("ascii")
                self.assertEqual(first, second)
                self.assertEqual(json.loads(first), expected)

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
                        [
                            "/usr/bin/python3",
                            "-I",
                            str(LAUNCHER),
                            "classify-event",
                            "--event-name",
                            case["event_name"],
                            "--event-path",
                            str(event_path),
                            "--github-ref",
                            case["github_ref"],
                            "--github-sha",
                            case["github_sha"],
                            "--expected-build-sha",
                            case["expected_build_sha"],
                            "--output",
                            str(output_path),
                        ],
                        cwd=ROOT,
                        env=environment,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    expected = dict(case["expected"])
                    expected.pop("jobs")
                    self.assertEqual(json.loads(completed.stdout), expected)
                    self.assertEqual(
                        output_path.read_text(encoding="ascii").splitlines(),
                        [
                            f"classification={expected['classification']}",
                            f"reason={expected['reason']}",
                            "run_expensive="
                            + ("true" if expected["run_expensive"] else "false"),
                            f"expected_head={expected['expected_head']}",
                        ],
                    )

    def test_malformed_event_files_fail_instead_of_suppressing_workers(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-event-malformed-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            duplicate = sandbox / "duplicate.json"
            duplicate.write_text('{"action":"edited","action":"opened"}\n', encoding="ascii")
            with self.assertRaisesRegex(
                event_classifier.EventClassificationError,
                "repeats key",
            ):
                event_classifier.load_event(duplicate)

            oversized = sandbox / "oversized.json"
            oversized.write_bytes(b" " * (event_classifier.MAX_EVENT_BYTES + 1))
            with self.assertRaisesRegex(
                event_classifier.EventClassificationError,
                "exceeds 1 MiB",
            ):
                event_classifier.load_event(oversized)

    def test_incomplete_identity_and_unknown_event_fail_closed_to_full(self):
        expected = "1" * 40
        incomplete = event_classifier.classify_event(
            "pull_request",
            {"action": "edited", "changes": {"body": {"from": "old"}}},
            github_ref="refs/pull/177/merge",
            github_sha="a" * 40,
            expected_build_sha=expected,
        )
        unknown = event_classifier.classify_event(
            "future-event",
            {},
            github_ref="refs/unknown",
            github_sha=expected,
            expected_build_sha=expected,
        )
        self.assertTrue(incomplete.run_expensive)
        self.assertEqual(incomplete.reason, "incomplete-pull-request")
        self.assertTrue(unknown.run_expensive)
        self.assertEqual(unknown.reason, "unknown-event")


if __name__ == "__main__":
    unittest.main()
