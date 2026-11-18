"""Execute original map/configuration consumers in an isolated ARM module ROM."""

import heapq
import itertools
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from scripts.localization.game_catalog.tests.runtime.initial_cache_native import _selected_tools
from scripts.generated_data.validators import extract_enum_constants
from scripts.modernize import gdb_smoke

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/gba-playtest"))
sys.path.insert(0, str(ROOT / "tools/gba-playtest/tests"))

from host_mode import host_only_enabled
from probe_bindings import resolve_elf_symbol, resolve_elf_symbol_address


@unittest.skipIf(host_only_enabled(), "explicit host-only lane excludes ARM/libmGBA execution")
class GradoVisualNativeTests(unittest.TestCase):
    def run_command(self, command, *, cwd, timeout=90):
        result = subprocess.run(
            [str(value) for value in command], cwd=cwd,
            capture_output=True, text=True, timeout=timeout,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def build_native_owner(self, work, tools, chapter_input):
        work.mkdir()
        output = work / "native"
        target = output / "debug/aapcs/src/data/const_data_chapter_maps.o"
        entrypoint = work / "native.mk"
        entrypoint.write_text(
            "include Makefile\n"
            + "MODERN_PREPROC_FLAGS += "
            + json.dumps("-Rgraphics/map/layout/PrologueMap.bin.lz=" + str(chapter_input)) + "\n"
            + str(target.with_suffix(".pre.c")) + ": " + str(chapter_input) + "\n"
            + "include " + str(target.with_suffix(".assets.d")) + "\n",
            encoding="ascii",
        )
        self.run_command([
            "make", "--no-print-directory", "-f", entrypoint, "-j2", target,
            "NODEP=1", "FE8_PROJECT=default", "FE8_PROJECT_PROFILE=default",
            "MODERN_CONFIG=debug", "MODERN_ABI=aapcs", "MODERN_BUILD_ROOT=" + str(output),
            "ASSET_MANIFEST=examples/restoration-of-grado/assets/manifest.json",
            "PYTHON=" + sys.executable, "MODERN_CC=" + tools["MODERN_CC"],
        ], cwd=ROOT)
        return target

    def fort_pixel(self, config, graphics, palette, metatile, x, y):
        tile = struct.unpack_from("<H", config, 2 * (metatile + 2 * (y // 8) + x // 8))[0]
        px, py = x % 8, y % 8
        if tile & 0x400:
            px = 7 - px
        if tile & 0x800:
            py = 7 - py
        packed = graphics[32 * (tile & 0x3FF) + 4 * py + px // 2]
        color = (packed >> (4 * (px % 2))) & 15
        if color == 0:
            return "0x000000"
        bgr = struct.unpack_from("<H", palette, 32 * (tile >> 12) + 2 * color)[0]
        channels = (bgr & 31, (bgr >> 5) & 31, (bgr >> 10) & 31)
        return "0x" + "".join(f"{(channel << 3) | (channel >> 2):02x}" for channel in channels)

    def reference_movement(self, payload, config, costs, start, blocked=None):
        width, height = payload[:2]
        tiles = struct.unpack_from("<" + str(width * height) + "H", payload, 2)
        terrain = [config[8192 + tile // 4] for tile in tiles]
        result = [255] * len(terrain)
        result[start[1] * width + start[0]] = 0
        pending = [(0, *start)]
        while pending:
            spent, x, y = heapq.heappop(pending)
            if spent != result[y * width + x]:
                continue
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if not (0 <= nx < width and 0 <= ny < height) or (nx, ny) == blocked:
                    continue
                cost = costs[terrain[ny * width + nx]]
                if cost < 0:
                    continue
                distance = spent + cost
                if distance <= 124 and distance < result[ny * width + nx]:
                    result[ny * width + nx] = distance
                    heapq.heappush(pending, (distance, nx, ny))
        return bytes(result)

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
                    "bmidoten.c", "ramfunc.c",
                )), fixture, messages,
            ):
                target = work / source.with_suffix(".o").name
                self.run_command([tools["MODERN_CC"], *flags, "-c", source, "-o", target], cwd=ROOT)
                objects.append(target)
            movement_root = work / "m"
            movement_object = movement_root / "debug/aapcs/src/data_t-movecost.o"
            self.run_command([
                "make", "--no-print-directory", "-j2", movement_object, "NODEP=1",
                "FE8_PROJECT=default", "FE8_PROJECT_PROFILE=default",
                "MODERN_CONFIG=debug", "MODERN_ABI=aapcs",
                "MODERN_BUILD_ROOT=" + str(movement_root),
                "GENERATED_DATA_OUT_DIR=" + str(work / "mc"), "PYTHON=" + sys.executable,
                "MODERN_CC=" + tools["MODERN_CC"],
            ], cwd=ROOT)
            terrains = extract_enum_constants(
                str(ROOT / "include/constants/terrains.h"), name_prefix="TERRAIN_",
            )
            cost_source = json.loads((ROOT / "src/data/movecost.json").read_text())
            expected_costs = [
                {terrains[name][0]: value for name, value in next(
                    row for row in cost_source["profiles"] if row["profile"] == profile
                )["normal"]["values"].items()}
                for profile in ("CommonT2", "Civilian", "Fly")
            ]
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
            coordinates = json.loads(
                (ROOT / "examples/restoration-of-grado/assets/coordinates.json").read_text()
            )
            for chapter, payload in zip(("CH00", "CH02"), binaries):
                for identity, role, coordinate, expected in coordinates["chapters"][chapter]["coordinates"]:
                    x, y = coordinate
                    offset = 2 + 2 * (payload[0] * y + x)
                    value = int.from_bytes(payload[offset:offset + 2], "little")
                    with self.subTest(chapter=chapter, identity=identity, role=role):
                        self.assertEqual(config[8192 + (value >> 2)], expected)
            self.assertIn(
                "CH02 deployment (7,10) is unreachable from (7,4) in the accepted terrain",
                coordinates["chapters"]["CH02"]["retainedWarnings"],
            )
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
            owner_objects = {}
            cases = (
                *itertools.product(("raw", "lz77", "native-owner"), (False, True)),
                *itertools.product(("render0", "render2"), (False, True)),
            )
            for compression, unscaled in cases:
                render_chapter = {"render0": 0, "render2": 2}.get(compression)
                source_owner = compression == "native-owner" or render_chapter is not None
                movement = compression == "native-owner"
                payloads = [bytearray(data) for data in binaries]
                if unscaled:
                    for index in range(2, len(payloads[0]), 2):
                        value = int.from_bytes(payloads[0][index:index + 2], "little") // 4
                        payloads[0][index:index + 2] = value.to_bytes(2, "little")
                assembly = work / "native_inputs.S"
                lines = []
                payload_paths = {}
                native_payloads = (
                    ("GradoNativeMapCH00", payloads[0]), ("GradoNativeMapCH02", payloads[1]),
                    ("GradoNativeTileConfig", config),
                )
                if source_owner:
                    native_payloads = native_payloads[:1]
                for name, payload in native_payloads:
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
                    payload_paths[name] = path
                    lines.extend((
                        f'.section .rodata.{name}, "a", %progbits', ".balign 4",
                        f".global {name}", f"{name}:", f".incbin {json.dumps(str(path))}",
                    ))
                selected_objects = objects
                if source_owner:
                    if unscaled not in owner_objects:
                        owner_objects[unscaled] = self.build_native_owner(
                            work / ("owner" + str(int(unscaled))), tools,
                            payload_paths["GradoNativeMapCH00"],
                        )
                    inputs = owner_objects[unscaled]
                    selected_fixture = work / ("source_owner_fixture-" + str(render_chapter) + ".o")
                    if not selected_fixture.exists():
                        self.run_command([
                            tools["MODERN_CC"], *flags, "-DGRADO_NATIVE_SOURCE_OWNER=1",
                            *(["-DGRADO_MOVEMENT=1"] if movement else []),
                            *([] if render_chapter is None else [
                                "-DGRADO_RENDER_CHAPTER=" + str(render_chapter),
                            ]),
                            "-c", fixture, "-o", selected_fixture,
                        ], cwd=ROOT)
                    selected_objects = [
                        selected_fixture if value.name == "grado_map_native.o" else value
                        for value in objects
                    ]
                else:
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
                    "-Wl,-T," + str(linker), *selected_objects, inputs,
                    *([movement_object] if movement else []),
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
                movement_size = 0
                flood_pointer_binding = None
                if movement:
                    _, movement_size = resolve_elf_symbol(
                        elf, "gGradoMovementProbe", tools["MODERN_NM"],
                    )
                    self.assertEqual(movement_size, 8 + 8 * 204)
                    flood_pointer_binding = "0x" + format(
                        resolve_elf_symbol_address(elf, "gUnk_65", tools["MODERN_NM"]), "08x",
                    )
                scenario = work / "scenario.json"
                pixel_probes = []
                if render_chapter is not None:
                    coordinates = (
                        ((0, 0), (2, 4), (4, 3), (14, 5), (12, 1)),
                        ((0, 0), (7, 10), (4, 9), (14, 1), (7, 4)),
                    )[render_chapter // 2]
                    graphics = (ROOT / "graphics/map/ObjectType5.4bpp").read_bytes()
                    palette = (ROOT / "graphics/map/MapPalette7.gbapal").read_bytes()
                    for (x, y), metatile in zip(coordinates, expected_values[render_chapter // 2]):
                        if y >= 10:
                            continue
                        if unscaled and render_chapter == 0:
                            metatile //= 4
                        for px, py in ((0, 0), (7, 7), (8, 8), (15, 15)):
                            pixel_probes.append({
                                "x": x * 16 + px, "y": y * 16 + py,
                                "expected": self.fort_pixel(config, graphics, palette, metatile, px, py),
                            })
                scenario.write_text(json.dumps({
                    "schema_version": 1,
                    "name": "grado-map-" + compression + ("-unscaled" if unscaled else "-native"),
                    "frames": [],
                    "checkpoints": [{
                        "name": "map-records", "frame": 30, "framebuffer": render_chapter is not None,
                        "probes": [{"address": f"gGradoMapProbe+0x{offset:x}", "size": 4}
                                   for offset in range(0, size, 4)] + [
                            {"address": f"gGradoMovementProbe+0x{offset:x}", "size": 4}
                            for offset in range(0, movement_size, 4)
                                   ] + ([{"address": flood_pointer_binding, "size": 4}] if movement else []),
                        "pixel_probes": pixel_probes,
                    }],
                }))
                capture = work / "capture.json"
                self.run_command([
                    sys.executable, "-B", ROOT / "tools/gba-playtest/gba_playtest.py", "capture",
                    "--rom", rom, "--elf", elf, "--nm", tools["MODERN_NM"],
                    "--scenario", scenario, "--output", capture,
                ], cwd=ROOT)
                checkpoint = json.loads(capture.read_text())["checkpoints"][0]
                probes = checkpoint["probes"]
                data = {probe["address"]: int(probe["value"], 16) for probe in probes}
                values = [data[f"gGradoMapProbe+0x{offset:x}"] for offset in range(0, size, 4)]
                self.assertEqual(values[:2], [2, 0])
                movement_records = {}
                if movement:
                    copied_flood = (
                        resolve_elf_symbol_address(elf, "gUnk_68", tools["MODERN_NM"])
                        + resolve_elf_symbol_address(elf, "MapFloodCore", tools["MODERN_NM"])
                        - resolve_elf_symbol_address(elf, "ARMCodeToCopy_Start", tools["MODERN_NM"])
                    )
                    self.assertGreaterEqual(copied_flood, 0x03000000)
                    self.assertLess(copied_flood, 0x03007800)
                    self.assertEqual(data[flood_pointer_binding], copied_flood)
                    movement_data = b"".join(
                        data[f"gGradoMovementProbe+0x{offset:x}"].to_bytes(4, "little")
                        for offset in range(0, movement_size, 4)
                    )
                    self.assertEqual(struct.unpack_from("<2I", movement_data), (2, 8))
                    starts, blockers = ((2, 4), (7, 4)), ((4, 3), (7, 5))
                    for row in range(8):
                        offset = 8 + row * 204
                        chapter, profile, collision = struct.unpack_from("<3I", movement_data, offset)
                        self.assertIn(chapter, (0, 2))
                        self.assertIn((profile, collision), ((0, 0), (1, 0), (2, 0), (0, 1)))
                        index = chapter // 2
                        expected = self.reference_movement(
                            payloads[index], config, expected_costs[profile], starts[index],
                            blockers[index] if collision else None,
                        )
                        self.assertEqual(movement_data[offset + 12:offset + 204], expected)
                        movement_records[(chapter, profile, collision)] = list(expected)
                    self.assertEqual(len(movement_records), 8)
                    if not unscaled:
                        for index in range(2):
                            x, y = blockers[index]
                            normal = movement_records[(index * 2, 0, 0)]
                            blocked = movement_records[(index * 2, 0, 1)]
                            self.assertLess(
                                normal[y * 16 + x], 120,
                                f"CH{index * 2:02}: collision control {(x, y)} is unreachable; "
                                + repr([(cell % 16, cell // 16)
                                        for cell, cost in enumerate(normal) if 0 < cost < 120]),
                            )
                            self.assertEqual(blocked[y * 16 + x], 255)
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
                    **({"framebuffer": checkpoint["framebuffer_hash"], "pixels": checkpoint["pixel_probes"]}
                       if render_chapter is not None else {}),
                    **({"movement": {str(key): value for key, value in movement_records.items()}}
                       if movement else {}),
                }
            self.assertNotEqual(
                observed["render0-native"]["framebuffer"], observed["render0-unscaled"]["framebuffer"],
            )
            self.assertEqual(
                observed["render2-native"]["framebuffer"], observed["render2-unscaled"]["framebuffer"],
            )
            native_movement = observed["native-owner-native"]["movement"]
            unscaled_movement = observed["native-owner-unscaled"]["movement"]
            self.assertNotEqual(native_movement[str((0, 0, 0))], unscaled_movement[str((0, 0, 0))])
            for profile, collision in ((0, 0), (1, 0), (2, 0), (0, 1)):
                self.assertEqual(
                    native_movement[str((2, profile, collision))],
                    unscaled_movement[str((2, profile, collision))],
                )
            self.assertEqual(
                native_movement[str((2, 0, 0))][10 * 16 + 7],
                255,
                "accepted CH02 (7,10) route is now reachable; update the retained warning",
            )
            print(json.dumps({"actual_native_records": observed}, sort_keys=True))

    def test_selected_static_consumers(self):
        output = ROOT / "build/projects/restoration-of-grado/primary/debug/aapcs"
        self.run_command([
            "make", "--no-print-directory", "-j2", "FE8_PROJECT=restoration-of-grado",
            "FE8_PROJECT_PROFILE=primary", "MODERN_BUILD_ROOT=build",
            "MODERN_CONFIG=debug", "MODERN_ABI=aapcs", "expansion-modern-rom",
        ], cwd=ROOT, timeout=900)
        elf, rom = output / "fireemblem8.elf", output / "fireemblem8.gba"
        gdb = gdb_smoke.select_command(("arm-none-eabi-gdb", "gdb-multiarch"), "ARM GDB")
        mgba = gdb_smoke.select_command(("mgba", "/usr/games/mgba"), "mGBA SDL frontend")
        records = []
        with tempfile.TemporaryDirectory(prefix="grado-static-native-", dir=ROOT / "build") as directory:
            work = Path(directory)
            palette_guard = b"\x5a\x5a" * 512
            tilemap_guard = b"\x77\x77" * 1024
            for name, data in (
                ("palette-input", palette_guard), ("tilemap-input", tilemap_guard),
            ):
                (work / name).write_bytes(data)

            def capture(setup, call, bg, palette_source=None, source_bg=None):
                self.assertTrue(gdb_smoke.port_is_available(2345), "mGBA GDB port is occupied")
                # One inferior call per fresh boot: repeated calls are not reliable in mGBA 0.10.
                with tempfile.TemporaryDirectory(prefix="boot-", dir=work) as boot_directory:
                    boot = Path(boot_directory)
                    shutil.copyfile(rom, boot / "probe.gba")
                    address = 0x06008000 if bg == 3 else 0x06000000
                    commands = [
                        "set pagination off", "set confirm off",
                        "target remote 127.0.0.1:2345", "break StartGame", "continue", "disable 1",
                        "set gLCDControlBuffer.bg2cnt.charBaseBlock = 0",
                        "set gLCDControlBuffer.bg3cnt.charBaseBlock = 2",
                        f"restore {work / 'palette-input'} binary &gPaletteBuffer",
                        f"restore {work / 'tilemap-input'} binary &gBG{bg}TilemapBuffer",
                        *([] if palette_source is None else [
                            f"dump binary memory {boot / 'palette-source'} &{palette_source} ((char *)&{palette_source}+256)",
                        ]),
                        f"dump binary memory {boot / 'graphics-before'} {address:#x} {address + 0x5000:#x}",
                        *([] if source_bg is None else [
                            f"dump binary memory {boot / 'source-graphics-before'} "
                            f"{(0x06008000 if source_bg == 3 else 0x06000000):#x} "
                            f"{(0x0600D000 if source_bg == 3 else 0x06005000):#x}",
                        ]),
                        'printf "SRAM_OFFSET=%u\\n", ((char *)&gSram->unkstruct2-(char *)gSram)',
                        "set $sp = $sp - 512",
                        *setup, call,
                        f"dump binary memory {boot / 'graphics'} {address:#x} {address + 0x5000:#x}",
                        f"dump binary memory {boot / 'tilemap'} &gBG{bg}TilemapBuffer ((char *)&gBG{bg}TilemapBuffer+0x800)",
                        f"dump binary memory {boot / 'palette'} &gPaletteBuffer ((char *)&gPaletteBuffer+0x400)",
                        *([] if source_bg is None else [
                            f"dump binary memory {boot / 'source-graphics'} "
                            f"{(0x06008000 if source_bg == 3 else 0x06000000):#x} "
                            f"{(0x0600D000 if source_bg == 3 else 0x06005000):#x}",
                            f"dump binary memory {boot / 'source-tilemap'} "
                            f"&gBG{source_bg}TilemapBuffer ((char *)&gBG{source_bg}TilemapBuffer+0x800)",
                        ]),
                        "disconnect",
                    ]
                    script = boot / "commands.gdb"
                    script.write_text("\n".join(commands) + "\n", encoding="ascii")
                    environment = os.environ.copy()
                    environment.update(
                        SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
                        XDG_CONFIG_HOME=str(boot / "config"),
                    )
                    with (boot / "mgba.log").open("w+") as log:
                        process = subprocess.Popen(
                            [mgba, "-g", str(boot / "probe.gba")], env=environment,
                            stdout=log, stderr=subprocess.STDOUT,
                        )
                        try:
                            gdb_smoke.wait_for_server(process, 2345, 15)
                            result = subprocess.run(
                                [gdb, "-nx", "-q", "-batch", str(elf), "-x", str(script)],
                                capture_output=True, text=True, timeout=45,
                            )
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        finally:
                            gdb_smoke.terminate(process)
                    offsets = re.findall(r"^SRAM_OFFSET=(\d+)$", result.stdout, re.MULTILINE)
                    self.assertEqual(len(offsets), 1, result.stdout)
                    offset = int(offsets[0])
                    save = (boot / "probe.sav").read_bytes()
                    self.assertGreaterEqual(len(save), offset + 20)
                    return {
                        **{name: (boot / name).read_bytes() for name in (
                            "graphics", "graphics-before", "tilemap", "palette",
                        )},
                        "saved-cg": save[offset:offset + 20],
                        **({} if palette_source is None else {
                            "palette-source": (boot / "palette-source").read_bytes(),
                        }),
                        **({} if source_bg is None else {
                            name: (boot / name).read_bytes()
                            for name in ("source-graphics-before", "source-graphics", "source-tilemap")
                        }),
                    }

            carriers = (
                (1, 0x38, "bg/grado_imperial_tomb"),
                (1, 0x39, "bg/grado_council_hall"),
                (2, 10, "cg/grado_funeral_omen"),
                (2, 11, "cg/grado_title"),
                (1, 0, "bg/bg_House"),
                (1, 0x35, "bg/bg_Blank"),
                (2, 0, "cg/cg_0"),
                (2, 9, "cg/cg_9"),
                (1, 0x37, "bg/bg_House"),
                (1, 0x37, "bg/bg_Black_Temple_Inside"),
            )
            for kind, index, carrier in carriers:
                prefix = ROOT / "graphics" / carrier
                if carrier.startswith("cg/cg_"):
                    raw = b"".join(
                        Path(f"{prefix}_part_{part}.4bpp").read_bytes() for part in range(10)
                    )
                    tsa = prefix.with_suffix(".tsa.bin").read_bytes()
                else:
                    raw = prefix.with_suffix(".feimg2.bin").read_bytes()
                    tsa = prefix.with_suffix(".fetsa2.bin").read_bytes()
                palette = prefix.with_suffix(".gbapal").read_bytes()
                self.assertEqual(tsa[:2], bytes((29, 19)))
                entries = struct.unpack_from("<600H", tsa, 2)
                if "grado_" in carrier:
                    self.assertLess(max(value >> 12 for value in entries), 6)
                self.assertLessEqual(len(raw), 0x5000)
                for route in ("direct", "fade2", "fade3", "literal", "slot2"):
                    with self.subTest(carrier=carrier, route=route):
                        bg = 2 if route == "fade2" else 3
                        pal = 0 if bg == 2 else 8
                        count = 6 if route.startswith("fade") else 8
                        setup = []
                        if route.startswith("fade"):
                            setup = [
                                "set $fade = (struct ConvoBackgroundFadeProc *)$sp",
                                f"set $fade->unkType = {kind}",
                                f"set $fade->bgIndex = {index}",
                            ]
                            call = f"call ConvoBackgroundFade_LoadBg{bg}($fade)"
                        elif route in ("literal", "slot2"):
                            setup = [
                                "set $event = (struct EventEngineProc *)$sp",
                                "set $event->pEventCurrent = (unsigned short *)($sp+256)",
                                "set {unsigned short}($sp+256) = 0x2140",
                                f"set {{short}}($sp+258) = {index if route == 'literal' else -1}",
                                f"set $event->activeTextType = {kind}",
                                f"set gEventSlots[2] = {index}",
                            ]
                            call = "call Event21_TextBg($event)"
                        else:
                            call = f"call EventShowTextBgDirect({kind}, {index})"
                        if index == 0x37 and kind == 1:
                            seeds = (0, 0, 0) if carrier.endswith("House") else (0, 0x8000, 0x7FFF)
                            setup.extend(
                                f"set gRNSeeds[{position}] = {value}"
                                for position, value in enumerate(seeds)
                            )
                        actual = capture(
                            setup, call, bg,
                            f"{prefix.name}_palette" if carrier.startswith("cg/cg_") else None,
                        )
                        expected_graphics = (
                            raw.ljust(0x5000, b"\x00") if kind == 2
                            else raw + actual["graphics-before"][len(raw):]
                        )
                        expected_map = bytearray(tilemap_guard)
                        for y in range(20):
                            for x in range(30):
                                struct.pack_into(
                                    "<H", expected_map, 2 * (y * 32 + x),
                                    entries[(19 - y) * 30 + x] + (pal << 12),
                                )
                        expected_palette = bytearray(palette_guard)
                        source_palette = palette
                        if carrier.startswith("cg/cg_"):
                            # Stock six-bank objects retain the legacy eight-bank direct read.
                            source_palette = actual["palette-source"]
                            self.assertEqual(source_palette[:len(palette)], palette)
                        expected_palette[pal * 32:(pal + count) * 32] = source_palette[:count * 32]
                        if kind == 1:
                            expected_palette[:2] = b"\x00\x00"
                        for name, expected in (
                            ("graphics", expected_graphics), ("tilemap", expected_map),
                            ("palette", expected_palette),
                        ):
                            mismatch = next(
                                (i for i, pair in enumerate(zip(actual[name], expected))
                                 if pair[0] != pair[1]), None,
                            )
                            self.assertEqual(len(actual[name]), len(expected), name)
                            self.assertIsNone(
                                mismatch, f"{carrier}/{route}: {name} first mismatch {mismatch}",
                            )
                        saved = struct.unpack("<4I2H", actual["saved-cg"])
                        expected_bits = (1 << index) if kind == 2 else 0
                        self.assertEqual(saved, (expected_bits, 0, 0, 0, 2 * expected_bits, 0))
                        records.append({
                            "carrier": carrier, "route": route,
                            "graphics_bytes": len(actual["graphics"]),
                            "tilemap_bytes": len(actual["tilemap"]),
                            "palette_bytes": len(actual["palette"]),
                            "saved_cg_bits": saved[0],
                        })

            copies = []
            for kind, index, carrier in carriers[:4]:
                prefix = ROOT / "graphics" / carrier
                raw = prefix.with_suffix(".feimg2.bin").read_bytes()
                tsa = prefix.with_suffix(".fetsa2.bin").read_bytes()
                palette = prefix.with_suffix(".gbapal").read_bytes()[:6 * 32]
                self.assertEqual(len(palette), 6 * 32)
                entries = struct.unpack_from("<600H", tsa, 2)
                for source_bg, target_bg in ((3, 2), (2, 3)):
                    with self.subTest(carrier=carrier, source=source_bg, target=target_bg):
                        source_pal = 8 if source_bg == 3 else 0
                        target_pal = 8 if target_bg == 3 else 0
                        source_map = bytearray(tilemap_guard)
                        for y in range(20):
                            for x in range(30):
                                struct.pack_into(
                                    "<H", source_map, 2 * (y * 32 + x),
                                    entries[(19 - y) * 30 + x] + (source_pal << 12),
                                )
                        setup = [
                            f"restore {work / 'tilemap-input'} binary &gBG{source_bg}TilemapBuffer",
                            "set $fade = (struct ConvoBackgroundFadeProc *)$sp",
                            f"set $fade->unkType = {kind}",
                            f"set $fade->bgIndex = {index}",
                            "set $r0 = $fade",
                            "set $lr = (unsigned int)&StartGame | 1",
                            "tbreak StartGame",
                            f"set $pc = (unsigned int)&ConvoBackgroundFade_LoadBg{source_bg}",
                            "continue",
                        ]
                        actual = capture(
                            setup, f"call ConvoBackgroundFade_CopyBg{source_bg}ToBg{target_bg}(0)",
                            target_bg, source_bg=source_bg,
                        )
                        expected_map = bytearray(source_map)
                        for offset in range(0, len(expected_map), 2):
                            value = struct.unpack_from("<H", source_map, offset)[0]
                            bank = ((value >> 12) + target_pal - source_pal) & 15
                            struct.pack_into("<H", expected_map, offset, (value & 0xFFF) | (bank << 12))
                        expected_palette = bytearray(palette_guard)
                        expected_palette[source_pal * 32:(source_pal + 6) * 32] = palette
                        if kind == 1:
                            expected_palette[:2] = b"\x00\x00"
                        expected_palette[target_pal * 32:(target_pal + 6) * 32] = (
                            expected_palette[source_pal * 32:(source_pal + 6) * 32]
                        )
                        graphics = (
                            raw.ljust(0x5000, b"\x00") if kind == 2
                            else raw + actual["source-graphics-before"][len(raw):]
                        )
                        self.assertEqual(actual["graphics"], graphics)
                        self.assertEqual(actual["source-graphics"], graphics)
                        self.assertEqual(actual["tilemap"], expected_map)
                        self.assertEqual(actual["source-tilemap"], source_map)
                        self.assertEqual(actual["palette"], expected_palette)
                        saved = struct.unpack("<4I2H", actual["saved-cg"])
                        expected_bits = (1 << index) if kind == 2 else 0
                        self.assertEqual(saved, (expected_bits, 0, 0, 0, 2 * expected_bits, 0))
                        copies.append({"carrier": carrier, "source": source_bg, "target": target_bg})
        print(json.dumps({
            "selected_static_consumers": records, "outgoing_fade_copies": copies,
        }, sort_keys=True))
