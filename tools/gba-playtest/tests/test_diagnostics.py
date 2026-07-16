import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gba_playtest


class DiagnosticTests(unittest.TestCase):
    def test_hash_and_probe_mismatches_name_exact_paths(self):
        expected = {
            "format_version": 2,
            "scenario": "boot",
            "rom": {
                "sha1": "0" * 40,
                "size": 1024,
                "title": "FIXTURE",
                "game_code": "TST0",
            },
            "checkpoints": [
                {
                    "frame": 5,
                    "name": "visible",
                    "framebuffer_hash": "fnv1a64-rgb24:0000000000000000",
                    "probes": [
                        {"address": "0x02000000", "size": 4, "value": "0x00000000"}
                    ],
                }
            ],
        }
        actual = {
            "format_version": 2,
            "scenario": "boot",
            "rom": {
                "sha1": "0" * 40,
                "size": 1024,
                "title": "FIXTURE",
                "game_code": "TST0",
            },
            "checkpoints": [
                {
                    "frame": 5,
                    "name": "visible",
                    "framebuffer_hash": "fnv1a64-rgb24:1111111111111111",
                    "probes": [
                        {"address": "0x02000000", "size": 4, "value": "0x12345678"}
                    ],
                }
            ],
        }
        differences = gba_playtest.compare_fingerprints(expected, actual)
        joined = "\n".join(differences)
        self.assertIn("checkpoints[0].framebuffer_hash", joined)
        self.assertIn("checkpoints[0].probes[0].value", joined)
        self.assertIn("expected '0x00000000', actual '0x12345678'", joined)

    def test_exact_policy_binds_rom_and_behavior_policy_is_explicit(self):
        baseline = {
            "format_version": 2,
            "scenario": "boot",
            "rom": {
                "sha1": "0" * 40,
                "size": 1024,
                "title": "BASE",
                "game_code": "BAS0",
            },
            "checkpoints": [],
        }
        candidate = {
            **baseline,
            "rom": {
                "sha1": "1" * 40,
                "size": 1024,
                "title": "CANDIDATE",
                "game_code": "CAN0",
            },
        }
        exact = gba_playtest.compare_fingerprints(
            baseline, candidate, policy="exact-rom"
        )
        self.assertTrue(any(difference.startswith("rom.sha1:") for difference in exact))
        self.assertEqual(
            gba_playtest.compare_fingerprints(
                baseline, candidate, policy="behavior"
            ),
            [],
        )

    def test_fingerprint_provenance_is_required_and_strict(self):
        missing = {
            "format_version": 2,
            "scenario": "boot",
            "checkpoints": [],
        }
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "missing.*rom"):
            gba_playtest.validate_fingerprint(missing, "<fingerprint>")

        malformed = {
            **missing,
            "rom": {
                "sha1": "not-a-sha1",
                "size": 1,
                "title": "TEST",
                "game_code": "TST0",
            },
        }
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "40 lowercase"):
            gba_playtest.validate_fingerprint(malformed, "<fingerprint>")


if __name__ == "__main__":
    unittest.main()
