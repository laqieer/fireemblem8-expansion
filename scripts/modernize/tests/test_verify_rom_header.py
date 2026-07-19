"""Tests for scripts/modernize/verify_rom_header.py.

Covers valid-header round-tripping and each individually detected mismatch:
wrong size, title, game code, maker code, fixed byte, and checksum. Also
covers the CLI entry point's exit codes and stderr messaging. All tests build
a synthetic, minimal 16 MiB-sized GBA header in memory; none depend on a real
ROM build or the modern toolchain.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "modernize"))

import verify_rom_header as vrh  # noqa: E402


def make_valid_header(size: int = vrh.EXPECTED_SIZE) -> bytearray:
    """Build a minimal, checksum-correct 16 MiB ROM image in memory."""
    data = bytearray(b"\xff" * size)
    data[vrh.HEADER_TITLE_OFFSET : vrh.HEADER_TITLE_OFFSET + vrh.HEADER_TITLE_SIZE] = (
        vrh.EXPECTED_TITLE.encode("ascii").ljust(vrh.HEADER_TITLE_SIZE, b"\x00")
    )
    data[
        vrh.HEADER_GAME_CODE_OFFSET : vrh.HEADER_GAME_CODE_OFFSET
        + vrh.HEADER_GAME_CODE_SIZE
    ] = vrh.EXPECTED_GAME_CODE.encode("ascii")
    data[
        vrh.HEADER_MAKER_CODE_OFFSET : vrh.HEADER_MAKER_CODE_OFFSET
        + vrh.HEADER_MAKER_CODE_SIZE
    ] = vrh.EXPECTED_MAKER_CODE.encode("ascii")
    data[vrh.HEADER_FIXED_BYTE_OFFSET] = vrh.EXPECTED_FIXED_BYTE
    data[vrh.HEADER_CHECKSUM_OFFSET] = vrh.compute_checksum(bytes(data))
    return data


class VerifyRomHeaderTests(unittest.TestCase):
    def write_rom(self, directory: Path, data: bytes) -> Path:
        path = directory / "fixture.gba"
        path.write_bytes(data)
        return path

    def test_valid_header_reports_all_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = self.write_rom(Path(tmp), make_valid_header())
            facts = vrh.verify_rom_header(rom)
        self.assertEqual(facts["size"], vrh.EXPECTED_SIZE)
        self.assertEqual(facts["title"], vrh.EXPECTED_TITLE)
        self.assertEqual(facts["game_code"], vrh.EXPECTED_GAME_CODE)
        self.assertEqual(facts["maker_code"], vrh.EXPECTED_MAKER_CODE)
        self.assertEqual(facts["fixed_byte"], vrh.EXPECTED_FIXED_BYTE)
        self.assertEqual(
            facts["checksum"], vrh.compute_checksum(bytes(make_valid_header()))
        )

    def test_wrong_size_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            rom = self.write_rom(Path(tmp), make_valid_header()[:-1])
            with self.assertRaises(vrh.RomHeaderError) as ctx:
                vrh.verify_rom_header(rom)
        self.assertIn("size", str(ctx.exception))
        self.assertIn(str(vrh.EXPECTED_SIZE), str(ctx.exception))

    def test_wrong_title_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_valid_header()
            data[vrh.HEADER_TITLE_OFFSET : vrh.HEADER_TITLE_OFFSET + 4] = b"XXXX"
            # Recompute checksum so only the title is wrong, isolating the check.
            data[vrh.HEADER_CHECKSUM_OFFSET] = vrh.compute_checksum(bytes(data))
            rom = self.write_rom(Path(tmp), data)
            with self.assertRaises(vrh.RomHeaderError) as ctx:
                vrh.verify_rom_header(rom)
        self.assertIn("title", str(ctx.exception))

    def test_wrong_game_code_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_valid_header()
            data[vrh.HEADER_GAME_CODE_OFFSET : vrh.HEADER_GAME_CODE_OFFSET + 4] = (
                b"ZZZZ"
            )
            data[vrh.HEADER_CHECKSUM_OFFSET] = vrh.compute_checksum(bytes(data))
            rom = self.write_rom(Path(tmp), data)
            with self.assertRaises(vrh.RomHeaderError) as ctx:
                vrh.verify_rom_header(rom)
        self.assertIn("game code", str(ctx.exception))

    def test_wrong_maker_code_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_valid_header()
            data[vrh.HEADER_MAKER_CODE_OFFSET : vrh.HEADER_MAKER_CODE_OFFSET + 2] = (
                b"99"
            )
            data[vrh.HEADER_CHECKSUM_OFFSET] = vrh.compute_checksum(bytes(data))
            rom = self.write_rom(Path(tmp), data)
            with self.assertRaises(vrh.RomHeaderError) as ctx:
                vrh.verify_rom_header(rom)
        self.assertIn("maker code", str(ctx.exception))

    def test_wrong_fixed_byte_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_valid_header()
            data[vrh.HEADER_FIXED_BYTE_OFFSET] = 0x00
            data[vrh.HEADER_CHECKSUM_OFFSET] = vrh.compute_checksum(bytes(data))
            rom = self.write_rom(Path(tmp), data)
            with self.assertRaises(vrh.RomHeaderError) as ctx:
                vrh.verify_rom_header(rom)
        self.assertIn("fixed header byte", str(ctx.exception))

    def test_wrong_checksum_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_valid_header()
            data[vrh.HEADER_CHECKSUM_OFFSET] ^= 0xFF
            rom = self.write_rom(Path(tmp), data)
            with self.assertRaises(vrh.RomHeaderError) as ctx:
                vrh.verify_rom_header(rom)
        self.assertIn("checksum", str(ctx.exception))

    def test_non_ascii_title_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = make_valid_header()
            data[vrh.HEADER_TITLE_OFFSET] = 0x01
            rom = self.write_rom(Path(tmp), data)
            with self.assertRaises(vrh.RomHeaderError) as ctx:
                vrh.verify_rom_header(rom)
        self.assertIn("printable ASCII", str(ctx.exception))

    def test_cli_exit_codes_and_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid_rom = self.write_rom(root, make_valid_header())
            good = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "modernize" / "verify_rom_header.py"),
                 str(valid_rom)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(good.returncode, 0, good.stderr)
            self.assertIn("ROM header valid", good.stdout)

            bad_data = make_valid_header()
            bad_data[vrh.HEADER_CHECKSUM_OFFSET] ^= 0xFF
            bad_rom = self.write_rom(root, bad_data)
            bad = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "modernize" / "verify_rom_header.py"),
                 str(bad_rom)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("checksum", bad.stderr)

    def test_missing_rom_reports_error_not_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.gba"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "modernize" / "verify_rom_header.py"),
                 str(missing)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
