"""Semantic schema/generator coverage for issue #90 autoplay strategies."""

import copy
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.generated_data.autoplaystrategies import generate, schema
from scripts.generated_data.chapterbundle import schema as chapterbundle_schema
from scripts.generated_data.chapterobjectives import schema as objectives_schema
from scripts.generated_data.diagnostics import DiagnosticCollector, GeneratedDataError
from scripts.generated_data.tests._util import fixture_path
from scripts.generated_data.units import schema as units_schema


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def strategy_fixture(name):
    return fixture_path("autoplaystrategies", name)


def _dependency_records(strategy_records):
    objective_records = objectives_schema.load_records(
        fixture_path(
            "chapterobjectives",
            "strategy_valid.json" if strategy_records["chapters"] else "valid.json",
        )
    )
    units = units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json"))
    chapter_bundles = chapterbundle_schema.load_records(
        fixture_path(
            "chapterobjectives",
            "strategy_bundle.json" if strategy_records["chapters"] else "ch2_bundle.json",
        )
    )

    if strategy_records["chapters"]:
        for chapter_id, bundles in chapter_bundles.by_chapter.items():
            chapter_bundle = bundles[0]
            owned_chapters = [
                chapter for chapter in strategy_records["chapters"]
                if chapter.chapter == chapter_id
            ]
            chapter_bundle.autoplay_strategies = chapterbundle_schema.TableRef(
                "autoplaystrategies",
                strategy_fixture("valid.json"),
                chapter_bundle.loc,
                [chapter.symbol for chapter in owned_chapters],
                [chapter.symbol_loc for chapter in owned_chapters],
                chapter_bundle.loc,
            )

    return {
        "chapterobjectives": objective_records,
        "units": units,
        "chapterbundle": chapter_bundles,
    }


def _validate(name):
    strategy_records = schema.load_records(strategy_fixture(name))
    diagnostics = DiagnosticCollector()
    schema.validate(strategy_records, diagnostics, _dependency_records(strategy_records))
    return strategy_records, diagnostics


