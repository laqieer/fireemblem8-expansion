import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CC = shutil.which("gcc") or shutil.which("cc")
INCLUDES = ["-I", str(ROOT / "include"), "-I", str(ROOT / "include/generated")]
SRC = ROOT / "src/debugtools_diagnostics.c"
FIXTURES = ROOT / "tools/gba-playtest/tests/c"


class DebugToolsDiagnosticsHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if CC is None:
            raise unittest.SkipTest("no host C compiler")

    def _compile(self, work, source, output, defines=()):
        command = [CC, "-c", "-std=gnu89", "-w", *INCLUDES]
        command += [f"-D{define}" for define in defines]
        command += [str(source), "-o", str(work / output)]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return work / output

    def _link_and_run(self, work, objects, name):
        executable = work / name
        result = subprocess.run(
            [CC, *map(str, objects), "-o", str(executable)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run(
            [str(executable)], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_enabled_provider_contexts_and_forced_restoration(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            objects = [
                self._compile(
                    work,
                    SRC,
                    "diagnostics.o",
                    (
                        "MODERN=1",
                        "FE8_EXPANSION_DEBUGTOOLS_ENABLED=1",
                        "FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST=1",
                    ),
                ),
                self._compile(
                    work,
                    FIXTURES / "debugtools_diagnostics_host_stubs.c",
                    "stubs.o",
                    (
                        "MODERN=1",
                        "FE8_EXPANSION_DEBUGTOOLS_ENABLED=1",
                        "FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST=1",
                    ),
                ),
                self._compile(
                    work,
                    FIXTURES / "debugtools_diagnostics_driver.c",
                    "driver.o",
                    (
                        "MODERN=1",
                        "FE8_EXPANSION_DEBUGTOOLS_ENABLED=1",
                        "FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST=1",
                    ),
                ),
            ]
            output = self._link_and_run(work, objects, "diagnostics-test")
            self.assertIn("DEBUGTOOLS_DIAGNOSTICS_HOST_TEST: PASS", output)

    def test_disabled_provider_is_zeroing_stub(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            objects = [
                self._compile(
                    work,
                    SRC,
                    "diagnostics-disabled.o",
                    ("MODERN=1", "FE8_EXPANSION_DEBUGTOOLS_ENABLED=0"),
                ),
                self._compile(
                    work,
                    FIXTURES / "debugtools_diagnostics_disabled_driver.c",
                    "disabled-driver.o",
                    ("MODERN=1", "FE8_EXPANSION_DEBUGTOOLS_ENABLED=0"),
                ),
            ]
            output = self._link_and_run(
                work, objects, "diagnostics-disabled-test"
            )
            self.assertIn(
                "DEBUGTOOLS_DIAGNOSTICS_DISABLED_HOST_TEST: PASS", output
            )

    def test_owned_path_forbids_raw_display_and_framebuffer_oracles(self):
        sources = SRC.read_text(encoding="utf-8")
        for forbidden in (
            "SetupDebugFontForBG(",
            "SetupDebugFontForOBJ(",
            "PrintDebugStringToBG(",
            "PutDrawText(",
            "gLCDControlBuffer.dispcnt.bg2_on =",
            "MENU_ACT_CLEAR",
        ):
            self.assertNotIn(forbidden, sources)

        for path in (
            ROOT / "tools/gba-playtest/scenarios",
            ROOT / "tools/gba-playtest/fingerprints",
        ):
            for file in path.glob("debugtools-diagnostics-*.json"):
                text = file.read_text(encoding="utf-8")
                self.assertNotIn('"framebuffer": true', text)
                self.assertNotIn("fnv1a64-rgb24", text)

    def test_reused_owner_proc_payload_is_initialized(self):
        source = SRC.read_text(encoding="utf-8")
        begin = source.index("enum DebugToolsResult DebugToolsDiagnostics_BeginSession")
        end = source.index("void DebugToolsDiagnostics_EndSession", begin)
        body = source[begin:end]

        for initialization in (
            "displayOwner->ownerLock = 0;",
            "displayOwner->restoring = 0;",
            "displayOwner->endMode = DEBUGTOOLS_END_EXTERNAL;",
            "memset(&scratch->scratch, 0, sizeof(scratch->scratch));",
        ):
            self.assertIn(initialization, body)

    def test_scenario_specs_are_scalar_and_cover_all_frozen_contexts(self):
        scenarios = ROOT / "tools/gba-playtest/specs"
        debug = json.loads(
            (scenarios / "debugtools-diagnostics-modern-debug.json").read_text(
                encoding="utf-8"
            )
        )
        release = json.loads(
            (scenarios / "debugtools-diagnostics-modern-release.json").read_text(
                encoding="utf-8"
            )
        )
        prep = json.loads(
            (scenarios / "debugtools-diagnostics-prep-modern-debug.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(debug["tail_frames"], release["tail_frames"])
        self.assertEqual(debug["base_frame_limit"], release["base_frame_limit"])
        self.assertEqual(prep["kind"], "prep")
        self.assertIn("title", debug["checkpoint_frames"])
        self.assertIn("map_runtime", debug["checkpoint_frames"])
        self.assertIn("prep", prep["checkpoint_frames"])
        runner = (
            ROOT / "tools/gba-playtest/run_debugtools_diagnostics_checks.py"
        ).read_text(encoding="utf-8")
        for semantic in (
            "battleRejectCount",
            "emptyUnitCaptureCount",
            "restorationMismatchMask",
            "postViewMapIdleCount",
            "prepCaptureCount",
        ):
            self.assertIn(semantic, runner)
        self.assertNotIn("framebuffer_hash", runner)

    def test_source_and_linker_enforce_frozen_resource_contract(self):
        modern_mk = (ROOT / "modern.mk").read_text(encoding="utf-8")
        linker = (ROOT / "linker/expansion.ld").read_text(encoding="utf-8")
        legacy = (ROOT / "ldscript.txt").read_text(encoding="utf-8")
        header = (ROOT / "include/expansion_debugtools.h").read_text(
            encoding="utf-8"
        )
        internal = (ROOT / "include/debugtools_internal.h").read_text(
            encoding="utf-8"
        )

        self.assertIn("src/debugtools_diagnostics.c", modern_mk)
        self.assertIn("expansion-modern-debugtools-diagnostics-check", modern_mk)
        self.assertIn("ewram_overlay_debugtools", linker)
        self.assertIn("<= 0x630", linker)
        self.assertIn("<= 0x70", linker)
        self.assertNotIn("ewram_overlay_debugtools", legacy)
        self.assertIn("DEBUGTOOLS_BUILTIN_ID_MAX = 9", header)
        self.assertIn("DEBUGTOOLS_CONTRIBUTOR_ID_MIN = 10", header)
        self.assertIn("sizeof(struct DebugToolsDiagnosticsSnapshot)", (
            ROOT / "src/debugtools_diagnostics.c"
        ).read_text(encoding="utf-8"))
        self.assertIn("restorationMismatchMask", internal)


if __name__ == "__main__":
    unittest.main()
