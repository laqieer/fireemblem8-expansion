"""Finite provider review binding: staged API, source-suite and owner observations."""

import ast
from dataclasses import replace
import json
from pathlib import Path
import shlex
import types
import unittest

from scripts.workflow_pilot.tests.test_review_subjects import SubjectTestCase
from scripts.workflow_pilot.tests.review_support import request


CASE = "TC-OWNERSHIP-SEALED-PLATFORM-STORAGE-001"
SUBJECT = "sealed-platform-storage"
SOURCE = "scripts/validation_ownership/runtime_image.py"
TESTS = "scripts/validation_ownership/tests/test_platform_image.py"
OWNER = "tests/workflows/test_ownership_probe.py"
NEW_COVERAGE = {
    "test_source_admission_rejects_each_untrusted_observation_before_backing",
    "test_capture_rejects_each_changed_descriptor_and_path_identity",
    "test_slices_enforce_actual_workspace_and_key_boundaries",
    "test_slice_quota_and_deadline_refuse_before_reading_owned_body",
}


class PlatformStorageSubjectTests(SubjectTestCase):
    def scope(self, head=None):
        return request(CASE, SUBJECT, self.repo.base, head or self.repo.base)

    def test_registered_review_family_discovery_selects_complete_binding_suite(self):
        root = Path(__file__).resolve().parents[3]
        catalog = json.loads((root / "docs/test-cases/registry.json").read_text())
        case = next(row for row in catalog["cases"]
                    if row["id"] == "TC-WORKFLOW-REVIEW-FAMILY-001")
        commands = [shlex.split(row["command"]) for row in case["automation"]]
        discovery = next(argv for argv in commands
                         if "discover" in argv and "-p" in argv and "-s" in argv
                         and argv[argv.index("-s") + 1] == "scripts/workflow_pilot/tests")
        loader = unittest.TestLoader()
        suite = loader.discover(
            str(root / discovery[discovery.index("-s") + 1]),
            pattern=discovery[discovery.index("-p") + 1], top_level_dir=str(root),
        )

        def identifiers(items):
            for item in items:
                if isinstance(item, unittest.TestSuite):
                    yield from identifiers(item)
                else:
                    yield item.id()

        expected = set(identifiers(loader.loadTestsFromTestCase(type(self))))
        self.assertFalse(loader.errors, loader.errors)
        self.assertTrue(expected)
        self.assertEqual(expected & set(identifiers(suite)), expected)

    def test_complete_finite_roles_and_shared_source_execution_closure(self):
        members = self.tools.members(self.scope())
        self.assertEqual(len(members), 15)
        self.assertEqual({item.family for item in members},
                         {"wire", "lifecycle", "resource", "generated"})
        for family in {item.family for item in members}:
            self.assertEqual({item.role for item in members if item.family == family},
                             set(self.model.FAMILIES[family]))
        self.assertTrue({
            "validators:source-admission", "stale-bindings:source-identity",
            "enabled:bounded-slices", "owners:probe-inventory",
        } <= {item.member for item in members})
        observations = self.run_members(members, self.repo.base)
        self.assert_satisfied(observations)
        self.assertEqual({item.kind for item in observations}, {"host", "parsed"})
        closure = set(members[0].source_inputs)
        self.assertTrue({SOURCE, TESTS, OWNER,
                         "scripts/validation_ownership/budget.py",
                         "scripts/validation_ownership/lifecycle.py",
                         "scripts/validation_ownership/producer_channel.py",
                         "scripts/validation_ownership/authority.py",
                         ".github/workflows/build.yml",
                         "scripts/validation_ownership/foundation.mk"} <= closure)
        self.assertTrue(all(set(item.source_inputs) == closure for item in members))
        self.assertTrue(all(dict(item.source_objects) == {
            path: self.tools.tree(self.repo.base).oid(path) for path in closure
        } for item in observations))
        for item in observations:
            if item.obligation.probe in {"platform:admission", "platform:identity", "platform:workspace"}:
                self.assertIn("12-case suite passed", item.detail)
                self.assertIn("coverage observation", item.detail)

    def coverage_origin(self):
        parsed = ast.parse((self.repo.root / TESTS).read_bytes())
        provider = next(node for node in parsed.body if isinstance(node, ast.ClassDef)
                        and node.name == "PlatformImageTests")
        removed = {node.name for node in provider.body
                   if isinstance(node, ast.FunctionDef) and node.name in NEW_COVERAGE}
        self.assertEqual(removed, NEW_COVERAGE)
        provider.body = [node for node in provider.body
                         if not isinstance(node, ast.FunctionDef) or node.name not in NEW_COVERAGE]
        # This fixture models the coverage gap; it is not a historical Git-tree claim.
        before = self.repo.commit({TESTS: ast.unparse(parsed)})
        after = self.repo.commit({TESTS: (self.repo.root / TESTS).read_bytes()}, parent=before)
        return before, after

    def test_original_suite_survival_is_coverage_gap_not_runtime_defect(self):
        before, after = self.coverage_origin()
        data = self.scope(after)
        members = tuple(item for item in self.tools.members(data, (before,))
                        if item.family in {"wire", "resource"})
        prior = self.run_members(members, before)
        current = self.run_members(members, after)
        self.assert_satisfied(current)
        reported = {"validators:source-admission", "stale-bindings:source-identity",
                    "enabled:bounded-slices"}
        self.assertEqual({item.obligation.member for item in prior
                          if item.verdict == "contract-violation"}, reported)
        for item in prior:
            if item.obligation.member in reported:
                self.assertIn("coverage gap, not an old runtime violation", item.detail)
                self.assertIn("original 8-case suite has 0/", item.detail)
                self.assertIn("original unmutated suite passed", item.detail)
        session = self.model.ReviewSession(
            "coordinator", "implementer", frozenset({CASE + "/" + SUBJECT}), after,
            identity=("owner/repo", 1, self.repo.base))
        for index, member in enumerate(sorted(reported)):
            obligation = next(item for item in members if item.member == member)
            finding = self.model.Finding(
                "coverage-" + str(index), obligation.subject, obligation.family,
                member, before, TESTS, "review-1",
            )
            session.accept(finding)
            data["findings"].append({
                "finding_id": finding.id, **data["subjects"][0],
                "family": finding.family, "reported_member": member,
            })
        arguments = dict(tool_revision=self.repo.base, remote_reviews=(), triage=(),
                         pre_review_required=False)
        report = self.model.assess_handoff(
            data, members, (*prior, *current), session, **arguments)
        self.assertTrue(report["handoff_eligible"])
        self.assertFalse(report["merge_permission"])
        self.assertFalse(report["exact_head_review_clean"])
        missing = tuple(item for item in current
                        if item.obligation.member != "replay:sealed-body")
        with self.assertRaises(ValueError):
            self.model.assess_handoff(data, members, (*prior, *missing), session, **arguments)
        forged = tuple(replace(item, tool_revision="f" * 40) for item in current)
        with self.assertRaises(ValueError):
            self.model.assess_handoff(data, members, (*prior, *forged), session, **arguments)

    def test_parsed_make_selection_reproduces_missing_owner_inventory(self):
        source = (self.repo.root / OWNER).read_text()
        missing = source.replace(
            '    "scripts.validation_ownership.tests.test_platform_image",\n', "")
        self.assertNotEqual(source, missing)
        before = self.repo.commit({OWNER: missing})
        after = self.repo.commit({OWNER: source}, parent=before)
        members = tuple(item for item in self.tools.members(self.scope(after), (before,))
                        if item.family == "generated")
        prior = self.run_members(members, before)
        self.assertEqual({item.obligation.member for item in prior
                          if item.verdict == "contract-violation"},
                         {"owners:probe-inventory", "drift-checks:probe-inventory"})
        self.assert_satisfied(self.run_members(members, after))

    def test_module_load_tests_bindings_and_wildcard_imports_refuse_finite_selection(self):
        source = (self.repo.root / TESTS).read_text()
        bindings = (
            "load_tests = lambda loader, tests, pattern: tests",
            "load_tests: object = lambda loader, tests, pattern: tests",
            "load_tests: object",
            "load_tests, unrelated = None, None",
            "[load_tests] = [None]",
            "load_tests += ()",
            "(load_tests := None)",
            "import unittest as load_tests",
            "from unittest import TestSuite as load_tests",
            "from unittest import load_tests",
            "from unittest import *",
            "def load_tests(loader, tests, pattern):\n    return tests",
            "async def load_tests(loader, tests, pattern):\n    return tests",
            "class load_tests:\n    pass",
            "if False:\n    load_tests = None",
            "for load_tests in ():\n    pass",
            "with open('/never-opened') as load_tests:\n    pass",
            "try:\n    pass\nexcept Exception as load_tests:\n    pass",
        )
        for binding in (*bindings, *(item.replace("load_tests", "__getattr__")
                                     for item in bindings),
                        *(item.replace("load_tests", "__dir__") for item in bindings)):
            with self.subTest(binding=binding):
                head = self.repo.commit({TESTS: source + "\n" + binding + "\n"})
                members = tuple(item for item in self.tools.members(self.scope(head))
                                if item.family == "generated")
                observed = next(item for item in self.run_members(members, head)
                                if item.obligation.member == "owners:probe-inventory")
                self.assertEqual(observed.verdict, "unavailable", observed.detail)
                self.assertIn("custom selection needs a reviewed model", observed.detail)
        harmless = (
            "\ndef helper():\n    load_tests = None\n    return load_tests\n"
            "\ndef read_hook():\n    return load_tests\n"
        )
        head = self.repo.commit({TESTS: source + harmless})
        members = tuple(item for item in self.tools.members(self.scope(head))
                        if item.family == "generated")
        self.assert_satisfied(self.run_members(members, head))

    def test_actual_unittest_module_enumeration_and_attribute_hooks_change_selection(self):
        module = types.ModuleType("owned_selection_fixture")
        module.SelectedTests = type("SelectedTests", (unittest.TestCase,), {
            "test_selected": lambda self: None,
        })
        loader = unittest.TestLoader()
        self.assertEqual(loader.loadTestsFromModule(module).countTestCases(), 1)
        module.__dir__ = lambda: []
        self.assertEqual(loader.loadTestsFromModule(module).countTestCases(), 0)
        del module.__dir__
        module.__getattr__ = lambda name: (
            (lambda loader, suite, pattern: unittest.TestSuite()) if name == "load_tests" else None
        )
        self.assertEqual(loader.loadTestsFromModule(module).countTestCases(), 0)
        del module.__getattr__
        self.assertEqual(loader.loadTestsFromModule(module).countTestCases(), 1)

    def test_changed_execution_import_and_identity_inventory_fail_closed(self):
        source = (self.repo.root / SOURCE).read_text()
        for changed in (
            source + "\nfrom scripts.validation_ownership import session\n",
            source.replace("info.st_mtime_ns, info.st_ctime_ns,", "info.st_mtime_ns,"),
        ):
            with self.subTest(changed=changed[-80:]):
                head = self.repo.commit({SOURCE: changed})
                with self.assertRaises(ValueError):
                    self.tools.members(self.scope(head))

    def test_missing_generated_output_and_disabled_consumer_are_not_success(self):
        makefile = "scripts/validation_ownership/foundation.mk"
        workflow = ".github/workflows/build.yml"
        source = (self.repo.root / makefile).read_text()
        missing = source.replace(" scripts.validation_ownership.tests.test_platform_image", "")
        self.assertNotEqual(source, missing)
        source = (self.repo.root / workflow).read_text()
        command = "      run: make -f " + makefile + " ownership-probe-test"
        disabled = source.replace(command, "      if: false\n" + command)
        self.assertNotEqual(source, disabled)
        for path, changed, reported in (
            (makefile, missing, "outputs:probe-inventory"),
            (workflow, disabled, "consumers:probe-inventory"),
        ):
            with self.subTest(member=reported):
                head = self.repo.commit({path: changed})
                members = tuple(item for item in self.tools.members(self.scope(head))
                                if item.family == "generated")
                observation = next(item for item in self.run_members(members, head)
                                   if item.obligation.member == reported)
                self.assertEqual(observation.verdict, "contract-violation", observation.detail)

    def test_runtime_regression_and_unexecutable_suite_cannot_pass_coverage(self):
        source = (self.repo.root / SOURCE).read_text()
        broken = source.replace("before.st_uid != 0", "before.st_uid != before.st_uid")
        self.assertNotEqual(source, broken)
        head = self.repo.commit({SOURCE: broken})
        members = tuple(item for item in self.tools.members(self.scope(head))
                        if item.family == "wire")
        observed = next(item for item in self.run_members(members, head)
                        if item.obligation.probe == "platform:admission")
        self.assertEqual(observed.verdict, "contract-violation")
        self.assertIn("fails before mutation", observed.detail)
        head = self.repo.commit({TESTS: "raise RuntimeError('unexecutable original suite')\n"})
        members = tuple(item for item in self.tools.members(self.scope(head))
                        if item.family == "wire")
        observed = next(item for item in self.run_members(members, head)
                        if item.obligation.probe == "platform:admission")
        self.assertEqual((observed.verdict, observed.checks), ("unavailable", 0))

    def test_semantic_local_rename_and_identity_field_order_remain_green(self):
        parsed = ast.parse((self.repo.root / SOURCE).read_bytes())
        for node in ast.walk(parsed):
            if isinstance(node, ast.Name) and node.id == "before":
                node.id = "initial_info"
            if isinstance(node, ast.FunctionDef) and node.name == "_identity":
                returned = next(item for item in node.body if isinstance(item, ast.Return))
                returned.value.elts.reverse()
        head = self.repo.commit({SOURCE: ast.unparse(parsed)})
        members = self.tools.members(self.scope(head))
        self.assert_satisfied(self.run_members(members, head))


if __name__ == "__main__":
    unittest.main()
