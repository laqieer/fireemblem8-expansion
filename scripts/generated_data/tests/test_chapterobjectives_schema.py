"""Semantic schema/generator coverage for issue #89 chapter objectives."""

import os
import unittest

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
    schema.validate(records, diagnostics, {"units": units})
    return records, diagnostics


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
            "hold_until_turn objective accepts only group, area, and untilTurn",
            "cannot complete by defeating its protected character",
        ):
            self.assertIn(expected, rendered)

    def test_invalid_non_ascii_id_reports_a_structured_diagnostic(self):
        records, diagnostics = _validate("valid.json")
        records[0].objectives[0].id = "OBJECTIVE_\u00e9"
        schema.validate(records, diagnostics, {
            "units": units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json"))
        })
        self.assertFalse(diagnostics.ok)
        self.assertIn("must use uppercase stable identifier spelling", diagnostics.render())


if __name__ == "__main__":
    unittest.main()
