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
                commands = [shlex.split(line.lstrip("\t").lstrip("@+"))
                            for line in rule.replace("\\\n", " ").splitlines()
                            if line.startswith("\t")]
                self.assertIn(
                    ["MODERN_CC=$(MODERN_CC)", "MODERN_NM=$(MODERN_NM)",
                     "MODERN_SIZE=$(MODERN_SIZE)", "$(PYTHON)", "-m", "unittest",
                     "scripts.workflow_pilot.tests.arm_review_subjects", "-v"],
                    commands)

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
            *(replace(good, kind=kind) for kind in ("native", "parsed", "arm-object", None)),
        ):
            with self.subTest(observation=bad), self.assertRaises(ValueError):
                bad.validate()

    def test_worker_record_types_and_missing_fields_reject_before_admission(self):
        members = self.tools.members(self.scope("session"))
        payload = json.dumps([item.probe for item in members]).encode()
        run = subprocess.run
        captured = []

        def capture(*args, **kwargs):
            completed = run(*args, **kwargs)
            if kwargs.get("input") == payload:
                captured.append(json.loads(completed.stdout))
            return completed

        with patch.object(subprocess, "run", side_effect=capture):
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
        ):
            malformed.append({**good[0], field: value})
        malformed.append({**good[0], "verdict": "unavailable", "checks": 1})
        for row in malformed:
            with self.subTest(row=row):
                raw = json.dumps([row, *good[1:]]).encode()

                def corrupt(*args, **kwargs):
                    if kwargs.get("input") == payload:
                        return subprocess.CompletedProcess(args[0], 0, raw, b"")
                    return run(*args, **kwargs)

                with patch.object(subprocess, "run", side_effect=corrupt):
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
                    run = subprocess.run
                    captured = []

                    def capture(*args, **kwargs):
                        completed = run(*args, **kwargs)
                        if kwargs.get("input") == payload:
                            captured.extend(json.loads(completed.stdout))
                        return completed

                    with patch.object(subprocess, "run", side_effect=capture):
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
