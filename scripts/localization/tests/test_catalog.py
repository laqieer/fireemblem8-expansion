import argparse
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization import cli, schema
from scripts.localization.catalog import (
    RegistryEntry,
    load_catalog,
    parse_registry,
)
from scripts.localization.pseudo import apply_pseudo_policy, pseudoize
from scripts.localization.schema import SchemaError


def _base_registry():
    return {
        "messages": [
            {
                "id": 0,
                "key": "a.one",
                "status": "active",
                "surface": "framework_generic",
                "max_width": 20,
                "max_decoded_bytes": 32,
            },
            {
                "id": 1,
                "key": "a.two",
                "status": "active",
                "surface": "framework_generic",
                "max_width": 20,
                "max_decoded_bytes": 32,
            },
        ]
    }


def _write(directory: Path, registry: dict, strings: dict):
    reg_path = directory / "registry.json"
    cat_path = directory / "catalog.en.json"
    reg_path.write_text(json.dumps(registry), encoding="utf-8")
    cat_path.write_text(json.dumps({"locale": "en", "strings": strings}), encoding="utf-8")
    return reg_path, cat_path


def _load(registry_path: Path, catalog_path: Path):
    return load_catalog(
        registry_path=registry_path,
        catalog_paths={"en": catalog_path},
    )


class EmissionDocumentationContractTests(unittest.TestCase):
    def test_authoring_contract_matches_schema_and_cli(self):
        document = (ROOT / "docs" / "localization.md").read_text(encoding="utf-8")
        contracts = [
            json.loads(block)
            for block in re.findall(
                r"```json\n(.*?)\n```",
                document,
                flags=re.DOTALL,
            )
            if '"contract": "registry-emission-v1"' in block
        ]
        self.assertEqual(len(contracts), 1)
        contract = contracts[0]

        self.assertEqual(contract["field"], "emission")
        self.assertEqual(contract["active_default"], schema.DEFAULT_EMISSION)
        self.assertEqual(contract["allowed_values"], list(schema.EMISSIONS))
        self.assertEqual(
            contract["stable_ids_and_authored_translations"],
            "retained",
        )
        self.assertEqual(
            contract["materialized_values_by_profile"],
            {
                profile: [
                    emission
                    for emission in schema.EMISSIONS
                    if RegistryEntry(
                        id=0,
                        key=emission,
                        status=schema.STATUS_ACTIVE,
                        emission=emission,
                    ).emits_for(profile)
                ]
                for profile in schema.EMISSION_PROFILES
            },
        )

        parser = cli.build_parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        for command in ("generate", "check", "budget"):
            action = next(
                candidate
                for candidate in subparsers.choices[command]._actions
                if "--emission-profile" in candidate.option_strings
            )
            with self.subTest(command=command):
                self.assertEqual(
                    contract["cli"]["option"],
                    "--emission-profile",
                )
                self.assertEqual(
                    contract["cli"]["choices"],
                    list(action.choices),
                )
                self.assertEqual(contract["cli"]["default"], action.default)


