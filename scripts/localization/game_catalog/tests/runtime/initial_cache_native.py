"""Explicit ARM/libmGBA regression for the cold English message cache."""

import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[5]
FIXTURES = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools/gba-playtest"))
sys.path.insert(0, str(ROOT / "tools/gba-playtest/tests"))

from probe_bindings import resolve_elf_symbol, resolve_elf_symbol_address
from host_mode import host_only_enabled


_NATIVE_TOOLS = (
    ("MODERN_CC", "gcc"), ("MODERN_NM", "nm"), ("MODERN_OBJCOPY", "objcopy"),
)


def _selected_tools(environment):
    prefix = environment.get("PREFIX", "arm-none-eabi-")
    root = environment.get("MODERN_TOOLCHAIN_ROOT", "")
    selected = {}
    for variable, suffix in _NATIVE_TOOLS:
        requested = environment.get(variable)
        if requested is None:
            requested = prefix + suffix + environment.get("EXE", "")
            if root:
                requested = str(ROOT / root / "bin") + "/" + requested
        if os.path.dirname(requested):
            requested = str((ROOT / requested).absolute())
        executable = shutil.which(requested, path=environment.get("PATH", os.defpath))
        if executable is None:
            raise FileNotFoundError(f"required native tool is missing: {variable}={requested!r}")
        selected[variable] = str((ROOT / executable).absolute())
    return selected


