"""Tests for modern expansion-linker verification target wiring."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.modernize.tests.make_database import (
    make_database_rule,
    make_database_rule_header,
    make_database_variable,
)


ROOT = Path(__file__).resolve().parents[3]
MODERN_MK = ROOT / "modern.mk"
EXPANSION_LD = ROOT / "linker" / "expansion.ld"


class LinkerCheckTargetTests(unittest.TestCase):
    PROFILE_SCRATCH = ROOT / "build" / "test-scratch" / "locale-profile-dry-run"

    def tearDown(self):
        shutil.rmtree(self.PROFILE_SCRATCH, ignore_errors=True)

    def make(self, *args):
        overrides = []
        toolchain_root = ROOT / "build" / "toolchain-root" / "usr"
        newlib = toolchain_root / "lib" / "arm-none-eabi" / "newlib"
        if (toolchain_root / "bin" / "arm-none-eabi-gcc").is_file():
            overrides.extend([
                f"MODERN_TOOLCHAIN_ROOT={toolchain_root}",
                f"MODERN_NEWLIB_LIB={newlib}",
            ])
        return subprocess.run(
            ["make", "--no-print-directory", *args, *overrides],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def resolved_make_database(self, *config):
        result = self.make(
            "-rR", "-n", "-p", *config, "__issue89_linker_checks_probe__"
        )
        self.assertNotEqual(result.returncode, 0)
        return result.stdout

    def test_all_linker_checks_are_modern_goals(self):
        text = MODERN_MK.read_text(encoding="utf-8")
        goals = text.split("MODERN_GOALS :=", 1)[1].split("ifneq", 1)[0]
        for target in (
            "expansion-modern-title-check",
            "expansion-modern-budget-check",
            "expansion-modern-relocs",
            "expansion-modern-overlay-audit",
            "expansion-modern-shifted-check",
            "expansion-modern-linker-check",
            "expansion-modern-chapter-objectives-check",
        ):
            self.assertIn(target, goals)

    def test_chapter_objectives_runtime_check_requires_aapcs_and_consolidates(self):
        result = self.make(
            "-n", "expansion-modern-chapter-objectives-check", "MODERN_ABI=apcs-gnu"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires MODERN_ABI=aapcs", result.stdout)

    def test_chapter_objectives_runtime_check_runs_debug_and_skips_release(self):
        debug_database = self.resolved_make_database(
            "MODERN_CONFIG=debug",
            "MODERN_ABI=aapcs",
        )
        debug_rule = make_database_rule(
            debug_database, "expansion-modern-chapter-objectives-check"
        )
        self.assertIsNotNone(debug_rule, debug_database[-4000:])
        self.assertIn("$(MODERN_CHAPTER_OBJECTIVES_RUNTIME_SCRIPT)", debug_rule)
        self.assertEqual(
            make_database_variable(debug_database, "MODERN_CHAPTER_OBJECTIVES_RUNTIME_SCRIPT"),
            "tools/gba-playtest/run_chapter_objective_checks.py",
        )

        release_database = self.resolved_make_database(
            "MODERN_CONFIG=release",
            "MODERN_ABI=aapcs",
        )
        release_rule = make_database_rule(
            release_database, "expansion-modern-chapter-objectives-check"
        )
        self.assertIsNotNone(release_rule, release_database[-4000:])
        release_header = make_database_rule_header(
            release_rule, "expansion-modern-chapter-objectives-check"
        )
        self.assertIn("expansion-modern-chapter-objectives-profile-boot-check", release_header)
        self.assertIn("runtime check skipped", release_rule)
        self.assertNotIn("run_chapter_objective_checks.py", release_rule)

    def test_chapter_objectives_profile_routes_inventory_to_its_build_root(self):
        database = self.resolved_make_database(
            "MODERN_CONFIG=debug",
            "MODERN_ABI=aapcs",
        )
        profile_rule = make_database_rule(
            database, "expansion-modern-chapter-objectives-profile-rom"
        )
        self.assertIsNotNone(profile_rule, database[-4000:])
        self.assertIn(
            "GENERATED_DATA_CHAPTEROBJECTIVES_INVENTORY=$(MODERN_CHAPTER_OBJECTIVES_PROFILE_INVENTORY)",
            profile_rule,
        )
        self.assertEqual(
            make_database_variable(database, "MODERN_CHAPTER_OBJECTIVES_PROFILE_INVENTORY"),
            "build/expansion-modern-chapter-objectives/generated_data_chapterobjectives_inventory.md",
        )

    def test_linker_check_dry_run_wires_every_gate(self):
        result = self.make("-n", "expansion-modern-linker-check")
        self.assertEqual(result.returncode, 0, result.stdout[-1000:])
        for expected in (
            "budget.py",
            "modern_emit_relocs.sh",
            "overlay_audit.py",
            "boot.json",
            "title-progression.json",
            "modern_shifted_boot.sh",
            "scan_build_addrs.py",
            "scan_raw_casts.sh",
            "run_chapter_objective_checks.py",
        ):
            self.assertIn(expected, result.stdout)

    def test_debug_linker_aggregate_schedules_accelerated_fidelity(self):
        database = self.make(
            "-pn",
            "expansion-modern-linker-check",
            "MODERN_CONFIG=debug",
            "MAKE=echo",
        )
        self.assertEqual(database.returncode, 0, database.stdout[-2000:])
        header = make_database_rule_header(
            database.stdout,
            "expansion-modern-linker-check",
        )
        self.assertIsNotNone(header)
        self.assertIn(
            "expansion-modern-autoplay-accelerated-fidelity-check",
            header,
        )

        result = self.make(
            "-n",
            "expansion-modern-autoplay-accelerated-fidelity-check",
            "MODERN_CONFIG=debug",
            "MAKE=echo",
        )
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])
        self.assertIn("run_accelerated_fidelity_checks.py", result.stdout)

    def test_release_linker_aggregate_omits_accelerated_runtime_capture(self):
        database = self.make(
            "-pn",
            "expansion-modern-linker-check",
            "MODERN_CONFIG=release",
            "MAKE=echo",
        )
        self.assertEqual(database.returncode, 0, database.stdout[-2000:])
        header = make_database_rule_header(
            database.stdout,
            "expansion-modern-linker-check",
        )
        self.assertIsNotNone(header)
        self.assertNotIn(
            "expansion-modern-autoplay-accelerated-fidelity-check",
            header,
        )

        result = self.make(
            "-n",
            "expansion-modern-linker-check",
            "MODERN_CONFIG=release",
            "MAKE=echo",
        )
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])
        self.assertNotIn("run_accelerated_fidelity_checks.py", result.stdout)

    def test_budget_check_requires_elf_identity_and_user_stack_margin(self):
        result = self.make("-n", "expansion-modern-budget-check")
        self.assertEqual(result.returncode, 0, result.stdout[-1000:])
        self.assertIn("--validate-elf", result.stdout)
        self.assertIn("--require-positive-headroom ewram", result.stdout)
        self.assertIn("--require-positive-headroom iwram", result.stdout)

    def test_linker_reserves_nonzero_user_stack_floor(self):
        text = EXPANSION_LD.read_text(encoding="utf-8")
        self.assertIn("__iwram_static_limit = __sp_usr - 0x1000;", text)
        self.assertIn(
            "__iwram_static_end = ADDR(IWRAM) + SIZEOF(IWRAM);", text
        )
        self.assertIn(
            "ASSERT(__iwram_static_end < __sp_usr,", text
        )
        self.assertIn(
            "ASSERT(__iwram_static_end <= __iwram_static_limit,", text
        )

    def test_nonzero_shift_is_forwarded_to_linker(self):
        result = self.make(
            "-n",
            "expansion-modern-elf",
            "MODERN_TEXT_SHIFT=0x40000",
        )
        self.assertEqual(result.returncode, 0, result.stdout[-1000:])
        self.assertIn("--defsym=__text_shift=0x40000", result.stdout)
        self.assertIn("text_shift=0x40000", result.stdout)

    def test_title_fingerprint_is_configuration_specific(self):
        debug = self.make(
            "print-MODERN_TITLE_FINGERPRINT", "MODERN_CONFIG=debug"
        )
        release = self.make(
            "print-MODERN_TITLE_FINGERPRINT", "MODERN_CONFIG=release"
        )
        self.assertIn("title-progression-modern-debug.json", debug.stdout)
        self.assertIn("title-progression-modern-release.json", release.stdout)

    def test_english_and_pseudo_profiles_dry_run_at_16m(self):
        profiles = (
            ("en", "0"),
            ("en,qps-ploc", "1"),
        )
        for index, (locales, pseudo) in enumerate(profiles):
            with self.subTest(locales=locales):
                result = self.make(
                    "-n",
                    "expansion-modern-rom",
                    "MODERN_ROM_SIZE=16M",
                    f"EXPANSION_ENABLED_LOCALES={locales}",
                    f"EXPANSION_PSEUDO_LOCALE={pseudo}",
                    f"MODERN_BUILD_ROOT={self.PROFILE_SCRATCH / str(index)}",
                )
                self.assertEqual(result.returncode, 0, result.stdout[-2000:])

    def test_real_cjk_profiles_fail_fast_at_16m(self):
        for index, locales in enumerate(
            ("en,ja", "en,zh-Hans", "en,ja,zh-Hans")
        ):
            with self.subTest(locales=locales):
                result = self.make(
                    "-n",
                    "expansion-modern-rom",
                    "MODERN_ROM_SIZE=16M",
                    f"EXPANSION_ENABLED_LOCALES={locales}",
                    f"MODERN_BUILD_ROOT={self.PROFILE_SCRATCH / str(index)}",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("which require MODERN_ROM_SIZE=32M", result.stdout)
                self.assertIn("Use MODERN_ROM_SIZE=32M or remove", result.stdout)

    def test_real_cjk_profiles_dry_run_at_32m(self):
        for index, locales in enumerate(
            ("en,ja", "en,zh-Hans", "zh-Hans,en,ja")
        ):
            with self.subTest(locales=locales):
                result = self.make(
                    "-n",
                    "expansion-modern-rom",
                    "MODERN_ROM_SIZE=32M",
                    f"EXPANSION_ENABLED_LOCALES={locales}",
                    f"MODERN_BUILD_ROOT={self.PROFILE_SCRATCH / str(index)}",
                )
                self.assertEqual(result.returncode, 0, result.stdout[-2000:])

    def test_invalid_default_locale_still_fails_dry_run(self):
        result = self.make(
            "-n",
            "expansion-modern-rom",
            "MODERN_ROM_SIZE=32M",
            "EXPANSION_ENABLED_LOCALES=en",
            "EXPANSION_DEFAULT_LOCALE=qps-ploc",
            f"MODERN_BUILD_ROOT={self.PROFILE_SCRATCH / 'invalid-default'}",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EXPANSION_DEFAULT_LOCALE", result.stdout)


if __name__ == "__main__":
    unittest.main()
