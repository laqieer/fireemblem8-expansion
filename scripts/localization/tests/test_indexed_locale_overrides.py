import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_locales.importer import PINNED_SOURCE_SHA256
from scripts.localization.game_locales.overrides import (
    apply_indexed_overrides,
    load_override_catalog,
)
from scripts.localization.game_locales.parsers import IndexedMessage, LocaleSourceError


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
        self.assertEqual(catalog.entry_count, 186)
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
        self.assertEqual(len(catalog.sources["fe8j_indexed"].entries), 63)
        self.assertEqual(len(catalog.sources["fe8cn_source"].entries), 123)

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

    def test_blue_team_followup_overrides_are_exact(self):
        catalog = load_override_catalog(
            self.OVERRIDE_PATH,
            expected_source_hashes=PINNED_SOURCE_SHA256,
        )
        ja = catalog.sources["fe8j_indexed"].entries
        zh = catalog.sources["fe8cn_source"].entries

        self.assertEqual(ja[0x0061].replacement_text, "なし")
        self.assertLessEqual(
            len(ja[0x0061].replacement_text.encode("utf-8")), 14
        )
        expected_zh = {
            0x018C: "本次",
            0x048A: "伙伴",
            0x04D7: "人物的等级\n等级越高，能力越强",
            0x07E5: "所持物品已满，请选择要送往运输队的物品",
            0x07F6: "请选择持有待修复武器的角色",
            0x085C: "这件物品无法出售。[CTRL:0003]",
            0x085D: "呵呵……等一下。\n这件物品无法出售！[CTRL:0003]",
            0x086A: "真遗憾，你还没有运输队，\n无法送过去。[CTRL:0003]",
            0x086B: (
                "可惜你还没有运输队，\n"
                "不然我就能替你送过去……[CTRL:0003]"
            ),
        }
        for message_id, payload in expected_zh.items():
            self.assertEqual(
                zh[message_id].replacement_text,
                payload.replace("\n", "[CTRL:0001]"),
            )

        forbidden = ("今回", "配合", "等级20", "买不起", "无发")
        active_payload = "\n".join(
            zh[message_id].replacement_text for message_id in expected_zh
        )
        for fragment in forbidden:
            self.assertNotIn(fragment, active_payload)

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

    def test_supplement_hash_drift_is_rejected(self):
        document = self._load_document()
        document["supplements"][0]["sha256"] = "0" * 64
        test_dir = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(
            prefix=".indexed_override_supplement_hash_",
            dir=test_dir,
        ) as temporary:
            path = self._write_fixture(temporary, document)
            with self.assertRaisesRegex(
                LocaleSourceError,
                "override supplement SHA-256 drift",
            ):
                load_override_catalog(
                    path,
                    expected_source_hashes=PINNED_SOURCE_SHA256,
                )

    def test_per_payload_hash_pin_is_checked_before_structure(self):
        document = copy.deepcopy(self._load_document())
        document["supplements"] = []
        entry = document["sources"]["fe8cn_source"]["entries"]["0x004D"]
        document["sources"]["fe8cn_source"]["entries"] = {"0x004D": entry}
        expected_text = entry.pop("expected_text")
        replacement_text = entry.pop("replacement_text")
        entry["expected_text_sha256"] = hashlib.sha256(
            expected_text.encode("utf-8")
        ).hexdigest()
        entry["replacements"] = [
            {
                "expected": expected_text,
                "replacement": replacement_text,
            }
        ]
        test_dir = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(
            prefix=".indexed_override_payload_hash_",
            dir=test_dir,
        ) as temporary:
            path = self._write_fixture(temporary, document)
            catalog = load_override_catalog(
                path,
                expected_source_hashes=PINNED_SOURCE_SHA256,
            )
            source = catalog.sources["fe8cn_source"]
            messages = (IndexedMessage(0x004D, expected_text, 1),)
            applied, overrides = apply_indexed_overrides(
                messages,
                source=source,
            )
            self.assertEqual(len(overrides), len(source.entries))
            self.assertEqual(
                {message.id: message.text for message in applied}[0x004D],
                "正在载入",
            )

            entry["expected_text_sha256"] = "0" * 64
            bad_path = self._write_fixture(temporary, document)
            bad_catalog = load_override_catalog(
                bad_path,
                expected_source_hashes=PINNED_SOURCE_SHA256,
            )
            with self.assertRaisesRegex(
                LocaleSourceError,
                "does not match override expected text hash",
            ):
                apply_indexed_overrides(
                    messages,
                    source=bad_catalog.sources["fe8cn_source"],
                )

    def test_control_or_newline_structure_change_is_rejected(self):
        document = copy.deepcopy(self._load_document())
        document["supplements"] = []
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

    def test_hash_pinned_audited_control_change_is_allowed(self):
        document = copy.deepcopy(self._load_document())
        document["supplements"] = []
        entry = document["sources"]["fe8cn_source"]["entries"]["0x004D"]
        expected_text = entry.pop("expected_text")
        entry["expected_text_sha256"] = hashlib.sha256(
            expected_text.encode("utf-8")
        ).hexdigest()
        entry["replacement_text"] = "正在[CTRL:0001]载入"
        entry["preserve_structure"] = False
        document["sources"]["fe8cn_source"]["entries"] = {"0x004D": entry}
        test_dir = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(
            prefix=".indexed_override_control_change_",
            dir=test_dir,
        ) as temporary:
            path = self._write_fixture(temporary, document)
            catalog = load_override_catalog(
                path,
                expected_source_hashes=PINNED_SOURCE_SHA256,
            )
            messages = (
                IndexedMessage(0x004D, expected_text, 1),
            )
            updated, applied = apply_indexed_overrides(
                messages,
                source=catalog.sources["fe8cn_source"],
            )
        self.assertEqual(updated[0].text, "正在[CTRL:0001]载入")
        self.assertFalse(applied[0].preserve_structure)


if __name__ == "__main__":
    unittest.main()
