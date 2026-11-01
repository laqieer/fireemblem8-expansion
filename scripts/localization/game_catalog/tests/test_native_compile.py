import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scripts.localization.game_catalog.build import generate


class NativeCompileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for tool in ("cc", "nm"):
            try:
                subprocess.run(
                    [tool, "--version"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                )
            except (OSError, subprocess.CalledProcessError):
                raise unittest.SkipTest(f"no host {tool} available")

    def _tmpdir(self):
        return tempfile.TemporaryDirectory(dir=TEST_DIR)

    def test_compiled_ascii_metrics_follow_native_glyph_layout_and_pointer_addends(self):
        prefix = os.environ.get("PREFIX", "arm-none-eabi-")
        toolchain = os.environ.get("MODERN_TOOLCHAIN_ROOT", "")
        compiler = os.environ.get(
            "MODERN_CC",
            str(Path(toolchain) / "bin" / f"{prefix}gcc") if toolchain else f"{prefix}gcc",
        )
        if shutil.which(compiler) is None:
            self.skipTest("selected ARM compiler is unavailable")
        with self._tmpdir() as tmp:
            root = Path(tmp)
            source = root / "metrics.c"
            source.write_text(
                '#include "global.h"\n#include "fontgrp.h"\n'
                '#include "localized_font.h"\n#include <stddef.h>\n'
                "const u32 gSelectedTextGlyphLayout[] = {\n"
                "    sizeof(struct Glyph *), sizeof(struct Glyph), offsetof(struct Glyph, width),\n"
                "    sizeof(((struct Glyph *)0)->width)\n"
                "};\n"
                "struct Glyph gSelectedGlyphs[2] = {\n"
                "    {0, 0, 3, {0}}, {0, 0, 11, {0}}\n"
                "};\n"
                "struct Glyph *const gSelectedGlyphTable[] = {\n"
                "    gSelectedGlyphs, gSelectedGlyphs + 1, 0\n"
                "};\n"
                "const u32 gSelectedTextLocalizedLayout[] = {\n"
                "    sizeof(u32), sizeof(u8), LOCALIZED_FONT_BITMAP_STRIDE\n"
                "};\n"
                "const u8 gSelectedCjkCodepoints[] = {0, 0x4E, 0, 0, 0x8C, 0x4E, 0, 0};\n"
                "const u8 gSelectedCjkWidths[] = {13, 7};\n"
                "const u8 gSelectedCjkBitmaps[2 * LOCALIZED_FONT_BITMAP_STRIDE] = {0};\n"
                "const u32 gSelectedCjkGlyphCount = 2;\n",
                encoding="ascii",
            )
            output = root / "metrics.o"
            driver = [compiler]
            if os.environ.get("MODERN_BINUTILS_DIR"):
                driver.append("-B" + os.environ["MODERN_BINUTILS_DIR"] + os.sep)
            newlib = os.environ.get(
                "MODERN_NEWLIB_INCLUDE",
                "/usr/include/newlib" if Path("/usr/include/newlib/stdlib.h").is_file() else "",
            )
            result = subprocess.run(
                [
                    *driver, "-mthumb", "-mcpu=arm7tdmi", "-DMODERN", "-DNONMATCHING",
                    "-DFE8_EXPANSION_ENABLED_LOCALE_MASK=3",
                    "-I", str(ROOT / "include"), "-I", str(ROOT),
                    *(["-isystem", newlib] if newlib else []),
                    "-fdata-sections", "-c", str(source), "-o", str(output),
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            from scripts.localization.game_catalog.compiled_fonts import (
                CompiledFontError,
                read_ascii_widths,
                read_localized_widths,
            )
            self.assertEqual(
                read_ascii_widths(output, "gSelectedGlyphTable", layout_object=output),
                {0: 3, 1: 11},
            )
            self.assertEqual(
                read_localized_widths(output, "gSelectedCjk", layout_object=output),
                {0x4E00: 13, 0x4E8C: 7},
            )
            truncated = root / "truncated.o"
            truncated.write_bytes(output.read_bytes()[:64])
            with self.assertRaises(CompiledFontError):
                read_ascii_widths(truncated, "gSelectedGlyphTable", layout_object=output)
            with self.assertRaises(CompiledFontError):
                read_localized_widths(output, "gMissingSelectedFont", layout_object=output)

    def test_source_only_declarations_compile_without_catalog_or_font_inputs(self):
        with self._tmpdir() as tmp:
            root = Path(tmp)
            message_header = root / "selected_msg.h"
            message_header.write_text("#define MSG_COUNT 0x0003\n", encoding="ascii")
            declaration = root / "game_localization_declarations.h"
            result = subprocess.run(
                [
                    sys.executable, "-B", "-m", "scripts.localization.game_catalog",
                    "declare", "--output", str(declaration),
                    "--target-header", str(message_header), "--enabled-locales", "ja",
                ],
                cwd=root,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            probe = root / "declaration.c"
            probe.write_text(
                '#include "game_localization_declarations.h"\n'
                "typedef char TargetCount[(GAME_LOCALIZATION_TARGET_COUNT == 3u) ? 1 : -1];\n"
                "typedef char EnabledJa[(GAME_LOCALIZATION_JA_ENABLED == 1u) ? 1 : -1];\n"
                "typedef char DisabledZh[(GAME_LOCALIZATION_ZH_HANS_ENABLED == 0u) ? 1 : -1];\n"
                "const struct GameLocalizationLocaleCatalog *Probe(void)\n"
                "{ return &gGameLocalizationCatalogJa; }\n",
                encoding="ascii",
            )
            compiled = subprocess.run(
                [
                    "cc", "-std=c89", "-pedantic-errors", "-Wall", "-Wextra", "-Werror",
                    "-I", str(TEST_DIR / "host_include"), "-I", str(root),
                    "-fsyntax-only", str(probe),
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            self.assertFalse((root / "game_localization_catalog.c").exists())
            self.assertFalse((root / "localized_game_text_data.h").exists())

    def test_generated_header_and_source_compile_as_strict_c89(self):
        with self._tmpdir() as tmp:
            out_dir = Path(tmp)
            written = generate(output_dir=out_dir)
            probe = out_dir / "probe.c"
            probe.write_text(
                "\n".join(
                    (
                        '#include "game_localization_catalog.h"',
                        "const struct GameLocalizationLocaleCatalog *Probe(void)",
                        "{",
                        "    return gGameLocalizationCatalogs[GAME_LOCALIZATION_LOCALE_JA];",
                        "}",
                        "",
                    )
                ),
                encoding="ascii",
            )
            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            for path in (written["source"], probe):
                result = subprocess.run(
                    [
                        "cc",
                        "-std=c89",
                        "-pedantic-errors",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        "-fcf-protection=none",
                        "-I",
                        str(TEST_DIR / "host_include"),
                        "-I",
                        str(out_dir),
                        "-fsyntax-only",
                        str(path),
                    ],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                    env=env,
                )
                self.assertEqual(result.returncode, 0, result.stdout)

    def _compile_symbols(self, out_dir: Path, enabled_locales):
        written = generate(output_dir=out_dir, enabled_locales=enabled_locales)
        object_path = out_dir / "catalog.o"
        result = subprocess.run(
            [
                "cc",
                "-std=c89",
                "-pedantic-errors",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-fcf-protection=none",
                "-I",
                str(TEST_DIR / "host_include"),
                "-I",
                str(out_dir),
                "-c",
                str(written["source"]),
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
        linked_path = out_dir / "catalog-linked.o"
        link_result = subprocess.run(
            ["cc", "-nostdlib", "-r", str(object_path), "-o", str(linked_path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(link_result.returncode, 0, link_result.stdout)
        nm_result = subprocess.run(
            ["nm", "-S", "--defined-only", str(linked_path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(nm_result.returncode, 0, nm_result.stdout)
        symbols = {}
        for line in nm_result.stdout.splitlines():
            fields = line.split()
            if len(fields) == 4:
                symbols[fields[3]] = int(fields[1], 16)
        budget = json.loads(written["budget_json"].read_text(encoding="utf-8"))
        return symbols, budget

    def test_profile_linked_objects_contain_only_enabled_symbols_and_payloads(self):
        with self._tmpdir() as ja_tmp, self._tmpdir() as zh_tmp:
            ja_symbols, ja_budget = self._compile_symbols(Path(ja_tmp), ("ja",))
            zh_symbols, zh_budget = self._compile_symbols(
                Path(zh_tmp), ("zh-Hans",)
            )

            self.assertIn("gGameLocalizationJaCompressedBlob", ja_symbols)
            self.assertIn("gGameLocalizationEnglishCompressedBlob", ja_symbols)
            self.assertNotIn("gGameLocalizationZhHansCompressedBlob", ja_symbols)
            self.assertEqual(
                ja_symbols["gGameLocalizationJaCompressedBlob"],
                ja_budget["locales"]["ja"]["compressed_bytes"],
            )
            self.assertEqual(
                ja_symbols["gGameLocalizationEnglishCompressedBlob"],
                ja_budget["shared_english"]["compressed_bytes"],
            )
            self.assertIn("gGameLocalizationZhHansCompressedBlob", zh_symbols)
            self.assertIn("gGameLocalizationEnglishCompressedBlob", zh_symbols)
            self.assertNotIn("gGameLocalizationJaCompressedBlob", zh_symbols)
            self.assertEqual(
                zh_symbols["gGameLocalizationZhHansCompressedBlob"],
                zh_budget["locales"]["zh-Hans"]["compressed_bytes"],
            )
            self.assertEqual(
                zh_symbols["gGameLocalizationEnglishCompressedBlob"],
                zh_budget["shared_english"]["compressed_bytes"],
            )
            self.assertEqual(
                ja_symbols["gGameLocalizationEnglishCompressedBlob"],
                zh_symbols["gGameLocalizationEnglishCompressedBlob"],
            )


if __name__ == "__main__":
    unittest.main()
