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

    def run_cache_scenario(self, *, mode="cache", generator=None):
        if mode not in ("cache", "singleton", "controls"):
            raise ValueError(f"unknown native text scenario {mode!r}")
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
            if mode == "controls":
                messages = (
                    ("MSG_CACHE_ZERO", "一[LOAD][LOW_ZERO][X]"),
                    ("MSG_CACHE_ONE", "A[LOAD][HIGH_ZERO]B[X]"),
                    ("MSG_ODD", "A[MOVE]B[X]"),
                    ("MSG_FACE_AFTER_ODD", "[GLYPH_HEAD][GLYPH_TAIL][MOVE]B[X]"),
                    ("MSG_COLOR_ZERO", "[COLOR_ZERO][X]"),
                    ("MSG_COLOR_ARG_ZERO", "[COLOR_ARG_ZERO]B[X]"),
                )
            else:
                messages = (
                    ("MSG_CACHE_ZERO", "Cache zero[.][X]"),
                    ("MSG_CACHE_ONE", "Cache one[.][X]"),
                    ("MSG_ODD", "Odd[X]"),
                    ("MSG_FACE_AFTER_ODD", "A[LOAD][FACE][X]"),
                )
            source.write_text("".join(
                f"## {name}\n{'[X]' if mode == 'singleton' else text}\n"
                for name, text in messages
            ), encoding="utf-8")
            definitions.write_text(
                "[X] = 0\n[.] = 31\n[LOAD] = 16\n[FACE] = 0, 1\n"
                "[LOW_ZERO] = 0, 1\n[HIGH_ZERO] = 16, 0\n[MOVE] = 0x80, 16\n"
                "[COLOR_ZERO] = 0x80, 0, 1\n[COLOR_ARG_ZERO] = 0x80, 1, 0\n"
                "[GLYPH_HEAD] = 0xE4, 0xB8\n[GLYPH_TAIL] = 0x80\n",
                encoding="ascii",
            )
            generated = work / "msg_data.c"
            header = work / "native_msg.h"
            self.run_command([
                sys.executable, "-B", generator or ROOT / "scripts/texttools/textprocess.py",
                source, definitions, generated, header, "utf8",
            ], cwd=ROOT)
            flags = [
                *driver_flags,
                "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-mabi=aapcs",
                "-std=gnu89", "-ffreestanding", "-fno-builtin", "-fno-common",
                "-fno-pic", "-fno-pie", "-fno-unwind-tables",
                "-fno-asynchronous-unwind-tables", "-ffunction-sections", "-fdata-sections",
                "-O1", "-g3", "-DMODERN=1", "-DNONMATCHING=1", "-DBUGFIX=1",
                "-DFE8_EXPANSION_ENABLED_LOCALE_MASK=1u",
                "-DFE8_EXPANSION_DEFAULT_LOCALE_ID=0", "-DFE8_EXPANSION_STARTER_CONTENT=0",
                "-I" + str(ROOT / "include"), "-I" + str(ROOT), "-I" + str(work),
                "-include", header,
                "-Werror=implicit-function-declaration", "-Werror=incompatible-pointer-types",
            ]
            if mode == "singleton":
                flags.append("-DNATIVE_SINGLETON_TEXT=1")
            elif mode == "controls":
                flags.append("-DNATIVE_CONTROL_TEXT=1")
            sources = [
                ROOT / "src" / name for name in (
                    "msg.c", "ramfunc.c", "bmitem.c", "bmlib.c", "bmio.c", "bm.c",
                    "hardware.c", "bmmap.c", "expansion_locale.c",
                    "localized_game_text.c", "localized_text_codec.c", "text_utf8.c",
                )
            ] + [generated, FIXTURES / "initial_cache_native.c"]
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
            # An absent compressed bit selects an out-of-range child in the
            # old singleton tree; controlled ROM bytes make that observable.
            singleton_sections = (
                "*(.rodata.CompressedText_*) KEEP(*(.singleton_sentinel))"
                if mode == "singleton" else ""
            )
            singleton_poison = (
                ".singleton_poison (gMsgHuffmanTable + 0x3fffc) : "
                "{ KEEP(*(.singleton_poison)) } > ROM"
                if mode == "singleton" else ""
            )
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
   *(.text*) {singleton_sections} *(.rodata*) *(.locale_data) . = ALIGN(4);
 }} > ROM
 {singleton_poison}
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
            layout = {}
            if mode == "singleton":
                rom_bytes = rom.read_bytes()
                for name in (
                    "CompressedText_MSG_CACHE_ZERO", "CompressedText_MSG_CACHE_ONE",
                    "CompressedText_MSG_ODD", "CompressedText_MSG_FACE_AFTER_ODD",
                    "gSingletonSentinel", "gSingletonPoison", "gMsgHuffmanTable",
                ):
                    address = resolve_elf_symbol_address(elf, name, tools["MODERN_NM"])
                    layout[name] = {
                        "address": hex(address), "first_byte": rom_bytes[address - 0x08000000],
                    }
                self.assertEqual(
                    int(layout["gSingletonPoison"]["address"], 16)
                    - int(layout["gMsgHuffmanTable"]["address"], 16),
                    0x3fffc,
                )
                self.assertEqual(layout["gSingletonSentinel"]["first_byte"], 1)
                self.assertEqual(layout["gSingletonPoison"]["first_byte"], ord("A"))
            _, size = resolve_elf_symbol(elf, "gInitialCacheProbe", tools["MODERN_NM"])
            self.assertEqual(size, 20 + 6 * 24)
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
            self.assertEqual((phase, count, buffer_bytes, initial_id), (2, len(messages), 0x1000, 0))
            self.assertGreaterEqual(decoder, 0x03000000)
            self.assertLess(decoder, 0x03007800)
            records = []
            for slot in range(6):
                offset = 20 + slot * 24
                identifier, length = struct.unpack_from("<2I", data, offset)
                self.assertLessEqual(length, 16)
                text = data[offset + 8:offset + 8 + length]
                records.append((identifier, length, text))
            print(json.dumps({
                "message_count": count, "buffer_bytes": buffer_bytes,
                "decoder_address": hex(decoder), "rom_bytes": rom.stat().st_size,
                "singleton_layout": layout,
                "records": [
                    {"id": identifier, "length": length, "hex": text.hex()}
                    for identifier, length, text in records
                ],
            }, sort_keys=True))
            return records

    def test_cold_zero_lookup_and_valid_cache_transitions(self):
        self.assertEqual(self.run_cache_scenario(), [
            (0, 10, b"Cache zero"), (0, 10, b"Cache zero"),
            (1, 9, b"Cache one"), (0, 10, b"Cache zero"),
            (2, 3, b"Odd"), (3, 5, b"A\x10\x00\x01\x00"),
        ])

    def test_singleton_huffman_messages_decode_on_native_arm(self):
        self.assertEqual(self.run_cache_scenario(mode="singleton"), [
            (0, 0, b""), (0, 0, b""), (1, 0, b""),
            (0, 0, b""), (2, 0, b""), (3, 0, b""),
        ])

    def test_control_operands_decode_on_native_arm(self):
        self.assertEqual(self.run_cache_scenario(mode="controls"), [
            (0, 7, b"\xe4\xb8\x80\x10\x00\x01\x00"),
            (1, 6, b"A\x10\x10\x00B\x00"),
            (2, 5, b"A\x80\x10B\x00"),
            (3, 7, b"\xe4\xb8\x80\x80\x10B\x00"),
            (4, 4, b"\x80\x00\x01\x00"),
            (5, 5, b"\x80\x01\x00B\x00"),
        ])

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
                 "4361636865206f6e65", "4361636865207a65726f",
                 "4f6464", "4110000100"],
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
