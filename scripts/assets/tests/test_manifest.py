"""Positive and adversarial host checks for the version-1 asset manifest."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from scripts.assets import manifest, tmx
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
            REPO_ROOT, "scripts", "assets", "tests", ".asset_manifest_source_link.tmx"
        )
        os.symlink(
            os.path.join(            REPO_ROOT, "assets", "tmx", "Ch2Map.tmx"),
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

    def test_capacity_and_actual_ownership_conflicts_fail(self):
        capacity = valid_record()
        capacity["resources"]["mapWidth"] = 200
        capacity["resources"]["mapHeight"] = 200
        self.assert_validation_error([capacity], "exceed the 2048-byte gBmMapBuffer")
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


if __name__ == "__main__":
    unittest.main()
