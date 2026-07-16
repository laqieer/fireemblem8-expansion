import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BASELINE_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SPEC = importlib.util.spec_from_file_location(
    "baseline_capture", BASELINE_DIR / "capture.py"
)
assert SPEC and SPEC.loader
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class HeaderTests(unittest.TestCase):
    def make_rom(self, directory: Path, header: bytes) -> Path:
        path = directory / "test.gba"
        path.write_bytes((bytes(0xA0) + header).ljust(0xC0, b"\0"))
        return path

    def test_parses_header_fields(self):
        header = bytes.fromhex((FIXTURES / "gba_header.hex").read_text().strip())
        with tempfile.TemporaryDirectory() as temp:
            parsed = capture.parse_rom(self.make_rom(Path(temp), header))
        self.assertEqual(
            parsed["header"],
            {
                "game_code": "BE8E",
                "maker_code": "01",
                "revision": 0,
                "title": "FIREEMBLEM2E",
            },
        )

    def test_rejects_short_and_non_ascii_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            short = Path(temp) / "short.gba"
            short.write_bytes(bytes(0xBF))
            with self.assertRaisesRegex(capture.BaselineError, "too small"):
                capture.parse_rom(short)
            bad_header = bytearray(0x1D)
            bad_header[0] = 0xFF
            with self.assertRaisesRegex(capture.BaselineError, "printable ASCII"):
                capture.parse_rom(self.make_rom(Path(temp), bad_header))


class SerializationTests(unittest.TestCase):
    def test_serialization_is_sorted_and_byte_identical(self):
        manifest = {"z": [3, 2, 1], "a": {"z": 2, "a": 1}}
        first = capture.serialize(manifest)
        second = capture.serialize(json.loads(first))
        self.assertEqual(first, second)
        self.assertEqual(first[:7], b'{\n  "a"')
        self.assertTrue(first.endswith(b"\n"))

    def test_map_fixture_is_parsed_deterministically(self):
        first, sections = capture.parse_map(FIXTURES / "valid.map")
        second, _ = capture.parse_map(FIXTURES / "valid.map")
        self.assertEqual(capture.serialize(first), capture.serialize(second))
        self.assertEqual(sections["ROM"], {"address": 0x08000000, "size_bytes": 0x40})


class FailureTests(unittest.TestCase):
    def test_missing_inputs_report_a_concise_error(self):
        with tempfile.TemporaryDirectory() as temp:
            process = subprocess.run(
                [
                    sys.executable,
                    str(BASELINE_DIR / "capture.py"),
                    "--root",
                    temp,
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(process.returncode, 2)
        self.assertEqual(
            process.stderr, "error: ROM input is missing or not a regular file\n"
        )
        self.assertEqual(process.stdout, "")

    def test_malformed_elf_and_map_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            malformed = directory / "bad.elf"
            malformed.write_bytes(b"not an ELF")
            with self.assertRaisesRegex(capture.BaselineError, "invalid ELF"):
                capture.parse_elf(malformed)
            bad_map = directory / "bad.map"
            bad_map.write_text("not a linker map\n")
            with self.assertRaisesRegex(capture.BaselineError, "lacks"):
                capture.parse_map(bad_map)


if __name__ == "__main__":
    unittest.main()
