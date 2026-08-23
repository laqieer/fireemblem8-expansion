#ifndef GUARD_CUSTOM_SPELL_EFFECT_H
#define GUARD_CUSTOM_SPELL_EFFECT_H

#include "global.h"

struct Anim;
struct CustomSpellEffectFrameAssets;

#define CUSTOM_SPELL_EFFECT_BASE 0x80
#define CUSTOM_SPELL_EFFECT_COUNT 16
#define CUSTOM_SPELL_EFFECT_RUNTIME_ABI 1
#define CUSTOM_SPELL_EFFECT_LAST \
    (CUSTOM_SPELL_EFFECT_BASE + CUSTOM_SPELL_EFFECT_COUNT - 1)

#define CUSTOM_SPELL_EFFECT_MAX_FRAMES 64
#define CUSTOM_SPELL_EFFECT_MAX_OBJ_BYTES 0x1000
#define CUSTOM_SPELL_EFFECT_MAX_BG_BYTES 0x2000
#define CUSTOM_SPELL_EFFECT_BG_TSA_BYTES 1200
#define CUSTOM_SPELL_EFFECT_OBJ_PALETTE_LINE 2
#define CUSTOM_SPELL_EFFECT_BG_PALETTE_LINE 1
#define CUSTOM_SPELL_EFFECT_MAX_OAM_ENTRIES 16
#define CUSTOM_SPELL_EFFECT_MAX_SOUND_EVENTS 8
#define CUSTOM_SPELL_EFFECT_MAX_ROM_BYTES 0x40000

struct CustomSpellEffectFrame
{
    /* 00 */ u8 duration;
    /* 01 */ u8 flags;
    /* 02 */ u16 soundId;
    /* 04 */ const struct CustomSpellEffectFrameAssets *assets;
};

struct CustomSpellEffectResources
{
    /* 00 */ u16 objBytes;
    /* 02 */ u16 bgBytes;
    /* 04 */ u16 bgTsaBytes;
    /* 06 */ u8 objPaletteLine;
    /* 07 */ u8 bgPaletteLine;
    /* 08 */ u8 objOamEntries;
    /* 09 */ u8 soundEvents;
    /* 0A */ STRUCT_PAD(0x0A, 0x0C);
    /* 0C */ u32 romBytes;
};

struct CustomSpellEffectFrameAssets
{
    /* 00 */ const u16 *objGfx;
    /* 04 */ const u16 *bgGfx;
    /* 08 */ const u16 *bgTsaLeft;
    /* 0C */ const u16 *bgTsaRight;
    /* 10 */ const u16 *objPalette;
    /* 14 */ const u16 *bgPalette;
};

struct CustomSpellEffectOamScripts
{
    /* 00 */ const u32 *rightFront;
    /* 04 */ const u32 *leftFront;
    /* 08 */ const u32 *rightBack;
    /* 0C */ const u32 *leftBack;
};

struct CustomSpellEffect
{
    /* 00 */ const char *symbol;
    /* 04 */ const struct CustomSpellEffectFrame *frames;
    /* 08 */ struct CustomSpellEffectResources resources;
    /* 18 */ struct CustomSpellEffectOamScripts oamScripts;
    /* 28 */ u8 animationId;
    /* 29 */ u8 fallbackAnimationId;
    /* 2A */ u8 frameCount;
    /* 2B */ u8 totalFrames;
    /* 2C */ u8 hitFrame;
    /* 2D */ u8 _pad[3];
};

const struct CustomSpellEffect *CustomSpellEffect_Lookup(u8 animationId);
void CustomSpellEffect_Start(const struct CustomSpellEffect *effect, struct Anim *anim);
bool8 CustomSpellEffect_Validate(const struct CustomSpellEffect *effect);
bool8 CustomSpellEffect_IsActive(void);

#if FE8_EXPANSION_MODERN_BUILD && FE8_EXPANSION_CUSTOM_SPELL_EFFECTS \
    && FE8_EXPANSION_DEBUG
struct CustomSpellEffectDebugProbe
{
    /* 00 */ u8 starts;
    /* 01 */ u8 fallbacks;
    /* 02 */ u8 hits;
    /* 03 */ u8 cleanups;
    /* 04 */ u8 lastFallbackReason;
    /* 05 */ u8 lookups;
    /* 06 */ u8 resourceAcquires;
    /* 07 */ u8 _pad;
};

extern struct CustomSpellEffectDebugProbe gCustomSpellEffectDebugProbe;
#endif

#endif /* GUARD_CUSTOM_SPELL_EFFECT_H */
