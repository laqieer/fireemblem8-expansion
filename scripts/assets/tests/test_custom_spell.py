"""TC-CUSTOM-SPELL-061-003 strict adapter and generation checks."""

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
from types import SimpleNamespace
from unittest import mock

from scripts.assets import custom_spell, manifest
from scripts.generated_data.diagnostics import GeneratedDataError, GeneratedDataValidationError


ROOT = manifest.REPO_ROOT
REFERENCE_MANIFEST = os.path.join(
    ROOT, "assets", "manifests", "custom-spell-reference.json"
)
TEST_ROOT = os.path.join(
    ROOT, "build", "generated", "assets", "custom-spell-test"
)


def _chunk(name, data):
    return (
        struct.pack(">I", len(data))
        + name
        + data
        + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
    )


def _write_png(path, width, height, *, depth=4, color_type=3, alpha=b"\0\xff"):
    row_bytes = (width * depth + 7) // 8
    scanlines = b"".join(b"\0" + b"\0" * row_bytes for _ in range(height))
    palette = b"\0\0\0\xff\xff\xff"
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(
            b"IHDR",
            struct.pack(
                ">IIBBBBB", width, height, depth, color_type, 0, 0, 0
            ),
        )
        + _chunk(b"PLTE", palette)
        + _chunk(b"tRNS", alpha)
        + _chunk(b"IDAT", zlib.compress(scanlines))
        + _chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(payload)


