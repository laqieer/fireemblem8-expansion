#include "global.h"

#ifndef FE8_ARCHIVAL_BUILD
#include "expansion_debugtools.h"

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

#include <string.h>

#include "bm.h"
#include "bmio.h"
#include "chapterdata.h"
#include "debugtools_internal.h"
#include "eventinfo.h"
#include "expansion_locale.h"
#include "expansion_msg_ids.h"
#include "fontgrp.h"
#include "gamecontrol.h"
#include "hardware.h"
#include "proc.h"
#include "uimenu.h"
#include "worldmap.h"

#include "constants/worldmap.h"

enum
{
    DEBUGTOOLS_SELECTOR_TARGET_ID_KIND_SHIFT = 12,
    DEBUGTOOLS_SELECTOR_TARGET_ID_MODE_SHIFT = 8,
    DEBUGTOOLS_SELECTOR_MAP_HANDOFF_TIMEOUT = 60,
};

_Static_assert(NODE_MAX <= 0x100, "selector target ID reserves eight node bits");
_Static_assert(NODE_MAX * 4 <= 0xFF, "selector target count probe must fit u8");

#ifdef DEBUGTOOLS_SELECTOR_HOST_TEST
int gDebugToolsSelectorEnumerationCount;
#endif

struct DebugToolsSelectorState
{
    u16 targetId;
    u8 pending;
    u8 requestOrigin;
    u8 mapHandoffScheduled;
};

struct DebugToolsSelectorHandoffProc
{
    PROC_HEADER;

    /* 2C */ u16 timer;
};

EWRAM_DATA static struct DebugToolsSelectorState sSelectorState = {0};
extern struct ProcCmd CONST_DATA gProcScr_DebugToolsSelectorMapHandoff[];

static int DebugToolsSelector_IsChapterAvailable(int chapterId)
{
    const struct ROMChapterData* chapter;
    const struct ChapterEventGroup* events;

    if (chapterId < 0 || (unsigned)chapterId >= gChapterDataCount)
        return 0;

    chapter = GetROMChapterStruct(chapterId);
    if (chapter->internalName == NULL || chapter->internalName[0] == '\0')
        return 0;

    if (chapter->mapEventDataId == 0 || chapter->chapTitleTextId == 0)
        return 0;

    events = GetChapterEventDataPointer(chapterId);
    if (events == NULL
        || events->playerUnitsInNormal == NULL
        || events->beginningSceneEvents == NULL)
        return 0;

    return 1;
}

static int DebugToolsSelector_IsSpawnNode(int nodeId)
{
    int count = gWMMonsterSpawnsSize;
    int i;

    if (count > WM_MON_LOC_MAX)
        count = WM_MON_LOC_MAX;

    for (i = 0; i < count; ++i)
        if (gWMMonsterSpawnLocations[i] == nodeId)
            return 1;

    return 0;
}

static int DebugToolsSelector_IsSkirmishAvailable(int nodeId, int chapterId)
{
    const struct ChapterEventGroup* events;

    if (!DebugToolsSelector_IsChapterAvailable(chapterId))
        return 0;

    if (nodeId < 0 || nodeId >= NODE_MAX
        || gWMNodeData[nodeId].encounters != GMAP_ENCOUNTERS_MONSTERS
        || !DebugToolsSelector_IsSpawnNode(nodeId))
        return 0;

    events = GetChapterEventDataPointer(chapterId);
    return events->playerUnitsChoice1InEncounter != NULL
        && events->playerUnitsChoice2InEncounter != NULL
        && events->playerUnitsChoice3InEncounter != NULL
        && events->enemyUnitsChoice1InEncounter != NULL
        && events->enemyUnitsChoice2InEncounter != NULL
        && events->enemyUnitsChoice3InEncounter != NULL;
}

static u16 DebugToolsSelector_MakeTargetId(int kind, int chapterMode, int nodeId)
{
    return (kind << DEBUGTOOLS_SELECTOR_TARGET_ID_KIND_SHIFT)
        | (chapterMode << DEBUGTOOLS_SELECTOR_TARGET_ID_MODE_SHIFT)
        | nodeId;
}

