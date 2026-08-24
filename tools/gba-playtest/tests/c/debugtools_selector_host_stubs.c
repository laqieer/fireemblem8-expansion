#include "global.h"

#include <string.h>

#include "bm.h"
#include "chapterdata.h"
#include "debugtools_internal.h"
#include "eventinfo.h"
#include "expansion_locale.h"
#include "fontgrp.h"
#include "gamecontrol.h"
#include "hardware.h"
#include "uimenu.h"
#include "worldmap.h"

#include "constants/worldmap.h"

struct DebugToolsProbe gDebugToolsProbe;
struct KeyStatusBuffer gDebugToolsSelectorTestKeyStatus;
struct KeyStatusBuffer* CONST_DATA gKeyStatusPtr =
    &gDebugToolsSelectorTestKeyStatus;
struct LCDControlBuffer gLCDControlBuffer;

static u16 sBgMap[32 * 32];
static struct DebugToolsAction sRegisteredAction;
static struct GameCtrlProc sGameControl;
static u8 sHandoffProc[0x40];
static int sHubActive;
static int sMapActive;

const struct GMapNodeData gWMNodeData[NODE_MAX] = {
    [0 ... NODE_MAX - 1] = {
        .encounters = GMAP_ENCOUNTERS_NONE,
        .chapteridx_eirika = 0xFF,
        .chapteridx_ephram = 0xFF,
    },
    [NODE_IDE] = {
        .encounters = GMAP_ENCOUNTERS_MONSTERS,
        .chapteridx_eirika = 2,
        .chapteridx_ephram = 2,
        .nameTextId = 2,
    },
    [NODE_ZAHA_WOODS] = {
        .encounters = GMAP_ENCOUNTERS_MONSTERS,
        .chapteridx_eirika = 4,
        .chapteridx_ephram = 4,
        .nameTextId = 4,
    },
    [NODE_TERAZ_PLATEAU] = {
        .encounters = GMAP_ENCOUNTERS_MONSTERS,
        .chapteridx_eirika = 10,
        .chapteridx_ephram = 24,
        .nameTextId = 10,
    },
};

const u8 gWMMonsterSpawnLocations[WM_MON_LOC_MAX] = {
    NODE_ZAHA_WOODS,
    NODE_TERAZ_PLATEAU,
};
const u8 gWMMonsterSpawnsSize = 2;

struct ROMChapterData gChapterDataTable[32];
const unsigned gChapterDataCount = ARRAY_COUNT(gChapterDataTable);
static struct ChapterEventGroup sChapterEvents[32];
static int sUnitData;
static int sEventData;

const struct DebugToolsAction* gDebugToolsSelectorCapturedAction;
const struct MenuDef* gDebugToolsSelectorCapturedMenuDef;
int gDebugToolsSelectorQueueSubmenuCount;
int gDebugToolsSelectorReturnHubCount;
int gDebugToolsSelectorEndSessionCount;
int gDebugToolsSelectorProcStartCount;
int gDebugToolsSelectorProcBreakCount;
int gDebugToolsSelectorEndBMapCount;
int gDebugToolsSelectorSetNextAction;
int gDebugToolsSelectorProcGotoLabel = -1;
ProcPtr gDebugToolsSelectorLastProc;

void DebugToolsSelectorHostStub_Init(void)
{
    int chapters[] = {2, 4, 10, 24};
    int i;

    memset(&gDebugToolsProbe, 0, sizeof(gDebugToolsProbe));
    memset(gChapterDataTable, 0, sizeof(gChapterDataTable));
    memset(sChapterEvents, 0, sizeof(sChapterEvents));
    memset(&sRegisteredAction, 0, sizeof(sRegisteredAction));
    memset(&sGameControl, 0, sizeof(sGameControl));
    memset(sHandoffProc, 0, sizeof(sHandoffProc));
    memset(
        &gDebugToolsSelectorTestKeyStatus,
        0,
        sizeof(gDebugToolsSelectorTestKeyStatus));

    gDebugToolsSelectorCapturedAction = NULL;
    gDebugToolsSelectorCapturedMenuDef = NULL;
    gDebugToolsSelectorQueueSubmenuCount = 0;
    gDebugToolsSelectorReturnHubCount = 0;
    gDebugToolsSelectorEndSessionCount = 0;
    gDebugToolsSelectorProcStartCount = 0;
    gDebugToolsSelectorProcBreakCount = 0;
    gDebugToolsSelectorEndBMapCount = 0;
    gDebugToolsSelectorSetNextAction = -1;
    gDebugToolsSelectorProcGotoLabel = -1;
    gDebugToolsSelectorLastProc = NULL;
    sHubActive = 0;
    sMapActive = 1;

    for (i = 0; i < (int)ARRAY_COUNT(chapters); ++i)
    {
        int chapter = chapters[i];
        struct ChapterEventGroup* events = &sChapterEvents[chapter];

        gChapterDataTable[chapter].internalName = "VALID";
        gChapterDataTable[chapter].mapEventDataId = 1;
        gChapterDataTable[chapter].chapTitleTextId = 1;
        events->playerUnitsInNormal = &sUnitData;
        events->beginningSceneEvents = &sEventData;
        events->playerUnitsChoice1InEncounter = &sUnitData;
        events->playerUnitsChoice2InEncounter = &sUnitData;
        events->playerUnitsChoice3InEncounter = &sUnitData;
        events->enemyUnitsChoice1InEncounter = &sUnitData;
        events->enemyUnitsChoice2InEncounter = &sUnitData;
        events->enemyUnitsChoice3InEncounter = &sUnitData;
    }

    sChapterEvents[2].playerUnitsChoice1InEncounter = NULL;
}

