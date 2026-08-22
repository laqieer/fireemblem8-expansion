import re
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.modernize.tests.test_save_format_meta_bytes_native import (
    _extract_c_function,
)


ROOT = Path(__file__).resolve().parents[3]


class BanimPolicyNativeTests(unittest.TestCase):
    def test_real_policy_validator_accepts_reference_and_rejects_bounds(self):
        header = (ROOT / "include/banim_presentation.h").read_text(encoding="utf-8")
        source = (ROOT / "src/banim_presentation.c").read_text(encoding="utf-8")
        struct_match = re.search(
            r"struct BanimPresentationPolicy\s*\{.*?\n\};",
            header,
            re.DOTALL,
        )
        self.assertIsNotNone(struct_match)
        validate_fn = _extract_c_function(source, "BanimPresentationPolicy_Validate")

        probe = f"""
#include <stdint.h>
#include <stddef.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint8_t bool8;
#define TRUE 1
#define FALSE 0
#define PLAY_ANIMCONF_ON 0
#define PLAY_ANIMCONF_OFF 1
#define PLAY_ANIMCONF_SOLO_ANIM 2
#define PLAY_ANIMCONF_ON_UNIQUE_BG 3
#define BANIM_PRESENTATION_POLICY_COUNT 5
#define BANIM_PRESENTATION_BACKGROUND_STANDARD 1
#define BANIM_PRESENTATION_DAMAGE_NUMBERS_OFF 2
#define BANIM_PRESENTATION_HIT_EFFECT_OFF 2
#define BANIM_PRESENTATION_MAX_HIT_EFFECT_PALETTE 1
#define BANIM_PRESENTATION_MAX_EFFECT_SPEED 8
#define BANIM_PRESENTATION_MAX_PALETTE_SLOTS 16
#define BANIM_PRESENTATION_MAX_OAM_ENTRIES 128
#define BANIM_PRESENTATION_MAX_VRAM_BYTES 0x8000
#define BANIM_PRESENTATION_MAX_TIMING_FRAMES 255
{struct_match.group(0)}
{validate_fn}
int main(void) {{
    struct BanimPresentationPolicy reference =
        {{2, 0, 0, 1, 1, 1, 2, 8, 64, 0, 0x3000, 1, 120}};
    struct BanimPresentationPolicy invalid = reference;
    invalid.vramBytes = 0x8001;
    struct BanimPresentationPolicy invalid_palette_slots = reference;
    invalid_palette_slots.paletteSlots = 17;
    struct BanimPresentationPolicy invalid_oam = reference;
    invalid_oam.oamEntries = 129;
    struct BanimPresentationPolicy invalid_extension = reference;
    invalid_extension.effectSpeed = 9;
    struct BanimPresentationPolicy invalid_palette = reference;
    invalid_palette.hitEffectPalette = 2;
    struct BanimPresentationPolicy invalid_timing = reference;
    invalid_timing.timingMinFrames = 121;
    return BanimPresentationPolicy_Validate(&reference) == TRUE
        && BanimPresentationPolicy_Validate(&invalid) == FALSE
        && BanimPresentationPolicy_Validate(&invalid_palette_slots) == FALSE
        && BanimPresentationPolicy_Validate(&invalid_oam) == FALSE
        && BanimPresentationPolicy_Validate(&invalid_extension) == FALSE
        && BanimPresentationPolicy_Validate(&invalid_palette) == FALSE
        && BanimPresentationPolicy_Validate(&invalid_timing) == FALSE ? 0 : 1;
}}
"""
        artifact_dir = ROOT / "build" / "test-artifacts" / "banim-policy-native"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        source_path = artifact_dir / "probe.c"
        binary_path = artifact_dir / "probe"
        source_path.write_text(probe, encoding="utf-8")
        try:
            subprocess.run(
                ["cc", "-std=c99", str(source_path), "-o", str(binary_path)],
                check=True,
                cwd=ROOT,
            )
            subprocess.run([str(binary_path)], check=True)
        finally:
            shutil.rmtree(artifact_dir, ignore_errors=True)

    def test_runtime_policy_seams_make_reduced_deterministically_different(self):
        header = (ROOT / "include/banim_presentation.h").read_text(encoding="utf-8")
        source = (ROOT / "src/banim_presentation.c").read_text(encoding="utf-8")
        struct_match = re.search(
            r"struct BanimPresentationPolicy\s*\{.*?\n\};",
            header,
            re.DOTALL,
        )
        self.assertIsNotNone(struct_match)
        function_names = (
            "BanimPresentationPolicy_FromAnimationOption",
            "BanimPresentationPolicy_UsesBackgrounds",
            "BanimPresentationPolicy_UsesHitEffects",
            "BanimPresentationPolicy_UsesHitEffectPalette",
            "BanimPresentationPolicy_ShowsHitNumbers",
            "BanimPresentationPolicy_ShowsDamageNumbers",
            "BanimPresentationPolicy_ShowsCritNumbers",
            "BanimPresentationPolicy_AdjustEffectDuration",
            "BanimPresentationPolicy_ApplyDamageNumberStyle",
        )
        functions = "\n".join(_extract_c_function(source, name) for name in function_names)
        probe = f"""
#include <stdint.h>
#include <stddef.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef int16_t s16;
typedef uint8_t bool8;
#define TRUE 1
#define FALSE 0
#define BANIM_PRESENTATION_BACKGROUND_NONE 0
#define BANIM_PRESENTATION_BACKGROUND_STANDARD 1
#define BANIM_PRESENTATION_DAMAGE_NUMBERS_STANDARD 0
#define BANIM_PRESENTATION_DAMAGE_NUMBERS_REDUCED 1
#define BANIM_PRESENTATION_DAMAGE_NUMBERS_OFF 2
#define BANIM_PRESENTATION_HIT_EFFECT_STANDARD 0
#define BANIM_PRESENTATION_HIT_EFFECT_REDUCED 1
#define BANIM_PRESENTATION_HIT_EFFECT_OFF 2
#define BANIM_PRESENTATION_HIT_EFFECT_PALETTE_STANDARD 0
#define BANIM_PRESENTATION_HIT_EFFECT_PALETTE_OFF 1
#define PLAY_ANIMCONF_ON 0
#define PLAY_ANIMCONF_OFF 1
#define PLAY_ANIMCONF_SOLO_ANIM 2
#define PLAY_ANIMCONF_ON_UNIQUE_BG 3
#define BANIM_PRESENTATION_POLICY_DEFAULT 0
#define BANIM_PRESENTATION_POLICY_WITH_BACKGROUNDS 1
#define BANIM_PRESENTATION_POLICY_OFF 3
#define BANIM_PRESENTATION_POLICY_SOLO 4
{struct_match.group(0)}
{functions}
int main(void)
{{
    struct BanimPresentationPolicy standard =
        {{0, 0, 0, 0, 0, 0, 1, 8, 96, 0, 0x4000, 1, 255}};
    struct BanimPresentationPolicy reduced =
        {{2, 0, 0, 1, 1, 1, 2, 8, 64, 0, 0x3000, 1, 120}};
    s16 standardHit = 80;
    s16 standardDamage = 20;
    s16 standardCrit = 5;
    s16 reducedHit = 80;
    s16 reducedDamage = 20;
    s16 reducedCrit = 5;

    BanimPresentationPolicy_ApplyDamageNumberStyle(
        &standard, &standardHit, &standardDamage, &standardCrit);
    BanimPresentationPolicy_ApplyDamageNumberStyle(
        &reduced, &reducedHit, &reducedDamage, &reducedCrit);
    struct BanimPresentationPolicy off =
        {{3, 1, 0, 2, 2, 0, 0, 0, 0, 0, 0, 1, 1}};
    s16 offHit = 80;
    s16 offDamage = 20;
    s16 offCrit = 5;
    BanimPresentationPolicy_ApplyDamageNumberStyle(
        &off, &offHit, &offDamage, &offCrit);
    return !(
        BanimPresentationPolicy_FromAnimationOption(PLAY_ANIMCONF_ON)
            == BANIM_PRESENTATION_POLICY_DEFAULT
        && BanimPresentationPolicy_FromAnimationOption(PLAY_ANIMCONF_ON_UNIQUE_BG)
            == BANIM_PRESENTATION_POLICY_WITH_BACKGROUNDS
        && BanimPresentationPolicy_FromAnimationOption(PLAY_ANIMCONF_OFF)
            == BANIM_PRESENTATION_POLICY_OFF
        && BanimPresentationPolicy_FromAnimationOption(PLAY_ANIMCONF_SOLO_ANIM)
            == BANIM_PRESENTATION_POLICY_SOLO
        && !BanimPresentationPolicy_UsesBackgrounds(&standard)
        && !BanimPresentationPolicy_UsesBackgrounds(&reduced)
        && BanimPresentationPolicy_UsesHitEffects(&standard)
        && BanimPresentationPolicy_UsesHitEffects(&reduced)
        && BanimPresentationPolicy_UsesHitEffectPalette(&standard)
        && !BanimPresentationPolicy_UsesHitEffectPalette(&reduced)
        && BanimPresentationPolicy_ShowsHitNumbers(&standard)
        && !BanimPresentationPolicy_ShowsHitNumbers(&reduced)
        && BanimPresentationPolicy_ShowsDamageNumbers(&standard)
        && BanimPresentationPolicy_ShowsDamageNumbers(&reduced)
        && BanimPresentationPolicy_ShowsCritNumbers(&standard)
        && !BanimPresentationPolicy_ShowsCritNumbers(&reduced)
        && standardHit == 80
        && standardDamage == 20
        && standardCrit == 5
        && reducedHit == -1
        && reducedDamage == 20
        && reducedCrit == -1
        && BanimPresentationPolicy_AdjustEffectDuration(&standard, 8) == 8
        && BanimPresentationPolicy_AdjustEffectDuration(&reduced, 8) == 4
        && BanimPresentationPolicy_AdjustEffectDuration(&reduced, 5) == 2
        && !BanimPresentationPolicy_UsesHitEffects(&off)
        && !BanimPresentationPolicy_UsesHitEffectPalette(&off)
        && offHit == -1
        && offDamage == -1
        && offCrit == -1);
}}
"""
        artifact_dir = ROOT / "build" / "test-artifacts" / "banim-policy-runtime"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        source_path = artifact_dir / "probe.c"
        binary_path = artifact_dir / "probe"
        source_path.write_text(probe, encoding="utf-8")
        try:
            subprocess.run(
                ["cc", "-std=c99", str(source_path), "-o", str(binary_path)],
                check=True,
                cwd=ROOT,
            )
            subprocess.run([str(binary_path)], check=True)
        finally:
            shutil.rmtree(artifact_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
