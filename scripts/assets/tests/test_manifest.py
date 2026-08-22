"""Positive and adversarial host checks for the version-1 asset manifest."""

from __future__ import annotations

import copy
import json
import os
import shutil
import struct
import subprocess
import sys
import unittest
from unittest import mock
import zlib

from scripts.assets import manifest
from scripts.generated_data.diagnostics import GeneratedDataError, GeneratedDataValidationError


REPO_ROOT = manifest.REPO_ROOT
TEST_ROOT = os.path.join(REPO_ROOT, "build", "generated", "assets", "test-work")
FIXTURE_ROOT = "graphics/map/layout"


def valid_record():
    return {
        "id": "CH2_MAIN_MAP",
        "kind": "chapter-map-layout",
        "sources": [FIXTURE_ROOT + "/Ch2Map.mar", FIXTURE_ROOT + "/Ch2Map.json"],
        "dependsOn": [],
        "options": {"format": "mar", "compression": "lz77"},
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


def write_indexed_png(path, width=128, height=112, indexed=True):
    color_type = 3 if indexed else 2
    depth = 4 if indexed else 8
    palette = bytes(component for value in range(16) for component in (value * 16,) * 3)
    rows = b"".join(b"\0" + (b"\0" * ((width * depth + 7) // 8)) for _ in range(height))

    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    chunks = [
        b"\x89PNG\r\n\x1a\n",
        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, 0)),
    ]
    if indexed:
        chunks.extend((chunk(b"PLTE", palette), chunk(b"tRNS", b"\0" + b"\xff" * 15)))
    chunks.extend((chunk(b"IDAT", zlib.compress(rows)), chunk(b"IEND", b"")))
    with open(path, "wb") as handle:
        handle.write(b"".join(chunks))


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

    def test_real_manifest_is_valid_and_deterministic(self):
        path = os.path.join(REPO_ROOT, "assets", "manifest.json")
        first = manifest.load_and_validate(path)
        second = manifest.load_and_validate(path)
        self.assertEqual(manifest.render_makefile(first), manifest.render_makefile(second))
        self.assertIn(
            "$(MODERN_OUTPUT_DIR)/src/data/data_8B363C.o: graphics/map/layout/Ch2Map.mar",
            manifest.render_makefile(first),
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
        non_posix["sources"][0] = "graphics\\map\\layout\\Ch2Map.mar"
        self.assert_validation_error([non_posix], "normalized POSIX separators")
        non_nfc = valid_record()
        non_nfc["sources"][0] = "graphics/map/layout/Ch2Map\u0065\u0301.mar"
        self.assert_validation_error([non_nfc], "NFC-normalized Unicode")

        link_path = os.path.join(
            REPO_ROOT, "scripts", "assets", "tests", ".asset_manifest_source_link.mar"
        )
        os.symlink(
            os.path.join(REPO_ROOT, "graphics", "map", "layout", "Ch2Map.mar"),
            link_path,
        )
        self.addCleanup(lambda: os.path.lexists(link_path) and os.unlink(link_path))
        symlink = valid_record()
        symlink["sources"][0] = "scripts/assets/tests/.asset_manifest_source_link.mar"
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

    def test_formatted_portrait_package_reports_malformed_metadata_without_crashing(self):
        record = valid_portrait_record(TEST_ROOT)
        with open(os.path.join(REPO_ROOT, record["sources"][1]), "w", encoding="utf-8") as handle:
            handle.write("{ malformed metadata")
        self.assert_validation_error([record], "cannot load portrait metadata")

    def test_formatted_portrait_package_reports_unreadable_metadata_without_crashing(self):
        record = valid_portrait_record(TEST_ROOT)
        metadata_path = os.path.join(REPO_ROOT, record["sources"][1])
        real_open = open

        def reject_metadata(path, *args, **kwargs):
            if os.fspath(path) == metadata_path:
                raise OSError("metadata unavailable for test")
            return real_open(path, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=reject_metadata):
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
        self.assert_validation_error([record], "portrait metadata must contain exactly")

    def test_formatted_portrait_package_requires_metadata_json(self):
        record = valid_portrait_record(TEST_ROOT)
        record["sources"][1] = record["sources"][1].replace("metadata.json", "proof.json")
        source_path = os.path.join(REPO_ROOT, record["sources"][1])
        os.rename(
            os.path.join(REPO_ROOT, record["sources"][1].replace("proof.json", "metadata.json")),
            source_path,
        )
        self.assert_validation_error([record], "metadata.json")

    def test_sources_command_includes_portrait_registry_dependency(self):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.assets", "sources"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
        )
        self.assertIn("assets/portrait_registry.json", result.stdout.splitlines())


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
        ownership = valid_record()
        ownership["ownership"]["mainLayerId"] = 12
        self.assert_validation_error([ownership], "does not select mainLayerId 12")

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
            manifest_path = os.path.join(
                "build", "generated", "assets", "test-work", "linked-output"
            )
            manifest.safe_output_dir(manifest_path)

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
            "src/data/data_8B363C.o: dependent.mar transitive.mar direct.mar\n",
            rendered,
        )

    @staticmethod
    def read_outputs(out_dir):
        outputs = {}
        for name in manifest.OUTPUT_NAMES:
            with open(os.path.join(out_dir, name), encoding="utf-8") as handle:
                outputs[name] = handle.read()
        return outputs


if __name__ == "__main__":
    unittest.main()
