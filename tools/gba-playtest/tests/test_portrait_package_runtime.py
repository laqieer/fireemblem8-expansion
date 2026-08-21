"""Issue #63 live portrait package regression through the normal game route."""

from __future__ import annotations

import os
import sys
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
PORTRAIT_RUNTIME_BUILD_ROOT = Path(
    os.environ.get(
        "FE8_PORTRAIT_PACKAGE_RUNTIME_BUILD_ROOT",
        REPO_ROOT / "build" / "expansion-modern-portrait-package-runtime",
    )
)
sys.path.insert(0, str(PLAYTEST_DIR))
sys.path.insert(0, str(PLAYTEST_DIR / "tests"))

import gba_playtest as gp  # noqa: E402
import host_mode  # noqa: E402
import sram_fixture as sf  # noqa: E402
from probe_bindings import ElfSymbolResolver  # noqa: E402


class PortraitPackageScenarioTests(unittest.TestCase):
    def test_video_and_rom_probe_ranges_are_supported(self):
        for address in (0x05000000, 0x06000000, 0x07000000, 0x08000000):
            with self.subTest(address=hex(address)):
                gp.parse_scenario_data(
                    {
                        "schema_version": 1,
                        "name": "portrait-probe-range",
                        "frames": [],
                        "checkpoints": [{
                            "name": "probe",
                            "frame": 1,
                            "framebuffer": False,
                            "probes": [{"address": f"0x{address:08x}", "size": 4}],
                        }],
                    }
                )

    def test_portrait_probe_is_confined_to_internal_runtime_target(self):
        modern_mk = (REPO_ROOT / "modern.mk").read_text(encoding="utf-8")
        tools_src = (REPO_ROOT / "src" / "debugtools_tools.c").read_text(encoding="utf-8")
        public_header = (REPO_ROOT / "include" / "expansion_debugtools.h").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "expansion-modern-portrait-package-runtime-check:",
            modern_mk,
        )
        self.assertIn(
            'MODERN_INTERNAL_TEST_DEFINES="-DFE8_PORTRAIT_PACKAGE_RUNTIME_TEST=1"',
            modern_mk,
        )
        self.assertIn(
            "#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)",
            tools_src,
        )
        self.assertNotIn("PortraitPackageRuntimeProbe", public_header)
        self.assertNotIn("portraitProbeFaceId", public_header)


