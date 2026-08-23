#include "global.h"

#include "anime.h"
#include "banim_presentation.h"
#include "custom_spell_effect.h"
#include "custom_spell_effect_test.h"
#include "efxbattle.h"
#include "efxmagic.h"
#include "ekrbattle.h"
#include "m4a.h"
#include "proc.h"

#if FE8_EXPANSION_MODERN_BUILD && FE8_EXPANSION_CUSTOM_SPELL_EFFECTS

#define CUSTOM_SPELL_EFFECT_REFERENCE_FALLBACK 22

enum
{
    CUSTOM_SPELL_EFFECT_FALLBACK_INVALID = 1,
    CUSTOM_SPELL_EFFECT_FALLBACK_REENTRANT,
    CUSTOM_SPELL_EFFECT_FALLBACK_SEMAPHORE,
    CUSTOM_SPELL_EFFECT_FALLBACK_PRESENTATION,
    CUSTOM_SPELL_EFFECT_FALLBACK_CAPACITY,
    CUSTOM_SPELL_EFFECT_FALLBACK_PROC,
};

struct ProcCustomSpellEffect
{
    PROC_HEADER;

    /* 2C */ const struct CustomSpellEffect *effect;
    /* 30 */ struct Anim *anim;
    /* 34 */ struct Anim *childAnim;
    /* 38 */ u8 frameIndex;
    /* 39 */ u8 frameTimer;
    /* 3A */ u8 elapsedFrames;
    /* 3B */ u8 hitted;
    /* 3C */ u8 hitApplied;
    /* 3D */ u8 acquired;
    /* 3E */ u8 finalDisplayLatch;
    /* 3F */ u8 _pad;
};

static const struct CustomSpellEffectFrameAssets sReferenceFrameAssets[] =
{
    {
        Img_FireSpellSprites,
        Img_FireSpellBg,
        Tsa_Banim_0,
        Tsa_efxFireBG_0,
        Pal_FireSpellSprites,
        Pal_FireSpellBg,
    },
    {
        Img_FireSpellSprites,
        Img_FireSpellBg,
        Tsa_Banim_0,
        Tsa_efxFireBG_0,
        Pal_FireSpellSprites,
        Pal_FireSpellBg,
    },
};

static const struct CustomSpellEffectFrame sReferenceFrames[] =
{
    { 2, 0, 0, 1, &sReferenceFrameAssets[0] },
    { 2, 0, 1, 0, &sReferenceFrameAssets[1] },
};

static const u16 sReferenceSoundIds[] = { 0xF1 };

static const struct CustomSpellEffect sReferenceEffect =
{
    "CUSTOM_SPELL_REFERENCE",
    sReferenceFrames,
    {
        CUSTOM_SPELL_EFFECT_MAX_OBJ_BYTES,
        CUSTOM_SPELL_EFFECT_MAX_BG_BYTES,
        CUSTOM_SPELL_EFFECT_BG_TSA_BYTES,
        CUSTOM_SPELL_EFFECT_OBJ_PALETTE_LINE,
        CUSTOM_SPELL_EFFECT_BG_PALETTE_LINE,
        1,
        1,
        { 0, 0 },
        0,
    },
    {
        FramScr_Unk5D4F90,
        FramScr_Unk5D4F90,
        FramScr_Unk5D4F90,
        FramScr_Unk5D4F90,
    },
    sReferenceSoundIds,
    CUSTOM_SPELL_EFFECT_BASE,
    CUSTOM_SPELL_EFFECT_REFERENCE_FALLBACK,
    2,
    4,
    2,
    { 0, 0, 0 },
};

#if FE8_EXPANSION_DEBUG
SECTION("debugtools_contributor_data") struct CustomSpellEffectDebugProbe
    gCustomSpellEffectDebugProbe;
#endif

static void CustomSpellEffect_OnEnd(struct ProcCustomSpellEffect *proc);
static void CustomSpellEffect_Loop(struct ProcCustomSpellEffect *proc);
static void CustomSpellEffect_StartVanillaFallback(
    const struct CustomSpellEffect *effect,
    struct Anim *anim,
    u8 reason);
static void CustomSpellEffect_ApplyFrameAssets(
    struct ProcCustomSpellEffect *proc,
    const struct CustomSpellEffectFrameAssets *assets);
static bool8 CustomSpellEffect_LoadInitialResources(struct ProcCustomSpellEffect *proc);

static CONST_DATA struct ProcCmd sProcScrCustomSpellEffect[] =
{
    PROC_NAME("customSpellEffect"),
    PROC_SET_END_CB(CustomSpellEffect_OnEnd),
    PROC_REPEAT(CustomSpellEffect_Loop),
    PROC_END,
};

