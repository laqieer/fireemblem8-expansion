"""Semantic schema/generator coverage for issue #90 autoplay strategies."""

import os
import unittest

from scripts.generated_data.autoplaystrategies import generate, schema
from scripts.generated_data.chapterbundle import schema as chapterbundle_schema
from scripts.generated_data.chapterobjectives import schema as objectives_schema
from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.tests._util import fixture_path
from scripts.generated_data.units import schema as units_schema


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def strategy_fixture(name):
    return fixture_path("autoplaystrategies", name)


def _dependency_records(strategy_records):
    objective_records = objectives_schema.load_records(
        fixture_path("chapterobjectives", "valid.json")
    )
    units = units_schema.load_records(os.path.join(REPO_ROOT, "src", "data", "ch2_units.json"))
    chapter_bundle = chapterbundle_schema.load_records(
        fixture_path("chapterobjectives", "ch2_bundle.json")
    )

    if strategy_records["chapters"]:
        owned_chapters = [
            chapter for chapter in strategy_records["chapters"]
            if chapter.chapter == chapter_bundle.chapter.id
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
        "chapterbundle": chapter_bundle,
    }


def _validate(name):
    strategy_records = schema.load_records(strategy_fixture(name))
    diagnostics = DiagnosticCollector()
    schema.validate(strategy_records, diagnostics, _dependency_records(strategy_records))
    return strategy_records, diagnostics


class AutoplayStrategiesSchemaTests(unittest.TestCase):
    def test_reference_profiles_generate_c89_with_stable_ids(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, diagnostics.render())
        output = generate.generate_c_source(records, strategy_fixture("valid.json"))
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
        output = generate.generate_c_source(records, "src/data/autoplay_strategies.json")
        self.assertIn("EXPANSION_AUTOPLAY_STRATEGY_CHAPTER_NONE", output)
        self.assertNotIn("ExpansionAutoplayStrategy_Aggressive", output)

    def test_authored_strategy_bundle_requires_its_chapter_owner_declaration(self):
        records = schema.load_records(strategy_fixture("valid.json"))
        dependencies = _dependency_records(records)
        dependencies["chapterbundle"].autoplay_strategies.symbols = []
        diagnostics = DiagnosticCollector()
        schema.validate(records, diagnostics, dependencies)
        self.assertTrue(
            any(
                "is not declared by its owning chapter bundle" in error.message
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
