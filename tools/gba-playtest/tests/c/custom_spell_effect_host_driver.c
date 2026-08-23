#include <stdio.h>
#include <string.h>

#include "../../../../src/custom_spell_effect.c"

#define TEST_VANILLA_ANIM_LAST 72
#define TEST_VANILLA_ANIM_COUNT 73

static void VanillaFallback(struct Anim *anim);
static void VanillaBoundaryFallback(struct Anim *anim);

static struct ProcCustomSpellEffect sProc;
static struct Anim sChildAnim;
static struct Anim sTargetAnim;
static struct BanimPresentationPolicy sPolicy =
{
    BANIM_PRESENTATION_POLICY_DEFAULT,
    PLAY_ANIMCONF_ON,
    BANIM_PRESENTATION_BACKGROUND_NONE,
    BANIM_PRESENTATION_DAMAGE_NUMBERS_STANDARD,
    BANIM_PRESENTATION_HIT_EFFECT_STANDARD,
    BANIM_PRESENTATION_HIT_EFFECT_PALETTE_STANDARD,
    1,
    8,
    96,
    0,
    0x4000,
    1,
    255,
};

u16 Img_FireSpellBg[1];
u16 Pal_FireSpellBg[16];
u16 Tsa_Banim_0[600];
u16 Tsa_efxFireBG_0[600];
u16 Img_FireSpellSprites[1];
u16 Pal_FireSpellSprites[16];
u32 AnimScr_EfxFireOBJ_L_Front[1];
u32 AnimScr_EfxFireOBJ_L_Back[1];
u32 AnimScr_EfxFireOBJ_R_Front[1];
u32 AnimScr_EfxFireOBJ_R_Back[1];
u32 FramScr_Unk5D4F90[2];
u32 gEfxBgSemaphore;
struct Anim *gAnims[4];
SpellAnimFunc gEkrSpellAnimLut[] =
{
    [CUSTOM_SPELL_EFFECT_REFERENCE_FALLBACK] = VanillaFallback,
    [TEST_VANILLA_ANIM_LAST] = VanillaBoundaryFallback,
};
const u32 gEkrSpellAnimLutCount = ARRAY_COUNT(gEkrSpellAnimLut);

static int sFallbacks;
static int sBoundaryFallbacks;
static int sBegins;
static int sFinishes;
static int sBgClears;
static int sBgPositionClears;
static int sColorRestores;
static int sRegistrations;
static int sObjGfxLoads;
static int sBgGfxLoads;
static int sObjPalLoads;
static int sBgPalLoads;
static int sTsaWrites;
static int sChildDeletes;
static int sHits;
static int sHitSounds;
static int sFrameSounds;
static int sProcEnds;

static void ResetState(void)
{
    memset(&sProc, 0, sizeof(sProc));
    memset(&sChildAnim, 0, sizeof(sChildAnim));
    memset(&sTargetAnim, 0, sizeof(sTargetAnim));
    memset(&gCustomSpellEffectDebugProbe, 0, sizeof(gCustomSpellEffectDebugProbe));
    gEfxBgSemaphore = 0;
    sFallbacks = 0;
    sBoundaryFallbacks = 0;
    sBegins = 0;
    sFinishes = 0;
    sBgClears = 0;
    sBgPositionClears = 0;
    sColorRestores = 0;
    sRegistrations = 0;
    sObjGfxLoads = 0;
    sBgGfxLoads = 0;
    sObjPalLoads = 0;
    sBgPalLoads = 0;
    sTsaWrites = 0;
    sChildDeletes = 0;
    sHits = 0;
    sHitSounds = 0;
    sFrameSounds = 0;
    sProcEnds = 0;
    sPolicy.id = BANIM_PRESENTATION_POLICY_DEFAULT;
    sPolicy.backgroundMode = BANIM_PRESENTATION_BACKGROUND_NONE;
    sPolicy.oamEntries = 96;
    sPolicy.vramBytes = 0x4000;
}