static void CustomSpellEffect_StartVanillaFallback(
    const struct CustomSpellEffect *effect,
    struct Anim *anim,
    u8 reason)
{
    u8 fallback = CUSTOM_SPELL_EFFECT_REFERENCE_FALLBACK;

#if FE8_EXPANSION_DEBUG
    gCustomSpellEffectDebugProbe.fallbacks++;
    gCustomSpellEffectDebugProbe.lastFallbackReason = reason;
#endif

    if (anim == NULL)
        return;

    if (effect != NULL)
        fallback = effect->fallbackAnimationId;

    if (fallback >= gEkrSpellAnimLutCount
        || gEkrSpellAnimLut[fallback] == NULL)
        return;

#if FE8_EXPANSION_CUSTOM_SPELL_TEST
    if (CustomSpellEffectTest_InterceptFallback(fallback, reason))
        return;
#endif

    gEkrSpellAnimLut[fallback](anim);
}

bool8 CustomSpellEffect_Validate(const struct CustomSpellEffect *effect)
{
    const struct CustomSpellEffectResources *resources;
    const struct CustomSpellEffectFrame *frameData;
    const struct CustomSpellEffectFrameAssets *assets;
    u8 frame;
    u8 soundIndex;
    u8 validatedSoundEvents = 0;
    u16 soundEnd;
    u16 totalFrames = 0;

    if (effect == NULL || effect->symbol == NULL || effect->frames == NULL)
        return FALSE;
    if (effect->animationId < CUSTOM_SPELL_EFFECT_BASE
        || effect->animationId > CUSTOM_SPELL_EFFECT_LAST)
        return FALSE;
    if (effect->fallbackAnimationId >= gEkrSpellAnimLutCount
        || gEkrSpellAnimLut[effect->fallbackAnimationId] == NULL)
        return FALSE;
    if (effect->frameCount == 0 || effect->frameCount > CUSTOM_SPELL_EFFECT_MAX_FRAMES)
        return FALSE;
    if (effect->totalFrames == 0 || effect->hitFrame >= effect->totalFrames)
        return FALSE;

    resources = &effect->resources;
    if (resources->objBytes == 0 || resources->objBytes > CUSTOM_SPELL_EFFECT_MAX_OBJ_BYTES)
        return FALSE;
    if (resources->bgBytes == 0 || resources->bgBytes > CUSTOM_SPELL_EFFECT_MAX_BG_BYTES)
        return FALSE;
    if (resources->bgTsaBytes != CUSTOM_SPELL_EFFECT_BG_TSA_BYTES)
        return FALSE;
    if (resources->objPaletteLine != CUSTOM_SPELL_EFFECT_OBJ_PALETTE_LINE
        || resources->bgPaletteLine != CUSTOM_SPELL_EFFECT_BG_PALETTE_LINE)
        return FALSE;
    if (resources->objOamEntries == 0
        || resources->objOamEntries > CUSTOM_SPELL_EFFECT_MAX_OAM_ENTRIES)
        return FALSE;
    if (resources->soundEvents > CUSTOM_SPELL_EFFECT_MAX_SOUND_EVENTS
        || resources->romBytes > CUSTOM_SPELL_EFFECT_MAX_ROM_BYTES)
        return FALSE;
    if (resources->soundEvents != 0 && effect->soundIds == NULL)
        return FALSE;
    if (effect->oamScripts.rightFront == NULL
        || effect->oamScripts.leftFront == NULL
        || effect->oamScripts.rightBack == NULL
        || effect->oamScripts.leftBack == NULL)
        return FALSE;

    for (frame = 0; frame < effect->frameCount; ++frame)
    {
        frameData = &effect->frames[frame];
        assets = frameData->assets;
        if (frameData->duration == 0 || frameData->flags != 0 || assets == NULL)
            return FALSE;
        if (frameData->soundStart != validatedSoundEvents)
            return FALSE;
        soundEnd = (u16)frameData->soundStart + frameData->soundCount;
        if (soundEnd > resources->soundEvents)
            return FALSE;
        for (soundIndex = frameData->soundStart; soundIndex < soundEnd; ++soundIndex)
            if (effect->soundIds[soundIndex] == 0)
                return FALSE;
        if (assets->objGfx == NULL
            || assets->bgGfx == NULL
            || assets->bgTsaLeft == NULL
            || assets->bgTsaRight == NULL
            || assets->objPalette == NULL
            || assets->bgPalette == NULL)
            return FALSE;
        totalFrames += frameData->duration;
        validatedSoundEvents = soundEnd;
    }

    return totalFrames == effect->totalFrames
        && validatedSoundEvents == resources->soundEvents;
}

