import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gba_playtest


class SerializationTests(unittest.TestCase):
    def test_serialization_is_sorted_and_has_one_trailing_newline(self):
        first = {
            "scenario": "stable",
            "format_version": 2,
            "checkpoints": [{"probes": [], "name": "cp", "frame": 1}],
        }
        second = {
            "checkpoints": [{"frame": 1, "name": "cp", "probes": []}],
            "format_version": 2,
            "scenario": "stable",
        }
        self.assertEqual(
            gba_playtest.serialize_fingerprint(first),
            gba_playtest.serialize_fingerprint(second),
        )
        rendered = gba_playtest.serialize_fingerprint(first)
        self.assertTrue(rendered.endswith("}\n"))
        self.assertFalse(rendered.endswith("}\n\n"))
        self.assertLess(rendered.index('"checkpoints"'), rendered.index('"scenario"'))


if __name__ == "__main__":
    unittest.main()
