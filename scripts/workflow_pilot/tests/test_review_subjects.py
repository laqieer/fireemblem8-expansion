"""Actual source, native/ARM and generated-data controls for review convergence."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import copy
from dataclasses import replace
import json
import shlex
import subprocess
import unittest
from unittest.mock import patch

from scripts.modernize.tests.make_database import make_database_rule
from scripts.workflow_pilot.trusted_review_gate import GitTree, ReviewTools
from scripts.workflow_pilot.tests.review_support import ROOT, ENV, git, request, snapshot


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
        path = "scripts/workflow_pilot/review_subjects.py"
        source = (self.repo.root / path).read_text()
        missing = self.repo.root / "build" / "uninstalled-arm-none-eabi-gcc"
        self.assertFalse(missing.exists())
        compiler = '"/usr/bin/arm-none-eabi-gcc"'
        self.assertEqual(source.count(compiler), 1)
        revision = self.repo.commit({path: source.replace(compiler, repr(str(missing)))})
        tools = ReviewTools(GitTree(self.repo.root, revision), self.repo.root)
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
                        data, members, evidence, session, tool_revision=revision,
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
                commands = [shlex.split(line.lstrip("\t").lstrip("@+"))
                            for line in rule.replace("\\\n", " ").splitlines()
                            if line.startswith("\t")]
                self.assertIn(
                    ["$(PYTHON)", "-m", "unittest",
                     "scripts.workflow_pilot.tests.arm_review_subjects", "-v"],
                    commands)

    def remediation(self, kind, path, broken, member):
        original = (self.repo.root / path).read_bytes()
        before = self.repo.commit({path: broken})
        after = self.repo.commit({path: original}, parent=before)
        data = self.scope(kind, after)
        members = tuple(item for item in self.tools.members(data, (before,))
                        if item.family == ("action" if kind == "aoe" else "generated"))
        prior = self.run_members(members, before)
        current = self.run_members(members, after)
        self.assert_satisfied(current)
        relevant = [item for item in prior if item.obligation.member == member]
        self.assertEqual(len(relevant), 1)
        self.assertEqual(relevant[0].verdict, "contract-violation", relevant[0].detail)
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

    def test_lifecycle_and_wire_execute_actual_origin_source(self):
        path = "scripts/workflow_pilot/review_family.py"
        source = (self.repo.root / path).read_text()
        mutation = source.replace(
            'if self.hold is not None or (review.outcome == "untriaged" and not requested):',
            'if review.outcome == "untriaged":').replace(
            'if review.outcome == "clean":\n            self.consecutive = 0',
            'if review.outcome == "clean":\n            self.hold = None\n            self.consecutive = 0')
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
        self.assertEqual(self.tools.subjects.worker(["not-a-probe"])[0]["verdict"], "unavailable")

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
        self.assertFalse(sentinel.exists())

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
        # Subsequent tests continue using the explicitly selected original tool objects.

    def test_zero_skipped_and_wrong_source_observations_cannot_pass(self):
        members = self.tools.members(self.scope("session"))
        good = self.run_members(members, self.repo.base)[0]
        for bad in (
            replace(good, checks=0), replace(good, verdict="skipped"),
            replace(good, source_objects=(("unrelated.py", self.repo.base),)),
            replace(good, evidence=("runtime",)), replace(good, kind="rom"),
        ):
            with self.assertRaises(ValueError):
                bad.validate()


if __name__ == "__main__":
    unittest.main()
