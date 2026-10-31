import base64
import hashlib
import json
import lzma
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

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
        originally_packed = json.loads(original).get("kind") == ENVELOPE_KIND
        self.assertEqual(original, packed if originally_packed else plain)

    def test_invalid_or_unbounded_envelopes_fail_explicitly(self):
        envelope = json.loads(encode_index({"source": "glyphs"}, packed=True))
        compressed = base64.b64decode(envelope["payload"])
        cases = [
            {**envelope, "version": 2},
            {**envelope, "version": True},
            {**envelope, "unknown": "field"},
            {**envelope, "encoding": "unknown"},
            {**envelope, "decoded_bytes": MAX_INDEX_BYTES + 1},
            {**envelope, "decoded_bytes": True},
            {**envelope, "decoded_bytes": envelope["decoded_bytes"] + 1},
            {**envelope, "payload": "%%%"},
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
                        self.assertEqual(
                            decode_index((root / relative).read_bytes()),
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
            for relative, data in originals.items():
                self.assertEqual((root / relative).read_bytes(), data)
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
