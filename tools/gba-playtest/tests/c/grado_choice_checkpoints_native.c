#include "global.h"

#include "agb_sram.h"
#include "bmsave.h"
#include "eventinfo.h"
#include "fontgrp.h"
#include "hardware.h"
#include "uimenu.h"
#include "uiutils.h"
#include "grado_campaign_state.h"

#include <string.h>

enum GradoChoiceFamily
{
    CHOICE_LEGITIMACY,
    CHOICE_RENAIS,
    CHOICE_SALTROAD,
    CHOICE_TRUTH,
    CHOICE_RATION,
    CHOICE_WYVERN,
    CHOICE_VILLAGE,
    CHOICE_RAUSTEN_MED,
    CHOICE_RAUSTEN_DATA,
    CHOICE_DIRECTION,
    CHOICE_FAMILY_COUNT,
};

enum
{
    CHOICE_DEFAULT_COUNT = 9,
    CHOICE_OUTCOME_COUNT = 31,
    CHOICE_DIRECTION_HISTORY_COUNT = 14,
    CHOICE_DEFAULT_RECORD_WORDS = 6,
    CHOICE_OUTCOME_RECORD_WORDS = 22,
    CHOICE_BASE_GOLD = 1234,
    CHOICE_SKIP_KEYS = A_BUTTON | B_BUTTON | START_BUTTON | SELECT_BUTTON,
};

struct GradoChoiceOption
{
    u16 value;
    const char * label;
};

struct GradoChoiceDefinition
{
    enum GradoStateSymbol symbol;
    u8 optionCount;
    struct GradoChoiceOption options[4];
};

struct GradoChoiceDefaultRecord
{
    u32 family;
    u32 expectedValue;
    u32 actualValue;
    u32 preflightRecovery;
    u32 loadedAndRecovered;
    u32 savePreserved;
};

struct GradoChoiceOutcomeRecord
{
    u32 family;
    u32 optionIndex;
    u32 expectedValue;
    u32 actualValue;
    u32 baseGold;
    u32 committedGold;
    u32 displayedLabelHash;
    u32 displayedLabelLength;
    u32 pendingWithoutEffects;
    u32 freshConfirmation;
    u32 cancelReturnedToChoices;
    u32 duplicateBlocked;
    u32 resourceApplyCount;
    u32 normalRestored;
    u32 newChapterRetained;
    u32 newChapterCleared;
    u32 suspendRestored;
    u32 suspendSourceSlot;
    u32 suspendBackingSlot;
    u32 pendingRollbackRestored;
    u32 pendingNormalSaveRejected;
    u32 pendingSuspendSaveRejected;
};

struct GradoChoiceDirectionHistoryRecord
{
    u32 historyIndex;
    u32 targetDirection;
    u32 drawnLabelCount;
    u32 choiceValues[CHOICE_DEFAULT_COUNT];
    u32 displayedLabelHashes[4];
    u32 displayedLabelLengths[4];
};

struct GradoChoiceCheckpointProbe
{
    u32 phase;
    u32 outcomeCount;
    u32 supportedEpoch;
    u32 heldSkipBlocked;
    u32 pendingSaveBlocked;
    u32 missingDirectionReopened;
    u32 directionNotDefaulted;
    u32 directionMenuReady;
    u32 epoch2Refused;
    u32 exiledNormalRecovered;
    u32 exiledSuspendRecovered;
    u32 directionSourceSlot;
    u32 directionBackingSlot;
    u32 raustenDualPreserved;
    u32 directionPriorChoiceRestored;
    u32 directionResourcesRestored;
    u32 directionCostAppliedFromRestoredGold;
    u32 pendingNormalSaveRejected;
    u32 pendingSuspendSaveRejected;
    u32 preChoiceNormalSaveAllowed;
    u32 preChoiceSuspendSaveAllowed;
    u32 completedNormalSaveAllowed;
    u32 completedSuspendSaveAllowed;
    u32 directionHistoryCount;
    struct GradoChoiceDefaultRecord defaults[CHOICE_DEFAULT_COUNT];
    struct GradoChoiceOutcomeRecord outcomes[CHOICE_OUTCOME_COUNT];
    struct GradoChoiceDirectionHistoryRecord
        directionHistories[CHOICE_DIRECTION_HISTORY_COUNT];
};

struct GradoChoiceTransaction
{
    enum GradoChoiceFamily family;
    const struct GradoChoiceDefinition * definition;
    volatile struct GradoChoiceOutcomeRecord * record;
    u8 triggerFired;
    u8 pending;
    u8 committed;
    u8 resourceApplied;
    u8 phase;
    u8 selectedIndex;
    u16 pendingValue;
    u16 pendingGoldDelta;
    u32 applyCount;
    struct MenuProc choiceMenu;
    struct MenuProc confirmMenu;
    struct MenuItemProc choiceItems[4];
    struct MenuItemProc confirmItems[2];
};

enum
{
    CHOICE_PHASE_IDLE,
    CHOICE_PHASE_WAIT_SKIP_RELEASE,
    CHOICE_PHASE_CHOOSING,
    CHOICE_PHASE_WAIT_CONFIRM_RELEASE,
    CHOICE_PHASE_CONFIRMING,
    CHOICE_PHASE_COMPLETE,
    CHOICE_PHASE_ERROR,
};

static const struct GradoChoiceDefinition sChoices[CHOICE_FAMILY_COUNT] = {
    {
        GRADO_STATE_LEGITIMACY, 3,
        {
            { 1, "CENTRAL | Cost: local resistance" },
            { 2, "COUNCIL | Cost: slower enforcement" },
            { 3, "LOCAL | Cost: raid pressure" },
        },
    },
    {
        GRADO_STATE_RENAIS, 3,
        {
            { 1, "FULL | Cost: recurring payments" },
            { 2, "LIMITED | Cost: delayed accountability" },
            { 3, "DECLINE | Cost: lost grain aid" },
        },
    },
    {
        GRADO_STATE_SALTROAD, 3,
        {
            { 1, "SUPPRESS | Cost: local alienation" },
            { 2, "CONCEDE | Cost: central revenue" },
            { 3, "AUTONOMY | Cost: weaker central authority" },
        },
    },
    {
        GRADO_STATE_TRUTH, 3,
        {
            { 1, "FULL_PUBLIC | Cost: military morale" },
            { 2, "LIMITED | Cost: public distrust" },
            { 3, "SEALED | Cost: foreign trust" },
        },
    },
    {
        GRADO_STATE_RATION, 3,
        {
            { 1, "CAPITAL | Cost: remote districts abandoned" },
            { 2, "FRONTIER | Cost: capital supply" },
            { 3, "EVEN | Cost: moderate losses everywhere" },
        },
    },
    {
        GRADO_STATE_WYVERN, 3,
        {
            { 1, "SHIELD | Cost: weaker central deterrence" },
            { 2, "STATE_ARMY | Cost: local distrust" },
            { 3, "DISBAND_MIN | Cost: weaker air rescue" },
        },
    },
    {
        GRADO_STATE_VILLAGE, 3,
        {
            { 1, "SELF_RULE | Cost: weaker coordination" },
            { 2, "GARRISON | Cost: local backlash" },
            { 3, "MEDIATE | Cost: slower results" },
        },
    },
    {
        GRADO_STATE_RAUSTEN_MED, 3,
        {
            { 1, "FULL | Cost: lower temple autonomy" },
            { 2, "LIMITED | Cost: continued mutual distrust" },
            { 3, "REFUSE | Cost: no outside medical teams" },
        },
    },
    {
        GRADO_STATE_RAUSTEN_DATA, 3,
        {
            { 1, "SURRENDER | Cost: knowledge and rewards lost" },
            { 2, "JOINT | Cost: leak risk" },
            { 3, "RETAIN | Cost: less foreign support" },
        },
    },
    {
        GRADO_STATE_DIRECTION, 4,
        {
            { 1, NULL },
            { 2, NULL },
            { 3, NULL },
            { 4, NULL },
        },
    },
};

static const char * const sDirectionLabels[4][3] = {
    {
        "CENTRAL | Cost: favorable transition",
        "CENTRAL | Cost: moderate transition",
        "CENTRAL | Cost: adverse transition",
    },
    {
        "AUTONOMY | Cost: favorable transition",
        "AUTONOMY | Cost: moderate transition",
        "AUTONOMY | Cost: adverse transition",
    },
    {
        "FOREIGN | Cost: favorable transition",
        "FOREIGN | Cost: moderate transition",
        "FOREIGN | Cost: adverse transition",
    },
    {
        "RECKONING | Cost: favorable transition",
        "RECKONING | Cost: moderate transition",
        "RECKONING | Cost: adverse transition",
    },
};

