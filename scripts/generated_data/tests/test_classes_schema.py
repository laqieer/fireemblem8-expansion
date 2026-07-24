import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.classes import schema as classes_schema
from scripts.generated_data.terrainstats import schema as terrainstats_schema
from scripts.generated_data.movecost import schema as movecost_schema
from scripts.generated_data.tests._util import fixture_path

_MINI_CLASSES_HEADER = fixture_path("classes", "mini_classes.h")
_MINI_BMUNIT_HEADER = fixture_path("classes", "mini_bmunit.h")
_MINI_BMITEM_HEADER = fixture_path("classes", "mini_bmitem.h")
_MINI_EKRBATTLE_HEADER = fixture_path("classes", "mini_ekrbattle.h")
_MINI_VARIABLES_HEADER = fixture_path("classes", "mini_variables.h")
_MINI_MSG_HEADER = fixture_path("classes", "mini_msg.h")
_MINI_PORTRAIT_SOURCE = fixture_path("classes", "mini_portrait_data.c")
_MINI_SMS_SOURCE = fixture_path("classes", "mini_sms_data.c")


def _deps_terrainstats():
    return {
        "terrainstats": terrainstats_schema.load_records(
            fixture_path("classes", "deps_terrainstats.json")
        )
    }


def _validate(fixture_name):
    records = classes_schema.load_records(fixture_path("classes", fixture_name))
    diagnostics = DiagnosticCollector()
    classes_schema.validate(
        records,
        diagnostics,
        dependency_records=_deps_terrainstats(),
        classes_header=_MINI_CLASSES_HEADER,
        bmunit_header=_MINI_BMUNIT_HEADER,
        bmitem_header=_MINI_BMITEM_HEADER,
        ekrbattle_header=_MINI_EKRBATTLE_HEADER,
        variables_header=_MINI_VARIABLES_HEADER,
        msg_header=_MINI_MSG_HEADER,
        portrait_source=_MINI_PORTRAIT_SOURCE,
        sms_source=_MINI_SMS_SOURCE,
    )
    return records, diagnostics


class ClassesSchemaValidTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(len(records), 2)

    def test_encoded_fields_resolved(self):
        records, _ = _validate("valid.json")
        by_class = {r.class_name: r for r in records}
        fixture_a = by_class["CLASS_FIXTURE_A"]
        self.assertEqual(fixture_a.battle_anim, "AnimConf_Fixture")
        self.assertEqual(fixture_a.reserved_terrain_table, "Unk_TerrainTable_Fixture")
        self.assertEqual(
            fixture_a.encoded_base_ranks(
                {"ITYPE_SWORD": (0, None), "ITYPE_LANCE": (1, None), "ITYPE_STAFF": (2, None)},
                {"WPN_EXP_0": (0, None), "WPN_EXP_E": (1, None), "WPN_EXP_D": (31, None)},
                8,
            ),
            (31, 0, 0, 0, 0, 0, 0, 0),
        )

    def test_mov_cost_table_null_literal_modeled_as_none(self):
        records, _ = _validate("valid.json")
        by_class = {r.class_name: r for r in records}
        self.assertEqual(by_class["CLASS_FIXTURE_B"].mov_cost_table, [None, None, None])


