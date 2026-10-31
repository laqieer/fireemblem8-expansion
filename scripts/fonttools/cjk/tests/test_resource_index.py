import base64
import hashlib
import json
import lzma
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

from scripts.fonttools.cjk.inventory import CjkFontError, check_generated_files
from scripts.fonttools.cjk.package import FEHRR_SOURCES, _read_fehrr_lock, check_compact_assets
from scripts.fonttools.cjk.resource_index import (
    ENVELOPE_KIND,
    MAX_INDEX_BYTES,
    ResourceIndexError,
    decode_index,
    encode_index,
    encode_like_index,
)


ROOT = Path(__file__).resolve().parents[4]


class ResourceIndexTests(unittest.TestCase):
    def test_fehrr_fresh_plain_roots_produce_identical_packed_outputs(self):
        outputs = []
        with tempfile.TemporaryDirectory() as temporary:
            for name in ("first", "second"):
                root = Path(temporary) / name
                for relative in ("fonts/cjk", "graphics/fonts/cjk"):
                    shutil.copytree(ROOT / relative, root / relative)
                for representation in ("plain", "packed"):
                    result = subprocess.run(
                        [
                            sys.executable, "-B", "-m", "scripts.fonttools.cjk",
                            "--root", str(root), "repack-index",
                            "--index", "fehrr", "--format", representation,
                        ],
                        cwd=ROOT, capture_output=True, text=True, timeout=180,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    if representation == "plain":
                        plain_checksum = hashlib.sha256((root / FEHRR_SOURCES).read_bytes()).hexdigest()
                manifest_path = root / "graphics/fonts/cjk/manifest.json"
                valid_manifest = manifest_path.read_bytes()
                stale = json.loads(valid_manifest)
                stale["sources"]["fehrr_priority"]["sha256"] = plain_checksum
                manifest_path.write_bytes(encode_index(stale))
                paths = {
                    FEHRR_SOURCES, "graphics/fonts/cjk/manifest.json",
                    *(asset[kind]["path"] for asset in stale["assets"].values()
                      for kind in ("bitmap", "codepoints", "widths")),
                }
                before = {path: (root / path).read_bytes() for path in paths}
                with self.assertRaisesRegex(CjkFontError, "FEHRR priority provenance drifted"):
                    check_compact_assets(root)
                for path, data in before.items():
                    self.assertTrue((root / path).read_bytes() == data, path + " changed on refusal")
                manifest_path.write_bytes(valid_manifest)
                check_compact_assets(root)
                outputs.append({
                    path: (root / path).read_bytes()
                    for path in (FEHRR_SOURCES, "graphics/fonts/cjk/manifest.json")
                })
        for path in outputs[0]:
            self.assertTrue(outputs[0][path] == outputs[1][path], path + " packed outputs differ")

    def test_unpaired_surrogate_keys_and_values_fail_through_the_typed_boundary(self):
        cases = (b'{"value":"\\ud800"}', b'{"\\udfff":"value"}', b'{"value":["\\ud800"]}')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "index.json"
            previous = encode_index({"value": "unchanged"}, packed=True)
            for plain in cases:
                wrapped = json.dumps({
                    "kind": ENVELOPE_KIND, "version": 1, "encoding": "xz-base64",
                    "decoded_bytes": len(plain),
                    "payload": base64.b64encode(lzma.compress(plain)).decode(),
                }).encode()
                for stored in (plain, wrapped):
                    with self.subTest(operation="decode", plain=plain, packed=stored is wrapped):
                        with self.assertRaises(ResourceIndexError):
                            decode_index(stored)
                value = json.loads(plain)
                for packed in (False, True):
                    with self.subTest(operation="encode", plain=plain, packed=packed):
                        with self.assertRaises(ResourceIndexError):
                            encode_index(value, packed=packed)
                        path.write_bytes(previous)
                        with self.assertRaises(ResourceIndexError):
                            encode_like_index(value, path, packed=packed)
                        self.assertEqual(path.read_bytes(), previous)
            valid = b'{"value":"\\ud83d\\ude00"}'
            value = {"value": "\U0001F600"}
            self.assertEqual(decode_index(valid), value)
            for packed in (False, True):
                self.assertEqual(decode_index(encode_index(value, packed=packed)), value)

    def test_parser_limits_are_reported_as_resource_errors_in_both_formats(self):
        envelope = json.loads(encode_index({"value": 0}, packed=True))
        cases = (
            b'{"value":' + b"[" * 16000 + b"0" + b"]" * 16000 + b"}",
            b'{"value":' + b"1" * 5000 + b"}",
        )
        for plain in cases:
            packed = json.dumps({
                **envelope, "decoded_bytes": len(plain),
                "payload": base64.b64encode(lzma.compress(plain)).decode(),
            }).encode()
            for name, data in (("plain", plain), ("packed", packed)):
                with self.subTest(format=name, input_bytes=len(plain)):
                    with self.assertRaises(ResourceIndexError):
                        decode_index(data)

    def test_decoder_memory_bound_rejects_a_valid_large_dictionary_stream(self):
        value = {"glyphs": [0x4E00]}
        plain = encode_index(value)
        stream = bytearray(lzma.compress(plain))
        header_end = 12 + (stream[12] + 1) * 4
        self.assertEqual(bytes(stream[14:16]), b"\x21\x01")
        # A valid 64 MiB dictionary header avoids allocating a huge encoder fixture.
        stream[16] = 28
        stream[header_end - 4:header_end] = struct.pack(
            "<I", zlib.crc32(stream[12:header_end - 4])
        )
        self.assertEqual(lzma.decompress(stream, memlimit=80 * 1024 * 1024), plain)
        envelope = json.loads(encode_index(value, packed=True))
        envelope["payload"] = base64.b64encode(stream).decode()
        with self.assertRaises(ResourceIndexError):
            decode_index(json.dumps(envelope).encode())

    def test_cli_rejection_retains_the_original_codec_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("fonts/cjk", "graphics/fonts/cjk"):
                shutil.copytree(ROOT / relative, root / relative)
            for broken in (
                b"invalid index",
                b'{"value":' + b"[" * 16000 + b"0" + b"]" * 16000 + b"}",
            ):
                with self.subTest(input_bytes=len(broken)):
                    (root / FEHRR_SOURCES).write_bytes(broken)
                    paths = (FEHRR_SOURCES, "graphics/fonts/cjk/manifest.json")
                    before = {path: (root / path).read_bytes() for path in paths}
                    result = subprocess.run(
                        [
                            sys.executable, "-B", "-m", "scripts.fonttools.cjk",
                            "--root", str(root), "repack-index",
                            "--index", "fehrr", "--format", "packed",
                        ],
                        cwd=ROOT, capture_output=True, text=True, timeout=180,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertTrue(result.stderr.startswith("error:"), result.stderr)
                    with self.assertRaises(ResourceIndexError) as failure:
                        decode_index(broken)
                    self.assertIn(str(failure.exception), result.stderr)
                    self.assertEqual({path: (root / path).read_bytes() for path in paths}, before)

    def test_non_finite_numbers_are_rejected_in_both_formats_and_encoding(self):
        envelope = json.loads(encode_index({"value": 0}, packed=True))
        for token in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
            plain = ('{"value":' + token + "}").encode()
            wrapped = {
                **envelope, "decoded_bytes": len(plain),
                "payload": base64.b64encode(lzma.compress(plain)).decode(),
            }
            inputs = (
                ("plain", lambda: decode_index(plain)),
                ("packed", lambda: decode_index(json.dumps(wrapped).encode())),
                ("encode", lambda: encode_index({"value": float(token)})),
            )
            for operation, call in inputs:
                with self.subTest(token=token, operation=operation):
                    with self.assertRaises(ResourceIndexError):
                        call()

    def test_representable_exponents_round_trip_in_both_formats(self):
        for number in (1e308, -1e308):
            value = {"value": number}
            for packed in (False, True):
                with self.subTest(number=number, packed=packed):
                    self.assertEqual(decode_index(encode_index(value, packed=packed)), value)

    def test_existing_index_is_plain_byte_stable_and_losslessly_packable(self):
        original = (ROOT / "fonts/cjk/fehrr-sources.json").read_bytes()
        value = decode_index(original)
        plain = encode_index(value)
        self.assertEqual(decode_index(plain), value)
        self.assertEqual(encode_index(decode_index(plain)), plain)
        packed = encode_index(value, packed=True)
        self.assertLess(len(packed), len(plain))
        self.assertEqual(decode_index(packed), value)
        self.assertEqual(encode_index(value, packed=True), packed)
        self.assertEqual(decode_index(original), value)
        self.assertEqual(encode_like_index(value, ROOT / "fonts/cjk/fehrr-sources.json"), original)

    def test_invalid_or_unbounded_envelopes_fail_explicitly(self):
        envelope = json.loads(encode_index({"source": "glyphs"}, packed=True))
        compressed = base64.b64decode(envelope["payload"])
        nested = json.dumps(envelope).encode()
        cases = [
            {**envelope, "version": 2},
            {**envelope, "version": True},
            {**envelope, "unknown": "field"},
            {**envelope, "encoding": "unknown"},
            {**envelope, "decoded_bytes": MAX_INDEX_BYTES + 1},
            {**envelope, "decoded_bytes": True},
            {**envelope, "decoded_bytes": envelope["decoded_bytes"] + 1},
            {**envelope, "payload": "%%%"},
            *({**envelope, "payload": value} for value in (None, False, 1, [], {})),
            {**envelope, "decoded_bytes": len(nested), "payload": base64.b64encode(
                lzma.compress(nested)
            ).decode()},
            {**envelope, "payload": base64.b64encode(compressed[:-4]).decode()},
            {**envelope, "payload": base64.b64encode(compressed + b"x").decode()},
            {**envelope, "payload": base64.b64encode(compressed * 2).decode()},
            {**envelope, "payload": base64.b64encode(
                compressed[:-8] + bytes(8)
            ).decode()},
            {**envelope, "decoded_bytes": 1, "payload": base64.b64encode(
                lzma.compress(b" " * (MAX_INDEX_BYTES + 1))
            ).decode()},
        ]
        for number, case in enumerate(cases):
            with self.subTest(number=number):
                with self.assertRaises(ResourceIndexError):
                    decode_index(json.dumps(case).encode())
        for data in (b"[]", b"\xff", b'{"a":1,"a":2}', b" " * (MAX_INDEX_BYTES + 1)):
            with self.subTest(length=len(data)):
                with self.assertRaises(ResourceIndexError):
                    decode_index(data)

    def test_writer_preserves_representation_and_does_not_replace_invalid_input(self):
        original = {"glyphs": [1, 2]}
        changed = {"glyphs": [1, 2, 3]}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "index.json"
            self.assertEqual(encode_like_index(original, path), encode_index(original))
            for packed in (False, True):
                path.write_bytes(encode_index(original, packed=packed))
                encoded = encode_like_index(changed, path)
                self.assertEqual(encoded, encode_index(changed, packed=packed))
                self.assertEqual(decode_index(encoded), changed)
            path.write_bytes(b"invalid index")
            with self.assertRaises(ResourceIndexError):
                encode_like_index(changed, path)
            self.assertEqual(path.read_bytes(), b"invalid index")

    def test_unchanged_packed_json_keeps_a_valid_alternate_encoder_stream(self):
        value = {"glyphs": [0x4E00, 0x4E01]}
        standard = encode_index(value, packed=True)
        alternate = json.loads(standard)
        alternate["payload"] = base64.b64encode(
            lzma.compress(encode_index(value), preset=0)
        ).decode()
        stored = (json.dumps(alternate, indent=2, sort_keys=True) + "\n").encode()
        self.assertNotEqual(stored, standard)
        self.assertEqual(decode_index(stored), value)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "index.json"
            path.write_bytes(stored)
            self.assertEqual(encode_like_index(value, path), stored)
            self.assertEqual(encode_like_index(value, path, packed=True), stored)
            self.assertEqual(encode_like_index(value, path, packed=False), encode_index(value))
            changed = {"glyphs": [0x4E00, 0x4E01, 0x4E02]}
            result = encode_like_index(changed, path)
            self.assertEqual(decode_index(result), changed)

    def test_actual_priority_reader_keeps_physical_checksum_boundary(self):
        original = (ROOT / FEHRR_SOURCES).read_bytes()
        value = decode_index(original)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / FEHRR_SOURCES
            path.parent.mkdir(parents=True)
            for packed in (False, True):
                with self.subTest(packed=packed):
                    stored = encode_index(value, packed=packed)
                    path.write_bytes(stored)
                    priority = {
                        "path": FEHRR_SOURCES,
                        "sha256": hashlib.sha256(stored).hexdigest(),
                        "policy": "FEHRR original-game glyphs and widths before FEBuilderGBA fallback",
                    }
                    self.assertEqual(
                        _read_fehrr_lock(root, {"fehrr_priority": priority}), value
                    )
                    priority["sha256"] = hashlib.sha256(b"not stored bytes").hexdigest()
                    with self.assertRaisesRegex(CjkFontError, "provenance drifted"):
                        _read_fehrr_lock(root, {"fehrr_priority": priority})

    def test_actual_cli_round_trips_both_indexes_with_existing_consumers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (
                "fonts", "graphics/fonts/cjk", "src", "include", "texts",
                "assets", "reports", "scripts/generated_data",
            ):
                shutil.copytree(
                    ROOT / relative, root / relative,
                    ignore=shutil.ignore_patterns("*.o", "*.d", "__pycache__"),
                )
            originals = {
                relative: (root / relative).read_bytes()
                for relative in (
                    FEHRR_SOURCES, "fonts/cjk/runtime_usage.json",
                    "fonts/cjk/inventory.json", "graphics/fonts/cjk/manifest.json",
                )
            }
            before_assets = check_compact_assets(root)
            for index, relative in (
                ("fehrr", FEHRR_SOURCES),
                ("runtime-usage", "fonts/cjk/runtime_usage.json"),
            ):
                originally_packed = json.loads(originals[relative]).get("kind") == ENVELOPE_KIND
                formats = ("plain", "packed") if originally_packed else ("packed", "plain")
                for representation in formats:
                    with self.subTest(index=index, format=representation):
                        result = subprocess.run(
                            [
                                sys.executable, "-B", "-m", "scripts.fonttools.cjk",
                                "--root", str(root), "repack-index",
                                "--index", index, "--format", representation,
                            ],
                            cwd=ROOT, capture_output=True, text=True, timeout=180,
                        )
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        stored = (root / relative).read_bytes()
                        self.assertEqual(
                            json.loads(stored).get("kind") == ENVELOPE_KIND,
                            representation == "packed",
                        )
                        self.assertEqual(
                            decode_index(stored),
                            decode_index(originals[relative]),
                        )
                        after_assets = check_compact_assets(root)
                        for path, data in before_assets.items():
                            if path not in (FEHRR_SOURCES, "graphics/fonts/cjk/manifest.json"):
                                self.assertEqual(after_assets[path], data)
                        generated = check_generated_files(root)
                        self.assertEqual(
                            generated["fonts/cjk/runtime_usage.json"],
                            (root / "fonts/cjk/runtime_usage.json").read_bytes(),
                        )
            for relative in (FEHRR_SOURCES, "fonts/cjk/runtime_usage.json"):
                packed = json.loads(originals[relative]).get("kind") == ENVELOPE_KIND
                self.assertEqual(
                    (root / relative).read_bytes(),
                    encode_index(decode_index(originals[relative]), packed=packed),
                )
            inventory_path = "fonts/cjk/inventory.json"
            usage_path = "fonts/cjk/runtime_usage.json"
            usage = (root / usage_path).read_bytes()
            expected_inventory = json.loads(originals[inventory_path])
            expected_inventory["outputs"][usage_path] = {
                "byte_count": len(usage), "sha256": hashlib.sha256(usage).hexdigest(),
            }
            self.assertEqual(json.loads((root / inventory_path).read_bytes()), expected_inventory)
            expected_manifest = json.loads(originals["graphics/fonts/cjk/manifest.json"])
            for key, relative in (("inventory", inventory_path), ("fehrr_priority", FEHRR_SOURCES)):
                expected_manifest["sources"][key]["sha256"] = hashlib.sha256(
                    (root / relative).read_bytes()
                ).hexdigest()
            self.assertEqual(
                json.loads((root / "graphics/fonts/cjk/manifest.json").read_bytes()), expected_manifest
            )
            broken = root / FEHRR_SOURCES
            broken.write_bytes(b"invalid index")
            before = {relative: (root / relative).read_bytes() for relative in originals}
            result = subprocess.run(
                [
                    sys.executable, "-B", "-m", "scripts.fonttools.cjk",
                    "--root", str(root), "repack-index",
                    "--index", "fehrr", "--format", "packed",
                ],
                cwd=ROOT, capture_output=True, text=True, timeout=180,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("error:", result.stderr)
            self.assertEqual(
                {relative: (root / relative).read_bytes() for relative in originals}, before
            )


if __name__ == "__main__":
    unittest.main()
