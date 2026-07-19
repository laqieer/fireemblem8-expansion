import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODERN_MK = ROOT / "modern.mk"
COHORT = (
    "fe3_dummy",
    "unit_facing",
    "time",
    "ramfunc",
    "agb_sram",
    "bmlib-math",
    "rng",
    "irq",
)


class ModernBuildTests(unittest.TestCase):
    def make_fixture(self, destination: Path) -> Path:
        root = destination / "repo with spaces"
        (root / "include").mkdir(parents=True)
        (root / "src").mkdir()
        shutil.copy2(MODERN_MK, root / "modern.mk")
        (root / "Makefile").write_text(
            "PREFIX ?= arm-none-eabi-\n"
            "EXE :=\n"
            "CLEAN_DIRS :=\n"
            "MODERN_COHORT_ASM_SOURCES :=\n"
            "include modern.mk\n"
            "%.d:\n"
            "\t@touch legacy-dependency-regenerated\n",
            encoding="utf-8",
        )
        (root / "include" / "global.h").write_text(
            "#include <stdlib.h>\n"
            "#include <stdint.h>\n"
            "#include <limits.h>\n"
            '#include "fixture.h"\n',
            encoding="utf-8",
        )
        (root / "include" / "fixture.h").write_text(
            "#define FIXTURE_VALUE 1\n", encoding="utf-8"
        )
        for index, name in enumerate(COHORT):
            (root / "src" / f"{name}.c").write_text(
                '#include "global.h"\n'
                f"int modern_fixture_{index}(void) {{ return FIXTURE_VALUE + {index}; }}\n",
                encoding="utf-8",
            )
        return root

    def make_asm_fixture(self, destination: Path) -> Path:
        root = destination / "asm repo with spaces"
        (root / "include").mkdir(parents=True)
        (root / "asm").mkdir()
        shutil.copy2(MODERN_MK, root / "modern.mk")
        (root / "Makefile").write_text(
            "PREFIX ?= arm-none-eabi-\n"
            "EXE :=\n"
            "CLEAN_DIRS :=\n"
            "MODERN_COHORT_ASM_SOURCES :=\n"
            "include modern.mk\n",
            encoding="utf-8",
        )
        (root / "include" / "global.h").write_text(
            "#include <stdlib.h>\n"
            "#include <stdint.h>\n"
            "#include <limits.h>\n",
            encoding="utf-8",
        )
        (root / "include" / "fixture.inc").write_text(
            ".equ FIXTURE_ASM_VALUE, 1\n", encoding="utf-8"
        )
        (root / "asm" / "fixture.s").write_text(
            '\t.INCLUDE "fixture.inc"\n'
            "\t.text\n"
            "\t.thumb\n"
            "\t.global modern_asm_fixture_probe\n"
            "\t.thumb_func\n"
            "modern_asm_fixture_probe:\n"
            "\tbx lr\n",
            encoding="utf-8",
        )
        return root

    def make_all_fixture(self, destination: Path) -> Path:
        # A minimal synthetic tree exercising expansion-modern-all's three
        # source kinds (normal C, preprocessed data C, handwritten assembly)
        # with fake preproc/scaninc scripts standing in for the real tools,
        # so the test does not depend on the real INCBIN_* macro grammar or
        # any pre-generated repository graphics assets.
        root = destination / "all repo with spaces"
        (root / "include").mkdir(parents=True)
        (root / "src" / "data").mkdir(parents=True)
        (root / "asm").mkdir()
        (root / "assets").mkdir()
        (root / "tools" / "preproc").mkdir(parents=True)
        (root / "tools" / "scaninc").mkdir(parents=True)
        shutil.copy2(MODERN_MK, root / "modern.mk")
        (root / "Makefile").write_text(
            "PREFIX ?= arm-none-eabi-\n"
            "EXE :=\n"
            "CLEAN_DIRS :=\n"
            "MODERN_COHORT_SOURCES :=\n"
            "MODERN_COHORT_ASM_SOURCES :=\n"
            "include modern.mk\n",
            encoding="utf-8",
        )
        (root / "include" / "global.h").write_text(
            "#include <stdlib.h>\n"
            "#include <stdint.h>\n"
            "#include <limits.h>\n"
            '#include "fixture.h"\n',
            encoding="utf-8",
        )
        (root / "include" / "fixture.h").write_text(
            "#define FIXTURE_VALUE 1\n", encoding="utf-8"
        )
        (root / "include" / "fixture.inc").write_text(
            ".equ FIXTURE_ASM_VALUE, 1\n", encoding="utf-8"
        )
        (root / "src" / "normal_fixture.c").write_text(
            '#include "global.h"\n'
            "int modern_all_normal_fixture(void) { return FIXTURE_VALUE; }\n",
            encoding="utf-8",
        )
        (root / "assets" / "fixture_asset.bin").write_text("v1", encoding="utf-8")
        (root / "assets" / "fixture_asset_2.bin").write_text("v1", encoding="utf-8")
        # Two INCBIN assets deliberately exercise scaninc reporting more than
        # one path (real scaninc prints one path per line): the appended
        # dependency line must still collapse to a single, valid Makefile
        # line even when scaninc's output spans multiple lines.
        (root / "src" / "data" / "data_fixture.c").write_text(
            '#include "global.h"\n'
            'FIXTURE_INCBIN("assets/fixture_asset.bin")\n'
            'FIXTURE_INCBIN("assets/fixture_asset_2.bin")\n',
            encoding="utf-8",
        )
        (root / "asm" / "fixture.s").write_text(
            '\t.INCLUDE "fixture.inc"\n'
            "\t.text\n"
            "\t.thumb\n"
            "\t.global modern_all_asm_fixture_probe\n"
            "\t.thumb_func\n"
            "modern_all_asm_fixture_probe:\n"
            "\tbx lr\n",
            encoding="utf-8",
        )

        fake_preproc = root / "tools" / "preproc" / "preproc"
        fake_preproc.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, re, sys\n"
            "text = pathlib.Path(sys.argv[1]).read_text()\n"
            "counter = [0]\n"
            "def expand(match):\n"
            "    data = pathlib.Path(match.group(1)).read_bytes()\n"
            "    body = ', '.join(str(b) for b in data)\n"
            "    counter[0] += 1\n"
            "    name = 'gFixtureAsset' + str(counter[0])\n"
            "    return 'const unsigned char ' + name + '[] = {' + body + '};'\n"
            "text = re.sub(r'FIXTURE_INCBIN\\(\"([^\"]+)\"\\)', expand, text)\n"
            "sys.stdout.write(text)\n",
            encoding="utf-8",
        )
        fake_preproc.chmod(0o755)

        fake_scaninc = root / "tools" / "scaninc" / "scaninc"
        fake_scaninc.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, re, sys\n"
            "text = pathlib.Path(sys.argv[-1]).read_text()\n"
            "for match in re.finditer(r'FIXTURE_INCBIN\\(\"([^\"]+)\"\\)', text):\n"
            "    print(match.group(1))\n",
            encoding="utf-8",
        )
        fake_scaninc.chmod(0o755)

        return root

    def make(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["make", "--no-print-directory", *arguments],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def tool_overrides(self) -> list[str] | None:
        prefix = os.environ.get("PREFIX", "arm-none-eabi-")
        root = os.environ.get("MODERN_TOOLCHAIN_ROOT", "")
        cc = os.environ.get("MODERN_CC", "")
        if not cc and root:
            cc = str(Path(root) / "bin" / f"{prefix}gcc")
        if not cc:
            cc = shutil.which(f"{prefix}gcc") or ""

        objdump = os.environ.get("MODERN_OBJDUMP", "")
        if not objdump:
            objdump = shutil.which(f"{prefix}objdump") or ""

        if not cc or not objdump:
            return None

        overrides = [f"MODERN_CC={cc}", f"MODERN_OBJDUMP={objdump}"]
        for variable in ("MODERN_BINUTILS_DIR", "MODERN_NEWLIB_INCLUDE"):
            value = os.environ.get(variable)
            if value:
                overrides.append(f"{variable}={value}")
        return overrides

    def test_rejects_unknown_config_and_abi(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_fixture(Path(temporary))
            config = self.make(
                root, "expansion-modern-cohort", "MODERN_CONFIG=profile"
            )
            abi = self.make(root, "expansion-modern-cohort", "MODERN_ABI=eabi")

        self.assertNotEqual(config.returncode, 0)
        self.assertIn("unsupported MODERN_CONFIG 'profile'", config.stdout)
        self.assertNotEqual(abi.returncode, 0)
        self.assertIn("unsupported MODERN_ABI 'eabi'", abi.stdout)

    def test_missing_and_wrong_target_compilers_are_actionable(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            root = self.make_fixture(temporary_path)

            missing = self.make(
                root,
                "expansion-modern-toolchain-check",
                f"MODERN_CC={temporary_path / 'missing-gcc'}",
            )

            wrong = temporary_path / "wrong-gcc"
            wrong.write_text(
                "#!/bin/sh\n"
                'case "$*" in\n'
                '  *--version*) printf "%s\\n" "fixture gcc 1.0" ;;\n'
                '  *-dumpmachine*) printf "%s\\n" "x86_64-linux-gnu" ;;\n'
                '  *) exit 1 ;;\n'
                "esac\n",
                encoding="utf-8",
            )
            wrong.chmod(0o755)
            wrong_target = self.make(
                root,
                "expansion-modern-toolchain-check",
                f"MODERN_CC={wrong}",
            )
            legacy_regenerated = (root / "legacy-dependency-regenerated").exists()

        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("modern compiler not found", missing.stdout)
        self.assertIn("MODERN_TOOLCHAIN_ROOT/MODERN_CC", missing.stdout)
        self.assertFalse(legacy_regenerated)
        self.assertNotEqual(wrong_target.returncode, 0)
        self.assertIn("targets 'x86_64-linux-gnu'", wrong_target.stdout)
        self.assertIn("expected 'arm-none-eabi'", wrong_target.stdout)

    def test_prep_sprite_draw_layout_in_all_modern_modes(self):
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            source = temporary_path / "prep_sprite_draw_layout.c"
            output_root = temporary_path / "layout-objects"
            source.write_text(
                "#include <stddef.h>\n"
                '#include "prepscreen.h"\n'
                "_Static_assert(offsetof(struct PrepSpriteDrawProc, unk2A) == 0x2A, "
                '"PrepSpriteDrawProc.unk2A offset");\n'
                "_Static_assert(offsetof(struct PrepSpriteDrawProc, apProc) == 0x38, "
                '"PrepSpriteDrawProc.apProc offset");\n'
                "_Static_assert(sizeof(struct PrepSpriteDrawProc) == 0x3C, "
                '"PrepSpriteDrawProc size");\n',
                encoding="utf-8",
            )

            for config in ("debug", "release"):
                for abi in ("aapcs", "apcs-gnu"):
                    result = self.make(
                        ROOT,
                        "expansion-modern-cohort",
                        f"MODERN_CONFIG={config}",
                        f"MODERN_ABI={abi}",
                        f"MODERN_BUILD_ROOT={output_root}",
                        f"MODERN_COHORT_SOURCES={source}",
                        "MODERN_COHORT_ASM_SOURCES=",
                        *overrides,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        f"{config}/{abi} layout probe failed:\n{result.stdout}",
                    )

            self.assertEqual(len(list(output_root.rglob("*.o"))), 4)
            self.assertEqual(len(list(output_root.rglob("*.d"))), 4)

    def test_world_map_save_layout_in_all_modern_modes(self):
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            source = temporary_path / "world_map_save_layout.c"
            output_root = temporary_path / "layout-objects"
            source.write_text(
                "#include <stddef.h>\n"
                '#include "src/bmsave-gmap.c"\n'
                "_Static_assert(sizeof(struct GMNode) == 4, "
                '"GMNode size");\n'
                "_Static_assert(_Alignof(struct GMNode) == 4, "
                '"GMNode alignment");\n'
                "_Static_assert(sizeof(struct OpenPaths) == 0x24, "
                '"OpenPaths size");\n'
                "_Static_assert(_Alignof(struct OpenPaths) == 4, "
                '"OpenPaths alignment");\n'
                "_Static_assert(sizeof(union PackedWorldMapUnit) == 4, "
                '"PackedWorldMapUnit size");\n'
                "_Static_assert(_Alignof(union PackedWorldMapUnit) == 4, "
                '"PackedWorldMapUnit alignment");\n'
                "_Static_assert(sizeof(struct GMapSaveInfo) == 0x24, "
                '"GMapSaveInfo size");\n'
                "_Static_assert(_Alignof(struct GMapSaveInfo) == 4, "
                '"GMapSaveInfo alignment");\n'
                "_Static_assert(offsetof(struct GMapSaveInfo, skirmishState) == 0x20, "
                '"GMapSaveInfo.skirmishState offset");\n'
                "_Static_assert(sizeof(struct GMapData) == 0xD0, "
                '"GMapData size");\n'
                "_Static_assert(offsetof(struct GMapData, nodes) == 0x30, "
                '"GMapData.nodes offset");\n'
                "_Static_assert(sizeof(((struct GMapData *)0)->nodes) == 0x74, "
                '"GMapData.nodes size");\n'
                "_Static_assert(offsetof(struct GMapData, openPaths) == 0xA4, "
                '"GMapData.openPaths offset");\n'
                "_Static_assert(offsetof(struct GMapData, current_node) == 0xC8, "
                '"GMapData.current_node offset");\n'
                "const union PackedWorldMapUnit gPackedWorldMapUnitProbe = {\n"
                "    .pat1 = {\n"
                "        .unk0_0 = 1,\n"
                "        .unk0_1 = 0x2A,\n"
                "        .unk0_7 = 1,\n"
                "        .unk1 = 0x5A,\n"
                "    },\n"
                "};\n",
                encoding="utf-8",
            )

            for config in ("debug", "release"):
                for abi in ("aapcs", "apcs-gnu"):
                    result = self.make(
                        ROOT,
                        "expansion-modern-cohort",
                        f"MODERN_CONFIG={config}",
                        f"MODERN_ABI={abi}",
                        f"MODERN_BUILD_ROOT={output_root}",
                        f"MODERN_COHORT_SOURCES={source}",
                        "MODERN_COHORT_ASM_SOURCES=",
                        *overrides,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        f"{config}/{abi} world-map layout probe failed:\n"
                        f"{result.stdout}",
                    )

            objects = sorted(output_root.rglob("*.o"))
            self.assertEqual(len(objects), 4)
            self.assertEqual(len(list(output_root.rglob("*.d"))), 4)

            objdump = next(
                value.split("=", 1)[1]
                for value in overrides
                if value.startswith("MODERN_OBJDUMP=")
            )
            for object_file in objects:
                section = subprocess.run(
                    [objdump, "-s", "-j", ".rodata", str(object_file)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(section.returncode, 0, section.stdout)
                self.assertIn("d55a0000", "".join(section.stdout.split()).lower())

    def test_plain_inline_definition_exports_global_symbol_in_all_modern_modes(self):
        # Legacy agbcc always emits a plain (non-static, non-extern) `inline`
        # definition as an externally linkable symbol. Under bare -std=gnu11,
        # GCC may instead treat it as a C99 "inline definition" and elide the
        # external body once it can eliminate every call site it can see,
        # silently breaking any other translation unit that still calls it
        # (see src/bmunit.c's CanUnitCrossTerrain). This probe defines such a
        # function with no paired extern declaration and no internal caller
        # -- the worst case for elision -- and asserts the modern object
        # still exports it as a defined, global .text symbol in every
        # debug/release x aapcs/apcs-gnu cell. It fails if -fgnu89-inline is
        # removed from MODERN_LANGUAGE_FLAGS.
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            source = temporary_path / "gnu89_inline_symbol.c"
            output_root = temporary_path / "gnu89-inline-objects"
            source.write_text(
                "inline int modern_gnu89_inline_probe(void) { return 1; }\n",
                encoding="utf-8",
            )

            for config in ("debug", "release"):
                for abi in ("aapcs", "apcs-gnu"):
                    result = self.make(
                        ROOT,
                        "expansion-modern-cohort",
                        f"MODERN_CONFIG={config}",
                        f"MODERN_ABI={abi}",
                        f"MODERN_BUILD_ROOT={output_root}",
                        f"MODERN_COHORT_SOURCES={source}",
                        "MODERN_COHORT_ASM_SOURCES=",
                        *overrides,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        f"{config}/{abi} gnu89-inline probe failed:\n"
                        f"{result.stdout}",
                    )

            objects = sorted(output_root.rglob("*.o"))
            self.assertEqual(len(objects), 4)

            objdump = next(
                value.split("=", 1)[1]
                for value in overrides
                if value.startswith("MODERN_OBJDUMP=")
            )
            for object_file in objects:
                symbols = subprocess.run(
                    [objdump, "-t", str(object_file)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
                self.assertEqual(symbols.returncode, 0, symbols.stdout)
                defined_global = [
                    line
                    for line in symbols.stdout.splitlines()
                    if "modern_gnu89_inline_probe" in line
                    and ".text" in line
                    and " g " in line
                ]
                self.assertTrue(
                    defined_global,
                    "plain inline definition was not exported as a global "
                    f".text symbol for {object_file.name}; -fgnu89-inline "
                    "may be missing from MODERN_LANGUAGE_FLAGS:\n"
                    f"{symbols.stdout}",
                )

    def test_real_objects_are_isolated_architectural_and_dependency_aware(self):
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_fixture(Path(temporary))
            result = self.make(
                root,
                "expansion-modern-cohort",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                *overrides,
            )
            self.assertEqual(result.returncode, 0, result.stdout)

            output = root / "build" / "expansion-modern" / "debug" / "aapcs"
            objects = sorted(output.glob("src/*.o"))
            dependencies = sorted(output.glob("src/*.d"))
            self.assertEqual(len(objects), 8)
            self.assertEqual(len(dependencies), 8)
            self.assertFalse(list((root / "src").glob("*.o")))
            self.assertFalse(list((root / "src").glob("*.d")))
            self.assertFalse(list((root / "src").glob("*.s")))

            objdump = next(
                value.split("=", 1)[1]
                for value in overrides
                if value.startswith("MODERN_OBJDUMP=")
            )
            architecture = subprocess.run(
                [objdump, "-f", *map(str, objects)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(architecture.returncode, 0, architecture.stdout)
            self.assertEqual(architecture.stdout.count("file format elf32-littlearm"), 8)
            self.assertEqual(architecture.stdout.count("architecture: armv4t"), 8)

            dependency_text = dependencies[0].read_text(encoding="utf-8")
            self.assertIn("include/fixture.h", dependency_text)
            watched_object = objects[0]
            before = watched_object.stat().st_mtime_ns
            time.sleep(1.1)
            (root / "include" / "fixture.h").touch()
            query = self.make(
                root,
                "--question",
                str(watched_object.relative_to(root)),
                *overrides,
            )
            self.assertEqual(query.returncode, 1, query.stdout)
            rebuild = self.make(
                root, str(watched_object.relative_to(root)), *overrides
            )
            self.assertEqual(rebuild.returncode, 0, rebuild.stdout)
            self.assertGreater(watched_object.stat().st_mtime_ns, before)
            self.assertFalse(list((root / "src").glob("*.o")))
            self.assertFalse(list((root / "src").glob("*.d")))
            self.assertFalse(list((root / "src").glob("*.s")))

    def test_synthetic_assembly_is_architectural_and_dependency_aware(self):
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_asm_fixture(Path(temporary))
            asm_arguments = (
                "expansion-modern-cohort",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "MODERN_COHORT_SOURCES=",
                "MODERN_COHORT_ASM_SOURCES=asm/fixture.s",
            )
            result = self.make(root, *asm_arguments, *overrides)
            self.assertEqual(result.returncode, 0, result.stdout)

            output = root / "build" / "expansion-modern" / "debug" / "aapcs"
            objects = sorted(output.glob("asm/*.o"))
            dependencies = sorted(output.glob("asm/*.d"))
            self.assertEqual(len(objects), 1)
            self.assertEqual(len(dependencies), 1)
            self.assertFalse(list((root / "asm").glob("*.o")))
            self.assertFalse(list((root / "asm").glob("*.d")))

            objdump = next(
                value.split("=", 1)[1]
                for value in overrides
                if value.startswith("MODERN_OBJDUMP=")
            )
            architecture = subprocess.run(
                [objdump, "-f", *map(str, objects)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(architecture.returncode, 0, architecture.stdout)
            self.assertEqual(architecture.stdout.count("file format elf32-littlearm"), 1)
            self.assertEqual(architecture.stdout.count("architecture: armv4t"), 1)

            # GAS's --MD tracks the uppercase .INCLUDE directive, so the
            # generated .d file must list the included fixture header.
            dependency_text = dependencies[0].read_text(encoding="utf-8")
            self.assertIn("include/fixture.inc", dependency_text)

            watched_object = objects[0]
            before = watched_object.stat().st_mtime_ns
            time.sleep(1.1)
            (root / "include" / "fixture.inc").write_text(
                ".equ FIXTURE_ASM_VALUE, 2\n", encoding="utf-8"
            )
            rebuild = self.make(root, *asm_arguments, *overrides)
            self.assertEqual(rebuild.returncode, 0, rebuild.stdout)
            self.assertGreater(watched_object.stat().st_mtime_ns, before)
            self.assertFalse(list((root / "asm").glob("*.o")))
            self.assertFalse(list((root / "asm").glob("*.d")))

    def test_full_source_target_is_architectural_and_dependency_aware(self):
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_all_fixture(Path(temporary))
            all_arguments = (
                "expansion-modern-all",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "MODERN_ALL_C_SOURCES=src/normal_fixture.c",
                "MODERN_ALL_DATA_C_SOURCES=src/data/data_fixture.c",
                "MODERN_ALL_ASM_SOURCES=asm/fixture.s",
                "MODERN_PREPROC=tools/preproc/preproc",
                "MODERN_SCANINC=tools/scaninc/scaninc",
            )
            result = self.make(root, *all_arguments, *overrides)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("Built 3 modern relocatable objects", result.stdout)

            output = root / "build" / "expansion-modern" / "debug" / "aapcs"
            normal_object = output / "src" / "normal_fixture.o"
            data_pre = output / "src" / "data" / "data_fixture.pre.c"
            data_object = output / "src" / "data" / "data_fixture.o"
            asm_object = output / "asm" / "fixture.o"

            objects = sorted(output.rglob("*.o"))
            dependencies = sorted(
                path
                for path in output.rglob("*.d")
                if not path.name.endswith(".assets.d") and not path.name.endswith(".headers.d")
            )
            asset_dependencies = sorted(output.rglob("*.assets.d"))
            header_dependencies = sorted(output.rglob("*.headers.d"))
            self.assertEqual(len(objects), 3)
            self.assertEqual(len(dependencies), 3)
            self.assertEqual(len(asset_dependencies), 1)
            # MODERN_ALL_C_HEADER_DEPS covers every MODERN_ALL_C_SOURCES
            # member (here just normal_fixture.c); data/asm sources are
            # untouched by this second, generated-header bootstrap.
            self.assertEqual(len(header_dependencies), 1)
            self.assertTrue(data_pre.is_file())
            self.assertTrue(normal_object.is_file())
            self.assertTrue(data_object.is_file())
            self.assertTrue(asm_object.is_file())

            # The generated .headers.d file must use GCC's own "-MM -MG"
            # scan (not scaninc, which cannot see not-yet-generated
            # headers) and must still report real, already-existing
            # headers such as fixture.h correctly.
            normal_header_dependency_path = output / "src" / "normal_fixture.headers.d"
            self.assertTrue(normal_header_dependency_path.is_file())
            normal_header_dependency_text = normal_header_dependency_path.read_text(
                encoding="utf-8"
            )
            self.assertIn("include/fixture.h", normal_header_dependency_text)

            # No intermediate/output ever lands in the source tree.
            self.assertFalse(list((root / "src").rglob("*.o")))
            self.assertFalse(list((root / "src").rglob("*.d")))
            self.assertFalse(list((root / "src").rglob("*.pre.c")))
            self.assertFalse(list((root / "src").rglob("*.assets.d")))
            self.assertFalse(list((root / "src").rglob("*.headers.d")))
            self.assertFalse(list((root / "asm").glob("*.o")))
            self.assertFalse(list((root / "asm").glob("*.d")))

            objdump = next(
                value.split("=", 1)[1]
                for value in overrides
                if value.startswith("MODERN_OBJDUMP=")
            )
            architecture = subprocess.run(
                [objdump, "-f", *map(str, objects)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(architecture.returncode, 0, architecture.stdout)
            self.assertEqual(
                architecture.stdout.count("file format elf32-littlearm"), 3
            )
            self.assertEqual(architecture.stdout.count("architecture: armv4t"), 3)

            # The data object's own .d file only carries GCC's own
            # object/header rule now (asset tracking moved to the generated
            # .assets.d bootstrap file below), so it still names the header
            # but no longer needs to name the INCBIN assets itself.
            data_dependency_path = output / "src" / "data" / "data_fixture.d"
            data_dependency_text = data_dependency_path.read_text(encoding="utf-8")
            self.assertIn("include/fixture.h", data_dependency_text)

            # The generated $(MODERN_OUTPUT_DIR)/%.assets.d bootstrap file
            # must carry the pre.c-depends-on-source-and-asset rule instead.
            # The fixture's data source intentionally embeds two INCBIN
            # assets: real scaninc prints one path per line, so this proves
            # the generated rule collapses multi-line scaninc output into
            # one valid Makefile line rather than corrupting the file with
            # a bare continuation-less newline (which would break "make"
            # with "missing separator" on any subsequent goal, since this
            # file is `include`d directly, not filtered through
            # $(wildcard ...)).
            asset_dependency_path = output / "src" / "data" / "data_fixture.assets.d"
            self.assertTrue(asset_dependency_path.is_file())
            asset_dependency_text = asset_dependency_path.read_text(encoding="utf-8")
            self.assertIn("src/data/data_fixture.c", asset_dependency_text)
            self.assertIn("assets/fixture_asset.bin", asset_dependency_text)
            self.assertIn("assets/fixture_asset_2.bin", asset_dependency_text)

            appended_rule_lines = [
                line
                for line in asset_dependency_text.splitlines()
                if line.startswith("build/expansion-modern/debug/aapcs/src/data/data_fixture.pre.c:")
            ]
            self.assertEqual(len(appended_rule_lines), 1)
            self.assertIn("assets/fixture_asset.bin", appended_rule_lines[0])
            self.assertIn("assets/fixture_asset_2.bin", appended_rule_lines[0])

            # A corrupted (multi-line, non-continued) generated rule breaks
            # make's parser outright as soon as the .assets.d is included;
            # re-invoking make with the same arguments must keep succeeding
            # now that the asset list is collapsed onto one line, and must
            # be a true no-op (nothing touched, nothing rebuilt).
            before_reparse_normal = normal_object.stat().st_mtime_ns
            before_reparse_data = data_object.stat().st_mtime_ns
            reparse = self.make(root, *all_arguments, *overrides)
            self.assertEqual(reparse.returncode, 0, reparse.stdout)
            self.assertEqual(normal_object.stat().st_mtime_ns, before_reparse_normal)
            self.assertEqual(data_object.stat().st_mtime_ns, before_reparse_data)

            # Header touch rebuilds both the normal object and the data
            # object (the .pre.c intermediate still contains the original
            # #include "global.h", so GCC's own -MMD tracks it transitively).
            before_normal = normal_object.stat().st_mtime_ns
            before_data = data_object.stat().st_mtime_ns
            time.sleep(1.1)
            (root / "include" / "fixture.h").touch()
            rebuild = self.make(root, *all_arguments, *overrides)
            self.assertEqual(rebuild.returncode, 0, rebuild.stdout)
            self.assertGreater(normal_object.stat().st_mtime_ns, before_normal)
            self.assertGreater(data_object.stat().st_mtime_ns, before_data)

            # Touching the scanned INCBIN asset (not the .c source) rebuilds
            # the data object immediately, since the already-generated
            # .assets.d file already declares it as a real prerequisite of
            # .pre.c (no "next invocation" delay needed, unlike the old
            # append-after-compile mechanism this replaces).
            before_data_after_header = data_object.stat().st_mtime_ns
            time.sleep(1.1)
            (root / "assets" / "fixture_asset.bin").write_text(
                "v2", encoding="utf-8"
            )
            asset_rebuild = self.make(root, *all_arguments, *overrides)
            self.assertEqual(asset_rebuild.returncode, 0, asset_rebuild.stdout)
            self.assertGreater(
                data_object.stat().st_mtime_ns, before_data_after_header
            )
            self.assertFalse(list((root / "src").rglob("*.o")))
            self.assertFalse(list((root / "src").rglob("*.d")))
            self.assertFalse(list((root / "src").rglob("*.pre.c")))
            self.assertFalse(list((root / "src").rglob("*.assets.d")))
            self.assertFalse(list((root / "src").rglob("*.headers.d")))

    def test_cohort_target_never_scans_data_sources_for_assets(self):
        # expansion-modern-cohort must stay untouched by (and not pay the
        # cost of) the data-source asset dependency bootstrap, nor the
        # generated-header bootstrap for normal C sources: its source list
        # is fixed and disjoint from MODERN_ALL_DATA_C_SOURCES/
        # MODERN_ALL_C_SOURCES, and the goal filter guarding
        # `include $(MODERN_ALL_DATA_ASSET_DEPS)`/
        # `include $(MODERN_ALL_C_HEADER_DEPS)` must never fire for
        # expansion-modern-cohort/-toolchain-check/-clean.
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_all_fixture(Path(temporary))
            # Deliberately do not provide a working MODERN_SCANINC: if the
            # cohort goal ever tried to generate a .assets.d file it would
            # fail loudly (missing executable), giving a clear, fast
            # failure signal if isolation ever regresses.
            result = self.make(
                root,
                "expansion-modern-cohort",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "MODERN_COHORT_SOURCES=src/normal_fixture.c",
                "MODERN_COHORT_ASM_SOURCES=",
                "MODERN_SCANINC=tools/scaninc/does-not-exist",
                *overrides,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertFalse(
                list((root / "build").rglob("*.assets.d")),
                "expansion-modern-cohort must never generate .assets.d files",
            )
            self.assertFalse(
                list((root / "build").rglob("*.headers.d")),
                "expansion-modern-cohort must never generate .headers.d files",
            )

    def test_missing_generated_asset_is_built_before_preprocessing(self):
        # The core first-invocation bug this milestone fixes: a graphics/
        # binary asset that does not exist yet, but is derivable via an
        # existing top-level generation rule, must be built automatically
        # before preproc ever runs -- on the very first invocation, with no
        # prior build required.
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_all_fixture(Path(temporary))
            # Replace the pre-created asset with a "source" file plus a
            # top-level generation rule, standing in for real asset
            # pipelines like %.4bpp: %.png without needing gbagfx/PNG
            # decoding in this fixture.
            (root / "assets" / "fixture_asset.bin").unlink()
            (root / "assets" / "fixture_asset.src").write_text(
                "generated-from-source", encoding="utf-8"
            )
            makefile_path = root / "Makefile"
            makefile_path.write_text(
                makefile_path.read_text(encoding="utf-8")
                + "assets/%.bin: assets/%.src\n"
                + "\tcp $< $@\n",
                encoding="utf-8",
            )

            all_arguments = (
                "expansion-modern-all",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "MODERN_ALL_C_SOURCES=src/normal_fixture.c",
                "MODERN_ALL_DATA_C_SOURCES=src/data/data_fixture.c",
                "MODERN_ALL_ASM_SOURCES=asm/fixture.s",
                "MODERN_PREPROC=tools/preproc/preproc",
                "MODERN_SCANINC=tools/scaninc/scaninc",
            )
            result = self.make(root, *all_arguments, *overrides)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("Built 3 modern relocatable objects", result.stdout)

            # The missing asset must now exist, generated by the top-level
            # rule, and the data object must have been produced from it.
            self.assertTrue((root / "assets" / "fixture_asset.bin").is_file())
            self.assertEqual(
                (root / "assets" / "fixture_asset.bin").read_text(encoding="utf-8"),
                "generated-from-source",
            )
            data_object = (
                root
                / "build"
                / "expansion-modern"
                / "debug"
                / "aapcs"
                / "src"
                / "data"
                / "data_fixture.o"
            )
            self.assertTrue(data_object.is_file())

    def test_source_less_asset_fails_actionably(self):
        # A data source whose INCBIN references an asset with no matching
        # file and no rule to build one must fail with GNU Make's own
        # actionable "No rule to make target" error naming the missing
        # asset, not a confusing preproc "cannot open incbin file" error or
        # a silent/successful no-op.
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_all_fixture(Path(temporary))
            (root / "src" / "data" / "data_fixture.c").write_text(
                '#include "global.h"\n'
                'FIXTURE_INCBIN("assets/does_not_exist_anywhere.bin")\n',
                encoding="utf-8",
            )

            all_arguments = (
                "expansion-modern-all",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "MODERN_ALL_C_SOURCES=src/normal_fixture.c",
                "MODERN_ALL_DATA_C_SOURCES=src/data/data_fixture.c",
                "MODERN_ALL_ASM_SOURCES=asm/fixture.s",
                "MODERN_PREPROC=tools/preproc/preproc",
                "MODERN_SCANINC=tools/scaninc/scaninc",
            )
            result = self.make(root, *all_arguments, *overrides)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("No rule to make target", result.stdout)
            self.assertIn("does_not_exist_anywhere.bin", result.stdout)

    def test_missing_generated_header_is_built_before_normal_compile(self):
        # The chapterdata.c/chapter_settings.h class of bug: an ordinary
        # (non-data) MODERN_ALL_C_SOURCES member #includes a *generated*
        # header (not an INCBIN asset) that does not exist yet, but is
        # derivable via an existing top-level generation rule (mirroring
        # json_data_rules.mk's chapter_settings.h rule). scaninc cannot
        # discover this at all (it silently drops unresolvable #includes),
        # so this must go through GCC's own "-MM -MG" MODERN_ALL_C_HEADER_DEPS
        # bootstrap instead, and the header must be built automatically
        # before the normal compile -- on the very first invocation.
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_all_fixture(Path(temporary))
            (root / "src" / "generated_header_fixture.c").write_text(
                '#include "global.h"\n'
                '#include "src/generated_fixture.h"\n'
                "int modern_all_generated_header_fixture(void) "
                "{ return GENERATED_FIXTURE_VALUE; }\n",
                encoding="utf-8",
            )
            (root / "src" / "generated_fixture.src").write_text(
                "#define GENERATED_FIXTURE_VALUE 7\n", encoding="utf-8"
            )
            makefile_path = root / "Makefile"
            makefile_path.write_text(
                makefile_path.read_text(encoding="utf-8")
                + "src/%.h: src/%.src\n"
                + "\tcp $< $@\n",
                encoding="utf-8",
            )

            all_arguments = (
                "expansion-modern-all",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "MODERN_ALL_C_SOURCES=src/normal_fixture.c src/generated_header_fixture.c",
                "MODERN_ALL_DATA_C_SOURCES=src/data/data_fixture.c",
                "MODERN_ALL_ASM_SOURCES=asm/fixture.s",
                "MODERN_PREPROC=tools/preproc/preproc",
                "MODERN_SCANINC=tools/scaninc/scaninc",
            )
            result = self.make(root, *all_arguments, *overrides)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("Built 4 modern relocatable objects", result.stdout)

            # The missing header must now exist, generated by the top-level
            # rule, and the depending object must have been produced from
            # it -- no prior build required.
            self.assertTrue((root / "src" / "generated_fixture.h").is_file())
            generated_object = (
                root
                / "build"
                / "expansion-modern"
                / "debug"
                / "aapcs"
                / "src"
                / "generated_header_fixture.o"
            )
            self.assertTrue(generated_object.is_file())

    def test_source_less_header_fails_actionably(self):
        # A normal C source that #includes a header with no matching file
        # and no rule to build one must fail with GNU Make's own actionable
        # "No rule to make target" error naming the missing header, not a
        # confusing compiler "fatal error: ... No such file or directory"
        # or a silent/successful no-op.
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_all_fixture(Path(temporary))
            (root / "src" / "generated_header_fixture.c").write_text(
                '#include "global.h"\n'
                '#include "does_not_exist_anywhere.h"\n',
                encoding="utf-8",
            )

            all_arguments = (
                "expansion-modern-all",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "MODERN_ALL_C_SOURCES=src/normal_fixture.c src/generated_header_fixture.c",
                "MODERN_ALL_DATA_C_SOURCES=src/data/data_fixture.c",
                "MODERN_ALL_ASM_SOURCES=asm/fixture.s",
                "MODERN_PREPROC=tools/preproc/preproc",
                "MODERN_SCANINC=tools/scaninc/scaninc",
            )
            result = self.make(root, *all_arguments, *overrides)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("No rule to make target", result.stdout)
            self.assertIn("does_not_exist_anywhere.h", result.stdout)

    def test_missing_compiler_is_actionable_before_header_dep_generation(self):
        # GNU Make remakes stale/missing `include`d makefiles -- here, the
        # generated .headers.d files -- before expansion-modern-all's own
        # "expansion-modern-toolchain-check" prerequisite is ever evaluated
        # as part of running that goal's recipe. Without an order-only
        # dependency wiring toolchain-check into .headers.d generation, a
        # missing/misconfigured MODERN_CC would instead surface as a raw
        # "$(MODERN_CC): not found"-style shell error from the "-MM -MG"
        # pre-scan recipe. This must produce the existing, actionable
        # expansion-modern-toolchain-check diagnostic instead, on the very
        # first invocation, with no .headers.d files pre-existing.
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            root = self.make_all_fixture(temporary_path)

            all_arguments = (
                "expansion-modern-all",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "MODERN_ALL_C_SOURCES=src/normal_fixture.c",
                "MODERN_ALL_DATA_C_SOURCES=src/data/data_fixture.c",
                "MODERN_ALL_ASM_SOURCES=asm/fixture.s",
                "MODERN_PREPROC=tools/preproc/preproc",
                "MODERN_SCANINC=tools/scaninc/scaninc",
                f"MODERN_CC={temporary_path / 'missing-gcc'}",
            )
            result = self.make(root, *all_arguments)

            header_dependency_path = (
                root
                / "build"
                / "expansion-modern"
                / "debug"
                / "aapcs"
                / "src"
                / "normal_fixture.headers.d"
            )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("modern compiler not found", result.stdout)
        self.assertIn("MODERN_TOOLCHAIN_ROOT/MODERN_CC", result.stdout)
        # The canonical diagnostic must be what is reported, not the raw
        # shell/pre-scan failure this bug used to surface instead.
        self.assertNotIn("failed to pre-scan", result.stdout)
        self.assertNotIn("/bin/sh:", result.stdout)
        self.assertFalse(header_dependency_path.is_file())

    def test_default_scaninc_builds_via_explicit_host_rule(self):
        # A missing default tools/scaninc/scaninc$(EXE) must be built by
        # modern.mk's own explicit host rule ("$(MAKE) -C tools/scaninc",
        # using that Makefile's real host C++ flags and all four real
        # source files), not silently reached through GNU Make's built-in
        # implicit "%: %.o"/"%.o: %.cpp" rule chain -- which would compile
        # only scaninc.cpp (missing c_file.cpp/asm_file.cpp/source_file.cpp)
        # with default host flags plus any legacy CPPFLAGS already exported
        # for the agbcc preprocessing pipeline (e.g. -nostdinc), producing
        # either a broken link or a "cstdio: No such file or directory"
        # failure instead of a correct scaninc binary.
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")
        if shutil.which("g++") is None:
            self.skipTest("a host g++ is not available to build scaninc")

        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_all_fixture(Path(temporary))

            # Swap the fixture's fake python-stub scaninc for the real
            # tool sources (no prebuilt binary), so expansion-modern-all
            # must build the real scaninc via modern.mk's explicit rule.
            # The fixture's data source keeps using its own fake
            # FIXTURE_INCBIN grammar (matched by the fixture's fake
            # preproc, not the real scaninc's INCBIN_U8/etc. identifiers):
            # this test cares about scaninc being *built correctly and run
            # successfully*, not about it recognizing this fixture's
            # placeholder macro.
            scaninc_dir = root / "tools" / "scaninc"
            shutil.rmtree(scaninc_dir)
            shutil.copytree(
                ROOT / "tools" / "scaninc",
                scaninc_dir,
                ignore=shutil.ignore_patterns("scaninc", "scaninc.exe"),
            )
            self.assertFalse((scaninc_dir / "scaninc").exists())

            all_arguments = (
                "expansion-modern-all",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "MODERN_ALL_C_SOURCES=src/normal_fixture.c",
                "MODERN_ALL_DATA_C_SOURCES=src/data/data_fixture.c",
                "MODERN_ALL_ASM_SOURCES=asm/fixture.s",
                "MODERN_PREPROC=tools/preproc/preproc",
                # MODERN_SCANINC deliberately left at its default so the
                # target name matches modern.mk's explicit build rule.
            )
            result = self.make(root, *all_arguments, *overrides)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("make -C tools/scaninc", result.stdout)
            self.assertNotIn("cstdio", result.stdout)
            self.assertNotIn("nostdinc", result.stdout)

            built_scaninc = scaninc_dir / "scaninc"
            self.assertTrue(built_scaninc.is_file())
            self.assertTrue(os.access(built_scaninc, os.X_OK))

            # A valid .assets.d proves the freshly built scaninc actually
            # ran successfully (not just linked), and the full cohort
            # still reaches a successful build.
            asset_dependency_path = (
                root
                / "build"
                / "expansion-modern"
                / "debug"
                / "aapcs"
                / "src"
                / "data"
                / "data_fixture.assets.d"
            )
            self.assertTrue(asset_dependency_path.is_file())
            self.assertIn("Built 3 modern relocatable objects", result.stdout)

    def test_spaced_toolchain_binutils_newlib_and_direct_cc_paths(self):
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        values = dict(item.split("=", 1) for item in overrides)
        cc = Path(values["MODERN_CC"])
        if not cc.is_absolute():
            resolved_cc = shutil.which(str(cc))
            self.assertIsNotNone(resolved_cc)
            cc = Path(resolved_cc)

        newlib_value = values.get("MODERN_NEWLIB_INCLUDE")
        if not newlib_value and Path("/usr/include/newlib/stdlib.h").is_file():
            newlib_value = "/usr/include/newlib"
        if not newlib_value or not Path(newlib_value).is_dir():
            self.skipTest("a source newlib include directory is not available")

        assembler_command = [str(cc)]
        binutils_value = values.get("MODERN_BINUTILS_DIR")
        if binutils_value:
            assembler_command.append(f"-B{binutils_value}/")
        assembler_command.append("-print-prog-name=as")
        assembler_name = subprocess.check_output(
            assembler_command, text=True
        ).strip()
        assembler = Path(assembler_name)
        if not assembler.is_absolute():
            resolved_assembler = shutil.which(assembler_name)
            self.assertIsNotNone(resolved_assembler)
            assembler = Path(resolved_assembler)

        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            root = self.make_fixture(temporary_path)
            toolchain_root = temporary_path / "toolchain root with spaces"
            toolchain_bin = toolchain_root / "bin"
            binutils = temporary_path / "binutils directory with spaces"
            newlib = temporary_path / "newlib headers with spaces"
            toolchain_bin.mkdir(parents=True)
            binutils.mkdir()

            prefix = os.environ.get("PREFIX", "arm-none-eabi-")
            spaced_cc = toolchain_bin / f"{prefix}gcc"
            spaced_objdump = toolchain_bin / f"{prefix}objdump"
            os.symlink(cc.resolve(), spaced_cc)
            os.symlink(Path(values["MODERN_OBJDUMP"]).resolve(), spaced_objdump)
            os.symlink(assembler.resolve(), binutils / "as")
            os.symlink(Path(newlib_value).resolve(), newlib, target_is_directory=True)

            root_check = self.make(
                root,
                "expansion-modern-toolchain-check",
                f"MODERN_TOOLCHAIN_ROOT={toolchain_root}",
                f"MODERN_BINUTILS_DIR={binutils}",
                f"MODERN_NEWLIB_INCLUDE={newlib}",
            )
            self.assertEqual(root_check.returncode, 0, root_check.stdout)

            cohort = self.make(
                root,
                "expansion-modern-cohort",
                f"MODERN_CC={spaced_cc}",
                f"MODERN_OBJDUMP={spaced_objdump}",
                f"MODERN_BINUTILS_DIR={binutils}",
                f"MODERN_NEWLIB_INCLUDE={newlib}",
            )
            self.assertEqual(cohort.returncode, 0, cohort.stdout)
            output = root / "build" / "expansion-modern" / "debug" / "aapcs"
            objects = sorted(output.glob("src/*.o"))
            self.assertEqual(len(objects), 8)
            self.assertEqual(len(list(output.glob("src/*.d"))), 8)
            architecture = subprocess.run(
                [str(spaced_objdump), "-f", *map(str, objects)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            self.assertEqual(architecture.returncode, 0, architecture.stdout)
            self.assertEqual(architecture.stdout.count("file format elf32-littlearm"), 8)
            self.assertEqual(architecture.stdout.count("architecture: armv4t"), 8)

    def test_iwram_objects_have_per_symbol_bss_sections(self):
        """Three IWRAM owner objects must have per-symbol BSS sections."""
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary) / "iwram-objs"
            for source in (
                "src/agb_sram.c", "src/m4a.c", "src/bmshop.c",
            ):
                result = self.make(
                    ROOT,
                    f"MODERN_BUILD_ROOT={output_root}",
                    f"MODERN_CONFIG=debug",
                    f"MODERN_ABI=aapcs",
                    f"MODERN_COHORT_SOURCES={source}",
                    "MODERN_COHORT_ASM_SOURCES=",
                    "expansion-modern-cohort",
                    *overrides,
                )
                self.assertEqual(
                    result.returncode, 0,
                    f"{source} build failed:\n{result.stdout[-300:]}",
                )

            objdump = next(
                v.split("=", 1)[1]
                for v in overrides
                if v.startswith("MODERN_OBJDUMP=")
            )
            obj_dir = output_root / "debug" / "aapcs"

            # agb_sram.o
            agb = subprocess.run(
                [objdump, "-h", str(obj_dir / "src" / "agb_sram.o")],
                capture_output=True, text=True,
            )
            for sect in (
                ".bss.ReadSramFast",
                ".bss.VerifySramFast",
                ".bss.readSramFast_Work",
                ".bss.verifySramFast_Work",
            ):
                self.assertIn(
                    sect, agb.stdout,
                    f"agb_sram.o missing section {sect}",
                )

            # m4a.o
            m4a = subprocess.run(
                [objdump, "-h", str(obj_dir / "src" / "m4a.o")],
                capture_output=True, text=True,
            )
            for sect in (
                ".bss.gSoundInfo",
                ".bss.gMPlayJumpTable",
                ".bss.gCgbChans",
                ".bss.gMPlayMemAccArea",
                ".bss.code",
            ):
                self.assertIn(
                    sect, m4a.stdout,
                    f"m4a.o missing section {sect}",
                )

            # bmshop.o
            bms = subprocess.run(
                [objdump, "-h", str(obj_dir / "src" / "bmshop.o")],
                capture_output=True, text=True,
            )
            self.assertIn(
                ".bss.gText_GoldBox", bms.stdout,
                "bmshop.o missing section .bss.gText_GoldBox",
            )

    def test_fetsa_sibling_ordering_prevents_duplicate_generation(self):
        # Old-style FETSATOOL rules (graphics_file_rules.mk) produce a
        # "<name>.feimgN.bin"/"<name>.fetsaN.bin" pair from ONE recipe
        # invocation, using a multi-target explicit (non-grouped "&:")
        # rule. If two different data sources each independently list only
        # one sibling of the pair as a prerequisite (one the feimg, another
        # the fetsa or its .lz), GNU Make can decide both need building and
        # run the shared recipe twice -- and a plain "fetsa: feimg" ordering
        # edge alone does not stop this once a parallel (-j>1) build is in
        # play (confirmed against an isolated GNU Make 4.3 reproduction),
        # since Make can dispatch the fetsa target's own copy of the shared
        # recipe as soon as the feimg build job is merely already scheduled,
        # not once it has actually finished writing output. This fixture
        # reproduces exactly that shape with a synthetic FETSATOOL standing
        # in for the real scripts/gfxtools/tsa_generator.py, routed through
        # the modern.mk-overridden $(FETSATOOL) so it exercises the real
        # lock-guarded wrapper (not just the ordering edge), and exercises
        # both a raw-fetsa reference and a compressed-fetsa (.lz) reference
        # sourced from a THIRD, independent data file.
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("arm-none-eabi GCC and objdump are not available")

        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_all_fixture(Path(temporary))

            (root / "assets" / "fixture_pair_source.src").write_text(
                "shared-pair-payload", encoding="utf-8"
            )

            # Fixture stand-in for scripts/gfxtools/tsa_generator.py: logs
            # one "run" line per actual invocation and copies the source
            # payload into both sibling outputs, exactly like the real tool.
            fake_fetsatool = root / "scripts" / "gfxtools" / "fetsatool_fixture.py"
            fake_fetsatool.parent.mkdir(parents=True)
            fake_fetsatool.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "src, out1, out2 = sys.argv[1:4]\n"
                # Make always runs this recipe with cwd set to the
                # Makefile's own directory, so a plain relative path
                # matches the run-log path used elsewhere in the fixture.
                "with open('assets/fetsa_generator_runs.log', 'a', encoding='utf-8') as handle:\n"
                "    handle.write('run\\n')\n"
                "payload = pathlib.Path(src).read_bytes()\n"
                "pathlib.Path(out1).write_bytes(payload)\n"
                "pathlib.Path(out2).write_bytes(payload)\n",
                encoding="utf-8",
            )
            fake_fetsatool.chmod(0o755)

            makefile_path = root / "Makefile"
            makefile_path.write_text(
                # FETSATOOL is defined before "include modern.mk", matching
                # the real top-level Makefile, so modern.mk's lock-guarded
                # override (only active for modern goals) takes effect.
                "FETSATOOL := scripts/gfxtools/fetsatool_fixture.py\n"
                + makefile_path.read_text(encoding="utf-8")
                # Old-style two-output rule standing in for a real
                # FETSATOOL invocation: one recipe, two sibling targets.
                + "assets/fixture.feimg2.bin assets/fixture.fetsa2.bin: "
                + "assets/fixture_pair_source.src\n"
                + "\t$(FETSATOOL) $< assets/fixture.feimg2.bin assets/fixture.fetsa2.bin\n"
                # Minimal stand-in for the top-level Makefile's real
                # "%.lz: %" LZ-compression rule.
                + "assets/%.bin.lz: assets/%.bin\n"
                + "\techo run >> assets/lz_generator_runs.log\n"
                + "\tcp $< $@\n",
                encoding="utf-8",
            )

            # Three independent data sources, each referencing only one
            # sibling of the pair (or its compressed variant), so each
            # contributes an independent prerequisite edge to the shared
            # recipe's two targets -- the exact shape that used to trigger
            # a duplicate invocation.
            (root / "src" / "data" / "data_fixture.c").write_text(
                '#include "global.h"\n'
                'FIXTURE_INCBIN("assets/fixture.feimg2.bin")\n',
                encoding="utf-8",
            )
            (root / "src" / "data" / "data_fixture2.c").write_text(
                '#include "global.h"\n'
                'FIXTURE_INCBIN("assets/fixture.fetsa2.bin")\n',
                encoding="utf-8",
            )
            (root / "src" / "data" / "data_fixture3.c").write_text(
                '#include "global.h"\n'
                'FIXTURE_INCBIN("assets/fixture.fetsa2.bin.lz")\n',
                encoding="utf-8",
            )

            all_arguments = (
                "expansion-modern-all",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "MODERN_ALL_C_SOURCES=src/normal_fixture.c",
                "MODERN_ALL_DATA_C_SOURCES="
                "src/data/data_fixture.c src/data/data_fixture2.c src/data/data_fixture3.c",
                "MODERN_ALL_ASM_SOURCES=asm/fixture.s",
                "MODERN_PREPROC=tools/preproc/preproc",
                "MODERN_SCANINC=tools/scaninc/scaninc",
                "-j4",
            )

            result = self.make(root, *all_arguments, *overrides)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("Built 5 modern relocatable objects", result.stdout)

            feimg_path = root / "assets" / "fixture.feimg2.bin"
            fetsa_path = root / "assets" / "fixture.fetsa2.bin"
            fetsa_lz_path = root / "assets" / "fixture.fetsa2.bin.lz"
            generator_log = root / "assets" / "fetsa_generator_runs.log"
            lz_log = root / "assets" / "lz_generator_runs.log"

            self.assertTrue(feimg_path.is_file())
            self.assertTrue(fetsa_path.is_file())
            self.assertTrue(fetsa_lz_path.is_file())
            self.assertEqual(feimg_path.read_text(encoding="utf-8"), "shared-pair-payload")
            self.assertEqual(fetsa_path.read_text(encoding="utf-8"), "shared-pair-payload")
            self.assertEqual(fetsa_lz_path.read_text(encoding="utf-8"), "shared-pair-payload")

            # The shared two-output recipe must have run exactly once,
            # not twice, despite being reachable from three independent
            # data sources' prerequisite lists.
            self.assertEqual(
                generator_log.read_text(encoding="utf-8").count("run"), 1,
                "the shared feimg/fetsa recipe ran more than once",
            )
            self.assertEqual(
                lz_log.read_text(encoding="utf-8").count("run"), 1,
                "the fetsa .lz compression recipe ran more than once",
            )

            # An immediate, identical second invocation is a true no-op:
            # no data object recompiles and neither generator re-runs.
            second_result = self.make(root, *all_arguments, *overrides)
            self.assertEqual(second_result.returncode, 0, second_result.stdout)
            self.assertIn("Built 5 modern relocatable objects", second_result.stdout)
            self.assertNotIn("-MMD -MP -MF", second_result.stdout)
            self.assertNotIn("fixture_pair_source.src", second_result.stdout)
            self.assertEqual(
                generator_log.read_text(encoding="utf-8").count("run"), 1,
                "the shared feimg/fetsa recipe re-ran on a true no-op second invocation",
            )
            self.assertEqual(
                lz_log.read_text(encoding="utf-8").count("run"), 1,
                "the fetsa .lz compression recipe re-ran on a true no-op second invocation",
            )


if __name__ == "__main__":
    unittest.main()
