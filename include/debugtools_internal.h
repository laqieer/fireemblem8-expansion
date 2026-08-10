#ifndef GUARD_DEBUGTOOLS_INTERNAL_H
#define GUARD_DEBUGTOOLS_INTERNAL_H

#include "proc.h"
#include "expansion_debugtools.h"

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

int DebugTools_RegisterBuiltinAction(const struct DebugToolsAction* action);
void DebugTools_QueueSubmenuTransition(
    struct MenuProc* menu,
    const struct MenuDef* menuDef);
void DebugTools_ReturnToHubAfterMenuEnd(struct MenuProc* menu);
void DebugTools_RunMenuTransition(ProcPtr proc);

extern struct ProcCmd CONST_DATA gProcScr_DebugToolsMenuTransition[];

#endif

#endif
