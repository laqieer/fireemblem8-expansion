#include "global.h"

#include "bmsave.h"
#include "save_format.h"
#include "expansion_debug_save_fixture.h"
#include "grado_campaign_state.h"

extern u8 sGameStartSaveBuf[0x8000];
extern int DebugSaveFixtureHost_GetGameLoadCount(void);
extern int DebugSaveFixtureHost_GetSuspendLoadCount(void);

static enum GradoStateLoadResult sGradoPreflightResult = GRADO_STATE_LOAD_RECOVERY;
static int sGradoPreflightCalls;
static int sGradoPreflightViewIsFixtureImage;
static int sGradoRecoveryCalls;
static int sGradoLoadCountAtRecovery;

void DebugSaveFixtureGradoHost_SetPreflightResult(
    enum GradoStateLoadResult result)
{
    sGradoPreflightResult = result;
}

int DebugSaveFixtureGradoHost_GetPreflightCalls(void)
{
    return sGradoPreflightCalls;
}

int DebugSaveFixtureGradoHost_GetPreflightViewIsFixtureImage(void)
{
    return sGradoPreflightViewIsFixtureImage;
}

int DebugSaveFixtureGradoHost_GetRecoveryCalls(void)
{
    return sGradoRecoveryCalls;
}

int DebugSaveFixtureGradoHost_GetLoadCountAtRecovery(void)
{
    return sGradoLoadCountAtRecovery;
}

enum GradoStateLoadResult GradoState_PreflightSave(
    enum SaveCompatState compatibility,
    const struct ExpansionSaveMeta *meta,
    enum GradoStateLoadRoute route,
    unsigned int chapter,
    const struct GradoStateFlagView *view,
    struct GradoStateLoadDiagnostic *diagnostic)
{
    struct SaveBlocks *fixture = (struct SaveBlocks *)sGameStartSaveBuf;
    struct SuspendSaveBlock *suspend = &fixture->suspendSaveBlocks[1];
    struct GameSaveBlock *game = &fixture->gameSaveBlocks[0];

    (void)chapter;
    (void)diagnostic;
    sGradoPreflightCalls++;
    sGradoPreflightViewIsFixtureImage =
        compatibility == SAVE_COMPAT_CURRENT
        && meta == &fixture->expansionSaveMeta
        && ((route == GRADO_LOAD_SUSPEND
                && view->chapterFlags == suspend->chapterFlags
                && view->permanentFlags == suspend->permanentFlags
                && view->chapterBytes == sizeof(suspend->chapterFlags)
                && view->permanentBytes == sizeof(suspend->permanentFlags))
            || (route == GRADO_LOAD_NORMAL
                && view->chapterFlags == NULL
                && view->permanentFlags == game->permanentFlags
                && view->chapterBytes == 0
                && view->permanentBytes == sizeof(game->permanentFlags)));

    return sGradoPreflightResult;
}

enum GradoStateLoadResult GradoState_ApplyLoadRecovery(
    struct GradoStateLoadDiagnostic *diagnostic)
{
    (void)diagnostic;
    sGradoRecoveryCalls++;
    sGradoLoadCountAtRecovery =
        DebugSaveFixtureHost_GetGameLoadCount()
        + DebugSaveFixtureHost_GetSuspendLoadCount();
    return GRADO_STATE_LOAD_VALID;
}
