#include "global.h"

#include "banim_presentation.h"

#ifdef MODERN

struct BanimPresentationPolicy const gBanimPresentationPolicies[BANIM_PRESENTATION_POLICY_COUNT] =
{
    [BANIM_PRESENTATION_POLICY_DEFAULT] =
    {
        BANIM_PRESENTATION_POLICY_DEFAULT, PLAY_ANIMCONF_ON,
        BANIM_PRESENTATION_BACKGROUND_NONE,
        BANIM_PRESENTATION_DAMAGE_NUMBERS_STANDARD,
        BANIM_PRESENTATION_HIT_EFFECT_STANDARD, 0, 1, 8, 96, 0,
        0x4000, 1, 255
    },
    [BANIM_PRESENTATION_POLICY_WITH_BACKGROUNDS] =
    {
        BANIM_PRESENTATION_POLICY_WITH_BACKGROUNDS, PLAY_ANIMCONF_ON_UNIQUE_BG,
        BANIM_PRESENTATION_BACKGROUND_STANDARD,
        BANIM_PRESENTATION_DAMAGE_NUMBERS_STANDARD,
        BANIM_PRESENTATION_HIT_EFFECT_STANDARD, 0, 1, 8, 96, 0,
        0x6000, 1, 255
    },
    [BANIM_PRESENTATION_POLICY_REDUCED] =
    {
        BANIM_PRESENTATION_POLICY_REDUCED, PLAY_ANIMCONF_ON,
        BANIM_PRESENTATION_BACKGROUND_NONE,
        BANIM_PRESENTATION_DAMAGE_NUMBERS_REDUCED,
        BANIM_PRESENTATION_HIT_EFFECT_REDUCED, 1, 2, 8, 64, 0,
        0x3000, 1, 120
    },
    [BANIM_PRESENTATION_POLICY_OFF] =
    {
        BANIM_PRESENTATION_POLICY_OFF, PLAY_ANIMCONF_OFF,
        BANIM_PRESENTATION_BACKGROUND_NONE,
        BANIM_PRESENTATION_DAMAGE_NUMBERS_OFF,
        BANIM_PRESENTATION_HIT_EFFECT_OFF, 0, 0, 0, 0, 0,
        0, 1, 1
    },
    [BANIM_PRESENTATION_POLICY_SOLO] =
    {
        BANIM_PRESENTATION_POLICY_SOLO, PLAY_ANIMCONF_SOLO_ANIM,
        BANIM_PRESENTATION_BACKGROUND_NONE,
        BANIM_PRESENTATION_DAMAGE_NUMBERS_STANDARD,
        BANIM_PRESENTATION_HIT_EFFECT_STANDARD, 0, 1, 8, 96, 0,
        0x4000, 1, 255
    },
};

#if FE8_EXPANSION_HQ_MIXER
EWRAM_DATA static u8 sSelectedPolicyState;
#else
IWRAM_DATA static u8 sSelectedPolicyState;
#endif

#if FE8_AUTOPLAY_EVENT_TRACE_TEST
EWRAM_DATA struct BanimPresentationPolicyHarnessProbe
    gBanimPresentationPolicyHarnessProbe = {0};
#endif

#ifdef BANIM_PRESENTATION_RUNTIME_PROBE_POLICY
#if BANIM_PRESENTATION_RUNTIME_PROBE_POLICY != BANIM_PRESENTATION_POLICY_DEFAULT && \
    BANIM_PRESENTATION_RUNTIME_PROBE_POLICY != BANIM_PRESENTATION_POLICY_OFF
#error "BANIM_PRESENTATION_RUNTIME_PROBE_POLICY must select standard or off"
#endif

IWRAM_DATA struct BanimPresentationRuntimeProbe gBanimPresentationRuntimeProbe = {0};

void BanimPresentationPolicy_RuntimeProbePrepare(void)
{
    sSelectedPolicyState = BANIM_PRESENTATION_RUNTIME_PROBE_POLICY + 1;
}

void BanimPresentationPolicy_RuntimeProbeRecordHit(
    struct BanimPresentationPolicy const *policy)
{
    if (policy == NULL)
        return;