class ParseRegistryTests(unittest.TestCase):
    def test_valid_registry_parses(self):
        entries = parse_registry(_base_registry())
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].key, "a.one")

    def test_duplicate_id_rejected(self):
        reg = _base_registry()
        reg["messages"][1]["id"] = 0
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_duplicate_key_rejected(self):
        reg = _base_registry()
        reg["messages"][1]["key"] = "a.one"
        reg["messages"][1]["id"] = 5
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_out_of_order_ids_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["id"] = 5
        reg["messages"][1]["id"] = 1
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_negative_id_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["id"] = -1
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_max_assignable_id_65534_active_accepted(self):
        reg = _base_registry()
        reg["messages"][0]["id"] = 0
        reg["messages"][1]["id"] = schema.MSG_ID_MAX
        entries = parse_registry(reg)
        self.assertEqual(entries[1].id, 0xFFFE)

    def test_max_assignable_id_65534_tombstone_accepted(self):
        reg = _base_registry()
        reg["messages"].append(
            {"id": schema.MSG_ID_MAX, "key": "a.retired", "status": "tombstone"}
        )
        entries = parse_registry(reg)
        self.assertEqual(entries[2].id, 0xFFFE)
        self.assertEqual(entries[2].status, "tombstone")

    def test_sentinel_id_65535_active_rejected(self):
        reg = _base_registry()
        reg["messages"][1]["id"] = 0xFFFF
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_sentinel_id_65535_tombstone_rejected(self):
        reg = _base_registry()
        reg["messages"].append({"id": 0xFFFF, "key": "a.retired", "status": "tombstone"})
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_id_65536_rejected(self):
        reg = _base_registry()
        reg["messages"][1]["id"] = 65536
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_id_70000_rejected(self):
        reg = _base_registry()
        reg["messages"][1]["id"] = 70000
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_bool_id_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["id"] = True
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_float_id_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["id"] = 1.5
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_string_id_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["id"] = "0"
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_invalid_status_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["status"] = "bogus"
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_invalid_surface_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["surface"] = "not-a-surface"
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_pseudo_policy_defaults_to_transform(self):
        entries = parse_registry(_base_registry())
        self.assertEqual(
            entries[0].pseudo_policy,
            schema.PSEUDO_POLICY_TRANSFORM,
        )

    def test_emission_defaults_to_always(self):
        entries = parse_registry(_base_registry())
        self.assertEqual(entries[0].emission, schema.EMISSION_ALWAYS)

    def test_debug_only_emission_is_accepted(self):
        reg = _base_registry()
        reg["messages"][0]["emission"] = schema.EMISSION_DEBUG_ONLY
        entries = parse_registry(reg)
        self.assertEqual(entries[0].emission, schema.EMISSION_DEBUG_ONLY)

    def test_invalid_emission_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["emission"] = "sometimes"
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_pseudo_policy_preserve_accepted(self):
        reg = _base_registry()
        reg["messages"][0]["pseudo_policy"] = schema.PSEUDO_POLICY_PRESERVE
        entries = parse_registry(reg)
        self.assertEqual(
            entries[0].pseudo_policy,
            schema.PSEUDO_POLICY_PRESERVE,
        )

    def test_invalid_pseudo_policy_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["pseudo_policy"] = "decorate-sometimes"
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_tombstone_pseudo_policy_rejected(self):
        reg = _base_registry()
        reg["messages"].append(
            {
                "id": 2,
                "key": "a.retired",
                "status": "tombstone",
                "pseudo_policy": schema.PSEUDO_POLICY_PRESERVE,
            }
        )
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_tombstone_emission_rejected(self):
        reg = _base_registry()
        reg["messages"].append(
            {
                "id": 2,
                "key": "a.retired",
                "status": "tombstone",
                "emission": schema.EMISSION_DEBUG_ONLY,
            }
        )
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_max_width_out_of_range_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["max_width"] = 0
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_max_decoded_bytes_out_of_range_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["max_decoded_bytes"] = 0
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_tombstone_entry_needs_no_surface(self):
        reg = _base_registry()
        reg["messages"].append({"id": 2, "key": "a.retired", "status": "tombstone"})
        entries = parse_registry(reg)
        self.assertEqual(entries[2].status, "tombstone")
        self.assertIsNone(entries[2].surface)

    def test_reused_tombstone_id_rejected(self):
        # A tombstone entry occupies its id permanently; a later entry
        # (active or tombstone) must not reuse that same numeric id.
        reg = _base_registry()
        reg["messages"].append({"id": 2, "key": "a.retired", "status": "tombstone"})
        reg["messages"].append({"id": 2, "key": "a.reused", "status": "active",
                                 "surface": "framework_generic", "max_width": 10,
                                 "max_decoded_bytes": 16})
        with self.assertRaises(SchemaError):
            parse_registry(reg)

    def test_empty_messages_rejected(self):
        with self.assertRaises(SchemaError):
            parse_registry({"messages": []})


