"""Unit tests for linker_report.budget."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
BUDGET_PY = Path(__file__).parent.parent / "budget.py"


def run_budget(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BUDGET_PY), *args],
        capture_output=True, text=True,
    )


class TestMapParsing(unittest.TestCase):
    """Core map-file parsing tests."""

    def test_basic_regions_and_sections(self):
        """Parse a minimal map and check region/section counts."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = f.name
        r = run_budget("--map", str(FIXTURES / "basic.map"), "--output", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(Path(out).read_text())
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
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = f.name
        r = run_budget("--map", str(FIXTURES / "overlay.map"), "--output", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(Path(out).read_text())
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
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = f.name
        r = run_budget("--map", str(FIXTURES / "overflow.map"), "--output", out)
        self.assertEqual(r.returncode, 1)
        report = json.loads(Path(out).read_text())
        self.assertTrue(report["overflow"])
        ewram = next(reg for reg in report["regions"] if reg["name"] == "ewram")
        self.assertTrue(ewram["overflow"])
        self.assertEqual(ewram["occupied_bytes"], 0x50000)

    def test_malformed_map_exits_2(self):
        """A map file with no Memory Configuration returns exit 2."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = f.name
        r = run_budget("--map", str(FIXTURES / "malformed.map"), "--output", out)
        self.assertEqual(r.returncode, 2)

    def test_deterministic_output(self):
        """Two runs on the same input produce byte-identical JSON."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out1 = f.name
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out2 = f.name
        run_budget("--map", str(FIXTURES / "overlay.map"), "--output", out1)
        run_budget("--map", str(FIXTURES / "overlay.map"), "--output", out2)
        self.assertEqual(Path(out1).read_text(), Path(out2).read_text())

    def test_check_mode_passes_on_match(self):
        """--check succeeds when report matches."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = f.name
        run_budget("--map", str(FIXTURES / "basic.map"), "--output", out)
        r = run_budget("--map", str(FIXTURES / "basic.map"), "--output", out, "--check")
        self.assertEqual(r.returncode, 0)

    def test_check_mode_fails_on_drift(self):
        """--check exits 1 when report content differs."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = f.name
        # Write with basic, check against overlay (different content)
        run_budget("--map", str(FIXTURES / "basic.map"), "--output", out)
        r = run_budget("--map", str(FIXTURES / "overlay.map"), "--output", out, "--check")
        self.assertEqual(r.returncode, 1)

    def test_pinned_assignments_captured(self):
        """Symbol assignments at GBA addresses are recorded."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = f.name
        r = run_budget("--map", str(FIXTURES / "basic.map"), "--output", out)
        self.assertEqual(r.returncode, 0)
        report = json.loads(Path(out).read_text())
        pinned = report["pinned_assignments"]
        names = {p["name"] for p in pinned}
        self.assertIn("__text_start", names)
        self.assertIn("__ewram_start", names)
        self.assertIn("__iwram_start", names)


if __name__ == "__main__":
    unittest.main()