struct GradoChoiceDirectionHistoryInput
{
    u32 targetDirection;
    u16 choices[CHOICE_DEFAULT_COUNT];
};

static const struct GradoChoiceDirectionHistoryInput
sDirectionHistoryInputs[CHOICE_DIRECTION_HISTORY_COUNT] = {
    { 1, { 1, 2, 2, 2, 2, 2, 2, 2, 2 } },
    { 1, { 2, 2, 2, 2, 2, 1, 2, 2, 1 } },
    { 1, { 3, 2, 2, 2, 2, 1, 1, 2, 1 } },
    { 2, { 3, 2, 2, 2, 1, 1, 1, 2, 1 } },
    { 2, { 2, 2, 2, 2, 1, 1, 2, 2, 1 } },
    { 2, { 3, 2, 1, 2, 1, 2, 1, 2, 1 } },
    { 2, { 1, 2, 2, 2, 1, 1, 2, 2, 1 } },
    { 3, { 2, 1, 2, 1, 2, 1, 2, 1, 1 } },
    { 3, { 2, 2, 2, 1, 2, 1, 2, 2, 1 } },
    { 3, { 2, 1, 2, 3, 2, 1, 2, 1, 3 } },
    { 3, { 2, 3, 2, 1, 2, 1, 2, 3, 1 } },
    { 4, { 2, 2, 2, 1, 2, 1, 2, 1, 1 } },
    { 4, { 2, 2, 2, 2, 2, 1, 2, 1, 1 } },
    { 4, { 2, 2, 2, 3, 2, 1, 2, 1, 1 } },
};

static const struct
{
    enum GradoChoiceFamily family;
    enum GradoStateSymbol producer;
    u16 defaultValue;
} sChoiceDefaults[CHOICE_DEFAULT_COUNT] = {
    { CHOICE_LEGITIMACY, GRADO_FLAG_CH02_CLEAR, 2 },
    { CHOICE_RENAIS, GRADO_FLAG_CH06_CLEAR, 2 },
    { CHOICE_SALTROAD, GRADO_FLAG_CH08_CLEAR, 2 },
    { CHOICE_TRUTH, GRADO_FLAG_CH10_CLEAR, 2 },
    { CHOICE_RATION, GRADO_FLAG_CH11_CLEAR, 3 },
    { CHOICE_WYVERN, GRADO_FLAG_CH13_CLEAR, 3 },
    { CHOICE_VILLAGE, GRADO_FLAG_CH14_CLEAR, 3 },
    { CHOICE_RAUSTEN_MED, GRADO_FLAG_CH15_CLEAR, 2 },
    { CHOICE_RAUSTEN_DATA, GRADO_FLAG_CH15_CLEAR, 2 },
};

static volatile struct GradoChoiceCheckpointProbe gGradoChoiceCheckpointProbe;
static struct GradoChoiceTransaction sTransaction;
static struct MenuDef sChoiceMenuDef;
static struct MenuDef sConfirmMenuDef;
static struct MenuItemDef sChoiceMenuItems[4];
static struct MenuItemDef sConfirmMenuItems[2];
static u32 sDrawHashes[4];
static u32 sDrawLengths[4];
static u32 sDrawCount;
static u8 sBefore[CART_SRAM_SIZE];

static u32 GradoChoice_HashString(const char * text)
{
    u32 hash = 2166136261u;

    while (*text)
    {
        hash ^= (u8)*text++;
        hash *= 16777619u;
    }

    return hash;
}

void Text_DrawString(struct Text * text, const char * string)
{
    (void)text;

    if (sDrawCount < ARRAY_COUNT(sDrawHashes))
    {
        sDrawHashes[sDrawCount] = GradoChoice_HashString(string);
        sDrawLengths[sDrawCount] = strlen(string);
    }

    ++sDrawCount;
}

char * GetStringFromIndex(int textId)
{
    (void)textId;
    return "";
}

void Text_SetColor(struct Text * text, int colorId)
{
    (void)text;
    (void)colorId;
}

void PutText(struct Text * text, u16 * dest)
{
    (void)text;
    (void)dest;
}

void DrawUiFrame(
    u16 * tilemap, int x, int y, int width, int height, int tilebase, int style)
{
    (void)tilemap;
    (void)x;
    (void)y;
    (void)width;
    (void)height;
    (void)tilebase;
    (void)style;
}

void ClearUiFrame(u16 * tilemap, int x, int y, int width, int height)
{
    (void)tilemap;
    (void)x;
    (void)y;
    (void)width;
    (void)height;
}

void DrawUiItemHoverExt(int bg, int base, int x, int y, int width)
{
    (void)bg;
    (void)base;
    (void)x;
    (void)y;
    (void)width;
}

void ClearUiItemHoverExt(int bg, int base, int x, int y, int width)
{
    (void)bg;
    (void)base;
    (void)x;
    (void)y;
    (void)width;
}

void Proc_End(ProcPtr proc)
{
    (void)proc;
}

void DisplayFrozenUiHand(int x, int y)
{
    (void)x;
    (void)y;
}

void DisplayUiHand(int x, int y)
{
    (void)x;
    (void)y;
}

void EndFaceById(int faceSlot)
{
    (void)faceSlot;
}

static void GradoChoice_PrepareCurrent(void)
{
    struct GlobalSaveInfo * info;

    memset(gSram, 0, CART_SRAM_SIZE);
    SramInit();
    info = &gSram->globalSaveInfo;
    memcpy(info->name, "AGB-FE9", sizeof("AGB-FE9"));
    info->magic32 = SAVEMAGIC32;
    info->magic16 = SAVEMAGIC16;
    info->checksum = Checksum16(info, GLOBALSIZEINFO_SIZE_FOR_CHECKSUM);
    BuildCurrentExpansionSaveMeta(&gSram->expansionSaveMeta);
}

static void GradoChoice_PrepareUnits(struct Unit * units, unsigned int count)
{
    unsigned int i;

    for (i = 0; i < count; ++i)
    {
        units[i].pCharacterData = gCharacterData;
        units[i].pClassData = gClassData;
        units[i].level = 7;
        units[i].exp = 33;
        units[i].xPos = 2;
        units[i].yPos = 3;
        units[i].maxHP = 23;
        units[i].curHP = 19;
    }
}

static void GradoChoice_PrepareLive(void)
{
    memset(&gPlaySt, 0, sizeof(gPlaySt));
    InitUnits();
    GradoChoice_PrepareUnits(gUnitArrayBlue, UNIT_SAVE_AMOUNT_BLUE);
    GradoChoice_PrepareUnits(gUnitArrayRed, UNIT_SAVE_AMOUNT_RED);
    GradoChoice_PrepareUnits(gUnitArrayGreen, UNIT_SAVE_AMOUNT_GREEN);
    gPlaySt.partyGoldAmount = CHOICE_BASE_GOLD;
    gPlaySt.chapterIndex = 0;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME0;
}

static void GradoChoice_SeedAllChoices(void)
{
    GradoState_WriteSymbol(GRADO_STATE_LEGITIMACY, 0, 3);
    GradoState_WriteSymbol(GRADO_STATE_RENAIS, 0, 2);
    GradoState_WriteSymbol(GRADO_STATE_SALTROAD, 0, 3);
    GradoState_WriteSymbol(GRADO_STATE_TRUTH, 0, 1);
    GradoState_WriteSymbol(GRADO_STATE_RATION, 0, 2);
    GradoState_WriteSymbol(GRADO_STATE_WYVERN, 0, 3);
    GradoState_WriteSymbol(GRADO_STATE_VILLAGE, 0, 1);
    GradoState_WriteSymbol(GRADO_STATE_RAUSTEN_MED, 0, 1);
    GradoState_WriteSymbol(GRADO_STATE_RAUSTEN_DATA, 0, 2);
    GradoState_WriteSymbol(GRADO_STATE_DIRECTION, 0, 4);
    GradoState_WriteSymbol(GRADO_FLAG_CH07_GUARD_KEPT, 8, 2);
    GradoState_WriteSymbol(GRADO_FLAG_CH09_MEDNET, 10, 2);
}

