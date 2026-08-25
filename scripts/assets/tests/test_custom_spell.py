"""TC-CUSTOM-SPELL-061-003 strict adapter and generation checks."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import unittest
import zlib
from types import SimpleNamespace
from unittest import mock

from scripts.assets import cli, custom_spell, manifest
from scripts.generated_data.diagnostics import GeneratedDataError, GeneratedDataValidationError


ROOT = manifest.REPO_ROOT
DEFAULT_MANIFEST = os.path.join(ROOT, "assets", "manifest.json")
REFERENCE_MANIFEST = os.path.join(
    ROOT, "assets", "manifests", "custom-spell-reference.json"
)
PROFILE_TEST_ROOT = os.path.join(ROOT, "build", "custom-spell-profile-test")
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


def _write_png(
    path,
    width,
    height,
    *,
    depth=4,
    color_type=3,
    alpha=b"\0\xff",
    iend=b"",
    palette=None,
    pixels=None,
    ancillary_before_plte=(),
    ancillary_before_idat=(),
    ancillary_after_idat=(),
    split_idat=False,
):
    if palette is None:
        palette = ((0, 0, 0), (255, 255, 255))
    if pixels is None:
        pixels = bytes(width * height)
    if len(pixels) != width * height:
        raise ValueError("PNG fixture pixels must fill the image")
    scanlines = bytearray()
    for row_start in range(0, len(pixels), width):
        scanlines.append(0)
        for offset in range(row_start, row_start + width, 2):
            scanlines.append((pixels[offset] << 4) | pixels[offset + 1])
    palette_bytes = bytes(component for color in palette for component in color)
    compressed = zlib.compress(bytes(scanlines))
    if split_idat:
        midpoint = len(compressed) // 2
        idat_parts = (compressed[:midpoint], compressed[midpoint:])
    else:
        idat_parts = (compressed,)
    chunks = (
        (
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, 0),
        ),
        *ancillary_before_plte,
        (b"PLTE", palette_bytes),
        (b"tRNS", alpha),
        *ancillary_before_idat,
        *((b"IDAT", part) for part in idat_parts),
        *ancillary_after_idat,
        (b"IEND", iend),
    )
    _write_png_chunks(path, chunks)


def _write_png_chunks(path, chunks):
    payload = b"\x89PNG\r\n\x1a\n" + b"".join(
        _chunk(name, data) for name, data in chunks
    )
    with open(path, "wb") as handle:
        handle.write(payload)


def _reference_gba_lz77(data):
    output = bytearray(b"\x10")
    output.extend(len(data).to_bytes(3, "little"))
    position = 0
    while position < len(data):
        flag_offset = len(output)
        output.append(0)
        flags = 0
        for bit in range(8):
            if position >= len(data):
                break
            best_length = 0
            best_distance = 0
            for candidate in range(max(0, position - 0x1000), position):
                length = 0
                while (
                    length < 18
                    and position + length < len(data)
                    and data[candidate + length] == data[position + length]
                ):
                    length += 1
                    if candidate + length >= position:
                        break
                if length > best_length:
                    best_length = length
                    best_distance = position - candidate
            if best_length >= 3:
                flags |= 0x80 >> bit
                output.append(((best_length - 3) << 4) | ((best_distance - 1) >> 8))
                output.append((best_distance - 1) & 0xFF)
                position += best_length
            else:
                output.append(data[position])
                position += 1
        output[flag_offset] = flags
    return bytes(output)


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
        self.assertEqual(package.runtime_bytes, 2460)
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

    def test_script_accounting_matches_emitted_words(self):
        package = self.load_reference()
        baseline = custom_spell.runtime_bytes(package, "CUSTOM_SPELL_REFERENCE")
        self.assertEqual(baseline, package.runtime_bytes)
        package.frames[0]["duration"] = 64
        package.frames[1]["duration"] = 126
        script_lines = []
        custom_spell._script(script_lines, "LeftScript", ["A", "B"], package.frames)
        custom_spell._script(script_lines, "RightScript", ["A", "B"], package.frames)
        words = [
            line for line in script_lines
            if "ANIMSCR_FORCE_SPRITE" in line or "ANIMSCR_BLOCKED" in line
        ]
        self.assertEqual(len(words), 10)
        actual = custom_spell.runtime_bytes(package, "CUSTOM_SPELL_REFERENCE")
        self.assertEqual(
            actual,
            baseline + 16,
        )
        old_one_script_accounting = actual - 16
        aggregate_records = [
            SimpleNamespace(
                custom_spell_package=SimpleNamespace(
                    runtime_bytes=custom_spell.MAX_ROM_BYTES
                    - old_one_script_accounting
                ),
                ownership={"item": "ITEM_ANIMA_FORBLAZE", "effectSymbol": "A"},
            ),
            SimpleNamespace(
                custom_spell_package=SimpleNamespace(runtime_bytes=actual),
                ownership={"item": "ITEM_LIGHT_LUCE", "effectSymbol": "B"},
            ),
        ]
        with self.assertRaisesRegex(ValueError, "aggregate custom spell runtime payload"):
            custom_spell.validate_collection(aggregate_records)

    def test_cli_requires_an_explicit_item_id_cap(self):
        with mock.patch.dict(os.environ, {"FE8_ITEM_ID_CAP": "0xCE"}, clear=False):
            self.assertEqual(
                cli.main(["--manifest", DEFAULT_MANIFEST, "validate"]),
                1,
            )
        self.assertEqual(
            cli.main(
                [
                    "--item-id-cap",
                    "0xCD",
                    "--manifest",
                    DEFAULT_MANIFEST,
                    "validate",
                ]
            ),
            0,
        )

    def test_assets_make_forwards_the_configured_item_cap(self):
        config = os.path.join(TEST_ROOT, "configured-item-cap.mk")
        with open(config, "w", encoding="utf-8") as handle:
            handle.write("FE8_ITEM_ID_CAP := 0xCE\n")
        environment = os.environ.copy()
        environment["FE8_ITEM_ID_CAP"] = "0xCD"
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-n",
                "assets-validate",
                "AUTOTOOLS_CONFIG_MK={}".format(config),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('--item-id-cap "0xCE"', result.stdout)
        self.assertNotIn('--item-id-cap "0xCD"', result.stdout)

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
        with open(
            os.path.join(
                output, "custom_spell", "custom_spell_effect_generated.h"
            ),
            encoding="utf-8",
        ) as handle:
            self.assertIn(
                "#define CUSTOM_SPELL_REFERENCE (CUSTOM_SPELL_EFFECT_BASE + 0)",
                handle.read(),
            )
        self.assertIn("ITEM_ANIMA_FORBLAZE, 128", association)
        self.assertIn(
            "#define CUSTOM_SPELL_EFFECT_TEST_ITEM ITEM_ANIMA_FORBLAZE",
            runtime_test,
        )
        self.assertEqual(inventory["inventory"]["runtime_abi"], 1)
        self.assertEqual(
            inventory["inventory"]["effects"][0]["animation_id"], 0x80
        )
        self.assertRegex(
            inventory["inventory"]["effects"][0]["frames"][0]["oam_sha256"],
            r"^[0-9a-f]{64}$",
        )
        contract = custom_spell.canonical_contract(records)
        self.assertEqual(
            inventory["inventory_digest"], contract["inventory_digest"]
        )
        self.assertEqual(
            inventory["resource_digest"], contract["resource_digest"]
        )

    def test_item_binding_uses_active_generated_profile_and_cap(self):
        with mock.patch.dict(
            os.environ, {"FE8_ITEM_ID_CAP": "0xCE"}, clear=False
        ):
            record = custom_spell._active_item_record(
                ROOT, "ITEM_EXPANSION_CE"
            )
        self.assertEqual(record.item, "ITEM_EXPANSION_CE")
        self.assertEqual(record.weapon_type, "ITYPE_ITEM")

        with mock.patch.dict(
            os.environ, {"FE8_ITEM_ID_CAP": "0xCD"}, clear=False
        ):
            with self.assertRaisesRegex(
                ValueError, r"beyond configured FE8_ITEM_ID_CAP=0xCD"
            ):
                custom_spell._active_item_record(
                    ROOT, "ITEM_EXPANSION_CE"
                )
        with self.assertRaisesRegex(ValueError, "not a declared ITEM"):
            custom_spell._active_item_record(ROOT, "ITEM_NOT_REAL")

    def test_manifest_normalizes_invalid_item_caps(self):
        for item_id_cap, environment, message in (
            (None, {"FE8_ITEM_ID_CAP": "not-a-cap"}, "is not an integer"),
            (0x100, {}, "exceeds the technical maximum"),
        ):
            with self.subTest(item_id_cap=item_id_cap, environment=environment):
                with mock.patch.dict(os.environ, environment, clear=False):
                    with self.assertRaises(GeneratedDataValidationError) as raised:
                        manifest.load_and_validate(
                            REFERENCE_MANIFEST,
                            1,
                            item_id_cap=item_id_cap,
                        )
                self.assertIn(message, str(raised.exception))
                self.assertIn("CUSTOM_SPELL_REFERENCE.package", str(raised.exception))

    def test_generated_magic_item_can_own_runtime_binding(self):
        ownership = copy.deepcopy(self.reference_record()["ownership"])
        ownership["item"] = "ITEM_EXPANSION_CE"
        generated_magic = SimpleNamespace(
            weapon_type="ITYPE_ANIMA",
            attributes=("IA_WEAPON", "IA_MAGIC"),
        )
        with mock.patch.object(
            custom_spell,
            "_active_item_record",
            return_value=generated_magic,
        ):
            fallback, item_type = custom_spell.validate_runtime_binding(
                ROOT, ownership
            )
        self.assertEqual(fallback, 22)
        self.assertEqual(item_type, "ITYPE_ANIMA")

    def test_moved_sprite_geometry_changes_inventory_identity(self):
        def sprite_at(tile_x):
            pixels = bytearray(custom_spell.OBJ_WIDTH * custom_spell.OBJ_HEIGHT)
            for y in range(8):
                for x in range(tile_x * 8, tile_x * 8 + 8):
                    pixels[y * custom_spell.OBJ_WIDTH + x] = 1
            return {
                "pixels": bytes(pixels),
                "palette": [(0, 0, 0), (255, 255, 255)],
            }

        original_tiles, original_oam = custom_spell._pack_obj(sprite_at(0))
        moved_tiles, moved_oam = custom_spell._pack_obj(sprite_at(1))
        self.assertEqual(original_tiles, moved_tiles)
        self.assertNotEqual(original_oam, moved_oam)

        original_package = copy.deepcopy(self.load_reference())
        original_package.frames[0]["obj_lz"] = custom_spell.gba_lz77(original_tiles)
        original_package.frames[0]["oam"] = original_oam
        moved_package = copy.deepcopy(original_package)
        moved_package.frames[0]["obj_lz"] = custom_spell.gba_lz77(moved_tiles)
        moved_package.frames[0]["oam"] = moved_oam

        def record(package):
            return SimpleNamespace(
                id="MOVED_SPRITE",
                custom_spell_package=package,
                custom_spell_fallback_id=22,
                ownership={
                    "effectSymbol": "CUSTOM_SPELL_MOVED_SPRITE",
                    "item": "ITEM_ANIMA_FORBLAZE",
                },
                resources={"hitFrame": 2},
            )

        original = custom_spell.canonical_contract([record(original_package)])
        moved = custom_spell.canonical_contract([record(moved_package)])
        original_frame = original["inventory"]["effects"][0]["frames"][0]
        moved_frame = moved["inventory"]["effects"][0]["frames"][0]
        self.assertEqual(
            original_frame["obj_gfx_sha256"], moved_frame["obj_gfx_sha256"]
        )
        self.assertNotEqual(original_frame["oam_sha256"], moved_frame["oam_sha256"])
        self.assertNotEqual(original["inventory_digest"], moved["inventory_digest"])

    def test_generated_oam_vectors_cover_canonical_and_mirrored_front_back_geometry(self):
        pixels = bytearray(custom_spell.OBJ_WIDTH * custom_spell.OBJ_HEIGHT)
        for y in range(3 * 8, 4 * 8):
            for x in range(2 * 8, 3 * 8):
                pixels[y * custom_spell.OBJ_WIDTH + x] = 1
        for y in range(4 * 8, 5 * 8):
            for x in range(240 + 5 * 8, 240 + 6 * 8):
                pixels[y * custom_spell.OBJ_WIDTH + x] = 2

        _, entries = custom_spell._pack_obj(
            {
                "pixels": bytes(pixels),
                "palette": ((0, 0, 0), (255, 255, 255), (255, 0, 0)),
            }
        )
        self.assertEqual(
            entries,
            [
                {
                    "source_x": 2,
                    "source_y": 3,
                    "seat_x": 0,
                    "seat_y": 0,
                    "width": 1,
                    "height": 1,
                    "shape": "ATTR0_SQUARE",
                    "size": "ATTR1_SIZE_8",
                },
                {
                    "source_x": 5,
                    "source_y": 4,
                    "seat_x": 1,
                    "seat_y": 0,
                    "width": 1,
                    "height": 1,
                    "shape": "ATTR0_SQUARE",
                    "size": "ATTR1_SIZE_8",
                },
            ],
        )

        canonical = []
        custom_spell._oam_array(canonical, "Vector", entries, False)
        self.assertEqual(
            "".join(canonical),
            "static const struct AnimSpriteData Vector[] =\n"
            "{\n"
            "    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), "
            ".as = { .object = { 0, -156, -64 } } },\n"
            "    { .header = (u32)(ATTR0_SQUARE) | ((u32)(ATTR1_SIZE_8) << 16), "
            ".as = { .object = { 1, -132, -56 } } },\n"
            "    ANIM_SPRITE_END,\n"
            "};\n\n",
        )

        mirrored = []
        custom_spell._oam_array(mirrored, "Vector", entries, True)
        self.assertEqual(
            "".join(mirrored),
            "static const struct AnimSpriteData Vector[] =\n"
            "{\n"
            "    { .header = (u32)(ATTR0_SQUARE) | "
            "((u32)((ATTR1_SIZE_8 + ATTR1_FLIP_X)) << 16), "
            ".as = { .object = { 0, 148, -64 } } },\n"
            "    { .header = (u32)(ATTR0_SQUARE) | "
            "((u32)((ATTR1_SIZE_8 + ATTR1_FLIP_X)) << 16), "
            ".as = { .object = { 1, 124, -56 } } },\n"
            "    ANIM_SPRITE_END,\n"
            "};\n\n",
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
        with open(spell_path, "w", encoding="utf-8") as handle:
            handle.write(
                '{"schemaVersion": 1, "schemaVersion": 1, "soundTable": []}'
            )
        with self.assertRaisesRegex(ValueError, "duplicate key 'schemaVersion'"):
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
            "unicode wait": "O p- obj.png\nB p- bg.png\n\u0661\n~~~\n",
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
        unused_partial_alpha = os.path.join(TEST_ROOT, "unused-partial-alpha.png")
        _write_png(
            unused_partial_alpha,
            240,
            64,
            alpha=b"\0\x80\xff",
            palette=((0, 0, 0), (255, 0, 0), (0, 255, 0)),
            pixels=bytes([2]) * (240 * 64),
        )
        self.assertEqual(
            set(custom_spell.read_indexed_png(unused_partial_alpha, 240, 64)["pixels"]),
            {2},
        )
        used_partial_alpha = os.path.join(TEST_ROOT, "used-partial-alpha.png")
        _write_png(
            used_partial_alpha,
            240,
            64,
            alpha=b"\0\x80\xff",
            palette=((0, 0, 0), (255, 0, 0), (0, 255, 0)),
            pixels=bytes([1]) * (240 * 64),
        )
        with self.assertRaisesRegex(
            ValueError, "used nonzero palette indices opaque"
        ):
            custom_spell.read_indexed_png(used_partial_alpha, 240, 64)
        nonempty_iend = os.path.join(TEST_ROOT, "nonempty-iend.png")
        _write_png(nonempty_iend, 240, 64, iend=b"invalid")
        with self.assertRaisesRegex(ValueError, "non-empty IEND"):
            custom_spell.read_indexed_png(nonempty_iend, 240, 64)

    def test_custom_spell_package_rejects_symlinked_direct_children(self):
        package_dir = os.path.join(
            ROOT, "scripts", "assets", "tests", ".custom-spell-symlink-package"
        )
        reference_dir = os.path.join(ROOT, "graphics", "custom_spell", "reference")
        relative = os.path.relpath(package_dir, ROOT).replace(os.sep, "/")
        sources = [
            relative + "/spell.json",
            relative + "/animation.txt",
            relative + "/images/reference_obj_00.png",
            relative + "/images/reference_bg_00.png",
            relative + "/images/reference_obj_01.png",
            relative + "/images/reference_bg_01.png",
        ]

        def cleanup():
            if os.path.islink(package_dir):
                os.unlink(package_dir)
            elif os.path.exists(package_dir):
                shutil.rmtree(package_dir)

        def load():
            return custom_spell.load_package(
                ROOT,
                sources[0],
                sources[1],
                sources,
                "include/constants/songs.h",
                "CUSTOM_SPELL_REFERENCE",
            )

        self.addCleanup(cleanup)
        os.symlink(reference_dir, package_dir)
        with self.assertRaisesRegex(ValueError, "package directory"):
            load()

        for filename, expected in (
            ("spell.json", "spell.json"),
            ("animation.txt", "animation.txt"),
            ("images/reference_obj_00.png", "images"),
        ):
            with self.subTest(filename=filename):
                cleanup()
                shutil.copytree(reference_dir, package_dir)
                path = os.path.join(package_dir, filename)
                os.unlink(path)
                os.symlink(os.path.join(reference_dir, filename), path)
                with self.assertRaisesRegex(ValueError, expected):
                    load()

    def test_png_accepts_legal_ancillary_and_consecutive_idat_chunks(self):
        path = os.path.join(TEST_ROOT, "ancillary-multi-idat.png")
        _write_png(
            path,
            240,
            64,
            ancillary_before_plte=((b"gAMA", struct.pack(">I", 45455)),),
            ancillary_before_idat=(
                (b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
                (b"bKGD", b"\0"),
                (b"aBCD", b""),
                (b"tEXt", b"source\0before"),
            ),
            ancillary_after_idat=(
                (b"tEXt", b"source\0after"),
                (b"tIME", b"\x07\xe8\x01\x01\0\0\0"),
            ),
            split_idat=True,
        )
        self.assertEqual(
            len(custom_spell.read_indexed_png(path, 240, 64)["pixels"]),
            240 * 64,
        )

    def test_png_rejects_unknown_critical_and_invalid_chunk_ordering(self):
        path = os.path.join(TEST_ROOT, "unknown-critical.png")
        _write_png(path, 240, 64, ancillary_before_idat=((b"ABCD", b""),))
        with self.assertRaisesRegex(ValueError, "unsupported critical"):
            custom_spell.read_indexed_png(path, 240, 64)

        width = 240
        height = 64
        scanlines = b"\0" * (height * (width // 2 + 1))
        compressed = zlib.compress(scanlines)
        ihdr = struct.pack(">IIBBBBB", width, height, 4, 3, 0, 0, 0)
        palette = b"\0\0\0\xff\xff\xff"
        midpoint = len(compressed) // 2
        _write_png_chunks(
            path,
            (
                (b"IHDR", ihdr),
                (b"PLTE", palette),
                (b"tRNS", b"\0\xff"),
                (b"IDAT", compressed[:midpoint]),
                (b"tEXt", b"split\0idat"),
                (b"IDAT", compressed[midpoint:]),
                (b"IEND", b""),
            ),
        )
        with self.assertRaisesRegex(ValueError, "IDAT chunks must be contiguous"):
            custom_spell.read_indexed_png(path, 240, 64)

        _write_png_chunks(
            path,
            (
                (b"IHDR", ihdr),
                (b"PLTE", palette),
                (b"IDAT", compressed),
                (b"tRNS", b"\0\xff"),
                (b"IEND", b""),
            ),
        )
        with self.assertRaisesRegex(ValueError, "tRNS must occur after PLTE and before IDAT"):
            custom_spell.read_indexed_png(path, 240, 64)

    def test_png_rejects_invalid_chunk_types_and_ancillary_placement(self):
        path = os.path.join(TEST_ROOT, "invalid-ancillary-placement.png")
        width = 240
        height = 64
        scanlines = b"\0" * (height * (width // 2 + 1))
        compressed = zlib.compress(scanlines)
        ihdr = struct.pack(">IIBBBBB", width, height, 4, 3, 0, 0, 0)
        palette = b"\0\0\0\xff\xff\xff"

        for chunk_type, message in (
            (b"gAmA", "reserved third letter"),
            (b"gA?A", "four ASCII letters"),
        ):
            with self.subTest(chunk_type=chunk_type):
                _write_png_chunks(
                    path,
                    (
                        (b"IHDR", ihdr),
                        (chunk_type, b""),
                        (b"PLTE", palette),
                        (b"tRNS", b"\0\xff"),
                        (b"IDAT", compressed),
                        (b"IEND", b""),
                    ),
                )
                with self.assertRaisesRegex(ValueError, message):
                    custom_spell.read_indexed_png(path, width, height)

    def test_known_ancillary_payloads_are_validated(self):
        path = os.path.join(TEST_ROOT, "known-ancillary.png")
        compressed = zlib.compress(b"profile")
        valid_cases = (
            (b"cHRM", b"\0" * 32, "before"),
            (b"gAMA", struct.pack(">I", 45455), "before"),
            (b"iCCP", b"profile\0\0" + compressed, "before"),
            (b"sBIT", b"\x08\x08\x08", "before"),
            (b"sRGB", b"\0", "before"),
            (b"pHYs", struct.pack(">IIB", 2835, 2835, 1), "between"),
            (b"bKGD", b"\1", "between"),
            (b"hIST", b"\0\1\0\2", "between"),
            (b"tEXt", b"note\0text", "between"),
            (b"zTXt", b"note\0\0" + zlib.compress(b"text"), "between"),
            (b"iTXt", b"note\0\0\0en\0title\0text", "between"),
            (b"tIME", b"\x07\xe8\x01\x01\0\0\0", "after"),
        )
        for name, payload, placement in valid_cases:
            with self.subTest(name=name):
                kwargs = {
                    "ancillary_before_plte": ((name, payload),)
                    if placement == "before"
                    else (),
                    "ancillary_before_idat": ((name, payload),)
                    if placement == "between"
                    else (),
                    "ancillary_after_idat": ((name, payload),)
                    if placement == "after"
                    else (),
                }
                _write_png(path, 240, 64, **kwargs)
                custom_spell.read_indexed_png(path, 240, 64)

        splt_one = b"one\0\x08\x01\x02\x03\xff\0\1"
        splt_two = b"two\0\x10\0\x01\0\x02\0\x03\0\xff\0\1"
        _write_png(
            path,
            240,
            64,
            ancillary_before_idat=((b"sPLT", splt_one), (b"sPLT", splt_two)),
        )
        custom_spell.read_indexed_png(path, 240, 64)

        invalid_cases = (
            (b"gAMA", b"\0\0\0\0", "before", "nonzero u32"),
            (b"cHRM", b"\0" * 31, "before", "eight u32"),
            (b"iCCP", b"profile\0\1data", "before", "method 0"),
            (b"sBIT", b"\0\x08\x08", "before", "1..8"),
            (b"sRGB", b"\4", "before", "0..3"),
            (b"pHYs", struct.pack(">IIB", 1, 1, 2), "between", "unit 0 or 1"),
            (b"sPLT", b"name\0\x08\x01", "between", "entries"),
            (b"bKGD", b"\2", "between", "PLTE entry"),
            (b"hIST", b"\0\1", "between", "one u16"),
            (b"tEXt", b"keyword", "between", "keyword terminator"),
            (b"zTXt", b"key\0\1text", "between", "method 0"),
            (b"iTXt", b"key\0\2\0en\0title\0text", "between", "compression fields"),
            (b"tIME", b"\x07\xe8\0\x01\0\0\0", "after", "UTC timestamp"),
        )
        for name, payload, placement, message in invalid_cases:
            with self.subTest(name=name):
                kwargs = {
                    "ancillary_before_plte": ((name, payload),)
                    if placement == "before"
                    else (),
                    "ancillary_before_idat": ((name, payload),)
                    if placement == "between"
                    else (),
                    "ancillary_after_idat": ((name, payload),)
                    if placement == "after"
                    else (),
                }
                _write_png(path, 240, 64, **kwargs)
                with self.assertRaisesRegex(ValueError, message):
                    custom_spell.read_indexed_png(path, 240, 64)

        _write_png(
            path,
            240,
            64,
            ancillary_before_idat=((b"sPLT", splt_one), (b"sPLT", splt_one)),
        )
        with self.assertRaisesRegex(ValueError, "suggested-palette name is duplicated"):
            custom_spell.read_indexed_png(path, 240, 64)

        strict_itxt_cases = (
            (
                b"note\0\0\0en us\0title\0text",
                "language tag has invalid grammar",
            ),
            (
                b"note\0\0\0en_US\0title\0text",
                "language tag has invalid grammar",
            ),
            (
                b"note\0\0\0en\0title\xff\0text",
                "translated keyword must be valid UTF-8",
            ),
            (
                b"note\0\0\0en\0title\0text\xff",
                "text must be valid UTF-8",
            ),
            (
                b"note\0\1\0en\0title\0" + zlib.compress(b"text\xff"),
                "text must be valid UTF-8",
            ),
        )
        for payload, message in strict_itxt_cases:
            with self.subTest(message=message):
                _write_png(
                    path,
                    240,
                    64,
                    ancillary_before_idat=((b"iTXt", payload),),
                )
                with self.assertRaisesRegex(ValueError, message):
                    custom_spell.read_indexed_png(path, 240, 64)

    def test_compressed_ancillary_streams_are_bounded_and_complete(self):
        path = os.path.join(TEST_ROOT, "compressed-ancillary.png")
        valid = zlib.compress(b"payload")
        cases = (
            (b"iCCP", b"profile\0\0", "before"),
            (b"zTXt", b"note\0\0", "between"),
            (b"iTXt", b"note\0\1\0en\0title\0", "between"),
        )
        invalid_streams = (
            (b"not-zlib", "invalid zlib stream"),
            (valid[:-1], "incomplete or trailing zlib stream"),
            (valid + b"trailing", "incomplete or trailing zlib stream"),
            (
                zlib.compress(
                    b"x" * (custom_spell.MAX_ANCILLARY_DECOMPRESSED_BYTES + 1)
                ),
                "decompression exceeds",
            ),
        )
        for name, prefix, placement in cases:
            for stream, message in invalid_streams:
                with self.subTest(name=name, message=message):
                    payload = prefix + stream
                    kwargs = {
                        "ancillary_before_plte": ((name, payload),)
                        if placement == "before"
                        else (),
                        "ancillary_before_idat": ((name, payload),)
                        if placement == "between"
                        else (),
                    }
                    _write_png(path, 240, 64, **kwargs)
                    with self.assertRaisesRegex(ValueError, message):
                        custom_spell.read_indexed_png(path, 240, 64)

        width = 240
        height = 64
        compressed = zlib.compress(b"\0" * (height * (width // 2 + 1)))
        ihdr = struct.pack(">IIBBBBB", width, height, 4, 3, 0, 0, 0)
        palette = b"\0\0\0\xff\xff\xff"
        cases = (
            (
                "gamma-after-plte",
                (
                    (b"IHDR", ihdr),
                    (b"PLTE", palette),
                    (b"gAMA", struct.pack(">I", 45455)),
                    (b"tRNS", b"\0\xff"),
                    (b"IDAT", compressed),
                    (b"IEND", b""),
                ),
                "gAMA must occur before PLTE and IDAT",
            ),
            (
                "gamma-after-idat",
                (
                    (b"IHDR", ihdr),
                    (b"PLTE", palette),
                    (b"tRNS", b"\0\xff"),
                    (b"IDAT", compressed),
                    (b"gAMA", struct.pack(">I", 45455)),
                    (b"IEND", b""),
                ),
                "gAMA must occur before PLTE and IDAT",
            ),
            (
                "background-before-plte",
                (
                    (b"IHDR", ihdr),
                    (b"bKGD", b"\0"),
                    (b"PLTE", palette),
                    (b"tRNS", b"\0\xff"),
                    (b"IDAT", compressed),
                    (b"IEND", b""),
                ),
                "bKGD must occur after PLTE and before IDAT",
            ),
            (
                "physical-after-idat",
                (
                    (b"IHDR", ihdr),
                    (b"PLTE", palette),
                    (b"tRNS", b"\0\xff"),
                    (b"IDAT", compressed),
                    (b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
                    (b"IEND", b""),
                ),
                "pHYs must occur before IDAT",
            ),
        )
        for name, chunks, message in cases:
            with self.subTest(name=name):
                _write_png_chunks(path, chunks)
                with self.assertRaisesRegex(ValueError, message):
                    custom_spell.read_indexed_png(path, width, height)

    def test_png_rejects_stream_truncated_immediately_after_idat(self):
        path = os.path.join(TEST_ROOT, "truncated-after-idat.png")
        _write_png(path, 240, 64)
        with open(path, "rb") as handle:
            encoded = handle.read()
        terminal_iend = _chunk(b"IEND", b"")
        self.assertTrue(encoded.endswith(terminal_iend))
        with open(path, "wb") as handle:
            handle.write(encoded[:-len(terminal_iend)])
        with self.assertRaisesRegex(ValueError, "must end with IEND"):
            custom_spell.read_indexed_png(path, 240, 64)

    def test_public_png_contract_matches_parser(self):
        with open(
            os.path.join(ROOT, "docs", "custom_spell_effects.md"),
            encoding="utf-8",
        ) as handle:
            documentation = " ".join(handle.read().split())
        for phrase in (
            "used nonzero palette index must be opaque",
            "unused nonzero palette entries may have any `tRNS` alpha",
            "one or more consecutive `IDAT` chunks",
            "Other syntactically valid ancillary chunks may appear",
            "ends with a zero-payload `IEND`",
            "uppercase reserved third letter",
            "`cHRM`/`gAMA`/`iCCP`/`sBIT`/`sRGB` only before `PLTE`",
            "`sPLT` may repeat only with distinct suggested-palette names",
        ):
            self.assertIn(phrase, documentation)

    def test_background_scaling_and_tsa_vectors(self):
        pixels = bytearray()
        for source_y in range(custom_spell.BG_HEIGHT):
            pixels.extend([(source_y % 15) + 1] * custom_spell.BG_WIDTH)
        png = {
            "pixels": bytes(pixels),
            "palette": ((0, 0, 0),) * 16,
        }
        scaled = custom_spell._scale_bg(png)

        def source_row(row):
            start = row * custom_spell.BG_WIDTH
            return bytes(pixels[start:start + custom_spell.BG_WIDTH])

        for output_y, expected_source_y in (
            (0, 0),
            (1, 0),
            (2, 1),
            (79, 32),
            (80, 32),
            (157, 63),
            (158, 63),
        ):
            start = output_y * custom_spell.BG_WIDTH
            self.assertEqual(
                scaled[start:start + custom_spell.BG_WIDTH],
                source_row(expected_source_y),
            )
        self.assertEqual(
            scaled[159 * custom_spell.BG_WIDTH:],
            bytes(custom_spell.BG_WIDTH),
        )

        _, tsa = custom_spell._pack_bg(png)
        entries = [
            int.from_bytes(tsa[offset:offset + 2], "little")
            for offset in range(0, len(tsa), 2)
        ]
        self.assertEqual(
            entries,
            [
                tile_index
                for tile_index in range(1, 21)
                for _tile_x in range(30)
            ],
        )

    def test_generated_source_stamp_handles_supported_source_paths(self):
        package_dir = os.path.join(
            ROOT, "scripts", "assets", "tests", ".custom spell #$%:|; package"
        )
        self.addCleanup(shutil.rmtree, package_dir, ignore_errors=True)
        shutil.copytree(
            os.path.join(ROOT, "graphics", "custom_spell", "reference"),
            package_dir,
        )
        relative_package = os.path.relpath(package_dir, ROOT).replace(os.sep, "/")
        record = copy.deepcopy(self.reference_record())
        record["sources"] = [
            relative_package + "/spell.json",
            relative_package + "/animation.txt",
            relative_package + "/images/reference_obj_00.png",
            relative_package + "/images/reference_bg_00.png",
            relative_package + "/images/reference_obj_01.png",
            relative_package + "/images/reference_bg_01.png",
        ]
        source_manifest = os.path.join(TEST_ROOT, "special-path-manifest.json")
        with open(source_manifest, "w", encoding="utf-8") as handle:
            json.dump({"schemaVersion": 1, "assets": [record]}, handle)
        def tracked_paths(command, **_kwargs):
            paths = command[command.index("--") + 1:]
            return SimpleNamespace(
                returncode=0,
                stdout=b"\0".join(path.encode("utf-8") for path in paths) + b"\0",
                stderr=b"",
            )

        with mock.patch.object(manifest.subprocess, "run", side_effect=tracked_paths):
            records = manifest.load_and_validate(source_manifest, 1)

        out_dir = os.path.join(TEST_ROOT, "special-path-output")
        outputs = custom_spell.output_paths(records, out_dir)
        source_stamp = os.path.join(TEST_ROOT, "special-path-sources.stamp")
        with open(source_stamp, "w", encoding="utf-8"):
            pass
        fragment = os.path.join(TEST_ROOT, "special-path-rules.mk")
        with open(fragment, "w", encoding="utf-8") as handle:
            handle.write(
                "ASSET_MANIFEST_SOURCE_STAMP := {}\n".format(source_stamp)
                + manifest.render_makefile(records)
            )
        tool = os.path.join(TEST_ROOT, "touch-custom-spell-outputs.py")
        with open(tool, "w", encoding="utf-8") as handle:
            handle.write(
                "import os\n"
                "from pathlib import Path\n"
                "for output in {}:\n"
                "    Path(output).parent.mkdir(parents=True, exist_ok=True)\n"
                "    Path(output).touch()\n".format(repr(outputs))
            )
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-f",
                fragment,
                outputs[0],
                "ASSET_OUTPUT_DIR={}".format(out_dir),
                "ASSET_OUTPUT_MK={}".format(fragment),
                "ASSET_TOOL={} {}".format(sys.executable, tool),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(all(os.path.isfile(output) for output in outputs))

    def test_assets_make_discovers_and_builds_special_source_paths(self):
        package_dir = os.path.join(
            ROOT, "scripts", "assets", "tests", ".assets make #$%:|; package"
        )
        source_manifest = os.path.join(TEST_ROOT, "assets-make-special.json")
        git_wrapper = os.path.join(TEST_ROOT, "git")
        fragment = "build/generated/assets/asset_manifest.mk"
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        shutil.copytree(
            os.path.join(ROOT, "graphics", "custom_spell", "reference"),
            package_dir,
        )
        relative_package = os.path.relpath(package_dir, ROOT).replace(os.sep, "/")
        record = copy.deepcopy(self.reference_record())
        record["sources"] = [
            relative_package + "/spell.json",
            relative_package + "/animation.txt",
            relative_package + "/images/reference_obj_00.png",
            relative_package + "/images/reference_bg_00.png",
            relative_package + "/images/reference_obj_01.png",
            relative_package + "/images/reference_bg_01.png",
        ]
        with open(source_manifest, "w", encoding="utf-8") as handle:
            json.dump({"schemaVersion": 1, "assets": [record]}, handle)
        with open(git_wrapper, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/bin/sh\n"
                "for arg in \"$@\"; do\n"
                "    if [ \"$arg\" = \"ls-files\" ]; then\n"
                "        after_separator=0\n"
                "        for path in \"$@\"; do\n"
                "            if [ \"$path\" = \"--\" ]; then\n"
                "                after_separator=1\n"
                "            elif [ \"$after_separator\" = 1 ]; then\n"
                "                printf '%s\\000' \"$path\"\n"
                "            fi\n"
                "        done\n"
                "        exit 0\n"
                "    fi\n"
                "done\n"
                "exec \"{}\" \"$@\"\n".format(real_git)
            )
        os.chmod(git_wrapper, 0o755)
        environment = os.environ.copy()
        environment["PATH"] = TEST_ROOT + os.pathsep + environment["PATH"]

        def run(manifest_path, enabled, *goals, env=None):
            return subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-f",
                    "assets.mk",
                    *goals,
                    "PYTHON={}".format(sys.executable),
                    "ASSET_MANIFEST={}".format(manifest_path),
                    "EXPANSION_CUSTOM_SPELL_EFFECTS={}".format(enabled),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

        try:
            generated = run(source_manifest, 1, fragment, env=environment)
            self.assertEqual(
                generated.returncode, 0, generated.stdout + generated.stderr
            )
            self.assertTrue(
                os.path.isfile(
                    os.path.join(
                        ROOT,
                        "build",
                        "generated",
                        "assets",
                        "custom_spell",
                        "custom_spell_effect_data.inc",
                    )
                )
            )
            source_path = os.path.join(ROOT, record["sources"][0])
            source_stat = os.stat(source_path)
            os.utime(
                source_path,
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns + 1_000_000_000),
            )
            rebuilt = run(source_manifest, 1, fragment, env=environment)
            self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
            self.assertIn("OK: generated", rebuilt.stdout)
        finally:
            shutil.rmtree(package_dir, ignore_errors=True)
            restored = run(DEFAULT_MANIFEST, 0, fragment)
            if restored.returncode != 0:
                raise AssertionError(restored.stdout + restored.stderr)

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

    def test_public_effect_symbol_collisions_are_source_located(self):
        with open(REFERENCE_MANIFEST, encoding="utf-8") as handle:
            document = json.load(handle)
        record = next(
            row for row in document["assets"]
            if row["kind"] == "custom-spell-effect"
        )
        path = os.path.join(TEST_ROOT, "manifest.json")
        for symbol in (
            "CUSTOM_SPELL_EFFECT_BASE",
            "CUSTOM_SPELL_EFFECT_TEST_PROBE_MAGIC",
            "CUSTOM_SPELL_EFFECT_FALLBACK_INVALID",
        ):
            with self.subTest(symbol=symbol):
                changed = copy.deepcopy(document)
                target = next(
                    row for row in changed["assets"]
                    if row["kind"] == "custom-spell-effect"
                )
                target["ownership"]["effectSymbol"] = symbol
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(changed, handle)
                with self.assertRaises(GeneratedDataValidationError) as raised:
                    manifest.load_and_validate(path, 1)
                diagnostic = str(raised.exception)
                self.assertIn(path, diagnostic)
                self.assertIn("ownership.effectSymbol", diagnostic)
                self.assertIn("collides with a public/test", diagnostic)

    def test_invalid_ownership_stops_before_conversion_or_access(self):
        with open(REFERENCE_MANIFEST, encoding="utf-8") as handle:
            document = json.load(handle)
        path = os.path.join(TEST_ROOT, "manifest.json")

        changed = copy.deepcopy(document)
        record = next(
            row for row in changed["assets"]
            if row["kind"] == "custom-spell-effect"
        )
        record["ownership"]["spellAssociationSource"] = "../outside.c"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(changed, handle)
        with (
            mock.patch.object(custom_spell, "validate_runtime_binding") as binding,
            mock.patch.object(custom_spell, "load_package") as loader,
        ):
            with self.assertRaisesRegex(
                GeneratedDataValidationError,
                "ownership.spellAssociationSource must be",
            ):
                manifest.load_and_validate(path, 1)
        binding.assert_not_called()
        loader.assert_not_called()

        changed = copy.deepcopy(document)
        record = next(
            row for row in changed["assets"]
            if row["kind"] == "custom-spell-effect"
        )
        record["ownership"]["effectSymbol"] = 7
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(changed, handle)
        with (
            mock.patch.object(custom_spell, "_spell_fallbacks") as fallbacks,
            mock.patch.object(custom_spell, "load_package") as loader,
        ):
            with self.assertRaisesRegex(
                GeneratedDataValidationError,
                "ownership.effectSymbol must be a CUSTOM_SPELL",
            ):
                manifest.load_and_validate(path, 1)
        fallbacks.assert_not_called()
        loader.assert_not_called()

    def test_rejected_sources_never_load_custom_package(self):
        with open(REFERENCE_MANIFEST, encoding="utf-8") as handle:
            document = json.load(handle)
        path = os.path.join(TEST_ROOT, "manifest.json")
        for rejected_source in ("/outside/spell.json", "../outside/spell.json"):
            with self.subTest(source=rejected_source):
                changed = copy.deepcopy(document)
                record = next(
                    row for row in changed["assets"]
                    if row["kind"] == "custom-spell-effect"
                )
                record["sources"][0] = rejected_source
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(changed, handle)
                with (
                    mock.patch.object(custom_spell, "load_package") as loader,
                    mock.patch.object(
                        custom_spell, "read_indexed_png"
                    ) as image_reader,
                ):
                    with self.assertRaises(GeneratedDataValidationError):
                        manifest.load_and_validate(path, 1)
                loader.assert_not_called()
                image_reader.assert_not_called()

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
        manifest.generate(REFERENCE_MANIFEST, output, 1)
        with open(
            os.path.join(output, "unexpected.manifest-discovery.mk"),
            "w",
            encoding="utf-8",
        ):
            pass
        with self.assertRaisesRegex(
            GeneratedDataValidationError, "orphan generated output"
        ):
            manifest.check(REFERENCE_MANIFEST, output, 1)

    def test_parallel_generation_shares_one_custom_output_owner(self):
        output = os.path.join(TEST_ROOT, "parallel")
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    manifest.generate,
                    REFERENCE_MANIFEST,
                    output,
                    1,
                )
                for _ in range(2)
            ]
            for future in futures:
                future.result()
        manifest.check(REFERENCE_MANIFEST, output, 1)

    def test_custom_incbin_consumer_uses_isolated_output_override(self):
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
                "assets-generate",
                "assets-check",
                "PYTHON={}".format(sys.executable),
                "EXPANSION_CUSTOM_SPELL_EFFECTS=1",
                "ASSET_MANIFEST={}".format(manifest_path),
                "ASSET_OUTPUT_DIR=build/generated/assets/custom-spell-test/custom-spell-override",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    ROOT,
                    "build",
                    "generated",
                    "assets",
                    "custom-spell-test",
                    "custom-spell-override",
                    "custom_spell",
                    "custom_spell_effect_data.inc",
                )
            )
        )

    def test_discovery_queries_skip_conversion_for_16x64_profile(self):
        with open(REFERENCE_MANIFEST, encoding="utf-8") as handle:
            document = json.load(handle)
        template = next(
            record for record in document["assets"]
            if record["kind"] == "custom-spell-effect"
        )
        document["assets"] = []
        for index in range(16):
            record = copy.deepcopy(template)
            record_id = "CUSTOM_SPELL_QUERY_{:02d}".format(index)
            package = "synthetic/custom_spell_{:02d}".format(index)
            record["id"] = record_id
            record["ownership"]["item"] = "ITEM_QUERY_{:02d}".format(index)
            record["ownership"]["effectSymbol"] = record_id
            record["sources"] = [
                package + "/spell.json",
                package + "/animation.txt",
            ]
            for frame in range(64):
                record["sources"].extend(
                    (
                        package + "/images/obj_{:02d}.png".format(frame),
                        package + "/images/bg_{:02d}.png".format(frame),
                    )
                )
            document["assets"].append(record)
        manifest_path = os.path.join(TEST_ROOT, "query-profile.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)

        artifact = os.path.join(TEST_ROOT, "query-profile.mk")
        with (
            mock.patch.object(
                manifest, "load_manifest", wraps=manifest.load_manifest
            ) as discover,
            mock.patch.object(
                manifest, "_repo_path", side_effect=lambda path, *_, **__: path
            ),
            mock.patch.object(manifest, "_validate_tracked_paths"),
            mock.patch.object(
                manifest,
                "render_source_stamp",
                return_value='{"sources": []}\n',
            ),
            mock.patch.object(custom_spell, "load_package") as convert,
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    cli.main(
                        [
                            "--custom-spell-effects",
                            "1",
                            "--item-id-cap",
                            "0xCD",
                            "--manifest",
                            manifest_path,
                            "--discovery-makefile",
                            artifact,
                            "discovery-makefile",
                        ]
                    ),
                    0,
                )
        self.assertEqual(discover.call_count, 1)
        convert.assert_not_called()
        with open(artifact, encoding="utf-8") as handle:
            discovery = handle.read()
        self.assertEqual(
            len(
                [
                    record_id for record_id in discovery.split()
                    if record_id.startswith("CUSTOM_SPELL_QUERY_")
                ]
            ),
            16,
        )

    def test_discovery_makefile_batches_real_16x64_profile_sources(self):
        fixture_root = os.path.join(
            ROOT, "scripts", "assets", "tests", ".discovery-16x64-profile"
        )
        artifact = os.path.join(TEST_ROOT, "discovery.mk")
        manifest_path = os.path.join(TEST_ROOT, "discovery.json")
        git_wrapper = os.path.join(TEST_ROOT, "git")
        git_calls = os.path.join(TEST_ROOT, "git-calls")
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        ref_images = os.path.join(ROOT, "graphics", "custom_spell", "reference", "images")
        document = {"schemaVersion": 1, "assets": []}
        template = self.reference_record()

        for index in range(16):
            package = os.path.join(fixture_root, "effect_{:02d}".format(index))
            images = os.path.join(package, "images")
            os.makedirs(images)
            with open(os.path.join(package, "spell.json"), "w", encoding="utf-8") as handle:
                json.dump({"schemaVersion": 1, "soundTable": []}, handle)
            with open(os.path.join(package, "animation.txt"), "w", encoding="utf-8") as handle:
                for frame in range(64):
                    handle.write(
                        "O p- obj_{0:02d}.png\nB p- bg_{0:02d}.png\n1\n".format(frame)
                    )
                handle.write("~~~\n")
            record = copy.deepcopy(template)
            record["id"] = "CUSTOM_SPELL_DISCOVERY_{:02d}".format(index)
            record["ownership"]["item"] = "ITEM_DISCOVERY_{:02d}".format(index)
            record["ownership"]["effectSymbol"] = record["id"]
            relative = os.path.relpath(package, ROOT).replace(os.sep, "/")
            record["sources"] = [relative + "/spell.json", relative + "/animation.txt"]
            for frame in range(64):
                for role, source in (
                    ("obj", "reference_obj_00.png"),
                    ("bg", "reference_bg_00.png"),
                ):
                    name = "{}_{:02d}.png".format(role, frame)
                    os.link(os.path.join(ref_images, source), os.path.join(images, name))
                    record["sources"].append(relative + "/images/" + name)
            document["assets"].append(record)

        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        with open(git_wrapper, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/bin/sh\n"
                "for arg in \"$@\"; do\n"
                "    if [ \"$arg\" = \"ls-files\" ]; then\n"
                "        printf 'x\\n' >> \"{}\"\n"
                "        after_separator=0\n"
                "        for path in \"$@\"; do\n"
                "            if [ \"$path\" = \"--\" ]; then\n"
                "                after_separator=1\n"
                "            elif [ \"$after_separator\" = 1 ]; then\n"
                "                printf '%s\\000' \"$path\"\n"
                "            fi\n"
                "        done\n"
                "        exit 0\n"
                "    fi\n"
                "done\n"
                "exec \"{}\" \"$@\"\n".format(git_calls, real_git)
            )
        os.chmod(git_wrapper, 0o755)
        environment = os.environ.copy()
        environment["PATH"] = TEST_ROOT + os.pathsep + environment["PATH"]

        try:
            started = time.monotonic()
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.assets",
                    "--item-id-cap",
                    "0xCD",
                    "--manifest",
                    manifest_path,
                    "--discovery-makefile",
                    artifact,
                    "discovery-makefile",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertLess(elapsed, 10)
            with open(git_calls, encoding="utf-8") as handle:
                self.assertEqual(handle.read().splitlines(), ["x"])
            with open(artifact, encoding="utf-8") as handle:
                discovery = handle.read()
            self.assertIn("ASSET_MANIFEST_SOURCE_DIGEST :=", discovery)
            for index in range(16):
                self.assertIn(
                    "CUSTOM_SPELL_DISCOVERY_{:02d}".format(index),
                    discovery,
                )
        finally:
            shutil.rmtree(fixture_root, ignore_errors=True)

    def test_default_to_reference_warm_build_regenerates_custom_bindings(self):
        output = os.path.join(ROOT, "build", "generated", "assets")
        fragment = os.path.join(output, "asset_manifest.mk")
        custom_dir = os.path.join(output, "custom_spell")
        data_include = os.path.join(
            custom_dir, "custom_spell_effect_data.inc"
        )

        def run(manifest_path, enabled, *goals):
            return subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-f",
                    "assets.mk",
                    *goals,
                    "PYTHON={}".format(sys.executable),
                    "ASSET_MANIFEST={}".format(manifest_path),
                    "EXPANSION_CUSTOM_SPELL_EFFECTS={}".format(enabled),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        try:
            cleaned = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-f",
                    "assets.mk",
                    "assets-clean",
                    "PYTHON={}".format(sys.executable),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cleaned.returncode, 0, cleaned.stdout + cleaned.stderr)
            default = run(DEFAULT_MANIFEST, 0, fragment)
            self.assertEqual(
                default.returncode, 0, default.stdout + default.stderr
            )
            with open(fragment, encoding="utf-8") as handle:
                self.assertNotIn("custom_spell_effect_data.inc", handle.read())
            shutil.rmtree(custom_dir, ignore_errors=True)
            self.assertFalse(os.path.exists(data_include))

            reference = run(REFERENCE_MANIFEST, 1, data_include)
            self.assertEqual(
                reference.returncode, 0, reference.stdout + reference.stderr
            )
            self.assertTrue(os.path.isfile(data_include))
            with open(fragment, encoding="utf-8") as handle:
                self.assertIn("custom_spell_effect_data.inc", handle.read())

            shutil.rmtree(TEST_ROOT, ignore_errors=True)
            checked = run(REFERENCE_MANIFEST, 1, "assets-check")
            self.assertEqual(
                checked.returncode, 0, checked.stdout + checked.stderr
            )

            default = run(DEFAULT_MANIFEST, 0, fragment)
            self.assertEqual(
                default.returncode, 0, default.stdout + default.stderr
            )
            self.assertFalse(os.path.exists(custom_dir))
            checked = run(DEFAULT_MANIFEST, 0, "assets-check")
            self.assertEqual(
                checked.returncode, 0, checked.stdout + checked.stderr
            )

            os.makedirs(PROFILE_TEST_ROOT)
            alternate_manifest = os.path.join(
                PROFILE_TEST_ROOT, "alternate-custom-spell-manifest.json"
            )
            shutil.copyfile(REFERENCE_MANIFEST, alternate_manifest)
            alternate = run(alternate_manifest, 1, data_include)
            self.assertEqual(
                alternate.returncode, 0, alternate.stdout + alternate.stderr
            )
            checked = run(alternate_manifest, 1, "assets-check")
            self.assertEqual(
                checked.returncode, 0, checked.stdout + checked.stderr
            )
        finally:
            shutil.rmtree(custom_dir, ignore_errors=True)
            shutil.rmtree(PROFILE_TEST_ROOT, ignore_errors=True)
            restored = run(DEFAULT_MANIFEST, 0, fragment)
            if restored.returncode != 0:
                raise AssertionError(restored.stdout + restored.stderr)

    def test_custom_dependency_touch_regenerates_outputs(self):
        output = os.path.join(ROOT, "build", "generated", "assets")
        fragment = os.path.join(output, "asset_manifest.mk")
        source_stamp = os.path.join(
            ROOT,
            "build",
            "generated",
            "asset-discovery",
            "build_generated_assets.mk",
        )
        stale_source_stamp = output + ".manifest-discovery.mk"
        custom_dir = os.path.join(output, "custom_spell")
        data_include = os.path.join(
            custom_dir, "custom_spell_effect_data.inc"
        )
        dependency = os.path.join(ROOT, "include", "spellassoc.h")

        def run(*goals):
            return subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-f",
                    "assets.mk",
                    *goals,
                    "PYTHON={}".format(sys.executable),
                    "ASSET_MANIFEST={}".format(REFERENCE_MANIFEST),
                    "EXPANSION_CUSTOM_SPELL_EFFECTS=1",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        original = os.stat(dependency)
        try:
            generated = run(data_include)
            self.assertEqual(
                generated.returncode, 0, generated.stdout + generated.stderr
            )
            with open(source_stamp, encoding="utf-8") as handle:
                self.assertIn("ASSET_MANIFEST_SOURCE_DIGEST :=", handle.read())
            os.unlink(source_stamp)
            with open(stale_source_stamp, "w", encoding="utf-8") as handle:
                handle.write("ASSET_MANIFEST_SOURCE_DIGEST := stale\n")
            regenerated = run(data_include)
            self.assertEqual(
                regenerated.returncode, 0, regenerated.stdout + regenerated.stderr
            )
            with open(source_stamp, encoding="utf-8") as handle:
                self.assertIn("ASSET_MANIFEST_SOURCE_DIGEST :=", handle.read())
            with open(stale_source_stamp, encoding="utf-8") as handle:
                self.assertEqual(
                    handle.read(),
                    "ASSET_MANIFEST_SOURCE_DIGEST := stale\n",
                )

            os.utime(
                dependency,
                ns=(
                    original.st_atime_ns,
                    max(original.st_mtime_ns, time.time_ns()) + 2_000_000_000,
                ),
            )
            regenerated = run(data_include)
            self.assertEqual(
                regenerated.returncode, 0, regenerated.stdout + regenerated.stderr
            )
            self.assertIn("scripts.assets", regenerated.stdout)
        finally:
            os.utime(
                dependency, ns=(original.st_atime_ns, original.st_mtime_ns)
            )
            if os.path.exists(stale_source_stamp):
                os.unlink(stale_source_stamp)
            shutil.rmtree(custom_dir, ignore_errors=True)
            restored = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-f",
                    "assets.mk",
                    fragment,
                    "PYTHON={}".format(sys.executable),
                    "ASSET_MANIFEST={}".format(DEFAULT_MANIFEST),
                    "EXPANSION_CUSTOM_SPELL_EFFECTS=0",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            if restored.returncode != 0:
                raise AssertionError(restored.stdout + restored.stderr)

    def test_concurrent_make_profile_generation_keeps_one_coherent_output_tree(self):
        output = os.path.join(ROOT, "build", "generated", "assets")

        def run(manifest_path, enabled):
            return subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-f",
                    "assets.mk",
                    "assets-generate",
                    "PYTHON={}".format(sys.executable),
                    "ASSET_MANIFEST={}".format(manifest_path),
                    "EXPANSION_CUSTOM_SPELL_EFFECTS={}".format(enabled),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        try:
            cleaned = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-f",
                    "assets.mk",
                    "assets-clean",
                    "PYTHON={}".format(sys.executable),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cleaned.returncode, 0, cleaned.stdout + cleaned.stderr)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = tuple(
                    future.result()
                    for future in (
                        executor.submit(run, REFERENCE_MANIFEST, 1),
                        executor.submit(run, DEFAULT_MANIFEST, 0),
                    )
                )
            for result in results:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            custom_dir = os.path.join(output, "custom_spell")
            if os.path.isdir(custom_dir):
                manifest.check(REFERENCE_MANIFEST, output, 1, item_id_cap=0xCD)
                expected_selection = "custom_spell_effects=1"
            else:
                manifest.check(DEFAULT_MANIFEST, output, 0, item_id_cap=0xCD)
                expected_selection = "custom_spell_effects=0"
            with open(output + ".manifest-selection", encoding="utf-8") as handle:
                self.assertIn(expected_selection, handle.read())
        finally:
            restored = run(DEFAULT_MANIFEST, 0)
            if restored.returncode != 0:
                raise AssertionError(restored.stdout + restored.stderr)

    def test_concurrent_make_selection_stamp_uses_unique_temporary_files(self):
        manifest_path = os.path.join(TEST_ROOT, "empty-manifest.json")
        output = "build/generated/assets/test-work/concurrent-selection-stamp"
        stamp = os.path.join(ROOT, output + ".manifest-selection")
        discovery = os.path.join(
            ROOT,
            "build",
            "generated",
            "asset-discovery",
            "build_generated_assets_test-work_concurrent-selection-stamp.mk",
        )
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump({"schemaVersion": 1, "assets": []}, handle)

        def run(enabled):
            return subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-f",
                    "assets.mk",
                    output + ".manifest-selection",
                    "PYTHON={}".format(sys.executable),
                    "ASSET_MANIFEST={}".format(manifest_path),
                    "ASSET_OUTPUT_DIR={}".format(output),
                    "EXPANSION_CUSTOM_SPELL_EFFECTS={}".format(enabled),
                    "FE8_ITEM_ID_CAP=0xCE",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = tuple(
                    future.result()
                    for future in (
                        executor.submit(run, 0),
                        executor.submit(run, 1),
                    )
                )
            for result in results:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with open(stamp, encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("item_id_cap=0xCE", content)
            self.assertIn(
                content,
                (
                    "manifest={}\ncustom_spell_effects=0\nitem_id_cap=0xCE\n".format(
                        os.path.abspath(manifest_path)
                    ),
                    "manifest={}\ncustom_spell_effects=1\nitem_id_cap=0xCE\n".format(
                        os.path.abspath(manifest_path)
                    ),
                ),
            )
            self.assertFalse(os.path.exists(stamp + ".tmp"))
            self.assertEqual(
                [
                    name
                    for name in os.listdir(os.path.dirname(stamp))
                    if name.startswith(os.path.basename(stamp) + ".")
                    and name.endswith(".tmp")
                ],
                [],
            )
        finally:
            shutil.rmtree(os.path.join(ROOT, output), ignore_errors=True)
            if os.path.exists(stamp):
                os.unlink(stamp)
            lock = os.path.join(
                ROOT, output + ".asset-manifest-generate.lock"
            )
            if os.path.exists(lock):
                os.unlink(lock)
            if os.path.exists(discovery):
                os.unlink(discovery)
            shutil.rmtree(
                os.path.join(ROOT, "build", "generated", "assets", "test-work"),
                ignore_errors=True,
            )

    def test_dense_index_allocation_is_sorted_and_capacity_bounded(self):
        package = self.load_reference()

        def record(record_id, item, runtime_bytes=None):
            copied_package = copy.deepcopy(package)
            if runtime_bytes is not None:
                copied_package.runtime_bytes = runtime_bytes
            return SimpleNamespace(
                id=record_id,
                custom_spell_package=copied_package,
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
        with self.assertRaisesRegex(
            ValueError, "aggregate custom spell runtime payload .* exceeds 0x40000"
        ):
            custom_spell.validate_collection(
                [
                    record(
                        "ROM{:02d}".format(index),
                        "ITEM_ROM_{:02d}".format(index),
                        custom_spell.MAX_ROM_BYTES // 8 + 1,
                    )
                    for index in range(8)
                ]
            )
        association_only_overflow = [
            record(
                "ASSOC{:02d}".format(index),
                "ITEM_ASSOC_{:02d}".format(index),
                custom_spell.MAX_ROM_BYTES // custom_spell.MAX_EFFECTS,
            )
            for index in range(custom_spell.MAX_EFFECTS)
        ]
        for runtime_record in association_only_overflow:
            runtime_record.custom_spell_package.runtime_bytes += (
                custom_spell.SPELL_ASSOC_ENTRY_BYTES
            )
        with self.assertRaisesRegex(
            ValueError, "aggregate custom spell runtime payload .* exceeds 0x40000"
        ):
            custom_spell.validate_collection(association_only_overflow)
        duplicate_item = [
            record("FIRST", "ITEM_ANIMA_FORBLAZE"),
            record("SECOND", "ITEM_ANIMA_FORBLAZE"),
        ]
        with self.assertRaisesRegex(ValueError, "duplicate custom spell item"):
            custom_spell.validate_collection(duplicate_item)

    def test_lz77_matches_reference_vectors_within_profile_bound(self):
        vectors = (
            b"",
            b"A",
            b"ABCABCABCABCABCABC",
            bytes(range(32)) * 4,
            b"ABCD" * 128 + b"EFGH" * 128,
        )
        for vector in vectors:
            with self.subTest(size=len(vector)):
                self.assertEqual(
                    custom_spell.gba_lz77(vector), _reference_gba_lz77(vector)
                )

        payload = bytes(range(256)) * 16
        expected = custom_spell.gba_lz77(payload)
        started = time.monotonic()
        for _effect in range(custom_spell.MAX_EFFECTS):
            for _frame in range(custom_spell.MAX_FRAMES):
                self.assertEqual(custom_spell.gba_lz77(payload), expected)
        self.assertLess(time.monotonic() - started, 20)


if __name__ == "__main__":
    unittest.main()
