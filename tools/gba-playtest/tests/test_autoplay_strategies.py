"""Issue #90 typed autoplay strategy registry and profile contract."""

import json
import os
import re
import shlex
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


class AutoplayStrategyCaseContractTests(unittest.TestCase):
    def test_canonical_arm_command_is_exact_and_runnable(self):
        registry = json.loads(
            (ROOT / "docs" / "test-cases" / "registry.json").read_text(
                encoding="utf-8"
            )
        )
        case = next(
            item for item in registry["cases"]
            if item["id"] == "TC-AUTOPLAY-STRATEGY-001"
        )
        expected = (
            "python3 -m unittest "
            "tools.gba-playtest.tests.test_autoplay_strategies."
            "AutoplayStrategiesRuntimeTests."
            "test_arm_profiles_bound_pending_ewram_and_gate_reference_callbacks -v"
        )
        commands = [entry["command"] for entry in case["automation"]]
        self.assertIn(expected, commands)

        command = shlex.split(expected)
        self.assertEqual(
            command[-2].rsplit(".", 1)[-1],
            "test_arm_profiles_bound_pending_ewram_and_gate_reference_callbacks",
        )
        completed = run(command)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_downstream_strategy_example_parses_and_compiles(self):
        docs = (ROOT / "docs" / "autoplay.md").read_text(encoding="utf-8")
        section = docs.split("### Downstream strategy example", 1)[1].split(
            "The runtime uses", 1
        )[0]
        c_source = re.search(r"```c\n(.*?)```", section, re.DOTALL).group(1)
        descriptor = json.loads(
            re.search(r"```json\n(.*?)```", section, re.DOTALL).group(1)
        )

        strategy = descriptor["strategies"][0]
        self.assertEqual(
            strategy,
            {
                "id": "AUTOPLAY_STRATEGY_ADVANCE_ONLY",
                "callback": "ExpansionAutoplayStrategy_AdvanceOnly",
                "objectiveKinds": ["reach_area"],
                "actionKinds": ["objective_move"],
            },
        )
        assignment = descriptor["chapters"][0]["groupAssignments"][0]
        self.assertEqual(
            assignment["strategy"],
            "AUTOPLAY_STRATEGY_ADVANCE_ONLY",
        )
        for required in (
            "return false;",
            "return true;",
            "AiClearDecision();",
            "gAiDecision.actionId != AI_ACTION_NONE",
        ):
            self.assertIn(required, c_source)

        if CC is not None:
            completed = subprocess.run(
                [
                    CC,
                    "-std=gnu89",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-I",
                    str(ROOT / "include"),
                    "-I",
                    str(ROOT / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-x",
                    "c",
                    "-fsyntax-only",
                    "-",
                ],
                cwd=ROOT,
                input=c_source,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )

    def test_direct_c_example_handles_phase_active_without_claiming_deferral(self):
        tutorial = (ROOT / "docs" / "generated_data_tutorial.md").read_text(
            encoding="utf-8"
        )
        section = tutorial.split("### Direct C assignment changes", 1)[1].split(
            "`activationFlag` and `deactivationFlag`", 1
        )[0]
        c_source = re.search(r"```c\n(.*?)```", section, re.DOTALL).group(1)
        self.assertIn("EXPANSION_AUTOPLAY_STRATEGY_ERR_PHASE_ACTIVE", c_source)
        self.assertIn("Nothing was queued", c_source)
        self.assertNotIn("EventActivate", c_source)

        if CC is not None:
            completed = subprocess.run(
                [
                    CC,
                    "-std=gnu89",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-I",
                    str(ROOT / "include"),
                    "-I",
                    str(ROOT / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-x",
                    "c",
                    "-fsyntax-only",
                    "-",
                ],
                cwd=ROOT,
                input=(
                    '#include "global.h"\n'
                    '#include "constants/event-flags.h"\n'
                    '#include "expansion_autoplay_strategies.h"\n\n'
                    + c_source
                ),
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )

    def test_structured_budget_separates_router_and_reference_deltas(self):
        evidence = json.loads(
            (ROOT / "reports" / "autoplay_strategy_budget.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(evidence["schema"], "fe8.autoplay-strategy-budget.v1")
        internal_seam = "MODERN_INTERNAL_AUTOPLAY_STRATEGY_ROUTER_ABSENT"
        self.assertIn(
            internal_seam,
            (ROOT / "modern.mk").read_text(encoding="utf-8"),
        )
        for public_surface in (
            ROOT / "configure.ac",
            ROOT / "scripts" / "modernize" / "expansion_config.py",
            ROOT / "docs" / "config_identity.md",
        ):
            self.assertNotIn(
                internal_seam,
                public_surface.read_text(encoding="utf-8"),
                str(public_surface),
            )

        registry = json.loads(
            (ROOT / "docs" / "test-cases" / "registry.json").read_text(
                encoding="utf-8"
            )
        )
        case = next(
            item for item in registry["cases"]
            if item["id"] == "TC-AUTOPLAY-STRATEGY-001"
        )
        self.assertIn(
            {
                "command": "make expansion-modern-autoplay-strategy-budget",
                "evidence": "reports/autoplay_strategy_budget.json",
            },
            case["automation"],
        )

        for config in ("debug", "release"):
            values = evidence["configs"][config]
            current = json.loads(
                (
                    ROOT
                    / "reports"
                    / "linker-budget"
                    / "modern-{}.json".format(config)
                ).read_text(encoding="utf-8")
            )
            current_end = next(
                item["address"]
                for item in current["pinned_assignments"]
                if item["name"] == "__floating_end"
            )
            self.assertEqual(
                values["profiles_disabled"]["floating_end"],
                current_end,
            )
            self.assertEqual(
                values["profiles_disabled"]["shared_router_delta_bytes"],
                current_end - values["router_absent"]["floating_end"],
            )
            self.assertEqual(
                values["references_enabled"]["reference_incremental_delta_bytes"],
                values["references_enabled"]["floating_end"] - current_end,
            )
            self.assertGreater(
                values["profiles_disabled"]["shared_router_delta_bytes"],
                0,
            )
            self.assertGreater(
                values["references_enabled"]["reference_incremental_delta_bytes"],
                0,
            )
            self.assertFalse(any(values["router_absent"]["symbols"].values()))
            self.assertTrue(all(values["profiles_disabled"]["symbols"].values()))
            self.assertTrue(all(values["references_enabled"]["symbols"].values()))

    def test_budget_regeneration_is_independent_of_git_and_router_source(self):
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            work = Path(temporary)
            def write_linker_report(path, floating_end):
                path.write_text(
                    json.dumps({
                        "pinned_assignments": [
                            {
                                "name": "__floating_end",
                                "address": floating_end,
                            }
                        ]
                    }),
                    encoding="utf-8",
                )

            absent_debug = work / "absent-debug.json"
            absent_release = work / "absent-release.json"
            disabled_debug = work / "disabled-debug.json"
            disabled_release = work / "disabled-release.json"
            enabled_debug = work / "enabled-debug.json"
            enabled_release = work / "enabled-release.json"
            write_linker_report(absent_debug, 1000)
            write_linker_report(absent_release, 2000)
            write_linker_report(disabled_debug, 1100)
            write_linker_report(disabled_release, 2200)
            write_linker_report(enabled_debug, 1130)
            write_linker_report(enabled_release, 2240)
            outputs = []
            fake_nm = work / "fake-nm.py"
            fake_nm.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "if 'absent' not in pathlib.Path(sys.argv[-1]).name:\n"
                "    print('00000000 T ExpansionAutoplayStrategies_TryDecide')\n"
                "    print('00000004 D gExpansionAutoplayStrategies')\n"
                "    print('00000008 D gExpansionAutoplayStrategyBundles')\n",
                encoding="utf-8",
            )
            fake_nm.chmod(0o755)
            elf_paths = {}
            for profile in ("absent", "disabled", "enabled"):
                for config in ("debug", "release"):
                    path = work / "{}-{}.elf".format(profile, config)
                    path.write_bytes(b"")
                    elf_paths[(profile, config)] = path
            for caller_profile in ("0", "1"):
                output = work / "report-{}.json".format(caller_profile)
                env = dict(os.environ)
                env["GIT_DIR"] = str(work / "missing-git-directory")
                env["EXPANSION_AUTOPLAY_STRATEGIES"] = caller_profile
                completed = subprocess.run(
                    [
                        "python3",
                        str(
                            ROOT
                            / "scripts"
                            / "linker_report"
                            / "autoplay_strategy_budget.py"
                        ),
                        "--nm",
                        str(fake_nm),
                        "--absent-debug",
                        str(absent_debug),
                        "--absent-release",
                        str(absent_release),
                        "--disabled-debug",
                        str(disabled_debug),
                        "--disabled-release",
                        str(disabled_release),
                        "--enabled-debug",
                        str(enabled_debug),
                        "--enabled-release",
                        str(enabled_release),
                        "--absent-debug-elf",
                        str(elf_paths[("absent", "debug")]),
                        "--absent-release-elf",
                        str(elf_paths[("absent", "release")]),
                        "--disabled-debug-elf",
                        str(elf_paths[("disabled", "debug")]),
                        "--disabled-release-elf",
                        str(elf_paths[("disabled", "release")]),
                        "--enabled-debug-elf",
                        str(elf_paths[("enabled", "debug")]),
                        "--enabled-release-elf",
                        str(elf_paths[("enabled", "release")]),
                        "--output",
                        str(output),
                    ],
                    cwd=work,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )
                outputs.append(json.loads(output.read_text(encoding="utf-8")))
            self.assertEqual(outputs[0], outputs[1])
            report = outputs[0]
            self.assertEqual(
                report["configs"]["debug"]["profiles_disabled"][
                    "shared_router_delta_bytes"
                ],
                100,
            )
            self.assertEqual(
                report["configs"]["release"]["references_enabled"][
                    "reference_incremental_delta_bytes"
                ],
                40,
            )

    def test_budget_make_forces_profiles_under_enabled_caller(self):
        env = dict(os.environ)
        env["EXPANSION_AUTOPLAY_STRATEGIES"] = "1"

        def dry_run(target):
            completed = subprocess.run(
                [
                    "make",
                    "-n",
                    "--no-print-directory",
                    target,
                    "MAKE=true",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            return completed.stdout

        absent = dry_run("expansion-modern-autoplay-strategy-router-absent-budget")
        self.assertIn("EXPANSION_AUTOPLAY_STRATEGIES=0", absent)
        enabled = dry_run("expansion-modern-autoplay-strategy-enabled-budget")
        self.assertIn("EXPANSION_AUTOPLAY_STRATEGIES=1", enabled)
        owner = dry_run("expansion-modern-autoplay-strategy-budget")
        logical_owner = owner.replace("\\\n", " ")
        disabled_lines = [
            line for line in logical_owner.splitlines()
            if "true expansion-modern-budget MODERN_CONFIG=" in line
        ]
        self.assertEqual(len(disabled_lines), 2, owner)
        self.assertTrue(
            all("EXPANSION_AUTOPLAY_STRATEGIES=0" in line for line in disabled_lines),
            owner,
        )


class AutoplayStrategiesRuntimeTests(unittest.TestCase):
    def test_public_header_is_include_order_independent(self):
        if CC is None:
            self.skipTest("no host C compiler")

        source = """
#include "expansion_autoplay_strategies.h"
#include "event.h"

static void (*sEventActivate)(struct EventEngineProc*) =
    ExpansionAutoplayStrategies_EventActivate;

int main(void)
{
    return sEventActivate == 0;
}
"""
        completed = subprocess.run(
            [
                CC,
                "-std=gnu89",
                "-Werror",
                "-I",
                str(ROOT / "include"),
                "-I",
                str(ROOT / "include" / "generated"),
                "-DFE8_EXPANSION_MODERN_BUILD=1",
                "-x",
                "c",
                "-fsyntax-only",
                "-",
            ],
            cwd=ROOT,
            input=source,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

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
                            "-DFE8_AUTOPLAY_STRATEGY_RUNTIME_TEST=1",
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

    def test_arm_profiles_bound_pending_ewram_and_gate_reference_callbacks(self):
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
                    self.assertEqual(strategy_ewram_bytes, 8)
                    self.assertEqual(ewram_bytes, 28)
                    self.assertLessEqual(text_bytes, 4096)


if __name__ == "__main__":
    unittest.main()
