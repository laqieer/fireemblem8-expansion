import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization import extract_ui_graphics


class LocalizedUiGraphicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(
            (ROOT / "graphics/localized_ui/manifest.json").read_text(encoding="utf-8")
        )

    def test_committed_assets_and_generated_registry_are_current(self):
        extract_ui_graphics.check()

    def test_each_requested_surface_has_both_cjk_variants(self):
        self.assertEqual(self.manifest["chapter_title_count"], 88)
        self.assertEqual(self.manifest["subtitle_slide_count"], 7)

        expected_main_sprites = {
            "ja": (
                "0x08AA39DC",
                "062c6299df0c57248573202a34e94542ea119badceb9a4be52b3ac4aa7c8e9fd",
            ),
            "zh-Hans": (
                "0x08B44B40",
                "cd7a9bd0d77a10d1ce08140bf631ec4bb7e90ab7dc1e3734231ffa75ac6a0a1d",
            ),
        }
        for locale in ("ja", "zh-Hans"):
            variant = self.manifest["variants"][locale]
            self.assertEqual(set(variant["title"]), {"logo", "labels"})
            self.assertIsInstance(variant["menu"], int)
            self.assertIsInstance(variant["main_sprites"], int)
            main_sprites = variant["assets"][variant["main_sprites"]]
            self.assertEqual(main_sprites["name"], "menu/main_sprites")
            self.assertEqual(main_sprites["source_address"], expected_main_sprites[locale][0])
            self.assertEqual(main_sprites["raw_sha256"], expected_main_sprites[locale][1])
            self.assertEqual(main_sprites["raw_size"], 0x3800)
            self.assertEqual(
                main_sprites["dimensions"],
                {"height": 112, "tiles_per_row": 32, "width": 256},
            )
            self.assertEqual(len(variant["subtitle"]), 7)
            self.assertEqual(len(variant["chapter"]["entries"]), 88)

            for entry in variant["chapter"]["entries"]:
                self.assertEqual(len(entry), 3)
                self.assertIsNotNone(entry[0])

            for slide in variant["subtitle"]:
                self.assertGreater(slide["timer"], 0)
                self.assertIsInstance(slide["gfx"], int)
                self.assertIsInstance(slide["tsa"], int)

    def test_provenance_and_asset_types_are_pinned(self):
        self.assertEqual(
            self.manifest["sources"]["ja"]["sha256"],
            extract_ui_graphics.JP_SHA256,
        )
        self.assertEqual(
            self.manifest["sources"]["zh-Hans"]["sha256"],
            extract_ui_graphics.CN_SHA256,
        )
        self.assertEqual(self.manifest["sprite_layout"]["size"], 0xEC)
        self.assertEqual(
            self.manifest["sprite_layout"]["ja_address"],
            self.manifest["sprite_layout"]["zh_Hans_address"],
        )
        self.assertEqual(len(self.manifest["sprite_layout"]["sha256"]), 64)

        for locale in ("ja", "zh-Hans"):
            for asset in self.manifest["variants"][locale]["assets"]:
                path = ROOT / asset["path"]
                data = path.read_bytes()
                self.assertIn(path.suffix, {".png", ".bin"})
                if path.suffix == ".png":
                    raw, dimensions = extract_ui_graphics.decode_tiled_4bpp_png(data)
                    self.assertEqual(asset["kind"], "tiled_4bpp_png")
                    self.assertEqual(len(data), asset["png_size"])
                    self.assertEqual(len(raw), asset["raw_size"])
                    self.assertEqual(dimensions, asset["dimensions"])
                    self.assertEqual(len(raw) % 32, 0, path)
                else:
                    self.assertEqual(asset["kind"], "tsa")
                    self.assertEqual(len(data), asset["size"])
                    width = data[0] + 1
                    height = data[1] + 1
                    self.assertEqual(len(data), 2 + width * height * 2, path)

    def test_runtime_consumers_use_the_registry(self):
        expected = {
            "src/titlescreen.c": (
                "LocalizedUiGraphics_GetTitle",
                "LocalizedUiGraphics_GetTitleSprites",
            ),
            "src/savemenu.c": ("LocalizedUiGraphics_GetSaveMenuOptions",),
            "src/opsubtitle.c": ("LocalizedUiGraphics_GetSubtitleSlides",),
            "src/chapter_title.c": (
                "LocalizedUiGraphics_GetChapterTitle",
                "LocalizedUiGraphics_GetChapterTitleFrame",
                "LocalizedUiGraphics_GetChapterTitleTsa",
            ),
        }
        for path, selectors in expected.items():
            source = (ROOT / path).read_text(encoding="utf-8")
            for selector in selectors:
                self.assertIn(selector, source, (path, selector))

        registry = (ROOT / "src/data/localized_ui_graphics.c").read_text(
            encoding="utf-8"
        )
        self.assertIn("EXPANSION_LOCALE_JA", registry)
        self.assertIn("EXPANSION_LOCALE_ZH_HANS", registry)
        self.assertIn("LocalizedUiGraphics_GetSaveMenuMainSprites", registry)
        self.assertIn("return 0;", registry)

    def test_cjk_main_sprite_oam_layouts_match_regional_sheets(self):
        source = (ROOT / "src" / "savemenu_data.c").read_text(encoding="utf-8")
        expected_layouts = {
            "Ja": {
                0: (("32x16", "32x16", None, "100"), ("32x16", "32x16", "32", "104"),
                    ("32x16", "32x16", "64", "108"), ("32x16", "32x16", "96", "10C")),
                1: (("32x16", "32x16", None, "186"), ("16x16", "16x16", "32", "18A"),
                    ("32x16", "32x16", "48", "106"), ("32x16", "32x16", "80", "10A"),
                    ("16x16", "16x16", "112", "10E")),
                2: (("32x16", "32x16", "16", "110"), ("32x16", "32x16", "48", "114"),
                    ("32x16", "32x16", "80", "118")),
                3: (("32x16", "32x16", "16", "110"), ("16x16", "16x16", "48", "114"),
                    ("8x16", "8x16", "64", "116"), ("16x16", "16x16", "72", "15D"),
                    ("8x16", "8x16", "88", "15F"), ("16x16", "16x16", "96", "11A")),
                4: (("32x16", "32x16", None, "180"), ("16x16", "16x16", "32", "184"),
                    ("32x16", "32x16", "48", "106"), ("32x16", "32x16", "80", "10A"),
                    ("16x16", "16x16", "112", "10E")),
                5: (("32x16", "32x16", "16", "D0"), ("32x16", "32x16", "48", "D4"),
                    ("32x16", "32x16", "80", "D8")),
                6: (("32x16", "32x16", "24", "18C"), ("32x16", "32x16", "56", "190"),
                    ("16x16", "16x16", "88", "194")),
                7: (("32x16", "32x16", "16", "C0"), ("32x16", "32x16", "48", "C4"),
                    ("32x16", "32x16", "80", "C8")),
                8: (("32x16", "32x16", None, "C0"), ("32x16", "32x16", "32", "C4"),
                    ("32x16", "32x16", "64", "C8"), ("32x16", "32x16", "96", "CC")),
                9: (("32x16", "32x16", None, "18E"), ("32x16", "32x16", "32", "192"),
                    ("32x16", "32x16", "64", "196"), ("16x16", "16x16", "96", "19A")),
            },
            "ZhHans": {
                0: (("32x16", "32x16", None, "100"), ("32x16", "32x16", "32", "104"),
                    ("32x16", "32x16", "64", "108"), ("32x16", "32x16", "96", "10C")),
                1: (("32x16", "32x16", None, "C0"), ("16x16", "16x16", "32", "C4"),
                    ("32x16", "32x16", "48", "C6"), ("32x16", "32x16", "80", "CA"),
                    ("16x16", "16x16", "112", "CE")),
                2: (("32x16", "32x16", "16", "110"), ("32x16", "32x16", "48", "114"),
                    ("32x16", "32x16", "80", "118")),
                3: (("32x16", "32x16", "16", "180"), ("16x16", "16x16", "48", "184"),
                    ("8x16", "8x16", "64", "186"), ("16x16", "16x16", "72", "187"),
                    ("8x16", "8x16", "88", "189"), ("16x16", "16x16", "96", "18A")),
                4: (("32x16", "32x16", None, "54"), ("16x16", "16x16", "32", "58"),
                    ("16x16", "16x16", "48", "5A"), ("32x16", "32x16", "64", "94"),
                    ("32x16", "32x16", "96", "98")),
                5: (("32x16", "32x16", "16", "D0"), ("32x16", "32x16", "48", "D4"),
                    ("32x16", "32x16", "80", "D8")),
                6: (("32x16", "32x16", "24", "18C"), ("32x16", "32x16", "56", "190"),
                    ("16x16", "16x16", "88", "194")),
                7: (("32x16", "32x16", "16", "240"), ("32x16", "32x16", "48", "244"),
                    ("32x16", "32x16", "80", "248")),
                8: (("32x16", "32x16", None, "C0"), ("32x16", "32x16", "32", "C4"),
                    ("32x16", "32x16", "64", "C8"), ("32x16", "32x16", "96", "CC")),
                9: (("32x16", "32x16", None, "18E"), ("32x16", "32x16", "32", "192"),
                    ("32x16", "32x16", "64", "196"), ("16x16", "16x16", "96", "19A")),
            },
        }
        for locale, expected_descriptors in expected_layouts.items():
            for index, expected in expected_descriptors.items():
                match = re.search(
                    rf"static u16 CONST_DATA sSprite_SavemenuData{locale}_{index}\[\]\s*="
                    r"\s*\{(.*?)\n\};",
                    source,
                    re.DOTALL,
                )
                self.assertIsNotNone(match, (locale, index))
                count = re.search(r"^\s*(\d+),", match.group(1))
                self.assertIsNotNone(count, (locale, index))
                self.assertEqual(int(count.group(1)), len(expected), (locale, index))
                self.assertEqual(
                    tuple(
                        (shape, size, x or None, tile)
                        for shape, size, x, tile in re.findall(
                            r"OAM0_SHAPE_([0-9x]+), OAM1_SIZE_([0-9x]+)"
                            r"(?: \+ OAM1_X\((\d+)\))?, "
                            r"OAM2_CHR\(0x([0-9A-F]+)\) \+ OAM2_LAYER\(2\),",
                            match.group(1),
                        )
                    ),
                    expected,
                )
                self.assertNotIn("OAM0_END", match.group(1))
                self.assertTrue(match.group(1).rstrip().endswith(","))

            array = re.search(
                rf"static u16 \* CONST_DATA sSpriteArray_SavemenuData{locale}\[\]\s*="
                r"\s*\{(.*?)\n\};",
                source,
                re.DOTALL,
            )
            self.assertIsNotNone(array, locale)
            self.assertEqual(
                tuple(
                    re.findall(
                        rf"sSprite_SavemenuData{locale}_(\d+)",
                        array.group(1),
                    )
                ),
                ("0", "1", "2", "3", "4", "5", "6", "1", "8", "9", "7"),
            )

    def test_main_sprite_layout_registry_selects_each_cjk_locale(self):
        source = (ROOT / "src" / "savemenu_data.c").read_text(encoding="utf-8")
        header = (ROOT / "include" / "savemenu.h").read_text(encoding="utf-8")

        self.assertEqual(
            source.count("FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x06u"), 2
        )
        self.assertEqual(
            source.count("FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x02u"), 2
        )
        self.assertEqual(
            source.count("FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x04u"), 2
        )
        self.assertIn("FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x06u", header)
        self.assertEqual(source.count("ExpansionLocale_GetCurrent()"), 1)
        self.assertIn("ExpansionLocaleId locale;", source)
        self.assertIn("u16 * const * sprites;", source)
        self.assertEqual(
            tuple(
                re.findall(
                    r"\{\s*(EXPANSION_LOCALE_[A-Z_]+),\s*"
                    r"(sSpriteArray_SavemenuData[A-Za-z]+)\s*\}",
                    source,
                )
            ),
            (
                ("EXPANSION_LOCALE_JA", "sSpriteArray_SavemenuDataJa"),
                ("EXPANSION_LOCALE_ZH_HANS", "sSpriteArray_SavemenuDataZhHans"),
            ),
        )
        self.assertIn("return SpriteArray_SavemenuData_1[spriteIdx];", source)
        self.assertNotIn("LocalizedUiGraphics_GetSaveMenuMainSprites", source)
        self.assertNotIn(
            "ExpansionLocale_GetCurrent() == EXPANSION_LOCALE_JA",
            source,
        )

    def test_zh_hans_main_menu_runtime_oracle_records_the_fixed_layout(self):
        fingerprint = json.loads(
            (
                ROOT
                / "tools/gba-playtest/fingerprints"
                / "locale-cjk-softreset-persistence-modern-debug.json"
            ).read_text(encoding="utf-8")
        )
        expected = next(
            checkpoint
            for checkpoint in fingerprint["checkpoints"]
            if checkpoint["name"] == "post-reset-selector-skipped-zh-hans-restored"
        )

        self.assertEqual(
            expected["framebuffer_hash"], "fnv1a64-rgb24:33ebd93fb62f99e7"
        )
        self.assertNotEqual(
            expected["framebuffer_hash"], "fnv1a64-rgb24:0be48d3d7170ba97"
        )
        docs = (ROOT / "docs/localization.md").read_text(encoding="utf-8")
        self.assertIn("0be48d3d7170ba97", docs)
        self.assertIn("33ebd93fb62f99e7", docs)

    def test_required_graphics_sources_are_tracked_source_types(self):
        generated = (ROOT / "src/data/localized_ui_graphics.c").read_text(
            encoding="utf-8"
        )
        self.assertIn(".4bpp.lz", generated)
        self.assertNotIn(".png.lz", generated)
        for locale in ("ja", "zh-Hans"):
            for asset in self.manifest["variants"][locale]["assets"]:
                path = ROOT / asset["path"]
                if path.suffix == ".png":
                    ignored = subprocess.run(
                        ["git", "check-ignore", "-q", "--", path],
                        cwd=ROOT,
                        check=False,
                    )
                    self.assertNotEqual(ignored.returncode, 0, path)

    def test_title_sprite_and_chapter_tsa_destinations_match_each_surface(self):
        title = (ROOT / "src/titlescreen.c").read_text(encoding="utf-8")
        self.assertIn("(void *)0x06012800", title)
        self.assertIn("(void *)0x06013000", title)
        self.assertIn(
            "localizedTitle != 0 ? (void *)0x06012800 : (void *)0x06013000",
            title,
        )

        intro = (ROOT / "src/chapterintrofx.c").read_text(encoding="utf-8")
        self.assertIn(
            "DrawChapterTitleStrEx(TILEMAP_LOCATED(gBG0TilemapBuffer, 3, 9), 5, titleId);",
            intro,
        )
        chapter = (ROOT / "src/chapter_title.c").read_text(encoding="utf-8")
        self.assertIn(
            "CallARM_FillTileRect(gBG0TilemapBuffer, gGenericBuffer", chapter
        )

        save = (ROOT / "src/savemenu.c").read_text(encoding="utf-8")
        terminal = (ROOT / "src/sio_term.c").read_text(encoding="utf-8")
        self.assertIn("LocalizedUiGraphics_GetSaveMenuMainSprites", save)
        self.assertEqual(
            save.count("Decompress(SaveMenu_GetMainSpritesGfx(),"), 2
        )
        self.assertIn("return Img_SaveScreenSprits;", save)
        self.assertNotIn("Decompress(Img_SaveScreenSprits", save)
        savedraw = (ROOT / "src" / "savedraw.c").read_text(encoding="utf-8")
        savemenu_data = (ROOT / "src" / "savemenu_data.c").read_text(
            encoding="utf-8"
        )
        self.assertEqual(savedraw.count("GetSaveMenuMainOptionSprite("), 4)
        self.assertNotIn("SpriteArray_SavemenuData_1[", savedraw)
        self.assertIn("sSpriteArray_SavemenuDataJa", savemenu_data)
        self.assertIn("sSpriteArray_SavemenuDataZhHans", savemenu_data)
        self.assertIn("OAM2_CHR(0x186)", savemenu_data)
        self.assertIn("OAM2_CHR(0x187)", savemenu_data)
        self.assertIn(
            "{ EXPANSION_LOCALE_ZH_HANS, sSpriteArray_SavemenuDataZhHans }",
            savemenu_data,
        )
        self.assertIn("PutChapterTitleGfx(", save)
        self.assertIn("DrawChapterTitleStr(", terminal)
        self.assertNotIn("DrawChapterTitleStrEx(", save)
        self.assertNotIn("DrawChapterTitleStrEx(", terminal)


if __name__ == "__main__":
    unittest.main()
