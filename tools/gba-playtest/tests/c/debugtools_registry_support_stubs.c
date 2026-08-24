#include "global.h"

#include "hardware.h"
#include "fontgrp.h"
#include "uimenu.h"
#include "expansion_debugtools.h"
#include "debugtools_internal.h"

static struct DebugToolsDiagnosticsSnapshot sSnapshot;
static char sStatusBuffer[64];
static enum DebugToolsDiagnosticsContext sContext;

u8 DebugTools_CancelMenu(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6B;
}

void ClearText(struct Text* text)
{
    (void)text;
}

void InitText(struct Text* text, int tileWidth)
{
    (void)text;
    (void)tileWidth;
}

void Text_SetColor(struct Text* text, int colorId)
{
    (void)text;
    (void)colorId;
}

void Text_DrawString(struct Text* text, const char* string)
{
    (void)text;
    (void)string;
}

void PutText(struct Text* text, u16* dest)
{
    (void)text;
    (void)dest;
}

void RedrawMenu(struct MenuProc* menu)
{
    (void)menu;
}

void DrawMenuItemHover(struct MenuProc* menu, int item, s8 hover)
{
    (void)menu;
    (void)item;
    (void)hover;
}

u8 MenuAlwaysDisabled(const struct MenuItemDef* def, int number)
{
    (void)def;
    (void)number;
    return MENU_DISABLED;
}

void __attribute__((weak)) BG_EnableSyncByMask(int mask)
{
    (void)mask;
}

enum DebugToolsResult DebugToolsDiagnostics_BeginSession(void)
{
    return DEBUGTOOLS_OK;
}

void DebugToolsDiagnostics_EndSession(int forced)
{
    (void)forced;
}

void DebugToolsDiagnostics_ForceCloseSession(void)
{
    DebugToolsDiagnostics_EndSession(1);
}

void __attribute__((weak)) DebugToolsSaveState_OnHubReturn(void)
{
}

void DebugToolsDiagnostics_SetSessionContext(
    enum DebugToolsDiagnosticsContext context)
{
    sContext = context;
}

void DebugToolsDiagnostics_ClearSessionContext(void)
{
    sContext = DEBUGTOOLS_DIAG_CONTEXT_UNAVAILABLE;
}

enum DebugToolsDiagnosticsContext DebugToolsDiagnostics_GetSessionContext(void)
{
    return sContext;
}

void DebugToolsDiagnostics_SetActiveMenu(struct MenuProc* menu)
{
    (void)menu;
}

void DebugToolsDiagnostics_ClearActiveMenu(struct MenuProc* menu)
{
    (void)menu;
}

struct MenuProc* DebugToolsDiagnostics_GetActiveMenu(void)
{
    return NULL;
}

struct MenuProc* DebugToolsDiagnostics_StartOwnedMenu(
    const struct MenuDef* menuDef)
{
    (void)menuDef;
    return NULL;
}

int DebugToolsDiagnostics_IsRestoring(void)
{
    return 0;
}

const struct DebugToolsDiagnosticsSnapshot* DebugToolsDiagnostics_GetSnapshot(void)
{
    return &sSnapshot;
}

char* DebugToolsDiagnostics_GetStatusBuffer(void)
{
    return sStatusBuffer;
}

void DebugToolsDiagnostics_DrawStatusText(
    struct MenuProc* menu,
    const char* text)
{
    (void)menu;
    (void)text;
}

enum DebugToolsResult DebugToolsDiagnostics_RefreshSnapshot(void)
{
    return DEBUGTOOLS_OK;
}

void DebugToolsDiagnostics_RecordViewOpen(int engineView)
{
    (void)engineView;
}
