/*
 * Issue #11 closure -- host-executed behavior test driver for the five
 * bounded validated tools (src/debugtools_tools.c): unit inspect/heal,
 * convoy inspect/add, flag/chapter inspect/toggle, RNG inspect/reseed,
 * and save-state inspect (read-only).
 *
 * Links directly against the real, unmodified src/debugtools_tools.c and
 * src/debugtools_registry.c (compiled for the host, see
 * test_debugtools_registry.py) plus debugtools_tools_host_stubs.c's small
 * set of engine/hardware/menu stand-ins, and drives
 * DebugTools_RegisterExtendedToolActions/DebugTools_GetRegisteredAction/
 * each action's own onSelected and confirm-submenu MenuItemDef::onSelected
 * through the exact same MenuProc/MenuItemProc callback shape the real
 * menu engine uses -- not a reimplementation of any tool's logic.
 *
 * Prints "DEBUGTOOLS_TOOLS_HOST_TEST: PASS" and exits 0 on success; on
 * any failure it prints the specific failing assertion to stderr and
 * exits 1 without running further checks (fail fast, actionable
 * diagnostic).
 */
#include <stdio.h>
#include <string.h>

#include "global.h"
#include "hardware.h"
#include "face.h"
#include "uimenu.h"
#include "bmunit.h"
#include "cp_common.h"
#include "expansion_debugtools.h"
#include "debugtools_internal.h"
#include "save_format.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUGTOOLS_TOOLS_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

#define CLOSE_HUB_FLAGS (MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR)

extern int gDebugToolsToolsHostStub_StartOrphanMenuCallCount;
extern const struct MenuDef* gDebugToolsToolsHostStub_LastMenuDef;
extern int gDebugToolsToolsHostStubPutFaceChibiCallCount;
extern int gDebugToolsToolsHostStubLastFaceChibiId;
extern int gDebugToolsToolsHostStubLastFaceChibiChr;
extern int gDebugToolsToolsHostStubLastFaceChibiPal;
extern int gDebugToolsToolsHostStubLastFaceChibiFlipped;
extern int gDebugToolsToolsHostStubBgSyncCallCount;
extern int gDebugToolsToolsHostStubLastBgSyncMask;
extern int gDebugToolsToolsHostStubStartFace2CallCount;
extern int gDebugToolsToolsHostStubLastStartFaceId;
extern int gDebugToolsToolsHostStubLastEyeControl;
extern int gDebugToolsToolsHostStubFaceMouthInitCount;
extern int gDebugToolsToolsHostStubFaceMouthLoopCount;
extern int gDebugToolsToolsHostStubRefreshEntityMapCount;
extern int gDebugToolsToolsHostStubRenderMapCount;
extern int gDebugToolsToolsHostStubRefreshUnitSpritesCount;
extern int gDebugToolsToolsHostStubUnitCheckStatCapsCount;
extern int gDebugToolsToolsHostStubChangeUnitAiCount;
extern int gDebugToolsToolsHostStubLastAiA;
extern int gDebugToolsToolsHostStubLastAiB;
extern struct KeyStatusBuffer gDebugToolsToolsTestKeyStatus;
extern void DebugToolsHostStub_SetFakeUnit(int present, int curHp, int maxHp);
extern struct Unit* DebugToolsHostStub_GetFakeUnit(void);
extern struct ClassData* DebugToolsHostStub_GetFakeClass(void);
extern void DebugToolsHostStub_SetCursor(int x, int y);
extern void DebugToolsHostStub_MoveFakeUnit(int x, int y);
extern void DebugToolsHostStub_SetUnitEditContext(
    int playerPhaseActive,
    int eventActive,
    int battleEventActive,
    int battleActive);
extern void DebugToolsHostStub_SetFakeConvoy(int count, int full);
extern void DebugToolsHostStub_ClearFakeFlags(void);
extern void DebugToolsHostStub_SetFakeSaveCompatState(enum SaveCompatState state);
extern void DebugToolsHostStub_RunPendingTransition(void);

extern struct MenuDef CONST_DATA gDebugToolsUnitMenuDef;
extern struct MenuDef CONST_DATA gDebugToolsUnitHpMenuDef;
extern struct MenuDef CONST_DATA gDebugToolsUnitStatsMenuDef;
extern struct MenuDef CONST_DATA gDebugToolsUnitAiMenuDef;
extern struct MenuDef CONST_DATA gDebugToolsConvoyMenuDef;
extern struct MenuDef CONST_DATA gDebugToolsFlagMenuDef;
extern struct MenuDef CONST_DATA gDebugToolsRngMenuDef;
extern struct MenuDef CONST_DATA gDebugToolsSaveStateMenuDef;
extern struct MenuDef CONST_DATA gDebugToolsHubMenuDef;

static int ReadUnitStatField(
    const struct Unit* unit,
    enum DebugToolsUnitEditField field)
{
    switch (field)
    {
        case DEBUGTOOLS_UNIT_EDIT_FIELD_MAX_HP:
            return unit->maxHP;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_POWER:
            return unit->pow;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_SKILL:
            return unit->skl;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_SPEED:
            return unit->spd;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_DEFENSE:
            return unit->def;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_RESISTANCE:
            return unit->res;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_LUCK:
            return unit->lck;

        default:
            return -1;
    }
}