static int DebugToolsSelector_IsTargetAvailable(int kind, int nodeId, int chapterId)
{
    if (kind == DEBUGTOOLS_LAUNCH_TARGET_CHAPTER)
        return DebugToolsSelector_IsChapterAvailable(chapterId);

    if (kind == DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH)
        return DebugToolsSelector_IsSkirmishAvailable(nodeId, chapterId);

    return 0;
}

static void DebugToolsSelector_RecordEnumeration(void)
{
#ifdef DEBUGTOOLS_SELECTOR_HOST_TEST
    gDebugToolsSelectorEnumerationCount++;
#endif
}

static int DebugToolsSelector_EmitTarget(
    int wantedIndex,
    int* currentIndex,
    int kind,
    int chapterMode,
    int nodeId,
    int chapterId,
    struct DebugToolsLaunchTarget* out)
{
    if (!DebugToolsSelector_IsTargetAvailable(kind, nodeId, chapterId))
        return 0;

    if (*currentIndex == wantedIndex && out != NULL)
    {
        out->id = DebugToolsSelector_MakeTargetId(kind, chapterMode, nodeId);
        out->kind = kind;
        out->chapterMode = chapterMode;
        out->nodeId = nodeId;
        out->chapterId = chapterId;
        out->encounterChoice = 0;
        out->_pad = 0;
    }

    (*currentIndex)++;
    return 1;
}

static int DebugToolsSelector_ResolveTarget(
    int wantedIndex,
    struct DebugToolsLaunchTarget* out)
{
    int currentIndex = 0;
    int nodeId;

    if (wantedIndex < 0)
        return 0;

    DebugToolsSelector_RecordEnumeration();

    for (nodeId = 0; nodeId < NODE_MAX; ++nodeId)
    {
        int eirikaChapter = gWMNodeData[nodeId].chapteridx_eirika;
        int ephraimChapter = gWMNodeData[nodeId].chapteridx_ephram;

        if (eirikaChapter == ephraimChapter)
        {
            DebugToolsSelector_EmitTarget(
                wantedIndex,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_CHAPTER,
                CHAPTER_MODE_COMMON,
                nodeId,
                eirikaChapter,
                out);
            DebugToolsSelector_EmitTarget(
                wantedIndex,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH,
                CHAPTER_MODE_COMMON,
                nodeId,
                eirikaChapter,
                out);
        }
        else
        {
            DebugToolsSelector_EmitTarget(
                wantedIndex,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_CHAPTER,
                CHAPTER_MODE_EIRIKA,
                nodeId,
                eirikaChapter,
                out);
            DebugToolsSelector_EmitTarget(
                wantedIndex,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH,
                CHAPTER_MODE_EIRIKA,
                nodeId,
                eirikaChapter,
                out);
            DebugToolsSelector_EmitTarget(
                wantedIndex,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_CHAPTER,
                CHAPTER_MODE_EPHRAIM,
                nodeId,
                ephraimChapter,
                out);
            DebugToolsSelector_EmitTarget(
                wantedIndex,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH,
                CHAPTER_MODE_EPHRAIM,
                nodeId,
                ephraimChapter,
                out);
        }

        if (wantedIndex < currentIndex)
            return 1;
    }

    return 0;
}

int DebugTools_GetLaunchTargetCount(void)
{
    int currentIndex = 0;
    int nodeId;

    for (nodeId = 0; nodeId < NODE_MAX; ++nodeId)
    {
        int eirikaChapter = gWMNodeData[nodeId].chapteridx_eirika;
        int ephraimChapter = gWMNodeData[nodeId].chapteridx_ephram;

        if (eirikaChapter == ephraimChapter)
        {
            DebugToolsSelector_EmitTarget(
                -1,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_CHAPTER,
                CHAPTER_MODE_COMMON,
                nodeId,
                eirikaChapter,
                NULL);
            DebugToolsSelector_EmitTarget(
                -1,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH,
                CHAPTER_MODE_COMMON,
                nodeId,
                eirikaChapter,
                NULL);
        }
        else
        {
            DebugToolsSelector_EmitTarget(
                -1,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_CHAPTER,
                CHAPTER_MODE_EIRIKA,
                nodeId,
                eirikaChapter,
                NULL);
            DebugToolsSelector_EmitTarget(
                -1,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH,
                CHAPTER_MODE_EIRIKA,
                nodeId,
                eirikaChapter,
                NULL);
            DebugToolsSelector_EmitTarget(
                -1,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_CHAPTER,
                CHAPTER_MODE_EPHRAIM,
                nodeId,
                ephraimChapter,
                NULL);
            DebugToolsSelector_EmitTarget(
                -1,
                &currentIndex,
                DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH,
                CHAPTER_MODE_EPHRAIM,
                nodeId,
                ephraimChapter,
                NULL);
        }
    }

    return currentIndex;
}

