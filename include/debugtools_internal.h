#ifndef GUARD_DEBUGTOOLS_INTERNAL_H
#define GUARD_DEBUGTOOLS_INTERNAL_H

#include "proc.h"
#include "expansion_debugtools.h"

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

#define DEBUGTOOLS_SHARED_MENU_ITEM_MAX \
    (((FE8_EXPANSION_ENABLED_LOCALE_COUNT + 2) > 6) \
        ? (FE8_EXPANSION_ENABLED_LOCALE_COUNT + 2) \
        : 6)

extern struct MenuItemDef
    sDebugToolsMenuItemDefs[DEBUGTOOLS_SHARED_MENU_ITEM_MAX];

int DebugTools_RegisterBuiltinAction(const struct DebugToolsAction* action);
void DebugTools_EndSessionAfterMenuEnd(struct MenuProc* menu);
int DebugTools_IsMenuTransitionScheduled(void);
void DebugTools_RunMenuTransition(ProcPtr proc);

extern struct ProcCmd CONST_DATA gProcScr_DebugToolsMenuTransition[];

#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
/*
 * This private probe is compiled only by
 * expansion-modern-portrait-package-runtime-check. It deliberately stays
 * out of expansion_debugtools.h and every supported profile's EWRAM layout.
 */
struct PortraitPackageRuntimeProbe
{
    u32 faceId;
    u32 minimugRenderCount;
    u32 minimugVramWord;
    u32 minimugPaletteWord;
    u32 fullFaceRenderCount;
    u32 mouthDisplayBits;
    u32 eyeControl;
    u32 faceOam2;
    u32 mouthFrame0;
    u32 mouthFrame2;
};

extern struct PortraitPackageRuntimeProbe gPortraitPackageRuntimeProbe;
#endif

#endif

#endif
