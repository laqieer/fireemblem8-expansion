"""Positive and adversarial host checks for the version-1 asset manifest."""

from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import unittest
import zlib
from unittest import mock
from types import SimpleNamespace

from scripts.assets import banim, cli, manifest, tmx
from scripts.generated_data.diagnostics import GeneratedDataError, GeneratedDataValidationError


REPO_ROOT = manifest.REPO_ROOT
TEST_ROOT = os.path.join(REPO_ROOT, "build", "generated", "assets", "test-work")
FIXTURE_ROOT = "assets/tmx"
TMX_FIXTURE_ROOT = os.path.join(
    REPO_ROOT, "scripts", "assets", "tests", "fixtures", "tmx"
)


def valid_record():
    return {
        "id": "CH2_MAIN_MAP",
        "kind": "tiled-tmx-map-layout",
        "sources": [FIXTURE_ROOT + "/Ch2Map.tmx"],
        "dependsOn": [],
        "options": {
            "format": "tmx-safe-v1",
            "compression": "lz77",
            "layer": "Main",
            "tilesetId": "fe8-metatiles-16px-4096",
        },
        "ownership": {
            "seam": "chapter-data-asset-table",
            "tableSource": "src/data/data_8B363C.c",
            "chapterSettings": "src/data/chapter_settings.json",
            "chapterSettingsIndex": 2,
            "mainLayerId": 11,
            "symbol": "Ch2Map",
            "consumer": "GetChapterMapPointer",
        },
        "resources": {"mapWidth": 15, "mapHeight": 15, "mapBufferBytes": 2048},
        "provenance": {
            "origin": "test fixture",
            "license": "test-only provenance; not legal clearance",
            "modifications": "none",
            "tools": [],
        },
    }


def valid_portrait_record(root):
    package = os.path.join(root, "portrait-package")
    os.makedirs(package)
    sheet = os.path.join(package, "proof.png")
    metadata = os.path.join(package, "metadata.json")
    sidecar = os.path.join(package, "proof.pal")
    registry = os.path.join(root, "portrait_registry.json")
    write_indexed_png(sheet)
    write_jasc_palette(sidecar)
    with open(metadata, "w", encoding="utf-8") as handle:
        json.dump({
            "schemaVersion": 1,
            "portraitId": 1,
            "symbol": "Proof",
            "blinkKind": "FACE_BLINK_NORMAL",
            "anchors": {"mouth": [2, 6], "eyes": [3, 4]},
            "frames": {
                "main": [0, 0, 80, 72],
                "minimug": [80, 0, 32, 32],
                "eyeOpen": [0, 72, 32, 16],
                "eyeClosed": [32, 72, 32, 16],
                "mouthClosed": [64, 72, 32, 16],
                "mouthOpen": [96, 72, 32, 16],
            },
            "alias": {"mode": "generated", "components": {}},
        }, handle)
    entries = []
    for portrait_id in range(1, 173):
        entries.append({
            "id": portrait_id,
            "img": "portrait_Mystery_1_tileset",
            "imgChibi": "portrait_Mystery_1_chibi",
            "pal": "portrait_Mystery_1_palette",
            "imgMouth": "portrait_Mystery_1_mouth",
            "imgCard": None,
            "xMouth": 2,
            "yMouth": 5,
            "xEyes": 3,
            "yEyes": 3,
            "blinkKind": "FACE_BLINK_NORMAL",
        })
    with open(registry, "w", encoding="utf-8") as handle:
        json.dump({"schemaVersion": 1, "entries": entries}, handle)
    return {
        "id": "PROOF_FORMATTED_PORTRAIT",
        "kind": "formatted-portrait-package",
        "sources": [
            os.path.relpath(sheet, REPO_ROOT),
            os.path.relpath(metadata, REPO_ROOT),
            os.path.relpath(sidecar, REPO_ROOT),
        ],
        "dependsOn": [],
        "options": {
            "format": "fe7-fe8-formatted-png",
            "adapterVersion": 1,
            "jascSidecar": True,
        },
        "ownership": {
            "seam": "portrait-data-table",
            "tableSource": "src/portrait_data.c",
            "registrySource": os.path.relpath(registry, REPO_ROOT),
            "portraitId": 1,
            "symbol": "Proof",
            "consumer": "GetPortraitData",
        },
        "resources": {
            "sheetWidth": 128,
            "sheetHeight": 112,
            "paletteColors": 16,
            "mainBytes": 2880,
            "minimugBytes": 512,
            "eyeFrameBytes": 256,
            "mouthFrameBytes": 256,
        },
        "provenance": {
            "origin": "synthetic test fixture",
            "license": "test-only; not legal clearance",
            "modifications": "none",
            "tools": ["python standard library"],
        },
    }


def png_chunk(kind, payload):
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def write_indexed_png_stream(
    path, *, width=128, height=112, depth=4, indexed=True, interlace=0, idat=b""
):
    color_type = 3 if indexed else 2
    palette = bytes(component for value in range(16) for component in (value * 16,) * 3)
    chunks = [
        b"\x89PNG\r\n\x1a\n",
        png_chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, interlace),
        ),
    ]
    if indexed:
        chunks.extend(
            (png_chunk(b"PLTE", palette), png_chunk(b"tRNS", b"\0" + b"\xff" * 15))
        )
    chunks.extend((png_chunk(b"IDAT", idat), png_chunk(b"IEND", b"")))
    with open(path, "wb") as handle:
        handle.write(b"".join(chunks))


