import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.weapontriangle import schema as weapontriangle_schema
from scripts.generated_data.tests._util import fixture_path

_BMITEM_HEADER = None  # use the real include/bmitem.h -- ITYPE_* is a stable, small, core enum


def _validate(fixture_name):
    records = weapontriangle_schema.load_records(fixture_path("weapontriangle", fixture_name))
    diagnostics = DiagnosticCollector()
    weapontriangle_schema.validate(records, diagnostics)
    return records, diagnostics


class WeaponTriangleSchemaValidTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(len(records), 12)

    def test_expected_rule_count_constant_is_12(self):
        self.assertEqual(weapontriangle_schema.EXPECTED_RULE_COUNT, 12)

    def test_groups_are_two_closed_three_type_triangles(self):
        self.assertEqual(len(weapontriangle_schema.GROUPS), 2)
        for group in weapontriangle_schema.GROUPS:
            self.assertEqual(len(group), 3)
        self.assertEqual(
            weapontriangle_schema.IN_SCOPE_TYPES,
            frozenset(["ITYPE_SWORD", "ITYPE_LANCE", "ITYPE_AXE", "ITYPE_ANIMA", "ITYPE_LIGHT", "ITYPE_DARK"]),
        )


class WeaponTriangleSchemaDuplicateTests(unittest.TestCase):
    def test_duplicate_pair_detected(self):
        _, diagnostics = _validate("duplicate_pair.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("duplicate weapontriangle rule for 'ITYPE_SWORD->ITYPE_LANCE'" in m for m in messages), messages
        )


class WeaponTriangleSchemaCountTests(unittest.TestCase):
    def test_wrong_count_detected(self):
        _, diagnostics = _validate("wrong_count.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("expected exactly 12 weapontriangle rules, found 11" in m for m in messages), messages
        )


class WeaponTriangleSchemaTypeReferenceTests(unittest.TestCase):
    def test_unknown_type_detected(self):
        _, diagnostics = _validate("unknown_type.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined weapon-type reference 'ITYPE_TOTALLY_MADE_UP'" in m for m in messages), messages
        )

    def test_out_of_scope_type_detected(self):
        _, diagnostics = _validate("out_of_scope_type.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any(
                "weapon type 'ITYPE_BOW' does not participate in any weapon triangle" in m
                for m in messages
            ),
            messages,
        )


class WeaponTriangleSchemaPairShapeTests(unittest.TestCase):
    def test_self_pair_detected(self):
        _, diagnostics = _validate("self_pair.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("self-pair rule: attacker and defender are both 'ITYPE_SWORD'" in m for m in messages), messages
        )

    def test_cross_group_pair_detected(self):
        _, diagnostics = _validate("cross_group.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("cross-group weapon-triangle pair: 'ITYPE_SWORD' and 'ITYPE_ANIMA'" in m for m in messages),
            messages,
        )


class WeaponTriangleSchemaReciprocalTests(unittest.TestCase):
    def test_missing_reciprocal_detected(self):
        _, diagnostics = _validate("missing_reciprocal.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("missing reciprocal rule for (ITYPE_LANCE, ITYPE_SWORD)" in m for m in messages), messages
        )

    def test_reciprocal_mismatch_detected(self):
        _, diagnostics = _validate("reciprocal_mismatch.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("reciprocal hitBonus mismatch" in m for m in messages), messages)
        self.assertTrue(any("reciprocal atkBonus mismatch" in m for m in messages), messages)


class WeaponTriangleSchemaRangeTests(unittest.TestCase):
    def test_bad_range_detected(self):
        _, diagnostics = _validate("bad_range.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("200" in m and "out of range" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
