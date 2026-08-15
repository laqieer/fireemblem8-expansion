"""Regression coverage for the checked-in original-text edit ledger."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.localization.game_locales.text_edit_ledger import (
    DOC_PATH,
    REPORT_PATH,
    build_ledger,
    check_ledger,
)


ROOT = Path(__file__).resolve().parents[4]


class TextEditLedgerTests(unittest.TestCase):
    def test_committed_ledger_matches_authorized_or_pinned_sources(self):
        outputs = check_ledger(ROOT)
        self.assertEqual(set(outputs), {DOC_PATH.as_posix(), REPORT_PATH.as_posix()})

    def test_every_changed_original_has_reviewed_reason_and_provenance(self):
        ledger = build_ledger(ROOT)
        for locale in ("ja", "zh-Hans"):
            records = ledger["locales"][locale]["records"]
            edited = [
                record
                for record in records
                if record["category"] == "reviewed_indexed_override"
            ]
            self.assertEqual(len(edited), ledger["locales"][locale]["changed_count"])
            self.assertEqual(len(records), len(edited))
            self.assertTrue(all(record["reason"] != "none" for record in edited))
            self.assertTrue(all(isinstance(record["provenance"], dict) for record in edited))
            self.assertEqual(
                3339 - len(edited),
                ledger["locales"][locale]["direct_import_count"],
            )
            unchanged = ledger["locales"][locale]["unchanged_imports"]
            self.assertEqual(unchanged["count"], len(unchanged["ids"]))
            self.assertEqual(len(unchanged["aggregate_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
