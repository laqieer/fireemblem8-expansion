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


if __name__ == "__main__":
    unittest.main()