class NativeToolSelectionTests(unittest.TestCase):
    def test_explicit_overrides_win_and_preserve_entrypoints(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-tool-select-", dir=ROOT / "build") as directory:
            work = Path(directory)
            environment = {"MODERN_TOOLCHAIN_ROOT": "/missing/root", "PREFIX": "missing-", "PATH": ""}
            expected = {}
            for variable, suffix in _NATIVE_TOOLS:
                entrypoint = work / ("selected-" + suffix)
                entrypoint.symlink_to(sys.executable)
                environment[variable] = str(entrypoint.relative_to(ROOT))
                expected[variable] = str(entrypoint)
            self.assertEqual(_selected_tools(environment), expected)

    def test_partial_overrides_keep_the_other_selected_root_tools(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-tool-partial-", dir=ROOT / "build") as directory:
            work = Path(directory)
            binaries = work / "bin"
            binaries.mkdir()
            expected = {}
            for variable, suffix in _NATIVE_TOOLS:
                entrypoint = binaries / ("selected-" + suffix)
                entrypoint.symlink_to(sys.executable)
                expected[variable] = str(entrypoint)
            override = work / "override"
            override.symlink_to(sys.executable)
            baseline = {
                "MODERN_TOOLCHAIN_ROOT": str(work.relative_to(ROOT)),
                "PREFIX": "selected-", "PATH": "",
            }
            for variable, _suffix in _NATIVE_TOOLS:
                with self.subTest(variable=variable):
                    self.assertEqual(
                        _selected_tools({**baseline, variable: str(override)}),
                        {**expected, variable: str(override)},
                    )

    def test_invalid_or_empty_explicit_tool_is_not_replaced_by_a_default(self):
        baseline = {variable: sys.executable for variable, _suffix in _NATIVE_TOOLS}
        for variable, _suffix in _NATIVE_TOOLS:
            for invalid in ("", "/missing/selected-native-tool"):
                with self.subTest(variable=variable, invalid=invalid):
                    with self.assertRaisesRegex(FileNotFoundError, variable):
                        _selected_tools({**os.environ, **baseline, variable: invalid})


@unittest.skipIf(
    host_only_enabled(),
    "explicit host-only lane excludes ARM/libmGBA execution",
)
class InitialMessageCacheNativeTests(unittest.TestCase):
    def run_command(self, command, *, cwd):
        result = subprocess.run(
            [str(value) for value in command], cwd=cwd,
            capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def run_native_scenario(self, *, transform=False, capacity=False, locale_mask=1):
        tools = _selected_tools(os.environ)
        paths = {
            name: str((ROOT / os.environ[name]).absolute())
            for name in ("MODERN_BINUTILS_DIR", "MODERN_NEWLIB_INCLUDE", "MODERN_NEWLIB_LIB")
            if os.environ.get(name)
        }
        driver_flags = []
        if "MODERN_BINUTILS_DIR" in paths:
            driver_flags.append("-B" + paths["MODERN_BINUTILS_DIR"] + "/")
        if "MODERN_NEWLIB_INCLUDE" in paths:
            driver_flags.extend(("-isystem", paths["MODERN_NEWLIB_INCLUDE"]))
        library_flags = (
            ["-L" + paths["MODERN_NEWLIB_LIB"]]
            if "MODERN_NEWLIB_LIB" in paths else []
        )
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="initial-cache-native-", dir=ROOT / "build") as directory:
            work = Path(directory)
            source = work / "texts.txt"
            definitions = work / "textdefs.txt"
            if transform:
                source.write_text(
                    "## MSG_CACHE_ZERO\nA[LOAD][HIGH_ZERO]B[TACT][X]\n"
                    "## MSG_CACHE_ONE\nText[TACT][.][X]\n"
                    "## MSG_ITEM\nSword[.][X]\n"
                    "## MSG_CHARACTER\nAlice[.][X]\n"
                    "## MSG_CHARACTER_1\nBasil[.][X]\n"
                    "## MSG_CHARACTER_2\nCelia[.][X]\n"
                    "## MSG_CHARACTER_3\nDara[.][X]\n"
                    "## MSG_SPECIAL\nK[LOAD][HIGH_ZERO][ITEM]/[CHAR]/[TACT]Z[.][X]\n"
                    "## MSG_AMBIGUOUS\n"
                    "[GLYPH2][TACT]/[GLYPH4][LQUOTE][TACT]/"
                    "[ACCENT][RQUOTE][ITEM]/[ACCENT][LQUOTE][CHAR][.][X]\n"
                    "## MSG_ALL_CHARACTERS\n[CHAR]/[CHAR1]/[CHAR2]/[CHAR3][X]\n",
                    encoding="ascii",
                )
                definitions.write_text(
                    "[X] = 0\n[.] = 31\n[LOAD] = 16\n[HIGH_ZERO] = 16, 0\n"
                    "[TACT] = 0x80, 0x20\n[ITEM] = 0x80, 0x22\n[CHAR] = 0x80, 0x12\n"
                    "[CHAR1] = 0x80, 0x13\n[CHAR2] = 0x80, 0x14\n[CHAR3] = 0x80, 0x15\n"
                    "[GLYPH2] = 0xc2\n[GLYPH4] = 0xe4\n[ACCENT] = 0xe9\n"
                    "[LQUOTE] = 0x93\n[RQUOTE] = 0x94\n",
                    encoding="ascii",
                )
            else:
                source.write_text(
                    "## MSG_CACHE_ZERO\nCache zero[.][X]\n"
                    "## MSG_CACHE_ONE\nCache one[.][X]\n",
                    encoding="ascii",
                )
                definitions.write_text("[X] = 0\n[.] = 31\n", encoding="ascii")
            generated = work / "msg_data.c"
            header = work / "native_msg.h"
            self.run_command([
                sys.executable, "-B", ROOT / "scripts/texttools/textprocess.py",
                source, definitions, generated, header, "utf8",
            ], cwd=ROOT)
            flags = [
                *driver_flags,
                "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-mabi=aapcs",
                "-std=gnu89", "-ffreestanding", "-fno-builtin", "-fno-common",
                "-fno-pic", "-fno-pie", "-fno-unwind-tables",
                "-fno-asynchronous-unwind-tables", "-ffunction-sections", "-fdata-sections",
                "-O1", "-g3", "-DMODERN=1", "-DNONMATCHING=1", "-DBUGFIX=1",
                f"-DFE8_EXPANSION_ENABLED_LOCALE_MASK={locale_mask}u",
                "-DFE8_EXPANSION_DEFAULT_LOCALE_ID=0", "-DFE8_EXPANSION_STARTER_CONTENT=0",
                "-I" + str(ROOT / "include"), "-I" + str(ROOT), "-I" + str(work),
                "-include", header,
                "-Werror=implicit-function-declaration", "-Werror=incompatible-pointer-types",
            ]
            if capacity:
                flags.append("-DNATIVE_TRANSFORM_CAPACITY=1")
            sources = [
                ROOT / "src" / name for name in (
                    "msg.c", "ramfunc.c", "bmitem.c", "bmlib.c", "bmio.c", "bm.c",
                    "hardware.c", "bmmap.c", "expansion_locale.c",
                    "localized_game_text.c", "localized_text_codec.c", "text_utf8.c",
                )
            ] + [
                generated,
                FIXTURES / (
                    "native_transform_runtime.c" if transform else "initial_cache_native.c"
                ),
            ]
            objects = []
            for path in sources:
                target = work / path.with_suffix(".o").name
                self.run_command([tools["MODERN_CC"], *flags, "-c", path, "-o", target], cwd=ROOT)
                objects.append(target)
            for path, name in (
                (ROOT / "src/rom_header.s", "header.o"),
                (FIXTURES / "module_native_boot.S", "boot.o"),
                (ROOT / "asm/arm.s", "arm.o"),
                (ROOT / "src/libagbsyscall.s", "syscalls.o"),
            ):
                target = work / name
                self.run_command([
                    tools["MODERN_CC"], "-mcpu=arm7tdmi", "-mthumb-interwork",
                    *driver_flags,
                    "-I" + str(ROOT / "include"), "-I" + str(ROOT),
                    "-c", path, "-o", target,
                ], cwd=ROOT)
                objects.append(target)
            (work / "sound").mkdir()
            self.run_command([
                tools["MODERN_CC"], "-mcpu=arm7tdmi", "-mthumb-interwork",
                *driver_flags,
                "-I" + str(ROOT), "-c", ROOT / "sound/music_player_table.s",
                "-o", work / "sound/music_player_table.o",
            ], cwd=ROOT)
            linker = work / "module.ld"
            linker.write_text(f"""
OUTPUT_ARCH(arm)
ENTRY(Init)
MEMORY {{
 ROM(rx) : ORIGIN = 0x08000000, LENGTH = 1M
 EWRAM(rwx) : ORIGIN = 0x02000000, LENGTH = 256K
 IWRAM(rwx) : ORIGIN = 0x03000000, LENGTH = 32K
}}
__hq_mixer_enabled = 0;
SECTIONS {{
 .rom : {{
   KEEP({work}/header.o(.text))
   KEEP({work}/boot.o(.text.boot))
   *(.text*) *(.rodata*) *(.locale_data) . = ALIGN(4);
 }} > ROM
 .data : {{
   __data_start = .; *(.data*) . = ALIGN(4); __data_end = .;
 }} > EWRAM AT> ROM
 __data_load = LOADADDR(.data);
 .ewram (NOLOAD) : {{
   *(ewram_data*) *(.bss*) *(COMMON) . = ALIGN(4);
 }} > EWRAM
 .iwram (NOLOAD) : {{
   INCLUDE {ROOT}/linker/iwram.ld
   *(iwram_data*) . = ALIGN(4);
 }} > IWRAM
 /DISCARD/ : {{ *(.comment) *(.note*) }}
}}
ASSERT(SIZEOF(.iwram) < 0x7800, "module IWRAM overlaps stack")
ASSERT(SIZEOF(.data) + SIZEOF(.ewram) < 0x40000, "module EWRAM overflow")
{"gNativeTransformGuard = sMsgString + 0x1000;" if transform else ""}
""", encoding="ascii")
            elf, rom = work / "module.elf", work / "module.gba"
            self.run_command([
                tools["MODERN_CC"], "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork",
                *driver_flags, *library_flags,
                "-mabi=aapcs", "-nostartfiles", "-Wl,--gc-sections", "-Wl,--no-undefined",
                "-Wl,-T," + str(linker), *objects, "-lc", "-lnosys", "-lgcc", "-o", elf,
            ], cwd=work)
            empty_data = (
                resolve_elf_symbol_address(elf, "__data_start", tools["MODERN_NM"])
                == resolve_elf_symbol_address(elf, "__data_end", tools["MODERN_NM"])
            )
            self.run_command([
                tools["MODERN_OBJCOPY"], "-O", "binary", "--gap-fill=0xff",
                *(["--remove-section=.data"] if empty_data else []), elf, rom,
            ], cwd=work)
            self.assertLessEqual(rom.stat().st_size, 1024 * 1024)
            _, size = resolve_elf_symbol(elf, "gInitialCacheProbe", tools["MODERN_NM"])
            record_count = 39 if transform else 4
            record_width = 32 if transform else 16
            record_stride = 8 + record_width
            self.assertEqual(size, 20 + record_count * record_stride)
            if capacity:
                self.assertEqual(
                    resolve_elf_symbol_address(elf, "gNativeTransformGuard", tools["MODERN_NM"]),
                    resolve_elf_symbol_address(elf, "sMsgString", tools["MODERN_NM"]) + 0x1000,
                )
            scenario = work / "scenario.json"
            scenario.write_text(json.dumps({
                "schema_version": 1, "name": "initial-message-cache", "frames": [],
                "checkpoints": [{
                    "name": "native-cache-records", "frame": 60, "framebuffer": False,
                    "probes": [
                        {"address": f"gInitialCacheProbe+0x{offset:x}", "size": 4}
                        for offset in range(0, size, 4)
                    ],
                }],
            }), encoding="utf-8")
            output = work / "capture.json"
            self.run_command([
                sys.executable, "-B", ROOT / "tools/gba-playtest/gba_playtest.py",
                "capture", "--rom", rom, "--elf", elf, "--nm", tools["MODERN_NM"],
                "--scenario", scenario, "--output", output,
            ], cwd=ROOT)
            captured = json.loads(output.read_text(encoding="utf-8"))
            values = {
                probe["address"]: int(probe["value"], 16)
                for probe in captured["checkpoints"][0]["probes"]
            }
            data = b"".join(
                values[f"gInitialCacheProbe+0x{offset:x}"].to_bytes(4, "little")
                for offset in range(0, size, 4)
            )
            phase, count, buffer_bytes, decoder, initial_id = struct.unpack_from("<5I", data)
            self.assertEqual(
                (phase, count, buffer_bytes, initial_id),
                (2, 10 if transform else 2, 0x1000, 0),
            )
            self.assertGreaterEqual(decoder, 0x03000000)
            self.assertLess(decoder, 0x03007800)
            records = []
            for slot in range(record_count):
                offset = 20 + slot * record_stride
                identifier, length = struct.unpack_from("<2I", data, offset)
                self.assertLessEqual(length, record_width)
                text = data[offset + 8:offset + 8 + length]
                records.append((identifier, length, text))
            print(json.dumps({
                "message_count": count, "buffer_bytes": buffer_bytes,
                "decoder_address": hex(decoder), "rom_bytes": rom.stat().st_size,
                "records": [
                    {"id": identifier, "length": length, "hex": text.hex()}
                    for identifier, length, text in records
                ],
            }, sort_keys=True))
            return records

    def test_cold_zero_lookup_and_valid_cache_transitions(self):
        self.assertEqual(self.run_native_scenario(), [
            (0, 10, b"Cache zero"), (0, 10, b"Cache zero"),
            (1, 9, b"Cache one"), (0, 10, b"Cache zero"),
        ])

    def test_native_transforms_preserve_control_operands_and_boundaries(self):
        expected = [
            b"A\x10\x10\0B\x80\x20\0",
            b"A\x10\x10\0BT\0",
            b"A\x10\x10\0BT\0",
            b"PA\x10\x10\0B\0",
            b"K\x10\x10\0Sword/Alice/TZ\0",
            b"\x80\x01\0BT\0",
            b"\x80\x01\0BT\0",
            b"A\x10\x01\x1f\0",
            b"A\x80\x01\x1f\0",
            b"A\x80\x1f\0",
            b"\x80\0\0",
            b"\x93\x94\xe9\x81\x40\x10\0\x01BT\0",
            b"\xc2T\0",
            b"TextT\0",
        ]
        for locale_mask in (1, 0x81):
            with self.subTest(locale_mask=locale_mask):
                records = self.run_native_scenario(transform=True, locale_mask=locale_mask)
                self.assertEqual(records[:len(expected)], [
                    (index, len(text), text) for index, text in enumerate(expected)
                ])
                nested = b"K\x10\x10\0A\x10\x10\0BT/Alice/TZ\0"
                self.assertEqual(records[21], (21, len(nested), nested))
                ambiguous = [
                    b"\xc2\x80\x20/\xe4\x93\x80\x20/"
                    b"\xe9\x94\x80\x22/\xe9\x93\x80\x12\0",
                    b"\xc2T/\xe4\x93T/\xe9\x94\x80\x22/\xe9\x93\x80\x12\0",
                    b"\xc2T/\xe4\x93T/\xe9\x94Sword/\xe9\x93Alice\0",
                    b"P\xe9\x93\x80\x01\0Z\0",
                    b"\xe9\x93\x80\x01\0Z\0\0",
                ]
                self.assertEqual(records[24:29], [
                    (index, len(text), text) for index, text in enumerate(ambiguous, 24)
                ])
                characters = b"Alice/Basil/Celia/Dara\0"
                self.assertEqual(records[29], (29, len(characters), characters))
                operands = b"A\x10\x80\x20B\x80\x01\x80CT\0"
                self.assertEqual(records[30:32], [
                    (index, len(operands), operands) for index in (30, 31)
                ])
                prefixes = [b"P\x80\x01\0QS\0", b"P\x10\x10\0QS\0"]
                self.assertEqual(records[34:36], [
                    (index, len(text), text) for index, text in enumerate(prefixes, 34)
                ])

    def test_native_transform_capacity_and_guard(self):
        marker = b"<!LOC_OVF!>\0"
        expected = [b"aaa\0", marker, marker, b"Paaa\0", marker, marker, b"XYZXYZ\0"]
        records = self.run_native_scenario(transform=True, capacity=True)
        self.assertEqual(records[14:21], [
            (index, len(text), text) for index, text in enumerate(expected, 14)
        ])
        self.assertEqual(records[22], (22, 4, b"\x5a" * 4))
        self.assertEqual(records[23], (23, 1, b"\x6b"))
        self.assertEqual(records[32], (32, 12, b"<!LOC_BAD!>\0"))
        self.assertEqual(records[33], (33, 4, b"\x5a" * 4))
        self.assertEqual(records[36], (36, 9, b"P\x80\x01\0Qaaa\0"))
        self.assertEqual(records[37], (37, 12, marker))
        self.assertEqual(records[38], (38, 4, b"\x5a" * 4))

    def test_selected_tools_keep_their_symlink_entrypoints(self):
        self._check_tool_facades("root")

    def test_selected_prefix_tools_on_path_keep_their_entrypoints(self):
        self._check_tool_facades("path")

    def _check_tool_facades(self, location):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-compiler-entry-", dir=ROOT / "build") as directory:
            work = Path(directory)
            driver = work / "driver"
            environment = os.environ.copy()
            compiler_log = work / "compiler-argv"
            toolchain = work / "toolchain root"
            binaries = toolchain / "bin"
            binaries.mkdir(parents=True)
            overrides = {
                name: work / folder for name, folder in (
                    ("MODERN_BINUTILS_DIR", "bin tools"),
                    ("MODERN_NEWLIB_INCLUDE", "target headers"),
                    ("MODERN_NEWLIB_LIB", "target libraries"),
                )
            }
            for name, path in overrides.items():
                configured = os.environ.get(name)
                if configured:
                    path.symlink_to((ROOT / configured).absolute(), target_is_directory=True)
                    self.assertTrue(path.samefile(ROOT / configured), name)
                else:
                    path.mkdir()
                environment[name] = str(path.relative_to(ROOT))
            forwarding = []
            real_tools = _selected_tools(os.environ)
            for variable, suffix in _NATIVE_TOOLS:
                name = "selected-" + suffix + environment.get("EXE", "")
                forwarding.append(
                    "*/" + name + ") "
                    + (
                        "printf '%s\\0' \"$0\" \"$@\" '' >> "
                        + shlex.quote(str(compiler_log)) + "; "
                        if variable == "MODERN_CC" else ""
                    )
                    + "exec "
                    + shlex.quote(real_tools[variable]) + ' "$@";;\n'
                )
                selected = binaries / name
                selected.symlink_to(driver)
                environment.pop(variable, None)
            if location == "root":
                environment["MODERN_TOOLCHAIN_ROOT"] = str(toolchain.relative_to(ROOT))
            else:
                environment.pop("MODERN_TOOLCHAIN_ROOT", None)
                environment["PATH"] = str(binaries) + os.pathsep + environment.get("PATH", os.defpath)
            environment["PREFIX"] = "selected-"
            driver.write_text(
                '#!/bin/sh\ncase "$0" in\n' + "".join(forwarding)
                + '*) echo "selected tool entrypoint was lost" >&2; exit 64;;\nesac\n',
                encoding="utf-8",
            )
            driver.chmod(0o755)
            environment.pop("GBA_PLAYTEST_HOST_ONLY", None)
            result = subprocess.run(
                [
                    sys.executable, "-B", "-m", "unittest",
                    __name__ + ".InitialMessageCacheNativeTests."
                    "test_cold_zero_lookup_and_valid_cache_transitions",
                ],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(compiler_log.is_file(), "selected root/prefix compiler was not executed")
            observations = [
                json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
            ]
            self.assertEqual(len(observations), 1)
            self.assertEqual(
                [record["hex"] for record in observations[0]["records"]],
                ["4361636865207a65726f", "4361636865207a65726f",
                 "4361636865206f6e65", "4361636865207a65726f"],
            )
            stages = set()
            for record in compiler_log.read_bytes().split(b"\0\0"):
                if not record:
                    continue
                argv = [argument.decode() for argument in record.split(b"\0")]
                self.assertIn("-B" + str(overrides["MODERN_BINUTILS_DIR"]) + "/", argv)
                self.assertIn("-isystem", argv)
                self.assertEqual(
                    argv[argv.index("-isystem") + 1], str(overrides["MODERN_NEWLIB_INCLUDE"]),
                )
                if "-nostartfiles" in argv:
                    stages.add("link")
                    self.assertIn("-L" + str(overrides["MODERN_NEWLIB_LIB"]), argv)
                else:
                    source = Path(argv[argv.index("-c") + 1])
                    stages.add("assembly" if source.suffix in (".s", ".S") else "compile")
            self.assertEqual(stages, {"compile", "assembly", "link"})
