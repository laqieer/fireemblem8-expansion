import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class SioLocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "src/sio_event.c").read_text(encoding="utf-8")
        cls.registry = json.loads(
            (ROOT / "texts/expansion/registry.json").read_text(encoding="utf-8")
        )
        cls.catalogs = {
            locale: json.loads(
                (ROOT / f"texts/expansion/catalog.{locale}.json").read_text(
                    encoding="utf-8"
                )
            )["strings"]
            for locale in ("en", "ja", "zh-Hans")
        }

    def test_transfer_progress_has_complete_locale_specific_messages(self):
        expected = {
            "sio.transfer.sending": {
                "en": "Sending",
                "ja": "送信中",
                "zh-Hans": "发送中",
            },
            "sio.transfer.receiving": {
                "en": "Receiving",
                "ja": "受信中",
                "zh-Hans": "接收中",
            },
        }
        registry_keys = {
            row["key"]
            for row in self.registry["messages"]
            if row["status"] == "active"
        }
        for key, translations in expected.items():
            self.assertIn(key, registry_keys)
            for locale, text in translations.items():
                self.assertEqual(self.catalogs[locale][key], text)

    def test_modern_progress_calls_resolve_messages_without_raw_literal_leakage(self):
        self.assertIn("EXP_MSG_SIO_TRANSFER_SENDING", self.source)
        self.assertIn("EXP_MSG_SIO_TRANSFER_RECEIVING", self.source)
        self.assertIn("ExpansionLocale_ResolveCurrent", self.source)
        self.assertEqual(
            re.findall(
                r"PutXMapProgressPercent\([^;]*,\s*\"(?:送信中|受信中)\"",
                self.source,
                flags=re.DOTALL,
            ),
            [],
        )
        self.assertIn(
            "PutXMapProgressPercent(&gUnk_Sio_7[0], "
            "SIO_TRANSFER_SENDING_TEXT",
            self.source,
        )
        self.assertIn(
            "PutXMapProgressPercent(&gUnk_Sio_7[0], "
            "SIO_TRANSFER_RECEIVING_TEXT",
            self.source,
        )
        self.assertEqual(self.source.count('"送信中"'), 1)
        self.assertEqual(self.source.count('"受信中"'), 1)


if __name__ == "__main__":
    unittest.main()
