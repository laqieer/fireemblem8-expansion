"""Semantic catalog-to-scenario coverage for issue #56 localization cases.

This test reads only the canonical tester-case registry and checked-in
scenario/fingerprint data. It deliberately does not search runtime source text
or recreate runtime logic: host-native drivers and libmGBA remain the behavior
authorities. Its purpose is to fail when an indexed case loses the semantic
selector, stable-ID, repair, no-wipe, or real-reset evidence it promises.
"""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = ROOT / "docs" / "test-cases" / "registry.json"
SCENARIOS_DIR = ROOT / "tools" / "gba-playtest" / "scenarios"
FINGERPRINTS_DIR = ROOT / "tools" / "gba-playtest" / "fingerprints"
PROBE = "gExpansionLanguageMenuProbe"

CASE_IDS = {
    "TC-LOCALIZATION-001",
    "TC-LOCALIZATION-002",
    "TC-LOCALIZATION-003",
    "TC-LOCALIZATION-004",
    "TC-LOCALIZATION-005",
    "TC-LOCALIZATION-006",
    "TC-LOCALIZATION-007",
    "TC-LOCALIZATION-008",
}

FEATURE_CASES = {
    "expansion-locale-selection": {
        "TC-LOCALIZATION-001",
        "TC-LOCALIZATION-002",
    },
    "full-game-localization": {"TC-LOCALIZATION-003"},
    "locale-preference-persistence": {"TC-LOCALIZATION-004"},
    "localized-text-input-ui": {
        "TC-LOCALIZATION-005",
        "TC-LOCALIZATION-006",
        "TC-LOCALIZATION-007",
    },
    "localization-profile-validation": {"TC-LOCALIZATION-008"},
}


def load_json(directory, name):
    return json.loads((directory / name).read_text(encoding="utf-8"))


def checkpoints_by_name(scenario):
    return {checkpoint["name"]: checkpoint for checkpoint in scenario["checkpoints"]}


def probe_values(checkpoint):
    return {
        probe["address"]: probe["expected"]
        for probe in checkpoint.get("probes", [])
        if "expected" in probe
    }


def expect_probe(test_case, checkpoint, offset, value):
    probes = probe_values(checkpoint)
    test_case.assertEqual(probes.get(f"{PROBE}+{offset}"), value)


