import os
import unittest

from scripts.generated_data.characters import schema as characters_schema
from scripts.generated_data.classes import schema as classes_schema
from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.items import schema as items_schema
from scripts.generated_data.movecost import generate as movecost_generate
from scripts.generated_data.movecost import schema as movecost_schema
from scripts.generated_data.shops import schema as shops_schema
from scripts.generated_data.supports import schema as supports_schema
from scripts.generated_data.terrainstats import generate as terrainstats_generate
from scripts.generated_data.terrainstats import schema as terrainstats_schema
from scripts.generated_data.tests._util import fixture_path
from scripts.generated_data.tests.test_cli import run_cli
from scripts.generated_data.units import schema as units_schema
from scripts.generated_data.weapontriangle import generate as weapontriangle_generate
from scripts.generated_data.weapontriangle import schema as weapontriangle_schema


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SCHEMAS = dict(
    characters=characters_schema, items=items_schema, movecost=movecost_schema, shops=shops_schema,
    supports=supports_schema, terrainstats=terrainstats_schema, units=units_schema,
    weapontriangle=weapontriangle_schema,
)


def _validate(schema, table, fixture_name, **kwargs):
    records = schema.load_records(fixture_path(table, fixture_name))
    diagnostics = DiagnosticCollector()
    schema.validate(records, diagnostics, **kwargs)
    return records, diagnostics


def _validate_table(table, fixture_name):
    if table == "classes":
        dependencies = {
            "terrainstats": terrainstats_schema.load_records(
                fixture_path("classes", "deps_terrainstats.json")
            )
        }
        return _validate(
            classes_schema,
            table,
            fixture_name,
            dependency_records=dependencies,
            classes_header=fixture_path(table, "mini_classes.h"),
            bmunit_header=fixture_path(table, "mini_bmunit.h"),
            bmitem_header=fixture_path(table, "mini_bmitem.h"),
            ekrbattle_header=fixture_path(table, "mini_ekrbattle.h"),
            variables_header=fixture_path(table, "mini_variables.h"),
            msg_header=fixture_path(table, "mini_msg.h"),
            portrait_source=fixture_path(table, "mini_portrait_data.c"),
            sms_source=fixture_path(table, "mini_sms_data.c"),
        )
    if table == "items":
        return _validate(
            items_schema,
            table,
            fixture_name,
            items_header=fixture_path(table, "mini_items.h"),
            bmitem_header=fixture_path(table, "mini_bmitem.h"),
            variables_header=fixture_path(table, "mini_variables.h"),
            msg_header=fixture_path(table, "mini_msg.h"),
            icon_source=fixture_path(table, "mini_item_icon.c"),
        )
    if table in ("movecost", "terrainstats"):
        return _validate(
            SCHEMAS[table],
            table,
            fixture_name,
            terrains_header=fixture_path(table, "terrains_small.h"),
        )
    return _validate(SCHEMAS[table], table, fixture_name)


def _cli_args(table, fixture_name):
    args = ["validate", "--table", table, "--source", fixture_path(table, fixture_name), "--no-roundtrip"]
    if table == "eventlists":
        args.extend(
            value
            for dependency in ("units", "shops", "traps", "eventscripts")
            for value in (
                "--dep-source",
                "{}={}".format(dependency, fixture_path("eventlists", "deps_{}.json".format(dependency))),
            )
        )
    return args


