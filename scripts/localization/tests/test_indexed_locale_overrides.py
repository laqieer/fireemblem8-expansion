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
        self.assertEqual(catalog.entry_count, 79)
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
        self.assertEqual(len(catalog.sources["fe8j_indexed"].entries), 35)
        self.assertEqual(len(catalog.sources["fe8cn_source"].entries), 44)

    def test_full_semantic_audit_overrides_are_exact(self):
        catalog = load_override_catalog(
            self.OVERRIDE_PATH,
            expected_source_hashes=PINNED_SOURCE_SHA256,
        )
        ja = catalog.sources["fe8j_indexed"].entries
        zh = catalog.sources["fe8cn_source"].entries
        expected_ja = {
            0x0013: "ウィンドウカラー",
            0x0254: "フォレストナイト",
            0x0256: "ドラゴンマスター",
            0x0257: "ワイバーンナイト",
            0x026F: "ファルコンナイト",
            0x0280: "モーサドゥーグ",
            0x0287: "ゴーゴンエッグ",
            0x0289: "デスガーゴイル",
            0x028A: "ドラゴンゾンビ",
            0x0334: "サンダーストーム",
            0x0479: "ＭＨＰ",
            0x0717: "現れし異形の影Ａ",
            0x0718: "現れし異形の影Ｂ",
            0x073F: "勝利の歌Ａ",
            0x0740: "勝利の歌Ｂ",
        }
        expected_zh = {
            0x0013: "窗口颜色",
            0x001E: "单位移动速度",
            0x0717: "扭曲之影Ａ",
            0x0718: "扭曲之影Ｂ",
            0x073F: "胜利之歌Ａ",
            0x0740: "胜利之歌Ｂ",
        }
        for message_id, payload in expected_ja.items():
            self.assertEqual(ja[message_id].replacement_text, payload)
        for message_id, payload in expected_zh.items():
            self.assertEqual(zh[message_id].replacement_text, payload)
        self.assertIn("游戏中各个部分", zh[0x0913].replacement_text)
        self.assertNotIn("哥哥部分", zh[0x0913].replacement_text)
        self.assertIn("硬币", zh[0x0CB7].replacement_text)
        self.assertNotIn("骰子", zh[0x0CB7].replacement_text)

    def test_dummy_overrides_use_placeholder_wording(self):
        catalog = load_override_catalog(
            self.OVERRIDE_PATH,
            expected_source_hashes=PINNED_SOURCE_SHA256,
        )
        entries = catalog.sources["fe8cn_source"].entries
        for message_id in (0x038A, 0x03D1, 0x0433, 0x046C):
            self.assertIn("占位", entries[message_id].replacement_text)
            self.assertNotIn("大米", entries[message_id].replacement_text)

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
