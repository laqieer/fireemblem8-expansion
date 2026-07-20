"""Unit tests for linker_report.overlay_audit."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import overlay_audit


FIXTURES = Path(__file__).parent / "fixtures"
OVERLAY_AUDIT_PY = Path(__file__).parent.parent / "overlay_audit.py"
SCRATCH_DIR = Path(__file__).parent / ".scratch"
SCRATCH_DIR.mkdir(exist_ok=True)
REAL_MODERN_MAP = Path(
    "/home/laqieer/fireemblem8-expansion/build/expansion-modern/debug/aapcs/fireemblem8.map"
)


def run_overlay_audit(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(OVERLAY_AUDIT_PY), *args],
        capture_output=True,
        text=True,
    )


class TestOverlayAudit(unittest.TestCase):
    def make_output_path(self, suffix: str) -> Path:
        path = SCRATCH_DIR / f"{self._testMethodName}-{suffix}.json"
        path.unlink(missing_ok=True)
        self.addCleanup(lambda path=path: path.unlink(missing_ok=True))
        return path

    def make_dummy_elf(self, suffix: str) -> Path:
        path = SCRATCH_DIR / f"{self._testMethodName}-{suffix}.elf"
        path.write_text("")
        self.addCleanup(lambda path=path: path.unlink(missing_ok=True))
        return path

    def test_bounds_ok(self):
        out = self.make_output_path("ok")
        result = run_overlay_audit(
            "--map", str(FIXTURES / "overlay_bounds_ok.map"),
            "--output", str(out),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(out.read_text())
        self.assertFalse(report["overflow"])
        self.assertEqual(report["peak_overlay"]["name"], "ewram_overlay_banim")
        by_name = {entry["name"]: entry for entry in report["overlays"]}
        self.assertTrue(by_name["ewram_overlay_banim"]["fits_within_ewram"])
        self.assertEqual(
            report["cross_overlay_relocations"]["reason"],
            "no ELF provided",
        )

    def test_bounds_exceeded(self):
        out = self.make_output_path("overflow")
        result = run_overlay_audit(
            "--map", str(FIXTURES / "overlay_bounds_exceeded.map"),
            "--output", str(out),
        )
        self.assertEqual(result.returncode, 1)
        report = json.loads(out.read_text())
        self.assertTrue(report["overflow"])
        self.assertEqual(report["peak_overlay"]["name"], "ewram_overlay_peak")
        peak_entry = next(
            entry for entry in report["overlays"] if entry["name"] == "ewram_overlay_peak"
        )
        self.assertFalse(peak_entry["fits_within_ewram"])

    def test_cross_overlay_detection_with_synthetic_tools(self):
        map_text = """
Memory Configuration

Name             Origin             Length             Attributes
rom              0x08000000         0x02000000
iwram            0x03000000         0x00008000
ewram            0x02000000         0x00040000
*default*        0x00000000         0xffffffff

Linker script and memory map

ewram_overlay_alpha
                0x02000000       0x40
 *(ewram_overlay_alpha)
 ewram_overlay_alpha
                0x02000000       0x40 build/test/alpha.o
                0x02000000                alphaSymbol

ewram_overlay_beta
                0x02000000       0x80
 *(ewram_overlay_beta)
 ewram_overlay_beta
                0x02000000       0x80 build/test/beta.o
                0x02000000                betaSymbol
"""
        _regions, overlays, symbol_to_overlays = overlay_audit.parse_overlay_map(map_text)
        dummy_elf = self.make_dummy_elf("synthetic")

        def fake_run(args, capture_output, text, timeout):
            if args[0] == "arm-none-eabi-readelf":
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=(
                        "Relocation section '.rel.ewram_overlay_alpha' at offset 0x0 contains 1 entry:\n"
                        " Offset     Info    Type            Sym.Value  Sym. Name\n"
                        "00000004  00000002 R_ARM_ABS32      02000000   betaSymbol\n"
                    ),
                    stderr="",
                )
            if args[0] == "arm-none-eabi-nm":
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="02000000 B alphaSymbol\n02000000 B betaSymbol\n",
                    stderr="",
                )
            raise AssertionError(f"unexpected tool: {args[0]}")

        with mock.patch("overlay_audit.subprocess.run", side_effect=fake_run):
            report = overlay_audit.scan_cross_overlay_relocations(
                str(dummy_elf), overlays, symbol_to_overlays
            )

        self.assertEqual(report["status"], "found")
        self.assertEqual(report["count"], 1)
        self.assertEqual(report["references"][0]["origin_overlay"], "ewram_overlay_alpha")
        self.assertEqual(report["references"][0]["target_overlay"], "ewram_overlay_beta")
        self.assertEqual(report["references"][0]["symbol"], "betaSymbol")

    def test_graceful_degradation_when_readelf_unavailable(self):
        out = self.make_output_path("graceful")
        dummy_elf = self.make_dummy_elf("graceful")

        def fake_run(args, capture_output, text, timeout):
            if args[0] == "arm-none-eabi-readelf":
                raise FileNotFoundError(args[0])
            raise AssertionError(f"unexpected tool: {args[0]}")

        with mock.patch("overlay_audit.subprocess.run", side_effect=fake_run):
            return_code = overlay_audit.main([
                "--map", str(FIXTURES / "overlay_bounds_ok.map"),
                "--elf", str(dummy_elf),
                "--output", str(out),
            ])

        self.assertEqual(return_code, 0)
        report = json.loads(out.read_text())
        reloc_report = report["cross_overlay_relocations"]
        self.assertEqual(reloc_report["status"], "skipped")
        self.assertEqual(reloc_report["reason"], "arm-none-eabi-readelf unavailable")

    def test_no_relocations_in_elf_is_skipped(self):
        dummy_elf = self.make_dummy_elf("noreloc")

        def fake_run(args, capture_output, text, timeout):
            if args[0] == "arm-none-eabi-readelf":
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout="There are no relocations in this file.\n",
                    stderr="",
                )
            raise AssertionError(f"unexpected tool: {args[0]}")

        with mock.patch("overlay_audit.subprocess.run", side_effect=fake_run):
            report = overlay_audit.scan_cross_overlay_relocations(
                str(dummy_elf), [], {}
            )

        self.assertEqual(report["status"], "skipped")
        self.assertEqual(report["reason"], "no relocations in ELF")


    @unittest.skipUnless(REAL_MODERN_MAP.exists(), "modern map artifact not available")
    def test_real_modern_map_bounds(self):
        out = self.make_output_path("real-modern")
        result = run_overlay_audit(
            "--map", str(REAL_MODERN_MAP),
            "--output", str(out),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(out.read_text())
        self.assertEqual(len(report["overlays"]), 8)
        self.assertFalse(report["overflow"])


if __name__ == "__main__":
    unittest.main()
