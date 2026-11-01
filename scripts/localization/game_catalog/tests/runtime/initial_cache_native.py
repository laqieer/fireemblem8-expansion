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

from probe_bindings import resolve_elf_symbol, resolve_elf_symbol_address


@unittest.skipIf(
    os.environ.get("GBA_PLAYTEST_HOST_ONLY") == "1",
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

    def test_cold_zero_lookup_and_valid_cache_transitions(self):
        tools = {}
        for variable, default in (
            ("MODERN_CC", "arm-none-eabi-gcc"),
            ("MODERN_NM", "arm-none-eabi-nm"),
            ("MODERN_OBJCOPY", "arm-none-eabi-objcopy"),
        ):
            executable = shutil.which(os.environ.get(variable, default))
            self.assertIsNotNone(executable, f"required native tool is missing: {variable}")
            tools[variable] = str(Path(executable).absolute())
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="initial-cache-native-", dir=ROOT / "build") as directory:
            work = Path(directory)
            source = work / "texts.txt"
            definitions = work / "textdefs.txt"
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
                    "-I" + str(ROOT / "include"), "-I" + str(ROOT),
                    "-c", path, "-o", target,
                ], cwd=ROOT)
                objects.append(target)
            (work / "sound").mkdir()
            self.run_command([
                tools["MODERN_CC"], "-mcpu=arm7tdmi", "-mthumb-interwork",
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
""", encoding="ascii")
            elf, rom = work / "module.elf", work / "module.gba"
            self.run_command([
                tools["MODERN_CC"], "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork",
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
            self.assertEqual(size, 20 + 4 * 24)
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
            self.assertEqual((phase, count, buffer_bytes, initial_id), (2, 2, 0x1000, 0))
            self.assertGreaterEqual(decoder, 0x03000000)
            self.assertLess(decoder, 0x03007800)
            records = []
            for slot in range(4):
                offset = 20 + slot * 24
                identifier, length = struct.unpack_from("<2I", data, offset)
                text = data[offset + 8:offset + 24].split(b"\0", 1)[0]
                records.append((identifier, length, text))
            print(json.dumps({
                "message_count": count, "buffer_bytes": buffer_bytes,
                "decoder_address": hex(decoder), "rom_bytes": rom.stat().st_size,
                "records": [
                    {"id": identifier, "length": length, "hex": text.hex()}
                    for identifier, length, text in records
                ],
            }, sort_keys=True))
            self.assertEqual(records, [
                (0, 10, b"Cache zero"), (0, 10, b"Cache zero"),
                (1, 9, b"Cache one"), (0, 10, b"Cache zero"),
            ])

    def test_selected_tools_keep_their_symlink_entrypoints(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native-compiler-entry-", dir=ROOT / "build") as directory:
            work = Path(directory)
            driver = work / "driver"
            environment = os.environ.copy()
            forwarding = []
            for variable, name in (
                ("MODERN_CC", "arm-none-eabi-gcc"),
                ("MODERN_NM", "arm-none-eabi-nm"),
                ("MODERN_OBJCOPY", "arm-none-eabi-objcopy"),
            ):
                executable = shutil.which(os.environ.get(variable, name))
                self.assertIsNotNone(executable, f"required native tool is missing: {variable}")
                forwarding.append(
                    "*/" + name + ") exec "
                    + shlex.quote(str(Path(executable).absolute())) + ' "$@";;\n'
                )
                selected = work / name
                selected.symlink_to(driver)
                environment[variable] = str(selected)
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
            observations = [
                json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
            ]
            self.assertEqual(len(observations), 1)
            self.assertEqual(
                [record["hex"] for record in observations[0]["records"]],
                ["4361636865207a65726f", "4361636865207a65726f",
                 "4361636865206f6e65", "4361636865207a65726f"],
            )
