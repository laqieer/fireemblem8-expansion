import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.localization.catalog import load_catalog
from scripts.localization.game_locales.ending_metrics import (
    _ascii_widths,
    _cjk_widths,
    _line_width,
)


ROOT = Path(__file__).resolve().parents[3]
HOST_DRIVER = Path(__file__).with_name("host_sio_progress_layout_driver.c")


class SioLocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "src/sio_event.c").read_text(encoding="utf-8")
        cls.main_source = (ROOT / "src/sio_main.c").read_text(encoding="utf-8")
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
        cls.loaded_catalog = load_catalog()
        cls.ascii_widths = _ascii_widths(ROOT)
        cls.cjk_widths = {
            locale: _cjk_widths(ROOT, locale)[0]
            for locale in ("ja", "zh-Hans")
        }

    @classmethod
    def _pixel_width(cls, text, locale):
        return _line_width(
            text,
            locale=locale,
            ascii_widths=cls.ascii_widths,
            cjk_widths=cls.cjk_widths.get(locale, {}),
        )

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

    def test_progress_layout_measures_labels_and_bounds_the_value_column(self):
        self.assertIn("SioGetProgressValueX(", self.source)
        self.assertIn("GetStringTextLen(str)", self.source)
        self.assertIn("GetStringTextLen(suffix)", self.source)
        self.assertIn("DrawXMapProgressLabel(th, str, labelMaxWidth)", self.source)
        self.assertIn(
            "int maxValueX = textWidth - suffixWidth - PROGRESS_SUFFIX_OFFSET;",
            self.main_source,
        )

        suffix_width = self.ascii_widths[ord("%")]
        for locale in ("en", "ja", "zh-Hans", "qps-ploc"):
            strings = self.loaded_catalog.strings_for(locale)
            for key in ("sio.transfer.sending", "sio.transfer.receiving"):
                label_width = self._pixel_width(strings[key], locale)
                for tile_width in range(4, 17):
                    with self.subTest(
                        locale=locale,
                        key=key,
                        tile_width=tile_width,
                    ):
                        text_width = tile_width * 8
                        value_x = min(
                            label_width + 20,
                            text_width - suffix_width - 8,
                        )
                        label_max_width = max(0, value_x - 20)
                        if label_max_width:
                            self.assertLessEqual(label_max_width + 20, value_x)
                        self.assertGreaterEqual(value_x, 16)
                        self.assertLessEqual(value_x + 8 + suffix_width, text_width)
                        if tile_width >= 10:
                            self.assertLessEqual(label_width, label_max_width)

    def test_qps_progress_labels_use_the_compact_width_safe_policy(self):
        qps = self.loaded_catalog.strings_for("qps-ploc")
        self.assertEqual(qps["sio.transfer.sending"], "SeNdInG")
        self.assertEqual(qps["sio.transfer.receiving"], "ReCeIvInG")

        receiving_width = self._pixel_width(
            qps["sio.transfer.receiving"],
            "qps-ploc",
        )
        value_x = min(
            receiving_width + 20,
            80 - self.ascii_widths[ord("%")] - 8,
        )
        self.assertGreater(value_x, 54)
        self.assertLessEqual(receiving_width + 20, value_x)

    def test_progress_layout_helper_runs_natively(self):
        cc = shutil.which("gcc") or shutil.which("cc")
        if cc is None:
            self.skipTest("no host C compiler available")

        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="sio-progress-layout-",
            dir=build_root,
        ) as work_dir_name:
            work_dir = Path(work_dir_name)
            include_flags = [
                "-I",
                str(ROOT / "include"),
                "-I",
                str(ROOT / "include/generated"),
            ]
            common_flags = [
                cc,
                "-c",
                "-w",
                "-DMODERN",
                "-DFE8_EXPANSION_ENABLED_LOCALE_MASK=0",
                "-ffunction-sections",
                "-fdata-sections",
                *include_flags,
            ]
            commands = (
                [
                    *common_flags,
                    str(ROOT / "src/sio_main.c"),
                    "-o",
                    str(work_dir / "sio_main.o"),
                ],
                [
                    *common_flags,
                    str(HOST_DRIVER),
                    "-o",
                    str(work_dir / "driver.o"),
                ],
                [
                    cc,
                    "-Wl,--gc-sections",
                    str(work_dir / "sio_main.o"),
                    str(work_dir / "driver.o"),
                    "-o",
                    str(work_dir / "test"),
                ],
            )
            for command in commands:
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
            result = subprocess.run(
                [str(work_dir / "test")],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode,
                0,
                result.stdout + result.stderr,
            )


if __name__ == "__main__":
    unittest.main()
