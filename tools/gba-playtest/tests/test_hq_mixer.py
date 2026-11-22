"""Configuration and compiled-artifact checks for issue #83's HQ mixer."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PLAYTEST_DIR = ROOT / "tools" / "gba-playtest"
sys.path.insert(0, str(PLAYTEST_DIR))

import run_hq_mixer_checks as hq

sys.path.insert(0, str(ROOT / "scripts" / "modernize"))
import expansion_config as ec


ARM_GCC = shutil.which("arm-none-eabi-gcc")
ARM_NM = shutil.which("arm-none-eabi-nm")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def build_scratch_dir() -> Path:
    path = ROOT / "build"
    path.mkdir(parents=True, exist_ok=True)
    return path


class HqMixerConfigurationTests(unittest.TestCase):
    def test_flag_changes_identity_not_save_epoch(self) -> None:
        disabled = ec.load_identity(
            ROOT / "config.mk", "release", "aapcs", "16M", repo_root=ROOT, hq_mixer=0
        )
        enabled = ec.load_identity(
            ROOT / "config.mk", "release", "aapcs", "16M", repo_root=ROOT, hq_mixer=1
        )
        self.assertEqual(disabled.hq_mixer, 0)
        self.assertEqual(enabled.hq_mixer, 1)
        self.assertNotEqual(disabled.config_fingerprint, enabled.config_fingerprint)
        self.assertEqual(disabled.save_compat_epoch, enabled.save_compat_epoch)
        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                ROOT / "config.mk", "release", "aapcs", "16M", repo_root=ROOT, hq_mixer=2
            )
        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                ROOT / "config.mk",
                "release",
                "aapcs",
                "32M",
                repo_root=ROOT,
                hq_mixer=1,
                enabled_locales="en,ja",
            )

    def test_configure_persists_enabled_hq_mixer(self) -> None:
        with tempfile.TemporaryDirectory(dir=build_scratch_dir()) as temporary:
            fragment = Path(temporary) / "config.autotools.mk"
            result = subprocess.run(
                [str(ROOT / "configure"), "--enable-hq-mixer"],
                cwd=temporary,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(
                "EXPANSION_HQ_MIXER := 1",
                fragment.read_text(encoding="utf-8"),
            )

    def test_archival_lane_rejects_enabled_mixer_before_building(self) -> None:
        result = run(
            [
                "make",
                "-n",
                "legacy",
                "MAKE=:",
                "EXPANSION_HQ_MIXER=1",
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported by the archival lane", result.stdout + result.stderr)

    def test_archival_lane_defaults_hq_mixer_off_before_modern_config_load(self) -> None:
        result = run(
            [
                "make",
                "-n",
                "legacy",
                "MAKE=:",
            ]
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("unsupported by the archival lane", result.stdout + result.stderr)

    def test_resolved_modern_link_inputs_only_include_hq_source_when_enabled(self) -> None:
        for enabled in (0, 1):
            result = run(
                [
                    "make",
                    "-rR",
                    "-pn",
                    "__hq_mixer_make_database_probe__",
                    f"EXPANSION_HQ_MIXER={enabled}",
                ]
            )
            text = result.stdout + result.stderr
            source_lists = [
                line
                for line in text.splitlines()
                if line.startswith("MODERN_ELF_EXTRA_ASM_SOURCES :=")
            ]
            self.assertEqual(
                len(source_lists),
                1,
                "make -rR -pn output must contain exactly one "
                f"MODERN_ELF_EXTRA_ASM_SOURCES assignment (enabled={enabled}):\n{text}",
            )
            source_list = source_lists[0]
            with self.subTest(enabled=enabled):
                self.assertEqual("src/m4a_hq_mixer.s" in source_list, bool(enabled))

    def test_listening_roms_are_isolated_and_non_instrumented(self) -> None:
        outputs = []
        for target in (
            "expansion-modern-hq-mixer-listening-enabled-rom",
            "expansion-modern-hq-mixer-listening-control-rom",
        ):
            result = run(
                [
                    "make",
                    "-n",
                    target,
                    "MAKE=:",
                    "MODERN_CONFIG=release",
                    "MODERN_ABI=aapcs",
                ]
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            outputs.append(result.stdout + result.stderr)
        text = "\n".join(outputs)
        self.assertIn(
            "MODERN_BUILD_ROOT=build/expansion-modern-hq-mixer-listening/enabled",
            text,
        )
        self.assertIn(
            "MODERN_BUILD_ROOT=build/expansion-modern-hq-mixer-listening/stock-control",
            text,
        )
        self.assertIn("EXPANSION_HQ_MIXER=1", text)
        self.assertIn("EXPANSION_HQ_MIXER=0", text)
        self.assertNotIn("FE8_HQ_MIXER_TEST_FIXTURE", text)

    def test_documented_budget_matches_enforced_hq_contract(self) -> None:
        audio_doc = (ROOT / "docs" / "audio.md").read_text(encoding="utf-8")
        for value in (
            f"0x{hq.HQ_EWRAM_BOOKKEEPING_BYTES:X}",
            f"0x{hq.HQ_DISABLED_IWRAM_STATIC_END:08X}",
            f"0x{hq.HQ_ENABLED_IWRAM_STATIC_END:08X}",
            f"0x{hq.HQ_IWRAM_STATIC_DELTA:X}",
        ):
            self.assertIn(value, audio_doc)
        self.assertIn(
            "build/expansion-modern-hq-mixer-listening/enabled",
            audio_doc,
        )
        self.assertIn(
            "build/expansion-modern-hq-mixer-listening/stock-control",
            audio_doc,
        )


class HqMixerSymbolValidationTests(unittest.TestCase):
    def test_disabled_hq_symbols_must_be_absent(self) -> None:
        for record in (
            "00000000 T SoundMainRAM_End",
            "08000000 T SoundMainRAM_End",
            "00000000 00000000 T SoundMainRAM_End",
            "08000000 00000004 T SoundMainRAM_End",
        ):
            result = subprocess.CompletedProcess(["nm"], 0, record + "\n", "")
            with self.subTest(record=record), mock.patch(
                "probe_bindings.subprocess.run", return_value=result
            ):
                hq.require_absent_symbol(Path("fixture.o"), "Missing", "nm")
                with self.assertRaisesRegex(RuntimeError, "must be absent"):
                    hq.require_absent_symbol(Path("fixture.o"), "SoundMainRAM_End", "nm")

    def test_symbol_tool_errors_are_not_absence(self) -> None:
        failed = subprocess.CompletedProcess(["nm"], 3, "", "nm failed")
        for result in (
            failed,
            FileNotFoundError("nm is unavailable"),
            subprocess.TimeoutExpired("nm", 300),
        ):
            patch_args = (
                {"side_effect": result}
                if isinstance(result, Exception)
                else {"return_value": result}
            )
            with self.subTest(result=result), mock.patch(
                "probe_bindings.subprocess.run", **patch_args
            ):
                with self.assertRaises(hq.ProbeBindingError):
                    hq.require_absent_symbol(Path("fixture.o"), "SoundMainRAM_End", "nm")

    def test_malformed_symbol_table_is_not_absence(self) -> None:
        for table in (
            "not a symbol table\n",
            "00000000 ! Other\n",
        ):
            result = subprocess.CompletedProcess(["nm"], 0, table, "")
            with self.subTest(table=table), mock.patch(
                "probe_bindings.subprocess.run", return_value=result
            ):
                with self.assertRaisesRegex(hq.ProbeBindingError, "unrecognized nm symbol table"):
                    hq.require_absent_symbol(Path("fixture.o"), "Missing", "nm")

        result = subprocess.CompletedProcess(
            ["nm"], 0, "00000000 T SoundMainRAM_End\nnot a symbol table\n", ""
        )
        with mock.patch("probe_bindings.subprocess.run", return_value=result):
            with self.assertRaisesRegex(hq.ProbeBindingError, "unrecognized nm symbol table"):
                hq.find_elf_symbol(Path("fixture.o"), "SoundMainRAM_End", "nm")

    def test_real_host_elf_symbols_and_failed_tools(self) -> None:
        compiler = shutil.which("cc")
        nm = shutil.which("nm")
        if compiler is None or nm is None:
            self.skipTest("host compiler and nm unavailable")
        with tempfile.TemporaryDirectory(dir=build_scratch_dir()) as temporary:
            work = Path(temporary)
            probe = work / "probe.o"
            result = subprocess.run(
                [compiler, "-x", "c", "-c", "-o", str(probe), "-"],
                cwd=ROOT,
                input="int hq_symbol_probe;\n",
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if probe.read_bytes()[:4] != b"\x7fELF":
                self.skipTest("host compiler does not emit ELF objects")
            result = subprocess.run(
                [nm, "-S", "--defined-only", str(probe)],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                self.skipTest("host nm lacks GNU-compatible ELF options")
            self.assertIsNotNone(hq.find_elf_symbol(probe, "hq_symbol_probe", nm))

            object_file = work / "symbols.o"
            result = subprocess.run(
                [compiler, "-x", "assembler", "-c", "-o", str(object_file), "-"],
                cwd=ROOT,
                input=(
                    ".text\n"
                    ".globl SoundMainRAM_End\n"
                    "SoundMainRAM_End:\n"
                    ".byte 0\n"
                    ".globl SoundMainRAM_MixBuffer\n"
                    "SoundMainRAM_MixBuffer:\n"
                    ".byte 0\n"
                    ".size SoundMainRAM_MixBuffer, . - SoundMainRAM_MixBuffer\n"
                ),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            hq.require_absent_symbol(object_file, "gExpansionHqMixerProbe", nm)
            for symbol in ("SoundMainRAM_End", "SoundMainRAM_MixBuffer"):
                with self.subTest(symbol=symbol):
                    with self.assertRaisesRegex(RuntimeError, "must be absent"):
                        hq.require_absent_symbol(object_file, symbol, nm)
            failed_nm = work / "failed-nm"
            failed_nm.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
            failed_nm.chmod(0o755)
            for tool in (work / "missing-nm", failed_nm):
                with self.subTest(tool=tool):
                    with self.assertRaises(hq.ProbeBindingError):
                        hq.require_absent_symbol(object_file, "SoundMainRAM_End", str(tool))


class HqMixerCompiledArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        if ARM_GCC is None or ARM_NM is None:
            self.skipTest("arm-none-eabi GCC/binutils unavailable")

    def test_dma_disabled_mixer_and_stock_replacement_are_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory(dir=build_scratch_dir()) as temporary:
            work = Path(temporary)
            stock = work / "m4a-stock.o"
            hq_stock = work / "m4a-hq-stock.o"
            hq_object = work / "m4a-hq.o"
            common = [
                ARM_GCC,
                "-mcpu=arm7tdmi",
                "-mthumb",
                "-mthumb-interwork",
                "-Iinclude",
                "-I.",
            ]
            for output, source, enabled in (
                (stock, ROOT / "src/m4a_1.s", 0),
                (hq_stock, ROOT / "src/m4a_1.s", 1),
                (hq_object, ROOT / "src/m4a_hq_mixer.s", 1),
            ):
                result = run(
                    [
                        *common,
                        f"-Wa,--defsym,FE8_EXPANSION_HQ_MIXER={enabled}",
                        "-c",
                        str(source),
                        "-o",
                        str(output),
                    ]
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            stock_symbols = run([ARM_NM, "-S", str(stock)]).stdout
            hq_stock_symbols = run([ARM_NM, "-S", str(hq_stock)]).stdout
            hq_symbols = run([ARM_NM, "-S", str(hq_object)]).stdout

            self.assertIn(" SoundMainRAM", stock_symbols)
            self.assertNotIn(" T SoundMainRAM", hq_stock_symbols)
            self.assertIn(" T SoundMainRAM", hq_symbols)
            self.assertIn(" T SoundMainRAM_End", hq_symbols)
            self.assertIn(" U SoundMainRAM_MixBuffer", hq_symbols)
            self.assertNotIn("MixerBuffer", hq_symbols)

            resolver = hq.ElfSymbolResolver(hq_object, ARM_NM)
            mixer, mixer_size = resolver("SoundMainRAM")
            mixer_end, _ = resolver("SoundMainRAM_End")
            self.assertEqual(mixer_size, hq.HQ_NO_REVERB_CODE_BYTES)
            self.assertEqual(mixer_end - mixer, hq.HQ_NO_REVERB_CODE_BYTES)


class HqMixerQualityFixtureTests(unittest.TestCase):
    def test_pre_fix_player_alias_collision_is_rejected(self) -> None:
        players = {
            symbol: (0x03006A80, hq.MPLAY_INFO_BYTES)
            for symbol in hq.MPLAY_INFO_SYMBOLS
        }
        with self.assertRaisesRegex(RuntimeError, "player-info symbols overlap"):
            hq.validate_player_ranges(
                players,
                (0x03006A80, hq.HQ_MIX_BUFFER_BYTES),
            )

    def test_early_pcm_cannot_hide_a_silent_final_window(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "enabled late PCM window is silent at frame 3290",
        ):
            hq.require_sustained_late_audio(
                [120, 600, 3280, 3290],
                [[1], [2], [3], [0]],
                [[4], [5], [6], [0]],
                [1, 1, 1, 0],
                "enabled",
            )

    def test_early_stereo_cannot_hide_an_identical_late_window(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "enabled late PCM window has identical left/right samples at frame 3280",
        ):
            hq.require_sustained_late_audio(
                [120, 600, 3280, 3290],
                [[1, 2], [3, 4], [5, 6], [7, 8]],
                [[9, 10], [11, 12], [5, 6], [7, 8]],
                [1, 1, 1, 1],
                "enabled",
            )

    def test_final_quantization_has_lower_rms_error(self) -> None:
        stock_rms, hq_rms = hq.quantization_rms()
        self.assertGreater(stock_rms, 0)
        self.assertLess(hq_rms, stock_rms)


if __name__ == "__main__":
    unittest.main()