    gBanimPresentationRuntimeProbe.policyId = policy->id;
    gBanimPresentationRuntimeProbe.realHitPathObserved = TRUE;
    gBanimPresentationRuntimeProbe.hitEffectsEnabled =
        BanimPresentationPolicy_UsesHitEffects(policy);
    gBanimPresentationRuntimeProbe.paletteFlashEnabled =
        BanimPresentationPolicy_UsesHitEffectPalette(policy);
    gBanimPresentationRuntimeProbe.hitNumbersVisible =
        BanimPresentationPolicy_ShowsHitNumbers(policy);
    gBanimPresentationRuntimeProbe.damageNumbersVisible =
        BanimPresentationPolicy_ShowsDamageNumbers(policy);
    gBanimPresentationRuntimeProbe.critNumbersVisible =
        BanimPresentationPolicy_ShowsCritNumbers(policy);
}

void BanimPresentationPolicy_RuntimeProbeRecordPaletteFlash(void)
{
    gBanimPresentationRuntimeProbe.paletteFlashStarted = TRUE;
}
#endif

struct BanimPresentationPolicy const *BanimPresentationPolicy_Get(u8 policyId)
{
    if (policyId >= BANIM_PRESENTATION_POLICY_COUNT)
        return NULL;

    return &gBanimPresentationPolicies[policyId];
}

u8 BanimPresentationPolicy_FromAnimationOption(u8 animationOption)
{
    switch (animationOption)
    {
    case PLAY_ANIMCONF_ON:
        return BANIM_PRESENTATION_POLICY_DEFAULT;
    case PLAY_ANIMCONF_ON_UNIQUE_BG:
        return BANIM_PRESENTATION_POLICY_WITH_BACKGROUNDS;
    case PLAY_ANIMCONF_OFF:
        return BANIM_PRESENTATION_POLICY_OFF;
    case PLAY_ANIMCONF_SOLO_ANIM:
        return BANIM_PRESENTATION_POLICY_SOLO;
    default:
        return BANIM_PRESENTATION_POLICY_DEFAULT;
    }
}

struct BanimPresentationPolicy const *BanimPresentationPolicy_GetCurrent(void)
{
    u8 policyId;
    struct BanimPresentationPolicy const *policy;

    if (sSelectedPolicyState == 0)
        policyId = BanimPresentationPolicy_FromAnimationOption(gPlaySt.config.animationType);
    else
        policyId = sSelectedPolicyState - 1;

    policy = BanimPresentationPolicy_Get(policyId);
    if (policy != NULL && sSelectedPolicyState != 0
        && policy->animationOption != gPlaySt.config.animationType)
    {
        BanimPresentationPolicy_AdoptAnimationOption(gPlaySt.config.animationType);
        policy = BanimPresentationPolicy_Get(sSelectedPolicyState - 1);
    }

#if FE8_AUTOPLAY_EVENT_TRACE_TEST
    if (policy != NULL)
    {
        gBanimPresentationPolicyHarnessProbe.currentPolicyId = policy->id;
        if (gBanimPresentationPolicyHarnessProbe.getCurrentCallCount != 0xFFFFFFFFu)
            gBanimPresentationPolicyHarnessProbe.getCurrentCallCount++;
    }
#endif

    return policy;
}

bool8 BanimPresentationPolicy_Select(u8 policyId)
{
    struct BanimPresentationPolicy const *policy = BanimPresentationPolicy_Get(policyId);

    if (!BanimPresentationPolicy_Validate(policy))
        return FALSE;

    sSelectedPolicyState = policyId + 1;
    gPlaySt.config.animationType = policy->animationOption;
    return TRUE;
}

void BanimPresentationPolicy_AdoptAnimationOption(u8 animationOption)
{
    sSelectedPolicyState =
        BanimPresentationPolicy_FromAnimationOption(animationOption) + 1;
}