int DebugTools_GetLaunchTarget(int index, struct DebugToolsLaunchTarget* out)
{
    if (out == NULL)
        return 0;

    memset(out, 0, sizeof(*out));
    return DebugToolsSelector_ResolveTarget(index, out);
}

static int DebugToolsSelector_IsTargetIdShapeValid(u16 targetId)
{
    int kind = targetId >> DEBUGTOOLS_SELECTOR_TARGET_ID_KIND_SHIFT;
    int chapterMode = (targetId >> DEBUGTOOLS_SELECTOR_TARGET_ID_MODE_SHIFT) & 0x0F;
    int nodeId = targetId & 0xFF;

    if (kind != DEBUGTOOLS_LAUNCH_TARGET_CHAPTER
        && kind != DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH)
        return 0;

    if (chapterMode != CHAPTER_MODE_COMMON
        && chapterMode != CHAPTER_MODE_EIRIKA
        && chapterMode != CHAPTER_MODE_EPHRAIM)
        return 0;

    return nodeId < NODE_MAX;
}

static int DebugToolsSelector_DecodeTargetId(
    u16 targetId,
    struct DebugToolsLaunchTarget* out)
{
    int kind;
    int chapterMode;
    int nodeId;
    int eirikaChapter;
    int ephraimChapter;
    int chapterId;

    if (!DebugToolsSelector_IsTargetIdShapeValid(targetId))
        return 0;

    kind = targetId >> DEBUGTOOLS_SELECTOR_TARGET_ID_KIND_SHIFT;
    chapterMode = (targetId >> DEBUGTOOLS_SELECTOR_TARGET_ID_MODE_SHIFT) & 0x0F;
    nodeId = targetId & 0xFF;
    eirikaChapter = gWMNodeData[nodeId].chapteridx_eirika;
    ephraimChapter = gWMNodeData[nodeId].chapteridx_ephram;

    if (chapterMode == CHAPTER_MODE_COMMON)
    {
        if (eirikaChapter != ephraimChapter)
            return 0;

        chapterId = eirikaChapter;
    }
    else if (chapterMode == CHAPTER_MODE_EIRIKA)
    {
        chapterId = eirikaChapter;
    }
    else
    {
        chapterId = ephraimChapter;
    }

    if (!DebugToolsSelector_IsTargetAvailable(kind, nodeId, chapterId))
        return 0;

    if (out != NULL)
    {
        out->id = targetId;
        out->kind = kind;
        out->chapterMode = chapterMode;
        out->nodeId = nodeId;
        out->chapterId = chapterId;
        out->encounterChoice = 0;
        out->_pad = 0;
    }

    return 1;
}

static int DebugToolsSelector_FindTargetIndexEmit(
    u16 targetId,
    int* currentIndex,
    int kind,
    int chapterMode,
    int nodeId,
    int chapterId,
    int* outIndex)
{
    if (!DebugToolsSelector_IsTargetAvailable(kind, nodeId, chapterId))
        return 0;

    if (DebugToolsSelector_MakeTargetId(kind, chapterMode, nodeId) == targetId)
    {
        *outIndex = *currentIndex;
        return 1;
    }

    (*currentIndex)++;
    return 0;
}

