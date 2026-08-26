import json
import os
import shutil
import unittest

from scripts.generated_data.tests._util import fixture_path, scratch_dir
from scripts.generated_data.tests.test_cli import run_cli


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


class CliUnitsTests(unittest.TestCase):
    def test_real_ch2_units_source_validates_and_roundtrips_clean(self):
        code, out, err = run_cli(["validate", "--table", "units"])
        self.assertEqual(code, 0, msg=out + err)

    def test_generate_writes_c_and_inventory(self):
        with scratch_dir() as tmp:
            out_dir = os.path.join(tmp, "out")
            inventory_path = os.path.join(tmp, "inventory.md")
            code, out, err = run_cli([
                "generate", "--table", "units",
                "--source", fixture_path("units", "valid.json"),
                "--out-dir", out_dir,
                "--inventory", inventory_path,
                "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)
            generated_file = os.path.join(out_dir, "data_ch2_units.c")
            self.assertTrue(os.path.exists(generated_file))
            self.assertTrue(os.path.exists(inventory_path))
            with open(generated_file) as f:
                self.assertIn("UnitDef_Fixture_Test", f.read())

    def test_check_real_ch2_units_table_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "units"])
        self.assertEqual(code, 0, msg=out + err)


class CliShopsTests(unittest.TestCase):
    def test_real_ch2_shops_source_validates_and_roundtrips_clean(self):
        code, out, err = run_cli(["validate", "--table", "shops"])
        self.assertEqual(code, 0, msg=out + err)

    def test_check_real_ch2_shops_table_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "shops"])
        self.assertEqual(code, 0, msg=out + err)


class CliTrapsTests(unittest.TestCase):
    def test_real_ch2_traps_source_validates_and_roundtrips_clean(self):
        code, out, err = run_cli(["validate", "--table", "traps"])
        self.assertEqual(code, 0, msg=out + err)

    def test_check_real_ch2_traps_table_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "traps"])
        self.assertEqual(code, 0, msg=out + err)


class CliEventScriptsTests(unittest.TestCase):
    def test_real_ch2_eventscripts_source_validates_clean(self):
        code, out, err = run_cli(["validate", "--table", "eventscripts"])
        self.assertEqual(code, 0, msg=out + err)

    def test_generate_skips_c_output_for_metadata_only_table(self):
        with scratch_dir() as tmp:
            out_dir = os.path.join(tmp, "out")
            inventory_path = os.path.join(tmp, "inventory.md")
            code, out, err = run_cli([
                "generate", "--table", "eventscripts",
                "--source", fixture_path("eventscripts", "valid.json"),
                "--out-dir", out_dir,
                "--inventory", inventory_path,
                "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)
            self.assertIn("skip: table 'eventscripts' is metadata-only; no C output generated", out)
            self.assertTrue(os.path.exists(inventory_path))
            self.assertFalse(os.listdir(out_dir) if os.path.isdir(out_dir) else False)

    def test_check_real_ch2_eventscripts_table_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "eventscripts"])
        self.assertEqual(code, 0, msg=out + err)


def _eventlists_dep_source_args():
    args = []
    for name in ("units", "shops", "traps", "eventscripts"):
        args += ["--dep-source", "{}={}".format(name, fixture_path("eventlists", "deps_{}.json".format(name)))]
    return args


