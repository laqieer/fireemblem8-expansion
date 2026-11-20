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
            sources.extend(ROOT / "examples/restoration-of-grado/src" / name
                for name in (
                    "state.c", "values.c", "campaign_state.c", "lifecycle.c",
                    "native_save_load.c", "native_save_load_ui.c",
                    "native_bmdebug.c", "native_savemenu.c", "native_sio_term.c",
                ))
            sources.append(
                ROOT / "tools/gba-playtest/tests/c/grado_choice_checkpoints_native.c"
            )
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
                split_const_data = work / "split-const-data.h"
                if full_paths:
                    split_const_data.write_text(
                        '#include "global.h"\n#undef CONST_DATA\n#define CONST_DATA\n',
                        encoding="ascii",
                    )
                flags = [
                    *driver_flags, "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-mabi=aapcs",
                    "-std=gnu11", "-fgnu89-inline", "-ffreestanding", "-fno-builtin", "-fno-common", "-fno-pic",
                    "-fno-pie", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
                    "-ffunction-sections", "-fdata-sections", "-O2", "-g3", "-DNDEBUG",
                    "-DMODERN=1", "-DNONMATCHING=1", "-DBUGFIX=1", "-DFE8_EXPANSION_DEBUG=0",
                    "-DFE8_EXPANSION_SAVE_COMPAT_EPOCH=" + str(epoch),
                    "-DFE8_EXPANSION_ENABLED_LOCALE_MASK=1u", "-DFE8_EXPANSION_DEFAULT_LOCALE_ID=0",
                    "-DFE8_EXPANSION_STARTER_CONTENT=0", "-I" + str(ROOT / "include"),
                    "-I" + str(ROOT / "examples/restoration-of-grado/include"),
                    "-I" + str(ROOT), *(("-isystem", newlib) if newlib else ()),
                    *(("-include", str(split_const_data)) if full_paths else ()),
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
                    self._assert_choice_checkpoints(epoch, work, tools, elf, rom)
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
        self.assertEqual(size, 8 + 9 * 48)
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
        self.assertEqual(struct.unpack_from("<2I", data), (2, 9))
        records = [struct.unpack_from("<12I", data, 8 + index * 48) for index in range(9)]
        results = {(record[0], record[1]): record[2:] for record in records}
        expected_paths = {(slot, slot) for slot in range(3)} | {
            (slot, backing) for slot in (3, 4) for backing in range(3)
        }
        with self.subTest(save_family="native-paths"):
            self.assertEqual(len(results), 9)
            self.assertEqual(results, {path: (1,) * 10 for path in expected_paths})
        _, semantic_size = resolve_elf_symbol(
            elf, "gGradoSemanticSaveProbe", tools["MODERN_NM"])
        self.assertEqual(semantic_size, 48 + 9 * 36)
        _, native_size = resolve_elf_symbol(
            elf, "gGradoNativeSaveProbe", tools["MODERN_NM"])
        self.assertEqual(native_size, 8 + 5 * 28)
        _, caller_size = resolve_elf_symbol(
            elf, "gGradoNativeLoadRefusalProbe", tools["MODERN_NM"])
        self.assertEqual(caller_size, 32 * 4)

        semantic_frame_budget = 10_000

        def capture_semantic(name, keys, count):
            semantic_scenario = work / f"semantic-{name}-scenario.json"
            semantic_scenario.write_text(json.dumps({
                "schema_version": 2, "name": f"grado-semantic-{name}",
                "frames": [{"start": 0, "end": semantic_frame_budget - 1, "keys": keys}],
                "run_until": {
                    "max_frames": semantic_frame_budget,
                    "terminal_conditions": [{
                        "reason": "success",
                        "all": [
                            {
                                "address": "gGradoSemanticSaveProbe+0x0",
                                "size": 4,
                                "operator": "eq",
                                "value": "0x00000002",
                            },
                            {
                                "address": "gGradoSemanticSaveProbe+0x4",
                                "size": 4,
                                "operator": "eq",
                                "value": f"0x{count:08x}",
                            },
                        ],
                    }],
                    "checkpoint": {
                        "name": name, "framebuffer": False,
                        "probes": [
                            {"address": f"gGradoSemanticSaveProbe+0x{offset:x}", "size": 4}
                            for offset in range(0, semantic_size, 4)
                        ],
                    },
                },
            }), encoding="utf-8")
            semantic_output = work / f"semantic-{name}-capture.json"
            self.run_command([
                sys.executable, "-B", ROOT / "tools/gba-playtest/gba_playtest.py",
                "capture", "--rom", rom, "--elf", elf, "--nm", tools["MODERN_NM"],
                "--scenario", semantic_scenario, "--output", semantic_output,
            ], cwd=ROOT)
            semantic_capture = json.loads(semantic_output.read_text())
            semantic_values = {
                probe["address"]: int(probe["value"], 16)
                for probe in semantic_capture["checkpoints"][0]["probes"]
            }
            semantic_data = b"".join(
                semantic_values[f"gGradoSemanticSaveProbe+0x{offset:x}"].to_bytes(4, "little")
                for offset in range(0, semantic_size, 4)
            )
            header = struct.unpack_from("<12I", semantic_data)
            self.assertEqual(header[:2], (2, count))
            return header[2:], [
                struct.unpack_from("<9I", semantic_data, 48 + index * 36)
                for index in range(count)
            ]

        symbols, saved = capture_semantic("backed", ["A"], 9)
        (normal, suspend, legacy, valid, incompatible, corrupt,
         missing, conflict, recovery, reopen) = symbols
        no_slot = 0xFFFFFFFF
        native_no_slot = 0xFF
        expected_saved = {(normal, slot, slot) for slot in range(3)} | {
            (suspend, slot, backing) for slot in (3, 4) for backing in range(3)
        }
        with self.subTest(save_family="semantic-backed"):
            self.assertEqual({record[:3] for record in saved}, expected_saved)
            self.assertTrue(
                all(record[3:] == ((valid if epoch == 256 else incompatible), 1, 1, 1, 1, 1)
                    for record in saved),
                saved,
            )

        native_scenario = work / "native-preflight-scenario.json"
        native_scenario.write_text(json.dumps({
            "schema_version": 1, "name": "grado-native-save-preflight",
            "frames": [{"start": 0, "end": 120, "keys": ["R"]}],
            "checkpoints": [{
                    "name": "native-source-records", "frame": 120, "framebuffer": False,
                    "probes": [
                        {"address": f"gGradoNativeSaveProbe+0x{offset:x}", "size": 4}
                        for offset in range(0, native_size, 4)
                    ] + [
                        {"address": f"gGradoNativeLoadRefusalProbe+0x{offset:x}", "size": 4}
                        for offset in range(0, caller_size, 4)
                    ],
            }],
        }), encoding="utf-8")
        native_output = work / "native-preflight-capture.json"
        self.run_command([
            sys.executable, "-B", ROOT / "tools/gba-playtest/gba_playtest.py",
            "capture", "--rom", rom, "--elf", elf, "--nm", tools["MODERN_NM"],
            "--scenario", native_scenario, "--output", native_output,
        ], cwd=ROOT)
        native_capture = json.loads(native_output.read_text())
        native_values = {
            probe["address"]: int(probe["value"], 16)
            for probe in native_capture["checkpoints"][0]["probes"]
        }
        native_data = b"".join(
            native_values[f"gGradoNativeSaveProbe+0x{offset:x}"].to_bytes(4, "little")
            for offset in range(0, native_size, 4)
        )
        self.assertEqual(struct.unpack_from("<2I", native_data), (2, 5))
        native = [
            struct.unpack_from("<7I", native_data, 8 + index * 28)
            for index in range(5)
        ]
        self.assertEqual([record[:3] for record in native], [
            (normal, 2, native_no_slot),
            (suspend, 3, 1),
            (suspend, 3, 2),
            (normal, 2, native_no_slot),
            (suspend, 3, native_no_slot),
        ])
        self.assertTrue(all(record[6] == 1 for record in native), native)
        if epoch == 256:
            self.assertTrue(
                all(record[3] in (valid, recovery) for record in native[:3]), native
            )
            self.assertTrue(all(record[3] == corrupt for record in native[3:]), native)
            self.assertEqual(
                [record[4:6] for record in native[:3]],
                [(2, native_no_slot), (4, 1), (3, 2)],
            )
            self.assertEqual(native[3][4:6], (2, native_no_slot), native)
            self.assertTrue(all(
                record[4] in (3, 4) and record[5] == 5
                for record in native[4:]
            ), native)
        else:
            self.assertTrue(all(record[3] == incompatible for record in native), native)
            self.assertTrue(all(
                record[4:6] == (native_no_slot, native_no_slot) for record in native
            ), native)
        caller_data = b"".join(
            native_values[
                f"gGradoNativeLoadRefusalProbe+0x{offset:x}"
            ].to_bytes(4, "little")
            for offset in range(0, caller_size, 4)
        )
        caller = struct.unpack("<32I", caller_data)
        with self.subTest(save_family="native-caller-refusal"):
            self.assertEqual(caller[:5], (2, 0, 1, 0x30, 0x30))
            self.assertEqual(caller[5], caller[6])
            self.assertEqual(caller[7:9], (1, 1))
            self.assertEqual(caller[9:12], (1, 1, 0))
            self.assertEqual(caller[12:14], (1, 0))
            self.assertEqual(caller[14], 7)
            self.assertEqual(caller[15], 1)
            self.assertEqual(caller[16], 2)
            self.assertEqual(caller[17], incompatible if epoch == 2 else conflict)
            self.assertEqual(caller[18:20], (2, 4))
            self.assertEqual(caller[20:23], (1, 1, 1))
            self.assertEqual(caller[23], 2)
            if epoch == 256:
                self.assertEqual(caller[24:26], (reopen, reopen))
                self.assertEqual(caller[26:32], (1, 1, 1, 1, 1, 1))
            else:
                self.assertEqual(caller[24:26], (incompatible, incompatible))
                self.assertEqual(caller[26:32], (0, 0, 0, 0, 0, 0))
        negative = []
        view_records = []
        if epoch == 256:
            negative_symbols, negative = capture_semantic("refused", ["A", "B"], 9)
            expected_negative = {
                (normal, missing), (suspend, conflict), (normal, recovery),
                (legacy, incompatible), (suspend, reopen),
            }
            with self.subTest(save_family="semantic-refusal"):
                self.assertEqual(negative_symbols, symbols)
                unbacked = [record for record in negative if record[1] == no_slot]
                backed = [record for record in negative if record[1] != no_slot]
                self.assertEqual(len(unbacked), 5)
                self.assertEqual({(record[0], record[3]) for record in unbacked}, expected_negative)
                self.assertTrue(all(
                    record[1:3] == (no_slot, no_slot)
                    and record[4:] == (1, 1, no_slot, no_slot, 1)
                    for record in unbacked
                ), unbacked)
                self.assertEqual(len(backed), 4)
                self.assertEqual(
                    {(record[0], record[3]) for record in backed},
                    {(normal, recovery), (suspend, recovery), (normal, conflict), (suspend, conflict)},
                )
                self.assertTrue(all(
                    record[2] == 0
                    and (record[1] == 0 if record[0] == normal else record[1] in (3, 4))
                    and record[4:] == (1, 1, 1, 1, 1)
                    for record in backed
                ), backed)
            view_symbols, view_records = capture_semantic("chapter-views", ["A", "B", "L"], 2)
            with self.subTest(save_family="semantic-chapter-views"):
                self.assertEqual(view_symbols, symbols)
                self.assertEqual(len(view_records), 2)
                self.assertEqual({(record[0], record[3]) for record in view_records}, {
                    (suspend, valid), (suspend, corrupt),
                })
                self.assertTrue(all(
                    record[1] in (3, 4) and record[2] == 0
                    and record[4:] == (1, 1, 1, 1, 1)
                    for record in view_records
                ), view_records)
        resources = {
            name: resolve_elf_symbol_address(elf, "__" + name + "_end", tools["MODERN_NM"])
            - resolve_elf_symbol_address(elf, "__" + name + "_start", tools["MODERN_NM"])
            for name in ("data", "ewram", "iwram")
        }
        print(json.dumps({
            "epoch": epoch, "profile": "native release whole-save module",
            "rom_bytes": rom.stat().st_size, "records": records,
            "semantic_records": saved, "refusal_records": negative,
            "chapter_view_records": view_records,
            "ewram_bytes": resources["data"] + resources["ewram"],
            "iwram_bytes": resources["iwram"],
        }))

    def _assert_choice_checkpoints(self, epoch, work, tools, elf, rom):
        _, size = resolve_elf_symbol(
            elf, "gGradoChoiceCheckpointProbe", tools["MODERN_NM"])
        header_words = 24
        default_words = 9 * 6
        outcome_words = 31 * 22
        direction_history_words = 14 * 20
        self.assertEqual(
            size,
            (header_words + default_words + outcome_words + direction_history_words) * 4,
        )

        scenario = work / "choice-checkpoints-scenario.json"
        scenario.write_text(json.dumps({
            "schema_version": 1, "name": "grado-choice-checkpoints",
            "frames": [{"start": 0, "end": 1200, "keys": ["R", "SELECT"]}],
            "checkpoints": [{
                "name": "choice-checkpoint-records", "frame": 1200, "framebuffer": False,
                "probes": [
                    {"address": f"gGradoChoiceCheckpointProbe+0x{offset:x}", "size": 4}
                    for offset in range(0, size, 4)
                ],
            }],
        }), encoding="utf-8")
        output = work / "choice-checkpoints-capture.json"
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
            values[f"gGradoChoiceCheckpointProbe+0x{offset:x}"].to_bytes(4, "little")
            for offset in range(0, size, 4)
        )
        header = struct.unpack_from("<24I", data)
        self.assertEqual(header[:3], (2, 31 if epoch == 256 else 0, int(epoch == 256)))

        if epoch == 2:
            self.assertEqual(
                header,
                (2, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
            )
            self.assertEqual(data[header_words * 4:], bytes(size - header_words * 4))
            return

        self.assertEqual(header[3:11], (1, 1, 1, 1, 1, 0, 1, 1))
        self.assertIn(header[11], (3, 4))
        self.assertEqual(header[12:17], (0, 1, 1, 1, 1))
        self.assertEqual(header[17:23], (1, 1, 1, 1, 1, 1))
        self.assertEqual(header[23], 14)

        defaults = [
            struct.unpack_from("<6I", data, header_words * 4 + index * 24)
            for index in range(9)
        ]
        expected_defaults = (2, 2, 2, 2, 3, 3, 3, 2, 2)
        self.assertEqual(defaults, [
            (family, value, value, 1, 1, 1)
            for family, value in enumerate(expected_defaults)
        ])

        labels = (
            (
                "CENTRAL | Cost: local resistance",
                "COUNCIL | Cost: slower enforcement",
                "LOCAL | Cost: raid pressure",
            ),
            (
                "FULL | Cost: recurring payments",
                "LIMITED | Cost: delayed accountability",
                "DECLINE | Cost: lost grain aid",
            ),
            (
                "SUPPRESS | Cost: local alienation",
                "CONCEDE | Cost: central revenue",
                "AUTONOMY | Cost: weaker central authority",
            ),
            (
                "FULL_PUBLIC | Cost: military morale",
                "LIMITED | Cost: public distrust",
                "SEALED | Cost: foreign trust",
            ),
            (
                "CAPITAL | Cost: remote districts abandoned",
                "FRONTIER | Cost: capital supply",
                "EVEN | Cost: moderate losses everywhere",
            ),
            (
                "SHIELD | Cost: weaker central deterrence",
                "STATE_ARMY | Cost: local distrust",
                "DISBAND_MIN | Cost: weaker air rescue",
            ),
            (
                "SELF_RULE | Cost: weaker coordination",
                "GARRISON | Cost: local backlash",
                "MEDIATE | Cost: slower results",
            ),
            (
                "FULL | Cost: lower temple autonomy",
                "LIMITED | Cost: continued mutual distrust",
                "REFUSE | Cost: no outside medical teams",
            ),
            (
                "SURRENDER | Cost: knowledge and rewards lost",
                "JOINT | Cost: leak risk",
                "RETAIN | Cost: less foreign support",
            ),
            (
                "CENTRAL | Cost: adverse transition",
                "AUTONOMY | Cost: favorable transition",
                "FOREIGN | Cost: favorable transition",
                "RECKONING | Cost: favorable transition",
            ),
        )

        def label_hash(text):
            result = 2166136261
            for byte in text.encode("ascii"):
                result = ((result ^ byte) * 16777619) & 0xFFFFFFFF
            return result

        outcome_offset = (header_words + default_words) * 4
        outcomes = [
            struct.unpack_from("<22I", data, outcome_offset + index * 88)
            for index in range(31)
        ]
        expected = []
        for family, family_labels in enumerate(labels):
            for option, label in enumerate(family_labels):
                expected_value = option + 1
                expected_gold = 1234 + family * 4 + option + 1
                outcome = outcomes[len(expected)]
                expected.append(outcome)
                self.assertEqual(outcome[:3], (family, option, expected_value))
                self.assertEqual(outcome[3], expected_value, (family, option, outcome))
                self.assertEqual(outcome[4:8], (
                    1234, expected_gold, label_hash(label), len(label),
                ))
                self.assertEqual(outcome[8:13], (
                    1, 1, int(family == 0 and option == 0), 1, 1,
                ))
                self.assertEqual(outcome[13:17], (1, 1, 1, 1))
                self.assertIn(outcome[17], (3, 4))
                self.assertEqual(outcome[18], 0)
                self.assertEqual(outcome[19], 1)
                self.assertEqual(outcome[20:22], (1, 1))
        self.assertEqual(len(expected), 31)

        direction_labels = (
            (
                "CENTRAL | Cost: favorable transition",
                "CENTRAL | Cost: moderate transition",
                "CENTRAL | Cost: adverse transition",
            ),
            (
                "AUTONOMY | Cost: favorable transition",
                "AUTONOMY | Cost: moderate transition",
                "AUTONOMY | Cost: adverse transition",
            ),
            (
                "FOREIGN | Cost: favorable transition",
                "FOREIGN | Cost: moderate transition",
                "FOREIGN | Cost: adverse transition",
            ),
            (
                "RECKONING | Cost: favorable transition",
                "RECKONING | Cost: moderate transition",
                "RECKONING | Cost: adverse transition",
            ),
        )
        direction_histories = (
            ("central favorable", 1, (1, 2, 2, 2, 2, 2, 2, 2, 2)),
            ("central neither", 1, (2, 2, 2, 2, 2, 1, 2, 2, 1)),
            ("central adverse", 1, (3, 2, 2, 2, 2, 1, 1, 2, 1)),
            ("autonomy favorable", 2, (3, 2, 2, 2, 1, 1, 1, 2, 1)),
            ("autonomy neither", 2, (2, 2, 2, 2, 1, 1, 2, 2, 1)),
            ("autonomy both", 2, (3, 2, 1, 2, 1, 2, 1, 2, 1)),
            ("autonomy adverse", 2, (1, 2, 2, 2, 1, 1, 2, 2, 1)),
            ("foreign favorable", 3, (2, 1, 2, 1, 2, 1, 2, 1, 1)),
            ("foreign neither", 3, (2, 2, 2, 1, 2, 1, 2, 2, 1)),
            ("foreign both", 3, (2, 1, 2, 3, 2, 1, 2, 1, 3)),
            ("foreign adverse", 3, (2, 3, 2, 1, 2, 1, 2, 3, 1)),
            ("reckoning favorable", 4, (2, 2, 2, 1, 2, 1, 2, 1, 1)),
            ("reckoning neither", 4, (2, 2, 2, 2, 2, 1, 2, 1, 1)),
            ("reckoning adverse", 4, (2, 2, 2, 3, 2, 1, 2, 1, 1)),
        )

        def direction_support(values, direction):
            legitimacy, renais, saltroad, truth, ration, wyvern, village, med, data = values
            if direction == 1:
                foundation = (legitimacy == 1 and wyvern == 2) or (
                    saltroad == 1 and village == 2
                )
                conflict = (legitimacy == 3 and village == 1) or (
                    saltroad == 3 and wyvern == 3
                )
            elif direction == 2:
                foundation = (legitimacy == 3 and village == 1) or (
                    saltroad == 3 and ration == 2
                )
                conflict = (legitimacy == 1 and village == 2) or (
                    saltroad == 1 and wyvern == 2
                )
            elif direction == 3:
                foundation = (renais == 1 and med == 1) or (
                    renais == 2 and data == 2
                )
                conflict = (renais == 3 and med == 3) or (data == 3 and truth == 3)
            else:
                foundation = (truth == 1 and data == 2) or (
                    truth == 1 and renais != 3
                )
                conflict = truth == 3 or (truth == 2 and renais == 3)
            return foundation, conflict

        direction_offset = (header_words + default_words + outcome_words) * 4
        direction_coverage = {direction: set() for direction in range(1, 5)}
        for index, (name, target, choice_values) in enumerate(direction_histories):
            record = struct.unpack_from("<20I", data, direction_offset + index * 80)
            self.assertEqual(record[:3], (index, target, 4), name)
            self.assertEqual(record[3:12], choice_values, name)
            for direction in range(1, 5):
                foundation, conflict = direction_support(choice_values, direction)
                tier = 0 if foundation and not conflict else (
                    2 if conflict and not foundation else 1
                )
                direction_coverage[direction].add(tier)
                label = direction_labels[direction - 1][tier]
                self.assertEqual(
                    (record[11 + direction], record[15 + direction]),
                    (label_hash(label), len(label)),
                    (name, direction, foundation, conflict),
                )
                if direction == target and name.endswith("both"):
                    self.assertEqual((foundation, conflict), (True, True), name)
                if direction == target and name.endswith("neither"):
                    self.assertEqual((foundation, conflict), (False, False), name)
        self.assertEqual(direction_coverage, {direction: {0, 1, 2} for direction in range(1, 5)})
