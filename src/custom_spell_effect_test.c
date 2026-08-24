#include "global.h"

#include "custom_spell_effect_test.h"

#if FE8_EXPANSION_CUSTOM_SPELL_TEST

#include <string.h>

#include "constants/items.h"
#include "anime.h"
#include "banim_presentation.h"
#include "custom_spell_effect.h"
#include "efxbattle.h"
#include "efxmagic.h"
#include "ekrbattle.h"
#include "proc.h"
#include "spellassoc.h"

#if FE8_EXPANSION_CUSTOM_SPELL_EFFECTS
#include "build/generated/assets/custom_spell/custom_spell_effect_runtime_test.h"
#endif

#define CUSTOM_SPELL_EFFECT_TEST_FALLBACK_INVALID 1
#define CUSTOM_SPELL_EFFECT_TEST_FALLBACK_REENTRANT 2
#define CUSTOM_SPELL_EFFECT_TEST_FALLBACK_SEMAPHORE 3
#define CUSTOM_SPELL_EFFECT_TEST_FALLBACK_PRESENTATION 4
#define CUSTOM_SPELL_EFFECT_TEST_FALLBACK_PROC 6
#define CUSTOM_SPELL_EFFECT_TEST_FALLBACK_ANIMATION 22
#define CUSTOM_SPELL_EFFECT_TEST_VANILLA_ANIMATION 24
#define CUSTOM_SPELL_EFFECT_TEST_TIMEOUT 240

enum CustomSpellEffectTestCase
{
    CUSTOM_SPELL_TEST_CASE_NORMAL = 1 << 0,
    CUSTOM_SPELL_TEST_CASE_VANILLA = 1 << 1,
    CUSTOM_SPELL_TEST_CASE_MISSING = 1 << 2,
    CUSTOM_SPELL_TEST_CASE_INVALID = 1 << 3,
    CUSTOM_SPELL_TEST_CASE_REENTRANT = 1 << 4,
    CUSTOM_SPELL_TEST_CASE_RESOURCE_FAILURE = 1 << 5,
    CUSTOM_SPELL_TEST_CASE_BACKGROUNDS = 1 << 6,
    CUSTOM_SPELL_TEST_CASE_SEMAPHORE = 1 << 7,
    CUSTOM_SPELL_TEST_CASE_FORCED = 1 << 8,
};

#define CUSTOM_SPELL_TEST_CASE_ALL \
    (CUSTOM_SPELL_TEST_CASE_NORMAL | CUSTOM_SPELL_TEST_CASE_VANILLA \
     | CUSTOM_SPELL_TEST_CASE_MISSING | CUSTOM_SPELL_TEST_CASE_INVALID \
     | CUSTOM_SPELL_TEST_CASE_REENTRANT | CUSTOM_SPELL_TEST_CASE_RESOURCE_FAILURE \
     | CUSTOM_SPELL_TEST_CASE_BACKGROUNDS | CUSTOM_SPELL_TEST_CASE_SEMAPHORE \
     | CUSTOM_SPELL_TEST_CASE_FORCED)

enum CustomSpellEffectTestPhase
{
    CUSTOM_SPELL_TEST_PHASE_INIT,
    CUSTOM_SPELL_TEST_PHASE_NORMAL_WAIT,
    CUSTOM_SPELL_TEST_PHASE_VANILLA,
    CUSTOM_SPELL_TEST_PHASE_MISSING,
    CUSTOM_SPELL_TEST_PHASE_INVALID,
    CUSTOM_SPELL_TEST_PHASE_REENTRANT_WAIT,
    CUSTOM_SPELL_TEST_PHASE_RESOURCE_FAILURE_WAIT,
    CUSTOM_SPELL_TEST_PHASE_BACKGROUNDS,
    CUSTOM_SPELL_TEST_PHASE_SEMAPHORE,
    CUSTOM_SPELL_TEST_PHASE_FORCED_WAIT,
    CUSTOM_SPELL_TEST_PHASE_FINISH,
};

enum CustomSpellEffectTestSetupFailure
{
    CUSTOM_SPELL_TEST_SETUP_FAILURE_NONE,
    CUSTOM_SPELL_TEST_SETUP_FAILURE_ANIM,
    CUSTOM_SPELL_TEST_SETUP_FAILURE_PROC,
};

