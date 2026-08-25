"""Semantic schema/generator coverage for issue #89 chapter objectives."""

import copy
import os
import re
import unittest

from scripts.generated_data.chapterbundle import schema as chapterbundle_schema
from scripts.generated_data.chapterobjectives import generate, schema
from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.tests._util import fixture_path
from scripts.generated_data.units import schema as units_schema


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def objective_fixture(name):
    return fixture_path("chapterobjectives", name)


def _validate(name):
    records = schema.load_records(objective_fixture(name))
    diagnostics = DiagnosticCollector()
    units = units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json"))
    bundle_name = (
        "symbol_collision_bundles" if name == "symbol_collision.json" else "ch2_bundle.json"
    )
    chapter_bundle = chapterbundle_schema.load_records(objective_fixture(bundle_name))
    schema.validate(records, diagnostics, {"units": units, "chapterbundle": chapter_bundle})
    return records, diagnostics


def _two_chapter_records_and_dependencies():
    return (
        schema.load_records(objective_fixture("two_chapters.json")),
        {
            "units": units_schema.load_records(objective_fixture("deps_units_two_chapters.json")),
            "chapterbundle": chapterbundle_schema.load_records(objective_fixture("two_chapter_bundles")),
        },
    )


class ChapterObjectivesSchemaTests(unittest.TestCase):
    def test_valid_fixture_covers_every_initial_kind_and_generates_c89(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, diagnostics.render())
        self.assertEqual(
            [objective.kind for objective in records[0].objectives],
            ["event_flag", "reach_area", "defeat_group", "hold_until_turn", "protect"],
        )
        output = generate.generate_c_source(records, objective_fixture("valid.json"))
        self.assertIn("gExpansionChapterObjectiveBundles", output)
        self.assertIn("EXPANSION_CHAPTER_OBJECTIVE_PROTECT", output)
        self.assertIn("EXPANSION_CHAPTER_OBJECTIVE_HOLD_UNTIL_TURN", output)
        self.assertNotIn("//", output)

    def test_default_source_has_no_authored_chapter_records(self):
        records = schema.load_records(os.path.join(REPO_ROOT, "src", "data", "chapter_objectives.json"))
        diagnostics = DiagnosticCollector()
        units = units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json"))
        schema.validate(records, diagnostics, {"units": units})
        self.assertTrue(diagnostics.ok, diagnostics.render())
        output = generate.generate_c_source(records, "src/data/chapter_objectives.json")
        self.assertIn("EXPANSION_CHAPTER_OBJECTIVE_CHAPTER_NONE", output)
        self.assertNotIn("ChapterObjectives_", output)

    def test_empty_groups_and_contradictory_flags_fail_closed(self):
        for name, expected in (
            ("empty_group.json", "must contain at least one member"),
            ("contradiction.json", "eventFlag and deactivationFlag are contradictory"),
        ):
            with self.subTest(name=name):
                _, diagnostics = _validate(name)
                self.assertFalse(diagnostics.ok)
                self.assertIn(expected, diagnostics.render())

    def test_missing_exact_dependency_declarations_fail_closed(self):
        _, diagnostics = _validate("missing_dependency.json")
        self.assertFalse(diagnostics.ok)
        rendered = diagnostics.render()
        self.assertIn("character 'CHARACTER_EIRIKA' is used", rendered)
        self.assertIn("event flag 'EVFLAG_BATTLE_QUOTES' is used", rendered)
        self.assertIn("unit group 'UnitDef_Event_Ch2Ally' is used", rendered)

    def test_runtime_ids_are_stable_and_distinct(self):
        self.assertEqual(schema.stable_id_value("OBJECTIVE_FIXTURE_EVENT"), 0xA6F02B15)
        self.assertNotEqual(
            schema.stable_id_value("OBJECTIVE_FIXTURE_EVENT"),
            schema.stable_id_value("AI_GROUP_FIXTURE_EIRIKA"),
        )

    def test_group_member_symbols_are_delimited_by_stable_id_hash_and_cross_chapter_members_fail(self):
        records, diagnostics = _validate("symbol_collision.json")
        self.assertFalse(diagnostics.ok)
        ownership_errors = [
            error for error in diagnostics.errors
            if error.message == "unit group 'UnitDef_Event_Ch2Ally' is not owned by chapter 'CHAPTER_L_3'"
            and error.reference_path
            == "chapters[symbol=A_B].aiGroups[id=C].members[character=CHARACTER_EIRIKA].unitGroup"
        ]
        self.assertEqual(len(ownership_errors), 1, diagnostics.render())
        self.assertEqual(ownership_errors[0].location, records[1].groups[0].members[0].unit_group_loc)
        self.assertEqual(
            ownership_errors[0].reference_path,
            "chapters[symbol=A_B].aiGroups[id=C].members[character=CHARACTER_EIRIKA].unitGroup",
        )
        output = generate.generate_c_source(records, objective_fixture("symbol_collision.json"))
        members = re.findall(r"static const u8 (s[A-Za-z0-9_]+Members)\[\]", output)
        self.assertEqual(len(members), 2)
        self.assertEqual(len(set(members)), 2)

    def test_multiple_chapter_bundles_resolve_their_own_unit_groups(self):
        records, dependencies = _two_chapter_records_and_dependencies()
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(diagnostics.ok, diagnostics.render())
        self.assertEqual(
            sorted(dependencies["chapterbundle"].by_chapter),
            ["CHAPTER_L_2", "CHAPTER_L_3"],
        )
        l2_units = chapterbundle_schema.resolve_bundle_dependencies(
            dependencies["chapterbundle"][0]
        )["units"]
        l3_units = chapterbundle_schema.resolve_bundle_dependencies(
            dependencies["chapterbundle"][1]
        )["units"]
        self.assertEqual([group.symbol for group in l2_units], ["UnitDef_Fixture_L2Ally"])
        self.assertEqual([group.symbol for group in l3_units], ["UnitDef_Fixture_L3Ally"])

    def test_cross_owner_missing_and_duplicate_bundle_owners_fail_closed(self):
        records, dependencies = _two_chapter_records_and_dependencies()
        diagnostics = DiagnosticCollector()
        member = records[1].groups[0].members[0]
        member.unit_group = "UnitDef_Fixture_L2Ally"
        schema.validate(records, diagnostics, dependencies)
        cross_owner = [
            error for error in diagnostics.errors
            if error.reference_path
            == "chapters[symbol=ChapterObjectives_FixtureL3].aiGroups[id=AI_GROUP_FIXTURE_L3]"
            ".members[character=CHARACTER_SETH].unitGroup"
        ]
        self.assertEqual(len(cross_owner), 1, diagnostics.render())
        self.assertEqual(cross_owner[0].location, member.unit_group_loc)

        records, dependencies = _two_chapter_records_and_dependencies()
        diagnostics = DiagnosticCollector()
        dependencies["chapterbundle"] = chapterbundle_schema.ChapterBundleRecords(
            [dependencies["chapterbundle"][0]]
        )
        schema.validate(records, diagnostics, dependencies)
        missing_owner = [
            error for error in diagnostics.errors
            if error.message
            == "chapter objective bundle 'ChapterObjectives_FixtureL3' for chapter 'CHAPTER_L_3' "
            "has no owning chapter bundle"
        ]
        self.assertEqual(len(missing_owner), 1, diagnostics.render())
        self.assertEqual(missing_owner[0].location, records[1].chapter_loc)
        self.assertEqual(
            missing_owner[0].reference_path,
            "chapters[symbol=ChapterObjectives_FixtureL3].chapter",
        )

        records, dependencies = _two_chapter_records_and_dependencies()
        diagnostics = DiagnosticCollector()
        dependencies["chapterbundle"] = chapterbundle_schema.ChapterBundleRecords(
            [
                dependencies["chapterbundle"][0],
                dependencies["chapterbundle"][1],
                copy.deepcopy(dependencies["chapterbundle"][1]),
            ]
        )
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                error.reference_path == "bundles[chapter=CHAPTER_L_3].chapter"
                and "duplicate chapter bundle owner" in error.message
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

    def test_owner_dependency_sources_and_map_bounds_fail_closed(self):
        records, dependencies = _two_chapter_records_and_dependencies()
        diagnostics = DiagnosticCollector()
        dependencies["chapterbundle"][1].tables_by_name["units"].source = (
            "scripts/generated_data/tests/fixtures/chapterobjectives/deps_units_l2.json"
        )
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                error.reference_path
                == "chapters[symbol=ChapterObjectives_FixtureL3].aiGroups[id=AI_GROUP_FIXTURE_L3]"
                ".members[character=CHARACTER_SETH].unitGroup"
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

        records, dependencies = _two_chapter_records_and_dependencies()
        diagnostics = DiagnosticCollector()
        source = dependencies["chapterbundle"][1].tables_by_name["units"]
        source.source = "scripts/generated_data/tests/fixtures/chapterobjectives/missing_units.json"
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                error.reference_path == "bundles[chapter=CHAPTER_L_3].tables.units.source"
                and error.location == source.source_loc
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

        records, dependencies = _two_chapter_records_and_dependencies()
        diagnostics = DiagnosticCollector()
        l2_area = records[0].objectives[0].area
        l3_area = records[1].objectives[0].area
        l2_area.x_min = l2_area.x_max = 14
        l2_area.y_min = l2_area.y_max = 14
        l3_area.x_min = l3_area.x_max = 16
        l3_area.y_min = l3_area.y_max = 15
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(diagnostics.ok, diagnostics.render())

        records, dependencies = _two_chapter_records_and_dependencies()
        diagnostics = DiagnosticCollector()
        l2_area = records[0].objectives[0].area
        l2_area.x_min = 14
        l2_area.x_max = 15
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                error.reference_path
                == "chapters[symbol=ChapterObjectives_FixtureL2].objectives[id=OBJECTIVE_FIXTURE_L2_REACH].xMax"
                and error.location == l2_area.x_max_loc
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

        records, dependencies = _two_chapter_records_and_dependencies()
        diagnostics = DiagnosticCollector()
        l3_area = records[1].objectives[0].area
        l3_area.y_min = 15
        l3_area.y_max = 16
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                error.reference_path
                == "chapters[symbol=ChapterObjectives_FixtureL3].objectives[id=OBJECTIVE_FIXTURE_L3_REACH].yMax"
                and error.location == l3_area.y_max_loc
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

    def test_chapter_bundle_rejects_stale_objective_symbol(self):
        records, diagnostics = _validate("valid.json")
        chapter_bundle = chapterbundle_schema.load_records(objective_fixture("ch2_bundle.json"))
        chapter_bundle[0].chapter_objectives.symbols.append("ChapterObjectives_Stale")
        chapter_bundle[0].chapter_objectives.symbol_locs.append(
            chapter_bundle[0].chapter_objectives.symbol_locs[0]
        )
        schema.validate(
            records,
            diagnostics,
            {
                "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json")),
                "chapterbundle": chapter_bundle,
            },
        )
        self.assertTrue(
            any(
                error.reference_path == "chapterObjectives.symbols[ChapterObjectives_Stale]"
                for error in diagnostics.errors
            ),
            diagnostics.render(),
        )

    def test_owner_source_must_match_loaded_records_and_directory_order_is_stable(self):
        records = schema.load_records(objective_fixture("unrelated_objectives.json"))
        diagnostics = DiagnosticCollector()
        chapter_bundle = chapterbundle_schema.load_records(objective_fixture("ch2_bundle.json"))
        schema.validate(
            records,
            diagnostics,
            {
                "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json")),
                "chapterbundle": chapter_bundle,
            },
        )
        source_errors = [
            error for error in diagnostics.errors
            if error.reference_path == "bundles[chapter=CHAPTER_L_2].chapterObjectives.source"
        ]
        self.assertEqual(len(source_errors), 1, diagnostics.render())
        self.assertEqual(source_errors[0].location, chapter_bundle[0].chapter_objectives.source_loc)
        self.assertIn("unrelated_objectives.json", source_errors[0].message)

        directory_records = schema.load_records(objective_fixture("source_identity_objectives"))
        self.assertEqual(
            [os.path.basename(path) for path in directory_records.source_paths],
            ["a_objectives.json", "b_objectives.json"],
        )
        self.assertEqual(
            [os.path.basename(record.source_path) for record in directory_records],
            ["a_objectives.json", "b_objectives.json"],
        )

    def test_kind_specific_extras_and_protect_defeat_contradictions_fail_closed(self):
        records, diagnostics = _validate("valid.json")
        objectives = {objective.id: objective for objective in records[0].objectives}
        objectives["OBJECTIVE_FIXTURE_REACH"].protected_character = "CHARACTER_EIRIKA"
        objectives["OBJECTIVE_FIXTURE_REACH"].completion_objective = "OBJECTIVE_FIXTURE_EVENT"
        objectives["OBJECTIVE_FIXTURE_REACH"].event_flag = "EVFLAG_BATTLE_QUOTES"
        objectives["OBJECTIVE_FIXTURE_REACH"].until_turn = 2
        objectives["OBJECTIVE_FIXTURE_DEFEAT"].area = objectives["OBJECTIVE_FIXTURE_REACH"].area
        objectives["OBJECTIVE_FIXTURE_EVENT"].group = "AI_GROUP_FIXTURE_EIRIKA"
        objectives["OBJECTIVE_FIXTURE_HOLD"].event_flag = "EVFLAG_BATTLE_QUOTES"
        objectives["OBJECTIVE_FIXTURE_PROTECT"].completion_objective = "OBJECTIVE_FIXTURE_DEFEAT"
        schema.validate(records, diagnostics, {
            "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json"))
        })
        rendered = diagnostics.render()
        for expected in (
            "reach_area objective accepts only group and area",
            "defeat_group objective accepts only group",
            "event_flag objective accepts only eventFlag",
            "hold_until_turn objective accepts only group, area, untilTurn, and failureFlag",
            "completion chain reaches a defeat_group containing its protected character",
        ):
            self.assertIn(expected, rendered)

    def test_bundle_capacity_points_to_the_first_overflowing_record(self):
        records, diagnostics = _validate("valid.json")
        for _ in range(schema.BUNDLE_CAPACITY):
            records.append(copy.deepcopy(records[0]))

        schema.validate(
            records,
            diagnostics,
            {
                "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json")),
                "chapterbundle": chapterbundle_schema.load_records(objective_fixture("ch2_bundle.json")),
            },
        )
        capacity_errors = [
            error for error in diagnostics.errors
            if error.message == "chapter objective bundles count 33 exceeds fixed capacity 32"
        ]
        self.assertEqual(len(capacity_errors), 1, diagnostics.render())
        self.assertEqual(capacity_errors[0].location, records[schema.BUNDLE_CAPACITY].loc)
        self.assertEqual(capacity_errors[0].reference_path, "chapters[32]")

    def test_invalid_non_ascii_id_reports_a_structured_diagnostic(self):
        records, diagnostics = _validate("valid.json")
        records[0].objectives[0].id = "OBJECTIVE_\u00e9"
        schema.validate(records, diagnostics, {
            "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json"))
        })
        self.assertFalse(diagnostics.ok)
        self.assertIn("must use uppercase stable identifier spelling", diagnostics.render())

    def test_trailing_newlines_are_rejected_from_symbols_and_stable_ids(self):
        records, diagnostics = _validate("valid.json")
        records[0].symbol += "\n"
        records[0].groups[0].id += "\n"
        records[0].objectives[0].id += "\n"
        schema.validate(records, diagnostics, {
            "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json"))
        })
        rendered = diagnostics.render()
        self.assertFalse(diagnostics.ok)
        self.assertIn("bundle symbol", rendered)
        self.assertIn("must use uppercase stable identifier spelling", rendered)

    def test_protect_objective_rejects_null_character_sentinel(self):
        records, diagnostics = _validate("valid.json")
        objectives = {objective.id: objective for objective in records[0].objectives}
        objectives["OBJECTIVE_FIXTURE_PROTECT"].protected_character = "CHARACTER_NONE"
        schema.validate(records, diagnostics, {
            "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json"))
        })
        self.assertFalse(diagnostics.ok)
        self.assertIn("protectedCharacter must not be CHARACTER_NONE", diagnostics.render())

    def test_protect_requires_a_unique_failure_flag(self):
        records, diagnostics = _validate("valid.json")
        objectives = {objective.id: objective for objective in records[0].objectives}
        objectives["OBJECTIVE_FIXTURE_PROTECT"].failure_flag = None
        schema.validate(
            records,
            diagnostics,
            {
                "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json")),
                "chapterbundle": chapterbundle_schema.load_records(objective_fixture("ch2_bundle.json")),
            },
        )
        self.assertIn(
            "protect objective requires protectedCharacter, completionObjective, failureFlag, and completionFlag",
            diagnostics.render(),
        )

        records, diagnostics = _validate("valid.json")
        objectives = {objective.id: objective for objective in records[0].objectives}
        objectives["OBJECTIVE_FIXTURE_PROTECT"].completion_flag = "EVFLAG_ALWAYS_FALSE"
        schema.validate(
            records,
            diagnostics,
            {
                "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json")),
                "chapterbundle": chapterbundle_schema.load_records(objective_fixture("ch2_bundle.json")),
            },
        )
        self.assertIn("completionFlag must not be EVFLAG_ALWAYS_FALSE", diagnostics.render())

        records, diagnostics = _validate("valid.json")
        objectives = {objective.id: objective for objective in records[0].objectives}
        objectives["OBJECTIVE_FIXTURE_PROTECT"].failure_flag = "EVFLAG_5"
        schema.validate(
            records,
            diagnostics,
            {
                "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json")),
                "chapterbundle": chapterbundle_schema.load_records(objective_fixture("ch2_bundle.json")),
            },
        )
        self.assertIn(
            "duplicate objective failureFlag 'EVFLAG_5'",
            diagnostics.render(),
        )

        records, diagnostics = _validate("valid.json")
        objectives = {objective.id: objective for objective in records[0].objectives}
        objectives["OBJECTIVE_FIXTURE_PROTECT"].completion_flag = None
        schema.validate(
            records,
            diagnostics,
            {
                "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json")),
                "chapterbundle": chapterbundle_schema.load_records(objective_fixture("ch2_bundle.json")),
            },
        )
        self.assertIn(
            "protect objective requires protectedCharacter, completionObjective, failureFlag, and completionFlag",
            diagnostics.render(),
        )

        records, diagnostics = _validate("valid.json")
        duplicate = copy.deepcopy(records[0].objectives[-1])
        duplicate.id = "OBJECTIVE_FIXTURE_PROTECT_DUPLICATE"
        records[0].objectives.append(duplicate)
        schema.validate(
            records,
            diagnostics,
            {
                "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json")),
                "chapterbundle": chapterbundle_schema.load_records(objective_fixture("ch2_bundle.json")),
            },
        )
        self.assertIn(
            "duplicate protect completionFlag 'EVFLAG_WIN'",
            diagnostics.render(),
        )

    def test_protect_chain_flag_aliases_fail_closed(self):
        records, diagnostics = _validate("valid.json")
        objectives = {objective.id: objective for objective in records[0].objectives}
        parent = objectives["OBJECTIVE_FIXTURE_PROTECT"]
        child = copy.deepcopy(parent)
        child.id = "OBJECTIVE_FIXTURE_PROTECT_CHILD"
        child.failure_flag = "EVFLAG_5"
        child.completion_flag = "EVFLAG_BATTLE_QUOTES"
        parent.completion_objective = child.id
        parent.completion_flag = child.failure_flag
        records[0].objectives.append(child)
        schema.validate(
            records,
            diagnostics,
            {
                "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json")),
                "chapterbundle": chapterbundle_schema.load_records(objective_fixture("ch2_bundle.json")),
            },
        )
        self.assertIn(
            "protect completionFlag 'EVFLAG_5' aliases objectives[id=OBJECTIVE_FIXTURE_PROTECT_CHILD] failureFlag",
            diagnostics.render(),
        )

        records, diagnostics = _validate("valid.json")
        objectives = {objective.id: objective for objective in records[0].objectives}
        parent = objectives["OBJECTIVE_FIXTURE_PROTECT"]
        parent.failure_flag = "EVFLAG_BATTLE_QUOTES"
        schema.validate(
            records,
            diagnostics,
            {
                "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json")),
                "chapterbundle": chapterbundle_schema.load_records(objective_fixture("ch2_bundle.json")),
            },
        )
        self.assertIn(
            "protect failureFlag 'EVFLAG_BATTLE_QUOTES' aliases objectives[id=OBJECTIVE_FIXTURE_EVENT] eventFlag",
            diagnostics.render(),
        )

    def test_protect_objective_requires_a_validated_chapter_unit_group(self):
        records, diagnostics = _validate("valid.json")
        objectives = {objective.id: objective for objective in records[0].objectives}
        objectives["OBJECTIVE_FIXTURE_PROTECT"].protected_character = "CHARACTER_LYON"
        schema.validate(records, diagnostics, {
            "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json"))
        })
        self.assertFalse(diagnostics.ok)
        self.assertIn("must belong to a validated chapter unit group", diagnostics.render())

    def test_protect_completion_chain_rejects_a_terminal_defeat_of_the_protected_unit(self):
        records, diagnostics = _validate("valid.json")
        objectives = {objective.id: objective for objective in records[0].objectives}
        root = objectives["OBJECTIVE_FIXTURE_PROTECT"]
        intermediate = objectives["OBJECTIVE_FIXTURE_HOLD"]
        root.completion_objective = intermediate.id
        intermediate.kind = "protect"
        intermediate.group = None
        intermediate.area = None
        intermediate.until_turn = None
        intermediate.protected_character = "CHARACTER_LYON"
        intermediate.protected_character_loc = intermediate.id_loc
        intermediate.completion_objective = "OBJECTIVE_FIXTURE_DEFEAT"
        intermediate.completion_objective_loc = intermediate.id_loc
        schema.validate(records, diagnostics, {
            "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json"))
        })
        self.assertFalse(diagnostics.ok)
        self.assertIn(
            "completion chain reaches a defeat_group containing its protected character",
            diagnostics.render(),
        )


if __name__ == "__main__":
    unittest.main()