const struct CustomSpellEffect *CustomSpellEffect_Lookup(u8 animationId)
{
#if FE8_EXPANSION_CUSTOM_SPELL_TEST
    const struct CustomSpellEffect *testEffect;
#endif

#if FE8_EXPANSION_DEBUG
    gCustomSpellEffectDebugProbe.lookups++;
#endif

    if (animationId == sReferenceEffect.animationId)
        return &sReferenceEffect;

#if FE8_EXPANSION_CUSTOM_SPELL_TEST
    testEffect = CustomSpellEffectTest_OverrideLookup(animationId, &sReferenceEffect);
    if (testEffect != NULL)
        return testEffect;
#endif

    return NULL;
}

bool8 CustomSpellEffect_IsActive(void)
{
    return Proc_Find(sProcScrCustomSpellEffect) != NULL;
}

void CustomSpellEffect_Start(const struct CustomSpellEffect *effect, struct Anim *anim)
{
    const struct BanimPresentationPolicy *policy;
    struct ProcCustomSpellEffect *proc;

    policy = BanimPresentationPolicy_GetCurrent();
    if (!CustomSpellEffect_Validate(effect) || anim == NULL)
    {
        CustomSpellEffect_StartVanillaFallback(effect, anim, CUSTOM_SPELL_EFFECT_FALLBACK_INVALID);
        return;
    }

    if (Proc_Find(sProcScrCustomSpellEffect) != NULL)
    {
        CustomSpellEffect_StartVanillaFallback(effect, anim, CUSTOM_SPELL_EFFECT_FALLBACK_REENTRANT);
        return;
    }

    if (gEfxBgSemaphore != 0)
    {
        CustomSpellEffect_StartVanillaFallback(effect, anim, CUSTOM_SPELL_EFFECT_FALLBACK_SEMAPHORE);
        return;
    }

    if (!BanimPresentationPolicy_Validate(policy)
        || policy->id == BANIM_PRESENTATION_POLICY_OFF
        || BanimPresentationPolicy_UsesBackgrounds(policy))
    {
        CustomSpellEffect_StartVanillaFallback(
            effect, anim, CUSTOM_SPELL_EFFECT_FALLBACK_PRESENTATION);
        return;
    }

    if (policy->oamEntries + effect->resources.objOamEntries
            > BANIM_PRESENTATION_MAX_OAM_ENTRIES
        || policy->vramBytes + effect->resources.objBytes + effect->resources.bgBytes
            > BANIM_PRESENTATION_MAX_VRAM_BYTES)
    {
        CustomSpellEffect_StartVanillaFallback(effect, anim, CUSTOM_SPELL_EFFECT_FALLBACK_CAPACITY);
        return;
    }

    proc = Proc_Start(sProcScrCustomSpellEffect, PROC_TREE_3);
    if (proc == NULL)
    {
        CustomSpellEffect_StartVanillaFallback(effect, anim, CUSTOM_SPELL_EFFECT_FALLBACK_PROC);
        return;
    }

    proc->effect = effect;
    proc->anim = anim;
    proc->childAnim = NULL;
    proc->frameIndex = 0;
    proc->frameTimer = 0;
    proc->elapsedFrames = 0;
    proc->hitted = CheckRoundMiss(GetAnimRoundTypeAnotherSide(anim));
    proc->hitApplied = 0;
    proc->acquired = 1;
    proc->finalDisplayLatch = 0;
    gEfxBgSemaphore++;
    SpellFx_Begin();
    NewEfxSpellCast();
    SpellFx_ClearBG1Position();
    SpellFx_SetSomeColorEffect();

    if (!CustomSpellEffect_LoadInitialResources(proc))
    {
        Proc_End(proc);
        CustomSpellEffect_StartVanillaFallback(effect, anim, CUSTOM_SPELL_EFFECT_FALLBACK_PROC);
        return;
    }

#if FE8_EXPANSION_DEBUG
    gCustomSpellEffectDebugProbe.starts++;
    gCustomSpellEffectDebugProbe.resourceAcquires++;
#endif
#if FE8_EXPANSION_CUSTOM_SPELL_TEST
    CustomSpellEffectTest_RecordStart();
#endif
}

static void CustomSpellEffect_ApplyFrameAssets(
    struct ProcCustomSpellEffect *proc,
    const struct CustomSpellEffectFrameAssets *assets)
{
    SpellFx_RegisterBgPal(assets->bgPalette, 0x20);
    SpellFx_RegisterBgGfx(assets->bgGfx, proc->effect->resources.bgBytes);
    SpellFx_RegisterObjPal(assets->objPalette, 0x20);
    SpellFx_RegisterObjGfx(assets->objGfx, proc->effect->resources.objBytes);
    SpellFx_WriteBgMap(proc->anim, assets->bgTsaLeft, assets->bgTsaRight);

#if FE8_EXPANSION_CUSTOM_SPELL_TEST
    CustomSpellEffectTest_RecordResourceLoad();
#endif
}