static int DebugToolsSelector_FindTargetIndex(u16 targetId, int* outIndex)
{
    int currentIndex = 0;
    int nodeId;

    if (outIndex == NULL)
        return 0;

    DebugToolsSelector_RecordEnumeration();

    for (nodeId = 0; nodeId < NODE_MAX; ++nodeId)
    {
        int eirikaChapter = gWMNodeData[nodeId].chapteridx_eirika;
        int ephraimChapter = gWMNodeData[nodeId].chapteridx_ephram;

        if (eirikaChapter == ephraimChapter)
        {
            if (DebugToolsSelector_FindTargetIndexEmit(
                    targetId,
                    &currentIndex,
                    DEBUGTOOLS_LAUNCH_TARGET_CHAPTER,
                    CHAPTER_MODE_COMMON,
                    nodeId,
                    eirikaChapter,
                    outIndex)
                || DebugToolsSelector_FindTargetIndexEmit(
                    targetId,
                    &currentIndex,
                    DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH,
                    CHAPTER_MODE_COMMON,
                    nodeId,
                    eirikaChapter,
                    outIndex))
                return 1;
        }
        else
        {
            if (DebugToolsSelector_FindTargetIndexEmit(
                    targetId,
                    &currentIndex,
                    DEBUGTOOLS_LAUNCH_TARGET_CHAPTER,
                    CHAPTER_MODE_EIRIKA,
                    nodeId,
                    eirikaChapter,
                    outIndex)
                || DebugToolsSelector_FindTargetIndexEmit(
                    targetId,
                    &currentIndex,
                    DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH,
                    CHAPTER_MODE_EIRIKA,
                    nodeId,
                    eirikaChapter,
                    outIndex)
                || DebugToolsSelector_FindTargetIndexEmit(
                    targetId,
                    &currentIndex,
                    DEBUGTOOLS_LAUNCH_TARGET_CHAPTER,
                    CHAPTER_MODE_EPHRAIM,
                    nodeId,
                    ephraimChapter,
                    outIndex)
                || DebugToolsSelector_FindTargetIndexEmit(
                    targetId,
                    &currentIndex,
                    DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH,
                    CHAPTER_MODE_EPHRAIM,
                    nodeId,
                    ephraimChapter,
                    outIndex))
                return 1;
        }
    }

    return 0;
}

static int DebugToolsSelector_FindTargetById(
    u16 targetId,
    struct DebugToolsLaunchTarget* out,
    int* outIndex)
{
    if (!DebugToolsSelector_DecodeTargetId(targetId, out))
        return 0;

    if (outIndex != NULL
        && !DebugToolsSelector_FindTargetIndex(targetId, outIndex))
        return 0;

    return 1;
}

enum DebugToolsLaunchRequestResult DebugTools_RequestTargetLaunchWithOrigin(
    u16 targetId,
    enum DebugToolsLaunchRequestOrigin origin)
{
    struct DebugToolsLaunchTarget target;

    if (sSelectorState.pending)
        return DEBUGTOOLS_LAUNCH_REQUEST_BUSY;

    if (origin != DEBUGTOOLS_LAUNCH_REQUEST_ORIGIN_DIRECT
        && origin != DEBUGTOOLS_LAUNCH_REQUEST_ORIGIN_CH4_PREP_COMPAT)
        return DEBUGTOOLS_LAUNCH_REQUEST_INVALID;

    if (!DebugToolsSelector_FindTargetById(targetId, &target, NULL))
        return DebugToolsSelector_IsTargetIdShapeValid(targetId)
            ? DEBUGTOOLS_LAUNCH_REQUEST_UNAVAILABLE
            : DEBUGTOOLS_LAUNCH_REQUEST_INVALID;

    sSelectorState.targetId = target.id;
    sSelectorState.requestOrigin = origin;
    sSelectorState.pending = 1;
    return DEBUGTOOLS_LAUNCH_REQUEST_OK;
}

enum DebugToolsLaunchRequestResult DebugTools_RequestTargetLaunch(u16 targetId)
{
    return DebugTools_RequestTargetLaunchWithOrigin(
        targetId,
        DEBUGTOOLS_LAUNCH_REQUEST_ORIGIN_DIRECT);
}

u16 DebugTools_GetSelectedTargetId(void)
{
    return sSelectorState.targetId;
}

int DebugTools_IsTargetLaunchPending(void)
{
    return sSelectorState.pending;
}

int DebugTools_ConsumePendingTargetLaunch(struct DebugToolsLaunchRequest* out)
{
    struct DebugToolsLaunchTarget target;

    if (!sSelectorState.pending || out == NULL)
        return 0;

    if (!DebugToolsSelector_FindTargetById(
            sSelectorState.targetId,
            &target,
            NULL))
    {
        sSelectorState.pending = 0;
        return 0;
    }

    out->targetId = target.id;
    out->kind = target.kind;
    out->chapterMode = target.chapterMode;
    out->nodeId = target.nodeId;
    out->chapterId = target.chapterId;
    out->encounterChoice = target.encounterChoice;
    out->origin = sSelectorState.requestOrigin;
    sSelectorState.pending = 0;
    return 1;
}

