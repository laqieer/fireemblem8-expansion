"""Execute the existing live compatibility mechanism for default and Grado epochs."""

import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/gba-playtest"))
sys.path.insert(0, str(ROOT / "tools/gba-playtest/tests"))

from host_mode import host_only_enabled
from probe_bindings import resolve_elf_symbol, resolve_elf_symbol_address
from scripts.localization.game_catalog.tests.runtime.initial_cache_native import _selected_tools


@unittest.skipIf(host_only_enabled(), "explicit host-only lane excludes ARM/libmGBA execution")
class GradoSaveCompatNativeTests(unittest.TestCase):
    def run_command(self, command, *, cwd):
        result = subprocess.run(
            [str(value) for value in command], cwd=cwd,
            capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_native_compatibility_probe_under_symlinked_source_path(self):
        _selected_tools(os.environ)
        with tempfile.TemporaryDirectory(prefix="grado-save-link-") as directory:
            linked = Path(directory) / "checkout"
            linked.symlink_to(ROOT, target_is_directory=True)
            source = linked / Path(__file__).resolve().relative_to(ROOT)
            specification = importlib.util.spec_from_file_location("grado_save_linked", source)
            self.assertIsNotNone(specification)
            self.assertIsNotNone(specification.loader)
            module = importlib.util.module_from_spec(specification)
            specification.loader.exec_module(module)
            self.assertTrue(module.ROOT.samefile(ROOT))
            result = unittest.TestResult()
            module.GradoSaveCompatNativeTests(
                "test_native_compatible_and_refused_histories_preserve_all_input_bytes",
            ).run(result)
            self.assertEqual(result.testsRun, 1)
            self.assertFalse(result.skipped)
            self.assertTrue(
                result.wasSuccessful(),
                "\n".join(text for _, text in (*result.errors, *result.failures)),
            )

    def test_native_compatible_and_refused_histories_preserve_all_input_bytes(self):
        self._native_case(full_paths=False)

    def test_native_whole_normal_and_suspend_paths_restore_saved_state(self):
        self._native_case(full_paths=True)

    def _native_case(self, *, full_paths):
        tools = _selected_tools(os.environ)
        boot = ROOT / "scripts/localization/game_catalog/tests/runtime/module_native_boot.S"
        driver = ROOT / "tools/gba-playtest/tests/c" / (
            "grado_save_paths_native.c" if full_paths else "grado_save_compat_native.c"
        )
        sources = [
            ROOT / "src/bmsave-lib.c", ROOT / "src/bmlib.c", ROOT / "src/eventinfo.c",
            ROOT / "src/agb_sram.c",
        ]
        assembly = [
            (ROOT / "src/rom_header.s", "header.o"), (boot, "boot.o"),
            (ROOT / "src/libagbsyscall.s", "syscalls.o"),
        ]
        if full_paths:
            self.run_command(["make", "-C", ROOT / "tools/gbagfx"], cwd=ROOT)
            self.run_command([
                "make", "src/msg_data.c",
                "graphics/misc/gImg_WorldmapNodeRevealEffect.4bpp.lz",
                "graphics/misc/gPal_WorldmapNodeRevealEffect.gbapal",
                "graphics/misc/Img_GmapPath.4bpp.lz",
                *(
                    "graphics/misc/" + name + ".4bpp" for name in (
                        "gUnknown_08A97FA4", "gUnknown_08A98104", "gUnknown_08A9819C",
                        "gUnknown_08A982D4", "gUnknown_08A985FC",
                    )
                ),
            ], cwd=ROOT)
            sources.extend(ROOT / "src" / (name + ".c") for name in (
                "bmsave", "bmsave-bwl", "bmsave-gmap", "bmunit", "bmdifficulty",
                "bmcontainer", "bmmind", "time", "uimenu", "expansion_ui_prefs",
                "expansion_save_prefs", "rng", "bm", "bmitem", "worldmap_path",
                "banim_presentation", "bmtrick", "hardware", "event", "bmmap", "msg_data",
            ))
            assembly.extend((
                (ROOT / "asm/arm.s", "arm.o"),
                (ROOT / "asm/arm_call.s", "arm_call.o"),
                (ROOT / "src/data/worldmap/anim_worldmap_sprite.s", "worldmap-data.o"),
            ))
        sources.append(driver)
        driver_flags = (
            ["-B" + str((ROOT / os.environ["MODERN_BINUTILS_DIR"]).absolute()) + "/"]
            if os.environ.get("MODERN_BINUTILS_DIR") else []
        )
        newlib = os.environ.get(
            "MODERN_NEWLIB_INCLUDE",
            "/usr/include/newlib" if Path("/usr/include/newlib/stdlib.h").is_file() else "",
        )
        library_flags = (
            ["-L" + str((ROOT / os.environ["MODERN_NEWLIB_LIB"]).absolute())]
            if os.environ.get("MODERN_NEWLIB_LIB") else []
        )
        (ROOT / "build").mkdir(exist_ok=True)
        for epoch in (2, 256):
            with self.subTest(epoch=epoch), tempfile.TemporaryDirectory(
                prefix="grado-save-native-", dir=ROOT / "build",
            ) as directory:
                work = Path(directory)
                flags = [
                    *driver_flags, "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-mabi=aapcs",
                    "-std=gnu11", "-fgnu89-inline", "-ffreestanding", "-fno-builtin", "-fno-common", "-fno-pic",
                    "-fno-pie", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
                    "-ffunction-sections", "-fdata-sections", "-O2", "-g3", "-DNDEBUG",
                    "-DMODERN=1", "-DNONMATCHING=1", "-DBUGFIX=1", "-DFE8_EXPANSION_DEBUG=0",
                    "-DFE8_EXPANSION_SAVE_COMPAT_EPOCH=" + str(epoch),
                    "-DFE8_EXPANSION_ENABLED_LOCALE_MASK=1u", "-DFE8_EXPANSION_DEFAULT_LOCALE_ID=0",
                    "-DFE8_EXPANSION_STARTER_CONTENT=0", "-I" + str(ROOT / "include"),
                    "-I" + str(ROOT), *(("-isystem", newlib) if newlib else ()),
                    "-Werror=implicit-function-declaration", "-Werror=incompatible-pointer-types",
                ]
                objects = []
                for source in sources:
                    target = (work / source.relative_to(ROOT)).with_suffix(".o")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    selected = flags
                    if source.name == "agb_sram.c":
                        selected = [
                            value for value in flags if value != "-ffunction-sections"
                        ] + ["-fno-toplevel-reorder", "-fno-reorder-functions"]
                    self.run_command(
                        [tools["MODERN_CC"], *selected, "-c", source, "-o", target], cwd=ROOT,
                    )
                    objects.append(target)
                for source, name in assembly:
                    target = work / name
                    self.run_command([
                        tools["MODERN_CC"], *driver_flags, "-mcpu=arm7tdmi", "-mthumb-interwork",
                        "-I" + str(ROOT / "include"), "-I" + str(ROOT),
                        "-c", source, "-o", target,
                    ], cwd=ROOT)
                    objects.append(target)
                (work / "sound").mkdir()
                self.run_command([
                    tools["MODERN_CC"], *driver_flags, "-mcpu=arm7tdmi", "-mthumb-interwork",
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
   *(.text*) *(.rodata*) . = ALIGN(4);
 }} > ROM
 .data : {{
   __data_start = .; *(.data*) . = ALIGN(4); __data_end = .;
 }} > EWRAM AT> ROM
 __data_load = LOADADDR(.data);
 .iwram (NOLOAD) : {{
   __iwram_start = .;
   INCLUDE {ROOT}/linker/iwram.ld
   *(iwram_data*) . = ALIGN(4); __iwram_end = .;
 }} > IWRAM
 .ewram (NOLOAD) : {{
   __ewram_start = .; *(ewram_data*) *(.bss*) *(COMMON) . = ALIGN(4); __ewram_end = .;
 }} > EWRAM
 /DISCARD/ : {{ *(.comment) *(.note*) }}
}}
ASSERT(SIZEOF(.iwram) < 0x7800, "module IWRAM overlaps stack")
ASSERT(SIZEOF(.data) + SIZEOF(.ewram) < 0x40000, "module EWRAM overflow")
""", encoding="ascii")
                elf, rom = work / "module.elf", work / "module.gba"
                self.run_command([
                    tools["MODERN_CC"], *driver_flags, *library_flags,
                    "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-mabi=aapcs",
                    "-nostartfiles", "-Wl,--gc-sections", "-Wl,--no-undefined",
                    "-Wl,-T," + str(linker), *objects, "-lc", "-lnosys", "-lgcc", "-o", elf,
                ], cwd=work)
                sram_workspaces = {
                    name: resolve_elf_symbol(elf, name, tools["MODERN_NM"])
                    for name in (
                        "readSramFast_Work", "verifySramFast_Work", "verifySramValueFast_Work",
                    )
                }
                for address, extent in sram_workspaces.values():
                    self.assertGreaterEqual(address, 0x03000000)
                    self.assertLessEqual(address + extent, 0x03007800)
                empty_data = (
                    resolve_elf_symbol_address(elf, "__data_start", tools["MODERN_NM"])
                    == resolve_elf_symbol_address(elf, "__data_end", tools["MODERN_NM"])
                )
                self.run_command([
                    tools["MODERN_OBJCOPY"], "-O", "binary", "--gap-fill=0xff",
                    *(["--remove-section=.data"] if empty_data else []), elf, rom,
                ], cwd=work)
                self.assertLessEqual(rom.stat().st_size, 1024 * 1024)
                if full_paths:
                    self._assert_native_save_paths(epoch, work, tools, elf, rom)
                    continue
                _, size = resolve_elf_symbol(elf, "gGradoSaveCompatProbe", tools["MODERN_NM"])
                self.assertEqual(size, 16 + 13 * 12)
                _, flag_size = resolve_elf_symbol(elf, "gGradoSaveFlagsProbe", tools["MODERN_NM"])
                self.assertEqual(flag_size, 24 + 20 * 32)
                scenario = work / "scenario.json"
                scenario.write_text(json.dumps({
                    "schema_version": 1, "name": "grado-save-compat", "frames": [],
                    "checkpoints": [{
                        "name": "native-compatibility-records", "frame": 120, "framebuffer": False,
                        "probes": [
                            {"address": f"gGradoSaveCompatProbe+0x{offset:x}", "size": 4}
                            for offset in range(0, size, 4)
                        ] + [
                            {"address": name, "size": 4}
                            for name in ("ReadSramFast", "VerifySramFast", "VerifySramValueFast")
                        ] + [
                            {"address": f"gGradoSaveFlagsProbe+0x{offset:x}", "size": 4}
                            for offset in range(0, flag_size, 4)
                        ],
                    }],
                }), encoding="utf-8")
                output = work / "capture.json"
                self.run_command([
                    sys.executable, "-B", ROOT / "tools/gba-playtest/gba_playtest.py",
                    "capture", "--rom", rom, "--elf", elf, "--nm", tools["MODERN_NM"],
                    "--scenario", scenario, "--output", output,
                ], cwd=ROOT)
                capture = json.loads(output.read_text())
                values = {
                    probe["address"]: int(probe["value"], 16)
                    for probe in capture["checkpoints"][0]["probes"]
                }
                data = b"".join(
                    values[f"gGradoSaveCompatProbe+0x{offset:x}"].to_bytes(4, "little")
                    for offset in range(0, size, 4)
                )
                self.assertEqual(struct.unpack_from("<4I", data), (2, 13, epoch, 2))
                records = [struct.unpack_from("<3I", data, 16 + index * 12) for index in range(13)]
                self.assertEqual(records, [
                    (0, 0, 1), (1, 0, 1), (2, 2, 1), (3, 1, 1), (4, 4, 1),
                    (5, 7, 1), (6, 5, 1), (7, 6, 1), (8, 3, 1), (9, 2, 1),
                    (10, 4, 1), (11, 2, 1), (12, 2, 1),
                ])
                flag_data = b"".join(
                    values[f"gGradoSaveFlagsProbe+0x{offset:x}"].to_bytes(4, "little")
                    for offset in range(0, flag_size, 4)
                )
                self.assertEqual(struct.unpack_from("<4I", flag_data), (2, 20, 25, 5))
                flag_addresses = struct.unpack_from("<2I", flag_data, 16)
                for address, extent in zip(flag_addresses, (25, 5)):
                    self.assertGreaterEqual(address, 0x03000000)
                    self.assertLess(address + extent, 0x03007800)
                self.assertLess(flag_addresses[0] + 25, flag_addresses[1])
                self.assertLess(
                    flag_addresses[1] + 5,
                    resolve_elf_symbol_address(elf, "gGMData", tools["MODERN_NM"]),
                )
                flag_records = [
                    struct.unpack_from("<8I", flag_data, 24 + index * 32)
                    for index in range(20)
                ]
                flag_results = {(row[0], row[1]): row[2:] for row in flag_records}
                self.assertEqual(len(flag_results), 20)
                self.assertEqual(flag_results, {
                    (slot, pattern): (1, 1, 1, 1, 1, 1)
                    for slot in range(5) for pattern in range(4)
                })
                self.assertEqual(values["ReadSramFast"], sram_workspaces["readSramFast_Work"][0] | 1)
                self.assertEqual(values["VerifySramFast"], sram_workspaces["verifySramFast_Work"][0] | 1)
                self.assertIn(values["VerifySramValueFast"], (
                    sram_workspaces["verifySramValueFast_Work"][0] | 1,
                    resolve_elf_symbol_address(elf, "VerifySramValueFast_Core", tools["MODERN_NM"]) | 1,
                ))
                resources = {
                    name: resolve_elf_symbol_address(elf, "__" + name + "_end", tools["MODERN_NM"])
                    - resolve_elf_symbol_address(elf, "__" + name + "_start", tools["MODERN_NM"])
                    for name in ("data", "ewram", "iwram")
                }
                print(json.dumps({
                    "epoch": epoch, "profile": "native release module",
                    "rom_bytes": rom.stat().st_size,
                    "ewram_bytes": resources["data"] + resources["ewram"],
                    "iwram_bytes": resources["iwram"], "records": records,
                    "flag_records": flag_records,
                    "flag_storage": dict(zip(("permanent", "chapter"), flag_addresses)),
                    "sram_workspaces": sram_workspaces,
                    "active_sram_functions": {
                        name: values[name]
                        for name in ("ReadSramFast", "VerifySramFast", "VerifySramValueFast")
                    },
                }))

    def _assert_native_save_paths(self, epoch, work, tools, elf, rom):
        _, size = resolve_elf_symbol(elf, "gGradoSavePathProbe", tools["MODERN_NM"])
        self.assertEqual(size, 8 + 5 * 36)
        scenario = work / "scenario.json"
        scenario.write_text(json.dumps({
            "schema_version": 1, "name": "grado-original-save-paths", "frames": [],
            "checkpoints": [{
                "name": "original-normal-and-suspend", "frame": 120, "framebuffer": False,
                "probes": [
                    {"address": f"gGradoSavePathProbe+0x{offset:x}", "size": 4}
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
        capture = json.loads(output.read_text())
        values = {
            probe["address"]: int(probe["value"], 16)
            for probe in capture["checkpoints"][0]["probes"]
        }
        data = b"".join(
            values[f"gGradoSavePathProbe+0x{offset:x}"].to_bytes(4, "little")
            for offset in range(0, size, 4)
        )
        self.assertEqual(struct.unpack_from("<2I", data), (2, 5))
        records = [struct.unpack_from("<9I", data, 8 + index * 36) for index in range(5)]
        results = {record[0]: record[1:] for record in records}
        self.assertEqual(len(results), 5)
        self.assertEqual(results, {slot: (1,) * 8 for slot in range(5)})
        resources = {
            name: resolve_elf_symbol_address(elf, "__" + name + "_end", tools["MODERN_NM"])
            - resolve_elf_symbol_address(elf, "__" + name + "_start", tools["MODERN_NM"])
            for name in ("data", "ewram", "iwram")
        }
        print(json.dumps({
            "epoch": epoch, "profile": "native release whole-save module",
            "rom_bytes": rom.stat().st_size, "records": records,
            "ewram_bytes": resources["data"] + resources["ewram"],
            "iwram_bytes": resources["iwram"],
        }))
