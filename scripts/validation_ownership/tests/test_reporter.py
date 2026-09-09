"""Full graph/oracle contract tests; native execution is tested in sibling suites."""

import copy
import importlib
import io
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock
import sys

from scripts.validation_ownership import reporter
from scripts.validation_ownership.authority import AuthorityLoader, git_tree_entries
from scripts.validation_ownership.budget import ProbeBudget
from scripts.validation_ownership.graph_report import check


ROOT = Path(__file__).resolve().parents[3]


class AssetOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.generated_data.registry import REGISTRY

        cls.budget = ProbeBudget()
        cls.graph = reporter.load_json(ROOT / reporter.GRAPH_PATH)
        cls.schema = reporter.load_json(ROOT / reporter.SCHEMA_PATH)
        cls.oracle = reporter.load_json(ROOT / reporter.PROBE_ORACLE_PATH)
        cls.entries = git_tree_entries(ROOT, budget=cls.budget)
        cls.loader = AuthorityLoader(ROOT, cls.entries, "HEAD", budget=cls.budget)
        cls.generated_paths = {
            path for name in REGISTRY.all_names() for schema in (REGISTRY.resolve(name),)
            for path in (schema.default_source, schema.default_hand_source, schema.default_inventory_path)
            if path in cls.entries
        }
        cls.admission_sources = reporter._path_admission_sources(cls.loader, cls.generated_paths)
        cls.scratch = reporter.prepare_validation_scratch(ROOT)
        with mock.patch.object(sys, "path", [str(ROOT / "tools/gba-playtest"), *sys.path]):
            cls.playtest = importlib.import_module("gba_playtest")
            cls.presentation = importlib.import_module("run_banim_presentation_checks")
            cls.banim = importlib.import_module("run_banim_package_runtime_check")

    @classmethod
    def tearDownClass(cls):
        cls.budget.close()
        reporter.cleanup_validation_scratch(cls.scratch)

    def model(self, graph=None, entries=None):
        graph = self.graph if graph is None else graph
        authorities = {
            node["id"]: {"display": json.dumps(node["authority"], sort_keys=True)}
            for node in graph["nodes"] if node["kind"] == "evidence"
        }
        with mock.patch.object(reporter, "_validate_authorities", return_value=authorities), \
             mock.patch.object(reporter, "_generated_registry_records", return_value=([], self.generated_paths)), \
             mock.patch.object(reporter, "_path_admission_sources", return_value=self.admission_sources):
            schema_budget = ProbeBudget()
            try:
                reporter.validate_json_schema(graph, self.schema, self.schema, budget=schema_budget)
            finally:
                schema_budget.close()
            return reporter._validate_semantics(graph, self.loader, self.entries if entries is None else entries)

    def test_complete_partition_oracle_and_consumer_specific_assets(self):
        model = self.model()
        self.assertEqual(set(model["coverage"]), set(self.entries))
        reporter.validate_probe_oracle(self.oracle, self.graph, self.entries)
        measured = reporter._measure(self.oracle, self.graph, model)
        self.assertEqual(measured["false_positive_selections"], 0)
        self.assertEqual(measured["false_negative_selections"], 0)
        helper = reporter._resolve_path(
            "scripts/workflow_pilot/tests/coordinator_support.py", self.graph, model,
        )
        self.assertEqual(helper["surface"], "surface.host")
        self.assertEqual(helper["admission"], "exact-ownership-rule")
        self.assertEqual(
            {(owner["edge_type"], owner["evidence_id"]) for owner in helper["owners"]},
            {("owns-test", "owner.host-build"), ("adversarial-control", "owner.host-workflow")},
        )
        for path, surface, target in (
            ("graphics/titlescreen/title_main_background_1.png", "surface.title-visual", "expansion-modern-title-check"),
            ("assets/banim/lorm_sp1/script.txt", "surface.banim-package", "expansion-modern-banim-package-runtime-check"),
            ("graphics/banim/banim_lorm_sp1_sheet_0.png", "surface.banim-package", "expansion-modern-banim-package-runtime-check"),
            ("assets/portraits/eirika/metadata.json", "surface.portrait-package", "expansion-modern-portrait-package-runtime-check"),
            ("graphics/portrait/portrait_Eirika_chibi.png", "surface.portrait-package", "expansion-modern-portrait-package-runtime-check"),
        ):
            with self.subTest(path=path):
                actual = reporter._resolve_path(path, self.graph, model)
                self.assertEqual(actual["surface"], surface)
                runtime = [model["evidence"][owner["evidence_id"]]["authority"]
                           for owner in actual["owners"] if owner["edge_type"] == "target-scenario"]
                self.assertEqual(runtime, [{"kind": "make-target", "target": target}])
        for path in ("banim/banim_lorm_sp1_motion.s", "graphics/titlescreen/title_demon_king.png",
                     "sound/direct_sound_data.s", "assets/tmx/Ch2Map.tmx", "preview/tsa/MANIFEST.tsv",
                     ".github/manual-testing-handoff.json"):
            with self.subTest(path=path):
                actual = reporter._resolve_path(path, self.graph, model)
                self.assertNotIn("target-scenario", {owner["edge_type"] for owner in actual["owners"]})

    def test_every_edge_family_removal_and_live_owner_redirect_reject(self):
        for edge in self.graph["edges"]:
            with self.subTest(edge=edge["id"], mutation="remove"):
                changed = copy.deepcopy(self.graph)
                changed["edges"] = [item for item in changed["edges"] if item["id"] != edge["id"]]
                with self.assertRaises(reporter.OwnershipError):
                    model = self.model(changed)
                    reporter.validate_probe_oracle(self.oracle, changed, self.entries)
                    reporter._measure(self.oracle, changed, model)
            if edge["type"] == "depends-on":
                continue
            old = next(node for node in self.graph["nodes"] if node["id"] == edge["target"])
            replacement = next((node["id"] for node in self.graph["nodes"]
                                if node["kind"] == "evidence" and node["id"] != old["id"]
                                and node["evidence_type"] == old["evidence_type"]), None)
            if replacement:
                with self.subTest(edge=edge["id"], mutation="redirect"):
                    changed = copy.deepcopy(self.graph)
                    next(item for item in changed["edges"] if item["id"] == edge["id"])["target"] = replacement
                    with self.assertRaises(reporter.OwnershipError):
                        model = self.model(changed)
                        reporter.validate_probe_oracle(self.oracle, changed, self.entries)
                        reporter._measure(self.oracle, changed, model)

    def test_asset_implementation_and_profile_keep_complete_applicable_owners(self):
        model = self.model()
        pipeline = {
            ("generated-by", "owner.generator-assets"),
            ("drift-check", "owner.drift-assets"),
            ("generated-consumer", "owner.link-modern"),
        }
        implementation = {
            ("owns-test", "owner.host-assets"),
            ("adversarial-control", "owner.host-build"),
            ("compile-owner", "owner.compile-modern"),
            ("link-owner", "owner.link-modern"),
        } | pipeline
        paths = [
            path for path in self.entries
            if path.startswith("scripts/assets/") and not path.startswith("scripts/assets/tests/")
        ]
        self.assertIn("scripts/assets/manifest.py", paths)
        for path in paths:
            with self.subTest(path=path):
                selected = reporter._resolve_path(path, self.graph, model)
                self.assertEqual(selected["surface"], "surface.asset-generator")
                self.assertEqual(
                    {(owner["edge_type"], owner["evidence_id"]) for owner in selected["owners"]},
                    implementation,
                )
        profile = reporter._resolve_path("assets.mk", self.graph, model)
        configuration = {
            (edge["type"], edge["target"])
            for edge in self.graph["edges"] if edge["source"] == "surface.configuration"
        }
        self.assertEqual(profile["surface"], "surface.asset-profile")
        self.assertEqual(
            {(owner["edge_type"], owner["evidence_id"]) for owner in profile["owners"]},
            configuration | pipeline,
        )
        tests = reporter._resolve_path("scripts/assets/tests/test_manifest.py", self.graph, model)
        self.assertEqual(tests["surface"], "surface.host")
        authored = reporter._resolve_path("assets/tmx/Ch2Map.tmx", self.graph, model)
        self.assertIn("manual-handoff", {owner["edge_type"] for owner in authored["owners"]})
        path = "scripts/assets/unclassified_generator.py"
        entries = {**self.entries, path: reporter.GitTreeEntry(path, "100644", "blob", "0" * 40)}
        with self.assertRaisesRegex(reporter.OwnershipError, "semantic admission"):
            self.model(entries=entries)

    def test_asset_implementation_pipeline_removal_and_wrong_live_owners_reject(self):
        wrong_targets = {
            "generated-by": "owner.generator-generated",
            "drift-check": "owner.drift-generated",
            "generated-consumer": "owner.consumer-generated",
        }
        for surface in ("surface.asset-generator", "surface.asset-profile"):
            for kind, wrong in wrong_targets.items():
                edge = next(item for item in self.graph["edges"]
                            if item["source"] == surface and item["type"] == kind)
                for remove in (True, False):
                    with self.subTest(surface=surface, kind=kind, remove=remove):
                        graph = copy.deepcopy(self.graph)
                        if remove:
                            graph["edges"] = [item for item in graph["edges"] if item["id"] != edge["id"]]
                        else:
                            next(item for item in graph["edges"] if item["id"] == edge["id"])["target"] = wrong
                        with self.assertRaises(reporter.OwnershipError):
                            model = self.model(graph)
                            reporter.validate_probe_oracle(self.oracle, graph, self.entries)
                            reporter._measure(self.oracle, graph, model)

    def test_unknown_paths_modes_exclusions_and_prefix_admission_reject(self):
        model = self.model()
        for path in ("src/untracked.c", "../src/bm.c", "mgfembp", ".github/CODEOWNERS"):
            with self.subTest(path=path), self.assertRaises(reporter.OwnershipError):
                reporter._resolve_path(path, self.graph, model)
        for mode, kind in (("120000", "blob"), ("160000", "commit")):
            with self.subTest(mode=mode), self.assertRaises(reporter.OwnershipError):
                entries = {**self.entries, "src/bm.c": reporter.GitTreeEntry("src/bm.c", mode, kind, "0" * 40)}
                self.model(entries=entries)
        for path in (
            "src/foo.c", "scripts/unclassified.py", "docs/unclassified.md",
            "graphics/unclassified.png", "changelog_fragments/unclassified.json",
            "scripts/workflow_pilot/tests/unclassified_support.py",
        ):
            entries = {**self.entries, path: reporter.GitTreeEntry(path, "100644", "blob", "0" * 40)}
            with self.subTest(path=path), self.assertRaisesRegex(reporter.OwnershipError, "semantic admission"):
                self.model(entries=entries)
        path = "src/foo.c"
        entries = {**self.entries, path: reporter.GitTreeEntry(path, "100644", "blob", "0" * 40)}
        admitted = copy.deepcopy(self.graph)
        next(rule for rule in admitted["path_rules"] if rule["id"] == "paths.runtime")["include"].append(
            {"kind": "exact", "path": path},
        )
        result = reporter._resolve_path(path, admitted, self.model(admitted, entries))
        self.assertEqual(result["admission"], "exact-ownership-rule")
        self.assertEqual({owner["edge_type"] for owner in result["owners"]},
                         {"owns-test", "adversarial-control", "compile-owner", "link-owner", "target-scenario"})

    def test_ambiguous_rules_duplicate_owners_cycles_and_missing_dependents_reject(self):
        controls = []
        overlap = copy.deepcopy(self.graph)
        next(rule for rule in overlap["path_rules"] if rule["id"] == "paths.host")["include"].append(
            {"kind": "exact", "path": "src/bm.c"},
        )
        controls.append(overlap)
        duplicate = copy.deepcopy(self.graph)
        copied = copy.deepcopy(duplicate["edges"][0]); copied["id"] = "duplicate.edge"
        duplicate["edges"].append(copied); controls.append(duplicate)
        cycle = copy.deepcopy(self.graph)
        edge = next(item for item in cycle["edges"] if item["type"] == "depends-on")
        reverse = {"id": "reverse.depends", "type": "depends-on", "source": edge["target"],
                   "target": edge["source"], "reason": "Cyclic dependency"}
        cycle["edges"].append(reverse)
        next(node for node in cycle["nodes"] if node["id"] == reverse["source"])["dependencies"].append(reverse["target"])
        controls.append(cycle)
        missing = copy.deepcopy(self.graph)
        next(node for node in missing["nodes"] if node["id"] == edge["source"])["dependencies"] = []
        controls.append(missing)
        for graph in controls:
            with self.assertRaises(reporter.OwnershipError):
                self.model(graph)

    def test_oracle_seal_schema_types_and_semantic_ordering(self):
        reordered = copy.deepcopy(self.graph)
        reordered["edges"].reverse(); reordered["nodes"].reverse(); reordered["path_rules"].reverse()
        baseline = reporter._measure(self.oracle, self.graph, self.model())
        self.assertEqual(baseline["probes"], reporter._measure(self.oracle, reordered, self.model(reordered))["probes"])
        wrong = copy.deepcopy(self.oracle)
        wrong["seal"] = "0" * 64
        with self.assertRaises(reporter.OwnershipError):
            reporter.validate_probe_oracle(wrong, self.graph, self.entries)
        for graph in ({**self.graph, "unexpected": True}, {**self.graph, "schema_version": True}):
            with self.assertRaises(reporter.OwnershipError):
                self.model(graph)
        for trigger in (item for item in self.graph["lifecycle_events"] if item["type"] != "deletion_proof"):
            graph = copy.deepcopy(self.graph)
            next(item for item in graph["lifecycle_events"] if item["id"] == trigger["id"])["authority"] = "fabricated"
            with self.assertRaises(reporter.OwnershipError):
                self.model(graph)

    def test_all_112_domain_declarations_and_eight_jobs_are_preserved(self):
        domains = reporter.load_make_prerequisite_domains(self.loader, required=True)
        self.assertEqual(len(domains), 112)
        self.assertEqual(sum(item["kind"] == "tracked-fallback" for item in domains.values()), 111)
        self.assertEqual(domains["NODEP"]["values"], ["", "0", "1"])
        jobs, _ = reporter._workflow_authorities(self.loader, strict=True)
        self.assertEqual(len(jobs), 8)
        self.assertNotIn("patch-release", jobs)

    def test_packaging_sources_and_explicit_base_deletion_keep_complete_owners(self):
        model = self.model()
        expected = {("owns-test", "owner.host-build"), ("adversarial-control", "owner.host-workflow")}
        for path in ("scripts/modernize/package_ci_patch.sh", "tests/workflows/test_patch_release_workflow.py"):
            result = reporter._resolve_path(path, self.graph, model)
            self.assertEqual({(owner["edge_type"], owner["evidence_id"]) for owner in result["owners"]}, expected)
        path = "scripts/workflow_pilot/publisher_shell_contract.py"
        base = {path: reporter.GitTreeEntry(path, "100644", "blob", "0" * 40)}
        base_model = {**model, "entries": base}
        deletion = reporter._resolve_path(path, self.graph, model, base, base_model=base_model)
        self.assertEqual({(owner["edge_type"], owner["evidence_id"]) for owner in deletion["owners"]}, expected)
        self.assertTrue(all(owner["reason"] for owner in deletion["owners"]))
        with self.assertRaises(reporter.OwnershipError):
            reporter._resolve_path(path, self.graph, model, base)

    def test_title_check_has_asserted_framebuffer_behavior(self):
        scenario = self.playtest.load_scenario(ROOT / "tools/gba-playtest/scenarios/title-progression.json")
        for config in ("debug", "release"):
            expected = reporter.load_json(ROOT / f"tools/gba-playtest/fingerprints/title-progression-modern-{config}.json")
            self.assertEqual(len(scenario.checkpoints), len(expected["checkpoints"]))
            for checkpoint, captured in zip(scenario.checkpoints, expected["checkpoints"]):
                self.assertTrue(checkpoint.framebuffer)
                self.assertEqual((checkpoint.name, checkpoint.frame), (captured["name"], captured["frame"]))
                self.assertIn("framebuffer_hash", captured)
            self.assertEqual([], self.playtest.compare_fingerprints(expected, copy.deepcopy(expected), "behavior"))
            for index in range(len(scenario.checkpoints)):
                changed = copy.deepcopy(expected)
                del changed["checkpoints"][index]["framebuffer_hash"]
                self.assertTrue(self.playtest.compare_fingerprints(expected, changed, "behavior"))

    def test_banim_package_checker_requires_resource_and_lifecycle_consumption(self):
        package = self.banim.banim.load_package(
            str(ROOT), "assets/banim/lorm_sp1/package.json", "assets/banim/lorm_sp1/script.txt",
            {"assets/banim/lorm_sp1/package.json", "assets/banim/lorm_sp1/script.txt",
             "graphics/banim/banim_lorm_sp1_sheet_0.png"},
        )
        values = {
            "magic": 0x42505431, "selectionCount": 1, "originalIndex": 1, "defaultClassId": 0x5F,
            "aliasIndex": 500, "modeCount": len(package.mode_durations),
            "normalDuration": package.mode_durations["normal"], "totalDuration": sum(package.mode_durations.values()),
            "resourcesReady": 0x1F, "battleEntryCount": 1, "battleCompleteCount": 1,
            "selectedBattleIndex": 500, "runtimeDataConsumeCount": 1,
        }
        with tempfile.TemporaryDirectory(dir=self.scratch.path) as directory:
            output = Path(directory)
            for field in (None, "runtimeDataConsumeCount", "resourcesReady", "battleCompleteCount"):
                observed = dict(values)
                if field: observed[field] = 0
                capture = {"checkpoints": [
                    {"probes": [{"value": "0x0"} for _ in self.banim.PROBE_FIELDS]},
                    {"probes": [{"value": hex(observed[name])} for name in self.banim.PROBE_FIELDS]},
                ]}
                with mock.patch.object(sys, "argv", ["check", "--rom", str(output / "test.gba"),
                     "--elf", str(output / "test.elf"), "--out-dir", str(output)]), \
                     mock.patch.object(self.banim, "resolve_probe", return_value=0x02010000), \
                     mock.patch.object(self.banim, "generated_define", return_value=500), \
                     mock.patch.object(self.banim.gba_playtest, "capture", return_value=capture), \
                     mock.patch.object(sys, "stdout", io.StringIO()):
                    if field:
                        with self.assertRaisesRegex(RuntimeError, field): self.banim.main()
                    else: self.assertEqual(self.banim.main(), 0)

    def test_presentation_capture_is_policy_and_hp_not_asset_av_evidence(self):
        with tempfile.TemporaryDirectory(dir=self.scratch.path) as directory:
            output = Path(directory)
            scenarios = []
            def capture(command, **kwargs):
                scenarios.append(reporter.load_json(Path(command[command.index("--scenario") + 1])))
                checkpoints = []
                for hp, hit in ((15, 0), (0, 1)):
                    values = dict.fromkeys(self.presentation.CAPTURE_FIELDS, 0)
                    values.update(enemyMaxHp=15, enemyCurHp=hp, realHitPathObserved=hit)
                    checkpoints.append({"name": str(hp), "probes": [
                        {"value": hex(values[name])} for name in self.presentation.CAPTURE_FIELDS
                    ]})
                Path(command[command.index("--output") + 1]).write_text(json.dumps({"checkpoints": checkpoints}))
                return subprocess.CompletedProcess(command, 0, "", "")
            with mock.patch.object(self.presentation, "resolve_probe", return_value=(0x02010000, 0x02020000)), \
                 mock.patch.object(self.presentation.subprocess, "run", side_effect=capture):
                result = self.presentation.capture(output / "test.gba", output / "test.elf", "standard", output)
            self.assertEqual((result["enemyCurHpBefore"], result["enemyCurHpAfter"]), (15, 0))
            parsed = self.playtest.parse_scenario_data(scenarios[0])
            self.assertTrue(parsed.checkpoints)
            self.assertTrue(all(not checkpoint.framebuffer for checkpoint in parsed.checkpoints))


class FullRepositoryAcceptanceTests(unittest.TestCase):
    def test_actual_full_report_domain_partition_oracle_and_lifecycle(self):
        budget = ProbeBudget()
        try:
            result = check(ROOT, budget=budget)
            self.assertGreater(result["coverage"]["tracked_paths"], 1000)
            self.assertEqual(result["coverage"]["tracked_paths"],
                             result["coverage"]["owned_paths"] + result["coverage"]["fail_closed_exclusions"])
            self.assertEqual(result["measurement"]["false_positive_selections"], 0)
            self.assertEqual(result["measurement"]["false_negative_selections"], 0)
            self.assertEqual(len(result["artifact"]["executable_lifecycle"]), 3)
        finally:
            budget.close()


if __name__ == "__main__":
    unittest.main()