class CliEventListsTests(unittest.TestCase):
    def test_validate_tutorial_wrong_count_fails(self):
        code, out, err = run_cli([
            "validate", "--table", "eventlists",
            "--source", fixture_path("eventlists", "tutorial_wrong_count.json"),
            "--no-roundtrip",
        ] + _eventlists_dep_source_args())
        self.assertEqual(code, 1)
        self.assertIn("must have exactly 30 entries", err)

    def test_real_ch2_eventlists_source_validates_and_roundtrips_clean(self):
        code, out, err = run_cli(["validate", "--table", "eventlists"])
        self.assertEqual(code, 0, msg=out + err)

    def test_generate_writes_c_and_inventory(self):
        with scratch_dir() as tmp:
            out_dir = os.path.join(tmp, "out")
            inventory_path = os.path.join(tmp, "inventory.md")
            code, out, err = run_cli([
                "generate", "--table", "eventlists",
                "--source", fixture_path("eventlists", "valid.json"),
                "--out-dir", out_dir,
                "--inventory", inventory_path,
                "--no-roundtrip",
            ] + _eventlists_dep_source_args())
            self.assertEqual(code, 0, msg=out + err)
            generated_file = os.path.join(out_dir, "data_ch2_eventlists.c")
            self.assertTrue(os.path.exists(generated_file))
            self.assertTrue(os.path.exists(inventory_path))
            with open(generated_file) as f:
                content = f.read()
                self.assertIn("EventListScr_EL_Turn", content)
                self.assertIn("CONST_DATA struct ChapterEventGroup ELEvents", content)

    def test_strategy_helper_uses_optional_strategy_validation_input(self):
        with scratch_dir() as tmp:
            with open(fixture_path("eventlists", "helpers_valid.json"), encoding="utf-8") as handle:
                source = json.load(handle)
            source["helperScripts"][0]["entries"].append(
                {
                    "helper": "strategy",
                    "operation": "activate",
                    "args": [
                        "AUTOPLAY_STRATEGY_OBJECTIVE_FIRST",
                        "EVFLAG_HIDE_BLINKING_ICON",
                    ],
                }
            )
            source["helperScripts"][0]["entries"].append(
                {
                    "helper": "strategy",
                    "operation": "deactivate",
                    "args": [
                        "AUTOPLAY_STRATEGY_OBJECTIVE_FIRST",
                        "EVFLAG_HIDE_BLINKING_ICON",
                    ],
                }
            )
            source_path = os.path.join(tmp, "strategy-helper.json")
            with open(source_path, "w", encoding="utf-8") as handle:
                json.dump(source, handle)
            with open(
                fixture_path("chapterbundle", "valid.json"),
                encoding="utf-8",
            ) as handle:
                bundle = json.load(handle)
            bundle["chapter"]["id"] = "CHAPTER_L_2"
            bundle_path = os.path.join(tmp, "strategy-helper-bundle.json")
            with open(bundle_path, "w", encoding="utf-8") as handle:
                json.dump(bundle, handle)
            code, out, err = run_cli(
                [
                    "validate",
                    "--table",
                    "eventlists",
                    "--source",
                    source_path,
                    "--no-roundtrip",
                    "--dep-source",
                    "autoplaystrategies={}".format(
                        fixture_path("autoplaystrategies", "valid.json")
                    ),
                    "--dep-source",
                    "chapterbundle={}".format(bundle_path),
                ]
                + _eventlists_dep_source_args()
            )
            self.assertEqual(code, 0, msg=out + err)

    def test_check_real_ch2_eventlists_table_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "eventlists"])
        self.assertEqual(code, 0, msg=out + err)


