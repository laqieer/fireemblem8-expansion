"""Positive and adversarial host checks for the version-1 asset manifest."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import unittest
import zlib
from unittest import mock
from types import SimpleNamespace

from scripts.assets import banim, manifest, tmx
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
            "$(MODERN_OUTPUT_DIR)/src/data/data_8B363C.o: assets/tmx/Ch2Map.tmx",
            manifest.render_makefile(first),
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

    def test_asset_makefile_tracks_declared_manifest_sources(self):
        with open(os.path.join(REPO_ROOT, "assets.mk"), encoding="utf-8") as handle:
            asset_makefile = handle.read()
        self.assertIn(
            'ASSET_MANIFEST_SOURCES := $(shell $(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" sources)',
            asset_makefile,
        )
        self.assertIn(
            "$(ASSET_OUTPUT_MK): $(ASSET_MANIFEST) $(ASSET_MANIFEST_SOURCES) $(ASSET_TOOL_INPUTS)",
            asset_makefile,
        )

    def test_asset_makefile_guards_only_tmx_incbin_consumers(self):
        guarded = subprocess.run(
            [
                "make",
                "ASSET_OUTPUT_DIR=build/generated/assets/alternate",
                "assets-validate",
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertNotEqual(guarded.returncode, 0)
        self.assertIn("must be build/generated/assets", guarded.stderr)

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
        with self.assertRaisesRegex(ValueError, "tRNS entries must be 0 or 255"):
            banim.read_indexed_png(partial)

        opaque = os.path.join(TEST_ROOT, "opaque-alpha.png")
        self.write_indexed_png(opaque, b"\xff\xff")
        with self.assertRaisesRegex(ValueError, "exactly one transparent"):
            banim.read_indexed_png(opaque)

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
                "ASSET_TOOL := $(PYTHON) -m scripts.assets\n"
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
            "src/banim_data.c": "build/generated/assets/banim/banim_data_entries.inc",
            "src/data_banimconf.c": "build/generated/assets/banim/banim_defs.inc",
            "src/banim_package_runtime_test.c":
                "build/generated/assets/banim/banim_runtime_test_defs.h",
        }
        for source, generated in expected.items():
            with self.subTest(source=source):
                with open(os.path.join(REPO_ROOT, source), encoding="utf-8") as handle:
                    text = handle.read()
                self.assertIn('#include "{}"'.format(generated), text)
                self.assertNotIn("../build/generated/assets", text)
        with open(os.path.join(REPO_ROOT, "src", "banim_data.c"), encoding="utf-8") as handle:
            self.assertIn(
                '#include "build/generated/assets/banim/banim_runtime_symbols.h"',
                handle.read(),
            )
        with open(os.path.join(REPO_ROOT, "include", "ekrbattle.h"), encoding="utf-8") as handle:
            self.assertNotIn("build/generated/assets", handle.read())

    def test_partial_banim_output_clean_regenerates_all_grouped_outputs(self):
        with open(os.path.join(REPO_ROOT, "assets.mk"), encoding="utf-8") as handle:
            rules = handle.read()
        self.assertIn("$(ASSET_BANIM_RUNTIME_SYMBOLS) &: $(ASSET_OUTPUT_MK)", rules)
        self.assertIn(
            '$(ASSET_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate',
            rules,
        )


if __name__ == "__main__":
    unittest.main()
