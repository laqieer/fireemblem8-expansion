#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bm.h"
#include "bmmap.h"
#include "bmunit.h"
#include "constants/chapters.h"
#include "constants/characters.h"
#include "constants/event-flags.h"
#include "cp_common.h"
#include "cp_utility.h"
#include "expansion_autoplay_internal.h"
#include "expansion_autoplay_strategies.h"
#include "eventinfo.h"
#include "event.h"

#if FE8_EXPANSION_AUTOPLAY_STRATEGIES
extern bool ExpansionAutoplayStrategy_Aggressive(
    const struct ExpansionAutoplayStrategyContext* context);
extern bool ExpansionAutoplayStrategy_ObjectiveFirst(
    const struct ExpansionAutoplayStrategyContext* context);
#endif

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "AUTOPLAY_STRATEGIES_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

#define AUTOPLAY_STRATEGY_TENTATIVE_FALLBACK_ID 0xA70E0F94

struct PlaySt gPlaySt;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct AiDecision gAiDecision;
u32 gEventSlots[EVENT_SLOT_COUNT];
struct Vec2 gBmMapSize;
u8 ** gBmMapMovement;
u8 ** gBmMapRange;

static struct CharacterData sEirikaCharacter;
static struct Unit sEirika;
static struct CharacterData sSethCharacter;
static struct Unit sSeth;
static u8 sRangeData[16][16];
static u8 * sRangeRows[16];
static u8 sMovementData[16][16];
static u8 * sMovementRows[16];
static bool sFlags[0x100];
static int sSetFlagCalls[0x100];
static int sClearFlagCalls[0x100];
static bool sBlueComputerPhase;
static int sCombatCalls;
static int sMoveCalls;
static int sCombatMoveX;
static int sCombatMoveY;
static int sMoveDecisionX;
static int sMoveDecisionY;
static bool sUsePerUnitCombatMap;
static int sEirikaCombatX;
static int sEirikaCombatY;
static int sSethCombatX;
static int sSethCombatY;
static bool sEirikaHasMagicRank;
static bool sSethHasMagicRank;
static int sMagicSealX;
static int sMagicSealY;
static int sMovementMapGenerationCount;
static int sMagicSealGenerationCount;
static struct Unit* sLastMovementMapUnit;
static bool sTentativeReturnsSuccess;
static u8 sTentativeActionId;

bool CheckFlag(int flag)
{
    return flag >= 0 && flag < (int)ARRAY_COUNT(sFlags) && sFlags[flag];
}

void SetFlag(int flag)
{
    if (flag >= 0 && flag < (int)ARRAY_COUNT(sFlags))
    {
        sFlags[flag] = true;
        sSetFlagCalls[flag]++;
    }
}

void ClearFlag(int flag)
{
    if (flag >= 0 && flag < (int)ARRAY_COUNT(sFlags))
    {
        sFlags[flag] = false;
        sClearFlagCalls[flag]++;
    }
}

struct Unit* GetUnitFromCharId(int character)
{
    if (character == CHARACTER_EIRIKA)
        return &sEirika;
    if (character == CHARACTER_SETH)
        return &sSeth;
    return NULL;
}

struct Unit* GetUnit(int unitId)
{
    if (unitId == 1)
        return &sEirika;
    if (unitId == 2)
        return &sSeth;
    return NULL;
}

void AiSetDecision(s16 xMove, s16 yMove, u8 actionId, u8 targetId, u8 itemSlot, u8 xTarget, u8 yTarget)
{
    gAiDecision.xMove = xMove;
    gAiDecision.yMove = yMove;
    gAiDecision.actionId = actionId;
    gAiDecision.targetId = targetId;
    gAiDecision.itemSlot = itemSlot;
    gAiDecision.xTarget = xTarget;
    gAiDecision.yTarget = yTarget;
    gAiDecision.actionPerformed = true;
}

void AiClearDecision(void)
{
    memset(&gAiDecision, 0, sizeof(gAiDecision));
}

