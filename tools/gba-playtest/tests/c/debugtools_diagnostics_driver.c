#include <stdio.h>

#include "global.h"

#include "expansion_debugtools.h"
#include "debugtools_internal.h"

void DebugToolsDiagnosticsHost_Reset(void);
void DebugToolsDiagnosticsHost_SetMap(int prep, int withUnit);
void DebugToolsDiagnosticsHost_SetBattle(int active);
void DebugToolsDiagnosticsHost_SetBattleDaemon(int active);
void DebugToolsDiagnosticsHost_SetEvent(int active);
void DebugToolsDiagnosticsHost_FillDisplay(u16 value);
int DebugToolsDiagnosticsHost_DisplayEquals(u16 value);
void DebugToolsDiagnosticsHost_OverwriteDisplay(u16 value);
int DebugToolsDiagnosticsHost_GetFontCounter(void);
int DebugToolsDiagnosticsHost_GetGameLock(void);
int DebugToolsDiagnosticsHost_GetRestoredCount(void);
void DebugToolsDiagnosticsHost_EndContext(void);

#define CHECK(cond, message) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUGTOOLS_DIAGNOSTICS_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

static int TestTitleAndRestoration(void)
{
    struct DebugToolsDiagnosticsSnapshot snapshot;

    DebugToolsDiagnosticsHost_Reset();
    DebugToolsDiagnosticsHost_FillDisplay(0x1357);
    DebugToolsDiagnostics_SetSessionContext(DEBUGTOOLS_DIAG_CONTEXT_TITLE);
    CHECK(DebugToolsDiagnostics_BeginSession() == DEBUGTOOLS_OK,
          "title session must start");
    CHECK(DebugToolsDiagnosticsHost_GetGameLock() == 1,
          "session owner must acquire exactly one lock");
    CHECK(DebugTools_CaptureDiagnostics(&snapshot) == DEBUGTOOLS_OK,
          "title capture must succeed");
    CHECK(snapshot.sequence == 1,
          "first successful capture must have sequence 1");
    CHECK(snapshot.validMask == DEBUGTOOLS_DIAG_VALID_COMMON,
          "title must expose common fields only");
    CHECK(snapshot.context == DEBUGTOOLS_DIAG_CONTEXT_TITLE,
          "title context must be explicit");
    CHECK(snapshot.gameClockFrames == 1234 && snapshot.procCount == 12,
          "title common scalars must come from authoritative helpers");
    CHECK(snapshot.rngState[0] == 0x1111
          && snapshot.rngState[1] == 0x2222
          && snapshot.rngState[2] == 0x3333,
          "capture must return the exact three-word RNG state");

    DebugToolsDiagnosticsHost_OverwriteDisplay(0x2468);
    DebugToolsDiagnostics_ForceCloseSession();
    CHECK(DebugToolsDiagnosticsHost_DisplayEquals(0x1357),
          "forced teardown must restore every owned BG0/BG1 cell");
    CHECK(DebugToolsDiagnosticsHost_GetFontCounter() == 23,
          "forced teardown must restore the exact font counter");
    CHECK(DebugToolsDiagnosticsHost_GetGameLock() == 0,
          "forced teardown must release exactly its own lock");
    CHECK(DebugToolsDiagnosticsHost_GetRestoredCount() == 1,
          "owner end callback must release the session once");
    CHECK(gDebugToolsDiagnosticsProbe.forcedTeardownCount == 1
          && gDebugToolsDiagnosticsProbe.restorationCount == 1,
          "forced teardown telemetry must increment once");
    CHECK(gDebugToolsDiagnosticsProbe.restorationMismatchMask == 0,
          "exact restoration must report no scalar mismatch");
    return 0;
}

