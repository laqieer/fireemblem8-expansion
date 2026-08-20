import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class UiRegistryContractTests(unittest.TestCase):
    def test_battle_policy_registry_has_default_and_reduced_reference(self):
        source = (ROOT / "src/banim_presentation.c").read_text(encoding="utf-8")
        header = (ROOT / "include/banim_presentation.h").read_text(encoding="utf-8")
        self.assertIn("BANIM_PRESENTATION_POLICY_DEFAULT 0", header)
        self.assertIn("BANIM_PRESENTATION_POLICY_REDUCED 2", header)
        self.assertIn("BANIM_PRESENTATION_POLICY_REDUCED,", source)
        self.assertIn("0x3000, 1, 120", source)
        self.assertIn("BanimPresentationPolicy_ValidateAll", source)

    def test_battle_policy_fields_are_connected_to_existing_animation_seams(self):
        intro = (ROOT / "src/banim-ekrbattleintro.c").read_text(encoding="utf-8")
        hit_effects = (ROOT / "src/banim-ekrutils.c").read_text(encoding="utf-8")
        damage_effect = (ROOT / "src/banim-efxhit.c").read_text(encoding="utf-8")

        self.assertIn("BanimPresentationPolicy_UsesBackgrounds", intro)
        self.assertIn("BanimPresentationPolicy_ApplyDamageNumberStyle", intro)
        self.assertIn("BanimPresentationPolicy_UsesHitEffects", hit_effects)
        self.assertIn("BanimPresentationPolicy_UsesHitEffectPalette", hit_effects)
        self.assertIn("BanimPresentationPolicy_AdjustEffectDuration", hit_effects)
        self.assertIn("BanimPresentationPolicy_AdjustEffectDuration", damage_effect)

    def test_animation_option_mapping_preserves_each_saved_enum(self):
        source = (ROOT / "src/banim_presentation.c").read_text(encoding="utf-8")

        expected_mappings = (
            ("PLAY_ANIMCONF_ON", "BANIM_PRESENTATION_POLICY_DEFAULT"),
            ("PLAY_ANIMCONF_ON_UNIQUE_BG", "BANIM_PRESENTATION_POLICY_WITH_BACKGROUNDS"),
            ("PLAY_ANIMCONF_OFF", "BANIM_PRESENTATION_POLICY_OFF"),
            ("PLAY_ANIMCONF_SOLO_ANIM", "BANIM_PRESENTATION_POLICY_SOLO"),
        )
        for animation_option, policy in expected_mappings:
            self.assertRegex(
                source,
                r"case %s:\s*return %s;" % (animation_option, policy),
            )

    def test_default_animation_path_remains_the_existing_option_surface(self):
        source = (ROOT / "src/uiconfig.c").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("ExpansionUiPrefs_NotifyAnimationOptionChange"), 4)
        self.assertNotIn("ExpansionUiPrefs_NotifyAnimationOptionChange(newValue)", source)
        self.assertEqual(
            source.count(
                "ExpansionUiPrefs_NotifyAnimationOptionChange(gPlaySt.config.animationType)"
            ),
            4,
        )
        self.assertIn("gPlaySt.config.animationType = PLAY_ANIMCONF_ON;", source)
        self.assertIn("gPlaySt.config.animationType = PLAY_ANIMCONF_ON_UNIQUE_BG;", source)
        expected_assignments = (
            (0, "PLAY_ANIMCONF_ON"),
            (1, "PLAY_ANIMCONF_ON_UNIQUE_BG"),
            (2, "PLAY_ANIMCONF_OFF"),
            (3, "PLAY_ANIMCONF_SOLO_ANIM"),
        )
        for row, animation in expected_assignments:
            match = re.search(
                r"case %d:\s*gPlaySt\.config\.animationType = %s;"
                r"\s*#ifdef MODERN\s*"
                r"ExpansionUiPrefs_NotifyAnimationOptionChange\(gPlaySt\.config\.animationType\);"
                % (row, animation),
                source,
                re.DOTALL,
            )
            self.assertIsNotNone(
                match,
                "animation row %d must persist its assigned %s enum value"
                % (row, animation),
            )

    def test_ui_preferences_are_bounded_and_threat_range_is_disabled_by_default(self):
        source = (ROOT / "src/expansion_ui_prefs.c").read_text(encoding="utf-8")
        header = (ROOT / "include/expansion_ui_prefs.h").read_text(encoding="utf-8")
        self.assertIn("EXPANSION_UI_PREF_COUNT 2", header)
        self.assertRegex(
            source,
            r"\[EXPANSION_UI_PREF_THREAT_RANGE\][\s\S]*?"
            r"EXPANSION_UI_PREF_THREAT_RANGE,\s*0,\s*1",
        )
        self.assertIn("value > descriptor->maxValue", source)

    def test_prefs_reserved_selection_bytes_are_checked(self):
        source = (ROOT / "src/bmsave-lib.c").read_text(encoding="utf-8")
        self.assertIn("prefs->reserved[0] > 4", source)
        self.assertIn("prefs->reserved[3] != 0", source)
        self.assertIn("ExpansionUserPrefs_StoreRawWithSelections", source)

    def test_battle_policy_selection_round_trips_through_saved_preferences(self):
        source = (ROOT / "src/expansion_ui_prefs.c").read_text(encoding="utf-8")

        self.assertIn("ExpansionUserPrefs_GetSelections(&policyId, &utilityFlags)", source)
        self.assertIn("BanimPresentationPolicy_Select(policyId)", source)
        self.assertIn("ExpansionUserPrefs_StoreSelections(", source)
        self.assertIn("BanimPresentationPolicy_GetCurrent()->id", source)


class SaveLoadUiPrefsContractTests(unittest.TestCase):
    def test_game_and_suspend_loads_share_one_post_load_ui_prefs_hook(self):
        source = (ROOT / "src/bmsave.c").read_text(encoding="utf-8")
        hook = "ApplySavedGlobalUiPrefsAfterLoad"

        self.assertEqual(source.count("static void %s" % hook), 2)
        self.assertIn("ExpansionUiPrefs_ApplySaved();", source)

        for function in ("ReadGameSave", "ReadSuspendSave"):
            match = re.search(
                r"void %s\([^)]*\)\s*\{(.*?)\n\}" % function,
                source,
                re.DOTALL,
            )
            self.assertIsNotNone(match, "%s() not found" % function)
            body = match.group(1)
            self.assertEqual(body.count("%s();" % hook), 1)
            self.assertTrue(
                body.rstrip().endswith("%s();" % hook),
                "%s() must invoke the shared hook before returning" % function,
            )


if __name__ == "__main__":
    unittest.main()
