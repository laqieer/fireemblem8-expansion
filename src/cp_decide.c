
#include "global.h"

#include "proc.h"
#include "bmunit.h"
#include "bmmap.h"
#include "bmtrick.h"
#include "bmarch.h"
#include "bmudisp.h"
#include "cp_utility.h"
#include "cp_script.h"
#include "bmsave.h"
#include "bmmind.h"
#include "constants/classes.h"

#include "cp_common.h"
#ifndef FE8_ARCHIVAL_BUILD
#include "expansion_autoplay_internal.h"
#include "expansion_autoplay_strategies.h"
#if FE8_EXPANSION_AUTOPLAY_PLANNER && FE8_EXPANSION_DEBUG
#include "expansion_autoplay_planner.h"
#endif
#endif

static void CpDecide_Suspend(ProcPtr proc);
static void CpDecide_Main(ProcPtr proc);
#if !defined(FE8_ARCHIVAL_BUILD) && FE8_EXPANSION_AUTOPLAY_PLANNER && FE8_EXPANSION_DEBUG
static void CpDecide_PollPlanner(ProcPtr proc);
#endif
static void CpDecide_CompleteDecision(ProcPtr proc);

static void DecideHealOrEscape(void);
static void DecideScriptA(void);
static void DecideScriptB(void);
static void DecideSpecialItems(void);

EWRAM_DATA struct AiDecision gAiDecision = {0};

typedef void(*DecideFunc)(void);

static DecideFunc CONST_DATA sDecideFuncList[] =
{
    DecideHealOrEscape,
    DecideScriptA,
    DecideScriptB,
    DecideSpecialItems,
    NULL, NULL,
};

static DecideFunc CONST_DATA sUnused_CpDecide_0[] =
{
    DecideSpecialItems,
    DecideScriptA,
    DecideHealOrEscape,
    DecideScriptB,
    NULL, NULL,
};

struct ProcCmd CONST_DATA gProcScr_CpDecide[] =
{
    PROC_NAME("E_CPDECIDE"),

PROC_LABEL(0),
    PROC_CALL(CpDecide_Main),
    PROC_SLEEP(0),

    PROC_CALL(CpDecide_Suspend),

    PROC_GOTO(0),

#if !defined(FE8_ARCHIVAL_BUILD) && FE8_EXPANSION_AUTOPLAY_PLANNER && FE8_EXPANSION_DEBUG
PROC_LABEL(1),
    PROC_REPEAT(CpDecide_PollPlanner),
    PROC_GOTO(2),

PROC_LABEL(2),
    PROC_CALL(CpDecide_CompleteDecision),
    PROC_SLEEP(0),
    PROC_CALL(CpDecide_Suspend),
    PROC_GOTO(0),
#endif

    PROC_END,
};

void CpDecide_Suspend(ProcPtr proc)
{
#ifndef FE8_ARCHIVAL_BUILD
    if (ExpansionAutoplay_IsBlueComputerPhase())
    {
        ExpansionAutoplay_RecordSuspendSuppressed();
        return;
    }
#endif

    if (UNIT_FACTION(gActiveUnit) == FACTION_BLUE)
        gActionData.suspendPointType = SUSPEND_POINT_BSKPHASE;
    else
        gActionData.suspendPointType = SUSPEND_POINT_CPPHASE;

    WriteSuspendSave(SAVE_ID_SUSPEND);
}