@host_mode.live_artifact_testcase("portrait package runtime coverage")
class PortraitPackageRuntimeTests(unittest.TestCase):
    """Exercise the live table and face pipeline without a savestate."""

    @staticmethod
    def _symbol_address(elf: Path, symbol: str) -> int:
        result = subprocess.run(
            ["arm-none-eabi-nm", "-n", "--defined-only", str(elf)],
            check=True,
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[2] == symbol:
                return int(fields[0], 16)
        raise AssertionError(f"{symbol} is missing from {elf}")

    @staticmethod
    def _defined_symbols(elf: Path) -> set[str]:
        result = subprocess.run(
            ["arm-none-eabi-nm", "--defined-only", str(elf)],
            check=True,
            capture_output=True,
            text=True,
        )
        return {
            fields[-1]
            for line in result.stdout.splitlines()
            if (fields := line.split())
        }

    def _scenario(self, elf: Path):
        resolver = ElfSymbolResolver(elf)
        table, _ = resolver("portrait_data")
        probes = (
            gp.Probe("portrait_data+0x1c", table + 0x1C, 4, None),
            gp.Probe("portrait_data+0x20", table + 0x20, 4, None),
            gp.Probe("portrait_data+0x24", table + 0x24, 4, None),
            gp.Probe("portrait_data+0x28", table + 0x28, 4, None),
            gp.Probe("gFaces", self._symbol_address(elf, "gFaces"), 4, None),
            gp.Probe("obj-pal-6", 0x050002C0, 4, None),
            gp.Probe("face-vram-slot-0", 0x06016120, 4, None),
            gp.Probe("face-oam", 0x07000000, 4, None),
        )
        base = gp.load_scenario(PLAYTEST_DIR / "scenarios" / "new-game.json")
        inputs = base.inputs + tuple(
            gp.InputRange(frame, frame + 4, gp.KEY_BITS["A"])
            for frame in range(1800, 20001, 600)
        )
        return gp.Scenario(
            "issue63-eirika-dialogue",
            "Clean New Game reaches the early dialogue route through ordinary input.",
            False,
            None,
            inputs,
            (
                gp.Checkpoint(
                    "eirika-dialogue-live",
                    20200,
                    True,
                    None,
                    False,
                    None,
                    (),
                    probes,
                    (),
                    (),
                ),
            ),
        )

    def _run(self, config: str):
        rom = host_mode.modern_rom(config)
        elf = host_mode.modern_elf(config)
        host_mode.require_built_rom(rom, f"modern {config} ROM")
        host_mode.require_built_rom(elf, f"modern {config} ELF")
        resolver = ElfSymbolResolver(elf)
        scenario = self._scenario(elf)
        fixture = sf.write_deterministic_current_fixture(
            REPO_ROOT / "build" / "gba-playtest-tmp" / f"issue63-{config}.sav"
        )
        actual = host_mode.capture_live_or_skip(
            rom, scenario, fixture, label=f"issue #63 portrait runtime ({config})"
        )
        values = {
            probe["address"]: int(probe["value"], 16)
            for probe in actual["checkpoints"][0]["probes"]
        }
        expected_symbols = (
            "portrait_Eirika_tileset",
            "portrait_Eirika_chibi",
            "portrait_Eirika_palette",
            "portrait_Eirika_mouth",
        )
        for offset, symbol in zip((0x1C, 0x20, 0x24, 0x28), expected_symbols):
            self.assertEqual(
                values[f"portrait_data+0x{offset:x}"],
                resolver(symbol)[0],
                f"GetPortraitData(2) component {symbol} must retain its existing alias",
            )
        self.assertNotEqual(values["gFaces"], 0, "dialogue must own a live face proc")
        self.assertNotEqual(values["obj-pal-6"], 0, "face palette must reach palette RAM")
        self.assertNotEqual(values["face-vram-slot-0"], 0, "face tiles must reach VRAM")
        self.assertNotEqual(values["face-oam"], 0, "face sprites must reach hardware OAM")

    def _minimug_scenario(self, elf: Path):
        base = gp.load_scenario(
            PLAYTEST_DIR / "scenarios" / "debugtools-tools-modern-debug.json"
        )
        frame = 14600
        probe_base = self._symbol_address(elf, "gPortraitPackageRuntimeProbe")
        probes = tuple(
            gp.Probe(name, probe_base + offset, 4, None)
            for name, offset in (
                ("portrait-probe-face-id", 0x00),
                ("portrait-probe-render-count", 0x04),
                ("portrait-probe-vram-word", 0x08),
                ("portrait-probe-palette-word", 0x0C),
                ("portrait-probe-full-face-count", 0x10),
                ("portrait-probe-mouth-display-bits", 0x14),
                ("portrait-probe-eye-control", 0x18),
                ("portrait-probe-face-oam2", 0x1C),
                ("portrait-probe-mouth-frame-0", 0x20),
                ("portrait-probe-mouth-frame-2", 0x24),
            )
        ) + (gp.Probe("eirika-mouth-vram", 0x060163C0, 4, None),)
        checkpoints = tuple(
            gp.Checkpoint(
                f"eirika-minimug-and-face-{checkpoint_frame}",
                checkpoint_frame,
                False,
                None,
                False,
                None,
                (),
                probes,
                (),
                (),
            )
            for checkpoint_frame in (frame, frame + 48, frame + 96)
        )
        return gp.Scenario(
            "issue63-minimug-runtime-test",
            "Use the existing Unit Inspect debug seam to render Eirika's minimug.",
            False,
            None,
            tuple(item for item in base.inputs if item.end <= frame),
            checkpoints,
        )

    def test_debug_unit_inspect_renders_eirika_minimug(self):
        rom = PORTRAIT_RUNTIME_BUILD_ROOT / "debug" / "aapcs" / "fireemblem8.gba"
        elf = PORTRAIT_RUNTIME_BUILD_ROOT / "debug" / "aapcs" / "fireemblem8.elf"
        host_mode.require_built_rom(rom, "portrait runtime test ROM")
        host_mode.require_built_rom(elf, "portrait runtime test ELF")
        fixture = sf.write_deterministic_current_fixture(
            REPO_ROOT / "build" / "gba-playtest-tmp" / "issue63-minimug-runtime-test.sav"
        )
        actual = host_mode.capture_live_or_skip(
            rom,
            self._minimug_scenario(elf),
            fixture,
            label="issue #63 Eirika minimug runtime",
        )
        values = {
            probe["address"]: int(probe["value"], 16)
            for probe in actual["checkpoints"][0]["probes"]
        }
        expected_vram = int.from_bytes(
            (REPO_ROOT / "graphics/portrait/portrait_Eirika_chibi.4bpp").read_bytes()[0x20:0x24],
            "little",
        )
        expected_palette = int.from_bytes(
            (REPO_ROOT / "graphics/portrait/portrait_Eirika_palette.agbpal").read_bytes()[:4],
            "little",
        )
        self.assertEqual(values["portrait-probe-face-id"], 2)
        self.assertEqual(values["portrait-probe-render-count"], 1)
        self.assertEqual(values["portrait-probe-vram-word"], expected_vram)
        self.assertEqual(values["portrait-probe-palette-word"], expected_palette)
        self.assertEqual(values["portrait-probe-full-face-count"], 1)
        self.assertEqual(values["portrait-probe-mouth-display-bits"], 0x10)
        self.assertEqual(values["portrait-probe-eye-control"], 2)
        self.assertNotEqual(values["portrait-probe-mouth-frame-0"], 0)
        self.assertNotEqual(values["portrait-probe-mouth-frame-2"], 0)
        mouth_words = [
            int(checkpoint["probes"][-1]["value"], 16)
            for checkpoint in actual["checkpoints"]
        ]
        self.assertTrue(any(mouth_words), "Eirika mouth tiles must reach VRAM")

    def test_default_profiles_omit_portrait_probe_symbol(self):
        for config in ("debug", "release"):
            with self.subTest(config=config):
                elf = host_mode.modern_elf(config)
                host_mode.require_built_rom(elf, f"modern {config} ELF")
                self.assertNotIn(
                    "gPortraitPackageRuntimeProbe",
                    self._defined_symbols(elf),
                    f"modern {config} must omit portrait runtime test state",
                )

    def test_debug_eirika_dialogue_loads_face_pipeline(self):
        self._run("debug")

    def test_release_eirika_aliases_remain_default_behavior(self):
        self._run("release")


if __name__ == "__main__":
    unittest.main()
