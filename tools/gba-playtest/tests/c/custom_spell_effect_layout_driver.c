#include "global.h"

#include <stddef.h>

#include "custom_spell_effect.h"

typedef char CustomSpellEffectFrameAssetsObjGfxOffset[
    offsetof(struct CustomSpellEffectFrameAssets, objGfx) == 0x00 ? 1 : -1];
typedef char CustomSpellEffectFrameAssetsBgGfxOffset[
    offsetof(struct CustomSpellEffectFrameAssets, bgGfx) == 0x04 ? 1 : -1];
typedef char CustomSpellEffectFrameAssetsBgTsaLeftOffset[
    offsetof(struct CustomSpellEffectFrameAssets, bgTsaLeft) == 0x08 ? 1 : -1];
typedef char CustomSpellEffectFrameAssetsBgTsaRightOffset[
    offsetof(struct CustomSpellEffectFrameAssets, bgTsaRight) == 0x0C ? 1 : -1];
typedef char CustomSpellEffectFrameAssetsObjPaletteOffset[
    offsetof(struct CustomSpellEffectFrameAssets, objPalette) == 0x10 ? 1 : -1];
typedef char CustomSpellEffectFrameAssetsBgPaletteOffset[
    offsetof(struct CustomSpellEffectFrameAssets, bgPalette) == 0x14 ? 1 : -1];
typedef char CustomSpellEffectFrameAssetsSize[
    sizeof(struct CustomSpellEffectFrameAssets) == 0x18 ? 1 : -1];

typedef char CustomSpellEffectFrameAssetsOffset[
    offsetof(struct CustomSpellEffectFrame, assets) == 0x04 ? 1 : -1];
typedef char CustomSpellEffectFrameSize[
    sizeof(struct CustomSpellEffectFrame) == 0x08 ? 1 : -1];

typedef char CustomSpellEffectOamScriptsRightFrontOffset[
    offsetof(struct CustomSpellEffectOamScripts, rightFront) == 0x00 ? 1 : -1];
typedef char CustomSpellEffectOamScriptsLeftFrontOffset[
    offsetof(struct CustomSpellEffectOamScripts, leftFront) == 0x04 ? 1 : -1];
typedef char CustomSpellEffectOamScriptsRightBackOffset[
    offsetof(struct CustomSpellEffectOamScripts, rightBack) == 0x08 ? 1 : -1];
typedef char CustomSpellEffectOamScriptsLeftBackOffset[
    offsetof(struct CustomSpellEffectOamScripts, leftBack) == 0x0C ? 1 : -1];
typedef char CustomSpellEffectOamScriptsSize[
    sizeof(struct CustomSpellEffectOamScripts) == 0x10 ? 1 : -1];

typedef char CustomSpellEffectResourcesOffset[
    offsetof(struct CustomSpellEffect, resources) == 0x08 ? 1 : -1];
typedef char CustomSpellEffectOamScriptsOffset[
    offsetof(struct CustomSpellEffect, oamScripts) == 0x18 ? 1 : -1];
typedef char CustomSpellEffectAnimationIdOffset[
    offsetof(struct CustomSpellEffect, animationId) == 0x28 ? 1 : -1];
typedef char CustomSpellEffectHitFrameOffset[
    offsetof(struct CustomSpellEffect, hitFrame) == 0x2C ? 1 : -1];
typedef char CustomSpellEffectSize[
    sizeof(struct CustomSpellEffect) == 0x30 ? 1 : -1];

int CustomSpellEffectLayoutCompileProbe(void)
{
    return 0;
}
