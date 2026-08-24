"""Issue #87 host, menu, localization, and resource contract checks."""

import json
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INCLUDES = ["-I", str(ROOT / "include"), "-I", str(ROOT / "include" / "generated")]
SOURCE = ROOT / "src" / "expansion_blue_phase_delegate.c"
AUTOPLAY_SOURCE = ROOT / "src" / "expansion_autoplay.c"
CP_ORDER_SOURCE = ROOT / "src" / "cp_order.c"
MENU_DEF_SOURCE = ROOT / "src" / "menu_def.c"
BMMENU_SOURCE = ROOT / "src" / "bmmenu.c"
HELPBOX_SOURCE = ROOT / "src" / "helpbox.c"
STATSCREEN_SOURCE = ROOT / "src" / "statscreen.c"
DRIVER = Path(__file__).resolve().parent / "c" / "expansion_blue_phase_delegate_driver.c"
FLAG = "FE8_EXPANSION_BLUE_PHASE_DELEGATE=1"
MODERN = "FE8_EXPANSION_MODERN_BUILD=1"
CC = shutil.which("gcc") or shutil.which("cc")
NM = shutil.which("nm")
ARM_CC = shutil.which("arm-none-eabi-gcc")
ARM_SIZE = shutil.which("arm-none-eabi-size")
RUNNER = ROOT / "tools" / "gba-playtest" / "run_blue_phase_delegate_checks.py"
FINGERPRINT = (
    ROOT
    / "tools"
    / "gba-playtest"
    / "fingerprints"
    / "autoplay-charge-modern-debug.json"
)


def run(command):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def compile_object(work, source, name, defines=(), include_dirs=()):
    output = Path(work) / name
    command = [CC, "-std=gnu89", "-c", "-w"]
    for directory in include_dirs:
        command += ["-I", str(directory)]
    command += INCLUDES
    for define in defines:
        command += ["-D", define]
    command += [str(source), "-o", str(output)]
    completed = run(command)
    return completed, output


def symbol_size(path, name):
    completed = run([NM, "--print-size", str(path)])
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[3] == name:
            return int(fields[1], 16)
    raise AssertionError(f"missing symbol {name} in {path}")


