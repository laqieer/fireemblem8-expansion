import shutil
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_catalog.build import build_game_catalog, write_build
from scripts.localization.game_catalog.model import GameCatalogError
from scripts.localization.game_catalog.compiled_fonts import SelectedFontMetrics
from scripts.generated_data.schema import SchemaInputs
from scripts.localization.game_locales.width_contract import (
    DEFAULT_WIDTH_REGISTRY_PATH,
    load_width_registry,
)
from scripts.localization.game_locales.fixed_width_labels import (
    FixedWidthLabelError,
    build_fixed_width_label_metrics,
)


class FixtureBuildTests(unittest.TestCase):
    def test_extended_catalog_retains_real_narrow_name_aliases(self):
        with tempfile.TemporaryDirectory(prefix="selected-name-labels-", dir=ROOT / "build") as directory:
            work = Path(directory)
            source = work / "texts.txt"
            source.write_text(
                f'#include "{ROOT / "texts/texts.txt"}"\n'
                "## MSG_SELECTED_EXTRA\nExtra[.][X]\n",
                encoding="utf-8",
            )
            header = work / "msg.h"
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "scripts/texttools/textprocess.py"),
                 str(source), str(ROOT / "texts/textdefs.txt"),
                 str(work / "msg_data.c"), str(header), "utf8"],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            mapping = json.loads((ROOT / "texts/locales/mapping/fe8u_target_map.json").read_bytes())
            mapping["rows"].append({
                "target_id": "0x0D56", "state": "verified",
                "source": {"kind": "english_fallback", "reason": "selected-extension-fixture"},
                "verification": {
                    "method": "authored-fixture", "evidence": "original selected extension",
                    "evidence_kind": "fixture", "source_table": "selected message fixture",
                    "source_symbol": "MSG_SELECTED_EXTRA", "source_key": "0x0D56",
                    "subsystem": "fixture", "rationale": "one actual extra message",
                    "confidence": "explicit",
                },
            })
            mapping_path = work / "mapping.json"
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            build = build_game_catalog(
                english_texts_path=source, target_header_path=header,
                mapping_path=mapping_path, enabled_locales=("ja",),
            )
            self.assertEqual(build.target_count, 3415)
            expected = json.loads((ROOT / "texts/locales/fixed_width_display_aliases.json").read_bytes())
            self.assertEqual(
                build.display_aliases.get("ja", {}).get("item_name_56", {}),
                {int(key, 16): value for key, value in expected["aliases"]["ja"]["item_name_56"].items()},
            )

    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        self.fixture_dir = Path(__file__).resolve().parent / "fixtures"
        if self.fixture_dir.exists():
            shutil.rmtree(self.fixture_dir)
        self.fixture_dir.mkdir()

        (self.fixture_dir / "textdefs.txt").write_text(
            "[X] = 0\n"
            "[LF] = 1\n"
            "[DashedLine] = 0x7F\n"
            "[TAB] = 0x81, 0x40\n"
            "[LQuote] = 0x93\n"
            "[RQuote] = 0x94\n"
            "[AccentedE] = 0xE9\n",
            encoding="utf-8",
        )
        (self.fixture_dir / "texts.txt").write_text(
            "#0x0\n"
            "English zero[X]\n"
            "## MSG_FIXTURE_ONE\n"
            "[LQuote]English[DashedLine]one[RQuote][X]\n"
            "## MSG_FIXTURE_TWO\n"
            "English[TAB]two[AccentedE][X]\n",
            encoding="utf-8",
        )
        (self.fixture_dir / "ja_indexed.txt").write_text(
            "\n".join(
                (
                    "# fixture ja indexed",
                    "#0x0000",
                    "日[CTRL:0080][CTRL:0020]",
                    "#0x0001",
                    "著者",
                    "#0x0002",
                    "英語fallback対象",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        (self.fixture_dir / "zh_indexed.txt").write_text(
            "\n".join(
                (
                    "# fixture zh indexed",
                    "#0x0000",
                    "中[CTRL:0080][CTRL:0020]",
                    "#0x0001",
                    "作者",
                    "#0x0002",
                    "英语fallback目标",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        (self.fixture_dir / "zh_raw.json").write_text(
            "{\n"
            '  "schema_version": 2,\n'
            '  "locale_id": "zh-Hans",\n'
            '  "source_layout": "FE8CN-raw-address",\n'
            '  "record_count": 1,\n'
            '  "unique_import_count": 1,\n'
            '  "unique_address_count": 1,\n'
            '  "records": [\n'
            '    {\n'
            '      "import_id": "fe8cn.raw.import-0000",\n'
            '      "text": "原始",\n'
            '      "provenance": {"address": "0x00000000", "occurrences": []}\n'
            '    }\n'
            '  ]\n'
            '}\n',
            encoding="utf-8",
        )
        ja_providers = {
            "0x0001": {"symbol": "fixture", "text": "生"},
        }
        ja_blob = "生".encode("cp932") + b"\0"
        (self.fixture_dir / "ja_raw_values.cp932").write_bytes(ja_blob)
        ja_origin = 'const char fixture[] = "生";\n'.encode("utf-8")
        (self.fixture_dir / "ja_origin.c").write_bytes(ja_origin)
        ja_origin_oid = hashlib.sha1(
            f"blob {len(ja_origin)}\0".encode("ascii") + ja_origin
        ).hexdigest()
        ja_src_tree = (
            b"100644 fixture.c\0" + bytes.fromhex(ja_origin_oid)
        )
        ja_src_tree_oid = hashlib.sha1(
            f"tree {len(ja_src_tree)}\0".encode("ascii") + ja_src_tree
        ).hexdigest()
        ja_root_tree = b"40000 src\0" + bytes.fromhex(ja_src_tree_oid)
        ja_root_tree_oid = hashlib.sha1(
            f"tree {len(ja_root_tree)}\0".encode("ascii") + ja_root_tree
        ).hexdigest()
        ja_commit = (
            "tree " + ja_root_tree_oid + "\n"
            "author Fixture <fixture@example.com> 0 +0000\n"
            "committer Fixture <fixture@example.com> 0 +0000\n"
            "\n"
            "fixture source\n"
        ).encode("utf-8")
        ja_revision = hashlib.sha1(
            f"commit {len(ja_commit)}\0".encode("ascii") + ja_commit
        ).hexdigest()
        (self.fixture_dir / "ja_source_commit.txt").write_bytes(ja_commit)
        (self.fixture_dir / "ja_root.tree").write_bytes(ja_root_tree)
        (self.fixture_dir / "ja_src.tree").write_bytes(ja_src_tree)
        ja_snapshot = {
            "kind": "fe8j-raw-symbol-source-snapshot",
            "provider_count": 1,
            "providers": {
                "0x0001": {
                    "byte_length": len(ja_blob),
                    "offset": 0,
                    "source_anchor": "fixture",
                    "source_path": "src/fixture.c",
                    "source_value_index": 0,
                    "symbol": "fixture",
                    "value_sha256": hashlib.sha256(ja_blob).hexdigest(),
                }
            },
            "provider_values_artifact": {
                "encoding": "cp932-nul-terminated",
                "generated_from_paths": ["src/fixture.c"],
                "path": "ja_raw_values.cp932",
                "sha256": hashlib.sha256(ja_blob).hexdigest(),
            },
            "schema_version": 6,
            "source_blobs": [
                {
                    "oid": ja_origin_oid,
                    "path": "src/fixture.c",
                    "sha256": hashlib.sha256(ja_origin).hexdigest(),
                    "vendored_path": "ja_origin.c",
                }
            ],
            "source_commit": {
                "path": "ja_source_commit.txt",
                "sha256": hashlib.sha256(ja_commit).hexdigest(),
            },
            "source_trees": [
                {
                    "oid": ja_root_tree_oid,
                    "path": "",
                    "sha256": hashlib.sha256(ja_root_tree).hexdigest(),
                    "vendored_path": "ja_root.tree",
                },
                {
                    "oid": ja_src_tree_oid,
                    "path": "src",
                    "sha256": hashlib.sha256(ja_src_tree).hexdigest(),
                    "vendored_path": "ja_src.tree",
                },
            ],
            "source_repository": "https://github.com/example/fixture",
            "source_revision": ja_revision,
            "source_url": "https://github.com/example/fixture/tree/" + ja_revision,
        }
        ja_snapshot_bytes = (
            json.dumps(ja_snapshot, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        (self.fixture_dir / "ja_raw_source.json").write_bytes(ja_snapshot_bytes)
        (self.fixture_dir / "ja_raw.json").write_text(
            json.dumps(
                {
                    "kind": "fe8j-raw-provider-catalog",
                    "locale_id": "ja",
                    "provider_count": 1,
                    "providers": ja_providers,
                    "schema_version": 6,
                    "source_layout": "FE8J-raw-symbol",
                    "source_revision": ja_revision,
                    "source_snapshot": {
                        "path": "ja_raw_source.json",
                        "sha256": hashlib.sha256(ja_snapshot_bytes).hexdigest(),
                    },
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.fixture_dir / "mapping.json").write_text(
            "{\n"
            '  "schema_version": 2,\n'
            '  "kind": "fe8u-locale-mapping",\n'
            '  "authority": "verified",\n'
            '  "authoritative": true,\n'
            '  "locale_ids": ["ja", "zh-Hans"],\n'
            '  "note": "fixture",\n'
            '  "rows": [\n'
            '    {\n'
            '      "target_id": "0x0000",\n'
            '      "state": "verified",\n'
            '      "source": {"kind": "indexed", "layout": "FE8J", "id": "0x0000"},\n'
            '      "verification": {\n'
            '        "method": "fixture",\n'
            '        "evidence": "fixture",\n'
            '        "evidence_kind": "fixture",\n'
            '        "source_table": "fixture",\n'
            '        "source_symbol": "fixture",\n'
            '        "source_key": "fixture",\n'
            '        "subsystem": "fixture",\n'
            '        "rationale": "fixture",\n'
            '        "confidence": "high"\n'
            '      }\n'
            '    },\n'
            '    {\n'
            '      "target_id": "0x0001",\n'
            '      "state": "verified",\n'
            '      "source": {\n'
            '        "kind": "raw",\n'
            '        "import_id": "fe8cn.raw.import-0000",\n'
            '        "regional_sources": {\n'
            '          "ja": {"kind": "symbol", "symbol": "fixture"},\n'
            '          "zh-Hans": {"kind": "import", "import_id": "fe8cn.raw.import-0000"}\n'
            '        }\n'
            '      },\n'
            '      "verification": {\n'
            '        "method": "fixture",\n'
            '        "evidence": "fixture",\n'
            '        "evidence_kind": "fixture",\n'
            '        "source_table": "fixture",\n'
            '        "source_symbol": "fixture",\n'
            '        "source_key": "fixture",\n'
            '        "subsystem": "fixture",\n'
            '        "rationale": "fixture",\n'
            '        "confidence": "high"\n'
            '      }\n'
            '    },\n'
            '    {\n'
            '      "target_id": "0x0002",\n'
            '      "state": "verified",\n'
            '      "source": {"kind": "english_fallback", "reason": "not-yet-verified"},\n'
            '      "verification": {\n'
            '        "method": "fixture",\n'
            '        "evidence": "fixture",\n'
            '        "evidence_kind": "fixture",\n'
            '        "source_table": "fixture",\n'
            '        "source_symbol": "fixture",\n'
            '        "source_key": "fixture",\n'
            '        "subsystem": "fixture",\n'
            '        "rationale": "fixture",\n'
            '        "confidence": "explicit"\n'
            '      }\n'
            '    }\n'
            '  ]\n'
            '}\n',
            encoding="utf-8",
        )
        (self.fixture_dir / "msg.h").write_text(
            "#ifndef MSG_H\n#define MSG_H\n"
            "#define MSG_000 0x0000\n"
            "#define MSG_FIXTURE_ONE 0x0001\n"
            "#define MSG_FIXTURE_TWO 0x0002\n"
            "#define MSG_COUNT 0x0003\n#endif\n",
            encoding="utf-8",
        )

    def tearDown(self):
        if self.fixture_dir.exists():
            shutil.rmtree(self.fixture_dir)

    def selected_inputs(self):
        return {
            "english_texts_path": self.fixture_dir / "texts.txt",
            "english_definitions_path": self.fixture_dir / "textdefs.txt",
            "ja_indexed_path": self.fixture_dir / "ja_indexed.txt",
            "ja_raw_path": self.fixture_dir / "ja_raw.json",
            "zh_indexed_path": self.fixture_dir / "zh_indexed.txt",
            "zh_raw_path": self.fixture_dir / "zh_raw.json",
            "mapping_path": self.fixture_dir / "mapping.json",
            "target_header_path": self.fixture_dir / "msg.h",
            "ja_raw_expected_repository": None,
            "ja_raw_expected_revision": None,
        }

    def test_selected_font_metrics_reach_final_catalog_width_validation(self):
        inputs = self.selected_inputs()
        inputs.update(event_root_path=self.fixture_dir, enabled_locales=("ja",))
        metrics: SelectedFontMetrics = {
            "ascii": {
                "system": {scalar: 20 for scalar in range(0x20, 0x7F)},
                "talk": {scalar: 1 for scalar in range(0x20, 0x7F)},
            },
            "localized": {
                "ja": {
                    style: {ord("日"): 13, ord("生"): 7}
                    for style in ("system", "talk")
                },
            },
            "inputs": {
                "compiler_context": "fixture-only-not-native-evidence",
                "source_root": str(ROOT),
                "renderer_object": "fixture-renderer",
                "font_objects": ("fixture-font", "fixture-localized-font"),
                "locale_mask": 3,
            },
        }
        build = build_game_catalog(**inputs, font_metrics=metrics)
        self.assertEqual(build.report["width_validation"]["ja"]["max_line_width"], 236)
        self.assertIsNone(build.locales[0].entries[2].encoded_bytes)
        metrics["ascii"]["system"] = {scalar: 24 for scalar in range(0x20, 0x7F)}
        with self.assertRaisesRegex(GameCatalogError, "280px"):
            build_game_catalog(**inputs, font_metrics=metrics)

    def selected_native_inputs(self, *, locale="ja"):
        compiler = os.environ.get("MODERN_CC", "arm-none-eabi-gcc")
        if shutil.which(compiler) is None:
            self.skipTest("selected ARM compiler is unavailable")
        work = self.fixture_dir / "native"
        work.mkdir()
        mask, stem, codepoint = {"ja": (3, "Ja", 0x4E00), "fr": (9, "Eu", 0xE9)}[locale]
        flags = [
            "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-mabi=aapcs",
            "-std=gnu89", "-ffreestanding", "-fno-common", "-ffunction-sections",
            "-fdata-sections", "-DMODERN=1", "-DNONMATCHING=1",
            f"-DFE8_EXPANSION_ENABLED_LOCALE_MASK={mask}u",
            "-DFE8_EXPANSION_DEFAULT_LOCALE_ID=0", "-DFE8_EXPANSION_STARTER_CONTENT=0",
            "-DSELECTED_CJK_WIDTH=13",
            "-I" + str(ROOT / "include"), "-I" + str(ROOT),
        ]
        renderer = work / "renderer.c"
        renderer.write_text(
            '#include "global.h"\n#include "fontgrp.h"\n#include "localized_font.h"\n',
            encoding="ascii",
        )
        font = work / "font.c"
        font.write_text(
            '#include "global.h"\n#include "fontgrp.h"\n'
            'static struct Glyph sFixtureGlyph = {0, 0, 6, {0}};\n'
            'struct Glyph *TextGlyphs_System[128] = {[0 ... 127] = &sFixtureGlyph};\n'
            'struct Glyph *TextGlyphs_Talk[128] = {[0 ... 127] = &sFixtureGlyph};\n',
            encoding="ascii",
        )
        localized = work / "localized.c"
        localized.write_text(
            '#include "global.h"\n#include "localized_font.h"\n'
            + "".join(
                f"const u8 gLocalizedFont{stem}{style}Codepoints[] = "
                f"{{{codepoint & 0xFF}, {codepoint >> 8}, 0, 0}};\n"
                f"const u8 gLocalizedFont{stem}{style}Widths[] = {{SELECTED_CJK_WIDTH}};\n"
                f"const u8 gLocalizedFont{stem}{style}Bitmaps[LOCALIZED_FONT_BITMAP_STRIDE] = {{0}};\n"
                f"const u32 gLocalizedFont{stem}{style}GlyphCount = 1;\n"
                for style in ("System", "Talk")
            ),
            encoding="ascii",
        )
        retained = work / "retained.c"
        retained.write_text(
            '#include "global.h"\n#include "bmitem.h"\n'
            '#if SELECTED_REFERENCE_BAD\n'
            '#define RETAINED_MESSAGE 3u\n'
            '#else\n'
            '#define RETAINED_MESSAGE 1u\n'
            '#endif\n'
            '#if SELECTED_REFERENCE_FIELD\n'
            'const struct ItemData RetainedItem = {\n'
            '#if SELECTED_REFERENCE_FIELD == 1\n'
            '    .nameTextId = RETAINED_MESSAGE,\n'
            '#elif SELECTED_REFERENCE_FIELD == 2\n'
            '    .descTextId = RETAINED_MESSAGE,\n'
            '#else\n'
            '    .useDescTextId = RETAINED_MESSAGE,\n'
            '#endif\n'
            '};\n'
            '#else\n'
            'void RetainedReference(void) { GetStringFromIndex(RETAINED_MESSAGE); }\n'
            '#endif\n',
            encoding="ascii",
        )
        units = []
        for logical, source in (
            ("src/fontgrp.c", renderer),
            ("src/fontgrp-data.c", font),
            ("src/data/localized_font_data.c", localized),
            ("src/retained.c", retained),
        ):
            target = work / Path(logical).with_suffix(".o").name
            command = [compiler, *flags, "-DSELECTED_REFERENCE_BAD=0", "-c", str(source),
                       "-o", str(target)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            units.append({
                "schema_version": 1, "kind": "fe8-selected-text-compile",
                "logical_source": logical, "selected_source": str(source),
                "compiler_input": str(source), "object": str(target),
                "compiler_argv": command, "preprocessor_argv": [],
            })
        inputs = self.selected_inputs()
        inputs["english_texts_path"].write_text(
            "## MSG_000\nA[X]\n## MSG_FIXTURE_ONE\nA[X]\n## MSG_FIXTURE_TWO\nA[X]\n",
            encoding="ascii",
        )
        mapping = json.loads(inputs["mapping_path"].read_bytes())
        for row in mapping["rows"]:
            row["source"] = {"kind": "english_fallback", "reason": "module-native CPP fixture"}
            row["verification"]["confidence"] = "explicit"
        inputs["mapping_path"].write_text(json.dumps(mapping), encoding="utf-8")
        context = work / "context.json"
        context.write_text(json.dumps({
            "schema_version": 1, "kind": "fe8-selected-text-inputs",
            "source_root": str(ROOT), "linker": "arm-none-eabi-ld", "units": units,
        }), encoding="utf-8")
        return inputs, units, context

    def test_selected_generation_rejects_live_native_ids_before_publication(self):
        inputs, units, context = self.selected_native_inputs(locale="fr")
        work = context.parent
        eu_mapping = work / "eu-mapping.json"
        eu_mapping.write_text(json.dumps({
            "kind": "fe8eu-to-fe8u-target-map", "format": 1, "authoritative": True,
            "target_count": 3, "locale_ids": ["fr", "de", "es", "it"],
            "source_message_count": 1,
            "rows": [
                {"target_id": f"0x{target:04X}", "source": {
                    "kind": "english_preserve", "reason": "scoped native publication fixture",
                }}
                for target in range(3)
            ],
        }), encoding="utf-8")
        eu_indexed = work / "fr.txt"
        eu_indexed.write_text("#0x0000\nA\n", encoding="ascii")
        authored_arguments = []
        for locale in ("fr", "de", "es", "it"):
            authored = work / f"{locale}.authored.json"
            authored.write_text(json.dumps({
                "kind": "fe8eu-target-authored-catalog", "locale": locale, "strings": {},
            }), encoding="utf-8")
            authored_arguments.extend(("--eu-authored", f"{locale}={authored}"))
        generated = work / "published"
        from scripts.localization.game_catalog.constants import OUTPUT_SOURCE_NAME
        catalog_source = generated / OUTPUT_SOURCE_NAME
        catalog_object = work / "catalog.o"
        catalog_command = list(units[0]["compiler_argv"])
        catalog_command[catalog_command.index("-c") + 1] = str(catalog_source)
        catalog_command[-1] = str(catalog_object)
        catalog_command[1:1] = ["-I" + str(generated)]
        units.append({
            "logical_source": "src/localized_game_text-catalog.c",
            "selected_source": str(catalog_source), "compiler_input": str(catalog_source),
            "object": str(catalog_object), "compiler_argv": catalog_command,
            "preprocessor_argv": [],
        })
        context.write_text(json.dumps({
            "schema_version": 1, "kind": "fe8-selected-text-inputs",
            "source_root": str(ROOT), "linker": "arm-none-eabi-ld", "units": units,
        }), encoding="utf-8")
        program = [sys.executable, "-B", "-m", "scripts.localization.game_catalog"]
        options = [
            "--english-texts", str(inputs["english_texts_path"]),
            "--english-definitions", str(inputs["english_definitions_path"]),
            "--target-header", str(inputs["target_header_path"]),
            "--mapping", str(inputs["mapping_path"]), "--eu-mapping", str(eu_mapping),
            "--eu-indexed", f"fr={eu_indexed}", *authored_arguments,
            "--event-root", str(self.fixture_dir), "--enabled-locales", "fr",
            "--compile-context", str(context),
        ]
        arguments = [*program, "generate", *options, "--out-dir", str(generated)]
        result = subprocess.run(
            arguments, cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        published = {
            path.relative_to(generated): path.read_bytes()
            for path in generated.rglob("*") if path.is_file()
        }
        self.assertTrue(published)
        catalog_source.write_text(
            '#include "global.h"\n'
            "void StaleCatalog(void) { GetStringFromIndex(3); }\n",
            encoding="ascii",
        )
        result = subprocess.run(
            arguments, cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(catalog_source.read_bytes(), published[Path(OUTPUT_SOURCE_NAME)])
        texts = inputs["english_texts_path"]
        texts.write_text(texts.read_text().replace("A[X]", "B[X]", 1), encoding="ascii")
        units[-2]["compiler_argv"] = [
            argument if not argument.startswith("-DSELECTED_REFERENCE_BAD=")
            else "-DSELECTED_REFERENCE_BAD=1" for argument in units[-2]["compiler_argv"]
        ]
        context.write_text(json.dumps({
            "schema_version": 1, "kind": "fe8-selected-text-inputs",
            "source_root": str(ROOT), "linker": "arm-none-eabi-ld", "units": units,
        }), encoding="utf-8")
        for command in (
            "generate", "check", "budget", "validate",
            "audit-leakage", "check-leakage", "check-width",
        ):
            with self.subTest(command=command):
                outputs = (
                    ["--out-dir", str(generated)]
                    if command in ("generate", "check", "budget") else
                    ["--report", str(generated / "audit.json")]
                    if command in ("audit-leakage", "check-leakage") else []
                )
                result = subprocess.run(
                    [*program, command, *options, *outputs],
                    cwd=ROOT, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("retained.c", result.stderr)
                self.assertEqual(published, {
                    path.relative_to(generated): path.read_bytes()
                    for path in generated.rglob("*") if path.is_file()
                })
        units[-2]["compiler_argv"] = [
            argument if not argument.startswith("-DSELECTED_REFERENCE_BAD=")
            else "-DSELECTED_REFERENCE_BAD=0" for argument in units[-2]["compiler_argv"]
        ]
        context.write_text(json.dumps({
            "schema_version": 1, "kind": "fe8-selected-text-inputs",
            "source_root": str(ROOT), "linker": "arm-none-eabi-ld", "units": units,
        }), encoding="utf-8")
        catalog_source.write_text(
            '#include "global.h"\n'
            "void InvalidProspectiveCatalog(void) { GetStringFromIndex(3); }\n",
            encoding="ascii",
        )
        from scripts.localization.game_catalog.compiled_inputs import (
            CompiledInputError, validate_compiled_references,
        )
        with self.assertRaisesRegex(CompiledInputError, "outside the active"):
            validate_compiled_references(
                context, target_count=3, generated_catalog_dir=generated,
            )

    def test_selected_context_checks_live_retained_cpp_references(self):
        inputs, units, context = self.selected_native_inputs()
        work = context.parent
        mapping = json.loads(inputs["mapping_path"].read_bytes())
        program = [sys.executable, "-B", "-m", "scripts.localization.game_catalog"]
        options = [
            "--english-texts", str(inputs["english_texts_path"]),
            "--english-definitions", str(inputs["english_definitions_path"]),
            "--target-header", str(inputs["target_header_path"]),
            "--mapping", str(inputs["mapping_path"]),
            "--ja-indexed", str(inputs["ja_indexed_path"]),
            "--event-root", str(self.fixture_dir), "--enabled-locales", "ja",
            "--compile-context", str(context),
        ]
        arguments = [*program, "check-width", *options]
        original_command = units[-1]["compiler_argv"]
        for field in (0, 1, 2, 3):
            for bad in (0, 1):
                with self.subTest(field=field, live_invalid_reference=bad):
                    units[-1]["compiler_argv"] = [
                        original_command[0], f"-DSELECTED_REFERENCE_FIELD={field}",
                        *(
                            argument if not argument.startswith("-DSELECTED_REFERENCE_BAD=")
                            else f"-DSELECTED_REFERENCE_BAD={bad}"
                            for argument in original_command[1:]
                        ),
                    ]
                    context.write_text(json.dumps({
                        "schema_version": 1, "kind": "fe8-selected-text-inputs",
                        "source_root": str(ROOT), "linker": "arm-none-eabi-ld", "units": units,
                    }), encoding="utf-8")
                    result = subprocess.run(
                        arguments, cwd=ROOT, capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(result.returncode, bad, result.stdout + result.stderr)
                    if bad:
                        self.assertIn("retained.c", result.stderr)
                        self.assertIn("active", result.stderr)
        units[-1]["compiler_argv"] = original_command
        context.write_text(json.dumps({
            "schema_version": 1, "kind": "fe8-selected-text-inputs",
            "source_root": str(ROOT), "linker": "arm-none-eabi-ld", "units": units,
        }), encoding="utf-8")
        mapping["rows"][1]["source"] = {"kind": "authored", "translation_key": "native-width"}
        mapping["rows"][1]["verification"]["confidence"] = "high"
        inputs["mapping_path"].write_text(json.dumps(mapping), encoding="utf-8")
        authored = work / "authored.ja.json"
        for glyphs, inserted in ((18, 0), (19, 1)):
            with self.subTest(native_glyphs=glyphs):
                authored.write_text(json.dumps({
                    "kind": "fe8u-game-authored-catalog", "locale": "ja",
                    "strings": {"native-width": "一" * glyphs},
                }, ensure_ascii=False), encoding="utf-8")
                result = subprocess.run(
                    [*arguments, "--authored", f"ja={authored}"],
                    cwd=ROOT, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"inserted={inserted}", result.stdout)

    def test_native_reference_constants_use_complete_operands(self):
        from scripts.localization.game_catalog.compiled_inputs import (
            CompiledInputError, validate_compiled_references,
        )

        compiler = os.environ.get("MODERN_CC", "arm-none-eabi-gcc")
        if shutil.which(compiler) is None:
            self.skipTest("selected ARM compiler is unavailable")
        source = self.fixture_dir / "constant-reference.c"
        context = self.fixture_dir / "constant-context.json"
        target = self.fixture_dir / "constant-reference.o"
        context.write_text(json.dumps({
            "schema_version": 1, "kind": "fe8-selected-text-inputs",
            "source_root": str(ROOT), "linker": "arm-none-eabi-ld",
            "units": [{
                "logical_source": "src/constant-reference.c", "selected_source": str(source),
                "compiler_input": str(source), "object": str(target),
                "preprocessor_argv": [],
                "compiler_argv": [
                    compiler, "-mcpu=arm7tdmi", "-mthumb", "-std=gnu89", "-DMODERN=1",
                    "-I" + str(ROOT / "include"), "-I" + str(ROOT),
                    "-c", str(source), "-o", str(target),
                ],
            }],
        }), encoding="utf-8")
        forms = (
            ("void Retained(void) {{ GetStringFromIndex({value}); }}", False),
            ("void Retained(void) {{ StartTalkMsg(1, 1, {value}); }}", False),
            ("void Retained(void) {{ StartTalkMsgExt(1, 1, {value}, 0); }}", False),
            ("const struct ItemData Retained = {{.nameTextId = {value}}};", True),
            ("const struct ItemData Retained = {{.descTextId = {value}}};", True),
            ("const struct ItemData Retained = {{.useDescTextId = {value}}};", True),
            ("const struct ItemData Retained[] = {{[0] = {{.nameTextId = {value}}}}};", True),
        )
        for form, narrow in forms:
            for value, valid in (
                ("(1u)", True), ("3u - 2u", True), ("(u16)65536u", True),
                ("(u16)(65535u + 2u)", True), ("sizeof(u16)", True), (r"'\1'", True),
                ("(const unsigned short)1u", True),
                ("65537u", narrow), ("4294967297ULL", True),
                ("(1u + 2u)", False), ("-1", False), ("(u16)-1", False),
                ("sizeof(u32)", False), ("'3'", False),
                ("(const unsigned short)3u", False),
            ):
                with self.subTest(form=form, value=value):
                    source.write_text(
                        '#include "global.h"\n#include "bmitem.h"\n#include "scene.h"\n'
                        + form.format(value=value) + "\n", encoding="ascii",
                    )
                    if valid:
                        validate_compiled_references(context, target_count=3)
                    else:
                        with self.assertRaisesRegex(
                            CompiledInputError, "outside the active message set",
                        ):
                            validate_compiled_references(context, target_count=3)
        for body, valid in (
            ("struct P { int *msg[2]; }; struct P p = {.msg = {0, 0}};", True),
            ('struct P { const char *msg; }; struct P p = {.msg = "not an ID"};', True),
            ("struct P { struct { int a, b; } msg; }; struct P p = {.msg = {3, 4}};", True),
            ("struct P { u16 optionTextId; }; struct P p = {.optionTextId = 1};"
             "int Read(void) { return p.optionTextId == 0; }", True),
            ("struct P { u16 msg; }; struct P p = {.msg = 1};", True),
            ("struct P { u16 msg; }; struct P p = {.msg = 3};", False),
            ("struct P { u16 msg; }; struct P p = {.msg = {3}};", False),
            ("struct P { u16 msg; }; struct P p = {.msg = {{1},}};", True),
            ("struct P { u16 msg; }; struct P p = {.msg = {'3'}};", False),
            (r"struct P { u16 msg; }; struct P p = {.msg = {1 + '\0'}};", True),
            (r"struct P { u16 msg; }; struct P p = {.msg = {{'\1'},}};", True),
        ):
            with self.subTest(member_kind=body):
                source.write_text('#include "global.h"\n' + body + "\n", encoding="ascii")
                if valid:
                    validate_compiled_references(context, target_count=3)
                else:
                    with self.assertRaisesRegex(
                        CompiledInputError, "outside the active message set",
                    ):
                        validate_compiled_references(context, target_count=3)
        for field in ("nameTextId", "descTextId", "useDescTextId"):
            for value, valid in (("1u", True), ("3u", False)):
                with self.subTest(array_member_designator=field, value=value):
                    source.write_text(
                        '#include "global.h"\n#include "bmitem.h"\n'
                        f"const struct ItemData Retained[] = {{[0].{field} = {value}}};\n",
                        encoding="ascii",
                    )
                    if valid:
                        validate_compiled_references(context, target_count=3)
                    else:
                        with self.assertRaisesRegex(
                            CompiledInputError, "outside the active message set",
                        ):
                            validate_compiled_references(context, target_count=3)
        for value, valid in (("1u", True), ("3u", False)):
            for operand in (
                "SELECTED_MESSAGE", "(enum MessageId)SELECTED_MESSAGE",
                "(const enum MessageId)SELECTED_MESSAGE",
                "__builtin_bswap16(SELECTED_MESSAGE << 8)",
            ):
                with self.subTest(enum_value=value, operand=operand):
                    source.write_text(
                        '#include "global.h"\n'
                        f"enum MessageId {{ SELECTED_MESSAGE = {value} }};\n"
                        f"void Retained(void) {{ GetStringFromIndex({operand}); }}\n",
                        encoding="ascii",
                    )
                    if valid:
                        validate_compiled_references(context, target_count=3)
                    else:
                        with self.assertRaisesRegex(
                            CompiledInputError, "outside the active message set",
                        ):
                            validate_compiled_references(context, target_count=3)
            for body in (
                "typedef unsigned short LocalId __attribute__((may_alias));"
                "struct ItemData item = {.nameTextId = (LocalId)SELECTED_MESSAGE};"
                "GetStringFromIndex(item.nameTextId);",
                "GetStringFromIndex((StartTalkMsg(1, 1, SELECTED_MESSAGE), 1));",
            ):
                with self.subTest(local_or_nested=value, body=body):
                    source.write_text(
                        '#include "global.h"\n#include "bmitem.h"\n#include "scene.h"\n'
                        f"void Retained(void) {{ enum {{ SELECTED_MESSAGE = {value} }};"
                        + body + "}\n", encoding="ascii",
                    )
                    if valid:
                        validate_compiled_references(context, target_count=3)
                    else:
                        with self.assertRaisesRegex(
                            CompiledInputError, "outside the active message set",
                        ):
                            validate_compiled_references(context, target_count=3)
        for selected, included, valid in (("u16", "u8", False), ("u8", "u16", True)):
            with self.subTest(selected=selected, included=included):
                source.write_text(
                    '#include "global.h"\n'
                    "#if __INCLUDE_LEVEL__ == 0\n"
                    f"typedef {selected} SelectedMessageId;\n"
                    "#else\n"
                    f"typedef {included} SelectedMessageId;\n"
                    "#endif\n"
                    "void Retained(void) { GetStringFromIndex((SelectedMessageId)256u); }\n",
                    encoding="ascii",
                )
                if valid:
                    validate_compiled_references(context, target_count=3)
                else:
                    with self.assertRaisesRegex(
                        CompiledInputError, "outside the active message set",
                    ):
                        validate_compiled_references(context, target_count=3)
        for declaration in (
            "extern char *GetStringFromIndex(signed int);",
            "typedef signed int SelectedId; extern char *GetStringFromIndex(SelectedId);",
            "extern char *Unused(int), *GetStringFromIndex(signed int);",
        ):
            for local in (False, True):
                for value, valid in (("1u", True), ("3u", False)):
                    with self.subTest(declaration=declaration, local=local, value=value):
                        source.write_text(
                            '#include "global.h"\n'
                            + ("" if local else declaration)
                            + "\nvoid Retained(void) {\n"
                            + (declaration if local else "")
                            + f"\nGetStringFromIndex({value});\n}}\n", encoding="ascii",
                        )
                        if valid:
                            validate_compiled_references(context, target_count=3)
                        else:
                            with self.assertRaisesRegex(
                                CompiledInputError, "outside the active message set",
                            ):
                                validate_compiled_references(context, target_count=3)

    def test_selected_name_keeps_legacy_space_distinct_from_ideographic_space(self):
        def measure(payload):
            return build_fixed_width_label_metrics(
                ROOT, localized_payloads={"ja": {530: payload}},
                fallback_payloads={530: b"A\0"},
            )

        measured = measure(b"AAA\x81\x40AAA\0")
        records = measured["locales"]["ja"]["surfaces"]["character_name_40"]["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["canonical_width"], 36)
        self.assertEqual(records[0]["display_width"], 36)
        self.assertFalse(records[0]["alias_applied"])
        with self.assertRaisesRegex(FixedWidthLabelError, "41px"):
            measure(b"AAAA\x81\x40AAA\0")
        with self.assertRaisesRegex(FixedWidthLabelError, "46px"):
            measure(b"AAA" + "\u3000".encode("utf-8") + b"AAA\0")

    def test_selected_typed_name_sources_enforce_every_narrow_surface(self):
        inputs = self.selected_inputs()
        inputs.update(event_root_path=self.fixture_dir, enabled_locales=("ja",))
        mapping = json.loads(inputs["mapping_path"].read_bytes())
        mapping["rows"][1]["source"] = {"kind": "authored", "translation_key": "narrow"}
        inputs["mapping_path"].write_text(json.dumps(mapping), encoding="utf-8")
        authored = self.fixture_dir / "authored.ja.json"
        authored.write_text(json.dumps({
            "kind": "fe8u-game-authored-catalog", "locale": "ja",
            "strings": {"narrow": "日" * 10},
        }, ensure_ascii=False), encoding="utf-8")
        inputs["authored_paths"] = {"ja": authored}
        aliases_path = self.fixture_dir / "aliases.json"
        default_aliases = json.loads((ROOT / "texts/locales/fixed_width_display_aliases.json").read_bytes())
        for table, surface in (
            ("characters", "character_name_40"),
            ("classes", "class_name_64"),
            ("items", "item_name_56"),
        ):
            with self.subTest(table=table):
                schema = SchemaInputs().resolve(table)
                data = json.loads((ROOT / schema.default_source).read_bytes())
                data[table] = data[table][:1]
                data[table][0]["nameTextId"] = 1
                source = self.fixture_dir / (table + ".json")
                source.write_text(json.dumps(data), encoding="utf-8")
                context = SchemaInputs({table: str(source)}, message_header=str(inputs["target_header_path"]))
                with self.assertRaisesRegex(GameCatalogError, "canonical width"):
                    build_game_catalog(**inputs, schema_inputs=context)
                aliases = json.loads(json.dumps(default_aliases))
                aliases["aliases"]["ja"][surface]["0x0001"] = "日"
                aliases_path.write_text(json.dumps(aliases, ensure_ascii=False), encoding="utf-8")
                build = build_game_catalog(
                    **inputs, schema_inputs=context, fixed_width_aliases_path=aliases_path,
                )
                self.assertEqual(build.display_aliases["ja"][surface], {1: "日"})
                self.assertEqual(build.report["fixed_width_labels"]["summary"]["alias_count"], 1)
                arguments = [
                    sys.executable, "-B", "-m", "scripts.localization.game_catalog", "check-width",
                    "--english-texts", str(inputs["english_texts_path"]),
                    "--english-definitions", str(inputs["english_definitions_path"]),
                    "--mapping", str(inputs["mapping_path"]),
                    "--target-header", str(inputs["target_header_path"]),
                    "--event-root", str(self.fixture_dir),
                    "--ja-indexed", str(inputs["ja_indexed_path"]),
                    "--authored", "ja=" + str(authored), "--enabled-locales", "ja",
                    "--source-override", table + "=" + str(source),
                ]
                result = subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("canonical width", result.stderr)
                result = subprocess.run(
                    [*arguments, "--fixed-width-aliases", str(aliases_path)],
                    cwd=ROOT, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                data[table][0]["nameTextId"] = 3
                source.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(GameCatalogError):
                    build_game_catalog(
                        **inputs, schema_inputs=context, fixed_width_aliases_path=aliases_path,
                    )

    def test_selected_name_terminators_do_not_change_rendered_width(self):
        inputs = self.selected_inputs()
        inputs.update(event_root_path=self.fixture_dir, enabled_locales=("ja",))
        with inputs["english_definitions_path"].open("a", encoding="utf-8") as stream:
            stream.write("[.] = 31\n")
        mapping = json.loads(inputs["mapping_path"].read_bytes())
        mapping["rows"][1]["source"] = {"kind": "authored", "translation_key": "narrow"}
        inputs["mapping_path"].write_text(json.dumps(mapping), encoding="utf-8")
        authored = self.fixture_dir / "authored.ja.json"
        inputs["authored_paths"] = {"ja": authored}
        items = json.loads((ROOT / "src/data/items.json").read_bytes())
        items["items"] = items["items"][:1]
        items["items"][0]["nameTextId"] = 1
        source = self.fixture_dir / "items.json"
        source.write_text(json.dumps(items), encoding="utf-8")
        context = SchemaInputs({"items": str(source)})
        for suffix in ("", "[.][X]"):
            with self.subTest(suffix=suffix):
                authored.write_text(json.dumps({
                    "kind": "fe8u-game-authored-catalog", "locale": "ja",
                    "strings": {"narrow": "剣" * 6 + suffix},
                }, ensure_ascii=False), encoding="utf-8")
                build = build_game_catalog(**inputs, schema_inputs=context)
                self.assertEqual(build.display_aliases["ja"]["item_name_56"], {})
                record, = build.report["fixed_width_labels"]["locales"]["ja"]["surfaces"][
                    "item_name_56"]["records"]
                self.assertEqual(record["canonical_width"], 54)
                self.assertEqual(record["display_text"], "剣" * 6)

    def test_selected_names_reject_substitutions_and_generated_wrapping(self):
        inputs = self.selected_inputs()
        inputs.update(event_root_path=self.fixture_dir, enabled_locales=("ja",))
        mapping = json.loads(inputs["mapping_path"].read_bytes())
        mapping["rows"][1]["source"] = {"kind": "authored", "translation_key": "narrow"}
        inputs["mapping_path"].write_text(json.dumps(mapping), encoding="utf-8")
        authored = self.fixture_dir / "authored.ja.json"
        inputs["authored_paths"] = {"ja": authored}
        items = json.loads((ROOT / "src/data/items.json").read_bytes())
        items["items"] = items["items"][:1]
        items["items"][0]["nameTextId"] = 1
        source = self.fixture_dir / "items.json"
        source.write_text(json.dumps(items), encoding="utf-8")
        context = SchemaInputs({"items": str(source)})
        aliases = json.loads((ROOT / "texts/locales/fixed_width_display_aliases.json").read_bytes())
        aliases["aliases"]["ja"]["item_name_56"]["0x0001"] = "剣"
        alias_path = self.fixture_dir / "aliases.json"
        alias_path.write_text(json.dumps(aliases, ensure_ascii=False), encoding="utf-8")
        for text, error in (
            ("MMMM[CTRL:0080][CTRL:0020]", "substitution"), ("剣" * 30, "240px"),
        ):
            with self.subTest(text=text):
                authored.write_text(json.dumps({
                    "kind": "fe8u-game-authored-catalog", "locale": "ja",
                    "strings": {"narrow": text},
                }, ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(GameCatalogError, error):
                    build_game_catalog(
                        **inputs, schema_inputs=context,
                        fixed_width_aliases_path=alias_path if text.startswith("剣") else
                        ROOT / "texts/locales/fixed_width_display_aliases.json",
                    )

    def test_selected_streams_enforce_the_real_transform_capacity(self):
        inputs = self.selected_inputs()
        inputs.update(event_root_path=self.fixture_dir, enabled_locales=("ja",))
        with inputs["english_definitions_path"].open("a", encoding="utf-8") as stream:
            stream.write("[Item] = 0x80, 0x22\n")
        original = inputs["english_texts_path"].read_text()
        indexed = inputs["ja_indexed_path"].read_text()
        for locale in ("en", "ja"):
            for count in (2, 1025):
                with self.subTest(locale=locale, count=count):
                    inputs["english_texts_path"].write_text(
                        original.replace("English zero", "[Item]" * count)
                        if locale == "en" else original,
                        encoding="utf-8",
                    )
                    inputs["ja_indexed_path"].write_text(
                        indexed.replace(
                            "日[CTRL:0080][CTRL:0020]", "[CTRL:0080][CTRL:0022]" * count,
                        )
                        if locale == "ja" else indexed,
                        encoding="utf-8",
                    )
                    if count == 1025:
                        with self.assertRaisesRegex(GameCatalogError, "transform"):
                            build_game_catalog(**inputs)
                    else:
                        build_game_catalog(**inputs)

    def test_composition_derives_required_glyphs_before_render_validation(self):
        from scripts.localization.game_catalog.build import compose_game_catalog

        source = self.fixture_dir / "ja_indexed.txt"
        source.write_text(source.read_text().replace("日[CTRL:", "試[CTRL:"))
        inputs = self.selected_inputs()
        composition = compose_game_catalog(**inputs)
        japanese = next(bundle for bundle in composition.locales if bundle.locale == "ja")
        self.assertEqual(
            japanese.catalog.decode_entry(0), "試".encode() + b"\x80\x20\0"
        )
        with self.assertRaisesRegex(GameCatalogError, "U\\+8A66"):
            build_game_catalog(**inputs, event_root_path=self.fixture_dir)

    def test_selected_header_cannot_bind_a_message_symbol_to_another_entry(self):
        header = self.fixture_dir / "msg.h"
        header.write_text(
            header.read_text(encoding="utf-8").replace(
                "#define MSG_FIXTURE_ONE 0x0001", "#define MSG_FIXTURE_ONE 0x0002"
            ),
            encoding="utf-8",
        )
        with self.assertRaises(GameCatalogError):
            build_game_catalog(
                english_texts_path=self.fixture_dir / "texts.txt",
                english_definitions_path=self.fixture_dir / "textdefs.txt",
                ja_indexed_path=self.fixture_dir / "ja_indexed.txt",
                ja_raw_path=self.fixture_dir / "ja_raw.json",
                zh_indexed_path=self.fixture_dir / "zh_indexed.txt",
                zh_raw_path=self.fixture_dir / "zh_raw.json",
                mapping_path=self.fixture_dir / "mapping.json",
                target_header_path=header,
                event_root_path=self.fixture_dir,
                ja_raw_expected_repository=None,
                ja_raw_expected_revision=None,
            )

    def test_selected_header_cannot_retain_an_allocation_absent_from_the_active_source(self):
        header = self.fixture_dir / "msg.h"
        with header.open("a", encoding="utf-8") as stream:
            stream.write("#define MSG_RETIRED 0xFFFF\n")
        with self.assertRaises(GameCatalogError):
            build_game_catalog(
                english_texts_path=self.fixture_dir / "texts.txt",
                english_definitions_path=self.fixture_dir / "textdefs.txt",
                ja_indexed_path=self.fixture_dir / "ja_indexed.txt",
                ja_raw_path=self.fixture_dir / "ja_raw.json",
                zh_indexed_path=self.fixture_dir / "zh_indexed.txt",
                zh_raw_path=self.fixture_dir / "zh_raw.json",
                mapping_path=self.fixture_dir / "mapping.json",
                target_header_path=header,
                event_root_path=self.fixture_dir,
                ja_raw_expected_repository=None,
                ja_raw_expected_revision=None,
            )

    def test_selected_header_cannot_hide_unsupported_or_rebound_allocations(self):
        header = self.fixture_dir / "msg.h"
        original = header.read_text(encoding="utf-8")
        inputs = self.selected_inputs()
        inputs["event_root_path"] = self.fixture_dir
        for directive in (
            "#define MSG_RETIRED 0xFFFFu\n",
            "#define MSG_RETIRED (1u + 2u)\n",
            "#define RETIRED_ALIAS -1\n",
            "#undef MSG_FIXTURE_ONE\n#define MSG_FIXTURE_ONE (0x0002)\n",
            "#define MSG_FIXTURE_ONE (0x0002)\n",
        ):
            with self.subTest(directive=directive):
                header.write_text(original + directive, encoding="utf-8")
                with self.assertRaises(GameCatalogError):
                    build_game_catalog(**inputs)
        header.write_text(
            original.replace(
                "#define MSG_FIXTURE_ONE 0x0001",
                "#define MSG_FIXTURE_ONE ((0x0001u)) /* same allocation */",
            ).replace(
                "#define MSG_FIXTURE_TWO 0x0002",
                "#define MSG_FIXTURE_TWO 02UL",
            )
            + "/*\n#define MSG_COMMENTED_OUT 0xFFFF\n*/\n",
            encoding="utf-8",
        )
        build = build_game_catalog(**inputs)
        self.assertEqual(build.target_count, 3)

    def test_non_stock_catalog_rejects_an_overwide_unbreakable_system_label(self):
        source = self.fixture_dir / "ja_indexed.txt"
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "日[CTRL:0080][CTRL:0020]", "A" * 80 + "[CTRL:0080][CTRL:0020]", 1
            ),
            encoding="utf-8",
        )
        with self.assertRaises(GameCatalogError):
            build_game_catalog(
                english_texts_path=self.fixture_dir / "texts.txt",
                english_definitions_path=self.fixture_dir / "textdefs.txt",
                ja_indexed_path=source,
                ja_raw_path=self.fixture_dir / "ja_raw.json",
                zh_indexed_path=self.fixture_dir / "zh_indexed.txt",
                zh_raw_path=self.fixture_dir / "zh_raw.json",
                mapping_path=self.fixture_dir / "mapping.json",
                target_header_path=self.fixture_dir / "msg.h",
                event_root_path=self.fixture_dir,
                ja_raw_expected_repository=None,
                ja_raw_expected_revision=None,
            )

    def test_effective_english_fallback_cannot_hide_an_invalid_portrait_operand(self):
        definitions = self.fixture_dir / "textdefs.txt"
        with definitions.open("a", encoding="utf-8") as stream:
            stream.write("[LoadFace] = 0x10\n[FID_REVIEW_INVALID] = 0xFE, 0xFF\n")
        source = self.fixture_dir / "texts.txt"
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "English[TAB]two[AccentedE][X]",
                "[LoadFace][FID_REVIEW_INVALID]Fallback[X]",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(GameCatalogError, "invalid FE8U FID"):
            build_game_catalog(
                english_texts_path=source,
                english_definitions_path=definitions,
                ja_indexed_path=self.fixture_dir / "ja_indexed.txt",
                ja_raw_path=self.fixture_dir / "ja_raw.json",
                zh_indexed_path=self.fixture_dir / "zh_indexed.txt",
                zh_raw_path=self.fixture_dir / "zh_raw.json",
                mapping_path=self.fixture_dir / "mapping.json",
                target_header_path=self.fixture_dir / "msg.h",
                event_root_path=self.fixture_dir,
                ja_raw_expected_repository=None,
                ja_raw_expected_revision=None,
            )

    def test_fixture_build_materializes_raw_providers_and_preserves_explicit_fallback(self):
        build = build_game_catalog(
            english_texts_path=self.fixture_dir / "texts.txt",
            english_definitions_path=self.fixture_dir / "textdefs.txt",
            ja_indexed_path=self.fixture_dir / "ja_indexed.txt",
            ja_raw_path=self.fixture_dir / "ja_raw.json",
            zh_indexed_path=self.fixture_dir / "zh_indexed.txt",
            zh_raw_path=self.fixture_dir / "zh_raw.json",
            mapping_path=self.fixture_dir / "mapping.json",
            target_header_path=self.fixture_dir / "msg.h",
            event_root_path=self.fixture_dir,
            ja_raw_expected_repository=None,
            ja_raw_expected_revision=None,
        )
        ja = build.locale_bundle("ja")
        zh = build.locale_bundle("zh-Hans")

        self.assertEqual(build.english.catalog.decode_entry(0), b"English zero\x00")
        self.assertEqual(
            build.english.catalog.decode_entry(1), b'"English-one"\x00'
        )
        self.assertEqual(
            build.english.catalog.decode_entry(2),
            b"English" + "\u3000".encode("utf-8") + b"twoe\x00",
        )
        self.assertEqual(ja.catalog.decode_entry(0), "日".encode("utf-8") + b"\x80\x20\x00")
        self.assertEqual(ja.catalog.decode_entry(1), "生".encode("utf-8") + b"\x00")
        self.assertEqual(zh.catalog.decode_entry(1), "原始".encode("utf-8") + b"\x00")
        self.assertIsNone(zh.catalog.decode_entry(2))
        validation = build.report["control_stream_validation"]
        self.assertEqual(validation["validated_fallback_payload_count"], 2)
        self.assertEqual(validation["validated_payload_count"], 6)

        report = build.report
        self.assertEqual(report["mapping_source_counts"]["indexed"], 1)
        self.assertEqual(report["mapping_source_counts"]["raw"], 1)
        self.assertEqual(report["mapping_source_counts"]["english_fallback"], 1)
        self.assertEqual(report["locales"]["ja"]["provider_counts"]["raw"], 1)
        self.assertEqual(report["locales"]["ja"]["provider_unavailable_count"], 0)
        self.assertEqual(report["locales"]["ja"]["explicit_fallback_count"], 1)
        self.assertEqual(report["locales"]["zh-Hans"]["provider_counts"]["raw"], 1)
        self.assertEqual(report["locales"]["zh-Hans"]["explicit_fallback_count"], 1)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            written = write_build(build, output_dir=output)
            probe = output / "declaration-and-storage.c"
            probe.write_text(
                '#include "game_localization_declarations.h"\n'
                '#include "game_localization_catalog.h"\n'
                "const struct GameLocalizationLocaleCatalog *Probe(void)\n"
                "{ return &gGameLocalizationCatalogJa; }\n",
                encoding="ascii",
            )
            for source in (written["source"], probe):
                result = subprocess.run(
                    [
                        "cc", "-std=c89", "-pedantic-errors", "-Wall", "-Wextra", "-Werror",
                        "-I", str(Path(__file__).parent / "host_include"),
                        "-I", str(output), "-fsyntax-only", str(source),
                    ],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_selected_events_drive_both_width_context_and_active_id_validation(self):
        inputs = self.selected_inputs()
        inputs["event_root_path"] = self.fixture_dir
        source = self.fixture_dir / "selected.c"
        source.write_text(
            "EventScr Selected[] = {\n"
            "    /* TEXTSHOW(MSG_RETIRED) */\n"
            "    // TEXTSHOW(MSG_UNRESOLVED)\n"
            "    TEXTSHOW(MSG_FIXTURE_ONE)\n    ENDA\n};\n"
        )
        build = build_game_catalog(**inputs)
        self.assertEqual(build.report["control_stream_validation"]["modeled_target_count"], 1)
        policy = load_width_registry(ROOT / DEFAULT_WIDTH_REGISTRY_PATH)
        for locale in ("ja", "zh-Hans"):
            record = next(
                row for row in build.report["width_validation"][locale]["records"]
                if row["target_id"] == "0x0001"
            )
            self.assertIn(policy.talk_context, record["contexts"])
            self.assertTrue(any(
                item["renderer"] == "talk" and item["style"] == "talk"
                for item in record["requirements"]
            ))
        texts = self.fixture_dir / "texts.txt"
        original_texts = texts.read_text()
        numeric_texts = original_texts.replace("## MSG_FIXTURE_ONE", "#0x1")
        self.assertNotEqual(original_texts, numeric_texts)
        texts.write_text(numeric_texts)
        header = self.fixture_dir / "msg.h"
        header.write_text(header.read_text().replace("MSG_FIXTURE_ONE", "MSG_001"))
        source.write_text(
            "EventScr Selected[] = {\n    TEXTSHOW(MSG_001)\n    ENDA\n};\n"
        )
        numeric_build = build_game_catalog(**inputs)
        self.assertEqual(
            numeric_build.report["control_stream_validation"]["modeled_target_count"], 1
        )
        source.write_text(
            "EventScr Selected[] = {\n    TEXTSHOW(0x0003)\n    ENDA\n};\n"
        )
        with self.assertRaisesRegex(GameCatalogError, "active.*set"):
            build_game_catalog(**inputs)
        source.write_text(
            "EventScr Selected[] = {\n    TEXTSHOW(MSG_UNRESOLVED)\n    ENDA\n};\n"
        )
        with self.assertRaisesRegex(GameCatalogError, "unresolved event message"):
            build_game_catalog(**inputs)
        inputs["event_root_path"] = self.fixture_dir / "missing"
        with self.assertRaisesRegex(GameCatalogError, "event source directory"):
            build_game_catalog(**inputs)

    def test_committed_build_emits_verified_japanese_raw_literal(self):
        build = build_game_catalog()
        ja = build.locale_bundle("ja")

        self.assertEqual(
            ja.catalog.decode_entry(0x0023),
            b"\x81\x40" + "決定".encode("utf-8") + b"\x00",
        )
        self.assertEqual(
            ja.catalog.decode_entry(0x059C),
            "売る".encode("utf-8")
            + b"\x81\x40\x81\x40"
            + "やめる".encode("utf-8")
            + b"\x00",
        )
        self.assertEqual(build.report["locales"]["ja"]["provider_counts"]["raw"], 130)
        self.assertEqual(
            ja.catalog.decode_entry(0x01C1),
            "残り".encode("utf-8") + b"\x00",
        )


if __name__ == "__main__":
    unittest.main()
