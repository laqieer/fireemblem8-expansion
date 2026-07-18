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
            dependencies = sorted(output.rglob("*.d"))
            self.assertEqual(len(objects), 3)
            self.assertEqual(len(dependencies), 3)
            self.assertTrue(data_pre.is_file())
            self.assertTrue(normal_object.is_file())
            self.assertTrue(data_object.is_file())
            self.assertTrue(asm_object.is_file())

            # No intermediate/output ever lands in the source tree.
            self.assertFalse(list((root / "src").rglob("*.o")))
            self.assertFalse(list((root / "src").rglob("*.d")))
            self.assertFalse(list((root / "src").rglob("*.pre.c")))
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

            # The data object's single .d file must carry both GCC's own
            # object/header rule and the appended scaninc-derived
            # pre.c-depends-on-source-and-asset rule. The fixture's data
            # source intentionally embeds two INCBIN assets: real scaninc
            # prints one path per line, so this proves the appended rule
            # collapses multi-line scaninc output into one valid Makefile
            # line rather than corrupting the .d file with a bare
            # continuation-less newline (which previously broke "make" with
            # "missing separator" on any subsequent goal, including the fast
            # cohort, since both .d sets are -included together).
            data_dependency_path = output / "src" / "data" / "data_fixture.d"
            data_dependency_text = data_dependency_path.read_text(encoding="utf-8")
            self.assertIn("include/fixture.h", data_dependency_text)
            self.assertIn("assets/fixture_asset.bin", data_dependency_text)
            self.assertIn("assets/fixture_asset_2.bin", data_dependency_text)
            self.assertIn("src/data/data_fixture.c", data_dependency_text)

            appended_rule_lines = [
                line
                for line in data_dependency_text.splitlines()
                if line.startswith("build/expansion-modern/debug/aapcs/src/data/data_fixture.pre.c:")
            ]
            self.assertEqual(len(appended_rule_lines), 1)
            self.assertIn("assets/fixture_asset.bin", appended_rule_lines[0])
            self.assertIn("assets/fixture_asset_2.bin", appended_rule_lines[0])

            # A corrupted (multi-line, non-continued) appended rule breaks
            # make's parser outright as soon as the .d is -included; re-
            # invoking make with the same arguments must keep succeeding now
            # that the asset list is collapsed onto one line.
            reparse = self.make(root, *all_arguments, *overrides)
            self.assertEqual(reparse.returncode, 0, reparse.stdout)

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
            # the data object starting with the next make invocation, per
            # the appended dependency rule.
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


if __name__ == "__main__":
    unittest.main()