bool8 BanimPresentationPolicy_Validate(struct BanimPresentationPolicy const *policy)
{
    if (policy == NULL)
        return FALSE;

    if (policy->id >= BANIM_PRESENTATION_POLICY_COUNT)
        return FALSE;
    if (policy->animationOption > PLAY_ANIMCONF_ON_UNIQUE_BG)
        return FALSE;
    if (policy->backgroundMode > BANIM_PRESENTATION_BACKGROUND_STANDARD)
        return FALSE;
    if (policy->damageNumberStyle > BANIM_PRESENTATION_DAMAGE_NUMBERS_OFF)
        return FALSE;
    if (policy->hitEffectStyle > BANIM_PRESENTATION_HIT_EFFECT_OFF)
        return FALSE;
    if (policy->hitEffectPalette > BANIM_PRESENTATION_MAX_HIT_EFFECT_PALETTE)
        return FALSE;
    if (policy->effectSpeed > BANIM_PRESENTATION_MAX_EFFECT_SPEED)
        return FALSE;
    if (policy->paletteSlots > BANIM_PRESENTATION_MAX_PALETTE_SLOTS)
        return FALSE;
    if (policy->oamEntries > BANIM_PRESENTATION_MAX_OAM_ENTRIES)
        return FALSE;
    if (policy->reserved != 0)
        return FALSE;
    if (policy->vramBytes > BANIM_PRESENTATION_MAX_VRAM_BYTES)
        return FALSE;
    if (policy->timingMinFrames == 0 || policy->timingMinFrames > policy->timingMaxFrames)
        return FALSE;
    if (policy->timingMaxFrames > BANIM_PRESENTATION_MAX_TIMING_FRAMES)
        return FALSE;

    return TRUE;
}

bool8 BanimPresentationPolicy_UsesBackgrounds(struct BanimPresentationPolicy const *policy)
{
    return policy != NULL && policy->backgroundMode == BANIM_PRESENTATION_BACKGROUND_STANDARD;
}

bool8 BanimPresentationPolicy_UsesHitEffects(struct BanimPresentationPolicy const *policy)
{
    return policy != NULL && policy->hitEffectStyle != BANIM_PRESENTATION_HIT_EFFECT_OFF;
}

bool8 BanimPresentationPolicy_UsesHitEffectPalette(struct BanimPresentationPolicy const *policy)
{
    return BanimPresentationPolicy_UsesHitEffects(policy)
        && policy->hitEffectPalette == BANIM_PRESENTATION_HIT_EFFECT_PALETTE_STANDARD;
}

bool8 BanimPresentationPolicy_ShowsHitNumbers(struct BanimPresentationPolicy const *policy)
{
    return policy != NULL
        && policy->damageNumberStyle == BANIM_PRESENTATION_DAMAGE_NUMBERS_STANDARD;
}

bool8 BanimPresentationPolicy_ShowsDamageNumbers(struct BanimPresentationPolicy const *policy)
{
    return policy != NULL && policy->damageNumberStyle != BANIM_PRESENTATION_DAMAGE_NUMBERS_OFF;
}

bool8 BanimPresentationPolicy_ShowsCritNumbers(struct BanimPresentationPolicy const *policy)
{
    return BanimPresentationPolicy_ShowsHitNumbers(policy);
}

u16 BanimPresentationPolicy_AdjustEffectDuration(
    struct BanimPresentationPolicy const *policy,
    u16 defaultFrames)
{
    u16 speed;
    u16 frames;

    if (policy == NULL || defaultFrames == 0)
        return defaultFrames;

    speed = policy->effectSpeed;
    if (speed == 0)
        speed = 1;

    frames = defaultFrames / speed;
    if (frames == 0)
        frames = 1;
    if (frames < policy->timingMinFrames)
        frames = policy->timingMinFrames;
    if (frames > policy->timingMaxFrames)
        frames = policy->timingMaxFrames;

    return frames;
}

void BanimPresentationPolicy_ApplyDamageNumberStyle(
    struct BanimPresentationPolicy const *policy,
    s16 *hit,
    s16 *damage,
    s16 *crit)
{
    if (policy == NULL)
        return;

    if (hit != NULL && !BanimPresentationPolicy_ShowsHitNumbers(policy))
        *hit = -1;
    if (damage != NULL && !BanimPresentationPolicy_ShowsDamageNumbers(policy))
        *damage = -1;
    if (crit != NULL && !BanimPresentationPolicy_ShowsCritNumbers(policy))
        *crit = -1;
}

bool8 BanimPresentationPolicy_ValidateAll(void)
{
    u8 i;

    for (i = 0; i < BANIM_PRESENTATION_POLICY_COUNT; ++i)
    {
        if (!BanimPresentationPolicy_Validate(&gBanimPresentationPolicies[i]))
            return FALSE;
    }

    return TRUE;
}

#endif
