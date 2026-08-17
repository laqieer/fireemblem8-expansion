import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization import eu, eu_mapping
from scripts.localization.game_catalog.build import build_game_catalog


class EuResourceTests(unittest.TestCase):
    def test_committed_extraction_and_mapping_are_current(self):
        eu.check()
        eu_mapping.check()

    def test_source_manifest_pins_authorized_rom_and_all_five_banks(self):
        manifest = json.loads(eu.TEXT_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["source"]["sha256"], eu.EU_ROM_SHA256)
        self.assertEqual(manifest["bank_order"], ["en", "de", "fr", "es", "it"])
        for locale in manifest["bank_order"]:
            self.assertEqual(
                manifest["locales"][locale]["message_count"],
                eu.MESSAGE_COUNT,
            )

    def test_shared_eu_font_covers_representative_accents(self):
        manifest = json.loads(eu.FONT_MANIFEST_PATH.read_text(encoding="utf-8"))
        codepoints = set(manifest["used_non_ascii_codepoints"])
        for codepoint in ("U+00E9", "U+00F1", "U+00FC", "U+00E8"):
            self.assertIn(codepoint, codepoints)
        self.assertGreater(manifest["styles"]["system"]["glyph_count"], 50)
        self.assertGreater(manifest["styles"]["talk"]["glyph_count"], 50)

    def test_ui_manifest_covers_all_extracted_surface_families(self):
        manifest = json.loads(eu.UI_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["compressed_resources"]), 46)
        self.assertEqual(len(manifest["raw_resources"]), 1)
        self.assertEqual(len(manifest["ap_resources"]), 3)
        self.assertEqual(manifest["chapter_titles"]["count"], 88)
        for locale in eu.PRODUCTION_EU_LOCALES:
            self.assertEqual(
                len(manifest["subtitles"][locale]["slides"]),
                eu.SUBTITLE_COUNT,
            )
            self.assertEqual(
                len(manifest["chapter_titles"]["variants"][locale]["entries"]),
                eu.CHAPTER_TITLE_COUNT,
            )


class EuGameCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = build_game_catalog(enabled_locales=("fr", "de", "es", "it"))

    def test_every_eu_locale_materializes_every_fe8u_target(self):
        self.assertEqual(self.build.enabled_locales, ("fr", "de", "es", "it"))
        for locale in self.build.enabled_locales:
            bundle = self.build.locale_bundle(locale)
            self.assertEqual(len(bundle.entries), 3414)
            self.assertTrue(all(entry.present for entry in bundle.entries))

    def test_official_indexed_authored_and_concat_sources_are_present(self):
        french = self.build.locale_bundle("fr").entries
        self.assertEqual(french[0x0002].mapping_source_kind, "eu_indexed")
        self.assertIn("Niveau d'arme", french[0x0002].source_text)
        self.assertEqual(french[0x040C].mapping_source_kind, "eu_authored")
        self.assertEqual(french[0x0B61].mapping_source_kind, "eu_concat")
        self.assertEqual(french[0x0884].mapping_source_kind, "eu_english_preserve")

    def test_generated_report_has_zero_eu_fallback(self):
        for locale in self.build.enabled_locales:
            report = self.build.report["locales"][locale]
            self.assertEqual(report["present_count"], 3414)
            self.assertEqual(report["explicit_fallback_count"], 0)
            self.assertEqual(report["provider_unavailable_count"], 0)

    def test_combined_cjk_and_eu_cli_profile_generates(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.localization.game_catalog",
                    "generate",
                    "--out-dir",
                    tmp,
                    "--enabled-locales",
                    "ja,zh-Hans,fr,de,es,it",
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            report = json.loads(
                (Path(tmp) / "game_localization_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                report["enabled_locales"],
                ["ja", "zh-Hans", "fr", "de", "es", "it"],
            )
            self.assertEqual(
                report["compiled_locales"],
                ["en", "ja", "zh-Hans", "fr", "de", "es", "it"],
            )


if __name__ == "__main__":
    unittest.main()
