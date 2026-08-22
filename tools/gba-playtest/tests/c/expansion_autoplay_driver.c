#include "global.h"

#include <stdio.h>

#include "bmunit.h"
#include "cp_common.h"
#include "expansion_autoplay.h"
#include "expansion_autoplay_internal.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "AUTOPLAY_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

static int TestControllerAndLifecycle(void)
{
    const struct ExpansionAutoplayTelemetry* telemetry;

    ExpansionAutoplay_Reset();
    telemetry = ExpansionAutoplay_GetTelemetry();

    CHECK(ExpansionAutoplay_GetBlueControl() == EXPANSION_BLUE_CONTROL_PLAYER,
          "reset must restore PLAYER control");
    CHECK(telemetry->controller == EXPANSION_BLUE_CONTROL_PLAYER,
          "telemetry must identify PLAYER control after reset");
    CHECK(telemetry->state == EXPANSION_AUTOPLAY_STATE_RESET,
          "reset state must be explicit");
    CHECK(telemetry->committedActionCount == 0,
          "reset must clear committed actions");

    CHECK(ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER)
              == EXPANSION_AUTOPLAY_OK,
          "COMPUTER must be accepted");
    ExpansionAutoplay_OnBlueComputerPhaseStart();
    ExpansionAutoplay_RecordEligibleActors(FACTION_BLUE, 3);
    ExpansionAutoplay_RecordRelationCheck(1, 0x81, false);
    ExpansionAutoplay_RecordRelationCheck(1, 0x41, true);
    ExpansionAutoplay_RecordCommittedAction(
        FACTION_BLUE,
        1,
        AI_ACTION_COMBAT,
        0x81,
        EXPANSION_AUTOPLAY_TARGET_HOSTILE);
    ExpansionAutoplay_RecordSuspendSuppressed();
    ExpansionAutoplay_OnBlueComputerPhaseComplete();

    CHECK(telemetry->bluePhaseStartCount == 1,
          "computer phase start must be counted");
    CHECK(telemetry->bluePhaseCompleteCount == 1,
          "computer phase completion must be counted");
    CHECK(telemetry->eligibleActorCount == 3,
          "eligible blue unit count must be recorded");
    CHECK(telemetry->committedActionCount == 1,
          "one committed action must be counted");
    CHECK(telemetry->lastActorSlot == 1
              && telemetry->lastActionId == AI_ACTION_COMBAT
              && telemetry->lastTargetSlot == 0x81
              && telemetry->lastTargetRelation == EXPANSION_AUTOPLAY_TARGET_HOSTILE,
          "last action telemetry must be semantic and complete");
    CHECK(telemetry->hostileTargetCheckCount == 1
              && telemetry->alliedTargetCheckCount == 1
              && telemetry->invalidRecordCount == 0,
          "red hostility and green alliance checks must remain valid");
    CHECK(telemetry->suspendWriteSuppressedCount == 1,
          "transient blue AI must report suppressed suspend writes");
    CHECK(telemetry->state == EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE_COMPLETE
              && telemetry->failure == EXPANSION_AUTOPLAY_FAILURE_NONE,
          "successful phase must terminate without a failure");

    return 0;
}

