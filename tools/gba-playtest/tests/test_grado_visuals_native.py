"""Execute original map/configuration consumers in an isolated ARM module ROM."""

import itertools
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from scripts.localization.game_catalog.tests.runtime.initial_cache_native import _selected_tools

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/gba-playtest"))
sys.path.insert(0, str(ROOT / "tools/gba-playtest/tests"))

from host_mode import host_only_enabled
from probe_bindings import resolve_elf_symbol, resolve_elf_symbol_address


@unittest.skipIf(host_only_enabled(), "explicit host-only lane excludes ARM/libmGBA execution")
class GradoVisualNativeTests(unittest.TestCase):
    def run_command(self, command, *, cwd):
        result = subprocess.run(
            [str(value) for value in command], cwd=cwd,
            capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_map_binding_and_terrain_consumers(self):
        tools = _selected_tools(os.environ)
        paths = {
            name: str((ROOT / os.environ[name]).absolute())
            for name in ("MODERN_BINUTILS_DIR", "MODERN_NEWLIB_INCLUDE", "MODERN_NEWLIB_LIB")
            if os.environ.get(name)
        }
        driver_flags = (
            ["-B" + paths["MODERN_BINUTILS_DIR"] + "/"]
            if "MODERN_BINUTILS_DIR" in paths else []
        )
        newlib = paths.get(
            "MODERN_NEWLIB_INCLUDE",
            "/usr/include/newlib" if Path("/usr/include/newlib/stdlib.h").is_file() else "",
        )
        library_flags = ["-L" + paths["MODERN_NEWLIB_LIB"]] if "MODERN_NEWLIB_LIB" in paths else []
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="grado-map-native-", dir=ROOT / "build") as directory:
            work = Path(directory)
            assets = work / "generated/assets"
            self.run_command([
                sys.executable, "-B", "-m", "scripts.assets", "--item-id-cap", "0xCD",
                "--manifest", ROOT / "examples/restoration-of-grado/assets/manifest.json",
                "--out-dir", assets, "generate",
            ], cwd=ROOT)
            binaries = []
            for chapter, source in (
                ("CH00", ROOT / "graphics/map/layout/grado/CH00.mar"),
                ("CH02", assets / "tmx/CH2_MAIN_MAP.mar"),
            ):
                target = work / (chapter + ".bin")
                self.run_command([
                    sys.executable, "-B", ROOT / "scripts/mar_to_map.py", source, target,
                ], cwd=ROOT)
                binaries.append(target.read_bytes())
            flags = [
                *driver_flags, "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork",
                "-mabi=aapcs", "-std=gnu89", "-ffreestanding", "-fno-builtin",
                "-fno-common", "-fno-pic", "-fno-pie", "-fno-unwind-tables",
                "-fno-asynchronous-unwind-tables", "-ffunction-sections", "-fdata-sections",
                "-O1", "-g3", "-DMODERN=1", "-DNONMATCHING=1",
                "-DFE8_EXPANSION_ENABLED_LOCALE_MASK=1u",
                "-DFE8_EXPANSION_DEFAULT_LOCALE_ID=0",
                "-I" + str(work), "-I" + str(ROOT / "include"), "-I" + str(ROOT),
                *(["-isystem", newlib] if newlib else []),
                "-Werror=implicit-function-declaration", "-Werror=incompatible-pointer-types",
            ]
            (work / "src/data").mkdir(parents=True)
            (work / "src/data/chapter_settings.h").write_text('#include "chapterdata.h"\n')
            message_source = work / "texts.txt"
            definitions = work / "textdefs.txt"
            message_source.write_text("## MSG_MAP_PROBE\nMap probe[X]\n", encoding="ascii")
            definitions.write_text("[X] = 0\n", encoding="ascii")
            messages = work / "msg_data.c"
            self.run_command([
                sys.executable, "-B", ROOT / "scripts/texttools/textprocess.py",
                message_source, definitions, messages, work / "native_msg.h", "utf8",
            ], cwd=ROOT)
            objects = []
            fixture = ROOT / "tools/gba-playtest/tests/c/grado_map_native.c"
            for source in (
                *(ROOT / "src" / name for name in (
                    "bmmap.c", "bmlib.c", "chapterdata.c", "bm.c", "bmio.c", "hardware.c", "proc.c",
                )), fixture, messages,
            ):
                target = work / source.with_suffix(".o").name
                self.run_command([tools["MODERN_CC"], *flags, "-c", source, "-o", target], cwd=ROOT)
                objects.append(target)
            boot = ROOT / "scripts/localization/game_catalog/tests/runtime/module_native_boot.S"
            for source, name in (
                (ROOT / "src/rom_header.s", "header.o"), (boot, "boot.o"),
                (ROOT / "src/libagbsyscall.s", "syscalls.o"),
                (ROOT / "asm/arm.s", "arm.o"),
                (ROOT / "asm/arm_call.s", "arm_call.o"),
            ):
                target = work / name
                self.run_command([
                    tools["MODERN_CC"], *driver_flags, "-mcpu=arm7tdmi", "-mthumb-interwork",
                    "-I" + str(ROOT / "include"), "-I" + str(ROOT),
                    "-c", source, "-o", target,
                ], cwd=ROOT)
                objects.append(target)
            (work / "sound").mkdir()
            music = work / "sound/music_player_table.o"
            self.run_command([
                tools["MODERN_CC"], *driver_flags, "-mcpu=arm7tdmi", "-mthumb-interwork",
                "-I" + str(ROOT), "-c", ROOT / "sound/music_player_table.s", "-o", music,
            ], cwd=ROOT)
            configuration = work / "configuration.o"
            self.run_command([
                tools["MODERN_CC"], *driver_flags, "-mcpu=arm7tdmi",
                "-I" + str(ROOT), "-c", ROOT / "graphics/map/TileConfiguration5.S",
                "-o", configuration,
            ], cwd=ROOT)
            config_binary = work / "configuration.bin"
            self.run_command([
                tools["MODERN_OBJCOPY"], "-O", "binary", configuration, config_binary,
            ], cwd=ROOT)
            config = config_binary.read_bytes()
            self.assertEqual(len(config), 9216)
            linker = work / "module.ld"
            linker.write_text(f"""
OUTPUT_ARCH(arm)
ENTRY(Init)
MEMORY {{
 ROM(rx): ORIGIN = 0x08000000, LENGTH = 1M
 EWRAM(rwx): ORIGIN = 0x02000000, LENGTH = 256K
 IWRAM(rwx): ORIGIN = 0x03000000, LENGTH = 32K
}}
__hq_mixer_enabled = 0;
SECTIONS {{
 .rom : {{
  KEEP({work}/header.o(.text)) KEEP({work}/boot.o(.text.boot))
  *(.text*) *(.rodata*) . = ALIGN(4);
 }} > ROM
 .data : {{ __data_start = .; *(.data*) . = ALIGN(4); __data_end = .; }} > EWRAM AT> ROM
 __data_load = LOADADDR(.data);
 .ewram (NOLOAD) : {{
  __ewram_start = .;
  *(ewram_data*) *(ewram_overlay*) *(.bss*) *(COMMON) . = ALIGN(4);
  __ewram_end = .;
 }} > EWRAM
 .iwram (NOLOAD) : {{
  __iwram_start = .;
  INCLUDE {ROOT}/linker/iwram.ld
  *(iwram_data*) . = ALIGN(4); __iwram_end = .;
 }} > IWRAM
 /DISCARD/ : {{ *(.comment) *(.note*) }}
}}
ASSERT(SIZEOF(.iwram) < 0x7800, "module IWRAM overlaps stack")
ASSERT(SIZEOF(.data) + SIZEOF(.ewram) < 0x40000, "module EWRAM overflow")
""", encoding="ascii")
            expected_values = [
                [1704, 1996, 2492, 2876, 1808],
                [2604, 2904, 3328, 2492, 3280],
            ]
            expected_terrain = [[0x1A, 0x17, 0x1F, 0x1D, 0x2D], [0x1A, 2, 0x1E, 0x1F, 0x17]]
            observed = {}
            for compression, unscaled in itertools.product(("raw", "lz77"), (False, True)):
                payloads = [bytearray(data) for data in binaries]
                if unscaled:
                    for index in range(2, len(payloads[0]), 2):
                        value = int.from_bytes(payloads[0][index:index + 2], "little") // 4
                        payloads[0][index:index + 2] = value.to_bytes(2, "little")
                assembly = work / "native_inputs.S"
                lines = []
                for name, payload in (
                    ("GradoNativeMapCH00", payloads[0]), ("GradoNativeMapCH02", payloads[1]),
                    ("GradoNativeTileConfig", config),
                ):
                    if compression == "raw":
                        path = work / (name + ".raw")
                        path.write_bytes(struct.pack("<I", (len(payload) + 4) << 8) + payload)
                    else:
                        source = work / (name + ".bin")
                        source.write_bytes(payload)
                        path = work / (name + ".lz")
                        self.run_command([
                            os.environ.get("GBAGFX", str(ROOT / "tools/gbagfx/gbagfx")),
                            source, path,
                        ], cwd=ROOT)
                    lines.extend((
                        f'.section .rodata.{name}, "a", %progbits', ".balign 4",
                        f".global {name}", f"{name}:", f".incbin {json.dumps(str(path))}",
                    ))
                assembly.write_text("\n".join(lines) + "\n", encoding="ascii")
                inputs = work / "native_inputs.o"
                self.run_command([
                    tools["MODERN_CC"], *driver_flags, "-mcpu=arm7tdmi",
                    "-c", assembly, "-o", inputs,
                ], cwd=ROOT)
                elf, rom = work / "module.elf", work / "module.gba"
                self.run_command([
                    tools["MODERN_CC"], *driver_flags, *library_flags,
                    "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-mabi=aapcs",
                    "-nostartfiles", "-Wl,--gc-sections", "-Wl,--no-undefined",
                    "-Wl,-T," + str(linker), *objects, inputs,
                    "-lc", "-lnosys", "-lgcc", "-o", elf,
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
                _, size = resolve_elf_symbol(elf, "gGradoMapProbe", tools["MODERN_NM"])
                self.assertEqual(size, 152)
                scenario = work / "scenario.json"
                scenario.write_text(json.dumps({
                    "schema_version": 1, "name": "grado-map-native", "frames": [],
                    "checkpoints": [{
                        "name": "map-records", "frame": 30, "framebuffer": False,
                        "probes": [{"address": f"gGradoMapProbe+0x{offset:x}", "size": 4}
                                   for offset in range(0, size, 4)],
                    }],
                }))
                capture = work / "capture.json"
                self.run_command([
                    sys.executable, "-B", ROOT / "tools/gba-playtest/gba_playtest.py", "capture",
                    "--rom", rom, "--elf", elf, "--nm", tools["MODERN_NM"],
                    "--scenario", scenario, "--output", capture,
                ], cwd=ROOT)
                probes = json.loads(capture.read_text())["checkpoints"][0]["probes"]
                data = {probe["address"]: int(probe["value"], 16) for probe in probes}
                values = [data[f"gGradoMapProbe+0x{offset:x}"] for offset in range(0, size, 4)]
                self.assertEqual(values[:2], [2, 0])
                records = [values[2:20], values[20:38]]
                for index, record in enumerate(records):
                    self.assertEqual(record[:8], [index * 2, 1, 16, 12, 60, 61, 62, 4 + index * 7])
                if unscaled:
                    self.assertEqual(records[0][8:13], [426, 499, 623, 719, 452])
                    self.assertNotEqual(records[0][13:18], expected_terrain[0])
                    self.assertEqual(records[1][8:13], expected_values[1])
                    self.assertEqual(records[1][13:18], expected_terrain[1])
                else:
                    for index, record in enumerate(records):
                        self.assertEqual(record[8:13], expected_values[index])
                        self.assertEqual(record[13:18], expected_terrain[index])
                addresses = {
                    name: resolve_elf_symbol_address(elf, name, tools["MODERN_NM"])
                    for name in ("__data_start", "__data_end", "__ewram_start", "__ewram_end",
                                 "__iwram_start", "__iwram_end")
                }
                observed[compression + ("-unscaled" if unscaled else "-native")] = {
                    "records": records, "rom_bytes": rom.stat().st_size,
                    "ewram_bytes": addresses["__data_end"] - addresses["__data_start"]
                    + addresses["__ewram_end"] - addresses["__ewram_start"],
                    "iwram_bytes": addresses["__iwram_end"] - addresses["__iwram_start"],
                }
            print(json.dumps({"actual_native_records": observed}, sort_keys=True))