struct CustomSpellEffectTestCounters
{
    u32 customDispatches;
    u32 vanillaDispatches;
    u32 fallbacks;
    u32 fallbackReason;
    u32 fallbackAnimation;
    u32 starts;
    u32 resourceLoads;
    u32 hits;
    u32 cleanups;
    u32 childCreates;
    u32 childDeletes;
    u32 finalDisplayLatches;
};

struct ProcCustomSpellEffectTestHarness
{
    PROC_HEADER;

    /* 2C */ u16 phase;
    /* 2E */ u16 timer;
};

EWRAM_DATA struct CustomSpellEffectTestProbe gCustomSpellEffectTestProbe;
EWRAM_DATA static struct CustomSpellEffectTestCounters sCounters;
EWRAM_DATA static struct CustomSpellEffect sInvalidEffect;
EWRAM_DATA static u8 sInvalidLookupEnabled;
EWRAM_DATA static u8 sFailResourceLoad;
EWRAM_DATA static u8 sSetupFailureMode;

static void CustomSpellEffectTest_Loop(struct ProcCustomSpellEffectTestHarness *proc);
static void CustomSpellEffectTest_OnEnd(struct ProcCustomSpellEffectTestHarness *proc);

static CONST_DATA struct ProcCmd sProcScrCustomSpellEffectTestHarness[] =
{
    PROC_NAME("customSpellIsolatedTest"),
    PROC_SET_END_CB(CustomSpellEffectTest_OnEnd),
    PROC_REPEAT(CustomSpellEffectTest_Loop),
    PROC_END,
};

static void CustomSpellEffectTest_ResetCounters(void)
{
    memset(&sCounters, 0, sizeof(sCounters));
}

static void CustomSpellEffectTest_CleanupAnims(void)
{
    int i;

    AnimClearAll();
    for (i = 0; i < 4; ++i)
        gAnims[i] = NULL;

    gEfxBgSemaphore = 0;
    gEfxSpellAnimExists = FALSE;
    gpProcEfxSpellCast = NULL;
}

static bool8 CustomSpellEffectTest_SetupStateClean(void)
{
    int i;

    for (i = 0; i < 4; ++i)
        if (gAnims[i] != NULL)
            return FALSE;

    return gEfxBgSemaphore == 0
        && gEfxSpellAnimExists == FALSE
        && gpProcEfxSpellCast == NULL;
}

static void CustomSpellEffectTest_RecordSetupFailure(u8 mode)
{
    if (mode == CUSTOM_SPELL_TEST_SETUP_FAILURE_ANIM)
        gCustomSpellEffectTestProbe.animAllocationFailures++;
    else if (mode == CUSTOM_SPELL_TEST_SETUP_FAILURE_PROC)
        gCustomSpellEffectTestProbe.procAllocationFailures++;

    CustomSpellEffectTest_CleanupAnims();
    if (CustomSpellEffectTest_SetupStateClean())
        gCustomSpellEffectTestProbe.allocationFailureCleanups++;
    else
        gCustomSpellEffectTestProbe.failureMask |= 0x20000000;
}

static void CustomSpellEffectTest_FinalizeSetupFailure(u8 mode)
{
    CustomSpellEffectTest_RecordSetupFailure(mode);
    gCustomSpellEffectTestProbe.failureMask |= 0x40000000;
    gCustomSpellEffectTestProbe.finalSpellCastActive = gpProcEfxSpellCast != NULL;
    gCustomSpellEffectTestProbe.finalSemaphore = gEfxBgSemaphore;
    gCustomSpellEffectTestProbe.finalSpellState = gEfxSpellAnimExists;
    gCustomSpellEffectTestProbe.magic = CUSTOM_SPELL_EFFECT_TEST_PROBE_MAGIC;
    gCustomSpellEffectTestProbe.harnessEnded = TRUE;
}

static struct Anim *CustomSpellEffectTest_CreateAnim(int index)
{
    if (sSetupFailureMode == CUSTOM_SPELL_TEST_SETUP_FAILURE_ANIM && index == 2)
        return NULL;

    return AnimCreate(FramScr_Unk5D4F90, 0);
}

static struct ProcCustomSpellEffectTestHarness *CustomSpellEffectTest_StartHarnessProc(void)
{
    if (sSetupFailureMode == CUSTOM_SPELL_TEST_SETUP_FAILURE_PROC)
        return NULL;

