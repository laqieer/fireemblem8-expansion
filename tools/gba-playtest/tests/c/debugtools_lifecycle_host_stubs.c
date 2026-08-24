#include <string.h>

#include "global.h"

#include "hardware.h"
#include "fontgrp.h"
#include "proc.h"
#include "uimenu.h"
#include "expansion_locale.h"
#include "expansion_debugtools.h"
#include "debugtools_internal.h"

struct KeyStatusBuffer gDebugToolsLifecycleKeyStatus = {0};
struct KeyStatusBuffer * CONST_DATA gKeyStatusPtr = &gDebugToolsLifecycleKeyStatus;
static enum DebugToolsDiagnosticsContext sDiagnosticsContext;
struct LCDControlBuffer gLCDControlBuffer = {0};
struct PlaySt gPlaySt = {0};

u8 DebugTools_CancelMenu(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6B;
}

static struct Font sDebugToolsLifecycleFont = {0};
struct Font* gActiveFont = &sDebugToolsLifecycleFont;
static u16 sDebugToolsLifecycleBgMap[32 * 32];
static struct MenuProc sDebugToolsLifecycleMenuProc;
static struct MenuItemProc sDebugToolsLifecycleMenuItems[MENU_ITEM_MAX];
static struct Proc sDebugToolsLifecycleTransitionProc;
static ProcPtr sDebugToolsLifecyclePendingTransition;

int gDebugToolsLifecycleStartMenuCount = 0;
int gDebugToolsLifecycleTransitionProcCount = 0;
int gDebugToolsLifecycleEndMenuCount = 0;
int gDebugToolsLifecycleCursorDisplayCount = 0;
int gDebugToolsLifecycleLastMenuItemCount = 0;
const struct MenuDef* gDebugToolsLifecycleLastMenuDef = NULL;
struct MenuProc* gDebugToolsLifecycleLastMenuProc = NULL;