const struct ROMChapterData* GetROMChapterStruct(unsigned chapter)
{
    return &gChapterDataTable[chapter];
}

const struct ChapterEventGroup* GetChapterEventDataPointer(unsigned chapter)
{
    return &sChapterEvents[chapter];
}

const char* GetWorldMapNodeName(u32 node)
{
    static const char* names[NODE_MAX] = {
        [NODE_IDE] = "Ide",
        [NODE_ZAHA_WOODS] = "Za'ha Woods",
        [NODE_TERAZ_PLATEAU] = "Teraz Plateau",
    };

    return names[node] == NULL ? "Unknown" : names[node];
}

const char* ExpansionLocale_ResolveCurrent(ExpansionMsgId id)
{
    (void)id;
    return "Localized";
}

int DebugTools_RegisterBuiltinAction(const struct DebugToolsAction* action)
{
    sRegisteredAction = *action;
    gDebugToolsSelectorCapturedAction = &sRegisteredAction;
    return DEBUGTOOLS_OK;
}

void DebugTools_QueueSubmenuTransition(
    struct MenuProc* menu,
    const struct MenuDef* menuDef)
{
    (void)menu;
    gDebugToolsSelectorCapturedMenuDef = menuDef;
    gDebugToolsSelectorQueueSubmenuCount++;
}

void DebugTools_ReturnToHubAfterMenuEnd(struct MenuProc* menu)
{
    (void)menu;
    gDebugToolsSelectorReturnHubCount++;
}

void DebugTools_EndSessionAfterMenuEnd(struct MenuProc* menu)
{
    (void)menu;
    gDebugToolsSelectorEndSessionCount++;
}

int DebugTools_IsHubActive(void)
{
    return sHubActive;
}

void DebugToolsSelectorHostStub_SetHubActive(int active)
{
    sHubActive = active;
}

void DebugToolsSelectorHostStub_SetMapActive(int active)
{
    sMapActive = active;
}

u8 MenuAlwaysEnabled(const struct MenuItemDef* def, int number)
{
    (void)def;
    (void)number;
    return MENU_ENABLED;
}

u8 MenuCancelSelect(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return MENU_ACT_END;
}

u16* BG_GetMapBuffer(int bg)
{
    (void)bg;
    return sBgMap;
}

void BG_Fill(void* tm, int fill)
{
    (void)tm;
    (void)fill;
}

void BG_EnableSyncByMask(int bg)
{
    (void)bg;
}

void InitText(struct Text* text, int tileWidth)
{
    memset(text, 0, sizeof(*text));
    text->tile_width = tileWidth;
}

void ClearText(struct Text* text)
{
    (void)text;
}

void PutDrawText(
    struct Text* text,
    u16* tm,
    int color,
    int x,
    int tileWidth,
    const char* str)
{
    (void)text;
    (void)tm;
    (void)color;
    (void)x;
    (void)tileWidth;
    (void)str;
}

void Text_InsertDrawString(
    struct Text* text,
    int x,
    int color,
    const char* str)
{
    (void)text;
    (void)x;
    (void)color;
    (void)str;
}

void Text_InsertDrawNumberOrBlank(
    struct Text* text,
    int x,
    int color,
    int number)
{
    (void)text;
    (void)x;
    (void)color;
    (void)number;
}

void PutText(struct Text* text, u16* tm)
{
    (void)text;
    (void)tm;
}

struct ProcCmd CONST_DATA gProc_BMapMain[] = {{0}};

ProcPtr Proc_Find(const struct ProcCmd* script)
{
    if (script == gProc_BMapMain && sMapActive)
        return sHandoffProc;

    return NULL;
}

ProcPtr Proc_Start(const struct ProcCmd* script, ProcPtr parent)
{
    (void)script;
    (void)parent;
    gDebugToolsSelectorProcStartCount++;
    gDebugToolsSelectorLastProc = sHandoffProc;
    return sHandoffProc;
}

void Proc_Break(ProcPtr proc)
{
    (void)proc;
    gDebugToolsSelectorProcBreakCount++;
}

void Proc_Goto(ProcPtr proc, int label)
{
    (void)proc;
    gDebugToolsSelectorProcGotoLabel = label;
}

struct GameCtrlProc* GetGameControl(void)
{
    return &sGameControl;
}

void SetNextGameActionId(int action)
{
    gDebugToolsSelectorSetNextAction = action;
}

void EndBMapMain(void)
{
    gDebugToolsSelectorEndBMapCount++;
    sMapActive = 0;
}
