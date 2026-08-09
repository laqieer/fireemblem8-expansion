import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from scripts.localization.game_catalog.build import build_game_catalog
from scripts.localization.game_catalog.control_streams import (
    CONTROL_DOMAIN_FE8J,
    CONTROL_DOMAIN_FE8U,
    ControlStreamError,
    build_event_continuation_models,
    load_portrait_operand_map,
    tokenize_payload,
    validate_mouth_toggle_balance,
)


def face_operands(payload):
    return tuple(
        token.argument
        for token in tokenize_payload(payload, source_name="test payload")
        if token.kind == "control" and token.control == 0x10
    )


def break_talk_count(payload):
    return sum(
        token.kind == "extended" and token.scalar == 0x04
        for token in tokenize_payload(payload, source_name="test payload")
    )


def structural_sequence(text):
    return re.findall(r"\[[^\]\n]+\]|\n", text)


class FinalControlStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = build_game_catalog(suffix_share=False)
        cls.portrait_map = load_portrait_operand_map()

    def test_every_final_payload_is_bounded_and_fids_match_fe8u_context(self):
        self.assertEqual(
            self.build.report["control_stream_validation"],
            {
                "model_count": 286,
                "modeled_target_count": 275,
                "portrait_remapped_target_count": 187,
                "validated_payload_count": 6828,
            },
        )
        for bundle in self.build.locales:
            for entry in bundle.entries:
                if entry.encoded_bytes is None:
                    continue
                tokens = tokenize_payload(
                    entry.encoded_bytes,
                    source_name=f"{bundle.locale} 0x{entry.target_id:04X}",
                )
                self.assertEqual(tokens[-1].kind, "end")
                self.assertEqual(
                    face_operands(entry.encoded_bytes),
                    face_operands(
                        self.build.english.entries[entry.target_id].encoded_bytes
                    ),
                    (bundle.locale, entry.target_id),
                )

    def test_portrait_evidence_covers_closed_shop_debug_and_target_override(self):
        data = json.loads(self.portrait_map.path.read_text(encoding="utf-8"))
        entries = {entry["source_operand"]: entry for entry in data["entries"]}
        expected_shop = {
            "0x0165": ("0x0164", "Anna"),
            "0x0166": ("0x0165", "Armoury"),
            "0x0167": ("0x0166", "Vendor"),
            "0x0168": ("0x0167", "Arena"),
            "0x0169": ("0x0168", "Secret_Shop"),
        }
        for source, (target, name) in expected_shop.items():
            self.assertEqual(entries[source]["target_operand"], target)
            self.assertEqual(entries[source]["name"], name)
        self.assertGreaterEqual(
            sum(entry["name"].endswith("EyeClosed") for entry in data["entries"]),
            12,
        )
        self.assertEqual(entries["0x0148"]["target_operand"], "0x0101")
        self.assertEqual(entries["0x01AD"]["target_operand"], "0x01AB")
        self.assertEqual(data["target_overrides"], [
            {
                "mapped_operand": "0x016A",
                "rationale": (
                    "FE8J provider 0x0B53 uses generic Soldier1, but FE8U target "
                    "0x0B93 explicitly loads the Rausten Soldier portrait; target "
                    "context overrides the generic signature map."
                ),
                "source_operand": "0x016B",
                "target_id": "0x0B93",
                "target_operand": "0x016F",
            }
        ])

    def test_anna_regression_and_provider_domain_prevent_double_remap(self):
        for locale in ("ja", "zh-Hans"):
            bundle = self.build.locale_bundle(locale)
            self.assertEqual(bundle.entries[0x0840].control_domain, CONTROL_DOMAIN_FE8J)
            self.assertEqual(face_operands(bundle.entries[0x0840].encoded_bytes), (0x0164,))
            self.assertEqual(
                bundle.entries[0x0884].control_domain,
                CONTROL_DOMAIN_FE8U,
            )
            self.assertEqual(face_operands(bundle.entries[0x0884].encoded_bytes), (0x0164,))
            self.assertEqual(
                face_operands(bundle.entries[0x0B93].encoded_bytes),
                (0x0153, 0x0122, 0x016F),
            )

    def test_tokenizer_rejects_truncated_extended_and_face_controls(self):
        for payload, message in (
            (b"\x80\x00", "truncated extended control"),
            (b"\x80\x01\x00", "truncated extended argument"),
            (b"\x10\x64\x00", "truncated LoadFace"),
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ControlStreamError, message):
                    tokenize_payload(payload, source_name="malformed")
        tokens = tokenize_payload(b"\x10\x64\x01\x80\x1f\x00", source_name="valid")
        self.assertEqual(tokens[0].argument, 0x0164)
        self.assertEqual(tokens[1].scalar, 0x1F)

    def test_mouth_balance_validator_rejects_open_state_at_dialogue_boundary(self):
        with self.assertRaisesRegex(
            ControlStreamError,
            "ToggleMouthMove.*is not paired",
        ):
            validate_mouth_toggle_balance(
                b"\x16\xe2\x80\xa6\x03\x00",
                source_name="unbalanced mouth fixture",
            )
        validate_mouth_toggle_balance(
            b"\x16\xe2\x80\xa6\x16\x03\x00",
            source_name="balanced mouth fixture",
        )

    def test_breaktalk_counts_follow_fe8u_and_event_continuations(self):
        expected = {
            0x08E7: 4,
            0x08E8: 4,
            0x0B35: 0,
            0x0B7B: 2,
        }
        models = build_event_continuation_models()
        self.assertIn(
            ("WM_TEXT", 3),
            {(model.start_kind, model.continuation_count) for model in models[0x08E7]},
        )
        self.assertIn(
            ("TEXTSHOW", 2),
            {(model.start_kind, model.continuation_count) for model in models[0x0B7B]},
        )
        for target_id, count in expected.items():
            self.assertEqual(
                break_talk_count(self.build.english.entries[target_id].encoded_bytes),
                count,
            )
            for locale in ("ja", "zh-Hans"):
                self.assertEqual(
                    break_talk_count(
                        self.build.locale_bundle(locale).entries[target_id].encoded_bytes
                    ),
                    count,
                    (locale, target_id),
                )
        zh_b7b = self.build.locale_bundle("zh-Hans").entries[0x0B7B].source_text
        self.assertEqual(
            zh_b7b.split("[CTRL:0080][CTRL:0004]")[2][:11],
            "[CTRL:000C]",
        )

    def test_requested_control_and_semantic_regressions(self):
        ja = self.build.locale_bundle("ja").entries
        zh = self.build.locale_bundle("zh-Hans").entries

        self.assertIn("[CTRL:0080][CTRL:001F]", zh[0x09CE].source_text)
        self.assertIn("[CTRL:0080][CTRL:001F][CTRL:0005]", zh[0x0A40].source_text)
        self.assertIn("[CTRL:0080][CTRL:001F][CTRL:0005]", zh[0x0D1A].source_text)
        self.assertIn("[CTRL:0080][CTRL:000D][CTRL:000B]城内", zh[0x0A04].source_text)
        self.assertIn("[CTRL:0003][CTRL:000A]梅尔，怎么了？", zh[0x0BAC].source_text)
        self.assertIn("きゃっ[CTRL:0003][CTRL:0008]", ja[0x0AFC].source_text)

        self.assertEqual(
            zh[0x0317].source_text,
            "使用弓从远处攻击敌人的战士[CTRL:0001]装备『弓』",
        )
        self.assertTrue(
            zh[0x08F6].source_text.startswith(
                "伊弗列姆的妹妹陷入了危机。"
                "[CTRL:0103][CTRL:0080][CTRL:0004]"
            )
        )
        self.assertIn("[CTRL:0009]遵命！[CTRL:0003]", zh[0x0929].source_text)
        self.assertIn("帮助外面的旅行者", zh[0x09A5].source_text)
        self.assertNotIn("去救助那些村民", zh[0x09A5].source_text)
        self.assertNotIn(
            "[CTRL:0003][CTRL:0003]",
            zh[0x09C0].source_text,
        )
        for fragment in ("叛国", "火刑柱", "烧死"):
            self.assertIn(fragment, zh[0x0AA5].source_text)
        for fragment in ("伊弗列姆", "杜塞尔", "两件", "战利品"):
            self.assertIn(fragment, zh[0x0ABE].source_text)
        self.assertIn("不知道", zh[0x0BFF].source_text)
        self.assertIn("是否平安", zh[0x0BFF].source_text)
        self.assertNotIn("现在没有危险", zh[0x0BFF].source_text)
        self.assertIn("以前太沉重", zh[0x0C49].source_text)
        self.assertIn("根本穿不了", zh[0x0C49].source_text)
        self.assertIn("我一定不会放过你", zh[0x0C5D].source_text)

        for target_id in (0x0BA9, 0x0BC0, 0x0BFF, 0x0C00, 0x0C10):
            validate_mouth_toggle_balance(
                zh[target_id].encoded_bytes,
                source_name=f"zh-Hans target 0x{target_id:04X}",
            )

        self.assertIn("不低于对方", zh[0x061D].source_text)
        self.assertIn("決められたターン数", ja[0x0638].source_text)
        self.assertIn("规定的回合数", zh[0x0638].source_text)
        self.assertIn("根本不值那么多钱", zh[0x0A9D].source_text)
        self.assertIn("消失的古拉德皇子利昂", zh[0x08EE].source_text)
        self.assertIn("消失的古拉德皇子利昂", zh[0x08F9].source_text)
        self.assertIn("最后一块【圣石】", zh[0x08EF].source_text)
        self.assertIn("最后一块【圣石】", zh[0x08FA].source_text)
        self.assertIn("本应就在这里", zh[0x08F5].source_text)
        self.assertIn("走吧，塞思", zh[0x090D].source_text)
        self.assertIn("亲眼看见", zh[0x092D].source_text)
        self.assertIn("同じ光景を見た", ja[0x092D].source_text)
        self.assertIn("素不相识的贵族", zh[0x0962].source_text)
        self.assertIn("顔も知らぬ貴族", ja[0x0962].source_text)
        for target_id in (0x09B3, 0x09B4):
            self.assertIn("僵尸只是低级魔物", zh[target_id].source_text)
            self.assertIn("根本不是我的对手", zh[target_id].source_text)
        self.assertIn("这个送给你们", zh[0x09B5].source_text)
        self.assertIn("迅速识破并处决", zh[0x0A45].source_text)
        self.assertIn("すぐに処刑", ja[0x0A45].source_text)
        self.assertIn("仍然", zh[0x0A50].source_text)
        self.assertIn("今もなお", ja[0x0A50].source_text)
        self.assertIn("艾瑞珂用剑", zh[0x0A68].source_text)
        self.assertIn("エイリークは剣", ja[0x0A68].source_text)
        self.assertIn("失去理智", zh[0x0A8F].source_text)
        self.assertIn("不会再有流血", zh[0x0ABA].source_text)

        for target_id in (0x0AA2, 0x0AA5, 0x0AA6):
            self.assertIn("格布", zh[target_id].source_text)
            self.assertNotIn("肯普", zh[target_id].source_text)
        self.assertIn("不是战争，而是屠杀", zh[0x0AA1].source_text)
        self.assertIn("比从正面挑战敌方主力军", zh[0x0AA3].source_text)
        self.assertIn("亲手处死", zh[0x0B0D].source_text)
        self.assertIn("艾瑞珂公主", zh[0x0B1B].source_text)
        self.assertIn("不幸身亡", zh[0x0C00].source_text)
        self.assertIn("值得成为例外", zh[0x0C1C].source_text)
        self.assertIn("赌局的门道", zh[0x0CE5].source_text)
        self.assertIn("賭けのやり方", ja[0x0CE5].source_text)

    def test_garcia_dozla_support_arc_is_target_authored_and_structural(self):
        expected_keys = {
            0x0CB6: "game.semantic_correction.msg_cb6",
            0x0CB7: "game.semantic_correction.msg_cb7",
            0x0CB8: "game.semantic_correction.msg_cb8",
        }
        for locale in ("ja", "zh-Hans"):
            entries = self.build.locale_bundle(locale).entries
            for target_id, key in expected_keys.items():
                entry = entries[target_id]
                self.assertEqual(entry.mapping_source_kind, "authored")
                self.assertEqual(entry.locale_provider_kind, "authored")
                self.assertEqual(entry.mapping_source["translation_key"], key)
                self.assertEqual(entry.fallback_kind, "none")
                self.assertEqual(
                    structural_sequence(entry.source_text),
                    structural_sequence(
                        self.build.english.entries[target_id].source_text
                    ),
                    (locale, target_id),
                )
                validate_mouth_toggle_balance(
                    entry.encoded_bytes,
                    source_name=f"{locale} target 0x{target_id:04X}",
                )

        ja = self.build.locale_bundle("ja").entries
        zh = self.build.locale_bundle("zh-Hans").entries
        self.assertIn("弓を学びたかった", ja[0x0CB6].source_text)
        self.assertIn("一緒に学んで", ja[0x0CB6].source_text)
        self.assertIn("魔法はどうじゃ", ja[0x0CB7].source_text)
        self.assertIn("ひげを全部焼いた", ja[0x0CB8].source_text)
        self.assertIn("学习弓术", zh[0x0CB6].source_text)
        self.assertIn("一起练习", zh[0x0CB6].source_text)
        self.assertIn("魔法怎么样", zh[0x0CB7].source_text)
        self.assertIn("胡子全烧光", zh[0x0CB8].source_text)
        for fragment in ("硬币", "骰子", "喝酒", "父亲"):
            for target_id in expected_keys:
                self.assertNotIn(fragment, zh[target_id].source_text)