static bool8 CustomSpellEffect_LoadInitialResources(struct ProcCustomSpellEffect *proc)
{
    const struct CustomSpellEffectOamScripts *scripts = &proc->effect->oamScripts;

    CustomSpellEffect_ApplyFrameAssets(proc, proc->effect->frames[0].assets);

#if FE8_EXPANSION_CUSTOM_SPELL_TEST
    if (CustomSpellEffectTest_ShouldFailResourceLoad())
        return FALSE;
#endif

    proc->childAnim = EfxCreateFrontAnim(
        proc->anim,
        scripts->rightFront,
        scripts->leftFront,
        scripts->rightBack,
        scripts->leftBack);

#if FE8_EXPANSION_CUSTOM_SPELL_TEST
    if (proc->childAnim != NULL)
        CustomSpellEffectTest_RecordChildCreate();
#endif

    return proc->childAnim != NULL;
}

static void CustomSpellEffect_Loop(struct ProcCustomSpellEffect *proc)
{
    const struct CustomSpellEffectFrame *frame;
    struct Anim *target;
    u8 sound;

    if (proc->finalDisplayLatch != 0)
    {
        Proc_Break(proc);
        return;
    }

    frame = &proc->effect->frames[proc->frameIndex];
    if (proc->frameTimer == 0)
        for (sound = 0; sound < frame->soundCount; ++sound)
            PlaySFX(
                proc->effect->soundIds[frame->soundStart + sound],
                0x100,
                proc->anim->xPosition,
                1);

    if (proc->elapsedFrames == proc->effect->hitFrame && proc->hitApplied == 0)
    {
        target = GetAnimAnotherSide(proc->anim);
        if (target != NULL)
        {
#if FE8_EXPANSION_CUSTOM_SPELL_TEST
            if (!CustomSpellEffectTest_InterceptHit(target, proc->hitted))
            {
#endif
            target->state3 |= ANIM_BIT3_TAKE_BACK_ENABLE | ANIM_BIT3_HIT_EFFECT_APPLIED;
            StartBattleAnimHitEffectsDefault(target, proc->hitted);
            if (proc->hitted == EKR_HITTED)
                EfxPlayHittedSFX(target);
#if FE8_EXPANSION_CUSTOM_SPELL_TEST
            }
#endif
        }
        proc->hitApplied = 1;
#if FE8_EXPANSION_DEBUG
        gCustomSpellEffectDebugProbe.hits++;
#endif
    }

    proc->elapsedFrames++;
    proc->frameTimer++;

    if (proc->frameTimer >= frame->duration)
    {
        proc->frameTimer = 0;
        proc->frameIndex++;
        if (proc->frameIndex < proc->effect->frameCount)
            CustomSpellEffect_ApplyFrameAssets(
                proc, proc->effect->frames[proc->frameIndex].assets);
    }

    if (proc->frameIndex >= proc->effect->frameCount)
    {
        proc->finalDisplayLatch = 1;
#if FE8_EXPANSION_CUSTOM_SPELL_TEST
        CustomSpellEffectTest_RecordFinalDisplayLatch();
#endif
    }
}

static void CustomSpellEffect_OnEnd(struct ProcCustomSpellEffect *proc)
{
    if (proc->acquired == 0)
        return;

    if (proc->childAnim != NULL)
    {
        AnimDelete(proc->childAnim);
#if FE8_EXPANSION_CUSTOM_SPELL_TEST
        CustomSpellEffectTest_RecordChildDelete();
#endif
    }

    SpellFx_ClearBG1();
    SpellFx_ClearBG1Position();
    SetDefaultColorEffects_();
    SpellFx_Finish();
    RegisterEfxSpellCastEnd();
    if (gEfxBgSemaphore != 0)
        gEfxBgSemaphore--;
    proc->acquired = 0;

#if FE8_EXPANSION_DEBUG
    gCustomSpellEffectDebugProbe.cleanups++;
#endif
#if FE8_EXPANSION_CUSTOM_SPELL_TEST
    CustomSpellEffectTest_RecordCleanup();
#endif
}

#if FE8_EXPANSION_CUSTOM_SPELL_TEST
void CustomSpellEffectTest_ForceEndOwner(void)
{
    Proc_EndEach(sProcScrCustomSpellEffect);
}
#endif

#endif
