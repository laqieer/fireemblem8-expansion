"""Issue #89 generated objective runtime contract."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "gba-playtest"))
CC = shutil.which("gcc") or shutil.which("cc")
ARM_CC = shutil.which("arm-none-eabi-gcc")
ARM_NM = shutil.which("arm-none-eabi-nm")
ARM_OBJDUMP = shutil.which("arm-none-eabi-objdump")
SOURCE = ROOT / "src" / "expansion_chapter_objectives.c"
DRIVER = Path(__file__).resolve().parent / "c" / "expansion_chapter_objectives_driver.c"
FIXTURE = ROOT / "scripts" / "generated_data" / "tests" / "fixtures" / "chapterobjectives" / "valid.json"

import gba_playtest  # noqa: E402
import run_chapter_objective_checks as runtime_check  # noqa: E402


class ChapterObjectivesRuntimeTests(unittest.TestCase):
    def test_default_runtime_negative_uses_bounded_objective_probes(self):
        scenario = gba_playtest.parse_scenario_data(runtime_check.scenario_data())
        self.assertEqual(scenario.schema_version, 2)
        self.assertEqual(scenario.run_until.max_frames, 3951)
        objective_bindings = [
            probe.binding
            for probe in scenario.checkpoints[-1].probes
            if probe.binding.startswith("gExpansionChapterObjectiveTelemetry")
        ]
        self.assertEqual(
            objective_bindings,
            [
                "gExpansionChapterObjectiveTelemetry",
                "gExpansionChapterObjectiveTelemetry+0x04",
                "gExpansionChapterObjectiveTelemetry+0x08",
                "gExpansionChapterObjectiveTelemetry+0x0c",
            ],
        )

    def test_generated_fixture_evaluates_all_kinds_and_reconstructs_after_reset(self):
        if CC is None:
            self.skipTest("no host C compiler")

        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            temporary_path = Path(temporary)
            generated_dir = temporary_path / "generated"
            inventory = temporary_path / "inventory.md"
            generated = generated_dir / "data_chapter_objectives.c"
            generate = subprocess.run(
                [
                    "python3",
                    "-m",
                    "scripts.generated_data",
                    "generate",
                    "--table",
                    "chapterobjectives",
                    "--source",
                    str(FIXTURE),
                    "--out-dir",
                    str(generated_dir),
                    "--inventory",
                    str(inventory),
                    "--no-roundtrip",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(generate.returncode, 0, generate.stdout + generate.stderr)
            self.assertTrue(generated.is_file())

            executable = temporary_path / "chapter-objectives-host"
            compiled = subprocess.run(
                [
                    CC,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-O2",
                    "-I",
                    str(ROOT / "include"),
                    "-I",
                    str(ROOT / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    str(SOURCE),
                    str(generated),
                    str(DRIVER),
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            ran = subprocess.run([str(executable)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("CHAPTER_OBJECTIVES_HOST_TEST: PASS", ran.stdout)

    def test_arm_aapcs_default_table_and_telemetry_budgets(self):
        if ARM_CC is None or ARM_NM is None or ARM_OBJDUMP is None:
            self.skipTest("arm-none-eabi compiler/binutils unavailable")

        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            temporary_path = Path(temporary)
            generated_dir = temporary_path / "generated"
            inventory = temporary_path / "inventory.md"
            generated = generated_dir / "data_chapter_objectives.c"
            generated_object = temporary_path / "data_chapter_objectives.o"
            runtime_object = temporary_path / "expansion_chapter_objectives.o"
            generate = subprocess.run(
                [
                    "python3",
                    "-m",
                    "scripts.generated_data",
                    "generate",
                    "--table",
                    "chapterobjectives",
                    "--out-dir",
                    str(generated_dir),
                    "--inventory",
                    str(inventory),
                    "--no-roundtrip",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(generate.returncode, 0, generate.stdout + generate.stderr)

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
                "-I",
                str(ROOT / "include"),
                "-I",
                str(ROOT / "include" / "generated"),
                "-DFE8_EXPANSION_MODERN_BUILD=1",
                "-c",
            ]
            for source, output in ((generated, generated_object), (SOURCE, runtime_object)):
                compiled = subprocess.run(
                    [*common, str(source), "-o", str(output)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)

            symbols = subprocess.run(
                [ARM_NM, "-S", str(generated_object), str(runtime_object)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
            sizes = {}
            for line in symbols.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 4:
                    sizes[fields[-1]] = int(fields[1], 16)
            self.assertEqual(sizes["gExpansionChapterObjectiveBundles"], 12)
            self.assertEqual(sizes["gExpansionChapterObjectiveTelemetry"], 16)

            table = subprocess.run(
                [ARM_OBJDUMP, "-t", str(runtime_object)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(table.returncode, 0, table.stdout + table.stderr)
            telemetry_symbols = [
                line
                for line in table.stdout.splitlines()
                if line.endswith(" gExpansionChapterObjectiveTelemetry")
            ]
            self.assertEqual(len(telemetry_symbols), 1)
            self.assertIn("ewram_data", telemetry_symbols[0].split())


if __name__ == "__main__":
    unittest.main()
