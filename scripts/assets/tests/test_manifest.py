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
import zlib
from unittest import mock
from types import SimpleNamespace

from scripts.assets import banim, manifest
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
    def write_indexed_png(path, alpha):
        def chunk(name, data):
            return (
                struct.pack(">I", len(data))
                + name
                + data
                + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
            )

        pixels = b"".join(b"\x00" + b"\x00" * 4 for _ in range(8))
        payload = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 4, 3, 0, 0, 0))
            + chunk(b"PLTE", b"\x00\x00\x00\xff\xff\xff")
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
