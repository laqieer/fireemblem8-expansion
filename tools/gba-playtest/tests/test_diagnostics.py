import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gba_playtest


class DiagnosticTests(unittest.TestCase):
    def test_hash_and_probe_mismatches_name_exact_paths(self):
        expected = {
            "format_version": 1,
            "scenario": "boot",
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
            "format_version": 1,
            "scenario": "boot",
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


if __name__ == "__main__":
    unittest.main()