int main(void)
{
    const struct DebugToolsAction* action;
    u8 rc;

    /* --- Registration: five actions, ids 5-9, deterministic order. ---- */
    DebugTools_RegisterExtendedToolActions();
    CHECK(DebugTools_GetRegisteredCount() == 5, "expected exactly 5 extended tool actions registered");
    CHECK(DebugTools_OpenHub() == DEBUGTOOLS_OK, "opening the tools-focused hub must succeed");
    CHECK(gDebugToolsToolsHostStub_LastMenuDef == &gDebugToolsHubMenuDef,
          "the initial menu must be the real debug hub");

    action = DebugTools_GetRegisteredAction(0);
    CHECK(action != NULL && action->id == 5 && strcmp(action->label, "Unit Inspect") == 0, "action 0 must be Unit Inspect (id 5)");
    action = DebugTools_GetRegisteredAction(1);
    CHECK(action != NULL && action->id == 6 && strcmp(action->label, "Convoy Inspect") == 0, "action 1 must be Convoy Inspect (id 6)");
    action = DebugTools_GetRegisteredAction(2);
    CHECK(action != NULL && action->id == 7 && strcmp(action->label, "Flag/Chapter") == 0, "action 2 must be Flag/Chapter (id 7)");
    action = DebugTools_GetRegisteredAction(3);
    CHECK(action != NULL && action->id == 8 && strcmp(action->label, "RNG Inspect") == 0, "action 3 must be RNG Inspect (id 8)");
    action = DebugTools_GetRegisteredAction(4);
    CHECK(action != NULL && action->id == 9 && strcmp(action->label, "Save State") == 0, "action 4 must be Save State (id 9)");

    /* Idempotent: a repeat call must not grow the registry. */
    DebugTools_RegisterExtendedToolActions();
    CHECK(DebugTools_GetRegisteredCount() == 5, "a repeat DebugTools_RegisterExtendedToolActions call must not grow the registry");

    /* ================= 1. Cursor-selected unit editor =============== */

    {
        struct MenuItemProc menuItem;
        struct Unit* unit;
        int fieldIndex;
        int refreshBefore;
        int statCapsBefore;

        memset(&menuItem, 0, sizeof(menuItem));

        /* Valid live-map target: inspect is read-only and snapshots
         * canonical slot/character/class/state, not a fixed character ID. */
        DebugToolsHostStub_SetFakeUnit(1, 5, 20);
        action = DebugTools_GetRegisteredAction(0);
        rc = action->onSelected(NULL, NULL);
        CHECK(rc == CLOSE_HUB_FLAGS, "valid cursor Unit Inspect must close the hub");
        CHECK(gDebugToolsToolsHostStub_LastMenuDef == &gDebugToolsHubMenuDef,
              "Unit Inspect must defer its root submenu until the hub ends");
        gDebugToolsHubMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        CHECK(gDebugToolsToolsHostStub_LastMenuDef == &gDebugToolsUnitMenuDef,
              "valid cursor Unit Inspect must open the bounded root menu");
        CHECK(gDebugToolsProbe.unitInspectTargetFound == 1,
              "valid cursor target must set unitInspectTargetFound");
        CHECK(gDebugToolsUnitEditorProbe.unitInspectTargetSlot == 1
              && gDebugToolsUnitEditorProbe.unitInspectLastCharacterNumber == 1
              && gDebugToolsUnitEditorProbe.unitInspectLastClassNumber == 1,
              "inspect must sample canonical unit/character/class identity");
        CHECK(gDebugToolsProbe.unitInspectLastCurHp == 5
              && gDebugToolsProbe.unitInspectLastMaxHp == 20,
              "inspect must sample raw current and authoritative max HP");
        CHECK(gDebugToolsUnitEditorProbe.unitEditLastOutcome
                  == DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED,
              "read-only inspect must record INSPECTED outcome");
        CHECK(gDebugToolsUnitEditorProbe.unitEditTransactionCount == 0
              && gDebugToolsProbe.unitHealTransactionCount == 0,
              "inspect alone must never mutate the unit");
#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
        CHECK(gDebugToolsToolsHostStubPutFaceChibiCallCount == 1,
              "valid Unit Inspect must render exactly one minimug");
        CHECK(gDebugToolsToolsHostStubLastFaceChibiId == 2
              && gDebugToolsToolsHostStubLastFaceChibiChr == 0x280
              && gDebugToolsToolsHostStubLastFaceChibiPal == 2
              && gDebugToolsToolsHostStubLastFaceChibiFlipped == FALSE,
              "portrait-package probe must retain its documented parameters");
        CHECK(gDebugToolsToolsHostStubBgSyncCallCount == 1
              && gDebugToolsToolsHostStubLastBgSyncMask == BG2_SYNC_BIT,
              "minimug rendering must synchronize BG2 exactly once");
        CHECK(gPortraitPackageRuntimeProbe.faceId == 2
              && gPortraitPackageRuntimeProbe.minimugRenderCount == 1,
              "valid Unit Inspect must record minimug evidence");
        CHECK(gPortraitPackageRuntimeProbe.minimugVramWord == 0xE1A2B3C4
              && gPortraitPackageRuntimeProbe.minimugPaletteWord == 0x56781234,
              "minimug probe must sample rendered VRAM and palette state");
        CHECK(gDebugToolsToolsHostStubStartFace2CallCount == 1
              && gDebugToolsToolsHostStubLastStartFaceId == 2
              && gPortraitPackageRuntimeProbe.fullFaceRenderCount == 1,
              "valid Unit Inspect must render the documented full face once");
        CHECK(gDebugToolsToolsHostStubLastEyeControl == 2
              && gPortraitPackageRuntimeProbe.eyeControl == 2,
              "full-face probe must exercise eye control state 2");
        CHECK(gPortraitPackageRuntimeProbe.mouthDisplayBits == FACE_DISP_TALK_1,
              "full-face probe must preserve the requested talk display bit");
        CHECK(gDebugToolsToolsHostStubFaceMouthInitCount == 1
              && gDebugToolsToolsHostStubFaceMouthLoopCount == 2,
              "full-face probe must initialize and render both mouth states");
        CHECK(gPortraitPackageRuntimeProbe.mouthFrame0 != 0
              && gPortraitPackageRuntimeProbe.mouthFrame2 != 0
              && gPortraitPackageRuntimeProbe.mouthFrame0
                  != gPortraitPackageRuntimeProbe.mouthFrame2,
              "mouth probe must record distinct nonzero frame evidence");
#else
        CHECK(gDebugToolsToolsHostStubPutFaceChibiCallCount == 0
              && gDebugToolsToolsHostStubStartFace2CallCount == 0,
              "supported Unit Inspect builds must omit portrait test instrumentation");
#endif
        CHECK(strcmp(gDebugToolsUnitMenuDef.menuItems[0].name,
                     "Confirm Heal to Full") == 0,
              "root item 0 must preserve the explicit heal confirmation");
        CHECK(strcmp(gDebugToolsUnitMenuDef.menuItems[1].name, "Edit HP") == 0
              && strcmp(gDebugToolsUnitMenuDef.menuItems[2].name, "Edit Stats") == 0
              && strcmp(gDebugToolsUnitMenuDef.menuItems[3].name, "Edit AI") == 0,
              "root must expose bounded HP/stat/AI submenus");
        CHECK(strcmp(gDebugToolsUnitMenuDef.menuItems[4].name,
                     "Confirm Clear Status") == 0,
              "root must expose only explicit status clearing");
        CHECK(strcmp(gDebugToolsUnitMenuDef.menuItems[7].name, "Back") == 0,
              "root must end with Back");

        /* Current HP: LEFT changes only the fixed preview; A is a separate
         * confirmation that revalidates then calls SetUnitHp. */
        menuItem.def = &gDebugToolsUnitMenuDef.menuItems[1];
        rc = menuItem.def->onSelected(NULL, &menuItem);
        CHECK(rc == CLOSE_HUB_FLAGS, "Edit HP must defer from root to HP submenu");
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        CHECK(gDebugToolsToolsHostStub_LastMenuDef == &gDebugToolsUnitHpMenuDef,
              "Edit HP must open the owned HP submenu");
        unit = DebugToolsHostStub_GetFakeUnit();
        gDebugToolsToolsTestKeyStatus.repeatedKeys = DPAD_LEFT;
        menuItem.def = &gDebugToolsUnitHpMenuDef.menuItems[0];
        menuItem.def->onIdle(NULL, &menuItem);
        gDebugToolsToolsTestKeyStatus.repeatedKeys = 0;
        CHECK(unit->curHP == 5, "HP preview must not mutate the unit");
        CHECK(gDebugToolsUnitEditorProbe.unitEditLastOldValue == 5
              && gDebugToolsUnitEditorProbe.unitEditLastNewValue == 4
              && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                  == DEBUGTOOLS_UNIT_EDIT_OUTCOME_PREVIEWED,
              "HP preview telemetry must record exact old/new values");
        refreshBefore = gDebugToolsToolsHostStubRefreshEntityMapCount;
        rc = menuItem.def->onSelected(NULL, &menuItem);
        CHECK(rc == CLOSE_HUB_FLAGS, "HP confirmation must close the editor");
        CHECK(unit->curHP == 4, "confirmed HP edit must apply exactly the preview");
        CHECK(gDebugToolsUnitEditorProbe.unitEditTransactionCount == 1
              && gDebugToolsUnitEditorProbe.unitEditLastField
                  == DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP
              && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                  == DEBUGTOOLS_UNIT_EDIT_OUTCOME_APPLIED,
              "confirmed HP edit must record applied field/outcome");
        CHECK(gDebugToolsToolsHostStubRefreshEntityMapCount == refreshBefore + 1
              && gDebugToolsToolsHostStubRenderMapCount == refreshBefore + 1
              && gDebugToolsToolsHostStubRefreshUnitSpritesCount == refreshBefore + 1,
              "confirmed HP edit must run all authoritative map refresh helpers");
        gDebugToolsUnitHpMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();

        /* Heal remains the first explicit confirm and now mutates HP only. */
        rc = action->onSelected(NULL, NULL);
        CHECK(rc == CLOSE_HUB_FLAGS, "reinspect wounded unit must open root");
        gDebugToolsHubMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        unit->statusIndex = UNIT_STATUS_POISON;
        unit->statusDuration = 3;
        refreshBefore = gDebugToolsToolsHostStubRefreshEntityMapCount;
        rc = gDebugToolsUnitMenuDef.menuItems[0].onSelected(NULL, NULL);
        CHECK(rc == CLOSE_HUB_FLAGS, "heal confirmation must close root");
        CHECK(unit->curHP == 20, "heal must set current HP to authoritative max");
        CHECK(unit->statusIndex == UNIT_STATUS_POISON && unit->statusDuration == 3,
              "heal must not hide a second status-clear mutation");
        CHECK(gDebugToolsProbe.unitHealTransactionCount == 1,
              "valid heal confirmation must preserve the legacy transaction counter");
        CHECK(gDebugToolsUnitEditorProbe.unitEditLastOldValue == 4
              && gDebugToolsUnitEditorProbe.unitEditLastNewValue == 20
              && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                  == DEBUGTOOLS_UNIT_EDIT_OUTCOME_APPLIED,
              "heal telemetry must record wounded-to-full values");
        CHECK(gDebugToolsToolsHostStubRefreshEntityMapCount == refreshBefore + 1,
              "wounded heal must refresh the live map");
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();

        /* Every visible raw stat has its own bounded preview and invokes
         * UnitCheckStatCaps on both the checked copy and committed unit. */
        for (fieldIndex = 0; fieldIndex < 7; fieldIndex++)
        {
            enum DebugToolsUnitEditField field =
                DEBUGTOOLS_UNIT_EDIT_FIELD_MAX_HP + fieldIndex;
            int oldValue;

            DebugToolsHostStub_SetFakeUnit(1, 5, 20);
            unit = DebugToolsHostStub_GetFakeUnit();
            oldValue = ReadUnitStatField(unit, field);
            rc = action->onSelected(NULL, NULL);
            CHECK(rc == CLOSE_HUB_FLAGS, "stat iteration inspect must open root");
            gDebugToolsHubMenuDef.onEnd(NULL);
            DebugToolsHostStub_RunPendingTransition();
            menuItem.def = &gDebugToolsUnitMenuDef.menuItems[2];
            rc = menuItem.def->onSelected(NULL, &menuItem);
            CHECK(rc == CLOSE_HUB_FLAGS, "Edit Stats must defer to stat submenu");
            gDebugToolsUnitMenuDef.onEnd(NULL);
            DebugToolsHostStub_RunPendingTransition();
            CHECK(gDebugToolsToolsHostStub_LastMenuDef
                      == &gDebugToolsUnitStatsMenuDef,
                  "Edit Stats must open the bounded stat submenu");

            menuItem.def = &gDebugToolsUnitStatsMenuDef.menuItems[fieldIndex];
            gDebugToolsToolsTestKeyStatus.repeatedKeys = DPAD_RIGHT;
            menuItem.def->onIdle(NULL, &menuItem);
            gDebugToolsToolsTestKeyStatus.repeatedKeys = 0;
            statCapsBefore = gDebugToolsToolsHostStubUnitCheckStatCapsCount;
            refreshBefore = gDebugToolsToolsHostStubRefreshEntityMapCount;
            rc = menuItem.def->onSelected(NULL, &menuItem);
            CHECK(rc == CLOSE_HUB_FLAGS, "stat confirmation must close submenu");
            CHECK(ReadUnitStatField(unit, field) == oldValue + 1,
                  "stat confirmation must mutate exactly the selected raw field");
            CHECK(gDebugToolsToolsHostStubUnitCheckStatCapsCount
                      == statCapsBefore + 2,
                  "stat edit must execute UnitCheckStatCaps on preview and commit");
            CHECK(gDebugToolsToolsHostStubRefreshEntityMapCount
                      == refreshBefore + 1,
                  "stat edit must refresh the map exactly once");
            CHECK(gDebugToolsUnitEditorProbe.unitEditLastField == (u32)field
                  && gDebugToolsUnitEditorProbe.unitEditLastOldValue == (u32)oldValue
                  && gDebugToolsUnitEditorProbe.unitEditLastNewValue
                      == (u32)(oldValue + 1)
                  && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                      == DEBUGTOOLS_UNIT_EDIT_OUTCOME_APPLIED,
                  "stat telemetry must identify exact field/old/new/outcome");
            gDebugToolsUnitStatsMenuDef.onEnd(NULL);
            DebugToolsHostStub_RunPendingTransition();
        }

        /* AI A/B use only cp_common.h's closed enum ranges and the real
         * ChangeUnitAi contract (including PC reset and AI_B_0C flag). */
        DebugToolsHostStub_SetFakeUnit(1, 5, 20);
        unit = DebugToolsHostStub_GetFakeUnit();
        unit->ai1 = AI_A_13;
        unit->ai_a_pc = 7;
        rc = action->onSelected(NULL, NULL);
        gDebugToolsHubMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitMenuDef.menuItems[3];
        menuItem.def->onSelected(NULL, &menuItem);
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitAiMenuDef.menuItems[0];
        gDebugToolsToolsTestKeyStatus.repeatedKeys = DPAD_RIGHT;
        menuItem.def->onIdle(NULL, &menuItem);
        gDebugToolsToolsTestKeyStatus.repeatedKeys = 0;
        refreshBefore = gDebugToolsToolsHostStubRefreshEntityMapCount;
        menuItem.def->onSelected(NULL, &menuItem);
        CHECK(unit->ai1 == AI_A_14 && unit->ai_a_pc == 0,
              "AI A confirmation must apply a documented enum and reset its PC");
        CHECK(gDebugToolsToolsHostStubLastAiA == AI_A_14
              && gDebugToolsToolsHostStubLastAiB == AI_B_INVALID,
              "AI A edit must preserve AI B through its typed sentinel");
        CHECK(gDebugToolsToolsHostStubRefreshEntityMapCount == refreshBefore + 1,
              "AI A edit must refresh the map");
        gDebugToolsUnitAiMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();

        DebugToolsHostStub_SetFakeUnit(1, 5, 20);
        unit = DebugToolsHostStub_GetFakeUnit();
        unit->ai2 = AI_B_0B;
        unit->ai_b_pc = 9;
        rc = action->onSelected(NULL, NULL);
        gDebugToolsHubMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitMenuDef.menuItems[3];
        menuItem.def->onSelected(NULL, &menuItem);
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitAiMenuDef.menuItems[1];
        gDebugToolsToolsTestKeyStatus.repeatedKeys = DPAD_RIGHT;
        menuItem.def->onIdle(NULL, &menuItem);
        gDebugToolsToolsTestKeyStatus.repeatedKeys = 0;
        menuItem.def->onSelected(NULL, &menuItem);
        CHECK(unit->ai2 == AI_B_0C && unit->ai_b_pc == 0,
              "AI B confirmation must apply a documented enum and reset its PC");
        CHECK(gDebugToolsToolsHostStubLastAiA == AI_A_INVALID
              && gDebugToolsToolsHostStubLastAiB == AI_B_0C,
              "AI B edit must preserve AI A through its typed sentinel");
        CHECK(unit->aiFlags & AI_UNIT_FLAG_3,
              "ChangeUnitAi's documented AI_B_0C side effect must be preserved");
        gDebugToolsUnitAiMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();

        /* Every named temporary status is clearable; Recovery and unnamed
         * Condition slots are disabled and reject direct callback misuse. */
        {
            static const u8 clearableStatuses[] = {
                UNIT_STATUS_POISON,
                UNIT_STATUS_SLEEP,
                UNIT_STATUS_SILENCED,
                UNIT_STATUS_BERSERK,
                UNIT_STATUS_ATTACK,
                UNIT_STATUS_DEFENSE,
                UNIT_STATUS_CRIT,
                UNIT_STATUS_AVOID,
                UNIT_STATUS_SICK,
                UNIT_STATUS_PETRIFY,
            };
            static const u8 unsupportedStatuses[] = {
                UNIT_STATUS_NONE,
                UNIT_STATUS_RECOVER,
                UNIT_STATUS_12,
                UNIT_STATUS_13,
            };
            unsigned statusIndex;

            for (statusIndex = 0;
                 statusIndex < sizeof(clearableStatuses);
                 statusIndex++)
            {
                DebugToolsHostStub_SetFakeUnit(1, 5, 20);
                unit = DebugToolsHostStub_GetFakeUnit();
                unit->statusIndex = clearableStatuses[statusIndex];
                unit->statusDuration = 3;
                action->onSelected(NULL, NULL);
                gDebugToolsHubMenuDef.onEnd(NULL);
                DebugToolsHostStub_RunPendingTransition();
                CHECK(gDebugToolsUnitMenuDef.menuItems[4].isAvailable(
                          &gDebugToolsUnitMenuDef.menuItems[4], 4)
                          == MENU_ENABLED,
                      "named temporary status must enable clear confirmation");
                refreshBefore = gDebugToolsToolsHostStubRefreshEntityMapCount;
                gDebugToolsUnitMenuDef.menuItems[4].onSelected(NULL, NULL);
                CHECK(unit->statusIndex == UNIT_STATUS_NONE
                      && unit->statusDuration == 0,
                      "confirmed named temporary status must clear via SetUnitStatus");
                CHECK(gDebugToolsToolsHostStubRefreshEntityMapCount
                          == refreshBefore + 1,
                      "status clear must refresh the map");
                gDebugToolsUnitMenuDef.onEnd(NULL);
                DebugToolsHostStub_RunPendingTransition();
            }

            for (statusIndex = 0;
                 statusIndex < sizeof(unsupportedStatuses);
                 statusIndex++)
            {
                u32 rejectBefore = gDebugToolsUnitEditorProbe.unitEditRejectCount;

                DebugToolsHostStub_SetFakeUnit(1, 5, 20);
                unit = DebugToolsHostStub_GetFakeUnit();
                unit->statusIndex = unsupportedStatuses[statusIndex];
                unit->statusDuration =
                    unsupportedStatuses[statusIndex] == UNIT_STATUS_NONE ? 0 : 3;
                action->onSelected(NULL, NULL);
                gDebugToolsHubMenuDef.onEnd(NULL);
                DebugToolsHostStub_RunPendingTransition();
                CHECK(gDebugToolsUnitMenuDef.menuItems[4].isAvailable(
                          &gDebugToolsUnitMenuDef.menuItems[4], 4)
                          == MENU_DISABLED,
                      "none/Recovery/unnamed Condition status must stay disabled");
                refreshBefore = gDebugToolsToolsHostStubRefreshEntityMapCount;
                gDebugToolsUnitMenuDef.menuItems[4].onSelected(NULL, NULL);
                CHECK(unit->statusIndex == unsupportedStatuses[statusIndex],
                      "unsupported status callback misuse must not mutate state");
                CHECK(gDebugToolsUnitEditorProbe.unitEditRejectCount
                          == rejectBefore + 1
                      && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                          == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_UNSUPPORTED,
                      "unsupported status must record explicit rejection");
                CHECK(gDebugToolsToolsHostStubRefreshEntityMapCount == refreshBefore,
                      "unsupported status must not refresh or claim mutation");
                gDebugToolsUnitMenuDef.onEnd(NULL);
                DebugToolsHostStub_RunPendingTransition();
            }
        }

        /* Cancel returns through the same owned submenu seam and preserves HP. */
        DebugToolsHostStub_SetFakeUnit(1, 5, 20);
        unit = DebugToolsHostStub_GetFakeUnit();
        action->onSelected(NULL, NULL);
        gDebugToolsHubMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitMenuDef.menuItems[1];
        menuItem.def->onSelected(NULL, &menuItem);
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitHpMenuDef.menuItems[0];
        gDebugToolsToolsTestKeyStatus.repeatedKeys = DPAD_LEFT;
        menuItem.def->onIdle(NULL, &menuItem);
        gDebugToolsToolsTestKeyStatus.repeatedKeys = 0;
        {
            u32 cancelBefore = gDebugToolsUnitEditorProbe.unitEditCancelCount;

            rc = gDebugToolsUnitHpMenuDef.onBPress(NULL, &menuItem);
            CHECK(rc == CLOSE_HUB_FLAGS, "HP B must cancel back to unit root");
            CHECK(unit->curHP == 5, "HP cancel must preserve current HP");
            CHECK(gDebugToolsUnitEditorProbe.unitEditCancelCount == cancelBefore + 1
                  && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                      == DEBUGTOOLS_UNIT_EDIT_OUTCOME_CANCELLED,
                  "HP cancel must record exact cancellation outcome");
        }
        gDebugToolsUnitHpMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        CHECK(gDebugToolsToolsHostStub_LastMenuDef == &gDebugToolsUnitMenuDef,
              "child Back must return through deferred unit root transition");
        gDebugToolsUnitMenuDef.menuItems[7].onSelected(NULL, NULL);
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();

        /* No-change at the lower bound is a confirmed no-op, not a mutation. */
        DebugToolsHostStub_SetFakeUnit(1, 1, 1);
        unit = DebugToolsHostStub_GetFakeUnit();
        action->onSelected(NULL, NULL);
        gDebugToolsHubMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitMenuDef.menuItems[1];
        menuItem.def->onSelected(NULL, &menuItem);
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitHpMenuDef.menuItems[0];
        gDebugToolsToolsTestKeyStatus.repeatedKeys = DPAD_LEFT;
        menuItem.def->onIdle(NULL, &menuItem);
        gDebugToolsToolsTestKeyStatus.repeatedKeys = 0;
        refreshBefore = gDebugToolsToolsHostStubRefreshEntityMapCount;
        menuItem.def->onSelected(NULL, &menuItem);
        CHECK(unit->curHP == 1
              && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                  == DEBUGTOOLS_UNIT_EDIT_OUTCOME_NO_CHANGE,
              "lower-bound confirmation must be a typed no-change");
        CHECK(gDebugToolsToolsHostStubRefreshEntityMapCount == refreshBefore,
              "no-change confirmation must not refresh or count a mutation");
        gDebugToolsUnitHpMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();

        /* Target/value revalidation negative controls. */
        DebugToolsHostStub_SetFakeUnit(1, 5, 20);
        unit = DebugToolsHostStub_GetFakeUnit();
        action->onSelected(NULL, NULL);
        gDebugToolsHubMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitMenuDef.menuItems[1];
        menuItem.def->onSelected(NULL, &menuItem);
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitHpMenuDef.menuItems[0];
        gDebugToolsToolsTestKeyStatus.repeatedKeys = DPAD_RIGHT;
        menuItem.def->onIdle(NULL, &menuItem);
        gDebugToolsToolsTestKeyStatus.repeatedKeys = 0;
        unit->maxHP = 5;
        menuItem.def->onSelected(NULL, &menuItem);
        CHECK(unit->curHP == 5
              && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                  == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_RANGE,
              "cap change before commit must reject the now-out-of-range preview");
        gDebugToolsUnitHpMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();

        DebugToolsHostStub_SetFakeUnit(1, 5, 20);
        unit = DebugToolsHostStub_GetFakeUnit();
        action->onSelected(NULL, NULL);
        gDebugToolsHubMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitMenuDef.menuItems[1];
        menuItem.def->onSelected(NULL, &menuItem);
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitHpMenuDef.menuItems[0];
        gDebugToolsToolsTestKeyStatus.repeatedKeys = DPAD_LEFT;
        menuItem.def->onIdle(NULL, &menuItem);
        gDebugToolsToolsTestKeyStatus.repeatedKeys = 0;
        unit->curHP = 6;
        menuItem.def->onSelected(NULL, &menuItem);
        CHECK(unit->curHP == 6
              && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                  == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_STALE,
              "field drift before commit must reject rather than overwrite");
        gDebugToolsUnitHpMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();

        DebugToolsHostStub_SetFakeUnit(1, 5, 20);
        unit = DebugToolsHostStub_GetFakeUnit();
        action->onSelected(NULL, NULL);
        gDebugToolsHubMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitMenuDef.menuItems[1];
        menuItem.def->onSelected(NULL, &menuItem);
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitHpMenuDef.menuItems[0];
        gDebugToolsToolsTestKeyStatus.repeatedKeys = DPAD_LEFT;
        menuItem.def->onIdle(NULL, &menuItem);
        gDebugToolsToolsTestKeyStatus.repeatedKeys = 0;
        DebugToolsHostStub_MoveFakeUnit(3, 3);
        menuItem.def->onSelected(NULL, &menuItem);
        CHECK(unit->curHP == 5
              && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                  == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_STALE,
              "target movement/disappearance before commit must reject stale target");
        gDebugToolsUnitHpMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();

        DebugToolsHostStub_SetFakeUnit(1, 5, 20);
        unit = DebugToolsHostStub_GetFakeUnit();
        action->onSelected(NULL, NULL);
        gDebugToolsHubMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitMenuDef.menuItems[1];
        menuItem.def->onSelected(NULL, &menuItem);
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitHpMenuDef.menuItems[0];
        gDebugToolsToolsTestKeyStatus.repeatedKeys = DPAD_LEFT;
        menuItem.def->onIdle(NULL, &menuItem);
        gDebugToolsToolsTestKeyStatus.repeatedKeys = 0;
        DebugToolsHostStub_SetUnitEditContext(1, 1, 0, 0);
        menuItem.def->onSelected(NULL, &menuItem);
        CHECK(unit->curHP == 5
              && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                  == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_CONFLICT,
              "event ownership appearing before commit must reject mutation");
        DebugToolsHostStub_SetUnitEditContext(1, 0, 0, 0);
        gDebugToolsUnitHpMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();

        /* Forced teardown has its own explicit outcome and never commits. */
        DebugToolsHostStub_SetFakeUnit(1, 5, 20);
        unit = DebugToolsHostStub_GetFakeUnit();
        action->onSelected(NULL, NULL);
        gDebugToolsHubMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitMenuDef.menuItems[1];
        menuItem.def->onSelected(NULL, &menuItem);
        gDebugToolsUnitMenuDef.onEnd(NULL);
        DebugToolsHostStub_RunPendingTransition();
        menuItem.def = &gDebugToolsUnitHpMenuDef.menuItems[0];
        gDebugToolsToolsTestKeyStatus.repeatedKeys = DPAD_LEFT;
        menuItem.def->onIdle(NULL, &menuItem);
        gDebugToolsToolsTestKeyStatus.repeatedKeys = 0;
        {
            u32 forcedBefore =
                gDebugToolsUnitEditorProbe.unitEditForcedCleanupCount;

            gDebugToolsUnitHpMenuDef.onEnd(NULL);
            CHECK(unit->curHP == 5,
                  "forced submenu teardown must not apply the pending preview");
            CHECK(gDebugToolsUnitEditorProbe.unitEditForcedCleanupCount
                      == forcedBefore + 1
                  && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                      == DEBUGTOOLS_UNIT_EDIT_OUTCOME_FORCED_CLEANUP,
                  "forced teardown must record explicit cleanup telemetry");
        }
        DebugToolsHostStub_RunPendingTransition();
        CHECK(!DebugTools_IsHubActive(),
              "forced teardown must clean the debug session without reopening the hub");

        /* Empty/dead/purple/noncanonical and event/battle contexts fail
         * closed before any submenu is allocated. */
        {
            int menuStartsBefore;
            u32 transactionsBefore =
                gDebugToolsUnitEditorProbe.unitEditTransactionCount;

            DebugToolsHostStub_SetFakeUnit(1, 5, 20);
            DebugToolsHostStub_SetCursor(7, 7);
            menuStartsBefore = gDebugToolsToolsHostStub_StartOrphanMenuCallCount;
            rc = action->onSelected(NULL, NULL);
            CHECK(rc == MENU_ACT_SND6B
                  && gDebugToolsToolsHostStub_StartOrphanMenuCallCount
                      == menuStartsBefore,
                  "empty cursor tile must reject without opening a submenu");
            CHECK(gDebugToolsProbe.unitInspectTargetFound == 0
                  && gDebugToolsUnitEditorProbe.unitEditLastOutcome
                      == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_EMPTY,
                  "empty tile rejection must be explicit telemetry");

            DebugToolsHostStub_SetFakeUnit(1, 5, 20);
            unit = DebugToolsHostStub_GetFakeUnit();
            unit->state = US_DEAD;
            action->onSelected(NULL, NULL);
            CHECK(gDebugToolsUnitEditorProbe.unitEditLastOutcome
                      == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_DEAD,
                  "dead target must fail closed");

            DebugToolsHostStub_SetFakeUnit(1, 5, 20);
            unit = DebugToolsHostStub_GetFakeUnit();
            unit->index = (s8)0xC1;
            DebugToolsHostStub_MoveFakeUnit(2, 3);
            action->onSelected(NULL, NULL);
            CHECK(gDebugToolsUnitEditorProbe.unitEditLastOutcome
                      == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_UNSUPPORTED,
                  "purple/link-arena target must fail closed");

            DebugToolsHostStub_SetFakeUnit(1, 5, 20);
            unit = DebugToolsHostStub_GetFakeUnit();
            unit->pClassData = NULL;
            action->onSelected(NULL, NULL);
            CHECK(gDebugToolsUnitEditorProbe.unitEditLastOutcome
                      == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_INVALID,
                  "noncanonical class target must fail closed");

            DebugToolsHostStub_SetFakeUnit(1, 5, 20);
            DebugToolsHostStub_SetUnitEditContext(0, 0, 0, 0);
            action->onSelected(NULL, NULL);
            CHECK(gDebugToolsUnitEditorProbe.unitEditLastOutcome
                      == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_CONFLICT,
                  "non-live-map context must fail closed");
            DebugToolsHostStub_SetUnitEditContext(1, 0, 1, 0);
            action->onSelected(NULL, NULL);
            CHECK(gDebugToolsUnitEditorProbe.unitEditLastOutcome
                      == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_CONFLICT,
                  "battle-event context must fail closed");
            DebugToolsHostStub_SetUnitEditContext(1, 0, 0, 1);
            action->onSelected(NULL, NULL);
            CHECK(gDebugToolsUnitEditorProbe.unitEditLastOutcome
                      == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_CONFLICT,
                  "active battle daemon must fail closed");
            DebugToolsHostStub_SetUnitEditContext(1, 0, 0, 0);

            CHECK(gDebugToolsUnitEditorProbe.unitEditTransactionCount
                      == transactionsBefore,
                  "all target/conflict negatives must preserve transaction count");
        }

        CHECK(DebugTools_OpenHub() == DEBUGTOOLS_OK,
              "later tool tests must reopen one clean session after forced cleanup");
    }

    /* ================= 2. Convoy inspect/add ========================= */

    DebugToolsHostStub_SetFakeConvoy(3, 0);
    rc = DebugTools_GetRegisteredAction(1)->onSelected(NULL, NULL);
    CHECK(rc == CLOSE_HUB_FLAGS, "Convoy Inspect onSelected must close the hub");
    gDebugToolsHubMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();
    CHECK(gDebugToolsToolsHostStub_LastMenuDef == &gDebugToolsConvoyMenuDef, "Convoy Inspect must open gDebugToolsConvoyMenuDef");
    CHECK(gDebugToolsProbe.convoyLastItemCount == 3, "inspect must sample the convoy item count");
    CHECK(gDebugToolsProbe.convoyAddTransactionCount == 0, "inspect alone must never apply an add transaction");

    rc = gDebugToolsConvoyMenuDef.menuItems[0].onSelected(NULL, NULL);
    CHECK(rc == CLOSE_HUB_FLAGS, "Convoy confirm must close its submenu");
    CHECK(gDebugToolsProbe.convoyAddTransactionCount == 1, "confirming add with room available must apply exactly one transaction");

    gDebugToolsConvoyMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();

    /* --- Full convoy: confirm must be a safe, logged no-op. ----------- */
    DebugToolsHostStub_SetFakeConvoy(100, 1);
    DebugTools_GetRegisteredAction(1)->onSelected(NULL, NULL);
    gDebugToolsHubMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();
    {
        u32 addCountBefore = gDebugToolsProbe.convoyAddTransactionCount;

        rc = gDebugToolsConvoyMenuDef.menuItems[0].onSelected(NULL, NULL);
        CHECK(rc == CLOSE_HUB_FLAGS, "Convoy confirm must still close its submenu when full");
        CHECK(gDebugToolsProbe.convoyAddTransactionCount == addCountBefore, "confirming add on a full convoy must never apply a transaction");
    }

    gDebugToolsConvoyMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();

    /* ================= 3. Flag/chapter/event state =================== */

    DebugToolsHostStub_ClearFakeFlags();
    gPlaySt.chapterIndex = 2;

    rc = DebugTools_GetRegisteredAction(2)->onSelected(NULL, NULL);
    CHECK(rc == CLOSE_HUB_FLAGS, "Flag/Chapter Inspect onSelected must close the hub");
    gDebugToolsHubMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();
    CHECK(gDebugToolsToolsHostStub_LastMenuDef == &gDebugToolsFlagMenuDef, "Flag/Chapter Inspect must open gDebugToolsFlagMenuDef");
    CHECK(gDebugToolsProbe.chapterIndexSample == 2, "inspect must sample gPlaySt.chapterIndex");
    CHECK(gDebugToolsProbe.debugFlagLastValue == 0, "a freshly cleared debug flag must sample as 0");
    CHECK(gDebugToolsProbe.debugFlagToggleCount == 0, "inspect alone must never apply a toggle transaction");

    {
        u32 assertCountBeforeToggle = DebugTools_GetAssertFailureCount();

        rc = gDebugToolsFlagMenuDef.menuItems[0].onSelected(NULL, NULL);
        CHECK(rc == CLOSE_HUB_FLAGS, "Flag confirm must close its submenu");
        CHECK(gDebugToolsProbe.debugFlagToggleCount == 1, "confirming toggle must apply exactly one transaction");
        CHECK(gDebugToolsProbe.debugFlagLastValue == 1, "toggling a cleared flag must set it");
        CHECK(DebugTools_GetAssertFailureCount() == assertCountBeforeToggle,
              "toggling the fixed in-range debug flag must never record a new assert failure");
    }

    rc = gDebugToolsFlagMenuDef.menuItems[0].onSelected(NULL, NULL);
    CHECK(gDebugToolsProbe.debugFlagToggleCount == 2, "a second confirm must apply a second transaction");
    CHECK(gDebugToolsProbe.debugFlagLastValue == 0, "toggling a set flag must clear it back");

    gDebugToolsFlagMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();

    /* ================= 4. RNG inspect/control ========================= */

    rc = DebugTools_GetRegisteredAction(3)->onSelected(NULL, NULL);
    CHECK(rc == CLOSE_HUB_FLAGS, "RNG Inspect onSelected must close the hub");
    gDebugToolsHubMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();
    CHECK(gDebugToolsToolsHostStub_LastMenuDef == &gDebugToolsRngMenuDef, "RNG Inspect must open gDebugToolsRngMenuDef");
    CHECK(gDebugToolsProbe.rngInspectSeedSample0 == 0x1111, "inspect must sample the current seed state (fake initial seed)");
    CHECK(gDebugToolsProbe.rngReseedTransactionCount == 0, "inspect alone must never apply a reseed transaction");

    rc = gDebugToolsRngMenuDef.menuItems[0].onSelected(NULL, NULL);
    CHECK(rc == CLOSE_HUB_FLAGS, "RNG confirm must close its submenu");
    CHECK(gDebugToolsProbe.rngReseedTransactionCount == 1, "confirming reseed must apply exactly one transaction");

    gDebugToolsRngMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();
    DebugTools_GetRegisteredAction(3)->onSelected(NULL, NULL);
    gDebugToolsHubMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();
    CHECK(gDebugToolsProbe.rngInspectSeedSample0 != 0x1111, "a re-inspect after reseeding must observe a changed seed state");

    gDebugToolsRngMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();

    /* ================= 5. Save compatibility/state (read-only) ======== */

    DebugToolsHostStub_SetFakeSaveCompatState(SAVE_COMPAT_CURRENT);
    rc = DebugTools_GetRegisteredAction(4)->onSelected(NULL, NULL);
    CHECK(rc == CLOSE_HUB_FLAGS, "Save State Inspect onSelected must close the hub");
    gDebugToolsHubMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();
    CHECK(gDebugToolsToolsHostStub_LastMenuDef == &gDebugToolsSaveStateMenuDef, "Save State Inspect must open gDebugToolsSaveStateMenuDef");
    CHECK(gDebugToolsProbe.saveCompatLastState == (u32)SAVE_COMPAT_CURRENT, "inspect must sample the current save-compat state");
    CHECK(gDebugToolsProbe.saveCompatInspectCount == 1, "inspect must increment the inspect counter exactly once");

    /* Read-only: no Confirm item at all -- the submenu's only live item
     * must be Back, using the same MenuCancelSelect idiom as every other
     * tool's Back entry. */
    CHECK(strcmp(gDebugToolsSaveStateMenuDef.menuItems[0].name, "Back") == 0, "save-state submenu's only item must be Back");
    CHECK(gDebugToolsSaveStateMenuDef.menuItems[0].onSelected == MenuCancelSelect, "save-state submenu Back must use MenuCancelSelect");

    gDebugToolsSaveStateMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();
    DebugToolsHostStub_SetFakeSaveCompatState(SAVE_COMPAT_MIGRATABLE_OLDER);
    DebugTools_GetRegisteredAction(4)->onSelected(NULL, NULL);
    gDebugToolsHubMenuDef.onEnd(NULL);
    DebugToolsHostStub_RunPendingTransition();
    CHECK(gDebugToolsProbe.saveCompatLastState == (u32)SAVE_COMPAT_MIGRATABLE_OLDER, "a re-inspect must resample a changed save-compat state");
    CHECK(gDebugToolsProbe.saveCompatInspectCount == 2, "a second inspect must increment the counter again");

    printf("DEBUGTOOLS_TOOLS_HOST_TEST: PASS\n");
    return 0;
}
