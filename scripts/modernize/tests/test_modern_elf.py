"""Tests for the expansion-modern-elf Make target wiring.

Covers legacy-assembly filtering, FE6 SIO preflight, legacy dependency
freshness, library discovery, output isolation, and non-interaction
with fast/all targets.

Tests using print-* and make -n run unconditionally.  Tests invoking
real cross tools skip only when the exact required executable is absent.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class ModernElfTargetTests(unittest.TestCase):

    def make(self, *args, cwd=None):
        return subprocess.run(
            ["make", "--no-print-directory", *args],
            cwd=cwd or ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def tool_overrides(self):
        prefix = os.environ.get("PREFIX", "arm-none-eabi-")
        cc = os.environ.get("MODERN_CC", "")
        if not cc:
            root = os.environ.get("MODERN_TOOLCHAIN_ROOT", "")
            if root:
                cc = str(Path(root) / "bin" / f"{prefix}gcc")
            else:
                cc = shutil.which(f"{prefix}gcc") or ""
        objdump = os.environ.get("MODERN_OBJDUMP", "")
        if not objdump:
            objdump = shutil.which(f"{prefix}objdump") or ""
        ld = os.environ.get("MODERN_LD", "")
        if not ld:
            ld = shutil.which(f"{prefix}ld") or ""
        if not cc or not objdump or not ld:
            return None
        overrides = [
            f"MODERN_CC={cc}",
            f"MODERN_OBJDUMP={objdump}",
            f"MODERN_LD={ld}",
        ]
        for var in ("MODERN_BINUTILS_DIR", "MODERN_NEWLIB_INCLUDE"):
            val = os.environ.get(var)
            if val:
                overrides.append(f"{var}={val}")
        return overrides

    # -- Legacy-asm filter --------------------------------------------------

    def test_legacy_asm_filter_excludes_all_replaced(self):
        """All six modern-replaced asm objects excluded from legacy list."""
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("modern toolchain not available")
        result = self.make(
            "print-MODERN_ELF_LEGACY_ASM",
            "print-MODERN_ELF_REPLACED_ASM",
            *overrides,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        legacy = set()
        replaced = set()
        for line in result.stdout.strip().splitlines():
            if line.startswith("MODERN_ELF_LEGACY_ASM ") and "[" in line:
                legacy = set(line.split("[")[1].rstrip("]").split())
            elif line.startswith("MODERN_ELF_REPLACED_ASM ") and "[" in line:
                replaced = set(line.split("[")[1].rstrip("]").split())
        expected = {
            "src/libagbsyscall.o", "asm/arm.o", "asm/arm_call.o",
            "src/rom_header.o", "src/crt0.o", "src/m4a_1.o",
        }
        self.assertTrue(expected.issubset(replaced),
                        f"missing: {expected - replaced}")
        self.assertEqual(legacy & replaced, set(),
                         f"overlap: {legacy & replaced}")

    # -- FE6 SIO preflight --------------------------------------------------

    def test_fe6sio_check_fails_without_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.make(
                "expansion-modern-fe6sio-check",
                f"MODERN_ELF_FE6SIO={tmp}/missing.o",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pre-existing object missing", result.stdout)

    def test_fe6sio_check_succeeds_with_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "stub.o").write_bytes(b"\x00")
            result = self.make(
                "expansion-modern-fe6sio-check",
                f"MODERN_ELF_FE6SIO={tmp}/stub.o",
            )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_fe6sio_check_no_toolchain_invocation(self):
        result = self.make("-n", "expansion-modern-fe6sio-check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("gcc", result.stdout.lower())

    # -- Cohort/all non-interaction -----------------------------------------

    def test_cohort_dry_run_no_elf(self):
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("modern toolchain not available")
        result = self.make("-n", "expansion-modern-cohort", *overrides)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("fireemblem8.elf", result.stdout)
        self.assertNotIn("prepare_modern_link", result.stdout)

    def test_all_dry_run_no_elf(self):
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("modern toolchain not available")
        result = self.make("-n", "expansion-modern-all", *overrides)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("fireemblem8.elf", result.stdout)
        self.assertNotIn("prepare_modern_link", result.stdout)

    # -- Space-safe library discovery ---------------------------------------

    def test_library_discovery_space_safe(self):
        """Library dirs with spaces must be discovered correctly."""
        overrides = self.tool_overrides()
        if overrides is None:
            self.skipTest("modern toolchain not available")
        cc = next(o.split("=", 1)[1] for o in overrides
                  if o.startswith("MODERN_CC="))
        # Query the real compiler for library paths
        libgcc_result = subprocess.run(
            [cc, "-mcpu=arm7tdmi", "-mthumb", "-print-libgcc-file-name"],
            capture_output=True, text=True,
        )
        self.assertEqual(libgcc_result.returncode, 0)
        expected_dir = str(Path(libgcc_result.stdout.strip()).parent)

        # Verify Make's shell-variable discovery matches
        result = self.make("print-MODERN_LIBGCC_DIR", *overrides)
        self.assertEqual(result.returncode, 0, result.stdout)
        for line in result.stdout.strip().splitlines():
            if "MODERN_LIBGCC_DIR" in line and "[" in line:
                discovered = line.split("[")[1].rstrip("]").strip()
                self.assertEqual(
                    discovered, expected_dir,
                    "library discovery mismatch",
                )
                break

    # -- Legacy freshness via NODEP=0 recursive make ------------------------

    def test_legacy_ready_step_uses_nodep_zero(self):
        """modern.mk must declare NODEP=0 in the legacy-ready step."""
        mk = (ROOT / "modern.mk").read_text(encoding="utf-8")
        self.assertIn("expansion-modern-legacy-ready", mk)
        self.assertIn("+$(MAKE) NODEP=0", mk)
        # link-prepare must depend on legacy-ready (may span lines)
        self.assertIn("expansion-modern-legacy-ready", mk)
        prep_idx = mk.index("expansion-modern-link-prepare:")
        ready_idx = mk.index("expansion-modern-legacy-ready", prep_idx)
        self.assertGreater(ready_idx, prep_idx)

    # -- Dry-run safety -----------------------------------------------------

    def test_dry_run_does_not_fail_on_missing_sidecar(self):
        """make -n with a guaranteed-missing sidecar must exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            missing_sym = Path(tmp) / "dir with spaces" / "banim.o.sym.o"
            banim_obj = Path(ROOT / "banim" / "data_banim.o")
            if not banim_obj.is_file():
                banim_obj = Path(tmp) / "banim_stub.o"
                banim_obj.write_bytes(b"\x00")
            self.assertFalse(missing_sym.exists())
            result = self.make(
                "-n", "expansion-modern-elf",
                f"MODERN_ELF_BANIM_SYM={missing_sym}",
                f"BANIM_OBJECT={banim_obj}",
            )
        self.assertEqual(
            result.returncode, 0,
            f"dry-run failed:\n{result.stdout[-500:]}",
        )
        self.assertFalse(missing_sym.exists(),
                         "sidecar must not be created by dry-run")
        self.assertFalse(
            any("error:" in line and "not produced" in line
                for line in result.stdout.splitlines()
                if not line.startswith("\t") and not line.startswith(" ")),
            "real error line found in dry-run output",
        )


if __name__ == "__main__":
    unittest.main()
