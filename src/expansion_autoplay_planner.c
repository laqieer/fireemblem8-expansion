#include "global.h"

#include <stddef.h>

#include "action_semantics.h"
#include "bm.h"
#include "bmcontainer.h"
#include "bmitem.h"
#include "bmitemuse.h"
#include "bmmap.h"
#include "bmmind.h"
#include "bmphase.h"
#include "bmtarget.h"
#include "bmtrick.h"
#include "bmunit.h"
#include "cp_common.h"
#include "eventinfo.h"
#include "rng.h"

#include "constants/classes.h"
#include "constants/items.h"
#include "constants/terrains.h"

#include "expansion_autoplay.h"
#include "expansion_autoplay_internal.h"
#include "expansion_autoplay_planner.h"
#include "expansion_autoplay_strategies.h"
#include "expansion_chapter_objectives.h"
#include "expansion_config.h"

typedef char ExpansionAutoplayPlannerObservationSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerObservationV2) == 1024 ? 1 : -1];
typedef char ExpansionAutoplayPlannerCommandSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerCommandV2) == 64 ? 1 : -1];
typedef char ExpansionAutoplayPlannerCheckpointSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerCampaignCheckpointV2) == 52 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPointerFreeActionCheck[
    sizeof(struct ExpansionAutoplayPlannerActionV2) == 40 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPointerFreeSemanticCheck[
    sizeof(struct ExpansionAutoplayPlannerSemanticFieldV2) == 8 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPointerFreeUnitCheck[
    sizeof(struct ExpansionAutoplayPlannerUnitV2) == 40 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPointerFreeValueCheck[
    sizeof(struct ExpansionAutoplayPlannerValueRecordV2) == 8 ? 1 : -1];
typedef char ExpansionAutoplayPlannerRecordStartSizeCheck[
    sizeof(union ExpansionAutoplayPlannerRecordStartV2) == 4 ? 1 : -1];
typedef char ExpansionAutoplayPlannerRecordCountSizeCheck[
    sizeof(union ExpansionAutoplayPlannerRecordCountV2) == 4 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPayloadSizeCheck[
    sizeof(union ExpansionAutoplayPlannerPayloadV2) == 924 ? 1 : -1];
typedef char ExpansionAutoplayPlannerCampaignSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerCampaignV2) == 812 ? 1 : -1];
typedef char ExpansionAutoplayPlannerObjectiveSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerObjectiveV2) == 32 ? 1 : -1];
typedef char ExpansionAutoplayPlannerGroupSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerGroupV2) == 24 ? 1 : -1];
typedef char ExpansionAutoplayPlannerStrategySizeCheck[
    sizeof(struct ExpansionAutoplayPlannerStrategyV2) == 16 ? 1 : -1];
typedef char ExpansionAutoplayPlannerAssignmentSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerAssignmentV2) == 12 ? 1 : -1];
typedef char ExpansionAutoplayPlannerSummarySizeCheck[
    sizeof(struct ExpansionAutoplayPlannerSummaryV2) == 876 ? 1 : -1];
typedef char ExpansionAutoplayPlannerRecordStartOffsetCheck[
    offsetof(struct ExpansionAutoplayPlannerObservationV2, start) == 36 ? 1 : -1];
