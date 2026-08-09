import json
import re
import unittest
from collections import Counter
from pathlib import Path


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
    DIRECT_MENU_LABELS = Counter(
        {
            "Back": 6,
            "Confirm Heal to Full": 1,
            "Confirm Add Item": 1,
            "Confirm Toggle Flag": 1,
            "Confirm Reseed": 1,
            "Weather": 1,
            "Fog": 1,
        }
    )
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
        cls.sources = {
            name: (ROOT / f"src/{name}").read_text(encoding="utf-8")
            for name in (
                "debugtools_registry.c",
                "debugtools_actions.c",
                "debugtools_launcher.c",
                "debugtools_tools.c",
            )
        }
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

    def test_direct_debug_ui_literal_inventory_is_exact_and_closed(self):
        action_labels = []
        for name in (
            "debugtools_launcher.c",
            "debugtools_actions.c",
            "debugtools_tools.c",
        ):
            action_labels.extend(
                re.findall(
                    r"DebugToolsAction\s+\w+\s*=\s*\{\s*\d+,\s*\"([^\"]+)\"",
                    self.sources[name],
                )
            )
        self.assertEqual(
            Counter(action_labels),
            Counter(self.ACTION_LABELS.keys()),
        )

        menu_labels = []
        for name in (
            "debugtools_registry.c",
            "debugtools_actions.c",
            "debugtools_tools.c",
        ):
            menu_labels.extend(
                re.findall(r"\.name\s*=\s*\"([^\"]+)\"", self.sources[name])
            )
        self.assertEqual(Counter(menu_labels), self.DIRECT_MENU_LABELS)

        registry = {
            row["key"]: row
            for row in self.registry["messages"]
            if row["status"] == "active"
        }
        for literal, key in {
            **self.ACTION_LABELS,
            **self.EXPANSION_ADAPTERS,
        }.items():
            with self.subTest(literal=literal, key=key):
                self.assertIn(key, registry)
                self.assertEqual(self.catalogs["en"][key], literal)
                self.assertIn(key, self.catalogs["ja"])
                self.assertIn(key, self.catalogs["zh-Hans"])

    def test_every_direct_debug_literal_has_the_expected_runtime_adapter(self):
        registry = self.sources["debugtools_registry.c"]
        tools = self.sources["debugtools_tools.c"]

        for key_suffix in (
            "FASTBOOT_CH2",
            "WEATHER",
            "FOG",
            "FASTBOOT_CH4PREP",
            "UNIT_INSPECT",
            "CONVOY_INSPECT",
            "FLAG_CHAPTER",
            "RNG_INSPECT",
            "SAVE_STATE",
        ):
            self.assertIn(f"EXP_MSG_DEBUG_ACTION_{key_suffix}", registry)

        self.assertIn("EXP_MSG_FRAMEWORK_BACK", registry)
        self.assertIn("DebugToolsHub_BuiltinActionRowDraw", registry)
        self.assertIn("EXP_MSG_DEBUG_STATUS_HUB", registry)
        self.assertIn("EXP_MSG_DEBUG_STATUS_HUB_ERROR", registry)

        for key_suffix in (
            "CONFIRM_HEAL_FULL",
            "CONFIRM_ADD_ITEM",
            "CONFIRM_TOGGLE_FLAG",
            "CONFIRM_RESEED",
            "STATUS_UNIT_HP",
            "STATUS_UNIT_UNAVAILABLE",
            "STATUS_CONVOY",
            "STATUS_CHAPTER",
            "STATUS_FLAG",
            "STATUS_RNG_SEED",
            "STATUS_SAVE_STATE",
        ):
            self.assertIn(f"EXP_MSG_DEBUG_{key_suffix}", tools)

        self.assertEqual(tools.count("EXP_MSG_FRAMEWORK_BACK"), 5)
        self.assertIn("DebugToolsTools_LocalizedMenuItemDraw", tools)
        self.assertIn("ExpansionLocale_ResolveCurrent", tools)
        self.assertIn("PutDrawText(", tools)

        self.assertEqual(
            re.findall(
                r"PrintDebugStringToBG\([^;]*ExpansionLocale_ResolveCurrent",
                registry + tools,
                flags=re.DOTALL,
            ),
            [],
        )

    def test_japanese_and_chinese_debug_adapters_do_not_fall_back_to_english(self):
        for key in self.EXPANSION_ADAPTERS.values():
            with self.subTest(key=key):
                self.assertNotEqual(self.catalogs["ja"][key], self.catalogs["en"][key])
                self.assertNotEqual(
                    self.catalogs["zh-Hans"][key],
                    self.catalogs["en"][key],
                )


if __name__ == "__main__":
    unittest.main()