static int GradoChoice_ReadValue(enum GradoStateSymbol symbol, u16 * value)
{
    return GradoState_ReadSymbol(symbol, GRADO_STATE_NO_CHAPTER, value)
        == GRADO_STATE_VALUE_VALID;
}

static unsigned int GradoChoice_DirectionCost(unsigned int direction)
{
    u16 legitimacy;
    u16 renais;
    u16 saltroad;
    u16 truth;
    u16 ration;
    u16 wyvern;
    u16 village;
    u16 raustenMed;
    u16 raustenData;
    int foundation;
    int conflict;

    if (!GradoChoice_ReadValue(GRADO_STATE_LEGITIMACY, &legitimacy)
        || !GradoChoice_ReadValue(GRADO_STATE_RENAIS, &renais)
        || !GradoChoice_ReadValue(GRADO_STATE_SALTROAD, &saltroad)
        || !GradoChoice_ReadValue(GRADO_STATE_TRUTH, &truth)
        || !GradoChoice_ReadValue(GRADO_STATE_RATION, &ration)
        || !GradoChoice_ReadValue(GRADO_STATE_WYVERN, &wyvern)
        || !GradoChoice_ReadValue(GRADO_STATE_VILLAGE, &village)
        || !GradoChoice_ReadValue(GRADO_STATE_RAUSTEN_MED, &raustenMed)
        || !GradoChoice_ReadValue(GRADO_STATE_RAUSTEN_DATA, &raustenData))
        return 1;

    switch (direction)
    {
    case 1:
        foundation = (legitimacy == 1 && wyvern == 2)
            || (saltroad == 1 && village == 2);
        conflict = (legitimacy == 3 && village == 1)
            || (saltroad == 3 && wyvern == 3);
        break;

    case 2:
        foundation = (legitimacy == 3 && village == 1)
            || (saltroad == 3 && ration == 2);
        conflict = (legitimacy == 1 && village == 2)
            || (saltroad == 1 && wyvern == 2);
        break;

    case 3:
        foundation = (renais == 1 && raustenMed == 1)
            || (renais == 2 && raustenData == 2);
        conflict = (renais == 3 && raustenMed == 3)
            || (raustenData == 3 && truth == 3);
        break;

    case 4:
        foundation = (truth == 1 && raustenData == 2)
            || (truth == 1 && renais != 3);
        conflict = (truth == 3) || (truth == 2 && renais == 3);
        break;

    default:
        return 1;
    }

    if (foundation != conflict)
        return foundation ? 0 : 2;

    return 1;
}

static const char * GradoChoice_Label(unsigned int family, unsigned int option)
{
    if (family == CHOICE_DIRECTION)
        return sDirectionLabels[option][GradoChoice_DirectionCost(option + 1)];

    return sChoices[family].options[option].label;
}

static void GradoChoice_SetInput(u16 newKeys, u16 repeatedKeys, u16 heldKeys)
{
    gKeyStatusPtr->newKeys = newKeys;
    gKeyStatusPtr->repeatedKeys = repeatedKeys;
    gKeyStatusPtr->heldKeys = heldKeys;
}

static int GradoChoice_CanSave(void)
{
    return !sTransaction.pending;
}

static int GradoChoice_RequestGameSave(u8 slot)
{
    if (!GradoChoice_CanSave())
        return 0;

    WriteGameSave(slot);
    return 1;
}

static int GradoChoice_RequestSuspendSave(u8 slot)
{
    if (!GradoChoice_CanSave())
        return 0;

    WriteSuspendSave(slot);
    return 1;
}

static u16 GradoChoice_GoldDelta(unsigned int family, unsigned int option)
{
    return family * 4 + option + 1;
}

static u8 GradoChoice_ChoiceSelected(
    struct MenuProc * menu, struct MenuItemProc * item);
static u8 GradoChoice_ConfirmSelected(
    struct MenuProc * menu, struct MenuItemProc * item);
static u8 GradoChoice_ConfirmBack(
    struct MenuProc * menu, struct MenuItemProc * item);

static void GradoChoice_StartMenu(
    struct MenuProc * menu,
    struct MenuItemProc * procs,
    struct MenuItemDef * defs,
    const struct MenuDef * definition,
    unsigned int count)
{
    unsigned int i;

    memset(menu, 0, sizeof(*menu));
    menu->def = definition;
    menu->rect = definition->rect;
    menu->state = MENU_STATE_FLAT;
    menu->itemCount = count;
    menu->itemCurrent = 0;
    menu->itemPrevious = 0;

    for (i = 0; i < count; ++i)
    {
        memset(procs + i, 0, sizeof(procs[i]));
        procs[i].def = defs + i;
        procs[i].itemNumber = i;
        procs[i].availability = defs[i].isAvailable
            ? defs[i].isAvailable(defs + i, i) : MENU_ENABLED;
        procs[i].xTile = 1;
        procs[i].yTile = i + 1;
        menu->menuItems[i] = procs + i;
    }

    Menu_OnInit(menu);
}

static void GradoChoice_DrawMenu(struct MenuProc * menu)
{
    sDrawCount = 0;
    RedrawMenu(menu);
}

static void GradoChoice_StartChoiceMenu(void)
{
    const struct GradoChoiceDefinition * definition =
        sTransaction.definition;
    unsigned int i;

    sChoiceMenuDef.rect = (struct MenuRect){ 0, 0, 26, 9 };
    sChoiceMenuDef.menuItems = sChoiceMenuItems;
    sChoiceMenuDef.onBPress = NULL;

    for (i = 0; i < definition->optionCount; ++i)
    {
        sChoiceMenuItems[i].name = GradoChoice_Label(sTransaction.family, i);
        sChoiceMenuItems[i].nameMsgId = 0;
        sChoiceMenuItems[i].color = 0;
        sChoiceMenuItems[i].isAvailable = MenuAlwaysEnabled;
        sChoiceMenuItems[i].onSelected = GradoChoice_ChoiceSelected;
        sChoiceMenuItems[i].onIdle = NULL;
        sChoiceMenuItems[i].onSwitchIn = NULL;
        sChoiceMenuItems[i].onSwitchOut = NULL;
    }

    GradoChoice_StartMenu(
        &sTransaction.choiceMenu, sTransaction.choiceItems, sChoiceMenuItems,
        &sChoiceMenuDef, definition->optionCount);
    GradoChoice_DrawMenu(&sTransaction.choiceMenu);
}

static void GradoChoice_StartConfirmMenu(void)
{
    sConfirmMenuItems[0].name = "Confirm";
    sConfirmMenuItems[0].nameMsgId = 0;
    sConfirmMenuItems[0].color = 0;
    sConfirmMenuItems[0].isAvailable = MenuAlwaysEnabled;
    sConfirmMenuItems[0].onSelected = GradoChoice_ConfirmSelected;
    sConfirmMenuItems[0].onIdle = NULL;
    sConfirmMenuItems[0].onSwitchIn = NULL;
    sConfirmMenuItems[0].onSwitchOut = NULL;

    sConfirmMenuItems[1].name = "Choose again";
    sConfirmMenuItems[1].nameMsgId = 0;
    sConfirmMenuItems[1].color = 0;
    sConfirmMenuItems[1].isAvailable = MenuAlwaysEnabled;
    sConfirmMenuItems[1].onSelected = GradoChoice_ConfirmSelected;
    sConfirmMenuItems[1].onIdle = NULL;
    sConfirmMenuItems[1].onSwitchIn = NULL;
    sConfirmMenuItems[1].onSwitchOut = NULL;

    sConfirmMenuDef.rect = (struct MenuRect){ 0, 0, 16, 5 };
    sConfirmMenuDef.menuItems = sConfirmMenuItems;
    sConfirmMenuDef.onBPress = GradoChoice_ConfirmBack;
    GradoChoice_StartMenu(
        &sTransaction.confirmMenu, sTransaction.confirmItems, sConfirmMenuItems,
        &sConfirmMenuDef, 2);
    GradoChoice_DrawMenu(&sTransaction.confirmMenu);
}