class CustomSpellAdapterTests(unittest.TestCase):
    def setUp(self):
        if os.path.isdir(TEST_ROOT):
            shutil.rmtree(TEST_ROOT)
        os.makedirs(TEST_ROOT)

    def tearDown(self):
        if os.path.isdir(TEST_ROOT):
            shutil.rmtree(TEST_ROOT)

    def reference_record(self):
        with open(REFERENCE_MANIFEST, encoding="utf-8") as handle:
            document = json.load(handle)
        return next(
            record for record in document["assets"]
            if record["kind"] == "custom-spell-effect"
        )

    def load_reference(self):
        record = self.reference_record()
        return custom_spell.load_package(
            ROOT,
            record["sources"][0],
            record["sources"][1],
            record["sources"],
            "include/constants/songs.h",
            record["ownership"]["effectSymbol"],
        )

    def test_reference_package_conversion_is_deterministic_and_bounded(self):
        package = self.load_reference()
        self.assertEqual(len(package.frames), 2)
        self.assertEqual(sum(frame["duration"] for frame in package.frames), 4)
        self.assertEqual(package.sound_ids, [0xF1])
        self.assertEqual(package.bg_bytes, 0x500)
        self.assertEqual(package.obj_oam_entries, 2)
        self.assertEqual(package.runtime_bytes, 2444)
        first = [
            (
                frame["obj_lz"],
                frame["bg_lz"],
                frame["tsa_lz"],
                frame["obj_palette"],
                frame["bg_palette"],
            )
            for frame in package.frames
        ]
        second = self.load_reference()
        self.assertEqual(
            first,
            [
                (
                    frame["obj_lz"],
                    frame["bg_lz"],
                    frame["tsa_lz"],
                    frame["obj_palette"],
                    frame["bg_palette"],
                )
                for frame in second.frames
            ],
        )
        self.assertTrue(all(len(frame["obj_tiles"]) == 0x1000 for frame in package.frames))
        self.assertTrue(all(len(frame["bg_tiles"]) == 0x500 for frame in package.frames))
        self.assertTrue(all(len(frame["tsa"]) == 1200 for frame in package.frames))

    def test_reference_manifest_generates_runtime_binding_and_identity(self):
        records = manifest.load_and_validate(REFERENCE_MANIFEST, 1)
        output = os.path.join(TEST_ROOT, "out")
        manifest.generate(REFERENCE_MANIFEST, output, 1)
        manifest.check(REFERENCE_MANIFEST, output, 1)
        with open(
            os.path.join(output, "custom_spell", "custom_spell_effect_data.inc"),
            encoding="utf-8",
        ) as handle:
            data = handle.read()
        with open(
            os.path.join(
                output, "custom_spell", "custom_spell_effect_spellassoc.inc"
            ),
            encoding="utf-8",
        ) as handle:
            association = handle.read()
        with open(
            os.path.join(
                output, "custom_spell", "custom_spell_effect_inventory.json"
            ),
            encoding="utf-8",
        ) as handle:
            inventory = json.load(handle)
        with open(
            os.path.join(
                output, "custom_spell", "custom_spell_effect_runtime_test.h"
            ),
            encoding="utf-8",
        ) as handle:
            runtime_test = handle.read()
        self.assertIn("gGeneratedCustomSpellEffects", data)
        self.assertIn("CUSTOM_SPELL_REFERENCE", data)
        self.assertIn("ITEM_ANIMA_FORBLAZE, 128", association)
        self.assertIn(
            "#define CUSTOM_SPELL_EFFECT_TEST_ITEM ITEM_ANIMA_FORBLAZE",
            runtime_test,
        )
        self.assertEqual(inventory["inventory"]["runtime_abi"], 1)
        self.assertEqual(
            inventory["inventory"]["effects"][0]["animation_id"], 0x80
        )
        contract = custom_spell.canonical_contract(records)
        self.assertEqual(
            inventory["inventory_digest"], contract["inventory_digest"]
        )
        self.assertEqual(
            inventory["resource_digest"], contract["resource_digest"]
        )

    def test_reference_manifest_preserves_the_complete_default_asset_catalog(self):
        with open(os.path.join(ROOT, "assets", "manifest.json"), encoding="utf-8") as handle:
            default = json.load(handle)
        with open(REFERENCE_MANIFEST, encoding="utf-8") as handle:
            reference = json.load(handle)
        reference_base = [
            record for record in reference["assets"]
            if record["kind"] != "custom-spell-effect"
        ]
        self.assertEqual(reference_base, default["assets"])

    def test_feature_selection_is_fail_closed(self):
        root_records = manifest.load_and_validate(
            os.path.join(ROOT, "assets", "manifest.json"), 0
        )
        self.assertFalse(
            any(record.kind == "custom-spell-effect" for record in root_records)
        )
        with self.assertRaisesRegex(
            GeneratedDataError, "require EXPANSION_CUSTOM_SPELL_EFFECTS=1"
        ):
            manifest.load_and_validate(REFERENCE_MANIFEST, 0)
        with self.assertRaisesRegex(
            GeneratedDataError, "requires at least one custom-spell-effect"
        ):
            manifest.load_and_validate(
                os.path.join(ROOT, "assets", "manifest.json"), 1
            )

    def test_spell_schema_unknown_duplicate_mismatch_and_unused_fail(self):
        spell_path = os.path.join(TEST_ROOT, "spell.json")
        valid = {
            "schemaVersion": 1,
            "soundTable": [{"id": "F1", "song": "SONG_F1"}],
        }
        songs = os.path.join(ROOT, "include", "constants", "songs.h")
        cases = []
        unknown = copy.deepcopy(valid)
        unknown["extra"] = 1
        cases.append((unknown, "unknown field"))
        version = copy.deepcopy(valid)
        version["schemaVersion"] = 2
        cases.append((version, "schemaVersion"))
        lowercase = copy.deepcopy(valid)
        lowercase["soundTable"][0]["id"] = "f1"
        cases.append((lowercase, "canonical uppercase"))
        mismatch = copy.deepcopy(valid)
        mismatch["soundTable"][0]["song"] = "SONG_F6"
        cases.append((mismatch, "not 0xF1"))
        duplicate = copy.deepcopy(valid)
        duplicate["soundTable"].append({"id": "F1", "song": "SONG_F1"})
        cases.append((duplicate, "duplicates sound id"))
        for document, message in cases:
            with self.subTest(message=message):
                with open(spell_path, "w", encoding="utf-8") as handle:
                    json.dump(document, handle)
                with self.assertRaisesRegex(ValueError, message):
                    custom_spell._read_spell(spell_path, songs)

    def test_animation_grammar_rejects_every_unsupported_boundary(self):
        path = os.path.join(TEST_ROOT, "animation.txt")
        table = {0xF1: "SONG_F1"}
        valid_frame = "O p- obj.png\nB p- bg.png\n1\n"
        cases = {
            "unknown token": "C00\n" + valid_frame + "~~~\n",
            "CSA record": "CSA 00000000\n" + valid_frame + "~~~\n",
            "arbitrary C": "int main(void) {}\n" + valid_frame + "~~~\n",
            "missing B": "O p- obj.png\n1\n~~~\n",
            "missing wait": "O p- obj.png\nB p- bg.png\n~~~\n",
            "sound before frame": "SF1\n" + valid_frame + "~~~\n",
            "sound after frame": valid_frame + "SF1\n~~~\n",
            "undeclared sound": valid_frame + "SF2\n" + valid_frame + "~~~\n",
            "unsafe path": "O p- ../obj.png\nB p- bg.png\n1\n~~~\n",
            "multiple terminator": valid_frame + "~~~\n~~~\n",
            "content after terminator": valid_frame + "~~~\n# trailing\n",
            "missing terminator": valid_frame,
            "zero wait": "O p- obj.png\nB p- bg.png\n0\n~~~\n",
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                with open(path, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(source)
                with self.assertRaises(ValueError):
                    custom_spell.parse_animation(path, table)

    def test_multiple_ordered_sounds_map_to_following_frame(self):
        path = os.path.join(TEST_ROOT, "animation.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "O p- obj0.png\nB p- bg0.png\n1\n"
                "SF1\nSF6\n"
                "O p- obj1.png\nB p- bg1.png\n2\n~~~\n"
            )
        frames, sounds, sources = custom_spell.parse_animation(
            path, {0xF1: "SONG_F1", 0xF6: "SONG_F6"}
        )
        self.assertEqual(sounds, [0xF1, 0xF6])
        self.assertEqual(frames[0]["sounds"], ())
        self.assertEqual(frames[1]["sounds"], (0xF1, 0xF6))
        self.assertEqual(
            sources,
            [
                "images/obj0.png",
                "images/bg0.png",
                "images/obj1.png",
                "images/bg1.png",
            ],
        )
        lines = []
        custom_spell._script(
            lines,
            "sLongWait",
            ["sFrame"],
            [{"duration": 64}],
        )
        rendered = "".join(lines)
        self.assertIn("ANIMSCR_FORCE_SPRITE(sFrame, 63)", rendered)
        self.assertIn("ANIMSCR_FORCE_SPRITE(sFrame, 1)", rendered)

    def test_png_contract_rejects_dimensions_depth_palette_and_alpha(self):
        valid = os.path.join(TEST_ROOT, "valid.png")
        _write_png(valid, 240, 64)
        self.assertEqual(
            len(custom_spell.read_indexed_png(valid, 240, 64)["pixels"]),
            240 * 64,
        )
        cases = (
            ("wrong-size.png", 248, 64, 4, 3, b"\0\xff", "exactly 240x64"),
            ("wrong-depth.png", 240, 64, 8, 3, b"\0\xff", "indexed 4bpp"),
            ("true-color.png", 240, 64, 4, 2, b"\0\xff", "indexed 4bpp"),
            ("opaque-zero.png", 240, 64, 4, 3, b"\xff\xff", "only palette index 0"),
            ("partial-alpha.png", 240, 64, 4, 3, b"\0\x80", "only palette index 0"),
        )
        for name, width, height, depth, color_type, alpha, message in cases:
            with self.subTest(name=name):
                path = os.path.join(TEST_ROOT, name)
                _write_png(
                    path,
                    width,
                    height,
                    depth=depth,
                    color_type=color_type,
                    alpha=alpha,
                )
                with self.assertRaisesRegex(ValueError, message):
                    custom_spell.read_indexed_png(path, 240, 64)

    def test_obj_oam_and_frame_timing_capacities_fail_closed(self):
        pixels = bytearray(custom_spell.OBJ_WIDTH * custom_spell.OBJ_HEIGHT)
        for index in range(17):
            tile_x = (index % 10) * 3
            tile_y = (index // 10) * 3
            for y in range(tile_y * 8, tile_y * 8 + 8):
                for x in range(tile_x * 8, tile_x * 8 + 8):
                    pixels[y * custom_spell.OBJ_WIDTH + x] = 1
        with self.assertRaisesRegex(ValueError, "exceeding 16"):
            custom_spell._pack_obj(
                {"pixels": bytes(pixels), "palette": [(0, 0, 0), (255, 255, 255)]}
            )

        animation = os.path.join(TEST_ROOT, "animation.txt")
        with open(animation, "w", encoding="utf-8", newline="\n") as handle:
            for index in range(65):
                handle.write(
                    "O p- obj_{0}.png\nB p- bg_{0}.png\n1\n".format(index)
                )
            handle.write("~~~\n")
        with self.assertRaisesRegex(ValueError, "exceeds 64 frames"):
            custom_spell.parse_animation(animation, {})

        with open(animation, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "O p- obj.png\nB p- bg.png\n255\n"
                "O p- obj2.png\nB p- bg2.png\n1\n~~~\n"
            )
        with self.assertRaisesRegex(ValueError, "exceeds 255 total frames"):
            custom_spell.parse_animation(animation, {})

    def test_manifest_binding_and_resource_drift_fail(self):
        with open(REFERENCE_MANIFEST, encoding="utf-8") as handle:
            document = json.load(handle)
        record = next(
            row for row in document["assets"]
            if row["kind"] == "custom-spell-effect"
        )
        path = os.path.join(TEST_ROOT, "manifest.json")
        cases = (
            ("item", "ITEM_STAFF_HEAL", "battle-capable"),
            ("fallbackVanillaEffect", "SASSOC_EFX_NOT_REAL", "not declared"),
            ("effectSymbol", "BAD_SYMBOL", "CUSTOM_SPELL"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                changed = copy.deepcopy(document)
                target = next(
                    row for row in changed["assets"]
                    if row["kind"] == "custom-spell-effect"
                )
                target["ownership"][field] = value
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(changed, handle)
                with mock.patch.object(
                    manifest, "_repo_path", side_effect=lambda value, *_: value
                ):
                    with self.assertRaisesRegex(
                        GeneratedDataValidationError, message
                    ):
                        manifest.load_and_validate(path)

        record["resources"]["bgBytes"] += 32
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with mock.patch.object(
            manifest, "_repo_path", side_effect=lambda value, *_: value
        ):
            with self.assertRaisesRegex(
                GeneratedDataValidationError, "generated value"
            ):
                manifest.load_and_validate(path)

    def test_check_rejects_missing_stale_and_orphan_custom_outputs(self):
        output = os.path.join(TEST_ROOT, "out")
        manifest.generate(REFERENCE_MANIFEST, output, 1)
        inventory = os.path.join(
            output, "custom_spell", "custom_spell_effect_inventory.json"
        )
        os.unlink(inventory)
        with self.assertRaisesRegex(
            GeneratedDataValidationError, "missing generated output"
        ):
            manifest.check(REFERENCE_MANIFEST, output, 1)
        manifest.generate(REFERENCE_MANIFEST, output, 1)
        with open(inventory, "a", encoding="utf-8") as handle:
            handle.write("stale")
        with self.assertRaisesRegex(
            GeneratedDataValidationError, "stale generated output"
        ):
            manifest.check(REFERENCE_MANIFEST, output, 1)
        manifest.generate(REFERENCE_MANIFEST, output, 1)
        orphan = os.path.join(output, "custom_spell", "orphan.bin")
        with open(orphan, "wb") as handle:
            handle.write(b"orphan")
        with self.assertRaisesRegex(
            GeneratedDataValidationError, "orphan generated output"
        ):
            manifest.check(REFERENCE_MANIFEST, output, 1)

    def test_custom_incbin_consumer_rejects_output_override(self):
        with open(REFERENCE_MANIFEST, encoding="utf-8") as handle:
            document = json.load(handle)
        document["assets"] = [
            record for record in document["assets"]
            if record["kind"] == "custom-spell-effect"
        ]
        manifest_path = os.path.join(TEST_ROOT, "custom-only-manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-f",
                "assets.mk",
                "assets-validate",
                "PYTHON={}".format(sys.executable),
                "EXPANSION_CUSTOM_SPELL_EFFECTS=1",
                "ASSET_MANIFEST={}".format(manifest_path),
                "ASSET_OUTPUT_DIR=build/generated/assets/custom-spell-override",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "custom-spell-effect INCBIN consumer(s) CUSTOM_SPELL_REFERENCE",
            result.stdout + result.stderr,
        )

    def test_dense_index_allocation_is_sorted_and_capacity_bounded(self):
        package = self.load_reference()

        def record(record_id, item):
            return SimpleNamespace(
                id=record_id,
                custom_spell_package=copy.deepcopy(package),
                custom_spell_fallback_id=22,
                ownership={
                    "effectSymbol": "CUSTOM_SPELL_" + record_id,
                    "item": item,
                },
                resources={"hitFrame": 2},
            )

        records = [record("ZETA", "ITEM_ANIMA_FORBLAZE"), record("ALPHA", "ITEM_LIGHT_LUCE")]
        contract = custom_spell.canonical_contract(records)
        self.assertEqual(
            [effect["id"] for effect in contract["inventory"]["effects"]],
            ["ALPHA", "ZETA"],
        )
        self.assertEqual(
            [effect["animation_id"] for effect in contract["inventory"]["effects"]],
            [0x80, 0x81],
        )
        collision_free = custom_spell._render_data_include(
            [
                record("A_B", "ITEM_ANIMA_FORBLAZE"),
                record("A__B", "ITEM_LIGHT_LUCE"),
            ],
            TEST_ROOT,
        )
        self.assertIn("sCustomSpell_A_BFrames", collision_free)
        self.assertIn("sCustomSpell_A__BFrames", collision_free)
        with self.assertRaisesRegex(ValueError, "capacity 16"):
            custom_spell.validate_collection(
                [record("E{:02d}".format(index), "ITEM_{:02d}".format(index)) for index in range(17)]
            )
        duplicate_item = [
            record("FIRST", "ITEM_ANIMA_FORBLAZE"),
            record("SECOND", "ITEM_ANIMA_FORBLAZE"),
        ]
        with self.assertRaisesRegex(ValueError, "duplicate custom spell item"):
            custom_spell.validate_collection(duplicate_item)


if __name__ == "__main__":
    unittest.main()
