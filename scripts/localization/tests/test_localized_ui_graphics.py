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

    def test_japanese_main_sprite_oam_layout_matches_regional_sheet(self):
        source = (ROOT / "src" / "savemenu_data.c").read_text(encoding="utf-8")
        expected_chr = {
            0: ("100", "104", "108", "10C"),
            1: ("186", "18A", "106", "10A", "10E"),
            2: ("110", "114", "118"),
            3: ("110", "114", "116", "15D", "15F", "11A"),
            4: ("180", "184", "106", "10A", "10E"),
            5: ("D0", "D4", "D8"),
            6: ("18C", "190", "194"),
            7: ("C0", "C4", "C8"),
            8: ("C0", "C4", "C8", "CC"),
            9: ("18E", "192", "196", "19A"),
        }
        for index, expected in expected_chr.items():
            match = re.search(
                rf"static u16 CONST_DATA sSprite_SavemenuDataJa_{index}\[\]\s*="
                r"\s*\{(.*?)\n\};",
                source,
                re.DOTALL,
            )
            self.assertIsNotNone(match, index)
            self.assertEqual(
                tuple(re.findall(r"OAM2_CHR\(0x([0-9A-F]+)\)", match.group(1))),
                expected,
            )

        array = re.search(
            r"static u16 \* CONST_DATA sSpriteArray_SavemenuDataJa\[\]\s*="
            r"\s*\{(.*?)\n\};",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(array)
        self.assertEqual(
            tuple(
                re.findall(
                    r"sSprite_SavemenuDataJa_(\d+)",
                    array.group(1),
                )
            ),
            ("0", "1", "2", "3", "4", "5", "6", "1", "8", "9", "7"),
        )

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
        self.assertIn("OAM2_CHR(0x186)", savemenu_data)
        self.assertIn("OAM2_CHR(0x15D)", savemenu_data)
        self.assertIn(
            "ExpansionLocale_GetCurrent() == EXPANSION_LOCALE_JA",
            savemenu_data,
        )
        self.assertIn("PutChapterTitleGfx(", save)
        self.assertIn("DrawChapterTitleStr(", terminal)
        self.assertNotIn("DrawChapterTitleStrEx(", save)
        self.assertNotIn("DrawChapterTitleStrEx(", terminal)


if __name__ == "__main__":
    unittest.main()