static int DebugToolsSelector_Draw(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    struct DebugToolsLaunchTarget target;
    const char* kindLabel;
    const char* modeLabel = NULL;

    ClearText(&item->text);

    if (!DebugToolsSelector_FindTargetById(
            sSelectorState.targetId,
            &target,
            NULL))
    {
        Text_InsertDrawString(
            &item->text,
            0,
            TEXT_COLOR_SYSTEM_GRAY,
            ExpansionLocale_ResolveCurrent(
                EXP_MSG_DEBUG_SELECTOR_UNAVAILABLE));
    }
    else
    {
        kindLabel = ExpansionLocale_ResolveCurrent(
            target.kind == DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH
                ? EXP_MSG_DEBUG_SELECTOR_SKIRMISH
                : EXP_MSG_DEBUG_SELECTOR_CHAPTER);

        if (target.chapterMode == CHAPTER_MODE_EIRIKA)
            modeLabel = ExpansionLocale_ResolveCurrent(
                EXP_MSG_DEBUG_SELECTOR_EIRIKA);
        else if (target.chapterMode == CHAPTER_MODE_EPHRAIM)
            modeLabel = ExpansionLocale_ResolveCurrent(
                EXP_MSG_DEBUG_SELECTOR_EPHRAIM);

        Text_InsertDrawString(
            &item->text,
            0,
            TEXT_COLOR_SYSTEM_WHITE,
            kindLabel);
        Text_InsertDrawNumberOrBlank(
            &item->text,
            72,
            TEXT_COLOR_SYSTEM_BLUE,
            target.chapterId);

        if (modeLabel != NULL)
            Text_InsertDrawString(
                &item->text,
                96,
                TEXT_COLOR_SYSTEM_GREEN,
                modeLabel);
    }

    PutText(
        &item->text,
        TILEMAP_LOCATED(
            BG_GetMapBuffer(menu->frontBg),
            item->xTile,
            item->yTile));
    return 0;
}

static u8 DebugToolsSelector_Idle(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    struct DebugToolsLaunchTarget target;
    int count;
    int targetIndex;

    if (!(gKeyStatusPtr->newKeys & (DPAD_LEFT | DPAD_RIGHT)))
        return 0;

    count = DebugTools_GetLaunchTargetCount();
    if (count <= 0)
        return 0;

    if (!DebugToolsSelector_FindTargetById(
            sSelectorState.targetId,
            NULL,
            &targetIndex))
        targetIndex = 0;

    if (gKeyStatusPtr->newKeys & DPAD_LEFT)
    {
        if (targetIndex == 0)
            targetIndex = count - 1;
        else
            targetIndex--;
    }
    else
    {
        targetIndex++;
        if (targetIndex >= count)
            targetIndex = 0;
    }

    if (DebugTools_GetLaunchTarget(targetIndex, &target))
        sSelectorState.targetId = target.id;

    DebugToolsSelector_Draw(menu, item);
    return 0;
}

static u8 DebugToolsSelector_Selected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    struct DebugToolsLaunchTarget target;
    enum DebugToolsLaunchRequestResult result;
    enum DebugToolsLaunchRequestOrigin origin;

    (void)menu;
    (void)item;

    if (!DebugToolsSelector_FindTargetById(
            sSelectorState.targetId,
            &target,
            NULL))
        return MENU_ACT_SND6B;

    origin = (gKeyStatusPtr->newKeys & L_BUTTON)
        ? DEBUGTOOLS_LAUNCH_REQUEST_ORIGIN_CH4_PREP_COMPAT
        : DEBUGTOOLS_LAUNCH_REQUEST_ORIGIN_DIRECT;
    result = DebugTools_RequestTargetLaunchWithOrigin(target.id, origin);
    if (result != DEBUGTOOLS_LAUNCH_REQUEST_OK)
        return MENU_ACT_SND6B;

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