static void GradoChoice_Begin(
    enum GradoChoiceFamily family,
    volatile struct GradoChoiceOutcomeRecord * record)
{
    u16 currentValue;
    enum GradoStateValueResult result;

    memset(&sTransaction, 0, sizeof(sTransaction));
    sTransaction.family = family;
    sTransaction.definition = sChoices + family;
    sTransaction.record = record;
    result = GradoState_ReadSymbol(
        sTransaction.definition->symbol, GRADO_STATE_NO_CHAPTER, &currentValue);
    if (result == GRADO_STATE_VALUE_VALID)
    {
        sTransaction.committed = 1;
        sTransaction.resourceApplied = 1;
        sTransaction.phase = CHOICE_PHASE_COMPLETE;
        return;
    }

    if (result != GRADO_STATE_VALUE_UNSET)
    {
        sTransaction.phase = CHOICE_PHASE_ERROR;
        return;
    }

    sTransaction.phase = CHOICE_PHASE_IDLE;
}

static void GradoChoice_Trigger(void)
{
    if (sTransaction.triggerFired || sTransaction.committed
        || sTransaction.phase == CHOICE_PHASE_ERROR)
        return;

    sTransaction.triggerFired = 1;
    sTransaction.phase = CHOICE_PHASE_WAIT_SKIP_RELEASE;
}

static void GradoChoice_Advance(void)
{
    if (sTransaction.phase != CHOICE_PHASE_WAIT_SKIP_RELEASE
        && sTransaction.phase != CHOICE_PHASE_WAIT_CONFIRM_RELEASE)
        return;

    if ((gKeyStatusPtr->heldKeys & CHOICE_SKIP_KEYS) != 0
        || gKeyStatusPtr->newKeys != 0)
        return;

    if (sTransaction.phase == CHOICE_PHASE_WAIT_SKIP_RELEASE)
    {
        sTransaction.phase = CHOICE_PHASE_CHOOSING;
        GradoChoice_StartChoiceMenu();
    }
    else
    {
        sTransaction.phase = CHOICE_PHASE_CONFIRMING;
        GradoChoice_StartConfirmMenu();
    }
}

static u8 GradoChoice_ChoiceSelected(
    struct MenuProc * menu, struct MenuItemProc * item)
{
    const struct GradoChoiceOption * option;
    unsigned int index;

    (void)menu;
    index = item->itemNumber;
    if (sTransaction.phase != CHOICE_PHASE_CHOOSING
        || !sTransaction.triggerFired || sTransaction.pending
        || sTransaction.committed || index >= sTransaction.definition->optionCount)
        return MENU_ACT_SKIPCURSOR;

    option = sTransaction.definition->options + index;
    sTransaction.selectedIndex = index;
    sTransaction.pendingValue = option->value;
    sTransaction.pendingGoldDelta = GradoChoice_GoldDelta(
        sTransaction.family, index);
    sTransaction.pending = 1;
    sTransaction.phase = CHOICE_PHASE_WAIT_CONFIRM_RELEASE;
    return MENU_ACT_SKIPCURSOR;
}

static void GradoChoice_CancelPending(void)
{
    sTransaction.pending = 0;
    sTransaction.pendingValue = 0;
    sTransaction.pendingGoldDelta = 0;
    sTransaction.phase = CHOICE_PHASE_CHOOSING;
    GradoChoice_StartChoiceMenu();
}

static u8 GradoChoice_ConfirmSelected(
    struct MenuProc * menu, struct MenuItemProc * item)
{
    u32 newGold;
    enum GradoStateValueResult result;

    (void)menu;
    if (sTransaction.phase != CHOICE_PHASE_CONFIRMING
        || !sTransaction.pending || sTransaction.committed)
        return MENU_ACT_SKIPCURSOR;

    if (item->itemNumber != 0)
    {
        GradoChoice_CancelPending();
        return MENU_ACT_SKIPCURSOR;
    }

    if (gPlaySt.partyGoldAmount + sTransaction.pendingGoldDelta > 0xFFFF)
        return MENU_ACT_SKIPCURSOR;

    newGold = gPlaySt.partyGoldAmount + sTransaction.pendingGoldDelta;
    result = GradoState_WriteSymbol(
        sTransaction.definition->symbol, GRADO_STATE_NO_CHAPTER,
        sTransaction.pendingValue);
    if (result != GRADO_STATE_VALUE_VALID)
    {
        sTransaction.phase = CHOICE_PHASE_ERROR;
        return MENU_ACT_SKIPCURSOR;
    }

    gPlaySt.partyGoldAmount = newGold;
    sTransaction.pending = 0;
    sTransaction.committed = 1;
    sTransaction.resourceApplied = 1;
    ++sTransaction.applyCount;
    sTransaction.phase = CHOICE_PHASE_COMPLETE;
    return MENU_ACT_SKIPCURSOR;
}

static u8 GradoChoice_ConfirmBack(
    struct MenuProc * menu, struct MenuItemProc * item)
{
    (void)menu;
    (void)item;

    if (sTransaction.phase == CHOICE_PHASE_CONFIRMING && sTransaction.pending)
        GradoChoice_CancelPending();

    return MENU_ACT_SKIPCURSOR;
}

static void GradoChoice_SelectOutcome(unsigned int optionIndex)
{
    unsigned int i;

    for (i = 0; i < optionIndex; ++i)
    {
        GradoChoice_SetInput(DPAD_DOWN, DPAD_DOWN, DPAD_DOWN);
        Menu_OnIdle(&sTransaction.choiceMenu);
        GradoChoice_SetInput(0, 0, 0);
    }

    GradoChoice_SetInput(A_BUTTON, 0, A_BUTTON);
    Menu_OnIdle(&sTransaction.choiceMenu);
}

static int GradoChoice_ConfirmWithFreshInput(void)
{
    int heldWasBlocked;
    int confirmWasOpened;

    GradoChoice_SetInput(0, 0, A_BUTTON);
    GradoChoice_Advance();
    heldWasBlocked = sTransaction.phase == CHOICE_PHASE_WAIT_CONFIRM_RELEASE
        && sTransaction.pending && !sTransaction.committed;

    GradoChoice_SetInput(0, 0, 0);
    GradoChoice_Advance();
    confirmWasOpened = sTransaction.phase == CHOICE_PHASE_CONFIRMING
        && sTransaction.pending && !sTransaction.committed;

    GradoChoice_SetInput(A_BUTTON, 0, A_BUTTON);
    Menu_OnIdle(&sTransaction.confirmMenu);
    return heldWasBlocked && confirmWasOpened
        && sTransaction.committed && !sTransaction.pending
        && sTransaction.resourceApplied && sTransaction.applyCount == 1;
}

static int GradoChoice_CommitFixture(
    enum GradoChoiceFamily family, unsigned int option, u32 expectedGold)
{
    u16 value;
    u32 initialGold = gPlaySt.partyGoldAmount;

    GradoChoice_Begin(family, NULL);
    GradoChoice_Trigger();
    GradoChoice_SetInput(0, 0, 0);
    GradoChoice_Advance();
    if (sTransaction.phase != CHOICE_PHASE_CHOOSING)
        return 0;

    GradoChoice_SelectOutcome(option);
    if (sTransaction.phase != CHOICE_PHASE_WAIT_CONFIRM_RELEASE
        || !sTransaction.pending || sTransaction.committed
        || GradoChoice_CanSave()
        || GradoState_ReadSymbol(
            sChoices[family].symbol, GRADO_STATE_NO_CHAPTER, &value)
                != GRADO_STATE_VALUE_UNSET
        || gPlaySt.partyGoldAmount != initialGold)
        return 0;

    return GradoChoice_ConfirmWithFreshInput()
        && GradoChoice_ReadValue(sChoices[family].symbol, &value)
        && value == sChoices[family].options[option].value
        && gPlaySt.partyGoldAmount == expectedGold;
}

