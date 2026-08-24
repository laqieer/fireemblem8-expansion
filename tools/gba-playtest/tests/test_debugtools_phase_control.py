"""Issue #124 focused host, ARM, localization, and release-omission checks."""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BUILD = ROOT / "build"
INCLUDES = ["-I", str(ROOT / "include"), "-I", str(ROOT / "include" / "generated")]
TOOLS_SOURCE = ROOT / "src" / "debugtools_tools.c"
BM_SOURCE = ROOT / "src" / "bm.c"
BMIO_SOURCE = ROOT / "src" / "bmio.c"
REGISTRY_SOURCE = ROOT / "src" / "debugtools_registry.c"
AUTOPLAY_SOURCE = ROOT / "src" / "expansion_autoplay.c"
DRIVER = Path(__file__).resolve().parent / "c" / "debugtools_phase_control_driver.c"
REGISTRY = ROOT / "texts" / "expansion" / "registry.json"
TEST_CASE_REGISTRY = ROOT / "docs" / "test-cases" / "registry.json"
RUNTIME_RUNNER = ROOT / "tools" / "gba-playtest" / "run_debugtools_phase_control_checks.py"
BMUNIT_HEADER = ROOT / "include" / "bmunit.h"
DEBUG_RUNTIME_FINGERPRINT = (
    ROOT
    / "tools"
    / "gba-playtest"
    / "fingerprints"
    / "debugtools-phase-control-modern-debug.json"
)
BLOCKED_RUNTIME_FINGERPRINT = (
    ROOT
    / "tools"
    / "gba-playtest"
    / "fingerprints"
    / "debugtools-phase-blocked-modern-debug.json"
)
RELEASE_RUNTIME_FINGERPRINT = (
    ROOT
    / "tools"
    / "gba-playtest"
    / "fingerprints"
    / "debugtools-phase-control-modern-release.json"
)
CC = shutil.which("gcc") or shutil.which("cc")
ARM_CC = shutil.which("arm-none-eabi-gcc")
NM = shutil.which("nm")
ARM_NM = shutil.which("arm-none-eabi-nm")
ARM_SIZE = shutil.which("arm-none-eabi-size")

DEBUG_DEFINES = (
    "FE8_EXPANSION_MODERN_BUILD=1",
    "FE8_EXPANSION_DEBUGTOOLS_ENABLED=1",
)

MESSAGE_KEYS = {
    "debug.confirm.turn_increment": 121,
    "debug.confirm.turn_decrement": 122,
    "debug.confirm.red_computer": 123,
    "debug.confirm.red_blocked": 124,
    "debug.confirm.green_computer": 125,
    "debug.confirm.green_blocked": 126,
    "debug.status.turn": 127,
    "debug.mode.computer": 128,
    "debug.mode.blocked": 129,
}


def run(command):
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def write_message_header(directory):
    messages = json.loads(REGISTRY.read_text(encoding="utf-8"))["messages"]
    lines = ["#ifndef TEST_EXPANSION_MSG_IDS_H", "#define TEST_EXPANSION_MSG_IDS_H"]
    for message in messages:
        key = message["key"]
        macro = "EXP_MSG_" + re.sub(r"[^A-Za-z0-9]+", "_", key).upper()
        lines.append(f"#define {macro} {message['id']}u")
    lines += ["#endif", ""]
    (directory / "expansion_msg_ids.h").write_text("\n".join(lines), encoding="utf-8")