s8 AiAttemptCombatWithinMovement(s8 (*isEnemy)(struct Unit* unit))
{
    (void)isEnemy;
    sCombatCalls++;
    if (sCombatMoveX < 0 || sCombatMoveX >= gBmMapSize.x
        || sCombatMoveY < 0 || sCombatMoveY >= gBmMapSize.y
        || gBmMapMovement[sCombatMoveY][sCombatMoveX] > MAP_MOVEMENT_MAX)
        return 0;

    AiSetDecision(sCombatMoveX, sCombatMoveY, AI_ACTION_COMBAT, 0x81, 0, 0, 0);
    return 1;
}

s8 AiIsUnitEnemy(struct Unit* unit)
{
    (void)unit;
    return true;
}

void AiGenerateUnitMovementMapRespectStay(struct Unit* unit)
{
    int x;
    int y;
    int reachableX = sCombatMoveX;
    int reachableY = sCombatMoveY;

    sMovementMapGenerationCount++;
    sLastMovementMapUnit = unit;
    for (y = 0; y < gBmMapSize.y; y++)
        for (x = 0; x < gBmMapSize.x; x++)
            sMovementData[y][x] = 0xFF;

    sMovementData[unit->yPos][unit->xPos] = 0;
    if (sUsePerUnitCombatMap)
    {
        if (unit == &sEirika)
        {
            reachableX = sEirikaCombatX;
            reachableY = sEirikaCombatY;
        }
        else
        {
            reachableX = sSethCombatX;
            reachableY = sSethCombatY;
        }
    }

    if (reachableX >= 0 && reachableX < gBmMapSize.x
        && reachableY >= 0 && reachableY < gBmMapSize.y)
        sMovementData[reachableY][reachableX] = 1;
}

bool UnitHasMagicRank(struct Unit* unit)
{
    return unit == &sEirika ? sEirikaHasMagicRank : sSethHasMagicRank;
}

void GenerateMagicSealMap(int value)
{
    (void)value;
    sMagicSealGenerationCount++;
    if (sMagicSealX >= 0 && sMagicSealX < gBmMapSize.x
        && sMagicSealY >= 0 && sMagicSealY < gBmMapSize.y)
        sMovementData[sMagicSealY][sMagicSealX] = 0xFF;
}

void AiTryMoveTowards(s16 x, s16 y, u8 action, u8 maxDanger, u8 unk)
{
    (void)action;
    (void)maxDanger;
    (void)unk;
    sMoveCalls++;
    AiSetDecision(
        sMoveDecisionX >= 0 ? sMoveDecisionX : x,
        sMoveDecisionY >= 0 ? sMoveDecisionY : y,
        AI_ACTION_NONE,
        0,
        0,
        0,
        0
    );
}

bool ExpansionAutoplay_IsBlueComputerPhase(void)
{
    return sBlueComputerPhase;
}

static bool DummyStrategy(const struct ExpansionAutoplayStrategyContext* context)
{
    (void)context;
    return false;
}

#if FE8_EXPANSION_AUTOPLAY_STRATEGIES
bool ExpansionAutoplayStrategy_TentativeFallback(
    const struct ExpansionAutoplayStrategyContext* context)
{
    (void)context;
    AiTryMoveTowards(9, 9, 0, 0, 1);
    gAiDecision.actionId = sTentativeActionId;
    return sTentativeReturnsSuccess;
}
#endif

static void RefreshObjectiveTelemetry(void)
{
    ExpansionChapterObjectives_RefreshTelemetry();
}

