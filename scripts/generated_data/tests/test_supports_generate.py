import os
import unittest

from scripts.generated_data.cgen import write_if_changed
from scripts.generated_data.supports.generate import generate_c_source
from scripts.generated_data.supports.schema import load_records
from scripts.generated_data.tests._util import fixture_path, scratch_dir


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(fixture_path("supports", "valid.json"))
        first = generate_c_source(records, "fixtures/supports/valid.json")
        second = generate_c_source(records, "fixtures/supports/valid.json")
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_symbols_and_values(self):
        records = load_records(fixture_path("supports", "valid.json"))
        content = generate_c_source(records, "fixtures/supports/valid.json")
        self.assertIn("CONST_DATA struct SupportData SupportData_Eirika", content)
        self.assertIn(".characters = { CHARACTER_SETH, CHARACTER_FRANZ }", content)
        self.assertIn(".supportExpBase = { 10, 5 }", content)
        self.assertIn(".supportExpGrowth = { 2, 1 }", content)
        self.assertIn(".supportCount = 2", content)
        self.assertIn('#include "global.h"', content)
        self.assertIn('#include "bmreliance.h"', content)

    def test_record_order_preserved_from_source(self):
        records = load_records(fixture_path("supports", "valid.json"))
        content = generate_c_source(records, "src")
        eirika_pos = content.index("SupportData_Eirika")
        seth_pos = content.index("SupportData_Seth")
        franz_pos = content.index("SupportData_Franz")
        self.assertLess(eirika_pos, seth_pos)
        self.assertLess(seth_pos, franz_pos)


class WriteIfChangedTests(unittest.TestCase):
    def test_first_write_creates_file_and_reports_changed(self):
        with scratch_dir() as tmp:
            path = os.path.join(tmp, "nested", "out.c")
            changed = write_if_changed(path, "content-v1\n")
            self.assertTrue(changed)
            with open(path) as f:
                self.assertEqual(f.read(), "content-v1\n")

    def test_identical_rewrite_is_a_noop(self):
        with scratch_dir() as tmp:
            path = os.path.join(tmp, "out.c")
            write_if_changed(path, "same\n")
            mtime_before = os.path.getmtime(path)
            changed = write_if_changed(path, "same\n")
            self.assertFalse(changed)
            self.assertEqual(os.path.getmtime(path), mtime_before)

    def test_changed_content_rewrites(self):
        with scratch_dir() as tmp:
            path = os.path.join(tmp, "out.c")
            write_if_changed(path, "v1\n")
            changed = write_if_changed(path, "v2\n")
            self.assertTrue(changed)
            with open(path) as f:
                self.assertEqual(f.read(), "v2\n")


if __name__ == "__main__":
    unittest.main()
