"""Tests for exact modern shifted-layout verification."""

from __future__ import annotations

import unittest

from scripts.shiftcheck.verify_shifted_layout import verify_layout


class VerifyShiftedLayoutTests(unittest.TestCase):
    def symbols(self):
        return {
            "Init": 0x08000000,
            "__shift_start": 0x08000A20,
            "__shift_end": 0x08000A20,
            "ReadSramFast_Core": 0x08000A20,
            "__floating_end": 0x08B26F0C,
            "_banim_pal_start": 0x08EF8000,
        }

    def test_exact_shift_passes(self):
        shift = 0x40000
        base = self.symbols()
        shifted = dict(base)
        shifted["__shift_end"] += shift
        shifted["ReadSramFast_Core"] += shift
        shifted["__floating_end"] += shift
        self.assertEqual(verify_layout(base, shifted, shift), [])

    def test_moved_pin_fails(self):
        shift = 0x40000
        base = self.symbols()
        shifted = dict(base)
        shifted["Init"] += shift
        shifted["__shift_end"] += shift
        shifted["ReadSramFast_Core"] += shift
        shifted["__floating_end"] += shift
        errors = verify_layout(base, shifted, shift)
        self.assertTrue(any("pinned symbol Init moved" in error for error in errors))

    def test_pre_shifted_base_fails(self):
        shift = 0x40000
        base = self.symbols()
        base["__shift_end"] += 0x100
        shifted = dict(base)
        shifted["__shift_end"] += shift
        shifted["ReadSramFast_Core"] += shift
        shifted["__floating_end"] += shift
        errors = verify_layout(base, shifted, shift)
        self.assertTrue(any("base ELF is already shifted" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
