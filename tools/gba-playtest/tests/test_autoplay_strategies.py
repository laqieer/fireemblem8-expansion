"""Issue #90 typed autoplay strategy registry and profile contract."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CC = shutil.which("gcc") or shutil.which("cc")
ARM_CC = shutil.which("arm-none-eabi-gcc")
ARM_NM = shutil.which("arm-none-eabi-nm")
ARM_SIZE = shutil.which("arm-none-eabi-size")
STRATEGY_SOURCE = ROOT / "src" / "expansion_autoplay_strategies.c"
OBJECTIVE_SOURCE = ROOT / "src" / "expansion_chapter_objectives.c"
DRIVER = Path(__file__).resolve().parent / "c" / "expansion_autoplay_strategies_driver.c"
STRATEGY_FIXTURE = (
    ROOT / "scripts" / "generated_data" / "tests" / "fixtures" / "autoplaystrategies" / "valid.json"
)
OBJECTIVE_FIXTURE = (
    ROOT / "scripts" / "generated_data" / "tests" / "fixtures" / "chapterobjectives" / "strategy_valid.json"
)
CHAPTER_BUNDLE_FIXTURE = (
    ROOT / "scripts" / "generated_data" / "tests" / "fixtures" / "chapterobjectives" / "strategy_bundle.json"
)


def run(command):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def generate(temporary_path, enabled):
    generated_dir = temporary_path / "generated"
    inventory = temporary_path / "inventory.md"
    objective_source = OBJECTIVE_FIXTURE if enabled else ROOT / "src" / "data" / "chapter_objectives.json"
    strategy_source = STRATEGY_FIXTURE if enabled else ROOT / "src" / "data" / "autoplay_strategies.json"
    objective = generated_dir / "data_chapter_objectives.c"
    strategy = generated_dir / "data_autoplay_strategies.c"

    bundle_args = ["--dep-source", "chapterbundle={}".format(CHAPTER_BUNDLE_FIXTURE)] if enabled else []
    for table, source, extra_args in (
        ("chapterobjectives", objective_source, bundle_args),
        (
            "autoplaystrategies",
            strategy_source,
            [
                "--dep-source",
                "chapterobjectives={}".format(objective_source),
                *bundle_args,
            ],
        ),
    ):
        completed = run(
            [
                "python3",
                "-m",
                "scripts.generated_data",
                "generate",
                "--table",
                table,
                "--source",
                str(source),
                "--out-dir",
                str(generated_dir),
                "--inventory",
                str(inventory),
                "--no-roundtrip",
                "--reference-profiles",
                str(int(enabled)),
                *extra_args,
            ]
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stdout + completed.stderr)

    return objective, strategy


class AutoplayStrategiesRuntimeTests(unittest.TestCase):
    def test_profiles_dispatch_deterministically_and_disabled_preserves_fallback(self):
        if CC is None:
            self.skipTest("no host C compiler")

        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            temporary_path = Path(temporary)
            for enabled in (False, True):
                with self.subTest(enabled=enabled):
                    objective, strategy = generate(temporary_path / str(enabled), enabled)
                    executable = temporary_path / ("strategies-enabled" if enabled else "strategies-disabled")
                    compiled = run(
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
                            "-DFE8_EXPANSION_AUTOPLAY_STRATEGIES={}".format(int(enabled)),
                            str(STRATEGY_SOURCE),
                            str(OBJECTIVE_SOURCE),
                            str(objective),
                            str(strategy),
                            str(DRIVER),
                            "-o",
                            str(executable),
                        ]
                    )
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                    ran = run([str(executable)])
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                    self.assertIn("AUTOPLAY_STRATEGIES_HOST_TEST: PASS", ran.stdout)

    def test_arm_profiles_are_ewram_free_and_gate_reference_callbacks(self):
        if ARM_CC is None or ARM_NM is None or ARM_SIZE is None:
            self.skipTest("arm-none-eabi compiler/binutils unavailable")

        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            temporary_path = Path(temporary)
            for enabled in (False, True):
                with self.subTest(enabled=enabled):
                    objective, strategy = generate(temporary_path / str(enabled), enabled)
                    objects = []
                    for source in (STRATEGY_SOURCE, OBJECTIVE_SOURCE, objective, strategy):
                        output = temporary_path / (
                            "{}-{}-{}.o".format(source.stem, "enabled" if enabled else "disabled", len(objects))
                        )
                        completed = run(
                            [
                                ARM_CC,
                                "-mcpu=arm7tdmi",
                                "-mthumb",
                                "-mthumb-interwork",
                                "-mabi=aapcs",
                                "-std=gnu89",
                                "-ffreestanding",
                                "-fno-builtin",
                                "-O2",
                                "-Werror=declaration-after-statement",
                                "-Werror=implicit-function-declaration",
                                "-Werror=implicit-int",
                                "-I",
                                str(ROOT / "include"),
                                "-I",
                                str(ROOT / "include" / "generated"),
                                "-DFE8_EXPANSION_MODERN_BUILD=1",
                                "-DFE8_EXPANSION_AUTOPLAY_STRATEGIES={}".format(int(enabled)),
                                "-c",
                                str(source),
                                "-o",
                                str(output),
                            ]
                        )
                        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                        objects.append(output)

                    symbols = run([ARM_NM, *map(str, objects)])
                    self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
                    if enabled:
                        self.assertIn("ExpansionAutoplayStrategy_Aggressive", symbols.stdout)
                        self.assertIn("ExpansionAutoplayStrategy_ObjectiveFirst", symbols.stdout)
                    else:
                        self.assertNotIn("ExpansionAutoplayStrategy_Aggressive", symbols.stdout)
                        self.assertNotIn("ExpansionAutoplayStrategy_ObjectiveFirst", symbols.stdout)

                    sizes = run([ARM_SIZE, "-A", *map(str, objects)])
                    self.assertEqual(sizes.returncode, 0, sizes.stdout + sizes.stderr)
                    ewram_bytes = sum(
                        int(value)
                        for value in re.findall(
                            r"^ewram_data\s+(\d+)\s+", sizes.stdout, re.MULTILINE
                        )
                    )
                    text_bytes = sum(
                        int(value)
                        for value in re.findall(r"^\.text\s+(\d+)\s+", sizes.stdout, re.MULTILINE)
                    )
                    strategy_sizes = run([ARM_SIZE, "-A", str(objects[0]), str(objects[3])])
                    self.assertEqual(
                        strategy_sizes.returncode,
                        0,
                        strategy_sizes.stdout + strategy_sizes.stderr,
                    )
                    strategy_ewram_bytes = sum(
                        int(value)
                        for value in re.findall(
                            r"^ewram_data\s+(\d+)\s+", strategy_sizes.stdout, re.MULTILINE
                        )
                    )
                    self.assertEqual(strategy_ewram_bytes, 0)
                    self.assertEqual(ewram_bytes, 20)
                    self.assertLessEqual(text_bytes, 4096)


if __name__ == "__main__":
    unittest.main()
