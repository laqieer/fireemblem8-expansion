"""Unit tests for tools/gba-playtest/tests/sram_fixture.py: the synthetic
SRAM image generator used by save-gate runtime scenarios (issue #2 slice 2,
requirement 6). All images are generated in a temporary directory at test
time -- nothing here is committed as a binary."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sram_fixture as sf


class FixtureImageShapeTests(unittest.TestCase):
    def test_every_state_produces_an_exact_0x8000_byte_image_and_reclassifies(self):
        epoch = sf.resolve_epoch()
        for state in sf.ALL_FIXTURE_STATES:
            with self.subTest(state=state):
                image = sf.build_fixture_image(state)
                self.assertEqual(len(image), sf.sft.SRAM_SIZE)
                self.assertEqual(sf.sft.classify_image(image, epoch), state)

    def test_all_eight_states_are_covered(self):
        self.assertEqual(len(sf.ALL_FIXTURE_STATES), 8)
        self.assertEqual(len(set(sf.ALL_FIXTURE_STATES)), 8)

    def test_unknown_state_raises(self):
        with self.assertRaises(ValueError):
            sf.build_fixture_image("SAVE_COMPAT_NOT_A_REAL_STATE")


class WriteFixtureTests(unittest.TestCase):
    def test_write_fixture_creates_exact_size_file_under_given_directory(self):
        with tempfile.TemporaryDirectory(prefix="sram-fixture-test-") as temp:
            path = Path(temp) / "nested" / "current.sav"
            sf.write_fixture(path, sf.STATE_CURRENT)
            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_size, sf.sft.SRAM_SIZE)


class MigrateFixtureTests(unittest.TestCase):
    def test_migrate_legacy_fixture_reclassifies_current(self):
        with tempfile.TemporaryDirectory(prefix="sram-fixture-test-") as temp:
            root = Path(temp)
            source = root / "legacy.sav"
            dest = root / "migrated.sav"
            sf.write_fixture(source, sf.STATE_VALID_LEGACY)
            sf.migrate_fixture(source, dest)
            self.assertEqual(dest.stat().st_size, sf.sft.SRAM_SIZE)
            epoch = sf.resolve_epoch()
            self.assertEqual(sf.sft.classify_image(dest.read_bytes(), epoch), sf.STATE_CURRENT)

    def test_migrate_non_migratable_fixture_raises(self):
        with tempfile.TemporaryDirectory(prefix="sram-fixture-test-") as temp:
            root = Path(temp)
            source = root / "header-corrupt.sav"
            dest = root / "migrated.sav"
            sf.write_fixture(source, sf.STATE_HEADER_CORRUPT)
            with self.assertRaises(RuntimeError):
                sf.migrate_fixture(source, dest)


if __name__ == "__main__":
    unittest.main()
