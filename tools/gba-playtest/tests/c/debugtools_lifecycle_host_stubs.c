#include <string.h>

#include "global.h"

#include "hardware.h"
#include "fontgrp.h"
#include "proc.h"
#include "uimenu.h"
#include "expansion_debugtools.h"
#include "debugtools_internal.h"

struct KeyStatusBuffer gDebugToolsLifecycleKeyStatus = {0};
struct KeyStatusBuffer * CONST_DATA gKeyStatusPtr = &gDebugToolsLifecycleKeyStatus;
struct LCDControlBuffer gLCDControlBuffer = {0};
struct PlaySt gPlaySt = {0};

static struct Font sDebugToolsLifecycleFont = {0};
struct Font* gActiveFont = &sDebugToolsLifecycleFont;
static u16 sDebugToolsLifecycleBgMap[32 * 32];
static struct MenuProc sDebugToolsLifecycleMenuProc;
static struct MenuItemProc sDebugToolsLifecycleMenuItems[MENU_ITEM_MAX];
static struct Proc sDebugToolsLifecycleTransitionProc;
static ProcPtr sDebugToolsLifecyclePendingTransition;

int gDebugToolsLifecycleStartMenuCount = 0;
int gDebugToolsLifecycleTransitionProcCount = 0;
int gDebugToolsLifecycleLastMenuItemCount = 0;
const struct MenuDef* gDebugToolsLifecycleLastMenuDef = NULL;
struct MenuProc* gDebugToolsLifecycleLastMenuProc = NULL;

void DebugToolsLifecycle_SetTextCounter(u16 value)
{
    sDebugToolsLifecycleFont.tileref = 0x80;
    sDebugToolsLifecycleFont.chr_counter = value;
}

u16 DebugToolsLifecycle_GetTextCounter(void)
{
    return sDebugToolsLifecycleFont.chr_counter;
}

void InitText(struct Text* text, int tileWidth)
{
    text->chr_position = gActiveFont->chr_counter;
    text->tile_width = tileWidth;
    gActiveFont->chr_counter += tileWidth;
}

u16* BG_GetMapBuffer(int bg)
{
    (void)bg;
    return sDebugToolsLifecycleBgMap;
}

void SetupDebugFontForBG(int bg, int tileDataOffset)
{
    (void)bg;
    (void)tileDataOffset;
}

void PrintDebugStringToBG(u16* bg, const char* asciiStr)
{
    (void)bg;
    (void)asciiStr;
}

u8 MenuAlwaysEnabled(const struct MenuItemDef* def, int number)
{
    (void)def;
    (void)number;
    return 1;
}

u8 MenuCancelSelect(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 0;
}

struct Proc* EndMenu(struct MenuProc* proc)
{
    if (proc != NULL && proc->def != NULL && proc->def->onEnd != NULL)
        proc->def->onEnd(proc);

    return (struct Proc*)proc;
}

struct MenuProc* StartOrphanMenu(const struct MenuDef* def)
{
    int i;

    memset(&sDebugToolsLifecycleMenuProc, 0, sizeof(sDebugToolsLifecycleMenuProc));
    memset(sDebugToolsLifecycleMenuItems, 0, sizeof(sDebugToolsLifecycleMenuItems));

    gDebugToolsLifecycleStartMenuCount++;
    gDebugToolsLifecycleLastMenuDef = def;
    gDebugToolsLifecycleLastMenuItemCount = 0;
    gDebugToolsLifecycleLastMenuProc = &sDebugToolsLifecycleMenuProc;
    sDebugToolsLifecycleMenuProc.def = def;

    for (i = 0; def->menuItems[i].isAvailable != NULL; ++i)
    {
        gDebugToolsLifecycleLastMenuItemCount++;
        sDebugToolsLifecycleMenuProc.menuItems[i] = &sDebugToolsLifecycleMenuItems[i];
        InitText(&sDebugToolsLifecycleMenuItems[i].text, def->rect.w - 1);
    }

    sDebugToolsLifecycleMenuProc.itemCount = gDebugToolsLifecycleLastMenuItemCount;
    return &sDebugToolsLifecycleMenuProc;
}

ProcPtr Proc_Start(const struct ProcCmd* script, ProcPtr parent)
{
    (void)parent;

    if (script == gProcScr_DebugToolsMenuTransition)
    {
        gDebugToolsLifecycleTransitionProcCount++;
        memset(
            &sDebugToolsLifecycleTransitionProc,
            0,
            sizeof(sDebugToolsLifecycleTransitionProc));
        sDebugToolsLifecyclePendingTransition = &sDebugToolsLifecycleTransitionProc;
        return sDebugToolsLifecyclePendingTransition;
    }

    return (ProcPtr)1;
}

void DebugToolsLifecycle_RunPendingTransition(void)
{
    ProcPtr proc = sDebugToolsLifecyclePendingTransition;

    sDebugToolsLifecyclePendingTransition = NULL;
    DebugTools_RunMenuTransition(proc);
}

u8 DebugToolsLifecycle_Builtin1Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 1;
}

u8 DebugToolsLifecycle_Builtin2Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 2;
}

u8 DebugToolsLifecycle_Builtin3Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 3;
}

u8 DebugToolsLifecycle_Builtin4Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 4;
}

u8 DebugToolsLifecycle_Builtin5Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 5;
}

u8 DebugToolsLifecycle_Builtin6Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 6;
}

u8 DebugToolsLifecycle_Builtin7Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 7;
}

u8 DebugToolsLifecycle_Builtin8Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 8;
}

u8 DebugToolsLifecycle_Builtin9Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 9;
}

static const struct DebugToolsAction sBuiltinActions[] =
{
    {1, "Fast Boot: Chapter 2", DebugToolsLifecycle_Builtin1Selected},
    {2, "Weather", DebugToolsLifecycle_Builtin2Selected},
    {3, "Fog", DebugToolsLifecycle_Builtin3Selected},
    {4, "Fast Boot: Ch4 Prep", DebugToolsLifecycle_Builtin4Selected},
    {5, "Unit Inspect", DebugToolsLifecycle_Builtin5Selected},
    {6, "Convoy Inspect", DebugToolsLifecycle_Builtin6Selected},
    {7, "Flag/Chapter", DebugToolsLifecycle_Builtin7Selected},
    {8, "RNG Inspect", DebugToolsLifecycle_Builtin8Selected},
    {9, "Save State", DebugToolsLifecycle_Builtin9Selected},
};

void DebugTools_RegisterBuiltinActions(void)
{
    DebugTools_RegisterBuiltinAction(&sBuiltinActions[0]);
}

void DebugTools_RegisterWeatherFogActions(void)
{
    DebugTools_RegisterBuiltinAction(&sBuiltinActions[1]);
    DebugTools_RegisterBuiltinAction(&sBuiltinActions[2]);
}

void DebugTools_RegisterChapter4PrepAction(void)
{
    DebugTools_RegisterBuiltinAction(&sBuiltinActions[3]);
}

void DebugTools_RegisterExtendedToolActions(void)
{
    int i;

    for (i = 4; i < 9; ++i)
        DebugTools_RegisterBuiltinAction(&sBuiltinActions[i]);
}