static void ResetFixture(void)
{
    int index;

    for (index = 0; index < (int)ARRAY_COUNT(sFlags); index++)
    {
        sFlags[index] = false;
        sSetFlagCalls[index] = 0;
        sClearFlagCalls[index] = 0;
    }

    sEirikaCharacter.number = CHARACTER_EIRIKA;
    sEirika.pCharacterData = &sEirikaCharacter;
    sEirika.state = US_NONE;
    sEirika.xPos = 10;
    sEirika.yPos = 10;
    sSethCharacter.number = CHARACTER_SETH;
    sSeth.pCharacterData = &sSethCharacter;
    sSeth.state = US_NONE;
    sSeth.xPos = 10;
    sSeth.yPos = 10;
    gActiveUnit = &sEirika;
    gActiveUnitId = 1;
    gPlaySt.chapterIndex = CHAPTER_L_2;
    gPlaySt.faction = FACTION_RED;
    gPlaySt.chapterTurnNumber = 1;
    gAiDecision.actionPerformed = false;
    sBlueComputerPhase = false;
    sCombatCalls = 0;
    sMoveCalls = 0;
    sCombatMoveX = sEirika.xPos;
    sCombatMoveY = sEirika.yPos;
    sMoveDecisionX = 3;
    sMoveDecisionY = 3;
    sUsePerUnitCombatMap = false;
    sEirikaCombatX = sCombatMoveX;
    sEirikaCombatY = sCombatMoveY;
    sSethCombatX = sCombatMoveX;
    sSethCombatY = sCombatMoveY;
    sEirikaHasMagicRank = false;
    sSethHasMagicRank = false;
    sMagicSealX = -1;
    sMagicSealY = -1;
    sMovementMapGenerationCount = 0;
    sMagicSealGenerationCount = 0;
    sLastMovementMapUnit = NULL;
    sTentativeReturnsSuccess = false;
    sTentativeActionId = AI_ACTION_NONE;
    memset(gEventSlots, 0, sizeof(gEventSlots));
    gBmMapSize.x = 16;
    gBmMapSize.y = 16;
    for (index = 0; index < 16; index++)
    {
        sRangeRows[index] = sRangeData[index];
        sMovementRows[index] = sMovementData[index];
        memset(sRangeData[index], MAP_MOVEMENT_MAX, sizeof(sRangeData[index]));
        memset(sMovementData[index], 0xFF, sizeof(sMovementData[index]));
    }
    gBmMapMovement = sMovementRows;
    gBmMapRange = sRangeRows;
    sRangeData[sEirika.yPos][sEirika.xPos] = 10;
    sRangeData[sMoveDecisionY][sMoveDecisionX] = 5;
    ExpansionChapterObjectives_ResetTelemetry();
    ExpansionChapterObjectives_OnBeginningEventsComplete();
    ExpansionAutoplayStrategies_ResetPendingActivation();
}

static int TestRegistryFailures(void)
{
    const struct ExpansionAutoplayStrategy duplicate[] = {
        { 1, 0, EXPANSION_AUTOPLAY_STRATEGY_ACTION_COMBAT, DummyStrategy, 0 },
        { 1, 0, EXPANSION_AUTOPLAY_STRATEGY_ACTION_COMBAT, DummyStrategy, 0 },
    };
    const struct ExpansionAutoplayStrategy missingCallback[] = {
        { 1, 0, EXPANSION_AUTOPLAY_STRATEGY_ACTION_COMBAT, NULL, 0 },
    };
    const struct ExpansionAutoplayStrategy reservedSentinel[] = {
        { 0, 0, EXPANSION_AUTOPLAY_STRATEGY_ACTION_COMBAT, DummyStrategy, 0 },
    };
    const struct ExpansionAutoplayStrategy invalidCapability[] = {
        { 1, 0x80000000, EXPANSION_AUTOPLAY_STRATEGY_ACTION_COMBAT, DummyStrategy, 0 },
    };

    CHECK(
        ExpansionAutoplayStrategies_ValidateRegistry(
            duplicate, ARRAY_COUNT(duplicate)) == EXPANSION_AUTOPLAY_STRATEGY_ERR_DUPLICATE_ID,
        "duplicate IDs must fail"
    );
    CHECK(
        ExpansionAutoplayStrategies_ValidateRegistry(
            missingCallback, ARRAY_COUNT(missingCallback))
            == EXPANSION_AUTOPLAY_STRATEGY_ERR_MISSING_CALLBACK,
        "missing callback must fail"
    );
    CHECK(
        ExpansionAutoplayStrategies_ValidateRegistry(
            reservedSentinel, ARRAY_COUNT(reservedSentinel))
            == EXPANSION_AUTOPLAY_STRATEGY_ERR_UNKNOWN_ID,
        "reserved zero ID must not truncate a runtime registry"
    );
    CHECK(
        ExpansionAutoplayStrategies_ValidateRegistry(
            invalidCapability, ARRAY_COUNT(invalidCapability))
            == EXPANSION_AUTOPLAY_STRATEGY_ERR_UNSUPPORTED_CAPABILITY,
        "unsupported capability bits must fail"
    );
    CHECK(
        ExpansionAutoplayStrategies_ValidateRegistry(
            duplicate, EXPANSION_AUTOPLAY_STRATEGY_CAPACITY + 1)
            == EXPANSION_AUTOPLAY_STRATEGY_ERR_CAPACITY,
        "registry capacity overflow must fail"
    );
    return 0;
}

