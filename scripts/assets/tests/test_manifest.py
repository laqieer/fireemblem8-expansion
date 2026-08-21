"""Positive and adversarial host checks for the version-1 asset manifest."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

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
        os.symlink(REPO_ROOT, link_path)
        self.addCleanup(lambda: os.path.lexists(link_path) and os.unlink(link_path))
        with self.assertRaises(GeneratedDataError):
            manifest_path = os.path.join(
                "build", "generated", "assets", "test-work", "linked-output"
            )
            manifest.safe_output_dir(manifest_path)

    @staticmethod
    def read_outputs(out_dir):
        outputs = {}
        for name in manifest.OUTPUT_NAMES:
            with open(os.path.join(out_dir, name), encoding="utf-8") as handle:
                outputs[name] = handle.read()
        return outputs


if __name__ == "__main__":
    unittest.main()