static void DebugToolsSelector_OnEnd(struct MenuProc* menu)
{
    if (sSelectorState.pending)
    {
        DebugTools_EndSessionAfterMenuEnd(menu);
        return;
    }

    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

CONST_DATA static struct MenuItemDef sSelectorMenuItemDefs[] = {
    {
        .isAvailable = MenuAlwaysEnabled,
        .onDraw = DebugToolsSelector_Draw,
        .onSelected = DebugToolsSelector_Selected,
        .onIdle = DebugToolsSelector_Idle,
    },
    { 0 },
};

CONST_DATA struct MenuDef gDebugToolsChapterSelectorMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sSelectorMenuItemDefs,
    0,
    DebugToolsSelector_OnEnd,
    0,
    MenuCancelSelect,
    0,
    0,
};

static int DebugToolsSelector_FindDefaultTarget(void)
{
    struct DebugToolsLaunchTarget target;
    u16 targetId = DebugToolsSelector_MakeTargetId(
        DEBUGTOOLS_LAUNCH_TARGET_CHAPTER,
        CHAPTER_MODE_COMMON,
        NODE_ZAHA_WOODS);

    if (!DebugToolsSelector_DecodeTargetId(targetId, &target))
        return 0;

    return target.id;
}

static u8 DebugToolsSelector_ActionSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    int count;

    (void)item;

    count = DebugTools_GetLaunchTargetCount();
    if (count <= 0)
        return MENU_ACT_SND6B;

    {
        struct DebugToolsLaunchTarget target;
        u16 targetId = DebugToolsSelector_FindDefaultTarget();

        if (!DebugToolsSelector_DecodeTargetId(targetId, &target))
            return MENU_ACT_SND6B;

        sSelectorState.targetId = target.id;
    }
    DebugTools_QueueSubmenuTransition(
        menu,
        &gDebugToolsChapterSelectorMenuDef);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

CONST_DATA static struct DebugToolsAction sChapterSelectorAction = {
    4, "Chapter/Skirmish", DebugToolsSelector_ActionSelected
};

void DebugTools_RegisterChapterSelectorAction(void)
{
    DebugTools_RegisterBuiltinAction(&sChapterSelectorAction);
}

int DebugTools_QueueMapLaunchHandoff(void)
{
    struct DebugToolsSelectorHandoffProc* handoff;

    if (!sSelectorState.pending)
        return 0;

    if (DebugTools_IsHubActive())
        return 1;

    if (sSelectorState.mapHandoffScheduled)
        return 1;

    if (Proc_Find(gProc_BMapMain) == NULL || GetGameControl() == NULL)
        return 0;

    sSelectorState.mapHandoffScheduled = 1;
    handoff = Proc_Start(
        gProcScr_DebugToolsSelectorMapHandoff,
        PROC_TREE_3);
    handoff->timer = 0;
    return 1;
}

void DebugToolsSelector_RunMapHandoff(ProcPtr proc)
{
    struct DebugToolsSelectorHandoffProc* handoff = proc;
    struct GameCtrlProc* gameControl;

    if (DebugTools_IsHubActive())
        return;

    if (!sSelectorState.pending)
    {
        Proc_Break(proc);
        return;
    }

    gameControl = GetGameControl();
    if (Proc_Find(gProc_BMapMain) == NULL || gameControl == NULL)
    {
        if (++handoff->timer >= DEBUGTOOLS_SELECTOR_MAP_HANDOFF_TIMEOUT)
        {
            sSelectorState.pending = 0;
            Proc_Break(proc);
        }
        return;
    }

    SetNextGameActionId(GAME_ACTION_EVENT_RETURN);
    EndBMapMain();
    Proc_Goto(gameControl, LGAMECTRL_POST_TITLE_IDLE);
    Proc_Break(proc);
}

void DebugToolsSelector_MapHandoffOnEnd(ProcPtr proc)
{
    (void)proc;
    sSelectorState.mapHandoffScheduled = 0;
}

struct ProcCmd CONST_DATA gProcScr_DebugToolsSelectorMapHandoff[] = {
    PROC_SET_END_CB(DebugToolsSelector_MapHandoffOnEnd),
    PROC_YIELD,
    PROC_REPEAT(DebugToolsSelector_RunMapHandoff),
    PROC_END,
};

#endif /* FE8_EXPANSION_DEBUGTOOLS_ENABLED */
#endif /* FE8_ARCHIVAL_BUILD */
