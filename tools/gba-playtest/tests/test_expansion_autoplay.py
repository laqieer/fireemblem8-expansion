"""Issue #85 host and ARM checks for transient blue computer control."""

import json
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INCLUDES = ["-I", str(ROOT / "include"), "-I", str(ROOT / "include" / "generated")]
SOURCE = ROOT / "src" / "expansion_autoplay.c"
BMPHASE_SOURCE = ROOT / "src" / "bmphase.c"
DRIVER = Path(__file__).resolve().parent / "c" / "expansion_autoplay_driver.c"
CC = shutil.which("gcc") or shutil.which("cc")
ARM_CC = shutil.which("arm-none-eabi-gcc")
ARM_NM = shutil.which("arm-none-eabi-nm")
ARM_SIZE = shutil.which("arm-none-eabi-size")
RUNNER = ROOT / "tools" / "gba-playtest" / "run_autoplay_checks.py"
FINGERPRINTS = ROOT / "tools" / "gba-playtest" / "fingerprints"
PLAYER_PHASE = ROOT / "src" / "playerphase.c"
sys.path.insert(0, str(ROOT / "tools" / "gba-playtest"))

import gba_playtest  # noqa: E402


def run(command):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


class AutoplayHostTests(unittest.TestCase):
    def test_archival_bmphase_omits_autoplay_relation_helper(self):
        if ARM_CC is None or ARM_NM is None:
            self.skipTest("arm-none-eabi compiler/binutils unavailable")
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            obj = Path(temporary) / "bmphase-archival.o"
            completed = run(
                [
                    ARM_CC,
                    "-mcpu=arm7tdmi",
                    "-mthumb",
                    "-mthumb-interwork",
                    "-std=gnu89",
                    "-ffreestanding",
                    "-fno-builtin",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    *INCLUDES,
                    "-DFE8_ARCHIVAL_BUILD=1",
                    "-c",
                    str(BMPHASE_SOURCE),
                    "-o",
                    str(obj),
                ]
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            symbols = run([ARM_NM, str(obj)])
            self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
            self.assertNotIn("IsAllegianceAllied", symbols.stdout)
            self.assertNotIn("ExpansionAutoplay", symbols.stdout)

    def test_debug_activation_closes_side_windows_before_phase_exit(self):
        text = PLAYER_PHASE.read_text(encoding="utf-8")
        activation = text.index("if (ExpansionAutoplay_TryActivateScenario(")
        cleanup = text.index("EndPlayerPhaseSideWindows();", activation)
        phase_exit = text.index("Proc_Goto(proc, 3);", activation)
        early_return = text.index("return;", activation)
        self.assertLess(activation, cleanup)
        self.assertLess(cleanup, phase_exit)
        self.assertLess(phase_exit, early_return)

    def test_public_controller_and_telemetry_contract(self):
        if CC is None:
            self.skipTest("no host C compiler")
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            for config, extra_define in (("debug", []), ("release", ["-DNDEBUG"])):
                with self.subTest(config=config):
                    executable = Path(temporary) / f"autoplay-host-{config}"
                    completed = run(
                        [
                            CC,
                            "-std=gnu89",
                            "-Werror=declaration-after-statement",
                            "-Werror=implicit-function-declaration",
                            "-Werror=implicit-int",
                            "-O2",
                            "-fstrict-aliasing",
                            "-Wstrict-aliasing=2",
                            *INCLUDES,
                            "-DFE8_EXPANSION_MODERN_BUILD=1",
                            *extra_define,
                            str(SOURCE),
                            str(BMPHASE_SOURCE),
                            str(DRIVER),
                            "-o",
                            str(executable),
                        ]
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        "compile failed:\n" + completed.stdout + completed.stderr,
                    )
                    completed = run([str(executable)])
                    self.assertEqual(
                        completed.returncode,
                        0,
                        "driver failed:\n" + completed.stdout + completed.stderr,
                    )
                    self.assertIn("AUTOPLAY_HOST_TEST: PASS", completed.stdout)

    def test_arm_aapcs_rom_and_ram_budgets(self):
        if ARM_CC is None or ARM_NM is None or ARM_SIZE is None:
            self.skipTest("arm-none-eabi compiler/binutils unavailable")
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            obj = Path(temporary) / "expansion_autoplay.o"
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
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    *INCLUDES,
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-c",
                    str(SOURCE),
                    "-o",
                    str(obj),
                ]
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            symbols = run([ARM_NM, "-S", str(obj)])
            self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
            telemetry = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"gExpansionAutoplayTelemetry$",
                symbols.stdout,
                flags=re.MULTILINE,
            )
            controller = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"sExpansionBlueControl$",
                symbols.stdout,
                flags=re.MULTILINE,
            )
            self.assertIsNotNone(telemetry, "telemetry symbol missing")
            self.assertIsNotNone(controller, "controller symbol missing")
            self.assertEqual(int(telemetry.group(1), 16), 64)
            self.assertEqual(int(controller.group(1), 16), 4)
            sections = run([ARM_SIZE, "-A", str(obj)])
            self.assertEqual(sections.returncode, 0, sections.stdout + sections.stderr)
            ewram_bytes = sum(
                int(value)
                for value in re.findall(
                    r"^ewram_data\s+(\d+)\s+", sections.stdout, re.MULTILINE
                )
            )
            text_bytes = sum(
                int(value)
                for value in re.findall(
                    r"^\.text\s+(\d+)\s+", sections.stdout, re.MULTILINE
                )
            )
            iwram_bytes = sum(
                int(value)
                for value in re.findall(
                    r"^iwram_data\s+(\d+)\s+", sections.stdout, re.MULTILINE
                )
            )
            self.assertEqual(ewram_bytes, 0)
            self.assertEqual(iwram_bytes, 68)
            self.assertLessEqual(text_bytes, 4096)

    def test_checked_runtime_evidence_satisfies_semantic_contract(self):
        module = runpy.run_path(str(RUNNER))
        cases = (
            (
                "autoplay-computer-modern-debug",
                module["_check_positive"],
                (),
            ),
            (
                "autoplay-player-default-modern-debug",
                module["_check_default"],
                ("debug",),
            ),
            (
                "autoplay-player-default-modern-release",
                module["_check_default"],
                ("release",),
            ),
        )
        for name, check, args in cases:
            with self.subTest(name=name):
                path = FINGERPRINTS / f"{name}.json"
                self.assertTrue(path.is_file(), f"missing runtime fingerprint {path}")
                fingerprint = gba_playtest.validate_fingerprint(
                    json.loads(path.read_text(encoding="utf-8")),
                    str(path),
                    policy="behavior",
                )
                self.assertEqual(check(fingerprint, *args), [])

        positive = gba_playtest.parse_scenario_data(module["_positive_data"]())
        self.assertEqual(positive.name, "autoplay-computer-modern-debug")
        self.assertIn(
            gba_playtest.KEY_BITS["SELECT"]
            | gba_playtest.KEY_BITS["START"]
            | gba_playtest.KEY_BITS["R"],
            [entry.key_mask for entry in positive.inputs],
        )
        self.assertEqual(positive.inputs[-1].key_mask, 1)
        self.assertEqual(positive.checkpoints[-1].frame, 18000)
        for config in ("debug", "release"):
            negative = gba_playtest.parse_scenario_data(
                module["_negative_data"](config)
            )
            self.assertEqual(
                negative.name, f"autoplay-player-default-modern-{config}"
            )


if __name__ == "__main__":
    unittest.main()