    return Proc_Start(sProcScrCustomSpellEffectTestHarness, PROC_TREE_3);
}

static void CustomSpellEffectTest_SetAnimation(u8 animationId)
{
    gEkrSpellAnimIndex[EKR_POS_L] = animationId;
}

static bool8 CustomSpellEffectTest_IsSettled(void)
{
#if FE8_EXPANSION_CUSTOM_SPELL_EFFECTS
    if (CustomSpellEffect_IsActive())
        return FALSE;
#endif

    return gpProcEfxSpellCast == NULL && gEfxSpellAnimExists == FALSE;
}

static void CustomSpellEffectTest_Fail(u32 bit)
{
    gCustomSpellEffectTestProbe.failureMask |= bit;
}

static bool8 CustomSpellEffectTest_Wait(
    struct ProcCustomSpellEffectTestHarness *proc,
    u32 failureBit)
{
    if (CustomSpellEffectTest_IsSettled())
    {
        proc->timer = 0;
        return TRUE;
    }

    if (++proc->timer >= CUSTOM_SPELL_EFFECT_TEST_TIMEOUT)
    {
        CustomSpellEffectTest_Fail(failureBit);
        proc->timer = 0;
        return TRUE;
    }

    return FALSE;
}

static bool8 CustomSpellEffectTest_StateClean(void)
{
#if FE8_EXPANSION_CUSTOM_SPELL_EFFECTS
    if (CustomSpellEffect_IsActive())
        return FALSE;
#endif

    return gEfxBgSemaphore == 0 && gEfxSpellAnimExists == FALSE;
}

static bool8 CustomSpellEffectTest_PrepareAnims(void)
{
    int i;

    CustomSpellEffectTest_CleanupAnims();
    for (i = 0; i < 4; ++i)
    {
        gAnims[i] = CustomSpellEffectTest_CreateAnim(i);
        if (gAnims[i] == NULL)
        {
            CustomSpellEffectTest_CleanupAnims();
            return FALSE;
        }

        gAnims[i]->xPosition = (i < 2) ? 64 : 176;
        gAnims[i]->yPosition = 80;
        gAnims[i]->nextRoundId = 1;
        if (i >= 2)
            gAnims[i]->state2 |= ANIM_BIT2_POS_RIGHT;
    }

    gAnimRoundData[0] = ANIM_ROUND_TAKING_HIT_CLOSE;
    gAnimRoundData[1] = ANIM_ROUND_TAKING_HIT_CLOSE;
    gEkrDistanceType = EKR_DISTANCE_CLOSE;
    gEfxBgSemaphore = 0;
    gEfxSpellAnimExists = FALSE;
    gpProcEfxSpellCast = NULL;
    BanimPresentationPolicy_Select(BANIM_PRESENTATION_POLICY_DEFAULT);
    return TRUE;
}

#if FE8_EXPANSION_CUSTOM_SPELL_EFFECTS
static u8 CustomSpellEffectTest_ReferenceFrameCount(void)
{
    const struct CustomSpellEffect *effect =
        CustomSpellEffect_Lookup(CUSTOM_SPELL_EFFECT_BASE);

    if (effect == NULL)
        return 0;

    return effect->frameCount;
}

static void CustomSpellEffectTest_StartDispatch(u8 animationId)
{
    CustomSpellEffectTest_SetAnimation(animationId);
    StartSpellAnimation(gAnims[0]);
}

static void CustomSpellEffectTest_RecordNormal(void)
{
    struct CustomSpellEffectTestProbe *probe = &gCustomSpellEffectTestProbe;

    probe->normalCustomDispatches = sCounters.customDispatches;
    probe->normalStarts = sCounters.starts;
    probe->normalResourceLoads = sCounters.resourceLoads;
    probe->normalHits = sCounters.hits;
    probe->normalCleanups = sCounters.cleanups;
    probe->normalChildCreates = sCounters.childCreates;
    probe->normalChildDeletes = sCounters.childDeletes;
    probe->normalFinalDisplayLatches = sCounters.finalDisplayLatches;
    probe->normalDistanceType = gEkrDistanceType;
    probe->normalFinalActive = CustomSpellEffect_IsActive();
    probe->normalFinalSemaphore = gEfxBgSemaphore;
    probe->normalFinalSpellState = gEfxSpellAnimExists;
    if (sCounters.customDispatches != 1 || sCounters.starts != 1
        || sCounters.resourceLoads != CustomSpellEffectTest_ReferenceFrameCount()
        || sCounters.hits != 1
        || sCounters.cleanups != 1 || sCounters.childCreates != 1
        || sCounters.childDeletes != 1 || sCounters.finalDisplayLatches != 1
        || !CustomSpellEffectTest_StateClean())
        CustomSpellEffectTest_Fail(CUSTOM_SPELL_TEST_CASE_NORMAL);
    probe->completedMask |= CUSTOM_SPELL_TEST_CASE_NORMAL;
}

