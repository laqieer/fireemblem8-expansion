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
            source_list = next(
                line
                for line in text.splitlines()
                if line.startswith("MODERN_ELF_EXTRA_ASM_SOURCES :=")
            )
            with self.subTest(enabled=enabled):
                self.assertEqual("src/m4a_hq_mixer.s" in source_list, bool(enabled))


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

    def test_disabled_hq_symbols_must_be_absent(self) -> None:
        with mock.patch.object(
            hq,
            "resolve_elf_symbol",
            side_effect=hq.ProbeBindingError("symbol missing"),
        ):
            hq.require_absent_symbol(Path("disabled.elf"), "SoundMainRAM_End", "nm")

        with mock.patch.object(hq, "resolve_elf_symbol", return_value=(0, 0)):
            with self.assertRaisesRegex(RuntimeError, "must be absent"):
                hq.require_absent_symbol(Path("disabled.elf"), "SoundMainRAM_End", "nm")

        with mock.patch.object(hq, "resolve_elf_symbol", return_value=(0x08000000, 4)):
            with self.assertRaisesRegex(RuntimeError, "must be absent"):
                hq.require_absent_symbol(Path("disabled.elf"), "SoundMainRAM_End", "nm")


class HqMixerQualityFixtureTests(unittest.TestCase):
    def test_final_quantization_has_lower_rms_error(self) -> None:
        stock_rms, hq_rms = hq.quantization_rms()
        self.assertGreater(stock_rms, 0)
        self.assertLess(hq_rms, stock_rms)


if __name__ == "__main__":
    unittest.main()
