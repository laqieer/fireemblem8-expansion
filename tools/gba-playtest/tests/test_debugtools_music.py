"""Issue #126 bounded debugtools music-preview contracts."""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "c"
INCLUDES = [ROOT / "include", ROOT / "include" / "generated"]
CC = shutil.which("gcc") or shutil.which("cc")
sys.path.insert(0, str(ROOT / "tools" / "gba-playtest"))
import gba_playtest  # noqa: E402


def _compile(work, source, output, defines=()):
    command = [
        CC,
        "-c",
        "-std=gnu99",
        "-w",
        "-ffunction-sections",
        "-fdata-sections",
    ]
    for include in INCLUDES:
        command += ["-I", str(include)]
    for define in defines:
        command += ["-D", define]
    command += [str(source), "-o", str(work / output)]
    return subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _link(work, objects, output):
    return subprocess.run(
        [
            CC,
            *[str(work / obj) for obj in objects],
            "-Wl,--gc-sections",
            "-o",
            str(work / output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _defined_symbols(path):
    result = subprocess.run(
        ["nm", str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    symbols = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[1] != "U":
            symbols.add(fields[2])
    return symbols


class DebugToolsMusicHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if CC is None:
            raise unittest.SkipTest("no host C compiler")

    def setUp(self):
        self.work = ROOT / "build" / "test-artifacts" / self._testMethodName
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def _compile_ok(self, source, output, defines=()):
        result = _compile(self.work, source, output, defines)
        self.assertEqual(
            result.returncode,
            0,
            f"compiling {source} failed:\n{result.stdout}{result.stderr}",
        )

    def test_real_action_and_typed_owner_cover_boundaries_and_restoration(self):
        defines = ["FE8_EXPANSION_DEBUGTOOLS_ENABLED=1"]
        self._compile_ok(
            ROOT / "src" / "debugtools_music.c",
            "music.o",
            defines,
        )
        self._compile_ok(
            ROOT / "src" / "expansion_bgm.c",
            "bgm.o",
            defines,
        )
        self._compile_ok(
            FIXTURES / "debugtools_music_host_stubs.c",
            "stubs.o",
            defines,
        )
        self._compile_ok(
            FIXTURES / "debugtools_music_driver.c",
            "driver.o",
            defines,
        )
        result = _link(
            self.work,
            ("music.o", "bgm.o", "stubs.o", "driver.o"),
            "music-host",
        )
        self.assertEqual(
            result.returncode,
            0,
            f"linking music host test failed:\n{result.stdout}{result.stderr}",
        )
        result = subprocess.run(
            [str(self.work / "music-host")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"music host test failed:\n{result.stdout}{result.stderr}",
        )
        self.assertIn("DEBUGTOOLS_MUSIC_HOST_TEST: PASS", result.stdout)

    def test_real_sound_helper_never_unlocks_and_restores_silence(self):
        defines = ["FE8_EXPANSION_DEBUGTOOLS_ENABLED=1"]
        self._compile_ok(
            ROOT / "src" / "soundwrapper.c",
            "soundwrapper.o",
            defines,
        )
        self._compile_ok(
            FIXTURES / "soundwrapper_transient_host_stubs.c",
            "sound-stubs.o",
            defines,
        )
        self._compile_ok(
            FIXTURES / "soundwrapper_transient_driver.c",
            "sound-driver.o",
            defines,
        )
        result = _link(
            self.work,
            ("soundwrapper.o", "sound-stubs.o", "sound-driver.o"),
            "sound-host",
        )
        self.assertEqual(
            result.returncode,
            0,
            f"linking transient sound test failed:\n{result.stdout}{result.stderr}",
        )
        result = subprocess.run(
            [str(self.work / "sound-host")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"transient sound test failed:\n{result.stdout}{result.stderr}",
        )
        self.assertIn("SOUND_TRANSIENT_HOST_TEST: PASS", result.stdout)

    def test_release_objects_omit_action_submenu_and_preview_owner(self):
        self._compile_ok(
            ROOT / "src" / "debugtools_music.c",
            "music-disabled.o",
            ["FE8_EXPANSION_DEBUGTOOLS_ENABLED=0"],
        )
        self._compile_ok(
            ROOT / "src" / "expansion_bgm.c",
            "bgm-disabled.o",
            ["FE8_EXPANSION_DEBUGTOOLS_ENABLED=0"],
        )
        self._compile_ok(
            ROOT / "src" / "soundwrapper.c",
            "sound-disabled.o",
            ["FE8_EXPANSION_DEBUGTOOLS_ENABLED=0"],
        )

        music_symbols = _defined_symbols(self.work / "music-disabled.o")
        bgm_symbols = _defined_symbols(self.work / "bgm-disabled.o")
        sound_symbols = _defined_symbols(self.work / "sound-disabled.o")
        self.assertNotIn("gDebugToolsMusicPreviewMenuDef", music_symbols)
        self.assertFalse(
            any(symbol.startswith("DebugToolsMusic_") for symbol in music_symbols)
        )
        for symbol in (
            "ExpansionBgm_AcquirePreview",
            "ExpansionBgm_PreviewSong",
            "ExpansionBgm_ReleasePreview",
            "ExpansionBgm_GetPreviewOwner",
            "ExpansionBgm_GetCurrentContext",
        ):
            self.assertNotIn(symbol, bgm_symbols)
        for symbol in (
            "Sound_CaptureBgmContext",
            "Sound_StartTransientBgm",
            "Sound_RestoreBgmContext",
        ):
            self.assertNotIn(symbol, sound_symbols)

    def test_source_contract_uses_authoritative_catalog_and_cleanup_hooks(self):
        music = (ROOT / "src" / "debugtools_music.c").read_text(encoding="utf-8")
        sound = (ROOT / "src" / "soundwrapper.c").read_text(encoding="utf-8")
        room = (ROOT / "src" / "soundroom.c").read_text(encoding="utf-8")
        gamecontrol = (ROOT / "src" / "gamecontrol.c").read_text(encoding="utf-8")
        launcher = (ROOT / "src" / "debugtools_launcher.c").read_text(
            encoding="utf-8"
        )
        registry = (ROOT / "src" / "debugtools_registry.c").read_text(
            encoding="utf-8"
        )

        self.assertIn("gSoundRoomTable", music)
        self.assertIn("IsSoundRoomCatalogEntryValid", music)
        self.assertIn("ent->nameTextId >= MSG_COUNT", room)
        self.assertNotIn("DebugMenu_BgmDraw", music)
        self.assertNotIn("DebugMenu_BgmIdle", music)
        self.assertNotIn("UnlockSoundRoomSong", music)
        self.assertIn("PlaySongCore(songId, player, FALSE);", sound)
        self.assertIn("PlaySongCore(context->state.songId, player, FALSE);", sound)
        self.assertGreaterEqual(
            gamecontrol.count("DebugTools_ForceSessionCleanup();"),
            2,
        )
        self.assertIn("DebugTools_ForceSessionCleanup();", launcher)
        self.assertIn("DebugTools_CleanupMusicPreview();", registry)
        self.assertIn("void DebugTools_ForceSessionCleanup(void)", registry)


class DebugToolsMusicScenarioTests(unittest.TestCase):
    def _scenario(self, name):
        path = ROOT / "tools" / "gba-playtest" / "scenarios" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        gba_playtest.parse_scenario_data(data)
        return data

    def test_title_debug_and_release_scenarios_share_exact_input(self):
        debug = self._scenario("debugtools-music-title-modern-debug.json")
        release = self._scenario("debugtools-music-title-modern-release.json")
        self.assertEqual(debug["frames"], release["frames"])

        names = {checkpoint["name"] for checkpoint in debug["checkpoints"]}
        self.assertEqual(
            names,
            {
                "music-title-hub-open",
                "music-title-owner-acquired",
                "music-title-first-preview",
                "music-title-boundary-preview",
                "music-title-rapid-replacement",
                "music-title-restored",
                "music-title-session-closed",
            },
        )
        self.assertTrue(
            all(not checkpoint["framebuffer"] for checkpoint in debug["checkpoints"])
        )
        self.assertTrue(
            all(not checkpoint["framebuffer"] for checkpoint in release["checkpoints"])
        )

    def test_live_map_scenario_covers_preview_restore_and_interactivity(self):
        data = self._scenario("debugtools-map-hub-modern-debug.json")
        checkpoints = {
            checkpoint["name"]: checkpoint for checkpoint in data["checkpoints"]
        }
        for name in (
            "music-map-prior-context",
            "music-map-owner-acquired",
            "music-map-first-preview",
            "music-map-boundary-preview",
            "music-map-rapid-replacement",
            "music-map-restored",
            "music-map-hub-closed",
            "music-map-remains-interactive",
        ):
            self.assertIn(name, checkpoints)
            self.assertFalse(checkpoints[name]["framebuffer"])

        self.assertTrue(checkpoints["music-map-prior-context"]["sram_hash"])
        self.assertTrue(checkpoints["music-map-restored"]["sram_hash"])

    def test_canonical_case_registry_maps_host_and_rom_automation(self):
        registry = json.loads(
            (ROOT / "docs" / "test-cases" / "registry.json").read_text(
                encoding="utf-8"
            )
        )
        cases = {case["id"]: case for case in registry["cases"]}
        case = cases["TC-DEBUGTOOLS-PROTOTYPE-004"]
        commands = {entry["command"] for entry in case["automation"]}
        self.assertIn(
            "python3 -m unittest tools.gba-playtest.tests.test_debugtools_music -v",
            commands,
        )
        self.assertIn(
            "python3 -m unittest tools.gba-playtest.tests.test_debugtools_registry.DebugToolsRegistryHostTests.test_builtin_identity_and_text_allocator_lifecycle -v",
            commands,
        )
        self.assertIn(
            "make expansion-modern-debugtools-music-check MODERN_CONFIG=debug MODERN_ABI=aapcs",
            commands,
        )
        self.assertIn(
            "make expansion-modern-debugtools-music-check MODERN_CONFIG=release MODERN_ABI=aapcs",
            commands,
        )

    def test_committed_fingerprints_restore_exact_context_and_sram(self):
        def load(name):
            return json.loads(
                (
                    ROOT
                    / "tools"
                    / "gba-playtest"
                    / "fingerprints"
                    / name
                ).read_text(encoding="utf-8")
            )

        def checkpoints(data):
            return {entry["name"]: entry for entry in data["checkpoints"]}

        def sound_values(checkpoint):
            return {
                probe["address"]: probe["value"]
                for probe in checkpoint["probes"]
                if probe["address"].startswith("gSoundSt+")
            }

        title = checkpoints(load("debugtools-music-title-modern-debug.json"))
        self.assertEqual(
            title["music-title-hub-open"]["sram_hash"],
            title["music-title-restored"]["sram_hash"],
        )
        self.assertEqual(
            title["music-title-hub-open"]["probes"][-2:],
            [
                {
                    "address": "gSoundSt+0x04",
                    "size": 2,
                    "value": "0x0043",
                },
                {
                    "address": "gSoundSt+0x06",
                    "size": 1,
                    "value": "0x01",
                },
            ],
        )

        live_map = checkpoints(load("debugtools-map-hub-modern-debug.json"))
        self.assertEqual(
            sound_values(live_map["music-map-prior-context"]),
            sound_values(live_map["music-map-restored"]),
        )
        self.assertEqual(
            live_map["music-map-prior-context"]["sram_hash"],
            live_map["music-map-restored"]["sram_hash"],
        )

        release = load("debugtools-music-title-modern-release.json")
        for checkpoint in release["checkpoints"]:
            self.assertTrue(
                all(
                    probe["value"] == "0x00000000"
                    for probe in checkpoint["probes"]
                )
            )


if __name__ == "__main__":
    unittest.main()
