"""Actual project declaration predicates through captured production sources."""

import shutil
import subprocess
import unittest

from scripts.workflow_pilot.tests.review_support import ENV, ROOT, git, request, snapshot
from scripts.workflow_pilot.trusted_review_gate import GitTree, ReviewTools


class ProjectSubjectTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.workspace = snapshot()
        cls.repo = cls.workspace.__enter__()
        names = subprocess.check_output(
            ["/usr/bin/git", "-C", str(ROOT), "ls-files", "-z"], env=ENV,
        ).decode().split("\0")
        for name in filter(None, names):
            source = ROOT / name
            if not source.is_file() or source.is_symlink() or "/tests/" in name:
                continue
            target = cls.repo.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        git(cls.repo.root, "add", ".")
        git(cls.repo.root, "commit", "-qm", "captured project declaration inputs")
        cls.repo.base = git(cls.repo.root, "rev-parse", "HEAD")
        cls.tools = ReviewTools(GitTree(cls.repo.root, cls.repo.base), cls.repo.root)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.__exit__(None, None, None)

    def scope(self, head=None):
        return request(
            "TC-PROJECT-SELECTION-001", "project-selection",
            self.repo.base, head or self.repo.base,
        )

    def test_project_action_family_executes_real_frontend_predicates(self):
        members = tuple(item for item in self.tools.members(self.scope())
                        if item.family == "action")
        self.assertEqual({item.role for item in members}, {"actions", "items", "targets"})
        observed = self.tools.run_obligations(members, self.repo.base)
        self.assertEqual(
            [(item.obligation.member, item.verdict, item.detail)
             for item in observed if item.verdict != "satisfied"],
            [],
        )
        self.assertTrue(all(item.kind == "host" and item.checks > 0 for item in observed))

    def test_project_resources_compile_actual_enabled_and_default_identity(self):
        path = "src/expansion_metadata.c"
        original = (self.repo.root / path).read_text()
        ignored = original.replace("FE8_EXPANSION_ROM_SIZE_BYTES,", "0x01000000,", 1)
        self.assertNotEqual(original, ignored)
        before = self.repo.commit({path: ignored})
        after = self.repo.commit({path: original}, parent=before)
        family = tuple(item for item in self.tools.members(self.scope(after), (before,))
                       if item.family == "resource")
        self.assertEqual({item.role for item in family}, {"enabled", "disabled"})
        broken = self.tools.run_obligations(family, before)
        self.assertEqual([(item.verdict, item.checks) for item in broken
                          if item.obligation.role == "enabled"],
                         [("contract-violation", 1)],
                         [(item.obligation.role, item.detail) for item in broken])
        self.assertTrue(all(item.verdict == "satisfied" and item.kind == "arm-object"
                            and item.checks > 0 for item in broken
                            if item.obligation.role == "disabled"))
        repaired = self.tools.run_obligations(family, after)
        self.assertTrue(all(item.verdict == "satisfied" and item.kind == "arm-object"
                            and item.checks > 0 for item in repaired))
        allocated = original + (
            '\nstatic int sReviewResourceState __attribute__((used, section("review_ram")));\n'
        )
        before = self.repo.commit({path: allocated})
        after = self.repo.commit({path: original}, parent=before)
        family = tuple(item for item in self.tools.members(self.scope(after), (before,))
                       if item.family == "resource")
        broken = self.tools.run_obligations(family, before)
        self.assertTrue(all(item.verdict == "contract-violation" and item.checks == 1
                            for item in broken), [(item.obligation.role, item.detail)
                                                 for item in broken])
        repaired = self.tools.run_obligations(family, after)
        self.assertTrue(all(item.verdict == "satisfied" and item.kind == "arm-object"
                            and item.checks > 0 for item in repaired))

    def test_project_generated_family_consumes_all_selected_schema_artifacts(self):
        members = tuple(item for item in self.tools.members(self.scope())
                        if item.family == "generated")
        self.assertEqual({item.role for item in members},
                         {"owners", "outputs", "consumers", "drift-checks"})
        observed = self.tools.run_obligations(members, self.repo.base)
        self.assertEqual(
            [(item.obligation.member, item.verdict, item.detail)
             for item in observed if item.verdict != "satisfied" or item.checks <= 0],
            [],
        )
        path = "scripts/generated_data/cli.py"
        original = (self.repo.root / path).read_text()
        ignored = original.replace("args.source = replacement", "args.source = None", 1)
        self.assertNotEqual(original, ignored)
        before = self.repo.commit({path: ignored})
        after = self.repo.commit({path: original}, parent=before)
        family = tuple(item for item in self.tools.members(self.scope(after), (before,))
                       if item.family == "generated")
        broken = self.tools.run_obligations(family, before)
        self.assertEqual([(item.verdict, item.checks) for item in broken
                          if item.obligation.role == "owners"],
                         [("contract-violation", 1)])
        repaired = self.tools.run_obligations(family, after)
        self.assertTrue(all(item.verdict == "satisfied" and item.checks > 0 for item in repaired))

    def test_project_generated_consumer_compiles_the_cli_published_artifact(self):
        path = "scripts/generated_data/cli.py"
        original = (self.repo.root / path).read_text()
        damaged_publication = original.replace(
            "content = schema.generate_c(records, source_path)",
            'content = ("int wrong_selected_publication;\\n" if schema.replacement_source '
            'else schema.generate_c(records, source_path))', 1)
        self.assertNotEqual(original, damaged_publication)
        before_publication = self.repo.commit({path: damaged_publication})
        after_publication = self.repo.commit({path: original}, parent=before_publication)
        family = tuple(item for item in self.tools.members(
            self.scope(after_publication), (before_publication,))
                       if item.family == "generated")
        broken = self.tools.run_obligations(family, before_publication)
        self.assertEqual([(item.verdict, item.checks) for item in broken
                          if item.obligation.role == "consumers"],
                         [("contract-violation", 1)])
        repaired = self.tools.run_obligations(family, after_publication)
        self.assertTrue(all(item.verdict == "satisfied" and item.checks > 0 for item in repaired))

    def test_unknown_project_provider_declaration_requires_a_reviewed_model(self):
        path = "project_selection.mk"
        original = (self.repo.root / path).read_text()
        changed = original.replace(
            "FE8_PROJECT_GAME_OPTIONS := ", "FE8_PROJECT_GAME_OPTIONS := unreviewed ", 1)
        self.assertNotEqual(original, changed)
        head = self.repo.commit({path: changed})
        with self.assertRaisesRegex(ValueError, "reviewed finite model"):
            self.tools.members(self.scope(head))

    def test_project_lifecycle_detects_default_metadata_leak_and_restores_context(self):
        members = tuple(item for item in self.tools.members(self.scope())
                        if item.family == "lifecycle")
        self.assertEqual({item.role for item in members},
                         {"entries", "preservation", "resets", "terminals"})
        observed = self.tools.run_obligations(members, self.repo.base)
        self.assertEqual([(item.obligation.member, item.verdict, item.detail)
                          for item in observed
                          if item.verdict != "satisfied" or item.checks <= 0], [])
        path = "scripts/modernize/expansion_config.py"
        original = (self.repo.root / path).read_text()
        changed = original.replace(
            'if self.project != "default":\n            data["project"] = self.project',
            'if True:\n            data["project"] = self.project', 1)
        self.assertNotEqual(original, changed)
        before = self.repo.commit({path: changed})
        after = self.repo.commit({path: original}, parent=before)
        members = tuple(item for item in self.tools.members(self.scope(after), (before,))
                        if item.family == "lifecycle")
        broken = self.tools.run_obligations(members, before)
        preserved = next(item for item in broken if item.obligation.role == "preservation")
        self.assertEqual((preserved.verdict, preserved.checks), ("contract-violation", 1))
        repaired = self.tools.run_obligations(members, after)
        self.assertEqual([(item.obligation.member, item.verdict, item.detail)
                          for item in repaired if item.verdict != "satisfied"], [])

    def test_project_schema_factory_aliases_are_structural_not_spelling(self):
        path = "scripts/generated_data/registry.py"
        original = (self.repo.root / path).read_text()
        renamed = original.replace("_units_schema", "_unit_schema_factory")
        self.assertNotEqual(original, renamed)
        head = self.repo.commit({path: renamed})
        self.assertEqual(self.tools.members(self.scope(head)), self.tools.members(self.scope()))
        wrong = original.replace("return UnitsTableSchema()", "return ShopsTableSchema()", 1)
        self.assertNotEqual(original, wrong)
        head = self.repo.commit({path: wrong})
        with self.assertRaisesRegex(ValueError, "reviewed finite model"):
            self.tools.members(self.scope(head))

    def test_project_wire_binds_and_replays_real_schema_invocations(self):
        members = tuple(item for item in self.tools.members(self.scope())
                        if item.family == "wire")
        self.assertEqual({item.role for item in members},
                         {"producers", "consumers", "validators", "replay", "stale-bindings"})
        observed = self.tools.run_obligations(members, self.repo.base)
        self.assertEqual([(item.obligation.member, item.verdict, item.detail)
                          for item in observed if item.verdict != "satisfied"], [])
        path = "scripts/generated_data/schema.py"
        original = (self.repo.root / path).read_text()
        changed = original.replace(
            "schema.message_header = self.message_header", "schema.message_header = None", 1)
        self.assertNotEqual(original, changed)
        before = self.repo.commit({path: changed})
        after = self.repo.commit({path: original}, parent=before)
        members = tuple(item for item in self.tools.members(self.scope(after), (before,))
                        if item.family == "wire")
        broken = self.tools.run_obligations(members, before)
        consumer = next(item for item in broken if item.obligation.role == "consumers")
        self.assertEqual((consumer.verdict, consumer.checks), ("contract-violation", 1))
        repaired = self.tools.run_obligations(members, after)
        self.assertEqual([(item.obligation.member, item.verdict, item.detail)
                          for item in repaired if item.verdict != "satisfied"], [])