static void GradoChoice_RunRaustenDual(void)
{
    struct GradoStateLoadDiagnostic diagnostic;
    enum GradoStateLoadResult result;
    u16 medValue = 0;
    u16 dataValue = 0;
    u32 expectedGold = CHOICE_BASE_GOLD + GradoChoice_GoldDelta(CHOICE_RAUSTEN_MED, 0)
        + GradoChoice_GoldDelta(CHOICE_RAUSTEN_DATA, 2);

    GradoChoice_PrepareCurrent();
    GradoChoice_PrepareLive();
    GradoState_BeginLifecycle(GRADO_LOAD_NEW_GAME);
    gPlaySt.partyGoldAmount = CHOICE_BASE_GOLD;
    gPlaySt.chapterIndex = 0;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME0;
    if (!GradoChoice_RequestGameSave(SAVE_ID_GAME0))
        return;
    gGradoChoiceCheckpointProbe.preChoiceNormalSaveAllowed = 1;

    if (!GradoChoice_CommitFixture(
            CHOICE_RAUSTEN_MED, 0,
            CHOICE_BASE_GOLD + GradoChoice_GoldDelta(CHOICE_RAUSTEN_MED, 0))
        || !GradoChoice_CommitFixture(
            CHOICE_RAUSTEN_DATA, 2, expectedGold))
        return;

    if (!GradoChoice_RequestGameSave(SAVE_ID_GAME0))
        return;
    gGradoChoiceCheckpointProbe.completedNormalSaveAllowed = 1;
    memset(GetPermanentFlagBits(), 0, GetPermanentFlagBitsSize());
    gPlaySt.partyGoldAmount = 0;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME2;
    result = GradoState_LoadNativeSave(
        GRADO_LOAD_NORMAL, SAVE_ID_GAME0, &diagnostic);
    GradoChoice_Begin(CHOICE_RAUSTEN_MED, NULL);
    GradoChoice_Trigger();
    GradoChoice_Begin(CHOICE_RAUSTEN_DATA, NULL);
    GradoChoice_Trigger();
    gGradoChoiceCheckpointProbe.raustenDualPreserved =
        result == GRADO_STATE_LOAD_VALID
        && GradoChoice_ReadValue(GRADO_STATE_RAUSTEN_MED, &medValue)
        && medValue == 1
        && GradoChoice_ReadValue(GRADO_STATE_RAUSTEN_DATA, &dataValue)
        && dataValue == 3
        && gPlaySt.partyGoldAmount == expectedGold
        && sTransaction.committed && !sTransaction.pending
        && !sTransaction.triggerFired
        && sTransaction.applyCount == 0;
}

static void GradoChoice_SetOutcomeState(
    enum GradoChoiceFamily family)
{
    GradoChoice_PrepareCurrent();
    GradoChoice_PrepareLive();
    GradoState_BeginLifecycle(GRADO_LOAD_NEW_GAME);
    if (family == CHOICE_DIRECTION)
    {
        GradoChoice_SeedAllChoices();
        GradoState_WriteSymbol(
            GRADO_STATE_DIRECTION, GRADO_STATE_NO_CHAPTER, 0);
    }
}

static void GradoChoice_RunOutcome(
    enum GradoChoiceFamily family, unsigned int option, int exerciseSkip)
{
    volatile struct GradoChoiceOutcomeRecord * record;
    struct GradoStateLoadDiagnostic diagnostic;
    enum GradoStateLoadResult loadResult;
    u16 currentValue;
    u16 localValue;
    u32 beforeGold;
    u32 committedGold;
    unsigned int recordIndex;
    int pendingWithoutEffects;
    int duplicateWhilePending;
    int cancelled;
    int duplicateAfterCommit;

    GradoChoice_SetOutcomeState(family);
    gPlaySt.partyGoldAmount = CHOICE_BASE_GOLD;
    gPlaySt.chapterIndex = 0;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME0;
    if (!GradoChoice_RequestGameSave(SAVE_ID_GAME0))
        return;
    gGradoChoiceCheckpointProbe.preChoiceNormalSaveAllowed = 1;

    recordIndex = gGradoChoiceCheckpointProbe.outcomeCount++;
    record = &gGradoChoiceCheckpointProbe.outcomes[recordIndex];
    record->family = family;
    record->optionIndex = option;
    record->expectedValue = sChoices[family].options[option].value;
    record->baseGold = CHOICE_BASE_GOLD;
    record->displayedLabelHash = 0;
    record->displayedLabelLength = 0;
    record->pendingWithoutEffects = 0;
    record->freshConfirmation = 0;
    record->cancelReturnedToChoices = 0;
    record->duplicateBlocked = 0;
    record->resourceApplyCount = 0;
    record->normalRestored = 0;
    record->newChapterRetained = 0;
    record->newChapterCleared = 0;
    record->suspendRestored = 0;
    record->suspendSourceSlot = GRADO_STATE_NO_NATIVE_SAVE_SLOT;
    record->suspendBackingSlot = GRADO_STATE_NO_NATIVE_SAVE_SLOT;
    record->pendingRollbackRestored = 0;
    record->pendingNormalSaveRejected = 0;
    record->pendingSuspendSaveRejected = 0;

    beforeGold = gPlaySt.partyGoldAmount;
    GradoChoice_Begin(family, record);
    if (exerciseSkip)
    {
        GradoChoice_SetInput(0, 0, A_BUTTON);
        GradoChoice_Trigger();
        GradoChoice_Advance();
        GradoChoice_Trigger();
        gGradoChoiceCheckpointProbe.heldSkipBlocked =
            sTransaction.phase == CHOICE_PHASE_WAIT_SKIP_RELEASE
            && sTransaction.triggerFired && !sTransaction.pending
            && !sTransaction.committed
            && GradoState_ReadSymbol(
                sChoices[family].symbol, 0, &currentValue)
                    == GRADO_STATE_VALUE_UNSET
            && gPlaySt.partyGoldAmount == beforeGold;
        GradoChoice_SetInput(0, 0, 0);
        GradoChoice_Advance();
    }
    else
    {
        GradoChoice_Trigger();
        GradoChoice_Trigger();
        GradoChoice_SetInput(0, 0, 0);
        GradoChoice_Advance();
    }

    if (sTransaction.phase != CHOICE_PHASE_CHOOSING)
        return;

    GradoChoice_DrawMenu(&sTransaction.choiceMenu);
    if (option < sDrawCount)
    {
        record->displayedLabelHash = sDrawHashes[option];
        record->displayedLabelLength = sDrawLengths[option];
    }

    GradoChoice_SelectOutcome(option);
    pendingWithoutEffects =
        sTransaction.phase == CHOICE_PHASE_WAIT_CONFIRM_RELEASE
        && sTransaction.triggerFired && sTransaction.pending
        && !sTransaction.committed && !sTransaction.resourceApplied
        && GradoState_ReadSymbol(
            sChoices[family].symbol, 0, &currentValue) == GRADO_STATE_VALUE_UNSET
        && gPlaySt.partyGoldAmount == beforeGold;
    duplicateWhilePending = sTransaction.pending
        && sTransaction.pendingValue == sChoices[family].options[option].value
        && !GradoChoice_CanSave();
    GradoChoice_Trigger();
    duplicateWhilePending = duplicateWhilePending
        && sTransaction.phase == CHOICE_PHASE_WAIT_CONFIRM_RELEASE
        && sTransaction.pending && !sTransaction.committed
        && GradoState_ReadSymbol(
            sChoices[family].symbol, 0, &currentValue) == GRADO_STATE_VALUE_UNSET
        && gPlaySt.partyGoldAmount == beforeGold;
    memcpy(sBefore, gSram, sizeof(sBefore));
    record->pendingNormalSaveRejected =
        !GradoChoice_RequestGameSave(SAVE_ID_GAME0)
        && memcmp(sBefore, gSram, sizeof(sBefore)) == 0;
    record->pendingSuspendSaveRejected =
        !GradoChoice_RequestSuspendSave(SAVE_ID_SUSPEND)
        && memcmp(sBefore, gSram, sizeof(sBefore)) == 0;
    gGradoChoiceCheckpointProbe.pendingNormalSaveRejected &=
        record->pendingNormalSaveRejected;
    gGradoChoiceCheckpointProbe.pendingSuspendSaveRejected &=
        record->pendingSuspendSaveRejected;
    gGradoChoiceCheckpointProbe.pendingSaveBlocked =
        gGradoChoiceCheckpointProbe.pendingNormalSaveRejected
        && gGradoChoiceCheckpointProbe.pendingSuspendSaveRejected;

    memset(&sTransaction, 0, sizeof(sTransaction));
    memset(GetPermanentFlagBits(), 0, GetPermanentFlagBitsSize());
    memset(GetChapterFlagBits(), 0, GetChapterFlagBitsSize());
    gPlaySt.partyGoldAmount = 0;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME2;
    loadResult = GradoState_LoadNativeSave(
        GRADO_LOAD_NORMAL, SAVE_ID_GAME0, &diagnostic);
    record->pendingRollbackRestored =
        loadResult == GRADO_STATE_LOAD_VALID
        && sTransaction.phase == CHOICE_PHASE_IDLE
        && !sTransaction.pending && !sTransaction.committed
        && !sTransaction.resourceApplied && sTransaction.applyCount == 0
        && GradoState_ReadSymbol(
            sChoices[family].symbol, 0, &currentValue)
                == GRADO_STATE_VALUE_UNSET
        && gPlaySt.partyGoldAmount == beforeGold
        && GradoChoice_CanSave();
    if (!record->pendingRollbackRestored)
        return;

