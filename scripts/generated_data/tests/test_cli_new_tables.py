import json
import os
import shutil
import unittest

from scripts.generated_data.tests._util import fixture_path, scratch_dir
from scripts.generated_data.tests.test_cli import run_cli


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
            self.assertEqual(direct_report, directory_report)

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
