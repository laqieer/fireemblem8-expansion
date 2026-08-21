"""Host behavior and ARM object-boundary tests for issue #68's log transport."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INCLUDES = ["-I", str(ROOT / "include"), "-I", str(ROOT / "include" / "generated")]
SOURCE = ROOT / "src" / "expansion_log.c"
DRIVER = Path(__file__).resolve().parent / "c" / "expansion_log_driver.c"
DISABLED_DRIVER = Path(__file__).resolve().parent / "c" / "expansion_log_disabled_driver.c"
CC = shutil.which("gcc") or shutil.which("cc")
ARM_CC = shutil.which("arm-none-eabi-gcc")
ARM_NM = shutil.which("arm-none-eabi-nm")
ARM_OBJDUMP = shutil.which("arm-none-eabi-objdump")


def run(command):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def temporary_directory():
    root = ROOT / "build"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


class ExpansionLogHostTests(unittest.TestCase):
    def setUp(self):
        if CC is None:
            self.skipTest("no host C compiler")

    def test_enabled_handshake_payload_and_failure_contracts(self):
        with temporary_directory() as temporary:
            work = Path(temporary)
            objects = []
            for source in (SOURCE, DRIVER):
                obj = work / f"{source.stem}.o"
                completed = run(
                    [
                        CC,
                        "-std=gnu89",
                        "-Werror=declaration-after-statement",
                        "-Werror=implicit-function-declaration",
                        "-Werror=implicit-int",
                        *INCLUDES,
                        "-DFE8_EXPANSION_MODERN_BUILD=1",
                        "-DFE8_EXPANSION_LOGGING_ENABLED=1",
                        "-DEXPANSION_LOG_TEST_BACKEND=1",
                        "-c",
                        str(source),
                        "-o",
                        str(obj),
                    ]
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                objects.append(obj)
            executable = work / "expansion-log-enabled"
            completed = run([CC, *(str(obj) for obj in objects), "-o", str(executable)])
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            completed = run([str(executable)])
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("EXPANSION_LOG_HOST_TEST: PASS", completed.stdout)

    def test_disabled_macros_are_inert_without_a_backend_object(self):
        with temporary_directory() as temporary:
            work = Path(temporary)
            executable = work / "expansion-log-disabled"
            completed = run(
                [
                    CC,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    *INCLUDES,
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DFE8_EXPANSION_LOGGING_ENABLED=0",
                    str(DISABLED_DRIVER),
                    "-o",
                    str(executable),
                ]
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            completed = run([str(executable)])
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("EXPANSION_LOG_DISABLED_HOST_TEST: PASS", completed.stdout)


class ExpansionLogArmTests(unittest.TestCase):
    def test_aapcs_enabled_and_disabled_symbol_boundaries(self):
        if ARM_CC is None or ARM_NM is None or ARM_OBJDUMP is None:
            self.skipTest("arm-none-eabi compiler/binutils unavailable")
        with temporary_directory() as temporary:
            work = Path(temporary)
            enabled = work / "enabled.o"
            disabled = work / "disabled.o"
            common = [
                ARM_CC,
                "-mcpu=arm7tdmi",
                "-mthumb",
                "-mthumb-interwork",
                "-mabi=aapcs",
                "-std=gnu89",
                "-ffreestanding",
                "-fno-builtin",
                "-Werror=declaration-after-statement",
                "-Werror=implicit-function-declaration",
                "-Werror=implicit-int",
                *INCLUDES,
                "-DFE8_EXPANSION_MODERN_BUILD=1",
            ]
            completed = run(
                [*common, "-DFE8_EXPANSION_LOGGING_ENABLED=1", "-c", str(SOURCE), "-o", str(enabled)]
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            completed = run(
                [*common, "-DFE8_EXPANSION_LOGGING_ENABLED=0", "-c", str(SOURCE), "-o", str(disabled)]
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("ExpansionLog_Write", run([ARM_NM, str(enabled)]).stdout)
            self.assertNotIn("ExpansionLog_", run([ARM_NM, str(disabled)]).stdout)
            disassembly = run([ARM_OBJDUMP, "-d", str(enabled)]).stdout.lower()
            self.assertIn("strh", disassembly)
            self.assertIn("ldrh", disassembly)
            self.assertIn("strb", disassembly)
            self.assertIn("04fff780", disassembly)
            self.assertIn("04fff600", disassembly)
            self.assertIn("04fff700", disassembly)

    def test_archival_lane_cfile_list_excludes_logging_backend(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("$(filter-out src/expansion_log.c,$(CFILES))", makefile)

    def test_shared_public_sources_keep_c89_comment_style(self):
        for path in (SOURCE, ROOT / "include" / "expansion_log.h"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("//", text, f"{path.name} must remain C89-comment-safe")


if __name__ == "__main__":
    unittest.main()