class SchemaEvidenceTests(unittest.TestCase):
    def test_schema_fixtures(self):
        valid_cases = (("characters", 3), ("items", 3), ("supports", 3))
        failure_cases = (
            ("characters", "bad_base_ranks_itype_ref.json", "ITYPE_NOT_A_REAL_TYPE"),
            ("classes", "bad_base_ranks_itype_ref.json", "ITYPE_NOT_A_REAL_TYPE"),
            ("characters", "bad_base_ranks_wexp_ref.json", "WPN_EXP_NOT_REAL"),
            ("classes", "bad_base_ranks_wexp_ref.json", "WPN_EXP_NOT_REAL"),
            ("characters", "bad_base_stat_range.json", "hp 999 out of range [-128, 127]"),
            ("classes", "bad_base_stat_range.json", "hp 999 out of range [-128, 127]"),
            ("classes", "bad_text_id.json", "nameTextId 9999 out of range [0, 9]"),
            ("items", "bad_text_id.json", "nameTextId 9999 out of range [0, 9]"),
            ("movecost", "extra_terrain_key.json", "TERRAIN_NOT_REAL"),
            ("terrainstats", "extra_terrain_key.json", "TERRAIN_NOT_REAL"),
            ("movecost", "bad_range.json", "200"),
            ("terrainstats", "bad_range.json", "200"),
            ("weapontriangle", "bad_range.json", "200"),
            ("shops", "missing_item_ref.json", "ITEM_NOT_A_REAL_ITEM"),
            ("units", "missing_item_ref.json", "ITEM_NOT_A_REAL_ITEM"),
        )

        for table, expected_count in valid_cases:
            with self.subTest(table=table, fixture="valid.json"):
                records, diagnostics = _validate_table(table, "valid.json")
                self.assertTrue(diagnostics.ok, msg=diagnostics.render())
                self.assertEqual(len(records), expected_count)

        for table, fixture_name, expected_error in failure_cases:
            with self.subTest(table=table, fixture=fixture_name):
                _, diagnostics = _validate_table(table, fixture_name)
                messages = [str(error) for error in diagnostics.errors]
                self.assertFalse(diagnostics.ok)
                self.assertTrue(any(expected_error in message for message in messages), messages)
                if fixture_name == "bad_range.json":
                    self.assertTrue(any("out of range" in message for message in messages), messages)

    def test_generated_outputs_are_repeatable(self):
        cases = (
            ("movecost", movecost_schema, movecost_generate),
            ("terrainstats", terrainstats_schema, terrainstats_generate),
            ("weapontriangle", weapontriangle_schema, weapontriangle_generate),
        )

        for table, schema, generator in cases:
            with self.subTest(table=table):
                source = os.path.join(REPO_ROOT, "src", "data", "{}.json".format(table))
                records = schema.load_records(source)
                self.assertEqual(
                    generator.generate_c_source(records, source),
                    generator.generate_c_source(records, source),
                )


class CliEvidenceTests(unittest.TestCase):
    def test_valid_fixtures_pass(self):
        for table, expected_output in (
            ("units", "OK:"), ("shops", None), ("traps", None), ("eventscripts", None),
            ("eventlists", "OK:"), ("characters", "OK:"),
        ):
            with self.subTest(table=table, fixture="valid.json"):
                code, out, err = run_cli(_cli_args(table, "valid.json"))
                self.assertEqual(code, 0, msg=out + err)
                if expected_output:
                    self.assertIn(expected_output, out)

    def test_invalid_fixtures_fail(self):
        cases = (
            ("units", "duplicate_group.json", ("duplicate_group.json",)),
            ("shops", "missing_item_ref.json", ("undefined item reference",)),
            ("traps", "bad_type.json", ("undefined trap type reference",)),
            ("eventscripts", "undeclared_symbol.json", ("is not declared in",)),
            ("eventlists", "missing_unit_ref.json", ("missing_unit_ref.json", "undefined unit group reference")),
            (
                "chapterbundle",
                "cli_missing_support_owner.json",
                ("cli_missing_support_owner.json", "missing from supportOwners.required"),
            ),
            ("items", "bad_weapon_type.json", ("undefined weapon type reference",)),
            ("classes", "bad_class_ref.json", ("undefined class reference 'CLASS_NOT_A_REAL_CLASS'",)),
            ("characters", "duplicate_symbolic.json", ("duplicate_symbolic.json", "duplicate character designator 1")),
        )

        for table, fixture_name, expected_errors in cases:
            with self.subTest(table=table, fixture=fixture_name):
                code, out, err = run_cli(_cli_args(table, fixture_name))
                self.assertEqual(code, 1, msg=out + err)
                for expected_error in expected_errors:
                    self.assertIn(expected_error, err)


if __name__ == "__main__":
    unittest.main()