    GradoChoice_Begin(family, record);
    GradoChoice_Trigger();
    GradoChoice_SetInput(0, 0, 0);
    GradoChoice_Advance();
    if (sTransaction.phase != CHOICE_PHASE_CHOOSING)
        return;
    GradoChoice_SelectOutcome(option);

    if (family == CHOICE_LEGITIMACY && option == 0)
    {
        GradoChoice_SetInput(0, 0, 0);
        GradoChoice_Advance();
        GradoChoice_SetInput(B_BUTTON, 0, B_BUTTON);
        Menu_OnIdle(&sTransaction.confirmMenu);
        cancelled = sTransaction.phase == CHOICE_PHASE_CHOOSING
            && !sTransaction.pending && !sTransaction.committed
            && GradoState_ReadSymbol(
                sChoices[family].symbol, 0, &currentValue)
                    == GRADO_STATE_VALUE_UNSET
            && gPlaySt.partyGoldAmount == beforeGold;
        record->cancelReturnedToChoices = cancelled;
        GradoChoice_SetInput(0, 0, 0);
        GradoChoice_SelectOutcome(option);
    }
    else
    {
        cancelled = 1;
    }

    record->pendingWithoutEffects = pendingWithoutEffects && duplicateWhilePending;
    record->freshConfirmation = GradoChoice_ConfirmWithFreshInput();
    record->resourceApplyCount = sTransaction.applyCount;
    committedGold = gPlaySt.partyGoldAmount;
    record->committedGold = committedGold;

    duplicateAfterCommit = sTransaction.committed && !sTransaction.pending
        && sTransaction.resourceApplied && sTransaction.applyCount == 1;
    GradoChoice_Trigger();
    duplicateAfterCommit = duplicateAfterCommit
        && sTransaction.phase == CHOICE_PHASE_COMPLETE
        && sTransaction.applyCount == 1
        && gPlaySt.partyGoldAmount == committedGold;
    GradoChoice_SetInput(DPAD_DOWN, DPAD_DOWN, DPAD_DOWN);
    Menu_OnIdle(&sTransaction.confirmMenu);
    GradoChoice_SetInput(0, 0, 0);
    GradoChoice_SetInput(A_BUTTON, 0, A_BUTTON);
    Menu_OnIdle(&sTransaction.confirmMenu);
    duplicateAfterCommit = duplicateAfterCommit
        && sTransaction.phase == CHOICE_PHASE_COMPLETE
        && sTransaction.committed && !sTransaction.pending
        && sTransaction.resourceApplied && sTransaction.applyCount == 1
        && gPlaySt.partyGoldAmount == committedGold
        && GradoChoice_ReadValue(sChoices[family].symbol, &currentValue)
        && currentValue == record->expectedValue;

    record->actualValue = GradoChoice_ReadValue(
        sChoices[family].symbol, &currentValue) ? currentValue : 0xFFFFFFFFu;

    if (!GradoChoice_RequestGameSave(SAVE_ID_GAME0))
        return;
    gGradoChoiceCheckpointProbe.completedNormalSaveAllowed = 1;
    memset(GetPermanentFlagBits(), 0, GetPermanentFlagBitsSize());
    gPlaySt.partyGoldAmount = 0;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME2;
    memset(&sTransaction, 0, sizeof(sTransaction));
    loadResult = GradoState_LoadNativeSave(
        GRADO_LOAD_NORMAL, SAVE_ID_GAME0, &diagnostic);
    record->normalRestored = loadResult == GRADO_STATE_LOAD_VALID
        && GradoChoice_ReadValue(sChoices[family].symbol, &currentValue)
        && currentValue == record->expectedValue
        && gPlaySt.partyGoldAmount == committedGold
        && duplicateAfterCommit;

    GradoState_WriteSymbol(GRADO_FLAG_CH00_WARN, 0, 1);
    loadResult = GradoState_BeginLifecycle(GRADO_LOAD_NEW_CHAPTER);
    record->newChapterRetained = loadResult == GRADO_STATE_LOAD_VALID
        && GradoChoice_ReadValue(sChoices[family].symbol, &currentValue)
        && currentValue == record->expectedValue;
    record->newChapterCleared =
        GradoState_ReadSymbol(GRADO_FLAG_CH00_WARN, 0, &localValue)
            == GRADO_STATE_VALUE_VALID
        && localValue == 0;

    GradoState_WriteSymbol(GRADO_FLAG_CH00_WARN, 0, 1);
    gPlaySt.chapterIndex = 0;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME0;
    if (!GradoChoice_RequestGameSave(SAVE_ID_GAME0)
        || !GradoChoice_RequestSuspendSave(SAVE_ID_SUSPEND))
        return;
    gGradoChoiceCheckpointProbe.completedNormalSaveAllowed = 1;
    gGradoChoiceCheckpointProbe.completedSuspendSaveAllowed = 1;
    memset(GetPermanentFlagBits(), 0, GetPermanentFlagBitsSize());
    memset(GetChapterFlagBits(), 0, GetChapterFlagBitsSize());
    gPlaySt.partyGoldAmount = 0;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME2;
    memset(&sTransaction, 0, sizeof(sTransaction));
    loadResult = GradoState_LoadNativeSave(
        GRADO_LOAD_SUSPEND, SAVE_ID_SUSPEND, &diagnostic);
    record->suspendSourceSlot = diagnostic.sourceSlot;
    record->suspendBackingSlot = diagnostic.backingSlot;
    record->suspendRestored = loadResult == GRADO_STATE_LOAD_VALID
        && GradoChoice_ReadValue(sChoices[family].symbol, &currentValue)
        && currentValue == record->expectedValue
        && GradoState_ReadSymbol(GRADO_FLAG_CH00_WARN, 0, &localValue)
            == GRADO_STATE_VALUE_VALID
        && localValue == 1
        && gPlaySt.partyGoldAmount == committedGold
        && diagnostic.backingSlot == SAVE_ID_GAME0
        && (diagnostic.sourceSlot == SAVE_ID_SUSPEND
            || diagnostic.sourceSlot == SAVE_ID_SUSPEND + 1)
        && ReadSaveBlockInfo(NULL, diagnostic.sourceSlot);

    GradoChoice_Begin(family, record);
    beforeGold = gPlaySt.partyGoldAmount;
    GradoChoice_Trigger();
    record->duplicateBlocked = duplicateAfterCommit
        && sTransaction.committed && !sTransaction.pending
        && sTransaction.resourceApplied && sTransaction.applyCount == 0
        && sTransaction.phase == CHOICE_PHASE_COMPLETE
        && gPlaySt.partyGoldAmount == beforeGold;

    if (family == CHOICE_LEGITIMACY && option == 0 && !cancelled)
        record->cancelReturnedToChoices = 0;
}

