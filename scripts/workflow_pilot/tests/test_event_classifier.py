"""Parsed Build event classification fixtures for issue #177."""

from __future__ import annotations

import copy
import json
import math
import os
import stat
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

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
    expected["head_valid"] = bool(expected["expected_head"])
    expected["full_fallback"] = (
        case["event_name"] == "pull_request"
        and expected["head_valid"]
        and not expected["identity_valid"]
    )
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
    def test_base_refs_match_git_full_ref_oracle_with_safety_bounds(self):
        for case in _load_fixture()["base_ref_validation_cases"]:
            with self.subTest(case=case["id"]):
                oracle = subprocess.run(
                    [
                        "/usr/bin/git",
                        "check-ref-format",
                        f"refs/heads/{case['ref']}",
                    ],
                    check=False,
                    capture_output=True,
                ).returncode == 0
                if case.get("git_accepts_full_ref"):
                    self.assertTrue(oracle)
                    self.assertFalse(case["accepted"])
                else:
                    self.assertEqual(oracle, case["accepted"])
                self.assertEqual(
                    event_classifier._is_git_branch_ref(case["ref"]),
                    case["accepted"],
                )

        at_limit = "a" * event_classifier.MAX_BRANCH_REF_BYTES
        over_limit = at_limit + "a"
        self.assertTrue(event_classifier._is_git_branch_ref(at_limit))
        self.assertFalse(event_classifier._is_git_branch_ref(over_limit))
        self.assertFalse(event_classifier._is_git_branch_ref("\ud800"))

    def test_body_and_title_metadata_require_valid_base_refs(self):
        fixture = _load_fixture()
        templates = {
            field: next(
                case
                for case in fixture["cases"]
                if case["id"] == case_id
            )
            for field, case_id in (
                ("body", "body-only-merge-sha-ignored"),
                ("title", "title-only"),
            )
        }
        for ref_case in fixture["base_ref_validation_cases"]:
            for field, template in templates.items():
                with self.subTest(case=ref_case["id"], field=field):
                    case = copy.deepcopy(template)
                    case["payload"]["pull_request"]["base"]["ref"] = ref_case["ref"]
                    decision = _decision(case)
                    if ref_case["accepted"]:
                        self.assertEqual(decision.classification, "metadata-only")
                        self.assertTrue(decision.identity_valid)
                    else:
                        self.assertEqual(decision.classification, "full")
                        self.assertEqual(
                            decision.reason,
                            "missing-pull-request-base",
                        )
                        self.assertTrue(decision.head_valid)
                        self.assertTrue(decision.full_fallback)
                        self.assertFalse(decision.identity_valid)
                        self.assertEqual(
                            decision.expected_head,
                            case["runner"]["pr_head_sha"],
                        )

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
                "base-only-stack-retarget",
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
                self.assertEqual(
                    decision.full_fallback,
                    decision.head_valid,
                )
                self.assertEqual(
                    decision.head_valid,
                    bool(decision.expected_head),
                )
                self.assertTrue(decision.run_expensive)
                self.assertNotEqual(
                    decision.expected_head,
                    case["runner"]["github_sha"],
                )
                expected_jobs = {"event-classifier", "summary"}
                if case_id == "missing-base":
                    expected_jobs.update(
                        {"host-tests", "build", "extended-host-tests", "legacy"}
                    )
                self.assertEqual(set(case["expected"]["jobs"]), expected_jobs)
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
                            "full_fallback="
                            + ("true" if expected["full_fallback"] else "false"),
                            "head_valid="
                            + ("true" if expected["head_valid"] else "false"),
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

    def test_event_snapshot_rejects_actual_symlink_without_output(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="workflow-event-link-", dir=artifact_root) as temporary:
            sandbox = Path(temporary)
            target, link, output = (sandbox / name for name in ("event.json", "link.json", "event.out"))
            case = _load_fixture()["cases"][0]
            target.write_text(json.dumps(case["payload"]), encoding="ascii")
            link.symlink_to(target.name)
            self.assertEqual(event_classifier.load_event(target), case["payload"])
            with self.assertRaises(event_classifier.EventClassificationError):
                event_classifier.load_event(link)
            completed = subprocess.run(
                _launcher_command(case, link, output),
                cwd=ROOT, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertEqual(completed.stdout, b"")
            self.assertFalse(output.exists())
            self.assertEqual(event_classifier.load_event(target), case["payload"])

    def test_event_snapshot_rejects_mismatched_owner_before_read_and_closes_fd(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="workflow-event-owner-", dir=artifact_root) as temporary:
            path = Path(temporary) / "event.json"
            payload = _load_fixture()["cases"][0]["payload"]
            path.write_text(json.dumps(payload), encoding="ascii")
            self.assertEqual(event_classifier.load_event(path), payload)
            fstat = os.fstat
            descriptors = []

            def other_owner(fd):
                descriptors.append(fd)
                metadata = list(fstat(fd))
                metadata[stat.ST_UID] = os.geteuid() + 1
                return os.stat_result(metadata)

            with (
                mock.patch.object(os, "fstat", side_effect=other_owner),
                mock.patch.object(os, "read", wraps=os.read) as read,
            ):
                with self.assertRaisesRegex(event_classifier.EventClassificationError, "same-owner"):
                    event_classifier.load_event(path)
                read.assert_not_called()
            self.assertEqual(len(descriptors), 1)
            with self.assertRaises(OSError):
                fstat(descriptors[0])
            self.assertEqual(event_classifier.load_event(path), payload)

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

    def test_base_edit_requires_coherent_ref_and_sha_transition(self):
        cases = {case["id"]: case for case in _load_fixture()["cases"]}
        base_edit = cases["base-only-stack-retarget"]
        decision = _decision(base_edit)
        self.assertEqual(decision.reason, "base-edit")
        self.assertEqual(
            decision.expected_head,
            base_edit["runner"]["pr_head_sha"],
        )

        mutations = []
        missing = copy.deepcopy(base_edit)
        missing["payload"]["changes"] = {}
        mutations.append(missing)

        ref_only = copy.deepcopy(base_edit)
        del ref_only["payload"]["changes"]["base"]["sha"]
        mutations.append(ref_only)

        sha_only = copy.deepcopy(base_edit)
        del sha_only["payload"]["changes"]["base"]["ref"]
        mutations.append(sha_only)

        same_ref = copy.deepcopy(base_edit)
        same_ref["payload"]["changes"]["base"]["ref"]["from"] = "master"
        mutations.append(same_ref)

        same_sha = copy.deepcopy(base_edit)
        same_sha["payload"]["changes"]["base"]["sha"]["from"] = "2" * 40
        mutations.append(same_sha)

        spoofed = copy.deepcopy(base_edit)
        spoofed["payload"]["changes"]["base"]["ref"]["from"] = "master"
        spoofed["payload"]["changes"]["base"]["sha"]["from"] = "2" * 40
        mutations.append(spoofed)

        malformed_sha = copy.deepcopy(base_edit)
        malformed_sha["payload"]["changes"]["base"]["sha"]["from"] = "short"
        mutations.append(malformed_sha)

        malformed_ref = copy.deepcopy(base_edit)
        malformed_ref["payload"]["changes"]["base"]["ref"]["from"] = "bad ref"
        mutations.append(malformed_ref)

        extra_key = copy.deepcopy(base_edit)
        extra_key["payload"]["changes"]["base"]["current"] = {}
        mutations.append(extra_key)

        nested_extra = copy.deepcopy(base_edit)
        nested_extra["payload"]["changes"]["base"]["sha"]["extra"] = True
        mutations.append(nested_extra)

        for mutation in mutations:
            with self.subTest(changes=mutation["payload"]["changes"]):
                result = _decision(mutation)
                self.assertEqual(result.classification, "full")
                self.assertEqual(result.reason, "incomplete-edit")
                self.assertTrue(result.identity_valid)
                self.assertTrue(result.run_expensive)
                self.assertEqual(
                    result.expected_head,
                    mutation["runner"]["pr_head_sha"],
                )

        mixed = _decision(cases["mixed-base-and-body"])
        self.assertEqual(mixed.classification, "full")
        self.assertEqual(mixed.reason, "mixed-edit")
        self.assertEqual(
            mixed.expected_head,
            cases["mixed-base-and-body"]["runner"]["pr_head_sha"],
        )

    def test_valid_head_enables_full_fallback_for_every_invalid_base_shape(self):
        fixture = _load_fixture()
        template = next(
            case
            for case in fixture["cases"]
            if case["id"] == "body-only-merge-sha-ignored"
        )
        for incomplete in fixture["incomplete_base_cases"]:
            with self.subTest(case=incomplete["id"]):
                case = copy.deepcopy(template)
                case["payload"]["pull_request"]["base"] = incomplete["base"]
                case["runner"]["pr_base_sha"] = incomplete["runner_base_sha"]
                decision = _decision(case)
                self.assertEqual(decision.classification, "full")
                self.assertTrue(decision.run_expensive)
                self.assertTrue(decision.head_valid)
                self.assertTrue(decision.full_fallback)
                self.assertFalse(decision.identity_valid)
                self.assertEqual(
                    decision.expected_head,
                    case["runner"]["pr_head_sha"],
                )
                self.assertEqual(
                    decision.expected_base,
                    incomplete["expected_base"],
                )
                self.assertNotEqual(decision.classification, "metadata-only")

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
        self.assertFalse(mismatch.full_fallback)
        self.assertEqual(mismatch.reason, "pull-request-identity-mismatch")
        self.assertFalse(unknown.identity_valid)
        self.assertEqual(unknown.reason, "unknown-event")


if __name__ == "__main__":
    unittest.main()