void CpDecide_Main(ProcPtr proc)
{
next_unit:
    gAiState.decideState = 0;

    if (*gAiState.unitIt)
    {
        gAiState.unk7C = 0;

        gActiveUnitId = *gAiState.unitIt;
        gActiveUnit = GetUnit(gActiveUnitId);

        if (gActiveUnit->state & (US_DEAD | US_UNSELECTABLE) || !gActiveUnit->pCharacterData)
        {
            gAiState.unitIt++;
            goto next_unit;
        }

        do
        {
            RefreshEntityBmMaps();
            RenderBmMap();
            RefreshUnitSprites();

            AiUpdateNoMoveFlag(gActiveUnit);

            gAiState.combatWeightTableId = (gActiveUnit->ai_config & AI_UNIT_CONFIG_COMBATWEIGHT_MASK) >> AI_UNIT_CONFIG_COMBATWEIGHT_SHIFT;

            gAiState.dangerMapFilled = FALSE;
            AiInitDangerMap();

            AiClearDecision();
#if !defined(FE8_ARCHIVAL_BUILD) && FE8_EXPANSION_AUTOPLAY_PLANNER \
    && FE8_EXPANSION_DEBUG
            if (ExpansionAutoplayPlanner_IsActive())
            {
                AiGenerateUnitMovementMapRespectStay(gActiveUnit);
                switch (ExpansionAutoplayPlanner_OfferDecision(NULL))
                {
                case EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT:
                    Proc_Goto(proc, 1);
                    return;

                case EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED:
                case EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED:
                    Proc_End(proc);
                    return;

                default:
                    break;
                }
            }
            else
#endif
            {
#ifndef FE8_ARCHIVAL_BUILD
#if !defined(FE8_INTERNAL_AUTOPLAY_STRATEGY_ROUTER_ABSENT)
                if (ExpansionAutoplay_IsBlueComputerPhase())
                {
                    enum ExpansionAutoplayStrategyResult strategyResult =
                        ExpansionAutoplayStrategies_TryDecide();

                    if (strategyResult != EXPANSION_AUTOPLAY_STRATEGY_OK
                        && strategyResult != EXPANSION_AUTOPLAY_STRATEGY_FALLBACK)
                    {
                        ExpansionAutoplay_RecordStrategyFailure(strategyResult);
                        Proc_End(proc);
                        return;
                    }

                    if (strategyResult == EXPANSION_AUTOPLAY_STRATEGY_FALLBACK)
                        AiDecideMainFunc();
                }
                else
#endif
#endif
                AiDecideMainFunc();
            }
            CpDecide_CompleteDecision(proc);
        } while (0);
    }

    else
    {
        Proc_End(proc);
    }
}

#if !defined(FE8_ARCHIVAL_BUILD) && FE8_EXPANSION_AUTOPLAY_PLANNER && FE8_EXPANSION_DEBUG
static void CpDecide_PollPlanner(ProcPtr proc)
{
    switch (ExpansionAutoplayPlanner_PollDecision(&gAiDecision))
    {
    case EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT:
        return;

    case EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED:
        Proc_Break(proc);
        return;

    default:
        Proc_End(proc);
        return;
    }
}
#endif

static void CpDecide_CompleteDecision(ProcPtr proc)
{
    gActiveUnit->state |= US_HAS_MOVED_AI;

#if FE8_EXPANSION_AUTOPLAY_PLANNER && FE8_EXPANSION_DEBUG
    if (!gAiDecision.actionPerformed ||
        (!ExpansionAutoplayPlanner_IsActive()
            && gActiveUnit->xPos == gAiDecision.xMove
            && gActiveUnit->yPos == gAiDecision.yMove
            && gAiDecision.actionId == AI_ACTION_NONE))
#else
    if (!gAiDecision.actionPerformed ||
        (gActiveUnit->xPos == gAiDecision.xMove && gActiveUnit->yPos == gAiDecision.yMove && gAiDecision.actionId == AI_ACTION_NONE))
#endif
    {
        gAiState.unitIt++;
        Proc_Goto(proc, 0);
        return;
    }

    gAiState.unitIt++;
#ifndef FE8_ARCHIVAL_BUILD
    if (ExpansionAutoplay_IsBlueComputerPhase()
        && !ExpansionAutoplay_IsActionSupported(gAiDecision.actionId))
    {
        gAiDecision.actionPerformed = FALSE;
        Proc_Goto(proc, 0);
        return;
    }
#endif
    Proc_StartBlocking(gProcScr_CpPerform, proc);
}

#if FE8_AUTOPLAY_PLANNER_RUNTIME_TEST
void CpDecide_CompleteDecisionForTest(ProcPtr proc)
{
    CpDecide_CompleteDecision(proc);
}
#endif