class ClassesSchemaDuplicateTests(unittest.TestCase):
    def test_duplicate_class_detected(self):
        _, diagnostics = _validate("duplicate_id.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("duplicate class entry 'CLASS_FIXTURE_A'" in m for m in messages), messages)


class ClassesSchemaCoverageTests(unittest.TestCase):
    def test_gap_in_coverage_detected(self):
        _, diagnostics = _validate("gap.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("missing class coverage for 'CLASS_FIXTURE_B' (index 2)" in m for m in messages), messages
        )


class ClassesSchemaReferenceTests(unittest.TestCase):
    def test_bad_class_reference_detected(self):
        _, diagnostics = _validate("bad_class_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("undefined class reference 'CLASS_NOT_A_REAL_CLASS'" in m for m in messages), messages)

    def test_bad_promotion_reference_detected(self):
        _, diagnostics = _validate("bad_promotion_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("undefined class reference 'CLASS_NOT_A_REAL_CLASS'" in m for m in messages), messages)

    def test_bad_attribute_detected(self):
        _, diagnostics = _validate("bad_attribute.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined class attribute reference 'CA_NOT_A_REAL_FLAG'" in m for m in messages), messages
        )

    def test_duplicate_attribute_detected(self):
        _, diagnostics = _validate("duplicate_attribute.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("duplicate class attribute 'CA_LORD' in the same list" in m for m in messages), messages
        )

    def test_bad_base_ranks_itype_reference_detected(self):
        _, diagnostics = _validate("bad_base_ranks_itype_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined weapon type reference 'ITYPE_NOT_A_REAL_TYPE'" in m for m in messages), messages
        )

    def test_bad_base_ranks_wexp_reference_detected(self):
        _, diagnostics = _validate("bad_base_ranks_wexp_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined weapon-exp threshold reference 'WPN_EXP_NOT_REAL'" in m for m in messages), messages
        )

    def test_bad_battle_anim_pointer_detected(self):
        _, diagnostics = _validate("bad_battle_anim_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("AnimConf_NotReal" in m for m in messages), messages)

    def test_bad_mov_cost_pointer_detected(self):
        _, diagnostics = _validate("bad_mov_cost_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("TerrainTable_MovCost_NotReal" in m for m in messages), messages)

    def test_bad_mov_cost_table_count_detected(self):
        _, diagnostics = _validate("bad_mov_cost_count.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("movCostTable has 2 entries, expected exactly 3" in m for m in messages), messages
        )

    def test_bad_terrain_pointer_detected(self):
        _, diagnostics = _validate("bad_terrain_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any(
                "TerrainTable_Avo_NotReal' is not one of the arrays authored in terrainstats" in m
                for m in messages
            ),
            messages,
        )

    def test_bad_reserved_terrain_pointer_detected(self):
        _, diagnostics = _validate("bad_reserved_terrain_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("Unk_TerrainTable_NotReal" in m for m in messages), messages)


class ClassesTerrainstatsDependencyTests(unittest.TestCase):
    """Issue #5 Batch 1: terrainAvoid/terrainDefense/terrainResistance are
    resolved against the terrainstats dependency's own field metadata,
    not just a generic extern-symbol presence check."""

    def test_field_mismatch_against_terrainstats_detected(self):
        records = classes_schema.load_records(fixture_path("classes", "valid.json"))
        diagnostics = DiagnosticCollector()
        # deps_terrainstats.json tags TerrainTable_Def_Fixture with field
        # 'terrainDefense' -- referencing it from terrainAvoid must fail
        # even though the symbol itself is a real authored terrainstats
        # array (just for the wrong role).
        mismatched_deps = {
            "terrainstats": terrainstats_schema.load_records(
                fixture_path("classes", "deps_terrainstats_mismatch.json")
            )
        }
        classes_schema.validate(
            records,
            diagnostics,
            dependency_records=mismatched_deps,
            classes_header=_MINI_CLASSES_HEADER,
            bmunit_header=_MINI_BMUNIT_HEADER,
            bmitem_header=_MINI_BMITEM_HEADER,
            ekrbattle_header=_MINI_EKRBATTLE_HEADER,
            variables_header=_MINI_VARIABLES_HEADER,
            msg_header=_MINI_MSG_HEADER,
            portrait_source=_MINI_PORTRAIT_SOURCE,
            sms_source=_MINI_SMS_SOURCE,
        )
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("authored in terrainstats with field 'terrainDefense', expected 'terrainAvoid'" in m
                for m in messages),
            messages,
        )

    def test_no_terrainstats_dependency_falls_back_to_symbol_ref_check(self):
        records = classes_schema.load_records(fixture_path("classes", "valid.json"))
        diagnostics = DiagnosticCollector()
        classes_schema.validate(
            records,
            diagnostics,
            dependency_records=None,
            classes_header=_MINI_CLASSES_HEADER,
            bmunit_header=_MINI_BMUNIT_HEADER,
            bmitem_header=_MINI_BMITEM_HEADER,
            ekrbattle_header=_MINI_EKRBATTLE_HEADER,
            variables_header=_MINI_VARIABLES_HEADER,
            msg_header=_MINI_MSG_HEADER,
            portrait_source=_MINI_PORTRAIT_SOURCE,
            sms_source=_MINI_SMS_SOURCE,
        )
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())


