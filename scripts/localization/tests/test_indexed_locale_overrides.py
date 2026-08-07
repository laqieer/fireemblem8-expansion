import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_locales.importer import PINNED_SOURCE_SHA256
from scripts.localization.game_locales.overrides import load_override_catalog
from scripts.localization.game_locales.parsers import LocaleSourceError


class IndexedLocaleOverrideTests(unittest.TestCase):
    OVERRIDE_PATH = ROOT / "texts/locales/indexed_overrides.json"

    def _load_document(self):
        return json.loads(self.OVERRIDE_PATH.read_text(encoding="utf-8"))

    def _write_fixture(self, directory, document):
        path = Path(directory) / "overrides.json"
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def test_catalog_is_hash_pinned_and_has_provenance(self):
        catalog = load_override_catalog(
            self.OVERRIDE_PATH,
            expected_source_hashes=PINNED_SOURCE_SHA256,
        )
        self.assertEqual(catalog.entry_count, 27)
        self.assertEqual(set(catalog.sources), {"fe8j_indexed", "fe8cn_source"})
        for source in catalog.sources.values():
            self.assertEqual(
                source.source_sha256,
                PINNED_SOURCE_SHA256[source.source_id],
            )
            for entry in source.entries.values():
                self.assertTrue(entry.reason)
                self.assertTrue(entry.provenance["audit"])
                self.assertTrue(entry.provenance["context"])
                self.assertTrue(entry.provenance["target_ids"])

    def test_source_hash_drift_is_rejected(self):
        document = self._load_document()
        document["sources"]["fe8cn_source"]["source_sha256"] = "0" * 64
        test_dir = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(
            prefix=".indexed_override_hash_",
            dir=test_dir,
        ) as temporary:
            path = self._write_fixture(temporary, document)
            with self.assertRaisesRegex(LocaleSourceError, "not pinned"):
                load_override_catalog(
                    path,
                    expected_source_hashes=PINNED_SOURCE_SHA256,
                )

    def test_control_or_newline_structure_change_is_rejected(self):
        document = copy.deepcopy(self._load_document())
        entry = document["sources"]["fe8cn_source"]["entries"]["0x004D"]
        entry["expected_text"] = "NOW[CTRL:0004]\nLOADING"
        entry["replacement_text"] = "正在载入[CTRL:0004]"
        test_dir = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(
            prefix=".indexed_override_structure_",
            dir=test_dir,
        ) as temporary:
            path = self._write_fixture(temporary, document)
            with self.assertRaisesRegex(
                LocaleSourceError,
                "preserve controls, newlines, placeholders",
            ):
                load_override_catalog(
                    path,
                    expected_source_hashes=PINNED_SOURCE_SHA256,
                )


if __name__ == "__main__":
    unittest.main()
