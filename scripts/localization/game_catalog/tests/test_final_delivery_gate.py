import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
GAME_LOCALIZATION_MK = ROOT / "game_localization.mk"
MAKEFILE = ROOT / "Makefile"


class FinalDeliveryGateTests(unittest.TestCase):
    def setUp(self):
        self.fragment = GAME_LOCALIZATION_MK.read_text(encoding="utf-8")
        self.makefile = MAKEFILE.read_text(encoding="utf-8")

    def _prerequisites(self, target):
        match = re.search(
            rf"^{re.escape(target)}:\s*([^\n]*)$",
            self.fragment,
            re.MULTILINE,
        )
        self.assertIsNotNone(match, f"missing Make target {target}")
        return match.group(1).split()

    def test_final_gate_has_one_serial_dependency_chain(self):
        expected_edges = {
            "game-localization-final-mapping-check": (
                "game-localization-final-authored-check"
            ),
            "game-localization-final-raw-closure-check": (
                "game-localization-final-mapping-check"
            ),
            "game-localization-final-leakage-audit": (
                "game-localization-final-raw-closure-check"
            ),
            "game-localization-final-font-check": (
                "game-localization-final-leakage-audit"
            ),
            "game-localization-final-check": (
                "game-localization-final-font-check"
            ),
        }
        for target, prerequisite in expected_edges.items():
            with self.subTest(target=target):
                self.assertEqual(self._prerequisites(target), [prerequisite])

    def test_final_gate_dry_run_contains_every_gate_in_order(self):
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-n",
                "game-localization-final-check",
                "FE8J_BASEROM=/nonexistent/fe8j.gba",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        commands = (
            "check-authored-catalogs",
            "check-final-mapping --require-no-fallback --require-live-origin",
            "check-raw-closure",
            "scripts.localization.game_catalog audit-leakage",
            "scripts.fonttools.cjk check",
        )
        positions = []
        for command in commands:
            self.assertEqual(output.count(command), 1, output)
            positions.append(output.index(command))
        self.assertEqual(positions, sorted(positions), output)

    def test_final_gate_skips_archival_dependency_generation(self):
        required = (
            "game-localization-final-authored-check",
            "game-localization-final-mapping-check",
            "game-localization-final-raw-closure-check",
            "game-localization-final-leakage-audit",
            "game-localization-final-font-check",
            "game-localization-final-check",
        )
        block = re.search(
            r"^MAKECMDGOALS_NODEP\s*:=.*?(?=^\n|^ifeq)",
            self.makefile,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(block)
        for target in required:
            self.assertIn(target, block.group(0))


if __name__ == "__main__":
    unittest.main()