static int Check(int condition, const char *message)
{
    if (!condition)
    {
        fprintf(stderr, "CUSTOM_SPELL_HOST_TEST: %s\n", message);
        return 0;
    }

    return 1;
}

static void VanillaFallback(struct Anim *anim)
{
    (void)anim;
    sFallbacks++;
}

static void VanillaBoundaryFallback(struct Anim *anim)
{
    (void)anim;
    sBoundaryFallbacks++;
}

void StartSpellAnimation(struct Anim *anim)
{
    (void)anim;
}

struct BanimPresentationPolicy const *BanimPresentationPolicy_GetCurrent(void)
{
    return &sPolicy;
}

bool8 BanimPresentationPolicy_Validate(struct BanimPresentationPolicy const *policy)
{
    return policy != NULL && policy->id < BANIM_PRESENTATION_POLICY_COUNT;
}

bool8 BanimPresentationPolicy_UsesBackgrounds(struct BanimPresentationPolicy const *policy)
{
    return policy != NULL && policy->backgroundMode == BANIM_PRESENTATION_BACKGROUND_STANDARD;
}

ProcPtr Proc_Start(const struct ProcCmd *script, ProcPtr parent)
{
    (void)parent;
    sProc.proc_script = script;
    sProc.proc_endCb = (ProcFunc)script[1].dataPtr;
    return &sProc;
}

void Proc_End(ProcPtr proc)
{
    struct ProcCustomSpellEffect *customProc = proc;

    sProcEnds++;
    if (customProc->proc_endCb != NULL)
        customProc->proc_endCb(proc);
    customProc->proc_script = NULL;
}

void Proc_Break(ProcPtr proc)
{
    Proc_End(proc);
}

ProcPtr Proc_Find(const struct ProcCmd *script)
{
    if (sProc.proc_script == script)
        return &sProc;

    return NULL;
}

struct Anim *GetAnimAnotherSide(struct Anim *anim)
{
    (void)anim;
    return &sTargetAnim;
}

s16 GetAnimRoundTypeAnotherSide(struct Anim *anim)
{
    (void)anim;
    return 0;
}

int CheckRoundMiss(s16 roundType)
{
    (void)roundType;
    return EKR_HITTED;
}

struct Anim *EfxCreateFrontAnim(
    struct Anim *anim,
    const u32 *rightFront,
    const u32 *leftFront,
    const u32 *rightBack,
    const u32 *leftBack)
{
    (void)anim;
    (void)rightFront;
    (void)leftFront;
    (void)rightBack;
    (void)leftBack;
    return &sChildAnim;
}

void AnimDelete(struct Anim *anim)
{
    if (anim == &sChildAnim)
        sChildDeletes++;
}

void SpellFx_Begin(void)
{
    sBegins++;
}

void NewEfxSpellCast(void)
{
}

void SpellFx_ClearBG1Position(void)
{
    sBgPositionClears++;
}

void SpellFx_SetSomeColorEffect(void)
{
}

void SpellFx_RegisterBgPal(const u16 *palette, u32 size)
{
    (void)palette;
    (void)size;
    sBgPalLoads++;
}

void SpellFx_RegisterBgGfx(const u16 *graphics, u32 size)
{
    (void)graphics;
    (void)size;
    sBgGfxLoads++;
}

void SpellFx_RegisterObjPal(const u16 *palette, u32 size)
{
    (void)palette;
    (void)size;
    sObjPalLoads++;
}

void SpellFx_RegisterObjGfx(const u16 *graphics, u32 size)
{
    (void)graphics;
    (void)size;
    sObjGfxLoads++;
}

void SpellFx_WriteBgMap(struct Anim *anim, const u16 *left, const u16 *right)
{
    (void)anim;
    (void)left;
    (void)right;
    sTsaWrites++;
}

void SpellFx_ClearBG1(void)
{
    sBgClears++;
}

void SetDefaultColorEffects_(void)
{
    sColorRestores++;
}

void SpellFx_Finish(void)
{
    sFinishes++;
}

