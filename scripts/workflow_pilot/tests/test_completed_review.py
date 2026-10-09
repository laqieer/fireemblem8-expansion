"""Terminal native observations preserve original context without a fake lease."""

from dataclasses import asdict, replace
import unittest

from scripts.workflow_pilot import review_family as model
from scripts.workflow_pilot.tests.review_support import Runtime


class CompletedReviewTests(unittest.TestCase):
    def setUp(self):
        self.head = "a" * 40
        self.scope = frozenset({"TC-OWNERSHIP-SEALED-PLATFORM-STORAGE-001/sealed-platform-storage"})
        self.path = "scripts/validation_ownership/runtime_image.py"
        self.finding = model.Finding(
            "byte-budget", next(iter(self.scope)), "resource", "disabled:byte-body",
            self.head, self.path, "local:native-task",
        )
        self.observation = model.CompletedReviewObservation(
            "native-task", "native-reviewer", "code-review", self.head, self.scope,
            "completed", frozenset({"read-candidate", "emit-report"}),
            "2026-10-09T10:05:18.945Z", "2026-10-09T10:07:59.932Z",
            (self.path,), (self.finding,), "Original byte compatibility finding",
        )

    def session(self, *, owners=None):
        def no_monotonic_reconstruction():
            raise AssertionError("historical import must not read a monotonic clock")
        return model.ReviewSession(
            "coordinator", "coordinator", self.scope, self.head,
            identity=("owner/repo", 280, self.head),
            owners=owners if owners is not None else model.ReviewOwnership(),
            clock=no_monotonic_reconstruction,
        )

    def test_terminal_import_preserves_original_report_findings_and_unknown_total(self):
        session = self.session()
        report = session.observe_completed(self.observation)
        self.assertIsNone(session.lease)
        self.assertIsNone(report.files)
        self.assertEqual(report.observed_paths, (self.path,))
        self.assertEqual(report.original_content, self.observation.original_content)
        self.assertEqual(report.findings, (self.finding,))
        self.assertFalse(session.owners.records[id(session)][3])
        self.assertTrue(session.original_review_context_ready())
        with self.assertRaisesRegex(ValueError, "complete triage"):
            session.review_state((), (), pre_review_required=True)
        session.triage_local(
            self.finding.id, accepted=False, reason="Compatibility bytes have caller-owned budgets",
        )
        session.advance("b" * 40)
        self.assertEqual((session.head, session.report.head), ("b" * 40, self.head))
        self.assertTrue(session.original_review_context_ready())
        self.assertEqual(session.review_state((), (), pre_review_required=True), (True, False))
        self.assertEqual(session.local_triage[self.finding.id][0], False)
        self.assertEqual(session.accepted, {})
        with self.assertRaises(ValueError):
            session.observe_completed(self.observation)
        with self.assertRaises(ValueError):
            session.begin(Runtime("b" * 40, self.scope), "another-reviewer")

    def test_wrong_owner_head_role_scope_actions_and_nonterminal_block_without_side_effects(self):
        for changes in (
            {"owner": "coordinator"}, {"owner": ""}, {"task": ""},
            {"head": "b" * 40}, {"role": "general-purpose"},
            {"scope": frozenset({"unrelated"})},
            {"actions": frozenset({"read-candidate", "emit-report", "push"})},
            {"actions": frozenset({"emit-report"})},
            {"state": "running"}, {"state": "idle"}, {"state": "failed"},
        ):
            with self.subTest(changes=changes):
                session = self.session()
                with self.assertRaises(ValueError):
                    session.observe_completed(replace(self.observation, **changes))
                self.assertIsNone(session.lease)
                self.assertIsNone(session.report)
                self.assertIsNone(session.completed_observation)
                self.assertEqual(session.owners.records, {})

    def test_missing_reversed_invalid_and_expired_chronology_block(self):
        for changes in (
            {"started_at": None}, {"completed_at": None},
            {"completed_at": "not a native timestamp"},
            {"completed_at": "2026-10-09T10:04:18.945Z"},
            {"completed_at": "2026-10-09T11:05:18.946Z"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.session().observe_completed(replace(self.observation, **changes))
        report = self.session().observe_completed(replace(
            self.observation, completed_at="2026-10-09T11:05:18.945Z",
        ))
        self.assertEqual(report.completed_at, "2026-10-09T11:05:18.945Z")

    def test_unknown_invalid_excessive_paths_and_observed_runtime_counts_block(self):
        for changes in (
            {"observed_paths": None}, {"observed_paths": ()},
            {"observed_paths": ("../runtime.py",)}, {"observed_paths": ("/runtime.py",)},
            {"observed_paths": (self.path, self.path)},
            {"observed_paths": tuple(f"src/{index}.py" for index in range(201))},
            {"runtime_files": True}, {"runtime_files": 0}, {"runtime_files": 201},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.session().observe_completed(replace(self.observation, **changes))
        report = self.session().observe_completed(replace(self.observation, runtime_files=200))
        self.assertEqual(report.files, 200)
        self.assertEqual(len(report.observed_paths), 1)

    def test_missing_content_wrong_or_unobserved_findings_and_candidate_json_block(self):
        for changes in (
            {"original_content": ""}, {"original_content": None},
            {"findings": (replace(self.finding, origin="b" * 40),)},
            {"findings": (replace(self.finding, review_id="local:other-task"),)},
            {"findings": (replace(self.finding, source_path="unobserved.py"),)},
            {"findings": (self.finding, self.finding)},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.session().observe_completed(replace(self.observation, **changes))
        with self.assertRaises(ValueError):
            self.session().observe_completed({**asdict(self.observation), "passed": True})

    def test_ownership_overlap_and_observation_report_drift_block(self):
        owners = model.ReviewOwnership()
        active = model.ReviewSession(
            "coordinator", "implementer", self.scope, self.head,
            identity=("owner/repo", 280, self.head), owners=owners,
        )
        active.begin(Runtime(self.head, self.scope), "live-reviewer")
        imported = self.session(owners=owners)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            imported.observe_completed(self.observation)
        self.assertIsNone(imported.report)
        session = self.session()
        session.observe_completed(self.observation)
        session.completed_observation = replace(self.observation, owner="different-owner")
        self.assertFalse(session.original_review_context_ready())

    def test_every_imported_observation_and_report_field_is_bound_after_validation(self):
        changes = {
            "task": "other-task", "owner": "other-reviewer", "role": "general-purpose",
            "head": "b" * 40, "scope": frozenset({"other/subject"}), "state": "running",
            "actions": frozenset({"read-candidate", "read-evidence", "emit-report"}),
            "started_at": "2026-10-09T10:05:19.945Z",
            "completed_at": "2026-10-09T10:08:00.932Z",
            "observed_paths": ("other/source.py",), "findings": (),
            "original_content": "Changed report content", "runtime_files": 1,
        }
        report_names = {"scope": "subjects", "runtime_files": "files"}
        for name, value in changes.items():
            with self.subTest(representation="observation", field=name):
                session = self.session()
                session.observe_completed(self.observation)
                session.completed_observation = replace(self.observation, **{name: value})
                self.assertFalse(session.original_review_context_ready())
            if name != "state":
                with self.subTest(representation="report", field=name):
                    session = self.session()
                    report = session.observe_completed(self.observation)
                    session.report = replace(report, **{report_names.get(name, name): value})
                    self.assertFalse(session.original_review_context_ready())
        for name in ("completed", "read_only"):
            with self.subTest(representation="report", field=name):
                session = self.session()
                report = session.observe_completed(self.observation)
                session.report = replace(report, **{name: False})
                self.assertFalse(session.original_review_context_ready())

    def test_fresh_before_first_remote_and_accepted_findings_remain_required(self):
        session = self.session()
        session.observe_completed(self.observation)
        session.triage_local(self.finding.id, accepted=True, reason="Mapped for actual family evidence")
        self.assertEqual(session.accepted, {self.finding.id: self.finding})
        for submitted, valid in (
            ("2026-10-09T10:07:59.931Z", False),
            ("2026-10-09T10:07:59.932Z", False),
            ("2026-10-09T10:07:59.933Z", True),
        ):
            with self.subTest(submitted=submitted):
                observed = self.session()
                observed.observe_completed(self.observation)
                observed.triage_local(self.finding.id, accepted=False, reason="Actual rejected finding")
                fact = model.ReviewFact(
                    "remote", self.head, "copilot", "COMMENTED", submitted, "Complete content", (),
                )
                triage = model.Triage(fact, "clean")
                observed.triage(triage)
                if valid:
                    self.assertEqual(observed.review_state(
                        (fact,), (triage,), pre_review_required=True), (True, True))
                else:
                    with self.assertRaisesRegex(ValueError, "precede"):
                        observed.review_state((fact,), (triage,), pre_review_required=True)


if __name__ == "__main__":
    unittest.main()
