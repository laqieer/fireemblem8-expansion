import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization import schema
from scripts.localization.catalog import LoadedCatalog, RegistryEntry, load_catalog
from scripts.localization.generate import (
    _c_string_literal,
    build_budget,
    build_catalog_c,
    build_msg_ids_header,
    generate,
    key_to_macro,
    locale_to_symbol,
)
from scripts.localization.pseudo import pseudoize


class KeyToMacroTests(unittest.TestCase):
    def test_dots_become_underscores(self):
        self.assertEqual(key_to_macro("framework.title"), "EXP_MSG_FRAMEWORK_TITLE")

    def test_mixed_separators_collapse(self):
        self.assertEqual(
            key_to_macro("framework.locale_name.qps_ploc"),
            "EXP_MSG_FRAMEWORK_LOCALE_NAME_QPS_PLOC",
        )


class BuildOutputsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def test_header_defines_every_active_key_once(self):
        header = build_msg_ids_header(self.catalog)
        for entry in self.catalog.active_entries:
            self.assertEqual(
                header.count(f"#define {key_to_macro(entry.key)} {entry.id}u"), 1
            )

    def test_header_excludes_tombstones(self):
        header = build_msg_ids_header(self.catalog)
        for entry in self.catalog.tombstone_entries:
            self.assertNotIn(key_to_macro(entry.key), header)

    def test_catalog_c_has_matching_array_lengths(self):
        source = build_catalog_c(self.catalog)
        active_count = len(self.catalog.active_entries)
        self.assertIn(f"const u16 gExpansionLocaleMsgCount = {active_count}u;", source)
        self.assertEqual(source.count("u,\n"), active_count)  # gExpansionLocaleMsgIds entries
        self.assertIn(
            "gExpansionLocaleCatalogs[EXPANSION_LOCALE_COUNT]", source
        )

    def test_authored_newline_emits_engine_line_break_control(self):
        self.assertEqual(
            _c_string_literal("Line one\nLine two"),
            '"Line one\\001Line two"',
        )

    def test_catalog_c_ids_ascending(self):
        source = build_catalog_c(self.catalog)
        ids_block = source.split("gExpansionLocaleMsgIds[] =")[1].split("};")[0]
        lines = [ln.strip() for ln in ids_block.strip().splitlines()]
        lines = [ln for ln in lines if ln not in ("{", "}")]
        ids = [int(tok.strip().rstrip("u,")) for tok in lines]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_charge_and_diagnostics_message_ids_are_stable_and_disjoint(self):
        entries_by_key = {
            entry.key: entry for entry in self.catalog.active_entries
        }
        diagnostic_keys = (
            "debug.view.state",
            "debug.view.engine",
            "debug.action.refresh",
            "debug.value.unavailable",
            "debug.field.context",
            "debug.field.clock",
            "debug.field.chapter",
            "debug.field.turn",
            "debug.field.phase",
            "debug.field.cursor",
            "debug.field.unit",
            "debug.field.character",
            "debug.field.class",
            "debug.field.hp",
            "debug.field.weather",
            "debug.field.fog",
            "debug.field.rng",
            "debug.field.proc_count",
            "debug.field.event",
            "debug.field.actions",
            "debug.field.log_retained",
            "debug.field.log_writes",
            "debug.field.log_last",
            "debug.field.assert_count",
            "debug.field.assert_last",
            "debug.value.context_title",
            "debug.value.context_map",
            "debug.value.context_prep",
            "debug.value.context_battle",
            "debug.value.phase_player",
            "debug.value.phase_enemy",
            "debug.value.phase_npc",
            "debug.value.phase_other",
        )

        self.assertEqual(entries_by_key["autoplay.charge.label"].id, 80)
        self.assertEqual(entries_by_key["autoplay.charge.help"].id, 81)
        self.assertEqual(
            {
                key: entries_by_key[key].id
                for key in (
                    "debug.action.chapter_skirmish",
                    "debug.selector.chapter",
                    "debug.selector.skirmish",
                    "debug.selector.eirika",
                    "debug.selector.ephraim",
                    "debug.selector.unavailable",
                )
            },
            {
                "debug.action.chapter_skirmish": 82,
                "debug.selector.chapter": 83,
                "debug.selector.skirmish": 84,
                "debug.selector.eirika": 85,
                "debug.selector.ephraim": 86,
                "debug.selector.unavailable": 87,
            },
        )
        self.assertEqual(
            [entries_by_key[key].id for key in diagnostic_keys],
            list(range(88, 121)),
        )

    def test_debug_only_phase_messages_keep_ids_but_release_omits_payloads(self):
        phase_keys = (
            "debug.confirm.turn_increment",
            "debug.confirm.turn_decrement",
            "debug.confirm.red_computer",
            "debug.confirm.red_blocked",
            "debug.confirm.green_computer",
            "debug.confirm.green_blocked",
            "debug.status.turn",
            "debug.mode.computer",
            "debug.mode.blocked",
        )
        entries = {entry.key: entry for entry in self.catalog.active_entries}
        header = build_msg_ids_header(self.catalog)
        debug_source = build_catalog_c(
            self.catalog, emission_profile=schema.EMISSION_PROFILE_DEBUG
        )
        release_source = build_catalog_c(
            self.catalog, emission_profile=schema.EMISSION_PROFILE_RELEASE
        )
        release_budget = build_budget(
            self.catalog, emission_profile=schema.EMISSION_PROFILE_RELEASE
        )

        self.assertEqual(
            [entries[key].emission for key in phase_keys],
            [schema.EMISSION_DEBUG_ONLY] * len(phase_keys),
        )
        for key in phase_keys:
            entry = entries[key]
            self.assertIn(
                f"#define {key_to_macro(key)} {entry.id}u",
                header,
                "stable IDs remain available to all profiles",
            )
            self.assertIn(
                self.catalog.en_strings[key],
                debug_source,
                "debug catalog must resolve the localized phase string",
            )
            self.assertNotIn(
                self.catalog.en_strings[key],
                release_source,
                "release catalog must omit the debug-only phase payload",
            )
            self.assertNotIn(
                f"    {entry.id}u,",
                release_source,
                "release catalog must omit the debug-only phase index",
            )
        self.assertEqual(
            release_budget["omitted_active_message_count"], len(phase_keys)
        )
        self.assertEqual(
            release_budget["active_message_count"],
            len(self.catalog.active_entries) - len(phase_keys),
        )

    def test_budget_reports_all_required_sections(self):
        budget = build_budget(self.catalog)
        for key in (
            "active_message_count",
            "tombstone_count",
            "pseudo_policy_counts",
            "catalog_string_bytes",
            "catalog_index_bytes",
            "scratch_budget_bytes",
            "scratch_slot_bytes_used_max",
            "codepoints",
            "limits",
        ):
            self.assertIn(key, budget)

    def test_budget_reports_pseudo_policy_counts(self):
        budget = build_budget(self.catalog)
        self.assertEqual(
            budget["pseudo_policy_counts"],
            dict(
                Counter(
                    entry.pseudo_policy
                    for entry in self.catalog.active_entries
                )
            ),
        )

    def test_generated_qps_catalog_preserves_build_timestamp(self):
        source = build_catalog_c(self.catalog)
        qps_block = source.split("gExpansionCatalog_qps_ploc[] =")[1].split("};")[0]
        timestamp = self.catalog.en_strings[
            "raw_surface.diagnostic.build_timestamp"
        ]
        self.assertIn(f'"{timestamp}"', qps_block)
        self.assertNotIn(f'"{pseudoize(timestamp)}"', qps_block)

    def test_budget_scratch_usage_within_budget(self):
        budget = build_budget(self.catalog)
        self.assertLessEqual(
            budget["scratch_slot_bytes_used_max"], budget["scratch_budget_bytes"]
        )

    def test_budget_reports_real_utf8_codepoints(self):
        budget = build_budget(self.catalog)
        self.assertEqual(budget["populated_descriptor_count"], 8)
        self.assertIn("ja", budget["codepoints"]["per_locale"])
        self.assertIn("zh-Hans", budget["codepoints"]["per_locale"])
        self.assertIn("fr", budget["codepoints"]["per_locale"])
        self.assertIn("U+62E1", budget["codepoints"]["utf8_scalars"])

    def test_generated_c_uses_exact_utf8_byte_escapes(self):
        source = build_catalog_c(self.catalog)
        self.assertNotIn("拡張フレームワーク", source)
        self.assertIn(r"\346\213\241", source)

    def test_every_stable_locale_has_descriptor_slot(self):
        source = build_catalog_c(self.catalog)
        for locale in schema.LOCALE_IDS:
            self.assertIn(f"/* {locale} */", source)
        self.assertEqual(source.count("{ NULL, NULL, 0u }"), 0)

    def test_locale_symbol_sanitizes_bcp47_separator(self):
        self.assertEqual(
            locale_to_symbol("zh-Hans"), "gExpansionCatalog_zh_Hans"
        )


