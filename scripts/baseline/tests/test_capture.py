import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


BASELINE_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SPEC = importlib.util.spec_from_file_location(
    "baseline_capture", BASELINE_DIR / "capture.py"
)
assert SPEC and SPEC.loader
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


def make_rom(directory: Path) -> tuple[Path, bytes]:
    header = bytes.fromhex((FIXTURES / "gba_header.hex").read_text().strip())
    rom = bytearray(0xC0)
    rom[0xA0 : 0xA0 + len(header)] = header
    path = directory / "fireemblem8.gba"
    path.write_bytes(rom)
    return path, bytes(rom)


def make_elf(
    path: Path,
    rom: bytes,
    *,
    debug_path: bytes = b"/build/one/source.c\0",
    machine: int = capture.EM_ARM,
    rom_address: int = 0x08000000,
) -> bytes:
    string_table = b"\0function\0"
    symbols = bytes(16) + struct.pack(
        "<IIIBBH", 1, rom_address, 4, (1 << 4) | 2, 0, 1
    )
    names = [
        "",
        "ROM",
        "ewram_data",
        "IWRAM",
        ".debug_str",
        ".symtab",
        ".strtab",
        ".shstrtab",
    ]
    section_names = bytearray(b"\0")
    name_offsets = [0]
    for name in names[1:]:
        name_offsets.append(len(section_names))
        section_names.extend(name.encode("ascii") + b"\0")

    # name, type, flags, address, contents, logical size, link, info, align, entsize
    specs = [
        ("", 0, 0, 0, b"", 0, 0, 0, 0, 0),
        ("ROM", 1, 7, rom_address, rom, len(rom), 0, 0, 4, 0),
        ("ewram_data", 8, 3, 0x02000000, b"", 0x20, 0, 0, 4, 0),
        ("IWRAM", 8, 3, 0x03000000, b"", 0x10, 0, 0, 4, 0),
        (".debug_str", 1, 0, 0, debug_path, len(debug_path), 0, 0, 1, 0),
        (".symtab", 2, 0, 0, symbols, len(symbols), 6, 1, 4, 16),
        (".strtab", 3, 0, 0, string_table, len(string_table), 0, 0, 1, 0),
        (
            ".shstrtab",
            3,
            0,
            0,
            bytes(section_names),
            len(section_names),
            0,
            0,
            1,
            0,
        ),
    ]
    image = bytearray(52)
    offsets = []
    for _name, section_type, _flags, _address, contents, _size, *_rest in specs:
        if section_type == capture.SHT_NOBITS or not contents:
            offsets.append(len(image))
            continue
        while len(image) % 4:
            image.append(0)
        offsets.append(len(image))
        image.extend(contents)
    while len(image) % 4:
        image.append(0)
    section_offset = len(image)
    for index, spec in enumerate(specs):
        (
            _name,
            section_type,
            flags,
            address,
            _contents,
            size,
            link,
            info,
            alignment,
            entry_size,
        ) = spec
        image.extend(
            struct.pack(
                "<IIIIIIIIII",
                name_offsets[index],
                section_type,
                flags,
                address,
                offsets[index],
                size,
                link,
                info,
                alignment,
                entry_size,
            )
        )
    ident = b"\x7fELF" + bytes([1, 1, 1]) + bytes(9)
    image[:52] = ident + struct.pack(
        "<HHIIIIIHHHHHH",
        2,
        machine,
        1,
        rom_address,
        0,
        section_offset,
        0,
        52,
        0,
        0,
        40,
        len(specs),
        7,
    )
    path.write_bytes(image)
    return bytes(image)