static int TestValidationAndBounds(void)
{
    enum ExpansionBlueControl previous;

    ExpansionAutoplay_Reset();
    previous = ExpansionAutoplay_GetBlueControl();
    CHECK(ExpansionAutoplay_SetBlueControl((enum ExpansionBlueControl)2)
              == EXPANSION_AUTOPLAY_ERR_INVALID_CONTROL,
          "unknown controller must fail");
    CHECK(ExpansionAutoplay_GetBlueControl() == previous,
          "invalid controller must not change control");
    CHECK(gExpansionAutoplayTelemetry.state == EXPANSION_AUTOPLAY_STATE_FAILURE
              && gExpansionAutoplayTelemetry.failure
                  == EXPANSION_AUTOPLAY_FAILURE_INVALID_CONTROL,
          "invalid controller must leave explicit failure telemetry");
    CHECK(ExpansionAutoplay_SetBlueControl((enum ExpansionBlueControl)-1)
              == EXPANSION_AUTOPLAY_ERR_INVALID_CONTROL,
          "negative controller must fail");
    CHECK(ExpansionAutoplay_GetBlueControl() == previous,
          "negative controller must not change control");

    ExpansionAutoplay_Reset();
#if FE8_EXPANSION_DEBUG
    CHECK(!ExpansionAutoplay_TryActivateScenario(
              SELECT_BUTTON | START_BUTTON,
              SELECT_BUTTON | START_BUTTON),
          "partial debug chord must be inert");
    CHECK(!ExpansionAutoplay_TryActivateScenario(
              EXPANSION_AUTOPLAY_SCENARIO_HOTKEY_MASK,
              EXPANSION_AUTOPLAY_SCENARIO_HOTKEY_MASK | A_BUTTON),
          "debug chord with unrelated keys must be inert");
    CHECK(ExpansionAutoplay_TryActivateScenario(
              EXPANSION_AUTOPLAY_SCENARIO_HOTKEY_MASK,
              EXPANSION_AUTOPLAY_SCENARIO_HOTKEY_MASK),
          "debug scenario activation must be accepted");
    CHECK(ExpansionAutoplay_GetBlueControl() == EXPANSION_BLUE_CONTROL_COMPUTER,
          "debug scenario activation must use the validated controller");
    CHECK(gExpansionAutoplayTelemetry.debugActivationCount == 1,
          "debug scenario activation must be counted");
#else
    CHECK(!ExpansionAutoplay_TryActivateScenario(
              EXPANSION_AUTOPLAY_SCENARIO_HOTKEY_MASK,
              EXPANSION_AUTOPLAY_SCENARIO_HOTKEY_MASK),
          "release scenario activation must be inert");
    CHECK(ExpansionAutoplay_GetBlueControl() == EXPANSION_BLUE_CONTROL_PLAYER,
          "release scenario activation must preserve PLAYER");
#endif
    CHECK(ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER)
              == EXPANSION_AUTOPLAY_OK,
          "typed API must enable COMPUTER in every modern config");

    ExpansionAutoplay_OnBlueComputerPhaseStart();
    CHECK(ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_PLAYER)
              == EXPANSION_AUTOPLAY_ERR_PHASE_ACTIVE,
          "controller changes during a blue computer phase must fail");
    CHECK(ExpansionAutoplay_GetBlueControl() == EXPANSION_BLUE_CONTROL_COMPUTER,
          "active-phase rejection must preserve COMPUTER control");
    CHECK(gExpansionAutoplayTelemetry.failure
              == EXPANSION_AUTOPLAY_FAILURE_INVALID_PHASE,
          "active-phase controller change must be observable");
    ExpansionAutoplay_Reset();
    ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER);
    ExpansionAutoplay_OnBlueComputerPhaseStart();
    ExpansionAutoplay_RecordEligibleActors(
        FACTION_BLUE, EXPANSION_AUTOPLAY_BLUE_ACTOR_CAPACITY + 1);
    CHECK(gExpansionAutoplayTelemetry.failure
              == EXPANSION_AUTOPLAY_FAILURE_ROSTER_CAPACITY,
          "unit-list overflow must fail explicitly");

    ExpansionAutoplay_Reset();
    ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER);
    ExpansionAutoplay_OnBlueComputerPhaseStart();
    CHECK(!ExpansionAutoplay_IsActionSupported(AI_ACTION_ESCAPE),
          "blue escape must be explicitly unsupported");
    CHECK(gExpansionAutoplayTelemetry.failure
              == EXPANSION_AUTOPLAY_FAILURE_UNSUPPORTED_ESCAPE,
          "unsupported escape must be observable");
    CHECK(ExpansionAutoplay_IsBlueComputerPhase(),
          "failure telemetry must not disable blue-phase safety checks");
    ExpansionAutoplay_OnBlueComputerPhaseComplete();
    CHECK(gExpansionAutoplayTelemetry.failure
                  == EXPANSION_AUTOPLAY_FAILURE_UNSUPPORTED_ESCAPE
              && gExpansionAutoplayTelemetry.bluePhaseCompleteCount == 0,
          "phase cleanup must preserve a specific failure");

    ExpansionAutoplay_Reset();
    ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER);
    ExpansionAutoplay_OnBlueComputerPhaseStart();
    gExpansionAutoplayTelemetry.committedActionCount = 0xFFFFFFFFu;
    ExpansionAutoplay_RecordCommittedAction(
        FACTION_BLUE,
        1,
        AI_ACTION_NONE,
        0,
        EXPANSION_AUTOPLAY_TARGET_NONE);
    CHECK(gExpansionAutoplayTelemetry.committedActionCount == 0xFFFFFFFFu,
          "telemetry counters must saturate");

    return 0;
}

static int TestActionCapabilities(void)
{
    int action;

    ExpansionAutoplay_Reset();
    for (action = AI_ACTION_NONE; action <= AI_ACTION_PICK; action++)
    {
        bool expected = action != AI_ACTION_ESCAPE;
        CHECK(ExpansionAutoplay_IsActionSupported(action) == expected,
              "known action capability mismatch");
    }
    CHECK(!ExpansionAutoplay_IsActionSupported(0xFF),
          "unknown action must not be supported");

    return 0;
}

int main(void)
{
    CHECK(sizeof(struct ExpansionAutoplayTelemetry)
              == EXPANSION_AUTOPLAY_TELEMETRY_SIZE,
          "telemetry size contract changed");
    CHECK(TestControllerAndLifecycle() == 0, "controller/lifecycle test");
    CHECK(TestValidationAndBounds() == 0, "validation/bounds test");
    CHECK(TestActionCapabilities() == 0, "action capability test");

    puts("AUTOPLAY_HOST_TEST: PASS");
    return 0;
}
