import os
import unittest

from scripts.generated_data.diagnostics import SourceLocation
from scripts.generated_data.validators import (
    extract_define_constant,
    extract_enum_constants,
    resolve_bitmask_flags,
    validate_fixed_capacity,
    validate_parallel_arrays,
    validate_range,
    validate_reference,
    validate_unique,
)
from scripts.generated_data.tests._util import scratch_dir

_LOC = SourceLocation("test.json", 1, 1)


class ExtractEnumConstantsTests(unittest.TestCase):
    def test_reads_prefixed_hex_and_decimal_values(self):
        with scratch_dir() as tmp:
            header = os.path.join(tmp, "demo.h")
            with open(header, "w") as f:
                f.write(
                    "enum {\n"
                    "    THING_NONE = 0x00,\n"
                    "    THING_ONE = 1,\n"
                    "    OTHER_TWO = 0x02,\n"
                    "};\n"
                )
            constants = extract_enum_constants(header, name_prefix="THING_")
            self.assertEqual(set(constants), {"THING_NONE", "THING_ONE"})
            self.assertEqual(constants["THING_NONE"], (0, 2))
            self.assertEqual(constants["THING_ONE"], (1, 3))

    def test_real_characters_header_has_eirika(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        header = os.path.join(repo_root, "include", "constants", "characters.h")
        constants = extract_enum_constants(header, name_prefix="CHARACTER_")
        self.assertIn("CHARACTER_EIRIKA", constants)
        self.assertEqual(constants["CHARACTER_EIRIKA"][0], 1)


class ExtractDefineConstantTests(unittest.TestCase):
    def test_reads_hex_and_decimal_defines(self):
        with scratch_dir() as tmp:
            header = os.path.join(tmp, "demo.h")
            with open(header, "w") as f:
                f.write(
                    "#define OTHER_THING 3\n"
                    "#define THING_COUNT 0x0D56\n"
                    "#define THING_COUNT_COMMENTED 5 // trailing comment\n"
                )
            value, line = extract_define_constant(header, "THING_COUNT")
            self.assertEqual(value, 0x0D56)
            self.assertEqual(line, 2)
            value2, _ = extract_define_constant(header, "THING_COUNT_COMMENTED")
            self.assertEqual(value2, 5)

    def test_missing_define_raises(self):
        with scratch_dir() as tmp:
            header = os.path.join(tmp, "demo.h")
            with open(header, "w") as f:
                f.write("#define UNRELATED 1\n")
            with self.assertRaises(Exception) as ctx:
                extract_define_constant(header, "THING_COUNT")
            self.assertIn("THING_COUNT", str(ctx.exception))

    def test_real_msg_header_has_msg_count(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        header = os.path.join(repo_root, "include", "constants", "msg.h")
        value, _ = extract_define_constant(header, "MSG_COUNT")
        self.assertGreater(value, 0)


class ResolveBitmaskFlagsTests(unittest.TestCase):
    _FLAGS = {"FLAG_A": 1, "FLAG_B": 2, "FLAG_C": 4}

    def test_empty_list_resolves_to_zero(self):
        value, errors = resolve_bitmask_flags([], self._FLAGS, _LOC, "ref")
        self.assertEqual(value, 0)
        self.assertEqual(errors, [])

    def test_valid_flags_are_ored_together(self):
        value, errors = resolve_bitmask_flags(["FLAG_A", "FLAG_C"], self._FLAGS, _LOC, "ref")
        self.assertEqual(value, 5)
        self.assertEqual(errors, [])

    def test_unknown_flag_errors_and_is_excluded_from_value(self):
        value, errors = resolve_bitmask_flags(["FLAG_A", "FLAG_NOPE"], self._FLAGS, _LOC, "ref", kind="widget")
        self.assertEqual(value, 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("undefined widget reference 'FLAG_NOPE'", str(errors[0]))

    def test_duplicate_flag_errors_and_is_not_double_counted(self):
        value, errors = resolve_bitmask_flags(["FLAG_A", "FLAG_A"], self._FLAGS, _LOC, "ref")
        self.assertEqual(value, 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("duplicate flag 'FLAG_A' in the same list", str(errors[0]))


class ValidateUniqueTests(unittest.TestCase):
    def test_no_duplicates_is_empty(self):
        errors = validate_unique(
            [("a", _LOC), ("b", _LOC)], "dup {key} at {first_loc}", "path[{key}]"
        )
        self.assertEqual(errors, [])

    def test_duplicate_reports_first_location(self):
        first = SourceLocation("test.json", 2, 1)
        second = SourceLocation("test.json", 5, 1)
        errors = validate_unique(
            [("a", first), ("a", second)], "dup {key} (first at {first_loc})", "path[{key}]"
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].location, second)
        self.assertIn("first at test.json:2:1", str(errors[0]))


class ValidateReferenceTests(unittest.TestCase):
    def test_known_symbol_ok(self):
        self.assertEqual(validate_reference("A", {"A", "B"}, _LOC, "ref"), [])

    def test_unknown_symbol_errors(self):
        errors = validate_reference("C", {"A", "B"}, _LOC, "ref", kind="character")
        self.assertEqual(len(errors), 1)
        self.assertIn("undefined character reference 'C'", str(errors[0]))


class ValidateRangeTests(unittest.TestCase):
    def test_in_range_ok(self):
        self.assertEqual(validate_range(5, 0, 255, _LOC, "ref"), [])

    def test_out_of_range_errors(self):
        errors = validate_range(300, 0, 255, _LOC, "ref", field_name="supportExpBase")
        self.assertEqual(len(errors), 1)
        self.assertIn("supportExpBase 300 out of range [0, 255]", str(errors[0]))


class ValidateFixedCapacityTests(unittest.TestCase):
    def test_within_capacity_ok(self):
        self.assertEqual(validate_fixed_capacity(7, 7, _LOC, "ref"), [])

    def test_exceeds_capacity_errors(self):
        errors = validate_fixed_capacity(8, 7, _LOC, "ref", what="support partners")
        self.assertEqual(len(errors), 1)
        self.assertIn("support partners count 8 exceeds fixed capacity 7", str(errors[0]))


class ValidateParallelArraysTests(unittest.TestCase):
    def test_equal_lengths_ok(self):
        self.assertEqual(validate_parallel_arrays([3, 3, 3], _LOC, "ref", ["a", "b", "c"]), [])

    def test_mismatched_lengths_errors(self):
        errors = validate_parallel_arrays([3, 2, 3], _LOC, "ref", ["a", "b", "c"])
        self.assertEqual(len(errors), 1)
        self.assertIn("mismatched lengths", str(errors[0]))


if __name__ == "__main__":
    unittest.main()