class GenerateWritesFilesTests(unittest.TestCase):
    def test_generate_writes_all_three_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            written = generate(output_dir=out_dir)
            for path in written.values():
                self.assertTrue(path.is_file(), f"{path} missing")
            budget = json.loads(written["budget_json"].read_text(encoding="utf-8"))
            self.assertIn("active_message_count", budget)

    def test_generate_is_idempotent_write_if_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            generate(output_dir=out_dir)
            header_path = out_dir / "expansion_msg_ids.h"
            first_mtime = header_path.stat().st_mtime_ns
            generate(output_dir=out_dir)
            second_mtime = header_path.stat().st_mtime_ns
            self.assertEqual(first_mtime, second_mtime)

    def test_generated_catalog_c_compiles_with_declarations(self):
        # Minimal syntax sanity check without a full compiler: braces and
        # semicolons balance, and every extern declared in
        # include/expansion_locale.h has a matching definition here.
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            written = generate(output_dir=out_dir)
            source = written["catalog_c"].read_text(encoding="utf-8")
            self.assertEqual(source.count("{"), source.count("}"))
            for symbol in (
                "gExpansionLocaleMsgIds",
                "gExpansionLocaleMsgCount",
                "gExpansionCatalog_en",
                "gExpansionCatalog_ja",
                "gExpansionCatalog_zh_Hans",
                "gExpansionCatalog_qps_ploc",
                "gExpansionLocaleCatalogs",
                "gExpansionLocalePopulatedCount",
                "gExpansionLocaleTombstoneCount",
            ):
                self.assertIn(symbol, source)


