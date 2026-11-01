"""Unit tests for the typed rendered-width and generated-wrap contract."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.localization.game_locales.width_contract import (
    TextRenderer,
    TextWidthContractError,
    WidthMetrics,
    apply_width_contract,
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

    def test_explicit_english_fallback_is_validated_without_reflow_or_materialization(self):
        registry = load_width_registry(ROOT / "texts/locales/mapping/text_width_contexts.json")
        with tempfile.TemporaryDirectory() as directory:
            options = dict(
                repo_root=ROOT, locale="ja", target_payloads=(None,),
                registry=registry, event_root=Path(directory), fixed_label_ids=(),
            )
            payloads, _report = apply_width_contract(
                **options, fallback_payloads=(b"English\0",),
            )
            self.assertEqual(payloads, (None,))
            with self.assertRaises(TextWidthContractError):
                apply_width_contract(**options, fallback_payloads=(b"Word " * 80 + b"\0",))

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

    def test_nl2_keeps_full_width_talk_lines_separate_without_reflow(self):
        payload = b"A" * 30 + b"\x02" + b"A" * 30 + b"\0"
        self.assertEqual(
            validate_payload_width(
                payload, metrics=self.metrics, max_pixels=240, source_name="English fallback",
                renderer=TextRenderer.TALK,
            ),
            {"line_count": 2, "max_line_width": 240},
        )
        self.assertEqual(
            insert_safe_line_breaks(
                payload, metrics=self.metrics, max_pixels=240, source_name="English fallback",
                renderer=TextRenderer.TALK,
            ),
            (payload, 0),
        )
        with self.assertRaises(TextWidthContractError):
            validate_payload_width(
                payload[:-1] + b"A\0", metrics=self.metrics, max_pixels=240,
                source_name="overflowing English fallback",
                renderer=TextRenderer.TALK,
            )

    def test_nl2_does_not_reset_static_text_even_with_talk_glyphs(self):
        payload = b"A" * 30 + b"\x02" + b"A" * 30 + b"\0"
        with self.assertRaisesRegex(TextWidthContractError, "480px"):
            validate_payload_width(
                payload, metrics=self.metrics, max_pixels=240, source_name="static Talk-font NL2",
            )
        with self.assertRaisesRegex(TextWidthContractError, "unbreakable"):
            insert_safe_line_breaks(
                payload, metrics=self.metrics, max_pixels=240, source_name="static Talk-font NL2",
            )

    def test_wait_control_does_not_reset_a_drawing_cursor(self):
        payload = b"AA\x03AA\0"
        for renderer in (TextRenderer.TALK, TextRenderer.BOX, TextRenderer.CG):
            with self.subTest(renderer=renderer):
                self.assertEqual(
                    validate_payload_width(
                        payload, metrics=self.metrics, max_pixels=32,
                        source_name="continuous wait", renderer=renderer,
                    )["max_line_width"],
                    32,
                )
                with self.assertRaisesRegex(TextWidthContractError, "32px"):
                    validate_payload_width(
                        payload, metrics=self.metrics, max_pixels=24,
                        source_name="continuous wait", renderer=renderer,
                    )
        for renderer in (TextRenderer.BOX_MEASURE, TextRenderer.CG_MEASURE):
            with self.subTest(measurement=renderer):
                self.assertEqual(
                    validate_payload_width(
                        payload, metrics=self.metrics, max_pixels=None,
                        source_name="native box sizing", renderer=renderer,
                    )["max_line_width"],
                    24,
                )
        with self.assertRaisesRegex(TextWidthContractError, "8px"):
            validate_payload_width(
                b"\x03\0", metrics=self.metrics, max_pixels=7,
                source_name="wait caret", renderer=TextRenderer.BOX,
            )

    def test_speaker_reselection_does_not_clear_an_open_bubble(self):
        from scripts.localization.game_catalog.control_streams import (
            ControlStreamError, TalkFontMetrics, tokenize_payload, validate_talk_line_widths,
        )

        for middle in (b"\x0A", b"\x0B\x0A", b"\x0B\x11\x0A", b"\x14"):
            payload = b"\x0A" + b"A" * 20 + middle + b"A" * 20 + b"\0"
            with self.subTest(middle=middle, interface="canonical"):
                with self.assertRaisesRegex(TextWidthContractError, "320px"):
                    validate_payload_width(
                        payload, metrics=self.metrics, max_pixels=240,
                        source_name="same speaker", renderer=TextRenderer.TALK,
                    )
            with self.subTest(middle=middle, interface="talk"):
                with self.assertRaises(ControlStreamError):
                    validate_talk_line_widths(
                        tokenize_payload(payload, source_name="same speaker"),
                        source_name="same speaker",
                        metrics=TalkFontMetrics(
                            locale="ja", ascii_widths=self.metrics.ascii_widths,
                            cjk_widths=self.metrics.cjk_widths,
                        ),
                    )

        for transition in (b"\x0B", b"\x11\x10\x01\x00"):
            different = b"\x0A" + b"A" * 20 + transition + b"A" * 20 + b"\0"
            with self.subTest(real_clear=transition):
                self.assertEqual(
                    validate_payload_width(
                        different, metrics=self.metrics, max_pixels=240,
                        source_name="new bubble", renderer=TextRenderer.TALK,
                    ),
                    {"line_count": 2, "max_line_width": 160},
                )
                self.assertEqual(
                    validate_talk_line_widths(
                        tokenize_payload(different, source_name="new bubble"),
                        source_name="new bubble",
                        metrics=TalkFontMetrics(
                            locale="ja", ascii_widths=self.metrics.ascii_widths,
                            cjk_widths=self.metrics.cjk_widths,
                        ),
                    )["max_talk_line_width"],
                    160,
                )
        payload = (
            b"\x0A\x10\x01\x00" + ("日" * 20).encode()
            + b"\x0A" + ("日" * 20).encode() + b"\0"
        )
        wrapped, inserted = insert_safe_line_breaks(
            payload, metrics=self.metrics, max_pixels=240,
            source_name="same speaker wrapping", renderer=TextRenderer.TALK,
        )
        self.assertEqual(inserted, 1)
        validate_payload_width(
            wrapped, metrics=self.metrics, max_pixels=240,
            source_name="same speaker wrapping", renderer=TextRenderer.TALK,
        )
        tokens = tokenize_payload(wrapped, source_name="same speaker wrapping")
        self.assertEqual(
            b"".join(
                wrapped[token.offset:token.offset + token.length] for token in tokens
                if not (token.kind == "control" and token.control == 0x01)
            ),
            payload,
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
        self.assertGreaterEqual(sum(counts.values()), 3414)
        self.assertGreater(counts["talk_dialogue_240"], 0)
        self.assertTrue(
            all(
                item.context in registry.contexts
                for requirements in assignments.values() for item in requirements
            )
        )
        for target in (0x0866, 0x0867):
            self.assertIn(registry.subtitle_context, {item.context for item in assignments[target]})

    def test_event_font_classification_preserves_native_text_mode(self):
        registry = load_width_registry(ROOT / "texts/locales/mapping/text_width_contexts.json")
        with tempfile.TemporaryDirectory() as directory:
            event_root = Path(directory)
            source = event_root / "selected.c"
            for start, expected in (
                ("TEXTSTART", registry.talk_context),
                ("EvtTextStart", registry.talk_context),
                ("REMOVEPORTRAITS", registry.talk_context),
                ("EvtTextStartType1", registry.talk_context),
                ("_1A22", registry.cg_context),
                ("EvtTextStartType2", registry.cg_context),
                ("TUTORIALTEXTBOXSTART", registry.box_context),
                ("EvtTextTutorialStart", registry.box_context),
                ("EvtTextStartType3", registry.box_context),
                ("SOLOTEXTBOXSTART", registry.box_context),
                ("EvtTextStartType4", registry.box_context),
                ("_1A25", registry.box_context),
                ("EvtTextStartType5", registry.box_context),
            ):
                with self.subTest(start=start):
                    source.write_text(
                        f"EventScr Selected[] = {{ {start} TEXTSHOW(MSG_SELECTED) ENDA }};\n"
                    )
                    assignments, _ = classify_targets(
                        ROOT, target_count=3, registry=registry, event_root=event_root,
                        message_ids={"MSG_SELECTED": 1}, fixed_label_ids=(),
                    )
                    drawing = [item for item in assignments[1] if not item.measure_only]
                    self.assertEqual({item.context for item in drawing}, {expected})
                    self.assertEqual({item.style for item in drawing}, {"talk"})
                    if expected == registry.box_context:
                        self.assertEqual(
                            {
                                (item.renderer, item.style) for item in assignments[1]
                                if item.measure_only
                            },
                            {
                                (TextRenderer.BOX_MEASURE, "system"),
                                (TextRenderer.BOX_MEASURE, "talk"),
                            },
                        )

    def test_native_uses_union_and_live_references_cannot_be_silently_dropped(self):
        registry = load_width_registry(ROOT / "texts/locales/mapping/text_width_contexts.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "selected.c"
            source.write_text(
                "EventScr Selected[] = {\n"
                "BEQ(1, EVT_SLOT_C, EVT_SLOT_0)\n"
                "TEXTSTART TEXTSHOW(MSG_SELECTED) GOTO(2)\n"
                "LABEL(1) SOLOTEXTBOXSTART TEXTSHOW(MSG_SELECTED)\n"
                "LABEL(2) ENDA\n};\n"
            )
            assignments, _ = classify_targets(
                ROOT, target_count=3, registry=registry, event_root=root,
                message_ids={"MSG_SELECTED": 1}, fixed_label_ids=(1,),
            )
            self.assertEqual(
                {item.context for item in assignments[1]},
                {registry.fixed_context, registry.talk_context, registry.box_context},
            )
            self.assertEqual({item.style for item in assignments[1]}, {"system", "talk"})
            for target, labels in ((3, ()), (1, (3,))):
                with self.subTest(event_target=target, fixed_labels=labels):
                    with self.assertRaisesRegex(TextWidthContractError, "active target"):
                        classify_targets(
                            ROOT, target_count=3, registry=registry, event_root=root,
                            message_ids={"MSG_SELECTED": target}, fixed_label_ids=labels,
                        )

    def test_subtitle_help_payloads_never_receive_generated_newlines(self):
        build = build_game_catalog(enabled_locales=("ja",))
        for target in (0x0866, 0x0867):
            entry = build.locale_bundle("ja").entries[target]
            self.assertNotIn(b"\x01", entry.encoded_bytes, hex(target))


if __name__ == "__main__":
    unittest.main()