class ClassesMovecostDependencyTests(unittest.TestCase):
    """Issue #5 Batch 2: movCostTable is resolved against the movecost
    dependency's per-slot symbol index -- each non-null entry must be an
    authored movecost array for its own normal/rain/snow slot, and all
    non-null entries in a triplet must resolve to the same movecost
    profile (a mix is a mismatch even if every individual symbol is a
    real authored array elsewhere)."""

    def _deps(self, *, terrainstats=True, movecost=True):
        deps = {}
        if terrainstats:
            deps["terrainstats"] = terrainstats_schema.load_records(
                fixture_path("classes", "deps_terrainstats.json")
            )
        if movecost:
            deps["movecost"] = movecost_schema.load_records(
                fixture_path("classes", "deps_movecost.json")
            )
        return deps

    def _validate_with(self, records, deps):
        diagnostics = DiagnosticCollector()
        classes_schema.validate(
            records,
            diagnostics,
            dependency_records=deps,
            classes_header=_MINI_CLASSES_HEADER,
            bmunit_header=_MINI_BMUNIT_HEADER,
            bmitem_header=_MINI_BMITEM_HEADER,
            ekrbattle_header=_MINI_EKRBATTLE_HEADER,
            variables_header=_MINI_VARIABLES_HEADER,
            msg_header=_MINI_MSG_HEADER,
            portrait_source=_MINI_PORTRAIT_SOURCE,
            sms_source=_MINI_SMS_SOURCE,
        )
        return diagnostics

    def test_valid_triplet_resolves_against_movecost(self):
        records = classes_schema.load_records(fixture_path("classes", "valid.json"))
        diagnostics = self._validate_with(records, self._deps())
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())

    def test_null_triplet_remains_valid(self):
        records = classes_schema.load_records(fixture_path("classes", "valid.json"))
        by_class = {r.class_name: r for r in records}
        self.assertEqual(by_class["CLASS_FIXTURE_B"].mov_cost_table, [None, None, None])
        diagnostics = self._validate_with(records, self._deps())
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())

    def test_single_variant_profile_resolves_in_every_slot(self):
        records = classes_schema.load_records(fixture_path("classes", "valid.json"))
        by_class = {r.class_name: r for r in records}
        # FixtureSingle has only a 'normal' array authored (DemonKing-style
        # single-variant profile) -- repeating its lone symbol in all 3
        # ClassData slots must resolve cleanly, weather-invariant.
        by_class["CLASS_FIXTURE_A"].mov_cost_table = [
            "TerrainTable_MovCost_FixtureSingle",
            "TerrainTable_MovCost_FixtureSingle",
            "TerrainTable_MovCost_FixtureSingle",
        ]
        diagnostics = self._validate_with(records, self._deps())
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())

    def test_unresolvable_symbol_detected(self):
        records = classes_schema.load_records(fixture_path("classes", "valid.json"))
        by_class = {r.class_name: r for r in records}
        by_class["CLASS_FIXTURE_A"].mov_cost_table[1] = "TerrainTable_MovCost_NotReal"
        diagnostics = self._validate_with(records, self._deps())
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any(
                "movCostTable[1] 'TerrainTable_MovCost_NotReal' is not one of the arrays "
                "authored in movecost for the 'rain' slot" in m
                for m in messages
            ),
            messages,
        )

    def test_mixed_profile_triplet_detected(self):
        records = classes_schema.load_records(fixture_path("classes", "valid.json"))
        by_class = {r.class_name: r for r in records}
        # normal/rain come from 'Fixture', snow from the unrelated
        # 'FixtureB' profile -- every individual symbol is a real
        # authored movecost array, but the triplet must still be flagged
        # since it does not resolve to a single consistent profile.
        by_class["CLASS_FIXTURE_A"].mov_cost_table[2] = "TerrainTable_MovCost_FixtureBSnow"
        diagnostics = self._validate_with(records, self._deps())
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("movCostTable mixes symbols from different movecost profiles" in m for m in messages),
            messages,
        )

    def test_no_movecost_dependency_falls_back_to_symbol_ref_check(self):
        records = classes_schema.load_records(fixture_path("classes", "valid.json"))
        diagnostics = self._validate_with(records, self._deps(movecost=False))
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())


class ClassesSchemaRangeTests(unittest.TestCase):
    def test_bad_text_id_detected(self):
        _, diagnostics = _validate("bad_text_id.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("nameTextId 9999 out of range [0, 9]" in m for m in messages), messages)

    def test_bad_sms_id_detected(self):
        _, diagnostics = _validate("bad_sms_id.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("smsId 999 out of range [0, 2]" in m for m in messages), messages)

    def test_bad_portrait_id_detected(self):
        _, diagnostics = _validate("bad_portrait_id.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("defaultPortraitId 999 out of range [0, 2]" in m for m in messages), messages)

    def test_bad_base_stat_range_detected(self):
        _, diagnostics = _validate("bad_base_stat_range.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("hp 999 out of range [-128, 127]" in m for m in messages), messages)

    def test_bad_promotion_gain_range_detected(self):
        _, diagnostics = _validate("bad_promotion_gain_range.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("hp -5 out of range [0, 255]" in m for m in messages), messages)


class ClassesHeaderConstantsTests(unittest.TestCase):
    def test_reads_class_attributes_without_helper_combos(self):
        flags = classes_schema.read_class_attributes(_MINI_BMUNIT_HEADER)
        self.assertIn("CA_LORD", flags)
        self.assertIn("CA_PROMOTED", flags)
        self.assertNotIn("CA_MOUNTEDAID", flags)

    def test_reads_live_msg_count(self):
        self.assertEqual(classes_schema.read_msg_count(_MINI_MSG_HEADER), 10)

    def test_reads_live_portrait_count(self):
        self.assertEqual(classes_schema.read_portrait_count(_MINI_PORTRAIT_SOURCE), 3)

    def test_reads_live_sms_count(self):
        self.assertEqual(classes_schema.read_sms_count(_MINI_SMS_SOURCE), 3)

    def test_reads_class_data_array_capacity(self):
        self.assertEqual(
            classes_schema.read_class_data_array_capacity("baseRanks", _MINI_BMUNIT_HEADER), 8
        )
        self.assertEqual(
            classes_schema.read_class_data_array_capacity("pMovCostTable", _MINI_BMUNIT_HEADER), 3
        )


if __name__ == "__main__":
    unittest.main()
