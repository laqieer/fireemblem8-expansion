"""Exercise the actual project-selected guide slots on ARM/libmGBA."""

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples/restoration-of-grado"
sys.path.insert(0, str(ROOT / "tools/gba-playtest"))
sys.path.insert(0, str(ROOT / "tools/gba-playtest/tests"))

from host_mode import host_only_enabled
from probe_bindings import resolve_elf_symbol, resolve_elf_symbol_address
from scripts.localization.game_catalog.tests.runtime.initial_cache_native import _selected_tools


class GuideSelectionProbe(ctypes.LittleEndianStructure):
    _fields_ = [
        (name, ctypes.c_uint32)
        for name in (
            "phase", "coldLocked", "coldAllRead", "resetLocked", "resetAllRead",
            "unlockedLocked", "unlockedAllRead", "entryCount", "visibleCount",
            "readCount", "topicCount", "chapterCount", "sortSet", "sortCleared",
            "sortChapterMode", "sortChapterFlag", "sortTopicMode", "sortTopicFlag",
            "redrawCount", "unexpectedCall",
        )
    ] + [
        (name, ctypes.c_uint8 * size)
        for name, size in (
            ("topicOrder", 20), ("chapterOrder", 20),
            ("sortChapterIndices", 4), ("sortTopicIndices", 4),
            ("resetPermanent", 25), ("resetChapter", 5), ("unlockedPermanent", 25),
            ("sortChapterPermanent", 25), ("sortChapterFlags", 5),
            ("afterConsumersPermanent", 25), ("afterConsumersChapter", 5),
        )
    ]