class CliChapterBundleTests(unittest.TestCase):
    """chapterbundle has no dependency_tables() CLI override needed for
    these fixtures: its 6 declared dependency tables (units/shops/traps/
    eventscripts/eventlists/supports) all fall back to their own real,
    committed default_source when --dep-source isn't given, so a
    bundle-level fixture only needs to vary the bundle JSON itself (the
    chapters.h/chapter_settings.json/gChapterDataAssetTable cross-check is
    always against the real repo files too -- CLI validate() has no way to
    override those paths, unlike the direct schema-level tests in
    test_chapterbundle_schema.py)."""

    def test_validate_real_ch2_bundle_passes(self):
        code, out, err = run_cli(["validate", "--table", "chapterbundle"])
        self.assertEqual(code, 0, msg=out + err)
        self.assertIn("OK:", out)

    def test_validate_valid_fixture_copy_passes(self):
        code, out, err = run_cli([
            "validate", "--table", "chapterbundle",
            "--source", fixture_path("chapterbundle", "cli_valid.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 0, msg=out + err)

    def test_cli_validates_declared_chapter_objectives_source_before_inventory(self):
        with scratch_dir() as tmp:
            with open(fixture_path("chapterbundle", "cli_valid.json"), encoding="utf-8") as handle:
                bundle = json.load(handle)
            objective_source = fixture_path("chapterobjectives", "valid.json")
            bundle["chapterObjectives"] = {
                "source": objective_source,
                "symbols": ["ChapterObjectives_Fixture"],
            }
            valid_path = os.path.join(tmp, "bundle-with-objectives.json")
            with open(valid_path, "w", encoding="utf-8") as handle:
                json.dump(bundle, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle", "--source", valid_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)

            stale = json.loads(json.dumps(bundle))
            stale["chapterObjectives"]["symbols"] = ["ChapterObjectives_Stale"]
            stale_path = os.path.join(tmp, "bundle-with-stale-objective.json")
            with open(stale_path, "w", encoding="utf-8") as handle:
                json.dump(stale, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle", "--source", stale_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn("chapterObjectives.symbols[ChapterObjectives_Stale]", err)

            missing = json.loads(json.dumps(bundle))
            missing["chapterObjectives"]["source"] = os.path.join(tmp, "missing-objectives.json")
            missing_path = os.path.join(tmp, "bundle-with-missing-objective.json")
            with open(missing_path, "w", encoding="utf-8") as handle:
                json.dump(missing, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle", "--source", missing_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn("chapterObjectives.source", err)
            self.assertIn("missing-objectives.json", err)

            out_dir = os.path.join(tmp, "missing-objective-out")
            inventory_path = os.path.join(tmp, "missing-objective.md")
            code, out, err = run_cli([
                "generate", "--table", "chapterbundle", "--source", missing_path,
                "--out-dir", out_dir, "--inventory", inventory_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn("chapterObjectives.source", err)
            self.assertFalse(os.path.exists(inventory_path))

    def test_cli_validates_declared_autoplay_strategy_source_before_inventory(self):
        with scratch_dir() as tmp:
            with open(fixture_path("chapterbundle", "cli_valid.json"), encoding="utf-8") as handle:
                bundle = json.load(handle)
            strategy_source = fixture_path("autoplaystrategies", "valid.json")
            bundle["autoplayStrategies"] = {
                "source": strategy_source,
                "symbols": ["AutoplayStrategies_Fixture"],
            }
            valid_path = os.path.join(tmp, "bundle-with-strategies.json")
            with open(valid_path, "w", encoding="utf-8") as handle:
                json.dump(bundle, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle", "--source", valid_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)

            out_dir = os.path.join(tmp, "strategy-out")
            inventory_path = os.path.join(tmp, "strategy-inventory.md")
            code, out, err = run_cli([
                "generate", "--table", "chapterbundle", "--source", valid_path,
                "--out-dir", out_dir, "--inventory", inventory_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)
            with open(inventory_path, encoding="utf-8") as handle:
                inventory = handle.read()
            self.assertIn("| autoplaystrategies |", inventory)
            self.assertIn("fixtures/autoplaystrategies/valid.json", inventory)

            stale = json.loads(json.dumps(bundle))
            stale["autoplayStrategies"]["symbols"] = ["AutoplayStrategies_Stale"]
            stale_path = os.path.join(tmp, "bundle-with-stale-strategy.json")
            with open(stale_path, "w", encoding="utf-8") as handle:
                json.dump(stale, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle", "--source", stale_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn(
                "autoplayStrategies.symbols[AutoplayStrategies_Stale]",
                err,
            )

            undeclared = json.loads(json.dumps(bundle))
            undeclared["autoplayStrategies"]["symbols"] = []
            undeclared_path = os.path.join(tmp, "bundle-with-undeclared-strategy.json")
            with open(undeclared_path, "w", encoding="utf-8") as handle:
                json.dump(undeclared, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle",
                "--source", undeclared_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn(
                "contains chapter 'CHAPTER_L_2' symbol 'AutoplayStrategies_Fixture'",
                err,
            )

            wrong = json.loads(json.dumps(bundle))
            wrong["autoplayStrategies"]["source"] = "src/data/autoplay_strategies.json"
            wrong_path = os.path.join(tmp, "bundle-with-wrong-strategy-source.json")
            with open(wrong_path, "w", encoding="utf-8") as handle:
                json.dump(wrong, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle", "--source", wrong_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn(
                "is not a record for chapter 'CHAPTER_L_2'",
                err,
            )

            missing = json.loads(json.dumps(bundle))
            missing["autoplayStrategies"]["source"] = os.path.join(
                tmp, "missing-strategies.json"
            )
            missing_path = os.path.join(tmp, "bundle-with-missing-strategy.json")
            with open(missing_path, "w", encoding="utf-8") as handle:
                json.dump(missing, handle)
            code, out, err = run_cli([
                "generate", "--table", "chapterbundle", "--source", missing_path,
                "--out-dir", os.path.join(tmp, "missing-strategy-out"),
                "--inventory", os.path.join(tmp, "missing-strategy.md"),
                "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn("autoplayStrategies.source", err)
            self.assertFalse(os.path.exists(os.path.join(tmp, "missing-strategy.md")))

    def test_cli_directory_autoplay_source_validates_and_hashes_every_member(self):
        def write_source(path, chapter, symbol):
            chapters = []
            if chapter is not None:
                chapters.append({
                    "chapter": chapter,
                    "symbol": symbol,
                    "groupAssignments": [],
                    "unitAssignments": [],
                })
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "$schema": "fe8.autoplaystrategies.v1",
                    "strategies": [],
                    "chapters": chapters,
                }, handle)

        with scratch_dir() as tmp:
            with open(fixture_path("chapterbundle", "cli_valid.json"), encoding="utf-8") as handle:
                bundle = json.load(handle)
            strategy_dir = os.path.join(tmp, "strategy-sources")
            os.mkdir(strategy_dir)
            source_a = os.path.join(strategy_dir, "a_strategies.json")
            source_b = os.path.join(strategy_dir, "b_strategies.json")
            write_source(source_a, "CHAPTER_L_2", "AutoplayStrategies_SourceA")
            write_source(source_b, "CHAPTER_L_3", "AutoplayStrategies_SourceB")
            bundle["autoplayStrategies"] = {
                "source": strategy_dir,
                "symbols": ["AutoplayStrategies_SourceA"],
            }
            bundle_path = os.path.join(tmp, "bundle-with-strategy-directory.json")
            with open(bundle_path, "w", encoding="utf-8") as handle:
                json.dump(bundle, handle)

            code, out, err = run_cli([
                "validate", "--table", "chapterbundle", "--source", bundle_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)

            first_inventory = os.path.join(tmp, "strategy-directory-first.md")
            code, out, err = run_cli([
                "generate", "--table", "chapterbundle", "--source", bundle_path,
                "--out-dir", os.path.join(tmp, "first-out"),
                "--inventory", first_inventory, "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)
            with open(first_inventory, encoding="utf-8") as handle:
                first_report = handle.read()
            self.assertIn("a_strategies.json, ", first_report)
            self.assertIn("b_strategies.json", first_report)
            first_strategy_row = next(
                line for line in first_report.splitlines()
                if line.startswith("| autoplaystrategies |")
            )

            with open(source_b, "a", encoding="utf-8") as handle:
                handle.write("\n")
            second_inventory = os.path.join(tmp, "strategy-directory-second.md")
            code, out, err = run_cli([
                "generate", "--table", "chapterbundle", "--source", bundle_path,
                "--out-dir", os.path.join(tmp, "second-out"),
                "--inventory", second_inventory, "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)
            with open(second_inventory, encoding="utf-8") as handle:
                second_report = handle.read()
            second_strategy_row = next(
                line for line in second_report.splitlines()
                if line.startswith("| autoplaystrategies |")
            )
            self.assertNotEqual(first_strategy_row, second_strategy_row)

            mismatch = json.loads(json.dumps(bundle))
            mismatch["autoplayStrategies"]["symbols"] = ["AutoplayStrategies_SourceB"]
            mismatch_path = os.path.join(tmp, "bundle-with-strategy-member-mismatch.json")
            with open(mismatch_path, "w", encoding="utf-8") as handle:
                json.dump(mismatch, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle",
                "--source", mismatch_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn(
                "autoplayStrategies.symbols[AutoplayStrategies_SourceB]",
                err,
            )

            undeclared = json.loads(json.dumps(bundle))
            undeclared["autoplayStrategies"]["symbols"] = []
            undeclared_path = os.path.join(tmp, "bundle-with-undeclared-directory-strategy.json")
            with open(undeclared_path, "w", encoding="utf-8") as handle:
                json.dump(undeclared, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle",
                "--source", undeclared_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn(
                "contains chapter 'CHAPTER_L_2' symbol 'AutoplayStrategies_SourceA'",
                err,
            )

            missing = json.loads(json.dumps(bundle))
            missing["autoplayStrategies"] = {
                "source": os.path.join(tmp, "missing-strategy-sources"),
                "symbols": [],
            }
            missing_path = os.path.join(tmp, "bundle-with-missing-strategy-directory.json")
            with open(missing_path, "w", encoding="utf-8") as handle:
                json.dump(missing, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle",
                "--source", missing_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn("autoplayStrategies.source", err)

            wrong_dir = os.path.join(tmp, "wrong-strategy-sources")
            os.mkdir(wrong_dir)
            shutil.copyfile(
                fixture_path("chapterbundle", "deps_units.json"),
                os.path.join(wrong_dir, "wrong_strategies.json"),
            )
            wrong = json.loads(json.dumps(bundle))
            wrong["autoplayStrategies"] = {
                "source": wrong_dir,
                "symbols": [],
            }
            wrong_path = os.path.join(tmp, "bundle-with-wrong-strategy-directory.json")
            with open(wrong_path, "w", encoding="utf-8") as handle:
                json.dump(wrong, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle",
                "--source", wrong_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn("autoplayStrategies.source", err)
            self.assertIn("unexpected $schema", err)

    def test_cli_directory_chapter_objectives_source_validates_and_generates(self):
        with scratch_dir() as tmp:
            with open(fixture_path("chapterbundle", "cli_valid.json"), encoding="utf-8") as handle:
                bundle = json.load(handle)
            objective_directory = os.path.join(tmp, "objective-sources")
            os.mkdir(objective_directory)
            source_directory = fixture_path("chapterobjectives", "source_identity_objectives")
            for name in ("a_objectives.json", "b_objectives.json"):
                shutil.copyfile(
                    os.path.join(source_directory, name),
                    os.path.join(objective_directory, name),
                )
            bundle["chapterObjectives"] = {
                "source": objective_directory,
                "symbols": ["ChapterObjectives_SourceA"],
            }
            valid_path = os.path.join(tmp, "bundle-with-objective-directory.json")
            with open(valid_path, "w", encoding="utf-8") as handle:
                json.dump(bundle, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle", "--source", valid_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)

            out_dir = os.path.join(tmp, "out")
            inventory_path = os.path.join(tmp, "inventory.md")
            code, out, err = run_cli([
                "generate", "--table", "chapterbundle", "--source", valid_path,
                "--out-dir", out_dir, "--inventory", inventory_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)
            self.assertTrue(os.path.exists(inventory_path))
            with open(inventory_path, encoding="utf-8") as handle:
                inventory = handle.read()
            self.assertIn("a_objectives.json, ", inventory)
            self.assertIn("b_objectives.json", inventory)

            undeclared = json.loads(json.dumps(bundle))
            undeclared["chapterObjectives"]["symbols"] = []
            undeclared_path = os.path.join(tmp, "bundle-with-undeclared-objective.json")
            with open(undeclared_path, "w", encoding="utf-8") as handle:
                json.dump(undeclared, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle", "--source", undeclared_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn("contains chapter 'CHAPTER_L_2' symbol 'ChapterObjectives_SourceA'", err)

            mismatch = json.loads(json.dumps(bundle))
            mismatch["chapterObjectives"]["symbols"] = ["ChapterObjectives_SourceB"]
            mismatch_path = os.path.join(tmp, "bundle-with-objective-member-mismatch.json")
            with open(mismatch_path, "w", encoding="utf-8") as handle:
                json.dump(mismatch, handle)
            code, out, err = run_cli([
                "validate", "--table", "chapterbundle", "--source", mismatch_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 1)
            self.assertIn("chapterObjectives.symbols[ChapterObjectives_SourceB]", err)

    def test_custom_bundle_cli_uses_declared_sources_for_file_and_directory_inputs(self):
        with scratch_dir() as tmp:
            source = fixture_path("chapterbundle", "cli_valid.json")
            with open(source, encoding="utf-8") as handle:
                bundle = json.load(handle)

            bundle_path = os.path.join(tmp, "custom_bundle.json")
            with open(bundle_path, "w", encoding="utf-8") as handle:
                json.dump(bundle, handle)

            code, out, err = run_cli([
                "validate", "--table", "chapterbundle", "--source", bundle_path, "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)

            bundle_dir = os.path.join(tmp, "bundles")
            os.mkdir(bundle_dir)
            directory_bundle = os.path.join(bundle_dir, "custom_bundle.json")
            shutil.copyfile(bundle_path, directory_bundle)
            direct_out = os.path.join(tmp, "direct")
            direct_inventory = os.path.join(tmp, "direct.md")
            directory_out = os.path.join(tmp, "directory")
            directory_inventory = os.path.join(tmp, "directory.md")
            for bundle_source, out_dir, inventory in (
                (bundle_path, direct_out, direct_inventory),
                (bundle_dir, directory_out, directory_inventory),
            ):
                code, out, err = run_cli([
                    "generate", "--table", "chapterbundle", "--source", bundle_source,
                    "--out-dir", out_dir, "--inventory", inventory, "--no-roundtrip",
                ])
                self.assertEqual(code, 0, msg=out + err)
            with open(direct_inventory, encoding="utf-8") as handle:
                direct_report = handle.read()
            with open(directory_inventory, encoding="utf-8") as handle:
                directory_report = handle.read()
            direct_source = os.path.relpath(bundle_path, REPO_ROOT).replace(os.sep, "/")
            directory_source = os.path.relpath(directory_bundle, REPO_ROOT).replace(os.sep, "/")
            self.assertIn("`{}`".format(direct_source), direct_report)
            self.assertIn("`{}`".format(directory_source), directory_report)
            self.assertEqual(
                direct_report.replace(direct_source, directory_source),
                directory_report,
            )

            for field, source_key, expected_path in (
                ("tables.units", ("tables", "units", "source"), "tables.units.source"),
                ("supportOwners", ("supportOwners", "source"), "supportOwners.source"),
            ):
                bad_bundle = json.loads(json.dumps(bundle))
                cursor = bad_bundle
                for key in source_key[:-1]:
                    cursor = cursor[key]
                cursor[source_key[-1]] = os.path.join(tmp, "missing-{}.json".format(field))
                bad_path = os.path.join(tmp, "missing-{}.json".format(field))
                with open(bad_path, "w", encoding="utf-8") as handle:
                    json.dump(bad_bundle, handle)
                code, out, err = run_cli([
                    "validate", "--table", "chapterbundle", "--source", bad_path, "--no-roundtrip",
                ])
                self.assertEqual(code, 1)
                self.assertIn(expected_path, err)

                bad_out = os.path.join(tmp, "bad-{}".format(field))
                bad_inventory = os.path.join(tmp, "bad-{}.md".format(field))
                code, out, err = run_cli([
                    "generate", "--table", "chapterbundle", "--source", bad_path,
                    "--out-dir", bad_out, "--inventory", bad_inventory, "--no-roundtrip",
                ])
                self.assertEqual(code, 1)
                self.assertIn(expected_path, err)
                self.assertFalse(os.path.exists(bad_inventory))

    def test_generate_skips_c_output_for_metadata_only_table(self):
        with scratch_dir() as tmp:
            out_dir = os.path.join(tmp, "out")
            inventory_path = os.path.join(tmp, "inventory.md")
            code, out, err = run_cli([
                "generate", "--table", "chapterbundle",
                "--source", fixture_path("chapterbundle", "cli_valid.json"),
                "--out-dir", out_dir,
                "--inventory", inventory_path,
                "--no-roundtrip",
            ])
            self.assertEqual(code, 0, msg=out + err)
            self.assertIn("skip: table 'chapterbundle' is metadata-only; no C output generated", out)
            self.assertTrue(os.path.exists(inventory_path))
            self.assertFalse(os.listdir(out_dir) if os.path.isdir(out_dir) else False)
            with open(inventory_path) as f:
                content = f.read()
                self.assertIn("Ch2Events", content)

    def test_check_real_ch2_bundle_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "chapterbundle"])
        self.assertEqual(code, 0, msg=out + err)

    def test_check_detects_injected_drift_in_committed_inventory(self):
        with scratch_dir() as tmp:
            out_dir = os.path.join(tmp, "out")
            inventory_path = os.path.join(tmp, "inventory.md")
            common = [
                "--table", "chapterbundle",
                "--source", fixture_path("chapterbundle", "cli_valid.json"),
                "--out-dir", out_dir,
                "--inventory", inventory_path,
                "--no-roundtrip",
            ]
            run_cli(["generate"] + common)
            with open(inventory_path, "a") as f:
                f.write("\ntampered line\n")
            code, out, err = run_cli(["check"] + common)
            self.assertEqual(code, 1)
            self.assertIn("DRIFT", err)


class CliChapterObjectivesTests(unittest.TestCase):
    def test_validate_real_default_source_passes(self):
        code, out, err = run_cli(["validate", "--table", "chapterobjectives"])
        self.assertEqual(code, 0, msg=out + err)

    def test_validate_fixture_and_invalid_dependency_diagnostics(self):
        code, out, err = run_cli([
            "validate", "--table", "chapterobjectives",
            "--source", fixture_path("chapterobjectives", "valid.json"),
            "--no-roundtrip",
            "--dep-source",
            "chapterbundle={}".format(fixture_path("chapterobjectives", "ch2_bundle.json")),
        ])
        self.assertEqual(code, 0, msg=out + err)

        code, out, err = run_cli([
            "validate", "--table", "chapterobjectives",
            "--source", fixture_path("chapterobjectives", "missing_dependency.json"),
            "--no-roundtrip",
            "--dep-source",
            "chapterbundle={}".format(fixture_path("chapterobjectives", "ch2_bundle.json")),
        ])
        self.assertEqual(code, 1)
        self.assertIn("missing_dependency.json", err)
        self.assertIn("is used by this chapter objective bundle", err)

    def test_validate_multiple_chapters_with_indexed_bundle_owners(self):
        code, out, err = run_cli([
            "validate", "--table", "chapterobjectives",
            "--source", fixture_path("chapterobjectives", "two_chapters.json"),
            "--no-roundtrip",
            "--dep-source",
            "units={}".format(fixture_path("chapterobjectives", "deps_units_two_chapters.json")),
            "--dep-source",
            "chapterbundle={}".format(fixture_path("chapterobjectives", "two_chapter_bundles")),
        ])
        self.assertEqual(code, 0, msg=out + err)
        self.assertIn("2 record(s)", out)

    def test_rejects_owner_with_an_unrelated_objective_source(self):
        code, out, err = run_cli([
            "validate", "--table", "chapterobjectives",
            "--source", fixture_path("chapterobjectives", "unrelated_objectives.json"),
            "--no-roundtrip",
            "--dep-source",
            "chapterbundle={}".format(fixture_path("chapterobjectives", "ch2_bundle.json")),
        ])
        self.assertEqual(code, 1)
        self.assertIn("chapterObjectives.source", err)
        self.assertIn("unrelated_objectives.json", err)

    def test_check_real_default_source_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "chapterobjectives"])
        self.assertEqual(code, 0, msg=out + err)


class CliItemsTests(unittest.TestCase):
    def test_real_items_source_validates_and_roundtrips_clean(self):
        code, out, err = run_cli(["validate", "--table", "items"])
        self.assertEqual(code, 0, msg=out + err)

    def test_generate_writes_c_and_inventory(self):
        with scratch_dir() as tmp:
            out_dir = os.path.join(tmp, "out")
            inventory_path = os.path.join(tmp, "inventory.md")
            code, out, err = run_cli(["generate", "--table", "items", "--out-dir", out_dir,
                                       "--inventory", inventory_path, "--no-roundtrip"])
            self.assertEqual(code, 0, msg=out + err)
            generated_file = os.path.join(out_dir, "data_items.c")
            self.assertTrue(os.path.exists(generated_file))
            self.assertTrue(os.path.exists(inventory_path))
            with open(generated_file) as f:
                self.assertIn("CONST_DATA struct ItemData gItemData[] = {", f.read())

    def test_check_real_items_table_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "items"])
        self.assertEqual(code, 0, msg=out + err)


class CliClassesTests(unittest.TestCase):
    def test_real_classes_source_validates_and_roundtrips_clean(self):
        code, out, err = run_cli(["validate", "--table", "classes"])
        self.assertEqual(code, 0, msg=out + err)

    def test_generate_writes_c_and_inventory(self):
        with scratch_dir() as tmp:
            out_dir = os.path.join(tmp, "out")
            inventory_path = os.path.join(tmp, "inventory.md")
            code, out, err = run_cli(["generate", "--table", "classes", "--out-dir", out_dir,
                                       "--inventory", inventory_path, "--no-roundtrip"])
            self.assertEqual(code, 0, msg=out + err)
            generated_file = os.path.join(out_dir, "data_classes.c")
            self.assertTrue(os.path.exists(generated_file))
            self.assertTrue(os.path.exists(inventory_path))
            with open(generated_file) as f:
                self.assertIn("CONST_DATA struct ClassData gClassData[] = {", f.read())

    def test_check_real_classes_table_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "classes"])
        self.assertEqual(code, 0, msg=out + err)


class CliCharactersTests(unittest.TestCase):
    """Issue #5 Batch 2b: ``characters`` is fully wired up (real
    ``default_source``/``default_hand_source``/``default_output_name``/
    ``default_inventory_path`` -- see ``CharactersTableSchema``), so this
    class also covers the ``test_real_*_source_validates``/
    ``test_generate_*``/``test_check_*`` counterparts every other
    fully-registered table has.
    """

    def test_validate_missing_source_table_is_registered_without_being_in_all_tables(self):
        # characters is resolvable via --table (registered in registry.py)
        # but must not appear in any all-tables default loop; there is no
        # such loop in this CLI today, so this is simply confirming the
        # schema resolves cleanly on its own. --no-roundtrip is required
        # here because this 3-record fixture's designators (CHARACTER_EIRIKA,
        # 27, 256) legitimately overlap the real gCharacterData[] designator
        # space -- default_hand_source now points at the real
        # src/data_characters.c (Batch 2b), so without --no-roundtrip the
        # CLI would (correctly) compare this fixture's placeholder field
        # values against the real hand-written records and fail.
        code, out, err = run_cli([
            "validate", "--table", "characters",
            "--source", fixture_path("characters", "valid.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 0, msg=out + err)

    def test_real_characters_source_validates_and_roundtrips_clean(self):
        code, out, err = run_cli(["validate", "--table", "characters"])
        self.assertEqual(code, 0, msg=out + err)

    def test_generate_writes_c_and_inventory(self):
        with scratch_dir() as tmp:
            out_dir = os.path.join(tmp, "out")
            inventory_path = os.path.join(tmp, "inventory.md")
            code, out, err = run_cli(["generate", "--table", "characters", "--out-dir", out_dir,
                                       "--inventory", inventory_path, "--no-roundtrip"])
            self.assertEqual(code, 0, msg=out + err)
            generated_file = os.path.join(out_dir, "data_characters.c")
            self.assertTrue(os.path.exists(generated_file))
            self.assertTrue(os.path.exists(inventory_path))
            with open(generated_file) as f:
                self.assertIn("CONST_DATA struct CharacterData gCharacterData[] = {", f.read())

    def test_check_real_characters_table_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "characters"])
        self.assertEqual(code, 0, msg=out + err)

    def test_validate_with_dependency_overrides_passes(self):
        code, out, err = run_cli([
            "validate", "--table", "characters",
            "--source", fixture_path("characters", "valid.json"),
            "--dep-source", "classes=" + fixture_path("characters", "deps_classes.json"),
            "--dep-source", "supports=" + fixture_path("characters", "deps_supports.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 0, msg=out + err)
        self.assertIn("OK:", out)

    def test_validate_class_missing_from_dependency_table_detected(self):
        code, out, err = run_cli([
            "validate", "--table", "characters",
            "--source", fixture_path("characters", "class_missing_from_table.json"),
            "--dep-source", "classes=" + fixture_path("characters", "deps_classes.json"),
            "--dep-source", "supports=" + fixture_path("characters", "deps_supports.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 1)
        self.assertIn("has no ClassData record in the loaded classes table", err)

    def test_validate_support_data_owner_mismatch_detected_with_dependency_override(self):
        code, out, err = run_cli([
            "validate", "--table", "characters",
            "--source", fixture_path("characters", "support_data_owner_mismatch.json"),
            "--dep-source", "classes=" + fixture_path("characters", "deps_classes.json"),
            "--dep-source", "supports=" + fixture_path("characters", "deps_supports.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 1)
        self.assertIn("belongs to owner 'CHARACTER_EIRIKA', not 'CHARACTER_SETH'", err)

    def test_validate_event_autoload_slot_sentinels_rejected(self):
        """Regression: ``CHARACTER_EVT_LEADER``/``ACTIVE``/``SLOTB``/
        ``SLOT2`` (the separate ``event_autoload_pid_idx`` enum, sharing
        the ``CHARACTER_`` prefix, two of them negative) must never be
        accepted as ``CharacterData`` designators end to end through the
        CLI."""
        code, out, err = run_cli([
            "validate", "--table", "characters",
            "--source", fixture_path("characters", "character_evt_sentinels.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 1)
        for sentinel in (
            "CHARACTER_EVT_LEADER", "CHARACTER_EVT_ACTIVE", "CHARACTER_EVT_SLOTB", "CHARACTER_EVT_SLOT2",
        ):
            self.assertIn("undefined character reference '{}'".format(sentinel), err)


if __name__ == "__main__":
    unittest.main()