void AiClearDecision(void)
{
    gAiDecision.actionId = 0;

    gAiDecision.unitId = 0;
    gAiDecision.xMove = 0;
    gAiDecision.yMove = 0;
    gAiDecision.unk04 = 0;
    gAiDecision.unk05 = 0;
    gAiDecision.targetId = 0;
    gAiDecision.itemSlot = 0;
    gAiDecision.xTarget = 0;
    gAiDecision.yTarget = 0;

    gAiDecision.actionPerformed = FALSE;
}

void AiSetDecision(s16 xMove, s16 yMove, u8 actionId, u8 targetId, u8 itemSlot, u8 xTarget, u8 yTarget)
{
    gAiDecision.unitId = gActiveUnitId;
    gAiDecision.xMove = xMove;
    gAiDecision.yMove = yMove;

    gAiDecision.actionId = actionId;

    gAiDecision.targetId = targetId;
    gAiDecision.itemSlot = itemSlot;
    gAiDecision.xTarget = xTarget;
    gAiDecision.yTarget = yTarget;

    gAiDecision.actionPerformed = TRUE;
}

void AiUpdateDecision(u8 actionId, u8 targetId, u8 itemSlot, u8 xTarget, u8 yTarget)
{
    if (actionId != 0xFF)
        gAiDecision.actionId = actionId;

    if (targetId != 0xFF)
        gAiDecision.targetId = targetId;

    if (itemSlot != 0xFF)
        gAiDecision.itemSlot = itemSlot;

    if (xTarget != 0xFF)
        gAiDecision.xTarget = xTarget;

    if (yTarget != 0xFF)
        gAiDecision.yTarget = yTarget;

    gAiDecision.actionPerformed = TRUE;
}

void AiDecideMain(void)
{
    while (sDecideFuncList[gAiState.decideState] && !gAiDecision.actionPerformed)
    {
        sDecideFuncList[gAiState.decideState++]();
    }
}

void DecideHealOrEscape(void)
{
    if (gAiState.flags & AI_FLAG_BERSERKED)
        return;

    if (AiUpdateGetUnitIsHealing(gActiveUnit) == TRUE)
    {
        struct Vec2 vec2;

        if (AiTryHealSelf() == TRUE)
            return;

        if ((gActiveUnit->aiFlags & AI_UNIT_FLAG_3) && (AiTryMoveTowardsEscape() == TRUE))
        {
            AiTryDanceOrStealAfterMove();
            return;
        }

        if (AiTryGetNearestHealPoint(&vec2) != TRUE)
            return;

        AiTryMoveTowards(vec2.x, vec2.y, 0, 0, 1);

        if (gAiDecision.actionPerformed == TRUE)
            AiTryActionAfterMove();
    }
    else
    {
        if ((gActiveUnit->aiFlags & AI_UNIT_FLAG_3) && (AiTryMoveTowardsEscape() == TRUE))
            AiTryDanceOrStealAfterMove();
    }
}

void DecideSpecialItems(void)
{
    if (gAiState.flags & AI_FLAG_BERSERKED)
        return;

    AiTryDoSpecialItems();
}

void DecideScriptA(void)
{
    int i = 0;

    if (UNIT_IS_GORGON_EGG(gActiveUnit))
        return;

    if (gAiState.flags & AI_FLAG_BERSERKED)
    {
        AiDoBerserkAction();
        return;
    }

    for (i = 0; i < 0x100; ++i)
    {
        if (AiTryExecScriptA() == TRUE)
            return;
    }

    AiExecFallbackScriptA();
}

void DecideScriptB(void)
{
    int i = 0;

    if ((gActiveUnit->state & US_IN_BALLISTA) && (GetRiddenBallistaAt(gActiveUnit->xPos, gActiveUnit->yPos) != NULL))
        return;

    if (gAiState.flags & AI_FLAG_BERSERKED)
    {
        AiDoBerserkMove();
        return;
    }

    for (i = 0; i < 0x100; ++i)
    {
        if (AiTryExecScriptB() == TRUE)
            return;
    }

    AiExecFallbackScriptB();
}