@unittest.skipIf(host_only_enabled(), "explicit host-only lane excludes ARM/libmGBA execution")
class GradoSelectedGuideNativeTests(unittest.TestCase):
    def run_command(self, command, *, cwd=ROOT, timeout=120, env=None):
        result = subprocess.run(
            [str(value) for value in command], cwd=cwd,
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_selected_and_default_guide_slots(self):
        tools = _selected_tools(os.environ)
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="grado-guide-build-", dir=ROOT / "build") as directory, \
                tempfile.TemporaryDirectory(prefix="grado-guide-project-", dir=ROOT / "examples") as project_dir:
            work = Path(directory)
            project = Path(project_dir)
            (project / "src").symlink_to(EXAMPLE / "src", target_is_directory=True)
            (project / "include").symlink_to(EXAMPLE / "include", target_is_directory=True)
            (work / "split-const-data.h").write_text(
                '#include "global.h"\n#undef CONST_DATA\n#define CONST_DATA\n',
                encoding="ascii",
            )
            project_path = project.relative_to(ROOT).as_posix()
            (project / "project.mk").write_text(
                "FE8_PROJECT_PROFILES := probe\n"
                "FE8_PROJECT_DEFAULT_PROFILE := probe\n"
                "FE8_PROJECT_REQUIRED_SETTINGS := EXPANSION_SAVE_COMPAT_EPOCH\n"
                "FE8_PROJECT_probe_EXPANSION_SAVE_COMPAT_EPOCH := 256\n"
                "FE8_PROJECT_REPLACE := code\n"
                "FE8_PROJECT_INHERIT := $(filter-out code,$(FE8_PROJECT_MODULES))\n"
                f"FE8_PROJECT_CODE_REPLACEMENTS := src/bmguide.c={project_path}/src/static_guide.c "
                f"src/eventinfo.c={project_path}/src/native_flags.c\n"
                f"FE8_PROJECT_C_SOURCES := {project_path}/src/state.c\n",
                encoding="ascii",
            )
            inherited_environment = dict(os.environ, EXPANSION_AUTOPLAY_PLANNER="1")
            for config in ("debug", "release"):
                for selected in (False, True):
                    with self.subTest(config=config, selected=selected):
                        self._check_profile(
                            work, project, config, selected, tools, inherited_environment,
                        )

    def _check_profile(self, work, project, config, selected, tools, inherited_environment):
        build_root = work.relative_to(ROOT)
        if selected:
            output = build_root / "projects" / project.name / "probe" / config / "aapcs"
        else:
            output = build_root / config / "aapcs"
        sources = ["src/bmguide.o", "src/eventinfo.o", "src/bmguide_config.o"]
        if selected:
            sources.append(f"examples/{project.name}/src/state.o")
        objects = [output / source for source in sources]
        environment = inherited_environment.copy()
        toolchain_overrides = {
            "MODERN_CC", "MODERN_LD", "MODERN_NM", "MODERN_OBJCOPY",
            "MODERN_OBJDUMP", "MODERN_READELF", "MODERN_SIZE",
            "MODERN_TOOLCHAIN_ROOT", "MODERN_BINUTILS_DIR",
            "MODERN_NEWLIB_INCLUDE", "MODERN_NEWLIB_LIB",
        }
        for variable in tuple(environment):
            if (
                variable.startswith(("EXPANSION_", "FE8_"))
                or variable.startswith("MODERN_") and variable not in toolchain_overrides
                or variable in {
                    "ASSET_MANIFEST", "MAKE", "MAKEFILES", "MAKEFLAGS", "GNUMAKEFLAGS",
                    "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL",
                }
            ):
                del environment[variable]
        self.run_command([
            "make", "--no-print-directory", "-f", "Makefile", "-j2",
            "AUTOTOOLS_CONFIG_MK=/dev/null",
            "FE8_PROJECT=" + (project.name if selected else "default"),
            "FE8_PROJECT_PROFILE=" + ("probe" if selected else "default"),
            "EXPANSION_SAVE_COMPAT_EPOCH=" + ("256" if selected else "2"),
            "MODERN_BUILD_ROOT=" + build_root.as_posix(), "MODERN_CONFIG=" + config,
            "MODERN_ABI=aapcs",
            "MODERN_LAYOUT_FLAGS=-ffunction-sections -fdata-sections "
            "-fno-merge-constants -fno-merge-all-constants",
            "MODERN_INCLUDE_FLAGS=-Iinclude -I. -include "
            + (build_root / "split-const-data.h").as_posix(),
            *objects,
        ], env=environment)

        profile = work / (config + ("-selected" if selected else "-default") + "-\u00e1")
        profile.mkdir()
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
        driver = profile / "driver.o"
        self.run_command([
            tools["MODERN_CC"], *driver_flags, "-mcpu=arm7tdmi", "-mthumb",
            "-mthumb-interwork", "-mabi=aapcs", "-std=gnu11", "-ffreestanding",
            "-fno-builtin", "-fno-common", "-ffunction-sections", "-fdata-sections",
            "-DMODERN=1", "-DNONMATCHING=1", f"-DGRADO_GUIDE_SELECTED={int(selected)}",
            "-I" + str(ROOT / "include"), "-I" + str(EXAMPLE / "include"),
            "-I" + str(ROOT), *(("-isystem", newlib) if newlib else ()),
            "-Werror=implicit-function-declaration", "-Werror=incompatible-pointer-types",
            "-c", ROOT / "tools/gba-playtest/tests/c/grado_guide_selected_native.c",
            "-o", driver,
        ])
        for source, name in (
            (ROOT / "src/rom_header.s", "header.o"),
            (ROOT / "scripts/localization/game_catalog/tests/runtime/module_native_boot.S", "boot.o"),
        ):
            self.run_command([
                tools["MODERN_CC"], *driver_flags, "-mcpu=arm7tdmi", "-mthumb-interwork",
                "-I" + str(ROOT / "include"), "-I" + str(ROOT), "-c", source, "-o", profile / name,
            ])

        linker = profile / "module.ld"
        linker.write_text("""
OUTPUT_ARCH(arm)
ENTRY(Init)
MEMORY {
 ROM(rx) : ORIGIN = 0x08000000, LENGTH = 1M
 EWRAM(rwx) : ORIGIN = 0x02000000, LENGTH = 256K
 IWRAM(rwx) : ORIGIN = 0x03000000, LENGTH = 32K
}
SECTIONS {
 .rom : {
   KEEP(header.o(.text))
   KEEP(boot.o(.text.boot))
   *(.text*) *(.rodata*) . = ALIGN(4);
 } > ROM
 .data : {
   __data_start = .; *(.data*) . = ALIGN(4); __data_end = .;
 } > EWRAM AT> ROM
 __data_load = LOADADDR(.data);
 .iwram (NOLOAD) : {
   __iwram_start = .; *(iwram_data*) . = ALIGN(4); __iwram_end = .;
 } > IWRAM
 .ewram (NOLOAD) : {
   __ewram_start = .; *(ewram_data*) *(.bss*) *(COMMON) . = ALIGN(4); __ewram_end = .;
 } > EWRAM
 /DISCARD/ : { *(.comment) *(.note*) }
}
ASSERT(SIZEOF(.iwram) < 0x7800, "module IWRAM overlaps stack")
ASSERT(SIZEOF(.data) + SIZEOF(.ewram) < 0x40000, "module EWRAM overflow")
""", encoding="ascii")
        elf, rom = profile / "guide.elf", profile / "guide.gba"
        self.run_command([
            tools["MODERN_CC"], *driver_flags, *library_flags, "-mcpu=arm7tdmi",
            "-mthumb", "-mthumb-interwork", "-mabi=aapcs", "-nostartfiles",
            "-Wl,--gc-sections", "-Wl,--no-undefined", "-Wl,-T,module.ld",
            "header.o", "boot.o", "driver.o",
            *(ROOT / obj for obj in objects), "-lc", "-lnosys", "-lgcc", "-o", "guide.elf",
        ], cwd=profile)
        self.run_command([
            tools["MODERN_OBJCOPY"], "-O", "binary", "--gap-fill=0xff", elf, rom,
        ])

        defined = {
            line.split()[0]
            for line in self.run_command([
                tools["MODERN_NM"], "-g", "--defined-only", "--format=posix", elf,
            ]).splitlines()
        }
        guide_policy = {
            "GradoGuide_CheckFlag", "GradoGuide_SetFlag", "GradoGuide_ClearFlag",
        }
        self.assertEqual(defined & guide_policy, guide_policy if selected else set())
        reset_slot = {
            line.split()[0]
            for line in self.run_command([
                tools["MODERN_NM"], "-g", "--defined-only", "--format=posix",
                ROOT / output / "src/eventinfo.o",
            ]).splitlines()
        }
        self.assertEqual("GradoNative_ResetPermanentFlags" in reset_slot, selected)
        storage = [
            resolve_elf_symbol(elf, name, tools["MODERN_NM"])
            for name in ("gPermanentFlagBits", "gChapterFlagBits")
        ]
        self.assertEqual([extent for _, extent in storage], [25, 5])
        for address, extent in storage:
            self.assertGreaterEqual(address, 0x03000000)
            self.assertLessEqual(address + extent, 0x03007800)
        self.assertTrue(
            storage[0][0] + storage[0][1] <= storage[1][0]
            or storage[1][0] + storage[1][1] <= storage[0][0]
        )

        _, size = resolve_elf_symbol(elf, "gGuideSelectionProbe", tools["MODERN_NM"])
        self.assertEqual(size, ctypes.sizeof(GuideSelectionProbe))
        scenario = profile / "scenario.json"
        scenario.write_text(json.dumps({
            "schema_version": 1, "name": "grado-selected-guide", "frames": [],
            "checkpoints": [{
                "name": "guide-selection", "frame": 120, "framebuffer": False,
                "probes": [
                    {"address": f"gGuideSelectionProbe+0x{offset:x}", "size": 4}
                    for offset in range(0, size, 4)
                ],
            }],
        }), encoding="utf-8")
        capture = profile / "capture.json"
        self.run_command([
            sys.executable, "-B", ROOT / "tools/gba-playtest/gba_playtest.py",
            "capture", "--rom", rom, "--elf", elf, "--nm", tools["MODERN_NM"],
            "--scenario", scenario, "--output", capture,
        ])
        values = {
            probe["address"]: int(probe["value"], 16)
            for probe in json.loads(capture.read_text())["checkpoints"][0]["probes"]
        }
        data = b"".join(
            values[f"gGuideSelectionProbe+0x{offset:x}"].to_bytes(4, "little")
            for offset in range(0, size, 4)
        )
        probe = GuideSelectionProbe.from_buffer_copy(data)
        self.assertEqual(probe.phase, 2, f"unexpected guide adapter call {probe.unexpectedCall}")
        scalars = tuple(getattr(probe, name) for name, _ in GuideSelectionProbe._fields_[:20])
        self.assertEqual(scalars, (
            2, int(selected), int(selected), int(not selected), 1, 0, int(selected),
            56, 56, 56 if selected else 0, 11, 9, 1, 0, 1, 1, 0, 0, 2, 0,
        ))
        self.assertEqual(bytes(probe.topicOrder[:11]), bytes(range(1, 12)))
        self.assertEqual(bytes(probe.chapterOrder[:9]), bytes(range(9)))
        self.assertEqual(bytes(probe.sortChapterIndices), bytes(4))
        self.assertEqual(bytes(probe.sortTopicIndices), bytes(4))
        reset = bytearray(25)
        if selected:
            reset[10] = 1
        unlocked = bytearray(reset)
        if not selected:
            for flag in range(180, 236):
                position = flag - 101
                unlocked[position // 8] |= 1 << (position % 8)
        sorted_chapter = bytearray(unlocked)
        sorted_chapter[9] |= 0x40
        self.assertEqual(bytes(probe.resetPermanent), reset)
        self.assertEqual(bytes(probe.resetChapter), b"\xA6" * 5)
        self.assertEqual(bytes(probe.unlockedPermanent), unlocked)
        self.assertEqual(bytes(probe.sortChapterPermanent), sorted_chapter)
        self.assertEqual(bytes(probe.sortChapterFlags), b"\xA6" * 5)
        self.assertEqual(bytes(probe.afterConsumersPermanent), unlocked)
        self.assertEqual(bytes(probe.afterConsumersChapter), b"\xA6" * 5)
        resources = {
            name: resolve_elf_symbol_address(elf, "__" + name + "_end", tools["MODERN_NM"])
            - resolve_elf_symbol_address(elf, "__" + name + "_start", tools["MODERN_NM"])
            for name in ("data", "ewram", "iwram")
        }
        print(json.dumps({
            "config": config, "selected": selected, "rom_bytes": rom.stat().st_size,
            "ewram_bytes": resources["data"] + resources["ewram"],
            "iwram_bytes": resources["iwram"], "observations": scalars,
        }))
