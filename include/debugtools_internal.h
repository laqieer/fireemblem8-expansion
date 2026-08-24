#ifndef GUARD_DEBUGTOOLS_INTERNAL_H
#define GUARD_DEBUGTOOLS_INTERNAL_H

#ifndef FE8_ARCHIVAL_BUILD

#include "proc.h"
#include "uimenu.h"
#include "expansion_debugtools.h"

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

int DebugTools_RegisterBuiltinAction(const struct DebugToolsAction* action);
void DebugTools_EndSessionAfterMenuEnd(struct MenuProc* menu);
int DebugTools_IsMenuTransitionScheduled(void);
void DebugTools_RunMenuTransition(ProcPtr proc);
void DebugTools_QueueSubmenuTransitionWithBuilder(
    struct MenuProc* menu,
    const struct MenuDef* menuDef,
    void (*builder)(void));

extern struct ProcCmd CONST_DATA gProcScr_DebugToolsMenuTransition[];
extern struct MenuItemDef gDebugToolsMenuItemDefs[DEBUGTOOLS_HUB_MENU_SLOTS];

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

#endif /* FE8_EXPANSION_DEBUGTOOLS_ENABLED */

#endif /* FE8_ARCHIVAL_BUILD */

#endif /* GUARD_DEBUGTOOLS_INTERNAL_H */
