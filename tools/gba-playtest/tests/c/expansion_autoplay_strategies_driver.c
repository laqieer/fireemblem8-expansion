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
#include "expansion_autoplay_strategies.h"
#include "eventinfo.h"
#include "event.h"

#if FE8_EXPANSION_AUTOPLAY_STRATEGIES
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

struct PlaySt gPlaySt;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct AiDecision gAiDecision;
u32 gEventSlots[EVENT_SLOT_COUNT];
struct Vec2 gBmMapSize;
u8 ** gBmMapRange;

static struct CharacterData sEirikaCharacter;
static struct Unit sEirika;
static u8 sRangeData[16][16];
static u8 * sRangeRows[16];
static bool sFlags[0x100];
static bool sBlueComputerPhase;
static int sCombatCalls;
static int sMoveCalls;
static int sCombatMoveX;
static int sCombatMoveY;
static int sMoveDecisionX;
static int sMoveDecisionY;

bool CheckFlag(int flag)
{
    return flag >= 0 && flag < (int)ARRAY_COUNT(sFlags) && sFlags[flag];
}

void SetFlag(int flag)
{
    if (flag >= 0 && flag < (int)ARRAY_COUNT(sFlags))
        sFlags[flag] = true;
}

struct Unit* GetUnitFromCharId(int character)
{
    if (character == CHARACTER_EIRIKA)
        return &sEirika;
    return NULL;
}

struct Unit* GetUnit(int unitId)
{
    if (unitId == gActiveUnitId)
        return &sEirika;
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
    AiSetDecision(sCombatMoveX, sCombatMoveY, AI_ACTION_COMBAT, 0x81, 0, 0, 0);
    return 1;
}

s8 AiIsUnitEnemy(struct Unit* unit)
{
    (void)unit;
    return true;
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
    return false;
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
        sFlags[index] = false;

    sEirikaCharacter.number = CHARACTER_EIRIKA;
    sEirika.pCharacterData = &sEirikaCharacter;
    sEirika.state = US_NONE;
    sEirika.xPos = 10;
    sEirika.yPos = 10;
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
    memset(gEventSlots, 0, sizeof(gEventSlots));
    gBmMapSize.x = 16;
    gBmMapSize.y = 16;
    for (index = 0; index < 16; index++)
    {
        sRangeRows[index] = sRangeData[index];
        memset(sRangeData[index], MAP_MOVEMENT_MAX, sizeof(sRangeData[index]));
    }
    gBmMapRange = sRangeRows;
    sRangeData[sEirika.yPos][sEirika.xPos] = 10;
    sRangeData[sMoveDecisionY][sMoveDecisionX] = 5;
    ExpansionChapterObjectives_ResetTelemetry();
    ExpansionChapterObjectives_OnBeginningEventsComplete();
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

    ResetFixture();
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_HIDE_BLINKING_ICON;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    CHECK(
        CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "typed event production helper must activate its declared pair"
    );

    sBlueComputerPhase = true;
    sFlags[EVFLAG_HIDE_BLINKING_ICON] = false;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    CHECK(
        !CheckFlag(EVFLAG_HIDE_BLINKING_ICON),
        "same-phase event activation must defer until the next safe boundary"
    );

    ResetFixture();
    gEventSlots[EVT_SLOT_B] = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    gEventSlots[EVT_SLOT_C] = EVFLAG_BATTLE_QUOTES;
    ExpansionAutoplayStrategies_EventActivate(NULL);
    CHECK(
        !CheckFlag(EVFLAG_BATTLE_QUOTES),
        "event activation wrapper must reject undeclared strategy-flag pairs"
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
    return 0;
}
#endif

int main(void)
{
    CHECK(TestRegistryFailures() == 0, "registry validation");
#if FE8_EXPANSION_AUTOPLAY_STRATEGIES
    CHECK(TestReferenceProfiles() == 0, "reference profiles");
#else
    CHECK(TestDisabledProfileNegative() == 0, "disabled profile negative");
#endif
    puts("AUTOPLAY_STRATEGIES_HOST_TEST: PASS");
    return 0;
}