static void CustomSpellEffectTest_RecordMissing(void)
{
    struct CustomSpellEffectTestProbe *probe = &gCustomSpellEffectTestProbe;

    probe->missingCustomDispatches = sCounters.customDispatches;
    probe->missingFallbackReason = sCounters.fallbackReason;
    probe->missingFallbackAnimation = sCounters.fallbackAnimation;
    probe->missingResourceLoads = sCounters.resourceLoads;
    probe->missingFinalActive = CustomSpellEffect_IsActive();
    if (sCounters.customDispatches != 1 || sCounters.fallbacks != 1
        || sCounters.fallbackReason != CUSTOM_SPELL_EFFECT_TEST_FALLBACK_INVALID
        || sCounters.fallbackAnimation != CUSTOM_SPELL_EFFECT_TEST_FALLBACK_ANIMATION
        || sCounters.resourceLoads != 0 || !CustomSpellEffectTest_StateClean())
        CustomSpellEffectTest_Fail(CUSTOM_SPELL_TEST_CASE_MISSING);
    probe->completedMask |= CUSTOM_SPELL_TEST_CASE_MISSING;
}

static void CustomSpellEffectTest_RecordInvalid(void)
{
    struct CustomSpellEffectTestProbe *probe = &gCustomSpellEffectTestProbe;

    probe->invalidCustomDispatches = sCounters.customDispatches;
    probe->invalidFallbackReason = sCounters.fallbackReason;
    probe->invalidFallbackAnimation = sCounters.fallbackAnimation;
    probe->invalidResourceLoads = sCounters.resourceLoads;
    probe->invalidFinalActive = CustomSpellEffect_IsActive();
    if (sCounters.customDispatches != 1 || sCounters.fallbacks != 1
        || sCounters.fallbackReason != CUSTOM_SPELL_EFFECT_TEST_FALLBACK_INVALID
        || sCounters.fallbackAnimation != CUSTOM_SPELL_EFFECT_TEST_FALLBACK_ANIMATION
        || sCounters.resourceLoads != 0 || !CustomSpellEffectTest_StateClean())
        CustomSpellEffectTest_Fail(CUSTOM_SPELL_TEST_CASE_INVALID);
    probe->completedMask |= CUSTOM_SPELL_TEST_CASE_INVALID;
}

static void CustomSpellEffectTest_RecordReentrant(void)
{
    struct CustomSpellEffectTestProbe *probe = &gCustomSpellEffectTestProbe;

    probe->reentrantCustomDispatches = sCounters.customDispatches;
    probe->reentrantStarts = sCounters.starts;
    probe->reentrantFallbacks = sCounters.fallbacks;
    probe->reentrantFallbackReason = sCounters.fallbackReason;
    probe->reentrantResourceLoads = sCounters.resourceLoads;
    probe->reentrantCleanups = sCounters.cleanups;
    probe->reentrantFinalDisplayLatches = sCounters.finalDisplayLatches;
    probe->reentrantFinalActive = CustomSpellEffect_IsActive();
    probe->reentrantFinalSemaphore = gEfxBgSemaphore;
    if (sCounters.customDispatches != 2 || sCounters.starts != 1
        || sCounters.fallbacks != 1
        || sCounters.fallbackReason != CUSTOM_SPELL_EFFECT_TEST_FALLBACK_REENTRANT
        || sCounters.resourceLoads != CustomSpellEffectTest_ReferenceFrameCount()
        || sCounters.cleanups != 1 || sCounters.finalDisplayLatches != 1
        || !CustomSpellEffectTest_StateClean())
        CustomSpellEffectTest_Fail(CUSTOM_SPELL_TEST_CASE_REENTRANT);
    probe->completedMask |= CUSTOM_SPELL_TEST_CASE_REENTRANT;
}

