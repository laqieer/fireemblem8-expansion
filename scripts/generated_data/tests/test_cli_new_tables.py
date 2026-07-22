import os
import unittest

from scripts.generated_data.tests._util import fixture_path, scratch_dir
from scripts.generated_data.tests.test_cli import run_cli


class CliUnitsTests(unittest.TestCase):
    def test_validate_valid_fixture_passes(self):
        code, out, err = run_cli([
            "validate", "--table", "units",
            "--source", fixture_path("units", "valid.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 0, msg=out + err)
        self.assertIn("OK:", out)

    def test_validate_invalid_fixture_fails(self):
        code, out, err = run_cli([
            "validate", "--table", "units",
            "--source", fixture_path("units", "duplicate_group.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 1)
        self.assertIn("duplicate_group.json", err)

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
    def test_validate_valid_fixture_passes(self):
        code, out, err = run_cli([
            "validate", "--table", "shops",
            "--source", fixture_path("shops", "valid.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 0, msg=out + err)

    def test_validate_invalid_fixture_fails(self):
        code, out, err = run_cli([
            "validate", "--table", "shops",
            "--source", fixture_path("shops", "missing_item_ref.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 1)
        self.assertIn("undefined item reference", err)

    def test_real_ch2_shops_source_validates_and_roundtrips_clean(self):
        code, out, err = run_cli(["validate", "--table", "shops"])
        self.assertEqual(code, 0, msg=out + err)

    def test_check_real_ch2_shops_table_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "shops"])
        self.assertEqual(code, 0, msg=out + err)


class CliTrapsTests(unittest.TestCase):
    def test_validate_valid_fixture_passes(self):
        code, out, err = run_cli([
            "validate", "--table", "traps",
            "--source", fixture_path("traps", "valid.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 0, msg=out + err)

    def test_validate_invalid_fixture_fails(self):
        code, out, err = run_cli([
            "validate", "--table", "traps",
            "--source", fixture_path("traps", "bad_type.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 1)
        self.assertIn("undefined trap type reference", err)

    def test_real_ch2_traps_source_validates_and_roundtrips_clean(self):
        code, out, err = run_cli(["validate", "--table", "traps"])
        self.assertEqual(code, 0, msg=out + err)

    def test_check_real_ch2_traps_table_has_no_drift(self):
        code, out, err = run_cli(["check", "--table", "traps"])
        self.assertEqual(code, 0, msg=out + err)


class CliEventScriptsTests(unittest.TestCase):
    def test_validate_valid_fixture_passes(self):
        code, out, err = run_cli([
            "validate", "--table", "eventscripts",
            "--source", fixture_path("eventscripts", "valid.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 0, msg=out + err)

    def test_validate_invalid_fixture_fails(self):
        code, out, err = run_cli([
            "validate", "--table", "eventscripts",
            "--source", fixture_path("eventscripts", "undeclared_symbol.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 1)
        self.assertIn("is not declared in", err)

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
    def test_validate_valid_fixture_passes(self):
        code, out, err = run_cli([
            "validate", "--table", "eventlists",
            "--source", fixture_path("eventlists", "valid.json"),
            "--no-roundtrip",
        ] + _eventlists_dep_source_args())
        self.assertEqual(code, 0, msg=out + err)
        self.assertIn("OK:", out)

    def test_validate_invalid_fixture_fails_with_location(self):
        code, out, err = run_cli([
            "validate", "--table", "eventlists",
            "--source", fixture_path("eventlists", "missing_unit_ref.json"),
            "--no-roundtrip",
        ] + _eventlists_dep_source_args())
        self.assertEqual(code, 1)
        self.assertIn("missing_unit_ref.json", err)
        self.assertIn("undefined unit group reference", err)

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

    def test_validate_invalid_fixture_fails_with_location(self):
        code, out, err = run_cli([
            "validate", "--table", "chapterbundle",
            "--source", fixture_path("chapterbundle", "cli_missing_support_owner.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 1)
        self.assertIn("cli_missing_support_owner.json", err)
        self.assertIn("missing from supportOwners.required", err)

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


class CliItemsTests(unittest.TestCase):
    def test_validate_invalid_fixture_fails(self):
        code, out, err = run_cli([
            "validate", "--table", "items",
            "--source", fixture_path("items", "bad_weapon_type.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 1)
        self.assertIn("undefined weapon type reference", err)

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
    def test_validate_invalid_fixture_fails(self):
        code, out, err = run_cli([
            "validate", "--table", "classes",
            "--source", fixture_path("classes", "bad_class_ref.json"),
            "--no-roundtrip",
        ])
        self.assertEqual(code, 1)
        self.assertIn("undefined class reference 'CLASS_NOT_A_REAL_CLASS'", err)

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


if __name__ == "__main__":
    unittest.main()