static int TestMapAndEmptyUnit(void)
{
    struct DebugToolsDiagnosticsSnapshot snapshot;

    DebugToolsDiagnosticsHost_Reset();
    DebugToolsDiagnosticsHost_SetMap(0, 1);
    DebugToolsDiagnostics_SetSessionContext(DEBUGTOOLS_DIAG_CONTEXT_MAP);
    CHECK(DebugToolsDiagnostics_BeginSession() == DEBUGTOOLS_OK,
          "live map session must start");
    CHECK(DebugToolsDiagnosticsHost_GetGameLock() == 0,
          "map session must use its context Proc semaphore, not a global lock");
    CHECK(DebugTools_CaptureDiagnostics(&snapshot) == DEBUGTOOLS_OK,
          "live map capture must succeed");
    CHECK((snapshot.validMask
          & (DEBUGTOOLS_DIAG_VALID_COMMON
              | DEBUGTOOLS_DIAG_VALID_MAP
              | DEBUGTOOLS_DIAG_VALID_CURSOR
              | DEBUGTOOLS_DIAG_VALID_UNIT))
          == (DEBUGTOOLS_DIAG_VALID_COMMON
              | DEBUGTOOLS_DIAG_VALID_MAP
              | DEBUGTOOLS_DIAG_VALID_CURSOR
              | DEBUGTOOLS_DIAG_VALID_UNIT),
          "valid cursor unit must set every field-validity bit");
    CHECK(snapshot.chapterIndex == 2
          && snapshot.turn == 7
          && snapshot.cursorX == 3
          && snapshot.cursorY == 4,
          "map capture must preserve chapter/turn/tile coordinates");
    CHECK(snapshot.cursorUnitId == 1
          && snapshot.characterId == 0x12
          && snapshot.classId == 0x34
          && snapshot.currentHp == 17
          && snapshot.maxHp == 24,
          "map capture must use the validated cursor unit");
    DebugToolsDiagnostics_EndSession(0);

    DebugToolsDiagnosticsHost_Reset();
    DebugToolsDiagnosticsHost_SetMap(1, 0);
    DebugToolsDiagnosticsHost_SetBattleDaemon(1);
    DebugToolsDiagnostics_SetSessionContext(DEBUGTOOLS_DIAG_CONTEXT_PREP);
    CHECK(DebugToolsDiagnostics_BeginSession() == DEBUGTOOLS_OK,
          "authoritative live prep must ignore a lingering battle daemon");
    CHECK(DebugToolsDiagnosticsHost_GetGameLock() == 0,
          "prep session must use its context Proc semaphore, not a global lock");
    CHECK(DebugTools_CaptureDiagnostics(&snapshot) == DEBUGTOOLS_OK,
          "empty-tile prep capture must still succeed");
    CHECK((snapshot.validMask & DEBUGTOOLS_DIAG_VALID_CURSOR) != 0,
          "empty in-bounds tile must retain cursor validity");
    CHECK((snapshot.validMask & DEBUGTOOLS_DIAG_VALID_UNIT) == 0,
          "empty tile must clear unit validity");
    CHECK(snapshot.cursorUnitId == 0
          && snapshot.characterId == 0
          && snapshot.classId == 0,
          "empty tile must zero unavailable unit fields");
    DebugToolsDiagnostics_EndSession(0);

    return 0;
}

static int TestUnavailableContexts(void)
{
    struct DebugToolsDiagnosticsSnapshot snapshot;

    DebugToolsDiagnosticsHost_Reset();
    DebugToolsDiagnosticsHost_SetMap(0, 1);
    DebugToolsDiagnosticsHost_SetBattle(1);
    DebugToolsDiagnostics_SetSessionContext(DEBUGTOOLS_DIAG_CONTEXT_MAP);
    CHECK(DebugToolsDiagnostics_BeginSession()
          == DEBUGTOOLS_ERR_CONTEXT_UNAVAILABLE,
          "battle ownership must reject display entry");
    CHECK(DebugTools_CaptureDiagnostics(&snapshot)
          == DEBUGTOOLS_ERR_CONTEXT_UNAVAILABLE,
          "battle capture must fail closed");
    CHECK(snapshot.context == DEBUGTOOLS_DIAG_CONTEXT_BATTLE
          && snapshot.validMask == 0,
          "battle negative must report context with no valid fields");

    DebugToolsDiagnosticsHost_Reset();
    DebugToolsDiagnosticsHost_SetMap(0, 1);
    DebugToolsDiagnosticsHost_SetEvent(1);
    DebugToolsDiagnostics_SetSessionContext(DEBUGTOOLS_DIAG_CONTEXT_MAP);
    CHECK(DebugToolsDiagnostics_BeginSession()
          == DEBUGTOOLS_ERR_CONTEXT_UNAVAILABLE,
          "active event must reject map diagnostics");
    CHECK(DebugTools_CaptureDiagnostics(NULL)
          == DEBUGTOOLS_ERR_INVALID_ARGUMENT,
          "NULL output must fail explicitly");
    return 0;
}

static int TestParentTeardown(void)
{
    DebugToolsDiagnosticsHost_Reset();
    DebugToolsDiagnosticsHost_FillDisplay(0x369C);
    DebugToolsDiagnostics_SetSessionContext(DEBUGTOOLS_DIAG_CONTEXT_TITLE);
    CHECK(DebugToolsDiagnostics_BeginSession() == DEBUGTOOLS_OK,
          "parent-teardown session must start");
    DebugToolsDiagnosticsHost_OverwriteDisplay(0x147A);
    DebugToolsDiagnosticsHost_EndContext();
    CHECK(DebugToolsDiagnosticsHost_DisplayEquals(0x369C),
          "context-parent teardown must restore owned display cells");
    CHECK(gDebugToolsDiagnosticsProbe.forcedTeardownCount == 0
          && gDebugToolsDiagnosticsProbe.restorationCount == 1,
          "parent teardown is restored once without counting as explicit force");
    CHECK(gDebugToolsDiagnosticsProbe.restorationMismatchMask == 0,
          "parent teardown must retain exact restoration");
    return 0;
}

int main(void)
{
    CHECK(sizeof(struct DebugToolsDiagnosticsSnapshot) == 0x40,
          "public snapshot ABI must remain exactly 0x40 bytes");
    CHECK(TestTitleAndRestoration() == 0,
          "title/restoration case failed");
    CHECK(TestMapAndEmptyUnit() == 0,
          "map/empty-unit case failed");
    CHECK(TestUnavailableContexts() == 0,
          "unavailable-context case failed");
    CHECK(TestParentTeardown() == 0,
          "parent-teardown case failed");
    printf("DEBUGTOOLS_DIAGNOSTICS_HOST_TEST: PASS\n");
    return 0;
}
