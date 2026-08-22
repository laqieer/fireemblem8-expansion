"""Behavior-baseline metadata census for issue #29."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
FINGERPRINTS_DIR = PLAYTEST_DIR / "fingerprints"

sys.path.insert(0, str(PLAYTEST_DIR))
import gba_playtest  # noqa: E402


class FingerprintIdentityPolicyCensusTests(unittest.TestCase):
    def test_committed_behavior_baselines_omit_rom_identity(self):
        paths = sorted(FINGERPRINTS_DIR.glob("*.json"))
        self.assertGreaterEqual(len(paths), 81, "expected the committed baseline corpus")
        for path in paths:
            with self.subTest(path=path.name):
                fingerprint = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("rom", fingerprint)
                self.assertEqual(
                    gba_playtest.validate_fingerprint(
                        fingerprint, str(path), policy="behavior"
                    ),
                    fingerprint,
                )

    def test_runtime_consumers_explicitly_select_behavior_policy(self):
        modern_mk = (REPO_ROOT / "modern.mk").read_text(encoding="utf-8").splitlines()
        recipes = []
        for index, line in enumerate(modern_mk):
            if '"$(MODERN_PLAYTEST)" verify' in line:
                recipes.append("\n".join(modern_mk[index:index + 8]))
        self.assertGreaterEqual(len(recipes), 35, "expected all modern runtime recipes")
        for recipe in recipes:
            self.assertIn("--expected", recipe)
            self.assertRegex(recipe, r"--policy\s+behavior")

        shifted_boot = (
            REPO_ROOT / "scripts" / "shiftcheck" / "modern_shifted_boot.sh"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            shifted_boot,
            re.compile(
                r"gba_playtest\.py verify.*?--policy\s+behavior",
                re.DOTALL,
            ),
        )

        save_compat = (
            PLAYTEST_DIR / "run_save_compat_checks.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"--policy", "behavior"', save_compat)


if __name__ == "__main__":
    unittest.main()