class DebugToolsPhaseControlHostTests(unittest.TestCase):
    def test_real_router_and_controller_contract(self):
        if CC is None:
            self.skipTest("no host C compiler")
        BUILD.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BUILD) as temporary:
            work = Path(temporary)
            write_message_header(work)
            objects = []
            for source, name in (
                (TOOLS_SOURCE, "tools.o"),
                (BM_SOURCE, "bm.o"),
                (BMIO_SOURCE, "bmio.o"),
                (AUTOPLAY_SOURCE, "autoplay.o"),
                (DRIVER, "driver.o"),
            ):
                output = work / name
                source_defines = list(DEBUG_DEFINES)
                if source == BM_SOURCE:
                    source_defines += (
                        "gProc_BMapMain=TestBmMainScript",
                        "ProcScr_CamMove=TestBmCamMoveScript",
                    )
                completed = run(
                    [
                        CC,
                        "-std=gnu89",
                        "-w",
                        "-ffunction-sections",
                        "-fdata-sections",
                        "-I",
                        str(work),
                        *INCLUDES,
                        *[f"-D{define}" for define in source_defines],
                        "-c",
                        str(source),
                        "-o",
                        str(output),
                    ]
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )
                objects.append(output)
            executable = work / "phase-control-host"
            completed = run(
                [
                    CC,
                    *map(str, objects),
                    "-Wl,--gc-sections",
                    "-o",
                    str(executable),
                ]
            )
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
            self.assertIn("DEBUGTOOLS_PHASE_CONTROL_HOST_TEST: PASS", completed.stdout)

    def test_release_object_physically_omits_phase_control(self):
        if CC is None or NM is None:
            self.skipTest("host compiler/binutils unavailable")
        BUILD.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BUILD) as temporary:
            output = Path(temporary) / "tools-release.o"
            completed = run(
                [
                    CC,
                    "-std=gnu89",
                    "-w",
                    *INCLUDES,
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DNDEBUG",
                    "-c",
                    str(TOOLS_SOURCE),
                    "-o",
                    str(output),
                ]
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            symbols = run([NM, str(output)])
            self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
            self.assertNotIn("DebugToolsPhaseControl", symbols.stdout)
            self.assertNotIn("sPhaseControlRequest", symbols.stdout)


class DebugToolsPhaseControlArmTests(unittest.TestCase):
    def test_arm_debug_budget_and_release_omission(self):
        if ARM_CC is None or ARM_NM is None or ARM_SIZE is None:
            self.skipTest("ARM compiler/binutils unavailable")
        BUILD.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BUILD) as temporary:
            work = Path(temporary)
            write_message_header(work)
            debug_object = work / "tools-debug.o"
            release_object = work / "tools-release.o"
            debug_registry = work / "registry-debug.o"
            release_registry = work / "registry-release.o"
            archival_object = work / "tools-archival.o"
            common = [
                ARM_CC,
                "-mcpu=arm7tdmi",
                "-mthumb",
                "-mthumb-interwork",
                "-mabi=aapcs",
                "-std=gnu89",
                "-ffreestanding",
                "-fno-builtin",
                "-I",
                str(work),
                *INCLUDES,
                "-DFE8_EXPANSION_MODERN_BUILD=1",
                "-c",
                str(TOOLS_SOURCE),
            ]
            debug = run(
                [
                    *common[:-1],
                    "-DFE8_EXPANSION_DEBUGTOOLS_ENABLED=1",
                    common[-1],
                    "-o",
                    str(debug_object),
                ]
            )
            self.assertEqual(debug.returncode, 0, debug.stdout + debug.stderr)
            release = run(
                [
                    *common[:-1],
                    "-DNDEBUG",
                    common[-1],
                    "-o",
                    str(release_object),
                ]
            )
            self.assertEqual(release.returncode, 0, release.stdout + release.stderr)
            debug_registry_compile = run(
                [
                    *common[:-1],
                    "-DFE8_EXPANSION_DEBUGTOOLS_ENABLED=1",
                    str(REGISTRY_SOURCE),
                    "-o",
                    str(debug_registry),
                ]
            )
            self.assertEqual(
                debug_registry_compile.returncode,
                0,
                debug_registry_compile.stdout + debug_registry_compile.stderr,
            )
            release_registry_compile = run(
                [
                    *common[:-1],
                    "-DNDEBUG",
                    str(REGISTRY_SOURCE),
                    "-o",
                    str(release_registry),
                ]
            )
            self.assertEqual(
                release_registry_compile.returncode,
                0,
                release_registry_compile.stdout + release_registry_compile.stderr,
            )
            archival = run(
                [
                    *common[:-1],
                    "-DFE8_EXPANSION_DEBUGTOOLS_ENABLED=1",
                    "-DFE8_ARCHIVAL_BUILD=1",
                    common[-1],
                    "-o",
                    str(archival_object),
                ]
            )
            self.assertEqual(archival.returncode, 0, archival.stdout + archival.stderr)
            debug_symbols = run([ARM_NM, str(debug_object)])
            release_symbols = run([ARM_NM, str(release_object)])
            archival_symbols = run([ARM_NM, str(archival_object)])
            self.assertEqual(debug_symbols.returncode, 0, debug_symbols.stderr)
            self.assertEqual(release_symbols.returncode, 0, release_symbols.stderr)
            self.assertEqual(archival_symbols.returncode, 0, archival_symbols.stderr)
            self.assertIn("DebugToolsPhaseControl_ApplyAtPhaseStart", debug_symbols.stdout)
            self.assertIn(
                "DebugToolsPhaseControl_ApplyTurnBeforePhaseEvents",
                debug_symbols.stdout,
            )
            self.assertNotIn("DebugToolsPhaseControl", release_symbols.stdout)
            self.assertNotIn("DebugToolsPhaseControl", archival_symbols.stdout)
            sized_symbols = run([ARM_NM, "-S", str(debug_object)])
            debug_registry_symbols = run([ARM_NM, "-S", str(debug_registry)])
            release_registry_symbols = run([ARM_NM, "-S", str(release_registry)])
            self.assertEqual(sized_symbols.returncode, 0, sized_symbols.stderr)
            self.assertEqual(debug_registry_symbols.returncode, 0, debug_registry_symbols.stderr)
            self.assertEqual(
                release_registry_symbols.returncode,
                0,
                release_registry_symbols.stderr,
            )
        request = re.search(
            r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
            r"sPhaseControlRequest$",
            sized_symbols.stdout,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(request, "debug request state symbol missing")
        self.assertLessEqual(int(request.group(1), 16), 16)
        debug_probe = re.search(
            r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
            r"gDebugToolsProbe$",
            debug_registry_symbols.stdout,
            flags=re.MULTILINE,
        )
        release_probe = re.search(
            r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
            r"gDebugToolsProbe$",
            release_registry_symbols.stdout,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(debug_probe, "debug telemetry probe missing")
        self.assertIsNotNone(release_probe, "release telemetry probe missing")
        self.assertEqual(int(debug_probe.group(1), 16), 0xBC)
        self.assertEqual(
            int(release_probe.group(1), 16),
            0x8C,
            "release gDebugToolsProbe must retain the #127 release layout",
        )


class DebugToolsPhaseControlLocalizationTests(unittest.TestCase):
    def test_every_authored_locale_has_the_stable_phase_control_messages(self):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        entries = {entry["key"]: entry["id"] for entry in registry["messages"]}
        self.assertEqual({key: entries[key] for key in MESSAGE_KEYS}, MESSAGE_KEYS)
        for locale in ("en", "ja", "zh-Hans", "fr", "de", "es", "it"):
            strings = json.loads(
                (ROOT / "texts" / "expansion" / f"catalog.{locale}.json").read_text(
                    encoding="utf-8"
                )
            )["strings"]
            for key in MESSAGE_KEYS:
                self.assertTrue(strings[key], f"{locale} must translate {key}")
                self.assertNotIn("\n", strings[key])


class DebugToolsPhaseControlCaseCatalogTests(unittest.TestCase):
    def test_issue_case_is_indexed_with_focused_automation(self):
        catalog = json.loads(TEST_CASE_REGISTRY.read_text(encoding="utf-8"))
        self.assertIn(
            "transient-debugtools-phase-control",
            catalog["coverage"]["expected_feature_ids"],
        )
        features = {entry["id"]: entry for entry in catalog["features"]}
        feature = features["transient-debugtools-phase-control"]
        self.assertEqual(feature["required_cases"], ["TC-DEBUGTOOLS-PROTOTYPE-002"])
        self.assertEqual(feature["reference"], "docs/debugtools.md")
        cases = {entry["id"]: entry for entry in catalog["cases"]}
        case = cases["TC-DEBUGTOOLS-PROTOTYPE-002"]
        self.assertEqual(case["feature_id"], feature["id"])
        self.assertEqual(
            case["anchor"],
            "tc-debugtools-prototype-002-transient-turn-and-faction-control",
        )
        commands = {entry["command"] for entry in case["automation"]}
        self.assertIn(
            'python3 -m unittest discover -s tools/gba-playtest/tests -p '
            '"test_debugtools_phase_control.py" -v',
            commands,
        )
        self.assertIn("make localization-validate", commands)
        self.assertIn(
            "make expansion-modern-debugtools-phase-control-check "
            "MODERN_CONFIG=debug MODERN_ABI=aapcs",
            commands,
        )
        self.assertIn(
            "make expansion-modern-debugtools-phase-control-check "
            "MODERN_CONFIG=release MODERN_ABI=aapcs",
            commands,
        )


class DebugToolsPhaseControlRuntimeContractTests(unittest.TestCase):
    def test_checked_debug_and_release_captures_are_semantic_and_menu_driven(self):
        import runpy

        self.assertTrue(RUNTIME_RUNNER.is_file(), f"missing {RUNTIME_RUNNER}")
        module = runpy.run_path(str(RUNTIME_RUNNER))
        debug_capture = json.loads(DEBUG_RUNTIME_FINGERPRINT.read_text(encoding="utf-8"))
        blocked_capture = json.loads(
            BLOCKED_RUNTIME_FINGERPRINT.read_text(encoding="utf-8")
        )
        release_capture = json.loads(
            RELEASE_RUNTIME_FINGERPRINT.read_text(encoding="utf-8")
        )
        self.assertEqual(module["_check_positive"](debug_capture), [])
        self.assertEqual(module["_check_blocked"](blocked_capture), [])
        self.assertEqual(module["_check_negative"](release_capture), [])
        frames = module["_positive_frames"]()
        key_sets = [tuple(frame["keys"]) for frame in frames]
        self.assertIn(("SELECT", "L"), key_sets)
        self.assertIn(("DOWN",), key_sets)
        self.assertIn(("R",), key_sets)
        self.assertIn(("B",), key_sets)
        self.assertIn(("A",), key_sets)
        self.assertEqual(
            [
                checkpoint["name"]
                for checkpoint in module["_positive_data"]()["checkpoints"]
            ],
            [
                "player-before-apply",
                "turn-requested-from-live-submenu",
                "red-boundary-observes-requested-turn",
                "next-blue-before-map-input",
                "next-blue-map-interactive",
            ],
        )
        self.assertEqual(
            module["FACTION_CONSTANTS"],
            {
                "FACTION_BLUE": 0x00,
                "FACTION_GREEN": 0x40,
                "FACTION_RED": 0x80,
            },
        )
        self.assertIn("FACTION_GREEN  = 0x40", BMUNIT_HEADER.read_text(encoding="utf-8"))
        self.assertIn("FACTION_RED    = 0x80", BMUNIT_HEADER.read_text(encoding="utf-8"))
        blocked_frames = module["_blocked_frames"]()
        block_downs = [
            frame
            for frame in blocked_frames
            if 17630 < frame["start"] < 18110
            and tuple(frame["keys"]) == ("DOWN",)
        ]
        self.assertEqual(
            len(block_downs),
            module["FLAG_MENU_GREEN_BLOCK_ROW"],
        )
        self.assertEqual(
            [
                checkpoint["name"]
                for checkpoint in module["_blocked_data"]()["checkpoints"]
            ],
            [
                "player-before-green-block",
                "green-block-requested",
                "green-block-next-blue-restored",
            ],
        )


if __name__ == "__main__":
    unittest.main()