def write_map(directory: Path, rom_size: int, *, rom_size_delta: int = 0) -> Path:
    path = directory / "fireemblem8.map"
    path.write_text(
        "Memory Configuration\n\n"
        "Name             Origin             Length             Attributes\n"
        "rom              0x08000000         0x02000000         xr\n"
        "ewram            0x02000000         0x00040000         rw\n\n"
        "Linker script and memory map\n\n"
        "ewram_data      0x02000000       0x20\n"
        "IWRAM           0x03000000       0x10\n"
        f"ROM             0x08000000       0x{rom_size + rom_size_delta:x}\n",
        encoding="utf-8",
    )
    return path


def make_artifacts(directory: Path, *, debug_path: bytes = b"/one/source.c\0"):
    rom_path, rom = make_rom(directory)
    elf_path = directory / "fireemblem8.elf"
    make_elf(elf_path, rom, debug_path=debug_path)
    map_path = write_map(directory, len(rom))
    return rom_path, elf_path, map_path


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def make_agbcc_source(root: Path) -> Path:
    source = root / "agbcc-source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "Compiler Source Test")
    git(source, "config", "user.email", "compiler@example.invalid")
    (source / "gcc").mkdir()
    (source / "gcc/version.c").write_text(
        'char *version_string = "2.9-arm-000512";\n'
    )
    (source / "gcc/Makefile").write_text(
        "all:\n\t@echo current\nold:\n\t@echo old\n"
    )
    (source / "build.sh").write_text(
        "make -C gcc old\nmake -C gcc\n"
    )
    git(source, "add", ".")
    git(source, "commit", "-qm", "compiler source")
    return source


FIXED_SOURCE = {
    "commit": "1" * 40,
    "dirty": False,
    "generator_sha256": "2" * 64,
}
FIXED_TOOLS = {"tool": "normalized"}


class HeaderTests(unittest.TestCase):
    def test_parses_header_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            parsed = capture.parse_rom(make_rom(Path(temp))[0])
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
            directory = Path(temp)
            short = directory / "short.gba"
            short.write_bytes(bytes(0xBF))
            with self.assertRaisesRegex(capture.BaselineError, "too small"):
                capture.parse_rom(short)
            bad, _ = make_rom(directory)
            contents = bytearray(bad.read_bytes())
            contents[0xA0] = 0xFF
            bad.write_bytes(contents)
            with self.assertRaisesRegex(capture.BaselineError, "printable ASCII"):
                capture.parse_rom(bad)