#if FE8_EXPANSION_AUTOPLAY_STRATEGIES
static int TestCombatMovementPreparation(void)
{
    struct ExpansionAutoplayStrategyContext context = { NULL };

    ResetFixture();
    sUsePerUnitCombatMap = true;
    sEirikaCombatX = 8;
    sEirikaCombatY = 8;
    sSethCombatX = 4;
    sSethCombatY = 4;
    sCombatMoveX = 8;
    sCombatMoveY = 8;
    CHECK(
        ExpansionAutoplayStrategy_Aggressive(&context)
            && gAiDecision.actionPerformed
            && sLastMovementMapUnit == &sEirika,
        "Aggressive must prepare and consume the first unit movement map"
    );

    AiClearDecision();
    gActiveUnit = &sSeth;
    gActiveUnitId = 2;
    CHECK(
        !ExpansionAutoplayStrategy_Aggressive(&context)
            && !gAiDecision.actionPerformed
            && sLastMovementMapUnit == &sSeth
            && sMovementMapGenerationCount == 2,
        "the next unit must replace stale movement data and reject an unreachable move"
    );

    sCombatMoveX = 4;
    sCombatMoveY = 4;
    CHECK(
        ExpansionAutoplayStrategy_Aggressive(&context)
            && gAiDecision.actionPerformed
            && gAiDecision.xMove == 4
            && gAiDecision.yMove == 4,
        "the next unit must retain legal combat from its own movement map"
    );

    ResetFixture();
    sCombatMoveX = 8;
    sCombatMoveY = 8;
    sEirikaHasMagicRank = true;
    sMagicSealX = 8;
    sMagicSealY = 8;
    CHECK(
        !ExpansionAutoplayStrategy_Aggressive(&context)
            && !gAiDecision.actionPerformed
            && sMagicSealGenerationCount == 1
            && gBmMapMovement[8][8] > MAP_MOVEMENT_MAX,
        "magic-seal preparation must reject combat from a sealed destination"
    );
    return 0;
}

