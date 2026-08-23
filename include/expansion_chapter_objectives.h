#ifndef GUARD_EXPANSION_CHAPTER_OBJECTIVES_H
#define GUARD_EXPANSION_CHAPTER_OBJECTIVES_H

#include "global.h"

enum ExpansionChapterObjectiveKind
{
    EXPANSION_CHAPTER_OBJECTIVE_PROTECT = 1,
    EXPANSION_CHAPTER_OBJECTIVE_REACH_AREA = 2,
    EXPANSION_CHAPTER_OBJECTIVE_DEFEAT_GROUP = 3,
    EXPANSION_CHAPTER_OBJECTIVE_EVENT_FLAG = 4,
    EXPANSION_CHAPTER_OBJECTIVE_HOLD_UNTIL_TURN = 5,
};

enum ExpansionChapterObjectiveState
{
    EXPANSION_CHAPTER_OBJECTIVE_INACTIVE = 0,
    EXPANSION_CHAPTER_OBJECTIVE_PENDING = 1,
    EXPANSION_CHAPTER_OBJECTIVE_SUCCESS = 2,
    EXPANSION_CHAPTER_OBJECTIVE_FAILURE = 3,
};

enum
{
    EXPANSION_CHAPTER_OBJECTIVE_BUNDLE_CAPACITY = 32,
    EXPANSION_CHAPTER_OBJECTIVE_PER_CHAPTER_CAPACITY = 8,
    EXPANSION_CHAPTER_AI_GROUP_PER_CHAPTER_CAPACITY = 8,
    EXPANSION_CHAPTER_AI_GROUP_MEMBER_CAPACITY = 16,
    EXPANSION_CHAPTER_OBJECTIVE_FLAG_NONE = 0xFFFF,
    EXPANSION_CHAPTER_OBJECTIVE_CHAPTER_NONE = 0xFFFF,
};

struct ExpansionChapterAiGroup
{
    u32 id;
    const u8* members;
    u8 memberCount;
};

struct ExpansionChapterObjective
{
    u32 id;
    u32 completionObjectiveId;
    const struct ExpansionChapterAiGroup* group;
    u16 activationFlag;
    u16 deactivationFlag;
    u16 eventFlag;
    u16 untilTurn;
    u8 kind;
    u8 protectedCharacter;
    u8 xMin;
    u8 yMin;
    u8 xMax;
    u8 yMax;
};

struct ExpansionChapterObjectiveBundle
{
    u16 chapterId;
    u8 objectiveCount;
    u8 groupCount;
    const struct ExpansionChapterObjective* objectives;
    const struct ExpansionChapterAiGroup* groups;
};

struct ExpansionChapterObjectiveTelemetry
{
    u32 objectiveId;
    u32 state;
    u32 progress;
    u32 activeCount;
};

extern const struct ExpansionChapterObjectiveBundle gExpansionChapterObjectiveBundles[];
extern struct ExpansionChapterObjectiveTelemetry gExpansionChapterObjectiveTelemetry;

void ExpansionChapterObjectives_ResetTelemetry(void);
void ExpansionChapterObjectives_RefreshTelemetry(void);
enum ExpansionChapterObjectiveState ExpansionChapterObjectives_GetStatus(u32 objectiveId, u32* progressOut);
const struct ExpansionChapterObjective* ExpansionChapterObjectives_GetActiveObjective(void);
const struct ExpansionChapterAiGroup* ExpansionChapterObjectives_FindGroup(u32 groupId);
bool ExpansionChapterObjectives_GroupContains(u32 groupId, u8 character);

#endif /* GUARD_EXPANSION_CHAPTER_OBJECTIVES_H */