class LocaleTesterCaseSemanticTests(unittest.TestCase):
    def test_registry_owns_the_eight_stable_localization_cases(self):
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        features = {feature["id"]: feature for feature in registry["features"]}
        cases = {case["id"]: case for case in registry["cases"]}

        self.assertTrue(CASE_IDS.issubset(cases))
        for feature_id, expected_cases in FEATURE_CASES.items():
            self.assertIn(feature_id, features)
            self.assertEqual(set(features[feature_id]["required_cases"]), expected_cases)
            for case_id in expected_cases:
                self.assertEqual(cases[case_id]["feature_id"], feature_id)
                self.assertTrue(cases[case_id]["automation"])
                self.assertTrue(cases[case_id]["profiles"])

    def test_default_single_locale_scenario_has_no_selector(self):
        scenario = load_json(
            SCENARIOS_DIR, "locale-blank-sram-no-selector-default-modern-debug.json"
        )
        checkpoint = checkpoints_by_name(scenario)["post-startup-settled"]

        expect_probe(self, checkpoint, "0x00", "0x00")
        expect_probe(self, checkpoint, "0x02", "0x00")
        expect_probe(self, checkpoint, "0x05", "0x05")
        expect_probe(self, checkpoint, "0x08", "0x01")

    def test_multi_locale_selector_commits_pseudo_stable_id(self):
        scenario = load_json(
            SCENARIOS_DIR, "locale-selector-multi-switch-qps-modern-debug.json"
        )
        checkpoints = checkpoints_by_name(scenario)

        expect_probe(self, checkpoints["selector-shown-before-pick"], "0x00", "0x01")
        expect_probe(self, checkpoints["selector-shown-before-pick"], "0x08", "0x02")
        committed = checkpoints["qps-ploc-committed-and-dismissed"]
        expect_probe(self, committed, "0x00", "0x00")
        expect_probe(self, committed, "0x06", "0x07")
        expect_probe(self, committed, "0x07", "0x07")
        expect_probe(self, committed, "0x0a", "0x0001")

    def test_real_locale_selectors_commit_stable_locale_ids(self):
        cases = (
            ("locale-cjk-first-start-ja-modern-debug.json", "japanese-committed", "0x01", "0x03"),
            (
                "locale-cjk-first-start-zh-hans-modern-debug.json",
                "simplified-chinese-committed",
                "0x02",
                "0x03",
            ),
            ("locale-eu-first-start-fr-modern-debug.json", "french-committed", "0x03", "0x05"),
        )
        for scenario_name, checkpoint_name, locale_id, enabled_count in cases:
            with self.subTest(scenario=scenario_name):
                scenario = load_json(SCENARIOS_DIR, scenario_name)
                checkpoint = checkpoints_by_name(scenario)[checkpoint_name]
                expect_probe(self, checkpoint, "0x00", "0x00")
                expect_probe(self, checkpoint, "0x06", locale_id)
                expect_probe(self, checkpoint, "0x07", locale_id)
                expect_probe(self, checkpoint, "0x08", enabled_count)
                expect_probe(self, checkpoint, "0x12", "0x00")

    def test_repair_matrix_keeps_prompt_no_wipe_and_real_reset_evidence(self):
        expected_states = {
            "unset": "0x00",
            "corrupt": "0x01",
            "unknown": "0x02",
            "disabled": "0x03",
        }
        for state, prefs_state in expected_states.items():
            for config in ("debug", "release"):
                name = f"locale-repair-{state}-multi-modern-{config}.json"
                with self.subTest(scenario=name):
                    scenario = load_json(SCENARIOS_DIR, name)
                    checkpoints = checkpoints_by_name(scenario)
                    pre = checkpoints["pre-repair-selector-shown"]
                    post = checkpoints["post-repair-committed"]
                    reset = checkpoints["post-reset-selector-skipped-en-restored"]

                    expect_probe(self, pre, "0x00", "0x01")
                    expect_probe(self, pre, "0x01", "0x00")
                    expect_probe(self, pre, "0x05", prefs_state)
                    expect_probe(self, pre, "0x12", "0x01")
                    expect_probe(self, post, "0x00", "0x00")
                    expect_probe(self, post, "0x0a", "0x0001")
                    expect_probe(self, post, "0x12", "0x00")
                    expect_probe(self, reset, "0x02", "0x00")
                    expect_probe(self, reset, "0x05", "0x05")
                    expect_probe(self, reset, "0x0c", "0x0001")
                    self.assertTrue(
                        pre.get("sram_hash") or checkpoints[
                            "pre-runtimeinit-sram-baseline"
                        ].get("sram_hash")
                    )
                    self.assertTrue(post.get("sram_hash"))
                    self.assertEqual(
                        checkpoints["pre-runtimeinit-sram-baseline"][
                            "sram_hash_exclude_ranges"
                        ],
                        post["sram_hash_exclude_ranges"],
                    )
                    self.assertTrue(
                        any(
                            set(frame["keys"]) == {"A", "B", "SELECT", "START"}
                            for frame in scenario["frames"]
                        )
                    )

    def test_cjk_persistence_uses_real_reset_with_sram_retention(self):
        scenario = load_json(
            SCENARIOS_DIR, "locale-cjk-softreset-persistence-modern-debug.json"
        )
        checkpoints = checkpoints_by_name(scenario)
        committed = checkpoints["pre-reset-zh-hans-committed"]
        fresh = checkpoints["post-reset-ewram-fresh-sram-retained"]
        restored = checkpoints["post-reset-selector-skipped-zh-hans-restored"]

        expect_probe(self, committed, "0x07", "0x02")
        expect_probe(self, fresh, "0x07", "0x00")
        self.assertEqual(probe_values(fresh)["0x0e0073d6"], "0x02")
        expect_probe(self, restored, "0x02", "0x00")
        expect_probe(self, restored, "0x07", "0x02")
        self.assertTrue(
            any(
                set(frame["keys"]) == {"A", "B", "SELECT", "START"}
                for frame in scenario["frames"]
            )
        )

    def test_semantic_scenarios_have_matched_fingerprints(self):
        scenario_names = (
            "locale-blank-sram-no-selector-default-modern-debug.json",
            "locale-selector-multi-switch-qps-modern-debug.json",
            "locale-cjk-first-start-ja-modern-debug.json",
            "locale-cjk-first-start-zh-hans-modern-debug.json",
            "locale-cjk-softreset-persistence-modern-debug.json",
            "locale-eu-first-start-fr-modern-debug.json",
        )
        for name in scenario_names:
            with self.subTest(scenario=name):
                scenario = load_json(SCENARIOS_DIR, name)
                fingerprint = load_json(FINGERPRINTS_DIR, name)
                self.assertEqual(fingerprint["scenario"], scenario["name"])
                self.assertEqual(
                    [checkpoint["name"] for checkpoint in fingerprint["checkpoints"]],
                    [checkpoint["name"] for checkpoint in scenario["checkpoints"]],
                )


if __name__ == "__main__":
    unittest.main()