static int TestReferenceProfiles(void)
{
    enum ExpansionAutoplayStrategyResult result;
    struct ExpansionChapterAiGroup holdGroup = {
        0x79A64E39,
        NULL,
        0,
    };
    struct ExpansionChapterObjective holdObjective = {
        0xC06E2F8C,
        0,
        &holdGroup,
        0,
        0,
        0,
        0,
        3,
        EXPANSION_CHAPTER_OBJECTIVE_HOLD_UNTIL_TURN,
        0,
        0,
        3,
        3,
    };
    struct ExpansionAutoplayStrategyContext holdContext = {
        &holdObjective,
    };

    ResetFixture();
    sFlags[EVFLAG_GAMEOVER] = true;
    RefreshObjectiveTelemetry();
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(result == EXPANSION_AUTOPLAY_STRATEGY_OK, "chapter Aggressive must dispatch");
    CHECK(
        gAiDecision.actionId == AI_ACTION_COMBAT && sCombatCalls == 1 && sMoveCalls == 0,
        "Aggressive must select the immediate legal combat action"
    );

    ResetFixture();
    sFlags[EVFLAG_GAMEOVER] = true;
    sFlags[EVFLAG_HIDE_BLINKING_ICON] = true;
    RefreshObjectiveTelemetry();
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(result == EXPANSION_AUTOPLAY_STRATEGY_OK, "group Objective-first must dispatch");
    CHECK(
        gAiDecision.actionId == AI_ACTION_NONE
            && gAiDecision.xMove == 3
            && gAiDecision.yMove == 3
            && sCombatCalls == 0
            && sMoveCalls == 1,
        "Objective-first must accept a deterministic progressive approach"
    );

    ResetFixture();
    sFlags[EVFLAG_GAMEOVER] = true;
    sFlags[EVFLAG_HIDE_BLINKING_ICON] = true;
    sMoveDecisionX = 5;
    sMoveDecisionY = 5;
    sRangeData[sMoveDecisionY][sMoveDecisionX] = 10;
    RefreshObjectiveTelemetry();
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(
        result == EXPANSION_AUTOPLAY_STRATEGY_OK
            && !gAiDecision.actionPerformed
            && sCombatCalls == 0
            && sMoveCalls == 1,
        "no-progress objective movement must wait without unconstrained combat"
    );

    sFlags[EVFLAG_BATTLE_QUOTES] = true;
    RefreshObjectiveTelemetry();
    gAiDecision.actionPerformed = false;
    sCombatCalls = 0;
    sMoveCalls = 0;
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(result == EXPANSION_AUTOPLAY_STRATEGY_FALLBACK, "unit assignment must fallback");
    CHECK(
        !gAiDecision.actionPerformed && sCombatCalls == 0 && sMoveCalls == 1,
        "tentative callback decisions must clear before Unit.ai fallback"
    );

    gAiDecision.actionPerformed = false;
    sMoveCalls = 0;
    sTentativeReturnsSuccess = true;
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(
        result == EXPANSION_AUTOPLAY_STRATEGY_ERR_UNSUPPORTED_CAPABILITY
            && !gAiDecision.actionPerformed
            && sMoveCalls == 1,
        "combat-only strategies must reject and clear a produced move"
    );

    sMoveCalls = 0;
    sTentativeActionId = AI_ACTION_STAFF;
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(
        result == EXPANSION_AUTOPLAY_STRATEGY_ERR_UNSUPPORTED_CAPABILITY
            && !gAiDecision.actionPerformed
            && sMoveCalls == 1,
        "strategies must reject actions outside the public capability taxonomy"
    );

    sMoveCalls = 0;
    sTentativeActionId = AI_ACTION_COMBAT;
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(
        result == EXPANSION_AUTOPLAY_STRATEGY_OK
            && gAiDecision.actionPerformed
            && gAiDecision.actionId == AI_ACTION_COMBAT,
        "a produced action declared by the strategy must remain accepted"
    );

    ResetFixture();
    sFlags[EVFLAG_GAMEOVER] = true;
    RefreshObjectiveTelemetry();
    CHECK(
        ExpansionAutoplayStrategies_ActivateAssignment(
            EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID, EVFLAG_HIDE_BLINKING_ICON)
            == EXPANSION_AUTOPLAY_STRATEGY_OK
            && CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "typed event helper must activate only the declared assignment flag"
    );
    gAiDecision.actionPerformed = false;
    sCombatCalls = 0;
    sMoveCalls = 0;
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(
        result == EXPANSION_AUTOPLAY_STRATEGY_OK
            && gAiDecision.actionId == AI_ACTION_NONE
            && sMoveCalls == 1,
        "event activation must take effect through the existing flag at the next decision boundary"
    );
    CHECK(
        ExpansionAutoplayStrategies_ActivateAssignment(
            EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID, EVFLAG_BATTLE_QUOTES)
            == EXPANSION_AUTOPLAY_STRATEGY_ERR_INVALID_EVENT_ASSIGNMENT,
        "typed event helper must reject undeclared assignment flags"
    );
    CHECK(
        ExpansionAutoplayStrategies_DeactivateAssignment(
            EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID,
            EVFLAG_BATTLE_QUOTES)
            == EXPANSION_AUTOPLAY_STRATEGY_ERR_INVALID_EVENT_ASSIGNMENT,
        "typed deactivation must reject undeclared assignment flags"
    );
    CHECK(
        ExpansionAutoplayStrategies_DeactivateAssignment(
            EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID,
            EVFLAG_HIDE_BLINKING_ICON)
            == EXPANSION_AUTOPLAY_STRATEGY_OK
            && !CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "safe typed deactivation must clear only its declared activation flag"
    );

    ResetFixture();
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_HIDE_BLINKING_ICON;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    CHECK(
        CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "typed event production helper must activate its declared pair"
    );
    ExpansionAutoplayStrategies_EventDeactivate(NULL);
    CHECK(
        !CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "typed event production helper must deactivate its declared pair"
    );

    sBlueComputerPhase = true;
    sFlags[EVFLAG_HIDE_BLINKING_ICON] = false;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    CHECK(
        !CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "same-phase event activation must not change current units"
    );

    ResetFixture();
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_BATTLE_QUOTES;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    CHECK(
        !CheckFlag(EVFLAG_BATTLE_QUOTES),
        "event activation wrapper must reject undeclared strategy-flag pairs"
    );

    ResetFixture();
    sFlags[EVFLAG_GAMEOVER] = true;
    sBlueComputerPhase = true;
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_HIDE_BLINKING_ICON;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(
        result == EXPANSION_AUTOPLAY_STRATEGY_OK
            && gAiDecision.actionId == AI_ACTION_COMBAT
            && !CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "pending activation must leave the current phase on its existing strategy"
    );
    sBlueComputerPhase = false;
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    AiClearDecision();
    sCombatCalls = 0;
    sMoveCalls = 0;
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(
        result == EXPANSION_AUTOPLAY_STRATEGY_OK
            && gAiDecision.actionId == AI_ACTION_NONE
            && sMoveCalls == 1
            && CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "pending activation must apply at the next safe phase boundary"
    );
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    CHECK(
        sSetFlagCalls[EVFLAG_HIDE_BLINKING_ICON] == 1,
        "pending activation must apply exactly once"
    );

    ResetFixture();
    sFlags[EVFLAG_GAMEOVER] = true;
    sFlags[EVFLAG_HIDE_BLINKING_ICON] = true;
    sBlueComputerPhase = true;
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_HIDE_BLINKING_ICON;
    ExpansionAutoplayStrategies_EventDeactivate(NULL);
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(
        result == EXPANSION_AUTOPLAY_STRATEGY_OK
            && gAiDecision.actionId == AI_ACTION_NONE
            && CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "pending deactivation must leave the current phase on its selected strategy"
    );
    sBlueComputerPhase = false;
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    AiClearDecision();
    sCombatCalls = 0;
    sMoveCalls = 0;
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(
        result == EXPANSION_AUTOPLAY_STRATEGY_OK
            && gAiDecision.actionId == AI_ACTION_COMBAT
            && !CheckFlag(EVFLAG_HIDE_BLINKING_ICON)
            && sClearFlagCalls[EVFLAG_HIDE_BLINKING_ICON] == 1,
        "pending deactivation must clear once at the safe boundary"
    );
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    CHECK(
        sClearFlagCalls[EVFLAG_HIDE_BLINKING_ICON] == 1,
        "pending deactivation must apply exactly once"
    );

    ResetFixture();
    sBlueComputerPhase = true;
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_HIDE_BLINKING_ICON;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    ExpansionAutoplayStrategies_EventActivate(NULL);
    sBlueComputerPhase = false;
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    CHECK(
        CheckFlag(EVFLAG_HIDE_BLINKING_ICON)
            && sSetFlagCalls[EVFLAG_HIDE_BLINKING_ICON] == 1,
        "duplicate pending requests must coalesce into one application"
    );

    ResetFixture();
    sBlueComputerPhase = true;
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_HIDE_BLINKING_ICON;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    ExpansionAutoplayStrategies_EventDeactivate(NULL);
    ExpansionAutoplayStrategies_EventDeactivate(NULL);
    sBlueComputerPhase = false;
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    CHECK(
        !CheckFlag(EVFLAG_HIDE_BLINKING_ICON)
            && sSetFlagCalls[EVFLAG_HIDE_BLINKING_ICON] == 0
            && sClearFlagCalls[EVFLAG_HIDE_BLINKING_ICON] == 1,
        "latest deactivation must replace activation and duplicate clears must coalesce"
    );

    ResetFixture();
    sBlueComputerPhase = true;
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_HIDE_BLINKING_ICON;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    gEventSlots[EVT_SLOT_B] = AUTOPLAY_STRATEGY_TENTATIVE_FALLBACK_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_BATTLE_QUOTES;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    sBlueComputerPhase = false;
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    CHECK(
        !CheckFlag(EVFLAG_HIDE_BLINKING_ICON)
            && CheckFlag(EVFLAG_BATTLE_QUOTES),
        "a later valid pending request must replace the earlier pair"
    );

    ResetFixture();
    sBlueComputerPhase = true;
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_HIDE_BLINKING_ICON;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    CHECK(
        ExpansionAutoplayStrategies_ActivateAssignment(
            EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID,
            EVFLAG_BATTLE_QUOTES)
            == EXPANSION_AUTOPLAY_STRATEGY_ERR_INVALID_EVENT_ASSIGNMENT,
        "active-phase requests must reject invalid pairs before queueing"
    );
    gEventSlots[EVT_SLOT_C] = EVFLAG_BATTLE_QUOTES;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    sBlueComputerPhase = false;
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    CHECK(
        CheckFlag(EVFLAG_HIDE_BLINKING_ICON)
            && !CheckFlag(EVFLAG_BATTLE_QUOTES),
        "an invalid request must not replace a valid pending pair"
    );

    ResetFixture();
    sBlueComputerPhase = true;
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_HIDE_BLINKING_ICON;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    ExpansionAutoplayStrategies_ResetPendingActivation();
    sBlueComputerPhase = false;
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    CHECK(
        !CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "chapter and suspend-resume lifecycle reset must discard pending activation"
    );

    ResetFixture();
    sFlags[EVFLAG_HIDE_BLINKING_ICON] = true;
    sBlueComputerPhase = true;
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_HIDE_BLINKING_ICON;
    ExpansionAutoplayStrategies_EventDeactivate(NULL);
    ExpansionAutoplayStrategies_ResetPendingActivation();
    sBlueComputerPhase = false;
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    CHECK(
        CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "suspend-resume lifecycle reset must discard pending deactivation"
    );

    CHECK(
        ExpansionAutoplayStrategies_ValidateObjectiveSupport(
            EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID,
            EXPANSION_CHAPTER_OBJECTIVE_EVENT_FLAG)
            == EXPANSION_AUTOPLAY_STRATEGY_ERR_UNSUPPORTED_OBJECTIVE,
        "unsupported profile/objective pairs must fail before a decision"
    );
    CHECK(
        ExpansionAutoplayStrategies_ValidateObjectiveSupport(
            0xDEADBEEF, EXPANSION_CHAPTER_OBJECTIVE_REACH_AREA)
            == EXPANSION_AUTOPLAY_STRATEGY_ERR_UNKNOWN_ID,
        "unknown strategy IDs must fail explicitly"
    );

    ResetFixture();
    sFlags[EVFLAG_GAMEOVER] = true;
    sFlags[EVFLAG_HIDE_BLINKING_ICON] = true;
    sEirika.xPos = 2;
    sEirika.yPos = 2;
    sCombatMoveX = 3;
    sCombatMoveY = 3;
    sUsePerUnitCombatMap = true;
    sEirikaCombatX = 3;
    sEirikaCombatY = 3;
    RefreshObjectiveTelemetry();
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(
        result == EXPANSION_AUTOPLAY_STRATEGY_OK
            && gAiDecision.actionPerformed
            && gAiDecision.actionId == AI_ACTION_COMBAT
            && sCombatCalls == 1
            && sMoveCalls == 0
            && sLastMovementMapUnit == &sEirika,
        "pending reach combat must use the current unit prepared movement map"
    );

    ResetFixture();
    sFlags[EVFLAG_GAMEOVER] = true;
    sFlags[EVFLAG_HIDE_BLINKING_ICON] = true;
    sEirika.xPos = 2;
    sEirika.yPos = 2;
    sCombatMoveX = 3;
    sCombatMoveY = 3;
    sUsePerUnitCombatMap = true;
    sEirikaCombatX = 4;
    sEirikaCombatY = 4;
    RefreshObjectiveTelemetry();
    result = ExpansionAutoplayStrategies_TryDecide();
    CHECK(
        result == EXPANSION_AUTOPLAY_STRATEGY_OK
            && !gAiDecision.actionPerformed
            && sCombatCalls == 1
            && sMovementMapGenerationCount == 1,
        "pending reach combat must reject a rectangle tile unreachable by this unit"
    );

    ResetFixture();
    sEirika.xPos = 2;
    sEirika.yPos = 2;
    sCombatMoveX = 8;
    sCombatMoveY = 8;
    CHECK(
        ExpansionAutoplayStrategy_ObjectiveFirst(&holdContext)
            && gAiDecision.actionPerformed
            && gAiDecision.actionId == AI_ACTION_COMBAT,
        "completed hold must return to Aggressive instead of remaining constrained"
    );
    return 0;
}
#else
static int TestDisabledProfileNegative(void)
{
    ResetFixture();
    CHECK(
        ExpansionAutoplayStrategies_ValidateCurrentChapter()
            == EXPANSION_AUTOPLAY_STRATEGY_FALLBACK,
        "disabled default profile data must preserve the Unit.ai fallback"
    );
    CHECK(
        ExpansionAutoplayStrategies_TryDecide() == EXPANSION_AUTOPLAY_STRATEGY_FALLBACK,
        "disabled profiles must not synthesize an action"
    );
    sBlueComputerPhase = true;
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_HIDE_BLINKING_ICON;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    sBlueComputerPhase = false;
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    CHECK(
        !CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "disabled/default profile must not queue or apply strategy activation"
    );
    sFlags[EVFLAG_HIDE_BLINKING_ICON] = true;
    sBlueComputerPhase = true;
    ExpansionAutoplayStrategies_EventDeactivate(NULL);
    sBlueComputerPhase = false;
    ExpansionAutoplayStrategies_ApplyPendingActivation();
    CHECK(
        CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "disabled/default profile must not queue or apply strategy deactivation"
    );
    return 0;
}
#endif

int main(void)
{
    CHECK(TestRegistryFailures() == 0, "registry validation");
#if FE8_EXPANSION_AUTOPLAY_STRATEGIES
    CHECK(TestCombatMovementPreparation() == 0, "combat movement preparation");
    CHECK(TestReferenceProfiles() == 0, "reference profiles");
#else
    CHECK(TestDisabledProfileNegative() == 0, "disabled profile negative");
#endif
    puts("AUTOPLAY_STRATEGIES_HOST_TEST: PASS");
    return 0;
}