typedef char ExpansionAutoplayPlannerRecordCountOffsetCheck[
    offsetof(struct ExpansionAutoplayPlannerObservationV2, count) == 40 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPayloadOffsetCheck[
    offsetof(struct ExpansionAutoplayPlannerObservationV2, payload) == 100 ? 1 : -1];
typedef char ExpansionAutoplayPlannerChapterModeOffsetCheck[
    offsetof(struct ExpansionAutoplayPlannerCampaignCheckpointV2, chapterMode) == 20 ? 1 : -1];
typedef char ExpansionAutoplayPlannerCommandPayloadOffsetCheck[
    offsetof(struct ExpansionAutoplayPlannerCommandV2, payload) == 32 ? 1 : -1];
typedef char ExpansionAutoplayPlannerCommandResultOffsetCheck[
    offsetof(struct ExpansionAutoplayPlannerCommandV2, result) == 56 ? 1 : -1];

#if FE8_EXPANSION_AUTOPLAY_PLANNER && FE8_EXPANSION_DEBUG

struct ExpansionAutoplayPlannerObservationV2 EWRAM_DATA
    gExpansionAutoplayPlannerObservation = { 0 };
volatile struct ExpansionAutoplayPlannerCommandV2 EWRAM_DATA
    gExpansionAutoplayPlannerCommand = { 0 };
struct ExpansionAutoplayPlannerCampaignCheckpointV2 EWRAM_DATA
    gExpansionAutoplayPlannerCampaignCheckpoint = { 0 };

static bool sPlannerActive;
static u32 sPlannerRunId;
static u32 sPlannerNextObservationId;
static u32 sPlannerTraceDigest;
static u32 sPlannerCommitCount;
static u32 sPlannerCandidateState;
static u32 sPlannerCandidateDigest;

enum
{
    PLANNER_CANDIDATE_COUNT_MASK = 0x3FF,
    PLANNER_WAIT_FRAMES_SHIFT = 16,
    PLANNER_FLAG_BYTE_CAPACITY = 0x100,
};

#define PLANNER_PUBLISH_BARRIER() __asm__ volatile("" ::: "memory")

struct PlannerEnumeration
{
    ExpansionAutoplayPlannerActionVisitor visitor;
    void* context;
    u32 count;
    bool stopped;
};

struct PlannerCandidateLookup
{
    u32 requested;
    struct AiDecision* output;
    bool found;
};

struct PlannerPageCollector
{
    u32 start;
    u32 count;
};

s8 CanUnitCrossTerrain(struct Unit* unit, int terrain);

static u32 MixDigest(u32 digest, u32 value)
{
    return (digest ^ value) * 16777619u;
}

static u32 HashText(const char* text)
{
    u32 digest = 2166136261u;

    while (*text != '\0')
        digest = MixDigest(digest, (u8)*text++);
    return digest;
}

static u32 CandidateCount(void)
{
    return sPlannerCandidateState & PLANNER_CANDIDATE_COUNT_MASK;
}

static u32 WaitFrames(void)
{
    return sPlannerCandidateState >> PLANNER_WAIT_FRAMES_SHIFT;
}

static void SetCandidateState(u32 count, u32 waitFrames)
{
    sPlannerCandidateState = count | (waitFrames << PLANNER_WAIT_FRAMES_SHIFT);
}

static void ClearCommand(void)
{
    gExpansionAutoplayPlannerCommand.kind = EXPANSION_AUTOPLAY_PLANNER_COMMAND_NONE;
}

static u32 ActualRomIdentity(void)
{
    u32 digest = HashText(FE8_EXPANSION_BUILD_COMMIT);
#if FE8_AUTOPLAY_PLANNER_RUNTIME_TEST
    return MixDigest(digest, 0x54455354u);
#else
    const volatile u8* header = (const volatile u8*)0x080000A0;
    int index;
    for (index = 0; index < 16; index++)
        digest = MixDigest(digest, header[index]);
    return digest;
#endif
}

static u32 ActualConfigIdentity(void)
{
    return HashText(FE8_EXPANSION_CONFIG_FINGERPRINT);
}

static u32 ActualScenarioIdentity(void)
{
    u32 digest = MixDigest(2166136261u, FE8_EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID);

    digest = MixDigest(digest, (u8)gPlaySt.chapterIndex);
    digest = MixDigest(digest, (u16)gBmMapSize.x | ((u32)(u16)gBmMapSize.y << 16));
    return digest;
}

static u32 ActualSeedIdentity(void)
{
    u16 seeds[3];
    u32 digest = 2166136261u;

    StoreRNState(seeds);
    digest = MixDigest(digest, seeds[0]);
    digest = MixDigest(digest, seeds[1]);
    digest = MixDigest(digest, seeds[2]);
    return MixDigest(digest, GetLCGRNValue());
}

static u32 MixCandidateItemState(u32 digest, const struct AiDecision* decision)
{
    if (decision->itemSlot < UNIT_ITEM_COUNT && gActiveUnit != NULL)
    {
        digest = MixDigest(digest, 0xA17E0000u | decision->itemSlot);
        digest = MixDigest(digest, gActiveUnit->items[decision->itemSlot]);
    }
    if (decision->actionId == AI_ACTION_STAFF && decision->unk04 < UNIT_ITEM_COUNT)
    {
        struct Unit* target = GetUnit(decision->targetId);

        digest = MixDigest(
            digest,
            0x7A260000u | ((u32)decision->targetId << 8) | decision->unk04);
        digest = MixDigest(
            digest,
            target == NULL ? 0 : target->items[decision->unk04]);
    }
    return digest;
}

static u32 MakeTokenWord(const struct AiDecision* decision, u32 observationId, u32 ordinal,
                         u32 domain)
{
    u32 digest = MixDigest(2166136261u, domain);

    digest = MixDigest(digest, sPlannerRunId);
    digest = MixDigest(digest, observationId);
    digest = MixDigest(digest, ordinal);
    digest = MixDigest(digest, decision->unitId | ((u32)decision->actionId << 8)
                                   | ((u32)decision->targetId << 16)
                                   | ((u32)decision->itemSlot << 24));
    digest = MixDigest(digest,
                       (u32)(u16)decision->xMove | ((u32)(u16)decision->yMove << 16));
    digest = MixDigest(
        digest,
        decision->xTarget
            | ((u32)decision->yTarget << 8)
            | ((u32)decision->unk04 << 16));
    return MixCandidateItemState(digest, decision);
}

static void MakeToken(const struct AiDecision* decision, u32 observationId, u32 ordinal,
                      u32* token)
{
    token[0] = MakeTokenWord(decision, observationId, ordinal, 0x243F6A88u);
    token[1] = MakeTokenWord(decision, observationId, ordinal, 0x85A308D3u);
    token[2] = MakeTokenWord(decision, observationId, ordinal, 0x13198A2Eu);
    token[3] = MakeTokenWord(decision, observationId, ordinal, 0x03707344u);
}

static enum ExpansionAutoplayPlannerActionKind ActionKindFromAiAction(u8 actionId)
{
    switch (actionId)
    {
    case AI_ACTION_NONE:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_MOVE_WAIT;

    case AI_ACTION_COMBAT:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_COMBAT;

    case AI_ACTION_STAFF:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_STAFF;

    case AI_ACTION_USEITEM:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_USE_ITEM;

    case AI_ACTION_PICK:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_PICK;

    case AI_ACTION_SUMMON:
    case AI_ACTION_DKSUMMON:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_SUMMON;

    default:
        return 0;
    }
}

static bool IsCommandHeaderValid(void)
{
    return gExpansionAutoplayPlannerCommand.magic == EXPANSION_AUTOPLAY_PLANNER_MAGIC
        && gExpansionAutoplayPlannerCommand.version
            == EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION
        && gExpansionAutoplayPlannerCommand.byteSize
            == sizeof(struct ExpansionAutoplayPlannerCommandV2);
}

static void Reject(enum ExpansionAutoplayPlannerRejection rejection)
{
    gExpansionAutoplayPlannerObservation.rejection = rejection;
    gExpansionAutoplayPlannerCommand.result = 0;
    gExpansionAutoplayPlannerCommand.rejection = rejection;
    ClearCommand();
}

static void ClearObservation(void)
{
    u8* bytes = (u8*)&gExpansionAutoplayPlannerObservation;
    int index;
    for (index = 0; index < (int)sizeof(gExpansionAutoplayPlannerObservation); index++)
        bytes[index] = 0;
}

static void ClearCheckpoint(void)
{
    u8* bytes = (u8*)&gExpansionAutoplayPlannerCampaignCheckpoint;
    int index;

    gExpansionAutoplayPlannerCampaignCheckpoint.magic = 0;
    PLANNER_PUBLISH_BARRIER();
    for (index = sizeof(gExpansionAutoplayPlannerCampaignCheckpoint.magic);
         index < (int)sizeof(gExpansionAutoplayPlannerCampaignCheckpoint);
         index++)
        bytes[index] = 0;
    PLANNER_PUBLISH_BARRIER();
}

static void ClearFullCommand(void)
{
    u8* bytes = (u8*)&gExpansionAutoplayPlannerCommand;
    int index;
    for (index = 0; index < (int)sizeof(gExpansionAutoplayPlannerCommand); index++)
        bytes[index] = 0;
}

static void EndPlannerRun(enum ExpansionAutoplayPlannerState state)
{
    ClearCheckpoint();
    gExpansionAutoplayPlannerObservation.state = state;
    sPlannerActive = false;
    ExpansionAutoplay_RequestPlayerControlRestore();
}

static bool IsMapReady(void)
{
    return gBmMapSize.x > 0
        && gBmMapSize.y > 0
        && gBmMapSize.x <= 64
        && gBmMapSize.y <= 64
        && gBmMapSize.x * gBmMapSize.y
            <= EXPANSION_AUTOPLAY_PLANNER_MAP_CELL_CAPACITY
        && gBmMapMovement != NULL
        && gBmMapUnit != NULL
        && gBmMapTerrain != NULL;
}

static bool IsPositionVisible(int x, int y)
{
    return gPlaySt.chapterVisionRange == 0
        || gBmMapFog == NULL
        || gBmMapFog[y][x] != 0;
}

static bool IsReachableDestination(int x, int y)
{
    if (!IsMapReady())
        return false;
    if (gBmMapMovement[y][x] > MAP_MOVEMENT_MAX)
        return false;
    if (gBmMapUnit[y][x] != 0 && gBmMapUnit[y][x] != gActiveUnitId)
        return false;
    return true;
}

static enum ExpansionAutoplayPlannerAvailability GetUnitAvailability(const struct Unit* unit)
{
    if (!UNIT_IS_VALID(unit))
        return EXPANSION_AUTOPLAY_PLANNER_NOT_APPLICABLE;
    if (unit->state & US_UNAVAILABLE)
        return EXPANSION_AUTOPLAY_PLANNER_UNAVAILABLE;
    if (unit->state & (US_HIDDEN | US_RESCUED))
        return EXPANSION_AUTOPLAY_PLANNER_NOT_VISIBLE;
    if (unit->xPos < 0 || unit->xPos >= gBmMapSize.x
        || unit->yPos < 0 || unit->yPos >= gBmMapSize.y)
        return EXPANSION_AUTOPLAY_PLANNER_OUT_OF_RANGE;
    if (!AreUnitsAllied(gActiveUnitId, unit->index)
        && !IsPositionVisible(unit->xPos, unit->yPos))
        return EXPANSION_AUTOPLAY_PLANNER_NOT_VISIBLE;
    return EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
}

static bool IsVisibleValidUnit(const struct Unit* unit)
{
    return GetUnitAvailability(unit)
        == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
}

static bool IsCanonicalUnitSlot(int unitId)
{
    return (unitId >= 1 && unitId <= 0x3E)
        || (unitId >= 0x41 && unitId <= 0x54)
        || (unitId >= 0x81 && unitId <= 0xB2);
}

static int RectDistance(int xA, int yA, int xB, int yB)
{
    return ABS(xA - xB) + ABS(yA - yB);
}

static void MakeDecision(struct AiDecision* decision, int xMove, int yMove, u8 actionId,
                         u8 targetId, u8 itemSlot, u8 xTarget, u8 yTarget)
{
    decision->actionId = actionId;
    decision->unitId = gActiveUnitId;
    decision->xMove = xMove;
    decision->yMove = yMove;
    decision->unk04 = 0xFF;
    decision->unk05 = 0;
    decision->targetId = targetId;
    decision->itemSlot = itemSlot;
    decision->xTarget = xTarget;
    decision->yTarget = yTarget;
    decision->actionPerformed = true;
}

static enum ExpansionAutoplayPlannerEnumerationResult EmitDecision(
    struct PlannerEnumeration* enumeration, const struct AiDecision* decision)
{
    if (enumeration->count >= EXPANSION_AUTOPLAY_PLANNER_TOTAL_ACTION_CAPACITY)
        return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_CAPACITY;
    if (enumeration->visitor != NULL
        && !enumeration->visitor(enumeration->count, decision, enumeration->context))
    {
        enumeration->stopped = true;
    }
    enumeration->count++;
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static bool IsCombatTargetLegal(const struct Unit* target, int xMove, int yMove, int item)
{
    int distance;
    if (!IsVisibleValidUnit(target)
        || AreUnitsAllied(gActiveUnitId, target->index))
        return false;

    distance = RectDistance(xMove, yMove, target->xPos, target->yPos);
    return distance >= GetItemMinRange(item)
        && distance <= GetItemMaxRange(item);
}

static bool __attribute__((noinline)) IsStaffTargetLegal(int item, const struct Unit* target,
                                                         int xMove, int yMove)
{
    return IsVisibleValidUnit(target)
        && IsUnitInStaffTargetListAt(gActiveUnit, (struct Unit*)target, item, xMove, yMove);
}

static bool IsSelfUseItemLegal(int item)
{
    switch (GetItemIndex(item))
    {
    case ITEM_VULNERARY:
    case ITEM_ELIXIR:
    case ITEM_VULNERARY_2:
        return CanUnitUseHealItem(gActiveUnit);

    case ITEM_PUREWATER:
        return CanUnitUsePureWaterItem(gActiveUnit);

    case ITEM_TORCH:
        return CanUnitUseTorchItem(gActiveUnit);

    case ITEM_ANTITOXIN:
        return CanUnitUseAntitoxinItem(gActiveUnit);

    case ITEM_BOOSTER_HP:
    case ITEM_BOOSTER_POW:
    case ITEM_BOOSTER_SKL:
    case ITEM_BOOSTER_SPD:
    case ITEM_BOOSTER_LCK:
    case ITEM_BOOSTER_DEF:
    case ITEM_BOOSTER_RES:
    case ITEM_BOOSTER_MOV:
    case ITEM_BOOSTER_CON:
        return CanUnitUseStatGainItem(gActiveUnit, item);

    case ITEM_HEROCREST:
    case ITEM_KNIGHTCREST:
    case ITEM_ORIONSBOLT:
    case ITEM_ELYSIANWHIP:
    case ITEM_GUIDINGRING:
    case ITEM_MASTERSEAL:
    case ITEM_HEAVENSEAL:
    case ITEM_OCEANSEAL:
    case ITEM_LUNARBRACE:
    case ITEM_SOLARBRACE:
    case ITEM_UNK_C1:
        return CanUnitUsePromotionItem(gActiveUnit, item);

    case ITEM_METISSTOME:
        return !(gActiveUnit->state & US_GROWTH_BOOST);

    case ITEM_JUNAFRUIT:
        return CanUnitUseFruitItem(gActiveUnit);

    default:
        return false;
    }
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateWait(
    struct PlannerEnumeration* enumeration, int xMove, int yMove)
{
    struct AiDecision decision;

    MakeDecision(&decision, xMove, yMove, AI_ACTION_NONE, 0, 0xFF, 0, 0);
    return EmitDecision(enumeration, &decision);
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateCombat(
    struct PlannerEnumeration* enumeration, int xMove, int yMove)
{
    int itemSlot;
    int targetId;
    for (itemSlot = 0; itemSlot < UNIT_ITEM_COUNT; itemSlot++)
    {
        int item = gActiveUnit->items[itemSlot];

        if (item == 0)
            break;
        if (!CanUnitUseWeapon(gActiveUnit, item))
            continue;
        if ((GetItemAttributes(item) & IA_MAGIC)
            && IsPositionMagicSealed(xMove, yMove))
            continue;

        for (targetId = 1; targetId < 0xC0; targetId++)
        {
            struct Unit* target = GetUnit(targetId);
            struct AiDecision decision;
            enum ExpansionAutoplayPlannerEnumerationResult result;

            if (!IsCanonicalUnitSlot(targetId)
                || !IsCombatTargetLegal(target, xMove, yMove, item))
                continue;
            MakeDecision(&decision, xMove, yMove, AI_ACTION_COMBAT, target->index, itemSlot, 0,
                         0);
            result = EmitDecision(enumeration, &decision);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
        }
        for (targetId = 0; targetId < TRAP_MAX_COUNT; targetId++)
        {
            struct Trap* trap = GetTrap(targetId);
            struct AiDecision decision;
            enum ExpansionAutoplayPlannerEnumerationResult result;

            if (trap->type == TRAP_NONE)
                break;
            if (!IsSnagAttackTargetAt(item, xMove, yMove, trap->xPos, trap->yPos))
                continue;
            MakeDecision(&decision, xMove, yMove, AI_ACTION_COMBAT, 0, itemSlot, trap->xPos,
                         trap->yPos);
            result = EmitDecision(enumeration, &decision);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
        }
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateWarpDestinations(
    struct PlannerEnumeration* enumeration, int xMove, int yMove, int itemSlot,
    struct Unit* target)
{
    int yTarget;
    int xTarget;
    for (yTarget = 0; yTarget < gBmMapSize.y; yTarget++)
    {
        for (xTarget = 0; xTarget < gBmMapSize.x; xTarget++)
        {
            struct AiDecision decision;
            enum ExpansionAutoplayPlannerEnumerationResult result;

            if (!ActionSemantics_IsWarpDestination(gActiveUnit, target, xMove, yMove, xTarget,
                                                   yTarget))
                continue;
            MakeDecision(&decision, xMove, yMove, AI_ACTION_STAFF, target->index, itemSlot,
                         xTarget, yTarget);
            result = EmitDecision(enumeration, &decision);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
        }
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateUnlockTargets(
    struct PlannerEnumeration* enumeration, int xMove, int yMove, int itemSlot)
{
    int yTarget;
    int xTarget;
    for (yTarget = 0; yTarget < gBmMapSize.y; yTarget++)
    {
        for (xTarget = 0; xTarget < gBmMapSize.x; xTarget++)
        {
            struct AiDecision decision;
            enum ExpansionAutoplayPlannerEnumerationResult result;
            if (!ActionSemantics_IsUnlockStaffTarget(gActiveUnit, xMove, yMove, xTarget, yTarget))
                continue;
            MakeDecision(&decision, xMove, yMove, AI_ACTION_STAFF, 0, itemSlot, xTarget, yTarget);
            result = EmitDecision(enumeration, &decision);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
        }
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateTorchTargets(
    struct PlannerEnumeration* enumeration, int xMove, int yMove, int itemSlot)
{
    int reach = GetUnitItemUseReachBits(gActiveUnit, itemSlot);
    int yTarget;
    int xTarget;
    if (gPlaySt.chapterVisionRange == 0)
        return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
    for (yTarget = 0; yTarget < gBmMapSize.y; yTarget++)
    {
        for (xTarget = 0; xTarget < gBmMapSize.x; xTarget++)
        {
            struct AiDecision decision;
            enum ExpansionAutoplayPlannerEnumerationResult result;

            if (!ActionSemantics_IsStandingReachPosition(gActiveUnit, xMove, yMove, reach,
                                                         xTarget, yTarget))
                continue;
            MakeDecision(&decision, xMove, yMove, AI_ACTION_STAFF, 0, itemSlot, xTarget, yTarget);
            result = EmitDecision(enumeration, &decision);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
        }
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateHammerneSlots(
    struct PlannerEnumeration* enumeration, int xMove, int yMove, int itemSlot,
    struct Unit* target)
{
    int targetSlot;
    for (targetSlot = 0; targetSlot < UNIT_ITEM_COUNT; targetSlot++)
    {
        struct AiDecision decision;
        enum ExpansionAutoplayPlannerEnumerationResult result;

        if (!IsItemHammernable(target->items[targetSlot]))
            continue;
        MakeDecision(&decision, xMove, yMove, AI_ACTION_STAFF, target->index, itemSlot, 0, 0);
        decision.unk04 = targetSlot;
        result = EmitDecision(enumeration, &decision);
        if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
            || enumeration->stopped)
            return result;
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateStaff(
    struct PlannerEnumeration* enumeration, int xMove, int yMove)
{
    int itemSlot;
    for (itemSlot = 0; itemSlot < UNIT_ITEM_COUNT; itemSlot++)
    {
        int item = gActiveUnit->items[itemSlot];
        int itemId;
        int targetId;

        if (item == 0)
            break;
        if (!CanUnitUseStaff(gActiveUnit, item)
            || IsPositionMagicSealed(xMove, yMove))
            continue;
        itemId = GetItemIndex(item);
        if (itemId == ITEM_STAFF_TORCH)
        {
            enum ExpansionAutoplayPlannerEnumerationResult result =
                EnumerateTorchTargets(enumeration, xMove, yMove, itemSlot);

            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
            continue;
        }
        if (itemId == ITEM_STAFF_FORTIFY
            || itemId == ITEM_STAFF_LATONA)
        {
            struct AiDecision decision;
            enum ExpansionAutoplayPlannerEnumerationResult result;

            if (itemId == ITEM_STAFF_FORTIFY
                ? !HasRangedHealTargetAt(gActiveUnit, xMove, yMove)
                : !HasLatonaTarget(gActiveUnit))
                continue;
            MakeDecision(&decision, xMove, yMove, AI_ACTION_STAFF, 0, itemSlot, 0, 0);
            result = EmitDecision(enumeration, &decision);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
            continue;
        }
        if (itemId == ITEM_STAFF_UNLOCK)
        {
            enum ExpansionAutoplayPlannerEnumerationResult result =
                EnumerateUnlockTargets(enumeration, xMove, yMove, itemSlot);

            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
            continue;
        }

        for (targetId = 1; targetId < 0xC0; targetId++)
        {
            struct Unit* target = GetUnit(targetId);
            struct AiDecision decision;
            enum ExpansionAutoplayPlannerEnumerationResult result;

            if (!IsCanonicalUnitSlot(targetId)
                || !IsStaffTargetLegal(item, target, xMove, yMove))
                continue;
            if (itemId == ITEM_STAFF_REPAIR)
            {
                result = EnumerateHammerneSlots(enumeration, xMove, yMove, itemSlot, target);
            }
            else if (itemId == ITEM_STAFF_WARP)
            {
                result = EnumerateWarpDestinations(enumeration, xMove, yMove, itemSlot, target);
            }
            else
            {
                MakeDecision(&decision, xMove, yMove, AI_ACTION_STAFF, target->index, itemSlot, 0,
                             0);
                result = EmitDecision(enumeration, &decision);
            }
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
        }
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateItems(
    struct PlannerEnumeration* enumeration, int xMove, int yMove)
{
    int itemSlot;
    for (itemSlot = 0; itemSlot < UNIT_ITEM_COUNT; itemSlot++)
    {
        struct AiDecision decision;
        enum ExpansionAutoplayPlannerEnumerationResult result;
        int item = gActiveUnit->items[itemSlot];

        if (item == 0)
            break;
        if (GetItemAttributes(item) & (IA_WEAPON | IA_STAFF))
            continue;
        if (!IsSelfUseItemLegal(item))
            continue;
        MakeDecision(&decision, xMove, yMove, AI_ACTION_USEITEM, 0, itemSlot, 0, 0);
        result = EmitDecision(enumeration, &decision);
        if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
            || enumeration->stopped)
            return result;
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static bool IsPickItemSlotForTarget(
    const struct Unit* unit, int itemSlot, int xMove, int yMove, int xTarget, int yTarget)
{
    int terrain, item, itemId;
    if (unit == NULL || unit->pClassData == NULL
        || itemSlot < 0 || itemSlot >= UNIT_ITEM_COUNT
        || !ActionSemantics_IsKeyTarget(xMove, yMove, xTarget, yTarget))
        return false;
    item = unit->items[itemSlot];
    if (item == 0 || ITEM_USES(item) == 0)
        return false;
    terrain = gBmMapTerrain[yTarget][xTarget];
    itemId = GetItemIndex(item);
    if ((UNIT_CATTRIBUTES(unit) & CA_THIEF) && itemId == ITEM_LOCKPICK)
        return true;
    if (terrain == TERRAIN_CHEST_FULL)
        return itemId == ITEM_CHESTKEY || itemId == ITEM_CHESTKEY_BUNDLE;
    return terrain == TERRAIN_DOOR && itemId == ITEM_DOORKEY;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumeratePick(
    struct PlannerEnumeration* enumeration, int xMove, int yMove)
{
    int yTarget, xTarget;
    bool rogue = gActiveUnit->pClassData->number == CLASS_ROGUE;
    for (yTarget = 0; yTarget < gBmMapSize.y; yTarget++)
    {
        for (xTarget = 0; xTarget < gBmMapSize.x; xTarget++)
        {
            int slot;
            int slotCount = rogue ? 1 : UNIT_ITEM_COUNT;

            for (slot = 0; slot < slotCount; slot++)
            {
                struct AiDecision decision;
                enum ExpansionAutoplayPlannerEnumerationResult result;
                int itemSlot = rogue ? -1 : slot;

                if (itemSlot < 0
                    ? !ActionSemantics_IsPickTarget(xMove, yMove, xTarget, yTarget)
                    : !IsPickItemSlotForTarget(
                        gActiveUnit, itemSlot, xMove, yMove, xTarget, yTarget))
                    continue;
                MakeDecision(&decision, xMove, yMove, AI_ACTION_PICK, 0,
                             itemSlot < 0 ? 0xFF : itemSlot, xTarget, yTarget);
                result = EmitDecision(enumeration, &decision);
                if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                    || enumeration->stopped)
                    return result;
            }
        }
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateSummon(
    struct PlannerEnumeration* enumeration, int xMove, int yMove, bool normalSummonAvailable,
    bool darkSummonAvailable)
{
    int yTarget;
    int xTarget;
    if (normalSummonAvailable)
    {
        for (yTarget = 0; yTarget < gBmMapSize.y; yTarget++)
        {
            for (xTarget = 0; xTarget < gBmMapSize.x; xTarget++)
            {
                struct AiDecision decision;
                enum ExpansionAutoplayPlannerEnumerationResult result;

                if (!ActionSemantics_IsNormalSummonTarget(gActiveUnit, xMove, yMove, xTarget,
                                                          yTarget))
                    continue;
                MakeDecision(&decision, xMove, yMove, AI_ACTION_SUMMON, 0, 0xFF, xTarget,
                             yTarget);
                result = EmitDecision(enumeration, &decision);
                if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                    || enumeration->stopped)
                    return result;
            }
        }
    }
    if (darkSummonAvailable)
    {
        struct AiDecision decision;

        MakeDecision(&decision, xMove, yMove, AI_ACTION_DKSUMMON, 0, 0xFF, 0, 0);
        return EmitDecision(enumeration, &decision);
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

enum ExpansionAutoplayPlannerEnumerationResult ExpansionAutoplayPlanner_EnumerateLegalActions(
    ExpansionAutoplayPlannerActionVisitor visitor, void* context, u32* countOut)
{
    struct PlannerEnumeration enumeration;
    enum ExpansionAutoplayPlannerEnumerationResult result =
        EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
    int yMove;
    int xMove;
    bool normalSummonAvailable;
    bool darkSummonAvailable;
    if (countOut != NULL)
        *countOut = 0;
    if (gActiveUnit == NULL
        || gActiveUnit->pCharacterData == NULL
        || gActiveUnit->pClassData == NULL
        || (gActiveUnit->state & US_UNAVAILABLE)
        || !IsMapReady())
        return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_UNAVAILABLE;

    normalSummonAvailable = ActionSemantics_IsNormalSummonAvailable(gActiveUnit, false);
    darkSummonAvailable = ActionSemantics_IsDarkSummonAvailable(gActiveUnit);
    enumeration.visitor = visitor;
    enumeration.context = context;
    enumeration.count = 0;
    enumeration.stopped = false;
    for (yMove = 0; yMove < gBmMapSize.y; yMove++)
    {
        for (xMove = 0; xMove < gBmMapSize.x; xMove++)
        {
            if (!IsReachableDestination(xMove, yMove))
                continue;
            result = EnumerateWait(&enumeration, xMove, yMove);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration.stopped)
                goto done;
            result = EnumerateCombat(&enumeration, xMove, yMove);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration.stopped)
                goto done;
            result = EnumerateStaff(&enumeration, xMove, yMove);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration.stopped)
                goto done;
            result = EnumerateItems(&enumeration, xMove, yMove);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration.stopped)
                goto done;
            result = EnumeratePick(&enumeration, xMove, yMove);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration.stopped)
                goto done;
            result = EnumerateSummon(&enumeration, xMove, yMove, normalSummonAvailable,
                                     darkSummonAvailable);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration.stopped)
                goto done;
        }
    }

done:
    if (countOut != NULL)
        *countOut = enumeration.count;
    return result;
}

static bool FindCandidate(u32 ordinal, const struct AiDecision* decision, void* context)
{
    struct PlannerCandidateLookup* lookup = context;
    if (ordinal != lookup->requested)
        return true;
    *lookup->output = *decision;
    lookup->found = true;
    return false;
}

static bool DigestCandidate(u32 ordinal, const struct AiDecision* decision, void* context)
{
    u32* digest = context;

    *digest = MixDigest(*digest, ordinal);
    *digest = MixDigest(*digest, decision->unitId);
    *digest = MixDigest(*digest, decision->xMove | ((u32)decision->yMove << 8));
    *digest = MixDigest(*digest, decision->actionId);
    *digest = MixDigest(*digest, decision->targetId | ((u32)decision->itemSlot << 8));
    *digest = MixDigest(*digest, decision->xTarget | ((u32)decision->yTarget << 8));
    *digest = MixDigest(*digest, decision->unk04);
    *digest = MixCandidateItemState(*digest, decision);
    return true;
}

static bool CandidateSetUnchanged(void)
{
    enum ExpansionAutoplayPlannerEnumerationResult result;
    u32 digest = 2166136261u;
    u32 count;

    result = ExpansionAutoplayPlanner_EnumerateLegalActions(DigestCandidate, &digest, &count);
    return result == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
        && count == CandidateCount()
        && digest == sPlannerCandidateDigest;
}

static bool GetCandidate(u32 ordinal, struct AiDecision* candidate)
{
    struct PlannerCandidateLookup lookup;
    enum ExpansionAutoplayPlannerEnumerationResult result;

    lookup.requested = ordinal;
    lookup.output = candidate;
    lookup.found = false;
    result = ExpansionAutoplayPlanner_EnumerateLegalActions(FindCandidate, &lookup, NULL);
    return result == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
        && lookup.found;
}

static u32 MapRecordCount(void)
{
    if (!IsMapReady())
        return 0;
    return gBmMapSize.x * gBmMapSize.y;
}

static u32 UnitRecordCount(void)
{
    u32 count = 0;
    int unitId;
    for (unitId = 1; unitId < 0xC0; unitId++)
        if (IsCanonicalUnitSlot(unitId)
            && UNIT_IS_VALID(GetUnit(unitId)))
            count++;
    return count;
}

static u32 PageCountFor(u32 recordCount, u32 capacity)
{
    return (recordCount + capacity - 1) / capacity;
}

static u32 MapPageCount(void)
{
    return PageCountFor(MapRecordCount(), EXPANSION_AUTOPLAY_PLANNER_MAP_RECORD_CAPACITY);
}

static u32 UnitPageCount(void)
{
    return PageCountFor(UnitRecordCount(), EXPANSION_AUTOPLAY_PLANNER_UNIT_RECORD_CAPACITY);
}

static u32 InventoryRecordCount(void)
{
    return UnitRecordCount() * UNIT_ITEM_COUNT;
}

static u32 InventoryPageCount(void)
{
    return PageCountFor(InventoryRecordCount(),
                        EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY);
}

static u32 ResourceRecordCount(void)
{
    return 1 + CONVOY_ITEM_COUNT
        + sizeof(gExpansionAutoplayTelemetry) / sizeof(u32);
}

static u32 ResourcePageCount(void)
{
    return PageCountFor(ResourceRecordCount(),
                        EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY);
}

static u32 SafeFlagByteCount(int size)
{
    if (size < 0 || size > PLANNER_FLAG_BYTE_CAPACITY)
        return 0;
    return size;
}

static u32 PermanentFlagRecordCount(void)
{
    return SafeFlagByteCount(GetPermanentFlagBitsSize()) * 8;
}

static u32 FlagRecordCount(void)
{
    return PermanentFlagRecordCount() + SafeFlagByteCount(GetChapterFlagBitsSize()) * 8;
}

static u32 FlagPageCount(void)
{
    u32 pageCount =
        PageCountFor(FlagRecordCount(), EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY);
    return pageCount == 0 ? 1 : pageCount;
}

static u32 ActionPageCount(void)
{
    return PageCountFor(CandidateCount(), EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY);
}

static u32 MapStateDigest(void)
{
    u32 digest = 2166136261u;
    int y;
    int x;
    if (!IsMapReady())
        return 0;
    for (y = 0; y < gBmMapSize.y; y++)
    {
        for (x = 0; x < gBmMapSize.x; x++)
        {
            u32 unit = IsPositionVisible(x, y) ? gBmMapUnit[y][x] : 0;

            digest = MixDigest(digest, gBmMapTerrain[y][x]);
            digest = MixDigest(digest, unit);
            digest = MixDigest(digest, IsPositionVisible(x, y));
        }
    }
    return digest;
}

static bool GetFlagsDigest(u32* result)
{
    u32 digest = 2166136261u;
    u8* flags;
    int size;
    int index;

    flags = GetPermanentFlagBits();
    size = GetPermanentFlagBitsSize();
    if (flags == NULL
        || (SafeFlagByteCount(size) == 0 && size != 0))
        return false;
    size = SafeFlagByteCount(size);
    for (index = 0; index < size; index++)
        digest = MixDigest(digest, flags[index]);
    flags = GetChapterFlagBits();
    size = GetChapterFlagBitsSize();
    if (flags == NULL
        || (SafeFlagByteCount(size) == 0 && size != 0))
        return false;
    size = SafeFlagByteCount(size);
    for (index = 0; index < size; index++)
        digest = MixDigest(digest, flags[index]);
    *result = digest;
    return true;
}

static u32 InventoryDigest(const struct Unit* unit)
{
    u32 digest = 2166136261u;
    int item;
    for (item = 0; item < UNIT_ITEM_COUNT; item++)
        digest = MixDigest(digest, unit->items[item]);
    return digest;
}

static bool GetConvoyDigest(u32* result)
{
    u16* convoy = GetConvoyItemArray();
    u32 digest = 2166136261u;
    int index;
    if (convoy == NULL)
        return false;
    for (index = 0; index < CONVOY_ITEM_COUNT; index++)
        digest = MixDigest(digest, convoy[index]);
    *result = digest;
    return true;
}

static void SetSemanticField(int index, enum ExpansionAutoplayPlannerSemanticFieldId id,
                             enum ExpansionAutoplayPlannerAvailability availability, u32 value)
{
    struct ExpansionAutoplayPlannerSemanticFieldV2* field =
        &gExpansionAutoplayPlannerObservation.payload.summary.fields[index];

    field->id = id;
    field->availability = availability;
    field->valueSize = sizeof(value);
    field->value = availability == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
        ? value : 0;
}

static u32 CampaignAssignmentIdentity(
    u16 activationFlag,
    enum ExpansionAutoplayPlannerAssignmentSource source,
    bool active,
    bool current)
{
    return activationFlag
        | ((u32)source << 16)
        | ((u32)active << 20)
        | ((u32)current << 21)
        | ((u32)EXPANSION_AUTOPLAY_PLANNER_AVAILABLE << 24);
}

static void PublishCampaignSummary(void)
{
    struct ExpansionAutoplayPlannerCampaignV2* campaign =
        &gExpansionAutoplayPlannerObservation.payload.summary.campaign;
    const struct ExpansionAutoplayStrategyBundle* strategyBundle =
        ExpansionAutoplayStrategies_GetCurrentBundle();
    struct ExpansionAutoplayStrategyResolution resolution;
    enum ExpansionAutoplayPlannerAvailability objectiveAvailability =
        EXPANSION_AUTOPLAY_PLANNER_UNSUPPORTED_RULE;
    enum ExpansionAutoplayPlannerAvailability strategyAvailability;
    enum ExpansionAutoplayPlannerAvailability assignmentAvailability;
    enum ExpansionAutoplayPlannerAvailability currentAssignmentAvailability =
        EXPANSION_AUTOPLAY_PLANNER_NOT_APPLICABLE;
    const struct ExpansionAutoplayStrategy* currentStrategy = NULL;
    u8 objectiveCount = 0;
    u8 groupCount = 0;
    u8 strategyCount;
    u8 assignmentCount = 0;
    u8 index;
#if FE8_CHAPTER_OBJECTIVES_ENABLED
    const struct ExpansionChapterObjectiveBundle* objectiveBundle =
        ExpansionChapterObjectives_GetCurrentBundle();

    if (objectiveBundle != NULL)
    {
        objectiveAvailability = EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
        objectiveCount = objectiveBundle->objectiveCount;
        groupCount = objectiveBundle->groupCount;
        if (objectiveCount > EXPANSION_AUTOPLAY_PLANNER_OBJECTIVE_CAPACITY
            || groupCount > EXPANSION_AUTOPLAY_PLANNER_GROUP_CAPACITY)
        {
            objectiveCount = 0;
            groupCount = 0;
            objectiveAvailability = EXPANSION_AUTOPLAY_PLANNER_OUT_OF_RANGE;
        }
    }
    else
    {
        objectiveAvailability = EXPANSION_AUTOPLAY_PLANNER_NOT_APPLICABLE;
    }
#endif

    for (strategyCount = 0;
         strategyCount <= EXPANSION_AUTOPLAY_PLANNER_STRATEGY_CAPACITY;
         strategyCount++)
        if (gExpansionAutoplayStrategies[strategyCount].id == 0)
            break;
    strategyAvailability =
        strategyCount == 0
            ? EXPANSION_AUTOPLAY_PLANNER_NOT_APPLICABLE
            : EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
    if (strategyCount > EXPANSION_AUTOPLAY_PLANNER_STRATEGY_CAPACITY)
    {
        strategyCount = 0;
        strategyAvailability = EXPANSION_AUTOPLAY_PLANNER_OUT_OF_RANGE;
    }
    if (strategyBundle != NULL)
    {
        assignmentCount = strategyBundle->groupAssignmentCount
            + strategyBundle->unitAssignmentCount
            + (strategyBundle->chapterStrategyId != 0);
    }
    if (assignmentCount > EXPANSION_AUTOPLAY_PLANNER_ASSIGNMENT_CAPACITY)
    {
        assignmentCount = 0;
        strategyBundle = NULL;
        assignmentAvailability = EXPANSION_AUTOPLAY_PLANNER_OUT_OF_RANGE;
    }
    else
    {
        assignmentAvailability =
            assignmentCount == 0
                ? EXPANSION_AUTOPLAY_PLANNER_NOT_APPLICABLE
                : EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
    }
    if (assignmentAvailability == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
        && ExpansionAutoplayStrategies_ResolveCurrent(&resolution)
        == EXPANSION_AUTOPLAY_STRATEGY_OK)
    {
        currentStrategy = ExpansionAutoplayStrategies_Find(resolution.strategyId);
        currentAssignmentAvailability = EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
    }
    else
    {
        resolution.strategyId = 0;
        resolution.subjectId = 0;
        resolution.source = EXPANSION_AUTOPLAY_STRATEGY_ASSIGNMENT_NONE;
    }

    campaign->availability = EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
        | ((u32)objectiveAvailability << 8)
        | ((u32)strategyAvailability << 16)
        | ((u32)assignmentAvailability << 24);
    campaign->chapter = (u8)gPlaySt.faction
        | ((u32)(u8)gPlaySt.chapterIndex << 8)
        | ((u32)gPlaySt.chapterModeIndex << 16);
    campaign->counts = objectiveCount
        | ((u32)groupCount << 8)
        | ((u32)strategyCount << 16)
        | ((u32)assignmentCount << 24);
    campaign->currentStrategyId = resolution.strategyId;
    campaign->currentAssignment = (u32)resolution.source << 8
        | ((u32)currentAssignmentAvailability << 16);
    if (currentStrategy != NULL)
    {
        campaign->currentObjectiveCapabilities = currentStrategy->objectiveCapabilities;
        campaign->currentActionCapabilities = currentStrategy->actionCapabilities;
        campaign->currentAssignment |= currentStrategy->flags;
        campaign->currentAssignmentSubject = resolution.subjectId;
    }

#if FE8_CHAPTER_OBJECTIVES_ENABLED
    for (index = 0; index < objectiveCount; index++)
    {
        const struct ExpansionChapterObjective* objective =
            &objectiveBundle->objectives[index];
        struct ExpansionAutoplayPlannerObjectiveV2* record =
            &campaign->objectives[index];
        u32 progress;
        enum ExpansionChapterObjectiveState state =
            ExpansionChapterObjectives_GetSnapshot(objective->id, &progress);

        record->id = objective->id;
        record->completionObjectiveId = objective->completionObjectiveId;
        record->groupId = objective->group == NULL ? 0 : objective->group->id;
        record->activationFlags =
            objective->activationFlag | ((u32)objective->deactivationFlag << 16);
        record->completionFlags =
            objective->eventFlag | ((u32)objective->completionFlag << 16);
        record->kind = objective->untilTurn
            | ((u32)objective->kind << 16)
            | ((u32)objective->protectedCharacter << 24);
        record->area = objective->xMin
            | ((u32)objective->yMin << 8)
            | ((u32)objective->xMax << 16)
            | ((u32)objective->yMax << 24);
        record->status = state
            | ((u32)EXPANSION_AUTOPLAY_PLANNER_AVAILABLE << 8)
            | (progress << 16);
    }
    for (index = 0; index < groupCount; index++)
    {
        const struct ExpansionChapterAiGroup* group = &objectiveBundle->groups[index];
        struct ExpansionAutoplayPlannerGroupV2* record = &campaign->groups[index];
        int member;

        record->id = group->id;
        if (group->memberCount > EXPANSION_AUTOPLAY_PLANNER_GROUP_MEMBER_CAPACITY)
        {
            record->identity = (u32)EXPANSION_AUTOPLAY_PLANNER_OUT_OF_RANGE << 24;
            continue;
        }
        record->identity = group->memberCount
            | ((u32)EXPANSION_AUTOPLAY_PLANNER_AVAILABLE << 24);
        for (member = 0; member < group->memberCount; member++)
            record->members[member / 4] |= (u32)group->members[member] << (member % 4 * 8);
    }
#endif
    for (index = 0; index < strategyCount; index++)
    {
        const struct ExpansionAutoplayStrategy* strategy =
            &gExpansionAutoplayStrategies[index];
        struct ExpansionAutoplayPlannerStrategyV2* record =
            &campaign->strategies[index];

        record->id = strategy->id;
        record->objectiveCapabilities = strategy->objectiveCapabilities;
        record->actionCapabilities = strategy->actionCapabilities;
        record->identity = strategy->flags
            | ((u32)EXPANSION_AUTOPLAY_PLANNER_AVAILABLE << 24);
    }
    index = 0;
    if (strategyBundle != NULL && strategyBundle->chapterStrategyId != 0)
    {
        struct ExpansionAutoplayPlannerAssignmentV2* record =
            &campaign->assignments[index++];
        bool active = strategyBundle->chapterActivationFlag
                == EXPANSION_AUTOPLAY_STRATEGY_FLAG_NONE
            || CheckFlag(strategyBundle->chapterActivationFlag);

        record->identity = CampaignAssignmentIdentity(
            strategyBundle->chapterActivationFlag,
            EXPANSION_AUTOPLAY_PLANNER_ASSIGNMENT_CHAPTER,
            active,
            resolution.source == EXPANSION_AUTOPLAY_STRATEGY_ASSIGNMENT_CHAPTER);
        record->subjectId = strategyBundle->chapterId;
        record->strategyId = strategyBundle->chapterStrategyId;
    }
    if (strategyBundle != NULL)
    {
        int assignment;

        for (assignment = 0; assignment < strategyBundle->groupAssignmentCount; assignment++)
        {
            const struct ExpansionAutoplayStrategyGroupAssignment* source =
                &strategyBundle->groupAssignments[assignment];
            struct ExpansionAutoplayPlannerAssignmentV2* record =
                &campaign->assignments[index++];
            bool active = source->activationFlag == EXPANSION_AUTOPLAY_STRATEGY_FLAG_NONE
                || CheckFlag(source->activationFlag);

            record->identity = CampaignAssignmentIdentity(
                source->activationFlag,
                EXPANSION_AUTOPLAY_PLANNER_ASSIGNMENT_GROUP,
                active,
                resolution.source == EXPANSION_AUTOPLAY_STRATEGY_ASSIGNMENT_GROUP
                    && resolution.subjectId == source->groupId
                    && resolution.strategyId == source->strategyId);
            record->subjectId = source->groupId;
            record->strategyId = source->strategyId;
        }
        for (assignment = 0; assignment < strategyBundle->unitAssignmentCount; assignment++)
        {
            const struct ExpansionAutoplayStrategyUnitAssignment* source =
                &strategyBundle->unitAssignments[assignment];
            struct ExpansionAutoplayPlannerAssignmentV2* record =
                &campaign->assignments[index++];
            bool active = source->activationFlag == EXPANSION_AUTOPLAY_STRATEGY_FLAG_NONE
                || CheckFlag(source->activationFlag);

            record->identity = CampaignAssignmentIdentity(
                source->activationFlag,
                EXPANSION_AUTOPLAY_PLANNER_ASSIGNMENT_UNIT,
                active,
                resolution.source == EXPANSION_AUTOPLAY_STRATEGY_ASSIGNMENT_UNIT
                    && resolution.subjectId == source->character
                    && resolution.strategyId == source->strategyId);
            record->subjectId = source->character;
            record->strategyId = source->strategyId;
        }
    }
}

static void PublishSummaryPage(void)
{
    enum ExpansionAutoplayPlannerAvailability mapAvailability =
        EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
    enum ExpansionAutoplayPlannerAvailability unitAvailability =
        EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
    enum ExpansionAutoplayPlannerAvailability objectiveAvailability =
        EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
    enum ExpansionAutoplayPlannerAvailability flagAvailability =
        EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
    enum ExpansionAutoplayPlannerAvailability resourceAvailability =
        EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
    u32 objectiveState = 0;
    u32 flagDigest;
    u32 convoyDigest;
    if (!IsMapReady())
        mapAvailability = EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED;
    unitAvailability = GetUnitAvailability(gActiveUnit);
#if FE8_CHAPTER_OBJECTIVES_ENABLED
    if (gExpansionChapterObjectiveTelemetry.objectiveId == 0)
        objectiveAvailability = EXPANSION_AUTOPLAY_PLANNER_NOT_APPLICABLE;
    else
        objectiveState = gExpansionChapterObjectiveTelemetry.state
            | (gExpansionChapterObjectiveTelemetry.progress << 8);
#else
    objectiveAvailability = EXPANSION_AUTOPLAY_PLANNER_UNSUPPORTED_RULE;
#endif
    flagDigest = 0;
    if (!GetFlagsDigest(&flagDigest))
        flagAvailability = EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED;
    convoyDigest = 0;
    if (!GetConvoyDigest(&convoyDigest))
        resourceAvailability = EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED;

    SetSemanticField(0, EXPANSION_AUTOPLAY_PLANNER_FIELD_MAP_DIMENSIONS, mapAvailability,
                     (u16)gBmMapSize.x | ((u32)(u16)gBmMapSize.y << 16));
    SetSemanticField(1, EXPANSION_AUTOPLAY_PLANNER_FIELD_MAP_STATE_DIGEST, mapAvailability,
                     MapStateDigest());
    SetSemanticField(
        2,
        EXPANSION_AUTOPLAY_PLANNER_FIELD_ACTIVE_UNIT,
        unitAvailability,
        unitAvailability == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
            ? (u8)gActiveUnit->index
                | ((u32)gActiveUnit->pCharacterData->number << 8)
                | ((u32)gActiveUnit->pClassData->number << 16)
            : 0);
    SetSemanticField(
        3,
        EXPANSION_AUTOPLAY_PLANNER_FIELD_ACTIVE_UNIT_STATE,
        unitAvailability,
        unitAvailability == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
            ? (u8)gActiveUnit->xPos
                | ((u32)(u8)gActiveUnit->yPos << 8)
                | ((u32)(u8)gActiveUnit->curHP << 16)
                | ((u32)(u8)gActiveUnit->maxHP << 24)
            : 0);
    SetSemanticField(
        4,
        EXPANSION_AUTOPLAY_PLANNER_FIELD_OBJECTIVE_ID,
        objectiveAvailability,
#if FE8_CHAPTER_OBJECTIVES_ENABLED
        gExpansionChapterObjectiveTelemetry.objectiveId
#else
        0
#endif
    );
    SetSemanticField(5, EXPANSION_AUTOPLAY_PLANNER_FIELD_OBJECTIVE_STATE,
                     objectiveAvailability, objectiveState);
    SetSemanticField(6, EXPANSION_AUTOPLAY_PLANNER_FIELD_FLAGS_DIGEST, flagAvailability,
                     flagDigest);
    SetSemanticField(7, EXPANSION_AUTOPLAY_PLANNER_FIELD_RESOURCE_DIGEST,
                     resourceAvailability,
                     MixDigest(MixDigest(2166136261u, gPlaySt.partyGoldAmount), convoyDigest));
    PublishCampaignSummary();
    gExpansionAutoplayPlannerObservation.start.recordStart = 0;
    gExpansionAutoplayPlannerObservation.count.recordCount =
        EXPANSION_AUTOPLAY_PLANNER_SEMANTIC_FIELD_CAPACITY;
    gExpansionAutoplayPlannerObservation.totalRecordCount =
        EXPANSION_AUTOPLAY_PLANNER_SEMANTIC_FIELD_CAPACITY;
}

static void PublishMapPage(u32 mapPage)
{
    u32 start = mapPage * EXPANSION_AUTOPLAY_PLANNER_MAP_RECORD_CAPACITY;
    u32 total = MapRecordCount();
    u32 remaining = total - start;
    u32 count = remaining < EXPANSION_AUTOPLAY_PLANNER_MAP_RECORD_CAPACITY
        ? remaining
        : EXPANSION_AUTOPLAY_PLANNER_MAP_RECORD_CAPACITY;
    u32 index;
    for (index = 0; index < count; index++)
    {
        u32 ordinal = start + index;
        int x = ordinal % gBmMapSize.x;
        int y = ordinal / gBmMapSize.x;
        enum ExpansionAutoplayPlannerAvailability availability =
            IsPositionVisible(x, y)
                ? EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
                : EXPANSION_AUTOPLAY_PLANNER_NOT_VISIBLE;
        u32 unit = availability == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
            ? gBmMapUnit[y][x] : 0;

        gExpansionAutoplayPlannerObservation.payload.mapCells[index] =
            (x & 0x3F)
            | ((u32)(y & 0x3F) << 6)
            | ((u32)gBmMapTerrain[y][x] << 12)
            | (unit << 20)
            | ((u32)availability << 28);
    }
    gExpansionAutoplayPlannerObservation.start.recordStart = start;
    gExpansionAutoplayPlannerObservation.count.recordCount = count;
    gExpansionAutoplayPlannerObservation.totalRecordCount = total;
}

static struct Unit* GetUnitRecord(u32 ordinal)
{
    u32 current = 0;
    int unitId;
    for (unitId = 1; unitId < 0xC0; unitId++)
    {
        struct Unit* unit = GetUnit(unitId);

        if (!IsCanonicalUnitSlot(unitId) || !UNIT_IS_VALID(unit))
            continue;
        if (current++ == ordinal)
            return unit;
    }
    return NULL;
}

static void PublishUnitPage(u32 unitPage)
{
    u32 start = unitPage * EXPANSION_AUTOPLAY_PLANNER_UNIT_RECORD_CAPACITY;
    u32 total = UnitRecordCount();
    u32 remaining = total - start;
    u32 count = remaining < EXPANSION_AUTOPLAY_PLANNER_UNIT_RECORD_CAPACITY
        ? remaining : EXPANSION_AUTOPLAY_PLANNER_UNIT_RECORD_CAPACITY;
    u32 index;
    for (index = 0; index < count; index++)
    {
        struct Unit* unit = GetUnitRecord(start + index);
        struct ExpansionAutoplayPlannerUnitV2* record =
            &gExpansionAutoplayPlannerObservation.payload.units[index];
        enum ExpansionAutoplayPlannerAvailability availability =
            GetUnitAvailability(unit);
        int equippedSlot;
        u32 semanticFlags = 0;

        record->identity = (u8)unit->index | ((u32)availability << 24);
        record->rescueAndEquipped = 0xFF00;
        if (availability != EXPANSION_AUTOPLAY_PLANNER_NOT_VISIBLE
            && availability != EXPANSION_AUTOPLAY_PLANNER_NOT_APPLICABLE)
        {
            record->identity |= ((u32)unit->pCharacterData->number << 8)
                | ((u32)unit->pClassData->number << 16);
            record->position = (u8)unit->xPos
                | ((u32)(u8)unit->yPos << 8)
                | ((u32)(u8)unit->curHP << 16)
                | ((u32)(u8)unit->maxHP << 24);
            record->state = unit->state;
            record->inventoryDigest = InventoryDigest(unit);
            if (!(unit->state & US_UNAVAILABLE))
                semanticFlags |= EXPANSION_AUTOPLAY_PLANNER_UNIT_DEPLOYED;
            if (unit->state & US_DEAD)
                semanticFlags |= EXPANSION_AUTOPLAY_PLANNER_UNIT_DEAD;
            if (unit->state & US_HAS_MOVED)
                semanticFlags |= EXPANSION_AUTOPLAY_PLANNER_UNIT_MOVED;
            if (unit->state & (US_HAS_MOVED | US_HAS_MOVED_AI))
                semanticFlags |= EXPANSION_AUTOPLAY_PLANNER_UNIT_ACTED;
            if (unit->state & US_RESCUED)
                semanticFlags |= EXPANSION_AUTOPLAY_PLANNER_UNIT_RESCUED;
            if (unit->state & US_RESCUING)
                semanticFlags |= EXPANSION_AUTOPLAY_PLANNER_UNIT_RESCUING;
            record->status = unit->statusIndex
                | ((u32)unit->statusDuration << 4)
                | ((u32)(u8)unit->level << 8)
                | ((u32)unit->exp << 16)
                | (semanticFlags << 24);
            equippedSlot = GetUnitEquippedWeaponSlot(unit);
            if (equippedSlot >= UNIT_ITEM_COUNT)
                equippedSlot = -1;
            record->rescueAndEquipped = unit->rescue
                | ((u32)(equippedSlot < 0 ? 0xFF : equippedSlot) << 8)
                | ((u32)(equippedSlot < 0 ? 0 : unit->items[equippedSlot]) << 16);
            record->stats0 = (u8)GetUnitPower(unit)
                | ((u32)(u8)GetUnitSkill(unit) << 8)
                | ((u32)(u8)GetUnitSpeed(unit) << 16)
                | ((u32)(u8)GetUnitLuck(unit) << 24);
            record->stats1 = (u8)GetUnitDefense(unit)
                | ((u32)(u8)GetUnitResistance(unit) << 8)
                | ((u32)(u8)UNIT_CON(unit) << 16)
                | ((u32)(u8)UNIT_MOV(unit) << 24);
            record->ranks0 = unit->ranks[0]
                | ((u32)unit->ranks[1] << 8)
                | ((u32)unit->ranks[2] << 16)
                | ((u32)unit->ranks[3] << 24);
            record->ranks1 = unit->ranks[4]
                | ((u32)unit->ranks[5] << 8)
                | ((u32)unit->ranks[6] << 16)
                | ((u32)unit->ranks[7] << 24);
        }
    }
    gExpansionAutoplayPlannerObservation.start.recordStart = start;
    gExpansionAutoplayPlannerObservation.count.recordCount = count;
    gExpansionAutoplayPlannerObservation.totalRecordCount = total;
}

static u32 __attribute__((noinline)) ValueRecordIdentity(
    enum ExpansionAutoplayPlannerValueKind kind, u32 index,
    enum ExpansionAutoplayPlannerAvailability availability)
{
    return kind
        | ((index & 0xFFFF) << 8)
        | ((u32)availability << 24);
}

static void PublishInventoryPage(u32 inventoryPage)
{
    u32 start = inventoryPage * EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY;
    u32 total = InventoryRecordCount();
    u32 remaining = total - start;
    u32 count =
        remaining < EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY
            ? remaining
            : EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY;
    u32 index;
    for (index = 0; index < count; index++)
    {
        u32 ordinal = start + index;
        struct Unit* unit = GetUnitRecord(ordinal / UNIT_ITEM_COUNT);
        int itemSlot = ordinal % UNIT_ITEM_COUNT;
        int item = unit->items[itemSlot];
        enum ExpansionAutoplayPlannerAvailability availability =
            GetUnitAvailability(unit);
        struct ExpansionAutoplayPlannerValueRecordV2* record =
            &gExpansionAutoplayPlannerObservation.payload.inventory[index];

        if (availability == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE)
            availability = item == 0
                ? EXPANSION_AUTOPLAY_PLANNER_EMPTY
                : EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
        record->identity =
            ValueRecordIdentity(EXPANSION_AUTOPLAY_PLANNER_VALUE_UNIT_ITEM,
                                (u8)unit->index | ((u32)itemSlot << 8), availability);
        record->value =
            availability == EXPANSION_AUTOPLAY_PLANNER_NOT_VISIBLE
                ? 0
                : (u16)item;
    }
    gExpansionAutoplayPlannerObservation.start.recordStart = start;
    gExpansionAutoplayPlannerObservation.count.recordCount = count;
    gExpansionAutoplayPlannerObservation.totalRecordCount = total;
}

static void PublishResourcePage(u32 resourcePage)
{
    u32 start = resourcePage * EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY;
    u32 total = ResourceRecordCount();
    u32 remaining = total - start;
    u32 count =
        remaining < EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY
            ? remaining
            : EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY;
    u16* convoy = GetConvoyItemArray();
    const u32* telemetry = (const u32*)&gExpansionAutoplayTelemetry;
    u32 index;
    for (index = 0; index < count; index++)
    {
        u32 ordinal = start + index;
        struct ExpansionAutoplayPlannerValueRecordV2* record =
            &gExpansionAutoplayPlannerObservation.payload.resources[index];

        if (ordinal == 0)
        {
            record->identity = ValueRecordIdentity(EXPANSION_AUTOPLAY_PLANNER_VALUE_GOLD, 0,
                                                   EXPANSION_AUTOPLAY_PLANNER_AVAILABLE);
            record->value = gPlaySt.partyGoldAmount;
        }
        else if (ordinal <= CONVOY_ITEM_COUNT)
        {
            int convoySlot = ordinal - 1;
            int item = convoy == NULL ? 0 : convoy[convoySlot];
            enum ExpansionAutoplayPlannerAvailability availability =
                convoy == NULL
                    ? EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED
                    : (item == 0
                        ? EXPANSION_AUTOPLAY_PLANNER_EMPTY
                        : EXPANSION_AUTOPLAY_PLANNER_AVAILABLE);

            record->identity = ValueRecordIdentity(
                EXPANSION_AUTOPLAY_PLANNER_VALUE_CONVOY_ITEM, convoySlot, availability);
            record->value = (u16)item;
        }
        else
        {
            int telemetryIndex =
                ordinal - 1 - CONVOY_ITEM_COUNT;

            record->identity =
                ValueRecordIdentity(EXPANSION_AUTOPLAY_PLANNER_VALUE_AUTOPLAY_TELEMETRY,
                                    telemetryIndex, EXPANSION_AUTOPLAY_PLANNER_AVAILABLE);
            record->value = telemetry[telemetryIndex];
        }
    }
    gExpansionAutoplayPlannerObservation.start.recordStart = start;
    gExpansionAutoplayPlannerObservation.count.recordCount = count;
    gExpansionAutoplayPlannerObservation.totalRecordCount = total;
}

static void PublishFlagPage(u32 flagPage)
{
    u32 start = flagPage * EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY;
    u32 total = FlagRecordCount();
    u32 remaining = total - start;
    u32 count =
        remaining < EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY
            ? remaining
            : EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY;
    u32 permanentCount = PermanentFlagRecordCount();
    u8* permanentFlags = GetPermanentFlagBits();
    u8* chapterFlags = GetChapterFlagBits();
    u32 index;
    for (index = 0; index < count; index++)
    {
        u32 ordinal = start + index;
        bool permanent = ordinal < permanentCount;
        u32 flag = permanent ? ordinal : ordinal - permanentCount;
        u8* flags = permanent ? permanentFlags : chapterFlags;
        enum ExpansionAutoplayPlannerAvailability availability =
            flags == NULL
                ? EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED
                : EXPANSION_AUTOPLAY_PLANNER_AVAILABLE;
        struct ExpansionAutoplayPlannerValueRecordV2* record =
            &gExpansionAutoplayPlannerObservation.payload.flags[index];

        record->identity = ValueRecordIdentity(
            permanent
                ? EXPANSION_AUTOPLAY_PLANNER_VALUE_PERMANENT_FLAG
                : EXPANSION_AUTOPLAY_PLANNER_VALUE_CHAPTER_FLAG,
            flag,
            availability);
        record->value = flags == NULL
            ? 0
            : (flags[flag >> 3] >> (flag & 7)) & 1;
    }
    gExpansionAutoplayPlannerObservation.start.recordStart = start;
    gExpansionAutoplayPlannerObservation.count.recordCount = count;
    gExpansionAutoplayPlannerObservation.totalRecordCount = total;
}

static bool CollectPageAction(u32 ordinal, const struct AiDecision* decision, void* context)
{
    struct PlannerPageCollector* collector = context;
    struct ExpansionAutoplayPlannerActionV2* action;
    u32 token[4];
    if (ordinal < collector->start)
        return true;
    if (ordinal >= collector->start + collector->count)
        return false;
    action = &gExpansionAutoplayPlannerObservation.payload.actions[
        ordinal - collector->start];
    MakeToken(decision, gExpansionAutoplayPlannerObservation.observationId, ordinal, token);
    action->kind = ActionKindFromAiAction(decision->actionId);
    action->actor = decision->unitId;
    action->destination = (u16)decision->xMove
        | ((u32)(u16)decision->yMove << 16);
    action->target = decision->targetId
        | ((u32)decision->xTarget << 8)
        | ((u32)decision->yTarget << 16);
    action->itemSlot = decision->itemSlot | ((u32)decision->unk04 << 8);
    action->token0 = token[0];
    action->token1 = token[1];
    action->token2 = token[2];
    action->token3 = token[3];
    action->actionId = decision->actionId;
    return true;
}

static void PublishActionPage(u32 actionPage)
{
    u32 start = actionPage * EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY;
    u32 remaining = CandidateCount() - start;
    struct PlannerPageCollector collector;

    collector.start = start;
    collector.count =
        remaining < EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY
            ? remaining
            : EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY;
    ExpansionAutoplayPlanner_EnumerateLegalActions(CollectPageAction, &collector, NULL);
    gExpansionAutoplayPlannerObservation.start.recordStart = start;
    gExpansionAutoplayPlannerObservation.count.recordCount = collector.count;
    gExpansionAutoplayPlannerObservation.totalRecordCount = CandidateCount();
}

static void __attribute__((noinline)) PublishRuntimeIdentity(void)
{
    gExpansionAutoplayPlannerObservation.magic = EXPANSION_AUTOPLAY_PLANNER_MAGIC;
    gExpansionAutoplayPlannerObservation.version =
        EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION;
    gExpansionAutoplayPlannerObservation.byteSize =
        sizeof(struct ExpansionAutoplayPlannerObservationV2);
    gExpansionAutoplayPlannerObservation.runId = sPlannerRunId;
    gExpansionAutoplayPlannerObservation.actualRomIdentity = ActualRomIdentity();
    gExpansionAutoplayPlannerObservation.actualConfigIdentity = ActualConfigIdentity();
    gExpansionAutoplayPlannerObservation.actualScenarioIdentity = ActualScenarioIdentity();
    gExpansionAutoplayPlannerObservation.actualSeedIdentity = ActualSeedIdentity();
}

static bool PublishPage(u32 pageIndex)
{
    u32 mapPages = MapPageCount();
    u32 unitPages = UnitPageCount();
    u32 inventoryPages = InventoryPageCount();
    u32 resourcePages = ResourcePageCount();
    u32 flagPages = FlagPageCount();
    u32 pageCount =
        1
        + mapPages
        + unitPages
        + inventoryPages
        + resourcePages
        + flagPages
        + ActionPageCount();
    u8* payload = (u8*)&gExpansionAutoplayPlannerObservation.payload;
    int index;
    int payloadSize = sizeof(gExpansionAutoplayPlannerObservation.payload);
    u16 seeds[3];
    if (pageIndex >= pageCount)
        return false;

    gExpansionAutoplayPlannerObservation.state =
        EXPANSION_AUTOPLAY_PLANNER_STATE_DISABLED;
    for (index = 0; index < payloadSize; index++)
        payload[index] = 0;
    StoreRNState(seeds);
    PublishRuntimeIdentity();
    gExpansionAutoplayPlannerObservation.pageCount = pageCount;
    gExpansionAutoplayPlannerObservation.totalActionCount = CandidateCount();
    gExpansionAutoplayPlannerObservation.rejection =
        EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE;
    gExpansionAutoplayPlannerObservation.chapterIndex =
        (u8)gPlaySt.chapterIndex;
    gExpansionAutoplayPlannerObservation.chapterTurn =
        gPlaySt.chapterTurnNumber;
    gExpansionAutoplayPlannerObservation.rngState0 = seeds[0];
    gExpansionAutoplayPlannerObservation.rngState1 = seeds[1];
    gExpansionAutoplayPlannerObservation.rngState2 = seeds[2];
    gExpansionAutoplayPlannerObservation.rngLcg = GetLCGRNValue();
    gExpansionAutoplayPlannerObservation.rngConsumption =
        GetRNConsumptionCount();
    if (pageIndex == 0)
    {
        gExpansionAutoplayPlannerObservation.pageKind =
            EXPANSION_AUTOPLAY_PLANNER_PAGE_SUMMARY;
        PublishSummaryPage();
    }
    else if (pageIndex <= mapPages)
    {
        gExpansionAutoplayPlannerObservation.pageKind =
            EXPANSION_AUTOPLAY_PLANNER_PAGE_MAP;
        PublishMapPage(pageIndex - 1);
    }
    else if (pageIndex <= mapPages + unitPages)
    {
        gExpansionAutoplayPlannerObservation.pageKind =
            EXPANSION_AUTOPLAY_PLANNER_PAGE_UNITS;
        PublishUnitPage(pageIndex - 1 - mapPages);
    }
    else if (pageIndex <= mapPages + unitPages + inventoryPages)
    {
        gExpansionAutoplayPlannerObservation.pageKind =
            EXPANSION_AUTOPLAY_PLANNER_PAGE_INVENTORY;
        PublishInventoryPage(
            pageIndex - 1 - mapPages - unitPages);
    }
    else if (pageIndex
        <= mapPages + unitPages + inventoryPages + resourcePages)
    {
        gExpansionAutoplayPlannerObservation.pageKind =
            EXPANSION_AUTOPLAY_PLANNER_PAGE_RESOURCES;
        PublishResourcePage(
            pageIndex
            - 1
            - mapPages
            - unitPages
            - inventoryPages);
    }
    else if (pageIndex
        <= mapPages
            + unitPages
            + inventoryPages
            + resourcePages
            + flagPages)
    {
        gExpansionAutoplayPlannerObservation.pageKind =
            EXPANSION_AUTOPLAY_PLANNER_PAGE_FLAGS;
        PublishFlagPage(
            pageIndex
            - 1
            - mapPages
            - unitPages
            - inventoryPages
            - resourcePages);
    }
    else
    {
        gExpansionAutoplayPlannerObservation.pageKind =
            EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS;
        PublishActionPage(
            pageIndex
            - 1
            - mapPages
            - unitPages
            - inventoryPages
            - resourcePages
            - flagPages);
    }
    gExpansionAutoplayPlannerObservation.pageIndex = pageIndex;
    PLANNER_PUBLISH_BARRIER();
    gExpansionAutoplayPlannerObservation.state =
        EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING;
    return true;
}

static u32 SemanticStateDigest(void)
{
    u32 digest = 2166136261u;
    u32 flagDigest = 0;
    u16* convoy;
    int index;

    digest = MixDigest(digest, (u8)gPlaySt.chapterIndex);
    digest = MixDigest(digest, gPlaySt.chapterModeIndex);
    digest = MixDigest(digest, gPlaySt.chapterTurnNumber);
    digest = MixDigest(digest, gPlaySt.partyGoldAmount);
#if FE8_CHAPTER_OBJECTIVES_ENABLED
    digest = MixDigest(digest, gExpansionChapterObjectiveTelemetry.objectiveId);
    digest = MixDigest(digest, gExpansionChapterObjectiveTelemetry.state);
    digest = MixDigest(digest, gExpansionChapterObjectiveTelemetry.progress);
    digest = MixDigest(digest, gExpansionChapterObjectiveTelemetry.activeCount);
#endif
    for (index = 1; index < 0x40; index++)
    {
        struct Unit* unit = GetUnit(index);
        int item;

        if (unit == NULL || unit->pCharacterData == NULL)
        {
            digest = MixDigest(digest, 0);
            continue;
        }
        digest = MixDigest(digest, unit->pCharacterData->number);
        digest = MixDigest(
            digest,
            unit->pClassData == NULL ? 0 : unit->pClassData->number);
        digest = MixDigest(digest, unit->level);
        digest = MixDigest(digest, unit->exp);
        digest = MixDigest(digest, unit->curHP);
        digest = MixDigest(digest, unit->state);
        for (item = 0; item < UNIT_ITEM_COUNT; item++)
            digest = MixDigest(digest, unit->items[item]);
    }
    convoy = GetConvoyItemArray();
    if (convoy != NULL)
        for (index = 0; index < CONVOY_ITEM_COUNT; index++)
            digest = MixDigest(digest, convoy[index]);
    digest = MixDigest(digest, GetFlagsDigest(&flagDigest)
                                   ? EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
                                   : EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED);
    digest = MixDigest(digest, flagDigest);
    return MixDigest(digest, sPlannerTraceDigest);
}

static void PublishReadyState(void)
{
    u32 rejection = gExpansionAutoplayPlannerObservation.rejection;

    ClearObservation();
    PublishRuntimeIdentity();
    gExpansionAutoplayPlannerObservation.pageCount = 1;
    gExpansionAutoplayPlannerObservation.rejection = rejection;
    PLANNER_PUBLISH_BARRIER();
    gExpansionAutoplayPlannerObservation.state =
        EXPANSION_AUTOPLAY_PLANNER_STATE_READY;
}

void ExpansionAutoplayPlanner_Reset(void)
{
    ClearObservation();
    ClearCheckpoint();
    ClearFullCommand();
    sPlannerActive = false;
    sPlannerRunId = 0;
    sPlannerNextObservationId = 1;
    SetCandidateState(0, 0);
    sPlannerTraceDigest = 2166136261u;
    sPlannerCommitCount = 0;
    sPlannerCandidateDigest = 0;
}

void ExpansionAutoplayPlanner_OnMapReset(void)
{
    if (!sPlannerActive)
    {
        ExpansionAutoplayPlanner_Reset();
        return;
    }

    ClearObservation();
    ClearFullCommand();
    SetCandidateState(0, 0);
    sPlannerCandidateDigest = 0;
    ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER);
}

void ExpansionAutoplayPlanner_OnMapReady(void)
{
    if (sPlannerActive)
        PublishReadyState();
}

bool ExpansionAutoplayPlanner_PollStart(void)
{
    if (gExpansionAutoplayPlannerCommand.kind
        == EXPANSION_AUTOPLAY_PLANNER_COMMAND_NONE)
    {
        if (!sPlannerActive
            && gExpansionAutoplayPlannerObservation.state
                != EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED
            && gExpansionAutoplayPlannerObservation.state
                != EXPANSION_AUTOPLAY_PLANNER_STATE_CANCELLED)
            PublishReadyState();
        return false;
    }
    if (sPlannerActive
        || gExpansionAutoplayPlannerObservation.state
            != EXPANSION_AUTOPLAY_PLANNER_STATE_READY
        || gExpansionAutoplayPlannerCommand.kind
            != EXPANSION_AUTOPLAY_PLANNER_COMMAND_START)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
        return false;
    }
    if (!IsCommandHeaderValid())
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
        return false;
    }
    if (gExpansionAutoplayPlannerCommand.payload.start.expectedRomIdentity
            != gExpansionAutoplayPlannerObservation.actualRomIdentity
        || gExpansionAutoplayPlannerCommand.payload.start.expectedConfigIdentity
            != gExpansionAutoplayPlannerObservation.actualConfigIdentity
        || gExpansionAutoplayPlannerCommand.payload.start.expectedScenarioIdentity
            != gExpansionAutoplayPlannerObservation.actualScenarioIdentity
        || gExpansionAutoplayPlannerCommand.payload.start.expectedSeedIdentity
            != gExpansionAutoplayPlannerObservation.actualSeedIdentity)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
        return false;
    }
    if (ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER)
        != EXPANSION_AUTOPLAY_OK)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_NOT_READY);
        return false;
    }

    ClearCheckpoint();
    sPlannerActive = true;
    sPlannerRunId++;
    sPlannerNextObservationId = 1;
    sPlannerTraceDigest = MixDigest(2166136261u, sPlannerRunId);
    sPlannerCommitCount = 0;
    gExpansionAutoplayPlannerCommand.result = 1;
    gExpansionAutoplayPlannerCommand.rejection =
        EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE;
    ClearCommand();
    PublishReadyState();
    return true;
}

bool ExpansionAutoplayPlanner_IsActive(void)
{
    return sPlannerActive;
}

bool ExpansionAutoplayPlanner_PrepareActionData(const struct AiDecision* decision)
{
    struct Unit* target;
    int item;
    int itemId;
    if (decision == NULL
        || gActiveUnit == NULL
        || decision->unitId != gActiveUnitId)
        return false;
    if (decision->actionId == AI_ACTION_COMBAT
        && decision->targetId == 0)
    {
        if (decision->itemSlot >= UNIT_ITEM_COUNT)
            return false;
        item = gActiveUnit->items[decision->itemSlot];
        return CanUnitUseWeapon(gActiveUnit, item)
            && !((GetItemAttributes(item) & IA_MAGIC)
                && IsPositionMagicSealed(decision->xMove, decision->yMove))
            && IsSnagAttackTargetAt(item, decision->xMove, decision->yMove, decision->xTarget,
                                    decision->yTarget);
    }
    if (decision->actionId == AI_ACTION_SUMMON)
    {
        if (decision->targetId != 0
            || decision->itemSlot != 0xFF
            || decision->unk04 != 0xFF
            || !ActionSemantics_IsNormalSummonAvailable(gActiveUnit, false)
            || !ActionSemantics_IsNormalSummonTarget(gActiveUnit, decision->xMove,
                                                      decision->yMove, decision->xTarget,
                                                      decision->yTarget))
            return false;
        gActionData.xOther = decision->xTarget;
        gActionData.yOther = decision->yTarget;
        return true;
    }
    if (decision->actionId == AI_ACTION_DKSUMMON)
    {
        return decision->targetId == 0
            && decision->itemSlot == 0xFF
            && decision->unk04 == 0xFF
            && decision->xTarget == 0
            && decision->yTarget == 0
            && ActionSemantics_IsDarkSummonAvailable(gActiveUnit);
    }
    if (decision->actionId == AI_ACTION_PICK)
    {
        if (decision->targetId != 0)
            return false;
        if (gActiveUnit->pClassData->number == CLASS_ROGUE)
        {
            if (decision->itemSlot != 0xFF || decision->unk04 != 0xFF
                || decision->unk05 != 0
                || !ActionSemantics_IsPickTarget(
                    decision->xMove, decision->yMove, decision->xTarget, decision->yTarget))
                return false;
        }
        else
        {
            if (decision->itemSlot >= UNIT_ITEM_COUNT
                || (gExpansionAutoplayPlannerObservation.state
                        == EXPANSION_AUTOPLAY_PLANNER_STATE_COMMITTED
                    && gActiveUnit->items[decision->itemSlot]
                        != (u16)sPlannerCandidateDigest)
                || !IsPickItemSlotForTarget(
                    gActiveUnit, decision->itemSlot, decision->xMove, decision->yMove,
                    decision->xTarget, decision->yTarget))
                return false;
        }
        gActionData.xOther = decision->xTarget;
        gActionData.yOther = decision->yTarget;
        gActionData.itemSlotIndex = decision->itemSlot;
        return true;
    }
    if (decision->actionId != AI_ACTION_STAFF
        || decision->itemSlot >= UNIT_ITEM_COUNT)
        return decision->actionId != AI_ACTION_STAFF;
    item = gActiveUnit->items[decision->itemSlot];
    if (!CanUnitUseStaff(gActiveUnit, item))
        return false;
    itemId = GetItemIndex(item);
    switch (itemId)
    {
    case ITEM_STAFF_TORCH:
        if (gPlaySt.chapterVisionRange == 0
            || !ActionSemantics_IsStandingReachPosition(
                gActiveUnit, decision->xMove, decision->yMove,
                GetUnitItemUseReachBits(gActiveUnit, decision->itemSlot),
                decision->xTarget, decision->yTarget))
            return false;
        gActionData.xOther = decision->xTarget;
        gActionData.yOther = decision->yTarget;
        return true;

    case ITEM_STAFF_WARP:
        target = GetUnit(decision->targetId);
        if (!IsCanonicalUnitSlot(decision->targetId)
            || !IsStaffTargetLegal(item, target, decision->xMove, decision->yMove)
            || !ActionSemantics_IsWarpDestination(gActiveUnit, target, decision->xMove,
                                                  decision->yMove, decision->xTarget,
                                                  decision->yTarget))
            return false;
        gActionData.xOther = decision->xTarget;
        gActionData.yOther = decision->yTarget;
        return true;

    case ITEM_STAFF_UNLOCK:
        if (!ActionSemantics_IsUnlockStaffTarget(gActiveUnit, decision->xMove, decision->yMove,
                                                 decision->xTarget, decision->yTarget))
            return false;
        gActionData.xOther = decision->xTarget;
        gActionData.yOther = decision->yTarget;
        return true;

    case ITEM_STAFF_REPAIR:
        target = GetUnit(decision->targetId);
        if (!IsCanonicalUnitSlot(decision->targetId)
            || !IsStaffTargetLegal(item, target, decision->xMove, decision->yMove)
            || decision->unk04 >= UNIT_ITEM_COUNT
            || !IsItemHammernable(target->items[decision->unk04]))
            return false;
        gActionData.trapType = decision->unk04;
        return true;

    default:
        return decision->unk04 == 0xFF;
    }
}

enum ExpansionAutoplayPlannerDecisionResult ExpansionAutoplayPlanner_OfferDecision(
    const struct AiDecision* decision)
{
    enum ExpansionAutoplayPlannerEnumerationResult enumerationResult;
    u32 digest = 2166136261u;
    u32 count;

    (void)decision;
    if (!sPlannerActive)
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_FALLBACK;
    if (gExpansionAutoplayPlannerObservation.state
        == EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING)
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;

    enumerationResult =
        ExpansionAutoplayPlanner_EnumerateLegalActions(DigestCandidate, &digest, &count);
    if (enumerationResult
        == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_CAPACITY)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_RESOURCE_LIMIT);
        EndPlannerRun(EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED;
    }
    if (enumerationResult
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_UNAVAILABLE
        || count == 0)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_CAPABILITY_UNAVAILABLE);
        EndPlannerRun(EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED;
    }

    SetCandidateState(count, 0);
    sPlannerCandidateDigest = digest;
    gExpansionAutoplayPlannerObservation.observationId =
        sPlannerNextObservationId++;
    PublishPage(0);
    return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
}

static bool AdvanceDecisionDeadline(void)
{
    u32 waitFrames = WaitFrames() + 1;

    SetCandidateState(CandidateCount(), waitFrames);
    if (waitFrames < EXPANSION_AUTOPLAY_PLANNER_DECISION_TIMEOUT_FRAMES)
        return true;
    Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_TIMEOUT);
    EndPlannerRun(EXPANSION_AUTOPLAY_PLANNER_STATE_CANCELLED);
    return false;
}

enum ExpansionAutoplayPlannerDecisionResult ExpansionAutoplayPlanner_PollDecision(
    struct AiDecision* decision)
{
    struct AiDecision candidate;
    u32 token[4];
    if (!sPlannerActive
        || gExpansionAutoplayPlannerObservation.state
            != EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING)
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_FALLBACK;
    if (!AdvanceDecisionDeadline())
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED;
    if (gExpansionAutoplayPlannerCommand.kind
        == EXPANSION_AUTOPLAY_PLANNER_COMMAND_NONE)
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    if (gExpansionAutoplayPlannerCommand.kind
        == EXPANSION_AUTOPLAY_PLANNER_COMMAND_START)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }
    if (gExpansionAutoplayPlannerCommand.kind
        == EXPANSION_AUTOPLAY_PLANNER_COMMAND_CANCEL)
    {
        if (!IsCommandHeaderValid()
            || gExpansionAutoplayPlannerCommand.runId != sPlannerRunId
            || gExpansionAutoplayPlannerCommand.observationId
                != gExpansionAutoplayPlannerObservation.observationId)
        {
            Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_STALE_OBSERVATION);
            return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
        }

        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_CANCELLED);
        EndPlannerRun(EXPANSION_AUTOPLAY_PLANNER_STATE_CANCELLED);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED;
    }
    if (gExpansionAutoplayPlannerCommand.kind
        == EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE)
    {
        if (!IsCommandHeaderValid()
            || gExpansionAutoplayPlannerCommand.runId != sPlannerRunId
            || gExpansionAutoplayPlannerCommand.observationId
                != gExpansionAutoplayPlannerObservation.observationId)
        {
            Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_STALE_OBSERVATION);
            return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
        }
        if (!CandidateSetUnchanged())
        {
            Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL);
            return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
        }
        if (!PublishPage(gExpansionAutoplayPlannerCommand.pageIndex))
        {
            Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_UNKNOWN_ACTION);
            return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
        }
        gExpansionAutoplayPlannerCommand.result = 1;
        ClearCommand();
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }
    if (gExpansionAutoplayPlannerCommand.kind
        != EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }
    if (!IsCommandHeaderValid())
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }
    if (gExpansionAutoplayPlannerCommand.runId != sPlannerRunId
        || gExpansionAutoplayPlannerCommand.observationId
            != gExpansionAutoplayPlannerObservation.observationId)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_STALE_OBSERVATION);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }
    if (gExpansionAutoplayPlannerCommand.actionOrdinal >= CandidateCount())
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_UNKNOWN_ACTION);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }
    if (!CandidateSetUnchanged())
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }
    if (!GetCandidate(gExpansionAutoplayPlannerCommand.actionOrdinal, &candidate))
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }
    if (sPlannerCommitCount
        >= EXPANSION_AUTOPLAY_PLANNER_TRACE_ACTION_CAPACITY)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_RESOURCE_LIMIT);
        EndPlannerRun(EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED;
    }
    MakeToken(&candidate, gExpansionAutoplayPlannerObservation.observationId,
              gExpansionAutoplayPlannerCommand.actionOrdinal, token);
    if (gExpansionAutoplayPlannerCommand.payload.commit.token0 != token[0]
        || gExpansionAutoplayPlannerCommand.payload.commit.token1 != token[1]
        || gExpansionAutoplayPlannerCommand.payload.commit.token2 != token[2]
        || gExpansionAutoplayPlannerCommand.payload.commit.token3 != token[3])
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_TOKEN_MISMATCH);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }

    gExpansionAutoplayPlannerObservation.state =
        EXPANSION_AUTOPLAY_PLANNER_STATE_COMMITTED;
    gExpansionAutoplayPlannerObservation.rejection =
        EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE;
    gExpansionAutoplayPlannerCommand.result = 1;
    gExpansionAutoplayPlannerCommand.rejection =
        EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE;
    ClearCommand();
    if (candidate.actionId == AI_ACTION_PICK && candidate.itemSlot < UNIT_ITEM_COUNT)
        sPlannerCandidateDigest = gActiveUnit->items[candidate.itemSlot];
    sPlannerTraceDigest = MixDigest(sPlannerTraceDigest, token[0]);
    sPlannerTraceDigest = MixDigest(sPlannerTraceDigest, token[1]);
    sPlannerTraceDigest = MixDigest(sPlannerTraceDigest, token[2]);
    sPlannerTraceDigest = MixDigest(sPlannerTraceDigest, token[3]);
    sPlannerCommitCount++;
    *decision = candidate;
    return EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED;
}

