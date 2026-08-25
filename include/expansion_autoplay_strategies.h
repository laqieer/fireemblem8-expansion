#ifndef GUARD_EXPANSION_AUTOPLAY_STRATEGIES_H
#define GUARD_EXPANSION_AUTOPLAY_STRATEGIES_H

#include "global.h"

#include "bmunit.h"
#include "expansion_chapter_objectives.h"

enum ExpansionAutoplayStrategyObjectiveCapability
{
    EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_PROTECT = (1 << 0),
    EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_REACH_AREA = (1 << 1),
    EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_DEFEAT_GROUP = (1 << 2),
    EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_EVENT_FLAG = (1 << 3),
    EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_HOLD_UNTIL_TURN = (1 << 4),
    EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_ALL = (1 << 5) - 1,
};

enum ExpansionAutoplayStrategyActionCapability
{
    EXPANSION_AUTOPLAY_STRATEGY_ACTION_COMBAT = (1 << 0),
    EXPANSION_AUTOPLAY_STRATEGY_ACTION_OBJECTIVE_MOVE = (1 << 1),
    EXPANSION_AUTOPLAY_STRATEGY_ACTION_ALL = (1 << 2) - 1,
};

enum ExpansionAutoplayStrategyFlags
{
    EXPANSION_AUTOPLAY_STRATEGY_FLAG_REFERENCE_PROFILE = (1 << 0),
    EXPANSION_AUTOPLAY_STRATEGY_FLAG_ALL = EXPANSION_AUTOPLAY_STRATEGY_FLAG_REFERENCE_PROFILE,
};

enum ExpansionAutoplayStrategyResult
{
    EXPANSION_AUTOPLAY_STRATEGY_OK = 0,
    EXPANSION_AUTOPLAY_STRATEGY_FALLBACK = 1,
    EXPANSION_AUTOPLAY_STRATEGY_ERR_CAPACITY = 2,
    EXPANSION_AUTOPLAY_STRATEGY_ERR_DUPLICATE_ID = 3,
    EXPANSION_AUTOPLAY_STRATEGY_ERR_UNKNOWN_ID = 4,
    EXPANSION_AUTOPLAY_STRATEGY_ERR_MISSING_CALLBACK = 5,
    EXPANSION_AUTOPLAY_STRATEGY_ERR_UNSUPPORTED_CAPABILITY = 6,
    EXPANSION_AUTOPLAY_STRATEGY_ERR_UNSUPPORTED_OBJECTIVE = 7,
    EXPANSION_AUTOPLAY_STRATEGY_ERR_PROFILE_DISABLED = 8,
    EXPANSION_AUTOPLAY_STRATEGY_ERR_INVALID_EVENT_ASSIGNMENT = 9,
    EXPANSION_AUTOPLAY_STRATEGY_ERR_PHASE_ACTIVE = 10,
};

enum
{
    EXPANSION_AUTOPLAY_STRATEGY_CAPACITY = 8,
    EXPANSION_AUTOPLAY_STRATEGY_ASSIGNMENT_CAPACITY = 8,
    EXPANSION_AUTOPLAY_STRATEGY_FLAG_NONE = 0xFFFF,
    EXPANSION_AUTOPLAY_STRATEGY_CHAPTER_NONE = 0xFFFF,
    EXPANSION_AUTOPLAY_STRATEGY_AGGRESSIVE_ID = 0x8A98AADD,
    EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID = 0x7F2C07B5,
};

#ifndef FE8_AUTOPLAY_STRATEGY_RUNTIME_TEST
#define FE8_AUTOPLAY_STRATEGY_RUNTIME_TEST 0
#endif

#if (FE8_AUTOPLAY_STRATEGY_RUNTIME_TEST != 0) \
    && (FE8_AUTOPLAY_STRATEGY_RUNTIME_TEST != 1)
#error "FE8_AUTOPLAY_STRATEGY_RUNTIME_TEST must be 0 or 1"
#endif

struct ExpansionAutoplayStrategyContext
{
    const struct ExpansionChapterObjective* objective;
};

typedef bool (*ExpansionAutoplayStrategyCallback)(
    const struct ExpansionAutoplayStrategyContext* context);

struct ExpansionAutoplayStrategy
{
    u32 id;
    u32 objectiveCapabilities;
    u32 actionCapabilities;
    ExpansionAutoplayStrategyCallback callback;
    u8 flags;
};

struct ExpansionAutoplayStrategyGroupAssignment
{
    u32 groupId;
    u32 strategyId;
    u16 activationFlag;
};

struct ExpansionAutoplayStrategyUnitAssignment
{
    u8 character;
    u32 strategyId;
    u16 activationFlag;
};

struct ExpansionAutoplayStrategyBundle
{
    u16 chapterId;
    u8 groupAssignmentCount;
    u8 unitAssignmentCount;
    u32 chapterStrategyId;
    u16 chapterActivationFlag;
    const struct ExpansionAutoplayStrategyGroupAssignment* groupAssignments;
    const struct ExpansionAutoplayStrategyUnitAssignment* unitAssignments;
};

extern const struct ExpansionAutoplayStrategy gExpansionAutoplayStrategies[];
extern const struct ExpansionAutoplayStrategyBundle gExpansionAutoplayStrategyBundles[];

bool ExpansionAutoplayStrategies_HasStrategies(void);
enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_ValidateRegistry(
    const struct ExpansionAutoplayStrategy* registry,
    u8 count);
enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_ValidateObjectiveSupport(
    u32 strategyId,
    enum ExpansionChapterObjectiveKind kind);
enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_ValidateCurrentChapter(void);
enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_TryDecide(void);
enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_ActivateAssignment(
    u32 strategyId,
    u16 activationFlag);

#if FE8_AUTOPLAY_STRATEGY_RUNTIME_TEST
struct ExpansionAutoplayStrategyRuntimeProbe
{
    u32 magic;
    u32 objectiveFirstCount;
    u32 objectiveFirstObjectiveId;
    u32 objectiveFirstActionId;
    u32 objectiveFirstX;
    u32 objectiveFirstY;
    u32 aggressiveCount;
    u32 aggressiveActionId;
};

extern struct ExpansionAutoplayStrategyRuntimeProbe gExpansionAutoplayStrategyRuntimeProbe;
#endif

#endif /* GUARD_EXPANSION_AUTOPLAY_STRATEGIES_H */
