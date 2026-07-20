"""Unit tests for linker_report.budget."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
BUDGET_PY = Path(__file__).parent.parent / "budget.py"
SCRATCH_DIR = Path(__file__).parent / ".scratch"
SCRATCH_DIR.mkdir(exist_ok=True)


def run_budget(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BUDGET_PY), *args],
        capture_output=True, text=True,
    )


class TestMapParsing(unittest.TestCase):
    """Core map-file parsing tests."""

    def make_output_path(self, suffix: str) -> Path:
        path = SCRATCH_DIR / f"{self._testMethodName}-{suffix}.json"
        path.unlink(missing_ok=True)
        self.addCleanup(lambda path=path: path.unlink(missing_ok=True))
        return path

    def test_basic_regions_and_sections(self):
        """Parse a minimal map and check region/section counts."""
        out = self.make_output_path("basic")
        r = run_budget("--map", str(FIXTURES / "basic.map"), "--output", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(out.read_text())
        self.assertEqual(report["schema_version"], 1)
        # 3 non-default regions
        self.assertEqual(len(report["regions"]), 3)
        names = {reg["name"] for reg in report["regions"]}
        self.assertEqual(names, {"rom", "iwram", "ewram"})
        # 3 output sections (ewram_data, IWRAM, ROM)
        self.assertEqual(len(report["sections"]), 3)
        # No overlays in basic
        self.assertEqual(len(report["overlays"]), 0)
        self.assertFalse(report["overflow"])

    def test_overlay_detection(self):
        """Overlays sharing an address are detected with correct peak."""
        out = self.make_output_path("overlay")
        r = run_budget("--map", str(FIXTURES / "overlay.map"), "--output", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(out.read_text())
        overlays = report["overlays"]
        self.assertEqual(len(overlays), 3)
        # All at same address
        for ov in overlays:
            self.assertEqual(ov["address"], 0x02000000)
            self.assertEqual(ov["region"], "ewram")
        # Peak is the largest: ewram_overlay_banim = 0x20188
        for ov in overlays:
            self.assertEqual(ov["peak_bytes"], 0x20188)
        # Individual sizes
        by_name = {ov["name"]: ov for ov in overlays}
        self.assertEqual(by_name["ewram_overlay_0"]["size_bytes"], 0x1f19c)
        self.assertEqual(by_name["ewram_overlay_banim"]["size_bytes"], 0x20188)
        self.assertEqual(by_name["ewram_overlay_sio"]["size_bytes"], 0x1188)

    def test_region_overflow_exits_nonzero(self):
        """EWRAM exceeding capacity triggers exit code 1."""
        out = self.make_output_path("overflow")
        r = run_budget("--map", str(FIXTURES / "overflow.map"), "--output", str(out))
        self.assertEqual(r.returncode, 1)
        report = json.loads(out.read_text())
        self.assertTrue(report["overflow"])
        ewram = next(reg for reg in report["regions"] if reg["name"] == "ewram")
        self.assertTrue(ewram["overflow"])
        self.assertEqual(ewram["occupied_bytes"], 0x50000)

    def test_malformed_map_exits_2(self):
        """A map file with no Memory Configuration returns exit 2."""
        out = self.make_output_path("malformed")
        r = run_budget("--map", str(FIXTURES / "malformed.map"), "--output", str(out))
        self.assertEqual(r.returncode, 2)

    def test_deterministic_output(self):
        """Two runs on the same input produce byte-identical JSON."""
        out1 = self.make_output_path("deterministic-1")
        out2 = self.make_output_path("deterministic-2")
        run_budget("--map", str(FIXTURES / "overlay.map"), "--output", str(out1))
        run_budget("--map", str(FIXTURES / "overlay.map"), "--output", str(out2))
        self.assertEqual(out1.read_text(), out2.read_text())

    def test_check_mode_passes_on_match(self):
        """--check succeeds when report matches."""
        out = self.make_output_path("check-pass")
        run_budget("--map", str(FIXTURES / "basic.map"), "--output", str(out))
        r = run_budget("--map", str(FIXTURES / "basic.map"), "--output", str(out), "--check")
        self.assertEqual(r.returncode, 0)

    def test_check_mode_fails_on_drift(self):
        """--check exits 1 when report content differs."""
        out = self.make_output_path("check-fail")
        # Write with basic, check against overlay (different content)
        run_budget("--map", str(FIXTURES / "basic.map"), "--output", str(out))
        r = run_budget("--map", str(FIXTURES / "overlay.map"), "--output", str(out), "--check")
        self.assertEqual(r.returncode, 1)

    def test_pinned_assignments_captured(self):
        """Symbol assignments at GBA addresses are recorded."""
        out = self.make_output_path("pinned")
        r = run_budget("--map", str(FIXTURES / "basic.map"), "--output", str(out))
        self.assertEqual(r.returncode, 0)
        report = json.loads(out.read_text())
        pinned = report["pinned_assignments"]
        names = {p["name"] for p in pinned}
        self.assertIn("__text_start", names)
        self.assertIn("__ewram_start", names)
        self.assertIn("__iwram_start", names)
        self.assertIn("__sp_irq", names)
        self.assertIn("__iwram_top", names)

    def test_boundary_pinned_assignments_are_marked(self):
        """Region-end marker assignments are tagged as boundaries."""
        out = self.make_output_path("boundary")
        r = run_budget("--map", str(FIXTURES / "basic.map"), "--output", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(out.read_text())
        by_name = {entry["name"]: entry for entry in report["pinned_assignments"]}
        self.assertEqual(by_name["__sp_irq"]["region"], "iwram")
        self.assertNotIn("boundary", by_name["__sp_irq"])
        self.assertEqual(by_name["__iwram_top"]["region"], "iwram")
        self.assertTrue(by_name["__iwram_top"]["boundary"])

    def test_overlay_growth_detects_overflow(self):
        """Overlay peak beyond both coverage and capacity triggers overflow."""
        out = self.make_output_path("overlay-growth")
        r = run_budget("--map", str(FIXTURES / "overlay_growth.map"), "--output", str(out))
        self.assertEqual(r.returncode, 1)
        report = json.loads(out.read_text())
        ewram = next(reg for reg in report["regions"] if reg["name"] == "ewram")
        self.assertTrue(report["overflow"])
        self.assertTrue(ewram["overflow"])
        self.assertEqual(ewram["occupied_bytes"], 0x42000)

    def test_overlay_contained_does_not_double_count(self):
        """Contained overlay peaks do not increase occupied bytes."""
        out = self.make_output_path("overlay-contained")
        r = run_budget("--map", str(FIXTURES / "overlay_contained.map"), "--output", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(out.read_text())
        ewram = next(reg for reg in report["regions"] if reg["name"] == "ewram")
        self.assertFalse(report["overflow"])
        self.assertFalse(ewram["overflow"])
        self.assertEqual(ewram["occupied_bytes"], 0x3efb8)

    def test_dotprefixed_output_sections_are_parsed(self):
        """Dot-prefixed output sections are recognized in both map formats."""
        out = self.make_output_path("dotprefix")
        r = run_budget("--map", str(FIXTURES / "dotprefix.map"), "--output", str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(out.read_text())
        self.assertEqual(
            {section["name"] for section in report["sections"]},
            {".text", ".data", ".bss"},
        )


if __name__ == "__main__":
    unittest.main()