class LoadCatalogTests(unittest.TestCase):
    def test_valid_catalog_loads_with_pseudo_derived(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, _base_registry(), {"a.one": "Hello", "a.two": "World"}
            )
            loaded = _load(reg_path, cat_path)
            self.assertEqual(loaded.en_strings["a.one"], "Hello")
            self.assertIn("a.one", loaded.pseudo_strings)
            self.assertNotEqual(loaded.pseudo_strings["a.one"], loaded.en_strings["a.one"])

    def test_preserve_policy_keeps_selected_pseudo_entry_exact(self):
        reg = _base_registry()
        reg["messages"][0]["pseudo_policy"] = schema.PSEUDO_POLICY_PRESERVE
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, reg, {"a.one": "Identifier 123", "a.two": "World"}
            )
            loaded = _load(reg_path, cat_path)
            self.assertEqual(
                loaded.pseudo_strings["a.one"],
                loaded.en_strings["a.one"],
            )
            self.assertEqual(
                loaded.pseudo_strings["a.two"],
                pseudoize(loaded.en_strings["a.two"]),
            )

    def test_missing_catalog_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(tmp_path, _base_registry(), {"a.one": "Hello"})
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_extra_catalog_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, _base_registry(),
                {"a.one": "Hello", "a.two": "World", "a.extra": "Nope"},
            )
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_non_ascii_utf8_text_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, _base_registry(), {"a.one": "Hell\u00f6", "a.two": "World"}
            )
            loaded = _load(reg_path, cat_path)
            self.assertEqual(loaded.en_strings["a.one"], "Hellö")

    def test_control_scalar_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, _base_registry(), {"a.one": "Bad\tTab", "a.two": "World"}
            )
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_c1_control_scalar_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, _base_registry(), {"a.one": "Bad\u0080", "a.two": "World"}
            )
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_surrogate_rejected_even_when_json_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, _base_registry(), {"a.one": "\ud800", "a.two": "World"}
            )
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_invalid_utf8_source_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, _base_registry(), {"a.one": "Hello", "a.two": "World"}
            )
            cat_path.write_bytes(
                b'{"locale":"en","strings":{"a.one":"' + bytes([0xFF]) + b'"}}'
            )
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_explicit_null_translation_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, _base_registry(), {"a.one": None, "a.two": "World"}
            )
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_width_overflow_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["max_width"] = 3
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(tmp_path, reg, {"a.one": "Hello", "a.two": "World"})
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_decoded_bytes_overflow_rejected(self):
        reg = _base_registry()
        reg["messages"][0]["max_decoded_bytes"] = 3
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(tmp_path, reg, {"a.one": "Hello", "a.two": "World"})
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_pseudo_overflow_rejected_even_if_english_fits(self):
        # English text fits its byte budget, but the pseudo transform's
        # deterministic vowel-doubling/bracket expansion pushes it over --
        # this must also fail visibly at build time (never silently pass).
        reg = _base_registry()
        reg["messages"][0]["max_decoded_bytes"] = 12
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, reg, {"a.one": "aeiouaeiou", "a.two": "World"}
            )
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_placeholder_parity_holds_for_real_pseudo_transform(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, _base_registry(), {"a.one": "Sample {0}", "a.two": "World"}
            )
            loaded = _load(reg_path, cat_path)
            self.assertIn("{0}", loaded.pseudo_strings["a.one"])

    def test_real_locale_placeholder_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, en_path = _write(
                tmp_path, _base_registry(), {"a.one": "Value {0}", "a.two": "World"}
            )
            ja_path = tmp_path / "catalog.ja.json"
            ja_path.write_text(
                json.dumps(
                    {"locale": "ja", "strings": {"a.one": "値 {1}", "a.two": "世界"}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SchemaError):
                load_catalog(
                    registry_path=reg_path,
                    catalog_paths={"en": en_path, "ja": ja_path},
                )

    def test_real_locale_newline_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, en_path = _write(
                tmp_path, _base_registry(), {"a.one": "Line one\nLine two", "a.two": "World"}
            )
            ja_path = tmp_path / "catalog.ja.json"
            ja_path.write_text(
                json.dumps(
                    {"locale": "ja", "strings": {"a.one": "一行だけ", "a.two": "世界"}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SchemaError):
                load_catalog(
                    registry_path=reg_path,
                    catalog_paths={"en": en_path, "ja": ja_path},
                )

    def test_sparse_non_english_catalog_is_valid_for_english_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, en_path = _write(
                tmp_path, _base_registry(), {"a.one": "Hello", "a.two": "World"}
            )
            ja_path = tmp_path / "catalog.ja.json"
            ja_path.write_text(
                json.dumps(
                    {"locale": "ja", "strings": {"a.one": "こんにちは"}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            loaded = load_catalog(
                registry_path=reg_path,
                catalog_paths={"en": en_path, "ja": ja_path},
            )
            self.assertEqual(loaded.missing_keys("ja"), ("a.two",))

    def test_cjk_surface_width_counts_wide_scalars(self):
        reg = _base_registry()
        reg["messages"][0]["max_width"] = 3
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, reg, {"a.one": "日本", "a.two": "World"}
            )
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_utf8_byte_budget_counts_encoded_bytes(self):
        reg = _base_registry()
        reg["messages"][0]["max_decoded_bytes"] = 6
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            reg_path, cat_path = _write(
                tmp_path, reg, {"a.one": "日本", "a.two": "World"}
            )
            with self.assertRaises(SchemaError):
                _load(reg_path, cat_path)

    def test_real_repository_registry_and_catalog_load_cleanly(self):
        # The committed texts/expansion/registry.json + catalog.en.json
        # this sprint ships must themselves pass every check above.
        loaded = load_catalog()
        self.assertGreater(len(loaded.active_entries), 0)
        self.assertGreaterEqual(len(loaded.tombstone_entries), 1)
        self.assertEqual(
            loaded.authored_locales,
            ("en", "ja", "zh-Hans", "fr", "de", "es", "it"),
        )
        self.assertEqual(
            loaded.generated_locales,
            ("en", "ja", "zh-Hans", "fr", "de", "es", "it", "qps-ploc"),
        )
        for locale in ("ja", "zh-Hans", "fr", "de", "es", "it"):
            self.assertEqual(loaded.missing_keys(locale), ())
        self.assertEqual(loaded.en_strings["framework.locale_name.ja"], "Japanese")
        self.assertEqual(
            loaded.en_strings["framework.locale_name.zh_hans"],
            "Simplified Chinese",
        )
        self.assertEqual(loaded.en_strings["framework.locale_short_name.ja"], "JA")
        self.assertEqual(loaded.en_strings["framework.locale_short_name.zh_hans"], "ZH")
        preserve_entries = [
            entry.key
            for entry in loaded.active_entries
            if entry.pseudo_policy == schema.PSEUDO_POLICY_PRESERVE
        ]
        self.assertEqual(
            preserve_entries,
            [
                "raw_surface.diagnostic.build_timestamp",
                "framework.locale_short_name.fr",
                "framework.locale_short_name.de",
                "framework.locale_short_name.es",
                "framework.locale_short_name.it",
                "ui.presentation.chapter_title_default",
            ],
        )
        for entry in loaded.active_entries:
            en_text = loaded.en_strings[entry.key]
            expected = apply_pseudo_policy(en_text, entry.pseudo_policy)
            self.assertEqual(loaded.pseudo_strings[entry.key], expected)


if __name__ == "__main__":
    unittest.main()
