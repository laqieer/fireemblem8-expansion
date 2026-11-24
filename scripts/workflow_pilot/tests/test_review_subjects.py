"""Actual source, native/ARM and generated-data controls for review convergence."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import ast
import copy
from dataclasses import replace
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.modernize.tests.make_database import make_database_rule
from scripts.workflow_pilot.trusted_review_gate import GitTree, ReviewTools
from scripts.workflow_pilot.tests.review_support import ROOT, ENV, TRUSTED_LAUNCHER, git, request, snapshot


class SubjectTestCase(unittest.TestCase):
    maxDiff = None
    @classmethod
    def setUpClass(cls):
        cls.workspace = snapshot()
        cls.repo = cls.workspace.__enter__()
        cls.tools = ReviewTools(GitTree(cls.repo.root, cls.repo.base), cls.repo.root)
        cls.model = cls.tools.model
        cls.observed = {}

    @classmethod
    def tearDownClass(cls):
        cls.workspace.__exit__(None, None, None)

    def scope(self, kind, head=None):
        cases = {
            "aoe": ("TC-GAMEPLAY-006", "aoe-item-dispatch"),
            "generated": ("TC-CORE-004", "generated-eventlists"),
            "session": ("TC-WORKFLOW-REVIEW-FAMILY-001", "review-session"),
        }
        case, subject = cases[kind]
        return request(case, subject, self.repo.base, head or self.repo.base)

    def run_members(self, members, revision):
        key = revision, tuple(item.identity for item in members)
        if key not in self.observed:
            self.observed[key] = self.tools.run_obligations(members, revision)
        return self.observed[key]

    def assert_satisfied(self, observations):
        self.assertTrue(observations)
        bad = [(item.obligation.member, item.verdict, item.detail) for item in observations
               if item.verdict != "satisfied"]
        self.assertEqual(bad, [])
        self.assertTrue(all(item.checks > 0 for item in observations))


class AuthoredCaseFamilyTests(SubjectTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for name in (
            "docs/test-cases/template.md", "docs/test-cases/localization.md",
            "scripts/check_docs.py",
            "tools/gba-playtest/tests/test_locale_tester_cases.py",
        ):
            target = cls.repo.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        git(cls.repo.root, "add", ".")
        git(cls.repo.root, "commit", "-qm", "source-backed authored case procedure")
        cls.repo.base = git(cls.repo.root, "rev-parse", "HEAD")
        cls.tools = ReviewTools(GitTree(cls.repo.root, cls.repo.base), cls.repo.root)
        cls.model = cls.tools.model

    def case_scope(self, head=None):
        return request(
            "TC-LOCALIZATION-AUTHORED-SCOPE-001", "authored-scope-case",
            self.repo.base, head or self.repo.base)

    def test_retired_recursive_subject_cannot_execute_candidate_programs(self):
        selectors = [
            "authored-binding:" + role for role in self.model.FAMILIES["generated"]
        ]
        selectors.append("authored-binding:guide-destinations")
        selectors += [
            "authored-fixture:" + role for role in self.model.FAMILIES["wire"]
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build").mkdir()
            marker = root / "candidate-program-executed"
            (root / "build/review-subject-binding.py").write_text(
                f"with open({str(marker)!r}, 'wb') as output:\n"
                "    output.write(b'executed')\n"
            )
            previous = Path.cwd()
            try:
                os.chdir(root)
                observed = self.tools.subjects.worker(selectors)
            finally:
                os.chdir(previous)
            self.assertFalse(marker.exists(), "retired route executed a candidate program")
        self.assertEqual(
            [(item["kind"], item["verdict"], item["checks"]) for item in observed],
            [(None, "unavailable", 0)] * len(selectors),
        )
        scope = request(
            "TC-WORKFLOW-REVIEW-FAMILY-001", "authored-case-binding",
            self.repo.base, self.repo.base)
        with self.assertRaisesRegex(ValueError, "unknown subject"):
            self.tools.members(scope)

    def test_actual_case_consumer_rejects_missing_member_and_recovers(self):
        path = "tools/gba-playtest/tests/test_locale_tester_cases.py"
        original = (self.repo.root / path).read_text()
        broken = original.replace(
            '        "TC-LOCALIZATION-AUTHORED-SCOPE-001",\n', "", 1)
        self.assertNotEqual(broken, original)
        before = self.repo.commit({path: broken})
        after = self.repo.commit({path: original}, parent=before)
        members = self.tools.members(self.case_scope(after), (before,))
        self.assertEqual({item.role for item in members},
                         {"owners", "outputs", "consumers", "drift-checks"})
        self.assert_satisfied(self.run_members(members, after))
        observed = self.run_members(members, before)
        rejected = [item for item in observed if item.verdict != "satisfied"]
        self.assertEqual([(item.obligation.member, item.verdict) for item in rejected],
                         [("consumers:full-game-localization", "contract-violation")])
        self.assertGreater(rejected[0].checks, 0)
        consumer = next(item for item in members if item.role == "consumers")
        self.assertNotIn("docs/test-cases/localization.md", consumer.inputs)
        self.assertIn("docs/test-cases/localization.md", consumer.execution_inputs)

    def test_retained_headings_cannot_hide_an_empty_expected_result(self):
        path = "docs/test-cases/localization.md"
        original = (self.repo.root / path).read_text()
        prefix, procedure = original.split(
            "## TC-LOCALIZATION-AUTHORED-SCOPE-001:", 1)
        broken = re.sub(
            r"(?<=### Expected result\n).*?(?=### Negative control)",
            "\n", procedure, count=1, flags=re.S)
        self.assertNotEqual(procedure, broken)
        before = self.repo.commit({
            path: prefix + "## TC-LOCALIZATION-AUTHORED-SCOPE-001:" + broken,
        })
        after = self.repo.commit({path: original}, parent=before)
        members = self.tools.members(self.case_scope(after), (before,))
        self.assert_satisfied(self.run_members(members, after))
        observed = self.run_members(members, before)
        self.assertEqual(
            [(item.obligation.member, item.verdict) for item in observed
             if item.verdict != "satisfied"],
            [("outputs:full-game-localization", "contract-violation")])

    def test_indexed_procedure_drift_is_independent_of_guide_rendering(self):
        catalog = json.loads((self.repo.root / "docs/test-cases/registry.json").read_text())
        feature = next(row for row in catalog["features"] if row["id"] == "full-game-localization")
        for guide in ("", "`\n\n# unrelated block `\n", "[unused]: other.md\n"):
            with self.subTest(guide=guide):
                head = self.repo.commit({feature["reference"]: guide})
                members = self.tools.members(self.case_scope(head))
                self.assert_satisfied(self.run_members(members, head))
                drift = next(item for item in members if item.role == "drift-checks")
                self.assertNotIn(feature["reference"], drift.inputs)

    def test_annotated_literal_metadata_preserves_both_consumer_call_sites(self):
        path = "tools/gba-playtest/tests/test_locale_tester_cases.py"
        original = (self.repo.root / path).read_text()
        annotated = original.replace(
            "FEATURE_CASES = {", "FEATURE_CASES: dict[str, set[str]] = {", 1)
        self.assertNotEqual(original, annotated)
        head = self.repo.commit({path: annotated})
        self.assert_satisfied(self.run_members(self.tools.members(self.case_scope(head)), head))
        annotated_tool = ReviewTools(GitTree(self.repo.root, head), self.repo.root)
        self.assert_satisfied(annotated_tool.run_obligations(
            annotated_tool.members(self.case_scope()), self.repo.base))
        bad = self.repo.commit({
            path: annotated.replace('        "TC-LOCALIZATION-AUTHORED-SCOPE-001",\n', "", 1),
        })
        consumer = next(
            item for item in self.run_members(self.tools.members(self.case_scope(bad)), bad)
            if item.obligation.role == "consumers")
        self.assertEqual((consumer.verdict, consumer.checks), ("contract-violation", 1))

    def test_refactors_preserve_members_and_syntax_failure_is_not_semantic_evidence(self):
        path = "tools/gba-playtest/tests/test_locale_tester_cases.py"
        original = (self.repo.root / path).read_text()
        renamed = original.replace("FEATURE_CASES", "LOCALE_CONTRACTS")
        renamed = renamed.replace(
            'REGISTRY_PATH.read_text(encoding="utf-8")', "REGISTRY_PATH.read_bytes()")
        renamed = renamed.replace(
            'self.assertEqual(set(features[feature_id]["required_cases"]), expected_cases)',
            'self.assertEqual(expected_cases, set(features[feature_id]["required_cases"]))')
        self.assertNotEqual(original, renamed)
        head = self.repo.commit({path: renamed})
        members = self.tools.members(self.case_scope(head))
        self.assert_satisfied(self.run_members(members, head))

        malformed = self.repo.commit({
            path: renamed + "\n(\n",
            "scripts/check_docs.py": b"",
        })
        observed = self.run_members(
            self.tools.members(self.case_scope(malformed)), malformed)
        consumer = next(item for item in observed if item.obligation.role == "consumers")
        self.assertEqual((consumer.verdict, consumer.checks), ("unavailable", 0))
        self.assert_satisfied(tuple(item for item in observed
                                    if item.obligation.role != "consumers"))

    def test_unknown_canonical_case_members_block_the_finite_model(self):
        path = "docs/test-cases/registry.json"
        catalog = json.loads((self.repo.root / path).read_text())
        feature = next(item for item in catalog["features"]
                       if item["id"] == "full-game-localization")
        feature["required_cases"].append("TC-LOCALIZATION-UNREVIEWED-001")
        head = self.repo.commit({path: json.dumps(catalog)})
        with self.assertRaisesRegex(ValueError, "reviewed finite model"):
            self.tools.members(self.case_scope(head))

    def test_candidate_predicate_or_path_cannot_hide_captured_metadata_drift(self):
        path = "tools/gba-playtest/tests/test_locale_tester_cases.py"
        original = (self.repo.root / path).read_text()
        stale = original.replace(
            '        "TC-LOCALIZATION-AUTHORED-SCOPE-001",\n', "", 1)
        mutants = (
            stale.replace('set(features[feature_id]["required_cases"])',
                          'set({"required_cases": expected_cases}["required_cases"])', 1),
            stale.replace("        registry = json.loads(",
                          '        REGISTRY_PATH = ROOT / "missing.json"\n'
                          "        registry = json.loads(", 1),
            stale.replace('features[feature_id]["required_cases"]',
                          'features["expansion-locale-selection"]["required_cases"]', 1),
            stale.replace("        registry = json.loads(",
                          "        return\n        registry = json.loads(", 1),
            stale.replace("    def test_registry_owns_the_eight_stable_localization_cases(",
                          "    def not_collected_registry_cases(", 1),
            stale.replace("    def test_registry_owns_the_eight_stable_localization_cases(",
                          '    @unittest.skip("")\n'
                          "    def test_registry_owns_the_eight_stable_localization_cases(", 1),
        )
        for source in mutants:
            with self.subTest(disconnected=source == mutants[0]):
                self.assertNotEqual(source, stale)
                head = self.repo.commit({path: source})
                observed = self.run_members(self.tools.members(self.case_scope(head)), head)
                consumer = next(item for item in observed
                                if item.obligation.role == "consumers")
                self.assertEqual((consumer.verdict, consumer.checks),
                                 ("contract-violation", 1))

    def test_missing_sibling_or_automation_is_an_owner_violation_and_blocks_consumers(self):
        path = "docs/test-cases/registry.json"
        original = json.loads((self.repo.root / path).read_text())
        for missing in ("record", "automation"):
            with self.subTest(missing=missing):
                catalog = copy.deepcopy(original)
                sibling = next(item for item in catalog["cases"]
                               if item["id"] == "TC-LOCALIZATION-003")
                if missing == "record":
                    catalog["cases"].remove(sibling)
                else:
                    sibling.pop("automation")
                head = self.repo.commit({path: json.dumps(catalog)})
                observed = self.run_members(self.tools.members(self.case_scope(head)), head)
                owner = next(item for item in observed if item.obligation.role == "owners")
                self.assertEqual((owner.verdict, owner.checks), ("contract-violation", 1))
                consumer = next(item for item in observed
                                if item.obligation.role == "consumers")
                self.assertEqual((consumer.verdict, consumer.checks), ("unavailable", 0))
                self.assertEqual(consumer.blocked_by, ("owners:full-game-localization",))

    def test_unknown_consumer_feature_is_a_real_guard_violation(self):
        path = "tools/gba-playtest/tests/test_locale_tester_cases.py"
        source = (self.repo.root / path).read_text()
        unknown = source.replace("FEATURE_CASES = {\n",
                                 'FEATURE_CASES = {\n    "unknown-feature": set(),\n', 1)
        self.assertNotEqual(source, unknown)
        head = self.repo.commit({path: unknown})
        observed = self.run_members(self.tools.members(self.case_scope(head)), head)
        consumer = next(item for item in observed if item.obligation.role == "consumers")
        self.assertEqual((consumer.verdict, consumer.checks), ("contract-violation", 1))

    def test_consumer_executes_the_selected_reviewed_predicate_not_an_ast_imitation(self):
        path = "tools/gba-playtest/tests/test_locale_tester_cases.py"
        original = (self.repo.root / path).read_text()
        different_predicate = original.replace(
            'set(features[feature_id]["required_cases"])', "set()", 1)
        self.assertNotEqual(original, different_predicate)
        tool_revision = self.repo.commit({path: different_predicate})
        tools = ReviewTools(GitTree(self.repo.root, tool_revision), self.repo.root)
        observed = tools.run_obligations(
            tools.members(self.case_scope()), self.repo.base)
        consumer = next(item for item in observed if item.obligation.role == "consumers")
        self.assertEqual((consumer.verdict, consumer.checks), ("contract-violation", 1))

    def test_selected_consumer_local_variable_rename_preserves_binding(self):
        import ast

        path = "tools/gba-playtest/tests/test_locale_tester_cases.py"
        source = ast.parse((self.repo.root / path).read_text())

        class RenameLocal(ast.NodeTransformer):
            def visit_Name(self, node):
                if node.id == "features":
                    node.id = "registry_features"
                return node

        renamed = ast.unparse(RenameLocal().visit(source))
        tool_revision = self.repo.commit({path: renamed})
        tools = ReviewTools(GitTree(self.repo.root, tool_revision), self.repo.root)
        scope = self.case_scope()
        self.assert_satisfied(tools.run_obligations(tools.members(scope), self.repo.base))

    def test_existing_unrelated_anchor_does_not_bind_the_authored_procedure(self):
        path = "docs/test-cases/registry.json"
        original = (self.repo.root / path).read_text()
        catalog = json.loads(original)
        case = next(item for item in catalog["cases"]
                    if item["id"] == "TC-LOCALIZATION-AUTHORED-SCOPE-001")
        case["anchor"] = (
            "tc-localization-003-production-expansion-and-full-game-locale-catalogs")
        head = self.repo.commit({path: json.dumps(catalog)})
        after = self.repo.commit({path: original}, parent=head)
        members = self.tools.members(self.case_scope(after), (head,))
        self.assert_satisfied(self.run_members(members, after))
        observed = self.run_members(members, head)
        drift = next(item for item in observed if item.obligation.role == "drift-checks")
        self.assertEqual(drift.verdict, "contract-violation")

    def test_remapped_document_and_reference_paths_require_a_reviewed_model(self):
        path = "docs/test-cases/registry.json"
        original = json.loads((self.repo.root / path).read_text())
        for owner, field in (("cases", "document"), ("features", "reference")):
            with self.subTest(field=field):
                catalog = copy.deepcopy(original)
                identifier = ("TC-LOCALIZATION-AUTHORED-SCOPE-001"
                              if owner == "cases" else "full-game-localization")
                record = next(item for item in catalog[owner] if item["id"] == identifier)
                record[field] = "docs/test-cases/template.md"
                head = self.repo.commit({path: json.dumps(catalog)})
                with self.assertRaisesRegex(ValueError, "reviewed finite model"):
                    self.tools.members(self.case_scope(head))

class CIHistoryFamilyTests(SubjectTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        source = GitTree(ROOT, git(ROOT, "rev-parse", "HEAD"))
        for name in cls.tools.subjects.local_python_inputs(
                source, cls.tools.subjects.CI_HISTORY_SOURCES):
            target = cls.repo.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        git(cls.repo.root, "add", "scripts")
        git(cls.repo.root, "commit", "-qm", "source-backed CI history predicates")
        cls.repo.base = git(cls.repo.root, "rev-parse", "HEAD")
        cls.tools = ReviewTools(GitTree(cls.repo.root, cls.repo.base), cls.repo.root)
        cls.model = cls.tools.model

    def history_scope(self, head=None):
        return request("TC-WORKFLOW-BUILD-CI-SPLIT-001", "ci-lane-history",
                       self.repo.base, head or self.repo.base)

    def mutate_predicate(self, path, name, value):
        tree = ast.parse((self.repo.root / path).read_text())
        functions = [item for item in tree.body
                     if isinstance(item, ast.FunctionDef) and item.name == name]
        self.assertEqual(len(functions), 1)
        functions[0].body = [ast.Return(value=ast.Constant(value=value))]
        return self.repo.commit({path: ast.unparse(ast.fix_missing_locations(tree)) + "\n"})

    def wrap_predicate(self, path, name, wrapper):
        tree = ast.parse((self.repo.root / path).read_text())
        functions = [item for item in tree.body
                     if isinstance(item, ast.FunctionDef) and item.name == name]
        self.assertEqual(len(functions), 1)
        functions[0].name = "_original_" + name.lstrip("_")
        tree.body.extend(ast.parse(wrapper).body)
        return self.repo.commit({path: ast.unparse(ast.fix_missing_locations(tree)) + "\n"})

    def test_complete_source_backed_lifecycle_and_wire_family(self):
        members = self.tools.members(self.history_scope())
        self.assertEqual({(item.family, item.role) for item in members}, {
            (family, role) for family in ("lifecycle", "wire")
            for role in self.model.FAMILIES[family]
        })
        self.assertTrue(all(item.subject.endswith("/ci-lane-history") for item in members))
        observations = self.run_members(members, self.repo.base)
        self.assert_satisfied(observations)
        self.assertTrue(all(item.kind == "host" and item.checks > 1 for item in observations))

    def test_lane_and_stale_identity_mutations_fail_their_actual_members(self):
        path = "scripts/workflow_pilot/adaptive_gate.py"
        for value, role in (("concurrent", "consumers"), ("review-first", "stale-bindings")):
            with self.subTest(value=value, role=role):
                revision = self.mutate_predicate(path, "_history_lane", value)
                members = self.tools.members(self.history_scope(revision), (self.repo.base,))
                observations = self.run_members(members, revision)
                target = next(item for item in observations
                              if item.obligation.family == "wire" and item.obligation.role == role)
                self.assertEqual(target.verdict, "contract-violation", target.detail)

    def test_metadata_preparation_mutation_fails_its_lifecycle_member(self):
        path = "scripts/workflow_pilot/pr_metadata.py"
        revision = self.mutate_predicate(path, "_pre_full_preparation", False)
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        observations = self.run_members(members, revision)
        terminal = next(item for item in observations
                        if item.obligation.family == "lifecycle" and item.obligation.role == "terminals")
        self.assertEqual(terminal.verdict, "contract-violation", terminal.detail)

    def test_legacy_preflight_validator_reports_a_contract_violation(self):
        path = "scripts/workflow_pilot/candidate_evidence.py"
        tree = ast.parse((self.repo.root / path).read_text())
        function = next(item for item in tree.body
                        if isinstance(item, ast.FunctionDef) and item.name == "preflight_success")
        function.args.kwonlyargs = []
        function.args.kw_defaults = []
        function.body = [ast.Return(value=ast.Constant(value=False))]
        revision = self.repo.commit({path: ast.unparse(ast.fix_missing_locations(tree)) + "\n"})
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        observation = next(item for item in self.run_members(members, revision)
                           if item.obligation.family == "wire"
                           and item.obligation.role == "validators")
        self.assertEqual(observation.verdict, "contract-violation", observation.detail)

    def test_intake_consumer_mutation_fails_its_own_member(self):
        revision = self.mutate_predicate(
            "scripts/workflow_pilot/adaptive_gate.py", "_finalize_intake", None)
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        consumer = next(item for item in self.run_members(members, revision)
                        if item.obligation.family == "wire"
                        and item.obligation.role == "consumers")
        self.assertEqual(consumer.verdict, "contract-violation", consumer.detail)

    def test_classifier_mutation_is_not_attributed_to_unrelated_members(self):
        revision = self.mutate_predicate(
            "scripts/workflow_pilot/pr_metadata.py", "_run_mode", "review-first")
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        observations = self.run_members(members, revision)
        rejected = [item for item in observations if item.verdict != "satisfied"]
        self.assertEqual({(item.obligation.family, item.obligation.role, item.verdict)
                          for item in rejected}, {
            ("lifecycle", "entries", "contract-violation"),
            ("wire", "producers", "contract-violation"),
        })
        self.assert_satisfied([item for item in observations if item not in rejected])

    def test_classifier_rejects_metadata_and_single_new_worker_mutations(self):
        path = "scripts/workflow_pilot/pr_metadata.py"
        variants = (
            "    if 'metadata-classifier' in names and not names & new_workers:\n"
            "        return 'historical-full'\n",
            "    if 'review-first-classifier' in names and len(names & new_workers) == 1:\n"
            "        return 'review-first'\n",
        )
        for mutation in variants:
            with self.subTest(mutation=mutation):
                revision = self.wrap_predicate(path, "_run_mode",
                    "def _run_mode(jobs, *, run_id, status):\n"
                    "    names = {job.name for job in jobs}\n"
                    "    new_workers = {'workflow-pilot-tests', 'ownership-probe-tests', "
                    "'game-localization-tests'}\n"
                    + mutation
                    + "    return _original_run_mode(jobs, run_id=run_id, status=status)\n")
                members = self.tools.members(self.history_scope(revision), (self.repo.base,))
                producer = next(item for item in self.run_members(members, revision)
                                if item.obligation.family == "wire"
                                and item.obligation.role == "producers")
                self.assertEqual(producer.verdict, "contract-violation", producer.detail)

    def test_lane_consumer_rejects_malformed_history_with_valid_retry(self):
        path = "scripts/workflow_pilot/adaptive_gate.py"
        revision = self.wrap_predicate(path, "_history_lane",
            "def _history_lane(pr, base, runs):\n"
            "    normalized = []\n"
            "    for run in runs:\n"
            "        if run.mode == 'historical-review-first':\n"
            "            jobs = tuple(SimpleNamespace(**{**vars(job), 'conclusion': 'success'})\n"
            "                         if job.name == 'event-identity' and job.conclusion == 'failure'\n"
            "                         else job for job in run.jobs)\n"
            "            run = SimpleNamespace(**{**vars(run), 'jobs': jobs})\n"
            "        normalized.append(run)\n"
            "    return _original_history_lane(pr, base, tuple(normalized))\n")
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        validator = next(item for item in self.run_members(members, revision)
                         if item.obligation.family == "wire"
                         and item.obligation.role == "validators")
        self.assertEqual(validator.verdict, "contract-violation", validator.detail)

    def test_lane_consumer_rejects_malformed_failed_attempt_with_valid_retry(self):
        path = "scripts/workflow_pilot/adaptive_gate.py"
        revision = self.wrap_predicate(path, "_history_lane",
            "def _history_lane(pr, base, runs):\n"
            "    normalized = []\n"
            "    for run in runs:\n"
            "        if any(job.name in {'host-tests', 'build'} and "
            "job.conclusion in {'failure', 'cancelled', 'timed_out'} for job in run.jobs):\n"
            "            jobs = tuple(replace(job, conclusion='success') "
            "if job.name == 'event-identity' and job.conclusion == 'failure' "
            "else job for job in run.jobs)\n"
            "            run = replace(run, jobs=jobs)\n"
            "        normalized.append(run)\n"
            "    return _original_history_lane(pr, base, tuple(normalized))\n")
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        validator = next(item for item in self.run_members(members, revision)
                         if item.obligation.probe == "ci-history:wire:validators")
        self.assertEqual(validator.verdict, "contract-violation", validator.detail)

    def test_history_fixture_uses_the_parsed_pull_request_type(self):
        path = "scripts/workflow_pilot/adaptive_gate.py"
        revision = self.wrap_predicate(path, "_history_lane",
            "def _history_lane(pr, base, runs):\n"
            "    from scripts.workflow_pilot import pr_metadata\n"
            "    if not isinstance(pr, pr_metadata.PullRequestState):\n"
            "        raise ValueError('pull request is not parsed')\n"
            "    return _original_history_lane(pr, base, runs)\n")
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        self.assert_satisfied(self.run_members(members, revision))

    def test_history_fixture_allows_advanced_live_base(self):
        path = "scripts/workflow_pilot/adaptive_gate.py"
        revision = self.wrap_predicate(path, "_history_lane",
            "def _history_lane(pr, base, runs):\n"
            "    if getattr(pr, 'base_sha', base) != base:\n"
            "        raise ValueError('live base differs from frozen base')\n"
            "    return _original_history_lane(pr, base, runs)\n")
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        stale = next(item for item in self.run_members(members, revision)
                     if item.obligation.probe == "ci-history:wire:stale-bindings")
        self.assertEqual(stale.verdict, "contract-violation", stale.detail)

    def test_history_fixtures_follow_workflow_job_dependencies(self):
        path = "scripts/workflow_pilot/adaptive_gate.py"
        revision = self.wrap_predicate(path, "_history_lane",
            "def _history_lane(pr, base, runs):\n"
            "    for run in runs:\n"
            "        jobs = {job.name: job for job in run.jobs}\n"
            "        classifier = next(name for name in jobs if name.endswith('-classifier'))\n"
            "        for name, job in jobs.items():\n"
            "            parents = (('event-identity',) if name == 'event-router' else\n"
            "                       ('event-identity', 'event-router') if name == classifier else\n"
            "                       tuple(other for other in jobs if other != 'summary') "
            "if name == 'summary' else\n"
            "                       () if name == 'event-identity' else "
            "('event-identity', classifier))\n"
            "            if any(job.started_at < jobs[parent].completed_at for parent in parents):\n"
            "                raise ValueError('job starts before a dependency completed')\n"
            "    return _original_history_lane(pr, base, runs)\n")
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        self.assert_satisfied(self.run_members(members, revision))

        revision = self.wrap_predicate(path, "_history_lane",
            "def _history_lane(pr, base, runs):\n"
            "    for run in runs:\n"
            "        jobs = {job.name: job for job in run.jobs}\n"
            "        if jobs['event-router'].started_at >= jobs['event-identity'].completed_at:\n"
            "            raise ValueError('job ordering reversed')\n"
            "    return _original_history_lane(pr, base, runs)\n")
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        entry = next(item for item in self.run_members(members, revision)
                     if item.obligation.probe == "ci-history:lifecycle:entries")
        self.assertEqual(entry.verdict, "contract-violation", entry.detail)

    def test_run_fixtures_match_parsed_job_types_and_timing(self):
        path = "scripts/workflow_pilot/adaptive_gate.py"
        revision = self.wrap_predicate(path, "_history_lane",
            "def _history_lane(pr, base, runs):\n"
            "    from scripts.workflow_pilot import pr_metadata\n"
            "    for run in runs:\n"
            "        if not isinstance(run, pr_metadata.RunState):\n"
            "            raise ValueError('run is not parsed')\n"
            "        for job in run.jobs:\n"
            "            if not isinstance(job, pr_metadata.JobState):\n"
            "                raise ValueError('job is not parsed')\n"
            "            pr_metadata._validate_job_timing(\n"
            "                job_id=job.job_id, status=job.status, conclusion=job.conclusion,\n"
            "                assigned=job.runner_name is not None, created_at=job.created_at,\n"
            "                started_at=job.started_at, completed_at=job.completed_at,\n"
            "                run_created_at=run.created_at, run_started_at=run.run_started_at,\n"
            "                run_updated_at=run.updated_at)\n"
            "    return _original_history_lane(pr, base, runs)\n")
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        self.assert_satisfied(self.run_members(members, revision))

        revision = self.wrap_predicate(path, "_history_lane",
            "def _history_lane(pr, base, runs):\n"
            "    for run in runs:\n"
            "        for job in run.jobs:\n"
            "            if job.started_at is not None:\n"
            "                job.started_at.endswith('Z')\n"
            "    return _original_history_lane(pr, base, runs)\n")
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        entry = next(item for item in self.run_members(members, revision)
                     if item.obligation.family == "lifecycle"
                     and item.obligation.role == "entries")
        self.assertNotEqual(entry.verdict, "satisfied", entry.detail)

    def test_reservation_follows_the_completed_preflight(self):
        path = "scripts/workflow_pilot/adaptive_gate.py"
        revision = self.wrap_predicate(path, "_reserved_dispatch",
            "def _reserved_dispatch(record, pr, run, runs):\n"
            "    requested = reporter.parse_time(record['dispatch_requested_at'], 'dispatch')\n"
            "    for prior in runs:\n"
            "        if prior.mode in {'review-first', 'historical-review-first'} "
            "and prior.run_number <= record['watermark'] and prior.updated_at > requested:\n"
            "            raise ValueError('preflight completed after reservation')\n"
            "    return _original_reserved_dispatch(record, pr, run, runs)\n")
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        self.assert_satisfied(self.run_members(members, revision))

    def test_historical_validator_rejects_malformed_preflights(self):
        path = "scripts/workflow_pilot/candidate_evidence.py"
        tree = ast.parse((self.repo.root / path).read_text())
        function = next(item for item in tree.body
                        if isinstance(item, ast.FunctionDef) and item.name == "preflight_success")
        function.body.insert(0, ast.parse("if historical:\n    return True\n").body[0])
        revision = self.repo.commit({path: ast.unparse(ast.fix_missing_locations(tree)) + "\n"})
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        validator = next(item for item in self.run_members(members, revision)
                         if item.obligation.family == "wire"
                         and item.obligation.role == "validators")
        self.assertEqual(validator.verdict, "contract-violation", validator.detail)

    def test_dispatch_identity_and_attempt_pins_are_exercised(self):
        path = "scripts/workflow_pilot/adaptive_gate.py"
        for field in ("full_run_id", "full_attempt"):
            with self.subTest(field=field):
                tree = ast.parse((self.repo.root / path).read_text())
                original = next(item for item in tree.body
                                if isinstance(item, ast.FunctionDef)
                                and item.name == "_reserved_dispatch")
                original.name = "_original_reserved_dispatch"
                tree.body.extend(ast.parse(
                    "def _reserved_dispatch(record, pr, run, runs):\n"
                    f"    return _original_reserved_dispatch({{**record, {field!r}: None}}, "
                    "pr, run, runs)\n").body)
                revision = self.repo.commit({
                    path: ast.unparse(ast.fix_missing_locations(tree)) + "\n"})
                members = self.tools.members(self.history_scope(revision), (self.repo.base,))
                stale = next(item for item in self.run_members(members, revision)
                             if item.obligation.family == "wire"
                             and item.obligation.role == "stale-bindings")
                self.assertEqual(stale.verdict, "contract-violation", stale.detail)

    def test_fixture_runs_do_not_repeat_a_run_attempt(self):
        path = "scripts/workflow_pilot/adaptive_gate.py"
        tree = ast.parse((self.repo.root / path).read_text())
        original = next(item for item in tree.body
                        if isinstance(item, ast.FunctionDef) and item.name == "_history_lane")
        original.name = "_original_history_lane"
        tree.body.extend(ast.parse(
            "def _history_lane(pr, base, runs):\n"
            "    identities = [(run.run_id, run.run_attempt) for run in runs]\n"
            "    if len(identities) != len(set(identities)):\n"
            "        raise ValueError('duplicate run attempt')\n"
            "    return _original_history_lane(pr, base, runs)\n").body)
        revision = self.repo.commit({path: ast.unparse(ast.fix_missing_locations(tree)) + "\n"})
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        self.assert_satisfied(self.run_members(members, revision))

    def test_keyword_accepting_validator_signature_preserves_behavior(self):
        path = "scripts/workflow_pilot/candidate_evidence.py"
        tree = ast.parse((self.repo.root / path).read_text())
        function = next(item for item in tree.body
                        if isinstance(item, ast.FunctionDef) and item.name == "preflight_success")
        function.args.kwonlyargs = []
        function.args.kw_defaults = []
        function.args.kwarg = ast.arg(arg="options")
        function.body.insert(0, ast.parse("historical = options.get('historical', False)").body[0])
        revision = self.repo.commit({path: ast.unparse(ast.fix_missing_locations(tree)) + "\n"})
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        self.assert_satisfied(self.run_members(members, revision))

    def test_candidate_package_imports_are_not_replaced_by_trusted_tools(self):
        for path in ("scripts/workflow_pilot/__init__.py",
                     "scripts/workflow_pilot/raw_diff_check.py"):
            with self.subTest(path=path):
                revision = self.repo.commit({path: "raise RuntimeError('candidate import reached')\n"})
                members = self.tools.members(self.history_scope(revision), (self.repo.base,))
                observations = self.run_members(members, revision)
                self.assertTrue(all(item.verdict == "unavailable" for item in observations))
                self.assertTrue(all("candidate import reached" in item.detail for item in observations))

    def test_equivalent_candidate_package_initializer_is_accepted(self):
        path = "scripts/workflow_pilot/__init__.py"
        source = (self.repo.root / path).read_text()
        revision = self.repo.commit({path: source + "\n# Equivalent package initialization.\n"})
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        self.assert_satisfied(self.run_members(members, revision))

    def test_mixed_subject_workers_preserve_candidate_and_tool_sources(self):
        paths = (
            "scripts/workflow_pilot/__init__.py",
            "scripts/workflow_pilot/raw_diff_check.py",
            "scripts/workflow_pilot/review_family.py",
        )
        changes = {path: (self.repo.root / path).read_text() + "\n# Equivalent candidate.\n"
                   for path in paths}
        revision = self.repo.commit(changes)
        scope = self.history_scope(revision)
        scope["subjects"].extend(self.scope("session")["subjects"])
        members = self.tools.members(scope, (self.repo.base,))
        tree = self.tools.tree(revision)
        with tempfile.TemporaryDirectory(dir=self.repo.root / "build") as directory:
            root = Path(directory)
            self.tools._stage(tree, root, members)
            for path in members[0].source_inputs:
                self.assertEqual((root / path).read_bytes(), tree.read(path), path)
            for path in paths:
                self.assertEqual((root / "build/ci-history-tools" / path).read_bytes(),
                                 self.tools.tool_tree.read(path))
                self.assertNotEqual(tree.read(path), self.tools.tool_tree.read(path))
        self.assert_satisfied(self.run_members(members, revision))

    def test_mixed_session_worker_executes_candidate_dependency(self):
        raw_path = "scripts/workflow_pilot/raw_diff_check.py"
        family_path = "scripts/workflow_pilot/review_family.py"
        raw_source = (self.repo.root / raw_path).read_text()
        family_source = (self.repo.root / family_path).read_text()
        raw = raw_source.replace(
            'SHA_RE = re.compile(r"^[0-9a-f]{40}$")',
            'SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|HEAD)$")', 1)
        family = family_source.replace(
            'SHA_PATTERN = r"[0-9a-f]{40}"',
            'from scripts.workflow_pilot.raw_diff_check import SHA_RE\nSHA_PATTERN = SHA_RE.pattern', 1)
        self.assertNotEqual(raw, raw_source)
        self.assertNotEqual(family, family_source)
        revision = self.repo.commit({raw_path: raw, family_path: family})
        scope = self.history_scope(revision)
        scope["subjects"].extend(self.scope("session")["subjects"])
        members = self.tools.members(scope, (self.repo.base,))
        validator = next(item for item in self.run_members(members, revision)
                         if item.obligation.subject.endswith("/review-session")
                         and item.obligation.family == "wire"
                         and item.obligation.role == "validators")
        self.assertEqual(validator.verdict, "contract-violation", validator.detail)

    def test_mixed_authored_worker_preserves_candidate_docs_dependency(self):
        fixture_paths = (
            "docs/test-cases/template.md", "docs/test-cases/localization.md",
            "scripts/check_docs.py", "tools/gba-playtest/tests/test_locale_tester_cases.py",
        )
        tool_revision = self.repo.commit({
            path: (ROOT / path).read_bytes() for path in fixture_paths
        })
        family_path = "scripts/workflow_pilot/review_family.py"
        checker_path = "scripts/check_docs.py"
        family = (self.repo.root / family_path).read_text()
        marker = 'SHA_PATTERN = r"[0-9a-f]{40}"'
        self.assertIn(marker, family)
        safe = family.replace(marker, "from scripts import check_docs\n" + marker, 1)
        broken = family.replace(
            marker,
            "from scripts import check_docs\n"
            'SHA_PATTERN = (r"[0-9a-f]{40}" if '
            'check_docs.TEST_CASE_FEATURE_ID_RE.fullmatch("review-project-source") '
            'else r"(?:[0-9a-f]{40}|HEAD)")', 1)
        base = self.repo.commit({family_path: safe}, parent=tool_revision)
        checker = (ROOT / checker_path).read_text()
        head = self.repo.commit({
            family_path: broken,
            checker_path: checker + '\nTEST_CASE_FEATURE_ID_RE = re.compile(r"^[a-z]+$")\n',
        }, parent=base)
        tools = ReviewTools(GitTree(self.repo.root, tool_revision), self.repo.root)
        scope = request("TC-WORKFLOW-BUILD-CI-SPLIT-001", "ci-lane-history", base, head)
        scope["subjects"].extend(request(
            "TC-LOCALIZATION-AUTHORED-SCOPE-001", "authored-scope-case")["subjects"])
        scope["subjects"].extend(request()["subjects"])
        members = tools.members(scope, (base,))
        probes = {
            "ci-history:wire:consumers", "authored-case:outputs", "wire:validators",
        }
        observations = tools.run_obligations(members, head)
        self.assertEqual({item.obligation.probe: item.verdict for item in observations
                          if item.obligation.probe in probes}, {
            "ci-history:wire:consumers": "satisfied",
            "authored-case:outputs": "satisfied",
            "wire:validators": "contract-violation",
        }, [(item.obligation.probe, item.detail) for item in observations])

    def test_semantics_preserving_python_reformat_keeps_the_family_green(self):
        path = "scripts/workflow_pilot/adaptive_gate.py"
        source = (self.repo.root / path).read_text()
        reformatted = ast.unparse(ast.parse(source)) + "\n"
        revision = self.repo.commit({path: reformatted})
        members = self.tools.members(self.history_scope(revision), (self.repo.base,))
        self.assert_satisfied(self.run_members(members, revision))


class StagingFamilyTests(SubjectTestCase):
    gate_path = "scripts/workflow_pilot/trusted_review_gate.py"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        source = GitTree(ROOT, git(ROOT, "rev-parse", "HEAD"))
        for name in cls.tools.subjects.local_python_inputs(source, (cls.gate_path,)):
            target = cls.repo.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        git(cls.repo.root, "add", "scripts")
        git(cls.repo.root, "commit", "-qm", "source-backed staging predicates")
        cls.repo.base = git(cls.repo.root, "rev-parse", "HEAD")
        cls.tools = ReviewTools(GitTree(cls.repo.root, cls.repo.base), cls.repo.root)
        cls.model = cls.tools.model

    def staging_scope(self, head=None):
        return request("TC-WORKFLOW-REVIEW-FAMILY-001", "review-staging",
                       self.repo.base, head or self.repo.base)

    def mutate_gate(self, transform):
        tree = ast.parse((self.repo.root / self.gate_path).read_text())
        transform(tree)
        return self.repo.commit({self.gate_path: ast.unparse(ast.fix_missing_locations(tree)) + "\n"})

    def staging_observations(self, revision):
        members = self.tools.members(self.staging_scope(revision), (self.repo.base,))
        return self.run_members(members, revision)

    def test_complete_staging_family_executes_bound_source(self):
        members = self.tools.members(self.staging_scope())
        self.assertEqual({(item.family, item.role) for item in members}, {
            (family, role) for family in ("lifecycle", "wire")
            for role in self.model.FAMILIES[family]
        })
        observations = self.run_members(members, self.repo.base)
        self.assert_satisfied(observations)
        self.assertTrue(all(item.kind == "host" and item.checks > 1 for item in observations))
        self.assertTrue(all(
            self.gate_path in dict(item.source_objects) for item in observations))

    def test_old_keyword_call_breaks_the_existing_wrapper_member(self):
        def restore_regression(tree):
            classes = [item for item in tree.body
                       if isinstance(item, ast.ClassDef) and item.name == "ReviewTools"]
            self.assertEqual(len(classes), 1)
            method = next(item for item in classes[0].body
                          if isinstance(item, ast.FunctionDef) and item.name == "run_obligations")
            matches = []
            for node in ast.walk(method):
                if (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                        and node.test.id == "_isolate_candidate" and node.orelse
                        and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Call)
                        and isinstance(node.body[0].value.func, ast.Attribute)
                        and node.body[0].value.func.attr == "_stage"):
                    matches.append(node)
            self.assertEqual(len(matches), 1)
            node = matches[0]
            node.test = ast.Constant(value=True)
            node.body[0].value.keywords[0].value = ast.Name(
                id="isolate_candidate", ctx=ast.Load())
            node.orelse = []

        revision = self.mutate_gate(restore_regression)
        observation = next(item for item in self.staging_observations(revision)
                           if item.obligation.family == "lifecycle"
                           and item.obligation.role == "entries")
        self.assertEqual(observation.verdict, "contract-violation", observation.detail)

    def test_unconfirmed_cleanup_mutation_fails_its_own_member(self):
        def falsely_confirm_cleanup(tree):
            cls = next(item for item in tree.body
                       if isinstance(item, ast.ClassDef) and item.name == "ReviewTools")
            method = next(item for item in cls.body
                          if isinstance(item, ast.FunctionDef) and item.name == "run_obligations")
            matches = [item for item in ast.walk(method)
                       if isinstance(item, ast.Assign) and len(item.targets) == 1
                       and isinstance(item.targets[0], ast.Name)
                       and item.targets[0].id == "cleanup"
                       and isinstance(item.value, ast.Constant) and item.value.value is False]
            self.assertEqual(len(matches), 1)
            matches[0].value = ast.Constant(value=True)

        revision = self.mutate_gate(falsely_confirm_cleanup)
        observation = next(item for item in self.staging_observations(revision)
                           if item.obligation.family == "lifecycle"
                           and item.obligation.role == "preservation")
        self.assertEqual(observation.verdict, "contract-violation", observation.detail)

    def test_mixed_worker_isolation_mutation_fails_its_consumer(self):
        def drop_isolation(tree):
            matches = [item for item in ast.walk(tree)
                       if isinstance(item, ast.keyword) and item.arg == "_isolate_candidate"
                       and isinstance(item.value, ast.Constant) and item.value.value is True]
            self.assertEqual(len(matches), 1)
            matches[0].value = ast.Constant(value=False)

        revision = self.mutate_gate(drop_isolation)
        observation = next(item for item in self.staging_observations(revision)
                           if item.obligation.family == "wire"
                           and item.obligation.role == "consumers")
        self.assertEqual(observation.verdict, "contract-violation", observation.detail)

    def test_equivalent_gate_reformat_keeps_the_staging_family_green(self):
        source = (self.repo.root / self.gate_path).read_text()
        revision = self.repo.commit({self.gate_path: ast.unparse(ast.parse(source)) + "\n"})
        self.assert_satisfied(self.staging_observations(revision))

    def test_candidate_package_failure_is_not_masked_by_the_probe_tools(self):
        revision = self.repo.commit({
            "scripts/workflow_pilot/__init__.py":
                "raise RuntimeError('candidate package import reached')\n",
        })
        observations = self.staging_observations(revision)
        self.assertTrue(all(item.verdict == "unavailable" and item.checks == 0
                            and "candidate package import reached" in item.detail
                            for item in observations))


class SubjectTests(SubjectTestCase):
    @staticmethod
    def host_members(members):
        return tuple(item for item in members if not item.probe.startswith("aoe-arm:"))

    def test_two_unrelated_production_subjects_and_all_five_families(self):
        families = set()
        for kind in ("aoe", "generated", "session"):
            with self.subTest(subject=kind):
                members = self.tools.members(self.scope(kind))
                observations = self.run_members(self.host_members(members), self.repo.base)
                self.assert_satisfied(observations)
                families.update(item.obligation.family for item in observations)
                self.assertEqual({item.kind for item in observations},
                                 {"native"} if kind == "aoe"
                                 else {"parsed"} if kind == "generated" else {"host"})
        self.assertEqual(families, set(self.model.FAMILIES))

    def test_missing_arm_compiler_is_unavailable_and_cannot_admit_handoff(self):
        missing = self.repo.root / "build" / "uninstalled-arm-none-eabi-gcc"
        self.assertFalse(missing.exists())
        tools = ReviewTools(GitTree(self.repo.root, self.repo.base), self.repo.root,
                            arm_tools={"MODERN_CC": str(missing)})
        data = self.scope("aoe")
        members = tools.members(data)
        observations = tools.run_obligations(members, self.repo.base)
        native = tuple(item for item in observations if item.kind == "native")
        self.assert_satisfied(native)
        self.assertEqual(len(native), len(self.host_members(members)))
        arm = tuple(item for item in observations if item.kind == "arm-object")
        self.assertEqual(
            {(item.obligation.member, item.verdict, item.checks) for item in arm},
            {("enabled:objects", "unavailable", 0), ("disabled:objects", "unavailable", 0)})
        for item in arm:
            self.assertIn("FileNotFoundError", item.detail)
            self.assertIn(str(missing), item.detail)
        model = tools.model
        session = model.ReviewSession(
            "coordinator", "implementer",
            frozenset({model.subject_key(data["subjects"][0])}), self.repo.base,
            identity=("owner/repo", 1, self.repo.base))
        for evidence, error in (
            (observations, "candidate obligation failed"),
            (native, "missing or wrong member evidence"),
        ):
            with self.subTest(evidence_count=len(evidence)):
                with self.assertRaisesRegex(ValueError, error):
                    model.assess_handoff(
                        data, members, evidence, session, tool_revision=self.repo.base,
                        remote_reviews=(), triage=(), pre_review_required=False)

    def test_modern_linker_recipe_runs_mandatory_arm_positives(self):
        toolchain = self.repo.root / "uninstalled-arm-toolchain"
        self.assertFalse(toolchain.exists())
        for config in ("debug", "release"):
            with self.subTest(config=config):
                completed = subprocess.run(
                    ["make", "--no-print-directory", "-rR", "-n", "-p",
                     "MODERN_CONFIG=" + config, "MODERN_ABI=aapcs",
                     "MODERN_TOOLCHAIN_ROOT=" + str(toolchain),
                     "__issue179_review_profile_probe__"],
                    cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=60)
                self.assertNotEqual(completed.returncode, 0)
                rule = make_database_rule(completed.stdout, "expansion-modern-linker-check")
                self.assertIsNotNone(rule, completed.stderr)
                self.assertIn("expansion-modern-review-arm-check", shlex.split(rule.splitlines()[0]))
                arm_rule = make_database_rule(completed.stdout, "expansion-modern-review-arm-check")
                self.assertIsNotNone(arm_rule, completed.stderr)
                commands = [shlex.split(line.lstrip("\t").lstrip("@+"))
                            for line in arm_rule.replace("\\\n", " ").splitlines()
                            if line.startswith("\t")]
                self.assertIn(
                    ["MODERN_CC=$(MODERN_CC)", "MODERN_NM=$(MODERN_NM)",
                     "MODERN_SIZE=$(MODERN_SIZE)", "$(PYTHON)", "-m", "unittest",
                     "scripts.workflow_pilot.tests.arm_review_subjects", "-v"],
                    commands)
                if config == "debug":
                    commands = [
                        shlex.split(line.lstrip("\t").lstrip("@+"))
                        for line in rule.replace("\\\n", " ").splitlines()
                        if line.startswith("\t")
                    ]
                    publication = [
                        command for command in commands
                        if "scripts.modernize.tests.test_expansion_config.ProjectSelectionIdentityTests."
                        "test_native_parallel_capture_reads_only_complete_dependency_products" in command
                    ]
                    self.assertEqual(len(publication), 1)
                    self.assertEqual(publication[0][0], "env")
                    self.assertIn(
                        ("-u", "GBA_PLAYTEST_HOST_ONLY"),
                        tuple(zip(publication[0], publication[0][1:])),
                    )

    def test_ci_keeps_both_arm_review_profiles_in_existing_legacy_headroom(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()

        def commands(job):
            block = re.search(
                rf"^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z][A-Za-z0-9_-]*:\s*$|\Z)",
                workflow, re.M | re.S,
            )
            self.assertIsNotNone(block)
            scripts = re.findall(
                r"^      run: (?:\|\n((?:        [^\n]*\n)*)|([^\n]+)\n)",
                block.group(1), re.M,
            )
            return [
                shlex.split(line.strip())
                for multiline, single in scripts
                for line in (multiline or single).splitlines()
                if line.strip().startswith("make ")
            ]

        helper = "expansion-modern-review-arm-check"
        legacy = [argv for argv in commands("legacy") if helper in argv]
        self.assertEqual(len(legacy), 2)
        self.assertEqual(
            {next(word for word in argv if word.startswith("MODERN_CONFIG=")) for argv in legacy},
            {"MODERN_CONFIG=debug", "MODERN_CONFIG=release"},
        )
        for argv in legacy:
            self.assertFalse(any(
                word in {"-n", "--just-print", "--dry-run", "--recon",
                         "-q", "--question", "-t", "--touch"}
                or word.startswith(("-o", "--old-file", "--assume-old"))
                for word in argv[1:]
            ), argv)
        modern = [
            argv for argv in commands("build") if "expansion-modern-linker-check" in argv
        ]
        self.assertEqual(len(modern), 2)
        self.assertEqual(
            {next(word for word in argv if word.startswith("MODERN_CONFIG=")) for argv in modern},
            {"MODERN_CONFIG=debug", "MODERN_CONFIG=release"},
        )
        for argv in modern:
            self.assertIn("-o", argv)
            self.assertEqual(argv[argv.index("-o") + 1], helper)

    def remediation(self, kind, path, broken, member, *, fixed=None):
        original = (self.repo.root / path).read_bytes()
        before = self.repo.commit({path: broken})
        after = self.repo.commit({path: original if fixed is None else fixed}, parent=before)
        data = self.scope(kind, after)
        members = tuple(item for item in self.tools.members(data, (before,))
                        if item.family == ("action" if kind == "aoe" else "generated"))
        prior = self.run_members(members, before)
        current = self.run_members(members, after)
        self.assert_satisfied(current)
        relevant = [item for item in prior if item.obligation.member == member]
        self.assertEqual(len(relevant), 1)
        self.assertEqual(relevant[0].verdict, "contract-violation", relevant[0].detail)
        self.assertEqual(relevant[0].kind, relevant[0].obligation.kind)
        self.assertGreater(relevant[0].checks, 0)
        model = self.model
        key = model.subject_key(data["subjects"][0])
        session = model.ReviewSession("coordinator", "implementer", frozenset({key}), after,
                                      identity=("owner/repo", 1, self.repo.base))
        finding = model.Finding("actual-finding", key, members[0].family, member,
                                before, path, "review-1")
        session.accept(finding)
        data["findings"] = [{
            "finding_id": finding.id, **data["subjects"][0],
            "family": finding.family, "reported_member": member,
        }]
        report = model.assess_handoff(
            data, members, (*prior, *current), session, tool_revision=self.repo.base,
            remote_reviews=(), triage=(), pre_review_required=False)
        self.assertTrue(report["handoff_eligible"])
        self.assertFalse(report["exact_head_review_clean"])
        self.assertFalse(report["merge_permission"])
        self.assertTrue(any(row["outcome"] == "affected-fixed" for row in report["outcomes"]))
        return data, members, prior, current, session

    def test_real_aoe_before_after_missing_and_unrelated_evidence(self):
        path = "src/expansion_aoe.c"
        source = (self.repo.root / path).read_text()
        broken = source.replace("&& route->aiPolicy == EXPANSION_AOE_AI_NEVER", "&& 0")
        self.assertNotEqual(source, broken)
        data, members, prior, current, session = self.remediation(
            "aoe", path, broken, "actions:AI_SELECT")
        kwargs = dict(tool_revision=self.repo.base, remote_reviews=(), triage=(),
                      pre_review_required=False)
        for observations in (
            (*prior, *current[1:]),
            (*prior, *current, current[0]),
            (*current, *(replace(item, revision=prior[0].revision) for item in current)),
            (*prior, *(replace(item, tool_revision="f" * 40) for item in current)),
            (*prior, *(replace(item, revision="e" * 40) for item in current)),
        ):
            with self.assertRaises(ValueError):
                self.model.assess_handoff(data, members, observations, session, **kwargs)
        wrong = copy.deepcopy(data)
        wrong["findings"][0]["reported_member"] = "targets:unknown"
        with self.assertRaises(ValueError):
            self.model.assess_handoff(wrong, members, (*prior, *current), session, **kwargs)

    def test_cross_same_count_wrong_geometry_fails_its_selected_member(self):
        path = "src/expansion_aoe.c"
        source = (self.repo.root / path).read_text()
        broken = source.replace(
            """    case EXPANSION_AOE_SHAPE_CROSS:
        if (dx != 0 && dy != 0)
            return -1;

        return dx + dy;""",
            """    case EXPANSION_AOE_SHAPE_CROSS:
        return 2 * (dx > dy ? dx : dy);""")
        self.assertNotEqual(source, broken)
        self.remediation("aoe", path, broken, "targets:CROSS")

    def test_real_generated_owner_before_after_missing_sibling(self):
        path = "src/data/ch2_eventlists.json"
        data = json.loads((self.repo.root / path).read_text())
        data["lists"][0]["entries"][0]["args"][1] = "EventScr_UnknownReviewReference"
        arguments = self.remediation("generated", path, json.dumps(data), "owners:eventlists")
        request_data, members, prior, current, session = arguments
        missing = tuple(item for item in current if item.obligation.member != "owners:shops")
        with self.assertRaises(ValueError):
            self.model.assess_handoff(
                request_data, members, (*prior, *missing), session, tool_revision=self.repo.base,
                remote_reviews=(), triage=(), pre_review_required=False)

    def test_invalid_generated_source_cannot_claim_drift_or_consumer_repairs(self):
        path = "src/data/ch2_eventlists.json"
        source = json.loads((self.repo.root / path).read_text())
        source["lists"][0]["entries"][0]["args"][1] = "EventScr_UnknownReviewReference"
        data, members, prior, current, session = self.remediation(
            "generated", path, json.dumps(source), "owners:eventlists")
        finding = next(iter(session.accepted.values()))
        for member, unrelated_path in (
            ("drift-checks:eventlists", "reports/generated_data_eventlists_inventory.md"),
            ("consumers:eventlists", "src/events/ch2-eventinfo.h"),
        ):
            with self.subTest(reported=member):
                self.assertEqual(self.tools.tree(finding.origin).oid(unrelated_path),
                                 self.tools.tree(data["candidate_sha"]).oid(unrelated_path))
                session.accepted[finding.id] = replace(
                    finding, member=member, source_path=unrelated_path)
                data["findings"][0]["reported_member"] = member
                with self.assertRaisesRegex(ValueError, "reported member"):
                    self.model.assess_handoff(
                        data, members, (*prior, *current), session, tool_revision=self.repo.base,
                        remote_reviews=(), triage=(), pre_review_required=False)
        for member in ("outputs:eventlists", "consumers:eventlists", "drift-checks:eventlists"):
            with self.subTest(prerequisite=member):
                observation = next(item for item in prior if item.obligation.member == member)
                self.assertEqual((observation.verdict, observation.checks), ("unavailable", 0))
                self.assertEqual(observation.blocked_by, ("owners:eventlists",))
        session.accepted[finding.id] = finding
        data["findings"][0]["reported_member"] = finding.member
        report = self.model.assess_handoff(
            data, members, (*prior, *current), session, tool_revision=self.repo.base,
            remote_reviews=(), triage=(), pre_review_required=False)
        self.assertEqual({row["outcome"] for row in report["outcomes"]
                          if row["member"] in {"outputs:eventlists", "consumers:eventlists",
                                               "drift-checks:eventlists"}},
                         {"prerequisite-fixed"})
        blocked = next(item for item in prior if item.obligation.member == "outputs:eventlists")
        for blockers in ((), ("owners:shops",), ("owners:unknown",), ("consumers:eventlists",)):
            with self.subTest(unobserved_prerequisites=blockers):
                altered = tuple(replace(item, blocked_by=blockers) if item is blocked else item
                                for item in prior)
                with self.assertRaisesRegex(ValueError, "origin (evidence|prerequisite)"):
                    self.model.assess_handoff(
                        data, members, (*altered, *current), session, tool_revision=self.repo.base,
                        remote_reviews=(), triage=(), pre_review_required=False)

    def test_real_inventory_and_consumer_repairs_execute_the_selected_predicates(self):
        for path, member in (
            ("reports/generated_data_eventlists_inventory.md", "drift-checks:eventlists"),
            ("src/events/ch2-eventinfo.h", "consumers:eventlists"),
        ):
            with self.subTest(member=member):
                source = (self.repo.root / path).read_text()
                broken = (source + "\nUncommitted inventory change\n" if member.startswith("drift")
                          else source.replace("FACTION_ID_BLUE", "FACTION_ID_RED"))
                self.assertNotEqual(source, broken)
                self.remediation("generated", path, broken, member)

    def test_mixed_subject_findings_keep_semantic_inputs_separate_from_staged_bytes(self):
        paths = ("src/expansion_aoe.c", "reports/generated_data_eventlists_inventory.md",
                 "scripts/workflow_pilot/review_family.py")
        originals = {path: (self.repo.root / path).read_text() for path in paths}
        changes = {
            paths[0]: originals[paths[0]].replace("&& route->aiPolicy == EXPANSION_AOE_AI_NEVER", "&& 0"),
            paths[1]: originals[paths[1]] + "\nUncommitted inventory change\n",
            paths[2]: originals[paths[2]].replace('require(request["candidate_sha"] == session.head,',
                                                 'require(True,'),
        }
        self.assertTrue(all(changes[path] != originals[path] for path in paths))
        origin = self.repo.commit(changes)
        candidate = self.repo.commit(originals, parent=origin)
        data = self.scope("aoe", candidate)
        data["subjects"].extend(self.scope("generated", candidate)["subjects"])
        data["subjects"].extend(self.scope("session", candidate)["subjects"])
        members = self.host_members(self.tools.members(data, (origin,)))
        current = self.run_members(members, candidate)
        self.assert_satisfied(current)
        selected = ("action", "generated", "wire")
        prior = self.run_members(tuple(item for item in members if item.family in selected), origin)
        model = self.model
        scope = frozenset(model.subject_key(item) for item in data["subjects"])
        session = model.ReviewSession("coordinator", "implementer", scope, candidate,
                                      identity=("owner/repo", 1, self.repo.base))
        findings = []
        for index, (subject, family, member, path) in enumerate(zip(
            data["subjects"], selected,
            ("actions:AI_SELECT", "drift-checks:eventlists", "stale-bindings:review-session"), paths,
        )):
            finding = model.Finding(
                "finding-" + str(index), model.subject_key(subject), family, member,
                origin, path, "review-1")
            findings.append(finding)
            session.accept(finding)
            data["findings"].append({
                "finding_id": finding.id, **subject, "family": family, "reported_member": member,
            })
        arguments = dict(tool_revision=self.repo.base, remote_reviews=(), triage=(),
                         pre_review_required=False)
        good = model.assess_handoff(data, members, (*prior, *current), session, **arguments)
        self.assertTrue(good["handoff_eligible"])
        for finding, unrelated_path in zip(findings, (paths[1], paths[2], paths[0])):
            with self.subTest(subject=finding.subject):
                self.assertTrue(all(unrelated_path in dict(item.source_objects)
                                    for item in (*prior, *current)))
                session.accepted[finding.id] = replace(finding, source_path=unrelated_path)
                with self.assertRaisesRegex(ValueError, "unrelated source"):
                    model.assess_handoff(data, members, (*prior, *current), session, **arguments)
                session.accepted[finding.id] = finding
        for kind in ("aoe", "generated", "session"):
            standalone = self.tools.members(self.scope(kind, candidate), (origin,))
            expected = {item.identity: item.inputs for item in standalone}
            for member in members:
                if member.identity in expected:
                    with self.subTest(semantic_inputs=member.identity):
                        self.assertTrue(member.inputs == expected[member.identity],
                                        "mixed request changed the member's semantic input mapping")

    def test_two_round_handoffs_retain_bound_actual_origin_and_candidate_evidence(self):
        inventory = "reports/generated_data_eventlists_inventory.md"
        consumer = "src/events/ch2-eventinfo.h"
        originals = {path: (self.repo.root / path).read_text() for path in (inventory, consumer)}
        first = self.repo.commit({inventory: originals[inventory] + "\nInventory drift\n"})
        second = self.repo.commit({
            inventory: originals[inventory],
            consumer: originals[consumer].replace("FACTION_ID_BLUE", "FACTION_ID_RED"),
        }, parent=first)
        candidate = self.repo.commit({consumer: originals[consumer]}, parent=second)
        data = self.scope("generated", candidate)
        members = self.tools.members(data, (first, second))
        observations = tuple(item for revision in (first, second, candidate)
                             for item in self.run_members(members, revision))
        self.assert_satisfied(tuple(item for item in observations if item.revision == candidate))
        model = self.model
        key = model.subject_key(data["subjects"][0])
        session = model.ReviewSession("coordinator", "implementer", frozenset({key}), candidate,
                                      identity=("owner/repo", 1, self.repo.base))
        for number, origin, member, path in (
            (1, first, "drift-checks:eventlists", inventory),
            (2, second, "consumers:eventlists", consumer),
        ):
            fact = model.ReviewFact(
                "review-" + str(number), origin, "observed-copilot", "CHANGES_REQUESTED",
                f"2026-01-01T00:00:{number:02d}Z", "Complete reviewed source defect",
                (("comment-" + str(number), path, "Observed selected predicate failure"),))
            finding = model.Finding(
                "finding-" + str(number), key, "generated", member, origin, path, fact.id)
            session.triage(model.Triage(fact, "changes-requested", (finding,)))
            data["findings"].append({
                "finding_id": finding.id, **data["subjects"][0],
                "family": finding.family, "reported_member": member,
            })
        report = model.assess_handoff(
            data, members, observations, session, tool_revision=self.repo.base,
            remote_reviews=tuple(item.fact for item in session.rounds.events),
            triage=tuple(session.rounds.events), pre_review_required=False)
        self.assertTrue(report["handoff_eligible"])
        wire = json.loads(json.dumps(report))
        self.assertEqual(len(wire["round_handoffs"]), 2)
        actual = {(item.obligation.identity, item.revision): item for item in observations}
        for round_handoff in wire["round_handoffs"]:
            with self.subTest(round=round_handoff["consecutive"]):
                self.assertIn("outcome_refs", round_handoff)
                self.assertEqual(round_handoff["candidate_sha"], candidate)
                self.assertEqual(round_handoff["tool_revision"], self.repo.base)
                rows = [wire["outcomes"][index] for index in round_handoff["outcome_refs"]]
                self.assertEqual({(row["subject"], row["family"], row["member"]) for row in rows},
                                 {item.identity for item in members})
                self.assertEqual(len(rows), len(members))
                self.assertEqual({row["finding_id"] for row in rows}, set(round_handoff["findings"]))
                for row in rows:
                    for field, revision in (("origin_evidence", round_handoff["head"]),
                                            ("candidate_evidence", candidate)):
                        evidence = wire["evidence"][row[field]]
                        identity = tuple(evidence[name] for name in ("subject", "family", "member"))
                        observed = actual[identity, revision]
                        self.assertEqual(identity, (row["subject"], row["family"], row["member"]))
                        self.assertEqual(evidence["revision"], revision)
                        self.assertEqual(evidence["tool_revision"], observed.tool_revision)
                        for name in ("verdict", "checks", "kind", "detail"):
                            self.assertEqual(evidence[name], getattr(observed, name))
                        self.assertEqual(evidence["profile"], observed.obligation.profile)
                        self.assertEqual(evidence["probe"], observed.obligation.probe)
                        self.assertEqual(evidence["evidence"], list(observed.evidence))
                        source_set = wire["source_sets"][evidence["source_set"]]
                        self.assertEqual(source_set["revision"], revision)
                        self.assertEqual(source_set["tool_revision"], observed.tool_revision)
                        self.assertEqual(dict(source_set["objects"]), dict(observed.source_objects))
                    before = wire["evidence"][row["origin_evidence"]]
                    expected = ("affected-fixed" if before["verdict"] == "contract-violation"
                                else "verified-unaffected")
                    self.assertEqual(row["outcome"], expected)
        self.assertEqual(len(wire["source_sets"]), 3)
        self.assertEqual(len(wire["evidence"]), len(observations))
        saved = copy.deepcopy(report)
        edited = replace(session.rounds.events[0].fact, body="Review content edited")
        session.refresh_reviews((edited, session.rounds.events[1].fact))
        self.assertEqual(report, saved, "later round refresh rewrote an already returned assessment")

    def test_compiled_header_dependency_finding_uses_actual_member_failure(self):
        path = "include/bmunit.h"
        source = (self.repo.root / path).read_text()
        broken = source.replace("#define UNIT_IS_VALID(aUnit) ((aUnit) && (aUnit)->pCharacterData)",
                                "#define UNIT_IS_VALID(aUnit) ((aUnit) && (aUnit)->pCharacterData && 0)")
        self.assertNotEqual(source, broken)
        data, members, prior, current, session = self.remediation(
            "aoe", path, broken, "targets:stable-slots")
        self.assertTrue(all(path in dict(item.source_objects) for item in (*prior, *current)))
        finding = next(iter(session.accepted.values()))
        unrelated = replace(finding, member="items:routes")
        self.assertEqual(next(item.verdict for item in prior
                              if item.obligation.member == unrelated.member), "satisfied")
        session.accepted[finding.id] = unrelated
        data["findings"][0]["reported_member"] = unrelated.member
        with self.assertRaisesRegex(ValueError, "reported member"):
            self.model.assess_handoff(
                data, members, (*prior, *current), session, tool_revision=self.repo.base,
                remote_reviews=(), triage=(), pre_review_required=False)

    def test_every_shared_worker_binds_its_actual_staged_candidate_closure(self):
        for kind in ("aoe", "generated", "mixed"):
            with self.subTest(kind=kind):
                data = self.scope("aoe" if kind == "mixed" else kind)
                if kind == "mixed":
                    for other in ("generated", "session"):
                        data["subjects"].extend(self.scope(other)["subjects"])
                members = self.host_members(self.tools.members(data))
                tree = self.tools.tree(self.repo.base)
                root = self.repo.root / "build" / ("staged-closure-" + kind)
                root.mkdir(parents=True, exist_ok=True)
                copied = set()
                materialize = tree.materialize

                def capture(destination, paths):
                    materialize(destination, paths)
                    copied.update(paths)
                    for path in paths:
                        self.assertEqual((destination / path).read_bytes(), tree.read(path))

                with patch.object(tree, "materialize", side_effect=capture):
                    self.tools._stage(tree, root, members)
                self.assertTrue(copied)
                for member in members:
                    self.assertEqual(set(member.source_inputs), copied, member.member)
                    self.assertTrue(set(member.inputs) <= copied)
                observations = self.run_members(members, self.repo.base)
                self.assert_satisfied(observations)
                for item in observations:
                    self.assertEqual(dict(item.source_objects),
                                     {path: tree.oid(path) for path in copied})
                narrowed = replace(members[0], execution_inputs=members[0].execution_inputs[1:])
                with self.assertRaisesRegex(ValueError, "candidate inputs"):
                    self.tools.run_obligations((narrowed, *members[1:]), self.repo.base)

    def test_optional_owner_semantic_invalid_data_is_rejected(self):
        for path, owner in (
            ("src/data/autoplay_strategies.json", "autoplaystrategies"),
            ("src/data/ch2_bundle.json", "chapterbundle"),
        ):
            with self.subTest(owner=owner):
                data = json.loads((self.repo.root / path).read_text())
                fixed = None
                if owner == "autoplaystrategies":
                    selected = copy.deepcopy(data["strategies"][0])
                    selected["id"] = "AUTOPLAY_STRATEGY_REVIEW_SELECTED"
                    data["strategies"].append(selected)
                    fixed = json.dumps(data)
                    selected["callback"] = "not a C callback"
                else:
                    data["chapter"]["mapEventDataId"] = 9999
                self.remediation("generated", path, json.dumps(data), "owners:" + owner, fixed=fixed)

    def test_generated_producer_consumer_and_inventory_findings_bind_executed_source(self):
        cases = (
            ("generate.py", "outputs:eventlists",
             'return "".join(parts)', 'return "".join(parts).replace("FACTION_ID_BLUE", "FACTION_ID_RED")'),
            ("parser.py", "consumers:eventlists",
             "return errors", 'return errors + [GeneratedDataError("invalid parsed consumer")]'),
            ("inventory.py", "drift-checks:eventlists",
             'return "".join(lines)', 'return "".join(lines) + "unexpected inventory row\\n"'),
        )
        for name, member, old, new in cases:
            with self.subTest(source=name):
                path = "scripts/generated_data/eventlists/" + name
                source = (self.repo.root / path).read_text()
                self.assertIn(old, source)
                self.remediation("generated", path, source.replace(old, new), member)
                revision = self.repo.commit({path: "# Harmless formatting control.\n" + source})
                members = self.tools.members(self.scope("generated", revision))
                self.assert_satisfied(self.run_members(members, revision))

    def test_lifecycle_and_wire_execute_actual_origin_source(self):
        path = "scripts/workflow_pilot/review_family.py"
        source = (self.repo.root / path).read_text()
        mutation = source.replace(
            '        self._refresh()\n',
            '        self._refresh()\n        if review.outcome == "clean":\n            self.hold = None\n')
        self.assertNotEqual(source, mutation)
        origin = self.repo.commit({path: mutation})
        members = self.tools.members(self.scope("session"), (origin,))
        observations = self.run_members(members, origin)
        bad = {item.obligation.family for item in observations if item.verdict == "contract-violation"}
        self.assertEqual(bad, {"lifecycle", "wire"})
        self.assert_satisfied(self.run_members(members, self.repo.base))

    def test_unknown_added_deleted_and_wrong_subjects_fail_closed(self):
        data = self.scope("aoe")
        data["subjects"][0]["subject"] = "arbitrary-plugin"
        with self.assertRaises(ValueError):
            self.tools.members(data)
        path = "include/expansion_aoe.h"
        original = (self.repo.root / path).read_text()
        for changed in (
            original.replace("EXPANSION_AOE_ITEM_PHASE_COUNT",
                             "EXPANSION_AOE_ITEM_NEW_ACTION,\n    EXPANSION_AOE_ITEM_PHASE_COUNT"),
            original.replace("    EXPANSION_AOE_ITEM_BEGIN_USE,\n", ""),
        ):
            revision = self.repo.commit({path: changed})
            with self.assertRaises(ValueError):
                self.tools.members(self.scope("aoe", revision), (self.repo.base,))
        deleted = self.repo.commit({"src/data/ch2_shops.json": None})
        with self.assertRaises(ValueError):
            self.tools.members(self.scope("generated", deleted), (self.repo.base,))

    def test_finite_enum_values_match_native_compiler_and_reject_aliased_coverage(self):
        path = "include/expansion_aoe.h"
        original = (self.repo.root / path).read_text()
        phase_names = ("CAN_USE", "BEGIN_USE", "EXECUTE", "AI_SELECT", "PHASE_COUNT")
        shape_names = ("DIAMOND", "SQUARE", "CROSS", "COUNT")
        symbols = (["EXPANSION_AOE_ITEM_" + name for name in phase_names] +
                   ["EXPANSION_AOE_SHAPE_" + name for name in shape_names])
        expected = (0, 1, 2, 3, 4, 0, 1, 2, 3)
        reordered = re.sub(
            r"enum ExpansionAoEItemPhase\s*\{[^}]*\}",
            """enum ExpansionAoEItemPhase {
    EXPANSION_AOE_ITEM_BEGIN_USE = 01, /* Same values, another declaration order. */
    EXPANSION_AOE_ITEM_CAN_USE = 0x0,
    EXPANSION_AOE_ITEM_EXECUTE = 0x2,
    EXPANSION_AOE_ITEM_AI_SELECT = 03,
    EXPANSION_AOE_ITEM_PHASE_COUNT = 4
}""", original)
        variants = (
            ("original", original, True),
            ("equivalent", reordered, True),
            ("phase-alias", original.replace("EXPANSION_AOE_ITEM_BEGIN_USE,",
                                             "EXPANSION_AOE_ITEM_BEGIN_USE = 0,"), False),
            ("shape-alias", original.replace("EXPANSION_AOE_SHAPE_SQUARE,",
                                             "EXPANSION_AOE_SHAPE_SQUARE = 0,"), False),
            ("phase-gap", original.replace("EXPANSION_AOE_ITEM_BEGIN_USE,",
                                           "EXPANSION_AOE_ITEM_BEGIN_USE = 2,"), False),
            ("count", original.replace("    EXPANSION_AOE_ITEM_PHASE_COUNT\n",
                                       "    EXPANSION_AOE_ITEM_PHASE_COUNT = 3\n"), False),
        )
        directory = Path(self.enterContext(tempfile.TemporaryDirectory(
            prefix="review-enum-values-", dir=ROOT / "build")))
        base = self.tools.tree(self.repo.base)
        base.materialize(directory, base.under("include"))
        executable = directory / "values"
        code = ('#include "global.h"\n#include "expansion_aoe.h"\n#include <stdio.h>\n'
                'int main(void) { printf("' + " ".join(["%d"] * len(symbols)) + '\\n", '
                + ", ".join(symbols) + '); return 0; }\n')
        for name, source, accepted in variants:
            with self.subTest(variant=name):
                revision = self.repo.base if name == "original" else self.repo.commit({path: source})
                tree = self.tools.tree(revision)
                (directory / path).write_bytes(tree.read(path))
                compiled = subprocess.run(
                    ["/usr/bin/gcc", "-std=gnu89", "-DFE8_EXPANSION_MODERN_BUILD=1",
                     "-I", str(directory / "include"), "-x", "c", "-", "-o", str(executable)],
                    input=code, env=ENV, capture_output=True, text=True, timeout=60)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                actual = subprocess.run([str(executable)], env=ENV, capture_output=True,
                                        text=True, check=True, timeout=10)
                values = tuple(map(int, actual.stdout.split()))
                if accepted:
                    self.assertEqual(values, expected)
                    self.assertTrue(self.tools.members(self.scope("aoe", revision)))
                else:
                    self.assertNotEqual(values, expected)
                    with self.assertRaises(ValueError):
                        self.tools.members(self.scope("aoe", revision))

    def test_wire_stale_binding_probe_reaches_production_head_predicate(self):
        path = "scripts/workflow_pilot/review_family.py"
        source = (self.repo.root / path).read_text()
        broken = source.replace('require(request["candidate_sha"] == session.head,',
                                'require(True,')
        self.assertNotEqual(source, broken)
        revision = self.repo.commit({path: broken})
        members = self.tools.members(self.scope("session", revision))
        observations = self.run_members(members, revision)
        stale = next(item for item in observations if item.obligation.role == "stale-bindings")
        self.assertEqual(stale.verdict, "contract-violation", stale.detail)

    def test_compile_import_and_unknown_probe_are_unavailable_not_repairs(self):
        cases = (
            ("aoe", "src/expansion_aoe.c", "not valid C;\n"),
            ("session", "scripts/workflow_pilot/review_family.py", "import missing_review_module\n"),
        )
        for kind, path, source in cases:
            revision = self.repo.commit({path: source})
            members = self.tools.members(self.scope(kind, revision))
            observed = self.run_members(members, revision)
            self.assertTrue(any(item.verdict == "unavailable" for item in observed))
            self.assertFalse(any(item.verdict == "contract-violation" for item in observed))
        unknown = self.tools.subjects.worker(["not-a-probe"])[0]
        self.assertEqual((unknown["verdict"], unknown["kind"], unknown["checks"]),
                         ("unavailable", None, 0))

    def test_immutable_git_bytes_not_reopened_worktree_and_no_fsmonitor(self):
        tree = GitTree(self.repo.root, self.repo.base)
        path = "scripts/workflow_pilot/review_family.py"
        intended = tree.read(path)
        worktree = self.repo.root / path
        worktree.write_text("raise RuntimeError('worktree substitution')\n")
        try:
            self.assertEqual(tree.read(path), intended)
            self.assertEqual(GitTree(self.repo.root, self.repo.base).read(path), intended)
            self.assert_satisfied(self.tools.run_obligations(
                self.tools.members(self.scope("session")), self.repo.base))
        finally:
            worktree.write_bytes(intended)
        hook = self.repo.root / "fsmonitor-hook"
        sentinel = self.repo.root / "hook-ran"
        hook.write_text("#!/bin/sh\nprintf invoked > '" + str(sentinel) + "'\n")
        hook.chmod(0o700)
        subprocess.run(["/usr/bin/git", "-C", str(self.repo.root), "config",
                        "core.fsmonitor", str(hook)], env=ENV, check=True)
        GitTree(self.repo.root, self.repo.base).read(path)
        GitTree(self.repo.root, self.repo.base).git("status", "--porcelain")
        self.assertFalse(sentinel.exists())
        subprocess.run(["/usr/bin/git", "-C", str(self.repo.root), "status", "--porcelain"],
                       env=ENV, capture_output=True, check=True)
        self.assertTrue(sentinel.exists(), "raw Git did not exercise the configured fsmonitor hook")

    def test_semantics_preserving_source_refactor_remains_green(self):
        path = "src/expansion_aoe.c"
        source = (self.repo.root / path).read_text()
        revision = self.repo.commit({path: "/* Formatting-only review control. */\n" + source})
        members = self.tools.members(self.scope("aoe", revision), (self.repo.base,))
        self.assert_satisfied(self.run_members(self.host_members(members), revision))

    def test_selected_git_objects_modes_and_bytes_are_checked(self):
        path = "include/expansion_aoe.h"
        tree = GitTree(self.repo.root, self.repo.base)
        with patch.object(tree, "git", return_value=b"substituted source"):
            with self.assertRaisesRegex(ValueError, "bytes differ"):
                tree.read(path)
        git(self.repo.root, "read-tree", self.repo.base)
        git(self.repo.root, "update-index", "--cacheinfo",
            "120000," + tree.oid(path) + "," + path)
        changed_tree = git(self.repo.root, "write-tree")
        revision = git(self.repo.root, "commit-tree", changed_tree, "-p", self.repo.base,
                       "-m", "unsafe source-mode control")
        git(self.repo.root, "read-tree", self.repo.base)
        with self.assertRaisesRegex(ValueError, "regular Git blob"):
            self.tools.members(self.scope("aoe", revision))

    def test_same_feature_can_supply_reviewed_binding_without_base_installation(self):
        path = "scripts/workflow_pilot/review_subjects.py"
        source = (self.repo.root / path).read_text()
        changed = source.replace(
            'BINDINGS = (\n',
            'BINDINGS = (\n    SubjectSpec("TC-GAMEPLAY-006", "new-reviewed-binding", "aoe"),\n')
        revision = self.repo.commit({path: changed})
        data = self.scope("aoe", revision)
        data["subjects"][0]["subject"] = "new-reviewed-binding"
        with self.assertRaisesRegex(ValueError, "unknown subject"):
            self.tools.members(data)
        reviewed = ReviewTools(GitTree(self.repo.root, revision), self.repo.root)
        members = reviewed.members(data)
        self.assertTrue(members)
        self.assertTrue(all(item.subject.endswith("/new-reviewed-binding") for item in members))
        request_path = self.repo.root / "build" / "new-binding-request.json"
        request_path.parent.mkdir(exist_ok=True)
        request_path.write_text(json.dumps(data))
        planned = subprocess.run(
            [sys.executable, "-I", str(TRUSTED_LAUNCHER), "review-family",
             "--repository-root", str(self.repo.root), "--subject-root", str(self.repo.root),
             "--tool-revision", revision, "--candidate", revision,
             "--request", str(request_path), "--mode", "plan"],
            env=ENV, capture_output=True, text=True, timeout=30)
        self.assertEqual(planned.returncode, 0, planned.stderr)
        plan = json.loads(planned.stdout)
        self.assertEqual(plan["tool_revision"], revision)
        self.assertTrue(all(item["subject"].endswith("/new-reviewed-binding")
                            for item in plan["obligations"]))
        self.assertFalse(plan["merge_permission"])
        # Subsequent tests continue using the explicitly selected original tool objects.

    def test_zero_skipped_and_wrong_source_observations_cannot_pass(self):
        members = self.tools.members(self.scope("session"))
        good = self.run_members(members, self.repo.base)[0]
        for bad in (
            replace(good, checks=0), replace(good, verdict="skipped"),
            replace(good, checks=True), replace(good, checks=1.0),
            replace(good, verdict=[]), replace(good, kind={}),
            replace(good, source_objects=(("unrelated.py", self.repo.base),)),
            replace(good, source_objects=(("source",),)),
            replace(good, source_objects=None), replace(good, evidence=([],)),
            replace(good, evidence=None), replace(good, evidence=("runtime",)),
            replace(good, detail=None), replace(good, kind="rom"),
            replace(good, blocked_by=None), replace(good, blocked_by=([],)),
            replace(good, blocked_by=("owners:eventlists",)),
            *(replace(good, kind=kind) for kind in ("native", "parsed", "arm-object", None)),
        ):
            with self.subTest(observation=bad), self.assertRaises(ValueError):
                bad.validate()

    def test_worker_record_types_and_missing_fields_reject_before_admission(self):
        members = self.tools.members(self.scope("session"))
        payload = json.dumps([item.probe for item in members]).encode()
        run = self.tools.subjects.run_process
        captured = []

        def capture(*args, **kwargs):
            completed = run(*args, **kwargs)
            if kwargs.get("input") == payload:
                captured.append(json.loads(completed.stdout))
            return completed

        with patch.object(self.tools.subjects, "run_process", side_effect=capture):
            observations = self.tools.run_obligations(members, self.repo.base)
            self.assert_satisfied(observations)
        self.assertEqual(len(captured), 1)
        good = captured[0]
        self.assertEqual(
            [(item.kind, item.verdict, item.checks, item.detail) for item in observations],
            [(row["kind"], row["verdict"], row["checks"], row["detail"]) for row in good])
        malformed = [None, [], "row", {**good[0], "trusted": True}]
        for field in good[0]:
            row = dict(good[0])
            del row[field]
            malformed.append(row)
        for field, value in (
            ("kind", "native"), ("kind", "parsed"), ("kind", "arm-object"),
            ("kind", None), ("kind", {}), ("kind", []),
            ("checks", True), ("checks", 1.0), ("checks", "1"), ("checks", 0),
            ("checks", -1), ("checks", None), ("verdict", []), ("verdict", "skipped"),
            ("detail", None), ("detail", []), ("detail", "x" * 2001), ("detail", ""),
            ("probe", None), ("probe", good[1]["probe"]),
            ("blocked_by", None), ("blocked_by", "owners:eventlists"),
            ("blocked_by", [None]), ("blocked_by", [[]]),
            ("blocked_by", ["owners:eventlists"]), ("blocked_by", [True]),
        ):
            malformed.append({**good[0], field: value})
        malformed.append({**good[0], "verdict": "unavailable", "checks": 1})
        for row in malformed:
            with self.subTest(row=row):
                raw = json.dumps([row, *good[1:]]).encode()

                def corrupt(*args, **kwargs):
                    if kwargs.get("input") == payload:
                        kwargs["_on_cleanup"]()
                        return subprocess.CompletedProcess(args[0], 0, raw, b"")
                    return run(*args, **kwargs)

                with patch.object(self.tools.subjects, "run_process", side_effect=corrupt):
                    with self.assertRaises(ValueError):
                        self.tools.run_obligations(members, self.repo.base)

    def test_wrongly_routed_real_worker_success_failure_and_unavailability_keep_kind(self):
        worker_path = "scripts/workflow_pilot/review_subjects.py"
        worker_source = (self.repo.root / worker_path).read_text()
        for kind, old, route, source_path, source in (
            ("arm-object", 'return _arm(probe == "aoe-arm:enabled")',
             'return _native("aoe-phase:AI_SELECT")', "src/expansion_aoe.c", "native"),
            ("parsed", "return _generated(probe)",
             'return _session_probe("lifecycle:resets")',
             "scripts/workflow_pilot/review_family.py", "host"),
        ):
            original = (self.repo.root / source_path).read_text()
            broken = (original.replace("&& route->aiPolicy == EXPANSION_AOE_AI_NEVER", "&& 0")
                      if source == "native" else original.replace(
                          "                consecutive = 0\n", "                consecutive += 0\n"))
            unavailable = "not valid C;\n" if source == "native" else "import missing_review_module\n"
            routed = worker_source.replace(old, route)
            self.assertNotEqual(routed, worker_source)
            self.assertNotEqual(original, broken)
            for verdict, contents in (
                ("satisfied", original), ("contract-violation", broken), ("unavailable", unavailable),
            ):
                with self.subTest(expected=kind, actual=source, verdict=verdict):
                    revision = self.repo.commit({worker_path: routed})
                    candidate = (revision if contents == original else
                                 self.repo.commit({source_path: contents}, parent=revision))
                    tools = ReviewTools(GitTree(self.repo.root, revision), self.repo.root)
                    data = self.scope("aoe" if kind == "arm-object" else "generated", candidate)
                    if kind == "parsed":
                        data["subjects"].extend(self.scope("session")["subjects"])
                    members = tools.members(data)
                    if kind == "arm-object":
                        members = tuple(item for item in members if item.family == "resource")
                    probes = {item.probe for item in members
                              if item.probe.startswith("aoe-arm:" if kind == "arm-object"
                                                       else "generated-")}
                    payload = json.dumps([item.probe for item in members]).encode()
                    run = tools.subjects.run_process
                    captured = []

                    def capture(*args, **kwargs):
                        completed = run(*args, **kwargs)
                        if kwargs.get("input") == payload:
                            captured.extend(json.loads(completed.stdout))
                        return completed

                    with patch.object(tools.subjects, "run_process", side_effect=capture):
                        with self.assertRaisesRegex(ValueError, "kind"):
                            tools.run_obligations(members, candidate)
                    actual = [row for row in captured if row["probe"] in probes]
                    self.assertEqual({row["probe"] for row in actual}, probes)
                    self.assertEqual({(row["kind"], row["verdict"]) for row in actual},
                                     {(source, verdict)})
                    self.assertTrue(all(row["checks"] == 0 if verdict == "unavailable"
                                        else row["checks"] > 0 for row in actual))

    def test_worker_exit_without_rows_has_no_evidence_kind_or_checks(self):
        path = "scripts/workflow_pilot/review_subjects.py"
        source = (self.repo.root / path).read_text()
        source = source.replace("def worker(probes: list[str]) -> list[dict]:",
                                "def worker(probes: list[str]) -> list[dict]:\n    raise SystemExit(7)")
        revision = self.repo.commit({path: source})
        tools = ReviewTools(GitTree(self.repo.root, revision), self.repo.root)
        members = tools.members(self.scope("session"))
        observations = tools.run_obligations(members, self.repo.base)
        self.assertEqual({(item.verdict, item.kind, item.checks) for item in observations},
                         {("unavailable", None, 0)})
        self.assertTrue(all("process failed" in item.detail for item in observations))


if __name__ == "__main__":
    unittest.main()