static void CustomSpellEffectTest_RecordResourceFailure(void)
{
    struct CustomSpellEffectTestProbe *probe = &gCustomSpellEffectTestProbe;

    probe->resourceFailureCustomDispatches = sCounters.customDispatches;
    probe->resourceFailureStarts = sCounters.starts;
    probe->resourceFailureFallbacks = sCounters.fallbacks;
    probe->resourceFailureFallbackReason = sCounters.fallbackReason;
    probe->resourceFailureResourceLoads = sCounters.resourceLoads;
    probe->resourceFailureCleanups = sCounters.cleanups;
    probe->resourceFailureChildCreates = sCounters.childCreates;
    probe->resourceFailureFinalActive = CustomSpellEffect_IsActive();
    probe->resourceFailureFinalSemaphore = gEfxBgSemaphore;
    if (sCounters.customDispatches != 1 || sCounters.starts != 0
        || sCounters.fallbacks != 1
        || sCounters.fallbackReason != CUSTOM_SPELL_EFFECT_TEST_FALLBACK_PROC
        || sCounters.resourceLoads != 1 || sCounters.cleanups != 1
        || sCounters.childCreates != 0 || !CustomSpellEffectTest_StateClean())
        CustomSpellEffectTest_Fail(CUSTOM_SPELL_TEST_CASE_RESOURCE_FAILURE);
    probe->completedMask |= CUSTOM_SPELL_TEST_CASE_RESOURCE_FAILURE;
}

static void CustomSpellEffectTest_RecordForced(void)
{
    struct CustomSpellEffectTestProbe *probe = &gCustomSpellEffectTestProbe;

    probe->forcedCustomDispatches = sCounters.customDispatches;
    probe->forcedStarts = sCounters.starts;
    probe->forcedCleanups = sCounters.cleanups;
    probe->forcedChildCreates = sCounters.childCreates;
    probe->forcedChildDeletes = sCounters.childDeletes;
    probe->forcedFinalActive = CustomSpellEffect_IsActive();
    probe->forcedFinalSemaphore = gEfxBgSemaphore;
    if (sCounters.customDispatches != 1 || sCounters.starts != 1
        || sCounters.cleanups != 1 || sCounters.childCreates != 1
        || sCounters.childDeletes != 1 || !CustomSpellEffectTest_StateClean())
        CustomSpellEffectTest_Fail(CUSTOM_SPELL_TEST_CASE_FORCED);
    probe->completedMask |= CUSTOM_SPELL_TEST_CASE_FORCED;
}
#endif

