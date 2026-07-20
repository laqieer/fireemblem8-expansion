"""Tests for scripts/modernize/finalize_rom_header.py (issue #8).

Covers the in-place ROM header patch + checksum recompute round-trip, the
"validate everything before touching the file" guarantee for each malformed
identity field, and the --metadata-json fallback-source behavior used by
modern.mk's expansion-modern-rom recipe. All tests build a synthetic,
minimal GBA header in memory; none depend on a real ROM build or the
modern toolchain.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "modernize"))

import finalize_rom_header as frh  # noqa: E402
import verify_rom_header as vrh  # noqa: E402


def make_stub_rom(size: int = vrh.EXPECTED_SIZE) -> bytearray:
    """A minimal ROM image with a placeholder (unpatched) header -- as if
    freshly produced by objcopy from src/rom_header.s's hardcoded values,
    before finalize_rom_header.py has patched it."""
    data = bytearray(b"\xff" * size)
    data[vrh.HEADER_TITLE_OFFSET : vrh.HEADER_TITLE_OFFSET + vrh.HEADER_TITLE_SIZE] = (
        b"PLACEHOLDER".ljust(vrh.HEADER_TITLE_SIZE, b"\x00")
    )
    data[
        vrh.HEADER_GAME_CODE_OFFSET : vrh.HEADER_GAME_CODE_OFFSET + vrh.HEADER_GAME_CODE_SIZE
    ] = b"AAAA"
    data[
        vrh.HEADER_MAKER_CODE_OFFSET : vrh.HEADER_MAKER_CODE_OFFSET + vrh.HEADER_MAKER_CODE_SIZE
    ] = b"00"
    data[vrh.HEADER_FIXED_BYTE_OFFSET] = vrh.EXPECTED_FIXED_BYTE
    data[vrh.HEADER_SOFTWARE_VERSION_OFFSET] = 0
    data[vrh.HEADER_CHECKSUM_OFFSET] = 0x00  # deliberately wrong; must be recomputed
    return data


class FinalizeRomHeaderFunctionTests(unittest.TestCase):
    def write_rom(self, directory: Path, data: bytes) -> Path:
        path = directory / "fixture.gba"
        path.write_bytes(data)
        return path

    def test_valid_patch_recomputes_checksum_and_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = self.write_rom(Path(tmp), make_stub_rom())
            checksum = frh.finalize_rom_header(rom, "FIREEMBLEM2E", "BE8E", "01", 3)

            patched = rom.read_bytes()
            self.assertEqual(
                patched[vrh.HEADER_TITLE_OFFSET : vrh.HEADER_TITLE_OFFSET + vrh.HEADER_TITLE_SIZE],
                b"FIREEMBLEM2E",
            )
            self.assertEqual(
                patched[
                    vrh.HEADER_GAME_CODE_OFFSET : vrh.HEADER_GAME_CODE_OFFSET
                    + vrh.HEADER_GAME_CODE_SIZE
                ],
                b"BE8E",
            )
            self.assertEqual(
                patched[
                    vrh.HEADER_MAKER_CODE_OFFSET : vrh.HEADER_MAKER_CODE_OFFSET
                    + vrh.HEADER_MAKER_CODE_SIZE
                ],
                b"01",
            )
            self.assertEqual(patched[vrh.HEADER_SOFTWARE_VERSION_OFFSET], 3)
            self.assertEqual(patched[vrh.HEADER_CHECKSUM_OFFSET], checksum)
            self.assertEqual(vrh.compute_checksum(patched), checksum)

            # verify_rom_header.py must independently agree the result is valid.
            facts = vrh.verify_rom_header(rom, expected_revision=3)
            self.assertEqual(facts["title"], "FIREEMBLEM2E")
            self.assertEqual(facts["revision"], 3)

    def test_short_title_is_padded_with_nulls(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = self.write_rom(Path(tmp), make_stub_rom())
            frh.finalize_rom_header(rom, "FE8", "BE8E", "01", 0)
            patched = rom.read_bytes()
            self.assertEqual(
                patched[vrh.HEADER_TITLE_OFFSET : vrh.HEADER_TITLE_OFFSET + vrh.HEADER_TITLE_SIZE],
                b"FE8".ljust(vrh.HEADER_TITLE_SIZE, b"\x00"),
            )

    def test_invalid_title_rejected_without_touching_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = make_stub_rom()
            rom = self.write_rom(Path(tmp), original)
            with self.assertRaises(frh.ec.ConfigError):
                frh.finalize_rom_header(rom, "THIS TITLE IS WAY TOO LONG", "BE8E", "01", 0)
            self.assertEqual(rom.read_bytes(), bytes(original))

    def test_invalid_game_code_rejected_without_touching_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = make_stub_rom()
            rom = self.write_rom(Path(tmp), original)
            with self.assertRaises(frh.ec.ConfigError):
                frh.finalize_rom_header(rom, "FIREEMBLEM2E", "ABC", "01", 0)
            self.assertEqual(rom.read_bytes(), bytes(original))

    def test_invalid_maker_code_rejected_without_touching_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = make_stub_rom()
            rom = self.write_rom(Path(tmp), original)
            with self.assertRaises(frh.ec.ConfigError):
                frh.finalize_rom_header(rom, "FIREEMBLEM2E", "BE8E", "ABC", 0)
            self.assertEqual(rom.read_bytes(), bytes(original))

    def test_invalid_revision_rejected_without_touching_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = make_stub_rom()
            rom = self.write_rom(Path(tmp), original)
            with self.assertRaises(frh.ec.ConfigError):
                frh.finalize_rom_header(rom, "FIREEMBLEM2E", "BE8E", "01", 256)
            self.assertEqual(rom.read_bytes(), bytes(original))

    def test_rom_too_small_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = self.write_rom(Path(tmp), b"\xff" * 16)
            with self.assertRaises(frh.FinalizeRomHeaderError):
                frh.finalize_rom_header(rom, "FIREEMBLEM2E", "BE8E", "01", 0)


class FinalizeRomHeaderCliTests(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "modernize" / "finalize_rom_header.py"

    def write_rom(self, directory: Path, data: bytes) -> Path:
        path = directory / "fixture.gba"
        path.write_bytes(data)
        return path

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(self.SCRIPT), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_cli_explicit_flags_patch_and_report_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = self.write_rom(Path(tmp), make_stub_rom())
            result = self.run_cli(
                "--title", "FIREEMBLEM2E",
                "--game-code", "BE8E",
                "--maker-code", "01",
                "--revision", "0",
                str(rom),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ROM header finalized", result.stdout)
        self.assertIn("checksum=0x", result.stdout)

    def test_cli_metadata_json_supplies_all_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom = self.write_rom(root, make_stub_rom())
            metadata_json = root / "expansion_build_metadata.json"
            metadata_json.write_text(
                json.dumps(
                    {
                        "rom_title": "FIREEMBLEM2E",
                        "rom_game_code": "BE8E",
                        "rom_maker_code": "01",
                        "rom_revision": 4,
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_cli("--metadata-json", str(metadata_json), str(rom))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("revision=4", result.stdout)

    def test_cli_explicit_flag_overrides_metadata_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rom = self.write_rom(root, make_stub_rom())
            metadata_json = root / "expansion_build_metadata.json"
            metadata_json.write_text(
                json.dumps(
                    {
                        "rom_title": "FIREEMBLEM2E",
                        "rom_game_code": "BE8E",
                        "rom_maker_code": "01",
                        "rom_revision": 4,
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "--metadata-json", str(metadata_json), "--revision", "9", str(rom)
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("revision=9", result.stdout)

    def test_cli_missing_value_without_metadata_json_fails_actionably(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = make_stub_rom()
            rom = self.write_rom(Path(tmp), original)
            result = self.run_cli("--title", "FIREEMBLEM2E", str(rom))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--game-code", result.stderr)
            self.assertEqual(rom.read_bytes(), bytes(original))

    def test_cli_invalid_field_fails_actionably_and_does_not_touch_rom(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = make_stub_rom()
            rom = self.write_rom(Path(tmp), original)
            result = self.run_cli(
                "--title", "FIREEMBLEM2E",
                "--game-code", "TOOLONG",
                "--maker-code", "01",
                "--revision", "0",
                str(rom),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("EXPANSION_ROM_GAME_CODE", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(rom.read_bytes(), bytes(original))

    def test_cli_missing_metadata_json_file_fails_actionably(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = make_stub_rom()
            rom = self.write_rom(root, original)
            result = self.run_cli(
                "--metadata-json", str(root / "does-not-exist.json"), str(rom)
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("error:", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(rom.read_bytes(), bytes(original))


if __name__ == "__main__":
    unittest.main()