void ExpansionAutoplayPlanner_RecordCampaignCheckpoint(void)
{
    u16 seeds[3];
    if (!sPlannerActive)
        return;

    StoreRNState(seeds);
    gExpansionAutoplayPlannerCampaignCheckpoint.magic = 0;
    gExpansionAutoplayPlannerCampaignCheckpoint.version =
        EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION;
    gExpansionAutoplayPlannerCampaignCheckpoint.byteSize =
        sizeof(struct ExpansionAutoplayPlannerCampaignCheckpointV2);
    gExpansionAutoplayPlannerCampaignCheckpoint.runId = sPlannerRunId;
    gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex =
        (u8)gPlaySt.chapterIndex;
    gExpansionAutoplayPlannerCampaignCheckpoint.chapterMode =
        gPlaySt.chapterModeIndex;
    gExpansionAutoplayPlannerCampaignCheckpoint.chapterTurn =
        gPlaySt.chapterTurnNumber;
    gExpansionAutoplayPlannerCampaignCheckpoint.rngState0 = seeds[0];
    gExpansionAutoplayPlannerCampaignCheckpoint.rngState1 = seeds[1];
    gExpansionAutoplayPlannerCampaignCheckpoint.rngState2 = seeds[2];
    gExpansionAutoplayPlannerCampaignCheckpoint.rngLcg = GetLCGRNValue();
    gExpansionAutoplayPlannerCampaignCheckpoint.rngConsumption =
        GetRNConsumptionCount();
    gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest =
        SemanticStateDigest();
    PLANNER_PUBLISH_BARRIER();
    gExpansionAutoplayPlannerCampaignCheckpoint.magic =
        EXPANSION_AUTOPLAY_PLANNER_MAGIC;
}

#endif
