import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from struct import unpack
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
        expected_difficulty_menu = {
            "ja": (
                "0x08AA65A8",
                "2d5af0d84088b41ccee69c4262ad81b83e0b6184872adbce4e281ccea8c88069",
            ),
            "zh-Hans": (
                "0x08AA39DC",
                "b562014ef0376d6abb509438e728a310f6f6671a5d34d27ae61daa4ac4e8ae5e",
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
            difficulty_menu = variant["assets"][variant["difficulty_menu"]]
            self.assertEqual(difficulty_menu["name"], "menu/difficulty_mode")
            self.assertEqual(
                difficulty_menu["source_address"], expected_difficulty_menu[locale][0]
            )
            self.assertEqual(
                difficulty_menu["raw_sha256"], expected_difficulty_menu[locale][1]
            )
            self.assertEqual(difficulty_menu["raw_size"], 0x1800)
            self.assertEqual(
                difficulty_menu["dimensions"],
                {"height": 48, "tiles_per_row": 32, "width": 256},
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

    def test_cjk_consumers_link_to_the_locale_registry(self):
        cc = shutil.which("cc")
        nm = shutil.which("nm")
        if cc is None or nm is None:
            self.skipTest("no host compiler or symbol reader available")

        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        consumers = {
            "titlescreen.c": (
                "LocalizedUiGraphics_GetTitle",
                "LocalizedUiGraphics_GetTitleSprites",
            ),
            "savemenu.c": (
                "LocalizedUiGraphics_GetSaveMenuMainSprites",
                "LocalizedUiGraphics_GetSaveMenuOptions",
            ),
            "difficultymenu.c": ("LocalizedUiGraphics_GetDifficultyMenuObjects",),
            "opsubtitle.c": ("LocalizedUiGraphics_GetSubtitleSlides",),
            "chapter_title.c": (
                "LocalizedUiGraphics_GetChapterTitle",
                "LocalizedUiGraphics_GetChapterTitleFrame",
                "LocalizedUiGraphics_GetChapterTitleTsa",
            ),
        }
        with tempfile.TemporaryDirectory(
            prefix="localized-ui-consumers-",
            dir=build_root,
        ) as temporary:
            build_dir = Path(temporary)
            for source, selectors in consumers.items():
                object_path = build_dir / f"{Path(source).stem}.o"
                result = subprocess.run(
                    [
                        cc,
                        "-std=gnu89",
                        "-w",
                        "-DMODERN=1",
                        "-DNONMATCHING=1",
                        "-DFE8_EXPANSION_ENABLED_LOCALE_MASK=0x07u",
                        "-I",
                        str(ROOT / "include"),
                        "-c",
                        str(ROOT / "src" / source),
                        "-o",
                        str(object_path),
                    ],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                symbols = subprocess.run(
                    [nm, "-u", str(object_path)],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(symbols.returncode, 0, symbols.stdout)
                for selector in selectors:
                    self.assertIn(selector, symbols.stdout, (source, selector))

    def test_difficulty_menu_tiles_palette_and_oam_stay_within_their_surfaces(self):
        tsa = (ROOT / "graphics/misc/Tsa_DifficultyMenuObjs.tsa.bin").read_bytes()
        width, height = tsa[0] + 1, tsa[1] + 1
        entries = unpack(f"<{(len(tsa) - 2) // 2}H", tsa[2:])
        tile_ids = tuple(entry & 0x03FF for entry in entries)
        palette_ids = tuple((entry >> 12) & 0xF for entry in entries)

        self.assertEqual((width, height, len(entries)), (13, 12, 156))
        self.assertEqual((min(tile_ids), max(tile_ids)), (0x1, 0x45))
        self.assertEqual((min(palette_ids), max(palette_ids)), (0, 0))

        for locale in ("ja", "zh-Hans"):
            variant = self.manifest["variants"][locale]
            asset = variant["assets"][variant["difficulty_menu"]]
            tile_count = asset["raw_size"] // 32

            self.assertEqual(tile_count, 192)
            self.assertEqual(
                tile_count,
                (asset["dimensions"]["width"] // 8)
                * (asset["dimensions"]["height"] // 8),
            )
            self.assertLessEqual(0x100 + max(tile_ids), 0x100 + tile_count - 1)
            self.assertEqual(0x06010800 + asset["raw_size"], 0x06012000)

        difficulty = (ROOT / "src/difficultymenu.c").read_text(encoding="utf-8")
        self.assertIn("ApplyPalettes(Pal_DifficultyMenuObjs, 17, 10);", difficulty)
        self.assertLessEqual(17 + 10, 32)
        self.assertIn(
            "CallARM_FillTileRect(gBG1TilemapBuffer + 0xd1, gGenericBuffer, 0x1000);",
            difficulty,
        )
        self.assertIn("OAM2_PAL(5 + (i * 2))", difficulty)
        self.assertIn("OAM2_PAL(6 + (i * 2))", difficulty)
        self.assertLessEqual(6 + (2 * 2), 15)

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
    def test_required_graphics_sources_are_tracked_source_types(self):
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

if __name__ == "__main__":
    unittest.main()