static void GradoChoice_RunDefaults(void)
{
    unsigned int i;

    for (i = 0; i < CHOICE_DEFAULT_COUNT; ++i)
    {
        volatile struct GradoChoiceDefaultRecord * record;
        struct GradoStateLoadDiagnostic diagnostic;
        enum GradoStateLoadResult preflight;
        enum GradoStateLoadResult loaded;
        u16 actualValue = 0;
        unsigned int gameOffset;
        unsigned int family = sChoiceDefaults[i].family;
        enum GradoStateSymbol symbol = sChoices[family].symbol;

        gGradoChoiceCheckpointProbe.phase = 20 + i;
        GradoChoice_PrepareCurrent();
        gGradoChoiceCheckpointProbe.phase = 30 + i;
        GradoChoice_PrepareLive();
        gGradoChoiceCheckpointProbe.phase = 40 + i;
        GradoState_BeginLifecycle(GRADO_LOAD_NEW_GAME);
        gGradoChoiceCheckpointProbe.phase = 50 + i;
        GradoChoice_SeedAllChoices();
        gGradoChoiceCheckpointProbe.phase = 60 + i;
        GradoState_WriteSymbol(symbol, GRADO_STATE_NO_CHAPTER, 0);
        GradoState_WriteSymbol(sChoiceDefaults[i].producer, 0, 1);
        gGradoChoiceCheckpointProbe.phase = 70 + i;
        gPlaySt.chapterIndex = 21;
        gPlaySt.gameSaveSlot = SAVE_ID_GAME0;
        if (!GradoChoice_RequestGameSave(SAVE_ID_GAME0))
            continue;
        gGradoChoiceCheckpointProbe.phase = 80 + i;
        memcpy(sBefore, gSram, sizeof(sBefore));
        gameOffset = SramAddrToOffset(GetSaveReadAddr(SAVE_ID_GAME0));

        record = &gGradoChoiceCheckpointProbe.defaults[i];
        record->family = family;
        record->expectedValue = sChoiceDefaults[i].defaultValue;
        preflight = GradoState_PreflightNativeSave(
            GRADO_LOAD_NORMAL, SAVE_ID_GAME0, &diagnostic);
        gGradoChoiceCheckpointProbe.phase = 90 + i;
        record->preflightRecovery = preflight == GRADO_STATE_LOAD_RECOVERY
            && diagnostic.missingSymbol == symbol
            && diagnostic.producerSymbol == sChoiceDefaults[i].producer
            && diagnostic.recovery == GRADO_STATE_RECOVERY_CHOICE
            && memcmp(sBefore, gSram, sizeof(sBefore)) == 0;
        memset(GetPermanentFlagBits(), 0, GetPermanentFlagBitsSize());
        gPlaySt.partyGoldAmount = 0;
        loaded = GradoState_LoadNativeSave(
            GRADO_LOAD_NORMAL, SAVE_ID_GAME0, &diagnostic);
        record->loadedAndRecovered = loaded == GRADO_STATE_LOAD_VALID
            && diagnostic.recoveredCount == 1
            && diagnostic.recoveredSymbols[0] == symbol
            && GradoState_ReadSymbol(symbol, GRADO_STATE_NO_CHAPTER, &actualValue)
                == GRADO_STATE_VALUE_VALID
            && actualValue == sChoiceDefaults[i].defaultValue;
        record->actualValue = actualValue;
        record->savePreserved = memcmp(
            sBefore + gameOffset, GetSaveReadAddr(SAVE_ID_GAME0),
            sizeof(struct GameSaveBlock)) == 0;
        gGradoChoiceCheckpointProbe.phase = 100 + i;
    }
}

static void GradoChoice_RunMissingDirection(void)
{
    struct GradoStateLoadDiagnostic diagnostic;
    enum GradoStateLoadResult preflight;
    enum GradoStateLoadResult loaded;
    u16 legitimacy = 0;
    u16 direction = 0;
    u32 savedGold = 2345;

    GradoChoice_PrepareCurrent();
    GradoChoice_PrepareLive();
    GradoState_BeginLifecycle(GRADO_LOAD_NEW_GAME);
    GradoChoice_SeedAllChoices();
    GradoState_WriteSymbol(GRADO_STATE_LEGITIMACY, 0, 2);
    GradoState_WriteSymbol(
        GRADO_STATE_DIRECTION, GRADO_STATE_NO_CHAPTER, 0);
    GradoState_WriteSymbol(GRADO_FLAG_CH18_CLEAR, 0, 1);
    gPlaySt.chapterIndex = 21;
    gPlaySt.partyGoldAmount = savedGold;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME0;
    if (!GradoChoice_RequestGameSave(SAVE_ID_GAME0)
        || !GradoChoice_RequestSuspendSave(SAVE_ID_SUSPEND))
        return;
    gGradoChoiceCheckpointProbe.preChoiceNormalSaveAllowed = 1;
    gGradoChoiceCheckpointProbe.preChoiceSuspendSaveAllowed = 1;

    preflight = GradoState_PreflightNativeSave(
        GRADO_LOAD_SUSPEND, SAVE_ID_SUSPEND, &diagnostic);
    GradoChoice_SetInput(0, 0, 0);
    memset(GetPermanentFlagBits(), 0, GetPermanentFlagBitsSize());
    memset(GetChapterFlagBits(), 0, GetChapterFlagBitsSize());
    gPlaySt.partyGoldAmount = 0x5555;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME2;
    memset(&sTransaction, 0, sizeof(sTransaction));
    loaded = GradoState_LoadNativeSave(
        GRADO_LOAD_SUSPEND, SAVE_ID_SUSPEND, &diagnostic);
    gGradoChoiceCheckpointProbe.directionPriorChoiceRestored =
        loaded == GRADO_STATE_LOAD_REOPEN
        && GradoState_ReadSymbol(
            GRADO_STATE_LEGITIMACY, 0, &legitimacy) == GRADO_STATE_VALUE_VALID
        && legitimacy == 2;
    gGradoChoiceCheckpointProbe.directionResourcesRestored =
        loaded == GRADO_STATE_LOAD_REOPEN
        && gPlaySt.partyGoldAmount == savedGold
        && gPlaySt.chapterIndex == 21;
    GradoState_ReadSymbol(
        GRADO_STATE_DIRECTION, GRADO_STATE_NO_CHAPTER, &direction);
    gGradoChoiceCheckpointProbe.directionSourceSlot = diagnostic.sourceSlot;
    gGradoChoiceCheckpointProbe.directionBackingSlot = diagnostic.backingSlot;
    gGradoChoiceCheckpointProbe.missingDirectionReopened =
        preflight == GRADO_STATE_LOAD_REOPEN
        && loaded == GRADO_STATE_LOAD_REOPEN && direction == 0;

    GradoChoice_Begin(CHOICE_DIRECTION, NULL);
    GradoChoice_Trigger();
    GradoChoice_Advance();
    gGradoChoiceCheckpointProbe.directionNotDefaulted =
        GradoState_ReadSymbol(
            GRADO_STATE_DIRECTION, GRADO_STATE_NO_CHAPTER, &direction)
                == GRADO_STATE_VALUE_UNSET
        && sTransaction.phase == CHOICE_PHASE_CHOOSING
        && sTransaction.choiceMenu.itemCurrent == 0
        && !sTransaction.pending && !sTransaction.committed;
    gGradoChoiceCheckpointProbe.directionMenuReady =
        sTransaction.choiceMenu.itemCount == 4
        && sTransaction.choiceItems[0].availability == MENU_ENABLED
        && sTransaction.choiceItems[1].availability == MENU_ENABLED
        && sTransaction.choiceItems[2].availability == MENU_ENABLED
        && sTransaction.choiceItems[3].availability == MENU_ENABLED;
    GradoChoice_SelectOutcome(0);
    gGradoChoiceCheckpointProbe.directionCostAppliedFromRestoredGold =
        gGradoChoiceCheckpointProbe.directionPriorChoiceRestored
        && gGradoChoiceCheckpointProbe.directionResourcesRestored
        && GradoChoice_ConfirmWithFreshInput()
        && GradoChoice_ReadValue(GRADO_STATE_DIRECTION, &direction)
        && direction == 1
        && gPlaySt.partyGoldAmount
            == savedGold + GradoChoice_GoldDelta(CHOICE_DIRECTION, 0);
}

static void GradoChoice_RunDirectionHistories(void)
{
    unsigned int historyIndex;

    for (historyIndex = 0;
        historyIndex < CHOICE_DIRECTION_HISTORY_COUNT;
        ++historyIndex)
    {
        const struct GradoChoiceDirectionHistoryInput * input =
            sDirectionHistoryInputs + historyIndex;
        volatile struct GradoChoiceDirectionHistoryRecord * record =
            &gGradoChoiceCheckpointProbe.directionHistories[historyIndex];
        unsigned int i;

        GradoChoice_PrepareCurrent();
        GradoChoice_PrepareLive();
        GradoState_BeginLifecycle(GRADO_LOAD_NEW_GAME);
        for (i = 0; i < CHOICE_DEFAULT_COUNT; ++i)
        {
            if (GradoState_WriteSymbol(
                    sChoices[i].symbol, GRADO_STATE_NO_CHAPTER,
                    input->choices[i]) != GRADO_STATE_VALUE_VALID)
                return;
        }
        if (GradoState_WriteSymbol(
                GRADO_STATE_DIRECTION, GRADO_STATE_NO_CHAPTER, 0)
            != GRADO_STATE_VALUE_VALID)
            return;

        GradoChoice_Begin(CHOICE_DIRECTION, NULL);
        GradoChoice_Trigger();
        GradoChoice_SetInput(0, 0, 0);
        GradoChoice_Advance();
        if (sTransaction.phase != CHOICE_PHASE_CHOOSING)
            return;

        GradoChoice_DrawMenu(&sTransaction.choiceMenu);
        record->historyIndex = historyIndex;
        record->targetDirection = input->targetDirection;
        record->drawnLabelCount = sDrawCount;
        for (i = 0; i < CHOICE_DEFAULT_COUNT; ++i)
            record->choiceValues[i] = input->choices[i];
        for (i = 0; i < ARRAY_COUNT(record->displayedLabelHashes); ++i)
        {
            record->displayedLabelHashes[i] = sDrawHashes[i];
            record->displayedLabelLengths[i] = sDrawLengths[i];
        }
        if (sDrawCount != ARRAY_COUNT(record->displayedLabelHashes))
            return;

        ++gGradoChoiceCheckpointProbe.directionHistoryCount;
    }
}