void DebugToolsLifecycle_SetTextCounter(u16 value)
{
    sDebugToolsLifecycleFont.tileref = 0x80;
    sDebugToolsLifecycleFont.chr_counter = value;
    gActiveFont = &sDebugToolsLifecycleFont;
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

void ClearText(struct Text* text)
{
    (void)text;
}

void SetTextFont(struct Font* font)
{
    gActiveFont = font == NULL ? &sDebugToolsLifecycleFont : font;
}

u16* BG_GetMapBuffer(int bg)
{
    (void)bg;
    return sDebugToolsLifecycleBgMap;
}

void BG_Fill(void* tm, int fill)
{
    (void)tm;
    (void)fill;
}

void BG_EnableSyncByMask(int bgMask)
{
    (void)bgMask;
}

void BG_SetPosition(u16 bg, u16 x, u16 y)
{
    (void)bg;
    (void)x;
    (void)y;
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

void Text_SetColor(struct Text* text, int colorId)
{
    (void)text;
    (void)colorId;
}

void Text_DrawString(struct Text* text, const char* str)
{
    (void)text;
    (void)str;
}

void PutText(struct Text* text, u16* dest)
{
    (void)text;
    (void)dest;
}

void DrawUiFrame(
    u16* map,
    int x,
    int y,
    int width,
    int height,
    int tileref,
    int style)
{
    (void)map;
    (void)x;
    (void)y;
    (void)width;
    (void)height;
    (void)tileref;
    (void)style;
}

void ClearUiFrame(u16* map, int x, int y, int width, int height)
{
    (void)map;
    (void)x;
    (void)y;
    (void)width;
    (void)height;
}

char* GetStringFromIndex(int index)
{
    (void)index;
    return "";
}

void PutDrawText(
    struct Text* text,
    u16* dest,
    int colorId,
    int x,
    int tileWidth,
    const char* string)
{
    (void)text;
    (void)dest;
    (void)colorId;
    (void)x;
    (void)tileWidth;
    (void)string;
}

ExpansionLocaleId ExpansionLocale_GetCurrent(void)
{
    return EXPANSION_LOCALE_QPS_PLOC;
}

const char* ExpansionLocale_ResolveCurrent(ExpansionMsgId msgId)
{
    (void)msgId;
    return "[!! QPS !!]";
}

struct MenuProc* DebugToolsLifecycle_StartOrphanMenu(const struct MenuDef* def)
{
    int i;

    memset(&sDebugToolsLifecycleMenuProc, 0, sizeof(sDebugToolsLifecycleMenuProc));
    memset(sDebugToolsLifecycleMenuItems, 0, sizeof(sDebugToolsLifecycleMenuItems));

    gDebugToolsLifecycleStartMenuCount++;
    gDebugToolsLifecycleLastMenuDef = def;
    gDebugToolsLifecycleLastMenuItemCount = 0;
    gDebugToolsLifecycleLastMenuProc = &sDebugToolsLifecycleMenuProc;
    sDebugToolsLifecycleMenuProc.def = def;
    sDebugToolsLifecycleMenuProc.rect = def->rect;
    sDebugToolsLifecycleMenuProc.itemCurrent = 0;
    sDebugToolsLifecycleMenuProc.itemPrevious = -1;
    sDebugToolsLifecycleMenuProc.backBg = 1;
    sDebugToolsLifecycleMenuProc.frontBg = 0;

    for (i = 0; def->menuItems[i].isAvailable != NULL; ++i)
    {
        gDebugToolsLifecycleLastMenuItemCount++;
        sDebugToolsLifecycleMenuProc.menuItems[i] = &sDebugToolsLifecycleMenuItems[i];
        sDebugToolsLifecycleMenuItems[i].def = &def->menuItems[i];
        sDebugToolsLifecycleMenuItems[i].availability =
            def->menuItems[i].isAvailable(&def->menuItems[i], i);
        sDebugToolsLifecycleMenuItems[i].xTile = def->rect.x + 1;
        sDebugToolsLifecycleMenuItems[i].yTile = def->rect.y + 1 + i * 2;
        InitText(&sDebugToolsLifecycleMenuItems[i].text, def->rect.w - 1);
    }

    sDebugToolsLifecycleMenuProc.itemCount = gDebugToolsLifecycleLastMenuItemCount;
    return &sDebugToolsLifecycleMenuProc;
}

#if !DEBUGTOOLS_LIFECYCLE_USE_REAL_UIMENU
u8 MenuAlwaysEnabled(const struct MenuItemDef* def, int number)
{
    (void)def;
    (void)number;
    return MENU_ENABLED;
}

u8 MenuAlwaysDisabled(const struct MenuItemDef* def, int number)
{
    (void)def;
    (void)number;
    return MENU_DISABLED;
}

u8 MenuCancelSelect(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return MENU_ACT_SKIPCURSOR | MENU_ACT_CLEAR | MENU_ACT_END | MENU_ACT_SND6B;
}

struct Proc* EndMenu(struct MenuProc* proc)
{
    if (proc != NULL && proc->def != NULL && proc->def->onEnd != NULL)
        proc->def->onEnd(proc);

    return (struct Proc*)proc;
}

struct MenuProc* StartOrphanMenu(const struct MenuDef* def)
{
    return DebugToolsLifecycle_StartOrphanMenu(def);
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
#endif

void Proc_End(ProcPtr proc)
{
    if (proc == &sDebugToolsLifecycleMenuProc)
        gDebugToolsLifecycleEndMenuCount++;
}

void UnlockGame(void)
{
}

void PlaySoundEffect(int songId)
{
    (void)songId;
}

void m4aSongNumStart(u16 songId)
{
    (void)songId;
}

void EndFaceById(int faceId)
{
    (void)faceId;
}

void DisplayUiHand(int x, int y)
{
    (void)x;
    (void)y;
    gDebugToolsLifecycleCursorDisplayCount++;
}

void DisplayFrozenUiHand(int x, int y)
{
    (void)x;
    (void)y;
}

void DrawUiItemHoverExt(int bg, int tile, int x, int y, int width)
{
    (void)bg;
    (void)tile;
    (void)x;
    (void)y;
    (void)width;
}

void ClearUiItemHoverExt(int bg, int x, int y, int width)
{
    (void)bg;
    (void)x;
    (void)y;
    (void)width;
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

ProcPtr Proc_Find(const struct ProcCmd* script)
{
    if (script == gProcScr_DebugToolsMenuTransition)
        return sDebugToolsLifecyclePendingTransition;

    return NULL;
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

u8 DebugToolsLifecycle_Builtin10Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 10;
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
    {10, "Music Preview", DebugToolsLifecycle_Builtin10Selected},
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

enum DebugToolsResult DebugToolsDiagnostics_BeginSession(void)
{
    return DEBUGTOOLS_OK;
}

void DebugToolsDiagnostics_EndSession(int forced)
{
    (void)forced;
    DebugToolsDiagnostics_OnSessionRestored();
}

void DebugToolsDiagnostics_ForceCloseSession(void)
{
    DebugToolsDiagnostics_EndSession(1);
}

void DebugToolsSaveState_OnHubReturn(void)
{
}

void DebugToolsDiagnostics_SetSessionContext(
    enum DebugToolsDiagnosticsContext context)
{
    sDiagnosticsContext = context;
}

void DebugToolsDiagnostics_ClearSessionContext(void)
{
    sDiagnosticsContext = DEBUGTOOLS_DIAG_CONTEXT_UNAVAILABLE;
}

enum DebugToolsDiagnosticsContext DebugToolsDiagnostics_GetSessionContext(void)
{
    return sDiagnosticsContext;
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
    return gDebugToolsLifecycleLastMenuProc;
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
    static struct DebugToolsDiagnosticsSnapshot snapshot;
    return &snapshot;
}

char* DebugToolsDiagnostics_GetStatusBuffer(void)
{
    static char status[64];
    return status;
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

#if !DEBUGTOOLS_LIFECYCLE_USE_REAL_MUSIC
void DebugTools_RegisterMusicPreviewAction(void)
{
    DebugTools_RegisterBuiltinAction(&sBuiltinActions[9]);
}

void DebugTools_CleanupMusicPreview(void)
{
}
#endif