class BluePhaseDelegateHostTests(unittest.TestCase):
    def test_real_lifecycle_and_invalid_state_driver(self):
        if CC is None:
            self.skipTest("no host C compiler")
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build) as temporary:
            for name, extra in (("debug", []), ("release", ["-DNDEBUG"])):
                with self.subTest(config=name):
                    executable = Path(temporary) / f"delegate-{name}"
                    command = [
                        CC,
                        "-std=gnu89",
                        "-O2",
                        "-ffunction-sections",
                        "-fdata-sections",
                        "-Werror=declaration-after-statement",
                        "-Werror=implicit-function-declaration",
                        "-Werror=implicit-int",
                        *INCLUDES,
                        "-D" + FLAG,
                        "-D" + MODERN,
                        *extra,
                        str(SOURCE),
                        str(AUTOPLAY_SOURCE),
                        str(CP_ORDER_SOURCE),
                        str(DRIVER),
                        "-Wl,--gc-sections",
                        "-o",
                        str(executable),
                    ]
                    completed = run(command)
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stdout + completed.stderr,
                    )
                    completed = run([str(executable)])
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stdout + completed.stderr,
                    )
                    self.assertIn(
                        "BLUE_PHASE_DELEGATE_HOST_TEST: PASS",
                        completed.stdout,
                    )

    def test_disabled_module_has_no_delegate_symbols(self):
        if CC is None or NM is None:
            self.skipTest("host compiler/binutils unavailable")
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build) as temporary:
            completed, obj = compile_object(
                temporary, SOURCE, "delegate-disabled.o", defines=[MODERN]
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            symbols = run([NM, str(obj)]).stdout
        self.assertNotIn("ExpansionBluePhaseDelegate_", symbols)

    def test_compile_time_dependency_rejects_nonmodern_enable(self):
        if CC is None:
            self.skipTest("no host C compiler")
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build) as temporary:
            source = Path(temporary) / "dependency.c"
            source.write_text(
                '#include "global.h"\nint main(void) { return 0; }\n',
                encoding="utf-8",
            )
            output = Path(temporary) / "dependency.o"
            completed = run(
                [
                    CC,
                    "-std=gnu89",
                    *INCLUDES,
                    "-D" + FLAG,
                    "-DFE8_EXPANSION_MODERN_BUILD=0",
                    "-c",
                    str(source),
                    "-o",
                    str(output),
                ]
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("requires the modern issue #85 blue controller", completed.stderr)


class BluePhaseDelegateMenuTests(unittest.TestCase):
    @staticmethod
    def generated_header(directory):
        path = Path(directory) / "expansion_msg_ids.h"
        path.write_text(
            "#define EXP_MSG_RAW_SURFACE_UNIT_ACTION_SUMMON 33u\n"
            "#define EXP_MSG_RAW_SURFACE_UNIT_ACTION_CALL_MONSTER 34u\n"
            "#define EXP_MSG_AUTOPLAY_CHARGE_LABEL 79u\n"
            "#define EXP_MSG_AUTOPLAY_CHARGE_HELP 80u\n",
            encoding="utf-8",
        )
        return path

    def test_disabled_table_unchanged_and_compositions_fit_capacity(self):
        if CC is None or NM is None:
            self.skipTest("host compiler/binutils unavailable")
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build) as temporary:
            self.generated_header(temporary)
            completed, disabled = compile_object(
                temporary,
                MENU_DEF_SOURCE,
                "menu-disabled.o",
                defines=["MODERN=1", MODERN],
                include_dirs=[temporary],
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            completed, enabled = compile_object(
                temporary,
                MENU_DEF_SOURCE,
                "menu-enabled.o",
                defines=["MODERN=1", MODERN, FLAG],
                include_dirs=[temporary],
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            completed, composed = compile_object(
                temporary,
                MENU_DEF_SOURCE,
                "menu-composed.o",
                defines=[
                    "MODERN=1",
                    MODERN,
                    FLAG,
                    "FE8_EXPANSION_DANGER_OVERLAY_MENU=1",
                ],
                include_dirs=[temporary],
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            disabled_size = symbol_size(disabled, "gMapMenuItems")
            enabled_size = symbol_size(enabled, "gMapMenuItems")
            composed_size = symbol_size(composed, "gMapMenuItems")
        item_size = enabled_size - disabled_size
        self.assertGreater(item_size, 0)
        self.assertEqual(composed_size, disabled_size + 2 * item_size)
        self.assertEqual(disabled_size // item_size - 1, 8)
        self.assertEqual(composed_size // item_size - 1, 10)
        self.assertLessEqual(composed_size // item_size - 1, 11)

    def test_enabled_menu_and_help_sources_compile(self):
        if CC is None:
            self.skipTest("no host compiler")
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build) as temporary:
            self.generated_header(temporary)
            for source in (BMMENU_SOURCE, HELPBOX_SOURCE, STATSCREEN_SOURCE):
                with self.subTest(source=source.name):
                    completed, _ = compile_object(
                        temporary,
                        source,
                        source.stem + ".o",
                        defines=["MODERN=1", MODERN, FLAG],
                        include_dirs=[temporary],
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stdout + completed.stderr,
                    )

    def test_expansion_help_id_never_enters_the_vanilla_catalog(self):
        bmmenu = BMMENU_SOURCE.read_text(encoding="utf-8")
        menu_def = MENU_DEF_SOURCE.read_text(encoding="utf-8")
        function = re.search(
            r"u8 ExpansionBluePhaseDelegate_MenuRPress\(.*?\n\}",
            bmmenu,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(function)
        self.assertIn("MenuAutoHelpBoxSelect(menu);", function.group(0))
        self.assertNotIn("MenuStdHelpBox", function.group(0))
        self.assertIn("ExpansionBluePhaseDelegate_MenuHelpBox", bmmenu)
        self.assertIn("StartHelpBoxString(", bmmenu)
        self.assertIn(
            "ExpansionLocale_ResolveCurrentPersistent(EXP_MSG_AUTOPLAY_CHARGE_HELP)",
            bmmenu,
        )
        self.assertNotIn("sBluePhaseDelegateHelpText", bmmenu)
        self.assertIn("EXP_MSG_AUTOPLAY_CHARGE_HELP", menu_def)
        self.assertIn("ExpansionBluePhaseDelegate_MenuRPress", menu_def)
        self.assertIn("ExpansionBluePhaseDelegate_MenuHelpBox", menu_def)


class BluePhaseDelegateLocalizationTests(unittest.TestCase):
    def test_stable_ids_and_every_authored_locale(self):
        registry = json.loads(
            (ROOT / "texts" / "expansion" / "registry.json").read_text(
                encoding="utf-8"
            )
        )
        entries = {entry["key"]: entry for entry in registry["messages"]}
        self.assertEqual(entries["autoplay.charge.label"]["id"], 79)
        self.assertEqual(entries["autoplay.charge.help"]["id"], 80)
        for locale in ("en", "ja", "zh-Hans", "fr", "de", "es", "it"):
            catalog = json.loads(
                (
                    ROOT / "texts" / "expansion" / f"catalog.{locale}.json"
                ).read_text(encoding="utf-8")
            )
            strings = catalog["strings"]
            self.assertTrue(strings["autoplay.charge.label"])
            self.assertTrue(strings["autoplay.charge.help"])
            self.assertNotIn("\n", strings["autoplay.charge.label"])
            self.assertLessEqual(len(strings["autoplay.charge.label"]), 8)


class BluePhaseDelegateResourceTests(unittest.TestCase):
    def test_arm_module_has_no_static_ram(self):
        if ARM_CC is None or ARM_SIZE is None:
            self.skipTest("arm-none-eabi compiler/binutils unavailable")
        build = ROOT / "build"
        build.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build) as temporary:
            obj = Path(temporary) / "delegate.o"
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
                    *INCLUDES,
                    "-D" + FLAG,
                    "-D" + MODERN,
                    "-c",
                    str(SOURCE),
                    "-o",
                    str(obj),
                ]
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            sections = run([ARM_SIZE, "-A", str(obj)])
            self.assertEqual(sections.returncode, 0, sections.stderr)
        ram = sum(
            int(value)
            for value in re.findall(
                r"^(?:\.bss|ewram_data|iwram_data)\s+(\d+)\s+",
                sections.stdout,
                re.MULTILINE,
            )
        )
        text = sum(
            int(value)
            for value in re.findall(
                r"^\.text\s+(\d+)\s+", sections.stdout, re.MULTILINE
            )
        )
        rom_data = sum(
            int(value)
            for value in re.findall(
                r"^\.data\s+(\d+)\s+", sections.stdout, re.MULTILINE
            )
        )
        self.assertEqual(ram, 0)
        self.assertLessEqual(text, 2048)
        self.assertLessEqual(rom_data, 64)


class BluePhaseDelegateRuntimeContractTests(unittest.TestCase):
    def test_checked_positive_is_semantic_and_menu_driven(self):
        self.assertTrue(FINGERPRINT.is_file(), f"missing {FINGERPRINT}")
        module = runpy.run_path(str(RUNNER))
        capture = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
        self.assertEqual(module["_check_positive"](capture), [])
        scenario = module["_positive_data"]()
        key_sets = [tuple(frame["keys"]) for frame in scenario["frames"]]
        self.assertIn(("A",), key_sets)
        self.assertIn(("UP",), key_sets)
        self.assertIn(("R",), key_sets)
        self.assertNotIn(("SELECT", "START", "R"), key_sets)
        self.assertIn(
            "charge-r-help-domain-guard",
            [checkpoint["name"] for checkpoint in scenario["checkpoints"]],
        )
        self.assertIn(
            "next-blue-boundary-player-restored",
            [checkpoint["name"] for checkpoint in scenario["checkpoints"]],
        )
        self.assertEqual(
            scenario["checkpoints"][-1]["name"],
            "next-blue-player-interactive",
        )


if __name__ == "__main__":
    unittest.main()