static void GradoChoice_RunExiledRoute(enum GradoStateLoadRoute route, int * passed)
{
    struct GradoStateLoadDiagnostic diagnostic;
    enum GradoStateLoadResult result;
    u16 roderich = 0;
    u32 savedGold;
    unsigned int gameOffset;
    unsigned int sourceSlot;

    GradoChoice_PrepareCurrent();
    GradoChoice_PrepareLive();
    GradoState_BeginLifecycle(GRADO_LOAD_NEW_GAME);
    GradoChoice_SeedAllChoices();
    GradoState_WriteSymbol(GRADO_STATE_RODERICH, 19, GRADO_RODERICH_CAPTURED);
    GradoState_RecordResolution(GRADO_STATE_RODERICH, 19);
    GradoState_WriteSymbol(GRADO_STATE_RODERICH, 19, 0);
    GradoState_WriteSymbol(GRADO_FLAG_CH02_CLEAR, 0, 1);
    GradoState_WriteSymbol(GRADO_FLAG_CH06_CLEAR, 0, 1);
    GradoState_WriteSymbol(GRADO_FLAG_CH07_CLEAR, 0, 1);
    GradoState_WriteSymbol(GRADO_FLAG_CH08_CLEAR, 0, 1);
    GradoState_WriteSymbol(GRADO_FLAG_CH09_CLEAR, 0, 1);
    GradoState_WriteSymbol(GRADO_FLAG_CH10_CLEAR, 0, 1);
    gPlaySt.chapterIndex = 21;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME0;
    savedGold = gPlaySt.partyGoldAmount;
    if (!GradoChoice_RequestGameSave(SAVE_ID_GAME0))
        return;
    if (route == GRADO_LOAD_SUSPEND)
    {
        if (!GradoChoice_RequestSuspendSave(SAVE_ID_SUSPEND))
            return;
    }

    memcpy(sBefore, gSram, sizeof(sBefore));
    gameOffset = SramAddrToOffset(GetSaveReadAddr(SAVE_ID_GAME0));
    sourceSlot = route == GRADO_LOAD_SUSPEND
        ? SAVE_ID_SUSPEND + GetLastSuspendSaveId() : SAVE_ID_GAME0;
    memset(GetPermanentFlagBits(), 0, GetPermanentFlagBitsSize());
    memset(GetChapterFlagBits(), 0, GetChapterFlagBitsSize());
    gPlaySt.partyGoldAmount = 0;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME2;
    result = GradoState_LoadNativeSave(
        route,
        route == GRADO_LOAD_SUSPEND ? SAVE_ID_SUSPEND : SAVE_ID_GAME0,
        &diagnostic);
    *passed = result == GRADO_STATE_LOAD_VALID
        && diagnostic.recoveredCount == 1
        && diagnostic.recoveredSymbols[0] == GRADO_STATE_RODERICH
        && GradoState_ReadSymbol(
            GRADO_STATE_RODERICH, GRADO_STATE_NO_CHAPTER, &roderich)
                == GRADO_STATE_VALUE_VALID
        && roderich == GRADO_RODERICH_EXILED
        && gPlaySt.partyGoldAmount == savedGold
        && gPlaySt.gameSaveSlot == SAVE_ID_GAME0
        && gUnitArrayBlue[0].pCharacterData == gCharacterData
        && gUnitArrayBlue[0].level == 7
        && memcmp(sBefore + gameOffset, GetSaveReadAddr(SAVE_ID_GAME0),
            sizeof(struct GameSaveBlock)) == 0
        && diagnostic.sourceSlot == sourceSlot
        && (route != GRADO_LOAD_SUSPEND
            || diagnostic.backingSlot == SAVE_ID_GAME0);
}

static void GradoChoice_RunEpoch2Control(void)
{
    struct GradoStateLoadDiagnostic diagnostic;
    struct PlaySt beforePlay;
    u8 beforePermanent[GRADO_STATE_PERMANENT_BYTES];
    u8 beforeChapter[GRADO_STATE_CHAPTER_BYTES];
    enum GradoStateLoadResult result;

    GradoChoice_PrepareCurrent();
    GradoChoice_PrepareLive();
    GradoState_BeginLifecycle(GRADO_LOAD_NEW_GAME);
    gPlaySt.gameSaveSlot = SAVE_ID_GAME0;
    if (!GradoChoice_RequestGameSave(SAVE_ID_GAME0))
        return;
    memcpy(sBefore, gSram, sizeof(sBefore));
    beforePlay = gPlaySt;
    memcpy(beforePermanent, GetPermanentFlagBits(), sizeof(beforePermanent));
    memcpy(beforeChapter, GetChapterFlagBits(), sizeof(beforeChapter));
    result = GradoState_PreflightNativeSave(
        GRADO_LOAD_NORMAL, SAVE_ID_GAME0, &diagnostic);
    gGradoChoiceCheckpointProbe.epoch2Refused =
        result == GRADO_STATE_LOAD_INCOMPATIBLE
        && memcmp(sBefore, gSram, sizeof(sBefore)) == 0
        && memcmp(&beforePlay, &gPlaySt, sizeof(beforePlay)) == 0
        && memcmp(beforePermanent, GetPermanentFlagBits(), sizeof(beforePermanent)) == 0
        && memcmp(beforeChapter, GetChapterFlagBits(), sizeof(beforeChapter)) == 0;
    gGradoChoiceCheckpointProbe.phase = 2;
}

void GradoChoiceCheckpoints_Run(void)
{
    unsigned int family;
    unsigned int option;
    int exiledNormal;
    int exiledSuspend;

    memset((void *)&gGradoChoiceCheckpointProbe, 0, sizeof(gGradoChoiceCheckpointProbe));
    gGradoChoiceCheckpointProbe.phase = 1;
#if FE8_EXPANSION_SAVE_COMPAT_EPOCH != 256
    GradoChoice_RunEpoch2Control();
    return;
#else
    gGradoChoiceCheckpointProbe.supportedEpoch = 1;
    gGradoChoiceCheckpointProbe.phase = 10;
    GradoChoice_RunMissingDirection();
    GradoChoice_RunDirectionHistories();
    gGradoChoiceCheckpointProbe.phase = 11;
    GradoChoice_RunDefaults();
    gGradoChoiceCheckpointProbe.phase = 12;
    GradoChoice_RunExiledRoute(GRADO_LOAD_NORMAL, &exiledNormal);
    GradoChoice_RunExiledRoute(GRADO_LOAD_SUSPEND, &exiledSuspend);
    gGradoChoiceCheckpointProbe.exiledNormalRecovered = exiledNormal;
    gGradoChoiceCheckpointProbe.exiledSuspendRecovered = exiledSuspend;
    gGradoChoiceCheckpointProbe.phase = 13;
    GradoChoice_RunRaustenDual();
    gGradoChoiceCheckpointProbe.phase = 14;
    gGradoChoiceCheckpointProbe.phase = 15;
    gGradoChoiceCheckpointProbe.pendingNormalSaveRejected = 1;
    gGradoChoiceCheckpointProbe.pendingSuspendSaveRejected = 1;
    gGradoChoiceCheckpointProbe.pendingSaveBlocked = 1;

    for (family = 0; family < CHOICE_FAMILY_COUNT; ++family)
        for (option = 0; option < sChoices[family].optionCount; ++option)
            GradoChoice_RunOutcome(
                family, option,
                family == CHOICE_LEGITIMACY && option == 0);

    gGradoChoiceCheckpointProbe.phase = 2;
#endif
}
