import json
import re
import unittest
from pathlib import Path

from scripts.localization.catalog import load_catalog
from scripts.localization.game_catalog.build import build_game_catalog
from scripts.localization.game_locales.ending_metrics import (
    _ascii_widths,
    _cjk_widths,
    _line_width,
)
from scripts.localization.legacy_spacing import (
    LEGACY_SJIS_SPACE_BYTES,
    LEGACY_SJIS_SPACE_WIDTH,
)


ROOT = Path(__file__).resolve().parents[3]


class DebugToolsLocalizationTests(unittest.TestCase):
    ACTION_LABELS = {
        "Fast Boot: Chapter 2": "debug.action.fastboot_ch2",
        "Weather": "debug.action.weather",
        "Fog": "debug.action.fog",
        "Fast Boot: Ch4 Prep": "debug.action.fastboot_ch4prep",
        "Unit Inspect": "debug.action.unit_inspect",
        "Convoy Inspect": "debug.action.convoy_inspect",
        "Flag/Chapter": "debug.action.flag_chapter",
        "RNG Inspect": "debug.action.rng_inspect",
        "Save State": "debug.action.save_state",
    }
    EXPANSION_ADAPTERS = {
        "Back": "framework.back",
        "Confirm Heal to Full": "debug.confirm.heal_full",
        "Confirm Add Item": "debug.confirm.add_item",
        "Confirm Toggle Flag": "debug.confirm.toggle_flag",
        "Confirm Reseed": "debug.confirm.reseed",
        "DBGTOOLS": "debug.status.hub",
        "DBGTOOLS ERR": "debug.status.hub_error",
        "UNIT HP": "debug.status.unit_hp",
        "UNIT N/A": "debug.status.unit_unavailable",
        "CONVOY": "debug.status.convoy",
        "CH": "debug.status.chapter",
        "FLAG": "debug.status.flag",
        "RNG SEED": "debug.status.rng_seed",
        "SAVE STATE": "debug.status.save_state",
    }

    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(
            (ROOT / "texts/expansion/registry.json").read_text(encoding="utf-8")
        )
        cls.catalogs = {
            locale: json.loads(
                (ROOT / f"texts/expansion/catalog.{locale}.json").read_text(
                    encoding="utf-8"
                )
            )["strings"]
            for locale in ("en", "ja", "zh-Hans")
        }
        cls.loaded_catalog = load_catalog()
        cls.game_catalog = build_game_catalog(
            enabled_locales=("ja", "zh-Hans")
        )
        cls.header = (ROOT / "include/expansion_debugtools.h").read_text(
            encoding="utf-8"
        )
        cls.ascii_widths = _ascii_widths(ROOT)
        cls.cjk_widths = {
            locale: _cjk_widths(ROOT, locale)[0]
            for locale in ("ja", "zh-Hans")
        }

    @classmethod
    def _pixel_width(cls, text, locale):
        return _line_width(
            text,
            locale=locale,
            ascii_widths=cls.ascii_widths,
            cjk_widths=cls.cjk_widths.get(locale, {}),
        )

    @classmethod
    def _catalog_entry_width(cls, catalog, message_id, locale):
        payload = catalog.decode_entry(message_id)[:-1]
        legacy_spaces = payload.count(LEGACY_SJIS_SPACE_BYTES)
        text = payload.replace(LEGACY_SJIS_SPACE_BYTES, b"").decode("utf-8")
        return (
            cls._pixel_width(text, locale)
            + legacy_spaces * LEGACY_SJIS_SPACE_WIDTH
        )

    @classmethod
    def _constant(cls, name):
        match = re.search(rf"\b{name}\s*=\s*(\d+)", cls.header)
        if match is None:
            raise AssertionError(f"{name} is missing from expansion_debugtools.h")
        return int(match.group(1))

    def test_japanese_and_chinese_debug_adapters_do_not_fall_back_to_english(self):
        for key in self.EXPANSION_ADAPTERS.values():
            with self.subTest(key=key):
                self.assertNotEqual(self.catalogs["ja"][key], self.catalogs["en"][key])
                self.assertNotEqual(
                    self.catalogs["zh-Hans"][key],
                    self.catalogs["en"][key],
                )

    def test_every_generated_debug_menu_label_fits_actual_text_allocation(self):
        menu_width_tiles = self._constant("DEBUGTOOLS_MENU_WIDTH_TILES")
        allocation_pixels = (menu_width_tiles - 1) * 8
        self.assertLessEqual(1 + menu_width_tiles, 30)

        menu_keys = {
            "framework.back",
            *self.ACTION_LABELS.values(),
            *(
                key
                for key in self.EXPANSION_ADAPTERS.values()
                if key.startswith("debug.confirm.")
            ),
        }
        for locale in ("en", "ja", "zh-Hans", "qps-ploc"):
            strings = self.loaded_catalog.strings_for(locale)
            for key in sorted(menu_keys):
                with self.subTest(locale=locale, key=key):
                    self.assertLessEqual(
                        self._pixel_width(strings[key], locale),
                        allocation_pixels,
                    )

    def test_maximum_hub_rows_and_qps_labels_fit_allocator_budget(self):
        action_max = self._constant("DEBUGTOOLS_ACTION_MAX")
        contributor_max = self._constant("DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX")
        page_action_max = self._constant("DEBUGTOOLS_HUB_PAGE_ACTION_MAX")
        page_max = self._constant("DEBUGTOOLS_HUB_PAGE_MAX")
        menu_width = self._constant("DEBUGTOOLS_MENU_WIDTH_TILES")
        status_width = self._constant("DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES")
        capacity = self._constant("DEBUGTOOLS_TEXT_ALLOC_CAPACITY")
        configured_budget = self._constant(
            "DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET"
        )

        expected_budget = (page_action_max + 1) * (menu_width - 1) + status_width
        self.assertEqual(action_max, 18)
        self.assertEqual(contributor_max, 9)
        self.assertEqual(page_action_max, 9)
        self.assertEqual(page_max, 2)
        self.assertEqual(expected_budget, 187)
        self.assertEqual(configured_budget, expected_budget)
        self.assertLessEqual(expected_budget, capacity)

        qps = self.loaded_catalog.strings_for("qps-ploc")
        allocation_pixels = (menu_width - 1) * 8
        for key in sorted({"framework.back", *self.ACTION_LABELS.values()}):
            with self.subTest(key=key):
                self.assertLessEqual(
                    self._pixel_width(qps[key], "qps-ploc"),
                    allocation_pixels,
                )

    def test_every_generated_debug_status_row_fits_actual_surface_geometry(self):
        suffixes = {
            "debug.status.hub": " 18 2/2",
            "debug.status.hub_error": " -99",
            "debug.status.unit_hp": " 255/255",
            "debug.status.unit_unavailable": "",
            "debug.status.convoy": " 100/100",
            "debug.status.rng_seed": " FFFF",
            "debug.status.save_state": " -99",
        }
        for locale in ("en", "ja", "zh-Hans", "qps-ploc"):
            strings = self.loaded_catalog.strings_for(locale)
            allocation_pixels = (
                self._constant("DEBUGTOOLS_MENU_WIDTH_TILES") - 1
            ) * 8
            for key, suffix in suffixes.items():
                text = strings[key] + suffix
                width = (
                    self._pixel_width(text, locale)
                    if locale in ("ja", "zh-Hans")
                    else len(text) * 8
                )
                with self.subTest(locale=locale, key=key):
                    self.assertLessEqual(width, allocation_pixels)

            combined = (
                strings["debug.status.chapter"]
                + " 255 "
                + strings["debug.status.flag"]
                + " 1"
            )
            combined_width = (
                self._pixel_width(combined, locale)
                if locale in ("ja", "zh-Hans")
                else len(combined) * 8
            )
            with self.subTest(locale=locale, key="chapter+flag"):
                self.assertLessEqual(combined_width, allocation_pixels)

    def test_weather_and_fog_rows_fit_the_same_actual_menu_geometry(self):
        allocation_pixels = (
            self._constant("DEBUGTOOLS_MENU_WIDTH_TILES") - 1
        ) * 8
        rows = (
            (0x06AC, range(0x06B1, 0x06B8)),
            (0x06AD, (0x0849, 0x084A)),
        )
        for locale in ("en", "ja", "zh-Hans", "qps-ploc"):
            game_locale = "en" if locale == "qps-ploc" else locale
            catalog = (
                self.game_catalog.english.catalog
                if game_locale == "en"
                else self.game_catalog.locale_bundle(game_locale).catalog
            )
            for label_id, value_ids in rows:
                label_width = self._catalog_entry_width(
                    catalog,
                    label_id,
                    game_locale,
                )
                with self.subTest(
                    locale=locale,
                    label_id=f"0x{label_id:04X}",
                ):
                    self.assertLessEqual(8 + label_width, 64)
                for value_id in value_ids:
                    value_width = self._catalog_entry_width(
                        catalog,
                        value_id,
                        game_locale,
                    )
                    with self.subTest(
                        locale=locale,
                        label_id=f"0x{label_id:04X}",
                        value_id=f"0x{value_id:04X}",
                    ):
                        self.assertLessEqual(64 + value_width, allocation_pixels)


if __name__ == "__main__":
    unittest.main()