class ElfTests(unittest.TestCase):
    def test_valid_arm_elf_sections_symbols_and_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            _, rom = make_rom(directory)
            path = directory / "valid.elf"
            make_elf(path, rom)
            evidence, sections = capture.parse_elf(path)
        self.assertEqual(evidence["machine"], "ARM")
        self.assertEqual(evidence["sections"][2]["name"], "ROM")
        self.assertEqual(evidence["symbols"]["named"], 1)
        self.assertEqual(evidence["symbols"]["types"]["function"], 1)
        self.assertEqual(len(evidence["identity"]["sha256"]), 64)
        self.assertEqual(
            next(section for section in sections if section["name"] == "ROM")[
                "contents"
            ],
            rom,
        )

    def test_rejects_non_arm_and_out_of_file_section(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            _, rom = make_rom(directory)
            non_arm = directory / "non-arm.elf"
            make_elf(non_arm, rom, machine=3)
            with self.assertRaisesRegex(capture.BaselineError, "not an ARM"):
                capture.parse_elf(non_arm)

            malformed = directory / "bounds.elf"
            image = bytearray(make_elf(malformed, rom))
            section_offset = struct.unpack_from("<I", image, 32)[0]
            struct.pack_into("<I", image, section_offset + 40 + 16, len(image) + 1)
            malformed.write_bytes(image)
            with self.assertRaisesRegex(capture.BaselineError, "past end"):
                capture.parse_elf(malformed)

    def test_debug_paths_do_not_change_canonical_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            _, rom = make_rom(directory)
            first = directory / "first.elf"
            second = directory / "second.elf"
            first_bytes = make_elf(first, rom, debug_path=b"/short/source.c\0")
            second_bytes = make_elf(
                second, rom, debug_path=b"/different/long/path/source.c\0"
            )
            first_evidence, _ = capture.parse_elf(first)
            second_evidence, _ = capture.parse_elf(second)
        self.assertNotEqual(hashlib.sha256(first_bytes).digest(), hashlib.sha256(second_bytes).digest())
        self.assertEqual(first_evidence, second_evidence)

    def test_memory_bounds_are_enforced(self):
        sections = [
            {
                "address": 0x02000000,
                "name": "too_large",
                "size_bytes": 0x40001,
            }
        ]
        with self.assertRaisesRegex(capture.BaselineError, "exceeds ewram"):
            capture.memory_usage(sections, 0)


class MapTests(unittest.TestCase):
    def test_memory_configuration_rows_are_not_output_sections(self):
        evidence, sections = capture.parse_map(FIXTURES / "valid.map")
        self.assertNotIn("rom", sections)
        self.assertNotIn("ewram", sections)
        self.assertNotIn(".debug_str", sections)
        self.assertEqual(sections["ROM"], {"address": 0x08000000, "size_bytes": 0x40})
        self.assertEqual(len(evidence["identity"]["sha256"]), 64)

    def test_malformed_map_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.map"
            path.write_text("not a linker map\n")
            with self.assertRaisesRegex(capture.BaselineError, "lacks"):
                capture.parse_map(path)


class CaptureTests(unittest.TestCase):
    def capture_with_fixed_environment(self, directory: Path):
        paths = make_artifacts(directory)
        with mock.patch.object(
            capture, "source_provenance", return_value=FIXED_SOURCE
        ), mock.patch.object(capture, "tool_versions", return_value=FIXED_TOOLS):
            return capture.capture(directory, *paths, "unused-")

    def test_full_capture_validates_and_serializes(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = self.capture_with_fixed_environment(Path(temp))
        self.assertEqual(manifest["schema_version"], 4)
        self.assertIn("sha1", manifest["rom"])
        self.assertEqual(manifest["source"], FIXED_SOURCE)
        self.assertEqual(json.loads(capture.serialize(manifest)), manifest)

    def test_full_capture_is_path_and_debug_path_independent(self):
        with tempfile.TemporaryDirectory() as first_temp, tempfile.TemporaryDirectory() as second_temp:
            first_dir = Path(first_temp)
            second_dir = Path(second_temp)
            first_paths = make_artifacts(
                first_dir, debug_path=f"{first_dir}/source.c\0".encode()
            )
            second_paths = make_artifacts(
                second_dir, debug_path=f"{second_dir}/source.c\0".encode()
            )
            with mock.patch.object(
                capture, "source_provenance", return_value=FIXED_SOURCE
            ), mock.patch.object(
                capture, "tool_versions", return_value=FIXED_TOOLS
            ):
                first = capture.capture(first_dir, *first_paths, "unused-")
                second = capture.capture(second_dir, *second_paths, "unused-")
        self.assertEqual(capture.serialize(first), capture.serialize(second))

    def test_rom_elf_mismatches_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            rom_path, elf_path, map_path = make_artifacts(directory)
            original = rom_path.read_bytes()
            changed = bytearray(original)
            changed[0] ^= 1
            rom_path.write_bytes(changed)
            with self.assertRaisesRegex(capture.BaselineError, "ELF ROM section"):
                capture.capture(
                    directory, rom_path, elf_path, map_path, "unused-"
                )

            padded = original + b"\0"
            rom_path.write_bytes(padded)
            with self.assertRaisesRegex(capture.BaselineError, "not 0xFF padding"):
                capture.capture(
                    directory, rom_path, elf_path, map_path, "unused-"
                )

    def test_map_artifact_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            rom_path, elf_path, _ = make_artifacts(directory)
            bad_map = write_map(directory, rom_path.stat().st_size, rom_size_delta=1)
            with self.assertRaisesRegex(capture.BaselineError, "ELF/map disagree"):
                capture.capture(
                    directory, rom_path, elf_path, bad_map, "unused-"
                )


class ProvenanceTests(unittest.TestCase):
    def git(self, root: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

    def test_generator_and_dirty_state_are_included_but_reports_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.git(root, "init", "-q")
            self.git(root, "config", "user.name", "Baseline Test")
            self.git(root, "config", "user.email", "baseline@example.invalid")
            generator = root / "scripts/baseline/capture.py"
            generator.parent.mkdir(parents=True)
            generator.write_text("generator-v1\n")
            (root / "source.txt").write_text("source\n")
            self.git(root, "add", ".")
            self.git(root, "commit", "-qm", "source")
            source_commit = self.git(root, "rev-parse", "HEAD")

            report = root / "reports/baseline/baseline.json"
            report.parent.mkdir(parents=True)
            report.write_text("{}\n")
            self.git(root, "add", ".")
            self.git(root, "commit", "-qm", "report only")

            clean = capture.source_provenance(root)
            self.assertEqual(clean["commit"], source_commit)
            self.assertFalse(clean["dirty"])
            self.assertEqual(clean["generator_sha256"], capture.sha256_file(generator))

            report.write_text('{"changed": true}\n')
            self.assertFalse(capture.source_provenance(root)["dirty"])
            generator.write_text("generator-v2\n")
            dirty = capture.source_provenance(root)
            self.assertTrue(dirty["dirty"])
            self.assertNotEqual(
                dirty["generator_sha256"], clean["generator_sha256"]
            )


class ToolTests(unittest.TestCase):
    def test_failed_version_command_is_rejected_even_with_version_text(self):
        failed = subprocess.CompletedProcess(
            args=["tool"], returncode=1, stdout="GNU tool 9.9\n"
        )
        with mock.patch.object(capture.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(capture.BaselineError, "exit code 1"):
                capture.command_version(["tool", "--version"], "test tool")

    def test_compiler_source_identity_is_cross_path_and_dirty_aware(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = make_agbcc_source(root)
            second = root / "different-checkout-path"
            subprocess.run(
                ["git", "clone", "-q", str(first), str(second)],
                check=True,
            )

            first_clean = capture.compiler_source_provenance(first)
            second_clean = capture.compiler_source_provenance(second)
            self.assertEqual(first_clean, second_clean)
            self.assertFalse(first_clean["source"]["dirty"])
            self.assertNotEqual(
                first_clean["roles"]["agbcc"]["identity_sha256"],
                first_clean["roles"]["old_agbcc"]["identity_sha256"],
            )

            for checkout in (first, second):
                with (checkout / "gcc/version.c").open("a") as stream:
                    stream.write("/* equivalent dirty change */\n")
                (checkout / "local-source.c").write_text("int local_source;\n")
            first_dirty = capture.compiler_source_provenance(first)
            second_dirty = capture.compiler_source_provenance(second)
            self.assertEqual(first_dirty, second_dirty)
            self.assertTrue(first_dirty["source"]["dirty"])
            self.assertNotEqual(
                first_dirty["source"]["content_sha256"],
                first_clean["source"]["content_sha256"],
            )

    def test_missing_compiler_source_requires_explicit_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing"
            with self.assertRaisesRegex(
                capture.BaselineError, "--agbcc-source PATH"
            ):
                capture.agbcc_source_identity(missing)


class CliAndSerializationTests(unittest.TestCase):
    def test_serialization_is_sorted_and_byte_identical(self):
        manifest = {"z": [3, 2, 1], "a": {"z": 2, "a": 1}}
        first = capture.serialize(manifest)
        second = capture.serialize(json.loads(first))
        self.assertEqual(first, second)
        self.assertEqual(first[:7], b'{\n  "a"')
        self.assertTrue(first.endswith(b"\n"))

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


if __name__ == "__main__":
    unittest.main()
