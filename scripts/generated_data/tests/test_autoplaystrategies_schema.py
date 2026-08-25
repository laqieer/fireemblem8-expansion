"""Semantic schema/generator coverage for issue #90 autoplay strategies."""

import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

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
        with tempfile.TemporaryDirectory(dir=os.path.join(REPO_ROOT, "build")) as temporary:
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
