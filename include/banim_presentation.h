#ifndef GUARD_BANIM_PRESENTATION_H
#define GUARD_BANIM_PRESENTATION_H

#include "global.h"

#define BANIM_PRESENTATION_POLICY_DEFAULT 0
#define BANIM_PRESENTATION_POLICY_WITH_BACKGROUNDS 1
#define BANIM_PRESENTATION_POLICY_REDUCED 2
#define BANIM_PRESENTATION_POLICY_OFF 3
#define BANIM_PRESENTATION_POLICY_SOLO 4
#define BANIM_PRESENTATION_POLICY_COUNT 5
#define BANIM_PRESENTATION_POLICY_INVALID 0xFF

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

#define BANIM_PRESENTATION_MAX_PALETTE_SLOTS 16
#define BANIM_PRESENTATION_MAX_HIT_EFFECT_PALETTE BANIM_PRESENTATION_HIT_EFFECT_PALETTE_OFF
#define BANIM_PRESENTATION_MAX_EFFECT_SPEED 8
#define BANIM_PRESENTATION_MAX_OAM_ENTRIES 128
#define BANIM_PRESENTATION_MAX_VRAM_BYTES 0x8000
#define BANIM_PRESENTATION_MAX_TIMING_FRAMES 255

struct BanimPresentationPolicy
{
    /* 00 */ u8 id;
    /* 01 */ u8 animationOption;
    /* 02 */ u8 backgroundMode;
    /* 03 */ u8 damageNumberStyle;
    /* 04 */ u8 hitEffectStyle;
    /* 05 */ u8 hitEffectPalette;
    /* 06 */ u8 effectSpeed;
    /* 07 */ u8 paletteSlots; /* metadata-only; not a selectable runtime setting */
    /* 08 */ u8 oamEntries; /* metadata-only; not a selectable runtime setting */
    /* 09 */ u8 reserved; /* unsupported extension slots must remain zero */
    /* 0A */ u16 vramBytes; /* metadata-only; not a selectable runtime setting */
    /* 0C */ u16 timingMinFrames;
    /* 0E */ u16 timingMaxFrames;
};

#if FE8_AUTOPLAY_EVENT_TRACE_TEST
struct BanimPresentationPolicyHarnessProbe
{
    /* 00 */ u32 currentPolicyId;
    /* 04 */ u32 getCurrentCallCount;
};

extern struct BanimPresentationPolicyHarnessProbe gBanimPresentationPolicyHarnessProbe;
#endif

#ifdef BANIM_PRESENTATION_RUNTIME_PROBE_POLICY
struct BanimPresentationRuntimeProbe
{
    /* 00 */ u32 policyId;
    /* 04 */ u32 realHitPathObserved;
    /* 08 */ u32 hitEffectsEnabled;
    /* 0C */ u32 paletteFlashEnabled;
    /* 10 */ u32 paletteFlashStarted;
    /* 14 */ u32 hitNumbersVisible;
    /* 18 */ u32 damageNumbersVisible;
    /* 1C */ u32 critNumbersVisible;
    /* 20 */ u32 autoLaunchArmed;
};

extern struct BanimPresentationRuntimeProbe gBanimPresentationRuntimeProbe;

void BanimPresentationPolicy_RuntimeProbePrepare(void);
void BanimPresentationPolicy_RuntimeProbeRecordHit(
    struct BanimPresentationPolicy const *policy);
void BanimPresentationPolicy_RuntimeProbeRecordPaletteFlash(void);
#endif

extern struct BanimPresentationPolicy const gBanimPresentationPolicies[BANIM_PRESENTATION_POLICY_COUNT];

struct BanimPresentationPolicy const *BanimPresentationPolicy_Get(u8 policyId);
struct BanimPresentationPolicy const *BanimPresentationPolicy_GetCurrent(void);
u8 BanimPresentationPolicy_FromAnimationOption(u8 animationOption);
bool8 BanimPresentationPolicy_Select(u8 policyId);
bool8 BanimPresentationPolicy_Validate(struct BanimPresentationPolicy const *policy);
bool8 BanimPresentationPolicy_ValidateAll(void);
void BanimPresentationPolicy_AdoptAnimationOption(u8 animationOption);
bool8 BanimPresentationPolicy_UsesBackgrounds(struct BanimPresentationPolicy const *policy);
bool8 BanimPresentationPolicy_UsesHitEffects(struct BanimPresentationPolicy const *policy);
bool8 BanimPresentationPolicy_UsesHitEffectPalette(struct BanimPresentationPolicy const *policy);
bool8 BanimPresentationPolicy_ShowsHitNumbers(struct BanimPresentationPolicy const *policy);
bool8 BanimPresentationPolicy_ShowsDamageNumbers(struct BanimPresentationPolicy const *policy);
bool8 BanimPresentationPolicy_ShowsCritNumbers(struct BanimPresentationPolicy const *policy);
u16 BanimPresentationPolicy_AdjustEffectDuration(
    struct BanimPresentationPolicy const *policy,
    u16 defaultFrames);
void BanimPresentationPolicy_ApplyDamageNumberStyle(
    struct BanimPresentationPolicy const *policy,
    s16 *hit,
    s16 *damage,
    s16 *crit);

#endif