void RegisterEfxSpellCastEnd(void)
{
    sRegistrations++;
}

void StartBattleAnimHitEffectsDefault(struct Anim *anim, int type)
{
    (void)anim;
    (void)type;
    sHits++;
}

void EfxPlayHittedSFX(struct Anim *anim)
{
    (void)anim;
    sHitSounds++;
}

void PlaySFX(int songId, int volume, int xPosition, int usePan)
{
    (void)songId;
    (void)volume;
    (void)xPosition;
    (void)usePan;
    sFrameSounds++;
}

int main(void)
{
    const struct CustomSpellEffect *effect;
    struct CustomSpellEffect invalid;
    struct Anim attacker;
    int frame;

    memset(&attacker, 0, sizeof(attacker));
    effect = CustomSpellEffect_Lookup(CUSTOM_SPELL_EFFECT_BASE);
    if (!Check(effect != NULL, "reference lookup failed"))
        return 1;
    if (!Check(CustomSpellEffect_Lookup(CUSTOM_SPELL_EFFECT_BASE - 1) == NULL
               && CustomSpellEffect_Lookup(CUSTOM_SPELL_EFFECT_LAST) == NULL
               && CustomSpellEffect_Lookup(CUSTOM_SPELL_EFFECT_LAST + 1) == NULL,
               "custom index range escaped its single generated binding"))
        return 1;

    ResetState();
    CustomSpellEffect_Start(effect, &attacker);
    if (!Check(CustomSpellEffect_IsActive(), "custom effect did not acquire ownership")
        || !Check(gEfxBgSemaphore == 1, "custom effect did not reserve exactly one semaphore")
        || !Check(sBegins == 1 && sObjGfxLoads == 1 && sBgGfxLoads == 1,
                  "custom effect did not load both reserved VRAM lanes")
        || !Check(sObjPalLoads == 1 && sBgPalLoads == 1 && sTsaWrites == 1,
                  "custom effect did not own both palettes and BG1 TSA"))
        return 1;

    for (frame = 0; frame < effect->totalFrames; ++frame)
        CustomSpellEffect_Loop(&sProc);

    if (!Check(!CustomSpellEffect_IsActive() && gEfxBgSemaphore == 0,
               "normal completion did not release ownership")
        || !Check(sHits == 1 && sHitSounds == 1 && sFrameSounds == 1,
                  "custom effect did not apply exactly one hit and declared sound")
        || !Check(sChildDeletes == 1 && sBgClears == 1 && sBgPositionClears == 2,
                  "normal completion did not clean child and BG1")
        || !Check(sColorRestores == 1 && sFinishes == 1 && sRegistrations == 1,
                  "normal completion did not restore spell lifecycle")
        || !Check(gCustomSpellEffectDebugProbe.starts == 1
                  && gCustomSpellEffectDebugProbe.hits == 1
                  && gCustomSpellEffectDebugProbe.cleanups == 1,
                  "debug probe did not record the positive lifecycle"))
        return 1;

    ResetState();
    CustomSpellEffect_Start(effect, &attacker);
    Proc_End(&sProc);
    if (!Check(!CustomSpellEffect_IsActive() && gEfxBgSemaphore == 0,
               "forced end did not release ownership")
        || !Check(sChildDeletes == 1 && sBgClears == 1 && sFinishes == 1,
                  "forced end did not clean all acquired resources"))
        return 1;

    ResetState();
    CustomSpellEffect_Start(effect, &attacker);
    CustomSpellEffect_Start(effect, &attacker);
    if (!Check(sFallbacks == 1 && sObjGfxLoads == 1 && sBgGfxLoads == 1,
               "reentrant start allocated a second custom effect")
        || !Check(gEfxBgSemaphore == 1 && CustomSpellEffect_IsActive(),
                  "reentrant fallback changed the first owner's reservation"))
        return 1;
    Proc_End(&sProc);

    ResetState();
    sPolicy.backgroundMode = BANIM_PRESENTATION_BACKGROUND_STANDARD;
    CustomSpellEffect_Start(effect, &attacker);
    if (!Check(sFallbacks == 1 && sObjGfxLoads == 0 && sBgGfxLoads == 0,
               "background-policy fallback wrote custom resources")
        || !Check(gEfxBgSemaphore == 0 && !CustomSpellEffect_IsActive(),
                  "background-policy fallback retained ownership"))
        return 1;

    ResetState();
    sPolicy.id = BANIM_PRESENTATION_POLICY_OFF;
    CustomSpellEffect_Start(effect, &attacker);
    if (!Check(sFallbacks == 1 && sObjGfxLoads == 0 && sBgGfxLoads == 0,
               "OFF presentation policy wrote custom resources")
        || !Check(gEfxBgSemaphore == 0 && !CustomSpellEffect_IsActive(),
                  "OFF presentation policy retained custom ownership"))
        return 1;

    ResetState();
    gEfxBgSemaphore = 1;
    CustomSpellEffect_Start(effect, &attacker);
    if (!Check(sFallbacks == 1 && sObjGfxLoads == 0 && sBgGfxLoads == 0,
               "occupied semaphore fallback wrote custom resources")
        || !Check(gEfxBgSemaphore == 1 && !CustomSpellEffect_IsActive(),
                  "occupied semaphore fallback modified foreign ownership"))
        return 1;

    ResetState();
    invalid = *effect;
    invalid.resources.objPaletteLine = 0;
    if (!Check(!CustomSpellEffect_Validate(&invalid), "invalid palette lane was accepted"))
        return 1;
    invalid = *effect;
    invalid.frameCount = CUSTOM_SPELL_EFFECT_MAX_FRAMES + 1;
    if (!Check(!CustomSpellEffect_Validate(&invalid), "frame count overflow was accepted"))
        return 1;
    invalid = *effect;
    invalid.totalFrames = 0;
    if (!Check(!CustomSpellEffect_Validate(&invalid), "zero total frame count was accepted"))
        return 1;
    invalid = *effect;
    invalid.resources.romBytes = CUSTOM_SPELL_EFFECT_MAX_ROM_BYTES + 1;
    if (!Check(!CustomSpellEffect_Validate(&invalid), "ROM resource overflow was accepted"))
        return 1;

    invalid = *effect;
    invalid.fallbackAnimationId = TEST_VANILLA_ANIM_LAST;
    if (!Check(CustomSpellEffect_Validate(&invalid),
               "last vanilla LUT animation 72 was rejected"))
        return 1;
    invalid.resources.objPaletteLine = 0;
    ResetState();
    CustomSpellEffect_Start(&invalid, &attacker);
    if (!Check(sBoundaryFallbacks == 1 && sFallbacks == 0,
               "last vanilla LUT animation 72 was not dispatched"))
        return 1;

    invalid = *effect;
    invalid.fallbackAnimationId = TEST_VANILLA_ANIM_COUNT;
    if (!Check(!CustomSpellEffect_Validate(&invalid),
               "out-of-range vanilla LUT animation 73 was accepted"))
        return 1;
    ResetState();
    CustomSpellEffect_Start(&invalid, &attacker);
    if (!Check(sFallbacks == 1 && sBoundaryFallbacks == 0,
               "out-of-range fallback indexed past the vanilla LUT"))
        return 1;

    ResetState();
    invalid = *effect;
    invalid.resources.objPaletteLine = 0;
    CustomSpellEffect_Start(&invalid, &attacker);
    CustomSpellEffect_Start(NULL, NULL);
    if (!Check(sFallbacks == 1 && sObjGfxLoads == 0 && sBgGfxLoads == 0,
               "invalid descriptor fallback was not clean")
        || !Check(gCustomSpellEffectDebugProbe.fallbacks == 2,
                  "null descriptor was not rejected safely"))
        return 1;

    puts("CUSTOM_SPELL_HOST_TEST: PASS");
    return 0;
}
