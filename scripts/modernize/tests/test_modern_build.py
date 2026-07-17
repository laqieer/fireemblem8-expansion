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
                        *overrides,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        f"{config}/{abi} layout probe failed:\n{result.stdout}",
                    )

            self.assertEqual(len(list(output_root.rglob("*.o"))), 4)
            self.assertEqual(len(list(output_root.rglob("*.d"))), 4)

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