static void CustomSpellEffectTest_Loop(struct ProcCustomSpellEffectTestHarness *proc)
{
    struct CustomSpellEffectTestProbe *probe = &gCustomSpellEffectTestProbe;

#if !FE8_EXPANSION_CUSTOM_SPELL_EFFECTS
    if (proc->phase == CUSTOM_SPELL_TEST_PHASE_INIT)
    {
        CustomSpellEffectTest_ResetCounters();
        CustomSpellEffectTest_SetAnimation(CUSTOM_SPELL_EFFECT_TEST_VANILLA_ANIMATION);
        StartSpellAnimation(gAnims[0]);
        probe->vanillaDispatches = sCounters.vanillaDispatches;
        probe->vanillaCustomDispatches = sCounters.customDispatches;
        probe->completedMask = CUSTOM_SPELL_TEST_CASE_VANILLA;
        if (sCounters.vanillaDispatches != 1 || sCounters.customDispatches != 0)
            CustomSpellEffectTest_Fail(CUSTOM_SPELL_TEST_CASE_VANILLA);
        proc->phase = CUSTOM_SPELL_TEST_PHASE_FINISH;
    }
#else
    switch (proc->phase)
    {
    case CUSTOM_SPELL_TEST_PHASE_INIT:
        CustomSpellEffectTest_ResetCounters();
        gAnimRoundData[0] = ANIM_ROUND_TAKING_HIT_FAR;
        gAnimRoundData[1] = ANIM_ROUND_TAKING_HIT_FAR;
        gEkrDistanceType = EKR_DISTANCE_FAR;
#if FE8_EXPANSION_CUSTOM_SPELL_EFFECTS
        CustomSpellEffectTest_StartDispatch(
            GetSpellAssocEfxIndex(CUSTOM_SPELL_EFFECT_TEST_ITEM));
#else
        CustomSpellEffectTest_StartDispatch(CUSTOM_SPELL_EFFECT_BASE);
#endif
        proc->phase = CUSTOM_SPELL_TEST_PHASE_NORMAL_WAIT;
        break;

    case CUSTOM_SPELL_TEST_PHASE_NORMAL_WAIT:
        if (!CustomSpellEffectTest_Wait(proc, CUSTOM_SPELL_TEST_CASE_NORMAL))
            break;
        CustomSpellEffectTest_RecordNormal();
        proc->phase = CUSTOM_SPELL_TEST_PHASE_VANILLA;
        break;

    case CUSTOM_SPELL_TEST_PHASE_VANILLA:
        CustomSpellEffectTest_ResetCounters();
        CustomSpellEffectTest_StartDispatch(CUSTOM_SPELL_EFFECT_TEST_VANILLA_ANIMATION);
        probe->vanillaDispatches = sCounters.vanillaDispatches;
        probe->vanillaCustomDispatches = sCounters.customDispatches;
        if (sCounters.vanillaDispatches != 1 || sCounters.customDispatches != 0)
            CustomSpellEffectTest_Fail(CUSTOM_SPELL_TEST_CASE_VANILLA);
        probe->completedMask |= CUSTOM_SPELL_TEST_CASE_VANILLA;
        proc->phase = CUSTOM_SPELL_TEST_PHASE_MISSING;
        break;

    case CUSTOM_SPELL_TEST_PHASE_MISSING:
        CustomSpellEffectTest_ResetCounters();
        CustomSpellEffectTest_StartDispatch(CUSTOM_SPELL_EFFECT_LAST);
        CustomSpellEffectTest_RecordMissing();
        proc->phase = CUSTOM_SPELL_TEST_PHASE_INVALID;
        break;

    case CUSTOM_SPELL_TEST_PHASE_INVALID:
        CustomSpellEffectTest_ResetCounters();
        sInvalidLookupEnabled = TRUE;
        CustomSpellEffectTest_StartDispatch(CUSTOM_SPELL_EFFECT_BASE + 1);
        sInvalidLookupEnabled = FALSE;
        CustomSpellEffectTest_RecordInvalid();
        proc->phase = CUSTOM_SPELL_TEST_PHASE_REENTRANT_WAIT;
        CustomSpellEffectTest_ResetCounters();
        CustomSpellEffectTest_StartDispatch(CUSTOM_SPELL_EFFECT_BASE);
        CustomSpellEffectTest_StartDispatch(CUSTOM_SPELL_EFFECT_BASE);
        break;

    case CUSTOM_SPELL_TEST_PHASE_REENTRANT_WAIT:
        if (!CustomSpellEffectTest_Wait(proc, CUSTOM_SPELL_TEST_CASE_REENTRANT))
            break;
        CustomSpellEffectTest_RecordReentrant();
        CustomSpellEffectTest_ResetCounters();
        sFailResourceLoad = TRUE;
        CustomSpellEffectTest_StartDispatch(CUSTOM_SPELL_EFFECT_BASE);
        sFailResourceLoad = FALSE;
        proc->phase = CUSTOM_SPELL_TEST_PHASE_RESOURCE_FAILURE_WAIT;
        break;

    case CUSTOM_SPELL_TEST_PHASE_RESOURCE_FAILURE_WAIT:
        if (!CustomSpellEffectTest_Wait(
                proc, CUSTOM_SPELL_TEST_CASE_RESOURCE_FAILURE))
            break;
        CustomSpellEffectTest_RecordResourceFailure();
        proc->phase = CUSTOM_SPELL_TEST_PHASE_BACKGROUNDS;
        break;

    case CUSTOM_SPELL_TEST_PHASE_BACKGROUNDS:
        CustomSpellEffectTest_ResetCounters();
        BanimPresentationPolicy_Select(BANIM_PRESENTATION_POLICY_WITH_BACKGROUNDS);
        CustomSpellEffectTest_StartDispatch(CUSTOM_SPELL_EFFECT_BASE);
        probe->backgroundsCustomDispatches = sCounters.customDispatches;
        probe->backgroundsFallbackReason = sCounters.fallbackReason;
        probe->backgroundsFallbackAnimation = sCounters.fallbackAnimation;
        probe->backgroundsResourceLoads = sCounters.resourceLoads;
        probe->backgroundsFinalActive = CustomSpellEffect_IsActive();
        if (sCounters.customDispatches != 1 || sCounters.fallbacks != 1
            || sCounters.fallbackReason != CUSTOM_SPELL_EFFECT_TEST_FALLBACK_PRESENTATION
            || sCounters.fallbackAnimation != CUSTOM_SPELL_EFFECT_TEST_FALLBACK_ANIMATION
            || sCounters.resourceLoads != 0 || !CustomSpellEffectTest_StateClean())
            CustomSpellEffectTest_Fail(CUSTOM_SPELL_TEST_CASE_BACKGROUNDS);
        probe->completedMask |= CUSTOM_SPELL_TEST_CASE_BACKGROUNDS;
        BanimPresentationPolicy_Select(BANIM_PRESENTATION_POLICY_DEFAULT);
        proc->phase = CUSTOM_SPELL_TEST_PHASE_SEMAPHORE;
        break;

    case CUSTOM_SPELL_TEST_PHASE_SEMAPHORE:
        CustomSpellEffectTest_ResetCounters();
        gEfxBgSemaphore = 1;
        CustomSpellEffectTest_StartDispatch(CUSTOM_SPELL_EFFECT_BASE);
        probe->semaphoreCustomDispatches = sCounters.customDispatches;
        probe->semaphoreFallbackReason = sCounters.fallbackReason;
        probe->semaphoreFallbackAnimation = sCounters.fallbackAnimation;
        probe->semaphoreResourceLoads = sCounters.resourceLoads;
        probe->semaphorePreserved = gEfxBgSemaphore;
        probe->semaphoreFinalActive = CustomSpellEffect_IsActive();
        if (sCounters.customDispatches != 1 || sCounters.fallbacks != 1
            || sCounters.fallbackReason != CUSTOM_SPELL_EFFECT_TEST_FALLBACK_SEMAPHORE
            || sCounters.fallbackAnimation != CUSTOM_SPELL_EFFECT_TEST_FALLBACK_ANIMATION
            || sCounters.resourceLoads != 0 || gEfxBgSemaphore != 1
            || CustomSpellEffect_IsActive())
            CustomSpellEffectTest_Fail(CUSTOM_SPELL_TEST_CASE_SEMAPHORE);
        probe->completedMask |= CUSTOM_SPELL_TEST_CASE_SEMAPHORE;
        gEfxBgSemaphore = 0;
        CustomSpellEffectTest_ResetCounters();
        CustomSpellEffectTest_StartDispatch(CUSTOM_SPELL_EFFECT_BASE);
        CustomSpellEffectTest_ForceEndOwner();
        proc->phase = CUSTOM_SPELL_TEST_PHASE_FORCED_WAIT;
        break;

    case CUSTOM_SPELL_TEST_PHASE_FORCED_WAIT:
        if (!CustomSpellEffectTest_Wait(proc, CUSTOM_SPELL_TEST_CASE_FORCED))
            break;
        CustomSpellEffectTest_RecordForced();
        proc->phase = CUSTOM_SPELL_TEST_PHASE_FINISH;
        break;

    default:
        proc->phase = CUSTOM_SPELL_TEST_PHASE_FINISH;
        break;
    }
#endif

    if (proc->phase != CUSTOM_SPELL_TEST_PHASE_FINISH)
        return;

#if FE8_EXPANSION_CUSTOM_SPELL_EFFECTS
    probe->finalCustomActive = CustomSpellEffect_IsActive();
#endif
    probe->finalSpellCastActive = gpProcEfxSpellCast != NULL;
    probe->finalSemaphore = gEfxBgSemaphore;
    probe->finalSpellState = gEfxSpellAnimExists;
    if (probe->finalCustomActive != 0 || probe->finalSpellCastActive != 0
        || probe->finalSemaphore != 0 || probe->finalSpellState != 0)
        probe->failureMask |= 0x80000000;
    probe->magic = CUSTOM_SPELL_EFFECT_TEST_PROBE_MAGIC;
    Proc_Break(proc);
}

