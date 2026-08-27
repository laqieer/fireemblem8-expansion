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
#include "expansion_chapter_objectives.h"
#include "expansion_config.h"

typedef char ExpansionAutoplayPlannerObservationSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerObservationV2) == 996 ? 1 : -1];
typedef char ExpansionAutoplayPlannerCommandSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerCommandV2) == 64 ? 1 : -1];
typedef char ExpansionAutoplayPlannerCheckpointSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerCampaignCheckpointV2) == 52 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPointerFreeActionCheck[
    sizeof(struct ExpansionAutoplayPlannerActionV2) == 40 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPointerFreeSemanticCheck[
    sizeof(struct ExpansionAutoplayPlannerSemanticFieldV2) == 8 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPointerFreeUnitCheck[
    sizeof(struct ExpansionAutoplayPlannerUnitV2) == 16 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPointerFreeValueCheck[
    sizeof(struct ExpansionAutoplayPlannerValueRecordV2) == 8 ? 1 : -1];
typedef char ExpansionAutoplayPlannerRecordStartSizeCheck[
    sizeof(union ExpansionAutoplayPlannerRecordStartV2) == 4 ? 1 : -1];
typedef char ExpansionAutoplayPlannerRecordCountSizeCheck[
    sizeof(union ExpansionAutoplayPlannerRecordCountV2) == 4 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPayloadSizeCheck[
    sizeof(union ExpansionAutoplayPlannerPayloadV2) == 896 ? 1 : -1];
typedef char ExpansionAutoplayPlannerRecordStartOffsetCheck[
    offsetof(struct ExpansionAutoplayPlannerObservationV2, start) == 36 ? 1 : -1];
typedef char ExpansionAutoplayPlannerRecordCountOffsetCheck[
    offsetof(struct ExpansionAutoplayPlannerObservationV2, count) == 40 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPayloadOffsetCheck[
    offsetof(struct ExpansionAutoplayPlannerObservationV2, payload) == 100 ? 1 : -1];
typedef char ExpansionAutoplayPlannerChapterModeOffsetCheck[
    offsetof(
        struct ExpansionAutoplayPlannerCampaignCheckpointV2,
        chapterMode) == 20 ? 1 : -1];
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
    u32 digest = MixDigest(
        2166136261u,
        FE8_EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID);

    digest = MixDigest(digest, (u8)gPlaySt.chapterIndex);
    digest = MixDigest(
        digest,
        (u16)gBmMapSize.x | ((u32)(u16)gBmMapSize.y << 16));
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

static u32 MakeTokenWord(
    const struct AiDecision* decision,
    u32 observationId,
    u32 ordinal,
    u32 domain)
{
    u32 digest = MixDigest(2166136261u, domain);

    digest = MixDigest(digest, sPlannerRunId);
    digest = MixDigest(digest, observationId);
    digest = MixDigest(digest, ordinal);
    digest = MixDigest(
        digest,
        decision->unitId
            | ((u32)decision->actionId << 8)
            | ((u32)decision->targetId << 16)
            | ((u32)decision->itemSlot << 24));
    digest = MixDigest(
        digest,
        (u32)(u16)decision->xMove | ((u32)(u16)decision->yMove << 16));
    digest = MixDigest(
        digest,
        decision->xTarget
            | ((u32)decision->yTarget << 8)
            | ((u32)decision->unk04 << 16));
    return digest;
}

static void MakeToken(
    const struct AiDecision* decision,
    u32 observationId,
    u32 ordinal,
    u32* token)
{
    token[0] = MakeTokenWord(
        decision,
        observationId,
        ordinal,
        0x243F6A88u);
    token[1] = MakeTokenWord(
        decision,
        observationId,
        ordinal,
        0x85A308D3u);
    token[2] = MakeTokenWord(
        decision,
        observationId,
        ordinal,
        0x13198A2Eu);
    token[3] = MakeTokenWord(
        decision,
        observationId,
        ordinal,
        0x03707344u);
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

static enum ExpansionAutoplayPlannerAvailability GetUnitAvailability(
    const struct Unit* unit)
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

static void MakeDecision(
    struct AiDecision* decision,
    int xMove,
    int yMove,
    u8 actionId,
    u8 targetId,
    u8 itemSlot,
    u8 xTarget,
    u8 yTarget)
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
    struct PlannerEnumeration* enumeration,
    const struct AiDecision* decision)
{
    if (enumeration->count >= EXPANSION_AUTOPLAY_PLANNER_TOTAL_ACTION_CAPACITY)
        return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_CAPACITY;

    if (enumeration->visitor != NULL
        && !enumeration->visitor(
            enumeration->count,
            decision,
            enumeration->context))
    {
        enumeration->stopped = true;
    }
    enumeration->count++;
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static bool IsCombatTargetLegal(
    const struct Unit* target,
    int xMove,
    int yMove,
    int item)
{
    int distance;

    if (!IsVisibleValidUnit(target)
        || AreUnitsAllied(gActiveUnitId, target->index))
        return false;

    distance = RectDistance(xMove, yMove, target->xPos, target->yPos);
    return distance >= GetItemMinRange(item)
        && distance <= GetItemMaxRange(item);
}

static bool __attribute__((noinline)) IsStaffTargetLegal(
    int item,
    const struct Unit* target,
    int xMove,
    int yMove)
{
    return IsVisibleValidUnit(target)
        && IsUnitInStaffTargetListAt(
            gActiveUnit,
            (struct Unit*)target,
            item,
            xMove,
            yMove);
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
    struct PlannerEnumeration* enumeration,
    int xMove,
    int yMove)
{
    struct AiDecision decision;

    if (xMove == gActiveUnit->xPos && yMove == gActiveUnit->yPos)
        return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
    MakeDecision(
        &decision,
        xMove,
        yMove,
        AI_ACTION_NONE,
        0,
        0xFF,
        0,
        0);
    return EmitDecision(enumeration, &decision);
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateCombat(
    struct PlannerEnumeration* enumeration,
    int xMove,
    int yMove)
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
            MakeDecision(
                &decision,
                xMove,
                yMove,
                AI_ACTION_COMBAT,
                target->index,
                itemSlot,
                0,
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
            if (!IsSnagAttackTargetAt(
                    item,
                    xMove,
                    yMove,
                    trap->xPos,
                    trap->yPos))
                continue;
            MakeDecision(
                &decision,
                xMove,
                yMove,
                AI_ACTION_COMBAT,
                0,
                itemSlot,
                trap->xPos,
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
    struct PlannerEnumeration* enumeration,
    int xMove,
    int yMove,
    int itemSlot,
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

            if (!ActionSemantics_IsWarpDestination(
                    gActiveUnit,
                    target,
                    xMove,
                    yMove,
                    xTarget,
                    yTarget))
                continue;
            MakeDecision(
                &decision,
                xMove,
                yMove,
                AI_ACTION_STAFF,
                target->index,
                itemSlot,
                xTarget,
                yTarget);
            result = EmitDecision(enumeration, &decision);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
        }
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateUnlockTargets(
    struct PlannerEnumeration* enumeration,
    int xMove,
    int yMove,
    int itemSlot)
{
    int yTarget;
    int xTarget;

    for (yTarget = 0; yTarget < gBmMapSize.y; yTarget++)
    {
        for (xTarget = 0; xTarget < gBmMapSize.x; xTarget++)
        {
            struct AiDecision decision;
            enum ExpansionAutoplayPlannerEnumerationResult result;
            if (!ActionSemantics_IsUnlockStaffTarget(
                    gActiveUnit,
                    xMove,
                    yMove,
                    xTarget,
                    yTarget))
                continue;
            MakeDecision(
                &decision,
                xMove,
                yMove,
                AI_ACTION_STAFF,
                0,
                itemSlot,
                xTarget,
                yTarget);
            result = EmitDecision(enumeration, &decision);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
        }
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateTorchTargets(
    struct PlannerEnumeration* enumeration,
    int xMove,
    int yMove,
    int itemSlot)
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

            if (!ActionSemantics_IsStandingReachPosition(
                    gActiveUnit,
                    xMove,
                    yMove,
                    reach,
                    xTarget,
                    yTarget))
                continue;
            MakeDecision(
                &decision,
                xMove,
                yMove,
                AI_ACTION_STAFF,
                0,
                itemSlot,
                xTarget,
                yTarget);
            result = EmitDecision(enumeration, &decision);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
        }
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateHammerneSlots(
    struct PlannerEnumeration* enumeration,
    int xMove,
    int yMove,
    int itemSlot,
    struct Unit* target)
{
    int targetSlot;

    for (targetSlot = 0; targetSlot < UNIT_ITEM_COUNT; targetSlot++)
    {
        struct AiDecision decision;
        enum ExpansionAutoplayPlannerEnumerationResult result;

        if (!IsItemHammernable(target->items[targetSlot]))
            continue;
        MakeDecision(
            &decision,
            xMove,
            yMove,
            AI_ACTION_STAFF,
            target->index,
            itemSlot,
            0,
            0);
        decision.unk04 = targetSlot;
        result = EmitDecision(enumeration, &decision);
        if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
            || enumeration->stopped)
            return result;
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateStaff(
    struct PlannerEnumeration* enumeration,
    int xMove,
    int yMove)
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
                EnumerateTorchTargets(
                    enumeration,
                    xMove,
                    yMove,
                    itemSlot);

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
            MakeDecision(
                &decision,
                xMove,
                yMove,
                AI_ACTION_STAFF,
                0,
                itemSlot,
                0,
                0);
            result = EmitDecision(enumeration, &decision);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
            continue;
        }
        if (itemId == ITEM_STAFF_UNLOCK)
        {
            enum ExpansionAutoplayPlannerEnumerationResult result =
                EnumerateUnlockTargets(
                    enumeration,
                    xMove,
                    yMove,
                    itemSlot);

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
                result = EnumerateHammerneSlots(
                    enumeration,
                    xMove,
                    yMove,
                    itemSlot,
                    target);
            }
            else if (itemId == ITEM_STAFF_WARP)
            {
                result = EnumerateWarpDestinations(
                    enumeration,
                    xMove,
                    yMove,
                    itemSlot,
                    target);
            }
            else
            {
                MakeDecision(
                    &decision,
                    xMove,
                    yMove,
                    AI_ACTION_STAFF,
                    target->index,
                    itemSlot,
                    0,
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
    struct PlannerEnumeration* enumeration,
    int xMove,
    int yMove)
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
        MakeDecision(
            &decision,
            xMove,
            yMove,
            AI_ACTION_USEITEM,
            0,
            itemSlot,
            0,
            0);
        result = EmitDecision(enumeration, &decision);
        if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
            || enumeration->stopped)
            return result;
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static int PickItemSlotForTarget(
    int xMove,
    int yMove,
    int xTarget,
    int yTarget)
{
    int terrain;
    int keySlot;

    if (gActiveUnit->pClassData == NULL
        || xTarget < 0
        || xTarget >= gBmMapSize.x
        || yTarget < 0
        || yTarget >= gBmMapSize.y)
        return -2;
    terrain = gBmMapTerrain[yTarget][xTarget];
    if (gActiveUnit->pClassData->number == CLASS_ROGUE)
        return ActionSemantics_IsPickTarget(
            xMove,
            yMove,
            xTarget,
            yTarget) ? -1 : -2;
    if (!ActionSemantics_IsKeyTarget(
            xMove,
            yMove,
            xTarget,
            yTarget))
        return -2;
    if (terrain == TERRAIN_BRIDGE_14)
        terrain = TERRAIN_DOOR;
    keySlot = GetUnitKeyItemSlotForTerrain(gActiveUnit, terrain);
    if (keySlot < 0)
        return -2;
    if (gBmMapTerrain[yTarget][xTarget] == TERRAIN_BRIDGE_14
        && GetItemIndex(gActiveUnit->items[keySlot]) != ITEM_LOCKPICK)
        return -2;
    return keySlot;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumeratePick(
    struct PlannerEnumeration* enumeration,
    int xMove,
    int yMove)
{
    int yTarget;
    int xTarget;

    for (yTarget = 0; yTarget < gBmMapSize.y; yTarget++)
    {
        for (xTarget = 0; xTarget < gBmMapSize.x; xTarget++)
        {
            struct AiDecision decision;
            enum ExpansionAutoplayPlannerEnumerationResult result;
            int keySlot = PickItemSlotForTarget(
                xMove,
                yMove,
                xTarget,
                yTarget);

            if (keySlot < -1)
                continue;
            MakeDecision(
                &decision,
                xMove,
                yMove,
                AI_ACTION_PICK,
                0,
                keySlot < 0 ? 0xFF : keySlot,
                xTarget,
                yTarget);
            result = EmitDecision(enumeration, &decision);
            if (result != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
                || enumeration->stopped)
                return result;
        }
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

static enum ExpansionAutoplayPlannerEnumerationResult EnumerateSummon(
    struct PlannerEnumeration* enumeration,
    int xMove,
    int yMove,
    bool normalSummonAvailable,
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

                if (!ActionSemantics_IsNormalSummonTarget(
                        gActiveUnit,
                        xMove,
                        yMove,
                        xTarget,
                        yTarget))
                    continue;
                MakeDecision(
                    &decision,
                    xMove,
                    yMove,
                    AI_ACTION_SUMMON,
                    0,
                    0xFF,
                    xTarget,
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

        MakeDecision(
            &decision,
            xMove,
            yMove,
            AI_ACTION_DKSUMMON,
            0,
            0xFF,
            0,
            0);
        return EmitDecision(enumeration, &decision);
    }
    return EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK;
}

enum ExpansionAutoplayPlannerEnumerationResult
ExpansionAutoplayPlanner_EnumerateLegalActions(
    ExpansionAutoplayPlannerActionVisitor visitor,
    void* context,
    u32* countOut)
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

    normalSummonAvailable =
        ActionSemantics_IsNormalSummonAvailable(gActiveUnit, false);
    darkSummonAvailable =
        ActionSemantics_IsDarkSummonAvailable(gActiveUnit);
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
            result = EnumerateSummon(
                &enumeration,
                xMove,
                yMove,
                normalSummonAvailable,
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

static bool FindCandidate(
    u32 ordinal,
    const struct AiDecision* decision,
    void* context)
{
    struct PlannerCandidateLookup* lookup = context;

    if (ordinal != lookup->requested)
        return true;
    *lookup->output = *decision;
    lookup->found = true;
    return false;
}

static bool DigestCandidate(
    u32 ordinal,
    const struct AiDecision* decision,
    void* context)
{
    u32* digest = context;

    *digest = MixDigest(*digest, ordinal);
    *digest = MixDigest(*digest, decision->unitId);
    *digest = MixDigest(
        *digest,
        decision->xMove | ((u32)decision->yMove << 8));
    *digest = MixDigest(*digest, decision->actionId);
    *digest = MixDigest(
        *digest,
        decision->targetId | ((u32)decision->itemSlot << 8));
    *digest = MixDigest(
        *digest,
        decision->xTarget | ((u32)decision->yTarget << 8));
    *digest = MixDigest(*digest, decision->unk04);
    return true;
}

static bool CandidateSetUnchanged(void)
{
    enum ExpansionAutoplayPlannerEnumerationResult result;
    u32 digest = 2166136261u;
    u32 count;

    result = ExpansionAutoplayPlanner_EnumerateLegalActions(
        DigestCandidate,
        &digest,
        &count);
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
    result = ExpansionAutoplayPlanner_EnumerateLegalActions(
        FindCandidate,
        &lookup,
        NULL);
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
    return PageCountFor(
        MapRecordCount(),
        EXPANSION_AUTOPLAY_PLANNER_MAP_RECORD_CAPACITY);
}

static u32 UnitPageCount(void)
{
    return PageCountFor(
        UnitRecordCount(),
        EXPANSION_AUTOPLAY_PLANNER_UNIT_RECORD_CAPACITY);
}

static u32 InventoryRecordCount(void)
{
    return UnitRecordCount() * UNIT_ITEM_COUNT;
}

static u32 InventoryPageCount(void)
{
    return PageCountFor(
        InventoryRecordCount(),
        EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY);
}

static u32 ResourceRecordCount(void)
{
    return 1
        + CONVOY_ITEM_COUNT
        + sizeof(gExpansionAutoplayTelemetry) / sizeof(u32);
}

static u32 ResourcePageCount(void)
{
    return PageCountFor(
        ResourceRecordCount(),
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
    return PermanentFlagRecordCount()
        + SafeFlagByteCount(GetChapterFlagBitsSize()) * 8;
}

static u32 FlagPageCount(void)
{
    return PageCountFor(
        FlagRecordCount(),
        EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY);
}

static u32 ActionPageCount(void)
{
    return PageCountFor(
        CandidateCount(),
        EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY);
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

static u32 FlagsDigest(void)
{
    u32 digest = 2166136261u;
    u8* flags;
    int size;
    int index;

    flags = GetPermanentFlagBits();
    size = GetPermanentFlagBitsSize();
    if (flags == NULL || size < 0 || size > PLANNER_FLAG_BYTE_CAPACITY)
        return 0;
    for (index = 0; index < size; index++)
        digest = MixDigest(digest, flags[index]);
    flags = GetChapterFlagBits();
    size = GetChapterFlagBitsSize();
    if (flags == NULL || size < 0 || size > PLANNER_FLAG_BYTE_CAPACITY)
        return 0;
    for (index = 0; index < size; index++)
        digest = MixDigest(digest, flags[index]);
    return digest;
}

static u32 InventoryDigest(const struct Unit* unit)
{
    u32 digest = 2166136261u;
    int item;

    for (item = 0; item < UNIT_ITEM_COUNT; item++)
        digest = MixDigest(digest, unit->items[item]);
    return digest;
}

static u32 ConvoyDigest(void)
{
    u16* convoy = GetConvoyItemArray();
    u32 digest = 2166136261u;
    int index;

    if (convoy == NULL)
        return 0;
    for (index = 0; index < CONVOY_ITEM_COUNT; index++)
        digest = MixDigest(digest, convoy[index]);
    return digest;
}

static void SetSemanticField(
    int index,
    enum ExpansionAutoplayPlannerSemanticFieldId id,
    enum ExpansionAutoplayPlannerAvailability availability,
    u32 value)
{
    struct ExpansionAutoplayPlannerSemanticFieldV2* field =
        &gExpansionAutoplayPlannerObservation.payload.fields[index];

    field->id = id;
    field->availability = availability;
    field->valueSize = sizeof(value);
    field->value = availability == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
        ? value : 0;
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
    flagDigest = FlagsDigest();
    if (flagDigest == 0)
        flagAvailability = EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED;
    convoyDigest = ConvoyDigest();
    if (convoyDigest == 0)
        resourceAvailability = EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED;

    SetSemanticField(
        0,
        EXPANSION_AUTOPLAY_PLANNER_FIELD_MAP_DIMENSIONS,
        mapAvailability,
        (u16)gBmMapSize.x | ((u32)(u16)gBmMapSize.y << 16));
    SetSemanticField(
        1,
        EXPANSION_AUTOPLAY_PLANNER_FIELD_MAP_STATE_DIGEST,
        mapAvailability,
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
    SetSemanticField(
        5,
        EXPANSION_AUTOPLAY_PLANNER_FIELD_OBJECTIVE_STATE,
        objectiveAvailability,
        objectiveState);
    SetSemanticField(
        6,
        EXPANSION_AUTOPLAY_PLANNER_FIELD_FLAGS_DIGEST,
        flagAvailability,
        flagDigest);
    SetSemanticField(
        7,
        EXPANSION_AUTOPLAY_PLANNER_FIELD_RESOURCE_DIGEST,
        resourceAvailability,
        MixDigest(
            MixDigest(2166136261u, gPlaySt.partyGoldAmount),
            convoyDigest));
    gExpansionAutoplayPlannerObservation.start.recordStart = 0;
    gExpansionAutoplayPlannerObservation.count.recordCount =
        EXPANSION_AUTOPLAY_PLANNER_SEMANTIC_FIELD_CAPACITY;
    gExpansionAutoplayPlannerObservation.totalRecordCount =
        EXPANSION_AUTOPLAY_PLANNER_SEMANTIC_FIELD_CAPACITY;
}

static void PublishMapPage(u32 mapPage)
{
    u32 start =
        mapPage * EXPANSION_AUTOPLAY_PLANNER_MAP_RECORD_CAPACITY;
    u32 total = MapRecordCount();
    u32 remaining = total - start;
    u32 count = remaining < EXPANSION_AUTOPLAY_PLANNER_MAP_RECORD_CAPACITY
        ? remaining : EXPANSION_AUTOPLAY_PLANNER_MAP_RECORD_CAPACITY;
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
    u32 start =
        unitPage * EXPANSION_AUTOPLAY_PLANNER_UNIT_RECORD_CAPACITY;
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

        record->identity = (u8)unit->index | ((u32)availability << 24);
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
        }
    }
    gExpansionAutoplayPlannerObservation.start.recordStart = start;
    gExpansionAutoplayPlannerObservation.count.recordCount = count;
    gExpansionAutoplayPlannerObservation.totalRecordCount = total;
}

static u32 __attribute__((noinline)) ValueRecordIdentity(
    enum ExpansionAutoplayPlannerValueKind kind,
    u32 index,
    enum ExpansionAutoplayPlannerAvailability availability)
{
    return kind
        | ((index & 0xFFFF) << 8)
        | ((u32)availability << 24);
}

static void PublishInventoryPage(u32 inventoryPage)
{
    u32 start =
        inventoryPage * EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY;
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
        record->identity = ValueRecordIdentity(
            EXPANSION_AUTOPLAY_PLANNER_VALUE_UNIT_ITEM,
            (u8)unit->index | ((u32)itemSlot << 8),
            availability);
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
    u32 start =
        resourcePage * EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY;
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
            record->identity = ValueRecordIdentity(
                EXPANSION_AUTOPLAY_PLANNER_VALUE_GOLD,
                0,
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
                EXPANSION_AUTOPLAY_PLANNER_VALUE_CONVOY_ITEM,
                convoySlot,
                availability);
            record->value = (u16)item;
        }
        else
        {
            int telemetryIndex =
                ordinal - 1 - CONVOY_ITEM_COUNT;

            record->identity = ValueRecordIdentity(
                EXPANSION_AUTOPLAY_PLANNER_VALUE_AUTOPLAY_TELEMETRY,
                telemetryIndex,
                EXPANSION_AUTOPLAY_PLANNER_AVAILABLE);
            record->value = telemetry[telemetryIndex];
        }
    }
    gExpansionAutoplayPlannerObservation.start.recordStart = start;
    gExpansionAutoplayPlannerObservation.count.recordCount = count;
    gExpansionAutoplayPlannerObservation.totalRecordCount = total;
}

static void PublishFlagPage(u32 flagPage)
{
    u32 start =
        flagPage * EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY;
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

static bool CollectPageAction(
    u32 ordinal,
    const struct AiDecision* decision,
    void* context)
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
    MakeToken(
        decision,
        gExpansionAutoplayPlannerObservation.observationId,
        ordinal,
        token);
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
    u32 start =
        actionPage * EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY;
    u32 remaining = CandidateCount() - start;
    struct PlannerPageCollector collector;

    collector.start = start;
    collector.count =
        remaining < EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY
            ? remaining
            : EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectPageAction,
        &collector,
        NULL);
    gExpansionAutoplayPlannerObservation.start.recordStart = start;
    gExpansionAutoplayPlannerObservation.count.recordCount = collector.count;
    gExpansionAutoplayPlannerObservation.totalRecordCount = CandidateCount();
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
    gExpansionAutoplayPlannerObservation.magic =
        EXPANSION_AUTOPLAY_PLANNER_MAGIC;
    gExpansionAutoplayPlannerObservation.version =
        EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION;
    gExpansionAutoplayPlannerObservation.byteSize =
        sizeof(struct ExpansionAutoplayPlannerObservationV2);
    gExpansionAutoplayPlannerObservation.runId = sPlannerRunId;
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
    gExpansionAutoplayPlannerObservation.actualRomIdentity =
        ActualRomIdentity();
    gExpansionAutoplayPlannerObservation.actualConfigIdentity =
        ActualConfigIdentity();
    gExpansionAutoplayPlannerObservation.actualScenarioIdentity =
        ActualScenarioIdentity();
    gExpansionAutoplayPlannerObservation.actualSeedIdentity =
        ActualSeedIdentity();

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
    u16* convoy;
    u8* flags;
    int size;
    int index;

    digest = MixDigest(digest, (u8)gPlaySt.chapterIndex);
    digest = MixDigest(digest, gPlaySt.chapterModeIndex);
    digest = MixDigest(digest, gPlaySt.chapterTurnNumber);
    digest = MixDigest(digest, gPlaySt.partyGoldAmount);
#if FE8_CHAPTER_OBJECTIVES_ENABLED
    digest = MixDigest(
        digest,
        gExpansionChapterObjectiveTelemetry.objectiveId);
    digest = MixDigest(
        digest,
        gExpansionChapterObjectiveTelemetry.state);
    digest = MixDigest(
        digest,
        gExpansionChapterObjectiveTelemetry.progress);
    digest = MixDigest(
        digest,
        gExpansionChapterObjectiveTelemetry.activeCount);
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
    flags = GetPermanentFlagBits();
    size = GetPermanentFlagBitsSize();
    for (index = 0; flags != NULL && index < size; index++)
        digest = MixDigest(digest, flags[index]);
    flags = GetChapterFlagBits();
    size = GetChapterFlagBitsSize();
    for (index = 0; flags != NULL && index < size; index++)
        digest = MixDigest(digest, flags[index]);
    return MixDigest(digest, sPlannerTraceDigest);
}

static void PublishReadyState(void)
{
    u32 rejection = gExpansionAutoplayPlannerObservation.rejection;

    ClearObservation();
    gExpansionAutoplayPlannerObservation.magic =
        EXPANSION_AUTOPLAY_PLANNER_MAGIC;
    gExpansionAutoplayPlannerObservation.version =
        EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION;
    gExpansionAutoplayPlannerObservation.byteSize =
        sizeof(struct ExpansionAutoplayPlannerObservationV2);
    gExpansionAutoplayPlannerObservation.runId = sPlannerRunId;
    gExpansionAutoplayPlannerObservation.pageCount = 1;
    gExpansionAutoplayPlannerObservation.actualRomIdentity =
        ActualRomIdentity();
    gExpansionAutoplayPlannerObservation.actualConfigIdentity =
        ActualConfigIdentity();
    gExpansionAutoplayPlannerObservation.actualScenarioIdentity =
        ActualScenarioIdentity();
    gExpansionAutoplayPlannerObservation.actualSeedIdentity =
        ActualSeedIdentity();
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

bool ExpansionAutoplayPlanner_PrepareActionData(
    const struct AiDecision* decision)
{
    struct Unit* target;
    int item;
    int itemId;
    int expectedKeySlot;

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
                && IsPositionMagicSealed(
                    decision->xMove,
                    decision->yMove))
            && IsSnagAttackTargetAt(
                item,
                decision->xMove,
                decision->yMove,
                decision->xTarget,
                decision->yTarget);
    }

    if (decision->actionId == AI_ACTION_SUMMON)
    {
        if (decision->targetId != 0
            || decision->itemSlot != 0xFF
            || decision->unk04 != 0xFF
            || !ActionSemantics_IsNormalSummonAvailable(
                gActiveUnit,
                false)
            || !ActionSemantics_IsNormalSummonTarget(
                gActiveUnit,
                decision->xMove,
                decision->yMove,
                decision->xTarget,
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
        expectedKeySlot = PickItemSlotForTarget(
            decision->xMove,
            decision->yMove,
            decision->xTarget,
            decision->yTarget);
        if (expectedKeySlot < -1
            || decision->itemSlot
                != (expectedKeySlot < 0 ? 0xFF : expectedKeySlot))
            return false;
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
                gActiveUnit,
                decision->xMove,
                decision->yMove,
                GetUnitItemUseReachBits(gActiveUnit, decision->itemSlot),
                decision->xTarget,
                decision->yTarget))
            return false;
        gActionData.xOther = decision->xTarget;
        gActionData.yOther = decision->yTarget;
        return true;

    case ITEM_STAFF_WARP:
        target = GetUnit(decision->targetId);
        if (!IsCanonicalUnitSlot(decision->targetId)
            || !IsStaffTargetLegal(
                item,
                target,
                decision->xMove,
                decision->yMove)
            || !ActionSemantics_IsWarpDestination(
                gActiveUnit,
                target,
                decision->xMove,
                decision->yMove,
                decision->xTarget,
                decision->yTarget))
            return false;
        gActionData.xOther = decision->xTarget;
        gActionData.yOther = decision->yTarget;
        return true;

    case ITEM_STAFF_UNLOCK:
        if (!ActionSemantics_IsUnlockStaffTarget(
                gActiveUnit,
                decision->xMove,
                decision->yMove,
                decision->xTarget,
                decision->yTarget))
            return false;
        gActionData.xOther = decision->xTarget;
        gActionData.yOther = decision->yTarget;
        return true;

    case ITEM_STAFF_REPAIR:
        target = GetUnit(decision->targetId);
        if (!IsCanonicalUnitSlot(decision->targetId)
            || !IsStaffTargetLegal(
                item,
                target,
                decision->xMove,
                decision->yMove)
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

    enumerationResult = ExpansionAutoplayPlanner_EnumerateLegalActions(
        DigestCandidate,
        &digest,
        &count);
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
        Reject(
            EXPANSION_AUTOPLAY_PLANNER_REJECTION_CAPABILITY_UNAVAILABLE);
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
            Reject(
                EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL);
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
        Reject(
            EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }
    if (!GetCandidate(
            gExpansionAutoplayPlannerCommand.actionOrdinal,
            &candidate))
    {
        Reject(
            EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }
    if (sPlannerCommitCount
        >= EXPANSION_AUTOPLAY_PLANNER_TRACE_ACTION_CAPACITY)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_RESOURCE_LIMIT);
        EndPlannerRun(EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED;
    }
    MakeToken(
        &candidate,
        gExpansionAutoplayPlannerObservation.observationId,
        gExpansionAutoplayPlannerCommand.actionOrdinal,
        token);
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
