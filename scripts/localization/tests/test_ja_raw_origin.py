import json
import os
import shutil
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_locales.raw_origin import (
    ORIGIN_PROOF_FILENAME,
    PINNED_FE8J_RANGE_MERKLE_ROOT,
    OriginRange,
    RawOriginError,
    canonical_json_bytes,
    verify_origin_proof,
)
from scripts.localization.game_locales.raw_providers import (
    RawProviderError,
    refresh_ja_raw_provider_origin,
    verify_ja_raw_provider_origin,
)


class JapaneseRawOriginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ja_root = ROOT / "texts/locales/ja"
        cls.ja_raw = json.loads(
            (cls.ja_root / "raw.json").read_text(encoding="utf-8")
        )
        source_path = cls.ja_root / cls.ja_raw["source_snapshot"]["path"]
        cls.source_root = source_path.parent
        cls.snapshot = json.loads(source_path.read_text(encoding="utf-8"))
        specification = cls.snapshot["baserom_source"]
        artifact = (
            source_path.parent / specification["artifact"]["path"]
        ).read_bytes()
        cls.ranges = tuple(
            OriginRange(
                target=target,
                symbol=record["symbol"],
                rom_offset=record["rom_offset"],
                raw=artifact[
                    record["artifact_offset"] :
                    record["artifact_offset"] + record["length"]
                ],
            )
            for target, record in sorted(specification["records"].items())
        )
        cls.proof_path = source_path.parent / ORIGIN_PROOF_FILENAME

    def setUp(self):
        self.scratch = ROOT / "build/tests/ja-raw-origin"
        if self.scratch.exists():
            shutil.rmtree(self.scratch)
        self.scratch.mkdir(parents=True)

    def tearDown(self):
        if self.scratch.exists():
            shutil.rmtree(self.scratch)

    def test_committed_proof_is_bound_to_independent_known_rom_root(self):
        self.assertEqual(
            PINNED_FE8J_RANGE_MERKLE_ROOT,
            "a12049be5f9a6fc6e2fbb913a03c7a548fcc6e70c540ed1a485c327506b9851b",
        )
        verify_origin_proof(self.proof_path, ranges=self.ranges)

    def test_refresh_requires_an_explicit_live_baserom_argument(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.localization.game_locales",
                "refresh-ja-raw-origin",
            ],
            cwd=ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--baserom", result.stderr)
        self.assertIn("required", result.stderr)

    def test_refresh_rejects_wrong_rom(self):
        wrong_rom = self.scratch / "wrong.gba"
        wrong_rom.write_bytes(b"\0" * 0x1000000)
        with self.assertRaisesRegex(RawProviderError, "baserom SHA-256 mismatch"):
            refresh_ja_raw_provider_origin(
                self.ja_raw,
                source_root=self.ja_root,
                baserom_path=wrong_rom,
            )

    def test_wrong_root_offset_and_slice_fail_closed(self):
        proof = json.loads(self.proof_path.read_text(encoding="utf-8"))
        proof["rom"]["range_merkle_root"] = "0" * 64
        wrong_root = self.scratch / "wrong-root.json"
        wrong_root.write_bytes(canonical_json_bytes(proof))
        with self.assertRaisesRegex(RawOriginError, "identity/root"):
            verify_origin_proof(wrong_root, ranges=self.ranges)

        wrong_offset = (
            replace(self.ranges[0], rom_offset=self.ranges[0].rom_offset + 1),
            *self.ranges[1:],
        )
        with self.assertRaisesRegex(RawOriginError, "pinned offset"):
            verify_origin_proof(self.proof_path, ranges=wrong_offset)

        wrong_slice = (
            replace(
                self.ranges[0],
                raw=bytes((self.ranges[0].raw[0] ^ 1,))
                + self.ranges[0].raw[1:],
            ),
            *self.ranges[1:],
        )
        with self.assertRaisesRegex(
            RawOriginError,
            "ranges differ|bytes mismatch",
        ):
            verify_origin_proof(self.proof_path, ranges=wrong_slice)

    def test_arbitrary_rebound_slice_and_manifest_cannot_reuse_proof(self):
        rebound = (
            replace(
                self.ranges[0],
                raw="偽り\0".encode("cp932"),
            ),
            *self.ranges[1:],
        )
        with self.assertRaisesRegex(RawOriginError, "ranges differ"):
            verify_origin_proof(self.proof_path, ranges=rebound)

    @unittest.skipUnless(
        os.environ.get("FE8J_BASEROM"),
        "set FE8J_BASEROM for the maintainer live-ROM gate",
    )
    def test_authorized_live_baserom_passes(self):
        verify_ja_raw_provider_origin(
            self.ja_raw,
            source_root=self.ja_root,
            baserom_path=Path(os.environ["FE8J_BASEROM"]),
        )


if __name__ == "__main__":
    unittest.main()
