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
    TalkFontMetrics,
    _continuation_speakers,
    build_event_continuation_models,
    load_portrait_operand_map,
    tokenize_payload,
    validate_final_payload,
    validate_mouth_toggle_balance,
    validate_talk_line_widths,
)
from scripts.localization.legacy_spacing import LEGACY_SJIS_SPACE_SCALAR


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

    def test_fe8j_space_token_keeps_legacy_width_semantics(self):
        tokens = tokenize_payload(
            b"A\x81\x40B\x00",
            source_name="legacy FE8J spacing",
        )
        self.assertEqual(
            tuple(token.scalar for token in tokens if token.kind == "scalar"),
            (ord("A"), LEGACY_SJIS_SPACE_SCALAR, ord("B")),
        )

    def test_every_final_payload_is_bounded_and_fids_match_fe8u_context(self):
        self.assertEqual(
            self.build.report["control_stream_validation"],
            {
                "fe8u_mouth_topology_validated_payload_count": 656,
                "max_talk_line_width": 237,
                "model_count": 286,
                "modeled_target_count": 275,
                "mouth_balance_validated_payload_count": 6828,
                "portrait_remapped_target_count": 187,
                "talk_line_count": 54714,
                "talk_payload_count": 1976,
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
                    len(_continuation_speakers(tokens)),
                    break_talk_count(entry.encoded_bytes),
                    (bundle.locale, entry.target_id),
                )
                validate_mouth_toggle_balance(
                    entry.encoded_bytes,
                    source_name=f"{bundle.locale} 0x{entry.target_id:04X}",
                )
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
        for boundary in (b"\x03", b"\x0e", b"\x0f", b"\x14", b"\x15", b"\x80\x04"):
            with self.subTest(boundary=boundary):
                with self.assertRaisesRegex(
                    ControlStreamError,
                    "ToggleMouthMove.*is not paired",
                ):
                    validate_mouth_toggle_balance(
                        b"\x16\xe2\x80\xa6" + boundary + b"\x00",
                        source_name="unbalanced mouth fixture",
                    )
        validate_mouth_toggle_balance(
            b"\x16\xe2\x80\xa6\x16\x03\x00",
            source_name="balanced mouth fixture",
        )

    def test_talk_line_gate_honors_face_segments_and_rejects_overflow(self):
        metrics = TalkFontMetrics(
            locale="fixture",
            ascii_widths={},
            cjk_widths={ord("界"): 16},
        )
        fitting = (
            b"\x09"
            + ("界" * 15).encode("utf-8")
            + b"\x03\x0c"
            + ("界" * 15).encode("utf-8")
            + b"\x03\x00"
        )
        self.assertEqual(
            validate_talk_line_widths(
                tokenize_payload(fitting, source_name="fitting talk fixture"),
                source_name="fitting talk fixture",
                metrics=metrics,
            ),
            {
                "max_talk_line_width": 240,
                "talk_line_count": 2,
                "talk_payload_count": 1,
            },
        )
        overflowing = (
            b"\x09" + ("界" * 16).encode("utf-8") + b"\x03\x00"
        )
        with self.assertRaisesRegex(
            ControlStreamError,
            "256px.*240px",
        ):
            validate_talk_line_widths(
                tokenize_payload(
                    overflowing,
                    source_name="overflowing talk fixture",
                ),
                source_name="overflowing talk fixture",
                metrics=metrics,
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

    def test_breaktalk_speaker_state_covers_all_slots_and_transparent_controls(self):
        expected = {
            0x09BE: (0x0F,),
            0x09BF: (0x0F,),
            0x0B89: (0x09,),
            0x09CC: (0x0C,),
            0x0B66: (0x0C, 0x0C, 0x0C),
            0x0B67: (0x0C, 0x0C, 0x0C),
            0x0B82: (0x0C, 0x0C),
        }
        for target_id, speakers in expected.items():
            for locale, entry in (
                ("en", self.build.english.entries[target_id]),
                ("ja", self.build.locale_bundle("ja").entries[target_id]),
                (
                    "zh-Hans",
                    self.build.locale_bundle("zh-Hans").entries[target_id],
                ),
            ):
                self.assertEqual(
                    _continuation_speakers(
                        tokenize_payload(
                            entry.encoded_bytes,
                            source_name=f"{locale} target 0x{target_id:04X}",
                        )
                    ),
                    speakers,
                    (locale, target_id),
                )

        for slot in range(0x08, 0x10):
            payload = bytes((slot,)) + b"A\x80\x04\x17\x16\x16\x80\x16\x80\x21B\x00"
            self.assertEqual(
                _continuation_speakers(
                    tokenize_payload(payload, source_name=f"slot 0x{slot:02X}")
                ),
                (slot,),
            )

        payload_controls = (
            b"\x0fA\x80\x04\x0e\x10\x08\x01\x80\x01\x0aB\x00"
        )
        self.assertEqual(
            _continuation_speakers(
                tokenize_payload(
                    payload_controls,
                    source_name="payload-control speaker fixture",
                )
            ),
            (0x0E,),
        )

    def test_breaktalk_speaker_validator_rejects_injected_mismatches(self):
        metrics = TalkFontMetrics(
            locale="fixture",
            ascii_widths={ord("A"): 8, ord("B"): 8},
            cjk_widths={},
        )
        mismatches = (
            (
                b"\x0fA\x80\x04\x17B\x00",
                b"\x0eA\x80\x04\x17B\x00",
            ),
            (
                b"\x0cA\x80\x04\x17\x16\x16\x80\x16\x80\x21B\x00",
                b"\x09A\x80\x04\x17\x16\x16\x80\x16\x80\x21B\x00",
            ),
            (
                b"\x0fA\x80\x04\x0f\x10\x08\x01B\x00",
                b"\x0eA\x80\x04\x0e\x10\x08\x01B\x00",
            ),
        )
        for english_payload, localized_payload in mismatches:
            with self.subTest(
                english_payload=english_payload,
                localized_payload=localized_payload,
            ):
                with self.assertRaisesRegex(
                    ControlStreamError,
                    "continuation speakers after BreakTalk",
                ):
                    validate_final_payload(
                        localized_payload,
                        english_payload=english_payload,
                        target_id=0xFFFF,
                        locale="fixture",
                        control_domain=CONTROL_DOMAIN_FE8U,
                        portrait_map=self.portrait_map,
                        talk_metrics=metrics,
                    )

    def test_requested_control_and_semantic_regressions(self):
        ja = self.build.locale_bundle("ja").entries
        zh = self.build.locale_bundle("zh-Hans").entries

        self.assertEqual(ja[0x07D5].source_text, "復興の女王　エイリーク")
        self.assertEqual(zh[0x07D5].source_text, "复兴女王 艾瑞珂")
        self.assertEqual(ja[0x07E9].source_text, "策謀の王　ヒーニアス")
        self.assertEqual(zh[0x07E9].source_text, "谋略之王 希尼亚斯")
        self.assertEqual(ja[0x0815].source_text, "天翔ける女王　ターナ")
        self.assertEqual(zh[0x0815].source_text, "飞翼女王 塔娜")

        self.assertIn("ルネス復興", ja[0x07D6].source_text)
        self.assertNotIn("大陸復興", ja[0x07D6].source_text)
        self.assertIn("重建祖国", zh[0x07D6].source_text)
        self.assertNotIn("复兴大陆", zh[0x07D6].source_text)
        self.assertIn("その献身", ja[0x07FA].source_text)
        self.assertIn("民衆の英雄", ja[0x07FA].source_text)
        self.assertIn("自我牺牲", zh[0x07FA].source_text)
        self.assertIn("民间英雄", zh[0x07FA].source_text)
        self.assertIn("二度と戻ることはなかった", ja[0x07FC].source_text)
        self.assertIn("从此再[CTRL:0001]也没有回来", zh[0x07FC].source_text)
        self.assertNotIn("休業", ja[0x07FC].source_text)
        self.assertNotIn("退意", zh[0x07FC].source_text)

        self.assertIn(
            "虽然还有些零星抵抗，但我想"
            "[CTRL:0001]战争本身已经结束了。"
            "[CTRL:0003]",
            zh[0x0B29].source_text,
        )
        self.assertNotIn("帝都的战斗可以算是结束了", zh[0x0B29].source_text)

        self.assertIn("[CTRL:0080][CTRL:001F]", zh[0x09CE].source_text)
        self.assertIn("[CTRL:0080][CTRL:001F][CTRL:0005]", zh[0x0A40].source_text)
        self.assertIn("[CTRL:0080][CTRL:001F][CTRL:0005]", zh[0x0D1A].source_text)
        self.assertIn("[CTRL:0080][CTRL:000D][CTRL:000B]城内", zh[0x0A04].source_text)
        bac_payload = zh[0x0BAC].encoded_bytes
        self.assertEqual(bac_payload.count(b"\x0A" + "怎么了？".encode("utf-8")), 1)
        self.assertEqual(
            bac_payload.count(b"\x0A" + "梅尔，怎么了？".encode("utf-8")),
            1,
        )
        self.assertTrue(
            zh[0x0D05].source_text.startswith(
                "[CTRL:0009][CTRL:0010][CTRL:0118]"
                "[CTRL:000C][CTRL:0010][CTRL:012B]"
                "[CTRL:000C][CTRL:0017]"
                "凯尔，你还撑得住吗？"
                "[CTRL:0003][CTRL:0017][CTRL:0009]"
                "战况很艰苦。"
            )
        )
        self.assertIn("一定还会去看弗雷利亚的海", zh[0x0D05].source_text)
        self.assertTrue(zh[0x0D05].source_text.endswith("[CTRL:0003]"))
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
        self.assertIn("但不想再打赌了", zh[0x0CE5].source_text)
        self.assertNotIn("无论赌博抑或练习我均愿奉陪", zh[0x0CE5].source_text)
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
        self.assertIn("断食を破る食事", ja[0x0CB6].source_text)
        self.assertIn("朝食とは、", ja[0x0CB6].source_text)
        self.assertIn("断食だ。", ja[0x0CB6].source_text)
        self.assertIn("魔法はどうじゃ", ja[0x0CB7].source_text)
        self.assertIn("ひげを全部焼いた", ja[0x0CB8].source_text)
        self.assertIn("学习弓术", zh[0x0CB6].source_text)
        self.assertIn("一起练习", zh[0x0CB6].source_text)
        self.assertIn("打破断食的一餐", zh[0x0CB6].source_text)
        self.assertIn("早餐要“打破”什么？", zh[0x0CB6].source_text)
        self.assertIn("断食。", zh[0x0CB6].source_text)
        self.assertIn("魔法怎么样", zh[0x0CB7].source_text)
        self.assertIn("胡子全烧光", zh[0x0CB8].source_text)
        for fragment in ("硬币", "骰子", "喝酒", "父亲"):
            for target_id in expected_keys:
                self.assertNotIn(fragment, zh[target_id].source_text)

    def test_audited_mouth_streams_and_rewrapped_lines_are_final(self):
        requests = {
            "ja": (0x0C65,),
            "zh-Hans": (
                0x09CA,
                0x0AFB,
                0x0B47,
                0x0B51,
                0x0C65,
                0x0CAA,
                0x0CAB,
                0x0CC4,
                0x0CD7,
                0x0CD8,
                0x0CEE,
                0x0D21,
            ),
        }
        for locale, target_ids in requests.items():
            entries = self.build.locale_bundle(locale).entries
            for target_id in target_ids:
                self.assertEqual(entries[target_id].locale_provider_kind, "indexed")
                validate_mouth_toggle_balance(
                    entries[target_id].encoded_bytes,
                    source_name=f"{locale} target 0x{target_id:04X}",
                )

        ja = self.build.locale_bundle("ja").entries
        zh = self.build.locale_bundle("zh-Hans").entries
        self.assertIn(
            "あなたも私と同じ光景を見たはずです。"
            "[CTRL:0001]民たちは　グラドの兵士に",
            ja[0x092D].source_text,
        )
        self.assertIn(
            "而发动这场战争呢？"
            "[CTRL:0103]士兵们毫无意义地战死，"
            "[CTRL:0001]这不是战争，而是屠杀！",
            zh[0x0AA1].source_text,
        )
