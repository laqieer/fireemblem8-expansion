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
        root = destination / "repo"
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


if __name__ == "__main__":
    unittest.main()