static void CustomSpellEffectTest_OnEnd(struct ProcCustomSpellEffectTestHarness *proc)
{
    (void)proc;
    gCustomSpellEffectTestProbe.harnessEnded = TRUE;
}

void CustomSpellEffectTest_Start(void)
{
    struct ProcCustomSpellEffectTestHarness *proc;

    memset(&gCustomSpellEffectTestProbe, 0, sizeof(gCustomSpellEffectTestProbe));
    memset(&sCounters, 0, sizeof(sCounters));
    gCustomSpellEffectTestProbe.enabled = FE8_EXPANSION_CUSTOM_SPELL_EFFECTS;

    sSetupFailureMode = CUSTOM_SPELL_TEST_SETUP_FAILURE_ANIM;
    if (CustomSpellEffectTest_PrepareAnims())
    {
        CustomSpellEffectTest_FinalizeSetupFailure(CUSTOM_SPELL_TEST_SETUP_FAILURE_ANIM);
        return;
    }
    CustomSpellEffectTest_RecordSetupFailure(CUSTOM_SPELL_TEST_SETUP_FAILURE_ANIM);

    sSetupFailureMode = CUSTOM_SPELL_TEST_SETUP_FAILURE_NONE;
    if (!CustomSpellEffectTest_PrepareAnims())
    {
        CustomSpellEffectTest_FinalizeSetupFailure(CUSTOM_SPELL_TEST_SETUP_FAILURE_ANIM);
        return;
    }

    sSetupFailureMode = CUSTOM_SPELL_TEST_SETUP_FAILURE_PROC;
    proc = CustomSpellEffectTest_StartHarnessProc();
    if (proc != NULL)
    {
        Proc_End(proc);
        CustomSpellEffectTest_FinalizeSetupFailure(CUSTOM_SPELL_TEST_SETUP_FAILURE_PROC);
        return;
    }
    CustomSpellEffectTest_RecordSetupFailure(CUSTOM_SPELL_TEST_SETUP_FAILURE_PROC);

    sSetupFailureMode = CUSTOM_SPELL_TEST_SETUP_FAILURE_NONE;
    if (!CustomSpellEffectTest_PrepareAnims())
    {
        CustomSpellEffectTest_FinalizeSetupFailure(CUSTOM_SPELL_TEST_SETUP_FAILURE_ANIM);
        return;
    }

    proc = CustomSpellEffectTest_StartHarnessProc();
    if (proc == NULL)
    {
        CustomSpellEffectTest_FinalizeSetupFailure(CUSTOM_SPELL_TEST_SETUP_FAILURE_PROC);
        return;
    }

    proc->phase = CUSTOM_SPELL_TEST_PHASE_INIT;
    proc->timer = 0;
}