class DefensiveIdBypassTests(unittest.TestCase):
    """Simulates a caller that constructs a LoadedCatalog directly,
    bypassing catalog.parse_registry's own id-range validation --
    generate.py's own defensive re-check (schema.MSG_ID_MAX /
    MSG_ID_INVALID) must still catch it."""

    def _catalog_with_bad_active_id(self, bad_id):
        entry = RegistryEntry(
            id=bad_id,
            key="a.bad",
            status="active",
            surface="framework_generic",
            max_width=20,
            max_decoded_bytes=32,
        )
        return LoadedCatalog(
            entries=(entry,),
            active_entries=(entry,),
            tombstone_entries=(),
            locale_strings={
                "en": {"a.bad": "Hello"},
                schema.PSEUDO_LOCALE: {"a.bad": "Hello"},
            },
            authored_locales=("en",),
            generated_locales=("en", schema.PSEUDO_LOCALE),
        )

    def test_build_msg_ids_header_rejects_sentinel_bypass(self):
        catalog = self._catalog_with_bad_active_id(schema.MSG_ID_INVALID)
        with self.assertRaises(schema.SchemaError):
            build_msg_ids_header(catalog)

    def test_build_catalog_c_rejects_sentinel_bypass(self):
        catalog = self._catalog_with_bad_active_id(schema.MSG_ID_INVALID)
        with self.assertRaises(schema.SchemaError):
            build_catalog_c(catalog)

    def test_build_msg_ids_header_rejects_over_u16_bypass(self):
        catalog = self._catalog_with_bad_active_id(70000)
        with self.assertRaises(schema.SchemaError):
            build_msg_ids_header(catalog)

    def test_build_msg_ids_header_accepts_max_assignable_id_bypass(self):
        catalog = self._catalog_with_bad_active_id(schema.MSG_ID_MAX)
        header = build_msg_ids_header(catalog)
        self.assertIn(f"{schema.MSG_ID_MAX}u", header)

    def test_generate_raises_before_writing_any_file_on_bad_id(self):
        catalog = self._catalog_with_bad_active_id(schema.MSG_ID_INVALID)
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "generated"

            import scripts.localization.generate as generate_module

            original_load_catalog = generate_module.load_catalog
            generate_module.load_catalog = lambda **kwargs: catalog
            try:
                with self.assertRaises(schema.SchemaError):
                    generate(output_dir=out_dir)
            finally:
                generate_module.load_catalog = original_load_catalog
            self.assertFalse(out_dir.exists() and any(out_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