class AutoplayStrategiesSchemaTests(unittest.TestCase):
    def _with_second_assigned_group(self, overlap):
        records = schema.load_records(strategy_fixture("valid.json"))
        dependencies = _dependency_records(records)
        objective_record = dependencies["chapterobjectives"][0]
        first_group = objective_record.groups[0]
        second_group = copy.deepcopy(first_group)
        second_group.id = "AI_GROUP_FIXTURE_OVERLAP"
        second_group.members = [copy.deepcopy(first_group.members[0])]
        if overlap == "character":
            second_group.members[0].unit_group = "UnitDef_Fixture_Alternate"
        elif overlap == "unit_group":
            second_group.members[0].character = "CHARACTER_FRANZ"
        elif overlap == "none":
            second_group.members[0].character = "CHARACTER_FRANZ"
            second_group.members[0].unit_group = "UnitDef_Fixture_Alternate"
        else:
            raise AssertionError("unknown overlap kind")
        objective_record.groups.append(second_group)

        assignment = copy.deepcopy(records["chapters"][0].group_assignments[0])
        assignment.group = second_group.id
        records["chapters"][0].group_assignments.append(assignment)
        return records, dependencies, second_group

    def test_reference_profiles_generate_c89_with_stable_ids(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, diagnostics.render())
        output = generate.generate_c_source(
            schema.AutoplayStrategiesTableSchema().configure_records(
                records,
                reference_profiles="1",
            ),
            strategy_fixture("valid.json"),
        )
        self.assertIn("gExpansionAutoplayStrategies", output)
        self.assertIn("gExpansionAutoplayStrategyBundles", output)
        self.assertIn("0x8A98AADD", output)
        self.assertIn("0x7F2C07B5", output)
        self.assertNotIn("//", output)

    def test_default_source_has_no_profiles_or_assignments(self):
        records = schema.load_records(os.path.join(REPO_ROOT, "src", "data", "autoplay_strategies.json"))
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, _dependency_records(records))
        self.assertTrue(diagnostics.ok, diagnostics.render())
        table = schema.AutoplayStrategiesTableSchema()
        disabled = table.configure_records(records, reference_profiles="0")
        output = generate.generate_c_source(disabled, "src/data/autoplay_strategies.json")
        self.assertIn("EXPANSION_AUTOPLAY_STRATEGY_CHAPTER_NONE", output)
        self.assertNotIn("ExpansionAutoplayStrategy_Aggressive", output)
        self.assertEqual(table.manifest_record_count(records), 2)

        enabled = table.configure_records(records, reference_profiles="1")
        output = generate.generate_c_source(enabled, "src/data/autoplay_strategies.json")
        self.assertIn("ExpansionAutoplayStrategy_Aggressive", output)
        self.assertIn("ExpansionAutoplayStrategy_ObjectiveFirst", output)

    def test_disabled_profile_keeps_non_reference_descriptor(self):
        records = schema.load_records(strategy_fixture("valid.json"))
        custom = copy.deepcopy(records["strategies"][0])
        custom.id = "AUTOPLAY_STRATEGY_CUSTOM"
        custom.callback = "ExpansionAutoplayStrategy_Custom"
        records["strategies"].append(custom)
        output = generate.generate_c_source(
            schema.AutoplayStrategiesTableSchema().configure_records(
                records,
                reference_profiles="0",
            ),
            strategy_fixture("valid.json"),
        )
        self.assertIn("ExpansionAutoplayStrategy_Custom", output)
        self.assertNotIn("ExpansionAutoplayStrategy_Aggressive", output)

    def test_cli_default_omits_reference_profiles(self):
        build_root = os.path.join(REPO_ROOT, "build")
        os.makedirs(build_root, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            temporary_path = Path(temporary)
            generated = temporary_path / "generated"
            inventory = temporary_path / "inventory.md"
            completed = subprocess.run(
                [
                    "python3",
                    "-m",
                    "scripts.generated_data",
                    "generate",
                    "--table",
                    "autoplaystrategies",
                    "--source",
                    strategy_fixture("valid.json"),
                    "--dep-source",
                    "chapterobjectives={}".format(
                        fixture_path("chapterobjectives", "strategy_valid.json")
                    ),
                    "--dep-source",
                    "chapterbundle={}".format(
                        fixture_path("chapterobjectives", "strategy_bundle.json")
                    ),
                    "--out-dir",
                    str(generated),
                    "--inventory",
                    str(inventory),
                    "--no-roundtrip",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            output = (generated / "data_autoplay_strategies.c").read_text(encoding="utf-8")
        self.assertNotIn("ExpansionAutoplayStrategy_Aggressive", output)
        self.assertIn("ExpansionAutoplayStrategy_TentativeFallback", output)

    def test_authored_strategy_bundle_requires_its_chapter_owner_declaration(self):
        records = schema.load_records(strategy_fixture("valid.json"))
        dependencies = _dependency_records(records)
        dependencies["chapterbundle"].by_chapter["CHAPTER_L_2"][0].autoplay_strategies.symbols = []
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                "is not declared by its owning chapter bundle" in error.message
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

    def test_strategy_owner_source_must_match_loaded_records(self):
        records = schema.load_records(strategy_fixture("valid.json"))
        dependencies = _dependency_records(records)
        owner = dependencies["chapterbundle"].by_chapter["CHAPTER_L_2"][0]
        owner.autoplay_strategies.source = strategy_fixture("invalid.json")
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                error.reference_path == "autoplayStrategies.source"
                and "does not match the loaded strategy source" in error.message
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

    def test_directory_source_tracks_member_origins_and_owner_boundaries(self):
        build_root = os.path.join(REPO_ROOT, "build")
        os.makedirs(build_root, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            source_dir = Path(temporary)
            primary = source_dir / "a_strategies.json"
            secondary = source_dir / "b_strategies.json"
            primary.write_bytes(Path(strategy_fixture("valid.json")).read_bytes())
            secondary.write_text(
                json.dumps({
                    "$schema": "fe8.autoplaystrategies.v1",
                    "strategies": [],
                    "chapters": [],
                }),
                encoding="utf-8",
            )

            records = schema.load_records(str(source_dir))
            self.assertEqual(
                records["source_paths"],
                (os.path.realpath(primary), os.path.realpath(secondary)),
            )
            self.assertEqual(records["chapters"][0].source_path, os.path.realpath(primary))

            dependencies = _dependency_records(records)
            owner = dependencies["chapterbundle"].by_chapter["CHAPTER_L_2"][0]
            owner.autoplay_strategies.source = str(source_dir)
            diagnostics = DiagnosticCollector()
            schema.validate(records, diagnostics, dependencies)
            self.assertTrue(diagnostics.ok, diagnostics.render())

            owner.autoplay_strategies.source = str(secondary)
            diagnostics = DiagnosticCollector()
            schema.validate(records, diagnostics, dependencies)
            self.assertTrue(
                any(
                    error.reference_path == "autoplayStrategies.source"
                    and "does not match the loaded strategy source" in error.message
                    for error in diagnostics.errors
                ),
                diagnostics.render(),
            )

            empty_dir = source_dir / "empty"
            empty_dir.mkdir()
            with self.assertRaisesRegex(
                GeneratedDataError,
                r"has no \*_strategies.json sources",
            ):
                schema.load_records(str(empty_dir))

    def test_assigned_group_character_overlap_is_order_independent_with_unit_override(self):
        records, dependencies, second_group = self._with_second_assigned_group(
            "character"
        )
        self.assertTrue(records["chapters"][0].unit_assignments)

        rendered = []
        locations = []
        for reverse in (False, True):
            candidate = copy.deepcopy(records)
            if reverse:
                candidate["chapters"][0].group_assignments.reverse()
            diagnostics = DiagnosticCollector()
            schema.validate(candidate, diagnostics, dependencies)
            errors = [
                error
                for error in diagnostics.errors
                if "belongs to both strategy-assigned groups" in error.message
            ]
            self.assertEqual(len(errors), 1, diagnostics.render())
            rendered.append(errors[0].message)
            locations.append(errors[0].location)
            self.assertIn("character 'CHARACTER_EIRIKA'", errors[0].message)
            self.assertIn("AI_GROUP_FIXTURE_EIRIKA", errors[0].message)
            self.assertIn("AI_GROUP_FIXTURE_OVERLAP", errors[0].message)
            self.assertIn(
                "groupAssignments[group=AI_GROUP_FIXTURE_EIRIKA]",
                errors[0].message,
            )
            self.assertIn(
                "groupAssignments[group=AI_GROUP_FIXTURE_OVERLAP]",
                errors[0].message,
            )
        self.assertEqual(rendered[0], rendered[1])
        self.assertEqual(
            locations,
            [second_group.members[0].character_loc] * 2,
        )

    def test_assigned_group_unit_group_overlap_is_rejected_even_for_same_strategy(self):
        records, dependencies, second_group = self._with_second_assigned_group(
            "unit_group"
        )
        assignments = records["chapters"][0].group_assignments
        self.assertEqual(assignments[0].strategy, assignments[1].strategy)
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        errors = [
            error
            for error in diagnostics.errors
            if "unit group 'UnitDef_Event_Ch2Ally' belongs to both" in error.message
        ]
        self.assertEqual(len(errors), 1, diagnostics.render())
        self.assertEqual(errors[0].location, second_group.members[0].unit_group_loc)

    def test_nonoverlap_and_unassigned_overlap_remain_valid(self):
        records, dependencies, _second_group = self._with_second_assigned_group(
            "none"
        )
        records["chapters"][0].group_assignments[1].strategy = (
            "AUTOPLAY_STRATEGY_AGGRESSIVE"
        )
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(diagnostics.ok, diagnostics.render())

        groups = {
            group.id: group for group in dependencies["chapterobjectives"][0].groups
        }

        def resolved_by_character(candidate, character):
            _strategies, chapters = schema.selected_records(
                schema.AutoplayStrategiesTableSchema().configure_records(
                    candidate,
                    reference_profiles="1",
                )
            )
            group_assignments = chapters[0][2]
            for assignment in group_assignments:
                if any(
                    member.character == character
                    for member in groups[assignment.group].members
                ):
                    return assignment.strategy
            return None

        reversed_records = copy.deepcopy(records)
        reversed_records["chapters"][0].group_assignments.reverse()
        generated_pairs = []
        for candidate in (records, reversed_records):
            output = generate.generate_c_source(
                schema.AutoplayStrategiesTableSchema().configure_records(
                    candidate,
                    reference_profiles="1",
                ),
                strategy_fixture("valid.json"),
            )
            generated_pairs.append(
                set(
                    re.findall(
                        r"\.groupId = (0x[0-9A-F]+),.*?"
                        r"\.strategyId = (0x[0-9A-F]+),",
                        output,
                        re.DOTALL,
                    )
                )
            )
        self.assertEqual(generated_pairs[0], generated_pairs[1])
        self.assertEqual(len(generated_pairs[0]), 2)

        for character, expected in (
            ("CHARACTER_EIRIKA", "AUTOPLAY_STRATEGY_OBJECTIVE_FIRST"),
            ("CHARACTER_FRANZ", "AUTOPLAY_STRATEGY_AGGRESSIVE"),
        ):
            self.assertEqual(resolved_by_character(records, character), expected)
            self.assertEqual(
                resolved_by_character(reversed_records, character),
                expected,
            )

        records, dependencies, _second_group = self._with_second_assigned_group(
            "character"
        )
        records["chapters"][0].group_assignments.pop()
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(diagnostics.ok, diagnostics.render())

    def test_directory_backed_identical_memberships_are_scoped_per_chapter(self):
        build_root = os.path.join(REPO_ROOT, "build")
        os.makedirs(build_root, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            source_dir = Path(temporary)
            source = json.loads(
                Path(strategy_fixture("valid.json")).read_text(encoding="utf-8")
            )
            l2_chapter = source["chapters"][0]
            l2_chapter["groupAssignments"][0]["group"] = "AI_GROUP_FIXTURE_L2"
            l2_chapter["unitAssignments"] = []
            l3_chapter = copy.deepcopy(l2_chapter)
            l3_chapter["chapter"] = "CHAPTER_L_3"
            l3_chapter["symbol"] = "AutoplayStrategies_FixtureL3"
            l3_chapter["groupAssignments"][0]["group"] = "AI_GROUP_FIXTURE_L3"
            (source_dir / "l2_strategies.json").write_text(
                json.dumps(source),
                encoding="utf-8",
            )
            (source_dir / "l3_strategies.json").write_text(
                json.dumps({
                    "$schema": "fe8.autoplaystrategies.v1",
                    "strategies": [],
                    "chapters": [l3_chapter],
                }),
                encoding="utf-8",
            )
            records = schema.load_records(str(source_dir))

            objectives = objectives_schema.load_records(
                fixture_path("chapterobjectives", "two_chapters.json")
            )
            objectives[1].groups[0].members[0].character = "CHARACTER_EIRIKA"
            objectives[1].groups[0].members[0].unit_group = (
                "UnitDef_Fixture_L2Ally"
            )
            chapter_bundles = chapterbundle_schema.load_records(
                fixture_path("chapterobjectives", "two_chapter_bundles")
            )
            for chapter_id, bundles in chapter_bundles.by_chapter.items():
                chapter_bundle = bundles[0]
                owned = [
                    chapter for chapter in records["chapters"]
                    if chapter.chapter == chapter_id
                ]
                chapter_bundle.autoplay_strategies = chapterbundle_schema.TableRef(
                    "autoplaystrategies",
                    str(source_dir),
                    chapter_bundle.loc,
                    [chapter.symbol for chapter in owned],
                    [chapter.symbol_loc for chapter in owned],
                    chapter_bundle.loc,
                )
            diagnostics = DiagnosticCollector()
            schema.validate(
                records,
                diagnostics,
                {
                    "chapterobjectives": objectives,
                    "chapterbundle": chapter_bundles,
                },
            )
            overlap_errors = [
                error for error in diagnostics.errors
                if "belongs to both strategy-assigned groups" in error.message
            ]
            self.assertEqual(overlap_errors, [], diagnostics.render())

    def test_unit_assignment_must_resolve_once_in_owning_chapter_data(self):
        records = schema.load_records(strategy_fixture("valid.json"))
        dependencies = _dependency_records(records)
        assignment = records["chapters"][0].unit_assignments[0]
        assignment.character = "CHARACTER_EPHRAIM"
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                "assignment character 'CHARACTER_EPHRAIM' resolves to 0" in error.message
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

        records = schema.load_records(strategy_fixture("valid.json"))
        dependencies = _dependency_records(records)
        owner = dependencies["chapterbundle"].by_chapter["CHAPTER_L_2"][0]
        owner.tables_by_name["units"].source = fixture_path(
            "chapterobjectives",
            "deps_units_duplicate_eirika.json",
        )
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                "assignment character 'CHARACTER_EIRIKA' resolves to 2" in error.message
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

    def test_selected_strategy_must_support_every_owned_objective_kind(self):
        records = schema.load_records(strategy_fixture("valid.json"))
        dependencies = _dependency_records(records)
        dependencies["chapterobjectives"][0].objectives[0].kind = "event_flag"
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                error.reference_path
                == "chapters[symbol=AutoplayStrategies_Fixture].groupAssignments[group=AI_GROUP_FIXTURE_EIRIKA].strategy"
                and "does not support chapter objective kind 'event_flag'" in error.message
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

    def test_reserved_zero_hash_rejects_before_registry_generation(self):
        records = schema.load_records(strategy_fixture("valid.json"))
        diagnostics = DiagnosticCollector()
        with mock.patch.object(schema, "stable_id_value", return_value=0):
            schema.validate(records, diagnostics, _dependency_records(records))
        self.assertTrue(
            any(
                "reserved runtime sentinel 0" in error.message
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )
        with mock.patch.object(generate, "stable_id_value", return_value=0):
            with self.assertRaisesRegex(ValueError, "reserved runtime sentinel 0"):
                generate.generate_c_source(
                    schema.AutoplayStrategiesTableSchema().configure_records(
                        records,
                        reference_profiles="1",
                    ),
                    strategy_fixture("valid.json"),
                )

    def test_multi_chapter_strategy_owners_resolve_by_chapter_index(self):
        records = schema.load_records(strategy_fixture("valid.json"))
        records["chapters"][0].group_assignments = []
        l3_record = copy.deepcopy(records["chapters"][0])
        l3_record.chapter = "CHAPTER_L_3"
        l3_record.symbol = "AutoplayStrategies_FixtureL3"
        l3_record.group_assignments = []
        l3_record.unit_assignments = []
        records["chapters"].append(l3_record)

        chapter_bundles = chapterbundle_schema.load_records(
            fixture_path("chapterobjectives", "two_chapter_bundles")
        )
        for chapter_id, bundles in chapter_bundles.by_chapter.items():
            chapter_bundle = bundles[0]
            owned_chapters = [
                chapter for chapter in records["chapters"] if chapter.chapter == chapter_id
            ]
            chapter_bundle.autoplay_strategies = chapterbundle_schema.TableRef(
                "autoplaystrategies",
                strategy_fixture("valid.json"),
                chapter_bundle.loc,
                [chapter.symbol for chapter in owned_chapters],
                [chapter.symbol_loc for chapter in owned_chapters],
                chapter_bundle.loc,
            )

        dependencies = {
            "chapterobjectives": objectives_schema.load_records(
                fixture_path("chapterobjectives", "two_chapters.json")
            ),
            "units": units_schema.load_records(
                fixture_path("chapterobjectives", "deps_units_two_chapters.json")
            ),
            "chapterbundle": chapter_bundles,
        }
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(diagnostics.ok, diagnostics.render())

        chapter_bundles.by_chapter["CHAPTER_L_3"][0].autoplay_strategies.symbols = []
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                error.reference_path == "chapters[symbol=AutoplayStrategies_FixtureL3].symbol"
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

    def test_invalid_profiles_and_assignments_fail_closed(self):
        _, diagnostics = _validate("invalid.json")
        self.assertFalse(diagnostics.ok)
        rendered = diagnostics.render()
        self.assertIn("frozen callback and capabilities", rendered)
        self.assertIn("requires at least one action capability", rendered)
        self.assertIn("undefined chapter AI group reference", rendered)
        self.assertIn("undefined strategy reference", rendered)
        self.assertIn("undefined character reference", rendered)
        self.assertIn("must not be EVFLAG_ALWAYS_FALSE", rendered)


if __name__ == "__main__":
    unittest.main()