def write_indexed_png(path, width=128, height=112, indexed=True):
    depth = 4 if indexed else 8
    rows = b"".join(
        b"\0" + (b"\0" * ((width * depth + 7) // 8)) for _ in range(height)
    )
    write_indexed_png_stream(
        path,
        width=width,
        height=height,
        depth=depth,
        indexed=indexed,
        idat=zlib.compress(rows),
    )


def write_jasc_palette(path):
    with open(path, "w", encoding="ascii") as handle:
        handle.write("JASC-PAL\n0100\n16\n")
        for value in range(16):
            handle.write("{0} {0} {0}\n".format(value * 16))


class AssetManifestTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_ROOT):
            shutil.rmtree(TEST_ROOT)
        os.makedirs(TEST_ROOT)

    def tearDown(self):
        if os.path.exists(TEST_ROOT):
            shutil.rmtree(TEST_ROOT)

    def write_manifest(self, assets):
        return self.write_document({"schemaVersion": manifest.SCHEMA_VERSION, "assets": assets})

    def write_document(self, document):
        path = os.path.join(TEST_ROOT, "manifest.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
        return path

    def assert_validation_error(self, assets, text):
        with self.assertRaises(GeneratedDataValidationError) as raised:
            manifest.load_and_validate(self.write_manifest(assets))
        self.assertIn(text, str(raised.exception))

    def run_assets_make(self, manifest_path, output_dir, *goals):
        return subprocess.run(
            [
                "make",
                "-f",
                "assets.mk",
                *goals,
                "PYTHON={}".format(sys.executable),
                "ASSET_MANIFEST={}".format(manifest_path),
                "ASSET_OUTPUT_DIR={}".format(output_dir),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_real_manifest_is_valid_and_deterministic(self):
        path = os.path.join(REPO_ROOT, "assets", "manifest.json")
        first = manifest.load_and_validate(path)
        second = manifest.load_and_validate(path)
        rendered = manifest.render_makefile(first)
        self.assertEqual(rendered, manifest.render_makefile(second))
        self.assertIn(
            "$(MODERN_OUTPUT_DIR)/src/data/data_8B363C.o: "
            "$(ASSET_MANIFEST_SOURCE_STAMP)",
            rendered,
        )

    def test_eirika_existing_component_alias_preserves_face_data_row(self):
        records = manifest.load_and_validate(os.path.join(REPO_ROOT, "assets", "manifest.json"))
        registry = manifest._read_portrait_registry("assets/portrait_registry.json")
        eirika = registry[2]
        self.assertEqual(
            (
                eirika["img"],
                eirika["imgChibi"],
                eirika["pal"],
                eirika["imgMouth"],
                eirika["xMouth"],
                eirika["yMouth"],
                eirika["xEyes"],
                eirika["yEyes"],
                eirika["blinkKind"],
            ),
            (
                "portrait_Eirika_tileset",
                "portrait_Eirika_chibi",
                "portrait_Eirika_palette",
                "portrait_Eirika_mouth",
                2,
                6,
                3,
                4,
                "FACE_BLINK_NORMAL",
            ),
        )
        rendered = manifest.render_portrait_data(records)
        self.assertIn(
            "{portrait_Eirika_tileset, portrait_Eirika_chibi, portrait_Eirika_palette, "
            "portrait_Eirika_mouth, 0, 2, 6, 3, 4, FACE_BLINK_NORMAL}, // 1",
            rendered,
        )
        self.assertEqual(manifest.portrait_registration_ids(), tuple(range(1, 173)))

    def test_portrait_registration_surface_includes_global_first(self):
        source = os.path.join(REPO_ROOT, "src", "portrait_data.c")
        with open(source, encoding="utf-8") as handle:
            includes = [
                line.strip()
                for line in handle
                if line.startswith("#include ")
            ]
        self.assertEqual(includes[0], '#include "global.h"')
        self.assertIn('#include "portrait_data.inc"', includes)

    def test_portrait_registry_is_complete_without_package_overrides(self):
        records = manifest.load_and_validate(self.write_manifest([valid_record()]))
        rendered = manifest.render_portrait_data(records)
        self.assertEqual(rendered.count("// "), 172)
        self.assertEqual(
            manifest.portrait_registration_ids(self.write_manifest([valid_record()])),
            tuple(range(1, 173)),
        )

    def test_generated_fragment_uses_existing_chapter_table_object(self):
        records = manifest.load_and_validate(self.write_manifest([valid_record()]))
        rendered = manifest.render_makefile(records)
        self.assertIn("src/data/data_8B363C.o:", rendered)
        self.assertIn(
            "src/data/const_data_chapter_maps.o: $(ASSET_OUTPUT_DIR)/tmx/CH2_MAIN_MAP.bin.lz",
            rendered,
        )
        self.assertNotIn("gAsset", rendered)
        self.assertNotIn("linker_script", rendered)

    def test_duplicate_id_and_ownership_fail(self):
        first = valid_record()
        second = copy.deepcopy(first)
        self.assert_validation_error([first, second], "duplicate asset id")
        self.assert_validation_error([first, second], "ownership conflict")

    def test_unknown_kind_and_unsafe_path_fail_closed(self):
        unknown = valid_record()
        unknown["kind"] = "editor-import"
        self.assert_validation_error([unknown], "unknown asset kind")
        unsafe = valid_record()
        unsafe["sources"][0] = "build/generated/assets/Map.mar"
        self.assert_validation_error([unsafe], "unsafe source path")

    def test_posix_unicode_and_symlink_source_paths_fail_closed(self):
        non_posix = valid_record()
        non_posix["sources"][0] = "assets\\tmx\\Ch2Map.tmx"
        self.assert_validation_error([non_posix], "normalized POSIX separators")
        non_nfc = valid_record()
        non_nfc["sources"][0] = "assets/tmx/Ch2Map\u0065\u0301.tmx"
        self.assert_validation_error([non_nfc], "NFC-normalized Unicode")

        link_path = os.path.join(
            REPO_ROOT,
            "scripts",
            "assets",
            "tests",
            ".asset_manifest_source_link.tmx",
        )
        os.symlink(
            os.path.join(REPO_ROOT, "assets", "tmx", "Ch2Map.tmx"),
            link_path,
        )
        self.addCleanup(lambda: os.path.lexists(link_path) and os.unlink(link_path))
        symlink = valid_record()
        symlink["sources"][0] = "scripts/assets/tests/.asset_manifest_source_link.tmx"
        self.assert_validation_error([symlink], "must not traverse a symbolic link")

    def test_casefold_and_unicode_source_collision_keys_are_stable(self):
        self.assertEqual(
            manifest._canonical_source_key("graphics/Map/Tile.mar"),
            manifest._canonical_source_key("graphics/map/tile.mar"),
        )
        self.assertEqual(
            manifest._canonical_source_key("graphics/\u00e9.mar"),
            manifest._canonical_source_key("graphics/e\u0301.mar"),
        )

    def test_schema_version_and_unknown_option_fail_closed(self):
        with self.assertRaises(GeneratedDataError) as raised:
            manifest.load_and_validate(self.write_document({
                "schemaVersion": 2,
                "assets": [valid_record()],
            }))
        self.assertIn("unsupported schema version", str(raised.exception))
        options = valid_record()
        options["options"]["editorMode"] = "automatic"
        with self.assertRaises(GeneratedDataValidationError) as raised:
            manifest.load_and_validate(self.write_manifest([options]))
        self.assertIn("unknown field 'editorMode'", str(raised.exception))

    def test_malformed_provenance_and_chapter_settings_fail_closed(self):
        missing_provenance = valid_record()
        del missing_provenance["provenance"]["license"]
        self.assert_validation_error([missing_provenance], "missing required field 'license'")
        extra_provenance = valid_record()
        extra_provenance["provenance"]["reviewer"] = "unexpected"
        self.assert_validation_error([extra_provenance], "unknown field 'reviewer'")
        missing_settings = valid_record()
        missing_settings["ownership"]["chapterSettings"] = "missing/settings.json"
        self.assert_validation_error([missing_settings], "ownership.chapterSettings")

    def test_dangling_dependency_and_bad_provenance_fail(self):
        dangling = valid_record()
        dangling["dependsOn"] = ["MISSING_ASSET"]
        self.assert_validation_error([dangling], "dangling dependency")
        provenance = valid_record()
        provenance["provenance"]["license"] = ""
        self.assert_validation_error([provenance], "provenance.license")

    def test_dependency_cycles_and_duplicate_edges_fail(self):
        first = valid_record()
        second = copy.deepcopy(first)
        second["id"] = "CH2_OTHER_MAP"
        first["dependsOn"] = ["CH2_OTHER_MAP"]
        second["dependsOn"] = ["CH2_MAIN_MAP", "CH2_MAIN_MAP"]
        self.assert_validation_error([first, second], "dependency cycle")
        self.assert_validation_error([first, second], "duplicate dependency")

    def test_cross_kind_ownership_collisions_fail(self):
        class StaticTestKind:
            name = "static-test-kind"

            def validate(self, record, diagnostics):
                del record, diagnostics

            def ownership_key(self, record):
                return "chapter-data-asset-table:src/data/data_8B363C.c:11"

            def make_dependencies(self, record):
                del record
                return ()

        other = valid_record()
        other["id"] = "STATIC_TEST_ASSET"
        other["kind"] = StaticTestKind.name
        other["options"] = {"adapter": "test"}
        other["ownership"] = {"seam": "test"}
        other["resources"] = {"capacity": 1}
        with mock.patch.dict(
            manifest.KIND_REGISTRY._kinds,
            {StaticTestKind.name: StaticTestKind()},
        ):
            self.assert_validation_error([valid_record(), other], "ownership conflict")

    def test_formatted_portrait_package_generates_components_and_registration(self):
        record = valid_portrait_record(TEST_ROOT)
        source = self.write_manifest([record])
        out_dir = os.path.join(TEST_ROOT, "out")
        with mock.patch.object(manifest, "_repo_path", side_effect=lambda path, *_: path):
            records = manifest.generate(source, out_dir)
            manifest.check(source, out_dir)
        with open(os.path.join(out_dir, manifest.OUTPUT_PORTRAIT_DATA), encoding="utf-8") as handle:
            generated = handle.read()
        self.assertIn("portrait_Proof_tileset", generated)
        self.assertIn("// 0", generated)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "portraits", record["id"], "tileset.4bpp.lz")))
        self.assertEqual(len(manifest.portrait_component_outputs(records, out_dir)), 7)
        with open(
            os.path.join(out_dir, manifest.OUTPUT_PORTRAIT_COMPONENTS),
            encoding="utf-8",
        ) as handle:
            generated_components = handle.read()
        self.assertIn(
            "u16 __attribute__((aligned(4))) portrait_Proof_palette[] = INCBIN_U16(",
            generated_components,
        )
        self.assertEqual(
            manifest.portrait_incbin_consumer_ids(records),
            ("PROOF_FORMATTED_PORTRAIT",),
        )

    def test_formatted_portrait_package_uses_tile_ordered_low_nibble_first_4bpp(self):
        rows = [[1, 2] * 4 + [3, 4] * 4 for _ in range(8)]
        packed = manifest._pack_4bpp(rows, 0, 0, 16, 8)
        self.assertEqual(
            packed[:4],
            bytes((0x21, 0x21, 0x21, 0x21)),
        )
        self.assertEqual(packed[32:36], bytes((0x43, 0x43, 0x43, 0x43)))
        with self.assertRaisesRegex(ValueError, "multiples of 8"):
            manifest._pack_4bpp([[1, 2, 3, 4]], 0, 0, 4, 1)

    def test_formatted_portrait_package_rejects_sheet_and_metadata_failures(self):
        record = valid_portrait_record(TEST_ROOT)
        source = self.write_manifest([record])
        with mock.patch.object(manifest, "_repo_path", side_effect=lambda path, *_: path):
            manifest.load_and_validate(source)

            write_indexed_png(os.path.join(REPO_ROOT, record["sources"][0]), width=127)
            self.assert_validation_error([record], "exactly 128x112")

            write_indexed_png(os.path.join(REPO_ROOT, record["sources"][0]))
            with open(os.path.join(REPO_ROOT, record["sources"][1]), encoding="utf-8") as handle:
                metadata = json.load(handle)
            del metadata["anchors"]
            with open(os.path.join(REPO_ROOT, record["sources"][1]), "w", encoding="utf-8") as handle:
                json.dump(metadata, handle)
            self.assert_validation_error([record], "must contain exactly")

    def test_formatted_portrait_png_reader_bounds_decompression(self):
        huge_path = os.path.join(TEST_ROOT, "huge-ihdr.png")
        write_indexed_png_stream(
            huge_path,
            width=0x7FFFFFFF,
            height=0x7FFFFFFF,
            idat=zlib.compress(b"bomb"),
        )
        with mock.patch.object(manifest.zlib, "decompressobj") as decompressor:
            with self.assertRaisesRegex(ValueError, "exactly 128x112"):
                manifest._read_png(huge_path)
        decompressor.assert_not_called()

        expected_size = 112 * (((128 * 4 + 7) // 8) + 1)
        overrun_path = os.path.join(TEST_ROOT, "overrun.png")
        write_indexed_png_stream(
            overrun_path,
            idat=zlib.compress(b"\0" * (expected_size * 128)),
        )
        real_decompressobj = zlib.decompressobj
        limits = []

        class RecordingDecompressor:
            def __init__(self):
                self._inner = real_decompressobj()

            def decompress(self, data, max_length):
                limits.append(max_length)
                return self._inner.decompress(data, max_length)

            def flush(self, max_length):
                return self._inner.flush(max_length)

            @property
            def eof(self):
                return self._inner.eof

            @property
            def unconsumed_tail(self):
                return self._inner.unconsumed_tail

            @property
            def unused_data(self):
                return self._inner.unused_data

        with mock.patch.object(
            manifest.zlib, "decompressobj", side_effect=RecordingDecompressor
        ):
            with self.assertRaisesRegex(ValueError, "exceeds the expected length"):
                manifest._read_png(overrun_path)
        self.assertEqual(limits, [expected_size + 1])

        trailing_path = os.path.join(TEST_ROOT, "trailing-zlib.png")
        valid_rows = b"\0" * expected_size
        write_indexed_png_stream(
            trailing_path,
            idat=zlib.compress(valid_rows) + zlib.compress(b"trailing"),
        )
        with self.assertRaisesRegex(ValueError, "trailing data"):
            manifest._read_png(trailing_path)

    def test_formatted_portrait_package_reports_malformed_metadata_without_crashing(self):
        record = valid_portrait_record(TEST_ROOT)
        with open(os.path.join(REPO_ROOT, record["sources"][1]), "w", encoding="utf-8") as handle:
            handle.write("{ malformed metadata")
        with mock.patch.object(manifest, "_repo_path", side_effect=lambda path, *_: path):
            self.assert_validation_error([record], "cannot load portrait metadata")

    def test_formatted_portrait_package_reports_unreadable_metadata_without_crashing(self):
        record = valid_portrait_record(TEST_ROOT)
        metadata_path = os.path.join(REPO_ROOT, record["sources"][1])
        real_open = open

        def reject_metadata(path, *args, **kwargs):
            if os.fspath(path) == metadata_path:
                raise OSError("metadata unavailable for test")
            return real_open(path, *args, **kwargs)

        with (
            mock.patch("builtins.open", side_effect=reject_metadata),
            mock.patch.object(manifest, "_repo_path", side_effect=lambda path, *_: path),
        ):
            self.assert_validation_error([record], "cannot load portrait metadata")

    def test_formatted_portrait_package_reports_missing_metadata_field_without_crashing(self):
        record = valid_portrait_record(TEST_ROOT)
        metadata_path = os.path.join(REPO_ROOT, record["sources"][1])
        with open(metadata_path, encoding="utf-8") as handle:
            metadata = json.load(handle)
        del metadata["blinkKind"]
        metadata["alias"] = {
            "mode": "existing-components",
            "components": {
                "img": "portrait_Mystery_1_tileset",
                "imgChibi": "portrait_Mystery_1_chibi",
                "pal": "portrait_Mystery_1_palette",
                "imgMouth": "portrait_Mystery_1_mouth",
            },
        }
        with open(metadata_path, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle)
        with mock.patch.object(manifest, "_repo_path", side_effect=lambda path, *_: path):
            self.assert_validation_error([record], "portrait metadata must contain exactly")

    def test_formatted_portrait_package_requires_metadata_json(self):
        record = valid_portrait_record(TEST_ROOT)
        record["sources"][1] = record["sources"][1].replace("metadata.json", "proof.json")
        source_path = os.path.join(REPO_ROOT, record["sources"][1])
        os.rename(
            os.path.join(REPO_ROOT, record["sources"][1].replace("proof.json", "metadata.json")),
            source_path,
        )
        with mock.patch.object(manifest, "_repo_path", side_effect=lambda path, *_: path):
            self.assert_validation_error([record], "metadata.json")

    def test_sources_command_includes_portrait_registry_dependency(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.assets",
                "--item-id-cap",
                "0xCD",
                "sources",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
        )
        sources = result.stdout.splitlines()
        self.assertIn("assets/portrait_registry.json", sources)
        self.assertIn("assets/tmx/Ch2Map.tmx", sources)
        self.assertTrue(all("$(" not in source for source in sources))
        self.assertFalse(any(source.startswith("build/") for source in sources))

    def test_discovery_rejects_missing_dependency_ownership_without_conversion(self):
        with open(os.path.join(REPO_ROOT, "assets", "manifest.json"), encoding="utf-8") as handle:
            template = json.load(handle)
        cases = (
            ("tiled-tmx-map-layout", "chapterSettings"),
            ("formatted-portrait-package", "registrySource"),
            ("battle-animation-package", "classData"),
        )
        for kind_name, field in cases:
            with self.subTest(kind=kind_name, field=field):
                document = copy.deepcopy(template)
                record = next(
                    item for item in document["assets"] if item["kind"] == kind_name
                )
                del record["ownership"][field]
                source = self.write_document(document)
                stderr = io.StringIO()
                with (
                    mock.patch.object(tmx, "parse_tmx") as tmx_convert,
                    mock.patch.object(banim, "load_package") as banim_convert,
                    mock.patch.object(
                        manifest.FormattedPortraitPackageKind, "_validate_metadata"
                    ) as portrait_convert,
                    mock.patch.object(manifest, "_write_bytes_if_changed") as write_output,
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(
                        cli.main(
                            [
                                "--item-id-cap",
                                "0xCD",
                                "--manifest",
                                source,
                                "sources",
                            ]
                        ),
                        1,
                    )
                self.assertIn("ownership.{}".format(field), stderr.getvalue())
                tmx_convert.assert_not_called()
                banim_convert.assert_not_called()
                portrait_convert.assert_not_called()
                write_output.assert_not_called()

    def test_captured_discovery_matches_git_validated_rendering(self):
        source = os.path.join(REPO_ROOT, "assets", "manifest.json")
        ordinary = manifest.load_discovery(source)
        expected = manifest.render_discovery_makefile(ordinary)
        tracked = frozenset(subprocess.check_output(
            ["git", "-C", REPO_ROOT, "ls-files", "-z"],
        ).decode("utf-8").split("\0")) - {""}
        with mock.patch.object(
            manifest.subprocess, "run", side_effect=AssertionError("unexpected Git subprocess"),
        ):
            captured = manifest.load_discovery(source, tracked_sources=tracked)
            self.assertEqual(manifest.render_discovery_makefile(captured), expected)
        self.assertEqual(manifest.discovery_sources(captured), manifest.discovery_sources(ordinary))

    def test_captured_discovery_rejects_missing_source_membership(self):
        source = os.path.join(REPO_ROOT, "assets", "manifest.json")
        records = manifest.load_discovery(source)
        tracked = frozenset(manifest.discovery_sources(records))
        with mock.patch.object(
            manifest.subprocess, "run", side_effect=AssertionError("unexpected Git subprocess"),
        ):
            with self.assertRaisesRegex(GeneratedDataValidationError, "not a tracked committed source"):
                manifest.load_discovery(
                    source, tracked_sources=tracked - {"assets/portrait_registry.json"},
                )

    def test_captured_discovery_rejects_malformed_admission(self):
        source = os.path.join(REPO_ROOT, "assets", "manifest.json")
        for tracked in ("assets/portrait_registry.json", [1], {1}, {"../outside"}):
            with self.subTest(tracked=tracked):
                with self.assertRaises(GeneratedDataError):
                    manifest.load_discovery(source, tracked_sources=tracked)

    def test_captured_discovery_keeps_source_path_validation(self):
        with open(os.path.join(REPO_ROOT, "assets", "manifest.json"), encoding="utf-8") as handle:
            document = json.load(handle)
        document["assets"][0]["sources"][0] = "../outside"
        source = self.write_document(document)
        with mock.patch.object(
            manifest.subprocess, "run", side_effect=AssertionError("unexpected Git subprocess"),
        ):
            with self.assertRaisesRegex(GeneratedDataValidationError, "unsafe source path"):
                manifest.load_discovery(source, tracked_sources=frozenset())

    def test_discovery_artifact_uses_same_validation_rendering_and_logical_path(self):
        source = os.path.join(REPO_ROOT, "assets", "manifest.json")
        ordinary = manifest.load_discovery(source)
        expected = manifest.render_discovery_makefile(ordinary)
        tracked = frozenset(manifest.discovery_sources(ordinary))
        logical = "build/generated/asset-discovery/captured.mk"
        with mock.patch.object(
            manifest.subprocess, "run", side_effect=AssertionError("unexpected Git subprocess"),
        ):
            path, content = manifest.render_discovery_artifact(
                source, logical, tracked_sources=tracked,
            )
        self.assertEqual(path, logical)
        self.assertEqual(content, expected)
        self.assertEqual(len(ordinary), 3)
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, logical)))

    def test_discovery_artifact_rejects_malformed_or_escaping_outputs(self):
        source = os.path.join(REPO_ROOT, "assets", "manifest.json")
        tracked = frozenset(manifest.discovery_sources(manifest.load_discovery(source)))
        for logical in (
            "", "build", "src/forged.mk", "build/../../forged.mk",
            "/work/build/generated/asset-discovery/forged.mk", "build/bad\0.mk",
        ):
            with self.subTest(logical=logical):
                with self.assertRaises(GeneratedDataError):
                    manifest.render_discovery_artifact(
                        source, logical, tracked_sources=tracked,
                    )

    def test_discovery_artifact_requires_complete_captured_identity(self):
        source = os.path.join(REPO_ROOT, "assets", "manifest.json")
        tracked = frozenset(manifest.discovery_sources(manifest.load_discovery(source)))
        with mock.patch.object(
            manifest.subprocess, "run", side_effect=AssertionError("unexpected Git subprocess"),
        ):
            for admitted, error in (
                (None, GeneratedDataError),
                (tracked - {"assets/portrait_registry.json"}, GeneratedDataValidationError),
            ):
                with self.subTest(admitted=admitted):
                    with self.assertRaises(error):
                        manifest.render_discovery_artifact(
                            source, "build/generated/asset-discovery/captured.mk",
                            tracked_sources=admitted,
                        )

    def test_discovery_artifact_make_behavior_uses_equivalent_input_metadata(self):
        source = os.path.join(REPO_ROOT, "assets", "manifest.json")
        records = manifest.load_discovery(source)
        sources = manifest.discovery_sources(records)
        before = {path: os.stat(os.path.join(REPO_ROOT, path)).st_mtime_ns for path in sources}
        ordinary = manifest.render_discovery_makefile(records)
        _, adapted = manifest.render_discovery_artifact(
            source, "build/generated/asset-discovery/captured.mk",
            tracked_sources=frozenset(sources),
        )
        cli_output = os.path.join(TEST_ROOT, "ordinary-cli.mk")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([
                "--item-id-cap", "0xCD", "--manifest", source,
                "--discovery-makefile", cli_output, "discovery-makefile",
            ]), 0)
        with open(cli_output, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), ordinary)
        self.assertEqual(
            {path: os.stat(os.path.join(REPO_ROOT, path)).st_mtime_ns for path in sources},
            before,
        )
        self.assertEqual(adapted, ordinary)
        variables = (
            "ASSET_MANIFEST_SOURCE_DIGEST", "ASSET_TMX_INCBIN_CONSUMERS",
            "ASSET_PORTRAIT_INCBIN_CONSUMERS", "ASSET_BANIM_INCBIN_CONSUMERS",
            "ASSET_CUSTOM_SPELL_INCBIN_CONSUMERS",
        )
        makefile = os.path.join(TEST_ROOT, "consumer.mk")
        artifact = os.path.join(TEST_ROOT, "artifact.mk")
        with open(makefile, "w", encoding="utf-8") as handle:
            handle.write(
                "include artifact.mk\n.PHONY: observe\nobserve:\n"
                "\t@printf '%s\\n' " + " ".join("'$({})'".format(name) for name in variables) + "\n"
            )

        def observe(content):
            with open(artifact, "w", encoding="utf-8") as handle:
                handle.write(content)
            completed = subprocess.run(
                ["/usr/bin/make", "--no-print-directory", "-f", makefile, "observe"],
                cwd=TEST_ROOT,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                capture_output=True, text=True, check=True,
            )
            return dict(zip(variables, completed.stdout.splitlines()))

        expected = observe(ordinary)
        self.assertEqual(observe(adapted), expected)
        self.assertEqual(observe("# Nonsemantic producer comment\n" + adapted), expected)
        self.assertEqual(expected["ASSET_TMX_INCBIN_CONSUMERS"], "CH2_MAIN_MAP")
        changed = observe(adapted + "ASSET_TMX_INCBIN_CONSUMERS := WRONG_CONSUMER\n")
        self.assertNotEqual(changed, expected)

    def test_make_supports_isolated_output_override_with_portrait_incbin_consumer(self):
        result = self.run_assets_make(
            os.path.join(REPO_ROOT, "assets", "manifest.json"),
            "build/generated/assets/test-work/portrait-output-override",
            "assets-generate",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    REPO_ROOT,
                    "build",
                    "generated",
                    "assets",
                    "test-work",
                    "portrait-output-override",
                    "portrait_data.inc",
                )
            )
        )

    def test_make_allows_output_override_without_portrait_incbin_consumer(self):
        legacy = valid_record()
        legacy["id"] = "CH1_MAIN_MAP"
        legacy["kind"] = "chapter-map-layout"
        legacy["sources"] = [
            "graphics/map/layout/Ch1Map.mar",
            "graphics/map/layout/Ch1Map.json",
        ]
        legacy["options"] = {"format": "mar", "compression": "lz77"}
        legacy["ownership"].update(
            {"chapterSettingsIndex": 1, "mainLayerId": 8, "symbol": "Ch1Map"}
        )
        legacy["resources"].update({"mapWidth": 15, "mapHeight": 10})
        source = self.write_manifest([legacy])
        output_dir = "build/generated/assets/test-work/map-output-override"
        result = self.run_assets_make(source, output_dir, "assets-generate", "assets-check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            os.path.isfile(os.path.join(REPO_ROOT, output_dir, manifest.OUTPUT_MAKEFILE))
        )


    def test_formatted_portrait_package_rejects_boolean_geometry_and_alias_drift(self):
        record = valid_portrait_record(TEST_ROOT)
        source = self.write_manifest([record])
        with mock.patch.object(manifest, "_repo_path", side_effect=lambda path, *_: path):
            with open(os.path.join(REPO_ROOT, record["sources"][1]), encoding="utf-8") as handle:
                metadata = json.load(handle)
            metadata["frames"]["main"][0] = True
            with open(os.path.join(REPO_ROOT, record["sources"][1]), "w", encoding="utf-8") as handle:
                json.dump(metadata, handle)
            self.assert_validation_error([record], "frame geometry")

            metadata["frames"]["main"][0] = 0
            metadata["alias"] = {
                "mode": "existing-components",
                "components": {
                    "img": "portrait_Mystery_1_tileset",
                    "imgChibi": "portrait_Mystery_1_chibi",
                    "pal": "portrait_Mystery_1_palette",
                    "imgMouth": "portrait_NotTheRegistry_mouth",
                },
            }
            with open(os.path.join(REPO_ROOT, record["sources"][1]), "w", encoding="utf-8") as handle:
                json.dump(metadata, handle)
            self.assert_validation_error([record], "must match canonical FaceData symbol")

    def test_formatted_portrait_package_rejects_unsafe_registry_expression(self):
        record = valid_portrait_record(TEST_ROOT)
        registry_path = os.path.join(REPO_ROOT, record["ownership"]["registrySource"])
        with open(registry_path, encoding="utf-8") as handle:
            registry = json.load(handle)
        registry["entries"][0]["img"] = "portrait_Proof_tileset; injected"
        with open(registry_path, "w", encoding="utf-8") as handle:
            json.dump(registry, handle)
        self.assert_validation_error([record], "C identifiers or 0")

    def test_capacity_and_actual_ownership_conflicts_fail(self):
        capacity = valid_record()
        capacity["resources"]["mapWidth"] = 200
        capacity["resources"]["mapHeight"] = 200
        self.assert_validation_error([capacity], "exceed the 2048-byte gBmMapBuffer")

    def test_tmx_capacity_boundaries_agree_for_resources_and_payloads(self):
        fitting = valid_record()
        fitting["resources"]["mapWidth"] = 31
        fitting["resources"]["mapHeight"] = 33
        self.assertEqual(
            manifest.ChapterMapLayoutKind._map_payload_bytes(31 * 33),
            2048,
        )
        with mock.patch.object(tmx, "parse_tmx", return_value=(31, 33, [0] * (31 * 33))):
            manifest.load_and_validate(self.write_manifest([fitting]))

        overflowing = valid_record()
        overflowing["resources"]["mapWidth"] = 32
        overflowing["resources"]["mapHeight"] = 32
        self.assertEqual(
            manifest.ChapterMapLayoutKind._map_payload_bytes(32 * 32),
            2050,
        )
        with mock.patch.object(tmx, "parse_tmx") as parse_tmx:
            self.assert_validation_error(
                [overflowing],
                "map dimensions 32x32 exceed the 2048-byte gBmMapBuffer",
            )
        parse_tmx.assert_not_called()

    def test_actual_ownership_conflict_fails(self):
        ownership = valid_record()
        ownership["ownership"]["mainLayerId"] = 12
        self.assert_validation_error([ownership], "does not select mainLayerId 12")

    def test_tmx_adapter_preserves_chapter_two_bytes_and_runtime_wiring(self):
        source = os.path.join(REPO_ROOT, "assets", "tmx", "Ch2Map.tmx")
        width, height, values = tmx.parse_tmx(source)
        mar = tmx.render_mar(values)
        map_payload = bytes((width, height)) + b"".join(
            value.to_bytes(2, byteorder="little") for value in values
        )
        self.assertEqual((width, height, len(values)), (15, 15, 225))
        self.assertEqual(
            hashlib.sha256(mar).hexdigest(),
            "fde487693e8b2f0c0696329c859511ec6fdcde7dfaa0b0ddeb3eea6e25578b56",
        )
        self.assertEqual(
            hashlib.sha256(map_payload).hexdigest(),
            "ed7696f03cc64202ed40fea773cf241317a30722dcc40cddc8ff1138c2f64447",
        )
        with open(
            os.path.join(REPO_ROOT, "src", "data", "const_data_chapter_maps.c"),
            encoding="utf-8",
        ) as handle:
            self.assertIn(
                'Ch2Map[] = INCBIN_U8("build/generated/assets/tmx/CH2_MAIN_MAP.bin.lz")',
                handle.read(),
            )
        with open(os.path.join(REPO_ROOT, "src", "chapterdata.c"), encoding="utf-8") as handle:
            self.assertIn(
                "gChapterDataAssetTable[GetROMChapterStruct(chIndex)->map.mainLayerId]",
                handle.read(),
            )

    def test_tmx_metadata_change_rebuilds_bin_lz_and_incbin_consumer(self):
        source = self.write_manifest([valid_record()])
        out_dir = os.path.join(TEST_ROOT, "incremental")
        manifest.generate(source, out_dir)
        mar_path = os.path.join(out_dir, "tmx", "CH2_MAIN_MAP.mar")
        metadata_path = os.path.join(out_dir, "tmx", "CH2_MAIN_MAP.json")
        bin_path = os.path.join(out_dir, "tmx", "CH2_MAIN_MAP.bin")
        lz_path = bin_path + ".lz"
        consumer_path = os.path.join(TEST_ROOT, "incbin.o")
        consumer_target = os.path.relpath(consumer_path, REPO_ROOT)
        makefile_path = os.path.join(TEST_ROOT, "incremental.mk")
        with open(makefile_path, "w", encoding="utf-8") as handle:
            handle.write(
                "ASSET_OUTPUT_DIR := {out_dir}\n"
                "MODERN_OUTPUT_DIR := {out_dir}/modern\n"
                "MARTOMAP := {python} {mar_to_map}\n"
                "GBAGFX := {gbagfx}\n"
                "include {fragment}\n"
                "\n"
                "%.bin: %.mar\n"
                "\t$(MARTOMAP) $< $@\n"
                "%.bin.lz: %.bin\n"
                "\t$(GBAGFX) $< $@\n"
                "{consumer}: $(ASSET_OUTPUT_DIR)/tmx/CH2_MAIN_MAP.bin.lz\n"
                "\tcp $< $@\n".format(
                    out_dir=out_dir,
                    python=sys.executable,
                    mar_to_map=os.path.join(REPO_ROOT, "scripts", "mar_to_map.py"),
                    gbagfx=os.path.join(REPO_ROOT, "tools", "gbagfx", "gbagfx"),
                    fragment=os.path.join(out_dir, manifest.OUTPUT_MAKEFILE),
                    consumer=consumer_target,
                )
            )

        def build_consumer():
            return subprocess.run(
                ["make", "-f", makefile_path, consumer_target],
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        first = build_consumer()
        self.assertEqual(first.returncode, 0, first.stderr)
        with open(mar_path, "rb") as handle:
            mar_before = handle.read()
        with open(bin_path, "rb") as handle:
            bin_before = handle.read()
        with open(lz_path, "rb") as handle:
            lz_before = handle.read()
        with open(consumer_path, "rb") as handle:
            consumer_before = handle.read()
        self.assertEqual(bin_before[:2], b"\x0f\x0f")

        with open(metadata_path, "wb") as handle:
            handle.write(tmx.render_metadata("Ch2Map", 9, 25))
        modified_at = max(time.time_ns(), os.stat(bin_path).st_mtime_ns + 1)
        os.utime(metadata_path, ns=(modified_at, modified_at))

        second = build_consumer()
        self.assertEqual(second.returncode, 0, second.stderr)
        with open(mar_path, "rb") as handle:
            self.assertEqual(handle.read(), mar_before)
        with open(bin_path, "rb") as handle:
            bin_after = handle.read()
        with open(lz_path, "rb") as handle:
            lz_after = handle.read()
        with open(consumer_path, "rb") as handle:
            consumer_after = handle.read()
        self.assertEqual(bin_after[:2], b"\x09\x19")
        self.assertNotEqual(bin_after, bin_before)
        self.assertNotEqual(lz_after, lz_before)
        self.assertNotEqual(consumer_after, consumer_before)
        self.assertEqual(consumer_after, lz_after)

    def test_tmx_synthetic_positive_and_adversarial_fixtures(self):
        width, height, values = tmx.parse_tmx(
            os.path.join(TMX_FIXTURE_ROOT, "valid.tmx")
        )
        self.assertEqual((width, height, values), (2, 2, [0, 1, 2, 3]))
        self.assertEqual(
            tmx.render_mar(values),
            b"\x00\x00\x08\x00\x10\x00\x18\x00",
        )
        for name in ("external_tileset.tmx", "flipped_gid.tmx"):
            with self.subTest(name=name):
                with self.assertRaises(tmx.TmxError):
                    tmx.parse_tmx(os.path.join(TMX_FIXTURE_ROOT, name))

    def test_tmx_safe_subset_rejects_unsupported_inputs(self):
        source_path = os.path.join(REPO_ROOT, "assets", "tmx", "Ch2Map.tmx")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
        cases = {
            "external_tileset": source.replace(
                'name="fe8-metatiles-16px-4096"', 'source="../tiles.tsx"', 1
            ),
            "wrong_tile_size": source.replace('tilewidth="16"', 'tilewidth="8"', 1),
            "base64": source.replace('encoding="csv"', 'encoding="base64"'),
            "flipped_gid": source.replace("521,521", "2147484169,521", 1),
            "zero_gid": source.replace("521,521", "0,521", 1),
            "group": source.replace("</map>", "<group id=\"2\" name=\"bad\"/></map>"),
            "entity": source.replace("<map ", "<!DOCTYPE map [<!ENTITY x \"y\">]><map "),
            "chunk": source.replace("<data encoding=\"csv\">", "<data encoding=\"csv\"><chunk>"),
            "namespace": source.replace("<map ", "<map xmlns=\"urn:unsupported\" ", 1),
            "wrong_orientation": source.replace('orientation="orthogonal"', 'orientation="isometric"'),
            "wrong_render_order": source.replace('renderorder="right-down"', 'renderorder="right-up"'),
            "infinite": source.replace('infinite="0"', 'infinite="1"'),
            "large_dimension": source.replace('width="15"', 'width="256"', 1),
            "second_tileset": source.replace("</map>", "<tileset firstgid=\"1\"/></map>"),
            "tileset_image": source.replace("/>", "><image source=\"tiles.png\"/></tileset>", 1),
            "bad_firstgid": source.replace('firstgid="1"', 'firstgid="2"'),
            "bad_tile_count": source.replace('tilecount="4096"', 'tilecount="4095"'),
            "bad_columns": source.replace('columns="64"', 'columns="63"'),
            "wrong_layer_size": source.replace('width="15" height="15">', 'width="14" height="15">', 1),
            "second_layer": source.replace("</map>", "<layer id=\"2\" name=\"Other\" width=\"15\" height=\"15\"/>"
                                          "</map>"),
            "object_layer": source.replace("</map>", "<objectgroup id=\"2\" name=\"Objects\"/></map>"),
            "image_layer": source.replace("</map>", "<imagelayer id=\"2\" name=\"Image\"/></map>"),
            "property": source.replace("</map>", "<properties><property name=\"x\"/></properties></map>"),
            "compression": source.replace('encoding="csv"', 'encoding="csv" compression="zlib"'),
            "wrong_count": source.replace("521,521", "521", 1),
            "signed_gid": source.replace("521,521", "-1,521", 1),
            "plus_gid": source.replace("521,521", "+1,521", 1),
            "overflow_gid": source.replace("521,521", "99999999999,521", 1),
            "unicode_whitespace": source.replace("521,521", "521,\u00a0521", 1),
        }
        for name, document in cases.items():
            with self.subTest(name=name):
                path = os.path.join(TEST_ROOT, name + ".tmx")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(document)
                with self.assertRaises(tmx.TmxError):
                    tmx.parse_tmx(path)

    def test_tmx_reports_non_map_roots_before_map_attribute_diagnostics(self):
        path = os.path.join(TEST_ROOT, "non-map-root.tmx")
        with open(path, "wb") as handle:
            handle.write(tmx.TMX_XML_DECLARATION + b"\n<tileset/>")
        with self.assertRaisesRegex(tmx.TmxError, "^root element must be map$"):
            tmx.parse_tmx(path)

    def test_tmx_rejects_oversized_source_before_xml_parsing(self):
        path = os.path.join(TEST_ROOT, "oversized.tmx")
        with open(path, "wb") as handle:
            handle.write(tmx.TMX_XML_DECLARATION)
            handle.write(b" " * (tmx.MAX_TMX_BYTES + 1))
        with self.assertRaisesRegex(tmx.TmxError, "source limit"):
            tmx.parse_tmx(path)

    def test_check_detects_missing_stale_and_orphan_output(self):
        source = self.write_manifest([valid_record()])
        out_dir = os.path.join(TEST_ROOT, "out")
        with self.assertRaises(GeneratedDataValidationError) as raised:
            manifest.check(source, out_dir)
        self.assertIn("missing generated output", str(raised.exception))

        manifest.generate(source, out_dir)
        with open(os.path.join(out_dir, manifest.OUTPUT_MAKEFILE), "a", encoding="utf-8") as handle:
            handle.write("# stale\n")
        with self.assertRaises(GeneratedDataValidationError) as raised:
            manifest.check(source, out_dir)
        self.assertIn("stale generated output", str(raised.exception))

        manifest.generate(source, out_dir)
        with open(os.path.join(out_dir, "orphan.txt"), "w", encoding="utf-8") as handle:
            handle.write("orphan\n")
        with self.assertRaises(GeneratedDataValidationError) as raised:
            manifest.check(source, out_dir)
        self.assertIn("orphan generated output", str(raised.exception))

    def test_generate_prunes_retired_chapter_map_include(self):
        source = self.write_manifest([valid_record()])
        out_dir = os.path.join(TEST_ROOT, "out")
        manifest.generate(source, out_dir)
        retired = os.path.join(out_dir, "ch2_main_map.inc")
        with open(retired, "w", encoding="utf-8") as handle:
            handle.write("retired generated include\n")
        manifest.generate(source, out_dir)
        self.assertFalse(os.path.exists(retired))
        manifest.check(source, out_dir)

    def test_asset_makefile_tracks_declared_manifest_sources(self):
        with open(os.path.join(REPO_ROOT, "assets.mk"), encoding="utf-8") as handle:
            asset_makefile = handle.read()
        self.assertNotIn("ASSET_MANIFEST_SOURCES := $(shell", asset_makefile)
        rule = next(
            line
            for line in asset_makefile.splitlines()
            if line.startswith("$(ASSET_OUTPUT_MK):")
        )
        prerequisites = set(rule.split(":", 1)[1].split())
        self.assertTrue(
            {
                "$(ASSET_SELECTION_STAMP)",
                "$(ASSET_MANIFEST_SOURCE_STAMP)",
                "$(ASSET_MANIFEST)",
                "$(ASSET_TOOL_INPUTS)",
            }.issubset(prerequisites)
        )
        legacy = valid_record()
        legacy["id"] = "CH1_MAIN_MAP"
        legacy["kind"] = "chapter-map-layout"
        legacy["sources"] = [
            "graphics/map/layout/Ch1Map.mar",
            "graphics/map/layout/Ch1Map.json",
        ]
        legacy["options"] = {"format": "mar", "compression": "lz77"}
        legacy["ownership"].update(
            {"chapterSettingsIndex": 1, "mainLayerId": 8, "symbol": "Ch1Map"}
        )
        legacy["resources"].update({"mapWidth": 15, "mapHeight": 10})
        source = self.write_manifest([legacy])
        output_dir = os.path.join(TEST_ROOT, "selection-stamp")
        stamp = output_dir + ".manifest-selection"

        def update_stamp(enabled):
            return subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-f",
                    "assets.mk",
                    stamp,
                    "PYTHON={}".format(sys.executable),
                    "ASSET_MANIFEST={}".format(source),
                    "ASSET_OUTPUT_DIR={}".format(output_dir),
                    "EXPANSION_CUSTOM_SPELL_EFFECTS={}".format(enabled),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        first = update_stamp(0)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        with open(stamp, encoding="utf-8") as handle:
            self.assertIn("custom_spell_effects=0", handle.read())
        second = update_stamp(1)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        with open(stamp, encoding="utf-8") as handle:
            self.assertIn("custom_spell_effects=1", handle.read())

    def assert_asset_manifest_would_regenerate(
        self, manifest_path, output_dir, dependencies
    ):
        manifest.generate(manifest_path, output_dir)
        target = os.path.relpath(
            os.path.join(output_dir, manifest.OUTPUT_MAKEFILE), REPO_ROOT
        )
        target_path = os.path.join(output_dir, manifest.OUTPUT_MAKEFILE)
        manifest_arg = os.path.relpath(manifest_path, REPO_ROOT)
        output_arg = os.path.relpath(output_dir, REPO_ROOT)
        driver_path = os.path.join(TEST_ROOT, "asset-manifest-incremental.mk")
        with open(driver_path, "w", encoding="utf-8") as handle:
            handle.write(
                "PYTHON := {python}\n"
                "ASSET_MANIFEST := {manifest}\n"
                "ASSET_OUTPUT_DIR := {output}\n"
                "include assets.mk\n"
                ".PHONY: verify\n"
                "verify: $(ASSET_OUTPUT_MK)\n".format(
                    python=sys.executable,
                    manifest=manifest_arg,
                    output=output_arg,
                )
            )

        for dependency in dependencies:
            dependency_path = os.path.join(REPO_ROOT, dependency)
            original = os.stat(dependency_path)
            target_original = os.stat(target_path)
            updated_mtime = max(
                time.time_ns(),
                target_original.st_mtime_ns + 2_000_000_000,
            )
            os.utime(
                dependency_path,
                ns=(original.st_atime_ns, updated_mtime),
            )
            os.utime(
                target_path,
                ns=(target_original.st_atime_ns, updated_mtime - 1_000_000_000),
            )
            try:
                result = subprocess.run(
                    [
                        "make",
                        "-n",
                        "-f",
                        driver_path,
                        "verify",
                    ],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("scripts.assets", result.stdout)
            finally:
                os.utime(
                    dependency_path,
                    ns=(original.st_atime_ns, original.st_mtime_ns),
                )
                os.utime(
                    target_path,
                    ns=(target_original.st_atime_ns, target_original.st_mtime_ns),
                )

    def test_immutable_validation_sources_trigger_asset_manifest_regeneration(self):
        chapter_dependencies = (
            "src/data/chapter_settings.json",
            "src/data/data_8B363C.c",
        )
        legacy = valid_record()
        legacy["id"] = "CH1_MAIN_MAP"
        legacy["kind"] = "chapter-map-layout"
        legacy["sources"] = [
            "graphics/map/layout/Ch1Map.mar",
            "graphics/map/layout/Ch1Map.json",
        ]
        legacy["options"] = {"format": "mar", "compression": "lz77"}
        legacy["ownership"].update(
            {"chapterSettingsIndex": 1, "mainLayerId": 8, "symbol": "Ch1Map"}
        )
        legacy["resources"].update({"mapWidth": 15, "mapHeight": 10})
        legacy_manifest = self.write_manifest([legacy])
        legacy_record = manifest.load_and_validate(legacy_manifest)[0]
        self.assertEqual(
            manifest.ChapterMapLayoutKind().source_dependencies(legacy_record),
            chapter_dependencies,
        )
        self.assert_asset_manifest_would_regenerate(
            legacy_manifest,
            os.path.join(TEST_ROOT, "legacy-incremental"),
            chapter_dependencies,
        )

        records = manifest.load_and_validate(
            os.path.join(REPO_ROOT, "assets", "manifest.json")
        )
        tiled = next(
            record for record in records if record.kind == manifest.TiledTmxMapLayoutKind.name
        )
        tiled_dependencies = chapter_dependencies + (
            "src/data/const_data_chapter_maps.c",
        )
        self.assertEqual(
            manifest.TiledTmxMapLayoutKind().source_dependencies(tiled),
            tiled_dependencies,
        )
        self.assert_asset_manifest_would_regenerate(
            os.path.join(REPO_ROOT, "assets", "manifest.json"),
            os.path.join(REPO_ROOT, "build", "generated", "assets"),
            tiled_dependencies,
        )

    def test_asset_makefile_supports_isolated_output_override_for_default_manifest(self):
        isolated = subprocess.run(
            [
                "make",
                "ASSET_OUTPUT_DIR=build/generated/assets/test-work/alternate",
                "assets-generate",
                "assets-check",
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(isolated.returncode, 0, isolated.stdout + isolated.stderr)
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    REPO_ROOT,
                    "build",
                    "generated",
                    "assets",
                    "test-work",
                    "alternate",
                    manifest.OUTPUT_MAKEFILE,
                )
            )
        )

        legacy = valid_record()
        legacy["id"] = "CH1_MAIN_MAP"
        legacy["kind"] = "chapter-map-layout"
        legacy["sources"] = [
            "graphics/map/layout/Ch1Map.mar",
            "graphics/map/layout/Ch1Map.json",
        ]
        legacy["options"] = {"format": "mar", "compression": "lz77"}
        legacy["ownership"].update(
            {"chapterSettingsIndex": 1, "mainLayerId": 8, "symbol": "Ch1Map"}
        )
        legacy["resources"].update({"mapWidth": 15, "mapHeight": 10})
        legacy_manifest = self.write_manifest([legacy])
        allowed = subprocess.run(
            [
                "make",
                "ASSET_MANIFEST={}".format(os.path.relpath(legacy_manifest, REPO_ROOT)),
                "ASSET_OUTPUT_DIR=build/generated/assets/test-work/legacy-override",
                "assets-generate",
                "assets-check",
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_make_supports_isolated_output_override_with_banim_incbin_consumer(self):
        with open(os.path.join(REPO_ROOT, "assets", "manifest.json"), encoding="utf-8") as handle:
            document = json.load(handle)
        document["assets"] = [
            record for record in document["assets"] if record["kind"] == "battle-animation-package"
        ]
        source = self.write_document(document)
        result = self.run_assets_make(
            source,
            "build/generated/assets/test-work/banim-output-override",
            "assets-generate",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    REPO_ROOT,
                    "build",
                    "generated",
                    "assets",
                    "test-work",
                    "banim-output-override",
                    "banim",
                    "banim_data_entries.inc",
                )
            )
        )

    def test_check_detects_orphans_through_a_relative_output_path(self):
        source = self.write_manifest([valid_record()])
        out_dir = os.path.join(TEST_ROOT, "out")
        manifest.generate(source, out_dir)
        with open(os.path.join(out_dir, "orphan.txt"), "w", encoding="utf-8") as handle:
            handle.write("orphan\n")
        relative_out_dir = os.path.relpath(out_dir, REPO_ROOT)
        with self.assertRaises(GeneratedDataValidationError) as raised:
            manifest.check(source, relative_out_dir)
        self.assertIn("orphan generated output", str(raised.exception))

    def test_failed_generation_keeps_existing_outputs_unchanged(self):
        source = self.write_manifest([valid_record()])
        out_dir = os.path.join(TEST_ROOT, "out")
        manifest.generate(source, out_dir)
        before = self.read_outputs(out_dir)
        broken = valid_record()
        broken["sources"][0] = "../outside.mar"
        invalid_source = self.write_manifest([broken])
        with self.assertRaises(GeneratedDataValidationError):
            manifest.generate(invalid_source, out_dir)
        after = self.read_outputs(out_dir)
        self.assertEqual(before, after)

    def test_output_replacement_is_atomic_and_cleans_temporary_file(self):
        out_dir = os.path.join(TEST_ROOT, "out")
        path = os.path.join(out_dir, manifest.OUTPUT_MAKEFILE)
        os.makedirs(out_dir)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("previous\n")
        with mock.patch.object(manifest.os, "replace", side_effect=OSError("injected failure")):
            with self.assertRaises(OSError):
                manifest._write_if_changed(path, "replacement\n")
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "previous\n")
        self.assertEqual(
            [name for name in os.listdir(out_dir) if name.startswith(".asset-manifest-")],
            [],
        )

    def test_parallel_custom_output_prune_preserves_active_atomic_writer(self):
        out_dir = os.path.join(TEST_ROOT, "parallel-custom-spell")
        custom_spell_dir = os.path.join(out_dir, "custom_spell")
        path = os.path.join(custom_spell_dir, "custom_spell_effect_data.inc")
        os.makedirs(custom_spell_dir)

        writer_ready = threading.Event()
        prune_finished = threading.Event()
        writer_failures = []
        active_temporary_paths = []
        real_replace = manifest.os.replace

        def replace_after_concurrent_prune(source, destination):
            if destination == path:
                active_temporary_paths.append(source)
                writer_ready.set()
                if not prune_finished.wait(timeout=5):
                    raise RuntimeError("concurrent prune did not complete")
            return real_replace(source, destination)

        def write_output():
            try:
                manifest._write_bytes_if_changed(path, b"generated binding\n")
            except Exception as error:
                writer_failures.append(error)

        def prune_custom_outputs():
            if not writer_ready.wait(timeout=5):
                writer_failures.append(RuntimeError("atomic writer did not create a temporary file"))
                prune_finished.set()
                return
            manifest._prune_obsolete_custom_spell_outputs(out_dir, {path})
            prune_finished.set()

        with mock.patch.object(manifest.os, "replace", side_effect=replace_after_concurrent_prune):
            writer = threading.Thread(target=write_output)
            pruner = threading.Thread(target=prune_custom_outputs)
            writer.start()
            pruner.start()
            writer.join(timeout=5)
            pruner.join(timeout=5)

        self.assertFalse(writer.is_alive())
        self.assertFalse(pruner.is_alive())
        self.assertEqual(writer_failures, [])
        self.assertEqual(len(active_temporary_paths), 1)
        self.assertFalse(os.path.exists(active_temporary_paths[0]))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"generated binding\n")

    def test_cli_rejects_output_outside_ignored_generated_root(self):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.assets", "--out-dir", "assets", "clean"],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("must stay under", result.stderr)

    def test_output_directory_cannot_target_shared_generated_root(self):
        with self.assertRaises(GeneratedDataError):
            manifest.safe_output_dir(os.path.join(REPO_ROOT, "build", "generated"))

    def test_cli_rejects_symlinked_output_directory(self):
        link_path = os.path.join(TEST_ROOT, "linked-output")
        os.symlink(TEST_ROOT, link_path)
        self.addCleanup(lambda: os.path.lexists(link_path) and os.unlink(link_path))
        with self.assertRaises(GeneratedDataError):
            manifest_path = os.path.relpath(link_path, REPO_ROOT)
            manifest.safe_output_dir(manifest_path)

    def test_generate_and_check_reject_descendant_output_symlinks(self):
        source = self.write_manifest([valid_record()])
        out_dir = os.path.join(TEST_ROOT, "out")
        os.makedirs(out_dir)
        os.symlink(TEST_ROOT, os.path.join(out_dir, "tmx"))
        with self.assertRaisesRegex(GeneratedDataError, "symbolic link"):
            manifest.generate(source, out_dir)
        with self.assertRaisesRegex(GeneratedDataError, "symbolic link"):
            manifest.check(source, out_dir)

    def test_rendered_prerequisites_include_transitive_dependencies(self):
        dependent = valid_record()
        direct = valid_record()
        transitive = valid_record()
        dependent["id"] = "DEPENDENT"
        direct["id"] = "DIRECT"
        transitive["id"] = "TRANSITIVE"
        dependent["sources"] = ["dependent.mar"]
        direct["sources"] = ["direct.mar"]
        transitive["sources"] = ["transitive.mar"]
        dependent["dependsOn"] = ["DIRECT"]
        direct["dependsOn"] = ["TRANSITIVE"]
        records = manifest.load_manifest(
            self.write_document({"schemaVersion": manifest.SCHEMA_VERSION, "assets": [
                dependent, direct, transitive,
            ]})
        )
        rendered = manifest.render_makefile(records)
        self.assertIn(
            "src/data/data_8B363C.o: $(ASSET_MANIFEST_SOURCE_STAMP)\n",
            rendered,
        )

    @staticmethod
    def read_outputs(out_dir):
        outputs = {}
        for name in manifest.OUTPUT_NAMES:
            with open(os.path.join(out_dir, name), encoding="utf-8") as handle:
                outputs[name] = handle.read()
        return outputs


class BattleAnimationPackageTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(TEST_ROOT):
            shutil.rmtree(TEST_ROOT)
        os.makedirs(TEST_ROOT)

    def tearDown(self):
        if os.path.exists(TEST_ROOT):
            shutil.rmtree(TEST_ROOT)

    def test_real_package_generates_runtime_symbols_and_payloads(self):
        records = manifest.load_and_validate(os.path.join(REPO_ROOT, "assets", "manifest.json"))
        output = os.path.join(TEST_ROOT, "out")
        manifest.generate(os.path.join(REPO_ROOT, "assets", "manifest.json"), output)
        with open(os.path.join(output, "banim", "banim_data_entries.inc"), encoding="utf-8") as handle:
            entry = handle.read()
        with open(os.path.join(output, "banim", "banim_defs.inc"), encoding="utf-8") as handle:
            definition = handle.read()
        self.assertIn('{"lorm_sp1", &banim_package_lorm_sp1_proof_modes_bin', entry)
        self.assertIn("BanimPackage_LORM_SP1_PROOF", definition)
        self.assertIn(".wtype = 0x100 | ITYPE_DARK", definition)
        with open(
            os.path.join(output, "banim", "banim_package_lorm_sp1_proof_motion.s"),
            encoding="utf-8",
        ) as handle:
            motion = handle.read()
        self.assertIn("banim_code_sound_sword_swing_short", motion)
        self.assertNotIn("banim_lorm_sp1_motion", motion)
        self.assertEqual(
            os.path.getsize(
                os.path.join(output, "banim", "banim_package_lorm_sp1_proof_idle.4bpp")
            ),
            8192,
        )
        self.assertEqual(
            os.path.getsize(
                os.path.join(output, "banim", "banim_package_lorm_sp1_proof_modes.bin")
            ),
            96,
        )
        with open(os.path.join(output, "banim", "linker_script_banim.txt"), encoding="utf-8") as handle:
            linker = handle.read()
        self.assertIn("banim_package_lorm_sp1_proof_motion.o|.data.script>lz", linker)
        self.assertIn("banim_package_lorm_sp1_proof_palette.pal>lz", linker)
        self.assertEqual(len([record for record in records if record.kind == "battle-animation-package"]), 1)
        manifest.check(os.path.join(REPO_ROOT, "assets", "manifest.json"), output)

    def test_png_contract_and_script_commands_fail_closed(self):
        png = os.path.join(REPO_ROOT, "graphics", "banim", "banim_lorm_sp1_sheet_0.png")
        self.assertEqual(banim.read_indexed_png(png)["tiles"], 256)
        corrupt_png = os.path.join(TEST_ROOT, "invalid.png")
        with open(png, "rb") as source, open(corrupt_png, "wb") as destination:
            data = bytearray(source.read())
            data[29] ^= 1
            destination.write(data)
        with self.assertRaisesRegex(ValueError, "invalid PNG chunk CRC"):
            banim.read_indexed_png(corrupt_png)

        script = os.path.join(TEST_ROOT, "invalid.txt")
        with open(script, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("BANIM 1\nmode normal\nframe 1 idle\ncommand call_spell\nend\n")
        with self.assertRaisesRegex(ValueError, "unsupported vanilla command"):
            banim.parse_script(script, {"idle": "fixture.png"})

        with open(script, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("BANIM 1\nmode normal\nframe 1 idle\nend\n")
        with self.assertRaisesRegex(ValueError, "must define every v1 mode"):
            banim.parse_script(script, {"idle": "fixture.png"})

        with open(script, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "BANIM 1\nmode normal\nframe 256 idle\nend\n"
                "mode critical\nwait 1\nend\nmode ranged\nwait 1\nend\n"
                "mode dodge\nwait 1\nend\nmode standing\nwait 1\nend\n"
            )
        with self.assertRaisesRegex(ValueError, "within 1..255"):
            banim.parse_script(script, {"idle": "fixture.png"})

        with open(script, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "BANIM 1\nmode normal\nwait 1\ncommand start_attack_1\nloop 1\nend\n"
                "mode critical\nwait 1\nend\nmode ranged\nwait 1\nend\n"
                "mode dodge\nwait 1\nend\nmode standing\nwait 1\nend\n"
            )
        with self.assertRaisesRegex(ValueError, "loop without a preceding timed group"):
            banim.parse_script(script, {"idle": "fixture.png"})

        with open(script, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "BANIM 1\nmode normal\nframe 1 idle\nsound unknown\nend\n"
                "mode critical\nwait 1\nend\nmode ranged\nwait 1\nend\n"
                "mode dodge\nwait 1\nend\nmode standing\nwait 1\nend\n"
            )
        with self.assertRaisesRegex(ValueError, "unsupported sound command"):
            banim.parse_script(script, {"idle": "fixture.png"})

    @staticmethod
    def write_indexed_png(path, alpha, width=32, height=32, palette=b"\x00\x00\x00\xff\xff\xff"):
        def chunk(name, data):
            return (
                struct.pack(">I", len(data))
                + name
                + data
                + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
            )

        pixels = b"".join(b"\x00" + b"\x00" * (width // 2) for _ in range(height))
        payload = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 4, 3, 0, 0, 0))
            + chunk(b"PLTE", palette)
            + chunk(b"tRNS", alpha)
            + chunk(b"IDAT", zlib.compress(pixels))
            + chunk(b"IEND", b"")
        )
        with open(path, "wb") as handle:
            handle.write(payload)

    def test_png_transparency_is_binary_and_has_one_transparent_index(self):
        valid = os.path.join(TEST_ROOT, "binary-alpha.png")
        self.write_indexed_png(valid, b"\x00\xff")
        self.assertEqual(banim.read_indexed_png(valid)["colors"], 2)

        partial = os.path.join(TEST_ROOT, "partial-alpha.png")
        self.write_indexed_png(partial, b"\x00\x80")
        with self.assertRaisesRegex(ValueError, "only palette index 0 transparent"):
            banim.read_indexed_png(partial)

        opaque = os.path.join(TEST_ROOT, "opaque-alpha.png")
        self.write_indexed_png(opaque, b"\xff\xff")
        with self.assertRaisesRegex(ValueError, "only palette index 0 transparent"):
            banim.read_indexed_png(opaque)

        wrong_index = os.path.join(TEST_ROOT, "wrong-index.png")
        self.write_indexed_png(wrong_index, b"\xff\x00")
        with self.assertRaisesRegex(ValueError, "only palette index 0 transparent"):
            banim.read_indexed_png(wrong_index)

        non_block = os.path.join(TEST_ROOT, "forty-pixels.png")
        self.write_indexed_png(non_block, b"\x00\xff", width=40, height=32)
        with self.assertRaisesRegex(ValueError, "multiples of 32"):
            banim.read_indexed_png(non_block)

    def test_script_diagnostics_preserve_original_source_line_numbers(self):
        script = os.path.join(TEST_ROOT, "line-numbers.txt")
        with open(script, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "# comment before the header\n\nBANIM 1\n\nmode normal\nframe 1 idle\nend\n"
                "# diagnostics must retain this line\ninvalid_command\n"
            )
        with self.assertRaisesRegex(ValueError, r"line-numbers\.txt:9 has unknown command"):
            banim.parse_script(script, {"idle": "fixture.png"})

    def test_manifest_ids_remain_collision_free_generated_symbols(self):
        package = banim.load_package(
            REPO_ROOT,
            "assets/banim/lorm_sp1/package.json",
            "assets/banim/lorm_sp1/script.txt",
            {
                "assets/banim/lorm_sp1/package.json",
                "assets/banim/lorm_sp1/script.txt",
                "graphics/banim/banim_lorm_sp1_sheet_0.png",
            },
        )
        records = [
            SimpleNamespace(
                id=record_id,
                kind=manifest.BattleAnimationPackageKind.name,
                banim_package=copy.deepcopy(package),
            )
            for record_id in ("A_B", "A__B")
        ]
        for record in records:
            record.banim_package.data["id"] = record.id
        entries = manifest.banim_expected_outputs(records, TEST_ROOT)[
            os.path.join(TEST_ROOT, "banim", "banim_data_entries.inc")
        ]
        self.assertIn("banim_package_a_b_modes_bin", entries)
        self.assertIn("banim_package_a__b_modes_bin", entries)

    def test_banim_ownership_and_resource_conflicts_fail(self):
        with open(os.path.join(REPO_ROOT, "assets", "manifest.json"), encoding="utf-8") as handle:
            document = json.load(handle)
        proof = next(record for record in document["assets"] if record["kind"] == "battle-animation-package")
        duplicate = copy.deepcopy(proof)
        duplicate["id"] = "LORM_SP1_DUPLICATE"
        document["assets"].append(duplicate)
        path = os.path.join(TEST_ROOT, "manifest.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        package = banim.load_package(
            REPO_ROOT,
            proof["sources"][0],
            proof["sources"][1],
            set(proof["sources"]),
        )
        package.data = copy.deepcopy(package.data)
        duplicate_package = copy.deepcopy(package)
        duplicate_package.data["id"] = duplicate["id"]
        with mock.patch.object(banim, "load_package", side_effect=[package, duplicate_package]):
            with self.assertRaises(GeneratedDataValidationError) as raised:
                manifest.load_and_validate(path)
        self.assertIn("ownership conflict", str(raised.exception))

        proof["resources"]["oamEntries"] = 129
        document["assets"] = [record for record in document["assets"] if record["id"] != "LORM_SP1_DUPLICATE"]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with self.assertRaises(GeneratedDataValidationError) as raised:
            manifest.load_and_validate(path)
        self.assertIn("resources.oamEntries", str(raised.exception))

    def test_generated_runtime_budgets_fail_closed(self):
        with open(os.path.join(REPO_ROOT, "assets", "manifest.json"), encoding="utf-8") as handle:
            document = json.load(handle)
        proof = next(record for record in document["assets"] if record["kind"] == "battle-animation-package")
        path = os.path.join(TEST_ROOT, "manifest.json")

        proof["resources"]["oamEntries"] = 1
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with self.assertRaises(GeneratedDataValidationError) as raised:
            manifest.load_and_validate(path)
        self.assertIn("generated OAM count", str(raised.exception))

        proof["resources"]["oamEntries"] = 32
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        package = banim.load_package(
            REPO_ROOT,
            proof["sources"][0],
            proof["sources"][1],
            set(proof["sources"]),
        )
        package.modes["critical"][0] = ("frame", 1, "idle", "left")
        with mock.patch.object(banim, "load_package", return_value=package):
            with self.assertRaises(GeneratedDataValidationError) as raised:
                manifest.load_and_validate(path)
        self.assertIn("generated OAM count", str(raised.exception))

        proof["resources"]["romBytes"] = 1
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with self.assertRaises(GeneratedDataValidationError) as raised:
            manifest.load_and_validate(path)
        self.assertIn("generated runtime data", str(raised.exception))

    def test_runtime_test_declarations_are_generated_from_package_data(self):
        records = manifest.load_and_validate(os.path.join(REPO_ROOT, "assets", "manifest.json"))
        output = os.path.join(TEST_ROOT, "out")
        manifest.generate(os.path.join(REPO_ROOT, "assets", "manifest.json"), output)
        with open(
            os.path.join(output, "banim", "banim_runtime_test_defs.h"), encoding="utf-8"
        ) as handle:
            definitions = handle.read()
        package = next(record.banim_package for record in records if record.id == "LORM_SP1_PROOF")
        self.assertIn("#define BANIM_PACKAGE_LORM_SP1_PROOF_MODE_COUNT 5", definitions)
        self.assertIn("#define BANIM_PACKAGE_LORM_SP1_PROOF_SOUND_OPCODE 0x85000022", definitions)
        self.assertIn(
            "#define BANIM_PACKAGE_LORM_SP1_PROOF_TOTAL_DURATION {}".format(
                sum(package.mode_durations.values())
            ),
            definitions,
        )

    def test_identical_frames_share_one_generated_sheet(self):
        package = banim.load_package(
            REPO_ROOT,
            "assets/banim/lorm_sp1/package.json",
            "assets/banim/lorm_sp1/script.txt",
            {
                "assets/banim/lorm_sp1/package.json",
                "assets/banim/lorm_sp1/script.txt",
                "graphics/banim/banim_lorm_sp1_sheet_0.png",
            },
        )
        package.frames["duplicate"] = package.frames["idle"]
        package.pngs["duplicate"] = package.pngs["idle"]
        outputs, paths, _metadata = banim.runtime_outputs(package, TEST_ROOT)
        self.assertEqual(paths["frame_idle"], paths["frame_duplicate"])
        self.assertEqual(
            len(
                [
                    path
                    for path in outputs
                    if path.endswith(".4bpp")
                ]
            ),
            1,
        )

    def test_sheet_tile_budget_uses_deduplicated_frame_payloads(self):
        package_path = os.path.join(TEST_ROOT, "package.json")
        script_path = os.path.join(TEST_ROOT, "script.txt")
        first_path = os.path.join(TEST_ROOT, "first.png")
        second_path = os.path.join(TEST_ROOT, "second.png")
        with open(
            os.path.join(REPO_ROOT, "assets", "banim", "lorm_sp1", "package.json"),
            encoding="utf-8",
        ) as handle:
            package = json.load(handle)
        with open(
            os.path.join(REPO_ROOT, "assets", "banim", "lorm_sp1", "script.txt"),
            encoding="utf-8",
        ) as source, open(script_path, "w", encoding="utf-8", newline="\n") as destination:
            destination.write(source.read())
        package["frames"] = [
            {"id": "idle", "path": "first.png"},
            {"id": "duplicate", "path": "second.png"},
        ]
        package["resources"]["maxFrames"] = 2
        package["resources"]["maxSheetTiles"] = 16
        self.write_indexed_png(first_path, b"\x00\xff")
        shutil.copyfile(first_path, second_path)
        with open(package_path, "w", encoding="utf-8") as handle:
            json.dump(package, handle)

        loaded = banim.load_package(
            TEST_ROOT,
            "package.json",
            "script.txt",
            {"package.json", "script.txt", "first.png", "second.png"},
        )
        self.assertEqual(len(loaded.frames), 2)

    def test_obj_vram_budget_uses_deduplicated_runtime_payloads(self):
        with open(os.path.join(REPO_ROOT, "assets", "manifest.json"), encoding="utf-8") as handle:
            document = json.load(handle)
        proof = next(record for record in document["assets"] if record["kind"] == "battle-animation-package")
        path = os.path.join(TEST_ROOT, "manifest.json")
        package = banim.load_package(
            REPO_ROOT,
            "assets/banim/lorm_sp1/package.json",
            "assets/banim/lorm_sp1/script.txt",
            {
                "assets/banim/lorm_sp1/package.json",
                "assets/banim/lorm_sp1/script.txt",
                "graphics/banim/banim_lorm_sp1_sheet_0.png",
            },
        )
        duplicate = copy.deepcopy(package)
        duplicate.data = copy.deepcopy(package.data)
        duplicate.data["resources"]["maxFrames"] = 2
        duplicate.data["resources"]["maxSheetTiles"] = 512
        duplicate.frames["duplicate"] = duplicate.frames["idle"]
        duplicate.pngs["duplicate"] = duplicate.pngs["idle"]
        _outputs, _paths, duplicate_metadata = banim.runtime_outputs(duplicate, TEST_ROOT)
        proof["resources"]["objVramBytes"] = duplicate_metadata["unique_frame_bytes"]
        proof["resources"]["romBytes"] = duplicate_metadata["runtime_bytes"]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with mock.patch.object(banim, "load_package", return_value=duplicate):
            manifest.load_and_validate(path)

        proof["resources"]["romBytes"] = duplicate_metadata["runtime_bytes"] - 1
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with mock.patch.object(banim, "load_package", return_value=duplicate):
            with self.assertRaises(GeneratedDataValidationError) as raised:
                manifest.load_and_validate(path)
        self.assertIn("generated runtime data", str(raised.exception))

        unique = copy.deepcopy(duplicate)
        unique.pngs["duplicate"] = copy.deepcopy(package.pngs["idle"])
        unique.pngs["duplicate"]["pixels"] = (
            b"\x11" + unique.pngs["duplicate"]["pixels"][1:]
        )
        _outputs, _paths, unique_metadata = banim.runtime_outputs(unique, TEST_ROOT)
        proof["resources"]["romBytes"] = unique_metadata["runtime_bytes"]
        proof["resources"]["objVramBytes"] = duplicate_metadata["unique_frame_bytes"]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with mock.patch.object(banim, "load_package", return_value=unique):
            with self.assertRaises(GeneratedDataValidationError) as raised:
                manifest.load_and_validate(path)
        self.assertIn("generated frame data", str(raised.exception))

    def test_zero_package_manifest_regenerates_base_banim_linker_script(self):
        with open(os.path.join(REPO_ROOT, "assets", "manifest.json"), encoding="utf-8") as handle:
            document = json.load(handle)
        document["assets"] = [
            record for record in document["assets"] if record["kind"] != "battle-animation-package"
        ]
        manifest_path = os.path.join(TEST_ROOT, "manifest.json")
        output = os.path.join(TEST_ROOT, "rollback")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        records = manifest.generate(manifest_path, output)
        rendered = manifest.render_makefile(records)
        combined = os.path.join(output, "banim", "linker_script_banim.txt")
        with open(os.path.join(REPO_ROOT, "linker_script_banim.txt"), "rb") as handle:
            base = handle.read()
        with open(combined, "rb") as handle:
            self.assertEqual(handle.read(), base + b"# AUTO-GENERATED package runtime entries; do not edit.\n")
        self.assertIn(
            "$(ASSET_BANIM_COMBINED_LINKER_SCRIPT): $(ASSET_OUTPUT_MK)",
            rendered,
        )

        os.unlink(combined)
        makefile = os.path.join(TEST_ROOT, "rollback.mk")
        with open(makefile, "w", encoding="utf-8") as handle:
            handle.write(
                "PYTHON := {}\n"
                "ASSET_MANIFEST := {}\n"
                "ASSET_OUTPUT_DIR := {}\n"
                "ASSET_OUTPUT_MK := {}/asset_manifest.mk\n"
                "ASSET_BANIM_COMBINED_LINKER_SCRIPT := {}\n"
                "ASSET_TOOL := $(PYTHON) -m scripts.assets --item-id-cap 0xCD\n"
                "include {}/asset_manifest.mk\n"
                "banim/data_banim.o: $(ASSET_BANIM_COMBINED_LINKER_SCRIPT)\n"
                "\t@test -f $<\n".format(
                    sys.executable,
                    manifest_path,
                    output,
                    output,
                    combined,
                    output,
                )
            )
        result = subprocess.run(
            ["make", "--no-print-directory", "-f", makefile, "banim/data_banim.o"],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(os.path.isfile(combined))

        package_records = manifest.load_and_validate(
            os.path.join(REPO_ROOT, "assets", "manifest.json")
        )
        self.assertIn(
            "$(ASSET_BANIM_COMBINED_LINKER_SCRIPT) &: $(ASSET_OUTPUT_MK)",
            manifest.render_makefile(package_records),
        )

    def test_grouped_banim_output_rule_rejects_missing_sibling(self):
        records = manifest.load_and_validate(
            os.path.join(REPO_ROOT, "assets", "manifest.json")
        )
        package = next(record.banim_package for record in records if record.id == "LORM_SP1_PROOF")
        _outputs, paths, _metadata = banim.runtime_outputs(
            package, "$(ASSET_OUTPUT_DIR)"
        )
        output = os.path.join(TEST_ROOT, "grouped")
        output_mk = os.path.join(output, "asset_manifest.mk")
        target = os.path.join(output, "banim", os.path.basename(paths["motion"]))
        os.makedirs(os.path.dirname(target))
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("partial output\n")
        with open(output_mk, "w", encoding="utf-8") as handle:
            handle.write("# stale manifest fragment\n")
        newer = os.stat(target).st_mtime_ns + 2_000_000_000
        os.utime(output_mk, ns=(newer, newer))
        makefile = os.path.join(TEST_ROOT, "grouped.mk")
        with open(makefile, "w", encoding="utf-8") as handle:
            handle.write(
                "ASSET_OUTPUT_DIR := {output}\n"
                "ASSET_OUTPUT_MK := {output_mk}\n"
                "ASSET_BANIM_COMBINED_LINKER_SCRIPT := {combined}\n"
                "ASSET_TOOL := true\n"
                "{fragment}".format(
                    output=output,
                    output_mk=output_mk,
                    combined=os.path.join(output, "banim", "linker_script_banim.txt"),
                    fragment=manifest.render_makefile(records),
                )
            )
        result = subprocess.run(
            ["make", "--no-print-directory", "-f", makefile, target],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(os.path.basename(target), result.stdout)

    def test_side_specific_frames_emit_distinct_aligned_oam_payloads(self):
        package = banim.load_package(
            REPO_ROOT,
            "assets/banim/lorm_sp1/package.json",
            "assets/banim/lorm_sp1/script.txt",
            {
                "assets/banim/lorm_sp1/package.json",
                "assets/banim/lorm_sp1/script.txt",
                "graphics/banim/banim_lorm_sp1_sheet_0.png",
            },
        )
        package.modes["normal"][0] = ("frame", 1, "idle", "left")
        package.modes["critical"][0] = ("frame", 1, "idle", "right")
        outputs, paths, _metadata = banim.runtime_outputs(package, TEST_ROOT)
        left = outputs[paths["oam_left"]]
        right = outputs[paths["oam_right"]]
        self.assertNotEqual(left, right)
        self.assertEqual(len(left), len(right))
        self.assertEqual(left[0:12], right[408:420])
        self.assertEqual(right[0:12], left[408:420])

    def test_palette_variant_and_frame_palette_mismatches_fail_closed(self):
        package_path = os.path.join(TEST_ROOT, "package.json")
        script_path = os.path.join(TEST_ROOT, "script.txt")
        frame_path = os.path.join(TEST_ROOT, "frame.png")
        second_path = os.path.join(TEST_ROOT, "second.png")
        with open(os.path.join(REPO_ROOT, "assets/banim/lorm_sp1/package.json"), encoding="utf-8") as handle:
            package = json.load(handle)
        with open(os.path.join(REPO_ROOT, "assets/banim/lorm_sp1/script.txt"), encoding="utf-8") as source:
            script = source.read()
        shutil.copyfile(os.path.join(REPO_ROOT, "graphics/banim/banim_lorm_sp1_sheet_0.png"), frame_path)
        package["frames"][0]["path"] = "frame.png"
        package["abbreviation"] = "way_too_long"
        with open(package_path, "w", encoding="utf-8") as handle:
            json.dump(package, handle)
        with open(script_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(script)
        with self.assertRaisesRegex(ValueError, r"fit char abbr\[12\]"):
            banim.load_package(
                TEST_ROOT,
                "package.json",
                "script.txt",
                {"package.json", "script.txt", "frame.png"},
            )

        package["abbreviation"] = "lorm_sp1"
        package["paletteVariants"] = ["default", "alternate"]
        with open(package_path, "w", encoding="utf-8") as handle:
            json.dump(package, handle)
        with open(script_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(script)
        with self.assertRaisesRegex(ValueError, "exactly \\['default'\\]"):
            banim.load_package(TEST_ROOT, "package.json", "script.txt", {"package.json", "script.txt", "frame.png"})

        package["paletteVariants"] = ["default"]
        package["frames"].append({"id": "second", "path": "second.png"})
        self.write_indexed_png(second_path, b"\x00\xff")
        with open(package_path, "w", encoding="utf-8") as handle:
            json.dump(package, handle)
        with self.assertRaisesRegex(ValueError, "identical PLTE and tRNS"):
            banim.load_package(
                TEST_ROOT,
                "package.json",
                "script.txt",
                {"package.json", "script.txt", "frame.png", "second.png"},
            )

    def test_generated_linker_derivatives_are_not_orphans(self):
        manifest_path = os.path.join(REPO_ROOT, "assets", "manifest.json")
        output = os.path.join(TEST_ROOT, "out")
        records = manifest.generate(manifest_path, output)
        package = next(record.banim_package for record in records if record.id == "LORM_SP1_PROOF")
        _outputs, paths, _metadata = banim.runtime_outputs(package, output)
        with open(paths["motion"][:-1] + "o", "wb") as handle:
            handle.write(b"test object")
        with open(paths["frame_idle"] + ".lz.o", "wb") as handle:
            handle.write(b"test compressed object")
        manifest.check(manifest_path, output)

    def test_generated_banim_includes_are_root_relative(self):
        expected = {
            "src/banim_data.c": "banim_data_entries.inc",
            "src/data_banimconf.c": "banim_defs.inc",
            "src/banim_package_runtime_test.c":
                "banim_runtime_test_defs.h",
        }
        for source, generated in expected.items():
            with self.subTest(source=source):
                with open(os.path.join(REPO_ROOT, source), encoding="utf-8") as handle:
                    text = handle.read()
                self.assertIn('#include "{}"'.format(generated), text)
                self.assertNotIn("build/generated/assets", text)
        with open(os.path.join(REPO_ROOT, "src", "banim_data.c"), encoding="utf-8") as handle:
            self.assertIn(
                '#include "banim_runtime_symbols.h"',
                handle.read(),
            )
        with open(os.path.join(REPO_ROOT, "include", "ekrbattle.h"), encoding="utf-8") as handle:
            self.assertNotIn("build/generated/assets", handle.read())

    def test_partial_banim_output_clean_regenerates_all_grouped_outputs(self):
        with open(os.path.join(REPO_ROOT, "assets.mk"), encoding="utf-8") as handle:
            rules = handle.read()
        self.assertIn("$(ASSET_BANIM_RUNTIME_SYMBOLS) &: $(ASSET_OUTPUT_MK)", rules)
        self.assertNotIn("\t@test -f $@", rules)
        for output in (
            "ASSET_BANIM_DATA_ENTRIES",
            "ASSET_BANIM_DEFS",
            "ASSET_BANIM_DEFS_HEADER",
            "ASSET_BANIM_RUNTIME_TEST_DEFS",
            "ASSET_BANIM_RUNTIME_SYMBOLS",
        ):
            self.assertIn("\t@test -f $({})".format(output), rules)
        self.assertIn(
            '$(ASSET_GENERATE_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate',
            rules,
        )


if __name__ == "__main__":
    unittest.main()
