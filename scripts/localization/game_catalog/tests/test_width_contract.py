"""Unit tests for the typed rendered-width and generated-wrap contract."""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.localization.game_locales.width_contract import (
    TextWidthContractError,
    WidthMetrics,
    classify_targets,
    insert_safe_line_breaks,
    load_width_registry,
    validate_payload_width,
)
from scripts.localization.game_catalog.build import build_game_catalog


ROOT = Path(__file__).resolve().parents[4]


class WidthContractTests(unittest.TestCase):
    def setUp(self):
        self.metrics = WidthMetrics(
            locale="ja",
            style="talk",
            ascii_widths={value: 8 for value in range(0x20, 0x7F)},
            cjk_widths={ord(character): 8 for character in "日本語（）"},
        )

    def test_cjk_wrap_inserts_only_runtime_newline(self):
        wrapped, inserted = insert_safe_line_breaks(
            "日本語".encode("utf-8") + b"\0",
            metrics=self.metrics,
            max_pixels=16,
            source_name="fixture",
        )
        self.assertEqual(wrapped, "日本".encode("utf-8") + b"\x01" + "語".encode("utf-8") + b"\0")
        self.assertEqual(inserted, 1)
        self.assertEqual(
            validate_payload_width(
                wrapped,
                metrics=self.metrics,
                max_pixels=16,
                source_name="fixture",
            ),
            {"line_count": 2, "max_line_width": 16},
        )

    def test_preserves_existing_breaks_and_rejects_unbreakable_word(self):
        explicit = b"AA\x01AA\0"
        wrapped, inserted = insert_safe_line_breaks(
            explicit,
            metrics=self.metrics,
            max_pixels=16,
            source_name="explicit",
        )
        self.assertEqual(wrapped, explicit)
        self.assertEqual(inserted, 0)
        with self.assertRaisesRegex(TextWidthContractError, "unbreakable"):
            insert_safe_line_breaks(
                b"ABCDE\0",
                metrics=self.metrics,
                max_pixels=16,
                source_name="word",
            )

    def test_wrap_is_an_insertion_only_transform(self):
        payload = "日 本語".encode("utf-8") + b"\0"
        wrapped, inserted = insert_safe_line_breaks(
            payload,
            metrics=self.metrics,
            max_pixels=16,
            source_name="insertion-only",
        )
        self.assertGreater(inserted, 0)
        self.assertEqual(wrapped.replace(b"\x01", b""), payload)

    def test_kinsoku_boundary_is_never_used(self):
        with self.assertRaisesRegex(TextWidthContractError, "unbreakable"):
            insert_safe_line_breaks(
                "日）".encode("utf-8") + b"\0",
                metrics=self.metrics,
                max_pixels=8,
                source_name="kinsoku",
            )

    def test_registry_classifies_every_catalog_target(self):
        registry = load_width_registry(ROOT / "texts/locales/mapping/text_width_contexts.json")
        assignments, counts = classify_targets(
            ROOT,
            target_count=3414,
            registry=registry,
        )
        self.assertEqual(len(assignments), 3414)
        self.assertEqual(sum(counts.values()), 3414)
        self.assertGreater(counts["talk_dialogue_240"], 0)
        self.assertTrue(
            all(context in registry.contexts for context, _ in assignments.values())
        )
        for target in (0x0866, 0x0867):
            self.assertEqual(assignments[target][0], registry.subtitle_context)

    def test_subtitle_help_payloads_never_receive_generated_newlines(self):
        build = build_game_catalog(enabled_locales=("ja",))
        for target in (0x0866, 0x0867):
            entry = build.locale_bundle("ja").entries[target]
            self.assertNotIn(b"\x01", entry.encoded_bytes, hex(target))


if __name__ == "__main__":
    unittest.main()