void CustomSpellEffectTest_RecordDispatch(s16 animationIndex, bool8 custom)
{
    (void)animationIndex;
    if (custom)
        sCounters.customDispatches++;
    else
        sCounters.vanillaDispatches++;
}

const struct CustomSpellEffect *CustomSpellEffectTest_OverrideLookup(
    u8 animationId,
    const struct CustomSpellEffect *reference)
{
    if (!sInvalidLookupEnabled || animationId != CUSTOM_SPELL_EFFECT_BASE + 1)
        return NULL;

    sInvalidEffect = *reference;
    sInvalidEffect.animationId = animationId;
    sInvalidEffect.resources.objPaletteLine = 0;
    return &sInvalidEffect;
}

bool8 CustomSpellEffectTest_InterceptFallback(u8 animationId, u8 reason)
{
    sCounters.fallbacks++;
    sCounters.fallbackReason = reason;
    sCounters.fallbackAnimation = animationId;
    return TRUE;
}

bool8 CustomSpellEffectTest_InterceptHit(struct Anim *target, int hitted)
{
    (void)target;
    (void)hitted;
    sCounters.hits++;
    return TRUE;
}

bool8 CustomSpellEffectTest_ShouldFailResourceLoad(void)
{
    return sFailResourceLoad;
}

void CustomSpellEffectTest_RecordStart(void)
{
    sCounters.starts++;
}

void CustomSpellEffectTest_RecordResourceLoad(void)
{
    sCounters.resourceLoads++;
}

void CustomSpellEffectTest_RecordCleanup(void)
{
    sCounters.cleanups++;
}

void CustomSpellEffectTest_RecordChildCreate(void)
{
    sCounters.childCreates++;
}

void CustomSpellEffectTest_RecordChildDelete(void)
{
    sCounters.childDeletes++;
}

void CustomSpellEffectTest_RecordFinalDisplayLatch(void)
{
    sCounters.finalDisplayLatches++;
}

#endif /* FE8_EXPANSION_CUSTOM_SPELL_TEST */
